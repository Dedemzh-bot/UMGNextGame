#!/usr/bin/env python3
"""Prepare a deterministic NextGame UIProgramHandoff after post-build user acceptance."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from _document_contract_common import (
    BUILD_ACCEPTANCE_SCHEMA,
    HANDOFF_SCHEMA,
    accepted_claim_ids,
    is_accepted_in_scope,
    load_json,
    load_layouts,
    mapping_widget,
    readback_indexes,
    result,
    runtime_collections,
    runtime_field_node_mappings,
    runtime_fields,
    sha256_file,
    validate_schema_instance,
    write_json,
)
from validate_build_acceptance import validate_acceptance_handoff_binding, validate_build_acceptance


DATE_PATTERN = re.compile(r"^[0-9]{8}$")

PROGRAM_VARIABLE_PURPOSE_BY_VALUE_KIND = {
    "text": "程序控制文本内容",
    "image": "程序控制图像内容",
    "visibility": "程序控制可见状态",
    "progress": "程序控制进度显示",
    "state": "程序控制界面状态",
    "collection": "程序控制集合内容",
    "other": "程序控制动态内容",
}

STATE_CONTROL_DESCRIPTION_BY_KIND = {
    "user-interaction": "根据用户交互选择目标状态",
    "data-condition": "根据数据条件选择目标状态",
    "program-state": "根据程序状态选择目标状态",
    "external-state": "根据外部状态选择目标状态",
}


def _accepted_refs(entity: dict[str, Any], accepted: set[str]) -> list[str]:
    return sorted({claim_id for claim_id in entity.get("claimIds", []) if claim_id in accepted})


def _mapping_for_refs(mappings: list[dict[str, Any]], refs: set[Any]) -> dict[str, Any] | None:
    candidates = [mapping for mapping in mappings if refs & set(mapping.get("requirementRefs", []))]
    return candidates[0] if len(candidates) == 1 else None


def _resolved_state_widget(
    *,
    element_id: Any,
    state_id: Any,
    asset_id: str,
    elements: dict[str, dict[str, Any]],
    accepted: set[str],
    mappings: list[dict[str, Any]],
    layouts: dict[str, dict[str, Any]],
    indexes: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve one accepted runtime-controlled state element through all variable gates."""

    if not isinstance(element_id, str) or not isinstance(state_id, str):
        raise ValueError(f"Cannot project state {state_id!r} element {element_id!r}: identifiers must be strings.")
    element = elements.get(element_id)
    if (
        not isinstance(element, dict)
        or element.get("runtimeControlled") is not True
        or not is_accepted_in_scope(element, accepted)
    ):
        raise ValueError(
            f"Cannot project state {state_id!r} element {element_id!r}: "
            "element must be accepted, in scope, and runtimeControlled."
        )
    candidates = [
        mapping
        for mapping in mappings
        if mapping.get("assetId") == asset_id
        and element_id in mapping.get("requirementRefs", [])
        and state_id in mapping.get("stateRefs", [])
    ]
    if len(candidates) != 1:
        raise ValueError(
            f"Cannot project state {state_id!r} element {element_id!r}: "
            f"expected exactly one Bundle mapping for the asset and state, found {len(candidates)}."
        )
    mapping = candidates[0]
    _, widget = mapping_widget(mapping, indexes)
    layout_node = layouts.get(asset_id, {}).get("nodes", {}).get(mapping.get("layoutNodeId"), {})
    if (
        not isinstance(widget, dict)
        or widget.get("isVariable") is not True
        or not isinstance(layout_node, dict)
        or layout_node.get("isVariable") is not True
    ):
        raise ValueError(
            f"Cannot project state {state_id!r} element {element_id!r}: "
            "mapped UILayoutSpec node and actual Unreal Widget must both be variables."
        )
    if not isinstance(widget.get("visibility"), str):
        raise ValueError(
            f"Cannot project state {state_id!r} element {element_id!r}: "
            "actual Unreal Widget must report saved Visibility evidence."
        )
    return mapping, widget


