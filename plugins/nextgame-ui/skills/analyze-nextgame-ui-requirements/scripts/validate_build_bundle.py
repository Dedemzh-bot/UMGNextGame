#!/usr/bin/env python3
"""Validate a NextGame UIBuildBundle 0.1/0.2/0.3 and its linked artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from _contract_common import ASSETS_ROOT, issue, load_json, resolve_contract_path, result, sha256_file, validate_schema_instance
from validate_requirement_spec import (
    DEFAULT_SCHEMA as REQUIREMENT_SCHEMA,
    build_requirement_index,
    required_design_size_modes,
    required_user_interaction_buttons,
    validate_requirement_spec,
)


PLUGIN_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_SCRIPTS = PLUGIN_ROOT / "scripts"
if str(PLUGIN_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(PLUGIN_SCRIPTS))

from validate_shared_widget_registry import (  # noqa: E402
    DEFAULT_REGISTRY as SHARED_REGISTRY,
    DEFAULT_SCHEMA as SHARED_REGISTRY_SCHEMA,
    load_json as load_shared_registry_json,
    value_matches_kind,
    validate_registry as validate_shared_registry,
)
from validate_shared_widget_bootstrap import (  # noqa: E402
    DEFAULT_SCHEMA as SHARED_BOOTSTRAP_SCHEMA,
    load_json as load_shared_bootstrap_json,
    validate_bootstrap_snapshot,
)


DEFAULT_SCHEMA = ASSETS_ROOT / "ui-build-bundle.schema.json"
AUTHORITATIVE_SHARED_REGISTRY = SHARED_REGISTRY.resolve()
AUTHORITATIVE_SHARED_REGISTRY_SCHEMA = SHARED_REGISTRY_SCHEMA.resolve()
RECT_TOLERANCE = 0.001


def _image_requirement_realizations(
    element_id: str,
    mappings: list[dict[str, Any]],
    reuse_relations: list[dict[str, Any]],
    layout_node_records_by_asset: dict[str, dict[str, dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return concrete image-node and nested-shared-widget realizations for one image requirement."""

    visual_mappings = [
        mapping
        for mapping in mappings
        if element_id
        in (mapping.get("requirementRefs", []) if isinstance(mapping.get("requirementRefs"), list) else [])
        and layout_node_records_by_asset.get(mapping.get("assetId"), {})
        .get(mapping.get("layoutNodeId"), {})
        .get("role")
        == "visual.image"
    ]
    nested_reuse_relations = [
        relation
        for relation in reuse_relations
        if relation.get("type") == "widget-tree-instance"
        and element_id
        in (relation.get("requirementRefs", []) if isinstance(relation.get("requirementRefs"), list) else [])
    ]
    return visual_mappings, nested_reuse_relations


