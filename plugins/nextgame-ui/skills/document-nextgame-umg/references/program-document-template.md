# NextGame UMG program-document template contract

Use this contract only after `UIProgramHandoff` and `program-document-content.json` have passed their validators. `build_program_docx.py` must also revalidate their current Requirement, Bundle, Unreal Readback, and post-build acceptance bindings before it writes. The packaged DOCX is the presentation authority; the two verified JSON artifacts are the only document business-content authority. If presentation and data disagree, preserve the verified data and adapt the layout.

## Normative status and authority

- This is the canonical structure and presentation contract for NextGame production UMG programmer handoff documents aligned with `program-document-content.json` 0.4. It does not apply to requirement analysis, design review, prototype/legacy output, or pre-acceptance documentation.
- Authority order is: validated `UIProgramHandoff` and `program-document-content.json` business data; this contract; the neutral `artifact-template-nextgame-umg/assets/reference.docx`; then any generated system-specific DOCX.
- A downstream artifact may specialize verified content but may not redefine upstream semantics. If layers conflict, repair or regenerate the lower-authority layer.
- A template-format change is not complete until the template Skill, UI metadata, neutral reference, preview, generator, strict validator, regression tests, and a fresh page-by-page render review all agree.

## Presentation system

- Use A4 portrait pages: `210 × 297 mm`.
- Use `1.25 in` left/right margins and `1.0 in` top/bottom margins.
- Use `0.55 in` header/footer distances. The running header is `UGame <SystemFolder> · 程序接入说明`; the footer is `<SystemFolder> · <PAGE>`.
- Preserve the approved style system: black `Heading 1` at 22 pt for the document title, black `Heading 2` at 16 pt for asset-detail headings, blue `Tool Section` at 17 pt, blue `Tool Asset` at 13 pt, 10.5 pt body text, and 8 pt centered `Tree Caption`. Keep one pale-blue `程序范围` callout after the title metadata and before the first numbered module; its content is programmer-facing scope, not a user-request restatement or generation note.
- Use `#2E75B6` for section headings, `#E7EFF8` for table headers, `#BCCBDA` for table borders, and `#F1F4F8` for the scope callout. Table headers are 9 pt, body cells are approximately 8.8 pt, header rows repeat after page breaks, and individual data rows do not split where practical.
- Do not assume a fixed page count. Add pages only as required by actual content and remove accidental blank pages.
- Do not print `内容模式`, a restatement of the user's request, AI workflow notes, format-tuning commentary, or other generation-process explanations in the opening.
- Do not create a standalone target-assets section or target-asset table. Preserve this document-level content order and number the sections continuously after omitting any empty conditional section: asset details; conditional other-asset program notes; accepted build deviations; handoff gaps. Dynamic collections, state models, and support-dependency notes are routed into asset blocks as described below and never form standalone document-level sections. Program variables remain inside each asset-detail table rather than in a document-level section. Do not add a developer-facing `只读快照边界` section or a semantic-trace appendix.

## Header slots

Replace every bracketed marker in the retained reference. Populate the title metadata from the verified handoff contract:

- `[YYYYMMDD]` (title alias of the compact output date)
- `[System]` (title alias of `SystemFolder`)
- `[SystemFolder]`
- `[DeliveryDate]`
- `[TargetAssetCount]`
- `[HandoffId]`
- `[ScopeStatement]`

Do not emit an unchanged marker in the finished document.

## Dynamic blocks

