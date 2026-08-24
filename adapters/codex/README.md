# Codex adapter

This mapping uses Codex collaboration primitives to execute the portable
NextGame UI requirement-analysis DAG. It is an orchestration guide; the
NextGame UI skill and its JSON validators remain the semantic authority.

## Primitive mapping

| Portable operation | Codex primitive | Coordinator rule |
| --- | --- | --- |
| Start an isolated role | `spawn_agent` | Assign one role, exact inputs, and exactly one findings path |
| Wait at a barrier | `wait_agent`, optionally `list_agents` | Continue only after all three required files exist |
| Correct an active role | `send_message` | Clarify scope without changing file ownership |
| Retry an idle role | `followup_task` | Re-run the same role against validator errors |
| Stop invalid work | `interrupt_agent` | Preserve existing files for inspection; do not synthesize them |

Do not pass a different model or reasoning override merely because a task is
delegated. The primary retains the complete user request, architecture decisions,
integration, conflict resolution, approvals, and final verification.

## Dispatch sequence

1. The primary validates `request-packet.json` and creates three isolated input
   views. It then starts these roles in parallel:

   - `visual-structure` -> `findings/visual-structure.json`
   - `text-requirements` -> `findings/text-requirements.json`
   - `project-pattern` -> `findings/project-pattern.json`

2. The primary waits for all three, ignores their prose as evidence, and runs
   `validate_agent_findings.py` on each file. Validator failures go back only to
   the owner of the failed file.
3. The primary writes `contexts/normalized-context.json`. Normalization is not a
   delegated `AgentFindings` role.
4. Against the same normalized context, start these roles in parallel:

   - `state-modeling` -> `findings/state-modeling.json`
   - `data-adaptation` -> `findings/data-adaptation.json`
   - `asset-decomposition` -> `findings/asset-decomposition.json`

5. Wait for all three and validate each with the request packet and normalized
   context. The primary alone synthesizes `ui-requirement.draft.json`.
6. Start the review trio in parallel. Give all three the same
   `contexts/normalized-context.json` plus `ui-requirement.draft.json` as a
   read-only input:

   - `state-visual-review` -> `findings/state-visual-review.json`
   - `schema-feasibility-review` -> `findings/schema-feasibility-review.json`
   - `coverage-review` -> `findings/coverage-review.json`

7. Wait for all three and validate them. The same primary adjudicates every
   review finding into `ui-requirement.pending.json`, then runs strict requirement
   validation with linked-file checking. Only a later accepted gate produces
   `ui-requirement.json`.
8. Present the requirement and stop. Only the primary asks questions. A later
   direct user response may accept or amend this first gate.

## Assignment template

Every `spawn_agent` assignment should state all of the following explicitly:

- objective and role name;
- read-only input paths and one exclusive output path;
- allowed mutation: only that output file;
- forbidden changes: other findings, normalized contexts, requirement files,
  Unreal assets, Git state, and user-facing approvals;
- expected evidence and the exact validator command;
- completion condition: saved valid `AgentFindings`, not a prose conclusion;
- warning that other agents share the workspace and their edits must not be
  reverted.

If concurrency is unavailable, the primary may run role assignments one at a
time. The inputs and output ownership must remain isolated exactly as above.

## User gates

The first gate occurs after strict requirement validation and before any Unreal
mutation. The second occurs only after the concrete build has been compiled,
saved, verified, previewed, read back, and presented. The primary ends that turn
and waits for a later direct user acceptance before creating build-acceptance
evidence or starting formal documentation. No subagent may satisfy either gate.
