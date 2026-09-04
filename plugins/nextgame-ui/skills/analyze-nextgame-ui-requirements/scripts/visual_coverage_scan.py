#!/usr/bin/env python3
"""Build a reproducible static-visual coverage evidence packet from a UI raster.

The scanner is deliberately proposal-oriented: it finds visual primitives without
business labels, compares them with declared Requirement/Layout geometry, and
leaves unmatched medium/high-salience candidates open for independent review.
It never edits Requirement, Layout, Bundle, or Unreal assets.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import math
import os
import platform
import sys
import tempfile
import zlib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

try:
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont, ImageOps
except ImportError as error:  # pragma: no cover - exercised by deployment environment
    raise SystemExit(f"visual_coverage_scan.py requires numpy and Pillow: {error}")


TOOL_VERSION = "0.2.0"
DETECTION_CACHE_FORMAT = "visual-detection-cache-0.1"
DETECTION_CACHE_KEY_FORMAT = "visual-detection-cache-key-0.1"
MEDIUM_SCORE = 0.46
HIGH_SCORE = 0.70
TERMINAL_DISPOSITIONS = {"mapped", "merged", "excluded", "rejected-noise"}


@dataclass
class Candidate:
    detector: str
    bbox: tuple[int, int, int, int]
    runs: list[tuple[int, int, int]]
    pixel_area: int
    source_window: str | None = None
    detectors: set[str] = field(default_factory=set)
    mean_rgb: tuple[float, float, float] = (0.0, 0.0, 0.0)
    saturation: float = 0.0
    local_contrast: float = 0.0
    fill_ratio: float = 0.0
    edge_density: float = 0.0
    provisional_score: float = 0.0
    score: float = 0.0
    repeat_group: str | None = None
    repeat_count: int = 1
    candidate_id: str = ""
    region_id: str | None = None
    disposition: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.detectors.add(self.detector)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=False)
        handle.write("\n")


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def decoded_raster_sha256(rgb: "np.ndarray") -> str:
    digest = hashlib.sha256()
    digest.update(b"RGB\0")
    digest.update(str(tuple(int(value) for value in rgb.shape)).encode("ascii"))
    digest.update(b"\0")
    digest.update(rgb.tobytes(order="C"))
    return digest.hexdigest()


def candidate_cache_record(candidate: Candidate) -> dict[str, Any]:
    return {
        "detector": candidate.detector,
        "bbox": list(candidate.bbox),
        "runs": [list(run) for run in candidate.runs],
        "pixelArea": candidate.pixel_area,
        "sourceWindow": candidate.source_window,
        "detectors": sorted(candidate.detectors),
        "meanRgb": list(candidate.mean_rgb),
        "saturation": candidate.saturation,
        "localContrast": candidate.local_contrast,
        "fillRatio": candidate.fill_ratio,
        "edgeDensity": candidate.edge_density,
        "provisionalScore": candidate.provisional_score,
        "score": candidate.score,
        "repeatGroup": candidate.repeat_group,
        "repeatCount": candidate.repeat_count,
        "candidateId": candidate.candidate_id,
        "regionId": candidate.region_id,
    }


def candidate_from_cache_record(record: dict[str, Any], *, width: int, height: int) -> Candidate:
    required = {
        "detector",
        "bbox",
        "runs",
        "pixelArea",
        "sourceWindow",
        "detectors",
        "meanRgb",
        "saturation",
        "localContrast",
        "fillRatio",
        "edgeDensity",
        "provisionalScore",
        "score",
        "repeatGroup",
        "repeatCount",
        "candidateId",
        "regionId",
    }
    if set(record) != required:
        raise ValueError("Cached candidate fields do not match the closed cache format.")
    bbox = record["bbox"]
    runs = record["runs"]
    mean_rgb = record["meanRgb"]
    detectors = record["detectors"]
    if not (
        isinstance(bbox, list)
        and len(bbox) == 4
        and all(isinstance(value, int) and not isinstance(value, bool) for value in bbox)
    ):
        raise ValueError("Cached candidate bbox is invalid.")
    x, y, box_width, box_height = bbox
    if x < 0 or y < 0 or box_width <= 0 or box_height <= 0 or x + box_width > width or y + box_height > height:
        raise ValueError("Cached candidate bbox falls outside the decoded raster.")
    if not (
        isinstance(runs, list)
        and runs
        and all(
            isinstance(run, list)
            and len(run) == 3
            and all(isinstance(value, int) and not isinstance(value, bool) for value in run)
            for run in runs
        )
    ):
        raise ValueError("Cached candidate runs are invalid.")
    for row, start, length in runs:
        if (
            row < y
            or row >= y + box_height
            or start < x
            or length <= 0
            or start + length > x + box_width
            or row < 0
            or row >= height
            or start < 0
            or start + length > width
        ):
            raise ValueError("Cached candidate run falls outside its bbox or decoded raster.")
    if not (isinstance(mean_rgb, list) and len(mean_rgb) == 3 and all(isinstance(value, (int, float)) for value in mean_rgb)):
        raise ValueError("Cached candidate meanRgb is invalid.")
    if not (
        isinstance(detectors, list)
        and detectors
        and detectors == sorted(set(detectors))
        and all(isinstance(value, str) and value for value in detectors)
    ):
        raise ValueError("Cached candidate detectors are invalid.")
    detector = record["detector"]
    candidate_id = record["candidateId"]
    if not isinstance(detector, str) or not detector or not isinstance(candidate_id, str) or not candidate_id:
        raise ValueError("Cached candidate identity is invalid.")
    if detector not in {"chromatic-component", "local-contrast", "quantized-color", "horizontal-band"} or detector not in detectors:
        raise ValueError("Cached candidate detector membership is invalid.")
    pixel_area = record["pixelArea"]
    if (
        not isinstance(pixel_area, int)
        or isinstance(pixel_area, bool)
        or pixel_area <= 0
        or pixel_area > box_width * box_height
        or sum(run[2] for run in runs) != pixel_area
    ):
        raise ValueError("Cached candidate pixel area is invalid.")
    source_window = record["sourceWindow"]
    region_id = record["regionId"]
    repeat_group = record["repeatGroup"]
    repeat_count = record["repeatCount"]
    if source_window is not None and (not isinstance(source_window, str) or not source_window):
        raise ValueError("Cached candidate sourceWindow is invalid.")
    if region_id is not None and (not isinstance(region_id, str) or not region_id):
        raise ValueError("Cached candidate regionId is invalid.")
    if repeat_group is not None and (not isinstance(repeat_group, str) or not repeat_group.startswith("repeat.")):
        raise ValueError("Cached candidate repeatGroup is invalid.")
    if not isinstance(repeat_count, int) or isinstance(repeat_count, bool) or repeat_count < 1:
        raise ValueError("Cached candidate repeatCount is invalid.")
    if (repeat_group is None) != (repeat_count == 1):
        raise ValueError("Cached candidate repeat group/count relationship is invalid.")
    numeric_fields = {
        "saturation": (0.0, 1.0),
        "localContrast": (0.0, 1.0),
        "fillRatio": (0.0, 1.0),
        "edgeDensity": (0.0, 1.0),
        "provisionalScore": (0.0, 1.0),
        "score": (0.0, 1.0),
    }
    for field_name, (minimum, maximum) in numeric_fields.items():
        value = record[field_name]
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)) or not minimum <= float(value) <= maximum:
            raise ValueError(f"Cached candidate {field_name} is invalid.")
    if any(not math.isfinite(float(value)) or not 0.0 <= float(value) <= 255.0 for value in mean_rgb):
        raise ValueError("Cached candidate meanRgb falls outside the valid range.")
    candidate = Candidate(
        detector=detector,
        bbox=tuple(bbox),
        runs=[tuple(run) for run in runs],
        pixel_area=pixel_area,
        source_window=source_window,
    )
    candidate.detectors = set(detectors)
    candidate.mean_rgb = tuple(float(value) for value in mean_rgb)
    candidate.saturation = float(record["saturation"])
    candidate.local_contrast = float(record["localContrast"])
    candidate.fill_ratio = float(record["fillRatio"])
    candidate.edge_density = float(record["edgeDensity"])
    candidate.provisional_score = float(record["provisionalScore"])
    candidate.score = float(record["score"])
    candidate.repeat_group = repeat_group
    candidate.repeat_count = repeat_count
    candidate.candidate_id = candidate_id
    candidate.region_id = region_id
    return candidate


def detection_cache_identity(rgb: "np.ndarray", windows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return {
        "format": DETECTION_CACHE_KEY_FORMAT,
        "scannerSourceSha256": sha256_file(Path(__file__).resolve()),
        "decodedRasterSha256": decoded_raster_sha256(rgb),
        "decodedMode": "RGB",
        "pixelSize": [int(rgb.shape[1]), int(rgb.shape[0])],
        "numpyVersion": str(np.__version__),
        "pillowVersion": str(getattr(Image, "__version__", "unknown")),
        "pythonRuntime": {
            "implementation": str(getattr(sys.implementation, "name", "unknown")),
            "cacheTag": str(getattr(sys.implementation, "cache_tag", "unknown")),
            "versionInfo": {
                "major": int(sys.version_info.major),
                "minor": int(sys.version_info.minor),
                "micro": int(sys.version_info.micro),
                "releaseLevel": str(sys.version_info.releaselevel),
                "serial": int(sys.version_info.serial),
            },
            "byteOrder": sys.byteorder,
            "system": platform.system(),
            "machine": platform.machine(),
        },
        "salienceThresholds": {"medium": MEDIUM_SCORE, "high": HIGH_SCORE},
        "detectorWindows": [
            {
                "id": str(window.get("id", "")),
                "source": str(window.get("source", "")),
                "bbox": [int(value) for value in window["bbox"]],
            }
            for window in windows
        ],
    }


def read_detection_cache(path: Path, expected_key: str, expected_identity: dict[str, Any]) -> list[Candidate]:
    raw = gzip.decompress(path.read_bytes())
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict) or set(value) != {"format", "key", "identity", "candidatePayloadSha256", "candidates"}:
        raise ValueError("Detection cache entry does not match the closed manifest shape.")
    if value["format"] != DETECTION_CACHE_FORMAT or value["key"] != expected_key or value["identity"] != expected_identity:
        raise ValueError("Detection cache identity does not match the current scan.")
    records = value["candidates"]
    if not isinstance(records, list):
        raise ValueError("Detection cache candidates must be an array.")
    if sha256_bytes(canonical_json_bytes(records)) != value["candidatePayloadSha256"]:
        raise ValueError("Detection cache candidate payload hash mismatch.")
    pixel_size = expected_identity.get("pixelSize")
    if not (isinstance(pixel_size, list) and len(pixel_size) == 2 and all(isinstance(value, int) and value > 0 for value in pixel_size)):
        raise ValueError("Detection cache identity pixelSize is invalid.")
    width, height = pixel_size
    candidates = [
        candidate_from_cache_record(record, width=width, height=height)
        for record in records
        if isinstance(record, dict)
    ]
    if len(candidates) != len(records):
        raise ValueError("Detection cache contains a non-object candidate.")
    expected_ids = [f"vc.{index:04d}" for index in range(1, len(candidates) + 1)]
    if [candidate.candidate_id for candidate in candidates] != expected_ids:
        raise ValueError("Detection cache candidate ordering or identifiers are invalid.")
    window_ids = {window.get("id") for window in expected_identity.get("detectorWindows", []) if isinstance(window, dict)}
    region_ids = {
        window.get("id")
        for window in expected_identity.get("detectorWindows", [])
        if isinstance(window, dict) and window.get("source") == "requirement"
    }
    if any(candidate.source_window is not None and candidate.source_window not in window_ids for candidate in candidates):
        raise ValueError("Detection cache candidate references an unknown detector window.")
    if any(candidate.region_id is not None and candidate.region_id not in region_ids for candidate in candidates):
        raise ValueError("Detection cache candidate references an unknown Requirement region.")
    return candidates


def write_detection_cache(
    path: Path,
    key: str,
    identity: dict[str, Any],
    candidates: Sequence[Candidate],
) -> int:
    records = [candidate_cache_record(candidate) for candidate in candidates]
    value = {
        "format": DETECTION_CACHE_FORMAT,
        "key": key,
        "identity": identity,
        "candidatePayloadSha256": sha256_bytes(canonical_json_bytes(records)),
        "candidates": records,
    }
    compressed = gzip.compress(canonical_json_bytes(value), compresslevel=9, mtime=0)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix=f".{key}.", suffix=".tmp", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(compressed)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return len(compressed)


def normalized_bbox(bbox: Sequence[int], width: int, height: int) -> list[float]:
    x, y, w, h = bbox
    return [round(x / width, 6), round(y / height, 6), round(w / width, 6), round(h / height, 6)]


def pixel_bbox(bounds: Sequence[float], width: int, height: int) -> tuple[int, int, int, int]:
    x, y, w, h = bounds
    left = max(0, min(width, int(round(float(x) * width))))
    top = max(0, min(height, int(round(float(y) * height))))
    right = max(left, min(width, int(round((float(x) + float(w)) * width))))
    bottom = max(top, min(height, int(round((float(y) + float(h)) * height))))
    return left, top, right - left, bottom - top


def bbox_area(bbox: Sequence[int]) -> int:
    return max(0, int(bbox[2])) * max(0, int(bbox[3]))


def bbox_intersection(a: Sequence[int], b: Sequence[int]) -> int:
    ax, ay, aw, ah = map(int, a)
    bx, by, bw, bh = map(int, b)
    width = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    height = max(0, min(ay + ah, by + bh) - max(ay, by))
    return width * height


def bbox_iou(a: Sequence[int], b: Sequence[int]) -> float:
    intersection = bbox_intersection(a, b)
    union = bbox_area(a) + bbox_area(b) - intersection
    return intersection / union if union else 0.0


def bbox_contains_point(bbox: Sequence[int], point: tuple[float, float]) -> bool:
    x, y, w, h = bbox
    return x <= point[0] <= x + w and y <= point[1] <= y + h


def rectangle_runs(bbox: Sequence[int]) -> list[tuple[int, int, int]]:
    x, y, w, h = map(int, bbox)
    return [(row, x, w) for row in range(y, y + h)]


class UnionFind:
    def __init__(self) -> None:
        self.parent: list[int] = []
        self.rank: list[int] = []

    def add(self) -> int:
        index = len(self.parent)
        self.parent.append(index)
        self.rank.append(0)
        return index

    def find(self, value: int) -> int:
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        if self.rank[left_root] < self.rank[right_root]:
            left_root, right_root = right_root, left_root
        self.parent[right_root] = left_root
        if self.rank[left_root] == self.rank[right_root]:
            self.rank[left_root] += 1


def connected_runs(mask: "np.ndarray", *, min_area: int, eight_connected: bool = True) -> list[dict[str, Any]]:
    """Connected components as row runs, avoiding scipy/opencv dependencies."""

    union_find = UnionFind()
    all_runs: list[tuple[int, int, int, int]] = []
    previous: list[tuple[int, int, int]] = []
    padding = 1 if eight_connected else 0

    for y in range(mask.shape[0]):
        # Signed transitions are required: uint8 would wrap 0 - 1 to 255 and
        # silently lose every run end.
        row = np.asarray(mask[y], dtype=np.int8)
        transitions = np.diff(np.pad(row, (1, 1), constant_values=0))
        starts = np.flatnonzero(transitions == 1)
        ends = np.flatnonzero(transitions == -1)
        current: list[tuple[int, int, int]] = []
        previous_index = 0
        for start, end in zip(starts.tolist(), ends.tolist()):
            label = union_find.add()
            while previous_index < len(previous) and previous[previous_index][1] < start - padding:
                previous_index += 1
            scan_index = previous_index
            while scan_index < len(previous) and previous[scan_index][0] <= end - 1 + padding:
                prior_start, prior_end, prior_label = previous[scan_index]
                if prior_end >= start - padding and prior_start <= end - 1 + padding:
                    union_find.union(label, prior_label)
                scan_index += 1
            current.append((start, end - 1, label))
            all_runs.append((y, start, end - 1, label))
        previous = current

    aggregates: dict[int, dict[str, Any]] = {}
    for y, start, end, label in all_runs:
        root = union_find.find(label)
        aggregate = aggregates.setdefault(
            root,
            {"area": 0, "min_x": start, "max_x": end, "min_y": y, "max_y": y, "runs": []},
        )
        length = end - start + 1
        aggregate["area"] += length
        aggregate["min_x"] = min(aggregate["min_x"], start)
        aggregate["max_x"] = max(aggregate["max_x"], end)
        aggregate["min_y"] = min(aggregate["min_y"], y)
        aggregate["max_y"] = max(aggregate["max_y"], y)
        aggregate["runs"].append((y, start, length))

    result: list[dict[str, Any]] = []
    for aggregate in aggregates.values():
        if aggregate["area"] < min_area:
            continue
        aggregate["bbox"] = (
            aggregate["min_x"],
            aggregate["min_y"],
            aggregate["max_x"] - aggregate["min_x"] + 1,
            aggregate["max_y"] - aggregate["min_y"] + 1,
        )
        result.append(aggregate)
    return result


def dilate(mask: "np.ndarray", radius: int = 1) -> "np.ndarray":
    result = mask.copy()
    height, width = mask.shape
    for dy in range(-radius, radius + 1):
        source_y0 = max(0, -dy)
        source_y1 = min(height, height - dy)
        target_y0 = source_y0 + dy
        target_y1 = source_y1 + dy
        for dx in range(-radius, radius + 1):
            source_x0 = max(0, -dx)
            source_x1 = min(width, width - dx)
            target_x0 = source_x0 + dx
            target_x1 = source_x1 + dx
            result[target_y0:target_y1, target_x0:target_x1] |= mask[source_y0:source_y1, source_x0:source_x1]
    return result


def erode(mask: "np.ndarray", radius: int = 1) -> "np.ndarray":
    return ~dilate(~mask, radius)


def close_mask(mask: "np.ndarray", radius: int = 1) -> "np.ndarray":
    return erode(dilate(mask, radius), radius)


def box_blur(channel: "np.ndarray", radius: int) -> "np.ndarray":
    padded = np.pad(channel.astype(np.float32), ((radius, radius), (radius, radius)), mode="edge")
    integral = np.pad(padded, ((1, 0), (1, 0)), mode="constant").cumsum(0).cumsum(1)
    size = radius * 2 + 1
    total = integral[size:, size:] - integral[:-size, size:] - integral[size:, :-size] + integral[:-size, :-size]
    return total / float(size * size)


def rgb_to_hsv(rgb: "np.ndarray") -> tuple["np.ndarray", "np.ndarray", "np.ndarray"]:
    data = rgb.astype(np.float32) / 255.0
    maximum = data.max(axis=2)
    minimum = data.min(axis=2)
    delta = maximum - minimum
    saturation = np.divide(delta, maximum, out=np.zeros_like(delta), where=maximum > 1e-6)
    hue = np.zeros_like(maximum)
    nonzero = delta > 1e-6
    red, green, blue = data[:, :, 0], data[:, :, 1], data[:, :, 2]
    red_max = nonzero & (maximum == red)
    green_max = nonzero & (maximum == green)
    blue_max = nonzero & (maximum == blue)
    hue[red_max] = ((green[red_max] - blue[red_max]) / delta[red_max]) % 6.0
    hue[green_max] = (blue[green_max] - red[green_max]) / delta[green_max] + 2.0
    hue[blue_max] = (red[blue_max] - green[blue_max]) / delta[blue_max] + 4.0
    hue /= 6.0
    return hue, saturation, maximum


def component_candidate(
    detector: str,
    component: dict[str, Any],
    *,
    scale: int = 1,
    source_window: str | None = None,
    source_size: tuple[int, int] | None = None,
) -> Candidate:
    x, y, w, h = component["bbox"]
    if scale == 1:
        bbox = (x, y, w, h)
        runs = list(component["runs"])
        area = int(component["area"])
    else:
        if source_size is None:
            raise ValueError("Scaled component candidates require the decoded source size.")
        source_width, source_height = source_size
        left = x * scale
        top = y * scale
        right = min(source_width, (x + w) * scale)
        bottom = min(source_height, (y + h) * scale)
        bbox = (left, top, right - left, bottom - top)
        runs = rectangle_runs(bbox)
        area = bbox_area(bbox)
    return Candidate(detector=detector, bbox=bbox, runs=runs, pixel_area=area, source_window=source_window)


def add_chromatic_candidates(rgb: "np.ndarray") -> list[Candidate]:
    height, width = rgb.shape[:2]
    hue, saturation, value = rgb_to_hsv(rgb)
    candidates: list[Candidate] = []
    min_area = max(12, int(width * height * 0.000003))
    for hue_index in range(12):
        lower = hue_index / 12.0
        upper = (hue_index + 1) / 12.0
        mask = (hue >= lower) & (hue < upper) & (saturation >= 0.24) & (value >= 0.22)
        mask = close_mask(mask, 1)
        components = connected_runs(mask, min_area=min_area)
        components.sort(key=lambda item: item["area"], reverse=True)
        for component in components[:80]:
            x, y, w, h = component["bbox"]
            fill = component["area"] / max(1, w * h)
            if min(w, h) < 2 or max(w, h) < 8 or fill < 0.08:
                continue
            if w * h > width * height * 0.7:
                continue
            candidates.append(component_candidate("chromatic-component", component))
    return candidates


def add_contrast_candidates(rgb: "np.ndarray") -> list[Candidate]:
    height, width = rgb.shape[:2]
    luminance = 0.2126 * rgb[:, :, 0] + 0.7152 * rgb[:, :, 1] + 0.0722 * rgb[:, :, 2]
    local = box_blur(luminance, 9)
    residual = np.abs(luminance - local)
    threshold = max(22.0, float(np.percentile(residual, 91.5)))
    mask = close_mask(residual >= threshold, 1)
    components = connected_runs(mask, min_area=max(18, int(width * height * 0.000004)))
    candidates: list[Candidate] = []
    components.sort(key=lambda item: item["area"], reverse=True)
    for component in components[:350]:
        x, y, w, h = component["bbox"]
        fill = component["area"] / max(1, w * h)
        # This detector is most useful for compact controls and plates. Sparse
        # text strokes and illustration fragments are retained by the contact
        # sheet, not promoted as independent visual primitives.
        if min(w, h) < 6 or max(w, h) < 12 or fill < 0.11:
            continue
        if w * h > width * height * 0.75:
            continue
        candidates.append(component_candidate("local-contrast", component))
    return candidates


def add_quantized_candidates(rgb: "np.ndarray") -> list[Candidate]:
    scale = 2
    source_height, source_width = rgb.shape[:2]
    sampled = rgb[::scale, ::scale]
    height, width = sampled.shape[:2]
    quantized = sampled // 32
    codes = quantized[:, :, 0].astype(np.int16) * 64 + quantized[:, :, 1].astype(np.int16) * 8 + quantized[:, :, 2]
    values, counts = np.unique(codes, return_counts=True)
    ranked = sorted(zip(counts.tolist(), values.tolist()), reverse=True)
    candidates: list[Candidate] = []
    for count, code in ranked[:48]:
        if count < 20 or count > width * height * 0.42:
            continue
        mask = close_mask(codes == code, 1)
        components = connected_runs(mask, min_area=12)
        components.sort(key=lambda item: item["area"], reverse=True)
        for component in components[:35]:
            x, y, w, h = component["bbox"]
            fill = component["area"] / max(1, w * h)
            if min(w, h) < 3 or max(w, h) < 10 or fill < 0.24:
                continue
            if w * h > width * height * 0.55:
                continue
            candidates.append(
                component_candidate(
                    "quantized-color",
                    component,
                    scale=scale,
                    source_size=(source_width, source_height),
                )
            )
    return candidates


def requirement_regions(requirement: dict[str, Any] | None, width: int, height: int) -> list[dict[str, Any]]:
    regions: list[dict[str, Any]] = []
    if requirement:
        for region in requirement.get("uiModel", {}).get("regions", []):
            bounds = region.get("bounds")
            if not isinstance(bounds, list) or len(bounds) != 4:
                continue
            bbox = pixel_bbox(bounds, width, height)
            if bbox[2] >= 100 and bbox[3] >= 40 and bbox_area(bbox) < width * height * 0.9:
                regions.append({"id": str(region.get("id", "region")), "bbox": bbox, "source": "requirement"})
    # Blind fallback windows ensure discovery does not depend on a complete requirement.
    for row in range(3):
        for column in range(4):
            x0 = int(round(column * width / 4))
            x1 = int(round((column + 1) * width / 4))
            y0 = int(round(row * height / 3))
            y1 = int(round((row + 1) * height / 3))
            regions.append({"id": f"grid.{row}.{column}", "bbox": (x0, y0, x1 - x0, y1 - y0), "source": "blind-grid"})
    unique: dict[tuple[int, int, int, int], dict[str, Any]] = {}
    for region in regions:
        unique.setdefault(tuple(region["bbox"]), region)
    return list(unique.values())


def smooth_rows(rows: "np.ndarray", window: int = 5) -> "np.ndarray":
    result = np.empty_like(rows, dtype=np.float32)
    kernel = np.ones(window, dtype=np.float32) / float(window)
    for channel in range(rows.shape[1]):
        result[:, channel] = np.convolve(rows[:, channel], kernel, mode="same")
    return result


def add_band_candidates(rgb: "np.ndarray", windows: Sequence[dict[str, Any]]) -> list[Candidate]:
    image_height, image_width = rgb.shape[:2]
    candidates: list[Candidate] = []
    for window in windows:
        x, y, width, height = window["bbox"]
        crop = rgb[y : y + height, x : x + width]
        if crop.size == 0 or width < 80 or height < 30:
            continue
        # Row means retain broad low-contrast plates while reducing glyph noise.
        rows = crop.astype(np.float32).mean(axis=1)
        rows = smooth_rows(rows, 5)
        edge = np.linalg.norm(rows[4:] - rows[:-4], axis=1)
        if edge.size < 8:
            continue
        threshold = max(4.0, float(np.percentile(edge, 80)))
        raw_peaks = [index + 2 for index in range(1, edge.size - 1) if edge[index] >= threshold and edge[index] >= edge[index - 1] and edge[index] >= edge[index + 1]]
        peaks: list[int] = []
        for peak in sorted(raw_peaks, key=lambda index: float(edge[index - 2]), reverse=True):
            if all(abs(peak - existing) >= 4 for existing in peaks):
                peaks.append(peak)
        peaks.sort()
        boundaries = sorted(set([0, height] + peaks))
        for start, end in zip(boundaries, boundaries[1:]):
            band_height = end - start
            if band_height < 5 or band_height > min(150, int(height * 0.38)):
                continue
            if width / band_height < 3.0:
                continue
            before = rows[max(0, start - 8) : start]
            after = rows[end : min(height, end + 8)]
            surroundings = np.concatenate([part for part in (before, after) if part.size], axis=0)
            if not surroundings.size:
                continue
            inside_color = rows[start:end].mean(axis=0)
            outside_color = surroundings.mean(axis=0)
            contrast = float(np.linalg.norm(inside_color - outside_color))
            if contrast < 4.0:
                continue

            # Find the broadest columns responsible for the row transition.
            inside_columns = crop[start:end].astype(np.float32).mean(axis=0)
            surrounding_rows = np.concatenate(
                [crop[max(0, start - 5) : start], crop[end : min(height, end + 5)]], axis=0
            )
            if surrounding_rows.size:
                outside_columns = surrounding_rows.astype(np.float32).mean(axis=0)
                column_delta = np.linalg.norm(inside_columns - outside_columns, axis=1)
                column_threshold = max(3.5, float(np.percentile(column_delta, 55)))
                column_mask = dilate((column_delta >= column_threshold)[None, :], 2)[0]
                # Signed transitions are required: uint8 would wrap the 1 -> 0
                # edge to 255 and force every detected band back to full width.
                transitions = np.diff(np.pad(column_mask.astype(np.int8), (1, 1)))
                starts = np.flatnonzero(transitions == 1)
                ends = np.flatnonzero(transitions == -1)
                runs = [(int(left), int(right)) for left, right in zip(starts, ends) if right - left >= width * 0.23]
                if runs:
                    left, right = max(runs, key=lambda pair: pair[1] - pair[0])
                else:
                    left, right = 0, width
            else:
                left, right = 0, width
            bbox = (x + left, y + start, right - left, band_height)
            if bbox[2] < 40:
                continue
            candidate = Candidate(
                detector="horizontal-band",
                bbox=bbox,
                runs=rectangle_runs(bbox),
                pixel_area=bbox_area(bbox),
                source_window=window["id"],
            )
            candidates.append(candidate)
    return candidates


def candidate_statistics(candidate: Candidate, rgb: "np.ndarray", saturation_map: "np.ndarray") -> None:
    height, width = rgb.shape[:2]
    x, y, box_width, box_height = candidate.bbox
    values: list["np.ndarray"] = []
    saturation_values: list["np.ndarray"] = []
    actual_area = 0
    for row, start, length in candidate.runs:
        if row < 0 or row >= height or length <= 0:
            continue
        left = max(0, start)
        right = min(width, start + length)
        if right <= left:
            continue
        values.append(rgb[row, left:right])
        saturation_values.append(saturation_map[row, left:right])
        actual_area += right - left
    if not values:
        return
    pixels = np.concatenate(values, axis=0).astype(np.float32)
    candidate.mean_rgb = tuple(float(value) for value in pixels.mean(axis=0))
    candidate.saturation = float(np.concatenate(saturation_values).mean())
    candidate.pixel_area = actual_area
    candidate.fill_ratio = actual_area / max(1, box_width * box_height)

    margin = max(5, min(18, int(round(max(box_width, box_height) * 0.08))))
    left = max(0, x - margin)
    top = max(0, y - margin)
    right = min(width, x + box_width + margin)
    bottom = min(height, y + box_height + margin)
    outer = rgb[top:bottom, left:right].astype(np.float32)
    ring_parts = [
        outer[: max(0, y - top)],
        outer[max(0, y + box_height - top) :],
        outer[max(0, y - top) : max(0, y + box_height - top), : max(0, x - left)],
        outer[max(0, y - top) : max(0, y + box_height - top), max(0, x + box_width - left) :],
    ]
    ring = np.concatenate([part.reshape(-1, 3) for part in ring_parts if part.size], axis=0) if any(part.size for part in ring_parts) else pixels
    ring_mean = ring.mean(axis=0)
    candidate.local_contrast = min(1.0, float(np.linalg.norm(np.asarray(candidate.mean_rgb) - ring_mean)) / 100.0)

    crop = rgb[y : y + box_height, x : x + box_width].astype(np.float32)
    if crop.shape[0] > 1 and crop.shape[1] > 1:
        luminance = 0.2126 * crop[:, :, 0] + 0.7152 * crop[:, :, 1] + 0.0722 * crop[:, :, 2]
        horizontal = np.abs(np.diff(luminance, axis=1))
        vertical = np.abs(np.diff(luminance, axis=0))
        candidate.edge_density = float((horizontal > 14).mean() * 0.5 + (vertical > 14).mean() * 0.5)

    image_area = width * height
    area_score = min(1.0, math.sqrt(candidate.pixel_area / max(1, image_area)) * 20.0)
    aspect = max(box_width / max(1, box_height), box_height / max(1, box_width))
    shape_score = min(1.0, aspect / 8.0)
    fill = min(1.0, candidate.fill_ratio)
    if candidate.detector == "chromatic-component":
        score = 0.38 * candidate.local_contrast + 0.25 * candidate.saturation + 0.13 * area_score + 0.14 * shape_score + 0.10 * fill
    elif candidate.detector == "horizontal-band":
        score = 0.38 * candidate.local_contrast + 0.20 * area_score + 0.18 * shape_score + 0.12 * fill + 0.12 * candidate.edge_density
    elif candidate.detector == "quantized-color":
        score = 0.36 * candidate.local_contrast + 0.24 * area_score + 0.18 * shape_score + 0.14 * fill + 0.08 * candidate.saturation
    else:
        score = 0.48 * candidate.local_contrast + 0.20 * area_score + 0.17 * candidate.edge_density + 0.15 * shape_score
    candidate.provisional_score = min(1.0, score)
    if candidate.source_window and candidate.source_window.startswith("grid."):
        # Blind windows protect recall when semantic regions are incomplete,
        # but their hard tile boundaries make them noisier than real regions.
        candidate.provisional_score = max(0.0, candidate.provisional_score - 0.14)
    candidate.score = candidate.provisional_score


def deduplicate_candidates(candidates: Sequence[Candidate]) -> list[Candidate]:
    ordered = sorted(candidates, key=lambda candidate: (candidate.provisional_score, candidate.pixel_area), reverse=True)
    kept: list[Candidate] = []
    for candidate in ordered:
        merged = None
        candidate_area = bbox_area(candidate.bbox)
        for existing in kept:
            intersection = bbox_intersection(candidate.bbox, existing.bbox)
            smaller = min(candidate_area, bbox_area(existing.bbox))
            size_ratio = smaller / max(candidate_area, bbox_area(existing.bbox), 1)
            if bbox_iou(candidate.bbox, existing.bbox) >= 0.72 or (smaller and intersection / smaller >= 0.93 and size_ratio >= 0.42):
                merged = existing
                break
        if merged is None:
            kept.append(candidate)
        else:
            merged.detectors.update(candidate.detectors)
            if candidate.provisional_score > merged.provisional_score:
                merged.provisional_score = candidate.provisional_score
                merged.score = candidate.score
    return kept


def candidate_priority(candidate: Candidate) -> tuple[float, float, float]:
    """Prefer coherent visual objects over window-wide band fragments."""

    detector_bonus = 0.14 if "chromatic-component" in candidate.detectors else 0.09 if "quantized-color" in candidate.detectors else 0.04 if "local-contrast" in candidate.detectors else 0.0
    coherence = min(1.0, candidate.fill_ratio) * 0.10 + candidate.local_contrast * 0.10
    return candidate.score + detector_bonus + coherence, candidate.fill_ratio, candidate.pixel_area


def suppress_band_fragments(candidates: Sequence[Candidate]) -> list[Candidate]:
    """Remove overlapping horizontal-band fragments when a stronger object exists.

    Region and blind-grid windows intentionally overlap. Without this pass the
    same plate can be emitted once per window and as multiple adjacent slices.
    """

    ordered = sorted(candidates, key=candidate_priority, reverse=True)
    kept: list[Candidate] = []
    for candidate in ordered:
        x, y, width, height = candidate.bbox
        area = bbox_area(candidate.bbox)
        fragmented = False
        for existing in kept:
            ex, ey, ew, eh = existing.bbox
            intersection = bbox_intersection(candidate.bbox, existing.bbox)
            if not intersection:
                continue
            candidate_coverage = intersection / max(1, area)
            existing_coverage = intersection / max(1, bbox_area(existing.bbox))
            vertical_overlap = max(0, min(y + height, ey + eh) - max(y, ey)) / max(1, min(height, eh))
            if candidate.detector == "horizontal-band" and (
                candidate_coverage >= 0.62
                or (vertical_overlap >= 0.82 and existing_coverage >= 0.28 and width >= ew * 0.85)
            ):
                existing.detectors.update(candidate.detectors)
                fragmented = True
                break
        if not fragmented:
            kept.append(candidate)
    return kept


def suppress_low_information_candidates(candidates: Sequence[Candidate], image_width: int, image_height: int) -> list[Candidate]:
    result: list[Candidate] = []
    for candidate in candidates:
        _, _, width, height = candidate.bbox
        area_fraction = bbox_area(candidate.bbox) / max(1, image_width * image_height)
        aspect = max(width / max(1, height), height / max(1, width))
        detectors = candidate.detectors
        # Screen-border/background bands are not independent visual objects.
        if area_fraction > 0.12 and candidate.local_contrast < 0.30:
            continue
        # Quantized vertical fragments and tiny sparse components mostly come
        # from illustration shading or text antialiasing.
        if detectors == {"quantized-color"} and height > width * 5 and candidate.fill_ratio < 0.55:
            continue
        if candidate.pixel_area < 30 and candidate.repeat_count == 1:
            continue
        if max(width, height) < 9:
            continue
        if aspect < 1.35 and candidate.pixel_area < 180 and candidate.repeat_count == 1 and candidate.saturation < 0.24:
            continue
        result.append(candidate)
    return result


def assign_regions(candidates: Sequence[Candidate], regions: Sequence[dict[str, Any]]) -> None:
    requirement_regions_only = [region for region in regions if region.get("source") == "requirement"]
    for candidate in candidates:
        x, y, width, height = candidate.bbox
        center = (x + width / 2, y + height / 2)
        containing = [region for region in requirement_regions_only if bbox_contains_point(region["bbox"], center)]
        if containing:
            candidate.region_id = min(containing, key=lambda region: bbox_area(region["bbox"]))["id"]


def assign_repeat_groups(candidates: Sequence[Candidate], image_width: int, image_height: int) -> None:
    groups: list[list[Candidate]] = []
    eligible = [candidate for candidate in candidates if candidate.detector in {"horizontal-band", "quantized-color", "local-contrast"}]
    for candidate in sorted(eligible, key=lambda item: (item.region_id or "", item.bbox[1], item.bbox[0])):
        x, y, width, height = candidate.bbox
        selected: list[Candidate] | None = None
        for group in groups:
            reference = group[0]
            rx, ry, rw, rh = reference.bbox
            same_region = candidate.region_id == reference.region_id and candidate.region_id is not None
            aligned = abs(x - rx) <= image_width * 0.035
            similar_width = min(width, rw) / max(width, rw, 1) >= 0.68
            similar_height = min(height, rh) / max(height, rh, 1) >= 0.62
            color_distance = float(np.linalg.norm(np.asarray(candidate.mean_rgb) - np.asarray(reference.mean_rgb))) <= 46.0
            separated = abs(y - ry) >= min(height, rh) * 0.55
            if same_region and aligned and similar_width and similar_height and color_distance and separated:
                selected = group
                break
        if selected is None:
            groups.append([candidate])
        else:
            selected.append(candidate)

    repeat_index = 1
    for group in groups:
        if len(group) < 2:
            continue
        group_id = f"repeat.{repeat_index:03d}"
        repeat_index += 1
        for candidate in group:
            candidate.repeat_group = group_id
            candidate.repeat_count = len(group)
            repeat_bonus = min(0.22, 0.08 + 0.035 * (len(group) - 2))
            candidate.score = min(1.0, candidate.provisional_score + repeat_bonus)


def salience_level(score: float) -> str:
    if score >= HIGH_SCORE:
        return "high"
    if score >= MEDIUM_SCORE:
        return "medium"
    return "low"


def layout_paths(layout_args: Sequence[Path]) -> list[Path]:
    result: list[Path] = []
    for value in layout_args:
        if value.is_dir():
            result.extend(sorted(value.glob("*.json")))
        elif value.is_file():
            result.append(value)
        else:
            raise FileNotFoundError(f"Layout path does not exist: {value}")
    return sorted(set(path.resolve() for path in result))


def drawable_role(node: dict[str, Any]) -> str | None:
    role = str(node.get("role", ""))
    if role.startswith("visual."):
        return "image"
    if role.startswith("text."):
        return "text"
    if role == "input.button":
        return "button"
    return None


def is_background_like(node: dict[str, Any]) -> bool:
    rect = node.get("rect")
    properties = node.get("properties", {})
    color = properties.get("color", {}) if isinstance(properties, dict) else {}
    alpha = color.get("a") if isinstance(color, dict) else None
    if isinstance(alpha, (int, float)) and alpha <= 0.05:
        return True
    if node.get("rootLayer") == "background" or node.get("fullHeight") is True:
        return True
    if isinstance(rect, list) and len(rect) == 4 and float(rect[2]) * float(rect[3]) >= 0.65:
        return True
    return False


def declared_visuals(
    requirement: dict[str, Any] | None,
    layouts: Sequence[tuple[Path, dict[str, Any]]],
    image_width: int,
    image_height: int,
) -> list[dict[str, Any]]:
    visuals: list[dict[str, Any]] = []
    screen_layouts = [(path, layout) for path, layout in layouts if layout.get("profile", {}).get("assetKind") == "screen"]
    child_layouts = [(path, layout) for path, layout in layouts if layout.get("profile", {}).get("assetKind") != "screen"]
    child_by_name = {str(layout.get("asset", {}).get("name", "")).lower(): (path, layout) for path, layout in child_layouts}

    for path, layout in screen_layouts:
        for node in layout.get("nodes", []):
            render_kind = drawable_role(node)
            rect = node.get("rect")
            if render_kind is None or not isinstance(rect, list) or len(rect) != 4 or is_background_like(node):
                continue
            visuals.append(
                {
                    "id": f"layout:{layout.get('asset', {}).get('name')}:{node.get('id')}",
                    "layoutPath": str(path),
                    "layoutNodeId": node.get("id"),
                    "role": node.get("role"),
                    "renderKind": render_kind,
                    "bbox": pixel_bbox(rect, image_width, image_height),
                    "projection": "screen",
                }
            )

        for host in layout.get("nodes", []):
            if not str(host.get("role", "")).startswith("collection."):
                continue
            properties = host.get("properties", {})
            entry = properties.get("entryWidgetClass", {}) if isinstance(properties, dict) else {}
            reference = str(entry.get("refPath", "")) if isinstance(entry, dict) else ""
            matched = next(((name, data) for name, data in child_by_name.items() if name and name in reference.lower()), None)
            if matched is None:
                continue
            _, (child_path, child_layout) = matched
            host_rect = host.get("rect")
            if not isinstance(host_rect, list) or len(host_rect) != 4:
                continue
            host_bbox = pixel_bbox(host_rect, image_width, image_height)
            child_size = child_layout.get("referenceSize", [host_bbox[2], host_bbox[3]])
            if not isinstance(child_size, list) or len(child_size) != 2:
                continue
            entry_width = max(1.0, float(child_size[0]))
            entry_height = max(1.0, float(child_size[1]))
            preview_count = int(properties.get("designerPreviewEntries", max(1, math.ceil(host_bbox[3] / entry_height))))
            spacing = float(properties.get("verticalEntrySpacing", 0))
            for instance_index in range(max(0, preview_count)):
                origin_x = float(host_bbox[0])
                origin_y = float(host_bbox[1]) + instance_index * (entry_height + spacing)
                if origin_y >= host_bbox[1] + host_bbox[3]:
                    break
                for node in child_layout.get("nodes", []):
                    render_kind = drawable_role(node)
                    rect = node.get("rect")
                    if render_kind is None or not isinstance(rect, list) or len(rect) != 4 or is_background_like(node):
                        continue
                    projected = (
                        int(round(origin_x + float(rect[0]) * entry_width)),
                        int(round(origin_y + float(rect[1]) * entry_height)),
                        int(round(float(rect[2]) * entry_width)),
                        int(round(float(rect[3]) * entry_height)),
                    )
                    visuals.append(
                        {
                            "id": f"layout:{child_layout.get('asset', {}).get('name')}:{node.get('id')}@{instance_index}",
                            "layoutPath": str(child_path),
                            "layoutNodeId": node.get("id"),
                            "role": node.get("role"),
                            "renderKind": render_kind,
                            "bbox": projected,
                            "projection": "collection-entry-preview",
                            "hostNodeId": host.get("id"),
                            "instanceIndex": instance_index,
                        }
                    )

    if requirement:
        evidence = {item.get("id"): item for item in requirement.get("evidence", []) if isinstance(item, dict)}
        for element in requirement.get("uiModel", {}).get("elements", []):
            if element.get("inBuildScope") is False:
                continue
            bounds = element.get("bounds")
            if not (isinstance(bounds, list) and len(bounds) == 4):
                geometry = evidence.get(element.get("geometryEvidenceId"), {})
                bounds = geometry.get("bounds") if isinstance(geometry, dict) else None
            if not isinstance(bounds, list) or len(bounds) != 4:
                continue
            kind = str(element.get("kind", ""))
            render_kind = "text" if kind == "text" else "button" if kind == "button" else "image" if kind == "image" else None
            if render_kind:
                visuals.append(
                    {
                        "id": f"requirement:{element.get('id')}",
                        "requirementElementId": element.get("id"),
                        "renderKind": render_kind,
                        "role": kind,
                        "bbox": pixel_bbox(bounds, image_width, image_height),
                        "projection": "requirement-geometry",
                    }
                )
    return visuals


def exclusion_rects(requirement: dict[str, Any] | None, width: int, height: int) -> list[dict[str, Any]]:
    if not requirement:
        return []
    regions = {region.get("id"): region for region in requirement.get("uiModel", {}).get("regions", []) if isinstance(region, dict)}
    exclusions: list[dict[str, Any]] = []
    for element in requirement.get("uiModel", {}).get("elements", []):
        if element.get("inBuildScope") is not False:
            continue
        bounds = element.get("bounds")
        source = "element-bounds"
        if not isinstance(bounds, list) or len(bounds) != 4:
            region = regions.get(element.get("regionId"), {})
            bounds = region.get("bounds") if isinstance(region, dict) else None
            source = "owning-region-proxy"
        if not isinstance(bounds, list) or len(bounds) != 4:
            continue
        exclusions.append(
            {
                "id": f"exclusion:{element.get('id')}",
                "elementId": element.get("id"),
                "reason": element.get("scopedOutReason", "Requirement element is outside build scope."),
                "claimIds": element.get("claimIds", []),
                "bbox": pixel_bbox(bounds, width, height),
                "geometrySource": source,
            }
        )
    return exclusions


def mapping_compatible(candidate: Candidate, visual: dict[str, Any]) -> bool:
    render_kind = visual.get("renderKind")
    if candidate.detector == "horizontal-band":
        return render_kind in {"image", "button"}
    if candidate.detector in {"chromatic-component", "quantized-color"}:
        return render_kind in {"image", "button", "text"}
    return render_kind in {"image", "button", "text"}


def candidate_mapping(candidate: Candidate, visuals: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    candidate_area = bbox_area(candidate.bbox)
    for visual in visuals:
        if not mapping_compatible(candidate, visual):
            continue
        visual_area = bbox_area(visual["bbox"])
        intersection = bbox_intersection(candidate.bbox, visual["bbox"])
        if not intersection:
            continue
        candidate_coverage = intersection / max(1, candidate_area)
        visual_coverage = intersection / max(1, visual_area)
        iou = bbox_iou(candidate.bbox, visual["bbox"])
        size_ratio = min(candidate_area, visual_area) / max(candidate_area, visual_area, 1)
        aspect = max(candidate.bbox[2] / max(1, candidate.bbox[3]), candidate.bbox[3] / max(1, candidate.bbox[2]))
        contained_detail = (
            candidate_coverage >= 0.86
            and candidate_area <= visual_area * 0.58
            and (
                visual.get("renderKind") in {"image", "button"}
                or (visual.get("renderKind") == "text" and candidate.detector != "horizontal-band" and aspect < 4.5)
            )
        )
        accepted = (
            (iou >= 0.18 and candidate_coverage >= 0.30)
            or (candidate_coverage >= 0.72 and size_ratio >= 0.13)
            or (visual_coverage >= 0.78 and size_ratio >= 0.18)
            or contained_detail
        )
        if not accepted:
            continue
        score = 0.45 * iou + 0.30 * candidate_coverage + 0.15 * visual_coverage + 0.10 * size_ratio
        matches.append(
            {
                "declaredVisualId": visual["id"],
                "layoutNodeId": visual.get("layoutNodeId"),
                "requirementElementId": visual.get("requirementElementId"),
                "role": visual.get("role"),
                "projection": visual.get("projection"),
                "geometryScore": round(score, 4),
                "candidateCoverage": round(candidate_coverage, 4),
                "declaredVisualCoverage": round(visual_coverage, 4),
                "iou": round(iou, 4),
            }
        )
    matches.sort(key=lambda item: item["geometryScore"], reverse=True)
    return matches[:5]


def assign_dispositions(
    candidates: Sequence[Candidate],
    visuals: Sequence[dict[str, Any]],
    exclusions: Sequence[dict[str, Any]],
    image_width: int,
    image_height: int,
) -> None:
    for candidate in candidates:
        area = bbox_area(candidate.bbox)
        matching_exclusions = []
        for exclusion in exclusions:
            coverage = bbox_intersection(candidate.bbox, exclusion["bbox"]) / max(1, area)
            if coverage >= 0.62:
                matching_exclusions.append({**exclusion, "candidateCoverage": round(coverage, 4)})
        if matching_exclusions:
            candidate.disposition = {
                "status": "excluded",
                "reason": "Candidate falls predominantly inside an explicitly scoped-out Requirement element; owning-region proxies require reviewer confirmation.",
                "exclusions": matching_exclusions,
                "reviewRequired": any(item["geometrySource"] == "owning-region-proxy" for item in matching_exclusions),
            }
            continue
        matches = candidate_mapping(candidate, visuals)
        if matches:
            candidate.disposition = {
                "status": "mapped",
                "reason": "Candidate geometry is substantially represented by declared Requirement/Layout visual geometry.",
                "mappings": matches,
                "reviewRequired": False,
            }
            continue
        screen_fraction = area / max(1, image_width * image_height)
        if salience_level(candidate.score) == "low" and (screen_fraction > 0.12 or candidate.local_contrast < 0.06):
            candidate.disposition = {
                "status": "rejected-noise",
                "reason": "Low-salience broad/background variation; retained in the draft for audit but excluded from the open coverage gate.",
                "reviewRequired": True,
            }
            continue
        candidate.disposition = {
            "status": "unresolved",
            "reason": "No sufficiently similar declared visual geometry or evidence-backed exclusion was found.",
            "reviewRequired": True,
        }


def candidate_record(candidate: Candidate, width: int, height: int) -> dict[str, Any]:
    x, y, box_width, box_height = candidate.bbox
    return {
        "id": candidate.candidate_id,
        "detectors": sorted(candidate.detectors),
        "sourceWindow": candidate.source_window,
        "regionId": candidate.region_id,
        "geometry": {
            "sourceDimensions": [width, height],
            "pixelBounds": [x, y, box_width, box_height],
            "bounds": normalized_bbox(candidate.bbox, width, height),
            "pixelArea": candidate.pixel_area,
            "bboxFillRatio": round(candidate.fill_ratio, 6),
            "maskRleRows": [[row, start, length] for row, start, length in candidate.runs],
            "measurementMethod": "deterministic-raster-proposal",
        },
        "appearance": {
            "meanRgb": [round(value, 3) for value in candidate.mean_rgb],
            "meanSaturation": round(candidate.saturation, 6),
            "localContrast": round(candidate.local_contrast, 6),
            "edgeDensity": round(candidate.edge_density, 6),
        },
        "repetition": {
            "groupId": candidate.repeat_group,
            "observedInstanceCount": candidate.repeat_count,
        },
        "salience": {
            "score": round(candidate.score, 6),
            "level": salience_level(candidate.score),
            "thresholds": {"medium": MEDIUM_SCORE, "high": HIGH_SCORE},
        },
        "disposition": candidate.disposition,
    }


def bbox_union(boxes: Sequence[Sequence[int]]) -> tuple[int, int, int, int]:
    left = min(int(box[0]) for box in boxes)
    top = min(int(box[1]) for box in boxes)
    right = max(int(box[0]) + int(box[2]) for box in boxes)
    bottom = max(int(box[1]) + int(box[3]) for box in boxes)
    return left, top, right - left, bottom - top


def bbox_gap(left: Sequence[int], right: Sequence[int]) -> tuple[int, int]:
    lx, ly, lw, lh = map(int, left)
    rx, ry, rw, rh = map(int, right)
    horizontal = max(0, max(lx, rx) - min(lx + lw, rx + rw))
    vertical = max(0, max(ly, ry) - min(ly + lh, ry + rh))
    return horizontal, vertical


def review_cluster_pair_indices(open_candidates: Sequence[Candidate]) -> Iterable[tuple[int, int]]:
    """Yield a lossless superset of every pair that can satisfy the link rule.

    Every link rule requires horizontal intersection or a horizontal gap of at
    most ten pixels. A left-to-right sweep therefore avoids the prior quadratic
    all-pairs walk without changing which pairs reach the exact predicate.
    """

    ordered = sorted(
        enumerate(open_candidates),
        key=lambda item: (item[1].bbox[0], item[1].bbox[0] + item[1].bbox[2], item[1].bbox[1], item[0]),
    )
    active: list[tuple[int, Candidate]] = []
    for current_index, current in ordered:
        current_left = current.bbox[0]
        active = [
            (prior_index, prior)
            for prior_index, prior in active
            if prior.bbox[0] + prior.bbox[2] + 10 >= current_left
        ]
        for prior_index, prior in active:
            if prior.region_id == current.region_id:
                yield (min(prior_index, current_index), max(prior_index, current_index))
        active.append((current_index, current))


def review_cluster_candidates_linked(left: Candidate, right: Candidate) -> bool:
    intersection = bbox_intersection(left.bbox, right.bbox)
    smaller_area = min(bbox_area(left.bbox), bbox_area(right.bbox))
    horizontal_gap, vertical_gap = bbox_gap(left.bbox, right.bbox)
    lx, ly, lw, lh = left.bbox
    rx, ry, rw, rh = right.bbox
    horizontal_overlap = max(0, min(lx + lw, rx + rw) - max(lx, rx)) / max(1, min(lw, rw))
    vertical_overlap = max(0, min(ly + lh, ry + rh) - max(ly, ry)) / max(1, min(lh, rh))
    return (
        (smaller_area > 0 and intersection / smaller_area >= 0.18)
        or (vertical_gap <= 10 and horizontal_overlap >= 0.42)
        or (horizontal_gap <= 10 and vertical_overlap >= 0.50)
    )


def uncovered_review_clusters(
    candidates: Sequence[Candidate],
    image_width: int,
    image_height: int,
    *,
    diagnostics: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Group open primitives into auditable region-scale review subjects.

    Primitive IDs remain authoritative in inventory-draft.json. Clustering is
    only a workload aid and therefore never resolves or suppresses a member.
    """

    open_candidates = [
        candidate
        for candidate in candidates
        if candidate.disposition.get("status") == "unresolved"
        and salience_level(candidate.score) in {"medium", "high"}
    ]
    union_find = UnionFind()
    for _ in open_candidates:
        union_find.add()
    pair_comparisons = 0
    for left_index, right_index in review_cluster_pair_indices(open_candidates):
        pair_comparisons += 1
        if review_cluster_candidates_linked(open_candidates[left_index], open_candidates[right_index]):
            union_find.union(left_index, right_index)

    if diagnostics is not None:
        diagnostics.update(
            {
                "openCandidateCount": len(open_candidates),
                "pairComparisons": pair_comparisons,
                "exhaustivePairCount": len(open_candidates) * max(0, len(open_candidates) - 1) // 2,
            }
        )

    groups: dict[int, list[Candidate]] = defaultdict(list)
    for index, candidate in enumerate(open_candidates):
        groups[union_find.find(index)].append(candidate)

    records: list[dict[str, Any]] = []
    ordered_groups = sorted(
        groups.values(),
        key=lambda group: (
            min(candidate.bbox[1] for candidate in group),
            min(candidate.bbox[0] for candidate in group),
        ),
    )
    for index, group in enumerate(ordered_groups, 1):
        bbox = bbox_union([candidate.bbox for candidate in group])
        high_count = sum(salience_level(candidate.score) == "high" for candidate in group)
        records.append(
            {
                "id": f"cluster.{index:03d}",
                "regionId": group[0].region_id,
                "pixelBounds": list(bbox),
                "bounds": normalized_bbox(bbox, image_width, image_height),
                "memberCount": len(group),
                "memberIds": [candidate.candidate_id for candidate in sorted(group, key=lambda item: item.candidate_id)],
                "highMemberCount": high_count,
                "mediumMemberCount": len(group) - high_count,
                "maximumSalienceScore": round(max(candidate.score for candidate in group), 6),
                "detectors": sorted({detector for candidate in group for detector in candidate.detectors}),
                "repeatGroupIds": sorted({candidate.repeat_group for candidate in group if candidate.repeat_group}),
                "reviewStatus": "open",
                "reviewInstruction": "Inspect every member primitive and the full-screen contact sheet; split or merge visual objects semantically before resolving dispositions.",
            }
        )
    return records


