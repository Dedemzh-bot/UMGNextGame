---
name: document-nextgame-umg
description: Validate the accepted NextGame UIRequirementSpec, completed and verified UIBuildBundle, post-save Unreal Widget readback, and the user's independent post-build acceptance; derive a program-facing UIProgramHandoff; then coordinate DOCX creation and page-by-page verification. Use after NextGame UMG construction has been presented and explicitly accepted when Codex must produce or verify the programmer handoff document without exposing static Designer configuration or inventing runtime API details.
---

# Document NextGame UMG

Treat documentation as a gated stage after requirement acceptance, verified UMG construction, concrete build-result presentation, and a later direct user confirmation. Read [program-handoff.md](references/program-handoff.md) before preparing artifacts, and read [program-document-template.md](references/program-document-template.md) before creating the DOCX.

## Workflow

1. Require the current accepted `UIRequirementSpec`, the completed and passed `UIBuildBundle`, an actual post-save Unreal readback, and `ui-build-acceptance.json`. The first three are authoritative data sources; acceptance is independent authorization evidence that must come from a later direct user message after the concrete build result was presented. Never generate, infer, repair, or pre-authorize acceptance in this skill, and never infer readback fields from the Bundle or UILayoutSpec.
2. Validate the readback:

   ```powershell
   python scripts/validate_unreal_widget_readback.py <readback.json> --requirement <requirement.json> --bundle <bundle.json>
   ```

   When `analysisPolicy.designSizeModeRequired` is enabled, require every Bundle asset, including `reuse-only`, to contain the actual post-save generated-class CDO `DesignSizeMode` and match the accepted Requirement asset plan's `designSizeModeDecision.mode`. Never derive this value from `assetKind`, `umg_`/`uw_` naming, or Bundle representation. Archived Requirements with the policy absent or false may omit the readback field; if they provide it, accept only `FillScreen` or `Desired` and compare it with a valid bound Requirement decision when one exists. For NxUE-only reads, record an exact `acquisition.fieldFallbacks[].jsonPath` for every reported DesignSizeMode. For mixed reads, treat `fieldFallbacks` as the exhaustive list of NxUE-acquired fields and use only concrete `$.assets[i].designSizeMode` paths, never wildcard or aggregate evidence. Treat this as static verification evidence only; do not expose it as a programmer-facing runtime interface.

3. Validate the post-build acceptance against those exact files:

   ```powershell
   python scripts/validate_build_acceptance.py <ui-build-acceptance.json> --requirement <requirement.json> --bundle <bundle.json> --readback <readback.json>
   ```

   Require phase `post-build-ui-review`, status `accepted`, reviewer actor type `user`, confirmation source `direct-user-message`, current Requirement/Bundle/Readback bindings, and exact reviewed asset ID/path coverage. The Requirement review gate is not a substitute.
4. Prepare and validate `UIProgramHandoff 0.3`:

   ```powershell
   python scripts/prepare_program_handoff.py --requirement <requirement.json> --bundle <bundle.json> --readback <readback.json> --build-acceptance <ui-build-acceptance.json> --output <ui-program-handoff.json>
   python scripts/validate_program_handoff.py <ui-program-handoff.json> --requirement <requirement.json> --bundle <bundle.json> --readback <readback.json> --build-acceptance <ui-build-acceptance.json>
   ```

5. Generate the deterministic, safe DOCX content contract:

   ```powershell
   python scripts/prepare_program_document_contract.py --handoff <ui-program-handoff.json> --requirement <requirement.json> --bundle <bundle.json> --readback <readback.json> --build-acceptance <ui-build-acceptance.json> --output <program-document-content.json>
   ```

   Its `requiredSemanticRelationshipStatements` are machine-generated trace rows, not prose for the Agent to paraphrase. Include every row verbatim in a clearly labeled program-trace appendix. This preserves exact asset/Widget/EntryClass/state relationships while the rest of the document remains human-readable.
6. Only after validation passes, resolve the user environment variable named by `output.rootEnvironmentVariable` and ask the main coordinating agent to use the available `$documents` skill to generate the DOCX named by `output.fileName` beneath that root. Use the packaged document template declared by `../artifact-template-nextgame-umg/artifact-template.json`, its neutral `assets/reference.docx`, and [program-document-template.md](references/program-document-template.md) as presentation guidance. Give `$documents` only the verified `UIProgramHandoff` and its generated `program-document-content.json` as business data, not the raw Requirement, Bundle, UILayoutSpec, source prose, acceptance artifact, or content from a previous DOCX. Before invoking `$documents`, the NextGame-specific stage may project location-only WidgetTree diagrams from the already validated Readback, limited to `widgetName`, `classPath`, `parentWidgetName`, and `isVariable`; pass the resulting diagram files rather than the raw Readback. The verified handoff and generated document contract always override the presentation template. Replace every placeholder; clone target rows, complete asset-detail blocks, WidgetTree diagrams, and table rows to the actual unbounded counts; omit empty conditional sections; repeat headers and paginate tall trees without clipping, overlap, or blank pages. Never carry system-specific identifiers, asset paths, Widget names, tree nodes, trace statements, screenshots, or media out of the neutral reference. Never serialize the resolved root. `documents:documents` is a soft dependency: never hard-code its cache path or version. If the environment variable or skill is unavailable, stop after the verified JSON handoff and report the missing prerequisite.
7. Create the authoritative render evidence. The validator itself performs a fresh headless DOCX-to-PDF conversion and uses `pdftoppm` to create the final `page-<N>.png` files in the fresh directory; those exact pages are the pages to review:

   ```powershell
   python scripts/validate_program_docx.py --docx <document.docx> --render-dir <fresh-pages> --render-evidence-output <render-evidence.json> [--soffice-path <soffice>] [--pdftoppm-path <pdftoppm>]
   ```

