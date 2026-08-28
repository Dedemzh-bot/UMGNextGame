#!/usr/bin/env python3
"""Shared contract logic for the NextGame UMG documentation stage."""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parent.parent
ASSETS_ROOT = SKILL_ROOT / "assets"
ANALYSIS_SKILL_ROOT = SKILL_ROOT.parent / "analyze-nextgame-ui-requirements"
ANALYSIS_SCRIPTS = ANALYSIS_SKILL_ROOT / "scripts"
ANALYSIS_ASSETS = ANALYSIS_SKILL_ROOT / "assets"
if str(ANALYSIS_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(ANALYSIS_SCRIPTS))

from _contract_common import (  # noqa: E402
    canonical_sha256,
    compute_approved_content_sha256,
    find_forbidden_keys,
    issue,
    load_json,
    result,
    sha256_file,
    validate_schema_instance,
)
from validate_build_bundle import validate_build_bundle  # noqa: E402
from validate_requirement_spec import build_requirement_index, validate_requirement_spec  # noqa: E402


REQUIREMENT_SCHEMA = ANALYSIS_ASSETS / "ui-requirement-spec.schema.json"
BUNDLE_SCHEMA = ANALYSIS_ASSETS / "ui-build-bundle.schema.json"
READBACK_SCHEMA = ASSETS_ROOT / "unreal-widget-readback.schema.json"
BUILD_ACCEPTANCE_SCHEMA = ASSETS_ROOT / "ui-build-acceptance.schema.json"
HANDOFF_SCHEMA = ASSETS_ROOT / "ui-program-handoff.schema.json"
DOCUMENT_VERIFICATION_SCHEMA = ASSETS_ROOT / "document-verification.schema.json"
PROGRAM_DOCUMENT_CONTENT_SCHEMA = ASSETS_ROOT / "program-document-content.schema.json"
SYSTEM_PATH_PATTERN = re.compile(r"^/Game/UI/UMG/([^/]+)/")
PROJECT_COMMON_WIDGET_PATH_PATTERN = re.compile(
    r"^/Game/UI/UMG/Widgets/uw_common_[A-Za-z0-9_]+$"
)
DOCX_NAME_PATTERN = re.compile(r"^(\d{8})_UGame([A-Za-z0-9_]+)界面说明\.docx$")

FORBIDDEN_HANDOFF_KEYS = {
    "generatedContentDataSource",
    "generatedContentOwner",
    "generatedContentRefreshStrategy",
    "dataSource",
    "contentOwner",
    "refreshStrategy",
    "runtimeParameterType",
    "runtimeParameterDefaultValue",
    "runtimeParameterUpdateTiming",
    "parameterType",
    "defaultValue",
    "updateTiming",
    "eventName",
    "callbackName",
    "eventPayload",
    "callbackPayload",
    "payload",
    "collectionItemSchema",
    "listItemSchema",
    "listItemStructure",
    "itemSchema",
    "itemFields",
}


def prefix_issues(prefix: str, problems: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        issue(f"{prefix}.{problem.get('code', 'invalid')}", problem.get("path", "$"), problem.get("message", "Invalid contract."))
        for problem in problems
    ]


def parse_iso8601(value: Any, path: str, errors: list[dict[str, str]]) -> None:
    if not isinstance(value, str):
        return
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append(issue("time.iso8601", path, "Expected an ISO-8601 timestamp."))


def parse_aware_iso8601(value: Any, path: str, errors: list[dict[str, str]]) -> datetime | None:
    """Parse a required ISO-8601 timestamp whose UTC offset is explicit."""

    if not isinstance(value, str) or not value.strip():
        errors.append(issue("time.required", path, "Expected a required ISO-8601 timestamp with an explicit timezone."))
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append(issue("time.iso8601", path, "Expected an ISO-8601 timestamp."))
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        errors.append(issue("time.timezone", path, "Timestamp must include an explicit valid timezone offset."))
        return None
    return parsed


