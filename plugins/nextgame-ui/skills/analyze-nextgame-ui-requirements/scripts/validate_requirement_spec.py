#!/usr/bin/env python3
"""Validate a NextGame UIRequirementSpec 0.1 without third-party packages."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from _contract_common import (
    ASSETS_ROOT,
    compute_approved_content_sha256,
    issue,
    load_json,
    resolve_contract_path,
    result,
    sha256_file,
    validate_schema_instance,
)
from validate_agent_findings import (
    CONTEXT_ROLES,
    DEFAULT_SCHEMA as AGENT_FINDINGS_SCHEMA,
    ROUND_ONE_ROLES,
    validate_agent_findings,
)
from validate_request_packet import DEFAULT_SCHEMA as REQUEST_PACKET_SCHEMA, validate_request_packet


DEFAULT_SCHEMA = ASSETS_ROOT / "ui-requirement-spec.schema.json"
REQUIRED_AGENT_ROLES = {
    "visual-structure",
    "text-requirements",
    "project-pattern",
    "state-modeling",
    "data-adaptation",
    "asset-decomposition",
    "state-visual-review",
    "schema-feasibility-review",
    "coverage-review",
}


def _items(spec: dict[str, Any], *path: str) -> list[Any]:
    current: Any = spec
    for key in path:
        if not isinstance(current, dict):
            return []
        current = current.get(key)
    return current if isinstance(current, list) else []


def requirement_entities(spec: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    """Return (kind, json path, entity) for every canonical object with an id."""

    entities: list[tuple[str, str, dict[str, Any]]] = []

    def add(kind: str, path: str, values: Iterable[Any]) -> None:
        for index, value in enumerate(values):
            if isinstance(value, dict):
                entities.append((kind, f"{path}[{index}]", value))

    add("source", "$.sources", spec.get("sources", []))
    add("evidence", "$.evidence", spec.get("evidence", []))
    add("claim", "$.claims", spec.get("claims", []))
    ui_model = spec.get("uiModel") if isinstance(spec.get("uiModel"), dict) else {}
    for field, kind in (
        ("regions", "region"),
        ("componentFamilies", "component-family"),
        ("elements", "element"),
        ("collections", "collection"),
        ("runtimeFields", "runtime-field"),
        ("responsiveIntent", "responsive-intent"),
    ):
        add(kind, f"$.uiModel.{field}", ui_model.get(field, []))
    for model_index, model in enumerate(spec.get("stateModels", [])):
        if not isinstance(model, dict):
            continue
        entities.append(("state-model", f"$.stateModels[{model_index}]", model))
        add(
            "state-control-input",
            f"$.stateModels[{model_index}].controlInputs",
            model.get("controlInputs", []),
        )
        for axis_index, axis in enumerate(model.get("axes", [])):
            if not isinstance(axis, dict):
                continue
            entities.append(("state-axis", f"$.stateModels[{model_index}].axes[{axis_index}]", axis))
            add(
                "state",
                f"$.stateModels[{model_index}].axes[{axis_index}].states",
                axis.get("states", []),
            )
    add("asset", "$.assetPlan", spec.get("assetPlan", []))
    add("assumption", "$.assumptions", spec.get("assumptions", []))
    add("question", "$.questions", spec.get("questions", []))
    add("acceptance-criterion", "$.acceptanceCriteria", spec.get("acceptanceCriteria", []))
    return entities


def build_requirement_index(spec: dict[str, Any]) -> dict[str, Any]:
    entities = requirement_entities(spec)
    by_id = {
        entity["id"]: {"kind": kind, "path": path, "entity": entity}
        for kind, path, entity in entities
        if isinstance(entity.get("id"), str)
    }
    by_kind: dict[str, dict[str, dict[str, Any]]] = {}
    for entity_id, indexed in by_id.items():
        by_kind.setdefault(indexed["kind"], {})[entity_id] = indexed["entity"]
    return {"entities": entities, "byId": by_id, "byKind": by_kind}


def required_user_interaction_buttons(
    spec: dict[str, Any],
    requirement_index: dict[str, Any] | None = None,
) -> dict[str, set[str]]:
    """Return the in-scope same-family Button elements required by each interactive state model.

    This contract applies only to newly synthesized requirements that opt in through
    ``analysisPolicy.stateControlInputRequired``. Archived 0.1 requirements without
    that policy retain their historical validation behavior.
    """

    analysis_policy = spec.get("analysisPolicy") if isinstance(spec.get("analysisPolicy"), dict) else {}
    if analysis_policy.get("stateControlInputRequired") is not True:
        return {}

    index = requirement_index or build_requirement_index(spec)
    elements = index.get("byKind", {}).get("element", {})
    requirements: dict[str, set[str]] = {}
    for model in spec.get("stateModels", []):
        if not isinstance(model, dict) or not isinstance(model.get("id"), str):
            continue
        control_inputs = model.get("controlInputs") if isinstance(model.get("controlInputs"), list) else []
        has_user_interaction = any(
            isinstance(control_input, dict) and control_input.get("kind") == "user-interaction"
            for control_input in control_inputs
        )
        if not has_user_interaction:
            continue
        family_id = model.get("componentFamilyId")
        requirements[model["id"]] = {
            element_id
            for element_id, element in elements.items()
            if element.get("kind") == "button"
            and element.get("inBuildScope") is True
            and element.get("familyId") == family_id
        }
    return requirements


def required_design_size_modes(
    spec: dict[str, Any],
    requirement_index: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Return each in-scope asset's reviewed Designer size-mode decision.

    New requirements opt into this executable/readback gate with
    ``analysisPolicy.designSizeModeRequired`` and persist the result in
    ``assetPlan[].designSizeModeDecision``. The ``umg_`` basename is a hard
    project FillScreen rule; ``uw_`` assets are analyzed; legacy names fall back
    conservatively. Asset kind never selects the mode. Archived requirements
    without the policy retain their historical behavior.
    """

    analysis_policy = spec.get("analysisPolicy") if isinstance(spec.get("analysisPolicy"), dict) else {}
    if analysis_policy.get("designSizeModeRequired") is not True:
        return {}

    index = requirement_index or build_requirement_index(spec)
    return {
        asset_id: decision["mode"]
        for asset_id, asset in index.get("byKind", {}).get("asset", {}).items()
        if asset.get("inBuildScope") is True
        and isinstance((decision := asset.get("designSizeModeDecision")), dict)
        and decision.get("mode") in {"FillScreen", "Desired"}
    }


def _check_refs(
    errors: list[dict[str, str]],
    refs: Any,
    allowed: set[str],
    path: str,
    label: str,
) -> None:
    if not isinstance(refs, list):
        return
    for ref in refs:
        if isinstance(ref, str) and ref not in allowed:
            errors.append(issue(f"ref.{label}", path, f"Unknown {label} id: {ref}"))


def _has_accepted_claim(entity: dict[str, Any], accepted_claim_ids: set[str]) -> bool:
    claim_ids = entity.get("claimIds")
    return isinstance(claim_ids, list) and any(claim_id in accepted_claim_ids for claim_id in claim_ids)


