#!/usr/bin/env python3
"""Regression tests for the NextGame UI token telemetry ledger."""

from __future__ import annotations

import io
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from typing import Iterator
from unittest import mock

import token_telemetry
from token_telemetry import (
    DEFAULT_SCHEMA,
    EVENT_KINDS,
    LEGACY_UNCLASSIFIED,
    LOCAL_OPERATION,
    METRIC_FIELDS,
    MODEL_CALL,
    PROXY_FIELDS,
    TOKEN_FIELDS,
    TelemetryValidationError,
    append_event,
    check_budget,
    check_ledger_budget,
    check_measurement_boundary,
    load_ledger,
    make_event,
    make_local_operation_event,
    make_model_call_event,
    summarize_ledger,
    validate_event,
)


FIXED_TIME = "2026-08-29T08:30:00.123Z"
CALL_DIGEST = "1" * 64
USAGE_DIGEST = "2" * 64
RUN_DIGEST = "a" * 64
OLD_RUN_DIGEST = "b" * 64
TEST_TEMP_ROOT = Path(
    os.environ.get("NEXTGAME_UI_TEST_TEMP_ROOT", tempfile.gettempdir())
).resolve()


@contextmanager
def temporary_ledger() -> Iterator[Path]:
    """Yield a unique system-temp ledger and remove every sidecar afterward."""

    TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    ledger = TEST_TEMP_ROOT / f".token-telemetry-test-{uuid.uuid4().hex}.json"
    try:
        yield ledger
    finally:
        candidates = [ledger, ledger.with_name(ledger.name + ".lock")]
        candidates.extend(ledger.parent.glob(f".{ledger.name}.*.tmp"))
        for candidate in candidates:
            try:
                candidate.unlink()
            except FileNotFoundError:
                pass


