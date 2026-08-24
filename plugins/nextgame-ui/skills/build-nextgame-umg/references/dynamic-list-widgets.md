# Data-driven list Widget Blueprints

These rules were explicitly supplied by the project owner on 2026-07-26 and grounded in the designated fight references `/Game/UI/UMG/Fight/Widget/uw_fight_prompt` and `/Game/UI/UMG/Fight/Widget/uw_fight_prompt_list`.

Those two designated references remain in the legacy singular `Widget` folder. They may be inspected as historical evidence, but every newly created or migrated child asset uses the canonical plural `Widgets` folder.

Read `component-knowledge.md` for the reusable `LuaListView` component identity, property meanings, input behavior, and verification contract. This file governs how a dynamic-list feature is authored as an asset pair.

## When to use a collection

- Use `LuaListView` for a linear collection whose runtime item count can change.
- Use `LuaTileView` for a tiled collection whose runtime item count can change.
- Prefer a collection when repeated entries have the same WidgetTree structure and only their loaded text, numerical values, images/brushes, or visual state differ. A screenshot's fixed instance count is sample data, not evidence that the rows should be authored separately.
- Keep repeated widgets as individual static nodes only when they are genuinely fixed decoration or the accepted requirement explicitly makes their contents and structure independently authored. A fixed viewport or known initial count does not change this list-preferred rule.
- Do not simulate runtime collection data by statically duplicating row widgets inside a `CanvasPanel`, `VerticalBox`, or `GameScrollBox`.
- Keep `LuaListView` and `LuaTileView` as WidgetTree leaves. They are data-driven endpoints and must not own static WidgetTree children.

## Asset pair

Create an entry Widget Blueprint for every dynamic list. Create a separate collection-module Widget Blueprint only when the collection has its own reusable frame, controls, placement, or feature identity:

1. Optional collection module: `uw_<system>[_<subsystem>]_<function>` with `profile.listRole: container`.
2. Entry widget: `uw_<system>[_<subsystem>]_<function>_list` with `profile.listRole: entry`, `profile.secondaryFunction: list`, and `profile.parentClass: /Script/UIFramework.ListViewItem`.

Store both below `/Game/UI/UMG/<SystemFolder>/Widgets`.

The collection module is normally a `UserWidget` containing a mapped collection instance such as `ListPrompt`. In a normal non-fight system, a screen-local collection such as `ListTask` may instead live directly in the system screen. The entry asset always represents exactly one row or tile.

## Required relationship

- Compile the entry asset first.
- Set the collection's `entryWidgetClass` to the entry asset generated class, for example `/Game/UI/UMG/Fight/Widgets/uw_fight_task_list.uw_fight_task_list_C`.
- Compile the collection module after assigning `entryWidgetClass`.
- Integrate the collection module or the screen-local list instance. Do not add the entry asset directly to the integration screen.
- In `umg_fight`, keep the collection module as a direct `uw_fight_*` child of the integration root, following `fight-ui.md`.

## Entry contents and variables

### Local entry root sizing

The entry's local `referenceSize` must be materialized in UMG. Its root may be
the required structural Canvas, but the first internal structural Panel must
have an explicit fixed Slot width and height equal to `referenceSize` (or an
accepted content-driven record with `verified: true`, positive two-axis
`measuredDesiredSize`, and a valid `evidenceId`). A root Canvas with
dual-axis stretch and zero offsets is not a size contract and fails validation.
Internals may fill that first Panel. Do not insert a `SizeBox` solely to give
the entry root its size; `SizeBox` remains reserved for an independently
justified one-child constraint.

- Keep one entry's icon, title, description, and other fields inside the entry Widget Blueprint.
- Use a project `GameImage` for an icon and separate `TextBlock` components for independently bounded title and description text.
- Use one continuous description paragraph instead of authored line breaks, and set a concrete positive `Wrap Text At` width when it wraps.
- Set `isVariable: true` on the `LuaListView` or `LuaTileView` collection instance because project code must populate or control it.
- Set `isVariable: true` on entry icons, titles, descriptions, counts, progress displays, and other fields whose runtime content changes.
- Leave entry backgrounds, separators, fixed frames, and other purely decorative fields false or omitted unless project code has a concrete need to control them.
- Do not add EventGraph behavior or business-data population as part of UMG production; project code owns runtime behavior.
- Apply the common passive-component hit-testing rule to entry visuals so they do not block list input.

