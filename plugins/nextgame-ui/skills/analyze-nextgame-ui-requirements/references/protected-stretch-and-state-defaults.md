# Protected Stretch Regions And Default Visual States

Use these rules for every NextGame requirement analysis, across fight and non-fight systems.

## Protected stretch regions

When the evidence identifies a region whose purpose is to preserve or reveal the central model, map, scene, or another protected composition area while the viewport changes, classify that region as a screen-expanding protected stretch region.

First distinguish UMG-owned content from externally rendered scene content. A map viewport, mask, frame, overlay, or interactive surface that is genuinely owned by UMG may be an in-scope protected region. A 3D character or other model rendered by the scene is external content when no UMG surface is required there: retain its evidence as out of build scope, do not synthesize an in-scope region or placeholder element solely to reserve its pixels, and express clearance through the responsive intent of the real surrounding UMG regions.

- Record the adaptive intent independently on the horizontal and vertical axes.
- Mark every axis intended to absorb viewport growth as `stretch`; do not reduce the region to a fixed rectangle or a single-point anchor.
- State which margins or neighboring edge modules remain stable while the protected region receives the added width or height.
- Trace the stretch intent through the owning screen region and every ancestor that could constrain that axis.
- Include wider-viewport and taller-viewport acceptance checks. The added screen area must enlarge the protected region instead of producing unused bands or pushing edge modules into it.
- Do not infer stretch solely from empty pixels. Cite image composition, neighboring edge ownership, an explicit user decision, or a project reference.
- Do not turn scene-model clearance into an empty Panel, transparent image, SizeBox, Overlay, or dummy wrapper. Wider/taller acceptance checks for external scene content must verify that the actual edge modules keep their ownership and margins without claiming a WidgetTree node for the scene area.

## Default state for visual-state families

When a component family contains `unselected` plus any other visual states, the default designer-visible state is `unselected` unless the user explicitly specifies another default. This applies when the family also contains `selected`, `locked`, `unlocked`, availability, or other code-controlled branches.

- Record `unselected` as the default state.
- Treat every other accepted visual state as runtime-controlled by project code.
- Preserve evidence-backed states as independent axes where appropriate; do not invent unsupported state combinations.
- For composite presentations, describe the complete `unselected` branch and every evidence-backed alternate branch. The build handoff must start with only `unselected` visible.
- An explicit user instruction may override this default for a specific component family and must be recorded as accepted evidence.
