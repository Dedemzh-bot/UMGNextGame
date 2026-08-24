#!/usr/bin/env python3
"""Validate actual Unreal Widget readback against accepted Requirement and verified Bundle."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from _document_contract_common import (
    READBACK_SCHEMA,
    is_accepted_in_scope,
    issue,
    layout_entry_class,
    load_json,
    load_layouts,
    mapping_widget,
    parse_aware_iso8601,
    prefix_issues,
    readback_indexes,
    resolve_request_path,
    result,
    runtime_collections,
    runtime_field_node_mappings,
    runtime_fields,
    sha256_file,
    validate_requirement_and_bundle,
    validate_schema_instance,
)


REQUIRED_READBACK_CHECK_TYPES = ("widget-tree", "key-properties")
SUPPORTED_READBACK_VERSIONS = {"0.1", "0.2", "0.3"}
PLACEMENT_RECT_TOLERANCE = 0.001
V03_SLOT_CLASS_PATHS = {
    "CanvasPanel": "/Script/UMG.CanvasPanelSlot",
    "Overlay": "/Script/UMG.OverlaySlot",
    "Button": "/Script/UMG.ButtonSlot",
    "HorizontalBox": "/Script/UMG.HorizontalBoxSlot",
    "VerticalBox": "/Script/UMG.VerticalBoxSlot",
    "GameScrollBox": "/Script/UMG.ScrollBoxSlot",
    "WrapBox": "/Script/UMG.WrapBoxSlot",
    "ScaleBox": "/Script/UMG.ScaleBoxSlot",
}
V03_SLOT_PARENT_CLASS_PATHS = {
    "CanvasPanel": "/Script/UMG.CanvasPanel",
    "Overlay": "/Script/UMG.Overlay",
    "Button": "/Script/UMG.Button",
    "HorizontalBox": "/Script/UMG.HorizontalBox",
    "VerticalBox": "/Script/UMG.VerticalBox",
    "GameScrollBox": "/Script/UIFramework.GameScrollBox",
    "WrapBox": "/Script/UMG.WrapBox",
    "ScaleBox": "/Script/UMG.ScaleBox",
}
VALID_DESIGN_SIZE_MODES = {"FillScreen", "Desired"}


def _numeric_sequence_delta(left: Any, right: Any) -> float | None:
    if not (
        isinstance(left, list)
        and isinstance(right, list)
        and len(left) == len(right)
        and all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in left + right)
    ):
        return None
    return max(abs(float(a) - float(b)) for a, b in zip(left, right)) if left else 0.0


def _widget_tree_path(asset_path: Any, widget_name: Any) -> str | None:
    if not isinstance(asset_path, str) or not asset_path or not isinstance(widget_name, str) or not widget_name:
        return None
    asset_name = asset_path.rsplit("/", 1)[-1]
    return f"{asset_path}.{asset_name}:WidgetTree.{widget_name}"


def _validate_design_size_modes(
    readback: dict[str, Any],
    requirement: dict[str, Any],
    bundle_assets: dict[str, dict[str, Any]],
    *,
    indexes: dict[str, Any],
    errors: list[dict[str, str]],
) -> None:
    """Validate the generated Widget CDO Designer size mode for each asset.

    Archived requirements remain valid without this newly recorded Editor-only
    property. Once the policy is enabled, every Bundle asset must carry actual
    readback and must bind to the accepted per-asset Requirement decision. Asset
    kind and Blueprint name are intentionally not used to infer the mode.
    """

    analysis_policy = requirement.get("analysisPolicy") if isinstance(requirement.get("analysisPolicy"), dict) else {}
    required = analysis_policy.get("designSizeModeRequired") is True
    requirement_plans = {
        item.get("id"): item
        for item in requirement.get("assetPlan", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    requirement_plan_positions = {
        item.get("id"): index
        for index, item in enumerate(requirement.get("assetPlan", []))
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    readback_positions = {
        asset.get("assetId"): index
        for index, asset in enumerate(readback.get("assets", []))
        if isinstance(asset, dict) and isinstance(asset.get("assetId"), str)
    }
    for asset_id, bundle_asset in bundle_assets.items():
        actual_asset = indexes.get("assets", {}).get(asset_id)
        if not isinstance(actual_asset, dict):
            continue
        asset_plan_id = bundle_asset.get("assetPlanId")
        requirement_plan = requirement_plans.get(asset_plan_id)
        decision = (
            requirement_plan.get("designSizeModeDecision")
            if isinstance(requirement_plan, dict)
            and isinstance(requirement_plan.get("designSizeModeDecision"), dict)
            else None
        )
        expected_mode = decision.get("mode") if isinstance(decision, dict) else None
        expected_mode_is_valid = expected_mode in VALID_DESIGN_SIZE_MODES
        plan_index = requirement_plan_positions.get(asset_plan_id, "*")
        decision_path = f"$.assetPlan[{plan_index}].designSizeModeDecision.mode"
        if required and not expected_mode_is_valid:
            errors.append(
                issue(
                    "design_size_mode.decision_missing",
                    decision_path,
                    f"Bundle asset {asset_id} must bind through assetPlanId to an accepted per-asset designSizeModeDecision.mode.",
                )
            )
        asset_index = readback_positions.get(asset_id, "*")
        path = f"$.assets[{asset_index}].designSizeMode"
        actual_mode = actual_asset.get("designSizeMode")
        if actual_mode is None:
            if required:
                errors.append(
                    issue(
                        "design_size_mode.missing",
                        path,
                        f"Bundle asset {asset_id} requires actual generated-class CDO DesignSizeMode readback.",
                    )
                )
            continue
        if actual_mode not in VALID_DESIGN_SIZE_MODES:
            errors.append(
                issue(
                    "design_size_mode.invalid",
                    path,
                    f"Bundle asset {asset_id} has unsupported DesignSizeMode {actual_mode!r}; expected FillScreen or Desired.",
                )
            )
            continue
        if expected_mode_is_valid and actual_mode != expected_mode:
            errors.append(
                issue(
                    "design_size_mode.mismatch",
                    path,
                    f"Bundle asset {asset_id} must match Requirement asset plan {asset_plan_id!r} DesignSizeMode decision {expected_mode}, got {actual_mode!r}.",
                )
            )

    _validate_design_size_mode_acquisition(
        readback,
        errors=errors,
    )


def _validate_design_size_mode_acquisition(
    readback: dict[str, Any],
    *,
    errors: list[dict[str, str]],
) -> None:
    """Bind any NxUE DesignSizeMode fallback to its exact readback JSON field.

    ``mixed.fieldFallbacks`` is the exhaustive list of fields acquired through
    NxUE rather than official Unreal MCP. Absence from that list therefore means
    the field came from official MCP. A full ``nxue-agent`` read has no official
    fields, so each recorded DesignSizeMode needs its own exact fallback row.
    """

    acquisition = readback.get("acquisition") if isinstance(readback.get("acquisition"), dict) else {}
    method = acquisition.get("method")
    if method not in {"nxue-agent", "mixed"}:
        return
    fallbacks = acquisition.get("fieldFallbacks") if isinstance(acquisition.get("fieldFallbacks"), list) else []
    fallback_records: list[tuple[int, str]] = [
        (index, item["jsonPath"])
        for index, item in enumerate(fallbacks)
        if isinstance(item, dict) and isinstance(item.get("jsonPath"), str)
    ]
    seen: set[str] = set()
    for index, fallback_path in fallback_records:
        if fallback_path in seen:
            errors.append(
                issue(
                    "acquisition.field_fallback_duplicate",
                    f"$.acquisition.fieldFallbacks[{index}].jsonPath",
                    f"Fallback JSON path {fallback_path!r} is duplicated.",
                )
            )
        seen.add(fallback_path)

    actual_mode_paths = {
        f"$.assets[{index}].designSizeMode"
        for index, asset in enumerate(readback.get("assets", []))
        if isinstance(asset, dict) and asset.get("designSizeMode") is not None
    }
    for index, fallback_path in fallback_records:
        if "designSizeMode" in fallback_path and fallback_path not in actual_mode_paths:
            errors.append(
                issue(
                    "acquisition.design_size_mode_path",
                    f"$.acquisition.fieldFallbacks[{index}].jsonPath",
                    "A DesignSizeMode fallback must bind one exact existing $.assets[i].designSizeMode field; wildcards and aggregate paths are not evidence.",
                )
            )

    if method == "nxue-agent":
        for mode_path in sorted(actual_mode_paths):
            if mode_path not in seen:
                errors.append(
                    issue(
                        "acquisition.design_size_mode_path_missing",
                        "$.acquisition.fieldFallbacks",
                        f"Full NxUE acquisition requires exact fallback evidence for {mode_path}.",
                    )
                )


def validate_readback_verification_checks(
    bundle: dict[str, Any],
    *,
    bundle_path: Path,
    readback_path: Path,
    errors: list[dict[str, str]],
) -> None:
    """Require per-asset passed checks whose artifact is this exact readback file."""

    bundle_assets = {
        asset.get("id"): asset
        for asset in bundle.get("assets", [])
        if isinstance(asset, dict) and isinstance(asset.get("id"), str)
    }
    verification = bundle.get("verification") if isinstance(bundle.get("verification"), dict) else {}
    checks = verification.get("checks") if isinstance(verification.get("checks"), list) else []
    covered: dict[tuple[str, str], int] = {}
    expected_readback_path = readback_path.resolve()

    for index, check in enumerate(checks):
        if not isinstance(check, dict) or check.get("type") not in REQUIRED_READBACK_CHECK_TYPES:
            continue
        check_path = f"$.verification.checks[{index}]"
        asset_id = check.get("assetId")
        if asset_id not in bundle_assets:
            errors.append(issue("verification.check_asset", f"{check_path}.assetId", "Readback verification check must reference a Bundle asset."))
            continue
        check_type = check["type"]
        covered[(asset_id, check_type)] = covered.get((asset_id, check_type), 0) + 1
        if check.get("status") != "passed":
            errors.append(issue("verification.check_status", f"{check_path}.status", f"{check_type} readback check must be passed."))

        raw_artifact = check.get("artifactPath")
        if not isinstance(raw_artifact, str):
            errors.append(issue("verification.artifact_path", f"{check_path}.artifactPath", "Readback verification check requires artifactPath."))
            continue
        try:
            artifact_path = resolve_request_path(bundle_path, raw_artifact)
        except ValueError as error:
            errors.append(issue("verification.artifact_path", f"{check_path}.artifactPath", str(error)))
            continue
        if artifact_path != expected_readback_path:
            errors.append(issue("verification.artifact_path", f"{check_path}.artifactPath", "artifactPath must resolve to the current UnrealWidgetReadback file."))

    for asset_id in bundle_assets:
        for check_type in REQUIRED_READBACK_CHECK_TYPES:
            if covered.get((asset_id, check_type), 0) == 0:
                errors.append(issue("verification.check_missing", "$.verification.checks", f"Bundle asset {asset_id} requires a passed {check_type} check for this readback."))


def validate_unreal_widget_readback(
    readback: Any,
    schema: dict[str, Any],
    *,
    readback_path: Path,
    requirement: Any,
    requirement_path: Path,
    bundle: Any,
    bundle_path: Path,
) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    if not isinstance(readback, dict):
        return result(validate_schema_instance(readback, schema), warnings)
    readback_version = readback.get("version")
    if readback_version not in SUPPORTED_READBACK_VERSIONS:
        return result([issue("readback.version", "$.version", f"Unsupported UnrealWidgetReadback version: {readback_version!r}.")], warnings)
    errors.extend(validate_schema_instance(readback, schema))
    bundle_version = bundle.get("version") if isinstance(bundle, dict) else None
    expected_readback_version = {"0.1": "0.1", "0.2": "0.2", "0.3": "0.3"}.get(bundle_version, "0.1")
    if readback_version != expected_readback_version:
        errors.append(issue("version.bundle_readback", "$.version", f"Bundle {bundle_version!r} requires UnrealWidgetReadback {expected_readback_version}."))
    upstream_errors, context = validate_requirement_and_bundle(
        requirement,
        bundle,
        requirement_path=requirement_path,
        bundle_path=bundle_path,
        check_linked_files=True,
    )
    errors.extend(upstream_errors)
    if not isinstance(readback, dict) or not isinstance(requirement, dict) or not isinstance(bundle, dict):
        return result(errors, warnings)

    captured_at = parse_aware_iso8601(readback.get("capturedAt"), "$.capturedAt", errors)
    execution = bundle.get("execution") if isinstance(bundle.get("execution"), dict) else {}
    completed_at = parse_aware_iso8601(execution.get("completedAt"), "$.execution.completedAt", errors)
    if captured_at is not None and completed_at is not None and captured_at < completed_at:
        errors.append(issue("time.readback_before_bundle", "$.capturedAt", "Readback capturedAt must not precede Bundle execution.completedAt."))
    acquisition = readback.get("acquisition") if isinstance(readback.get("acquisition"), dict) else {}
    if acquisition.get("method") == "nxue-agent" and not acquisition.get("fallbackReason"):
        errors.append(issue("acquisition.fallback_reason", "$.acquisition.fallbackReason", "NxUEAgent acquisition requires a concrete fallback reason."))
    if acquisition.get("method") == "mixed" and not acquisition.get("fieldFallbacks"):
        errors.append(issue("acquisition.field_fallbacks", "$.acquisition.fieldFallbacks", "Mixed acquisition requires field-level NxUE fallback records."))

    requirement_binding = readback.get("requirementBinding") if isinstance(readback.get("requirementBinding"), dict) else {}
    review = requirement.get("reviewGate") if isinstance(requirement.get("reviewGate"), dict) else {}
    expected_requirement = {
        "requestId": requirement.get("requestId"),
        "revision": requirement.get("revision"),
        "approvedContentSha256": review.get("approvedContentSha256"),
        "sha256": sha256_file(requirement_path),
    }
    if requirement_binding != expected_requirement:
        errors.append(issue("binding.requirement", "$.requirementBinding", "Readback Requirement binding does not match the actual current Requirement file."))
    bundle_binding = readback.get("bundleBinding") if isinstance(readback.get("bundleBinding"), dict) else {}
    expected_bundle = {"bundleId": bundle.get("bundleId"), "sha256": sha256_file(bundle_path)}
    if bundle_binding != expected_bundle:
        errors.append(issue("binding.bundle", "$.bundleBinding", "Readback Bundle binding does not match the actual Bundle file."))

    validate_readback_verification_checks(
        bundle,
        bundle_path=bundle_path,
        readback_path=readback_path,
        errors=errors,
    )

    indexes = readback_indexes(readback, errors)
    bundle_assets = {item.get("id"): item for item in bundle.get("assets", []) if isinstance(item, dict)}
    if set(indexes["assets"]) != set(bundle_assets):
        errors.append(issue("coverage.assets", "$.assets", "Readback assets must exactly cover Bundle assets."))
    for asset_id, bundle_asset in bundle_assets.items():
        actual_asset = indexes["assets"].get(asset_id)
        if isinstance(actual_asset, dict) and actual_asset.get("assetPath") != bundle_asset.get("assetPath"):
            errors.append(issue("identity.asset_path", "$.assets", f"Readback assetPath differs for {asset_id}."))
        if readback_version in {"0.2", "0.3"} and isinstance(actual_asset, dict):
            if actual_asset.get("representationKind") != bundle_asset.get("representationKind"):
                errors.append(issue("identity.representation_kind", "$.assets", f"Readback representationKind differs for {asset_id}."))
            expected_object_path = f"{bundle_asset.get('assetPath')}.{str(bundle_asset.get('assetPath')).rsplit('/', 1)[-1]}"
            expected_generated_class = f"{expected_object_path}_C"
            if actual_asset.get("assetObjectPath") != expected_object_path:
                errors.append(issue("identity.asset_object_path", "$.assets", f"Readback assetObjectPath differs for {asset_id}."))
            if actual_asset.get("generatedClassPath") != expected_generated_class:
                errors.append(issue("identity.generated_class", "$.assets", f"Readback generatedClassPath differs for {asset_id}."))

    _validate_design_size_modes(
        readback,
        requirement,
        bundle_assets,
        indexes=indexes,
        errors=errors,
    )

    bundle_mappings = {item.get("id"): item for item in bundle.get("nodeMappings", []) if isinstance(item, dict)}
    if set(indexes["mappings"]) != set(bundle_mappings):
        errors.append(issue("coverage.node_mappings", "$.assets[*].nodeMappings", "Readback must cover every Bundle nodeMapping exactly once and contain no extras."))
    layouts = load_layouts(bundle, bundle_path, errors)
    for mapping_id, mapping in bundle_mappings.items():
        record = indexes["mappings"].get(mapping_id)
        if record is None:
            continue
        asset_id, actual_mapping = record
        if asset_id != mapping.get("assetId") or actual_mapping.get("layoutNodeId") != mapping.get("layoutNodeId"):
            errors.append(issue("identity.node_mapping", "$.assets[*].nodeMappings", f"Readback mapping identity differs for {mapping_id}."))
            continue
        layout_node = layouts.get(asset_id, {}).get("nodes", {}).get(mapping.get("layoutNodeId"))
        widget = indexes["widgets"].get((asset_id, actual_mapping.get("widgetName")))
        if not isinstance(layout_node, dict) or not isinstance(widget, dict):
            errors.append(issue("identity.mapping_target", "$.assets[*].nodeMappings", f"Mapping {mapping_id} does not resolve to both a layout node and actual Widget."))
            continue
        if widget.get("widgetName") != layout_node.get("name"):
            errors.append(issue("identity.widget_name", "$.assets[*].widgets", f"Actual Widget name differs from mapped UILayoutSpec node for {mapping_id}."))
        parent_id = layout_node.get("parent")
        expected_parent = layouts.get(asset_id, {}).get("nodes", {}).get(parent_id, {}).get("name") if isinstance(parent_id, str) else None
        if widget.get("parentWidgetName") != expected_parent:
            errors.append(issue("identity.parent", "$.assets[*].widgets", f"Actual parent differs from mapped UILayoutSpec node for {mapping_id}."))

    accepted = context.get("acceptedClaimIds", set())
    mappings = list(bundle_mappings.values())
    readback_relation_by_id: dict[str, dict[str, Any]] = {}
    accepted_runtime_fields = runtime_fields(requirement, accepted)
    if readback_version in {"0.2", "0.3"}:
        readback_relation_by_id = _validate_reuse_readback_relations(
            readback,
            bundle,
            indexes=indexes,
            allowed_runtime_field_ids={field.get("id") for field in accepted_runtime_fields if isinstance(field.get("id"), str)},
            errors=errors,
        )
    for field in accepted_runtime_fields:
        raw_candidates = [mapping for mapping in mappings if field.get("id") in mapping.get("requirementRefs", [])]
        relation_candidates = [
            relation
            for relation in readback_relation_by_id.values()
            if field.get("id") in relation.get("runtimeFieldRefs", [])
        ]
        if relation_candidates:
            if raw_candidates or len(relation_candidates) != 1:
                errors.append(issue("runtime.mapping", "$.reuseRelations", f"Runtime field {field.get('id')} must resolve through exactly one nodeMapping or one verified reuse relation."))
            continue
        candidates = runtime_field_node_mappings(field, mappings, requirement, accepted)
        if not candidates:
            errors.append(issue("runtime.mapping", "$.assets[*].nodeMappings", f"Runtime field {field.get('id')} must resolve through exactly one nodeMapping or one complete mirrored mapping set across a reviewed exclusive state axis."))
            continue
        for mapping in candidates:
            layout_node = layouts.get(mapping.get("assetId"), {}).get("nodes", {}).get(mapping.get("layoutNodeId"), {})
            _, widget = mapping_widget(mapping, indexes)
            if layout_node.get("isVariable") is not True:
                errors.append(issue("runtime.layout_variable", "$.assets[*].nodeMappings", f"Runtime field {field.get('id')} maps to a layout node that is not isVariable."))
            if not isinstance(widget, dict) or widget.get("isVariable") is not True:
                errors.append(issue("runtime.actual_variable", "$.assets[*].widgets", f"Runtime field {field.get('id')} is not an actual Unreal variable."))

    for collection in runtime_collections(requirement, accepted):
        refs = {collection.get("id"), collection.get("containerElementId")}
        candidates = [mapping for mapping in mappings if refs & set(mapping.get("requirementRefs", []))]
        if len(candidates) != 1:
            errors.append(issue("collection.mapping", "$.assets[*].nodeMappings", f"Collection {collection.get('id')} must resolve through exactly one nodeMapping."))
            continue
        mapping = candidates[0]
        layout_node = layouts.get(mapping.get("assetId"), {}).get("nodes", {}).get(mapping.get("layoutNodeId"), {})
        _, widget = mapping_widget(mapping, indexes)
        if layout_node.get("isVariable") is not True or not isinstance(widget, dict) or widget.get("isVariable") is not True:
            errors.append(issue("collection.variable", "$.assets[*].widgets", f"Collection {collection.get('id')} must be variable in layout and actual Unreal readback."))
            continue
        if not any(token in str(widget.get("classPath")) for token in ("LuaListView", "LuaTileView")):
            errors.append(issue("collection.class", "$.assets[*].widgets", f"Collection {collection.get('id')} is not an actual LuaListView/LuaTileView."))
        actual_entry = widget.get("entryWidgetClass")
        if not isinstance(actual_entry, str) or not actual_entry:
            errors.append(issue("collection.entry_class", "$.assets[*].widgets", f"Collection {collection.get('id')} requires actual EntryWidgetClass."))
        expected_entry = layout_entry_class(layout_node)
        if expected_entry is not None and actual_entry != expected_entry:
            errors.append(issue("collection.entry_class_mismatch", "$.assets[*].widgets", f"Collection {collection.get('id')} EntryWidgetClass differs from the verified layout."))

    for model in requirement.get("stateModels", []):
        if not isinstance(model, dict) or not is_accepted_in_scope(model, accepted, require_scope=False):
            continue
        implementation = model.get("implementation") if isinstance(model.get("implementation"), dict) else {}
        for branch in implementation.get("branches", []):
            if not isinstance(branch, dict):
                continue
            panel_id = branch.get("panelElementId")
            state_id = branch.get("stateId")
            candidates = [
                mapping
                for mapping in mappings
                if panel_id in mapping.get("requirementRefs", []) and state_id in mapping.get("stateRefs", [])
            ]
            if len(candidates) != 1:
                errors.append(issue("state.branch_mapping", "$.assets[*].nodeMappings", f"State branch {state_id} must resolve through exactly one nodeMapping."))
                continue
            mapping = candidates[0]
            layout_node = layouts.get(mapping.get("assetId"), {}).get("nodes", {}).get(mapping.get("layoutNodeId"), {})
            _, widget = mapping_widget(mapping, indexes)
            if layout_node.get("isVariable") is not True:
                errors.append(issue("state.layout_variable", "$.assets[*].nodeMappings", f"State branch {state_id} Panel is not isVariable in UILayoutSpec."))
            if not isinstance(widget, dict) or widget.get("isVariable") is not True:
                errors.append(issue("state.actual_variable", "$.assets[*].widgets", f"State branch {state_id} Panel is not an actual Unreal variable."))
            if not isinstance(widget, dict) or not isinstance(widget.get("visibility"), str):
                errors.append(issue("state.visibility_missing", "$.assets[*].widgets", f"State branch {state_id} requires actual Visibility."))
            elif widget.get("visibility") != branch.get("visibility"):
                errors.append(issue("state.visibility_mismatch", "$.assets[*].widgets", f"State branch {state_id} actual Visibility differs from accepted Requirement."))

    return result(errors, warnings)


DUAL_SLOT_INTENT_KEYS = (
    "role",
    "standardName",
    "classPath",
    "parentRelation",
    "treeOrder",
    "zOrderRelation",
    "layout",
    "autoSize",
    "isVariable",
    "visibility",
)


def _validate_dual_named_slot_readback(
    actual: Any,
    expected: Any,
    *,
    source_asset_id: Any,
    indexes: dict[str, Any],
    path: str,
    errors: list[dict[str, str]],
) -> None:
    """Prove the dual extension layers from ordered, numeric actual sibling evidence."""

    actual_slots = actual if isinstance(actual, dict) else {}
    expected_slots = expected if isinstance(expected, dict) else {}
    for key in ("operation", "legacyStandardMigration", "legacyPreservedNames"):
        if actual_slots.get(key) != expected_slots.get(key):
            errors.append(issue("reuse.named_slots", f"{path}.{key}", f"Actual dual NamedSlot field {key} differs from Bundle intent."))

    actual_slot_items = actual_slots.get("slots") if isinstance(actual_slots.get("slots"), list) else []
    expected_slot_items = expected_slots.get("slots") if isinstance(expected_slots.get("slots"), list) else []
    for slot_index in range(min(len(actual_slot_items), len(expected_slot_items))):
        actual_slot = actual_slot_items[slot_index] if isinstance(actual_slot_items[slot_index], dict) else {}
        expected_slot = expected_slot_items[slot_index] if isinstance(expected_slot_items[slot_index], dict) else {}
        for key in DUAL_SLOT_INTENT_KEYS:
            if actual_slot.get(key) != expected_slot.get(key):
                errors.append(
                    issue(
                        "reuse.named_slots",
                        f"{path}.slots[{slot_index}].{key}",
                        f"Actual dual NamedSlot field {key} differs from Bundle intent.",
                    )
                )
    if len(actual_slot_items) != len(expected_slot_items):
        errors.append(issue("reuse.named_slots", f"{path}.slots", "Actual dual NamedSlot count differs from Bundle intent."))

    children = actual_slots.get("directRootChildren") if isinstance(actual_slots.get("directRootChildren"), list) else []
    layers = [item for item in actual_slots.get("directRootLayers", []) if isinstance(item, dict)] if isinstance(actual_slots.get("directRootLayers"), list) else []
    names = [item.get("name") for item in layers]
    sibling_indexes = [item.get("directSiblingIndex") for item in layers]
    tree_indexes = [item.get("treeIndex") for item in layers]
    if len(set(name for name in names if isinstance(name, str))) != len(names):
        errors.append(issue("reuse.layer_duplicate", f"{path}.directRootLayers", "Direct-root layer names must be unique."))
    if not all(isinstance(value, int) and not isinstance(value, bool) for value in sibling_indexes) or sorted(sibling_indexes) != list(range(len(layers))):
        errors.append(issue("reuse.layer_order", f"{path}.directRootLayers", "Direct sibling indexes must be unique and contiguous from zero."))
        ordered_layers = layers
    else:
        ordered_layers = sorted(layers, key=lambda item: item["directSiblingIndex"])
    ordered_names = [item.get("name") for item in ordered_layers]
    if children != ordered_names:
        errors.append(issue("reuse.layer_order", f"{path}.directRootChildren", "Ordered direct-root child names must equal the numeric layer snapshot."))
    ordered_tree_indexes = [item.get("treeIndex") for item in ordered_layers]
    if not all(isinstance(value, int) and not isinstance(value, bool) for value in tree_indexes) or any(
        left >= right for left, right in zip(ordered_tree_indexes, ordered_tree_indexes[1:])
    ):
        errors.append(issue("reuse.tree_order", f"{path}.directRootLayers", "Direct-root child tree indexes must increase strictly in sibling order."))

    parent_names = {item.get("parentWidgetName") for item in layers if isinstance(item.get("parentWidgetName"), str)}
    if len(parent_names) != 1:
        errors.append(issue("reuse.layer_parent", f"{path}.directRootLayers", "Every direct-root layer must name the same parent Widget."))
    parent_name = next(iter(parent_names), None)
    parent_widget = indexes.get("widgets", {}).get((source_asset_id, parent_name))
    if not isinstance(parent_widget, dict) or parent_widget.get("parentWidgetName") is not None:
        errors.append(issue("reuse.layer_parent", f"{path}.directRootLayers", "The shared layer parent must be the actual WidgetTree root."))
    source_widgets = [
        widget
        for (asset_id, _), widget in indexes.get("widgets", {}).items()
        if asset_id == source_asset_id and isinstance(widget, dict)
    ]
    actual_direct_names = {
        widget.get("widgetName")
        for widget in source_widgets
        if widget.get("parentWidgetName") == parent_name and isinstance(widget.get("widgetName"), str)
    }
    if set(children) != actual_direct_names:
        errors.append(issue("reuse.layer_coverage", f"{path}.directRootChildren", "The direct-root layer snapshot must cover every actual direct child exactly once."))
    for layer in layers:
        layer_widget = indexes.get("widgets", {}).get((source_asset_id, layer.get("name")))
        if not isinstance(layer_widget, dict) or layer_widget.get("parentWidgetName") != layer.get("parentWidgetName"):
            errors.append(issue("reuse.layer_widget_identity", f"{path}.directRootLayers", f"Direct-root layer {layer.get('name')!r} does not match an actual direct child Widget."))

    layer_by_name = {
        item.get("name"): item
        for item in layers
        if isinstance(item.get("name"), str)
    }
    slot_by_name = {
        item.get("standardName"): item
        for item in actual_slot_items
        if isinstance(item, dict) and isinstance(item.get("standardName"), str)
    }
    for slot_name in ("SlotDown", "SlotUp"):
        slot = slot_by_name.get(slot_name)
        layer = layer_by_name.get(slot_name)
        if not isinstance(slot, dict) or not isinstance(layer, dict):
            errors.append(issue("reuse.slot_missing", f"{path}.slots", f"Actual {slot_name} evidence is missing from the shared direct-root layers."))
            continue
        for slot_key, layer_key in (
            ("parentWidgetName", "parentWidgetName"),
            ("treeIndex", "treeIndex"),
            ("directSiblingIndex", "directSiblingIndex"),
            ("zOrder", "zOrder"),
        ):
            if slot.get(slot_key) != layer.get(layer_key):
                errors.append(issue("reuse.slot_snapshot", f"{path}.slots", f"{slot_name} {slot_key} differs from the direct-root layer snapshot."))
        widget = indexes.get("widgets", {}).get((source_asset_id, slot_name))
        if not isinstance(widget, dict):
            errors.append(issue("reuse.slot_widget_missing", f"{path}.slots", f"Actual WidgetTree is missing {slot_name}."))
        elif (
            widget.get("classPath") != slot.get("classPath")
            or widget.get("parentWidgetName") != slot.get("parentWidgetName")
            or widget.get("isVariable") != slot.get("isVariable")
            or widget.get("visibility") != slot.get("visibility")
        ):
            errors.append(issue("reuse.slot_widget_identity", f"{path}.slots", f"Actual Widget identity differs for {slot_name}."))

    if ordered_names:
        down = slot_by_name.get("SlotDown", {})
        up = slot_by_name.get("SlotUp", {})
        if down.get("directSiblingIndex") != 0 or ordered_names[0] != "SlotDown":
            errors.append(issue("reuse.slot_tree_order", f"{path}.slots[0]", "SlotDown must be the first direct child of the shared root."))
        last_index = len(ordered_names) - 1
        if up.get("directSiblingIndex") != last_index or ordered_names[-1] != "SlotUp":
            errors.append(issue("reuse.slot_tree_order", f"{path}.slots[1]", "SlotUp must be the last direct child of the shared root."))

    down_layer = layer_by_name.get("SlotDown")
    up_layer = layer_by_name.get("SlotUp")
    if isinstance(down_layer, dict) and isinstance(down_layer.get("zOrder"), int):
        other_z = [item.get("zOrder") for item in layers if item is not down_layer]
        if not other_z or not all(isinstance(value, int) and down_layer["zOrder"] < value for value in other_z):
            errors.append(issue("reuse.slot_z_order", f"{path}.slots[0].zOrder", "SlotDown ZOrder must be strictly lower than every direct sibling."))
    if isinstance(up_layer, dict) and isinstance(up_layer.get("zOrder"), int):
        other_z = [item.get("zOrder") for item in layers if item is not up_layer]
        if not other_z or not all(isinstance(value, int) and up_layer["zOrder"] > value for value in other_z):
            errors.append(issue("reuse.slot_z_order", f"{path}.slots[1].zOrder", "SlotUp ZOrder must be strictly higher than every direct sibling."))

    preserved_names = actual_slots.get("legacyPreservedNames") if isinstance(actual_slots.get("legacyPreservedNames"), list) else []
    for legacy_name in preserved_names:
        legacy_widget = indexes.get("widgets", {}).get((source_asset_id, legacy_name))
        if legacy_name not in layer_by_name or not isinstance(legacy_widget, dict) or legacy_widget.get("classPath") != "/Script/UMG.NamedSlot":
            errors.append(issue("reuse.legacy_slot_missing", f"{path}.legacyPreservedNames", f"Preserved legacy NamedSlot {legacy_name!r} is absent from the actual shared root."))

    migration = actual_slots.get("legacyStandardMigration") if isinstance(actual_slots.get("legacyStandardMigration"), dict) else {}
    old_name = migration.get("oldName")
    if isinstance(old_name, str) and (
        old_name in layer_by_name or isinstance(indexes.get("widgets", {}).get((source_asset_id, old_name)), dict)
    ):
        errors.append(issue("reuse.old_standard_slot_present", f"{path}.legacyStandardMigration.oldName", "The migrated old standard NamedSlot must not remain in the actual shared tree."))


def _validate_reuse_readback_relations(
    readback: dict[str, Any],
    bundle: dict[str, Any],
    *,
    indexes: dict[str, Any],
    allowed_runtime_field_ids: set[str],
    errors: list[dict[str, str]],
) -> dict[str, dict[str, Any]]:
    """Prove every executable Bundle reuse relation from actual Unreal reads."""

    bundle_version = bundle.get("version")
    bundle_relations = {
        relation.get("id"): relation
        for relation in bundle.get("reuseRelations", [])
        if isinstance(relation, dict) and isinstance(relation.get("id"), str)
    }
    actual_relations: dict[str, dict[str, Any]] = {}
    runtime_field_owners: dict[str, str] = {}
    for index, actual in enumerate(readback.get("reuseRelations", [])):
        if not isinstance(actual, dict):
            continue
        path = f"$.reuseRelations[{index}]"
        relation_id = actual.get("bundleRelationId")
        if relation_id in actual_relations:
            errors.append(issue("reuse.duplicate", f"{path}.bundleRelationId", "Each Bundle reuse relation may be read back only once."))
            continue
        expected = bundle_relations.get(relation_id)
        if not isinstance(expected, dict):
            errors.append(issue("reuse.unknown", f"{path}.bundleRelationId", "Readback names an unknown Bundle reuse relation."))
            continue
        actual_relations[relation_id] = actual
        source_asset = indexes["assets"].get(actual.get("sourceAssetId"))
        target_asset = indexes["assets"].get(actual.get("targetAssetId"))
        if not isinstance(source_asset, dict) or not isinstance(target_asset, dict):
            errors.append(issue("reuse.asset_missing", path, "Reuse relation source and target must both exist in actual asset readback."))
            source_asset = source_asset if isinstance(source_asset, dict) else {}
            target_asset = target_asset if isinstance(target_asset, dict) else {}
        for key in ("type", "sourceAssetId", "sourceAssetPath", "targetAssetId", "targetAssetPath"):
            if actual.get(key) != expected.get(key):
                errors.append(issue("reuse.identity", f"{path}.{key}", f"Actual reuse relation field {key} differs from Bundle intent."))
        for field_id in actual.get("runtimeFieldRefs", []):
            if field_id not in allowed_runtime_field_ids:
                errors.append(issue("reuse.runtime_field_unknown", f"{path}.runtimeFieldRefs", f"Runtime field {field_id} is not accepted and in scope."))
            if field_id not in expected.get("requirementRefs", []):
                errors.append(issue("reuse.runtime_field_intent", f"{path}.runtimeFieldRefs", f"Runtime field {field_id} is not assigned to this Bundle reuse relation."))
            if field_id in runtime_field_owners:
                errors.append(issue("reuse.runtime_field_duplicate", f"{path}.runtimeFieldRefs", f"Runtime field {field_id} is already owned by {runtime_field_owners[field_id]}."))
            runtime_field_owners[field_id] = str(relation_id)

        relation_type = actual.get("type")
        if relation_type == "shared-prototype-extension":
            if bundle_version == "0.3":
                _validate_dual_named_slot_readback(
                    actual.get("namedSlots"),
                    expected.get("namedSlots"),
                    source_asset_id=actual.get("sourceAssetId"),
                    indexes=indexes,
                    path=f"{path}.namedSlots",
                    errors=errors,
                )
            elif actual.get("namedSlot") != expected.get("namedSlot"):
                errors.append(issue("reuse.named_slot", f"{path}.namedSlot", "Actual standard/legacy NamedSlot evidence differs from Bundle intent."))
            if source_asset is not target_asset:
                errors.append(issue("reuse.extension_asset", path, "Shared extension source and target must resolve to the same actual asset."))
        elif relation_type == "class-settings-parent-class":
            if actual.get("parentClassPath") != expected.get("parentClassPath"):
                errors.append(issue("reuse.parent_class", f"{path}.parentClassPath", "Actual Parent Class differs from Bundle intent."))
            if target_asset.get("parentClassPath") != actual.get("parentClassPath"):
                errors.append(issue("reuse.parent_class_asset", f"{path}.parentClassPath", "Actual child asset Parent Class identity is inconsistent."))
            inherited_key = "inheritedSlots" if bundle_version == "0.3" else "inheritedSlot"
            if actual.get(inherited_key) != expected.get(inherited_key):
                errors.append(issue("reuse.inherited_slot", f"{path}.{inherited_key}", "Actual inherited Slot content differs from Bundle intent."))
            inherited_slots = (
                actual.get("inheritedSlots")
                if bundle_version == "0.3" and isinstance(actual.get("inheritedSlots"), list)
                else [actual.get("inheritedSlot")]
            )
            declared_panel_names: set[str] = set()
            for inherited_index, inherited_slot in enumerate(inherited_slots):
                if not isinstance(inherited_slot, dict) or inherited_slot.get("contentMode") != "panel":
                    continue
                panel = inherited_slot.get("panel") if isinstance(inherited_slot.get("panel"), dict) else {}
                if isinstance(panel.get("widgetName"), str):
                    declared_panel_names.add(panel["widgetName"])
                panel_widget = indexes["widgets"].get((actual.get("targetAssetId"), panel.get("widgetName")))
                panel_path = f"{path}.{inherited_key}{f'[{inherited_index}]' if bundle_version == '0.3' else ''}.panel"
                if not isinstance(panel_widget, dict):
                    errors.append(issue("reuse.inherited_panel_missing", f"{panel_path}.widgetName", "Inherited Slot Panel is absent from the actual child WidgetTree."))
                elif panel_widget.get("classPath") != panel.get("classPath"):
                    errors.append(issue("reuse.inherited_panel_class", f"{panel_path}.classPath", "Inherited Slot Panel class differs from the actual child WidgetTree."))
            child_widgets = target_asset.get("widgets") if isinstance(target_asset.get("widgets"), list) else []
            child_widgets_by_name = {
                widget.get("widgetName"): widget
                for widget in child_widgets
                if isinstance(widget, dict) and isinstance(widget.get("widgetName"), str)
            }
            if not declared_panel_names and child_widgets_by_name:
                errors.append(issue("reuse.inherited_unexpected_widgets", f"{path}.{inherited_key}", "A child with all inherited Slots empty must not own WidgetTree nodes."))
            elif declared_panel_names:
                root_names = {
                    name
                    for name, widget in child_widgets_by_name.items()
                    if widget.get("parentWidgetName") is None
                }
                if root_names != declared_panel_names:
                    errors.append(issue("reuse.inherited_panel_roots", f"{path}.{inherited_key}", "Child-owned WidgetTree roots must exactly equal the declared inherited Slot Panels."))
                for widget_name, widget in child_widgets_by_name.items():
                    current_name = widget_name
                    seen: set[str] = set()
                    while current_name in child_widgets_by_name and current_name not in seen:
                        if current_name in declared_panel_names:
                            break
                        seen.add(current_name)
                        parent_name = child_widgets_by_name[current_name].get("parentWidgetName")
                        if not isinstance(parent_name, str):
                            current_name = ""
                            break
                        current_name = parent_name
                    if current_name not in declared_panel_names:
                        errors.append(issue("reuse.inherited_widget_scope", f"{path}.{inherited_key}", f"Child Widget {widget_name!r} is not inside a declared inherited Slot Panel."))
            if source_asset.get("generatedClassPath") != actual.get("parentClassPath"):
                errors.append(issue("reuse.parent_generated_class", f"{path}.parentClassPath", "Parent Class does not equal the actual shared prototype GeneratedClass."))
        elif relation_type == "widget-tree-instance":
            host = actual.get("host") if isinstance(actual.get("host"), dict) else {}
            expected_host = expected.get("host") if isinstance(expected.get("host"), dict) else {}
            if host.get("widgetName") != expected_host.get("widgetName") or host.get("treePath") != expected_host.get("treePath"):
                errors.append(issue("reuse.host_identity", f"{path}.host", "Actual host Widget identity differs from Bundle intent."))
            if actual.get("sharedPrototypeClassPath") != expected.get("sharedPrototypeClassPath"):
                errors.append(issue("reuse.shared_class", f"{path}.sharedPrototypeClassPath", "Actual shared prototype class differs from Bundle intent."))
            if actual.get("nestedWidgetClassPath") != expected.get("nestedWidgetClassPath"):
                errors.append(issue("reuse.nested_class", f"{path}.nestedWidgetClassPath", "Actual nested Widget class differs from Bundle intent."))
            if bundle_version == "0.3" and actual.get("parameterOverrides") != expected.get("parameterOverrides"):
                errors.append(issue("reuse.parameter_overrides", f"{path}.parameterOverrides", "Actual nested Widget parameter overrides differ from Bundle intent."))
            if host.get("classPath") != actual.get("nestedWidgetClassPath"):
                errors.append(issue("reuse.host_class", f"{path}.host.classPath", "Host Widget class does not equal nestedWidgetClassPath."))
            host_widget = indexes["widgets"].get((actual.get("targetAssetId"), host.get("widgetName")))
            if not isinstance(host_widget, dict):
                errors.append(issue("reuse.host_widget_missing", f"{path}.host.widgetName", "Nested host Widget is absent from the actual target WidgetTree."))
            elif host_widget.get("classPath") != host.get("classPath") or host_widget.get("parentWidgetName") != host.get("parentWidgetName"):
                errors.append(issue("reuse.host_widget_identity", f"{path}.host", "Host relation evidence differs from the target asset Widget record."))
            placement = actual.get("placement") if isinstance(actual.get("placement"), dict) else {}
            expected_placement = expected.get("placementContract") if isinstance(expected.get("placementContract"), dict) else {}
            rect_delta = _numeric_sequence_delta(placement.get("hostNormalizedRect"), expected_placement.get("hostNormalizedRect"))
            if rect_delta is None or rect_delta > PLACEMENT_RECT_TOLERANCE:
                errors.append(issue("reuse.placement", f"{path}.placement.hostNormalizedRect", "Actual hostNormalizedRect differs from Bundle intent beyond tolerance."))
            for key in ("hostSize", "zOrder"):
                if placement.get(key) != expected_placement.get(key):
                    errors.append(issue("reuse.placement", f"{path}.placement.{key}", f"Actual placement {key} differs from Bundle intent."))
            actual_slot = placement.get("slot") if isinstance(placement.get("slot"), dict) else {}
            expected_slot = expected_placement.get("slot") if isinstance(expected_placement.get("slot"), dict) else {}
            slot_keys = ["containerType", "horizontalAlignment", "verticalAlignment", "padding"]
            if bundle_version == "0.3":
                slot_keys.append("size")
            for key in slot_keys:
                if actual_slot.get(key) != expected_slot.get(key):
                    errors.append(issue("reuse.placement_slot", f"{path}.placement.slot.{key}", f"Actual host Slot {key} differs from Bundle intent."))
            expected_slot_class = (
                V03_SLOT_CLASS_PATHS.get(expected_slot.get("containerType"))
                if bundle_version == "0.3"
                else {"CanvasPanel": "/Script/UMG.CanvasPanelSlot"}.get(expected_slot.get("containerType"))
            )
            if expected_slot_class is not None and actual_slot.get("classPath") != expected_slot_class:
                errors.append(issue("reuse.placement_slot_class", f"{path}.placement.slot.classPath", f"Actual host Slot class must be {expected_slot_class}."))
            if bundle_version == "0.3":
                for key in ("parentWidgetName", "parentTreePath"):
                    if key in expected_slot and actual_slot.get(key) != expected_slot.get(key):
                        errors.append(
                            issue(
                                "reuse.placement_slot_parent",
                                f"{path}.placement.slot.{key}",
                                f"Actual host Slot {key} differs from the Bundle parent identity.",
                            )
                        )
                actual_parent_name = actual_slot.get("parentWidgetName")
                if isinstance(actual_parent_name, str) and actual_parent_name != host.get("parentWidgetName"):
                    errors.append(
                        issue(
                            "reuse.placement_slot_parent",
                            f"{path}.placement.slot.parentWidgetName",
                            "Actual Slot parentWidgetName must equal the nested host Widget's actual parentWidgetName.",
                        )
                    )
                host_parent_widget = indexes["widgets"].get(
                    (actual.get("targetAssetId"), host.get("parentWidgetName"))
                )
                expected_parent_class_path = V03_SLOT_PARENT_CLASS_PATHS.get(actual_slot.get("containerType"))
                if not isinstance(host_parent_widget, dict):
                    errors.append(
                        issue(
                            "reuse.placement_slot_parent_missing",
                            f"{path}.placement.slot.containerType",
                            "The nested host Widget's parent is absent from the actual target WidgetTree, so its Slot container cannot be proven.",
                        )
                    )
                elif (
                    expected_parent_class_path is not None
                    and host_parent_widget.get("classPath") != expected_parent_class_path
                ):
                    errors.append(
                        issue(
                            "reuse.placement_slot_parent_class",
                            f"{path}.placement.slot.containerType",
                            f"Actual Slot container {actual_slot.get('containerType')!r} requires parent class {expected_parent_class_path}.",
                        )
                    )
                actual_parent_tree_path = actual_slot.get("parentTreePath")
                expected_actual_parent_tree_path = _widget_tree_path(
                    actual.get("targetAssetPath"),
                    host.get("parentWidgetName"),
                )
                if (
                    isinstance(actual_parent_tree_path, str)
                    and actual_parent_tree_path != expected_actual_parent_tree_path
                ):
                    errors.append(
                        issue(
                            "reuse.placement_slot_parent",
                            f"{path}.placement.slot.parentTreePath",
                            "Actual Slot parentTreePath must resolve to the nested host Widget's actual parent in the target WidgetTree.",
                        )
                    )
            if source_asset.get("generatedClassPath") != actual.get("nestedWidgetClassPath"):
                errors.append(issue("reuse.nested_generated_class", f"{path}.nestedWidgetClassPath", "Nested class does not equal the actual source child GeneratedClass."))

    if set(actual_relations) != set(bundle_relations):
        missing = sorted(set(bundle_relations) - set(actual_relations))
        extra = sorted(set(actual_relations) - set(bundle_relations))
        errors.append(issue("reuse.coverage", "$.reuseRelations", f"Readback must cover every Bundle reuse relation exactly once; missing={missing}, extra={extra}."))
    return actual_relations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("readback", type=Path)
    parser.add_argument("--requirement", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--schema", type=Path, default=READBACK_SCHEMA)
    args = parser.parse_args()
    try:
        output = validate_unreal_widget_readback(
            load_json(args.readback),
            load_json(args.schema),
            readback_path=args.readback.resolve(),
            requirement=load_json(args.requirement),
            requirement_path=args.requirement.resolve(),
            bundle=load_json(args.bundle),
            bundle_path=args.bundle.resolve(),
        )
    except (OSError, json.JSONDecodeError, ValueError) as error:
        output = result([issue("io.read", "$", str(error))])
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if output["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