## Designer preview

- Use `numDesignerPreviewEntries` only to review repeated layout in the Widget Blueprint designer.
- Treat designer preview entries as virtual data; they do not create runtime items or replace the runtime data source.
- For a linear `LuaListView`, configure its intended list spacing on the collection rather than embedding blank spacer entries.
- For `LuaTileView`, do not use `Horizontal Entry Spacing` or `Vertical Entry Spacing` to widen the visible gap. Keep them at `0` or default and create the tile pitch through positive `Entry Width` and `Entry Height` values larger than the visible content footprint inside one entry.

## Collection sizing contract

Every collection container spec must declare one overflow contract in `profile.collectionSizing`:

- `show-all`: all generated entries are part of the visible module. The module grows on the collection axis and must not become an internal scroll viewport merely because the preview or runtime count exceeds the reference count.
- `fixed-viewport`: the collection keeps a deliberate viewport and uses the product's intended scroll, clip, or paging behavior for overflow.

Choose this from the feature requirement, not from the component default. A dynamic collection can use either contract.

For `show-all`, enable `Size To Content` on the direct child slot below the nearest `CanvasPanel` ancestor. Verify that ancestor slots also permit growth. If a TileView must preserve a fixed number of columns, do not auto-size it without a width constraint: place it inside a `SizeBox`, set only `Width Override`, leave `Height Override` disabled, let the tile fill the `SizeBox`, and auto-size the wrapper's Canvas slot. This preserves the width while desired height grows with additional rows.

For a display-only `show-all` collection, set selection to none, consume mouse wheel to never, and use passive visibility. Keep a collection interactive only when the design actually requires selection, scrolling, focus, pointer, or other input.

When an entry supports click/tap selection or switches selected, unselected, locked, or another discrete state, the entry Widget Blueprint must include a hit-area `Btn*` component. Place the visual state branches below its content layout, keep them passive, and let project code own the actual state change. A `LuaListView` or `LuaTileView` selection setting does not replace the entry's required Button trigger surface.

`numDesignerPreviewEntries` is only a preview sample; it is never a capacity limit. Validate a show-all collection with more preview entries than the original screenshot or previous fixed height could display.

## Designated fight reference

Observed reference relationship:

```text
uw_fight_prompt                         parent: UserWidget
-- ListPrompt                           class: LuaListView, Blueprint variable
   -- EntryWidgetClass                   uw_fight_prompt_list_C

uw_fight_prompt_list                    parent: ListViewItem
-- one prompt entry row
```

The reference EventGraphs contain only disabled default widget events; the structural collection-to-entry relationship does not require extra Blueprint graph logic.

## Verification

Verify all of the following after compile and save:

- entry parent class is `/Script/UIFramework.ListViewItem`;
- collection class is `/Script/UIFramework.LuaListView` or `/Script/UIFramework.LuaTileView`;
- collection `entryWidgetClass` points to the intended entry generated class;
- collection reads back with `bIsVariable: true`;
- each program-controlled entry field reads back with `bIsVariable: true`, while fixed decoration is not unnecessarily exposed;
- collection orientation, spacing, and designer preview count match the layout;
- every Tile has positive `Entry Width` and `Entry Height`; visible Tile gaps come from the entry-cell pitch versus the visible entry-content footprint, while Horizontal/Vertical Entry Spacing remain zero/default;
- `profile.collectionSizing` matches the feature's required overflow behavior;
- a `show-all` collection remains fully visible beyond the original reference count and its nearest Canvas child has `Size To Content` enabled;
- a fixed-column `show-all` tile preserves its column count through a width-only constraint;
- the entry asset is not directly integrated as a static screen child;
- the first internal structural Panel has a non-zero, explicit local size (or
  an accepted content-driven sizing contract), and is not only a zero-offset
  full-stretch Canvas child;
- no unnecessary Lua, C++, or Blueprint business hookup was introduced.
- every click/tap or discrete state-switch entry contains a hit-testable `Btn*` input owner.