def validate_requirement_spec(
    spec: Any,
    schema: dict[str, Any],
    request_packet: dict[str, Any] | None = None,
    request_packet_schema: dict[str, Any] | None = None,
    request_packet_path: Path | None = None,
    spec_path: Path | None = None,
    check_findings_files: bool = False,
) -> dict[str, Any]:
    errors = validate_schema_instance(spec, schema)
    warnings: list[dict[str, str]] = []
    if not isinstance(spec, dict):
        return result(errors, warnings)

    index = build_requirement_index(spec)
    analysis_policy = spec.get("analysisPolicy") if isinstance(spec.get("analysisPolicy"), dict) else {}
    static_visual_coverage_required = analysis_policy.get("staticVisualCoverageRequired") is True
    explicit_panel_slots_required = analysis_policy.get("explicitPanelSlotsRequired") is True
    explicit_image_owner_intent_required = analysis_policy.get("explicitImageOwnerIntentRequired") is True
    design_size_mode_required = analysis_policy.get("designSizeModeRequired") is True
    all_ids: set[str] = set()
    for kind, path, entity in index["entities"]:
        entity_id = entity.get("id")
        if not isinstance(entity_id, str):
            continue
        if entity_id in all_ids:
            errors.append(issue("id.duplicate", f"{path}.id", f"Canonical id {entity_id!r} is duplicated."))
        all_ids.add(entity_id)

    by_kind: dict[str, dict[str, dict[str, Any]]] = index["byKind"]
    source_ids = set(by_kind.get("source", {}))
    evidence_ids = set(by_kind.get("evidence", {}))
    claim_ids = set(by_kind.get("claim", {}))
    region_ids = set(by_kind.get("region", {}))
    family_ids = set(by_kind.get("component-family", {}))
    element_ids = set(by_kind.get("element", {}))
    collection_ids = set(by_kind.get("collection", {}))
    state_model_ids = set(by_kind.get("state-model", {}))
    axis_ids = set(by_kind.get("state-axis", {}))
    state_ids = set(by_kind.get("state", {}))
    asset_ids = set(by_kind.get("asset", {}))

    normalization = spec.get("normalization") if isinstance(spec.get("normalization"), dict) else {}
    findings_inputs: dict[str, dict[str, Any]] = {}
    findings_roles: set[str] = set()
    loaded_findings_local_ids: dict[str, set[str]] = {}
    loaded_findings_documents: dict[str, dict[str, Any]] = {}
    loaded_review_findings: dict[tuple[str, str], dict[str, Any]] = {}
    if check_findings_files and (spec_path is None or request_packet is None):
        errors.append(issue("normalization.check_inputs", "$.normalization", "Linked findings validation requires both spec_path and RequestPacket."))
    findings_schema = load_json(AGENT_FINDINGS_SCHEMA) if check_findings_files else None
    packet_schema_for_findings = request_packet_schema or (load_json(REQUEST_PACKET_SCHEMA) if check_findings_files else None)
    for input_index, findings_input in enumerate(normalization.get("findingsInputs", [])):
        if not isinstance(findings_input, dict):
            continue
        findings_ref = findings_input.get("findingsRef")
        if isinstance(findings_ref, str):
            if findings_ref in findings_inputs:
                errors.append(issue("normalization.findings_duplicate", f"$.normalization.findingsInputs[{input_index}].findingsRef", "findingsRef must be unique."))
            findings_inputs[findings_ref] = findings_input
            findings_path = Path(findings_ref)
            if findings_path.is_absolute() or ".." in findings_path.parts:
                errors.append(issue("normalization.findings_scope", f"$.normalization.findingsInputs[{input_index}].findingsRef", "findingsRef must remain relative to the RequirementSpec directory and cannot contain '..'."))
            elif check_findings_files and spec_path is not None:
                resolved_findings = resolve_contract_path(spec_path, findings_ref)
                if not resolved_findings.is_file():
                    errors.append(issue("normalization.findings_missing", f"$.normalization.findingsInputs[{input_index}].findingsRef", f"Findings file does not exist: {resolved_findings}"))
                else:
                    actual_hash = sha256_file(resolved_findings)
                    if findings_input.get("findingsSha256") != actual_hash:
                        errors.append(issue("normalization.findings_digest", f"$.normalization.findingsInputs[{input_index}].findingsSha256", f"Findings hash mismatch; expected {actual_hash}."))
        agent_role = findings_input.get("agentRole")
        if isinstance(agent_role, str):
            if agent_role in findings_roles:
                errors.append(issue("normalization.role_duplicate", f"$.normalization.findingsInputs[{input_index}].agentRole", "Each Multi-Agent role may contribute exactly one findings input."))
            findings_roles.add(agent_role)
        context: dict[str, Any] | None = None
        context_ref = findings_input.get("contextRef")
        context_digest = findings_input.get("contextSha256")
        if agent_role in CONTEXT_ROLES:
            if not isinstance(context_ref, str) or not isinstance(context_digest, str):
                errors.append(issue("normalization.context_required", f"$.normalization.findingsInputs[{input_index}]", "Round-two and review findings require contextRef and contextSha256."))
        elif agent_role in ROUND_ONE_ROLES and (context_ref is not None or context_digest is not None):
            errors.append(issue("normalization.context_forbidden", f"$.normalization.findingsInputs[{input_index}]", "Round-one findings must not declare normalized context."))
        if isinstance(context_ref, str):
            context_path = Path(context_ref)
            if context_path.is_absolute() or ".." in context_path.parts:
                errors.append(issue("normalization.context_scope", f"$.normalization.findingsInputs[{input_index}].contextRef", "contextRef must remain relative to the RequirementSpec directory and cannot contain '..'."))
            elif check_findings_files and spec_path is not None:
                resolved_context = resolve_contract_path(spec_path, context_ref)
                if not resolved_context.is_file():
                    errors.append(issue("normalization.context_missing", f"$.normalization.findingsInputs[{input_index}].contextRef", f"Context file does not exist: {resolved_context}"))
                else:
                    actual_context_hash = sha256_file(resolved_context)
                    if context_digest != actual_context_hash:
                        errors.append(issue("normalization.context_digest", f"$.normalization.findingsInputs[{input_index}].contextSha256", f"Context hash mismatch; expected {actual_context_hash}."))
                    try:
                        loaded_context = load_json(resolved_context)
                        if isinstance(loaded_context, dict):
                            context = loaded_context
                        else:
                            errors.append(issue("normalization.context_type", f"$.normalization.findingsInputs[{input_index}].contextRef", "Context JSON must be an object."))
                    except (OSError, json.JSONDecodeError, ValueError) as error:
                        errors.append(issue("normalization.context_read", f"$.normalization.findingsInputs[{input_index}].contextRef", str(error)))
        if (
            check_findings_files
            and spec_path is not None
            and request_packet is not None
            and isinstance(findings_ref, str)
            and not Path(findings_ref).is_absolute()
            and ".." not in Path(findings_ref).parts
        ):
            resolved_findings = resolve_contract_path(spec_path, findings_ref)
            if resolved_findings.is_file():
                try:
                    findings_document = load_json(resolved_findings)
                    if not isinstance(findings_document, dict):
                        errors.append(issue("normalization.findings_type", f"$.normalization.findingsInputs[{input_index}].findingsRef", "Findings JSON must be an object."))
                    else:
                        loaded_findings_documents[findings_ref] = findings_document
                        if findings_document.get("agentRole") != agent_role:
                            errors.append(issue("normalization.findings_role", f"$.normalization.findingsInputs[{input_index}].agentRole", "findingsInput agentRole must match the linked AgentFindings."))
                        findings_validation = validate_agent_findings(
                            findings_document,
                            findings_schema or load_json(AGENT_FINDINGS_SCHEMA),
                            request_packet,
                            packet_schema_for_findings or load_json(REQUEST_PACKET_SCHEMA),
                            context,
                            request_packet_path,
                        )
                        if not findings_validation["valid"]:
                            nested_codes = sorted({entry["code"] for entry in findings_validation["errors"]})
                            errors.append(issue("normalization.findings_invalid", f"$.normalization.findingsInputs[{input_index}].findingsRef", f"Linked AgentFindings failed validation: {nested_codes}."))
                        loaded_findings_local_ids[findings_ref] = {
                            item["localId"]
                            for section in ("evidence", "findings", "questionCandidates")
                            for item in findings_document.get(section, [])
                            if isinstance(item, dict) and isinstance(item.get("localId"), str)
                        }
                        if agent_role in {"state-visual-review", "schema-feasibility-review", "coverage-review"}:
                            for finding in findings_document.get("findings", []):
                                if isinstance(finding, dict) and isinstance(finding.get("localId"), str):
                                    loaded_review_findings[(findings_ref, finding["localId"])] = finding
                except (OSError, json.JSONDecodeError, ValueError) as error:
                    errors.append(issue("normalization.findings_read", f"$.normalization.findingsInputs[{input_index}].findingsRef", str(error)))
        if findings_input.get("inputDigest") != spec.get("inputDigest"):
            errors.append(issue("normalization.input_digest", f"$.normalization.findingsInputs[{input_index}].inputDigest", "Every normalized findings input must bind to this RequirementSpec inputDigest."))
    alias_keys: set[tuple[str, str]] = set()
    for alias_index, alias in enumerate(normalization.get("aliases", [])):
        if not isinstance(alias, dict):
            continue
        alias_path = f"$.normalization.aliases[{alias_index}]"
        findings_ref = alias.get("findingsRef")
        local_id = alias.get("localId")
        alias_key = (findings_ref, local_id)
        if isinstance(findings_ref, str) and isinstance(local_id, str):
            if alias_key in alias_keys:
                errors.append(issue("normalization.alias_duplicate", alias_path, "Each findingsRef/localId alias may be normalized only once."))
            alias_keys.add(alias_key)
        findings_input = findings_inputs.get(findings_ref)
        if findings_input is None:
            errors.append(issue("normalization.findings_ref", f"{alias_path}.findingsRef", "Alias findingsRef is not declared in findingsInputs."))
        elif alias.get("agentRole") != findings_input.get("agentRole"):
            errors.append(issue("normalization.agent_role", f"{alias_path}.agentRole", "Alias agentRole must match its findings input."))
        if alias.get("canonicalId") not in all_ids:
            errors.append(issue("normalization.canonical_ref", f"{alias_path}.canonicalId", "Alias canonicalId must resolve to a canonical RequirementSpec entity."))
    discarded_keys: set[tuple[str, str]] = set()
    for discarded_index, discarded in enumerate(normalization.get("discardedLocalIds", [])):
        if not isinstance(discarded, dict):
            continue
        discarded_path = f"$.normalization.discardedLocalIds[{discarded_index}]"
        findings_ref = discarded.get("findingsRef")
        local_id = discarded.get("localId")
        discarded_key = (findings_ref, local_id)
        if isinstance(findings_ref, str) and isinstance(local_id, str):
            if discarded_key in discarded_keys:
                errors.append(issue("normalization.discard_duplicate", discarded_path, "Each findingsRef/localId may be discarded only once."))
            discarded_keys.add(discarded_key)
        findings_input = findings_inputs.get(findings_ref)
        if findings_input is None:
            errors.append(issue("normalization.findings_ref", f"{discarded_path}.findingsRef", "Discarded local id findingsRef is not declared in findingsInputs."))
        elif discarded.get("agentRole") != findings_input.get("agentRole"):
            errors.append(issue("normalization.agent_role", f"{discarded_path}.agentRole", "Discarded local id agentRole must match its findings input."))
    overlap = alias_keys & discarded_keys
    if overlap:
        errors.append(issue("normalization.trace_overlap", "$.normalization", f"Local ids cannot be both aliased and discarded: {sorted(overlap)}."))
    review_preview = spec.get("reviewGate") if isinstance(spec.get("reviewGate"), dict) else {}
    if review_preview.get("status") == "accepted" and findings_roles != REQUIRED_AGENT_ROLES:
        errors.append(
            issue(
                "normalization.role_coverage",
                "$.normalization.findingsInputs",
                f"An accepted synthesis must contain exactly one findings input from every required role; missing={sorted(REQUIRED_AGENT_ROLES - findings_roles)}, extra={sorted(findings_roles - REQUIRED_AGENT_ROLES)}.",
            )
        )
    if review_preview.get("status") == "accepted" and not (alias_keys or discarded_keys):
        errors.append(issue("normalization.empty_trace", "$.normalization", "Accepted synthesis requires at least one aliased or explicitly discarded local id."))
    if check_findings_files:
        for findings_ref, actual_local_ids in loaded_findings_local_ids.items():
            documented_local_ids = {
                local_id
                for trace_ref, local_id in alias_keys | discarded_keys
                if trace_ref == findings_ref
            }
            if documented_local_ids != actual_local_ids:
                errors.append(
                    issue(
                        "normalization.local_id_coverage",
                        "$.normalization",
                        f"Local-id trace for {findings_ref} is incomplete; missing={sorted(actual_local_ids - documented_local_ids)}, extra={sorted(documented_local_ids - actual_local_ids)}.",
                    )
                )

    # Static visual coverage is opt-in so archived 0.1 requirements retain their
    # historical validation behavior. In strict mode, preserve the semantic type
    # of every visual-structure element finding through normalization. A visible
    # layer may be scoped out later, but it must first exist as a canonical element;
    # silently discarding it or folding it into a region/evidence record makes a
    # screenshot omission invisible to downstream mapping checks.
    if static_visual_coverage_required and check_findings_files:
        visual_inputs = [
            item
            for item in findings_inputs.values()
            if item.get("agentRole") == "visual-structure"
        ]
        for visual_input in visual_inputs:
            findings_ref = visual_input.get("findingsRef")
            findings_document = loaded_findings_documents.get(findings_ref)
            if not isinstance(findings_ref, str) or not isinstance(findings_document, dict):
                continue
            alias_targets = {
                alias.get("localId"): alias.get("canonicalId")
                for alias in normalization.get("aliases", [])
                if isinstance(alias, dict)
                and alias.get("findingsRef") == findings_ref
                and alias.get("agentRole") == "visual-structure"
            }
            discarded_ids = {
                item.get("localId")
                for item in normalization.get("discardedLocalIds", [])
                if isinstance(item, dict)
                and item.get("findingsRef") == findings_ref
                and item.get("agentRole") == "visual-structure"
            }
            local_evidence = {
                item.get("localId"): item
                for item in findings_document.get("evidence", [])
                if isinstance(item, dict) and isinstance(item.get("localId"), str)
            }
            for finding_index, finding in enumerate(findings_document.get("findings", [])):
                if not isinstance(finding, dict) or finding.get("category") != "element":
                    continue
                local_id = finding.get("localId")
                finding_path = f"{findings_ref}#$.findings[{finding_index}]"
                if local_id in discarded_ids:
                    errors.append(
                        issue(
                            "visual.element_discard_forbidden",
                            finding_path,
                            "Visual-structure element findings must become canonical elements; scope exclusions belong on the canonical element instead of discardedLocalIds.",
                        )
                    )
                    continue
                canonical_id = alias_targets.get(local_id)
                indexed_target = index["byId"].get(canonical_id)
                if not isinstance(indexed_target, dict) or indexed_target.get("kind") != "element":
                    errors.append(
                        issue(
                            "visual.element_alias_type",
                            finding_path,
                            "Visual-structure element findings must alias to uiModel.elements without changing semantic type.",
                        )
                    )
                if finding.get("impact") in {"medium", "high"}:
                    evidence_refs = finding.get("evidenceRefs", [])
                    has_measured_geometry = any(
                        isinstance(local_evidence.get(evidence_ref), dict)
                        and all(
                            local_evidence[evidence_ref].get(key) is not None
                            for key in ("bounds", "sourceDimensions", "pixelBounds", "measurementMethod")
                        )
                        for evidence_ref in evidence_refs
                    )
                    if not has_measured_geometry:
                        errors.append(
                            issue(
                                "visual.element_geometry_required",
                                finding_path,
                                "Medium/high visual element findings require at least one measured image-evidence record.",
                            )
                        )

    source_keys: set[str] = set()
    for source_id, source in by_kind.get("source", {}).items():
        source_key = source.get("sourceKey")
        if isinstance(source_key, str):
            if source_key in source_keys:
                errors.append(issue("source.key_duplicate", f"$.sources[{source_id}]", f"Duplicate sourceKey: {source_key}"))
            source_keys.add(source_key)
        kind = source.get("kind")
        if kind == "image":
            if not isinstance(source.get("path"), str):
                errors.append(issue("source.image_path", f"$.sources[{source_id}]", "Image sources require path."))
            if not isinstance(source.get("dimensions"), list):
                errors.append(issue("source.image_dimensions", f"$.sources[{source_id}]", "Image sources require dimensions."))
        locator_kind = source.get("locatorKind")
        if locator_kind in {"local-file", "unreal-object"} and not isinstance(source.get("contentSha256"), str):
            errors.append(issue("source.content_digest", f"$.sources[{source_id}]", f"{locator_kind} sources require contentSha256."))
        if locator_kind == "local-file":
            local_path = source.get("path")
            if not isinstance(local_path, str):
                errors.append(issue("source.local_path", f"$.sources[{source_id}].path", "local-file sources require path."))
            elif not Path(local_path).is_absolute():
                errors.append(issue("source.local_absolute", f"$.sources[{source_id}].path", "local-file source paths must be absolute."))
        if locator_kind == "unreal-object":
            if not isinstance(source.get("path"), str) or not source["path"].startswith("/Game/"):
                errors.append(issue("source.unreal_path", f"$.sources[{source_id}].path", "unreal-object sources require a /Game/... path."))
            snapshot_path = source.get("snapshotPath")
            if not isinstance(snapshot_path, str):
                errors.append(issue("source.snapshot_path", f"$.sources[{source_id}].snapshotPath", "unreal-object sources require snapshotPath."))
            else:
                snapshot = Path(snapshot_path)
                if snapshot.is_absolute() or ".." in snapshot.parts:
                    errors.append(
                        issue(
                            "source.snapshot_scope",
                            f"$.sources[{source_id}].snapshotPath",
                            "snapshotPath must be relative to the RequestPacket directory and cannot contain '..'.",
                        )
                    )

    for evidence_id, evidence in by_kind.get("evidence", {}).items():
        source_id = evidence.get("sourceId")
        if isinstance(source_id, str) and source_id not in source_ids:
            errors.append(issue("ref.source", f"$.evidence[{evidence_id}].sourceId", f"Unknown source id: {source_id}"))

    claims = by_kind.get("claim", {})
    for claim_id, claim in claims.items():
        _check_refs(errors, claim.get("evidenceIds"), evidence_ids, f"$.claims[{claim_id}].evidenceIds", "evidence")
        _check_refs(errors, claim.get("subjectRefs"), all_ids - claim_ids, f"$.claims[{claim_id}].subjectRefs", "subject")
        if not claim.get("evidenceIds"):
            errors.append(issue("claim.evidence_required", f"$.claims[{claim_id}].evidenceIds", "Every claim requires an evidence chain."))

    accepted_claims = {claim_id for claim_id, claim in claims.items() if claim.get("status") == "accepted"}
    rejected_claims = {claim_id for claim_id, claim in claims.items() if claim.get("status") == "rejected"}
    review = spec.get("reviewGate") if isinstance(spec.get("reviewGate"), dict) else {}
    listed_accepted = set(review.get("acceptedClaimIds", [])) if isinstance(review.get("acceptedClaimIds"), list) else set()
    listed_rejected = set(review.get("rejectedClaimIds", [])) if isinstance(review.get("rejectedClaimIds"), list) else set()
    _check_refs(errors, list(listed_accepted), claim_ids, "$.reviewGate.acceptedClaimIds", "claim")
    _check_refs(errors, list(listed_rejected), claim_ids, "$.reviewGate.rejectedClaimIds", "claim")
    if listed_accepted != accepted_claims:
        errors.append(
            issue(
                "review.accepted_claims",
                "$.reviewGate.acceptedClaimIds",
                "acceptedClaimIds must exactly match claims whose status is accepted.",
            )
        )
    if listed_rejected != rejected_claims:
        errors.append(
            issue(
                "review.rejected_claims",
                "$.reviewGate.rejectedClaimIds",
                "rejectedClaimIds must exactly match claims whose status is rejected.",
            )
        )
    if listed_accepted & listed_rejected:
        errors.append(issue("review.claim_overlap", "$.reviewGate", "A claim cannot be both accepted and rejected."))
    for rejected_id in listed_rejected:
        if rejected_id in accepted_claims:
            errors.append(issue("review.rejected_status", "$.reviewGate.rejectedClaimIds", f"Rejected claim {rejected_id} still has accepted status."))
    review_status = review.get("status")
    geometry_evidence_required = analysis_policy.get("geometryEvidenceRequired") is True
    list_priority_required = analysis_policy.get("listPriorityRequired") is True
    state_control_input_required = analysis_policy.get("stateControlInputRequired") is True
    has_reviewer = isinstance(review.get("reviewedBy"), str)
    has_review_time = isinstance(review.get("reviewedAt"), str)
    if review_status in {"accepted", "rejected"}:
        if not has_reviewer or not has_review_time:
            errors.append(issue("review.audit", "$.reviewGate", "Completed review requires reviewedBy and reviewedAt."))
        if has_review_time:
            try:
                datetime.fromisoformat(review["reviewedAt"].replace("Z", "+00:00"))
            except ValueError:
                errors.append(issue("review.time", "$.reviewGate.reviewedAt", "reviewedAt must be an ISO-8601 timestamp."))
    elif review_status == "pending" and (has_reviewer or has_review_time):
        errors.append(issue("review.pending_audit", "$.reviewGate", "Pending review must not claim reviewer or review time."))
    approved_digest = review.get("approvedContentSha256")
    if review_status == "accepted":
        try:
            expected_approval_digest = compute_approved_content_sha256(spec)
        except (TypeError, ValueError) as error:
            expected_approval_digest = None
            errors.append(issue("review.content_digest_input", "$.reviewGate", f"Approved content is not canonically serializable: {error}"))
        if expected_approval_digest is not None and approved_digest != expected_approval_digest:
            errors.append(
                issue(
                    "review.content_digest",
                    "$.reviewGate.approvedContentSha256",
                    f"Approved content digest mismatch; expected {expected_approval_digest}.",
                )
            )
        high_unresolved = [
            claim_id
            for claim_id, claim in claims.items()
            if claim.get("status") == "unresolved"
            and (claim.get("impact") == "high" or claim.get("blocksBuild") is True)
        ]
        if high_unresolved:
            errors.append(
                issue(
                    "review.high_unresolved_claim",
                    "$.claims",
                    f"Accepted review cannot retain high-impact unresolved claims: {sorted(high_unresolved)}.",
                )
            )
        high_open_questions = [
            question.get("id")
            for question in spec.get("questions", [])
            if isinstance(question, dict)
            and question.get("status") == "open"
            and (question.get("impact") == "high" or question.get("blocksBuild") is True)
        ]
        if high_open_questions:
            errors.append(
                issue(
                    "review.high_open_question",
                    "$.questions",
                    f"Accepted review cannot retain high-impact open questions: {sorted(high_open_questions)}.",
                )
            )
    elif approved_digest is not None:
        errors.append(
            issue(
                "review.unaccepted_digest",
                "$.reviewGate.approvedContentSha256",
                "Only an accepted review may carry approvedContentSha256.",
            )
        )

    review_resolutions = spec.get("reviewResolutions", [])
    review_resolution_keys: set[tuple[str, str]] = set()
    for resolution_index, resolution in enumerate(review_resolutions):
        if not isinstance(resolution, dict):
            continue
        resolution_path = f"$.reviewResolutions[{resolution_index}]"
        key = (resolution.get("findingsRef"), resolution.get("localId"))
        if all(isinstance(value, str) for value in key):
            if key in review_resolution_keys:
                errors.append(issue("review.resolution_duplicate", resolution_path, "A review finding may have only one resolution."))
            review_resolution_keys.add(key)
        findings_input = findings_inputs.get(resolution.get("findingsRef"))
        if findings_input is None or findings_input.get("agentRole") != resolution.get("agentRole"):
            errors.append(issue("review.resolution_source", resolution_path, "Resolution must point to the declared review findings file and its matching role."))
        if resolution.get("status") == "open" and (resolution.get("impact") == "high"):
            errors.append(issue("review.high_open_finding", resolution_path, "High-impact review findings must be resolved before acceptance."))
    if review_status == "accepted" and check_findings_files:
        missing_review_resolutions = sorted(set(loaded_review_findings) - review_resolution_keys)
        if missing_review_resolutions:
            errors.append(
                issue(
                    "review.finding_unresolved",
                    "$.reviewResolutions",
                    f"Accepted review requires a closure record for every reviewer finding: {missing_review_resolutions}.",
                )
            )

    for claim_id, claim in claims.items():
        if claim.get("status") == "unresolved" and claim.get("impact") == "high" and claim.get("blocksBuild") is not True:
            errors.append(issue("claim.blocks_build", f"$.claims[{claim_id}].blocksBuild", "High-impact unresolved claims must block build."))
    for question_id, question in by_kind.get("question", {}).items():
        if question.get("status") == "open" and question.get("impact") == "high" and question.get("blocksBuild") is not True:
            errors.append(issue("question.blocks_build", f"$.questions[{question_id}].blocksBuild", "High-impact open questions must block build."))

    def check_evidence_claims(entity: dict[str, Any], path: str) -> None:
        if "evidenceIds" in entity:
            _check_refs(errors, entity.get("evidenceIds"), evidence_ids, f"{path}.evidenceIds", "evidence")
        if "claimIds" in entity:
            _check_refs(errors, entity.get("claimIds"), claim_ids, f"{path}.claimIds", "claim")

    build_relevant_kinds = {
        "region",
        "component-family",
        "element",
        "collection",
        "runtime-field",
        "responsive-intent",
        "state-model",
        "state-axis",
        "state",
        "asset",
        "acceptance-criterion",
    }

    for kind, path, entity in index["entities"]:
        if kind not in {"source", "evidence", "claim", "question", "acceptance-criterion"}:
            check_evidence_claims(entity, path)
        if kind in build_relevant_kinds:
            in_scope = entity.get("inBuildScope")
            reason = entity.get("scopedOutReason")
            if in_scope is False and not isinstance(reason, str):
                errors.append(issue("scope.reason", path, "Entities outside build scope require scopedOutReason."))
            if in_scope is True and reason is not None:
                errors.append(issue("scope.in_scope_reason", path, "In-scope entities must not declare scopedOutReason."))
            if in_scope is True:
                entity_claim_ids = set(entity.get("claimIds", [])) if isinstance(entity.get("claimIds"), list) else set()
                if not entity_claim_ids or not entity_claim_ids.issubset(accepted_claims):
                    errors.append(
                        issue(
                            "review.in_scope_claim",
                            path,
                            "Every claim attached to an in-scope entity must have accepted status, regardless of review-gate status.",
                        )
                    )
                if review_status == "accepted":
                    entity_id = entity.get("id")
                    if isinstance(entity_id, str):
                        for claim_id in sorted(entity_claim_ids):
                            if entity_id not in set(claims.get(claim_id, {}).get("subjectRefs", [])):
                                errors.append(
                                    issue(
                                        "review.claim_subject",
                                        f"{path}.claimIds",
                                        f"Supporting claim {claim_id} must name in-scope entity {entity_id} in subjectRefs.",
                                    )
                                )

    # The evidence contract is bidirectional for build-relevant entities. A claim
    # cannot name an entity unless that entity also records the claim in claimIds.
    for claim_id, claim in claims.items():
        for subject_ref in claim.get("subjectRefs", []):
            indexed_subject = index["byId"].get(subject_ref)
            if not isinstance(indexed_subject, dict) or indexed_subject.get("kind") not in build_relevant_kinds:
                continue
            subject_entity = indexed_subject["entity"]
            if claim_id not in set(subject_entity.get("claimIds", [])):
                errors.append(
                    issue(
                        "review.claim_subject_reverse",
                        f"$.claims[{claim_id}].subjectRefs",
                        f"Build-relevant subject {subject_ref} must include claim {claim_id} in its claimIds.",
                    )
                )

    regions = by_kind.get("region", {})
    def valid_bounds(value: Any) -> bool:
        return (
            isinstance(value, list)
            and len(value) == 4
            and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value)
            and value[0] >= 0 and value[1] >= 0 and value[2] > 0 and value[3] > 0
            and value[0] + value[2] <= 1.000001 and value[1] + value[3] <= 1.000001
        )

    def overlap_area(left: list[float], right: list[float]) -> float:
        x = max(0.0, min(left[0] + left[2], right[0] + right[2]) - max(left[0], right[0]))
        y = max(0.0, min(left[1] + left[3], right[1] + right[3]) - max(left[1], right[1]))
        return x * y

    def geometry_bounds_from_evidence(evidence: dict[str, Any], require_measurement: bool = False) -> list[float] | None:
        bounds = evidence.get("bounds")
        dimensions = evidence.get("sourceDimensions")
        pixels = evidence.get("pixelBounds")
        if not valid_bounds(bounds):
            return None
        if dimensions is None and pixels is None:
            return None if require_measurement else bounds
        if not (
            isinstance(dimensions, list) and len(dimensions) == 2
            and all(isinstance(value, (int, float)) and value > 0 for value in dimensions)
            and isinstance(pixels, list) and len(pixels) == 4
            and all(isinstance(value, (int, float)) and value >= 0 for value in pixels)
        ):
            return None
        derived = [pixels[0] / dimensions[0], pixels[1] / dimensions[1], pixels[2] / dimensions[0], pixels[3] / dimensions[1]]
        if any(abs(float(actual) - expected) > 0.003 for actual, expected in zip(bounds, derived)):
            errors.append(issue("geometry.evidence_normalization", "$.evidence", "Evidence bounds must match pixelBounds normalized by sourceDimensions."))
        return bounds

    for region_id, region in regions.items():
        parent = region.get("parentRegionId")
        if isinstance(parent, str) and parent not in region_ids:
            errors.append(issue("ref.region", f"$.uiModel.regions[{region_id}].parentRegionId", f"Unknown region id: {parent}"))
        if parent == region_id:
            errors.append(issue("region.self_parent", f"$.uiModel.regions[{region_id}].parentRegionId", "A region cannot parent itself."))
        bounds = region.get("bounds")
        if not valid_bounds(bounds):
            errors.append(issue("geometry.region_bounds", f"$.uiModel.regions[{region_id}].bounds", "Region bounds must remain inside the normalized screen frame."))
        evidence_id = region.get("geometryEvidenceId")
        if evidence_id is not None:
            evidence = by_kind.get("evidence", {}).get(evidence_id)
            expected = geometry_bounds_from_evidence(evidence, require_measurement=True) if isinstance(evidence, dict) else None
            if expected is None:
                errors.append(issue("geometry.evidence_required", f"$.uiModel.regions[{region_id}].geometryEvidenceId", "geometryEvidenceId must reference measured evidence with valid normalized bounds."))
            elif valid_bounds(bounds) and any(abs(float(actual) - expected_value) > 0.003 for actual, expected_value in zip(bounds, expected)):
                errors.append(issue("geometry.requirement_drift", f"$.uiModel.regions[{region_id}].bounds", "Region bounds drift from its cited measurement evidence."))
        if region.get("allowOverlap") is True and not isinstance(region.get("overlapReason"), str):
            errors.append(issue("geometry.overlap_reason", f"$.uiModel.regions[{region_id}]", "Intentional region overlap requires overlapReason."))
        if region.get("allowOverlap") is not True and region.get("overlapReason") is not None:
            errors.append(issue("geometry.overlap_reason", f"$.uiModel.regions[{region_id}]", "overlapReason is only valid when allowOverlap is true."))

    screen_region_ids = {region_id for region_id, region in regions.items() if region.get("purpose") == "screen"}
    if review_status == "accepted" and geometry_evidence_required:
        for region_id, region in regions.items():
            if (
                region.get("inBuildScope") is True
                and region.get("parentRegionId") in screen_region_ids
                and region.get("purpose") != "screen"
                and region.get("geometryEvidenceId") is None
            ):
                errors.append(issue("geometry.top_region_evidence", f"$.uiModel.regions[{region_id}]", "Accepted top-level visible regions require measured geometryEvidenceId; do not invent screen placement from a child asset size."))

    def check_parent_cycles(parent_by_id: dict[str, str], code: str, path: str, label: str) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node_id: str) -> None:
            if node_id in visited:
                return
            if node_id in visiting:
                errors.append(issue(code, path, f"{label} parent cycle includes {node_id}."))
                return
            visiting.add(node_id)
            parent_id = parent_by_id.get(node_id)
            if parent_id in parent_by_id:
                visit(parent_id)
            visiting.remove(node_id)
            visited.add(node_id)

        for node_id in parent_by_id:
            visit(node_id)

    check_parent_cycles(
        {region_id: region["parentRegionId"] for region_id, region in regions.items() if isinstance(region.get("parentRegionId"), str)},
        "region.parent_cycle",
        "$.uiModel.regions",
        "Region",
    )

    siblings_by_parent: dict[str | None, list[tuple[str, dict[str, Any]]]] = {}
    for region_id, region in regions.items():
        if region.get("inBuildScope") is True and region.get("purpose") != "screen" and valid_bounds(region.get("bounds")):
            siblings_by_parent.setdefault(region.get("parentRegionId"), []).append((region_id, region))
    for siblings in siblings_by_parent.values():
        for sibling_index, (left_id, left) in enumerate(siblings):
            for right_id, right in siblings[sibling_index + 1:]:
                if left.get("allowOverlap") is True or right.get("allowOverlap") is True:
                    continue
                if overlap_area(left["bounds"], right["bounds"]) > 0.0001:
                    errors.append(issue("geometry.sibling_overlap", "$.uiModel.regions", f"Sibling regions {left_id} and {right_id} overlap without an explicit overlap contract."))

    families = by_kind.get("component-family", {})
    for family_id, family in families.items():
        if family.get("regionId") not in region_ids:
            errors.append(issue("ref.region", f"$.uiModel.componentFamilies[{family_id}].regionId", "Unknown region id."))
        _check_refs(errors, family.get("memberElementIds"), element_ids, f"$.uiModel.componentFamilies[{family_id}].memberElementIds", "element")
        repetition = family.get("repetition")
        if review_status == "accepted" and list_priority_required and len(family.get("memberElementIds", [])) >= 2 and not isinstance(repetition, dict):
            errors.append(issue("collection.repetition_required", f"$.uiModel.componentFamilies[{family_id}]", "Accepted repeated component families require data-driven or static-repeat classification."))
        if isinstance(repetition, dict):
            classification = repetition.get("classification")
            varying_fields = repetition.get("varyingFields", [])
            if classification == "data-driven" and not varying_fields:
                errors.append(issue("collection.data_driven_fields", f"$.uiModel.componentFamilies[{family_id}].repetition", "Data-driven repeated families must name the runtime-varying fields."))
            if classification == "static-repeat" and not isinstance(repetition.get("staticRepeatReason"), str):
                errors.append(issue("collection.static_reason", f"$.uiModel.componentFamilies[{family_id}].repetition", "Static repeated families require an explicit structural or decorative reason."))

    elements = by_kind.get("element", {})
    for element_id, element in elements.items():
        if element.get("regionId") not in region_ids:
            errors.append(issue("ref.region", f"$.uiModel.elements[{element_id}].regionId", "Unknown region id."))
        parent = element.get("parentElementId")
        if isinstance(parent, str) and parent not in element_ids:
            errors.append(issue("ref.element", f"$.uiModel.elements[{element_id}].parentElementId", f"Unknown element id: {parent}"))
        if isinstance(parent, str) and parent in elements and elements[parent].get("kind") in {"list", "tile"}:
            errors.append(
                issue(
                    "element.list_leaf",
                    f"$.uiModel.elements[{element_id}].parentElementId",
                    "List and tile elements must be WidgetTree leaves; model their entries through collections instead.",
                )
            )
        if parent == element_id:
            errors.append(issue("element.self_parent", f"$.uiModel.elements[{element_id}].parentElementId", "An element cannot parent itself."))
        family = element.get("familyId")
        if isinstance(family, str) and family not in family_ids:
            errors.append(issue("ref.family", f"$.uiModel.elements[{element_id}].familyId", f"Unknown component family id: {family}"))
        element_bounds = element.get("bounds")
        if element_bounds is not None and not valid_bounds(element_bounds):
            errors.append(issue("geometry.element_bounds", f"$.uiModel.elements[{element_id}].bounds", "Element bounds must remain inside the normalized screen frame."))
        evidence_id = element.get("geometryEvidenceId")
        if evidence_id is not None:
            evidence = by_kind.get("evidence", {}).get(evidence_id)
            expected = geometry_bounds_from_evidence(evidence, require_measurement=True) if isinstance(evidence, dict) else None
            if expected is None:
                errors.append(issue("geometry.evidence_required", f"$.uiModel.elements[{element_id}].geometryEvidenceId", "geometryEvidenceId must reference measured evidence with valid normalized bounds."))
            elif not valid_bounds(element_bounds):
                errors.append(issue("geometry.element_evidence_bounds", f"$.uiModel.elements[{element_id}].bounds", "Measured elements require explicit bounds."))
            elif any(abs(float(actual) - expected_value) > 0.003 for actual, expected_value in zip(element_bounds, expected)):
                errors.append(issue("geometry.requirement_drift", f"$.uiModel.elements[{element_id}].bounds", "Element bounds drift from its cited measurement evidence."))

        layout_role = element.get("layoutRole")
        if layout_role in {"container.vertical", "container.horizontal"} and element.get("kind") != "panel":
            errors.append(
                issue(
                    "panel_slot.container_kind",
                    f"$.uiModel.elements[{element_id}].layoutRole",
                    "VerticalBox and HorizontalBox layout roles require an element whose kind is panel.",
                )
            )
        if layout_role == "container.game-scroll" and element.get("kind") != "scroll":
            errors.append(
                issue(
                    "panel_slot.container_kind",
                    f"$.uiModel.elements[{element_id}].layoutRole",
                    "container.game-scroll requires an element whose kind is scroll.",
                )
            )

        slot_intent = element.get("panelSlotIntent")
        parent_element = elements.get(parent) if isinstance(parent, str) else None
        parent_layout_role = parent_element.get("layoutRole") if isinstance(parent_element, dict) else None
        if isinstance(slot_intent, dict):
            expected_slot_type = (
                "flow"
                if parent_layout_role in {"container.vertical", "container.horizontal"}
                else "scroll"
                if parent_layout_role == "container.game-scroll"
                else None
            )
            if expected_slot_type is None:
                errors.append(
                    issue(
                        "panel_slot.parent_role",
                        f"$.uiModel.elements[{element_id}].panelSlotIntent",
                        "panelSlotIntent is valid only for a direct child whose parent declares a vertical, horizontal, or GameScrollBox layoutRole.",
                    )
                )
            elif slot_intent.get("slotType") != expected_slot_type:
                errors.append(
                    issue(
                        "panel_slot.type",
                        f"$.uiModel.elements[{element_id}].panelSlotIntent.slotType",
                        f"A child of {parent_layout_role} requires slotType {expected_slot_type!r}.",
                    )
                )
        if (
            explicit_panel_slots_required
            and element.get("inBuildScope") is True
            and isinstance(parent_element, dict)
            and parent_element.get("inBuildScope") is True
            and parent_layout_role in {"container.vertical", "container.horizontal", "container.game-scroll"}
            and not isinstance(slot_intent, dict)
        ):
            errors.append(
                issue(
                    "panel_slot.intent_required",
                    f"$.uiModel.elements[{element_id}].panelSlotIntent",
                    "Every in-scope direct child of a modeled VerticalBox, HorizontalBox, or GameScrollBox requires a reviewed immediate-parent Slot intent.",
                )
            )

    check_parent_cycles(
        {element_id: element["parentElementId"] for element_id, element in elements.items() if isinstance(element.get("parentElementId"), str)},
        "element.parent_cycle",
        "$.uiModel.elements",
        "Element",
    )

    children_by_parent: dict[str, set[str]] = {}
    for element_id, element in elements.items():
        parent = element.get("parentElementId")
        if isinstance(parent, str):
            children_by_parent.setdefault(parent, set()).add(element_id)

    def descendant_closure(root_id: str) -> set[str]:
        closure: set[str] = set()
        pending = [root_id]
        while pending:
            current = pending.pop()
            if current in closure:
                continue
            closure.add(current)
            pending.extend(children_by_parent.get(current, set()))
        return closure

    static_visual_element_ids = {
        element_id
        for element_id, element in elements.items()
        if element.get("kind") == "image" and element.get("runtimeControlled") is False
    }
    if static_visual_coverage_required:
        for element_id in sorted(static_visual_element_ids):
            element = elements[element_id]
            if element.get("inBuildScope") is not True:
                continue
            family_id = element.get("familyId")
            if isinstance(family_id, str):
                family = families.get(family_id, {})
                # Static families commonly list concrete screen instances in
                # memberElementIds while their reusable entry/template root is a
                # separate family-owned element. Accept both explicit members and
                # root-most family-owned elements as composition roots.
                entry_root_ids = set(family.get("memberElementIds", [])) | {
                    candidate_id
                    for candidate_id, candidate in elements.items()
                    if candidate.get("familyId") == family_id
                    and candidate.get("kind") in {
                        "panel", "button", "list", "tile", "scroll", "size", "scale", "overlay", "other"
                    }
                    and (
                        not isinstance(candidate.get("parentElementId"), str)
                        or elements.get(candidate["parentElementId"], {}).get("familyId") != family_id
                    )
                }
                if not entry_root_ids:
                    errors.append(
                        issue(
                            "visual.family_member_required",
                            f"$.uiModel.elements[{element_id}].familyId",
                            "A family-owned static visual requires at least one family member/template root.",
                        )
                    )
                elif not any(element_id in descendant_closure(root_id) for root_id in entry_root_ids):
                    errors.append(
                        issue(
                            "visual.family_composition",
                            f"$.uiModel.elements[{element_id}]",
                            "A family-owned static visual must be a descendant of a family member/template root so entry composition cannot omit it.",
                        )
                    )

    collections = by_kind.get("collection", {})
    for collection_id, collection in collections.items():
        for key, allowed, label in (
            ("regionId", region_ids, "region"),
            ("containerElementId", element_ids, "element"),
            ("entryFamilyId", family_ids, "family"),
        ):
            if collection.get(key) not in allowed:
                errors.append(issue(f"ref.{label}", f"$.uiModel.collections[{collection_id}].{key}", f"Unknown {label} id."))
        container = elements.get(collection.get("containerElementId"), {})
        container_kind = container.get("kind")
        if collection.get("dynamic") is True and container_kind not in {"list", "tile"}:
            errors.append(
                issue(
                    "collection.dynamic_container",
                    f"$.uiModel.collections[{collection_id}].containerElementId",
                    "Runtime-variable collections require a list or tile container.",
                )
            )
        if collection.get("dynamic") is False and container_kind in {"list", "tile"}:
            errors.append(
                issue(
                    "collection.fixed_container",
                    f"$.uiModel.collections[{collection_id}].containerElementId",
                    "Fixed repeated decoration should use a structural panel instead of a runtime collection widget.",
                )
            )

    for family_id, family in families.items():
        repetition = family.get("repetition") if isinstance(family.get("repetition"), dict) else {}
        if repetition.get("classification") == "data-driven":
            matching_collections = [
                collection_id for collection_id, collection in collections.items()
                if collection.get("entryFamilyId") == family_id and collection.get("dynamic") is True
            ]
            if not matching_collections:
                errors.append(issue("collection.list_required", f"$.uiModel.componentFamilies[{family_id}].repetition", "A data-driven repeated family must be represented by a dynamic LuaListView or LuaTileView collection, even when the preview count is fixed."))

    for runtime_id, runtime_field in by_kind.get("runtime-field", {}).items():
        element_id = runtime_field.get("elementId")
        if element_id not in element_ids:
            errors.append(issue("ref.element", f"$.uiModel.runtimeFields[{runtime_id}].elementId", "Unknown element id."))
        elif elements[element_id].get("runtimeControlled") is not True:
            errors.append(issue("runtime.variable", f"$.uiModel.runtimeFields[{runtime_id}].elementId", "Runtime fields must reference runtimeControlled elements."))

    responsive_intents = by_kind.get("responsive-intent", {})
    for intent_id, intent in responsive_intents.items():
        has_region_target = isinstance(intent.get("regionId"), str)
        has_element_target = isinstance(intent.get("elementId"), str)
        if has_region_target == has_element_target:
            errors.append(
                issue(
                    "responsive.target_exactly_one",
                    f"$.uiModel.responsiveIntent[{intent_id}]",
                    "Responsive intent must target exactly one regionId or elementId.",
                )
            )
        if has_region_target and intent.get("regionId") not in region_ids:
            errors.append(issue("ref.region", f"$.uiModel.responsiveIntent[{intent_id}].regionId", "Unknown region id."))
        if has_element_target and intent.get("elementId") not in element_ids:
            errors.append(issue("ref.element", f"$.uiModel.responsiveIntent[{intent_id}].elementId", "Unknown element id."))

    if analysis_policy.get("imageCompositionRequired") is True:
        image_groups: dict[str, list[tuple[str, dict[str, Any], dict[str, Any]]]] = {}
        for element_id, element in elements.items():
            element_path = f"$.uiModel.elements[{element_id}]"
            composition = element.get("imageComposition")
            if element.get("kind") != "image":
                if composition is not None:
                    errors.append(
                        issue(
                            "image.composition.non_image",
                            f"{element_path}.imageComposition",
                            "Only image elements may declare imageComposition.",
                        )
                    )
                continue

            if element.get("inBuildScope") is True and not isinstance(composition, dict):
                errors.append(
                    issue(
                        "image.composition.required",
                        f"{element_path}.imageComposition",
                        "Every in-scope image requires an imageComposition contract.",
                    )
                )
                continue
            if not isinstance(composition, dict):
                continue

            role = composition.get("role")
            if role == "layer":
                if not isinstance(element.get("evidenceIds"), list) or not element.get("evidenceIds"):
                    errors.append(
                        issue(
                            "image.composition.layer_evidence",
                            f"{element_path}.evidenceIds",
                            "A separated image layer requires evidence for the split.",
                        )
                    )
                if (
                    review_status == "accepted"
                    and element.get("inBuildScope") is True
                    and not _has_accepted_claim(element, listed_accepted)
                ):
                    errors.append(
                        issue(
                            "image.composition.layer_claim",
                            f"{element_path}.claimIds",
                            "An accepted separated image layer requires at least one reviewed accepted claim.",
                        )
                    )
                if (
                    composition.get("splitReason") == "independent-adaptation"
                    and composition.get("adaptation") != "independent"
                ):
                    errors.append(
                        issue(
                            "image.composition.independent_split_adaptation",
                            f"{element_path}.imageComposition",
                            "splitReason 'independent-adaptation' requires adaptation 'independent' and its own direct responsive intent.",
                        )
                    )
            elif role == "complete" and "splitReason" in composition:
                errors.append(
                    issue(
                        "image.composition.complete_split",
                        f"{element_path}.imageComposition.splitReason",
                        "A complete image is not a split layer and cannot declare splitReason.",
                    )
                )

            group_key = composition.get("groupKey")
            if element.get("inBuildScope") is True and isinstance(group_key, str):
                image_groups.setdefault(group_key, []).append((element_id, element, composition))

        for group_key, members in sorted(image_groups.items()):
            complete_ids = [
                element_id
                for element_id, _element, composition in members
                if composition.get("role") == "complete"
            ]
            if len(complete_ids) != 1:
                errors.append(
                    issue(
                        "image.composition.group_complete",
                        "$.uiModel.elements",
                        f"Image composition group {group_key!r} requires exactly one complete image; found {complete_ids}.",
                    )
                )

        def ancestor_element_ids(element: dict[str, Any]) -> set[str]:
            ancestors: set[str] = set()
            parent_id = element.get("parentElementId")
            while isinstance(parent_id, str) and parent_id in elements and parent_id not in ancestors:
                ancestors.add(parent_id)
                parent_id = elements[parent_id].get("parentElementId")
            return ancestors

        def owner_region_ids(element: dict[str, Any]) -> set[str]:
            owners: set[str] = set()
            region_id = element.get("regionId")
            while isinstance(region_id, str) and region_id in regions and region_id not in owners:
                owners.add(region_id)
                region_id = regions[region_id].get("parentRegionId")
            return owners

        for element_id, element in elements.items():
            if element.get("kind") != "image" or element.get("inBuildScope") is not True:
                continue
            composition = element.get("imageComposition")
            if not isinstance(composition, dict):
                continue
            element_path = f"$.uiModel.elements[{element_id}].imageComposition.adaptation"
            adaptation = composition.get("adaptation")
            if adaptation == "independent":
                if composition.get("ownerIntentId") is not None:
                    errors.append(
                        issue(
                            "image.adaptation.independent_owner",
                            f"$.uiModel.elements[{element_id}].imageComposition.ownerIntentId",
                            "Independent image adaptation must not name an inherited owner intent.",
                        )
                    )
                direct_intents = [
                    intent_id
                    for intent_id, intent in responsive_intents.items()
                    if intent.get("elementId") == element_id and intent.get("inBuildScope") is True
                ]
                if len(direct_intents) != 1:
                    errors.append(
                        issue(
                            "image.adaptation.independent_intent",
                            element_path,
                            f"Independent image adaptation requires exactly one in-scope element-targeted responsive intent; found {direct_intents}.",
                        )
                    )
            elif adaptation == "inherit-owner":
                ancestors = ancestor_element_ids(element)
                owner_regions = owner_region_ids(element)
                direct_intents = [
                    intent_id
                    for intent_id, intent in responsive_intents.items()
                    if intent.get("elementId") == element_id and intent.get("inBuildScope") is True
                ]
                if direct_intents:
                    errors.append(
                        issue(
                            "image.adaptation.inherited_direct_intent",
                            element_path,
                            f"Inherited image adaptation must not also declare an in-scope element-targeted responsive intent; found {direct_intents}.",
                        )
                    )
                inherited_sources = [
                    intent_id
                    for intent_id, intent in responsive_intents.items()
                    if intent.get("inBuildScope") is True
                    and _has_accepted_claim(intent, listed_accepted)
                    and (
                        (
                            intent.get("elementId") in ancestors
                            and elements.get(intent.get("elementId"), {}).get("inBuildScope") is True
                        )
                        or (
                            intent.get("regionId") in owner_regions
                            and regions.get(intent.get("regionId"), {}).get("inBuildScope") is True
                        )
                    )
                ]
                if explicit_image_owner_intent_required:
                    owner_intent_id = composition.get("ownerIntentId")
                    if not isinstance(owner_intent_id, str):
                        errors.append(
                            issue(
                                "image.adaptation.owner_intent_required",
                                f"$.uiModel.elements[{element_id}].imageComposition.ownerIntentId",
                                "Inherited image adaptation must name the exact accepted ancestor or region responsive intent.",
                            )
                        )
                    elif owner_intent_id not in inherited_sources:
                        errors.append(
                            issue(
                                "image.adaptation.owner_intent_invalid",
                                f"$.uiModel.elements[{element_id}].imageComposition.ownerIntentId",
                                f"ownerIntentId must resolve to an accepted in-scope intent on this image's element or region ancestry; eligible={inherited_sources}.",
                            )
                        )
                if not inherited_sources:
                    finding = issue(
                        "image.adaptation.inherited_intent",
                        element_path,
                        "Inherited image adaptation requires an in-scope accepted responsive intent on an element ancestor or its region ancestry.",
                    )
                    if review_status == "accepted":
                        errors.append(finding)
                    elif review_status == "pending":
                        warnings.append(finding)

    state_models = by_kind.get("state-model", {})
    interactive_button_requirements = required_user_interaction_buttons(spec, index)
    state_to_axis: dict[str, str] = {}
    axis_to_model: dict[str, str] = {}
    assigned_element_ids: set[str] = set()
    for model_id, model in state_models.items():
        if model.get("componentFamilyId") not in family_ids:
            errors.append(issue("ref.family", f"$.stateModels[{model_id}].componentFamilyId", "Unknown component family id."))
        model_axes = [axis for axis in model.get("axes", []) if isinstance(axis, dict)]
        model_axis_ids = {axis.get("id") for axis in model_axes if isinstance(axis.get("id"), str)}
        axes_by_id = {
            axis["id"]: axis
            for axis in model_axes
            if isinstance(axis.get("id"), str)
        }
        model_state_ids: set[str] = set()
        for axis in model_axes:
            axis_id = axis.get("id")
            if not isinstance(axis_id, str):
                continue
            axis_to_model[axis_id] = model_id
            states = [state for state in axis.get("states", []) if isinstance(state, dict)]
            ids = {state.get("id") for state in states if isinstance(state.get("id"), str)}
            model_state_ids.update(ids)
            for state_id in ids:
                state_to_axis[state_id] = axis_id
            for state in states:
                composition = state.get("composition") if isinstance(state.get("composition"), dict) else {}
                _check_refs(
                    errors,
                    composition.get("elementIds"),
                    element_ids,
                    f"$.stateModels[{model_id}].axes[{axis_id}].states[{state.get('id')}].composition.elementIds",
                    "element",
                )
            if axis.get("exclusive") is True:
                defaults = [state for state in states if state.get("isDefault") is True]
                if len(defaults) != 1:
                    errors.append(issue("state.default", f"$.stateModels[{model_id}].axes[{axis_id}]", "An exclusive axis requires exactly one default state."))
        control_inputs = [
            control_input
            for control_input in model.get("controlInputs", [])
            if isinstance(control_input, dict)
        ]
        if state_control_input_required and not control_inputs:
            errors.append(
                issue(
                    "state.control_input_required",
                    f"$.stateModels[{model_id}].controlInputs",
                    "The stateControlInputRequired policy requires at least one high-level control input per state model.",
                )
            )
        if model_id in interactive_button_requirements and not interactive_button_requirements[model_id]:
            errors.append(
                issue(
                    "state.control_input_button_required",
                    f"$.stateModels[{model_id}].controlInputs",
                    "A user-interaction state control requires at least one in-scope Button element in the same component family.",
                )
            )
        model_evidence_ids = set(model.get("evidenceIds", [])) if isinstance(model.get("evidenceIds"), list) else set()
        model_claim_ids = set(model.get("claimIds", [])) if isinstance(model.get("claimIds"), list) else set()
        for control_index, control_input in enumerate(control_inputs):
            control_path = f"$.stateModels[{model_id}].controlInputs[{control_index}]"
            control_axis_id = control_input.get("axisId")
            control_axis = axes_by_id.get(control_axis_id)
            if control_axis is None:
                errors.append(
                    issue(
                        "state.control_input_axis",
                        f"{control_path}.axisId",
                        "A control input must reference an axis in the same state model.",
                    )
                )
                control_axis_state_ids: set[str] = set()
            else:
                control_axis_state_ids = {
                    state.get("id")
                    for state in control_axis.get("states", [])
                    if isinstance(state, dict) and isinstance(state.get("id"), str)
                }
            _check_refs(
                errors,
                control_input.get("targetStateIds"),
                control_axis_state_ids,
                f"{control_path}.targetStateIds",
                "state",
            )
            control_evidence_ids = set(control_input.get("evidenceIds", [])) if isinstance(control_input.get("evidenceIds"), list) else set()
            control_claim_ids = set(control_input.get("claimIds", [])) if isinstance(control_input.get("claimIds"), list) else set()
            if not control_evidence_ids.issubset(model_evidence_ids):
                errors.append(
                    issue(
                        "state.control_input_evidence",
                        f"{control_path}.evidenceIds",
                        "Control-input evidence must also be attached to the same state model.",
                    )
                )
            if not control_claim_ids.issubset(model_claim_ids):
                errors.append(
                    issue(
                        "state.control_input_claim",
                        f"{control_path}.claimIds",
                        "Control-input claims must also be attached to the same state model.",
                    )
                )
            if review_status == "accepted" and not control_claim_ids.issubset(accepted_claims):
                errors.append(
                    issue(
                        "review.state_control_input_claim",
                        f"{control_path}.claimIds",
                        "Every claim supporting a control input in an accepted requirement must be accepted.",
                    )
                )
            if control_input.get("kind") == "unspecified":
                warnings.append(
                    issue(
                        "state.control_input_unspecified",
                        f"{control_path}.kind",
                        "The high-level state trigger is unspecified; keep this as a non-blocking program-handoff gap.",
                    )
                )
        if review_status == "accepted" and any(
            state.get("inBuildScope") is True
            for axis in model_axes
            for state in axis.get("states", [])
            if isinstance(state, dict)
        ):
            gated_entities = [("state-model", model_id, model)] + [
                ("state-axis", axis.get("id"), axis) for axis in model_axes
            ]
            for gated_kind, gated_id, gated_entity in gated_entities:
                gated_claims = set(gated_entity.get("claimIds", []))
                if not gated_claims or not gated_claims.issubset(accepted_claims):
                    errors.append(
                        issue(
                            "review.state_model_claim",
                            f"$.stateModels[{model_id}]",
                            f"In-scope {gated_kind} {gated_id} requires only reviewed accepted claims.",
                        )
                    )
                if isinstance(gated_id, str):
                    for claim_id in sorted(gated_claims):
                        if gated_id not in set(claims.get(claim_id, {}).get("subjectRefs", [])):
                            errors.append(
                                issue(
                                    "review.claim_subject",
                                    f"$.stateModels[{model_id}]",
                                    f"Supporting claim {claim_id} must name {gated_kind} {gated_id} in subjectRefs.",
                                )
                            )
        implementation = model.get("implementation") if isinstance(model.get("implementation"), dict) else {}
        implementation_axis = implementation.get("axisId")
        if implementation_axis not in model_axis_ids:
            errors.append(issue("ref.axis", f"$.stateModels[{model_id}].implementation.axisId", "Implementation must reference an axis in the same state model."))
        target_axis = next((axis for axis in model_axes if axis.get("id") == implementation_axis), None)
        target_states = [state for state in target_axis.get("states", []) if isinstance(state, dict)] if target_axis else []
        target_state_ids = {state.get("id") for state in target_states if isinstance(state.get("id"), str)}

        if implementation.get("strategy") == "exclusive-panel-branches":
            if not target_axis or target_axis.get("exclusive") is not True:
                errors.append(issue("state.branch_axis", f"$.stateModels[{model_id}].implementation", "exclusive-panel-branches requires an exclusive axis."))
            branches = [branch for branch in implementation.get("branches", []) if isinstance(branch, dict)]
            branch_state_ids = {branch.get("stateId") for branch in branches if isinstance(branch.get("stateId"), str)}
            if branch_state_ids != target_state_ids or len(branches) != len(target_state_ids):
                errors.append(issue("state.branch_complete", f"$.stateModels[{model_id}].implementation.branches", "There must be exactly one complete branch for every state on the selected axis."))
            panel_ids: set[str] = set()
            branch_element_sets: list[set[str]] = []
            states_by_id = {state.get("id"): state for state in target_states}
            for branch_index, branch in enumerate(branches):
                branch_path = f"$.stateModels[{model_id}].implementation.branches[{branch_index}]"
                panel_id = branch.get("panelElementId")
                if panel_id in panel_ids:
                    errors.append(issue("state.branch_panel_unique", f"{branch_path}.panelElementId", "Each state branch requires a distinct panel element."))
                if isinstance(panel_id, str):
                    panel_ids.add(panel_id)
                panel = elements.get(panel_id)
                if panel is None or panel.get("kind") != "panel":
                    errors.append(issue("state.branch_panel", f"{branch_path}.panelElementId", "Branch root must reference a panel element."))
                elif panel.get("runtimeControlled") is not True:
                    errors.append(issue("state.branch_variable", f"{branch_path}.panelElementId", "State branch panels must be runtimeControlled."))
                complete_ids = branch.get("completeElementIds")
                _check_refs(errors, complete_ids, element_ids, f"{branch_path}.completeElementIds", "element")
                if isinstance(complete_ids, list) and panel_id not in complete_ids:
                    errors.append(issue("state.branch_root_missing", f"{branch_path}.completeElementIds", "completeElementIds must include panelElementId."))
                if isinstance(panel_id, str) and isinstance(complete_ids, list):
                    complete_set = set(complete_ids)
                    branch_element_sets.append(complete_set)
                    if static_visual_coverage_required:
                        missing_static_visuals = (
                            descendant_closure(panel_id) & static_visual_element_ids
                        ) - complete_set
                        if missing_static_visuals:
                            errors.append(
                                issue(
                                    "visual.state_branch_composition",
                                    f"{branch_path}.completeElementIds",
                                    f"State branch composition omits static visual descendants: {sorted(missing_static_visuals)}.",
                                )
                            )
                    expected_closure = descendant_closure(panel_id)
                    if complete_set != expected_closure:
                        errors.append(
                            issue(
                                "state.branch_descendants",
                                f"{branch_path}.completeElementIds",
                                f"A full branch must equal its panel descendant closure; expected {sorted(expected_closure)}.",
                            )
                        )
                state = states_by_id.get(branch.get("stateId"))
                if isinstance(state, dict):
                    composition = state.get("composition") if isinstance(state.get("composition"), dict) else {}
                    if composition.get("mode") != "full-branch":
                        errors.append(issue("state.branch_composition", branch_path, "exclusive-panel-branches requires full-branch state composition."))
                    if isinstance(complete_ids, list) and set(composition.get("elementIds", [])) != set(complete_ids):
                        errors.append(issue("state.branch_elements", branch_path, "Branch elements must exactly match the state's composition elementIds."))
                    if state.get("isDefault") is True:
                        if branch.get("visibility") != "SelfHitTestInvisible":
                            errors.append(
                                issue(
                                    "state.branch_visibility",
                                    f"{branch_path}.visibility",
                                    "The active default branch must be SelfHitTestInvisible.",
                                )
                            )
                    elif branch.get("visibility") not in {"Collapsed", "Hidden"}:
                        errors.append(
                            issue(
                                "state.branch_visibility",
                                f"{branch_path}.visibility",
                                "Inactive branches must be Collapsed or Hidden when layout space must be retained.",
                            )
                        )
                    if branch.get("visibility") == "Hidden" and not isinstance(branch.get("preserveLayoutReason"), str):
                        errors.append(
                            issue(
                                "state.hidden_reason",
                                f"{branch_path}.preserveLayoutReason",
                                "Hidden inactive branches require preserveLayoutReason.",
                            )
                        )
                    if branch.get("visibility") != "Hidden" and branch.get("preserveLayoutReason") is not None:
                        errors.append(
                            issue(
                                "state.hidden_reason_unused",
                                f"{branch_path}.preserveLayoutReason",
                                "preserveLayoutReason is only valid for Hidden branches.",
                            )
                        )
            for left_index, left in enumerate(branch_element_sets):
                for right in branch_element_sets[left_index + 1 :]:
                    overlap = left & right
                    if overlap:
                        errors.append(
                            issue(
                                "state.branch_overlap",
                                f"$.stateModels[{model_id}].implementation.branches",
                                f"Exclusive full branches must not share branch elements: {sorted(overlap)}.",
                            )
                        )

        if implementation.get("strategy") == "shared-tree-properties":
            root_id = implementation.get("sharedRootElementId")
            if root_id not in element_ids:
                errors.append(issue("ref.element", f"$.stateModels[{model_id}].implementation.sharedRootElementId", "Unknown shared root element."))
            overrides = [override for override in implementation.get("stateOverrides", []) if isinstance(override, dict)]
            override_ids = {override.get("stateId") for override in overrides if isinstance(override.get("stateId"), str)}
            if override_ids != target_state_ids or len(overrides) != len(target_state_ids):
                errors.append(issue("state.override_complete", f"$.stateModels[{model_id}].implementation.stateOverrides", "There must be exactly one property override object per state."))
            compositions = []
            for state in target_states:
                composition = state.get("composition") if isinstance(state.get("composition"), dict) else {}
                if composition.get("mode") != "node-overrides":
                    errors.append(issue("state.override_composition", f"$.stateModels[{model_id}]", "shared-tree-properties requires node-overrides compositions."))
                compositions.append(set(composition.get("elementIds", [])))
            if compositions and any(item != compositions[0] for item in compositions[1:]):
                errors.append(issue("state.override_tree", f"$.stateModels[{model_id}]", "shared-tree-properties states must share the same element tree."))
            shared_elements = compositions[0] if compositions else set()
            if isinstance(root_id, str) and shared_elements != descendant_closure(root_id):
                errors.append(
                    issue(
                        "state.override_descendants",
                        f"$.stateModels[{model_id}].implementation.sharedRootElementId",
                        "Shared-tree compositions must equal the shared root descendant closure.",
                    )
                )
            for override_index, override in enumerate(overrides):
                seen_changes: set[tuple[str, str]] = set()
                for change_index, change in enumerate(override.get("changes", [])):
                    if not isinstance(change, dict):
                        continue
                    change_path = f"$.stateModels[{model_id}].implementation.stateOverrides[{override_index}].changes[{change_index}]"
                    element_id = change.get("elementId")
                    if element_id not in shared_elements:
                        errors.append(issue("state.override_element", f"{change_path}.elementId", "Property changes must target an element in the shared state tree."))
                    key = (str(element_id), str(change.get("property")))
                    if key in seen_changes:
                        errors.append(issue("state.override_duplicate", change_path, "A state cannot override the same element property twice."))
                    seen_changes.add(key)

        model_family = families.get(model.get("componentFamilyId"), {})
        family_members = set(model_family.get("memberElementIds", []))
        repetition = model_family.get("repetition") if isinstance(model_family.get("repetition"), dict) else {}
        dynamic_data_driven_family = (
            repetition.get("classification") == "data-driven"
            and any(
                collection.get("entryFamilyId") == model.get("componentFamilyId") and collection.get("dynamic") is True
                for collection in collections.values()
            )
        )
        observed_state_evidence = any(
            state.get("source") in {"observed", "cross-instance"} and state.get("inBuildScope") is True
            for axis in model_axes
            for state in axis.get("states", [])
            if isinstance(state, dict)
        )
        assignment_members = {
            assignment.get("elementId")
            for assignment in model.get("stateAssignments", [])
            if isinstance(assignment, dict) and isinstance(assignment.get("elementId"), str)
        }
        if (
            review_status == "accepted"
            and observed_state_evidence
            and family_members
            and not dynamic_data_driven_family
            and not family_members.issubset(assignment_members)
        ):
            errors.append(
                issue(
                    "state.assignment_preview_coverage",
                    f"$.stateModels[{model_id}].stateAssignments",
                    "Observed or cross-instance state models must assign every observed family instance required for the static preview.",
                )
            )
        for assignment_index, assignment in enumerate(model.get("stateAssignments", [])):
            if not isinstance(assignment, dict):
                continue
            assignment_path = f"$.stateModels[{model_id}].stateAssignments[{assignment_index}]"
            assignment_element_id = assignment.get("elementId")
            if isinstance(assignment_element_id, str):
                if assignment_element_id in assigned_element_ids:
                    errors.append(issue("state.assignment_duplicate", f"{assignment_path}.elementId", "Each requirement element may have only one stateAssignment across the specification."))
                assigned_element_ids.add(assignment_element_id)
            if assignment_element_id not in family_members:
                errors.append(issue("state.assignment_member", f"{assignment_path}.elementId", "State assignment must reference a member of the modeled component family."))
            assigned = assignment.get("axisStateIds", [])
            _check_refs(errors, assigned, model_state_ids, f"{assignment_path}.axisStateIds", "state")
            for axis in model_axes:
                if axis.get("exclusive") is not True:
                    continue
                axis_states = {state.get("id") for state in axis.get("states", []) if isinstance(state, dict)}
                count = sum(state_id in axis_states for state_id in assigned)
                if count != 1:
                    errors.append(issue("state.assignment_exclusive", f"{assignment_path}.axisStateIds", f"Assignment requires exactly one state from exclusive axis {axis.get('id')}."))

    assets = by_kind.get("asset", {})
    target = spec.get("target") if isinstance(spec.get("target"), dict) else {}
    # New synthesized specs opt into explicit standard-system asset boundaries.
    # Legacy specs omit the policy flag and remain backwards compatible.
    if analysis_policy.get("assetBoundaryRequired") is True:
        allowed_boundaries = {
            "screen-root",
            "statically-referenced",
            "entry-widget-class",
            "runtime-template",
            "reusable-widget",
            "stale-candidate",
        }
        for asset_id, asset in assets.items():
            boundary = asset.get("boundaryClassification")
            path = f"$.assetPlan[{asset_id}].boundaryClassification"
            if boundary not in allowed_boundaries:
                errors.append(issue("asset.boundary.required", path, "Every planned asset must declare a boundaryClassification."))
            evidence = asset.get("boundaryEvidenceIds", [])
            if not isinstance(evidence, list) or not evidence:
                errors.append(issue("asset.boundary.evidence", f"$.assetPlan[{asset_id}].boundaryEvidenceIds", "Asset boundary classification requires evidence IDs."))
            else:
                _check_refs(errors, evidence, evidence_ids, f"$.assetPlan[{asset_id}].boundaryEvidenceIds", "evidence")
            if asset.get("assetKind") == "screen" and boundary != "screen-root":
                errors.append(issue("asset.boundary.screen", path, "A screen asset must use boundaryClassification screen-root."))
            if asset.get("assetKind") == "list-entry" and boundary != "entry-widget-class":
                errors.append(issue("asset.boundary.entry", path, "A list-entry asset must use boundaryClassification entry-widget-class."))
            if (
                analysis_policy.get("standardSystemBoundaryRequired") is True
                and target.get("system") != "fight"
                and asset.get("assetKind") == "child-widget"
                and boundary not in {"runtime-template", "reusable-widget"}
            ):
                errors.append(
                    issue(
                        "asset.boundary.standard_system",
                        path,
                        "A standard non-fight child widget requires explicit runtime-template or reusable-widget evidence; screen-local regions and collections stay in the screen.",
                    )
                )
        if review_status == "accepted" and any(asset.get("boundaryClassification") == "stale-candidate" for asset in assets.values()):
            errors.append(issue("asset.stale.final", "$.assetPlan", "Accepted requirements cannot retain stale-candidate assets; remove or archive them with authorization."))
    build_orders: set[int] = set()
    target_paths = set(target.get("targetAssetPaths", [])) if isinstance(target.get("targetAssetPaths"), list) else set()
    planned_paths: set[str] = set()
    in_scope_asset_ids = {asset_id for asset_id, asset in assets.items() if asset.get("inBuildScope") is True}
    for asset_id, asset in assets.items():
        if asset.get("inBuildScope") is True:
            planned_paths.add(asset.get("assetPath"))
        _check_refs(errors, asset.get("dependsOnAssetIds"), asset_ids, f"$.assetPlan[{asset_id}].dependsOnAssetIds", "asset")
        _check_refs(errors, asset.get("coversRegionIds"), region_ids, f"$.assetPlan[{asset_id}].coversRegionIds", "region")
        _check_refs(errors, asset.get("coversElementIds"), element_ids, f"$.assetPlan[{asset_id}].coversElementIds", "element")
        _check_refs(errors, asset.get("coversCollectionIds"), collection_ids, f"$.assetPlan[{asset_id}].coversCollectionIds", "collection")
        _check_refs(errors, asset.get("coversStateModelIds"), state_model_ids, f"$.assetPlan[{asset_id}].coversStateModelIds", "state-model")
        decision = asset.get("designSizeModeDecision")
        decision_path = f"$.assetPlan[{asset_id}].designSizeModeDecision"
        if design_size_mode_required and asset.get("inBuildScope") is True and not isinstance(decision, dict):
            errors.append(
                issue(
                    "asset.design_size_mode.required",
                    decision_path,
                    "Every policy-enabled in-scope asset requires an explicit designSizeModeDecision.",
                )
            )
        if isinstance(decision, dict):
            mode = decision.get("mode")
            basis = decision.get("basis")
            decision_evidence = decision.get("evidenceIds")
            decision_claim_id = decision.get("claimId")
            decision_claim = claims.get(decision_claim_id) if isinstance(decision_claim_id, str) else None
            _check_refs(errors, decision_evidence, evidence_ids, f"{decision_path}.evidenceIds", "evidence")
            if not isinstance(decision_claim, dict):
                errors.append(
                    issue(
                        "ref.claim",
                        f"{decision_path}.claimId",
                        f"Unknown Design Size decision claim id: {decision_claim_id}",
                    )
                )
            else:
                asset_claim_ids = set(asset.get("claimIds", [])) if isinstance(asset.get("claimIds"), list) else set()
                if decision_claim_id not in asset_claim_ids:
                    errors.append(
                        issue(
                            "asset.design_size_mode.claim_scope",
                            f"{decision_path}.claimId",
                            "The decision claim must also belong to the enclosing assetPlan.claimIds.",
                        )
                    )
                if decision_claim.get("type") != "asset-decomposition":
                    errors.append(
                        issue(
                            "asset.design_size_mode.claim_type",
                            f"{decision_path}.claimId",
                            "A Design Size decision must be proved by an asset-decomposition claim.",
                        )
                    )
                claim_subjects = set(decision_claim.get("subjectRefs", [])) if isinstance(decision_claim.get("subjectRefs"), list) else set()
                if asset_id not in claim_subjects:
                    errors.append(
                        issue(
                            "asset.design_size_mode.claim_subject",
                            f"{decision_path}.claimId",
                            "The decision claim subjectRefs must include the enclosing asset id.",
                        )
                    )
                if isinstance(decision_evidence, list):
                    claim_evidence = set(decision_claim.get("evidenceIds", [])) if isinstance(decision_claim.get("evidenceIds"), list) else set()
                    uncovered_evidence = {
                        evidence_id
                        for evidence_id in decision_evidence
                        if isinstance(evidence_id, str) and evidence_id not in claim_evidence
                    }
                    if uncovered_evidence:
                        errors.append(
                            issue(
                                "asset.design_size_mode.claim_evidence",
                                f"{decision_path}.evidenceIds",
                                f"The proving claim must cover every decision evidence id: {sorted(uncovered_evidence)}.",
                            )
                        )
                if basis == "fallback-unclear":
                    statement = decision_claim.get("statement")
                    if not isinstance(statement, str) or "fallback-unclear" not in statement or "FillScreen" not in statement:
                        errors.append(
                            issue(
                                "asset.design_size_mode.fallback_claim",
                                f"{decision_path}.claimId",
                                "A fallback claim must explicitly record both canonical tokens fallback-unclear and FillScreen.",
                            )
                        )
                if basis == "project-umg-rule":
                    statement = decision_claim.get("statement")
                    if not isinstance(statement, str) or "project-umg-rule" not in statement or "FillScreen" not in statement:
                        errors.append(
                            issue(
                                "asset.design_size_mode.umg_claim",
                                f"{decision_path}.claimId",
                                "The umg project-rule claim must explicitly record both canonical tokens project-umg-rule and FillScreen.",
                            )
                        )
                    evidence_by_id = by_kind.get("evidence", {})
                    sources_by_id = by_kind.get("source", {})
                    claim_evidence_ids = (
                        decision_claim.get("evidenceIds", [])
                        if isinstance(decision_claim.get("evidenceIds"), list)
                        else []
                    )
                    has_project_rule_source = any(
                        isinstance(evidence_by_id.get(evidence_id), dict)
                        and isinstance(sources_by_id.get(evidence_by_id[evidence_id].get("sourceId")), dict)
                        and sources_by_id[evidence_by_id[evidence_id]["sourceId"]].get("kind") == "project-rule"
                        for evidence_id in claim_evidence_ids
                        if isinstance(evidence_id, str)
                    )
                    if not has_project_rule_source:
                        errors.append(
                            issue(
                                "asset.design_size_mode.umg_rule_evidence",
                                f"{decision_path}.claimId",
                                "project-umg-rule requires the proving claim to cite evidence from a project-rule source.",
                            )
                        )
            if isinstance(decision_evidence, list):
                asset_evidence = set(asset.get("evidenceIds", [])) if isinstance(asset.get("evidenceIds"), list) else set()
                detached_evidence = {
                    evidence_id
                    for evidence_id in decision_evidence
                    if isinstance(evidence_id, str) and evidence_id not in asset_evidence
                }
                if detached_evidence:
                    errors.append(
                        issue(
                            "asset.design_size_mode.evidence_scope",
                            f"{decision_path}.evidenceIds",
                            f"Decision evidence must also belong to the planned asset evidenceIds: {sorted(detached_evidence)}.",
                        )
                    )
            evidence_is_empty = not isinstance(decision_evidence, list) or not decision_evidence
            if basis not in {"fallback-unclear", "project-umg-rule"} and evidence_is_empty:
                errors.append(
                    issue(
                        "asset.design_size_mode.evidence_required",
                        f"{decision_path}.evidenceIds",
                        "An analyzed Design Size mode requires positive evidence; fallback-unclear and project-umg-rule do not.",
                    )
                )
            if mode == "Desired" and evidence_is_empty:
                errors.append(
                    issue(
                        "asset.design_size_mode.desired_evidence",
                        f"{decision_path}.evidenceIds",
                        "Desired requires positive evidence that the Widget is locally/content sized or a verified local reference uses it.",
                    )
                )
            if basis == "verified-reference" and isinstance(decision_evidence, list):
                evidence_by_id = by_kind.get("evidence", {})
                sources_by_id = by_kind.get("source", {})
                has_verified_asset_readback = any(
                    isinstance(evidence_by_id.get(evidence_id), dict)
                    and evidence_by_id[evidence_id].get("measurementMethod") == "editor-readback"
                    and isinstance(sources_by_id.get(evidence_by_id[evidence_id].get("sourceId")), dict)
                    and sources_by_id[evidence_by_id[evidence_id]["sourceId"]].get("kind") == "project-asset"
                    and sources_by_id[evidence_by_id[evidence_id]["sourceId"]].get("locatorKind") == "unreal-object"
                    for evidence_id in decision_evidence
                    if isinstance(evidence_id, str)
                )
                if not has_verified_asset_readback:
                    errors.append(
                        issue(
                            "asset.design_size_mode.verified_reference_evidence",
                            f"{decision_path}.evidenceIds",
                            "verified-reference requires editor-readback evidence from a project-asset source using locatorKind unreal-object.",
                        )
                    )
            required_mode_by_basis = {
                "viewport-filling": "FillScreen",
                "content-sized-local": "Desired",
                "project-umg-rule": "FillScreen",
                "fallback-unclear": "FillScreen",
            }
            required_mode = required_mode_by_basis.get(basis)
            if isinstance(required_mode, str) and mode != required_mode:
                errors.append(
                    issue(
                        "asset.design_size_mode.basis_mode",
                        decision_path,
                        f"Basis {basis!r} requires mode {required_mode!r}; found {mode!r}.",
                    )
                )
            asset_path = asset.get("assetPath")
            asset_basename = asset_path.rstrip("/").rsplit("/", 1)[-1] if isinstance(asset_path, str) else ""
            if asset_basename.startswith("umg_"):
                if basis != "project-umg-rule" or mode != "FillScreen":
                    errors.append(
                        issue(
                            "asset.design_size_mode.umg_rule",
                            decision_path,
                            "An umg_* Blueprint must use mode FillScreen with basis project-umg-rule; it is not evidence-analyzed.",
                        )
                    )
                if isinstance(decision_evidence, list) and decision_evidence:
                    errors.append(
                        issue(
                            "asset.design_size_mode.umg_decision_evidence",
                            f"{decision_path}.evidenceIds",
                            "An umg_* project rule decision must keep decision evidenceIds empty.",
                        )
                    )
            elif asset_basename.startswith("uw_"):
                if basis == "project-umg-rule":
                    errors.append(
                        issue(
                            "asset.design_size_mode.uw_analysis_required",
                            decision_path,
                            "A uw_* Blueprint must be analyzed and cannot use project-umg-rule.",
                        )
                    )
            elif basis != "fallback-unclear" or mode != "FillScreen":
                errors.append(
                    issue(
                        "asset.design_size_mode.legacy_name_fallback",
                        decision_path,
                        "A non-umg/non-uw legacy Blueprint name must conservatively use fallback-unclear with FillScreen.",
                    )
                )
        if asset.get("id") in asset.get("dependsOnAssetIds", []):
            errors.append(issue("asset.self_dependency", f"$.assetPlan[{asset_id}].dependsOnAssetIds", "An asset cannot depend on itself."))
        order = asset.get("buildOrder")
        if asset.get("inBuildScope") is True and not isinstance(order, int):
            errors.append(issue("asset.build_order_required", f"$.assetPlan[{asset_id}].buildOrder", "In-scope assets require buildOrder."))
        if asset.get("inBuildScope") is False and order is not None:
            errors.append(issue("asset.scoped_out_order", f"$.assetPlan[{asset_id}].buildOrder", "Out-of-scope assets must use null buildOrder."))
        if asset.get("inBuildScope") is True and isinstance(order, int):
            if order in build_orders:
                errors.append(issue("asset.build_order", f"$.assetPlan[{asset_id}].buildOrder", "buildOrder must be unique."))
            build_orders.add(order)
        if asset.get("assetKind") == "screen" and asset.get("referenceSize") != [2560, 1440]:
            errors.append(issue("asset.screen_resolution", f"$.assetPlan[{asset_id}].referenceSize", "Screen assets must use [2560, 1440]."))
    if static_visual_coverage_required and (review_status == "accepted" or in_scope_asset_ids):
        covered_element_ids = {
            element_id
            for asset in assets.values()
            if asset.get("inBuildScope") is True
            for element_id in asset.get("coversElementIds", [])
            if isinstance(element_id, str)
        }
        for element_id in sorted(static_visual_element_ids):
            element = elements[element_id]
            if element.get("inBuildScope") is True and element_id not in covered_element_ids:
                errors.append(
                    issue(
                        "visual.asset_composition",
                        f"$.uiModel.elements[{element_id}]",
                        "Every in-scope static visual element must be covered by an in-scope asset plan.",
                    )
                )
    if build_orders and build_orders != set(range(len(in_scope_asset_ids))):
        errors.append(issue("asset.build_order_sequence", "$.assetPlan", "buildOrder values must form a zero-based contiguous sequence."))
    if target_paths != planned_paths:
        errors.append(issue("target.asset_plan", "$.target.targetAssetPaths", "targetAssetPaths must exactly match all in-scope assetPlan paths."))
    if target.get("assetKind") == "screen" and target.get("designCanvas") != [2560, 1440]:
        errors.append(issue("target.screen_resolution", "$.target.designCanvas", "Complete screens must use [2560, 1440]."))
    if review_status == "accepted":
        unresolved_target_fields = [
            key
            for key in ("system", "systemFolder", "mode", "assetKind", "designCanvas", "productionAuthorized")
            if target.get(key) is None
        ]
        if unresolved_target_fields or not target_paths:
            errors.append(
                issue(
                    "target.unresolved",
                    "$.target",
                    f"Accepted review requires a fully resolved target and at least one asset path; unresolved={unresolved_target_fields}.",
                )
            )
    if target.get("mode") == "production" and target.get("productionAuthorized") is not True:
        errors.append(issue("target.production_authorization", "$.target.productionAuthorized", "Production mode requires explicit authorization."))

    # Cycle detection also catches indirect self-dependencies.
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(asset_id: str) -> None:
        if asset_id in visited:
            return
        if asset_id in visiting:
            errors.append(issue("asset.dependency_cycle", "$.assetPlan", f"Asset dependency cycle includes {asset_id}."))
            return
        visiting.add(asset_id)
        for dependency in assets.get(asset_id, {}).get("dependsOnAssetIds", []):
            if isinstance(dependency, str) and dependency in assets:
                visit(dependency)
        visiting.remove(asset_id)
        visited.add(asset_id)

    for asset_id in assets:
        visit(asset_id)

    for asset_id, asset in assets.items():
        if asset.get("inBuildScope") is not True:
            continue
        asset_order = asset.get("buildOrder")
        for dependency in asset.get("dependsOnAssetIds", []):
            dependency_asset = assets.get(dependency)
            if not isinstance(dependency_asset, dict):
                continue
            if dependency_asset.get("inBuildScope") is not True:
                errors.append(
                    issue(
                        "asset.scoped_out_dependency",
                        f"$.assetPlan[{asset_id}].dependsOnAssetIds",
                        f"In-scope asset cannot depend on out-of-scope asset {dependency}.",
                    )
                )
                continue
            dependency_order = dependency_asset.get("buildOrder")
            if isinstance(asset_order, int) and isinstance(dependency_order, int) and dependency_order >= asset_order:
                errors.append(
                    issue(
                        "asset.dependency_order",
                        f"$.assetPlan[{asset_id}].dependsOnAssetIds",
                        f"Dependency {dependency} must have an earlier buildOrder.",
                    )
                )

    for assumption_id, assumption in by_kind.get("assumption", {}).items():
        _check_refs(errors, assumption.get("evidenceIds"), evidence_ids, f"$.assumptions[{assumption_id}].evidenceIds", "evidence")
        _check_refs(errors, assumption.get("affectsClaimIds"), claim_ids, f"$.assumptions[{assumption_id}].affectsClaimIds", "claim")
    for question_id, question in by_kind.get("question", {}).items():
        _check_refs(errors, question.get("affectsClaimIds"), claim_ids, f"$.questions[{question_id}].affectsClaimIds", "claim")
        if question.get("status") == "answered" and not isinstance(question.get("answer"), str):
            errors.append(issue("question.answer", f"$.questions[{question_id}].answer", "Answered questions require answer."))
        if question.get("status") == "open" and question.get("answer") is not None:
            errors.append(issue("question.open_answer", f"$.questions[{question_id}].answer", "Open questions must not include answer."))
    for criterion_id, criterion in by_kind.get("acceptance-criterion", {}).items():
        _check_refs(errors, criterion.get("claimIds"), claim_ids, f"$.acceptanceCriteria[{criterion_id}].claimIds", "claim")

    if request_packet is not None:
        packet_schema = request_packet_schema or load_json(REQUEST_PACKET_SCHEMA)
        packet_result = validate_request_packet(request_packet, packet_schema, packet_path=request_packet_path)
        if not packet_result["valid"]:
            errors.append(issue("packet.invalid", "$", "Referenced RequestPacket is invalid."))
        if spec.get("requestId") != request_packet.get("requestId"):
            errors.append(issue("packet.request_id", "$.requestId", "requestId must match RequestPacket."))
        if spec.get("inputDigest") != request_packet.get("inputDigest"):
            errors.append(issue("packet.input_digest", "$.inputDigest", "inputDigest must match RequestPacket."))
        packet_user_request = request_packet.get("userRequest") if isinstance(request_packet.get("userRequest"), dict) else {}
        spec_request = spec.get("request") if isinstance(spec.get("request"), dict) else {}
        if spec_request.get("originalText") != packet_user_request.get("originalText"):
            errors.append(issue("packet.original_text", "$.request.originalText", "Requirement originalText must exactly preserve RequestPacket user text."))
        if spec_request.get("language") != packet_user_request.get("language"):
            errors.append(issue("packet.language", "$.request.language", "Requirement language must match RequestPacket userRequest.language."))
        packet_sources = {
            item.get("sourceKey"): item
            for item in request_packet.get("sources", [])
            if isinstance(item, dict) and isinstance(item.get("sourceKey"), str)
        }
        spec_sources = {
            source.get("sourceKey"): source
            for source in spec.get("sources", [])
            if isinstance(source, dict) and isinstance(source.get("sourceKey"), str)
        }
        if set(spec_sources) != set(packet_sources):
            errors.append(issue("packet.source_set", "$.sources", "Requirement sources must exactly match RequestPacket source keys."))
        for source in spec.get("sources", []):
            if not isinstance(source, dict):
                continue
            packet_source = packet_sources.get(source.get("sourceKey"))
            if packet_source is None:
                errors.append(issue("packet.source", "$.sources", f"Requirement source {source.get('sourceKey')} is not in RequestPacket."))
            else:
                comparisons = {
                    "kind": "kind",
                    "locatorKind": "locatorKind",
                    "description": "description",
                    "path": "path",
                    "content": "content",
                    "mediaType": "mediaType",
                    "snapshotPath": "snapshotPath",
                    "contentSha256": "contentSha256",
                    "dimensions": "imageSize",
                }
                for spec_key, packet_key in comparisons.items():
                    if source.get(spec_key) != packet_source.get(packet_key):
                        errors.append(
                            issue(
                                "packet.source_field",
                                "$.sources",
                                f"Source {source.get('sourceKey')} field {spec_key} differs from RequestPacket.",
                            )
                        )
        target_hints = request_packet.get("targetHints") if isinstance(request_packet.get("targetHints"), dict) else {}
        target_spec = spec.get("target") if isinstance(spec.get("target"), dict) else {}
        for key in ("system", "systemFolder", "assetKind", "mode", "designCanvas", "productionAuthorized"):
            hinted = target_hints.get(key)
            if hinted is not None and hinted != "unknown" and target_spec.get(key) != hinted:
                errors.append(issue("packet.target_hint", f"$.target.{key}", f"Resolved target field {key} conflicts with RequestPacket targetHints."))
        hinted_paths = target_hints.get("targetAssetPaths")
        resolved_paths = target_spec.get("targetAssetPaths")
        if (
            isinstance(hinted_paths, list)
            and hinted_paths != "unknown"
            and not (review_status == "pending" and resolved_paths == [])
            and (not isinstance(resolved_paths, list) or not set(hinted_paths).issubset(resolved_paths))
        ):
            errors.append(issue("packet.target_hint", "$.target.targetAssetPaths", "Resolved targetAssetPaths must include every RequestPacket target hint."))

    return result(errors, warnings)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path, help="Path to UIRequirementSpec JSON.")
    parser.add_argument("--request-packet", type=Path, required=True)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--request-schema", type=Path, default=REQUEST_PACKET_SCHEMA)
    parser.add_argument("--check-findings-files", action="store_true", help="Rehash every normalization findingsRef relative to the RequirementSpec.")
    args = parser.parse_args()
    try:
        packet = load_json(args.request_packet) if args.request_packet else None
        output = validate_requirement_spec(
            load_json(args.spec),
            load_json(args.schema),
            packet,
            load_json(args.request_schema),
            args.request_packet.resolve(),
            args.spec.resolve(),
            args.check_findings_files,
        )
    except (OSError, json.JSONDecodeError, ValueError) as error:
        output = result([issue("io.read", "$", str(error))])
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if output["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
