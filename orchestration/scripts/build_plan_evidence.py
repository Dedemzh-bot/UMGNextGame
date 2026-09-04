#!/usr/bin/env python3
"""Generate or validate the fail-closed pre-mutation build-plan evidence.

The artifact root is a single immutable run directory.  This program intentionally
accepts no path overrides for its three authorities or its output: allowing those
names to drift would make the workflow manifest and the evidence disagree about
what was authorized.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


SCHEMA_VERSION = "0.1"
REQUIREMENT_PATH = "ui-requirement.json"
ACCEPTED_VIEW_PATH = "accepted-build-view.json"
PLANNED_BUNDLE_PATH = "ui-build-bundle.planned.json"
EVIDENCE_PATH = "status/ui-build-plan.pre-mutation-valid.json"
LAYOUT_PREFIX = "layouts/"
PLAN_PREFIX = "plans/"
DEFAULT_PLUGIN_ROOT = Path(__file__).resolve().parents[2] / "plugins" / "nextgame-ui"
ASSET_ID_RE = re.compile(r"^[a-z][a-z0-9.-]{2,95}$")
GAME_ASSET_PATH_RE = re.compile(r"^/Game/UI/(?:AIPrototype(?:/.*)?|UMG/.+)$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

CHECKS = {
    "requirementHashBound": True,
    "fullRequirementValid": True,
    "acceptedViewBindsRequirement": True,
    "acceptedViewValid": True,
    "plannedBundleBindsRequirement": True,
    "plannedBundleValid": True,
    "requirementCoverageValid": True,
    "assetCoverageExact": True,
    "assetIdsUnique": True,
    "assetOrderPreserved": True,
    "layoutCoverageExact": True,
    "layoutContractsValid": True,
    "layoutPathsContained": True,
    "layoutHashesValid": True,
    "planCoverageExact": True,
    "nativePlansDeterministic": True,
    "planPathsContained": True,
    "planHashesValid": True,
}


class EvidenceError(ValueError):
    """A deterministic contract violation."""


def _reject_constant(value: str) -> None:
    raise EvidenceError(f"JSON contains forbidden non-finite number {value!r}")


def _closed_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError(f"JSON contains duplicate object key {key!r}")
        result[key] = value
    return result


def _parse_json(raw: bytes, *, label: str) -> Any:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise EvidenceError(f"{label} is not UTF-8: {error}") from error
    try:
        return json.loads(
            text,
            object_pairs_hook=_closed_object,
            parse_constant=_reject_constant,
        )
    except EvidenceError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        raise EvidenceError(f"{label} is not valid strict JSON: {error}") from error


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return _sha256(encoded)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _validate_posix_relative_path(raw_path: Any, *, label: str, prefix: str | None = None) -> tuple[str, ...]:
    if not isinstance(raw_path, str) or not raw_path:
        raise EvidenceError(f"{label} must be a nonempty POSIX relative path")
    if "\\" in raw_path or "\x00" in raw_path:
        raise EvidenceError(f"{label} must use POSIX separators and contain no NUL")
    if raw_path.startswith("/") or re.match(r"^[A-Za-z]:", raw_path):
        raise EvidenceError(f"{label} must be relative to the artifact root")
    raw_parts = raw_path.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise EvidenceError(f"{label} contains an empty, current, or parent segment")
    parsed = PurePosixPath(raw_path)
    if parsed.is_absolute() or tuple(parsed.parts) != tuple(raw_parts):
        raise EvidenceError(f"{label} is not a normalized POSIX relative path")
    if prefix is not None and not raw_path.startswith(prefix):
        raise EvidenceError(f"{label} must be below {prefix!r}")
    return tuple(raw_parts)


def _resolve_existing_file(
    artifact_root: Path,
    raw_path: Any,
    *,
    label: str,
    prefix: str | None = None,
) -> Path:
    parts = _validate_posix_relative_path(raw_path, label=label, prefix=prefix)
    lexical_path = artifact_root.joinpath(*parts)
    try:
        resolved = lexical_path.resolve(strict=True)
    except OSError as error:
        raise EvidenceError(f"{label} cannot be resolved: {error}") from error
    if not _is_relative_to(resolved, artifact_root):
        raise EvidenceError(f"{label} escapes the artifact root (including through a symlink)")
    if not resolved.is_file():
        raise EvidenceError(f"{label} is not a regular file")
    return resolved


def _load_bound_json(
    artifact_root: Path,
    raw_path: str,
    *,
    label: str,
    prefix: str | None = None,
) -> tuple[Path, bytes, Any]:
    path = _resolve_existing_file(artifact_root, raw_path, label=label, prefix=prefix)
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise EvidenceError(f"{label} cannot be read: {error}") from error
    return path, raw, _parse_json(raw, label=label)


def _require_object(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceError(f"{label} must be a JSON object")
    return value


def _require_sha256(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise EvidenceError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _validate_generated_at(value: Any) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise EvidenceError("generatedAtUtc must be an RFC 3339 UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise EvidenceError(f"generatedAtUtc is invalid: {error}") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise EvidenceError("generatedAtUtc must identify UTC")
    return value


def _default_generated_at() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _ordered_assets(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    assets_value = bundle.get("assets")
    execution = bundle.get("execution")
    if not isinstance(assets_value, list) or not assets_value:
        raise EvidenceError("planned Bundle assets must be a nonempty array")
    if not isinstance(execution, dict):
        raise EvidenceError("planned Bundle execution must be an object")
    order = execution.get("buildOrderAssetIds")
    if not isinstance(order, list) or not order or any(not isinstance(item, str) for item in order):
        raise EvidenceError("execution.buildOrderAssetIds must be a nonempty string array")
    if len(order) != len(set(order)):
        raise EvidenceError("execution.buildOrderAssetIds contains duplicate asset IDs")

    asset_map: dict[str, dict[str, Any]] = {}
    build_orders: dict[str, int] = {}
    for index, value in enumerate(assets_value):
        asset = _require_object(value, label=f"assets[{index}]")
        asset_id = asset.get("id")
        if not isinstance(asset_id, str) or ASSET_ID_RE.fullmatch(asset_id) is None:
            raise EvidenceError(f"assets[{index}].id is not a canonical asset ID")
        if asset_id in asset_map:
            raise EvidenceError(f"planned Bundle contains duplicate asset ID {asset_id!r}")
        build_order = asset.get("buildOrder")
        if isinstance(build_order, bool) or not isinstance(build_order, int) or build_order < 0:
            raise EvidenceError(f"asset {asset_id!r} has invalid buildOrder")
        asset_map[asset_id] = asset
        build_orders[asset_id] = build_order

    if set(order) != set(asset_map) or len(order) != len(asset_map):
        missing = sorted(set(asset_map) - set(order))
        extra = sorted(set(order) - set(asset_map))
        raise EvidenceError(
            "execution.buildOrderAssetIds does not exactly cover assets "
            f"(missing={missing}, extra={extra})"
        )
    values = list(build_orders.values())
    if len(values) != len(set(values)) or sorted(values) != list(range(len(values))):
        raise EvidenceError("asset buildOrder values must be unique and contiguous from zero")
    expected_order = [asset_id for asset_id, _ in sorted(build_orders.items(), key=lambda item: item[1])]
    if order != expected_order:
        raise EvidenceError("execution.buildOrderAssetIds does not preserve asset buildOrder")
    return [asset_map[asset_id] for asset_id in order]


def _validate_authority_bindings(
    requirement_raw: bytes,
    view: dict[str, Any],
    bundle: dict[str, Any],
) -> str:
    requirement_sha = _sha256(requirement_raw)

    view_bindings = view.get("bindings")
    if not isinstance(view_bindings, dict):
        raise EvidenceError("Accepted Build View bindings must be an object")
    view_requirement_sha = _require_sha256(
        view_bindings.get("requirementFileSha256"),
        label="Accepted Build View bindings.requirementFileSha256",
    )
    if view_requirement_sha != requirement_sha:
        raise EvidenceError("Accepted Build View does not bind the complete Requirement file SHA-256")
    coverage = view.get("coverage")
    if (
        view.get("viewKind") != "nextgame-ui-accepted-build-view"
        or view.get("mode") != "projected"
        or view.get("buildAllowed") is not True
        or not isinstance(coverage, dict)
        or coverage.get("status") != "complete"
        or coverage.get("missingCanonicalIds") != []
    ):
        raise EvidenceError("Accepted Build View is not a complete build-allowed projected View")

    requirement_link = bundle.get("requirement")
    if not isinstance(requirement_link, dict):
        raise EvidenceError("planned Bundle requirement binding must be an object")
    bundle_requirement_sha = _require_sha256(
        requirement_link.get("sha256"),
        label="planned Bundle requirement.sha256",
    )
    if bundle_requirement_sha != requirement_sha:
        raise EvidenceError("planned Bundle does not bind the complete Requirement file SHA-256")
    linked_path = requirement_link.get("path")
    if not isinstance(linked_path, str):
        raise EvidenceError("planned Bundle requirement.path must be a POSIX relative path")
    return requirement_sha


def _validate_bundle_requirement_path(
    artifact_root: Path,
    bundle_path: Path,
    requirement_path: Path,
    bundle: dict[str, Any],
) -> None:
    requirement_link = _require_object(bundle.get("requirement"), label="planned Bundle requirement")
    raw_link = requirement_link.get("path")
    parts = _validate_posix_relative_path(raw_link, label="planned Bundle requirement.path")
    lexical_path = bundle_path.parent.joinpath(*parts)
    try:
        resolved = lexical_path.resolve(strict=True)
    except OSError as error:
        raise EvidenceError(f"planned Bundle requirement.path cannot be resolved: {error}") from error
    if not _is_relative_to(resolved, artifact_root):
        raise EvidenceError("planned Bundle requirement.path escapes the artifact root")
    if resolved != requirement_path:
        raise EvidenceError("planned Bundle requirement.path does not identify ui-requirement.json")


def _artifact_records(
    artifact_root: Path,
    ordered_assets: list[dict[str, Any]],
    *,
    plugin_root: Path,
    requirement_sha256: str,
    accepted_view_sha256: str,
    planned_bundle_sha256: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    layouts: list[dict[str, Any]] = []
    plans: list[dict[str, Any]] = []
    seen_layout_paths: set[str] = set()
    seen_plan_paths: set[str] = set()

    for asset in ordered_assets:
        asset_id = asset["id"]
        asset_path = asset.get("assetPath")
        if not isinstance(asset_path, str) or GAME_ASSET_PATH_RE.fullmatch(asset_path) is None:
            raise EvidenceError(f"asset {asset_id!r} has invalid assetPath")
        representation = asset.get("representationKind", "layout-spec")
        if representation == "reuse-only":
            if asset.get("layoutSpecPath") is not None or asset.get("layoutSpecSha256") is not None:
                raise EvidenceError(f"reuse-only asset {asset_id!r} must have null layout path and digest")
            layouts.append(
                {
                    "assetId": asset_id,
                    "representationKind": "reuse-only",
                    "path": None,
                    "sha256": None,
                    "skipReason": "reuse-only",
                }
            )
            plans.append(
                {
                    "assetId": asset_id,
                    "representationKind": "reuse-only",
                    "path": None,
                    "sha256": None,
                    "sourceSpec": None,
                    "assetPath": asset_path,
                    "layoutSpecSha256": None,
                    "requirementFileSha256": requirement_sha256,
                    "acceptedBuildViewFileSha256": accepted_view_sha256,
                    "plannedBundleFileSha256": planned_bundle_sha256,
                    "planCanonicalSha256": None,
                    "stepCount": 0,
                    "skipReason": "reuse-only",
                }
            )
            continue
        if representation != "layout-spec":
            raise EvidenceError(f"asset {asset_id!r} has unsupported representationKind {representation!r}")

        layout_path = asset.get("layoutSpecPath")
        layout_sha = _require_sha256(
            asset.get("layoutSpecSha256"),
            label=f"asset {asset_id!r} layoutSpecSha256",
        )
        if not isinstance(layout_path, str):
            raise EvidenceError(f"layout-spec asset {asset_id!r} must have a layoutSpecPath")
        if layout_path in seen_layout_paths:
            raise EvidenceError(f"layout sidecar path {layout_path!r} is enumerated more than once")
        seen_layout_paths.add(layout_path)
        _, layout_raw, _ = _load_bound_json(
            artifact_root,
            layout_path,
            label=f"layout sidecar for {asset_id}",
            prefix=LAYOUT_PREFIX,
        )
        actual_layout_sha = _sha256(layout_raw)
        if layout_sha != actual_layout_sha:
            raise EvidenceError(
                f"layout sidecar hash mismatch for {asset_id!r}: "
                f"declared {layout_sha}, actual {actual_layout_sha}"
            )
        layouts.append(
            {
                "assetId": asset_id,
                "representationKind": "layout-spec",
                "path": layout_path,
                "sha256": actual_layout_sha,
                "skipReason": None,
            }
        )

        plan_path = f"{PLAN_PREFIX}{asset_id}.plan.json"
        if plan_path in seen_plan_paths:
            raise EvidenceError(f"plan path {plan_path!r} is enumerated more than once")
        seen_plan_paths.add(plan_path)
        _, plan_raw, plan_value = _load_bound_json(
            artifact_root,
            plan_path,
            label=f"build plan for {asset_id}",
            prefix=PLAN_PREFIX,
        )
        plan = _require_object(plan_value, label=f"build plan for {asset_id}")
        expected_plan = _build_native_plan(
            plugin_root,
            artifact_root,
            layout_path,
        )
        difference = _first_difference(expected_plan, plan)
        if difference is not None:
            raise EvidenceError(
                f"build plan for {asset_id!r} is not the exact deterministic prepare_build.py v0.2 output: "
                f"{difference}"
            )
        if plan.get("version") != "0.2":
            raise EvidenceError(f"build plan for {asset_id!r} must use native plan version 0.2")
        if plan.get("assetPath") != asset_path:
            raise EvidenceError(f"build plan for {asset_id!r} does not target its Bundle assetPath")
        steps = plan.get("steps")
        if not isinstance(steps, list) or not steps:
            raise EvidenceError(f"build plan for {asset_id!r} requires nonempty native steps")
        plans.append(
            {
                "assetId": asset_id,
                "representationKind": "layout-spec",
                "path": plan_path,
                "sha256": _sha256(plan_raw),
                "sourceSpec": layout_path,
                "assetPath": asset_path,
                "layoutSpecSha256": actual_layout_sha,
                "requirementFileSha256": requirement_sha256,
                "acceptedBuildViewFileSha256": accepted_view_sha256,
                "plannedBundleFileSha256": planned_bundle_sha256,
                "planCanonicalSha256": canonical_sha256(plan),
                "stepCount": len(steps),
                "skipReason": None,
            }
        )

    expected_layout_paths = {
        record["path"] for record in layouts if record["path"] is not None
    }
    expected_plan_paths = {
        record["path"] for record in plans if record["path"] is not None
    }
    actual_layout_paths = _inventory_files(artifact_root, LAYOUT_PREFIX)
    actual_plan_paths = _inventory_files(artifact_root, PLAN_PREFIX)
    if actual_layout_paths != expected_layout_paths:
        raise EvidenceError(
            "layouts/ does not exactly match the staged Bundle enumeration "
            f"(missing={sorted(expected_layout_paths - actual_layout_paths)}, "
            f"extra={sorted(actual_layout_paths - expected_layout_paths)})"
        )
    if actual_plan_paths != expected_plan_paths:
        raise EvidenceError(
            "plans/ does not exactly match the deterministic build-order enumeration "
            f"(missing={sorted(expected_plan_paths - actual_plan_paths)}, "
            f"extra={sorted(actual_plan_paths - expected_plan_paths)})"
        )
    return layouts, plans


def _build_native_plan(
    plugin_root: Path,
    artifact_root: Path,
    layout_path: str,
) -> dict[str, Any]:
    script = _validator_script(
        plugin_root,
        "skills/build-nextgame-umg/scripts/prepare_build.py",
    )
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    try:
        completed = subprocess.run(
            [sys.executable, "-B", str(script), layout_path],
            cwd=artifact_root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise EvidenceError(f"native prepare_build.py could not run for {layout_path!r}: {error}") from error
    if completed.returncode != 0:
        details = (completed.stderr or completed.stdout).decode("utf-8", errors="replace").strip()
        raise EvidenceError(
            f"native prepare_build.py rejected {layout_path!r} with exit code {completed.returncode}: "
            f"{details[:2000]}"
        )
    plan = _parse_json(completed.stdout, label=f"native build plan for {layout_path}")
    return _require_object(plan, label=f"native build plan for {layout_path}")


def _inventory_files(artifact_root: Path, prefix: str) -> set[str]:
    directory = artifact_root.joinpath(*PurePosixPath(prefix.rstrip("/")).parts)
    if not directory.exists():
        return set()
    try:
        directory_resolved = directory.resolve(strict=True)
    except OSError as error:
        raise EvidenceError(f"{prefix} cannot be resolved: {error}") from error
    if not _is_relative_to(directory_resolved, artifact_root) or not directory_resolved.is_dir():
        raise EvidenceError(f"{prefix} escapes the artifact root or is not a directory")
    inventory: set[str] = set()
    try:
        descendants = list(directory.rglob("*"))
    except OSError as error:
        raise EvidenceError(f"{prefix} cannot be enumerated: {error}") from error
    for path in descendants:
        relative = path.relative_to(artifact_root).as_posix()
        try:
            resolved = path.resolve(strict=True)
        except OSError as error:
            raise EvidenceError(f"{relative} cannot be resolved: {error}") from error
        if not _is_relative_to(resolved, artifact_root):
            raise EvidenceError(f"{relative} escapes the artifact root through a symlink")
        if path.is_symlink() and resolved.is_dir():
            raise EvidenceError(f"{relative} is a directory symlink; directory aliases are forbidden")
        if resolved.is_file():
            inventory.add(relative)
        elif not resolved.is_dir():
            raise EvidenceError(f"{relative} is neither a regular file nor a directory")
    return inventory


def _validator_script(plugin_root: Path, relative_path: str) -> Path:
    root = plugin_root.resolve(strict=True)
    if not root.is_dir():
        raise EvidenceError("plugin root must be a directory")
    parts = _validate_posix_relative_path(relative_path, label="validator script path")
    script = root.joinpath(*parts).resolve(strict=True)
    if not _is_relative_to(script, root) or not script.is_file():
        raise EvidenceError(f"validator script {relative_path!r} is outside the plugin root or missing")
    return script


def _run_validator(
    plugin_root: Path,
    relative_script: str,
    arguments: list[Path | str],
    *,
    label: str,
    working_directory: Path,
) -> None:
    script = _validator_script(plugin_root, relative_script)
    command = [sys.executable, "-B", str(script), *(str(value) for value in arguments)]
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    try:
        completed = subprocess.run(
            command,
            cwd=working_directory,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise EvidenceError(f"{label} could not run: {error}") from error
    report: Any = None
    if completed.stdout.strip():
        try:
            report = json.loads(completed.stdout)
        except json.JSONDecodeError:
            report = None
    if completed.returncode != 0 or not isinstance(report, dict) or report.get("valid") is not True:
        details = completed.stderr.strip() or completed.stdout.strip()
        if len(details) > 2000:
            details = details[:2000] + "..."
        raise EvidenceError(
            f"{label} failed with exit code {completed.returncode}: {details or 'no valid JSON report'}"
        )


def _validate_semantic_authorities(
    artifact_root: Path,
    plugin_root: Path,
    *,
    requirement_path: Path,
    view_path: Path,
    bundle_path: Path,
    layouts: list[dict[str, Any]],
) -> None:
    analysis_scripts = "skills/analyze-nextgame-ui-requirements/scripts"
    _run_validator(
        plugin_root,
        f"{analysis_scripts}/validate_accepted_build_view.py",
        [view_path, "--requirement", requirement_path],
        label="full Requirement and Accepted Build View validation",
        working_directory=artifact_root,
    )
    _run_validator(
        plugin_root,
        f"{analysis_scripts}/validate_build_bundle.py",
        [
            bundle_path,
            "--requirement",
            requirement_path,
            "--accepted-build-view",
            view_path,
        ],
        label="staged UIBuildBundle validation",
        working_directory=artifact_root,
    )
    _run_validator(
        plugin_root,
        f"{analysis_scripts}/validate_requirement_coverage.py",
        [
            bundle_path,
            "--requirement",
            requirement_path,
            "--accepted-build-view",
            view_path,
        ],
        label="complete Requirement build-coverage validation",
        working_directory=artifact_root,
    )
    for layout in layouts:
        if layout["path"] is None:
            continue
        layout_path = _resolve_existing_file(
            artifact_root,
            layout["path"],
            label=f"layout sidecar for {layout['assetId']}",
            prefix=LAYOUT_PREFIX,
        )
        _run_validator(
            plugin_root,
            "skills/build-nextgame-umg/scripts/validate_layout_spec.py",
            [layout_path],
            label=f"UILayoutSpec 0.2 validation for {layout['assetId']}",
            working_directory=artifact_root,
        )


def build_expected_evidence(
    artifact_root: Path,
    *,
    generated_at_utc: str,
    plugin_root: Path = DEFAULT_PLUGIN_ROOT,
) -> dict[str, Any]:
    root = artifact_root.resolve(strict=True)
    if not root.is_dir():
        raise EvidenceError("artifact root must be a directory")
    generated_at = _validate_generated_at(generated_at_utc)

    requirement_path, requirement_raw, requirement_value = _load_bound_json(
        root, REQUIREMENT_PATH, label="complete Requirement"
    )
    _require_object(requirement_value, label="complete Requirement")
    view_path, view_raw, view_value = _load_bound_json(
        root, ACCEPTED_VIEW_PATH, label="Accepted Build View"
    )
    view = _require_object(view_value, label="Accepted Build View")
    bundle_path, bundle_raw, bundle_value = _load_bound_json(
        root, PLANNED_BUNDLE_PATH, label="planned Bundle"
    )
    bundle = _require_object(bundle_value, label="planned Bundle")

    requirement_sha = _validate_authority_bindings(requirement_raw, view, bundle)
    _validate_bundle_requirement_path(root, bundle_path, requirement_path, bundle)
    ordered_assets = _ordered_assets(bundle)
    view_sha = _sha256(view_raw)
    bundle_sha = _sha256(bundle_raw)
    layouts, plans = _artifact_records(
        root,
        ordered_assets,
        plugin_root=plugin_root,
        requirement_sha256=requirement_sha,
        accepted_view_sha256=view_sha,
        planned_bundle_sha256=bundle_sha,
    )
    _validate_semantic_authorities(
        root,
        plugin_root,
        requirement_path=requirement_path,
        view_path=view_path,
        bundle_path=bundle_path,
        layouts=layouts,
    )

    return {
        "schemaVersion": SCHEMA_VERSION,
        "artifactPath": EVIDENCE_PATH,
        "generatedAtUtc": generated_at,
        "bindings": {
            "requirement": {"path": REQUIREMENT_PATH, "sha256": requirement_sha},
            "acceptedBuildView": {
                "path": ACCEPTED_VIEW_PATH,
                "sha256": view_sha,
                "requirementFileSha256": requirement_sha,
            },
            "plannedBundle": {
                "path": PLANNED_BUNDLE_PATH,
                "sha256": bundle_sha,
                "requirementFileSha256": requirement_sha,
            },
        },
        "layouts": layouts,
        "plans": plans,
        "checks": dict(CHECKS),
    }


def _first_difference(expected: Any, actual: Any, path: str = "$") -> str | None:
    if type(expected) is not type(actual):
        return f"{path}: expected {type(expected).__name__}, got {type(actual).__name__}"
    if isinstance(expected, dict):
        if list(expected) != list(actual):
            return f"{path}: expected keys {list(expected)!r}, got {list(actual)!r}"
        for key in expected:
            difference = _first_difference(expected[key], actual[key], f"{path}.{key}")
            if difference is not None:
                return difference
        return None
    if isinstance(expected, list):
        if len(expected) != len(actual):
            return f"{path}: expected {len(expected)} entries, got {len(actual)}"
        for index, (expected_item, actual_item) in enumerate(zip(expected, actual)):
            difference = _first_difference(expected_item, actual_item, f"{path}[{index}]")
            if difference is not None:
                return difference
        return None
    if expected != actual:
        return f"{path}: expected {expected!r}, got {actual!r}"
    return None


def validate_evidence(
    artifact_root: Path,
    *,
    plugin_root: Path = DEFAULT_PLUGIN_ROOT,
) -> dict[str, Any]:
    root = artifact_root.resolve(strict=True)
    _, _, evidence_value = _load_bound_json(
        root,
        EVIDENCE_PATH,
        label="pre-mutation evidence",
        prefix="status/",
    )
    evidence = _require_object(evidence_value, label="pre-mutation evidence")
    generated_at = _validate_generated_at(evidence.get("generatedAtUtc"))
    expected = build_expected_evidence(
        root,
        generated_at_utc=generated_at,
        plugin_root=plugin_root,
    )
    difference = _first_difference(expected, evidence)
    if difference is not None:
        raise EvidenceError(f"pre-mutation evidence is not the exact deterministic projection: {difference}")
    return evidence


def _safe_output_path(artifact_root: Path) -> Path:
    root = artifact_root.resolve(strict=True)
    parts = _validate_posix_relative_path(EVIDENCE_PATH, label="evidence output", prefix="status/")
    output = root.joinpath(*parts)
    parent = output.parent
    nearest = parent
    while not nearest.exists():
        if nearest == root:
            break
        nearest = nearest.parent
    try:
        nearest_resolved = nearest.resolve(strict=True)
    except OSError as error:
        raise EvidenceError(f"evidence output parent cannot be resolved: {error}") from error
    if not _is_relative_to(nearest_resolved, root):
        raise EvidenceError("evidence output parent escapes the artifact root")
    parent.mkdir(parents=True, exist_ok=True)
    parent_resolved = parent.resolve(strict=True)
    if not _is_relative_to(parent_resolved, root):
        raise EvidenceError("evidence output parent escapes the artifact root through a symlink")
    return output


def _remove_output(output: Path) -> None:
    try:
        if output.exists() or output.is_symlink():
            output.unlink()
    except OSError as error:
        raise EvidenceError(f"cannot remove stale or invalid evidence output: {error}") from error


def generate_evidence(
    artifact_root: Path,
    *,
    generated_at_utc: str | None = None,
    plugin_root: Path = DEFAULT_PLUGIN_ROOT,
) -> dict[str, Any]:
    root = artifact_root.resolve(strict=True)
    output = _safe_output_path(root)
    _remove_output(output)
    temporary_path: Path | None = None
    try:
        evidence = build_expected_evidence(
            root,
            generated_at_utc=generated_at_utc or _default_generated_at(),
            plugin_root=plugin_root,
        )
        payload = (json.dumps(evidence, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".ui-build-plan.pre-mutation-valid.",
            suffix=".tmp",
            dir=output.parent,
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, output)
        temporary_path = None
        validated = validate_evidence(root, plugin_root=plugin_root)
        return validated
    except Exception:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            _remove_output(output)
        except EvidenceError:
            pass
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate or validate status/ui-build-plan.pre-mutation-valid.json."
    )
    parser.add_argument("artifact_root", type=Path, help="Immutable NextGame UI run artifact root")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate the existing evidence without writing or deleting it",
    )
    parser.add_argument(
        "--generated-at-utc",
        help="Fixed RFC 3339 UTC timestamp for deterministic generation (for example 2026-09-04T00:00:00Z)",
    )
    parser.add_argument(
        "--plugin-root",
        type=Path,
        default=DEFAULT_PLUGIN_ROOT,
        help="Canonical nextgame-ui plugin root containing the strict validators",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.validate_only and arguments.generated_at_utc is not None:
        print("error: --generated-at-utc cannot be used with --validate-only", file=sys.stderr)
        return 2
    try:
        if arguments.validate_only:
            evidence = validate_evidence(
                arguments.artifact_root,
                plugin_root=arguments.plugin_root,
            )
            action = "validated"
        else:
            evidence = generate_evidence(
                arguments.artifact_root,
                generated_at_utc=arguments.generated_at_utc,
                plugin_root=arguments.plugin_root,
            )
            action = "generated"
    except (EvidenceError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": "valid",
                "action": action,
                "artifactPath": evidence["artifactPath"],
                "assetCount": len(evidence["plans"]),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
