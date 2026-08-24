# Common UMG component production rules

These rules were explicitly supplied by the project owner on 2026-07-26. They apply to every system, including fight and non-fight interfaces. System-specific references may add constraints but must not weaken these rules.

## Project image component

- Map every `visual.image` role to the project component `/Script/UIFramework.GameImage` and keep the `Img` instance-name prefix.
- Use `GameImage` for icons, portraits, state art, backgrounds, masks, frames, separators, accents, and every other image-like visual in new assembly work.
- Do not create native `/Script/UMG.Image` widgets in new layouts, build plans, or WidgetTrees. Existing assets may still contain legacy native Images, but do not copy that class into new work.
- Apply the same runtime-variable and passive-hit-testing decisions to `GameImage` that previously applied to image visuals.

## Program-controlled widget variables

- During image interpretation, decide whether project code must directly read, modify, or control each WidgetTree component at runtime.
- Set `Is Variable` to true for every such component. In UILayoutSpec, record this as node-level `isVariable: true`.
- Dynamic TextBlocks include values such as level or map names, countdowns, player names, scores, ammunition, changing descriptions, and other text replaced by program data.
- Dynamic GameImages include portraits, equipment or item icons, map markers, state icons, and any brush or texture that project code replaces at runtime.
- Dynamic progress displays, lists, tiles, controls, child-widget instances, or containers also require `Is Variable` when project code directly changes, reads, populates, shows, hides, or otherwise controls that instance.
- Keep `Is Variable` false or omit `isVariable` for fixed labels, separator lines, static backgrounds, fixed frames, and other decoration that project code never references.
- Do not mark every widget as a variable by default. Make the decision from the component's functional purpose, not merely from its type or visibility.
- `Is Variable` is structural exposure for project code. It does not authorize EventGraph logic, Lua/C++ hookups, bindings, or other business behavior inside the Widget Blueprint.
- Use `UMGToolSet.UMGToolSet.ToggleWidgetAsVariable` to apply the flag; `bIsVariable` is protected and is not an ordinary `ObjectTools.set_properties` field.

Typical decisions:

| Component purpose | `Is Variable` |
| --- | --- |
| Level or map name, countdown, player name | true |
| Runtime portrait, equipment GameImage, state icon | true |
| Health, stamina, task progress, dynamic list or tile | true |
| Fixed section label that program code never replaces | false |
| Separator line, static background, fixed frame | false |

## Even font sizes

- Use a positive even integer for every explicit TextBlock font size.
- Do not use odd font sizes such as 19, 21, or 25.
- Record `font.size` explicitly for every TextBlock in UILayoutSpec so the validator can enforce the rule.
- When updating an existing TextBlock, read its complete `font` struct, change `font.size` to an even value, and preserve the font object, typeface, outline, material, letter spacing, and other existing members unless the task explicitly changes them.
- Verify the effective size after compile and save, including inherited or style-provided text whose font size was not authored directly in the layout spec.

## Passive-component hit testing

- For every component that does not need to receive click, pointer, hover, drag, scroll, or focus input, set `Visibility` to `Not Hit-Testable (Self Only)`.
- The Unreal enum/property value is `SelfHitTestInvisible` on the Widget `visibility` property.
- Apply this to passive TextBlocks, GameImages, decorations, progress displays, layout panels, and other non-interactive components.
- Use `SelfHitTestInvisible` for a passive container that may contain interactive descendants; it ignores the container itself while preserving descendant hit testing.
- A passive runtime-controlled state branch may instead use `Hidden` or `Collapsed` while inactive. Prefer `Collapsed` when the inactive branch must not occupy layout space; use `Hidden` only when preserving its layout allocation is deliberate. Its active branch uses `SelfHitTestInvisible`.
- `Visible` and `HitTestInvisible` are invalid for passive components. `Visible` needlessly receives hits, while `HitTestInvisible` disables hit testing for all descendants.
- Do not use `HitTestInvisible` (`Not Hit-Testable (Self & All Children)`) on a container whose descendants must remain interactive.
- Keep an input component hit-testable when it actually owns interaction. `LuaListView`, `LuaTileView`, and `GameScrollBox` also remain hit-testable when they must handle selection or scrolling.
- During verification, inspect the actual interaction requirement and read back `visibility`; do not infer correctness only from visual appearance.

## Button-owned interaction

