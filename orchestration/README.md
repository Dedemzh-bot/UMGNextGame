# Portable orchestration core

This directory defines the vendor-neutral coordination contract for the
NextGame UI requirements workflow. It does not call Codex, Hermes, WorkBuddy,
Unreal, or any other vendor API.

The contract is deliberately file-first:

- each worker owns one declared output path;
- all paths are POSIX-style and relative to a caller-selected artifact root;
- every validation and gate record binds upstream files with canonical SHA-256;
- missing files, stale hashes, failed validators, and absent user decisions block
  downstream dispatch;
- task messages and agent summaries are advisory, never authoritative artifacts.

`nextgame-ui.requirements.workflow.json` is a closed `2.0.0` workflow. Adding,
removing, or reordering a protected step requires a contract-version change and
matching validator/test updates.

Version `2.0.0` separates scheduler authority from Agent-visible data:

- `inputs` lists every artifact a scheduler or validator may use;
- `agentInputs` is the complete allowlist for constructing an Agent prompt;
- the root `agentDispatchPolicy` requires packet-path-only prompts, no inherited
  history (`forkTurns: none`), and no model or reasoning override;
- every one of the nine findings workers receives exactly one unique validated
  role packet. RequestPacket, complete contexts, immutable Draft, Review Views,
  and other hidden authority remain available to validators but are not implicitly
  exposed to workers.

Adapters must construct an Agent prompt only from `agentInputs`. They must never
treat the wider `inputs` list as prompt context.

## Requirements DAG

The default dispatch stops at the first human gate:

```text
validate packet
  + prepare complete Registry shortlist and 3 discovery role packets
  -> discovery (3 parallel AgentFindings)
  -> validate discovery
  -> normalize identities + prepare 3 focused contexts/role packets
  -> focused analysis (3 parallel AgentFindings)
  -> validate focused findings
  -> synthesize immutable draft + prepare 3 reviewer contexts/Review Views/role packets
  -> independent review (3 parallel AgentFindings)
  -> same synthesizer resolves reviews
  -> strict linked-file validation of Draft, 9 packets, 6 role contexts,
     3 Review Views, and 9 findings
  -> present and stop for explicit user confirmation
```

The three preparation rounds are deliberately folded into `validate-packet`,
`normalize-identities`, and `synthesize-draft`; they are not separately
dispatchable workflow steps.

The protected continuation is encoded for safety checks but is not dispatched by
default. Its mandatory order is accepted Requirement -> deterministically validated
Accepted Build View -> fresh no-history planning -> full-authority pre-mutation
validation -> Editor build/compile/save/verification -> post-save Unreal readback
-> display the final result -> a second, independent user confirmation ->
programmer handoff/document verification. An adapter must not infer either user
decision from the original request or from agent completion.

`prepare-accepted-build-view` revalidates the full accepted Requirement, binds the
View to its canonical SHA and approval, and blocks construction on incomplete
coverage or `full-fallback`. The protected continuation has a separate machine
dispatch policy for its build planner: one fresh task, no history or overrides,
and exactly the validated `accepted-build-view.json` path as visible input. The
planner may only emit staged layouts and `ui-build-bundle.planned.json`; it cannot
connect to Unreal. The Bundle is a composite output descriptor: its non-null
`assets[].layoutSpecPath` entries are uniquely enumerated `layouts/` sidecars,
each bound by `layoutSpecSha256`; reuse-only assets are the only null case. The
coordinator validates containment, digest, Schema, coverage, and full Requirement
bindings, then uses the existing `prepare_build.py` path to generate one native
v0.2 `plans/<asset>.plan.json` for every buildable asset in build order. The
closed `build-plan-pre-mutation.schema.json` contract and
`scripts/build_plan_evidence.py` builder/validator re-run the full validators,
exactly regenerate each executable plan, bind all authority and sidecar hashes,
and atomically create the pre-mutation status. That status is a second composite
descriptor that enumerates every plan by asset ID and SHA, with explicit
reuse-only skips. Final Bundle and coverage validation runs again after the build.

Generate and self-validate the evidence only after the native plan files exist:

```powershell
python -B orchestration/scripts/build_plan_evidence.py <run-directory> `
  --plugin-root plugins/nextgame-ui
```

Immediately before the first Editor connection, run the same command with
`--validate-only`. Any Requirement, View, Bundle, layout, plan, path, coverage,
or digest drift removes the mutation authorization.

## CLI

Only the Python standard library is required.

```powershell
python orchestration/scripts/portable_workflow.py validate `
  --workflow orchestration/nextgame-ui.requirements.workflow.json `
  --schema orchestration/workflow.schema.json
```

To materialize runtime paths without dispatching any agent API, put the immutable
RequestPacket inside a run directory and execute:

```powershell
python orchestration/scripts/portable_workflow.py plan `
  --workflow orchestration/nextgame-ui.requirements.workflow.json `
  --artifact-root <run-directory> `
  --request-packet <run-directory>/request-packet.json `
  --output <run-directory>/status/dispatch-manifest.json
```

Use a run directory outside the source checkout. For a disposable local test,
the repository permits only the ignored `.runs/<request-id>/` root. Never write
real references, findings, contexts, previews, or requirement artifacts into a
tracked source directory.

The generated `2.0.0` manifest contains concrete filesystem paths for both
`inputs` and `agentInputs`, the immutable dispatch policy, dependencies, parallel
groups, validators, user gates, and exclusive output ownership. Runtime adapters
translate those records into their own task APIs without widening prompt inputs.

Run the regression suite from the repository root:

```powershell
python -m unittest discover -s orchestration/tests -p "test_*.py"
```