def render_review_clusters(image: Image.Image, clusters: Sequence[dict[str, Any]], output: Path) -> None:
    overlay = image.convert("RGBA")
    draw = ImageDraw.Draw(overlay, "RGBA")
    font = ImageFont.load_default()
    cards: list[tuple[dict[str, Any], Image.Image]] = []
    width, height = image.size
    cluster_crop_dir = output / "review-cluster-crops"
    cluster_crop_dir.mkdir(parents=True, exist_ok=True)
    for cluster in clusters:
        x, y, box_width, box_height = cluster["pixelBounds"]
        color = (255, 30, 35, 230) if cluster["highMemberCount"] else (255, 150, 20, 220)
        draw.rectangle((x, y, x + box_width - 1, y + box_height - 1), outline=color, width=5)
        label = f"{cluster['id']} {cluster.get('regionId') or 'unassigned'} n={cluster['memberCount']}"
        text_box = draw.textbbox((x, y), label, font=font)
        text_width = text_box[2] - text_box[0]
        text_height = text_box[3] - text_box[1]
        label_y = max(0, y - text_height - 3)
        draw.rectangle((x, label_y, min(width - 1, x + text_width + 4), label_y + text_height + 3), fill=(0, 0, 0, 205))
        draw.text((x + 2, label_y + 1), label, fill=(255, 255, 255, 255), font=font)
        margin = max(12, min(40, int(round(max(box_width, box_height) * 0.06))))
        crop = image.crop((max(0, x - margin), max(0, y - margin), min(width, x + box_width + margin), min(height, y + box_height + margin)))
        crop.save(cluster_crop_dir / f"{cluster['id']}.png")
        cards.append((cluster, crop))
    overlay.convert("RGB").save(output / "review-cluster-overlay.png")

    if not cards:
        return
    cell_width, cell_height, columns = 400, 280, 3
    rows = math.ceil(len(cards) / columns)
    sheet = Image.new("RGB", (cell_width * columns, cell_height * rows), "#20242c")
    sheet_draw = ImageDraw.Draw(sheet)
    for index, (cluster, crop) in enumerate(cards):
        column = index % columns
        row = index // columns
        preview = crop.copy()
        preview.thumbnail((cell_width - 16, cell_height - 48), Image.Resampling.LANCZOS)
        left = column * cell_width + (cell_width - preview.width) // 2
        top = row * cell_height + 38 + (cell_height - 48 - preview.height) // 2
        sheet.paste(preview, (left, top))
        sheet_draw.text(
            (column * cell_width + 8, row * cell_height + 8),
            f"{cluster['id']} {cluster.get('regionId') or 'unassigned'} members={cluster['memberCount']}",
            fill="white",
            font=font,
        )
    sheet.save(output / "review-cluster-contact-sheet.png")


