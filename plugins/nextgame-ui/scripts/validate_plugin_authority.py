#!/usr/bin/env python3
"""Compare the authoritative NextGame UI plugin source with an installed copy.

The command deliberately does not discover a user profile or Codex cache on its
own.  Callers either provide an installed plugin path, provide an installed
cache root plus marketplace name, or request the source-only manifest check.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable, Sequence


EXIT_MATCH = 0
EXIT_DRIFT = 1
EXIT_ERROR = 2

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_RELATIVE_PATH = Path(".codex-plugin") / "plugin.json"
REPORT_SCHEMA_VERSION = "nextgame-plugin-authority-report/1.0"

SAFE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SEMVER_RE = re.compile(
    r"^(0|[1-9][0-9]*)\."
    r"(0|[1-9][0-9]*)\."
    r"(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)

EXCLUDED_DIRECTORY_NAMES = frozenset(
    {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
)
EXCLUDED_DIRECTORY_PREFIXES = (".plugin-authority-",)
EXCLUDED_FILE_NAMES = frozenset({".DS_Store", "Thumbs.db"})
EXCLUDED_FILE_SUFFIXES = (
    ".pyc",
    ".pyo",
    ".tmp",
    ".temp",
    ".swp",
    ".swo",
)
EXCLUDED_FILE_PREFIXES = ("~$", ".plugin-authority-")


class AuthorityError(RuntimeError):
    """An operational failure, as distinct from a detected package drift."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def compact_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def is_valid_semver(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    match = SEMVER_RE.fullmatch(value)
    if match is None:
        return False
    prerelease = match.group(4)
    if prerelease is None:
        return True
    return all(
        not (identifier.isdigit() and len(identifier) > 1 and identifier.startswith("0"))
        for identifier in prerelease.split(".")
    )


def load_manifest(plugin_root: Path, side: str) -> dict[str, Any]:
    manifest_path = plugin_root / MANIFEST_RELATIVE_PATH
    if not manifest_path.is_file():
        raise AuthorityError(
            f"{side}.manifest_missing",
            f"{side} plugin manifest is missing",
        )
    try:
        with manifest_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AuthorityError(
            f"{side}.manifest_unreadable",
            f"{side} plugin manifest cannot be read: {type(exc).__name__}",
        ) from exc
    if not isinstance(payload, dict):
        raise AuthorityError(
            f"{side}.manifest_shape",
            f"{side} plugin manifest must be a JSON object",
        )
    return payload


def manifest_issue(
    code: str,
    side: str,
    field: str,
    message: str,
) -> dict[str, str]:
    return {"code": code, "side": side, "field": field, "message": message}


