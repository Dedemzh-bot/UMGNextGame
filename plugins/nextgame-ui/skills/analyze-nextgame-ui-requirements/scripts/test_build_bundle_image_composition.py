#!/usr/bin/env python3
"""Regression tests for requirement-to-layout image composition bindings."""

from __future__ import annotations

import copy
import unittest
from pathlib import Path
from typing import Callable
from unittest.mock import patch

from _contract_common import ASSETS_ROOT, compute_approved_content_sha256, load_json
import validate_build_bundle as build_bundle_validator
from validate_build_bundle import (
    DEFAULT_SCHEMA,
    _image_realization_is_within_owner,
    _image_requirement_realizations,
    _responsive_requirement_realizations,
    validate_build_bundle,
)


EXAMPLE_PREFIX = "example-composite-tabs"
TARGET_IMAGE_ID = "element-tab-selected-background"
TARGET_NODE_ID = "node-tab-selected-background"
TARGET_MAPPING_ID = "mapping-tab-selected-background"
TARGET_INTENT_ID = "responsive-selected-background"
VIRTUAL_ARTIFACT_ROOT = ASSETS_ROOT / "__virtual_image_composition_test__"


def error_codes(validation: dict) -> set[str]:
    return {error["code"] for error in validation["errors"]}


def find_by_id(items: list[dict], entity_id: str) -> dict:
    return next(item for item in items if item.get("id") == entity_id)


class BuildBundleImageCompositionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = load_json(DEFAULT_SCHEMA)
        self.requirement_template = load_json(ASSETS_ROOT / f"{EXAMPLE_PREFIX}-requirement.json")
        self.bundle_template = load_json(ASSETS_ROOT / f"{EXAMPLE_PREFIX}-build-bundle.json")
        self.child_layout_template = load_json(ASSETS_ROOT / f"{EXAMPLE_PREFIX}-child-layout-spec.json")
        self.screen_layout_template = load_json(ASSETS_ROOT / f"{EXAMPLE_PREFIX}-screen-layout-spec.json")

    def _base_artifacts(self) -> tuple[dict, dict, dict, dict]:
        requirement = copy.deepcopy(self.requirement_template)
        bundle = copy.deepcopy(self.bundle_template)
        child_layout = copy.deepcopy(self.child_layout_template)
        screen_layout = copy.deepcopy(self.screen_layout_template)

        requirement["analysisPolicy"] = {
            "geometryEvidenceRequired": False,
            "listPriorityRequired": False,
            "imageCompositionRequired": True,
            "explicitImageOwnerIntentRequired": True,
        }
        image_elements = [element for element in requirement["uiModel"]["elements"] if element.get("kind") == "image"]
        for element in image_elements:
            group_key = element["id"].replace("element-tab-", "graphic.")
            element["imageComposition"] = {
                "groupKey": group_key,
                "role": "complete",
                "adaptation": "inherit-owner",
                "ownerIntentId": "responsive-navigation-left",
            }

        target_element = find_by_id(requirement["uiModel"]["elements"], TARGET_IMAGE_ID)
        target_element["imageComposition"] = {
            "groupKey": target_element["imageComposition"]["groupKey"],
            "role": "complete",
            "adaptation": "independent",
        }
        requirement["uiModel"]["responsiveIntent"].append(
            {
                "id": TARGET_INTENT_ID,
                "elementId": TARGET_IMAGE_ID,
                "horizontal": "stretch",
                "vertical": "stretch",
                "reason": "The complete background follows its owning panel on both axes.",
                "inBuildScope": True,
                "evidenceIds": list(target_element["evidenceIds"]),
                "claimIds": ["claim-tab-composite-state"],
            }
        )
        find_by_id(requirement["claims"], "claim-tab-composite-state")["subjectRefs"].append(TARGET_INTENT_ID)
        find_by_id(bundle["nodeMappings"], TARGET_MAPPING_ID)["requirementRefs"].append(TARGET_INTENT_ID)
        find_by_id(child_layout["nodes"], TARGET_NODE_ID)["adaptiveLayout"] = {
            "horizontal": "stretch",
            "vertical": "stretch",
            "reason": "The complete background follows its owning panel on both axes.",
        }
        return requirement, bundle, child_layout, screen_layout

    def _validate(
        self,
        mutate: Callable[[dict, dict, dict, dict], None] | None = None,
        *,
        policy_enabled: bool = True,
    ) -> dict:
        requirement, bundle, child_layout, screen_layout = self._base_artifacts()
        if not policy_enabled:
            requirement.pop("analysisPolicy", None)
            for element in requirement["uiModel"]["elements"]:
                element.pop("imageComposition", None)
        if mutate is not None:
            mutate(requirement, bundle, child_layout, screen_layout)

        requirement["reviewGate"]["approvedContentSha256"] = compute_approved_content_sha256(requirement)
        bundle["requirement"]["approvedContentSha256"] = requirement["reviewGate"]["approvedContentSha256"]

        requirement_path = VIRTUAL_ARTIFACT_ROOT / f"{EXAMPLE_PREFIX}-requirement.json"
        child_layout_path = VIRTUAL_ARTIFACT_ROOT / f"{EXAMPLE_PREFIX}-child-layout-spec.json"
        screen_layout_path = VIRTUAL_ARTIFACT_ROOT / f"{EXAMPLE_PREFIX}-screen-layout-spec.json"
        bundle_path = VIRTUAL_ARTIFACT_ROOT / f"{EXAMPLE_PREFIX}-build-bundle.json"
        virtual_hashes = {
            requirement_path.name: "1" * 64,
            child_layout_path.name: "2" * 64,
            screen_layout_path.name: "3" * 64,
        }
        virtual_layouts = {
            child_layout_path.name: child_layout,
            screen_layout_path.name: screen_layout,
        }
        bundle["requirement"]["sha256"] = virtual_hashes[requirement_path.name]
        for asset in bundle["assets"]:
            layout_name = Path(asset["layoutSpecPath"]).name
            if layout_name in virtual_hashes:
                asset["layoutSpecSha256"] = virtual_hashes[layout_name]

        def load_virtual_json(path: Path) -> dict:
            name = Path(path).name
            if name in virtual_layouts:
                return copy.deepcopy(virtual_layouts[name])
            return load_json(Path(path))

        def hash_virtual_file(path: Path) -> str:
            name = Path(path).name
            if name in virtual_hashes:
                return virtual_hashes[name]
            raise AssertionError(f"Unexpected virtual hash request: {path}")

        with patch.object(build_bundle_validator, "load_json", side_effect=load_virtual_json), patch.object(
            build_bundle_validator, "sha256_file", side_effect=hash_virtual_file
        ):
            return validate_build_bundle(
                bundle,
                self.schema,
                bundle_path=bundle_path,
                requirement_spec=requirement,
                requirement_path=requirement_path,
                check_linked_files=True,
            )

    @staticmethod
    def _add_duplicate_image_node(_requirement: dict, bundle: dict, child_layout: dict, _screen_layout: dict) -> None:
        duplicate_node = copy.deepcopy(find_by_id(child_layout["nodes"], TARGET_NODE_ID))
        duplicate_node["id"] = "node-tab-selected-background-copy"
        duplicate_node["name"] = "ImgSelectedBackgroundCopy"
        child_layout["nodes"].append(duplicate_node)
        duplicate_mapping = copy.deepcopy(find_by_id(bundle["nodeMappings"], TARGET_MAPPING_ID))
        duplicate_mapping.update(
            {
                "id": "mapping-tab-selected-background-copy",
                "layoutNodeId": duplicate_node["id"],
                "requirementRefs": [TARGET_IMAGE_ID],
            }
        )
        bundle["nodeMappings"].append(duplicate_mapping)

    def test_complete_image_and_element_intent_have_one_matching_node(self) -> None:
        validation = self._validate()
        self.assertTrue(validation["valid"], validation["errors"])

    def test_duplicate_image_nodes_are_rejected(self) -> None:
        validation = self._validate(self._add_duplicate_image_node)
        self.assertIn("mapping.image_composition_count", error_codes(validation))

    def test_orphan_visual_image_node_is_rejected(self) -> None:
        def mutate(_requirement: dict, bundle: dict, child_layout: dict, _screen_layout: dict) -> None:
            orphan_node = copy.deepcopy(find_by_id(child_layout["nodes"], TARGET_NODE_ID))
            orphan_node.update({"id": "node-generated-point-fragment", "name": "ImgGeneratedPointFragment"})
            child_layout["nodes"].append(orphan_node)
            bundle["nodeMappings"].append(
                {
                    "id": "mapping-generated-point-fragment",
                    "assetId": "build-child-navigation-tab",
                    "layoutNodeId": orphan_node["id"],
                    "mappingKind": "generated-support",
                    "requirementRefs": ["region-navigation"],
                    "claimIds": ["claim-navigation-region"],
                    "stateRefs": [],
                }
            )

        validation = self._validate(mutate)
        self.assertIn("mapping.visual_image_requirement_count", error_codes(validation))

    def test_image_requirement_mapped_to_wrong_role_is_rejected(self) -> None:
        def mutate(_requirement: dict, _bundle: dict, child_layout: dict, _screen_layout: dict) -> None:
            find_by_id(child_layout["nodes"], TARGET_NODE_ID)["role"] = "container.canvas"

        validation = self._validate(mutate)
        self.assertIn("mapping.image_composition_count", error_codes(validation))

    def test_element_intent_on_a_different_node_is_rejected(self) -> None:
        def mutate(_requirement: dict, bundle: dict, _child_layout: dict, _screen_layout: dict) -> None:
            image_mapping = find_by_id(bundle["nodeMappings"], TARGET_MAPPING_ID)
            image_mapping["requirementRefs"].remove(TARGET_INTENT_ID)
            find_by_id(bundle["nodeMappings"], "mapping-tab-selected-panel")["requirementRefs"].append(TARGET_INTENT_ID)

        validation = self._validate(mutate)
        self.assertIn("mapping.element_responsive_binding_count", error_codes(validation))

    def test_element_intent_and_target_cannot_co_map_to_a_different_container_node(self) -> None:
        def mutate(_requirement: dict, bundle: dict, _child_layout: dict, _screen_layout: dict) -> None:
            image_mapping = find_by_id(bundle["nodeMappings"], TARGET_MAPPING_ID)
            image_mapping["requirementRefs"].remove(TARGET_INTENT_ID)
            container_mapping = find_by_id(bundle["nodeMappings"], "mapping-tab-selected-panel")
            container_mapping["requirementRefs"].extend([TARGET_IMAGE_ID, TARGET_INTENT_ID])

        validation = self._validate(mutate)
        self.assertIn("mapping.element_responsive_target_node", error_codes(validation))

    def test_element_intent_adaptive_mismatch_is_rejected(self) -> None:
        def mutate(_requirement: dict, _bundle: dict, child_layout: dict, _screen_layout: dict) -> None:
            find_by_id(child_layout["nodes"], TARGET_NODE_ID)["adaptiveLayout"]["horizontal"] = "right"

        validation = self._validate(mutate)
        self.assertIn("mapping.element_responsive_adaptive", error_codes(validation))

    def test_non_image_element_intent_may_map_to_its_container_node(self) -> None:
        def mutate(requirement: dict, bundle: dict, child_layout: dict, _screen_layout: dict) -> None:
            intent_id = "responsive-selected-panel"
            requirement["uiModel"]["responsiveIntent"].append(
                {
                    "id": intent_id,
                    "elementId": "element-tab-selected-panel",
                    "horizontal": "stretch",
                    "vertical": "stretch",
                    "reason": "The state panel fills its owning content panel.",
                    "inBuildScope": True,
                    "evidenceIds": ["evidence-selected-tab"],
                    "claimIds": ["claim-tab-composite-state"],
                }
            )
            find_by_id(requirement["claims"], "claim-tab-composite-state")["subjectRefs"].append(intent_id)
            find_by_id(bundle["nodeMappings"], "mapping-tab-selected-panel")["requirementRefs"].append(intent_id)
            find_by_id(child_layout["nodes"], "node-tab-selected-panel")["adaptiveLayout"] = {
                "horizontal": "stretch",
                "vertical": "stretch",
                "reason": "The state panel fills its owning content panel.",
            }

        validation = self._validate(mutate)
        self.assertTrue(validation["valid"], validation["errors"])

    def test_inherited_owner_region_intent_must_match_its_owner_node_adaptation(self) -> None:
        def mutate(_requirement: dict, _bundle: dict, _child_layout: dict, screen_layout: dict) -> None:
            find_by_id(screen_layout["nodes"], "node-navigation-panel")["adaptiveLayout"]["horizontal"] = "right"

        validation = self._validate(mutate)
        self.assertIn("mapping.owner_responsive_adaptive", error_codes(validation))

    def test_inherited_image_must_remain_under_its_exact_element_owner(self) -> None:
        def mutate(requirement: dict, bundle: dict, child_layout: dict, _screen_layout: dict) -> None:
            owner_intent_id = "responsive-selected-panel"
            requirement["uiModel"]["responsiveIntent"].append(
                {
                    "id": owner_intent_id,
                    "elementId": "element-tab-selected-panel",
                    "horizontal": "stretch",
                    "vertical": "stretch",
                    "reason": "The selected artwork follows the selected state panel.",
                    "inBuildScope": True,
                    "evidenceIds": ["evidence-selected-tab"],
                    "claimIds": ["claim-tab-composite-state"],
                }
            )
            find_by_id(requirement["claims"], "claim-tab-composite-state")["subjectRefs"].append(owner_intent_id)
            find_by_id(requirement["uiModel"]["elements"], "element-tab-selected-accent")["imageComposition"]["ownerIntentId"] = owner_intent_id
            find_by_id(bundle["nodeMappings"], "mapping-tab-selected-panel")["requirementRefs"].append(owner_intent_id)
            find_by_id(child_layout["nodes"], "node-tab-selected-panel")["adaptiveLayout"] = {
                "horizontal": "stretch",
                "vertical": "stretch",
                "reason": "The selected state panel owns its artwork.",
            }
            find_by_id(child_layout["nodes"], "node-tab-selected-accent")["parent"] = "node-tab-unselected-panel"

        validation = self._validate(mutate)
        self.assertIn("mapping.image_owner_containment", error_codes(validation))

    def test_referenced_owner_realization_resolves_a_shared_widget_relation(self) -> None:
        owner_relation = {
            "id": "reuse-navigation-owner",
            "type": "widget-tree-instance",
            "requirementRefs": ["region-navigation", "responsive-navigation-left"],
        }
        mappings, relations = _responsive_requirement_realizations(
            "responsive-navigation-left",
            "region-navigation",
            [],
            [owner_relation],
        )
        self.assertEqual([], mappings)
        self.assertEqual([owner_relation], relations)

    def test_owner_relation_contains_layout_content_from_its_source_asset(self) -> None:
        image_mapping = {"assetId": "asset-child", "layoutNodeId": "node-image"}
        owner_relation = {
            "id": "reuse-owner",
            "type": "widget-tree-instance",
            "sourceAssetId": "asset-child",
            "targetAssetId": "asset-screen",
        }
        self.assertTrue(
            _image_realization_is_within_owner(
                ("mapping", image_mapping),
                ("relation", owner_relation),
                bundle={"crossAssetOperations": []},
                reuse_relations=[owner_relation],
                layout_node_records_by_asset={"asset-child": {"node-image": {"parent": None}}},
            )
        )

    def test_relation_backed_image_requires_exact_parent_tree_path_for_owner_containment(self) -> None:
        image_relation = {
            "id": "reuse-image",
            "type": "widget-tree-instance",
            "sourceAssetId": "asset-image",
            "targetAssetId": "asset-screen",
            "targetAssetPath": "/Game/UI/UMG/Test/umg_test",
            "placementContract": {
                "slot": {
                    "parentWidgetName": "PanelOwner",
                    "parentTreePath": "/Game/UI/UMG/Test/umg_test.umg_test:WidgetTree.PanelWrong",
                }
            },
        }
        owner_mapping = {"assetId": "asset-screen", "layoutNodeId": "node-owner"}
        self.assertFalse(
            _image_realization_is_within_owner(
                ("relation", image_relation),
                ("mapping", owner_mapping),
                bundle={"crossAssetOperations": []},
                reuse_relations=[image_relation],
                layout_node_records_by_asset={
                    "asset-screen": {
                        "node-owner": {"name": "PanelOwner", "parent": None},
                    }
                },
            )
        )

    def test_policy_omitted_keeps_duplicate_image_mapping_compatible(self) -> None:
        validation = self._validate(self._add_duplicate_image_node, policy_enabled=False)
        self.assertTrue(validation["valid"], validation["errors"])

    def test_shared_widget_instance_is_a_single_image_realization_without_a_duplicate_node(self) -> None:
        relations = [
            {
                "type": "widget-tree-instance",
                "requirementRefs": [TARGET_IMAGE_ID],
            },
            {
                "type": "class-settings-parent-class",
                "requirementRefs": [TARGET_IMAGE_ID],
            },
        ]
        visual_mappings, reuse_relations = _image_requirement_realizations(
            TARGET_IMAGE_ID,
            [],
            relations,
            {},
        )
        self.assertEqual([], visual_mappings)
        self.assertEqual([relations[0]], reuse_relations)


if __name__ == "__main__":
    unittest.main(verbosity=2)
