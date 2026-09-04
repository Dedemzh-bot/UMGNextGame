#!/usr/bin/env python3
"""Tests for the reusable static visual coverage proposal scanner."""

from __future__ import annotations

import copy
import gzip
import json
import random
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from _contract_common import load_json, validate_schema_instance
from visual_coverage_scan import (
    Candidate,
    add_band_candidates,
    canonical_json_bytes,
    review_cluster_candidates_linked,
    review_cluster_pair_indices,
    run_scan,
    sha256_bytes,
    uncovered_review_clusters,
)


class VisualCoverageScanTests(unittest.TestCase):
    def test_horizontal_band_column_bounds_use_signed_transitions(self) -> None:
        rgb = np.full((180, 400, 3), 220, dtype=np.uint8)
        rgb[70:105, 90:310] = (70, 90, 190)
        candidates = add_band_candidates(
            rgb,
            [{"id": "window", "bbox": (0, 0, 400, 180), "source": "test"}],
        )
        matching = [candidate for candidate in candidates if _iou(list(candidate.bbox), [90, 70, 220, 35]) >= 0.75]
        self.assertTrue(matching, "the broad band should be detected at its actual changed-column bounds")
        self.assertTrue(all(candidate.bbox[2] < 400 for candidate in matching))

    def test_odd_raster_quantized_candidates_remain_in_bounds_and_cache_warms(self) -> None:
        root = Path(tempfile.gettempdir()) / "nextgame-ui-visual-coverage-tests" / uuid.uuid4().hex
        root.mkdir(parents=True)
        try:
            image_path = root / "odd.png"
            image = Image.new("RGB", (641, 101), (220, 224, 232))
            draw = ImageDraw.Draw(image)
            draw.rectangle((600, 80, 640, 100), fill=(44, 76, 180))
            image.save(image_path)
            cache = root / "cache"
            cold = root / "cold"
            warm = root / "warm"
            run_scan(image_path, None, [], cold, cache_dir=cache)
            inventory = json.loads((cold / "inventory-draft.json").read_text(encoding="utf-8"))
            self.assertTrue(inventory["candidates"])
            for candidate in inventory["candidates"]:
                left, top, width, height = candidate["geometry"]["pixelBounds"]
                self.assertGreaterEqual(left, 0)
                self.assertGreaterEqual(top, 0)
                self.assertLessEqual(left + width, 641)
                self.assertLessEqual(top + height, 101)
            run_scan(image_path, None, [], warm, cache_dir=cache)
            telemetry = json.loads((warm / "cache-telemetry.json").read_text(encoding="utf-8"))
            self.assertEqual("hit", telemetry["cache"]["status"])
            self.assertEqual(_packet_hashes(cold), _packet_hashes(warm))
        finally:
            shutil.rmtree(root, ignore_errors=True)

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
            cache = root / "cache"

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

            report = run_scan(
                image_path,
                requirement_path,
                [layout_path],
                output,
                tile_size=128,
                tile_overlap=16,
                cache_dir=cache,
            )
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
                "cache-telemetry.json",
            ):
                self.assertTrue((output / filename).is_file(), filename)

            cold_telemetry = json.loads((output / "cache-telemetry.json").read_text(encoding="utf-8"))
            telemetry_schema = load_json(Path(__file__).resolve().parent.parent / "assets" / "visual-coverage-cache-telemetry.schema.json")
            self.assertEqual([], validate_schema_instance(cold_telemetry, telemetry_schema))
            self.assertEqual("miss", cold_telemetry["cache"]["status"])
            self.assertFalse(cold_telemetry["reuse"]["detectorStageReused"])
            cache_entry = cache / cold_telemetry["cache"]["entryFile"]
            self.assertTrue(cache_entry.is_file())

            warm_output = root / "warm-output"
            warm_report = run_scan(
                image_path,
                requirement_path,
                [layout_path],
                warm_output,
                tile_size=128,
                tile_overlap=16,
                cache_dir=cache,
            )
            self.assertEqual(report, warm_report)
            warm_telemetry = json.loads((warm_output / "cache-telemetry.json").read_text(encoding="utf-8"))
            self.assertEqual("hit", warm_telemetry["cache"]["status"])
            self.assertTrue(warm_telemetry["reuse"]["detectorStageReused"])
            self.assertEqual(_packet_hashes(output), _packet_hashes(warm_output))

            changed_layout = copy.deepcopy(layout)
            changed_layout["nodes"][1]["rect"] = [0.01, 0.01, 0.02, 0.02]
            layout_path.write_text(json.dumps(changed_layout), encoding="utf-8")
            remapped_output = root / "remapped-output"
            remapped_report = run_scan(
                image_path,
                requirement_path,
                [layout_path],
                remapped_output,
                tile_size=128,
                tile_overlap=16,
                cache_dir=cache,
            )
            remapped_telemetry = json.loads((remapped_output / "cache-telemetry.json").read_text(encoding="utf-8"))
            self.assertEqual("hit", remapped_telemetry["cache"]["status"])
            self.assertTrue(remapped_telemetry["reuse"]["detectorStageReused"])
            self.assertNotEqual(report, remapped_report)
            remapped_inventory = json.loads((remapped_output / "inventory-draft.json").read_text(encoding="utf-8"))
            remapped_blue = [
                item
                for item in remapped_inventory["candidates"]
                if _iou(item["geometry"]["pixelBounds"], [112, 105, 209, 31]) >= 0.35
            ]
            self.assertTrue(remapped_blue)
            self.assertTrue(any(item["disposition"]["status"] == "unresolved" for item in remapped_blue))
            layout_path.write_text(json.dumps(layout), encoding="utf-8")

            cache_manifest = json.loads(gzip.decompress(cache_entry.read_bytes()).decode("utf-8"))
            cache_manifest["candidates"][0]["bbox"][0] = -1
            cache_manifest["candidatePayloadSha256"] = sha256_bytes(
                canonical_json_bytes(cache_manifest["candidates"])
            )
            cache_entry.write_bytes(gzip.compress(canonical_json_bytes(cache_manifest), compresslevel=9, mtime=0))
            recovered_output = root / "recovered-output"
            recovered_report = run_scan(
                image_path,
                requirement_path,
                [layout_path],
                recovered_output,
                tile_size=128,
                tile_overlap=16,
                cache_dir=cache,
            )
            self.assertEqual(report, recovered_report)
            recovered_telemetry = json.loads((recovered_output / "cache-telemetry.json").read_text(encoding="utf-8"))
            self.assertEqual("invalid-recomputed", recovered_telemetry["cache"]["status"])
            self.assertFalse(recovered_telemetry["reuse"]["detectorStageReused"])
            self.assertEqual(_packet_hashes(output), _packet_hashes(recovered_output))

            malformed_payloads = (
                (b"\x1f\x8b\x08\x00", "truncated-gzip"),
                (gzip.compress(b"\xff\xfe", compresslevel=9, mtime=0), "invalid-utf8"),
            )
            for payload, name in malformed_payloads:
                cache_entry.write_bytes(payload)
                malformed_output = root / name
                malformed_report = run_scan(
                    image_path,
                    requirement_path,
                    [layout_path],
                    malformed_output,
                    tile_size=128,
                    tile_overlap=16,
                    cache_dir=cache,
                )
                self.assertEqual(report, malformed_report)
                malformed_telemetry = json.loads(
                    (malformed_output / "cache-telemetry.json").read_text(encoding="utf-8")
                )
                self.assertEqual("invalid-recomputed", malformed_telemetry["cache"]["status"])
                self.assertFalse(malformed_telemetry["reuse"]["detectorStageReused"])
                self.assertEqual(_packet_hashes(output), _packet_hashes(malformed_output))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_review_cluster_sweep_is_lossless_against_exhaustive_pairs(self) -> None:
        generator = random.Random(918273)
        for _ in range(20):
            candidates: list[Candidate] = []
            for index in range(90):
                bbox = (
                    generator.randrange(0, 900),
                    generator.randrange(0, 500),
                    generator.randrange(4, 120),
                    generator.randrange(4, 90),
                )
                candidate = Candidate(
                    detector="local-contrast",
                    bbox=bbox,
                    runs=[],
                    pixel_area=bbox[2] * bbox[3],
                )
                candidate.candidate_id = f"vc.{index + 1:04d}"
                candidate.region_id = generator.choice((None, "reg.left", "reg.right"))
                candidate.score = generator.uniform(0.46, 0.99)
                candidate.disposition = {"status": "unresolved"}
                candidates.append(candidate)

            proposed_pairs = set(review_cluster_pair_indices(candidates))
            exhaustive_linked = {
                (left_index, right_index)
                for left_index, left in enumerate(candidates)
                for right_index, right in enumerate(candidates[left_index + 1 :], left_index + 1)
                if left.region_id == right.region_id and review_cluster_candidates_linked(left, right)
            }
            self.assertTrue(exhaustive_linked.issubset(proposed_pairs))

            diagnostics: dict[str, int] = {}
            records = uncovered_review_clusters(candidates, 1000, 600, diagnostics=diagnostics)
            optimized_groups = {frozenset(record["memberIds"]) for record in records}
            self.assertEqual(_exhaustive_cluster_groups(candidates), optimized_groups)
            self.assertLessEqual(diagnostics["pairComparisons"], diagnostics["exhaustivePairCount"])

    def test_review_cluster_sweep_preserves_gap_boundary_and_reduces_sparse_pairs(self) -> None:
        def candidate(identifier: str, bbox: tuple[int, int, int, int]) -> Candidate:
            value = Candidate("local-contrast", bbox, [], bbox[2] * bbox[3])
            value.candidate_id = identifier
            value.score = 0.8
            value.disposition = {"status": "unresolved"}
            return value

        gap_ten = [candidate("vc.0001", (0, 0, 20, 20)), candidate("vc.0002", (30, 0, 20, 20))]
        gap_eleven = [candidate("vc.0001", (0, 0, 20, 20)), candidate("vc.0002", (31, 0, 20, 20))]
        self.assertTrue(review_cluster_candidates_linked(*gap_ten))
        self.assertIn((0, 1), set(review_cluster_pair_indices(gap_ten)))
        self.assertFalse(review_cluster_candidates_linked(*gap_eleven))
        self.assertNotIn((0, 1), set(review_cluster_pair_indices(gap_eleven)))

        sparse = [candidate(f"vc.{index + 1:04d}", (index * 100, 0, 20, 20)) for index in range(100)]
        diagnostics: dict[str, int] = {}
        uncovered_review_clusters(sparse, 10000, 100, diagnostics=diagnostics)
        self.assertEqual(0, diagnostics["pairComparisons"])
        self.assertEqual(4950, diagnostics["exhaustivePairCount"])


def _iou(left: list[int], right: list[int]) -> float:
    lx, ly, lw, lh = left
    rx, ry, rw, rh = right
    intersection = max(0, min(lx + lw, rx + rw) - max(lx, rx)) * max(0, min(ly + lh, ry + rh) - max(ly, ry))
    union = lw * lh + rw * rh - intersection
    return intersection / union if union else 0.0


def _packet_hashes(root: Path) -> dict[str, str]:
    import hashlib

    result: dict[str, str] = {}
    for path in sorted(value for value in root.rglob("*") if value.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative == "cache-telemetry.json":
            continue
        result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _exhaustive_cluster_groups(candidates: list[Candidate]) -> set[frozenset[str]]:
    parent = list(range(len(candidates)))

    def find(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for left_index, left in enumerate(candidates):
        for right_index, right in enumerate(candidates[left_index + 1 :], left_index + 1):
            if left.region_id == right.region_id and review_cluster_candidates_linked(left, right):
                union(left_index, right_index)

    groups: dict[int, set[str]] = {}
    for index, candidate in enumerate(candidates):
        groups.setdefault(find(index), set()).add(candidate.candidate_id)
    return {frozenset(group) for group in groups.values()}


if __name__ == "__main__":
    unittest.main()