- Use a Button component named with the `Btn` prefix as the input owner for every click, tap, activation, category selection, selected/unselected switch, or other discrete mode/state change.
- Make the Button's effective hit area cover the intended interactive region. Put visual Panels, GameImages, and TextBlocks inside its content layout or in the accepted layered structure while keeping those visual nodes passive.
- A state family driven by user interaction is incomplete when it contains only visual branches and no `Btn` input owner. This includes list-entry assets such as category tabs whose selected and unselected branches are switched by project code.
- Do not add EventGraph, Lua, C++, or Blueprint switching logic; the Button is the structural trigger surface and project code owns behavior.
- Specialized continuous controls keep their own semantics: Slider/RadialSlider dragging and list, tile, or scroll navigation do not become Buttons merely to satisfy this rule.

## Button visuals expressed by child GameImages

- When a Button's visible normal, hovered, or pressed state is supplied by child `GameImage` widgets or state-image branches, do not let the Button's own `WidgetStyle` draw a second visual layer.
- Set the `Draw As` value of the `WidgetStyle` `Normal`, `Hovered`, and `Pressed` brushes explicitly to `None` (`NoDrawType`). Do not leave one state on a box, border, or image draw type merely because that state is not visible in the reference.
- Keep the state GameImages or state branches responsible for their intended visual state, z-order, and visibility. The Button remains responsible for input and owns the direct content layout.
- This rule applies only when child GameImages express the button visual. A Button deliberately using its own `WidgetStyle` brush as the visual source must instead configure that style consistently with the accepted design.

## Explicit TextBlock colors

- Set `properties.color` explicitly on every TextBlock in UILayoutSpec and verify the resulting TextBlock foreground color after build.
- This includes ordinary white labels: record white as an explicit color value rather than relying on a widget, theme, or engine default.
- Preserve all RGBA channels, including alpha. When updating an existing TextBlock, read the current color first and change it only when the accepted design requires a different value.
- Do not use the color of a containing Button, GameImage, or panel as an implicit substitute for the TextBlock's own foreground color.

## Short centered button labels

- For a short, single-line label centered inside a Button, use a TextBlock CanvasPanelSlot with a center point anchor (`minimum == maximum == (0.5, 0.5)`), `alignment: (0.5, 0.5)`, and `bAutoSize: true`.
- Set that TextBlock's `Justification` to `Center` and `Visibility` to `SelfHitTestInvisible`. The Button remains the hit-testable input owner.
- Do not use a fixed label rectangle, manual left offset, or a separate centering Overlay for this common case. Reconsider the layout when the label can wrap, is long enough to need protected-edge growth, or is intentionally not centered.

## Explicit text wrapping width

- When text is intended to wrap, set `Wrap Text At` to a concrete positive pixel value.
- Do not leave `Wrap Text At` at `0` for a wrapping TextBlock.
- Store continuous paragraph source text without authored line breaks. Let the TextBlock wrap it at the specified width.
- Treat `autoWrapText` and `wrapTextAt` as separate settings. A concrete `wrapTextAt` is required even when automatic wrapping is enabled.
- Re-evaluate the wrap width after the font, margins, localization allowance, or containing layout width changes.

## Text justification and growth direction

- Explicitly set every TextBlock `Justification` to `Left`, `Center`, or `Right`; do not leave the choice implicit.
- Choose `Left` when the left boundary is stable and longer content may safely grow or wrap toward the right.
- Choose `Right` when the right boundary is stable or when content must grow toward the left to protect a character, icon, map, or other content on its right.
- Choose `Center` only when the text is visually centered and both sides provide safe expansion space. Do not use it merely because the TextBlock rectangle happens to be near the middle of the screen.
- `Justification` controls text inside its allotted geometry; it does not create adaptive geometry by itself. Pair it with the appropriate Canvas anchor, alignment, Slot width, wrapping rule, or `Size To Content` behavior.
- For dynamic single-line names or values, test a representative long value. If the protected edge must remain fixed, use the matching point anchor and alignment plus content-driven width so the text expands away from that edge.

## Text growth and localization adaptation

- Expect localized text to be longer than the source-language placeholder. Avoid a fixed-height background or decoration that clips or exposes text when the TextBlock grows.
- Prefer natural flow containers such as VerticalBox or HorizontalBox when their layout behavior fits the design.
- When an overlapping CanvasPanel layout is required, give expanding text a stretched content width, explicit wrapping width, and `bAutoSize: true`. Stretch related backgrounds or edge decorations along the axis that must follow the entry's resulting size.
- Preserve deliberate fixed margins while stretching. Do not replace every offset with a fixed absolute width or height.
- Verify with longer representative text and confirm that text, entry height, background, and decoration still agree.

## Explicit VerticalBox, HorizontalBox, and Scroll child Slots

