---
{
  "kind": "workflow",
  "workflow_name": "nextgame-ui-requirement-analysis",
  "portable_workflow_id": "nextgame-ui-requirements",
  "description": "Run the nine-role NextGame UI requirement analysis through validated file artifacts, then stop at the first user gate before Unreal mutation.",
  "execution": "main",
  "params_schema": {
    "type": "object",
    "required": ["run_root", "request_packet", "plugin_root"],
    "properties": {
      "run_root": {"type": "string", "minLength": 1, "description": "Configured durable run directory; no machine-specific default is assumed."},
      "request_packet": {"type": "string", "description": "Validated RequestPacket JSON path."},
      "plugin_root": {"type": "string", "description": "Installed nextgame-ui plugin root containing validators."}
    },
    "additionalProperties": false
  },
  "steps": [
    {
      "id": "validate-request",
      "name": "Validate RequestPacket",
      "step_type": "code",
      "execution": "main",
      "portable_step": "validate-packet",
      "depends_on": [],
      "result_schema": {"type": "object", "required": ["valid"], "properties": {"valid": {"const": true}}, "additionalProperties": false}
    },
    {
      "id": "discover-visual",
      "name": "Visual structure discovery",
      "step_type": "reasoning",
      "execution": "subagent",
      "portable_step": "discover-visual-structure",
      "parallel_group": "discovery",
      "depends_on": ["validate-request"],
      "agent_role": "visual-structure",
      "inputs": ["request-packet.json"],
      "result_schema": {"type": "object", "required": ["artifact_path", "validation_ready"], "properties": {"artifact_path": {"const": "findings/visual-structure.json"}, "validation_ready": {"type": "boolean"}}, "additionalProperties": false}
    },
    {
      "id": "discover-text",
      "name": "Written requirements discovery",
      "step_type": "reasoning",
      "execution": "subagent",
      "portable_step": "discover-text-requirements",
      "parallel_group": "discovery",
      "depends_on": ["validate-request"],
      "agent_role": "text-requirements",
      "inputs": ["request-packet.json"],
      "result_schema": {"type": "object", "required": ["artifact_path", "validation_ready"], "properties": {"artifact_path": {"const": "findings/text-requirements.json"}, "validation_ready": {"type": "boolean"}}, "additionalProperties": false}
    },
    {
      "id": "discover-project",
      "name": "Project pattern discovery",
      "step_type": "reasoning",
      "execution": "subagent",
      "portable_step": "discover-project-pattern",
      "parallel_group": "discovery",
      "depends_on": ["validate-request"],
      "agent_role": "project-pattern",
      "inputs": ["request-packet.json"],
      "result_schema": {"type": "object", "required": ["artifact_path", "validation_ready"], "properties": {"artifact_path": {"const": "findings/project-pattern.json"}, "validation_ready": {"type": "boolean"}}, "additionalProperties": false}
    },
    {
      "id": "validate-discovery",
      "name": "Validate discovery files",
      "step_type": "code",
      "execution": "main",
      "portable_step": "validate-discovery",
      "depends_on": ["discover-visual", "discover-text", "discover-project"],
      "result_schema": {"type": "object", "required": ["validated_roles"], "properties": {"validated_roles": {"type": "array", "minItems": 3, "maxItems": 3, "uniqueItems": true}}, "additionalProperties": false}
    },
    {
      "id": "normalize-context",
      "name": "Normalize canonical identities",
      "step_type": "reasoning",
      "execution": "main",
      "portable_step": "normalize-identities",
      "depends_on": ["validate-discovery"],
      "result_schema": {"type": "object", "required": ["artifact_path"], "properties": {"artifact_path": {"const": "contexts/normalized-context.json"}}, "additionalProperties": false}
    },
    {
      "id": "analyze-state",
      "name": "State modeling",
      "step_type": "reasoning",
      "execution": "subagent",
      "portable_step": "analyze-state-modeling",
      "parallel_group": "focused-analysis",
      "depends_on": ["normalize-context"],
      "agent_role": "state-modeling",
      "inputs": ["request-packet.json", "contexts/normalized-context.json"],
      "result_schema": {"type": "object", "required": ["artifact_path", "validation_ready"], "properties": {"artifact_path": {"const": "findings/state-modeling.json"}, "validation_ready": {"type": "boolean"}}, "additionalProperties": false}
    },
    {
      "id": "analyze-adaptation",
      "name": "Data and adaptation analysis",
      "step_type": "reasoning",
      "execution": "subagent",
      "portable_step": "analyze-data-adaptation",
      "parallel_group": "focused-analysis",
      "depends_on": ["normalize-context"],
      "agent_role": "data-adaptation",
      "inputs": ["request-packet.json", "contexts/normalized-context.json"],
      "result_schema": {"type": "object", "required": ["artifact_path", "validation_ready"], "properties": {"artifact_path": {"const": "findings/data-adaptation.json"}, "validation_ready": {"type": "boolean"}}, "additionalProperties": false}
    },
    {
      "id": "analyze-assets",
      "name": "Asset decomposition analysis",
      "step_type": "reasoning",
      "execution": "subagent",
      "portable_step": "analyze-asset-decomposition",
      "parallel_group": "focused-analysis",
      "depends_on": ["normalize-context"],
      "agent_role": "asset-decomposition",
      "inputs": ["request-packet.json", "contexts/normalized-context.json"],
      "result_schema": {"type": "object", "required": ["artifact_path", "validation_ready"], "properties": {"artifact_path": {"const": "findings/asset-decomposition.json"}, "validation_ready": {"type": "boolean"}}, "additionalProperties": false}
    },
    {
      "id": "validate-focused",
      "name": "Validate focused files",
      "step_type": "code",
      "execution": "main",
      "portable_step": "validate-focused",
      "depends_on": ["analyze-state", "analyze-adaptation", "analyze-assets"],
      "result_schema": {"type": "object", "required": ["validated_roles"], "properties": {"validated_roles": {"type": "array", "minItems": 3, "maxItems": 3, "uniqueItems": true}}, "additionalProperties": false}
    },
    {
      "id": "synthesize-draft",
      "name": "Synthesize authoritative draft",
      "step_type": "reasoning",
      "execution": "main",
      "portable_step": "synthesize-draft",
      "depends_on": ["validate-focused"],
      "result_schema": {"type": "object", "required": ["requirement_path"], "properties": {"requirement_path": {"const": "ui-requirement.draft.json"}}, "additionalProperties": false}
    },
    {
      "id": "review-state-visual",
      "name": "State and visual adversarial review",
      "step_type": "reasoning",
      "execution": "subagent",
      "portable_step": "review-state-visual",
      "parallel_group": "independent-review",
      "depends_on": ["synthesize-draft"],
      "agent_role": "state-visual-review",
      "inputs": ["request-packet.json", "contexts/normalized-context.json", "ui-requirement.draft.json"],
      "result_schema": {"type": "object", "required": ["artifact_path", "validation_ready"], "properties": {"artifact_path": {"const": "findings/state-visual-review.json"}, "validation_ready": {"type": "boolean"}}, "additionalProperties": false}
    },
    {
      "id": "review-schema",
      "name": "Schema and feasibility review",
      "step_type": "reasoning",
      "execution": "subagent",
      "portable_step": "review-schema-feasibility",
      "parallel_group": "independent-review",
      "depends_on": ["synthesize-draft"],
      "agent_role": "schema-feasibility-review",
      "inputs": ["request-packet.json", "contexts/normalized-context.json", "ui-requirement.draft.json"],
      "result_schema": {"type": "object", "required": ["artifact_path", "validation_ready"], "properties": {"artifact_path": {"const": "findings/schema-feasibility-review.json"}, "validation_ready": {"type": "boolean"}}, "additionalProperties": false}
    },
    {
      "id": "review-coverage",
      "name": "Requirement coverage review",
      "step_type": "reasoning",
      "execution": "subagent",
      "portable_step": "review-coverage",
      "parallel_group": "independent-review",
      "depends_on": ["synthesize-draft"],
      "agent_role": "coverage-review",
      "inputs": ["request-packet.json", "contexts/normalized-context.json", "ui-requirement.draft.json"],
      "result_schema": {"type": "object", "required": ["artifact_path", "validation_ready"], "properties": {"artifact_path": {"const": "findings/coverage-review.json"}, "validation_ready": {"type": "boolean"}}, "additionalProperties": false}
    },
    {
      "id": "adjudicate-review",
      "name": "Adjudicate all review findings",
      "step_type": "reasoning",
      "execution": "main",
      "portable_step": "finalize-review-resolutions",
      "depends_on": ["review-state-visual", "review-schema", "review-coverage"],
      "result_schema": {"type": "object", "required": ["requirement_path", "all_reviews_resolved"], "properties": {"requirement_path": {"const": "ui-requirement.pending.json"}, "all_reviews_resolved": {"const": true}}, "additionalProperties": false}
    },
    {
      "id": "strict-requirement-validation",
      "name": "Strictly validate requirement and linked files",
      "step_type": "code",
      "execution": "main",
      "portable_step": "strict-validate-requirement",
      "depends_on": ["adjudicate-review"],
      "result_schema": {"type": "object", "required": ["valid", "linked_findings_count"], "properties": {"valid": {"const": true}, "linked_findings_count": {"const": 9}}, "additionalProperties": false}
    },
    {
      "id": "requirement-user-gate",
      "name": "Present requirement and stop for user review",
      "step_type": "reasoning",
      "execution": "main",
      "portable_step": "requirements-confirmation",
      "depends_on": ["strict-requirement-validation"],
      "human_gate": true,
      "must_stop": true,
      "decision_artifact": "ui-requirement.json",
      "result_schema": {"type": "object", "required": ["presented", "stopped_before_unreal_mutation"], "properties": {"presented": {"const": true}, "stopped_before_unreal_mutation": {"const": true}}, "additionalProperties": false}
    }
  ]
}
---

