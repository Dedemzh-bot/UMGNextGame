# Shared Widget Blueprint discovery

Use the plugin-level [SharedWidgetRegistry](../../../assets/shared-widget-registry.json) as the curated source for reusable Widget Blueprint assets. It is separate from the build skill's `component-catalog.json`: the catalog maps semantic node roles to native or project component classes, while this registry identifies complete, already-existing Widget Blueprint assets and their observed reuse contracts.

## Discovery order

For every proposed `screen`, `child-widget`, list entry, or runtime-template boundary:

1. Honor an explicit user-named reusable asset first. Resolve its full `/Game/...` path and inspect it; a user selection has priority but does not waive existence or compatibility checks.
2. Inspect relevant existing assets in the current `SystemFolder`.
3. Validate and query the curated shared registry even when the current `SystemFolder` contains a candidate. Current-system matches have discovery priority, but they do not suppress the curated comparison set.
4. Exclude every registry entry whose `status` is not `active` from executable selection. A `candidate` entry may be shown only as a nomination or as a shared-parent extension proposal; it must never become a read-only dependency, Parent Class, nested class, target-path substitute, or build input.
5. Compare purpose, `capabilityIds`, declared generation modes and status, parent class, runtime Widget interface, size contract, state model, extension-slot contract, and documented consumers. Similar names alone are not compatibility evidence.
6. For each relation in the proposed asset chain, classify it as `class-settings-parent-class`, `inherited-named-slot-content`, `widget-tree-instance`, another class/reference usage, or no reuse. Report `direct reuse`, `reuse with extension`, `incompatible`, or `unverified`. When more than one compatible candidate or class chain remains, present the choice to the user instead of selecting arbitrarily.

Do not scan all of `/Game/UI/UMG` and silently choose a look-alike. AI may nominate an observed asset, but activation still requires the normal first review gate, the verified shared-control contract below, and any exact shared-asset mutation authorization.

## Similarity decision

Either an explicit user designation or an AI similarity result may start the common-control decision. Preserve that provenance; a user designation has priority but does not waive compatibility checks.

1. **Size is hard.** Compare the reusable control's effective content footprint with the requested populated-content footprint, not a surrounding list cell's pitch. It must match or have an explicitly evidenced, non-distorting adaptation. A default inherited extension Slot does not authorize scaling the prototype itself.
2. **State model is hard.** Every required content state must already be represented by the prototype, or be representable as an additive Slot extension without changing or hiding the prototype's required semantics. A missing or contradictory state is incompatible.
3. **Content is semantic.** Compare responsibilities and content meaning. `very-similar` is sufficient; pixel identity and identical leaf content are not required.
4. Record `user-explicit` or `ai-similarity`, the two hard-condition results, the semantic-content result, evidence, and the final decision. AI confidence alone cannot replace the two hard checks.

## Requirement-stage effect

- An executable common source must be `active`. A newly recognized candidate first needs exactly two standard root-direct `/Script/UMG.NamedSlot` extension surfaces. `SlotDown` is the first root direct child, precedes all base content and any observed legacy Slots, disables Auto Size, is `IsVariable`, uses `SelfHitTestInvisible`, defaults to full fill, and has ZOrder strictly lower than every other root direct sibling. `SlotUp` is the last root direct child, follows all base content and any observed legacy Slots, disables Auto Size, is `IsVariable`, uses `SelfHitTestInvisible`, defaults to full fill, and has ZOrder strictly higher than every other root direct sibling. Never use the exact name `Slot`, because it conflicts with `/Script/UMG.Widget:Slot` and prevents Blueprint compilation. A new common asset may add the two Slots directly; an existing asset must preserve every observed legacy NamedSlot and saved binding, with the concrete names coming from readback rather than a global `Slot1` assumption. Only after live compile/save/readback and Registry 0.4 interface/reuse hash refresh may the candidate become `active`.
- After common reuse is selected, plan a current-system-specific child Blueprint whose `Class Settings -> Parent Class` is the shared generated class. This child is not a second shared prototype.
- Classify additive visuals by draw layer. Put content that belongs below the shared base into inherited `SlotDown`; put content that belongs above it into inherited `SlotUp`. Each Slot is independently `empty|panel`, both may be used when the requirement needs both layers, and every used Slot has exactly one direct semantic Panel with its extra nodes below that Panel. The Panel defaults to full fill. Leave both Slots empty when no additive content is evidenced and never create placeholder Panels. Any non-fill adaptation needs a separate explicit requirement and evidence.
- A later host may nest the verified system child or use it as an EntryClass. Record those as separate relations. The canonical chain is `shared prototype -> system child by Parent Class -> optional inherited Slot content -> host/collection class use`.
- Do not create a second planned asset for the same accepted responsibility while a selected reuse candidate covers it.
- Treat the registry entry as discovery evidence. Before relying on its interface, read the live asset and compare the observed parent, Widget variables, sizes, generated class, and reuse-contract hash. A mismatch makes the candidate `unverified` until the registry is deliberately refreshed.
- Human-selected and AI-proposed candidates are both shown at the first user review; keep their provenance distinct.
- Selection authorizes reuse analysis only. It does not authorize modifying the shared asset. Any required extension must be proposed separately and explicitly authorized before mutation.