def _responsive_requirement_realizations(
    intent_id: str,
    target_requirement_id: str,
    mappings: list[dict[str, Any]],
    reuse_relations: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return nodes or nested controls that jointly realize one responsive intent and its target."""

    required_refs = {intent_id, target_requirement_id}
    node_mappings = [
        mapping
        for mapping in mappings
        if required_refs.issubset(
            set(mapping.get("requirementRefs", []))
            if isinstance(mapping.get("requirementRefs"), list)
            else set()
        )
    ]
    nested_relations = [
        relation
        for relation in reuse_relations
        if relation.get("type") == "widget-tree-instance"
        and required_refs.issubset(
            set(relation.get("requirementRefs", []))
            if isinstance(relation.get("requirementRefs"), list)
            else set()
        )
    ]
    return node_mappings, nested_relations


def _panel_slot_realizations(
    element_id: str,
    expected_field: str,
    mappings: list[dict[str, Any]],
    reuse_relations: list[dict[str, Any]],
    layout_node_records_by_asset: dict[str, dict[str, dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    node_mappings = []
    for mapping in mappings:
        requirement_refs = mapping.get("requirementRefs", []) if isinstance(mapping.get("requirementRefs"), list) else []
        if element_id not in requirement_refs:
            continue
        node = layout_node_records_by_asset.get(mapping.get("assetId"), {}).get(mapping.get("layoutNodeId"), {})
        if isinstance(node.get(expected_field), dict):
            node_mappings.append(mapping)
    nested_relations = [
        relation
        for relation in reuse_relations
        if relation.get("type") == "widget-tree-instance"
        and element_id
        in (relation.get("requirementRefs", []) if isinstance(relation.get("requirementRefs"), list) else [])
    ]
    return node_mappings, nested_relations


def _node_is_descendant_or_self(
    node_records: dict[str, dict[str, Any]],
    node_id: Any,
    ancestor_id: Any,
) -> bool:
    """Return whether a layout node is at or below another node in the same asset."""

    if not isinstance(node_id, str) or not isinstance(ancestor_id, str):
        return False
    current = node_id
    seen: set[str] = set()
    while current in node_records and current not in seen:
        if current == ancestor_id:
            return True
        seen.add(current)
        parent = node_records[current].get("parent")
        if not isinstance(parent, str):
            break
        current = parent
    return False


def _relation_parent_node_id(
    relation: dict[str, Any],
    layout_node_records_by_asset: dict[str, dict[str, dict[str, Any]]],
) -> str | None:
    placement = relation.get("placementContract") if isinstance(relation.get("placementContract"), dict) else {}
    slot = placement.get("slot") if isinstance(placement.get("slot"), dict) else {}
    parent_name = slot.get("parentWidgetName")
    parent_tree_path = slot.get("parentTreePath")
    target_asset_path = relation.get("targetAssetPath")
    if (
        not isinstance(parent_name, str)
        or not isinstance(parent_tree_path, str)
        or not isinstance(target_asset_path, str)
        or parent_tree_path != _widget_tree_path(target_asset_path, parent_name)
    ):
        return None
    target_records = layout_node_records_by_asset.get(str(relation.get("targetAssetId")), {})
    matches = [node_id for node_id, node in target_records.items() if node.get("name") == parent_name]
    return matches[0] if len(matches) == 1 else None


def _asset_containment_edges(
    bundle: dict[str, Any],
    reuse_relations: list[dict[str, Any]],
    layout_node_records_by_asset: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, list[tuple[str, str | None]]]:
    """Map a child asset to verified host assets and the host-side containment anchor."""

    edges: dict[str, list[tuple[str, str | None]]] = {}
    for operation in bundle.get("crossAssetOperations", []):
        if not isinstance(operation, dict) or operation.get("type") not in {
            "child-widget-integration",
            "entry-widget-class",
        }:
            continue
        source_id = operation.get("sourceAssetId")
        target_id = operation.get("targetAssetId")
        if isinstance(source_id, str) and isinstance(target_id, str):
            anchor = operation.get("targetLayoutNodeId")
            edges.setdefault(source_id, []).append((target_id, anchor if isinstance(anchor, str) else None))
    for relation in reuse_relations:
        if relation.get("type") != "widget-tree-instance":
            continue
        source_id = relation.get("sourceAssetId")
        target_id = relation.get("targetAssetId")
        if isinstance(source_id, str) and isinstance(target_id, str):
            edges.setdefault(source_id, []).append(
                (target_id, _relation_parent_node_id(relation, layout_node_records_by_asset))
            )
    return edges


def _final_containment_anchors(
    source_asset_id: str,
    target_asset_id: str,
    edges: dict[str, list[tuple[str, str | None]]],
    seen: set[str] | None = None,
) -> list[str | None]:
    """Return final host-side anchors for every discovered source-to-target asset path."""

    visited = set() if seen is None else set(seen)
    if source_asset_id in visited:
        return []
    visited.add(source_asset_id)
    anchors: list[str | None] = []
    for next_asset_id, anchor_node_id in edges.get(source_asset_id, []):
        if next_asset_id == target_asset_id:
            anchors.append(anchor_node_id)
        else:
            anchors.extend(_final_containment_anchors(next_asset_id, target_asset_id, edges, visited))
    return anchors


def _image_realization_is_within_owner(
    image_realization: tuple[str, dict[str, Any]],
    owner_realization: tuple[str, dict[str, Any]],
    *,
    bundle: dict[str, Any],
    reuse_relations: list[dict[str, Any]],
    layout_node_records_by_asset: dict[str, dict[str, dict[str, Any]]],
) -> bool:
    """Prove that an inherited image realization is structurally inside its declared owner."""

    image_kind, image_record = image_realization
    owner_kind, owner_record = owner_realization
    edges = _asset_containment_edges(bundle, reuse_relations, layout_node_records_by_asset)

    image_asset_id = (
        image_record.get("assetId")
        if image_kind == "mapping"
        else image_record.get("targetAssetId")
    )
    if not isinstance(image_asset_id, str):
        return False

    if owner_kind == "relation":
        if image_kind == "relation" and image_record.get("id") == owner_record.get("id"):
            return True
        owner_source_id = owner_record.get("sourceAssetId")
        if not isinstance(owner_source_id, str):
            return False
        if image_asset_id == owner_source_id:
            return True
        return bool(_final_containment_anchors(image_asset_id, owner_source_id, edges))

    owner_asset_id = owner_record.get("assetId")
    owner_node_id = owner_record.get("layoutNodeId")
    if not isinstance(owner_asset_id, str) or not isinstance(owner_node_id, str):
        return False
    owner_records = layout_node_records_by_asset.get(owner_asset_id, {})

    if image_asset_id == owner_asset_id:
        if image_kind == "mapping":
            return _node_is_descendant_or_self(
                owner_records,
                image_record.get("layoutNodeId"),
                owner_node_id,
            )
        relation_anchor = _relation_parent_node_id(image_record, layout_node_records_by_asset)
        return _node_is_descendant_or_self(owner_records, relation_anchor, owner_node_id)

    final_anchors = _final_containment_anchors(image_asset_id, owner_asset_id, edges)
    return bool(final_anchors) and all(
        _node_is_descendant_or_self(owner_records, anchor, owner_node_id)
        for anchor in final_anchors
    )


def _relation_slot_from_panel_intent(slot_intent: dict[str, Any], parent_layout_role: Any) -> dict[str, Any]:
    return {
        "containerType": {
            "container.vertical": "VerticalBox",
            "container.horizontal": "HorizontalBox",
            "container.game-scroll": "GameScrollBox",
        }.get(parent_layout_role),
        **{
            key: value
            for key, value in slot_intent.items()
            if key not in {"slotType", "sizingBasis", "reason"}
        },
    }


def _is_allowed_registry_source(registry_path: Path, *, bundle_path: Path, declared_sha256: Any) -> bool:
    """Allow the plugin registry or a content-addressed snapshot local to this Bundle."""

    resolved_registry = registry_path.resolve()
    if resolved_registry == AUTHORITATIVE_SHARED_REGISTRY:
        return True
    if (
        not isinstance(declared_sha256, str)
        or len(declared_sha256) != 64
        or any(character not in "0123456789abcdef" for character in declared_sha256)
    ):
        return False
    snapshot_root = (bundle_path.resolve().parent / "registry-snapshots").resolve()
    return (
        resolved_registry.parent == snapshot_root
        and resolved_registry.name == f"shared-widget-registry.{declared_sha256}.json"
    )


def _is_allowed_bootstrap_source(snapshot_path: Path, *, bundle_path: Path, declared_sha256: Any) -> bool:
    """Allow only a content-addressed bootstrap snapshot local to this Bundle."""

    if (
        not isinstance(declared_sha256, str)
        or len(declared_sha256) != 64
        or any(character not in "0123456789abcdef" for character in declared_sha256)
    ):
        return False
    resolved_snapshot = snapshot_path.resolve()
    snapshot_root = (bundle_path.resolve().parent / "registry-snapshots").resolve()
    return (
        resolved_snapshot.parent == snapshot_root
        and resolved_snapshot.name == f"shared-widget-bootstrap.{declared_sha256}.json"
    )


def _asset_object_path(asset_path: str) -> str:
    return asset_path.rstrip("/")


def _widget_tree_path(asset_path: str, widget_name: str) -> str:
    asset_name = asset_path.rstrip("/").rsplit("/", 1)[-1]
    return f"{asset_path}.{asset_name}:WidgetTree.{widget_name}"


def _layout_asset_path(layout: dict[str, Any]) -> str | None:
    asset = layout.get("asset")
    if not isinstance(asset, dict):
        return None
    folder = asset.get("folder")
    name = asset.get("name")
    if not isinstance(folder, str) or not isinstance(name, str):
        return None
    return f"{folder.rstrip('/')}/{name}"


def _rect_delta(left: Any, right: Any) -> float | None:
    if not (
        isinstance(left, list)
        and isinstance(right, list)
        and len(left) == len(right) == 4
        and all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in left + right)
    ):
        return None
    return max(abs(float(a) - float(b)) for a, b in zip(left, right))


def _expected_host_size(rect: Any, reference_size: Any) -> list[int] | None:
    if not (
        isinstance(rect, list)
        and len(rect) == 4
        and isinstance(reference_size, list)
        and len(reference_size) == 2
        and all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in rect + reference_size)
    ):
        return None
    return [round(float(rect[2]) * float(reference_size[0])), round(float(rect[3]) * float(reference_size[1]))]


def _has_slot_stretch(node: dict[str, Any], axis: str) -> bool:
    slot = node.get("slotLayout") if isinstance(node.get("slotLayout"), dict) else {}
    anchors = slot.get("anchors") if isinstance(slot.get("anchors"), dict) else {}
    minimum = anchors.get("minimum") if isinstance(anchors.get("minimum"), list) else []
    maximum = anchors.get("maximum") if isinstance(anchors.get("maximum"), list) else []
    offsets = slot.get("offsets") if isinstance(slot.get("offsets"), dict) else {}
    if len(minimum) != 2 or len(maximum) != 2:
        return False
    if axis == "horizontal":
        return minimum[0] == 0 and maximum[0] == 1 and offsets.get("left") == 0 and offsets.get("right") == 0
    return minimum[1] == 0 and maximum[1] == 1 and offsets.get("top") == 0 and offsets.get("bottom") == 0


def _has_adaptive_stretch(node: dict[str, Any], axis: str) -> bool:
    adaptive = node.get("adaptiveLayout") if isinstance(node.get("adaptiveLayout"), dict) else {}
    return adaptive.get("horizontal" if axis == "horizontal" else "vertical") == "stretch"


def _supports_stretch_axis(node: dict[str, Any], axis: str) -> bool:
    return _has_slot_stretch(node, axis) or _has_adaptive_stretch(node, axis)


def _positive_pair(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and all(
            isinstance(item, (int, float))
            and not isinstance(item, bool)
            and float(item) > 0
            for item in value
        )
    )


def _root_direct_nodes(layout: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = [node for node in layout.get("nodes", []) if isinstance(node, dict)]
    root_ids = {
        node.get("id")
        for node in nodes
        if node.get("parent") is None and isinstance(node.get("id"), str)
    }
    return [node for node in nodes if node.get("parent") in root_ids]


def _has_fixed_root_direct_desired_size(layout: dict[str, Any]) -> bool:
    """Prove non-zero Desired Size through one fixed Canvas-style root child Slot."""

    for node in _root_direct_nodes(layout):
        slot = node.get("slotLayout") if isinstance(node.get("slotLayout"), dict) else {}
        anchors = slot.get("anchors") if isinstance(slot.get("anchors"), dict) else {}
        minimum = anchors.get("minimum")
        maximum = anchors.get("maximum")
        offsets = slot.get("offsets") if isinstance(slot.get("offsets"), dict) else {}
        point_anchors = (
            isinstance(minimum, list)
            and isinstance(maximum, list)
            and len(minimum) == len(maximum) == 2
            and all(
                isinstance(value, (int, float)) and not isinstance(value, bool)
                for value in minimum + maximum
            )
            and minimum == maximum
        )
        positive_size = all(
            isinstance(offsets.get(key), (int, float))
            and not isinstance(offsets.get(key), bool)
            and float(offsets[key]) > 0
            for key in ("right", "bottom")
        )
        if slot.get("autoSize") is False and point_anchors and positive_size:
            return True
    return False


def _root_direct_content_driven_size_proofs(layout: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        proof
        for node in _root_direct_nodes(layout)
        if isinstance((proof := node.get("contentDrivenSize")), dict)
        and proof.get("verified") is True
        and _positive_pair(proof.get("measuredDesiredSize"))
        and isinstance(proof.get("evidenceId"), str)
    ]


SUPPORTED_BUNDLE_VERSIONS = {"0.1", "0.2", "0.3"}
REUSE_BUNDLE_VERSIONS = {"0.2", "0.3"}
SUPPORTED_SEMANTIC_PANEL_CLASSES = {
    "/Script/UMG.CanvasPanel",
    "/Script/UMG.Overlay",
    "/Script/UMG.HorizontalBox",
    "/Script/UMG.VerticalBox",
    "/Script/UMG.GridPanel",
    "/Script/UMG.UniformGridPanel",
    "/Script/UMG.WrapBox",
}


def _validate_shared_registry_binding(
    registry_binding: dict[str, Any],
    *,
    source_asset: dict[str, Any] | None,
    bundle_version: str,
    bundle_path: Path,
    path: str,
    binding_out: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Bind Bundle reuse intent to the actual validated shared registry snapshot."""

    errors: list[dict[str, str]] = []
    raw_registry_path = registry_binding.get("registryPath")
    if not isinstance(raw_registry_path, str) or not raw_registry_path:
        return [issue("reuse.registry_path", f"{path}.registryPath", "Shared registry path is required.")]
    registry_path = resolve_contract_path(bundle_path, raw_registry_path)
    if not registry_path.is_file():
        return [issue("reuse.registry_path", f"{path}.registryPath", f"Shared registry does not exist: {registry_path}.")]
    if not _is_allowed_registry_source(
        registry_path,
        bundle_path=bundle_path,
        declared_sha256=registry_binding.get("registrySha256"),
    ):
        return [
            issue(
                "reuse.registry_authority",
                f"{path}.registryPath",
                "Executable reuse must bind the Registry shipped by this nextgame-ui plugin or a Bundle-local registry-snapshots/shared-widget-registry.<sha256>.json immutable snapshot; arbitrary sibling registries and schemas are not trusted.",
            )
        ]
    actual_hash = sha256_file(registry_path)
    if registry_binding.get("registrySha256") != actual_hash:
        errors.append(issue("reuse.registry_sha256", f"{path}.registrySha256", f"Shared registry hash mismatch; expected {actual_hash}."))
    try:
        registry = load_shared_registry_json(registry_path)
        registry_schema = load_shared_registry_json(AUTHORITATIVE_SHARED_REGISTRY_SCHEMA)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        errors.append(issue("reuse.registry_read", f"{path}.registryPath", str(error)))
        return errors

    registry_report = validate_shared_registry(
        registry,
        registry_schema,
        registry_path=registry_path,
        check_linked_files=True,
    )
    if not registry_report.get("valid"):
        codes = sorted({item.get("code") for item in registry_report.get("errors", []) if isinstance(item, dict)})
        errors.append(issue("reuse.registry_invalid", f"{path}.registryPath", f"Shared registry validation failed: {codes}."))

    expected_registry_version = "0.3" if bundle_version == "0.2" else "0.4"
    declared_version = registry_binding.get("registryVersion") if bundle_version == "0.3" else expected_registry_version
    for key, actual, declared in (
        ("registryId", registry.get("registryId") if isinstance(registry, dict) else None, registry_binding.get("registryId")),
        ("registryVersion", registry.get("version") if isinstance(registry, dict) else None, declared_version),
        ("registryRevision", registry.get("registryRevision") if isinstance(registry, dict) else None, registry_binding.get("registryRevision")),
    ):
        if actual != declared:
            errors.append(issue("reuse.registry_identity", f"{path}.{key}", f"Bundle {key} does not match the actual shared registry snapshot."))

    entry_id = registry_binding.get("entryId")
    entries = registry.get("entries", []) if isinstance(registry, dict) else []
    matching_entries = [entry for entry in entries if isinstance(entry, dict) and entry.get("id") == entry_id]
    if len(matching_entries) != 1:
        errors.append(issue("reuse.registry_entry", f"{path}.entryId", "Bundle entryId must resolve to exactly one actual shared registry entry."))
        return errors
    entry = matching_entries[0]
    if binding_out is not None:
        binding_out.update({"registryPath": registry_path, "registry": registry, "entry": entry})
    extension_contract_key = "extensionSlotContract" if bundle_version == "0.2" else "extensionSlotsContract"
    extension_status_key = "extensionSlotStatus" if bundle_version == "0.2" else "extensionSlotsStatus"
    extension_contract = entry.get(extension_contract_key) if isinstance(entry.get(extension_contract_key), dict) else {}
    for key, actual, declared in (
        ("entryStatus", entry.get("status"), registry_binding.get("entryStatus")),
        (extension_status_key, extension_contract.get("status"), registry_binding.get(extension_status_key)),
        ("interfaceSha256", entry.get("interfaceSha256"), registry_binding.get("interfaceSha256")),
        ("reuseContractSha256", entry.get("reuseContractSha256"), registry_binding.get("reuseContractSha256")),
    ):
        if actual != declared:
            errors.append(issue("reuse.registry_entry_identity", f"{path}.{key}", f"Bundle {key} does not match the actual shared registry entry."))
    if isinstance(source_asset, dict):
        if entry.get("assetPath") != source_asset.get("assetPath"):
            errors.append(issue("reuse.registry_asset", f"{path}.entryId", "Shared registry entry assetPath does not match the Bundle source asset."))
        source_path = source_asset.get("assetPath")
        if isinstance(source_path, str):
            expected_generated_class = f"{source_path}.{source_path.rsplit('/', 1)[-1]}_C"
            if entry.get("generatedClassPath") != expected_generated_class:
                errors.append(issue("reuse.registry_class", f"{path}.entryId", "Shared registry GeneratedClass does not match the Bundle source asset."))
    if binding_out is not None:
        binding_out["valid"] = not errors
    return errors


def _validate_shared_bootstrap_binding(
    bootstrap_binding: dict[str, Any],
    *,
    source_asset: dict[str, Any] | None,
    bundle_path: Path,
    path: str,
    binding_out: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Bind a new project-common layout asset to a non-executable bootstrap plan."""

    errors: list[dict[str, str]] = []
    raw_snapshot_path = bootstrap_binding.get("snapshotPath")
    if not isinstance(raw_snapshot_path, str) or not raw_snapshot_path:
        return [issue("reuse.bootstrap_path", f"{path}.snapshotPath", "Bootstrap snapshot path is required.")]
    snapshot_path = resolve_contract_path(bundle_path, raw_snapshot_path)
    if not snapshot_path.is_file():
        return [issue("reuse.bootstrap_path", f"{path}.snapshotPath", f"Bootstrap snapshot does not exist: {snapshot_path}.")]
    if not _is_allowed_bootstrap_source(
        snapshot_path,
        bundle_path=bundle_path,
        declared_sha256=bootstrap_binding.get("snapshotSha256"),
    ):
        return [
            issue(
                "reuse.bootstrap_authority",
                f"{path}.snapshotPath",
                "A planned bootstrap must bind Bundle-local registry-snapshots/shared-widget-bootstrap.<sha256>.json; arbitrary files are not trusted.",
            )
        ]

    actual_snapshot_hash = sha256_file(snapshot_path)
    if bootstrap_binding.get("snapshotSha256") != actual_snapshot_hash:
        errors.append(issue("reuse.bootstrap_sha256", f"{path}.snapshotSha256", f"Bootstrap snapshot hash mismatch; expected {actual_snapshot_hash}."))
    try:
        snapshot = load_shared_bootstrap_json(snapshot_path)
        bootstrap_schema = load_shared_bootstrap_json(SHARED_BOOTSTRAP_SCHEMA)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        errors.append(issue("reuse.bootstrap_read", f"{path}.snapshotPath", str(error)))
        return errors

    snapshot_report = validate_bootstrap_snapshot(snapshot, bootstrap_schema)
    if not snapshot_report.get("valid"):
        codes = sorted({item.get("code") for item in snapshot_report.get("errors", []) if isinstance(item, dict)})
        errors.append(issue("reuse.bootstrap_invalid", f"{path}.snapshotPath", f"Bootstrap snapshot validation failed: {codes}."))

    for key, actual, declared in (
        ("snapshotId", snapshot.get("snapshotId") if isinstance(snapshot, dict) else None, bootstrap_binding.get("snapshotId")),
        ("snapshotVersion", snapshot.get("version") if isinstance(snapshot, dict) else None, bootstrap_binding.get("snapshotVersion")),
        ("snapshotRevision", snapshot.get("snapshotRevision") if isinstance(snapshot, dict) else None, bootstrap_binding.get("snapshotRevision")),
    ):
        if actual != declared:
            errors.append(issue("reuse.bootstrap_identity", f"{path}.{key}", f"Bundle {key} does not match the actual bootstrap snapshot."))

    entries = snapshot.get("entries", []) if isinstance(snapshot, dict) else []
    entry_id = bootstrap_binding.get("entryId")
    matching_entries = [entry for entry in entries if isinstance(entry, dict) and entry.get("id") == entry_id]
    if len(matching_entries) != 1:
        errors.append(issue("reuse.bootstrap_entry", f"{path}.entryId", "Bundle entryId must resolve to exactly one bootstrap entry."))
        return errors
    entry = matching_entries[0]
    for key, actual, declared in (
        ("entryStatus", entry.get("status"), bootstrap_binding.get("entryStatus")),
        ("extensionSlotsStatus", entry.get("extensionSlotsStatus"), bootstrap_binding.get("extensionSlotsStatus")),
        ("bootstrapContractSha256", entry.get("bootstrapContractSha256"), bootstrap_binding.get("bootstrapContractSha256")),
    ):
        if actual != declared:
            errors.append(issue("reuse.bootstrap_entry_identity", f"{path}.{key}", f"Bundle {key} does not match the bootstrap entry."))

    try:
        actual_registry = load_shared_registry_json(AUTHORITATIVE_SHARED_REGISTRY)
        registry_schema = load_shared_registry_json(AUTHORITATIVE_SHARED_REGISTRY_SCHEMA)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        errors.append(issue("reuse.bootstrap_base_registry", f"{path}.snapshotPath", str(error)))
        return errors
    registry_report = validate_shared_registry(
        actual_registry,
        registry_schema,
        registry_path=AUTHORITATIVE_SHARED_REGISTRY,
        check_linked_files=True,
    )
    if not registry_report.get("valid"):
        errors.append(issue("reuse.bootstrap_base_registry", f"{path}.snapshotPath", "The authoritative base Registry is invalid."))
    base_registry = snapshot.get("baseRegistry") if isinstance(snapshot.get("baseRegistry"), dict) else {}
    for key, actual, declared in (
        ("registryId", actual_registry.get("registryId"), base_registry.get("registryId")),
        ("registryVersion", actual_registry.get("version"), base_registry.get("registryVersion")),
        ("registryRevision", actual_registry.get("registryRevision"), base_registry.get("registryRevision")),
        ("registrySha256", sha256_file(AUTHORITATIVE_SHARED_REGISTRY), base_registry.get("registrySha256")),
    ):
        if actual != declared:
            errors.append(issue("reuse.bootstrap_base_registry", f"{path}.snapshotPath", f"Bootstrap base {key} is stale; expected {actual}."))
    registry_entries = actual_registry.get("entries") if isinstance(actual_registry.get("entries"), list) else []
    if any(
        isinstance(candidate, dict)
        and (candidate.get("id") == entry.get("id") or candidate.get("assetPath") == entry.get("assetPath"))
        for candidate in registry_entries
    ):
        errors.append(issue("reuse.bootstrap_existing_registry_entry", f"{path}.entryId", "planned-bootstrap cannot replace an existing Registry entry."))

    if not isinstance(source_asset, dict):
        errors.append(issue("reuse.bootstrap_asset", f"{path}.entryId", "Bootstrap source asset is missing."))
    else:
        for key, actual, declared in (
            ("assetPlanId", source_asset.get("assetPlanId"), entry.get("assetPlanId")),
            ("assetPath", source_asset.get("assetPath"), entry.get("assetPath")),
            ("assetKind", source_asset.get("assetKind"), entry.get("assetKind")),
            ("layoutSpecPath", source_asset.get("layoutSpecPath"), entry.get("layoutSpecPath")),
            ("layoutSpecSha256", source_asset.get("layoutSpecSha256"), entry.get("layoutSpecSha256")),
            ("referenceSize", source_asset.get("referenceSize"), entry.get("expectedReferenceSize")),
        ):
            if actual != declared:
                errors.append(issue("reuse.bootstrap_asset", f"{path}.entryId", f"Bootstrap {key} does not match the Bundle source asset."))
        if source_asset.get("representationKind") != "layout-spec":
            errors.append(issue("reuse.bootstrap_representation", f"{path}.entryId", "planned-bootstrap is restricted to a new layout-spec asset."))

        project_root = next(
            (parent for parent in (bundle_path.resolve().parent, *bundle_path.resolve().parents) if (parent / "NextGame.uproject").is_file()),
            None,
        )
        source_asset_path = source_asset.get("assetPath")
        if project_root is None:
            errors.append(issue("reuse.bootstrap_project_root", f"{path}.entryId", "Cannot prove a new /Game asset without the owning project root."))
        elif isinstance(source_asset_path, str) and source_asset_path.startswith("/Game/"):
            package_file = project_root / "Content" / f"{source_asset_path[len('/Game/'):]}.uasset"
            if package_file.is_file():
                errors.append(issue("reuse.bootstrap_existing_asset", f"{path}.entryId", f"planned-bootstrap is only for a new asset, but the package already exists: {package_file}."))

        layout_spec_path = source_asset.get("layoutSpecPath")
        if isinstance(layout_spec_path, str) and layout_spec_path:
            resolved_layout = resolve_contract_path(bundle_path, layout_spec_path)
            if not resolved_layout.is_file():
                errors.append(issue("reuse.bootstrap_layout", f"{path}.entryId", f"Bootstrap layout does not exist: {resolved_layout}."))
            else:
                actual_layout_hash = sha256_file(resolved_layout)
                if actual_layout_hash != entry.get("layoutSpecSha256"):
                    errors.append(issue("reuse.bootstrap_layout", f"{path}.entryId", f"Bootstrap layout hash mismatch; expected {actual_layout_hash}."))
                try:
                    layout = load_json(resolved_layout)
                except (OSError, json.JSONDecodeError, ValueError) as error:
                    errors.append(issue("reuse.bootstrap_layout", f"{path}.entryId", str(error)))
                else:
                    profile = layout.get("profile") if isinstance(layout.get("profile"), dict) else {}
                    if layout.get("version") != "0.2" or layout.get("mode") != "production":
                        errors.append(issue("reuse.bootstrap_layout", f"{path}.entryId", "planned-bootstrap requires a production UILayoutSpec 0.2 layout."))
                    if profile.get("assetScope") != "project-common" or profile.get("system") != "common":
                        errors.append(issue("reuse.bootstrap_scope", f"{path}.entryId", "planned-bootstrap requires a project-common layout with system common."))
                    if profile.get("assetKind") != "child-widget":
                        errors.append(issue("reuse.bootstrap_asset_kind", f"{path}.entryId", "A project-common bootstrap layout must use profile.assetKind child-widget."))
                    if entry.get("assetKind") == "list-entry" and profile.get("listRole") != "entry":
                        errors.append(issue("reuse.bootstrap_list_role", f"{path}.entryId", "A list-entry bootstrap source requires profile.listRole entry."))
                    if _layout_asset_path(layout) != source_asset.get("assetPath"):
                        errors.append(issue("reuse.bootstrap_layout", f"{path}.entryId", "Bootstrap layout target does not match the Bundle source asset."))
                    if layout.get("referenceSize") != entry.get("expectedReferenceSize"):
                        errors.append(issue("reuse.bootstrap_layout", f"{path}.entryId", "Bootstrap layout referenceSize does not match the planned entry."))
                    if profile.get("parentClass") != entry.get("expectedParentClassPath"):
                        errors.append(issue("reuse.bootstrap_parent", f"{path}.entryId", "Bootstrap expected Parent Class does not match the layout profile."))

    if binding_out is not None:
        binding_out.update({"snapshotPath": snapshot_path, "snapshot": snapshot, "entry": entry, "valid": not errors})
    return errors


def _validate_parameter_overrides_against_registry_entry(
    parameter_overrides: Any,
    registry_entry: dict[str, Any],
    *,
    path: str,
    allow_unverified_empty_plan: bool = False,
) -> list[dict[str, str]]:
    """Validate one nested instance against an executable or explicitly pending Registry contract."""

    errors: list[dict[str, str]] = []
    overrides = parameter_overrides if isinstance(parameter_overrides, list) else []
    modes = registry_entry.get("generationModes") if isinstance(registry_entry.get("generationModes"), list) else []
    nested_modes = [mode for mode in modes if isinstance(mode, dict) and mode.get("mode") == "widget-tree-instance"]
    if len(nested_modes) != 1:
        return [
            issue(
                "reuse.parameter_generation_mode",
                path,
                "The actual shared Registry entry must declare exactly one widget-tree-instance generation mode.",
            )
        ]

    mode = nested_modes[0]
    contract_status = mode.get("parameterContractStatus")
    pending_unverified_contract = (
        allow_unverified_empty_plan
        and not overrides
        and mode.get("status") == "unverified"
        and contract_status == "unverified"
    )
    if mode.get("status") != "verified" and not pending_unverified_contract:
        errors.append(
            issue(
                "reuse.parameter_generation_mode",
                path,
                "WidgetTree nesting requires a verified widget-tree-instance generation mode in the actual shared Registry entry.",
            )
        )

    parameters = mode.get("instanceParameters") if isinstance(mode.get("instanceParameters"), list) else []
    if contract_status == "none":
        if overrides:
            errors.append(
                issue(
                    "reuse.parameter_contract_none",
                    path,
                    "The actual shared Registry generation mode is verified parameterless; parameterOverrides must be empty.",
                )
            )
        return errors
    if contract_status != "verified":
        if pending_unverified_contract:
            return errors
        errors.append(
            issue(
                "reuse.parameter_contract_unverified",
                path,
                "Executable parameterOverrides require a verified parameter contract in the actual shared Registry entry.",
            )
        )
        return errors

    parameter_by_name: dict[str, dict[str, Any]] = {}
    for parameter_index, parameter in enumerate(parameters):
        if not isinstance(parameter, dict) or not isinstance(parameter.get("name"), str):
            continue
        parameter_name = parameter["name"]
        if parameter_name in parameter_by_name:
            errors.append(
                issue(
                    "reuse.parameter_contract_duplicate",
                    path,
                    f"The actual shared Registry parameter contract declares {parameter_name!r} more than once.",
                )
            )
        parameter_by_name.setdefault(parameter_name, parameter)

    override_names: set[str] = set()
    for override_index, override in enumerate(overrides):
        if not isinstance(override, dict):
            continue
        override_path = f"{path}[{override_index}]"
        override_name = override.get("name")
        if not isinstance(override_name, str):
            continue
        if override_name in override_names:
            errors.append(issue("reuse.parameter_override_duplicate", f"{override_path}.name", "Nested parameter override names must be unique per WidgetTree instance."))
        override_names.add(override_name)
        parameter = parameter_by_name.get(override_name)
        if parameter is None:
            errors.append(issue("reuse.parameter_override_unknown", f"{override_path}.name", "Nested parameter override is not declared by the actual shared Registry parameter contract."))
            continue
        value_kind = parameter.get("valueKind")
        if (
            override.get("valueSource") != "runtime-binding"
            and isinstance(value_kind, str)
            and not value_matches_kind(override.get("value"), value_kind)
        ):
            errors.append(
                issue(
                    "reuse.parameter_override_type",
                    f"{override_path}.value",
                    f"Concrete nested parameter override does not match Registry valueKind {value_kind!r}.",
                )
            )

    for parameter_name, parameter in parameter_by_name.items():
        if parameter.get("required") is True and "defaultValue" not in parameter and parameter_name not in override_names:
            errors.append(
                issue(
                    "reuse.parameter_override_required",
                    path,
                    f"Required nested parameter {parameter_name!r} has no override or Registry default.",
                )
            )
    return errors


def _validate_named_slots_against_registry_entry(
    named_slots: dict[str, Any],
    registry_entry: dict[str, Any],
    *,
    path: str,
) -> list[dict[str, str]]:
    """Bind the selected dual-Slot operation and evidenced legacy names to Registry facts."""

    errors: list[dict[str, str]] = []
    registry_migration = registry_entry.get("extensionSlotMigration") if isinstance(registry_entry.get("extensionSlotMigration"), dict) else {}
    registry_contract = registry_entry.get("extensionSlotsContract") if isinstance(registry_entry.get("extensionSlotsContract"), dict) else {}
    if named_slots.get("operation") != registry_migration.get("operation"):
        errors.append(issue("reuse.slot_operation_registry", f"{path}.operation", "Dual-Slot add/migration operation must exactly match the actual shared Registry entry."))
    slot_names = [
        slot.get("standardName")
        for slot in named_slots.get("slots", [])
        if isinstance(slot, dict)
    ]
    declared_legacy = named_slots.get("legacyPreservedNames") if isinstance(named_slots.get("legacyPreservedNames"), list) else []
    registry_legacy = registry_contract.get("legacyPreservedNames") if isinstance(registry_contract.get("legacyPreservedNames"), list) else []
    if declared_legacy != registry_legacy:
        errors.append(issue("reuse.slot_legacy_registry", f"{path}.legacyPreservedNames", "legacyPreservedNames must exactly equal the ordered, evidenced legacy names in the actual shared Registry entry."))
    if named_slots.get("operation") == "migrate-existing-standard-slot":
        declared_migration = named_slots.get("legacyStandardMigration") if isinstance(named_slots.get("legacyStandardMigration"), dict) else {}
        migration_facts = (
            ("oldName", "oldStandardName"),
            ("newName", "renamedStandardName"),
            ("preSaveValidationRequired", "preSaveValidationRequired"),
        )
        if any(declared_migration.get(bundle_key) != registry_migration.get(registry_key) for bundle_key, registry_key in migration_facts):
            errors.append(issue("reuse.slot_migration_registry", f"{path}.legacyStandardMigration", "SlotContent rename and pre-save requirements must exactly match the actual Registry migration record."))
        expected_slot_names = [registry_migration.get("addedStandardName"), registry_migration.get("renamedStandardName")]
        if slot_names != expected_slot_names:
            errors.append(issue("reuse.slot_migration_registry", f"{path}.slots", "Dual-Slot names and order must exactly match the actual Registry migration record."))
        migration_legacy = registry_migration.get("legacyPreservedNames") if isinstance(registry_migration.get("legacyPreservedNames"), list) else []
        if declared_legacy != migration_legacy:
            errors.append(issue("reuse.slot_legacy_migration", f"{path}.legacyPreservedNames", "Migration legacyPreservedNames must exactly equal the actual Registry migration record."))
    elif named_slots.get("operation") == "add-dual-layer-slots":
        registry_added_names = registry_migration.get("addedStandardNames") if isinstance(registry_migration.get("addedStandardNames"), list) else []
        if slot_names != registry_added_names:
            errors.append(issue("reuse.slot_add_registry", f"{path}.slots", "New dual-Slot names and order must exactly match the actual Registry add record."))
    return errors


def _validate_reuse_relations(
    bundle: dict[str, Any],
    *,
    assets: dict[str, dict[str, Any]],
    accepted_claim_ids: set[str],
    requirement_claims: dict[str, Any],
    requirement_by_id: dict[str, Any],
    verification: dict[str, Any],
    bundle_path: Path,
    check_linked_files: bool,
) -> list[dict[str, str]]:
    """Validate the executable shared-prototype -> child -> host chain in reuse Bundles."""

    errors: list[dict[str, str]] = []
    bundle_version = bundle.get("version")
    execution = bundle.get("execution") if isinstance(bundle.get("execution"), dict) else {}
    finalized_lifecycle = execution.get("status") == "completed" or verification.get("status") == "passed"
    relations = bundle.get("reuseRelations", [])
    if not isinstance(relations, list):
        return errors
    relation_types = {relation.get("type") for relation in relations if isinstance(relation, dict)}
    allowed_types = {
        "shared-prototype-extension",
        "class-settings-parent-class",
        "widget-tree-instance",
    }
    unknown_types = relation_types - allowed_types
    if unknown_types:
        errors.append(issue("reuse.type", "$.reuseRelations", f"Unknown reuse relation types: {sorted(unknown_types)}."))

    checks = {
        check.get("id"): check
        for check in verification.get("checks", [])
        if isinstance(check, dict) and isinstance(check.get("id"), str)
    }
    extensions_by_asset: dict[str, list[dict[str, Any]]] = {}
    parent_relations_by_child: dict[str, list[dict[str, Any]]] = {}
    for relation in relations:
        if not isinstance(relation, dict):
            continue
        if relation.get("type") == "shared-prototype-extension":
            extensions_by_asset.setdefault(str(relation.get("targetAssetId")), []).append(relation)
        elif relation.get("type") == "class-settings-parent-class":
            parent_relations_by_child.setdefault(str(relation.get("targetAssetId")), []).append(relation)

    registry_states_by_relation_id: dict[str, dict[str, Any]] = {}
    bootstrap_states_by_relation_id: dict[str, dict[str, Any]] = {}
    if check_linked_files:
        for relation_index, relation in enumerate(relations):
            if not isinstance(relation, dict) or relation.get("type") != "shared-prototype-extension":
                continue
            binding_state: dict[str, Any] = {}
            if isinstance(relation.get("bootstrapSnapshot"), dict):
                binding_errors = _validate_shared_bootstrap_binding(
                    relation["bootstrapSnapshot"],
                    source_asset=assets.get(relation.get("sourceAssetId")),
                    bundle_path=bundle_path,
                    path=f"$.reuseRelations[{relation_index}].bootstrapSnapshot",
                    binding_out=binding_state,
                )
            else:
                binding_errors = _validate_shared_registry_binding(
                    relation.get("registry") if isinstance(relation.get("registry"), dict) else {},
                    source_asset=assets.get(relation.get("sourceAssetId")),
                    bundle_version=str(bundle_version),
                    bundle_path=bundle_path,
                    path=f"$.reuseRelations[{relation_index}].registry",
                    binding_out=binding_state,
                )
            errors.extend(binding_errors)
            relation_id = relation.get("id")
            if isinstance(relation_id, str):
                if isinstance(relation.get("bootstrapSnapshot"), dict):
                    bootstrap_states_by_relation_id[relation_id] = binding_state
                else:
                    registry_states_by_relation_id[relation_id] = binding_state

    def transitive_dependents(source_asset_id: str) -> set[str]:
        dependents: set[str] = set()
        changed = True
        while changed:
            changed = False
            for candidate_id, candidate in assets.items():
                dependencies = set(candidate.get("dependsOnAssetIds", [])) if isinstance(candidate.get("dependsOnAssetIds"), list) else set()
                if candidate_id not in dependents and (source_asset_id in dependencies or bool(dependencies & dependents)):
                    dependents.add(candidate_id)
                    changed = True
        return dependents

    def premature_activation_consumers(extension_relation: dict[str, Any]) -> list[str]:
        source_asset_id = extension_relation.get("sourceAssetId")
        if not isinstance(source_asset_id, str):
            return []
        return [
            candidate_id
            for candidate_id in sorted(transitive_dependents(source_asset_id))
            if assets.get(candidate_id, {}).get("status") != "planned"
        ]

    def is_pending_nonexecuting_instance_plan(
        extension_relation: dict[str, Any],
        parameter_overrides: list[Any],
    ) -> bool:
        activation = extension_relation.get("activation") if isinstance(extension_relation.get("activation"), dict) else {}
        return (
            not finalized_lifecycle
            and not parameter_overrides
            and activation.get("mode") == "post-extension-activation"
            and activation.get("status") == "required"
            and not premature_activation_consumers(extension_relation)
        )

    for index, relation in enumerate(relations):
        if not isinstance(relation, dict):
            continue
        path = f"$.reuseRelations[{index}]"
        relation_type = relation.get("type")
        source_id = relation.get("sourceAssetId")
        target_id = relation.get("targetAssetId")
        source = assets.get(source_id)
        target = assets.get(target_id)
        if source is None:
            errors.append(issue("reuse.ref_asset", f"{path}.sourceAssetId", "Unknown source bundle asset."))
        elif relation.get("sourceAssetPath") != source.get("assetPath"):
            errors.append(issue("reuse.asset_path", f"{path}.sourceAssetPath", "sourceAssetPath must exactly match sourceAssetId."))
        if target is None:
            errors.append(issue("reuse.ref_asset", f"{path}.targetAssetId", "Unknown target bundle asset."))
        elif relation.get("targetAssetPath") != target.get("assetPath"):
            errors.append(issue("reuse.asset_path", f"{path}.targetAssetPath", "targetAssetPath must exactly match targetAssetId."))

        relation_claims = set(relation.get("claimIds", [])) if isinstance(relation.get("claimIds"), list) else set()
        if not relation_claims or not relation_claims.issubset(accepted_claim_ids):
            errors.append(issue("reuse.claim_status", f"{path}.claimIds", "Reuse relations require reviewed accepted claims."))
        relation_requirements = relation.get("requirementRefs", []) if isinstance(relation.get("requirementRefs"), list) else []
        if not relation_requirements:
            errors.append(issue("reuse.requirement_refs", f"{path}.requirementRefs", "Reuse relations require at least one requirement reference."))
        for requirement_ref in relation_requirements:
            indexed = requirement_by_id.get(requirement_ref)
            if indexed is None:
                errors.append(issue("reuse.ref_requirement", f"{path}.requirementRefs", f"Unknown requirement id: {requirement_ref}"))
                continue
            entity = indexed.get("entity", {})
            if entity.get("inBuildScope") is not True:
                errors.append(issue("reuse.out_of_scope", f"{path}.requirementRefs", f"Out-of-scope requirement {requirement_ref} cannot enter a reuse relation."))
            if not (set(entity.get("claimIds", [])) & relation_claims & accepted_claim_ids):
                errors.append(issue("reuse.evidence_chain", f"{path}.requirementRefs", f"Requirement {requirement_ref} is not backed by a relation claim."))
        subjects = {
            source.get("assetPlanId") if isinstance(source, dict) else None,
            target.get("assetPlanId") if isinstance(target, dict) else None,
        }
        subjects.update(requirement_requirements for requirement_requirements in relation_requirements)
        for claim_id in sorted(relation_claims):
            if not (set(requirement_claims.get(claim_id, {}).get("subjectRefs", [])) & subjects):
                errors.append(issue("reuse.unrelated_claim", f"{path}.claimIds", f"Reuse claim {claim_id} names neither participating assetPlan."))

        if relation_type == "shared-prototype-extension":
            if source_id != target_id:
                errors.append(issue("reuse.extension_identity", path, "A shared-prototype-extension mutates one asset in place; source and target IDs must match."))
            if isinstance(source, dict) and relation.get("sourceAssetPath") != relation.get("targetAssetPath"):
                errors.append(issue("reuse.extension_path", path, "A shared-prototype-extension source and target path must match."))
            if bundle_version == "0.2":
                named_slot = relation.get("namedSlot") if isinstance(relation.get("namedSlot"), dict) else {}
                if named_slot.get("operation") == "rename-legacy-slot" and named_slot.get("oldName") == named_slot.get("newName"):
                    errors.append(issue("reuse.slot_rename_identity", f"{path}.namedSlot", "A legacy NamedSlot rename must change the widget name."))
                if named_slot.get("operation") == "add-standard-slot":
                    standard_name = named_slot.get("standardName")
                    preserved_names = named_slot.get("legacyPreservedNames", [])
                    if isinstance(preserved_names, list) and standard_name in preserved_names:
                        errors.append(issue("reuse.slot_preserved_conflict", f"{path}.namedSlot.legacyPreservedNames", "The newly added standard SlotContent NamedSlot cannot also be listed as a preserved legacy NamedSlot."))
            else:
                named_slots = relation.get("namedSlots") if isinstance(relation.get("namedSlots"), dict) else {}
                slot_names = [
                    slot.get("standardName")
                    for slot in named_slots.get("slots", [])
                    if isinstance(slot, dict)
                ]
                preserved_names = named_slots.get("legacyPreservedNames", [])
                conflicts = sorted(set(slot_names) & set(preserved_names if isinstance(preserved_names, list) else []))
                if conflicts:
                    errors.append(issue("reuse.slots_preserved_conflict", f"{path}.namedSlots.legacyPreservedNames", f"New dual extension Slots cannot also be preserved legacy names: {conflicts}."))
            registry = relation.get("registry") if isinstance(relation.get("registry"), dict) else {}
            bootstrap = relation.get("bootstrapSnapshot") if isinstance(relation.get("bootstrapSnapshot"), dict) else {}
            binding_state = registry_states_by_relation_id.get(str(relation.get("id")), {})
            bootstrap_state = bootstrap_states_by_relation_id.get(str(relation.get("id")), {})
            activation = relation.get("activation") if isinstance(relation.get("activation"), dict) else {}
            activation_is_complete = activation.get("mode") == "preverified" or (
                activation.get("mode") == "post-extension-activation"
                and activation.get("status") == "verified"
            )
            if finalized_lifecycle and not activation_is_complete:
                errors.append(
                    issue(
                        "reuse.activation_lifecycle",
                        f"{path}.activation",
                        "A completed execution or passed verification cannot retain planned shared-prototype activation; use preverified or verified post-extension activation.",
                    )
                )
            extension_status_key = "extensionSlotStatus" if bundle_version == "0.2" else "extensionSlotsStatus"
            registry_is_active = registry.get("entryStatus") == "active" and registry.get(extension_status_key) == "verified"
            actual_entry = binding_state.get("entry") if binding_state.get("valid") is True and isinstance(binding_state.get("entry"), dict) else None
            actual_contract_key = "extensionSlotContract" if bundle_version == "0.2" else "extensionSlotsContract"
            actual_contract = actual_entry.get(actual_contract_key) if isinstance(actual_entry, dict) and isinstance(actual_entry.get(actual_contract_key), dict) else {}
            actual_registry_is_active = (
                isinstance(actual_entry, dict)
                and actual_entry.get("status") == "active"
                and actual_contract.get("status") == "verified"
            )
            if bundle_version == "0.3" and isinstance(actual_entry, dict):
                errors.extend(
                    _validate_named_slots_against_registry_entry(
                        relation.get("namedSlots") if isinstance(relation.get("namedSlots"), dict) else {},
                        actual_entry,
                        path=f"{path}.namedSlots",
                    )
                )
            if bootstrap:
                bootstrap_entry = bootstrap_state.get("entry") if bootstrap_state.get("valid") is True and isinstance(bootstrap_state.get("entry"), dict) else None
                if bundle_version != "0.3":
                    errors.append(issue("reuse.bootstrap_version", f"{path}.bootstrapSnapshot", "planned-bootstrap is supported only by Bundle 0.3."))
                if not check_linked_files:
                    errors.append(issue("reuse.bootstrap_binding", f"{path}.bootstrapSnapshot", "planned-bootstrap requires linked-file validation; --skip-linked-files cannot prove its content-addressed snapshot, layout, new-package status, or base Registry guard."))
                if not isinstance(source, dict) or source.get("representationKind") != "layout-spec":
                    errors.append(issue("reuse.bootstrap_representation", f"{path}.sourceAssetId", "planned-bootstrap is restricted to a new layout-spec asset."))
                if activation.get("mode") != "post-extension-activation" or activation.get("status") != "required":
                    errors.append(issue("reuse.bootstrap_activation", f"{path}.activation", "planned-bootstrap requires an unfinished post-extension activation gate."))
                if isinstance(bootstrap_entry, dict) and bootstrap_entry.get("authorization") != relation.get("authorization"):
                    errors.append(issue("reuse.bootstrap_authorization", f"{path}.authorization", "Relation authorization must exactly match the bootstrap entry authorization."))
                bootstrap_slots = relation.get("namedSlots") if isinstance(relation.get("namedSlots"), dict) else {}
                if bootstrap_slots.get("operation") != "add-dual-layer-slots":
                    errors.append(issue("reuse.bootstrap_slot_operation", f"{path}.namedSlots", "A new shared asset may only add the canonical dual-layer Slots."))
            if activation.get("mode") == "preverified":
                if not registry_is_active:
                    expected = "SlotContent" if bundle_version == "0.2" else "SlotDown/SlotUp"
                    errors.append(issue("reuse.activation_gate", f"{path}.activation", f"preverified activation requires an active registry entry with verified {expected} extension NamedSlot evidence."))
            elif activation.get("mode") == "post-extension-activation":
                verification_ids = activation.get("verificationCheckIds", [])
                for check_id in verification_ids:
                    if check_id not in checks:
                        errors.append(issue("reuse.activation_check", f"{path}.activation.verificationCheckIds", f"Unknown verification check: {check_id}"))
                if activation.get("status") == "verified":
                    if not check_linked_files:
                        errors.append(issue("reuse.activation_registry_binding", f"{path}.activation", "Verified post-extension activation requires linked-file validation of the authoritative shared Registry; --skip-linked-files cannot prove activation."))
                    elif not actual_registry_is_active:
                        errors.append(issue("reuse.activation_registry_binding", f"{path}.activation", f"Verified post-extension activation requires the actual authoritative Registry entry to be active with {actual_contract_key}.status verified."))
                    failed = [check_id for check_id in verification_ids if checks.get(check_id, {}).get("status") != "passed"]
                    if failed:
                        errors.append(issue("reuse.activation_evidence", f"{path}.activation", f"Verified post-extension activation requires passed checks: {failed}."))
                    artifact = activation.get("evidenceArtifactPath")
                    if check_linked_files and isinstance(artifact, str):
                        evidence_path = resolve_contract_path(bundle_path, artifact)
                        if not evidence_path.is_file():
                            errors.append(issue("reuse.activation_artifact", f"{path}.activation.evidenceArtifactPath", "Activation evidence artifact does not exist."))
                        elif activation.get("evidenceArtifactSha256") != sha256_file(evidence_path):
                            errors.append(issue("reuse.activation_artifact_hash", f"{path}.activation.evidenceArtifactSha256", "Activation evidence artifact hash does not match."))
                elif registry_is_active:
                    errors.append(issue("reuse.activation_mixed", f"{path}.activation", "An already active/verified registry snapshot must use preverified activation, not a planned activation."))

                if activation.get("status") == "required":
                    premature = premature_activation_consumers(relation)
                    if premature:
                        errors.append(
                            issue(
                                "reuse.activation_premature_consumer",
                                f"{path}.activation",
                                f"Consumers must remain planned until shared activation is verified: {premature}.",
                            )
                        )

            if isinstance(source, dict) and isinstance(source.get("buildOrder"), int):
                extension_order = source["buildOrder"]
                # The extension is the first executable step and therefore may not depend on later assets.
                later_dependencies = [
                    dependency
                    for dependency in source.get("dependsOnAssetIds", [])
                    if assets.get(dependency, {}).get("buildOrder", -1) >= extension_order
                ]
                if later_dependencies:
                    errors.append(issue("reuse.extension_order", path, f"Shared prototype extension has non-prior dependencies: {later_dependencies}."))

        elif relation_type == "class-settings-parent-class":
            if source_id == target_id:
                errors.append(issue("reuse.parent_identity", path, "Parent Class source and child target must be different assets."))
            if isinstance(source, dict) and isinstance(target, dict):
                if source_id not in set(target.get("dependsOnAssetIds", [])):
                    errors.append(issue("reuse.parent_dependency", path, "The system child must directly depend on its shared prototype."))
                if source.get("buildOrder", 10**9) >= target.get("buildOrder", -1):
                    errors.append(issue("reuse.parent_order", path, "The shared prototype must be built before its system child."))
                expected_parent = f"{source.get('assetPath')}.{str(source.get('assetPath')).rsplit('/', 1)[-1]}_C"
                if relation.get("parentClassPath") != expected_parent:
                    errors.append(issue("reuse.parent_class_path", f"{path}.parentClassPath", f"parentClassPath must equal the shared prototype generated class {expected_parent}."))
                extensions = extensions_by_asset.get(str(source_id), [])
                if not extensions:
                    errors.append(issue("reuse.parent_extension_missing", path, "Parent Class reuse requires an earlier shared-prototype-extension relation for the prototype."))
                elif any(source.get("buildOrder", -1) >= target.get("buildOrder", -1) for _ in extensions):
                    errors.append(issue("reuse.parent_extension_order", path, "Shared extension must precede child creation in asset build order."))
            if bundle_version == "0.3":
                inherited_slots = relation.get("inheritedSlots") if isinstance(relation.get("inheritedSlots"), list) else []
                panel_names: set[str] = set()
                panel_paths: set[str] = set()
                for slot_index, inherited_slot in enumerate(inherited_slots):
                    if not isinstance(inherited_slot, dict) or inherited_slot.get("contentMode") != "panel":
                        continue
                    panel = inherited_slot.get("panel") if isinstance(inherited_slot.get("panel"), dict) else {}
                    panel_path = f"{path}.inheritedSlots[{slot_index}].panel"
                    if panel.get("classPath") not in SUPPORTED_SEMANTIC_PANEL_CLASSES:
                        errors.append(issue("reuse.slot_panel_class", f"{panel_path}.classPath", "Inherited Slot content must begin with a supported semantic Panel class."))
                    if panel.get("layoutMode") == "fill":
                        expected_fill = {"mode": "fill", "anchors": [0, 0, 1, 1], "offsets": [0, 0, 0, 0], "alignment": [0, 0]}
                        if panel.get("layout") != expected_fill:
                            errors.append(issue("reuse.slot_panel_fill", panel_path, "A default inherited Slot Panel must use the canonical full-fill layout."))
                        if panel.get("specialAdaptationEvidenceRefs"):
                            errors.append(issue("reuse.slot_panel_fill_evidence", panel_path, "A full-fill inherited Slot Panel must not declare special-adaptation evidence."))
                    elif panel.get("layoutMode") == "special-adaptation":
                        if not panel.get("specialAdaptationEvidenceRefs"):
                            errors.append(issue("reuse.slot_panel_adaptation_evidence", panel_path, "Special Slot adaptation requires explicit accepted evidence refs."))
                        if "layout" in panel:
                            errors.append(issue("reuse.slot_panel_special_layout", panel_path, "Special Slot adaptation must not masquerade as the canonical full-fill layout."))
                    widget_name = panel.get("widgetName")
                    tree_path = panel.get("treePath")
                    if isinstance(widget_name, str):
                        if widget_name in panel_names:
                            errors.append(issue("reuse.slot_panel_identity", f"{panel_path}.widgetName", "Each used inherited Slot requires a distinct direct Panel identity."))
                        panel_names.add(widget_name)
                    if isinstance(tree_path, str):
                        if tree_path in panel_paths:
                            errors.append(issue("reuse.slot_panel_identity", f"{panel_path}.treePath", "Each used inherited Slot requires a distinct direct Panel tree path."))
                        panel_paths.add(tree_path)

        elif relation_type == "widget-tree-instance":
            host = relation.get("host") if isinstance(relation.get("host"), dict) else {}
            host_widget_name = host.get("widgetName")
            if isinstance(target, dict) and isinstance(host_widget_name, str):
                expected_host_tree_path = _widget_tree_path(str(target.get("assetPath")), host_widget_name)
                if host.get("treePath") != expected_host_tree_path:
                    errors.append(
                        issue(
                            "reuse.instance_host_path",
                            f"{path}.host.treePath",
                            f"Nested host treePath must identify widget {host_widget_name!r} inside targetAssetId; expected {expected_host_tree_path!r}.",
                        )
                    )
            if bundle_version == "0.2" and relation.get("parameterOverrides"):
                errors.append(
                    issue(
                        "reuse.parameter_overrides_version",
                        f"{path}.parameterOverrides",
                        "UIBuildBundle 0.2 cannot carry executable Designer parameter overrides because UnrealReadback 0.2 cannot prove them; use UIBuildBundle 0.3 for parameterized nesting.",
                    )
                )
            elif bundle_version == "0.3":
                parameter_overrides = relation.get("parameterOverrides") if isinstance(relation.get("parameterOverrides"), list) else []
                parent_relations = parent_relations_by_child.get(str(source_id), [])
                prototype_extensions = (
                    extensions_by_asset.get(str(parent_relations[0].get("sourceAssetId")), [])
                    if len(parent_relations) == 1
                    else []
                )
                if len(prototype_extensions) != 1:
                    errors.append(issue("reuse.parameter_registry_chain", f"{path}.parameterOverrides", "WidgetTree nesting requires exactly one prototype extension relation and one shared activation contract."))
                else:
                    prototype_extension = prototype_extensions[0]
                    pending_nonexecuting_plan = is_pending_nonexecuting_instance_plan(
                        prototype_extension,
                        parameter_overrides,
                    )
                    extension_id = prototype_extension.get("id")
                    binding_state = registry_states_by_relation_id.get(str(extension_id), {})
                    registry_entry = binding_state.get("entry") if binding_state.get("valid") is True else None
                    bootstrap_state = bootstrap_states_by_relation_id.get(str(extension_id), {})
                    bootstrap_is_valid = bootstrap_state.get("valid") is True
                    if pending_nonexecuting_plan:
                        if check_linked_files and isinstance(registry_entry, dict):
                            errors.extend(
                                _validate_parameter_overrides_against_registry_entry(
                                    parameter_overrides,
                                    registry_entry,
                                    path=f"{path}.parameterOverrides",
                                    allow_unverified_empty_plan=True,
                                )
                            )
                        elif check_linked_files and not bootstrap_is_valid:
                            errors.append(
                                issue(
                                    "reuse.parameter_registry_binding",
                                    f"{path}.parameterOverrides",
                                    "A pending WidgetTree plan requires a valid candidate Registry or planned-bootstrap binding.",
                                )
                            )
                    else:
                        if not check_linked_files:
                            errors.append(
                                issue(
                                    "reuse.parameter_registry_binding",
                                    f"{path}.parameterOverrides",
                                    "Executable or finalized WidgetTree nesting requires linked validation of the authoritative active shared Registry contract.",
                                )
                            )
                        else:
                            if isinstance(registry_entry, dict):
                                errors.extend(
                                    _validate_parameter_overrides_against_registry_entry(
                                        parameter_overrides,
                                        registry_entry,
                                        path=f"{path}.parameterOverrides",
                                    )
                                )
                            else:
                                errors.append(
                                    issue(
                                        "reuse.parameter_registry_binding",
                                        f"{path}.parameterOverrides",
                                        "Executable or finalized WidgetTree nesting cannot use a bootstrap, invalid, mismatched, or unreadable Registry binding.",
                                    )
                                )
            if source_id == target_id:
                errors.append(issue("reuse.instance_identity", path, "Nested source and host target must be different assets."))
            if isinstance(source, dict) and isinstance(target, dict):
                if source_id not in set(target.get("dependsOnAssetIds", [])):
                    errors.append(issue("reuse.instance_dependency", path, "The host must directly depend on the nested system child."))
                if source.get("buildOrder", 10**9) >= target.get("buildOrder", -1):
                    errors.append(issue("reuse.instance_order", path, "The system child must be built before its host."))
                parent_relations = parent_relations_by_child.get(str(source_id), [])
                if len(parent_relations) != 1:
                    errors.append(issue("reuse.instance_parent_chain", path, "Nested reuse requires exactly one class-settings-parent-class relation for its source child."))
                elif relation.get("sharedPrototypeClassPath") != parent_relations[0].get("parentClassPath"):
                    errors.append(issue("reuse.instance_prototype", f"{path}.sharedPrototypeClassPath", "Nested sharedPrototypeClassPath must match the child relation parentClassPath."))
                expected_child_class = f"{source.get('assetPath')}.{str(source.get('assetPath')).rsplit('/', 1)[-1]}_C"
                if relation.get("nestedWidgetClassPath") != expected_child_class:
                    errors.append(issue("reuse.instance_class", f"{path}.nestedWidgetClassPath", f"nestedWidgetClassPath must equal the actual system child generated class {expected_child_class}."))

                placement = relation.get("placementContract") if isinstance(relation.get("placementContract"), dict) else {}
                compatibility = placement.get("childSizingCompatibility") if isinstance(placement.get("childSizingCompatibility"), dict) else {}
                compatibility_path = f"{path}.placementContract.childSizingCompatibility"
                compatibility_mode = compatibility.get("mode")
                if compatibility_mode in {
                    "inherited-reuse-only-full-stretch",
                    "inherited-reuse-only-flow-slot",
                    "inherited-reuse-only-scroll-slot",
                }:
                    if source.get("representationKind") != "reuse-only" or source.get("layoutSpecPath") is not None or source.get("layoutSpecSha256") is not None:
                        errors.append(
                            issue(
                                "reuse.instance_sizing_representation",
                                compatibility_path,
                                f"{compatibility_mode} requires a reuse-only source with no UILayoutSpec identity.",
                            )
                        )
                    if set(compatibility.get("axes", [])) != {"horizontal", "vertical"}:
                        errors.append(issue("reuse.instance_sizing_axes", f"{compatibility_path}.axes", "Inherited reuse-only sizing evidence must cover both axes."))
                    slot = placement.get("slot") if isinstance(placement.get("slot"), dict) else {}
                    if compatibility_mode == "inherited-reuse-only-full-stretch":
                        if (
                            placement.get("sizingStrategy") != "fill-host"
                            or slot.get("horizontalAlignment") != "Fill"
                            or slot.get("verticalAlignment") != "Fill"
                            or slot.get("padding") != [0, 0, 0, 0]
                        ):
                            errors.append(
                                issue(
                                    "reuse.instance_sizing_host_fill",
                                    f"{path}.placementContract",
                                    "Inherited full-stretch requires fill-host sizing, Fill/Fill slot alignment, and zero padding.",
                                )
                            )
                    elif compatibility_mode == "inherited-reuse-only-flow-slot":
                        allocation = compatibility.get("allocation")
                        slot_size = slot.get("size") if isinstance(slot.get("size"), dict) else {}
                        expected_rule = "Auto" if allocation == "content-driven" else "Fill"
                        expected_sizing_strategy = allocation
                        fill_weight = slot_size.get("weight")
                        valid_fill_weight = (
                            isinstance(fill_weight, (int, float))
                            and not isinstance(fill_weight, bool)
                            and fill_weight > 0
                        )
                        if slot.get("containerType") not in {"HorizontalBox", "VerticalBox"}:
                            errors.append(
                                issue(
                                    "reuse.instance_sizing_flow_container",
                                    f"{path}.placementContract.slot.containerType",
                                    "inherited-reuse-only-flow-slot is valid only for a HorizontalBox or VerticalBox direct-child Slot; scrolling containers do not use Box Size allocation.",
                                )
                            )
                        if (
                            placement.get("sizingStrategy") != expected_sizing_strategy
                            or slot_size.get("rule") != expected_rule
                            or (expected_rule == "Auto" and "weight" in slot_size)
                            or (expected_rule == "Fill" and not valid_fill_weight)
                        ):
                            errors.append(
                                issue(
                                    "reuse.instance_sizing_flow_allocation",
                                    f"{path}.placementContract",
                                    f"Flow-slot allocation {allocation!r} requires sizingStrategy {expected_sizing_strategy!r} and Box Size rule {expected_rule!r}"
                                    + (" with a positive weight." if expected_rule == "Fill" else " without a weight."),
                                )
                            )
                    else:
                        if slot.get("containerType") != "GameScrollBox":
                            errors.append(
                                issue(
                                    "reuse.instance_sizing_scroll_container",
                                    f"{path}.placementContract.slot.containerType",
                                    "inherited-reuse-only-scroll-slot is valid only for a GameScrollBox direct-child Slot.",
                                )
                            )
                        if placement.get("sizingStrategy") != "scroll-slot" or "size" in slot:
                            errors.append(
                                issue(
                                    "reuse.instance_sizing_scroll_contract",
                                    f"{path}.placementContract",
                                    "A reuse-only GameScrollBox child requires sizingStrategy 'scroll-slot' and no Box Size field; Padding and both alignments remain independently reviewed.",
                                )
                            )
                    if len(parent_relations) == 1:
                        parent_relation = parent_relations[0]
                        if compatibility.get("parentRelationId") != parent_relation.get("id"):
                            errors.append(
                                issue(
                                    "reuse.instance_sizing_parent_relation",
                                    f"{compatibility_path}.parentRelationId",
                                    "parentRelationId must identify the source child's unique class-settings-parent-class relation.",
                                )
                            )
                        if bundle_version == "0.2":
                            inherited_slot = parent_relation.get("inheritedSlot") if isinstance(parent_relation.get("inheritedSlot"), dict) else {}
                            inherited_tree_is_empty = inherited_slot.get("contentMode") == "empty"
                        else:
                            inherited_slots = parent_relation.get("inheritedSlots") if isinstance(parent_relation.get("inheritedSlots"), list) else []
                            inherited_tree_is_empty = (
                                len(inherited_slots) == 2
                                and all(
                                    isinstance(inherited_slot, dict) and inherited_slot.get("contentMode") == "empty"
                                    for inherited_slot in inherited_slots
                                )
                            )
                        if not inherited_tree_is_empty:
                            errors.append(
                                issue(
                                    "reuse.instance_sizing_owned_tree",
                                    compatibility_path,
                                    "Inherited reuse-only sizing requires the system child to keep every inherited extension Slot empty so it contributes no owned layout tree.",
                                )
                            )

                        prototype_id = parent_relation.get("sourceAssetId")
                        prototype_extensions = extensions_by_asset.get(str(prototype_id), [])
                        if len(prototype_extensions) != 1:
                            errors.append(
                                issue(
                                    "reuse.instance_sizing_extension_chain",
                                    f"{compatibility_path}.prototypeExtensionRelationId",
                                    "Inherited reuse-only sizing requires exactly one shared-prototype-extension relation for the inherited parent.",
                                )
                            )
                        elif compatibility.get("prototypeExtensionRelationId") != prototype_extensions[0].get("id"):
                            errors.append(
                                issue(
                                    "reuse.instance_sizing_extension_relation",
                                    f"{compatibility_path}.prototypeExtensionRelationId",
                                    "prototypeExtensionRelationId must identify the inherited parent's unique shared-prototype-extension relation.",
                                )
                            )
                elif source.get("representationKind") == "reuse-only":
                    errors.append(
                        issue(
                            "reuse.instance_sizing_layout_evidence",
                            compatibility_path,
                            "A reuse-only source has no UILayoutSpec nodes; use an inherited reuse-only compatibility mode with explicit parent and prototype-extension relation evidence instead of sourceLayoutNodeIds.",
                        )
                    )

    return errors


def validate_build_bundle(
    bundle: Any,
    schema: dict[str, Any],
    *,
    bundle_path: Path,
    requirement_spec: dict[str, Any] | None = None,
    requirement_path: Path | None = None,
    requirement_schema: dict[str, Any] | None = None,
    check_linked_files: bool = True,
) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    if not isinstance(bundle, dict):
        return result(validate_schema_instance(bundle, schema), warnings)

    bundle_version = bundle.get("version")
    if bundle_version not in SUPPORTED_BUNDLE_VERSIONS:
        return result(
            [issue("bundle.version", "$.version", f"Unsupported UIBuildBundle version: {bundle_version!r}.")],
            warnings,
        )
    errors.extend(validate_schema_instance(bundle, schema))

    requirement_link = bundle.get("requirement") if isinstance(bundle.get("requirement"), dict) else {}
    if requirement_link.get("reviewStatus") != "accepted":
        errors.append(issue("requirement.review", "$.requirement.reviewStatus", "A build bundle may only use an accepted requirement review."))

    if requirement_path is None and isinstance(requirement_link.get("path"), str):
        requirement_path = resolve_contract_path(bundle_path, requirement_link["path"])
    if requirement_spec is None and requirement_path is not None:
        try:
            requirement_spec = load_json(requirement_path)
        except (OSError, json.JSONDecodeError) as error:
            errors.append(issue("requirement.read", "$.requirement.path", str(error)))

    requirement_schema = requirement_schema or load_json(REQUIREMENT_SCHEMA)
    requirement_index: dict[str, Any] = {"byId": {}, "byKind": {}}
    interactive_button_requirements: dict[str, set[str]] = {}
    design_size_modes_by_plan: dict[str, str] = {}
    accepted_claim_ids: set[str] = set()
    static_visual_policy = False
    image_composition_policy = False
    explicit_panel_slots_policy = False
    explicit_image_owner_intent_policy = False
    design_size_mode_policy = False
    if isinstance(requirement_spec, dict):
        requirement_result = validate_requirement_spec(requirement_spec, requirement_schema)
        if not requirement_result["valid"]:
            errors.append(issue("requirement.invalid", "$.requirement", "Linked UIRequirementSpec is invalid."))
        review = requirement_spec.get("reviewGate") if isinstance(requirement_spec.get("reviewGate"), dict) else {}
        if review.get("status") != "accepted":
            errors.append(issue("requirement.review_source", "$.requirement", "Linked UIRequirementSpec reviewGate must be accepted."))
        if requirement_link.get("reviewStatus") != review.get("status"):
            errors.append(issue("requirement.review_mismatch", "$.requirement.reviewStatus", "Bundle reviewStatus does not match UIRequirementSpec."))
        if requirement_link.get("requestId") != requirement_spec.get("requestId"):
            errors.append(issue("requirement.request_id", "$.requirement.requestId", "Bundle requestId does not match UIRequirementSpec."))
        if requirement_link.get("revision") != requirement_spec.get("revision"):
            errors.append(issue("requirement.revision", "$.requirement.revision", "Bundle revision does not match UIRequirementSpec."))
        if requirement_link.get("approvedContentSha256") != review.get("approvedContentSha256"):
            errors.append(
                issue(
                    "requirement.approval_digest",
                    "$.requirement.approvedContentSha256",
                    "Bundle approval digest does not match UIRequirementSpec reviewGate.",
                )
            )
        if check_linked_files and requirement_path is not None and requirement_path.is_file():
            actual_hash = sha256_file(requirement_path)
            if requirement_link.get("sha256") != actual_hash:
                errors.append(issue("requirement.sha256", "$.requirement.sha256", f"Requirement hash mismatch; expected {actual_hash}."))
        requirement_index = build_requirement_index(requirement_spec)
        interactive_button_requirements = required_user_interaction_buttons(requirement_spec, requirement_index)
        design_size_modes_by_plan = required_design_size_modes(requirement_spec, requirement_index)
        claims = requirement_index["byKind"].get("claim", {})
        reviewed = set(review.get("acceptedClaimIds", [])) if isinstance(review.get("acceptedClaimIds"), list) else set()
        accepted_claim_ids = {
            claim_id
            for claim_id, claim in claims.items()
            if claim.get("status") == "accepted" and claim_id in reviewed
        }
        static_visual_policy = (
            isinstance(requirement_spec.get("analysisPolicy"), dict)
            and requirement_spec["analysisPolicy"].get("staticVisualCoverageRequired") is True
        )
        image_composition_policy = (
            isinstance(requirement_spec.get("analysisPolicy"), dict)
            and requirement_spec["analysisPolicy"].get("imageCompositionRequired") is True
        )
        explicit_panel_slots_policy = (
            isinstance(requirement_spec.get("analysisPolicy"), dict)
            and requirement_spec["analysisPolicy"].get("explicitPanelSlotsRequired") is True
        )
        explicit_image_owner_intent_policy = (
            isinstance(requirement_spec.get("analysisPolicy"), dict)
            and requirement_spec["analysisPolicy"].get("explicitImageOwnerIntentRequired") is True
        )
        design_size_mode_policy = (
            isinstance(requirement_spec.get("analysisPolicy"), dict)
            and requirement_spec["analysisPolicy"].get("designSizeModeRequired") is True
        )

    all_bundle_ids: set[str] = set()
    bundle_id = bundle.get("bundleId")
    if isinstance(bundle_id, str):
        all_bundle_ids.add(bundle_id)
    id_sections = ["assets", "nodeMappings", "crossAssetOperations"]
    if bundle_version in REUSE_BUNDLE_VERSIONS:
        id_sections.append("reuseRelations")
    for section in id_sections:
        for index, entity in enumerate(bundle.get(section, [])):
            if not isinstance(entity, dict) or not isinstance(entity.get("id"), str):
                continue
            entity_id = entity["id"]
            if entity_id in all_bundle_ids:
                errors.append(issue("id.duplicate", f"$.{section}[{index}].id", f"Bundle id {entity_id!r} is duplicated."))
            all_bundle_ids.add(entity_id)
    verification = bundle.get("verification") if isinstance(bundle.get("verification"), dict) else {}
    for index, check in enumerate(verification.get("checks", [])):
        if not isinstance(check, dict) or not isinstance(check.get("id"), str):
            continue
        check_id = check["id"]
        if check_id in all_bundle_ids:
            errors.append(issue("id.duplicate", f"$.verification.checks[{index}].id", f"Bundle id {check_id!r} is duplicated."))
        all_bundle_ids.add(check_id)
    for index, deviation in enumerate(verification.get("deviations", [])):
        if not isinstance(deviation, dict) or not isinstance(deviation.get("id"), str):
            continue
        deviation_id = deviation["id"]
        if deviation_id in all_bundle_ids:
            errors.append(issue("id.duplicate", f"$.verification.deviations[{index}].id", f"Bundle id {deviation_id!r} is duplicated."))
        all_bundle_ids.add(deviation_id)

    assets = {
        asset["id"]: asset
        for asset in bundle.get("assets", [])
        if isinstance(asset, dict) and isinstance(asset.get("id"), str)
    }
    asset_ids = set(assets)
    requirement_assets = requirement_index["byKind"].get("asset", {})
    plan_to_bundle: dict[str, str] = {}
    build_orders: dict[int, str] = {}
    layout_nodes_by_asset: dict[str, set[str]] = {}
    layout_node_records_by_asset: dict[str, dict[str, dict[str, Any]]] = {}

    for asset_id, asset in assets.items():
        plan_id = asset.get("assetPlanId")
        if plan_id in plan_to_bundle:
            errors.append(issue("asset.plan_duplicate", f"$.assets[{asset_id}].assetPlanId", "Each assetPlan item may be realized only once."))
        if isinstance(plan_id, str):
            plan_to_bundle[plan_id] = asset_id
        plan = requirement_assets.get(plan_id)
        if plan is None:
            errors.append(issue("ref.asset_plan", f"$.assets[{asset_id}].assetPlanId", f"Unknown assetPlan id: {plan_id}"))
        else:
            if plan.get("inBuildScope") is not True:
                errors.append(issue("asset.out_of_scope", f"$.assets[{asset_id}].assetPlanId", "Out-of-scope assetPlan items cannot enter a build bundle."))
            for bundle_key, plan_key in (
                ("assetPath", "assetPath"),
                ("assetKind", "assetKind"),
                ("referenceSize", "referenceSize"),
                ("layoutSpecPath", "layoutSpecPath"),
                ("buildOrder", "buildOrder"),
            ):
                if bundle_version in REUSE_BUNDLE_VERSIONS and asset.get("representationKind") == "reuse-only" and bundle_key == "layoutSpecPath":
                    continue
                if asset.get(bundle_key) != plan.get(plan_key):
                    errors.append(issue("asset.plan_mismatch", f"$.assets[{asset_id}].{bundle_key}", f"{bundle_key} must match assetPlan {plan_id}."))
        for dependency in asset.get("dependsOnAssetIds", []):
            if dependency not in asset_ids:
                errors.append(issue("ref.asset", f"$.assets[{asset_id}].dependsOnAssetIds", f"Unknown bundle asset id: {dependency}"))
            if dependency == asset_id:
                errors.append(issue("asset.self_dependency", f"$.assets[{asset_id}].dependsOnAssetIds", "An asset cannot depend on itself."))
        order = asset.get("buildOrder")
        if isinstance(order, int):
            if order in build_orders:
                errors.append(issue("asset.build_order", f"$.assets[{asset_id}].buildOrder", "buildOrder must be unique."))
            build_orders[order] = asset_id
        if asset.get("assetKind") == "screen" and asset.get("referenceSize") != [2560, 1440]:
            errors.append(issue("asset.screen_resolution", f"$.assets[{asset_id}].referenceSize", "Screen assets must use [2560, 1440]."))

        if check_linked_files and asset.get("representationKind") != "reuse-only" and isinstance(asset.get("layoutSpecPath"), str):
            layout_path = resolve_contract_path(bundle_path, asset["layoutSpecPath"])
            try:
                layout = load_json(layout_path)
            except (OSError, json.JSONDecodeError) as error:
                errors.append(issue("layout.read", f"$.assets[{asset_id}].layoutSpecPath", str(error)))
                continue
            actual_hash = sha256_file(layout_path)
            if asset.get("layoutSpecSha256") != actual_hash:
                errors.append(issue("layout.sha256", f"$.assets[{asset_id}].layoutSpecSha256", f"Layout hash mismatch; expected {actual_hash}."))
            if not isinstance(layout, dict) or layout.get("version") != "0.2":
                errors.append(issue("layout.version", f"$.assets[{asset_id}].layoutSpecPath", "Linked layout must be UILayoutSpec 0.2."))
                continue
            if _layout_asset_path(layout) != _asset_object_path(str(asset.get("assetPath"))):
                errors.append(issue("layout.asset", f"$.assets[{asset_id}].assetPath", "Bundle assetPath does not match linked UILayoutSpec asset."))
            if layout.get("referenceSize") != asset.get("referenceSize"):
                errors.append(issue("layout.reference_size", f"$.assets[{asset_id}].referenceSize", "Bundle referenceSize does not match linked UILayoutSpec."))
            profile = layout.get("profile") if isinstance(layout.get("profile"), dict) else {}
            if explicit_panel_slots_policy and profile.get("explicitPanelSlots") is not True:
                errors.append(
                    issue(
                        "layout.explicit_panel_slots",
                        f"$.assets[{asset_id}].layoutSpecPath",
                        "A requirement with explicitPanelSlotsRequired needs every linked UILayoutSpec to enable profile.explicitPanelSlots.",
                    )
                )
            asset_kind = asset.get("assetKind")
            if design_size_mode_policy:
                expected_design_size_mode = design_size_modes_by_plan.get(asset.get("assetPlanId"))
                if isinstance(expected_design_size_mode, str) and profile.get("designSizeMode") != expected_design_size_mode:
                    errors.append(
                        issue(
                            "layout.design_size_mode",
                            f"$.assets[{asset_id}].layoutSpecPath",
                            f"The accepted assetPlan designSizeModeDecision requires profile.designSizeMode {expected_design_size_mode!r}; "
                            f"found {profile.get('designSizeMode')!r}.",
                        )
                    )
                if expected_design_size_mode == "Desired":
                    fixed_root_proof = _has_fixed_root_direct_desired_size(layout)
                    content_proofs = _root_direct_content_driven_size_proofs(layout)
                    decision = plan.get("designSizeModeDecision") if isinstance(plan, dict) else {}
                    decision_evidence_ids = (
                        set(decision.get("evidenceIds", []))
                        if isinstance(decision, dict) and isinstance(decision.get("evidenceIds"), list)
                        else set()
                    )
                    content_proof = next(
                        (
                            proof
                            for proof in content_proofs
                            if proof.get("evidenceId") in decision_evidence_ids
                        ),
                        None,
                    )
                    if not fixed_root_proof and content_proof is None:
                        errors.append(
                            issue(
                                "layout.desired_root_size_proof",
                                f"$.assets[{asset_id}].layoutSpecPath",
                                "Desired requires a root-direct fixed Slot proof (autoSize false, point anchors, positive right/bottom) "
                                "or a verified positive root-direct contentDrivenSize proof bound to decision evidence.",
                            )
                        )
                        if content_proofs:
                            errors.append(
                                issue(
                                    "layout.desired_root_size_evidence",
                                    f"$.assets[{asset_id}].layoutSpecPath",
                                    "The root-direct contentDrivenSize.evidenceId must belong to the linked assetPlan designSizeModeDecision.evidenceIds.",
                                )
                            )
                if asset_kind == "screen" and profile.get("assetKind") != "screen":
                    errors.append(issue("layout.asset_kind", f"$.assets[{asset_id}].assetKind", "Screen bundle assets require a screen UILayoutSpec profile."))
                elif asset_kind == "child-widget":
                    if profile.get("assetKind") != "child-widget":
                        errors.append(issue("layout.asset_kind", f"$.assets[{asset_id}].assetKind", "Child-widget bundle assets require a child-widget UILayoutSpec profile."))
                    if profile.get("listRole") == "entry":
                        errors.append(issue("layout.list_role", f"$.assets[{asset_id}].assetKind", "A child-widget bundle asset must not use UILayoutSpec profile.listRole entry."))
                elif asset_kind == "list-entry":
                    if profile.get("assetKind") != "child-widget":
                        errors.append(issue("layout.asset_kind", f"$.assets[{asset_id}].assetKind", "List-entry bundle assets require a child-widget UILayoutSpec profile."))
                    if profile.get("listRole") != "entry":
                        errors.append(issue("layout.list_role", f"$.assets[{asset_id}].assetKind", "A list-entry bundle asset requires UILayoutSpec profile.listRole entry."))
            elif asset_kind == "screen" and profile.get("assetKind") != "screen":
                errors.append(issue("layout.asset_kind", f"$.assets[{asset_id}].assetKind", "Screen bundle assets require a screen UILayoutSpec profile."))
            node_ids: set[str] = set()
            node_records: dict[str, dict[str, Any]] = {}
            for node_index, node in enumerate(layout.get("nodes", [])):
                if not isinstance(node, dict) or not isinstance(node.get("id"), str):
                    continue
                node_id = node["id"]
                if node_id in node_ids:
                    errors.append(issue("layout.node_duplicate", f"{layout_path}:$.nodes[{node_index}].id", f"Duplicate layout node id: {node_id}"))
                node_ids.add(node_id)
                node_records[node_id] = node
            layout_nodes_by_asset[asset_id] = node_ids
            layout_node_records_by_asset[asset_id] = node_records

    if build_orders and set(build_orders) != set(range(len(assets))):
        errors.append(issue("asset.build_order_sequence", "$.assets", "buildOrder values must form a zero-based contiguous sequence."))

    # Compare translated requirement dependencies only after all plan mappings are known.
    for asset_id, asset in assets.items():
        plan = requirement_assets.get(asset.get("assetPlanId"))
        if not isinstance(plan, dict):
            continue
        missing_planned_dependencies = {
            dependency for dependency in plan.get("dependsOnAssetIds", []) if dependency not in plan_to_bundle
        }
        if missing_planned_dependencies:
            errors.append(
                issue(
                    "asset.dependency_missing",
                    f"$.assets[{asset_id}].dependsOnAssetIds",
                    f"Required assetPlan dependencies are absent from the bundle: {sorted(missing_planned_dependencies)}.",
                )
            )
        expected = {
            plan_to_bundle[plan_dependency]
            for plan_dependency in plan.get("dependsOnAssetIds", [])
            if plan_dependency in plan_to_bundle
        }
        actual = set(asset.get("dependsOnAssetIds", []))
        if expected != actual:
            errors.append(issue("asset.dependencies", f"$.assets[{asset_id}].dependsOnAssetIds", "Bundle dependencies must exactly translate assetPlan dependencies."))
        for dependency in actual:
            if assets.get(dependency, {}).get("buildOrder", 10**9) >= asset.get("buildOrder", -1):
                errors.append(issue("asset.dependency_order", f"$.assets[{asset_id}].dependsOnAssetIds", f"Dependency {dependency} must be built first."))

    execution = bundle.get("execution") if isinstance(bundle.get("execution"), dict) else {}
    ordered_assets = [asset_id for _, asset_id in sorted(build_orders.items())]
    if execution.get("buildOrderAssetIds") != ordered_assets:
        errors.append(issue("execution.build_order", "$.execution.buildOrderAssetIds", "Execution order must exactly match asset buildOrder."))

    requirement_by_id = requirement_index.get("byId", {})
    requirement_claims = requirement_index["byKind"].get("claim", {})
    states = requirement_index["byKind"].get("state", {})
    mapped_nodes_by_asset: dict[str, set[str]] = {asset_id: set() for asset_id in assets}
    for mapping_index, mapping in enumerate(bundle.get("nodeMappings", [])):
        if not isinstance(mapping, dict):
            continue
        mapping_path = f"$.nodeMappings[{mapping_index}]"
        asset_id = mapping.get("assetId")
        if asset_id not in asset_ids:
            errors.append(issue("ref.asset", f"{mapping_path}.assetId", "Unknown bundle asset id."))
            continue
        node_id = mapping.get("layoutNodeId")
        if check_linked_files and node_id not in layout_nodes_by_asset.get(asset_id, set()):
            errors.append(issue("ref.layout_node", f"{mapping_path}.layoutNodeId", f"Unknown layout node {node_id!r} for asset {asset_id}."))
        if node_id in mapped_nodes_by_asset.setdefault(asset_id, set()):
            errors.append(issue("mapping.node_duplicate", f"{mapping_path}.layoutNodeId", "Each layout node must have exactly one mapping record."))
        if isinstance(node_id, str):
            mapped_nodes_by_asset[asset_id].add(node_id)

        mapping_claims = set(mapping.get("claimIds", [])) if isinstance(mapping.get("claimIds"), list) else set()
        unknown_claims = mapping_claims - accepted_claim_ids
        if unknown_claims:
            errors.append(issue("mapping.claim_status", f"{mapping_path}.claimIds", f"Only reviewed accepted claims may enter nodeMappings: {sorted(unknown_claims)}"))
        for requirement_ref in mapping.get("requirementRefs", []):
            indexed = requirement_by_id.get(requirement_ref)
            if indexed is None:
                errors.append(issue("ref.requirement", f"{mapping_path}.requirementRefs", f"Unknown requirement id: {requirement_ref}"))
                continue
            if indexed["entity"].get("inBuildScope") is False:
                errors.append(issue("mapping.out_of_scope", f"{mapping_path}.requirementRefs", f"Out-of-scope requirement {requirement_ref} cannot be mapped."))
            entity_claims = set(indexed["entity"].get("claimIds", []))
            if not (entity_claims & mapping_claims & accepted_claim_ids):
                errors.append(issue("mapping.evidence_chain", f"{mapping_path}.requirementRefs", f"Requirement {requirement_ref} is not backed by a mapped accepted claim."))
        for state_ref in mapping.get("stateRefs", []):
            state = states.get(state_ref)
            if state is None:
                errors.append(issue("ref.state", f"{mapping_path}.stateRefs", f"Unknown state id: {state_ref}"))
                continue
            if state.get("inBuildScope") is not True:
                errors.append(issue("mapping.out_of_scope_state", f"{mapping_path}.stateRefs", f"Out-of-scope state {state_ref} cannot be mapped."))
            if not (set(state.get("claimIds", [])) & mapping_claims & accepted_claim_ids):
                errors.append(issue("mapping.state_claim", f"{mapping_path}.stateRefs", f"State {state_ref} is not backed by a mapped accepted claim."))
        mapped_subjects = {
            ref
            for ref in list(mapping.get("requirementRefs", [])) + list(mapping.get("stateRefs", []))
            if isinstance(ref, str)
        }
        for claim_id in sorted(mapping_claims):
            claim_subjects = set(requirement_claims.get(claim_id, {}).get("subjectRefs", []))
            if not (claim_subjects & mapped_subjects):
                errors.append(
                    issue(
                        "mapping.unrelated_claim",
                        f"{mapping_path}.claimIds",
                        f"Mapped claim {claim_id} does not name any requirementRefs or stateRefs on this node.",
                    )
                )

    for asset_id, node_ids in layout_nodes_by_asset.items():
        missing = node_ids - mapped_nodes_by_asset.get(asset_id, set())
        extra = mapped_nodes_by_asset.get(asset_id, set()) - node_ids
        if missing or extra:
            errors.append(issue("mapping.layout_coverage", "$.nodeMappings", f"Asset {asset_id} layout mapping mismatch; missing={sorted(missing)}, extra={sorted(extra)}."))

    if check_linked_files:
        if image_composition_policy:
            mappings = [mapping for mapping in bundle.get("nodeMappings", []) if isinstance(mapping, dict)]
            reuse_relations = [relation for relation in bundle.get("reuseRelations", []) if isinstance(relation, dict)]
            accepted_image_requirement_ids = {
                element_id
                for element_id, element in requirement_index["byKind"].get("element", {}).items()
                if element.get("kind") == "image"
                and element.get("inBuildScope") is True
                and bool(
                    set(element.get("claimIds", [])) & accepted_claim_ids
                    if isinstance(element.get("claimIds"), list)
                    else set()
                )
            }

            unique_visual_mapping_by_image: dict[str, dict[str, Any]] = {}
            unique_reuse_relation_by_image: dict[str, dict[str, Any]] = {}
            for element_id in sorted(accepted_image_requirement_ids):
                visual_mappings, nested_reuse_relations = _image_requirement_realizations(
                    element_id,
                    mappings,
                    reuse_relations,
                    layout_node_records_by_asset,
                )
                realization_count = len(visual_mappings) + len(nested_reuse_relations)
                if realization_count != 1:
                    errors.append(
                        issue(
                            "mapping.image_composition_count",
                            "$.nodeMappings",
                            f"Accepted in-scope image element {element_id} must resolve exactly once, either as one visual.image layout node or through one widget-tree-instance shared-control relation; found nodes={len(visual_mappings)}, reuseRelations={len(nested_reuse_relations)}.",
                        )
                    )
                elif visual_mappings:
                    unique_visual_mapping_by_image[element_id] = visual_mappings[0]
                else:
                    unique_reuse_relation_by_image[element_id] = nested_reuse_relations[0]

            mapping_by_layout_node = {
                (mapping.get("assetId"), mapping.get("layoutNodeId")): mapping
                for mapping in mappings
            }
            referenced_owner_intent_ids = {
                composition.get("ownerIntentId")
                for element in requirement_index["byKind"].get("element", {}).values()
                if isinstance(element, dict)
                and element.get("kind") == "image"
                and element.get("inBuildScope") is True
                and isinstance(element.get("imageComposition"), dict)
                and (composition := element["imageComposition"]).get("adaptation") == "inherit-owner"
                and isinstance(composition.get("ownerIntentId"), str)
            }
            responsive_realization_by_intent: dict[str, tuple[str, dict[str, Any]]] = {}
            for asset_id, node_records in layout_node_records_by_asset.items():
                for node_id, node in node_records.items():
                    if node.get("role") != "visual.image":
                        continue
                    mapping = mapping_by_layout_node.get((asset_id, node_id), {})
                    requirement_refs = mapping.get("requirementRefs", []) if isinstance(mapping.get("requirementRefs"), list) else []
                    mapped_image_ids = accepted_image_requirement_ids.intersection(requirement_refs)
                    if len(mapped_image_ids) != 1:
                        errors.append(
                            issue(
                                "mapping.visual_image_requirement_count",
                                "$.nodeMappings",
                                f"visual.image layout node {asset_id}/{node_id} must map exactly one accepted in-scope image element; found {sorted(mapped_image_ids)}.",
                            )
                        )

            for intent_id, intent in requirement_index["byKind"].get("responsive-intent", {}).items():
                target_element_id = intent.get("elementId")
                target_region_id = intent.get("regionId")
                target_requirement_id = (
                    target_element_id
                    if isinstance(target_element_id, str)
                    else target_region_id
                    if isinstance(target_region_id, str)
                    else None
                )
                target_element = requirement_index["byKind"].get("element", {}).get(target_element_id, {})
                intent_claim_ids = set(intent.get("claimIds", [])) if isinstance(intent.get("claimIds"), list) else set()
                image_target = target_element.get("kind") == "image"
                if (
                    intent.get("inBuildScope") is not True
                    or not isinstance(target_requirement_id, str)
                    or not (intent_claim_ids & accepted_claim_ids)
                    or (
                        not image_target
                        and (
                            not explicit_image_owner_intent_policy
                            or intent_id not in referenced_owner_intent_ids
                        )
                    )
                ):
                    continue
                target_reuse_relation = unique_reuse_relation_by_image.get(target_element_id) if image_target else None
                if not image_target:
                    owner_mappings, owner_reuse_relations = _responsive_requirement_realizations(
                        intent_id,
                        target_requirement_id,
                        mappings,
                        reuse_relations,
                    )
                    if len(owner_mappings) + len(owner_reuse_relations) != 1:
                        errors.append(
                            issue(
                                "mapping.owner_responsive_binding_count",
                                "$.nodeMappings",
                                f"Referenced owner intent {intent_id} and target {target_requirement_id} must share exactly one layout-node or widget-tree-instance realization; found nodes={len(owner_mappings)}, reuseRelations={len(owner_reuse_relations)}.",
                            )
                        )
                        continue
                    if owner_reuse_relations:
                        owner_relation = owner_reuse_relations[0]
                        owner_placement = owner_relation.get("placementContract") if isinstance(owner_relation.get("placementContract"), dict) else {}
                        owner_slot = owner_placement.get("slot") if isinstance(owner_placement.get("slot"), dict) else {}
                        expected_horizontal = {
                            "left": "Left",
                            "center": "Center",
                            "right": "Right",
                            "stretch": "Fill",
                        }.get(intent.get("horizontal"))
                        expected_vertical = {
                            "top": "Top",
                            "center": "Center",
                            "bottom": "Bottom",
                            "stretch": "Fill",
                        }.get(intent.get("vertical"))
                        if (
                            owner_slot.get("horizontalAlignment") != expected_horizontal
                            or owner_slot.get("verticalAlignment") != expected_vertical
                        ):
                            errors.append(
                                issue(
                                    "mapping.owner_responsive_adaptive",
                                    "$.reuseRelations",
                                    f"The widget-tree-instance Slot for referenced owner intent {intent_id} must copy both reviewed adaptation axes.",
                                )
                            )
                        responsive_realization_by_intent[intent_id] = ("relation", owner_relation)
                        continue
                if isinstance(target_reuse_relation, dict):
                    relation_requirements = (
                        set(target_reuse_relation.get("requirementRefs", []))
                        if isinstance(target_reuse_relation.get("requirementRefs"), list)
                        else set()
                    )
                    if not {intent_id, target_requirement_id}.issubset(relation_requirements):
                        errors.append(
                            issue(
                                "mapping.element_responsive_reuse_binding",
                                "$.reuseRelations",
                                f"A relation-backed image intent {intent_id} must share the target image's unique widget-tree-instance relation.",
                            )
                        )
                        continue
                    placement = (
                        target_reuse_relation.get("placementContract")
                        if isinstance(target_reuse_relation.get("placementContract"), dict)
                        else {}
                    )
                    slot = placement.get("slot") if isinstance(placement.get("slot"), dict) else {}
                    horizontal_alignment = {
                        "left": "Left",
                        "center": "Center",
                        "right": "Right",
                        "stretch": "Fill",
                    }.get(intent.get("horizontal"))
                    vertical_alignment = {
                        "top": "Top",
                        "center": "Center",
                        "bottom": "Bottom",
                        "stretch": "Fill",
                    }.get(intent.get("vertical"))
                    if (
                        slot.get("horizontalAlignment") != horizontal_alignment
                        or slot.get("verticalAlignment") != vertical_alignment
                    ):
                        errors.append(
                            issue(
                                "mapping.element_responsive_reuse_adaptive",
                                "$.reuseRelations",
                                f"The widget-tree-instance Slot for image intent {intent_id} must copy the reviewed horizontal and vertical adaptation.",
                            )
                        )
                    responsive_realization_by_intent[intent_id] = ("relation", target_reuse_relation)
                    continue
                matching_mappings = [
                    mapping
                    for mapping in mappings
                    if {intent_id, target_requirement_id}.issubset(
                        set(mapping.get("requirementRefs", []))
                        if isinstance(mapping.get("requirementRefs"), list)
                        else set()
                    )
                ]
                if len(matching_mappings) != 1:
                    errors.append(
                        issue(
                            "mapping.element_responsive_binding_count" if image_target else "mapping.owner_responsive_binding_count",
                            "$.nodeMappings",
                            f"Responsive intent {intent_id} and target {target_requirement_id} must share exactly one layout-node mapping; found {len(matching_mappings)}.",
                        )
                    )
                    continue
                mapping = matching_mappings[0]
                responsive_realization_by_intent[intent_id] = ("mapping", mapping)
                node = layout_node_records_by_asset.get(mapping.get("assetId"), {}).get(mapping.get("layoutNodeId"), {})
                if image_target:
                    target_visual_mapping = unique_visual_mapping_by_image.get(target_element_id)
                    target_visual_key = (
                        (target_visual_mapping.get("assetId"), target_visual_mapping.get("layoutNodeId"))
                        if isinstance(target_visual_mapping, dict)
                        else None
                    )
                    matching_key = (mapping.get("assetId"), mapping.get("layoutNodeId"))
                    if node.get("role") != "visual.image" or target_visual_key != matching_key:
                        errors.append(
                            issue(
                                "mapping.element_responsive_target_node",
                                "$.nodeMappings",
                                f"Element-targeted responsive intent {intent_id} must share the target element's unique visual.image layout node.",
                            )
                        )
                        continue
                adaptive_layout = node.get("adaptiveLayout") if isinstance(node.get("adaptiveLayout"), dict) else {}
                if (
                    adaptive_layout.get("horizontal") != intent.get("horizontal")
                    or adaptive_layout.get("vertical") != intent.get("vertical")
                ):
                    errors.append(
                        issue(
                            "mapping.element_responsive_adaptive" if image_target else "mapping.owner_responsive_adaptive",
                            "$.nodeMappings",
                            f"Layout node for responsive intent {intent_id} must copy its horizontal and vertical adaptiveLayout exactly.",
                        )
                    )

            if explicit_image_owner_intent_policy:
                for image_element_id in sorted(accepted_image_requirement_ids):
                    image_element = requirement_index["byKind"].get("element", {}).get(image_element_id, {})
                    composition = image_element.get("imageComposition") if isinstance(image_element.get("imageComposition"), dict) else {}
                    if composition.get("adaptation") != "inherit-owner":
                        continue
                    owner_intent_id = composition.get("ownerIntentId")
                    owner_realization = responsive_realization_by_intent.get(owner_intent_id)
                    image_realization: tuple[str, dict[str, Any]] | None = None
                    if image_element_id in unique_visual_mapping_by_image:
                        image_realization = ("mapping", unique_visual_mapping_by_image[image_element_id])
                    elif image_element_id in unique_reuse_relation_by_image:
                        image_realization = ("relation", unique_reuse_relation_by_image[image_element_id])
                    if owner_realization is None or image_realization is None:
                        continue
                    if not _image_realization_is_within_owner(
                        image_realization,
                        owner_realization,
                        bundle=bundle,
                        reuse_relations=reuse_relations,
                        layout_node_records_by_asset=layout_node_records_by_asset,
                    ):
                        errors.append(
                            issue(
                                "mapping.image_owner_containment",
                                "$.nodeMappings",
                                f"Inherited image {image_element_id} is not structurally contained by the exact owner realization selected by ownerIntentId {owner_intent_id!r}.",
                            )
                        )

        if explicit_panel_slots_policy:
            mappings = [mapping for mapping in bundle.get("nodeMappings", []) if isinstance(mapping, dict)]
            mapping_by_layout_node = {
                (mapping.get("assetId"), mapping.get("layoutNodeId")): mapping
                for mapping in mappings
            }
            requirement_elements = requirement_index["byKind"].get("element", {})
            for element_id, element in requirement_elements.items():
                slot_intent = element.get("panelSlotIntent")
                element_claim_ids = set(element.get("claimIds", [])) if isinstance(element.get("claimIds"), list) else set()
                if (
                    element.get("inBuildScope") is not True
                    or not isinstance(slot_intent, dict)
                    or not (element_claim_ids & accepted_claim_ids)
                ):
                    continue

                expected_field = "flowSlot" if slot_intent.get("slotType") == "flow" else "scrollSlot"
                candidate_mappings, candidate_relations = _panel_slot_realizations(
                    element_id,
                    expected_field,
                    mappings,
                    [relation for relation in bundle.get("reuseRelations", []) if isinstance(relation, dict)],
                    layout_node_records_by_asset,
                )
                if len(candidate_mappings) + len(candidate_relations) != 1:
                    errors.append(
                        issue(
                            "mapping.panel_slot_count",
                            "$.nodeMappings",
                            f"Accepted element {element_id} with panelSlotIntent must resolve exactly once through either one layout node carrying {expected_field} or one widget-tree-instance placement Slot; found nodes={len(candidate_mappings)}, reuseRelations={len(candidate_relations)}.",
                        )
                    )
                    continue

                expected_slot = {
                    key: copy_value
                    for key, copy_value in slot_intent.items()
                    if key not in {"slotType", "sizingBasis", "reason"}
                }
                parent_element_id = element.get("parentElementId")
                parent_element = requirement_elements.get(parent_element_id, {})
                expected_parent_role = parent_element.get("layoutRole")

                if candidate_relations:
                    relation = candidate_relations[0]
                    placement = relation.get("placementContract") if isinstance(relation.get("placementContract"), dict) else {}
                    actual_slot = placement.get("slot") if isinstance(placement.get("slot"), dict) else {}
                    expected_relation_slot = _relation_slot_from_panel_intent(slot_intent, expected_parent_role)
                    actual_semantic_slot = {
                        key: value
                        for key, value in actual_slot.items()
                        if key not in {"parentWidgetName", "parentTreePath"}
                    }
                    if actual_semantic_slot != expected_relation_slot:
                        errors.append(
                            issue(
                                "mapping.panel_slot_values",
                                "$.reuseRelations",
                                f"The widget-tree-instance placement Slot for element {element_id} must copy its reviewed parent type, Box Size when applicable, Padding, and alignments exactly.",
                            )
                        )
                    if slot_intent.get("slotType") == "flow":
                        expected_sizing_strategy = slot_intent.get("sizingBasis")
                        if placement.get("sizingStrategy") != expected_sizing_strategy:
                            errors.append(
                                issue(
                                    "mapping.panel_slot_sizing_strategy",
                                    "$.reuseRelations",
                                    f"The widget-tree-instance sizingStrategy for {element_id} must be {expected_sizing_strategy!r} so Box allocation is not confused with Slot alignment.",
                                )
                            )
                    elif placement.get("sizingStrategy") != "scroll-slot":
                        errors.append(
                            issue(
                                "mapping.panel_slot_sizing_strategy",
                                "$.reuseRelations",
                                f"The widget-tree-instance sizingStrategy for scroll child {element_id} must be 'scroll-slot'; GameScrollBox has no Box Size allocation.",
                            )
                        )
                    relation_requirements = (
                        relation.get("requirementRefs", [])
                        if isinstance(relation.get("requirementRefs"), list)
                        else []
                    )
                    if parent_element_id not in relation_requirements:
                        errors.append(
                            issue(
                                "mapping.panel_slot_parent",
                                "$.reuseRelations",
                                f"The widget-tree-instance placement for {element_id} must also reference its immediate parent element {parent_element_id}.",
                            )
                        )
                    relation_target_id = relation.get("targetAssetId")
                    parent_mappings = [
                        parent_mapping
                        for parent_mapping in mappings
                        if parent_mapping.get("assetId") == relation_target_id
                        and parent_element_id
                        in (
                            parent_mapping.get("requirementRefs", [])
                            if isinstance(parent_mapping.get("requirementRefs"), list)
                            else []
                        )
                    ]
                    if len(parent_mappings) != 1:
                        errors.append(
                            issue(
                                "mapping.panel_slot_parent_identity",
                                "$.reuseRelations",
                                f"The immediate parent element {parent_element_id} for nested child {element_id} must have exactly one node mapping in the relation target asset; found {len(parent_mappings)}.",
                            )
                        )
                    else:
                        parent_mapping = parent_mappings[0]
                        parent_node = layout_node_records_by_asset.get(relation_target_id, {}).get(
                            parent_mapping.get("layoutNodeId"),
                            {},
                        )
                        expected_parent_widget_name = parent_node.get("name")
                        target_asset = assets.get(relation_target_id, {})
                        expected_parent_tree_path = (
                            _widget_tree_path(str(target_asset.get("assetPath")), expected_parent_widget_name)
                            if isinstance(expected_parent_widget_name, str)
                            else None
                        )
                        if (
                            parent_node.get("role") != expected_parent_role
                            or actual_slot.get("parentWidgetName") != expected_parent_widget_name
                            or actual_slot.get("parentTreePath") != expected_parent_tree_path
                        ):
                            errors.append(
                                issue(
                                    "mapping.panel_slot_parent_identity",
                                    "$.reuseRelations",
                                    f"The nested child {element_id} placement Slot must identify the mapped immediate parent Widget and tree path in targetAssetId {relation_target_id}.",
                                )
                            )
                    continue

                mapping = candidate_mappings[0]
                asset_id = mapping.get("assetId")
                node_id = mapping.get("layoutNodeId")
                node = layout_node_records_by_asset.get(asset_id, {}).get(node_id, {})
                actual_slot = node.get(expected_field)
                if actual_slot != expected_slot:
                    errors.append(
                        issue(
                            "mapping.panel_slot_values",
                            "$.nodeMappings",
                            f"Layout node {asset_id}/{node_id} must copy element {element_id}'s reviewed Slot size, padding, and alignments exactly.",
                        )
                    )

                parent_node_id = node.get("parent")
                parent_node = layout_node_records_by_asset.get(asset_id, {}).get(parent_node_id, {})
                parent_mapping = mapping_by_layout_node.get((asset_id, parent_node_id), {})
                parent_requirement_refs = (
                    parent_mapping.get("requirementRefs", [])
                    if isinstance(parent_mapping.get("requirementRefs"), list)
                    else []
                )
                if parent_node.get("role") != expected_parent_role or parent_element_id not in parent_requirement_refs:
                    errors.append(
                        issue(
                            "mapping.panel_slot_parent",
                            "$.nodeMappings",
                            f"Layout node {asset_id}/{node_id} must remain a direct child of the mapped {expected_parent_role} owner {parent_element_id}.",
                        )
                    )

        for model_id, button_element_ids in interactive_button_requirements.items():
            for button_element_id in sorted(button_element_ids):
                button_mappings = [
                    (mapping_index, mapping)
                    for mapping_index, mapping in enumerate(bundle.get("nodeMappings", []))
                    if isinstance(mapping, dict) and button_element_id in mapping.get("requirementRefs", [])
                ]
                if not button_mappings:
                    errors.append(
                        issue(
                            "mapping.control_input_button_missing",
                            "$.nodeMappings",
                            f"User-interaction state model {model_id} Button element {button_element_id} has no node mapping.",
                        )
                    )
                    continue
                for mapping_index, mapping in button_mappings:
                    node = layout_node_records_by_asset.get(mapping.get("assetId"), {}).get(mapping.get("layoutNodeId"))
                    if isinstance(node, dict) and node.get("role") != "input.button":
                        errors.append(
                            issue(
                                "mapping.control_input_button_role",
                                f"$.nodeMappings[{mapping_index}].layoutNodeId",
                                f"Mapping for user-interaction Button element {button_element_id} must resolve to a UILayoutSpec node with role input.button.",
                            )
                        )

    mapped_requirement_refs = {
        requirement_ref
        for mapping in bundle.get("nodeMappings", [])
        if isinstance(mapping, dict)
        for requirement_ref in mapping.get("requirementRefs", [])
        if isinstance(requirement_ref, str)
    }
    mapped_state_refs = {
        state_ref
        for mapping in bundle.get("nodeMappings", [])
        if isinstance(mapping, dict)
        for state_ref in mapping.get("stateRefs", [])
        if isinstance(state_ref, str)
    }
    if bundle_version in REUSE_BUNDLE_VERSIONS:
        mapped_requirement_refs.update(
            requirement_ref
            for relation in bundle.get("reuseRelations", [])
            if isinstance(relation, dict)
            for requirement_ref in relation.get("requirementRefs", [])
            if isinstance(requirement_ref, str)
        )
    coverage_sets = {
        "region": mapped_requirement_refs,
        "element": mapped_requirement_refs,
        "collection": mapped_requirement_refs,
        "runtime-field": mapped_requirement_refs,
        "responsive-intent": mapped_requirement_refs,
        "state": mapped_state_refs,
        "acceptance-criterion": {
            requirement_ref
            for check in verification.get("checks", [])
            if isinstance(check, dict)
            for requirement_ref in check.get("requirementRefs", [])
            if isinstance(requirement_ref, str)
        },
    }
    for kind, mapped_ids in coverage_sets.items():
        for entity_id, entity in requirement_index["byKind"].get(kind, {}).items():
            if entity.get("inBuildScope") is True and entity_id not in mapped_ids:
                errors.append(issue("coverage.missing", "$.nodeMappings", f"In-scope {kind} {entity_id} has no node mapping."))
    for plan_id, plan in requirement_assets.items():
        if plan.get("inBuildScope") is True and plan_id not in plan_to_bundle:
            errors.append(issue("coverage.asset_missing", "$.assets", f"In-scope assetPlan {plan_id} has no bundle asset."))

    for operation_index, operation in enumerate(bundle.get("crossAssetOperations", [])):
        if not isinstance(operation, dict):
            continue
        operation_path = f"$.crossAssetOperations[{operation_index}]"
        source_id = operation.get("sourceAssetId")
        target_id = operation.get("targetAssetId")
        if source_id not in asset_ids:
            errors.append(issue("ref.asset", f"{operation_path}.sourceAssetId", "Unknown source asset."))
        if target_id not in asset_ids:
            errors.append(issue("ref.asset", f"{operation_path}.targetAssetId", "Unknown target asset."))
        if source_id == target_id:
            errors.append(issue("operation.same_asset", operation_path, "Cross-asset operation requires different source and target assets."))
        if source_id in asset_ids and target_id in asset_ids and source_id not in set(assets[target_id].get("dependsOnAssetIds", [])):
            errors.append(issue("operation.dependency", operation_path, "Target asset must directly depend on the source asset."))
        if check_linked_files and operation.get("targetLayoutNodeId") not in layout_nodes_by_asset.get(target_id, set()):
            errors.append(issue("ref.layout_node", f"{operation_path}.targetLayoutNodeId", "Unknown target layout node."))
        operation_claims = set(operation.get("claimIds", []))
        if not operation_claims or not operation_claims.issubset(accepted_claim_ids):
            errors.append(issue("operation.claim_status", f"{operation_path}.claimIds", "Cross-asset operations require reviewed accepted claims."))
        target_mapping = next(
            (
                mapping
                for mapping in bundle.get("nodeMappings", [])
                if isinstance(mapping, dict)
                and mapping.get("assetId") == target_id
                and mapping.get("layoutNodeId") == operation.get("targetLayoutNodeId")
            ),
            None,
        )
        operation_subjects = {
            assets.get(source_id, {}).get("assetPlanId"),
            assets.get(target_id, {}).get("assetPlanId"),
        }
        if isinstance(target_mapping, dict):
            operation_subjects.update(target_mapping.get("requirementRefs", []))
            operation_subjects.update(target_mapping.get("stateRefs", []))
        for claim_id in sorted(operation_claims):
            if not (set(requirement_claims.get(claim_id, {}).get("subjectRefs", [])) & operation_subjects):
                errors.append(issue("operation.unrelated_claim", f"{operation_path}.claimIds", f"Operation claim {claim_id} names neither participating assetPlan nor target requirement/state."))

        operation_type = operation.get("type")
        strategy = operation.get("integrationStrategy")
        expected_strategy = {
            "child-widget-integration": "create-child-widget",
            "entry-widget-class": "set-entry-widget-class",
            "instance-state-initialization": "initialize-instance-state",
        }.get(operation_type)
        if strategy != expected_strategy:
            errors.append(
                issue(
                    "operation.integration_strategy",
                    f"{operation_path}.integrationStrategy",
                    f"{operation_type} must use {expected_strategy}; placeholder/template replacement is not a production integration strategy.",
                )
            )
        if operation_type == "child-widget-integration":
            if assets.get(source_id, {}).get("assetKind") != "child-widget":
                errors.append(issue("operation.child_source_kind", f"{operation_path}.sourceAssetId", "child-widget-integration must source a child-widget asset."))
            placement = operation.get("placementContract")
            if not isinstance(placement, dict):
                errors.append(issue("operation.placement_missing", f"{operation_path}.placementContract", "Child-widget integration requires an explicit placementContract."))
            elif check_linked_files:
                target_node = layout_node_records_by_asset.get(target_id, {}).get(operation.get("targetLayoutNodeId"))
                if not isinstance(target_node, dict):
                    errors.append(issue("operation.placement_target", f"{operation_path}.targetLayoutNodeId", "Placement target must resolve to a linked UILayoutSpec node."))
                else:
                    target_rect = target_node.get("rect")
                    rect_delta = _rect_delta(placement.get("hostNormalizedRect"), target_rect)
                    if rect_delta is None or rect_delta > RECT_TOLERANCE:
                        errors.append(issue("operation.placement_rect", f"{operation_path}.placementContract.hostNormalizedRect", "Placement hostNormalizedRect must match the target layout node rect."))
                    expected_size = _expected_host_size(target_rect, assets.get(target_id, {}).get("referenceSize"))
                    if expected_size is None or placement.get("hostSize") != expected_size:
                        errors.append(issue("operation.placement_size", f"{operation_path}.placementContract.hostSize", "Placement hostSize must match the target rect on the target asset design canvas."))
                    if placement.get("zOrder") != target_node.get("zOrder", 0):
                        errors.append(issue("operation.placement_z_order", f"{operation_path}.placementContract.zOrder", "Placement zOrder must match the target layout node."))
                slot = placement.get("slot") if isinstance(placement, dict) else {}
                if placement.get("sizingStrategy") == "fill-host" and isinstance(slot, dict):
                    if slot.get("horizontalAlignment") != "Fill" or slot.get("verticalAlignment") != "Fill" or slot.get("padding") != [0, 0, 0, 0]:
                        errors.append(issue("operation.placement_fill", f"{operation_path}.placementContract.slot", "fill-host placement requires Fill alignment and zero padding."))
                compatibility = placement.get("childSizingCompatibility") if isinstance(placement, dict) else {}
                if not isinstance(compatibility, dict):
                    errors.append(issue("operation.child_sizing_missing", f"{operation_path}.placementContract.childSizingCompatibility", "Child-widget integration requires machine-checkable child sizing compatibility."))
                else:
                    axes = set(compatibility.get("axes", []))
                    if axes != {"horizontal", "vertical"}:
                        errors.append(issue("operation.child_sizing_axes", f"{operation_path}.placementContract.childSizingCompatibility.axes", "A child placed in a two-dimensional host must prove compatibility on both axes."))
                    source_nodes = compatibility.get("sourceLayoutNodeIds", [])
                    source_node_records = layout_node_records_by_asset.get(source_id, {})
                    source_records = [source_node_records.get(node_id) for node_id in source_nodes]
                    if len(source_records) != len(source_nodes) or any(not isinstance(node, dict) for node in source_records):
                        errors.append(issue("operation.child_sizing_source", f"{operation_path}.placementContract.childSizingCompatibility.sourceLayoutNodeIds", "Child sizing compatibility must cite nodes in the source UILayoutSpec."))
                    elif compatibility.get("mode") == "host-equals-child-reference":
                        if placement.get("hostSize") != assets.get(source_id, {}).get("referenceSize"):
                            errors.append(issue("operation.child_sizing_fixed_mismatch", f"{operation_path}.placementContract.childSizingCompatibility", "Fixed child sizing requires hostSize to equal the child referenceSize."))
                    elif compatibility.get("mode") == "source-root-stretch":
                        if not any(
                            node.get("parent") is None
                            and _supports_stretch_axis(node, "horizontal")
                            and _supports_stretch_axis(node, "vertical")
                            for node in source_records
                        ):
                            errors.append(issue("operation.child_sizing_root_stretch", f"{operation_path}.placementContract.childSizingCompatibility", "source-root-stretch requires a cited source root with executable horizontal and vertical stretch evidence (zero-offset stretch slot or adaptiveLayout stretch)."))
                    elif compatibility.get("mode") == "source-flow-axis":
                        for axis in ("horizontal", "vertical"):
                            if not any(
                                str(node.get("role", "")).startswith(("container.", "collection."))
                                and _supports_stretch_axis(node, axis)
                                for node in source_records
                            ):
                                errors.append(issue("operation.child_sizing_flow_axis", f"{operation_path}.placementContract.childSizingCompatibility", f"source-flow-axis requires cited adaptive flow/stretch evidence on the {axis} axis."))
                    elif compatibility.get("mode") == "explicit-scalebox":
                        if not any("scale" in str(node.get("role", "")).lower() for node in source_records):
                            errors.append(issue("operation.child_sizing_scale", f"{operation_path}.placementContract.childSizingCompatibility", "explicit-scalebox requires a cited ScaleBox layout node."))
                    elif compatibility.get("mode") in {
                        "inherited-reuse-only-full-stretch",
                        "inherited-reuse-only-flow-slot",
                        "inherited-reuse-only-scroll-slot",
                    }:
                        errors.append(
                            issue(
                                "operation.child_sizing_reuse_relation_only",
                                f"{operation_path}.placementContract.childSizingCompatibility",
                                "Inherited reuse-only sizing is auditable only on a widget-tree-instance reuseRelation with explicit Parent Class and prototype-extension relation IDs.",
                            )
                        )
        elif operation_type == "entry-widget-class":
            if assets.get(source_id, {}).get("assetKind") != "list-entry":
                errors.append(issue("operation.entry_source_kind", f"{operation_path}.sourceAssetId", "entry-widget-class must source a list-entry asset."))

    if bundle_version in REUSE_BUNDLE_VERSIONS:
        errors.extend(
            _validate_reuse_relations(
                bundle,
                assets=assets,
                accepted_claim_ids=accepted_claim_ids,
                requirement_claims=requirement_claims,
                requirement_by_id=requirement_by_id,
                verification=verification,
                bundle_path=bundle_path,
                check_linked_files=check_linked_files,
            )
        )
        reuse_targets: dict[str, list[dict[str, Any]]] = {}
        for relation in bundle.get("reuseRelations", []):
            if isinstance(relation, dict) and isinstance(relation.get("targetAssetId"), str):
                reuse_targets.setdefault(relation["targetAssetId"], []).append(relation)
        for asset_id, asset in assets.items():
            if asset.get("representationKind") != "reuse-only":
                continue
            target_relations = reuse_targets.get(asset_id, [])
            valid_shared = any(
                relation.get("type") == "shared-prototype-extension" and relation.get("sourceAssetId") == asset_id
                for relation in target_relations
            )
            if bundle_version == "0.2":
                valid_child = any(
                    relation.get("type") == "class-settings-parent-class"
                    and isinstance(relation.get("inheritedSlot"), dict)
                    and relation["inheritedSlot"].get("contentMode") == "empty"
                    for relation in target_relations
                )
            else:
                valid_child = any(
                    relation.get("type") == "class-settings-parent-class"
                    and isinstance(relation.get("inheritedSlots"), list)
                    and len(relation["inheritedSlots"]) == 2
                    and all(
                        isinstance(inherited_slot, dict) and inherited_slot.get("contentMode") == "empty"
                        for inherited_slot in relation["inheritedSlots"]
                    )
                    for relation in target_relations
                )
            if not target_relations:
                errors.append(issue("reuse.asset_relation_missing", f"$.assets[{asset_id}]", "A reuse-only asset requires a reuse relation that targets it."))
            elif not (valid_shared or valid_child):
                errors.append(issue("reuse.asset_representation", f"$.assets[{asset_id}].representationKind", "reuse-only is permitted only for an in-place shared prototype extension or an empty-slot Parent Class child."))

    deviations = {
        deviation.get("id"): deviation
        for deviation in verification.get("deviations", [])
        if isinstance(deviation, dict) and isinstance(deviation.get("id"), str)
    }
    for deviation_index, deviation in enumerate(verification.get("deviations", [])):
        if not isinstance(deviation, dict):
            continue
        deviation_path = f"$.verification.deviations[{deviation_index}]"
        for requirement_ref in deviation.get("affectedRequirementRefs", []):
            if requirement_ref not in requirement_by_id:
                errors.append(issue("ref.requirement", f"{deviation_path}.affectedRequirementRefs", f"Unknown requirement id: {requirement_ref}"))
        for asset_ref in deviation.get("affectedAssetIds", []):
            if asset_ref not in asset_ids:
                errors.append(issue("ref.asset", f"{deviation_path}.affectedAssetIds", f"Unknown bundle asset id: {asset_ref}"))
        if deviation.get("impact") == "high" and deviation.get("status") != "accepted":
            errors.append(issue("deviation.high_approval", deviation_path, "High-impact deviations require explicit accepted approval."))
        if deviation.get("status") == "accepted" and (
            not isinstance(deviation.get("approvedBy"), str) or not isinstance(deviation.get("approvedAt"), str)
        ):
            errors.append(issue("deviation.audit", deviation_path, "Accepted deviations require approvedBy and approvedAt."))

    mappings_by_target: dict[tuple[str, str], dict[str, Any]] = {}
    for mapping in bundle.get("nodeMappings", []):
        if isinstance(mapping, dict):
            mappings_by_target[(mapping.get("assetId"), mapping.get("layoutNodeId"))] = mapping
    operations_by_target: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for operation in bundle.get("crossAssetOperations", []):
        if isinstance(operation, dict):
            operations_by_target.setdefault(
                (operation.get("targetAssetId"), operation.get("targetLayoutNodeId")), []
            ).append(operation)

    state_assignments: list[dict[str, Any]] = []
    state_model_by_state: dict[str, str] = {}
    state_composition_by_state: dict[str, set[str]] = {}
    for model in requirement_spec.get("stateModels", []) if isinstance(requirement_spec, dict) else []:
        if isinstance(model, dict):
            state_assignments.extend(
                assignment for assignment in model.get("stateAssignments", []) if isinstance(assignment, dict)
            )
            for axis in model.get("axes", []):
                if not isinstance(axis, dict):
                    continue
                for state in axis.get("states", []):
                    if not isinstance(state, dict) or not isinstance(state.get("id"), str):
                        continue
                    state_id = state["id"]
                    state_model_by_state[state_id] = model.get("id")
                    composition = state.get("composition") if isinstance(state.get("composition"), dict) else {}
                    state_composition_by_state[state_id] = {
                        element_id for element_id in composition.get("elementIds", []) if isinstance(element_id, str)
                    }
    assignments_by_element: dict[str, list[dict[str, Any]]] = {}
    for assignment in state_assignments:
        element_id = assignment.get("elementId")
        if isinstance(element_id, str):
            assignments_by_element.setdefault(element_id, []).append(assignment)
    for element_id, assignments in assignments_by_element.items():
        if len(assignments) != 1:
            errors.append(issue("state.assignment_duplicate", "$.requirement", f"Requirement element {element_id} has multiple stateAssignments."))
    for assignment in state_assignments:
        element_id = assignment.get("elementId")
        expected_states = set(assignment.get("axisStateIds", []))
        target_mappings = [
            mapping
            for mapping in bundle.get("nodeMappings", [])
            if isinstance(mapping, dict) and element_id in mapping.get("requirementRefs", [])
        ]
        handling_operations: list[dict[str, Any]] = []
        for mapping in target_mappings:
            handling_operations.extend(
                operation
                for operation in operations_by_target.get((mapping.get("assetId"), mapping.get("layoutNodeId")), [])
                if isinstance(operation.get("stateHandling"), dict)
            )
        if len(handling_operations) != 1:
            errors.append(
                issue(
                    "state.assignment_handling",
                    "$.crossAssetOperations",
                    f"State assignment for {element_id} requires exactly one executable or explicitly deferred stateHandling operation.",
                )
            )
            continue
        handling = handling_operations[0]["stateHandling"]
        if set(handling.get("stateRefs", [])) != expected_states:
            errors.append(issue("state.assignment_refs", "$.crossAssetOperations", f"stateHandling for {element_id} must exactly match its state assignment."))

    for operation_index, operation in enumerate(bundle.get("crossAssetOperations", [])):
        if not isinstance(operation, dict):
            continue
        operation_path = f"$.crossAssetOperations[{operation_index}]"
        mapping = mappings_by_target.get((operation.get("targetAssetId"), operation.get("targetLayoutNodeId")))
        target_states = set(mapping.get("stateRefs", [])) if isinstance(mapping, dict) else set()
        handling = operation.get("stateHandling")
        if target_states and not isinstance(handling, dict):
            errors.append(issue("state.handling_required", f"{operation_path}.stateHandling", "Child instances with stateRefs require stateHandling."))
            continue
        if not isinstance(handling, dict):
            continue
        if set(handling.get("stateRefs", [])) != target_states:
            errors.append(issue("state.handling_refs", f"{operation_path}.stateHandling.stateRefs", "stateHandling stateRefs must match the target instance mapping."))
        mapping_requirement_refs = mapping.get("requirementRefs", []) if isinstance(mapping, dict) else []
        assigned_target_elements = [
            requirement_ref
            for requirement_ref in mapping_requirement_refs
            if requirement_ref in assignments_by_element
        ]
        if len(assigned_target_elements) != 1:
            errors.append(
                issue(
                    "state.assignment_missing",
                    f"{operation_path}.stateHandling",
                    "A state-handled target mapping must name exactly one requirement element with a unique stateAssignment.",
                )
            )
        else:
            assignment_states = set(assignments_by_element[assigned_target_elements[0]][0].get("axisStateIds", []))
            if assignment_states != target_states:
                errors.append(issue("state.assignment_reverse_refs", f"{operation_path}.stateHandling", "Target mapping and stateHandling must exactly match the target element stateAssignment."))
        strategy = handling.get("strategy")
        if strategy == "runtime-dependent":
            if not isinstance(handling.get("previewExclusionReason"), str):
                errors.append(issue("state.runtime_preview", f"{operation_path}.stateHandling", "runtime-dependent state requires previewExclusionReason."))
            deviation = deviations.get(handling.get("deviationId"))
            if deviation is None or deviation.get("status") != "accepted":
                errors.append(issue("state.runtime_deviation", f"{operation_path}.stateHandling.deviationId", "runtime-dependent state requires an accepted deviation."))
        elif strategy == "instance-parameter":
            if operation.get("type") != "instance-state-initialization" or not isinstance(handling.get("parameterName"), str):
                errors.append(issue("state.instance_parameter", f"{operation_path}.stateHandling", "instance-parameter requires an instance-state-initialization operation and parameterName."))
        elif strategy == "static-variant-asset":
            source_asset_id = operation.get("sourceAssetId")
            if operation.get("type") != "child-widget-integration" or assets.get(source_asset_id, {}).get("assetKind") != "child-widget":
                errors.append(issue("state.static_variant_operation", operation_path, "static-variant-asset requires child-widget-integration from a child-widget asset."))
            source_plan = requirement_assets.get(assets.get(source_asset_id, {}).get("assetPlanId"), {})
            source_mappings = [
                item
                for item in bundle.get("nodeMappings", [])
                if isinstance(item, dict) and item.get("assetId") == source_asset_id
            ]
            source_state_refs = {
                state_ref
                for item in source_mappings
                for state_ref in item.get("stateRefs", [])
                if isinstance(state_ref, str)
            }
            if source_state_refs != target_states:
                errors.append(
                    issue(
                        "state.static_variant",
                        f"{operation_path}.stateHandling",
                        "A static variant source asset must map exactly the assigned stateRefs and no mutually exclusive sibling state.",
                    )
                )
            planned_models = set(source_plan.get("coversStateModelIds", [])) if isinstance(source_plan, dict) else set()
            required_models = {state_model_by_state[state_ref] for state_ref in target_states if state_ref in state_model_by_state}
            if not required_models or not required_models.issubset(planned_models):
                errors.append(
                    issue(
                        "state.static_variant_plan",
                        f"{operation_path}.sourceAssetId",
                        "A static variant source asset must declare every assigned state model in assetPlan coverage.",
                    )
                )
            source_requirement_refs = {
                ref
                for item in source_mappings
                if set(item.get("stateRefs", [])) & target_states
                for ref in item.get("requirementRefs", [])
                if isinstance(ref, str)
            }
            required_elements = set().union(*(state_composition_by_state.get(state_ref, set()) for state_ref in target_states)) if target_states else set()
            if not required_elements.issubset(source_requirement_refs):
                errors.append(issue("state.static_variant_complete", f"{operation_path}.stateHandling", "Static variant mappings must cover the complete assigned state composition."))
        elif strategy == "owning-screen-state-tree":
            target_asset_id = operation.get("targetAssetId")
            if operation.get("type") != "child-widget-integration" or assets.get(target_asset_id, {}).get("assetKind") != "screen":
                errors.append(issue("state.owning_screen_operation", operation_path, "owning-screen-state-tree requires child-widget-integration into a screen asset."))
            target_plan = requirement_assets.get(assets.get(target_asset_id, {}).get("assetPlanId"), {})
            required_models = {state_model_by_state[state_ref] for state_ref in target_states if state_ref in state_model_by_state}
            if not required_models.issubset(set(target_plan.get("coversStateModelIds", []))):
                errors.append(issue("state.owning_screen_plan", f"{operation_path}.targetAssetId", "The owning screen assetPlan must cover every state model realized in its tree."))
            for state_ref in target_states:
                target_state_nodes = [
                    item
                    for item in bundle.get("nodeMappings", [])
                    if isinstance(item, dict)
                    and item.get("assetId") == target_asset_id
                    and state_ref in item.get("stateRefs", [])
                    and item.get("layoutNodeId") != operation.get("targetLayoutNodeId")
                ]
                if not target_state_nodes:
                    errors.append(issue("state.owning_screen_tree", f"{operation_path}.stateHandling", f"State {state_ref} has no branch nodes in the owning screen asset."))
                target_requirement_refs = {
                    ref
                    for item in target_state_nodes
                    for ref in item.get("requirementRefs", [])
                    if isinstance(ref, str)
                }
                if not state_composition_by_state.get(state_ref, set()).issubset(target_requirement_refs):
                    errors.append(issue("state.owning_screen_complete", f"{operation_path}.stateHandling", f"State {state_ref} does not map its complete branch composition in the owning screen asset."))
            source_state_nodes = [
                item
                for item in bundle.get("nodeMappings", [])
                if isinstance(item, dict)
                and item.get("assetId") == operation.get("sourceAssetId")
                and set(item.get("stateRefs", [])) & target_states
            ]
            if source_state_nodes:
                errors.append(
                    issue(
                        "state.owning_screen_source_tree",
                        f"{operation_path}.stateHandling",
                        "owning-screen-state-tree must be realized by complete target-screen branches, not the source child's default state tree.",
                    )
                )

    passed_preview_asset_ids: set[str] = set()
    for check_index, check in enumerate(verification.get("checks", [])):
        if not isinstance(check, dict):
            continue
        check_path = f"$.verification.checks[{check_index}]"
        if check.get("assetId") is not None and check.get("assetId") not in asset_ids:
            errors.append(issue("ref.asset", f"{check_path}.assetId", "Unknown verification asset id."))
        check_claims = set(check.get("claimIds", []))
        if not check_claims.issubset(accepted_claim_ids):
            errors.append(issue("verification.claim_status", f"{check_path}.claimIds", "Verification checks may only cite reviewed accepted claims."))
        for requirement_ref in check.get("requirementRefs", []):
            indexed = requirement_by_id.get(requirement_ref)
            if indexed is None:
                errors.append(issue("ref.requirement", f"{check_path}.requirementRefs", f"Unknown requirement id: {requirement_ref}"))
                continue
            if indexed["entity"].get("inBuildScope") is False:
                errors.append(issue("verification.out_of_scope", f"{check_path}.requirementRefs", f"Out-of-scope requirement {requirement_ref} cannot be verified."))
            entity_claims = set(indexed["entity"].get("claimIds", []))
            if not (entity_claims & check_claims & accepted_claim_ids):
                errors.append(issue("verification.evidence_chain", f"{check_path}.requirementRefs", f"Verification requirement {requirement_ref} lacks a matching accepted claim."))
        check_subjects = {ref for ref in check.get("requirementRefs", []) if isinstance(ref, str)}
        for claim_id in sorted(check_claims):
            if not (set(requirement_claims.get(claim_id, {}).get("subjectRefs", [])) & check_subjects):
                errors.append(issue("verification.unrelated_claim", f"{check_path}.claimIds", f"Verification claim {claim_id} names none of this check's requirementRefs."))

        if check.get("type") == "preview" and check.get("status") == "passed":
            if isinstance(check.get("assetId"), str):
                passed_preview_asset_ids.add(check["assetId"])
            audit = check.get("previewAudit")
            if not isinstance(audit, dict):
                errors.append(issue("preview.audit_missing", f"{check_path}.previewAudit", "A passed preview requires structured previewAudit evidence, not prose and a screenshot path alone."))
                continue
            audit_asset_id = audit.get("targetAssetId")
            if audit_asset_id not in asset_ids:
                errors.append(issue("preview.audit_asset", f"{check_path}.previewAudit.targetAssetId", "previewAudit targetAssetId must name a bundle asset."))
            if check.get("assetId") != audit_asset_id:
                errors.append(issue("preview.audit_target", f"{check_path}.previewAudit.targetAssetId", "Passed preview audit must target the verification check asset."))
            audited_asset = assets.get(audit_asset_id, {})
            viewport = audit.get("viewport") if isinstance(audit.get("viewport"), list) else []
            canvas = audit.get("canvas") if isinstance(audit.get("canvas"), dict) else {}
            canvas_size = canvas.get("pixelSize") if isinstance(canvas.get("pixelSize"), list) else []
            canvas_aspect = canvas.get("aspectRatio")
            if len(canvas_size) != 2 or not all(isinstance(value, int) and value > 0 for value in canvas_size):
                errors.append(issue("preview.canvas_size", f"{check_path}.previewAudit.canvas.pixelSize", "Preview audit requires a nonzero canvas pixel size."))
            elif canvas_size[0] < 512 or canvas_size[1] < 288:
                errors.append(issue("preview.canvas_effective_size", f"{check_path}.previewAudit.canvas.pixelSize", "Preview canvas is too small to support a reliable layout audit."))
            elif not isinstance(canvas_aspect, (int, float)) or abs(float(canvas_aspect) - (canvas_size[0] / canvas_size[1])) > 0.01:
                errors.append(issue("preview.canvas_aspect", f"{check_path}.previewAudit.canvas.aspectRatio", "Preview canvas aspectRatio must match its pixelSize."))
            if audited_asset.get("assetKind") == "screen":
                if viewport != [2560, 1440]:
                    errors.append(issue("preview.screen_viewport", f"{check_path}.previewAudit.viewport", "Screen preview audits must use the 2560x1440 design viewport."))
                elif len(canvas_size) == 2 and canvas_size[1] and abs((canvas_size[0] / canvas_size[1]) - (2560 / 1440)) > 0.01:
                    errors.append(issue("preview.screen_aspect", f"{check_path}.previewAudit.canvas", "Screen preview canvas must preserve the 2560x1440 aspect ratio."))
            if audit.get("modalOrMultipleWindowContamination") is not False:
                errors.append(issue("preview.window_contamination", f"{check_path}.previewAudit.modalOrMultipleWindowContamination", "Passed preview audits must exclude modal and multi-window contamination."))
            if not audit.get("geometryComparisons"):
                errors.append(issue("preview.geometry_missing", f"{check_path}.previewAudit.geometryComparisons", "Passed preview audits require at least one geometry comparison."))
            compared_regions: set[str] = set()
            for comparison_index, comparison in enumerate(audit.get("geometryComparisons", [])):
                if not isinstance(comparison, dict):
                    continue
                comparison_path = f"{check_path}.previewAudit.geometryComparisons[{comparison_index}]"
                requirement_ref = comparison.get("requirementRef")
                indexed = requirement_by_id.get(requirement_ref)
                if indexed is None or indexed.get("kind") != "region":
                    errors.append(issue("preview.geometry_requirement", f"{comparison_path}.requirementRef", "Geometry comparisons must cite a requirement region."))
                    continue
                compared_regions.add(requirement_ref)
                expected_rect = indexed["entity"].get("bounds")
                if _rect_delta(comparison.get("expectedNormalizedRect"), expected_rect) not in (0.0,):
                    errors.append(issue("preview.geometry_expected", f"{comparison_path}.expectedNormalizedRect", "Preview expectedNormalizedRect must equal the cited requirement region bounds."))
                actual_delta = _rect_delta(comparison.get("expectedNormalizedRect"), comparison.get("actualNormalizedRect"))
                if actual_delta is None or actual_delta > comparison.get("maxDelta", -1):
                    errors.append(issue("preview.geometry_delta", comparison_path, "Preview geometry comparison exceeds its declared maxDelta."))
                layout_node_id = comparison.get("layoutNodeId")
                target_node = layout_node_records_by_asset.get(audit_asset_id, {}).get(layout_node_id)
                if not isinstance(target_node, dict):
                    errors.append(issue("preview.geometry_layout_node", f"{comparison_path}.layoutNodeId", "Preview geometry comparison must name a target asset layout node."))
                elif _rect_delta(comparison.get("actualNormalizedRect"), target_node.get("rect")) not in (0.0,):
                    errors.append(issue("preview.geometry_layout_rect", f"{comparison_path}.actualNormalizedRect", "Preview actualNormalizedRect must equal the audited layout node rect."))
            if audited_asset.get("assetKind") == "screen":
                required_regions = {
                    region_id
                    for region_id, region in requirement_index["byKind"].get("region", {}).items()
                    if region.get("inBuildScope") is True
                    and any(
                        isinstance(mapping, dict)
                        and mapping.get("assetId") == audit_asset_id
                        and region_id in mapping.get("requirementRefs", [])
                        for mapping in bundle.get("nodeMappings", [])
                    )
                }
                missing_regions = required_regions - compared_regions
                if missing_regions:
                    errors.append(issue("preview.geometry_coverage", f"{check_path}.previewAudit.geometryComparisons", f"Screen preview audit is missing mapped region comparisons: {sorted(missing_regions)}."))
            if static_visual_policy:
                visual_comparisons = audit.get("visualLayerComparisons")
                if not isinstance(visual_comparisons, list) or not visual_comparisons:
                    errors.append(issue("preview.visual_layer_missing", f"{check_path}.previewAudit.visualLayerComparisons", "Policy-enabled previews require per-element static visual layer evidence."))
                compared_visuals: set[str] = set()
                for visual_index, comparison in enumerate(visual_comparisons if isinstance(visual_comparisons, list) else []):
                    if not isinstance(comparison, dict):
                        continue
                    visual_path = f"{check_path}.previewAudit.visualLayerComparisons[{visual_index}]"
                    requirement_ref = comparison.get("requirementRef")
                    indexed = requirement_by_id.get(requirement_ref)
                    if indexed is None or indexed.get("kind") != "element" or indexed["entity"].get("kind") != "image":
                        errors.append(issue("preview.visual_layer_requirement", f"{visual_path}.requirementRef", "Visual layer comparisons must cite an image requirement element."))
                        continue
                    compared_visuals.add(requirement_ref)
                    target_node = layout_node_records_by_asset.get(audit_asset_id, {}).get(comparison.get("layoutNodeId"))
                    if not isinstance(target_node, dict) or target_node.get("role") != "visual.image":
                        errors.append(issue("preview.visual_layer_role", f"{visual_path}.layoutNodeId", "Static visual layer evidence must resolve to a visual.image layout node."))
                    disposition = comparison.get("disposition")
                    if disposition == "merged":
                        merged_ref = comparison.get("mergedIntoRequirementRef")
                        merged_indexed = requirement_by_id.get(merged_ref)
                        if merged_indexed is None or merged_indexed.get("kind") != "element" or merged_indexed["entity"].get("kind") != "image":
                            errors.append(issue("preview.visual_layer_merge", f"{visual_path}.mergedIntoRequirementRef", "Merged visual evidence must name another image requirement element."))
                    if disposition == "accepted-deviation":
                        deviation = deviations.get(comparison.get("deviationId"))
                        if deviation is None or deviation.get("status") != "accepted":
                            errors.append(issue("preview.visual_layer_deviation", f"{visual_path}.deviationId", "A visual-layer deviation must reference an accepted structured deviation."))
                required_static_visuals = {
                    element_id
                    for element_id, element in requirement_index["byKind"].get("element", {}).items()
                    if element.get("kind") == "image"
                    and element.get("runtimeControlled") is False
                    and element.get("inBuildScope") is True
                    and any(
                        isinstance(mapping, dict)
                        and mapping.get("assetId") == audit_asset_id
                        and element_id in mapping.get("requirementRefs", [])
                        for mapping in bundle.get("nodeMappings", [])
                    )
                }
                missing_visuals = required_static_visuals - compared_visuals
                if missing_visuals:
                    errors.append(issue("preview.visual_layer_coverage", f"{check_path}.previewAudit.visualLayerComparisons", f"Preview audit is missing mapped static visual elements: {sorted(missing_visuals)}."))
            if check_linked_files and isinstance(check.get("artifactPath"), str):
                artifact_path = resolve_contract_path(bundle_path, check["artifactPath"])
                if not artifact_path.is_file():
                    errors.append(issue("preview.artifact_read", f"{check_path}.artifactPath", "Preview artifact does not exist."))
                elif audit.get("artifactSha256") != sha256_file(artifact_path):
                    errors.append(issue("preview.artifact_hash", f"{check_path}.previewAudit.artifactSha256", "Preview artifact hash does not match artifactPath."))

    asset_statuses = {asset_id: asset.get("status") for asset_id, asset in assets.items()}
    check_statuses = [check.get("status") for check in verification.get("checks", []) if isinstance(check, dict)]
    if execution.get("status") == "completed":
        if not isinstance(execution.get("startedAt"), str) or not isinstance(execution.get("completedAt"), str):
            errors.append(issue("execution.completed_audit", "$.execution", "Completed execution requires startedAt and completedAt."))
        incomplete_assets = sorted(asset_id for asset_id, status in asset_statuses.items() if status not in {"built", "verified"})
        if incomplete_assets:
            errors.append(issue("execution.asset_status", "$.assets", f"Completed execution cannot contain planned or failed assets: {incomplete_assets}."))
        if any(status == "pending" for status in check_statuses):
            errors.append(issue("execution.check_status", "$.verification.checks", "Completed execution cannot retain pending verification checks."))
    if verification.get("status") == "passed":
        if execution.get("status") != "completed":
            errors.append(issue("verification.execution_status", "$.verification.status", "Passed verification requires completed execution."))
        non_verified_assets = sorted(asset_id for asset_id, status in asset_statuses.items() if status != "verified")
        if non_verified_assets:
            errors.append(issue("verification.asset_status", "$.assets", f"Passed verification requires every asset to be verified: {non_verified_assets}."))
        if any(status != "passed" for status in check_statuses):
            errors.append(issue("verification.check_status", "$.verification.checks", "Passed verification requires every check to be passed."))
        if static_visual_policy:
            static_visual_requirement_ids = {
                element_id
                for element_id, element in requirement_index["byKind"].get("element", {}).items()
                if element.get("kind") == "image"
                and element.get("runtimeControlled") is False
                and element.get("inBuildScope") is True
            }
            assets_requiring_visual_preview = {
                mapping.get("assetId")
                for mapping in bundle.get("nodeMappings", [])
                if isinstance(mapping, dict)
                and mapping.get("assetId") in asset_ids
                and static_visual_requirement_ids.intersection(mapping.get("requirementRefs", []))
            }
            missing_preview_assets = assets_requiring_visual_preview - passed_preview_asset_ids
            if missing_preview_assets:
                errors.append(
                    issue(
                        "preview.visual_asset_coverage",
                        "$.verification.checks",
                        "Policy-enabled passed verification requires a passed, structured static-visual preview audit "
                        f"for every asset that owns accepted static visual elements: {sorted(missing_preview_assets)}.",
                    )
                )
        material_unaccepted = sorted(
            deviation_id
            for deviation_id, deviation in deviations.items()
            if deviation.get("impact") in {"medium", "high"} and deviation.get("status") != "accepted"
        )
        if material_unaccepted:
            errors.append(issue("verification.material_deviation", "$.verification.deviations", f"Passed verification cannot retain unaccepted material deviations: {material_unaccepted}."))

    return result(errors, warnings)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path, help="Path to UIBuildBundle JSON.")
    parser.add_argument("--requirement", type=Path, help="Override linked UIRequirementSpec path.")
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--requirement-schema", type=Path, default=REQUIREMENT_SCHEMA)
    parser.add_argument("--skip-linked-files", action="store_true", help="Skip hash and UILayoutSpec file checks.")
    args = parser.parse_args()
    try:
        requirement = load_json(args.requirement) if args.requirement else None
        output = validate_build_bundle(
            load_json(args.bundle),
            load_json(args.schema),
            bundle_path=args.bundle.resolve(),
            requirement_spec=requirement,
            requirement_path=args.requirement.resolve() if args.requirement else None,
            requirement_schema=load_json(args.requirement_schema),
            check_linked_files=not args.skip_linked_files,
        )
    except (OSError, json.JSONDecodeError, ValueError) as error:
        output = result([issue("io.read", "$", str(error))])
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if output["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
