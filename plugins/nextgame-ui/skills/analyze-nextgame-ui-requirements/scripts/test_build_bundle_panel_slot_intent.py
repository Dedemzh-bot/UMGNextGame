#!/usr/bin/env python3
"""Regression tests for Requirement panel Slot intent lowering into UILayoutSpec."""

from __future__ import annotations

import copy
import unittest
from pathlib import Path
from typing import Callable
from unittest.mock import patch

from _contract_common import ASSETS_ROOT, compute_approved_content_sha256, load_json
import validate_build_bundle as build_bundle_validator
from validate_build_bundle import (
    DEFAULT_SCHEMA,
    _panel_slot_realizations,
    _relation_slot_from_panel_intent,
    validate_build_bundle,
)


EXAMPLE_PREFIX = "example-composite-tabs"
PARENT_ELEMENT_ID = "element-tab-selected-panel"
PARENT_NODE_ID = "node-tab-selected-panel"
CHILD_PAIRS = (
    ("element-tab-selected-background", "node-tab-selected-background"),
    ("element-tab-selected-accent", "node-tab-selected-accent"),
    ("element-tab-selected-label", "node-tab-selected-label"),
)
VIRTUAL_ARTIFACT_ROOT = ASSETS_ROOT / "__virtual_panel_slot_test__"


def error_codes(validation: dict) -> set[str]:
    return {error["code"] for error in validation["errors"]}


def find_by_id(items: list[dict], entity_id: str) -> dict:
    return next(item for item in items if item.get("id") == entity_id)


class BuildBundlePanelSlotIntentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = load_json(DEFAULT_SCHEMA)
        self.requirement_template = load_json(ASSETS_ROOT / f"{EXAMPLE_PREFIX}-requirement.json")
        self.bundle_template = load_json(ASSETS_ROOT / f"{EXAMPLE_PREFIX}-build-bundle.json")
        self.child_layout_template = load_json(ASSETS_ROOT / f"{EXAMPLE_PREFIX}-child-layout-spec.json")
        self.screen_layout_template = load_json(ASSETS_ROOT / f"{EXAMPLE_PREFIX}-screen-layout-spec.json")

    def _base_artifacts(self) -> tuple[dict, dict, dict, dict]:
        requirement = copy.deepcopy(self.requirement_template)
        bundle = copy.deepcopy(self.bundle_template)
        child_layout = copy.deepcopy(self.child_layout_template)
        screen_layout = copy.deepcopy(self.screen_layout_template)

        requirement["analysisPolicy"] = {
            "geometryEvidenceRequired": False,
            "listPriorityRequired": False,
            "explicitPanelSlotsRequired": True,
        }
        parent_element = find_by_id(requirement["uiModel"]["elements"], PARENT_ELEMENT_ID)
        parent_element["layoutRole"] = "container.horizontal"
        parent_node = find_by_id(child_layout["nodes"], PARENT_NODE_ID)
        parent_node["role"] = "container.horizontal"
        child_layout["profile"]["explicitPanelSlots"] = True
        screen_layout["profile"]["explicitPanelSlots"] = True

        alignments = (("Fill", "Fill"), ("Center", "Center"), ("Fill", "Center"))
        for (element_id, node_id), (horizontal, vertical) in zip(CHILD_PAIRS, alignments):
            slot = {
                "size": {"rule": "Auto"},
                "padding": [0, 0, 0, 0],
                "horizontalAlignment": horizontal,
                "verticalAlignment": vertical,
            }
            element = find_by_id(requirement["uiModel"]["elements"], element_id)
            element["panelSlotIntent"] = {
                "slotType": "flow",
                "sizingBasis": "content-driven",
                **copy.deepcopy(slot),
                "reason": "The child uses Desired Size on the flow axis and an evidence-based alignment inside its immediate parent Slot.",
            }
            find_by_id(child_layout["nodes"], node_id)["flowSlot"] = slot

        return requirement, bundle, child_layout, screen_layout

    def _validate(self, mutate: Callable[[dict, dict, dict, dict], None] | None = None) -> dict:
        requirement, bundle, child_layout, screen_layout = self._base_artifacts()
        if mutate is not None:
            mutate(requirement, bundle, child_layout, screen_layout)
        requirement["reviewGate"]["approvedContentSha256"] = compute_approved_content_sha256(requirement)
        bundle["requirement"]["approvedContentSha256"] = requirement["reviewGate"]["approvedContentSha256"]

        requirement_path = VIRTUAL_ARTIFACT_ROOT / f"{EXAMPLE_PREFIX}-requirement.json"
        child_layout_path = VIRTUAL_ARTIFACT_ROOT / f"{EXAMPLE_PREFIX}-child-layout-spec.json"
        screen_layout_path = VIRTUAL_ARTIFACT_ROOT / f"{EXAMPLE_PREFIX}-screen-layout-spec.json"
        bundle_path = VIRTUAL_ARTIFACT_ROOT / f"{EXAMPLE_PREFIX}-build-bundle.json"
        virtual_hashes = {
            requirement_path.name: "4" * 64,
            child_layout_path.name: "5" * 64,
            screen_layout_path.name: "6" * 64,
        }
        virtual_layouts = {
            child_layout_path.name: child_layout,
            screen_layout_path.name: screen_layout,
        }
        bundle["requirement"]["sha256"] = virtual_hashes[requirement_path.name]
        for asset in bundle["assets"]:
            layout_name = Path(asset["layoutSpecPath"]).name
            asset["layoutSpecSha256"] = virtual_hashes[layout_name]

        def load_virtual_json(path: Path) -> dict:
            name = Path(path).name
            if name in virtual_layouts:
                return copy.deepcopy(virtual_layouts[name])
            return load_json(Path(path))

        def hash_virtual_file(path: Path) -> str:
            name = Path(path).name
            if name in virtual_hashes:
                return virtual_hashes[name]
            raise AssertionError(f"Unexpected virtual hash request: {path}")

        with patch.object(build_bundle_validator, "load_json", side_effect=load_virtual_json), patch.object(
            build_bundle_validator, "sha256_file", side_effect=hash_virtual_file
        ):
            return validate_build_bundle(
                bundle,
                self.schema,
                bundle_path=bundle_path,
                requirement_spec=requirement,
                requirement_path=requirement_path,
                check_linked_files=True,
            )

    def test_reviewed_auto_and_fill_alignment_survive_exactly_into_layout(self) -> None:
        validation = self._validate()
        self.assertTrue(validation["valid"], validation["errors"])

    def test_new_requirement_driven_layout_must_enable_explicit_panel_slots(self) -> None:
        def mutate(_requirement: dict, _bundle: dict, child_layout: dict, _screen_layout: dict) -> None:
            child_layout["profile"].pop("explicitPanelSlots")

        validation = self._validate(mutate)
        self.assertIn("layout.explicit_panel_slots", error_codes(validation))

    def test_slot_values_cannot_be_reinferred_differently_during_build(self) -> None:
        def mutate(_requirement: dict, _bundle: dict, child_layout: dict, _screen_layout: dict) -> None:
            find_by_id(child_layout["nodes"], CHILD_PAIRS[0][1])["flowSlot"]["size"] = {
                "rule": "Fill",
                "weight": 1,
            }

        validation = self._validate(mutate)
        self.assertIn("mapping.panel_slot_values", error_codes(validation))

    def test_reviewed_slot_requires_one_slot_bearing_layout_node(self) -> None:
        def mutate(_requirement: dict, _bundle: dict, child_layout: dict, _screen_layout: dict) -> None:
            find_by_id(child_layout["nodes"], CHILD_PAIRS[0][1]).pop("flowSlot")

        validation = self._validate(mutate)
        self.assertIn("mapping.panel_slot_count", error_codes(validation))

    def test_slot_child_must_remain_under_the_mapped_immediate_parent(self) -> None:
        def mutate(_requirement: dict, _bundle: dict, child_layout: dict, _screen_layout: dict) -> None:
            find_by_id(child_layout["nodes"], PARENT_NODE_ID)["role"] = "container.vertical"

        validation = self._validate(mutate)
        self.assertIn("mapping.panel_slot_parent", error_codes(validation))

    def test_reused_child_can_carry_the_reviewed_auto_flow_slot_on_its_instance_relation(self) -> None:
        slot_intent = {
            "slotType": "flow",
            "sizingBasis": "content-driven",
            "size": {"rule": "Auto"},
            "padding": [0, 0, 0, 0],
            "horizontalAlignment": "Fill",
            "verticalAlignment": "Fill",
            "reason": "Desired Size controls the main axis.",
        }
        relation = {
            "type": "widget-tree-instance",
            "requirementRefs": [CHILD_PAIRS[0][0], PARENT_ELEMENT_ID],
        }
        node_mappings, relations = _panel_slot_realizations(
            CHILD_PAIRS[0][0],
            "flowSlot",
            [],
            [relation],
            {},
        )
        self.assertEqual([], node_mappings)
        self.assertEqual([relation], relations)
        self.assertEqual(
            {
                "containerType": "HorizontalBox",
                "size": {"rule": "Auto"},
                "padding": [0, 0, 0, 0],
                "horizontalAlignment": "Fill",
                "verticalAlignment": "Fill",
            },
            _relation_slot_from_panel_intent(slot_intent, "container.horizontal"),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
