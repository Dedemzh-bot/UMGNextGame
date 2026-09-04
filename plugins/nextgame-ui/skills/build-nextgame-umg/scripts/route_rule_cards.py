#!/usr/bin/env python3
"""Build and verify a fail-safe, content-addressed NextGame UMG rule-card pack.

The pack only narrows human-readable rule material. It deliberately reuses the
existing selector and layout validator, and it never controls machine validators.
Any uncertainty in the routing map or layout profile selects complete documents.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from select_rules import order_rules_by_source_precedence, select_rules
from validate_layout_spec import DEFAULT_CATALOG, validate_spec


SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RULES = SKILL_ROOT / "references" / "rule-index.json"
DEFAULT_ROUTING = SKILL_ROOT / "references" / "rule-card-routing.json"
DEFAULT_REFERENCES = SKILL_ROOT / "references"
PACK_VERSION = "0.1"
ROUTING_VERSION = "0.1"
RULE_INDEX_VERSION = "0.16"
EXPECTED_RULE_COUNT = 50
KNOWN_LAYOUT_VERSION = "0.2"
KNOWN_MODES = {"prototype", "production"}
KNOWN_ASSET_KINDS = {"prototype", "screen", "child-widget"}
STAGE_ORDER = ("build-planning", "build-execution", "build-verification")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+\S.*$")
FENCE_PATTERN = re.compile(r"^ {0,3}(`{3,}|~{3,})(?:[^\r\n]*)$")

# These three documents preserve every workflow gate even when the routing
# configuration itself is missing or corrupt.
FALLBACK_WORKFLOW_DOCS = (
    "requirement-build-handoff.md",
    "shared-widget-reuse.md",
    "umg-mcp-workflow.md",
)

FALLBACK_WORKFLOW_GUARDS: tuple[dict[str, Any], ...] = (
    {
        "id": "workflow.requirement-review-gate",
        "stages": ["build-planning"],
        "severity": "error",
        "summary": "Build only an accepted, hash-valid Requirement and keep the post-build acceptance gate separate.",
        "files": ["requirement-build-handoff.md"],
        "detailRefs": [
            {"file": "requirement-build-handoff.md", "heading": "## Review gate"},
        ],
    },
    {
        "id": "workflow.build-planning-ownership",
        "stages": ["build-planning"],
        "severity": "error",
        "summary": "Lower accepted requirements into layouts and the Bundle without reinterpreting rejected or unresolved claims.",
        "files": ["requirement-build-handoff.md"],
        "detailRefs": [
            {"file": "requirement-build-handoff.md", "heading": "## Build-planning ownership"},
        ],
    },
    {
        "id": "workflow.shared-widget-preflight",
        "stages": ["build-planning", "build-execution"],
        "severity": "error",
        "summary": "Validate shared Widget reuse against the authoritative Registry and live asset before execution.",
        "files": ["shared-widget-reuse.md"],
        "detailRefs": [
            {"file": "shared-widget-reuse.md", "heading": "## Preflight"},
            {"file": "shared-widget-reuse.md", "heading": "## Shared-control activation contract"},
            {"file": "shared-widget-reuse.md", "heading": "## Composable generation relations"},
            {"file": "shared-widget-reuse.md", "heading": "## Parameter contract"},
        ],
    },
    {
        "id": "workflow.editor-sequence",
        "stages": ["build-execution"],
        "severity": "error",
        "summary": "Use the required compile, CDO mode, save, and post-save readback sequence.",
        "files": ["umg-mcp-workflow.md"],
        "detailRefs": [
            {"file": "umg-mcp-workflow.md", "heading": "## Required sequence"},
        ],
    },
    {
        "id": "workflow.actual-readback",
        "stages": ["build-verification"],
        "severity": "error",
        "summary": "Treat actual post-save Unreal readback as authoritative over plans and expected mappings.",
        "files": ["umg-mcp-workflow.md", "requirement-build-handoff.md"],
        "detailRefs": [
            {"file": "umg-mcp-workflow.md", "heading": "## Actual readback boundary"},
            {"file": "requirement-build-handoff.md", "heading": "## Result capture"},
        ],
    },
    {
        "id": "workflow.post-build-acceptance",
        "stages": ["build-verification"],
        "severity": "error",
        "summary": "Present the verified build and stop; only a later direct user acceptance may open documentation.",
        "files": ["requirement-build-handoff.md"],
        "detailRefs": [
            {"file": "requirement-build-handoff.md", "heading": "## Post-build user acceptance gate"},
        ],
    },
)


class RequiredInputError(RuntimeError):
    """A file required to form even the complete fail-safe pack is unavailable."""


def require_plugin_authority_paths(
    rules_path: Path,
    routing_path: Path,
    catalog_path: Path,
    references_dir: Path,
) -> None:
    supplied = {
        "rule index": rules_path.resolve(),
        "routing config": routing_path.resolve(),
        "component catalog": catalog_path.resolve(),
        "reference directory": references_dir.resolve(),
    }
    authoritative = {
        "rule index": DEFAULT_RULES.resolve(),
        "routing config": DEFAULT_ROUTING.resolve(),
        "component catalog": DEFAULT_CATALOG.resolve(),
        "reference directory": DEFAULT_REFERENCES.resolve(),
    }
    replaced = [label for label, path in supplied.items() if path != authoritative[label]]
    if replaced:
        raise RequiredInputError(
            "plugin authority paths cannot be replaced in production: " + ", ".join(replaced)
        )


def validate_rule_index_authority(index: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(index, dict) or set(index) != {"version", "rules"}:
        return ["rule-index-shape-invalid"]
    if index.get("version") != RULE_INDEX_VERSION:
        errors.append("rule-index-version-unknown")
    rules = index.get("rules")
    if not isinstance(rules, list) or len(rules) != EXPECTED_RULE_COUNT:
        return [*errors, "rule-index-count-invalid"]
    ids: list[str] = []
    for position, rule in enumerate(rules):
        if not isinstance(rule, dict):
            errors.append(f"rule-index-rule-invalid:{position}")
            continue
        rule_id = rule.get("id")
        if not isinstance(rule_id, str) or not rule_id:
            errors.append(f"rule-index-id-invalid:{position}")
        else:
            ids.append(rule_id)
        if rule.get("severity") not in {"error", "warning", "info"}:
            errors.append(f"rule-index-severity-invalid:{position}")
        if not isinstance(rule.get("summary"), str) or not rule["summary"]:
            errors.append(f"rule-index-summary-invalid:{position}")
        if rule.get("sourceType", "baseline") not in {"explicit", "observed", "baseline"}:
            errors.append(f"rule-index-source-type-invalid:{position}")
        if "when" in rule and not isinstance(rule.get("when"), dict):
            errors.append(f"rule-index-condition-invalid:{position}")
        for key in ("reference", "validator"):
            if key in rule and (not isinstance(rule.get(key), str) or not rule[key]):
                errors.append(f"rule-index-{key}-invalid:{position}")
    if len(ids) != EXPECTED_RULE_COUNT or len(set(ids)) != len(ids):
        errors.append("rule-index-id-coverage-invalid")
    return errors


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def read_required_bytes(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise RequiredInputError(f"{label} unavailable: {path.name}: {exc}") from exc


def decode_required_utf8(raw: bytes, label: str, file_name: str) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RequiredInputError(f"{label} is not UTF-8: {file_name}: {exc}") from exc


def load_required_json(path: Path, label: str) -> tuple[Any, bytes]:
    raw = read_required_bytes(path, label)
    try:
        return json.loads(raw.decode("utf-8")), raw
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RequiredInputError(f"{label} is not valid UTF-8 JSON: {path.name}: {exc}") from exc


def try_load_json(path: Path) -> tuple[Any | None, bytes | None, str | None]:
    try:
        raw = path.read_bytes()
    except OSError:
        return None, None, "routing-config-missing"
    try:
        return json.loads(raw.decode("utf-8")), raw, None
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, raw, "routing-config-json-invalid"


def safe_reference_path(references_dir: Path, file_name: Any) -> Path | None:
    if not isinstance(file_name, str) or not file_name or Path(file_name).name != file_name or "\\" in file_name:
        return None
    return references_dir / file_name


def read_reference_for_routing(
    references_dir: Path,
    file_name: Any,
    cache: dict[str, tuple[bytes, str]],
) -> tuple[bytes, str] | None:
    path = safe_reference_path(references_dir, file_name)
    if path is None:
        return None
    if file_name in cache:
        return cache[file_name]
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    cache[file_name] = (raw, text)
    return cache[file_name]


def read_reference_required(
    references_dir: Path,
    file_name: str,
    cache: dict[str, tuple[bytes, str]],
) -> tuple[bytes, str]:
    cached = read_reference_for_routing(references_dir, file_name, cache)
    if cached is None:
        raise RequiredInputError(f"required fallback reference unavailable: {file_name}")
    return cached


def _scan_markdown_headings(text: str) -> tuple[list[str], list[int], list[tuple[int, str, int]]]:
    """Scan real Markdown headings while ignoring heading-like lines in fences."""

    lines = text.splitlines(keepends=True)
    line_offsets: list[int] = []
    offset = 0
    for line in lines:
        line_offsets.append(offset)
        offset += len(line)
    markdown_headings: list[tuple[int, str, int]] = []
    open_fence: tuple[str, int] | None = None
    for index, line in enumerate(lines):
        raw_line = line.rstrip("\r\n")
        fence_match = FENCE_PATTERN.match(raw_line)
        if open_fence is not None:
            fence_char, minimum_length = open_fence
            closing = re.fullmatch(
                rf" {{0,3}}{re.escape(fence_char)}{{{minimum_length},}}[ \t]*",
                raw_line,
            )
            if closing is not None:
                open_fence = None
            continue
        if fence_match is not None:
            marker = fence_match.group(1)
            open_fence = (marker[0], len(marker))
            continue
        candidate = HEADING_PATTERN.fullmatch(raw_line)
        if candidate is not None:
            markdown_headings.append((index, raw_line, len(candidate.group(1))))
    return lines, line_offsets, markdown_headings


def extract_exact_section_span(
    text: str,
    heading: str,
) -> tuple[tuple[str, int, int] | None, str | None]:
    """Return exact section text plus its proven half-open source interval."""

    match = HEADING_PATTERN.fullmatch(heading)
    if match is None:
        return None, "heading-format-invalid"
    level = len(match.group(1))
    lines, line_offsets, markdown_headings = _scan_markdown_headings(text)

    occurrences = [index for index, raw, _ in markdown_headings if raw == heading]
    if not occurrences:
        return None, "heading-missing"
    if len(occurrences) != 1:
        return None, "heading-duplicate"
    start = occurrences[0]
    end = len(lines)
    for index, _, candidate_level in markdown_headings:
        if index > start and candidate_level <= level:
            end = index
            break
    start_offset = line_offsets[start]
    end_offset = line_offsets[end] if end < len(lines) else len(text)
    return (text[start_offset:end_offset], start_offset, end_offset), None


def extract_exact_section(text: str, heading: str) -> tuple[str | None, str | None]:
    """Return one exact Markdown heading section, or a stable failure code.

    The returned text starts at the requested heading and ends immediately before
    the next heading at the same or a higher level. Raw line endings are retained.
    """

    section_span, error = extract_exact_section_span(text, heading)
    if section_span is None:
        return None, error
    return section_span[0], None


def normalize_stages(stages: list[str] | tuple[str, ...] | None) -> list[str]:
    if stages is None or not stages:
        return list(STAGE_ORDER)
    if any(not isinstance(stage, str) for stage in stages):
        raise ValueError("workflow stages must be strings")
    requested = set(stages)
    unknown = requested.difference(STAGE_ORDER)
    if unknown:
        raise ValueError(f"unknown workflow stage(s): {', '.join(sorted(unknown))}")
    return [stage for stage in STAGE_ORDER if stage in requested]


def _validate_detail_refs(value: Any, owner: str, errors: list[str]) -> list[dict[str, str]]:
    if not isinstance(value, list):
        errors.append(f"detail-refs-invalid:{owner}")
        return []
    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {"file", "heading"}:
            errors.append(f"detail-ref-shape-invalid:{owner}")
            continue
        file_name = item.get("file")
        heading = item.get("heading")
        if (
            not isinstance(file_name, str)
            or not file_name
            or Path(file_name).name != file_name
            or "\\" in file_name
            or not isinstance(heading, str)
            or HEADING_PATTERN.fullmatch(heading) is None
        ):
            errors.append(f"detail-ref-value-invalid:{owner}")
            continue
        key = (file_name, heading)
        if key in seen:
            errors.append(f"detail-ref-duplicate:{owner}:{file_name}:{heading}")
            continue
        seen.add(key)
        normalized.append({"file": file_name, "heading": heading})
    return normalized


def validate_routing_configuration(
    config: Any,
    index: Any,
    references_dir: Path,
    document_cache: dict[str, tuple[bytes, str]],
) -> tuple[list[str], dict[str, dict[str, Any]], list[dict[str, Any]], dict[tuple[str, str], str]]:
    errors: list[str] = []
    cards_by_id: dict[str, dict[str, Any]] = {}
    guards: list[dict[str, Any]] = []
    sections: dict[tuple[str, str], str] = {}

    if not isinstance(index, dict) or index.get("version") != RULE_INDEX_VERSION:
        errors.append("rule-index-version-unknown")
    rules = index.get("rules") if isinstance(index, dict) else None
    if not isinstance(rules, list):
        errors.append("rule-index-rules-invalid")
        rules = []
    rule_ids = [rule.get("id") for rule in rules if isinstance(rule, dict) and isinstance(rule.get("id"), str)]
    if len(rules) != EXPECTED_RULE_COUNT or len(rule_ids) != EXPECTED_RULE_COUNT:
        errors.append("rule-index-count-invalid")
    if len(set(rule_ids)) != len(rule_ids):
        errors.append("rule-index-id-duplicate")

    if not isinstance(config, dict) or set(config) != {"version", "ruleIndexVersion", "cards", "workflowGuards"}:
        errors.append("routing-config-shape-invalid")
        return errors, cards_by_id, guards, sections
    if config.get("version") != ROUTING_VERSION:
        errors.append("routing-config-version-unknown")
    if config.get("ruleIndexVersion") != index.get("version"):
        errors.append("routing-config-index-version-mismatch")

    cards = config.get("cards")
    if not isinstance(cards, list):
        errors.append("routing-config-cards-invalid")
        cards = []
    for item in cards:
        if not isinstance(item, dict) or set(item) != {"ruleId", "selfContained", "detailRefs"}:
            errors.append("routing-card-shape-invalid")
            continue
        rule_id = item.get("ruleId")
        if not isinstance(rule_id, str) or not rule_id:
            errors.append("routing-card-id-invalid")
            continue
        if rule_id in cards_by_id:
            errors.append(f"routing-card-id-duplicate:{rule_id}")
            continue
        refs = _validate_detail_refs(item.get("detailRefs"), f"rule:{rule_id}", errors)
        cards_by_id[rule_id] = {
            "ruleId": rule_id,
            "selfContained": item.get("selfContained"),
            "detailRefs": refs,
        }

    if set(cards_by_id) != set(rule_ids) or len(cards_by_id) != len(rule_ids):
        errors.append("routing-card-coverage-mismatch")

    rules_by_id = {
        rule["id"]: rule
        for rule in rules
        if isinstance(rule, dict) and isinstance(rule.get("id"), str)
    }
    for rule_id in rule_ids:
        rule = rules_by_id.get(rule_id, {})
        card = cards_by_id.get(rule_id)
        if card is None:
            continue
        reference = rule.get("reference")
        refs = card["detailRefs"]
        if isinstance(reference, str):
            if card.get("selfContained") is not False or not refs or any(ref["file"] != reference for ref in refs):
                errors.append(f"routing-card-reference-mismatch:{rule_id}")
        elif card.get("selfContained") is not True or refs:
            errors.append(f"routing-card-self-contained-mismatch:{rule_id}")

    raw_guards = config.get("workflowGuards")
    if not isinstance(raw_guards, list):
        errors.append("workflow-guards-invalid")
        raw_guards = []
    seen_guard_ids: set[str] = set()
    expected_guard_ids = {item["id"] for item in FALLBACK_WORKFLOW_GUARDS}
    canonical_guards = {
        item["id"]: {
            key: copy.deepcopy(value)
            for key, value in item.items()
            if key != "files"
        }
        for item in FALLBACK_WORKFLOW_GUARDS
    }
    for item in raw_guards:
        if not isinstance(item, dict) or set(item) != {"id", "stages", "severity", "summary", "detailRefs"}:
            errors.append("workflow-guard-shape-invalid")
            continue
        guard_id = item.get("id")
        guard_stages = item.get("stages")
        if not isinstance(guard_id, str) or not guard_id or guard_id in seen_guard_ids:
            errors.append(f"workflow-guard-id-invalid:{guard_id}")
            continue
        seen_guard_ids.add(guard_id)
        if (
            not isinstance(guard_stages, list)
            or not guard_stages
            or any(not isinstance(stage, str) for stage in guard_stages)
            or len(set(guard_stages)) != len(guard_stages)
            or any(stage not in STAGE_ORDER for stage in guard_stages)
            or item.get("severity") not in {"error", "warning"}
            or not isinstance(item.get("summary"), str)
            or not item.get("summary")
        ):
            errors.append(f"workflow-guard-value-invalid:{guard_id}")
        refs = _validate_detail_refs(item.get("detailRefs"), f"workflow:{guard_id}", errors)
        if not refs:
            errors.append(f"workflow-guard-detail-missing:{guard_id}")
        normalized_guard = {
            "id": guard_id,
            "stages": guard_stages if isinstance(guard_stages, list) else [],
            "severity": item.get("severity"),
            "summary": item.get("summary"),
            "detailRefs": refs,
        }
        if normalized_guard != canonical_guards.get(guard_id):
            errors.append(f"workflow-guard-authority-mismatch:{guard_id}")
        guards.append(normalized_guard)
    if seen_guard_ids != expected_guard_ids:
        errors.append("workflow-guard-coverage-mismatch")

    all_detail_refs: list[tuple[str, str, str]] = []
    for rule_id in rule_ids:
        card = cards_by_id.get(rule_id)
        if card is not None:
            all_detail_refs.extend((ref["file"], ref["heading"], f"rule:{rule_id}") for ref in card["detailRefs"])
    for guard in guards:
        all_detail_refs.extend((ref["file"], ref["heading"], f"workflow:{guard['id']}") for ref in guard["detailRefs"])

    for file_name, heading, owner in all_detail_refs:
        key = (file_name, heading)
        if key in sections:
            continue
        document = read_reference_for_routing(references_dir, file_name, document_cache)
        if document is None:
            errors.append(f"detail-document-unavailable:{file_name}")
            continue
        _, text = document
        section, section_error = extract_exact_section(text, heading)
        if section_error is not None:
            errors.append(f"{section_error}:{file_name}:{heading}:{owner}")
            continue
        assert section is not None
        sections[key] = section

    return errors, cards_by_id, guards, sections


def make_rule_cards(selected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": str(rule.get("id")),
            "severity": rule.get("severity") if rule.get("severity") in {"error", "warning", "info"} else "warning",
            "sourceType": str(rule.get("sourceType", "baseline")),
            "summary": str(rule.get("summary", "")),
            "validator": rule.get("validator") if isinstance(rule.get("validator"), str) else None,
            "reference": rule.get("reference") if isinstance(rule.get("reference"), str) else None,
        }
        for rule in selected
    ]


def make_workflow_cards(guards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": guard["id"],
            "stages": list(guard["stages"]),
            "severity": guard["severity"],
            "summary": guard["summary"],
        }
        for guard in guards
    ]


def source_groups(selected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    by_type: dict[str, list[str]] = {}
    for rule in selected:
        source_type = str(rule.get("sourceType", "baseline"))
        if source_type not in by_type:
            by_type[source_type] = []
            groups.append({"sourceType": source_type, "ruleIds": by_type[source_type]})
        by_type[source_type].append(str(rule.get("id")))
    return groups


def fallback_guard_records() -> list[dict[str, Any]]:
    return [
        {key: copy.deepcopy(value) for key, value in guard.items() if key != "files"}
        for guard in FALLBACK_WORKFLOW_GUARDS
    ]


def full_fallback_documents(
    selected: list[dict[str, Any]],
    references_dir: Path,
    document_cache: dict[str, tuple[bytes, str]],
) -> list[dict[str, Any]]:
    ordered_files: list[str] = []
    rule_ids_by_file: dict[str, list[str]] = {}
    for rule in selected:
        file_name = rule.get("reference")
        if not isinstance(file_name, str):
            continue
        if file_name not in ordered_files:
            ordered_files.append(file_name)
        rule_ids_by_file.setdefault(file_name, []).append(str(rule.get("id")))
    for file_name in FALLBACK_WORKFLOW_DOCS:
        if file_name not in ordered_files:
            ordered_files.append(file_name)

    workflow_ids_by_file: dict[str, list[str]] = {file_name: [] for file_name in ordered_files}
    for guard in FALLBACK_WORKFLOW_GUARDS:
        for file_name in guard["files"]:
            workflow_ids_by_file.setdefault(file_name, []).append(guard["id"])

    documents: list[dict[str, Any]] = []
    for file_name in ordered_files:
        raw, text = read_reference_required(references_dir, file_name, document_cache)
        documents.append(
            {
                "file": file_name,
                "sha256": sha256_bytes(raw),
                "bytes": len(raw),
                "ruleIds": rule_ids_by_file.get(file_name, []),
                "workflowGuardIds": workflow_ids_by_file.get(file_name, []),
                "text": text,
            }
        )
    return documents


def routed_sections(
    selected: list[dict[str, Any]],
    cards_by_id: dict[str, dict[str, Any]],
    active_guards: list[dict[str, Any]],
    sections: dict[tuple[str, str], str],
    document_cache: dict[str, tuple[bytes, str]],
) -> list[dict[str, Any]]:
    ordered_keys: list[tuple[str, str]] = []
    rule_ids_by_key: dict[tuple[str, str], list[str]] = {}
    guard_ids_by_key: dict[tuple[str, str], list[str]] = {}
    for rule in selected:
        rule_id = str(rule.get("id"))
        for ref in cards_by_id[rule_id]["detailRefs"]:
            key = (ref["file"], ref["heading"])
            if key not in ordered_keys:
                ordered_keys.append(key)
            rule_ids_by_key.setdefault(key, []).append(rule_id)
    for guard in active_guards:
        for ref in guard["detailRefs"]:
            key = (ref["file"], ref["heading"])
            if key not in ordered_keys:
                ordered_keys.append(key)
            guard_ids_by_key.setdefault(key, []).append(guard["id"])

    candidates: list[dict[str, Any]] = []
    for order, (file_name, heading) in enumerate(ordered_keys):
        raw, document_text = document_cache[file_name]
        section_span, span_error = extract_exact_section_span(document_text, heading)
        if section_span is None or span_error is not None:
            raise RequiredInputError(
                f"validated detail section interval became unavailable: {file_name}: {heading}"
            )
        text, start_offset, end_offset = section_span
        if text != sections[(file_name, heading)]:
            raise RequiredInputError(
                f"validated detail section text changed during routing: {file_name}: {heading}"
            )
        text_raw = text.encode("utf-8")
        candidates.append(
            {
                "file": file_name,
                "fileSha256": sha256_bytes(raw),
                "heading": heading,
                "headingLevel": len(heading.split(" ", 1)[0]),
                "sectionSha256": sha256_bytes(text_raw),
                "bytes": len(text_raw),
                "ruleIds": rule_ids_by_key.get((file_name, heading), []),
                "workflowGuardIds": guard_ids_by_key.get((file_name, heading), []),
                "text": text,
                "_start": start_offset,
                "_end": end_offset,
                "_order": order,
            }
        )

    def strictly_contains(parent: dict[str, Any], child: dict[str, Any]) -> bool:
        return (
            parent["file"] == child["file"]
            and parent["headingLevel"] < child["headingLevel"]
            and parent["_start"] < child["_start"]
            and parent["_end"] >= child["_end"]
        )

    # A retained interval is maximal among the requested intervals in its file.
    # Source offsets, not text matching, are the sole proof of containment.
    retained = [
        copy.deepcopy(candidate)
        for candidate in candidates
        if not any(
            other is not candidate and strictly_contains(other, candidate)
            for other in candidates
        )
    ]
    for candidate in retained:
        candidate["ruleIds"] = []
        candidate["workflowGuardIds"] = []
        candidate["_firstOrder"] = candidate["_order"]

    standalone_candidates: list[dict[str, Any]] = []
    for candidate in candidates:
        owners = [
            parent
            for parent in retained
            if parent["_order"] == candidate["_order"] or strictly_contains(parent, candidate)
        ]
        # Maximal well-formed Markdown intervals have exactly one owner. If that
        # cannot be proven, retain the candidate independently instead of merging.
        if len(owners) != 1:
            standalone = copy.deepcopy(candidate)
            standalone["_firstOrder"] = standalone["_order"]
            standalone_candidates.append(standalone)
            continue
        owner = owners[0]
        owner["_firstOrder"] = min(owner["_firstOrder"], candidate["_order"])
        for field in ("ruleIds", "workflowGuardIds"):
            for owner_id in candidate[field]:
                if owner_id not in owner[field]:
                    owner[field].append(owner_id)

    retained.extend(standalone_candidates)

    rule_owner_order = {
        owner_id: position
        for position, owner_id in enumerate(
            dict.fromkeys(str(rule.get("id")) for rule in selected)
        )
    }
    guard_owner_order = {
        owner_id: position
        for position, owner_id in enumerate(
            dict.fromkeys(str(guard.get("id")) for guard in active_guards)
        )
    }
    for candidate in retained:
        candidate["ruleIds"].sort(key=rule_owner_order.__getitem__)
        candidate["workflowGuardIds"].sort(key=guard_owner_order.__getitem__)

    result: list[dict[str, Any]] = []
    for candidate in sorted(retained, key=lambda item: (item["_firstOrder"], item["_order"])):
        result.append(
            {
                key: value
                for key, value in candidate.items()
                if not key.startswith("_")
            }
        )
    return result


def _pack_digest(pack_without_digest: dict[str, Any]) -> str:
    return sha256_bytes(canonical_json_bytes(pack_without_digest))


def _build_rule_card_pack(
    layout_path: Path,
    *,
    rules_path: Path = DEFAULT_RULES,
    routing_path: Path = DEFAULT_ROUTING,
    catalog_path: Path = DEFAULT_CATALOG,
    references_dir: Path = DEFAULT_REFERENCES,
    stages: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    selected_stages = normalize_stages(stages)
    layout, layout_raw = load_required_json(layout_path, "layout")
    index, index_raw = load_required_json(rules_path, "rule index")
    catalog, catalog_raw = load_required_json(catalog_path, "component catalog")

    authority_errors = validate_rule_index_authority(index)
    if authority_errors:
        raise RequiredInputError(
            "rule index authority is invalid: " + ", ".join(authority_errors)
        )

    selection_layout = layout if isinstance(layout, dict) else {}
    selection_index = index if isinstance(index, dict) else {}
    fallback_reasons: list[str] = []
    selector_uncertain = False
    if not isinstance(layout, dict) or layout.get("version") != KNOWN_LAYOUT_VERSION:
        fallback_reasons.append("layout-schema-version-unknown")
        selector_uncertain = True
    mode = layout.get("mode") if isinstance(layout, dict) else None
    profile = layout.get("profile") if isinstance(layout, dict) else None
    if mode not in KNOWN_MODES or not isinstance(profile, dict) or profile.get("assetKind") not in KNOWN_ASSET_KINDS:
        fallback_reasons.append("layout-profile-unknown")
        selector_uncertain = True
    try:
        selected = select_rules(selection_layout, selection_index)
    except Exception:  # A selector failure broadens to all indexed authority.
        selected = []
        selector_uncertain = True
        fallback_reasons.append("rule-selector-exception")

    try:
        validation = validate_spec(layout, catalog)
    except Exception:  # Fail safe around unknown future or malformed schemas.
        validation = {"valid": False, "errors": [{"code": "layout.validator.exception"}]}
        selector_uncertain = True
        fallback_reasons.append("layout-validator-exception")
    validation_errors = validation.get("errors") if isinstance(validation, dict) else []
    if not isinstance(validation_errors, list):
        validation_errors = []

    if not validation.get("valid", False):
        fallback_reasons.append("layout-validation-failed")

    fallback_authority_rules = (
        order_rules_by_source_precedence(index["rules"])
        if selector_uncertain
        else selected
    )

    routing, routing_raw, routing_load_error = try_load_json(routing_path)
    if routing_load_error is not None:
        fallback_reasons.append(routing_load_error)

    document_cache: dict[str, tuple[bytes, str]] = {}
    config_errors: list[str] = []
    cards_by_id: dict[str, dict[str, Any]] = {}
    config_guards: list[dict[str, Any]] = []
    extracted_sections: dict[tuple[str, str], str] = {}
    if routing is not None:
        config_errors, cards_by_id, config_guards, extracted_sections = validate_routing_configuration(
            routing,
            index,
            references_dir,
            document_cache,
        )
        fallback_reasons.extend(config_errors)

    # Stable unique reasons keep diagnostics deterministic without bloating the pack.
    fallback_reasons = list(dict.fromkeys(fallback_reasons))
    routing_mode = "fallback-full" if fallback_reasons else "routed"
    rule_cards = make_rule_cards(selected)

    fallback_documents = full_fallback_documents(
        fallback_authority_rules,
        references_dir,
        document_cache,
    )
    fallback_guards = fallback_guard_records()
    fallback_guard_cards = make_workflow_cards(fallback_guards)

    if routing_mode == "routed":
        selected_stage_set = set(selected_stages)
        active_guards = [guard for guard in config_guards if selected_stage_set.intersection(guard["stages"])]
        workflow_cards = make_workflow_cards(active_guards)
        detail_sections = routed_sections(
            selected,
            cards_by_id,
            active_guards,
            extracted_sections,
            document_cache,
        )
        full_documents: list[dict[str, Any]] = []
        fallback_authority_cards: list[dict[str, Any]] = []
    else:
        workflow_cards = fallback_guard_cards
        detail_sections = []
        full_documents = fallback_documents
        fallback_authority_cards = make_rule_cards(fallback_authority_rules)

    selected_card_bytes = len(canonical_json_bytes(rule_cards))
    fallback_authority_card_bytes = len(canonical_json_bytes(make_rule_cards(fallback_authority_rules)))
    active_rule_card_bytes = (
        selected_card_bytes if routing_mode == "routed" else fallback_authority_card_bytes
    )
    workflow_card_bytes = len(canonical_json_bytes(workflow_cards))
    detail_bytes = sum(section["bytes"] for section in detail_sections)
    full_document_bytes = sum(document["bytes"] for document in full_documents)
    instruction_bytes = active_rule_card_bytes + workflow_card_bytes + detail_bytes + full_document_bytes
    full_fallback_instruction_bytes = (
        fallback_authority_card_bytes
        + len(canonical_json_bytes(fallback_guard_cards))
        + sum(document["bytes"] for document in fallback_documents)
    )
    saved_bytes = max(0, full_fallback_instruction_bytes - instruction_bytes)
    reduction_percent = (
        round(saved_bytes * 100.0 / full_fallback_instruction_bytes, 4)
        if full_fallback_instruction_bytes
        else 0.0
    )

    pack: dict[str, Any] = {
        "version": PACK_VERSION,
        "routingMode": routing_mode,
        "stages": selected_stages,
        "bindings": {
            "layout": {"file": layout_path.name, "sha256": sha256_bytes(layout_raw)},
            "ruleIndex": {
                "file": rules_path.name,
                "version": str(index.get("version", "")) if isinstance(index, dict) else "",
                "sha256": sha256_bytes(index_raw),
            },
            "routingConfig": {
                "file": routing_path.name,
                "version": routing.get("version") if isinstance(routing, dict) and isinstance(routing.get("version"), str) else None,
                "sha256": sha256_bytes(routing_raw) if routing_raw is not None else None,
            },
            "componentCatalog": {"file": catalog_path.name, "sha256": sha256_bytes(catalog_raw)},
        },
        "selectedRuleIds": [str(rule.get("id")) for rule in selected],
        "selectedRuleIdsBySourceType": source_groups(selected),
        "ruleCards": rule_cards,
        "fallbackAuthorityRuleIds": [
            str(rule.get("id")) for rule in fallback_authority_rules
        ] if routing_mode == "fallback-full" else [],
        "fallbackAuthorityRuleCards": fallback_authority_cards,
        "workflowGuardCards": workflow_cards,
        "detailSections": detail_sections,
        "fullDocuments": full_documents,
        "fallbackReasons": fallback_reasons,
        "machineValidation": {
            "layoutValidator": "validate_layout_spec.validate_spec",
            "layoutValid": bool(validation.get("valid", False)),
            "layoutErrorCount": len(validation_errors),
            "machineValidatorsEnabled": True,
            "routingMayDisableValidators": False,
        },
        "byteTelemetry": {
            "basis": "utf-8 source text plus canonical compact JSON cards",
            "selectedRuleCardBytes": selected_card_bytes,
            "fallbackAuthorityRuleCardBytes": fallback_authority_card_bytes,
            "workflowGuardCardBytes": workflow_card_bytes,
            "detailSectionBytes": detail_bytes,
            "fullDocumentBytes": full_document_bytes,
            "instructionBytes": instruction_bytes,
            "fullFallbackInstructionBytes": full_fallback_instruction_bytes,
            "savedBytes": saved_bytes,
            "reductionPercent": reduction_percent,
        },
        "tokenTelemetry": {
            "actualInputTokens": None,
            "actualOutputTokens": None,
            "measurementStatus": "not-available-no-byte-to-token-conversion",
        },
    }
    pack["packDigestSha256"] = _pack_digest(pack)
    return pack


def build_rule_card_pack(
    layout_path: Path,
    *,
    stages: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Build from plugin-owned authorities only.

    Alternate authority injection is deliberately absent from this production API.
    Tests that exercise corrupt or missing authorities use the private helper below.
    """
    return _build_rule_card_pack(layout_path, stages=stages)