def validate_source_identity(
    source_root: Path,
    manifest: dict[str, Any],
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    name = manifest.get("name")
    version = manifest.get("version")
    if not isinstance(name, str) or not name or not SAFE_SEGMENT_RE.fullmatch(name):
        issues.append(
            manifest_issue(
                "source.manifest_name_invalid",
                "source",
                "name",
                "manifest name must be a non-empty package-safe string",
            )
        )
    elif source_root.name != name:
        issues.append(
            manifest_issue(
                "source.basename_name_mismatch",
                "source",
                "name",
                "source directory basename does not equal manifest name",
            )
        )
    if not is_valid_semver(version):
        issues.append(
            manifest_issue(
                "source.manifest_version_invalid",
                "source",
                "version",
                "manifest version must be a valid Semantic Version",
            )
        )
    return issues


def validate_installed_identity(
    manifest: dict[str, Any],
    expected_name: Any,
    expected_version: Any,
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    name = manifest.get("name")
    version = manifest.get("version")
    if not isinstance(name, str) or not name or not SAFE_SEGMENT_RE.fullmatch(name):
        issues.append(
            manifest_issue(
                "installed.manifest_name_invalid",
                "installed",
                "name",
                "installed manifest name must be a non-empty package-safe string",
            )
        )
    elif isinstance(expected_name, str) and name != expected_name:
        issues.append(
            manifest_issue(
                "installed.manifest_name_mismatch",
                "installed",
                "name",
                "installed manifest name does not equal source manifest name",
            )
        )
    if not is_valid_semver(version):
        issues.append(
            manifest_issue(
                "installed.manifest_version_invalid",
                "installed",
                "version",
                "installed manifest version must be a valid Semantic Version",
            )
        )
    elif isinstance(expected_version, str) and version != expected_version:
        issues.append(
            manifest_issue(
                "installed.manifest_version_mismatch",
                "installed",
                "version",
                "installed manifest version does not equal source manifest version",
            )
        )
    return issues


def has_manifest(path: Path) -> bool:
    return (path / MANIFEST_RELATIVE_PATH).is_file()


def versioned_plugin_candidates(container: Path) -> list[Path]:
    if not container.is_dir():
        return []
    try:
        children = sorted(container.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise AuthorityError(
            "installed.enumeration_failed",
            f"installed plugin versions cannot be enumerated: {type(exc).__name__}",
        ) from exc
    return [child for child in children if child.is_dir() and has_manifest(child)]


def choose_installed_plugin(container: Path, expected_version: str) -> Path:
    """Resolve either a plugin root or a container holding versioned roots."""

    if has_manifest(container):
        return container.resolve()
    exact_version = container / expected_version
    if has_manifest(exact_version):
        return exact_version.resolve()
    candidates = versioned_plugin_candidates(container)
    if len(candidates) == 1:
        # Selecting the sole installed version lets the caller receive a drift
        # report instead of an unhelpful not-found error.
        return candidates[0].resolve()
    if candidates:
        raise AuthorityError(
            "installed.version_ambiguous",
            "expected installed version is absent and multiple other versions exist",
        )
    raise AuthorityError(
        "installed.plugin_missing",
        "installed plugin directory or manifest is missing",
    )


def resolve_installed_plugin(
    *,
    installed_plugin: Path | None,
    installed_root: Path | None,
    marketplace_name: str | None,
    plugin_name: str,
    plugin_version: str,
) -> Path:
    if installed_plugin is not None:
        return choose_installed_plugin(installed_plugin.resolve(), plugin_version)
    if installed_root is None or marketplace_name is None:
        raise AuthorityError(
            "arguments.installed_locator_missing",
            "provide --installed-plugin or both --installed-root and --marketplace-name",
        )
    if not SAFE_SEGMENT_RE.fullmatch(marketplace_name):
        raise AuthorityError(
            "arguments.marketplace_name_invalid",
            "marketplace name must be a single package-safe path segment",
        )
    container = installed_root.resolve() / marketplace_name / plugin_name
    return choose_installed_plugin(container, plugin_version)


def is_excluded_file(name: str) -> bool:
    return (
        name in EXCLUDED_FILE_NAMES
        or name.startswith(EXCLUDED_FILE_PREFIXES)
        or name.endswith("~")
        or name.lower().endswith(EXCLUDED_FILE_SUFFIXES)
    )


def is_excluded_directory(name: str) -> bool:
    return (
        name in EXCLUDED_DIRECTORY_NAMES
        or name.startswith(EXCLUDED_DIRECTORY_PREFIXES)
        or name.lower().endswith((".tmp", ".temp"))
    )


def sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as handle:
            while True:
                block = handle.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
                size += len(block)
    except OSError as exc:
        raise AuthorityError(
            "tree.file_unreadable",
            f"plugin file cannot be hashed: {type(exc).__name__}",
        ) from exc
    return digest.hexdigest(), size


def index_tree(plugin_root: Path) -> tuple[dict[str, dict[str, Any]], int]:
    if not plugin_root.is_dir():
        raise AuthorityError("tree.root_missing", "plugin root is not a directory")
    records: dict[str, dict[str, Any]] = {}
    excluded_count = 0

    def raise_walk_error(error: OSError) -> None:
        raise AuthorityError(
            "tree.enumeration_failed",
            f"plugin tree cannot be enumerated: {type(error).__name__}",
        ) from error

    try:
        iterator = os.walk(
            plugin_root,
            topdown=True,
            onerror=raise_walk_error,
            followlinks=False,
        )
        for current_raw, directory_names, file_names in iterator:
            kept_directories: list[str] = []
            for directory_name in directory_names:
                if is_excluded_directory(directory_name):
                    excluded_count += 1
                else:
                    kept_directories.append(directory_name)
            directory_names[:] = kept_directories
            current = Path(current_raw)
            for file_name in file_names:
                if is_excluded_file(file_name):
                    excluded_count += 1
                    continue
                path = current / file_name
                relative_path = path.relative_to(plugin_root).as_posix()
                file_hash, byte_count = sha256_file(path)
                records[relative_path] = {
                    "sha256": file_hash,
                    "bytes": byte_count,
                }
    except AuthorityError:
        raise
    except OSError as exc:
        raise AuthorityError(
            "tree.enumeration_failed",
            f"plugin tree cannot be enumerated: {type(exc).__name__}",
        ) from exc
    return records, excluded_count


def tree_sha256(records: dict[str, dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for relative_path in sorted(records):
        record = records[relative_path]
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(record["bytes"]).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(record["sha256"]).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def compare_trees(
    source: dict[str, dict[str, Any]],
    installed: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    source_paths = set(source)
    installed_paths = set(installed)
    source_only = [
        {"path": path, **source[path]}
        for path in sorted(source_paths - installed_paths)
    ]
    installed_only = [
        {"path": path, **installed[path]}
        for path in sorted(installed_paths - source_paths)
    ]
    changed: list[dict[str, Any]] = []
    for path in sorted(source_paths & installed_paths):
        if source[path]["sha256"] == installed[path]["sha256"]:
            continue
        changed.append(
            {
                "path": path,
                "sourceSha256": source[path]["sha256"],
                "installedSha256": installed[path]["sha256"],
                "sourceBytes": source[path]["bytes"],
                "installedBytes": installed[path]["bytes"],
            }
        )
    return {
        "sourceOnly": source_only,
        "installedOnly": installed_only,
        "changed": changed,
    }


def source_skill_names(source_root: Path) -> set[str]:
    skills_root = source_root / "skills"
    if not skills_root.is_dir():
        return set()
    return {
        candidate.parent.name
        for candidate in skills_root.glob("*/SKILL.md")
        if candidate.is_file()
    }


def detect_standalone_skills(
    source_root: Path,
    standalone_roots: Iterable[Path],
    standalone_skills: Iterable[Path],
) -> list[dict[str, str]]:
    names = source_skill_names(source_root)
    detected: dict[str, dict[str, str]] = {}
    for root in standalone_roots:
        for skill_name in sorted(names):
            candidate = root.resolve() / skill_name
            if (candidate / "SKILL.md").is_file():
                detected[str(candidate)] = {
                    "name": skill_name,
                    "path": str(candidate),
                    "kind": "matching-root-child",
                }
    for raw_candidate in standalone_skills:
        candidate = raw_candidate.resolve()
        if (candidate / "SKILL.md").is_file() and candidate.name in names:
            detected[str(candidate)] = {
                "name": candidate.name,
                "path": str(candidate),
                "kind": "explicit",
            }
    return [detected[key] for key in sorted(detected)]


def ensure_output_outside_plugin_trees(
    output: Path | None,
    plugin_roots: Iterable[Path],
) -> None:
    if output is None:
        return
    resolved_output = output.resolve()
    for plugin_root in plugin_roots:
        resolved_root = plugin_root.resolve()
        try:
            resolved_output.relative_to(resolved_root)
        except ValueError:
            continue
        raise AuthorityError(
            "output.inside_plugin_tree",
            "authority report must be written outside source and installed plugin trees",
        )


def write_report(path: Path, report: dict[str, Any]) -> None:
    parent = path.resolve().parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
        encoded = (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        )
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=parent,
                prefix=".plugin-authority-",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(encoded)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_name = temporary.name
            os.replace(temporary_name, path.resolve())
            temporary_name = None
        finally:
            if temporary_name is not None:
                try:
                    Path(temporary_name).unlink()
                except OSError:
                    pass
    except OSError as exc:
        raise AuthorityError(
            "output.write_failed",
            f"authority report cannot be written: {type(exc).__name__}",
        ) from exc


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate the source NextGame UI plugin against an installed copy.",
    )
    parser.add_argument("--source", type=Path, default=PLUGIN_ROOT)
    parser.add_argument(
        "--source-only",
        action="store_true",
        help="Validate only source basename, manifest name, and manifest version.",
    )
    parser.add_argument(
        "--installed-plugin",
        type=Path,
        help="Explicit installed plugin directory or its version container.",
    )
    parser.add_argument(
        "--installed-root",
        type=Path,
        help="Explicit installed cache root containing <marketplace>/<plugin>/<version>.",
    )
    parser.add_argument(
        "--marketplace-name",
        help="Marketplace directory name used together with --installed-root.",
    )
    parser.add_argument(
        "--standalone-skills-root",
        type=Path,
        action="append",
        default=[],
        help="Read-only scan root for standalone skills matching plugin skill names.",
    )
    parser.add_argument(
        "--standalone-skill",
        type=Path,
        action="append",
        default=[],
        help="Read-only check of one explicit standalone skill directory.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional detailed JSON report; stdout remains a compact one-line summary.",
    )
    return parser


def validate_arguments(args: argparse.Namespace) -> None:
    if args.source_only and (
        args.installed_plugin is not None
        or args.installed_root is not None
        or args.marketplace_name is not None
    ):
        raise AuthorityError(
            "arguments.source_only_conflict",
            "--source-only cannot be combined with an installed locator",
        )
    if args.installed_plugin is not None and (
        args.installed_root is not None or args.marketplace_name is not None
    ):
        raise AuthorityError(
            "arguments.installed_locator_conflict",
            "--installed-plugin cannot be combined with cache-root options",
        )
    if (args.installed_root is None) != (args.marketplace_name is None):
        raise AuthorityError(
            "arguments.marketplace_pair_required",
            "--installed-root and --marketplace-name must be provided together",
        )
    if (
        not args.source_only
        and args.installed_plugin is None
        and args.installed_root is None
    ):
        raise AuthorityError(
            "arguments.mode_required",
            "explicitly provide --source-only or an installed plugin locator",
        )


def build_report(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any], int]:
    validate_arguments(args)
    source_root = args.source.resolve()
    if not source_root.is_dir():
        raise AuthorityError("source.root_missing", "source plugin root is not a directory")
    # Guard the paths explicitly supplied by the caller before reading any
    # manifest.  This also prevents an error report from being written into a
    # plugin tree when that manifest is missing or malformed.
    provisional_roots = [source_root]
    if args.installed_plugin is not None:
        provisional_roots.append(args.installed_plugin)
    if args.installed_root is not None:
        provisional_roots.append(args.installed_root)
    ensure_output_outside_plugin_trees(args.output, provisional_roots)
    source_manifest = load_manifest(source_root, "source")
    manifest_issues = validate_source_identity(source_root, source_manifest)
    plugin_name = source_manifest.get("name")
    plugin_version = source_manifest.get("version")
    standalone = detect_standalone_skills(
        source_root,
        args.standalone_skills_root,
        args.standalone_skill,
    )

    source_only = args.source_only
    mode = "source-only" if source_only else "source-vs-installed"

    if source_only:
        status = "drift" if manifest_issues else "match"
        exit_code = EXIT_DRIFT if manifest_issues else EXIT_MATCH
        details: dict[str, Any] = {
            "schemaVersion": REPORT_SCHEMA_VERSION,
            "status": status,
            "mode": mode,
            "plugin": {"name": plugin_name, "version": plugin_version},
            "manifestIssues": manifest_issues,
            "standaloneSkills": standalone,
        }
        summary = {
            "schemaVersion": REPORT_SCHEMA_VERSION,
            "status": status,
            "mode": mode,
            "plugin": plugin_name,
            "version": plugin_version,
            "manifestIssueCount": len(manifest_issues),
            "standaloneSkillCount": len(standalone),
        }
        return details, summary, exit_code

    if not isinstance(plugin_name, str) or not SAFE_SEGMENT_RE.fullmatch(plugin_name):
        status = "drift"
        details = {
            "schemaVersion": REPORT_SCHEMA_VERSION,
            "status": status,
            "mode": mode,
            "plugin": {"name": plugin_name, "version": plugin_version},
            "manifestIssues": manifest_issues,
            "standaloneSkills": standalone,
        }
        summary = {
            "schemaVersion": REPORT_SCHEMA_VERSION,
            "status": status,
            "mode": mode,
            "plugin": plugin_name,
            "version": plugin_version,
            "manifestIssueCount": len(manifest_issues),
            "standaloneSkillCount": len(standalone),
        }
        return details, summary, EXIT_DRIFT
    if not is_valid_semver(plugin_version):
        status = "drift"
        details = {
            "schemaVersion": REPORT_SCHEMA_VERSION,
            "status": status,
            "mode": mode,
            "plugin": {"name": plugin_name, "version": plugin_version},
            "manifestIssues": manifest_issues,
            "standaloneSkills": standalone,
        }
        summary = {
            "schemaVersion": REPORT_SCHEMA_VERSION,
            "status": status,
            "mode": mode,
            "plugin": plugin_name,
            "version": plugin_version,
            "manifestIssueCount": len(manifest_issues),
            "standaloneSkillCount": len(standalone),
        }
        return details, summary, EXIT_DRIFT

    installed_root = resolve_installed_plugin(
        installed_plugin=args.installed_plugin,
        installed_root=args.installed_root,
        marketplace_name=args.marketplace_name,
        plugin_name=plugin_name,
        plugin_version=plugin_version,
    )
    ensure_output_outside_plugin_trees(args.output, [source_root, installed_root])
    installed_manifest = load_manifest(installed_root, "installed")
    manifest_issues.extend(
        validate_installed_identity(installed_manifest, plugin_name, plugin_version)
    )

    source_records, source_excluded = index_tree(source_root)
    installed_records, installed_excluded = index_tree(installed_root)
    differences = compare_trees(source_records, installed_records)
    difference_counts = {key: len(value) for key, value in differences.items()}
    drift = bool(manifest_issues) or any(difference_counts.values())
    status = "drift" if drift else "match"
    exit_code = EXIT_DRIFT if drift else EXIT_MATCH
    source_tree_sha256 = tree_sha256(source_records)
    installed_tree_sha256 = tree_sha256(installed_records)

    details = {
        "schemaVersion": REPORT_SCHEMA_VERSION,
        "status": status,
        "mode": mode,
        "plugin": {"name": plugin_name, "version": plugin_version},
        "source": {
            "fileCount": len(source_records),
            "excludedEntryCount": source_excluded,
            "treeSha256": source_tree_sha256,
        },
        "installed": {
            "fileCount": len(installed_records),
            "excludedEntryCount": installed_excluded,
            "treeSha256": installed_tree_sha256,
            "manifestName": installed_manifest.get("name"),
            "manifestVersion": installed_manifest.get("version"),
        },
        "manifestIssues": manifest_issues,
        "differenceCounts": difference_counts,
        "differences": differences,
        "standaloneSkills": standalone,
    }
    summary = {
        "schemaVersion": REPORT_SCHEMA_VERSION,
        "status": status,
        "mode": mode,
        "plugin": plugin_name,
        "version": plugin_version,
        "sourceFileCount": len(source_records),
        "installedFileCount": len(installed_records),
        "sourceTreeSha256": source_tree_sha256,
        "installedTreeSha256": installed_tree_sha256,
        "manifestIssueCount": len(manifest_issues),
        "differenceCounts": difference_counts,
        "standaloneSkillCount": len(standalone),
    }
    return details, summary, exit_code


def error_payload(error: AuthorityError) -> dict[str, Any]:
    return {
        "schemaVersion": REPORT_SCHEMA_VERSION,
        "status": "error",
        "error": {"code": error.code, "message": str(error)},
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = create_parser()
    try:
        args = parser.parse_args(argv)
        details, summary, exit_code = build_report(args)
        if args.output is not None:
            write_report(args.output, details)
            summary["reportWritten"] = True
        print(compact_json(summary))
        return exit_code
    except AuthorityError as exc:
        payload = error_payload(exc)
        # Never write an error report.  Some argument errors happen before the
        # output path can be proven outside both package trees; stdout already
        # carries the complete compact operational error.
        print(compact_json(payload))
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
