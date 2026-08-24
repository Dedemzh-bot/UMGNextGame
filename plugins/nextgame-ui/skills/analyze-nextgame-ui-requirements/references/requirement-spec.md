# UI Requirement Contracts

## Contents

1. [Contract ownership](#contract-ownership)
2. [RequestPacket 0.1](#requestpacket-01)
3. [AgentFindings 0.1](#agentfindings-01)
4. [Canonical identifiers](#canonical-identifiers)
5. [Evidence and claims](#evidence-and-claims)
6. [UIRequirementSpec 0.1](#uirequirementspec-01)
7. [Status transitions](#status-transitions)
8. [Integrity rules](#integrity-rules)

## Contract ownership

Use three distinct contracts:

- `UIRequirementSpec 0.1` is authoritative for what the UI means, which states exist, which evidence supports them, and what the user approved.
- `UILayoutSpec 0.2` is authoritative only for one buildable WidgetTree.
- `UIBuildBundle 0.1`, `0.2`, or `0.3` is authoritative for the multi-asset build graph, requirement-to-node mappings, integration operations, and actual results, according to the closed routing below.

Never hide uncertainty in UILayoutSpec geometry or node names. Resolve it in UIRequirementSpec first.

JSON Schema files are authoritative for syntax. This document defines field meaning and merge behavior.

Route downstream bundles by accepted implementation meaning, never by convenience:

- `UIBuildBundle 0.1` is the original non-reuse contract and has no `reuseRelations`.
- `UIBuildBundle 0.2` is a closed historical contract for the former single `SlotContent` surface. Do not emit it for newly approved shared reuse, and never give it nonempty Designer `parameterOverrides`.
- `UIBuildBundle 0.3` is required when an accepted plan selects a shared Registry entry, extends or migrates the standard dual layers, creates a system-specific Parent Class child, nests that child in a later host, or carries Registry-verified Designer parameters.

Requirement analysis decides whether reuse is selected and records the evidence, size/state hard checks, semantic-content comparison, accepted claims, affected asset boundaries, and any explicitly authorized shared-asset mutation. It does not fabricate Registry activation or parameter facts. The downstream `0.3` Bundle must normally reload the plugin-authoritative Registry, or a Bundle-local content-addressed immutable Registry snapshot, validate either with the plugin-authoritative Schema, and bind those actual facts. The only pre-identity exception is the non-executable, Bundle-local `planned-bootstrap` declaration for a new project-common layout asset; it must be replaced by an actual Registry binding before any consumer executes or the Bundle can finalize.

For a new shared Widget, the Bundle uses `add-dual-layer-slots` with `SlotDown`, `SlotUp`, no migration object, and `legacyPreservedNames: []`. For an existing Widget with the former standard slot, it uses `migrate-existing-standard-slot`, renames `SlotContent` to `SlotUp`, adds `SlotDown`, and carries the exact evidenced legacy-name array. Do not globally infer `Slot1`: the current bag prototype preserves it because its Registry entry proves it, while another prototype may have no extra legacy slot.

Both branches retain the same strict layer contract: `SlotDown` is first and below every root sibling; `SlotUp` is last and above every root sibling; both are full fill, Auto Size false, variable, and `SelfHitTestInvisible`. Each derived child records both inherited surfaces independently as empty or as one direct semantic Panel. Candidate Registry entries may support a planned, explicitly authorized extension, but only an actual active entry with verified dual-slot evidence is executable after activation.

Do not infer instance parameters from Lua calls, screenshots, or a Bundle's own values. `widget-tree-instance.parameterOverrides` in `0.3` must resolve against the actual Registry `widget-tree-instance` generation mode: unique declared names, required/default semantics, and concrete `valueKind` checks apply. A Registry mode marked parameterless accepts only an empty override list; an unverified parameter contract cannot enter build instructions.

## RequestPacket 0.1

`request-packet.json` preserves raw input without interpretation.

| Field | Meaning |
| --- | --- |
| `version` | Contract version; use `0.1`. |
| `requestId` | Stable lowercase identifier used as the output directory name. |
| `inputDigest` | SHA-256 digest used to detect stale downstream findings. |
| `userRequest` | Original user text fragments and their language. Preserve, do not summarize, this input. |
| `sources` | Images, written briefs, project-rule files, designated reference assets, or other raw sources. |
| `targetHints` | Intended system/folder, design canvas, asset kind, mode, candidate paths, and production authorization. These remain minimum input hints until synthesis; derived child or entry assets belong in `assetPlan`, not back in the packet. |
| `projectRuleRefs` | Applicable project-rule paths, optional sections, and whether each reference is mandatory. |

Each source has `sourceKey`, `kind`, `locatorKind`, and `description`. Apply locator semantics as follows:

| `locatorKind` | Required locator and integrity data |
| --- | --- |
| `local-file` | Absolute filesystem `path` plus `contentSha256`; validation reads and hashes the file. |
| `inline` | Nonempty `content`; use this for preserved text embedded in the packet. |
| `unreal-object` | `/Game/...` object `path`, packet-relative `snapshotPath` with no `..`, and `contentSha256` of that readback snapshot. |

For every image source, also retain `path` and `imageSize`. Original image dimensions are measurement evidence, not automatically the target design canvas. Every `projectRuleRefs.path` must match a hashed `project-rule` source. Mirror `userRequest.originalText` exactly and in order with one `user-text` source per entry.

For every rule or reference asset, record whether the user designated it, project policy requires it, or analysis discovered it. Do not treat arbitrary neighboring assets as precedents.

If analysis uses a mutable shared-widget Registry, first copy it into the RequestPacket output tree as an immutable, content-addressed `local-file` source and hash that copy. An accepted packet must not point at the live Registry: later candidate activation legitimately changes the live file and must not invalidate the packet that authorized the build.

Compute `inputDigest` as the canonical SHA-256 of `userRequest`, `sources`, `targetHints`, and `projectRuleRefs`; `version` and `requestId` are not digest material. Use `unknown` for unresolved RequestPacket `assetKind` or `mode`. Run:

```text
python scripts/validate_request_packet.py <request-packet.json>
```

The only optional RequestPacket validator argument is `--schema <request-packet.schema.json>`.

## AgentFindings 0.1

All discovery, analysis, and review agents use the exact same envelope. A run's unique findings path and `inputDigest` provide provenance; do not add ad hoc envelope fields.

| Field | Meaning |
| --- | --- |
| `version` | Use `0.1`. |
| `requestId` | Must equal the RequestPacket request ID. |
| `agentRole` | One allowed orchestration role. |
| `inputDigest` | The originating RequestPacket `inputDigest`, for every role. |
| `contextDigest` | Canonical SHA-256 of the normalized context; required only for round-two and review roles. |
| `sourceScope` | Declared RequestPacket source keys available to this agent. Evidence may cite only these keys. |
| `findings` | Categorized conclusions with local IDs, evidence references, confidence, and impact. |
| `evidence` | Factual source records with local IDs, source keys, kind, description, confidence, and optional geometry. Image geometry records use `sourceDimensions`, `pixelBounds`, normalized `bounds`, and `measurementMethod`. |
| `questionCandidates` | Material unknowns; subagents record them but never ask the user. |

All AgentFindings entities use `local-*` IDs. First-round agents omit `contextDigest` and `subjectRefs`; their `sourceScope` must exactly equal the role's isolated relevant sources: images for `visual-structure`, user text for `text-requirements`, and project rules/assets for `project-pattern`. A first-round role with no relevant source emits empty `findings`, `evidence`, and `questionCandidates`.

Round-two and review agents receive the same normalized canonical context, set its canonical hash in `contextDigest`, and bind every finding to existing canonical IDs with nonempty `subjectRefs`. Their evidence still cites only source keys declared in `sourceScope`. No agent may write another agent's findings file.

Allowed role names stay stable: `visual-structure`, `text-requirements`, `project-pattern`, `state-modeling`, `data-adaptation`, `asset-decomposition`, `state-visual-review`, `schema-feasibility-review`, and `coverage-review`. The normalizer and sole Synthesizer are orchestration owners, not AgentFindings roles.

Use finding categories to carry the proposed entity or conclusion type: `region`, `component-family`, `element`, `collection`, `runtime-field`, `responsive-intent`, `state`, `asset-boundary`, `explicit-requirement`, `exclusion`, `acceptance-criterion`, or `risk`. Record contradictory sources as separate evidence plus a `risk` finding; do not add a custom conflicts field.

For `visual-structure`, use `category: element` for every independently authored visible layer that affects silhouette, grouping, hierarchy, or state: backplates/backgrounds, frames/borders, separators/rails/bands, accents/corners/glows, icons, and progress tracks/fills. Detector fragments are not automatically independent layers. When dots, lines, corners, color islands, or connected components share one semantic graphic, state, runtime control, draw layer, adaptation, and resource responsibility, measure and report the complete graphic while retaining the primitive evidence. Keep art distinct from text and input elements even when they share bounds. Medium/high-impact visual element findings require measured image evidence with `sourceDimensions`, `pixelBounds`, normalized `bounds`, and `measurementMethod`.

Validate first-round and context-aware findings with the exact forms below:

```text
python scripts/validate_agent_findings.py <findings.json> --request-packet <request-packet.json>
python scripts/validate_agent_findings.py <findings.json> --request-packet <request-packet.json> --context <normalized-context.json>
```

`--request-packet` is required. Optional schema overrides are `--schema <agent-findings.schema.json>` and `--request-schema <request-packet.schema.json>`; `--context` is prohibited for first-round roles and required for the other six roles.

## Canonical identifiers

Use stable, opaque IDs; keep display labels separate. Recommended prefixes:

| Entity | Prefix | Example |
| --- | --- | --- |
| Evidence | `ev.` | `ev.image.tab-selected` |
| Claim | `cl.` | `cl.tab.composite-state` |
| Region | `reg.` | `reg.primary-navigation` |
| Component family | `fam.` | `fam.navigation-tab` |
| Element | `el.` | `el.tab-label` |
| Collection | `col.` | `col.role-roster` |
| Runtime field | `rt.` | `rt.character-name` |
| State model | `sm.` | `sm.navigation-selection` |
| State control input | `ctrl.` | `ctrl.navigation-selection` |
| Planned asset | `asset.` | `asset.role-roster-entry` |

IDs must not depend on child order or transient agent numbering. The normalizer records an alias for every source-local identity it consumes into the canonical spec. If identity is uncertain, retain separate IDs and an unresolved possible-equivalence claim.

`normalization.findingsInputs` records `agentRole`, a RequirementSpec-relative `findingsRef` with no `..`, `findingsSha256`, and the parent `inputDigest`. Each of the six round-two/review inputs also requires a relative `contextRef` with no `..` and the context file's byte hash in `contextSha256`; the three first-round inputs must omit both context fields. At accepted status there must be exactly one findings input for each of the nine allowed roles; the normalizer and Synthesizer are not additional roles.

Give every local ID from every linked findings document exactly one trace outcome. `normalization.aliases` keys it by `agentRole`, `findingsRef`, and `localId`, then points to a resolving `canonicalId`. If it is deliberately not represented in the canonical spec, put the same identity in `normalization.discardedLocalIds` with a nonempty `reason`. An identity cannot be both aliased and discarded, and accepted normalization cannot have an empty trace.

When `analysisPolicy.staticVisualCoverageRequired` is `true`, normalization is type-preserving for every `visual-structure` element finding: it must alias to a canonical `uiModel.elements` item. Do not alias a visual element to a containing region, family, evidence record, or text/control element, and do not discard it as an implementation detail. Multiple primitive findings may alias to the same canonical image only through a measured whole-graphic synthesis decision; proximity or label similarity alone is not sufficient. A genuinely independent excluded layer remains a canonical element with `inBuildScope: false` and an evidence-backed `scopedOutReason`, so downstream coverage can distinguish exclusion from omission.

## Evidence and claims

### Evidence

An AgentFindings evidence record contains:

- `localId` and `sourceKey`;
- `kind`: `direct-observation`, `user-statement`, `cross-instance`, `project-reference`, or `inference`;
- a factual `description` and confidence;
- optional normalized image `bounds`. For a top-level visible region or independently positioned visible/interactive element, geometry is mandatory: include source dimensions, pixel bounds, normalized bounds, and a measurement method. This preserves source measurement separately from the target `2560x1440` design canvas.

After synthesis, UIRequirementSpec uses the corresponding canonical evidence kinds, including `user-requirement` rather than the AgentFindings spelling `user-statement`.

Evidence states what is observable. For example, "selected tab includes an underline and 28 px label while neighboring tabs do not" is evidence. "Use two state panels" is a claim.

An AgentFindings `finding` contains `localId`, category, statement, one or more evidence references, numeric confidence from `0` to `1`, and `low`, `medium`, or `high` impact. Status belongs only to the synthesized claim, never the preliminary finding.

### Synthesized claims

A UIRequirementSpec claim adds a canonical ID, conclusion type, review status, and referenced subjects to the normalized finding semantics. Its type is one of `explicit-requirement`, `project-rule`, `region-structure`, `component-family`, `element-structure`, `collection-behavior`, `runtime-behavior`, `responsive-behavior`, `state-behavior`, `asset-decomposition`, or `acceptance`. Confidence measures evidential support, not importance. Impact measures the cost of a wrong conclusion. A high-confidence inference remains `proposed` until the user approves it unless it directly restates a hard project rule or explicit user requirement.

## UIRequirementSpec 0.1

The sole Requirement Synthesizer owns `ui-requirement.json`.

Newly synthesized documents set `analysisPolicy.geometryEvidenceRequired`, `analysisPolicy.listPriorityRequired`, `analysisPolicy.stateControlInputRequired`, `analysisPolicy.staticVisualCoverageRequired`, `analysisPolicy.imageCompositionRequired`, `analysisPolicy.explicitPanelSlotsRequired`, `analysisPolicy.explicitImageOwnerIntentRequired`, and `analysisPolicy.designSizeModeRequired` to `true`. This activates the measurement, repeated-family, high-level state-control-intent, static-visual coverage, complete-image composition, exact owner-adaptation binding, reviewed immediate-parent Slot, and asset-kind-driven Designer Screen Size gates; older archived requirements without these policies remain readable but are not valid templates for new analysis. The newer policy properties remain optional in Schema 0.1 solely for backward compatibility; missing or `false` does not activate their gates.

### `request`

Carry forward purpose, original wording, scope, exclusions, and completion criteria. Keep source wording intact where practical; do not replace it with the synthesis summary. Preserve interaction requirements inside the relevant scope statement when present.

### `sources`

List every image, text source, project rule, and designated reference asset actually used. Preserve source IDs so evidence remains auditable.

### `target`

Record system identity, intended asset kind and path, reference canvas, mode, and production authorization. A complete NextGame screen uses `2560x1440`; child widgets and list entries use their local functional size.

A pending draft may use `null` for unresolved `system`, `systemFolder`, `mode`, `assetKind`, `designCanvas`, or `productionAuthorized`, with empty `targetAssetPaths` and an empty `assetPlan`. This is the correct representation of an unknown target. It may also keep resolved target metadata and candidate out-of-scope plans while leaving `targetAssetPaths` empty until the user accepts the multi-asset plan. RequestPacket path hints are a minimum set: once paths are resolved, the RequirementSpec must retain every hinted path but may add synthesized child or entry paths. Accepted review requires all target fields resolved, at least one target path, and target paths exactly matching in-scope asset-plan paths.

### `evidence` and `claims`

Contain the deduplicated evidence base and synthesized conclusions. Never delete a contrary record merely because synthesis selected another interpretation.

### `uiModel.regions`

Describe semantic modules, parent/child relationships, purpose, approximate normalized bounds, and responsive ownership. Region parents must resolve and form an acyclic hierarchy. Every accepted in-scope region directly below the screen region requires `geometryEvidenceId`; its bounds must match that evidence. Use `allowOverlap: true` plus `overlapReason` only for intentional overlap. Sibling top-level regions otherwise must not materially intersect. A region is not automatically a separate Widget Blueprint.

### `uiModel.componentFamilies`

Group visually or functionally equivalent instances such as navigation tabs, stat rows, or resource markers. Record shared composition and instance-level differences.

### `uiModel.elements`

Describe independently bounded visual or interactive parts: text, image, button, progress, panel, collection, or child widget. Independently positioned or interactive visible elements use `bounds` and `geometryEvidenceId`; these must match their cited image measurement. Reference its region and optional family; element parents must resolve and form an acyclic hierarchy. Do not encode decoration inside text.

With static visual coverage enabled, represent every complete fixed visual graphic as a `kind: image`, `runtimeControlled: false` element. Broad backplates and low-contrast framing remain separate when they have a different surface, layer, or adaptation responsibility. Dots, strokes, corners, and color pieces inside one icon or ornament do not become separate elements merely because the raster scanner found them separately.

When `analysisPolicy.imageCompositionRequired` is true, every in-scope image carries `imageComposition`. `groupKey` identifies one semantic graphic group. That group has exactly one `role: complete` image. A Button Backplate and fixed glyph normally use separate group keys and are each complete graphics because their surface/stretch and glyph/resource duties differ; scanner fragments inside the glyph merge into the glyph's complete image. An additional `role: layer` is a justified layer of the same graphic and requires a concrete `splitReason`: independent runtime control, state variation, adaptation, verified resource reuse, material/mask behavior, progress fill, or an accepted exception. `splitReason: independent-adaptation` requires `adaptation: independent`. Independent adaptation requires exactly one responsive intent targeted at that image element and forbids an inherited owner ID. Inherited adaptation forbids a direct image intent; with `analysisPolicy.explicitImageOwnerIntentRequired`, it records the exact accepted, in-scope ancestor-element or region intent in `ownerIntentId` and may not rely on an arbitrary reachable ancestor chosen later. A family-owned static image must descend from an explicit family member or from the root-most family-owned entry/template element. A static image that is only near a family in screen space but does not belong to its reusable composition must not receive that `familyId`.

When `analysisPolicy.explicitPanelSlotsRequired` is true, a modeled VerticalBox, HorizontalBox, or GameScrollBox element declares `layoutRole` as `container.vertical`, `container.horizontal`, or `container.game-scroll`. Every in-scope direct child carries `panelSlotIntent`, which is explicitly in the immediate-parent Slot coordinate space rather than the screen coordinate space. A flow child records `slotType: flow`, Padding, horizontal/vertical Alignment, a reason, and one closed sizing choice: `sizingBasis: content-driven` with `size.rule: Auto`, or `sizingBasis: weighted-remaining-space` with `size.rule: Fill` plus a positive weight. Alignment `Fill` affects occupancy inside the allocated Slot and does not imply weighted Box allocation; `Auto` plus Fill alignment is valid. A GameScrollBox child records `slotType: scroll`, Padding, both alignments, and a reason, but no Box Size. This preserves owner-relative placement such as a Right/Top corner inside a right-side panel even when the owning panel itself stretches with the viewport.

### `uiModel.collections`

Classify every component family with two or more members through `componentFamily.repetition`. If the instances share a composition and only their text, image/brush, number, or runtime state differs, classify it `data-driven` and create a dynamic `LuaListView` or `LuaTileView` collection plus entry asset, even if the current screenshot has a fixed visible count. `static-repeat` is only for structural differences, deliberately free-positioned instances, or pure decoration, and requires `staticRepeatReason`. For runtime collections, record the container and entry family plus the supported overflow contract: `show-all`, `scroll`, `page`, or `fixed`. Use `scroll`, `page`, or `fixed` only when the content is intentionally constrained to a viewport or capacity.

`LuaListView` and `LuaTileView` container elements are WidgetTree leaves. Never attach screenshot sample rows or cards beneath them as static `uiModel.elements`; keep visible sample count, text, brush, values, and observed selection in evidence and claims. The component family references the actual list-entry template root, while `collections.entryFamilyId` and the planned list-entry asset carry the reusable composition.

### `uiModel.runtimeFields`

List text, image, visibility, progress, state branch, collection, or other values that project code must control. This informs `Is Variable`; it does not define Lua, C++, or Blueprint behavior.

### `uiModel.responsiveIntent`

Record horizontal and vertical intent independently, protected visual areas, fixed edges, stretch axes, text growth direction, wrapping width intent, and justification rationale. A responsive intent targets exactly one region or one element. Use an element target for a decoration that adapts independently from its owner; otherwise keep the intent on the owner/region and mark the image composition as inheriting it. Measure both axes and the parent relationship before choosing edge, center, or stretch behavior; local `[0,0]` coordinates are not evidence for a screen-level top-left anchor.

`responsiveIntent` describes adaptation of the semantic owner or independently adapting element. It does not replace `panelSlotIntent`: when an element is a direct flow/scroll child, its immediate-parent Alignment and Box allocation are reviewed separately and must remain consistent with the larger adaptation decision.

### `stateModels`

Use the axes, evidence sources, composition rules, control inputs, and implementation strategies in [state-modeling.md](state-modeling.md). A `stateAssignment` targets a member of its modeled family and chooses exactly one state from every exclusive axis. Each requirement element may have at most one state assignment across the entire spec. Do not manufacture unsupported combinations.

Each new state model includes at least one `controlInputs` item. It records a high-level trigger category and description, a same-model axis, target states on that axis, and evidence/claims already attached to the model. It does not define executable transition logic or a code interface. `kind: "unspecified"` is valid when evidence does not establish the trigger; it does not block UMG construction and is carried into the program handoff as a documentation gap. Accepted requirements require accepted supporting claims.

For `exclusive-panel-branches`, every `completeElementIds` set is the complete descendant closure of its panel, including fixed backgrounds, borders, separators, and accents. Static-visual validation emits a focused composition error when any such image is omitted; the existing full-branch closure check remains the general invariant.

Do not add event/callback names or payloads, runtime parameter types/defaults/update timing, list data-item structures, or generated-content source/owner/refresh strategy to a control input.

### `assetPlan`

Describe candidate `screen`, `child-widget`, and `list-entry` assets with `assetPath`, `referenceSize`, `layoutSpecPath`, dependency IDs, zero-based contiguous `buildOrder`, scope, coverage IDs, evidence, claims, and an inventory `boundaryClassification`. `assetKind` is the sole semantic source for the Widget Blueprint Designer Screen Size mode: `screen` requires canonical `FillScreen` (the UI label “Fill Screen”), while `child-widget` and `list-entry` require canonical `Desired`. `DesiredOnScreen` is not an allowed substitute for a local control, and the Requirement must not duplicate this derived decision in another per-asset field. The source image size and `referenceSize` remain geometry contracts; neither overrides the semantic asset-kind decision. Allowed boundary classifications are `screen-root`, `entry-widget-class`, `runtime-template`, `reusable-widget`, `statically-referenced` (reserved for explicit system rules such as Fight), and `stale-candidate`. Normal non-Fight regions and screen-local collections remain ordinary semantic Panels/leaves in the screen; do not extract a functional wrapper without an accepted boundary claim and evidence. A list-entry must materialize its local `referenceSize` in UMG: the first internal structural Panel Slot has explicit non-zero width and height matching that size (or a separately verified content-driven desired size). A zero-offset full-stretch root is invalid, and a SizeBox must not be introduced solely to supply that root size. A pending spec may leave the array empty. In an accepted spec, every in-scope plan and every claim attached to it must be accepted; proposed, unresolved, or rejected decisions remain out of scope with `scopedOutReason`.

With static visual coverage enabled, the union of `coversElementIds` across in-scope assets must contain every in-scope fixed image element. This proves that each accepted backplate or decoration has a build owner; it does not prove raster fidelity, which remains a visual-review and downstream render-verification responsibility.

### `assumptions`

List proposed defaults, evidence, confidence, impact, and the consequence if wrong. Do not bury assumptions in geometry values.

### `questions`

Rank unresolved questions by impact. Include the affected claim and the safe consequence of leaving it unanswered. The coordinator presents no more than three at once.

### `acceptanceCriteria`

Express observable structural, visual, state, and validation outcomes. Do not include business logic that UMG construction does not own.

### `reviewGate`

Keep `required: true`. `acceptedClaimIds` must exactly equal claims whose `status` is `accepted`; `rejectedClaimIds` must exactly equal claims whose status is `rejected`, and the sets cannot overlap. Completed `accepted` or `rejected` reviews require `reviewedBy` and `reviewedAt`; pending reviews must not claim them.

For acceptance, set `approvedContentSha256` to the canonical SHA-256 of the complete UIRequirementSpec with only `reviewGate.approvedContentSha256` omitted. The top-level `revision` identifies that content revision. Any content edit after approval requires incrementing the revision, returning the gate to review, and computing a new digest after acceptance. The validator checks the digest and the bundle later checks both revision and digest. `reviewResolutions` closes every review finding using its exact findings file and local ID; a high-impact finding cannot remain open at acceptance.

## Status transitions

- Start explicit user requirements and hard project rules as `accepted` with direct evidence.
- Start AI interpretations as `proposed`, even at high confidence.
- Use `unresolved` when evidence is missing, contradictory, or materially ambiguous.
- Set `blocksBuild: true` on every high-impact unresolved claim and every high-impact open question. A lower-impact item may also block when explicitly necessary.
- On explicit user confirmation, promote the addressed `proposed` or `unresolved` claim to `accepted` and record decision provenance.
- On rejection, set the claim to `rejected`, list it in `rejectedClaimIds`, and retain its audit history; rejected claims never enter build scope.
- Never implicitly promote a claim because downstream construction would be easier.

An accepted gate cannot retain an unresolved claim or open question that is high-impact or has `blocksBuild: true`. Regardless of whether the gate is `pending` or `accepted`, every claim attached to an in-scope build-relevant entity must already have `accepted` status; a pending draft may still carry an explicit production screen target when that target is supported only by accepted claims. At accepted status, the claim's `subjectRefs` and the entity's `claimIds` must also point to each other.

## Integrity rules

- Every claim references existing evidence.
- Every entity reference and alias resolves exactly once.
- Every region/element parent chain is acyclic, and every requirement element has at most one state assignment.
- Every linked findings local ID is exactly one of aliased or explicitly discarded in strict validation.
- With static visual coverage enabled, each visual-structure element finding aliases to a canonical element without discard or type collapse; several primitive findings may share one canonical complete-image identity only through measured whole-graphic evidence, and medium/high findings cite measured geometry.
- With image composition enabled, every in-scope image belongs to a group with exactly one complete image; every extra layer has an accepted evidence-backed split reason, and every independently adapting image has exactly one element-targeted responsive intent.
- Every in-scope fixed image is covered by an in-scope asset, family-owned fixed images descend from a member or entry/template root, and full state branches include their static visual descendants.
- One source observation may support multiple claims, but a claim cannot cite itself or another claim as evidence.
- Only the sole Synthesizer edits the authoritative requirement file.
- Reviewer output is advisory AgentFindings, never a competing authoritative spec.
- `unresolved` and unapproved `proposed` claims are excluded from UILayoutSpec and executable UIBuildBundle operations.
- Preserve independent state axes rather than materializing an unsupported Cartesian product.
- Resolve every state control input to an axis and target states in the same model and to evidence/claims attached to that model; keep `unspecified` non-blocking.
- Preserve RequestPacket source IDs and original user intent through every later contract.

Validate synthesis and its linked findings with:

```text
python scripts/validate_requirement_spec.py <ui-requirement.json> --request-packet <request-packet.json> --check-findings-files
```

`--request-packet` is required. Optional schema overrides are `--schema <ui-requirement-spec.schema.json>` and `--request-schema <request-packet.schema.json>`. `--check-findings-files` is the strict review/handoff mode: it rehashes each findings and context file, loads all nine AgentFindings, validates each against the RequestPacket and its recorded context, verifies role consistency, and requires the loaded local-ID set for each file to equal its alias/discard trace exactly.
