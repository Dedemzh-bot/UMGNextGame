#!/usr/bin/env python3
"""Regression checks for semantic anchors, text justification, and Overlay minimality."""

from __future__ import annotations

from copy import deepcopy
import json
import sys
from pathlib import Path
from typing import Any

from prepare_build import build_plan
from validate_layout_spec import load_json, validate_spec


SKILL_ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = SKILL_ROOT / "references" / "component-catalog.json"
RULES_PATH = SKILL_ROOT / "references" / "rule-index.json"


def adaptive_screen_spec() -> dict[str, Any]:
    return {
        "version": "0.2",
        "mode": "production",
        "asset": {"folder": "/Game/UI/UMG/Adapt", "name": "umg_adapt"},
        "referenceSize": [2560, 1440],
        "profile": {
            "adaptive": True,
            "interactive": True,
            "hasText": True,
            "containsRepeatedElements": False,
            "regionGrouping": True,
            "assetKind": "screen",
            "designSizeMode": "FillScreen",
            "system": "adapt",
            "systemFolder": "Adapt",
            "targetAsset": {"folder": "/Game/UI/UMG/Adapt", "name": "umg_adapt"},
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
                "id": "background",
                "name": "ImgBackground",
                "role": "visual.image",
                "parent": "root",
                "rect": [0, 0, 1, 1],
                "anchor": "left-top",
                "rootLayer": "background",
                "adaptiveLayout": {
                    "horizontal": "stretch",
                    "vertical": "stretch",
                    "reason": "Fill the complete screen.",
                },
                "slotLayout": {
                    "anchors": {"minimum": [0, 0], "maximum": [1, 1]},
                    "offsets": {"left": 0, "top": 0, "right": 0, "bottom": 0},
                    "alignment": [0, 0],
                    "autoSize": False,
                },
                "properties": {},
            },
            {
                "id": "edge-region",
                "name": "PanelEdgeRegion",
                "role": "container.canvas",
                "parent": "root",
                "rect": [0.02, 0.05, 0.2, 0.9],
                "anchor": "left-top",
                "regionPurpose": "edge-content",
                "adaptiveLayout": {
                    "horizontal": "left",
                    "vertical": "stretch",
                    "reason": "Keep the left offset and preserve both vertical margins.",
                },
                "slotLayout": {
                    "anchors": {"minimum": [0, 0], "maximum": [0, 1]},
                    "offsets": {"left": 51.2, "top": 72, "right": 512, "bottom": 72},
                    "alignment": [0, 0],
                    "autoSize": False,
                },
                "properties": {},
            },
            {
                "id": "button",
                "name": "BtnAction",
                "role": "input.button",
                "parent": "edge-region",
                "rect": [0.04, 0.1, 0.16, 0.12],
                "anchor": "left-top",
                "properties": {"enabled": True},
            },
            {
                "id": "button-layout",
                "name": "PanelActionContent",
                "role": "container.canvas",
                "parent": "button",
                "rect": [0.04, 0.1, 0.16, 0.12],
                "anchor": "left-top",
                "buttonSlot": {
                    "padding": [0, 0, 0, 0],
                    "horizontalAlignment": "Fill",
                    "verticalAlignment": "Fill",
                },
                "properties": {},
            },
            {
                "id": "label",
                "name": "TxtAction",
                "role": "text.label",
                "parent": "button-layout",
                "rect": [0.05, 0.12, 0.14, 0.08],
                "anchor": "right-top",
                "properties": {"text": "Action", "font": {"size": 24}, "justification": "Right", "color": {"r": 1, "g": 1, "b": 1, "a": 1}},
            },
        ],
    }


def codes(report: dict[str, Any], collection: str) -> set[str]:
    return {item["code"] for item in report[collection]}


def resolve_slot_rect(slot_layout: dict[str, Any], parent_size: tuple[float, float]) -> tuple[float, float, float, float]:
    width, height = parent_size
    minimum = slot_layout["anchors"]["minimum"]
    maximum = slot_layout["anchors"]["maximum"]
    offsets = slot_layout["offsets"]
    alignment = slot_layout["alignment"]

    if maximum[0] > minimum[0]:
        left = minimum[0] * width + offsets["left"]
        resolved_width = maximum[0] * width - offsets["right"] - left
    else:
        resolved_width = offsets["right"]
        left = minimum[0] * width + offsets["left"] - alignment[0] * resolved_width
    if maximum[1] > minimum[1]:
        top = minimum[1] * height + offsets["top"]
        resolved_height = maximum[1] * height - offsets["bottom"] - top
    else:
        resolved_height = offsets["bottom"]
        top = minimum[1] * height + offsets["top"] - alignment[1] * resolved_height
    return left, top, resolved_width, resolved_height


