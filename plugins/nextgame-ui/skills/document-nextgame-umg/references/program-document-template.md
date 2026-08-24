# NextGame UMG program-document template contract

Use this contract only after `UIProgramHandoff` and `program-document-content.json` have passed their validators. The packaged DOCX is the presentation authority; the two verified JSON artifacts are the only business-content authority. If presentation and data disagree, preserve the verified data and adapt the layout.

## Presentation system

- Use A4 portrait pages: `210 × 297 mm`.
- Use `1.25 in` left/right margins and `1.0 in` top/bottom margins.
- Use `0.55 in` header/footer distances. The running header is `UGame <SystemFolder> · 程序接入说明`; the footer is `<SystemFolder> · <PAGE>`.
- Preserve the approved style system: black `Heading 1` at 22 pt for the document title, black `Heading 2` at 16 pt for asset-detail headings, blue `Tool Section` at 17 pt, blue `Tool Asset` at 13 pt, 10.5 pt body text, 8 pt centered `Tree Caption`, and 7.2 pt monospaced `Program Trace`.
- Use `#2E75B6` for section headings, `#E7EFF8` for table headers, `#BCCBDA` for table borders, and `#F1F4F8` for the scope callout. Table headers are 9 pt, body cells are approximately 8.8 pt, header rows repeat after page breaks, and individual data rows do not split where practical.
- Do not assume a fixed page count. Add pages only as required by actual content and remove accidental blank pages.
- Preserve this section order:

  1. target assets
  2. asset details
  3. program variables
  4. dynamic collections
  5. state controls
  6. accepted build deviations
  7. handoff gaps
  8. semantic trace appendix

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

- Clone the target-asset row once per actual target asset. The number of assets is unbounded.
- Clone the complete asset-detail block once per actual asset. Put the system screen first, followed by child widgets and entry assets in dependency/build order.
- Within an asset block, use only the location-only WidgetTree diagram projected by the NextGame-specific stage from validated Readback fields `widgetName`, `classPath`, `parentWidgetName`, and `isVariable`. `$documents` receives the generated diagram, not raw Readback. Do not reuse the skeleton tree or an earlier document's tree.
- Clone variable, collection, state, deviation, gap, and trace rows to the actual verified counts. Do not truncate to the reference count.
- Omit an entire conditional section when its verified source collection is empty. Do not leave an empty heading, placeholder row, or “none” filler merely to preserve page count.
- Keep target-assets and asset-details sections even when there is a single asset.

## Asset-detail layout and pagination

Each asset-detail block contains, in order: asset identity, functional summary, actual WidgetTree, then programmer-facing relationships. Keep the asset heading with its identity table. Allow long trees to cross pages, but never crop a tree line, overlap the next table, or shrink text below the template's readable size. Prefer a clean page break before a tall tree or before the next asset block when the remaining space is insufficient.

Repeat table headers after page breaks. Keep individual data rows together where practical. Long relationship text may wrap within its cell; it must not overflow the page or be converted into an image.

## Trace appendix

Copy every item from `program-document-content.json.requiredSemanticRelationshipStatements` into the semantic trace appendix as one complete, separate row or paragraph. Preserve each statement verbatim. Do not paraphrase, concatenate, split, reorder for aesthetics, or substitute a visually similar sentence. The strict validator remains responsible for proving exact coverage.

## Content isolation

- Never copy system names, asset paths, Widget names, handoff identifiers, trace statements, tree nodes, screenshots, or media from the retained skeleton or a prior output.
- Never use raw Requirement, Bundle, UILayoutSpec, source prose, or reference-image labels as replacement business data during this stage.
- The retained DOCX contains presentation placeholders only. Remove all placeholders before verification.
- The template does not weaken the three-authoritative-source plus post-build-acceptance contract, handoff/schema validators, policy exclusions, exact-trace requirement, canonical rendering, or page-by-page visual review. The acceptance authorizes document generation but is not business content to reproduce in the DOCX.
