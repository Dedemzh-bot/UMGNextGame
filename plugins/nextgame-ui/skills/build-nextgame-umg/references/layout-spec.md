# UILayoutSpec 0.2

Use the JSON Schema in `../assets/ui-layout-spec.schema.json` as the authoritative structure.

## Coordinate model

- Express every `rect` as `[x, y, width, height]` normalized against `referenceSize`.
- Every explicit Canvas `slotLayout.offsets` value is local to the node's direct parent Panel. After reparenting into a nested CanvasPanel, recompute left/top/right/bottom from the parent's local rectangle; never carry screen-global offsets into the child slot. Point anchors, alignment, and `autoSize` determine which local size terms are meaningful.
- For every complete project screen (`profile.assetKind: screen`), `referenceSize` is the target project design coordinate space and must be `[2560, 1440]`; it is not the source screenshot size.
- Resolve the guarded basename from the asset being mutated: prototype uses `asset.name`; production/formal lowering prefers `profile.targetAsset.name` and falls back to `asset.name`. A resolved `umg_*` basename uses `FillScreen`; explicit `Desired` is rejected, while an archived missing value remains readable and resolves to hard-rule `FillScreen`. Only a resolved `uw_*` basename may use analyzed `Desired` or `FillScreen`. Missing/unclear `uw_*` decisions and unknown/legacy basenames conservatively resolve to `FillScreen`. `profile.assetKind` and directory alone do not select the mode. A `uw_*` `Desired` tree must contain at least one root-direct child with either a point-anchored Canvas `slotLayout` whose `autoSize` is false and whose `offsets.right`/`offsets.bottom` are positive, or a `contentDrivenSize` record with `verified: true`, positive two-axis `measuredDesiredSize`, and a valid `evidenceId`. An empty root, auto-sized fixed Slot, verified-only record, or zero-offset full-stretch-only content is rejected. This general proof does not relax the stricter unique first-Panel rule for `listRole: entry`. The mode controls the Widget Blueprint Designer `Screen Size` preview; it neither replaces `referenceSize` nor authorizes `DesignTimeSize` writes.
- Source screenshots may have another size. Normalize their measurements and remap the intended composition to the complete project canvas rather than preserving a smaller fixed inner frame.
- Child widgets and collection entries keep their own local functional `referenceSize` and are not forced to the full-screen canvas. Their local size never defines their screen-host rectangle: a screen node's accepted `rect` is copied unchanged into the screen UILayoutSpec, then the host chooses Fill/stretch, flow, or a deliberate ScaleBox strategy.
- Measure `x` and `y` from the image's top-left corner.
- Keep nested-node rectangles normalized against the full reference image, not against the immediate parent.
- Keep a single root node with `role: screen.root`, `parent: null`, and `rect: [0, 0, 1, 1]`.
- Name each WidgetTree node with the `namePrefix` declared for its role in the component catalog, following `widget-component-naming.md`.
- Set node-level `isVariable: true` when project code must directly read, modify, populate, show, hide, or otherwise control that widget at runtime. Set it to `false` or omit it for fixed decoration.
- Read `region-module-structure.md`, identify cohesive regions before leaf controls, and introduce the appropriate container for each region.
- When `profile.regionGrouping` is true, keep ordinary content below a region ancestor instead of making it a direct child of the root.
- Require a child rectangle to stay within its parent region's rectangle.

## Region metadata

- Require `profile.regionGrouping` on every 0.2 spec.
- Use `true` when the reference has multiple recognizable visual or functional groups.
- Use `false` only for a genuinely atomic widget.
- Put a unique lower-case `regionPurpose` token on each region container.
- Use `rootLayer: background` or `rootLayer: overlay` only for a genuine global layer that must remain a direct child of the root.
- Do not combine `regionPurpose` and `rootLayer` on one node.

The build planner converts a child rectangle from full-reference coordinates into parent-local CanvasPanel geometry when the validated hierarchy is nested.
Interpret a nested node's anchor relative to its immediate region container.

## Node semantic metadata

Node semantic metadata describes structural rules to the validator and is never copied into `properties` or sent to Unreal as a raw property write.

