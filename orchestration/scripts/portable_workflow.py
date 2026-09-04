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


CONTRACT_VERSION = "2.0.0"
WORKFLOW_VERSION = "2.0.0"
MANIFEST_VERSION = "2.0.0"
SCHEMA_ID = "urn:nextgame-ui:portable-workflow:2.0.0"

EXPECTED_AGENT_DISPATCH_POLICY = {
    "promptContract": "packet-path-only",
    "historyPolicy": "none",
    "forkTurns": "none",
    "inheritsConversation": False,
    "allowModelOverride": False,
    "allowReasoningOverride": False,
}

EXPECTED_BUILD_AGENT_DISPATCH_POLICY = {
    "promptContract": "single-validated-artifact-path",
    "historyPolicy": "none",
    "forkTurns": "none",
    "inheritsConversation": False,
    "allowModelOverride": False,
    "allowReasoningOverride": False,
}

EXPECTED_PREMUTATION_EVIDENCE_CONTRACT = {
    "artifact": "status/ui-build-plan.pre-mutation-valid.json",
    "schemaRef": "build-plan-pre-mutation.schema.json",
    "toolRef": "scripts/build_plan_evidence.py",
    "generationMode": "generate-and-self-validate",
    "revalidationMode": "--validate-only",
    "requiredBeforeEditorMutation": True,
}

EXPECTED_COMPOSITE_OUTPUTS = [
    {
        "id": "planned-layout-specs",
        "ownerStep": "plan-umg-build",
        "descriptor": "ui-build-bundle.planned.json",
        "pathSelector": "assets[].layoutSpecPath",
        "digestSelector": "assets[].layoutSpecSha256",
        "identitySelector": "assets[].id",
        "coverageSelector": "assets[].id",
        "allowedPathPrefix": "layouts/",
        "consumerStep": "validate-build-plan",
        "validator": "ui-layout-spec-0.2",
        "nullPolicy": "only-reuse-only-assets",
        "requireRunRootContainment": True,
        "requireDigestMatch": True,
        "requireCompleteEnumeration": True,
    },
    {
        "id": "deterministic-build-plans",
        "ownerStep": "validate-build-plan",
        "descriptor": "status/ui-build-plan.pre-mutation-valid.json",
        "pathSelector": "plans[].path",
        "digestSelector": "plans[].sha256",
        "identitySelector": "plans[].assetId",
        "coverageSelector": "ui-build-bundle.planned.json:execution.buildOrderAssetIds",
        "allowedPathPrefix": "plans/",
        "consumerStep": "build-verified-umg",
        "validator": "deterministic-mcp-build-plan",
        "nullPolicy": "only-reuse-only-assets",
        "requireRunRootContainment": True,
        "requireDigestMatch": True,
        "requireCompleteEnumeration": True,
    }
]

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
    "prepare-accepted-build-view": ("requirements-confirmation",),
    "plan-umg-build": ("prepare-accepted-build-view",),
    "validate-build-plan": ("plan-umg-build",),
    "build-verified-umg": ("validate-build-plan",),
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
    "prepare-accepted-build-view": "projection",
    "plan-umg-build": "build",
    "validate-build-plan": "validation",
    "build-verified-umg": "build",
    "post-save-unreal-readback": "readback",
    "present-build-results": "presentation",
    "build-results-confirmation": "gate",
    "document-program-handoff": "document",
}

EXPECTED_STEP_EXECUTIONS = {
    **{
        step_id: "worker" if step_id in set(ROLE_TO_STEP.values()) else "coordinator"
        for step_id in EXPECTED_STEP_KINDS
    },
    "plan-umg-build": "worker",
    "requirements-confirmation": "human",
    "build-results-confirmation": "human",
}

EXPECTED_ALLOWED_OUTPUT_PREFIXES = (
    "inputs",
    "agent-inputs",
    "findings",
    "contexts",
    "review-views",
    "layouts",
    "plans",
    "status",
)

EXPECTED_ALLOWED_ROOT_OUTPUTS = (
    "ui-requirement.draft.json",
    "ui-requirement.pending.json",
    "ui-requirement.json",
    "accepted-build-view.json",
    "ui-build-bundle.planned.json",
    "ui-build-bundle.json",
    "unreal-widget-readback.json",
    "ui-build-acceptance.json",
    "ui-program-handoff.json",
    "program-document-content.json",
    "document-verification.json",
)

