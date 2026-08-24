#!/usr/bin/env python3
"""Regression checks for runtime ratio groups and full-height screen chains."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from prepare_build import build_plan
from select_rules import select_rules
from validate_layout_spec import load_json, validate_spec


SKILL_ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = SKILL_ROOT / "references" / "component-catalog.json"
RULES_PATH = SKILL_ROOT / "references" / "rule-index.json"


def full_slot() -> dict[str, Any]:
    return {
        "anchors": {"minimum": [0, 0], "maximum": [1, 1]},
        "offsets": {"left": 0, "top": 0, "right": 0, "bottom": 0},
        "alignment": [0, 0],
        "autoSize": False,
    }


def right_full_height_slot() -> dict[str, Any]:
    return {
        "anchors": {"minimum": [1, 0], "maximum": [1, 1]},
        "offsets": {"left": 0, "top": 0, "right": 1760, "bottom": 0},
        "alignment": [1, 0],
        "autoSize": False,
    }


def ratio_screen_spec() -> dict[str, Any]:
    return {
        "version": "0.2",
        "mode": "production",
        "asset": {"folder": "/Game/UI/UMG/Ratio", "name": "umg_ratio"},
        "referenceSize": [2560, 1440],
        "profile": {
            "adaptive": True,
            "interactive": False,
            "hasText": True,
            "containsRepeatedElements": False,
            "regionGrouping": True,
            "assetKind": "screen",
            "system": "ratio",
            "systemFolder": "Ratio",
            "targetAsset": {"folder": "/Game/UI/UMG/Ratio", "name": "umg_ratio"},
        },
        "nodes": [
            {"id": "root", "name": "PanelRoot", "role": "screen.root", "parent": None, "rect": [0, 0, 1, 1], "anchor": "left-top", "properties": {}},
            {
                "id": "screen-shell", "name": "PanelScreenShell", "role": "container.canvas", "parent": "root", "rect": [0, 0, 1, 1], "anchor": "left-top",
                "regionPurpose": "screen-shell", "fullHeight": True,
                "adaptiveLayout": {"horizontal": "stretch", "vertical": "stretch", "reason": "The shell covers the complete adaptive screen."},
                "slotLayout": full_slot(), "properties": {},
            },
            {
                "id": "screen-background", "name": "ImgScreenBackground", "role": "visual.image", "parent": "screen-shell", "rect": [0, 0, 1, 1], "anchor": "left-top",
                "fullHeight": True, "slotLayout": full_slot(), "properties": {},
            },
            {
                "id": "mission-status", "name": "HorMissionStatus", "role": "container.horizontal", "parent": "screen-shell", "rect": [0.75, 0.76, 0.15, 0.04], "anchor": "right-top",
                "textGroup": {"kind": "ratio", "alignment": "Right", "orderedChildren": ["mission-status-num", "mission-status-separator", "mission-status-max"]},
                "properties": {},
            },
            {
                "id": "mission-status-num", "name": "TxtMissionStatusNum", "role": "text.label", "parent": "mission-status", "rect": [0.75, 0.76, 0.04, 0.04], "anchor": "left-top", "isVariable": True,
                "properties": {"text": "15", "font": {"size": 20}, "justification": "Right", "color": {"r": 1, "g": 1, "b": 1, "a": 1}},
            },
            {
                "id": "mission-status-separator", "name": "TxtMissionStatusSeparator", "role": "text.label", "parent": "mission-status", "rect": [0.79, 0.76, 0.02, 0.04], "anchor": "left-top",
                "properties": {"text": "/", "font": {"size": 20}, "justification": "Center", "color": {"r": 1, "g": 1, "b": 1, "a": 1}},
            },
            {
                "id": "mission-status-max", "name": "TxtMissionStatusMax", "role": "text.label", "parent": "mission-status", "rect": [0.81, 0.76, 0.04, 0.04], "anchor": "left-top", "isVariable": True,
                "properties": {"text": "20", "font": {"size": 20}, "justification": "Left", "color": {"r": 1, "g": 1, "b": 1, "a": 1}},
            },
        ],
    }


def codes(spec: dict[str, Any], catalog: dict[str, Any]) -> set[str]:
    return {item["code"] for item in validate_spec(spec, catalog)["errors"]}


def expect(failures: list[str], label: str, spec: dict[str, Any], catalog: dict[str, Any], code: str) -> None:
    actual = codes(spec, catalog)
    if code not in actual:
        failures.append(f"{label}: expected {code}, got {sorted(actual)}")


def main() -> int:
    catalog = load_json(CATALOG_PATH)
    rules = load_json(RULES_PATH)
    failures: list[str] = []
    valid = ratio_screen_spec()
    report = validate_spec(valid, catalog)
    if not report["valid"]:
        failures.append(f"valid ratio/full-height spec rejected: {report['errors']}")

    right_owned = ratio_screen_spec()
    for node_index in (1, 2):
        right_owned["nodes"][node_index]["rect"] = [0.3125, 0, 0.6875, 1]
        right_owned["nodes"][node_index]["anchor"] = "right-top"
        right_owned["nodes"][node_index]["slotLayout"] = right_full_height_slot()
    right_owned["nodes"][1]["adaptiveLayout"] = {
        "horizontal": "right",
        "vertical": "stretch",
        "reason": "The fixed-width shell remains right-owned while covering the complete viewport height.",
    }
    right_report = validate_spec(right_owned, catalog)
    if not right_report["valid"]:
        failures.append(f"valid right-owned full-height spec rejected: {right_report['errors']}")

    right_plan = build_plan(Path("right-owned-full-height-test.json"), right_owned, catalog, rules)
    expected_right_slot = {
        "layoutData": {
            "offsets": {"left": 0, "top": 0, "right": 1760, "bottom": 0},
            "anchors": {"minimum": {"x": 1, "y": 0}, "maximum": {"x": 1, "y": 1}},
            "alignment": {"x": 1, "y": 0},
        },
        "bAutoSize": False,
        "zOrder": 0,
    }
    for step_id in ("set-slot-properties-screen-shell", "set-slot-properties-screen-background"):
        slot_values = next(
            (step["arguments"]["values"] for step in right_plan["steps"] if step["stepId"] == step_id),
            None,
        )
        if slot_values != expected_right_slot:
            failures.append(f"right-owned planner slot drifted for {step_id}: {slot_values!r}")

    selected = {rule["id"] for rule in select_rules(valid, rules)}
    for rule_id in {"text.runtime-ratio-group", "layout.full-height-stretch-chain"}:
        if rule_id not in selected:
            failures.append(f"selected rules omitted {rule_id}")

    plan = build_plan(Path("ratio-full-height-test.json"), valid, catalog, rules)
    writes = {
        step["stepId"]: step["arguments"].get("values", {})
        for step in plan["steps"]
        if step["stepId"].startswith("set-widget-properties-")
    }
    if any("textGroup" in values or "fullHeight" in values for values in writes.values()):
        failures.append("semantic metadata leaked into Unreal property writes")
    toggles = {
        step["stepId"]: step["arguments"]["bIsVariable"]
        for step in plan["steps"]
        if step["toolName"] == "ToggleWidgetAsVariable"
    }
    expected = {
        "toggle-widget-variable-mission-status-num": True,
        "toggle-widget-variable-mission-status-separator": False,
        "toggle-widget-variable-mission-status-max": True,
    }
    if {key: toggles.get(key) for key in expected} != expected:
        failures.append("ratio Is Variable planner mapping did not preserve current/separator/maximum decisions")

    wrong_role = deepcopy(valid)
    wrong_role["nodes"][3]["role"] = "container.vertical"
    expect(failures, "ratio parent role", wrong_role, catalog, "text.ratio.role")

    wrong_order = deepcopy(valid)
    wrong_order["nodes"][3]["textGroup"]["orderedChildren"] = ["mission-status-max", "mission-status-separator", "mission-status-num"]
    expect(failures, "ratio child order", wrong_order, catalog, "text.ratio.child_order")

    dynamic_separator = deepcopy(valid)
    dynamic_separator["nodes"][5]["isVariable"] = True
    expect(failures, "dynamic separator", dynamic_separator, catalog, "text.ratio.separator.variable")

    missing_dynamic_value = deepcopy(valid)
    missing_dynamic_value["nodes"][4]["isVariable"] = False
    expect(failures, "static current", missing_dynamic_value, catalog, "text.ratio.value.variable")

    wrong_separator = deepcopy(valid)
    wrong_separator["nodes"][5]["properties"]["text"] = "-"
    expect(failures, "wrong separator", wrong_separator, catalog, "text.ratio.separator.text")

    missing_alignment = deepcopy(valid)
    del missing_alignment["nodes"][3]["textGroup"]["alignment"]
    expect(failures, "missing alignment", missing_alignment, catalog, "text.ratio.alignment")

    fixed_shell = deepcopy(valid)
    fixed_shell["nodes"][1]["slotLayout"]["anchors"]["maximum"][1] = 0
    expect(failures, "fixed shell height", fixed_shell, catalog, "layout.full_height.stretch")

    auto_size_background = deepcopy(valid)
    auto_size_background["nodes"][2]["slotLayout"]["autoSize"] = True
    expect(failures, "auto-size background", auto_size_background, catalog, "layout.full_height.stretch")

    wrong_right_anchor = deepcopy(right_owned)
    wrong_right_anchor["nodes"][1]["slotLayout"]["anchors"]["minimum"][0] = 0
    wrong_right_anchor["nodes"][1]["slotLayout"]["anchors"]["maximum"][0] = 0
    expect(failures, "right-owned shell anchor", wrong_right_anchor, catalog, "layout.adaptive_intent.mismatch")

    wrong_right_alignment = deepcopy(right_owned)
    wrong_right_alignment["nodes"][2]["slotLayout"]["alignment"][0] = 0
    expect(failures, "right-owned background alignment", wrong_right_alignment, catalog, "slot_layout.local_coordinates")

    wrong_right_width = deepcopy(right_owned)
    wrong_right_width["nodes"][2]["slotLayout"]["offsets"]["right"] = 1700
    expect(failures, "right-owned background width", wrong_right_width, catalog, "slot_layout.local_coordinates")

    invalid_marker = deepcopy(valid)
    invalid_marker["nodes"][2]["fullHeight"] = "yes"
    expect(failures, "invalid fullHeight marker", invalid_marker, catalog, "layout.full_height.type")

    print(json.dumps({
        "ok": not failures,
        "checkedValidLayouts": 2,
        "checkedFailureModes": 12,
        "checkedRightOwnedPlans": 1,
        "checkedPlannerMetadataIsolation": 1,
        "checkedSelectedRules": 2,
        "failures": failures,
    }, indent=2, ensure_ascii=False))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
