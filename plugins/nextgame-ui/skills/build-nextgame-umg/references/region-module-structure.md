# Region-module structure

These rules were explicitly supplied by the project owner on 2026-07-23. Treat them as authoritative.

## Scope and creation stage

This file governs semantic region recognition and container hierarchy inside a Widget Blueprint. It is separate from:

- Widget Blueprint asset naming in `project-ui-rules.md`;
- WidgetTree component instance naming in `widget-component-naming.md`;
- project rules that require a functional region to become an independent `uw_*` Widget Blueprint.

A region module is a cohesive group inside the current WidgetTree. It does not automatically create another Widget Blueprint.

Apply this rule after interpreting the reference image and before creating leaf widgets:

1. Identify top-level and nested visual or functional regions.
2. Choose one suitable region container for each recognized group.
3. Place the region's leaf controls or nested regions under that container.
4. Only then assign geometry and component properties.

## Region-recognition evidence

Treat elements as one region when one or more of these signals are clear:

- they share a background, frame, header, title, or separator;
- they have consistent alignment, spacing, or proximity;
- they form one ordered row, column, wrapping group, list, or tile collection;
- they serve one visible purpose or interaction flow;
- they move, scale, scroll, show, or hide as one unit;
- they form an independent edge-anchored block.

Do not flatten recognized region content into unrelated direct children of the root CanvasPanel.

A clear area reserved only for a scene-rendered 3D model is not, by itself, a UMG region. Unless the accepted requirement assigns real UMG-owned decoration, masking, clipping, input, viewport, or render-target content to that area, keep it out of the WidgetTree and express the clearance through the surrounding real regions' anchors, margins, and responsive checks.

## Region-container selection

| Layout intent | UILayoutSpec role | Unreal class | Prefix |
|---|---|---|---|
| Free placement, overlap, or mixed absolute layout | `container.canvas` | `/Script/UMG.CanvasPanel` | `Panel` |
| Layered children sharing the same region rectangle | `container.overlay` | `/Script/UMG.Overlay` | `Over` |
| Ordered vertical content | `container.vertical` | `/Script/UMG.VerticalBox` | `Ver` |
| Ordered horizontal content | `container.horizontal` | `/Script/UMG.HorizontalBox` | `Hor` |
| Flowing or line-wrapping content | `container.wrap` | `/Script/UMG.WrapBox` | `Wrap` |
| Runtime linear repeated-data collection | `collection.lua-list` | `/Script/UIFramework.LuaListView` | `List` |
| Runtime tiled repeated-data collection | `collection.lua-tile` | `/Script/UIFramework.LuaTileView` | `Tile` |
| Scrollable hierarchy of explicitly authored child widgets | `container.game-scroll` | `/Script/UIFramework.GameScrollBox` | `Scr` |
| One child scaled into an available region | `container.scale` | `/Script/UMG.ScaleBox` | `Sca` |

Choose from layout behavior, not from visual resemblance alone.

- Use `LuaListView` and `LuaTileView` as data-driven region endpoints. They derive from `ListViewBase`, are not `PanelWidget` containers, and must not receive static WidgetTree children.
- Use `GameScrollBox` when explicitly authored child widgets must scroll. It derives through `ScrollBox` from `PanelWidget` and may own children.
- Use `ScaleBox` for exactly one child.
- Use `Overlay` when children share one rectangle and need independent alignment, adaptive shared bounds, or stacking. Do not add it when a single flow container or a Button's direct CanvasPanel child already provides the required layout.
- Do not use an unmapped container type as a project standard unless the owner supplies that rule.

### Content-driven Canvas modules

When a compact region consists of a background plus ordered content whose desired size should define the module bounds, keep the hierarchy direct:

1. Put one `CanvasPanel` region in an auto-sized owning Canvas slot.
2. Put its flow content in an auto-sized Canvas slot so the content supplies the region's desired size.
3. Put the background `GameImage` in the same CanvasPanel with full stretch anchors, explicit design offsets/margins, and auto-size disabled so it fills those bounds. Default the offsets to zero, but retain non-zero or negative outward offsets when the accepted design deliberately requires bleed or extension.

Do not introduce a `SizeBox` merely to retain a fixed width, or an `Overlay` merely to make this single background follow the flow content. Use those containers only when their distinct sizing or layering behavior is genuinely required.

## UILayoutSpec representation

- Set `profile.regionGrouping` to `true` when the reference contains multiple recognizable regions.
- Set it to `false` only for a genuinely atomic widget with no meaningful internal groups.
- Add a unique lower-case `regionPurpose` token to every recognized region container, for example `mission-tasks`, `map-viewport`, or `map-legend`.
- Keep all ordinary leaf content below a region container when `regionGrouping` is `true`.
- Allow a direct root child without a region only when it is a truly global layer. Mark it with `rootLayer: background` or `rootLayer: overlay`.
- Do not use `rootLayer` to bypass grouping for a normal content block.
- Keep every node rectangle normalized against the full reference image. A child region's rectangle must remain inside its parent region rectangle.

## Map-screen example

A map reference with the structure described by the project owner should normally produce a hierarchy similar to:

```text
PanelRoot
├─ ImgScreenBackground                     [rootLayer: background]
├─ HorTopNavigation                        [regionPurpose: top-navigation]
├─ PanelMissionTasks                       [regionPurpose: mission-tasks]
├─ PanelMapViewport                        [regionPurpose: map-viewport]
│  └─ PanelMapInfo                         [regionPurpose: map-info]
├─ VerMapLegend                            [regionPurpose: map-legend]
└─ HorBottomHints                          [regionPurpose: bottom-hints]
```

The exact container type may change with the real layout behavior. The requirement is that every clearly recognized group receives an appropriate structural container instead of being flattened into the root.

## Validation expectations

- Require at least one region when `profile.regionGrouping` is `true`.
- Require every non-global root child to be a region container.
- Require every ordinary descendant to have a region ancestor.
- Reject `regionPurpose` on component types that cannot represent regions.
- Reject empty PanelWidget-based regions. Also reject dummy `GameImage`, transparent image, wrapper, or sizing children whose only purpose is to make a scene-model reserve look non-empty. `LuaListView` and `LuaTileView` are valid leaf regions.
- Reject duplicate `regionPurpose` values and child rectangles outside their parent region.
- Review content-driven background modules for the direct auto-sized Canvas, stretched background GameImage, and auto-sized flow-content relationship before accepting a SizeBox or Overlay; read back the background slot's actual offsets instead of assuming zero margins.
