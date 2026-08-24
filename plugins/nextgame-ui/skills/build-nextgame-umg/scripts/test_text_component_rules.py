#!/usr/bin/env python3
"""Regression checks for owner-supplied TextBlock content and granularity rules."""

from __future__ import annotations

from copy import deepcopy
import json
import sys
from pathlib import Path
from typing import Any

from validate_layout_spec import load_json, validate_spec

SKILL_ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = SKILL_ROOT / "references" / "component-catalog.json"


def split_header_spec() -> dict[str, Any]:
    return {
        "version": "0.2",
        "mode": "production",
        "asset": {"folder": "/Game/UI/UMG/Map", "name": "umg_map"},
        "referenceSize": [2560, 1440],
        "profile": {
            "adaptive": False,
            "interactive": False,
            "hasText": True,
            "containsRepeatedElements": False,
            "regionGrouping": True,
            "assetKind": "screen",
            "system": "map",
            "systemFolder": "Map",
            "subsystem": None,
            "function": None,
            "secondaryFunction": None,
            "targetAsset": {
                "folder": "/Game/UI/UMG/Map",
                "name": "umg_map",
            },
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
                "id": "header",
                "name": "PanelTaskHeader",
                "role": "container.canvas",
                "parent": "root",
                "rect": [0.05, 0.05, 0.4, 0.08],
                "anchor": "left-top",
                "regionPurpose": "task-header",
                "properties": {},
            },
            {
                "id": "header-title",
                "name": "TxtTaskTitle",
                "role": "text.label",
                "parent": "header",
                "rect": [0.055, 0.06, 0.07, 0.05],
                "anchor": "left-top",
                "properties": {
                    "font": {"size": 26},
                    "text": "Description text",
                    "color": {"r": 1, "g": 1, "b": 1, "a": 1},
                },
            },
            {
                "id": "header-separator",
                "name": "ImgTaskTitleSeparator",
                "role": "visual.image",
                "parent": "header",
                "rect": [0.13, 0.085, 0.3, 0.002],
                "anchor": "left-top",
                "properties": {
                    "color": {"r": 0.8, "g": 0.8, "b": 0.8, "a": 1},
                },
            },
            {
                "id": "paragraph",
                "name": "TxtTaskDescription",
                "role": "text.label",
                "parent": "header",
                "rect": [0.055, 0.112, 0.36, 0.015],
                "anchor": "left-top",
                "slotLayout": {
                    "anchors": {"minimum": [0, 0], "maximum": [1, 0]},
                    "offsets": {"left": 13, "top": 89.3, "right": 89.6, "bottom": 48},
                    "alignment": [0, 0],
                    "autoSize": True,
                },
                "properties": {
                    "font": {"size": 20},
                    "text": "Description text",
                    "autoWrap": True,
                    "wrapTextAt": 310,
                    "color": {"r": 1, "g": 1, "b": 1, "a": 1},
                },
            },
        ],
    }


def error_codes(spec: dict[str, Any], catalog: dict[str, Any]) -> set[str]:
    return {error["code"] for error in validate_spec(spec, catalog)["errors"]}


def mutated_text_spec(text: str) -> dict[str, Any]:
    spec = deepcopy(split_header_spec())
    spec["nodes"][2]["properties"]["text"] = text
    return spec


def main() -> int:
    catalog = load_json(CATALOG_PATH)
    failures: list[str] = []

    valid_report = validate_spec(split_header_spec(), catalog)
    if not valid_report["valid"]:
        failures.append(f"valid split header rejected: {valid_report['errors']}")

    cases = [
        ("manual newline", "Title\nDescription", "text.independent_block"),
        ("layout spaces", "Item     Map", "text.spacing_layout"),
        ("icon glyph", " icon", "text.non_text_glyph"),
        ("decorative separator", "Title---", "text.decorative_run"),
    ]
    for label, text, expected_code in cases:
        codes = error_codes(mutated_text_spec(text), catalog)
        if expected_code not in codes:
            failures.append(
                f"{label}: expected {expected_code}, got {sorted(codes)}"
            )

    print(json.dumps({
        "ok": not failures,
        "checkedValidLayouts": 1,
        "checkedFailureModes": len(cases),
        "failures": failures,
    }, indent=2, ensure_ascii=False))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
