#!/usr/bin/env python3
"""Regression tests for reviewed VerticalBox/HorizontalBox/GameScrollBox Slot intent."""

from __future__ import annotations

import copy
import unittest

from _contract_common import ASSETS_ROOT, compute_approved_content_sha256, load_json
from validate_requirement_spec import DEFAULT_SCHEMA, validate_requirement_spec


EXAMPLE_PREFIX = "example-composite-tabs"
PARENT_ID = "element-screen-root"
CHILD_ID = "element-navigation-panel"


def error_codes(validation: dict) -> set[str]:
    return {error["code"] for error in validation["errors"]}


def find_by_id(items: list[dict], entity_id: str) -> dict:
    return next(item for item in items if item.get("id") == entity_id)


class PanelSlotIntentRequirementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = load_json(DEFAULT_SCHEMA)
        self.template = load_json(ASSETS_ROOT / f"{EXAMPLE_PREFIX}-requirement.json")

    def _spec(self, *, scroll: bool = False) -> dict:
        spec = copy.deepcopy(self.template)
        policy = spec.setdefault("analysisPolicy", {})
        policy.setdefault("geometryEvidenceRequired", False)
        policy.setdefault("listPriorityRequired", False)
        policy["explicitPanelSlotsRequired"] = True
        parent = find_by_id(spec["uiModel"]["elements"], PARENT_ID)
        child = find_by_id(spec["uiModel"]["elements"], CHILD_ID)
        if scroll:
            parent["kind"] = "scroll"
            parent["layoutRole"] = "container.game-scroll"
            child["panelSlotIntent"] = {
                "slotType": "scroll",
                "padding": [8, 4, 8, 4],
                "horizontalAlignment": "Right",
                "verticalAlignment": "Top",
                "reason": "The child keeps its measured right/top placement inside the immediate scroll Slot.",
            }
        else:
            parent["layoutRole"] = "container.horizontal"
            child["panelSlotIntent"] = {
                "slotType": "flow",
                "sizingBasis": "content-driven",
                "size": {"rule": "Auto"},
                "padding": [0, 0, 0, 0],
                "horizontalAlignment": "Fill",
                "verticalAlignment": "Fill",
                "reason": "The child fills its allocated Slot while its main-axis size follows Desired Size.",
            }
        spec["reviewGate"]["approvedContentSha256"] = compute_approved_content_sha256(spec)
        return spec

    def test_content_driven_flow_keeps_auto_even_when_both_alignments_fill(self) -> None:
        validation = validate_requirement_spec(self._spec(), self.schema)
        self.assertTrue(validation["valid"], validation["errors"])

    def test_scroll_child_records_immediate_parent_alignments_without_box_size(self) -> None:
        validation = validate_requirement_spec(self._spec(scroll=True), self.schema)
        self.assertTrue(validation["valid"], validation["errors"])

    def test_in_scope_child_of_modeled_flow_requires_slot_intent(self) -> None:
        spec = self._spec()
        find_by_id(spec["uiModel"]["elements"], CHILD_ID).pop("panelSlotIntent")
        spec["reviewGate"]["approvedContentSha256"] = compute_approved_content_sha256(spec)
        validation = validate_requirement_spec(spec, self.schema)
        self.assertIn("panel_slot.intent_required", error_codes(validation))

    def test_flow_slot_intent_is_rejected_under_scroll_parent(self) -> None:
        spec = self._spec(scroll=True)
        child = find_by_id(spec["uiModel"]["elements"], CHILD_ID)
        child["panelSlotIntent"] = {
            "slotType": "flow",
            "sizingBasis": "content-driven",
            "size": {"rule": "Auto"},
            "padding": [0, 0, 0, 0],
            "horizontalAlignment": "Fill",
            "verticalAlignment": "Fill",
            "reason": "Invalid test fixture.",
        }
        spec["reviewGate"]["approvedContentSha256"] = compute_approved_content_sha256(spec)
        validation = validate_requirement_spec(spec, self.schema)
        self.assertIn("panel_slot.type", error_codes(validation))

    def test_panel_slot_intent_is_rejected_without_a_modeled_parent_role(self) -> None:
        spec = self._spec()
        find_by_id(spec["uiModel"]["elements"], PARENT_ID).pop("layoutRole")
        spec["reviewGate"]["approvedContentSha256"] = compute_approved_content_sha256(spec)
        validation = validate_requirement_spec(spec, self.schema)
        self.assertIn("panel_slot.parent_role", error_codes(validation))

    def test_content_driven_flow_cannot_claim_fill_size(self) -> None:
        spec = self._spec()
        child = find_by_id(spec["uiModel"]["elements"], CHILD_ID)
        child["panelSlotIntent"]["size"] = {"rule": "Fill", "weight": 1}
        spec["reviewGate"]["approvedContentSha256"] = compute_approved_content_sha256(spec)
        validation = validate_requirement_spec(spec, self.schema)
        self.assertFalse(validation["valid"])
        self.assertIn("schema.one_of", error_codes(validation))

    def test_layout_role_must_match_the_container_element_kind(self) -> None:
        spec = self._spec()
        find_by_id(spec["uiModel"]["elements"], PARENT_ID)["kind"] = "image"
        spec["reviewGate"]["approvedContentSha256"] = compute_approved_content_sha256(spec)
        validation = validate_requirement_spec(spec, self.schema)
        self.assertIn("panel_slot.container_kind", error_codes(validation))


if __name__ == "__main__":
    unittest.main(verbosity=2)
