#!/usr/bin/env python3
"""Regression tests for executable placement and preview-audit bundle contracts."""

from __future__ import annotations

import copy
import unittest
from pathlib import Path

from _contract_common import ASSETS_ROOT, compute_approved_content_sha256, load_json, sha256_file
from validate_build_bundle import DEFAULT_SCHEMA, validate_build_bundle
from validate_requirement_coverage import validate_requirement_coverage


def error_codes(validation: dict) -> set[str]:
    return {error["code"] for error in validation["errors"]}


class BuildBundleQualityContractsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assets = ASSETS_ROOT
        self.bundle = load_json(self.assets / "example-composite-tabs-build-bundle.json")
        self.requirement = load_json(self.assets / "example-composite-tabs-requirement.json")
        self.requirement["reviewGate"]["approvedContentSha256"] = compute_approved_content_sha256(self.requirement)
        self.bundle["requirement"]["sha256"] = sha256_file(self.assets / "example-composite-tabs-requirement.json")
        self.bundle["requirement"]["approvedContentSha256"] = self.requirement["reviewGate"]["approvedContentSha256"]
        self.schema = load_json(DEFAULT_SCHEMA)
        self.bundle_path = self.assets / "example-composite-tabs-build-bundle.json"

    def validate_bundle(self, bundle: dict) -> dict:
        return validate_build_bundle(
            bundle,
            self.schema,
            bundle_path=self.bundle_path,
            requirement_spec=copy.deepcopy(self.requirement),
            requirement_path=self.assets / "example-composite-tabs-requirement.json",
            check_linked_files=True,
        )

    def test_child_widget_requires_explicit_placement_contract(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        bundle["crossAssetOperations"][0].pop("placementContract")
        self.assertIn("operation.placement_missing", error_codes(self.validate_bundle(bundle)))

    def test_fixed_canvas_host_mismatch_is_rejected(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        compatibility = bundle["crossAssetOperations"][0]["placementContract"]["childSizingCompatibility"]
        compatibility["mode"] = "host-equals-child-reference"
        bundle["crossAssetOperations"][0]["placementContract"]["hostSize"] = [308, 131]
        self.assertIn("operation.child_sizing_fixed_mismatch", error_codes(self.validate_bundle(bundle)))

    def test_full_rect_root_without_stretch_evidence_is_rejected(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        compatibility = bundle["crossAssetOperations"][0]["placementContract"]["childSizingCompatibility"]
        compatibility["mode"] = "source-root-stretch"
        bundle["crossAssetOperations"][0]["placementContract"]["hostSize"] = [308, 131]
        self.assertIn("operation.child_sizing_root_stretch", error_codes(self.validate_bundle(bundle)))

    def test_passed_preview_requires_structured_audit(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        bundle["execution"].update({"status": "completed", "startedAt": "2026-08-04T10:00:00+08:00", "completedAt": "2026-08-04T10:01:00+08:00"})
        for asset in bundle["assets"]:
            asset["status"] = "verified"
        for check in bundle["verification"]["checks"]:
            check["status"] = "passed"
        bundle["verification"]["checks"].append(
            {
                "id": "check-screen-preview",
                "type": "preview",
                "assetId": "build-screen-role",
                "status": "passed",
                "details": "A screenshot exists but has no machine-readable preview audit.",
                "artifactPath": "example-composite-tabs-screen-layout-spec.json",
                "requirementRefs": ["criterion-screen-resolution"],
                "claimIds": ["claim-screen-resolution"],
            }
        )
        bundle["verification"]["status"] = "passed"
        self.assertIn("preview.audit_missing", error_codes(self.validate_bundle(bundle)))

    def test_region_geometry_mismatch_is_reported(self) -> None:
        requirement = copy.deepcopy(self.requirement)
        requirement["uiModel"]["regions"][1]["bounds"] = [0.08, 0.08, 0.30, 0.72]
        coverage = validate_requirement_coverage(copy.deepcopy(self.bundle), requirement, bundle_path=self.bundle_path)
        self.assertIn("coverage.region_geometry", error_codes(coverage))

    def test_region_geometry_prefers_explicit_child_owner_over_screen(self) -> None:
        requirement = copy.deepcopy(self.requirement)
        region_id = "region-navigation"
        region = next(item for item in requirement["uiModel"]["regions"] if item["id"] == region_id)
        region["bounds"] = [0.0, 0.0, 1.0, 1.0]
        screen_plan = next(item for item in requirement["assetPlan"] if item["assetKind"] == "screen")
        screen_plan["coversRegionIds"].remove(region_id)

        coverage = validate_requirement_coverage(copy.deepcopy(self.bundle), requirement, bundle_path=self.bundle_path)
        self.assertTrue(coverage["valid"], coverage["errors"])

    def test_region_mapping_to_non_owner_screen_is_rejected(self) -> None:
        requirement = copy.deepcopy(self.requirement)
        bundle = copy.deepcopy(self.bundle)
        region_id = "region-navigation"
        screen_plan = next(item for item in requirement["assetPlan"] if item["assetKind"] == "screen")
        screen_plan["coversRegionIds"].remove(region_id)
        child_mapping = next(
            mapping
            for mapping in bundle["nodeMappings"]
            if mapping["assetId"] == "build-child-navigation-tab" and region_id in mapping["requirementRefs"]
        )
        child_mapping["requirementRefs"].remove(region_id)

        coverage = validate_requirement_coverage(bundle, requirement, bundle_path=self.bundle_path)
        self.assertIn("coverage.region_owner_mapping", error_codes(coverage))

    def test_region_without_declared_owner_keeps_screen_fallback(self) -> None:
        requirement = copy.deepcopy(self.requirement)
        region_id = "region-navigation"
        for asset_plan in requirement["assetPlan"]:
            if region_id in asset_plan["coversRegionIds"]:
                asset_plan["coversRegionIds"].remove(region_id)

        coverage = validate_requirement_coverage(copy.deepcopy(self.bundle), requirement, bundle_path=self.bundle_path)
        self.assertTrue(coverage["valid"], coverage["errors"])

    def test_image_element_cannot_be_covered_by_a_panel_mapping(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        accent_id = "element-tab-selected-accent"
        accent_mapping = next(mapping for mapping in bundle["nodeMappings"] if accent_id in mapping["requirementRefs"])
        panel_mapping = next(
            mapping
            for mapping in bundle["nodeMappings"]
            if mapping["assetId"] == accent_mapping["assetId"] and mapping["layoutNodeId"] == "node-tab-selected-panel"
        )
        accent_mapping["requirementRefs"].remove(accent_id)
        panel_mapping["requirementRefs"].append(accent_id)
        coverage = validate_requirement_coverage(bundle, copy.deepcopy(self.requirement), bundle_path=self.bundle_path)
        self.assertIn("coverage.element_role", error_codes(coverage))

    def test_static_visual_policy_requires_preview_for_owning_asset(self) -> None:
        requirement = copy.deepcopy(self.requirement)
        requirement.setdefault("analysisPolicy", {})["staticVisualCoverageRequired"] = True
        requirement["reviewGate"]["approvedContentSha256"] = compute_approved_content_sha256(requirement)
        bundle = copy.deepcopy(self.bundle)
        bundle["requirement"]["approvedContentSha256"] = requirement["reviewGate"]["approvedContentSha256"]
        bundle["execution"].update(
            {
                "status": "completed",
                "startedAt": "2026-08-04T10:00:00+08:00",
                "completedAt": "2026-08-04T10:01:00+08:00",
            }
        )
        for asset in bundle["assets"]:
            asset["status"] = "verified"
        for check in bundle["verification"]["checks"]:
            check["status"] = "passed"
        bundle["verification"]["status"] = "passed"

        validation = validate_build_bundle(
            bundle,
            self.schema,
            bundle_path=self.bundle_path,
            requirement_spec=requirement,
            requirement_path=self.assets / "example-composite-tabs-requirement.json",
            check_linked_files=False,
        )
        self.assertIn("preview.visual_asset_coverage", error_codes(validation))


if __name__ == "__main__":
    unittest.main(verbosity=2)
