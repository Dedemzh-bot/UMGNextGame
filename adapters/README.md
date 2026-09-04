# Runtime adapters

These adapters let Codex, Hermes, and WorkBuddy execute the same NextGame UI
requirement-analysis contract without changing its artifact schemas or review
gates. The portable boundary is deliberately narrow: runtimes may schedule work
differently, but all of them must produce the same files and run the same plugin
validators.

The authoritative execution order lives in the repository's vendor-neutral
orchestration manifest. `adapter-contract.json` records the invariants that a
runtime mapping may not change: nine roles, three parallel groups, packet-only
no-history dispatch, coordinator ownership of normalization and synthesis,
three protected Review Views, one Accepted Build View, a fresh View-only build
planner with a pre-mutation validation barrier, and two independent user gates.
Run the checks in `capability-contract.json` before dispatch. A runtime must stop
when it cannot preserve exclusive file ownership, local validation and hashing,
durable artifacts, coordinator ownership, no-history role isolation, or either
human pause. Parallel
subagents are required to call a run "multi-agent"; an explicitly disclosed
sequential compatibility mode may be used only when it still provides fresh,
isolated role passes.

## Authority and safety

- A subagent's final message is only a receipt. The file at its assigned output
  path and a successful validator run are authoritative.
- Each subagent receives exactly one validated `agent-inputs/<role>.json` path in
  a fresh no-history pass. Raw RequestPacket, complete normalized context,
  immutable Draft, other packets, and model/reasoning overrides are forbidden.
- Each delegated role owns exactly one `findings/<role>.json` file. It may not
  edit another role's file, the normalized context, or any
  `ui-requirement*.json` artifact.
- Only the primary/coordinator may normalize identities, synthesize or revise
  the requirement, and present questions. Only a later direct user message can
  decide either gate; the coordinator may then record and validate the bound
  decision artifact but cannot supply the decision itself.
- Requirement analysis is read-only with respect to Unreal. It must end after
  presenting the requirement review, before any Unreal mutation.
- Reviewers receive role-specific Review Views through their packets. Unknown
  fields, dangling references, or incomplete closure fall back to a complete
  View; the full Requirement validator still runs after review.
- Build planning runs in a fresh no-history task and receives only
  `accepted-build-view.json`, whose closed `dispatchContract` supplies the
  objective, exclusive outputs, forbidden actions, and completion rule. It may emit staged layouts and
  composite `ui-build-bundle.planned.json` only for a complete `projected` View
  with `buildAllowed: true`; it cannot connect to Unreal. That descriptor is the
  sole declaration of content-addressed `layouts/` sidecars. The coordinator
  validates their containment, hashes, schemas, coverage, and full accepted
  Requirement, then emits one content-addressed `plans/<asset>.plan.json` per
  buildable asset using the existing native `prepare_build.py` v0.2 contract.
  `build_plan_evidence.py` re-runs the full validators, exactly regenerates every
  plan, and writes a closed SHA-bound status that completely enumerates those
  plans and explicit reuse-only skips. The status is revalidated with
  `--validate-only` immediately before Editor work. Final Bundle validation
  repeats after build/save/verification.
- A requirement approval is not build acceptance. After a later build is saved,
  verified, previewed, read back, and presented, the coordinator must stop again
  for a new direct user response before documentation.
- Runtime-native task status, chat summaries, and delegated return values never
  bypass file validation, content hashes, or user approval.

## Included mappings

| Runtime | Dispatch mapping | Validation boundary |
| --- | --- | --- |
| Codex | fresh `spawn_agent` tasks, `wait_agent`, and `interrupt_agent` before retry | Primary runs the plugin validators after each barrier; retries never reuse history |
| Hermes | `delegate_task`, with MCP available to read project evidence | Root Hermes agent validates each exact output file |
| WorkBuddy | Two human-gated workflow knowledge units using `execution: main|subagent` and `depends_on` | Main steps validate each findings wave, Accepted Build View, staged plan before mutation, final Bundle, presentation status, and readback |

If a runtime cannot run delegated work concurrently, it may execute the three
roles in a group sequentially. It must preserve isolation, inputs, output paths,
barriers, and validation, and must not report that fallback as parallel
multi-agent execution. Sequential passes are also fresh and packet-only.

## Static validation

From the repository root, run:

```bash
python adapters/validate_adapters.py
python -m unittest discover -s adapters/tests
```

This checks the exact nine-role set, unique artifact ownership, packet-only
dispatch, role/View bindings, Accepted Build View gating, fresh build-planner
isolation, the pre-mutation build barrier, exact status artifacts, both user
gates, the WorkBuddy workflow front matter, and obvious local-path leaks. It does
not call any vendor service and is not an end-to-end certification of Hermes or
WorkBuddy.

Runtime capability references:

- [OpenAI multi-agent orchestration](https://developers.openai.com/api/docs/guides/responses-multi-agent)
- [Hermes Agent documentation](https://hermes-agent.nousresearch.com/docs/)
- [Hermes MCP support](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp)
- [WorkBuddy workflow architecture](https://docs.work-buddy.ai/handbook/architecture_workflows/)
