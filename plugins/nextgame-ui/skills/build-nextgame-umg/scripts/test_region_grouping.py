#!/usr/bin/env python3
"""Regression checks for owner-supplied region-module structure rules."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

from prepare_build import build_plan
from validate_layout_spec import load_json, validate_spec

SKILL_ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = SKILL_ROOT / "references" / "component-catalog.json"
RULES_PATH = SKILL_ROOT / "references" / "rule-index.json"


def grouped_spec() -> dict[str, Any]:
    return {
        "version": "0.2",
        "mode": "prototype",
        "asset": {"folder": "/Game/UI/AIPrototype", "name": "umg_ai_region_test"},
        "referenceSize": [1920, 1080],
        "profile": {
            "adaptive": True,
            "interactive": False,
            "hasText": True,
            "containsRepeatedElements": False,
            "regionGrouping": True,
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
            },
            {
                "id": "background",
                "name": "ImgGlobalBackground",
                "role": "visual.image",
                "parent": "root",
                "rect": [0, 0, 1, 1],
                "anchor": "left-top",
                "rootLayer": "background",
                "properties": {},
            },
            {
                "id": "content-region",
                "name": "PanelContent",
                "role": "container.canvas",
                "parent": "root",
                "rect": [0.25, 0.25, 0.5, 0.5],
                "anchor": "center",
                "regionPurpose": "content",
                "properties": {},
            },
            {
                "id": "content",
                "name": "TxtContent",
                "role": "text.label",
                "parent": "content-region",
                "rect": [0.375, 0.375, 0.25, 0.25],
                "anchor": "left-top",
                "properties": {"font": {"size": 24}, "color": {"r": 1, "g": 1, "b": 1, "a": 1}},
            },
        ],
    }


def scene_model_clearance_spec() -> dict[str, Any]:
    """A scene-rendered model needs geometric clearance, not a WidgetTree region."""
    spec = grouped_spec()
    spec["asset"]["name"] = "umg_ai_scene_model_clearance_test"
    spec["nodes"] = [
        spec["nodes"][0],
        spec["nodes"][1],
        {
            "id": "left-region",
            "name": "PanelLeftDetails",
            "role": "container.canvas",
            "parent": "root",
            "rect": [0.02, 0.1, 0.18, 0.8],
            "anchor": "left-top",
            "regionPurpose": "left-details",
            "properties": {},
        },
        {
            "id": "left-content",
            "name": "TxtLeftDetails",
            "role": "text.label",
            "parent": "left-region",
            "rect": [0.04, 0.2, 0.14, 0.1],
            "anchor": "left-top",
            "properties": {
                "font": {"size": 24},
                "color": {"r": 1, "g": 1, "b": 1, "a": 1},
            },
        },
        {
            "id": "right-region",
            "name": "PanelRightDetails",
            "role": "container.canvas",
            "parent": "root",
            "rect": [0.78, 0.1, 0.2, 0.8],
            "anchor": "right-top",
            "regionPurpose": "right-details",
            "properties": {},
        },
        {
            "id": "right-content",
            "name": "TxtRightDetails",
            "role": "text.label",
            "parent": "right-region",
            "rect": [0.8, 0.2, 0.16, 0.1],
            "anchor": "left-top",
            "properties": {
                "font": {"size": 24},
                "color": {"r": 1, "g": 1, "b": 1, "a": 1},
            },
        },
    ]
    return spec


def error_codes(report: dict[str, Any]) -> set[str]:
    return {error["code"] for error in report["errors"]}


def expect_error(
    failures: list[str],
    label: str,
    spec: dict[str, Any],
    catalog: dict[str, Any],
    expected_code: str,
) -> None:
    codes = error_codes(validate_spec(spec, catalog))
    if expected_code not in codes:
        failures.append(f"{label}: expected {expected_code}, got {sorted(codes)}")


def main() -> int:
    catalog = load_json(CATALOG_PATH)
    rules = load_json(RULES_PATH)
    failures: list[str] = []

    valid_spec = grouped_spec()
    valid_report = validate_spec(valid_spec, catalog)
    if not valid_report["valid"]:
        failures.append(f"grouped spec rejected: {valid_report['errors']}")

    flattened = grouped_spec()
    flattened["nodes"] = [flattened["nodes"][0], deepcopy(flattened["nodes"][3])]
    flattened["nodes"][1]["parent"] = "root"
    expect_error(failures, "flattened root content", flattened, catalog, "region.root_child.ungrouped")
    expect_error(failures, "missing region", flattened, catalog, "region.required")

    invalid_region_role = grouped_spec()
    invalid_region_role["nodes"][2]["role"] = "visual.image"
    invalid_region_role["nodes"][2]["name"] = "ImgContent"
    invalid_region_role["nodes"] = invalid_region_role["nodes"][:3]
    expect_error(failures, "leaf declared as region", invalid_region_role, catalog, "region.container_role")

    empty_region = grouped_spec()
    empty_region["nodes"] = empty_region["nodes"][:3]
    expect_error(failures, "empty panel region", empty_region, catalog, "region.empty")

    nested_root_layer = grouped_spec()
    nested_root_layer["nodes"][3]["rootLayer"] = "overlay"
    expect_error(failures, "nested root layer", nested_root_layer, catalog, "region.root_layer.parent")

    outside_parent = grouped_spec()
    outside_parent["nodes"][3]["rect"] = [0.6, 0.6, 0.25, 0.25]
    expect_error(failures, "child outside region", outside_parent, catalog, "tree.rect.outside_parent")

    list_region = grouped_spec()
    list_region["profile"]["containsRepeatedElements"] = True
    list_region["nodes"][2] = {
        "id": "items-region",
        "name": "ListItems",
        "role": "collection.lua-list",
        "isVariable": True,
        "parent": "root",
        "rect": [0.25, 0.25, 0.5, 0.5],
        "anchor": "center",
        "regionPurpose": "items",
        "properties": {},
    }
    list_region["nodes"] = list_region["nodes"][:3]
    list_report = validate_spec(list_region, catalog)
    if not list_report["valid"]:
        failures.append(f"LuaListView leaf region rejected: {list_report['errors']}")

    scene_clearance = scene_model_clearance_spec()
    scene_clearance_report = validate_spec(scene_clearance, catalog)
    if not scene_clearance_report["valid"]:
        failures.append(
            "scene-rendered model clearance without a placeholder region rejected: "
            f"{scene_clearance_report['errors']}"
        )

    scene_clearance_plan = build_plan(
        Path("scene-model-clearance-test.json"),
        scene_clearance,
        catalog,
        rules,
    )
    expected_add_steps = {f"add-{node['id']}" for node in scene_clearance["nodes"]}
    actual_add_steps = {
        step["stepId"]
        for step in scene_clearance_plan["steps"]
        if step["stepId"].startswith("add-")
    }
    if actual_add_steps != expected_add_steps:
        failures.append(
            "scene-rendered model clearance plan synthesized an unrequested WidgetTree node: "
            f"expected {sorted(expected_add_steps)}, got {sorted(actual_add_steps)}"
        )

    plan = build_plan(Path("region-test.json"), valid_spec, catalog, rules)
    content_slot_step = next(
        step for step in plan["steps"] if step["stepId"] == "set-slot-properties-content"
    )
    offsets = content_slot_step["arguments"]["values"]["layoutData"]["offsets"]
    expected_offsets = {"left": 240.0, "top": 135.0, "right": 480.0, "bottom": 270.0}
    for key, expected in expected_offsets.items():
        if abs(offsets[key] - expected) > 0.0001:
            failures.append(
                f"nested CanvasPanel geometry {key}: expected {expected}, got {offsets[key]}"
            )

    region_roles = {
        "container.canvas",
        "container.overlay",
        "container.vertical",
        "container.horizontal",
        "container.wrap",
        "container.scale",
        "container.game-scroll",
        "collection.lua-list",
        "collection.lua-tile",
    }
    mapped_region_roles = {
        component["role"]
        for component in catalog["components"]
        if component.get("canRepresentRegion")
    }
    if mapped_region_roles != region_roles:
        failures.append(
            f"region-role mapping mismatch: expected {sorted(region_roles)}, got {sorted(mapped_region_roles)}"
        )

    print(
        json.dumps(
            {
                "ok": not failures,
                "checkedRegionRoles": len(region_roles),
                "checkedFailureModes": 6,
                "checkedSceneModelClearance": True,
                "failures": failures,
            },
            indent=2,
        )
    )
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
