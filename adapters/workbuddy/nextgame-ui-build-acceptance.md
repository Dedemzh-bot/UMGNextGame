---
{
  "kind": "workflow",
  "workflow_name": "nextgame-ui-protected-build-and-acceptance",
  "portable_workflow_id": "nextgame-ui-requirements",
  "description": "Resume one accepted NextGame UI requirement through the Accepted Build View gate, verified UMG build, Unreal readback, result presentation, and independent user acceptance.",
  "execution": "main",
  "agent_dispatch_policy": {
    "prompt_contract": "single-validated-artifact-path",
    "history_policy": "none",
    "inherits_conversation": false,
    "model_override_allowed": false,
    "reasoning_override_allowed": false
  },
  "build_planning_dispatch_contract": "accepted-build-view.json#dispatchContract",
  "build_planning_retry_policy": {
    "on_validation_failure": "terminate-then-fresh-delegation",
    "reuse_agent": false,
    "prompt": "accepted-build-view.json"
  },
  "pre_mutation_evidence_contract": {
    "artifact": "status/ui-build-plan.pre-mutation-valid.json",
    "schemaRef": "build-plan-pre-mutation.schema.json",
    "toolRef": "scripts/build_plan_evidence.py",
    "generationMode": "generate-and-self-validate",
    "revalidationMode": "--validate-only",
    "requiredBeforeEditorMutation": true
  },
  "composite_artifact_sets": [
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
      "requireRunRootContainment": true,
      "requireDigestMatch": true,
      "requireCompleteEnumeration": true
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
      "requireRunRootContainment": true,
      "requireDigestMatch": true,
      "requireCompleteEnumeration": true
    }
  ],
  "params_schema": {
    "type": "object",
    "required": ["run_root", "plugin_root", "requirement"],
    "properties": {
      "run_root": {"type": "string", "minLength": 1, "description": "Existing durable run directory containing the accepted analysis revision and all linked sidecars."},
      "plugin_root": {"type": "string", "minLength": 1, "description": "Installed nextgame-ui plugin root containing the View and Bundle validators."},
      "requirement": {"type": "string", "description": "Accepted ui-requirement.json path bound to the first user gate."}
    },
    "additionalProperties": false
  },
  "steps": [
    {
      "id": "prepare-accepted-build-view",
      "name": "Revalidate the accepted Requirement and prepare its build View",
      "step_type": "code",
      "execution": "main",
      "portable_step": "prepare-accepted-build-view",
      "depends_on": [],
      "inputs": [
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
        "findings/coverage-review.json"
      ],
      "outputs": ["accepted-build-view.json"],
      "result_schema": {
        "type": "object",
        "required": ["view_path", "mode", "build_allowed"],
        "properties": {
          "view_path": {"const": "accepted-build-view.json"},
          "mode": {"const": "projected"},
          "build_allowed": {"const": true}
        },
        "additionalProperties": false
      }
    },
    {
      "id": "plan-umg-build",
      "name": "Plan UMG from the accepted build View without Editor mutation",
      "step_type": "reasoning",
      "execution": "subagent",
      "portable_step": "plan-umg-build",
      "depends_on": ["prepare-accepted-build-view"],
      "agent_role": "build-planning",
      "inputs": ["accepted-build-view.json"],
      "outputs": ["ui-build-bundle.planned.json"],
      "result_schema": {
        "type": "object",
        "required": [
          "artifact_path",
          "validation_ready",
          "editor_mutation_performed"
        ],
        "properties": {
          "artifact_path": {"const": "ui-build-bundle.planned.json"},
          "validation_ready": {"const": true},
          "editor_mutation_performed": {"const": false}
        },
        "additionalProperties": false
      }
    },
    {
      "id": "validate-build-plan",
      "name": "Validate staged Bundle and layouts before Unreal mutation",
      "step_type": "code",
      "execution": "main",
      "portable_step": "validate-build-plan",
      "depends_on": ["plan-umg-build"],
      "inputs": [
        "ui-requirement.json",
        "accepted-build-view.json",
        "ui-build-bundle.planned.json"
      ],
      "outputs": ["status/ui-build-plan.pre-mutation-valid.json"],
      "result_schema": {
        "type": "object",
        "required": [
          "artifact_path",
          "valid",
          "full_requirement_validated",
          "view_validated",
          "coverage_validated",
          "layouts_validated",
          "plan_manifest_complete",
          "editor_mutation_performed"
        ],
        "properties": {
          "artifact_path": {"const": "status/ui-build-plan.pre-mutation-valid.json"},
          "valid": {"const": true},
          "full_requirement_validated": {"const": true},
          "view_validated": {"const": true},
          "coverage_validated": {"const": true},
          "layouts_validated": {"const": true},
          "plan_manifest_complete": {"const": true},
          "editor_mutation_performed": {"const": false}
        },
        "additionalProperties": false
      }
    },
    {
      "id": "build-verified-umg",
      "name": "Execute, compile, save, and verify the prevalidated UMG plan",
      "step_type": "code",
      "execution": "main",
      "portable_step": "build-verified-umg",
      "depends_on": ["validate-build-plan"],
      "inputs": [
        "ui-requirement.json",
        "accepted-build-view.json",
        "ui-build-bundle.planned.json",
        "status/ui-build-plan.pre-mutation-valid.json"
      ],
      "outputs": ["ui-build-bundle.json"],
      "result_schema": {
        "type": "object",
        "required": [
          "artifact_path",
          "compiled",
          "saved",
          "verified",
          "final_bundle_validated"
        ],
        "properties": {
          "artifact_path": {"const": "ui-build-bundle.json"},
          "compiled": {"const": true},
          "saved": {"const": true},
          "verified": {"const": true},
          "final_bundle_validated": {"const": true}
        },
        "additionalProperties": false
      }
    },
    {
      "id": "post-save-unreal-readback",
      "name": "Read and validate the final saved Unreal Widget state",
      "step_type": "code",
      "execution": "main",
      "portable_step": "post-save-unreal-readback",
      "depends_on": ["build-verified-umg"],
      "inputs": ["ui-requirement.json", "ui-build-bundle.json"],
      "outputs": ["unreal-widget-readback.json"],
      "result_schema": {
        "type": "object",
        "required": ["artifact_path", "valid"],
        "properties": {
          "artifact_path": {"const": "unreal-widget-readback.json"},
          "valid": {"const": true}
        },
        "additionalProperties": false
      }
    },
    {
      "id": "present-built-result",
      "name": "Present exact assets, preview, tree, properties, and deviations",
      "step_type": "reasoning",
      "execution": "main",
      "portable_step": "present-build-results",
      "depends_on": ["post-save-unreal-readback"],
      "inputs": ["ui-build-bundle.json", "unreal-widget-readback.json"],
      "outputs": ["status/build-results.presented.json"],
      "result_schema": {
        "type": "object",
        "required": ["artifact_path", "presented_identity"],
        "properties": {
          "artifact_path": {"const": "status/build-results.presented.json"},
          "presented_identity": {"type": "string", "minLength": 1}
        },
        "additionalProperties": false
      }
    },
    {
      "id": "build-user-gate",
      "name": "Stop for post-presentation user acceptance",
      "step_type": "reasoning",
      "execution": "main",
      "portable_step": "build-results-confirmation",
      "depends_on": ["present-built-result"],
      "inputs": [
        "ui-requirement.json",
        "ui-build-bundle.json",
        "unreal-widget-readback.json",
        "status/build-results.presented.json"
      ],
      "outputs": [],
      "human_gate": true,
      "must_stop": true,
      "decision_artifact": "ui-build-acceptance.json",
      "result_schema": {
        "type": "object",
        "required": [
          "stopped_before_acceptance_artifact",
          "stopped_before_documentation"
        ],
        "properties": {
          "stopped_before_acceptance_artifact": {"const": true},
          "stopped_before_documentation": {"const": true}
        },
        "additionalProperties": false
      }
    }
  ]
}
---

