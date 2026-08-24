# Fight UMG rules and reference snapshot

Use this reference when `profile.system` is `fight`. The authoritative integration asset is `/Game/UI/UMG/Fight/umg_fight`; do not use `umg_fight1` as a standard reference.

## Explicit fight rules

These rules were supplied by the project owner on 2026-07-22 and are authoritative:

- Treat `umg_fight` as an integration and layout screen for independent fight feature modules.
- Do not implement feature behavior directly inside `umg_fight` with native functional controls or embedded feature hierarchies.
- Allow panels whose only purpose is integration layout or positioning.
- Implement each independent fight feature as a child Widget Blueprint under `/Game/UI/UMG/Fight/Widgets` using the `uw_fight_*` naming convention.
- Insert the completed feature child widget into `umg_fight` and adjust its integration layout there.
- When a fight feature has a runtime-variable entry count, follow `dynamic-list-widgets.md`: create a top-level collection module and a separate `ListViewItem` entry asset, then add only the collection module to `umg_fight`.
- For the fight magazine capacity grid (`profile.function: magazine`), also read `fight-magazine-grid.md` for its show-all sizing and three-state entry contract.

This restriction applies to the integration screen. A `uw_fight_*` child widget may contain the internal controls and hierarchy needed by its own feature.

## Observed standard-interface pattern

The following data was read from `/Game/UI/UMG/Fight/umg_fight` through the local Unreal MCP on 2026-07-22. Treat it as reference evidence, not as a hard rule unless it also appears in the explicit section.

- Root class: `/Script/UMG.CanvasPanel`.
- Direct functional modules: `Player`, `Cross`, `Energy`, `Weapon`, `Skill`, `Prompt`, `Item`, and `Tips`.
- Every direct functional module uses a `uw_fight_*` Widget Blueprint. Existing reference assets are currently stored in the legacy singular `Widget` folder; newly created or migrated modules use `/Game/UI/UMG/Fight/Widgets`.
- The reference tree contains no native functional leaf control directly in the integration screen.

### Observed placement

| Module | Child asset | Anchor | Alignment | Offset left/top | Auto size |
| --- | --- | --- | --- | --- | --- |
| Player | `uw_fight_player` | left-bottom | `(0,1)` | `(20,-52)` | yes |
| Cross | `uw_fight_cross` | center | `(0.5,0.5)` | `(0,0)` | no |
| Energy | `uw_fight_energy` | center-bottom | `(0.5,1)` | `(0,-50)` | yes |
| Weapon | `uw_fight_weapon` | right-bottom | `(1,1)` | `(-70,-50)` | yes |
| Skill | `uw_fight_skill` | right-bottom | `(0,0)` | `(-520,-150)` | yes |
| Prompt | `uw_fight_prompt` | right-bottom | `(1,1)` | `(-12,-250)` | yes |
| Item | `uw_fight_item` | left-top | `(0,0)` | `(20,644)` | yes |
| Tips | `uw_fight_tips` | center-top | `(0.5,0)` | `(0,150)` | yes |

Use the anchor distribution as a layout reference for new fight modules. Do not copy numeric offsets blindly; derive placement from the requested image, module purpose, and current reference resolution.

The legacy Fight `Widget` folder also contains child widgets that are not direct children of `umg_fight`, including list items, prompts, tips levels, key buttons, monster HP, and stamina. Do not automatically insert every child widget from either child folder into the integration screen; insert only the top-level feature modules required by the target design.

### Observed dynamic-list reference

- `/Game/UI/UMG/Fight/Widget/uw_fight_prompt` is a `UserWidget` collection module.
- Its `ListPrompt` instance is `/Script/UIFramework.LuaListView`, is a Blueprint variable, and points `EntryWidgetClass` at `uw_fight_prompt_list_C`.
- `/Game/UI/UMG/Fight/Widget/uw_fight_prompt_list` derives from `/Script/UIFramework.ListViewItem` and represents one row.
- Use this relationship as the designated fight reference for new runtime-variable lists; do not infer styling or numeric geometry from it when the target reference image differs.

The singular paths in this observed reference are historical evidence only. New child assets use `/Game/UI/UMG/Fight/Widgets`.
