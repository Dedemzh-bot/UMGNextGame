# NextGame project screen layout rules

These rules were explicitly supplied and refined by the project owner on 2026-07-31, 2026-08-20, and 2026-08-24. They define the target design coordinate space, analysis-selected Designer mode, and per-axis adaptation behavior. `profile.assetKind` and asset naming remain independent from the Designer mode decision.

## Designer mode analysis contract

- Resolve the basename from the asset actually mutated: prototype uses `asset.name` and ignores future `profile.targetAsset` metadata for this guard; production/formal lowering prefers `profile.targetAsset.name` and falls back to `asset.name`.
- Require every resolved `umg_*` basename to use `FillScreen`. Explicit `Desired` is invalid; an archived missing value remains readable and resolves to hard-rule `FillScreen`.
- Only formal `uw_*` targets may use interface analysis to select `Desired` or `FillScreen`. Missing or unclear `uw_*` decisions use `fallback-unclear -> FillScreen`; unknown or legacy target basenames use `fallback-unknown-target -> FillScreen`.
- `profile.assetKind` and target folder alone never select the mode.
- For a `uw_*` `Desired` decision, require positive evidence that the Widget is a local/content-sized control, its root content produces a non-zero Desired Size, and a parent/host is responsible for placement or constraints. In UILayoutSpec, prove the executable root size with at least one root-direct child that has either a point-anchored Canvas Slot with `autoSize: false` and positive `right`/`bottom` sizes, or `contentDrivenSize` containing `verified: true`, positive `[width, height]` `measuredDesiredSize`, and a valid `evidenceId`. An empty root, auto-sized fixed Slot, verified-only record, and zero-offset full-stretch-only content are invalid. A list entry must additionally satisfy its stricter unique first-Panel size rule.
- `FillScreen` and `Desired` are the only supported execution values. Reject `DesiredOnScreen`, `Custom`, and `CustomOnScreen`.

## Complete-screen design canvas

- Use `2560 × 1440` as the unified design canvas for every complete NextGame system screen.
- Every UILayoutSpec with `profile.assetKind: "screen"` must use `referenceSize: [2560, 1440]`, in both prototype and production modes.
- When interface analysis establishes this as a complete viewport-filling screen, set the generated Widget Blueprint CDO `DesignSizeMode` to `FillScreen` and record `profile.designSizeMode: "FillScreen"`. This is the Designer `Screen Size` dropdown, not the numeric design canvas.
- Treat the source image's pixel size as measurement evidence only. Convert its geometry into normalized rectangles, then author those rectangles against the `2560 × 1440` project canvas.
- Do not preserve a `1920 × 1080`, `2580 × 1440`, or other source-size rectangle as a centered or top-left fixed inner canvas.
- Keep the root node at `[0, 0, 1, 1]`. Stretch global backgrounds and full-screen overlays across the complete root canvas.
- Recalculate every screen-level region anchor and CanvasPanel Slot for the project canvas. Changing only `referenceSize` without remapping legacy geometry is invalid.

## Local child-widget dimensions

- `profile.assetKind: "child-widget"` assets use the dimensions required by their own feature, list entry, tile, or reusable module.
- Do not force a child widget or a `ListViewItem` entry to `2560 × 1440`.
- When analysis has the positive local/content-sized evidence described above, set the generated child CDO `DesignSizeMode` to `Desired` and record `profile.designSizeMode: "Desired"`. This commonly applies to reusable controls and collection entries, but their `child-widget` classification or `uw_` name does not prove it. Verify that the root content produces a non-zero Desired Size; do not change the mode to `Custom` or write `DesignTimeSize` merely to imitate `referenceSize`.
- When a screen-local collection changes size, update both its viewport geometry and the entry asset's local desired size where required.

