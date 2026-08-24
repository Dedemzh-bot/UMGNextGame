#!/usr/bin/env python3
"""Regression tests for Widget Blueprint Designer size-mode readback."""

from __future__ import annotations

import unittest
from typing import Any

from _document_contract_common import READBACK_SCHEMA, load_json, validate_schema_instance
from validate_unreal_widget_readback import _validate_design_size_modes


def error_codes(errors: list[dict[str, str]]) -> list[str]:
    return [item["code"] for item in errors]


class DesignerSizeModeReadbackTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bundle_assets = {
            "build.screen": {
                "id": "build.screen",
                "assetPath": "/Game/UI/UMG/Test/uw_embedded_screen",
                "assetKind": "screen",
                "representationKind": "layout-spec",
                "assetPlanId": "plan.screen",
            },
            "build.child": {
                "id": "build.child",
                "assetPath": "/Game/UI/UMG/Test/Widgets/uw_test_child",
                "assetKind": "child-widget",
                "representationKind": "layout-spec",
                "assetPlanId": "plan.child",
            },
            "build.entry": {
                "id": "build.entry",
                "assetPath": "/Game/UI/UMG/Test/Widgets/uw_test_entry",
                "assetKind": "list-entry",
                "representationKind": "layout-spec",
                "assetPlanId": "plan.entry",
            },
            "build.reuse": {
                "id": "build.reuse",
                "assetPath": "/Game/UI/UMG/Test/Widgets/uw_test_reuse",
                "assetKind": "child-widget",
                "representationKind": "reuse-only",
                "assetPlanId": "plan.reuse",
            },
            # Naming is authoritative even when semantic classification says child-widget.
            "build.umg": {
                "id": "build.umg",
                "assetPath": "/Game/UI/UMG/Test/umg_test",
                "assetKind": "child-widget",
                "representationKind": "layout-spec",
                "assetPlanId": "plan.umg",
            },
        }
        self.requirement = {
            "analysisPolicy": {"designSizeModeRequired": True},
            "assetPlan": [
                {"id": "plan.screen", "designSizeModeDecision": {"mode": "FillScreen"}},
                # Ambiguous/local-looking assets may intentionally fall back to FillScreen.
                {"id": "plan.child", "designSizeModeDecision": {"mode": "FillScreen"}},
                {"id": "plan.entry", "designSizeModeDecision": {"mode": "Desired"}},
                {"id": "plan.reuse", "designSizeModeDecision": {"mode": "Desired"}},
                {"id": "plan.umg", "designSizeModeDecision": {"mode": "FillScreen"}},
            ],
        }
        self.assets = [
            {"assetId": "build.screen", "designSizeMode": "FillScreen"},
            {"assetId": "build.child", "designSizeMode": "FillScreen"},
            {"assetId": "build.entry", "designSizeMode": "Desired"},
            {"assetId": "build.reuse", "designSizeMode": "Desired"},
            {"assetId": "build.umg", "designSizeMode": "FillScreen"},
        ]

    def _errors(
        self,
        *,
        required: bool,
        assets: list[dict[str, Any]] | None = None,
        requirement: dict[str, Any] | None = None,
        acquisition: dict[str, Any] | None = None,
    ) -> list[dict[str, str]]:
        actual_assets = self.assets if assets is None else assets
        actual_requirement = self.requirement if requirement is None else requirement
        actual_requirement.setdefault("analysisPolicy", {})["designSizeModeRequired"] = required
        errors: list[dict[str, str]] = []
        _validate_design_size_modes(
            {
                "assets": actual_assets,
                "acquisition": acquisition or {"method": "official-unreal-mcp"},
            },
            actual_requirement,
            self.bundle_assets,
            indexes={"assets": {asset["assetId"]: asset for asset in actual_assets}},
            errors=errors,
        )
        return errors

    def test_required_policy_accepts_each_bound_decision_including_umg_and_reuse_only(self) -> None:
        self.assertEqual([], self._errors(required=True))

    def test_required_policy_rejects_missing_mode_for_every_bundle_asset_kind(self) -> None:
        for asset_id in self.bundle_assets:
            with self.subTest(asset_id=asset_id):
                assets = [dict(asset) for asset in self.assets]
                next(asset for asset in assets if asset["assetId"] == asset_id).pop("designSizeMode")
                errors = self._errors(required=True, assets=assets)
                self.assertEqual(["design_size_mode.missing"], error_codes(errors))
                self.assertIn(asset_id, errors[0]["message"])

    def test_legal_but_wrong_mode_is_rejected_against_each_requirement_decision(self) -> None:
        for asset_id in self.bundle_assets:
            with self.subTest(asset_id=asset_id):
                assets = [dict(asset) for asset in self.assets]
                target = next(asset for asset in assets if asset["assetId"] == asset_id)
                target["designSizeMode"] = "Desired" if target["designSizeMode"] == "FillScreen" else "FillScreen"
                expected_code = (
                    "design_size_mode.umg_requires_fillscreen"
                    if asset_id == "build.umg"
                    else "design_size_mode.mismatch"
                )
                self.assertEqual([expected_code], error_codes(self._errors(required=True, assets=assets)))

    def test_required_policy_fails_closed_when_bound_requirement_decision_is_missing(self) -> None:
        requirement = {
            "analysisPolicy": {"designSizeModeRequired": True},
            "assetPlan": [dict(item) for item in self.requirement["assetPlan"]],
        }
        requirement["assetPlan"][1].pop("designSizeModeDecision")
        errors = self._errors(required=True, requirement=requirement)
        self.assertEqual(["design_size_mode.decision_missing"], error_codes(errors))
        self.assertIn("build.child", errors[0]["message"])

    def test_legacy_requirement_allows_missing_mode(self) -> None:
        assets = [{"assetId": asset_id} for asset_id in self.bundle_assets]
        requirement = {"assetPlan": []}
        self.assertEqual([], self._errors(required=False, assets=assets, requirement=requirement))

    def test_legacy_requirement_optional_mode_is_not_checked_against_asset_kind(self) -> None:
        assets = [dict(asset) for asset in self.assets]
        assets[0]["designSizeMode"] = "Desired"
        requirement = {"assetPlan": []}
        self.assertEqual([], self._errors(required=False, assets=assets, requirement=requirement))

    def test_legacy_umg_desired_is_rejected_even_without_policy(self) -> None:
        assets = [dict(asset) for asset in self.assets]
        next(asset for asset in assets if asset["assetId"] == "build.umg")["designSizeMode"] = "Desired"
        requirement = {"assetPlan": []}
        errors = self._errors(required=False, assets=assets, requirement=requirement)
        self.assertEqual(["design_size_mode.umg_requires_fillscreen"], error_codes(errors))
        self.assertIn("umg_test", errors[0]["message"])

    def test_policy_umg_fill_still_must_match_bound_decision(self) -> None:
        requirement = {
            "analysisPolicy": {"designSizeModeRequired": True},
            "assetPlan": [dict(item) for item in self.requirement["assetPlan"]],
        }
        next(
            item for item in requirement["assetPlan"] if item["id"] == "plan.umg"
        )["designSizeModeDecision"] = {"mode": "Desired"}
        self.assertEqual(
            ["design_size_mode.mismatch"],
            error_codes(self._errors(required=True, requirement=requirement)),
        )

    def test_legacy_requirement_optional_mode_matches_decision_when_bindable(self) -> None:
        assets = [dict(asset) for asset in self.assets]
        assets[1]["designSizeMode"] = "Desired"
        self.assertEqual(
            ["design_size_mode.mismatch"],
            error_codes(self._errors(required=False, assets=assets)),
        )

    def test_optional_mode_rejects_non_contract_value_even_without_policy(self) -> None:
        assets = [dict(asset) for asset in self.assets]
        assets[0]["designSizeMode"] = "DesiredOnScreen"
        requirement = {"assetPlan": []}
        self.assertEqual(
            ["design_size_mode.invalid"],
            error_codes(self._errors(required=False, assets=assets, requirement=requirement)),
        )

    def test_nxue_acquisition_requires_exact_fallback_path_for_each_mode(self) -> None:
        exact_fallbacks = [
            {
                "jsonPath": f"$.assets[{index}].designSizeMode",
                "fallbackReason": "Official MCP did not expose the generated-class CDO property.",
            }
            for index in range(len(self.assets))
        ]
        acquisition = {
            "method": "nxue-agent",
            "fallbackReason": "Official MCP did not expose the generated-class CDO property.",
            "fieldFallbacks": exact_fallbacks,
        }
        self.assertEqual([], self._errors(required=True, acquisition=acquisition))

        acquisition["fieldFallbacks"] = exact_fallbacks[:-1]
        self.assertEqual(
            ["acquisition.design_size_mode_path_missing"],
            error_codes(self._errors(required=True, acquisition=acquisition)),
        )

    def test_mixed_acquisition_rejects_wildcard_design_mode_evidence(self) -> None:
        acquisition = {
            "method": "mixed",
            "fieldFallbacks": [
                {
                    "jsonPath": "$.assets[*].designSizeMode",
                    "fallbackReason": "Official MCP did not expose the generated-class CDO property.",
                }
            ],
        }
        self.assertEqual(
            ["acquisition.design_size_mode_path"],
            error_codes(self._errors(required=True, acquisition=acquisition)),
        )

    def test_mixed_acquisition_accepts_exact_design_mode_fallback(self) -> None:
        acquisition = {
            "method": "mixed",
            "fieldFallbacks": [
                {
                    "jsonPath": "$.assets[1].designSizeMode",
                    "fallbackReason": "Official MCP did not expose this generated-class CDO property.",
                }
            ],
        }
        self.assertEqual([], self._errors(required=True, acquisition=acquisition))

    def test_mixed_acquisition_can_record_official_modes_and_unrelated_fallback(self) -> None:
        acquisition = {
            "method": "mixed",
            "fieldFallbacks": [
                {
                    "jsonPath": "$.reuseRelations[0].namedSlots",
                    "fallbackReason": "Official MCP did not expose inherited NamedSlot details.",
                }
            ],
        }
        self.assertEqual([], self._errors(required=True, acquisition=acquisition))

    def test_schema_exposes_optional_mode_on_all_asset_shapes(self) -> None:
        schema = load_json(READBACK_SCHEMA)
        for definition_name in ("assetV01", "layoutSpecAssetV02", "reuseOnlyAssetV02"):
            with self.subTest(definition_name=definition_name):
                definition = schema["$defs"][definition_name]
                self.assertNotIn("designSizeMode", definition["required"])
                self.assertEqual(
                    {"$ref": "#/$defs/designSizeMode"},
                    definition["properties"]["designSizeMode"],
                )

    def test_schema_accepts_only_fillscreen_or_desired(self) -> None:
        schema = load_json(READBACK_SCHEMA)
        fixture = load_json(READBACK_SCHEMA.parent / "fixtures" / "minimal-unreal-widget-readback.json")
        for mode in ("FillScreen", "Desired"):
            with self.subTest(mode=mode):
                fixture["assets"][0]["designSizeMode"] = mode
                self.assertEqual([], validate_schema_instance(fixture, schema))

        fixture["assets"][0]["designSizeMode"] = "DesiredOnScreen"
        self.assertTrue(validate_schema_instance(fixture, schema))

    def test_schema_allows_exact_field_fallbacks_on_nxue_acquisition(self) -> None:
        schema = load_json(READBACK_SCHEMA)
        fixture = load_json(READBACK_SCHEMA.parent / "fixtures" / "minimal-unreal-widget-readback.json")
        fixture["assets"][0]["designSizeMode"] = "FillScreen"
        fixture["acquisition"] = {
            "method": "nxue-agent",
            "fallbackReason": "Official MCP did not expose the generated-class CDO property.",
            "fieldFallbacks": [
                {
                    "jsonPath": "$.assets[0].designSizeMode",
                    "fallbackReason": "Official MCP did not expose the generated-class CDO property.",
                }
            ],
        }
        self.assertEqual([], validate_schema_instance(fixture, schema))


if __name__ == "__main__":
    unittest.main()
