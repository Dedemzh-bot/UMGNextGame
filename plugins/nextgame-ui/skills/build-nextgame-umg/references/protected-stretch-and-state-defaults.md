# Protected Stretch Regions And Default Visual States

Apply these production rules to every NextGame UMG build, across fight and non-fight systems.

## Distinguish UMG-owned protected regions from scene-rendered clearance

A protected region exists in the WidgetTree only when the accepted requirement assigns real UMG-owned content to that area. Examples include accepted decoration, a mask or clipping surface, an input surface, a viewport, or render-target presentation. Build the smallest real subtree that owns that accepted content and preserve its required stretch chain.

A clear area reserved only for a character or other 3D model rendered by the Unreal scene is a composition constraint, not a UMG region:

- Do not create an empty Panel, transparent `GameImage`, `SizeBox`, `Overlay`, `CanvasPanel`, or another dummy wrapper to frame, size, or reserve the scene-model area.
- Do not add a decorative or sizing child whose only purpose is to make a placeholder region appear non-empty or to bypass `region.empty` validation.
- Do not assign `regionPurpose` to scene-rendered clearance or map it to a generated WidgetTree node.
- Preserve the clearance through the anchors, margins, widths, and stretch behavior of the surrounding real UMG modules. Verify those modules at the baseline canvas and at the required wider and taller viewports so they do not drift into the scene model.
- Create a WidgetTree subtree in that area only when accepted evidence explicitly assigns real UMG decoration, masking, clipping, input, viewport, or render-target content to it. Every such node must map to that accepted content; the exception does not authorize a dummy placeholder.

## Build protected stretch regions as screen-expanding regions

When the accepted requirement marks a protected region as absorbing viewport width or height changes:

- Use stretch anchors on every designated axis and retain explicit baseline margins on the non-expanding edges.
- Preserve the stretch chain through the region host and every ancestor Slot. No fixed-height, fixed-width, center-point anchor, or child wrapper may cap an axis that is declared to expand.
- Keep surrounding left, right, top, or bottom modules attached to their semantic edges so added viewport space is assigned to the protected region.
- Verify the baseline `2560x1440` canvas and at least one wider and one taller viewport. The protected region must grow on its declared axes without unintended bands, clipping, overlap, or drift into edge modules.

## Full-height screen shell and background chains

When a screen shell must cover taller viewports, mark each participating node with node-level `fullHeight: true` in its `UILayoutSpec`. This marker is metadata only; it is never sent to Unreal as a Widget property.

- Mark the full-screen or screen-region shell and the bottom/background child that visually covers the shell. Marking only the parent does not protect a child that still stops at the design height.
- Each marked node must be a direct CanvasPanel child. Its CanvasPanel Slot, and the Slot of every CanvasPanel ancestor in the route to the root, must use vertical anchors `minimum.y: 0`, `maximum.y: 1`, and `autoSize: false`.
- Keep explicit top and bottom margins only when the design intentionally leaves them; a full-height background normally uses zero margins on that axis.
- Render or preview the screen at a taller viewport after compile and save. Treat any uncovered lower band, background bleed-through, or fixed-height shell as a layout failure.

## Initialize visual-state families as unselected

For every accepted component family that includes `unselected` and one or more alternate states such as `selected`, `locked`, or `unlocked`:

- Make the complete `unselected` branch the initial designer-visible branch.
- Set the active passive `unselected` branch to `SelfHitTestInvisible`.
- Set all other code-controlled state branches to `Collapsed` initially, unless an accepted requirement explicitly needs `Hidden` to preserve layout.
- Mark program-controlled state branch roots `Is Variable`.
- Build every accepted composite state branch completely, but do not create Blueprint, Lua, or C++ switching logic. Project code owns runtime state changes.
- When the state family has a `user-interaction` control input, add a hit-testable `Btn*` owner that covers the complete intended trigger region and hosts the visual state content. Passive Panels, GameImages, and TextBlocks do not substitute for this Button.
- An explicit accepted per-family default overrides this general rule.