def _build_rule_card_pack_for_test(
    layout_path: Path,
    *,
    rules_path: Path = DEFAULT_RULES,
    routing_path: Path = DEFAULT_ROUTING,
    catalog_path: Path = DEFAULT_CATALOG,
    references_dir: Path = DEFAULT_REFERENCES,
    stages: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Dependency-injected test seam; never exposed by the CLI or production API."""
    return _build_rule_card_pack(
        layout_path,
        rules_path=rules_path,
        routing_path=routing_path,
        catalog_path=catalog_path,
        references_dir=references_dir,
        stages=stages,
    )


def _validate_rule_card_pack(
    pack_path: Path,
    layout_path: Path,
    *,
    rules_path: Path = DEFAULT_RULES,
    routing_path: Path = DEFAULT_ROUTING,
    catalog_path: Path = DEFAULT_CATALOG,
    references_dir: Path = DEFAULT_REFERENCES,
    stages: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    if stages is None or not stages:
        return {
            "valid": False,
            "errors": [
                {
                    "code": "stage.expected-required",
                    "message": "Validation requires an explicit expected workflow stage.",
                }
            ],
        }
    try:
        pack, _ = load_required_json(pack_path, "rule-card pack")
        expected = _build_rule_card_pack(
            layout_path,
            rules_path=rules_path,
            routing_path=routing_path,
            catalog_path=catalog_path,
            references_dir=references_dir,
            stages=stages,
        )
    except (RequiredInputError, ValueError) as exc:
        return {"valid": False, "errors": [{"code": "input", "message": str(exc)}]}

    errors: list[dict[str, str]] = []
    if not isinstance(pack, dict):
        errors.append({"code": "pack.type", "message": "The rule-card pack must be an object."})
    else:
        digest = pack.get("packDigestSha256")
        payload = {key: value for key, value in pack.items() if key != "packDigestSha256"}
        if not isinstance(digest, str) or SHA256_PATTERN.fullmatch(digest) is None or digest != _pack_digest(payload):
            errors.append({"code": "pack.digest", "message": "packDigestSha256 does not match the pack payload."})
        if pack != expected:
            errors.append(
                {
                    "code": "pack.stale-or-tampered",
                    "message": "The pack does not exactly match current layout, selector, routing, catalog, and reference content.",
                }
            )
    return {
        "valid": not errors,
        "errors": errors,
        "summary": {
            "routingMode": expected.get("routingMode"),
            "selectedRules": len(expected.get("selectedRuleIds", [])),
            "instructionBytes": expected.get("byteTelemetry", {}).get("instructionBytes"),
        },
    }


def validate_rule_card_pack(
    pack_path: Path,
    layout_path: Path,
    *,
    stages: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Validate against plugin-owned authorities and an external expected stage."""
    return _validate_rule_card_pack(pack_path, layout_path, stages=stages)


def _validate_rule_card_pack_for_test(
    pack_path: Path,
    layout_path: Path,
    *,
    rules_path: Path = DEFAULT_RULES,
    routing_path: Path = DEFAULT_ROUTING,
    catalog_path: Path = DEFAULT_CATALOG,
    references_dir: Path = DEFAULT_REFERENCES,
    stages: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Dependency-injected validation seam for authority-failure tests only."""
    return _validate_rule_card_pack(
        pack_path,
        layout_path,
        rules_path=rules_path,
        routing_path=routing_path,
        catalog_path=catalog_path,
        references_dir=references_dir,
        stages=stages,
    )


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("layout", type=Path)
    parser.add_argument(
        "--stage",
        action="append",
        choices=STAGE_ORDER,
        dest="stages",
        required=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    route_parser = subparsers.add_parser("route", help="Create a deterministic rule-card pack.")
    add_common_arguments(route_parser)
    route_parser.add_argument("--output", type=Path)
    validate_parser = subparsers.add_parser("validate", help="Recompute and verify a rule-card pack.")
    validate_parser.add_argument("pack", type=Path)
    add_common_arguments(validate_parser)
    args = parser.parse_args()

    try:
        if args.command == "route":
            pack = build_rule_card_pack(
                args.layout,
                stages=args.stages,
            )
            if args.output is not None:
                write_json(args.output, pack)
                result: Any = {
                    "valid": True,
                    "output": str(args.output),
                    "routingMode": pack["routingMode"],
                    "selectedRules": len(pack["selectedRuleIds"]),
                    "byteTelemetry": pack["byteTelemetry"],
                }
            else:
                result = pack
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
        report = validate_rule_card_pack(
            args.pack,
            args.layout,
            stages=args.stages,
        )
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if report.get("valid") else 1
    except (RequiredInputError, ValueError) as exc:
        print(json.dumps({"valid": False, "errors": [{"code": "input", "message": str(exc)}]}, indent=2, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
