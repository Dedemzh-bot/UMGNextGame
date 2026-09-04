#!/usr/bin/env python3
"""Create compact, lossless SharedWidgetRegistry discovery cards or expand one bound entry.

The shortlist and expansion are deliberately non-authoritative. Every card is
non-executable, and expansion only hydrates the complete bound Registry entry.
Final size, state, semantic, Requirement, and Bundle validation remains the only
path to execution eligibility.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from validate_shared_widget_registry import (
    DEFAULT_REGISTRY,
    DEFAULT_SCHEMA,
    load_json,
    validate_registry,
)


SHORTLIST_VERSION = "0.1"
SHORTLIST_KIND = "nextgame-shared-widget-shortlist"
EXPANSION_KIND = "nextgame-shared-widget-expansion"
ERROR_KIND = "nextgame-shared-widget-operation-error"
TOKEN_PATTERN = re.compile(r"[a-z0-9_.:/-]+|[\u3400-\u9fff]+", re.IGNORECASE)
EXPANSION_BINDING_FIELDS = (
    "registryId",
    "registryVersion",
    "registryRevision",
    "registrySha256",
    "shortlistCanonicalSha256",
    "entryId",
    "interfaceSha256",
    "reuseContractSha256",
    "selectionClassification",
    "declaredHardConflict",
)


class ShortlistError(ValueError):
    """A deterministic request or authority failure safe to serialize to JSON."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _issue(code: str, path: str, message: str) -> dict[str, str]:
    return {"code": code, "path": path, "message": message}


