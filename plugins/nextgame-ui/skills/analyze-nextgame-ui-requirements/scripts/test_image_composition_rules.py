#!/usr/bin/env python3
"""Regression tests for image composition and element adaptation contracts."""

from __future__ import annotations

import copy
import unittest

from _contract_common import ASSETS_ROOT, compute_approved_content_sha256, load_json
from validate_requirement_spec import DEFAULT_SCHEMA, validate_requirement_spec


EXAMPLE_REQUIREMENT = ASSETS_ROOT / "example-composite-tabs-requirement.json"
SELECTED_BACKGROUND = "element-tab-selected-background"
SELECTED_ACCENT = "element-tab-selected-accent"
UNSELECTED_BACKGROUND = "element-tab-unselected-background"


def error_codes(validation: dict) -> set[str]:
    return {entry["code"] for entry in validation["errors"]}


def warning_codes(validation: dict) -> set[str]:
    return {entry["code"] for entry in validation["warnings"]}


def element(spec: dict, element_id: str) -> dict:
    return next(item for item in spec["uiModel"]["elements"] if item["id"] == element_id)


def claim(spec: dict, claim_id: str) -> dict:
    return next(item for item in spec["claims"] if item["id"] == claim_id)


def seal_review(spec: dict) -> None:
    review = spec["reviewGate"]
    review["acceptedClaimIds"] = [
        item["id"] for item in spec["claims"] if item.get("status") == "accepted"
    ]
    review["rejectedClaimIds"] = [
        item["id"] for item in spec["claims"] if item.get("status") == "rejected"
    ]
    if review.get("status") == "accepted":
        review["approvedContentSha256"] = compute_approved_content_sha256(spec)
    else:
        review.pop("approvedContentSha256", None)


def validate(spec: dict) -> dict:
    seal_review(spec)
    return validate_requirement_spec(spec, load_json(DEFAULT_SCHEMA))


def make_enabled_spec() -> dict:
    spec = load_json(EXAMPLE_REQUIREMENT)
    spec["analysisPolicy"] = {
        "geometryEvidenceRequired": False,
        "listPriorityRequired": False,
        "imageCompositionRequired": True,
    }
    element(spec, SELECTED_BACKGROUND)["imageComposition"] = {
        "groupKey": "tab.selected-art",
        "role": "complete",
        "adaptation": "inherit-owner",
    }
    element(spec, SELECTED_ACCENT)["imageComposition"] = {
        "groupKey": "tab.selected-art",
        "role": "layer",
        "adaptation": "inherit-owner",
        "splitReason": "accepted-exception",
    }
    element(spec, UNSELECTED_BACKGROUND)["imageComposition"] = {
        "groupKey": "tab.unselected-art",
        "role": "complete",
        "adaptation": "inherit-owner",
    }
    return spec


def make_explicit_owner_spec() -> dict:
    spec = make_enabled_spec()
    spec["analysisPolicy"]["explicitImageOwnerIntentRequired"] = True
    for element_id in (SELECTED_BACKGROUND, SELECTED_ACCENT, UNSELECTED_BACKGROUND):
        element(spec, element_id)["imageComposition"]["ownerIntentId"] = "responsive-navigation-left"
    return spec


