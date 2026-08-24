#!/usr/bin/env python3
"""Validate UILayoutSpec and emit an ordered, reference-aware Unreal MCP call plan."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from select_rules import select_rules
from validate_layout_spec import load_json, validate_spec

SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CATALOG = SKILL_ROOT / "references" / "component-catalog.json"
DEFAULT_RULES = SKILL_ROOT / "references" / "rule-index.json"
DEFAULT_PARENT_CLASS = "/Script/UMG.UserWidget"


def target_widget_basename(spec: dict[str, Any]) -> str | None:
    """Resolve the basename of the Widget Blueprint that this plan will mutate."""

    asset = spec.get("asset")
    if spec.get("mode") == "prototype":
        return asset.get("name") if isinstance(asset, dict) and isinstance(asset.get("name"), str) else None
    profile = spec.get("profile", {})
    target = profile.get("targetAsset") if isinstance(profile, dict) else None
    if isinstance(target, dict) and isinstance(target.get("name"), str):
        return target["name"]
    if isinstance(asset, dict) and isinstance(asset.get("name"), str):
        return asset["name"]
    return None


def effective_design_size_mode(spec: dict[str, Any]) -> str:
    """Return the target-safe analyzed mode or conservative FillScreen fallback."""

    return str(design_size_mode_resolution(spec)["mode"])


def design_size_mode_resolution(spec: dict[str, Any]) -> dict[str, Any]:
    """Describe the provenance of the executable Designer mode."""

    explicit = spec.get("profile", {}).get("designSizeMode")
    target_name = target_widget_basename(spec)
    if isinstance(target_name, str) and target_name.startswith("umg_"):
        missing_explicit_mode = explicit is None
        return {
            "mode": "FillScreen",
            "source": "umg-target-hard-rule",
            "fallbackApplied": missing_explicit_mode,
            "reason": (
                f"Target {target_name} is an umg_* Widget Blueprint; "
                + ("its archived mode is missing, so resolve it to hard-rule FillScreen." if missing_explicit_mode else "it must use FillScreen.")
            ),
        }
    if isinstance(target_name, str) and target_name.startswith("uw_") and explicit in {"FillScreen", "Desired"}:
        return {
            "mode": explicit,
            "source": "explicit-analysis",
            "fallbackApplied": False,
            "reason": f"Executed the explicit profile.designSizeMode analysis decision for uw_* target {target_name}.",
        }
    if isinstance(target_name, str) and target_name.startswith("uw_"):
        return {
            "mode": "FillScreen",
            "source": "fallback-unclear",
            "fallbackApplied": True,
            "reason": f"Target {target_name} has no explicit analyzed Designer mode; use FillScreen.",
        }
    return {
        "mode": "FillScreen",
        "source": "fallback-unknown-target",
        "fallbackApplied": True,
        "reason": (
            "The formal target basename is unknown or does not use a recognized umg_/uw_ contract; "
            "use the conservative FillScreen default."
        ),
    }

ANCHOR_POINTS = {
    "left-top": (0.0, 0.0),
    "center-top": (0.5, 0.0),
    "right-top": (1.0, 0.0),
    "left-center": (0.0, 0.5),
    "center": (0.5, 0.5),
    "right-center": (1.0, 0.5),
    "left-bottom": (0.0, 1.0),
    "center-bottom": (0.5, 1.0),
    "right-bottom": (1.0, 1.0),
}


def auto_anchor(rect: list[float]) -> str:
    x, y, width, height = rect
    center_x = x + width / 2
    center_y = y + height / 2
    horizontal = "left" if center_x < 1 / 3 else "right" if center_x > 2 / 3 else "center"
    vertical = "top" if center_y < 1 / 3 else "bottom" if center_y > 2 / 3 else "center"
    if horizontal == "center" and vertical == "center":
        return "center"
    return f"{horizontal}-{vertical}"


def canvas_layout(rect: list[float], anchor_name: str, reference_size: list[int], z_order: int) -> dict[str, Any]:
    if anchor_name == "auto":
        anchor_name = auto_anchor(rect)
    anchor_x, anchor_y = ANCHOR_POINTS[anchor_name]
    x, y, width, height = rect
    reference_width, reference_height = reference_size
    size_x = width * reference_width
    size_y = height * reference_height
    alignment_x = anchor_x
    alignment_y = anchor_y
    position_x = (x + width * alignment_x - anchor_x) * reference_width
    position_y = (y + height * alignment_y - anchor_y) * reference_height
    return {
        "layoutData": {
            "offsets": {"left": position_x, "top": position_y, "right": size_x, "bottom": size_y},
            "anchors": {
                "minimum": {"x": anchor_x, "y": anchor_y},
                "maximum": {"x": anchor_x, "y": anchor_y},
            },
            "alignment": {"x": alignment_x, "y": alignment_y},
        },
        "bAutoSize": False,
        "zOrder": z_order,
    }


def explicit_canvas_layout(slot_layout: dict[str, Any], z_order: int) -> dict[str, Any]:
    minimum = slot_layout["anchors"]["minimum"]
    maximum = slot_layout["anchors"]["maximum"]
    alignment = slot_layout["alignment"]
    return {
        "layoutData": {
            "offsets": dict(slot_layout["offsets"]),
            "anchors": {
                "minimum": {"x": minimum[0], "y": minimum[1]},
                "maximum": {"x": maximum[0], "y": maximum[1]},
            },
            "alignment": {"x": alignment[0], "y": alignment[1]},
        },
        "bAutoSize": slot_layout["autoSize"],
        "zOrder": z_order,
    }


def rect_relative_to_parent(rect: list[float], parent_rect: list[float]) -> list[float]:
    x, y, width, height = rect
    parent_x, parent_y, parent_width, parent_height = parent_rect
    return [
        (x - parent_x) / parent_width,
        (y - parent_y) / parent_height,
        width / parent_width,
        height / parent_height,
    ]


def ordered_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {node["id"]: node for node in nodes}
    result: list[dict[str, Any]] = []
    visited: set[str] = set()

    def visit(node: dict[str, Any]) -> None:
        if node["id"] in visited:
            return
        parent_id = node.get("parent")
        if parent_id is not None:
            visit(by_id[parent_id])
        visited.add(node["id"])
        result.append(node)

    for item in nodes:
        visit(item)
    return result


def tool_step(step_id: str, toolset: str, tool: str, arguments: dict[str, Any], **extra: Any) -> dict[str, Any]:
    result = {
        "stepId": step_id,
        "operation": "call_tool",
        "toolsetName": toolset,
        "toolName": tool,
        "arguments": arguments,
    }
    result.update(extra)
    return result


def build_plan(spec_path: Path, spec: dict[str, Any], catalog: dict[str, Any], rules: dict[str, Any]) -> dict[str, Any]:
    validation = validate_spec(spec, catalog)
    if not validation["valid"]:
        error_codes = ", ".join(
            str(entry.get("code", "unknown"))
            for entry in validation.get("errors", [])
            if isinstance(entry, dict)
        )
        raise ValueError(f"UILayoutSpec validation failed before planning: {error_codes or 'unknown error'}")
    component_by_role = {item["role"]: item for item in catalog["components"]}
    common_property_map = catalog.get("commonPropertyMap", {})
    nodes = ordered_nodes(spec["nodes"])
    node_by_id = {node["id"]: node for node in nodes}
    folder = spec["asset"]["folder"]
    asset_name = spec["asset"]["name"]
    package_path = f"{folder}/{asset_name}"
    object_path = f"{package_path}.{asset_name}"
    parent_class = spec.get("profile", {}).get(
        "parentClass",
        DEFAULT_PARENT_CLASS,
    )
    selected = select_rules(spec, rules)
    warnings = list(spec.get("notes", []))
    steps: list[dict[str, Any]] = []
    design_size_mode_result = design_size_mode_resolution(spec)
    design_size_mode = design_size_mode_result["mode"]
    if design_size_mode_result["fallbackApplied"]:
        warnings.append(f"{design_size_mode_result['source']}: {design_size_mode_result['reason']}")

    steps.append(tool_step(
        "check-destination",
        "UMGToolSet.UMGToolSet",
        "ListWidgetBlueprints",
        {"folderPath": folder},
        assertion=f"Stop if {object_path} already exists unless update was explicitly authorized.",
    ))
    steps.append(tool_step(
        "create-blueprint",
        "UMGToolSet.UMGToolSet",
        "CreateWidgetBlueprint",
        {
            "folderPath": folder,
            "assetName": asset_name,
            "parentClass": {"refPath": parent_class},
        },
        saveResultAs="blueprint",
    ))

    for node in nodes:
        component = component_by_role[node["role"]]
        node_id = node["id"]
        add_arguments: dict[str, Any] = {
            "widgetBlueprint": {"refPath": "${blueprint.returnValue.refPath}"},
            "widgetClass": {"refPath": component["classPath"]},
            "widgetDisplayName": node["name"],
            "childIndex": -1,
        }
        if node.get("parent") is not None:
            add_arguments["parentWidget"] = {"refPath": f"${{node.{node['parent']}.returnValue.widget.refPath}}"}
        steps.append(tool_step(
            f"add-{node_id}",
            "UMGToolSet.UMGToolSet",
            "AddWidget",
            add_arguments,
            saveResultAs=f"node.{node_id}",
        ))

        # Newly added UMG widgets do not share a reliable default for bIsVariable.
        # Reconcile every node; omitted isVariable means a deliberate false.
        steps.append(tool_step(
            f"toggle-widget-variable-{node_id}",
            "UMGToolSet.UMGToolSet",
            "ToggleWidgetAsVariable",
            {
                "widgetBlueprint": {"refPath": "${blueprint.returnValue.refPath}"},
                "widget": {"refPath": f"${{node.{node_id}.returnValue.widget.refPath}}"},
                "bIsVariable": bool(node.get("isVariable", False)),
            },
        ))

        mapped_values: dict[str, Any] = {}
        property_map = dict(common_property_map)
        property_map.update(component.get("propertyMap", {}))
        canonical_values = dict(node.get("properties", {}))
        if (
            component.get("hitTestPolicy") == "passive"
            and "visibility" not in canonical_values
        ):
            canonical_values["visibility"] = "SelfHitTestInvisible"
        write_unsupported = set(component.get("writeUnsupportedProperties", []))
        for canonical_name, value in canonical_values.items():
            if canonical_name in write_unsupported:
                guidance = component.get("writeUnsupportedGuidance", {}).get(
                    canonical_name,
                    "Use a separately verified supported route before claiming the property was applied.",
                )
                warnings.append(
                    f"{node_id}: {canonical_name} is read-only through the current MCP. {guidance}"
                )
                continue
            unreal_name = property_map[canonical_name]
            # UTextBlock::ColorAndOpacity is FSlateColor, while GameImage and
            # other visual widgets expose a plain FLinearColor under the same
            # canonical ``color`` name.  Passing {r,g,b,a} directly to a
            # TextBlock is accepted by the generic property tool but leaves
            # the engine's white SlateColor unchanged.  Lower the complete
            # explicit SlateColor shape only for text.
            if component.get("role") == "text.label" and canonical_name == "color":
                mapped_values[unreal_name] = {
                    "specifiedColor": value,
                    "colorUseRule": "UseColor_Specified",
                }
            else:
                mapped_values[unreal_name] = value
        if mapped_values:
            property_names = list(mapped_values)
            instance = {"refPath": f"${{node.{node_id}.returnValue.widget.refPath}}"}
            steps.append(tool_step(f"list-widget-properties-{node_id}", "editor_toolset.toolsets.object.ObjectTools", "list_properties", {"instance": instance}))
            steps.append(tool_step(f"get-widget-properties-{node_id}", "editor_toolset.toolsets.object.ObjectTools", "get_properties", {"instance": instance, "properties": property_names}))
            steps.append(tool_step(
                f"set-widget-properties-{node_id}",
                "editor_toolset.toolsets.object.ObjectTools",
                "set_properties",
                {"instance": instance, "values": mapped_values},
                instruction="Serialize values as a compact JSON string after confirming exact property names.",
                assertion="returnValue must be true",
            ))

        parent_id = node.get("parent")
        if parent_id is not None:
            parent_component = component_by_role[node_by_id[parent_id]["role"]]
            if parent_component["classPath"] == "/Script/UMG.CanvasPanel":
                slot_instance = {"refPath": f"${{node.{node_id}.returnValue.slot.refPath}}"}
                parent_rect = node_by_id[parent_id]["rect"]
                local_rect = rect_relative_to_parent(node["rect"], parent_rect)
                parent_reference_size = [
                    spec["referenceSize"][0] * parent_rect[2],
                    spec["referenceSize"][1] * parent_rect[3],
                ]
                if node.get("slotLayout") is not None:
                    layout_values = explicit_canvas_layout(
                        node["slotLayout"],
                        int(node.get("zOrder", 0)),
                    )
                else:
                    layout_values = canvas_layout(local_rect, node["anchor"], parent_reference_size, int(node.get("zOrder", 0)))
                steps.append(tool_step(f"list-slot-properties-{node_id}", "editor_toolset.toolsets.object.ObjectTools", "list_properties", {"instance": slot_instance}))
                steps.append(tool_step(f"get-slot-properties-{node_id}", "editor_toolset.toolsets.object.ObjectTools", "get_properties", {"instance": slot_instance, "properties": ["layoutData", "bAutoSize", "zOrder"]}))
                steps.append(tool_step(
                    f"set-slot-properties-{node_id}",
                    "editor_toolset.toolsets.object.ObjectTools",
                    "set_properties",
                    {"instance": slot_instance, "values": layout_values},
                    instruction="Serialize values as a compact JSON string after confirming exact property names.",
                    assertion="returnValue must be true",
                ))
            elif (
                parent_component["role"] in {"container.vertical", "container.horizontal"}
                and isinstance(node.get("flowSlot"), dict)
            ):
                flow_slot = node["flowSlot"]
                slot_instance = {"refPath": f"${{node.{node_id}.returnValue.slot.refPath}}"}
                padding = flow_slot["padding"]
                size = flow_slot["size"]
                size_rule = size["rule"]
                flow_slot_values = {
                    "size": {
                        "value": 1 if size_rule == "Auto" else size["weight"],
                        "sizeRule": "Automatic" if size_rule == "Auto" else "Fill",
                    },
                    "padding": {
                        "left": padding[0],
                        "top": padding[1],
                        "right": padding[2],
                        "bottom": padding[3],
                    },
                    "horizontalAlignment": f"HAlign_{flow_slot['horizontalAlignment']}",
                    "verticalAlignment": f"VAlign_{flow_slot['verticalAlignment']}",
                }
                steps.append(tool_step(f"list-flow-slot-properties-{node_id}", "editor_toolset.toolsets.object.ObjectTools", "list_properties", {"instance": slot_instance}))
                steps.append(tool_step(f"get-flow-slot-properties-{node_id}", "editor_toolset.toolsets.object.ObjectTools", "get_properties", {"instance": slot_instance, "properties": list(flow_slot_values)}))
                steps.append(tool_step(
                    f"set-flow-slot-properties-{node_id}",
                    "editor_toolset.toolsets.object.ObjectTools",
                    "set_properties",
                    {"instance": slot_instance, "values": flow_slot_values},
                    instruction="Serialize values as a compact JSON string after confirming exact VerticalBoxSlot or HorizontalBoxSlot property names.",
                    assertion="returnValue must be true",
                ))
            elif (
                parent_component["role"] == "container.game-scroll"
                and isinstance(node.get("scrollSlot"), dict)
            ):
                scroll_slot = node["scrollSlot"]
                slot_instance = {"refPath": f"${{node.{node_id}.returnValue.slot.refPath}}"}
                padding = scroll_slot["padding"]
                scroll_slot_values = {
                    "padding": {
                        "left": padding[0],
                        "top": padding[1],
                        "right": padding[2],
                        "bottom": padding[3],
                    },
                    "horizontalAlignment": f"HAlign_{scroll_slot['horizontalAlignment']}",
                    "verticalAlignment": f"VAlign_{scroll_slot['verticalAlignment']}",
                }
                steps.append(tool_step(f"list-scroll-slot-properties-{node_id}", "editor_toolset.toolsets.object.ObjectTools", "list_properties", {"instance": slot_instance}))
                steps.append(tool_step(f"get-scroll-slot-properties-{node_id}", "editor_toolset.toolsets.object.ObjectTools", "get_properties", {"instance": slot_instance, "properties": list(scroll_slot_values)}))
                steps.append(tool_step(
                    f"set-scroll-slot-properties-{node_id}",
                    "editor_toolset.toolsets.object.ObjectTools",
                    "set_properties",
                    {"instance": slot_instance, "values": scroll_slot_values},
                    instruction="Serialize values as a compact JSON string after confirming exact ScrollBoxSlot property names.",
                    assertion="returnValue must be true",
                ))
            elif parent_component["role"] == "container.overlay":
                overlay_slot = node["overlaySlot"]
                slot_instance = {"refPath": f"${{node.{node_id}.returnValue.slot.refPath}}"}
                overlay_slot_values = {
                    "horizontalAlignment": f"HAlign_{overlay_slot['horizontalAlignment']}",
                    "verticalAlignment": f"VAlign_{overlay_slot['verticalAlignment']}",
                }
                steps.append(tool_step(f"list-overlay-slot-properties-{node_id}", "editor_toolset.toolsets.object.ObjectTools", "list_properties", {"instance": slot_instance}))
                steps.append(tool_step(f"get-overlay-slot-properties-{node_id}", "editor_toolset.toolsets.object.ObjectTools", "get_properties", {"instance": slot_instance, "properties": list(overlay_slot_values)}))
                steps.append(tool_step(
                    f"set-overlay-slot-properties-{node_id}",
                    "editor_toolset.toolsets.object.ObjectTools",
                    "set_properties",
                    {"instance": slot_instance, "values": overlay_slot_values},
                    instruction="Serialize values as a compact JSON string after confirming exact OverlaySlot property names.",
                    assertion="returnValue must be true",
                ))
            elif (
                parent_component["role"] == "input.button"
                and node["role"] == "container.canvas"
            ):
                button_slot = node["buttonSlot"]
                slot_instance = {"refPath": f"${{node.{node_id}.returnValue.slot.refPath}}"}
                padding = button_slot["padding"]
                button_slot_values = {
                    "padding": {
                        "left": padding[0],
                        "top": padding[1],
                        "right": padding[2],
                        "bottom": padding[3],
                    },
                    "horizontalAlignment": "HAlign_Fill",
                    "verticalAlignment": "VAlign_Fill",
                }
                steps.append(tool_step(f"list-button-slot-properties-{node_id}", "editor_toolset.toolsets.object.ObjectTools", "list_properties", {"instance": slot_instance}))
                steps.append(tool_step(f"get-button-slot-properties-{node_id}", "editor_toolset.toolsets.object.ObjectTools", "get_properties", {"instance": slot_instance, "properties": list(button_slot_values)}))
                steps.append(tool_step(
                    f"set-button-slot-properties-{node_id}",
                    "editor_toolset.toolsets.object.ObjectTools",
                    "set_properties",
                    {"instance": slot_instance, "values": button_slot_values},
                    instruction="Serialize values as a compact JSON string after confirming exact ButtonSlot property names.",
                    assertion="returnValue must be true",
                ))

        if component.get("warning"):
            warnings.append(f"{node_id}: {component['warning']}")

    steps.append(tool_step("compile", "UMGToolSet.UMGToolSet", "CompileWidgetBlueprint", {"widgetBlueprint": {"refPath": "${blueprint.returnValue.refPath}"}}, assertion="returnValue must be true"))
    if design_size_mode is not None:
        steps.append(tool_step(
            "get-blueprint-default-object",
            "editor_toolset.toolsets.blueprint.BlueprintTools",
            "get_default_object",
            {"blueprint": {"refPath": "${blueprint.returnValue.refPath}"}},
            saveResultAs="blueprintDefaultObject",
            assertion="returnValue must resolve to the generated UUserWidget class default object",
        ))
        default_object = {"refPath": "${blueprintDefaultObject.returnValue.refPath}"}
        steps.append(tool_step(
            "list-design-size-mode-property",
            "editor_toolset.toolsets.object.ObjectTools",
            "list_properties",
            {"instance": default_object},
            assertion="The generated class CDO must expose designSizeMode (UUserWidget::DesignSizeMode).",
        ))
        steps.append(tool_step(
            "get-design-size-mode-before",
            "editor_toolset.toolsets.object.ObjectTools",
            "get_properties",
            {"instance": default_object, "properties": ["designSizeMode"]},
        ))
        steps.append(tool_step(
            "set-design-size-mode",
            "editor_toolset.toolsets.object.ObjectTools",
            "set_properties",
            {"instance": default_object, "values": {"designSizeMode": design_size_mode}},
            instruction="Serialize values as a compact JSON string. Set the generated UUserWidget CDO after the final compile and before save; never write this property on the WidgetBlueprint asset object.",
            assertion="returnValue must be true",
        ))
    steps.append(tool_step("save", "editor_toolset.toolsets.asset.AssetTools", "save_assets", {"asset_paths": [package_path]}, assertion="returnValue must be true"))
    if design_size_mode is not None:
        steps.append(tool_step(
            "get-saved-blueprint-default-object",
            "editor_toolset.toolsets.blueprint.BlueprintTools",
            "get_default_object",
            {"blueprint": {"refPath": "${blueprint.returnValue.refPath}"}},
            saveResultAs="savedBlueprintDefaultObject",
            assertion="returnValue must resolve to the saved generated UUserWidget class default object",
        ))
        steps.append(tool_step(
            "verify-design-size-mode",
            "editor_toolset.toolsets.object.ObjectTools",
            "get_properties",
            {
                "instance": {"refPath": "${savedBlueprintDefaultObject.returnValue.refPath}"},
                "properties": ["designSizeMode"],
            },
            assertion=f"Saved generated-class CDO designSizeMode must equal {design_size_mode}.",
        ))
    steps.append(tool_step("verify-tree", "UMGToolSet.UMGToolSet", "GetWidgets", {"widgetBlueprint": {"refPath": "${blueprint.returnValue.refPath}"}}))

    selected_by_source: dict[str, list[str]] = {}
    for rule in selected:
        source_type = str(rule.get("sourceType", "baseline"))
        selected_by_source.setdefault(source_type, []).append(str(rule.get("id")))

    return {
        "version": "0.2",
        "sourceSpec": str(spec_path),
        "assetPath": package_path,
        "objectPath": object_path,
        "intendedTarget": spec.get("profile", {}).get("targetAsset"),
        "designSizeMode": design_size_mode,
        "designSizeModeResolution": design_size_mode_result,
        "selectedRuleIds": [rule.get("id") for rule in selected],
        "selectedRuleIdsBySourceType": selected_by_source,
        "warnings": warnings,
        "executorContract": {
            "referenceSyntax": "${savedResult.json.path}",
            "propertyWriteSequence": ["list_properties", "get_properties", "set_properties"],
            "setPropertiesValuesEncoding": "compact JSON string",
            "designSizeModeWrite": {
                "primary": "official-unreal-mcp generated-CDO ObjectTools list/get/set",
                "fallback": "nxue manage-property only when the official route does not expose the protected CDO field",
                "cdoObjectPath": f"{package_path}.Default__{asset_name}_C",
                "propertyPath": "DesignSizeMode",
                "expectedValue": design_size_mode,
                "requiresPostSaveReadback": design_size_mode is not None,
            },
        },
        "steps": steps,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        spec = load_json(args.spec)
        catalog = load_json(args.catalog)
        rules = load_json(args.rules)
        report = validate_spec(spec, catalog)
        if not report["valid"]:
            print(json.dumps({"error": "UILayoutSpec validation failed", "validation": report}, indent=2, ensure_ascii=False))
            return 1
        plan = build_plan(args.spec, spec, catalog, rules)
        payload = json.dumps(plan, indent=2, ensure_ascii=False) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(payload, encoding="utf-8")
            print(json.dumps({"ok": True, "output": str(args.output), "steps": len(plan["steps"])}, indent=2))
        else:
            print(payload, end="")
        return 0
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, indent=2, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