def resolve_request_path(bundle_path: Path, raw_path: str) -> Path:
    """Resolve a linked artifact without allowing it to escape the Bundle request directory."""

    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("Linked path must be a non-empty relative path.")
    posix_path = PurePosixPath(raw_path)
    windows_path = PureWindowsPath(raw_path)
    if posix_path.is_absolute() or windows_path.is_absolute():
        raise ValueError("Linked path must be relative to the Bundle request directory.")
    if ".." in posix_path.parts or ".." in windows_path.parts:
        raise ValueError("Linked path must not contain '..' traversal segments.")

    request_root = bundle_path.resolve().parent
    candidate = (request_root / Path(raw_path)).resolve()
    try:
        candidate.relative_to(request_root)
    except ValueError as error:
        raise ValueError("Linked path resolves outside the Bundle request directory.") from error
    return candidate


def validate_linked_layout_scope(
    bundle: dict[str, Any], bundle_path: Path, errors: list[dict[str, str]]
) -> bool:
    """Reject unsafe UILayoutSpec links before the generic Bundle validator can open them."""

    scoped = True
    for index, asset in enumerate(bundle.get("assets", [])):
        if not isinstance(asset, dict) or not isinstance(asset.get("layoutSpecPath"), str):
            continue
        try:
            resolve_request_path(bundle_path, asset["layoutSpecPath"])
        except ValueError as error:
            scoped = False
            errors.append(issue("layout.path_scope", f"$.assets[{index}].layoutSpecPath", str(error)))
    return scoped


def accepted_claim_ids(requirement: dict[str, Any]) -> set[str]:
    review = requirement.get("reviewGate") if isinstance(requirement.get("reviewGate"), dict) else {}
    reviewed = set(review.get("acceptedClaimIds", []))
    return {
        claim.get("id")
        for claim in requirement.get("claims", [])
        if isinstance(claim, dict) and claim.get("status") == "accepted" and claim.get("id") in reviewed
    }


def is_accepted_in_scope(entity: dict[str, Any], accepted: set[str], *, require_scope: bool = True) -> bool:
    if require_scope and entity.get("inBuildScope") is not True:
        return False
    claim_ids = entity.get("claimIds", [])
    return isinstance(claim_ids, list) and any(claim_id in accepted for claim_id in claim_ids)


def system_folder_for_paths(paths: list[Any], errors: list[dict[str, str]], path: str) -> str | None:
    folders: set[str] = set()
    for index, asset_path in enumerate(paths):
        if not isinstance(asset_path, str):
            errors.append(issue("target.asset_path", f"{path}[{index}]", "Asset path must be a string."))
            continue
        match = SYSTEM_PATH_PATTERN.fullmatch(asset_path.rsplit("/", 1)[0] + "/") if "/" in asset_path else None
        if match is None:
            match = SYSTEM_PATH_PATTERN.match(asset_path)
        if match is None:
            errors.append(issue("target.asset_path", f"{path}[{index}]", "Expected /Game/UI/UMG/<SystemFolder>/..."))
            continue
        folders.add(match.group(1))
    if len(folders) != 1:
        errors.append(issue("target.system_folder", path, f"Expected exactly one SystemFolder, got {sorted(folders)}."))
        return None
    return next(iter(folders))


def verified_shared_prototype_paths(bundle: dict[str, Any]) -> set[str]:
    """Return the narrow reuse-Bundle cross-SystemFolder shared-prototype exception."""

    bundle_version = bundle.get("version")
    if bundle_version not in {"0.2", "0.3"}:
        return set()
    assets = {
        asset.get("id"): asset
        for asset in bundle.get("assets", [])
        if isinstance(asset, dict) and isinstance(asset.get("id"), str)
    }
    verified: set[str] = set()
    for relation in bundle.get("reuseRelations", []):
        if not isinstance(relation, dict) or relation.get("type") != "shared-prototype-extension":
            continue
        source_id = relation.get("sourceAssetId")
        target_id = relation.get("targetAssetId")
        source_path = relation.get("sourceAssetPath")
        activation = relation.get("activation") if isinstance(relation.get("activation"), dict) else {}
        registry = relation.get("registry") if isinstance(relation.get("registry"), dict) else {}
        extension_status_key = "extensionSlotStatus" if bundle_version == "0.2" else "extensionSlotsStatus"
        registry_verified = (
            registry.get("entryStatus") == "active"
            and registry.get(extension_status_key) == "verified"
        )
        activation_verified = registry_verified and (
            (
                activation.get("mode") == "post-extension-activation"
                and activation.get("status") == "verified"
            )
            or activation.get("mode") == "preverified"
        )
        asset = assets.get(source_id)
        if (
            source_id == target_id
            and isinstance(source_path, str)
            and source_path == relation.get("targetAssetPath")
            and PROJECT_COMMON_WIDGET_PATH_PATTERN.fullmatch(source_path) is not None
            and isinstance(asset, dict)
            and asset.get("representationKind") in {"reuse-only", "layout-spec"}
            and asset.get("assetPath") == source_path
            and activation_verified
        ):
            verified.add(source_path)
    return verified


