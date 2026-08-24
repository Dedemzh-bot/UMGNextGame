#!/usr/bin/env python3
"""Regression checks for Widget Blueprint asset naming and target-folder rules."""

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
SCHEMA_PATH = SKILL_ROOT / "assets" / "ui-layout-spec.schema.json"


def base_spec() -> dict[str, Any]:
    return {
        "version": "0.2",
        "mode": "prototype",
        "asset": {"folder": "/Game/UI/AIPrototype", "name": "umg_ai_asset_rule_test"},
        "referenceSize": [2560, 1440],
        "profile": {
            "adaptive": True,
            "interactive": False,
            "hasText": False,
            "containsRepeatedElements": False,
            "regionGrouping": False,
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
            }
        ],
    }


def child_spec() -> dict[str, Any]:
    spec = base_spec()
    spec["referenceSize"] = [400, 232]
    spec["profile"].update(
        {
            "assetKind": "child-widget",
            "system": "bag",
            "systemFolder": "Bag",
            "subsystem": None,
            "function": "item",
            "secondaryFunction": "list",
            "targetAsset": {
                "folder": "/Game/UI/UMG/Bag/Widgets",
                "name": "uw_bag_item_list",
            },
        }
    )
    return spec


def production_spec(source: dict[str, Any]) -> dict[str, Any]:
    spec = deepcopy(source)
    spec["mode"] = "production"
    spec["asset"] = {
        "folder": spec["profile"]["targetAsset"]["folder"],
        "name": spec["profile"]["targetAsset"]["name"],
    }
    return spec