- `textGroup` currently supports `kind: "ratio"` on a `container.horizontal`. Declare `alignment` as `Left`, `Center`, or `Right` and `orderedChildren` as exactly `[current, separator, maximum]`. The current and maximum nodes are variable TextBlocks; the separator is a static `"/"` TextBlock.
- `fullHeight: true` marks a Canvas-hosted screen shell or background that must stretch vertically through every CanvasPanel ancestor. Use it on both the shell and lower/background child when both must cover a taller viewport.

## Anchors

Use `auto` unless the image makes the intended responsive edge clear. Supported values are `left-top`, `center-top`, `right-top`, `left-center`, `center`, `right-center`, `left-bottom`, `center-bottom`, `right-bottom`, and `auto`.

`prepare_build.py` converts the normalized rectangle into CanvasPanel anchor, alignment, position, and size values.

For adaptive children, add explicit intent metadata when edge, center, or stretch behavior is important:

```json
"adaptiveLayout": {
  "horizontal": "left",
  "vertical": "stretch",
  "reason": "Keep the roster on the left and preserve top/bottom margins on taller screens."
}
```

Allowed horizontal values are `left`, `center`, `right`, and `stretch`. Allowed vertical values are `top`, `center`, `bottom`, and `stretch`. Under CanvasPanel, a stretch intent requires matching stretched anchors in `slotLayout` and point intent must agree with the selected point anchor. Under Overlay, VerticalBox, HorizontalBox, or GameScrollBox, each axis must agree with that parent's explicit child Slot alignment (`stretch` maps to `Fill`). The reason records the content or composition evidence behind the decision.

For an Overlay whose necessity is not evident from multiple layers, use `overlayPurpose` with `layering`, `adaptive-bounds`, or `independent-alignment`. Do not use this field on other component roles.

## Properties

Use canonical property names from the component catalog, not guessed Unreal property names. Examples:

- Text: `{"text": "Play", "color": {"r": 1, "g": 1, "b": 1, "a": 1}}`
- Progress: `{"percent": 0.75, "fillDirection": "LeftToRight"}`
- GameImage: `{"color": {"r": 0.2, "g": 0.4, "b": 1, "a": 1}}`

The build plan maps canonical names to Unreal names. The Editor workflow must still discover each real property with `list_properties` before writing it.

`isVariable` is node metadata rather than a normal entry in `properties`. The build planner applies it with `UMGToolSet.UMGToolSet.ToggleWidgetAsVariable`, because Unreal protects `bIsVariable` from ordinary property writes.

Use `visibility: "SelfHitTestInvisible"` for displayed passive components. The build planner supplies this value by default for catalog roles marked passive. A runtime-controlled inactive state Panel may explicitly use `Collapsed`, or `Hidden` when it must preserve its layout allocation. Passive components may therefore use only `SelfHitTestInvisible`, `Hidden`, or `Collapsed`; `Visible` and `HitTestInvisible` are rejected. Keep interactive input, list, tile, and scroll controls hit-testable when they own input.

## Build mode and project target

- `mode: prototype`: keep `asset` under `/Game/UI/AIPrototype` and describe the intended project destination separately in `profile`.
- `mode: production`: set `asset.folder/name` to the exact validated formal destination in `profile.targetAsset`.