def remove_responsive_intents(spec: dict) -> None:
    removed_ids = {
        item["id"]
        for item in spec["uiModel"]["responsiveIntent"]
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    spec["uiModel"]["responsiveIntent"] = []
    for supporting_claim in spec["claims"]:
        supporting_claim["subjectRefs"] = [
            subject_id
            for subject_id in supporting_claim.get("subjectRefs", [])
            if subject_id not in removed_ids
        ]


def add_element_intent(
    spec: dict,
    intent_id: str,
    element_id: str,
    *,
    in_build_scope: bool = True,
) -> dict:
    intent = {
        "id": intent_id,
        "elementId": element_id,
        "horizontal": "stretch",
        "vertical": "stretch",
        "reason": "The image or its owner follows the measured region on both axes.",
        "inBuildScope": in_build_scope,
        "evidenceIds": ["evidence-selected-tab"],
        "claimIds": ["claim-tab-composite-state"],
    }
    if not in_build_scope:
        intent["scopedOutReason"] = "This alternative is retained only as analysis evidence."
    spec["uiModel"]["responsiveIntent"].append(intent)
    claim(spec, "claim-tab-composite-state")["subjectRefs"].append(intent_id)
    return intent


class ImageCompositionRuleTests(unittest.TestCase):
    def test_legacy_spec_without_policy_remains_valid(self) -> None:
        spec = load_json(EXAMPLE_REQUIREMENT)
        self.assertNotIn("analysisPolicy", spec)
        self.assertTrue(validate(spec)["valid"])

    def test_complete_and_evidence_backed_layer_are_valid(self) -> None:
        self.assertTrue(validate(make_enabled_spec())["valid"])

    def test_policy_requires_composition_on_every_in_scope_image(self) -> None:
        spec = make_enabled_spec()
        del element(spec, SELECTED_ACCENT)["imageComposition"]
        validation = validate(spec)
        self.assertFalse(validation["valid"])
        self.assertIn("image.composition.required", error_codes(validation))

    def test_out_of_scope_image_does_not_require_composition(self) -> None:
        spec = make_enabled_spec()
        image = element(spec, UNSELECTED_BACKGROUND)
        image["inBuildScope"] = False
        image["scopedOutReason"] = "The unselected variant is excluded from this build."
        del image["imageComposition"]
        self.assertTrue(validate(spec)["valid"])

    def test_non_image_cannot_carry_image_composition(self) -> None:
        spec = make_enabled_spec()
        element(spec, "element-screen-root")["imageComposition"] = {
            "groupKey": "screen.background",
            "role": "complete",
            "adaptation": "inherit-owner",
        }
        validation = validate(spec)
        self.assertIn("image.composition.non_image", error_codes(validation))

    def test_composition_shape_is_closed(self) -> None:
        spec = make_enabled_spec()
        element(spec, SELECTED_BACKGROUND)["imageComposition"]["extra"] = True
        validation = validate(spec)
        self.assertFalse(validation["valid"])
        self.assertIn("schema.one_of", error_codes(validation))

    def test_group_key_must_be_stable_lowercase_identifier(self) -> None:
        spec = make_enabled_spec()
        element(spec, SELECTED_BACKGROUND)["imageComposition"]["groupKey"] = "Selected_Art"
        validation = validate(spec)
        self.assertFalse(validation["valid"])
        self.assertIn("schema.one_of", error_codes(validation))

    def test_layer_requires_split_reason(self) -> None:
        spec = make_enabled_spec()
        del element(spec, SELECTED_ACCENT)["imageComposition"]["splitReason"]
        validation = validate(spec)
        self.assertFalse(validation["valid"])
        self.assertIn("schema.one_of", error_codes(validation))

    def test_complete_forbids_split_reason(self) -> None:
        spec = make_enabled_spec()
        element(spec, SELECTED_BACKGROUND)["imageComposition"]["splitReason"] = "runtime-control"
        validation = validate(spec)
        self.assertIn("image.composition.complete_split", error_codes(validation))

    def test_group_requires_exactly_one_complete(self) -> None:
        spec = make_enabled_spec()
        composition = element(spec, SELECTED_BACKGROUND)["imageComposition"]
        composition["role"] = "layer"
        composition["splitReason"] = "resource-reuse"
        validation = validate(spec)
        self.assertIn("image.composition.group_complete", error_codes(validation))

    def test_group_rejects_multiple_complete_images(self) -> None:
        spec = make_enabled_spec()
        composition = element(spec, SELECTED_ACCENT)["imageComposition"]
        composition["role"] = "complete"
        del composition["splitReason"]
        validation = validate(spec)
        self.assertIn("image.composition.group_complete", error_codes(validation))

    def test_layer_requires_evidence(self) -> None:
        spec = make_enabled_spec()
        element(spec, SELECTED_ACCENT)["evidenceIds"] = []
        validation = validate(spec)
        self.assertIn("image.composition.layer_evidence", error_codes(validation))

    def test_independent_adaptation_split_reason_requires_independent_adaptation(self) -> None:
        spec = make_enabled_spec()
        composition = element(spec, SELECTED_ACCENT)["imageComposition"]
        composition["splitReason"] = "independent-adaptation"
        composition["adaptation"] = "inherit-owner"
        validation = validate(spec)
        self.assertIn("image.composition.independent_split_adaptation", error_codes(validation))

    def test_accepted_layer_requires_reviewed_accepted_claim(self) -> None:
        spec = make_enabled_spec()
        layer = element(spec, SELECTED_ACCENT)
        original_claim = claim(spec, "claim-tab-composite-state")
        original_claim["subjectRefs"].remove(SELECTED_ACCENT)
        proposed_claim = copy.deepcopy(original_claim)
        proposed_claim.update(
            {
                "id": "claim-selected-accent-split",
                "type": "asset-decomposition",
                "statement": "The accent remains a proposed separate authored layer.",
                "status": "proposed",
                "impact": "medium",
                "subjectRefs": [SELECTED_ACCENT],
            }
        )
        spec["claims"].append(proposed_claim)
        layer["claimIds"] = [proposed_claim["id"]]
        validation = validate(spec)
        self.assertIn("image.composition.layer_claim", error_codes(validation))

    def test_independent_adaptation_accepts_one_direct_intent(self) -> None:
        spec = make_enabled_spec()
        element(spec, SELECTED_BACKGROUND)["imageComposition"]["adaptation"] = "independent"
        add_element_intent(spec, "responsive-selected-background", SELECTED_BACKGROUND)
        self.assertTrue(validate(spec)["valid"])

    def test_independent_adaptation_requires_direct_intent(self) -> None:
        spec = make_enabled_spec()
        element(spec, SELECTED_BACKGROUND)["imageComposition"]["adaptation"] = "independent"
        validation = validate(spec)
        self.assertIn("image.adaptation.independent_intent", error_codes(validation))

    def test_independent_adaptation_rejects_duplicate_direct_intents(self) -> None:
        spec = make_enabled_spec()
        element(spec, SELECTED_BACKGROUND)["imageComposition"]["adaptation"] = "independent"
        add_element_intent(spec, "responsive-selected-background", SELECTED_BACKGROUND)
        add_element_intent(spec, "responsive-selected-background-alt", SELECTED_BACKGROUND)
        validation = validate(spec)
        self.assertIn("image.adaptation.independent_intent", error_codes(validation))

    def test_out_of_scope_direct_intent_does_not_satisfy_independent_adaptation(self) -> None:
        spec = make_enabled_spec()
        element(spec, SELECTED_BACKGROUND)["imageComposition"]["adaptation"] = "independent"
        add_element_intent(
            spec,
            "responsive-selected-background-preview",
            SELECTED_BACKGROUND,
            in_build_scope=False,
        )
        validation = validate(spec)
        self.assertIn("image.adaptation.independent_intent", error_codes(validation))

    def test_inherited_adaptation_accepts_element_ancestor_intents(self) -> None:
        spec = make_enabled_spec()
        remove_responsive_intents(spec)
        add_element_intent(spec, "responsive-selected-panel", "element-tab-selected-panel")
        add_element_intent(spec, "responsive-unselected-panel", "element-tab-unselected-panel")
        self.assertTrue(validate(spec)["valid"])

    def test_inherited_adaptation_accepts_region_ancestor_intent(self) -> None:
        spec = make_enabled_spec()
        spec["uiModel"]["responsiveIntent"][0]["regionId"] = "region-screen"
        self.assertTrue(validate(spec)["valid"])

    def test_explicit_owner_policy_accepts_exact_region_intent(self) -> None:
        self.assertTrue(validate(make_explicit_owner_spec())["valid"])

    def test_explicit_owner_policy_requires_owner_intent_id(self) -> None:
        spec = make_explicit_owner_spec()
        del element(spec, SELECTED_BACKGROUND)["imageComposition"]["ownerIntentId"]
        validation = validate(spec)
        self.assertIn("image.adaptation.owner_intent_required", error_codes(validation))

    def test_explicit_owner_policy_rejects_unreachable_owner_intent(self) -> None:
        spec = make_explicit_owner_spec()
        add_element_intent(spec, "responsive-selected-background", SELECTED_BACKGROUND)
        element(spec, SELECTED_BACKGROUND)["imageComposition"]["ownerIntentId"] = "responsive-selected-background"
        validation = validate(spec)
        self.assertIn("image.adaptation.owner_intent_invalid", error_codes(validation))

    def test_explicit_owner_policy_rejects_intent_whose_owner_is_out_of_scope(self) -> None:
        spec = make_explicit_owner_spec()
        remove_responsive_intents(spec)
        add_element_intent(spec, "responsive-selected-panel", "element-tab-selected-panel")
        add_element_intent(spec, "responsive-unselected-panel", "element-tab-unselected-panel")
        for element_id in (SELECTED_BACKGROUND, SELECTED_ACCENT):
            element(spec, element_id)["imageComposition"]["ownerIntentId"] = "responsive-selected-panel"
        element(spec, UNSELECTED_BACKGROUND)["imageComposition"]["ownerIntentId"] = "responsive-unselected-panel"
        selected_panel = element(spec, "element-tab-selected-panel")
        selected_panel["inBuildScope"] = False
        selected_panel["scopedOutReason"] = "The owner branch is excluded from this build."

        validation = validate(spec)
        self.assertIn("image.adaptation.owner_intent_invalid", error_codes(validation))

    def test_explicit_owner_policy_forbids_owner_id_on_independent_image(self) -> None:
        spec = make_explicit_owner_spec()
        image = element(spec, SELECTED_BACKGROUND)
        image["imageComposition"]["adaptation"] = "independent"
        add_element_intent(spec, "responsive-selected-background", SELECTED_BACKGROUND)
        validation = validate(spec)
        self.assertIn("image.adaptation.independent_owner", error_codes(validation))

    def test_independent_image_never_accepts_owner_id_when_owner_policy_is_omitted(self) -> None:
        spec = make_enabled_spec()
        spec["analysisPolicy"].pop("explicitImageOwnerIntentRequired", None)
        image = element(spec, SELECTED_BACKGROUND)
        image["imageComposition"]["adaptation"] = "independent"
        image["imageComposition"]["ownerIntentId"] = "responsive-navigation-left"
        add_element_intent(spec, "responsive-selected-background", SELECTED_BACKGROUND)
        validation = validate(spec)
        self.assertIn("image.adaptation.independent_owner", error_codes(validation))

    def test_inherited_adaptation_forbids_a_direct_image_intent(self) -> None:
        spec = make_explicit_owner_spec()
        add_element_intent(spec, "responsive-selected-accent", SELECTED_ACCENT)
        validation = validate(spec)
        self.assertIn("image.adaptation.inherited_direct_intent", error_codes(validation))

    def test_accepted_inherited_adaptation_requires_reachable_source(self) -> None:
        spec = make_enabled_spec()
        remove_responsive_intents(spec)
        validation = validate(spec)
        self.assertIn("image.adaptation.inherited_intent", error_codes(validation))

    def test_self_target_does_not_count_as_inherited_owner(self) -> None:
        spec = make_enabled_spec()
        remove_responsive_intents(spec)
        add_element_intent(spec, "responsive-selected-background", SELECTED_BACKGROUND)
        validation = validate(spec)
        self.assertIn("image.adaptation.inherited_intent", error_codes(validation))

    def test_pending_review_reports_missing_inherited_source_as_warning(self) -> None:
        spec = make_enabled_spec()
        remove_responsive_intents(spec)
        spec["reviewGate"]["status"] = "pending"
        spec["reviewGate"].pop("reviewedBy", None)
        spec["reviewGate"].pop("reviewedAt", None)
        validation = validate(spec)
        self.assertTrue(validation["valid"])
        self.assertIn("image.adaptation.inherited_intent", warning_codes(validation))


class ResponsiveIntentTargetTests(unittest.TestCase):
    def test_element_target_is_valid(self) -> None:
        spec = load_json(EXAMPLE_REQUIREMENT)
        intent = spec["uiModel"]["responsiveIntent"][0]
        del intent["regionId"]
        intent["elementId"] = "element-navigation-panel"
        self.assertTrue(validate(spec)["valid"])

    def test_target_rejects_both_region_and_element(self) -> None:
        spec = load_json(EXAMPLE_REQUIREMENT)
        spec["uiModel"]["responsiveIntent"][0]["elementId"] = "element-navigation-panel"
        validation = validate(spec)
        self.assertIn("responsive.target_exactly_one", error_codes(validation))

    def test_target_rejects_neither_region_nor_element(self) -> None:
        spec = load_json(EXAMPLE_REQUIREMENT)
        del spec["uiModel"]["responsiveIntent"][0]["regionId"]
        validation = validate(spec)
        self.assertIn("responsive.target_exactly_one", error_codes(validation))

    def test_element_target_reference_must_resolve(self) -> None:
        spec = load_json(EXAMPLE_REQUIREMENT)
        intent = spec["uiModel"]["responsiveIntent"][0]
        del intent["regionId"]
        intent["elementId"] = "element-missing-owner"
        validation = validate(spec)
        self.assertIn("ref.element", error_codes(validation))


if __name__ == "__main__":
    unittest.main()
