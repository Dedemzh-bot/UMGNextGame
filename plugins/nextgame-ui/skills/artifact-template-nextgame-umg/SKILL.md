---
name: artifact-template-nextgame-umg
description: Create a document using the NextGame UMG 界面说明 retained reference. Use when the user selects the NextGame UMG 界面说明 template or explicitly invokes $artifact-template-nextgame-umg for a programmer-facing UMG handoff document.
---

# NextGame UMG 界面说明

Create a document from the retained neutral reference without treating its placeholders as business data.

## Positioning and authority

- This is the canonical presentation template for NextGame production UMG programmer handoff documents aligned with `program-document-content.json` 0.4. It is not a requirement-analysis template, design-review template, prototype/legacy output template, or authorization to enter the document stage.
- The validated `UIProgramHandoff` and `program-document-content.json` are business-content authority. [The program-document template contract](../document-nextgame-umg/references/program-document-template.md) is structure and presentation authority. `assets/reference.docx` is a neutral visual reference only, and a generated system DOCX is always downstream of all three.
- If these layers disagree, preserve the validated business data, follow the contract, and adapt or regenerate the retained reference or output. Never copy a concrete system document back into the neutral template.
- A template-format change is complete only when this Skill, its UI metadata, retained reference, preview, generator, strict validators, regression tests, and page-by-page render review agree.

## Workflow

1. Read `artifact-template.json` and resolve its paths relative to this skill directory.
2. For production generation, use `../document-nextgame-umg/scripts/build_program_docx.py`; it fixes `assets/reference.docx` internally, revalidates the current accepted source chain, and exposes no arbitrary reference or output override. Use `$documents` for the mandatory render-and-inspect workflow, not as an unconstrained production author.
3. Treat the user's verified content sources as the only content authority. Treat the retained reference as presentation authority only. The deterministic builder consumes only the verified Handoff and `program-document-content.json` fields as document business data.
4. Replace the header slots from verified content and retain one concise `程序范围` callout; it describes only the programmer-facing handoff scope and must not restate the user request, AI process, or generation notes. Do not create a standalone target-asset section or target-asset table. Clone complete asset-detail blocks, their merged WidgetTree/program-purpose rows, and other data rows to match the source data; keep each asset path only inside its matching asset-detail block. Remove every unused conditional block and every bracketed placeholder. Never create a document-level `程序变量清单` section, and renumber all remaining sections continuously after omissions.
5. Render every asset's WidgetTree and program purposes as one native Word table with the exact headers `层级 / Widget`, `Class`, `Is Variable`, and `程序用途`, one actual node per data row. Set first-cell Word left indentation to `depth × widgetTreeTables.indentTwipsPerDepth` (currently `180 twips` per level), write the third cell exactly as lowercase `true` or `false`, and write the fourth cell from that asset row's verified `programPurpose` or as an empty string. An empty fourth cell never changes the actual third-cell value. Never use ASCII connectors, space-padded pseudo-trees, PNGs, screenshots, drawings, or other image substitutes. For a `reuse-only` asset with no owned nodes, use one row merged across all four columns containing exactly `无自有 WidgetTree 节点（继承结构不重复列出）`.
6. Title every asset-detail block as the exact Blueprint basename immediately followed by a concise Chinese functional name, for example `uw_fight_use道具使用图标`. Do not add parentheses or process/role labels such as `近期子控件` or `主目标`, and do not output a `根控件` field in the block. Keep the block order exact: title → `资产路径` → `Parent Class` → `功能说明` → merged WidgetTree/program-purpose table → `程序接入关系` → any owned collection, state, and support-dependency content. Populate `Parent Class` from the verified 0.4 content contract, and derive the concise functional explanation only from verified programmer-facing relationships.
7. Route dynamic collections, state models, and support-dependency notes by their owning or hosting asset. If that asset is present in the first `资产详细说明` module, append the content inside its asset-detail block after the WidgetTree and programmer-facing relationships; never create document-level collection, state, or `支持依赖定位` sections. Render embedded collection and state identifiers such as `collection.teammate.entries` and `state-model.teammate-life` as ordinary body text or an inline label, never with Heading, `Tool Asset`, or section-title styling. Do not repeat `所属资产` or the asset path inside embedded collection or state content because the surrounding asset block already provides that context.
8. Only when the owning or hosting asset is absent from the first module may a conditional `其他资产程序说明` module be created. Include only unmatched collection, state, or support-dependency content; group it by asset and print the asset name and complete path once at group level rather than per item. Omit the whole module when there are no unmatched items.
9. Preserve A4 page setup, blue heading system, light-blue table headers, scope box, section order, and footer treatment. Do not print `内容模式`, a restatement of the user's request, AI workflow notes, or other generation-process explanations in the opening. Do not emit a developer-facing `只读快照边界` section, Appendix A, or a semantic-trace appendix; snapshot/provenance boundaries and `requiredSemanticRelationshipStatements` remain machine-side validation data only. Asset-detail tables use fixed `52 / 34 / 20 / 40 mm` columns with matching `tblW`, `tblGrid`, and `tcW`, repeat and keep their header together, keep individual rows together, and do not emit any `w:trHeight`. Number every global section after the first module and optional `其他资产程序说明` module continuously according to the sections actually emitted.
10. Render the completed DOCX, visually inspect every page, and correct clipping, overlap, broken tables, or blank pages before returning it.

The retained reference and every production DOCX use canonical OPC packaging: sorted parts, fixed `2000-01-01` ZIP timestamps, fixed platform metadata, no `customXml`, no external relationships, and `ZIP_STORED` entries so output bytes do not depend on a zlib implementation. Repeated generation from identical validated inputs must be byte-identical.

## Safety

- Never copy placeholder values or prior system-specific content into a new document.
- Never invent facts to fill a slot.
- Never flatten program variables into a document-level section or map a purpose across asset boundaries.
- Do not constrain asset-detail blocks, variables, collections, states, support dependencies, deviations, gaps, or tree depth to the counts shown by the reference.
- For the NextGame production workflow, do not create a program handoff document until the verified UMG result has been shown to the user and a valid post-build `ui-build-acceptance.json` records the user's later explicit acceptance.
- The validated program handoff and generated document contract remain authoritative over this presentation template; the template cannot substitute for or bypass the post-build acceptance gate.