def reconcile_independent_review(
    review_path: Path,
    candidates: Sequence[Candidate],
    clusters: Sequence[dict[str, Any]],
    source_sha256: str,
    output: Path,
) -> dict[str, Any]:
    """Bind a human-first inventory to detector primitives without adjudicating it."""

    review = load_json(review_path)
    review_source = review.get("inputs", {}).get("referenceImage", {})
    review_source_sha = review_source.get("sha256") if isinstance(review_source, dict) else None
    if review_source_sha and review_source_sha != source_sha256:
        raise ValueError(
            f"Independent review source hash {review_source_sha} does not match scanned raster {source_sha256}."
        )
    items: list[dict[str, Any]] = []
    for region in review.get("regions", []):
        if not isinstance(region, dict):
            continue
        for item in region.get("items", []):
            if isinstance(item, dict):
                items.append({**item, "reviewRegionId": region.get("id")})

    records: list[dict[str, Any]] = []
    for item in items:
        bounds = item.get("pixelBounds")
        if not isinstance(bounds, list) or len(bounds) != 4:
            continue
        item_area = bbox_area(bounds)
        candidate_matches: list[dict[str, Any]] = []
        for candidate in candidates:
            intersection = bbox_intersection(bounds, candidate.bbox)
            if not intersection:
                continue
            item_coverage = intersection / max(1, item_area)
            candidate_coverage = intersection / max(1, bbox_area(candidate.bbox))
            iou = bbox_iou(bounds, candidate.bbox)
            score = 0.45 * item_coverage + 0.35 * candidate_coverage + 0.20 * iou
            if item_coverage < 0.05 and candidate_coverage < 0.50 and iou < 0.08:
                continue
            candidate_matches.append(
                {
                    "candidateId": candidate.candidate_id,
                    "candidateDisposition": candidate.disposition.get("status"),
                    "candidateSalience": salience_level(candidate.score),
                    "geometryScore": round(score, 6),
                    "itemCoverage": round(item_coverage, 6),
                    "candidateCoverage": round(candidate_coverage, 6),
                    "iou": round(iou, 6),
                }
            )
        candidate_matches.sort(key=lambda match: match["geometryScore"], reverse=True)
        candidate_matches = candidate_matches[:12]
        matched_candidate_ids = {match["candidateId"] for match in candidate_matches if match["geometryScore"] >= 0.18}
        matching_clusters = [
            cluster["id"]
            for cluster in clusters
            if matched_candidate_ids.intersection(cluster.get("memberIds", []))
        ]
        detected = any(
            match["geometryScore"] >= 0.18
            and (match["itemCoverage"] >= 0.10 or match["candidateCoverage"] >= 0.72)
            for match in candidate_matches
        )
        records.append(
            {
                "reviewItemId": item.get("id"),
                "reviewRegionId": item.get("reviewRegionId"),
                "description": item.get("description"),
                "pixelBounds": bounds,
                "salience": item.get("salience"),
                "reviewDisposition": item.get("disposition"),
                "detectorCaught": detected,
                "matchingClusterIds": matching_clusters,
                "candidateMatches": candidate_matches,
            }
        )

    medium_high_gaps = [
        record
        for record in records
        if record.get("salience") in {"medium", "high"}
        and record.get("reviewDisposition") in {"missing", "uncertain"}
    ]
    caught_gaps = [record for record in medium_high_gaps if record["detectorCaught"]]
    reconciliation = {
        "version": "0.1",
        "authoritative": False,
        "purpose": "Geometry-only reconciliation of an independent human-first inventory with automated raster proposals; it does not amend either input.",
        "inputs": {
            "sourceSha256": source_sha256,
            "independentReview": {"path": str(review_path.resolve()), "sha256": sha256_file(review_path)},
        },
        "summary": {
            "reviewItemCount": len(records),
            "mediumHighMissingOrUncertainCount": len(medium_high_gaps),
            "detectorCaughtMediumHighGapCount": len(caught_gaps),
            "detectorRecallMediumHighGaps": round(len(caught_gaps) / max(1, len(medium_high_gaps)), 6),
        },
        "mediumHighMissingOrUncertain": medium_high_gaps,
        "allItems": records,
    }
    write_json(output / "review-reconciliation.json", reconciliation)
    return reconciliation