# NextGame UI protected build and acceptance

This workflow resumes only after the first user gate has produced the exact
accepted `ui-requirement.json`. It structurally maps the protected continuation;
it is not a shortcut from raw requirements to Unreal mutation.

## prepare-accepted-build-view

The main coordinator reruns strict linked validation over the full accepted
Requirement, immutable Draft, nine findings and packets, six role contexts, and
three Review Views. It then deterministically creates and validates
`accepted-build-view.json`. Continue only when `mode` is `projected`,
`buildAllowed` is `true`, every accepted claim/element/state/criterion is covered
or explicitly non-build, and all SHA bindings match. Full fallback or incomplete
coverage stops before planning and Unreal mutation.

## plan-umg-build

Start a fresh no-history build task whose complete visible input is only
`accepted-build-view.json`. The View defines the exclusive authorized build
scope, and its closed `dispatchContract` defines the task objective, exclusive
staged `ui-build-bundle.planned.json` result contract, forbidden actions, and
completion rule. It may emit
only the Bundle's content-addressed UILayoutSpec sidecars under `layouts/`:
every non-null `assets[].layoutSpecPath` has the matching
`assets[].layoutSpecSha256`, and null is limited to reuse-only assets. It must not
connect to or mutate Unreal. Do not add the full Requirement, chat history, model/reasoning
overrides, or corrective follow-up text. A failed planning task is terminated and
replaced by a fresh task from the same validated View.

