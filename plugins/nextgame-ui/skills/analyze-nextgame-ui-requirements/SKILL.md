---
name: analyze-nextgame-ui-requirements
description: Analyze NextGame UI reference images, written requirements, project rules, and existing reference Widget Blueprints before UMG construction. Use when an AI coding agent must infer semantic regions, component families, runtime collections, responsive intent, or evidence-backed hidden and composite visual states; coordinate independent requirement-analysis agents; and produce a user-reviewed UIRequirementSpec for later conversion into UILayoutSpec and UIBuildBundle artifacts. This skill is analysis-only and must not create or modify Unreal assets.
---

# Analyze NextGame UI Requirements

Turn raw UI evidence into one reviewable requirement contract before invoking `$build-nextgame-umg`. Keep requirement meaning, build instructions, and actual Unreal results in separate artifacts.

## Required input

Start from `request-packet.json`, validated against [request-packet.schema.json](assets/request-packet.schema.json). Require:

- a stable `requestId` suitable for a directory name;
- at least one reference image or a concrete written UI requirement;
- the user's original wording, scope, exclusions, interactions, and completion criteria when supplied;
- target system, intended asset, and production authorization when known;
- paths to applicable project rules and explicitly designated reference assets.

Use the source locator contract exactly: `local-file` uses an absolute filesystem `path`; `inline` carries `content`; `unreal-object` uses a `/Game/...` `path` plus a snapshot path relative to the packet directory. Hash local files and Unreal snapshots in `contentSha256`. Mirror each `userRequest.originalText` item, in order, with one `user-text` source. Do not invent missing user intent. `targetHints.assetKind` and `mode` may be `unknown` while the request is pending.

Run `python scripts/validate_request_packet.py <request-packet.json>` before spawning agents. This also verifies source hashes, text mirroring, rule-source links, and the canonical `inputDigest`; stop and fix contract errors first.

## Read the contracts

- Read [requirement-spec.md](references/requirement-spec.md) before starting any analysis run. It defines RequestPacket, AgentFindings, canonical IDs, evidence, claims, and UIRequirementSpec semantics.
- Read [state-modeling.md](references/state-modeling.md) whenever the image contains repeated variants, tabs, selection, empty/filled content, progress, availability, visibility, or another possible state.
- Read [protected-stretch-and-state-defaults.md](references/protected-stretch-and-state-defaults.md) for every analysis run. Apply its screen-expanding protected-region rule and its default-unselected rule to fight and non-fight systems.
- Read [static-visual-coverage.md](references/static-visual-coverage.md) for every screenshot-driven run. Use its independent raster inventory and review gates to catch low-contrast plates, frames, separators, accents, and repeated entry decoration before synthesis, while keeping detector primitives distinct from complete semantic-image decisions.
- Read [build-bundle-handoff.md](references/build-bundle-handoff.md) before finalizing the requirement file. It defines the review gate and what the downstream planning and build agents may consume.
- Read [shared-widget-discovery.md](references/shared-widget-discovery.md) for every analysis run. Validate and query the plugin-level shared Widget Blueprint registry after checking the current SystemFolder; executable selection is `active`-only; use size and state as hard compatibility checks and content as semantic similarity; model `class-settings-parent-class`, the two independent inherited extension surfaces `NamedSlot("SlotDown")` and `NamedSlot("SlotUp")`, and later host nesting as distinct but composable relations.
- Read [portable-multi-agent-runtime.md](references/portable-multi-agent-runtime.md) when the host is not Codex, or when orchestration is being delegated through an external runner. Runtime messages coordinate work; validated files remain authoritative.
- Read applicable rules from the sibling `build-nextgame-umg/references/` directory, but do not generate a UILayoutSpec during this skill.

## Safety boundary

This skill is read-only with respect to Unreal and project UI assets.

