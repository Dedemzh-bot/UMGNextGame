#!/usr/bin/env python3
"""Regression checks for explicit flow and scroll child Slot contracts."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from prepare_build import build_plan
from validate_layout_spec import load_json, validate_spec


SKILL_ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = SKILL_ROOT / "references" / "component-catalog.json"
RULES_PATH = SKILL_ROOT / "references" / "rule-index.json"
SCHEMA_PATH = SKILL_ROOT / "assets" / "ui-layout-spec.schema.json"


def flow_slot(
    rule: str,
    horizontal: str,
    vertical: str,
    *,
    padding: list[float] | None = None,
    weight: float | None = None,
) -> dict[str, Any]:
    size: dict[str, Any] = {"rule": rule}
    if weight is not None:
        size["weight"] = weight
    return {
        "size": size,
        "padding": padding or [0, 0, 0, 0],
        "horizontalAlignment": horizontal,
        "verticalAlignment": vertical,
    }


def explicit_slot_spec() -> dict[str, Any]:
    return {
        "version": "0.2",
        "mode": "prototype",
        "asset": {"folder": "/Game/UI/AIPrototype", "name": "umg_ai_flow_slots"},
        "referenceSize": [1000, 1000],
        "profile": {
            "adaptive": True,
            "interactive": True,
            "hasText": False,
            "containsRepeatedElements": False,
            "regionGrouping": False,
            "explicitPanelSlots": True,
        },
        "nodes": [
            {
                "id": "root",
                "name": "PanelRoot",
                "role": "screen.root",
                "parent": None,
                "rect": [0, 0, 1, 1],
                "anchor": "left-top",
                "properties": {},
            },
            {
                "id": "flow",
                "name": "VerFlow",
                "role": "container.vertical",
                "parent": "root",
                "rect": [0.1, 0.1, 0.8, 0.8],
                "anchor": "left-top",
                "properties": {},
            },
            {
                "id": "content-row",
                "name": "HorContent",
                "role": "container.horizontal",
                "parent": "flow",
                "rect": [0.1, 0.1, 0.8, 0.2],
                "anchor": "left-top",
                "contentDrivenSize": {"verified": True},
                "adaptiveLayout": {
                    "horizontal": "stretch",
                    "vertical": "top",
                    "reason": "Fill the allocated width while retaining content-driven main-axis size.",
                },
                "flowSlot": flow_slot("Auto", "Fill", "Top", padding=[1, 2, 3, 4]),
                "properties": {},
            },
            {
                "id": "weighted-image",
                "name": "ImgWeighted",
                "role": "visual.image",
                "parent": "content-row",
                "rect": [0.1, 0.1, 0.4, 0.2],
                "anchor": "left-top",
                "adaptiveLayout": {
                    "horizontal": "center",
                    "vertical": "bottom",
                    "reason": "Center the graphic horizontally and keep it at the bottom of its allocation.",
                },
                "flowSlot": flow_slot("Fill", "Center", "Bottom", weight=2.5),
                "properties": {},
            },
            {
                "id": "scroll",
                "name": "ScrContent",
                "role": "container.game-scroll",
                "parent": "flow",
                "rect": [0.1, 0.3, 0.8, 0.6],
                "anchor": "left-top",
                "adaptiveLayout": {
                    "horizontal": "stretch",
                    "vertical": "stretch",
                    "reason": "Use the remaining flow allocation on both axes.",
                },
                "flowSlot": flow_slot("Fill", "Fill", "Fill", weight=1),
                "properties": {},
            },
            {
                "id": "scroll-image",
                "name": "ImgScrollContent",
                "role": "visual.image",
                "parent": "scroll",
                "rect": [0.5, 0.3, 0.4, 0.3],
                "anchor": "right-top",
                "adaptiveLayout": {
                    "horizontal": "right",
                    "vertical": "center",
                    "reason": "Keep the child at the right and vertically centered in the scroll allocation.",
                },
                "scrollSlot": {
                    "padding": [5, 6, 7, 8],
                    "horizontalAlignment": "Right",
                    "verticalAlignment": "Center",
                },
                "properties": {},
            },
        ],
    }


def error_codes(spec: dict[str, Any], catalog: dict[str, Any]) -> set[str]:
    return {item["code"] for item in validate_spec(spec, catalog)["errors"]}


def expect_code(
    failures: list[str],
    label: str,
    spec: dict[str, Any],
    catalog: dict[str, Any],
    expected: str,
) -> None:
    actual = error_codes(spec, catalog)
    if expected not in actual:
        failures.append(f"{label}: expected {expected}, got {sorted(actual)}")


def main() -> int:
    schema = load_json(SCHEMA_PATH)
    catalog = load_json(CATALOG_PATH)
    rules = load_json(RULES_PATH)
    failures: list[str] = []

    profile_properties = schema["properties"]["profile"]["properties"]
    node_properties = schema["properties"]["nodes"]["items"]["properties"]
    flow_schema = node_properties.get("flowSlot", {})
    scroll_schema = node_properties.get("scrollSlot", {})
    if profile_properties.get("explicitPanelSlots") != {"type": "boolean"}:
        failures.append("schema does not expose optional boolean profile.explicitPanelSlots")
    if flow_schema.get("additionalProperties") is not False or set(flow_schema.get("required", [])) != {
        "size", "padding", "horizontalAlignment", "verticalAlignment"
    }:
        failures.append("flowSlot schema is not closed with the complete required field set")
    size_schema = flow_schema.get("properties", {}).get("size", {})
    if size_schema.get("additionalProperties") is not False or not size_schema.get("allOf"):
        failures.append("flowSlot size schema does not close fields or condition Fill weight")
    if scroll_schema.get("additionalProperties") is not False or set(scroll_schema.get("required", [])) != {
        "padding", "horizontalAlignment", "verticalAlignment"
    }:
        failures.append("scrollSlot schema is not closed with the complete required field set")

    valid = explicit_slot_spec()
    valid_report = validate_spec(valid, catalog)
    if not valid_report["valid"]:
        failures.append(f"valid explicit flow/scroll Slots rejected: {valid_report['errors']}")
    if "layout.adaptive_intent.non_canvas" in {
        item["code"] for item in valid_report["warnings"]
    }:
        failures.append("flow/scroll adaptive intent fell back to the non-Canvas warning")

    archived = deepcopy(valid)
    archived["profile"].pop("explicitPanelSlots")
    for node in archived["nodes"]:
        node.pop("flowSlot", None)
        node.pop("scrollSlot", None)
    archived_report = validate_spec(archived, catalog)
    if not archived_report["valid"]:
        failures.append(f"policy-omitted archived layout rejected: {archived_report['errors']}")

    missing_flow = deepcopy(valid)
    missing_flow["nodes"][2].pop("flowSlot")
    expect_code(failures, "missing flow Slot", missing_flow, catalog, "flow.slot.missing")

    missing_scroll = deepcopy(valid)
    missing_scroll["nodes"][5].pop("scrollSlot")
    expect_code(failures, "missing scroll Slot", missing_scroll, catalog, "scroll.slot.missing")

    wrong_flow_parent = deepcopy(valid)
    wrong_flow_parent["nodes"][5]["flowSlot"] = flow_slot("Auto", "Fill", "Fill")
    expect_code(failures, "flow Slot wrong parent", wrong_flow_parent, catalog, "flow.slot.relationship")

    wrong_scroll_parent = deepcopy(valid)
    wrong_scroll_parent["nodes"][3]["scrollSlot"] = {
        "padding": [0, 0, 0, 0],
        "horizontalAlignment": "Fill",
        "verticalAlignment": "Fill",
    }
    expect_code(failures, "scroll Slot wrong parent", wrong_scroll_parent, catalog, "scroll.slot.relationship")

    auto_with_weight = deepcopy(valid)
    auto_with_weight["nodes"][2]["flowSlot"]["size"]["weight"] = 1
    expect_code(
        failures,
        "Auto weight forbidden",
        auto_with_weight,
        catalog,
        "flow.slot.size.weight_forbidden",
    )

    fill_without_weight = deepcopy(valid)
    fill_without_weight["nodes"][3]["flowSlot"]["size"].pop("weight")
    expect_code(
        failures,
        "Fill weight required",
        fill_without_weight,
        catalog,
        "flow.slot.size.weight_required",
    )

    content_driven_fill = deepcopy(valid)
    content_driven_fill["nodes"][2]["flowSlot"]["size"] = {"rule": "Fill", "weight": 1}
    expect_code(
        failures,
        "content-driven child uses Auto",
        content_driven_fill,
        catalog,
        "flow.slot.content_driven_auto",
    )

    flow_adaptive_mismatch = deepcopy(valid)
    flow_adaptive_mismatch["nodes"][2]["adaptiveLayout"]["horizontal"] = "right"
    expect_code(
        failures,
        "flow adaptive alignment mismatch",
        flow_adaptive_mismatch,
        catalog,
        "layout.adaptive_intent.mismatch",
    )

    scroll_adaptive_mismatch = deepcopy(valid)
    scroll_adaptive_mismatch["nodes"][5]["adaptiveLayout"]["vertical"] = "top"
    expect_code(
        failures,
        "scroll adaptive alignment mismatch",
        scroll_adaptive_mismatch,
        catalog,
        "layout.adaptive_intent.mismatch",
    )

    invalid_policy_type = deepcopy(valid)
    invalid_policy_type["profile"]["explicitPanelSlots"] = "yes"
    expect_code(
        failures,
        "explicit panel policy type",
        invalid_policy_type,
        catalog,
        "profile.explicit_panel_slots",
    )

    extra_flow_field = deepcopy(valid)
    extra_flow_field["nodes"][2]["flowSlot"]["unexpected"] = True
    expect_code(failures, "closed flow Slot", extra_flow_field, catalog, "flow.slot.fields")

    plan = build_plan(Path("flow-scroll-slot-test.json"), valid, catalog, rules)
    steps_by_id = {step["stepId"]: step for step in plan["steps"]}
    expected_auto = {
        "size": {"value": 1, "sizeRule": "Automatic"},
        "padding": {"left": 1, "top": 2, "right": 3, "bottom": 4},
        "horizontalAlignment": "HAlign_Fill",
        "verticalAlignment": "VAlign_Top",
    }
    actual_auto = steps_by_id.get("set-flow-slot-properties-content-row", {}).get("arguments", {}).get("values")
    if actual_auto != expected_auto:
        failures.append(f"Auto FlowSlot planner mapping mismatch: {actual_auto!r}")

    expected_fill = {
        "size": {"value": 2.5, "sizeRule": "Fill"},
        "padding": {"left": 0, "top": 0, "right": 0, "bottom": 0},
        "horizontalAlignment": "HAlign_Center",
        "verticalAlignment": "VAlign_Bottom",
    }
    actual_fill = steps_by_id.get("set-flow-slot-properties-weighted-image", {}).get("arguments", {}).get("values")
    if actual_fill != expected_fill:
        failures.append(f"Fill FlowSlot planner mapping mismatch: {actual_fill!r}")

    expected_scroll = {
        "padding": {"left": 5, "top": 6, "right": 7, "bottom": 8},
        "horizontalAlignment": "HAlign_Right",
        "verticalAlignment": "VAlign_Center",
    }
    actual_scroll = steps_by_id.get("set-scroll-slot-properties-scroll-image", {}).get("arguments", {}).get("values")
    if actual_scroll != expected_scroll:
        failures.append(f"ScrollSlot planner mapping mismatch: {actual_scroll!r}")

    for node_id, kind in (("content-row", "flow"), ("scroll-image", "scroll")):
        for operation in ("list", "get", "set"):
            step_id = f"{operation}-{kind}-slot-properties-{node_id}"
            if step_id not in steps_by_id:
                failures.append(f"planner omitted {step_id}")

    archived_plan = build_plan(Path("archived-flow-test.json"), archived, catalog, rules)
    if any("-flow-slot-properties-" in step["stepId"] or "-scroll-slot-properties-" in step["stepId"] for step in archived_plan["steps"]):
        failures.append("policy-omitted archived plan unexpectedly emitted explicit panel Slot writes")

    print(json.dumps({
        "ok": not failures,
        "checkedSchemaContracts": 4,
        "checkedValidLayouts": 2,
        "checkedFailureModes": 11,
        "checkedPlannerMappings": 3,
        "failures": failures,
    }, indent=2, ensure_ascii=False))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