- Treat a VerticalBox/HorizontalBox child's Slot Size and its Horizontal/Vertical Alignment as independent decisions. Size controls allocation on the Box main axis; Alignment controls how the child occupies the allocated rectangle.
- Use Slot Size `Auto` whenever a child's Desired Size should drive its main-axis allocation, including a middle child whose Horizontal or Vertical Alignment is `Fill`. Use Size `Fill` only when that child must consume weighted remaining space, and record a positive weight.
- Give every new VerticalBox/HorizontalBox child explicit Size, Padding, Horizontal Alignment, and Vertical Alignment. Give every new GameScrollBox child explicit Padding and both alignments; ScrollBoxSlot does not use the Box Size contract.
- In requirement-driven work, copy the accepted child's `panelSlotIntent` instead of re-inferring it. Direct layout nodes use `flowSlot`/`scrollSlot`; a nested shared child uses the same fields in its `widget-tree-instance` placement Slot, including Box Size for HorizontalBox/VerticalBox. If that nested source is a reuse-only Parent Class child, use `inherited-reuse-only-flow-slot` sizing compatibility for a flow parent (Auto is `content-driven`, weighted Fill is `weighted-remaining-space`) or `inherited-reuse-only-scroll-slot` for GameScrollBox (`scroll-slot`, no Box Size). These choices never replace the separately reviewed Alignment or Padding.
- Choose alignments from reference-image/node dimensions, parent direction, owning-region adaptation, expected content growth, and the scroll axis. Do not preserve an implicit engine default merely because the current sample happens to fit.
- The owner-adjusted `/Game/UI/UMG/Weapon/umg_weapon` ratio group is the verified relationship reference: `HorExperienceRatio` follows Desired Size, and each current/separator/maximum TextBlock uses `HorizontalBoxSlot.Size = Automatic` while both Slot alignments are Fill. Reuse the relationship, not its numeric offsets.

## Complete-image composition

- A raster detector component is not automatically a GameImage. When points, strokes, corners, rings, color islands, or other fragments form one complete non-text graphic with one state, runtime-control, layer, adaptation, and resource responsibility, represent the graphic with one GameImage and one texture/Brush.
- Split image layers only for accepted independent runtime control, state variation, adaptation, verified resource reuse, material/mask behavior, progress fill, or a documented accepted exception. A layer justified by independent adaptation must actually use its own element-targeted responsive intent; it cannot simultaneously inherit its owner's adaptation. Color differences or the ability to draw a shape from rectangles are not sufficient reasons.
- Keep Button hit targets and dynamic/localized TextBlocks separate from art. A stretchable Backplate and a fixed glyph are normally two separate complete semantic image groups, not `complete` plus `layer` inside one group, because their surface/adaptation/resource responsibilities differ. Merge only the glyph's own point/line fragments into its one complete GameImage.
- The owner-adjusted Weapon buttons are the decision reference: `BtnClose`, `BtnUtilityRound`, and `BtnUpgrade` retain their Backplate surface, while a visually complete close glyph, round glyph, or upgrade ornament should use one image resource rather than the observed legacy collections of untextured point and line images. This is a semantic judgment, not a blanket ban on legitimate layers.

### Prefer a content-driven Canvas module before SizeBox or Overlay

- When the design can be expressed by a CanvasPanel that follows its content, use that direct structure before adding a `SizeBox` or `Overlay` solely to preserve a fixed width or make a background follow the content.
- Set the CanvasPanel's owning CanvasPanelSlot to `bAutoSize: true`; place the ordered flow content (for example, a VerticalBox or HorizontalBox) in its own auto-sized Canvas slot so it supplies the module's desired size.
- Add the background GameImage as a sibling in that CanvasPanel with stretch anchors `(0,0)..(1,1)`, explicit design offsets/margins, and auto-size disabled, so it fills the resulting content-driven module bounds. Default the offsets to zero, but preserve non-zero or negative outward offsets when the accepted design deliberately requires bleed or extension.
- Keep `SizeBox` only when an actual width, height, minimum/maximum bound, or stable tile-column constraint is required. Keep `Overlay` only when multiple layers need a shared rectangle with independent alignment or stacking beyond this background-plus-flow-content pattern.
- Verify that the background expands with representative content and that no ancestor slot prevents the intended growth axis.

### Runtime current/maximum text groups

- A runtime progress value rendered as `current / maximum` is three independently authored TextBlocks inside one `HorizontalBox`; do not place the whole expression in one dynamic TextBlock.
- In `UILayoutSpec`, put node-level `textGroup` metadata on that `container.horizontal` node. It uses `kind: "ratio"`, an explicit design `alignment` (`Left`, `Center`, or `Right`), and `orderedChildren` in this exact order: current value, separator, maximum value.
- The current and maximum TextBlocks are independent runtime fields and must use `isVariable: true`. The separator is one static TextBlock with `text: "/"` and must remain non-variable.
- Select the HorizontalBox alignment from the visual design and safe growth direction; it is semantic metadata for the group and is not an Unreal TextBlock property write. Give each child TextBlock its own explicit text justification and even font size.

