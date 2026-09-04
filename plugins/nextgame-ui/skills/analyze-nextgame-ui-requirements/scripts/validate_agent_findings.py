#!/usr/bin/env python3
"""Validate NextGame UI AgentFindings 0.1 without third-party packages."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from _contract_common import (
    ASSETS_ROOT,
    canonical_sha256,
    collect_canonical_ids,
    find_forbidden_keys,
    issue,
    load_json,
    result,
    sha256_file,
    validate_schema_instance,
)
from validate_request_packet import DEFAULT_SCHEMA as REQUEST_SCHEMA, validate_request_packet
from prepare_agent_inputs import (
    DEFAULT_PACKET_SCHEMA as ROLE_PACKET_SCHEMA,
    DEFAULT_ROLE_CARDS,
    build_context_projection,
    validate_role_packet,
)


DEFAULT_SCHEMA = ASSETS_ROOT / "agent-findings.schema.json"
FORBIDDEN_CANONICAL_KEYS = {
    "canonicalId",
    "regionId",
    "elementId",
    "componentFamilyId",
    "collectionId",
    "stateModelId",
    "axisId",
    "stateId",
    "assetPlanId",
    "claimId",
    "claimIds",
}
ROUND_ONE_ROLES = {"visual-structure", "text-requirements", "project-pattern"}
CONTEXT_ROLES = {
    "state-modeling",
    "data-adaptation",
    "asset-decomposition",
    "state-visual-review",
    "schema-feasibility-review",
    "coverage-review",
}
ROUND_ONE_SOURCE_KINDS = {
    "visual-structure": {"image"},
    "text-requirements": {"user-text"},
    "project-pattern": {"project-rule", "project-asset"},
}


def validate_agent_findings(
    findings: Any,
    schema: dict[str, Any],
    request_packet: dict[str, Any] | None = None,
    request_schema: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    request_packet_path: Path | None = None,
    context_path: Path | None = None,
    role_packet: dict[str, Any] | None = None,
    role_packet_path: Path | None = None,
    base_context_path: Path | None = None,
    draft_requirement_path: Path | None = None,
) -> dict[str, Any]:
    errors = validate_schema_instance(findings, schema)
    warnings: list[dict[str, str]] = []
    if not isinstance(findings, dict):
        return result(errors, warnings)

    for key, path in find_forbidden_keys(findings, FORBIDDEN_CANONICAL_KEYS):
        errors.append(
            issue(
                "findings.canonical_id_forbidden",
                path,
                f"AgentFindings must use localId/local references; canonical field {key!r} is forbidden.",
            )
        )

    all_local_ids: set[str] = set()
    evidence_ids: set[str] = set()
    finding_ids: set[str] = set()
    for section in ("evidence", "findings", "questionCandidates"):
        items = findings.get(section)
        if not isinstance(items, list):
            continue
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            local_id = item.get("localId")
            if isinstance(local_id, str):
                if local_id in all_local_ids:
                    errors.append(issue("id.duplicate", f"$.{section}[{index}].localId", "localId must be unique across the document."))
                all_local_ids.add(local_id)
                if section == "evidence":
                    evidence_ids.add(local_id)
                elif section == "findings":
                    finding_ids.add(local_id)

    raw_findings = findings.get("findings")
    if isinstance(raw_findings, list):
        for index, finding in enumerate(raw_findings):
            if not isinstance(finding, dict):
                continue
            evidence_refs = finding.get("evidenceRefs")
            if not isinstance(evidence_refs, list) or not evidence_refs:
                errors.append(issue("finding.evidence_required", f"$.findings[{index}].evidenceRefs", "Each finding requires evidence."))
            elif all(isinstance(ref, str) for ref in evidence_refs):
                for ref in evidence_refs:
                    if ref not in evidence_ids:
                        errors.append(issue("ref.evidence", f"$.findings[{index}].evidenceRefs", f"Unknown evidence localId: {ref}"))

    role = findings.get("agentRole")
    if role in ROUND_ONE_ROLES:
        if findings.get("contextDigest") is not None or context is not None:
            errors.append(
                issue(
                    "findings.round_one_isolation",
                    "$.contextDigest",
                    "Round-one evidence agents must not receive normalized canonical context.",
                )
            )
        for index, finding in enumerate(findings.get("findings", [])):
            if isinstance(finding, dict) and finding.get("subjectRefs") is not None:
                errors.append(
                    issue(
                        "findings.round_one_subjects",
                        f"$.findings[{index}].subjectRefs",
                        "Round-one agents may only emit local findings, not canonical subjectRefs.",
                    )
                )
    elif role in CONTEXT_ROLES:
        if context is None:
            errors.append(
                issue(
                    "findings.context_required",
                    "$.contextDigest",
                    "Round-two and review agents require normalized context supplied with --context.",
                )
            )
        else:
            try:
                expected_context_digest = canonical_sha256(context)
            except (TypeError, ValueError) as error:
                expected_context_digest = None
                errors.append(issue("findings.context_digest_input", "$.contextDigest", f"Context is not canonically serializable: {error}"))
            if findings.get("contextDigest") != expected_context_digest:
                errors.append(
                    issue(
                        "findings.context_digest",
                        "$.contextDigest",
                        f"contextDigest does not match normalized context; expected {expected_context_digest}.",
                    )
                )
            canonical_ids = collect_canonical_ids(context)
            if context.get("requestId") != findings.get("requestId"):
                errors.append(issue("findings.context_request_id", "$.contextDigest", "Context requestId must match AgentFindings."))
            if context.get("inputDigest") != findings.get("inputDigest"):
                errors.append(issue("findings.context_input_digest", "$.contextDigest", "Context inputDigest must match the parent RequestPacket digest."))
            projection = context.get("projection")
            if context.get("contextKind") == "normalized-role-projection":
                if not isinstance(projection, dict):
                    errors.append(issue("findings.context_projection", "$.contextDigest", "Projected context requires projection metadata."))
                elif projection.get("agentRole") != role:
                    errors.append(issue("findings.context_projection_role", "$.contextDigest", "Projected context role must match AgentFindings."))
                elif request_packet_path is None or context_path is None or base_context_path is None:
                    errors.append(issue("findings.context_projection_path", "$.contextDigest", "Projected context validation requires external RequestPacket, role-context, and authoritative base-context paths."))
                else:
                    try:
                        request_root = request_packet_path.resolve().parent
                        base_path = base_context_path.resolve()
                        base_path.relative_to(request_root)
                        base_context = load_json(base_path)
                        rebuilt, _, _ = build_context_projection(
                            base_context,
                            request_packet or {},
                            str(role),
                            base_context_ref=base_path.relative_to(request_root).as_posix(),
                            base_file_sha256=sha256_file(base_path),
                        )
                        if context != rebuilt:
                            errors.append(issue("findings.context_projection_stale", "$.contextDigest", "Projected context is not the deterministic projection of its bound authoritative context."))
                    except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
                        errors.append(issue("findings.context_projection_invalid", "$.contextDigest", str(error)))
            for index, finding in enumerate(findings.get("findings", [])):
                if not isinstance(finding, dict):
                    continue
                subject_refs = finding.get("subjectRefs")
                if not isinstance(subject_refs, list) or not subject_refs:
                    errors.append(
                        issue(
                            "findings.subjects_required",
                            f"$.findings[{index}].subjectRefs",
                            "Context-aware findings must bind to existing canonical subjects.",
                        )
                    )
                    continue
                for subject_ref in subject_refs:
                    if subject_ref not in canonical_ids:
                        errors.append(
                            issue(
                                "findings.subject_ref",
                                f"$.findings[{index}].subjectRefs",
                                f"Unknown canonical subject id: {subject_ref}",
                            )
                        )

    questions = findings.get("questionCandidates")
    if isinstance(questions, list):
        for index, question in enumerate(questions):
            if not isinstance(question, dict):
                continue
            refs = question.get("relatedFindingRefs")
            if isinstance(refs, list):
                for ref in refs:
                    if isinstance(ref, str) and ref not in finding_ids:
                        errors.append(issue("ref.finding", f"$.questionCandidates[{index}].relatedFindingRefs", f"Unknown finding localId: {ref}"))

    if request_packet is None:
        errors.append(
            issue(
                "packet.required",
                "$",
                "AgentFindings validation requires the originating RequestPacket.",
            )
        )
    else:
        request_schema = request_schema or load_json(REQUEST_SCHEMA)
        packet_result = validate_request_packet(request_packet, request_schema, packet_path=request_packet_path)
        if not packet_result["valid"]:
            errors.append(issue("packet.invalid", "$", "Referenced RequestPacket is invalid."))
        if findings.get("requestId") != request_packet.get("requestId"):
            errors.append(issue("packet.request_id", "$.requestId", "requestId must match the RequestPacket."))
        if findings.get("inputDigest") != request_packet.get("inputDigest"):
            errors.append(issue("packet.input_digest", "$.inputDigest", "inputDigest must match the RequestPacket."))
        packet_sources = {
            source.get("sourceKey"): source
            for source in request_packet.get("sources", [])
            if isinstance(source, dict) and isinstance(source.get("sourceKey"), str)
        }
        allowed_sources = set(packet_sources)
        source_scope = set(findings.get("sourceScope", [])) if isinstance(findings.get("sourceScope"), list) else set()
        unknown_scope = source_scope - allowed_sources
        if unknown_scope:
            errors.append(issue("packet.source_scope", "$.sourceScope", f"sourceScope contains undeclared sources: {sorted(unknown_scope)}"))
        allowed_kinds = ROUND_ONE_SOURCE_KINDS.get(role)
        if allowed_kinds is not None:
            relevant_sources = {
                source_key
                for source_key, source in packet_sources.items()
                if source.get("kind") in allowed_kinds
            }
            if source_scope != relevant_sources:
                errors.append(
                    issue(
                        "findings.role_source_scope",
                        "$.sourceScope",
                        f"Role {role} sourceScope must exactly contain its isolated relevant sources: {sorted(relevant_sources)}.",
                    )
                )
            wrong_kind = {
                source_key
                for source_key in source_scope
                if packet_sources.get(source_key, {}).get("kind") not in allowed_kinds
            }
            if wrong_kind:
                errors.append(
                    issue(
                        "findings.role_source_scope",
                        "$.sourceScope",
                        f"Role {role} received disallowed source kinds: {sorted(wrong_kind)}.",
                    )
                )
            if not source_scope and any(findings.get(section) for section in ("findings", "evidence", "questionCandidates")):
                errors.append(
                    issue(
                        "findings.empty_scope_output",
                        "$.sourceScope",
                        "An agent with no relevant sources must emit empty findings, evidence, and questionCandidates.",
                    )
                )
        for index, evidence in enumerate(findings.get("evidence", [])):
            if not isinstance(evidence, dict):
                continue
            source_key = evidence.get("sourceKey")
            if source_key not in allowed_sources:
                errors.append(issue("packet.source_ref", f"$.evidence[{index}].sourceKey", "sourceKey is not declared by the RequestPacket."))
            elif source_key not in source_scope:
                errors.append(issue("findings.source_scope", f"$.evidence[{index}].sourceKey", "Evidence uses a source outside this agent's isolated sourceScope."))

    if role_packet is not None:
        if request_packet_path is None or role_packet_path is None:
            errors.append(issue("findings.role_packet_path", "$", "Role-packet validation requires both RequestPacket and role-packet paths."))
        else:
            packet_validation = validate_role_packet(
                role_packet,
                packet_path=role_packet_path,
                request_root=request_packet_path.resolve().parent,
                request_path=request_packet_path,
                base_context_path=base_context_path,
                draft_requirement_path=draft_requirement_path,
                packet_schema=load_json(ROLE_PACKET_SCHEMA),
                role_cards_path=DEFAULT_ROLE_CARDS,
            )
            if not packet_validation["valid"]:
                nested_codes = sorted({entry["code"] for entry in packet_validation["errors"]})
                errors.append(issue("findings.role_packet_invalid", "$", f"Linked no-history role packet failed validation: {nested_codes}."))
            if role_packet.get("agentRole") != role:
                errors.append(issue("findings.role_packet_role", "$.agentRole", "AgentFindings role must match the role packet."))
            if role_packet.get("requestId") != findings.get("requestId") or role_packet.get("inputDigest") != findings.get("inputDigest"):
                errors.append(issue("findings.role_packet_identity", "$", "AgentFindings request identity must match the role packet."))
            if role_packet.get("sourceScope") != findings.get("sourceScope"):
                errors.append(issue("findings.role_packet_scope", "$.sourceScope", "AgentFindings sourceScope must exactly match the role packet."))
            packet_context = role_packet.get("context")
            if isinstance(packet_context, dict) and findings.get("contextDigest") != packet_context.get("canonicalSha256"):
                errors.append(issue("findings.role_packet_context", "$.contextDigest", "AgentFindings contextDigest must match the role packet projection."))

    return result(errors, warnings)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("findings", type=Path, help="Path to AgentFindings JSON.")
    parser.add_argument("--request-packet", type=Path, required=True)
    parser.add_argument("--context", type=Path, help="Normalized canonical context for round-two/review roles.")
    parser.add_argument("--base-context", type=Path, help="Authoritative full normalized context used to verify a projected role context.")
    parser.add_argument("--draft-requirement", type=Path, help="Immutable full pending Requirement used only to validate a review-view binding.")
    parser.add_argument("--role-packet", type=Path, help="Validated no-history role packet that produced these findings.")
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--request-schema", type=Path, default=REQUEST_SCHEMA)
    args = parser.parse_args()
    try:
        packet = load_json(args.request_packet) if args.request_packet else None
        context = load_json(args.context) if args.context else None
        role_packet = load_json(args.role_packet) if args.role_packet else None
        output = validate_agent_findings(
            load_json(args.findings),
            load_json(args.schema),
            packet,
            load_json(args.request_schema),
            context,
            args.request_packet.resolve(),
            args.context.resolve() if args.context else None,
            role_packet,
            args.role_packet.resolve() if args.role_packet else None,
            args.base_context.resolve() if args.base_context else None,
            args.draft_requirement.resolve() if args.draft_requirement else None,
        )
    except (OSError, json.JSONDecodeError, ValueError) as error:
        output = result([issue("io.read", "$", str(error))])
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if output["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
