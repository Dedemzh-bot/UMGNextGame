#!/usr/bin/env python3
"""Regression tests for deterministic, dependency-closed Requirement Review Views."""

from __future__ import annotations

import copy
import json
import shutil
import sys
import unittest
import uuid
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _contract_common import ASSETS_ROOT, canonical_sha256, load_json
from review_view import (
    DEFAULT_REQUIREMENT_SCHEMA,
    ROLE_TO_PROFILE,
    ReviewViewError,
    build_canonical_index,
    build_review_view,
    collect_references,
    validate_review_view,
)


class ReviewViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base_draft = load_json(ASSETS_ROOT / "example-composite-tabs-requirement.json")
        cls.base_request = load_json(ASSETS_ROOT / "example-composite-tabs-request-packet.json")

    def setUp(self) -> None:
        self.draft = copy.deepcopy(self.base_draft)
        self.request = copy.deepcopy(self.base_request)
        self.draft["reviewGate"] = {
            "required": True,
            "status": "pending",
            "acceptedClaimIds": [],
            "rejectedClaimIds": [],
        }
        temp_parent = Path.cwd() / "Saved" / "CodexUITestTemp"
        temp_parent.mkdir(parents=True, exist_ok=True)
        self.temp_root = temp_parent / f"review-view-{uuid.uuid4().hex}"
        self.temp_root.mkdir()
        self.draft_hash = "a" * 64

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_root, ignore_errors=True)

    def build(self, role: str = "coverage-review", draft: dict | None = None, **kwargs):
        return build_review_view(
            draft or self.draft,
            self.request,
            role,
            draft_file_sha256=self.draft_hash,
            **kwargs,
        )

    @staticmethod
    def included_ids(view: dict) -> set[str]:
        return set(build_canonical_index(view["requirement"]).bindings)

    def test_three_roles_emit_matching_v2_profiles_and_validate(self) -> None:
        views = []
        for role, profile in ROLE_TO_PROFILE.items():
            view, mode, reason = self.build(role)
            self.assertEqual(mode, "projected")
            self.assertIsNone(reason)
            self.assertEqual(view["agentRole"], role)
            self.assertEqual(view["profile"], profile)
            self.assertNotIn("path", view["bindings"])
            self.assertEqual(
                view["bindings"]["requirementSchemaId"],
                "https://nextgame.local/schemas/ui-requirement-spec-0.1.json",
            )
            validation = validate_review_view(
                view,
                source_draft=self.draft,
                request=self.request,
                source_draft_file_sha256=self.draft_hash,
            )
            self.assertTrue(validation["valid"], validation)
            views.append(view)
        self.assertEqual(len({item["profile"] for item in views}), 3)

    def test_schema_profile_seeds_every_canonical_owner(self) -> None:
        view, mode, reason = self.build("schema-feasibility-review")
        self.assertEqual((mode, reason), ("projected", None))
        source_ids = set(build_canonical_index(self.draft).bindings)
        self.assertEqual(self.included_ids(view), source_ids)
        self.assertEqual(view["includedCanonicalIdCount"], len(source_ids))
        self.assertEqual(view["omittedCanonicalIdCount"], 0)

    def test_exact_review_metadata_references_seed_the_closure(self) -> None:
        baseline, _, _ = self.build("state-visual-review")
        omitted_claims = [
            item["id"]
            for item in self.draft["claims"]
            if item["id"] not in self.included_ids(baseline)
        ]
        if omitted_claims:
            self.draft["reviewGate"]["acceptedClaimIds"] = [omitted_claims[0]]
        self.draft["reviewResolutions"] = [
            {
                "agentRole": "state-visual-review",
                "findingsRef": "findings/review.json",
                "localId": "local-review-metadata",
                "impact": "low",
                "status": "resolved",
                "resolution": "Retained as exact review metadata.",
                "claimIds": [self.draft["claims"][0]["id"]],
            }
        ]
        view, mode, reason = self.build("state-visual-review")
        self.assertEqual((mode, reason), ("projected", None))
        self.assertEqual(view["requirement"]["reviewGate"], self.draft["reviewGate"])
        self.assertEqual(view["requirement"]["reviewResolutions"], self.draft["reviewResolutions"])
        included = self.included_ids(view)
        self.assertTrue(set(self.draft["reviewGate"]["acceptedClaimIds"]) <= included)
        self.assertIn(self.draft["claims"][0]["id"], included)

    def test_fixed_point_closure_follows_asset_claim_evidence_source_and_cycle(self) -> None:
        assets = self.draft["assetPlan"]
        # The original direction is screen -> child.  Closing the reverse edge
        # forms a cycle and proves that fixed-point traversal terminates.
        assets[0]["dependsOnAssetIds"] = [assets[1]["id"]]
        view, mode, _ = self.build("schema-feasibility-review")
        self.assertEqual(mode, "projected")
        included = self.included_ids(view)
        screen_asset = assets[1]
        claim_id = screen_asset["claimIds"][0]
        claim = next(item for item in self.draft["claims"] if item["id"] == claim_id)
        evidence_id = claim["evidenceIds"][0]
        evidence = next(item for item in self.draft["evidence"] if item["id"] == evidence_id)
        self.assertTrue({assets[0]["id"], assets[1]["id"], claim_id, evidence_id, evidence["sourceId"]} <= included)

    def test_nested_state_ids_are_owned_by_whole_state_model(self) -> None:
        view, mode, _ = self.build("state-visual-review")
        self.assertEqual(mode, "projected")
        model = self.draft["stateModels"][0]
        nested_ids = {
            model["id"],
            model["axes"][0]["id"],
            *(state["id"] for state in model["axes"][0]["states"]),
        }
        self.assertTrue(nested_ids <= self.included_ids(view))
        self.assertEqual(view["requirement"]["stateModels"], [model])
        index = build_canonical_index(self.draft)
        owner_keys = {index.bindings[item].owner_key for item in nested_ids}
        self.assertEqual(len(owner_keys), 1)

    def test_retained_records_and_array_order_are_exact(self) -> None:
        view, mode, _ = self.build("schema-feasibility-review")
        self.assertEqual(mode, "projected")
        projected_assets = view["requirement"]["assetPlan"]
        self.assertEqual(projected_assets, self.draft["assetPlan"])
        self.assertIsNot(projected_assets[0], self.draft["assetPlan"][0])
        self.assertEqual(
            [item["id"] for item in projected_assets],
            [item["id"] for item in self.draft["assetPlan"]],
        )

    def test_entry_widget_class_asset_reference_is_registered_and_closed(self) -> None:
        element = self.draft["uiModel"]["elements"][0]
        target_asset = self.draft["assetPlan"][0]["id"]
        element["properties"]["entryWidgetClassAssetId"] = target_asset
        view, mode, reason = self.build("state-visual-review")
        self.assertEqual((mode, reason), ("projected", None))
        self.assertIn(target_asset, self.included_ids(view))

    def test_image_composition_group_key_is_not_a_reference_and_coverage_keeps_peers(self) -> None:
        first, second = self.draft["uiModel"]["elements"][:2]
        for element in (first, second):
            element["imageComposition"] = {
                "groupKey": "imggrp.test-companion",
                "role": "complete",
                "adaptation": "independent",
            }
        view, mode, reason = self.build("coverage-review")
        self.assertEqual((mode, reason), ("projected", None))
        included = self.included_ids(view)
        self.assertIn(first["id"], included)
        self.assertIn(second["id"], included)
        references, errors = collect_references(self.draft, build_canonical_index(self.draft))
        self.assertFalse(errors)
        self.assertNotIn("imggrp.test-companion", {item.canonical_id for item in references})

    def assert_full_fallback(self, draft: dict, expected_reason: str) -> dict:
        view, mode, reason = self.build(draft=draft)
        self.assertEqual(mode, "full-fallback")
        self.assertEqual(reason, expected_reason)
        self.assertEqual(view["requirement"], draft)
        self.assertIsNot(view["requirement"], draft)
        self.assertEqual(view["bindings"]["viewContentCanonicalSha256"], canonical_sha256(draft))
        validation = validate_review_view(
            view,
            source_draft=draft,
            request=self.request,
            source_draft_file_sha256=self.draft_hash,
        )
        self.assertTrue(validation["valid"], validation)
        return view

    def test_closed_schema_unknown_field_falls_back_exactly(self) -> None:
        draft = copy.deepcopy(self.draft)
        draft["futureReviewMetadata"] = {"enabled": True}
        self.assert_full_fallback(draft, "unknown-field")

    def test_unknown_field_with_one_of_and_outer_closed_schema_falls_back_exactly(self) -> None:
        draft = copy.deepcopy(self.draft)
        draft["uiModel"]["responsiveIntent"][0]["futureRelation"] = {
            "enabled": True
        }
        self.assert_full_fallback(draft, "unknown-field")

    def test_unknown_reference_like_field_in_opaque_properties_falls_back(self) -> None:
        draft = copy.deepcopy(self.draft)
        draft["uiModel"]["elements"][0]["properties"]["futureTargetId"] = draft["uiModel"]["elements"][1]["id"]
        self.assert_full_fallback(draft, "unknown-reference-shape")

    def test_unregistered_opaque_canonical_value_falls_back(self) -> None:
        draft = copy.deepcopy(self.draft)
        draft["uiModel"]["elements"][0]["properties"]["opaqueOwner"] = draft["uiModel"]["elements"][1]["id"]
        self.assert_full_fallback(draft, "unknown-reference-shape")

    def test_unregistered_state_change_reference_value_falls_back(self) -> None:
        draft = copy.deepcopy(self.draft)
        model = draft["stateModels"][0]
        states = model["axes"][0]["states"]
        element_id = draft["uiModel"]["elements"][0]["id"]
        model["implementation"] = {
            "strategy": "shared-tree-properties",
            "axisId": model["axes"][0]["id"],
            "sharedRootElementId": element_id,
            "stateOverrides": [
                {
                    "stateId": states[0]["id"],
                    "changes": [{"elementId": element_id, "property": "Opaque.Target", "value": element_id}],
                },
                {
                    "stateId": states[1]["id"],
                    "changes": [{"elementId": element_id, "property": "Visibility", "value": "Collapsed"}],
                },
            ],
        }
        self.assert_full_fallback(draft, "unknown-reference-shape")

    def test_dangling_duplicate_and_type_mismatch_each_fall_back(self) -> None:
        dangling = copy.deepcopy(self.draft)
        dangling["assetPlan"][0]["dependsOnAssetIds"] = ["asset.does-not-exist"]
        self.assert_full_fallback(dangling, "dangling-reference")

        duplicate = copy.deepcopy(self.draft)
        duplicate["uiModel"]["regions"][1]["id"] = duplicate["uiModel"]["regions"][0]["id"]
        self.assert_full_fallback(duplicate, "duplicate-canonical-id")

        mismatched = copy.deepcopy(self.draft)
        mismatched["uiModel"]["elements"][0]["properties"]["entryWidgetClassAssetId"] = mismatched["uiModel"]["elements"][1]["id"]
        self.assert_full_fallback(mismatched, "reference-type-mismatch")

    def test_unknown_requirement_schema_digest_falls_back(self) -> None:
        schema_copy = self.temp_root / "requirement-schema.json"
        schema_copy.write_bytes(Path(DEFAULT_REQUIREMENT_SCHEMA).read_bytes() + b" ")
        self.assert_full_fallback_with_schema(schema_copy, "unknown-requirement-schema")

    def assert_full_fallback_with_schema(self, schema_path: Path, expected_reason: str) -> None:
        view, mode, reason = self.build(requirement_schema_path=schema_path)
        self.assertEqual((mode, reason), ("full-fallback", expected_reason))
        self.assertEqual(view["requirement"], self.draft)

    def test_identity_pending_and_non_unknown_schema_errors_hard_reject(self) -> None:
        wrong_identity = copy.deepcopy(self.draft)
        wrong_identity["requestId"] = "other-request"
        with self.assertRaisesRegex(ReviewViewError, "requestId"):
            self.build(draft=wrong_identity)

        accepted = copy.deepcopy(self.draft)
        accepted["reviewGate"]["status"] = "accepted"
        with self.assertRaisesRegex(ReviewViewError, "pending"):
            self.build(draft=accepted)

        malformed = copy.deepcopy(self.draft)
        del malformed["target"]
        with self.assertRaises(ReviewViewError) as raised:
            self.build(draft=malformed)
        self.assertEqual(raised.exception.code, "review-view.source_schema")

    def test_validator_rejects_tampering_and_wrong_source_hash(self) -> None:
        view, _, _ = self.build()
        tampered = copy.deepcopy(view)
        tampered["requirement"]["request"]["purpose"] = "tampered"
        tampered["bindings"]["viewContentCanonicalSha256"] = canonical_sha256(tampered["requirement"])
        validation = validate_review_view(
            tampered,
            source_draft=self.draft,
            request=self.request,
            source_draft_file_sha256=self.draft_hash,
        )
        self.assertFalse(validation["valid"])
        self.assertIn("review-view.exact_rebuild", {item["code"] for item in validation["errors"]})

        wrong_hash_validation = validate_review_view(
            view,
            source_draft=self.draft,
            request=self.request,
            source_draft_file_sha256="b" * 64,
        )
        self.assertFalse(wrong_hash_validation["valid"])

    def test_build_is_deterministic_and_accepts_profile_alias(self) -> None:
        first = self.build("state-visual-review")[0]
        second = self.build("state-visual-review-v2")[0]
        third = self.build("state-visual-review")[0]
        self.assertEqual(first, second)
        self.assertEqual(first, third)
        self.assertEqual(canonical_sha256(first), canonical_sha256(third))


if __name__ == "__main__":
    unittest.main()