`UIRequirementSpec 0.1` records accepted reuse intent and authorization; `UIBuildBundle 0.3` serializes the dual-layer shared extension, Class Settings inheritance with both inherited Slot records, and WidgetTree nesting relations. Bundle 0.2 remains the closed historical single-Slot contract and must not be widened. If an accepted relation cannot be represented by Bundle 0.3, keep it in the review summary and stop instead of generating a duplicate target.

## Generation modes and relation terminology

Record each layer independently:

- **`class-settings-parent-class`**: create or use a system-specific Widget Blueprint and set `Class Settings -> Parent Class` to the shared asset's generated class. The child Blueprint inherits the prototype WidgetTree and exposed variables. Record `classSettingsParentClassPath` as the exact saved value. The registry mode and known consumer use the same term.
- **`inherited-named-slot-content`**: two records authored by the system child against inherited shared `NamedSlot("SlotDown")` and `NamedSlot("SlotUp")`. They are not WidgetTree copies and not nested instances of the prototype. Record each Slot as `empty` or `panel`; for every used Slot record its one direct semantic Panel's name, class, tree path, direct-child count, default full-fill layout, and any accepted special-adaptation evidence. Simultaneous use is valid when both draw layers are required.
- **`widget-tree-instance`**: keep the host Blueprint's own Parent Class, generate one named nested Widget node, and set its class to either the shared generated class or a registered, verified child of that class. Record `widgetName`, stable `widgetTreePath`, `sharedPrototypeClassPath`, actual `nestedWidgetClassPath`, and every `parameterOverride` with its value source. Do not copy the prototype's internal subtree into the host.
- **Collection/class reference**: a list EntryClass, class property, or runtime property points at a generated class. This may create content dynamically but is neither authored mode unless the saved Blueprint also has the exact Class Settings or WidgetTree structure above. A NamedSlot is modeled by `inherited-named-slot-content`, not by this generic category.
- **Lua inheritance**: the Lua module explicitly calls `UI.Class('<super-module>')`. A comment saying “逻辑继承” or a WBP parent relationship does not prove Lua inheritance.
- **Dynamic class loading**: project code selects a generated class path, loads/creates that Widget, places it in a host slot or collection, and initializes it. This is a usage relationship, not a class hierarchy by itself.

## Parameters and execution status

Separate prototype declarations from instance assignments:

- `generationModes[].instanceParameters[]` is the prototype contract: parameter name, value kind, required flag, optional default, and meaning.
- `knownConsumers[].parameterOverrides[]` is one host instance's assignment: declared parameter name, value source, and applied value.
- A required parameter needs either a declared default or an override at every verified instance.
- `parameterContractStatus: unverified` makes the whole nested mode non-executable. Do not infer a parameter from a visual difference or from a user confirming only that the project supports the mechanism.
- A mode is `verified` only when its mode-level evidence paths point at concrete entry evidence. A known executable consumer cannot use an unverified mode.
- A `verified` consumer must explicitly reference Unreal readback at its exact asset path. That readback must bind its actual GeneratedClass and Parent Class; a nested consumer must additionally bind the named WidgetTree path and actual nested class. Only a verified, identity-bound Parent Class child may be selected as the class of a later nested instance.

For `shared.common.bag-item`, `uw_fightbag_item` verifies `class-settings-parent-class`: its saved Parent Class is the common generated class and it owns no independent WidgetTree root, while its Lua module uses plain `UI.Class()` and independently implements `Init`. Asset-package evidence also shows legacy `Slot1`, and `uw_common_item` binds a CanvasPanel containing `ImgDate` there. Live pre-save validation proved that a parent rename does not automatically migrate that child binding; another live compile probe proved exact name `Slot` conflicts with `/Script/UMG.Widget:Slot`. Registry 0.4 therefore records a controlled migration from the historical `SlotContent` name to `SlotUp`, adds `SlotDown`, and preserves `Slot1` with its bindings. Until the root order, strict ZOrder, full-fill, `IsVariable`, compile/save, post-save readback, and refreshed hashes are all verified, the entry remains a non-executable `candidate`. `/Game/UI/UMG/Production/Widgets/uw_production_makeitem` separately proves that a host can nest the verified child `uw_common_item_C`; its Lua `Show(...)` argument is runtime initialization, not a Designer `parameterOverride`.
