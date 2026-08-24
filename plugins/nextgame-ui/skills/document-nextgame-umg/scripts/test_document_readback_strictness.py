#!/usr/bin/env python3
"""Strict regression tests for post-save Unreal Widget readback gates."""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from _document_contract_common import READBACK_SCHEMA, load_json, sha256_file, write_json
from _document_contract_common import (
    system_folder_for_paths,
    runtime_field_node_mappings,
    validate_schema_instance,
    validate_system_folder_with_shared_prototypes,
    verified_shared_prototype_paths,
)
from test_document_contracts import FinalizedSources
from validate_unreal_widget_readback import (
    REQUIRED_READBACK_CHECK_TYPES,
    _validate_reuse_readback_relations,
    validate_unreal_widget_readback,
)


def error_codes(report: dict[str, Any]) -> set[str]:
    return {item["code"] for item in report.get("errors", [])}


class StrictReadbackContractsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sources = FinalizedSources()
        self._install_readback_checks()
        self._sync_bundle_binding()

    def tearDown(self) -> None:
        self.sources.close()

    def _install_readback_checks(self) -> None:
        artifact_path = self.sources.readback_path.name
        present: set[tuple[str, str]] = set()
        checks = self.sources.bundle["verification"]["checks"]
        for check in checks:
            if check.get("type") not in REQUIRED_READBACK_CHECK_TYPES:
                continue
            check["artifactPath"] = artifact_path
            present.add((check["assetId"], check["type"]))

        for asset in self.sources.bundle["assets"]:
            for check_type in REQUIRED_READBACK_CHECK_TYPES:
                if (asset["id"], check_type) in present:
                    continue
                checks.append(
                    {
                        "id": f"check-readback-{check_type}-{asset['id']}",
                        "type": check_type,
                        "assetId": asset["id"],
                        "status": "passed",
                        "details": f"Verified {check_type} from the post-save Unreal readback.",
                        "artifactPath": artifact_path,
                        "requirementRefs": [],
                        "claimIds": [],
                    }
                )

    def _sync_bundle_binding(self) -> None:
        write_json(self.sources.bundle_path, self.sources.bundle)
        self.sources.readback["bundleBinding"]["sha256"] = sha256_file(self.sources.bundle_path)
        write_json(self.sources.readback_path, self.sources.readback)

    def _validate(self) -> dict[str, Any]:
        return validate_unreal_widget_readback(
            self.sources.readback,
            load_json(READBACK_SCHEMA),
            readback_path=self.sources.readback_path,
            requirement=self.sources.requirement,
            requirement_path=self.sources.requirement_path,
            bundle=self.sources.bundle,
            bundle_path=self.sources.bundle_path,
        )

    def _first_branch_binding(self) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        model = next(model for model in self.sources.requirement["stateModels"] if model["implementation"].get("branches"))
        branch = model["implementation"]["branches"][0]
        mapping = next(
            mapping
            for mapping in self.sources.bundle["nodeMappings"]
            if branch["panelElementId"] in mapping.get("requirementRefs", [])
            and branch["stateId"] in mapping.get("stateRefs", [])
        )
        readback_asset = next(asset for asset in self.sources.readback["assets"] if asset["assetId"] == mapping["assetId"])
        readback_mapping = next(item for item in readback_asset["nodeMappings"] if item["nodeMappingId"] == mapping["id"])
        widget = next(item for item in readback_asset["widgets"] if item["widgetName"] == readback_mapping["widgetName"])
        return branch, mapping, widget

    def _layout_for_mapping(self, mapping: dict[str, Any]) -> tuple[Path, dict[str, Any], dict[str, Any]]:
        bundle_asset = next(asset for asset in self.sources.bundle["assets"] if asset["id"] == mapping["assetId"])
        layout_path = self.sources.bundle_path.parent / bundle_asset["layoutSpecPath"]
        layout = load_json(layout_path)
        node = next(node for node in layout["nodes"] if node["id"] == mapping["layoutNodeId"])
        return layout_path, layout, node

    def test_strict_happy_path_is_valid(self) -> None:
        self.assertTrue(self._validate()["valid"])

    def test_state_branch_requires_layout_and_actual_variables_and_visibility(self) -> None:
        branch, mapping, widget = self._first_branch_binding()
        layout_path, layout, node = self._layout_for_mapping(mapping)

        node["isVariable"] = False
        write_json(layout_path, layout)
        bundle_asset = next(asset for asset in self.sources.bundle["assets"] if asset["id"] == mapping["assetId"])
        bundle_asset["layoutSpecSha256"] = sha256_file(layout_path)
        self._sync_bundle_binding()
        self.assertIn("state.layout_variable", error_codes(self._validate()))

        node["isVariable"] = True
        write_json(layout_path, layout)
        bundle_asset["layoutSpecSha256"] = sha256_file(layout_path)
        widget["isVariable"] = False
        self._sync_bundle_binding()
        self.assertIn("state.actual_variable", error_codes(self._validate()))

        widget["isVariable"] = True
        widget["visibility"] = "Visible" if branch["visibility"] != "Visible" else "Collapsed"
        write_json(self.sources.readback_path, self.sources.readback)
        self.assertIn("state.visibility_mismatch", error_codes(self._validate()))

    def test_each_asset_requires_both_passed_checks_for_current_readback(self) -> None:
        target_asset = self.sources.bundle["assets"][0]["id"]
        checks = self.sources.bundle["verification"]["checks"]
        checks[:] = [
            check
            for check in checks
            if not (check.get("assetId") == target_asset and check.get("type") == "key-properties")
        ]
        self._sync_bundle_binding()
        self.assertIn("verification.check_missing", error_codes(self._validate()))

        self._install_readback_checks()
        target = next(check for check in checks if check.get("assetId") == target_asset and check.get("type") == "key-properties")
        target["status"] = "pending"
        self._sync_bundle_binding()
        self.assertIn("verification.check_status", error_codes(self._validate()))

        target["status"] = "passed"
        target["artifactPath"] = "different-readback.json"
        self._sync_bundle_binding()
        self.assertIn("verification.artifact_path", error_codes(self._validate()))

        target["artifactPath"] = self.sources.readback_path.name
        target["assetId"] = "unknown-readback-asset"
        self._sync_bundle_binding()
        self.assertIn("verification.check_asset", error_codes(self._validate()))

    def test_readback_timestamp_must_follow_aware_bundle_completion(self) -> None:
        self.sources.readback["capturedAt"] = "2026-08-10T10:09:59+08:00"
        write_json(self.sources.readback_path, self.sources.readback)
        self.assertIn("time.readback_before_bundle", error_codes(self._validate()))

        self.sources.readback["capturedAt"] = "2026-08-10T10:11:00+08:00"
        self.sources.bundle["execution"].pop("completedAt")
        self._sync_bundle_binding()
        self.assertIn("time.required", error_codes(self._validate()))

        self.sources.bundle["execution"]["completedAt"] = "2026-08-10T10:10:00"
        self._sync_bundle_binding()
        self.assertIn("time.timezone", error_codes(self._validate()))

    def test_layout_links_reject_absolute_and_parent_traversal_paths(self) -> None:
        asset = self.sources.bundle["assets"][0]
        original_path = asset["layoutSpecPath"]
        absolute_path = str((self.sources.bundle_path.parent / original_path).resolve())

        asset["layoutSpecPath"] = absolute_path
        self._sync_bundle_binding()
        self.assertIn("layout.path_scope", error_codes(self._validate()))

        asset["layoutSpecPath"] = f"../{self.sources.bundle_path.parent.name}/{original_path}"
        self._sync_bundle_binding()
        self.assertIn("layout.path_scope", error_codes(self._validate()))