- Never create, update, compile, save, rename, move, or delete a Widget Blueprint.
- Never call a mutating Unreal MCP tool. The `unreal-editor` dependency may be used only to inspect an explicitly designated existing reference asset when filesystem evidence is insufficient.
- Never produce Lua, C++, Blueprint behavior, or state-switching logic.
- Write analysis artifacts only under `Saved/CodexUIRequirements/<request-id>/`.
- Do not proceed to UMG construction in the same analysis step. Always stop at the user review gate.

## Workflow

### 1. Prepare isolated evidence packets

Create role-specific, read-only views of the validated RequestPacket. Give every first-round agent the raw source material it needs and a unique output path under `findings/`; do not give it another agent's findings, expected answer, hidden-state hypothesis, or intended implementation. In each `AgentFindings`, keep `inputDigest` equal to the RequestPacket digest and set `sourceScope` to the exact isolated source keys.

### 2. Run round-one independent discovery

Spawn these three agents in parallel:

1. **Visual structure** -- inspect images for regions, component families, independent text bounds, repeated elements, hierarchy, same-image variant evidence, and every independently authored visual layer that affects silhouette, grouping, hierarchy, or state. Treat detected dots, line segments, corners, color islands, and connected components as coverage evidence rather than automatic Widget boundaries. First decide whether they form one complete non-text graphic with one state, runtime-control, draw-layer, adaptation, and resource responsibility; when they do, emit one measured `category: element` finding for that complete graphic and retain the primitive evidence behind it. Emit genuinely independent backplates/backgrounds, frames/borders, separators/rails/bands, accents/corners/glows, icons, progress tracks/fills, and other separately controlled or adapted layers as distinct element findings instead of burying them in a region or prose description. Record source dimensions, pixel bounds, normalized bounds, and measurement method for every top-level visible region, independently positioned control, and medium/high-impact element finding. Do not infer requirements from prose.
2. **Written requirements** -- extract only explicit purpose, scope, exclusions, names, paths, interactions, and acceptance conditions. Do not make visual guesses.
3. **Project patterns** -- read project rules and designated reference UMG assets; inspect relevant assets in the target SystemFolder, then validate and query the plugin-level `assets/shared-widget-registry.json`; report naming, region, collection, responsive-layout, state-structure, and compatible reuse precedents. Treat observed assets as evidence, not authority over explicit rules. Distinguish user-selected reuse from AI-proposed candidates, and never infer Lua inheritance from a Widget Blueprint parent relationship.

Each agent must emit only an `AgentFindings` document. It must not author the authoritative requirement file, a final WidgetTree, UILayoutSpec, or UIBuildBundle.

Validate every first-round findings file with `python scripts/validate_agent_findings.py <findings.json> --request-packet <request-packet.json>` before normalization. First-round files must omit `contextDigest`; if their role has no relevant source, use an empty `sourceScope` and empty output arrays. Reject stale digests and cross-request output.

### 3. Normalize identities

Run one normalizer after all three findings exist. Assign stable canonical IDs for evidence, claims, regions, families, elements, collections, runtime fields, state models, and planned assets. For every local ID in every findings file, record exactly one outcome: an alias tuple of `agentRole`, relative `findingsRef`, `localId`, and `canonicalId`, or a `discardedLocalIds` record with the same role/ref/local identity and a nonempty reason. Preserve the entity type of every visual-structure `element` finding: alias it to `uiModel.elements`, never to a region/family/evidence record and never to `discardedLocalIds`. Several visual primitive findings may alias to one canonical image only when measured whole-graphic evidence proves that they share state, runtime control, draw layer, adaptation, and resource responsibility; similarity of labels, color, or proximity alone is insufficient. If a genuinely independent visible layer is outside scope, retain it as a canonical element with `inBuildScope: false` and an evidence-backed `scopedOutReason`.

### 4. Run round-two focused analysis

Give the same normalized evidence base and canonical IDs to three parallel agents:

1. **State modeler** -- identify supported state axes, defaults, complete compositions or property deltas, evidence source, implementation strategy, and at least one high-level control intent per state model. Describe only trigger category and intent; do not propose event/callback names, payloads, parameter contracts, list-item schemas, or generated-content ownership/refresh details. Use `unspecified` when evidence cannot establish the trigger.
2. **Data and adaptation analyst** -- decide runtime variables, dynamic collections, overflow behavior, anchoring, growth direction, text wrapping, justification, localization adaptation, and per-axis child Slot intent. For proposed VerticalBox, HorizontalBox, and scroll children, use the measured child size, desired-size behavior, parent direction, and owning-region adaptation to decide horizontal/vertical alignment explicitly. Persist the parent container as `element.layoutRole` and every direct child's reviewed immediate-parent decision as `element.panelSlotIntent`; do not leave Size, Padding, or Alignment for build planning to infer again. Keep main-axis Slot Size separate from alignment: content-driven VerticalBox/HorizontalBox children use `Auto` even when their horizontal or vertical alignment is `Fill`; use Size `Fill` only to consume weighted remaining space. For decorative images, decide whether adaptation is inherited from the owner or independent on each axis, while retaining its local placement inside the immediate parent Slot. When repeated entries share one composition and differ only by text, image/brush, number, or runtime state, classify them as `data-driven` and require a `LuaListView` or `LuaTileView` even when the reference shows a fixed visible count. A static repeat is allowed only for structural variation, deliberate free placement, or pure decoration, with an explicit reason.
3. **Asset decomposition analyst** -- propose the screen and any explicitly evidenced asset boundaries, dependencies, and build order. Classify every planned asset by semantic scope independently of its Designer mode: a complete screen is `screen`, while a local reusable control or collection entry is `child-widget` or `list-entry`. Apply the project basename rule before any size-mode analysis. A target basename beginning `umg_` always receives `mode: FillScreen`, `basis: project-umg-rule`, and empty decision `evidenceIds`; do not analyze it for `Desired`. Bind that rule through `claimId` to a reviewed `asset-decomposition` claim citing a project-rule source and explicitly naming `project-umg-rule` and `FillScreen`. Only a basename beginning `uw_` enters the evidence analysis: use `viewport-filling` with `FillScreen`, use `content-sized-local` with `Desired` only when positive evidence proves a locally hosted control whose root produces non-zero Desired Size, or use `verified-reference` after actual `project-asset`/`unreal-object` Editor readback. If a `uw_` result is unclear, use `fallback-unclear` with `FillScreen`. A non-`umg_`/non-`uw_` legacy basename also uses that conservative fallback and is not allowed to opt into `Desired`. `assetKind` never determines this mode, and `DesiredOnScreen`, `Custom`, and `CustomOnScreen` remain outside the contract. For non-Fight systems, keep screen-local regions and collections in the screen; every list-entry, runtime-template, or reusable-widget boundary requires accepted evidence and an inventory classification. Before proposing a new asset for that boundary, compare current-SystemFolder assets and active shared-registry entries by purpose, capabilities, interface, Parent Class, normalized dual-Slot contract, size hard check, state-model hard check, and semantic content similarity. Selected common reuse produces a system-specific Parent Class child; classify every additive visual as below or above the shared base content, map it to `SlotDown` or `SlotUp`, and give each used Slot exactly one direct semantic Panel. Both shared Slots are `SelfHitTestInvisible`; both inherited bindings remain empty when no additive content exists, and a later host/EntryClass relation is recorded separately.

Round-two agents also emit `AgentFindings`. They may add or challenge claims but may not edit the normalized base or another agent's output. Pass the same canonical context file with `--context`; each file keeps the RequestPacket `inputDigest`, sets `contextDigest` to the canonical hash of that context, and binds every finding through `subjectRefs` to existing canonical IDs. Preserve that context as JSON next to the requirement artifacts so the later `findingsInputs.contextRef` and `contextSha256` can be verified. Validate with `python scripts/validate_agent_findings.py <findings.json> --request-packet <request-packet.json> --context <normalized-context.json>`.