- `assetKind`: `prototype`, `screen`, or `child-widget`.
- `designSizeMode`: `FillScreen` or `Desired`. New layouts must set it explicitly. A target basename beginning `umg_` requires `FillScreen`; `Desired` is valid only for a formal `uw_*` target with positive local/content-sized evidence. A missing `uw_*` value enters `fallback-unclear -> FillScreen`; an unknown/legacy basename enters `fallback-unknown-target -> FillScreen`. This guard is independent of `assetKind` and directory. The `prototype` kind is archived compatibility only and must not be newly emitted.
- `assetScope`: optional `system` or `project-common`; omission keeps the historical `system` behavior. Use the explicit `project-common` value only for a cross-system child widget whose formal destination is `/Game/UI/UMG/Widgets`.
- `system`: lower-case system token used in the Widget Blueprint asset name.
- `systemFolder`: canonical Content Browser spelling of that same system. It may differ from `system` only by letter case.
- `subsystem`: optional screen/feature subsystem token.
- `function`: required for `child-widget`.
- `secondaryFunction`: optional child-widget detail token.
- `targetAsset.folder` and `targetAsset.name`: intended production destination.
- `targetAsset.integrationAsset`: integration screen for a child module when applicable.
- `listRole`: optional `container` or `entry` marker for a data-driven list pair.
- `collectionSizing`: required when `listRole` is `container`; choose `show-all` or `fixed-viewport` from the feature's overflow requirement.
- `parentClass`: required as `/Script/UIFramework.ListViewItem` when `listRole` is `entry`.
- `explicitPanelSlots`: set to `true` on every newly generated layout so VerticalBox, HorizontalBox, and GameScrollBox child Slot decisions are explicit and executable. Omission is legacy 0.2 compatibility only.

Example fight child target:

```json
{
  "assetKind": "child-widget",
  "system": "fight",
  "systemFolder": "Fight",
  "subsystem": null,
  "function": "stamina",
  "secondaryFunction": null,
  "targetAsset": {
    "folder": "/Game/UI/UMG/Fight/Widgets",
    "name": "uw_fight_stamina",
    "integrationAsset": "/Game/UI/UMG/Fight/umg_fight"
  }
}
```

The validator derives the expected name and folder from these fields. The executor writes to the validated `asset` destination. Production mode therefore requires explicit authorization and an exact match with `profile.targetAsset`.

Example project-common list entry:

```json
{
  "assetKind": "child-widget",
  "assetScope": "project-common",
  "system": "common",
  "systemFolder": "Common",
  "subsystem": null,
  "function": "material",
  "secondaryFunction": "list",
  "listRole": "entry",
  "parentClass": "/Script/UIFramework.ListViewItem",
  "targetAsset": {
    "folder": "/Game/UI/UMG/Widgets",
    "name": "uw_common_material_list"
  }
}
```

`project-common` changes the derived folder and omits the subsystem segment. It requires `assetKind: child-widget`, `system: common`, and a null or omitted `subsystem`; the existing function, name-token, `systemFolder` identity, list-role, parent-class, production-target, and WidgetTree rules still apply.

Derived production locations:

- System screen: `/Game/UI/UMG/<SystemFolder>/<screen-asset-name>`
- Functional child widget: `/Game/UI/UMG/<SystemFolder>/Widgets/<child-asset-name>`
- Project-common child widget: `/Game/UI/UMG/Widgets/uw_common_<function>[_<secondary_function>]`

Create or reuse the system folder when production mutation is authorized. For system-scoped child widgets, also create or reuse its `Widgets` subfolder. For an explicitly scoped project-common child widget, create or reuse the shared `/Game/UI/UMG/Widgets` folder instead.

## Text nodes

- Read `text-component-content.md` whenever `profile.hasText` is true.
- Store one independently bounded visual text block in each `text.label` node.
- Do not include tabs, manual line breaks, repeated layout spaces, icon glyphs, or decorative separators in `properties.text`.
- Use `properties.autoWrap: true` for a continuous paragraph that should wrap inside one TextBlock.
- Put `properties.font: {"size": <even integer>}` on every text node so the size can be validated. Preserve other font-struct members during updates.
- Put `properties.justification` on every text node with `Left`, `Center`, or `Right`, selected from the intended stable edge and safe text-growth direction.
- Set `properties.wrapTextAt` to a positive explicit pixel width for wrapping text. `autoWrap: true` never replaces this width.
- Use separate `visual.image` nodes for icons and separator lines. This role maps exclusively to `/Script/UIFramework.GameImage`; keep the `Img` name prefix and never author native `/Script/UMG.Image` in new specs.

## Adaptive CanvasPanel Slot overrides

Use optional `slotLayout` only for a node whose immediate parent is `screen.root` or `container.canvas`. It overrides the point-anchor geometry derived from `rect` and records an explicit adaptive CanvasPanelSlot relationship:

