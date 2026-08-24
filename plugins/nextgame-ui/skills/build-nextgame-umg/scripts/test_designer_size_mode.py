#!/usr/bin/env python3
"""Regression tests for Widget Blueprint Designer DesignSizeMode planning."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import unittest

from prepare_build import build_plan, effective_design_size_mode
from validate_layout_spec import load_json, validate_spec


SKILL_ROOT = Path(__file__).resolve().parent.parent
CATALOG = load_json(SKILL_ROOT / "references" / "component-catalog.json")
RULES = load_json(SKILL_ROOT / "references" / "rule-index.json")


def screen_spec() -> dict:
    return {
        "version": "0.2",
        "mode": "production",
        "asset": {"folder": "/Game/UI/UMG/Role", "name": "umg_role"},
        "referenceSize": [2560, 1440],
        "profile": {
            "adaptive": True,
            "interactive": False,
            "hasText": False,
            "containsRepeatedElements": False,
            "regionGrouping": False,
            "assetKind": "screen",
            "designSizeMode": "FillScreen",
            "system": "role",
            "systemFolder": "Role",
            "subsystem": None,
            "function": None,
            "secondaryFunction": None,
            "targetAsset": {"folder": "/Game/UI/UMG/Role", "name": "umg_role"},
        },
        "nodes": [
            {
                "id": "root",
                "name": "PanelRoot",
                "role": "screen.root",
                "parent": None,
                "rect": [0, 0, 1, 1],
                "anchor": "left-top",
                "properties": {},
            }
        ],
    }


def child_spec() -> dict:
    spec = screen_spec()
    spec["asset"] = {"folder": "/Game/UI/UMG/Role/Widgets", "name": "uw_role_item"}
    spec["referenceSize"] = [400, 232]
    spec["profile"].update(
        {
            "assetKind": "child-widget",
            "designSizeMode": "Desired",
            "function": "item",
            "targetAsset": {
                "folder": "/Game/UI/UMG/Role/Widgets",
                "name": "uw_role_item",
            },
        }
    )
    return spec


def error_codes(spec: dict) -> set[str]:
    return {entry["code"] for entry in validate_spec(spec, CATALOG)["errors"]}


class DesignerSizeModeTests(unittest.TestCase):
    def test_screen_and_child_modes_are_valid(self) -> None:
        self.assertTrue(validate_spec(screen_spec(), CATALOG)["valid"])
        self.assertTrue(validate_spec(child_spec(), CATALOG)["valid"])
        self.assertEqual(effective_design_size_mode(screen_spec()), "FillScreen")
        self.assertEqual(effective_design_size_mode(child_spec()), "Desired")

    def test_asset_kind_mode_mismatches_are_rejected(self) -> None:
        screen = screen_spec()
        screen["profile"]["designSizeMode"] = "Desired"
        self.assertIn("profile.design_size_mode_asset_kind", error_codes(screen))

        child = child_spec()
        child["profile"]["designSizeMode"] = "FillScreen"
        self.assertIn("profile.design_size_mode_asset_kind", error_codes(child))

    def test_on_screen_and_custom_modes_are_rejected(self) -> None:
        for invalid in ("DesiredOnScreen", "Custom", "CustomOnScreen"):
            spec = child_spec()
            spec["profile"]["designSizeMode"] = invalid
            self.assertIn("profile.design_size_mode", error_codes(spec), invalid)

    def test_archived_missing_field_remains_readable_with_warning(self) -> None:
        spec = child_spec()
        del spec["profile"]["designSizeMode"]
        report = validate_spec(spec, CATALOG)
        self.assertTrue(report["valid"], report["errors"])
        self.assertIn(
            "profile.design_size_mode_missing",
            {entry["code"] for entry in report["warnings"]},
        )
        self.assertEqual(effective_design_size_mode(spec), "Desired")

    def test_ambiguous_legacy_prototype_is_not_guessed(self) -> None:
        spec = screen_spec()
        spec["mode"] = "prototype"
        spec["asset"] = {"folder": "/Game/UI/AIPrototype", "name": "umg_ai_preview"}
        spec["profile"]["assetKind"] = "prototype"
        del spec["profile"]["designSizeMode"]
        self.assertIsNone(effective_design_size_mode(spec))
        plan = build_plan(Path("legacy-prototype.json"), spec, CATALOG, RULES)
        self.assertIsNone(plan["designSizeMode"])
        self.assertNotIn("set-design-size-mode", [step["stepId"] for step in plan["steps"]])
        self.assertNotIn("layout.designer-size-mode", plan["selectedRuleIds"])
        self.assertTrue(any("Ambiguous archived prototype" in item for item in plan["warnings"]))

    def test_plan_sets_generated_cdo_after_compile_and_verifies_after_save(self) -> None:
        for spec, expected in ((screen_spec(), "FillScreen"), (child_spec(), "Desired")):
            plan = build_plan(Path("design-size-mode.json"), spec, CATALOG, RULES)
            by_id = {step["stepId"]: step for step in plan["steps"]}
            order = [step["stepId"] for step in plan["steps"]]
            self.assertLess(order.index("compile"), order.index("get-blueprint-default-object"))
            self.assertLess(order.index("set-design-size-mode"), order.index("save"))
            self.assertLess(order.index("save"), order.index("verify-design-size-mode"))
            self.assertEqual(
                by_id["set-design-size-mode"]["arguments"]["values"],
                {"designSizeMode": expected},
            )
            self.assertEqual(
                by_id["set-design-size-mode"]["arguments"]["instance"]["refPath"],
                "${blueprintDefaultObject.returnValue.refPath}",
            )
            self.assertEqual(plan["designSizeMode"], expected)
            self.assertIn("layout.designer-size-mode", plan["selectedRuleIds"])


if __name__ == "__main__":
    unittest.main()
