# Artifact contract

All paths below are relative to the run root supplied during preflight. The
adapter does not embed a machine-specific output directory.

| Stage | Role | Exclusive output | Context |
| --- | --- | --- | --- |
| Discovery | `visual-structure` | `findings/visual-structure.json` | RequestPacket image sources only |
| Discovery | `text-requirements` | `findings/text-requirements.json` | RequestPacket user-text sources only |
| Discovery | `project-pattern` | `findings/project-pattern.json` | Project rules and permitted asset evidence only |
| Focused | `state-modeling` | `findings/state-modeling.json` | `contexts/normalized-context.json` |
| Focused | `data-adaptation` | `findings/data-adaptation.json` | `contexts/normalized-context.json` |
| Focused | `asset-decomposition` | `findings/asset-decomposition.json` | `contexts/normalized-context.json` |
| Review | `state-visual-review` | `findings/state-visual-review.json` | `contexts/normalized-context.json` plus read-only `ui-requirement.draft.json` |
| Review | `schema-feasibility-review` | `findings/schema-feasibility-review.json` | `contexts/normalized-context.json` plus read-only `ui-requirement.draft.json` |
| Review | `coverage-review` | `findings/coverage-review.json` | `contexts/normalized-context.json` plus read-only `ui-requirement.draft.json` |

The root agent owns `request-packet.json`, `contexts/normalized-context.json`,
`ui-requirement.draft.json`, and `ui-requirement.pending.json`. The accepted
`ui-requirement.json` is created only after the first user gate. Normalization
and synthesis are orchestration functions, not additional `AgentFindings` roles.

An output is usable only after the plugin's validator succeeds against the exact
RequestPacket and, for focused/review roles, the exact context. The final
requirement must pass strict validation with linked findings and context files.
Hashes and role/context pairings are part of that check.

Subagent return text, task completion status, and chat summaries are not evidence
and are not inputs to synthesis. If a receipt disagrees with a validated file,
the file wins. If the file is invalid, retry its owner; never repair it silently
inside the synthesizer.