# These maps are intentionally independent of the loaded workflow document.  They
# turn the v2 workflow into a closed protocol: adding, removing, reordering, or
# redirecting any declared input/output is a contract change, not adapter freedom.
EXPECTED_STEP_INPUTS = {
    "validate-packet": ("request-packet.json",),
    "discover-visual-structure": (
        "request-packet.json",
        "agent-inputs/visual-structure.json",
    ),
    "discover-text-requirements": (
        "request-packet.json",
        "agent-inputs/text-requirements.json",
    ),
    "discover-project-pattern": (
        "request-packet.json",
        "inputs/shared-widget-shortlist.json",
        "agent-inputs/project-pattern.json",
    ),
    "validate-discovery": (
        "request-packet.json",
        "agent-inputs/visual-structure.json",
        "agent-inputs/text-requirements.json",
        "agent-inputs/project-pattern.json",
        "findings/visual-structure.json",
        "findings/text-requirements.json",
        "findings/project-pattern.json",
    ),
    "normalize-identities": (
        "request-packet.json",
        "inputs/shared-widget-shortlist.json",
        "agent-inputs/visual-structure.json",
        "agent-inputs/text-requirements.json",
        "agent-inputs/project-pattern.json",
        "findings/visual-structure.json",
        "findings/text-requirements.json",
        "findings/project-pattern.json",
    ),
    "analyze-state-modeling": (
        "request-packet.json",
        "contexts/normalized-context.json",
        "contexts/roles/state-modeling.json",
        "agent-inputs/state-modeling.json",
    ),
    "analyze-data-adaptation": (
        "request-packet.json",
        "contexts/normalized-context.json",
        "contexts/roles/data-adaptation.json",
        "agent-inputs/data-adaptation.json",
    ),
    "analyze-asset-decomposition": (
        "request-packet.json",
        "inputs/shared-widget-shortlist.json",
        "contexts/normalized-context.json",
        "contexts/roles/asset-decomposition.json",
        "agent-inputs/asset-decomposition.json",
    ),
    "validate-focused": (
        "request-packet.json",
        "contexts/normalized-context.json",
        "contexts/roles/state-modeling.json",
        "contexts/roles/data-adaptation.json",
        "contexts/roles/asset-decomposition.json",
        "agent-inputs/state-modeling.json",
        "agent-inputs/data-adaptation.json",
        "agent-inputs/asset-decomposition.json",
        "findings/state-modeling.json",
        "findings/data-adaptation.json",
        "findings/asset-decomposition.json",
    ),
    "synthesize-draft": (
        "request-packet.json",
        "inputs/shared-widget-shortlist.json",
        "contexts/normalized-context.json",
        "contexts/roles/state-modeling.json",
        "contexts/roles/data-adaptation.json",
        "contexts/roles/asset-decomposition.json",
        "agent-inputs/visual-structure.json",
        "agent-inputs/text-requirements.json",
        "agent-inputs/project-pattern.json",
        "agent-inputs/state-modeling.json",
        "agent-inputs/data-adaptation.json",
        "agent-inputs/asset-decomposition.json",
        "findings/visual-structure.json",
        "findings/text-requirements.json",
        "findings/project-pattern.json",
        "findings/state-modeling.json",
        "findings/data-adaptation.json",
        "findings/asset-decomposition.json",
    ),
    "review-state-visual": (
        "request-packet.json",
        "contexts/normalized-context.json",
        "ui-requirement.draft.json",
        "contexts/roles/state-visual-review.json",
        "review-views/state-visual-review.review-view.json",
        "agent-inputs/state-visual-review.json",
    ),
    "review-schema-feasibility": (
        "request-packet.json",
        "contexts/normalized-context.json",
        "ui-requirement.draft.json",
        "contexts/roles/schema-feasibility-review.json",
        "review-views/schema-feasibility-review.review-view.json",
        "agent-inputs/schema-feasibility-review.json",
    ),
    "review-coverage": (
        "request-packet.json",
        "contexts/normalized-context.json",
        "ui-requirement.draft.json",
        "contexts/roles/coverage-review.json",
        "review-views/coverage-review.review-view.json",
        "agent-inputs/coverage-review.json",
    ),
    "finalize-review-resolutions": (
        "request-packet.json",
        "contexts/normalized-context.json",
        "ui-requirement.draft.json",
        "contexts/roles/state-visual-review.json",
        "contexts/roles/schema-feasibility-review.json",
        "contexts/roles/coverage-review.json",
        "review-views/state-visual-review.review-view.json",
        "review-views/schema-feasibility-review.review-view.json",
        "review-views/coverage-review.review-view.json",
        "agent-inputs/state-visual-review.json",
        "agent-inputs/schema-feasibility-review.json",
        "agent-inputs/coverage-review.json",
        "findings/state-visual-review.json",
        "findings/schema-feasibility-review.json",
        "findings/coverage-review.json",
    ),
    "strict-validate-requirement": (
        "request-packet.json",
        "inputs/shared-widget-shortlist.json",
        "contexts/normalized-context.json",
        "ui-requirement.draft.json",
        "ui-requirement.pending.json",
        "agent-inputs/visual-structure.json",
        "agent-inputs/text-requirements.json",
        "agent-inputs/project-pattern.json",
        "agent-inputs/state-modeling.json",
        "agent-inputs/data-adaptation.json",
        "agent-inputs/asset-decomposition.json",
        "agent-inputs/state-visual-review.json",
        "agent-inputs/schema-feasibility-review.json",
        "agent-inputs/coverage-review.json",
        "contexts/roles/state-modeling.json",
        "contexts/roles/data-adaptation.json",
        "contexts/roles/asset-decomposition.json",
        "contexts/roles/state-visual-review.json",
        "contexts/roles/schema-feasibility-review.json",
        "contexts/roles/coverage-review.json",
        "review-views/state-visual-review.review-view.json",
        "review-views/schema-feasibility-review.review-view.json",
        "review-views/coverage-review.review-view.json",
        "findings/visual-structure.json",
        "findings/text-requirements.json",
        "findings/project-pattern.json",
        "findings/state-modeling.json",
        "findings/data-adaptation.json",
        "findings/asset-decomposition.json",
        "findings/state-visual-review.json",
        "findings/schema-feasibility-review.json",
        "findings/coverage-review.json",
    ),
    "requirements-confirmation": (
        "ui-requirement.pending.json",
        "status/ui-requirement.strict-valid.json",
    ),
    "prepare-accepted-build-view": (
        "request-packet.json",
        "contexts/normalized-context.json",
        "ui-requirement.draft.json",
        "ui-requirement.json",
        "status/ui-requirement.strict-valid.json",
        "agent-inputs/visual-structure.json",
        "agent-inputs/text-requirements.json",
        "agent-inputs/project-pattern.json",
        "agent-inputs/state-modeling.json",
        "agent-inputs/data-adaptation.json",
        "agent-inputs/asset-decomposition.json",
        "agent-inputs/state-visual-review.json",
        "agent-inputs/schema-feasibility-review.json",
        "agent-inputs/coverage-review.json",
        "contexts/roles/state-modeling.json",
        "contexts/roles/data-adaptation.json",
        "contexts/roles/asset-decomposition.json",
        "contexts/roles/state-visual-review.json",
        "contexts/roles/schema-feasibility-review.json",
        "contexts/roles/coverage-review.json",
        "review-views/state-visual-review.review-view.json",
        "review-views/schema-feasibility-review.review-view.json",
        "review-views/coverage-review.review-view.json",
        "findings/visual-structure.json",
        "findings/text-requirements.json",
        "findings/project-pattern.json",
        "findings/state-modeling.json",
        "findings/data-adaptation.json",
        "findings/asset-decomposition.json",
        "findings/state-visual-review.json",
        "findings/schema-feasibility-review.json",
        "findings/coverage-review.json",
    ),
    "plan-umg-build": ("ui-requirement.json", "accepted-build-view.json"),
    "validate-build-plan": (
        "ui-requirement.json",
        "accepted-build-view.json",
        "ui-build-bundle.planned.json",
    ),
    "build-verified-umg": (
        "ui-requirement.json",
        "accepted-build-view.json",
        "ui-build-bundle.planned.json",
        "status/ui-build-plan.pre-mutation-valid.json",
    ),
    "post-save-unreal-readback": (
        "ui-requirement.json",
        "ui-build-bundle.json",
    ),
    "present-build-results": (
        "ui-build-bundle.json",
        "unreal-widget-readback.json",
    ),
    "build-results-confirmation": (
        "ui-requirement.json",
        "ui-build-bundle.json",
        "unreal-widget-readback.json",
        "status/build-results.presented.json",
    ),
    "document-program-handoff": (
        "ui-requirement.json",
        "ui-build-bundle.json",
        "unreal-widget-readback.json",
        "ui-build-acceptance.json",
    ),
}

