# NextGame UMG program handoff contract

## Inputs and gates

Use exactly three authoritative data inputs and one mandatory authorization artifact:

1. The current `UIRequirementSpec`. Its `reviewGate.status` must be `accepted`, its approval digest must match its current canonical content, and accepted claims define eligible runtime intent.
2. The final `UIBuildBundle`. Its Requirement identity and file hash must match input 1; execution must be `completed`; verification must be `passed`; every asset must be `verified`; every check must be `passed`.
3. `UnrealWidgetReadback`. Its Requirement and Bundle bindings must match the two actual files. It must be populated from post-save Unreal reads after the timezone-aware Bundle completion time and cover every Bundle asset and every `nodeMapping` exactly once. Every asset also needs passed `widget-tree` and `key-properties` checks whose `artifactPath` resolves to this exact Readback file.
4. `ui-build-acceptance.json`. This is authorization evidence, not a fourth source of UI meaning or actual implementation facts. It must have phase `post-build-ui-review`, status `accepted`, direct-user-message provenance from a user reviewer, exact hashes and identities for inputs 1-3, and exact reviewed asset ID/path coverage.

Validate with the schemas in `assets/` and the deterministic scripts in `scripts/`. A planned layout or Bundle field cannot substitute for actual Unreal evidence. The documentation Agent must never create, infer, repair, or pre-authorize acceptance. The requirement review and any request made before the final UMG result was presented cannot substitute for the later direct user confirmation.

Any asset mutation or re-save, Bundle change, preview replacement or re-verification, or Readback change invalidates the prior acceptance. Return to the build workflow, regenerate the affected final artifacts, present the revised result, and wait for a new direct user confirmation. Prototype, legacy standalone, and explicitly no-document tasks do not enter this contract and do not create acceptance.

## UnrealWidgetReadback

Each `assets[]` entry records the actual asset and contains:

- optional actual generated-class CDO `designSizeMode`, restricted to `FillScreen` or `Desired`; every reported Bundle asset whose Blueprint basename is `umg_*` must use `FillScreen` regardless of `assetKind`, representation, or legacy policy, while an archived readback may still omit this newly introduced field when the current policy does not require it. When the Requirement enables `analysisPolicy.designSizeModeRequired`, every Bundle asset must report the field and it must equal the accepted corresponding `assetPlan[].designSizeModeDecision.mode`; this decision binding also applies to `umg_*`, and only `uw_*` mode selection remains analysis-driven;
- `widgets[]`: `widgetName`, actual `classPath`, `parentWidgetName`, `isVariable`, optional actual `entryWidgetClass`, and optional actual `visibility`;
- `nodeMappings[]`: exact `nodeMappingId`, `layoutNodeId`, and the actual `widgetName` it resolved to.

Collection containers must report actual `entryWidgetClass`. Accepted state-branch nodes must report actual `visibility` and must be variables in both UILayoutSpec and the actual Unreal WidgetTree. The validator checks Bundle asset/mapping coverage, identities, hashes, collection properties, state variables, and runtime-variable agreement with linked layouts. Linked UILayoutSpec paths are relative and must remain inside the Bundle request directory.

`acquisition.fieldFallbacks` is an exhaustive field-level list for `mixed` acquisition. Any DesignSizeMode obtained through NxUE must be bound to the concrete readback location `$.assets[i].designSizeMode`; wildcard or aggregate paths are not evidence. A full `nxue-agent` acquisition must provide one such concrete fallback row for every reported DesignSizeMode. Historical readbacks without DesignSizeMode remain valid while the Requirement policy is absent or false.

## UIProgramHandoff projection

Project only safe programmer-facing facts, plus the explicitly separated post-save state evidence needed for deterministic validation:

- `programVariables`: accepted, in-scope `runtimeFields` whose element is runtime controlled and whose Bundle mapping, UILayoutSpec node, and actual Unreal Widget all agree that it is a variable; `purpose` is derived from the fixed `valueKind` mapping rather than copied from free-form `reason`;
- `collections`: accepted, in-scope dynamic collection containers with their actual Widget identity and `EntryWidgetClass`; do not describe entry data fields or item schemas;
- `states`: accepted state models, axes and states, evidence-only post-save branch bindings, accepted explicit runtime Visibility outcomes, and specified `stateModels[].controlInputs[]`; control descriptions are fixed high-level phrases derived from `kind`, not copied source prose;
- `deviations`: only deviation ID, status, impact, and affected Requirement/asset IDs; do not copy free-form expected/actual/reason text;
- `gaps`: only missing `controlInputs` or `kind: unspecified` state controls.

For `controlInputs`, preserve `id`, `axisId`, `kind`, `description`, `targetStateIds`, and traceable accepted claim IDs. Do not read a top-level `controlInputs` field and do not interpret an unspecified input as a concrete program API.

`UIProgramHandoff` version `0.3` retains the state separation introduced in 0.2 and adds the exact post-build acceptance source binding. The following state concepts must not be conflated:

- `implementationStrategy` preserves whether the accepted model uses `exclusive-panel-branches` or `shared-tree-properties`, so downstream projection never guesses from array contents.
- `actualSavedVisibilityBindings` identifies runtime-controlled, variable Widgets found in the post-save readback. Its `visibility` value is evidence for exact projection validation only and must not appear as a programmer instruction in the DOCX.
- `runtimeVisibilityOutcomes` records target Visibility values only when an accepted `shared-tree-properties` model explicitly declares a `Visibility` change in `implementation.stateOverrides[].changes[]`. Resolve each `elementId` through exactly one Bundle mapping for the state and then to an actual Unreal variable Widget. Never infer a runtime outcome from the saved readback default.

