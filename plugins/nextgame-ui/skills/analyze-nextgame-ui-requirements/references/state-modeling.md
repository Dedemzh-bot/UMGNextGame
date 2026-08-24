# Evidence-Backed UI State Modeling

## Contents

1. [State scope](#state-scope)
2. [Evidence hierarchy](#evidence-hierarchy)
3. [State axes](#state-axes)
4. [High-level control inputs](#high-level-control-inputs)
5. [Component-family inference](#component-family-inference)
6. [Implementation strategy](#implementation-strategy)
7. [Composite Panel contract](#composite-panel-contract)
8. [State matrix and review](#state-matrix-and-review)
9. [Instance assignment integrity](#instance-assignment-integrity)
10. [Examples](#examples)

## State scope

Model visual and structural states that affect UMG composition. Do not design business rules, events, or code-side transitions. A state model describes what each state contains and which branch is initially visible; project code later decides when to switch it.

Infer only states supported by the supplied image, written requirement, project rule, or explicitly designated reference asset. Do not automatically add `hover`, `pressed`, `disabled`, `loading`, or other conventional states.

## Evidence hierarchy

Record one or more evidence sources for every state:

- `observed`: the exact state is directly visible in a supplied source.
- `cross-instance`: multiple instances in the same UI reveal variants of one component family.
- `project-reference`: a designated existing UI demonstrates the pattern.
- `inferred`: the state is a reasoned interpretation without direct confirmation.

Direct user text and hard project rules are recorded as ordinary claim evidence in UIRequirementSpec even though they are not state-source values.

Prefer same-image cross-instance evidence when inferring selected/unselected tabs. It supports a family relationship but does not prove hover, disabled, or transition behavior.

## State axes

Use only these independent axes in version 0.1:

| Axis | Typical values |
| --- | --- |
| `selection` | selected, unselected |
| `interaction` | idle, hover, pressed |
| `availability` | enabled, disabled, locked |
| `data` | populated, empty, loading |
| `progress` | empty, current, complete |
| `visibility` | shown, hidden, collapsed |
| `mode` | feature-specific display modes supported by evidence |

Name a default state per axis. Do not create `selected-hover-disabled` or another cross-axis state unless the source explicitly depicts or requires that combination. Keep combination behavior unresolved when it matters but lacks evidence.

## High-level control inputs

For each newly synthesized state model, record at least one `controlInputs` entry describing why that axis may choose one or more target states. This is requirement intent for program handoff, not a Lua, C++, Blueprint, or event API.

| `kind` | Use when evidence supports |
| --- | --- |
| `user-interaction` | a player action such as selecting a tab or pressing a control |
| `data-condition` | a visual state derived from whether relevant UI data is present or meets a condition |
| `program-state` | an owning feature mode or application state selects the visual branch |
| `external-state` | another system or world condition selects the visual branch |
| `unspecified` | the trigger category is not established by current evidence |

Each entry has a stable `id`, one same-model `axisId`, a plain-language `description`, one or more `targetStateIds` from that axis, and supporting `evidenceIds` and `claimIds` already attached to the state model. In an accepted requirement, every supporting claim must be accepted. Keep `unspecified` valid and non-blocking for UMG construction; the documentation handoff reports it as a gap for program follow-up.

Under `analysisPolicy.stateControlInputRequired`, every state model with a `user-interaction` control input must also contain at least one in-scope element with `kind: button` and `familyId` equal to that model's `componentFamilyId`. The element is the structural `Btn` input owner for that component family. An unrelated Button elsewhere in the screen or asset cannot satisfy this relationship. The Button requirement does not authorize Blueprint, Lua, C++, event, callback, or state-switching behavior.

Do not add event or callback names, payloads, runtime parameter types/defaults/update timing, list-item data structures, or generated-content source/ownership/refresh strategy. Those are outside this 0.1 control-intent contract.

## Component-family inference

Treat instances as one family when they share purpose, placement pattern, base composition, and repeated visual grammar. Differences may then reveal states or instance data.

Do not merge instances when their responsibilities, interaction, or child structures indicate separate components. Similar color alone is weak evidence.

For a navigation example with one emphasized tab and three plain tabs:

1. record all four as instances of one navigation-tab family;
2. classify label text as instance data;
3. classify emphasis, decoration, size, and typography differences as candidate selection-state evidence;
4. model only selected and unselected when no other state is visible;
5. retain any uncertain difference as a proposed or unresolved claim.

## Implementation strategy

Choose `shared-tree-properties` only when both states have the same nodes and hierarchy and differ in a small, coherent property set, such as one tint or one image resource.

Choose the composite Panel strategy `exclusive-panel-branches` when any of these is true:

- node count or hierarchy differs;
- several property classes change together, such as font, size, geometry, background, and decoration;
- a graphic exists in only one state;
- state variants require independent adaptive layout;
- forcing one shared tree would create many nullable nodes or brittle property overrides.

Do not use two complete panels for a simple tint, opacity, or image-resource change on an otherwise identical tree. Record why the selected strategy is proportionate.

## Composite Panel contract

The build planner should realize a confirmed `exclusive-panel-branches` tab state model approximately as:

```text
BtnTab
`-- PanelTabContent
    |-- PanelSelected       [Is Variable, SelfHitTestInvisible]
    |   |-- ImgBackground
    |   |-- ImgAccent
    |   `-- TxtLabel
    `-- PanelUnselected     [Is Variable, Collapsed]
        |-- ImgBackground
        `-- TxtLabel
```

Rules:

- Let a Button directly own a CanvasPanel when that CanvasPanel already supplies internal placement and layering. Do not insert an Overlay without a shared-bounds, independent-alignment, or multilayer purpose.
- Bind the Button element to the same component family as the state model; downstream its node mapping must resolve specifically to UILayoutSpec role `input.button`.
- Mark both state branch panels `Is Variable` because project code must control their visibility.
- Use `SelfHitTestInvisible` for the active passive branch so the owning Button remains interactive.
- Use `Collapsed` for an inactive passive branch by default. Use `Hidden` only when it must retain layout allocation, and fill that branch's `preserveLayoutReason`. Never attach `preserveLayoutReason` to a `Collapsed` or active branch.
- Keep fixed decoration inside the branch that owns it. Do not mark every child variable merely because the branch changes.
- Do not implement state-switching Blueprint, Lua, or C++ logic.
- Preserve independent adaptive layout for each branch when that difference justified the composite strategy.

The requirement model describes full state composition. The downstream UILayoutSpec describes the initial realized WidgetTree, including both confirmed branches and their initial visibility. These branch rules are distinct from ordinary passive display: a passive widget that is currently displayed uses `SelfHitTestInvisible`; `Hidden` and `Collapsed` are for intentionally inactive state branches.

## State matrix and review

Present a compact row for every component-family/axis combination:

| Family | Axis | State | Source | Confidence | Status | Strategy |
| --- | --- | --- | --- | --- | --- | --- |

Classify each row as:

- observed or cross-instance evidence;
- proposed inference;
- unsupported and intentionally omitted.

State which variant is default and show node/property differences. A row with low confidence or high-impact ambiguity remains unresolved and cannot enter the build handoff.

When the user confirms an inferred state, record the user decision and promote its claim to accepted. Confirmation does not create other conventional states by implication.

## Instance assignment integrity

Use `stateAssignments` only for component-family members whose instance presentation is selected from modeled axes. An assignment references exactly one state from each exclusive axis, and the same requirement element may appear in at most one assignment across all state models.

For a `data-driven` family owned by a dynamic `LuaListView` or `LuaTileView`, screenshot instances are runtime preview data rather than static requirement elements. Keep their observed selected/unselected presentation as evidence for the family state model, leave `stateAssignments` empty, and build both supported state branches in the reusable entry asset. Static-repeat families still require assignments for each modeled visible instance.

Downstream lowering is bidirectional: every assignment needs exactly one matching state-handling operation, and every operation with `stateHandling` must target a mapping that names exactly one uniquely assigned requirement element. The target mapping's `stateRefs`, the operation's `stateHandling.stateRefs`, and the requirement's `axisStateIds` must be identical.

## Examples

### Composite selection state

Evidence shows the selected tab has an extra underline, larger label, different panel dimensions, and a distinct background. The unselected tab lacks the underline. Use two full panels because nodes, typography, geometry, and decoration change together.

### Shared-tree progress state

Evidence shows three magazine cells with identical frames and content nodes; only fill tint and opacity distinguish "already loaded" from "loaded this time." Use one cell tree with state-driven properties unless another source shows structural differences.

### Unsupported interaction states

One still image shows selected and unselected tabs, but nothing depicts hover or pressed styling. Record those interaction states as unsupported and omit them. Do not copy selection styling into hover merely because both emphasize a tab.

### Data state versus fixed repetition

Several stat rows with different labels and values may be runtime collection entries, while repeated decorative ticks behind a graph may be fixed images. Use purpose and runtime-count evidence; visual repetition alone does not imply a LuaListView.