EXPECTED_STEP_OUTPUTS = {
    "validate-packet": (
        "status/request-packet.validated.json",
        "inputs/shared-widget-shortlist.json",
        "agent-inputs/visual-structure.json",
        "agent-inputs/text-requirements.json",
        "agent-inputs/project-pattern.json",
    ),
    "discover-visual-structure": ("findings/visual-structure.json",),
    "discover-text-requirements": ("findings/text-requirements.json",),
    "discover-project-pattern": ("findings/project-pattern.json",),
    "validate-discovery": ("status/discovery-findings.validated.json",),
    "normalize-identities": (
        "contexts/normalized-context.json",
        "contexts/roles/state-modeling.json",
        "contexts/roles/data-adaptation.json",
        "contexts/roles/asset-decomposition.json",
        "agent-inputs/state-modeling.json",
        "agent-inputs/data-adaptation.json",
        "agent-inputs/asset-decomposition.json",
    ),
    "analyze-state-modeling": ("findings/state-modeling.json",),
    "analyze-data-adaptation": ("findings/data-adaptation.json",),
    "analyze-asset-decomposition": ("findings/asset-decomposition.json",),
    "validate-focused": ("status/focused-findings.validated.json",),
    "synthesize-draft": (
        "ui-requirement.draft.json",
        "contexts/roles/state-visual-review.json",
        "contexts/roles/schema-feasibility-review.json",
        "contexts/roles/coverage-review.json",
        "review-views/state-visual-review.review-view.json",
        "review-views/schema-feasibility-review.review-view.json",
        "review-views/coverage-review.review-view.json",
        "agent-inputs/state-visual-review.json",
        "agent-inputs/schema-feasibility-review.json",
        "agent-inputs/coverage-review.json",
    ),
    "review-state-visual": ("findings/state-visual-review.json",),
    "review-schema-feasibility": ("findings/schema-feasibility-review.json",),
    "review-coverage": ("findings/coverage-review.json",),
    "finalize-review-resolutions": ("ui-requirement.pending.json",),
    "strict-validate-requirement": ("status/ui-requirement.strict-valid.json",),
    "requirements-confirmation": (),
    "prepare-accepted-build-view": ("accepted-build-view.json",),
    "plan-umg-build": ("ui-build-bundle.planned.json",),
    "validate-build-plan": ("status/ui-build-plan.pre-mutation-valid.json",),
    "build-verified-umg": ("ui-build-bundle.json",),
    "post-save-unreal-readback": ("unreal-widget-readback.json",),
    "present-build-results": ("status/build-results.presented.json",),
    "build-results-confirmation": (),
    "document-program-handoff": (
        "ui-program-handoff.json",
        "program-document-content.json",
        "document-verification.json",
    ),
}