def color_for_candidate(candidate: Candidate) -> tuple[int, int, int, int]:
    status = candidate.disposition.get("status")
    if status == "mapped":
        return 35, 210, 90, 210
    if status == "excluded":
        return 40, 150, 245, 210
    if status == "rejected-noise":
        return 140, 140, 140, 150
    if salience_level(candidate.score) == "high":
        return 255, 40, 35, 230
    if salience_level(candidate.score) == "medium":
        return 255, 150, 25, 220
    return 240, 220, 40, 140


def render_masks_and_overlays(image: Image.Image, candidates: Sequence[Candidate], output: Path) -> None:
    width, height = image.size
    union = np.zeros((height, width), dtype=np.uint8)
    coverage = np.zeros((height, width, 3), dtype=np.uint8)
    for candidate in candidates:
        if salience_level(candidate.score) == "low":
            continue
        status = candidate.disposition.get("status")
        color = (35, 210, 90) if status == "mapped" else (40, 150, 245) if status == "excluded" else (150, 150, 150) if status == "rejected-noise" else (245, 55, 45)
        for row, start, length in candidate.runs:
            if 0 <= row < height:
                left = max(0, start)
                right = min(width, start + length)
                union[row, left:right] = 255
                coverage[row, left:right] = color
    Image.fromarray(union, mode="L").save(output / "candidate-mask.png")
    Image.fromarray(coverage, mode="RGB").save(output / "coverage-mask.png")

    overlay = image.convert("RGBA")
    draw = ImageDraw.Draw(overlay, "RGBA")
    font = ImageFont.load_default()
    uncovered = image.convert("RGBA")
    uncovered_draw = ImageDraw.Draw(uncovered, "RGBA")
    for candidate in candidates:
        if salience_level(candidate.score) == "low":
            continue
        x, y, box_width, box_height = candidate.bbox
        color = color_for_candidate(candidate)
        draw.rectangle((x, y, x + box_width - 1, y + box_height - 1), outline=color, width=3)
        label = f"{candidate.candidate_id} {salience_level(candidate.score)[0].upper()} {candidate.disposition.get('status')}"
        text_box = draw.textbbox((x, y), label, font=font)
        text_width = text_box[2] - text_box[0]
        text_height = text_box[3] - text_box[1]
        label_y = max(0, y - text_height - 3)
        draw.rectangle((x, label_y, min(width - 1, x + text_width + 4), label_y + text_height + 3), fill=(0, 0, 0, 190))
        draw.text((x + 2, label_y + 1), label, fill=(255, 255, 255, 255), font=font)
        if candidate.disposition.get("status") == "unresolved":
            uncovered_draw.rectangle((x, y, x + box_width - 1, y + box_height - 1), outline=color, fill=(255, 20, 20, 38), width=4)
    overlay.convert("RGB").save(output / "inventory-overlay.png")
    uncovered.convert("RGB").save(output / "uncovered-overlay.png")


