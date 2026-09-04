#!/usr/bin/env python3
"""Regression tests for the Bundle 0.3 planned shared-Widget bootstrap gate."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import test_build_bundle_reuse_relations as reuse_fixtures
import validate_build_bundle as build_bundle_validator
from _contract_common import sha256_file
from validate_build_bundle import (
    _is_allowed_bootstrap_source,
    _validate_shared_bootstrap_binding,
)
from validate_shared_widget_bootstrap import compute_bootstrap_contract_sha256
from validate_shared_widget_registry import load_json


PLUGIN_ROOT = Path(__file__).resolve().parents[3]


@contextmanager
def project_temporary_directory():
    test_root = PLUGIN_ROOT.parent.parent / "Saved" / "CodexUITestTemp"
    test_root.mkdir(parents=True, exist_ok=True)
    path = test_root / f"bootstrap-test-{uuid.uuid4().hex}"
    path.mkdir()
    try:
        (path / "NextGame.uproject").write_text("{}\n", encoding="utf-8")
        registry = load_json(build_bundle_validator.AUTHORITATIVE_SHARED_REGISTRY)
        registry["entries"] = [
            copy.deepcopy(
                next(
                    entry
                    for entry in registry["entries"]
                    if entry["id"] == "shared.common.bag-item"
                )
            )
        ]
        registry_path = path / "authority" / "shared-widget-registry.json"
        registry_path.parent.mkdir()
        registry_path.write_text(
            json.dumps(registry, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        with patch.object(
            build_bundle_validator,
            "AUTHORITATIVE_SHARED_REGISTRY",
            registry_path,
        ):
            yield path
    finally:
        shutil.rmtree(path)


def error_codes(validation: dict) -> set[str]:
    return {error["code"] for error in validation["errors"]}


class PlannedBootstrapBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        fixture = reuse_fixtures.BuildBundleReuseRelationsTests("test_legacy_v01_remains_schema_valid")
        fixture.setUp()
        self.fixture = fixture

    def make_bundle(self) -> dict:
        bundle = copy.deepcopy(self.fixture.bundle_v03)
        prototype = bundle["assets"][0]
        prototype.update(
            {
                "assetPath": "/Game/UI/UMG/Widgets/uw_common_material_list",
                "assetKind": "list-entry",
                "representationKind": "layout-spec",
                "layoutSpecPath": "layouts/uw_common_material_list.layout.json",
                "layoutSpecSha256": "1" * 64,
                "referenceSize": [176, 176],
            }
        )
        extension = bundle["reuseRelations"][0]
        extension["sourceAssetPath"] = prototype["assetPath"]
        extension["targetAssetPath"] = prototype["assetPath"]
        extension["namedSlots"].pop("legacyStandardMigration")
        extension["namedSlots"]["operation"] = "add-dual-layer-slots"
        extension["namedSlots"]["legacyPreservedNames"] = []
        extension.pop("registry")
        extension["bootstrapSnapshot"] = {
            "snapshotPath": "registry-snapshots/shared-widget-bootstrap." + "2" * 64 + ".json",
            "snapshotSha256": "2" * 64,
            "snapshotId": "nextgame-shared-widget-bootstrap",
            "snapshotVersion": "0.1",
            "snapshotRevision": 1,
            "entryId": "shared.common.material-list",
            "entryStatus": "planned-bootstrap",
            "extensionSlotsStatus": "required-before-activation",
            "bootstrapContractSha256": "3" * 64,
        }
        parent = bundle["reuseRelations"][1]
        parent["sourceAssetPath"] = prototype["assetPath"]
        parent["parentClassPath"] = "/Game/UI/UMG/Widgets/uw_common_material_list.uw_common_material_list_C"
        nested = bundle["reuseRelations"][2]
        nested["sharedPrototypeClassPath"] = parent["parentClassPath"]
        return bundle

    def test_pending_bootstrap_relation_is_schema_valid(self) -> None:
        bundle = self.make_bundle()
        relation_errors = reuse_fixtures.validate_schema_instance(
            bundle["reuseRelations"][0],
            self.fixture.schema["$defs"]["sharedPrototypeExtensionRelationV03"],
            root_schema=self.fixture.schema,
            path="$.reuseRelations[0]",
        )
        self.assertEqual([], relation_errors)

    def test_planned_bootstrap_cannot_skip_linked_files(self) -> None:
        codes = error_codes(self.fixture.validate(self.make_bundle()))
        self.assertIn("reuse.bootstrap_binding", codes)
        self.assertNotIn("reuse.parameter_registry_binding", codes)

    def test_every_consumer_is_gated_before_bootstrap_activation(self) -> None:
        for consumer_index in (1, 2):
            with self.subTest(consumer_index=consumer_index):
                bundle = self.make_bundle()
                bundle["assets"][consumer_index]["status"] = "built"
                codes = error_codes(self.fixture.validate(bundle))
                self.assertIn("reuse.activation_premature_consumer", codes)
                self.assertIn("reuse.parameter_registry_binding", codes)

    def test_pending_bootstrap_rejects_parameter_override(self) -> None:
        bundle = self.make_bundle()
        bundle["reuseRelations"][2]["parameterOverrides"] = [
            {"name": "AccentVisible", "valueSource": "literal", "value": True}
        ]
        self.assertIn("reuse.parameter_registry_binding", error_codes(self.fixture.validate(bundle)))

    def test_final_lifecycle_cannot_retain_planned_bootstrap(self) -> None:
        bundle = self.make_bundle()
        self.fixture._set_final_lifecycle(bundle, execution_completed=True, verification_passed=True)
        codes = error_codes(self.fixture.validate(bundle))
        self.assertIn("reuse.activation_lifecycle", codes)
        self.assertIn("reuse.activation_premature_consumer", codes)
        self.assertIn("reuse.parameter_registry_binding", codes)

    def test_reuse_only_existing_asset_cannot_claim_bootstrap(self) -> None:
        bundle = self.make_bundle()
        prototype = bundle["assets"][0]
        prototype.update({"representationKind": "reuse-only", "layoutSpecPath": None, "layoutSpecSha256": None})
        self.assertIn("reuse.bootstrap_representation", error_codes(self.fixture.validate(bundle)))

    def test_candidate_and_active_registry_relations_keep_existing_semantics(self) -> None:
        candidate_codes = error_codes(self.fixture.validate(copy.deepcopy(self.fixture.bundle_v03)))
        self.assertFalse({code for code in candidate_codes if code.startswith("reuse.bootstrap")}, candidate_codes)

        active = copy.deepcopy(self.fixture.bundle_v03)
        extension = active["reuseRelations"][0]
        extension["registry"].update({"entryStatus": "active", "extensionSlotsStatus": "verified"})
        extension["activation"] = {
            "mode": "preverified",
            "resultingEntryStatus": "active",
            "resultingExtensionSlotsStatus": "verified",
        }
        active_codes = error_codes(self.fixture.validate(active))
        self.assertFalse({code for code in active_codes if code.startswith("reuse.bootstrap")}, active_codes)


class PlannedBootstrapBindingTests(unittest.TestCase):
    def write_contract(
        self,
        task_root: Path,
        *,
        asset_name: str,
        asset_scope: str = "project-common",
        system: str = "common",
        list_role: str = "entry",
    ) -> tuple[Path, dict, dict]:
        asset_path = f"/Game/UI/UMG/Widgets/{asset_name}"
        layout = {
            "version": "0.2",
            "mode": "production",
            "asset": {"folder": "/Game/UI/UMG/Widgets", "name": asset_name},
            "referenceSize": [176, 176],
            "profile": {
                "assetKind": "child-widget",
                "assetScope": asset_scope,
                "system": system,
                "listRole": list_role,
                "parentClass": "/Script/UIFramework.ListViewItem",
            },
            "nodes": [],
        }
        layout_path = task_root / "layouts" / f"{asset_name}.layout.json"
        layout_path.parent.mkdir(parents=True)
        layout_path.write_text(json.dumps(layout, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        layout_hash = sha256_file(layout_path)

        registry_path = build_bundle_validator.AUTHORITATIVE_SHARED_REGISTRY
        registry = load_json(registry_path)
        entry = {
            "id": "shared.common.bootstrap-test",
            "status": "planned-bootstrap",
            "assetPlanId": "asset.common.bootstrap-test",
            "assetPath": asset_path,
            "assetKind": "list-entry",
            "scope": "project-common",
            "layoutSpecPath": f"layouts/{asset_name}.layout.json",
            "layoutSpecSha256": layout_hash,
            "expectedObjectPath": f"{asset_path}.{asset_name}",
            "expectedGeneratedClassPath": f"{asset_path}.{asset_name}_C",
            "expectedParentClassPath": "/Script/UIFramework.ListViewItem",
            "expectedReferenceSize": [176, 176],
            "capabilityIds": ["material.slot.empty"],
            "extensionSlotsStatus": "required-before-activation",
            "authorization": {
                "status": "accepted",
                "actorType": "user",
                "source": "direct-user-message",
                "evidenceRef": "q.project.shared.migration",
            },
        }
        entry["bootstrapContractSha256"] = compute_bootstrap_contract_sha256(entry)
        snapshot = {
            "version": "0.1",
            "snapshotId": "nextgame-shared-widget-bootstrap",
            "snapshotRevision": 1,
            "baseRegistry": {
                "registryId": registry["registryId"],
                "registryVersion": registry["version"],
                "registryRevision": registry["registryRevision"],
                "registrySha256": sha256_file(registry_path),
            },
            "entries": [entry],
        }
        snapshot_bytes = (json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        snapshot_hash = hashlib.sha256(snapshot_bytes).hexdigest()
        snapshot_path = task_root / "registry-snapshots" / f"shared-widget-bootstrap.{snapshot_hash}.json"
        snapshot_path.parent.mkdir(parents=True)
        snapshot_path.write_bytes(snapshot_bytes)

        source_asset = {
            "id": "build-common-bootstrap-test",
            "assetPlanId": entry["assetPlanId"],
            "assetPath": asset_path,
            "assetKind": entry["assetKind"],
            "referenceSize": entry["expectedReferenceSize"],
            "representationKind": "layout-spec",
            "layoutSpecPath": entry["layoutSpecPath"],
            "layoutSpecSha256": layout_hash,
            "dependsOnAssetIds": [],
            "buildOrder": 0,
            "status": "planned",
        }
        binding = {
            "snapshotPath": f"registry-snapshots/{snapshot_path.name}",
            "snapshotSha256": snapshot_hash,
            "snapshotId": snapshot["snapshotId"],
            "snapshotVersion": snapshot["version"],
            "snapshotRevision": snapshot["snapshotRevision"],
            "entryId": entry["id"],
            "entryStatus": entry["status"],
            "extensionSlotsStatus": entry["extensionSlotsStatus"],
            "bootstrapContractSha256": entry["bootstrapContractSha256"],
        }
        return snapshot_path, source_asset, binding

    def test_linked_content_addressed_bootstrap_binding_is_valid(self) -> None:
        with project_temporary_directory() as task_root:
            snapshot_path, source_asset, binding = self.write_contract(task_root, asset_name="uw_common_bootstrap_contract_test")
            bundle_path = task_root / "ui-build-bundle.json"
            errors = _validate_shared_bootstrap_binding(binding, source_asset=source_asset, bundle_path=bundle_path, path="$.bootstrapSnapshot")
            self.assertEqual([], errors)
            self.assertTrue(_is_allowed_bootstrap_source(snapshot_path, bundle_path=bundle_path, declared_sha256=binding["snapshotSha256"]))

    def test_zero_hash_and_arbitrary_path_are_rejected(self) -> None:
        with project_temporary_directory() as task_root:
            snapshot_path, source_asset, binding = self.write_contract(task_root, asset_name="uw_common_bootstrap_authority_test")
            bundle_path = task_root / "ui-build-bundle.json"

            zero = copy.deepcopy(binding)
            zero["snapshotSha256"] = "0" * 64
            errors = _validate_shared_bootstrap_binding(zero, source_asset=source_asset, bundle_path=bundle_path, path="$.bootstrapSnapshot")
            self.assertIn("reuse.bootstrap_authority", {error["code"] for error in errors})

            sibling_path = task_root / snapshot_path.name
            sibling_path.write_bytes(snapshot_path.read_bytes())
            arbitrary = copy.deepcopy(binding)
            arbitrary["snapshotPath"] = sibling_path.name
            errors = _validate_shared_bootstrap_binding(arbitrary, source_asset=source_asset, bundle_path=bundle_path, path="$.bootstrapSnapshot")
            self.assertIn("reuse.bootstrap_authority", {error["code"] for error in errors})

    def test_existing_common_currency_package_cannot_use_bootstrap(self) -> None:
        with project_temporary_directory() as task_root:
            _, source_asset, binding = self.write_contract(task_root, asset_name="uw_common_currency")
            package_path = task_root / "Content" / "UI" / "UMG" / "Widgets" / "uw_common_currency.uasset"
            package_path.parent.mkdir(parents=True)
            package_path.write_bytes(b"synthetic-existing-package")
            errors = _validate_shared_bootstrap_binding(
                binding,
                source_asset=source_asset,
                bundle_path=task_root / "ui-build-bundle.json",
                path="$.bootstrapSnapshot",
            )
            self.assertIn("reuse.bootstrap_existing_asset", {error["code"] for error in errors})

    def test_system_scoped_layout_cannot_use_bootstrap(self) -> None:
        with project_temporary_directory() as task_root:
            _, source_asset, binding = self.write_contract(
                task_root,
                asset_name="uw_common_bootstrap_scope_test",
                asset_scope="system",
                system="weapon",
            )
            errors = _validate_shared_bootstrap_binding(
                binding,
                source_asset=source_asset,
                bundle_path=task_root / "ui-build-bundle.json",
                path="$.bootstrapSnapshot",
            )
            self.assertIn("reuse.bootstrap_scope", {error["code"] for error in errors})

    def test_list_entry_layout_keeps_entry_role_contract(self) -> None:
        with project_temporary_directory() as task_root:
            _, source_asset, binding = self.write_contract(
                task_root,
                asset_name="uw_common_bootstrap_role_test",
                list_role="container",
            )
            errors = _validate_shared_bootstrap_binding(
                binding,
                source_asset=source_asset,
                bundle_path=task_root / "ui-build-bundle.json",
                path="$.bootstrapSnapshot",
            )
            self.assertIn("reuse.bootstrap_list_role", {error["code"] for error in errors})


if __name__ == "__main__":
    unittest.main(verbosity=2)