EXPECTED_GATE_ARTIFACTS = {
    "requirements-confirmation": "ui-requirement.json",
    "build-results-confirmation": "ui-build-acceptance.json",
}

ROLE_PACKET_PATHS = {
    role: f"agent-inputs/{role}.json" for role in REQUIRED_FINDINGS_ROLES
}
ROLE_CONTEXT_PATHS = {
    role: f"contexts/roles/{role}.json" for role in FOCUSED_ROLES + REVIEW_ROLES
}
REVIEW_VIEW_PATHS = {
    role: f"review-views/{role}.review-view.json" for role in REVIEW_ROLES
}
EXPECTED_PREPARATION_OUTPUTS = {
    "validate-packet": (
        "status/request-packet.validated.json",
        "inputs/shared-widget-shortlist.json",
        *(ROLE_PACKET_PATHS[role] for role in DISCOVERY_ROLES),
    ),
    "normalize-identities": (
        "contexts/normalized-context.json",
        *(ROLE_CONTEXT_PATHS[role] for role in FOCUSED_ROLES),
        *(ROLE_PACKET_PATHS[role] for role in FOCUSED_ROLES),
    ),
    "synthesize-draft": (
        "ui-requirement.draft.json",
        *(ROLE_CONTEXT_PATHS[role] for role in REVIEW_ROLES),
        *(REVIEW_VIEW_PATHS[role] for role in REVIEW_ROLES),
        *(ROLE_PACKET_PATHS[role] for role in REVIEW_ROLES),
    ),
    "prepare-accepted-build-view": ("accepted-build-view.json",),
}