# NextGame UI requirement analysis

The main WorkBuddy agent is the coordinator. A subagent step may return a small
receipt matching its `result_schema`, but that receipt is never the requirement
evidence. The exact JSON file and the NextGame UI validator are authoritative.

Before the first step, fail closed unless the runtime has a durable configured
run root, exclusive findings ownership, local validator and SHA-256 support,
coordinator-owned synthesis, and real pauses for both user gates. The advertised
multi-agent mode additionally requires parallel subagents and isolated role
inputs. An isolated sequential fallback may preserve compatibility, but must be
reported as such and not as a parallel multi-agent run. MCP endpoints and output
roots are runtime inputs, never hard-coded into this knowledge unit.

## validate-request

Validate the RequestPacket with the installed plugin. Create the run directory
and isolated role inputs only after validation succeeds. Do not mutate Unreal.

## discover-visual

Read image sources only. Follow the `visual-structure` role in the installed
analysis skill and write only `findings/visual-structure.json`.

## discover-text

Read user-statement sources only. Follow the `text-requirements` role and write
only `findings/text-requirements.json`.

## discover-project

Read allowed project rules and asset evidence only. Follow the `project-pattern`
role and write only `findings/project-pattern.json`. Project evidence is read-only.

## validate-discovery

Wait for all three discovery dependencies. Run the findings validator separately
on each exact file against the exact RequestPacket. Retry only the failing owner.

