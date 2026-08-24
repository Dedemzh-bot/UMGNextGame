# NextGame Widget Blueprint asset naming and path rules

These rules were explicitly supplied by the project owner on 2026-07-22 and expanded on 2026-07-23. Treat them as authoritative. Observed reference-interface patterns must not override them.

## Scope and creation stage

This file governs complete Widget Blueprint asset naming and Content Browser placement. It does not govern component instance names inside the WidgetTree or the internal region hierarchy.

Apply these rules before creating the Widget Blueprint asset:

1. Resolve the asset scope, system naming token, and canonical system-folder spelling.
2. For system scope, create or reuse the system folder under `/Game/UI/UMG`; for project-common scope, create or reuse `/Game/UI/UMG/Widgets`.
3. Derive the Widget Blueprint asset name.
4. For a functional child widget, create or reuse the system folder's `Widgets` subfolder.
5. Create, compile, and save the Widget Blueprint at the derived target path.

## System folder

- Store every production Widget Blueprint below `/Game/UI/UMG`.
- Create one folder per system at `/Game/UI/UMG/<SystemFolder>`.
- Name `<SystemFolder>` after the same system represented by the lower-case `system` asset-name token. They may differ in letter case for Content Browser presentation, but must not identify different systems.
- Preserve the project's existing canonical folder spelling when the system folder already exists. Do not create a second folder that differs only in case.
- When introducing a new system, create its system folder before creating the first Widget Blueprint.
- Do not create a subsystem folder unless the project owner later supplies a separate folder rule. A subsystem remains an asset-name segment.

Examples:

- `system: map`, `systemFolder: Map` → `/Game/UI/UMG/Map`
- `system: fight`, `systemFolder: Fight` → `/Game/UI/UMG/Fight`

## System-screen Widget Blueprint

- Name a system screen `umg_<system>[_<subsystem>]`.
- Omit the subsystem segment when the screen has no subsystem.
- Example: `umg_bag_sell` is the sell screen in the bag system.
- Store the screen directly in its system folder.
- Derive its folder and asset path as:
  - folder: `/Game/UI/UMG/<SystemFolder>`
  - asset: `/Game/UI/UMG/<SystemFolder>/umg_<system>[_<subsystem>]`

Examples:

- map screen: `/Game/UI/UMG/Map/umg_map`
- bag sell screen: `/Game/UI/UMG/Bag/umg_bag_sell`

## Functional child Widget Blueprint

- Name a functional child widget `uw_<system>[_<subsystem>]_<function>[_<secondary_function>]`.
- Require the function segment for child widgets. Omit the optional subsystem and secondary-function segments when they do not apply.
- Examples: `uw_fight_item` and `uw_fight_item_list`.
- Create or reuse `/Game/UI/UMG/<SystemFolder>/Widgets`.
- Store every functional child Widget Blueprint for that system in this `Widgets` subfolder.
- Derive its folder and asset path as:
  - folder: `/Game/UI/UMG/<SystemFolder>/Widgets`
  - asset: `/Game/UI/UMG/<SystemFolder>/Widgets/uw_<system>[_<subsystem>]_<function>[_<secondary_function>]`

Examples:

- fight item: `/Game/UI/UMG/Fight/Widgets/uw_fight_item`
- fight item list: `/Game/UI/UMG/Fight/Widgets/uw_fight_item_list`

Do not invent placeholder segments for omitted parts. Preserve the project-approved token for each system, subsystem, function, and secondary function.

## Project-common functional child Widget Blueprint

- Use `profile.assetScope: project-common` to declare a cross-system child widget explicitly. Omitting `assetScope` preserves the system-scoped rules above.
- Project-common scope is valid only for `assetKind: child-widget` and requires `system: common`. Keep `systemFolder` as the canonical spelling of that same token, normally `Common`; the ordinary system/system-folder identity check still applies.
- Project-common assets do not use a subsystem segment. Derive the name as `uw_common_<function>[_<secondary_function>]`.
- Store the asset directly in the project shared-widget folder:
  - folder: `/Game/UI/UMG/Widgets`
  - asset: `/Game/UI/UMG/Widgets/uw_common_<function>[_<secondary_function>]`
- A project-common list entry still requires `listRole: entry`, `secondaryFunction: list`, and `parentClass: /Script/UIFramework.ListViewItem`. Project-common scope does not relax list, naming, target, compilation, save, or verification contracts.
- Example: `/Game/UI/UMG/Widgets/uw_common_material_list` uses `system: common`, `function: material`, and `secondaryFunction: list`.

## Execution destination

- Use `mode: prototype` for exploratory work. Store the generated asset under `/Game/UI/AIPrototype` and record the formal destination in `profile.targetAsset`.
- Use `mode: production` when the project owner explicitly requests creation in the formal UMG folder.
- In production mode, require `asset.folder` and `asset.name` to exactly equal `profile.targetAsset.folder` and `profile.targetAsset.name`.
- For a system screen, create the actual asset directly under `/Game/UI/UMG/<SystemFolder>`.
- For a functional child widget, create or reuse `/Game/UI/UMG/<SystemFolder>/Widgets` and create the actual asset there.
- For an explicitly scoped project-common child widget, create or reuse `/Game/UI/UMG/Widgets` and create the actual asset there.
- Do not redirect an explicitly requested formal asset into `/Game/UI/AIPrototype`.
- Do not overwrite an existing asset unless the owner explicitly authorizes updating that exact asset.

## Rule-source precedence

Apply rule sources in this order:

1. Explicit project-owner rules in this file or another explicitly supplied reference.
2. Patterns observed from assets that the project owner designated as standard references.
3. Generic prototype rules.
4. Native Unreal defaults.

When an observed pattern conflicts with an explicit rule, keep the explicit rule and report the conflict for review.
Exclude any element the project owner identifies as unfinished, archival, temporary, or non-final from observed reference patterns. Do not count it, describe it, or derive rules from it.
