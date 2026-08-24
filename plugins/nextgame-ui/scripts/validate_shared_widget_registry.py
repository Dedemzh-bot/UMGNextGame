#!/usr/bin/env python3
"""Validate the curated NextGame SharedWidgetRegistry 0.3/0.4."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REGISTRY = PLUGIN_ROOT / "assets" / "shared-widget-registry.json"
DEFAULT_SCHEMA = PLUGIN_ROOT / "assets" / "shared-widget-registry.schema.json"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(
            handle,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"Non-finite JSON number is forbidden: {token}")
            ),
        )


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def compute_reuse_contract_sha256(entry: dict[str, Any]) -> str:
    """Hash the reusable identity, capabilities, interface, and supported creation modes."""

    material = {
        "generatedClassPath": entry.get("generatedClassPath"),
        "assetKind": entry.get("assetKind"),
        "capabilityIds": entry.get("capabilityIds"),
        "interfaceSha256": entry.get("interfaceSha256"),
        "similarityContract": entry.get("similarityContract"),
        "generationModes": entry.get("generationModes"),
    }
    if "extensionSlotsContract" in entry:
        material["extensionSlotsContract"] = entry.get("extensionSlotsContract")
        material["extensionSlotMigration"] = entry.get("extensionSlotMigration")
    else:
        material["extensionSlotContract"] = entry.get("extensionSlotContract")
    return canonical_sha256(material)


def resolve_registry_artifact_path(registry_path: Path, raw_path: str) -> Path:
    """Resolve project-relative registry evidence without assuming the process CWD."""

    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate.resolve()
    resolved_registry = registry_path.resolve()
    for parent in (resolved_registry.parent, *resolved_registry.parents):
        if (parent / "NextGame.uproject").is_file():
            return (parent / candidate).resolve()
    return (resolved_registry.parent / candidate).resolve()


def value_matches_kind(value: Any, value_kind: str) -> bool:
    """Check concrete JSON values for the primitive kinds the registry can prove."""

    if value_kind == "boolean":
        return isinstance(value, bool)
    if value_kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    if value_kind in {"text", "color", "brush", "asset", "class", "enum"}:
        return isinstance(value, str) and bool(value)
    if value_kind == "struct":
        return isinstance(value, dict)
    return True


def issue(code: str, path: str, message: str) -> dict[str, str]:
    return {"code": code, "path": path, "message": message}


def _is_up_to_date_blueprint_status(value: Any) -> bool:
    normalized = str(value).upper().replace(" ", "_")
    return "DIRTY" not in normalized and (
        "BS_UP_TO_DATE" in normalized or "UP_TO_DATE" in normalized or "UPTODATE" in normalized
    )


def _snapshot_groups(container: Any, names: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    snapshots: dict[str, dict[str, Any]] = {}
    if not isinstance(container, dict):
        return snapshots
    for name in names:
        group = container.get(name)
        if not isinstance(group, dict):
            continue
        for asset_path, snapshot in group.items():
            if isinstance(asset_path, str) and isinstance(snapshot, dict):
                snapshots[asset_path] = snapshot
    return snapshots


def _bound_consumer_snapshots(snapshots: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        asset_path: snapshot
        for asset_path, snapshot in snapshots.items()
        if bool(snapshot.get("namedSlots")) or bool(snapshot.get("namedSlotBindings"))
    }


def _file_sha256(records: Any, asset_path: str) -> str | None:
    if not isinstance(records, dict):
        return None
    record = records.get(asset_path)
    value = record.get("sha256") if isinstance(record, dict) else None
    return value if isinstance(value, str) else None


def _shared_slot_snapshot(common: Any, slot_name: str) -> dict[str, Any] | None:
    if not isinstance(common, dict):
        return None
    tree = common.get("tree")
    layouts = common.get("slotLayouts")
    if not isinstance(tree, list) or not isinstance(layouts, dict):
        return None
    widget = next(
        (item for item in tree if isinstance(item, dict) and item.get("name") == slot_name),
        None,
    )
    layout = layouts.get(slot_name)
    if not isinstance(widget, dict) or not isinstance(layout, dict):
        return None
    return {
        "classPath": widget.get("classPath"),
        "parentWidgetName": widget.get("parentName"),
        "isVariable": widget.get("isVariable"),
        "contentWidgetName": widget.get("namedSlotContent"),
        "layout": layout,
        "zOrder": layout.get("zOrder"),
        "visibility": widget.get("visibility"),
    }


def validate_verified_slot_operation_report(
    report: Any,
    entry: dict[str, Any],
    migration: dict[str, Any],
    schema: dict[str, Any],
    *,
    path: str,
) -> list[dict[str, str]]:
    """Validate saved, fresh-readback evidence instead of trusting a completed label."""

    errors: list[dict[str, str]] = []
    report_schema = schema.get("$defs", {}).get("verifiedSlotOperationReportV01")
    if isinstance(report_schema, dict):
        errors.extend(
            validate_schema_instance(
                report,
                report_schema,
                root_schema=schema,
                path=path,
            )
        )
    else:
        errors.append(issue("registry.extension_slot_migration_report_contract", path, "The verified migration-report schema is missing."))
    if not isinstance(report, dict):
        return errors

    if not (
        report.get("ok") is True
        and report.get("status") == "completed"
        and report.get("mode") == "commit"
        and report.get("mutationPerformed") is True
    ):
        errors.append(issue("registry.extension_slot_migration_report_status", path, "Verified evidence must be a successful completed commit that performed the declared mutation."))
    for flag in ("assetCompileInvoked", "assetSaveInvoked", "assetReloadInvoked"):
        if report.get(flag) is not True:
            errors.append(issue("registry.extension_slot_migration_report_lifecycle", f"{path}.{flag}", "Verified evidence must prove compile, save, and fresh reload."))

    mutation_tracker = report.get("mutationTracker")
    attempts = mutation_tracker.get("attempts") if isinstance(mutation_tracker, dict) else None
    performed_attempts = [
        item
        for item in attempts
        if isinstance(item, dict) and item.get("state") == "performed"
    ] if isinstance(attempts, list) else []
    required_mutations = (
        {"RenameWidget", "AddWidget"}
        if migration.get("operation") == "migrate-existing-standard-slot"
        else {"AddWidget"}
    )
    if (
        not isinstance(mutation_tracker, dict)
        or mutation_tracker.get("mutationPossible") is not True
        or mutation_tracker.get("mutationPerformed") is not True
        or not required_mutations.issubset({item.get("operation") for item in performed_attempts})
    ):
        errors.append(issue("registry.extension_slot_migration_report_mutation", f"{path}.mutationTracker", "Verified evidence requires performed mutating API attempts for the declared Slot operation."))

    asset_path = entry.get("assetPath")
    object_path = entry.get("objectPath")
    generated_class_path = entry.get("generatedClassPath")
    preflight = report.get("preflight")
    after_save = report.get("verificationAfterSave")
    reported_registry = preflight.get("registry") if isinstance(preflight, dict) else None
    reported_after_registry = after_save.get("registry") if isinstance(after_save, dict) else None
    reported_operation = reported_registry.get("extensionSlotMigration") if isinstance(reported_registry, dict) else None
    operation_fields = (
        ("operation", "addedStandardNames", "preSaveValidationRequired", "reportPath")
        if migration.get("operation") == "add-dual-layer-slots"
        else (
            "operation",
            "fromRegistryVersion",
            "oldStandardName",
            "renamedStandardName",
            "addedStandardName",
            "legacyPreservedNames",
            "preSaveValidationRequired",
            "reportPath",
        )
    )
    if (
        not isinstance(reported_registry, dict)
        or reported_registry.get("entryId") != entry.get("id")
        or not isinstance(reported_operation, dict)
        or reported_operation.get("status") != "planned"
        or any(reported_operation.get(field) != migration.get(field) for field in operation_fields)
        or reported_after_registry != reported_registry
    ):
        errors.append(issue("registry.extension_slot_migration_report_registry_binding", f"{path}.preflight.registry", "Migration report must bind the same planned registry entry and Slot operation before and after the saved migration."))
    common_before = preflight.get("common") if isinstance(preflight, dict) else None
    common_after = after_save.get("common") if isinstance(after_save, dict) else None
    if not isinstance(common_before, dict) or not isinstance(common_after, dict):
        errors.append(issue("registry.extension_slot_migration_report_common", path, "Verified evidence requires preflight and fresh post-save shared-Widget snapshots."))
        return errors

    for label, snapshot in (("preflight", common_before), ("postSave", common_after)):
        if (
            snapshot.get("assetPath") != asset_path
            or snapshot.get("objectPath") != object_path
            or snapshot.get("generatedClassPath") != generated_class_path
        ):
            errors.append(issue("registry.extension_slot_migration_report_identity", f"{path}.{label}", "Migration report shared-Widget identity does not match the registry entry."))
    if not _is_up_to_date_blueprint_status(common_after.get("compileStatus")):
        errors.append(issue("registry.extension_slot_migration_report_compile", f"{path}.verificationAfterSave.common.compileStatus", "Fresh post-save shared Widget must compile UpToDate."))

    operations = report.get("operations") if isinstance(report.get("operations"), list) else []
    if migration.get("operation") == "migrate-existing-standard-slot":
        rename_record = next(
            (
                item for item in operations
                if isinstance(item, dict)
                and item.get("operation") == "RenameWidget"
                and item.get("assetPath") == asset_path
                and item.get("oldName") == migration.get("oldStandardName")
                and item.get("newName") == migration.get("renamedStandardName")
                and item.get("readbackVerified") is True
            ),
            None,
        )
        added_names = {migration.get("addedStandardName")}
    else:
        rename_record = True
        added_names = set(migration.get("addedStandardNames", []))
    recorded_adds = {
        item.get("widgetName")
        for item in operations
        if isinstance(item, dict)
        and item.get("operation") == "AddWidget"
        and item.get("assetPath") == asset_path
        and item.get("readbackVerified") is True
    }
    if rename_record is None or not added_names.issubset(recorded_adds):
        errors.append(issue("registry.extension_slot_migration_report_mutation", f"{path}.operations", "Mutating operation records do not match the declared dual-layer Slot operation."))
    compiled = any(
        isinstance(item, dict)
        and item.get("operation") == "compile"
        and item.get("assetPath") == asset_path
        and item.get("result") is True
        for item in operations
    )
    saved = next(
        (
            item
            for item in operations
            if isinstance(item, dict)
            and item.get("operation") == "save"
            and item.get("assetPath") == asset_path
            and item.get("result") is True
        ),
        None,
    )
    reloaded = any(
        isinstance(item, dict)
        and item.get("operation") == "reload"
        and item.get("result") is True
        and isinstance(item.get("assetPaths"), list)
        and asset_path in item["assetPaths"]
        for item in operations
    )
    if not compiled or saved is None or not reloaded:
        errors.append(issue("registry.extension_slot_migration_report_save", f"{path}.operations", "Shared Widget requires successful compile, save, and fresh reload operation records."))

    dirty_allowed = after_save.get("dirtyAllowedPackages")
    dirty_guard = after_save.get("globalDirtyGuard")
    if (
        dirty_allowed != []
        or not isinstance(dirty_guard, dict)
        or dirty_guard.get("allowedNewDirty") != []
        or dirty_guard.get("newDirty") != []
        or (isinstance(asset_path, str) and asset_path in dirty_guard.get("current", []))
    ):
        errors.append(issue("registry.extension_slot_migration_report_clean", f"{path}.verificationAfterSave", "Fresh post-save shared Widget must be clean with no new dirty packages."))

    files_after = report.get("filesAfterSave")
    fresh_files = after_save.get("diskFiles")
    saved_sha = saved.get("sha256After") if isinstance(saved, dict) else None
    file_after_sha = _file_sha256(files_after, str(asset_path))
    fresh_sha = _file_sha256(fresh_files, str(asset_path))
    if (
        not isinstance(saved_sha, str)
        or not SHA256_PATTERN.fullmatch(saved_sha)
        or saved_sha != file_after_sha
        or saved_sha != fresh_sha
    ):
        errors.append(issue("registry.extension_slot_migration_report_disk", f"{path}.filesAfterSave", "Saved and fresh-loaded shared-Widget file identities must agree."))

    operation = migration.get("operation")
    preserved_names = migration.get("legacyPreservedNames", []) if operation == "migrate-existing-standard-slot" else []
    if not isinstance(preserved_names, list):
        preserved_names = []
    named_readback = common_after.get("namedSlotReadback")
    named_readback_names = [
        item.get("slotName")
        for item in named_readback
        if isinstance(item, dict) and isinstance(item.get("slotName"), str)
    ] if isinstance(named_readback, list) else []
    named_by_name = {
        item.get("slotName"): item
        for item in named_readback
        if isinstance(item, dict) and isinstance(item.get("slotName"), str)
    } if isinstance(named_readback, list) else {}
    tree = common_after.get("tree")
    tree_by_name = {
        item.get("name"): item
        for item in tree
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    } if isinstance(tree, list) else {}
    direct_children = common_after.get("directRootChildren")
    direct_layers = common_after.get("directRootLayers")
    layer_by_name = {
        item.get("name"): item
        for item in direct_layers
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    } if isinstance(direct_layers, list) else {}
    direct_layer_names = [
        item.get("name")
        for item in direct_layers
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    ] if isinstance(direct_layers, list) else []
    if len(named_readback_names) != len(set(named_readback_names)):
        errors.append(issue("registry.extension_slot_migration_report_slot", f"{path}.verificationAfterSave.common.namedSlotReadback", "Fresh NamedSlot readback names must be unique."))
    if (
        not isinstance(direct_children, list)
        or len(direct_children) != len(set(direct_children))
        or len(direct_layer_names) != len(set(direct_layer_names))
        or set(direct_layer_names) != set(direct_children)
    ):
        errors.append(issue("registry.extension_slot_migration_report_layering", f"{path}.verificationAfterSave.common.directRootLayers", "Direct-root child and layer evidence must be complete and unique."))
    slot_names = ["SlotDown", "SlotUp"]
    expected_layout = {
        "anchorsMinimum": [0, 0],
        "anchorsMaximum": [1, 1],
        "offsets": [0, 0, 0, 0],
        "alignment": [0, 0],
    }
    for slot_name in slot_names:
        slot = named_by_name.get(slot_name)
        widget = tree_by_name.get(slot_name)
        if not isinstance(slot, dict) or not isinstance(widget, dict):
            errors.append(issue("registry.extension_slot_migration_report_slot", f"{path}.verificationAfterSave.common", f"Fresh post-save readback is missing {slot_name}."))
            continue
        layout = slot.get("layout")
        if (
            widget.get("classPath") != "/Script/UMG.NamedSlot"
            or slot.get("isVariable") is not True
            or slot.get("visibility") != "SelfHitTestInvisible"
            or slot.get("contentWidgetName") is not None
            or not isinstance(layout, dict)
            or any(layout.get(key) != value for key, value in expected_layout.items())
            or layout.get("autoSize") is not False
            or slot.get("zOrder") != layout.get("zOrder")
            or slot.get("zOrder") != layer_by_name.get(slot_name, {}).get("zOrder")
        ):
            errors.append(issue("registry.extension_slot_migration_report_slot", f"{path}.verificationAfterSave.common.namedSlotReadback", f"{slot_name} does not satisfy the saved dual-layer Slot contract."))
    if not isinstance(direct_children, list) or not direct_children or direct_children[0] != "SlotDown" or direct_children[-1] != "SlotUp":
        errors.append(issue("registry.extension_slot_migration_report_layering", f"{path}.verificationAfterSave.common.directRootChildren", "SlotDown and SlotUp must be the first and last shared-root direct children."))
    down_layer = layer_by_name.get("SlotDown")
    up_layer = layer_by_name.get("SlotUp")
    other_layers = [item for name, item in layer_by_name.items() if name not in slot_names]
    if (
        not isinstance(down_layer, dict)
        or not isinstance(up_layer, dict)
        or not other_layers
        or not all(isinstance(item.get("zOrder"), int) for item in [down_layer, up_layer, *other_layers])
        or not all(down_layer["zOrder"] < item["zOrder"] for item in other_layers)
        or not all(up_layer["zOrder"] > item["zOrder"] for item in other_layers)
    ):
        errors.append(issue("registry.extension_slot_migration_report_layering", f"{path}.verificationAfterSave.common.directRootLayers", "Saved SlotDown/SlotUp ZOrder must be strict extrema."))

    old_name = migration.get("oldStandardName") if operation == "migrate-existing-standard-slot" else "SlotContent"
    if (
        not isinstance(old_name, str)
        or old_name in tree_by_name
        or old_name in named_by_name
        or ("oldStandardPresent" in common_after and common_after.get("oldStandardPresent") is not False)
    ):
        errors.append(issue("registry.extension_slot_migration_report_old_present", f"{path}.verificationAfterSave.common", "Fresh post-save readback must prove the old SlotContent-compatible standard Slot is absent."))
    slot_presence = after_save.get("commonSlotPresence") if isinstance(after_save, dict) else None
    expected_presence = {"SlotDown": True, "SlotUp": True, old_name: False}
    expected_presence.update({name: True for name in preserved_names})
    if not isinstance(slot_presence, dict) or any(slot_presence.get(name) is not value for name, value in expected_presence.items()):
        errors.append(issue("registry.extension_slot_migration_report_slot_presence", f"{path}.verificationAfterSave.commonSlotPresence", "Saved commonSlotPresence contradicts the required dual and preserved Slot set."))

    pre_snapshots = _snapshot_groups(preflight, ("consumers", "historicalReadOnly", "transitiveReadOnly"))
    after_snapshots = _snapshot_groups(after_save, ("consumers", "readOnlyRegression"))
    bound_before = _bound_consumer_snapshots(pre_snapshots)
    declared_items = report.get("legacyBindingVerification")
    declared_by_asset: dict[str, dict[str, Any]] = {}
    if isinstance(declared_items, list):
        for item in declared_items:
            if isinstance(item, dict) and isinstance(item.get("assetPath"), str):
                if item["assetPath"] in declared_by_asset:
                    errors.append(issue("registry.extension_slot_migration_report_binding_coverage", f"{path}.legacyBindingVerification", "Legacy binding verification asset paths must be unique."))
                declared_by_asset[item["assetPath"]] = item
    if set(declared_by_asset) != set(bound_before):
        errors.append(issue("registry.extension_slot_migration_report_binding_coverage", f"{path}.legacyBindingVerification", "Legacy binding evidence must exactly cover every preflight consumer with NamedSlot content."))
    files_before = preflight.get("filesBefore") if isinstance(preflight, dict) else None
    for consumer_path, before in bound_before.items():
        after = after_snapshots.get(consumer_path)
        declared = declared_by_asset.get(consumer_path)
        if not isinstance(after, dict) or before.get("namedSlots") != after.get("namedSlots") or before.get("namedSlotBindings") != after.get("namedSlotBindings"):
            errors.append(issue("registry.extension_slot_migration_report_binding_changed", f"{path}.verificationAfterSave", f"Saved NamedSlot bindings changed for {consumer_path}."))
            continue
        if not isinstance(declared, dict):
            continue
        expected_before_sha = _file_sha256(files_before, consumer_path)
        expected_after_sha = _file_sha256(files_after, consumer_path)
        if (
            declared.get("beforeNamedSlots") != before.get("namedSlots")
            or declared.get("afterNamedSlots") != after.get("namedSlots")
            or declared.get("beforeNamedSlotBindings") != before.get("namedSlotBindings")
            or declared.get("afterNamedSlotBindings") != after.get("namedSlotBindings")
            or declared.get("beforeFileSha256") != expected_before_sha
            or declared.get("afterFileSha256") != expected_after_sha
            or declared.get("matchesPreflight") is not True
            or declared.get("saved") is not True
            or declared.get("clean") is not True
        ):
            errors.append(issue("registry.extension_slot_migration_report_binding_evidence", f"{path}.legacyBindingVerification", f"Declared binding evidence does not match saved before/after snapshots for {consumer_path}."))

    if entry.get("id") == "shared.common.bag-item":
        known_path = "/Game/UI/UMG/Widgets/uw_common_item"
        known = bound_before.get(known_path, {}).get("namedSlots")
        expected_known = [
            ["NamedSlot_149", None, "CanvasPanel_42"],
            ["Slot1", None, "CanvasPanel_39"],
        ]
        if known != expected_known:
            errors.append(issue("registry.extension_slot_migration_report_known_binding", f"{path}.preflight.consumers", "uw_common_item must prove both known legacy NamedSlot bindings before migration."))

    shared_legacy = report.get("sharedLegacySlotVerification")
    if isinstance(after_save, dict) and after_save.get("legacyBindingVerification") != report.get("legacyBindingVerification"):
        errors.append(issue("registry.extension_slot_migration_report_binding_evidence", f"{path}.legacyBindingVerification", "Top-level and fresh post-save legacy binding evidence must be identical."))
    if isinstance(after_save, dict) and after_save.get("sharedLegacySlotVerification") != shared_legacy:
        errors.append(issue("registry.extension_slot_migration_report_legacy_slot", f"{path}.sharedLegacySlotVerification", "Top-level and fresh post-save shared legacy Slot evidence must be identical."))
    declared_legacy_names = shared_legacy.get("legacyPreservedNames") if isinstance(shared_legacy, dict) else None
    declared_slots = shared_legacy.get("slots") if isinstance(shared_legacy, dict) else None
    declared_slot_map = {
        item.get("slotName"): item
        for item in declared_slots
        if isinstance(item, dict) and isinstance(item.get("slotName"), str)
    } if isinstance(declared_slots, list) else {}
    if declared_legacy_names != preserved_names or set(declared_slot_map) != set(preserved_names):
        errors.append(issue("registry.extension_slot_migration_report_legacy_slot", f"{path}.sharedLegacySlotVerification", "Shared legacy Slot evidence must exactly cover the migration's preserved names."))
    for slot_name in preserved_names:
        before_slot = _shared_slot_snapshot(common_before, slot_name)
        after_slot = _shared_slot_snapshot(common_after, slot_name)
        declared = declared_slot_map.get(slot_name)
        if (
            before_slot is None
            or after_slot is None
            or before_slot != after_slot
            or not isinstance(declared, dict)
            or declared.get("before") != before_slot
            or declared.get("after") != after_slot
            or declared.get("matches") is not True
        ):
            errors.append(issue("registry.extension_slot_migration_report_legacy_slot", f"{path}.sharedLegacySlotVerification", f"Preserved shared NamedSlot {slot_name!r} changed or lacks exact saved evidence."))
        if slot_name not in named_by_name or tree_by_name.get(slot_name, {}).get("classPath") != "/Script/UMG.NamedSlot":
            errors.append(issue("registry.extension_slot_migration_report_legacy_slot", f"{path}.verificationAfterSave.common", f"Preserved shared NamedSlot {slot_name!r} is absent after save."))

    return errors


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def _resolve_ref(root_schema: dict[str, Any], reference: str) -> dict[str, Any] | None:
    if not reference.startswith("#/"):
        return None
    current: Any = root_schema
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current if isinstance(current, dict) else None


def validate_schema_instance(
    value: Any,
    schema: dict[str, Any],
    *,
    root_schema: dict[str, Any] | None = None,
    path: str = "$",
) -> list[dict[str, str]]:
    """Validate the closed JSON-Schema subset used by this registry."""

    root_schema = root_schema or schema
    reference = schema.get("$ref")
    if isinstance(reference, str):
        resolved = _resolve_ref(root_schema, reference)
        if resolved is None:
            return [issue("schema.ref", path, f"Unresolvable schema reference: {reference}")]
        return validate_schema_instance(value, resolved, root_schema=root_schema, path=path)

    alternatives = schema.get("anyOf")
    if isinstance(alternatives, list):
        branch_results = [
            validate_schema_instance(value, branch, root_schema=root_schema, path=path)
            for branch in alternatives
            if isinstance(branch, dict)
        ]
        if branch_results and all(result for result in branch_results):
            return [issue("schema.any_of", path, "Value does not match any allowed shape.")]
        return []

    alternatives = schema.get("oneOf")
    if isinstance(alternatives, list):
        branch_results = [
            validate_schema_instance(value, branch, root_schema=root_schema, path=path)
            for branch in alternatives
            if isinstance(branch, dict)
        ]
        if sum(not result for result in branch_results) != 1:
            return [issue("schema.one_of", path, "Value must match exactly one allowed shape.")]
        return []

    errors: list[dict[str, str]] = []
    expected = schema.get("type")
    expected_types = [expected] if isinstance(expected, str) else expected
    if isinstance(expected_types, list) and not any(_matches_type(value, item) for item in expected_types):
        return [issue("schema.type", path, f"Expected type {expected_types}, got {type(value).__name__}.")]

    if "const" in schema and value != schema["const"]:
        errors.append(issue("schema.const", path, f"Value must equal {schema['const']!r}."))
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        errors.append(issue("schema.enum", path, f"Value must be one of {enum!r}."))

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        for key in required if isinstance(required, list) else []:
            if key not in value:
                errors.append(issue("schema.required", path, f"Missing required field: {key}"))
        if schema.get("additionalProperties") is False and isinstance(properties, dict):
            for key in value:
                if key not in properties:
                    errors.append(issue("schema.additional_property", f"{path}.{key}", "Unknown field is not allowed."))
        if isinstance(properties, dict):
            for key, child in value.items():
                child_schema = properties.get(key)
                if isinstance(child_schema, dict):
                    errors.extend(
                        validate_schema_instance(
                            child,
                            child_schema,
                            root_schema=root_schema,
                            path=f"{path}.{key}",
                        )
                    )

    if isinstance(value, list):
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if isinstance(minimum, int) and len(value) < minimum:
            errors.append(issue("schema.min_items", path, f"Expected at least {minimum} items."))
        if isinstance(maximum, int) and len(value) > maximum:
            errors.append(issue("schema.max_items", path, f"Expected at most {maximum} items."))
        if schema.get("uniqueItems") is True:
            serialized = [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in value]
            if len(serialized) != len(set(serialized)):
                errors.append(issue("schema.unique_items", path, "Array items must be unique."))
        prefix_items = schema.get("prefixItems")
        if isinstance(prefix_items, list):
            for index, child_schema in enumerate(prefix_items):
                if index < len(value) and isinstance(child_schema, dict):
                    errors.extend(
                        validate_schema_instance(
                            value[index],
                            child_schema,
                            root_schema=root_schema,
                            path=f"{path}[{index}]",
                        )
                    )
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, child in enumerate(value):
                errors.extend(
                    validate_schema_instance(
                        child,
                        item_schema,
                        root_schema=root_schema,
                        path=f"{path}[{index}]",
                    )
                )

    if isinstance(value, str):
        minimum = schema.get("minLength")
        if isinstance(minimum, int) and len(value) < minimum:
            errors.append(issue("schema.min_length", path, f"String must contain at least {minimum} characters."))
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.fullmatch(pattern, value) is None:
            errors.append(issue("schema.pattern", path, f"String does not match required pattern: {pattern}"))

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        exclusive_minimum = schema.get("exclusiveMinimum")
        if isinstance(minimum, (int, float)) and value < minimum:
            errors.append(issue("schema.minimum", path, f"Value must be >= {minimum}."))
        if isinstance(exclusive_minimum, (int, float)) and value <= exclusive_minimum:
            errors.append(issue("schema.exclusive_minimum", path, f"Value must be > {exclusive_minimum}."))

    return errors


def validate_registry(
    registry: Any,
    schema: dict[str, Any],
    *,
    registry_path: Path | None = None,
    check_linked_files: bool = False,
) -> dict[str, Any]:
    errors = validate_schema_instance(registry, schema)
    warnings: list[dict[str, str]] = []
    if not isinstance(registry, dict):
        return {"valid": not errors, "errors": errors, "warnings": warnings}

    entries = registry.get("entries")
    if not isinstance(entries, list):
        return {"valid": not errors, "errors": errors, "warnings": warnings}

    seen: dict[str, set[str]] = {
        "id": set(),
        "assetPath": set(),
        "objectPath": set(),
        "generatedClassPath": set(),
    }
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        base = f"$.entries[{index}]"
        for field, values in seen.items():
            value = entry.get(field)
            if not isinstance(value, str):
                continue
            if value in values:
                errors.append(issue(f"registry.duplicate_{field}", f"{base}.{field}", f"{field} must be unique."))
            values.add(value)

        asset_path = entry.get("assetPath")
        object_path = entry.get("objectPath")
        generated_class_path = entry.get("generatedClassPath")
        if isinstance(asset_path, str):
            asset_name = asset_path.rsplit("/", 1)[-1]
            expected_object_path = f"{asset_path}.{asset_name}"
            expected_generated_class = f"{expected_object_path}_C"
            if object_path != expected_object_path:
                errors.append(issue("registry.object_path", f"{base}.objectPath", f"Expected {expected_object_path}."))
            if generated_class_path != expected_generated_class:
                errors.append(issue("registry.generated_class_path", f"{base}.generatedClassPath", f"Expected {expected_generated_class}."))

        interface_contract = entry.get("interfaceContract")
        interface_hash = entry.get("interfaceSha256")
        if isinstance(interface_contract, dict):
            expected_hash = canonical_sha256(interface_contract)
            if interface_hash != expected_hash:
                errors.append(
                    issue(
                        "registry.interface_digest",
                        f"{base}.interfaceSha256",
                        f"Interface hash mismatch; expected {expected_hash}.",
                    )
                )

            widgets = interface_contract.get("widgets")
            if isinstance(widgets, list):
                names: set[str] = set()
                widgets_by_name: dict[str, dict[str, Any]] = {}
                parents: dict[str, str | None] = {}
                roots = 0
                for widget_index, widget in enumerate(widgets):
                    if not isinstance(widget, dict):
                        continue
                    name = widget.get("name")
                    if isinstance(name, str):
                        if name in names:
                            errors.append(issue("registry.widget_duplicate", f"{base}.interfaceContract.widgets[{widget_index}].name", "Widget names must be unique inside an interface."))
                        names.add(name)
                        widgets_by_name[name] = widget
                        parents[name] = widget.get("parentName") if isinstance(widget.get("parentName"), str) else None
                    if widget.get("parentName") is None:
                        roots += 1
                if roots != 1:
                    errors.append(issue("registry.widget_root", f"{base}.interfaceContract.widgets", "Interface widgets must contain exactly one root."))
                for name, parent in parents.items():
                    if parent is not None and parent not in names:
                        errors.append(issue("registry.widget_parent", f"{base}.interfaceContract.widgets", f"Widget {name!r} references unknown parent {parent!r}."))

                extension_slot = entry.get("extensionSlotContract")
                if isinstance(extension_slot, dict):
                    slot_name = extension_slot.get("widgetName")
                    slot_widget = widgets_by_name.get(slot_name) if isinstance(slot_name, str) else None
                    slot_status = extension_slot.get("status")
                    if slot_status == "verified":
                        if not isinstance(slot_widget, dict):
                            errors.append(issue("registry.extension_slot_missing", f"{base}.interfaceContract.widgets", "A verified extension slot must exist in the shared WidgetTree interface."))
                        elif slot_widget.get("classPath") != extension_slot.get("classPath"):
                            errors.append(issue("registry.extension_slot_class", f"{base}.interfaceContract.widgets", "The verified extension slot Widget class must match extensionSlotContract.classPath."))
                        if isinstance(slot_widget, dict):
                            parent_name = slot_widget.get("parentName")
                            siblings = [
                                item for item in widgets
                                if isinstance(item, dict) and item.get("parentName") == parent_name
                            ]
                            sibling_indices = [item.get("siblingIndex") for item in siblings]
                            if (
                                not isinstance(slot_widget.get("siblingIndex"), int)
                                or any(not isinstance(value, int) for value in sibling_indices)
                                or slot_widget.get("siblingIndex") != max(sibling_indices, default=-1)
                            ):
                                errors.append(issue("registry.extension_slot_order", f"{base}.interfaceContract.widgets", "The verified extension slot must be the last direct child of its parent and carry siblingIndex evidence."))
                            expected_layout = extension_slot.get("defaultLayout")
                            observed_layout = slot_widget.get("slotLayout")
                            if not isinstance(expected_layout, dict) or observed_layout != {
                                "anchors": expected_layout.get("anchors"),
                                "offsets": expected_layout.get("offsets"),
                                "alignment": expected_layout.get("alignment"),
                            }:
                                errors.append(issue("registry.extension_slot_layout", f"{base}.interfaceContract.widgets", "The verified extension slot readback must match the default full-fill layout."))
                    elif isinstance(slot_widget, dict):
                        errors.append(issue("registry.extension_slot_status", f"{base}.extensionSlotContract.status", "An observed extension slot must be marked verified."))

                extension_slots = entry.get("extensionSlotsContract")
                if isinstance(extension_slots, dict):
                    slot_contracts = [
                        item for item in extension_slots.get("slots", []) if isinstance(item, dict)
                    ]
                    slot_status = extension_slots.get("status")
                    observed_standard_slots = {
                        name: widgets_by_name.get(name) for name in ("SlotDown", "SlotUp")
                    }
                    if slot_status == "verified":
                        root_names = [
                            item.get("name")
                            for item in widgets
                            if isinstance(item, dict) and item.get("parentName") is None and isinstance(item.get("name"), str)
                        ]
                        root_name = root_names[0] if len(root_names) == 1 else None
                        root_siblings = [
                            item
                            for item in widgets
                            if isinstance(item, dict) and item.get("parentName") == root_name
                        ] if isinstance(root_name, str) else []
                        sibling_indices = [item.get("siblingIndex") for item in root_siblings]
                        sibling_z_orders = [item.get("zOrder") for item in root_siblings]
                        if (
                            not root_siblings
                            or any(not isinstance(value, int) for value in sibling_indices)
                            or len(sibling_indices) != len(set(sibling_indices))
                        ):
                            errors.append(issue("registry.extension_slots_order_evidence", f"{base}.interfaceContract.widgets", "Verified dual extension Slots require unique siblingIndex evidence for every shared-root direct child."))
                        if any(not isinstance(value, int) for value in sibling_z_orders):
                            errors.append(issue("registry.extension_slots_z_order_evidence", f"{base}.interfaceContract.widgets", "Verified dual extension Slots require integer zOrder evidence for every shared-root direct child."))

                        for contract in slot_contracts:
                            slot_name = contract.get("widgetName")
                            slot_widget = widgets_by_name.get(slot_name) if isinstance(slot_name, str) else None
                            contract_path = f"{base}.extensionSlotsContract.slots"
                            if not isinstance(slot_widget, dict):
                                errors.append(issue("registry.extension_slot_missing", f"{base}.interfaceContract.widgets", f"Verified extension Slot {slot_name!r} is absent from the shared WidgetTree interface."))
                                continue
                            if slot_widget.get("classPath") != contract.get("classPath"):
                                errors.append(issue("registry.extension_slot_class", f"{base}.interfaceContract.widgets", f"Verified extension Slot {slot_name!r} class differs from its contract."))
                            if slot_widget.get("parentName") != root_name:
                                errors.append(issue("registry.extension_slot_parent", f"{base}.interfaceContract.widgets", f"Verified extension Slot {slot_name!r} must be a direct child of the shared root."))
                            if slot_widget.get("isVariable") is not True:
                                errors.append(issue("registry.extension_slot_variable", f"{base}.interfaceContract.widgets", f"Verified extension Slot {slot_name!r} must be IsVariable."))
                            if slot_widget.get("autoSize") is not False:
                                errors.append(issue("registry.extension_slot_auto_size", f"{base}.interfaceContract.widgets", f"Verified extension Slot {slot_name!r} must disable Auto Size for canonical full-fill layout."))
                            if slot_widget.get("visibility") != contract.get("visibility"):
                                errors.append(issue("registry.extension_slot_visibility", f"{base}.interfaceContract.widgets", f"Verified extension Slot {slot_name!r} must be SelfHitTestInvisible so the Slot itself does not intercept input while its children remain interactive."))
                            expected_layout = contract.get("defaultLayout")
                            observed_layout = slot_widget.get("slotLayout")
                            if not isinstance(expected_layout, dict) or observed_layout != {
                                "anchors": expected_layout.get("anchors"),
                                "offsets": expected_layout.get("offsets"),
                                "alignment": expected_layout.get("alignment"),
                            }:
                                errors.append(issue("registry.extension_slot_layout", f"{base}.interfaceContract.widgets", f"Verified extension Slot {slot_name!r} must match the default full-fill layout."))
                            slot_index = slot_widget.get("siblingIndex")
                            if contract.get("treeOrder") == "first" and (
                                not isinstance(slot_index, int) or slot_index != min((value for value in sibling_indices if isinstance(value, int)), default=-1)
                            ):
                                errors.append(issue("registry.extension_slot_order", contract_path, f"{slot_name} must be the first shared-root direct child."))
                            if contract.get("treeOrder") == "last" and (
                                not isinstance(slot_index, int) or slot_index != max((value for value in sibling_indices if isinstance(value, int)), default=-1)
                            ):
                                errors.append(issue("registry.extension_slot_order", contract_path, f"{slot_name} must be the last shared-root direct child."))
                            slot_z_order = slot_widget.get("zOrder")
                            other_z_orders = [
                                item.get("zOrder")
                                for item in root_siblings
                                if item is not slot_widget and isinstance(item.get("zOrder"), int)
                            ]
                            if contract.get("zOrderRelation") == "strictly-lower-than-all-direct-siblings" and (
                                not isinstance(slot_z_order, int) or not other_z_orders or not all(slot_z_order < value for value in other_z_orders)
                            ):
                                errors.append(issue("registry.extension_slot_z_order", contract_path, f"{slot_name} zOrder must be strictly lower than every other shared-root direct sibling."))
                            if contract.get("zOrderRelation") == "strictly-higher-than-all-direct-siblings" and (
                                not isinstance(slot_z_order, int) or not other_z_orders or not all(slot_z_order > value for value in other_z_orders)
                            ):
                                errors.append(issue("registry.extension_slot_z_order", contract_path, f"{slot_name} zOrder must be strictly higher than every other shared-root direct sibling."))

                        for legacy_name in extension_slots.get("legacyPreservedNames", []):
                            legacy_widget = widgets_by_name.get(legacy_name)
                            if not isinstance(legacy_widget, dict) or legacy_widget.get("classPath") != "/Script/UMG.NamedSlot":
                                errors.append(issue("registry.legacy_slot_missing", f"{base}.interfaceContract.widgets", f"Preserved legacy NamedSlot {legacy_name!r} is missing."))
                    elif any(isinstance(widget, dict) for widget in observed_standard_slots.values()):
                        errors.append(issue("registry.extension_slots_status", f"{base}.extensionSlotsContract.status", "Observed SlotDown/SlotUp extension Slots must be marked verified."))

                    migration = entry.get("extensionSlotMigration")
                    if isinstance(migration, dict):
                        operation = migration.get("operation")
                        contract_legacy_names = extension_slots.get("legacyPreservedNames", [])
                        if operation == "migrate-existing-standard-slot":
                            if migration.get("legacyPreservedNames") != contract_legacy_names:
                                errors.append(issue("registry.extension_slot_migration_legacy", f"{base}.extensionSlotMigration.legacyPreservedNames", "Migration-preserved NamedSlots must exactly match extensionSlotsContract.legacyPreservedNames."))
                            old_name = migration.get("oldStandardName")
                            if old_name in {"SlotDown", "SlotUp"} or old_name in contract_legacy_names:
                                errors.append(issue("registry.extension_slot_migration_names", f"{base}.extensionSlotMigration.oldStandardName", "The migrated old standard name must be distinct from dual-layer and preserved legacy Slot names."))
                        elif operation == "add-dual-layer-slots":
                            if contract_legacy_names != []:
                                errors.append(issue("registry.extension_slot_add_legacy", f"{base}.extensionSlotsContract.legacyPreservedNames", "A newly provisioned dual-layer shared Widget has no legacy NamedSlots to preserve."))
                            if "SlotContent" in widgets_by_name:
                                errors.append(issue("registry.extension_slot_add_old_present", f"{base}.interfaceContract.widgets", "add-dual-layer-slots is only valid for a new shared Widget without SlotContent."))
                        if migration.get("status") == "planned" and slot_status != "required-before-activation":
                            errors.append(issue("registry.extension_slot_migration_status", f"{base}.extensionSlotMigration.status", "A planned dual-slot migration requires extensionSlotsContract.status required-before-activation."))
                        if migration.get("status") == "verified" and slot_status != "verified":
                            errors.append(issue("registry.extension_slot_migration_status", f"{base}.extensionSlotMigration.status", "A verified dual-slot migration requires verified SlotDown/SlotUp contract evidence."))
                        if migration.get("status") == "verified":
                            old_name = migration.get("oldStandardName") if operation == "migrate-existing-standard-slot" else "SlotContent"
                            if isinstance(old_name, str) and old_name in widgets_by_name:
                                errors.append(issue("registry.extension_slot_migration_old_present", f"{base}.interfaceContract.widgets", f"Verified migration requires old standard NamedSlot {old_name!r} to be absent."))
                            if check_linked_files:
                                if registry_path is None:
                                    errors.append(issue("registry.extension_slot_migration_registry_path", f"{base}.extensionSlotMigration.reportPath", "Linked migration evidence validation requires the registry file path."))
                                else:
                                    report_path = resolve_registry_artifact_path(registry_path, str(migration.get("reportPath", "")))
                                    if not report_path.is_file():
                                        errors.append(issue("registry.extension_slot_migration_report", f"{base}.extensionSlotMigration.reportPath", f"Verified migration report does not exist: {report_path}."))
                                    else:
                                        report_bytes = report_path.read_bytes()
                                        actual_report_hash = hashlib.sha256(report_bytes).hexdigest()
                                        if migration.get("evidenceArtifactSha256") != actual_report_hash:
                                            errors.append(issue("registry.extension_slot_migration_report_hash", f"{base}.extensionSlotMigration.evidenceArtifactSha256", f"Migration report hash mismatch; expected {actual_report_hash}."))
                                        try:
                                            report_payload = json.loads(
                                                report_bytes.decode("utf-8"),
                                                parse_constant=lambda token: (_ for _ in ()).throw(
                                                    ValueError(f"Non-finite JSON number is forbidden: {token}")
                                                ),
                                            )
                                        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
                                            errors.append(issue("registry.extension_slot_migration_report_json", f"{base}.extensionSlotMigration.reportPath", f"Migration report is not valid UTF-8 JSON: {error}"))
                                        else:
                                            errors.extend(
                                                validate_verified_slot_operation_report(
                                                    report_payload,
                                                    entry,
                                                    migration,
                                                    schema,
                                                    path=f"{base}.extensionSlotMigration.report",
                                                )
                                            )

        evidence = entry.get("evidence")
        if isinstance(evidence, list):
            seen_evidence_paths: set[str] = set()
            for evidence_index, evidence_item in enumerate(evidence):
                if not isinstance(evidence_item, dict) or not isinstance(evidence_item.get("path"), str):
                    continue
                evidence_path = evidence_item["path"]
                if evidence_path in seen_evidence_paths:
                    errors.append(issue("registry.evidence_duplicate", f"{base}.evidence[{evidence_index}].path", "Evidence paths must be unique within an entry."))
                seen_evidence_paths.add(evidence_path)
        evidence_paths = {
            item.get("path")
            for item in evidence
            if isinstance(item, dict) and isinstance(item.get("path"), str)
        } if isinstance(evidence, list) else set()
        unreal_evidence_by_path = {
            item.get("path"): item
            for item in evidence
            if isinstance(item, dict)
            and item.get("kind") == "unreal-readback"
            and isinstance(item.get("path"), str)
        } if isinstance(evidence, list) else {}
        shared_identity_evidence = unreal_evidence_by_path.get(asset_path)
        shared_identity = shared_identity_evidence.get("assetIdentity") if isinstance(shared_identity_evidence, dict) else None
        if isinstance(shared_identity, dict):
            if shared_identity.get("generatedClassPath") != generated_class_path:
                errors.append(issue("registry.shared_identity_class", f"{base}.generatedClassPath", "Shared GeneratedClass must match its Unreal readback evidence."))
            expected_shared_parent = interface_contract.get("parentClassPath") if isinstance(interface_contract, dict) else None
            if shared_identity.get("parentClassPath") != expected_shared_parent:
                errors.append(issue("registry.shared_identity_parent", f"{base}.interfaceContract.parentClassPath", "Shared Parent Class must match its Unreal readback evidence."))
        else:
            errors.append(issue("registry.shared_identity_evidence", f"{base}.evidence", "A registry entry requires Unreal readback identity evidence at its exact assetPath."))

        generation_modes = entry.get("generationModes")
        mode_ids: set[str] = set()
        mode_by_id: dict[str, dict[str, Any]] = {}
        if isinstance(generation_modes, list):
            for mode_index, mode in enumerate(generation_modes):
                if not isinstance(mode, dict):
                    continue
                mode_path = f"{base}.generationModes[{mode_index}]"
                mode_id = mode.get("mode")
                if isinstance(mode_id, str):
                    if mode_id in mode_ids:
                        errors.append(issue("registry.generation_mode_duplicate", f"{mode_path}.mode", "generationModes must contain each mode at most once."))
                    mode_ids.add(mode_id)
                    mode_by_id.setdefault(mode_id, mode)

                mode_evidence = mode.get("evidencePaths")
                if mode.get("status") == "verified" and not mode_evidence:
                    errors.append(issue("registry.verified_mode_evidence", f"{mode_path}.evidencePaths", "A verified generation mode requires evidence."))
                if isinstance(mode_evidence, list):
                    for evidence_index, evidence_path in enumerate(mode_evidence):
                        if evidence_path not in evidence_paths:
                            errors.append(
                                issue(
                                    "registry.evidence_reference",
                                    f"{mode_path}.evidencePaths[{evidence_index}]",
                                    "Generation-mode evidence must reference an entry evidence path.",
                                )
                            )

                if mode_id == "class-settings-parent-class":
                    if mode.get("classSettingsParentClassPath") != generated_class_path:
                        errors.append(
                            issue(
                                "registry.generation_parent_class",
                                f"{mode_path}.classSettingsParentClassPath",
                                "Class Settings Parent Class must use the shared entry generated class.",
                            )
                        )
                    forbidden_fields = {"nestedWidgetClassPath", "parameterContractStatus", "instanceParameters"}
                    if forbidden_fields.intersection(mode):
                        errors.append(issue("registry.generation_mode_fields", mode_path, "Class Settings inheritance cannot declare nested-instance fields."))

                if mode_id == "widget-tree-instance":
                    if mode.get("nestedWidgetClassPath") != generated_class_path:
                        errors.append(
                            issue(
                                "registry.generation_nested_class",
                                f"{mode_path}.nestedWidgetClassPath",
                                "The shared prototype for WidgetTree nesting must be the entry generated class.",
                            )
                        )
                    if "classSettingsParentClassPath" in mode:
                        errors.append(issue("registry.generation_mode_fields", mode_path, "WidgetTree instance mode cannot declare a Class Settings Parent Class."))
                    parameter_status = mode.get("parameterContractStatus")
                    if parameter_status == "unverified" and mode.get("status") != "unverified":
                        errors.append(issue("registry.parameter_contract_status", f"{mode_path}.status", "An unverified parameter contract makes the nesting mode unverified."))
                    if parameter_status in {"verified", "none"} and mode.get("status") != "verified":
                        errors.append(issue("registry.parameter_contract_status", f"{mode_path}.status", "A verified or parameterless nesting contract must mark the mode verified."))
                    parameters = mode.get("instanceParameters")
                    if parameter_status == "none" and parameters:
                        errors.append(issue("registry.parameter_contract_none", f"{mode_path}.instanceParameters", "A parameterless nesting contract cannot declare instance parameters."))
                    if parameter_status == "verified" and isinstance(parameters, list) and not parameters:
                        errors.append(issue("registry.parameter_contract_empty", f"{mode_path}.instanceParameters", "A verified parameter contract must declare at least one parameter; use 'none' for a verified parameterless contract."))
                    if isinstance(parameters, list):
                        parameter_names: set[str] = set()
                        for parameter_index, parameter in enumerate(parameters):
                            if not isinstance(parameter, dict):
                                continue
                            parameter_name = parameter.get("name")
                            if isinstance(parameter_name, str):
                                if parameter_name in parameter_names:
                                    errors.append(
                                        issue(
                                            "registry.instance_parameter_duplicate",
                                            f"{mode_path}.instanceParameters[{parameter_index}].name",
                                            "Nested instance parameter names must be unique.",
                                        )
                                    )
                                parameter_names.add(parameter_name)
                            if "defaultValue" in parameter and isinstance(parameter.get("valueKind"), str) and not value_matches_kind(parameter.get("defaultValue"), parameter["valueKind"]):
                                errors.append(
                                    issue(
                                        "registry.parameter_default_type",
                                        f"{mode_path}.instanceParameters[{parameter_index}].defaultValue",
                                        "Nested parameter default does not match valueKind.",
                                    )
                                )

        reuse_hash = entry.get("reuseContractSha256")
        expected_reuse_hash = compute_reuse_contract_sha256(entry)
        if reuse_hash != expected_reuse_hash:
            errors.append(
                issue(
                    "registry.reuse_contract_digest",
                    f"{base}.reuseContractSha256",
                    f"Reuse contract hash mismatch; expected {expected_reuse_hash}.",
                )
            )

        consumers = entry.get("knownConsumers")
        if isinstance(consumers, list):
            derived_classes: set[str] = set()
            for candidate in consumers:
                if not isinstance(candidate, dict) or candidate.get("relation") != "class-settings-parent-class":
                    continue
                candidate_path = candidate.get("assetPath")
                candidate_evidence = unreal_evidence_by_path.get(candidate_path)
                candidate_identity = candidate_evidence.get("assetIdentity") if isinstance(candidate_evidence, dict) else None
                if (
                    candidate.get("status") == "verified"
                    and candidate.get("classSettingsParentClassPath") == generated_class_path
                    and isinstance(candidate.get("generatedClassPath"), str)
                    and isinstance(candidate_identity, dict)
                    and candidate_identity.get("generatedClassPath") == candidate.get("generatedClassPath")
                    and candidate_identity.get("parentClassPath") == generated_class_path
                ):
                    derived_classes.add(candidate["generatedClassPath"])
            consumer_identities: set[tuple[Any, ...]] = set()
            for consumer_index, consumer in enumerate(consumers):
                if not isinstance(consumer, dict):
                    continue
                consumer_base = f"{base}.knownConsumers[{consumer_index}]"
                consumer_path = consumer.get("assetPath")
                relation = consumer.get("relation")
                identity = (
                    consumer_path,
                    relation,
                    consumer.get("widgetTreePath") if relation == "widget-tree-instance" else None,
                )
                if isinstance(consumer_path, str):
                    if consumer_path == asset_path:
                        errors.append(issue("registry.consumer_self", f"{consumer_base}.assetPath", "A shared widget cannot consume itself."))
                    if identity in consumer_identities:
                        errors.append(issue("registry.consumer_duplicate", f"{consumer_base}.assetPath", "Known consumer relation identities must be unique per entry."))
                    consumer_identities.add(identity)

                if isinstance(relation, str) and relation not in mode_ids:
                    errors.append(issue("registry.consumer_mode_not_allowed", f"{consumer_base}.relation", "Consumer uses a generation mode not declared by the shared entry."))
                mode_contract = mode_by_id.get(relation) if isinstance(relation, str) else None
                if consumer.get("status") == "verified" and isinstance(mode_contract, dict) and mode_contract.get("status") != "verified":
                    errors.append(issue("registry.consumer_mode_unverified", f"{consumer_base}.relation", "A known executable consumer cannot use an unverified generation mode."))

                identity_evidence = unreal_evidence_by_path.get(consumer_path)
                identity = identity_evidence.get("assetIdentity") if isinstance(identity_evidence, dict) else None
                if consumer.get("status") == "verified":
                    if not isinstance(identity, dict):
                        errors.append(issue("registry.consumer_identity_evidence", f"{consumer_base}.evidencePaths", "A verified consumer requires Unreal readback evidence at its exact assetPath."))
                    else:
                        expected_parent = consumer.get("classSettingsParentClassPath") if relation == "class-settings-parent-class" else None
                        if identity.get("generatedClassPath") != consumer.get("generatedClassPath"):
                            errors.append(issue("registry.consumer_identity_class", f"{consumer_base}.generatedClassPath", "Verified consumer GeneratedClass must match its Unreal readback evidence."))
                        if relation == "class-settings-parent-class" and identity.get("parentClassPath") != expected_parent:
                            errors.append(issue("registry.consumer_identity_parent", f"{consumer_base}.classSettingsParentClassPath", "Verified consumer Parent Class must match its Unreal readback evidence."))

                consumer_evidence = consumer.get("evidencePaths")
                if isinstance(consumer_evidence, list):
                    for evidence_index, evidence_path in enumerate(consumer_evidence):
                        if evidence_path not in evidence_paths:
                            errors.append(issue("registry.evidence_reference", f"{consumer_base}.evidencePaths[{evidence_index}]", "Consumer evidence must reference an entry evidence path."))
                if consumer.get("status") == "verified" and consumer_path not in (consumer_evidence or []):
                    errors.append(issue("registry.consumer_identity_reference", f"{consumer_base}.evidencePaths", "A verified consumer must explicitly reference Unreal readback evidence at its exact assetPath."))

                if relation == "class-settings-parent-class" and consumer.get("classSettingsParentClassPath") != generated_class_path:
                    errors.append(
                        issue(
                            "registry.consumer_parent",
                            f"{consumer_base}.classSettingsParentClassPath",
                            "A Class Settings relation must name the shared entry generated class as Parent Class.",
                        )
                    )

                if relation == "widget-tree-instance":
                    if consumer.get("sharedPrototypeClassPath") != generated_class_path:
                        errors.append(issue("registry.consumer_nested_prototype", f"{consumer_base}.sharedPrototypeClassPath", "Nested consumer must identify the registered shared prototype class."))
                    allowed_nested_classes = {generated_class_path, *derived_classes}
                    if consumer.get("nestedWidgetClassPath") not in allowed_nested_classes:
                        errors.append(issue("registry.consumer_nested_class", f"{consumer_base}.nestedWidgetClassPath", "Nested class must be the shared prototype or a registered direct child of it."))
                    if consumer.get("status") == "verified" and isinstance(identity_evidence, dict):
                        widget_instances = identity_evidence.get("widgetInstances")
                        matching_instance = next(
                            (
                                instance
                                for instance in widget_instances
                                if isinstance(instance, dict)
                                and instance.get("widgetName") == consumer.get("widgetName")
                                and instance.get("widgetTreePath") == consumer.get("widgetTreePath")
                            ),
                            None,
                        ) if isinstance(widget_instances, list) else None
                        if not isinstance(matching_instance, dict) or matching_instance.get("classPath") != consumer.get("nestedWidgetClassPath"):
                            errors.append(issue("registry.consumer_instance_evidence", f"{consumer_base}.widgetTreePath", "Verified nested consumer node and class must match its Unreal readback evidence."))

                    parameters = mode_contract.get("instanceParameters") if isinstance(mode_contract, dict) else None
                    parameter_by_name = {
                        parameter.get("name"): parameter
                        for parameter in parameters
                        if isinstance(parameter, dict) and isinstance(parameter.get("name"), str)
                    } if isinstance(parameters, list) else {}
                    override_names: set[str] = set()
                    overrides = consumer.get("parameterOverrides")
                    if isinstance(overrides, list):
                        for override_index, override in enumerate(overrides):
                            if not isinstance(override, dict):
                                continue
                            override_name = override.get("name")
                            if isinstance(override_name, str):
                                if override_name in override_names:
                                    errors.append(issue("registry.parameter_override_duplicate", f"{consumer_base}.parameterOverrides[{override_index}].name", "Nested parameter overrides must be unique per instance."))
                                override_names.add(override_name)
                                if override_name not in parameter_by_name:
                                    errors.append(issue("registry.parameter_override_unknown", f"{consumer_base}.parameterOverrides[{override_index}].name", "Nested instance override is not declared by the shared parameter contract."))
                                parameter_contract = parameter_by_name.get(override_name)
                                if (
                                    isinstance(parameter_contract, dict)
                                    and override.get("valueSource") in {"user-requirement", "project-default", "literal"}
                                    and isinstance(parameter_contract.get("valueKind"), str)
                                    and not value_matches_kind(override.get("value"), parameter_contract["valueKind"])
                                ):
                                    errors.append(issue("registry.parameter_override_type", f"{consumer_base}.parameterOverrides[{override_index}].value", "Concrete nested parameter override does not match the declared valueKind."))
                    for parameter_name, parameter in parameter_by_name.items():
                        if parameter.get("required") and "defaultValue" not in parameter and parameter_name not in override_names:
                            errors.append(issue("registry.parameter_override_required", f"{consumer_base}.parameterOverrides", f"Required nested parameter {parameter_name!r} has no override or declared default."))

                dynamic_load = consumer.get("dynamicLoad")
                if isinstance(dynamic_load, dict) and dynamic_load.get("fallbackClassPath") != consumer.get("generatedClassPath"):
                    errors.append(issue("registry.consumer_fallback_class", f"{consumer_base}.dynamicLoad.fallbackClassPath", "Dynamic-load fallback class must match the consumer generated class."))

                lua_fields = ("luaModule", "luaSuperModule", "luaInheritance")
                present_lua_fields = {field for field in lua_fields if field in consumer}
                if present_lua_fields and len(present_lua_fields) != len(lua_fields):
                    errors.append(issue("registry.consumer_lua_contract", consumer_base, "Lua relation fields must be supplied together or omitted together."))
                shared_lua_module = interface_contract.get("luaModule") if isinstance(interface_contract, dict) else None
                if consumer.get("luaInheritance") == "inherits-shared-lua" and consumer.get("luaSuperModule") != shared_lua_module:
                    errors.append(issue("registry.consumer_lua_super", f"{consumer_base}.luaSuperModule", "inherits-shared-lua requires the shared entry Lua module as the explicit super module."))
                if consumer.get("luaInheritance") == "independent" and consumer.get("luaSuperModule") == shared_lua_module:
                    errors.append(issue("registry.consumer_lua_relation", f"{consumer_base}.luaInheritance", "A consumer using the shared Lua module as its explicit super is not independent."))

                slot_extension = consumer.get("slotExtension")
                if isinstance(slot_extension, dict):
                    if slot_extension.get("slotName") != entry.get("extensionSlotContract", {}).get("widgetName"):
                        errors.append(issue("registry.consumer_slot_name", f"{consumer_base}.slotExtension.slotName", "Inherited content must target the registered shared extension Slot."))
                    panel_class = slot_extension.get("rootPanelClassPath")
                    if not isinstance(panel_class, str) or not panel_class.startswith("/Script/") or not panel_class.endswith(("CanvasPanel", "Overlay", "HorizontalBox", "VerticalBox", "GridPanel", "UniformGridPanel", "WrapBox")):
                        errors.append(issue("registry.consumer_slot_panel", f"{consumer_base}.slotExtension.rootPanelClassPath", "Inherited Slot content must begin with a supported semantic Panel class."))
                    if slot_extension.get("layoutMode") == "special-adaptation" and not slot_extension.get("specialAdaptationEvidencePaths"):
                        errors.append(issue("registry.consumer_slot_adaptation_evidence", f"{consumer_base}.slotExtension", "Special Slot adaptation requires explicit evidence paths."))

                slot_extensions = consumer.get("slotExtensions")
                if isinstance(slot_extensions, list):
                    extension_slots_contract = entry.get("extensionSlotsContract") if isinstance(entry.get("extensionSlotsContract"), dict) else {}
                    if extension_slots_contract.get("status") != "verified":
                        errors.append(issue("registry.consumer_slots_unverified", f"{consumer_base}.slotExtensions", "Dual inherited Slot evidence cannot be executable before SlotDown/SlotUp are verified on the shared parent."))
                    panel_names: set[str] = set()
                    panel_paths: set[str] = set()
                    for slot_index, slot_record in enumerate(slot_extensions):
                        if not isinstance(slot_record, dict) or slot_record.get("contentMode") != "panel":
                            continue
                        slot_path = f"{consumer_base}.slotExtensions[{slot_index}]"
                        panel_class = slot_record.get("rootPanelClassPath")
                        if not isinstance(panel_class, str) or not panel_class.startswith("/Script/") or not panel_class.endswith(("CanvasPanel", "Overlay", "HorizontalBox", "VerticalBox", "GridPanel", "UniformGridPanel", "WrapBox")):
                            errors.append(issue("registry.consumer_slot_panel", f"{slot_path}.rootPanelClassPath", "Inherited Slot content must begin with a supported semantic Panel class."))
                        if slot_record.get("layoutMode") == "special-adaptation" and not slot_record.get("specialAdaptationEvidencePaths"):
                            errors.append(issue("registry.consumer_slot_adaptation_evidence", slot_path, "Special Slot adaptation requires explicit evidence paths."))
                        panel_name = slot_record.get("rootPanelName")
                        panel_tree_path = slot_record.get("rootPanelTreePath")
                        if isinstance(panel_name, str):
                            if panel_name in panel_names:
                                errors.append(issue("registry.consumer_slot_panel_identity", f"{slot_path}.rootPanelName", "Each used Slot requires a distinct direct semantic Panel identity."))
                            panel_names.add(panel_name)
                        if isinstance(panel_tree_path, str):
                            if panel_tree_path in panel_paths:
                                errors.append(issue("registry.consumer_slot_panel_identity", f"{slot_path}.rootPanelTreePath", "Each used Slot requires a distinct direct semantic Panel tree path."))
                            panel_paths.add(panel_tree_path)

        scope = entry.get("scope")
        owner_system_folder = entry.get("ownerSystemFolder")
        if scope == "project-common" and owner_system_folder is not None:
            errors.append(issue("registry.scope_owner", f"{base}.ownerSystemFolder", "project-common entries must not claim a system owner folder."))
        if scope == "system" and not isinstance(owner_system_folder, str):
            errors.append(issue("registry.scope_owner", f"{base}.ownerSystemFolder", "system entries require ownerSystemFolder."))

        if entry.get("status") == "active" and isinstance(entry.get("extensionSlotContract"), dict) and entry["extensionSlotContract"].get("status") != "verified":
            errors.append(issue("registry.extension_slot_activation", f"{base}.status", "An active shared entry must have a verified default extension Slot."))
        if entry.get("status") == "active" and isinstance(entry.get("extensionSlotsContract"), dict) and entry["extensionSlotsContract"].get("status") != "verified":
            errors.append(issue("registry.extension_slots_activation", f"{base}.status", "An active SharedWidgetRegistry 0.4 entry must have verified SlotDown/SlotUp extension evidence."))
        migration = entry.get("extensionSlotMigration")
        if isinstance(migration, dict) and migration.get("status") == "planned" and entry.get("status") != "candidate":
            errors.append(issue("registry.extension_slot_migration_entry_status", f"{base}.status", "A planned dual-layer Slot operation must remain a non-executable candidate."))

        if entry.get("status") == "active" and entry.get("confirmation") != {
            "actorType": "user",
            "source": "direct-user-message",
            "confirmedAt": entry.get("confirmation", {}).get("confirmedAt") if isinstance(entry.get("confirmation"), dict) else None,
        }:
            errors.append(issue("registry.confirmation", f"{base}.confirmation", "Active entries require a direct user confirmation."))

    return {"valid": not errors, "errors": errors, "warnings": warnings}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("registry", type=Path, nargs="?", default=DEFAULT_REGISTRY)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    args = parser.parse_args()
    try:
        output = validate_registry(
            load_json(args.registry),
            load_json(args.schema),
            registry_path=args.registry.resolve(),
            check_linked_files=True,
        )
    except (OSError, json.JSONDecodeError, ValueError) as error:
        output = {"valid": False, "errors": [issue("io.read", "$", str(error))], "warnings": []}
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if output["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
