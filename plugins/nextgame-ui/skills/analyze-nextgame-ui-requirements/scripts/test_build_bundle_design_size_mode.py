#!/usr/bin/env python3
"""Regression tests for asset-kind-driven UMG Designer size-mode contracts."""

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


class RequirementDesignSizeModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = load_json(REQUIREMENT_SCHEMA)
        cls.example = load_json(EXAMPLE_REQUIREMENT)

    def test_policy_derives_modes_only_from_asset_plan_kind(self) -> None:
        requirement = copy.deepcopy(self.example)
        requirement["analysisPolicy"] = {
            "geometryEvidenceRequired": False,
            "listPriorityRequired": False,
            "designSizeModeRequired": True,
        }
        requirement["assetPlan"].append(
            {
                **copy.deepcopy(requirement["assetPlan"][0]),
                "id": "asset-entry-navigation-tab",
                "assetPath": "/Game/UI/UMG/Role/Widgets/uw_role_navigation_tab_list",
                "assetKind": "list-entry",
                "layoutSpecPath": "entry-layout.json",
                "buildOrder": 2,
            }
        )

        self.assertEqual(
            required_design_size_modes(requirement),
            {
                "asset-child-navigation-tab": "Desired",
                "asset-screen-role": "FillScreen",
                "asset-entry-navigation-tab": "Desired",
            },
        )

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
        requirement["reviewGate"]["approvedContentSha256"] = compute_approved_content_sha256(requirement)
        bundle = copy.deepcopy(self.example_bundle)
        bundle["requirement"]["approvedContentSha256"] = requirement["reviewGate"]["approvedContentSha256"]
        child_layout = copy.deepcopy(self.child_layout)
        child_layout["profile"]["designSizeMode"] = "Desired"
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

    def test_policy_rejects_desired_for_screen(self) -> None:
        requirement, bundle, child_layout, screen_layout = self.make_policy_fixture()
        screen_layout["profile"]["designSizeMode"] = "Desired"
        self.assertIn(
            "layout.design_size_mode",
            error_codes(self.validate(requirement, bundle, child_layout, screen_layout)),
        )

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