```json
{
  "anchors": {"minimum": [0, 0], "maximum": [1, 0]},
  "offsets": {"left": 76, "top": 46, "right": 6, "bottom": 60.8},
  "alignment": [0, 0],
  "autoSize": true
}
```

Use horizontal or vertical stretch anchors for localization-sensitive text, backgrounds, and decorations when the design requires them to follow a changing containing size. Keep `rect` as the image-derived intent and use `slotLayout` as the exact authored CanvasPanelSlot behavior.

`slotLayout.autoSize` maps to the CanvasPanelSlot `Size To Content` setting. Set it from the content/overflow contract, not by copying whether the reference happened to fit inside its screenshot. For one-axis growth, preserve a constraint on the stable axis; a fixed-column tile commonly uses an auto-sized `SizeBox` wrapper with width override only.

## Direct Button Canvas content

When an `input.button` directly owns a `container.canvas`, the CanvasPanel is the Button's full content host. Put the following required `buttonSlot` object on that CanvasPanel node:

```json
"buttonSlot": {
  "padding": [0, 0, 0, 0],
  "horizontalAlignment": "Fill",
  "verticalAlignment": "Fill"
}
```

This is separate from `slotLayout`: `buttonSlot` controls the direct ButtonSlot, whereas `slotLayout` applies only to a direct CanvasPanelSlot. The generated plan emits the ButtonSlot property read/write sequence. Do not use a non-zero direct-host inset to compensate for bad source geometry; add an intentional inner layout only when the design actually needs an inset.

## Direct Overlay child alignment

Every node whose direct parent is `container.overlay` must declare its `OverlaySlot` alignment explicitly:

```json
"overlaySlot": {
  "horizontalAlignment": "Fill",
  "verticalAlignment": "Fill"
}
```

Allowed horizontal values are `Fill`, `Left`, `Center`, and `Right`; allowed vertical values are `Fill`, `Top`, `Center`, and `Bottom`. Use Fill on both axes when the child's normalized rectangle equals the Overlay rectangle and the child is intended to cover that complete shared region. Use a non-Fill value only for a deliberately local-sized or independently aligned layer. When the child declares `adaptiveLayout`, both axes must match this OverlaySlot alignment exactly. `overlaySlot` is invalid on any node whose direct parent is not an Overlay.

## VerticalBox and HorizontalBox child Slots

Every direct child of `container.vertical` or `container.horizontal` in a new layout declares `flowSlot`:

```json
"flowSlot": {
  "size": {"rule": "Auto"},
  "padding": [0, 0, 0, 0],
  "horizontalAlignment": "Fill",
  "verticalAlignment": "Center"
}
```

`size.rule` controls allocation along the Box main axis. `Auto` follows the child's Desired Size. `Fill` consumes weighted remaining space and therefore also requires a positive `weight`. Horizontal/vertical Alignment controls placement inside the allocated Slot and is independent from main-axis Size: a content-driven middle child commonly uses Size `Auto` with Alignment `Fill`. `contentDrivenSize` is a closed object with required boolean `verified` and optional typed `measuredDesiredSize: [width, height]` plus `evidenceId`. When `verified` is true, a flow child must use Size `Auto`. When the record is used as a root or list-entry Desired Size proof, both optional fields become semantically required, both measured axes must be positive, and `evidenceId` must match `^[a-z][a-z0-9.-]{2,95}$`.

Choose both alignments from the child geometry and adaptation intent. A declared `adaptiveLayout` stretch axis requires the corresponding Alignment `Fill`; left/right/center and top/bottom/center intent requires the matching alignment. This intent describes occupancy inside the already allocated Slot and never changes Box main-axis allocation: `flowSlot.size` remains authoritative, so `Auto` plus Alignment `Fill` is not weighted expansion. `flowSlot` is invalid under every other parent.

## GameScrollBox child Slots

Every direct child of `container.game-scroll` in a new layout declares `scrollSlot`:

```json
"scrollSlot": {
  "padding": [0, 0, 0, 0],
  "horizontalAlignment": "Fill",
  "verticalAlignment": "Top"
}
```

