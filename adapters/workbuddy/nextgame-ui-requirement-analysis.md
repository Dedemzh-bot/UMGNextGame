---
{
  "kind": "workflow",
  "workflow_name": "nextgame-ui-requirement-analysis",
  "portable_workflow_id": "nextgame-ui-requirements",
  "description": "Run the nine-role NextGame UI requirement analysis through validated file artifacts, then stop at the first user gate before Unreal mutation.",
  "execution": "main",
  "agent_dispatch_policy": {
    "prompt_contract": "packet-path-only",
    "history_policy": "none",
    "inherits_conversation": false,
    "model_override_allowed": false,
    "reasoning_override_allowed": false
  },
  "role_retry_policy": {
    "on_validation_failure": "terminate-then-fresh-delegation",
    "reuse_agent": false,
    "prompt": "agent-inputs/<same-role>.json"
  },
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
      "inputs": ["request-packet.json"],
      "outputs": ["status/request-packet.validated.json", "inputs/shared-widget-shortlist.json", "agent-inputs/visual-structure.json", "agent-inputs/text-requirements.json", "agent-inputs/project-pattern.json"],
      "result_schema": {
        "type": "object",
        "required": ["valid", "prepared_outputs"],
        "properties": {
          "valid": {"const": true},
          "prepared_outputs": {"const": ["status/request-packet.validated.json", "inputs/shared-widget-shortlist.json", "agent-inputs/visual-structure.json", "agent-inputs/text-requirements.json", "agent-inputs/project-pattern.json"]}
        },
        "additionalProperties": false
      }
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
      "inputs": ["agent-inputs/visual-structure.json"],
      "outputs": ["findings/visual-structure.json"],
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
      "inputs": ["agent-inputs/text-requirements.json"],
      "outputs": ["findings/text-requirements.json"],
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
      "inputs": ["agent-inputs/project-pattern.json"],
      "outputs": ["findings/project-pattern.json"],
      "result_schema": {"type": "object", "required": ["artifact_path", "validation_ready"], "properties": {"artifact_path": {"const": "findings/project-pattern.json"}, "validation_ready": {"type": "boolean"}}, "additionalProperties": false}
    },
    {
      "id": "validate-discovery",
      "name": "Validate discovery files",
      "step_type": "code",
      "execution": "main",
      "portable_step": "validate-discovery",
      "depends_on": ["discover-visual", "discover-text", "discover-project"],
      "inputs": ["request-packet.json", "agent-inputs/visual-structure.json", "agent-inputs/text-requirements.json", "agent-inputs/project-pattern.json", "findings/visual-structure.json", "findings/text-requirements.json", "findings/project-pattern.json"],
      "outputs": ["status/discovery-findings.validated.json"],
      "result_schema": {"type": "object", "required": ["artifact_path", "validated_roles"], "properties": {"artifact_path": {"const": "status/discovery-findings.validated.json"}, "validated_roles": {"const": ["visual-structure", "text-requirements", "project-pattern"]}}, "additionalProperties": false}
    },
    {
      "id": "normalize-context",
      "name": "Normalize canonical identities",
      "step_type": "reasoning",
      "execution": "main",
      "portable_step": "normalize-identities",
      "depends_on": ["validate-discovery"],
      "inputs": ["request-packet.json", "inputs/shared-widget-shortlist.json", "agent-inputs/visual-structure.json", "agent-inputs/text-requirements.json", "agent-inputs/project-pattern.json", "findings/visual-structure.json", "findings/text-requirements.json", "findings/project-pattern.json"],
      "outputs": ["contexts/normalized-context.json", "contexts/roles/state-modeling.json", "contexts/roles/data-adaptation.json", "contexts/roles/asset-decomposition.json", "agent-inputs/state-modeling.json", "agent-inputs/data-adaptation.json", "agent-inputs/asset-decomposition.json"],
      "result_schema": {
        "type": "object",
        "required": ["artifact_paths"],
        "properties": {
          "artifact_paths": {"const": ["contexts/normalized-context.json", "contexts/roles/state-modeling.json", "contexts/roles/data-adaptation.json", "contexts/roles/asset-decomposition.json", "agent-inputs/state-modeling.json", "agent-inputs/data-adaptation.json", "agent-inputs/asset-decomposition.json"]}
        },
        "additionalProperties": false
      }
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
      "inputs": ["agent-inputs/state-modeling.json"],
      "outputs": ["findings/state-modeling.json"],
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
      "inputs": ["agent-inputs/data-adaptation.json"],
      "outputs": ["findings/data-adaptation.json"],
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
      "inputs": ["agent-inputs/asset-decomposition.json"],
      "outputs": ["findings/asset-decomposition.json"],
      "result_schema": {"type": "object", "required": ["artifact_path", "validation_ready"], "properties": {"artifact_path": {"const": "findings/asset-decomposition.json"}, "validation_ready": {"type": "boolean"}}, "additionalProperties": false}
    },
    {
      "id": "validate-focused",
      "name": "Validate focused files",
      "step_type": "code",
      "execution": "main",
      "portable_step": "validate-focused",
      "depends_on": ["analyze-state", "analyze-adaptation", "analyze-assets"],
      "inputs": ["request-packet.json", "contexts/normalized-context.json", "contexts/roles/state-modeling.json", "contexts/roles/data-adaptation.json", "contexts/roles/asset-decomposition.json", "agent-inputs/state-modeling.json", "agent-inputs/data-adaptation.json", "agent-inputs/asset-decomposition.json", "findings/state-modeling.json", "findings/data-adaptation.json", "findings/asset-decomposition.json"],
      "outputs": ["status/focused-findings.validated.json"],
      "result_schema": {"type": "object", "required": ["artifact_path", "validated_roles"], "properties": {"artifact_path": {"const": "status/focused-findings.validated.json"}, "validated_roles": {"const": ["state-modeling", "data-adaptation", "asset-decomposition"]}}, "additionalProperties": false}
    },
    {
      "id": "synthesize-draft",
      "name": "Synthesize authoritative draft",
      "step_type": "reasoning",
      "execution": "main",
      "portable_step": "synthesize-draft",
      "depends_on": ["validate-focused"],
      "inputs": ["request-packet.json", "inputs/shared-widget-shortlist.json", "contexts/normalized-context.json", "contexts/roles/state-modeling.json", "contexts/roles/data-adaptation.json", "contexts/roles/asset-decomposition.json", "agent-inputs/visual-structure.json", "agent-inputs/text-requirements.json", "agent-inputs/project-pattern.json", "agent-inputs/state-modeling.json", "agent-inputs/data-adaptation.json", "agent-inputs/asset-decomposition.json", "findings/visual-structure.json", "findings/text-requirements.json", "findings/project-pattern.json", "findings/state-modeling.json", "findings/data-adaptation.json", "findings/asset-decomposition.json"],
      "outputs": ["ui-requirement.draft.json", "contexts/roles/state-visual-review.json", "contexts/roles/schema-feasibility-review.json", "contexts/roles/coverage-review.json", "review-views/state-visual-review.review-view.json", "review-views/schema-feasibility-review.review-view.json", "review-views/coverage-review.review-view.json", "agent-inputs/state-visual-review.json", "agent-inputs/schema-feasibility-review.json", "agent-inputs/coverage-review.json"],
      "result_schema": {
        "type": "object",
        "required": ["requirement_path", "artifact_paths"],
        "properties": {
          "requirement_path": {"const": "ui-requirement.draft.json"},
          "artifact_paths": {"const": ["ui-requirement.draft.json", "contexts/roles/state-visual-review.json", "contexts/roles/schema-feasibility-review.json", "contexts/roles/coverage-review.json", "review-views/state-visual-review.review-view.json", "review-views/schema-feasibility-review.review-view.json", "review-views/coverage-review.review-view.json", "agent-inputs/state-visual-review.json", "agent-inputs/schema-feasibility-review.json", "agent-inputs/coverage-review.json"]}
        },
        "additionalProperties": false
      }
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
      "inputs": ["agent-inputs/state-visual-review.json"],
      "outputs": ["findings/state-visual-review.json"],
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
      "inputs": ["agent-inputs/schema-feasibility-review.json"],
      "outputs": ["findings/schema-feasibility-review.json"],
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
      "inputs": ["agent-inputs/coverage-review.json"],
      "outputs": ["findings/coverage-review.json"],
      "result_schema": {"type": "object", "required": ["artifact_path", "validation_ready"], "properties": {"artifact_path": {"const": "findings/coverage-review.json"}, "validation_ready": {"type": "boolean"}}, "additionalProperties": false}
    },
    {
      "id": "adjudicate-review",
      "name": "Adjudicate all review findings",
      "step_type": "reasoning",
      "execution": "main",
      "portable_step": "finalize-review-resolutions",
      "depends_on": ["review-state-visual", "review-schema", "review-coverage"],
      "inputs": ["request-packet.json", "contexts/normalized-context.json", "ui-requirement.draft.json", "contexts/roles/state-visual-review.json", "contexts/roles/schema-feasibility-review.json", "contexts/roles/coverage-review.json", "review-views/state-visual-review.review-view.json", "review-views/schema-feasibility-review.review-view.json", "review-views/coverage-review.review-view.json", "agent-inputs/state-visual-review.json", "agent-inputs/schema-feasibility-review.json", "agent-inputs/coverage-review.json", "findings/state-visual-review.json", "findings/schema-feasibility-review.json", "findings/coverage-review.json"],
      "outputs": ["ui-requirement.pending.json"],
      "result_schema": {"type": "object", "required": ["requirement_path", "all_reviews_resolved", "validated_review_roles"], "properties": {"requirement_path": {"const": "ui-requirement.pending.json"}, "all_reviews_resolved": {"const": true}, "validated_review_roles": {"const": ["state-visual-review", "schema-feasibility-review", "coverage-review"]}}, "additionalProperties": false}
    },
    {
      "id": "strict-requirement-validation",
      "name": "Strictly validate requirement and linked files",
      "step_type": "code",
      "execution": "main",
      "portable_step": "strict-validate-requirement",
      "depends_on": ["adjudicate-review"],
      "inputs": ["request-packet.json", "inputs/shared-widget-shortlist.json", "contexts/normalized-context.json", "ui-requirement.draft.json", "ui-requirement.pending.json", "agent-inputs/visual-structure.json", "agent-inputs/text-requirements.json", "agent-inputs/project-pattern.json", "agent-inputs/state-modeling.json", "agent-inputs/data-adaptation.json", "agent-inputs/asset-decomposition.json", "agent-inputs/state-visual-review.json", "agent-inputs/schema-feasibility-review.json", "agent-inputs/coverage-review.json", "contexts/roles/state-modeling.json", "contexts/roles/data-adaptation.json", "contexts/roles/asset-decomposition.json", "contexts/roles/state-visual-review.json", "contexts/roles/schema-feasibility-review.json", "contexts/roles/coverage-review.json", "review-views/state-visual-review.review-view.json", "review-views/schema-feasibility-review.review-view.json", "review-views/coverage-review.review-view.json", "findings/visual-structure.json", "findings/text-requirements.json", "findings/project-pattern.json", "findings/state-modeling.json", "findings/data-adaptation.json", "findings/asset-decomposition.json", "findings/state-visual-review.json", "findings/schema-feasibility-review.json", "findings/coverage-review.json"],
      "outputs": ["status/ui-requirement.strict-valid.json"],
      "result_schema": {"type": "object", "required": ["artifact_path", "valid", "linked_findings_count", "linked_packet_count", "role_context_count", "review_view_count", "draft_revalidated"], "properties": {"artifact_path": {"const": "status/ui-requirement.strict-valid.json"}, "valid": {"const": true}, "linked_findings_count": {"const": 9}, "linked_packet_count": {"const": 9}, "role_context_count": {"const": 6}, "review_view_count": {"const": 3}, "draft_revalidated": {"const": true}}, "additionalProperties": false}
    },
    {
      "id": "requirement-user-gate",
      "name": "Present requirement and stop for user review",
      "step_type": "reasoning",
      "execution": "main",
      "portable_step": "requirements-confirmation",
      "depends_on": ["strict-requirement-validation"],
      "inputs": [
        "ui-requirement.pending.json",
        "status/ui-requirement.strict-valid.json"
      ],
      "outputs": [],
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
coordinator-owned synthesis, no-history packet-only role dispatch, and real
pauses for both user gates. The advertised multi-agent mode additionally
requires parallel subagents and isolated role inputs. An isolated sequential
fallback may preserve compatibility, but must be reported as such and not as a
parallel multi-agent run. MCP endpoints and output roots are runtime inputs,
never hard-coded into this knowledge unit.

## validate-request

Validate the RequestPacket with the installed plugin. Create and validate the
Registry shortlist and the three discovery files in `agent-inputs/` only after
request validation succeeds. Each subagent receives its one packet path with no
conversation history. Do not mutate Unreal.

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
on each exact file against the exact RequestPacket. On failure, terminate the old
task and start a fresh no-history subagent for the same logical role using only
the same validated packet path.

## normalize-context

The main agent assigns canonical identities, records alias/discard coverage, and
writes `contexts/normalized-context.json`. It then creates and validates the
three role projections under `contexts/roles/` and their bound packets. The
complete context remains validator-side authority. This is not an AgentFindings
role.

## analyze-state

Read only `agent-inputs/state-modeling.json` and write only
`findings/state-modeling.json`.

## analyze-adaptation

Read only `agent-inputs/data-adaptation.json` and write only
`findings/data-adaptation.json`.

## analyze-assets

Read only `agent-inputs/asset-decomposition.json` and write only
`findings/asset-decomposition.json`.

## validate-focused

Wait for all focused dependencies. Validate each file with both the RequestPacket
and `contexts/normalized-context.json`. A failed role artifact terminates that
task and starts a fresh no-history subagent using only the same validated role
packet path; never reuse the failed task.

## synthesize-draft

The main agent alone writes and freezes `ui-requirement.draft.json`, then creates
three reviewer context projections, three role-specific `review-views/*.json`
files, and three validated reviewer packets. Do not infer implementation from
receipt text.

## review-state-visual

Read only `agent-inputs/state-visual-review.json`. The packet binds the validated
state/visual Review View; the complete Draft is not an Agent input. Write only
this role's findings.

## review-schema

Read only `agent-inputs/schema-feasibility-review.json`. The packet binds the
validated schema/feasibility Review View; the complete Draft is not an Agent
input. Write only this role's findings.

## review-coverage

Read only `agent-inputs/coverage-review.json`. The packet binds the validated
coverage Review View; the complete Draft is not an Agent input. Write only this
role's findings.

## adjudicate-review

Wait for and validate all three review files. The same main synthesizer records a
resolution for every review finding and revises the authoritative requirement.
Any failed reviewer artifact terminates that task and starts a fresh no-history
subagent using only the same validated reviewer packet path; never reuse the
failed task.
Write the adjudicated result to `ui-requirement.pending.json`.

## strict-requirement-validation

Run strict requirement validation with linked-file checking. It must load and
validate the exact nine findings, nine packets, six role contexts, three Review
Views, the immutable Draft, and the full normalized context, not merely compare
receipts or hashes. Use the Draft-aware strict validator path. Validate
`ui-requirement.pending.json`; the accepted
`ui-requirement.json` does not exist until the user gate is completed.

## requirement-user-gate

Present modules, planned assets, state matrix, proposed assumptions, and up to
three high-impact questions. Only the main agent addresses the user. End the run
before any Unreal mutation, even when there are no open questions. A later direct
user response is required to proceed.

After a later direct response accepts the first gate, manually resume with the
companion `nextgame-ui-protected-build-and-acceptance` workflow. Before any
build-planning subagent or Unreal mutation, that workflow deterministically
creates and validates `accepted-build-view.json` from the exact accepted full
Requirement. Continue only for `mode: projected` and `buildAllowed: true`; full
fallback or incomplete coverage stops. The planning Agent receives only the
View, while Bundle validators still read the complete `ui-requirement.json` as
authority.