- Clone the complete asset-detail block once per actual asset. Put the system screen first, followed by child widgets and entry assets in dependency/build order.
- Keep each complete asset path only inside its matching asset-detail block. Title that block with the exact Blueprint basename immediately followed by a concise Chinese functional name, for example `uw_fight_use道具使用图标`. Do not add parentheses or labels such as `近期子控件` or `主目标`, and do not emit a `根控件` field.
- Route every dynamic collection, state model, and support-dependency note by its owning or hosting asset. When that asset appears in the first asset-details module, append the content inside the matching asset-detail block after its WidgetTree and programmer-facing relationships. Do not create document-level `动态集合`, `状态模型`, `状态控制`, or `支持依赖定位` sections for matched content.
- Render embedded collection and state identifiers such as `collection.teammate.entries` and `state-model.teammate-life` as ordinary body text or an inline label, never with Heading, `Tool Asset`, or section-title styling. Do not repeat `所属资产` or the asset path inside embedded collection or state content because its surrounding asset-detail block already supplies that context.
- Create a conditional `其他资产程序说明` module only for collection, state, or support-dependency content whose owning or hosting asset does not appear in the first asset-details module. Include no matched content in this module; group unmatched items by asset and print the asset identity and complete path once at group level, not inside every item. Omit the entire module when there are no unmatched items.
- Within an asset block, read `Parent Class` from that asset's verified `widgetTreeTables.assets[].parentClassPath` and build the merged WidgetTree/program-purpose table only from its ordered structured rows in validated `program-document-content.json` 0.4. `$documents` receives those values, not raw Readback or diagram files. Do not reuse the skeleton tree or an earlier document's tree.
- Render every asset detail as a native Word table whose exact headers are `层级 / Widget`, `Class`, `Is Variable`, and `程序用途`. Emit one data row per actual node. Set first-cell Word left indentation to `depth × widgetTreeTables.indentTwipsPerDepth` (currently `180 twips` per level), write the third cell exactly as lowercase `true` or `false`, and copy the fourth cell from the row's verified `programPurpose` or write an empty string. The fourth column is matched within the same asset only; an empty purpose does not change the readback-derived third column. Do not use ASCII connectors, space-padded pseudo-trees, PNGs, screenshots, drawings, or other image substitutes.
- Use fixed table layout with column widths `52 mm`, `34 mm`, `20 mm`, and `40 mm`; keep `tblW`, `tblGrid`, and every `tcW` consistent. Use approximately `8.8 pt` body text, center the third column, left-align the other columns, repeat and keep the header row together, keep each individual data row together, and do not emit any `w:trHeight`.
- For a `reuse-only` asset with no owned WidgetTree nodes, replace ordinary node rows with one row merged across all four columns containing exactly `无自有 WidgetTree 节点（继承结构不重复列出）`.
- Clone merged asset-detail rows, embedded collection/state/support-dependency rows, unmatched-asset groups, deviation rows, and gap rows to the actual verified counts. Do not truncate to the reference count, and never generate separate program-variable, dynamic-collection, state-model, or support-dependency sections.
- Omit an entire conditional section when its verified source collection is empty. Do not leave an empty heading, placeholder row, or “none” filler merely to preserve page count.
- Keep the asset-details section even when there is a single asset.

## Asset-detail layout and pagination

Each asset-detail block contains, in this exact order: the exact-basename-plus-Chinese title; `资产路径`; `Parent Class`; `功能说明`; the actual merged WidgetTree/program-purpose table; `程序接入关系`; then any dynamic collections, state models, and support-dependency notes owned or hosted by that asset. Do not emit any `根控件` field. Program variables appear only on their matching node rows in that table; there is no separate variable list or asset-level duplicate table. Keep the asset heading with the path and identity paragraphs that follow it, and keep the table caption with its table. Allow long tables to cross pages naturally, but never crop a row, overlap the next table, or shrink text below the template's readable size. Prefer a clean page break before a tall table or before the next asset block when the remaining space is insufficient; do not keep the whole table together or add fixed row heights.

Repeat table headers after page breaks. Keep individual data rows together where practical. Long relationship text may wrap within its cell; it must not overflow the page or be converted into an image.

## Machine-only audit content

Keep `program-document-content.json.requiredSemanticRelationshipStatements`, Readback snapshot timing, source bindings, acquisition details, and provenance boundaries in the machine contracts and verification artifacts. They remain available for deterministic audit and validation but are not programmer-facing DOCX content. Do not create Appendix A, a semantic-trace appendix, or a `只读快照边界` section from them.

## Content isolation

- Never copy system names, asset paths, Widget names, handoff identifiers, machine-only semantic statements, tree nodes, screenshots, or media from the retained skeleton or a prior output.
- Never use raw Requirement, Bundle, UILayoutSpec, source prose, or reference-image labels as replacement business data during this stage.
- The retained DOCX contains presentation placeholders only. Remove all placeholders before verification.
- The template does not weaken the three-authoritative-source plus post-build-acceptance contract, handoff/schema validators, policy exclusions, machine-side semantic auditing, canonical rendering, or page-by-page visual review. The acceptance authorizes document generation but is not business content to reproduce in the DOCX.
- Production DOCX authoring is deterministic: the builder fixes the neutral reference and output contract, uses stable asset-order projections and fixed-safe Chinese suffixes, atomically replaces only changed bytes, and canonicalizes OPC part order, timestamps, platform attributes, and storage method. Identical validated inputs must yield identical DOCX bytes; PDF bytes are not required to match, so page rendering and visual review remain mandatory.
