# Runtime adapters

These adapters let Codex, Hermes, and WorkBuddy execute the same NextGame UI
requirement-analysis contract without changing its artifact schemas or review
gates. The portable boundary is deliberately narrow: runtimes may schedule work
differently, but all of them must produce the same files and run the same plugin
validators.

The authoritative execution order lives in the repository's vendor-neutral
orchestration manifest. `adapter-contract.json` records the invariants that a
runtime mapping may not change: nine roles, three parallel groups, coordinator
ownership of normalization and synthesis, and two independent user gates.
Run the checks in `capability-contract.json` before dispatch. A runtime must stop
when it cannot preserve exclusive file ownership, local validation and hashing,
durable artifacts, coordinator ownership, or either human pause. Parallel
subagents are required to call a run "multi-agent"; an explicitly disclosed
sequential compatibility mode may be used only when it still provides fresh,
isolated role passes.

## Authority and safety

- A subagent's final message is only a receipt. The file at its assigned output
  path and a successful validator run are authoritative.
- Each delegated role owns exactly one `findings/<role>.json` file. It may not
  edit another role's file, the normalized context, or any
  `ui-requirement*.json` artifact.
- Only the primary/coordinator may normalize identities, synthesize or revise
  the requirement, ask the user questions, or accept a gate.
- Requirement analysis is read-only with respect to Unreal. It must end after
  presenting the requirement review, before any Unreal mutation.
- A requirement approval is not build acceptance. After a later build is saved,
  verified, previewed, read back, and presented, the coordinator must stop again
  for a new direct user response before documentation.
- Runtime-native task status, chat summaries, and delegated return values never
  bypass file validation, content hashes, or user approval.

## Included mappings

| Runtime | Dispatch mapping | Validation boundary |
| --- | --- | --- |
| Codex | `spawn_agent`, `wait_agent`, `send_message`/`followup_task` | Primary runs the plugin validators after each barrier |
| Hermes | `delegate_task`, with MCP available to read project evidence | Root Hermes agent validates each exact output file |
| WorkBuddy | Workflow knowledge units with `execution: main|subagent` and `depends_on` | Main steps validate files after each parallel group |

If a runtime cannot run delegated work concurrently, it may execute the three
roles in a group sequentially. It must preserve isolation, inputs, output paths,
barriers, and validation, and must not report that fallback as parallel
multi-agent execution.

## Static validation

From the repository root, run:

```bash
python adapters/validate_adapters.py
python -m unittest discover -s adapters/tests
```

This checks the exact nine-role set, unique artifact ownership, DAG barriers,
both user gates, the WorkBuddy workflow front matter, and obvious local-path
leaks. It does not call any vendor service and is not an end-to-end certification
of Hermes or WorkBuddy.

Runtime capability references:

- [OpenAI multi-agent orchestration](https://developers.openai.com/api/docs/guides/responses-multi-agent)
- [Hermes Agent documentation](https://hermes-agent.nousresearch.com/docs/)
- [Hermes MCP support](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp)
- [WorkBuddy workflow architecture](https://docs.work-buddy.ai/handbook/architecture_workflows/)
