#!/usr/bin/env python3
"""Regression checks for cross-system UMG component production rules."""

from __future__ import annotations

from copy import deepcopy
import json
import sys
from pathlib import Path
from typing import Any

from prepare_build import build_plan
from select_rules import select_rules
from test_text_component_rules import split_header_spec
from validate_layout_spec import load_json, validate_spec


SKILL_ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = SKILL_ROOT / "references" / "component-catalog.json"
RULES_PATH = SKILL_ROOT / "references" / "rule-index.json"


def error_codes(spec: dict[str, Any], catalog: dict[str, Any]) -> set[str]:
    return {error["code"] for error in validate_spec(spec, catalog)["errors"]}


def expect_error(
    failures: list[str],
    label: str,
    spec: dict[str, Any],
    catalog: dict[str, Any],
    expected_code: str,
) -> None:
    codes = error_codes(spec, catalog)
    if expected_code not in codes:
        failures.append(f"{label}: expected {expected_code}, got {sorted(codes)}")


def composite_state_panel_spec() -> dict[str, Any]:
    """Return a tab-like pair of runtime-controlled passive state branches."""
    spec = deepcopy(split_header_spec())
    spec["profile"]["interactive"] = True
    spec["nodes"].extend([
        {
            "id": "tab-button",
            "name": "BtnTab",
            "role": "input.button",
            "parent": "header",
            "rect": [0.30, 0.055, 0.12, 0.06],
            "anchor": "left-top",
            "properties": {"enabled": True},
        },
        {
            "id": "tab-button-content",
            "name": "PanelTabContent",
            "role": "container.canvas",
            "parent": "tab-button",
            "rect": [0.30, 0.055, 0.12, 0.06],
            "anchor": "left-top",
            "buttonSlot": {
                "padding": [0, 0, 0, 0],
                "horizontalAlignment": "Fill",
                "verticalAlignment": "Fill",
            },
            "properties": {},
        },
        {
            "id": "tab-selected",
            "name": "PanelTabSelected",
            "role": "container.canvas",
            "parent": "tab-button-content",
            "rect": [0.30, 0.055, 0.12, 0.06],
            "anchor": "left-top",
            "isVariable": True,
            "properties": {"visibility": "Collapsed"},
        },
        {
            "id": "tab-unselected",
            "name": "PanelTabUnselected",
            "role": "container.canvas",
            "parent": "tab-button-content",
            "rect": [0.30, 0.055, 0.12, 0.06],
            "anchor": "left-top",
            "isVariable": True,
            "properties": {"visibility": "SelfHitTestInvisible"},
        },
    ])
    return spec