def _visibility_binding(
    asset_id: str,
    mapping: dict[str, Any],
    widget: dict[str, Any],
    visibility: str,
) -> dict[str, Any]:
    return {
        "assetId": asset_id,
        "widgetName": widget["widgetName"],
        "visibility": visibility,
        "nodeMappingId": mapping["id"],
        "layoutNodeId": mapping["layoutNodeId"],
    }


def _sorted_visibility_bindings(bindings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        bindings,
        key=lambda item: (
            item["assetId"],
            item["widgetName"],
            item["nodeMappingId"],
            item["layoutNodeId"],
            item["visibility"],
        ),
    )


def build_program_handoff(
    requirement: dict[str, Any],
    bundle: dict[str, Any],
    readback: dict[str, Any],
    build_acceptance: dict[str, Any],
    *,
    requirement_path: Path,
    bundle_path: Path,
    readback_path: Path,
    build_acceptance_path: Path,
    output_date: str,
    generated_at: str,
) -> dict[str, Any]:
    accepted = accepted_claim_ids(requirement)
    review = requirement["reviewGate"]
    system_folder = requirement["target"]["systemFolder"]
    bundle_assets = [asset for asset in bundle.get("assets", []) if isinstance(asset, dict)]
    mappings = [mapping for mapping in bundle.get("nodeMappings", []) if isinstance(mapping, dict)]
    read_errors: list[dict[str, str]] = []
    indexes = readback_indexes(readback, read_errors)
    if read_errors:
        raise ValueError("Cannot build handoff from duplicate or unresolved readback identities.")
    layouts = load_layouts(bundle, bundle_path, read_errors)
    if read_errors:
        raise ValueError("Cannot build handoff from missing or stale UILayoutSpec files.")

    ui_model = requirement.get("uiModel") if isinstance(requirement.get("uiModel"), dict) else {}
    elements = {
        element.get("id"): element
        for element in ui_model.get("elements", [])
        if isinstance(element, dict) and isinstance(element.get("id"), str)
    }
    runtime_intents = runtime_fields(requirement, accepted)
    collections = runtime_collections(requirement, accepted)
    requirement_asset_plans = {
        asset.get("id"): asset for asset in requirement.get("assetPlan", []) if isinstance(asset, dict)
    }

    handoff_assets: list[dict[str, Any]] = []
    for bundle_asset in bundle_assets:
        asset_id = bundle_asset["id"]
        program_variables: list[dict[str, Any]] = []
        for runtime_field in runtime_intents:
            element = elements.get(runtime_field.get("elementId"))
            if (
                not isinstance(element, dict)
                or element.get("runtimeControlled") is not True
                or not is_accepted_in_scope(element, accepted)
            ):
                continue
            field_mappings = runtime_field_node_mappings(
                runtime_field, mappings, requirement, accepted
            )
            mirrored = len(field_mappings) > 1
            for mapping in field_mappings:
                if mapping.get("assetId") != asset_id:
                    continue
                _, widget = mapping_widget(mapping, indexes)
                layout_node = layouts.get(asset_id, {}).get("nodes", {}).get(mapping.get("layoutNodeId"), {})
                if not isinstance(widget, dict) or widget.get("isVariable") is not True or layout_node.get("isVariable") is not True:
                    continue
                trace_element_id = runtime_field["elementId"]
                if mirrored:
                    mapped_element_ids = [
                        ref
                        for ref in mapping.get("requirementRefs", [])
                        if ref in elements
                    ]
                    if len(mapped_element_ids) != 1:
                        raise ValueError(
                            f"Mirrored runtime field {runtime_field['id']} has an ambiguous mapped element."
                        )
                    trace_element_id = mapped_element_ids[0]
                program_variables.append(
                    {
                        "id": f"variable:{asset_id}:{widget['widgetName']}",
                        "widgetName": widget["widgetName"],
                        "widgetClass": widget["classPath"],
                        "purpose": PROGRAM_VARIABLE_PURPOSE_BY_VALUE_KIND[runtime_field["valueKind"]],
                        "trace": {
                            "runtimeFieldId": runtime_field["id"],
                            "elementId": trace_element_id,
                            "nodeMappingId": mapping["id"],
                            "layoutNodeId": mapping["layoutNodeId"],
                            "acceptedClaimIds": _accepted_refs(runtime_field, accepted),
                        },
                    }
                )

        collection_records: list[dict[str, Any]] = []
        for collection in collections:
            mapping = _mapping_for_refs(mappings, {collection.get("id"), collection.get("containerElementId")})
            if mapping is None or mapping.get("assetId") != asset_id:
                continue
            _, widget = mapping_widget(mapping, indexes)
            if not isinstance(widget, dict) or not isinstance(widget.get("entryWidgetClass"), str):
                continue
            collection_records.append(
                {
                    "id": collection["id"],
                    "widgetName": widget["widgetName"],
                    "widgetClass": widget["classPath"],
                    "entryWidgetClass": widget["entryWidgetClass"],
                    "purpose": "由程序填充",
                    "overflowStrategy": collection["overflowStrategy"],
                    "trace": {
                        "nodeMappingId": mapping["id"],
                        "layoutNodeId": mapping["layoutNodeId"],
                        "acceptedClaimIds": _accepted_refs(collection, accepted),
                    },
                }
            )

        state_records: list[dict[str, Any]] = []
        asset_plan = requirement_asset_plans.get(bundle_asset.get("assetPlanId"), {})
        covered_models = set(asset_plan.get("coversStateModelIds", [])) if isinstance(asset_plan, dict) else set()
        for model in requirement.get("stateModels", []):
            if (
                not isinstance(model, dict)
                or model.get("id") not in covered_models
                or not is_accepted_in_scope(model, accepted, require_scope=False)
            ):
                continue
            controls: list[dict[str, Any]] = []
            for control in model.get("controlInputs", []):
                if not isinstance(control, dict) or control.get("kind") == "unspecified":
                    continue
                control_claims = _accepted_refs(control, accepted)
                if not control_claims:
                    continue
                controls.append(
                    {
                        "id": control["id"],
                        "axisId": control["axisId"],
                        "kind": control["kind"],
                        "description": STATE_CONTROL_DESCRIPTION_BY_KIND[control["kind"]],
                        "targetStateIds": list(control["targetStateIds"]),
                        "acceptedClaimIds": control_claims,
                    }
                )
            axes: list[dict[str, Any]] = []
            implementation = model.get("implementation") if isinstance(model.get("implementation"), dict) else {}
            implementation_strategy = implementation.get("strategy")
            if implementation_strategy not in {"exclusive-panel-branches", "shared-tree-properties"}:
                raise ValueError(
                    f"Cannot project state model {model.get('id')!r}: implementation strategy is missing or unsupported."
                )
            branch_by_state = {
                branch.get("stateId"): branch
                for branch in implementation.get("branches", [])
                if isinstance(branch, dict) and isinstance(branch.get("stateId"), str)
            }
            override_by_state = {
                override.get("stateId"): override
                for override in implementation.get("stateOverrides", [])
                if isinstance(override, dict) and isinstance(override.get("stateId"), str)
            }
            for axis in model.get("axes", []):
                if not isinstance(axis, dict):
                    continue
                states: list[dict[str, Any]] = []
                for state in axis.get("states", []):
                    if not isinstance(state, dict) or not is_accepted_in_scope(state, accepted):
                        continue
                    state_id = state.get("id")
                    actual_saved_bindings: list[dict[str, Any]] = []
                    runtime_outcomes: list[dict[str, Any]] = []
                    if implementation_strategy == "exclusive-panel-branches":
                        branch = branch_by_state.get(state_id)
                        if not isinstance(branch, dict):
                            raise ValueError(f"Cannot project exclusive state {state_id!r}: branch definition is missing.")
                        mapping, widget = _resolved_state_widget(
                            element_id=branch.get("panelElementId"),
                            state_id=state_id,
                            asset_id=asset_id,
                            elements=elements,
                            accepted=accepted,
                            mappings=mappings,
                            layouts=layouts,
                            indexes=indexes,
                        )
                        actual_saved_bindings.append(
                            _visibility_binding(asset_id, mapping, widget, widget["visibility"])
                        )
                    elif implementation_strategy == "shared-tree-properties":
                        override = override_by_state.get(state_id)
                        if not isinstance(override, dict):
                            raise ValueError(f"Cannot project shared-tree state {state_id!r}: override definition is missing.")
                        changes = override.get("changes", [])
                        for change in changes:
                            if (
                                not isinstance(change, dict)
                                or not isinstance(change.get("property"), str)
                                or change["property"].casefold() != "visibility"
                            ):
                                continue
                            if not isinstance(change.get("value"), str):
                                raise ValueError(
                                    f"Cannot project state {state_id!r} element {change.get('elementId')!r}: "
                                    "accepted Visibility outcome must be a string."
                                )
                            mapping, widget = _resolved_state_widget(
                                element_id=change.get("elementId"),
                                state_id=state_id,
                                asset_id=asset_id,
                                elements=elements,
                                accepted=accepted,
                                mappings=mappings,
                                layouts=layouts,
                                indexes=indexes,
                            )
                            actual_saved_bindings.append(
                                _visibility_binding(asset_id, mapping, widget, widget["visibility"])
                            )
                            runtime_outcomes.append(
                                _visibility_binding(asset_id, mapping, widget, change["value"])
                            )
                    states.append(
                        {
                            "id": state["id"],
                            "name": state["name"],
                            "isDefault": state["isDefault"],
                            "actualSavedVisibilityBindings": _sorted_visibility_bindings(actual_saved_bindings),
                            "runtimeVisibilityOutcomes": _sorted_visibility_bindings(runtime_outcomes),
                        }
                    )
                axes.append({"id": axis["id"], "states": states})
            state_records.append(
                {
                    "id": model["id"],
                    "implementationStrategy": implementation_strategy,
                    "controlInputs": controls,
                    "axes": axes,
                    "acceptedClaimIds": _accepted_refs(model, accepted),
                }
            )

        handoff_assets.append(
            {
                "assetId": asset_id,
                "assetPath": bundle_asset["assetPath"],
                "programVariables": sorted(program_variables, key=lambda item: item["id"]),
                "collections": sorted(collection_records, key=lambda item: item["id"]),
                "states": sorted(state_records, key=lambda item: item["id"]),
            }
        )

    gaps: list[dict[str, Any]] = []
    for model in requirement.get("stateModels", []):
        if not isinstance(model, dict) or not is_accepted_in_scope(model, accepted, require_scope=False):
            continue
        controls = model.get("controlInputs")
        if not isinstance(controls, list) or not controls:
            gaps.append(
                {
                    "code": "state-control-input-missing",
                    "stateModelId": model["id"],
                    "description": "Requirement does not specify a control input for this accepted state model.",
                }
            )
            continue
        for control in controls:
            if isinstance(control, dict) and control.get("kind") == "unspecified":
                gaps.append(
                    {
                        "code": "state-control-input-unspecified",
                        "stateModelId": model["id"],
                        "controlInputId": control["id"],
                        "description": "Requirement records this state control input as unspecified.",
                    }
                )

    deviations = []
    verification = bundle.get("verification") if isinstance(bundle.get("verification"), dict) else {}
    for deviation in verification.get("deviations", []):
        if not isinstance(deviation, dict):
            continue
        deviations.append(
            {
                "id": deviation["id"],
                "status": deviation["status"],
                "impact": deviation["impact"],
                "affectedAssetIds": list(deviation["affectedAssetIds"]),
                "affectedRequirementRefs": list(deviation["affectedRequirementRefs"]),
            }
        )

    return {
        "version": "0.3",
        "handoffId": f"handoff:{bundle['bundleId']}",
        "generatedAt": generated_at,
        "target": {
            "systemFolder": system_folder,
            "mode": "production",
            "assetPaths": [asset["assetPath"] for asset in bundle_assets],
        },
        "sources": {
            "requirement": {
                "requestId": requirement["requestId"],
                "revision": requirement["revision"],
                "approvedContentSha256": review["approvedContentSha256"],
                "sha256": sha256_file(requirement_path),
            },
            "bundle": {"bundleId": bundle["bundleId"], "sha256": sha256_file(bundle_path)},
            "unrealReadback": {"readbackId": readback["readbackId"], "sha256": sha256_file(readback_path)},
            "buildAcceptance": {
                "acceptanceId": build_acceptance["acceptanceId"],
                "sha256": sha256_file(build_acceptance_path),
            },
        },
        "output": {
            "rootEnvironmentVariable": "NEXTGAME_UI_PROGRAM_DOCS_ROOT",
            "fileName": f"{output_date}_UGame{system_folder}界面说明.docx",
        },
        "contentPolicy": {
            "staticDesignerConfiguration": "excluded",
            "generatedContentLifecycleDetails": "forbidden",
            "runtimeParameterContractDetails": "forbidden",
            "eventCallbackContractDetails": "forbidden",
            "collectionItemSchemaDetails": "forbidden",
        },
        "assets": handoff_assets,
        "deviations": deviations,
        "gaps": gaps,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requirement", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--readback", type=Path, required=True)
    parser.add_argument("--build-acceptance", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--date", help="Output date in YYYYMMDD; defaults to local current date.")
    args = parser.parse_args()
    output_date = args.date or datetime.now().astimezone().strftime("%Y%m%d")
    try:
        if DATE_PATTERN.fullmatch(output_date) is None:
            raise ValueError("--date must use YYYYMMDD.")
        requirement = load_json(args.requirement)
        bundle = load_json(args.bundle)
        readback = load_json(args.readback)
        build_acceptance = load_json(args.build_acceptance)
        acceptance_report = validate_build_acceptance(
            build_acceptance,
            load_json(BUILD_ACCEPTANCE_SCHEMA),
            acceptance_path=args.build_acceptance.resolve(),
            requirement=requirement,
            requirement_path=args.requirement.resolve(),
            bundle=bundle,
            bundle_path=args.bundle.resolve(),
            readback=readback,
            readback_path=args.readback.resolve(),
        )
        if not acceptance_report["valid"]:
            print(json.dumps(acceptance_report, ensure_ascii=False, indent=2))
            return 1
        handoff = build_program_handoff(
            requirement,
            bundle,
            readback,
            build_acceptance,
            requirement_path=args.requirement.resolve(),
            bundle_path=args.bundle.resolve(),
            readback_path=args.readback.resolve(),
            build_acceptance_path=args.build_acceptance.resolve(),
            output_date=output_date,
            generated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        )
        schema_errors = validate_schema_instance(handoff, load_json(HANDOFF_SCHEMA))
        schema_errors.extend(
            validate_acceptance_handoff_binding(
                build_acceptance,
                args.build_acceptance.resolve(),
                handoff,
            )["errors"]
        )
        if schema_errors:
            report = result(schema_errors)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 1
        write_json(args.output, handoff)
    except (OSError, json.JSONDecodeError, ValueError, KeyError) as error:
        print(json.dumps(result([{"code": "prepare.failed", "path": "$", "message": str(error)}]), ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"valid": True, "output": str(args.output), "fileName": handoff["output"]["fileName"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