EXPECTED_AGENT_INPUTS = {
    **{step: (ROLE_PACKET_PATHS[role],) for role, step in ROLE_TO_STEP.items()},
    "plan-umg-build": ("accepted-build-view.json",),
}

STRICT_AUTHORITY_INPUTS = {
    "ui-requirement.draft.json",
    *(ROLE_PACKET_PATHS[role] for role in REQUIRED_FINDINGS_ROLES),
    *(ROLE_CONTEXT_PATHS[role] for role in FOCUSED_ROLES + REVIEW_ROLES),
    *(REVIEW_VIEW_PATHS[role] for role in REVIEW_ROLES),
}


class ContractError(ValueError):
    """Raised when a workflow or materialized plan violates the contract."""


class _DuplicateJSONKeyError(ValueError):
    """Raised while decoding an object whose source repeats a member name."""


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build one JSON object while rejecting duplicate keys at every depth."""

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJSONKeyError(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle, object_pairs_hook=_strict_json_object)
    except FileNotFoundError as error:
        raise ContractError(f"JSON file does not exist: {path}") from error
    except _DuplicateJSONKeyError as error:
        raise ContractError(f"Invalid JSON in {path}: {error}") from error
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
        agent_inputs = _strings(
            step.get("agentInputs"), f"{prefix}.agentInputs", errors
        )
        outputs = _strings(step.get("outputs"), f"{prefix}.outputs", errors)
        for key, paths in (
            ("inputs", inputs),
            ("agentInputs", agent_inputs),
            ("outputs", outputs),
        ):
            for duplicate in sorted(_duplicates(paths)):
                errors.append(f"{prefix}.{key} repeats {duplicate!r}")
        for item_index, path_text in enumerate(inputs):
            _check_path(path_text, f"{prefix}.inputs[{item_index}]", errors)
        for item_index, path_text in enumerate(agent_inputs):
            _check_path(path_text, f"{prefix}.agentInputs[{item_index}]", errors)
            if path_text not in inputs:
                errors.append(
                    f"{prefix}.agentInputs[{item_index}] must also be declared in inputs"
                )
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
    """Raise ContractError unless *workflow* is the closed 2.0.0 contract."""

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
    if root.get("workflowVersion") != WORKFLOW_VERSION:
        errors.append(f"workflowVersion must be {WORKFLOW_VERSION!r}")
    _check_path(root.get("requestPacketRef"), "requestPacketRef", errors)

    dispatch_policy = _mapping(
        root.get("agentDispatchPolicy"), "agentDispatchPolicy", errors
    )
    if dispatch_policy != EXPECTED_AGENT_DISPATCH_POLICY:
        errors.append(
            "agentDispatchPolicy must be exactly packet-path-only/no-history/fork-none "
            "with no inherited conversation, model override, or reasoning override"
        )

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
    if exchange.get("compositeOutputs") != EXPECTED_COMPOSITE_OUTPUTS:
        errors.append(
            "artifactExchange.compositeOutputs must declare the exact planned-layout "
            "descriptor, containment, digest, enumeration, and validator contract"
        )
    prefixes = _strings(
        exchange.get("allowedOutputPrefixes"),
        "artifactExchange.allowedOutputPrefixes",
        errors,
    )
    allowed_prefixes = set(prefixes)
    if prefixes != list(EXPECTED_ALLOWED_OUTPUT_PREFIXES):
        errors.append(
            "artifactExchange.allowedOutputPrefixes must be the exact closed v2 list"
        )
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
    if root_outputs != list(EXPECTED_ALLOWED_ROOT_OUTPUTS):
        errors.append(
            "artifactExchange.allowedRootOutputs must be the exact closed v2 list"
        )
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
    build_dispatch_policy = _mapping(
        continuation.get("agentDispatchPolicy"),
        "protectedContinuation.agentDispatchPolicy",
        errors,
    )
    if build_dispatch_policy != EXPECTED_BUILD_AGENT_DISPATCH_POLICY:
        errors.append(
            "protectedContinuation.agentDispatchPolicy must require a fresh "
            "single-validated-artifact-path/no-history/fork-none build planner "
            "with no inherited conversation, model override, or reasoning override"
        )
    evidence_contract = _mapping(
        continuation.get("preMutationEvidenceContract"),
        "protectedContinuation.preMutationEvidenceContract",
        errors,
    )
    if evidence_contract != EXPECTED_PREMUTATION_EVIDENCE_CONTRACT:
        errors.append(
            "protectedContinuation.preMutationEvidenceContract must bind the exact "
            "closed Schema, builder/validator, evidence path, and pre-Editor barrier"
        )
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
    analysis_order = [
        step.get("id") for step in analysis_steps if isinstance(step.get("id"), str)
    ]
    if analysis_order != list(EXPECTED_ANALYSIS_STEPS):
        errors.append(
            "analysis step order is closed; "
            f"expected={list(EXPECTED_ANALYSIS_STEPS)!r}, got={analysis_order!r}"
        )
    if set(continuation_map) != set(EXPECTED_CONTINUATION_STEPS):
        missing = sorted(set(EXPECTED_CONTINUATION_STEPS) - set(continuation_map))
        extra = sorted(set(continuation_map) - set(EXPECTED_CONTINUATION_STEPS))
        errors.append(f"protected continuation step set is closed; missing={missing}, extra={extra}")
    continuation_order = [
        step.get("id")
        for step in continuation_steps
        if isinstance(step.get("id"), str)
    ]
    if continuation_order != list(EXPECTED_CONTINUATION_STEPS):
        errors.append(
            "protected continuation step order is closed; "
            f"expected={list(EXPECTED_CONTINUATION_STEPS)!r}, got={continuation_order!r}"
        )

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
    for step_id, expected_execution in EXPECTED_STEP_EXECUTIONS.items():
        step = all_map.get(step_id)
        if step and step.get("execution") != expected_execution:
            errors.append(
                f"step {step_id!r} must use execution {expected_execution!r}"
            )

    for step_id in EXPECTED_STEP_KINDS:
        step = all_map.get(step_id)
        if step is None:
            continue
        expected_inputs = list(EXPECTED_STEP_INPUTS[step_id])
        if step.get("inputs") != expected_inputs:
            errors.append(
                f"step {step_id!r} inputs must be exactly {expected_inputs!r}; "
                f"got {step.get('inputs')!r}"
            )
        expected_outputs = list(EXPECTED_STEP_OUTPUTS[step_id])
        if step.get("outputs") != expected_outputs:
            errors.append(
                f"step {step_id!r} outputs must be exactly {expected_outputs!r}; "
                f"got {step.get('outputs')!r}"
            )

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

    for step_id, step in all_map.items():
        expected_agent_inputs = list(EXPECTED_AGENT_INPUTS.get(step_id, ()))
        actual_agent_inputs = step.get("agentInputs")
        if actual_agent_inputs != expected_agent_inputs:
            errors.append(
                f"step {step_id!r} agentInputs must be exactly {expected_agent_inputs!r}; "
                f"got {actual_agent_inputs!r}"
            )

    worker_packet_paths: list[str] = []
    for role, step_id in ROLE_TO_STEP.items():
        step = all_map.get(step_id, {})
        actual_agent_inputs = step.get("agentInputs", [])
        if isinstance(actual_agent_inputs, list):
            worker_packet_paths.extend(
                path for path in actual_agent_inputs if isinstance(path, str)
            )
    if len(worker_packet_paths) != len(set(worker_packet_paths)):
        errors.append("the nine findings workers must receive nine unique role packets")

    for step_id, expected_outputs in EXPECTED_PREPARATION_OUTPUTS.items():
        step = all_map.get(step_id)
        if step is not None and step.get("outputs") != list(expected_outputs):
            errors.append(
                f"step {step_id!r} outputs must be exactly {list(expected_outputs)!r}; "
                f"got {step.get('outputs')!r}"
            )

    expected_preparation_validators = {
        "validate-packet": "request-packet-and-discovery-role-packets",
        "normalize-identities": "normalized-context-and-focused-role-packets",
        "synthesize-draft": "draft-and-review-role-packets",
        "prepare-accepted-build-view": "accepted-build-view",
        "plan-umg-build": "planned-build-bundle-structure",
        "validate-build-plan": "build-plan-pre-mutation",
        "build-verified-umg": "build-bundle-final",
    }
    for step_id, expected_validator in expected_preparation_validators.items():
        step = all_map.get(step_id)
        if step is not None and step.get("contractValidator") != expected_validator:
            errors.append(
                f"step {step_id!r} must use contractValidator {expected_validator!r}"
            )

    strict_step = all_map.get("strict-validate-requirement", {})
    strict_inputs = strict_step.get("inputs", [])
    if isinstance(strict_inputs, list):
        missing_strict_inputs = sorted(STRICT_AUTHORITY_INPUTS - set(strict_inputs))
        if missing_strict_inputs:
            errors.append(
                "strict-validate-requirement must cover the immutable Draft, all nine "
                "role packets, all six role contexts, and all three Review Views; "
                f"missing={missing_strict_inputs}"
            )
    strict_instruction = strict_step.get("instruction", "")
    if (
        not isinstance(strict_instruction, str)
        or "--review-draft ui-requirement.draft.json" not in strict_instruction
    ):
        errors.append(
            "strict-validate-requirement instruction must include "
            "'--review-draft ui-requirement.draft.json'"
        )

    accepted_view_step = all_map.get("prepare-accepted-build-view", {})
    accepted_inputs = accepted_view_step.get("inputs", [])
    for required_input in ("ui-requirement.json", "ui-requirement.draft.json"):
        if not isinstance(accepted_inputs, list) or required_input not in accepted_inputs:
            errors.append(
                f"prepare-accepted-build-view inputs must include {required_input!r}"
            )
    accepted_instruction = accepted_view_step.get("instruction", "")
    for token in (
        "--review-draft ui-requirement.draft.json",
        "mode projected",
        "buildAllowed true",
        "coverage complete",
        "full-fallback",
        "blocks construction",
    ):
        if not isinstance(accepted_instruction, str) or token not in accepted_instruction:
            errors.append(
                f"prepare-accepted-build-view instruction must enforce {token!r}"
            )

    plan_step = all_map.get("plan-umg-build", {})
    if plan_step.get("inputs") != [
        "ui-requirement.json",
        "accepted-build-view.json",
    ]:
        errors.append(
            "plan-umg-build inputs must keep the complete Requirement as hidden "
            "validator authority beside the Accepted Build View"
        )
    if plan_step.get("outputs") != ["ui-build-bundle.planned.json"]:
        errors.append("plan-umg-build must emit only the staged planned Bundle")
    plan_instruction = plan_step.get("instruction", "")
    for token in (
        "fresh build-planning Agent",
        "complete visible input is only accepted-build-view.json",
        "read and obey that View's exact dispatchContract",
        "no inherited history or model/reasoning overrides",
        "composite ui-build-bundle.planned.json output",
        "assets[].layoutSpecPath",
        "layouts/ sidecar",
        "performs no Unreal Editor connection or mutation",
        "replaced by a fresh task",
    ):
        if not isinstance(plan_instruction, str) or token not in plan_instruction:
            errors.append(f"plan-umg-build instruction must preserve {token!r}")

    validate_plan_step = all_map.get("validate-build-plan", {})
    if validate_plan_step.get("inputs") != [
        "ui-requirement.json",
        "accepted-build-view.json",
        "ui-build-bundle.planned.json",
    ]:
        errors.append(
            "validate-build-plan must read the full Requirement, View, and staged Bundle"
        )
    if validate_plan_step.get("outputs") != [
        "status/ui-build-plan.pre-mutation-valid.json"
    ]:
        errors.append("validate-build-plan must emit the composite bound status")
    validate_plan_instruction = validate_plan_step.get("instruction", "")
    for token in (
        "Before any Unreal Editor connection or mutation",
        "enforce run-root containment and unique complete enumeration",
        "verify each layoutSpecSha256",
        "UILayoutSpec 0.2 validator",
        "validate_build_bundle.py ui-build-bundle.planned.json",
        "validate_requirement_coverage.py",
        "native prepare_build.py v0.2 plans/<asset>.plan.json per buildable asset",
        "reuse-only assets receive an explicit null/skip record",
        "orchestration/scripts/build_plan_evidence.py <artifact-root> --plugin-root <plugin-root>",
        "regenerate and exactly compare every native plan",
        "plans[].assetId/path/sha256 records must exactly and completely enumerate the build order",
        "otherwise stop without mutation",
    ):
        if (
            not isinstance(validate_plan_instruction, str)
            or token not in validate_plan_instruction
        ):
            errors.append(f"validate-build-plan instruction must preserve {token!r}")

    build_step = all_map.get("build-verified-umg", {})
    if build_step.get("inputs") != [
        "ui-requirement.json",
        "accepted-build-view.json",
        "ui-build-bundle.planned.json",
        "status/ui-build-plan.pre-mutation-valid.json",
    ]:
        errors.append("build-verified-umg must consume the validated staged build plan")
    if build_step.get("agentInputs") != []:
        errors.append("build-verified-umg must not dispatch another Agent")
    if build_step.get("outputs") != ["ui-build-bundle.json"]:
        errors.append("build-verified-umg must emit only the final Bundle")
    build_instruction = build_step.get("instruction", "")
    for token in (
        "only after validating",
        "orchestration/scripts/build_plan_evidence.py <artifact-root> --plugin-root <plugin-root> --validate-only",
        "status/ui-build-plan.pre-mutation-valid.json",
        "native prepare_build.py v0.2 execution plans",
        "validate_build_bundle.py ui-build-bundle.json",
        "complete Requirement coverage validator",
    ):
        if not isinstance(build_instruction, str) or token not in build_instruction:
            errors.append(f"build-verified-umg instruction must preserve {token!r}")

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
            "agentInputs": [
                {
                    "logicalPath": path_text,
                    "path": str(
                        _materialize_path(root, path_text, request_ref, packet)
                    ),
                    "integrity": {"algorithm": "sha256", "required": True},
                }
                for path_text in step["agentInputs"]
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
        "manifestVersion": MANIFEST_VERSION,
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
        "agentDispatchPolicy": dict(workflow["agentDispatchPolicy"]),
        "authority": {
            "source": "filesystem-artifacts",
            "messagesAreAuthoritative": False,
            "completionRule": workflow["artifactExchange"]["completionRule"],
            "integrityAlgorithm": workflow["artifactExchange"]["integrityAlgorithm"],
            "failClosedOnMissingOrDigestMismatch": workflow["artifactExchange"][
                "failClosedOnMissingOrDigestMismatch"
            ],
            "provenanceRule": workflow["artifactExchange"]["provenanceRule"],
            "compositeOutputs": list(
                workflow["artifactExchange"]["compositeOutputs"]
            ),
        },
        "requirementsTerminal": workflow["requirementsTerminal"],
        "parallelGroups": workflow["parallelGroups"],
        "steps": [materialize(step, "requirements") for step in analysis_steps]
        + [materialize(step, "protected-continuation") for step in continuation_steps],
        "protectedContinuation": {
            "dispatchByDefault": False,
            "schedulerPolicy": workflow["protectedContinuation"]["schedulerPolicy"],
            "agentDispatchPolicy": dict(
                workflow["protectedContinuation"]["agentDispatchPolicy"]
            ),
            "preMutationEvidenceContract": dict(
                workflow["protectedContinuation"]["preMutationEvidenceContract"]
            ),
            "description": workflow["protectedContinuation"]["description"],
        },
        "adapterContract": {
            "mayCallVendorApi": True,
            "thisManifestCallsVendorApi": False,
            "dispatchRule": "Dispatch a step only after all declared dependencies have authoritative validated artifacts.",
            "agentInputRule": "Construct every agent prompt from agentInputs only; inputs are scheduler and validator authority and must not be exposed implicitly.",
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
                schema_properties = (
                    schema.get("properties", {}) if isinstance(schema, dict) else {}
                )
                schema_versions_match = (
                    isinstance(schema_properties, dict)
                    and isinstance(schema_properties.get("contractVersion"), dict)
                    and schema_properties["contractVersion"].get("const")
                    == CONTRACT_VERSION
                    and isinstance(schema_properties.get("workflowVersion"), dict)
                    and schema_properties["workflowVersion"].get("const")
                    == WORKFLOW_VERSION
                )
                if (
                    not isinstance(schema, dict)
                    or schema.get("$id") != SCHEMA_ID
                    or not schema_versions_match
                ):
                    raise ContractError(
                        f"schema must bind $id {SCHEMA_ID!r}, contractVersion "
                        f"{CONTRACT_VERSION!r}, and workflowVersion {WORKFLOW_VERSION!r}"
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