def validate_system_folder_with_shared_prototypes(
    paths: list[Any],
    *,
    expected_system_folder: Any,
    shared_paths: set[str],
    errors: list[dict[str, str]],
    path: str,
) -> str | None:
    """Validate main-system paths while allowing only verified shared prototypes elsewhere."""

    if not isinstance(expected_system_folder, str) or not expected_system_folder:
        errors.append(issue("target.system_folder", path, "Expected a non-empty target SystemFolder."))
        return None
    main_count = 0
    for index, asset_path in enumerate(paths):
        if not isinstance(asset_path, str):
            errors.append(issue("target.asset_path", f"{path}[{index}]", "Asset path must be a string."))
            continue
        match = SYSTEM_PATH_PATTERN.match(asset_path)
        if match is None:
            errors.append(issue("target.asset_path", f"{path}[{index}]", "Expected /Game/UI/UMG/<SystemFolder>/..."))
            continue
        if match.group(1) == expected_system_folder:
            main_count += 1
        elif asset_path not in shared_paths:
            errors.append(
                issue(
                    "target.system_folder",
                    f"{path}[{index}]",
                    f"Cross-SystemFolder asset {asset_path!r} is not a verified reuse-Bundle shared prototype.",
                )
            )
    if main_count == 0:
        errors.append(issue("target.system_folder", path, f"No target asset belongs to SystemFolder {expected_system_folder!r}."))
        return None
    return expected_system_folder


