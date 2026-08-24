# UIBuildBundle Handoff

## Contents

1. [Handoff boundary](#handoff-boundary)
2. [Approval gate](#approval-gate)
3. [UIBuildBundle version routing and semantics](#uibuildbundle-version-routing-and-semantics)
4. [Requirement-to-build mapping](#requirement-to-build-mapping)
5. [Build order and integration](#build-order-and-integration)
6. [Verification and deviation records](#verification-and-deviation-records)
7. [Documentation handoff](#documentation-handoff)

## Handoff boundary

The analysis skill owns:

- `request-packet.json`;
- all independent and review findings;
- the normalized context JSON referenced by context-aware findings inputs;
- the user-reviewed `ui-requirement.json`.

The downstream planning/build workflow owns:

- `ui-build-bundle.json`;
- files below `layouts/`;
- Unreal mutation, compilation, saving, preview, and `verification.json`;
- normalized `unreal-widget-readback.json` for a requirement-driven production build.

After that build result has been presented, the primary coordinating Agent may own `ui-build-acceptance.json`, but only in a later turn after a direct user message explicitly accepts the concrete UMG result. For an accepted requirement-driven production build with that current authorization, the documentation workflow then owns `ui-program-handoff.json`, its derived `program-document-content.json`, and the UMG explanation DOCX. The DOCX is written beneath the user-configured documentation root rather than fabricated during analysis.

Do not create placeholder success results during analysis. A missing downstream file means the work has not reached that phase.

## Approval gate

Start build planning only when UIRequirementSpec contains:

- `reviewGate.status: accepted`, `reviewedBy`, `reviewedAt`, and a valid `approvedContentSha256` for the current `revision`;
- no unresolved claim or open question that is high-impact or has `blocksBuild: true`;
- accepted claims for every executable asset-plan decision;
- a fully resolved target, at least one target path, and at least one in-scope asset-plan item.

Validate the requirement in strict `--check-findings-files` mode before planning. This gate must successfully load and validate all nine AgentFindings and each recorded context file, and prove complete alias/discard coverage for their local IDs; hashes alone are insufficient.

An unanswered unresolved claim may remain in the requirement file, but it must be excluded from layouts and operations. If exclusion would make the requested UI materially incomplete, stop instead of silently choosing a default.

This requirement approval is only the pre-build gate. It cannot be reused as the post-build result acceptance and cannot pre-authorize documentation, even if the initial request asks for the complete analysis, construction, and document workflow.

## UIBuildBundle version routing and semantics

`ui-build-bundle.json` links one approved requirement revision to many build artifacts and actual results.

| Field | Meaning |
| --- | --- |
| `version` | Select `0.1`, `0.2`, or `0.3` only through the closed routing rules below. |
| `bundleId` | Stable bundle-local ID. |
| `requirement` | `path`, file `sha256`, `requestId`, `revision`, `reviewStatus`, and `approvedContentSha256` for the accepted requirement. |
| `assets` | One record per in-scope plan: bundle ID, `assetPlanId`, path/kind/size, layout path/hash, translated dependencies, order, and status. |
| `nodeMappings` | One mapping per UILayoutSpec node with `assetId`, `layoutNodeId`, `mappingKind`, requirement refs, accepted claim IDs, and optional state refs. |
| `crossAssetOperations` | `child-widget-integration`, `entry-widget-class`, or `instance-state-initialization` operations. |
| `execution` | Overall execution `status` and the exact `buildOrderAssetIds`, plus optional timestamps. |
| `verification` | Overall status with nested `checks` and structured `deviations`. |

There are no top-level `requestId`, `requirementSpec`, `reviewApproval`, `buildOrder`, `layouts`, `mappings`, `integrationOperations`, or `deviations` fields. The schema rejects them. The requirement path alone is insufficient: `requirement.sha256` hashes the actual file, while `revision` and `approvedContentSha256` bind the approved content.

Version routing is closed:

- `0.1` is the original non-reuse multi-asset contract. It has no `reuseRelations` field.
- `0.2` is the archived single-`SlotContent` reuse contract. It remains readable for historical bundles, but is not a template for new shared-widget work. Its `widget-tree-instance.parameterOverrides` must be empty because UnrealReadback 0.2 cannot prove saved Designer overrides.
- `0.3` is required for new executable shared reuse. It records the dual inherited layers, Registry-backed activation, current Parent Class child, later host nesting, and any verified Designer parameter overrides.

For `0.3`, `shared-prototype-extension.namedSlots` is a closed discriminated union:

- A new shared Widget uses `operation: add-dual-layer-slots`, exactly the ordered `SlotDown`/`SlotUp` pair, and `legacyPreservedNames: []`. A new prototype has no legacy surface, and `legacyStandardMigration` is forbidden.
- An existing shared Widget with the former standard slot uses `operation: migrate-existing-standard-slot`, the same ordered pair, `legacyStandardMigration` (`SlotContent` to `SlotUp`, with pre-save validation), and its evidenced `legacyPreservedNames`. The array is not globally hard-coded to `Slot1`: it may be empty for another Widget, while the linked Registry makes the current `uw_common_bag_item` value exactly `["Slot1"]`.

Both branches require `SlotDown` as the root's first direct child and strict lowest ZOrder, and `SlotUp` as its last direct child and strict highest ZOrder. Both are full-fill `NamedSlot` Widgets with Auto Size false, `Is Variable` true, and `SelfHitTestInvisible`. Their names cannot also appear in `legacyPreservedNames`.

The Registry link is executable evidence, not a caller-authored assertion. Final validation accepts the authoritative `assets/shared-widget-registry.json` shipped with the validating nextgame-ui plugin, or an explicitly content-addressed immutable snapshot directly below the Bundle's `registry-snapshots/` directory and named `shared-widget-registry.<registrySha256>.json`. Both forms are validated with the plugin's authoritative Registry Schema; an arbitrary sibling JSON or sibling Schema is not trusted. The Bundle identity, entry, operation, legacy names, interface hash, and reuse-contract hash must match the loaded entry.

A candidate Registry entry may appear while `post-extension-activation.status` is `required`. While that gate is required, every transitive consumer of the shared prototype must remain `planned`, whether the prototype is backed by a Registry candidate or a planned-bootstrap snapshot. A `widget-tree-instance` may cite an unverified nesting mode and unverified parameter contract only as a non-executing plan when its `parameterOverrides` is empty, every transitive consumer remains planned, and the Bundle lifecycle is not finalized. Any override, advanced consumer, completed execution, or passed verification removes this exception and requires a linked active Registry contract with a verified nesting mode and verified-or-none parameter contract. A final `status: verified` relation must run linked-file validation and find the actual entry `active` with `extensionSlotsContract.status: verified`; changing only the Bundle's `entryStatus` fields cannot pass. Likewise, `preverified` is reserved for an already active and verified entry. Do not use `--skip-linked-files` for final activation or executable nesting.

`class-settings-parent-class.inheritedSlots` independently records `SlotDown` and `SlotUp` as `empty` or `panel`. A used slot has exactly one direct semantic Panel; full fill is the default, special adaptation needs accepted evidence, and an unused slot stays empty. Later `widget-tree-instance` nesting remains a separate relation.

For `0.3` `widget-tree-instance.parameterOverrides`, load the actual Registry entry's unique `generationModes[mode=widget-tree-instance]`. The mode and parameter contract must be verified. Override names are unique and declared, concrete values match each declared `valueKind`, and every required parameter without a Registry default is supplied. `parameterContractStatus: none` requires an empty override list; an absent or unverified contract cannot be bypassed with self-reported Bundle fields.

A `widget-tree-instance` whose source is a `reuse-only` empty-tree Parent Class child has no UILayoutSpec nodes to cite. For a full host rectangle, its `childSizingCompatibility` uses `mode: inherited-reuse-only-full-stretch`; for a direct HorizontalBox/VerticalBox child, it uses `mode: inherited-reuse-only-flow-slot` plus `allocation: content-driven|weighted-remaining-space`; for a GameScrollBox child it uses `mode: inherited-reuse-only-scroll-slot`. Every inherited mode covers both axes and names the exact `parentRelationId` plus `prototypeExtensionRelationId`. The flow mode requires `Auto` with `sizingStrategy: content-driven`, or `Fill` with a positive weight and `sizingStrategy: weighted-remaining-space`; it does not impose Fill alignment or zero Padding. The scroll mode requires `sizingStrategy: scroll-slot`, no Box Size field, and preserves the reviewed Padding and both alignments. The validator accepts an inherited proof only when the source has no layout identity, the unique Parent Class relation leaves every inherited extension Slot empty, and the prototype has one matching extension relation. Never invent `sourceLayoutNodeIds` for a reuse-only asset. Layout-backed child integrations continue to use the existing node-citation modes unchanged.

## Requirement-to-build mapping

Map every accepted, build-relevant item to its implementation:

- region ID to the owning container node;
- element ID to the asset and stable UILayoutSpec node ID;
- runtime-field ID to the variable WidgetTree node;
- state ID to state branch nodes or shared property set;
- collection ID to container asset/node and entry asset;
- acceptance criterion ID to verification checks.

Contract coverage is exact for in-scope `asset`, `region`, `element`, `collection`, `runtime-field`, `responsive-intent`, `state`, and `acceptance-criterion` entities. The first seven use assets/node mappings; acceptance criteria use verification checks. When linked layouts are checked, every UILayoutSpec node must have exactly one `nodeMappings` record. Use `mappingKind: generated-support` for a structural helper, but still trace it to real accepted requirement and claim IDs; the schema does not support origin-free helpers.

Coverage is semantic, not an ID-presence exercise. An accepted region must map to a screen node whose normalized `rect` equals the requirement region `bounds`. A collection requires `mappingKind: collection`, must name its `containerElementId`, and must resolve to a `collection.*` node. A state requires a `composite-state` mapping of at least one element in its declared composition; a bare state ID is insufficient.

When `analysisPolicy.designSizeModeRequired` is true, do not add a duplicate mode field to `UIBuildBundle.assets`. Read the expected Widget Blueprint Designer Screen Size only from the linked accepted `assetPlan[].designSizeModeDecision.mode`; the Requirement validator has already bound that decision to its reviewed `asset-decomposition` claim and, for `verified-reference`, to actual project-asset Editor readback. With linked-file validation enabled, every layout-backed asset must carry that exact canonical value in `profile.designSizeMode`; a missing value, `DesiredOnScreen`, `Custom`, `CustomOnScreen`, or any mismatch is rejected. `assetKind` remains an independent Bundle-to-layout type binding: `screen` maps to `profile.assetKind: screen`; `child-widget` maps to `profile.assetKind: child-widget` without `listRole: entry`; and `list-entry` maps to `profile.assetKind: child-widget` with `listRole: entry`. None of those type bindings overrides the analyzed mode. A `reuse-only` asset intentionally has no UILayoutSpec; its expected final actual Unreal mode still comes from its Requirement decision, including the explicit `FillScreen` fallback when its analysis was unclear.

A linked layout whose accepted decision is `Desired` must also prove that its root content produces a non-zero size. A fixed proof is one direct child of the root with `slotLayout.autoSize: false`, equal minimum/maximum point anchors, and positive `offsets.right`/`offsets.bottom`. If that fixed proof is absent, one direct child of the root must instead carry `contentDrivenSize: { "verified": true, "measuredDesiredSize": [<positive>, <positive>], "evidenceId": "..." }`; that evidence ID must be present in the same Requirement `designSizeModeDecision.evidenceIds`. A proof placed on the root itself, an unverified/zero measurement, or evidence attached only to the broader asset/claim is insufficient.

When `analysisPolicy.imageCompositionRequired` is true, every accepted in-scope image element has exactly one realization: either one `visual.image` node, or one `widget-tree-instance` relation when the complete graphic is supplied inside a verified shared control. Every new `visual.image` node maps back to exactly one accepted image element. Never add a local image merely to duplicate a relation-backed shared visual. Do not map one complete image to several point/line fragments or authorize an extra fragment through `generated-support`. An element-targeted responsive intent shares the same unique realization with its target image: a direct node copies both axes into `adaptiveLayout`; a relation-backed image copies them into the nested instance Slot. With `explicitImageOwnerIntentRequired`, every inherited image's exact `ownerIntentId` also maps once to its owner node or verified nested-control relation, and that realization's two-axis adaptation must match the accepted owner intent. The image realization must remain a descendant of that owner in the same WidgetTree or through a proven child/reuse integration chain. A relation used in that proof records the exact `parentWidgetName` and content-derived `parentTreePath`, including under Canvas or Overlay; merely covering both IDs or naming an unrelated parent is insufficient.

When `analysisPolicy.explicitPanelSlotsRequired` is true, every newly generated linked layout sets `profile.explicitPanelSlots: true`. Lower each accepted element `panelSlotIntent` onto the same child node mapping: `flow` becomes an exact `flowSlot`, `scroll` becomes an exact `scrollSlot`, and the actual parent node must map the declared parent element and use its declared `layoutRole`. Main-axis Box Size `Auto` remains independent from Alignment `Fill`, and accepted content-driven children use Auto. Generated support children that do not have their own semantic element still receive an explicit Slot based on their accepted owner/evidence; they do not justify changing a reviewed element Slot.

For every policy-enabled state model with `controlInputs.kind: user-interaction`, map every in-scope same-family `kind: button` requirement element to its actual UILayoutSpec node. Every mapping that names one of those Button elements must resolve to role `input.button`; a generated support Panel or an unrelated Btn elsewhere cannot stand in for the family-owned trigger. This is a structural hit-target contract only and does not add behavior logic.

Do not map unapproved proposed or unresolved claims. If a node must embody such a claim, return to requirement review first.

## Build order and integration

Order assets by dependency:

1. collection entry assets;
2. child widgets and collection-container modules;
3. owning screen assets;
4. cross-asset integration operations;
5. compile, save, and verification.

Record explicit dependencies instead of relying only on numeric order. Reject cycles.

### Child-widget placement contract

Every `child-widget-integration` is a real construction operation, never a placeholder replacement. It must use `integrationStrategy: create-child-widget` and carry `placementContract`:

- `hostNormalizedRect` and the matching design-pixel `hostSize` for the exact target node;
- `slot` container type, horizontal/vertical alignment, and four-value padding;
- `zOrder`; and
- `sizingStrategy` (`fill-host`, `fixed-host-rect`, `content-driven`, `weighted-remaining-space`, or `scroll-slot`); and
- `childSizingCompatibility`, with both axes and cited source-layout nodes proving one of: exact host/child reference-size equality, a source root with executable dual-axis stretch, adaptive flow/stretch on each axis, or an explicit ScaleBox.

For `fill-host`, the slot is `Fill`/`Fill` with zero padding. `content-driven` means a HorizontalBox/VerticalBox Slot allocates by Desired Size with Size `Auto`; `weighted-remaining-space` means Size `Fill` with a positive weight. In those two flow cases, Alignment and Padding remain independent reviewed values and must not be overwritten by the allocation choice. The target-node rect, host rect, host size, and z-order must agree with the linked target UILayoutSpec. A `source-root-stretch` proof requires either a zero-offset stretch `slotLayout` on both axes or `adaptiveLayout.horizontal/vertical: stretch`; `rect: [0,0,1,1]` alone is not evidence. `source-flow-axis` must independently prove horizontal and vertical flow/stretch. A fixed child canvas cannot be placed in a differently sized host by merely naming `fill-host`; it must instead fit exactly or supply this verifiable stretch/flow/ScaleBox evidence. This prevents a correctly built child blueprint from being dropped into an unmeasured or overlapping host.

`entry-widget-class` is the only integration exempt from a placement contract. It must use `integrationStrategy: set-entry-widget-class` and source a `list-entry` asset. `ReplaceWidgetWithTemplate`, ad-hoc replacement, or similarly unspecified strategies are not production operations and are rejected.

Examples of integration operations:

- assign a generated entry class to `LuaListView` or `LuaTileView`;
- place a confirmed child-widget asset in its owning screen region;
- set the initial visibility of confirmed composite state branches;
- verify a production destination matches the approved target.

Every cross-asset operation must point to different source/target assets, the target must directly depend on the source, the target node must exist and have a mapping, and all operation claims must be reviewed and accepted.

### Child-instance state handling

When a target child instance mapping has `stateRefs`, its cross-asset operation requires `stateHandling`. A state assignment must resolve through exactly one such operation. Conversely, every state-handled operation must target a mapping whose `requirementRefs` name exactly one requirement element with one unique `stateAssignment`. The assignment's `axisStateIds`, target mapping's `stateRefs`, and `stateHandling.stateRefs` must be identical. Use one of four strategies:

- `owning-screen-state-tree`: a `child-widget-integration` into a screen whose asset plan covers the state models; complete state branch mappings live in the target screen, not in the source child.
- `static-variant-asset`: a `child-widget-integration` from a child-widget asset dedicated to exactly the assigned state refs; its plan and mappings cover the complete assigned compositions without mutually exclusive siblings.
- `instance-parameter`: an `instance-state-initialization` operation with `parameterName`.
- `runtime-dependent`: use only when the preview cannot realize the runtime state. Supply `previewExclusionReason` and `deviationId` referencing an already accepted deviation. This excludes only that runtime-dependent preview; it does not waive state mapping, coverage, or other verification.

Do not include Lua, C++, Blueprint graph behavior, events, or state-switching logic. `Is Variable`, child-widget placement, and list-entry class assignment are structural UMG operations and remain in scope.

## Verification and deviation records

For each asset, record:

- UILayoutSpec validation result;
- Unreal build execution result;
- compile and save status;
- actual asset path;
- WidgetTree and key-property readback;
- normalized `unreal-widget-readback.json` coverage for assets, actual Widgets, Bundle node mappings, `Is Variable`, collection EntryClass, and state Visibility;
- preview or screenshot path when available;
- requirement coverage result;
- warnings and deviations.

Use `verification.checks` records with type `schema`, `compile`, `save`, `widget-tree`, `key-properties`, `preview`, or `deviation`; each carries status/details, optional `assetId` and `artifactPath`, and `requirementRefs`/`claimIds` arrays. When those arrays are nonempty, they may cite only in-scope requirements and reviewed accepted claims with a matching evidence chain. Acceptance-criterion coverage requires the relevant refs. Put deviations only in `verification.deviations`, with affected requirement refs/assets, expected, actual, reason, impact, and status. High-impact deviations must be `accepted`; every accepted deviation requires `approvedBy` and `approvedAt`.

A `preview` check marked `passed` must include an auditable `previewAudit`, not just free text and a screenshot path. The audit identifies the target asset and window, viewport, effective canvas pixel size/aspect, an explicit `modalOrMultipleWindowContamination: false`, geometry comparisons, an empty `unauthorizedOverlaps` array, and the preview artifact SHA-256. Screen previews must use a `2560x1440` viewport and matching aspect. Each geometry comparison names a requirement region and target layout node, records expected/actual normalized rects, a maximum delta, and `passed`; all mapped screen regions must be included. The artifact hash is checked against `artifactPath` in a final linked-files validation.

When `analysisPolicy.staticVisualCoverageRequired` is `true`, passed verification also requires one passed preview check for every asset that owns an accepted in-scope static image element. Its `previewAudit.visualLayerComparisons` must cover every such element mapped to that asset and bind it to an actual `visual.image` layout node with `present`, `merged`, or `accepted-deviation`. `merged` cites the retained image requirement; `accepted-deviation` cites an accepted structured deviation. An accepted in-scope visual cannot be marked excluded at build time, and a global visual-similarity score cannot replace the per-element evidence.

Verification must compare actual Unreal state against both UILayoutSpec and accepted requirement IDs. A valid UILayoutSpec does not prove that the requested state family or asset decomposition was implemented.

For every requirement-driven production build, passed `widget-tree` and `key-properties` checks must use `artifactPath` to reference readback validated against [unreal-widget-readback.schema.json](../../document-nextgame-umg/assets/unreal-widget-readback.schema.json). Keep the Bundle 0.1 top-level shape unchanged. Populate actual fields through post-save official Unreal MCP reads and record `acquisition.method: official-unreal-mcp`; an NxUEAgent fallback is allowed only as `nxue-agent` with a nonempty reason or `mixed` with a reason for every field-level fallback. Never use a UILayoutSpec, build plan, or Bundle mapping as actual readback.

Record deviations rather than rewriting historical requirements. Every deviation includes affected IDs, expected result, actual result, reason, impact, and user approval when material.

Keep bundle lifecycle fields consistent:

- `execution.status: completed` requires `startedAt` and `completedAt`, all assets `built` or `verified`, and no pending verification check.
- `verification.status: passed` additionally requires completed execution, every asset `verified`, every check `passed`, and every medium/high-impact deviation accepted.
- Do not mark execution completed or verification passed merely because layout generation succeeded.

Displayed passive components use `SelfHitTestInvisible`. Inactive passive state branches use `Collapsed`, or `Hidden` only when preserving layout allocation is intentional and the requirement branch supplies `preserveLayoutReason`. Ordinary passive components left `Visible` remain noncompliant. Interactive owners and containers that preserve child interaction are evaluated separately.

Validate before Editor mutation:

```text
python scripts/validate_build_bundle.py <ui-build-bundle.json>
python scripts/validate_requirement_coverage.py <ui-build-bundle.json>
```

Use `--requirement <ui-requirement.json>` only to override `bundle.requirement.path`. `validate_build_bundle.py` accepts `--schema`, `--requirement-schema`, and `--skip-linked-files`; the coverage command uses `--bundle-schema`, `--requirement-schema`, and `--skip-linked-files`. Do not use `--skip-linked-files` for a final pre-build or final verification pass because it skips requirement/layout hashes and linked UILayoutSpec checks.

## Documentation handoff

The formal requirement-driven production chain is:

```text
raw evidence -> requirement review -> build, verification and normalized Unreal readback -> present concrete UMG result -> later user acceptance -> program handoff -> DOCX render and visual QA
```

Run the documentation stage by default only for an accepted requirement-driven production build. Skip it when the user explicitly excludes documentation. Prototype work and legacy standalone `UILayoutSpec` work do not produce the formal program-facing UMG explanation document.

After build verification and final readback succeed, the build Agent must first present all built asset paths, preview screenshots, the actual WidgetTree and key-property checks, plus every warning and deviation, then end that turn. It must not create a handoff or DOCX. A later direct user message must explicitly accept that presented result. Only then may the primary coordinating Agent create and validate `ui-build-acceptance.json` and invoke `$document-nextgame-umg`.

The documentation workflow must read all three authoritative data sources:

1. approved `UIRequirementSpec` for purpose and state meaning;
2. `UIBuildBundle` for asset relationships and requirement mappings;
3. schema-valid `unreal-widget-readback.json` for what Unreal actually contains.

It must also require the independent `ui-build-acceptance.json` as the fourth authorization input. That artifact binds the exact current Requirement, Bundle, Readback, reviewed asset IDs, and reviewed asset paths; it is authorization evidence, not another source of UI meaning or implemented facts. The requirement-review decision cannot substitute for it, and the documentation Agent may never create or infer it. Document-content generation and final DOCX verification each re-read all four current files and rerun the complete gate; an old handoff and old acceptance cannot authorize documentation after a source changes.

Any asset mutation, compile/save rerun, Bundle change, preview replacement or re-verification, or Readback change invalidates the prior acceptance. Regenerate the finalized Bundle and Readback as applicable, present the revised result, and wait for a new direct user confirmation. Prototype and legacy standalone builds, and production tasks whose user explicitly excludes documentation, do not create this acceptance artifact.

Never generate a final UI explanation document from the screenshot or UILayoutSpec alone. Doing so would lose inferred state meaning, approval provenance, cross-asset relationships, and implementation deviations.

`$document-nextgame-umg` first validates the current acceptance against the three bound files, then creates and validates `UIProgramHandoff 0.3` against [ui-program-handoff.schema.json](../../document-nextgame-umg/assets/ui-program-handoff.schema.json). It then derives `program-document-content.json` with the exact safe identifiers and semantic relationship rows. Give only those two verified artifacts to the available `documents:documents` capability. Include every relationship row verbatim in a trace appendix. The strict validator then fresh-converts the current DOCX to a canonical PDF and authoritative pages, binds them in `render-evidence.json`, requires explicit inspection of every page, and finishes with `document-verification.json`. It documents program-controlled Widget variables, dynamic collection-to-entry relationships, high-level state-control intent and target branches, and Widgets or collections populated by project code. High-level state control may retain the accepted trigger category or intent, but it must not invent event-interface details.

The project deliberately excludes generated/populated content source, owner, and refresh strategy; runtime parameter types, defaults, and update timing; event/callback names and payloads; and collection item schemas. Omit those details without treating them as handoff gaps. Static Designer configuration that program code does not read or change is also outside the handoff. A WidgetTree diagram may be retained for component and hierarchy lookup, but static nodes or properties visible in that diagram remain outside the program contract.

If DOCX capability is unavailable or output/render verification cannot complete, preserve the validated program handoff, report the documentation stage as incomplete, and do not claim that the complete production chain succeeded.
