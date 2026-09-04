#!/usr/bin/env python3
"""Regression tests for deterministic, full-Requirement-bound Accepted Build Views."""

from __future__ import annotations

import copy
import io
import json
import shutil
import sys
import unittest
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import accepted_build_view as accepted_view_module
from _contract_common import (
    ASSETS_ROOT,
    canonical_sha256,
    compute_approved_content_sha256,
    load_json,
    sha256_file,
    validate_schema_instance,
)
from accepted_build_view import (
    AcceptedBuildViewError,
    BUILD_PLANNING_DISPATCH_CONTRACT,
    DEFAULT_REQUIREMENT_SCHEMA,
    RequirementSnapshot,
    _validate_cli,
    _write_json,
    build_accepted_build_view,
    compute_view_content_canonical_sha256,
    validate_accepted_build_view,
)
from review_view import SUPPORTED_REQUIREMENT_SCHEMA_SHA256, build_canonical_index, collect_references
from validate_requirement_spec import validate_requirement_spec


class AcceptedBuildViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base_requirement = load_json(ASSETS_ROOT / "example-composite-tabs-requirement.json")
        cls.requirement_schema = load_json(DEFAULT_REQUIREMENT_SCHEMA)

    def setUp(self) -> None:
        self.requirement = copy.deepcopy(self.base_requirement)
        temp_parent = Path.cwd() / "Saved" / "CodexUITestTemp"
        temp_parent.mkdir(parents=True, exist_ok=True)
        self.temp_root = temp_parent / f"accepted-build-view-{uuid.uuid4().hex}"
        self.temp_root.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_root, ignore_errors=True)

    def approve(self, requirement: dict | None = None) -> dict:
        target = requirement or self.requirement
        target["reviewGate"]["approvedContentSha256"] = compute_approved_content_sha256(target)
        return target

    def requirement_bytes(self, requirement: dict | None = None, *, indent: int | None = None) -> bytes:
        target = requirement or self.requirement
        return (
            json.dumps(
                target,
                ensure_ascii=False,
                indent=indent,
                separators=None if indent is not None else (",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")

    def build(self, requirement: dict | None = None, **kwargs):
        return build_accepted_build_view(
            self.requirement_bytes(requirement),
            **kwargs,
        )

    @staticmethod
    def projected_ids(view: dict) -> set[str]:
        return set(build_canonical_index(view["requirement"]).bindings)

    def test_projected_view_is_deterministic_bound_and_not_a_requirement_spec(self) -> None:
        first, mode, reason = self.build()
        second, second_mode, second_reason = self.build()
        self.assertEqual((mode, reason), ("projected", None))
        self.assertEqual((second_mode, second_reason), (mode, reason))
        self.assertEqual(first, second)
        self.assertTrue(first["buildAllowed"])
        self.assertEqual(
            BUILD_PLANNING_DISPATCH_CONTRACT, first["dispatchContract"]
        )
        self.assertEqual(first["bindings"]["requirementCanonicalSha256"], canonical_sha256(self.requirement))
        self.assertEqual(
            first["bindings"]["approvedContentSha256"],
            self.requirement["reviewGate"]["approvedContentSha256"],
        )
        self.assertEqual(
            first["bindings"]["viewContentCanonicalSha256"],
            compute_view_content_canonical_sha256(first),
        )
        self.assertNotIn("reviewGate", first["requirement"])
        self.assertEqual(first["requirement"]["request"], self.requirement["request"])
        self.assertNotIn("reviewResolutions", first["requirement"])
        self.assertNotIn("normalization", first["requirement"])
        partial_validation = validate_requirement_spec(first["requirement"], self.requirement_schema)
        self.assertFalse(partial_validation["valid"])
        validation = validate_accepted_build_view(
            first,
            source_requirement=self.requirement_bytes(),
        )
        self.assertTrue(validation["valid"], validation)
        self.assertTrue(validation["buildAllowed"])

    def test_build_planning_dispatch_contract_is_required_and_exact(self) -> None:
        view, _, _ = self.build()
        tampered = copy.deepcopy(view)
        tampered["dispatchContract"]["forbiddenActions"].remove(
            "connect-unreal-editor"
        )
        tampered["bindings"][
            "viewContentCanonicalSha256"
        ] = compute_view_content_canonical_sha256(tampered)
        validation = validate_accepted_build_view(
            tampered,
            source_requirement=self.requirement_bytes(),
        )
        self.assertFalse(validation["valid"])
        codes = {item["code"] for item in validation["errors"]}
        self.assertIn("accepted-build-view.dispatch_contract", codes)
        self.assertIn("accepted-build-view.exact_rebuild", codes)

    def test_request_exclusions_and_review_resolutions_are_exact_execution_inputs(self) -> None:
        first, _, _ = self.build()
        changed = copy.deepcopy(self.requirement)
        changed["request"]["exclusions"].append("Never construct the newly excluded diagnostic overlay.")
        findings_input = changed["normalization"]["findingsInputs"][6]
        changed["reviewResolutions"] = [
            {
                "agentRole": findings_input["agentRole"],
                "findingsRef": findings_input["findingsRef"],
                "localId": "local-accepted-execution-constraint",
                "impact": "high",
                "status": "resolved",
                "resolution": "Preserve this additional accepted execution constraint.",
                "claimIds": [changed["claims"][0]["id"]],
            }
        ]
        self.approve(changed)
        second, _, _ = self.build(changed)

        self.assertNotEqual(first["requirement"], second["requirement"])
        self.assertEqual(second["requirement"]["request"], changed["request"])
        self.assertEqual(second["requirement"]["reviewResolutions"], changed["reviewResolutions"])

    def test_optional_review_resolutions_preserve_source_presence(self) -> None:
        candidate = copy.deepcopy(self.requirement)
        candidate.pop("reviewResolutions", None)
        self.approve(candidate)
        view, mode, reason = self.build(candidate)
        self.assertEqual((mode, reason), ("projected", None))
        self.assertNotIn("reviewResolutions", view["requirement"])

    def test_review_resolution_references_seed_dependency_closure(self) -> None:
        candidate = copy.deepcopy(self.requirement)
        proposed = copy.deepcopy(candidate["claims"][0])
        proposed["id"] = "cl.review-resolution-dependency"
        proposed["status"] = "proposed"
        proposed["subjectRefs"] = []
        candidate["claims"].append(proposed)
        findings_input = candidate["normalization"]["findingsInputs"][6]
        candidate["reviewResolutions"] = [
            {
                "agentRole": findings_input["agentRole"],
                "findingsRef": findings_input["findingsRef"],
                "localId": "local-review-dependency-closure",
                "impact": "medium",
                "status": "resolved",
                "resolution": "Retain the linked dependency as non-executable context.",
                "claimIds": [proposed["id"]],
            }
        ]
        self.approve(candidate)
        view, mode, reason = self.build(candidate)
        self.assertEqual((mode, reason), ("projected", None))
        projected_claim_ids = [claim["id"] for claim in view["requirement"]["claims"]]
        self.assertIn(proposed["id"], projected_claim_ids)

    def test_projection_preserves_source_array_order_and_exact_values(self) -> None:
        view, _, _ = self.build()
        projected = view["requirement"]
        paths = (
            ("sources",),
            ("evidence",),
            ("claims",),
            ("uiModel", "regions"),
            ("uiModel", "componentFamilies"),
            ("uiModel", "elements"),
            ("uiModel", "collections"),
            ("uiModel", "runtimeFields"),
            ("uiModel", "responsiveIntent"),
            ("stateModels",),
            ("assetPlan",),
            ("assumptions",),
            ("questions",),
            ("acceptanceCriteria",),
        )
        for path in paths:
            source: object = self.requirement
            selected: object = projected
            for token in path:
                source = source[token]  # type: ignore[index]
                selected = selected[token]  # type: ignore[index]
            source_by_id = {item["id"]: item for item in source}  # type: ignore[union-attr]
            selected_ids = [item["id"] for item in selected]  # type: ignore[union-attr]
            expected_ids = [item["id"] for item in source if item["id"] in selected_ids]  # type: ignore[union-attr]
            self.assertEqual(selected_ids, expected_ids, path)
            self.assertEqual(list(selected), [source_by_id[item_id] for item_id in selected_ids], path)  # type: ignore[arg-type]

    def test_every_accepted_claim_is_projected_in_source_order(self) -> None:
        view, _, _ = self.build()
        expected = [item["id"] for item in self.requirement["claims"] if item["status"] == "accepted"]
        self.assertEqual(view["coverage"]["acceptedClaims"]["buildProjected"], expected)
        self.assertEqual(view["coverage"]["acceptedClaims"]["explicitNonBuild"], [])
        self.assertTrue(set(expected) <= self.projected_ids(view))

    def test_accepted_assumptions_and_answered_questions_are_roots_only(self) -> None:
        accepted_assumption = self.requirement["assumptions"][0]
        accepted_assumption["status"] = "accepted"
        proposed_assumption = copy.deepcopy(accepted_assumption)
        proposed_assumption["id"] = "assumption.future-review-only"
        proposed_assumption["status"] = "proposed"
        self.requirement["assumptions"].append(proposed_assumption)

        answered_question = self.requirement["questions"][0]
        answered_question["status"] = "answered"
        answered_question["answer"] = "Use the accepted default-state contract."
        open_question = copy.deepcopy(answered_question)
        open_question["id"] = "question.future-review-only"
        open_question["status"] = "open"
        open_question.pop("answer", None)
        self.requirement["questions"].append(open_question)
        self.approve()

        view, mode, reason = self.build()
        self.assertEqual((mode, reason), ("projected", None))
        self.assertEqual(view["requirement"]["assumptions"], [accepted_assumption])
        self.assertEqual(view["requirement"]["questions"], [answered_question])
        included = self.projected_ids(view)
        self.assertIn(accepted_assumption["id"], included)
        self.assertIn(answered_question["id"], included)
        self.assertNotIn(proposed_assumption["id"], included)
        self.assertNotIn(open_question["id"], included)

    def test_scope_ledger_partitions_elements_states_and_criteria_exactly(self) -> None:
        out_element = self.requirement["uiModel"]["elements"][4]
        out_element["inBuildScope"] = False
        out_element["scopedOutReason"] = "Not built in this accepted iteration."
        out_state = self.requirement["stateModels"][0]["axes"][0]["states"][0]
        out_state["inBuildScope"] = False
        out_state["scopedOutReason"] = "Reference-only sibling state."
        out_criterion = self.requirement["acceptanceCriteria"][1]
        out_criterion["inBuildScope"] = False
        out_criterion["scopedOutReason"] = "Audit-only acceptance statement."
        self.approve()

        view, mode, reason = self.build()
        self.assertEqual((mode, reason), ("projected", None))
        coverage = view["coverage"]
        self.assertNotIn(out_element["id"], coverage["elements"]["buildProjected"])
        self.assertEqual(
            coverage["elements"]["explicitNonBuild"],
            [{"canonicalId": out_element["id"], "reason": out_element["scopedOutReason"]}],
        )
        self.assertNotIn(out_state["id"], coverage["states"]["buildProjected"])
        self.assertEqual(
            coverage["states"]["explicitNonBuild"],
            [{"canonicalId": out_state["id"], "reason": out_state["scopedOutReason"]}],
        )
        self.assertNotIn(out_criterion["id"], coverage["acceptanceCriteria"]["buildProjected"])
        self.assertEqual(
            coverage["acceptanceCriteria"]["explicitNonBuild"],
            [{"canonicalId": out_criterion["id"], "reason": out_criterion["scopedOutReason"]}],
        )

    def test_closed_schema_rejects_impossible_mode_and_build_flag_combination(self) -> None:
        view, _, _ = self.build()
        impossible = copy.deepcopy(view)
        impossible["mode"] = "full-fallback"
        impossible["buildAllowed"] = True
        impossible["fallbackReason"] = "closure-incomplete"
        impossible["coverage"]["status"] = "indeterminate"
        impossible["bindings"]["viewContentCanonicalSha256"] = compute_view_content_canonical_sha256(impossible)
        schema_errors = validate_schema_instance(impossible, load_json(accepted_view_module.DEFAULT_VIEW_SCHEMA))
        self.assertTrue(schema_errors)

    def test_mixed_scope_state_copies_whole_model_but_ledger_blocks_sibling(self) -> None:
        states = self.requirement["stateModels"][0]["axes"][0]["states"]
        states[0]["inBuildScope"] = False
        states[0]["scopedOutReason"] = "Reference-only sibling state."
        self.approve()
        view, _, _ = self.build()
        self.assertEqual(view["requirement"]["stateModels"], self.requirement["stateModels"])
        self.assertIn(states[1]["id"], view["coverage"]["states"]["buildProjected"])
        self.assertEqual(
            view["coverage"]["states"]["explicitNonBuild"],
            [{"canonicalId": states[0]["id"], "reason": states[0]["scopedOutReason"]}],
        )

    def test_fixed_point_closes_claim_evidence_source_and_every_projected_reference(self) -> None:
        view, _, _ = self.build()
        accepted_claim = next(item for item in self.requirement["claims"] if item["status"] == "accepted")
        evidence = next(item for item in self.requirement["evidence"] if item["id"] == accepted_claim["evidenceIds"][0])
        included = self.projected_ids(view)
        self.assertTrue({accepted_claim["id"], evidence["id"], evidence["sourceId"]} <= included)
        projected_index = build_canonical_index(view["requirement"])
        references, unknown = collect_references(view["requirement"], projected_index)
        self.assertEqual(unknown, [])
        self.assertTrue(all(reference.canonical_id in projected_index.bindings for reference in references))

    def test_unknown_open_field_reference_falls_back_exactly_and_blocks(self) -> None:
        first = self.requirement["uiModel"]["elements"][0]
        second = self.requirement["uiModel"]["elements"][1]
        first["properties"]["futureTargetId"] = second["id"]
        self.approve()
        view, mode, reason = self.build()
        self.assertEqual((mode, reason), ("full-fallback", "unknown-reference-shape"))
        self.assertFalse(view["buildAllowed"])
        self.assertEqual(view["requirement"], self.requirement)
        self.assertIsNot(view["requirement"], self.requirement)
        validation = validate_accepted_build_view(
            view,
            source_requirement=self.requirement_bytes(),
        )
        self.assertTrue(validation["valid"], validation)
        self.assertFalse(validation["buildAllowed"])

    def test_unknown_requirement_schema_bytes_fall_back_exactly_and_block(self) -> None:
        schema_copy = self.temp_root / "requirement-schema.json"
        schema_copy.write_bytes(Path(DEFAULT_REQUIREMENT_SCHEMA).read_bytes() + b" ")
        view, mode, reason = self.build(requirement_schema_path=schema_copy)
        self.assertEqual((mode, reason), ("full-fallback", "unknown-requirement-schema"))
        self.assertFalse(view["buildAllowed"])
        self.assertEqual(view["requirement"], self.requirement)
        self.assertEqual(view["bindings"]["requirementSchemaSha256"], sha256_file(schema_copy))

    def test_projection_omission_returns_exact_full_fallback_and_missing_id(self) -> None:
        real_project = accepted_view_module._project_requirement

        def omit_first_build_element(requirement: dict, included_owner_keys: set[str]) -> dict:
            projected = real_project(requirement, included_owner_keys)
            del projected["uiModel"]["elements"][0]
            return projected

        omitted_id = self.requirement["uiModel"]["elements"][0]["id"]
        with patch.object(accepted_view_module, "_project_requirement", omit_first_build_element):
            view, mode, reason = self.build()
        self.assertEqual((mode, reason), ("full-fallback", "projection-coverage-incomplete"))
        self.assertFalse(view["buildAllowed"])
        self.assertEqual(view["requirement"], self.requirement)
        self.assertEqual(view["coverage"]["status"], "incomplete")
        self.assertIn(omitted_id, view["coverage"]["missingCanonicalIds"])

    def test_projected_owner_value_mutation_returns_exact_blocked_fallback(self) -> None:
        real_project = accepted_view_module._project_requirement

        def mutate_retained_claim(requirement: dict, included_owner_keys: set[str]) -> dict:
            projected = real_project(requirement, included_owner_keys)
            projected["claims"][0]["statement"] = "Mutated projection value"
            return projected

        with patch.object(accepted_view_module, "_project_requirement", mutate_retained_claim):
            view, mode, reason = self.build()
        self.assertEqual((mode, reason), ("full-fallback", "closure-incomplete"))
        self.assertFalse(view["buildAllowed"])
        self.assertEqual(view["requirement"], self.requirement)
        self.assertEqual(view["coverage"]["status"], "indeterminate")

    def test_extra_non_closure_proposed_owner_returns_exact_blocked_fallback(self) -> None:
        real_project = accepted_view_module._project_requirement

        def add_proposed_audit_owner(requirement: dict, included_owner_keys: set[str]) -> dict:
            projected = real_project(requirement, included_owner_keys)
            proposed = next(item for item in requirement["assumptions"] if item["status"] == "proposed")
            projected["assumptions"].append(copy.deepcopy(proposed))
            return projected

        with patch.object(accepted_view_module, "_project_requirement", add_proposed_audit_owner):
            view, mode, reason = self.build()
        self.assertEqual((mode, reason), ("full-fallback", "closure-incomplete"))
        self.assertFalse(view["buildAllowed"])
        self.assertEqual(view["requirement"], self.requirement)

    def test_retained_header_mutation_returns_exact_blocked_fallback(self) -> None:
        real_project = accepted_view_module._project_requirement

        def mutate_target_header(requirement: dict, included_owner_keys: set[str]) -> dict:
            projected = real_project(requirement, included_owner_keys)
            projected["target"]["systemName"] = "TamperedHeader"
            return projected

        with patch.object(accepted_view_module, "_project_requirement", mutate_target_header):
            view, mode, reason = self.build()
        self.assertEqual((mode, reason), ("full-fallback", "closure-incomplete"))
        self.assertFalse(view["buildAllowed"])
        self.assertEqual(view["requirement"], self.requirement)

    def test_tamper_rehashed_internally_still_fails_external_exact_rebuild(self) -> None:
        view, _, _ = self.build()
        tampered = copy.deepcopy(view)
        tampered["requirement"]["target"]["systemName"] = "TamperedSystem"
        tampered["bindings"]["viewContentCanonicalSha256"] = compute_view_content_canonical_sha256(tampered)
        validation = validate_accepted_build_view(
            tampered,
            source_requirement=self.requirement_bytes(),
        )
        self.assertFalse(validation["valid"])
        self.assertIn("accepted-build-view.exact_rebuild", {item["code"] for item in validation["errors"]})

    def test_dict_subclass_cannot_override_exact_rebuild_comparison(self) -> None:
        class AlwaysEqualDict(dict):
            def __eq__(self, other):
                return True

            def __ne__(self, other):
                return False

        view, _, _ = self.build()
        tampered = copy.deepcopy(view)
        tampered["requirement"]["target"]["productionAuthorized"] = False
        tampered["bindings"]["viewContentCanonicalSha256"] = compute_view_content_canonical_sha256(tampered)
        validation = validate_accepted_build_view(
            AlwaysEqualDict(tampered),
            source_requirement=self.requirement_bytes(),
        )
        self.assertFalse(validation["valid"])
        self.assertIn("accepted-build-view.exact_rebuild", {item["code"] for item in validation["errors"]})

    def test_ledger_universe_audit_and_external_rebuild_reject_direct_tamper(self) -> None:
        view, _, _ = self.build()
        tampered = copy.deepcopy(view)
        omitted_id = tampered["coverage"]["elements"]["buildProjected"].pop(0)
        projected_index = build_canonical_index(tampered["requirement"])
        missing, audit_errors = accepted_view_module._audit_coverage_ledger(
            self.requirement, projected_index, tampered["coverage"]
        )
        self.assertIn(omitted_id, missing)
        self.assertIn("accepted-build-view.coverage_partition", {item["code"] for item in audit_errors})

        tampered["bindings"]["viewContentCanonicalSha256"] = compute_view_content_canonical_sha256(tampered)
        validation = validate_accepted_build_view(
            tampered,
            source_requirement=self.requirement_bytes(),
        )
        self.assertFalse(validation["valid"])
        self.assertIn("accepted-build-view.exact_rebuild", {item["code"] for item in validation["errors"]})

    def test_physical_sha_binds_format_even_when_canonical_value_matches(self) -> None:
        compact_path = self.temp_root / "compact.json"
        pretty_path = self.temp_root / "pretty.json"
        compact_path.write_text(
            json.dumps(self.requirement, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        pretty_path.write_text(
            json.dumps(self.requirement, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        compact_hash = sha256_file(compact_path)
        pretty_hash = sha256_file(pretty_path)
        self.assertNotEqual(compact_hash, pretty_hash)
        compact_view = build_accepted_build_view(compact_path)[0]
        pretty_view = build_accepted_build_view(pretty_path)[0]
        self.assertEqual(
            compact_view["bindings"]["requirementCanonicalSha256"],
            pretty_view["bindings"]["requirementCanonicalSha256"],
        )
        self.assertNotEqual(compact_view, pretty_view)
        wrong_binding = validate_accepted_build_view(
            compact_view,
            source_requirement=pretty_path,
        )
        self.assertFalse(wrong_binding["valid"])

    def test_memory_value_plus_foreign_hash_is_fail_closed(self) -> None:
        foreign_path = ASSETS_ROOT / "example-composite-tabs-build-bundle.json"
        foreign_hash = sha256_file(foreign_path)
        with self.assertRaises(AcceptedBuildViewError) as raised:
            build_accepted_build_view(
                self.requirement,
                requirement_file_sha256=foreign_hash,
            )
        self.assertEqual(raised.exception.code, "accepted-build-view.unsafe_source_binding")

        valid_view, _, _ = self.build()
        validation = validate_accepted_build_view(
            valid_view,
            source_requirement=self.requirement,
            source_requirement_file_sha256=foreign_hash,
        )
        self.assertFalse(validation["valid"])
        self.assertFalse(validation["buildAllowed"])
        self.assertIn(
            "accepted-build-view.unsafe_source_binding",
            {item["code"] for item in validation["errors"]},
        )

    def test_requirement_snapshot_subclass_cannot_override_bound_value_or_hash(self) -> None:
        requirement = self.requirement
        foreign_hash = sha256_file(ASSETS_ROOT / "example-composite-tabs-build-bundle.json")

        class ForgedSnapshot(RequirementSnapshot):
            def load(self):
                return copy.deepcopy(requirement)

            @property
            def file_sha256(self):
                return foreign_hash

        with self.assertRaises(AcceptedBuildViewError) as raised:
            build_accepted_build_view(ForgedSnapshot(b"{}"))
        self.assertEqual(raised.exception.code, "accepted-build-view.unsafe_source_binding")

    def test_mutated_snapshot_and_bytes_subclass_are_renormalized_before_use(self) -> None:
        requirement_text = self.requirement_bytes().decode("utf-8")

        class MisleadingBytes(bytes):
            def decode(self, *args, **kwargs):
                return requirement_text

        snapshot = RequirementSnapshot(b"{}")
        object.__setattr__(snapshot, "raw_bytes", MisleadingBytes(b"{}"))
        with self.assertRaises(AcceptedBuildViewError) as raised:
            build_accepted_build_view(snapshot)
        self.assertEqual(raised.exception.code, "accepted-build-view.source_requirement")

    def test_schema_snapshot_subclass_cannot_override_schema_bytes_authority(self) -> None:
        class ForgedSchemaSnapshot(RequirementSnapshot):
            pass

        forged = ForgedSchemaSnapshot(Path(DEFAULT_REQUIREMENT_SCHEMA).read_bytes())
        with self.assertRaises(AcceptedBuildViewError) as raised:
            build_accepted_build_view(
                self.requirement_bytes(),
                requirement_schema_path=forged,
            )
        self.assertEqual(raised.exception.code, "accepted-build-view.unsafe_schema_binding")

    def test_mutated_schema_snapshot_hashes_and_parses_the_same_plain_bytes(self) -> None:
        official_schema_text = Path(DEFAULT_REQUIREMENT_SCHEMA).read_text(encoding="utf-8")

        class MisleadingSchemaBytes(bytes):
            def decode(self, *args, **kwargs):
                return official_schema_text

        schema_snapshot = RequirementSnapshot(Path(DEFAULT_REQUIREMENT_SCHEMA).read_bytes())
        object.__setattr__(schema_snapshot, "raw_bytes", MisleadingSchemaBytes(b"{}"))
        view, mode, reason = build_accepted_build_view(
            self.requirement_bytes(),
            requirement_schema_path=schema_snapshot,
        )
        self.assertEqual((mode, reason), ("full-fallback", "unknown-requirement-schema"))
        self.assertFalse(view["buildAllowed"])

    def test_malformed_view_does_not_bypass_full_requirement_authority(self) -> None:
        validation = validate_accepted_build_view(
            {},
            source_requirement=object(),
        )
        codes = {item["code"] for item in validation["errors"]}
        self.assertIn("accepted-build-view.unsafe_source_binding", codes)
        self.assertFalse(validation["valid"])
        self.assertFalse(validation["buildAllowed"])

    def test_pending_and_rejected_requirements_are_refused(self) -> None:
        for status in ("pending", "rejected"):
            candidate = copy.deepcopy(self.requirement)
            candidate["reviewGate"]["status"] = status
            candidate["reviewGate"].pop("approvedContentSha256", None)
            if status == "pending":
                candidate["reviewGate"].pop("reviewedBy", None)
                candidate["reviewGate"].pop("reviewedAt", None)
            with self.assertRaises(AcceptedBuildViewError) as raised:
                self.build(candidate)
            self.assertEqual(raised.exception.code, "accepted-build-view.accepted_required")

    def test_invalid_approval_digest_is_refused_before_projection(self) -> None:
        self.requirement["reviewGate"]["approvedContentSha256"] = "0" * 64
        with self.assertRaises(AcceptedBuildViewError) as raised:
            self.build()
        self.assertEqual(raised.exception.code, "accepted-build-view.source_requirement")
        self.assertIn("review.content_digest", {item["code"] for item in raised.exception.errors})

    def test_compact_writer_is_byte_deterministic(self) -> None:
        view, _, _ = self.build()
        first_path = self.temp_root / "first.json"
        second_path = self.temp_root / "second.json"
        _write_json(first_path, view)
        _write_json(second_path, view)
        expected = (
            json.dumps(view, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
        ).encode("utf-8")
        self.assertEqual(first_path.read_bytes(), second_path.read_bytes())
        self.assertEqual(first_path.read_bytes(), expected)
        self.assertEqual(first_path.stat().st_size, len(expected))

    def test_blocked_validator_cli_returns_nonzero(self) -> None:
        self.requirement["uiModel"]["elements"][0]["properties"]["futureTargetId"] = self.requirement[
            "uiModel"
        ]["elements"][1]["id"]
        self.approve()
        requirement_path = self.temp_root / "requirement.json"
        view_path = self.temp_root / "accepted-build-view.json"
        _write_json(requirement_path, self.requirement)
        view = build_accepted_build_view(
            requirement_path
        )[0]
        _write_json(view_path, view)
        with redirect_stdout(io.StringIO()):
            exit_code = _validate_cli([str(view_path), "--requirement", str(requirement_path)])
        self.assertEqual(exit_code, 2)

    def test_requirement_schema_source_hash_is_unchanged(self) -> None:
        self.assertEqual(sha256_file(Path(DEFAULT_REQUIREMENT_SCHEMA)), SUPPORTED_REQUIREMENT_SCHEMA_SHA256)


if __name__ == "__main__":
    unittest.main()