Example:

```json
"textGroup": {
  "kind": "ratio",
  "alignment": "Right",
  "orderedChildren": ["mission-status-num", "mission-status-separator", "mission-status-max"]
}
```

## Owner-adjusted reference pattern

The project owner adjusted the legacy reference `/Game/UI/UMG/Fight/Widget/uw_fight_task_list` and designated these Slot relationships as an adaptation reference. New or migrated child assets use the canonical plural `Widgets` folder.

| Component | CanvasPanelSlot behavior | Purpose |
| --- | --- | --- |
| `TxtTaskDescription` | horizontal anchors `0..1`, left margin `76`, right margin about `6`, `bAutoSize: true`, `Wrap Text At: 310` | wrap a longer description and let its desired height grow |
| `ImgTaskEntryBackground` | anchors `(0,0)..(1,1)`, zero margins | fill the complete entry as its size changes |
| `ImgTaskAccent` | anchors `(0,0)..(0,1)`, fixed width `4` | preserve a fixed-width accent while matching entry height |

The numeric values belong to this task-entry layout. Reuse the relationship, not the numbers, when another interface has different padding or width.

## Content-driven size versus fixed viewport

- Decide the intended overflow behavior before setting a collection or module size. Runtime-variable item count does not automatically mean the content should scroll.
- Enable `Size To Content` on the relevant `CanvasPanelSlot` when every generated child must remain visible and the owning layout is allowed to grow. Typical examples are capacity indicators, short status groups, equipment grids, and compact HUD collections whose complete state must be readable at once.
- Keep a deliberate fixed viewport when the design expects browsing through a potentially long result set, inventory, log, ranking, or other collection by scrolling, clipping, or paging.
- For one-axis growth, constrain only the stable axis. A fixed-column tile grid normally keeps a fixed width and allows its height to follow desired content size.
- Do not enable `Size To Content` blindly on a tile whose width also determines the column count. With no surviving width constraint, the TileView can collapse to a single column. Wrap it in a one-child width constraint such as `SizeBox`, set only `Width Override`, leave `Height Override` disabled, and enable `Size To Content` on that wrapper's Canvas slot.
- Inspect every ancestor up to the integration screen. A child cannot show all content if a parent, slot, clipping region, or screen boundary still fixes or clips the growth axis.
- `Size To Content` means follow desired size; it is not a promise that an unbounded amount of data can fit on screen. If realistic maximum content exceeds the available screen area, the design must instead define scrolling, paging, collapsing, or another overflow policy.

## Verification checklist

- every program-controlled component reads back with `bIsVariable: true` from `GetWidgets`;
- fixed decoration does not have `bIsVariable` enabled without a documented program-control requirement;
- every effective font size is even;
- every TextBlock reads back with an explicit foreground color, including white labels;
- every TextBlock has an explicit `Left`, `Center`, or `Right` justification and long dynamic samples grow in the intended safe direction;
- every `visual.image` node reads back as `/Script/UIFramework.GameImage`, never native `/Script/UMG.Image` in new assembly work;
- every accepted complete image has one realization—one GameImage or one verified shared-widget instance that supplies it—and every additional GameImage layer has an accepted independent responsibility instead of being a point/line fragment;
- every click, tap, or discrete state-switch region has a hit-testable `Btn*` input owner;
- child-GameImage Buttons read back with `Normal`, `Hovered`, and `Pressed` WidgetStyle brushes set to `NoDrawType`;
- short centered Button labels use a center-point, auto-sized Canvas slot with center justification and `SelfHitTestInvisible` visibility;
- every displayed passive component reads back as `SelfHitTestInvisible`, while an intentionally inactive state branch may read back as `Hidden` or `Collapsed`;
- every wrapping TextBlock has a positive explicit `wrapTextAt`;
- longer localized text does not overlap, clip, or escape its background;
- stretched backgrounds and decorations follow the intended content axis;
- every VerticalBox/HorizontalBox child reads back with explicit Size, Padding, and both alignments; content-driven children use Size Automatic even when an alignment is Fill;
- every GameScrollBox child reads back with explicit Padding and both alignments selected from its size and adaptation intent;
- a content-driven Canvas module uses an auto-sized parent slot, auto-sized flow content, and a stretched background GameImage before adding SizeBox or Overlay solely for those relationships; read back the background slot's actual offsets rather than assuming zero margins;
- show-all collections expose all representative preview entries without an internal scroll viewport, while fixed-viewport collections preserve their intended viewport;
- a fixed-column show-all tile retains its column count while growing only on the intended axis;
- interactive descendants remain hit-testable.
