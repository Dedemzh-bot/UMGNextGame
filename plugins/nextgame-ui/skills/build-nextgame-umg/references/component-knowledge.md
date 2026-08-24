# NextGame UMG component knowledge

Read the relevant component section before selecting or configuring that project component. Record verified project behavior here separately from feature-specific layout rules.

## Contents

- [LuaListView](#lualistview)
- [LuaTileView](#luatileview)
- [GameImage](#gameimage)
- [VerticalBox and HorizontalBox](#verticalbox-and-horizontalbox)
- [GameScrollBox](#gamescrollbox)
- [SizeBox](#sizebox)
- [Overlay](#overlay)

## LuaListView

### Identity and purpose

- Class path: `/Script/UIFramework.LuaListView`.
- Base behavior: project list component built on Unreal `ListViewBase` behavior.
- Use it for a linear collection whose runtime item count can change.
- Treat it as a data-driven WidgetTree leaf, not as a `PanelWidget`. Do not add static WidgetTree children below it.
- Use a separate Widget Blueprint derived from `/Script/UIFramework.ListViewItem` to render one entry.

### Container-to-entry relationship

1. Create and compile the entry Widget Blueprint first.
2. Set `EntryWidgetClass` on `LuaListView` to the entry generated class (`*_C`).
3. Compile the collection module after assigning the entry class.
4. Do not author duplicate static rows.
5. Integrate the collection module or screen-local list into the screen, not the entry asset.

Runtime population and behavior belong to project code and are outside this UMG production tool's scope. The collection itself and every program-controlled entry field must still follow the common `Is Variable` rule; this structural exposure does not add Lua, C++, or EventGraph behavior.

For entry Widget Blueprints, the first internal structural Panel is the local size contract: its Canvas/Panel Slot must expose the entry referenceSize with explicit non-zero width and height (or a separately verified desired-size contract). A zero-offset dual-axis stretch root alone is invalid. Do not use a SizeBox as a substitute for this structural Panel.

The designated project reference is:

```text
uw_fight_prompt
-- ListPrompt : LuaListView
   -- EntryWidgetClass = uw_fight_prompt_list_C

uw_fight_prompt_list : ListViewItem
-- one runtime entry presentation
```

### Verified configuration properties

| Layout-spec name | Unreal property | Function |
| --- | --- | --- |
| `entryWidgetClass` | `entryWidgetClass` | generated class used to create each entry |
| `orientation` | `orientation` | vertical or horizontal list direction |
| `selectionMode` | `selectionMode` | item selection behavior |
| `horizontalEntrySpacing` | `horizontalEntrySpacing` | horizontal distance between generated entries |
| `verticalEntrySpacing` | `verticalEntrySpacing` | vertical distance between generated entries |
| `designerPreviewEntries` | `numDesignerPreviewEntries` | virtual designer-only preview count |

In the current Editor MCP bridge, `orientation` is readable but not writable through `ObjectTools.set_properties`. A vertical list may rely on the verified `Orient_Vertical` default and omit the write. A non-default horizontal list must use a separately verified supported route; block the build instead of claiming the direction was applied.

The component also exposes standard list/scroll behavior such as mouse-wheel consumption and wheel scroll multiplier. Inspect the live property schema before changing optional scrolling or focus settings because project and engine versions may expose additional fields.

### Designer preview and runtime data

- `numDesignerPreviewEntries` creates designer previews only. It does not create runtime items.
- Entry spacing belongs on the list. Do not create blank entries or embed spacer rows to imitate spacing.
- Set `isVariable: true` on the collection and on entry fields whose runtime values change. Leave purely decorative entry elements false or omitted.

### Hit testing

- Keep the `LuaListView` hit-testable when it must scroll, select, focus, or react to pointer input.
- Set passive visuals inside its entry to `SelfHitTestInvisible` so they do not block list input.
- If a particular list is intentionally display-only, document that decision before changing its hit-testing behavior.

### Verification

- class path is `/Script/UIFramework.LuaListView`;
- `EntryWidgetClass` points at the intended entry `*_C` class;
- the collection reads back with `bIsVariable: true`;
- entry parent class is `/Script/UIFramework.ListViewItem`;
- orientation, spacing, selection, and designer preview count match the intended layout;
- runtime-variable data is not represented by static duplicate rows.

## LuaTileView

### Identity and purpose

- Class path: `/Script/UIFramework.LuaTileView`.
- It derives from the project `LuaListView` and Unreal list-view behavior, but lays runtime entries out as tiles.
- Use it for a tiled collection whose item count changes at runtime. Treat it as a data-driven WidgetTree leaf and render one tile with a separate `/Script/UIFramework.ListViewItem` Widget Blueprint.
- The same container-to-entry compilation order and `Is Variable` rules described for `LuaListView` apply.

### Verified configuration properties

| Layout-spec name | Unreal property | Function |
| --- | --- | --- |
| `entryWidgetClass` | `entryWidgetClass` | generated entry class |
| `entryWidth` | `entryWidth` | generated tile width |
| `entryHeight` | `entryHeight` | generated tile height |
| `orientation` | `orientation` | collection growth and scroll orientation |
| `selectionMode` | `selectionMode` | item selection behavior |
| `horizontalEntrySpacing` | `horizontalEntrySpacing` | inward horizontal contraction of the usable entry area; not a wider tile pitch |
| `verticalEntrySpacing` | `verticalEntrySpacing` | inward vertical contraction of the usable entry area; not a wider tile pitch |
| `designerPreviewEntries` | `numDesignerPreviewEntries` | designer-only virtual entry count |
| `tileAlignment` | `tileAlignment` | alignment of each generated tile row |
| `consumeMouseWheel` | `consumeMouseWheel` | mouse-wheel consumption policy |

### Sizing and overflow

- Set positive explicit `entryWidth` and `entryHeight` on every Tile. Treat them as the generated tile-cell pitch.
- Create visible horizontal and vertical gaps by making `entryWidth` and `entryHeight` larger than the visible content footprint authored inside the entry Widget Blueprint. Keep the entry's visual background/content deliberately inside that cell footprint.
- Keep `horizontalEntrySpacing` and `verticalEntrySpacing` at `0` or omit them. In this project component they contract the usable entry area inward and cannot widen the distance between generated tile cells.
- Do not copy a desired outside gap into the two Entry Spacing fields. Verify the actual tile pitch from `entryWidth`/`entryHeight` and the visible entry-content size instead.
- Declare `profile.collectionSizing` as `show-all` or `fixed-viewport`; do not infer scrolling merely because runtime count is variable.
- `Size To Content` is a property of the containing `CanvasPanelSlot`, not a TileView item-count setting.
- A direct auto-sized TileView can lose the width that determines its column count and collapse to one column. For a fixed-column `show-all` grid, use `CanvasPanel -> SizeBox -> LuaTileView`: set only the `SizeBox` width override, clear its height override, enable `Size To Content` on the `SizeBox` Canvas slot, and let the TileView fill its `SizeBoxSlot`.
- Every ancestor on the growth axis must also allow the resulting desired size. A fixed-height ancestor or clipping panel reintroduces hidden entries even when the local wrapper is correct.
- For `fixed-viewport`, keep explicit viewport geometry and configure the required scroll behavior instead of enabling content-driven size.

### Display-only configuration

When the grid only communicates status and owns no direct interaction, use `selectionMode: None`, `consumeMouseWheel: Never`, and `SelfHitTestInvisible`. Project code still needs the collection and dynamic entry image fields exposed as variables so it can populate and update them.

### Verification

- entry class, entry size, spacing, orientation, alignment, and preview count read back correctly;
- `entryWidth` and `entryHeight` are positive, the visible gap is produced by cell size versus entry-content footprint, and both Entry Spacing fields remain zero/default;
- `show-all` grids retain their intended column count and display a preview count greater than the original fixed capacity;
- width-only wrappers have no active height override and their Canvas slot uses `Size To Content`;
- display-only grids do not consume mouse-wheel or hit-test input.

## GameImage

### Identity and naming

- Class path: `/Script/UIFramework.GameImage`.
- Use the existing `visual.image` semantic role and the required `Img` instance prefix.
- Use it for every image-like visual in new assembly work: icons, portraits, item art, backgrounds, masks, frames, separators, accents, markers, and state art.
- Do not create native `/Script/UMG.Image` widgets in new layouts or WidgetTrees. Legacy assets may be inspected as evidence, but their native Image class is not a project component precedent.

### Configuration and behavior

- Use the inherited image properties exposed by the live Editor, including the verified color/opacity mapping where applicable. Discover the exact property schema before each write.
- Mark a GameImage `Is Variable` only when project code changes its brush, texture, visibility, color, or other state at runtime; keep fixed backgrounds and decoration non-variable.
- Set passive GameImages to `SelfHitTestInvisible`. Input belongs to a `Btn` or another explicit interactive component, never to a visual GameImage.
- Prefer one GameImage for one recognized complete graphic. Do not reproduce a glyph, ring, ornament, or icon from many point, line, corner, or color-fragment images merely because those primitives are easy to draw. Keep separate layers only when their runtime control, state, adaptation, resource, material/mask, progress, or accepted exception is genuinely independent.
- Choose the image's two-axis adaptation from its owner and actual Slot type. Full surfaces may Fill; rules and rails may stretch on only one axis; corner art attaches to its corresponding corner; Desired-Size art may center. A local top-left coordinate is not a universal screen anchor.

### Verification

- every `visual.image` node reads back with class `/Script/UIFramework.GameImage`;
- every instance name begins with `Img`;
- no newly assembled WidgetTree contains `/Script/UMG.Image`;
- `Is Variable` and visibility follow the common runtime-control and passive-input rules.

## VerticalBox and HorizontalBox

### Identity and purpose

- Class paths: `/Script/UMG.VerticalBox` and `/Script/UMG.HorizontalBox`.
- Use `Ver` and `Hor` instance prefixes.
- Use them for ordered natural flow along one main axis. They are layout Panels, not visual surfaces.

### Slot contract

- Every new direct child declares `flowSlot` with `size`, four-sided `padding`, `horizontalAlignment`, and `verticalAlignment`.
- `size.rule: Auto` maps to `FSlateChildSize.SizeRule = Automatic` and follows Desired Size on the Box main axis. `size.rule: Fill` maps to `Fill` and requires a positive weight for remaining-space allocation.
- Slot Alignment is independent from Slot Size. It is valid and common for a Desired-Size child to use Size `Auto` with `HAlign_Fill` and/or `VAlign_Fill` inside its allocation.
- Choose both alignments from measured child geometry and adaptation intent. A stretch intent maps to Fill; fixed edge/center intent maps to the matching alignment.

### Verification

- read the actual child Slot class and its `Size`, `Padding`, `HorizontalAlignment`, and `VerticalAlignment`;
- verify content-driven children remain `Automatic` even when an alignment is Fill;
- verify Fill weights are positive and used only when the child intentionally consumes remaining main-axis space.

## GameScrollBox

### Identity and purpose

- Class path: `/Script/UIFramework.GameScrollBox`.
- Use the `Scr` instance prefix.
- Use it only when the accepted overflow design needs scrolling; it owns scroll behavior and its direct child Slot relationship.

### Slot contract

- Every new direct child declares `scrollSlot` with four-sided Padding plus Horizontal and Vertical Alignment.
- Do not add the VerticalBox/HorizontalBox `FSlateChildSize` object to a ScrollBox Slot.
- Select both alignments from source/node dimensions, the scroll direction, cross-axis fill intent, desired content size, and owning-region adaptation. Never rely on an implicit default or a Canvas anchor that does not belong to this Slot.

### Verification

- read the actual child Slot class, Padding, and both alignments after save;
- verify the scroll axis and overflow contract still match the Requirement;
- verify passive visual descendants do not block scroll input.

## SizeBox

### Identity and naming

- Class path: `/Script/UMG.SizeBox`.
- It is a one-child `ContentWidget` that constrains the desired size reported by its child; it is not a general multi-child layout panel and has no visual style of its own.
- Use the required instance prefix `Size`, followed by a concise constraint purpose, for example `SizeMagazineWidth`, `SizeItemIcon`, `SizePortrait`, or `SizeQuestMinWidth`.
- Its child uses `/Script/UMG.SizeBoxSlot` and should normally fill the slot unless the design requires deliberate alignment.

### Appropriate application scenarios

- Constrain one stable axis while the other axis follows content, such as fixing a four-column TileView width while allowing additional rows to increase height.
- Give an icon, portrait, equipment cell, or other one-child visual a deliberate exact width and/or height when that size is part of the layout contract.
- Set a minimum desired width or height so localized or runtime content cannot collapse below the usable design size.
- Set a maximum desired width or height when a content-driven child must stop growing before it disrupts surrounding layout; define the subsequent wrap, clip, scroll, or overflow behavior separately.
- Use it as a local constraint wrapper inside a larger semantic region. The SizeBox itself does not replace that region's CanvasPanel, Overlay, VerticalBox, HorizontalBox, or other structural owner.

### Selection boundaries

- Do not use SizeBox to arrange multiple children; choose the appropriate PanelWidget.
- Do not use SizeBox to scale visual content into available space; use `ScaleBox` when scaling is the requirement.
- Do not use SizeBox as a fixed viewport merely to hide a dynamic collection. Choose `fixed-viewport` intentionally and configure the appropriate list, tile, scroll box, clipping, or paging behavior.
- Do not add a SizeBox without a concrete constraint. A one-child wrapper whose overrides, minimums, and maximums are all disabled has no justified layout purpose.
- Avoid setting both exact width and exact height when only one axis needs control. Over-constraining both axes prevents content-driven adaptation.

### Configuration rules

- Activate only the required constraints. In Unreal Python use `set_width_override`, `set_height_override`, `set_min_desired_width`, `set_min_desired_height`, `set_max_desired_width`, and `set_max_desired_height`.
- Clear unused constraints with the corresponding `clear_*` method. Do not assume that assigning a raw numeric property enables or disables its hidden override state.
- When desired size must propagate through a CanvasPanel, enable `Size To Content` on the SizeBox's `CanvasPanelSlot`; Size To Content is a Slot rule, not a SizeBox override.
- Check every ancestor on the growth axis. A fixed or clipping ancestor can still cap a correctly configured SizeBox.
- Set passive SizeBox instances to `SelfHitTestInvisible`. Mark one `Is Variable` only when project code must directly change its constraints, visibility, or other state at runtime; a static layout constraint remains non-variable.

### Verification

- instance name starts with `Size`;
- class is `/Script/UMG.SizeBox` and it owns no more than one child;
- only required width, height, minimum, or maximum constraints are active;
- unused constraints were explicitly cleared;
- the child uses `SizeBoxSlot` with intended alignment;
- Canvas Slot `Size To Content` and all ancestor constraints agree with the intended growth behavior;
- passive visibility and `Is Variable` follow the common rules.

## Overlay

### Identity and purpose

- Class path: `/Script/UMG.Overlay`.
- It derives from `PanelWidget` and stacks children inside one shared rectangle.
- Use the instance prefix `Over`.
- Use it when children need independent alignment, shared adaptive bounds, or deliberate visual layering.
- Do not add it when an ordinary `HorizontalBox` or `VerticalBox` already expresses the layout.
- Do not add it when a Button's single CanvasPanel child already provides all required internal placement and layering. `Button -> CanvasPanel` is valid and preferred over a purposeless `Button -> Overlay -> CanvasPanel` chain.
- When that direct CanvasPanel is intended to cover the whole Button, set its ButtonSlot padding to zero and both horizontal and vertical alignment to Fill. Keep non-zero padding only when the design deliberately needs a content inset.

### Map-system pattern

The Map system demonstrates an adaptive Overlay pattern: a CanvasPanel owns an `Over*` region, the Overlay owns ordered content such as a `VerticalBox`, and a background or accent can stretch along the same content-driven rectangle. This allows long localized text to grow while its backing and decoration continue to cover the complete module.

One child inside an Overlay is acceptable only when the Overlay deliberately supplies adaptive shared bounds or independent alignment that the parent slot cannot express. Record that intent as `overlayPurpose`. A vague future possibility is not enough; a purposeless one-child wrapper should be removed.

### Selection rule

- Use `overlayPurpose: layering` for deliberate multi-layer stacking.
- Use `overlayPurpose: adaptive-bounds` when backgrounds, accents, and content share one content-driven rectangle.
- Use `overlayPurpose: independent-alignment` when children require different alignment inside one shared rectangle.
- Multiple overlapping children usually make the purpose evident, but recording it is still allowed. A one-child Overlay without an explicit purpose is a review warning.
- A one-child Overlay directly between Button and CanvasPanel is a structural error even if it is labeled with a generic purpose; CanvasPanel already owns the internal layering, so promote it to be the Button's direct child.

### Slot and hit-testing rules

- Configure each `OverlaySlot` with explicit horizontal and vertical alignment appropriate to its layer.
- Use `HAlign_Fill` and `VAlign_Fill` for every child that must cover the complete Overlay region, including full-size backgrounds, masks, state layers, or shared-bound Canvas content.
- Use non-Fill alignment only when the child intentionally keeps a local desired size or uses independent edge/center alignment. Do not leave either axis at an implicit default.
- Keep passive Overlay containers `SelfHitTestInvisible`; preserve interactive descendants by avoiding a visibility mode that disables child hit testing.
- Verify the actual `OverlaySlot.horizontalAlignment` and `OverlaySlot.verticalAlignment` after save, then test longer text and different aspect ratios before accepting the layering structure.
