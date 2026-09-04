#!/usr/bin/env python3
"""Dependency-free static checks for the portable runtime adapters."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


EXPECTED_ROLES = {
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

DISCOVERY_ROLES = {
    "visual-structure",
    "text-requirements",
    "project-pattern",
}
FOCUSED_ROLES = {
    "state-modeling",
    "data-adaptation",
    "asset-decomposition",
}
REVIEW_ROLES = {
    "state-visual-review",
    "schema-feasibility-review",
    "coverage-review",
}

EXPECTED_GROUPS = {
    "discovery": DISCOVERY_ROLES,
    "focused-analysis": FOCUSED_ROLES,
    "independent-review": REVIEW_ROLES,
}

EXPECTED_DISPATCH_POLICY = {
    "promptContract": "packet-path-only",
    "historyPolicy": "none",
    "forkTurns": "none",
    "inheritsConversation": False,
    "allowModelOverride": False,
    "allowReasoningOverride": False,
}

EXPECTED_BUILD_DISPATCH_POLICY = {
    "promptContract": "single-validated-artifact-path",
    "historyPolicy": "none",
    "forkTurns": "none",
    "inheritsConversation": False,
    "allowModelOverride": False,
    "allowReasoningOverride": False,
}

EXPECTED_WORKBUDDY_POLICY = {
    "prompt_contract": "packet-path-only",
    "history_policy": "none",
    "inherits_conversation": False,
    "model_override_allowed": False,
    "reasoning_override_allowed": False,
}

EXPECTED_WORKBUDDY_ROLE_RETRY_POLICY = {
    "on_validation_failure": "terminate-then-fresh-delegation",
    "reuse_agent": False,
    "prompt": "agent-inputs/<same-role>.json",
}

EXPECTED_WORKBUDDY_BUILD_POLICY = {
    "prompt_contract": "single-validated-artifact-path",
    "history_policy": "none",
    "inherits_conversation": False,
    "model_override_allowed": False,
    "reasoning_override_allowed": False,
}

EXPECTED_CODEX_RETRY_POLICY = {
    "onValidationFailure": "interrupt-then-fresh-spawn",
    "reuseAgent": False,
    "prompt": "agent-inputs/<same-role>.json",
}

EXPECTED_CODEX_BUILD_RETRY_POLICY = {
    "onValidationFailure": "interrupt-then-fresh-spawn",
    "reuseAgent": False,
    "prompt": "accepted-build-view.json",
}

EXPECTED_BUILD_DISPATCH_CONTRACT_REF = "accepted-build-view.json#dispatchContract"

EXPECTED_PREMUTATION_EVIDENCE_CONTRACT = {
    "artifact": "status/ui-build-plan.pre-mutation-valid.json",
    "schemaRef": "build-plan-pre-mutation.schema.json",
    "toolRef": "scripts/build_plan_evidence.py",
    "generationMode": "generate-and-self-validate",
    "revalidationMode": "--validate-only",
    "requiredBeforeEditorMutation": True,
}

EXPECTED_HUMAN_GATE_IDS = {"requirement-review", "build-acceptance"}
EXPECTED_HUMAN_GATE_WORKFLOW_STEPS = {
    "requirements-confirmation",
    "build-results-confirmation",
}

EXPECTED_CONTRACT_STAGES = [
    {"id": "validate-request", "owner": "coordinator", "dependsOn": []},
    {
        "id": "discovery",
        "owner": "delegated-roles",
        "parallel": True,
        "roles": ["visual-structure", "text-requirements", "project-pattern"],
        "dependsOn": ["validate-request"],
    },
    {
        "id": "validate-discovery",
        "owner": "coordinator",
        "dependsOn": ["discovery"],
    },
    {
        "id": "normalize-context",
        "owner": "coordinator",
        "dependsOn": ["validate-discovery"],
    },
    {
        "id": "focused-analysis",
        "owner": "delegated-roles",
        "parallel": True,
        "roles": ["state-modeling", "data-adaptation", "asset-decomposition"],
        "dependsOn": ["normalize-context"],
    },
    {
        "id": "validate-focused",
        "owner": "coordinator",
        "dependsOn": ["focused-analysis"],
    },
    {
        "id": "synthesize-draft",
        "owner": "coordinator",
        "dependsOn": ["validate-focused"],
    },
    {
        "id": "independent-review",
        "owner": "delegated-roles",
        "parallel": True,
        "roles": [
            "state-visual-review",
            "schema-feasibility-review",
            "coverage-review",
        ],
        "dependsOn": ["synthesize-draft"],
    },
    {
        "id": "adjudicate-review",
        "owner": "coordinator",
        "dependsOn": ["independent-review"],
    },
    {
        "id": "strict-requirement-validation",
        "owner": "coordinator",
        "dependsOn": ["adjudicate-review"],
    },
    {
        "id": "requirement-review-gate",
        "owner": "user",
        "type": "human-gate",
        "decisionActor": "user",
        "artifactRecorder": "coordinator",
        "mustStop": True,
        "dependsOn": ["strict-requirement-validation"],
    },
]

EXPECTED_ARTIFACT_AUTHORITY = {
    "authoritative": [
        "validated request packet",
        "validated AgentFindings files",
        "validated full normalized context",
        "strictly validated UIRequirementSpec",
    ],
    "validatedDispatchArtifacts": [
        "no-history role packets",
        "role context projections",
        "review views",
        "accepted build view",
    ],
    "nonAuthoritative": [
        "role context projections and their full-fallback copies",
        "review views",
        "accepted build view",
        "subagent final messages",
        "chat summaries",
        "runtime task status",
    ],
    "rule": (
        "Validated dispatch artifacts constrain Agent-visible input and bind their "
        "complete sources, but never replace the full RequestPacket, normalized "
        "context, Draft, accepted Requirement, Bundle, or their validators. A runtime "
        "receipt only announces an artifact."
    ),
}

EXPECTED_WORKBUDDY_DEPENDENCIES = {
    "validate-request": set(),
    "discover-visual": {"validate-request"},
    "discover-text": {"validate-request"},
    "discover-project": {"validate-request"},
    "validate-discovery": {
        "discover-visual",
        "discover-text",
        "discover-project",
    },
    "normalize-context": {"validate-discovery"},
    "analyze-state": {"normalize-context"},
    "analyze-adaptation": {"normalize-context"},
    "analyze-assets": {"normalize-context"},
    "validate-focused": {
        "analyze-state",
        "analyze-adaptation",
        "analyze-assets",
    },
    "synthesize-draft": {"validate-focused"},
    "review-state-visual": {"synthesize-draft"},
    "review-schema": {"synthesize-draft"},
    "review-coverage": {"synthesize-draft"},
    "adjudicate-review": {
        "review-state-visual",
        "review-schema",
        "review-coverage",
    },
    "strict-requirement-validation": {"adjudicate-review"},
    "requirement-user-gate": {"strict-requirement-validation"},
}


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


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as stream:
        try:
            data = json.load(stream, object_pairs_hook=_strict_json_object)
        except _DuplicateJSONKeyError as error:
            raise ValueError(f"{path}: {error}") from error
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return data


def load_json_front_matter(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"{path}: missing opening front-matter delimiter")
    closing = text.find("\n---\n", 4)
    if closing < 0:
        raise ValueError(f"{path}: missing closing front-matter delimiter")
    try:
        header = json.loads(
            text[4:closing], object_pairs_hook=_strict_json_object
        )
    except _DuplicateJSONKeyError as error:
        raise ValueError(f"{path}: front matter {error}") from error
    if not isinstance(header, dict):
        raise ValueError(f"{path}: front matter must be a mapping")
    return header, text[closing + 5 :]


def by_id(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {item.get("id", ""): item for item in items}


def duplicate_ids(items: list[dict[str, Any]]) -> set[Any]:
    seen: set[Any] = set()
    duplicates: set[Any] = set()
    for item in items:
        identifier = item.get("id")
        if identifier in seen:
            duplicates.add(identifier)
        seen.add(identifier)
    return duplicates


def inspect_human_gates(
    contract: dict[str, Any],
) -> tuple[
    list[str],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    """Validate the raw two-record gate list before constructing lookup maps."""

    errors: list[str] = []
    raw_gates = contract.get("humanGates")
    if not isinstance(raw_gates, list):
        return ["contract humanGates must be an array of exactly two records"], {}, {}
    if len(raw_gates) != 2:
        errors.append("contract humanGates must contain exactly two raw records")

    records: list[dict[str, Any]] = []
    for index, record in enumerate(raw_gates):
        if not isinstance(record, dict):
            errors.append(f"contract humanGates[{index}] must be an object")
            continue
        if any(record == previous for previous in records):
            errors.append("contract humanGates contains a duplicate raw record")
        records.append(record)

    ids = [record.get("id") for record in records]
    workflow_steps = [record.get("workflowStep") for record in records]
    if any(not isinstance(identifier, str) or not identifier for identifier in ids):
        errors.append("every human gate id must be a nonempty string")
    if any(not isinstance(step_id, str) or not step_id for step_id in workflow_steps):
        errors.append("every human gate workflowStep must be a nonempty string")

    string_ids = [identifier for identifier in ids if isinstance(identifier, str)]
    string_steps = [step_id for step_id in workflow_steps if isinstance(step_id, str)]
    if len(string_ids) != len(set(string_ids)):
        errors.append("contract humanGates ids must be unique")
    if len(string_steps) != len(set(string_steps)):
        errors.append("contract humanGates workflowStep values must be unique")
    if set(string_ids) != EXPECTED_HUMAN_GATE_IDS:
        errors.append("contract must contain exactly the requirement and build user gate ids")
    if set(string_steps) != EXPECTED_HUMAN_GATE_WORKFLOW_STEPS:
        errors.append("contract must map exactly the two canonical human gate workflow steps")

    by_gate_id = {
        record["id"]: record
        for record in records
        if isinstance(record.get("id"), str)
    }
    by_workflow_step = {
        record["workflowStep"]: record
        for record in records
        if isinstance(record.get("workflowStep"), str)
    }
    return errors, by_gate_id, by_workflow_step


def result_const(step: dict[str, Any], field: str) -> Any:
    schema = step.get("result_schema", {})
    if field not in schema.get("required", []):
        return None
    return schema.get("properties", {}).get(field, {}).get("const")


def validate_closed_result_schema(step: dict[str, Any], label: str) -> list[str]:
    """Require every declared receipt field and reject undeclared receipt data."""

    schema = step.get("result_schema", {})
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    if (
        schema.get("type") != "object"
        or schema.get("additionalProperties") is not False
        or not isinstance(properties, dict)
        or not isinstance(required, list)
        or len(required) != len(set(required))
        or set(required) != set(properties)
    ):
        return [f"{label} result_schema must require exactly its closed properties"]
    return []


def workflow_steps(workflow: dict[str, Any]) -> dict[str, dict[str, Any]]:
    steps = list(workflow.get("steps", []))
    steps.extend(workflow.get("protectedContinuation", {}).get("steps", []))
    return by_id(steps)


def _expected_packet(role: str) -> str:
    return f"agent-inputs/{role}.json"


def _expected_context(role: str) -> str | None:
    if role in FOCUSED_ROLES or role in REVIEW_ROLES:
        return f"contexts/roles/{role}.json"
    return None


def _expected_review_view(role: str) -> str | None:
    if role in REVIEW_ROLES:
        return f"review-views/{role}.review-view.json"
    return None


def validate_contract_against_workflow(
    contract: dict[str, Any], workflow: dict[str, Any]
) -> list[str]:
    """Compare adapter invariants to the vendor-neutral workflow source."""

    errors: list[str] = []
    human_gate_errors, _, contract_gates = inspect_human_gates(contract)
    errors.extend(human_gate_errors)
    contract_role_items = contract.get("roles", [])
    contract_roles = {item.get("role"): item for item in contract_role_items}
    if len(contract_role_items) != len(EXPECTED_ROLES) or len(contract_roles) != len(contract_role_items):
        errors.append("adapter contract must contain exactly nine unique role records")
    contract_role_names = set(contract_roles)
    workflow_role_names = set(workflow.get("requiredFindingsRoles", []))

    if contract.get("schemaVersion") != "2.0":
        errors.append("adapter contract must use schemaVersion 2.0")
    if workflow.get("contractVersion") != "2.0.0":
        errors.append("orchestration contractVersion must be 2.0.0")
    if contract.get("workflowId") != workflow.get("workflowId"):
        errors.append("adapter workflowId differs from the orchestration workflowId")
    if workflow_role_names != contract_role_names:
        errors.append("adapter and orchestration findings role sets differ")
    if contract.get("agentDispatchPolicy") != EXPECTED_DISPATCH_POLICY:
        errors.append("adapter dispatch policy is not the exact no-history packet-only policy")
    if workflow.get("agentDispatchPolicy") != EXPECTED_DISPATCH_POLICY:
        errors.append("orchestration dispatch policy is not the exact no-history packet-only policy")
    if contract.get("agentDispatchPolicy") != workflow.get("agentDispatchPolicy"):
        errors.append("adapter and orchestration dispatch policies differ")
    if contract.get("stages") != EXPECTED_CONTRACT_STAGES:
        errors.append(
            "adapter contract stages must match the exact closed order, ownership, "
            "dependencies, parallel roles, and gate metadata"
        )
    if contract.get("artifactAuthority") != EXPECTED_ARTIFACT_AUTHORITY:
        errors.append(
            "adapter artifactAuthority must exactly classify semantic authorities, "
            "validated dispatch artifacts, and non-authoritative receipts/messages"
        )

    steps = workflow_steps(workflow)
    workflow_role_steps = {
        step.get("agentRole"): step
        for step in steps.values()
        if step.get("agentRole") is not None
    }
    if set(workflow_role_steps) != contract_role_names:
        errors.append("orchestration worker steps do not map one-to-one to adapter roles")

    output_owner: dict[str, str] = {}
    for step_id, step in steps.items():
        for output in step.get("outputs", []):
            if isinstance(output, str):
                output_owner[output] = step_id

    for role, record in contract_roles.items():
        step = workflow_role_steps.get(role, {})
        expected_packet = _expected_packet(role)
        expected_context = _expected_context(role)
        expected_review_view = _expected_review_view(role)

        if record.get("packetPath") != expected_packet:
            errors.append(f"{role} packet path is not canonical")
        if record.get("contextPath") != expected_context:
            errors.append(f"{role} projected context path is not canonical")
        if record.get("reviewViewPath") != expected_review_view:
            if role in REVIEW_ROLES or record.get("reviewViewPath") is not None:
                errors.append(f"{role} Review View path is not canonical")
        if step.get("outputs") != [record.get("output")]:
            errors.append(f"{role} output differs between adapter and orchestration")

        inputs = step.get("inputs", [])
        agent_inputs = step.get("agentInputs", [])
        if agent_inputs != [expected_packet]:
            errors.append(f"{role} Agent-visible inputs must be exactly its one role packet")
        if expected_packet not in inputs:
            errors.append(f"{role} coordinator inputs omit its role packet")
        if not set(agent_inputs).issubset(set(inputs)):
            errors.append(f"{role} Agent-visible inputs are not a subset of validator inputs")
        forbidden_visible = {
            "request-packet.json",
            "contexts/normalized-context.json",
            "ui-requirement.draft.json",
            "ui-requirement.pending.json",
            "ui-requirement.json",
        }
        if forbidden_visible.intersection(agent_inputs):
            errors.append(f"{role} exposes a full authority to the delegated Agent")
        if any(
            isinstance(path, str)
            and (path.startswith("contexts/") or path.startswith("review-views/"))
            for path in agent_inputs
        ):
            errors.append(f"{role} exposes context/View sidecars outside its packet")

        actual_contexts = {
            path
            for path in inputs
            if isinstance(path, str) and path.startswith("contexts/roles/")
        }
        expected_contexts = {expected_context} if expected_context else set()
        if actual_contexts != expected_contexts:
            errors.append(f"{role} context path differs between adapter and orchestration")
        actual_review_views = {
            path
            for path in inputs
            if isinstance(path, str) and path.startswith("review-views/")
        }
        expected_review_views = {expected_review_view} if expected_review_view else set()
        if actual_review_views != expected_review_views:
            errors.append(f"{role} Review View path differs between adapter and orchestration")
        if step.get("parallelGroup") != record.get("stage"):
            errors.append(f"{role} parallel group differs between adapter and orchestration")

        expected_packet_owner = (
            "validate-packet"
            if role in DISCOVERY_ROLES
            else "normalize-identities"
            if role in FOCUSED_ROLES
            else "synthesize-draft"
        )
        if output_owner.get(expected_packet) != expected_packet_owner:
            errors.append(f"{role} packet is not produced by the protected coordinator step")
        if expected_context and output_owner.get(expected_context) != expected_packet_owner:
            errors.append(f"{role} context is not produced with its packet")
        if expected_review_view and output_owner.get(expected_review_view) != "synthesize-draft":
            errors.append(f"{role} Review View is not produced with the immutable Draft")

    contract_stages = by_id(contract.get("stages", []))
    workflow_groups = {
        group.get("id"): group for group in workflow.get("parallelGroups", [])
    }
    contract_group_ids = {
        stage_id
        for stage_id, stage in contract_stages.items()
        if stage.get("parallel") is True
    }
    if set(workflow_groups) != contract_group_ids:
        errors.append("adapter and orchestration parallel group ids differ")
    for group_id, group in workflow_groups.items():
        member_roles = {
            steps.get(member_id, {}).get("agentRole")
            for member_id in group.get("members", [])
        }
        expected_roles = set(contract_stages.get(group_id, {}).get("roles", []))
        if member_roles != expected_roles:
            errors.append(f"parallel group {group_id} has different roles")
        for member_id in group.get("members", []):
            if steps.get(member_id, {}).get("parallelGroup") != group_id:
                errors.append(f"workflow step {member_id} has a stale parallelGroup")

    layout = contract.get("artifactLayout", {})
    discovery_outputs = {
        "status/request-packet.validated.json",
        layout.get("sharedWidgetShortlist"),
        *(_expected_packet(role) for role in DISCOVERY_ROLES),
    }
    if set(steps.get("validate-packet", {}).get("outputs", [])) != discovery_outputs:
        errors.append("validate-packet does not own the exact discovery packet batch")

    focused_outputs = {
        layout.get("normalizedContext"),
        *(_expected_packet(role) for role in FOCUSED_ROLES),
        *(_expected_context(role) for role in FOCUSED_ROLES),
    }
    if set(steps.get("normalize-identities", {}).get("outputs", [])) != focused_outputs:
        errors.append("normalize-identities does not own the exact focused projections")

    review_outputs = {
        layout.get("draftRequirement"),
        *(_expected_packet(role) for role in REVIEW_ROLES),
        *(_expected_context(role) for role in REVIEW_ROLES),
        *(_expected_review_view(role) for role in REVIEW_ROLES),
    }
    if set(steps.get("synthesize-draft", {}).get("outputs", [])) != review_outputs:
        errors.append("synthesize-draft does not own the exact three Review Views and packets")

    if steps.get("finalize-review-resolutions", {}).get("outputs") != [
        layout.get("pendingRequirement")
    ]:
        errors.append("pending requirement output differs from adapter contract")

    strict_step = steps.get("strict-validate-requirement", {})
    strict_inputs = set(strict_step.get("inputs", []))
    required_strict_inputs = {
        "request-packet.json",
        layout.get("normalizedContext"),
        layout.get("draftRequirement"),
        layout.get("pendingRequirement"),
        *(_expected_packet(role) for role in EXPECTED_ROLES),
        *(_expected_context(role) for role in FOCUSED_ROLES | REVIEW_ROLES),
        *(_expected_review_view(role) for role in REVIEW_ROLES),
        *(record.get("output") for record in contract_roles.values()),
    }
    if not required_strict_inputs.issubset(strict_inputs):
        errors.append("strict requirement validation omits protected packet/context/View sidecars")
    strict_instruction = strict_step.get("instruction", "")
    if "--check-findings-files" not in strict_instruction or "--review-draft" not in strict_instruction:
        errors.append("strict requirement step omits Draft-aware linked validation")
    if strict_step.get("agentInputs") != []:
        errors.append("strict requirement validation must not dispatch an Agent")

    projection = contract.get("protectedBuildProjection", {})
    projection_step_id = projection.get("producerStep")
    projection_step = steps.get(projection_step_id, {})
    planning_step_id = projection.get("buildPlanningStep")
    planning_step = steps.get(planning_step_id, {})
    validate_plan_step_id = projection.get("preMutationValidatorStep")
    validate_plan_step = steps.get(validate_plan_step_id, {})
    build_step_id = projection.get("buildStep")
    build_step = steps.get(build_step_id, {})
    accepted_view = layout.get("acceptedBuildView")
    accepted_requirement = layout.get("acceptedRequirement")
    if projection.get("artifact") != accepted_view:
        errors.append("protected build projection artifact differs from layout")
    if projection_step.get("outputs") != [accepted_view]:
        errors.append("Accepted Build View producer has the wrong output")
    if projection_step.get("dependsOn") != ["requirements-confirmation"]:
        errors.append("Accepted Build View must be produced only after requirement acceptance")
    if projection_step.get("agentInputs") != []:
        errors.append("Accepted Build View projection must remain coordinator-only")
    continuation_policy = workflow.get("protectedContinuation", {}).get(
        "agentDispatchPolicy"
    )
    if projection.get("agentDispatchPolicy") != EXPECTED_BUILD_DISPATCH_POLICY:
        errors.append("protected build projection lacks the exact fresh planner policy")
    if continuation_policy != EXPECTED_BUILD_DISPATCH_POLICY:
        errors.append("orchestration protected continuation lacks the fresh planner policy")
    if projection.get("agentDispatchPolicy") != continuation_policy:
        errors.append("adapter and orchestration build planner policies differ")
    if projection.get("buildPlanningDispatchContract") != EXPECTED_BUILD_DISPATCH_CONTRACT_REF:
        errors.append("protected build projection must bind accepted-build-view dispatchContract")
    if projection.get("buildPlanningRetryPolicy") != EXPECTED_CODEX_BUILD_RETRY_POLICY:
        errors.append("protected build projection must require fresh same-View retry")
    workflow_evidence_contract = workflow.get("protectedContinuation", {}).get(
        "preMutationEvidenceContract"
    )
    if workflow_evidence_contract != EXPECTED_PREMUTATION_EVIDENCE_CONTRACT:
        errors.append("orchestration pre-mutation evidence contract is not closed")
    if projection.get("preMutationEvidenceContract") != workflow_evidence_contract:
        errors.append("adapter and orchestration pre-mutation evidence contracts differ")
    workflow_composites = workflow.get("artifactExchange", {}).get(
        "compositeOutputs"
    )
    if projection.get("compositeOutputs") != workflow_composites:
        errors.append("adapter and orchestration composite build outputs differ")
    if planning_step.get("dependsOn") != [projection_step_id]:
        errors.append("build planning must depend directly on the Accepted Build View gate")
    if planning_step.get("inputs") != [accepted_requirement, accepted_view]:
        errors.append("build planning inputs must keep hidden full Requirement authority")
    if planning_step.get("agentInputs") != projection.get("buildAgentInputs"):
        errors.append("build-planning Agent inputs differ from the protected projection contract")
    if planning_step.get("agentInputs") != [accepted_view]:
        errors.append("build-planning Agent must receive only Accepted Build View")
    if planning_step.get("outputs") != [projection.get("plannedBundle")]:
        errors.append("build planner must emit only the staged planned Bundle")
    if validate_plan_step.get("dependsOn") != [planning_step_id]:
        errors.append("pre-mutation validation must follow isolated build planning")
    if validate_plan_step.get("inputs") != [
        accepted_requirement,
        accepted_view,
        projection.get("plannedBundle"),
    ]:
        errors.append("pre-mutation validator must read full Requirement, View, and plan")
    if validate_plan_step.get("agentInputs") != []:
        errors.append("pre-mutation validation must remain coordinator-only")
    if validate_plan_step.get("outputs") != [projection.get("preMutationEvidence")]:
        errors.append("pre-mutation validation must emit its bound status artifact")
    if build_step.get("dependsOn") != [validate_plan_step_id]:
        errors.append("Editor build must depend on pre-mutation validation")
    if build_step.get("inputs") != [
        accepted_requirement,
        accepted_view,
        projection.get("plannedBundle"),
        projection.get("preMutationEvidence"),
    ]:
        errors.append("Editor build must consume the bound pre-mutation evidence")
    if build_step.get("agentInputs") != []:
        errors.append("Editor build must remain coordinator-owned after planning")
    if build_step.get("outputs") != [projection.get("finalBundle")]:
        errors.append("Editor build must emit only the final Bundle")
    if projection.get("validatorAuthority") != accepted_requirement:
        errors.append("full accepted Requirement must remain build validator authority")
    projection_instruction = projection_step.get("instruction", "")
    if (
        projection.get("requiredMode") not in projection_instruction
        or str(projection.get("buildAllowed", "")).lower() not in projection_instruction.lower()
        or "full-fallback" not in projection_instruction
    ):
        errors.append("Accepted Build View producer does not state fail-closed build conditions")

    workflow_gates = {
        step_id: step
        for step_id, step in steps.items()
        if step.get("kind") == "gate"
    }
    if set(contract_gates) != set(workflow_gates):
        errors.append("adapter and orchestration must define the same two gate steps")
    for step_id, gate_contract in contract_gates.items():
        gate_step = workflow_gates.get(step_id, {})
        gate_data = gate_step.get("gate", {})
        if gate_contract.get("decisionActor") != "user":
            errors.append(f"contract gate {step_id} decision actor must be the user")
        if gate_contract.get("artifactRecorder") != "coordinator":
            errors.append(f"contract gate {step_id} artifact recorder must be coordinator")
        if gate_data.get("actor") != gate_contract.get("decisionActor"):
            errors.append(f"workflow gate {step_id} actor differs from the contract")
        if gate_data.get("manualAdvance") is not True:
            errors.append(f"workflow gate {step_id} must require manual advance")
        if gate_data.get("decisionArtifact") != gate_contract.get("decisionArtifact"):
            errors.append(f"workflow gate {step_id} decision artifact differs")
        if set(gate_step.get("dependsOn", [])) != {gate_contract.get("afterStep")}:
            errors.append(f"workflow gate {step_id} predecessor differs")
    if accepted_requirement != contract_gates.get(
        "requirements-confirmation", {}
    ).get("decisionArtifact"):
        errors.append("accepted requirement output differs from the first gate artifact")

    return errors


def validate_codex_runtime_map(
    codex_map: dict[str, Any],
    workflow: dict[str, Any],
    contract: dict[str, Any],
) -> list[str]:
    """Reject any Codex mapping that can reuse history or bypass coordinator barriers."""

    errors: list[str] = []
    if codex_map.get("workflowId") != workflow.get("workflowId"):
        errors.append("Codex runtime map workflowId differs from orchestration")
    if codex_map.get("contractVersion") != workflow.get("contractVersion"):
        errors.append("Codex runtime map contractVersion differs from orchestration")
    if codex_map.get("agentDispatchPolicy") != workflow.get("agentDispatchPolicy"):
        errors.append("Codex runtime map dispatch policy differs from orchestration")
    if codex_map.get("dispatch") != "spawn_agent":
        errors.append("Codex role dispatch must use a fresh spawn_agent")
    if codex_map.get("stop") != "interrupt_agent":
        errors.append("Codex invalid-role handling must interrupt before retry")
    if codex_map.get("retryPolicy") != EXPECTED_CODEX_RETRY_POLICY:
        errors.append("Codex retry policy must interrupt then fresh-spawn the same packet")
    if codex_map.get("buildPlanningRetryPolicy") != EXPECTED_CODEX_BUILD_RETRY_POLICY:
        errors.append(
            "Codex build-planning retry must interrupt then fresh-spawn the same "
            "Accepted Build View"
        )
    if "correctActive" in codex_map or "retryIdle" in codex_map:
        errors.append("Codex runtime map must not expose history-reusing correction APIs")
    serialized = json.dumps(codex_map, sort_keys=True)
    if "send_message" in serialized or "followup_task" in serialized:
        errors.append("Codex runtime map contains a history-reusing primitive")
    if codex_map.get("receiptIsAuthoritative") is not False:
        errors.append("Codex task receipts must remain non-authoritative")
    if codex_map.get("artifactAndValidatorAreAuthoritative") is not True:
        errors.append("Codex must retain file-plus-validator authority")
    all_steps = list(workflow.get("steps", [])) + list(
        workflow.get("protectedContinuation", {}).get("steps", [])
    )
    expected_coordinator_steps = {
        step.get("id") for step in all_steps if step.get("execution") == "coordinator"
    }
    expected_human_steps = {
        step.get("id") for step in all_steps if step.get("execution") == "human"
    }
    if "coordinatorOnly" in codex_map:
        errors.append("Codex runtime map contains the obsolete coordinatorOnly field")
    if set(codex_map.get("coordinatorOwnedSteps", [])) != expected_coordinator_steps:
        errors.append("Codex coordinator-owned steps differ from the workflow-derived set")
    if set(codex_map.get("humanOwnedGateSteps", [])) != expected_human_steps:
        errors.append("Codex human-owned gate steps differ from the workflow-derived set")

    layout = contract.get("artifactLayout", {})
    projection = contract.get("protectedBuildProjection", {})
    if codex_map.get("buildPlanningStep") != projection.get("buildPlanningStep"):
        errors.append("Codex build planning step differs from the contract")
    if codex_map.get("buildPlanningAgentInputs") != [layout.get("acceptedBuildView")]:
        errors.append("Codex build planner must receive only Accepted Build View")
    if codex_map.get("buildPlanningAgentDispatchPolicy") != projection.get(
        "agentDispatchPolicy"
    ):
        errors.append("Codex build planner dispatch policy differs from the contract")
    if codex_map.get("buildPlanningAgentDispatchPolicy") != workflow.get(
        "protectedContinuation", {}
    ).get("agentDispatchPolicy"):
        errors.append("Codex build planner policy differs from orchestration")
    if codex_map.get("buildPlanningDispatchContract") != EXPECTED_BUILD_DISPATCH_CONTRACT_REF:
        errors.append("Codex build planner does not bind the View dispatchContract")
    if codex_map.get("buildPlanningDispatchContract") != projection.get(
        "buildPlanningDispatchContract"
    ):
        errors.append("Codex and adapter build planner dispatchContract refs differ")
    if codex_map.get("buildPlanningRetryPolicy") != projection.get(
        "buildPlanningRetryPolicy"
    ):
        errors.append("Codex and adapter build planner retry policies differ")
    if codex_map.get("buildPlanArtifact") != projection.get("plannedBundle"):
        errors.append("Codex staged build artifact differs from the contract")
    if codex_map.get("buildCompositeOutputs") != projection.get("compositeOutputs"):
        errors.append("Codex composite build outputs differ from the contract")
    if codex_map.get("preMutationValidationStep") != projection.get(
        "preMutationValidatorStep"
    ):
        errors.append("Codex pre-mutation validation step differs from the contract")
    if codex_map.get("preMutationValidationEvidence") != projection.get(
        "preMutationEvidence"
    ):
        errors.append("Codex pre-mutation evidence differs from the contract")
    if codex_map.get("preMutationEvidenceContract") != projection.get(
        "preMutationEvidenceContract"
    ):
        errors.append("Codex pre-mutation evidence Schema/tool contract differs")
    if codex_map.get("editorMutationStep") != projection.get("buildStep"):
        errors.append("Codex Editor mutation step differs from the contract")
    if codex_map.get("buildValidatorAuthority") != layout.get("acceptedRequirement"):
        errors.append("Codex build validator must retain the full accepted Requirement")
    return errors


def validate_workbuddy_build_mapping(
    build_workflow: dict[str, Any],
    workflow: dict[str, Any],
    contract: dict[str, Any],
) -> list[str]:
    """Validate the structured Accepted Build View continuation for WorkBuddy."""

    errors: list[str] = []
    if build_workflow.get("kind") != "workflow" or build_workflow.get("execution") != "main":
        errors.append("WorkBuddy protected build knowledge unit must be a main workflow")
    if build_workflow.get("portable_workflow_id") != workflow.get("workflowId"):
        errors.append("WorkBuddy build workflow id differs from orchestration")
    if build_workflow.get("agent_dispatch_policy") != EXPECTED_WORKBUDDY_BUILD_POLICY:
        errors.append("WorkBuddy build workflow must require fresh single-View dispatch")
    if build_workflow.get("build_planning_dispatch_contract") != EXPECTED_BUILD_DISPATCH_CONTRACT_REF:
        errors.append("WorkBuddy build planner does not bind the View dispatchContract")
    expected_workbuddy_retry = {
        "on_validation_failure": "terminate-then-fresh-delegation",
        "reuse_agent": False,
        "prompt": "accepted-build-view.json",
    }
    if build_workflow.get("build_planning_retry_policy") != expected_workbuddy_retry:
        errors.append("WorkBuddy build planner retry must be fresh and reuse the same View")
    if build_workflow.get("composite_artifact_sets") != workflow.get(
        "artifactExchange", {}
    ).get("compositeOutputs"):
        errors.append("WorkBuddy composite build outputs differ from orchestration")
    if build_workflow.get("pre_mutation_evidence_contract") != workflow.get(
        "protectedContinuation", {}
    ).get("preMutationEvidenceContract"):
        errors.append("WorkBuddy pre-mutation evidence Schema/tool contract differs")

    step_items = build_workflow.get("steps", [])
    steps = by_id(step_items)
    repeated_build_steps = duplicate_ids(step_items)
    if repeated_build_steps:
        errors.append(f"WorkBuddy protected build workflow repeats step ids: {sorted(repeated_build_steps)}")
    for item in step_items:
        errors.extend(
            validate_closed_result_schema(
                item, f"WorkBuddy protected build step {item.get('id')!r}"
            )
        )
    expected_step_order = [
        "prepare-accepted-build-view",
        "plan-umg-build",
        "validate-build-plan",
        "build-verified-umg",
        "post-save-unreal-readback",
        "present-built-result",
        "build-user-gate",
    ]
    if [item.get("id") for item in step_items] != expected_step_order:
        errors.append("WorkBuddy protected build workflow has a non-closed ordered step set")
    for main_step_id in (
        "prepare-accepted-build-view",
        "validate-build-plan",
        "build-verified-umg",
        "post-save-unreal-readback",
        "present-built-result",
        "build-user-gate",
    ):
        main_step = steps.get(main_step_id, {})
        if main_step.get("execution") != "main" or main_step.get("agent_role") is not None:
            errors.append(f"WorkBuddy {main_step_id} must remain coordinator-only")
    portable = workflow_steps(workflow)
    layout = contract.get("artifactLayout", {})

    prepare = steps.get("prepare-accepted-build-view", {})
    portable_prepare = portable.get("prepare-accepted-build-view", {})
    if prepare.get("portable_step") != "prepare-accepted-build-view":
        errors.append("WorkBuddy Accepted Build View step maps to the wrong portable step")
    if prepare.get("depends_on") != []:
        errors.append("WorkBuddy protected continuation must start only after external gate resume")
    if prepare.get("inputs") != portable_prepare.get("inputs"):
        errors.append("WorkBuddy Accepted Build View inputs differ from orchestration authorities")
    if prepare.get("outputs") != portable_prepare.get("outputs"):
        errors.append("WorkBuddy Accepted Build View outputs differ from orchestration")
    if result_const(prepare, "view_path") != layout.get("acceptedBuildView"):
        errors.append("WorkBuddy Accepted Build View step returns the wrong artifact")
    if result_const(prepare, "mode") != "projected":
        errors.append("WorkBuddy Accepted Build View must reject full fallback")
    if result_const(prepare, "build_allowed") is not True:
        errors.append("WorkBuddy Accepted Build View must require buildAllowed true")

    plan = steps.get("plan-umg-build", {})
    portable_plan = portable.get("plan-umg-build", {})
    if plan.get("execution") != "subagent" or plan.get("agent_role") != "build-planning":
        errors.append("WorkBuddy build planning must be an isolated delegated task")
    if plan.get("portable_step") != "plan-umg-build":
        errors.append("WorkBuddy build planner maps to the wrong portable step")
    if plan.get("depends_on") != ["prepare-accepted-build-view"]:
        errors.append("WorkBuddy build planner bypasses the Accepted Build View gate")
    if plan.get("inputs") != portable_plan.get("agentInputs"):
        errors.append("WorkBuddy build Agent must receive only the portable Agent View")
    if plan.get("outputs") != portable_plan.get("outputs"):
        errors.append("WorkBuddy build planner must emit only the staged Bundle")
    if result_const(plan, "artifact_path") != contract.get(
        "protectedBuildProjection", {}
    ).get("plannedBundle"):
        errors.append("WorkBuddy build planner returns the wrong staged Bundle")
    if result_const(plan, "validation_ready") is not True:
        errors.append("WorkBuddy build planner receipt must be validation-ready")
    if result_const(plan, "editor_mutation_performed") is not False:
        errors.append("WorkBuddy planner must prove that it did not mutate Unreal")

    validate_plan = steps.get("validate-build-plan", {})
    portable_validate_plan = portable.get("validate-build-plan", {})
    if validate_plan.get("portable_step") != "validate-build-plan":
        errors.append("WorkBuddy pre-mutation validator maps to the wrong portable step")
    if validate_plan.get("depends_on") != ["plan-umg-build"]:
        errors.append("WorkBuddy pre-mutation validation must follow planning")
    if validate_plan.get("inputs") != portable_validate_plan.get("inputs"):
        errors.append("WorkBuddy pre-mutation validator inputs differ from orchestration")
    if validate_plan.get("outputs") != portable_validate_plan.get("outputs"):
        errors.append("WorkBuddy pre-mutation status differs from orchestration")
    for field in (
        "valid",
        "full_requirement_validated",
        "view_validated",
        "coverage_validated",
        "layouts_validated",
        "plan_manifest_complete",
    ):
        if result_const(validate_plan, field) is not True:
            errors.append(f"WorkBuddy pre-mutation validator does not fail closed on {field}")
    if result_const(validate_plan, "editor_mutation_performed") is not False:
        errors.append("WorkBuddy pre-mutation validator must precede all Editor mutation")
    if result_const(validate_plan, "artifact_path") != contract.get(
        "protectedBuildProjection", {}
    ).get("preMutationEvidence"):
        errors.append("WorkBuddy pre-mutation validator returns the wrong status artifact")

    build = steps.get("build-verified-umg", {})
    portable_build = portable.get("build-verified-umg", {})
    if build.get("portable_step") != "build-verified-umg":
        errors.append("WorkBuddy Editor build maps to the wrong portable step")
    if build.get("depends_on") != ["validate-build-plan"]:
        errors.append("WorkBuddy Editor build bypasses pre-mutation validation")
    if build.get("inputs") != portable_build.get("inputs"):
        errors.append("WorkBuddy Editor build inputs differ from orchestration")
    if build.get("outputs") != portable_build.get("outputs"):
        errors.append("WorkBuddy final Bundle output differs from orchestration")
    for field in ("compiled", "saved", "verified", "final_bundle_validated"):
        if result_const(build, field) is not True:
            errors.append(f"WorkBuddy Editor build does not prove {field}")
    if result_const(build, "artifact_path") != contract.get(
        "protectedBuildProjection", {}
    ).get("finalBundle"):
        errors.append("WorkBuddy Editor build returns the wrong final Bundle")

    readback = steps.get("post-save-unreal-readback", {})
    if readback.get("depends_on") != ["build-verified-umg"]:
        errors.append("WorkBuddy readback must follow the verified Editor build")
    if readback.get("portable_step") != "post-save-unreal-readback":
        errors.append("WorkBuddy readback maps to the wrong portable step")
    if readback.get("inputs") != portable.get("post-save-unreal-readback", {}).get("inputs"):
        errors.append("WorkBuddy readback inputs differ from orchestration")
    if readback.get("outputs") != portable.get("post-save-unreal-readback", {}).get("outputs"):
        errors.append("WorkBuddy readback outputs differ from orchestration")
    if result_const(readback, "artifact_path") != "unreal-widget-readback.json":
        errors.append("WorkBuddy readback returns the wrong artifact")
    if result_const(readback, "valid") is not True:
        errors.append("WorkBuddy readback receipt must be valid")

    present = steps.get("present-built-result", {})
    if present.get("depends_on") != ["post-save-unreal-readback"]:
        errors.append("WorkBuddy presentation must follow fresh readback")
    if present.get("portable_step") != "present-build-results":
        errors.append("WorkBuddy presentation maps to the wrong portable step")
    if present.get("inputs") != portable.get("present-build-results", {}).get("inputs"):
        errors.append("WorkBuddy presentation inputs differ from orchestration")
    if present.get("outputs") != portable.get("present-build-results", {}).get("outputs"):
        errors.append("WorkBuddy presentation output differs from orchestration")
    if result_const(present, "artifact_path") != "status/build-results.presented.json":
        errors.append("WorkBuddy presentation must return its bound status artifact")

    gate = steps.get("build-user-gate", {})
    if gate.get("human_gate") is not True or gate.get("must_stop") is not True:
        errors.append("WorkBuddy build gate must be an explicit hard stop")
    if gate.get("depends_on") != ["present-built-result"]:
        errors.append("WorkBuddy build gate must occur after result presentation")
    if gate.get("portable_step") != "build-results-confirmation":
        errors.append("WorkBuddy build gate maps to the wrong orchestration gate")
    portable_gate = portable.get("build-results-confirmation", {})
    if gate.get("inputs") != portable_gate.get("inputs"):
        errors.append("WorkBuddy build gate inputs differ from orchestration")
    if gate.get("outputs") != portable_gate.get("outputs"):
        errors.append("WorkBuddy build gate outputs differ from orchestration")
    if gate.get("decision_artifact") != "ui-build-acceptance.json":
        errors.append("WorkBuddy build gate has the wrong decision artifact")
    return errors


def validate_workbuddy_analysis_proofs(
    workbuddy: dict[str, Any],
    workflow: dict[str, Any],
) -> list[str]:
    """Require machine-readable proof of every analysis projection/barrier."""

    errors: list[str] = []
    if workbuddy.get("role_retry_policy") != EXPECTED_WORKBUDDY_ROLE_RETRY_POLICY:
        errors.append(
            "WorkBuddy role retry policy must terminate then fresh-delegate the same packet"
        )
    step_items = workbuddy.get("steps", [])
    repeated_steps = duplicate_ids(step_items)
    if repeated_steps:
        errors.append(f"WorkBuddy requirement workflow repeats step ids: {sorted(repeated_steps)}")
    for item in step_items:
        errors.extend(
            validate_closed_result_schema(
                item, f"WorkBuddy requirement step {item.get('id')!r}"
            )
        )
    steps = by_id(step_items)
    portable = workflow_steps(workflow)

    expected_step_order = list(EXPECTED_WORKBUDDY_DEPENDENCIES)
    if [item.get("id") for item in step_items] != expected_step_order:
        errors.append("WorkBuddy requirement workflow has a non-closed ordered step set")
    portable_analysis_ids = {item.get("id") for item in workflow.get("steps", [])}
    mapped_portable_ids = [item.get("portable_step") for item in step_items]
    if (
        len(mapped_portable_ids) != len(set(mapped_portable_ids))
        or set(mapped_portable_ids) != portable_analysis_ids
    ):
        errors.append("WorkBuddy requirement steps are not a one-to-one portable mapping")
    for item in step_items:
        workbuddy_id = item.get("id")
        portable_step = portable.get(item.get("portable_step"), {})
        portable_execution = portable_step.get("execution")
        expected_execution = "subagent" if portable_execution == "worker" else "main"
        if item.get("execution") != expected_execution:
            errors.append(f"WorkBuddy {workbuddy_id} has the wrong execution owner")
        expected_inputs = (
            portable_step.get("agentInputs")
            if expected_execution == "subagent"
            else portable_step.get("inputs")
        )
        if item.get("inputs") != expected_inputs:
            errors.append(f"WorkBuddy {workbuddy_id} inputs differ from orchestration")
        if item.get("outputs") != portable_step.get("outputs"):
            errors.append(f"WorkBuddy {workbuddy_id} outputs differ from orchestration")
        if expected_execution == "main" and item.get("agent_role") is not None:
            errors.append(f"WorkBuddy {workbuddy_id} must not declare an Agent role")

    if result_const(steps.get("validate-request", {}), "prepared_outputs") != portable.get(
        "validate-packet", {}
    ).get("outputs"):
        errors.append("WorkBuddy validate-request does not declare the exact packet batch")
    if result_const(steps.get("validate-discovery", {}), "validated_roles") != [
        "visual-structure",
        "text-requirements",
        "project-pattern",
    ]:
        errors.append("WorkBuddy discovery barrier does not prove the exact three roles")
    if result_const(steps.get("validate-discovery", {}), "artifact_path") != (
        "status/discovery-findings.validated.json"
    ):
        errors.append("WorkBuddy discovery barrier does not return its status artifact")
    if result_const(steps.get("normalize-context", {}), "artifact_paths") != portable.get(
        "normalize-identities", {}
    ).get("outputs"):
        errors.append("WorkBuddy normalizer does not declare the exact role projections and packets")
    if result_const(steps.get("validate-focused", {}), "validated_roles") != [
        "state-modeling",
        "data-adaptation",
        "asset-decomposition",
    ]:
        errors.append("WorkBuddy focused barrier does not prove the exact three roles")
    if result_const(steps.get("validate-focused", {}), "artifact_path") != (
        "status/focused-findings.validated.json"
    ):
        errors.append("WorkBuddy focused barrier does not return its status artifact")
    if result_const(steps.get("synthesize-draft", {}), "artifact_paths") != portable.get(
        "synthesize-draft", {}
    ).get("outputs"):
        errors.append("WorkBuddy synthesizer does not declare the exact Review View batch")
    if result_const(steps.get("adjudicate-review", {}), "validated_review_roles") != [
        "state-visual-review",
        "schema-feasibility-review",
        "coverage-review",
    ]:
        errors.append("WorkBuddy adjudication does not prove all three review roles")
    strict_expected = {
        "artifact_path": "status/ui-requirement.strict-valid.json",
        "valid": True,
        "linked_findings_count": 9,
        "linked_packet_count": 9,
        "role_context_count": 6,
        "review_view_count": 3,
        "draft_revalidated": True,
    }
    if any(
        result_const(steps.get("strict-requirement-validation", {}), field) != value
        for field, value in strict_expected.items()
    ):
        errors.append("WorkBuddy strict barrier does not prove the complete v2 sidecar set")
    return errors


def validate_prompt_contract_text(relative: Path, text: str) -> list[str]:
    """Catch prose that reopens history or weakens full-authority validation."""

    errors: list[str] = []
    normalized = relative.as_posix()
    collapsed = " ".join(text.split())

    def action_is_negated(match: re.Match[str]) -> bool:
        """Recognize an explicit local prohibition on the matched action."""

        action_start = match.start("action")
        prefix = collapsed[max(0, action_start - 48) : action_start]
        sentence_boundary = max(
            prefix.rfind("."), prefix.rfind("!"), prefix.rfind("?")
        )
        if sentence_boundary >= 0:
            prefix = prefix[sentence_boundary + 1 :]
        return bool(
            re.search(
                r"(?:\bnever\b|\bcannot\b|\bcan't\b|"
                r"\b(?:do|does|must|should|may|will)\s+not\b)"
                r"(?:\s+[a-z-]+){0,3}\s*$",
                prefix,
                flags=re.IGNORECASE,
            )
        )

    contradictory_retry_patterns = (
        r"\b(?:resume|reuse|continue)\s+(?:the\s+)?(?:same|existing|failed)\s+(?:task|agent|delegation)\b",
        r"\b(?:send|append)\s+(?:clarified|corrective|extra)\s+(?:instructions|prompt|prose|text)\b",
        r"\bcorrect\s+(?:the\s+)?(?:active|same|existing|failed)\s+(?:task|agent|delegation)\b",
    )
    for pattern in contradictory_retry_patterns:
        for match in re.finditer(pattern, collapsed, flags=re.IGNORECASE):
            prefix = collapsed[max(0, match.start() - 24) : match.start()]
            if re.search(
                r"(?:never|do not|must not|cannot|can't)\s*$",
                prefix,
                flags=re.IGNORECASE,
            ):
                continue
            errors.append(
                f"{relative} contains a contradictory history-reusing retry instruction"
            )
            break

    view_term = r"(?:review\s+views?|accepted[- ]build\s+views?|views?)"
    requirement_term = (
        r"(?:(?:complete|full|accepted)\s+(?:accepted\s+)?requirements?"
        r"|ui-requirement(?:\.json)?)"
    )
    validator_term = (
        r"(?:final\s+bundle\s+validators?|bundle\s+validators?|"
        r"full\s+validators?|full\s+requirement\s+validators?|"
        r"full\s+requirement\s+validation|complete\s+requirement\s+validation)"
    )
    authority_bypass_patterns = (
        (
            "a View replacing the complete Requirement",
            rf"\b{view_term}\b[^.!?]{{0,100}}\b(?P<action>replaces?|supersedes?|substitutes?\s+for)\b"
            rf"[^.!?]{{0,80}}\b{requirement_term}\b",
            True,
        ),
        (
            "a View used instead of the complete Requirement",
            rf"\b(?P<action>use|treat)\b[^.!?]{{0,50}}\b{view_term}\b"
            rf"[^.!?]{{0,50}}\b(?:instead\s+of|in\s+place\s+of)\b"
            rf"[^.!?]{{0,50}}\b{requirement_term}\b",
            True,
        ),
        (
            "a full validator restricted to View-only authority",
            rf"\b{validator_term}\b[^.!?]{{0,80}}\b(?P<action>reads?|uses?|consumes?)\b"
            rf"[^.!?]{{0,30}}\bonly\b[^.!?]{{0,30}}\b{view_term}\b",
            True,
        ),
        (
            "skipping or bypassing a full/Bundle validator",
            rf"\b(?P<action>skip|bypass|omit|disable|avoid)\b[^.!?]{{0,80}}\b{validator_term}\b",
            True,
        ),
        (
            "declaring a full/Bundle validator skippable",
            rf"\b{validator_term}\b[^.!?]{{0,60}}\b(?P<action>(?:may|can|should)\s+be\s+"
            rf"(?:skipped|bypassed|omitted)|(?:is|are)\s+(?:optional|unnecessary))\b",
            False,
        ),
        (
            "forbidding execution of a full/Bundle validator",
            rf"\b(?P<action>(?:do|does|must|should)\s+not|never|cannot|can't)\s+"
            rf"(?:run|execute|apply|invoke)\b[^.!?]{{0,80}}\b{validator_term}\b",
            False,
        ),
        (
            "continuing without a full/Bundle validator",
            rf"\b(?P<action>without\s+(?:running|executing|applying|invoking))\b"
            rf"[^.!?]{{0,80}}\b{validator_term}\b",
            False,
        ),
    )
    for label, pattern, honor_negation in authority_bypass_patterns:
        for match in re.finditer(pattern, collapsed, flags=re.IGNORECASE):
            if honor_negation and action_is_negated(match):
                continue
            errors.append(f"{relative} contains contradictory authority prose: {label}")
            break

    if normalized in {"codex/README.md", "README.md"}:
        for forbidden in ("send_message", "followup_task"):
            if forbidden in text:
                errors.append(f"{relative} exposes forbidden history-reuse token {forbidden}")
    if normalized == "codex/README.md":
        if "complete visible prompt is only one validated packet path" not in collapsed:
            errors.append("Codex README does not state the closed packet-only prompt")
        if "extra prose would violate `packet-path-only`" not in collapsed:
            errors.append("Codex README does not forbid a second instruction channel")
        for required in (
            "fresh no-history build-planning",
            "complete visible prompt is only the validated relative path `accepted-build-view.json`",
            "failure, interrupt it and create a new fresh task",
            "pre-mutation evidence before Editor work",
        ):
            if required not in collapsed:
                errors.append(f"Codex README omits protected build rule: {required}")
    if normalized == "hermes/nextgame-ui-portable/SKILL.md":
        for forbidden in (
            "plus one exact output path",
            "Tell the subagent",
            "root agent may execute each role",
        ):
            if forbidden in text:
                errors.append(f"Hermes Skill reopens prompt text via: {forbidden}")
        if "complete visible prompt/input is exactly one" not in collapsed:
            errors.append("Hermes Skill does not state the closed packet-only prompt")
        if "new no-history delegation" not in collapsed:
            errors.append("Hermes Skill does not require a fresh retry")
        if "When no such primitive exists, fail closed" not in collapsed:
            errors.append("Hermes Skill does not fail closed without a fresh-context primitive")
        for required in (
            "fresh no-history build-planning delegation",
            "complete visible prompt/input is exactly the validated `accepted-build-view.json` path",
            "failure terminates that task and starts a new fresh delegation",
            "pre-mutation evidence before Editor work",
            "`dispatchContract`",
            "native `prepare_build.py` v0.2 plans",
            "`orchestration/scripts/build_plan_evidence.py`",
            "`--validate-only`",
            "final Bundle validators still read the complete accepted Requirement as authority",
        ):
            if required not in collapsed:
                errors.append(f"Hermes Skill omits protected build rule: {required}")
    if normalized == "hermes/nextgame-ui-portable/references/artifact-contract.md":
        if "retry its owner" in text:
            errors.append("Hermes artifact contract permits ambiguous task reuse")
        for required in (
            "terminate the old task and start a fresh no-history delegation",
            "Build planning uses a fresh no-history task",
            "binds pre-mutation evidence before Unreal work",
            "`dispatchContract`",
            "native `prepare_build.py` v0.2 plans",
            "`orchestration/scripts/build_plan_evidence.py`",
            "`--validate-only` mode",
            "Requirement remains final Bundle validator authority",
        ):
            if required not in collapsed:
                errors.append(f"Hermes artifact contract omits protected rule: {required}")
    return errors


def validate(root: Path) -> list[str]:
    errors: list[str] = []
    contract_path = root / "adapter-contract.json"
    capability_path = root / "capability-contract.json"
    codex_map_path = root / "codex" / "runtime-map.json"
    workflow_path = root.parent / "orchestration" / "nextgame-ui.requirements.workflow.json"

    try:
        contract = load_json(contract_path)
        capability = load_json(capability_path)
        codex_map = load_json(codex_map_path)
        workflow = load_json(workflow_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [str(exc)]

    errors.extend(validate_contract_against_workflow(contract, workflow))
    errors.extend(validate_codex_runtime_map(codex_map, workflow, contract))

    authority = contract.get("artifactAuthority", {})
    expected_dispatch_artifacts = {
        "no-history role packets",
        "role context projections",
        "review views",
        "accepted build view",
    }
    if set(authority.get("validatedDispatchArtifacts", [])) != expected_dispatch_artifacts:
        errors.append("adapter contract misclassifies the protected dispatch artifacts")
    semantic_authorities = set(authority.get("authoritative", []))
    if expected_dispatch_artifacts.intersection(semantic_authorities):
        errors.append("projected dispatch artifacts must not be semantic authorities")
    non_authoritative = set(authority.get("nonAuthoritative", []))
    for phrase in ("review views", "accepted build view"):
        if phrase not in non_authoritative:
            errors.append(f"adapter authority boundary omits non-authoritative {phrase}")

    roles = contract.get("roles", [])
    role_names = {item.get("role") for item in roles}
    role_records = {item.get("role"): item for item in roles}
    if len(roles) != len(EXPECTED_ROLES) or len(role_names) != len(roles):
        errors.append("adapter-contract must contain exactly nine unique role records")
    if role_names != EXPECTED_ROLES:
        errors.append(
            "adapter-contract roles differ from the exact nine-role contract: "
            f"{sorted(role_names)}"
        )

    outputs = [item.get("output") for item in roles]
    if len(outputs) != len(set(outputs)):
        errors.append("each role must own a unique findings output")
    if any(not isinstance(path, str) or not path.startswith("findings/") for path in outputs):
        errors.append("every role output must be a relative findings/ path")

    packets = [item.get("packetPath") for item in roles]
    if len(packets) != len(set(packets)):
        errors.append("each role must have a unique role packet")
    for role, record in role_records.items():
        if record.get("packetPath") != _expected_packet(role):
            errors.append(f"{role} has a non-canonical packet path")

    contract_stage_items = contract.get("stages", [])
    if duplicate_ids(contract_stage_items):
        errors.append("adapter-contract contains duplicate stage ids")
    stages = by_id(contract_stage_items)
    for stage_id, expected_roles in EXPECTED_GROUPS.items():
        stage = stages.get(stage_id)
        if stage is None:
            errors.append(f"missing contract stage {stage_id}")
            continue
        if set(stage.get("roles", [])) != expected_roles:
            errors.append(f"{stage_id} does not contain its exact three roles")
        if stage.get("parallel") is not True:
            errors.append(f"{stage_id} must retain parallel-group semantics")

    expected_stage_dependencies = {
        "discovery": {"validate-request"},
        "validate-discovery": {"discovery"},
        "normalize-context": {"validate-discovery"},
        "focused-analysis": {"normalize-context"},
        "validate-focused": {"focused-analysis"},
        "synthesize-draft": {"validate-focused"},
        "independent-review": {"synthesize-draft"},
        "adjudicate-review": {"independent-review"},
        "strict-requirement-validation": {"adjudicate-review"},
        "requirement-review-gate": {"strict-requirement-validation"},
    }
    for stage_id, expected in expected_stage_dependencies.items():
        actual = set(stages.get(stage_id, {}).get("dependsOn", []))
        if actual != expected:
            errors.append(
                f"{stage_id} dependencies are {sorted(actual)}, expected {sorted(expected)}"
            )

    _, gates, _ = inspect_human_gates(contract)
    for gate_id, gate in gates.items():
        if gate.get("mustStop") is not True:
            errors.append(f"{gate_id} must be a hard stop")
        if gate.get("decisionActor") != "user":
            errors.append(f"{gate_id} decision actor must remain the user")
        if gate.get("artifactRecorder") != "coordinator":
            errors.append(f"{gate_id} artifact recorder must remain coordinator-owned")
    requirement_gate_stage = stages.get("requirement-review-gate", {})
    if requirement_gate_stage.get("owner") != "user":
        errors.append("requirement-review-gate stage owner must be the user")
    if requirement_gate_stage.get("decisionActor") != "user":
        errors.append("requirement-review-gate decision actor must be the user")
    if requirement_gate_stage.get("artifactRecorder") != "coordinator":
        errors.append("requirement-review-gate artifact recorder must be coordinator")

    required_capabilities = {
        item.get("capability") for item in capability.get("required", [])
    }
    expected_capabilities = {
        "coordinator-ownership",
        "exclusive-artifact-writes",
        "local-schema-validation",
        "sha256",
        "durable-run-root",
        "human-gate-pause",
        "no-history-role-dispatch",
    }
    if capability.get("schemaVersion") != "2.0":
        errors.append("capability contract must use schemaVersion 2.0")
    if required_capabilities != expected_capabilities:
        errors.append("capability preflight is missing a fail-closed requirement")
    if any(item.get("failurePolicy") != "stop" for item in capability.get("required", [])):
        errors.append("every mandatory preflight capability must fail closed")
    delegation_modes = capability.get("delegationModes", {})
    if "parallel-subagents" not in delegation_modes.get("multi-agent", {}).get("requires", []):
        errors.append("multi-agent mode must require parallel subagents")
    for mode_name in ("multi-agent", "sequential-compatibility"):
        if "no-history-role-dispatch" not in delegation_modes.get(mode_name, {}).get("requires", []):
            errors.append(f"{mode_name} must require no-history role dispatch")
    if capability.get("configuration", {}).get("unrealMutationDuringAnalysis") is not False:
        errors.append("analysis preflight must forbid Unreal mutation")

    workbuddy_path = root / "workbuddy" / "nextgame-ui-requirement-analysis.md"
    build_gate_path = root / "workbuddy" / "nextgame-ui-build-acceptance.md"
    try:
        workbuddy, workbuddy_body = load_json_front_matter(workbuddy_path)
        build_gate, build_gate_body = load_json_front_matter(build_gate_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        errors.append(str(exc))
        workbuddy = {}
        workbuddy_body = ""
        build_gate = {}
        build_gate_body = ""

    errors.extend(
        validate_prompt_contract_text(
            Path("workbuddy/nextgame-ui-requirement-analysis.md"),
            workbuddy_body,
        )
    )
    errors.extend(
        validate_prompt_contract_text(
            Path("workbuddy/nextgame-ui-build-acceptance.md"),
            build_gate_body,
        )
    )

    if workbuddy.get("kind") != "workflow" or workbuddy.get("execution") != "main":
        errors.append("WorkBuddy requirement knowledge unit must be a main workflow")
    if workbuddy.get("portable_workflow_id") != workflow.get("workflowId"):
        errors.append("WorkBuddy requirement workflow id differs from orchestration")
    if workbuddy.get("agent_dispatch_policy") != EXPECTED_WORKBUDDY_POLICY:
        errors.append("WorkBuddy must declare the exact no-history packet-only policy")
    if workbuddy.get("role_retry_policy") != EXPECTED_WORKBUDDY_ROLE_RETRY_POLICY:
        errors.append("WorkBuddy role retry policy must terminate then fresh-delegate the same packet")
    errors.extend(validate_workbuddy_build_mapping(build_gate, workflow, contract))

    errors.extend(validate_workbuddy_analysis_proofs(workbuddy, workflow))
    workbuddy_steps = by_id(workbuddy.get("steps", []))
    portable_steps = workflow_steps(workflow)
    delegated_steps = [
        step
        for step in workbuddy_steps.values()
        if step.get("execution") == "subagent"
    ]
    delegated_roles = {step.get("agent_role") for step in delegated_steps}
    if delegated_roles != EXPECTED_ROLES:
        errors.append("WorkBuddy subagent steps do not cover the exact nine roles")
    for step in delegated_steps:
        role = step.get("agent_role")
        role_record = role_records.get(role)
        result_path = (
            step.get("result_schema", {})
            .get("properties", {})
            .get("artifact_path", {})
            .get("const")
        )
        if role_record is None or result_path != role_record.get("output"):
            errors.append(f"WorkBuddy role {role} receipt points at the wrong artifact")
        portable_step_id = step.get("portable_step")
        portable_step = portable_steps.get(portable_step_id, {})
        if portable_step.get("agentRole") != role:
            errors.append(f"WorkBuddy role {role} maps to the wrong portable step")
        if step.get("inputs") != portable_step.get("agentInputs"):
            errors.append(f"WorkBuddy role {role} Agent inputs differ from orchestration")
        if step.get("inputs") != [role_record.get("packetPath")]:
            errors.append(f"WorkBuddy role {role} must receive only its packet")
        if step.get("parallel_group") != portable_step.get("parallelGroup"):
            errors.append(f"WorkBuddy role {role} parallel group differs from orchestration")
        if portable_step.get("outputs") != [result_path]:
            errors.append(f"WorkBuddy role {role} output differs from orchestration")

    for step_id, expected in EXPECTED_WORKBUDDY_DEPENDENCIES.items():
        actual = set(workbuddy_steps.get(step_id, {}).get("depends_on", []))
        if actual != expected:
            errors.append(
                f"WorkBuddy {step_id} dependencies are {sorted(actual)}, "
                f"expected {sorted(expected)}"
            )

    first_gate = workbuddy_steps.get("requirement-user-gate", {})
    if first_gate.get("human_gate") is not True or first_gate.get("must_stop") is not True:
        errors.append("WorkBuddy requirement gate must be an explicit hard stop")
    requirement_gate_contract = gates.get("requirement-review", {})
    if first_gate.get("portable_step") != requirement_gate_contract.get("workflowStep"):
        errors.append("WorkBuddy requirement gate maps to the wrong orchestration gate")
    portable_first_gate = portable_steps.get("requirements-confirmation", {})
    if first_gate.get("inputs") != portable_first_gate.get("inputs"):
        errors.append("WorkBuddy requirement gate inputs differ from orchestration")
    if first_gate.get("outputs") != portable_first_gate.get("outputs"):
        errors.append("WorkBuddy requirement gate outputs differ from orchestration")
    if first_gate.get("decision_artifact") != requirement_gate_contract.get(
        "decisionArtifact"
    ):
        errors.append("WorkBuddy requirement gate has the wrong decision artifact")

    layout = contract.get("artifactLayout", {})
    draft_result = (
        workbuddy_steps.get("synthesize-draft", {})
        .get("result_schema", {})
        .get("properties", {})
        .get("requirement_path", {})
        .get("const")
    )
    if draft_result != layout.get("draftRequirement"):
        errors.append("WorkBuddy draft requirement output differs from orchestration")
    pending_result = (
        workbuddy_steps.get("adjudicate-review", {})
        .get("result_schema", {})
        .get("properties", {})
        .get("requirement_path", {})
        .get("const")
    )
    if pending_result != layout.get("pendingRequirement"):
        errors.append("WorkBuddy pending requirement output differs from orchestration")

    workbuddy_group_roles: dict[str, set[Any]] = {}
    for step in delegated_steps:
        workbuddy_group_roles.setdefault(step.get("parallel_group"), set()).add(
            step.get("agent_role")
        )
    manifest_group_roles = {
        group.get("id"): {
            portable_steps.get(member_id, {}).get("agentRole")
            for member_id in group.get("members", [])
        }
        for group in workflow.get("parallelGroups", [])
    }
    if workbuddy_group_roles != manifest_group_roles:
        errors.append("WorkBuddy parallel groups differ from orchestration")

    build_steps = by_id(build_gate.get("steps", []))
    second_gate = build_steps.get("build-user-gate", {})
    if second_gate.get("human_gate") is not True or second_gate.get("must_stop") is not True:
        errors.append("WorkBuddy build gate must be an explicit hard stop")
    if set(second_gate.get("depends_on", [])) != {"present-built-result"}:
        errors.append("WorkBuddy build gate must occur after result presentation")
    build_gate_contract = gates.get("build-acceptance", {})
    if second_gate.get("portable_step") != build_gate_contract.get("workflowStep"):
        errors.append("WorkBuddy build gate maps to the wrong orchestration gate")
    if second_gate.get("decision_artifact") != build_gate_contract.get(
        "decisionArtifact"
    ):
        errors.append("WorkBuddy build gate has the wrong decision artifact")

    if "agent-inputs/" not in workbuddy_body or "review-views/" not in workbuddy_body:
        errors.append("WorkBuddy instructions omit packet/View isolation")
    workbuddy_body_collapsed = " ".join(workbuddy_body.split())
    if "terminate the old task and start a fresh no-history subagent" not in workbuddy_body_collapsed:
        errors.append("WorkBuddy analysis retry does not require a fresh task")
    if "accepted-build-view.json" not in workbuddy_body:
        errors.append("WorkBuddy instructions omit the Accepted Build View boundary")
    build_gate_body_collapsed = " ".join(build_gate_body.split())
    for required in (
        "fresh no-history build task",
        "must not connect to or mutate Unreal",
        "status/ui-build-plan.pre-mutation-valid.json",
        "status/build-results.presented.json",
    ):
        if required not in build_gate_body_collapsed:
            errors.append(f"WorkBuddy build instructions omit protected rule: {required}")

    for relative in (
        Path("codex/README.md"),
        Path("hermes/nextgame-ui-portable/SKILL.md"),
        Path("hermes/nextgame-ui-portable/references/artifact-contract.md"),
    ):
        path = root / relative
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(str(exc))
            continue
        errors.extend(validate_prompt_contract_text(relative, text))
        missing = EXPECTED_ROLES.difference(role for role in EXPECTED_ROLES if role in text)
        if missing:
            errors.append(f"{relative} omits roles: {sorted(missing)}")
        for required_term in (
            "agent-inputs/",
            "contexts/normalized-context.json",
            "ui-requirement.draft.json",
            "review-views/",
            "accepted-build-view.json",
        ):
            if required_term not in text:
                errors.append(f"{relative} omits protected term {required_term}")

    hermes_skill = root / "hermes" / "nextgame-ui-portable" / "SKILL.md"
    try:
        skill_text = hermes_skill.read_text(encoding="utf-8")
        skill_header_end = skill_text.find("\n---\n", 4)
        header_lines = [
            line.split(":", 1)[0].strip()
            for line in skill_text[4:skill_header_end].splitlines()
            if ":" in line
        ]
        if set(header_lines) != {"name", "description"}:
            errors.append("Hermes SKILL.md front matter must contain only name and description")
    except OSError as exc:
        errors.append(str(exc))

    adapters_readme_path = root / "README.md"
    try:
        adapters_readme_text = adapters_readme_path.read_text(encoding="utf-8")
        errors.extend(validate_prompt_contract_text(Path("README.md"), adapters_readme_text))
    except OSError as exc:
        errors.append(str(exc))

    local_path_patterns = (
        re.compile(r"[A-Za-z]:\\"),
        re.compile(r"[A-Za-z]:/(?!/)"),
        re.compile(r"(?:^|[\s(\"'])/(?:Users|home)/[^/\s]+", re.MULTILINE),
        re.compile(r"\\\\[^\s]+\\[^\s]+"),
    )
    obsolete_review_context = "contexts/" + "review-context.json"
    for path in sorted(root.rglob("*")):
        if path.suffix not in {".md", ".json"}:
            continue
        text = path.read_text(encoding="utf-8")
        if obsolete_review_context in text:
            errors.append(f"obsolete review context found in {path.relative_to(root)}")
        if any(pattern.search(text) for pattern in local_path_patterns):
            errors.append(f"machine-specific absolute path found in {path.relative_to(root)}")

    return errors


def main() -> int:
    root = Path(__file__).resolve().parent
    errors = validate(root)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(
        "Adapter validation passed: contract v2, 9 packet-only no-history roles, "
        "6 role contexts, 3 Review Views, Accepted Build View, 3 parallel groups, "
        "2 user gates, 0 path leaks."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