The old `profile.assetKind: prototype` cannot identify a complete screen versus a local control by itself. Preserve archived inputs, but classify every new asset as `screen` or `child-widget` for the independent asset-structure contract. A missing legacy/standalone value resolves to `umg-target-hard-rule -> FillScreen` for an actual `umg_*` asset, `fallback-unclear -> FillScreen` for `uw_*`, or `fallback-unknown-target -> FillScreen` otherwise.

## Per-axis responsive intent

- Decide horizontal and vertical adaptation independently for every screen-level region and every important edge-attached Canvas child.
- Horizontal intent is one of `left`, `center`, `right`, or `stretch`. Vertical intent is one of `top`, `center`, `bottom`, or `stretch`.
- Record the decision in `adaptiveLayout` with a concise reason when `profile.adaptive` is true. The metadata must agree with the actual point anchor or stretched `slotLayout` anchors.
- Use stretch on only the axis that should absorb viewport growth. Preserve explicit margins on that axis and keep the stable axis constrained.
- A left-side roster that must use additional screen height should keep its fixed left position and width while both its containing region and list viewport stretch between top and bottom margins. Keep bottom actions anchored to the region's bottom so the list receives the additional height.

## Semantic anchor selection

- Choose a region anchor from its content gravity and composition role, not only from the center of its measured rectangle.
- A region whose title, rules, and content are visually left-aligned should normally remain attached to the left side. A right-side status region should normally remain attached to the right side.
- Use a center anchor only when the region itself is conceptually centered and may expand or move symmetrically without invading protected content.
- On model-view, map-view, or other composite screens, treat the central presentation area as protected. Edge modules should remain attached to their own edge as the viewport widens so the added width reveals more central content instead of moving edge UI inward.
- A protected presentation area is a composition constraint, not automatic authorization for a WidgetTree container. When a 3D model is rendered by the scene and the area owns no accepted UMG content, reserve it only through the geometry and responsive behavior of the surrounding real modules; do not create an empty Panel or placeholder visual for it.
- Evaluate the two axes separately. A module can be `left` horizontally and `center` vertically, or fixed horizontally and `stretch` vertically.

## Decorative-image adaptation

- Decide decorative adaptation from the image's semantic owner and actual parent Slot, not from a blanket top-left anchor rule. First establish whether the decoration follows its region or adapts independently; then decide horizontal and vertical intent separately.
- A full-region backplate normally fills its owner. A long header rule normally stretches horizontally while retaining its vertical relationship. A side rail normally remains attached to its side and stretches vertically. A corner ornament uses the matching corner anchor/alignment. A centered badge normally keeps Desired Size and centers inside its allocated Slot.
- A local `[0,0]` Canvas position inside an already adapted fixed-size Panel is only a local coordinate. It does not justify attaching that decoration, its parent region, or a screen-level copy to the top-left of the viewport.
- Canvas children express this decision through anchors and `slotLayout`; Overlay, VerticalBox, HorizontalBox, and GameScrollBox children express it through their own Slot alignment contracts. Do not copy Canvas anchors onto a non-Canvas relationship.
- Use the owner-adjusted `/Game/UI/UMG/Weapon/umg_weapon` only as a relationship reference: the header rule stretches horizontally, the left rail stretches vertically, the right panel stays right-attached and stretches vertically, its corners attach to their corresponding corners, and the bottom action remains right-bottom. Never copy the asset's concrete offsets or assume every local decoration shares those behaviors.

## Screen coverage and verification

- A complete screen must be previewed or rendered at `2560 × 1440`.
- Compare the occupied content bounds with the reference layout. Empty space is acceptable only when it is intentional composition, such as a reserved character-render area; that visual clearance does not require a corresponding UMG widget.
- Treat unintended blank edge bands, a visibly smaller fixed inner frame, or content that uses only the former source height as a layout error.
- Verify the full-screen background, major semantic regions, list viewports, bottom actions, and edge-anchored controls after compile and save.
- Verify adaptive screens at one taller and one wider viewport in addition to `2560 × 1440`. Check preserved edge margins, intended stretch axes, stable fixed axes, and whether central protected content gains rather than loses space.