def project_common_entry_spec() -> dict[str, Any]:
    spec = child_spec()
    spec["referenceSize"] = [160, 160]
    spec["profile"].update(
        {
            "assetScope": "project-common",
            "system": "common",
            "systemFolder": "Common",
            "function": "material",
            "secondaryFunction": "list",
            "listRole": "entry",
            "parentClass": "/Script/UIFramework.ListViewItem",
            "targetAsset": {
                "folder": "/Game/UI/UMG/Widgets",
                "name": "uw_common_material_list",
            },
        }
    )
    spec["nodes"] = [
        spec["nodes"][0],
        {
            "id": "entry-panel",
            "name": "PanelMaterialEntry",
            "role": "container.canvas",
            "parent": "root",
            "rect": [0, 0, 1, 1],
            "anchor": "left-top",
            "slotLayout": {
                "anchors": {"minimum": [0, 0], "maximum": [0, 0]},
                "offsets": {"left": 0, "top": 0, "right": 160, "bottom": 160},
                "alignment": [0, 0],
                "autoSize": False,
            },
            "properties": {},
        },
    ]
    return production_spec(spec)


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
    schema = load_json(SCHEMA_PATH)
    failures: list[str] = []

    if schema["properties"]["profile"]["properties"].get("assetScope") != {
        "enum": ["system", "project-common"]
    }:
        failures.append("schema must expose system and project-common asset scopes")
    common_scope_condition = next(
        (
            condition
            for condition in schema.get("allOf", [])
            if condition.get("if", {})
            .get("properties", {})
            .get("profile", {})
            .get("properties", {})
            .get("assetScope")
            == {"const": "project-common"}
        ),
        None,
    )
    if common_scope_condition is None:
        failures.append("schema must constrain project-common assets")
    else:
        common_profile = common_scope_condition["then"]["properties"]["profile"]
        if common_profile.get("properties", {}).get("assetKind") != {"const": "child-widget"}:
            failures.append("schema must limit project-common scope to child widgets")
        if common_profile.get("properties", {}).get("system") != {"const": "common"}:
            failures.append("schema must require the common system token")
        if common_profile.get("properties", {}).get("subsystem") != {"const": None}:
            failures.append("schema must exclude a project-common subsystem segment")

    valid_specs = (
        ("prototype-screen", base_spec()),
        ("prototype-child-widget", child_spec()),
        ("production-screen", production_spec(base_spec())),
        ("production-child-widget", production_spec(child_spec())),
        ("production-project-common-entry", project_common_entry_spec()),
    )
    for label, spec in valid_specs:
        report = validate_spec(spec, catalog)
        if not report["valid"]:
            failures.append(f"valid {label} spec rejected: {report['errors']}")

    mismatched_system_folder = base_spec()
    mismatched_system_folder["profile"]["systemFolder"] = "World"
    mismatched_system_folder["profile"]["targetAsset"]["folder"] = "/Game/UI/UMG/World"
    expect_error(
        failures,
        "system-folder identity",
        mismatched_system_folder,
        catalog,
        "profile.system_folder.system_mismatch",
    )

    wrong_screen_folder = base_spec()
    wrong_screen_folder["profile"]["targetAsset"]["folder"] = "/Game/UI/UMG/Map/Widgets"
    expect_error(failures, "screen folder", wrong_screen_folder, catalog, "target.folder")

    wrong_child_folder = child_spec()
    wrong_child_folder["profile"]["targetAsset"]["folder"] = "/Game/UI/UMG/Bag"
    expect_error(failures, "child folder", wrong_child_folder, catalog, "target.folder")

    wrong_screen_name = base_spec()
    wrong_screen_name["profile"]["targetAsset"]["name"] = "umg_world"
    expect_error(failures, "screen name", wrong_screen_name, catalog, "target.name")

    wrong_child_name = child_spec()
    wrong_child_name["profile"]["targetAsset"]["name"] = "uw_bag_list"
    expect_error(failures, "child name", wrong_child_name, catalog, "target.name")

    wrong_production_target = production_spec(base_spec())
    wrong_production_target["asset"]["folder"] = "/Game/UI/UMG/World"
    expect_error(
        failures,
        "production target mismatch",
        wrong_production_target,
        catalog,
        "asset.production_target",
    )

    wrong_screen_resolution = base_spec()
    wrong_screen_resolution["referenceSize"] = [1920, 1080]
    expect_error(
        failures,
        "project screen resolution",
        wrong_screen_resolution,
        catalog,
        "layout.project_design_resolution",
    )

    wrong_wide_screen_resolution = production_spec(base_spec())
    wrong_wide_screen_resolution["referenceSize"] = [2580, 1440]
    expect_error(
        failures,
        "project wide screen resolution",
        wrong_wide_screen_resolution,
        catalog,
        "layout.project_design_resolution",
    )

    production_prototype_kind = production_spec(base_spec())
    production_prototype_kind["profile"]["assetKind"] = "prototype"
    expect_error(
        failures,
        "production asset kind",
        production_prototype_kind,
        catalog,
        "profile.production_asset_kind",
    )

    invalid_asset_scope = project_common_entry_spec()
    invalid_asset_scope["profile"]["assetScope"] = "shared"
    expect_error(
        failures,
        "invalid asset scope",
        invalid_asset_scope,
        catalog,
        "profile.asset_scope",
    )

    common_screen = production_spec(base_spec())
    common_screen["profile"]["assetScope"] = "project-common"
    common_screen["profile"]["system"] = "common"
    common_screen["profile"]["systemFolder"] = "Common"
    common_screen["profile"]["targetAsset"] = {
        "folder": "/Game/UI/UMG/Common",
        "name": "umg_common",
    }
    common_screen["asset"] = deepcopy(common_screen["profile"]["targetAsset"])
    expect_error(
        failures,
        "project-common screen",
        common_screen,
        catalog,
        "profile.asset_scope.asset_kind",
    )

    wrong_common_system = project_common_entry_spec()
    wrong_common_system["profile"]["system"] = "weapon"
    wrong_common_system["profile"]["systemFolder"] = "Weapon"
    wrong_common_system["profile"]["targetAsset"]["name"] = "uw_weapon_material_list"
    wrong_common_system["asset"]["name"] = "uw_weapon_material_list"
    expect_error(
        failures,
        "project-common system token",
        wrong_common_system,
        catalog,
        "profile.asset_scope.common_system",
    )

    common_with_subsystem = project_common_entry_spec()
    common_with_subsystem["profile"]["subsystem"] = "weapon"
    common_with_subsystem["profile"]["targetAsset"]["name"] = "uw_common_weapon_material_list"
    common_with_subsystem["asset"]["name"] = "uw_common_weapon_material_list"
    expect_error(
        failures,
        "project-common subsystem segment",
        common_with_subsystem,
        catalog,
        "profile.asset_scope.subsystem",
    )

    wrong_common_folder = project_common_entry_spec()
    wrong_common_folder["profile"]["targetAsset"]["folder"] = "/Game/UI/UMG/Common/Widgets"
    wrong_common_folder["asset"]["folder"] = "/Game/UI/UMG/Common/Widgets"
    expect_error(
        failures,
        "project-common folder",
        wrong_common_folder,
        catalog,
        "target.folder",
    )

    wrong_common_entry_parent = project_common_entry_spec()
    wrong_common_entry_parent["profile"]["parentClass"] = "/Script/UMG.UserWidget"
    expect_error(
        failures,
        "project-common entry parent",
        wrong_common_entry_parent,
        catalog,
        "list.entry.parent_class",
    )

    implicit_system_scope = project_common_entry_spec()
    del implicit_system_scope["profile"]["assetScope"]
    expect_error(
        failures,
        "project-common folder requires explicit scope",
        implicit_system_scope,
        catalog,
        "target.folder",
    )

    subsystem_screen = base_spec()
    subsystem_screen["profile"]["system"] = "bag"
    subsystem_screen["profile"]["systemFolder"] = "Bag"
    subsystem_screen["profile"]["subsystem"] = "sell"
    subsystem_screen["profile"]["targetAsset"] = {
        "folder": "/Game/UI/UMG/Bag",
        "name": "umg_bag_sell",
    }
    subsystem_report = validate_spec(subsystem_screen, catalog)
    if not subsystem_report["valid"]:
        failures.append(
            f"subsystem screen should remain in system folder: {subsystem_report['errors']}"
        )

    screen_rule_ids = {rule["id"] for rule in select_rules(base_spec(), rules)}
    child_rule_ids = {rule["id"] for rule in select_rules(child_spec(), rules)}
    production_rule_ids = {
        rule["id"] for rule in select_rules(production_spec(base_spec()), rules)
    }
    if "folder.system-widget-blueprints" not in screen_rule_ids:
        failures.append("screen rule selection omitted folder.system-widget-blueprints")
    if "layout.project-design-resolution" not in screen_rule_ids:
        failures.append("screen rule selection omitted layout.project-design-resolution")
    if "folder.child-widget" in screen_rule_ids:
        failures.append("screen rule selection incorrectly included folder.child-widget")
    if not {"folder.system-widget-blueprints", "folder.child-widget"}.issubset(child_rule_ids):
        failures.append("child-widget rule selection omitted required folder rules")
    if "production.asset-target" not in production_rule_ids:
        failures.append("production rule selection omitted production.asset-target")
    if {"prototype.asset-path", "prototype.asset-name"} & production_rule_ids:
        failures.append("production rule selection incorrectly included prototype asset rules")

    print(
        json.dumps(
            {
                "ok": not failures,
                "checkedValidLayouts": 6,
                "checkedFailureModes": 16,
                "failures": failures,
            },
            indent=2,
        )
    )
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
