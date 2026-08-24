# Fight magazine capacity grid

Use this feature-specific reference when `profile.system` is `fight` and `profile.function` is `magazine`.

## Asset relationship

```text
/Game/UI/UMG/Fight/Widgets/uw_fight_magazine
PanelRoot : CanvasPanel
`-- SizeMagazineWidth : SizeBox (Width Override 180; no Height Override; Canvas Slot Size To Content)
    `-- TileMagazine : LuaTileView (42 x 42 entries; 4 px spacing; display-only)

/Game/UI/UMG/Fight/Widgets/uw_fight_magazine_list : ListViewItem
PanelRoot
`-- OverCell
    |-- ImgCellFrame : fixed frame
    `-- ImgCellState : runtime-variable state image
```

The collection module has `profile.collectionSizing: show-all`. It preserves four columns through the width-only `SizeBox` constraint and grows vertically as rows are added. `TileMagazine` uses selection none, consumes mouse wheel never, and is passive because the feature is a status display rather than a browsing viewport.

## Entry state contract

Project code controls the single variable `ImgCellState`; do not add Blueprint graph logic or duplicate state images.

| State | Meaning | Color and opacity |
| --- | --- | --- |
| `Existing` | item already in storage before this intake | `[1, 1, 1, 1]` white |
| `Incoming` | item entering storage in the current intake | `[0.5, 0.5, 0.5, 1]` half grey |
| `Empty` | unoccupied capacity | `[1, 1, 1, 0]` transparent fill; frame remains |

`ImgCellFrame` is fixed decoration and is not a variable. `ImgCellState` is a variable because project code changes its state presentation.

## Verification

- compile and save both assets;
- confirm the entry derives from `/Script/UIFramework.ListViewItem`;
- confirm `TileMagazine.EntryWidgetClass` targets `uw_fight_magazine_list_C`;
- confirm the SizeBox width override is active and its height override is disabled;
- confirm the SizeBox Canvas slot has `Size To Content` enabled;
- use at least 16 designer preview entries to prove the fourth row is visible without scrolling and the layout remains four columns;
- confirm the integration screen is unchanged unless integration was separately requested.
