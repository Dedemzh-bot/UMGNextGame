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
SCHEMA = load_json(SKILL_ROOT / "assets" / "ui-layout-spec.schema.json")


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
    add_fixed_root_content(spec, 400, 232)
    return spec


def add_fixed_root_content(spec: dict, width: int, height: int) -> None:
    spec["nodes"].append(
        {
            "id": "content",
            "name": "PanelContent",
            "role": "container.canvas",
            "parent": "root",
            "rect": [0, 0, 1, 1],
            "anchor": "left-top",
            "slotLayout": {
                "anchors": {"minimum": [0, 0], "maximum": [0, 0]},
                "offsets": {"left": 0, "top": 0, "right": width, "bottom": height},
                "alignment": [0, 0],
                "autoSize": False,
            },
            "properties": {},
        }
    )


def error_codes(spec: dict) -> set[str]:
    return {entry["code"] for entry in validate_spec(spec, CATALOG)["errors"]}


class DesignerSizeModeTests(unittest.TestCase):
    def test_screen_and_child_modes_are_valid(self) -> None:
        self.assertTrue(validate_spec(screen_spec(), CATALOG)["valid"])
        self.assertTrue(validate_spec(child_spec(), CATALOG)["valid"])
        self.assertEqual(effective_design_size_mode(screen_spec()), "FillScreen")
        self.assertEqual(effective_design_size_mode(child_spec()), "Desired")

    def test_explicit_modes_are_not_inferred_from_asset_kind_or_name_prefix(self) -> None:
        screen = screen_spec()
        screen["profile"]["designSizeMode"] = "Desired"
        add_fixed_root_content(screen, 2560, 1440)
        self.assertTrue(validate_spec(screen, CATALOG)["valid"])
        self.assertEqual(effective_design_size_mode(screen), "Desired")

        child = child_spec()
        child["profile"]["designSizeMode"] = "FillScreen"
        self.assertTrue(validate_spec(child, CATALOG)["valid"])
        self.assertEqual(effective_design_size_mode(child), "FillScreen")

    def test_desired_rejects_empty_root_and_zero_offset_full_stretch(self) -> None:
        empty = screen_spec()
        empty["profile"]["designSizeMode"] = "Desired"
        self.assertIn("profile.design_size_mode.desired_root_size", error_codes(empty))
        with self.assertRaisesRegex(ValueError, "desired_root_size"):
            build_plan(Path("invalid-empty-desired.json"), empty, CATALOG, RULES)

        full_stretch = deepcopy(empty)
        full_stretch["nodes"].append(
            {
                "id": "content",
                "name": "PanelContent",
                "role": "container.canvas",
                "parent": "root",
                "rect": [0, 0, 1, 1],
                "anchor": "left-top",
                "slotLayout": {
                    "anchors": {"minimum": [0, 0], "maximum": [1, 1]},
                    "offsets": {"left": 0, "top": 0, "right": 0, "bottom": 0},
                    "alignment": [0, 0],
                    "autoSize": False,
                },
                "properties": {},
            }
        )
        self.assertIn("profile.design_size_mode.desired_root_size", error_codes(full_stretch))

        auto_sized_fixed = screen_spec()
        auto_sized_fixed["profile"]["designSizeMode"] = "Desired"
        add_fixed_root_content(auto_sized_fixed, 2560, 1440)
        auto_sized_fixed["nodes"][1]["slotLayout"]["autoSize"] = True
        self.assertIn("profile.design_size_mode.desired_root_size", error_codes(auto_sized_fixed))

    def test_desired_accepts_verified_content_driven_root_child(self) -> None:
        spec = screen_spec()
        spec["profile"]["designSizeMode"] = "Desired"
        spec["nodes"].append(
            {
                "id": "content",
                "name": "PanelContent",
                "role": "container.vertical",
                "parent": "root",
                "rect": [0, 0, 0.25, 0.25],
                "anchor": "left-top",
                "contentDrivenSize": {
                    "verified": True,
                    "measuredDesiredSize": [640, 360],
                    "evidenceId": "evidence-desired-root",
                },
                "properties": {},
            }
        )
        self.assertTrue(validate_spec(spec, CATALOG)["valid"])

    def test_desired_rejects_unmeasured_or_malformed_content_driven_claims(self) -> None:
        verified_only = screen_spec()
        verified_only["profile"]["designSizeMode"] = "Desired"
        verified_only["nodes"].append(
            {
                "id": "content",
                "name": "PanelContent",
                "role": "container.vertical",
                "parent": "root",
                "rect": [0, 0, 0.25, 0.25],
                "anchor": "left-top",
                "contentDrivenSize": {"verified": True},
                "properties": {},
            }
        )
        self.assertIn("profile.design_size_mode.desired_root_size", error_codes(verified_only))

        malformed = deepcopy(verified_only)
        malformed["nodes"][1]["contentDrivenSize"] = {
            "verified": True,
            "measuredDesiredSize": [0, 360],
            "evidenceId": "Invalid Evidence Id",
            "unsupported": True,
        }
        codes = error_codes(malformed)
        self.assertIn("content_driven_size.measured_desired_size", codes)
        self.assertIn("content_driven_size.evidence_id", codes)
        self.assertIn("content_driven_size.fields", codes)
        self.assertIn("profile.design_size_mode.desired_root_size", codes)

    def test_content_driven_schema_is_closed_and_typed(self) -> None:
        schema = SCHEMA["properties"]["nodes"]["items"]["properties"]["contentDrivenSize"]
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["required"], ["verified"])
        self.assertEqual(
            set(schema["properties"]),
            {"verified", "measuredDesiredSize", "evidenceId"},
        )

    def test_on_screen_and_custom_modes_are_rejected(self) -> None:
        for invalid in ("DesiredOnScreen", "Custom", "CustomOnScreen"):
            spec = child_spec()
            spec["profile"]["designSizeMode"] = invalid
            self.assertIn("profile.design_size_mode", error_codes(spec), invalid)

    def test_legacy_missing_field_defaults_to_fill_screen_with_warning(self) -> None:
        spec = child_spec()
        del spec["profile"]["designSizeMode"]
        report = validate_spec(spec, CATALOG)
        self.assertTrue(report["valid"], report["errors"])
        self.assertIn(
            "profile.design_size_mode_missing",
            {entry["code"] for entry in report["warnings"]},
        )
        self.assertEqual(effective_design_size_mode(spec), "FillScreen")
        plan = build_plan(Path("legacy-child.json"), spec, CATALOG, RULES)
        self.assertEqual(plan["designSizeMode"], "FillScreen")
        self.assertEqual(
            plan["designSizeModeResolution"],
            {
                "mode": "FillScreen",
                "source": "fallback-unclear",
                "fallbackApplied": True,
                "reason": (
                    "No explicit analyzed Designer mode is available; use the fault-tolerant FillScreen default. "
                    "Neither profile.assetKind nor the asset-name prefix was used to infer the mode."
                ),
            },
        )
        self.assertTrue(any("fallback-unclear" in item for item in plan["warnings"]))
        self.assertIn("set-design-size-mode", [step["stepId"] for step in plan["steps"]])

    def test_ambiguous_legacy_prototype_uses_same_fill_screen_fallback(self) -> None:
        spec = screen_spec()
        spec["mode"] = "prototype"
        spec["asset"] = {"folder": "/Game/UI/AIPrototype", "name": "umg_ai_preview"}
        spec["profile"]["assetKind"] = "prototype"
        del spec["profile"]["designSizeMode"]
        self.assertEqual(effective_design_size_mode(spec), "FillScreen")
        plan = build_plan(Path("legacy-prototype.json"), spec, CATALOG, RULES)
        self.assertEqual(plan["designSizeMode"], "FillScreen")
        self.assertIn("set-design-size-mode", [step["stepId"] for step in plan["steps"]])
        self.assertTrue(plan["designSizeModeResolution"]["fallbackApplied"])
        self.assertTrue(any("fallback-unclear" in item for item in plan["warnings"]))

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
            self.assertEqual(plan["designSizeModeResolution"]["source"], "explicit-analysis")
            self.assertFalse(plan["designSizeModeResolution"]["fallbackApplied"])
            self.assertIn("layout.designer-size-mode", plan["selectedRuleIds"])


if __name__ == "__main__":
    unittest.main()
