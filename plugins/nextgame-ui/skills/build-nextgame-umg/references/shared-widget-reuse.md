# Shared Widget Blueprint reuse boundary

Before planning or creating a project Widget Blueprint, validate and read the plugin-level [SharedWidgetRegistry](../../../assets/shared-widget-registry.json). This registry is not the component catalog: it describes complete existing Widget Blueprint assets, their responsibilities, observed interface, reuse mechanisms, parameter contract, and known consumers.

## Preflight

1. Check whether the accepted responsibility has a selected registered asset or a compatible existing asset from the current `SystemFolder`. Only a registry entry with `status: active` is executable. A `candidate` is nomination or shared-parent-extension work only and must not be consumed as a Parent Class, nested class, read-only dependency, or target substitute.
2. Read the live asset and compare its parent class, generated class, Widget variables, size contract, and relevant reuse parameters with the registry. Treat interface or reuse-contract drift as a blocking stale-registry condition.
3. Keep reuse sources out of new-target paths and build order. Reusing a shared asset is read-only unless the user explicitly authorizes updating that exact shared asset.
4. Do not create a same-purpose replacement because `UIBuildBundle 0.1` lacks reuse relations. Use a validated `UIBuildBundle 0.3` for new shared-parent extension, Class Settings inheritance, and WidgetTree nesting. Bundle 0.2 stays closed for archived single-Slot artifacts. If an accepted relation cannot be represented by Bundle 0.3, stop before invoking Unreal and report the missing relation instead of duplicating the control.
5. Do not mutate a shared parent merely because a child needs extra behavior. Report the compatibility gap and require separate authorization for that exact shared asset. When a proposed common source lacks either normalized dual-layer extension Slot, it cannot be activated until that mutation is authorized and verified.
6. Block any selected relation whose registry mode is `unverified`; do not use it as a best-effort fallback.

## Shared-control activation contract

Before adding or promoting a reusable control in the public registry, verify all of the following against the saved asset:

- the WidgetTree has exactly two standard `/Script/UMG.NamedSlot` extension surfaces, both direct children of the shared root; never name either exactly `Slot`, because `/Script/UMG.Widget` already has a reflected `Slot` property and that name makes the Widget Blueprint fail compilation;
- `SlotDown` is the first shared-root direct child, precedes base content and legacy Slots, has Auto Size disabled, has `IsVariable`, uses `SelfHitTestInvisible`, defaults to anchors `[0,0,1,1]` with zero offsets/alignment, and has ZOrder strictly lower than every other shared-root direct sibling;
- `SlotUp` is the last shared-root direct child, follows base content and legacy Slots, has Auto Size disabled, has `IsVariable`, uses `SelfHitTestInvisible`, uses the same default full-fill layout, and has ZOrder strictly higher than every other shared-root direct sibling;
- the control has passed the size and state-model hard checks and at least `very-similar` semantic-content comparison;
- the shared asset was compiled, saved, read back, and its interface/reuse hashes were refreshed;
- activation and any shared mutation have the required user review evidence.

For an existing shared asset, preserve every observed legacy NamedSlot and every saved binding; the list may be empty and must come from readback. Rename any historical standard Slot only when live pre-save validation proves that every saved binding migrates without content or identity loss. `Slot1` is specific to the registered bag-item example, not a universal prerequisite. A Registry 0.4 `candidate` remains non-executable until root order, strict ZOrder, full-fill, variable flags, compile/save/post-save readback, migration evidence, and both hashes pass.

### New project-common bootstrap

Use `planned-bootstrap` only when the accepted asset is new, project-common, and represented by a linked production `UILayoutSpec`. Store the declaration in `registry-snapshots/shared-widget-bootstrap.<sha256>.json` beside the Bundle. The filename digest, `snapshotSha256`, linked layout digest, canonical bootstrap-contract digest, and the current authoritative Registry base digest/revision must all match. A zero digest, arbitrary sibling path, existing package, existing Registry identity, `reuse-only` asset, or actual/readback/interface evidence field is invalid.

The snapshot records construction intent with `expectedObjectPath`, `expectedGeneratedClassPath`, `expectedParentClassPath`, and `expectedReferenceSize`; these are not observed Unreal identity. Its extension relation uses `bootstrapSnapshot`, canonical direct-add `SlotDown`/`SlotUp`, and `post-extension-activation` with `status: required`. While required, every transitive consumer remains `planned`.

Execute the shared asset as a phase boundary: create it, add the two Slots, compile, save, and obtain fresh readback. Publish an ordinary active Registry 0.4 entry only if readback, interface/reuse hashes, Slot checks, and the snapshot's base Registry compare-and-swap guard all pass. Then replace the relation's `bootstrapSnapshot` with the actual `registry` binding, change activation to `verified` with its evidence artifact, rerun strict Bundle and coverage validation without `--skip-linked-files`, and only then build consumers. The same all-transitive-consumers-planned gate protects an existing Registry candidate while its activation remains required. A pending candidate or bootstrap may carry an empty `parameterOverrides` nesting plan despite an unverified generation/parameter contract; it may not execute, carry an override, advance any consumer, or reach a finalized lifecycle until an active verified Registry entry replaces that pending evidence. Existing assets never use the bootstrap path; read them back and bind a real Registry candidate instead.

## Composable generation relations