GameScrollBox child Slots expose Padding and horizontal/vertical Alignment, not the VerticalBox/HorizontalBox `FSlateChildSize` contract. Choose each axis from the measured child size, owning-region adaptation, scroll direction, and desired cross-axis behavior. `scrollSlot` is invalid under every other parent. The generated plan discovers and reads the real Slot properties before writing either contract.

## Data-driven list nodes

- Read `dynamic-list-widgets.md` when runtime data controls item count. A repeated family with the same structure whose text, numerical values, images, or visual state are supplied as data is list-preferred even when the reference shows only a fixed number of examples.
- Set `profile.listRole: container` on the collection module and use `collection.lua-list` or `collection.lua-tile` as a WidgetTree leaf.
- Set `profile.collectionSizing: show-all` when every entry must remain visible, or `fixed-viewport` when scrolling, clipping, or paging is intentional.
- Set `profile.listRole: entry`, `profile.secondaryFunction: list`, and `profile.parentClass: /Script/UIFramework.ListViewItem` on the entry asset.
- On a collection node, set `entryWidgetClass` to a `{ "refPath": "<entry-generated-class>" }` object.
- Set `isVariable: true` on every `LuaListView` or `LuaTileView` collection node because project code populates or controls the collection at runtime.
- Set `isVariable: true` on entry TextBlocks, GameImages, progress displays, or other fields whose content project code changes. Leave purely decorative entry elements false or omitted.
- Use `orientation`, `selectionMode`, `verticalEntrySpacing`, `horizontalEntrySpacing`, and `designerPreviewEntries` only when they are part of the intended collection behavior.
- For `collection.lua-tile`, set positive `entryWidth` and `entryHeight` and create the visible gap through the cell pitch versus the entry's visible content footprint. Omit `horizontalEntrySpacing` and `verticalEntrySpacing` or set them to `0`; they contract the usable area inward and cannot widen Tile spacing.
- A `show-all` collection must enable `slotLayout.autoSize` on the direct child below its nearest CanvasPanel ancestor. Preserve the stable-axis constraint separately when auto-sizing the collection directly would change its row or column count.
## Image interpretation

- Approximate colors; do not claim exact sampling unless a color extraction tool was used.
- Preserve visible text when legible and flag uncertain text in `notes`.
- Use `confidence` on nodes when semantic identity is uncertain.
- Prefer `visual.border` or `visual.image` for unknown decorative blocks; `visual.image` always resolves to GameImage.
- Materialize every accepted complete-image requirement as exactly one compatible `visual.image` node unless a verified `widget-tree-instance` relation supplies that complete graphic. Raster detector dots, strokes, corners, color islands, and connected components are evidence, not separate Widget instructions. If one texture or Brush can express a complete non-text graphic and its parts share state, runtime control, draw layer, adaptation, and resource responsibility, use one GameImage. Never add a local node to duplicate relation-backed shared content.
- Keep an additional image node only when the accepted Requirement proves independent runtime control, state variation, adaptation, resource reuse, material/mask behavior, progress fill, or an accepted exception. Backplates and glyph art commonly use separate complete semantic groups because their surface/stretch and fixed-resource responsibilities differ; the glyph itself should not be reconstructed from many point or line images, and `role: layer` is reserved for a justified layer of the same graphic.
- Choose decoration adaptation from its owning region and parent Slot on both axes. A full backplate may Fill, a horizontal rule may stretch only horizontally, a side rail may stretch vertically while remaining edge-attached, corner art belongs to its actual corner, and a badge may remain centered at Desired Size. Local top-left coordinates inside a fixed parent are not evidence for a screen-level top-left anchor.
- When a matching art resource is unavailable, keep the node and use the approved temporary resource or approximate pure-color `visual.image` placeholder. Missing art is not a reason to omit structure.
- Use `input.button` only when the element appears interactive.
- For every click/tap or discrete state-switch target, use an `input.button` named `Btn*` as the hit-testable owner. Keep its visual children passive and leave the behavior hookup to project code.
