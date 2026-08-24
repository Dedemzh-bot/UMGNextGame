#!/usr/bin/env python3
"""Regression tests for evidence-driven UMG Designer size-mode contracts."""

from __future__ import annotations

import copy
import unittest
from pathlib import Path
from unittest.mock import patch

from _contract_common import ASSETS_ROOT, compute_approved_content_sha256, load_json, sha256_file
from validate_build_bundle import DEFAULT_SCHEMA as BUNDLE_SCHEMA, validate_build_bundle
from validate_requirement_spec import (
    DEFAULT_SCHEMA as REQUIREMENT_SCHEMA,
    required_design_size_modes,
    validate_requirement_spec,
)


EXAMPLE_REQUIREMENT = ASSETS_ROOT / "example-composite-tabs-requirement.json"
EXAMPLE_BUNDLE = ASSETS_ROOT / "example-composite-tabs-build-bundle.json"
CHILD_LAYOUT = ASSETS_ROOT / "example-composite-tabs-child-layout-spec.json"
SCREEN_LAYOUT = ASSETS_ROOT / "example-composite-tabs-screen-layout-spec.json"


def error_codes(validation: dict) -> set[str]:
    return {entry["code"] for entry in validation["errors"]}


def entity_by_id(values: list[dict], entity_id: str) -> dict:
    return next(value for value in values if value.get("id") == entity_id)


def prepare_design_size_claim(requirement: dict) -> dict:
    claim = entity_by_id(requirement["claims"], "claim-asset-decomposition")
    claim["statement"] = (
        "Reviewed Design Size contract: project-umg-rule forces umg assets to FillScreen; "
        "viewport-filling uses FillScreen; content-sized-local uses Desired; verified-reference follows Editor readback; "
        "fallback-unclear explicitly falls back to FillScreen."
    )
    if "evidence-project-resolution" not in claim["evidenceIds"]:
        claim["evidenceIds"].append("evidence-project-resolution")
    return claim


def add_verified_local_widget_evidence(requirement: dict, asset_id: str) -> None:
    evidence_id = "evidence-verified-local-widget"
    if not any(source.get("id") == "source-verified-local-widget" for source in requirement["sources"]):
        requirement["sources"].append(
            {
                "id": "source-verified-local-widget",
                "sourceKey": "source-verified-local-widget",
                "kind": "project-asset",
                "locatorKind": "unreal-object",
                "description": "Designated Widget Blueprint inspected for Designer mode and host behavior.",
                "path": "/Game/UI/UMG/Role/Widgets/uw_role_verified_local",
                "snapshotPath": "snapshots/uw_role_verified_local.json",
                "contentSha256": "a" * 64,
            }
        )
        requirement["evidence"].append(
            {
                "id": evidence_id,
                "sourceId": "source-verified-local-widget",
                "kind": "project-reference",
                "description": "Editor readback confirms a local host and non-zero Desired Size behavior.",
                "measurementMethod": "editor-readback",
            }
        )
    asset = entity_by_id(requirement["assetPlan"], asset_id)
    if evidence_id not in asset["evidenceIds"]:
        asset["evidenceIds"].append(evidence_id)
    claim = prepare_design_size_claim(requirement)
    if evidence_id not in claim["evidenceIds"]:
        claim["evidenceIds"].append(evidence_id)


def root_and_direct_node(layout: dict) -> tuple[dict, dict]:
    root = next(node for node in layout["nodes"] if node.get("parent") is None)
    direct = next(node for node in layout["nodes"] if node.get("parent") == root["id"])
    return root, direct


def add_fixed_root_direct_size_proof(layout: dict) -> None:
    _, direct = root_and_direct_node(layout)
    direct["slotLayout"] = {
        "anchors": {"minimum": [0.0, 0.0], "maximum": [0.0, 0.0]},
        "offsets": {"left": 0.0, "top": 0.0, "right": 307.0, "bottom": 130.0},
        "alignment": [0.0, 0.0],
        "autoSize": False,
    }


class RequirementDesignSizeModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = load_json(REQUIREMENT_SCHEMA)
        cls.example = load_json(EXAMPLE_REQUIREMENT)

    def make_valid_policy_requirement(self) -> dict:
        requirement = copy.deepcopy(self.example)
        requirement["analysisPolicy"] = {
            "geometryEvidenceRequired": False,
            "listPriorityRequired": False,
            "designSizeModeRequired": True,
        }
        prepare_design_size_claim(requirement)
        requirement["assetPlan"][0]["designSizeModeDecision"] = {
            "mode": "Desired",
            "basis": "content-sized-local",
            "reason": "Measured local content supplies a non-zero Desired Size.",
            "evidenceIds": ["evidence-selected-tab"],
            "claimId": "claim-asset-decomposition",
        }
        requirement["assetPlan"][1]["designSizeModeDecision"] = {
            "mode": "FillScreen",
            "basis": "project-umg-rule",
            "reason": "The umg_* project rule fixes the Designer mode without evidence analysis.",
            "evidenceIds": [],
            "claimId": "claim-asset-decomposition",
        }
        requirement["reviewGate"]["approvedContentSha256"] = compute_approved_content_sha256(requirement)
        return requirement

    def test_policy_reads_each_asset_decision_without_kind_mapping(self) -> None:
        requirement = copy.deepcopy(self.example)
        requirement["analysisPolicy"] = {
            "geometryEvidenceRequired": False,
            "listPriorityRequired": False,
            "designSizeModeRequired": True,
        }
        prepare_design_size_claim(requirement)
        requirement["assetPlan"][0]["designSizeModeDecision"] = {
            "mode": "FillScreen",
            "basis": "fallback-unclear",
            "reason": "The available crop does not prove a content-sized host contract, so use the safe fallback.",
            "evidenceIds": [],
            "claimId": "claim-asset-decomposition",
        }
        requirement["assetPlan"][1]["designSizeModeDecision"] = {
            "mode": "FillScreen",
            "basis": "project-umg-rule",
            "reason": "The umg_* project rule fixes FillScreen without analyzing evidence.",
            "evidenceIds": [],
            "claimId": "claim-asset-decomposition",
        }
        requirement["assetPlan"].append(
            {
                **copy.deepcopy(requirement["assetPlan"][0]),
                "id": "asset-entry-navigation-tab",
                "assetPath": "/Game/UI/UMG/Role/Widgets/uw_role_navigation_tab_list",
                "assetKind": "list-entry",
                "layoutSpecPath": "entry-layout.json",
                "buildOrder": 2,
                "designSizeModeDecision": {
                    "mode": "Desired",
                    "basis": "content-sized-local",
                    "reason": "Measured entry content supplies a non-zero local desired size.",
                    "evidenceIds": ["evidence-selected-tab"],
                    "claimId": "claim-asset-decomposition",
                },
            }
        )
        claim = entity_by_id(requirement["claims"], "claim-asset-decomposition")
        claim["subjectRefs"].append("asset-entry-navigation-tab")

        self.assertEqual(
            required_design_size_modes(requirement),
            {
                "asset-child-navigation-tab": "FillScreen",
                "asset-screen-role": "FillScreen",
                "asset-entry-navigation-tab": "Desired",
            },
        )

    def test_policy_requires_decision_for_every_in_scope_asset(self) -> None:
        requirement = copy.deepcopy(self.example)
        requirement["analysisPolicy"] = {
            "geometryEvidenceRequired": False,
            "listPriorityRequired": False,
            "designSizeModeRequired": True,
        }
        prepare_design_size_claim(requirement)
        validation = validate_requirement_spec(requirement, self.schema)
        self.assertIn("asset.design_size_mode.required", error_codes(validation))

    def test_policy_accepts_claim_bound_decisions(self) -> None:
        validation = validate_requirement_spec(self.make_valid_policy_requirement(), self.schema)
        self.assertTrue(validation["valid"], validation)

    def test_umg_name_forces_project_rule_fill_without_decision_evidence(self) -> None:
        requirement = self.make_valid_policy_requirement()
        screen_decision = requirement["assetPlan"][1]["designSizeModeDecision"]
        self.assertEqual(screen_decision["basis"], "project-umg-rule")
        self.assertEqual(screen_decision["mode"], "FillScreen")
        self.assertEqual(screen_decision["evidenceIds"], [])

        screen_decision.update(
            {
                "basis": "viewport-filling",
                "evidenceIds": ["evidence-project-resolution"],
            }
        )
        validation = validate_requirement_spec(requirement, self.schema)
        self.assertIn("asset.design_size_mode.umg_rule", error_codes(validation))

    def test_umg_project_rule_rejects_decision_evidence(self) -> None:
        requirement = self.make_valid_policy_requirement()
        requirement["assetPlan"][1]["designSizeModeDecision"]["evidenceIds"] = ["evidence-project-resolution"]
        validation = validate_requirement_spec(requirement, self.schema)
        self.assertIn("asset.design_size_mode.umg_decision_evidence", error_codes(validation))

    def test_umg_project_rule_claim_requires_project_rule_source(self) -> None:
        requirement = self.make_valid_policy_requirement()
        claim = entity_by_id(requirement["claims"], "claim-asset-decomposition")
        claim["evidenceIds"] = ["evidence-selected-tab", "evidence-unselected-tabs"]
        validation = validate_requirement_spec(requirement, self.schema)
        self.assertIn("asset.design_size_mode.umg_rule_evidence", error_codes(validation))

    def test_uw_name_forbids_project_umg_rule(self) -> None:
        requirement = self.make_valid_policy_requirement()
        requirement["assetPlan"][0]["designSizeModeDecision"] = {
            "mode": "FillScreen",
            "basis": "project-umg-rule",
            "reason": "The umg-only rule cannot be reused for a uw asset.",
            "evidenceIds": [],
            "claimId": "claim-asset-decomposition",
        }
        validation = validate_requirement_spec(requirement, self.schema)
        self.assertIn("asset.design_size_mode.uw_analysis_required", error_codes(validation))

    def test_nonstandard_legacy_name_requires_fill_screen_fallback(self) -> None:
        requirement = self.make_valid_policy_requirement()
        child = requirement["assetPlan"][0]
        old_path = child["assetPath"]
        child["assetPath"] = "/Game/UI/UMG/Role/Widgets/legacy_navigation_tab"
        requirement["target"]["targetAssetPaths"] = [
            child["assetPath"] if path == old_path else path
            for path in requirement["target"]["targetAssetPaths"]
        ]
        child["designSizeModeDecision"] = {
            "mode": "FillScreen",
            "basis": "fallback-unclear",
            "reason": "A nonstandard legacy name uses the conservative fallback.",
            "evidenceIds": [],
            "claimId": "claim-asset-decomposition",
        }
        requirement["reviewGate"]["approvedContentSha256"] = compute_approved_content_sha256(requirement)
        validation = validate_requirement_spec(requirement, self.schema)
        self.assertTrue(validation["valid"], validation)

        child["designSizeModeDecision"] = {
            "mode": "Desired",
            "basis": "content-sized-local",
            "reason": "Legacy names cannot opt into Desired analysis.",
            "evidenceIds": ["evidence-selected-tab"],
            "claimId": "claim-asset-decomposition",
        }
        validation = validate_requirement_spec(requirement, self.schema)
        self.assertIn("asset.design_size_mode.legacy_name_fallback", error_codes(validation))

    def test_decision_requires_single_claim_id(self) -> None:
        requirement = self.make_valid_policy_requirement()
        requirement["assetPlan"][0]["designSizeModeDecision"].pop("claimId")
        validation = validate_requirement_spec(requirement, self.schema)
        self.assertFalse(validation["valid"])
        self.assertIn("ref.claim", error_codes(validation))

    def test_decision_claim_must_exist_belong_to_asset_and_have_asset_subject(self) -> None:
        requirement = self.make_valid_policy_requirement()
        requirement["assetPlan"][0]["designSizeModeDecision"]["claimId"] = "claim-does-not-exist"
        self.assertIn("ref.claim", error_codes(validate_requirement_spec(requirement, self.schema)))

        requirement = self.make_valid_policy_requirement()
        requirement["assetPlan"][0]["claimIds"].remove("claim-asset-decomposition")
        self.assertIn(
            "asset.design_size_mode.claim_scope",
            error_codes(validate_requirement_spec(requirement, self.schema)),
        )

        requirement = self.make_valid_policy_requirement()
        claim = entity_by_id(requirement["claims"], "claim-asset-decomposition")
        claim["subjectRefs"].remove("asset-child-navigation-tab")
        self.assertIn(
            "asset.design_size_mode.claim_subject",
            error_codes(validate_requirement_spec(requirement, self.schema)),
        )

    def test_decision_claim_must_be_asset_decomposition_and_cover_evidence(self) -> None:
        requirement = self.make_valid_policy_requirement()
        claim = entity_by_id(requirement["claims"], "claim-asset-decomposition")
        claim["type"] = "responsive-behavior"
        self.assertIn(
            "asset.design_size_mode.claim_type",
            error_codes(validate_requirement_spec(requirement, self.schema)),
        )

        requirement = self.make_valid_policy_requirement()
        claim = entity_by_id(requirement["claims"], "claim-asset-decomposition")
        claim["evidenceIds"].remove("evidence-selected-tab")
        self.assertIn(
            "asset.design_size_mode.claim_evidence",
            error_codes(validate_requirement_spec(requirement, self.schema)),
        )

    def test_fallback_requires_review_claim_to_name_canonical_fallback(self) -> None:
        requirement = self.make_valid_policy_requirement()
        requirement["assetPlan"][0]["designSizeModeDecision"] = {
            "mode": "FillScreen",
            "basis": "fallback-unclear",
            "reason": "The host behavior is ambiguous.",
            "evidenceIds": [],
            "claimId": "claim-asset-decomposition",
        }
        claim = entity_by_id(requirement["claims"], "claim-asset-decomposition")
        claim["statement"] = "The host behavior remains ambiguous."
        self.assertIn(
            "asset.design_size_mode.fallback_claim",
            error_codes(validate_requirement_spec(requirement, self.schema)),
        )

    def test_verified_reference_rejects_project_rule_or_image_evidence(self) -> None:
        requirement = self.make_valid_policy_requirement()
        requirement["assetPlan"][0]["designSizeModeDecision"] = {
            "mode": "Desired",
            "basis": "verified-reference",
            "reason": "An ordinary project rule is incorrectly presented as Widget readback.",
            "evidenceIds": ["evidence-project-resolution"],
            "claimId": "claim-asset-decomposition",
        }
        self.assertIn(
            "asset.design_size_mode.verified_reference_evidence",
            error_codes(validate_requirement_spec(requirement, self.schema)),
        )

    def test_verified_reference_accepts_project_asset_editor_readback(self) -> None:
        requirement = self.make_valid_policy_requirement()
        add_verified_local_widget_evidence(requirement, "asset-child-navigation-tab")
        requirement["assetPlan"][0]["designSizeModeDecision"] = {
            "mode": "Desired",
            "basis": "verified-reference",
            "reason": "The designated Widget readback proves local Desired-size hosting.",
            "evidenceIds": ["evidence-verified-local-widget"],
            "claimId": "claim-asset-decomposition",
        }
        requirement["reviewGate"]["approvedContentSha256"] = compute_approved_content_sha256(requirement)
        validation = validate_requirement_spec(requirement, self.schema)
        self.assertTrue(validation["valid"], validation)

    def test_verified_reference_rejects_inline_project_asset_source(self) -> None:
        requirement = self.make_valid_policy_requirement()
        add_verified_local_widget_evidence(requirement, "asset-child-navigation-tab")
        source = entity_by_id(requirement["sources"], "source-verified-local-widget")
        source["locatorKind"] = "inline"
        requirement["assetPlan"][0]["designSizeModeDecision"] = {
            "mode": "Desired",
            "basis": "verified-reference",
            "reason": "Inline metadata cannot prove an Unreal Widget readback.",
            "evidenceIds": ["evidence-verified-local-widget"],
            "claimId": "claim-asset-decomposition",
        }
        validation = validate_requirement_spec(requirement, self.schema)
        self.assertIn(
            "asset.design_size_mode.verified_reference_evidence",
            error_codes(validation),
        )

    def test_fallback_unclear_must_choose_fill_screen(self) -> None:
        requirement = copy.deepcopy(self.example)
        requirement["analysisPolicy"] = {
            "geometryEvidenceRequired": False,
            "listPriorityRequired": False,
            "designSizeModeRequired": True,
        }
        prepare_design_size_claim(requirement)
        for asset in requirement["assetPlan"]:
            asset["designSizeModeDecision"] = {
                "mode": "FillScreen",
                "basis": "fallback-unclear",
                "reason": "Evidence is insufficient to prove local desired-size behavior.",
                "evidenceIds": [],
                "claimId": "claim-asset-decomposition",
            }
        requirement["assetPlan"][0]["designSizeModeDecision"]["mode"] = "Desired"
        validation = validate_requirement_spec(requirement, self.schema)
        self.assertIn("asset.design_size_mode.basis_mode", error_codes(validation))
        self.assertIn("asset.design_size_mode.desired_evidence", error_codes(validation))

    def test_nonfallback_and_desired_decisions_require_attached_evidence(self) -> None:
        requirement = copy.deepcopy(self.example)
        requirement["analysisPolicy"] = {
            "geometryEvidenceRequired": False,
            "listPriorityRequired": False,
            "designSizeModeRequired": True,
        }
        prepare_design_size_claim(requirement)
        for asset in requirement["assetPlan"]:
            asset["designSizeModeDecision"] = {
                "mode": "FillScreen",
                "basis": "fallback-unclear",
                "reason": "Ambiguous evidence uses the safe fallback.",
                "evidenceIds": [],
                "claimId": "claim-asset-decomposition",
            }
        requirement["assetPlan"][0]["designSizeModeDecision"] = {
            "mode": "Desired",
            "basis": "content-sized-local",
            "reason": "The control is local and content-sized.",
            "evidenceIds": [],
            "claimId": "claim-asset-decomposition",
        }
        validation = validate_requirement_spec(requirement, self.schema)
        self.assertIn("asset.design_size_mode.evidence_required", error_codes(validation))
        self.assertIn("asset.design_size_mode.desired_evidence", error_codes(validation))

        requirement["assetPlan"][0]["designSizeModeDecision"]["evidenceIds"] = ["evidence-project-resolution"]
        validation = validate_requirement_spec(requirement, self.schema)
        self.assertIn("asset.design_size_mode.evidence_scope", error_codes(validation))

    def test_legacy_requirement_without_policy_derives_no_gate(self) -> None:
        self.assertEqual(required_design_size_modes(copy.deepcopy(self.example)), {})

    def test_policy_flag_must_be_boolean(self) -> None:
        requirement = copy.deepcopy(self.example)
        requirement["analysisPolicy"] = {
            "geometryEvidenceRequired": False,
            "listPriorityRequired": False,
            "designSizeModeRequired": "true",
        }
        validation = validate_requirement_spec(requirement, self.schema)
        self.assertFalse(validation["valid"])


class BuildBundleDesignSizeModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle_schema = load_json(BUNDLE_SCHEMA)
        cls.requirement_schema = load_json(REQUIREMENT_SCHEMA)
        cls.example_requirement = load_json(EXAMPLE_REQUIREMENT)
        cls.example_bundle = load_json(EXAMPLE_BUNDLE)
        cls.child_layout = load_json(CHILD_LAYOUT)
        cls.screen_layout = load_json(SCREEN_LAYOUT)

    def make_policy_fixture(self) -> tuple[dict, dict, dict, dict]:
        requirement = copy.deepcopy(self.example_requirement)
        requirement["analysisPolicy"] = {
            "geometryEvidenceRequired": False,
            "listPriorityRequired": False,
            "designSizeModeRequired": True,
        }
        prepare_design_size_claim(requirement)
        requirement["assetPlan"][0]["designSizeModeDecision"] = {
            "mode": "Desired",
            "basis": "content-sized-local",
            "reason": "The measured tab control is locally hosted and has non-zero content size.",
            "evidenceIds": ["evidence-selected-tab"],
            "claimId": "claim-asset-decomposition",
        }
        requirement["assetPlan"][1]["designSizeModeDecision"] = {
            "mode": "FillScreen",
            "basis": "project-umg-rule",
            "reason": "The umg_* project rule fixes FillScreen without evidence analysis.",
            "evidenceIds": [],
            "claimId": "claim-asset-decomposition",
        }
        requirement["reviewGate"]["approvedContentSha256"] = compute_approved_content_sha256(requirement)
        bundle = copy.deepcopy(self.example_bundle)
        bundle["requirement"]["approvedContentSha256"] = requirement["reviewGate"]["approvedContentSha256"]
        child_layout = copy.deepcopy(self.child_layout)
        child_layout["profile"]["designSizeMode"] = "Desired"
        add_fixed_root_direct_size_proof(child_layout)
        screen_layout = copy.deepcopy(self.screen_layout)
        screen_layout["profile"]["designSizeMode"] = "FillScreen"
        return requirement, bundle, child_layout, screen_layout

    def validate(
        self,
        requirement: dict,
        bundle: dict,
        child_layout: dict,
        screen_layout: dict,
        *,
        check_linked_files: bool = True,
    ) -> dict:
        original_load_json = load_json

        def load_linked_json(path: Path) -> dict:
            name = Path(path).name
            if name == CHILD_LAYOUT.name:
                return copy.deepcopy(child_layout)
            if name == SCREEN_LAYOUT.name:
                return copy.deepcopy(screen_layout)
            return original_load_json(path)

        original_sha256_file = sha256_file
        expected_hashes = {
            CHILD_LAYOUT.name: bundle["assets"][0]["layoutSpecSha256"],
            SCREEN_LAYOUT.name: bundle["assets"][1]["layoutSpecSha256"],
            EXAMPLE_REQUIREMENT.name: bundle["requirement"]["sha256"],
        }

        def linked_sha256(path: Path) -> str:
            expected = expected_hashes.get(Path(path).name)
            return expected if isinstance(expected, str) else original_sha256_file(path)

        with (
            patch("validate_build_bundle.load_json", side_effect=load_linked_json),
            patch("validate_build_bundle.sha256_file", side_effect=linked_sha256),
        ):
            return validate_build_bundle(
                bundle,
                self.bundle_schema,
                bundle_path=EXAMPLE_BUNDLE,
                requirement_spec=requirement,
                requirement_path=EXAMPLE_REQUIREMENT,
                requirement_schema=self.requirement_schema,
                check_linked_files=check_linked_files,
            )

    def test_legacy_bundle_without_policy_remains_valid(self) -> None:
        validation = self.validate(
            copy.deepcopy(self.example_requirement),
            copy.deepcopy(self.example_bundle),
            copy.deepcopy(self.child_layout),
            copy.deepcopy(self.screen_layout),
        )
        self.assertTrue(validation["valid"], validation)

    def test_policy_accepts_fill_screen_and_desired(self) -> None:
        requirement, bundle, child_layout, screen_layout = self.make_policy_fixture()
        validation = self.validate(requirement, bundle, child_layout, screen_layout)
        self.assertTrue(validation["valid"], validation)

    def test_desired_rejects_layout_without_root_direct_size_proof(self) -> None:
        requirement, bundle, child_layout, screen_layout = self.make_policy_fixture()
        _, direct = root_and_direct_node(child_layout)
        direct.pop("slotLayout")
        validation = self.validate(requirement, bundle, child_layout, screen_layout)
        self.assertIn("layout.desired_root_size_proof", error_codes(validation))

    def test_desired_fixed_root_proof_requires_fixed_point_slot_and_positive_size(self) -> None:
        mutations = (
            lambda slot: slot.__setitem__("autoSize", True),
            lambda slot: slot["anchors"].__setitem__("maximum", [1.0, 1.0]),
            lambda slot: slot["offsets"].__setitem__("right", 0.0),
            lambda slot: slot["offsets"].__setitem__("bottom", 0.0),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                requirement, bundle, child_layout, screen_layout = self.make_policy_fixture()
                _, direct = root_and_direct_node(child_layout)
                mutate(direct["slotLayout"])
                validation = self.validate(requirement, bundle, child_layout, screen_layout)
                self.assertIn("layout.desired_root_size_proof", error_codes(validation))

    def test_desired_accepts_evidence_bound_content_driven_root_size(self) -> None:
        requirement, bundle, child_layout, screen_layout = self.make_policy_fixture()
        _, direct = root_and_direct_node(child_layout)
        direct.pop("slotLayout")
        direct["contentDrivenSize"] = {
            "verified": True,
            "measuredDesiredSize": [307.0, 130.0],
            "evidenceId": "evidence-selected-tab",
        }
        validation = self.validate(requirement, bundle, child_layout, screen_layout)
        self.assertTrue(validation["valid"], validation)

    def test_desired_content_driven_proof_requires_positive_verified_size(self) -> None:
        invalid_proofs = (
            {"verified": False, "measuredDesiredSize": [307.0, 130.0], "evidenceId": "evidence-selected-tab"},
            {"verified": True, "measuredDesiredSize": [0.0, 130.0], "evidenceId": "evidence-selected-tab"},
            {"verified": True, "measuredDesiredSize": [307.0, -1.0], "evidenceId": "evidence-selected-tab"},
        )
        for proof in invalid_proofs:
            with self.subTest(proof=proof):
                requirement, bundle, child_layout, screen_layout = self.make_policy_fixture()
                _, direct = root_and_direct_node(child_layout)
                direct.pop("slotLayout")
                direct["contentDrivenSize"] = proof
                validation = self.validate(requirement, bundle, child_layout, screen_layout)
                self.assertIn("layout.desired_root_size_proof", error_codes(validation))

    def test_desired_content_driven_proof_must_bind_decision_evidence(self) -> None:
        requirement, bundle, child_layout, screen_layout = self.make_policy_fixture()
        _, direct = root_and_direct_node(child_layout)
        direct.pop("slotLayout")
        direct["contentDrivenSize"] = {
            "verified": True,
            "measuredDesiredSize": [307.0, 130.0],
            "evidenceId": "evidence-unselected-tabs",
        }
        validation = self.validate(requirement, bundle, child_layout, screen_layout)
        self.assertIn("layout.desired_root_size_proof", error_codes(validation))
        self.assertIn("layout.desired_root_size_evidence", error_codes(validation))

    def test_desired_content_proof_on_root_itself_is_not_root_direct(self) -> None:
        requirement, bundle, child_layout, screen_layout = self.make_policy_fixture()
        root, direct = root_and_direct_node(child_layout)
        direct.pop("slotLayout")
        root["contentDrivenSize"] = {
            "verified": True,
            "measuredDesiredSize": [307.0, 130.0],
            "evidenceId": "evidence-selected-tab",
        }
        validation = self.validate(requirement, bundle, child_layout, screen_layout)
        self.assertIn("layout.desired_root_size_proof", error_codes(validation))

    def test_policy_rejects_missing_design_size_mode(self) -> None:
        requirement, bundle, child_layout, screen_layout = self.make_policy_fixture()
        child_layout["profile"].pop("designSizeMode")
        self.assertIn(
            "layout.design_size_mode",
            error_codes(self.validate(requirement, bundle, child_layout, screen_layout)),
        )

    def test_policy_rejects_desired_on_screen_variant(self) -> None:
        requirement, bundle, child_layout, screen_layout = self.make_policy_fixture()
        child_layout["profile"]["designSizeMode"] = "DesiredOnScreen"
        self.assertIn(
            "layout.design_size_mode",
            error_codes(self.validate(requirement, bundle, child_layout, screen_layout)),
        )

    def test_policy_rejects_layout_mode_that_disagrees_with_decision(self) -> None:
        requirement, bundle, child_layout, screen_layout = self.make_policy_fixture()
        screen_layout["profile"]["designSizeMode"] = "Desired"
        codes = error_codes(self.validate(requirement, bundle, child_layout, screen_layout))
        self.assertIn("layout.design_size_mode", codes)
        self.assertIn("layout.umg_design_size_mode", codes)

    def test_uw_analysis_can_choose_fill_without_asset_kind_mapping(self) -> None:
        requirement, bundle, child_layout, screen_layout = self.make_policy_fixture()
        requirement["assetPlan"][0]["designSizeModeDecision"] = {
            "mode": "FillScreen",
            "basis": "fallback-unclear",
            "reason": "The crop does not establish a local Desired-size host contract.",
            "evidenceIds": [],
            "claimId": "claim-asset-decomposition",
        }
        requirement["reviewGate"]["approvedContentSha256"] = compute_approved_content_sha256(requirement)
        bundle["requirement"]["approvedContentSha256"] = requirement["reviewGate"]["approvedContentSha256"]
        child_layout["profile"]["designSizeMode"] = "FillScreen"
        validation = self.validate(requirement, bundle, child_layout, screen_layout)
        self.assertTrue(validation["valid"], validation)

    def test_child_widget_profile_must_not_be_entry(self) -> None:
        requirement, bundle, child_layout, screen_layout = self.make_policy_fixture()
        child_layout["profile"]["listRole"] = "entry"
        self.assertIn(
            "layout.list_role",
            error_codes(self.validate(requirement, bundle, child_layout, screen_layout)),
        )

    def test_child_widget_requires_child_widget_profile(self) -> None:
        requirement, bundle, child_layout, screen_layout = self.make_policy_fixture()
        child_layout["profile"]["assetKind"] = "screen"
        self.assertIn(
            "layout.asset_kind",
            error_codes(self.validate(requirement, bundle, child_layout, screen_layout)),
        )

    def test_screen_requires_screen_profile(self) -> None:
        requirement, bundle, child_layout, screen_layout = self.make_policy_fixture()
        screen_layout["profile"]["assetKind"] = "child-widget"
        self.assertIn(
            "layout.asset_kind",
            error_codes(self.validate(requirement, bundle, child_layout, screen_layout)),
        )

    def test_list_entry_requires_child_profile_and_entry_role(self) -> None:
        requirement, bundle, child_layout, screen_layout = self.make_policy_fixture()
        requirement["assetPlan"][0]["assetKind"] = "list-entry"
        requirement["reviewGate"]["approvedContentSha256"] = compute_approved_content_sha256(requirement)
        bundle["assets"][0]["assetKind"] = "list-entry"
        bundle["requirement"]["approvedContentSha256"] = requirement["reviewGate"]["approvedContentSha256"]
        child_layout["profile"].pop("listRole", None)
        validation = self.validate(requirement, bundle, child_layout, screen_layout)
        self.assertIn("layout.list_role", error_codes(validation))

    def test_policy_gate_runs_only_with_linked_file_validation(self) -> None:
        requirement, bundle, child_layout, screen_layout = self.make_policy_fixture()
        child_layout["profile"].pop("designSizeMode")
        validation = self.validate(
            requirement,
            bundle,
            child_layout,
            screen_layout,
            check_linked_files=False,
        )
        self.assertNotIn("layout.design_size_mode", error_codes(validation))


if __name__ == "__main__":
    unittest.main()