- `class-settings-parent-class`: create/use a system-specific child Widget Blueprint, then set `Class Settings -> Parent Class` to the shared generated class. Verify the saved child's actual Parent Class, inherited interface, and intentional child-owned nodes. The shared asset remains the prototype; the child asset is the system-specific consumer.
- `inherited-named-slot-content`: classify every additive visual as below or above the shared base. Put lower content into inherited `NamedSlot("SlotDown")` and upper content into inherited `NamedSlot("SlotUp")`. Record both Slots independently as `empty|panel`; each used Slot gets exactly one direct semantic Panel and that Panel fills by default. Both may be used when both layers are required. If no additive content is required, keep both empty and do not create placeholder Panels. This relation is neither prototype duplication nor `widget-tree-instance`.
- `widget-tree-instance`: keep the host Blueprint's own Parent Class, add one named nested node whose class is the shared prototype or one of its registered, verified child classes, and set the instance's declared parameters. Verify node class, stable WidgetTree path, Slot/layout, and every applied override after save. When the nested source is a `reuse-only` empty-tree Parent Class child, use `inherited-reuse-only-full-stretch` only for a full host rectangle; for a HorizontalBox/VerticalBox direct child use `inherited-reuse-only-flow-slot`, with `content-driven` for Auto or `weighted-remaining-space` for Fill plus positive weight; for GameScrollBox use `inherited-reuse-only-scroll-slot` with `scroll-slot` sizing and no Box Size. Every form cites the exact Parent Class and prototype-extension relation IDs, and nonexistent layout-node IDs must not be invented. Do not duplicate the prototype's internal WidgetTree.
- These relations may compose. Asset B sets its Parent Class to shared asset A, B independently fills neither, either, or both inherited layer Slots, and host C nests B or references it as an EntryClass. Preserve every link as an auditable relation.
- An EntryClass, class property, or runtime class-path reference is a separate usage relationship. Do not label it Parent Class inheritance or WidgetTree nesting without the corresponding saved structure.
- Lua inheritance exists only when the module explicitly passes the parent module to `UI.Class(...)`.
- Dynamic `UE.LoadUIWidget` or an EntryClass assignment records class use, not WBP or Lua inheritance on its own.

The generated plan for a future reuse-capable execution contract must carry the exact values below:

- inheritance relation: `classSettingsParentClassPath` equals the registered shared `generatedClassPath`;
- inherited Slot relation: exactly two ordered records for `SlotDown` and `SlotUp`, each with `contentMode: empty|panel`; for `panel`, include the one direct semantic Panel identity, stable tree path, `directChildCount: 1`, default full-fill layout, and any special-adaptation evidence;
- nested relation: stable host asset, `widgetName`, `widgetTreePath`, `sharedPrototypeClassPath`, actual `nestedWidgetClassPath`, and complete `parameterOverrides`;
- when the nested class is a derived class, it must match a registered verified Class Settings consumer whose Parent Class points to the shared prototype.

## Parameter contract

Prototype parameter declarations and consumer assignments are different records:

- `instanceParameters[]` declares supported names, value kinds, required flags, defaults, and meanings on the shared prototype.
- `parameterOverrides[]` records values actually assigned to one named nested instance and their source.
- Override names must be unique and declared by the prototype. Every required parameter needs an override or an explicit default.
- A mode with `parameterContractStatus: unverified` is not executable even when the project-wide nesting mechanism is user-confirmed. Inspect the live class and instance before promoting it to `verified`; never invent property names or values.
- A purely static WidgetTree nested consumer does not need Lua or `UE.LoadUIWidget` metadata. Record those only when independently observed.
- Treat a child or nested host as verified only when Unreal readback at that exact asset path binds its GeneratedClass and Parent Class; for nesting it must also bind the named WidgetTree node and class. Never promote a string-only class path into the allowed derived-class set.

### Registered bag item example

`/Game/UI/UMG/Widgets/uw_common_bag_item` is the designated 150x150 square item-icon base. It provides `ImgIcon`, `ImgQuality`, `TxtNum`, and `Lock` interfaces. `/Game/UI/UMG/FightBag/Widgets/uw_fightbag_item` verifies `class-settings-parent-class`: its Class Settings Parent Class is the common generated class and it has no independent WidgetTree root. Its Lua module nevertheless uses plain `UI.Class()` and implements a FightBag-specific `Init`; the item-slot Lua dynamically loads the child generated class and places it in `ItemSlot`.

Package evidence shows the current parent has legacy NamedSlot `Slot1`, and `/Game/UI/UMG/Widgets/uw_common_item` inherits it and binds a CanvasPanel containing `ImgDate`. Live pre-save validation proved that renaming the parent slot does not automatically migrate this saved binding. A separate live compile probe proved that exact name `Slot` conflicts with the inherited `/Script/UMG.Widget:Slot` property and cannot compile. Registry 0.4 therefore keeps the entry non-executable while a controlled migration renames the historical `SlotContent` surface to upper-layer `SlotUp`, adds lower-layer `SlotDown`, and preserves `Slot1`. Compile/save/post-save readback, strict root-order/ZOrder checks, interface/reuse hash refresh, and registry activation remain mandatory. `/Game/UI/UMG/Production/Widgets/uw_production_makeitem` separately nests `uw_common_item_C` as `Item`; its runtime Lua call to `Item.LuaImpl:Show(...)` is not a Designer `parameterOverride`.
