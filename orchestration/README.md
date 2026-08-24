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

`nextgame-ui.requirements.workflow.json` is a closed `1.0.0` workflow. Adding,
removing, or reordering a protected step requires a contract-version change and
matching validator/test updates.

## Requirements DAG

The default dispatch stops at the first human gate:

```text
validate packet
  -> discovery (3 parallel AgentFindings)
  -> validate discovery
  -> normalize identities
  -> focused analysis (3 parallel AgentFindings)
  -> validate focused findings
  -> synthesize draft
  -> independent review (3 parallel AgentFindings)
  -> same synthesizer resolves reviews
  -> strict linked-file validation
  -> present and stop for explicit user confirmation
```

The protected continuation is encoded for safety checks but is not dispatched by
default. Its mandatory order is accepted Requirement -> verified build ->
post-save Unreal readback -> display the final result -> a second, independent
user confirmation -> programmer handoff/document verification. An adapter must
not infer either user decision from the original request or from agent completion.

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

The generated manifest contains concrete filesystem paths, dependencies,
parallel groups, validators, user gates, and exclusive output ownership. Runtime
adapters translate those records into their own task APIs.

Run the regression suite from the repository root:

```powershell
python -m unittest discover -s orchestration/tests -p "test_*.py"
```