def validate_requirement_and_bundle(
    requirement: Any,
    bundle: Any,
    *,
    requirement_path: Path,
    bundle_path: Path,
    check_linked_files: bool = True,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    errors: list[dict[str, str]] = []
    context: dict[str, Any] = {}
    requirement_schema = load_json(REQUIREMENT_SCHEMA)
    requirement_report = validate_requirement_spec(requirement, requirement_schema)
    errors.extend(prefix_issues("requirement", requirement_report["errors"]))
    if not isinstance(requirement, dict):
        return errors, context

    review = requirement.get("reviewGate") if isinstance(requirement.get("reviewGate"), dict) else {}
    if review.get("status") != "accepted":
        errors.append(issue("requirement.not_accepted", "$.reviewGate.status", "Documentation requires an accepted Requirement."))
    try:
        expected_approval = compute_approved_content_sha256(requirement)
    except (TypeError, ValueError) as error:
        errors.append(issue("requirement.approval_input", "$.reviewGate", str(error)))
        expected_approval = None
    if expected_approval is not None and review.get("approvedContentSha256") != expected_approval:
        errors.append(issue("requirement.not_current", "$.reviewGate.approvedContentSha256", "Requirement changed after acceptance."))

    target = requirement.get("target") if isinstance(requirement.get("target"), dict) else {}
    if target.get("mode") != "production" or target.get("productionAuthorized") is not True:
        errors.append(issue("target.production", "$.target", "Documentation only accepts production-authorized Requirements."))
    target_paths = target.get("targetAssetPaths") if isinstance(target.get("targetAssetPaths"), list) else []

    if not isinstance(bundle, dict):
        errors.append(issue("bundle.type", "$", "UIBuildBundle must be an object."))
        return errors, context
    shared_paths = verified_shared_prototype_paths(bundle)
    if bundle.get("version") in {"0.2", "0.3"}:
        system_folder = validate_system_folder_with_shared_prototypes(
            target_paths,
            expected_system_folder=target.get("systemFolder"),
            shared_paths=shared_paths,
            errors=errors,
            path="$.target.targetAssetPaths",
        ) if target_paths else None
    else:
        system_folder = system_folder_for_paths(target_paths, errors, "$.target.targetAssetPaths") if target_paths else None
        if system_folder is not None and target.get("systemFolder") != system_folder:
            errors.append(issue("target.folder_mismatch", "$.target.systemFolder", "systemFolder does not match target asset paths."))
    linked_layouts_scoped = validate_linked_layout_scope(bundle, bundle_path, errors) if check_linked_files else True
    bundle_report = validate_build_bundle(
        bundle,
        load_json(BUNDLE_SCHEMA),
        bundle_path=bundle_path,
        requirement_spec=requirement,
        requirement_path=requirement_path,
        requirement_schema=requirement_schema,
        check_linked_files=check_linked_files and linked_layouts_scoped,
    )
    errors.extend(prefix_issues("bundle", bundle_report["errors"]))

    execution = bundle.get("execution") if isinstance(bundle.get("execution"), dict) else {}
    verification = bundle.get("verification") if isinstance(bundle.get("verification"), dict) else {}
    if execution.get("status") != "completed":
        errors.append(issue("bundle.execution", "$.execution.status", "Bundle execution must be completed."))
    if verification.get("status") != "passed":
        errors.append(issue("bundle.verification", "$.verification.status", "Bundle verification must be passed."))
    for index, asset in enumerate(bundle.get("assets", [])):
        if not isinstance(asset, dict) or asset.get("status") != "verified":
            errors.append(issue("bundle.asset_status", f"$.assets[{index}].status", "Every Bundle asset must be verified."))
    for index, check in enumerate(verification.get("checks", [])):
        if not isinstance(check, dict) or check.get("status") != "passed":
            errors.append(issue("bundle.check_status", f"$.verification.checks[{index}].status", "Every Bundle check must be passed."))

    bundle_paths = [asset.get("assetPath") for asset in bundle.get("assets", []) if isinstance(asset, dict)]
    if bundle.get("version") in {"0.2", "0.3"}:
        bundle_folder = validate_system_folder_with_shared_prototypes(
            bundle_paths,
            expected_system_folder=target.get("systemFolder"),
            shared_paths=shared_paths,
            errors=errors,
            path="$.assets",
        ) if bundle_paths else None
    else:
        bundle_folder = system_folder_for_paths(bundle_paths, errors, "$.assets") if bundle_paths else None
        if system_folder is not None and bundle_folder is not None and bundle_folder != system_folder:
            errors.append(issue("bundle.system_folder", "$.assets", "Bundle assets belong to a different SystemFolder."))
    if set(bundle_paths) != set(target_paths):
        errors.append(issue("bundle.asset_set", "$.assets", "Bundle asset paths must exactly match Requirement targetAssetPaths."))

    context.update(
        {
            "systemFolder": system_folder,
            "acceptedClaimIds": accepted_claim_ids(requirement),
            "requirementIndex": build_requirement_index(requirement),
        }
    )
    return errors, context


def load_layouts(bundle: dict[str, Any], bundle_path: Path, errors: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    layouts: dict[str, dict[str, Any]] = {}
    for index, asset in enumerate(bundle.get("assets", [])):
        if not isinstance(asset, dict):
            continue
        asset_id = asset.get("id")
        raw_path = asset.get("layoutSpecPath")
        if not isinstance(asset_id, str) or not isinstance(raw_path, str):
            continue
        try:
            layout_path = resolve_request_path(bundle_path, raw_path)
        except ValueError as error:
            errors.append(issue("layout.path_scope", f"$.assets[{index}].layoutSpecPath", str(error)))
            continue
        try:
            layout = load_json(layout_path)
        except (OSError, json.JSONDecodeError, ValueError) as error:
            errors.append(issue("layout.read", f"$.assets[{index}].layoutSpecPath", str(error)))
            continue
        if asset.get("layoutSpecSha256") != sha256_file(layout_path):
            errors.append(issue("layout.sha256", f"$.assets[{index}].layoutSpecSha256", "UILayoutSpec hash mismatch."))
        nodes = layout.get("nodes") if isinstance(layout, dict) and isinstance(layout.get("nodes"), list) else []
        layouts[asset_id] = {
            "document": layout,
            "path": layout_path,
            "nodes": {node.get("id"): node for node in nodes if isinstance(node, dict) and isinstance(node.get("id"), str)},
        }
    return layouts


def readback_indexes(readback: dict[str, Any], errors: list[dict[str, str]]) -> dict[str, Any]:
    assets: dict[str, dict[str, Any]] = {}
    widgets: dict[tuple[str, str], dict[str, Any]] = {}
    mappings: dict[str, tuple[str, dict[str, Any]]] = {}
    for asset_index, asset in enumerate(readback.get("assets", [])):
        if not isinstance(asset, dict):
            continue
        asset_id = asset.get("assetId")
        if not isinstance(asset_id, str):
            continue
        if asset_id in assets:
            errors.append(issue("readback.asset_duplicate", f"$.assets[{asset_index}].assetId", "Duplicate readback asset."))
        assets[asset_id] = asset
        local_names: set[str] = set()
        for widget_index, widget in enumerate(asset.get("widgets", [])):
            if not isinstance(widget, dict) or not isinstance(widget.get("widgetName"), str):
                continue
            name = widget["widgetName"]
            if name in local_names:
                errors.append(issue("readback.widget_duplicate", f"$.assets[{asset_index}].widgets[{widget_index}].widgetName", "Duplicate Widget name in asset."))
            local_names.add(name)
            widgets[(asset_id, name)] = widget
        for mapping_index, mapping in enumerate(asset.get("nodeMappings", [])):
            if not isinstance(mapping, dict) or not isinstance(mapping.get("nodeMappingId"), str):
                continue
            mapping_id = mapping["nodeMappingId"]
            if mapping_id in mappings:
                errors.append(issue("readback.mapping_duplicate", f"$.assets[{asset_index}].nodeMappings[{mapping_index}].nodeMappingId", "Duplicate nodeMapping readback."))
            mappings[mapping_id] = (asset_id, mapping)
            if (asset_id, mapping.get("widgetName")) not in widgets:
                errors.append(issue("readback.mapping_widget", f"$.assets[{asset_index}].nodeMappings[{mapping_index}].widgetName", "Mapped Widget is absent from this asset readback."))
    return {"assets": assets, "widgets": widgets, "mappings": mappings}


LEGACY_WIDGET_TREE_TABLE_FORMAT = "word-native-three-column-table-v1"
LEGACY_WIDGET_TREE_TABLE_HEADERS = ["层级 / Widget", "Class", "Is Variable"]
WIDGET_TREE_TABLE_FORMAT = "word-native-four-column-asset-detail-table-v2"
WIDGET_TREE_TABLE_HEADERS = ["层级 / Widget", "Class", "Is Variable", "程序用途"]
WIDGET_TREE_INDENT_TWIPS = 180
WIDGET_TREE_EMPTY_STATE = "reuse-only-no-owned-widgets"


def widget_class_name(class_path: str) -> str:
    """Return the compact class name carried into the token-minimal document contract."""

    return class_path.rsplit(".", 1)[-1]


def project_widget_tree_tables(
    readback: dict[str, Any],
    asset_order: list[dict[str, Any]],
    *,
    content_version: str = "0.4",
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Project validated readback trees into stable, compact, document-facing rows."""

    errors: list[dict[str, str]] = []
    if content_version not in {"0.3", "0.4"}:
        errors.append(
            issue(
                "document.widget_tree_contract_version",
                "$.version",
                f"Unsupported WidgetTree content contract version {content_version!r}.",
            )
        )
    legacy_three_column = content_version == "0.3"
    readback_assets = {
        asset.get("assetId"): asset
        for asset in readback.get("assets", [])
        if isinstance(asset, dict) and isinstance(asset.get("assetId"), str)
    }
    projected_assets: list[dict[str, Any]] = []
    for asset_index, ordered_asset in enumerate(asset_order):
        asset_id = ordered_asset.get("assetId")
        asset_path = ordered_asset.get("assetPath")
        actual = readback_assets.get(asset_id)
        path = f"$.widgetTreeTables.assets[{asset_index}]"
        if not isinstance(actual, dict):
            errors.append(issue("document.widget_tree_asset_missing", path, f"Readback asset {asset_id!r} is missing."))
            continue
        parent_class_path = actual.get("parentClassPath")
        if not legacy_three_column and (not isinstance(parent_class_path, str) or not parent_class_path):
            errors.append(
                issue(
                    "document.widget_tree_parent_class",
                    f"{path}.parentClassPath",
                    "Version 0.4 document content requires the verified readback Parent Class path.",
                )
            )
        program_purposes: dict[str, str] = {}
        if not legacy_three_column:
            variables = ordered_asset.get("programVariables") if isinstance(ordered_asset.get("programVariables"), list) else []
            for variable_index, variable in enumerate(variables):
                variable_path = f"{path}.programVariables[{variable_index}]"
                if not isinstance(variable, dict):
                    errors.append(issue("document.widget_tree_program_variable", variable_path, "Program variable must be an object."))
                    continue
                widget_name = variable.get("widgetName")
                purpose = variable.get("purpose")
                if not isinstance(widget_name, str) or not isinstance(purpose, str):
                    errors.append(
                        issue(
                            "document.widget_tree_program_variable",
                            variable_path,
                            "Program variable requires string widgetName and purpose values.",
                        )
                    )
                    continue
                if widget_name in program_purposes:
                    errors.append(
                        issue(
                            "document.widget_tree_program_purpose_duplicate",
                            f"{variable_path}.widgetName",
                            f"Widget {widget_name!r} has more than one program-purpose mapping.",
                        )
                    )
                    continue
                program_purposes[widget_name] = purpose
        widgets = actual.get("widgets") if isinstance(actual.get("widgets"), list) else []
        by_name: dict[str, dict[str, Any]] = {}
        children: dict[str | None, list[str]] = {}
        for widget_index, widget in enumerate(widgets):
            if not isinstance(widget, dict):
                continue
            name = widget.get("widgetName")
            if not isinstance(name, str):
                continue
            if name in by_name:
                errors.append(
                    issue(
                        "document.widget_tree_duplicate",
                        f"{path}.treeRows[{widget_index}].widgetName",
                        f"Duplicate Widget name {name!r}.",
                    )
                )
                continue
            by_name[name] = widget
            parent = widget.get("parentWidgetName")
            children.setdefault(parent, []).append(name)
        if not legacy_three_column:
            for widget_name in program_purposes:
                if widget_name not in by_name:
                    errors.append(
                        issue(
                            "document.widget_tree_program_widget_missing",
                            path,
                            f"Program variable Widget {widget_name!r} is absent from this asset readback.",
                        )
                    )
        for name, widget in by_name.items():
            parent = widget.get("parentWidgetName")
            if parent is not None and parent not in by_name:
                errors.append(
                    issue(
                        "document.widget_tree_parent_missing",
                        path,
                        f"Widget {name!r} names missing parent {parent!r}.",
                    )
                )

        rows: list[dict[str, Any]] = []
        state: dict[str, int] = {}

        def visit(name: str, depth: int) -> None:
            mark = state.get(name, 0)
            if mark == 1:
                errors.append(issue("document.widget_tree_cycle", path, f"Widget parent cycle reaches {name!r}."))
                return
            if mark == 2:
                return
            state[name] = 1
            widget = by_name[name]
            class_path = widget.get("classPath")
            row = {
                "depth": depth,
                "widgetName": name,
                "className": widget_class_name(class_path) if isinstance(class_path, str) else "",
                "isVariable": widget.get("isVariable") is True,
            }
            if not legacy_three_column:
                row["programPurpose"] = program_purposes.get(name, "")
            rows.append(row)
            for child_name in children.get(name, []):
                visit(child_name, depth + 1)
            state[name] = 2

        for root_name in children.get(None, []):
            visit(root_name, 0)
        for name in by_name:
            if state.get(name, 0) == 0:
                visit(name, 0)
        if len({row["widgetName"] for row in rows}) != len(by_name):
            errors.append(issue("document.widget_tree_disconnected", path, "WidgetTree projection did not cover every Widget exactly once."))

        projected: dict[str, Any] = {
            "assetId": asset_id,
            "assetPath": asset_path,
            "treeRows": rows,
        }
        if not legacy_three_column and isinstance(parent_class_path, str) and parent_class_path:
            projected["parentClassPath"] = parent_class_path
        if not rows:
            if actual.get("representationKind") != "reuse-only":
                errors.append(issue("document.widget_tree_empty", path, "Only reuse-only assets may have an empty owned WidgetTree."))
            projected["emptyState"] = WIDGET_TREE_EMPTY_STATE
        projected_assets.append(projected)
    return (
        {
            "format": LEGACY_WIDGET_TREE_TABLE_FORMAT if legacy_three_column else WIDGET_TREE_TABLE_FORMAT,
            "headers": LEGACY_WIDGET_TREE_TABLE_HEADERS if legacy_three_column else WIDGET_TREE_TABLE_HEADERS,
            "indentTwipsPerDepth": WIDGET_TREE_INDENT_TWIPS,
            "assets": projected_assets,
        },
        errors,
    )


def layout_entry_class(node: dict[str, Any]) -> str | None:
    properties = node.get("properties") if isinstance(node.get("properties"), dict) else {}
    entry = properties.get("entryWidgetClass")
    if isinstance(entry, dict) and isinstance(entry.get("refPath"), str):
        return entry["refPath"]
    return entry if isinstance(entry, str) else None


def runtime_fields(requirement: dict[str, Any], accepted: set[str]) -> list[dict[str, Any]]:
    ui_model = requirement.get("uiModel") if isinstance(requirement.get("uiModel"), dict) else {}
    elements = {item.get("id"): item for item in ui_model.get("elements", []) if isinstance(item, dict)}
    values: list[dict[str, Any]] = []
    for field in ui_model.get("runtimeFields", []):
        if not isinstance(field, dict) or not is_accepted_in_scope(field, accepted):
            continue
        element = elements.get(field.get("elementId"))
        if not isinstance(element, dict) or element.get("inBuildScope") is not True or element.get("runtimeControlled") is not True:
            continue
        values.append(field)
    return values


def runtime_field_node_mappings(
    runtime_field: dict[str, Any],
    mappings: list[dict[str, Any]],
    requirement: dict[str, Any],
    accepted: set[str],
) -> list[dict[str, Any]]:
    """Resolve one logical runtime value to one Widget or a complete state mirror.

    A value normally owns exactly one nodeMapping.  A full-branch exclusive state
    implementation may need the same value in one Widget per branch (for example,
    selected and unselected labels with different typography).  That exception is
    accepted only when every in-scope state of one reviewed exclusive axis is
    represented exactly once by composite-state mappings in the same asset and
    each mapping is traceable to an element in its corresponding complete branch.
    """

    field_id = runtime_field.get("id")
    candidates = [
        mapping
        for mapping in mappings
        if isinstance(mapping, dict)
        and field_id in mapping.get("requirementRefs", [])
    ]
    if len(candidates) == 1:
        return candidates
    if len(candidates) < 2:
        return []
    if len({mapping.get("assetId") for mapping in candidates}) != 1:
        return []
    if any(mapping.get("mappingKind") != "composite-state" for mapping in candidates):
        return []

    ui_model = (
        requirement.get("uiModel")
        if isinstance(requirement.get("uiModel"), dict)
        else {}
    )
    elements = {
        element.get("id"): element
        for element in ui_model.get("elements", [])
        if isinstance(element, dict) and isinstance(element.get("id"), str)
    }
    source_element = elements.get(runtime_field.get("elementId"))
    if (
        not isinstance(source_element, dict)
        or source_element.get("runtimeControlled") is not True
        or source_element.get("inBuildScope") is not True
        or not any(
            claim_id in accepted for claim_id in source_element.get("claimIds", [])
        )
    ):
        return []
    source_properties = (
        source_element.get("properties")
        if isinstance(source_element.get("properties"), dict)
        else {}
    )
    source_signature = (
        source_element.get("kind"),
        source_element.get("familyId"),
        source_properties.get("widgetClass"),
    )
    if (
        source_properties.get("isVariable") is not True
        or not all(isinstance(value, str) and value for value in source_signature)
    ):
        return []

    candidate_states: list[str] = []
    for mapping in candidates:
        state_refs = mapping.get("stateRefs")
        if (
            not isinstance(state_refs, list)
            or len(state_refs) != 1
            or not isinstance(state_refs[0], str)
        ):
            return []
        candidate_states.append(state_refs[0])
    if len(set(candidate_states)) != len(candidate_states):
        return []

    for model in requirement.get("stateModels", []):
        if not isinstance(model, dict) or not any(
            claim_id in accepted for claim_id in model.get("claimIds", [])
        ):
            continue
        implementation = (
            model.get("implementation")
            if isinstance(model.get("implementation"), dict)
            else {}
        )
        if implementation.get("strategy") != "exclusive-panel-branches":
            continue
        branches = implementation.get("branches")
        if not isinstance(branches, list):
            continue
        branches_by_state = {
            branch.get("stateId"): branch
            for branch in branches
            if isinstance(branch, dict) and isinstance(branch.get("stateId"), str)
        }
        for axis in model.get("axes", []):
            if (
                not isinstance(axis, dict)
                or axis.get("exclusive") is not True
                or implementation.get("axisId") != axis.get("id")
                or not any(
                    claim_id in accepted for claim_id in axis.get("claimIds", [])
                )
            ):
                continue
            axis_states = {
                state.get("id")
                for state in axis.get("states", [])
                if isinstance(state, dict)
                and state.get("inBuildScope") is True
                and isinstance(state.get("id"), str)
                and any(
                    claim_id in accepted for claim_id in state.get("claimIds", [])
                )
            }
            if set(candidate_states) != axis_states or set(branches_by_state) != axis_states:
                continue
            traceable = True
            mapped_mirror_element_ids: set[str] = set()
            for mapping, state_id in zip(candidates, candidate_states):
                branch = branches_by_state[state_id]
                complete_elements = set(branch.get("completeElementIds", []))
                mapped_branch_elements = complete_elements.intersection(
                    mapping.get("requirementRefs", [])
                )
                all_mapped_elements = set(mapping.get("requirementRefs", [])).intersection(
                    elements
                )
                if (
                    len(mapped_branch_elements) != 1
                    or all_mapped_elements != mapped_branch_elements
                ):
                    traceable = False
                    break
                mapped_element_id = next(iter(mapped_branch_elements))
                mapped_mirror_element_ids.add(mapped_element_id)
                mapped_element = elements.get(mapped_element_id)
                mapped_properties = (
                    mapped_element.get("properties")
                    if isinstance(mapped_element, dict)
                    and isinstance(mapped_element.get("properties"), dict)
                    else {}
                )
                if (
                    not isinstance(mapped_element, dict)
                    or mapped_element.get("runtimeControlled") is not True
                    or mapped_element.get("inBuildScope") is not True
                    or mapped_properties.get("isVariable") is not True
                    or not any(
                        claim_id in accepted
                        for claim_id in mapped_element.get("claimIds", [])
                    )
                    or (
                        mapped_element.get("kind"),
                        mapped_element.get("familyId"),
                        mapped_properties.get("widgetClass"),
                    )
                    != source_signature
                ):
                    traceable = False
                    break
            if (
                traceable
                and runtime_field.get("elementId") in mapped_mirror_element_ids
            ):
                return candidates
    return []


def runtime_collections(requirement: dict[str, Any], accepted: set[str]) -> list[dict[str, Any]]:
    ui_model = requirement.get("uiModel") if isinstance(requirement.get("uiModel"), dict) else {}
    return [
        item
        for item in ui_model.get("collections", [])
        if isinstance(item, dict) and item.get("dynamic") is True and is_accepted_in_scope(item, accepted)
    ]


def mapping_widget(
    mapping: dict[str, Any], indexes: dict[str, Any]
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    record = indexes["mappings"].get(mapping.get("id"))
    if record is None:
        return None, None
    asset_id, read_mapping = record
    return read_mapping, indexes["widgets"].get((asset_id, read_mapping.get("widgetName")))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
