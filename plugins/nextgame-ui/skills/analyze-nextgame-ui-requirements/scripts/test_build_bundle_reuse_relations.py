#!/usr/bin/env python3
"""Regression tests for closed legacy and dual-slot UIBuildBundle reuse chains."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path

from _contract_common import ASSETS_ROOT, compute_approved_content_sha256, load_json, sha256_file, validate_schema_instance
from validate_build_bundle import (
    DEFAULT_SCHEMA,
    PLUGIN_ROOT,
    _is_allowed_registry_source,
    _validate_named_slots_against_registry_entry,
    _validate_parameter_overrides_against_registry_entry,
    _validate_shared_registry_binding,
    validate_build_bundle,
)
from validate_shared_widget_registry import compute_reuse_contract_sha256


def error_codes(validation: dict) -> set[str]:
    return {error["code"] for error in validation["errors"]}


@contextmanager
def registry_temporary_directory():
    test_root = PLUGIN_ROOT.parent.parent / "Saved" / "CodexUITestTemp"
    test_root.mkdir(parents=True, exist_ok=True)
    path = test_root / f"registry-test-{uuid.uuid4().hex}"
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path)


class BuildBundleReuseRelationsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = load_json(DEFAULT_SCHEMA)
        self.requirement = load_json(ASSETS_ROOT / "example-composite-tabs-requirement.json")
        self.requirement["reviewGate"]["approvedContentSha256"] = compute_approved_content_sha256(self.requirement)
        self.bundle_path = ASSETS_ROOT / "example-composite-tabs-build-bundle.json"
        legacy = load_json(self.bundle_path)
        legacy["requirement"]["approvedContentSha256"] = self.requirement["reviewGate"]["approvedContentSha256"]
        self.legacy = legacy
        self.bundle = self._make_v02_bundle(legacy)
        self.bundle_v03 = self._make_v03_bundle(self.bundle)

    def _make_v02_bundle(self, legacy: dict) -> dict:
        bundle = copy.deepcopy(legacy)
        bundle["version"] = "0.2"
        prototype = bundle["assets"][0]
        child = bundle["assets"][1]
        prototype.update(
            {
                "id": "build-shared-prototype",
                "assetPath": "/Game/UI/UMG/Widgets/uw_common_bag_item",
                "buildOrder": 0,
                "dependsOnAssetIds": [],
                "representationKind": "reuse-only",
                "layoutSpecPath": None,
                "layoutSpecSha256": None,
            }
        )
        child.update(
            {
                "id": "build-system-child",
                "assetPath": "/Game/UI/UMG/Weapon/Widgets/uw_weapon_material_item",
                "assetKind": "child-widget",
                "buildOrder": 1,
                "dependsOnAssetIds": [prototype["id"]],
                "representationKind": "reuse-only",
                "layoutSpecPath": None,
                "layoutSpecSha256": None,
            }
        )
        host = copy.deepcopy(child)
        host.update(
            {
                "id": "build-host-entry",
                "assetPath": "/Game/UI/UMG/Weapon/Widgets/uw_weapon_material_slot_list",
                "assetKind": "list-entry",
                "buildOrder": 2,
                "dependsOnAssetIds": [child["id"]],
                "representationKind": "layout-spec",
            }
        )
        bundle["assets"] = [prototype, child, host]
        bundle["nodeMappings"] = []
        # Schema requires one mapping; semantic validation is intentionally exercised with linked files disabled.
        bundle["nodeMappings"] = [
            {
                "id": "mapping-reuse-root",
                "assetId": prototype["id"],
                "layoutNodeId": "node-child-root",
                "mappingKind": "generated-support",
                "requirementRefs": ["element-tab-template-button"],
                "claimIds": ["claim-tab-family"],
                "stateRefs": [],
            }
        ]
        bundle["crossAssetOperations"] = []
        bundle["execution"]["buildOrderAssetIds"] = [prototype["id"], child["id"], host["id"]]
        for check in bundle["verification"]["checks"]:
            if check.get("assetId"):
                check["assetId"] = prototype["id"]
        bundle["reuseRelations"] = [
            {
                "id": "reuse-extend-shared",
                "type": "shared-prototype-extension",
                "sourceAssetId": prototype["id"],
                "sourceAssetPath": prototype["assetPath"],
                "targetAssetId": prototype["id"],
                "targetAssetPath": prototype["assetPath"],
                "namedSlot": {
                    "operation": "add-standard-slot",
                    "standardName": "SlotContent",
                    "classPath": "/Script/UMG.NamedSlot",
                    "treeOrder": "last",
                    "layout": {"mode": "fill", "anchors": [0, 0, 1, 1], "offsets": [0, 0, 0, 0], "alignment": [0, 0]},
                    "legacyPreservedNames": ["Slot1"],
                },
                "authorization": {
                    "status": "accepted",
                    "actorType": "user",
                    "source": "direct-user-message",
                    "evidenceRef": "requirement.reviewGate",
                },
                "registry": {
                    "registryPath": "shared-widget-registry.json",
                    "registrySha256": "1" * 64,
                    "registryId": "nextgame-shared-widgets",
                    "registryRevision": 4,
                    "entryId": "shared.common.bag-item",
                    "entryStatus": "candidate",
                    "extensionSlotStatus": "required-before-activation",
                    "interfaceSha256": "2" * 64,
                    "reuseContractSha256": "3" * 64,
                },
                "activation": {
                    "mode": "post-extension-activation",
                    "status": "required",
                    "resultingEntryStatus": "active",
                    "resultingExtensionSlotStatus": "verified",
                    "verificationCheckIds": [bundle["verification"]["checks"][0]["id"]],
                },
                "requirementRefs": ["element-tab-template-button"],
                "claimIds": ["claim-tab-family"],
            },
            {
                "id": "reuse-parent-child",
                "type": "class-settings-parent-class",
                "sourceAssetId": prototype["id"],
                "sourceAssetPath": prototype["assetPath"],
                "targetAssetId": child["id"],
                "targetAssetPath": child["assetPath"],
                "parentClassPath": "/Game/UI/UMG/Widgets/uw_common_bag_item.uw_common_bag_item_C",
                "inheritedSlot": {"slotName": "SlotContent", "contentMode": "empty"},
                "requirementRefs": ["element-tab-template-button"],
                "claimIds": ["claim-tab-family"],
            },
            {
                "id": "reuse-nest-child",
                "type": "widget-tree-instance",
                "sourceAssetId": child["id"],
                "sourceAssetPath": child["assetPath"],
                "targetAssetId": host["id"],
                "targetAssetPath": host["assetPath"],
                "host": {
                    "widgetName": "ItemMaterial",
                    "treePath": "/Game/UI/UMG/Weapon/Widgets/uw_weapon_material_slot_list.uw_weapon_material_slot_list:WidgetTree.ItemMaterial",
                },
                "sharedPrototypeClassPath": "/Game/UI/UMG/Widgets/uw_common_bag_item.uw_common_bag_item_C",
                "nestedWidgetClassPath": "/Game/UI/UMG/Weapon/Widgets/uw_weapon_material_item.uw_weapon_material_item_C",
                "parameterOverrides": [],
                "placementContract": {
                    "hostNormalizedRect": [0, 0, 1, 1],
                    "hostSize": [2560, 1440],
                    "slot": {"containerType": "CanvasPanel", "horizontalAlignment": "Fill", "verticalAlignment": "Fill", "padding": [0, 0, 0, 0]},
                    "zOrder": 0,
                    "sizingStrategy": "fill-host",
                    "childSizingCompatibility": {
                        "mode": "inherited-reuse-only-full-stretch",
                        "axes": ["horizontal", "vertical"],
                        "parentRelationId": "reuse-parent-child",
                        "prototypeExtensionRelationId": "reuse-extend-shared",
                    },
                },
                "requirementRefs": ["element-tab-template-button"],
                "claimIds": ["claim-tab-family"],
            },
        ]
        return bundle

    def _make_v03_bundle(self, legacy_v02: dict) -> dict:
        bundle = copy.deepcopy(legacy_v02)
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
        extension["registry"].update(
            {
                "registryVersion": "0.4",
                "extensionSlotsStatus": extension["registry"].pop("extensionSlotStatus"),
            }
        )
        extension["activation"]["resultingExtensionSlotsStatus"] = extension["activation"].pop(
            "resultingExtensionSlotStatus"
        )
        parent = bundle["reuseRelations"][1]
        parent.pop("inheritedSlot")
        parent["inheritedSlots"] = [
            {"slotName": "SlotDown", "contentMode": "empty"},
            {"slotName": "SlotUp", "contentMode": "empty"},
        ]
        return bundle

    def _make_parameterized_registry_entry(self) -> dict:
        registry = load_json(PLUGIN_ROOT / "assets" / "shared-widget-registry.json")
        entry = copy.deepcopy(registry["entries"][0])
        nested_mode = next(mode for mode in entry["generationModes"] if mode["mode"] == "widget-tree-instance")
        nested_mode.update(
            {
                "status": "verified",
                "parameterContractStatus": "verified",
                "instanceParameters": [
                    {
                        "name": "AccentVisible",
                        "valueKind": "boolean",
                        "required": True,
                        "description": "Controls the optional accent layer.",
                    },
                    {
                        "name": "Caption",
                        "valueKind": "text",
                        "required": False,
                        "defaultValue": "Default",
                        "description": "Optional caption text.",
                    },
                ],
            }
        )
        return entry

    def validate(self, bundle: dict) -> dict:
        return validate_build_bundle(
            bundle,
            self.schema,
            bundle_path=self.bundle_path,
            requirement_spec=copy.deepcopy(self.requirement),
            requirement_path=ASSETS_ROOT / "example-composite-tabs-requirement.json",
            check_linked_files=False,
        )

    def _set_final_lifecycle(
        self,
        bundle: dict,
        *,
        execution_completed: bool,
        verification_passed: bool,
    ) -> None:
        if execution_completed:
            bundle["execution"].update(
                {
                    "status": "completed",
                    "startedAt": "2026-08-14T10:00:00+08:00",
                    "completedAt": "2026-08-14T10:01:00+08:00",
                }
            )
        if verification_passed:
            bundle["verification"]["status"] = "passed"
        if execution_completed or verification_passed:
            for asset in bundle["assets"]:
                asset["status"] = "verified"
            for check in bundle["verification"]["checks"]:
                check["status"] = "passed"

    def validate_named_slot(self, named_slot: dict) -> list[dict[str, str]]:
        return validate_schema_instance(
            named_slot,
            self.schema["$defs"]["namedSlotExtension"],
            root_schema=self.schema,
            path="$.namedSlot",
        )

    def validate_named_slots_v03(self, named_slots: dict) -> list[dict[str, str]]:
        return validate_schema_instance(
            named_slots,
            self.schema["$defs"]["dualNamedSlotExtension"],
            root_schema=self.schema,
            path="$.namedSlots",
        )

    def validate_placement_slot(self, placement_slot: dict) -> list[dict[str, str]]:
        return validate_schema_instance(
            placement_slot,
            self.schema["$defs"]["placementSlot"],
            root_schema=self.schema,
            path="$.placementSlot",
        )

    def validate_child_sizing_compatibility(self, compatibility: dict) -> list[dict[str, str]]:
        return validate_schema_instance(
            compatibility,
            self.schema["$defs"]["childSizingCompatibility"],
            root_schema=self.schema,
            path="$.childSizingCompatibility",
        )

    def test_legacy_v01_remains_schema_valid(self) -> None:
        self.assertFalse(error_codes(self.validate(copy.deepcopy(self.legacy))) & {"schema.one_of", "bundle.version"})

    def test_v02_closed_reuse_chain_has_no_reuse_errors(self) -> None:
        codes = error_codes(self.validate(copy.deepcopy(self.bundle)))
        self.assertFalse({code for code in codes if code.startswith("reuse.")}, codes)
        self.assertEqual([], self.validate_named_slot(self.bundle["reuseRelations"][0]["namedSlot"]))

    def test_widget_tree_instance_host_path_is_bound_to_target_asset_and_widget_name(self) -> None:
        bundle = copy.deepcopy(self.bundle_v03)
        bundle["reuseRelations"][2]["host"]["treePath"] = "/Game/UI/UMG/Other.uw_other:WidgetTree.ItemMaterial"
        self.assertIn("reuse.instance_host_path", error_codes(self.validate(bundle)))

    def test_relation_placement_slot_supports_auto_flow_and_game_scroll_contracts(self) -> None:
        flow_slot = {
            "containerType": "HorizontalBox",
            "parentWidgetName": "HorItems",
            "parentTreePath": "/Game/UI/UMG/Weapon/Widgets/uw_host.uw_host:WidgetTree.HorItems",
            "size": {"rule": "Auto"},
            "horizontalAlignment": "Fill",
            "verticalAlignment": "Fill",
            "padding": [0, 0, 0, 0],
        }
        scroll_slot = {
            "containerType": "GameScrollBox",
            "parentWidgetName": "ScrollItems",
            "parentTreePath": "/Game/UI/UMG/Weapon/Widgets/uw_host.uw_host:WidgetTree.ScrollItems",
            "horizontalAlignment": "Right",
            "verticalAlignment": "Top",
            "padding": [8, 4, 8, 4],
        }
        self.assertEqual([], self.validate_placement_slot(flow_slot))
        self.assertEqual([], self.validate_placement_slot(scroll_slot))

    def test_reuse_only_flow_slot_supports_horizontal_auto_and_vertical_weighted_allocation(self) -> None:
        for container_type, allocation, size, horizontal_alignment, vertical_alignment, padding in (
            ("HorizontalBox", "content-driven", {"rule": "Auto"}, "Fill", "Center", [8, 0, 4, 0]),
            ("VerticalBox", "weighted-remaining-space", {"rule": "Fill", "weight": 2}, "Center", "Bottom", [0, 6, 0, 10]),
        ):
            with self.subTest(container_type=container_type, allocation=allocation):
                bundle = copy.deepcopy(self.bundle_v03)
                placement = bundle["reuseRelations"][2]["placementContract"]
                placement["slot"] = {
                    "containerType": container_type,
                    "horizontalAlignment": horizontal_alignment,
                    "verticalAlignment": vertical_alignment,
                    "size": size,
                    "padding": padding,
                }
                placement["sizingStrategy"] = allocation
                placement["childSizingCompatibility"] = {
                    "mode": "inherited-reuse-only-flow-slot",
                    "axes": ["horizontal", "vertical"],
                    "allocation": allocation,
                    "parentRelationId": "reuse-parent-child",
                    "prototypeExtensionRelationId": "reuse-extend-shared",
                }

                codes = error_codes(self.validate(bundle))
                self.assertFalse({code for code in codes if code.startswith("reuse.")}, codes)
                self.assertEqual([], self.validate_child_sizing_compatibility(placement["childSizingCompatibility"]))

    def test_reuse_only_flow_slot_rejects_scroll_and_allocation_mismatches(self) -> None:
        cases = (
            ("GameScrollBox", "content-driven", {"rule": "Auto"}, "content-driven", "reuse.instance_sizing_flow_container"),
            ("HorizontalBox", "content-driven", {"rule": "Fill", "weight": 1}, "content-driven", "reuse.instance_sizing_flow_allocation"),
            ("VerticalBox", "weighted-remaining-space", {"rule": "Fill", "weight": 1}, "fill-host", "reuse.instance_sizing_flow_allocation"),
        )
        for container_type, allocation, size, sizing_strategy, expected_code in cases:
            with self.subTest(container_type=container_type, allocation=allocation, sizing_strategy=sizing_strategy):
                bundle = copy.deepcopy(self.bundle_v03)
                placement = bundle["reuseRelations"][2]["placementContract"]
                placement["slot"] = {
                    "containerType": container_type,
                    "horizontalAlignment": "Fill",
                    "verticalAlignment": "Fill",
                    "size": size,
                    "padding": [0, 0, 0, 0],
                }
                placement["sizingStrategy"] = sizing_strategy
                placement["childSizingCompatibility"] = {
                    "mode": "inherited-reuse-only-flow-slot",
                    "axes": ["horizontal", "vertical"],
                    "allocation": allocation,
                    "parentRelationId": "reuse-parent-child",
                    "prototypeExtensionRelationId": "reuse-extend-shared",
                }
                self.assertIn(expected_code, error_codes(self.validate(bundle)))

    def test_reuse_only_flow_slot_contract_is_closed(self) -> None:
        bundle = copy.deepcopy(self.bundle_v03)
        placement = bundle["reuseRelations"][2]["placementContract"]
        placement["slot"].update({"containerType": "HorizontalBox", "size": {"rule": "Auto"}})
        placement["sizingStrategy"] = "content-driven"
        placement["childSizingCompatibility"] = {
            "mode": "inherited-reuse-only-flow-slot",
            "axes": ["horizontal", "vertical"],
            "allocation": "auto-ish",
            "parentRelationId": "reuse-parent-child",
            "prototypeExtensionRelationId": "reuse-extend-shared",
        }
        self.assertIn(
            "schema.one_of",
            {error["code"] for error in self.validate_child_sizing_compatibility(placement["childSizingCompatibility"])},
        )

    def test_reuse_only_scroll_slot_supports_reviewed_alignment_and_padding(self) -> None:
        bundle = copy.deepcopy(self.bundle_v03)
        placement = bundle["reuseRelations"][2]["placementContract"]
        placement["slot"] = {
            "containerType": "GameScrollBox",
            "horizontalAlignment": "Right",
            "verticalAlignment": "Top",
            "padding": [4, 6, 8, 10],
        }
        placement["sizingStrategy"] = "scroll-slot"
        placement["childSizingCompatibility"] = {
            "mode": "inherited-reuse-only-scroll-slot",
            "axes": ["horizontal", "vertical"],
            "parentRelationId": "reuse-parent-child",
            "prototypeExtensionRelationId": "reuse-extend-shared",
        }

        codes = error_codes(self.validate(bundle))
        self.assertFalse({code for code in codes if code.startswith("reuse.")}, codes)
        self.assertEqual([], self.validate_child_sizing_compatibility(placement["childSizingCompatibility"]))

    def test_reuse_only_scroll_slot_rejects_flow_container_or_box_size_contract(self) -> None:
        cases = (
            ("HorizontalBox", "scroll-slot", False, "reuse.instance_sizing_scroll_container"),
            ("GameScrollBox", "content-driven", False, "reuse.instance_sizing_scroll_contract"),
            ("GameScrollBox", "scroll-slot", True, "reuse.instance_sizing_scroll_contract"),
        )
        for container_type, sizing_strategy, add_size, expected_code in cases:
            with self.subTest(container_type=container_type, sizing_strategy=sizing_strategy, add_size=add_size):
                bundle = copy.deepcopy(self.bundle_v03)
                placement = bundle["reuseRelations"][2]["placementContract"]
                placement["slot"] = {
                    "containerType": container_type,
                    "horizontalAlignment": "Center",
                    "verticalAlignment": "Bottom",
                    "padding": [1, 2, 3, 4],
                }
                if add_size:
                    placement["slot"]["size"] = {"rule": "Auto"}
                placement["sizingStrategy"] = sizing_strategy
                placement["childSizingCompatibility"] = {
                    "mode": "inherited-reuse-only-scroll-slot",
                    "axes": ["horizontal", "vertical"],
                    "parentRelationId": "reuse-parent-child",
                    "prototypeExtensionRelationId": "reuse-extend-shared",
                }
                self.assertIn(expected_code, error_codes(self.validate(bundle)))

    def test_reuse_only_instance_rejects_fabricated_layout_node_evidence(self) -> None:
        bundle = copy.deepcopy(self.bundle_v03)
        bundle["reuseRelations"][2]["placementContract"]["childSizingCompatibility"] = {
            "mode": "source-root-stretch",
            "axes": ["horizontal", "vertical"],
            "sourceLayoutNodeIds": ["fabricated-child-root"],
        }
        self.assertIn("reuse.instance_sizing_layout_evidence", error_codes(self.validate(bundle)))

    def test_inherited_sizing_rejects_layout_node_ids_in_schema(self) -> None:
        bundle = copy.deepcopy(self.bundle_v03)
        bundle["reuseRelations"][2]["placementContract"]["childSizingCompatibility"]["sourceLayoutNodeIds"] = ["fabricated-child-root"]
        self.assertIn("schema.one_of", error_codes(self.validate(bundle)))

    def test_inherited_sizing_binds_unique_parent_relation(self) -> None:
        bundle = copy.deepcopy(self.bundle_v03)
        bundle["reuseRelations"][2]["placementContract"]["childSizingCompatibility"]["parentRelationId"] = "reuse-wrong-parent"
        self.assertIn("reuse.instance_sizing_parent_relation", error_codes(self.validate(bundle)))

    def test_inherited_sizing_binds_unique_prototype_extension(self) -> None:
        bundle = copy.deepcopy(self.bundle_v03)
        bundle["reuseRelations"][2]["placementContract"]["childSizingCompatibility"]["prototypeExtensionRelationId"] = "reuse-wrong-extension"
        self.assertIn("reuse.instance_sizing_extension_relation", error_codes(self.validate(bundle)))

    def test_inherited_sizing_requires_empty_inherited_slots(self) -> None:
        bundle = copy.deepcopy(self.bundle_v03)
        bundle["reuseRelations"][1]["inheritedSlots"][1] = {
            "slotName": "SlotUp",
            "contentMode": "panel",
            "panel": {
                "widgetName": "PanelOwned",
                "classPath": "/Script/UMG.Overlay",
                "treePath": "/Game/UI/UMG/Weapon/Widgets/uw_weapon_material_item.uw_weapon_material_item:WidgetTree.PanelOwned",
                "layoutMode": "fill",
                "layout": {"mode": "fill", "anchors": [0, 0, 1, 1], "offsets": [0, 0, 0, 0], "alignment": [0, 0]},
            },
        }
        self.assertIn("reuse.instance_sizing_owned_tree", error_codes(self.validate(bundle)))

    def test_inherited_sizing_requires_full_host_slot(self) -> None:
        bundle = copy.deepcopy(self.bundle_v03)
        bundle["reuseRelations"][2]["placementContract"]["slot"]["horizontalAlignment"] = "Center"
        self.assertIn("reuse.instance_sizing_host_fill", error_codes(self.validate(bundle)))

    def test_v02_rejects_parameterized_nesting_that_readback_v02_cannot_prove(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        bundle["reuseRelations"][2]["parameterOverrides"] = [
            {"name": "AccentVisible", "valueSource": "literal", "value": True}
        ]
        self.assertIn("reuse.parameter_overrides_version", error_codes(self.validate(bundle)))

    def test_v03_parameterized_nesting_cannot_bypass_actual_registry_with_skip(self) -> None:
        bundle = copy.deepcopy(self.bundle_v03)
        bundle["reuseRelations"][2]["parameterOverrides"] = [
            {"name": "AccentVisible", "valueSource": "literal", "value": True}
        ]
        self.assertNotIn("reuse.parameter_overrides_version", error_codes(self.validate(bundle)))
        self.assertIn("reuse.parameter_registry_binding", error_codes(self.validate(bundle)))

    def test_v03_pending_candidate_allows_only_empty_planned_instance(self) -> None:
        bundle = copy.deepcopy(self.bundle_v03)
        codes = error_codes(self.validate(bundle))
        self.assertNotIn("reuse.parameter_registry_binding", codes)
        self.assertNotIn("reuse.parameter_generation_mode", codes)
        self.assertNotIn("reuse.parameter_contract_unverified", codes)

        bundle["reuseRelations"][2]["parameterOverrides"] = [
            {"name": "AccentVisible", "valueSource": "literal", "value": True}
        ]
        self.assertIn("reuse.parameter_registry_binding", error_codes(self.validate(bundle)))

    def test_v03_pending_candidate_blocks_every_transitive_consumer(self) -> None:
        for consumer_index in (1, 2):
            with self.subTest(consumer_index=consumer_index):
                bundle = copy.deepcopy(self.bundle_v03)
                bundle["assets"][consumer_index]["status"] = "built"
                codes = error_codes(self.validate(bundle))
                self.assertIn("reuse.activation_premature_consumer", codes)
                self.assertIn("reuse.parameter_registry_binding", codes)

    def test_unverified_registry_mode_is_allowed_only_for_empty_plan(self) -> None:
        entry = self._make_parameterized_registry_entry()
        nested_mode = next(mode for mode in entry["generationModes"] if mode["mode"] == "widget-tree-instance")
        nested_mode.update(
            {
                "status": "unverified",
                "parameterContractStatus": "unverified",
                "instanceParameters": [],
            }
        )

        strict_codes = {
            error["code"]
            for error in _validate_parameter_overrides_against_registry_entry(
                [],
                entry,
                path="$.parameterOverrides",
            )
        }
        self.assertIn("reuse.parameter_generation_mode", strict_codes)
        self.assertIn("reuse.parameter_contract_unverified", strict_codes)

        planned_errors = _validate_parameter_overrides_against_registry_entry(
            [],
            entry,
            path="$.parameterOverrides",
            allow_unverified_empty_plan=True,
        )
        self.assertEqual([], planned_errors)

        override_codes = {
            error["code"]
            for error in _validate_parameter_overrides_against_registry_entry(
                [{"name": "AccentVisible", "valueSource": "literal", "value": True}],
                entry,
                path="$.parameterOverrides",
                allow_unverified_empty_plan=True,
            )
        }
        self.assertIn("reuse.parameter_generation_mode", override_codes)
        self.assertIn("reuse.parameter_contract_unverified", override_codes)

    def test_linked_candidate_unverified_contract_allows_pending_empty_plan(self) -> None:
        with registry_temporary_directory() as task_root:
            registry = load_json(PLUGIN_ROOT / "assets" / "shared-widget-registry.json")
            entry = registry["entries"][0]
            nested_mode = next(mode for mode in entry["generationModes"] if mode["mode"] == "widget-tree-instance")
            nested_mode.update(
                {
                    "status": "unverified",
                    "parameterContractStatus": "unverified",
                    "instanceParameters": [],
                }
            )
            entry["knownConsumers"] = []
            entry["reuseContractSha256"] = compute_reuse_contract_sha256(entry)
            registry["entries"] = [entry]
            registry_bytes = (json.dumps(registry, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            registry_sha256 = hashlib.sha256(registry_bytes).hexdigest()
            registry_path = task_root / "registry-snapshots" / f"shared-widget-registry.{registry_sha256}.json"
            registry_path.parent.mkdir()
            registry_path.write_bytes(registry_bytes)

            bundle = copy.deepcopy(self.bundle_v03)
            extension = bundle["reuseRelations"][0]
            extension["registry"].update(
                {
                    "registryPath": f"registry-snapshots/{registry_path.name}",
                    "registrySha256": registry_sha256,
                    "registryId": registry["registryId"],
                    "registryVersion": registry["version"],
                    "registryRevision": registry["registryRevision"],
                    "entryId": entry["id"],
                    "entryStatus": entry["status"],
                    "extensionSlotsStatus": entry["extensionSlotsContract"]["status"],
                    "interfaceSha256": entry["interfaceSha256"],
                    "reuseContractSha256": entry["reuseContractSha256"],
                }
            )
            validation = validate_build_bundle(
                bundle,
                self.schema,
                bundle_path=task_root / "ui-build-bundle.json",
                requirement_spec=copy.deepcopy(self.requirement),
                requirement_path=ASSETS_ROOT / "example-composite-tabs-requirement.json",
                check_linked_files=True,
            )
            codes = error_codes(validation)
            self.assertNotIn("reuse.parameter_generation_mode", codes)
            self.assertNotIn("reuse.parameter_contract_unverified", codes)
            self.assertNotIn("reuse.parameter_registry_binding", codes, validation["errors"])

    def test_v03_parameter_contract_accepts_unique_declared_typed_overrides(self) -> None:
        entry = self._make_parameterized_registry_entry()
        errors = _validate_parameter_overrides_against_registry_entry(
            [{"name": "AccentVisible", "valueSource": "literal", "value": True}],
            entry,
            path="$.parameterOverrides",
        )
        self.assertEqual([], errors)

    def test_v03_required_parameter_may_use_registry_default(self) -> None:
        entry = self._make_parameterized_registry_entry()
        nested_mode = next(mode for mode in entry["generationModes"] if mode["mode"] == "widget-tree-instance")
        nested_mode["instanceParameters"] = [
            {
                "name": "AccentVisible",
                "valueKind": "boolean",
                "required": True,
                "defaultValue": False,
                "description": "Defaults the optional accent off.",
            }
        ]
        self.assertEqual(
            [],
            _validate_parameter_overrides_against_registry_entry([], entry, path="$.parameterOverrides"),
        )

    def test_v03_parameter_contract_rejects_duplicate_unknown_wrong_type_and_missing_required(self) -> None:
        entry = self._make_parameterized_registry_entry()
        errors = _validate_parameter_overrides_against_registry_entry(
            [
                {"name": "Caption", "valueSource": "literal", "value": 7},
                {"name": "Caption", "valueSource": "literal", "value": "ok"},
                {"name": "Undeclared", "valueSource": "literal", "value": True},
            ],
            entry,
            path="$.parameterOverrides",
        )
        codes = {error["code"] for error in errors}
        self.assertIn("reuse.parameter_override_duplicate", codes)
        self.assertIn("reuse.parameter_override_unknown", codes)
        self.assertIn("reuse.parameter_override_type", codes)
        self.assertIn("reuse.parameter_override_required", codes)

    def test_v03_parameterless_registry_rejects_nonempty_overrides(self) -> None:
        registry = load_json(PLUGIN_ROOT / "assets" / "shared-widget-registry.json")
        errors = _validate_parameter_overrides_against_registry_entry(
            [{"name": "AccentVisible", "valueSource": "literal", "value": True}],
            registry["entries"][0],
            path="$.parameterOverrides",
        )
        self.assertIn("reuse.parameter_contract_none", {error["code"] for error in errors})

    def test_v03_closed_dual_slot_chain_has_no_reuse_errors(self) -> None:
        codes = error_codes(self.validate(copy.deepcopy(self.bundle_v03)))
        self.assertFalse({code for code in codes if code.startswith("reuse.")}, codes)
        self.assertEqual([], self.validate_named_slots_v03(self.bundle_v03["reuseRelations"][0]["namedSlots"]))

    def test_v03_new_shared_widget_adds_dual_slots_without_migration_or_legacy_names(self) -> None:
        bundle = copy.deepcopy(self.bundle_v03)
        named_slots = bundle["reuseRelations"][0]["namedSlots"]
        named_slots["operation"] = "add-dual-layer-slots"
        named_slots.pop("legacyStandardMigration")
        named_slots["legacyPreservedNames"] = []
        codes = error_codes(self.validate(bundle))
        self.assertFalse({code for code in codes if code.startswith("reuse.slot") or code.startswith("reuse.slots")}, codes)
        self.assertEqual([], self.validate_named_slots_v03(named_slots))

    def test_v03_add_dual_slots_rejects_migration_fields(self) -> None:
        bundle = copy.deepcopy(self.bundle_v03)
        named_slots = bundle["reuseRelations"][0]["namedSlots"]
        named_slots["operation"] = "add-dual-layer-slots"
        named_slots["legacyPreservedNames"] = []
        self.assertTrue(self.validate_named_slots_v03(named_slots))

    def test_v03_new_shared_widget_add_branch_rejects_legacy_names(self) -> None:
        bundle = copy.deepcopy(self.bundle_v03)
        named_slots = bundle["reuseRelations"][0]["namedSlots"]
        named_slots["operation"] = "add-dual-layer-slots"
        named_slots.pop("legacyStandardMigration")
        named_slots["legacyPreservedNames"] = ["Slot1"]
        self.assertTrue(self.validate_named_slots_v03(named_slots))

    def test_v03_migration_allows_no_legacy_names_for_other_existing_widgets(self) -> None:
        bundle = copy.deepcopy(self.bundle_v03)
        named_slots = bundle["reuseRelations"][0]["namedSlots"]
        named_slots["legacyPreservedNames"] = []
        self.assertEqual([], self.validate_named_slots_v03(named_slots))

    def test_v03_current_bag_migration_binds_slot1_to_actual_registry(self) -> None:
        registry = load_json(PLUGIN_ROOT / "assets" / "shared-widget-registry.json")
        named_slots = copy.deepcopy(self.bundle_v03["reuseRelations"][0]["namedSlots"])
        named_slots["legacyPreservedNames"] = []
        errors = _validate_named_slots_against_registry_entry(
            named_slots,
            registry["entries"][0],
            path="$.namedSlots",
        )
        codes = {error["code"] for error in errors}
        self.assertIn("reuse.slot_legacy_registry", codes)
        self.assertIn("reuse.slot_legacy_migration", codes)

    def test_v03_migration_rename_facts_must_match_actual_registry(self) -> None:
        registry = load_json(PLUGIN_ROOT / "assets" / "shared-widget-registry.json")
        entry = copy.deepcopy(registry["entries"][0])
        entry["extensionSlotMigration"]["oldStandardName"] = "LegacyContent"
        errors = _validate_named_slots_against_registry_entry(
            copy.deepcopy(self.bundle_v03["reuseRelations"][0]["namedSlots"]),
            entry,
            path="$.namedSlots",
        )
        self.assertIn("reuse.slot_migration_registry", {error["code"] for error in errors})

    def test_v03_rejects_obsolete_dual_slot_operation_name(self) -> None:
        bundle = copy.deepcopy(self.bundle_v03)
        named_slots = bundle["reuseRelations"][0]["namedSlots"]
        named_slots["operation"] = "migrate-to-dual-layer-slots"
        self.assertTrue(self.validate_named_slots_v03(named_slots))

    def test_v03_can_use_both_layer_slots_with_one_direct_semantic_panel_each(self) -> None:
        bundle = copy.deepcopy(self.bundle_v03)
        bundle["assets"][1]["representationKind"] = "layout-spec"
        bundle["assets"][1]["layoutSpecPath"] = "child.json"
        bundle["assets"][1]["layoutSpecSha256"] = "a" * 64
        bundle["reuseRelations"][1]["inheritedSlots"] = [
            {
                "slotName": "SlotDown",
                "contentMode": "panel",
                "panel": {
                    "widgetName": "PanelUnderlay",
                    "classPath": "/Script/UMG.CanvasPanel",
                    "treePath": "SlotDown/PanelUnderlay",
                    "directChildRole": "semantic-panel",
                    "directChildCount": 1,
                    "layoutMode": "fill",
                    "layout": {"mode": "fill", "anchors": [0, 0, 1, 1], "offsets": [0, 0, 0, 0], "alignment": [0, 0]},
                },
            },
            {
                "slotName": "SlotUp",
                "contentMode": "panel",
                "panel": {
                    "widgetName": "PanelOverlay",
                    "classPath": "/Script/UMG.Overlay",
                    "treePath": "SlotUp/PanelOverlay",
                    "directChildRole": "semantic-panel",
                    "directChildCount": 1,
                    "layoutMode": "fill",
                    "layout": {"mode": "fill", "anchors": [0, 0, 1, 1], "offsets": [0, 0, 0, 0], "alignment": [0, 0]},
                },
            },
        ]
        codes = error_codes(self.validate(bundle))
        self.assertFalse({code for code in codes if code.startswith("reuse.slot_panel")}, codes)

    def test_v03_rejects_non_panel_and_non_fill_default_slot_content(self) -> None:
        bundle = copy.deepcopy(self.bundle_v03)
        bundle["reuseRelations"][1]["inheritedSlots"][1] = {
            "slotName": "SlotUp",
            "contentMode": "panel",
            "panel": {
                "widgetName": "TxtWrong",
                "classPath": "/Script/UMG.TextBlock",
                "treePath": "SlotUp/TxtWrong",
                "directChildRole": "semantic-panel",
                "directChildCount": 1,
                "layoutMode": "fill",
                "layout": {"mode": "fill", "anchors": [0, 0, 1, 1], "offsets": [1, 0, 0, 0], "alignment": [0, 0]},
            },
        }
        codes = error_codes(self.validate(bundle))
        self.assertIn("reuse.slot_panel_class", codes)
        self.assertIn("reuse.slot_panel_fill", codes)

    def test_v03_special_slot_adaptation_requires_evidence(self) -> None:
        bundle = copy.deepcopy(self.bundle_v03)
        bundle["reuseRelations"][1]["inheritedSlots"][0] = {
            "slotName": "SlotDown",
            "contentMode": "panel",
            "panel": {
                "widgetName": "PanelSpecial",
                "classPath": "/Script/UMG.CanvasPanel",
                "treePath": "SlotDown/PanelSpecial",
                "directChildRole": "semantic-panel",
                "directChildCount": 1,
                "layoutMode": "special-adaptation",
            },
        }
        self.assertIn("reuse.slot_panel_adaptation_evidence", error_codes(self.validate(bundle)))

    def test_v03_rejects_legacy_single_slot_shape(self) -> None:
        bundle = copy.deepcopy(self.bundle_v03)
        bundle["reuseRelations"][0]["namedSlot"] = copy.deepcopy(self.bundle["reuseRelations"][0]["namedSlot"])
        bundle["reuseRelations"][0].pop("namedSlots")
        self.assertIn("schema.one_of", error_codes(self.validate(bundle)))

    def test_v03_extension_slots_require_passive_visibility(self) -> None:
        bundle = copy.deepcopy(self.bundle_v03)
        bundle["reuseRelations"][0]["namedSlots"]["slots"][0]["visibility"] = "Visible"
        self.assertIn("schema.one_of", error_codes(self.validate(bundle)))

    def test_v03_extension_slots_keep_strict_order_z_fill_autosize_and_variable_contract(self) -> None:
        mutations = (
            (0, "treeOrder", "last"),
            (0, "zOrderRelation", "strictly-higher-than-all-direct-siblings"),
            (0, "autoSize", True),
            (1, "isVariable", False),
        )
        for slot_index, field, value in mutations:
            with self.subTest(slot_index=slot_index, field=field):
                bundle = copy.deepcopy(self.bundle_v03)
                bundle["reuseRelations"][0]["namedSlots"]["slots"][slot_index][field] = value
                self.assertIn("schema.one_of", error_codes(self.validate(bundle)))
        bundle = copy.deepcopy(self.bundle_v03)
        bundle["reuseRelations"][0]["namedSlots"]["slots"][1]["layout"]["offsets"] = [0, 0, 1, 0]
        self.assertIn("schema.one_of", error_codes(self.validate(bundle)))

    def test_v03_registry_binding_uses_actual_validated_snapshot(self) -> None:
        with registry_temporary_directory() as task_root:
            bundle = copy.deepcopy(self.bundle_v03)
            relation = bundle["reuseRelations"][0]
            registry = load_json(PLUGIN_ROOT / "assets" / "shared-widget-registry.json")
            entry = copy.deepcopy(registry["entries"][0])
            registry["entries"] = [entry]
            registry_bytes = (
                json.dumps(registry, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
            ).encode("utf-8")
            registry_sha256 = hashlib.sha256(registry_bytes).hexdigest()
            registry_path = (
                task_root
                / "registry-snapshots"
                / f"shared-widget-registry.{registry_sha256}.json"
            )
            registry_path.parent.mkdir()
            registry_path.write_bytes(registry_bytes)
            relation["registry"].update(
                {
                    "registryPath": f"registry-snapshots/{registry_path.name}",
                    "registrySha256": registry_sha256,
                    "registryId": registry["registryId"],
                    "registryVersion": registry["version"],
                    "registryRevision": registry["registryRevision"],
                    "entryId": entry["id"],
                    "entryStatus": entry["status"],
                    "extensionSlotsStatus": entry["extensionSlotsContract"]["status"],
                    "interfaceSha256": entry["interfaceSha256"],
                    "reuseContractSha256": entry["reuseContractSha256"],
                }
            )
            source = bundle["assets"][0]
            bundle_path = task_root / "ui-build-bundle.json"

            def codes() -> set[str]:
                return {
                    error["code"]
                    for error in _validate_shared_registry_binding(
                        relation["registry"],
                        source_asset=source,
                        bundle_version="0.3",
                        bundle_path=bundle_path,
                        path="$.reuseRelations[0].registry",
                    )
                }

            self.assertEqual(set(), codes())
            registry_path.write_bytes(registry_bytes + b"\n")
            self.assertIn("reuse.registry_sha256", codes())
            registry_path.write_bytes(registry_bytes)
            relation["registry"]["entryStatus"] = "active"
            self.assertIn("reuse.registry_entry_identity", codes())

    def test_v03_registry_binding_rejects_arbitrary_existing_json_path(self) -> None:
        registry_path = PLUGIN_ROOT / "assets" / "shared-widget-registry.json"
        registry = load_json(registry_path)
        entry = registry["entries"][0]
        binding = {
            "registryPath": "unused",
            "registrySha256": sha256_file(registry_path),
            "registryId": registry["registryId"],
            "registryVersion": registry["version"],
            "registryRevision": registry["registryRevision"],
            "entryId": entry["id"],
            "entryStatus": entry["status"],
            "extensionSlotsStatus": entry["extensionSlotsContract"]["status"],
            "interfaceSha256": entry["interfaceSha256"],
            "reuseContractSha256": entry["reuseContractSha256"],
        }
        binding["registryPath"] = str(PLUGIN_ROOT / "assets" / "shared-widget-registry.schema.json")
        errors = _validate_shared_registry_binding(
            binding,
            source_asset=self.bundle_v03["assets"][0],
            bundle_version="0.3",
            bundle_path=self.bundle_path,
            path="$.registry",
        )
        self.assertIn("reuse.registry_authority", {error["code"] for error in errors})

    def test_content_addressed_bundle_local_registry_snapshot_is_the_only_snapshot_shape(self) -> None:
        digest = "a" * 64
        snapshot = self.bundle_path.parent / "registry-snapshots" / f"shared-widget-registry.{digest}.json"
        sibling = self.bundle_path.parent / f"shared-widget-registry.{digest}.json"
        self.assertTrue(_is_allowed_registry_source(snapshot, bundle_path=self.bundle_path, declared_sha256=digest))
        self.assertFalse(_is_allowed_registry_source(sibling, bundle_path=self.bundle_path, declared_sha256=digest))
        self.assertFalse(_is_allowed_registry_source(snapshot, bundle_path=self.bundle_path, declared_sha256="b" * 64))

    def test_v03_verified_activation_cannot_self_report_candidate_as_active(self) -> None:
        bundle = copy.deepcopy(self.bundle_v03)
        extension = bundle["reuseRelations"][0]
        extension["registry"].update({"entryStatus": "active", "extensionSlotsStatus": "verified"})
        extension["activation"] = {
            "mode": "post-extension-activation",
            "status": "verified",
            "resultingEntryStatus": "active",
            "resultingExtensionSlotsStatus": "verified",
            "verificationCheckIds": [bundle["verification"]["checks"][0]["id"]],
            "evidenceArtifactPath": "self-reported.json",
            "evidenceArtifactSha256": "a" * 64,
        }
        bundle["verification"]["checks"][0]["status"] = "passed"
        self.assertIn("reuse.activation_registry_binding", error_codes(self.validate(bundle)))

    def test_v02_and_v03_final_lifecycle_rejects_candidate_required_activation(self) -> None:
        lifecycle_states = (
            (True, False),
            (False, True),
            (True, True),
        )
        for source_bundle in (self.bundle, self.bundle_v03):
            for execution_completed, verification_passed in lifecycle_states:
                with self.subTest(
                    version=source_bundle["version"],
                    execution_completed=execution_completed,
                    verification_passed=verification_passed,
                ):
                    bundle = copy.deepcopy(source_bundle)
                    self._set_final_lifecycle(
                        bundle,
                        execution_completed=execution_completed,
                        verification_passed=verification_passed,
                    )
                    self.assertIn("reuse.activation_lifecycle", error_codes(self.validate(bundle)))

    def test_v02_and_v03_preverified_activation_allows_completed_passed_lifecycle(self) -> None:
        for source_bundle in (self.bundle, self.bundle_v03):
            with self.subTest(version=source_bundle["version"]):
                bundle = copy.deepcopy(source_bundle)
                extension = bundle["reuseRelations"][0]
                if bundle["version"] == "0.2":
                    extension["registry"].update(
                        {"entryStatus": "active", "extensionSlotStatus": "verified"}
                    )
                    extension["activation"] = {
                        "mode": "preverified",
                        "resultingEntryStatus": "active",
                        "resultingExtensionSlotStatus": "verified",
                    }
                else:
                    extension["registry"].update(
                        {"entryStatus": "active", "extensionSlotsStatus": "verified"}
                    )
                    extension["activation"] = {
                        "mode": "preverified",
                        "resultingEntryStatus": "active",
                        "resultingExtensionSlotsStatus": "verified",
                    }
                self._set_final_lifecycle(
                    bundle,
                    execution_completed=True,
                    verification_passed=True,
                )
                codes = error_codes(self.validate(bundle))
                self.assertNotIn("reuse.activation_lifecycle", codes)
                if bundle["version"] == "0.3":
                    self.assertIn("reuse.parameter_registry_binding", codes)
                else:
                    self.assertFalse({code for code in codes if code.startswith("reuse.")}, codes)

    def test_rename_legacy_slot_shape_is_valid(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        bundle["reuseRelations"][0]["namedSlot"] = {
            "operation": "rename-legacy-slot",
            "oldName": "Slot1",
            "newName": "SlotContent",
            "classPath": "/Script/UMG.NamedSlot",
            "treeOrder": "last",
            "layout": {"mode": "fill", "anchors": [0, 0, 1, 1], "offsets": [0, 0, 0, 0], "alignment": [0, 0]},
        }
        codes = error_codes(self.validate(bundle))
        self.assertFalse({code for code in codes if code.startswith("reuse.")}, codes)
        self.assertEqual([], self.validate_named_slot(bundle["reuseRelations"][0]["namedSlot"]))

    def test_rename_legacy_slot_requires_old_and_new_names(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        named_slot = bundle["reuseRelations"][0]["namedSlot"]
        named_slot["operation"] = "rename-legacy-slot"
        named_slot.pop("standardName")
        named_slot.pop("legacyPreservedNames")
        named_slot["newName"] = "SlotContent"
        self.assertTrue(self.validate_named_slot(named_slot))

    def test_rename_legacy_slot_rejects_add_shape_fields(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        named_slot = bundle["reuseRelations"][0]["namedSlot"]
        named_slot.update({"operation": "rename-legacy-slot", "oldName": "Slot1", "newName": "SlotContent"})
        self.assertTrue(self.validate_named_slot(named_slot))

    def test_rename_legacy_slot_must_change_name(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        bundle["reuseRelations"][0]["namedSlot"] = {
            "operation": "rename-legacy-slot",
            "oldName": "SlotContent",
            "newName": "SlotContent",
            "classPath": "/Script/UMG.NamedSlot",
            "treeOrder": "last",
            "layout": {"mode": "fill", "anchors": [0, 0, 1, 1], "offsets": [0, 0, 0, 0], "alignment": [0, 0]},
        }
        self.assertIn("reuse.slot_rename_identity", error_codes(self.validate(bundle)))

    def test_add_standard_slot_requires_preserved_legacy_names(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        bundle["reuseRelations"][0]["namedSlot"]["legacyPreservedNames"] = []
        self.assertTrue(self.validate_named_slot(bundle["reuseRelations"][0]["namedSlot"]))

    def test_add_standard_slot_rejects_duplicate_legacy_names(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        bundle["reuseRelations"][0]["namedSlot"]["legacyPreservedNames"] = ["Slot1", "Slot1"]
        self.assertTrue(self.validate_named_slot(bundle["reuseRelations"][0]["namedSlot"]))

    def test_add_standard_slot_rejects_rename_shape_fields(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        named_slot = bundle["reuseRelations"][0]["namedSlot"]
        named_slot.update({"oldName": "Slot1", "newName": "SlotContent"})
        self.assertTrue(self.validate_named_slot(named_slot))

    def test_add_standard_slot_cannot_preserve_new_standard_name(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        bundle["reuseRelations"][0]["namedSlot"]["legacyPreservedNames"] = ["SlotContent"]
        self.assertIn("reuse.slot_preserved_conflict", error_codes(self.validate(bundle)))

    def test_add_standard_slot_rejects_reserved_widget_slot_name(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        bundle["reuseRelations"][0]["namedSlot"]["standardName"] = "Slot"
        self.assertTrue(self.validate_named_slot(bundle["reuseRelations"][0]["namedSlot"]))

    def test_rename_legacy_slot_rejects_reserved_widget_slot_name(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        bundle["reuseRelations"][0]["namedSlot"] = {
            "operation": "rename-legacy-slot",
            "oldName": "Slot1",
            "newName": "Slot",
            "classPath": "/Script/UMG.NamedSlot",
            "treeOrder": "last",
            "layout": {"mode": "fill", "anchors": [0, 0, 1, 1], "offsets": [0, 0, 0, 0], "alignment": [0, 0]},
        }
        self.assertTrue(self.validate_named_slot(bundle["reuseRelations"][0]["namedSlot"]))

    def test_inherited_slot_rejects_reserved_widget_slot_name(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        bundle["reuseRelations"][1]["inheritedSlot"]["slotName"] = "Slot"
        self.assertIn("schema.one_of", error_codes(self.validate(bundle)))

    def test_add_standard_slot_preserves_legacy_slot1(self) -> None:
        named_slot = self.bundle["reuseRelations"][0]["namedSlot"]
        self.assertEqual(["Slot1"], named_slot["legacyPreservedNames"])
        self.assertEqual([], self.validate_named_slot(named_slot))

    def test_v01_rejects_mixed_reuse_relations_field(self) -> None:
        bundle = copy.deepcopy(self.legacy)
        bundle["reuseRelations"] = []
        self.assertIn("schema.one_of", error_codes(self.validate(bundle)))

    def test_unknown_bundle_version_fails_explicit_route(self) -> None:
        bundle = copy.deepcopy(self.legacy)
        bundle["version"] = "0.4"
        self.assertIn("bundle.version", error_codes(self.validate(bundle)))

    def test_unknown_relation_field_is_rejected(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        bundle["reuseRelations"][0]["unexpected"] = True
        self.assertIn("schema.one_of", error_codes(self.validate(bundle)))

    def test_parent_must_follow_extension_in_build_order(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        bundle["assets"][0]["buildOrder"] = 1
        bundle["assets"][1]["buildOrder"] = 0
        bundle["execution"]["buildOrderAssetIds"] = ["build-system-child", "build-shared-prototype", "build-host-entry"]
        self.assertIn("reuse.parent_order", error_codes(self.validate(bundle)))

    def test_host_must_nest_actual_derived_class(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        bundle["reuseRelations"][2]["nestedWidgetClassPath"] = "/Game/UI/UMG/Widgets/uw_common_bag_item.uw_common_bag_item_C"
        self.assertIn("reuse.instance_class", error_codes(self.validate(bundle)))

    def test_preverified_gate_rejects_candidate_registry(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        bundle["reuseRelations"][0]["activation"] = {
            "mode": "preverified",
            "resultingEntryStatus": "active",
            "resultingExtensionSlotStatus": "verified",
        }
        self.assertIn("reuse.activation_gate", error_codes(self.validate(bundle)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
