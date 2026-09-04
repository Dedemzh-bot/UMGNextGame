#!/usr/bin/env python3
"""Regression checks for project data-driven list Widget Blueprint rules."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

from prepare_build import build_plan
from select_rules import select_rules
from validate_layout_spec import load_json, validate_spec


SKILL_ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = SKILL_ROOT / "references" / "component-catalog.json"
RULES_PATH = SKILL_ROOT / "references" / "rule-index.json"
SCHEMA_PATH = SKILL_ROOT / "assets" / "ui-layout-spec.schema.json"


def container_spec() -> dict[str, Any]:
    return {
        "version": "0.2",
        "mode": "production",
        "asset": {
            "folder": "/Game/UI/UMG/Fight/Widgets",
            "name": "uw_fight_task",
        },
        "referenceSize": [400, 232],
        "profile": {
            "adaptive": False,
            "interactive": False,
            "hasText": False,
            "containsRepeatedElements": True,
            "regionGrouping": True,
            "listRole": "container",
            "collectionSizing": "fixed-viewport",
            "parentClass": "/Script/UMG.UserWidget",
            "assetKind": "child-widget",
            "system": "fight",
            "systemFolder": "Fight",
            "subsystem": None,
            "function": "task",
            "secondaryFunction": None,
            "targetAsset": {
                "folder": "/Game/UI/UMG/Fight/Widgets",
                "name": "uw_fight_task",
                "integrationAsset": "/Game/UI/UMG/Fight/umg_fight",
            },
        },
        "nodes": [
            {
                "id": "root",
                "name": "PanelTaskRoot",
                "role": "screen.root",
                "parent": None,
                "rect": [0, 0, 1, 1],
                "anchor": "left-top",
                "properties": {},
            },
            {
                "id": "task-list",
                "name": "ListTask",
                "role": "collection.lua-list",
                "isVariable": True,
                "parent": "root",
                "rect": [0, 0, 1, 1],
                "anchor": "left-top",
                "regionPurpose": "task-list",
                "properties": {
                    "entryWidgetClass": {
                        "refPath": "/Game/UI/UMG/Fight/Widgets/uw_fight_task_list.uw_fight_task_list_C"
                    },
                    "orientation": "Orient_Vertical",
                    "selectionMode": "Single",
                    "verticalEntrySpacing": 8,
                    "designerPreviewEntries": 2,
                },
            },
        ],
    }


def entry_spec() -> dict[str, Any]:
    spec = container_spec()
    spec["asset"]["name"] = "uw_fight_task_list"
    spec["referenceSize"] = [400, 112]
    spec["profile"].update(
        {
            "containsRepeatedElements": False,
            "regionGrouping": False,
            "listRole": "entry",
            "parentClass": "/Script/UIFramework.ListViewItem",
            "secondaryFunction": "list",
        }
    )
    spec["profile"].pop("collectionSizing", None)
    spec["profile"]["targetAsset"]["name"] = "uw_fight_task_list"
    spec["nodes"] = [
        {
            "id": "root",
            "name": "PanelTaskEntryRoot",
            "role": "screen.root",
            "parent": None,
            "rect": [0, 0, 1, 1],
            "anchor": "left-top",
            "properties": {},
        },
        {
            "id": "entry-panel",
            "name": "PanelTaskEntry",
            "role": "container.canvas",
            "parent": "root",
            "rect": [0, 0, 1, 1],
            "anchor": "left-top",
            "slotLayout": {
                "anchors": {"minimum": [0, 0], "maximum": [0, 0]},
                "offsets": {"left": 0, "top": 0, "right": 400, "bottom": 112},
                "alignment": [0, 0],
                "autoSize": False,
            },
            "properties": {},
        },
    ]
    return spec


def tile_container_spec() -> dict[str, Any]:
    spec = container_spec()
    spec["asset"]["name"] = "uw_fight_grid"
    spec["profile"]["function"] = "grid"
    spec["profile"]["targetAsset"]["name"] = "uw_fight_grid"
    spec["nodes"][1] = {
        "id": "grid-tile",
        "name": "TileGrid",
        "role": "collection.lua-tile",
        "isVariable": True,
        "parent": "root",
        "rect": [0, 0, 1, 1],
        "anchor": "left-top",
        "regionPurpose": "grid-tiles",
        "properties": {
            "entryWidgetClass": {
                "refPath": "/Game/UI/UMG/Fight/Widgets/uw_fight_grid_list.uw_fight_grid_list_C"
            },
            "entryWidth": 120,
            "entryHeight": 80,
            "orientation": "Orient_Vertical",
            "horizontalEntrySpacing": 0,
            "verticalEntrySpacing": 0,
            "designerPreviewEntries": 6,
        },
    }
    return spec


def error_codes(report: dict[str, Any]) -> set[str]:
    return {error["code"] for error in report["errors"]}


def planned_parent_class(spec: dict[str, Any]) -> str:
    catalog = load_json(CATALOG_PATH)
    rules = load_json(RULES_PATH)
    plan = build_plan(
        Path("dynamic-list-parent-class-test.json"),
        spec,
        catalog,
        rules,
    )
    create_step = next(
        step
        for step in plan["steps"]
        if step["stepId"] == "create-blueprint"
    )
    return create_step["arguments"]["parentClass"]["refPath"]


def test_build_plan_uses_dynamic_list_entry_parent_class() -> None:
    assert planned_parent_class(entry_spec()) == (
        "/Script/UIFramework.ListViewItem"
    )


def test_build_plan_defaults_parent_class_to_user_widget() -> None:
    spec = container_spec()
    del spec["profile"]["parentClass"]
    assert planned_parent_class(spec) == "/Script/UMG.UserWidget"


def expect_error(
    failures: list[str],
    label: str,
    spec: dict[str, Any],
    catalog: dict[str, Any],
    expected_code: str,
) -> None:
    codes = error_codes(validate_spec(spec, catalog))
    if expected_code not in codes:
        failures.append(
            f"{label}: expected {expected_code}, got {sorted(codes)}"
        )


def main() -> int:
    catalog = load_json(CATALOG_PATH)
    rules = load_json(RULES_PATH)
    schema = load_json(SCHEMA_PATH)
    failures: list[str] = []

    node_schema = schema["properties"]["nodes"]["items"]["properties"]
    if node_schema.get("isVariable") != {"type": "boolean"}:
        failures.append("schema must expose node-level isVariable as a boolean")
    profile_schema = schema["properties"]["profile"]["properties"]
    if profile_schema.get("collectionSizing") != {
        "enum": ["show-all", "fixed-viewport"]
    }:
        failures.append("schema must expose the collection sizing contract")

    for label, spec in [
        ("container", container_spec()),
        ("entry", entry_spec()),
        ("tile container", tile_container_spec()),
    ]:
        report = validate_spec(spec, catalog)
        if not report["valid"]:
            failures.append(f"valid {label} spec rejected: {report['errors']}")

    missing_collection = container_spec()
    missing_collection["nodes"] = missing_collection["nodes"][:1]
    expect_error(
        failures,
        "missing collection",
        missing_collection,
        catalog,
        "list.container.missing",
    )

    missing_entry_class = container_spec()
    del missing_entry_class["nodes"][1]["properties"]["entryWidgetClass"]
    expect_error(
        failures,
        "missing entry class",
        missing_entry_class,
        catalog,
        "list.entry_widget_class",
    )

    invalid_preview_count = container_spec()
    invalid_preview_count["nodes"][1]["properties"][
        "designerPreviewEntries"
    ] = 21
    expect_error(
        failures,
        "preview count",
        invalid_preview_count,
        catalog,
        "list.preview_count",
    )

    tile_with_outward_spacing = tile_container_spec()
    tile_with_outward_spacing["nodes"][1]["properties"][
        "horizontalEntrySpacing"
    ] = 12
    expect_error(
        failures,
        "tile spacing must use entry pitch",
        tile_with_outward_spacing,
        catalog,
        "tile.entry_spacing.inward_only",
    )

    tile_without_entry_width = tile_container_spec()
    del tile_without_entry_width["nodes"][1]["properties"]["entryWidth"]
    expect_error(
        failures,
        "tile entry width",
        tile_without_entry_width,
        catalog,
        "tile.entry_size.required",
    )

    missing_collection_sizing = container_spec()
    del missing_collection_sizing["profile"]["collectionSizing"]
    expect_error(
        failures,
        "missing collection sizing",
        missing_collection_sizing,
        catalog,
        "list.collection_sizing",
    )

    invalid_collection_sizing = container_spec()
    invalid_collection_sizing["profile"]["collectionSizing"] = "automatic"
    expect_error(
        failures,
        "invalid collection sizing",
        invalid_collection_sizing,
        catalog,
        "list.collection_sizing",
    )

    show_all_without_auto_size = container_spec()
    show_all_without_auto_size["profile"]["collectionSizing"] = "show-all"
    expect_error(
        failures,
        "show-all missing auto size",
        show_all_without_auto_size,
        catalog,
        "list.show_all.auto_size",
    )

    show_all = container_spec()
    show_all["profile"]["collectionSizing"] = "show-all"
    show_all["nodes"][1]["slotLayout"] = {
        "anchors": {"minimum": [0, 0], "maximum": [0, 0]},
        "offsets": {"left": 0, "top": 0, "right": 400, "bottom": 232},
        "alignment": [0, 0],
        "autoSize": True,
    }
    show_all_report = validate_spec(show_all, catalog)
    if not show_all_report["valid"]:
        failures.append(
            f"valid show-all spec rejected: {show_all_report['errors']}"
        )

    missing_variable_flag = container_spec()
    del missing_variable_flag["nodes"][1]["isVariable"]
    expect_error(
        failures,
        "collection variable flag",
        missing_variable_flag,
        catalog,
        "widget.is_variable.collection_required",
    )

    wrong_parent = entry_spec()
    wrong_parent["profile"]["parentClass"] = "/Script/UMG.UserWidget"
    expect_error(
        failures,
        "entry parent",
        wrong_parent,
        catalog,
        "list.entry.parent_class",
    )

    wrong_secondary = entry_spec()
    wrong_secondary["profile"]["secondaryFunction"] = "item"
    wrong_secondary["profile"]["targetAsset"]["name"] = (
        "uw_fight_task_item"
    )
    wrong_secondary["asset"]["name"] = "uw_fight_task_item"
    expect_error(
        failures,
        "entry suffix",
        wrong_secondary,
        catalog,
        "list.entry.secondary_function",
    )
    full_stretch_entry = entry_spec()
    full_stretch_entry["nodes"][1]["slotLayout"] = {
        "anchors": {"minimum": [0, 0], "maximum": [1, 1]},
        "offsets": {"left": 0, "top": 0, "right": 0, "bottom": 0},
        "alignment": [0, 0],
        "autoSize": False,
    }
    expect_error(
        failures,
        "entry first Panel explicit size",
        full_stretch_entry,
        catalog,
        "list.entry.root_size",
    )

    measured_content_entry = deepcopy(full_stretch_entry)
    measured_content_entry["nodes"][1]["contentDrivenSize"] = {
        "verified": True,
        "measuredDesiredSize": [400, 112],
        "evidenceId": "evidence-entry-measurement",
    }
    measured_content_report = validate_spec(measured_content_entry, catalog)
    if not measured_content_report["valid"]:
        failures.append(
            f"measured content-driven entry rejected: {measured_content_report['errors']}"
        )

    verified_only_entry = deepcopy(full_stretch_entry)
    verified_only_entry["nodes"][1]["contentDrivenSize"] = {"verified": True}
    expect_error(
        failures,
        "entry verified-only desired size",
        verified_only_entry,
        catalog,
        "list.entry.root_size",
    )

    nested_collection_entry = entry_spec()
    nested_collection_entry["nodes"].append(
        {
            "id": "nested-list",
            "name": "ListNested",
            "role": "collection.lua-list",
            "isVariable": True,
            "parent": "entry-panel",
            "rect": [0, 0, 1, 1],
            "anchor": "left-top",
            "properties": {
                "entryWidgetClass": {
                    "refPath": "/Game/UI/UMG/Fight/Widgets/uw_fight_task_list.uw_fight_task_list_C"
                }
            },
        }
    )
    expect_error(
        failures,
        "entry nested collection",
        nested_collection_entry,
        catalog,
        "list.entry.nested_collection",
    )

    container_rule_ids = {
        rule["id"] for rule in select_rules(container_spec(), rules)
    }
    entry_rule_ids = {
        rule["id"] for rule in select_rules(entry_spec(), rules)
    }
    if "collection.dynamic-container" not in container_rule_ids:
        failures.append("container rule selection omitted dynamic list rule")
    if "collection.sizing-contract" not in container_rule_ids:
        failures.append("container rule selection omitted sizing contract")
    if "collection.dynamic-entry" not in entry_rule_ids:
        failures.append("entry rule selection omitted dynamic entry rule")
    if {"repeat.prototype-groups", "text.prototype-copy"} & (
        container_rule_ids | entry_rule_ids
    ):
        failures.append("production dynamic list selected prototype-only rules")

    plan = build_plan(
        Path("dynamic-list-test.json"),
        container_spec(),
        catalog,
        rules,
    )
    property_step = next(
        step
        for step in plan["steps"]
        if step["stepId"] == "set-widget-properties-task-list"
    )
    values = property_step["arguments"]["values"]
    expected_properties = {
        "entryWidgetClass",
        "selectionMode",
        "verticalEntrySpacing",
        "numDesignerPreviewEntries",
    }
    if set(values) != expected_properties:
        failures.append(
            "collection property mapping mismatch: "
            f"expected {sorted(expected_properties)}, got {sorted(values)}"
        )
    if not any(
        "task-list: orientation is read-only" in warning
        for warning in plan["warnings"]
    ):
        failures.append("planner did not report the skipped read-only LuaListView.orientation")

    variable_step = next(
        (
            step
            for step in plan["steps"]
            if step["stepId"] == "toggle-widget-variable-task-list"
        ),
        None,
    )
    if variable_step is None:
        failures.append("collection plan omitted ToggleWidgetAsVariable")
    elif (
        variable_step["toolName"] != "ToggleWidgetAsVariable"
        or variable_step["arguments"].get("bIsVariable") is not True
    ):
        failures.append(f"collection variable step mismatch: {variable_step!r}")

    tile_plan = build_plan(
        Path("dynamic-tile-test.json"),
        tile_container_spec(),
        catalog,
        rules,
    )
    tile_values = next(
        step["arguments"]["values"]
        for step in tile_plan["steps"]
        if step["stepId"] == "set-widget-properties-grid-tile"
    )
    expected_tile_values = {
        "entryWidgetClass",
        "entryWidth",
        "entryHeight",
        "horizontalEntrySpacing",
        "verticalEntrySpacing",
        "numDesignerPreviewEntries",
    }
    if set(tile_values) != expected_tile_values:
        failures.append(
            "tile property mapping mismatch: "
            f"expected {sorted(expected_tile_values)}, got {sorted(tile_values)}"
        )
    if not any(
        "grid-tile: orientation is read-only" in warning
        for warning in tile_plan["warnings"]
    ):
        failures.append("planner did not report the skipped read-only LuaTileView.orientation")

    entry_parent_class = planned_parent_class(entry_spec())
    if entry_parent_class != "/Script/UIFramework.ListViewItem":
        failures.append(
            "entry plan parent class mismatch: "
            f"got {entry_parent_class!r}"
        )
    default_parent_spec = container_spec()
    del default_parent_spec["profile"]["parentClass"]
    default_parent_class = planned_parent_class(default_parent_spec)
    if default_parent_class != "/Script/UMG.UserWidget":
        failures.append(
            "default plan parent class mismatch: "
            f"got {default_parent_class!r}"
        )

    print(
        json.dumps(
            {
                "ok": not failures,
                "checkedValidLayouts": 5,
                "checkedFailureModes": 14,
                "checkedMappedProperties": len(expected_properties),
                "checkedTileMappedProperties": len(expected_tile_values),
                "checkedVariablePlan": True,
                "checkedParentClassPlans": 2,
                "failures": failures,
            },
            indent=2,
        )
    )
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
