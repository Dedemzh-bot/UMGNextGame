#!/usr/bin/env python3
"""Regression tests for non-executable shared-Widget bootstrap snapshots."""

from __future__ import annotations

import copy
import unittest

from validate_shared_widget_bootstrap import (
    DEFAULT_SCHEMA,
    compute_bootstrap_contract_sha256,
    validate_bootstrap_snapshot,
)
from validate_shared_widget_registry import DEFAULT_SCHEMA as REGISTRY_SCHEMA, load_json, validate_schema_instance


def error_codes(validation: dict) -> set[str]:
    return {error["code"] for error in validation["errors"]}


class SharedWidgetBootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = load_json(DEFAULT_SCHEMA)
        self.entry = {
            "id": "shared.common.material-list",
            "status": "planned-bootstrap",
            "assetPlanId": "asset.common.material.entry",
            "assetPath": "/Game/UI/UMG/Widgets/uw_common_material_list",
            "assetKind": "list-entry",
            "scope": "project-common",
            "layoutSpecPath": "layouts/uw_common_material_list.layout.json",
            "layoutSpecSha256": "1" * 64,
            "expectedObjectPath": "/Game/UI/UMG/Widgets/uw_common_material_list.uw_common_material_list",
            "expectedGeneratedClassPath": "/Game/UI/UMG/Widgets/uw_common_material_list.uw_common_material_list_C",
            "expectedParentClassPath": "/Script/UIFramework.ListViewItem",
            "expectedReferenceSize": [176, 176],
            "capabilityIds": ["material.slot.empty", "material.slot.populated"],
            "extensionSlotsStatus": "required-before-activation",
            "authorization": {
                "status": "accepted",
                "actorType": "user",
                "source": "direct-user-message",
                "evidenceRef": "q.project.shared.migration",
            },
        }
        self.entry["bootstrapContractSha256"] = compute_bootstrap_contract_sha256(self.entry)
        self.snapshot = {
            "version": "0.1",
            "snapshotId": "nextgame-shared-widget-bootstrap",
            "snapshotRevision": 1,
            "baseRegistry": {
                "registryId": "nextgame-shared-widgets",
                "registryVersion": "0.4",
                "registryRevision": 7,
                "registrySha256": "2" * 64,
            },
            "entries": [self.entry],
        }

    def validate(self, snapshot: dict) -> dict:
        return validate_bootstrap_snapshot(snapshot, self.schema)

    def test_valid_planned_bootstrap_contains_only_expected_contract(self) -> None:
        self.assertTrue(self.validate(copy.deepcopy(self.snapshot))["valid"])

    def test_zero_hash_sentinels_are_rejected(self) -> None:
        for mutation in ("base", "layout", "contract"):
            with self.subTest(mutation=mutation):
                snapshot = copy.deepcopy(self.snapshot)
                if mutation == "base":
                    snapshot["baseRegistry"]["registrySha256"] = "0" * 64
                elif mutation == "layout":
                    snapshot["entries"][0]["layoutSpecSha256"] = "0" * 64
                    snapshot["entries"][0]["bootstrapContractSha256"] = compute_bootstrap_contract_sha256(snapshot["entries"][0])
                else:
                    snapshot["entries"][0]["bootstrapContractSha256"] = "0" * 64
                codes = error_codes(self.validate(snapshot))
                self.assertTrue({"bootstrap.base_registry_zero_sha256", "bootstrap.zero_sha256"} & codes, codes)

    def test_actual_or_unreal_readback_fields_are_forbidden(self) -> None:
        for field, value in (
            ("generatedClassPath", self.entry["expectedGeneratedClassPath"]),
            ("actualObjectPath", self.entry["expectedObjectPath"]),
            ("unrealReadbackPath", "unreal-widget-readback.json"),
            ("unrealReadbackSha256", "4" * 64),
            ("interfaceContract", {"widgets": []}),
            ("evidence", [{"kind": "unreal-readback"}]),
        ):
            with self.subTest(field=field):
                snapshot = copy.deepcopy(self.snapshot)
                snapshot["entries"][0][field] = value
                self.assertIn("schema.additional_property", error_codes(self.validate(snapshot)))

    def test_live_registry_schema_does_not_accept_planned_bootstrap(self) -> None:
        registry_schema = load_json(REGISTRY_SCHEMA)
        live_registry = load_json(REGISTRY_SCHEMA.parent / "shared-widget-registry.json")
        live_registry["entries"][0]["status"] = "planned-bootstrap"
        errors = validate_schema_instance(live_registry, registry_schema, root_schema=registry_schema)
        self.assertTrue(errors)


if __name__ == "__main__":
    unittest.main(verbosity=2)
