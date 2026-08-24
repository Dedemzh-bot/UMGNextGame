---
name: artifact-template-nextgame-umg
description: Create a document using the NextGame UMG 界面说明 retained reference. Use when the user selects the NextGame UMG 界面说明 template or explicitly invokes $artifact-template-nextgame-umg for a programmer-facing UMG handoff document.
---

# NextGame UMG 界面说明

Create a document from the retained neutral reference without treating its placeholders as business data.

## Workflow

1. Read `artifact-template.json` and resolve its paths relative to this skill directory.
2. Load `$documents` and use its retained-reference/template workflow with `assets/reference.docx`.
3. Treat the user's verified content sources as the only content authority. Treat the retained reference as presentation authority only.
4. Clone target-asset rows, asset-detail blocks, and data rows to match the source data. Remove every unused conditional block and every bracketed placeholder.
5. Preserve A4 page setup, blue heading system, light-blue table headers, scope box, section order, and footer treatment.
6. Render the completed DOCX, visually inspect every page, and correct clipping, overlap, broken tables, or blank pages before returning it.

## Safety

- Never copy placeholder values or prior system-specific content into a new document.
- Never invent facts to fill a slot.
- Do not constrain target assets, variables, collections, states, deviations, gaps, tree depth, or trace rows to the counts shown by the reference.
- For the NextGame production workflow, do not create a program handoff document until the verified UMG result has been shown to the user and a valid post-build `ui-build-acceptance.json` records the user's later explicit acceptance.
- The validated program handoff and generated document contract remain authoritative over this presentation template; the template cannot substitute for or bypass the post-build acceptance gate.