### 5. Synthesize and challenge

Use exactly one Requirement Synthesizer to merge all findings into `ui-requirement.json` conforming to [ui-requirement-spec.schema.json](assets/ui-requirement-spec.schema.json).

For every newly synthesized requirement, write `analysisPolicy: { "geometryEvidenceRequired": true, "listPriorityRequired": true, "assetBoundaryRequired": true, "standardSystemBoundaryRequired": true, "stateControlInputRequired": true, "staticVisualCoverageRequired": true, "imageCompositionRequired": true, "explicitPanelSlotsRequired": true, "explicitImageOwnerIntentRequired": true, "designSizeModeRequired": true }`. Standard non-fight screens default to semantic Panels and screen-local Lua collections; a `uw_*` boundary requires one of the four permitted boundary reasons plus evidence. These flags make the geometry, repeated-family, asset-boundary, high-level state-control-intent, static-visual coverage, complete-image composition, exact owner-adaptation binding, reviewed immediate-parent Slot, and per-asset evidence-driven Designer Screen Size gates machine-enforceable while preserving validation compatibility for pre-policy archived requirements.

- Mark direct user requirements and hard project rules `accepted`.
- Mark evidence-backed AI interpretations `proposed`.
- Mark insufficient, conflicting, or materially ambiguous conclusions `unresolved`.
- Retain conflicting evidence and provenance; do not silently select the convenient answer.
- Preserve measured geometry exactly: a canonical region or visible element with `geometryEvidenceId` must use the cited normalized bounds. Never use a child asset's local reference size to infer a screen host rectangle. Top-level visible regions require measured geometry before acceptance.
- Represent each complete fixed visual graphic as `uiModel.elements.kind: image` with `runtimeControlled: false`; keep text and hit targets separate from art. Put `imageComposition` on every in-scope image. Use `role: complete` for the one image that expresses a whole semantic graphic. A Button Backplate and its glyph are normally two different semantic `groupKey` values, each with its own `role: complete`, when surface/stretch and fixed-glyph responsibilities differ; only the glyph's internal dots, strokes, corners, or color pieces merge into the glyph's one complete image. Use `role: layer` only for a justified layer of the same graphic when evidence proves independent runtime control, state variation, adaptation, resource reuse, material/mask behavior, progress fill, or an accepted exception; record the exact `splitReason`. Every in-scope static image must appear in an in-scope `assetPlan.coversElementIds` list.
- Give a decoration an element-targeted `responsiveIntent` only when it adapts independently of its owner. Otherwise set `imageComposition.adaptation: inherit-owner` and record the exact accepted region or ancestor-element intent in `ownerIntentId`; do not leave the owner for build planning to guess. In both cases, use `panelSlotIntent` whenever the element is a direct child of a modeled VerticalBox, HorizontalBox, or GameScrollBox: owner inheritance controls the larger region while the Slot intent preserves local Left/Center/Right/Fill and Top/Center/Bottom/Fill placement. Independent intent must be chosen per axis from measured position, size, owning-region behavior, and protected-content needs; do not default images to a top-left anchor merely because their local source coordinates begin there.
- Place family-owned backplates, borders, separators, accents, and other static images below an explicit family member or the root-most family-owned entry/template element. Include every static visual descendant in a full state branch's `completeElementIds`; do not let a label-only or control-only branch stand in for the visible composition.
- Classify every repeated component family with two or more instances as `data-driven` or `static-repeat`; an omitted classification is not a static-repeat decision. `data-driven` creates a collection plus entry asset plan.
- Give every state model at least one `controlInputs` item. Bind it to an axis and target states in that model and to evidence/claims already attached to that model. Treat `unspecified` as a non-blocking program-handoff gap, not a reason to invent code-side transition details.
- When a `controlInputs` item uses `kind: user-interaction`, add at least one in-scope `kind: button` element whose `familyId` matches that state model's `componentFamilyId`. This is the family-owned `Btn` hit target; a Button from another family does not satisfy the contract, and no event or switching logic is implied.
- Do not convert `proposed` or `unresolved` claims into build instructions.
- Give every in-scope `assetPlan[]` in the new policy a `designSizeModeDecision` with `mode`, `basis`, a concrete `reason`, canonical `evidenceIds`, and one `claimId`. The claim must exist in the same asset's `claimIds`, use `type: asset-decomposition`, name the asset in `subjectRefs`, and cover every decision evidence ID. `umg_*` is the sole basename hard rule: it requires `project-umg-rule`, `FillScreen`, empty decision evidence, and a project-rule-backed claim. `uw_*` forbids that basis and uses `viewport-filling`, `content-sized-local`, `verified-reference`, or `fallback-unclear`; only `content-sized-local` requires `Desired`. Nonstandard legacy names require `fallback-unclear` and `FillScreen`. `assetKind` and source-image dimensions do not decide the mode.
- Present every human-selected or AI-proposed shared-widget candidate with provenance, size/state hard-check results, content-similarity result, registry status, and evidence at the review gate. A human selection wins candidate priority but never grants shared-asset mutation authority. A registry `candidate` is non-executable. If either standard dual-layer NamedSlot is missing or violates root order, full-fill, variable, or strict ZOrder requirements, list the exact shared-asset mutation and wait for explicit approval. A new common asset may add the pair directly; an existing asset must preserve every observed legacy NamedSlot and saved consumer binding unless pre-save validation proves a lossless migration. Never assume all assets have a legacy `Slot1`; record concrete legacy names from readback. Lower new executable reuse through `UIBuildBundle 0.3`; keep Bundle 0.2 as the closed historical single-Slot contract. If a relation is still not representable, stop before Editor mutation instead of recreating the control.

