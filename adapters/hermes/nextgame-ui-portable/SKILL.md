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
and SHA-256 support, coordinator ownership, no-history packet-only dispatch, and
two real user-pause points. Stop if any of those are unavailable. Do not embed an
MCP endpoint or output root.

## Orchestrate

Use `delegate_task` for one bounded role at a time. When the host supports
concurrent calls, dispatch each three-role group concurrently; otherwise execute
the group sequentially without sharing findings between roles.

For every delegated task, start a fresh no-history pass whose complete visible
prompt/input is exactly one validated `agent-inputs/<role>.json` path. The packet
already binds the role, source projection, exclusive `outputRef`, constraints,
and completion contract; do not restate them as unhashed prompt text. Never
expose the raw RequestPacket, complete normalized context, immutable Draft,
another role's packet, or inherited conversation. Returned prose is only a
receipt.

Run this order:

1. Validate the request, prepare the Registry shortlist, then create and validate
   the three discovery packets. Delegate `visual-structure`,
   `text-requirements`, and `project-pattern` with their individual packets.
2. After all three files exist, validate each without a context argument.
3. The root agent normalizes identities, writes
   `contexts/normalized-context.json`, and creates three validated role context
   projections plus their packets.
4. Delegate `state-modeling`, `data-adaptation`, and `asset-decomposition` with
   only their individual packets.
5. Validate all three with the exact RequestPacket and context.
6. The root agent alone synthesizes and freezes `ui-requirement.draft.json`, then
   creates three reviewer context projections, three deterministic
   `review-views/<role>.review-view.json` files, and their validated packets.
7. Delegate `state-visual-review`, `schema-feasibility-review`, and
   `coverage-review` with only their individual packets. Review packets bind the
   role-specific Review View; the complete Draft is validator-side only.
8. Validate the review files. The same root agent adjudicates them, revises the
   draft into `ui-requirement.pending.json`, and runs strict validation with
   linked-file checking over the immutable Draft, all nine packets, six role
   contexts, and three Review Views. The accepted `ui-requirement.json` is a later gate
   decision artifact, not a synthesis shortcut.
9. Present the requirement and stop before any Unreal mutation. Only the root
   agent may ask the user to accept or amend it.

If any delegated findings file fails validation, terminate that task and create
a new no-history delegation from the same validated packet path. Never append a
clarification to, or resume, the failed task.

MCP may be exposed to a delegated role when it is needed to read project or
Editor evidence. Keep analysis access read-only. Unreal mutation belongs to the
separate build stage after the first user gate.

If `delegate_task` is unavailable, sequential compatibility is permitted only
when the host exposes another verifiable primitive that creates a fresh context
with no inherited root history and exactly one visible packet path. The root
agent must not impersonate a role inside its own existing conversation. When no
such primitive exists, fail closed. A valid sequential run still writes nine
separate files, preserves packet-only scopes and context digests, and runs every
barrier validator. Do not merge roles into one free-form answer, and disclose
that the run was not parallel multi-agent execution.

## Preserve both gates

Requirement approval authorizes only the later build. After a production build
is compiled, saved, verified, previewed, read back, and presented, stop again.
Only a later direct user message accepting that exact build may authorize the
build-acceptance artifact and formal documentation. Neither delegated output nor
an earlier request to complete the whole flow is acceptance.

After accepted requirement approval, the root creates and validates
`accepted-build-view.json` before any build-planning delegation. Only
`mode: projected` with `buildAllowed: true` may continue. Start a fresh
no-history build-planning delegation with no inherited conversation and no model
or reasoning override. Its complete visible prompt/input is exactly the validated
`accepted-build-view.json` path; it reads and obeys that View's closed
`dispatchContract`, with no corrective prose. A failure terminates that task and
starts a new fresh delegation from the exact same validated View. The planner may
only emit layouts and `ui-build-bundle.planned.json`; it must not connect to
Unreal. The root validates the staged plan against the complete accepted
Requirement, generates the native `prepare_build.py` v0.2 plans, and runs
`orchestration/scripts/build_plan_evidence.py` to write bound pre-mutation
evidence. It reruns the tool with `--validate-only` as the required pre-mutation
evidence before Editor work.
Full-fallback or incomplete coverage stops, and final Bundle validators still
read the complete accepted Requirement as authority.

This adapter is statically validated in this repository. It does not claim that
the workflow has been executed end to end in every Hermes deployment.