def make_candidate_crops(image: Image.Image, candidates: Sequence[Candidate], output: Path) -> None:
    crop_dir = output / "crops"
    crop_dir.mkdir(parents=True, exist_ok=True)
    width, height = image.size
    cards: list[tuple[Candidate, Image.Image]] = []
    for candidate in candidates:
        if salience_level(candidate.score) == "low":
            continue
        x, y, box_width, box_height = candidate.bbox
        margin = max(8, min(32, int(round(max(box_width, box_height) * 0.12))))
        crop_box = (max(0, x - margin), max(0, y - margin), min(width, x + box_width + margin), min(height, y + box_height + margin))
        crop = image.crop(crop_box)
        crop.save(crop_dir / f"{candidate.candidate_id}.png")
        cards.append((candidate, crop))

    if not cards:
        return
    cell_width, cell_height, columns = 320, 230, 4
    rows = math.ceil(len(cards) / columns)
    sheet = Image.new("RGB", (cell_width * columns, cell_height * rows), "#20242c")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for index, (candidate, crop) in enumerate(cards):
        column = index % columns
        row = index // columns
        available = (cell_width - 16, cell_height - 44)
        preview = crop.copy()
        preview.thumbnail(available, Image.Resampling.LANCZOS)
        left = column * cell_width + (cell_width - preview.width) // 2
        top = row * cell_height + 30 + (available[1] - preview.height) // 2
        sheet.paste(preview, (left, top))
        label = f"{candidate.candidate_id} {salience_level(candidate.score)} {candidate.disposition.get('status')}"
        draw.text((column * cell_width + 8, row * cell_height + 8), label, fill="white", font=font)
    sheet.save(output / "candidate-contact-sheet.png")