Run three independent review passes over the draft: state/visual adversarial review, schema/reference/buildability review, and requirement-to-node coverage review. The coverage reviewer must compare the raster overlay, every medium/high candidate crop, and an overlapping full-image contact sheet against canonical static-image elements, family/entry roots, state-branch membership, and asset coverage. Reviewers emit findings only. The same sole Synthesizer adjudicates them and writes the revised authoritative file. Every reviewer finding must receive a `reviewResolutions` record (`resolved`, `accepted-deviation`, or `rejected`); high-impact findings cannot remain `open` when the gate is accepted.

The final normalization must contain exactly one `findingsInputs` record for each of the nine schema roles: the three discovery roles, three focused-analysis roles, and three review roles. Each record carries a relative `findingsRef`, its file SHA-256, and the parent RequestPacket `inputDigest`. The six context-aware records additionally carry a RequirementSpec-relative `contextRef` and that context file's SHA-256; first-round records must omit both fields.

Run `python scripts/validate_requirement_spec.py <ui-requirement.json> --request-packet <request-packet.json> --check-findings-files` after synthesis and after every review-driven change. In this strict mode the validator rehashes and deeply validates all linked AgentFindings and context JSON, checks each linked role/context pairing, and requires every discovered local ID to be represented exactly once by `aliases` or `discardedLocalIds`. Fix all errors before presenting the user review. A pending spec may retain `null` target fields, empty `target.targetAssetPaths`, and an empty `assetPlan`; do not fabricate a destination merely to make the draft look buildable.

### 6. Present the mandatory user review

On every initial analysis run, present:

- the module and planned-asset list;
- a state matrix showing observed, inferred, and unsupported states;
- proposed assumptions with evidence and confidence;
- up to three highest-impact unresolved questions.

Only the primary coordinating agent may ask the user questions. Do not ask from subagents. Stop after presenting the review, even when there are no questions. The user must explicitly confirm or amend the analysis before any downstream build begins.

