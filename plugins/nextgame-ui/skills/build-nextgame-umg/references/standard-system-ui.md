# Standard non-fight system UMG structure

These rules were confirmed by the project owner on 2026-07-28 after review of `/Game/UI/UMG/Map/umg_map` and its `uw_map_*` assets. They define the normal system-development model for every project UI system except Fight. Fight remains the stricter integration-shell exception described in `fight-ui.md`.

## Tool responsibility boundary

- Build and verify Widget Blueprint assets, WidgetTree hierarchy, `Is Variable` flags, component properties, list-to-entry class relationships, compile state, save state, and visual structure.
- Do not create, connect, or validate Lua, C++, or Blueprint business logic.
- Do not add EventGraph behavior as part of UMG production. Project code implements UI behavior separately.
- A missing business-code integration is not a UMG production warning or completion blocker.

## Asset layers

Use these layers for a normal system:

1. System screen: `/Game/UI/UMG/<SystemFolder>/umg_<system>[_<subsystem>]`.
2. Functional child widgets: `/Game/UI/UMG/<SystemFolder>/Widgets/uw_<system>_*`.
3. Data-entry widgets: `uw_<system>_*_list`, derived from `/Script/UIFramework.ListViewItem`.
4. Leaf UMG components inside the owning screen or child widget.

## System-screen responsibilities

The system screen owns system-wide composition:

- global background and screen-level layers;
- top-level anchors and geometry;
- semantic regions such as navigation, map viewport, task area, legend, and bottom prompts;
- layout-only Panel containers;
- screen-local `LuaListView` or `LuaTileView` instances when the collection is only used by that screen and does not need an independent feature shell.

The screen must still group each cohesive region under an appropriate Panel. Do not flatten unrelated leaves into the root.

Here, a semantic region means UMG-owned content. Scene-rendered 3D-model clearance is not a layout-only Panel responsibility and is not sufficient reason to create a Panel, transparent `GameImage`, or placeholder wrapper.

## When to extract a child widget

The default for a standard (non-Fight) screen is ordinary semantic Panels and
screen-local `LuaListView`/`LuaTileView` leaves. Cohesion, visual complexity,
self-contained state, or a locally complete region is not, by itself, evidence
for a `uw_*` functional wrapper. Keep that region in the screen and record the
semantic Panel in the layout tree.

Create a separate asset only when one of these explicit boundaries is present:

- a data-driven list entry (`ListViewItem`),
- an explicitly runtime-created template,
- proven reuse across screens, or
- the user explicitly requests a reusable widget.

The asset plan must cite evidence (or the explicit user text) for the selected
boundary. Fight is the integration-shell exception: its feature modules remain
`uw_fight_*` assets as required by `fight-ui.md`.

Do not create a child asset for a Panel used only for local alignment, stacking,
stretching, anchoring, cohesive grouping, or visual complexity.

### Asset inventory gate

Every planned or discovered asset is classified as exactly one of:

1. `screen-root` (the system screen asset),
2. `entry-widget-class` (referenced by a collection's `EntryWidgetClass`),
3. `runtime-template` (explicitly created by project code),
4. `reusable-widget` (proven cross-screen reuse or explicit user request),
5. `statically-referenced` (reserved for explicit system rules such as Fight), or
6. `stale-candidate` (no valid reference or authorized template claim).

The classification and its evidence are retained in the requirement/build
inventory. A `stale-candidate` is a final-verification error until it is removed
or archived with explicit authorization; do not silently retain it.

## Collection ownership

- Every runtime-variable collection uses `LuaListView` or `LuaTileView`; do not author duplicate static rows. Treat repeated same-structure rows as collection candidates when only their loaded text, numbers, images, or state differ, even when the screenshot presents a fixed count.
- Every collection entry is a separate `ListViewItem` Widget Blueprint and is assigned through `EntryWidgetClass`.
- A normal system screen may own the collection directly when it is screen-local, as demonstrated by `ListTab`, `ListTask`, `ListBigPt`, and `ListResPt` in `umg_map`.
- Extract a separate collection child module only when it is an evidenced runtime template, has proven cross-screen reuse, or is explicitly requested as reusable; frame/controls/cohesion/feature identity alone are insufficient.
- Do not nest a scrolling `LuaListView` inside a `GameScrollBox` for the same content. Let the list own scrolling.
- List population and behavior are project-code responsibilities outside this tool's scope.

## Runtime-created visual templates

Child assets may be intentionally absent from the static screen tree when they are templates for project-code creation. Examples from the Map system include map areas, extraction markers, and map-point widgets.

Treat absence from the static tree as intentional only when the asset has a clear template role. Verify the asset itself, but do not invent code wiring or force it into the screen.

## Adaptive module pattern

For content whose height follows text:

- use a flow container such as `VerticalBox` for ordered content;
- use an `Overlay` when content shares one adaptive layer or must align within the same region;
- give wrapping text a positive `Wrap Text At`;
- stretch the background or edge decoration along the content-growth axis;
- preserve independent TextBlocks for independently bounded text;
- verify longer placeholder content against the complete background and decoration relationship.

## Build and verification order

1. Build or update entry and reusable template assets.
2. Compile entry assets before assigning them to collections.
3. Build or update functional child modules.
4. Integrate regions, child modules, and screen-local collections into the system screen.
5. Compile and save changed child assets, then compile and save the screen.
6. Verify hierarchy, parent classes, `Is Variable` decisions, `EntryWidgetClass`, key Slot properties, naming warnings, passive hit testing, font sizes, and wrapping widths.

Business-code connection is not part of this sequence.
