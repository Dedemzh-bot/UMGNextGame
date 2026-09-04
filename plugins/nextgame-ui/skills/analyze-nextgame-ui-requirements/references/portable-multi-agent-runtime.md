# Portable Multi-Agent Runtime

The requirement pipeline is defined by files and validators, not by a particular agent product. A compatible runtime may execute roles concurrently or sequentially, but it must preserve the same no-history packet isolation, dependency order, role identities, output paths, validation sidecars, and user gates.

## Authority boundary

- `request-packet.json` is the immutable input identity for one analysis revision. It remains coordinator/validator-only and is never a worker attachment.
- Before every dispatch, the coordinator deterministically creates and validates `agent-inputs/<role>.json`. A worker receives exactly its packet path in a fresh no-history pass, without inherited conversation, raw RequestPacket, full normalized context, immutable Draft, another packet, or model/reasoning override.
- Every worker writes exactly one assigned `AgentFindings` file. Chat summaries, tool messages, and subagent return text are not findings evidence.
- The normalizer owns canonical IDs, `contexts/normalized-context.json`, and the six role projections under `contexts/roles/`. Workers never edit context or another role's output.
- One synthesizer owns immutable `ui-requirement.draft.json` and the adjudicated `ui-requirement.pending.json`. It also creates exactly three validated Review Views and reviewer packets. Reviewers only see their packet and only write findings.
- The coordinator writes canonical accepted `ui-requirement.json` only after a later direct user response accepts that pending revision. A legacy host may use `ui-requirement.json` as its pending filename, but the filename never implies acceptance: `reviewGate.status`, the approved digest, and the direct user decision remain mandatory.
- Validators read the complete authorities at every barrier. A smaller Agent View or runtime success status cannot replace full linked-file validation.
- Only the primary coordinator communicates a review decision to the user.

## Required role graph

After validating the RequestPacket, the coordinator derives a strict Registry shortlist, creates three discovery packets, and runs:

1. `visual-structure`
2. `text-requirements`
3. `project-pattern`

After all three findings validate against their packets, normalize identities. Create three role-specific context projections and focused packets, then run:

1. `state-modeling`
2. `data-adaptation`
3. `asset-decomposition`

After synthesis, freeze the complete Draft as validator-side authority. Create exactly these role-specific Review Views, reviewer context projections, and packets before running:

1. `state-visual-review` -> `review-views/state-visual-review.review-view.json`
2. `schema-feasibility-review` -> `review-views/schema-feasibility-review.review-view.json`
3. `coverage-review` -> `review-views/coverage-review.review-view.json`

The final Requirement normalization contains exactly those nine roles. Strict validation reads the immutable Draft, all nine packets and findings, the full normalized context, all six role projections, and all three Review Views. A host may use native subagents, subprocess agents, or a sequential fallback; it must not merge role files or let later roles silently rewrite earlier evidence.

## Runtime mapping

Each runtime adapter must provide these operations:

1. create and validate each role packet from coordinator-owned authorities;
2. dispatch one bounded role with exactly one Agent-visible packet and one exclusive output path, without history inheritance;
3. wait for every role in the current parallel group;
4. run the repository validators locally over both the visible packet and hidden complete authorities;
5. dispatch the normalizer or sole synthesizer only after its dependencies pass;
6. stop at the first user confirmation gate.

After the accepted requirement gate, the coordinator creates and validates `accepted-build-view.json` as a separate protected step. Build planning receives only this View and proceeds only when `mode` is `projected` and `buildAllowed` is `true`; full fallback or incomplete coverage stops. Bundle validators continue to read the complete accepted Requirement together with the View.

The build phase remains a separate authorization boundary. After build, compile, save, preview, and normalized Unreal readback, stop again for the user's independent acceptance before producing program handoff or documentation.

## Failure and fallback

- If a role fails, preserve its diagnostics and rerun only that role against the same immutable packet.
- If native parallel subagents are unavailable, execute roles one at a time in the listed group, with a fresh no-history pass for each packet. Disclose that this was not parallel multi-agent execution.
- If a runtime cannot enforce exclusive paths, use separate temporary workspaces and copy only a schema-valid findings file into the request directory.
- Role context projection and Review View generation may use their defined full-fallback modes when safe narrowing cannot be proven; validation still uses the complete source authority.
- Accepted Build View full-fallback is not buildable and must stop before planning or Unreal mutation.
- Never compensate for missing agent features by weakening digests, hashes, role coverage, linked-file checks, or either user confirmation gate.

The repository-level `orchestration/` contract and `adapters/` examples provide concrete dispatch mappings for supported hosts. The plugin validators remain the final authority.