def validate_authoritative_registry(
    registry_path: Path = DEFAULT_REGISTRY,
    registry_schema_path: Path = DEFAULT_SCHEMA,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fully validate the registry and linked evidence before any projection."""

    resolved_registry = registry_path.resolve()
    registry = load_json(resolved_registry)
    registry_schema = load_json(registry_schema_path.resolve())
    report = validate_registry(
        registry,
        registry_schema,
        registry_path=resolved_registry,
        check_linked_files=True,
    )
    if report.get("valid") is not True:
        codes = ", ".join(
            str(item.get("code"))
            for item in report.get("errors", [])
            if isinstance(item, dict)
        ) or "unknown"
        raise ShortlistError(f"Authoritative SharedWidgetRegistry validation failed: {codes}")
    return registry, report


def registry_binding(registry: dict[str, Any], registry_path: Path) -> dict[str, Any]:
    return {
        "registryId": registry["registryId"],
        "registryVersion": registry["version"],
        "registryRevision": registry["registryRevision"],
        "registrySha256": sha256_file(registry_path.resolve()),
    }


def entry_binding(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "entryId": entry["id"],
        "entryStatus": entry["status"],
        "assetPath": entry["assetPath"],
        "interfaceSha256": entry["interfaceSha256"],
        "reuseContractSha256": entry["reuseContractSha256"],
    }


def _unique_strings(value: Any, *, path: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ShortlistError(f"{path} must be an array of non-empty strings")
    result: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ShortlistError(f"{path}[{index}] must be a non-empty string")
        normalized = item.strip()
        if normalized not in seen:
            result.append(normalized)
            seen.add(normalized)
    return result


def normalize_request(request: dict[str, Any] | None) -> dict[str, Any]:
    raw = request or {}
    if not isinstance(raw, dict):
        raise ShortlistError("The shortlist request must be a JSON object")
    allowed = {
        "queryText",
        "explicitEntryIds",
        "explicitPaths",
        "assetKinds",
        "scopes",
        "ownerSystemFolders",
        "capabilityIds",
        "declaredHardConflicts",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ShortlistError(f"Unknown shortlist request fields: {', '.join(unknown)}")
    query_text = raw.get("queryText", "")
    if not isinstance(query_text, str):
        raise ShortlistError("queryText must be a string")
    conflicts = raw.get("declaredHardConflicts", [])
    if not isinstance(conflicts, list):
        raise ShortlistError("declaredHardConflicts must be an array")
    normalized_conflicts: list[dict[str, str]] = []
    seen_conflicts: set[tuple[str, str, str, str]] = set()
    required = ("entryId", "constraintId", "reason", "evidence")
    for index, conflict in enumerate(conflicts):
        if not isinstance(conflict, dict) or set(conflict) != set(required):
            raise ShortlistError(
                f"declaredHardConflicts[{index}] must contain exactly {', '.join(required)}"
            )
        values: dict[str, str] = {}
        for key in required:
            value = conflict.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ShortlistError(f"declaredHardConflicts[{index}].{key} must be non-empty")
            values[key] = value.strip()
        identity = tuple(values[key] for key in required)
        if identity not in seen_conflicts:
            normalized_conflicts.append(values)
            seen_conflicts.add(identity)
    return {
        "queryText": query_text.strip(),
        "explicitEntryIds": _unique_strings(raw.get("explicitEntryIds"), path="explicitEntryIds"),
        "explicitPaths": _unique_strings(raw.get("explicitPaths"), path="explicitPaths"),
        "assetKinds": _unique_strings(raw.get("assetKinds"), path="assetKinds"),
        "scopes": _unique_strings(raw.get("scopes"), path="scopes"),
        "ownerSystemFolders": _unique_strings(
            raw.get("ownerSystemFolders"), path="ownerSystemFolders"
        ),
        "capabilityIds": _unique_strings(raw.get("capabilityIds"), path="capabilityIds"),
        "declaredHardConflicts": normalized_conflicts,
    }


def _tokenize(value: str) -> set[str]:
    return {token.lower() for token in TOKEN_PATTERN.findall(value) if token}


def _entry_search_text(entry: dict[str, Any]) -> str:
    values: list[str] = [
        str(entry.get("id", "")),
        str(entry.get("assetPath", "")),
        str(entry.get("objectPath", "")),
        str(entry.get("generatedClassPath", "")),
        str(entry.get("assetKind", "")),
        str(entry.get("scope", "")),
        str(entry.get("ownerSystemFolder") or ""),
        str(entry.get("purpose", "")),
    ]
    values.extend(str(item) for item in entry.get("capabilityIds", []) if isinstance(item, str))
    return " ".join(values).lower()


def _soft_match_reasons(entry: dict[str, Any], request: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    query = request["queryText"].lower()
    if query:
        haystack = _entry_search_text(entry)
        query_tokens = _tokenize(query)
        haystack_tokens = _tokenize(haystack)
        if query in haystack or bool(query_tokens & haystack_tokens):
            reasons.append("query-text")
    if request["assetKinds"] and entry.get("assetKind") in request["assetKinds"]:
        reasons.append("asset-kind")
    if request["scopes"] and entry.get("scope") in request["scopes"]:
        reasons.append("scope")
    if request["ownerSystemFolders"] and entry.get("ownerSystemFolder") in request["ownerSystemFolders"]:
        reasons.append("owner-system-folder")
    capabilities = set(entry.get("capabilityIds", []))
    if request["capabilityIds"] and capabilities.intersection(request["capabilityIds"]):
        reasons.append("capability")
    return reasons


def _explicit_match(entry: dict[str, Any], request: dict[str, Any]) -> bool:
    if entry.get("id") in request["explicitEntryIds"]:
        return True
    paths = {
        entry.get("assetPath"),
        entry.get("objectPath"),
        entry.get("generatedClassPath"),
    }
    return bool(paths.intersection(request["explicitPaths"]))


def _validate_declared_conflicts(
    entries: list[dict[str, Any]],
    request: dict[str, Any],
) -> dict[str, list[dict[str, str]]]:
    entries_by_id = {entry["id"]: entry for entry in entries}
    by_entry: dict[str, list[dict[str, str]]] = {}
    for index, conflict in enumerate(request["declaredHardConflicts"]):
        entry = entries_by_id.get(conflict["entryId"])
        if entry is None:
            raise ShortlistError(
                f"declaredHardConflicts[{index}].entryId does not exist in the authoritative registry"
            )
        declared = set(entry.get("similarityContract", {}).get("hardConstraints", []))
        if conflict["constraintId"] not in declared:
            raise ShortlistError(
                f"declaredHardConflicts[{index}].constraintId is not a Registry-declared hard constraint for {entry['id']}"
            )
        by_entry.setdefault(entry["id"], []).append(conflict)
    return by_entry


def _fallback_reason(
    request: dict[str, Any],
    entries: list[dict[str, Any]],
    soft_matches: dict[str, list[str]],
) -> str:
    selectors_exist = bool(request["explicitEntryIds"] or request["explicitPaths"])
    matched_ids = {entry["id"] for entry in entries if _explicit_match(entry, request)}
    known_ids = {entry["id"] for entry in entries}
    known_paths = {
        path
        for entry in entries
        for path in (entry.get("assetPath"), entry.get("objectPath"), entry.get("generatedClassPath"))
        if isinstance(path, str)
    }
    if selectors_exist and (
        set(request["explicitEntryIds"]) - known_ids
        or set(request["explicitPaths"]) - known_paths
    ):
        return "unmatched-explicit-selector"
    has_soft_query = bool(
        request["queryText"]
        or request["assetKinds"]
        or request["scopes"]
        or request["ownerSystemFolders"]
        or request["capabilityIds"]
    )
    if not selectors_exist and not has_soft_query:
        return "no-query"
    soft_match_ids = {entry_id for entry_id, reasons in soft_matches.items() if reasons}
    combined_match_ids = matched_ids | soft_match_ids
    if not combined_match_ids:
        return "zero-match"
    if not selectors_exist and len(soft_match_ids) > 1:
        return "ambiguous-match"
    return "none"


def build_shortlist(
    request: dict[str, Any] | None = None,
    *,
    registry_path: Path = DEFAULT_REGISTRY,
    registry_schema_path: Path = DEFAULT_SCHEMA,
) -> dict[str, Any]:
    """Return one compact card per registry entry, without dropping any entry."""

    registry, _ = validate_authoritative_registry(registry_path, registry_schema_path)
    normalized = normalize_request(request)
    entries = registry.get("entries")
    if not isinstance(entries, list):
        raise ShortlistError("Validated registry unexpectedly has no entries array")
    conflicts_by_entry = _validate_declared_conflicts(entries, normalized)
    soft_matches = {
        entry["id"]: _soft_match_reasons(entry, normalized)
        for entry in entries
    }
    fallback_reason = _fallback_reason(normalized, entries, soft_matches)
    cards: list[dict[str, Any]] = []
    for entry in entries:
        explicit = _explicit_match(entry, normalized)
        hard_conflicts = conflicts_by_entry.get(entry["id"], [])
        reasons = list(soft_matches[entry["id"]])
        if explicit:
            reasons.insert(0, "explicit-selector")
            classification = "candidate"
        elif hard_conflicts:
            classification = "excluded"
            reasons.extend(
                f"declared-hard-conflict:{item['constraintId']}" for item in hard_conflicts
            )
        elif reasons:
            classification = "candidate"
        else:
            classification = "needsDetailedCheck"
            reasons.append("insufficient-discovery-information")

        blockers = ["discovery-card-not-authority"]
        if entry.get("status") != "active":
            blockers.append(f"registry-status-{entry.get('status')}")
        if hard_conflicts:
            blockers.append("declared-hard-conflict")
        card = {
            "entryId": entry["id"],
            "entryStatus": entry["status"],
            "assetPath": entry["assetPath"],
            "objectPath": entry["objectPath"],
            "generatedClassPath": entry["generatedClassPath"],
            "assetKind": entry["assetKind"],
            "scope": entry["scope"],
            "ownerSystemFolder": entry["ownerSystemFolder"],
            "purpose": entry["purpose"],
            "capabilityIds": list(entry["capabilityIds"]),
            "hardConstraintIds": list(entry["similarityContract"]["hardConstraints"]),
            "interfaceSha256": entry["interfaceSha256"],
            "reuseContractSha256": entry["reuseContractSha256"],
            "classification": classification,
            "matchReasons": list(dict.fromkeys(reasons)),
            "executionBlockers": list(dict.fromkeys(blockers)),
            "executable": False,
            "requiresExpansion": True,
        }
        cards.append(card)

    entry_ids = [entry["id"] for entry in entries]
    card_ids = [card["entryId"] for card in cards]
    if card_ids != entry_ids or len(card_ids) != len(set(card_ids)):
        raise ShortlistError("Shortlist coverage invariant failed: every Registry entry must appear exactly once")
    summary = {
        "total": len(cards),
        "candidate": sum(card["classification"] == "candidate" for card in cards),
        "needsDetailedCheck": sum(
            card["classification"] == "needsDetailedCheck" for card in cards
        ),
        "excluded": sum(card["classification"] == "excluded" for card in cards),
    }
    if summary["candidate"] + summary["needsDetailedCheck"] + summary["excluded"] != summary["total"]:
        raise ShortlistError("Shortlist classification invariant failed")
    return {
        "version": SHORTLIST_VERSION,
        "kind": SHORTLIST_KIND,
        "valid": True,
        "registryBinding": registry_binding(registry, registry_path),
        "request": normalized,
        "fallback": {"used": fallback_reason != "none", "reason": fallback_reason},
        "summary": summary,
        "cards": cards,
    }


def make_expansion_binding(shortlist: dict[str, Any], entry_id: str) -> dict[str, Any]:
    if shortlist.get("kind") != SHORTLIST_KIND or shortlist.get("valid") is not True:
        raise ShortlistError("A valid shortlist is required to create an expansion binding")
    registry = shortlist.get("registryBinding")
    cards = shortlist.get("cards")
    if not isinstance(registry, dict) or not isinstance(cards, list):
        raise ShortlistError("Shortlist binding fields are missing")
    card = next(
        (item for item in cards if isinstance(item, dict) and item.get("entryId") == entry_id),
        None,
    )
    if not isinstance(card, dict):
        raise ShortlistError(f"Shortlist has no entry {entry_id!r}")
    return {
        "registryId": registry["registryId"],
        "registryVersion": registry["registryVersion"],
        "registryRevision": registry["registryRevision"],
        "registrySha256": registry["registrySha256"],
        "shortlistCanonicalSha256": canonical_sha256(shortlist),
        "entryId": card["entryId"],
        "interfaceSha256": card["interfaceSha256"],
        "reuseContractSha256": card["reuseContractSha256"],
        "selectionClassification": card["classification"],
        "declaredHardConflict": "declared-hard-conflict" in card["executionBlockers"],
    }


def _normalize_expansion_binding(expected: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(expected, dict):
        raise ShortlistError("Expected expansion binding must be a JSON object")
    if set(expected) != set(EXPANSION_BINDING_FIELDS):
        missing = sorted(set(EXPANSION_BINDING_FIELDS) - set(expected))
        extra = sorted(set(expected) - set(EXPANSION_BINDING_FIELDS))
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if extra:
            details.append(f"unknown {', '.join(extra)}")
        raise ShortlistError("Expected expansion binding is not closed: " + "; ".join(details))
    normalized = dict(expected)
    if not isinstance(normalized["registryRevision"], int) or isinstance(
        normalized["registryRevision"], bool
    ):
        raise ShortlistError("registryRevision must be an integer")
    if normalized["selectionClassification"] not in {
        "candidate",
        "needsDetailedCheck",
        "excluded",
    }:
        raise ShortlistError("selectionClassification is invalid")
    if not isinstance(normalized["declaredHardConflict"], bool):
        raise ShortlistError("declaredHardConflict must be a boolean")
    for key in EXPANSION_BINDING_FIELDS:
        if key in {"registryRevision", "declaredHardConflict"}:
            continue
        if not isinstance(normalized[key], str) or not normalized[key]:
            raise ShortlistError(f"{key} must be a non-empty string")
    return normalized


def expand_entry(
    entry_id: str,
    expected_binding: dict[str, Any],
    *,
    shortlist: dict[str, Any],
    registry_path: Path = DEFAULT_REGISTRY,
    registry_schema_path: Path = DEFAULT_SCHEMA,
) -> dict[str, Any]:
    """Expand one complete entry only if its shortlist binding is still current."""

    registry, _ = validate_authoritative_registry(registry_path, registry_schema_path)
    expected = _normalize_expansion_binding(expected_binding)
    if not isinstance(entry_id, str) or not entry_id:
        raise ShortlistError("entry_id must be a non-empty string")
    current_registry = registry_binding(registry, registry_path)
    entry = next(
        (
            item
            for item in registry.get("entries", [])
            if isinstance(item, dict) and item.get("id") == entry_id
        ),
        None,
    )
    current_entry = entry_binding(entry) if isinstance(entry, dict) else None
    errors: list[dict[str, str]] = []
    if not isinstance(shortlist, dict):
        raise ShortlistError("A shortlist JSON object is required for bound expansion")
    try:
        regenerated_shortlist = build_shortlist(
            shortlist.get("request"),
            registry_path=registry_path,
            registry_schema_path=registry_schema_path,
        )
    except (KeyError, ShortlistError, TypeError, ValueError) as error:
        regenerated_shortlist = None
        errors.append(
            _issue(
                "shortlist.invalid_artifact",
                "$.shortlist",
                f"The supplied shortlist cannot be regenerated safely: {error}",
            )
        )
    if regenerated_shortlist is not None and shortlist != regenerated_shortlist:
        errors.append(
            _issue(
                "shortlist.stale_or_tampered_artifact",
                "$.shortlist",
                "The supplied shortlist does not exactly match a fresh projection of its request and the authoritative Registry.",
            )
        )
    actual_shortlist_hash = canonical_sha256(shortlist)
    if expected["shortlistCanonicalSha256"] != actual_shortlist_hash:
        errors.append(
            _issue(
                "shortlist.binding_digest",
                "$.requestedBinding.shortlistCanonicalSha256",
                f"Expected {expected['shortlistCanonicalSha256']!r}; supplied shortlist hashes to {actual_shortlist_hash!r}.",
            )
        )
    try:
        artifact_binding = make_expansion_binding(shortlist, entry_id)
    except (KeyError, ShortlistError, TypeError, ValueError) as error:
        artifact_binding = None
        errors.append(
            _issue(
                "shortlist.invalid_entry_binding",
                "$.shortlist.cards",
                str(error),
            )
        )
    if artifact_binding is not None and expected != artifact_binding:
        errors.append(
            _issue(
                "shortlist.binding_mismatch",
                "$.requestedBinding",
                "The requested binding does not exactly match the validated shortlist card.",
            )
        )
    if expected["entryId"] != entry_id:
        errors.append(
            _issue(
                "shortlist.entry_id_binding",
                "$.requestedBinding.entryId",
                f"Requested entry ID {entry_id!r} does not match bound ID {expected['entryId']!r}.",
            )
        )
    registry_pairs = (
        ("registryId", expected["registryId"], current_registry["registryId"]),
        ("registryVersion", expected["registryVersion"], current_registry["registryVersion"]),
        ("registryRevision", expected["registryRevision"], current_registry["registryRevision"]),
        ("registrySha256", expected["registrySha256"], current_registry["registrySha256"]),
    )
    for key, expected_value, current_value in registry_pairs:
        if expected_value != current_value:
            errors.append(
                _issue(
                    "shortlist.stale_registry_binding",
                    f"$.requestedBinding.{key}",
                    f"Expected {expected_value!r}; authoritative Registry now has {current_value!r}.",
                )
            )
    if not isinstance(entry, dict):
        errors.append(
            _issue(
                "shortlist.missing_entry",
                "$.requestedBinding.entryId",
                "The bound Registry entry no longer exists.",
            )
        )
    else:
        for key in ("interfaceSha256", "reuseContractSha256"):
            if expected[key] != entry[key]:
                errors.append(
                    _issue(
                        "shortlist.stale_entry_binding",
                        f"$.requestedBinding.{key}",
                        f"Expected {expected[key]!r}; authoritative entry now has {entry[key]!r}.",
                    )
                )
    valid = not errors
    blockers: list[str] = []
    if not valid:
        blockers.append("stale-or-invalid-binding")
    else:
        blockers.append("requires-authoritative-size-state-semantic-validation")
        if entry.get("status") != "active":
            blockers.append(f"registry-status-{entry.get('status')}")
        if expected["declaredHardConflict"]:
            blockers.append("declared-hard-conflict")
        if expected["selectionClassification"] == "needsDetailedCheck":
            blockers.append("needs-detailed-check")
        elif expected["selectionClassification"] == "excluded":
            blockers.append("excluded-by-declared-hard-conflict")
    executable = False
    return {
        "version": SHORTLIST_VERSION,
        "kind": EXPANSION_KIND,
        "valid": valid,
        "stale": not valid,
        "registryBinding": current_registry,
        "requestedBinding": expected,
        "entryBinding": current_entry,
        "errors": errors,
        "entry": entry if valid else None,
        "executable": executable,
        "executionBlockers": blockers,
    }


def _load_request(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    value = json.load(sys.stdin) if str(path) == "-" else load_json(path.resolve())
    if not isinstance(value, dict):
        raise ShortlistError(f"{path} must contain a JSON object")
    return value


def _operation_error(error: Exception) -> dict[str, Any]:
    return {
        "version": SHORTLIST_VERSION,
        "kind": ERROR_KIND,
        "valid": False,
        "errors": [_issue("shortlist.operation", "$", str(error))],
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--registry-schema", type=Path, default=DEFAULT_SCHEMA)
    subparsers = parser.add_subparsers(dest="operation", required=True)

    shortlist_parser = subparsers.add_parser("shortlist", help="Emit every compact discovery card")
    shortlist_parser.add_argument("--request-json", type=Path)
    shortlist_parser.add_argument("--query", default="")
    shortlist_parser.add_argument("--entry-id", action="append", default=[])
    shortlist_parser.add_argument("--path", action="append", default=[])
    shortlist_parser.add_argument("--asset-kind", action="append", default=[])
    shortlist_parser.add_argument("--scope", action="append", default=[])
    shortlist_parser.add_argument("--owner-system-folder", action="append", default=[])
    shortlist_parser.add_argument("--capability", action="append", default=[])
    shortlist_parser.add_argument("--output", type=Path)

    expand_parser = subparsers.add_parser("expand", help="Expand one fully bound Registry entry")
    expand_parser.add_argument("entry_id")
    expand_parser.add_argument("--shortlist-json", type=Path, required=True)
    expand_parser.add_argument("--expected-binding-json", type=Path)
    expand_parser.add_argument("--output", type=Path)

    args = parser.parse_args()
    try:
        if args.operation == "shortlist":
            request = _load_request(args.request_json)
            cli_request = {
                "queryText": args.query,
                "explicitEntryIds": args.entry_id,
                "explicitPaths": args.path,
                "assetKinds": args.asset_kind,
                "scopes": args.scope,
                "ownerSystemFolders": args.owner_system_folder,
                "capabilityIds": args.capability,
            }
            for key, value in cli_request.items():
                if value:
                    if key in request:
                        raise ShortlistError(
                            f"{key} cannot be supplied in both --request-json and CLI flags"
                        )
                    request[key] = value
            output = build_shortlist(
                request,
                registry_path=args.registry,
                registry_schema_path=args.registry_schema,
            )
        else:
            shortlist = _load_request(args.shortlist_json)
            expected_binding = (
                _load_request(args.expected_binding_json)
                if args.expected_binding_json is not None
                else make_expansion_binding(shortlist, args.entry_id)
            )
            output = expand_entry(
                args.entry_id,
                expected_binding,
                shortlist=shortlist,
                registry_path=args.registry,
                registry_schema_path=args.registry_schema,
            )
    except (OSError, json.JSONDecodeError, KeyError, ShortlistError, ValueError) as error:
        output = _operation_error(error)
    if args.output is not None:
        output_path = args.output.resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        compact = {
            "valid": output.get("valid") is True,
            "kind": output.get("kind"),
            "output": str(output_path),
            "sha256": sha256_file(output_path),
            "bytes": output_path.stat().st_size,
        }
        for key in ("fallback", "summary", "stale", "executable", "errors"):
            if key in output:
                compact[key] = output[key]
        print(json.dumps(compact, ensure_ascii=False, indent=2, allow_nan=False))
    else:
        print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if output.get("valid") is True else 1


if __name__ == "__main__":
    sys.exit(main())
