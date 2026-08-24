#!/usr/bin/env python3
"""Validate a NextGame UILayoutSpec without third-party packages."""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CATALOG = SKILL_ROOT / "references" / "component-catalog.json"
ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")
NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
REGION_PURPOSE_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")
EVIDENCE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9.-]{2,95}$")
ROOT_LAYERS = {"background", "overlay"}
MODES = {"prototype", "production"}
PROJECT_SCREEN_REFERENCE_SIZE = [2560, 1440]
COMMON_TEXT_SYMBOLS = set("$閳ь儩锛儻楠?閳?閳崡妞?<>閳倐澧?@&*^|~")
DECORATIVE_TEXT_RUN = re.compile(r"[-_=~]{3,}")
ANCHORS = {
    "auto", "left-top", "center-top", "right-top",
    "left-center", "center", "right-center",
    "left-bottom", "center-bottom", "right-bottom",
}
ADAPTIVE_HORIZONTAL = {"left", "center", "right", "stretch"}
ADAPTIVE_VERTICAL = {"top", "center", "bottom", "stretch"}
OVERLAY_PURPOSES = {"layering", "adaptive-bounds", "independent-alignment"}
TEXT_JUSTIFICATIONS = {"Left", "Center", "Right"}
PASSIVE_VISIBILITIES = {"SelfHitTestInvisible", "Hidden", "Collapsed"}
BUTTON_SLOT_FILL = "Fill"
OVERLAY_HORIZONTAL_ALIGNMENTS = {"Fill", "Left", "Center", "Right"}
OVERLAY_VERTICAL_ALIGNMENTS = {"Fill", "Top", "Center", "Bottom"}
FLOW_PARENT_ROLES = {"container.vertical", "container.horizontal"}
SCROLL_PARENT_ROLE = "container.game-scroll"
FLOW_SIZE_RULES = {"Auto", "Fill"}
PROJECT_GAME_IMAGE_CLASS = "/Script/UIFramework.GameImage"
RATIO_GROUP_KIND = "ratio"
ANCHOR_INTENTS = {
    "left-top": ("left", "top"),
    "center-top": ("center", "top"),
    "right-top": ("right", "top"),
    "left-center": ("left", "center"),
    "center": ("center", "center"),
    "right-center": ("right", "center"),
    "left-bottom": ("left", "bottom"),
    "center-bottom": ("center", "bottom"),
    "right-bottom": ("right", "bottom"),
}


def slot_axis_intent(minimum: float, maximum: float, axis: str) -> str | None:
    if maximum - minimum > 0.000001:
        return "stretch"
    point = minimum
    choices = (
        ((0.0, "left"), (0.5, "center"), (1.0, "right"))
        if axis == "horizontal"
        else ((0.0, "top"), (0.5, "center"), (1.0, "bottom"))
    )
    for expected, intent in choices:
        if abs(point - expected) <= 0.000001:
            return intent
    return None


def panel_alignment_intent(alignment: Any, axis: str) -> str | None:
    choices = (
        {"Fill": "stretch", "Left": "left", "Center": "center", "Right": "right"}
        if axis == "horizontal"
        else {"Fill": "stretch", "Top": "top", "Center": "center", "Bottom": "bottom"}
    )
    return choices.get(alignment)


def normalized_rects_equal(left: Any, right: Any, tolerance: float = 0.000001) -> bool:
    return (
        isinstance(left, list)
        and isinstance(right, list)
        and len(left) == len(right) == 4
        and all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for value in left + right
        )
        and max(abs(float(a) - float(b)) for a, b in zip(left, right)) <= tolerance
    )


def has_measured_content_driven_size(node: dict[str, Any]) -> bool:
    content_driven = node.get("contentDrivenSize")
    if not isinstance(content_driven, dict) or content_driven.get("verified") is not True:
        return False
    measured = content_driven.get("measuredDesiredSize")
    evidence_id = content_driven.get("evidenceId")
    return (
        isinstance(measured, list)
        and len(measured) == 2
        and all(
            isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
            for value in measured
        )
        and isinstance(evidence_id, str)
        and EVIDENCE_ID_PATTERN.fullmatch(evidence_id) is not None
    )


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def invalid_text_characters(text: str) -> list[str]:
    invalid: list[str] = []
    for character in text:
        if character == " ":
            continue
        category = unicodedata.category(character)
        if category[0] in {"L", "N", "P"} or character in COMMON_TEXT_SYMBOLS:
            continue
        if character not in invalid:
            invalid.append(character)
    return invalid


