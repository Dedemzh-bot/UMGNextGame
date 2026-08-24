---
name: nextgame-ui-portable
description: Orchestrate NextGame UI requirement analysis with isolated Hermes subagents while preserving the plugin's exact nine AgentFindings files, validation barriers, coordinator-owned synthesis, and two user approval gates. Use for screenshot or written-requirement analysis before any Unreal UMG mutation.
---

# NextGame UI portable orchestration for Hermes

Use the installed NextGame UI analysis skill for all semantic instructions,
schemas, and validator commands. This adapter only maps its file-artifact DAG to
Hermes. The portable workflow identity is `nextgame-ui-requirements`.

Read [artifact-contract.md](references/artifact-contract.md) before dispatching.
Validate the RequestPacket before starting any subagent. Preflight must also
prove a durable configured run root, exclusive output ownership, local validator
and SHA-256 support, coordinator ownership, and two real user-pause points. Stop
if any of those are unavailable. Do not embed an MCP endpoint or output root.

## Orchestrate

Use `delegate_task` for one bounded role at a time. When the host supports
concurrent calls, dispatch each three-role group concurrently; otherwise execute
the group sequentially without sharing findings between roles.

For every delegated task, provide only the role's allowed source view, the
RequestPacket digest, relevant normalized context when required, and one exact
output path. Tell the subagent that it may write only that file and must not edit
Unreal assets, Git state, contexts, the requirement, or another role's findings.
Its returned prose is only a receipt.

Run this order:

1. Delegate `visual-structure`, `text-requirements`, and `project-pattern`.
2. After all three files exist, validate each without a context argument.
3. The root agent normalizes identities and writes
   `contexts/normalized-context.json`.
4. Delegate `state-modeling`, `data-adaptation`, and `asset-decomposition`
   against that same context.
5. Validate all three with the exact RequestPacket and context.
6. The root agent alone synthesizes `ui-requirement.draft.json`.
7. Delegate `state-visual-review`, `schema-feasibility-review`, and
   `coverage-review` against the same `contexts/normalized-context.json`, with
   `ui-requirement.draft.json` supplied as an additional read-only input.
8. Validate the review files. The same root agent adjudicates them, revises the
   draft into `ui-requirement.pending.json`, and runs strict validation with
   linked-file checking. The accepted `ui-requirement.json` is a later gate
   decision artifact, not a synthesis shortcut.
9. Present the requirement and stop before any Unreal mutation. Only the root
   agent may ask the user to accept or amend it.

MCP may be exposed to a delegated role when it is needed to read project or
Editor evidence. Keep analysis access read-only. Unreal mutation belongs to the
separate build stage after the first user gate.

If `delegate_task` is unavailable, the root agent may execute each role in a
fresh, isolated pass. It must still write nine separate files, preserve their
source scopes and context digests, and run every barrier validator. Do not merge
roles into one free-form answer. Disclose this as sequential compatibility mode;
do not claim it was a parallel multi-agent run.

## Preserve both gates

Requirement approval authorizes only the later build. After a production build
is compiled, saved, verified, previewed, read back, and presented, stop again.
Only a later direct user message accepting that exact build may authorize the
build-acceptance artifact and formal documentation. Neither delegated output nor
an earlier request to complete the whole flow is acceptance.

This adapter is statically validated in this repository. It does not claim that
the workflow has been executed end to end in every Hermes deployment.