def make_fullscan_contact_sheet(image: Image.Image, output: Path, *, tile_size: int, overlap: int) -> dict[str, Any]:
    width, height = image.size
    step = max(1, tile_size - overlap)
    x_positions = list(range(0, max(1, width - tile_size + 1), step))
    y_positions = list(range(0, max(1, height - tile_size + 1), step))
    if not x_positions or x_positions[-1] != max(0, width - tile_size):
        x_positions.append(max(0, width - tile_size))
    if not y_positions or y_positions[-1] != max(0, height - tile_size):
        y_positions.append(max(0, height - tile_size))
    x_positions = sorted(set(x_positions))
    y_positions = sorted(set(y_positions))
    thumb = 192
    label_height = 20
    columns = min(6, max(1, len(x_positions)))
    tiles: list[dict[str, Any]] = []
    total = len(x_positions) * len(y_positions)
    rows = math.ceil(total / columns)
    sheet = Image.new("RGB", (columns * thumb, rows * (thumb + label_height)), "#171a20")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    index = 0
    for y in y_positions:
        for x in x_positions:
            crop = image.crop((x, y, min(width, x + tile_size), min(height, y + tile_size)))
            crop.thumbnail((thumb, thumb), Image.Resampling.LANCZOS)
            column = index % columns
            row = index // columns
            left = column * thumb + (thumb - crop.width) // 2
            top = row * (thumb + label_height) + label_height + (thumb - crop.height) // 2
            sheet.paste(crop, (left, top))
            tile_id = f"tile.{index + 1:03d}"
            draw.text((column * thumb + 4, row * (thumb + label_height) + 4), f"{tile_id} x{x} y{y}", fill="white", font=font)
            tiles.append({"id": tile_id, "pixelBounds": [x, y, min(tile_size, width - x), min(tile_size, height - y)]})
            index += 1
    sheet.save(output / "fullscan-contact-sheet.png")
    return {"tileSize": tile_size, "overlap": overlap, "tiles": tiles}


