# Repository agent rules

This repository publishes contracts and adapters, not project-generated UI assets.

- Keep `plugins/nextgame-ui` as the canonical schema, validator, and Skill implementation.
- Keep `orchestration` vendor-neutral. Runtime-specific APIs belong only in `adapters/<runtime>`.
- Preserve the exact nine AgentFindings roles, their isolated output files, the three dependency barriers, and both direct-user confirmation gates.
- Treat validated JSON artifacts as authoritative. Agent messages and task status are receipts only.
- Never commit Unreal assets, `Saved` request runs, screenshots from a user task, credentials, local caches, or machine-specific absolute paths. The only exception is an explicitly documented synthetic/hash-bound fixture already shipped under plugin `assets`; changing its placeholder paths requires rebuilding every linked digest.
- Requirement analysis is read-only with respect to Unreal. Build mutation begins only after the first direct user approval.
- Documentation begins only after the verified saved build and normalized readback are presented and accepted in a later direct user message.
- Run the repository, adapter, orchestration, Skill, and plugin checks documented in `README.md` before committing.
