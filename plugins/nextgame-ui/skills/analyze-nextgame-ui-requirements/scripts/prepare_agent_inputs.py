#!/usr/bin/env python3
"""Create and validate self-contained, no-history inputs for analysis agents.

The optimization is deliberately conservative.  Role projections copy the
normalized context byte-for-byte at the JSON value level and remove only the
normalizer-only ``firstRoundTrace`` plus role-irrelevant future-planning lists.
Unknown context shapes or dangling canonical references automatically expand
to a compact full-context fallback.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from _contract_common import (
    ASSETS_ROOT,
    canonical_sha256,
    collect_canonical_ids,
    issue,
    load_json,
    result,
    sha256_file,
    validate_schema_instance,
)
from validate_request_packet import DEFAULT_SCHEMA as REQUEST_SCHEMA
from validate_request_packet import validate_request_packet
from review_view import ROLE_TO_PROFILE, build_review_view
from validate_review_view import validate_review_view


SKILL_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = SKILL_ROOT.parent.parent
PLUGIN_SCRIPTS = PLUGIN_ROOT / "scripts"
if str(PLUGIN_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(PLUGIN_SCRIPTS))

from shortlist_shared_widgets import build_shortlist as build_registry_shortlist

PLUGIN_MANIFEST = PLUGIN_ROOT / ".codex-plugin" / "plugin.json"
DEFAULT_ROLE_CARDS = ASSETS_ROOT / "analysis-role-cards.json"
DEFAULT_PACKET_SCHEMA = ASSETS_ROOT / "analysis-role-packet.schema.json"
FINDINGS_SCHEMA = ASSETS_ROOT / "agent-findings.schema.json"
REQUIREMENT_SCHEMA = ASSETS_ROOT / "ui-requirement-spec.schema.json"
REVIEW_VIEW_SCHEMA = ASSETS_ROOT / "review-view.schema.json"
COVERAGE_REPORT_SCHEMA = ASSETS_ROOT / "visual-coverage-report.schema.json"

DISCOVERY_ROLES = ("visual-structure", "text-requirements", "project-pattern")
FOCUSED_ROLES = ("state-modeling", "data-adaptation", "asset-decomposition")
REVIEW_ROLES = ("state-visual-review", "schema-feasibility-review", "coverage-review")
CONTEXT_ROLES = FOCUSED_ROLES + REVIEW_ROLES
ALL_ROLES = DISCOVERY_ROLES + CONTEXT_ROLES

DISCOVERY_SOURCE_KINDS = {
    "visual-structure": {"image"},
    "text-requirements": {"user-text"},
    "project-pattern": {"project-rule", "project-asset"},
}
RAW_REVIEW_SOURCE_KINDS = {
    "state-visual-review": {"image"},
    "coverage-review": {"image"},
}

ROLE_PROFILE = {
    "state-modeling": "state-modeling-v1",
    "data-adaptation": "data-adaptation-v1",
    "asset-decomposition": "asset-decomposition-v1",
    "state-visual-review": "state-visual-review-v1",
    "schema-feasibility-review": "schema-feasibility-review-v1",
    "coverage-review": "coverage-review-v1",
}
KEEP_PLANNED_ASSETS = {
    "data-adaptation",
    "asset-decomposition",
    "schema-feasibility-review",
    "coverage-review",
}
KEEP_REUSE_CANDIDATES = {"asset-decomposition", "schema-feasibility-review"}
OMITTABLE_SECTION_ITEM_KEYS = {
    "plannedAssets": {"id", "assetPath", "assetKind"},
    "reuseCandidates": {"id", "assetPath", "status"},
}

CONTEXT_REQUIRED_KEYS = {
    "version",
    "contextKind",
    "authoritative",
    "notice",
    "requestId",
    "inputDigest",
    "sources",
    "target",
    "evidence",
    "preliminaryClaims",
    "regions",
    "componentFamilies",
    "elements",
    "collections",
    "runtimeFields",
    "responsiveIntents",
    "stateModels",
    "plannedAssets",
    "reuseCandidates",
    "acceptanceCriteria",
    "questions",
    "firstRoundTrace",
}
CONTEXT_KNOWN_KEYS = set(CONTEXT_REQUIRED_KEYS)
CONTEXT_ARRAY_KEYS = {
    "sources",
    "evidence",
    "preliminaryClaims",
    "regions",
    "componentFamilies",
    "elements",
    "collections",
    "runtimeFields",
    "responsiveIntents",
    "stateModels",
    "plannedAssets",
    "reuseCandidates",
    "acceptanceCriteria",
    "questions",
}
CANONICAL_REFERENCE_KEYS = {
    "canonicalId",
    "subjectRefs",
    "evidenceId",
    "evidenceIds",
    "claimId",
    "claimIds",
    "regionId",
    "regionIds",
    "parentRegionId",
    "componentFamilyId",
    "componentFamilyIds",
    "familyId",
    "familyIds",
    "elementId",
    "elementIds",
    "memberElementIds",
    "collectionId",
    "collectionIds",
    "runtimeFieldId",
    "runtimeFieldIds",
    "responsiveIntentId",
    "responsiveIntentIds",
    "stateModelId",
    "stateModelIds",
    "axisId",
    "axisIds",
    "stateId",
    "stateIds",
    "targetStateIds",
    "assetId",
    "assetIds",
    "assetPlanId",
    "assetPlanIds",
    "plannedAssetId",
    "plannedAssetIds",
    "coversElementIds",
    "dependsOnAssetIds",
    "ownerIntentId",
}
NONCANONICAL_REFERENCE_LIKE_KEYS = {
    "requestId",
    "localId",
    "findingsRefs",
}
FORBIDDEN_PACKET_KEYS = {
    "messages",
    "conversationHistory",
    "history",
    "model",
    "modelOverride",
    "reasoningEffort",
    "thinking",
}
ROLE_ATTACHMENT_KINDS: dict[str, set[str]] = {
    "project-pattern": {"registry-shortlist"},
    "asset-decomposition": {"registry-shortlist"},
}


class AgentInputError(ValueError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def compact_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode("utf-8")


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = compact_bytes(value)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def request_relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as error:
        raise AgentInputError(f"path must remain inside the request directory: {path}") from error


def resolve_request_ref(root: Path, raw_ref: str) -> Path:
    candidate = Path(raw_ref)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise AgentInputError(f"request-relative path is invalid: {raw_ref}")
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as error:
        raise AgentInputError(f"path escapes the request directory: {raw_ref}") from error
    return resolved


def _walk_keys(value: Any, path: str = "$") -> Iterable[tuple[str, str]]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            yield key, child_path
            yield from _walk_keys(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_keys(child, f"{path}[{index}]")


def _all_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _all_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _all_strings(child)


def _reference_values(value: Any, known_ids: set[str]) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in known_ids:
                refs.add(key)
            if key in CANONICAL_REFERENCE_KEYS:
                if isinstance(child, str):
                    refs.add(child)
                elif isinstance(child, list):
                    refs.update(item for item in child if isinstance(item, str))
            refs.update(_reference_values(child, known_ids))
    elif isinstance(value, list):
        for child in value:
            refs.update(_reference_values(child, known_ids))
    return refs


def _canonical_id_counts(value: Any, counts: dict[str, int] | None = None) -> dict[str, int]:
    """Count canonical definitions without collapsing duplicate ``id`` values."""

    if counts is None:
        counts = {}
    if isinstance(value, dict):
        identifier = value.get("id")
        if isinstance(identifier, str) and not identifier.startswith("local-"):
            counts[identifier] = counts.get(identifier, 0) + 1
        for child in value.values():
            _canonical_id_counts(child, counts)
    elif isinstance(value, list):
        for child in value:
            _canonical_id_counts(child, counts)
    return counts


def _unknown_reference_like_keys(value: Any) -> set[str]:
    """Reject new reference-shaped fields until projection semantics are reviewed."""

    unknown: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            reference_like = key.endswith(("Id", "Ids", "Refs"))
            if (
                reference_like
                and key not in CANONICAL_REFERENCE_KEYS
                and key not in NONCANONICAL_REFERENCE_LIKE_KEYS
            ):
                unknown.add(key)
            unknown.update(_unknown_reference_like_keys(child))
    elif isinstance(value, list):
        for child in value:
            unknown.update(_unknown_reference_like_keys(child))
    return unknown


def _validate_authoritative_context(context: Any, request: dict[str, Any]) -> None:
    if not isinstance(context, dict):
        raise AgentInputError("normalized context must be an object")
    missing = sorted(CONTEXT_REQUIRED_KEYS - set(context))
    if missing:
        raise AgentInputError(
            "normalized context is missing required sections: " + ", ".join(missing)
        )
    if context.get("version") != "0.1":
        raise AgentInputError("normalized context version must be 0.1")
    if context.get("contextKind") != "normalized-first-round":
        raise AgentInputError("base contextKind must be normalized-first-round")
    if context.get("authoritative") is not True:
        raise AgentInputError("base normalized context must be authoritative")
    if not isinstance(context.get("notice"), str) or not context["notice"]:
        raise AgentInputError("normalized context notice must be non-empty")
    if context.get("requestId") != request.get("requestId"):
        raise AgentInputError("normalized context requestId does not match RequestPacket")
    if context.get("inputDigest") != request.get("inputDigest"):
        raise AgentInputError("normalized context inputDigest does not match RequestPacket")
    if not isinstance(context.get("target"), dict):
        raise AgentInputError("normalized context target must be an object")
    if not isinstance(context.get("firstRoundTrace"), dict):
        raise AgentInputError("normalized context firstRoundTrace must be an object")
    invalid_arrays = sorted(key for key in CONTEXT_ARRAY_KEYS if not isinstance(context.get(key), list))
    if invalid_arrays:
        raise AgentInputError(
            "normalized context sections must be arrays: " + ", ".join(invalid_arrays)
        )
    non_object_sections = sorted(
        key
        for key in CONTEXT_ARRAY_KEYS
        if any(not isinstance(item, dict) for item in context.get(key, []))
    )
    if non_object_sections:
        raise AgentInputError(
            "normalized context sections must contain objects: "
            + ", ".join(non_object_sections)
        )
    unknown_reference_keys = sorted(_unknown_reference_like_keys(context))
    if unknown_reference_keys:
        raise AgentInputError(
            "normalized context contains unreviewed reference-like fields: "
            + ", ".join(unknown_reference_keys)
        )
    duplicate_ids = sorted(
        identifier
        for identifier, count in _canonical_id_counts(context).items()
        if count > 1
    )
    if duplicate_ids:
        raise AgentInputError(
            "normalized context contains duplicate canonical ids: "
            + ", ".join(duplicate_ids[:16])
        )
    canonical_ids = collect_canonical_ids(context)
    dangling = sorted(_reference_values(context, canonical_ids) - canonical_ids)
    if dangling:
        raise AgentInputError(
            "normalized context contains dangling canonical references: "
            + ", ".join(dangling[:16])
        )
    canonical_sha256(context)


def _context_fallback_reason(context: dict[str, Any], role: str) -> str | None:
    unknown = sorted(set(context) - CONTEXT_KNOWN_KEYS)
    if unknown:
        return f"unknown-top-level-sections:{','.join(unknown)}"
    sections_to_omit: list[str] = []
    if role not in KEEP_PLANNED_ASSETS:
        sections_to_omit.append("plannedAssets")
    if role not in KEEP_REUSE_CANDIDATES:
        sections_to_omit.append("reuseCandidates")
    for section in sections_to_omit:
        allowed = OMITTABLE_SECTION_ITEM_KEYS[section]
        unknown_item_keys = sorted(
            {
                key
                for item in context[section]
                for key in item
                if key not in allowed
            }
        )
        if unknown_item_keys:
            return f"unknown-omittable-section-shape:{section}:{','.join(unknown_item_keys)}"
    return None


def build_context_projection(
    context: dict[str, Any],
    request: dict[str, Any],
    role: str,
    *,
    base_context_ref: str,
    base_file_sha256: str,
) -> tuple[dict[str, Any], str, str | None]:
    if role not in CONTEXT_ROLES:
        raise AgentInputError(f"role does not accept normalized context: {role}")
    _validate_authoritative_context(context, request)
    fallback_reason = _context_fallback_reason(context, role)
    projected = copy.deepcopy(context)
    if fallback_reason is None:
        projected.pop("firstRoundTrace", None)
        if role not in KEEP_PLANNED_ASSETS:
            projected.pop("plannedAssets", None)
        if role not in KEEP_REUSE_CANDIDATES:
            projected.pop("reuseCandidates", None)

        base_ids = collect_canonical_ids(context)
        projected_ids = collect_canonical_ids(projected)
        referenced_base_ids = {
            value for value in _all_strings(projected) if value in base_ids
        } | _reference_values(projected, base_ids)
        dangling = sorted(referenced_base_ids - projected_ids)
        if dangling:
            fallback_reason = "dangling-canonical-references:" + ",".join(dangling[:16])
            projected = copy.deepcopy(context)

    mode = "full-fallback" if fallback_reason else "projected"
    projected["contextKind"] = "normalized-role-projection"
    projected["authoritative"] = False
    projected["notice"] = (
        "Role-local immutable projection. The full normalized context remains authoritative for synthesis and alias/discard audit."
        if mode == "projected"
        else "Compact full-context fallback. Projection narrowing was disabled to preserve completeness."
    )
    projected["projection"] = {
        "version": "0.1",
        "agentRole": role,
        "profile": ROLE_PROFILE[role],
        "mode": mode,
        "baseContextCanonicalSha256": canonical_sha256(context),
        "omittedSections": (
            []
            if mode == "full-fallback"
            else [
                section
                for section in ("firstRoundTrace", "plannedAssets", "reuseCandidates")
                if section not in projected
            ]
        ),
        "fallbackReason": fallback_reason,
    }
    return projected, mode, fallback_reason


def load_role_cards(path: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    document = load_json(path)
    if not isinstance(document, dict) or document.get("version") != "0.1":
        raise AgentInputError("analysis role cards must use version 0.1")
    cards = document.get("cards")
    if not isinstance(cards, list):
        raise AgentInputError("analysis role cards must contain a cards array")
    by_role: dict[str, dict[str, Any]] = {}
    ids: set[str] = set()
    for card in cards:
        if not isinstance(card, dict):
            raise AgentInputError("every analysis role card must be an object")
        role = card.get("agentRole")
        card_id = card.get("id")
        if role not in ALL_ROLES or not isinstance(card_id, str):
            raise AgentInputError("analysis role card has an invalid role or id")
        if role in by_role or card_id in ids:
            raise AgentInputError("analysis role card roles and ids must be unique")
        if card.get("round") not in {"discovery", "focused", "review"}:
            raise AgentInputError(f"analysis role card has invalid round: {card_id}")
        if not isinstance(card.get("objective"), str) or not card["objective"]:
            raise AgentInputError(f"analysis role card objective is missing: {card_id}")
        constraints = card.get("constraints")
        if not isinstance(constraints, list) or not constraints or not all(isinstance(item, str) and item for item in constraints):
            raise AgentInputError(f"analysis role card constraints are invalid: {card_id}")
        by_role[role] = card
        ids.add(card_id)
    if set(by_role) != set(ALL_ROLES):
        raise AgentInputError("analysis role cards must cover all nine roles exactly once")
    return document, by_role


def _binding(root: Path, path: Path, kind: str | None = None) -> dict[str, str]:
    value = {"ref": request_relative(root, path), "sha256": sha256_file(path)}
    if kind is not None:
        value = {"kind": kind, **value}
    return value


def _source_binding(source: dict[str, Any]) -> dict[str, str]:
    binding = {"sourceKey": str(source["sourceKey"]), "kind": str(source["kind"])}
    if isinstance(source.get("contentSha256"), str):
        binding["contentSha256"] = source["contentSha256"]
    return binding


def _validate_registry_shortlist_attachment(
    artifact: Any,
    request: dict[str, Any],
) -> str | None:
    if not isinstance(artifact, dict) or artifact.get("kind") != "nextgame-shared-widget-shortlist":
        return "Registry shortlist attachment has the wrong kind or shape."
    if artifact.get("valid") is not True:
        return "Registry shortlist attachment is not valid."
    binding = artifact.get("registryBinding")
    if not isinstance(binding, dict) or not isinstance(binding.get("registrySha256"), str):
        return "Registry shortlist attachment has no Registry file binding."
    registry_sources = [
        source
        for source in request.get("sources", [])
        if isinstance(source, dict)
        and source.get("kind") == "project-asset"
        and source.get("locatorKind") == "local-file"
        and source.get("contentSha256") == binding["registrySha256"]
        and isinstance(source.get("path"), str)
    ]
    if len(registry_sources) != 1:
        return "Registry shortlist must bind exactly one immutable project-asset source in the RequestPacket."
    expected = build_registry_shortlist(
        artifact.get("request"),
        registry_path=Path(registry_sources[0]["path"]),
    )
    if artifact != expected:
        return "Registry shortlist differs from a fresh projection of its bound immutable Registry source."
    return None


def _validate_draft_requirement_attachment(
    draft: Any,
    request: dict[str, Any],
) -> str | None:
    if not isinstance(draft, dict):
        return "Draft Requirement must be an object."
    if draft.get("requestId") != request.get("requestId"):
        return "Draft Requirement requestId does not match the role packet RequestPacket."
    if draft.get("inputDigest") != request.get("inputDigest"):
        return "Draft Requirement inputDigest does not match the role packet RequestPacket."
    if draft.get("request", {}).get("originalText") != request.get("userRequest", {}).get("originalText"):
        return "Draft Requirement does not preserve the RequestPacket original user text."
    if draft.get("reviewGate", {}).get("status") != "pending":
        return "Reviewer packets require a pending draft Requirement, not an already adjudicated artifact."
    # Structural validation is intentionally centralized in build_review_view.
    # Its unknown-field stripping distinguishes the exact full-fallback cases
    # from true type/required/shape errors, including unknowns nested under
    # oneOf/anyOf.  Re-validating here with only aggregate error codes would turn
    # those safe fallback cases into false hard failures.
    return None


def _validate_coverage_evidence_attachment(
    report: Any,
    request: dict[str, Any],
) -> str | None:
    schema_errors = validate_schema_instance(report, load_json(COVERAGE_REPORT_SCHEMA))
    if schema_errors:
        codes = ",".join(sorted({item["code"] for item in schema_errors}))
        return f"Coverage evidence fails the closed visual-coverage report Schema: {codes}"
    if not isinstance(report, dict):
        return "Coverage evidence must be a JSON object."
    image_hashes = {
        source["contentSha256"]
        for source in request.get("sources", [])
        if isinstance(source, dict)
        and source.get("kind") == "image"
        and isinstance(source.get("contentSha256"), str)
    }
    if report.get("sourceSha256") not in image_hashes:
        return "Coverage evidence sourceSha256 is not an immutable image source in the RequestPacket."

    summary = report["summary"]
    gate = report["gate"]
    uncovered_count = summary["uncoveredHighOrMediumSalienceCount"]
    if gate["uncoveredHighOrMediumSalienceCount"] != uncovered_count:
        return "Coverage summary and gate uncovered counts disagree."
    if len(report["uncoveredCandidates"]) != uncovered_count:
        return "Coverage uncoveredCandidates length does not match the declared uncovered count."
    if len(report["reviewClusters"]) != summary["openReviewClusterCount"]:
        return "Coverage reviewClusters length does not match the declared cluster count."
    if gate["passesDraftGate"] is not (uncovered_count == 0):
        return "Coverage passesDraftGate is inconsistent with unresolved medium/high evidence."
    expected_status = (
        "no-medium-high-gaps-detected" if uncovered_count == 0 else "review-required"
    )
    if report["status"] != expected_status:
        return "Coverage status is inconsistent with unresolved medium/high evidence."
    return None


def _round_for(role: str) -> str:
    if role in DISCOVERY_ROLES:
        return "discovery"
    if role in FOCUSED_ROLES:
        return "focused"
    return "review"


def _select_source_scope(request: dict[str, Any], role: str) -> list[str]:
    sources = [item for item in request.get("sources", []) if isinstance(item, dict)]
    if role in DISCOVERY_ROLES:
        kinds = DISCOVERY_SOURCE_KINDS[role]
        return [str(item["sourceKey"]) for item in sources if item.get("kind") in kinds]
    return [str(item["sourceKey"]) for item in sources]


def _raw_sources(request: dict[str, Any], role: str) -> list[dict[str, Any]]:
    sources = [item for item in request.get("sources", []) if isinstance(item, dict)]
    if role in DISCOVERY_ROLES:
        kinds = DISCOVERY_SOURCE_KINDS[role]
    else:
        kinds = RAW_REVIEW_SOURCE_KINDS.get(role, set())
    return [copy.deepcopy(item) for item in sources if item.get("kind") in kinds]


def build_role_packet(
    *,
    request_root: Path,
    request_path: Path,
    request: dict[str, Any],
    role: str,
    role_card: dict[str, Any],
    role_cards_path: Path,
    context_binding: dict[str, Any] | None,
    additional_inputs: list[dict[str, str]],
    attached_inputs: list[dict[str, str]],
) -> dict[str, Any]:
    source_scope = _select_source_scope(request, role)
    source_by_key = {
        item["sourceKey"]: item
        for item in request.get("sources", [])
        if isinstance(item, dict) and isinstance(item.get("sourceKey"), str)
    }
    manifest = load_json(PLUGIN_MANIFEST)
    packet: dict[str, Any] = {
        "version": "0.1",
        "requestId": request["requestId"],
        "agentRole": role,
        "round": _round_for(role),
        "historyPolicy": {"mode": "none", "forkTurns": "none", "inheritsConversation": False},
        "inputDigest": request["inputDigest"],
        "requestBinding": {"sha256": sha256_file(request_path)},
        "roleCard": {
            "id": role_card["id"],
            "sha256": canonical_sha256(role_card),
            "objective": role_card["objective"],
            "constraints": role_card["constraints"],
        },
        "sourceScope": source_scope,
        "sources": _raw_sources(request, role),
        "sourceBindings": [_source_binding(source_by_key[key]) for key in source_scope],
        "outputRef": f"findings/{role}.json",
        "outputContract": {
            "schemaId": "https://nextgame.local/schemas/ui-agent-findings-0.1.json",
            "schemaSha256": sha256_file(FINDINGS_SCHEMA),
        },
        "authority": {
            "pluginVersion": manifest["version"],
            "pluginManifestSha256": sha256_file(PLUGIN_MANIFEST),
            "roleCardsSha256": sha256_file(role_cards_path),
        },
    }
    if context_binding is not None:
        packet["context"] = context_binding
    if additional_inputs:
        packet["additionalInputs"] = additional_inputs
    if attached_inputs:
        packet["attachedInputs"] = attached_inputs
    return packet


def _parse_named_path(raw: str) -> tuple[str, Path]:
    if "=" not in raw:
        raise argparse.ArgumentTypeError("binding must use KIND=PATH")
    kind, raw_path = raw.split("=", 1)
    if not kind or not raw_path:
        raise argparse.ArgumentTypeError("binding must use non-empty KIND=PATH")
    return kind, Path(raw_path)


def _semantic_context_without_projection(context: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(context)
    value.pop("projection", None)
    return value


def validate_role_packet(
    packet: Any,
    *,
    packet_path: Path,
    request_root: Path,
    request_path: Path | None,
    base_context_path: Path | None = None,
    draft_requirement_path: Path | None = None,
    packet_schema: dict[str, Any],
    role_cards_path: Path = DEFAULT_ROLE_CARDS,
) -> dict[str, Any]:
    errors = validate_schema_instance(packet, packet_schema)
    warnings: list[dict[str, str]] = []
    if not isinstance(packet, dict):
        return result(errors, warnings)

    for key, path in _walk_keys(packet):
        if key in FORBIDDEN_PACKET_KEYS:
            errors.append(issue("packet.history_forbidden", path, f"Forbidden history or model field: {key}"))

    try:
        packet_path.resolve().relative_to(request_root.resolve())
        cards_document, cards_by_role = load_role_cards(role_cards_path)
        role = packet.get("agentRole")
        request_binding = packet.get("requestBinding", {})
        if request_path is None:
            raise AgentInputError("external RequestPacket path is required for role-packet validation")
        request_path = request_path.resolve()
        request_path.relative_to(request_root.resolve())
        request = load_json(request_path)
        request_validation = validate_request_packet(
            request,
            load_json(REQUEST_SCHEMA),
            packet_path=request_path,
        )
        if not request_validation["valid"]:
            errors.append(issue("packet.request_invalid", "$.requestBinding", "Bound RequestPacket is invalid."))
        if request_binding.get("sha256") != sha256_file(request_path):
            errors.append(issue("packet.request_hash", "$.requestBinding.sha256", "RequestPacket file hash mismatch."))
        if packet.get("requestId") != request.get("requestId") or packet.get("inputDigest") != request.get("inputDigest"):
            errors.append(issue("packet.request_identity", "$", "Packet request identity does not match RequestPacket."))

        review_draft: dict[str, Any] | None = None
        review_draft_file_sha256: str | None = None
        if role in REVIEW_ROLES:
            if draft_requirement_path is None:
                raise AgentInputError(
                    "external immutable Draft Requirement path is required for review-view validation"
                )
            resolved_draft = draft_requirement_path.resolve()
            resolved_draft.relative_to(request_root.resolve())
            loaded_draft = load_json(resolved_draft)
            draft_problem = _validate_draft_requirement_attachment(loaded_draft, request)
            if draft_problem is not None:
                raise AgentInputError(draft_problem)
            if not isinstance(loaded_draft, dict):
                raise AgentInputError("Draft Requirement must be an object")
            review_draft = loaded_draft
            review_draft_file_sha256 = sha256_file(resolved_draft)

        card = cards_by_role.get(role)
        if card is None:
            errors.append(issue("packet.role", "$.agentRole", "Unknown analysis role."))
        else:
            expected_card = {
                "id": card["id"],
                "sha256": canonical_sha256(card),
                "objective": card["objective"],
                "constraints": card["constraints"],
            }
            if packet.get("roleCard") != expected_card:
                errors.append(issue("packet.role_card", "$.roleCard", "Role card does not match the authoritative role-card catalog."))
            if packet.get("round") != card.get("round"):
                errors.append(issue("packet.round", "$.round", "Packet round does not match its role card."))

        if packet.get("authority", {}).get("roleCardsSha256") != sha256_file(role_cards_path):
            errors.append(issue("packet.role_cards_hash", "$.authority.roleCardsSha256", "Role-card catalog hash mismatch."))
        manifest = load_json(PLUGIN_MANIFEST)
        expected_authority = {
            "pluginVersion": manifest["version"],
            "pluginManifestSha256": sha256_file(PLUGIN_MANIFEST),
            "roleCardsSha256": sha256_file(role_cards_path),
        }
        if packet.get("authority") != expected_authority:
            errors.append(issue("packet.plugin_authority", "$.authority", "Packet plugin authority binding is stale or invalid."))

        expected_scope = _select_source_scope(request, str(role)) if role in ALL_ROLES else []
        if packet.get("sourceScope") != expected_scope:
            errors.append(issue("packet.source_scope", "$.sourceScope", "Packet sourceScope is not the deterministic role scope."))
        if packet.get("sources") != (_raw_sources(request, str(role)) if role in ALL_ROLES else []):
            errors.append(issue("packet.sources", "$.sources", "Packet raw sources are not the exact deterministic RequestPacket subset."))
        source_by_key = {
            item["sourceKey"]: item
            for item in request.get("sources", [])
            if isinstance(item, dict) and isinstance(item.get("sourceKey"), str)
        }
        expected_bindings = [_source_binding(source_by_key[key]) for key in expected_scope]
        if packet.get("sourceBindings") != expected_bindings:
            errors.append(issue("packet.source_bindings", "$.sourceBindings", "Packet source bindings do not match RequestPacket."))

        context_binding = packet.get("context")
        if role in DISCOVERY_ROLES and context_binding is not None:
            errors.append(issue("packet.discovery_context", "$.context", "Discovery roles must not receive normalized context."))
        if role in CONTEXT_ROLES:
            if not isinstance(context_binding, dict):
                errors.append(issue("packet.context_required", "$.context", "Focused and review roles require a validated context projection."))
            else:
                context_path = resolve_request_ref(request_root, context_binding.get("ref", ""))
                if base_context_path is None:
                    raise AgentInputError(
                        "external authoritative base-context path is required for projected role validation"
                    )
                base_path = base_context_path.resolve()
                base_path.relative_to(request_root.resolve())
                projected = load_json(context_path)
                base = load_json(base_path)
                rebuilt, mode, _ = build_context_projection(
                    base,
                    request,
                    str(role),
                    base_context_ref=request_relative(request_root, base_path),
                    base_file_sha256=sha256_file(base_path),
                )
                expected_context_binding = {
                    "ref": context_binding["ref"],
                    "fileSha256": sha256_file(context_path),
                    "canonicalSha256": canonical_sha256(projected),
                    "baseContextCanonicalSha256": canonical_sha256(base),
                    "profile": ROLE_PROFILE[str(role)],
                    "mode": mode,
                }
                if projected != rebuilt:
                    errors.append(issue("packet.context_projection", "$.context.ref", "Role projection differs from deterministic projection or fallback."))
                if context_binding != expected_context_binding:
                    errors.append(issue("packet.context_binding", "$.context", "Role context hashes, profile, or mode are stale."))

        if packet.get("outputRef") != f"findings/{role}.json":
            errors.append(issue("packet.output_ref", "$.outputRef", "Output must be the role's findings path."))
        resolve_request_ref(request_root, packet.get("outputRef", ""))
        expected_output_contract = {
            "schemaId": "https://nextgame.local/schemas/ui-agent-findings-0.1.json",
            "schemaSha256": sha256_file(FINDINGS_SCHEMA),
        }
        if packet.get("outputContract") != expected_output_contract:
            errors.append(issue("packet.output_contract", "$.outputContract", "AgentFindings Schema binding is stale."))

        for field in ("additionalInputs", "attachedInputs"):
            seen_kinds: set[str] = set()
            for index, binding in enumerate(packet.get(field, [])):
                kind = binding.get("kind")
                if kind in seen_kinds:
                    errors.append(
                        issue(
                            "packet.input_kind_duplicate",
                            f"$.{field}[{index}].kind",
                            "Bound input kinds must be unique within one role packet.",
                        )
                    )
                if isinstance(kind, str):
                    seen_kinds.add(kind)
                path = resolve_request_ref(request_root, binding.get("ref", ""))
                if not path.is_file() or binding.get("sha256") != sha256_file(path):
                    errors.append(issue("packet.input_binding", f"$.{field}[{index}]", "Bound input is missing or its hash changed."))
                elif field == "attachedInputs" and binding.get("kind") == "registry-shortlist":
                    shortlist_problem = _validate_registry_shortlist_attachment(load_json(path), request)
                    if shortlist_problem is not None:
                        errors.append(
                            issue(
                                "packet.registry_shortlist",
                                f"$.{field}[{index}]",
                                shortlist_problem,
                            )
                        )
                if path.is_file() and path.suffix.lower() == ".json":
                    try:
                        attached_json = load_json(path)
                        if field == "additionalInputs" and kind == "review-view":
                            expected_review_profile = ROLE_TO_PROFILE.get(str(role))
                            if not isinstance(attached_json, dict) or attached_json.get("agentRole") != role:
                                errors.append(
                                    issue(
                                        "packet.review_view_role_binding",
                                        f"$.{field}[{index}]::$.agentRole",
                                        "Review View agentRole must exactly match the packet agentRole.",
                                    )
                                )
                            if (
                                not isinstance(attached_json, dict)
                                or attached_json.get("profile") != expected_review_profile
                            ):
                                errors.append(
                                    issue(
                                        "packet.review_view_profile_binding",
                                        f"$.{field}[{index}]::$.profile",
                                        "Review View profile must exactly match the canonical profile for the packet agentRole.",
                                    )
                                )
                            if review_draft is None or review_draft_file_sha256 is None:
                                errors.append(
                                    issue(
                                        "packet.review_view_authority",
                                        f"$.{field}[{index}]",
                                        "Review View validation requires the external immutable full Draft.",
                                    )
                                )
                            else:
                                review_validation = validate_review_view(
                                    attached_json,
                                    source_draft=review_draft,
                                    request=request,
                                    source_draft_file_sha256=review_draft_file_sha256,
                                    schema=load_json(REVIEW_VIEW_SCHEMA),
                                )
                                if not review_validation["valid"]:
                                    nested_codes = sorted(
                                        {item["code"] for item in review_validation["errors"]}
                                    )
                                    errors.append(
                                        issue(
                                            "packet.review_view_invalid",
                                            f"$.{field}[{index}]",
                                            f"Review View failed deterministic validation: {nested_codes}.",
                                        )
                                    )
                        if field == "additionalInputs" and kind == "coverage-evidence":
                            coverage_problem = _validate_coverage_evidence_attachment(
                                attached_json,
                                request,
                            )
                            if coverage_problem is not None:
                                errors.append(
                                    issue(
                                        "packet.coverage_evidence_invalid",
                                        f"$.{field}[{index}]",
                                        coverage_problem,
                                    )
                                )
                        forbidden_in_input = [
                            (key, key_path)
                            for key, key_path in _walk_keys(attached_json)
                            if key in FORBIDDEN_PACKET_KEYS
                        ]
                        for forbidden_key, forbidden_path in forbidden_in_input:
                            errors.append(
                                issue(
                                    "packet.bound_history_forbidden",
                                    f"$.{field}[{index}]::{forbidden_path}",
                                    f"Bound input contains forbidden history/model field: {forbidden_key}",
                                )
                            )
                    except (OSError, json.JSONDecodeError):
                        errors.append(
                            issue(
                                "packet.bound_json_invalid",
                                f"$.{field}[{index}]",
                                "A bound .json input must contain valid JSON.",
                            )
                        )
        additional_kinds = [
            item.get("kind")
            for item in packet.get("additionalInputs", [])
            if isinstance(item, dict)
        ]
        allowed_additional = (
            {"review-view", "coverage-evidence"}
            if role == "coverage-review"
            else ({"review-view"} if role in REVIEW_ROLES else set())
        )
        if any(kind not in allowed_additional for kind in additional_kinds):
            errors.append(
                issue(
                    "packet.additional_input_scope",
                    "$.additionalInputs",
                    "additionalInputs contains a kind not allowed for this role.",
                )
            )
        attached_kinds = [
            item.get("kind")
            for item in packet.get("attachedInputs", [])
            if isinstance(item, dict)
        ]
        allowed_attached = ROLE_ATTACHMENT_KINDS.get(str(role), set())
        if any(kind not in allowed_attached for kind in attached_kinds):
            errors.append(
                issue(
                    "packet.attached_input_scope",
                    "$.attachedInputs",
                    "attachedInputs contains a kind not allowed for this role.",
                )
            )
        if role in REVIEW_ROLES:
            kinds = {item.get("kind") for item in packet.get("additionalInputs", []) if isinstance(item, dict)}
            if "review-view" not in kinds:
                errors.append(issue("packet.review_view", "$.additionalInputs", "Review roles require one role-specific review-view binding."))
    except (AgentInputError, OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        errors.append(issue("packet.validation", "$", str(error)))

    return result(errors, warnings)


def prepare_packets(
    *,
    request_path: Path,
    output_dir: Path,
    roles: Sequence[str],
    context_path: Path | None,
    draft_requirement: Path | None,
    coverage_evidence: Path | None,
    attachments: Sequence[tuple[str, Path]],
    role_cards_path: Path,
    packet_schema_path: Path,
    role_attachments: Mapping[str, Sequence[tuple[str, Path]]] | None = None,
) -> dict[str, Any]:
    request_path = request_path.resolve()
    request_root = request_path.parent
    output_dir = output_dir.resolve()
    request_relative(request_root, output_dir)
    request = load_json(request_path)
    request_validation = validate_request_packet(
        request,
        load_json(REQUEST_SCHEMA),
        packet_path=request_path,
    )
    if not request_validation["valid"]:
        codes = sorted({entry["code"] for entry in request_validation["errors"]})
        raise AgentInputError(f"RequestPacket validation failed: {codes}")

    _, cards_by_role = load_role_cards(role_cards_path)
    packet_schema = load_json(packet_schema_path)
    if not roles:
        roles = DISCOVERY_ROLES + FOCUSED_ROLES
    if len(set(roles)) != len(roles) or any(role not in ALL_ROLES for role in roles):
        raise AgentInputError("roles must be unique known analysis roles")
    if any(role in CONTEXT_ROLES for role in roles) and context_path is None:
        raise AgentInputError("context-aware roles require --context")
    if any(role in REVIEW_ROLES for role in roles) and draft_requirement is None:
        raise AgentInputError("review roles require --draft-requirement")
    if attachments:
        raise AgentInputError(
            "shared --attach inputs are disabled because they bypass role isolation; use --role-attach with an allowed role/kind"
        )
    role_attachments = role_attachments or {}
    unknown_attachment_roles = sorted(set(role_attachments) - set(ALL_ROLES))
    if unknown_attachment_roles:
        raise AgentInputError(
            "role attachments name unknown roles: " + ", ".join(unknown_attachment_roles)
        )
    for attachment_role, bindings in role_attachments.items():
        allowed_kinds = ROLE_ATTACHMENT_KINDS.get(attachment_role, set())
        for kind, _ in bindings:
            if kind not in allowed_kinds:
                raise AgentInputError(
                    f"attachment kind {kind!r} is not allowed for role {attachment_role}"
                )

    base_context: dict[str, Any] | None = None
    base_context_ref: str | None = None
    base_context_file_sha: str | None = None
    if context_path is not None:
        context_path = context_path.resolve()
        request_relative(request_root, context_path)
        loaded_context = load_json(context_path)
        if not isinstance(loaded_context, dict):
            raise AgentInputError("normalized context must be an object")
        base_context = loaded_context
        base_context_ref = request_relative(request_root, context_path)
        base_context_file_sha = sha256_file(context_path)

    review_draft: dict[str, Any] | None = None
    review_draft_file_sha256: str | None = None
    review_draft_path: Path | None = None
    if any(role in REVIEW_ROLES for role in roles):
        assert draft_requirement is not None
        review_draft_path = draft_requirement.resolve()
        request_relative(request_root, review_draft_path)
        loaded_draft = load_json(review_draft_path)
        draft_problem = _validate_draft_requirement_attachment(loaded_draft, request)
        if draft_problem is not None:
            raise AgentInputError(draft_problem)
        if not isinstance(loaded_draft, dict):
            raise AgentInputError("Draft Requirement must be an object")
        review_draft = loaded_draft
        review_draft_file_sha256 = sha256_file(review_draft_path)
    shared_additional: list[dict[str, str]] = []
    if coverage_evidence is not None:
        coverage_evidence = coverage_evidence.resolve()
        shared_additional.append(_binding(request_root, coverage_evidence, "coverage-evidence"))
    shared_attachments: list[dict[str, str]] = []
    bound_role_attachments = {
        role: [_binding(request_root, path.resolve(), kind) for kind, path in bindings]
        for role, bindings in role_attachments.items()
    }

    packet_records: list[dict[str, Any]] = []
    context_total = 0
    packet_total = 0
    projection_dir = output_dir.parent / "contexts" / "roles"
    review_view_dir = output_dir.parent / "review-views"
    review_view_total = 0
    review_view_records: list[dict[str, Any]] = []
    for role in roles:
        context_binding: dict[str, Any] | None = None
        context_record: dict[str, Any] | None = None
        if role in CONTEXT_ROLES:
            assert base_context is not None and base_context_ref is not None and base_context_file_sha is not None
            projected, mode, fallback_reason = build_context_projection(
                base_context,
                request,
                role,
                base_context_ref=base_context_ref,
                base_file_sha256=base_context_file_sha,
            )
            projected_path = projection_dir / f"{role}.json"
            atomic_write_json(projected_path, projected)
            context_binding = {
                "ref": request_relative(request_root, projected_path),
                "fileSha256": sha256_file(projected_path),
                "canonicalSha256": canonical_sha256(projected),
                "baseContextCanonicalSha256": canonical_sha256(base_context),
                "profile": ROLE_PROFILE[role],
                "mode": mode,
            }
            context_bytes = projected_path.stat().st_size
            context_total += context_bytes
            context_record = {
                "ref": context_binding["ref"],
                "bytes": context_bytes,
                "mode": mode,
                "fallbackReason": fallback_reason,
            }

        role_additional: list[dict[str, str]] = []
        review_view_record: dict[str, Any] | None = None
        if role in REVIEW_ROLES:
            assert review_draft is not None and review_draft_file_sha256 is not None
            review_view, review_mode, review_fallback_reason = build_review_view(
                review_draft,
                request,
                role,
                draft_file_sha256=review_draft_file_sha256,
                requirement_schema_path=REQUIREMENT_SCHEMA,
                view_schema_path=REVIEW_VIEW_SCHEMA,
            )
            review_view_path = review_view_dir / f"{role}.review-view.json"
            atomic_write_json(review_view_path, review_view)
            role_additional.append(_binding(request_root, review_view_path, "review-view"))
            review_view_bytes = review_view_path.stat().st_size
            review_view_total += review_view_bytes
            review_view_record = {
                "ref": request_relative(request_root, review_view_path),
                "bytes": review_view_bytes,
                "mode": review_mode,
                "fallbackReason": review_fallback_reason,
            }
            review_view_records.append({"agentRole": role, **review_view_record})
        if role == "coverage-review":
            role_additional.extend(
                item for item in shared_additional if item["kind"] == "coverage-evidence"
            )
        attached_inputs = [*shared_attachments, *bound_role_attachments.get(role, [])]
        attachment_kinds = [item["kind"] for item in attached_inputs]
        if len(attachment_kinds) != len(set(attachment_kinds)):
            raise AgentInputError(f"attached input kinds must be unique for role {role}")
        packet = build_role_packet(
            request_root=request_root,
            request_path=request_path,
            request=request,
            role=role,
            role_card=cards_by_role[role],
            role_cards_path=role_cards_path,
            context_binding=context_binding,
            additional_inputs=role_additional,
            attached_inputs=attached_inputs,
        )
        packet_path = output_dir / f"{role}.json"
        atomic_write_json(packet_path, packet)
        validation = validate_role_packet(
            packet,
            packet_path=packet_path,
            request_root=request_root,
            request_path=request_path,
            base_context_path=context_path,
            draft_requirement_path=review_draft_path,
            packet_schema=packet_schema,
            role_cards_path=role_cards_path,
        )
        if not validation["valid"]:
            codes = sorted({entry["code"] for entry in validation["errors"]})
            raise AgentInputError(f"generated role packet failed validation for {role}: {codes}")
        packet_bytes = packet_path.stat().st_size
        packet_total += packet_bytes
        packet_records.append(
            {
                "agentRole": role,
                "packetRef": request_relative(request_root, packet_path),
                "packetBytes": packet_bytes,
                "context": context_record,
                "reviewView": review_view_record,
            }
        )

    discovery_count = sum(role in DISCOVERY_ROLES for role in roles)
    context_count = sum(role in CONTEXT_ROLES for role in roles)
    request_bytes = request_path.stat().st_size
    base_context_bytes = context_path.stat().st_size if context_path is not None else 0
    legacy_proxy = request_bytes * discovery_count + base_context_bytes * context_count
    optimized_proxy = packet_total + context_total
    review_role_count = sum(role in REVIEW_ROLES for role in roles)
    draft_bytes = review_draft_path.stat().st_size if review_draft_path is not None else 0
    review_legacy_proxy = draft_bytes * review_role_count
    summary = {
        "version": "0.1",
        "requestId": request["requestId"],
        "status": "valid",
        "historyTelemetry": {
            "actualHistoryBytes": None,
            "actualModelTokens": None,
            "status": "N/A-platform-counters-unavailable",
            "structuralEvidence": "Every packet requires historyPolicy.mode=none and forkTurns=none.",
        },
        "authority": {
            "requestPacketRef": request_relative(request_root, request_path),
            "requestPacketSha256": sha256_file(request_path),
            "inputDigest": request["inputDigest"],
            "baseContextRef": base_context_ref,
            "baseContextFileSha256": base_context_file_sha,
        },
        "roles": packet_records,
        "byteProxyComparison": {
            "boundary": "Legacy full RequestPacket per discovery role plus full normalized context per context-aware role versus generated packet plus role context; review draft and optional attachments are excluded from both sides.",
            "legacyBytes": legacy_proxy,
            "optimizedBytes": optimized_proxy,
            "reductionBytes": max(0, legacy_proxy - optimized_proxy),
            "reductionRatio": (
                (legacy_proxy - optimized_proxy) / legacy_proxy if legacy_proxy else None
            ),
            "requestPacketBytes": request_bytes,
            "baseContextBytes": base_context_bytes,
            "rolePacketBytes": packet_total,
            "roleContextBytes": context_total,
        },
        "reviewViewByteProxyComparison": {
            "boundary": "Full Draft bytes repeated once per review role versus the three deterministic role-specific Review View files; role packets, role contexts, and optional evidence are excluded.",
            "legacyBytes": review_legacy_proxy,
            "optimizedBytes": review_view_total,
            "reductionBytes": review_legacy_proxy - review_view_total,
            "reductionRatio": (
                (review_legacy_proxy - review_view_total) / review_legacy_proxy
                if review_legacy_proxy
                else None
            ),
            "draftBytes": draft_bytes,
            "reviewRoleCount": review_role_count,
            "reviewViewBytes": review_view_total,
            "views": review_view_records,
            "actualModelTokens": None,
            "tokenStatus": "N/A-platform-counters-unavailable",
        },
    }
    summary_path = output_dir / "agent-input-summary.json"
    atomic_write_json(summary_path, summary)
    return {**summary, "summaryRef": request_relative(request_root, summary_path)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="generate deterministic no-history role packets")
    prepare.add_argument("request_packet", type=Path)
    prepare.add_argument("--output-dir", type=Path, required=True)
    prepare.add_argument("--context", type=Path)
    prepare.add_argument("--role", action="append", choices=ALL_ROLES, default=[])
    prepare.add_argument("--include-review", action="store_true")
    prepare.add_argument("--draft-requirement", type=Path)
    prepare.add_argument("--coverage-evidence", type=Path)
    prepare.add_argument(
        "--attach",
        action="append",
        type=_parse_named_path,
        default=[],
        metavar="KIND=PATH",
        help="deprecated and always rejected; use a role-scoped allowed binding",
    )
    prepare.add_argument(
        "--role-attach",
        action="append",
        default=[],
        metavar="ROLE:KIND=PATH",
        help="bind a validated Registry shortlist only to an allowlisted role",
    )

    validate = subparsers.add_parser("validate", help="validate one generated role packet")
    validate.add_argument("packet", type=Path)
    validate.add_argument("--request-root", type=Path, required=True)
    validate.add_argument("--request-packet", type=Path, required=True)
    validate.add_argument("--base-context", type=Path)
    validate.add_argument("--draft-requirement", type=Path, help="Immutable full pending Requirement used only to revalidate review-view inputs.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "prepare":
            roles = list(args.role)
            if not roles:
                roles = list(DISCOVERY_ROLES + FOCUSED_ROLES)
            if args.include_review:
                for role in REVIEW_ROLES:
                    if role not in roles:
                        roles.append(role)
            role_attachments: dict[str, list[tuple[str, Path]]] = {}
            for raw in args.role_attach:
                if ":" not in raw:
                    raise AgentInputError("--role-attach must use ROLE:KIND=PATH")
                role, named_path = raw.split(":", 1)
                if role not in ALL_ROLES:
                    raise AgentInputError(f"--role-attach names unknown role {role!r}")
                role_attachments.setdefault(role, []).append(_parse_named_path(named_path))
            output = prepare_packets(
                request_path=args.request_packet,
                output_dir=args.output_dir,
                roles=roles,
                context_path=args.context,
                draft_requirement=args.draft_requirement,
                coverage_evidence=args.coverage_evidence,
                attachments=args.attach,
                role_cards_path=DEFAULT_ROLE_CARDS,
                packet_schema_path=DEFAULT_PACKET_SCHEMA,
                role_attachments=role_attachments,
            )
            print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))
            return 0
        packet_path = args.packet.resolve()
        output = validate_role_packet(
            load_json(packet_path),
            packet_path=packet_path,
            request_root=args.request_root.resolve(),
            request_path=args.request_packet.resolve(),
            base_context_path=args.base_context.resolve() if args.base_context else None,
            draft_requirement_path=args.draft_requirement.resolve() if args.draft_requirement else None,
            packet_schema=load_json(DEFAULT_PACKET_SCHEMA),
            role_cards_path=DEFAULT_ROLE_CARDS,
        )
        print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))
        return 0 if output["valid"] else 1
    except (AgentInputError, OSError, json.JSONDecodeError, TypeError, ValueError) as error:
        print(json.dumps({"valid": False, "errors": [issue("agent-input", "$", str(error))], "warnings": []}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