def run_scan(
    image_path: Path,
    requirement_path: Path | None,
    layout_inputs: Sequence[Path],
    output: Path,
    *,
    tile_size: int = 256,
    tile_overlap: int = 32,
    independent_review_path: Path | None = None,
    cache_dir: Path | None = None,
) -> dict[str, Any]:
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"Output directory must be absent or empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    source_bytes = image_path.read_bytes()
    with Image.open(io.BytesIO(source_bytes)) as source_handle:
        source_media_type = Image.MIME.get(source_handle.format, "image/unknown")
        image = ImageOps.exif_transpose(source_handle).convert("RGB")
    rgb = np.asarray(image)
    height, width = rgb.shape[:2]
    source_sha256 = sha256_bytes(source_bytes)
    requirement = load_json(requirement_path) if requirement_path else None
    resolved_layout_paths = layout_paths(layout_inputs)
    layouts = [(path, load_json(path)) for path in resolved_layout_paths]
    windows = requirement_regions(requirement, width, height)

    cache_identity = detection_cache_identity(rgb, windows)
    cache_key = sha256_bytes(canonical_json_bytes(cache_identity))
    cache_path = cache_dir.resolve() / f"{cache_key}.json.gz" if cache_dir is not None else None
    cache_status = "disabled"
    cache_entry_bytes = 0
    cache_error: str | None = None
    candidates: list[Candidate] | None = None
    if cache_path is not None and cache_path.is_file():
        try:
            loaded_candidates = read_detection_cache(cache_path, cache_key, cache_identity)
            loaded_entry_bytes = cache_path.stat().st_size
            candidates = loaded_candidates
            cache_status = "hit"
            cache_entry_bytes = loaded_entry_bytes
        except (OSError, ValueError, TypeError, KeyError, EOFError, UnicodeDecodeError, zlib.error) as error:
            candidates = None
            cache_status = "invalid-recomputed"
            cache_error = type(error).__name__
    elif cache_path is not None:
        cache_status = "miss"

    if candidates is None:
        _, saturation_map, _ = rgb_to_hsv(rgb)
        raw_candidates: list[Candidate] = []
        raw_candidates.extend(add_chromatic_candidates(rgb))
        raw_candidates.extend(add_contrast_candidates(rgb))
        raw_candidates.extend(add_quantized_candidates(rgb))
        raw_candidates.extend(add_band_candidates(rgb, windows))
        for candidate in raw_candidates:
            candidate_statistics(candidate, rgb, saturation_map)
        raw_candidates = [candidate for candidate in raw_candidates if candidate.pixel_area >= 12 and candidate.bbox[2] >= 2 and candidate.bbox[3] >= 2]
        candidates = deduplicate_candidates(raw_candidates)
        candidates = suppress_band_fragments(candidates)
        assign_regions(candidates, windows)
        assign_repeat_groups(candidates, width, height)
        candidates = suppress_low_information_candidates(candidates, width, height)
        # Keep all medium/high proposals plus the strongest low proposals for auditability.
        high_and_medium = [candidate for candidate in candidates if salience_level(candidate.score) != "low"]
        low = sorted((candidate for candidate in candidates if salience_level(candidate.score) == "low"), key=lambda item: item.score, reverse=True)[:60]
        candidates = high_and_medium + low
        candidates.sort(key=lambda candidate: (candidate.bbox[1], candidate.bbox[0], -candidate.pixel_area, candidate.detector))
        for index, candidate in enumerate(candidates, 1):
            candidate.candidate_id = f"vc.{index:04d}"
        if cache_path is not None:
            try:
                cache_entry_bytes = write_detection_cache(cache_path, cache_key, cache_identity, candidates)
            except OSError as error:
                cache_status = "write-failed-bypassed"
                cache_error = type(error).__name__

    visuals = declared_visuals(requirement, layouts, width, height)
    exclusions = exclusion_rects(requirement, width, height)
    assign_dispositions(candidates, visuals, exclusions, width, height)

    source_raster = {
        "version": "0.1",
        "tool": {"name": "visual_coverage_scan", "version": TOOL_VERSION},
        "source": {
            "path": str(image_path.resolve()),
            "sha256": source_sha256,
            "mediaType": source_media_type,
            "pixelSize": [width, height],
            "decodedMode": "RGB",
            "orientationApplied": True,
        },
        "analysis": {
            "detectors": ["chromatic-component", "local-contrast", "quantized-color", "horizontal-band"],
            "salienceThresholds": {"medium": MEDIUM_SCORE, "high": HIGH_SCORE},
            "hardCodedSemanticClasses": [],
            "hardCodedSourceCoordinates": [],
        },
    }
    write_json(output / "source-raster.json", source_raster)

    records = [candidate_record(candidate, width, height) for candidate in candidates]
    visual_primitives = {
        "version": "0.1",
        "sourceSha256": source_raster["source"]["sha256"],
        "candidateCount": len(records),
        "detectorCandidateCounts": dict(sorted(Counter(detector for candidate in candidates for detector in candidate.detectors).items())),
        "candidates": records,
    }
    write_json(output / "visual-primitives.json", visual_primitives)

    inventory = {
        "version": "0.1",
        "draft": True,
        "authoritative": False,
        "sourceSha256": source_raster["source"]["sha256"],
        "requirement": {
            "path": str(requirement_path.resolve()) if requirement_path else None,
            "sha256": sha256_file(requirement_path) if requirement_path else None,
            "staticVisualCoverageRequired": bool(requirement and requirement.get("analysisPolicy", {}).get("staticVisualCoverageRequired") is True),
        },
        "layouts": [{"path": str(path), "sha256": sha256_file(path)} for path in resolved_layout_paths],
        "declaredVisualCount": len(visuals),
        "declaredVisuals": [{**visual, "bbox": list(visual["bbox"])} for visual in visuals],
        "exclusions": [{**exclusion, "bbox": list(exclusion["bbox"])} for exclusion in exclusions],
        "candidates": records,
    }
    write_json(output / "inventory-draft.json", inventory)

    unresolved = [candidate for candidate in candidates if candidate.disposition.get("status") == "unresolved"]
    unresolved_medium_high = [candidate for candidate in unresolved if salience_level(candidate.score) in {"medium", "high"}]
    cluster_diagnostics: dict[str, int] = {}
    review_clusters = uncovered_review_clusters(candidates, width, height, diagnostics=cluster_diagnostics)
    nonexcluded_medium_high = [candidate for candidate in candidates if salience_level(candidate.score) in {"medium", "high"} and candidate.disposition.get("status") != "excluded"]
    terminal = [candidate for candidate in candidates if candidate.disposition.get("status") in TERMINAL_DISPOSITIONS]
    mapped = [candidate for candidate in nonexcluded_medium_high if candidate.disposition.get("status") in {"mapped", "merged"}]
    weighted_total = sum(candidate.pixel_area * max(candidate.local_contrast, 0.03) * candidate.repeat_count for candidate in nonexcluded_medium_high)
    weighted_uncovered = sum(candidate.pixel_area * max(candidate.local_contrast, 0.03) * candidate.repeat_count for candidate in unresolved_medium_high)
    report = {
        "version": "0.1",
        "status": "review-required" if unresolved_medium_high else "no-medium-high-gaps-detected",
        "sourceSha256": source_raster["source"]["sha256"],
        "summary": {
            "candidateCount": len(candidates),
            "highCount": sum(salience_level(candidate.score) == "high" for candidate in candidates),
            "mediumCount": sum(salience_level(candidate.score) == "medium" for candidate in candidates),
            "mappedCount": sum(candidate.disposition.get("status") == "mapped" for candidate in candidates),
            "excludedCount": sum(candidate.disposition.get("status") == "excluded" for candidate in candidates),
            "unresolvedCount": len(unresolved),
            "uncoveredHighOrMediumSalienceCount": len(unresolved_medium_high),
            "openReviewClusterCount": len(review_clusters),
        },
        "gate": {
            "dispositionCompleteness": round(len(terminal) / max(1, len(candidates)), 6),
            "nonExcludedMappingRecallMediumHigh": round(len(mapped) / max(1, len(nonexcluded_medium_high)), 6),
            "weightedUncoveredRatioMediumHigh": round(weighted_uncovered / max(weighted_total, 1e-9), 6),
            "uncoveredHighOrMediumSalienceCount": len(unresolved_medium_high),
            "passesDraftGate": len(unresolved_medium_high) == 0,
            "note": "A draft cannot pass final static-visual coverage until independent review resolves every medium/high candidate and confirms proxy exclusions.",
        },
        "uncoveredCandidates": [
            {
                "id": candidate.candidate_id,
                "salience": salience_level(candidate.score),
                "score": round(candidate.score, 6),
                "pixelBounds": list(candidate.bbox),
                "regionId": candidate.region_id,
                "detectors": sorted(candidate.detectors),
                "repeatGroupId": candidate.repeat_group,
                "observedInstanceCount": candidate.repeat_count,
                "crop": f"crops/{candidate.candidate_id}.png",
            }
            for candidate in sorted(unresolved_medium_high, key=lambda item: item.score, reverse=True)
        ],
        "reviewClusters": review_clusters,
        "knownLimitations": [
            "This is a proposal generator, not semantic recognition or proof that every proposal is a required Widget.",
            "Text, detailed illustration, shadows, and gradients can create false-positive connected components.",
            "Requirement elements without geometry cannot directly map candidates; Layout geometry is used when supplied.",
            "Collection-entry projection uses designer preview counts and vertical spacing; runtime-only states still require human review.",
            "An out-of-scope element with no own bounds uses its owning region as a proxy exclusion and remains review-required.",
        ],
    }
    write_json(output / "report.json", report)
    render_masks_and_overlays(image, candidates, output)
    make_candidate_crops(image, candidates, output)
    render_review_clusters(image, review_clusters, output)
    if independent_review_path:
        reconciliation = reconcile_independent_review(
            independent_review_path,
            candidates,
            review_clusters,
            source_raster["source"]["sha256"],
            output,
        )
        report["independentReviewReconciliation"] = {
            "artifactPath": "review-reconciliation.json",
            **reconciliation["summary"],
        }
        write_json(output / "report.json", report)
    contact_sheet = make_fullscan_contact_sheet(image, output, tile_size=tile_size, overlap=tile_overlap)
    write_json(output / "fullscan-contact-sheet.json", contact_sheet)
    write_json(
        output / "cache-telemetry.json",
        {
            "version": "0.1",
            "authoritative": False,
            "cache": {
                "enabled": cache_path is not None,
                "status": cache_status,
                "key": cache_key,
                "entryFile": cache_path.name if cache_path is not None else None,
                "entryBytes": cache_entry_bytes,
                "fallbackReason": cache_error,
            },
            "reuse": {
                "detectorStageReused": cache_status == "hit",
                "candidateCount": len(candidates),
            },
            "reviewClustering": {
                "algorithm": "lossless-horizontal-sweep-0.1",
                **cluster_diagnostics,
            },
            "equivalenceBoundary": "All authoritative scan JSON and rendered evidence are produced by the same post-cache code path; this telemetry file is the only cache-status-dependent output.",
        },
    )
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True, help="Source UI raster (PNG/JPEG/etc.).")
    parser.add_argument("--requirement", type=Path, help="Optional UIRequirementSpec JSON used for regions, declared elements, and exclusions.")
    parser.add_argument(
        "--layouts",
        type=Path,
        nargs="*",
        default=[],
        help="Optional UILayoutSpec JSON files or directories. Child entry layouts are projected through screen collection hosts.",
    )
    parser.add_argument(
        "--independent-review",
        type=Path,
        help="Optional independent visual inventory JSON to reconcile geometrically after blind scanning.",
    )
    parser.add_argument("--output", type=Path, required=True, help="New or empty output directory for the evidence packet.")
    parser.add_argument(
        "--cache-dir",
        type=Path,
        help="Optional trusted, request-local content-addressed detector cache. Corrupt or mismatched entries are ignored and rebuilt.",
    )
    parser.add_argument("--tile-size", type=int, default=256, help="Full-screen contact-sheet source tile size (default: 256).")
    parser.add_argument("--tile-overlap", type=int, default=32, help="Full-screen contact-sheet overlap in pixels (default: 32).")
    parser.add_argument(
        "--print-report",
        choices=("summary", "full", "none"),
        default="summary",
        help="Console output detail; complete report.json is always written (default: summary).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.image.is_file():
        parser.error(f"--image does not exist: {args.image}")
    if args.requirement and not args.requirement.is_file():
        parser.error(f"--requirement does not exist: {args.requirement}")
    if args.independent_review and not args.independent_review.is_file():
        parser.error(f"--independent-review does not exist: {args.independent_review}")
    if args.tile_size < 64:
        parser.error("--tile-size must be at least 64")
    if args.tile_overlap < 0 or args.tile_overlap >= args.tile_size:
        parser.error("--tile-overlap must be >= 0 and smaller than --tile-size")
    try:
        report = run_scan(
            args.image,
            args.requirement,
            args.layouts,
            args.output,
            tile_size=args.tile_size,
            tile_overlap=args.tile_overlap,
            independent_review_path=args.independent_review,
            cache_dir=args.cache_dir,
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"valid": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    if args.print_report == "full":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif args.print_report == "summary":
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "summary": report["summary"],
                    "gate": report["gate"],
                    "report": str((args.output / "report.json").resolve()),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
