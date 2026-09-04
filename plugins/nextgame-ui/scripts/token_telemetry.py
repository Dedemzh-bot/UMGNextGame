#!/usr/bin/env python3
"""Privacy-bounded token and context telemetry for NextGame UI workflows.

The importable API is intentionally small:

* :func:`make_local_operation_event` builds an exact local/proxy event.
* :func:`make_model_call_event` builds a provider-receipt or tokenizer-proxy
  model-call event without accepting raw request identifiers.
* :func:`append_event` serializes concurrent writers and atomically replaces one
  JSON ledger.
* :func:`validate_ledger_location` rejects runtime ledgers inside the plugin.
* :func:`summarize_ledger` returns totals and per-stage aggregates without
  returning event payloads.
* :func:`check_budget` compares token or proxy counters with explicit limits.

Events accept bounded identifiers, SHA-256 digests, non-negative counters, and
an explicit metric not-applicability list only.  There is no field in the
contract for prompts, responses, tool payloads, artifact contents, raw provider
request IDs, or filesystem paths.  Actual provider token counters and local
operation proxies are different event kinds; a missing or non-applicable token
counter is never interpreted as a measured zero.

Concurrency strategy
--------------------
Each ledger has a persistent ``.lock`` sidecar containing no telemetry data.
Writers take both an in-process mutex and an operating-system file lock.  OS
locks are released automatically if a process exits.  While holding the lock, a
writer reads and validates the current ledger, appends one event, writes a
validated replacement to a temporary file in the same directory, flushes and
fsyncs it, and calls ``os.replace``.  Readers therefore see either the complete
old ledger or the complete new ledger; concurrent appends cannot overwrite one
another.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence


SCHEMA_VERSION = "1.1"
LEGACY_SCHEMA_VERSION = "1.0"
SUPPORTED_LEDGER_SCHEMA_VERSIONS = (LEGACY_SCHEMA_VERSION, SCHEMA_VERSION)
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCHEMA = PLUGIN_ROOT / "assets" / "token-telemetry.schema.json"

IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
TIMESTAMP_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)
MAX_METRIC_VALUE = 9_223_372_036_854_775_807
DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")

EVENT_KIND_FIELD = "eventKind"
LOCAL_OPERATION = "local-operation"
MODEL_CALL = "model-call"
LEGACY_UNCLASSIFIED = "legacy-unclassified"
EVENT_KINDS = (LOCAL_OPERATION, MODEL_CALL, LEGACY_UNCLASSIFIED)

PROVIDER_RECEIPT = "provider-receipt"
TOKENIZER_PROXY = "tokenizer-proxy"
TOKEN_SOURCES = (PROVIDER_RECEIPT, TOKENIZER_PROXY)

TOKEN_FIELDS = (
    "inputTokens",
    "cachedInputTokens",
    "outputTokens",
    "reasoningTokens",
    "visionTokens",
)
OPERATION_PROXY_FIELDS = (
    "instructionBytes",
    "toolInputBytes",
    "toolOutputBytes",
    "artifactBytes",
    "imageCount",
    "imagePixels",
    "agentCount",
    "toolCallCount",
    "cacheHitCount",
    "cacheMissCount",
)
TOKENIZER_PROXY_FIELD = "tokenizerProxyTokens"
PROXY_FIELDS = OPERATION_PROXY_FIELDS + (TOKENIZER_PROXY_FIELD,)
METRIC_FIELDS = TOKEN_FIELDS + PROXY_FIELDS
REQUIRED_EVENT_FIELDS = (
    "requestId",
    "stage",
    "operation",
    "timestamp",
    EVENT_KIND_FIELD,
)
NOT_APPLICABLE_FIELD = "notApplicableMetrics"
UNMEASURED_FIELD = "unmeasuredMetrics"
MODEL_CALL_REQUIRED_FIELDS = (
    "provider",
    "model",
    "agentRole",
    "tokenSource",
    "measurementBoundaryId",
    "callIdDigest",
    "runIdDigest",
)
MODEL_CALL_OPTIONAL_FIELDS = (
    "usageReceiptSha256",
    "pluginTreeSha256",
    "qualityReceiptSha256",
    "tokenizerEncoding",
    "tokenizerVersion",
)
MODEL_CALL_METADATA_FIELDS = MODEL_CALL_REQUIRED_FIELDS + MODEL_CALL_OPTIONAL_FIELDS
EVENT_FIELDS = (
    REQUIRED_EVENT_FIELDS
    + (NOT_APPLICABLE_FIELD, UNMEASURED_FIELD)
    + MODEL_CALL_METADATA_FIELDS
    + METRIC_FIELDS
)

# Version 1.0 never recorded an event kind.  Only the executor operations below
# have a deterministic local boundary; every other legacy event remains
# unclassified and therefore cannot satisfy a token-measurement gate.
LEGACY_LOCAL_OPERATIONS = {
    "execute-plan",
    "execute-plan-checkpoint",
    "execute-plan-completed",
    "execute-plan-failed",
}

EXIT_ERROR = 1
EXIT_BUDGET_EXCEEDED = 3

# Windows can transiently deny an otherwise valid replace/open while virus
# scanners or filesystem filters release a handle.  Retries stay small and
# bounded; a persistent ACL/share failure is re-raised unchanged.
WINDOWS_PERMISSION_RETRY_MAX_ATTEMPTS = 8
WINDOWS_PERMISSION_RETRY_INITIAL_SECONDS = 0.005
WINDOWS_PERMISSION_RETRY_MAX_SECONDS = 0.05


class TelemetryValidationError(ValueError):
    """Raised when an event, ledger, summary, or budget is invalid."""

    def __init__(self, messages: str | Iterable[str]) -> None:
        if isinstance(messages, str):
            normalized = (messages,)
        else:
            normalized = tuple(messages)
        self.messages = normalized
        super().__init__("; ".join(normalized))


class TelemetryLockTimeout(TimeoutError):
    """Raised when another writer holds a ledger lock past the timeout."""


def validate_ledger_location(path: str | os.PathLike[str]) -> None:
    """Keep runtime telemetry out of the authoritative plugin package."""

    try:
        Path(path).resolve().relative_to(PLUGIN_ROOT.resolve())
    except ValueError:
        return
    raise TelemetryValidationError(
        "ledger must not be stored inside the NextGame UI plugin package"
    )


def utc_timestamp() -> str:
    """Return an RFC 3339 UTC timestamp accepted by the telemetry contract."""

    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _validate_identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER_PATTERN.fullmatch(value):
        raise TelemetryValidationError(
            f"{field} must be a 1-128 character identifier containing only letters, digits, '.', '_' or '-'"
        )
    return value


def _validate_timestamp(value: Any) -> str:
    if not isinstance(value, str) or not TIMESTAMP_PATTERN.fullmatch(value):
        raise TelemetryValidationError("timestamp must be an RFC 3339 UTC timestamp ending in 'Z'")
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise TelemetryValidationError("timestamp contains an invalid calendar date or time") from error
    return value


def _validate_counter(value: Any, field: str) -> int:
    # bool is an int subclass, but it is never a meaningful counter here.
    if isinstance(value, bool) or not isinstance(value, int):
        if isinstance(value, float) and not math.isfinite(value):
            raise TelemetryValidationError(f"{field} must not be NaN or infinite")
        raise TelemetryValidationError(f"{field} must be a non-negative integer")
    if value < 0:
        raise TelemetryValidationError(f"{field} must be non-negative")
    if value > MAX_METRIC_VALUE:
        raise TelemetryValidationError(f"{field} exceeds the supported 64-bit counter range")
    return value


def _validate_digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or not DIGEST_PATTERN.fullmatch(value):
        raise TelemetryValidationError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _normalize_metric_list(event: Mapping[str, Any], field_name: str) -> list[str]:
    if field_name not in event:
        return []
    values = event[field_name]
    if not isinstance(values, list) or not values:
        raise TelemetryValidationError(
            f"{field_name} must be a non-empty array of metric names"
        )
    if any(not isinstance(field, str) or field not in METRIC_FIELDS for field in values):
        raise TelemetryValidationError(f"{field_name} contains unknown metrics")
    if len(set(values)) != len(values):
        raise TelemetryValidationError(f"{field_name} must not contain duplicates")
    value_set = set(values)
    return [field for field in METRIC_FIELDS if field in value_set]


def validate_event(
    event: Any,
    *,
    allow_legacy_kind: bool = False,
) -> dict[str, Any]:
    """Validate and canonicalize one event.

    Unknown fields are rejected.  This is also the privacy boundary that keeps
    raw prompts, tool payloads, artifact contents, and paths out of the ledger.
    """

    if not isinstance(event, Mapping):
        raise TelemetryValidationError("event must be a JSON object")

    string_keys = {key for key in event if isinstance(key, str)}
    non_string_keys = [key for key in event if not isinstance(key, str)]
    unknown = sorted(string_keys - set(EVENT_FIELDS))
    missing = [field for field in REQUIRED_EVENT_FIELDS if field not in event]
    errors: list[str] = []
    if non_string_keys:
        errors.append("event property names must be strings")
    if unknown:
        errors.append(f"event contains {len(unknown)} unknown field(s)")
    if missing:
        errors.append(f"missing event fields: {', '.join(missing)}")
    if not any(field in event for field in METRIC_FIELDS):
        errors.append("event must contain at least one token or proxy metric")
    if errors:
        raise TelemetryValidationError(errors)

    event_kind = event.get(EVENT_KIND_FIELD)
    if event_kind not in EVENT_KINDS:
        raise TelemetryValidationError(
            f"eventKind must be one of: {', '.join(EVENT_KINDS)}"
        )
    if event_kind == LEGACY_UNCLASSIFIED and not allow_legacy_kind:
        raise TelemetryValidationError(
            "legacy-unclassified events may only be loaded by the versioned ledger migrator"
        )

    normalized: dict[str, Any] = {
        "requestId": _validate_identifier(event["requestId"], "requestId"),
        "stage": _validate_identifier(event["stage"], "stage"),
        "operation": _validate_identifier(event["operation"], "operation"),
        "timestamp": _validate_timestamp(event["timestamp"]),
        EVENT_KIND_FIELD: event_kind,
    }
    not_applicable = _normalize_metric_list(event, NOT_APPLICABLE_FIELD)
    unmeasured = _normalize_metric_list(event, UNMEASURED_FIELD)
    not_applicable_set = set(not_applicable)
    unmeasured_set = set(unmeasured)
    measured_set = {field for field in METRIC_FIELDS if field in event}
    overlap = sorted(not_applicable_set & unmeasured_set)
    if overlap:
        raise TelemetryValidationError(
            "notApplicableMetrics and unmeasuredMetrics must not overlap"
        )
    measured_overlap = sorted(measured_set & (not_applicable_set | unmeasured_set))
    if measured_overlap:
        raise TelemetryValidationError(
            "measured event metrics must not overlap notApplicableMetrics or unmeasuredMetrics"
        )
    if not_applicable:
        normalized[NOT_APPLICABLE_FIELD] = not_applicable
    if unmeasured:
        normalized[UNMEASURED_FIELD] = unmeasured

    identifier_metadata = (
        "provider",
        "model",
        "agentRole",
        "measurementBoundaryId",
        "tokenizerEncoding",
        "tokenizerVersion",
    )
    digest_metadata = (
        "callIdDigest",
        "runIdDigest",
        "usageReceiptSha256",
        "pluginTreeSha256",
        "qualityReceiptSha256",
    )
    for field in identifier_metadata:
        if field in event:
            normalized[field] = _validate_identifier(event[field], field)
    if "tokenSource" in event:
        token_source = event["tokenSource"]
        if token_source not in TOKEN_SOURCES:
            raise TelemetryValidationError(
                f"tokenSource must be one of: {', '.join(TOKEN_SOURCES)}"
            )
        normalized["tokenSource"] = token_source
    for field in digest_metadata:
        if field in event:
            normalized[field] = _validate_digest(event[field], field)
    for field in METRIC_FIELDS:
        if field in event:
            normalized[field] = _validate_counter(event[field], field)

    metadata_present = [field for field in MODEL_CALL_METADATA_FIELDS if field in event]
    token_measured = measured_set & set(TOKEN_FIELDS)
    token_not_applicable = not_applicable_set & set(TOKEN_FIELDS)
    token_unmeasured = unmeasured_set & set(TOKEN_FIELDS)

    if event_kind == LOCAL_OPERATION:
        if metadata_present:
            raise TelemetryValidationError(
                "local-operation events must not contain model-call metadata"
            )
        if token_measured:
            raise TelemetryValidationError(
                "local-operation events must not contain actual token counters"
            )
        if token_unmeasured:
            raise TelemetryValidationError(
                "local-operation token metrics are not applicable, not unmeasured"
            )
        if token_not_applicable != set(TOKEN_FIELDS):
            raise TelemetryValidationError(
                "local-operation events must mark every token metric not applicable"
            )
        if TOKENIZER_PROXY_FIELD in measured_set:
            raise TelemetryValidationError(
                "tokenizerProxyTokens belongs to a model-call tokenizer-proxy event"
            )
    elif event_kind == MODEL_CALL:
        missing_metadata = [field for field in MODEL_CALL_REQUIRED_FIELDS if field not in event]
        if missing_metadata:
            raise TelemetryValidationError(
                "model-call event is missing metadata: " + ", ".join(missing_metadata)
            )
        if "inputTokens" in token_not_applicable or "outputTokens" in token_not_applicable:
            raise TelemetryValidationError(
                "inputTokens and outputTokens are always applicable to model-call events"
            )
        classified_tokens = token_measured | token_not_applicable | token_unmeasured
        if classified_tokens != set(TOKEN_FIELDS):
            missing_tokens = [field for field in TOKEN_FIELDS if field not in classified_tokens]
            raise TelemetryValidationError(
                "model-call token metrics must be measured, not applicable, or unmeasured: "
                + ", ".join(missing_tokens)
            )
        token_source = normalized["tokenSource"]
        if token_source == PROVIDER_RECEIPT:
            if "usageReceiptSha256" not in normalized:
                raise TelemetryValidationError(
                    "provider-receipt model calls require usageReceiptSha256"
                )
            if not token_measured:
                raise TelemetryValidationError(
                    "provider-receipt model calls require at least one measured token counter"
                )
            forbidden = [
                field
                for field in (TOKENIZER_PROXY_FIELD, "tokenizerEncoding", "tokenizerVersion")
                if field in event
            ]
            if forbidden:
                raise TelemetryValidationError(
                    "provider-receipt model calls must not contain tokenizer proxy fields"
                )
        else:
            required_proxy = (
                TOKENIZER_PROXY_FIELD,
                "tokenizerEncoding",
                "tokenizerVersion",
            )
            missing_proxy = [field for field in required_proxy if field not in event]
            if missing_proxy:
                raise TelemetryValidationError(
                    "tokenizer-proxy model calls are missing: " + ", ".join(missing_proxy)
                )
            if token_measured:
                raise TelemetryValidationError(
                    "tokenizer-proxy model calls must not contain provider token counters"
                )
            if "usageReceiptSha256" in event:
                raise TelemetryValidationError(
                    "tokenizer-proxy model calls must not claim a provider usage receipt"
                )
    return normalized


def make_event(
    request_id: str,
    stage: str,
    operation: str,
    *,
    event_kind: str,
    timestamp: str | None = None,
    not_applicable_metrics: Iterable[str] | None = None,
    unmeasured_metrics: Iterable[str] | None = None,
    provider: str | None = None,
    model: str | None = None,
    agent_role: str | None = None,
    token_source: str | None = None,
    measurement_boundary_id: str | None = None,
    call_id_digest: str | None = None,
    run_id_digest: str | None = None,
    usage_receipt_sha256: str | None = None,
    plugin_tree_sha256: str | None = None,
    quality_receipt_sha256: str | None = None,
    tokenizer_encoding: str | None = None,
    tokenizer_version: str | None = None,
    **metrics: int,
) -> dict[str, Any]:
    """Build a validated explicit-kind event.

    Prefer :func:`make_local_operation_event` or :func:`make_model_call_event`;
    this lower-level helper requires callers to provide the complete token
    classification themselves.
    """

    if event_kind == LEGACY_UNCLASSIFIED:
        raise TelemetryValidationError(
            "new events must not use the legacy-unclassified event kind"
        )

    event: dict[str, Any] = {
        "requestId": request_id,
        "stage": stage,
        "operation": operation,
        "timestamp": timestamp if timestamp is not None else utc_timestamp(),
        EVENT_KIND_FIELD: event_kind,
    }
    for values, field_name, parameter_name in (
        (not_applicable_metrics, NOT_APPLICABLE_FIELD, "not_applicable_metrics"),
        (unmeasured_metrics, UNMEASURED_FIELD, "unmeasured_metrics"),
    ):
        if values is None:
            continue
        if isinstance(values, str):
            raise TelemetryValidationError(
                f"{parameter_name} must be an iterable of metric names, not a string"
            )
        try:
            normalized_values = list(values)
        except TypeError as error:
            raise TelemetryValidationError(
                f"{parameter_name} must be an iterable of metric names"
            ) from error
        if normalized_values:
            event[field_name] = normalized_values
    metadata = {
        "provider": provider,
        "model": model,
        "agentRole": agent_role,
        "tokenSource": token_source,
        "measurementBoundaryId": measurement_boundary_id,
        "callIdDigest": call_id_digest,
        "runIdDigest": run_id_digest,
        "usageReceiptSha256": usage_receipt_sha256,
        "pluginTreeSha256": plugin_tree_sha256,
        "qualityReceiptSha256": quality_receipt_sha256,
        "tokenizerEncoding": tokenizer_encoding,
        "tokenizerVersion": tokenizer_version,
    }
    event.update({field: value for field, value in metadata.items() if value is not None})
    event.update(metrics)
    return validate_event(event)


def make_local_operation_event(
    request_id: str,
    stage: str,
    operation: str,
    *,
    timestamp: str | None = None,
    not_applicable_metrics: Iterable[str] | None = None,
    unmeasured_metrics: Iterable[str] | None = None,
    **metrics: int,
) -> dict[str, Any]:
    """Build a local operation event whose token metrics are inherently N/A."""

    extra_not_applicable = list(not_applicable_metrics or ())
    combined_not_applicable = list(TOKEN_FIELDS)
    combined_not_applicable.extend(
        field for field in extra_not_applicable if field not in TOKEN_FIELDS
    )
    return make_event(
        request_id,
        stage,
        operation,
        event_kind=LOCAL_OPERATION,
        timestamp=timestamp,
        not_applicable_metrics=combined_not_applicable,
        unmeasured_metrics=unmeasured_metrics,
        **metrics,
    )


def make_model_call_event(
    request_id: str,
    stage: str,
    operation: str,
    *,
    provider: str,
    model: str,
    agent_role: str,
    token_source: str,
    measurement_boundary_id: str,
    call_id_digest: str,
    run_id_digest: str,
    timestamp: str | None = None,
    not_applicable_metrics: Iterable[str] | None = None,
    unmeasured_metrics: Iterable[str] | None = None,
    usage_receipt_sha256: str | None = None,
    plugin_tree_sha256: str | None = None,
    quality_receipt_sha256: str | None = None,
    tokenizer_encoding: str | None = None,
    tokenizer_version: str | None = None,
    **metrics: int,
) -> dict[str, Any]:
    """Build one model-call event with a complete, fail-closed token tri-state."""

    not_applicable = list(not_applicable_metrics or ())
    unmeasured = list(unmeasured_metrics or ())
    classified = set(not_applicable) | set(unmeasured) | {
        field for field in TOKEN_FIELDS if field in metrics
    }
    unmeasured.extend(field for field in TOKEN_FIELDS if field not in classified)
    return make_event(
        request_id,
        stage,
        operation,
        event_kind=MODEL_CALL,
        timestamp=timestamp,
        not_applicable_metrics=not_applicable,
        unmeasured_metrics=unmeasured,
        provider=provider,
        model=model,
        agent_role=agent_role,
        token_source=token_source,
        measurement_boundary_id=measurement_boundary_id,
        call_id_digest=call_id_digest,
        run_id_digest=run_id_digest,
        usage_receipt_sha256=usage_receipt_sha256,
        plugin_tree_sha256=plugin_tree_sha256,
        quality_receipt_sha256=quality_receipt_sha256,
        tokenizer_encoding=tokenizer_encoding,
        tokenizer_version=tokenizer_version,
        **metrics,
    )


def _migrate_legacy_event(event: Any) -> dict[str, Any]:
    if not isinstance(event, Mapping):
        raise TelemetryValidationError("event must be a JSON object")
    if EVENT_KIND_FIELD in event or UNMEASURED_FIELD in event:
        raise TelemetryValidationError(
            "schemaVersion 1.0 events must not contain 1.1 classification fields"
        )
    migrated = dict(event)
    not_applicable = event.get(NOT_APPLICABLE_FIELD, [])
    legacy_not_applicable_is_safe = (
        isinstance(not_applicable, list)
        and all(isinstance(field, str) for field in not_applicable)
    )
    known_local = (
        event.get("operation") in LEGACY_LOCAL_OPERATIONS
        and not any(field in event for field in TOKEN_FIELDS)
        and legacy_not_applicable_is_safe
        and set(TOKEN_FIELDS).issubset(set(not_applicable))
    )
    migrated[EVENT_KIND_FIELD] = (
        LOCAL_OPERATION if known_local else LEGACY_UNCLASSIFIED
    )
    return migrated


def _model_receipt_key(event: Mapping[str, Any]) -> tuple[Any, ...] | None:
    if event.get(EVENT_KIND_FIELD) != MODEL_CALL:
        return None
    if event.get("tokenSource") == PROVIDER_RECEIPT:
        return (
            PROVIDER_RECEIPT,
            event.get("provider"),
            event.get("usageReceiptSha256"),
        )
    return (
        TOKENIZER_PROXY,
        event.get("provider"),
        event.get("runIdDigest"),
        event.get("measurementBoundaryId"),
        event.get("callIdDigest"),
    )


def validate_ledger(ledger: Any) -> dict[str, Any]:
    """Validate and canonicalize a complete ledger."""

    if not isinstance(ledger, Mapping):
        raise TelemetryValidationError("ledger must be a JSON object")
    allowed = {"schemaVersion", "events"}
    string_keys = {key for key in ledger if isinstance(key, str)}
    unknown = sorted(string_keys - allowed)
    non_string_keys = [key for key in ledger if not isinstance(key, str)]
    errors: list[str] = []
    if non_string_keys:
        errors.append("ledger property names must be strings")
    if unknown:
        errors.append(f"ledger contains {len(unknown)} unknown field(s)")
    schema_version = ledger.get("schemaVersion")
    if schema_version not in SUPPORTED_LEDGER_SCHEMA_VERSIONS:
        errors.append(
            "schemaVersion must equal one of: "
            + ", ".join(repr(value) for value in SUPPORTED_LEDGER_SCHEMA_VERSIONS)
        )
    events = ledger.get("events")
    if not isinstance(events, list):
        errors.append("events must be an array")
    if errors:
        raise TelemetryValidationError(errors)

    normalized_events: list[dict[str, Any]] = []
    event_errors: list[str] = []
    for index, event in enumerate(events):
        try:
            candidate = (
                _migrate_legacy_event(event)
                if schema_version == LEGACY_SCHEMA_VERSION
                else event
            )
            normalized_events.append(
                validate_event(candidate, allow_legacy_kind=True)
            )
        except TelemetryValidationError as error:
            event_errors.extend(f"events[{index}]: {message}" for message in error.messages)
    if event_errors:
        raise TelemetryValidationError(event_errors)
    seen_receipts: dict[tuple[Any, ...], int] = {}
    seen_logical_calls: dict[tuple[str, str, str], int] = {}
    for index, event in enumerate(normalized_events):
        receipt_key = _model_receipt_key(event)
        if receipt_key is None:
            continue
        if receipt_key in seen_receipts:
            event_errors.append(
                f"events[{index}]: duplicate model-call receipt; first seen at events[{seen_receipts[receipt_key]}]"
            )
        else:
            seen_receipts[receipt_key] = index
        logical_key = (
            event["runIdDigest"],
            event["measurementBoundaryId"],
            event["callIdDigest"],
        )
        if logical_key in seen_logical_calls:
            event_errors.append(
                f"events[{index}]: duplicate logical model call; first seen at events[{seen_logical_calls[logical_key]}]"
            )
        else:
            seen_logical_calls[logical_key] = index
    if event_errors:
        raise TelemetryValidationError(event_errors)
    return {"schemaVersion": SCHEMA_VERSION, "events": normalized_events}


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise TelemetryValidationError("duplicate JSON properties are forbidden")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise TelemetryValidationError(f"non-finite JSON number is forbidden: {value}")


def load_ledger(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Load and validate a ledger without exposing it through the CLI."""

    with Path(path).open("r", encoding="utf-8") as stream:
        data = json.load(
            stream,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    return validate_ledger(data)


_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


def _thread_lock_for(path: Path) -> threading.Lock:
    key = os.path.normcase(str(path.resolve(strict=False)))
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.Lock())


