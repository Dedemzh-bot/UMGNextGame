#!/usr/bin/env python3
"""Audit accepted UI requirements against UIBuildBundle layout coverage."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from _contract_common import ASSETS_ROOT, issue, load_json, resolve_contract_path, result
from validate_build_bundle import DEFAULT_SCHEMA as BUNDLE_SCHEMA, validate_build_bundle
from validate_requirement_spec import DEFAULT_SCHEMA as REQUIREMENT_SCHEMA, build_requirement_index


def _rect_matches(left: Any, right: Any, tolerance: float = 0.001) -> bool:
    return (
        isinstance(left, list)
        and isinstance(right, list)
        and len(left) == len(right) == 4
        and all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in left + right)
        and max(abs(float(a) - float(b)) for a, b in zip(left, right)) <= tolerance
    )


def _load_layout_nodes(bundle: dict[str, Any], bundle_path: Path | None, errors: list[dict[str, str]]) -> dict[str, dict[str, dict[str, Any]]]:
    nodes_by_asset: dict[str, dict[str, dict[str, Any]]] = {}
    if bundle_path is None:
        return nodes_by_asset
    for asset_index, asset in enumerate(bundle.get("assets", [])):
        if not isinstance(asset, dict) or not isinstance(asset.get("id"), str) or not isinstance(asset.get("layoutSpecPath"), str):
            continue
        layout_path = resolve_contract_path(bundle_path, asset["layoutSpecPath"])
        try:
            layout = load_json(layout_path)
        except (OSError, json.JSONDecodeError, ValueError) as error:
            errors.append(issue("coverage.layout_read", f"$.assets[{asset_index}].layoutSpecPath", str(error)))
            continue
        nodes_by_asset[asset["id"]] = {
            node["id"]: node
            for node in layout.get("nodes", [])
            if isinstance(node, dict) and isinstance(node.get("id"), str)
        }
    return nodes_by_asset


def _role_matches_element_kind(kind: Any, role: Any) -> bool:
    """Return whether one direct Requirement element lowers to a compatible layout role."""
    if not isinstance(kind, str) or not isinstance(role, str):
        return False
    expected_prefixes = {
        "panel": ("screen.root", "container."),
        "button": ("input.button",),
        "image": ("visual.image",),
        "text": ("text.",),
        "progress": ("progress.",),
        "list": ("collection.lua-list",),
        "tile": ("collection.lua-tile",),
        "scroll": ("container.game-scroll",),
        "size": ("container.size",),
        "scale": ("container.scale",),
        "overlay": ("container.overlay",),
    }
    prefixes = expected_prefixes.get(kind)
    if prefixes is None:
        return True
    return any(role == prefix or (prefix.endswith(".") and role.startswith(prefix)) for prefix in prefixes)


def validate_requirement_coverage(bundle: Any, requirement: Any, *, bundle_path: Path | None = None) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    if not isinstance(bundle, dict) or not isinstance(requirement, dict):
        return result([issue("coverage.type", "$", "Bundle and requirement must both be objects.")])

    index = build_requirement_index(requirement)
    claims = index["byKind"].get("claim", {})
    review = requirement.get("reviewGate") if isinstance(requirement.get("reviewGate"), dict) else {}
    reviewed_claims = set(review.get("acceptedClaimIds", [])) if isinstance(review.get("acceptedClaimIds"), list) else set()
    accepted_claims = {
        claim_id
        for claim_id, claim in claims.items()
        if claim.get("status") == "accepted" and claim_id in reviewed_claims
    }
    nodes_by_asset = _load_layout_nodes(bundle, bundle_path, errors)
    assets_by_id = {
        asset.get("id"): asset
        for asset in bundle.get("assets", [])
        if isinstance(asset, dict) and isinstance(asset.get("id"), str)
    }
    asset_ids_by_plan_id: dict[str, set[str]] = {}
    for asset_id, asset in assets_by_id.items():
        plan_id = asset.get("assetPlanId")
        if isinstance(plan_id, str):
            asset_ids_by_plan_id.setdefault(plan_id, set()).add(asset_id)

    owning_plan_ids_by_region: dict[str, set[str]] = {}
    for asset_plan in requirement.get("assetPlan", []):
        if (
            not isinstance(asset_plan, dict)
            or asset_plan.get("inBuildScope") is not True
            or not isinstance(asset_plan.get("id"), str)
        ):
            continue
        for region_id in asset_plan.get("coversRegionIds", []):
            if isinstance(region_id, str):
                owning_plan_ids_by_region.setdefault(region_id, set()).add(asset_plan["id"])

    mapped_asset_plans = {
        asset.get("assetPlanId")
        for asset in bundle.get("assets", [])
        if isinstance(asset, dict) and isinstance(asset.get("assetPlanId"), str)
    }
    mapped_requirements: set[str] = set()
    mapped_states: set[str] = set()
    for mapping in bundle.get("nodeMappings", []):
        if not isinstance(mapping, dict):
            continue
        mapped_requirements.update(ref for ref in mapping.get("requirementRefs", []) if isinstance(ref, str))
        mapped_states.update(ref for ref in mapping.get("stateRefs", []) if isinstance(ref, str))
    if bundle.get("version") in {"0.2", "0.3"}:
        for relation in bundle.get("reuseRelations", []):
            if isinstance(relation, dict):
                mapped_requirements.update(ref for ref in relation.get("requirementRefs", []) if isinstance(ref, str))
    verified_requirements = {
        ref
        for check in bundle.get("verification", {}).get("checks", [])
        if isinstance(check, dict)
        for ref in check.get("requirementRefs", [])
        if isinstance(ref, str)
    }

    mapping_targets = {
        "asset": mapped_asset_plans,
        "region": mapped_requirements,
        "element": mapped_requirements,
        "collection": mapped_requirements,
        "runtime-field": mapped_requirements,
        "responsive-intent": mapped_requirements,
        "state": mapped_states,
        "acceptance-criterion": verified_requirements,
    }
    for kind in (
        "asset",
        "region",
        "element",
        "collection",
        "runtime-field",
        "responsive-intent",
        "state",
        "acceptance-criterion",
    ):
        for entity_id, entity in index["byKind"].get(kind, {}).items():
            entity_claims = set(entity.get("claimIds", []))
            is_accepted = bool(entity_claims & accepted_claims)
            in_scope = entity.get("inBuildScope") is True
            mapped = entity_id in mapping_targets[kind]
            path = index["byId"][entity_id]["path"]
            if is_accepted and in_scope and not mapped:
                errors.append(
                    issue(
                        "coverage.missing",
                        path,
                        f"Accepted in-scope {kind} {entity_id} has no build mapping.",
                    )
                )
            if not in_scope and not isinstance(entity.get("scopedOutReason"), str):
                errors.append(
                    issue(
                        "coverage.scoped_out_reason",
                        path,
                        f"Out-of-scope {kind} {entity_id} requires scopedOutReason.",
                    )
                )
            if not in_scope and mapped:
                errors.append(
                    issue(
                        "coverage.out_of_scope_mapped",
                        path,
                        f"Out-of-scope {kind} {entity_id} must not enter the build bundle.",
                    )
                )
            if in_scope and not is_accepted and mapped:
                errors.append(
                    issue(
                        "coverage.unaccepted_mapped",
                        path,
                        f"Unaccepted {kind} {entity_id} must not enter the build bundle.",
                    )
                )

    mappings = [mapping for mapping in bundle.get("nodeMappings", []) if isinstance(mapping, dict)]
    for element_id, element in index["byKind"].get("element", {}).items():
        if element.get("inBuildScope") is not True or not (set(element.get("claimIds", [])) & accepted_claims):
            continue
        element_mappings = [mapping for mapping in mappings if element_id in mapping.get("requirementRefs", [])]
        reuse_mapped = any(
            isinstance(relation, dict) and element_id in relation.get("requirementRefs", [])
            for relation in bundle.get("reuseRelations", [])
        )
        if not element_mappings:
            if reuse_mapped:
                continue
            continue
        if bundle_path is None:
            continue
        compatible_direct_mapping = any(
            mapping.get("mappingKind") in {"direct", "composite-state", "collection"}
            and _role_matches_element_kind(
                element.get("kind"),
                nodes_by_asset.get(mapping.get("assetId"), {}).get(mapping.get("layoutNodeId"), {}).get("role"),
            )
            for mapping in element_mappings
        )
        if not compatible_direct_mapping:
            path = index["byId"][element_id]["path"]
            errors.append(
                issue(
                    "coverage.element_role",
                    path,
                    f"Accepted in-scope element {element_id} must map directly to a compatible UILayout role; "
                    "region containers and generated-support nodes cannot stand in for its visual or interactive type.",
                )
            )

    for region_id, region in index["byKind"].get("region", {}).items():
        if region.get("inBuildScope") is not True or not (set(region.get("claimIds", [])) & accepted_claims):
            continue
        region_mappings = [mapping for mapping in mappings if region_id in mapping.get("requirementRefs", [])]
        screen_mappings = [
            mapping
            for mapping in region_mappings
            if assets_by_id.get(mapping.get("assetId"), {}).get("assetKind") == "screen"
        ]
        path = index["byId"][region_id]["path"]
        owning_plan_ids = owning_plan_ids_by_region.get(region_id, set())
        owning_asset_ids = {
            asset_id
            for plan_id in owning_plan_ids
            for asset_id in asset_ids_by_plan_id.get(plan_id, set())
        }
        has_explicit_owner = bool(owning_plan_ids)
        if has_explicit_owner:
            eligible_mappings = [
                mapping
                for mapping in region_mappings
                if mapping.get("assetId") in owning_asset_ids
            ]
            expected = "owning assetPlan layout"
            mapping_error_code = "coverage.region_owner_mapping"
        else:
            # Legacy requirements may not declare coversRegionIds. Keep the
            # historical screen-first behavior only for those contracts.
            has_in_scope_screen = any(asset.get("assetKind") == "screen" for asset in assets_by_id.values())
            eligible_mappings = screen_mappings if has_in_scope_screen else region_mappings
            expected = "screen" if has_in_scope_screen else "owning in-scope asset"
            mapping_error_code = "coverage.region_screen_mapping"
        if not eligible_mappings:
            errors.append(issue(mapping_error_code, path, f"Accepted in-scope region {region_id} must map to a {expected} node."))
            continue
        if bundle_path is not None and not any(
            _rect_matches(
                nodes_by_asset.get(mapping.get("assetId"), {}).get(mapping.get("layoutNodeId"), {}).get("rect"),
                region.get("bounds"),
            )
            for mapping in eligible_mappings
        ):
            expected_geometry = "owning assetPlan" if has_explicit_owner else "eligible"
            errors.append(issue("coverage.region_geometry", path, f"Region {region_id} bounds must equal one mapped {expected_geometry} layout node rect."))

    for collection_id, collection in index["byKind"].get("collection", {}).items():
        if collection.get("inBuildScope") is not True or not (set(collection.get("claimIds", [])) & accepted_claims):
            continue
        collection_mappings = [mapping for mapping in mappings if collection_id in mapping.get("requirementRefs", [])]
        path = index["byId"][collection_id]["path"]
        if not any(
            mapping.get("mappingKind") == "collection"
            and collection.get("containerElementId") in mapping.get("requirementRefs", [])
            and (
                bundle_path is None
                or str(nodes_by_asset.get(mapping.get("assetId"), {}).get(mapping.get("layoutNodeId"), {}).get("role", "")).startswith("collection.")
            )
            for mapping in collection_mappings
        ):
            errors.append(issue("coverage.collection_contract", path, f"Collection {collection_id} requires a collection mapping of its container element to a collection.* layout node."))

    for state_id, state in index["byKind"].get("state", {}).items():
        if state.get("inBuildScope") is not True or not (set(state.get("claimIds", [])) & accepted_claims):
            continue
        state_mappings = [mapping for mapping in mappings if state_id in mapping.get("stateRefs", [])]
        path = index["byId"][state_id]["path"]
        composition = state.get("composition") if isinstance(state.get("composition"), dict) else {}
        required_elements = set(composition.get("elementIds", []))
        if not any(
            mapping.get("mappingKind") == "composite-state"
            and bool(required_elements & set(mapping.get("requirementRefs", [])))
            for mapping in state_mappings
        ):
            errors.append(issue("coverage.state_composition", path, f"State {state_id} requires a composite-state mapping of a composition element, not only its state ID."))

    return result(errors, warnings)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--requirement", type=Path, help="Override bundle requirement path.")
    parser.add_argument("--accepted-build-view", type=Path, help="Optional Accepted Build View sidecar; coverage is still computed from the complete Requirement.")
    parser.add_argument("--bundle-schema", type=Path, default=BUNDLE_SCHEMA)
    parser.add_argument("--requirement-schema", type=Path, default=REQUIREMENT_SCHEMA)
    parser.add_argument("--skip-linked-files", action="store_true")
    args = parser.parse_args()
    try:
        bundle = load_json(args.bundle)
        link = bundle.get("requirement") if isinstance(bundle, dict) and isinstance(bundle.get("requirement"), dict) else {}
        requirement_path = args.requirement.resolve() if args.requirement else resolve_contract_path(args.bundle.resolve(), link.get("path", ""))
        requirement = load_json(requirement_path)
        bundle_result = validate_build_bundle(
            bundle,
            load_json(args.bundle_schema),
            bundle_path=args.bundle.resolve(),
            requirement_spec=requirement,
            requirement_path=requirement_path,
            requirement_schema=load_json(args.requirement_schema),
            requirement_schema_path=args.requirement_schema.resolve(),
            accepted_build_view_path=args.accepted_build_view.resolve() if args.accepted_build_view else None,
            check_linked_files=not args.skip_linked_files,
        )
        coverage = validate_requirement_coverage(bundle, requirement, bundle_path=args.bundle.resolve())
        if not bundle_result["valid"]:
            coverage["errors"].insert(0, issue("bundle.invalid", "$", "UIBuildBundle failed contract validation."))
            coverage["valid"] = False
        output = coverage
    except (OSError, json.JSONDecodeError, ValueError) as error:
        output = result([issue("io.read", "$", str(error))])
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if output["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