def main() -> int:
    catalog = load_json(CATALOG_PATH)
    rules = load_json(RULES_PATH)
    failures: list[str] = []

    valid = adaptive_screen_spec()
    valid_report = validate_spec(valid, catalog)
    if not valid_report["valid"]:
        failures.append(f"valid adaptive screen rejected: {valid_report['errors']}")

    edge_slot = valid["nodes"][2]["slotLayout"]
    base_rect = resolve_slot_rect(edge_slot, (2560, 1440))
    tall_rect = resolve_slot_rect(edge_slot, (2560, 1800))
    wide_rect = resolve_slot_rect(edge_slot, (3440, 1440))
    if base_rect != (51.2, 72.0, 512.0, 1296.0):
        failures.append(f"base adaptive rect mismatch: {base_rect!r}")
    if tall_rect != (51.2, 72.0, 512.0, 1656.0):
        failures.append(f"tall-screen margins or vertical growth changed: {tall_rect!r}")
    if wide_rect != (51.2, 72.0, 512.0, 1296.0):
        failures.append(f"left semantic anchor drifted on wide screen: {wide_rect!r}")

    missing_intent = deepcopy(valid)
    del missing_intent["nodes"][2]["adaptiveLayout"]
    if "layout.adaptive_intent.missing" not in codes(validate_spec(missing_intent, catalog), "warnings"):
        failures.append("missing direct-region adaptive intent did not warn")

    mismatched = deepcopy(valid)
    mismatched["nodes"][2]["adaptiveLayout"]["horizontal"] = "right"
    if "layout.adaptive_intent.mismatch" not in codes(validate_spec(mismatched, catalog), "errors"):
        failures.append("semantic anchor mismatch did not fail")

    non_stretching = deepcopy(valid)
    non_stretching["nodes"][2]["slotLayout"]["anchors"]["maximum"][1] = 0
    if "layout.adaptive_intent.mismatch" not in codes(validate_spec(non_stretching, catalog), "errors"):
        failures.append("declared vertical stretch without stretched anchors did not fail")

    invalid_justification = deepcopy(valid)
    invalid_justification["nodes"][5]["properties"]["justification"] = "Auto"
    if "text.justification.value" not in codes(validate_spec(invalid_justification, catalog), "errors"):
        failures.append("invalid TextBlock justification did not fail")

    missing_justification = deepcopy(valid)
    del missing_justification["nodes"][5]["properties"]["justification"]
    if "text.justification.missing" not in codes(validate_spec(missing_justification, catalog), "warnings"):
        failures.append("missing TextBlock justification did not warn")

    redundant_overlay = deepcopy(valid)
    redundant_overlay["nodes"].insert(
        4,
        {
            "id": "button-overlay",
            "name": "OverAction",
            "role": "container.overlay",
            "parent": "button",
            "rect": [0.04, 0.1, 0.16, 0.12],
            "anchor": "left-top",
            "properties": {},
        },
    )
    next(node for node in redundant_overlay["nodes"] if node["id"] == "button-layout")["parent"] = "button-overlay"
    redundant_report = validate_spec(redundant_overlay, catalog)
    if "structure.overlay.redundant_button_canvas" not in codes(redundant_report, "errors"):
        failures.append("purposeless Button -> Overlay -> CanvasPanel did not fail")

    labeled_redundant_overlay = deepcopy(redundant_overlay)
    next(node for node in labeled_redundant_overlay["nodes"] if node["id"] == "button-overlay")["overlayPurpose"] = "adaptive-bounds"
    labeled_report = validate_spec(labeled_redundant_overlay, catalog)
    if "structure.overlay.redundant_button_canvas" not in codes(labeled_report, "errors"):
        failures.append("labeled one-child Button -> Overlay -> CanvasPanel bypassed the structural error")

    purposeful_overlay = deepcopy(valid)
    purposeful_overlay["nodes"].extend([
        {
            "id": "adaptive-overlay",
            "name": "OverAdaptiveNotice",
            "role": "container.overlay",
            "parent": "edge-region",
            "rect": [0.04, 0.3, 0.16, 0.12],
            "anchor": "left-top",
            "overlayPurpose": "adaptive-bounds",
            "properties": {},
        },
        {
            "id": "notice-image",
            "name": "ImgNotice",
            "role": "visual.image",
            "parent": "adaptive-overlay",
            "rect": [0.04, 0.3, 0.16, 0.12],
            "anchor": "left-top",
            "overlaySlot": {
                "horizontalAlignment": "Fill",
                "verticalAlignment": "Fill",
            },
            "adaptiveLayout": {
                "horizontal": "stretch",
                "vertical": "stretch",
                "reason": "The decoration fills its immediate Overlay Slot.",
            },
            "properties": {},
        },
    ])
    purposeful_report = validate_spec(purposeful_overlay, catalog)
    if not purposeful_report["valid"]:
        failures.append(f"documented one-child Overlay rejected: {purposeful_report['errors']}")

    missing_overlay_slot = deepcopy(purposeful_overlay)
    del next(node for node in missing_overlay_slot["nodes"] if node["id"] == "notice-image")["overlaySlot"]
    if "overlay.slot.missing" not in codes(validate_spec(missing_overlay_slot, catalog), "errors"):
        failures.append("direct Overlay child without explicit slot alignment did not fail")

    non_fill_full_overlay_child = deepcopy(purposeful_overlay)
    next(node for node in non_fill_full_overlay_child["nodes"] if node["id"] == "notice-image")["overlaySlot"]["horizontalAlignment"] = "Center"
    if "overlay.slot.full_region_fill" not in codes(validate_spec(non_fill_full_overlay_child, catalog), "errors"):
        failures.append("full-region Overlay child with non-Fill alignment did not fail")

    overlay_adaptive_mismatch = deepcopy(purposeful_overlay)
    next(node for node in overlay_adaptive_mismatch["nodes"] if node["id"] == "notice-image")["adaptiveLayout"]["horizontal"] = "center"
    if "layout.adaptive_intent.mismatch" not in codes(validate_spec(overlay_adaptive_mismatch, catalog), "errors"):
        failures.append("OverlaySlot alignment mismatch did not fail adaptive intent validation")

    plan = build_plan(Path("adaptive-layout-test.json"), valid, catalog, rules)
    text_values = next(
        step["arguments"]["values"]
        for step in plan["steps"]
        if step["stepId"] == "set-widget-properties-label"
    )
    if text_values.get("justification") != "Right":
        failures.append(f"justification planner mapping mismatch: {text_values!r}")

    button_slot_values = next(
        step["arguments"]["values"]
        for step in plan["steps"]
        if step["stepId"] == "set-button-slot-properties-button-layout"
    )
    expected_button_slot_values = {
        "padding": {"left": 0, "top": 0, "right": 0, "bottom": 0},
        "horizontalAlignment": "HAlign_Fill",
        "verticalAlignment": "VAlign_Fill",
    }
    if button_slot_values != expected_button_slot_values:
        failures.append(
            "ButtonSlot planner mapping mismatch: "
            f"expected {expected_button_slot_values!r}, got {button_slot_values!r}"
        )
    if button_slot_values.get("padding") != {"left": 0, "top": 0, "right": 0, "bottom": 0}:
        failures.append("ButtonSlot padding must map left/top/right/bottom in order")

    overlay_plan = build_plan(Path("overlay-slot-test.json"), purposeful_overlay, catalog, rules)
    overlay_slot_values = next(
        step["arguments"]["values"]
        for step in overlay_plan["steps"]
        if step["stepId"] == "set-overlay-slot-properties-notice-image"
    )
    if overlay_slot_values != {
        "horizontalAlignment": "HAlign_Fill",
        "verticalAlignment": "VAlign_Fill",
    }:
        failures.append(f"OverlaySlot planner mapping mismatch: {overlay_slot_values!r}")

    missing_button_slot = deepcopy(valid)
    del missing_button_slot["nodes"][4]["buttonSlot"]
    if "button.direct_canvas.slot.missing" not in codes(validate_spec(missing_button_slot, catalog), "errors"):
        failures.append("direct Button -> CanvasPanel without ButtonSlot did not fail")

    non_fill_button_slot = deepcopy(valid)
    non_fill_button_slot["nodes"][4]["buttonSlot"]["horizontalAlignment"] = "Center"
    if "button.direct_canvas.slot.alignment" not in codes(validate_spec(non_fill_button_slot, catalog), "errors"):
        failures.append("direct Button -> CanvasPanel with non-Fill ButtonSlot did not fail")

    nested = deepcopy(valid)
    next(node for node in nested["nodes"] if node["id"] == "edge-region")["rect"] = [0, 0, 1, 1]
    nested["nodes"].extend([
        {
            "id": "nested-canvas", "name": "PanelNested", "role": "container.canvas",
            "parent": "edge-region", "rect": [0.25, 0.2, 595 / 2560, 252 / 1440],
            "anchor": "left-top", "properties": {},
        },
        {
            "id": "nested-child", "name": "ImgNested", "role": "visual.image",
            "parent": "nested-canvas",
            "rect": [0.25 + (595 - 100) / 2560, 0.2 + 84 / 1440, 100 / 2560, 50 / 1440],
            "anchor": "left-top",
            "slotLayout": {
                "anchors": {"minimum": [0, 0], "maximum": [0, 0]},
                "offsets": {"left": 595, "top": 84, "right": 100, "bottom": 50},
                "alignment": [1, 0], "autoSize": True,
            },
            "properties": {},
        },
    ])
    if not validate_spec(nested, catalog)["valid"]:
        failures.append("valid nested Canvas local offsets rejected")
    global_offsets = deepcopy(nested)
    child = next(node for node in global_offsets["nodes"] if node["id"] == "nested-child")
    child["slotLayout"]["offsets"]["left"] = 1248
    child["slotLayout"]["offsets"]["top"] = 384
    if "slot_layout.local_coordinates" not in codes(validate_spec(global_offsets, catalog), "errors"):
        failures.append("global nested Canvas offsets were not rejected")

    print(json.dumps({
        "ok": not failures,
        "checkedValidLayouts": 2,
        "checkedViewportVariants": 3,
        "checkedFailureModes": 10,
        "checkedPlannerMappings": 3,
        "failures": failures,
    }, indent=2, ensure_ascii=False))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
