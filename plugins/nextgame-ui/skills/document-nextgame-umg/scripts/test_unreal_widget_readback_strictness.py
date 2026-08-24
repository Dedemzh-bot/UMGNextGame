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
                "assetKind": "screen",
                "representationKind": "layout-spec",
            },
            "build.child": {
                "id": "build.child",
                "assetKind": "child-widget",
                "representationKind": "layout-spec",
            },
            "build.entry": {
                "id": "build.entry",
                "assetKind": "list-entry",
                "representationKind": "layout-spec",
            },
            "build.reuse": {
                "id": "build.reuse",
                "assetKind": "child-widget",
                "representationKind": "reuse-only",
            },
        }
        self.assets = [
            {"assetId": "build.screen", "designSizeMode": "FillScreen"},
            {"assetId": "build.child", "designSizeMode": "Desired"},
            {"assetId": "build.entry", "designSizeMode": "Desired"},
            {"assetId": "build.reuse", "designSizeMode": "Desired"},
        ]

    def _errors(self, *, required: bool, assets: list[dict[str, Any]] | None = None) -> list[dict[str, str]]:
        actual_assets = self.assets if assets is None else assets
        errors: list[dict[str, str]] = []
        _validate_design_size_modes(
            {"assets": actual_assets},
            {"analysisPolicy": {"designSizeModeRequired": required}},
            self.bundle_assets,
            indexes={"assets": {asset["assetId"]: asset for asset in actual_assets}},
            errors=errors,
        )
        return errors

    def test_required_policy_accepts_screen_child_entry_and_reuse_only_modes(self) -> None:
        self.assertEqual([], self._errors(required=True))

    def test_required_policy_rejects_missing_mode_for_every_bundle_asset_kind(self) -> None:
        for asset_id in self.bundle_assets:
            with self.subTest(asset_id=asset_id):
                assets = [dict(asset) for asset in self.assets]
                next(asset for asset in assets if asset["assetId"] == asset_id).pop("designSizeMode")
                errors = self._errors(required=True, assets=assets)
                self.assertEqual(["design_size_mode.missing"], error_codes(errors))
                self.assertIn(asset_id, errors[0]["message"])

    def test_legal_but_wrong_mode_is_rejected_for_each_asset_kind(self) -> None:
        for asset_id in self.bundle_assets:
            with self.subTest(asset_id=asset_id):
                assets = [dict(asset) for asset in self.assets]
                target = next(asset for asset in assets if asset["assetId"] == asset_id)
                target["designSizeMode"] = "Desired" if target["designSizeMode"] == "FillScreen" else "FillScreen"
                self.assertEqual(
                    ["design_size_mode.mismatch"],
                    error_codes(self._errors(required=True, assets=assets)),
                )

    def test_legacy_requirement_allows_missing_mode(self) -> None:
        assets = [{"assetId": asset_id} for asset_id in self.bundle_assets]
        self.assertEqual([], self._errors(required=False, assets=assets))

    def test_legacy_requirement_cannot_publish_contradictory_optional_evidence(self) -> None:
        assets = [dict(asset) for asset in self.assets]
        assets[0]["designSizeMode"] = "Desired"
        self.assertEqual(
            ["design_size_mode.mismatch"],
            error_codes(self._errors(required=False, assets=assets)),
        )

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


if __name__ == "__main__":
    unittest.main()