class TokenTelemetryTests(unittest.TestCase):
    def event(
        self,
        request_id: str = "request-1",
        stage: str = "analysis",
        *,
        not_applicable_metrics: tuple[str, ...] | list[str] | None = None,
        unmeasured_metrics: tuple[str, ...] | list[str] | None = None,
        event_kind: str | None = None,
        **metrics: int,
    ) -> dict:
        selected_metrics = metrics or {"instructionBytes": 10}
        selected_kind = event_kind or (
            MODEL_CALL
            if any(field in selected_metrics for field in TOKEN_FIELDS)
            or "tokenizerProxyTokens" in selected_metrics
            else LOCAL_OPERATION
        )
        if selected_kind == LOCAL_OPERATION:
            return make_local_operation_event(
                request_id,
                stage,
                "unit-test",
                timestamp=FIXED_TIME,
                not_applicable_metrics=not_applicable_metrics,
                unmeasured_metrics=unmeasured_metrics,
                **selected_metrics,
            )
        if selected_kind == MODEL_CALL:
            logical_digest = hashlib.sha256(
                f"{request_id}|{stage}".encode("utf-8")
            ).hexdigest()
            return make_model_call_event(
                request_id,
                stage,
                "unit-test",
                provider="openai",
                model="gpt-5.6-sol",
                agent_role="unit-test",
                token_source="provider-receipt",
                measurement_boundary_id="unit-boundary-v1",
                call_id_digest=logical_digest,
                run_id_digest=RUN_DIGEST,
                usage_receipt_sha256=hashlib.sha256(
                    ("usage|" + logical_digest).encode("utf-8")
                ).hexdigest(),
                timestamp=FIXED_TIME,
                not_applicable_metrics=not_applicable_metrics,
                unmeasured_metrics=unmeasured_metrics,
                **selected_metrics,
            )
        return make_event(
            request_id,
            stage,
            "unit-test",
            event_kind=selected_kind,
            timestamp=FIXED_TIME,
            not_applicable_metrics=not_applicable_metrics,
            unmeasured_metrics=unmeasured_metrics,
            **selected_metrics,
        )

    def test_validation_rejects_unknown_negative_nan_and_sensitive_path_shape(self) -> None:
        valid = self.event()
        for mutation in (
            {"rawPrompt": "do not store me"},
            {"toolOutputBytes": -1},
            {"inputTokens": math.nan},
            {"artifactBytes": True},
        ):
            with self.subTest(mutation=mutation):
                candidate = dict(valid)
                candidate.update(mutation)
                with self.assertRaises(TelemetryValidationError):
                    validate_event(candidate)

        path_candidate = dict(valid, operation=r"C:\\sensitive\\artifact.json")
        with self.assertRaises(TelemetryValidationError):
            validate_event(path_candidate)

        with self.assertRaises(TelemetryValidationError):
            validate_event(
                {
                    "requestId": "request-1",
                    "stage": "analysis",
                    "operation": "empty-event",
                    "timestamp": FIXED_TIME,
                }
            )

    def test_not_applicable_metrics_reject_unknown_duplicate_and_measured_overlap(self) -> None:
        for not_applicable in (
            ["unknownMetric"],
            ["toolOutputBytes", "toolOutputBytes"],
        ):
            with self.subTest(not_applicable=not_applicable):
                with self.assertRaises(TelemetryValidationError):
                    self.event(not_applicable_metrics=not_applicable, instructionBytes=10)

        with self.assertRaises(TelemetryValidationError):
            self.event(not_applicable_metrics=["inputTokens"], inputTokens=10)

    def test_append_rejects_ledger_inside_plugin_package(self) -> None:
        ledger = token_telemetry.PLUGIN_ROOT / (
            ".token-telemetry-forbidden-" + uuid.uuid4().hex + ".json"
        )
        with self.assertRaisesRegex(
            TelemetryValidationError, "must not be stored inside"
        ):
            append_event(ledger, self.event(toolCallCount=1))
        self.assertFalse(ledger.exists())
        self.assertFalse(ledger.with_name(ledger.name + ".lock").exists())

    def test_append_and_summary_aggregate_total_and_stage_metrics(self) -> None:
        with temporary_ledger() as ledger:
            first = append_event(
                ledger,
                self.event(inputTokens=11, cachedInputTokens=4, instructionBytes=100),
            )
            second = append_event(
                ledger,
                self.event("request-2", "build", outputTokens=7, toolOutputBytes=250),
            )
            third = append_event(
                ledger,
                self.event("request-3", "analysis", inputTokens=5, toolCallCount=2),
            )

            self.assertEqual(first["eventCount"], 1)
            self.assertEqual(second["eventCount"], 2)
            self.assertEqual(third["eventCount"], 3)
            summary = summarize_ledger(ledger)
            self.assertEqual(summary["eventCount"], 3)
            self.assertEqual(summary["totals"]["inputTokens"], 16)
            self.assertEqual(summary["totals"]["toolOutputBytes"], 250)
            self.assertEqual(summary["metricEventCounts"]["inputTokens"], 2)
            self.assertEqual(summary["metricCoverageCounts"]["inputTokens"], 2)
            self.assertEqual(summary["byStage"]["analysis"]["eventCount"], 2)
            self.assertEqual(summary["byStage"]["analysis"]["totals"]["inputTokens"], 16)
            self.assertEqual(summary["byStage"]["analysis"]["metricEventCounts"]["inputTokens"], 2)
            self.assertEqual(summary["byStage"]["analysis"]["metricCoverageCounts"]["inputTokens"], 2)
            self.assertEqual(summary["byStage"]["build"]["totals"]["outputTokens"], 7)

    def test_budget_failure_reports_total_and_stage_violations(self) -> None:
        summary = token_telemetry.summarize_events(
            [
                self.event(inputTokens=30, toolOutputBytes=100),
                self.event("request-2", "build", inputTokens=20, toolOutputBytes=500),
            ]
        )
        result = check_budget(
            summary,
            total_limits={"inputTokens": 40},
            stage_limits={"build": {"toolOutputBytes": 400}},
        )
        self.assertFalse(result["ok"])
        self.assertFalse(result["withinBudget"])
        self.assertEqual(result["checkedLimitCount"], 2)
        self.assertEqual(
            {(item["scope"], item["metric"]) for item in result["violations"]},
            {("total", "inputTokens"), ("stage", "toolOutputBytes")},
        )
        self.assertEqual(result["unavailableMetrics"], [])

    def test_budget_fails_closed_when_total_metric_was_not_collected(self) -> None:
        summary = token_telemetry.summarize_events([self.event(instructionBytes=100)])
        result = check_budget(summary, total_limits={"inputTokens": 1000})
        self.assertFalse(result["withinBudget"])
        unavailable = result["unavailableMetrics"][0]
        self.assertEqual(unavailable["scope"], "total")
        self.assertEqual(unavailable["metric"], "inputTokens")
        self.assertEqual(unavailable["modelCallCount"], 0)
        self.assertEqual(unavailable["measuredCallCount"], 0)
        self.assertEqual(
            unavailable["unavailabilityReason"], "no-model-call-receipts"
        )
        self.assertEqual(result["violations"][0]["reason"], "unavailable")
        self.assertNotIn("actual", result["violations"][0])

    def test_budget_fails_closed_when_stage_metric_was_not_collected(self) -> None:
        summary = token_telemetry.summarize_events(
            [
                self.event(inputTokens=10),
                self.event("request-build", "build", toolOutputBytes=50),
            ]
        )
        result = check_budget(summary, stage_limits={"build": {"inputTokens": 1000}})
        self.assertFalse(result["withinBudget"])
        unavailable = result["unavailableMetrics"][0]
        self.assertEqual(unavailable["scope"], "stage")
        self.assertEqual(unavailable["stage"], "build")
        self.assertEqual(unavailable["modelCallCount"], 0)
        self.assertEqual(
            unavailable["unavailabilityReason"], "no-model-call-receipts"
        )
        self.assertEqual(result["violations"][0]["reason"], "unavailable")

    def test_budget_fails_closed_for_partially_collected_total_metric(self) -> None:
        summary = token_telemetry.summarize_events(
            [
                self.event(inputTokens=10),
                self.event(
                    "request-proxy",
                    "analysis",
                    outputTokens=1,
                    instructionBytes=100,
                ),
            ]
        )
        self.assertEqual(summary["metricEventCounts"]["inputTokens"], 1)
        self.assertEqual(summary["metricCoverageCounts"]["inputTokens"], 1)
        result = check_budget(summary, total_limits={"inputTokens": 1000})
        self.assertFalse(result["withinBudget"])
        unavailable = result["unavailableMetrics"][0]
        self.assertEqual(unavailable["modelCallCount"], 2)
        self.assertEqual(unavailable["applicableCallCount"], 2)
        self.assertEqual(unavailable["measuredCallCount"], 1)
        self.assertEqual(unavailable["unmeasuredCallCount"], 1)
        self.assertEqual(
            unavailable["unavailabilityReason"],
            "incomplete-model-call-measurement",
        )
        self.assertNotIn("actual", result["violations"][0])

    def test_budget_fails_closed_for_partially_collected_stage_metric(self) -> None:
        summary = token_telemetry.summarize_events(
            [
                self.event("request-token", "build", inputTokens=10),
                self.event(
                    "request-proxy",
                    "build",
                    outputTokens=1,
                    instructionBytes=100,
                ),
            ]
        )
        self.assertEqual(summary["byStage"]["build"]["metricEventCounts"]["inputTokens"], 1)
        self.assertEqual(summary["byStage"]["build"]["metricCoverageCounts"]["inputTokens"], 1)
        result = check_budget(summary, stage_limits={"build": {"inputTokens": 1000}})
        self.assertFalse(result["withinBudget"])
        unavailable = result["unavailableMetrics"][0]
        self.assertEqual(unavailable["scope"], "stage")
        self.assertEqual(unavailable["modelCallCount"], 2)
        self.assertEqual(unavailable["measuredCallCount"], 1)
        self.assertEqual(unavailable["unmeasuredCallCount"], 1)

    def test_explicit_zero_counts_as_collected_budget_data(self) -> None:
        summary = token_telemetry.summarize_events(
            [
                self.event("request-zero-1", "build", inputTokens=0),
                self.event("request-zero-2", "build", inputTokens=0),
            ]
        )
        result = check_budget(
            summary,
            total_limits={"inputTokens": 0},
            stage_limits={"build": {"inputTokens": 0}},
        )
        self.assertTrue(result["withinBudget"])
        self.assertEqual(result["unavailableMetrics"], [])

    def test_explicit_not_applicable_coverage_allows_mixed_proxy_and_token_events(self) -> None:
        summary = token_telemetry.summarize_events(
            [
                self.event(
                    "request-proxy",
                    "build",
                    not_applicable_metrics=TOKEN_FIELDS,
                    toolOutputBytes=100,
                ),
                self.event(
                    "request-token",
                    "build",
                    not_applicable_metrics=PROXY_FIELDS,
                    inputTokens=10,
                ),
            ]
        )
        self.assertEqual(summary["metricEventCounts"]["inputTokens"], 1)
        self.assertEqual(summary["metricEventCounts"]["toolOutputBytes"], 1)
        self.assertEqual(summary["metricCoverageCounts"]["inputTokens"], 2)
        self.assertEqual(summary["metricCoverageCounts"]["toolOutputBytes"], 2)
        self.assertEqual(
            summary["byStage"]["build"]["metricCoverageCounts"]["inputTokens"],
            2,
        )
        self.assertEqual(
            summary["tokenMeasurement"]["inputTokens"],
            {
                "applicableCallCount": 1,
                "measuredCallCount": 1,
                "notApplicableCount": 0,
                "unmeasuredCallCount": 0,
            },
        )
        result = check_budget(
            summary,
            total_limits={"inputTokens": 10, "toolOutputBytes": 100},
            stage_limits={
                "build": {"inputTokens": 10, "toolOutputBytes": 100}
            },
        )
        self.assertTrue(result["withinBudget"])
        self.assertEqual(result["unavailableMetrics"], [])

    def test_provider_receipt_zero_is_measured_but_missing_tokens_are_unmeasured(self) -> None:
        complete = make_model_call_event(
            "request-provider-zero",
            "analysis",
            "provider-call",
            provider="openai",
            model="gpt-5.6-sol",
            agent_role="visual-structure",
            token_source="provider-receipt",
            measurement_boundary_id="role-test-ab-v1",
            call_id_digest=CALL_DIGEST,
            run_id_digest=RUN_DIGEST,
            usage_receipt_sha256=USAGE_DIGEST,
            timestamp=FIXED_TIME,
            not_applicable_metrics=["reasoningTokens", "visionTokens"],
            inputTokens=0,
            cachedInputTokens=0,
            outputTokens=0,
        )
        summary = token_telemetry.summarize_events([complete])
        self.assertEqual(summary["eventKindCounts"][MODEL_CALL], 1)
        self.assertEqual(
            summary["tokenMeasurement"]["inputTokens"]["measuredCallCount"],
            1,
        )
        self.assertTrue(
            check_budget(
                summary,
                total_limits={"inputTokens": 0, "outputTokens": 0},
            )["withinBudget"]
        )
        vision_budget = check_budget(summary, total_limits={"visionTokens": 0})
        self.assertFalse(vision_budget["withinBudget"])
        self.assertEqual(
            vision_budget["unavailableMetrics"][0]["unavailabilityReason"],
            "no-applicable-model-calls",
        )

    def test_tokenizer_proxy_remains_separate_from_actual_tokens(self) -> None:
        event = make_model_call_event(
            "request-tokenizer-proxy",
            "analysis",
            "provider-call",
            provider="openai",
            model="gpt-5.6-sol",
            agent_role="schema-review",
            token_source="tokenizer-proxy",
            measurement_boundary_id="role-test-ab-v1",
            call_id_digest=CALL_DIGEST,
            run_id_digest=RUN_DIGEST,
            timestamp=FIXED_TIME,
            tokenizer_encoding="o200k_base",
            tokenizer_version="tiktoken-0.12.0",
            tokenizerProxyTokens=321,
        )
        summary = token_telemetry.summarize_events([event])
        self.assertEqual(summary["totals"]["tokenizerProxyTokens"], 321)
        self.assertNotIn("inputTokens", summary["totals"])
        result = check_budget(summary, total_limits={"inputTokens": 0})
        self.assertFalse(result["withinBudget"])
        self.assertEqual(
            result["unavailableMetrics"][0]["unavailabilityReason"],
            "incomplete-model-call-measurement",
        )

    def test_ledger_token_budget_requires_exact_expected_call_boundary(self) -> None:
        event = make_model_call_event(
            "request-boundary",
            "analysis",
            "provider-call",
            provider="openai",
            model="gpt-5.6-sol",
            agent_role="visual-structure",
            token_source="provider-receipt",
            measurement_boundary_id="role-test-ab-v1",
            call_id_digest=CALL_DIGEST,
            run_id_digest=RUN_DIGEST,
            usage_receipt_sha256=USAGE_DIGEST,
            timestamp=FIXED_TIME,
            not_applicable_metrics=["reasoningTokens", "visionTokens"],
            inputTokens=0,
            cachedInputTokens=0,
            outputTokens=0,
        )
        with temporary_ledger() as ledger:
            append_event(ledger, event)
            missing_contract = check_ledger_budget(
                ledger,
                total_limits={"inputTokens": 0},
            )
            self.assertFalse(missing_contract["withinBudget"])
            self.assertEqual(
                missing_contract["measurementBoundary"]["status"],
                "missing-contract",
            )

            exact = check_ledger_budget(
                ledger,
                total_limits={"inputTokens": 0},
                measurement_boundary_id="role-test-ab-v1",
                run_id_digest=RUN_DIGEST,
                expected_model_calls=[("analysis", "visual-structure", CALL_DIGEST)],
            )
            self.assertTrue(exact["withinBudget"])
            self.assertTrue(exact["measurementBoundary"]["complete"])
            self.assertEqual(exact["measurementBoundary"]["status"], "complete")
            self.assertEqual(
                exact["measurementBoundary"]["unmeasuredApplicableTokenCount"],
                0,
            )

            missing_run_contract = check_ledger_budget(
                ledger,
                total_limits={"inputTokens": 0},
                measurement_boundary_id="role-test-ab-v1",
                expected_model_calls=[("analysis", "visual-structure", CALL_DIGEST)],
            )
            self.assertFalse(missing_run_contract["withinBudget"])
            self.assertEqual(
                missing_run_contract["measurementBoundary"]["status"],
                "missing-contract",
            )

            mismatch = check_ledger_budget(
                ledger,
                total_limits={"inputTokens": 0},
                measurement_boundary_id="role-test-ab-v1",
                run_id_digest=RUN_DIGEST,
                expected_model_calls=[("analysis", "visual-structure", "3" * 64)],
            )
            self.assertFalse(mismatch["withinBudget"])
            self.assertEqual(mismatch["measurementBoundary"]["missingCallCount"], 1)
            self.assertEqual(mismatch["measurementBoundary"]["unexpectedCallCount"], 1)

    def test_input_only_budget_fails_when_expected_call_output_is_unmeasured(self) -> None:
        event = make_model_call_event(
            "request-partial-provider",
            "analysis",
            "provider-call",
            provider="openai",
            model="gpt-5.6-sol",
            agent_role="visual-structure",
            token_source="provider-receipt",
            measurement_boundary_id="role-test-ab-v1",
            call_id_digest=CALL_DIGEST,
            run_id_digest=RUN_DIGEST,
            usage_receipt_sha256=USAGE_DIGEST,
            timestamp=FIXED_TIME,
            not_applicable_metrics=[
                "cachedInputTokens",
                "reasoningTokens",
                "visionTokens",
            ],
            inputTokens=0,
        )
        with temporary_ledger() as ledger:
            append_event(ledger, event)
            result = check_ledger_budget(
                ledger,
                total_limits={"inputTokens": 0},
                measurement_boundary_id="role-test-ab-v1",
                run_id_digest=RUN_DIGEST,
                expected_model_calls=[("analysis", "visual-structure", CALL_DIGEST)],
            )
        self.assertFalse(result["withinBudget"])
        self.assertEqual(
            result["measurementBoundary"]["status"],
            "unmeasured-applicable-tokens",
        )
        self.assertEqual(result["measurementBoundary"]["unmeasuredCallCount"], 1)
        self.assertEqual(
            result["measurementBoundary"]["unmeasuredApplicableTokenCount"],
            1,
        )

    def test_old_run_cannot_satisfy_or_contaminate_new_run_budget(self) -> None:
        old_event = make_model_call_event(
            "request-old-run",
            "analysis",
            "provider-call",
            provider="openai",
            model="gpt-5.6-sol",
            agent_role="visual-structure",
            token_source="provider-receipt",
            measurement_boundary_id="role-test-ab-v1",
            call_id_digest=CALL_DIGEST,
            run_id_digest=OLD_RUN_DIGEST,
            usage_receipt_sha256="4" * 64,
            timestamp=FIXED_TIME,
            not_applicable_metrics=["reasoningTokens", "visionTokens"],
            inputTokens=999,
            cachedInputTokens=0,
            outputTokens=1,
        )
        new_event = make_model_call_event(
            "request-new-run",
            "analysis",
            "provider-call",
            provider="openai",
            model="gpt-5.6-sol",
            agent_role="visual-structure",
            token_source="provider-receipt",
            measurement_boundary_id="role-test-ab-v1",
            call_id_digest=CALL_DIGEST,
            run_id_digest=RUN_DIGEST,
            usage_receipt_sha256="5" * 64,
            timestamp=FIXED_TIME,
            not_applicable_metrics=["reasoningTokens", "visionTokens"],
            inputTokens=0,
            cachedInputTokens=0,
            outputTokens=0,
        )
        with temporary_ledger() as ledger:
            append_event(ledger, old_event)
            old_only = check_ledger_budget(
                ledger,
                total_limits={"inputTokens": 0},
                measurement_boundary_id="role-test-ab-v1",
                run_id_digest=RUN_DIGEST,
                expected_model_calls=[("analysis", "visual-structure", CALL_DIGEST)],
            )
            self.assertFalse(old_only["withinBudget"])
            self.assertEqual(old_only["measurementBoundary"]["missingCallCount"], 1)
            self.assertEqual(old_only["measurementBoundary"]["otherRunCallCount"], 1)

            append_event(ledger, new_event)
            current = check_ledger_budget(
                ledger,
                total_limits={"inputTokens": 0},
                measurement_boundary_id="role-test-ab-v1",
                run_id_digest=RUN_DIGEST,
                expected_model_calls=[("analysis", "visual-structure", CALL_DIGEST)],
            )
        self.assertTrue(current["withinBudget"])
        self.assertTrue(current["measurementBoundary"]["complete"])
        self.assertEqual(current["measurementBoundary"]["otherRunCallCount"], 1)

    def test_measurement_boundary_output_never_echoes_call_digests(self) -> None:
        event = self.event(
            inputTokens=1,
            cachedInputTokens=0,
            outputTokens=0,
            not_applicable_metrics=("reasoningTokens", "visionTokens"),
        )
        receipt = check_measurement_boundary(
            [event],
            measurement_boundary_id="unit-boundary-v1",
            run_id_digest=RUN_DIGEST,
            expected_model_calls=[("analysis", "unit-test", event["callIdDigest"])],
        )
        rendered = json.dumps(receipt)
        self.assertTrue(receipt["complete"])
        self.assertNotIn(event["callIdDigest"], rendered)
        self.assertNotIn(RUN_DIGEST, rendered)

    def test_model_call_requires_metadata_and_complete_token_classification(self) -> None:
        with self.assertRaisesRegex(TelemetryValidationError, "missing metadata"):
            make_event(
                "request-missing-metadata",
                "analysis",
                "provider-call",
                event_kind=MODEL_CALL,
                timestamp=FIXED_TIME,
                unmeasured_metrics=TOKEN_FIELDS,
                instructionBytes=1,
            )
        with self.assertRaisesRegex(TelemetryValidationError, "runIdDigest"):
            make_event(
                "request-missing-run",
                "analysis",
                "provider-call",
                event_kind=MODEL_CALL,
                provider="openai",
                model="gpt-5.6-sol",
                agent_role="schema-review",
                token_source="provider-receipt",
                measurement_boundary_id="role-test-ab-v1",
                call_id_digest=CALL_DIGEST,
                usage_receipt_sha256=USAGE_DIGEST,
                timestamp=FIXED_TIME,
                unmeasured_metrics=[
                    "cachedInputTokens",
                    "outputTokens",
                    "reasoningTokens",
                    "visionTokens",
                ],
                inputTokens=1,
            )
        with self.assertRaisesRegex(TelemetryValidationError, "lowercase SHA-256"):
            make_model_call_event(
                "request-bad-digest",
                "analysis",
                "provider-call",
                provider="openai",
                model="gpt-5.6-sol",
                agent_role="schema-review",
                token_source="provider-receipt",
                measurement_boundary_id="role-test-ab-v1",
                call_id_digest="raw-provider-call-id",
                run_id_digest=RUN_DIGEST,
                usage_receipt_sha256=USAGE_DIGEST,
                timestamp=FIXED_TIME,
                inputTokens=1,
            )
        with self.assertRaisesRegex(TelemetryValidationError, "always applicable"):
            make_model_call_event(
                "request-invalid-na",
                "analysis",
                "provider-call",
                provider="openai",
                model="gpt-5.6-sol",
                agent_role="schema-review",
                token_source="provider-receipt",
                measurement_boundary_id="role-test-ab-v1",
                call_id_digest=CALL_DIGEST,
                run_id_digest=RUN_DIGEST,
                usage_receipt_sha256=USAGE_DIGEST,
                timestamp=FIXED_TIME,
                not_applicable_metrics=["inputTokens"],
                outputTokens=1,
            )

    def test_model_call_receipt_append_is_idempotent_and_conflicts_fail(self) -> None:
        base = make_model_call_event(
            "request-idempotent",
            "analysis",
            "provider-call",
            provider="openai",
            model="gpt-5.6-sol",
            agent_role="schema-review",
            token_source="provider-receipt",
            measurement_boundary_id="role-test-ab-v1",
            call_id_digest=CALL_DIGEST,
            run_id_digest=RUN_DIGEST,
            usage_receipt_sha256=USAGE_DIGEST,
            timestamp=FIXED_TIME,
            inputTokens=10,
        )
        retry = dict(base, timestamp="2026-08-29T08:31:00.123Z")
        conflict = dict(base, timestamp="2026-08-29T08:32:00.123Z", inputTokens=11)
        logical_conflict = dict(
            base,
            timestamp="2026-08-29T08:33:00.123Z",
            usageReceiptSha256="4" * 64,
        )
        with temporary_ledger() as ledger:
            self.assertEqual(append_event(ledger, base)["action"], "append")
            retry_receipt = append_event(ledger, retry)
            self.assertEqual(retry_receipt["action"], "already-recorded")
            self.assertEqual(retry_receipt["eventCount"], 1)
            with self.assertRaisesRegex(TelemetryValidationError, "conflicts"):
                append_event(ledger, conflict)
            with self.assertRaisesRegex(TelemetryValidationError, "duplicate logical model call"):
                append_event(ledger, logical_conflict)
            self.assertEqual(len(load_ledger(ledger)["events"]), 1)

    def test_concurrent_duplicate_model_receipts_count_once(self) -> None:
        event = make_model_call_event(
            "request-concurrent-receipt",
            "analysis",
            "provider-call",
            provider="openai",
            model="gpt-5.6-sol",
            agent_role="coverage-review",
            token_source="provider-receipt",
            measurement_boundary_id="role-test-ab-v1",
            call_id_digest=CALL_DIGEST,
            run_id_digest=RUN_DIGEST,
            usage_receipt_sha256=USAGE_DIGEST,
            timestamp=FIXED_TIME,
            inputTokens=10,
        )
        with temporary_ledger() as ledger:
            with ThreadPoolExecutor(max_workers=8) as executor:
                receipts = list(executor.map(lambda _index: append_event(ledger, event), range(16)))
            self.assertEqual(len(load_ledger(ledger)["events"]), 1)
            self.assertEqual(sum(receipt["action"] == "append" for receipt in receipts), 1)

    def test_legacy_ledger_is_read_only_migrated_and_append_upgrades_atomically(self) -> None:
        legacy_local = {
            "requestId": "legacy-build",
            "stage": "umg-build",
            "operation": "execute-plan-completed",
            "timestamp": FIXED_TIME,
            "notApplicableMetrics": list(TOKEN_FIELDS),
            "toolCallCount": 1,
        }
        legacy_unknown = {
            "requestId": "legacy-analysis",
            "stage": "requirements",
            "operation": "analysis-complete",
            "timestamp": FIXED_TIME,
            "notApplicableMetrics": list(TOKEN_FIELDS),
            "agentCount": 9,
        }
        with temporary_ledger() as ledger:
            ledger.write_text(
                json.dumps(
                    {"schemaVersion": "1.0", "events": [legacy_local, legacy_unknown]}
                ),
                encoding="utf-8",
            )
            before = ledger.read_bytes()
            loaded = load_ledger(ledger)
            self.assertEqual(ledger.read_bytes(), before)
            self.assertEqual(loaded["schemaVersion"], token_telemetry.SCHEMA_VERSION)
            self.assertEqual(
                [event["eventKind"] for event in loaded["events"]],
                [LOCAL_OPERATION, LEGACY_UNCLASSIFIED],
            )
            result = check_budget(
                token_telemetry.summarize_events(loaded["events"]),
                total_limits={"inputTokens": 0},
            )
            self.assertFalse(result["withinBudget"])
            append_event(
                ledger,
                self.event("new-local", "umg-build", toolCallCount=1),
            )
            persisted = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual(persisted["schemaVersion"], "1.1")
            self.assertEqual(len(persisted["events"]), 3)

    def test_malformed_legacy_metric_list_returns_safe_validation_error(self) -> None:
        malformed = {
            "schemaVersion": "1.0",
            "events": [
                {
                    "requestId": "legacy-malformed",
                    "stage": "umg-build",
                    "operation": "execute-plan-completed",
                    "timestamp": FIXED_TIME,
                    "notApplicableMetrics": [{}],
                    "toolCallCount": 1,
                }
            ],
        }
        with temporary_ledger() as ledger:
            ledger.write_text(json.dumps(malformed), encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = token_telemetry.main(["summary", str(ledger)])
            self.assertEqual(exit_code, token_telemetry.EXIT_ERROR)
            rendered = output.getvalue()
            self.assertEqual(json.loads(rendered)["error"]["code"], "validation")
            self.assertNotIn("Traceback", rendered)
            self.assertNotIn(str(ledger), rendered)

    def test_cli_model_call_receipt_is_compact_and_redacted(self) -> None:
        with temporary_ledger() as ledger:
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = token_telemetry.main(
                    [
                        "append-model-call",
                        str(ledger),
                        "--request-id",
                        "request-provider-cli",
                        "--stage",
                        "analysis",
                        "--operation",
                        "provider-call",
                        "--provider",
                        "openai",
                        "--model",
                        "gpt-5.6-sol",
                        "--agent-role",
                        "visual-structure",
                        "--token-source",
                        "provider-receipt",
                        "--measurement-boundary-id",
                        "role-test-ab-v1",
                        "--call-id-digest",
                        CALL_DIGEST,
                        "--run-id-digest",
                        RUN_DIGEST,
                        "--usage-receipt-sha256",
                        USAGE_DIGEST,
                        "--timestamp",
                        FIXED_TIME,
                        "--input-tokens",
                        "10",
                        "--cached-input-tokens",
                        "0",
                        "--output-tokens",
                        "2",
                        "--not-applicable",
                        "reasoningTokens",
                        "--not-applicable",
                        "visionTokens",
                        "--not-applicable",
                        "toolOutputBytes",
                    ]
                )
            self.assertEqual(exit_code, 0)
            rendered = output.getvalue().strip()
            receipt = json.loads(rendered)
            self.assertEqual(receipt["eventKind"], MODEL_CALL)
            self.assertLess(len(rendered), 320)
            for secret in ("openai", "gpt-5.6-sol", CALL_DIGEST, USAGE_DIGEST, "inputTokens"):
                self.assertNotIn(secret, rendered)
            saved_event = load_ledger(ledger)["events"][0]
            self.assertEqual(saved_event["inputTokens"], 10)
            self.assertIn("toolOutputBytes", saved_event["notApplicableMetrics"])

            budget_output = io.StringIO()
            with redirect_stdout(budget_output):
                budget_exit = token_telemetry.main(
                    [
                        "check-budget",
                        str(ledger),
                        "--limit",
                        "inputTokens=10",
                        "--measurement-boundary-id",
                        "role-test-ab-v1",
                        "--run-id-digest",
                        RUN_DIGEST,
                        "--expected-call",
                        f"analysis:visual-structure:{CALL_DIGEST}",
                    ]
                )
            self.assertEqual(budget_exit, 0)
            self.assertTrue(json.loads(budget_output.getvalue())["withinBudget"])

    def test_cli_output_is_compact_and_never_echoes_the_ledger(self) -> None:
        with temporary_ledger() as ledger:
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = token_telemetry.main(
                    [
                        "append",
                        str(ledger),
                        "--request-id",
                        "request-cli",
                        "--stage",
                        "build",
                        "--operation",
                        "execute-plan",
                        "--timestamp",
                        FIXED_TIME,
                        "--tool-output-bytes",
                        "1234",
                        "--not-applicable",
                        "inputTokens",
                        "--not-applicable",
                        "outputTokens",
                    ]
                )
            self.assertEqual(exit_code, 0)
            line = output.getvalue().strip()
            payload = json.loads(line)
            self.assertLess(len(line), 300)
            self.assertNotIn("events", payload)
            self.assertNotIn("toolOutputBytes", payload)
            self.assertEqual(payload["requestId"], "request-cli")
            self.assertEqual(
                load_ledger(ledger)["events"][0]["notApplicableMetrics"],
                list(TOKEN_FIELDS),
            )
            self.assertEqual(
                load_ledger(ledger)["events"][0]["eventKind"],
                LOCAL_OPERATION,
            )

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = token_telemetry.main(
                    ["check-budget", str(ledger), "--limit", "toolOutputBytes=100"]
                )
            self.assertEqual(exit_code, token_telemetry.EXIT_BUDGET_EXCEEDED)
            self.assertFalse(json.loads(output.getvalue())["withinBudget"])

    def test_cli_normalizes_non_utf8_ledger_to_safe_compact_json(self) -> None:
        with temporary_ledger() as ledger:
            ledger.write_bytes(b"\xff\xfe\x00not-utf8")
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = token_telemetry.main(["summary", str(ledger)])

            self.assertEqual(exit_code, token_telemetry.EXIT_ERROR)
            rendered = output.getvalue()
            payload = json.loads(rendered)
            self.assertEqual(payload["error"]["code"], "invalid-encoding")
            self.assertNotIn(str(ledger), rendered)
            self.assertNotIn("Traceback", rendered)
            self.assertEqual(len(rendered.splitlines()), 1)

    def test_cli_rejects_not_applicable_and_measured_overlap(self) -> None:
        with temporary_ledger() as ledger:
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = token_telemetry.main(
                    [
                        "append-model-call",
                        str(ledger),
                        "--request-id",
                        "request-overlap",
                        "--stage",
                        "build",
                        "--operation",
                        "overlap-test",
                        "--provider",
                        "openai",
                        "--model",
                        "gpt-5.6-sol",
                        "--agent-role",
                        "unit-test",
                        "--token-source",
                        "provider-receipt",
                        "--measurement-boundary-id",
                        "unit-boundary-v1",
                        "--call-id-digest",
                        CALL_DIGEST,
                        "--run-id-digest",
                        RUN_DIGEST,
                        "--usage-receipt-sha256",
                        USAGE_DIGEST,
                        "--timestamp",
                        FIXED_TIME,
                        "--input-tokens",
                        "1",
                        "--not-applicable",
                        "inputTokens",
                    ]
                )
            self.assertEqual(exit_code, token_telemetry.EXIT_ERROR)
            self.assertEqual(json.loads(output.getvalue())["error"]["code"], "validation")
            self.assertFalse(ledger.exists())

    def test_concurrent_atomic_appends_are_lossless(self) -> None:
        with temporary_ledger() as ledger:

            def append(index: int) -> None:
                append_event(
                    ledger,
                    self.event(f"request-{index}", "build", toolCallCount=1),
                    lock_timeout_seconds=20.0,
                )

            with ThreadPoolExecutor(max_workers=8) as executor:
                list(executor.map(append, range(40)))

            saved = load_ledger(ledger)
            self.assertEqual(len(saved["events"]), 40)
            self.assertEqual({event["requestId"] for event in saved["events"]}, {f"request-{i}" for i in range(40)})
            self.assertEqual(summarize_ledger(ledger)["totals"]["toolCallCount"], 40)

    def test_concurrent_cli_process_appends_are_lossless(self) -> None:
        with temporary_ledger() as ledger:
            script = Path(token_telemetry.__file__).resolve()
            environment = dict(os.environ)
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            processes = []
            for index in range(12):
                processes.append(
                    subprocess.Popen(
                        [
                            sys.executable,
                            "-B",
                            str(script),
                            "append",
                            str(ledger),
                            "--request-id",
                            f"process-{index}",
                            "--stage",
                            "build",
                            "--operation",
                            "process-test",
                            "--timestamp",
                            FIXED_TIME,
                            "--tool-call-count",
                            "1",
                        ],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        encoding="utf-8",
                        env=environment,
                    )
                )

            outputs = [process.communicate(timeout=30) for process in processes]
            for process, (stdout, stderr) in zip(processes, outputs):
                self.assertEqual(process.returncode, 0, (stdout, stderr))
                self.assertTrue(json.loads(stdout)["ok"])
                self.assertEqual(stderr, "")

            saved = load_ledger(ledger)
            self.assertEqual(len(saved["events"]), 12)
            self.assertEqual(
                {event["requestId"] for event in saved["events"]},
                {f"process-{index}" for index in range(12)},
            )

    def test_failed_atomic_replace_preserves_previous_ledger(self) -> None:
        with temporary_ledger() as ledger:
            append_event(ledger, self.event("request-original", instructionBytes=1))
            before = ledger.read_bytes()

            with mock.patch.object(token_telemetry.os, "replace", side_effect=OSError("simulated replace failure")):
                with self.assertRaises(OSError):
                    append_event(ledger, self.event("request-new", instructionBytes=2))

            self.assertEqual(ledger.read_bytes(), before)
            self.assertEqual([event["requestId"] for event in load_ledger(ledger)["events"]], ["request-original"])
            self.assertEqual(list(ledger.parent.glob(f".{ledger.name}.*.tmp")), [])

    @unittest.skipUnless(os.name == "nt", "Windows-specific sharing retry")
    def test_windows_transient_replace_permission_is_retried_without_duplicates(self) -> None:
        with temporary_ledger() as ledger:
            append_event(ledger, self.event("request-original", instructionBytes=1))
            real_replace = os.replace
            attempts = 0

            def transient_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
                nonlocal attempts
                attempts += 1
                if attempts < 3:
                    raise PermissionError("simulated transient sharing violation")
                real_replace(source, target)

            with mock.patch.object(token_telemetry.os, "replace", side_effect=transient_replace):
                append_event(ledger, self.event("request-retried", instructionBytes=2))

            self.assertEqual(attempts, 3)
            saved = load_ledger(ledger)
            self.assertEqual(
                [event["requestId"] for event in saved["events"]],
                ["request-original", "request-retried"],
            )
            self.assertEqual(list(ledger.parent.glob(f".{ledger.name}.*.tmp")), [])

    @unittest.skipUnless(os.name == "nt", "Windows-specific sharing retry")
    def test_windows_permanent_replace_permission_remains_an_error(self) -> None:
        with temporary_ledger() as ledger:
            append_event(ledger, self.event("request-original", instructionBytes=1))
            before = ledger.read_bytes()
            with (
                mock.patch.object(
                    token_telemetry.os,
                    "replace",
                    side_effect=PermissionError("simulated permanent access denial"),
                ) as replace_mock,
                mock.patch.object(token_telemetry.time, "sleep"),
            ):
                with self.assertRaises(PermissionError):
                    append_event(ledger, self.event("request-denied", instructionBytes=2))

            self.assertEqual(
                replace_mock.call_count,
                token_telemetry.WINDOWS_PERMISSION_RETRY_MAX_ATTEMPTS,
            )
            self.assertEqual(ledger.read_bytes(), before)
            self.assertEqual(
                [event["requestId"] for event in load_ledger(ledger)["events"]],
                ["request-original"],
            )
            self.assertEqual(list(ledger.parent.glob(f".{ledger.name}.*.tmp")), [])

    @unittest.skipUnless(os.name == "nt", "Windows-specific sharing retry")
    def test_windows_permission_retry_never_restarts_at_deadline(self) -> None:
        attempts = 0

        def denied_operation() -> None:
            nonlocal attempts
            attempts += 1
            raise PermissionError("simulated sharing violation at deadline")

        with (
            mock.patch.object(
                token_telemetry.time,
                "monotonic",
                side_effect=[0.99, 1.0],
            ),
            mock.patch.object(token_telemetry.time, "sleep") as sleep_mock,
        ):
            with self.assertRaises(PermissionError):
                token_telemetry._retry_windows_permission(
                    denied_operation,
                    deadline=1.0,
                )

        self.assertEqual(attempts, 1)
        sleep_mock.assert_called_once_with(
            token_telemetry.WINDOWS_PERMISSION_RETRY_INITIAL_SECONDS
        )

    def test_schema_event_fields_and_metric_contract_match_code(self) -> None:
        schema = json.loads(DEFAULT_SCHEMA.read_text(encoding="utf-8"))
        event_schema = schema["$defs"]["event"]
        self.assertFalse(event_schema["additionalProperties"])
        self.assertEqual(set(event_schema["properties"]), set(token_telemetry.EVENT_FIELDS))
        metric_any_of = {item["required"][0] for item in event_schema["anyOf"]}
        self.assertEqual(metric_any_of, set(METRIC_FIELDS))
        self.assertEqual(schema["properties"]["schemaVersion"]["const"], token_telemetry.SCHEMA_VERSION)
        self.assertEqual(schema["$defs"]["counter"]["maximum"], token_telemetry.MAX_METRIC_VALUE)
        self.assertEqual(
            set(schema["$defs"]["metricName"]["enum"]),
            set(METRIC_FIELDS),
        )
        self.assertIn("metricEventCounts", schema["$defs"]["summary"]["required"])
        self.assertIn("metricEventCounts", schema["$defs"]["summaryBucket"]["required"])
        self.assertIn("metricCoverageCounts", schema["$defs"]["summary"]["required"])
        self.assertIn("metricCoverageCounts", schema["$defs"]["summaryBucket"]["required"])
        self.assertIn("eventKindCounts", schema["$defs"]["summary"]["required"])
        self.assertIn("tokenMeasurement", schema["$defs"]["summary"]["required"])
        self.assertEqual(
            event_schema["properties"]["notApplicableMetrics"]["items"]["$ref"],
            "#/$defs/metricName",
        )
        self.assertEqual(
            event_schema["properties"]["unmeasuredMetrics"]["items"]["$ref"],
            "#/$defs/metricName",
        )
        self.assertEqual(
            set(event_schema["properties"]["eventKind"]["enum"]),
            set(EVENT_KINDS),
        )
        model_call_rule = next(
            item
            for item in event_schema["allOf"]
            if item.get("if", {}).get("properties", {}).get("eventKind", {}).get("const")
            == MODEL_CALL
        )
        self.assertIn("runIdDigest", model_call_rule["then"]["required"])
        self.assertIn("runIdDigest", token_telemetry.MODEL_CALL_REQUIRED_FIELDS)
        for field in TOKEN_FIELDS:
            self.assertIn("classify" + field[0].upper() + field[1:], schema["$defs"])
        overlap_refs = {
            item["$ref"].rsplit("/", 1)[-1]
            for item in event_schema["allOf"]
            if "$ref" in item
            and "noClassificationOverlap" in item["$ref"]
        }
        self.assertEqual(
            overlap_refs,
            {
                "noClassificationOverlap" + field[0].upper() + field[1:]
                for field in METRIC_FIELDS
            },
        )
        self.assertEqual(schema["$defs"]["digest"]["pattern"], "^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main(verbosity=2)