def main() -> int:
    catalog = load_json(CATALOG_PATH)
    rules = load_json(RULES_PATH)
    failures: list[str] = []

    valid = split_header_spec()
    valid_report = validate_spec(valid, catalog)
    if not valid_report["valid"]:
        failures.append(f"valid common-rule spec rejected: {valid_report['errors']}")

    selected_rule_ids = {rule["id"] for rule in select_rules(valid, rules)}
    expected_rule_ids = {
        "layout.localized-content-adaptation",
        "interaction.passive-self-only",
        "widget.runtime-variable",
        "text.even-font-size",
        "text.explicit-wrap-width",
    }
    if not expected_rule_ids <= selected_rule_ids:
        failures.append(
            "common rule selection omitted: "
            + ", ".join(sorted(expected_rule_ids - selected_rule_ids))
        )

    odd_font = deepcopy(valid)
    odd_font["nodes"][2]["properties"]["font"]["size"] = 25
    expect_error(failures, "odd font", odd_font, catalog, "text.font_size.even")

    missing_font = deepcopy(valid)
    del missing_font["nodes"][2]["properties"]["font"]
    expect_error(failures, "missing font", missing_font, catalog, "text.font.required")

    missing_color = deepcopy(valid)
    del missing_color["nodes"][2]["properties"]["color"]
    expect_error(failures, "missing color", missing_color, catalog, "text.color.required")

    zero_wrap = deepcopy(valid)
    zero_wrap["nodes"][4]["properties"]["wrapTextAt"] = 0
    expect_error(failures, "zero wrap width", zero_wrap, catalog, "text.wrap_width.positive")

    missing_wrap = deepcopy(valid)
    del missing_wrap["nodes"][4]["properties"]["wrapTextAt"]
    expect_error(failures, "missing wrap width", missing_wrap, catalog, "text.wrap_width.required")

    fixed_wrapping_height = deepcopy(valid)
    fixed_wrapping_height["nodes"][4]["slotLayout"]["autoSize"] = False
    expect_error(
        failures,
        "wrapping text without auto size",
        fixed_wrapping_height,
        catalog,
        "text.adaptive_slot.auto_size",
    )

    wrong_slot_parent = deepcopy(valid)
    wrong_slot_parent["nodes"][1]["role"] = "container.vertical"
    wrong_slot_parent["nodes"][1]["name"] = "VerTaskHeader"
    expect_error(
        failures,
        "slot layout under non-canvas",
        wrong_slot_parent,
        catalog,
        "slot_layout.parent",
    )

    passive_visible = deepcopy(valid)
    passive_visible["nodes"][3]["properties"]["visibility"] = "Visible"
    expect_error(
        failures,
        "passive visible",
        passive_visible,
        catalog,
        "widget.passive_visibility",
    )

    passive_all_children_disabled = deepcopy(valid)
    passive_all_children_disabled["nodes"][3]["properties"]["visibility"] = "HitTestInvisible"
    expect_error(
        failures,
        "passive self and children hit-test invisible",
        passive_all_children_disabled,
        catalog,
        "widget.passive_visibility",
    )

    composite_states = composite_state_panel_spec()
    composite_report = validate_spec(composite_states, catalog)
    if not composite_report["valid"]:
        failures.append(
            "active SelfHitTestInvisible/inactive Collapsed composite state panels rejected: "
            f"{composite_report['errors']}"
        )
    if not any(node.get("role") == "input.button" for node in composite_states["nodes"]):
        failures.append("interactive composite state fixture omitted its Btn trigger owner")
    composite_plan = build_plan(Path("composite-state-test.json"), composite_states, catalog, rules)
    composite_values = {
        step["stepId"]: step["arguments"]["values"]
        for step in composite_plan["steps"]
        if step["stepId"] in {
            "set-widget-properties-tab-selected",
            "set-widget-properties-tab-unselected",
        }
    }
    if composite_values.get("set-widget-properties-tab-selected", {}).get("visibility") != "Collapsed":
        failures.append("planner did not preserve the inactive selected-state visibility")
    if composite_values.get("set-widget-properties-tab-unselected", {}).get("visibility") != "SelfHitTestInvisible":
        failures.append("planner did not preserve the active default-unselected visibility")

    hidden_inactive_state = deepcopy(composite_states)
    hidden_inactive_state["nodes"][-2]["properties"]["visibility"] = "Hidden"
    hidden_report = validate_spec(hidden_inactive_state, catalog)
    if not hidden_report["valid"]:
        failures.append(
            "inactive Hidden composite state panel rejected: "
            f"{hidden_report['errors']}"
        )

    state_without_button = composite_state_panel_spec()
    state_without_button["nodes"] = [
        node
        for node in state_without_button["nodes"]
        if node["id"] not in {"tab-button", "tab-button-content"}
    ]
    for node in state_without_button["nodes"]:
        if node["id"] in {"tab-selected", "tab-unselected"}:
            node["parent"] = "header"
    expect_error(
        failures,
        "interactive state family without Btn owner",
        state_without_button,
        catalog,
        "interaction.button_trigger.missing",
    )

    state_without_button_with_unrelated_list = deepcopy(state_without_button)
    state_without_button_with_unrelated_list["nodes"].append({
        "id": "unrelated-list",
        "name": "ListUnrelated",
        "role": "collection.lua-list",
        "parent": "header",
        "rect": [0.45, 0.10, 0.20, 0.40],
        "anchor": "left-top",
        "isVariable": True,
        "properties": {
            "entryWidgetClass": "/Game/UI/UMG/Test/Widgets/uw_test_entry.uw_test_entry_C",
            "selectionMode": "Single",
        },
    })
    expect_error(
        failures,
        "interactive state family cannot use an unrelated collection as its trigger owner",
        state_without_button_with_unrelated_list,
        catalog,
        "interaction.button_trigger.missing",
    )

    invalid_variable_flag = deepcopy(valid)
    invalid_variable_flag["nodes"][2]["isVariable"] = "yes"
    expect_error(
        failures,
        "non-boolean Is Variable flag",
        invalid_variable_flag,
        catalog,
        "widget.is_variable.boolean",
    )

    variable_spec = deepcopy(valid)
    variable_spec["nodes"][2]["isVariable"] = True
    variable_spec["nodes"][3]["isVariable"] = False
    variable_report = validate_spec(variable_spec, catalog)
    if not variable_report["valid"]:
        failures.append(f"valid variable decisions rejected: {variable_report['errors']}")

    plan = build_plan(Path("common-widget-test.json"), variable_spec, catalog, rules)
    variable_steps = {
        step["stepId"]: step
        for step in plan["steps"]
        if step["toolName"] == "ToggleWidgetAsVariable"
    }
    expected_variable_values = {
        "toggle-widget-variable-root": False,
        "toggle-widget-variable-header": False,
        "toggle-widget-variable-header-title": True,
        "toggle-widget-variable-header-separator": False,
        "toggle-widget-variable-paragraph": False,
    }
    actual_variable_values = {
        step_id: step["arguments"]["bIsVariable"]
        for step_id, step in variable_steps.items()
    }
    if actual_variable_values != expected_variable_values:
        failures.append(
            "Is Variable planner mapping mismatch: "
            f"expected {expected_variable_values!r}, got {actual_variable_values!r}"
        )
    title_values = next(
        step["arguments"]["values"]
        for step in plan["steps"]
        if step["stepId"] == "set-widget-properties-header-title"
    )
    if title_values.get("visibility") != "SelfHitTestInvisible":
        failures.append("planner did not apply passive SelfHitTestInvisible visibility")
    if title_values.get("font") != {"size": 26}:
        failures.append(f"font mapping mismatch: {title_values.get('font')!r}")
    expected_title_color = {
        "specifiedColor": variable_spec["nodes"][2]["properties"]["color"],
        "colorUseRule": "UseColor_Specified",
    }
    if title_values.get("colorAndOpacity") != expected_title_color:
        failures.append(
            "TextBlock color was not lowered to a complete SlateColor: "
            f"{title_values.get('colorAndOpacity')!r}"
        )
    separator_values = next(
        step["arguments"]["values"]
        for step in plan["steps"]
        if step["stepId"] == "set-widget-properties-header-separator"
    )
    expected_separator_color = variable_spec["nodes"][3]["properties"]["color"]
    if separator_values.get("colorAndOpacity") != expected_separator_color:
        failures.append(
            "GameImage color must remain a flat LinearColor: "
            f"{separator_values.get('colorAndOpacity')!r}"
        )

    paragraph_values = next(
        step["arguments"]["values"]
        for step in plan["steps"]
        if step["stepId"] == "set-widget-properties-paragraph"
    )
    if paragraph_values.get("wrapTextAt") != 310:
        failures.append("Wrap Text At property was not mapped to wrapTextAt")

    paragraph_slot = next(
        step["arguments"]["values"]
        for step in plan["steps"]
        if step["stepId"] == "set-slot-properties-paragraph"
    )
    expected_slot = {
        "layoutData": {
            "offsets": {"left": 13, "top": 89.3, "right": 89.6, "bottom": 48},
            "anchors": {
                "minimum": {"x": 0, "y": 0},
                "maximum": {"x": 1, "y": 0},
            },
            "alignment": {"x": 0, "y": 0},
        },
        "bAutoSize": True,
        "zOrder": 0,
    }
    if paragraph_slot != expected_slot:
        failures.append(f"adaptive slot mapping mismatch: {paragraph_slot!r}")

    spacer_spec = deepcopy(valid)
    spacer_spec["nodes"].append({
        "id": "protected-spacer",
        "name": "ProtectedSpacer",
        "role": "layout.spacer",
        "parent": "header",
        "rect": [0.20, 0.06, 0.10, 0.05],
        "anchor": "left-top",
        "properties": {"size": [622, 1280]},
    })
    spacer_report = validate_spec(spacer_spec, catalog)
    if not spacer_report["valid"]:
        failures.append(f"valid slot-sized Spacer rejected: {spacer_report['errors']}")
    spacer_plan = build_plan(Path("spacer-write-test.json"), spacer_spec, catalog, rules)
    spacer_values = next(
        step["arguments"]["values"]
        for step in spacer_plan["steps"]
        if step["stepId"] == "set-widget-properties-protected-spacer"
    )
    if "size" in spacer_values:
        failures.append("planner attempted to write read-only Spacer.size")
    if spacer_values.get("visibility") != "SelfHitTestInvisible":
        failures.append("planner dropped passive Spacer visibility while skipping size")
    if not any(
        "protected-spacer: size is read-only" in warning
        for warning in spacer_plan["warnings"]
    ):
        failures.append("planner did not report the skipped read-only Spacer.size")

    print(json.dumps({
        "ok": not failures,
        "checkedValidLayouts": 5,
        "checkedFailureModes": 12,
        "checkedPlannerDefaults": 7,
        "checkedSelectedRules": len(expected_rule_ids),
        "failures": failures,
    }, indent=2, ensure_ascii=False))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
