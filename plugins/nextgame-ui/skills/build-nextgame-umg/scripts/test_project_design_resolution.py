#!/usr/bin/env python3
"""Regression checks for the NextGame complete-screen design resolution."""

from __future__ import annotations

from copy import deepcopy
import json
import sys
from pathlib import Path
from typing import Any

from select_rules import select_rules
from validate_layout_spec import load_json, validate_spec

SKILL_ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = SKILL_ROOT / "references" / "component-catalog.json"
RULES_PATH = SKILL_ROOT / "references" / "rule-index.json"


def screen_spec() -> dict[str, Any]:
    return {
        "version": "0.2",
        "mode": "production",
        "asset": {"folder": "/Game/UI/UMG/Role", "name": "umg_role"},
        "referenceSize": [2560, 1440],
        "profile": {
            "adaptive": True,
            "interactive": False,
            "hasText": False,
            "containsRepeatedElements": False,
            "regionGrouping": False,
            "assetKind": "screen",
            "designSizeMode": "FillScreen",
            "system": "role",
            "systemFolder": "Role",
            "subsystem": None,
            "function": None,
            "secondaryFunction": None,
            "targetAsset": {
                "folder": "/Game/UI/UMG/Role",
                "name": "umg_role",
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
            }
        ],
    }


def main() -> int:
    catalog = load_json(CATALOG_PATH)
    rules = load_json(RULES_PATH)
    failures: list[str] = []

    valid_screen = screen_spec()
    if not validate_spec(valid_screen, catalog)["valid"]:
        failures.append("2560x1440 screen was rejected")

    for invalid_size in ([1920, 1080], [2580, 1440]):
        invalid_screen = deepcopy(valid_screen)
        invalid_screen["referenceSize"] = invalid_size
        codes = {
            error["code"]
            for error in validate_spec(invalid_screen, catalog)["errors"]
        }
        if "layout.project_design_resolution" not in codes:
            failures.append(f"screen size {invalid_size} was not rejected")

    prototype_screen = deepcopy(valid_screen)
    prototype_screen["mode"] = "prototype"
    prototype_screen["asset"] = {
        "folder": "/Game/UI/AIPrototype",
        "name": "umg_ai_role",
    }
    prototype_screen["referenceSize"] = [1920, 1080]
    prototype_codes = {
        error["code"]
        for error in validate_spec(prototype_screen, catalog)["errors"]
    }
    if "layout.project_design_resolution" not in prototype_codes:
        failures.append("prototype-mode project screen did not enforce 2560x1440")

    child_widget = deepcopy(valid_screen)
    child_widget["asset"] = {
        "folder": "/Game/UI/UMG/Role/Widgets",
        "name": "uw_role_item",
    }
    child_widget["referenceSize"] = [400, 232]
    child_widget["profile"].update(
        {
            "assetKind": "child-widget",
            "function": "item",
            "targetAsset": {
                "folder": "/Game/UI/UMG/Role/Widgets",
                "name": "uw_role_item",
            },
        }
    )
    if not validate_spec(child_widget, catalog)["valid"]:
        failures.append("local-size child widget was rejected")

    screen_rule_ids = {
        rule["id"] for rule in select_rules(valid_screen, rules)
    }
    child_rule_ids = {
        rule["id"] for rule in select_rules(child_widget, rules)
    }
    if "layout.project-design-resolution" not in screen_rule_ids:
        failures.append("screen did not select layout.project-design-resolution")
    if "layout.project-design-resolution" in child_rule_ids:
        failures.append("child widget selected screen-only resolution rule")

    print(
        json.dumps(
            {
                "ok": not failures,
                "checkedScreenSizes": 4,
                "checkedChildSizes": 1,
                "failures": failures,
            },
            indent=2,
        )
    )
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