class ReuseReadbackV02ContractsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = load_json(READBACK_SCHEMA)
        self.bundle = self._bundle()
        self.readback = self._readback()

    @staticmethod
    def _slot() -> dict[str, Any]:
        return {
            "operation": "add-standard-slot",
            "standardName": "SlotContent",
            "classPath": "/Script/UMG.NamedSlot",
            "treeOrder": "last",
            "layout": {"mode": "fill", "anchors": [0, 0, 1, 1], "offsets": [0, 0, 0, 0], "alignment": [0, 0]},
            "legacyPreservedNames": ["Slot1"],
        }

    def _bundle(self) -> dict[str, Any]:
        shared = "/Game/UI/UMG/Widgets/uw_common_bag_item"
        child = "/Game/UI/UMG/Weapon/Widgets/uw_weapon_material_item"
        host = "/Game/UI/UMG/Weapon/Widgets/uw_weapon_material_slot_list"
        placement = {
            "hostNormalizedRect": [0, 0, 1, 1],
            "hostSize": [150, 150],
            "slot": {"containerType": "CanvasPanel", "horizontalAlignment": "Center", "verticalAlignment": "Center", "padding": [0, 0, 0, 0]},
            "zOrder": 1,
            "sizingStrategy": "fixed-host-rect",
            "childSizingCompatibility": {"mode": "host-equals-child-reference", "axes": ["horizontal", "vertical"], "sourceLayoutNodeIds": ["material-populated"]},
        }
        return {
            "version": "0.2",
            "assets": [
                {"id": "build.shared", "assetPath": shared, "representationKind": "reuse-only"},
                {"id": "build.child", "assetPath": child, "representationKind": "reuse-only"},
                {"id": "build.host", "assetPath": host, "representationKind": "layout-spec"},
            ],
            "reuseRelations": [
                {
                    "id": "reuse.extend",
                    "type": "shared-prototype-extension",
                    "sourceAssetId": "build.shared",
                    "sourceAssetPath": shared,
                    "targetAssetId": "build.shared",
                    "targetAssetPath": shared,
                    "namedSlot": self._slot(),
                    "registry": {"entryStatus": "active", "extensionSlotStatus": "verified"},
                    "activation": {"mode": "post-extension-activation", "status": "verified"},
                    "requirementRefs": ["ac.reuse"],
                },
                {
                    "id": "reuse.parent",
                    "type": "class-settings-parent-class",
                    "sourceAssetId": "build.shared",
                    "sourceAssetPath": shared,
                    "targetAssetId": "build.child",
                    "targetAssetPath": child,
                    "parentClassPath": f"{shared}.uw_common_bag_item_C",
                    "inheritedSlot": {"slotName": "SlotContent", "contentMode": "empty"},
                    "requirementRefs": ["ac.reuse"],
                },
                {
                    "id": "reuse.instance",
                    "type": "widget-tree-instance",
                    "sourceAssetId": "build.child",
                    "sourceAssetPath": child,
                    "targetAssetId": "build.host",
                    "targetAssetPath": host,
                    "host": {"widgetName": "ItemMaterial", "treePath": f"{host}.uw_weapon_material_slot_list:WidgetTree.ItemMaterial"},
                    "sharedPrototypeClassPath": f"{shared}.uw_common_bag_item_C",
                    "nestedWidgetClassPath": f"{child}.uw_weapon_material_item_C",
                    "parameterOverrides": [],
                    "placementContract": placement,
                    "requirementRefs": ["rt.material-common-item", "ac.reuse"],
                },
            ],
        }

    def _readback(self) -> dict[str, Any]:
        shared, child, host = [asset["assetPath"] for asset in self.bundle["assets"]]
        return {
            "version": "0.2",
            "readbackId": "readback:reuse-v02",
            "capturedAt": "2026-08-13T12:00:00+08:00",
            "capturedFrom": "unreal-editor",
            "acquisition": {"method": "nxue-agent", "fallbackReason": "Official MCP lacks the exact inherited NamedSlot read operation."},
            "status": "verified",
            "requirementBinding": {"requestId": "reuse-v02", "revision": 1, "approvedContentSha256": "a" * 64, "sha256": "b" * 64},
            "bundleBinding": {"bundleId": "bundle.reuse-v02", "sha256": "c" * 64},
            "assets": [
                {
                    "assetId": "build.shared", "assetPath": shared, "assetObjectPath": f"{shared}.uw_common_bag_item",
                    "assetClass": "/Script/UMGEditor.WidgetBlueprint", "generatedClassPath": f"{shared}.uw_common_bag_item_C",
                    "parentClassPath": "/Script/UIFramework.ListViewItem", "representationKind": "reuse-only", "status": "verified",
                    "widgets": [], "nodeMappings": [],
                },
                {
                    "assetId": "build.child", "assetPath": child, "assetObjectPath": f"{child}.uw_weapon_material_item",
                    "assetClass": "/Script/UMGEditor.WidgetBlueprint", "generatedClassPath": f"{child}.uw_weapon_material_item_C",
                    "parentClassPath": f"{shared}.uw_common_bag_item_C", "representationKind": "reuse-only", "status": "verified",
                    "widgets": [], "nodeMappings": [],
                },
                {
                    "assetId": "build.host", "assetPath": host, "assetObjectPath": f"{host}.uw_weapon_material_slot_list",
                    "assetClass": "/Script/UMGEditor.WidgetBlueprint", "generatedClassPath": f"{host}.uw_weapon_material_slot_list_C",
                    "parentClassPath": "/Script/UIFramework.ListViewItem", "representationKind": "layout-spec", "status": "verified",
                    "widgets": [
                        {"widgetName": "PanelMaterialPopulated", "classPath": "/Script/UMG.CanvasPanel", "parentWidgetName": "PanelRoot", "isVariable": True},
                        {"widgetName": "ItemMaterial", "classPath": f"{child}.uw_weapon_material_item_C", "parentWidgetName": "PanelMaterialPopulated", "isVariable": True},
                    ],
                    "nodeMappings": [{"nodeMappingId": "map.host.populated", "layoutNodeId": "material-populated", "widgetName": "PanelMaterialPopulated"}],
                },
            ],
            "reuseRelations": [
                {
                    "bundleRelationId": "reuse.extend", "type": "shared-prototype-extension", "status": "verified",
                    "sourceAssetId": "build.shared", "sourceAssetPath": shared, "targetAssetId": "build.shared", "targetAssetPath": shared,
                    "namedSlot": self._slot(), "runtimeFieldRefs": [],
                },
                {
                    "bundleRelationId": "reuse.parent", "type": "class-settings-parent-class", "status": "verified",
                    "sourceAssetId": "build.shared", "sourceAssetPath": shared, "targetAssetId": "build.child", "targetAssetPath": child,
                    "parentClassPath": f"{shared}.uw_common_bag_item_C", "inheritedSlot": {"slotName": "SlotContent", "contentMode": "empty"}, "runtimeFieldRefs": [],
                },
                {
                    "bundleRelationId": "reuse.instance", "type": "widget-tree-instance", "status": "verified",
                    "sourceAssetId": "build.child", "sourceAssetPath": child, "targetAssetId": "build.host", "targetAssetPath": host,
                    "host": {"widgetName": "ItemMaterial", "treePath": f"{host}.uw_weapon_material_slot_list:WidgetTree.ItemMaterial", "classPath": f"{child}.uw_weapon_material_item_C", "parentWidgetName": "PanelMaterialPopulated"},
                    "sharedPrototypeClassPath": f"{shared}.uw_common_bag_item_C", "nestedWidgetClassPath": f"{child}.uw_weapon_material_item_C",
                    "placement": {"hostNormalizedRect": [0, 0, 1, 1], "hostSize": [150, 150], "slot": {"classPath": "/Script/UMG.CanvasPanelSlot", "containerType": "CanvasPanel", "horizontalAlignment": "Center", "verticalAlignment": "Center", "padding": [0, 0, 0, 0]}, "zOrder": 1},
                    "runtimeFieldRefs": ["rt.material-common-item"],
                },
            ],
        }

    def _v03_bundle(self) -> dict[str, Any]:
        bundle = load_json_from_value(self.bundle)
        bundle["version"] = "0.3"
        extension = bundle["reuseRelations"][0]
        extension.pop("namedSlot")
        extension["namedSlots"] = {
            "operation": "migrate-existing-standard-slot",
            "legacyStandardMigration": {
                "operation": "rename-standard-slot",
                "oldName": "SlotContent",
                "newName": "SlotUp",
                "preSaveValidationRequired": True,
            },
            "slots": [
                {
                    "role": "down",
                    "standardName": "SlotDown",
                    "classPath": "/Script/UMG.NamedSlot",
                    "parentRelation": "shared-root-direct-child",
                    "treeOrder": "first",
                    "zOrderRelation": "strictly-lower-than-all-direct-siblings",
                    "layout": {"mode": "fill", "anchors": [0, 0, 1, 1], "offsets": [0, 0, 0, 0], "alignment": [0, 0]},
                    "isVariable": True,
                    "autoSize": False,
                    "visibility": "SelfHitTestInvisible",
                },
                {
                    "role": "up",
                    "standardName": "SlotUp",
                    "classPath": "/Script/UMG.NamedSlot",
                    "parentRelation": "shared-root-direct-child",
                    "treeOrder": "last",
                    "zOrderRelation": "strictly-higher-than-all-direct-siblings",
                    "layout": {"mode": "fill", "anchors": [0, 0, 1, 1], "offsets": [0, 0, 0, 0], "alignment": [0, 0]},
                    "isVariable": True,
                    "autoSize": False,
                    "visibility": "SelfHitTestInvisible",
                },
            ],
            "legacyPreservedNames": ["Slot1"],
        }
        extension["registry"] = {"entryStatus": "active", "extensionSlotsStatus": "verified"}
        parent = bundle["reuseRelations"][1]
        parent.pop("inheritedSlot")
        parent["inheritedSlots"] = [
            {"slotName": "SlotDown", "contentMode": "empty"},
            {"slotName": "SlotUp", "contentMode": "empty"},
        ]
        return bundle

    def _v03_readback(self, bundle: dict[str, Any] | None = None) -> dict[str, Any]:
        intended_bundle = bundle or self._v03_bundle()
        value = load_json_from_value(self.readback)
        value["version"] = "0.3"
        planned = intended_bundle["reuseRelations"][0]["namedSlots"]
        legacy_names = list(planned["legacyPreservedNames"])
        direct_names = ["SlotDown", "PanelItem", *legacy_names, "SlotUp"]
        shared_asset = value["assets"][0]
        shared_asset["widgets"] = [
            {"widgetName": "CanvasPanel_48", "classPath": "/Script/UMG.CanvasPanel", "parentWidgetName": None, "isVariable": False},
            {"widgetName": "SlotDown", "classPath": "/Script/UMG.NamedSlot", "parentWidgetName": "CanvasPanel_48", "isVariable": True, "visibility": "SelfHitTestInvisible"},
            {"widgetName": "PanelItem", "classPath": "/Script/UMG.CanvasPanel", "parentWidgetName": "CanvasPanel_48", "isVariable": True},
            *[
                {"widgetName": name, "classPath": "/Script/UMG.NamedSlot", "parentWidgetName": "CanvasPanel_48", "isVariable": True}
                for name in legacy_names
            ],
            {"widgetName": "SlotUp", "classPath": "/Script/UMG.NamedSlot", "parentWidgetName": "CanvasPanel_48", "isVariable": True, "visibility": "SelfHitTestInvisible"},
        ]
        extension = value["reuseRelations"][0]
        extension.pop("namedSlot")
        slots = load_json_from_value(planned["slots"])
        slots[0].update({"parentWidgetName": "CanvasPanel_48", "treeIndex": 1, "directSiblingIndex": 0, "zOrder": -1, "autoSize": False})
        up_sibling_index = len(direct_names) - 1
        slots[1].update({"parentWidgetName": "CanvasPanel_48", "treeIndex": up_sibling_index + 1, "directSiblingIndex": up_sibling_index, "zOrder": 1, "autoSize": False})
        extension["namedSlots"] = {
            "operation": planned["operation"],
            "slots": slots,
            "legacyPreservedNames": legacy_names,
            "directRootChildren": direct_names,
            "directRootLayers": [
                {
                    "name": name,
                    "treeIndex": index + 1,
                    "directSiblingIndex": index,
                    "zOrder": -1 if name == "SlotDown" else 1 if name == "SlotUp" else 0,
                    "parentWidgetName": "CanvasPanel_48",
                }
                for index, name in enumerate(direct_names)
            ],
        }
        if "legacyStandardMigration" in planned:
            extension["namedSlots"]["legacyStandardMigration"] = load_json_from_value(
                planned["legacyStandardMigration"]
            )
        parent = value["reuseRelations"][1]
        parent.pop("inheritedSlot")
        parent["inheritedSlots"] = load_json_from_value(intended_bundle["reuseRelations"][1]["inheritedSlots"])
        value["reuseRelations"][2]["parameterOverrides"] = load_json_from_value(
            intended_bundle["reuseRelations"][2]["parameterOverrides"]
        )
        return value

    def _relation_errors(self, readback: dict[str, Any] | None = None, bundle: dict[str, Any] | None = None) -> set[str]:
        value = readback or self.readback
        indexes = {"assets": {}, "widgets": {}, "mappings": {}}
        for asset in value["assets"]:
            indexes["assets"][asset["assetId"]] = asset
            for widget in asset["widgets"]:
                indexes["widgets"][(asset["assetId"], widget["widgetName"])] = widget
        errors: list[dict[str, str]] = []
        _validate_reuse_readback_relations(value, bundle or self.bundle, indexes=indexes, allowed_runtime_field_ids={"rt.material-common-item"}, errors=errors)
        return {error["code"] for error in errors}

    def test_v02_schema_accepts_empty_reuse_only_trees_and_closed_relations(self) -> None:
        self.assertEqual([], validate_schema_instance(self.readback, self.schema))
        self.assertEqual(set(), self._relation_errors())

    def test_v02_readback_covers_all_legal_named_slot_and_inherited_panel_shapes(self) -> None:
        renamed_bundle = load_json_from_value(self.bundle)
        renamed_readback = load_json_from_value(self.readback)
        renamed_slot = {
            "operation": "rename-legacy-slot",
            "oldName": "Slot1",
            "newName": "SlotContent",
            "classPath": "/Script/UMG.NamedSlot",
            "treeOrder": "last",
            "layout": {"mode": "fill", "anchors": [0, 0, 1, 1], "offsets": [0, 0, 0, 0], "alignment": [0, 0]},
        }
        renamed_bundle["reuseRelations"][0]["namedSlot"] = load_json_from_value(renamed_slot)
        renamed_readback["reuseRelations"][0]["namedSlot"] = load_json_from_value(renamed_slot)
        self.assertEqual([], validate_schema_instance(renamed_readback, self.schema))
        self.assertEqual(set(), self._relation_errors(renamed_readback, renamed_bundle))

        panel_bundle = load_json_from_value(self.bundle)
        panel_readback = load_json_from_value(self.readback)
        inherited_panel = {
            "slotName": "SlotContent",
            "contentMode": "panel",
            "panel": {
                "widgetName": "PanelExtra",
                "classPath": "/Script/UMG.CanvasPanel",
                "treePath": "SlotContent/PanelExtra",
                "layoutMode": "fill",
            },
        }
        panel_bundle["reuseRelations"][1]["inheritedSlot"] = load_json_from_value(inherited_panel)
        panel_readback["reuseRelations"][1]["inheritedSlot"] = load_json_from_value(inherited_panel)
        panel_readback["assets"][1]["widgets"].append(
            {"widgetName": "PanelExtra", "classPath": "/Script/UMG.CanvasPanel", "parentWidgetName": None, "isVariable": False}
        )
        self.assertEqual([], validate_schema_instance(panel_readback, self.schema))
        self.assertEqual(set(), self._relation_errors(panel_readback, panel_bundle))
        panel_readback["assets"][1]["widgets"].clear()
        self.assertIn("reuse.inherited_panel_missing", self._relation_errors(panel_readback, panel_bundle))

    def test_v03_schema_accepts_closed_dual_slot_actual_relations(self) -> None:
        bundle = self._v03_bundle()
        readback = self._v03_readback(bundle)
        self.assertEqual([], validate_schema_instance(readback, self.schema))
        self.assertEqual(set(), self._relation_errors(readback, bundle))

    def test_v03_widget_tree_instance_proves_fixed_flow_and_scroll_slot_shapes(self) -> None:
        host_path = self.bundle["assets"][2]["assetPath"]
        parent_name = "PanelMaterialPopulated"
        parent_tree_path = f"{host_path}.uw_weapon_material_slot_list:WidgetTree.{parent_name}"
        cases = (
            ("Overlay", "/Script/UMG.OverlaySlot", "/Script/UMG.Overlay", None, "Right", "Bottom", [2, 4, 6, 8]),
            ("Button", "/Script/UMG.ButtonSlot", "/Script/UMG.Button", None, "Fill", "Fill", [0, 0, 0, 0]),
            ("WrapBox", "/Script/UMG.WrapBoxSlot", "/Script/UMG.WrapBox", None, "Left", "Top", [1, 2, 3, 4]),
            ("ScaleBox", "/Script/UMG.ScaleBoxSlot", "/Script/UMG.ScaleBox", None, "Fill", "Fill", [0, 0, 0, 0]),
            ("HorizontalBox", "/Script/UMG.HorizontalBoxSlot", "/Script/UMG.HorizontalBox", {"rule": "Auto"}, "Fill", "Center", [8, 0, 4, 0]),
            ("VerticalBox", "/Script/UMG.VerticalBoxSlot", "/Script/UMG.VerticalBox", {"rule": "Fill", "weight": 2}, "Center", "Bottom", [0, 6, 0, 10]),
            ("GameScrollBox", "/Script/UMG.ScrollBoxSlot", "/Script/UIFramework.GameScrollBox", None, "Right", "Top", [8, 4, 8, 4]),
        )
        for container_type, class_path, parent_class_path, size, horizontal, vertical, padding in cases:
            with self.subTest(container_type=container_type):
                bundle = self._v03_bundle()
                readback = self._v03_readback(bundle)
                expected_slot = {
                    "containerType": container_type,
                    "parentWidgetName": parent_name,
                    "parentTreePath": parent_tree_path,
                    "horizontalAlignment": horizontal,
                    "verticalAlignment": vertical,
                    "padding": padding,
                }
                actual_slot = {"classPath": class_path, **load_json_from_value(expected_slot)}
                if size is not None:
                    expected_slot["size"] = load_json_from_value(size)
                    actual_slot["size"] = load_json_from_value(size)
                bundle["reuseRelations"][2]["placementContract"]["slot"] = expected_slot
                readback["reuseRelations"][2]["placement"]["slot"] = actual_slot
                parent_widget = next(
                    widget
                    for widget in readback["assets"][2]["widgets"]
                    if widget["widgetName"] == parent_name
                )
                parent_widget["classPath"] = parent_class_path

                self.assertEqual([], validate_schema_instance(readback, self.schema))
                self.assertEqual(set(), self._relation_errors(readback, bundle))

    def test_v03_flow_and_scroll_actual_slot_shapes_are_closed(self) -> None:
        bundle = self._v03_bundle()
        readback = self._v03_readback(bundle)
        actual_slot = readback["reuseRelations"][2]["placement"]["slot"]
        actual_slot.update(
            {
                "classPath": "/Script/UMG.HorizontalBoxSlot",
                "containerType": "HorizontalBox",
                "size": {"rule": "Auto"},
            }
        )
        self.assertEqual([], validate_schema_instance(readback, self.schema))

        missing_size = load_json_from_value(readback)
        missing_size["reuseRelations"][2]["placement"]["slot"].pop("size")
        self.assertTrue(validate_schema_instance(missing_size, self.schema))

        auto_with_weight = load_json_from_value(readback)
        auto_with_weight["reuseRelations"][2]["placement"]["slot"]["size"]["weight"] = 1
        self.assertTrue(validate_schema_instance(auto_with_weight, self.schema))

        fill_without_weight = load_json_from_value(readback)
        fill_without_weight["reuseRelations"][2]["placement"]["slot"]["size"] = {"rule": "Fill"}
        self.assertTrue(validate_schema_instance(fill_without_weight, self.schema))

        nonpositive_fill = load_json_from_value(readback)
        nonpositive_fill["reuseRelations"][2]["placement"]["slot"]["size"] = {"rule": "Fill", "weight": 0}
        self.assertTrue(validate_schema_instance(nonpositive_fill, self.schema))

        scroll_with_size = load_json_from_value(readback)
        scroll_slot = scroll_with_size["reuseRelations"][2]["placement"]["slot"]
        scroll_slot.update(
            {
                "classPath": "/Script/UMG.ScrollBoxSlot",
                "containerType": "GameScrollBox",
                "size": {"rule": "Auto"},
            }
        )
        self.assertTrue(validate_schema_instance(scroll_with_size, self.schema))

    def test_v03_actual_slot_size_class_and_parent_must_match_bundle_and_tree(self) -> None:
        host_path = self.bundle["assets"][2]["assetPath"]
        parent_name = "PanelMaterialPopulated"
        parent_tree_path = f"{host_path}.uw_weapon_material_slot_list:WidgetTree.{parent_name}"
        bundle = self._v03_bundle()
        expected_slot = {
            "containerType": "HorizontalBox",
            "parentWidgetName": parent_name,
            "parentTreePath": parent_tree_path,
            "horizontalAlignment": "Fill",
            "verticalAlignment": "Center",
            "size": {"rule": "Auto"},
            "padding": [8, 0, 4, 0],
        }
        bundle["reuseRelations"][2]["placementContract"]["slot"] = expected_slot
        readback = self._v03_readback(bundle)
        readback["reuseRelations"][2]["placement"]["slot"] = {
            "classPath": "/Script/UMG.HorizontalBoxSlot",
            **load_json_from_value(expected_slot),
        }
        parent_widget = next(
            widget
            for widget in readback["assets"][2]["widgets"]
            if widget["widgetName"] == parent_name
        )
        parent_widget["classPath"] = "/Script/UMG.HorizontalBox"
        self.assertEqual(set(), self._relation_errors(readback, bundle))

        wrong_size = load_json_from_value(readback)
        wrong_size["reuseRelations"][2]["placement"]["slot"]["size"] = {"rule": "Fill", "weight": 1}
        self.assertIn("reuse.placement_slot", self._relation_errors(wrong_size, bundle))

        wrong_class = load_json_from_value(readback)
        wrong_class["reuseRelations"][2]["placement"]["slot"]["classPath"] = "/Script/UMG.VerticalBoxSlot"
        self.assertIn("reuse.placement_slot_class", self._relation_errors(wrong_class, bundle))

        missing_parent_evidence = load_json_from_value(readback)
        missing_parent_evidence["reuseRelations"][2]["placement"]["slot"].pop("parentWidgetName")
        missing_parent_evidence["reuseRelations"][2]["placement"]["slot"].pop("parentTreePath")
        self.assertIn("reuse.placement_slot_parent", self._relation_errors(missing_parent_evidence, bundle))

        wrong_parent_tree = load_json_from_value(readback)
        wrong_parent_tree["reuseRelations"][2]["placement"]["slot"]["parentTreePath"] = (
            f"{host_path}.uw_weapon_material_slot_list:WidgetTree.PanelOther"
        )
        self.assertIn("reuse.placement_slot_parent", self._relation_errors(wrong_parent_tree, bundle))

        wrong_parent_class = load_json_from_value(readback)
        wrong_parent_class["assets"][2]["widgets"][0]["classPath"] = "/Script/UMG.CanvasPanel"
        self.assertIn("reuse.placement_slot_parent_class", self._relation_errors(wrong_parent_class, bundle))

    def test_v02_slot_readback_remains_closed_against_v03_size_evidence(self) -> None:
        mixed = load_json_from_value(self.readback)
        mixed["reuseRelations"][2]["placement"]["slot"]["size"] = {"rule": "Auto"}
        self.assertTrue(validate_schema_instance(mixed, self.schema))

    def test_v03_schema_accepts_direct_dual_slot_add_without_legacy_migration(self) -> None:
        bundle = self._v03_bundle()
        named_slots = bundle["reuseRelations"][0]["namedSlots"]
        named_slots["operation"] = "add-dual-layer-slots"
        named_slots.pop("legacyStandardMigration")
        named_slots["legacyPreservedNames"] = []
        readback = self._v03_readback(bundle)
        self.assertEqual([], validate_schema_instance(readback, self.schema))
        self.assertEqual(set(), self._relation_errors(readback, bundle))

        mixed = load_json_from_value(readback)
        mixed["reuseRelations"][0]["namedSlots"]["legacyStandardMigration"] = {
            "operation": "rename-standard-slot",
            "oldName": "SlotContent",
            "newName": "SlotUp",
            "preSaveValidationRequired": True,
        }
        self.assertTrue(validate_schema_instance(mixed, self.schema))

    def test_v03_dual_slots_require_actual_tree_and_strict_relative_z(self) -> None:
        bundle = self._v03_bundle()

        incomplete_snapshot = self._v03_readback(bundle)
        incomplete_snapshot["assets"][0]["widgets"].insert(
            4,
            {"widgetName": "PanelUnreported", "classPath": "/Script/UMG.CanvasPanel", "parentWidgetName": "CanvasPanel_48", "isVariable": False},
        )
        self.assertIn("reuse.layer_coverage", self._relation_errors(incomplete_snapshot, bundle))

        wrong_tree = self._v03_readback(bundle)
        named_slots = wrong_tree["reuseRelations"][0]["namedSlots"]
        named_slots["directRootChildren"][:2] = ["PanelItem", "SlotDown"]
        named_slots["directRootLayers"][0]["directSiblingIndex"] = 1
        named_slots["directRootLayers"][1]["directSiblingIndex"] = 0
        named_slots["slots"][0]["directSiblingIndex"] = 1
        self.assertIn("reuse.slot_tree_order", self._relation_errors(wrong_tree, bundle))

        down_not_strict = self._v03_readback(bundle)
        named_slots = down_not_strict["reuseRelations"][0]["namedSlots"]
        named_slots["slots"][0]["zOrder"] = 0
        named_slots["directRootLayers"][0]["zOrder"] = 0
        self.assertIn("reuse.slot_z_order", self._relation_errors(down_not_strict, bundle))

        up_not_strict = self._v03_readback(bundle)
        named_slots = up_not_strict["reuseRelations"][0]["namedSlots"]
        named_slots["slots"][1]["zOrder"] = 0
        named_slots["directRootLayers"][3]["zOrder"] = 0
        self.assertIn("reuse.slot_z_order", self._relation_errors(up_not_strict, bundle))

        wrong_visibility = self._v03_readback(bundle)
        wrong_visibility["assets"][0]["widgets"][1]["visibility"] = "Visible"
        self.assertIn("reuse.slot_widget_identity", self._relation_errors(wrong_visibility, bundle))

    def test_v03_dual_slots_require_legacy_slot_and_completed_standard_rename(self) -> None:
        bundle = self._v03_bundle()

        missing_legacy = self._v03_readback(bundle)
        missing_legacy["assets"][0]["widgets"] = [
            widget for widget in missing_legacy["assets"][0]["widgets"] if widget["widgetName"] != "Slot1"
        ]
        self.assertIn("reuse.legacy_slot_missing", self._relation_errors(missing_legacy, bundle))

        old_standard_remains = self._v03_readback(bundle)
        old_standard_remains["assets"][0]["widgets"].append(
            {"widgetName": "SlotContent", "classPath": "/Script/UMG.NamedSlot", "parentWidgetName": "CanvasPanel_48", "isVariable": True}
        )
        self.assertIn("reuse.old_standard_slot_present", self._relation_errors(old_standard_remains, bundle))

    def test_v03_inherited_slots_match_bundle_and_panel_exists_in_actual_child(self) -> None:
        bundle = self._v03_bundle()
        readback = self._v03_readback(bundle)
        readback["reuseRelations"][1]["inheritedSlots"][1] = {
            "slotName": "SlotUp",
            "contentMode": "panel",
            "panel": {
                "widgetName": "PanelOverlay",
                "classPath": "/Script/UMG.CanvasPanel",
                "treePath": "SlotUp/PanelOverlay",
                "directChildRole": "semantic-panel",
                "directChildCount": 1,
                "layoutMode": "fill",
                "layout": {"mode": "fill", "anchors": [0, 0, 1, 1], "offsets": [0, 0, 0, 0], "alignment": [0, 0]},
            },
        }
        self.assertIn("reuse.inherited_slot", self._relation_errors(readback, bundle))

        bundle["reuseRelations"][1]["inheritedSlots"] = load_json_from_value(
            readback["reuseRelations"][1]["inheritedSlots"]
        )
        self.assertIn("reuse.inherited_panel_missing", self._relation_errors(readback, bundle))
        readback["assets"][1]["widgets"].append(
            {"widgetName": "PanelOverlay", "classPath": "/Script/UMG.CanvasPanel", "parentWidgetName": None, "isVariable": False}
        )
        self.assertNotIn("reuse.inherited_panel_missing", self._relation_errors(readback, bundle))

        readback["assets"][1]["widgets"].append(
            {"widgetName": "PanelUnrelated", "classPath": "/Script/UMG.CanvasPanel", "parentWidgetName": None, "isVariable": False}
        )
        self.assertIn("reuse.inherited_panel_roots", self._relation_errors(readback, bundle))

    def test_v03_empty_inherited_slots_forbid_child_owned_widgets(self) -> None:
        bundle = self._v03_bundle()
        readback = self._v03_readback(bundle)
        readback["assets"][1]["widgets"].append(
            {"widgetName": "PanelUnexpected", "classPath": "/Script/UMG.CanvasPanel", "parentWidgetName": None, "isVariable": False}
        )
        self.assertIn("reuse.inherited_unexpected_widgets", self._relation_errors(readback, bundle))

    def test_v03_nested_parameter_overrides_match_bundle(self) -> None:
        bundle = self._v03_bundle()
        readback = self._v03_readback(bundle)
        bundle["reuseRelations"][2]["parameterOverrides"] = [
            {"name": "Quality", "valueSource": "literal", "value": "Rare"}
        ]
        self.assertIn("reuse.parameter_overrides", self._relation_errors(readback, bundle))
        readback["reuseRelations"][2]["parameterOverrides"] = load_json_from_value(
            bundle["reuseRelations"][2]["parameterOverrides"]
        )
        self.assertNotIn("reuse.parameter_overrides", self._relation_errors(readback, bundle))

    def test_v03_cross_system_folder_exception_uses_plural_activation_contract(self) -> None:
        bundle = self._v03_bundle()
        self.assertEqual({"/Game/UI/UMG/Widgets/uw_common_bag_item"}, verified_shared_prototype_paths(bundle))
        bundle["assets"][0]["representationKind"] = "layout-spec"
        self.assertEqual({"/Game/UI/UMG/Widgets/uw_common_bag_item"}, verified_shared_prototype_paths(bundle))
        bundle["assets"][0]["representationKind"] = "unsupported"
        self.assertEqual(set(), verified_shared_prototype_paths(bundle))
        bundle["assets"][0]["representationKind"] = "layout-spec"
        bundle["reuseRelations"][0]["activation"]["status"] = "required"
        self.assertEqual(set(), verified_shared_prototype_paths(bundle))

    def test_runtime_field_can_resolve_complete_exclusive_branch_mirror(self) -> None:
        requirement = {
            "uiModel": {
                "elements": [
                    {
                        "id": "el.label.off",
                        "kind": "text",
                        "familyId": "fam.entry",
                        "runtimeControlled": True,
                        "inBuildScope": True,
                        "claimIds": ["claim.state"],
                        "properties": {
                            "isVariable": True,
                            "widgetClass": "/Script/UMG.TextBlock",
                        },
                    },
                    {
                        "id": "el.label.on",
                        "kind": "text",
                        "familyId": "fam.entry",
                        "runtimeControlled": True,
                        "inBuildScope": True,
                        "claimIds": ["claim.state"],
                        "properties": {
                            "isVariable": True,
                            "widgetClass": "/Script/UMG.TextBlock",
                        },
                    },
                ]
            },
            "stateModels": [
                {
                    "id": "sm.selection",
                    "claimIds": ["claim.state"],
                    "axes": [
                        {
                            "id": "axis.selection",
                            "exclusive": True,
                            "claimIds": ["claim.state"],
                            "states": [
                                {"id": "st.off", "inBuildScope": True, "claimIds": ["claim.state"]},
                                {"id": "st.on", "inBuildScope": True, "claimIds": ["claim.state"]},
                            ],
                        }
                    ],
                    "implementation": {
                        "strategy": "exclusive-panel-branches",
                        "axisId": "axis.selection",
                        "branches": [
                            {"stateId": "st.off", "completeElementIds": ["el.label.off"]},
                            {"stateId": "st.on", "completeElementIds": ["el.label.on"]},
                        ],
                    },
                }
            ]
        }
        field = {"id": "rt.label", "elementId": "el.label.off", "valueKind": "text"}
        mappings = [
            {
                "id": "map.label.off",
                "assetId": "build.entry",
                "mappingKind": "composite-state",
                "requirementRefs": ["rt.label", "el.label.off"],
                "stateRefs": ["st.off"],
            },
            {
                "id": "map.label.on",
                "assetId": "build.entry",
                "mappingKind": "composite-state",
                "requirementRefs": ["rt.label", "el.label.on"],
                "stateRefs": ["st.on"],
            },
        ]
        accepted = {"claim.state"}
        self.assertEqual(
            ["map.label.off", "map.label.on"],
            [
                mapping["id"]
                for mapping in runtime_field_node_mappings(
                    field, mappings, requirement, accepted
                )
            ],
        )

        partial = load_json_from_value(mappings[:1])
        partial.append(
            {
                "id": "map.label.off.duplicate",
                "assetId": "build.entry",
                "mappingKind": "composite-state",
                "requirementRefs": ["rt.label", "el.label.off"],
                "stateRefs": ["st.off"],
            }
        )
        self.assertEqual([], runtime_field_node_mappings(field, partial, requirement, accepted))

        cross_asset = load_json_from_value(mappings)
        cross_asset[1]["assetId"] = "build.other"
        self.assertEqual([], runtime_field_node_mappings(field, cross_asset, requirement, accepted))

        nonexclusive = load_json_from_value(requirement)
        nonexclusive["stateModels"][0]["axes"][0]["exclusive"] = False
        self.assertEqual([], runtime_field_node_mappings(field, mappings, nonexclusive, accepted))

        missing_branch_trace = load_json_from_value(mappings)
        missing_branch_trace[1]["requirementRefs"] = ["rt.label", "el.unrelated"]
        self.assertEqual([], runtime_field_node_mappings(field, missing_branch_trace, requirement, accepted))

        wrong_kind = load_json_from_value(requirement)
        wrong_kind["uiModel"]["elements"][1]["kind"] = "container"
        wrong_kind["uiModel"]["elements"][1]["properties"]["widgetClass"] = "/Script/UMG.CanvasPanel"
        self.assertEqual([], runtime_field_node_mappings(field, mappings, wrong_kind, accepted))

        wrong_family = load_json_from_value(requirement)
        wrong_family["uiModel"]["elements"][1]["familyId"] = "fam.other"
        self.assertEqual([], runtime_field_node_mappings(field, mappings, wrong_family, accepted))

        source_outside_axis = load_json_from_value(requirement)
        source_outside_axis["uiModel"]["elements"].append(
            {
                "id": "el.label.outside",
                "kind": "text",
                "familyId": "fam.entry",
                "runtimeControlled": True,
                "inBuildScope": True,
                "claimIds": ["claim.state"],
                "properties": {
                    "isVariable": True,
                    "widgetClass": "/Script/UMG.TextBlock",
                },
            }
        )
        outside_field = load_json_from_value(field)
        outside_field["elementId"] = "el.label.outside"
        self.assertEqual(
            [],
            runtime_field_node_mappings(
                outside_field, mappings, source_outside_axis, accepted
            ),
        )

        extra_element_ref = load_json_from_value(mappings)
        extra_element_ref[1]["requirementRefs"].append("el.label.off")
        self.assertEqual(
            [],
            runtime_field_node_mappings(
                field, extra_element_ref, requirement, accepted
            ),
        )

        missing_signature = load_json_from_value(requirement)
        missing_signature["uiModel"]["elements"][0]["familyId"] = None
        missing_signature["uiModel"]["elements"][1]["familyId"] = None
        self.assertEqual(
            [],
            runtime_field_node_mappings(
                field, mappings, missing_signature, accepted
            ),
        )

    def test_verified_post_activation_still_requires_active_verified_registry(self) -> None:
        bundle = self._v03_bundle()
        extension = bundle["reuseRelations"][0]
        extension["registry"].update(
            {"entryStatus": "candidate", "extensionSlotsStatus": "required-before-activation"}
        )
        self.assertEqual(set(), verified_shared_prototype_paths(bundle))

    def test_v01_shape_remains_closed_against_v02_fields(self) -> None:
        mixed = load_json(READBACK_SCHEMA.parent / "fixtures" / "minimal-unreal-widget-readback.json")
        mixed["reuseRelations"] = []
        self.assertTrue(validate_schema_instance(mixed, self.schema))

    def test_relation_coverage_parent_host_class_and_placement_are_strict(self) -> None:
        missing = dict(self.readback)
        missing["reuseRelations"] = self.readback["reuseRelations"][:-1]
        self.assertIn("reuse.coverage", self._relation_errors(missing))

        wrong_parent = load_json_from_value(self.readback)
        wrong_parent["reuseRelations"][1]["parentClassPath"] = "/Game/UI/UMG/Bad.Bad_C"
        self.assertIn("reuse.parent_class", self._relation_errors(wrong_parent))

        wrong_host = load_json_from_value(self.readback)
        wrong_host["reuseRelations"][2]["host"]["classPath"] = "/Game/UI/UMG/Bad.Bad_C"
        self.assertIn("reuse.host_class", self._relation_errors(wrong_host))

        wrong_placement = load_json_from_value(self.readback)
        wrong_placement["reuseRelations"][2]["placement"]["hostSize"] = [149, 150]
        self.assertIn("reuse.placement", self._relation_errors(wrong_placement))

        tolerant_rect = load_json_from_value(self.readback)
        tolerant_rect["reuseRelations"][2]["placement"]["hostNormalizedRect"][0] += 0.0005
        self.assertNotIn("reuse.placement", self._relation_errors(tolerant_rect))

        wrong_slot_class = load_json_from_value(self.readback)
        wrong_slot_class["reuseRelations"][2]["placement"]["slot"]["classPath"] = "/Script/UMG.OverlaySlot"
        self.assertIn("reuse.placement_slot_class", self._relation_errors(wrong_slot_class))

    def test_runtime_field_must_be_accepted_and_assigned_by_bundle_relation(self) -> None:
        unknown = load_json_from_value(self.readback)
        unknown["reuseRelations"][2]["runtimeFieldRefs"] = ["rt.unknown"]
        codes = self._relation_errors(unknown)
        self.assertIn("reuse.runtime_field_unknown", codes)
        self.assertIn("reuse.runtime_field_intent", codes)

    def test_cross_system_folder_exception_requires_verified_shared_extension(self) -> None:
        shared = verified_shared_prototype_paths(self.bundle)
        self.assertEqual({"/Game/UI/UMG/Widgets/uw_common_bag_item"}, shared)
        errors: list[dict[str, str]] = []
        validate_system_folder_with_shared_prototypes(
            [asset["assetPath"] for asset in self.bundle["assets"]], expected_system_folder="Weapon", shared_paths=shared, errors=errors, path="$.assets"
        )
        self.assertEqual([], errors)

        unverified = load_json_from_value(self.bundle)
        unverified["reuseRelations"][0]["activation"]["status"] = "required"
        errors = []
        validate_system_folder_with_shared_prototypes(
            [asset["assetPath"] for asset in unverified["assets"]], expected_system_folder="Weapon",
            shared_paths=verified_shared_prototype_paths(unverified), errors=errors, path="$.assets"
        )
        self.assertTrue(errors)

        legacy_errors: list[dict[str, str]] = []
        self.assertIsNone(system_folder_for_paths(["/Game/UI/UMG/Widgets/uw_common_bag_item", "/Game/UI/UMG/Weapon/umg_weapon"], legacy_errors, "$.assets"))
        self.assertTrue(legacy_errors)

    def test_full_validator_routes_bundle_v02_to_readback_v02_and_runtime_relation(self) -> None:
        requirement = {
            "requestId": "reuse-v02",
            "revision": 1,
            "reviewGate": {"approvedContentSha256": "a" * 64},
            "uiModel": {
                "elements": [{"id": "el.material-common-item", "runtimeControlled": True, "inBuildScope": True, "claimIds": ["claim.reuse"]}],
                "runtimeFields": [{"id": "rt.material-common-item", "elementId": "el.material-common-item", "inBuildScope": True, "claimIds": ["claim.reuse"]}],
                "collections": [],
            },
            "stateModels": [],
        }
        bundle = load_json_from_value(self.bundle)
        bundle.update({
            "bundleId": "bundle.reuse-v02",
            "nodeMappings": [{"id": "map.host.populated", "assetId": "build.host", "layoutNodeId": "material-populated"}],
            "execution": {"completedAt": "2026-08-13T11:59:00+08:00"},
        })
        layout_nodes = {
            "build.host": {
                "nodes": {
                    "material-root": {"name": "PanelRoot", "parent": None},
                    "material-populated": {"name": "PanelMaterialPopulated", "parent": "material-root", "isVariable": True},
                }
            }
        }
        import shutil

        test_root = self.sources_root_for_v02_test()
        if test_root.exists():
            shutil.rmtree(test_root)
        test_root.mkdir(parents=True)
        try:
            root = test_root
            requirement_path = root / "requirement.json"
            bundle_path = root / "bundle.json"
            readback_path = root / "readback.json"
            write_json(requirement_path, requirement)
            write_json(bundle_path, bundle)
            value = load_json_from_value(self.readback)
            value["requirementBinding"] = {"requestId": "reuse-v02", "revision": 1, "approvedContentSha256": "a" * 64, "sha256": sha256_file(requirement_path)}
            value["bundleBinding"] = {"bundleId": bundle["bundleId"], "sha256": sha256_file(bundle_path)}
            write_json(readback_path, value)
            with patch("validate_unreal_widget_readback.validate_requirement_and_bundle", return_value=([], {"acceptedClaimIds": {"claim.reuse"}})), patch(
                "validate_unreal_widget_readback.validate_readback_verification_checks"
            ), patch("validate_unreal_widget_readback.load_layouts", return_value=layout_nodes):
                report = validate_unreal_widget_readback(
                    value,
                    self.schema,
                    readback_path=readback_path,
                    requirement=requirement,
                    requirement_path=requirement_path,
                    bundle=bundle,
                    bundle_path=bundle_path,
                )
                self.assertTrue(report["valid"], report["errors"])

                wrong_version = load_json_from_value(value)
                wrong_version["version"] = "0.1"
                report = validate_unreal_widget_readback(
                    wrong_version,
                    self.schema,
                    readback_path=readback_path,
                    requirement=requirement,
                    requirement_path=requirement_path,
                    bundle=bundle,
                    bundle_path=bundle_path,
                )
                self.assertIn("version.bundle_readback", error_codes(report))
        finally:
            if test_root.exists():
                shutil.rmtree(test_root)

    def test_full_validator_routes_bundle_v03_to_readback_v03(self) -> None:
        requirement = {
            "requestId": "reuse-v03",
            "revision": 2,
            "reviewGate": {"approvedContentSha256": "d" * 64},
            "uiModel": {
                "elements": [{"id": "el.material-common-item", "runtimeControlled": True, "inBuildScope": True, "claimIds": ["claim.reuse"]}],
                "runtimeFields": [{"id": "rt.material-common-item", "elementId": "el.material-common-item", "inBuildScope": True, "claimIds": ["claim.reuse"]}],
                "collections": [],
            },
            "stateModels": [],
        }
        bundle = self._v03_bundle()
        bundle.update({
            "bundleId": "bundle.reuse-v03",
            "nodeMappings": [{"id": "map.host.populated", "assetId": "build.host", "layoutNodeId": "material-populated"}],
            "execution": {"completedAt": "2026-08-13T11:59:00+08:00"},
        })
        layout_nodes = {
            "build.host": {
                "nodes": {
                    "material-root": {"name": "PanelRoot", "parent": None},
                    "material-populated": {"name": "PanelMaterialPopulated", "parent": "material-root", "isVariable": True},
                }
            }
        }
        import shutil

        test_root = self.sources_root_for_v03_test()
        if test_root.exists():
            shutil.rmtree(test_root)
        test_root.mkdir(parents=True)
        try:
            requirement_path = test_root / "requirement.json"
            bundle_path = test_root / "bundle.json"
            readback_path = test_root / "readback.json"
            write_json(requirement_path, requirement)
            write_json(bundle_path, bundle)
            value = self._v03_readback(bundle)
            value["requirementBinding"] = {
                "requestId": "reuse-v03",
                "revision": 2,
                "approvedContentSha256": "d" * 64,
                "sha256": sha256_file(requirement_path),
            }
            value["bundleBinding"] = {"bundleId": bundle["bundleId"], "sha256": sha256_file(bundle_path)}
            write_json(readback_path, value)
            with patch("validate_unreal_widget_readback.validate_requirement_and_bundle", return_value=([], {"acceptedClaimIds": {"claim.reuse"}})), patch(
                "validate_unreal_widget_readback.validate_readback_verification_checks"
            ), patch("validate_unreal_widget_readback.load_layouts", return_value=layout_nodes):
                report = validate_unreal_widget_readback(
                    value,
                    self.schema,
                    readback_path=readback_path,
                    requirement=requirement,
                    requirement_path=requirement_path,
                    bundle=bundle,
                    bundle_path=bundle_path,
                )
                self.assertTrue(report["valid"], report["errors"])

                wrong_version = load_json_from_value(value)
                wrong_version["version"] = "0.2"
                report = validate_unreal_widget_readback(
                    wrong_version,
                    self.schema,
                    readback_path=readback_path,
                    requirement=requirement,
                    requirement_path=requirement_path,
                    bundle=bundle,
                    bundle_path=bundle_path,
                )
                self.assertIn("version.bundle_readback", error_codes(report))
        finally:
            if test_root.exists():
                shutil.rmtree(test_root)

    @staticmethod
    def sources_root_for_v02_test() -> Path:
        return READBACK_SCHEMA.parents[5] / "Saved" / "CodexUITests" / "document-nextgame-umg-v02-readback"

    @staticmethod
    def sources_root_for_v03_test() -> Path:
        return READBACK_SCHEMA.parents[5] / "Saved" / "CodexUITests" / "document-nextgame-umg-v03-readback"


def load_json_from_value(value: Any) -> Any:
    """Deep-copy JSON-compatible test values without introducing another dependency."""

    import json

    return json.loads(json.dumps(value))


if __name__ == "__main__":
    unittest.main()
