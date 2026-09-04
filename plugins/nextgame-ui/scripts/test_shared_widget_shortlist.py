#!/usr/bin/env python3
"""Regression tests for compact SharedWidgetRegistry discovery and bound expansion."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from shortlist_shared_widgets import (
    DEFAULT_REGISTRY,
    DEFAULT_SCHEMA,
    ShortlistError,
    build_shortlist,
    expand_entry,
    make_expansion_binding,
    validate_authoritative_registry,
)
from validate_shared_widget_registry import load_json


PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SHORTLIST_SCHEMA = PLUGIN_ROOT / "assets" / "shared-widget-shortlist.schema.json"
SCRIPT = Path(__file__).resolve().parent / "shortlist_shared_widgets.py"


def write_portable_candidate_registry(path: Path) -> tuple[Path, dict]:
    """Write a strict, self-contained Registry fixture with no project-local evidence links."""

    registry = load_json(DEFAULT_REGISTRY)
    candidate = copy.deepcopy(
        next(entry for entry in registry["entries"] if entry["id"] == "shared.common.bag-item")
    )
    registry["entries"] = [candidate]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return path, candidate


class SharedWidgetShortlistTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = load_json(DEFAULT_REGISTRY)
        cls.registry_report = {"valid": True, "errors": [], "warnings": []}
        cls.output_schema = load_json(SHORTLIST_SCHEMA)
        registry_schema = load_json(DEFAULT_SCHEMA)
        resource_registry = Registry().with_resource(
            registry_schema["$id"], Resource.from_contents(registry_schema)
        )
        cls.output_validator = Draft202012Validator(
            cls.output_schema,
            registry=resource_registry,
        )

    def setUp(self) -> None:
        authority = patch(
            "shortlist_shared_widgets.validate_authoritative_registry",
            return_value=(copy.deepcopy(self.registry), copy.deepcopy(self.registry_report)),
        )
        authority.start()
        self.addCleanup(authority.stop)

    def assert_schema_valid(self, value: dict) -> None:
        errors = sorted(self.output_validator.iter_errors(value), key=lambda item: list(item.path))
        self.assertEqual([], [error.message for error in errors])

    def card(self, shortlist: dict, entry_id: str) -> dict:
        return next(card for card in shortlist["cards"] if card["entryId"] == entry_id)

    def test_projection_schema_is_closed(self) -> None:
        self.assertTrue(self.registry_report["valid"])
        shortlist = build_shortlist()
        self.assert_schema_valid(shortlist)

        def visit(value: object) -> None:
            if isinstance(value, dict):
                if value.get("type") == "object":
                    self.assertIs(
                        False,
                        value.get("additionalProperties"),
                        msg=f"Open object schema: {value}",
                    )
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(self.output_schema)

    def test_authority_remains_linked_fail_closed(self) -> None:
        invalid_report = {
            "valid": False,
            "errors": [
                {
                    "code": "registry.extension_slot_migration_report",
                    "path": "$.entries[0]",
                    "message": "Linked migration evidence is unavailable.",
                }
            ],
            "warnings": [],
        }
        with patch(
            "shortlist_shared_widgets.validate_registry",
            return_value=invalid_report,
        ) as validate_mock:
            with self.assertRaises(ShortlistError):
                validate_authoritative_registry()
        self.assertIs(True, validate_mock.call_args.kwargs["check_linked_files"])

    def test_every_registry_entry_has_exactly_one_compact_non_executable_card(self) -> None:
        shortlist = build_shortlist({"queryText": "common"})
        registry_ids = [entry["id"] for entry in self.registry["entries"]]
        card_ids = [card["entryId"] for card in shortlist["cards"]]
        self.assertEqual(registry_ids, card_ids)
        self.assertEqual(len(card_ids), len(set(card_ids)))
        self.assertEqual(len(card_ids), shortlist["summary"]["total"])
        self.assertTrue(all(card["executable"] is False for card in shortlist["cards"]))
        self.assertTrue(all(card["requiresExpansion"] is True for card in shortlist["cards"]))
        self.assertLess(
            len(json.dumps(shortlist, ensure_ascii=False, separators=(",", ":"))),
            len(json.dumps(self.registry, ensure_ascii=False, separators=(",", ":"))),
        )

    def test_explicit_candidate_id_and_path_are_candidates_but_never_executable(self) -> None:
        candidate = next(entry for entry in self.registry["entries"] if entry["status"] == "candidate")
        by_id = build_shortlist({"explicitEntryIds": [candidate["id"]]})
        by_path = build_shortlist({"explicitPaths": [candidate["generatedClassPath"]]})
        for shortlist in (by_id, by_path):
            card = self.card(shortlist, candidate["id"])
            self.assertEqual("candidate", card["classification"])
            self.assertIn("explicit-selector", card["matchReasons"])
            self.assertFalse(card["executable"])
            self.assertIn("registry-status-candidate", card["executionBlockers"])
            expansion = expand_entry(
                candidate["id"],
                make_expansion_binding(shortlist, candidate["id"]),
                shortlist=shortlist,
            )
            self.assertTrue(expansion["valid"])
            self.assertFalse(expansion["executable"])
            self.assertIn("registry-status-candidate", expansion["executionBlockers"])
            self.assert_schema_valid(expansion)

    def test_missing_zero_and_ambiguous_queries_fall_back_to_all_cards(self) -> None:
        total = len(self.registry["entries"])
        no_query = build_shortlist()
        zero = build_shortlist({"queryText": "definitely-no-such-widget-928471"})
        ambiguous = build_shortlist({"scopes": ["project-common"]})
        self.assertEqual((True, "no-query"), (no_query["fallback"]["used"], no_query["fallback"]["reason"]))
        self.assertEqual((True, "zero-match"), (zero["fallback"]["used"], zero["fallback"]["reason"]))
        self.assertEqual(
            (True, "ambiguous-match"),
            (ambiguous["fallback"]["used"], ambiguous["fallback"]["reason"]),
        )
        for output in (no_query, zero, ambiguous):
            self.assertEqual(total, len(output["cards"]))
            self.assert_schema_valid(output)
        self.assertTrue(
            all(card["classification"] == "needsDetailedCheck" for card in zero["cards"])
        )

    def test_unmatched_explicit_selector_falls_back_without_dropping_cards(self) -> None:
        output = build_shortlist({"explicitEntryIds": ["shared.common.missing"]})
        self.assertEqual("unmatched-explicit-selector", output["fallback"]["reason"])
        self.assertEqual(len(self.registry["entries"]), len(output["cards"]))

    def test_only_registry_declared_hard_conflict_excludes(self) -> None:
        entry = next(entry for entry in self.registry["entries"] if entry["status"] == "active")
        conflict = {
            "entryId": entry["id"],
            "constraintId": entry["similarityContract"]["hardConstraints"][0],
            "reason": "Requested dimensions are incompatible with the fixed contract.",
            "evidence": "requirement.asset.referenceSize",
        }
        output = build_shortlist({"declaredHardConflicts": [conflict]})
        card = self.card(output, entry["id"])
        self.assertEqual("excluded", card["classification"])
        self.assertIn("declared-hard-conflict", card["executionBlockers"])
        self.assertEqual(1, output["summary"]["excluded"])
        self.assertTrue(
            all(
                item["classification"] != "excluded"
                for item in output["cards"]
                if item["entryId"] != entry["id"]
            )
        )

        explicit = build_shortlist(
            {"explicitEntryIds": [entry["id"]], "declaredHardConflicts": [conflict]}
        )
        explicit_card = self.card(explicit, entry["id"])
        self.assertEqual("candidate", explicit_card["classification"])
        self.assertFalse(explicit_card["executable"])
        self.assertIn("declared-hard-conflict", explicit_card["executionBlockers"])
        conflicted_expansion = expand_entry(
            entry["id"],
            make_expansion_binding(explicit, entry["id"]),
            shortlist=explicit,
        )
        self.assertTrue(conflicted_expansion["valid"])
        self.assertFalse(conflicted_expansion["executable"])
        self.assertIn("declared-hard-conflict", conflicted_expansion["executionBlockers"])
        self.assert_schema_valid(conflicted_expansion)

        invalid_conflict = copy.deepcopy(conflict)
        invalid_conflict["constraintId"] = "not-declared"
        with self.assertRaises(ShortlistError):
            build_shortlist({"declaredHardConflicts": [invalid_conflict]})

    def test_bound_active_entry_expands_full_and_hashes_match(self) -> None:
        active = next(entry for entry in self.registry["entries"] if entry["status"] == "active")
        shortlist = build_shortlist({"explicitEntryIds": [active["id"]]})
        binding = make_expansion_binding(shortlist, active["id"])
        expansion = expand_entry(active["id"], binding, shortlist=shortlist)
        self.assertTrue(expansion["valid"])
        self.assertFalse(expansion["stale"])
        self.assertFalse(expansion["executable"])
        self.assertIn(
            "requires-authoritative-size-state-semantic-validation",
            expansion["executionBlockers"],
        )
        self.assertEqual(active, expansion["entry"])
        self.assertEqual(active["interfaceSha256"], expansion["entryBinding"]["interfaceSha256"])
        self.assertEqual(
            active["reuseContractSha256"],
            expansion["entryBinding"]["reuseContractSha256"],
        )
        self.assertEqual(shortlist["registryBinding"], expansion["registryBinding"])
        self.assert_schema_valid(expansion)

    def test_stale_registry_and_entry_hashes_invalidate_expansion(self) -> None:
        active = next(entry for entry in self.registry["entries"] if entry["status"] == "active")
        shortlist = build_shortlist({"explicitEntryIds": [active["id"]]})
        original = make_expansion_binding(shortlist, active["id"])
        for key in (
            "registrySha256",
            "shortlistCanonicalSha256",
            "interfaceSha256",
            "reuseContractSha256",
        ):
            stale = dict(original)
            stale[key] = "0" * 64
            output = expand_entry(active["id"], stale, shortlist=shortlist)
            self.assertFalse(output["valid"], msg=key)
            self.assertTrue(output["stale"], msg=key)
            self.assertIsNone(output["entry"], msg=key)
            self.assertFalse(output["executable"], msg=key)
            self.assert_schema_valid(output)

    def test_tampered_shortlist_cannot_promote_an_entry(self) -> None:
        active = next(entry for entry in self.registry["entries"] if entry["status"] == "active")
        shortlist = build_shortlist()
        original_binding = make_expansion_binding(shortlist, active["id"])
        tampered = copy.deepcopy(shortlist)
        card = self.card(tampered, active["id"])
        card["classification"] = "candidate"
        card["matchReasons"] = ["forged"]
        forged_binding = make_expansion_binding(tampered, active["id"])
        for binding in (original_binding, forged_binding):
            expansion = expand_entry(active["id"], binding, shortlist=tampered)
            self.assertFalse(expansion["valid"])
            self.assertFalse(expansion["executable"])
            self.assertTrue(
                any(
                    error["code"] in {
                        "shortlist.binding_digest",
                        "shortlist.stale_or_tampered_artifact",
                    }
                    for error in expansion["errors"]
                )
            )
            self.assert_schema_valid(expansion)

    def test_registry_file_tamper_or_invalid_hash_cannot_reuse_old_binding(self) -> None:
        active = next(entry for entry in self.registry["entries"] if entry["status"] == "active")
        shortlist = build_shortlist({"explicitEntryIds": [active["id"]]})
        binding = make_expansion_binding(shortlist, active["id"])
        changed_registry = copy.deepcopy(self.registry)
        changed_registry["entries"][0]["purpose"] += "（测试变更）"

        with patch(
            "shortlist_shared_widgets.validate_authoritative_registry",
            return_value=(changed_registry, copy.deepcopy(self.registry_report)),
        ), patch(
            "shortlist_shared_widgets.sha256_file", return_value="1" * 64
        ):
            stale = expand_entry(active["id"], binding, shortlist=shortlist)
            self.assertFalse(stale["valid"])
            self.assertTrue(stale["stale"])
            self.assertTrue(
                any(error["code"] == "shortlist.stale_registry_binding" for error in stale["errors"])
            )

        with patch(
            "shortlist_shared_widgets.validate_authoritative_registry",
            side_effect=ShortlistError("Authoritative Registry hash validation failed"),
        ):
            with self.assertRaises(ShortlistError):
                build_shortlist()

    def test_cli_emits_json_and_preserves_full_coverage(self) -> None:
        case_root = (
            Path.cwd()
            / "Saved"
            / "CodexUITestTemp"
            / f"shared-widget-shortlist-cli-{uuid.uuid4().hex}"
        )
        registry_path, candidate = write_portable_candidate_registry(
            case_root / "registry-portable.json"
        )
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--registry",
                str(registry_path),
                "shortlist",
                "--entry-id",
                candidate["id"],
            ],
            cwd=PLUGIN_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, msg=result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(1, len(output["cards"]))
        self.assertEqual("candidate", self.card(output, candidate["id"])["classification"])
        self.assert_schema_valid(output)

        shortlist_path = case_root / "registry-shortlist-expand.json"
        shortlist_path.write_text(json.dumps(output, ensure_ascii=False), encoding="utf-8")
        try:
            expanded = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--registry",
                    str(registry_path),
                    "expand",
                    candidate["id"],
                    "--shortlist-json",
                    str(shortlist_path),
                ],
                cwd=PLUGIN_ROOT,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, expanded.returncode, msg=expanded.stderr)
            expanded_output = json.loads(expanded.stdout)
            self.assertEqual(candidate, expanded_output["entry"])
            self.assertFalse(expanded_output["executable"])
            self.assert_schema_valid(expanded_output)
        finally:
            shortlist_path.unlink(missing_ok=True)
            registry_path.unlink(missing_ok=True)
            case_root.rmdir()

    def test_cli_output_file_keeps_stdout_compact(self) -> None:
        case_root = (
            Path.cwd()
            / "Saved"
            / "CodexUITestTemp"
            / f"shared-widget-shortlist-output-{uuid.uuid4().hex}"
        )
        registry_path, _ = write_portable_candidate_registry(
            case_root / "registry-portable.json"
        )
        output_path = case_root / "registry-shortlist-cli.json"
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--registry",
                    str(registry_path),
                    "shortlist",
                    "--entry-id",
                    "shared.common.bag-item",
                    "--output",
                    str(output_path),
                ],
                cwd=PLUGIN_ROOT,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, result.returncode, msg=result.stderr)
            control = json.loads(result.stdout)
            artifact = load_json(output_path)
            self.assertNotIn("cards", control)
            self.assertEqual(output_path.resolve(), Path(control["output"]))
            self.assertEqual(1, len(artifact["cards"]))
            self.assert_schema_valid(artifact)
        finally:
            output_path.unlink(missing_ok=True)
            registry_path.unlink(missing_ok=True)
            case_root.rmdir()


if __name__ == "__main__":
    unittest.main()
