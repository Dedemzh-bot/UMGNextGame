#!/usr/bin/env python3
"""Regression checks for owner-supplied WidgetTree component name prefixes."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from prepare_build import build_plan
from validate_layout_spec import load_json, validate_spec

SKILL_ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = SKILL_ROOT / "references" / "component-catalog.json"
RULES_PATH = SKILL_ROOT / "references" / "rule-index.json"

EXPECTED = {
    "screen.root": ("/Script/UMG.CanvasPanel", "Panel"),
    "container.canvas": ("/Script/UMG.CanvasPanel", "Panel"),
    "container.vertical": ("/Script/UMG.VerticalBox", "Ver"),
    "container.horizontal": ("/Script/UMG.HorizontalBox", "Hor"),
    "container.overlay": ("/Script/UMG.Overlay", "Over"),
    "container.wrap": ("/Script/UMG.WrapBox", "Wrap"),
    "container.scale": ("/Script/UMG.ScaleBox", "Sca"),
    "container.size": ("/Script/UMG.SizeBox", "Size"),
    "container.game-scroll": ("/Script/UIFramework.GameScrollBox", "Scr"),
    "visual.image": ("/Script/UIFramework.GameImage", "Img"),
    "text.label": ("/Script/UMG.TextBlock", "Txt"),
    "input.button": ("/Script/UMG.Button", "Btn"),
    "input.slider": ("/Script/UMG.Slider", "Sli"),
    "input.radial-slider": ("/Script/AdvancedWidgets.RadialSlider", "Rad"),
    "progress.linear": ("/Script/UMG.ProgressBar", "Bar"),
    "collection.lua-list": ("/Script/UIFramework.LuaListView", "List"),
    "collection.lua-tile": ("/Script/UIFramework.LuaTileView", "Tile"),
}


def base_spec() -> dict[str, Any]:
    return {
        "version": "0.2",
        "mode": "prototype",
        "asset": {"folder": "/Game/UI/AIPrototype", "name": "umg_ai_prefix_test"},
        "referenceSize": [1920, 1080],
        "profile": {
            "adaptive": True,
            "interactive": False,
            "hasText": False,
            "containsRepeatedElements": False,
            "regionGrouping": False,
            "assetKind": "prototype",
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
            }
        ],
    }


def spec_for(role: str, name: str) -> dict[str, Any]:
    spec = base_spec()
    if role == "text.label":
        spec["profile"]["hasText"] = True
    if role == "screen.root":
        spec["nodes"][0]["name"] = name
    else:
        node = {
                "id": "subject",
                "name": name,
                "role": role,
                "parent": "root",
                "rect": [0, 0, 0.5, 0.5],
                "anchor": "left-top",
                "properties": {"font": {"size": 24}, "color": {"r": 1, "g": 1, "b": 1, "a": 1}} if role == "text.label" else {},
            }
        if role in {"collection.lua-list", "collection.lua-tile"}:
            node["isVariable"] = True
        if role == "collection.lua-tile":
            node["properties"].update({"entryWidth": 120, "entryHeight": 80})
        spec["nodes"].append(node)
    return spec


def main() -> int:
    catalog = load_json(CATALOG_PATH)
    by_role = {item["role"]: item for item in catalog["components"]}
    failures: list[str] = []

    for role, (class_path, prefix) in EXPECTED.items():
        component = by_role.get(role)
        if component is None:
            failures.append(f"missing catalog role: {role}")
            continue
        if component.get("classPath") != class_path:
            failures.append(f"{role}: expected classPath {class_path}, got {component.get('classPath')}")
        if component.get("namePrefix") != prefix:
            failures.append(f"{role}: expected namePrefix {prefix}, got {component.get('namePrefix')}")

        valid_report = validate_spec(spec_for(role, prefix + "Example"), catalog)
        if not valid_report["valid"]:
            failures.append(f"{role}: valid prefixed name was rejected: {valid_report['errors']}")

        invalid_report = validate_spec(spec_for(role, "WrongExample"), catalog)
        warning_codes = [warning["code"] for warning in invalid_report["warnings"]]
        if "node.name_prefix" not in warning_codes:
            failures.append(f"{role}: wrong prefix did not produce node.name_prefix warning: {warning_codes}")
        if not invalid_report["valid"]:
            failures.append(f"{role}: wrong prefix must remain a warning, got errors: {invalid_report['errors']}")

    panel_expectations = {
        "container.size": True,
        "collection.lua-list": False,
        "collection.lua-tile": False,
        "container.game-scroll": True,
    }
    for role, expected_is_panel in panel_expectations.items():
        if by_role.get(role, {}).get("isPanel") is not expected_is_panel:
            failures.append(f"{role}: expected isPanel={expected_is_panel}")

    for role in ("visual.border", "layout.spacer"):
        if "namePrefix" in by_role.get(role, {}):
            failures.append(f"{role}: prefix must remain unmapped until supplied by the project owner")

    image_plan = build_plan(
        Path("game-image-component-test.json"),
        spec_for("visual.image", "ImgExample"),
        catalog,
        load_json(RULES_PATH),
    )
    image_add_step = next(
        step for step in image_plan["steps"] if step["stepId"] == "add-subject"
    )
    image_class_path = image_add_step["arguments"]["widgetClass"]["refPath"]
    if image_class_path != "/Script/UIFramework.GameImage":
        failures.append(
            "visual.image build plan must create GameImage, got "
            f"{image_class_path!r}"
        )

    print(json.dumps({"ok": not failures, "checkedRoles": len(EXPECTED), "checkedGameImageBuildPlan": True, "failures": failures}, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
