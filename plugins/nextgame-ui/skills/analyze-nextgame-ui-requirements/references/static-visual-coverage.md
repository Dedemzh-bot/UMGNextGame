# Static Visual Coverage

Use this evidence pass to prevent visible static decoration from disappearing between screenshot discovery, canonical requirements, and UMG construction. It supplements semantic analysis; it does not replace visual judgment or authorize Unreal mutation.

## Command

```text
python scripts/visual_coverage_scan.py \
  --image <reference.png> \
  --requirement <ui-requirement.json> \
  --layouts <layouts-directory-or-json-files> \
  --independent-review <optional-human-first-inventory.json> \
  --output <new-or-empty-evidence-directory>
```

The implementation requires Pillow and NumPy. The scan is deterministic for the same decoded raster, Requirement, Layouts, and tool version. It contains no system-specific coordinates, named colors, expected candidate count, or business labels.

## Discovery model

The scanner unions four independent proposal streams:

1. saturated hue connected components, which retain small accents and colored strips;
2. local luminance-contrast connected components, which retain neutral glyphs and controls;
3. downsampled quantized-color components, which retain broad, approximately uniform shapes;
4. horizontal-band proposals from both Requirement regions and a blind full-screen grid, which retain low-contrast plates and repeated rows.

Similarity grouping boosts repeated instances but never turns repetition alone into a semantic collection decision. Candidate matching uses only measured overlap with declared Requirement or Layout geometry. For collection hosts, child entry Layout visuals are projected using the designer preview count and vertical entry spacing. A collection root or structural Panel does not automatically cover all pixels inside it.

Detector primitives are evidence, not Widget instructions. Several connected components, dots, short lines, corners, or color islands may be parts of one complete graphic. Merge them into one measured canonical image when they share semantic identity, state, runtime control, draw layer, adaptation, and resource responsibility and one texture or Brush can express the complete non-text graphic. Split them only for evidenced independent runtime control, state variation, adaptation, resource reuse, material/mask behavior, progress fill, or an accepted exception. Do not split merely because the detector emitted multiple candidates.

## Evidence packet

Every run writes:

- `source-raster.json`: source digest, decoded size/mode, detector version, and thresholds;
- `visual-primitives.json`: each candidate's detectors, source/pixel/normalized geometry, row-run mask, color and contrast measurements, repetition group, salience, and draft disposition;
- `inventory-draft.json`: the same candidates bound to the exact Requirement/Layout hashes, declared visual projections, and evidence-backed exclusions;
- `report.json`: draft gate metrics and every unresolved medium/high-salience candidate;
- `report.json` also groups adjacent/overlapping unresolved primitives into traceable review clusters; clusters reduce review workload but never resolve or discard their member IDs;
- `candidate-mask.png` and `coverage-mask.png`: union and disposition masks;
- `inventory-overlay.png` and `uncovered-overlay.png`: numbered audit overlays;
- `crops/` and `candidate-contact-sheet.png`: review crops for all medium/high candidates;
- `review-cluster-overlay.png`, `review-cluster-crops/`, and `review-cluster-contact-sheet.png`: region-scale review subjects with complete primitive membership;
- `fullscan-contact-sheet.png` plus JSON tile coordinates: overlapping full-raster zoom coverage, independent of detected candidates.
- `review-reconciliation.json`, when `--independent-review` is supplied: geometry-only candidate/cluster recall against that human-first inventory, with both inputs hash-bound.

Do not edit the authoritative Requirement from scanner output automatically. A reviewer must classify every open candidate as a mapped complete graphic, an evidenced independent layer, a merge into a more accurate whole-graphic candidate, an evidence-backed exclusion, or documented noise. A merge keeps every primitive ID traceable while allowing those local findings to alias to one canonical complete image with a measured outer bound. Genuinely independent static elements still receive stable Requirement identities and geometry even when they are not runtime variables.

## Required review and gates

The draft reports these metrics:

- `dispositionCompleteness`: terminal dispositions divided by all retained candidates;
- `nonExcludedMappingRecallMediumHigh`: mapped medium/high candidates divided by all non-excluded medium/high candidates;
- `weightedUncoveredRatioMediumHigh`: uncovered area weighted by local contrast and observed repetition;
- `uncoveredHighOrMediumSalienceCount`.

An accepted static-coverage review requires:

- disposition completeness `1.0`;
- non-excluded mapping recall `1.0`, including per-region review rather than only a global aggregate;
- zero unresolved medium/high candidates;
- zero exclusions without an accepted user/rule claim;
- independent inspection of the numbered overlay, every medium/high crop, and the overlapping full-screen contact sheet.
- an `imageComposition` decision for every in-scope image: exactly one `complete` image per group, an evidence-backed `splitReason` for every retained additional layer, and the exact accepted `ownerIntentId` whenever adaptation is inherited. Treat a stretchable Button Backplate and a fixed glyph as separate complete groups when their duties differ; merge the glyph's scanner fragments into the glyph rather than demoting the Backplate or fragments into arbitrary layers.

An owning-region proxy for an out-of-scope element is always review-required because it can be broader than the excluded pixels. Low-salience detector output may be rejected as illustration, text fragmentation, shadow, antialiasing, or background variation, but the reason remains in the inventory.

## Downstream render verification

After UMG construction, capture a clean widget-only render at the accepted design resolution. Do not use an Editor screenshot with chrome as the pixel-comparison artifact. Register and compare each accepted static candidate independently:

- broad fill or plate recall should be at least `0.90`;
- thin-line edge recall should be at least `0.85`;
- no per-candidate failure may be hidden by a global similarity average.

Color and art may remain approximate when the approved scope permits placeholders. The render must nevertheless contain the accepted structure, visual layer, and repeated instances.