After review, increment `revision` for revised content, update claim statuses, and record the decision provenance. `reviewGate.acceptedClaimIds` and `rejectedClaimIds` must exactly mirror claim statuses. An accepted review also requires `reviewedBy`, `reviewedAt`, a fully resolved nonempty target/asset plan, and `approvedContentSha256` computed over the complete spec except that hash field. Any later content change invalidates that approval and requires a new revision, review, and digest. Unanswered `unresolved` claims remain excluded from the build handoff; high-impact or explicitly `blocksBuild` unknowns prevent acceptance.

## Outputs

Keep all artifacts together:

```text
Saved/CodexUIRequirements/<request-id>/
|-- request-packet.json
|-- findings/
|-- contexts/                 # normalized JSON inputs for six context-aware roles
|-- ui-requirement.json
|-- ui-build-bundle.json       # downstream planning/build ownership
|-- layouts/                   # downstream UILayoutSpec ownership
|-- verification.json          # downstream Unreal verification ownership
|-- unreal-widget-readback.json # downstream post-save Unreal ownership
|-- ui-build-acceptance.json   # primary Agent, only after later user confirmation
|-- ui-program-handoff.json    # downstream documentation ownership
`-- program-document-content.json # downstream documentation ownership
```

This skill owns `request-packet.json`, `findings/`, `contexts/`, and `ui-requirement.json`. Do not fabricate the downstream files. The planning/build workflow creates them only after user approval.

There are two separate user gates. This skill's review accepts the requirement before any Unreal mutation; it cannot also accept a result that does not exist yet. After a production build is compiled, saved, verified, previewed, and captured in the final Unreal readback, the build workflow must present that concrete result and end its turn. Only a later direct user message accepting that presented result allows the primary coordinating Agent to create and validate `ui-build-acceptance.json` and start formal documentation. A request made before construction to “run the whole flow” is not post-build acceptance.

## Completion criteria

Before requesting review, confirm that:

- every claim cites evidence and has a confidence, impact, and review status;
- every referenced canonical ID resolves;
- every findings-local ID is aliased or explicitly discarded, with no overlap or undocumented extra ID;
- every visual-structure `element` finding aliases type-preservingly to a canonical element, and every medium/high such finding has measured image geometry;
- every in-scope static image is owned by an in-scope asset and, when family-owned, is nested below a family member or entry/template root; full state branches list all of their static visual descendants;
- region and element parent relationships are acyclic;
- repeated elements are classified as fixed decoration, component-family instances, or runtime collections;
- state axes remain independent and unsupported cross-product states were not invented;
- every state model records evidence-backed high-level control intent, with unresolved trigger categories represented as non-blocking `unspecified` rather than invented APIs;
- every user-interaction state model in a policy-enabled requirement owns an in-scope same-family Button element for downstream `input.button` mapping;
- each element has at most one `stateAssignment` across the requirement;
- unapproved `proposed` and all `unresolved` conclusions did not enter `assetPlan` as executable instructions;
- screen targets use the project `2560x1440` design contract while local child widgets retain functional dimensions;
- every in-scope asset has the correct semantic `assetKind` plus a basename-compliant `designSizeModeDecision`: hard-rule `FillScreen` for `umg_*`, analyzed mode for `uw_*`, and conservative `FillScreen` fallback for ambiguous or legacy names;
- no Unreal mutation or UILayoutSpec generation occurred; every planned/discovered asset has an inventory classification and stale candidates are blocked from acceptance.
- every proposed new reusable/list-entry/runtime-template boundary was checked against the current SystemFolder and the validated shared registry; executable sources are active; size and state hard checks plus semantic-content comparison are recorded; every selected chain retains `class-settings-parent-class`, the separate `SlotDown`/`SlotUp` `inherited-named-slot-content` records, and host nesting/EntryClass as separate relations; both inherited Slots are explicitly `empty|panel`, every used Slot has one direct semantic Panel, selected prototypes are not internally duplicated, and authored/Lua/dynamic-reference relations are described separately.