def _try_os_lock(stream: Any) -> bool:
    stream.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except (OSError, BlockingIOError):
        return False


def _unlock_os_file(stream: Any) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _retry_windows_permission(operation: Any, *, deadline: float) -> Any:
    """Retry transient Windows PermissionError without crossing the lock deadline."""

    attempt = 1
    delay = WINDOWS_PERMISSION_RETRY_INITIAL_SECONDS
    last_error: PermissionError | None = None
    while True:
        if last_error is not None and time.monotonic() >= deadline:
            raise last_error
        try:
            return operation()
        except PermissionError as error:
            now = time.monotonic()
            if (
                os.name != "nt"
                or attempt >= WINDOWS_PERMISSION_RETRY_MAX_ATTEMPTS
                or now >= deadline
            ):
                raise
            last_error = error
            time.sleep(min(delay, max(0.0, deadline - now)))
            delay = min(delay * 2.0, WINDOWS_PERMISSION_RETRY_MAX_SECONDS)
            attempt += 1


@contextmanager
def _ledger_write_lock(path: Path, timeout_seconds: float) -> Iterator[float]:
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
        raise TelemetryValidationError("lock timeout must be a positive finite number")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise TelemetryValidationError("lock timeout must be a positive finite number")

    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + float(timeout_seconds)
    thread_lock = _thread_lock_for(path)
    if not thread_lock.acquire(timeout=float(timeout_seconds)):
        raise TelemetryLockTimeout("timed out waiting for the in-process telemetry ledger lock")
    try:
        lock_path = path.with_name(path.name + ".lock")
        lock_stream = _retry_windows_permission(
            lambda: lock_path.open("a+b"),
            deadline=deadline,
        )
        with lock_stream:
            lock_stream.seek(0, os.SEEK_END)
            if lock_stream.tell() == 0:
                lock_stream.write(b"0")
                lock_stream.flush()
                os.fsync(lock_stream.fileno())

            while not _try_os_lock(lock_stream):
                if time.monotonic() >= deadline:
                    raise TelemetryLockTimeout("timed out waiting for the telemetry ledger file lock")
                time.sleep(min(0.025, max(0.001, deadline - time.monotonic())))
            try:
                yield deadline
            finally:
                _unlock_os_file(lock_stream)
    finally:
        thread_lock.release()


