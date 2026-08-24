# Unreal MCP workflow

## Server

Connect through the plugin dependency named `unreal-editor` at `http://127.0.0.1:8000/mcp`. The server exposes registry wrappers. Use `call_tool` with `toolset_name`, `tool_name`, and `arguments`.

## Toolsets

- `UMGToolSet.UMGToolSet`: create, inspect, modify, compile, and organize Widget Blueprints.
- `editor_toolset.toolsets.object.ObjectTools`: list, read, and write UObject and Slot properties.
- `editor_toolset.toolsets.asset.AssetTools`: save generated assets.
- `EditorToolset.EditorAppToolset` or `SlateInspectorToolset.SlateInspectorToolset`: capture a preview when available.

## Required sequence

1. Use `ListWidgetBlueprints` or asset tools to check the destination.
2. Use `CreateWidgetBlueprint` with `/Script/UMG.UserWidget` as the parent.
3. Use `AddWidget` in parent-before-child order and preserve returned `widget` and `slot` `refPath` values.
4. When a node declares `isVariable`, call `ToggleWidgetAsVariable` with the Widget Blueprint, returned Widget reference, and the intended boolean value. Do not write protected `bIsVariable` through ObjectTools.
5. Before every ordinary property write, call `ObjectTools.list_properties`, `ObjectTools.get_properties`, then `ObjectTools.set_properties` with exact discovered names.
6. Use `CompileWidgetBlueprint` after all structural and WidgetTree property changes.
7. After that final compile, use `BlueprintTools.get_default_object` on the WidgetBlueprint, then ObjectTools `list_properties -> get_properties -> set_properties` on the returned generated `UUserWidget` CDO. Set only `designSizeMode`: `FillScreen` for a complete screen and `Desired` for a child widget or collection entry.
8. Use `AssetTools.save_assets` with the package path, without the `.AssetName` suffix, only after compile and the CDO write succeed.
9. Reacquire the generated CDO after save and verify actual `designSizeMode`; also use `GetWidgets` and property reads to verify the saved result, including each returned `bIsVariable` flag. Read collection `EntryWidgetClass` and every accepted state-branch Visibility from the saved assets rather than copying their planned values.
10. For a requirement-driven production build, normalize these actual reads into `Saved/CodexUIRequirements/<request-id>/unreal-widget-readback.json` using [unreal-widget-readback.schema.json](../../document-nextgame-umg/assets/unreal-widget-readback.schema.json). Run `python ../document-nextgame-umg/scripts/validate_unreal_widget_readback.py <readback.json> --requirement <requirement.json> --bundle <bundle.json>` from the build skill directory. Cover every built asset, actual generated-CDO `designSizeMode`, actual Widget, Bundle node mapping, variable flag, collection entry class, and accepted state branch. Point the Bundle's passed `widget-tree` and `key-properties` checks to this artifact through `artifactPath` without changing the Bundle top-level shape.
11. For a complete screen, preview or capture it at `2560 × 1440`; verify that the root and global layers fill the full canvas and that the main regions do not remain inside a smaller legacy source-size frame.

## Actual readback boundary

- Use the official Unreal MCP as the authoritative acquisition route and record `acquisition.method: official-unreal-mcp`. If it is unavailable or does not expose the exact required read, NxUEAgent may fill only that missing read; use `nxue-agent` with a nonempty `fallbackReason`, or `mixed` with a reason and JSONPath for every field-level fallback.
- Treat `UILayoutSpec`, build plans, Bundle mappings, and mutation inputs as expected state only. Never serialize them as actual Widget values or use them to fill a missing Unreal read.
- `DesignSizeMode` is editor-only data on the generated `UUserWidget` CDO, not a WidgetTree node and not a property of the WidgetBlueprint asset object. Read it from the CDO after save and serialize only `FillScreen|Desired` as `assets[].designSizeMode`.
- When official ObjectTools does not expose the protected CDO field, the permitted fallback is NxUE `manage-property` with object path `/Game/.../<Asset>.Default__<Asset>_C` and property path `DesignSizeMode`. Record the fallback reason in acquisition evidence; for mixed acquisition, identify the exact `$.assets[i].designSizeMode` field path. Save the WidgetBlueprint package and use a second `manage-property` get as post-save readback. `UWidgetBlueprint.ThumbnailSizeMode` is unrelated and must not be changed.
- Preserve the actual asset and Widget identities returned by Unreal, then relate them to Bundle assets and node mappings explicitly. A matching planned name is not proof that the Widget exists or is exposed as a variable.
- Do not pass `widget-tree` or `key-properties` verification until the normalized artifact is schema-valid and covers the affected asset.

## References and failures

- Never guess WidgetTree or Slot references.
- A Widget Blueprint asset reference and its generated class reference differ. `AddWidget` may require `<asset>.<asset>_C` for a user-widget class.
- A CanvasPanel child layout belongs to its returned CanvasPanelSlot, not to the child Widget.
- Create only at the destination validated for the selected UILayoutSpec mode.
- In production mode, require the destination to exactly match the formal `profile.targetAsset`.
- If creation succeeds but later calls fail, keep the UILayoutSpec and report the partial asset path.
- Do not delete or overwrite an existing asset without explicit authorization.