def validate_spec(spec: Any, catalog: Any) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    def issue(collection: list[dict[str, str]], code: str, path: str, message: str) -> None:
        collection.append({"code": code, "path": path, "message": message})

    def validate_panel_slot(slot: Any, path: str, kind: str, include_size: bool) -> None:
        label = "FlowSlot" if kind == "flow" else "ScrollSlot"
        if not isinstance(slot, dict):
            issue(errors, f"{kind}.slot.type", path, f"{label} must be an object.")
            return

        required_fields = {"padding", "horizontalAlignment", "verticalAlignment"}
        if include_size:
            required_fields.add("size")
        missing_fields = sorted(required_fields - set(slot))
        extra_fields = sorted(set(slot) - required_fields)
        if missing_fields or extra_fields:
            details: list[str] = []
            if missing_fields:
                details.append(f"missing {', '.join(missing_fields)}")
            if extra_fields:
                details.append(f"unsupported {', '.join(extra_fields)}")
            issue(
                errors,
                f"{kind}.slot.fields",
                path,
                f"{label} fields are closed ({'; '.join(details)}).",
            )

        padding = slot.get("padding")
        if not (
            isinstance(padding, list)
            and len(padding) == 4
            and all(
                isinstance(value, (int, float)) and not isinstance(value, bool)
                for value in padding
            )
        ):
            issue(
                errors,
                f"{kind}.slot.padding",
                f"{path}.padding",
                f"{label} padding must contain four numeric left, top, right, and bottom values.",
            )

        horizontal_alignment = slot.get("horizontalAlignment")
        if horizontal_alignment not in OVERLAY_HORIZONTAL_ALIGNMENTS:
            issue(
                errors,
                f"{kind}.slot.horizontal_alignment",
                f"{path}.horizontalAlignment",
                f"{label} horizontalAlignment must be Fill, Left, Center, or Right.",
            )
        vertical_alignment = slot.get("verticalAlignment")
        if vertical_alignment not in OVERLAY_VERTICAL_ALIGNMENTS:
            issue(
                errors,
                f"{kind}.slot.vertical_alignment",
                f"{path}.verticalAlignment",
                f"{label} verticalAlignment must be Fill, Top, Center, or Bottom.",
            )

        if not include_size:
            return
        size = slot.get("size")
        if not isinstance(size, dict):
            issue(errors, "flow.slot.size", f"{path}.size", "FlowSlot size must be an object.")
            return
        extra_size_fields = sorted(set(size) - {"rule", "weight"})
        if extra_size_fields:
            issue(
                errors,
                "flow.slot.size.fields",
                f"{path}.size",
                f"FlowSlot size has unsupported fields: {', '.join(extra_size_fields)}.",
            )
        rule = size.get("rule")
        if rule not in FLOW_SIZE_RULES:
            issue(
                errors,
                "flow.slot.size.rule",
                f"{path}.size.rule",
                "FlowSlot size rule must be Auto or Fill.",
            )
            return
        has_weight = "weight" in size
        weight = size.get("weight")
        if rule == "Fill" and not has_weight:
            issue(
                errors,
                "flow.slot.size.weight_required",
                f"{path}.size.weight",
                "FlowSlot Fill size requires a positive weight.",
            )
        elif rule == "Fill" and not (
            isinstance(weight, (int, float)) and not isinstance(weight, bool) and weight > 0
        ):
            issue(
                errors,
                "flow.slot.size.weight",
                f"{path}.size.weight",
                "FlowSlot Fill weight must be a positive number.",
            )
        elif rule == "Auto" and has_weight:
            issue(
                errors,
                "flow.slot.size.weight_forbidden",
                f"{path}.size.weight",
                "FlowSlot Auto size must not declare a Fill weight.",
            )

    if not isinstance(spec, dict):
        issue(errors, "spec.type", "$", "The spec must be a JSON object.")
        return {"valid": False, "errors": errors, "warnings": warnings}

    for key in ("version", "mode", "asset", "referenceSize", "profile", "nodes"):
        if key not in spec:
            issue(errors, "spec.required", "$", f"Missing required field: {key}")

    if spec.get("version") != "0.2":
        issue(errors, "spec.version", "$.version", "Only UILayoutSpec version 0.2 is supported.")
    mode = spec.get("mode")
    if mode not in MODES:
        issue(errors, "spec.mode", "$.mode", "mode must be prototype or production.")

    asset = spec.get("asset")
    if not isinstance(asset, dict):
        issue(errors, "asset.type", "$.asset", "asset must be an object.")
    else:
        folder = asset.get("folder")
        name = asset.get("name")
        if not isinstance(folder, str):
            issue(errors, "asset.folder", "$.asset.folder", "Asset folder must be a string.")
        elif mode == "prototype" and not re.match(r"^/Game/UI/AIPrototype(?:/.*)?$", folder):
            issue(errors, "asset.folder", "$.asset.folder", "Prototype assets must be created under /Game/UI/AIPrototype.")
        elif mode == "production" and not re.match(r"^/Game/UI/UMG/.+$", folder):
            issue(errors, "asset.folder", "$.asset.folder", "Production assets must be created under /Game/UI/UMG/<SystemFolder>.")
        if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
            issue(errors, "asset.name", "$.asset.name", "Asset name must be an Unreal-safe identifier.")
        elif mode == "prototype" and not re.match(r"^umg_ai_[A-Za-z0-9_]+$", name):
            issue(errors, "asset.name", "$.asset.name", "Prototype asset name must start with umg_ai_.")

    reference_size = spec.get("referenceSize")
    if not (
        isinstance(reference_size, list)
        and len(reference_size) == 2
        and all(isinstance(value, int) and not isinstance(value, bool) and value > 0 for value in reference_size)
    ):
        issue(errors, "reference.size", "$.referenceSize", "referenceSize must contain two positive integers.")

    profile = spec.get("profile")
    region_grouping: bool | None = None
    list_role: str | None = None
    collection_sizing: str | None = None
    asset_kind: str | None = "prototype"
    design_size_mode: str | None = None
    explicit_panel_slots = False
    if not isinstance(profile, dict):
        issue(errors, "profile.type", "$.profile", "profile must be an object.")
    else:
        for key in ("adaptive", "interactive", "hasText", "containsRepeatedElements", "regionGrouping"):
            if not isinstance(profile.get(key), bool):
                issue(errors, "profile.boolean", f"$.profile.{key}", f"{key} must be a boolean.")
        if isinstance(profile.get("regionGrouping"), bool):
            region_grouping = profile["regionGrouping"]

        raw_explicit_panel_slots = profile.get("explicitPanelSlots")
        if raw_explicit_panel_slots is not None:
            if not isinstance(raw_explicit_panel_slots, bool):
                issue(
                    errors,
                    "profile.explicit_panel_slots",
                    "$.profile.explicitPanelSlots",
                    "explicitPanelSlots must be a boolean when specified.",
                )
            else:
                explicit_panel_slots = raw_explicit_panel_slots

        raw_list_role = profile.get("listRole")
        if raw_list_role is not None:
            if raw_list_role not in {"container", "entry"}:
                issue(
                    errors,
                    "profile.list_role",
                    "$.profile.listRole",
                    "listRole must be container or entry.",
                )
            else:
                list_role = raw_list_role

        raw_collection_sizing = profile.get("collectionSizing")
        if list_role == "container":
            if raw_collection_sizing not in {"show-all", "fixed-viewport"}:
                issue(
                    errors,
                    "list.collection_sizing",
                    "$.profile.collectionSizing",
                    "A collection container must declare collectionSizing as show-all or fixed-viewport.",
                )
            else:
                collection_sizing = raw_collection_sizing
        elif raw_collection_sizing is not None:
            issue(
                errors,
                "list.collection_sizing_role",
                "$.profile.collectionSizing",
                "collectionSizing is only valid when listRole is container.",
            )

        asset_kind = profile.get("assetKind", "prototype")
        if asset_kind not in {"prototype", "screen", "child-widget"}:
            issue(errors, "profile.asset_kind", "$.profile.assetKind", "assetKind must be prototype, screen, or child-widget.")
        design_size_mode = profile.get("designSizeMode")
        if design_size_mode is not None and design_size_mode not in {"FillScreen", "Desired"}:
            issue(
                errors,
                "profile.design_size_mode",
                "$.profile.designSizeMode",
                "designSizeMode must be FillScreen or Desired; Custom and on-screen variants are not permitted.",
            )
        if design_size_mode is None:
            issue(
                warnings,
                "profile.design_size_mode_missing",
                "$.profile.designSizeMode",
                "The analyzed Designer mode is missing. Legacy/standalone planning remains readable and uses the explicit fallback-unclear policy: FillScreen without inferring from assetKind or the asset-name prefix. New layouts must record the analyzed FillScreen or Desired decision explicitly.",
            )
        asset_scope = profile.get("assetScope", "system")
        if asset_scope not in {"system", "project-common"}:
            issue(
                errors,
                "profile.asset_scope",
                "$.profile.assetScope",
                "assetScope must be system or project-common.",
            )
            asset_scope = "system"
        if asset_scope == "project-common" and asset_kind != "child-widget":
            issue(
                errors,
                "profile.asset_scope.asset_kind",
                "$.profile.assetKind",
                "Project-common assets must be child-widget assets.",
            )
        if asset_scope == "project-common" and profile.get("subsystem") is not None:
            issue(
                errors,
                "profile.asset_scope.subsystem",
                "$.profile.subsystem",
                "Project-common assets do not use a subsystem name segment.",
            )
        if asset_kind == "screen" and reference_size != PROJECT_SCREEN_REFERENCE_SIZE:
            issue(
                errors,
                "layout.project_design_resolution",
                "$.referenceSize",
                "Complete NextGame system screens must use referenceSize [2560, 1440].",
            )
        if mode == "production" and asset_kind not in {"screen", "child-widget"}:
            issue(errors, "profile.production_asset_kind", "$.profile.assetKind", "Production mode requires assetKind screen or child-widget.")

        if list_role is not None and asset_kind != "child-widget":
            issue(
                errors,
                "list.asset_kind",
                "$.profile.assetKind",
                "A data-driven collection container or entry must be a child-widget asset.",
            )
        if list_role == "container" and profile.get("containsRepeatedElements") is not True:
            issue(
                errors,
                "list.container.repeated_elements",
                "$.profile.containsRepeatedElements",
                "A collection container must declare containsRepeatedElements as true.",
            )
        if list_role == "entry":
            if profile.get("secondaryFunction") != "list":
                issue(
                    errors,
                    "list.entry.secondary_function",
                    "$.profile.secondaryFunction",
                    "A collection entry asset must use secondaryFunction list.",
                )
            if profile.get("parentClass") != "/Script/UIFramework.ListViewItem":
                issue(
                    errors,
                    "list.entry.parent_class",
                    "$.profile.parentClass",
                    "A collection entry asset must derive from /Script/UIFramework.ListViewItem.",
                )

        token_pattern = re.compile(r"^[a-z][a-z0-9]*$")
        for key in ("system", "subsystem", "function", "secondaryFunction"):
            value = profile.get(key)
            if value is not None and (not isinstance(value, str) or not token_pattern.fullmatch(value)):
                issue(errors, "profile.naming_token", f"$.profile.{key}", f"{key} must be a lower-case naming token.")

        if asset_kind in {"screen", "child-widget"}:
            system = profile.get("system")
            system_folder = profile.get("systemFolder")
            subsystem = profile.get("subsystem")
            function_name = profile.get("function")
            secondary_function = profile.get("secondaryFunction")
            target_asset = profile.get("targetAsset")

            if not isinstance(system, str) or not token_pattern.fullmatch(system):
                issue(errors, "profile.system.required", "$.profile.system", "A valid system token is required for project-target assets.")
            elif asset_scope == "project-common" and system != "common":
                issue(
                    errors,
                    "profile.asset_scope.common_system",
                    "$.profile.system",
                    "Project-common assets must use the system token common.",
                )
            if not isinstance(system_folder, str) or not re.fullmatch(r"^[A-Za-z][A-Za-z0-9_]*$", system_folder):
                issue(errors, "profile.system_folder.required", "$.profile.systemFolder", "systemFolder is required for project-target assets.")
            elif isinstance(system, str) and token_pattern.fullmatch(system) and system_folder.casefold() != system.casefold():
                issue(
                    errors,
                    "profile.system_folder.system_mismatch",
                    "$.profile.systemFolder",
                    "systemFolder must identify the same system as profile.system and may differ only by letter case.",
                )
            if not isinstance(target_asset, dict):
                issue(errors, "profile.target_asset.required", "$.profile.targetAsset", "targetAsset is required for project-target assets.")
            elif isinstance(system, str) and isinstance(system_folder, str):
                if asset_kind == "screen":
                    expected_parts = ["umg", system]
                    if isinstance(subsystem, str):
                        expected_parts.append(subsystem)
                    expected_folder = f"/Game/UI/UMG/{system_folder}"
                else:
                    if not isinstance(function_name, str) or not token_pattern.fullmatch(function_name):
                        issue(errors, "profile.function.required", "$.profile.function", "A function token is required for child widgets.")
                    expected_parts = ["uw", system]
                    if isinstance(subsystem, str) and asset_scope != "project-common":
                        expected_parts.append(subsystem)
                    if isinstance(function_name, str):
                        expected_parts.append(function_name)
                    if isinstance(secondary_function, str):
                        expected_parts.append(secondary_function)
                    expected_folder = (
                        "/Game/UI/UMG/Widgets"
                        if asset_scope == "project-common"
                        else f"/Game/UI/UMG/{system_folder}/Widgets"
                    )

                expected_name = "_".join(expected_parts)
                if target_asset.get("name") != expected_name:
                    issue(errors, "target.name", "$.profile.targetAsset.name", f"Expected project asset name: {expected_name}")
                if target_asset.get("folder") != expected_folder:
                    issue(errors, "target.folder", "$.profile.targetAsset.folder", f"Expected project asset folder: {expected_folder}")
                if mode == "production" and isinstance(asset, dict):
                    if asset.get("folder") != target_asset.get("folder") or asset.get("name") != target_asset.get("name"):
                        issue(
                            errors,
                            "asset.production_target",
                            "$.asset",
                            "Production asset.folder and asset.name must exactly match profile.targetAsset.",
                        )
                if system == "fight" and asset_kind == "child-widget":
                    expected_integration = "/Game/UI/UMG/Fight/umg_fight"
                    if target_asset.get("integrationAsset") != expected_integration:
                        issue(errors, "fight.integration_asset", "$.profile.targetAsset.integrationAsset", f"Fight child widgets must target {expected_integration}.")

    components = catalog.get("components", []) if isinstance(catalog, dict) else []
    common_property_map = (
        catalog.get("commonPropertyMap", {}) if isinstance(catalog, dict) else {}
    )
    if not isinstance(common_property_map, dict):
        common_property_map = {}
    component_by_role = {
        item.get("role"): item
        for item in components
        if isinstance(item, dict) and isinstance(item.get("role"), str)
    }
    if not component_by_role:
        issue(errors, "catalog.empty", "$catalog", "Component catalog has no usable roles.")
    elif component_by_role.get("visual.image", {}).get("classPath") != PROJECT_GAME_IMAGE_CLASS:
        issue(
            errors,
            "component.project_game_image",
            "$catalog.components[role=visual.image].classPath",
            f"visual.image must map to {PROJECT_GAME_IMAGE_CLASS}; native /Script/UMG.Image is not allowed for new assembly work.",
        )

    nodes = spec.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        issue(errors, "nodes.type", "$.nodes", "nodes must be a non-empty array.")
        nodes = []

    node_by_id: dict[str, dict[str, Any]] = {}
    node_index_by_id: dict[str, int] = {}
    names: set[str] = set()
    roots: list[str] = []
    region_purposes: dict[str, str] = {}

    for index, node in enumerate(nodes):
        path = f"$.nodes[{index}]"
        if not isinstance(node, dict):
            issue(errors, "node.type", path, "Each node must be an object.")
            continue

        node_id = node.get("id")
        if not isinstance(node_id, str) or not ID_PATTERN.fullmatch(node_id):
            issue(errors, "node.id", f"{path}.id", "id must match ^[a-z][a-z0-9_-]*$.")
        elif node_id in node_by_id:
            issue(errors, "node.id.duplicate", f"{path}.id", f"Duplicate node id: {node_id}")
        else:
            node_by_id[node_id] = node
            node_index_by_id[node_id] = index

        name = node.get("name")
        if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
            issue(errors, "node.name", f"{path}.name", "name must be an Unreal-safe identifier.")
        elif name in names:
            issue(errors, "node.name.duplicate", f"{path}.name", f"Duplicate widget name: {name}")
        else:
            names.add(name)

        role = node.get("role")
        component = component_by_role.get(role)
        if component is None:
            issue(errors, "node.role", f"{path}.role", f"Unknown component role: {role!r}")
        else:
            name_prefix = component.get("namePrefix")
            if (
                isinstance(name, str)
                and NAME_PATTERN.fullmatch(name)
                and isinstance(name_prefix, str)
                and name_prefix
                and not name.startswith(name_prefix)
            ):
                issue(
                    warnings,
                    "node.name_prefix",
                    f"{path}.name",
                    f"Widget name for role {role} should start with {name_prefix}; review generated or mismatched names when purpose is clear.",
                )
            if component.get("warning"):
                issue(warnings, "component.placeholder", f"{path}.role", str(component["warning"]))

        is_variable = node.get("isVariable")
        if is_variable is not None and not isinstance(is_variable, bool):
            issue(
                errors,
                "widget.is_variable.boolean",
                f"{path}.isVariable",
                "isVariable must be a boolean when specified.",
            )
        if role in {"collection.lua-list", "collection.lua-tile"} and is_variable is not True:
            issue(
                errors,
                "widget.is_variable.collection_required",
                f"{path}.isVariable",
                "LuaListView and LuaTileView are runtime-populated collections and must set isVariable to true.",
            )

        region_purpose = node.get("regionPurpose")
        if region_purpose is not None:
            if not isinstance(region_purpose, str) or not REGION_PURPOSE_PATTERN.fullmatch(region_purpose):
                issue(
                    errors,
                    "region.purpose",
                    f"{path}.regionPurpose",
                    "regionPurpose must match ^[a-z][a-z0-9-]*$.",
                )
            elif region_purpose in region_purposes:
                issue(
                    errors,
                    "region.purpose.duplicate",
                    f"{path}.regionPurpose",
                    f"Duplicate regionPurpose: {region_purpose}",
                )
            elif isinstance(node_id, str):
                region_purposes[region_purpose] = node_id
            if component is not None and not component.get("canRepresentRegion"):
                issue(
                    errors,
                    "region.container_role",
                    f"{path}.role",
                    f"Role {role} cannot represent a region module.",
                )

        root_layer = node.get("rootLayer")
        if root_layer is not None and root_layer not in ROOT_LAYERS:
            issue(
                errors,
                "region.root_layer",
                f"{path}.rootLayer",
                "rootLayer must be background or overlay.",
            )
        if region_purpose is not None and root_layer is not None:
            issue(
                errors,
                "region.scope.conflict",
                path,
                "A node cannot define both regionPurpose and rootLayer.",
            )

        parent = node.get("parent")
        if parent is None:
            if isinstance(node_id, str):
                roots.append(node_id)
        elif not isinstance(parent, str):
            issue(errors, "node.parent", f"{path}.parent", "parent must be a node id or null.")

        rect = node.get("rect")
        if not (isinstance(rect, list) and len(rect) == 4 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in rect)):
            issue(errors, "node.rect", f"{path}.rect", "rect must contain four numbers.")
        else:
            x, y, width, height = rect
            if x < 0 or y < 0 or width <= 0 or height <= 0:
                issue(errors, "node.rect.range", f"{path}.rect", "x/y must be >= 0 and width/height must be > 0.")
            if x + width > 1.000001 or y + height > 1.000001:
                issue(errors, "node.rect.bounds", f"{path}.rect", "rect must remain inside the normalized reference frame.")

        if node.get("anchor") not in ANCHORS:
            issue(errors, "node.anchor", f"{path}.anchor", f"Unsupported anchor: {node.get('anchor')!r}")
        slot_layout = node.get("slotLayout")
        if slot_layout is not None:
            if parent is None:
                issue(
                    errors,
                    "slot_layout.root",
                    f"{path}.slotLayout",
                    "The root widget cannot define a CanvasPanelSlot layout.",
                )
            if not isinstance(slot_layout, dict):
                issue(errors, "slot_layout.type", f"{path}.slotLayout", "slotLayout must be an object.")
            else:
                anchors = slot_layout.get("anchors")
                offsets = slot_layout.get("offsets")
                alignment = slot_layout.get("alignment")
                auto_size = slot_layout.get("autoSize")
                if not isinstance(anchors, dict):
                    issue(errors, "slot_layout.anchors", f"{path}.slotLayout.anchors", "anchors must be an object.")
                else:
                    minimum = anchors.get("minimum")
                    maximum = anchors.get("maximum")
                    for anchor_key, anchor_value in (("minimum", minimum), ("maximum", maximum)):
                        if not (
                            isinstance(anchor_value, list)
                            and len(anchor_value) == 2
                            and all(
                                isinstance(value, (int, float))
                                and not isinstance(value, bool)
                                and 0 <= value <= 1
                                for value in anchor_value
                            )
                        ):
                            issue(
                                errors,
                                "slot_layout.anchor",
                                f"{path}.slotLayout.anchors.{anchor_key}",
                                "Canvas anchors must contain two numbers from 0 through 1.",
                            )
                    if (
                        isinstance(minimum, list)
                        and len(minimum) == 2
                        and isinstance(maximum, list)
                        and len(maximum) == 2
                        and all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in minimum + maximum)
                        and (minimum[0] > maximum[0] or minimum[1] > maximum[1])
                    ):
                        issue(
                            errors,
                            "slot_layout.anchor_order",
                            f"{path}.slotLayout.anchors",
                            "Canvas anchor minimum must not exceed maximum on either axis.",
                        )
                if not (
                    isinstance(offsets, dict)
                    and set(offsets) == {"left", "top", "right", "bottom"}
                    and all(
                        isinstance(value, (int, float)) and not isinstance(value, bool)
                        for value in offsets.values()
                    )
                ):
                    issue(
                        errors,
                        "slot_layout.offsets",
                        f"{path}.slotLayout.offsets",
                        "offsets must contain numeric left, top, right, and bottom values.",
                    )
                if not (
                    isinstance(alignment, list)
                    and len(alignment) == 2
                    and all(
                        isinstance(value, (int, float))
                        and not isinstance(value, bool)
                        and 0 <= value <= 1
                        for value in alignment
                    )
                ):
                    issue(
                        errors,
                        "slot_layout.alignment",
                        f"{path}.slotLayout.alignment",
                        "alignment must contain two numbers from 0 through 1.",
                    )
                if not isinstance(auto_size, bool):
                    issue(
                        errors,
                        "slot_layout.auto_size",
                        f"{path}.slotLayout.autoSize",
                        "autoSize must be a boolean.",
                    )

        if "contentDrivenSize" in node:
            content_driven = node.get("contentDrivenSize")
            content_path = f"{path}.contentDrivenSize"
            if not isinstance(content_driven, dict):
                issue(errors, "content_driven_size.type", content_path, "contentDrivenSize must be an object.")
            else:
                allowed_fields = {"verified", "measuredDesiredSize", "evidenceId"}
                extra_fields = sorted(set(content_driven) - allowed_fields)
                if extra_fields:
                    issue(
                        errors,
                        "content_driven_size.fields",
                        content_path,
                        f"contentDrivenSize has unsupported fields: {', '.join(extra_fields)}.",
                    )
                if not isinstance(content_driven.get("verified"), bool):
                    issue(
                        errors,
                        "content_driven_size.verified",
                        f"{content_path}.verified",
                        "contentDrivenSize.verified is required and must be a boolean.",
                    )
                if "measuredDesiredSize" in content_driven:
                    measured = content_driven.get("measuredDesiredSize")
                    if not (
                        isinstance(measured, list)
                        and len(measured) == 2
                        and all(
                            isinstance(value, (int, float))
                            and not isinstance(value, bool)
                            and value > 0
                            for value in measured
                        )
                    ):
                        issue(
                            errors,
                            "content_driven_size.measured_desired_size",
                            f"{content_path}.measuredDesiredSize",
                            "measuredDesiredSize must contain exactly two positive numbers.",
                        )
                if "evidenceId" in content_driven:
                    evidence_id = content_driven.get("evidenceId")
                    if not isinstance(evidence_id, str) or EVIDENCE_ID_PATTERN.fullmatch(evidence_id) is None:
                        issue(
                            errors,
                            "content_driven_size.evidence_id",
                            f"{content_path}.evidenceId",
                            "evidenceId must match ^[a-z][a-z0-9.-]{2,95}$.",
                        )

        adaptive_layout = node.get("adaptiveLayout")
        if adaptive_layout is not None:
            if not isinstance(adaptive_layout, dict):
                issue(errors, "layout.adaptive_intent.type", f"{path}.adaptiveLayout", "adaptiveLayout must be an object.")
            else:
                horizontal = adaptive_layout.get("horizontal")
                vertical = adaptive_layout.get("vertical")
                reason = adaptive_layout.get("reason")
                if horizontal not in ADAPTIVE_HORIZONTAL:
                    issue(errors, "layout.adaptive_intent.horizontal", f"{path}.adaptiveLayout.horizontal", "horizontal must be left, center, right, or stretch.")
                if vertical not in ADAPTIVE_VERTICAL:
                    issue(errors, "layout.adaptive_intent.vertical", f"{path}.adaptiveLayout.vertical", "vertical must be top, center, bottom, or stretch.")
                if not isinstance(reason, str) or not reason.strip():
                    issue(errors, "layout.adaptive_intent.reason", f"{path}.adaptiveLayout.reason", "adaptiveLayout must include a concise non-empty reason.")

        overlay_purpose = node.get("overlayPurpose")
        if overlay_purpose is not None:
            if role != "container.overlay":
                issue(errors, "structure.overlay_purpose.role", f"{path}.overlayPurpose", "overlayPurpose is only valid on an Overlay node.")
            elif overlay_purpose not in OVERLAY_PURPOSES:
                issue(errors, "structure.overlay_purpose.value", f"{path}.overlayPurpose", "overlayPurpose must be layering, adaptive-bounds, or independent-alignment.")
        if not isinstance(node.get("properties"), dict):
            issue(errors, "node.properties", f"{path}.properties", "properties must be an object.")
        elif component is not None:
            allowed_properties = set(common_property_map) | set(component.get("propertyMap", {}))
            unknown = sorted(set(node["properties"]) - allowed_properties)
            for property_name in unknown:
                issue(errors, "node.property.unknown", f"{path}.properties.{property_name}", f"Property is not mapped for role {role}.")
            properties = node["properties"]
            visibility = properties.get("visibility")
            if visibility is not None and visibility not in {
                "Visible",
                "Collapsed",
                "Hidden",
                "HitTestInvisible",
                "SelfHitTestInvisible",
            }:
                issue(
                    errors,
                    "widget.visibility",
                    f"{path}.properties.visibility",
                    "visibility must be a supported SlateVisibility value.",
                )
            if (
                component.get("hitTestPolicy") == "passive"
                and visibility is not None
                and visibility not in PASSIVE_VISIBILITIES
            ):
                issue(
                    errors,
                    "widget.passive_visibility",
                    f"{path}.properties.visibility",
                    "A passive component must use SelfHitTestInvisible, Hidden, or Collapsed; Visible and HitTestInvisible remain invalid.",
                )

            if role == "text.label":
                if "color" not in properties:
                    issue(
                        errors,
                        "text.color.required",
                        f"{path}.properties.color",
                        "Every TextBlock must explicitly declare its color.",
                    )

                font = properties.get("font")
                if font is None:
                    issue(
                        errors,
                        "text.font.required",
                        f"{path}.properties.font",
                        "Every TextBlock must declare its font size for even-size validation.",
                    )
                elif not isinstance(font, dict):
                    issue(errors, "text.font", f"{path}.properties.font", "font must be an object.")
                else:
                    font_size = font.get("size")
                    if not (
                        isinstance(font_size, int)
                        and not isinstance(font_size, bool)
                        and font_size > 0
                        and font_size % 2 == 0
                    ):
                        issue(
                            errors,
                            "text.font_size.even",
                            f"{path}.properties.font.size",
                            "TextBlock font size must be a positive even integer.",
                        )

                justification = properties.get("justification")
                if justification is None:
                    issue(
                        warnings,
                        "text.justification.missing",
                        f"{path}.properties.justification",
                        "Choose Left, Center, or Right justification from the stable edge and safe text-growth direction.",
                    )
                elif justification not in TEXT_JUSTIFICATIONS:
                    issue(
                        errors,
                        "text.justification.value",
                        f"{path}.properties.justification",
                        "TextBlock justification must be Left, Center, or Right.",
                    )

                auto_wrap = properties.get("autoWrap")
                if auto_wrap is not None and not isinstance(auto_wrap, bool):
                    issue(errors, "text.auto_wrap", f"{path}.properties.autoWrap", "autoWrap must be a boolean.")
                wrap_text_at = properties.get("wrapTextAt")
                if wrap_text_at is not None and (
                    not isinstance(wrap_text_at, (int, float))
                    or isinstance(wrap_text_at, bool)
                    or wrap_text_at <= 0
                ):
                    issue(
                        errors,
                        "text.wrap_width.positive",
                        f"{path}.properties.wrapTextAt",
                        "Wrap Text At must be a concrete positive number.",
                    )
                if auto_wrap is True and (
                    not isinstance(wrap_text_at, (int, float))
                    or isinstance(wrap_text_at, bool)
                    or wrap_text_at <= 0
                ):
                    issue(
                        errors,
                        "text.wrap_width.required",
                        f"{path}.properties.wrapTextAt",
                        "A wrapping TextBlock must set a concrete positive Wrap Text At value.",
                    )

            if role == "text.label" and "text" in properties:
                text = properties.get("text")
                if not isinstance(text, str):
                    issue(errors, "text.value", f"{path}.properties.text", "TextBlock text must be a string.")
                else:
                    if any(character in text for character in "\r\n\t"):
                        issue(
                            errors,
                            "text.independent_block",
                            f"{path}.properties.text",
                            "A TextBlock must contain one independent text block; create separate TextBlocks instead of embedding tabs or line breaks.",
                        )
                    if re.search(r" {2,}", text):
                        issue(
                            errors,
                            "text.spacing_layout",
                            f"{path}.properties.text",
                            "Do not use repeated spaces to position separate labels; create separate TextBlocks.",
                        )
                    invalid_characters = invalid_text_characters(text)
                    if invalid_characters:
                        issue(
                            errors,
                            "text.non_text_glyph",
                            f"{path}.properties.text",
                            "TextBlock contains icon or non-text glyphs; use GameImage components instead: "
                            + " ".join(f"U+{ord(character):04X}" for character in invalid_characters),
                        )
                    if DECORATIVE_TEXT_RUN.search(text):
                        issue(
                            errors,
                            "text.decorative_run",
                            f"{path}.properties.text",
                            "Do not draw separators or decoration with repeated text characters; use a GameImage component.",
                        )

    if isinstance(profile, dict) and profile.get("interactive") is True:
        roles = {
            node.get("role")
            for node in node_by_id.values()
            if isinstance(node, dict)
        }
        has_button = "input.button" in roles
        has_specialized_continuous_input = bool(
            roles
            & {
                "input.slider",
                "input.radial-slider",
                "container.game-scroll",
            }
        )
        delegates_interaction_to_collection_entries = (
            profile.get("listRole") == "container"
            and bool(roles & {"collection.lua-list", "collection.lua-tile"})
        )
        if (
            not has_button
            and not has_specialized_continuous_input
            and not delegates_interaction_to_collection_entries
        ):
            issue(
                errors,
                "interaction.button_trigger.missing",
                "$.nodes",
                "An interactive asset with click, tap, activation, or discrete state switching must contain a Btn input.button owner.",
            )

    collection_nodes = [
        (node_id, node)
        for node_id, node in node_by_id.items()
        if node.get("role") in {"collection.lua-list", "collection.lua-tile"}
    ]
    for node_id, node in collection_nodes:
        node_path = f"$.nodes[{node_index_by_id[node_id]}]"
        properties = node.get("properties", {})
        if not isinstance(properties, dict):
            continue
        for spacing_name in ("horizontalEntrySpacing", "verticalEntrySpacing"):
            spacing = properties.get(spacing_name)
            if spacing is not None and (
                not isinstance(spacing, (int, float))
                or isinstance(spacing, bool)
                or spacing < 0
            ):
                issue(
                    errors,
                    "list.entry_spacing",
                    f"{node_path}.properties.{spacing_name}",
                    f"{spacing_name} must be a non-negative number.",
                )
            elif node.get("role") == "collection.lua-tile" and spacing not in (None, 0, 0.0):
                issue(
                    errors,
                    "tile.entry_spacing.inward_only",
                    f"{node_path}.properties.{spacing_name}",
                    f"{spacing_name} contracts the usable Tile entry area inward and must remain zero/default; widen the tile pitch with entryWidth/entryHeight instead.",
                )
        if node.get("role") == "collection.lua-tile":
            for size_name in ("entryWidth", "entryHeight"):
                size = properties.get(size_name)
                if (
                    not isinstance(size, (int, float))
                    or isinstance(size, bool)
                    or size <= 0
                ):
                    issue(
                        errors,
                        "tile.entry_size.required",
                        f"{node_path}.properties.{size_name}",
                        f"LuaTileView must set a positive {size_name}; use the entry cell size relative to visible entry content to create spacing.",
                    )
    if list_role == "container":
        if not collection_nodes:
            issue(
                errors,
                "list.container.missing",
                "$.nodes",
                "A collection container must contain a LuaListView or LuaTileView node.",
            )
        elif len(collection_nodes) > 1:
            issue(
                errors,
                "list.container.multiple",
                "$.nodes",
                "One collection module must define exactly one data-driven collection endpoint.",
            )
        for node_id, node in collection_nodes:
            node_path = f"$.nodes[{node_index_by_id[node_id]}]"
            properties = node.get("properties", {})
            entry_reference = properties.get("entryWidgetClass") if isinstance(properties, dict) else None
            entry_path = (
                entry_reference.get("refPath")
                if isinstance(entry_reference, dict)
                else entry_reference
            )
            if not isinstance(entry_path, str) or not re.fullmatch(
                r"/Game/UI/UMG/.+\.[A-Za-z][A-Za-z0-9_]*_C",
                entry_path,
            ):
                issue(
                    errors,
                    "list.entry_widget_class",
                    f"{node_path}.properties.entryWidgetClass",
                    "entryWidgetClass must reference the generated class of a project entry Widget Blueprint.",
                )
            preview_count = properties.get("designerPreviewEntries") if isinstance(properties, dict) else None
            if preview_count is not None and (
                not isinstance(preview_count, int)
                or isinstance(preview_count, bool)
                or preview_count < 0
                or preview_count > 20
            ):
                issue(
                    errors,
                    "list.preview_count",
                    f"{node_path}.properties.designerPreviewEntries",
                    "designerPreviewEntries must be an integer from 0 through 20.",
                )
            if collection_sizing == "show-all":
                current_id = node_id
                visited: set[str] = set()
                canvas_child: dict[str, Any] | None = None
                canvas_child_path: str | None = None
                while current_id not in visited:
                    visited.add(current_id)
                    current = node_by_id.get(current_id)
                    if current is None:
                        break
                    parent_id = current.get("parent")
                    if not isinstance(parent_id, str):
                        break
                    parent = node_by_id.get(parent_id)
                    if parent is None:
                        break
                    parent_component = component_by_role.get(
                        parent.get("role"), {}
                    )
                    if (
                        parent_component.get("classPath")
                        == "/Script/UMG.CanvasPanel"
                    ):
                        canvas_child = current
                        canvas_child_path = (
                            f"$.nodes[{node_index_by_id[current_id]}]"
                        )
                        break
                    current_id = parent_id
                if canvas_child is None or canvas_child_path is None:
                    issue(
                        errors,
                        "list.show_all.canvas_ancestor",
                        node_path,
                        "A show-all collection must reach a CanvasPanel ancestor whose direct child can grow to desired size.",
                    )
                else:
                    slot_layout = canvas_child.get("slotLayout")
                    if (
                        not isinstance(slot_layout, dict)
                        or slot_layout.get("autoSize") is not True
                    ):
                        issue(
                            errors,
                            "list.show_all.auto_size",
                            f"{canvas_child_path}.slotLayout.autoSize",
                            "A show-all collection must enable Size To Content on the direct child slot under its nearest CanvasPanel ancestor.",
                        )
    if list_role == "entry" and collection_nodes:
        issue(
            errors,
            "list.entry.nested_collection",
            "$.nodes",
            "A collection entry asset represents one row or tile and must not contain another collection endpoint.",
        )

    if list_role == "entry":
        root_id = roots[0] if len(roots) == 1 else None
        direct_children = [node_id for node_id, node in node_by_id.items() if node.get("parent") == root_id]
        direct_panels = [
            node_id
            for node_id in direct_children
            if component_by_role.get(node_by_id[node_id].get("role"), {}).get("isPanel") is True
        ]
        first_panel_id = direct_panels[0] if len(direct_panels) == 1 else None
        first = node_by_id.get(first_panel_id) if first_panel_id else None
        first_path = f"$.nodes[{node_index_by_id[first_panel_id]}]" if first_panel_id else "$.nodes"
        if len(direct_panels) != 1:
            issue(
                errors,
                "list.entry.first_panel",
                first_path,
                "A list entry root must own exactly one first internal structural Panel that establishes its local size.",
            )
        else:
            slot = first.get("slotLayout")
            expected_w, expected_h = reference_size if isinstance(reference_size, list) else (None, None)
            offsets = slot.get("offsets") if isinstance(slot, dict) else None
            anchors = slot.get("anchors") if isinstance(slot, dict) else None
            fixed = (
                isinstance(offsets, dict)
                and offsets.get("left") == 0
                and offsets.get("top") == 0
                and offsets.get("right") == expected_w
                and offsets.get("bottom") == expected_h
                and isinstance(anchors, dict)
                and anchors.get("minimum") == [0, 0]
                and anchors.get("maximum") == [0, 0]
                and slot.get("autoSize") is False
            )
            content_driven = has_measured_content_driven_size(first)
            if not fixed and not content_driven:
                issue(errors, "list.entry.root_size", first_path, "First entry Panel Slot must explicitly match referenceSize width/height or declare verified content-driven size with positive measuredDesiredSize and a valid evidenceId; zero-offset full stretch is invalid.")

    if len(roots) != 1:
        issue(errors, "tree.root.count", "$.nodes", f"Exactly one root is required; found {len(roots)}.")
    elif roots:
        root = node_by_id.get(roots[0], {})
        if root.get("role") != "screen.root":
            issue(errors, "tree.root.role", "$.nodes", "The root node role must be screen.root.")
        if root.get("rect") != [0, 0, 1, 1]:
            issue(errors, "tree.root.rect", "$.nodes", "The root node rect must be [0, 0, 1, 1].")

    if design_size_mode == "Desired":
        root_id = roots[0] if len(roots) == 1 else None
        direct_children = [
            node
            for node in node_by_id.values()
            if root_id is not None and node.get("parent") == root_id
        ]

        def has_nonzero_desired_size_evidence(node: dict[str, Any]) -> bool:
            if has_measured_content_driven_size(node):
                return True
            slot = node.get("slotLayout")
            if not isinstance(slot, dict):
                return False
            anchors = slot.get("anchors")
            offsets = slot.get("offsets")
            if not isinstance(anchors, dict) or not isinstance(offsets, dict):
                return False
            minimum = anchors.get("minimum")
            maximum = anchors.get("maximum")
            point_anchored = (
                isinstance(minimum, list)
                and isinstance(maximum, list)
                and len(minimum) == len(maximum) == 2
                and all(
                    isinstance(value, (int, float)) and not isinstance(value, bool)
                    for value in minimum + maximum
                )
                and all(abs(float(low) - float(high)) <= 0.000001 for low, high in zip(minimum, maximum))
            )
            return (
                point_anchored
                and slot.get("autoSize") is False
                and isinstance(offsets.get("right"), (int, float))
                and not isinstance(offsets.get("right"), bool)
                and offsets["right"] > 0
                and isinstance(offsets.get("bottom"), (int, float))
                and not isinstance(offsets.get("bottom"), bool)
                and offsets["bottom"] > 0
            )

        if not any(has_nonzero_desired_size_evidence(node) for node in direct_children):
            issue(
                errors,
                "profile.design_size_mode.desired_root_size",
                "$.profile.designSizeMode",
                "Desired requires at least one root-direct child with a point-anchored Canvas slot whose autoSize is false and right/bottom size is positive, or verified contentDrivenSize with positive measuredDesiredSize and a valid evidenceId. Empty roots, auto-sized fixed slots, verified-only claims, and zero-offset full-stretch content cannot establish non-zero Desired Size.",
            )

    child_counts: dict[str, int] = {}
    children_by_parent: dict[str, list[str]] = {}
    for node_id, node in node_by_id.items():
        node_path = f"$.nodes[{node_index_by_id[node_id]}]"
        parent_id = node.get("parent")
        has_flow_slot = "flowSlot" in node
        has_scroll_slot = "scrollSlot" in node
        flow_slot = node.get("flowSlot")
        scroll_slot = node.get("scrollSlot")
        if parent_id is None:
            if has_flow_slot:
                issue(
                    errors,
                    "flow.slot.relationship",
                    f"{node_path}.flowSlot",
                    "flowSlot is only valid on a direct child of container.vertical or container.horizontal.",
                )
            if has_scroll_slot:
                issue(
                    errors,
                    "scroll.slot.relationship",
                    f"{node_path}.scrollSlot",
                    "scrollSlot is only valid on a direct child of container.game-scroll.",
                )
            if node.get("rootLayer") is not None:
                issue(
                    errors,
                    "region.root_layer.parent",
                    f"{node_path}.rootLayer",
                    "rootLayer is only valid on a direct child of the root.",
                )
            continue
        parent = node_by_id.get(parent_id)
        if parent is None:
            if has_flow_slot:
                issue(
                    errors,
                    "flow.slot.relationship",
                    f"{node_path}.flowSlot",
                    "flowSlot cannot be resolved because its direct parent is missing.",
                )
            if has_scroll_slot:
                issue(
                    errors,
                    "scroll.slot.relationship",
                    f"{node_path}.scrollSlot",
                    "scrollSlot cannot be resolved because its direct parent is missing.",
                )
            issue(errors, "tree.parent.missing", f"{node_path}.parent", f"Missing parent node: {parent_id}")
            continue
        parent_component = component_by_role.get(parent.get("role"), {})
        parent_role = parent.get("role")
        parent_is_flow = parent_role in FLOW_PARENT_ROLES
        parent_is_scroll = parent_role == SCROLL_PARENT_ROLE
        if parent_is_flow:
            if not has_flow_slot:
                if explicit_panel_slots:
                    issue(
                        errors,
                        "flow.slot.missing",
                        f"{node_path}.flowSlot",
                        "Every direct VerticalBox or HorizontalBox child must declare flowSlot when explicitPanelSlots is enabled.",
                    )
            else:
                validate_panel_slot(flow_slot, f"{node_path}.flowSlot", "flow", include_size=True)
                if (
                    isinstance(node.get("contentDrivenSize"), dict)
                    and node.get("contentDrivenSize", {}).get("verified") is True
                    and isinstance(flow_slot, dict)
                    and (
                        not isinstance(flow_slot.get("size"), dict)
                        or flow_slot.get("size", {}).get("rule") != "Auto"
                    )
                ):
                    issue(
                        errors,
                        "flow.slot.content_driven_auto",
                        f"{node_path}.flowSlot.size.rule",
                        "A verified content-driven child must use FlowSlot Size Auto; Fill alignment remains independent.",
                    )
        elif has_flow_slot:
            issue(
                errors,
                "flow.slot.relationship",
                f"{node_path}.flowSlot",
                "flowSlot is only valid on a direct child of container.vertical or container.horizontal.",
            )

        if parent_is_scroll:
            if not has_scroll_slot:
                if explicit_panel_slots:
                    issue(
                        errors,
                        "scroll.slot.missing",
                        f"{node_path}.scrollSlot",
                        "Every direct GameScrollBox child must declare scrollSlot when explicitPanelSlots is enabled.",
                    )
            else:
                validate_panel_slot(scroll_slot, f"{node_path}.scrollSlot", "scroll", include_size=False)
        elif has_scroll_slot:
            issue(
                errors,
                "scroll.slot.relationship",
                f"{node_path}.scrollSlot",
                "scrollSlot is only valid on a direct child of container.game-scroll.",
            )

        slot_layout = node.get("slotLayout")
        parent_is_canvas = parent_component.get("classPath") == "/Script/UMG.CanvasPanel"
        if slot_layout is not None and not parent_is_canvas:
            issue(
                errors,
                "slot_layout.parent",
                f"{node_path}.slotLayout",
                "slotLayout is only valid for a direct child of a CanvasPanel.",
            )
        button_slot = node.get("buttonSlot")
        is_direct_button_canvas = (
            parent.get("role") == "input.button"
            and node.get("role") == "container.canvas"
        )
        if is_direct_button_canvas:
            if not isinstance(button_slot, dict):
                issue(
                    errors,
                    "button.direct_canvas.slot.missing",
                    f"{node_path}.buttonSlot",
                    "A direct Button -> CanvasPanel content host must declare a ButtonSlot with zero padding and Fill alignment.",
                )
            else:
                padding = button_slot.get("padding")
                padding_is_zero = (
                    isinstance(padding, list)
                    and len(padding) == 4
                    and all(
                        isinstance(value, (int, float))
                        and not isinstance(value, bool)
                        and value == 0
                        for value in padding
                    )
                )
                if not padding_is_zero:
                    issue(
                        errors,
                        "button.direct_canvas.slot.padding",
                        f"{node_path}.buttonSlot.padding",
                        "A direct Button -> CanvasPanel content host must use ButtonSlot padding [0, 0, 0, 0].",
                    )
                for alignment_name in ("horizontalAlignment", "verticalAlignment"):
                    if button_slot.get(alignment_name) != BUTTON_SLOT_FILL:
                        issue(
                            errors,
                            "button.direct_canvas.slot.alignment",
                            f"{node_path}.buttonSlot.{alignment_name}",
                            "A direct Button -> CanvasPanel content host must use Fill horizontal and vertical ButtonSlot alignment.",
                        )
        elif button_slot is not None:
            issue(
                errors,
                "button_slot.relationship",
                f"{node_path}.buttonSlot",
                "buttonSlot is only valid on a CanvasPanel that is the direct child of an input.button.",
            )
        overlay_slot = node.get("overlaySlot")
        is_direct_overlay_child = parent.get("role") == "container.overlay"
        if is_direct_overlay_child:
            if not isinstance(overlay_slot, dict):
                issue(
                    errors,
                    "overlay.slot.missing",
                    f"{node_path}.overlaySlot",
                    "Every direct Overlay child must declare horizontal and vertical OverlaySlot alignment.",
                )
            else:
                horizontal_alignment = overlay_slot.get("horizontalAlignment")
                vertical_alignment = overlay_slot.get("verticalAlignment")
                if horizontal_alignment not in OVERLAY_HORIZONTAL_ALIGNMENTS:
                    issue(
                        errors,
                        "overlay.slot.horizontal_alignment",
                        f"{node_path}.overlaySlot.horizontalAlignment",
                        "OverlaySlot horizontalAlignment must be Fill, Left, Center, or Right.",
                    )
                if vertical_alignment not in OVERLAY_VERTICAL_ALIGNMENTS:
                    issue(
                        errors,
                        "overlay.slot.vertical_alignment",
                        f"{node_path}.overlaySlot.verticalAlignment",
                        "OverlaySlot verticalAlignment must be Fill, Top, Center, or Bottom.",
                    )
                if normalized_rects_equal(node.get("rect"), parent.get("rect")) and (
                    horizontal_alignment != "Fill" or vertical_alignment != "Fill"
                ):
                    issue(
                        errors,
                        "overlay.slot.full_region_fill",
                        f"{node_path}.overlaySlot",
                        "A child covering the complete Overlay rectangle must use Fill alignment on both axes.",
                    )
        elif overlay_slot is not None:
            issue(
                errors,
                "overlay.slot.relationship",
                f"{node_path}.overlaySlot",
                "overlaySlot is only valid on a direct child of container.overlay.",
            )
        properties = node.get("properties", {})
        wrap_text_at = properties.get("wrapTextAt") if isinstance(properties, dict) else None
        if (
            node.get("role") == "text.label"
            and parent_is_canvas
            and isinstance(wrap_text_at, (int, float))
            and not isinstance(wrap_text_at, bool)
            and wrap_text_at > 0
        ):
            if not isinstance(slot_layout, dict):
                issue(
                    errors,
                    "text.adaptive_slot.missing",
                    f"{node_path}.slotLayout",
                    "A wrapping TextBlock under CanvasPanel must define an adaptive slotLayout.",
                )
            elif slot_layout.get("autoSize") is not True:
                issue(
                    errors,
                    "text.adaptive_slot.auto_size",
                    f"{node_path}.slotLayout.autoSize",
                    "A wrapping TextBlock under CanvasPanel must enable Slot autoSize for text growth.",
                )
        if not parent_component.get("isPanel"):
            issue(errors, "tree.parent.not-panel", f"{node_path}.parent", f"Parent role {parent.get('role')} cannot own children.")
        allowed = parent_component.get("allowedChildren", [])
        if "*" not in allowed and node.get("role") not in allowed:
            issue(errors, "tree.child.disallowed", f"{node_path}.role", f"Role {node.get('role')} is not allowed under {parent.get('role')}.")

        child_rect = node.get("rect")
        parent_rect = parent.get("rect")
        if (
            isinstance(child_rect, list)
            and len(child_rect) == 4
            and all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in child_rect)
            and isinstance(parent_rect, list)
            and len(parent_rect) == 4
            and all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in parent_rect)
        ):
            child_x, child_y, child_width, child_height = child_rect
            parent_x, parent_y, parent_width, parent_height = parent_rect
            epsilon = 0.000001
            if (
                child_x < parent_x - epsilon
                or child_y < parent_y - epsilon
                or child_x + child_width > parent_x + parent_width + epsilon
                or child_y + child_height > parent_y + parent_height + epsilon
            ):
                issue(
                    errors,
                    "tree.rect.outside_parent",
                    f"{node_path}.rect",
                    f"Child rect must remain inside parent node {parent_id}.",
                )

        if node.get("rootLayer") is not None and parent_id not in roots:
            issue(
                errors,
                "region.root_layer.parent",
                f"{node_path}.rootLayer",
                "rootLayer is only valid on a direct child of the root.",
            )
        child_counts[parent_id] = child_counts.get(parent_id, 0) + 1
        children_by_parent.setdefault(parent_id, []).append(node_id)

    for parent_id, count in child_counts.items():
        parent = node_by_id[parent_id]
        maximum = component_by_role.get(parent.get("role"), {}).get("maxChildren")
        if isinstance(maximum, int) and count > maximum:
            parent_path = f"$.nodes[{node_index_by_id[parent_id]}]"
            issue(errors, "tree.children.max", parent_path, f"Node has {count} children but allows at most {maximum}.")

    # Compound text semantics are metadata, not Widget property writes. A ratio
    # group is deliberately explicit so gameplay code can update its current and
    # maximum values independently while the separator remains fixed decoration.
    for node_id, node in node_by_id.items():
        text_group = node.get("textGroup")
        if text_group is None:
            continue
        node_path = f"$.nodes[{node_index_by_id[node_id]}]"
        if not isinstance(text_group, dict):
            issue(errors, "text.group.type", f"{node_path}.textGroup", "textGroup must be an object.")
            continue
        if text_group.get("kind") != RATIO_GROUP_KIND:
            issue(errors, "text.ratio.kind", f"{node_path}.textGroup.kind", "Only the ratio text-group kind is supported.")
        if node.get("role") != "container.horizontal":
            issue(errors, "text.ratio.role", f"{node_path}.role", "A ratio text group must use a HorizontalBox container.")
        if text_group.get("alignment") not in TEXT_JUSTIFICATIONS:
            issue(errors, "text.ratio.alignment", f"{node_path}.textGroup.alignment", "A ratio text group must declare design alignment as Left, Center, or Right.")

        ordered_children = text_group.get("orderedChildren")
        if not (
            isinstance(ordered_children, list)
            and len(ordered_children) == 3
            and all(isinstance(child_id, str) for child_id in ordered_children)
            and len(set(ordered_children)) == 3
        ):
            issue(errors, "text.ratio.children", f"{node_path}.textGroup.orderedChildren", "A ratio text group must name exactly three distinct children in current, separator, maximum order.")
            continue

        actual_children = children_by_parent.get(node_id, [])
        if actual_children != ordered_children:
            issue(errors, "text.ratio.child_order", f"{node_path}.textGroup.orderedChildren", "A ratio group must directly own only current, separator, and maximum TextBlocks in the declared order.")

        for child_position, child_id in enumerate(ordered_children):
            child = node_by_id.get(child_id)
            child_path = f"$.nodes[{node_index_by_id[child_id]}]" if child_id in node_index_by_id else f"{node_path}.textGroup.orderedChildren[{child_position}]"
            if not isinstance(child, dict):
                issue(errors, "text.ratio.child.missing", child_path, "A ratio group child id must reference an existing node.")
                continue
            if child.get("parent") != node_id:
                issue(errors, "text.ratio.child.direct", child_path, "Each ratio group child must be a direct child of the HorizontalBox.")
            if child.get("role") != "text.label":
                issue(errors, "text.ratio.child.role", child_path, "Each ratio group child must be a TextBlock.")
            is_separator = child_position == 1
            if is_separator:
                if child.get("isVariable", False) is not False:
                    issue(errors, "text.ratio.separator.variable", f"{child_path}.isVariable", "The ratio separator is fixed decoration and must not be Is Variable.")
                properties = child.get("properties")
                if not isinstance(properties, dict) or properties.get("text") != "/":
                    issue(errors, "text.ratio.separator.text", f"{child_path}.properties.text", "The ratio separator TextBlock must contain exactly '/'.")
            elif child.get("isVariable") is not True:
                issue(errors, "text.ratio.value.variable", f"{child_path}.isVariable", "The ratio current and maximum TextBlocks must be Is Variable.")

    # A full-height marker protects a screen shell or its background from ending
    # at the design-time height when a taller viewport is used. Every CanvasPanel
    # slot from the marked node through its Canvas ancestors must remain a 0..1
    # vertical stretch with auto-size disabled.
    for node_id, node in node_by_id.items():
        full_height = node.get("fullHeight")
        if full_height is None or full_height is False:
            continue
        node_path = f"$.nodes[{node_index_by_id[node_id]}]"
        if not isinstance(full_height, bool):
            issue(errors, "layout.full_height.type", f"{node_path}.fullHeight", "fullHeight must be a boolean marker.")
            continue
        parent_id = node.get("parent")
        parent = node_by_id.get(parent_id) if isinstance(parent_id, str) else None
        parent_component = component_by_role.get(parent.get("role"), {}) if isinstance(parent, dict) else {}
        if parent_component.get("classPath") != "/Script/UMG.CanvasPanel":
            issue(errors, "layout.full_height.parent", f"{node_path}.fullHeight", "A fullHeight node must be directly hosted by a CanvasPanel so its vertical stretch can be verified.")
            continue

        current_id: str | None = node_id
        visited: set[str] = set()
        while isinstance(current_id, str) and current_id not in visited:
            visited.add(current_id)
            current = node_by_id.get(current_id)
            if not isinstance(current, dict):
                break
            current_parent_id = current.get("parent")
            if not isinstance(current_parent_id, str):
                break
            current_parent = node_by_id.get(current_parent_id)
            current_parent_component = component_by_role.get(current_parent.get("role"), {}) if isinstance(current_parent, dict) else {}
            if current_parent_component.get("classPath") == "/Script/UMG.CanvasPanel":
                slot_layout = current.get("slotLayout")
                anchors = slot_layout.get("anchors") if isinstance(slot_layout, dict) else None
                minimum = anchors.get("minimum") if isinstance(anchors, dict) else None
                maximum = anchors.get("maximum") if isinstance(anchors, dict) else None
                stretches_vertically = (
                    isinstance(minimum, list)
                    and isinstance(maximum, list)
                    and len(minimum) == 2
                    and len(maximum) == 2
                    and minimum[1] == 0
                    and maximum[1] == 1
                    and isinstance(slot_layout, dict)
                    and slot_layout.get("autoSize") is False
                )
                if not stretches_vertically:
                    current_path = f"$.nodes[{node_index_by_id[current_id]}]"
                    issue(errors, "layout.full_height.stretch", f"{current_path}.slotLayout", "A fullHeight stretch chain requires Canvas anchors minimum.y=0, maximum.y=1, and autoSize=false on the marked node and every Canvas ancestor.")
            current_id = current_parent_id

    # Canvas Slot offsets are always local to the direct parent CanvasPanel.
    # Compare explicit offsets with the node rectangles to catch stale screen-space
    # values after reparenting into a nested region.
    ref_w, ref_h = reference_size if isinstance(reference_size, list) else (None, None)
    if isinstance(ref_w, (int, float)) and isinstance(ref_h, (int, float)):
        tolerance = 1.0
        for node_id, node in node_by_id.items():
            slot = node.get("slotLayout")
            parent_id = node.get("parent")
            parent = node_by_id.get(parent_id) if isinstance(parent_id, str) else None
            if not isinstance(slot, dict) or not isinstance(parent, dict) or parent.get("role") not in component_by_role:
                continue
            if component_by_role.get(parent.get("role"), {}).get("classPath") != "/Script/UMG.CanvasPanel":
                continue
            # Root Canvas coordinates are the screen frame; nested Canvas children
            # are the regression boundary where global offsets are most dangerous.
            if parent_id == (roots[0] if roots else None):
                continue
            anchors = slot.get("anchors", {})
            offsets = slot.get("offsets", {})
            alignment = slot.get("alignment", [0, 0])
            child_rect = node.get("rect")
            parent_rect = parent.get("rect")
            if not (isinstance(child_rect, list) and isinstance(parent_rect, list) and len(child_rect) == 4 and len(parent_rect) == 4):
                continue
            minimum = anchors.get("minimum") if isinstance(anchors, dict) else None
            maximum = anchors.get("maximum") if isinstance(anchors, dict) else None
            if not (isinstance(minimum, list) and isinstance(maximum, list) and len(minimum) == 2 and len(maximum) == 2):
                continue
            parent_left, parent_top, parent_width, parent_height = (parent_rect[0] * ref_w, parent_rect[1] * ref_h, parent_rect[2] * ref_w, parent_rect[3] * ref_h)
            child_left, child_top, child_width, child_height = (child_rect[0] * ref_w, child_rect[1] * ref_h, child_rect[2] * ref_w, child_rect[3] * ref_h)
            expected_left = child_left - parent_left - minimum[0] * parent_width + alignment[0] * child_width
            expected_top = child_top - parent_top - minimum[1] * parent_height + alignment[1] * child_height
            node_path = f"$.nodes[{node_index_by_id[node_id]}].slotLayout"
            if isinstance(offsets.get("left"), (int, float)) and abs(offsets["left"] - expected_left) > tolerance:
                issue(errors, "slot_layout.local_coordinates", f"{node_path}.offsets.left", "Canvas Slot left offset must be expressed in the direct parent Panel's local coordinates, not screen coordinates.")
            if isinstance(offsets.get("top"), (int, float)) and abs(offsets["top"] - expected_top) > tolerance:
                issue(errors, "slot_layout.local_coordinates", f"{node_path}.offsets.top", "Canvas Slot top offset must be expressed in the direct parent Panel's local coordinates, not screen coordinates.")
            if slot.get("autoSize") is not True:
                if maximum[0] == minimum[0] and isinstance(offsets.get("right"), (int, float)) and abs(offsets["right"] - child_width) > tolerance:
                    issue(errors, "slot_layout.local_coordinates", f"{node_path}.offsets.right", "Point-anchored Canvas Slot width must use the child size in local coordinates.")
                if maximum[1] == minimum[1] and isinstance(offsets.get("bottom"), (int, float)) and abs(offsets["bottom"] - child_height) > tolerance:
                    issue(errors, "slot_layout.local_coordinates", f"{node_path}.offsets.bottom", "Point-anchored Canvas Slot height must use the child size in local coordinates.")
                if maximum[0] > minimum[0] and isinstance(offsets.get("right"), (int, float)):
                    expected_right = maximum[0] * parent_width - ((child_left - parent_left) + child_width)
                    if abs(offsets["right"] - expected_right) > tolerance:
                        issue(errors, "slot_layout.local_coordinates", f"{node_path}.offsets.right", "Stretched Canvas Slot right margin must be local to the direct parent Panel.")
                if maximum[1] > minimum[1] and isinstance(offsets.get("bottom"), (int, float)):
                    expected_bottom = maximum[1] * parent_height - ((child_top - parent_top) + child_height)
                    if abs(offsets["bottom"] - expected_bottom) > tolerance:
                        issue(errors, "slot_layout.local_coordinates", f"{node_path}.offsets.bottom", "Stretched Canvas Slot bottom margin must be local to the direct parent Panel.")

    if asset_kind == "screen" and isinstance(profile, dict) and profile.get("adaptive") is True and len(roots) == 1:
        root_id = roots[0]
        adaptive_review_ids = set(children_by_parent.get(root_id, []))
        for node_id, node in node_by_id.items():
            if node.get("role") in {"collection.lua-list", "collection.lua-tile"}:
                adaptive_review_ids.add(node_id)
            slot_layout = node.get("slotLayout")
            if not isinstance(slot_layout, dict):
                continue
            anchors = slot_layout.get("anchors")
            if not isinstance(anchors, dict):
                continue
            minimum = anchors.get("minimum")
            maximum = anchors.get("maximum")
            if (
                isinstance(minimum, list)
                and len(minimum) == 2
                and isinstance(maximum, list)
                and len(maximum) == 2
                and all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in minimum + maximum)
                and (maximum[0] - minimum[0] > 0.000001 or maximum[1] - minimum[1] > 0.000001)
            ):
                adaptive_review_ids.add(node_id)
        for node_id in adaptive_review_ids:
            node = node_by_id[node_id]
            if node.get("adaptiveLayout") is None:
                node_path = f"$.nodes[{node_index_by_id[node_id]}]"
                issue(
                    warnings,
                    "layout.adaptive_intent.missing",
                    f"{node_path}.adaptiveLayout",
                    "Adaptive screen regions and root layers should declare horizontal and vertical adaptation intent.",
                )

    for node_id, node in node_by_id.items():
        adaptive_layout = node.get("adaptiveLayout")
        if not isinstance(adaptive_layout, dict):
            continue
        node_path = f"$.nodes[{node_index_by_id[node_id]}]"
        parent_id = node.get("parent")
        parent = node_by_id.get(parent_id) if isinstance(parent_id, str) else None
        parent_component = component_by_role.get(parent.get("role"), {}) if isinstance(parent, dict) else {}
        parent_role = parent.get("role") if isinstance(parent, dict) else None
        parent_is_canvas = parent_component.get("classPath") == "/Script/UMG.CanvasPanel"
        parent_is_flow = parent_role in FLOW_PARENT_ROLES
        parent_is_scroll = parent_role == SCROLL_PARENT_ROLE
        parent_is_overlay = parent_role == "container.overlay"
        if not (parent_is_canvas or parent_is_flow or parent_is_scroll or parent_is_overlay):
            issue(
                warnings,
                "layout.adaptive_intent.non_canvas",
                f"{node_path}.adaptiveLayout",
                "Adaptive intent cannot be mechanically checked because the parent has no supported explicit Slot contract.",
            )
            continue

        actual_horizontal: str | None = None
        actual_vertical: str | None = None
        if parent_is_canvas:
            slot_layout = node.get("slotLayout")
            if isinstance(slot_layout, dict):
                anchors = slot_layout.get("anchors")
                if isinstance(anchors, dict):
                    minimum = anchors.get("minimum")
                    maximum = anchors.get("maximum")
                    if (
                        isinstance(minimum, list)
                        and len(minimum) == 2
                        and isinstance(maximum, list)
                        and len(maximum) == 2
                        and all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in minimum + maximum)
                    ):
                        actual_horizontal = slot_axis_intent(float(minimum[0]), float(maximum[0]), "horizontal")
                        actual_vertical = slot_axis_intent(float(minimum[1]), float(maximum[1]), "vertical")
            else:
                anchor = node.get("anchor")
                if anchor == "auto":
                    issue(
                        warnings,
                        "layout.adaptive_intent.auto_anchor",
                        f"{node_path}.anchor",
                        "Use an explicit anchor when adaptiveLayout records a semantic adaptation decision.",
                    )
                elif anchor in ANCHOR_INTENTS:
                    actual_horizontal, actual_vertical = ANCHOR_INTENTS[anchor]
        else:
            panel_slot = (
                node.get("flowSlot")
                if parent_is_flow
                else node.get("scrollSlot")
                if parent_is_scroll
                else node.get("overlaySlot")
            )
            if not isinstance(panel_slot, dict):
                slot_field = "flowSlot" if parent_is_flow else "scrollSlot" if parent_is_scroll else "overlaySlot"
                issue(
                    warnings,
                    "layout.adaptive_intent.panel_slot_missing",
                    f"{node_path}.{slot_field}",
                    "Adaptive intent needs explicit child Slot alignment before it can be mechanically checked.",
                )
                continue
            actual_horizontal = panel_alignment_intent(panel_slot.get("horizontalAlignment"), "horizontal")
            actual_vertical = panel_alignment_intent(panel_slot.get("verticalAlignment"), "vertical")

        for axis, actual in (("horizontal", actual_horizontal), ("vertical", actual_vertical)):
            declared = adaptive_layout.get(axis)
            if declared in (ADAPTIVE_HORIZONTAL if axis == "horizontal" else ADAPTIVE_VERTICAL):
                if actual is None:
                    issue(
                        errors,
                        "layout.adaptive_intent.unresolved",
                        f"{node_path}.adaptiveLayout.{axis}",
                        f"The {axis} adaptive intent cannot be resolved from the parent Slot behavior.",
                    )
                elif declared != actual:
                    issue(
                        errors,
                        "layout.adaptive_intent.mismatch",
                        f"{node_path}.adaptiveLayout.{axis}",
                        f"Declared {axis} intent {declared!r} does not match parent Slot behavior {actual!r}.",
                    )

    for node_id, node in node_by_id.items():
        if node.get("role") != "container.overlay":
            continue
        node_path = f"$.nodes[{node_index_by_id[node_id]}]"
        child_ids = children_by_parent.get(node_id, [])
        purpose = node.get("overlayPurpose")
        if len(child_ids) == 1 and purpose is None:
            issue(
                warnings,
                "structure.overlay.one_child",
                node_path,
                "A one-child Overlay needs a documented layout purpose or should be removed.",
            )
        parent_id = node.get("parent")
        parent = node_by_id.get(parent_id) if isinstance(parent_id, str) else None
        child = node_by_id.get(child_ids[0]) if len(child_ids) == 1 else None
        if (
            isinstance(parent, dict)
            and parent.get("role") == "input.button"
            and isinstance(child, dict)
            and child.get("role") == "container.canvas"
        ):
            issue(
                errors,
                "structure.overlay.redundant_button_canvas",
                node_path,
                "A one-child Button -> Overlay -> CanvasPanel chain is redundant even when labeled; make CanvasPanel the Button's direct child.",
            )

    for node_id, node in node_by_id.items():
        if node.get("regionPurpose") is None:
            continue
        component = component_by_role.get(node.get("role"), {})
        if component.get("isPanel") and child_counts.get(node_id, 0) == 0:
            node_path = f"$.nodes[{node_index_by_id[node_id]}]"
            issue(
                errors,
                "region.empty",
                node_path,
                "A PanelWidget-based region must contain at least one child.",
            )

    for start_id in node_by_id:
        seen: set[str] = set()
        current: str | None = start_id
        while current is not None:
            if current in seen:
                start_path = f"$.nodes[{node_index_by_id[start_id]}]"
                issue(errors, "tree.cycle", start_path, f"Parent cycle detected at {current}.")
                break
            seen.add(current)
            current_node = node_by_id.get(current)
            current = current_node.get("parent") if current_node else None

    if region_grouping is True:
        valid_region_ids = {
            node_id
            for node_id, node in node_by_id.items()
            if isinstance(node.get("regionPurpose"), str)
            and REGION_PURPOSE_PATTERN.fullmatch(node["regionPurpose"])
            and component_by_role.get(node.get("role"), {}).get("canRepresentRegion")
        }
        if not valid_region_ids:
            issue(
                errors,
                "region.required",
                "$.nodes",
                "regionGrouping is true but no valid region container was declared.",
            )

        root_id = roots[0] if len(roots) == 1 else None
        for node_id, node in node_by_id.items():
            if node_id == root_id:
                continue
            node_path = f"$.nodes[{node_index_by_id[node_id]}]"
            parent_id = node.get("parent")
            root_layer = node.get("rootLayer")

            if parent_id == root_id and root_layer is None and node_id not in valid_region_ids:
                issue(
                    errors,
                    "region.root_child.ungrouped",
                    node_path,
                    "Every non-global direct child of the root must be a region container.",
                )
                continue

            if root_layer is not None:
                continue

            has_region_ancestor = False
            seen: set[str] = set()
            current: str | None = node_id
            while current is not None and current not in seen:
                seen.add(current)
                if current in valid_region_ids:
                    has_region_ancestor = True
                    break
                current_node = node_by_id.get(current)
                current = current_node.get("parent") if current_node else None
            if not has_region_ancestor:
                issue(
                    errors,
                    "region.content.ungrouped",
                    node_path,
                    "Non-global content must be inside a declared region container.",
                )

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "summary": {"nodes": len(nodes), "errors": len(errors), "warnings": len(warnings)},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    args = parser.parse_args()
    try:
        report = validate_spec(load_json(args.spec), load_json(args.catalog))
    except (OSError, json.JSONDecodeError) as exc:
        report = {"valid": False, "errors": [{"code": "input.read", "path": "$", "message": str(exc)}], "warnings": []}
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report.get("valid") else 1


if __name__ == "__main__":
    sys.exit(main())
