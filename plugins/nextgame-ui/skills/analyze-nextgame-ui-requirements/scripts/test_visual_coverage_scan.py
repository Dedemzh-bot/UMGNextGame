#!/usr/bin/env python3
"""Tests for the reusable static visual coverage proposal scanner."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path

from PIL import Image, ImageDraw

from visual_coverage_scan import run_scan


class VisualCoverageScanTests(unittest.TestCase):
    def test_finds_unlabelled_accent_and_repeated_bands_without_semantic_hints(self) -> None:
        # Installed plugin caches are intentionally read-only. Keep generated
        # evidence in the operating-system test-temp area instead of beside the
        # test module or current working directory.
        root = Path(tempfile.gettempdir()) / "nextgame-ui-visual-coverage-tests" / uuid.uuid4().hex
        root.mkdir(parents=True)
        try:
            image_path = root / "input.png"
            requirement_path = root / "requirement.json"
            layout_path = root / "layout.json"
            output = root / "output"

            image = Image.new("RGB", (640, 360), (222, 228, 239))
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle((88, 34, 124, 41), radius=2, fill=(236, 151, 38))
            draw.rectangle((112, 105, 320, 135), fill=(67, 81, 205))
            for top in (190, 232, 274):
                draw.rectangle((330, top, 600, top + 28), fill=(204, 211, 226))
            image.save(image_path)

            requirement = {
                "analysisPolicy": {"staticVisualCoverageRequired": True},
                "uiModel": {
                    "regions": [
                        {"id": "reg.left", "bounds": [0.08, 0.05, 0.45, 0.4]},
                        {"id": "reg.rows", "bounds": [0.48, 0.48, 0.48, 0.42]},
                    ],
                    "elements": [],
                },
                "evidence": [],
            }
            requirement_path.write_text(json.dumps(requirement), encoding="utf-8")
            layout = {
                "version": "0.2",
                "asset": {"name": "test_screen"},
                "profile": {"assetKind": "screen"},
                "referenceSize": [640, 360],
                "nodes": [
                    {"id": "root", "role": "screen.root", "rect": [0, 0, 1, 1]},
                    {"id": "blue", "role": "visual.image", "rect": [0.175, 0.291667, 0.325, 0.083333], "properties": {"color": {"r": 0.2, "g": 0.3, "b": 0.8, "a": 1}}},
                ],
            }
            layout_path.write_text(json.dumps(layout), encoding="utf-8")

            report = run_scan(image_path, requirement_path, [layout_path], output, tile_size=128, tile_overlap=16)
            inventory = json.loads((output / "inventory-draft.json").read_text(encoding="utf-8"))
            candidates = inventory["candidates"]

            accent = [item for item in candidates if _iou(item["geometry"]["pixelBounds"], [88, 34, 37, 8]) >= 0.25]
            self.assertTrue(accent, "small chromatic accent should produce a candidate")
            self.assertTrue(any(item["disposition"]["status"] == "unresolved" for item in accent))

            blue = [item for item in candidates if _iou(item["geometry"]["pixelBounds"], [112, 105, 209, 31]) >= 0.35]
            self.assertTrue(blue, "declared blue band should produce a candidate")
            self.assertTrue(any(item["disposition"]["status"] == "mapped" for item in blue))

            repeated = [item for item in candidates if item["repetition"]["observedInstanceCount"] >= 2 and item["geometry"]["pixelBounds"][0] >= 300]
            self.assertTrue(repeated, "generic repeated-band detector should group repeated plates")
            self.assertGreater(report["summary"]["uncoveredHighOrMediumSalienceCount"], 0)
            for filename in (
                "source-raster.json",
                "visual-primitives.json",
                "inventory-draft.json",
                "report.json",
                "candidate-mask.png",
                "inventory-overlay.png",
                "uncovered-overlay.png",
                "candidate-contact-sheet.png",
                "fullscan-contact-sheet.png",
            ):
                self.assertTrue((output / filename).is_file(), filename)
        finally:
            shutil.rmtree(root, ignore_errors=True)


def _iou(left: list[int], right: list[int]) -> float:
    lx, ly, lw, lh = left
    rx, ry, rw, rh = right
    intersection = max(0, min(lx + lw, rx + rw) - max(lx, rx)) * max(0, min(ly + lh, ry + rh) - max(ly, ry))
    union = lw * lh + rw * rh - intersection
    return intersection / union if union else 0.0


if __name__ == "__main__":
    unittest.main()