8. Visually inspect every authoritative page in that evidence. Record every reviewed PNG filename explicitly; never infer page review from the existence of files.
9. Record and validate final document evidence. Repeat `--reviewed-page` once for every reviewed page. Final validation reconverts the current DOCX and rerenders it, then compares every page hash, size, and dimension with the reviewed pages:

   ```powershell
   python scripts/validate_program_docx.py --handoff <ui-program-handoff.json> --requirement <requirement.json> --bundle <bundle.json> --readback <readback.json> --build-acceptance <ui-build-acceptance.json> --docx <document.docx> --render-dir <fresh-pages> --render-evidence <render-evidence.json> --reviewed-by <agent> --reviewed-at <iso-8601> --reviewed-page <page-1.png> --output <document-verification.json> [--soffice-path <soffice>] [--pdftoppm-path <pdftoppm>]
   python scripts/validate_program_docx.py --handoff <ui-program-handoff.json> --requirement <requirement.json> --bundle <bundle.json> --readback <readback.json> --build-acceptance <ui-build-acceptance.json> --docx <document.docx> --render-dir <fresh-pages> --render-evidence <render-evidence.json> --verification <document-verification.json> [--soffice-path <soffice>] [--pdftoppm-path <pdftoppm>]
   ```

## Hard boundaries

- Accept only `target.mode: production` and one `/Game/UI/UMG/<SystemFolder>/...` system folder.
- Require a schema-valid, currently bound `ui-build-acceptance.json` with accepted post-build status and direct-user-message provenance. Never substitute the Requirement review, an earlier blanket request, or Agent inference, and never create the acceptance from within this skill.
- Treat any asset mutation or re-save, Requirement or Bundle change, preview replacement or re-verification, or Readback change as invalidating the acceptance. Re-read and fully validate all three current source files at document-content generation and again at final DOCX verification; comparing only an old handoff with an old acceptance is insufficient. Return to build-result presentation and wait for another direct user confirmation.
- Derive program variables only from the intersection of accepted in-scope runtime intent, a mapped layout node with `isVariable: true`, and actual Unreal `isVariable: true`. Accepted state branch Panels obey the same layout-and-actual variable gate.
- Keep post-save `actualSavedVisibilityBindings` as trace evidence only; never project their saved Visibility values into the programmer-facing DOCX. Emit `runtimeVisibilityOutcomes` only from accepted explicit `implementation.stateOverrides[].changes[]` whose property is `Visibility`, after the same accepted-element, layout-variable, and actual-variable gates.
- Preserve each state model's accepted `implementationStrategy`. Emit `State branch` document rows only for `exclusive-panel-branches`, and emit `State outcome` rows only for `shared-tree-properties`; exclusive states cannot carry runtime Visibility outcomes.
- Treat a missing, ambiguous, non-variable, or Visibility-less mapping for an accepted state target as a projection failure. Never silently omit it or downgrade it to a handoff gap.
- Include runtime collections, state controls, accepted build deviations, and explicit state-control gaps with source identifiers.
- Require every Bundle asset to have passed `widget-tree` and `key-properties` checks whose `artifactPath` resolves to this exact Readback. Require Readback time to follow the timezone-aware Bundle completion time.
- Exclude static Designer layout, anchors, dimensions, fonts, colors, draw settings, fixed text, backgrounds, decoration, and default visibility from program interface requirements.
- Do not document generated-content data source/owner/refresh strategy, runtime parameter type/default/update timing, event or callback name/payload, or list item data structure. These exclusions are policy, never handoff gaps.
- Project program purposes and state-control descriptions only from the fixed high-level enums in the contract. Never copy arbitrary runtime reasons, control descriptions, or deviation prose into the handoff.
- Require the DOCX to contain every generated semantic relationship statement exactly once or more in its trace appendix. Loose identifier presence does not prove the asset/Widget/EntryClass/state relationship.
- Reject the narrow bilingual policy phrases that would introduce any of the four excluded contract categories into the final DOCX.
- Reject saved/default Designer Visibility evidence, including branch-root Widget/value pairs and any `State branch:` row containing `visibility=`. Allow only accepted shared-tree target Visibility represented by `State outcome` semantics.
- Store only `NEXTGAME_UI_PROGRAM_DOCS_ROOT` and the DOCX filename for the output destination. Never store its resolved absolute directory or output path.
