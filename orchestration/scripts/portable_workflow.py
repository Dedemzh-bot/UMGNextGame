#!/usr/bin/env python3
"""Validate and materialize the NextGame UI portable workflow contract.

This module deliberately contains no agent-vendor SDK calls.  Adapters consume the
generated dispatch manifest and must treat declared files—not chat messages—as the
authoritative coordination surface.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence


CONTRACT_VERSION = "1.0.0"

DISCOVERY_ROLES = (
    "visual-structure",
    "text-requirements",
    "project-pattern",
)
FOCUSED_ROLES = (
    "state-modeling",
    "data-adaptation",
    "asset-decomposition",
)
REVIEW_ROLES = (
    "state-visual-review",
    "schema-feasibility-review",
    "coverage-review",
)
REQUIRED_FINDINGS_ROLES = DISCOVERY_ROLES + FOCUSED_ROLES + REVIEW_ROLES

EXPECTED_ANALYSIS_STEPS = {
    "validate-packet": (),
    "discover-visual-structure": ("validate-packet",),
    "discover-text-requirements": ("validate-packet",),
    "discover-project-pattern": ("validate-packet",),
    "validate-discovery": (
        "discover-visual-structure",
        "discover-text-requirements",
        "discover-project-pattern",
    ),
    "normalize-identities": ("validate-discovery",),
    "analyze-state-modeling": ("normalize-identities",),
    "analyze-data-adaptation": ("normalize-identities",),
    "analyze-asset-decomposition": ("normalize-identities",),
    "validate-focused": (
        "analyze-state-modeling",
        "analyze-data-adaptation",
        "analyze-asset-decomposition",
    ),
    "synthesize-draft": ("validate-focused",),
    "review-state-visual": ("synthesize-draft",),
    "review-schema-feasibility": ("synthesize-draft",),
    "review-coverage": ("synthesize-draft",),
    "finalize-review-resolutions": (
        "review-state-visual",
        "review-schema-feasibility",
        "review-coverage",
    ),
    "strict-validate-requirement": ("finalize-review-resolutions",),
    "requirements-confirmation": ("strict-validate-requirement",),
}

EXPECTED_CONTINUATION_STEPS = {
    "build-verified-umg": ("requirements-confirmation",),
    "post-save-unreal-readback": ("build-verified-umg",),
    "present-build-results": ("post-save-unreal-readback",),
    "build-results-confirmation": ("present-build-results",),
    "document-program-handoff": ("build-results-confirmation",),
}

EXPECTED_PARALLEL_GROUPS = {
    "discovery": {
        "discover-visual-structure",
        "discover-text-requirements",
        "discover-project-pattern",
    },
    "focused-analysis": {
        "analyze-state-modeling",
        "analyze-data-adaptation",
        "analyze-asset-decomposition",
    },
    "independent-review": {
        "review-state-visual",
        "review-schema-feasibility",
        "review-coverage",
    },
}

ROLE_TO_STEP = {
    "visual-structure": "discover-visual-structure",
    "text-requirements": "discover-text-requirements",
    "project-pattern": "discover-project-pattern",
    "state-modeling": "analyze-state-modeling",
    "data-adaptation": "analyze-data-adaptation",
    "asset-decomposition": "analyze-asset-decomposition",
    "state-visual-review": "review-state-visual",
    "schema-feasibility-review": "review-schema-feasibility",
    "coverage-review": "review-coverage",
}

EXPECTED_STEP_KINDS = {
    "validate-packet": "validation",
    "discover-visual-structure": "findings",
    "discover-text-requirements": "findings",
    "discover-project-pattern": "findings",
    "validate-discovery": "validation",
    "normalize-identities": "normalization",
    "analyze-state-modeling": "findings",
    "analyze-data-adaptation": "findings",
    "analyze-asset-decomposition": "findings",
    "validate-focused": "validation",
    "synthesize-draft": "synthesis",
    "review-state-visual": "findings",
    "review-schema-feasibility": "findings",
    "review-coverage": "findings",
    "finalize-review-resolutions": "synthesis",
    "strict-validate-requirement": "validation",
    "requirements-confirmation": "gate",
    "build-verified-umg": "build",
    "post-save-unreal-readback": "readback",
    "present-build-results": "presentation",
    "build-results-confirmation": "gate",
    "document-program-handoff": "document",
}

EXPECTED_GATE_ARTIFACTS = {
    "requirements-confirmation": "ui-requirement.json",
    "build-results-confirmation": "ui-build-acceptance.json",
}


class ContractError(ValueError):
    """Raised when a workflow or materialized plan violates the contract."""


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as error:
        raise ContractError(f"JSON file does not exist: {path}") from error
    except json.JSONDecodeError as error:
        raise ContractError(
            f"Invalid JSON in {path}: line {error.lineno}, column {error.colno}: {error.msg}"
        ) from error


def _mapping(value: Any, field: str, errors: list[str]) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        errors.append(f"{field} must be an object")
        return {}
    return value


def _list(value: Any, field: str, errors: list[str]) -> list[Any]:
    if not isinstance(value, list):
        errors.append(f"{field} must be an array")
        return []
    return value


def _strings(value: Any, field: str, errors: list[str]) -> list[str]:
    items = _list(value, field, errors)
    if any(not isinstance(item, str) for item in items):
        errors.append(f"{field} must contain only strings")
        return [item for item in items if isinstance(item, str)]
    return items


def _duplicates(items: Iterable[str]) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for item in items:
        if item in seen:
            duplicates.add(item)
        seen.add(item)
    return duplicates


def _is_safe_relative_posix(path_text: str) -> bool:
    if not path_text or "\\" in path_text:
        return False
    if path_text.startswith("/") or re.match(r"^[A-Za-z]:", path_text):
        return False
    path = PurePosixPath(path_text)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return False
    return path.as_posix() == path_text and not path_text.endswith("/")


def _check_path(
    path_text: Any,
    field: str,
    errors: list[str],
    *,
    allowed_prefixes: set[str] | None = None,
    allowed_root_outputs: set[str] | None = None,
) -> None:
    if not isinstance(path_text, str) or not _is_safe_relative_posix(path_text):
        errors.append(
            f"{field} must be a canonical artifact-root-relative POSIX path without '.', '..', a drive, or backslashes"
        )
        return
    if allowed_prefixes is not None:
        parts = PurePosixPath(path_text).parts
        root_file_allowed = len(parts) == 1 and path_text in (allowed_root_outputs or set())
        prefix_allowed = len(parts) > 1 and parts[0] in allowed_prefixes
        if not root_file_allowed and not prefix_allowed:
            errors.append(
                f"{field} escapes allowed output locations; prefixes={sorted(allowed_prefixes)}, "
                f"rootFiles={sorted(allowed_root_outputs or set())}: {path_text}"
            )


def _check_exact_dependencies(
    step_map: Mapping[str, Mapping[str, Any]],
    expected: Mapping[str, Sequence[str]],
    errors: list[str],
) -> None:
    for step_id, expected_dependencies in expected.items():
        step = step_map.get(step_id)
        if step is None:
            continue
        actual = step.get("dependsOn")
        if not isinstance(actual, list):
            continue
        if set(actual) != set(expected_dependencies) or len(actual) != len(expected_dependencies):
            errors.append(
                f"step {step_id!r} must depend exactly on {list(expected_dependencies)!r}; got {actual!r}"
            )


def _check_cycles(step_map: Mapping[str, Mapping[str, Any]], errors: list[str]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(step_id: str, trail: list[str]) -> None:
        if step_id in visiting:
            start = trail.index(step_id) if step_id in trail else 0
            errors.append(f"dependency cycle detected: {' -> '.join(trail[start:] + [step_id])}")
            return
        if step_id in visited:
            return
        visiting.add(step_id)
        trail.append(step_id)
        dependencies = step_map[step_id].get("dependsOn", [])
        if isinstance(dependencies, list):
            for dependency in dependencies:
                if isinstance(dependency, str) and dependency in step_map:
                    visit(dependency, trail)
        trail.pop()
        visiting.remove(step_id)
        visited.add(step_id)

    for candidate in step_map:
        visit(candidate, [])


def _check_steps(
    raw_steps: Any,
    field: str,
    errors: list[str],
    allowed_prefixes: set[str],
    allowed_root_outputs: set[str],
) -> tuple[list[Mapping[str, Any]], dict[str, Mapping[str, Any]]]:
    steps: list[Mapping[str, Any]] = []
    for index, value in enumerate(_list(raw_steps, field, errors)):
        step = _mapping(value, f"{field}[{index}]", errors)
        if step:
            steps.append(step)

    ids = [step.get("id") for step in steps if isinstance(step.get("id"), str)]
    for duplicate in sorted(_duplicates(ids)):
        errors.append(f"duplicate step id: {duplicate}")
    step_map = {step_id: step for step_id, step in zip(ids, [s for s in steps if isinstance(s.get('id'), str)])}

    for index, step in enumerate(steps):
        prefix = f"{field}[{index}]"
        step_id = step.get("id")
        if not isinstance(step_id, str) or not re.fullmatch(r"[a-z][a-z0-9-]+", step_id):
            errors.append(f"{prefix}.id must be a kebab-case identifier")
        for key in ("kind", "execution", "instruction"):
            if not isinstance(step.get(key), str) or not step.get(key):
                errors.append(f"{prefix}.{key} must be a nonempty string")
        dependencies = _strings(step.get("dependsOn"), f"{prefix}.dependsOn", errors)
        for duplicate in sorted(_duplicates(dependencies)):
            errors.append(f"{prefix}.dependsOn repeats {duplicate!r}")
        inputs = _strings(step.get("inputs"), f"{prefix}.inputs", errors)
        outputs = _strings(step.get("outputs"), f"{prefix}.outputs", errors)
        for item_index, path_text in enumerate(inputs):
            _check_path(path_text, f"{prefix}.inputs[{item_index}]", errors)
        for item_index, path_text in enumerate(outputs):
            _check_path(
                path_text,
                f"{prefix}.outputs[{item_index}]",
                errors,
                allowed_prefixes=allowed_prefixes,
                allowed_root_outputs=allowed_root_outputs,
            )

        role = step.get("agentRole")
        if step.get("kind") == "findings":
            if role not in REQUIRED_FINDINGS_ROLES:
                errors.append(f"{prefix}.agentRole must be one of the nine findings roles")
            if step.get("execution") != "worker":
                errors.append(f"{prefix} findings steps must use worker execution")
        elif role is not None:
            errors.append(f"{prefix}.agentRole is permitted only on findings steps")

        gate = step.get("gate")
        if step.get("kind") == "gate":
            gate_map = _mapping(gate, f"{prefix}.gate", errors)
            if gate_map.get("actor") != "user":
                errors.append(f"{prefix}.gate.actor must be 'user'")
            if gate_map.get("manualAdvance") is not True:
                errors.append(f"{prefix}.gate.manualAdvance must be true")
            _check_path(
                gate_map.get("decisionArtifact"),
                f"{prefix}.gate.decisionArtifact",
                errors,
                allowed_prefixes=allowed_prefixes,
                allowed_root_outputs=allowed_root_outputs,
            )
            if not isinstance(gate_map.get("acceptanceCondition"), str) or not gate_map.get("acceptanceCondition"):
                errors.append(f"{prefix}.gate.acceptanceCondition must be a nonempty string")
            if step.get("execution") != "human":
                errors.append(f"{prefix} gate steps must use human execution")
        elif gate is not None:
            errors.append(f"{prefix}.gate is permitted only on gate steps")

    return steps, step_map


def validate_workflow(workflow: Any) -> None:
    """Raise ContractError unless *workflow* is the closed 1.0.0 contract."""

    errors: list[str] = []
    root = _mapping(workflow, "$", errors)
    if not root:
        raise ContractError("; ".join(errors))

    if root.get("schemaRef") != "workflow.schema.json":
        errors.append("schemaRef must be 'workflow.schema.json'")
    if root.get("contractVersion") != CONTRACT_VERSION:
        errors.append(f"contractVersion must be {CONTRACT_VERSION!r}")
    if not isinstance(root.get("workflowId"), str) or not re.fullmatch(r"[a-z][a-z0-9-]+", root.get("workflowId", "")):
        errors.append("workflowId must be a kebab-case identifier")
    if not isinstance(root.get("workflowVersion"), str) or not re.fullmatch(r"\d+\.\d+\.\d+", root.get("workflowVersion", "")):
        errors.append("workflowVersion must be semantic version text")
    _check_path(root.get("requestPacketRef"), "requestPacketRef", errors)

    exchange = _mapping(root.get("artifactExchange"), "artifactExchange", errors)
    if exchange.get("authority") != "filesystem-artifacts":
        errors.append("artifactExchange.authority must be 'filesystem-artifacts'")
    if exchange.get("messagesAreAuthoritative") is not False:
        errors.append("artifactExchange.messagesAreAuthoritative must be false")
    if exchange.get("pathStyle") != "artifact-root-relative-posix":
        errors.append("artifactExchange.pathStyle must be 'artifact-root-relative-posix'")
    if not isinstance(exchange.get("completionRule"), str) or not exchange.get("completionRule"):
        errors.append("artifactExchange.completionRule must be a nonempty string")
    if exchange.get("integrityAlgorithm") != "sha256":
        errors.append("artifactExchange.integrityAlgorithm must be 'sha256'")
    if exchange.get("failClosedOnMissingOrDigestMismatch") is not True:
        errors.append(
            "artifactExchange.failClosedOnMissingOrDigestMismatch must be true"
        )
    if not isinstance(exchange.get("provenanceRule"), str) or not exchange.get("provenanceRule"):
        errors.append("artifactExchange.provenanceRule must be a nonempty string")
    prefixes = _strings(
        exchange.get("allowedOutputPrefixes"),
        "artifactExchange.allowedOutputPrefixes",
        errors,
    )
    allowed_prefixes = set(prefixes)
    if not allowed_prefixes:
        errors.append("artifactExchange.allowedOutputPrefixes must not be empty")
    for prefix in prefixes:
        if not re.fullmatch(r"[a-z][a-z0-9-]*", prefix):
            errors.append(f"invalid allowed output prefix: {prefix!r}")
    for duplicate in sorted(_duplicates(prefixes)):
        errors.append(f"duplicate allowed output prefix: {duplicate}")
    root_outputs = _strings(
        exchange.get("allowedRootOutputs"),
        "artifactExchange.allowedRootOutputs",
        errors,
    )
    allowed_root_outputs = set(root_outputs)
    if not allowed_root_outputs:
        errors.append("artifactExchange.allowedRootOutputs must not be empty")
    for root_output in root_outputs:
        if not _is_safe_relative_posix(root_output) or len(PurePosixPath(root_output).parts) != 1:
            errors.append(f"invalid allowed root output: {root_output!r}")
    for duplicate in sorted(_duplicates(root_outputs)):
        errors.append(f"duplicate allowed root output: {duplicate}")

    required_roles = _strings(root.get("requiredFindingsRoles"), "requiredFindingsRoles", errors)
    if set(required_roles) != set(REQUIRED_FINDINGS_ROLES) or len(required_roles) != len(REQUIRED_FINDINGS_ROLES):
        errors.append(
            "requiredFindingsRoles must contain each of the nine canonical roles exactly once"
        )

    analysis_steps, analysis_map = _check_steps(
        root.get("steps"),
        "steps",
        errors,
        allowed_prefixes,
        allowed_root_outputs,
    )
    continuation = _mapping(root.get("protectedContinuation"), "protectedContinuation", errors)
    continuation_steps, continuation_map = _check_steps(
        continuation.get("steps"),
        "protectedContinuation.steps",
        errors,
        allowed_prefixes,
        allowed_root_outputs,
    )

    if set(analysis_map) != set(EXPECTED_ANALYSIS_STEPS):
        missing = sorted(set(EXPECTED_ANALYSIS_STEPS) - set(analysis_map))
        extra = sorted(set(analysis_map) - set(EXPECTED_ANALYSIS_STEPS))
        errors.append(f"analysis step set is closed; missing={missing}, extra={extra}")
    if set(continuation_map) != set(EXPECTED_CONTINUATION_STEPS):
        missing = sorted(set(EXPECTED_CONTINUATION_STEPS) - set(continuation_map))
        extra = sorted(set(continuation_map) - set(EXPECTED_CONTINUATION_STEPS))
        errors.append(f"protected continuation step set is closed; missing={missing}, extra={extra}")

    all_steps = analysis_steps + continuation_steps
    all_map = dict(analysis_map)
    for step_id, step in continuation_map.items():
        if step_id in all_map:
            errors.append(f"step id is duplicated across analysis and continuation: {step_id}")
        all_map[step_id] = step

    for step_id, step in all_map.items():
        for dependency in step.get("dependsOn", []):
            if isinstance(dependency, str) and dependency not in all_map:
                errors.append(f"step {step_id!r} depends on unknown step {dependency!r}")

    _check_exact_dependencies(analysis_map, EXPECTED_ANALYSIS_STEPS, errors)
    _check_exact_dependencies(continuation_map, EXPECTED_CONTINUATION_STEPS, errors)
    _check_cycles(all_map, errors)

    for step_id, expected_kind in EXPECTED_STEP_KINDS.items():
        step = all_map.get(step_id)
        if step and step.get("kind") != expected_kind:
            errors.append(f"step {step_id!r} must use kind {expected_kind!r}")

    role_steps: dict[str, list[str]] = {}
    for step in all_steps:
        role = step.get("agentRole")
        step_id = step.get("id")
        if isinstance(role, str) and isinstance(step_id, str):
            role_steps.setdefault(role, []).append(step_id)
    if set(role_steps) != set(REQUIRED_FINDINGS_ROLES):
        missing = sorted(set(REQUIRED_FINDINGS_ROLES) - set(role_steps))
        extra = sorted(set(role_steps) - set(REQUIRED_FINDINGS_ROLES))
        errors.append(f"findings role coverage mismatch; missing={missing}, extra={extra}")
    for role, expected_step in ROLE_TO_STEP.items():
        actual_steps = role_steps.get(role, [])
        if actual_steps != [expected_step]:
            errors.append(
                f"findings role {role!r} must be owned exactly once by {expected_step!r}; got {actual_steps!r}"
            )

    groups_raw = _list(root.get("parallelGroups"), "parallelGroups", errors)
    groups: dict[str, set[str]] = {}
    for index, raw_group in enumerate(groups_raw):
        group = _mapping(raw_group, f"parallelGroups[{index}]", errors)
        group_id = group.get("id")
        members = _strings(group.get("members"), f"parallelGroups[{index}].members", errors)
        if not isinstance(group_id, str):
            errors.append(f"parallelGroups[{index}].id must be a string")
            continue
        if group_id in groups:
            errors.append(f"duplicate parallel group id: {group_id}")
        groups[group_id] = set(members)
        for duplicate in sorted(_duplicates(members)):
            errors.append(f"parallel group {group_id!r} repeats member {duplicate!r}")
    if groups != EXPECTED_PARALLEL_GROUPS:
        errors.append(
            f"parallel groups must exactly match the three canonical role waves; got {groups!r}"
        )
    for group_id, members in groups.items():
        dependency_signatures: set[tuple[str, ...]] = set()
        for member in members:
            step = analysis_map.get(member)
            if step is None:
                errors.append(f"parallel group {group_id!r} names unknown analysis step {member!r}")
                continue
            if step.get("parallelGroup") != group_id:
                errors.append(
                    f"parallel member {member!r} must declare parallelGroup {group_id!r}"
                )
            if step.get("kind") != "findings":
                errors.append(f"parallel member {member!r} must be a findings step")
            dependencies = step.get("dependsOn", [])
            if isinstance(dependencies, list):
                dependency_signatures.add(tuple(sorted(str(item) for item in dependencies)))
                if any(dependency in members for dependency in dependencies):
                    errors.append(
                        f"parallel member {member!r} may not depend on another member of {group_id!r}"
                    )
        if len(dependency_signatures) > 1:
            errors.append(f"parallel group {group_id!r} members must share the same dependencies")

    output_owners: dict[str, str] = {}
    for step in all_steps:
        step_id = str(step.get("id", "<unknown>"))
        owned_paths = list(step.get("outputs", [])) if isinstance(step.get("outputs"), list) else []
        gate = step.get("gate")
        if isinstance(gate, dict) and isinstance(gate.get("decisionArtifact"), str):
            owned_paths.append(gate["decisionArtifact"])
        for path_text in owned_paths:
            if not isinstance(path_text, str):
                continue
            previous = output_owners.get(path_text)
            if previous is not None:
                errors.append(
                    f"output path {path_text!r} has multiple owners: {previous!r} and {step_id!r}"
                )
            else:
                output_owners[path_text] = step_id

    for step in all_steps:
        step_id = step.get("id")
        for input_path in step.get("inputs", []):
            if input_path == root.get("requestPacketRef"):
                continue
            if input_path not in output_owners:
                errors.append(
                    f"step {step_id!r} input {input_path!r} has no authoritative producer"
                )

    if root.get("requirementsTerminal") != "requirements-confirmation":
        errors.append("requirementsTerminal must be the first manual gate 'requirements-confirmation'")
    if continuation.get("dispatchByDefault") is not False:
        errors.append("protectedContinuation.dispatchByDefault must be false")
    if continuation.get("schedulerPolicy") != "manual-resume-after-verified-gate-artifact":
        errors.append(
            "protectedContinuation.schedulerPolicy must require manual resume after verified gate evidence"
        )
    for gate_id, expected_artifact in EXPECTED_GATE_ARTIFACTS.items():
        gate_step = all_map.get(gate_id, {})
        gate = gate_step.get("gate") if isinstance(gate_step, dict) else None
        if not isinstance(gate, dict) or gate.get("decisionArtifact") != expected_artifact:
            errors.append(
                f"gate {gate_id!r} must own decision artifact {expected_artifact!r}"
            )

    if errors:
        raise ContractError("\n".join(f"- {message}" for message in errors))


def _ensure_within(root: Path, candidate: Path, label: str) -> Path:
    root_resolved = root.resolve()
    candidate_resolved = candidate.resolve()
    try:
        candidate_resolved.relative_to(root_resolved)
    except ValueError as error:
        raise ContractError(
            f"{label} must resolve inside artifact root {root_resolved}: {candidate_resolved}"
        ) from error
    return candidate_resolved


def _materialize_path(
    artifact_root: Path,
    logical_path: str,
    request_ref: str,
    request_packet: Path,
) -> Path:
    if logical_path == request_ref:
        return request_packet
    return _ensure_within(
        artifact_root,
        artifact_root.joinpath(*PurePosixPath(logical_path).parts),
        f"artifact {logical_path!r}",
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def render_dispatch_manifest(
    workflow: Mapping[str, Any],
    workflow_path: Path,
    artifact_root: Path,
    request_packet: Path,
) -> dict[str, Any]:
    """Return a concrete, vendor-neutral dispatch plan without running agents."""

    validate_workflow(workflow)
    root = artifact_root.resolve()
    packet = _ensure_within(root, request_packet, "request packet")
    if not packet.is_file():
        raise ContractError(f"request packet does not exist or is not a file: {packet}")
    request_ref = str(workflow["requestPacketRef"])

    analysis_steps = workflow["steps"]
    continuation_steps = workflow["protectedContinuation"]["steps"]

    def materialize(step: Mapping[str, Any], phase: str) -> dict[str, Any]:
        item: dict[str, Any] = {
            "stepId": step["id"],
            "phase": phase,
            "kind": step["kind"],
            "execution": step["execution"],
            "dependsOn": list(step["dependsOn"]),
            "inputs": [
                {
                    "logicalPath": path_text,
                    "path": str(
                        _materialize_path(root, path_text, request_ref, packet)
                    ),
                    "integrity": {"algorithm": "sha256", "required": True},
                }
                for path_text in step["inputs"]
            ],
            "outputs": [
                {
                    "logicalPath": path_text,
                    "path": str(
                        _materialize_path(root, path_text, request_ref, packet)
                    ),
                    "integrity": {"algorithm": "sha256", "required": True},
                }
                for path_text in step["outputs"]
            ],
            "instruction": step["instruction"],
        }
        for key in ("agentRole", "parallelGroup", "contractValidator"):
            if key in step:
                item[key] = step[key]
        if "gate" in step:
            decision = step["gate"]["decisionArtifact"]
            item["gate"] = {
                **step["gate"],
                "decisionArtifact": {
                    "logicalPath": decision,
                    "path": str(
                        _materialize_path(root, decision, request_ref, packet)
                    ),
                    "integrity": {"algorithm": "sha256", "required": True},
                },
            }
        return item

    return {
        "manifestVersion": "1.0.0",
        "workflow": {
            "id": workflow["workflowId"],
            "version": workflow["workflowVersion"],
            "contractVersion": workflow["contractVersion"],
            "source": str(workflow_path.resolve()),
        },
        "artifactRoot": str(root),
        "requestPacket": {
            "path": str(packet),
            "sha256": _sha256_file(packet),
        },
        "authority": {
            "source": "filesystem-artifacts",
            "messagesAreAuthoritative": False,
            "completionRule": workflow["artifactExchange"]["completionRule"],
            "integrityAlgorithm": workflow["artifactExchange"]["integrityAlgorithm"],
            "failClosedOnMissingOrDigestMismatch": workflow["artifactExchange"][
                "failClosedOnMissingOrDigestMismatch"
            ],
            "provenanceRule": workflow["artifactExchange"]["provenanceRule"],
        },
        "requirementsTerminal": workflow["requirementsTerminal"],
        "parallelGroups": workflow["parallelGroups"],
        "steps": [materialize(step, "requirements") for step in analysis_steps]
        + [materialize(step, "protected-continuation") for step in continuation_steps],
        "protectedContinuation": {
            "dispatchByDefault": False,
            "schedulerPolicy": workflow["protectedContinuation"]["schedulerPolicy"],
            "description": workflow["protectedContinuation"]["description"],
        },
        "adapterContract": {
            "mayCallVendorApi": True,
            "thisManifestCallsVendorApi": False,
            "dispatchRule": "Dispatch a step only after all declared dependencies have authoritative validated artifacts.",
            "gateRule": "Never dispatch past a human gate until its exact decision artifact exists and validates for the current upstream identities.",
            "writeRule": "Give each worker exclusive ownership of its declared output path and reject undeclared writes.",
        },
    }


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate or materialize the vendor-neutral NextGame UI workflow."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser(
        "validate", help="Validate the closed workflow contract."
    )
    validate_parser.add_argument("--workflow", required=True, type=Path)
    validate_parser.add_argument(
        "--schema",
        type=Path,
        help="Optionally parse the companion JSON Schema and verify its contract identifier.",
    )

    plan_parser = subparsers.add_parser(
        "plan", help="Write a concrete dispatch manifest without calling an agent API."
    )
    plan_parser.add_argument("--workflow", required=True, type=Path)
    plan_parser.add_argument("--artifact-root", required=True, type=Path)
    plan_parser.add_argument("--request-packet", required=True, type=Path)
    plan_parser.add_argument("--output", required=True, type=Path)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        workflow = load_json(args.workflow)
        if args.command == "validate":
            validate_workflow(workflow)
            if args.schema is not None:
                schema = load_json(args.schema)
                if not isinstance(schema, dict) or schema.get("$id") != "urn:nextgame-ui:portable-workflow:1.0.0":
                    raise ContractError(
                        "schema $id must be 'urn:nextgame-ui:portable-workflow:1.0.0'"
                    )
            print(
                f"valid workflow: {workflow['workflowId']}@{workflow['workflowVersion']} "
                f"({len(workflow['steps'])} requirements steps, "
                f"{len(workflow['protectedContinuation']['steps'])} protected continuation steps)"
            )
            return 0

        output = _ensure_within(args.artifact_root, args.output, "manifest output")
        manifest = render_dispatch_manifest(
            workflow,
            args.workflow,
            args.artifact_root,
            args.request_packet,
        )
        _write_json(output, manifest)
        print(f"wrote dispatch manifest: {output}")
        return 0
    except ContractError as error:
        print(f"workflow contract error:\n{error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
