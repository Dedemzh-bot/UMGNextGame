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

EXPECTED_GROUPS = {
    "discovery": {
        "visual-structure",
        "text-requirements",
        "project-pattern",
    },
    "focused-analysis": {
        "state-modeling",
        "data-adaptation",
        "asset-decomposition",
    },
    "independent-review": {
        "state-visual-review",
        "schema-feasibility-review",
        "coverage-review",
    },
}

EXPECTED_WORKBUDDY_DEPENDENCIES = {
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


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as stream:
        data = json.load(stream)
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
    header = json.loads(text[4:closing])
    if not isinstance(header, dict):
        raise ValueError(f"{path}: front matter must be a mapping")
    return header, text[closing + 5 :]


def by_id(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {item.get("id", ""): item for item in items}


def workflow_steps(workflow: dict[str, Any]) -> dict[str, dict[str, Any]]:
    steps = list(workflow.get("steps", []))
    steps.extend(workflow.get("protectedContinuation", {}).get("steps", []))
    return by_id(steps)


def validate_contract_against_workflow(
    contract: dict[str, Any], workflow: dict[str, Any]
) -> list[str]:
    """Compare adapter invariants to the vendor-neutral workflow source."""

    errors: list[str] = []
    contract_roles = {item.get("role"): item for item in contract.get("roles", [])}
    contract_role_names = set(contract_roles)
    workflow_role_names = set(workflow.get("requiredFindingsRoles", []))
    if contract.get("workflowId") != workflow.get("workflowId"):
        errors.append("adapter workflowId differs from the orchestration workflowId")
    if workflow_role_names != contract_role_names:
        errors.append("adapter and orchestration findings role sets differ")

    steps = workflow_steps(workflow)
    workflow_role_steps = {
        step.get("agentRole"): step
        for step in steps.values()
        if step.get("agentRole") is not None
    }
    if set(workflow_role_steps) != contract_role_names:
        errors.append("orchestration worker steps do not map one-to-one to adapter roles")

    for role, record in contract_roles.items():
        step = workflow_role_steps.get(role, {})
        if step.get("outputs") != [record.get("output")]:
            errors.append(f"{role} output differs between adapter and orchestration")
        actual_contexts = {
            path
            for path in step.get("inputs", [])
            if isinstance(path, str) and path.startswith("contexts/")
        }
        expected_context = record.get("contextPath")
        expected_contexts = {expected_context} if expected_context else set()
        if actual_contexts != expected_contexts:
            errors.append(f"{role} context path differs between adapter and orchestration")
        extra_input = record.get("additionalReadOnlyInput")
        if extra_input and extra_input not in step.get("inputs", []):
            errors.append(f"{role} is missing its read-only draft requirement input")
        if step.get("parallelGroup") != record.get("stage"):
            errors.append(f"{role} parallel group differs between adapter and orchestration")

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
    requirement_outputs = {
        "synthesize-draft": layout.get("draftRequirement"),
        "finalize-review-resolutions": layout.get("pendingRequirement"),
    }
    for step_id, expected_output in requirement_outputs.items():
        if steps.get(step_id, {}).get("outputs") != [expected_output]:
            errors.append(f"{step_id} requirement output differs from adapter contract")
    if steps.get("normalize-identities", {}).get("outputs") != [
        layout.get("normalizedContext")
    ]:
        errors.append("normalized context output differs from adapter contract")

    contract_gates = {
        gate.get("workflowStep"): gate for gate in contract.get("humanGates", [])
    }
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
        if gate_data.get("manualAdvance") is not True:
            errors.append(f"workflow gate {step_id} must require manual advance")
        if gate_data.get("decisionArtifact") != gate_contract.get("decisionArtifact"):
            errors.append(f"workflow gate {step_id} decision artifact differs")
        if set(gate_step.get("dependsOn", [])) != {gate_contract.get("afterStep")}:
            errors.append(f"workflow gate {step_id} predecessor differs")
    if layout.get("acceptedRequirement") != contract_gates.get(
        "requirements-confirmation", {}
    ).get("decisionArtifact"):
        errors.append("accepted requirement output differs from the first gate artifact")

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
    if codex_map.get("workflowId") != workflow.get("workflowId"):
        errors.append("Codex runtime map workflowId differs from orchestration")

    roles = contract.get("roles", [])
    role_names = {item.get("role") for item in roles}
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

    stages = by_id(contract.get("stages", []))
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

    gates = {gate.get("id"): gate for gate in contract.get("humanGates", [])}
    if set(gates) != {"requirement-review", "build-acceptance"}:
        errors.append("contract must contain exactly the requirement and build user gates")
    for gate_id, gate in gates.items():
        if gate.get("mustStop") is not True:
            errors.append(f"{gate_id} must be a hard stop")

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
    }
    if required_capabilities != expected_capabilities:
        errors.append("capability preflight is missing a fail-closed requirement")
    if any(item.get("failurePolicy") != "stop" for item in capability.get("required", [])):
        errors.append("every mandatory preflight capability must fail closed")
    delegation_modes = capability.get("delegationModes", {})
    if "parallel-subagents" not in delegation_modes.get("multi-agent", {}).get("requires", []):
        errors.append("multi-agent mode must require parallel subagents")
    if capability.get("configuration", {}).get("unrealMutationDuringAnalysis") is not False:
        errors.append("analysis preflight must forbid Unreal mutation")

    workbuddy_path = root / "workbuddy" / "nextgame-ui-requirement-analysis.md"
    build_gate_path = root / "workbuddy" / "nextgame-ui-build-acceptance.md"
    try:
        workbuddy, _ = load_json_front_matter(workbuddy_path)
        build_gate, _ = load_json_front_matter(build_gate_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        errors.append(str(exc))
        workbuddy = {}
        build_gate = {}

    if workbuddy.get("kind") != "workflow" or workbuddy.get("execution") != "main":
        errors.append("WorkBuddy requirement knowledge unit must be a main workflow")
    if workbuddy.get("portable_workflow_id") != workflow.get("workflowId"):
        errors.append("WorkBuddy requirement workflow id differs from orchestration")
    if build_gate.get("portable_workflow_id") != workflow.get("workflowId"):
        errors.append("WorkBuddy build-gate workflow id differs from orchestration")
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
        role_record = next((item for item in roles if item.get("role") == role), None)
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
        if step.get("inputs") != portable_step.get("inputs"):
            errors.append(f"WorkBuddy role {role} inputs differ from orchestration")
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

    for relative in (
        Path("codex/README.md"),
        Path("hermes/nextgame-ui-portable/SKILL.md"),
    ):
        path = root / relative
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(str(exc))
            continue
        missing = EXPECTED_ROLES.difference(role for role in EXPECTED_ROLES if role in text)
        if missing:
            errors.append(f"{relative} omits roles: {sorted(missing)}")
        if "contexts/normalized-context.json" not in text:
            errors.append(f"{relative} omits the shared normalized context path")
        if "ui-requirement.draft.json" not in text:
            errors.append(f"{relative} omits the reviewers' read-only draft input")

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
        "Adapter validation passed: manifest-aligned 9 roles, outputs, contexts, "
        "3 parallel groups, requirement artifacts, 2 user gates, 0 path leaks."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