## validate-build-plan

Before any Editor connection, the main coordinator enumerates the staged
Bundle's complete layout set, enforces run-root containment, verifies every
layout hash and Schema, and validates semantic coverage
with both `ui-requirement.json` and `accepted-build-view.json`. The full
Requirement remains authoritative; the View never replaces it. Only a complete
pass generates one deterministic `plans/<asset>.plan.json` per buildable asset
in `execution.buildOrderAssetIds` order using the existing native
`prepare_build.py` v0.2 contract. Then run
`python -B orchestration/scripts/build_plan_evidence.py <artifact-root> --plugin-root <plugin-root>`;
it re-runs the complete Requirement/View/Bundle,
coverage, and UILayoutSpec validators, exactly regenerates every plan, and
atomically writes the SHA-bound `status/ui-build-plan.pre-mutation-valid.json`. Its
`plans[].assetId/path/sha256` records completely enumerate that order, with an
explicit null/skip record only for reuse-only assets. Do not continue when any
linked validator, digest, containment, or enumeration check fails.

## build-verified-umg

The main coordinator verifies that the pre-mutation status still binds every
unchanged authority and plan artifact by rerunning the same evidence tool with
`--validate-only`, then connects to Unreal and performs the
build, compile, save, and verification stages. It writes the final
`ui-build-bundle.json` and reruns full Requirement, View, Bundle, layout, and
coverage validation before readback. The planning subagent never performs this
step.

## post-save-unreal-readback

After the final compile and save, the main coordinator reads actual Unreal Widget
state and writes validated `unreal-widget-readback.json`. It must cover the same
complete asset set as the Requirement and Bundle.

## present-built-result

The main agent presents the exact target paths, preview, WidgetTree, key runtime
properties, and known deviations, then writes
`status/build-results.presented.json` bound to the exact Requirement, final
Bundle, readback, and preview identities. A task status or chat summary is not
the presented build identity.

## build-user-gate

End the run after validating the bound presentation status. Do not create
`ui-build-acceptance.json` and do not start formal documentation. Only a later
direct user message accepting that exact presented result can authorize those
actions. Requirement approval and a request made before construction cannot
satisfy this gate.

This mapping has static schema/DAG checks only; it does not claim an end-to-end
execution on a live WorkBuddy service.