def _atomic_write_ledger(
    path: Path,
    ledger: Mapping[str, Any],
    *,
    permission_retry_deadline: float,
) -> None:
    canonical = validate_ledger(ledger)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            json.dump(canonical, stream, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        _retry_windows_permission(
            lambda: os.replace(temporary_path, path),
            deadline=permission_retry_deadline,
        )
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def append_event(
    path: str | os.PathLike[str],
    event: Mapping[str, Any],
    *,
    lock_timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    """Append one validated event and return a compact receipt.

    The receipt never contains the ledger path, metric payload, or existing
    events, so it is safe to print from a build orchestration layer.
    """

    ledger_path = Path(path)
    validate_ledger_location(ledger_path)
    canonical_event = validate_event(event)
    action = "append"
    with _ledger_write_lock(ledger_path, lock_timeout_seconds) as lock_deadline:
        if ledger_path.exists():
            ledger = _retry_windows_permission(
                lambda: load_ledger(ledger_path),
                deadline=lock_deadline,
            )
        else:
            ledger = {"schemaVersion": SCHEMA_VERSION, "events": []}
        if canonical_event[EVENT_KIND_FIELD] == MODEL_CALL:
            receipt_key = _model_receipt_key(canonical_event)
            matches = [
                existing
                for existing in ledger["events"]
                if _model_receipt_key(existing) == receipt_key
            ]
            if matches:
                without_timestamp = {
                    key: value
                    for key, value in canonical_event.items()
                    if key != "timestamp"
                }
                if any(
                    {
                        key: value
                        for key, value in existing.items()
                        if key != "timestamp"
                    }
                    != without_timestamp
                    for existing in matches
                ):
                    raise TelemetryValidationError(
                        "model-call receipt conflicts with an existing logical call"
                    )
                action = "already-recorded"
            else:
                ledger["events"].append(canonical_event)
        else:
            ledger["events"].append(canonical_event)
        if action == "append" or ledger.get("schemaVersion") != SCHEMA_VERSION:
            _atomic_write_ledger(
                ledger_path,
                ledger,
                permission_retry_deadline=lock_deadline,
            )
        event_count = len(ledger["events"])

    return {
        "ok": True,
        "action": action,
        "eventCount": event_count,
        "requestId": canonical_event["requestId"],
        "stage": canonical_event["stage"],
        "operation": canonical_event["operation"],
        EVENT_KIND_FIELD: canonical_event[EVENT_KIND_FIELD],
    }


def summarize_events(events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate validated events globally and by stage."""

    totals: dict[str, int] = {}
    metric_event_counts: dict[str, int] = {}
    metric_coverage_counts: dict[str, int] = {}
    event_kind_counts = {kind: 0 for kind in EVENT_KINDS}
    token_measurement = {
        field: {
            "applicableCallCount": 0,
            "measuredCallCount": 0,
            "notApplicableCount": 0,
            "unmeasuredCallCount": 0,
        }
        for field in TOKEN_FIELDS
    }
    stage_data: dict[str, dict[str, Any]] = {}
    event_count = 0
    for raw_event in events:
        event = validate_event(raw_event, allow_legacy_kind=True)
        event_count += 1
        stage = event["stage"]
        bucket = stage_data.setdefault(
            stage,
            {
                "eventCount": 0,
                "totals": {},
                "metricEventCounts": {},
                "metricCoverageCounts": {},
                "eventKindCounts": {kind: 0 for kind in EVENT_KINDS},
                "tokenMeasurement": {
                    field: {
                        "applicableCallCount": 0,
                        "measuredCallCount": 0,
                        "notApplicableCount": 0,
                        "unmeasuredCallCount": 0,
                    }
                    for field in TOKEN_FIELDS
                },
            },
        )
        bucket["eventCount"] += 1
        event_kind = event[EVENT_KIND_FIELD]
        event_kind_counts[event_kind] += 1
        bucket["eventKindCounts"][event_kind] += 1
        not_applicable = set(event.get(NOT_APPLICABLE_FIELD, []))
        unmeasured = set(event.get(UNMEASURED_FIELD, []))
        if event_kind == MODEL_CALL:
            for field in TOKEN_FIELDS:
                total_measurement = token_measurement[field]
                stage_measurement = bucket["tokenMeasurement"][field]
                if field in not_applicable:
                    total_measurement["notApplicableCount"] += 1
                    stage_measurement["notApplicableCount"] += 1
                    continue
                total_measurement["applicableCallCount"] += 1
                stage_measurement["applicableCallCount"] += 1
                if field in event:
                    total_measurement["measuredCallCount"] += 1
                    stage_measurement["measuredCallCount"] += 1
                elif field in unmeasured:
                    total_measurement["unmeasuredCallCount"] += 1
                    stage_measurement["unmeasuredCallCount"] += 1
                else:  # validate_event makes this unreachable for 1.1 model calls.
                    raise TelemetryValidationError(
                        f"model-call token classification is incomplete for {field}"
                    )
        for field in METRIC_FIELDS:
            if field in not_applicable:
                metric_coverage_counts[field] = metric_coverage_counts.get(field, 0) + 1
                bucket["metricCoverageCounts"][field] = (
                    bucket["metricCoverageCounts"].get(field, 0) + 1
                )
                continue
            if field not in event:
                continue
            totals[field] = totals.get(field, 0) + event[field]
            metric_event_counts[field] = metric_event_counts.get(field, 0) + 1
            metric_coverage_counts[field] = metric_coverage_counts.get(field, 0) + 1
            bucket["totals"][field] = bucket["totals"].get(field, 0) + event[field]
            bucket["metricEventCounts"][field] = bucket["metricEventCounts"].get(field, 0) + 1
            bucket["metricCoverageCounts"][field] = (
                bucket["metricCoverageCounts"].get(field, 0) + 1
            )

    ordered_totals = {field: totals[field] for field in METRIC_FIELDS if field in totals}
    ordered_metric_event_counts = {
        field: metric_event_counts[field]
        for field in METRIC_FIELDS
        if field in metric_event_counts
    }
    ordered_metric_coverage_counts = {
        field: metric_coverage_counts[field]
        for field in METRIC_FIELDS
        if field in metric_coverage_counts
    }
    ordered_stages: dict[str, Any] = {}
    for stage in sorted(stage_data):
        bucket = stage_data[stage]
        ordered_stages[stage] = {
            "eventCount": bucket["eventCount"],
            "totals": {
                field: bucket["totals"][field]
                for field in METRIC_FIELDS
                if field in bucket["totals"]
            },
            "metricEventCounts": {
                field: bucket["metricEventCounts"][field]
                for field in METRIC_FIELDS
                if field in bucket["metricEventCounts"]
            },
            "metricCoverageCounts": {
                field: bucket["metricCoverageCounts"][field]
                for field in METRIC_FIELDS
                if field in bucket["metricCoverageCounts"]
            },
            "eventKindCounts": {
                kind: bucket["eventKindCounts"][kind] for kind in EVENT_KINDS
            },
            "tokenMeasurement": bucket["tokenMeasurement"],
        }
    return {
        "ok": True,
        "schemaVersion": SCHEMA_VERSION,
        "eventCount": event_count,
        "totals": ordered_totals,
        "metricEventCounts": ordered_metric_event_counts,
        "metricCoverageCounts": ordered_metric_coverage_counts,
        "eventKindCounts": event_kind_counts,
        "tokenMeasurement": token_measurement,
        "byStage": ordered_stages,
    }


def summarize_ledger(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Load a ledger and return its compact aggregate only."""

    return summarize_events(load_ledger(path)["events"])


def _validate_limits(limits: Mapping[str, Any] | None, label: str) -> dict[str, int]:
    if limits is None:
        return {}
    if not isinstance(limits, Mapping):
        raise TelemetryValidationError(f"{label} must be an object")
    unknown = sorted(repr(key) for key in limits if not isinstance(key, str) or key not in METRIC_FIELDS)
    if unknown:
        raise TelemetryValidationError(f"{label} contains {len(unknown)} unknown metric(s)")
    return {field: _validate_counter(limits[field], f"{label}.{field}") for field in METRIC_FIELDS if field in limits}


def _validate_event_kind_counts(
    counts: Any,
    scope_event_count: int,
    label: str,
) -> dict[str, int]:
    if not isinstance(counts, Mapping):
        raise TelemetryValidationError(f"{label} must be an object")
    unknown = sorted(str(key) for key in counts if key not in EVENT_KINDS)
    if unknown:
        raise TelemetryValidationError(f"{label} contains unknown event kinds")
    canonical = {
        kind: _validate_counter(counts.get(kind, 0), f"{label}.{kind}")
        for kind in EVENT_KINDS
    }
    if sum(canonical.values()) != scope_event_count:
        raise TelemetryValidationError(f"{label} must sum to the scope event count")
    return canonical


def _validate_token_measurement(
    measurement: Any,
    model_call_count: int,
    label: str,
) -> dict[str, dict[str, int]]:
    if not isinstance(measurement, Mapping):
        raise TelemetryValidationError(f"{label} must be an object")
    unknown = sorted(str(key) for key in measurement if key not in TOKEN_FIELDS)
    missing = [field for field in TOKEN_FIELDS if field not in measurement]
    if unknown or missing:
        raise TelemetryValidationError(
            f"{label} must contain exactly the token metric fields"
        )
    result: dict[str, dict[str, int]] = {}
    expected_keys = {
        "applicableCallCount",
        "measuredCallCount",
        "notApplicableCount",
        "unmeasuredCallCount",
    }
    for field in TOKEN_FIELDS:
        raw = measurement[field]
        if not isinstance(raw, Mapping) or set(raw) != expected_keys:
            raise TelemetryValidationError(
                f"{label}.{field} must contain the complete measurement counts"
            )
        record = {
            key: _validate_counter(raw[key], f"{label}.{field}.{key}")
            for key in (
                "applicableCallCount",
                "measuredCallCount",
                "notApplicableCount",
                "unmeasuredCallCount",
            )
        }
        if (
            record["applicableCallCount"] + record["notApplicableCount"]
            != model_call_count
        ):
            raise TelemetryValidationError(
                f"{label}.{field} applicability counts do not match model-call count"
            )
        if (
            record["measuredCallCount"] + record["unmeasuredCallCount"]
            != record["applicableCallCount"]
        ):
            raise TelemetryValidationError(
                f"{label}.{field} measurement counts do not match applicable calls"
            )
        result[field] = record
    return result


def _token_unavailability(
    measurement: Mapping[str, int],
    event_kind_counts: Mapping[str, int],
) -> str | None:
    if event_kind_counts[MODEL_CALL] == 0:
        return "no-model-call-receipts"
    if event_kind_counts[LEGACY_UNCLASSIFIED] > 0:
        return "ambiguous-legacy-events"
    if measurement["applicableCallCount"] == 0:
        return "no-applicable-model-calls"
    if measurement["measuredCallCount"] != measurement["applicableCallCount"]:
        return "incomplete-model-call-measurement"
    return None


def check_budget(
    summary: Mapping[str, Any],
    *,
    total_limits: Mapping[str, int] | None = None,
    stage_limits: Mapping[str, Mapping[str, int]] | None = None,
) -> dict[str, Any]:
    """Check aggregate counters against total and per-stage upper bounds."""

    if not isinstance(summary, Mapping) or summary.get("schemaVersion") != SCHEMA_VERSION:
        raise TelemetryValidationError("summary must be produced by summarize_events or summarize_ledger")
    totals = summary.get("totals")
    metric_event_counts = summary.get("metricEventCounts")
    metric_coverage_counts = summary.get("metricCoverageCounts")
    event_kind_counts = summary.get("eventKindCounts")
    token_measurement = summary.get("tokenMeasurement")
    by_stage = summary.get("byStage")
    if (
        not isinstance(totals, Mapping)
        or not isinstance(metric_event_counts, Mapping)
        or not isinstance(metric_coverage_counts, Mapping)
        or not isinstance(event_kind_counts, Mapping)
        or not isinstance(token_measurement, Mapping)
        or not isinstance(by_stage, Mapping)
    ):
        raise TelemetryValidationError(
            "summary totals, metric counts, eventKindCounts, tokenMeasurement, and byStage must be objects"
        )
    scope_event_count = _validate_counter(summary.get("eventCount"), "summary.eventCount")
    canonical_event_kind_counts = _validate_event_kind_counts(
        event_kind_counts,
        scope_event_count,
        "summary.eventKindCounts",
    )
    canonical_token_measurement = _validate_token_measurement(
        token_measurement,
        canonical_event_kind_counts[MODEL_CALL],
        "summary.tokenMeasurement",
    )

    canonical_total_limits = _validate_limits(total_limits, "totalLimits")
    canonical_stage_limits: dict[str, dict[str, int]] = {}
    if stage_limits is not None:
        if not isinstance(stage_limits, Mapping):
            raise TelemetryValidationError("stageLimits must be an object")
        for stage, limits in stage_limits.items():
            canonical_stage = _validate_identifier(stage, "stageLimits stage")
            canonical_stage_limits[canonical_stage] = _validate_limits(limits, f"stageLimits.{canonical_stage}")

    violations: list[dict[str, Any]] = []
    unavailable_metrics: list[dict[str, Any]] = []
    checked = 0
    for field, limit in canonical_total_limits.items():
        checked += 1
        collected_event_count = _validate_counter(
            metric_event_counts.get(field, 0),
            f"summary.metricEventCounts.{field}",
        )
        coverage_event_count = _validate_counter(
            metric_coverage_counts.get(field, 0),
            f"summary.metricCoverageCounts.{field}",
        )
        if collected_event_count > coverage_event_count:
            raise TelemetryValidationError(
                f"summary.metricEventCounts.{field} exceeds metricCoverageCounts"
            )
        if coverage_event_count > scope_event_count:
            raise TelemetryValidationError(
                f"summary.metricCoverageCounts.{field} exceeds summary.eventCount"
            )
        if (collected_event_count > 0) != (field in totals):
            raise TelemetryValidationError(
                f"summary.totals.{field} is inconsistent with metricEventCounts"
            )
        if field in TOKEN_FIELDS:
            measurement = canonical_token_measurement[field]
            if (
                canonical_event_kind_counts[LEGACY_UNCLASSIFIED] == 0
                and collected_event_count != measurement["measuredCallCount"]
            ):
                raise TelemetryValidationError(
                    f"summary.metricEventCounts.{field} does not match measured model calls"
                )
            unavailability_reason = _token_unavailability(
                measurement,
                canonical_event_kind_counts,
            )
            if unavailability_reason is not None:
                unavailable = {
                    "scope": "total",
                    "metric": field,
                    "modelCallCount": canonical_event_kind_counts[MODEL_CALL],
                    **measurement,
                    "legacyUnclassifiedEventCount": canonical_event_kind_counts[
                        LEGACY_UNCLASSIFIED
                    ],
                    "unavailabilityReason": unavailability_reason,
                }
                unavailable_metrics.append(unavailable)
                violations.append({**unavailable, "reason": "unavailable", "limit": limit})
                continue
        elif (
            scope_event_count == 0
            or coverage_event_count != scope_event_count
            or collected_event_count == 0
        ):
            if scope_event_count == 0:
                unavailability_reason = "no-events"
            elif collected_event_count == 0 and coverage_event_count == scope_event_count:
                unavailability_reason = "no-measured-events"
            else:
                unavailability_reason = "incomplete-event-coverage"
            unavailable = {
                "scope": "total",
                "metric": field,
                "measuredEventCount": collected_event_count,
                "coverageEventCount": coverage_event_count,
                "scopeEventCount": scope_event_count,
                "unavailabilityReason": unavailability_reason,
            }
            unavailable_metrics.append(unavailable)
            violations.append({**unavailable, "reason": "unavailable", "limit": limit})
            continue
        actual = _validate_counter(totals[field], f"summary.totals.{field}")
        if actual > limit:
            violations.append(
                {
                    "scope": "total",
                    "metric": field,
                    "reason": "limit-exceeded",
                    "actual": actual,
                    "limit": limit,
                }
            )

    for stage in sorted(canonical_stage_limits):
        stage_summary = by_stage.get(stage, {})
        stage_totals = stage_summary.get("totals", {}) if isinstance(stage_summary, Mapping) else {}
        stage_metric_event_counts = (
            stage_summary.get("metricEventCounts", {})
            if isinstance(stage_summary, Mapping)
            else {}
        )
        stage_metric_coverage_counts = (
            stage_summary.get("metricCoverageCounts", {})
            if isinstance(stage_summary, Mapping)
            else {}
        )
        stage_event_kind_counts = (
            stage_summary.get("eventKindCounts", {})
            if isinstance(stage_summary, Mapping)
            else {}
        )
        stage_token_measurement = (
            stage_summary.get("tokenMeasurement", {})
            if isinstance(stage_summary, Mapping)
            else {}
        )
        stage_event_count = (
            _validate_counter(
                stage_summary.get("eventCount"),
                f"summary.byStage.{stage}.eventCount",
            )
            if isinstance(stage_summary, Mapping) and "eventCount" in stage_summary
            else 0
        )
        if (
            not isinstance(stage_totals, Mapping)
            or not isinstance(stage_metric_event_counts, Mapping)
            or not isinstance(stage_metric_coverage_counts, Mapping)
            or not isinstance(stage_event_kind_counts, Mapping)
            or not isinstance(stage_token_measurement, Mapping)
        ):
            raise TelemetryValidationError(
                f"summary.byStage.{stage} metric and measurement fields must be objects"
            )
        if stage_event_count == 0 and not stage_summary:
            stage_event_kind_counts = {kind: 0 for kind in EVENT_KINDS}
            stage_token_measurement = {
                field: {
                    "applicableCallCount": 0,
                    "measuredCallCount": 0,
                    "notApplicableCount": 0,
                    "unmeasuredCallCount": 0,
                }
                for field in TOKEN_FIELDS
            }
        canonical_stage_event_kind_counts = _validate_event_kind_counts(
            stage_event_kind_counts,
            stage_event_count,
            f"summary.byStage.{stage}.eventKindCounts",
        )
        canonical_stage_token_measurement = _validate_token_measurement(
            stage_token_measurement,
            canonical_stage_event_kind_counts[MODEL_CALL],
            f"summary.byStage.{stage}.tokenMeasurement",
        )
        for field, limit in canonical_stage_limits[stage].items():
            checked += 1
            collected_event_count = _validate_counter(
                stage_metric_event_counts.get(field, 0),
                f"summary.byStage.{stage}.metricEventCounts.{field}",
            )
            coverage_event_count = _validate_counter(
                stage_metric_coverage_counts.get(field, 0),
                f"summary.byStage.{stage}.metricCoverageCounts.{field}",
            )
            if collected_event_count > coverage_event_count:
                raise TelemetryValidationError(
                    f"summary.byStage.{stage}.metricEventCounts.{field} exceeds metricCoverageCounts"
                )
            if coverage_event_count > stage_event_count:
                raise TelemetryValidationError(
                    f"summary.byStage.{stage}.metricCoverageCounts.{field} exceeds eventCount"
                )
            if (collected_event_count > 0) != (field in stage_totals):
                raise TelemetryValidationError(
                    f"summary.byStage.{stage}.totals.{field} is inconsistent with metricEventCounts"
                )
            if field in TOKEN_FIELDS:
                measurement = canonical_stage_token_measurement[field]
                if (
                    canonical_stage_event_kind_counts[LEGACY_UNCLASSIFIED] == 0
                    and collected_event_count != measurement["measuredCallCount"]
                ):
                    raise TelemetryValidationError(
                        f"summary.byStage.{stage}.metricEventCounts.{field} does not match measured model calls"
                    )
                unavailability_reason = _token_unavailability(
                    measurement,
                    canonical_stage_event_kind_counts,
                )
                if unavailability_reason is not None:
                    unavailable = {
                        "scope": "stage",
                        "stage": stage,
                        "metric": field,
                        "modelCallCount": canonical_stage_event_kind_counts[MODEL_CALL],
                        **measurement,
                        "legacyUnclassifiedEventCount": canonical_stage_event_kind_counts[
                            LEGACY_UNCLASSIFIED
                        ],
                        "unavailabilityReason": unavailability_reason,
                    }
                    unavailable_metrics.append(unavailable)
                    violations.append(
                        {**unavailable, "reason": "unavailable", "limit": limit}
                    )
                    continue
            elif (
                stage_event_count == 0
                or coverage_event_count != stage_event_count
                or collected_event_count == 0
            ):
                if stage_event_count == 0:
                    unavailability_reason = "no-events"
                elif collected_event_count == 0 and coverage_event_count == stage_event_count:
                    unavailability_reason = "no-measured-events"
                else:
                    unavailability_reason = "incomplete-event-coverage"
                unavailable = {
                    "scope": "stage",
                    "stage": stage,
                    "metric": field,
                    "measuredEventCount": collected_event_count,
                    "coverageEventCount": coverage_event_count,
                    "scopeEventCount": stage_event_count,
                    "unavailabilityReason": unavailability_reason,
                }
                unavailable_metrics.append(unavailable)
                violations.append({**unavailable, "reason": "unavailable", "limit": limit})
                continue
            actual = _validate_counter(
                stage_totals[field],
                f"summary.byStage.{stage}.{field}",
            )
            if actual > limit:
                violations.append(
                    {
                        "scope": "stage",
                        "stage": stage,
                        "metric": field,
                        "reason": "limit-exceeded",
                        "actual": actual,
                        "limit": limit,
                    }
                )

    within_budget = not violations
    return {
        "ok": within_budget,
        "withinBudget": within_budget,
        "checkedLimitCount": checked,
        "unavailableMetrics": unavailable_metrics,
        "violations": violations,
    }


def check_measurement_boundary(
    events: Iterable[Mapping[str, Any]],
    *,
    measurement_boundary_id: str,
    run_id_digest: str,
    expected_model_calls: Iterable[tuple[str, str, str]],
) -> dict[str, Any]:
    """Match one run's exact expected-call set and complete provider receipts.

    The returned audit data contains counts only.  Neither the run digest nor
    any logical call digest is echoed back to callers.
    """

    boundary_id = _validate_identifier(
        measurement_boundary_id,
        "measurementBoundaryId",
    )
    canonical_run_digest = _validate_digest(run_id_digest, "runIdDigest")
    expected: set[tuple[str, str, str]] = set()
    for index, item in enumerate(expected_model_calls):
        if not isinstance(item, tuple) or len(item) != 3:
            raise TelemetryValidationError(
                f"expectedModelCalls[{index}] must contain stage, agentRole, and callIdDigest"
            )
        stage = _validate_identifier(item[0], f"expectedModelCalls[{index}].stage")
        agent_role = _validate_identifier(
            item[1],
            f"expectedModelCalls[{index}].agentRole",
        )
        call_digest = _validate_digest(
            item[2],
            f"expectedModelCalls[{index}].callIdDigest",
        )
        identity = (stage, agent_role, call_digest)
        if identity in expected:
            raise TelemetryValidationError("expectedModelCalls must not contain duplicates")
        expected.add(identity)
    if not expected:
        raise TelemetryValidationError("expectedModelCalls must not be empty")

    observed: set[tuple[str, str, str]] = set()
    other_boundary_call_count = 0
    other_run_call_count = 0
    provider_receipt_call_count = 0
    tokenizer_proxy_call_count = 0
    unmeasured_call_count = 0
    unmeasured_applicable_token_count = 0
    legacy_unclassified_event_count = 0
    for raw_event in events:
        event = validate_event(raw_event, allow_legacy_kind=True)
        if event[EVENT_KIND_FIELD] == LEGACY_UNCLASSIFIED:
            legacy_unclassified_event_count += 1
            continue
        if event[EVENT_KIND_FIELD] != MODEL_CALL:
            continue
        if event["measurementBoundaryId"] != boundary_id:
            other_boundary_call_count += 1
            continue
        if event["runIdDigest"] != canonical_run_digest:
            other_run_call_count += 1
            continue
        identity = (event["stage"], event["agentRole"], event["callIdDigest"])
        if identity in observed:
            raise TelemetryValidationError(
                "ledger contains duplicate logical model-call identities"
            )
        observed.add(identity)
        if event["tokenSource"] == PROVIDER_RECEIPT:
            provider_receipt_call_count += 1
        else:
            tokenizer_proxy_call_count += 1
        unmeasured_tokens = set(event.get(UNMEASURED_FIELD, ())) & set(TOKEN_FIELDS)
        if unmeasured_tokens:
            unmeasured_call_count += 1
            unmeasured_applicable_token_count += len(unmeasured_tokens)

    missing_count = len(expected - observed)
    unexpected_count = len(observed - expected)
    complete = (
        missing_count == 0
        and unexpected_count == 0
        and tokenizer_proxy_call_count == 0
        and unmeasured_applicable_token_count == 0
        and legacy_unclassified_event_count == 0
    )
    if legacy_unclassified_event_count:
        status = "ambiguous-legacy-events"
    elif missing_count or unexpected_count:
        status = "call-set-mismatch"
    elif tokenizer_proxy_call_count:
        status = "non-provider-receipt"
    elif unmeasured_applicable_token_count:
        status = "unmeasured-applicable-tokens"
    else:
        status = "complete"
    return {
        "ok": complete,
        "complete": complete,
        "status": status,
        "measurementBoundaryId": boundary_id,
        "expectedCallCount": len(expected),
        "observedCallCount": len(observed),
        "missingCallCount": missing_count,
        "unexpectedCallCount": unexpected_count,
        "otherBoundaryCallCount": other_boundary_call_count,
        "otherRunCallCount": other_run_call_count,
        "providerReceiptCallCount": provider_receipt_call_count,
        "tokenizerProxyCallCount": tokenizer_proxy_call_count,
        "unmeasuredCallCount": unmeasured_call_count,
        "unmeasuredApplicableTokenCount": unmeasured_applicable_token_count,
        "legacyUnclassifiedEventCount": legacy_unclassified_event_count,
    }


def _combine_budget_results(results: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Combine disjoint token/proxy checks while preserving public keys."""

    parts = list(results)
    violations = [item for part in parts for item in part["violations"]]
    unavailable_metrics = [
        item for part in parts for item in part["unavailableMetrics"]
    ]
    within_budget = not violations
    return {
        "ok": within_budget,
        "withinBudget": within_budget,
        "checkedLimitCount": sum(part["checkedLimitCount"] for part in parts),
        "unavailableMetrics": unavailable_metrics,
        "violations": violations,
    }


def check_ledger_budget(
    path: str | os.PathLike[str],
    *,
    total_limits: Mapping[str, int] | None = None,
    stage_limits: Mapping[str, Mapping[str, int]] | None = None,
    measurement_boundary_id: str | None = None,
    run_id_digest: str | None = None,
    expected_model_calls: Iterable[tuple[str, str, str]] | None = None,
) -> dict[str, Any]:
    """Convenience wrapper that summarizes a ledger before checking budgets."""

    ledger = load_ledger(path)
    ledger_summary = summarize_events(ledger["events"])
    canonical_total_limits = _validate_limits(total_limits, "totalLimits")
    canonical_stage_limits = {
        _validate_identifier(stage, "stageLimits stage"): _validate_limits(
            limits,
            f"stageLimits.{stage}",
        )
        for stage, limits in (stage_limits or {}).items()
    }
    has_token_limit = any(field in TOKEN_FIELDS for field in canonical_total_limits) or any(
        field in TOKEN_FIELDS
        for limits in canonical_stage_limits.values()
        for field in limits
    )
    if not has_token_limit:
        return check_budget(
            ledger_summary,
            total_limits=canonical_total_limits,
            stage_limits=canonical_stage_limits,
        )

    if (
        measurement_boundary_id is None
        or run_id_digest is None
        or expected_model_calls is None
    ):
        boundary_check = {
            "ok": False,
            "complete": False,
            "status": "missing-contract",
        }
    else:
        boundary_check = check_measurement_boundary(
            ledger["events"],
            measurement_boundary_id=measurement_boundary_id,
            run_id_digest=run_id_digest,
            expected_model_calls=expected_model_calls,
        )

    if boundary_check["complete"]:
        target_events = [
            event
            for event in ledger["events"]
            if event[EVENT_KIND_FIELD] == MODEL_CALL
            and event["measurementBoundaryId"] == measurement_boundary_id
            and event["runIdDigest"] == run_id_digest
        ]
        token_total_limits = {
            field: limit
            for field, limit in canonical_total_limits.items()
            if field in TOKEN_FIELDS
        }
        proxy_total_limits = {
            field: limit
            for field, limit in canonical_total_limits.items()
            if field not in TOKEN_FIELDS
        }
        token_stage_limits = {
            stage: {
                field: limit
                for field, limit in limits.items()
                if field in TOKEN_FIELDS
            }
            for stage, limits in canonical_stage_limits.items()
        }
        token_stage_limits = {
            stage: limits for stage, limits in token_stage_limits.items() if limits
        }
        proxy_stage_limits = {
            stage: {
                field: limit
                for field, limit in limits.items()
                if field not in TOKEN_FIELDS
            }
            for stage, limits in canonical_stage_limits.items()
        }
        proxy_stage_limits = {
            stage: limits for stage, limits in proxy_stage_limits.items() if limits
        }
        checks = [
            check_budget(
                summarize_events(target_events),
                total_limits=token_total_limits,
                stage_limits=token_stage_limits,
            )
        ]
        if proxy_total_limits or proxy_stage_limits:
            checks.append(
                check_budget(
                    ledger_summary,
                    total_limits=proxy_total_limits,
                    stage_limits=proxy_stage_limits,
                )
            )
        output = _combine_budget_results(checks)
    else:
        # Preserve the existing diagnostic details when the boundary contract
        # itself is incomplete; the boundary violation below remains decisive.
        output = check_budget(
            ledger_summary,
            total_limits=canonical_total_limits,
            stage_limits=canonical_stage_limits,
        )
    output["measurementBoundary"] = boundary_check
    if not boundary_check["complete"]:
        violation = {
            "scope": "measurement-boundary",
            "reason": "measurement-boundary-incomplete",
            "status": boundary_check.get("status", "mismatch"),
            "expectedCallCount": boundary_check.get("expectedCallCount"),
            "observedCallCount": boundary_check.get("observedCallCount"),
            "missingCallCount": boundary_check.get("missingCallCount"),
            "unexpectedCallCount": boundary_check.get("unexpectedCallCount"),
            "otherBoundaryCallCount": boundary_check.get("otherBoundaryCallCount"),
            "otherRunCallCount": boundary_check.get("otherRunCallCount"),
            "providerReceiptCallCount": boundary_check.get("providerReceiptCallCount"),
            "tokenizerProxyCallCount": boundary_check.get("tokenizerProxyCallCount"),
            "unmeasuredCallCount": boundary_check.get("unmeasuredCallCount"),
            "unmeasuredApplicableTokenCount": boundary_check.get(
                "unmeasuredApplicableTokenCount"
            ),
            "legacyUnclassifiedEventCount": boundary_check.get(
                "legacyUnclassifiedEventCount"
            ),
        }
        output["violations"].append(violation)
        output["ok"] = False
        output["withinBudget"] = False
    return output


def _camel_to_kebab(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "-", value).lower()


def _parse_limit(value: str) -> tuple[str, int]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("limit must use METRIC=INTEGER")
    field, raw_limit = value.split("=", 1)
    if field not in METRIC_FIELDS:
        raise argparse.ArgumentTypeError(f"unknown metric: {field}")
    try:
        limit = int(raw_limit, 10)
    except ValueError as error:
        raise argparse.ArgumentTypeError("limit must be an integer") from error
    try:
        return field, _validate_counter(limit, field)
    except TelemetryValidationError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def _parse_stage_limit(value: str) -> tuple[str, str, int]:
    if "=" not in value or "." not in value.split("=", 1)[0]:
        raise argparse.ArgumentTypeError("stage limit must use STAGE.METRIC=INTEGER")
    scope, raw_limit = value.split("=", 1)
    stage, field = scope.rsplit(".", 1)
    try:
        _validate_identifier(stage, "stage")
    except TelemetryValidationError as error:
        raise argparse.ArgumentTypeError(str(error)) from error
    parsed_field, limit = _parse_limit(f"{field}={raw_limit}")
    return stage, parsed_field, limit


def _parse_expected_call(value: str) -> tuple[str, str, str]:
    parts = value.split(":")
    if len(parts) != 3:
        raise argparse.ArgumentTypeError(
            "expected call must use STAGE:AGENT_ROLE:CALL_ID_SHA256"
        )
    stage, agent_role, call_id_digest = parts
    try:
        return (
            _validate_identifier(stage, "expected call stage"),
            _validate_identifier(agent_role, "expected call agent role"),
            _validate_digest(call_id_digest, "expected call digest"),
        )
    except TelemetryValidationError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def _pairs_to_limits(pairs: Sequence[tuple[str, int]], label: str) -> dict[str, int]:
    limits: dict[str, int] = {}
    for field, limit in pairs:
        if field in limits:
            raise TelemetryValidationError(f"duplicate {label}: {field}")
        limits[field] = limit
    return limits


def _stage_pairs_to_limits(pairs: Sequence[tuple[str, str, int]]) -> dict[str, dict[str, int]]:
    limits: dict[str, dict[str, int]] = {}
    for stage, field, limit in pairs:
        stage_bucket = limits.setdefault(stage, {})
        if field in stage_bucket:
            raise TelemetryValidationError(f"duplicate stage limit: {stage}.{field}")
        stage_bucket[field] = limit
    return limits


def _emit(value: Mapping[str, Any], pretty: bool) -> None:
    if pretty:
        print(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))
    else:
        print(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False))


def _safe_error(error: BaseException) -> dict[str, Any]:
    if isinstance(error, TelemetryValidationError):
        return {"ok": False, "error": {"code": "validation", "messages": list(error.messages)}}
    if isinstance(error, TelemetryLockTimeout):
        return {"ok": False, "error": {"code": "lock-timeout", "message": str(error)}}
    if isinstance(error, json.JSONDecodeError):
        return {
            "ok": False,
            "error": {
                "code": "invalid-json",
                "message": f"ledger JSON is invalid at line {error.lineno}, column {error.colno}",
            },
        }
    if isinstance(error, UnicodeError):
        return {
            "ok": False,
            "error": {
                "code": "invalid-encoding",
                "message": "ledger must be valid UTF-8 JSON",
            },
        }
    if isinstance(error, FileNotFoundError):
        return {"ok": False, "error": {"code": "not-found", "message": "ledger does not exist"}}
    if isinstance(error, PermissionError):
        return {"ok": False, "error": {"code": "permission", "message": "ledger cannot be accessed"}}
    return {"ok": False, "error": {"code": "io", "message": "ledger operation failed"}}


def _add_append_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("ledger", type=Path)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--operation", required=True)
    parser.add_argument("--timestamp")
    parser.add_argument("--lock-timeout-seconds", type=float, default=10.0)
    parser.add_argument("--pretty", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    append_parser = subparsers.add_parser(
        "append",
        help="append one local-operation proxy event atomically",
    )
    _add_append_common_arguments(append_parser)
    append_parser.add_argument(
        "--not-applicable",
        action="append",
        choices=METRIC_FIELDS,
        default=[],
        metavar="METRIC",
        help="mark an additional metric not applicable; token metrics are automatic",
    )
    append_parser.add_argument(
        "--unmeasured",
        action="append",
        choices=OPERATION_PROXY_FIELDS,
        default=[],
        metavar="METRIC",
        help="mark an applicable local proxy unavailable; repeat for multiple metrics",
    )
    for field in OPERATION_PROXY_FIELDS:
        append_parser.add_argument(f"--{_camel_to_kebab(field)}", dest=field, type=int)

    model_parser = subparsers.add_parser(
        "append-model-call",
        help="append one provider receipt or tokenizer proxy without raw call data",
    )
    _add_append_common_arguments(model_parser)
    model_parser.add_argument("--provider", required=True)
    model_parser.add_argument("--model", required=True)
    model_parser.add_argument("--agent-role", required=True)
    model_parser.add_argument("--token-source", choices=TOKEN_SOURCES, required=True)
    model_parser.add_argument("--measurement-boundary-id", required=True)
    model_parser.add_argument("--call-id-digest", required=True)
    model_parser.add_argument("--run-id-digest", required=True)
    model_parser.add_argument("--usage-receipt-sha256")
    model_parser.add_argument("--plugin-tree-sha256")
    model_parser.add_argument("--quality-receipt-sha256")
    model_parser.add_argument("--tokenizer-encoding")
    model_parser.add_argument("--tokenizer-version")
    model_parser.add_argument(
        "--not-applicable",
        action="append",
        choices=METRIC_FIELDS,
        default=[],
        metavar="METRIC",
        help="mark a truly inapplicable metric; missing applicable token values become unmeasured",
    )
    model_parser.add_argument(
        "--unmeasured",
        action="append",
        choices=METRIC_FIELDS,
        default=[],
        metavar="METRIC",
        help="explicitly mark an applicable metric unavailable; missing token values are automatic",
    )
    for field in METRIC_FIELDS:
        model_parser.add_argument(f"--{_camel_to_kebab(field)}", dest=field, type=int)

    summary_parser = subparsers.add_parser("summary", help="print totals and per-stage aggregates")
    summary_parser.add_argument("ledger", type=Path)
    summary_parser.add_argument("--pretty", action="store_true")

    budget_parser = subparsers.add_parser("check-budget", help="fail when an aggregate exceeds a limit")
    budget_parser.add_argument("ledger", type=Path)
    budget_parser.add_argument("--limit", action="append", type=_parse_limit, default=[], metavar="METRIC=INTEGER")
    budget_parser.add_argument(
        "--stage-limit",
        action="append",
        type=_parse_stage_limit,
        default=[],
        metavar="STAGE.METRIC=INTEGER",
    )
    budget_parser.add_argument(
        "--measurement-boundary-id",
        help="required with actual-token limits to bind the expected model-call set",
    )
    budget_parser.add_argument(
        "--run-id-digest",
        help="required with actual-token limits to bind receipts to one workflow run",
    )
    budget_parser.add_argument(
        "--expected-call",
        action="append",
        type=_parse_expected_call,
        default=None,
        metavar="STAGE:AGENT_ROLE:CALL_ID_SHA256",
        help="one expected hashed model call; repeat for the complete measurement boundary",
    )
    budget_parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "append":
            metrics = {
                field: getattr(args, field)
                for field in OPERATION_PROXY_FIELDS
                if getattr(args, field) is not None
            }
            event = make_local_operation_event(
                args.request_id,
                args.stage,
                args.operation,
                timestamp=args.timestamp,
                not_applicable_metrics=args.not_applicable or None,
                unmeasured_metrics=args.unmeasured or None,
                **metrics,
            )
            output = append_event(
                args.ledger,
                event,
                lock_timeout_seconds=args.lock_timeout_seconds,
            )
            _emit(output, args.pretty)
            return 0
        if args.command == "append-model-call":
            metrics = {
                field: getattr(args, field)
                for field in METRIC_FIELDS
                if getattr(args, field) is not None
            }
            event = make_model_call_event(
                args.request_id,
                args.stage,
                args.operation,
                provider=args.provider,
                model=args.model,
                agent_role=args.agent_role,
                token_source=args.token_source,
                measurement_boundary_id=args.measurement_boundary_id,
                call_id_digest=args.call_id_digest,
                timestamp=args.timestamp,
                not_applicable_metrics=args.not_applicable or None,
                unmeasured_metrics=args.unmeasured or None,
                run_id_digest=args.run_id_digest,
                usage_receipt_sha256=args.usage_receipt_sha256,
                plugin_tree_sha256=args.plugin_tree_sha256,
                quality_receipt_sha256=args.quality_receipt_sha256,
                tokenizer_encoding=args.tokenizer_encoding,
                tokenizer_version=args.tokenizer_version,
                **metrics,
            )
            output = append_event(
                args.ledger,
                event,
                lock_timeout_seconds=args.lock_timeout_seconds,
            )
            _emit(output, args.pretty)
            return 0
        if args.command == "summary":
            _emit(summarize_ledger(args.ledger), args.pretty)
            return 0
        if args.command == "check-budget":
            output = check_ledger_budget(
                args.ledger,
                total_limits=_pairs_to_limits(args.limit, "total limit"),
                stage_limits=_stage_pairs_to_limits(args.stage_limit),
                measurement_boundary_id=args.measurement_boundary_id,
                run_id_digest=args.run_id_digest,
                expected_model_calls=args.expected_call,
            )
            _emit(output, args.pretty)
            return 0 if output["withinBudget"] else EXIT_BUDGET_EXCEEDED
        raise AssertionError(f"unsupported command: {args.command}")
    except (
        TelemetryValidationError,
        TelemetryLockTimeout,
        json.JSONDecodeError,
        UnicodeError,
        OSError,
    ) as error:
        _emit(_safe_error(error), getattr(args, "pretty", False))
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
