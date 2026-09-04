# Artifact contract

All paths below are relative to the run root supplied during preflight. The
adapter does not embed a machine-specific output directory.

| Stage | Role | Exclusive output | Sole Agent input |
| --- | --- | --- | --- |
| Discovery | `visual-structure` | `findings/visual-structure.json` | `agent-inputs/visual-structure.json` |
| Discovery | `text-requirements` | `findings/text-requirements.json` | `agent-inputs/text-requirements.json` |
| Discovery | `project-pattern` | `findings/project-pattern.json` | `agent-inputs/project-pattern.json` |
| Focused | `state-modeling` | `findings/state-modeling.json` | `agent-inputs/state-modeling.json` |
| Focused | `data-adaptation` | `findings/data-adaptation.json` | `agent-inputs/data-adaptation.json` |
| Focused | `asset-decomposition` | `findings/asset-decomposition.json` | `agent-inputs/asset-decomposition.json` |
| Review | `state-visual-review` | `findings/state-visual-review.json` | `agent-inputs/state-visual-review.json` |
| Review | `schema-feasibility-review` | `findings/schema-feasibility-review.json` | `agent-inputs/schema-feasibility-review.json` |
| Review | `coverage-review` | `findings/coverage-review.json` | `agent-inputs/coverage-review.json` |

The root agent owns `request-packet.json`, `inputs/shared-widget-shortlist.json`,
all `agent-inputs/`, `contexts/normalized-context.json`, `contexts/roles/`, all
`review-views/`, `ui-requirement.draft.json`, and
`ui-requirement.pending.json`. The accepted `ui-requirement.json` is created only
after the first user gate. Normalization and synthesis are orchestration
functions, not additional `AgentFindings` roles.

An output is usable only after the plugin's validator succeeds against the exact
RequestPacket, role packet, and, for focused/review roles, both the complete and
projected contexts. Review findings also bind one of exactly three reproducible
Review Views while the immutable Draft stays outside the Agent-visible input.
The final requirement must pass strict Draft-aware validation with all linked
findings, packets, contexts, and Review Views. Hashes and role/context pairings
are part of that check.

Subagent return text, task completion status, and chat summaries are not evidence
and are not inputs to synthesis. If a receipt disagrees with a validated file,
the file wins. If the file is invalid, terminate the old task and start a fresh
no-history delegation for the same logical role from the same validated packet;
never repair it silently inside the synthesizer.

After the first gate, the root owns `accepted-build-view.json`. Build planning
uses a fresh no-history task whose complete visible input is only this validated
View, including its closed `dispatchContract`, and proceeds only for
`mode: projected` with `buildAllowed: true`. It emits
only staged layouts and `ui-build-bundle.planned.json`, without Editor mutation.
Full-fallback or incomplete coverage stops. The root validates the staged plan
against the complete `ui-requirement.json`, generates native `prepare_build.py`
v0.2 plans, and runs `orchestration/scripts/build_plan_evidence.py` to bind
the artifact that binds pre-mutation evidence before Unreal work. The same tool's `--validate-only` mode
must pass immediately before the first Editor connection; the Requirement
remains final Bundle validator authority and is never replaced by the View.
