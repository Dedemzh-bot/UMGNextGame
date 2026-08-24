#!/usr/bin/env python3
"""Regression tests for the curated NextGame shared Widget Blueprint registry."""

from __future__ import annotations

import copy
import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import validate_shared_widget_registry as validator_module

from validate_shared_widget_registry import (
    DEFAULT_REGISTRY,
    DEFAULT_SCHEMA,
    canonical_sha256,
    compute_reuse_contract_sha256,
    load_json,
    validate_registry,
)


def error_codes(report: dict) -> set[str]:
    return {item["code"] for item in report["errors"]}


class SharedWidgetRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = load_json(DEFAULT_SCHEMA)
        cls.registry = load_json(DEFAULT_REGISTRY)

    def refresh_reuse_hash(self, registry: dict) -> None:
        entry = registry["entries"][0]
        entry["reuseContractSha256"] = compute_reuse_contract_sha256(entry)

    def legacy_v03_registry(self, registry: dict) -> dict:
        legacy = copy.deepcopy(registry)
        # The v0.3 compatibility fixture models the historical bag-item entry,
        # not every entry that may exist in the current live v0.4 registry.
        legacy["entries"] = [
            copy.deepcopy(
                next(
                    entry
                    for entry in registry["entries"]
                    if entry["id"] == "shared.common.bag-item"
                )
            )
        ]
        legacy["version"] = "0.3"
        legacy["registryRevision"] = 5
        entry = legacy["entries"][0]
        entry["status"] = "active"
        entry.pop("extensionSlotsContract")
        entry.pop("extensionSlotMigration")
        entry["extensionSlotContract"] = {
            "status": "verified",
            "widgetName": "SlotContent",
            "classPath": "/Script/UMG.NamedSlot",
            "treeOrder": "last",
            "defaultLayout": {
                "mode": "fill",
                "anchors": [0, 0, 1, 1],
                "offsets": [0, 0, 0, 0],
                "alignment": [0, 0],
            },
            "childContentPolicy": {
                "containerRequired": True,
                "containerRole": "panel",
                "onlyWhenNeeded": True,
            },
        }
        for consumer in entry["knownConsumers"]:
            if "slotExtensions" in consumer:
                consumer["slotExtension"] = consumer.pop("slotExtensions")
        self.refresh_reuse_hash(legacy)
        return legacy

    def promote_dual_slots(self, registry: dict) -> dict:
        entry = registry["entries"][0]
        entry["status"] = "active"
        entry["extensionSlotsContract"]["status"] = "verified"
        migration = entry["extensionSlotMigration"]
        migration.update(
            {
                "status": "verified",
                "evidenceArtifactSha256": "f" * 64,
                "verifiedAt": "2026-08-14T12:00:00+08:00",
            }
        )
        widgets = entry["interfaceContract"]["widgets"]
        widgets[:] = [widget for widget in widgets if widget["name"] != "SlotContent"]
        root = next(widget for widget in widgets if widget["parentName"] is None)
        panel = next(widget for widget in widgets if widget["name"] == "PanelItem")
        legacy = next(widget for widget in widgets if widget["name"] == "Slot1")
        panel.update({"siblingIndex": 1, "zOrder": 0})
        legacy.update({"siblingIndex": 2, "zOrder": 0})
        fill = {
            "anchors": [0, 0, 1, 1],
            "offsets": [0, 0, 0, 0],
            "alignment": [0, 0],
        }
        widgets.extend(
            [
                {
                    "name": "SlotDown",
                    "classPath": "/Script/UMG.NamedSlot",
                    "parentName": root["name"],
                    "isVariable": True,
                    "isInherited": False,
                    "visibility": "SelfHitTestInvisible",
                    "autoSize": False,
                    "siblingIndex": 0,
                    "zOrder": -1,
                    "slotLayout": copy.deepcopy(fill),
                },
                {
                    "name": "SlotUp",
                    "classPath": "/Script/UMG.NamedSlot",
                    "parentName": root["name"],
                    "isVariable": True,
                    "isInherited": False,
                    "visibility": "SelfHitTestInvisible",
                    "autoSize": False,
                    "siblingIndex": 3,
                    "zOrder": 1,
                    "slotLayout": copy.deepcopy(fill),
                },
            ]
        )
        entry["interfaceSha256"] = canonical_sha256(entry["interfaceContract"])
        self.refresh_reuse_hash(registry)
        return registry

    def completed_migration_report(self, registry: dict) -> dict:
        entry = registry["entries"][0]
        common_path = entry["assetPath"]
        common_item_path = "/Game/UI/UMG/Widgets/uw_common_item"
        common_sha_before = "1" * 64
        common_sha_after = "2" * 64
        common_item_sha = "3" * 64
        slot_layout = {
            "anchorsMinimum": [0, 0],
            "anchorsMaximum": [1, 1],
            "offsets": [0, 0, 0, 0],
            "alignment": [0, 0],
            "autoSize": False,
            "zOrder": 0,
        }
        slot1_tree = {
            "name": "Slot1",
            "classPath": "/Script/UMG.NamedSlot",
            "parentName": "CanvasPanel_48",
            "isVariable": True,
            "visibility": "SelfHitTestInvisible",
            "namedSlotContent": None,
        }
        common_before = {
            "assetPath": common_path,
            "objectPath": entry["objectPath"],
            "generatedClassPath": entry["generatedClassPath"],
            "compileStatus": "<BlueprintStatus.BS_UP_TO_DATE: 3>",
            "tree": [copy.deepcopy(slot1_tree)],
            "slotLayouts": {"Slot1": copy.deepcopy(slot_layout)},
        }
        down_layout = copy.deepcopy(slot_layout)
        down_layout["zOrder"] = -1
        up_layout = copy.deepcopy(slot_layout)
        up_layout["zOrder"] = 1
        common_after = {
            "assetPath": common_path,
            "objectPath": entry["objectPath"],
            "generatedClassPath": entry["generatedClassPath"],
            "compileStatus": "<BlueprintStatus.BS_UP_TO_DATE: 3>",
            "tree": [
                {
                    "name": "SlotDown",
                    "classPath": "/Script/UMG.NamedSlot",
                    "parentName": "CanvasPanel_48",
                    "isVariable": True,
                    "visibility": "SelfHitTestInvisible",
                    "namedSlotContent": None,
                },
                {"name": "PanelItem", "classPath": "/Script/UMG.CanvasPanel", "parentName": "CanvasPanel_48", "isVariable": True, "visibility": "SelfHitTestInvisible"},
                copy.deepcopy(slot1_tree),
                {
                    "name": "SlotUp",
                    "classPath": "/Script/UMG.NamedSlot",
                    "parentName": "CanvasPanel_48",
                    "isVariable": True,
                    "visibility": "SelfHitTestInvisible",
                    "namedSlotContent": None,
                },
            ],
            "slotLayouts": {
                "SlotDown": down_layout,
                "Slot1": copy.deepcopy(slot_layout),
                "SlotUp": up_layout,
            },
            "namedSlotReadback": [
                {
                    "slotName": "SlotDown",
                    "isVariable": True,
                    "visibility": "SelfHitTestInvisible",
                    "contentWidgetName": None,
                    "zOrder": -1,
                    "layout": down_layout,
                },
                {
                    "slotName": "Slot1",
                    "isVariable": True,
                    "visibility": "SelfHitTestInvisible",
                    "contentWidgetName": None,
                    "zOrder": 0,
                    "layout": slot_layout,
                },
                {
                    "slotName": "SlotUp",
                    "isVariable": True,
                    "visibility": "SelfHitTestInvisible",
                    "contentWidgetName": None,
                    "zOrder": 1,
                    "layout": up_layout,
                },
            ],
            "directRootChildren": ["SlotDown", "PanelItem", "Slot1", "SlotUp"],
            "directRootLayers": [
                {"name": "SlotDown", "zOrder": -1},
                {"name": "PanelItem", "zOrder": 0},
                {"name": "Slot1", "zOrder": 0},
                {"name": "SlotUp", "zOrder": 1},
            ],
            "oldStandardPresent": False,
        }
        known_bindings = [
            ["NamedSlot_149", None, "CanvasPanel_42"],
            ["Slot1", None, "CanvasPanel_39"],
        ]
        common_item_snapshot = {
            "namedSlots": copy.deepcopy(known_bindings),
            "namedSlotBindings": None,
        }
        shared_slot_snapshot = {
            "classPath": "/Script/UMG.NamedSlot",
            "parentWidgetName": "CanvasPanel_48",
            "isVariable": True,
            "contentWidgetName": None,
            "layout": copy.deepcopy(slot_layout),
            "zOrder": 0,
            "visibility": "SelfHitTestInvisible",
        }
        legacy_binding_verification = [
            {
                "assetPath": common_item_path,
                "acquisition": "fresh-post-save:GetNamedSlots",
                "reflectionPropertyStatus": "unavailable",
                "beforeNamedSlots": copy.deepcopy(known_bindings),
                "afterNamedSlots": copy.deepcopy(known_bindings),
                "beforeNamedSlotBindings": None,
                "afterNamedSlotBindings": None,
                "matchesPreflight": True,
                "beforeFileSha256": common_item_sha,
                "afterFileSha256": common_item_sha,
                "saved": True,
                "clean": True,
            }
        ]
        shared_legacy_slot_verification = {
            "legacyPreservedNames": ["Slot1"],
            "slots": [
                {
                    "slotName": "Slot1",
                    "before": copy.deepcopy(shared_slot_snapshot),
                    "after": copy.deepcopy(shared_slot_snapshot),
                    "matches": True,
                }
            ],
            "matches": True,
        }
        reported_registry = {
            "entryId": entry["id"],
            "extensionSlotMigration": {
                key: copy.deepcopy(value)
                for key, value in entry["extensionSlotMigration"].items()
                if key not in {"evidenceArtifactSha256", "verifiedAt"}
            },
        }
        reported_registry["extensionSlotMigration"]["status"] = "planned"
        return {
            "version": "0.1",
            "operationId": "registry-test-migration",
            "startedAt": "2026-08-14T12:00:00+08:00",
            "mode": "commit",
            "commitGate": {},
            "allowedAssets": [common_path, common_item_path],
            "mutationAssets": [common_path],
            "compileSaveOrder": [common_path],
            "readOnlyRegressionAssets": [common_item_path],
            "expectedReferencerClosure": [common_item_path],
            "rollbackReloadOnlyAssets": [common_path, common_item_path],
            "forbiddenScope": ["test fixture"],
            "ok": True,
            "status": "completed",
            "mutationAttempted": True,
            "mutationPossible": True,
            "mutationPerformed": True,
            "mutationConfirmed": True,
            "mutationState": "confirmed",
            "mutationTracker": {
                "mutationPossible": True,
                "mutationPerformed": True,
                "attempts": [
                    {"attemptId": 1, "operation": "RenameWidget", "assetPath": common_path, "api": "UMGToolSet.RenameWidget", "state": "performed"},
                    {"attemptId": 2, "operation": "AddWidget", "assetPath": common_path, "api": "UMGToolSet.AddWidget", "state": "performed"},
                ],
            },
            "assetCompileInvoked": True,
            "assetSaveInvoked": True,
            "assetReloadInvoked": True,
            "backupCreated": True,
            "preflight": {
                "registry": copy.deepcopy(reported_registry),
                "common": common_before,
                "consumers": {common_item_path: copy.deepcopy(common_item_snapshot)},
                "historicalReadOnly": {},
                "transitiveReadOnly": {},
                "filesBefore": {
                    common_path: {"sha256": common_sha_before},
                    common_item_path: {"sha256": common_item_sha},
                },
            },
            "backup": {},
            "toctouGuard": {},
            "operations": [
                {"operation": "RenameWidget", "assetPath": common_path, "oldName": "SlotContent", "newName": "SlotUp", "readbackVerified": True},
                {"operation": "AddWidget", "assetPath": common_path, "widgetName": "SlotDown", "readbackVerified": True},
                {"operation": "compile", "assetPath": common_path, "result": True},
                {"operation": "save", "assetPath": common_path, "result": True, "sha256Before": common_sha_before, "sha256After": common_sha_after},
                {"operation": "reload", "assetPaths": [common_path], "result": True},
            ],
            "stageGuards": [],
            "verificationBeforeSave": {},
            "verificationAfterSave": {
                "phase": "fresh-post-save",
                "common": common_after,
                "consumers": {common_item_path: copy.deepcopy(common_item_snapshot)},
                "readOnlyRegression": {},
                "dirtyAllowedPackages": [],
                "globalDirtyGuard": {"current": [], "allowedNewDirty": [], "newDirty": []},
                "referencerClosure": {},
                "namedSlots": {},
                "commonSlotPresence": {"SlotDown": True, "SlotUp": True, "Slot1": True, "SlotContent": False},
                "legacyBindingVerification": copy.deepcopy(legacy_binding_verification),
                "sharedLegacySlotVerification": copy.deepcopy(shared_legacy_slot_verification),
                "freshLoadedAssetPaths": [common_path],
                "diskFiles": {common_path: {"sha256": common_sha_after}},
                "registry": copy.deepcopy(reported_registry),
            },
            "filesAfterSave": {
                common_path: {"sha256": common_sha_after},
                common_item_path: {"sha256": common_item_sha},
            },
            "sharedLayerRelation": {},
            "legacyBindingVerification": legacy_binding_verification,
            "sharedLegacySlotVerification": shared_legacy_slot_verification,
            "rollback": {"attempted": False},
            "completedAt": "2026-08-14T12:01:00+08:00",
        }

    def verified_nested_mode(self, registry: dict, *, parameters: list[dict] | None = None) -> dict:
        entry = registry["entries"][0]
        nested = entry["generationModes"][1]
        nested["status"] = "verified"
        nested["parameterContractStatus"] = "verified" if parameters else "none"
        nested["instanceParameters"] = parameters or []
        nested["evidencePaths"] = ["shared-widget-reuse-modes"]
        self.refresh_reuse_hash(registry)
        return nested

    def nested_consumer(self, registry: dict, *, nested_class: str | None = None, overrides: list[dict] | None = None) -> dict:
        entry = registry["entries"][0]
        host_asset = "/Game/UI/UMG/Test/uw_test_host"
        host_class = "/Game/UI/UMG/Test/uw_test_host.uw_test_host_C"
        actual_nested_class = nested_class or entry["generatedClassPath"]
        entry["evidence"].append(
            {
                "kind": "unreal-readback",
                "path": host_asset,
                "capturedAt": "2026-08-13T01:00:00+08:00",
                "acquisitionMethod": "official-unreal-mcp",
                "assetIdentity": {
                    "generatedClassPath": host_class,
                    "parentClassPath": "/Script/UMG.UserWidget",
                },
                "widgetInstances": [
                    {
                        "widgetName": "UwBagItem",
                        "widgetTreePath": "CanvasRoot/UwBagItem",
                        "classPath": actual_nested_class,
                    }
                ],
            }
        )
        return {
            "assetPath": host_asset,
            "generatedClassPath": host_class,
            "status": "verified",
            "relation": "widget-tree-instance",
            "widgetName": "UwBagItem",
            "widgetTreePath": "CanvasRoot/UwBagItem",
            "sharedPrototypeClassPath": entry["generatedClassPath"],
            "nestedWidgetClassPath": actual_nested_class,
            "parameterOverrides": overrides or [],
            "usage": "Test-only nested consumer fixture.",
            "evidencePaths": [host_asset],
        }

    def test_checked_in_registry_is_valid(self) -> None:
        report = validate_registry(self.registry, self.schema)
        self.assertTrue(report["valid"], report["errors"])

    def test_candidate_entry_is_not_executable(self) -> None:
        entry = self.registry["entries"][0]
        self.assertEqual("candidate", entry["status"])
        self.assertEqual("required-before-activation", entry["extensionSlotsContract"]["status"])
        self.assertEqual("planned", entry["extensionSlotMigration"]["status"])

    def test_legacy_v03_shape_remains_closed_and_valid(self) -> None:
        legacy = self.legacy_v03_registry(self.registry)
        report = validate_registry(legacy, self.schema)
        self.assertTrue(report["valid"], report["errors"])
        mixed = copy.deepcopy(legacy)
        mixed["entries"][0]["extensionSlotsContract"] = copy.deepcopy(
            self.registry["entries"][0]["extensionSlotsContract"]
        )
        report = validate_registry(mixed, self.schema)
        self.assertIn("schema.one_of", error_codes(report))

    def test_common_bag_item_contract_is_registered(self) -> None:
        self.assertEqual("0.4", self.registry["version"])
        entry = next(
            entry
            for entry in self.registry["entries"]
            if entry["id"] == "shared.common.bag-item"
        )
        self.assertEqual("shared.common.bag-item", entry["id"])
        self.assertEqual("方形道具图标基础共用控件", entry["purpose"])
        self.assertEqual("candidate", entry["status"])
        self.assertEqual(["size", "state-model"], entry["similarityContract"]["hardConstraints"])
        self.assertEqual("very-similar", entry["similarityContract"]["decisionThreshold"])
        self.assertEqual("required-before-activation", entry["extensionSlotsContract"]["status"])
        down, up = entry["extensionSlotsContract"]["slots"]
        self.assertEqual(("SlotDown", "first", "strictly-lower-than-all-direct-siblings"), (down["widgetName"], down["treeOrder"], down["zOrderRelation"]))
        self.assertEqual(("SlotUp", "last", "strictly-higher-than-all-direct-siblings"), (up["widgetName"], up["treeOrder"], up["zOrderRelation"]))
        self.assertTrue(down["isVariable"] and up["isVariable"])
        self.assertFalse(down["autoSize"] or up["autoSize"])
        self.assertEqual("SelfHitTestInvisible", down["visibility"])
        self.assertEqual("SelfHitTestInvisible", up["visibility"])
        self.assertEqual("fill", down["defaultLayout"]["mode"])
        self.assertTrue(entry["extensionSlotsContract"]["childContentPolicy"]["simultaneousUseAllowed"])
        self.assertEqual(1, entry["extensionSlotsContract"]["childContentPolicy"]["maxDirectChildrenPerUsedSlot"])
        self.assertEqual("migrate-existing-standard-slot", entry["extensionSlotMigration"]["operation"])
        self.assertEqual("SlotContent", entry["extensionSlotMigration"]["oldStandardName"])
        self.assertEqual("SlotUp", entry["extensionSlotMigration"]["renamedStandardName"])
        self.assertEqual([150, 150], entry["interfaceContract"]["effectiveContentSize"])
        self.assertEqual([110, 110], entry["interfaceContract"]["iconContentSize"])
        self.assertIn("不要把 WBP 继承误判为 Lua 自动继承", entry["notes"])
        modes = {mode["mode"]: mode for mode in entry["generationModes"]}
        inherited = modes["class-settings-parent-class"]
        nested = modes["widget-tree-instance"]
        self.assertEqual("verified", inherited["status"])
        self.assertEqual(entry["generatedClassPath"], inherited["classSettingsParentClassPath"])
        self.assertEqual("verified", nested["status"])
        self.assertEqual("none", nested["parameterContractStatus"])
        self.assertEqual([], nested["instanceParameters"])
        self.assertEqual(entry["generatedClassPath"], nested["nestedWidgetClassPath"])
        self.assertTrue(
            any(
                consumer["relation"] == "widget-tree-instance"
                and consumer["assetPath"] == "/Game/UI/UMG/Production/Widgets/uw_production_makeitem"
                and consumer["nestedWidgetClassPath"] == "/Game/UI/UMG/Widgets/uw_common_item.uw_common_item_C"
                and consumer["parameterOverrides"] == []
                for consumer in entry["knownConsumers"]
            )
        )
        self.assertEqual(compute_reuse_contract_sha256(entry), entry["reuseContractSha256"])

    def test_active_entry_requires_verified_extension_slot(self) -> None:
        registry = copy.deepcopy(self.registry)
        entry = registry["entries"][0]
        entry["status"] = "active"
        report = validate_registry(registry, self.schema)
        self.assertIn("registry.extension_slots_activation", error_codes(report))
        self.assertIn("registry.extension_slot_migration_entry_status", error_codes(report))

    def test_verified_extension_slots_must_exist_at_root_extremes(self) -> None:
        registry = self.promote_dual_slots(copy.deepcopy(self.registry))
        self.assertTrue(validate_registry(registry, self.schema)["valid"])

        missing = copy.deepcopy(registry)
        missing_entry = missing["entries"][0]
        missing_entry["interfaceContract"]["widgets"] = [
            item for item in missing_entry["interfaceContract"]["widgets"] if item["name"] != "SlotDown"
        ]
        missing_entry["interfaceSha256"] = canonical_sha256(missing_entry["interfaceContract"])
        self.refresh_reuse_hash(missing)
        self.assertIn("registry.extension_slot_missing", error_codes(validate_registry(missing, self.schema)))

        wrong_order = copy.deepcopy(registry)
        wrong_entry = wrong_order["entries"][0]
        down = next(item for item in wrong_entry["interfaceContract"]["widgets"] if item["name"] == "SlotDown")
        panel = next(item for item in wrong_entry["interfaceContract"]["widgets"] if item["name"] == "PanelItem")
        down["siblingIndex"], panel["siblingIndex"] = panel["siblingIndex"], down["siblingIndex"]
        wrong_entry["interfaceSha256"] = canonical_sha256(wrong_entry["interfaceContract"])
        self.refresh_reuse_hash(wrong_order)
        self.assertIn("registry.extension_slot_order", error_codes(validate_registry(wrong_order, self.schema)))

    def test_verified_extension_slots_require_strict_z_order_and_variable_flags(self) -> None:
        registry = self.promote_dual_slots(copy.deepcopy(self.registry))
        entry = registry["entries"][0]
        down = next(item for item in entry["interfaceContract"]["widgets"] if item["name"] == "SlotDown")
        down["zOrder"] = 0
        down["isVariable"] = False
        entry["interfaceSha256"] = canonical_sha256(entry["interfaceContract"])
        self.refresh_reuse_hash(registry)
        codes = error_codes(validate_registry(registry, self.schema))
        self.assertIn("registry.extension_slot_z_order", codes)
        self.assertIn("registry.extension_slot_variable", codes)

    def test_verified_extension_slots_require_passive_visibility(self) -> None:
        registry = self.promote_dual_slots(copy.deepcopy(self.registry))
        entry = registry["entries"][0]
        down = next(item for item in entry["interfaceContract"]["widgets"] if item["name"] == "SlotDown")
        down["visibility"] = "Visible"
        entry["interfaceSha256"] = canonical_sha256(entry["interfaceContract"])
        self.refresh_reuse_hash(registry)
        self.assertIn("registry.extension_slot_visibility", error_codes(validate_registry(registry, self.schema)))

    def test_verified_extension_slots_disable_auto_size(self) -> None:
        registry = self.promote_dual_slots(copy.deepcopy(self.registry))
        entry = registry["entries"][0]
        down = next(item for item in entry["interfaceContract"]["widgets"] if item["name"] == "SlotDown")
        down["autoSize"] = True
        entry["interfaceSha256"] = canonical_sha256(entry["interfaceContract"])
        self.refresh_reuse_hash(registry)
        self.assertIn("registry.extension_slot_auto_size", error_codes(validate_registry(registry, self.schema)))

    def test_verified_migration_removes_old_slot_and_binds_report_hash(self) -> None:
        registry = self.promote_dual_slots(copy.deepcopy(self.registry))
        entry = registry["entries"][0]
        root = next(item for item in entry["interfaceContract"]["widgets"] if item["parentName"] is None)
        entry["interfaceContract"]["widgets"].append(
            {
                "name": "SlotContent",
                "classPath": "/Script/UMG.NamedSlot",
                "parentName": root["name"],
                "isVariable": True,
                "isInherited": False,
                "visibility": "SelfHitTestInvisible",
                "autoSize": False,
                "siblingIndex": 4,
                "zOrder": 0,
                "slotLayout": {"anchors": [0, 0, 1, 1], "offsets": [0, 0, 0, 0], "alignment": [0, 0]},
            }
        )
        entry["interfaceSha256"] = canonical_sha256(entry["interfaceContract"])
        self.refresh_reuse_hash(registry)
        self.assertIn("registry.extension_slot_migration_old_present", error_codes(validate_registry(registry, self.schema)))

        registry = self.promote_dual_slots(copy.deepcopy(self.registry))
        registry["entries"] = [registry["entries"][0]]
        entry = registry["entries"][0]
        registry_path = Path("E:/SyntheticProject/Tools/plugin/assets/shared-widget-registry.json")
        entry["extensionSlotMigration"]["reportPath"] = "Saved/migration-report.json"
        report_payload = json.dumps(self.completed_migration_report(registry), separators=(",", ":")).encode("utf-8")
        entry["extensionSlotMigration"]["evidenceArtifactSha256"] = hashlib.sha256(report_payload).hexdigest()
        self.refresh_reuse_hash(registry)
        with (
            patch.object(validator_module, "resolve_registry_artifact_path", return_value=Path("migration-report.json")),
            patch.object(Path, "is_file", return_value=True),
            patch.object(Path, "read_bytes", return_value=report_payload),
        ):
            self.assertTrue(validate_registry(registry, self.schema, registry_path=registry_path, check_linked_files=True)["valid"])
            entry["extensionSlotMigration"]["evidenceArtifactSha256"] = "0" * 64
            self.refresh_reuse_hash(registry)
            self.assertIn(
                "registry.extension_slot_migration_report_hash",
                error_codes(validate_registry(registry, self.schema, registry_path=registry_path, check_linked_files=True)),
            )

    def test_empty_completed_migration_report_is_not_executable_evidence(self) -> None:
        registry = self.promote_dual_slots(copy.deepcopy(self.registry))
        registry["entries"] = [registry["entries"][0]]
        entry = registry["entries"][0]
        report_payload = b'{"status":"completed"}'
        entry["extensionSlotMigration"]["reportPath"] = "Saved/migration-report.json"
        entry["extensionSlotMigration"]["evidenceArtifactSha256"] = hashlib.sha256(report_payload).hexdigest()
        self.refresh_reuse_hash(registry)
        with (
            patch.object(validator_module, "resolve_registry_artifact_path", return_value=Path("migration-report.json")),
            patch.object(Path, "is_file", return_value=True),
            patch.object(Path, "read_bytes", return_value=report_payload),
        ):
            codes = error_codes(
                validate_registry(
                    registry,
                    self.schema,
                    registry_path=Path("E:/SyntheticProject/registry.json"),
                    check_linked_files=True,
                )
            )
        self.assertIn("schema.required", codes)
        self.assertIn("registry.extension_slot_migration_report_status", codes)

    def test_verified_migration_rejects_saved_consumer_binding_change(self) -> None:
        registry = self.promote_dual_slots(copy.deepcopy(self.registry))
        registry["entries"] = [registry["entries"][0]]
        entry = registry["entries"][0]
        entry["extensionSlotMigration"]["reportPath"] = "Saved/migration-report.json"
        report = self.completed_migration_report(registry)
        consumer_path = "/Game/UI/UMG/Widgets/uw_common_item"
        report["verificationAfterSave"]["consumers"][consumer_path]["namedSlots"][0][2] = "CanvasPanel_Drift"
        report_payload = json.dumps(report, separators=(",", ":")).encode("utf-8")
        entry["extensionSlotMigration"]["evidenceArtifactSha256"] = hashlib.sha256(report_payload).hexdigest()
        self.refresh_reuse_hash(registry)
        with (
            patch.object(validator_module, "resolve_registry_artifact_path", return_value=Path("migration-report.json")),
            patch.object(Path, "is_file", return_value=True),
            patch.object(Path, "read_bytes", return_value=report_payload),
        ):
            codes = error_codes(
                validate_registry(
                    registry,
                    self.schema,
                    registry_path=Path("E:/SyntheticProject/registry.json"),
                    check_linked_files=True,
                )
            )
        self.assertIn("registry.extension_slot_migration_report_binding_changed", codes)

    def test_new_shared_widget_add_dual_layer_slots_branch_is_legal(self) -> None:
        registry = self.promote_dual_slots(copy.deepcopy(self.registry))
        entry = registry["entries"][0]
        entry["extensionSlotsContract"]["legacyPreservedNames"] = []
        entry["extensionSlotMigration"] = {
            "operation": "add-dual-layer-slots",
            "status": "verified",
            "addedStandardNames": ["SlotDown", "SlotUp"],
            "preSaveValidationRequired": True,
            "reportPath": "Saved/new-shared-widget-slot-report.json",
            "evidenceArtifactSha256": "a" * 64,
            "verifiedAt": "2026-08-14T12:00:00+08:00",
        }
        widgets = entry["interfaceContract"]["widgets"]
        widgets[:] = [widget for widget in widgets if widget["name"] != "Slot1"]
        slot_up = next(widget for widget in widgets if widget["name"] == "SlotUp")
        slot_up["siblingIndex"] = 2
        entry["interfaceSha256"] = canonical_sha256(entry["interfaceContract"])
        self.refresh_reuse_hash(registry)
        report = validate_registry(registry, self.schema)
        self.assertTrue(report["valid"], report["errors"])

        illegal = copy.deepcopy(registry)
        illegal["entries"][0]["extensionSlotMigration"]["legacyPreservedNames"] = []
        self.refresh_reuse_hash(illegal)
        self.assertIn("schema.one_of", error_codes(validate_registry(illegal, self.schema)))

        illegal_legacy = copy.deepcopy(registry)
        illegal_legacy["entries"][0]["extensionSlotsContract"]["legacyPreservedNames"] = ["Slot1"]
        self.refresh_reuse_hash(illegal_legacy)
        self.assertIn("registry.extension_slot_add_legacy", error_codes(validate_registry(illegal_legacy, self.schema)))

        illegal_old = copy.deepcopy(registry)
        illegal_entry = illegal_old["entries"][0]
        root = next(widget for widget in illegal_entry["interfaceContract"]["widgets"] if widget["parentName"] is None)
        illegal_entry["interfaceContract"]["widgets"].append(
            {
                "name": "SlotContent",
                "classPath": "/Script/UMG.NamedSlot",
                "parentName": root["name"],
                "isVariable": True,
                "isInherited": False,
                "visibility": "SelfHitTestInvisible",
                "autoSize": False,
                "siblingIndex": 3,
                "zOrder": 0,
                "slotLayout": {"anchors": [0, 0, 1, 1], "offsets": [0, 0, 0, 0], "alignment": [0, 0]},
            }
        )
        illegal_entry["interfaceSha256"] = canonical_sha256(illegal_entry["interfaceContract"])
        self.refresh_reuse_hash(illegal_old)
        self.assertIn("registry.extension_slot_add_old_present", error_codes(validate_registry(illegal_old, self.schema)))

    def test_verified_extension_slot_must_read_back_full_fill(self) -> None:
        registry = self.promote_dual_slots(copy.deepcopy(self.registry))
        entry = registry["entries"][0]
        slot = next(item for item in entry["interfaceContract"]["widgets"] if item["name"] == "SlotUp")
        slot["slotLayout"] = {"anchors": [0, 0, 0, 0], "offsets": [0, 0, 0, 0], "alignment": [0, 0]}
        entry["interfaceSha256"] = canonical_sha256(entry["interfaceContract"])
        self.refresh_reuse_hash(registry)
        report = validate_registry(registry, self.schema)
        self.assertIn("registry.extension_slot_layout", error_codes(report))

    def test_inherited_slot_special_adaptation_requires_evidence(self) -> None:
        registry = self.promote_dual_slots(copy.deepcopy(self.registry))
        consumer = registry["entries"][0]["knownConsumers"][0]
        consumer["slotExtensions"] = [
            {
                "slotName": "SlotDown",
                "contentMode": "panel",
                "rootPanelName": "PanelExtra",
                "rootPanelClassPath": "/Script/UMG.CanvasPanel",
                "rootPanelTreePath": "SlotDown/PanelExtra",
                "directChildCount": 1,
                "layoutMode": "special-adaptation",
            },
            {"slotName": "SlotUp", "contentMode": "empty"},
        ]
        report = validate_registry(registry, self.schema)
        self.assertIn("registry.consumer_slot_adaptation_evidence", error_codes(report))

    def test_fight_bag_records_class_settings_but_not_lua_inheritance(self) -> None:
        entry = self.registry["entries"][0]
        consumer = entry["knownConsumers"][0]
        self.assertEqual("class-settings-parent-class", consumer["relation"])
        self.assertEqual(entry["generatedClassPath"], consumer["classSettingsParentClassPath"])
        self.assertEqual(0, consumer["ownWidgetCount"])
        self.assertEqual("independent", consumer["luaInheritance"])
        self.assertEqual("ui.base_widget", consumer["luaSuperModule"])
        self.assertEqual(consumer["generatedClassPath"], consumer["dynamicLoad"]["fallbackClassPath"])

    def test_interface_hash_drift_is_rejected(self) -> None:
        registry = copy.deepcopy(self.registry)
        registry["entries"][0]["interfaceContract"]["iconContentSize"] = [112, 112]
        report = validate_registry(registry, self.schema)
        self.assertIn("registry.interface_digest", error_codes(report))

    def test_reuse_mode_drift_is_rejected(self) -> None:
        registry = copy.deepcopy(self.registry)
        registry["entries"][0]["generationModes"][1]["description"] += " drift"
        report = validate_registry(registry, self.schema)
        self.assertIn("registry.reuse_contract_digest", error_codes(report))

    def test_duplicate_asset_identity_is_rejected(self) -> None:
        registry = copy.deepcopy(self.registry)
        duplicate = copy.deepcopy(registry["entries"][0])
        duplicate["id"] = "shared.common.bag-item-copy"
        registry["entries"].append(duplicate)
        report = validate_registry(registry, self.schema)
        self.assertIn("registry.duplicate_assetPath", error_codes(report))
        self.assertIn("registry.duplicate_generatedClassPath", error_codes(report))

    def test_child_is_not_registered_as_the_same_shared_source(self) -> None:
        registry = copy.deepcopy(self.registry)
        entry = registry["entries"][0]
        entry["assetPath"] = "/Game/UI/UMG/FightBag/Widgets/uw_fightbag_item"
        entry["objectPath"] = "/Game/UI/UMG/FightBag/Widgets/uw_fightbag_item.uw_fightbag_item"
        entry["generatedClassPath"] = "/Game/UI/UMG/FightBag/Widgets/uw_fightbag_item.uw_fightbag_item_C"
        entry["interfaceContract"]["parentClassPath"] = "/Game/UI/UMG/Widgets/uw_common_bag_item.uw_common_bag_item_C"
        entry["interfaceSha256"] = canonical_sha256(entry["interfaceContract"])
        self.refresh_reuse_hash(registry)
        report = validate_registry(registry, self.schema)
        self.assertIn("registry.consumer_self", error_codes(report))

    def test_duplicate_generation_mode_is_rejected(self) -> None:
        registry = copy.deepcopy(self.registry)
        registry["entries"][0]["generationModes"].append(copy.deepcopy(registry["entries"][0]["generationModes"][0]))
        self.refresh_reuse_hash(registry)
        report = validate_registry(registry, self.schema)
        self.assertIn("registry.generation_mode_duplicate", error_codes(report))

    def test_generation_modes_are_closed_discriminated_shapes(self) -> None:
        registry = copy.deepcopy(self.registry)
        inherited = registry["entries"][0]["generationModes"][0]
        inherited["nestedWidgetClassPath"] = registry["entries"][0]["generatedClassPath"]
        self.refresh_reuse_hash(registry)
        report = validate_registry(registry, self.schema)
        self.assertIn("schema.one_of", error_codes(report))
        self.assertIn("registry.generation_mode_fields", error_codes(report))

    def test_generation_mode_uses_shared_generated_class(self) -> None:
        registry = copy.deepcopy(self.registry)
        registry["entries"][0]["generationModes"][1]["nestedWidgetClassPath"] = "/Game/UI/UMG/FightBag/Widgets/uw_fightbag_item.uw_fightbag_item_C"
        self.refresh_reuse_hash(registry)
        report = validate_registry(registry, self.schema)
        self.assertIn("registry.generation_nested_class", error_codes(report))

    def test_unverified_nested_mode_cannot_have_executable_consumer(self) -> None:
        registry = copy.deepcopy(self.registry)
        nested = registry["entries"][0]["generationModes"][1]
        nested["status"] = "unverified"
        nested["parameterContractStatus"] = "unverified"
        nested["evidencePaths"] = ["shared-widget-reuse-modes"]
        self.refresh_reuse_hash(registry)
        registry["entries"][0]["knownConsumers"].append(self.nested_consumer(registry))
        report = validate_registry(registry, self.schema)
        self.assertIn("registry.consumer_mode_unverified", error_codes(report))

    def test_static_nested_consumer_needs_no_lua_or_dynamic_load(self) -> None:
        registry = copy.deepcopy(self.registry)
        self.verified_nested_mode(registry)
        registry["entries"][0]["knownConsumers"].append(self.nested_consumer(registry))
        report = validate_registry(registry, self.schema)
        self.assertTrue(report["valid"], report["errors"])

    def test_inherited_child_may_be_nested_by_a_host(self) -> None:
        registry = copy.deepcopy(self.registry)
        self.verified_nested_mode(registry)
        child_class = registry["entries"][0]["knownConsumers"][0]["generatedClassPath"]
        registry["entries"][0]["knownConsumers"].append(self.nested_consumer(registry, nested_class=child_class))
        report = validate_registry(registry, self.schema)
        self.assertTrue(report["valid"], report["errors"])

    def test_nested_instance_parameter_names_are_unique(self) -> None:
        registry = copy.deepcopy(self.registry)
        parameter = {"name": "ItemStyle", "valueKind": "enum", "required": False, "description": "Presentation style."}
        self.verified_nested_mode(registry, parameters=[parameter, copy.deepcopy(parameter)])
        report = validate_registry(registry, self.schema)
        self.assertIn("registry.instance_parameter_duplicate", error_codes(report))

    def test_unknown_parameter_override_is_rejected(self) -> None:
        registry = copy.deepcopy(self.registry)
        parameter = {"name": "ItemStyle", "valueKind": "enum", "required": False, "description": "Presentation style."}
        self.verified_nested_mode(registry, parameters=[parameter])
        override = {"name": "UnknownStyle", "valueSource": "literal", "value": "Fight"}
        registry["entries"][0]["knownConsumers"].append(self.nested_consumer(registry, overrides=[override]))
        report = validate_registry(registry, self.schema)
        self.assertIn("registry.parameter_override_unknown", error_codes(report))

    def test_required_parameter_needs_override_or_default(self) -> None:
        registry = copy.deepcopy(self.registry)
        parameter = {"name": "ItemStyle", "valueKind": "enum", "required": True, "description": "Presentation style."}
        self.verified_nested_mode(registry, parameters=[parameter])
        registry["entries"][0]["knownConsumers"].append(self.nested_consumer(registry))
        report = validate_registry(registry, self.schema)
        self.assertIn("registry.parameter_override_required", error_codes(report))

    def test_verified_consumer_identity_must_match_unreal_evidence(self) -> None:
        registry = copy.deepcopy(self.registry)
        consumer = registry["entries"][0]["knownConsumers"][0]
        consumer["generatedClassPath"] = "/Game/UI/UMG/Fake/uw_forged.uw_forged_C"
        consumer["dynamicLoad"]["fallbackClassPath"] = consumer["generatedClassPath"]
        report = validate_registry(registry, self.schema)
        self.assertIn("registry.consumer_identity_class", error_codes(report))

    def test_verified_parameter_contract_cannot_be_empty(self) -> None:
        registry = copy.deepcopy(self.registry)
        nested = registry["entries"][0]["generationModes"][1]
        nested["status"] = "verified"
        nested["parameterContractStatus"] = "verified"
        nested["instanceParameters"] = []
        self.refresh_reuse_hash(registry)
        report = validate_registry(registry, self.schema)
        self.assertIn("registry.parameter_contract_empty", error_codes(report))

    def test_parameter_default_must_match_value_kind(self) -> None:
        registry = copy.deepcopy(self.registry)
        parameter = {
            "name": "ShowCount",
            "valueKind": "boolean",
            "required": False,
            "defaultValue": "yes",
            "description": "Whether the stack count is visible.",
        }
        self.verified_nested_mode(registry, parameters=[parameter])
        report = validate_registry(registry, self.schema)
        self.assertIn("registry.parameter_default_type", error_codes(report))

    def test_literal_override_must_match_value_kind(self) -> None:
        registry = copy.deepcopy(self.registry)
        parameter = {"name": "ShowCount", "valueKind": "boolean", "required": False, "description": "Visibility."}
        self.verified_nested_mode(registry, parameters=[parameter])
        override = {"name": "ShowCount", "valueSource": "literal", "value": "yes"}
        registry["entries"][0]["knownConsumers"].append(self.nested_consumer(registry, overrides=[override]))
        report = validate_registry(registry, self.schema)
        self.assertIn("registry.parameter_override_type", error_codes(report))

    def test_duplicate_evidence_path_is_rejected(self) -> None:
        registry = copy.deepcopy(self.registry)
        registry["entries"][0]["evidence"].append(copy.deepcopy(registry["entries"][0]["evidence"][0]))
        report = validate_registry(registry, self.schema)
        self.assertIn("registry.evidence_duplicate", error_codes(report))


if __name__ == "__main__":
    unittest.main()
