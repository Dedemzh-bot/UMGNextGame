---
{
  "kind": "workflow",
  "workflow_name": "nextgame-ui-build-acceptance",
  "portable_workflow_id": "nextgame-ui-requirements",
  "description": "Validate and present one completed NextGame UMG build, then stop for a new user acceptance before any formal documentation.",
  "execution": "main",
  "params_schema": {
    "type": "object",
    "required": ["requirement", "build_bundle", "unreal_readback"],
    "properties": {
      "requirement": {"type": "string"},
      "build_bundle": {"type": "string"},
      "unreal_readback": {"type": "string"}
    },
    "additionalProperties": false
  },
  "steps": [
    {
      "id": "validate-built-artifacts",
      "name": "Validate accepted requirement, final bundle, and Unreal readback",
      "step_type": "code",
      "execution": "main",
      "portable_step": "post-save-unreal-readback",
      "depends_on": [],
      "result_schema": {"type": "object", "required": ["valid"], "properties": {"valid": {"const": true}}, "additionalProperties": false}
    },
    {
      "id": "present-built-result",
      "name": "Present exact assets, preview, tree, properties, and deviations",
      "step_type": "reasoning",
      "execution": "main",
      "portable_step": "present-build-results",
      "depends_on": ["validate-built-artifacts"],
      "result_schema": {"type": "object", "required": ["presented_identity"], "properties": {"presented_identity": {"type": "string", "minLength": 1}}, "additionalProperties": false}
    },
    {
      "id": "build-user-gate",
      "name": "Stop for post-presentation user acceptance",
      "step_type": "reasoning",
      "execution": "main",
      "portable_step": "build-results-confirmation",
      "depends_on": ["present-built-result"],
      "human_gate": true,
      "must_stop": true,
      "decision_artifact": "ui-build-acceptance.json",
      "result_schema": {"type": "object", "required": ["stopped_before_acceptance_artifact", "stopped_before_documentation"], "properties": {"stopped_before_acceptance_artifact": {"const": true}, "stopped_before_documentation": {"const": true}}, "additionalProperties": false}
    }
  ]
}
---

# NextGame UI build acceptance

This workflow represents the second, independent user gate. Run it only after
the accepted requirement has been built and the final UMG assets have been
compiled, saved, verified, previewed, and read back from Unreal.

## validate-built-artifacts

Validate the accepted requirement, final build bundle, and normalized Unreal
readback together. They must cover the same complete asset set. Do not reuse a
stale readback or an approval bound to older hashes.

## present-built-result

The main agent presents the exact target paths, preview, WidgetTree, key runtime
properties, and known deviations. A task status or chat summary is not the
presented build identity.

## build-user-gate

End the run after presentation. Do not create `ui-build-acceptance.json` and do
not start formal documentation. Only a later direct user message accepting that
exact presented result can authorize those actions. Requirement approval and a
request made before construction cannot satisfy this gate.

This mapping has static schema/DAG checks only; it does not claim an end-to-end
execution on a live WorkBuddy service.