For `exclusive-panel-branches`, bind only the accepted `panelElementId` branch root for its state. Do not bind static backplates, frames, accents, decoration, or other `completeElementIds`, and leave `runtimeVisibilityOutcomes` empty unless a future accepted contract supplies explicit property overrides. Every state binding requires an accepted, in-scope, `runtimeControlled: true` element, a mapped UILayoutSpec node with `isVariable: true`, and an actual Unreal Widget with `isVariable: true`.

Projection is strict: a missing or ambiguous mapping, a failed variable gate, or absent saved Visibility for any accepted exclusive branch root or shared-tree Visibility override is an error. Do not silently drop the target and do not turn it into a state-control gap.

Both readback and handoff Visibility values use only `Visible`, `Collapsed`, `Hidden`, `HitTestInvisible`, or `SelfHitTestInvisible`. In document coverage, `stateBranchWidgetIdentifiers` contains exclusive branch roots only, while `stateOutcomeWidgetIdentifiers` contains shared-tree target Widgets. A `State branch` row never includes Visibility; a `State outcome` row contains the accepted target Visibility. Final DOCX validation rejects explicit saved/readback/Designer Visibility prose, any branch row with `visibility=`, and any exclusive branch-root Widget paired with its saved value.

Four intentionally omitted contracts are neither inferred nor emitted as gaps:

- generated content data source, owner, or refresh strategy;
- runtime parameter type, default value, or update timing;
- event/callback name or payload;
- list item data structure.

`contentPolicy` records all four categories as `forbidden`. The Schema and semantic validator reject fields that attempt to add them. A purpose such as `由程序填充` or `由程序控制` is allowed.

## Output naming and destination

Accept a single `SystemFolder` parsed from every production target asset path. Name the document `<YYYYMMDD>_UGame<SystemFolder>界面说明.docx` using the output date.

In `UIProgramHandoff.output`, store only:

```json
{
  "rootEnvironmentVariable": "NEXTGAME_UI_PROGRAM_DOCS_ROOT",
  "fileName": "20260810_UGameRole界面说明.docx"
}
```

Never resolve or serialize the environment variable value in the handoff.

## DOCX verification

The default presentation layer is the neutral, built-in NextGame UMG document template at `../../artifact-template-nextgame-umg/assets/reference.docx`. Read [program-document-template.md](program-document-template.md) before generating a document. The template controls page geometry, styles, section order, tables, asset-detail blocks, and pagination; it never supplies production facts. Replace every placeholder from the verified handoff and generated content contract, omit empty conditional sections, and never carry example-system text into the output.

WidgetTree diagrams are navigation aids, not program-interface requirements. When they are included, derive them in the NextGame-specific stage only from the validated readback fields `widgetName`, `classPath`, `parentWidgetName`, and `isVariable`. Do not expose raw readback to free-form document generation, and do not project saved Visibility or other static Designer properties into either diagrams or prose.

Before `$documents` generates the DOCX, run `prepare_program_document_contract.py` with the exact current `--requirement`, `--bundle`, `--readback`, and `--build-acceptance` paths. It reruns the complete current-source and acceptance gate, rechecks the handoff binding, binds the verified handoff hash, and emits the complete identifier inventory plus canonical semantic relationship statements. Comparing only an old handoff with an old acceptance is not sufficient after any source file changes. Give `$documents` only the verified handoff and this derived content contract. The human-readable document must include every canonical statement verbatim in a labeled trace appendix; scattered identifiers do not prove their asset, Widget, EntryClass, state-control, or state-branch relationship.

The strategy-aware `program-document-content.json` and `document-verification.json` contracts use version `0.2`. `render-evidence.json` remains version `0.1` because its rendering provenance and page shape are unchanged.

After `$documents` generates the DOCX, create `render-evidence.json` in a fresh empty page directory. The validator performs the authoritative headless conversion itself: current DOCX to a fresh canonical PDF, then that PDF to final `page-<N>.png` files through `pdftoppm`. The evidence binds the current DOCX SHA-256, executable LibreOffice and Poppler versions, the canonical PDF, render time, and the hash, byte size, and dimensions of every page. `document-verification.json` then binds that evidence, the handoff, the DOCX, exact semantic statements, and the explicitly reviewed pages. Final validation reconverts the current DOCX and rerenders every page before comparison. It requires:

- the same exact current Requirement, Bundle, Readback, and `ui-build-acceptance.json` through required `--requirement`, `--bundle`, `--readback`, and `--build-acceptance` arguments in both final document-verification commands; the complete four-input gate is rerun and the acceptance hash and identity must match `UIProgramHandoff.sources.buildAcceptance`;
- exact handoff filename;
- the DOCX to be physically beneath the user-level `NEXTGAME_UI_PROGRAM_DOCS_ROOT` value without serializing that resolved path;
- every program Widget name, collection ID/Widget name/`EntryWidgetClass`, state model/axis/state ID, control ID/kind/high-level description/target state, and accepted runtime state Widget name;
- every generated semantic relationship statement, including target asset paths, each state branch identity and default flag, and every accepted explicit `State outcome` target Visibility; saved Designer/readback Visibility values remain excluded;
- a successful real headless LibreOffice conversion, not only a `soffice --version` string;
- render evidence whose canonical PDF and authoritative pages still match a fresh conversion of the current DOCX;
- `reviewedBy`, timezone-aware `reviewedAt`, and an explicit reviewed filename list covering every rendered page exactly once before `allPagesReviewed: true` can be emitted.

The four excluded contract categories do not participate in identifier coverage. A narrow bilingual policy scan also rejects those details if a document-generation step adds them independently of the safe handoff.