## normalize-context

The main agent assigns canonical identities, records alias/discard coverage, and
writes `contexts/normalized-context.json`. This is not an AgentFindings role.

## analyze-state

Follow the `state-modeling` role against the normalized context and write only
`findings/state-modeling.json`.

## analyze-adaptation

Follow the `data-adaptation` role against the normalized context and write only
`findings/data-adaptation.json`.

## analyze-assets

Follow the `asset-decomposition` role against the normalized context and write
only `findings/asset-decomposition.json`.

## validate-focused

Wait for all focused dependencies. Validate each file with both the RequestPacket
and `contexts/normalized-context.json`.

## synthesize-draft

The main agent alone writes `ui-requirement.draft.json`. Do not infer
implementation from receipt text.

## review-state-visual

Follow `state-visual-review` against `contexts/normalized-context.json`; also read
`ui-requirement.draft.json` without modifying it. Write only this role's findings.

## review-schema

Follow `schema-feasibility-review` against `contexts/normalized-context.json`;
also read `ui-requirement.draft.json` without modifying it. Write only this
role's findings.

## review-coverage

Follow `coverage-review` against `contexts/normalized-context.json`; also read
`ui-requirement.draft.json` without modifying it. Write only this role's findings.

## adjudicate-review

Wait for and validate all three review files. The same main synthesizer records a
resolution for every review finding and revises the authoritative requirement.
Write the adjudicated result to `ui-requirement.pending.json`.

## strict-requirement-validation

Run strict requirement validation with linked-file checking. It must load and
validate the exact nine findings and their contexts, not merely compare receipts
or hashes. Validate `ui-requirement.pending.json`; the accepted
`ui-requirement.json` does not exist until the user gate is completed.

## requirement-user-gate

Present modules, planned assets, state matrix, proposed assumptions, and up to
three high-impact questions. Only the main agent addresses the user. End the run
before any Unreal mutation, even when there are no open questions. A later direct
user response is required to proceed.

For the independent post-build gate, use the companion
`nextgame-ui-build-acceptance` workflow after construction and verification.
