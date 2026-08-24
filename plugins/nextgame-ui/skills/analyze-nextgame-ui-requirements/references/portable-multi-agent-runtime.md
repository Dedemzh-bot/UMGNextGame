# Portable Multi-Agent Runtime

The requirement pipeline is defined by files and validators, not by a particular agent product. A compatible runtime may execute roles concurrently or sequentially, but it must preserve the same isolation, dependency order, role identities, output paths, and user gates.

## Authority boundary

- `request-packet.json` is the immutable input identity for one analysis revision.
- Every worker writes exactly one assigned `AgentFindings` file. Chat summaries, tool messages, and subagent return text are not findings evidence.
- The normalizer owns canonical IDs and context files. Workers never edit another role's output.
- One synthesizer owns `ui-requirement.draft.json` and the adjudicated
  `ui-requirement.pending.json`; reviewers only write findings. The coordinator
  writes the canonical accepted `ui-requirement.json` only after a later direct
  user response accepts that pending revision. A legacy host may use
  `ui-requirement.json` as its pending filename, but the filename never implies
  acceptance: `reviewGate.status`, the approved digest, and the direct user
  decision remain mandatory.
- Validators must pass at each barrier. A runtime success status cannot replace validation.
- Only the primary coordinator communicates a review decision to the user.

## Required role graph

Run the first three roles from the same validated packet, with isolated sources:

1. `visual-structure`
2. `text-requirements`
3. `project-pattern`

After all three validate, normalize identities. Then run these three roles from the same normalized context:

1. `state-modeling`
2. `data-adaptation`
3. `asset-decomposition`

After synthesis, run these three reviewers from the same draft and canonical context:

1. `state-visual-review`
2. `schema-feasibility-review`
3. `coverage-review`

The final Requirement normalization contains exactly those nine roles. A host may use native subagents, subprocess agents, or a sequential fallback; it must not merge role files or let later roles silently rewrite earlier evidence.

## Runtime mapping

Each runtime adapter must provide five operations:

1. dispatch one bounded role with an exclusive output path;
2. wait for every role in the current parallel group;
3. run the repository validators locally;
4. dispatch the normalizer or sole synthesizer only after its dependencies pass;
5. stop at the first user confirmation gate.

The build phase remains a separate authorization boundary. After build, compile, save, preview, and normalized Unreal readback, stop again for the user's independent acceptance before producing program handoff or documentation.

## Failure and fallback

- If a role fails, preserve its diagnostics and rerun only that role against the same immutable inputs.
- If native parallel subagents are unavailable, execute the roles one at a time in the listed group while retaining their separate files and equal inputs.
- If a runtime cannot enforce exclusive paths, use separate temporary workspaces and copy only a schema-valid findings file into the request directory.
- Never compensate for missing agent features by weakening digests, hashes, role coverage, linked-file checks, or either user confirmation gate.

The repository-level `orchestration/` contract and `adapters/` examples provide concrete dispatch mappings for supported hosts. The plugin validators remain the final authority.
