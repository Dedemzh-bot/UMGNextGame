# Codex adapter

This mapping uses Codex collaboration primitives to execute the portable
NextGame UI requirement-analysis DAG. It is an orchestration guide; the
NextGame UI skill and its JSON validators remain the semantic authority.

## Primitive mapping

| Portable operation | Codex primitive | Coordinator rule |
| --- | --- | --- |
| Start an isolated role | `spawn_agent` | Use `fork_turns="none"`; the complete visible prompt is only one validated packet path |
| Wait at a barrier | `wait_agent`, optionally `list_agents` | Continue only after all three required files exist |
| Retry invalid work | `interrupt_agent`, then a fresh `spawn_agent` | Never append a clarification or reuse Agent history; dispatch the same packet path only |
| Stop invalid work | `interrupt_agent` | Preserve diagnostics for inspection; do not synthesize invalid findings |

Every role dispatch is fail-closed and no-history: `fork_turns="none"`, no
inherited conversation, and no model or reasoning override. The role receives
only `agent-inputs/<role>.json`; raw RequestPacket, the complete normalized
context, immutable Draft, and other role packets remain coordinator/validator
sidecars. The primary retains the complete user request, architecture decisions,
integration, conflict resolution, approvals, and final verification.

## Dispatch sequence

1. The primary validates `request-packet.json`, derives a bounded Registry
   shortlist with strict full-Registry fallback, then creates and validates the
   three discovery packets. It starts these roles in parallel, each with only its
   corresponding `agent-inputs/<role>.json` path:

   - `visual-structure` -> `findings/visual-structure.json`
   - `text-requirements` -> `findings/text-requirements.json`
   - `project-pattern` -> `findings/project-pattern.json`

2. The primary waits for all three, ignores their prose as evidence, and runs
   `validate_agent_findings.py` on each file. On failure, interrupt that role and
   create a fresh no-history role from the same validated packet; never append a
   corrective message to the old task.
3. The primary writes `contexts/normalized-context.json`, deterministically
   projects three `contexts/roles/<role>.json` files, and creates their bound
   packets. The complete context stays validator-side. Normalization is not a
   delegated `AgentFindings` role.
4. Start these roles in parallel with only their packet paths:

   - `state-modeling` -> `findings/state-modeling.json`
   - `data-adaptation` -> `findings/data-adaptation.json`
   - `asset-decomposition` -> `findings/asset-decomposition.json`

5. Wait for all three and validate each against the hidden RequestPacket,
   complete normalized context, role projection, and packet. The primary alone
   synthesizes and freezes `ui-requirement.draft.json`.
6. The primary creates three reviewer context projections, three deterministic
   `review-views/<role>.review-view.json` files, and three reviewer packets. Start
   the review trio in parallel with only its own packet; the complete Draft is
   never an Agent input:

   - `state-visual-review` -> `findings/state-visual-review.json`
   - `schema-feasibility-review` -> `findings/schema-feasibility-review.json`
   - `coverage-review` -> `findings/coverage-review.json`

7. Wait for all three and validate them against the hidden full authorities. The
   same primary adjudicates every review finding into
   `ui-requirement.pending.json`, then runs Draft-aware strict requirement
   validation over all nine findings/packets, six role contexts, and three Review
   Views. Only a later accepted gate produces `ui-requirement.json`.
8. Present the requirement and stop. Only the primary asks questions. A later
   direct user response may accept or amend this first gate.

## Packet-only dispatch contract

Before `spawn_agent`, the coordinator validates that the packet already embeds
the objective, role, source projection, exclusive `outputRef`, write constraints,
forbidden mutations, evidence requirements, validator contract, and completion
condition. The dispatch prompt contains only the relative packet path, for
example `agent-inputs/state-modeling.json`. Do not restate any of those fields in
the prompt: extra prose would violate `packet-path-only` and create a second,
unhashed instruction channel.

If concurrency is unavailable, the primary may run role assignments one at a
time. Every pass must still start with no history and receive exactly one packet
path; input and output ownership remain isolated exactly as above.

## User gates

The first gate occurs after strict requirement validation and before any Unreal
mutation. The second occurs only after the concrete build has been compiled,
saved, verified, previewed, read back, and presented. The primary ends that turn
and waits for a later direct user acceptance before creating build-acceptance
evidence or starting formal documentation. No subagent may satisfy either gate.

After the first gate accepts the full Requirement, the coordinator creates and
validates `accepted-build-view.json`. Start a fresh no-history build-planning
`spawn_agent` with `fork_turns: "none"`, no model or reasoning override, and no
inherited conversation. Its complete visible prompt is only the validated
relative path `accepted-build-view.json`; the task reads and obeys the closed
`dispatchContract` embedded in that View and receives no corrective prose. On
failure, interrupt it and create a new fresh task from that exact same validated
View. The
planner may only emit layouts and `ui-build-bundle.planned.json`; it cannot
connect to Unreal. The coordinator validates that staged plan against the
complete `ui-requirement.json`, generates native `prepare_build.py` v0.2 plans,
and runs `orchestration/scripts/build_plan_evidence.py` to write closed,
SHA-bound pre-mutation evidence. It reruns that tool with `--validate-only`
as the required pre-mutation evidence before Editor work. Fallback or incomplete
coverage stops the build, and final Bundle
validation still reads the complete Requirement together with the View.
