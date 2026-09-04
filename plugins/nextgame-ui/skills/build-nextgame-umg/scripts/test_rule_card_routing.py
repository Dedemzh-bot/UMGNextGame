#!/usr/bin/env python3
"""Regression tests for deterministic, fail-safe UMG rule-card routing."""

from __future__ import annotations

import os
import shutil
import sys
import unittest
import uuid
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from route_rule_cards import (  # noqa: E402
    DEFAULT_REFERENCES,
    DEFAULT_ROUTING,
    DEFAULT_RULES,
    FALLBACK_WORKFLOW_DOCS,
    RequiredInputError,
    _build_rule_card_pack_for_test,
    _validate_rule_card_pack_for_test,
    build_rule_card_pack,
    extract_exact_section,
    routed_sections,
    validate_rule_card_pack,
    write_json,
)
from select_rules import load_json, select_rules  # noqa: E402


SKILL_ROOT = SCRIPT_DIR.parent
PLUGIN_ROOT = SKILL_ROOT.parents[1].resolve()
EXAMPLE_LAYOUT = SKILL_ROOT / "assets" / "example-layout-spec.json"
FIGHT_LAYOUT = SKILL_ROOT / "assets" / "example-fight-child-layout-spec.json"
TEST_TEMP_ROOT = Path(
    os.environ.get(
        "NEXTGAME_UI_TEST_TMPDIR",
        str(Path.cwd() / "Saved" / "CodexUITestTemp"),
    )
).resolve() / "rule-card-routing"
if TEST_TEMP_ROOT == PLUGIN_ROOT or TEST_TEMP_ROOT.is_relative_to(PLUGIN_ROOT):
    raise RuntimeError("NEXTGAME_UI_TEST_TMPDIR must place rule-card fixtures outside the plugin tree")


class RuleCardRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp_dir = TEST_TEMP_ROOT / f"case-{uuid.uuid4().hex}"
        self.temp_dir.mkdir()

    def tearDown(self) -> None:
        resolved_root = TEST_TEMP_ROOT.resolve()
        resolved_case = self.temp_dir.resolve()
        if resolved_case != resolved_root and resolved_case.is_relative_to(resolved_root):
            shutil.rmtree(resolved_case)
        try:
            TEST_TEMP_ROOT.rmdir()
        except OSError:
            pass

    def copy_references(self) -> Path:
        destination = self.temp_dir / "references"
        shutil.copytree(DEFAULT_REFERENCES, destination)
        return destination

    def test_routed_ids_and_order_exactly_match_existing_selector(self) -> None:
        spec = load_json(EXAMPLE_LAYOUT)
        index = load_json(DEFAULT_RULES)
        legacy = select_rules(spec, index)
        pack = build_rule_card_pack(EXAMPLE_LAYOUT)

        self.assertEqual("routed", pack["routingMode"])
        self.assertEqual([rule["id"] for rule in legacy], pack["selectedRuleIds"])
        self.assertEqual(pack["selectedRuleIds"], [card["id"] for card in pack["ruleCards"]])
        self.assertEqual([], pack["fallbackAuthorityRuleIds"])
        self.assertEqual([], pack["fallbackAuthorityRuleCards"])
        self.assertTrue(pack["machineValidation"]["machineValidatorsEnabled"])
        self.assertFalse(pack["machineValidation"]["routingMayDisableValidators"])

    def test_every_selected_error_rule_has_a_card(self) -> None:
        spec = load_json(EXAMPLE_LAYOUT)
        selected = select_rules(spec, load_json(DEFAULT_RULES))
        pack = build_rule_card_pack(EXAMPLE_LAYOUT)
        expected_errors = [rule["id"] for rule in selected if rule.get("severity") == "error"]
        actual_errors = [card["id"] for card in pack["ruleCards"] if card["severity"] == "error"]
        self.assertEqual(expected_errors, actual_errors)

    def test_rule_card_metadata_preserves_selector_severity(self) -> None:
        spec = load_json(EXAMPLE_LAYOUT)
        selected = select_rules(spec, load_json(DEFAULT_RULES))
        pack = build_rule_card_pack(EXAMPLE_LAYOUT)
        self.assertEqual(
            [(rule["id"], rule["severity"]) for rule in selected],
            [(card["id"], card["severity"]) for card in pack["ruleCards"]],
        )

    def test_invalid_rule_index_is_a_hard_authority_failure(self) -> None:
        index = load_json(DEFAULT_RULES)
        index["rules"] = index["rules"][:-1]
        index_path = self.temp_dir / "invalid-rule-index.json"
        write_json(index_path, index)
        with self.assertRaises(RequiredInputError):
            _build_rule_card_pack_for_test(
                EXAMPLE_LAYOUT,
                rules_path=index_path,
            )

    def test_same_exact_section_is_deduplicated_without_losing_owners(self) -> None:
        pack = build_rule_card_pack(FIGHT_LAYOUT, stages=["build-planning"])
        matching = [
            section
            for section in pack["detailSections"]
            if section["file"] == "fight-ui.md" and section["heading"] == "## Explicit fight rules"
        ]
        self.assertEqual(1, len(matching))
        self.assertEqual(
            ["fight.reference-authority", "fight.child-module"],
            matching[0]["ruleIds"],
        )

    def test_missing_heading_falls_back_to_complete_documents(self) -> None:
        references = self.copy_references()
        target = references / "common-widget-rules.md"
        text = target.read_text(encoding="utf-8")
        self.assertIn("## Even font sizes", text)
        target.write_text(text.replace("## Even font sizes", "## Removed even font sizes", 1), encoding="utf-8")

        pack = _build_rule_card_pack_for_test(
            EXAMPLE_LAYOUT,
            references_dir=references,
        )
        self.assertEqual("fallback-full", pack["routingMode"])
        self.assertTrue(any(reason.startswith("heading-missing:common-widget-rules.md") for reason in pack["fallbackReasons"]))
        self.assertEqual([], pack["detailSections"])
        self.assertTrue(set(FALLBACK_WORKFLOW_DOCS).issubset({item["file"] for item in pack["fullDocuments"]}))

    def test_duplicate_heading_falls_back_to_complete_documents(self) -> None:
        references = self.copy_references()
        target = references / "common-widget-rules.md"
        with target.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write("\n## Even font sizes\nDuplicate sentinel.\n")

        pack = _build_rule_card_pack_for_test(
            EXAMPLE_LAYOUT,
            references_dir=references,
        )
        self.assertEqual("fallback-full", pack["routingMode"])
        self.assertTrue(any(reason.startswith("heading-duplicate:common-widget-rules.md") for reason in pack["fallbackReasons"]))

    def test_markdown_headings_inside_fences_do_not_truncate_or_duplicate_sections(self) -> None:
        markdown = (
            "## Routed section\n"
            "before\n"
            "```text\n"
            "## Routed section\n"
            "## Looks like a sibling but is code\n"
            "```\n"
            "after\n"
            "## Actual sibling\n"
            "outside\n"
        )
        section, error = extract_exact_section(markdown, "## Routed section")
        self.assertIsNone(error)
        self.assertEqual(
            "## Routed section\nbefore\n```text\n## Routed section\n"
            "## Looks like a sibling but is code\n```\nafter\n",
            section,
        )

    def test_nested_sections_keep_parent_text_and_all_owners_in_first_seen_order(self) -> None:
        markdown = (
            "# Synthetic reference\n"
            "## Parent\n"
            "parent body\n"
            "```md\n"
            "### Child\n"
            "fake heading in code\n"
            "```\n"
            "### Child\n"
            "child body\n"
            "## Next\n"
            "outside\n"
        )
        parent, parent_error = extract_exact_section(markdown, "## Parent")
        child, child_error = extract_exact_section(markdown, "### Child")
        self.assertIsNone(parent_error)
        self.assertIsNone(child_error)
        assert parent is not None and child is not None

        sections = routed_sections(
            [{"id": "rule.child"}, {"id": "rule.parent"}],
            {
                "rule.child": {
                    "detailRefs": [{"file": "synthetic.md", "heading": "### Child"}],
                },
                "rule.parent": {
                    "detailRefs": [{"file": "synthetic.md", "heading": "## Parent"}],
                },
            },
            [
                {
                    "id": "workflow.parent",
                    "detailRefs": [{"file": "synthetic.md", "heading": "## Parent"}],
                },
                {
                    "id": "workflow.child",
                    "detailRefs": [{"file": "synthetic.md", "heading": "### Child"}],
                },
            ],
            {
                ("synthetic.md", "## Parent"): parent,
                ("synthetic.md", "### Child"): child,
            },
            {"synthetic.md": (markdown.encode("utf-8"), markdown)},
        )

        self.assertEqual(1, len(sections))
        retained = sections[0]
        self.assertEqual("## Parent", retained["heading"])
        self.assertEqual(parent, retained["text"])
        self.assertEqual(len(parent.encode("utf-8")), retained["bytes"])
        self.assertEqual(["rule.child", "rule.parent"], retained["ruleIds"])
        self.assertEqual(
            ["workflow.parent", "workflow.child"],
            retained["workflowGuardIds"],
        )

    def test_sibling_sections_are_not_merged(self) -> None:
        markdown = (
            "## First\n"
            "first body\n"
            "## Second\n"
            "second body\n"
        )
        first, first_error = extract_exact_section(markdown, "## First")
        second, second_error = extract_exact_section(markdown, "## Second")
        self.assertIsNone(first_error)
        self.assertIsNone(second_error)
        assert first is not None and second is not None

        sections = routed_sections(
            [{"id": "rule.first"}, {"id": "rule.second"}],
            {
                "rule.first": {
                    "detailRefs": [{"file": "siblings.md", "heading": "## First"}],
                },
                "rule.second": {
                    "detailRefs": [{"file": "siblings.md", "heading": "## Second"}],
                },
            },
            [],
            {
                ("siblings.md", "## First"): first,
                ("siblings.md", "## Second"): second,
            },
            {"siblings.md": (markdown.encode("utf-8"), markdown)},
        )

        self.assertEqual(["## First", "## Second"], [item["heading"] for item in sections])
        self.assertEqual([["rule.first"], ["rule.second"]], [item["ruleIds"] for item in sections])

    def test_missing_routing_card_falls_back(self) -> None:
        routing = load_json(DEFAULT_ROUTING)
        routing["cards"] = routing["cards"][1:]
        routing_path = self.temp_dir / "rule-card-routing.json"
        write_json(routing_path, routing)

        pack = _build_rule_card_pack_for_test(
            EXAMPLE_LAYOUT,
            routing_path=routing_path,
        )
        self.assertEqual("fallback-full", pack["routingMode"])
        self.assertIn("routing-card-coverage-mismatch", pack["fallbackReasons"])
        self.assertEqual(pack["selectedRuleIds"], pack["fallbackAuthorityRuleIds"])
        self.assertEqual(pack["byteTelemetry"]["instructionBytes"], pack["byteTelemetry"]["fullFallbackInstructionBytes"])

    def test_workflow_guard_authority_mutation_falls_back_to_canonical_guards(self) -> None:
        routing = load_json(DEFAULT_ROUTING)
        guard = next(
            item
            for item in routing["workflowGuards"]
            if item["id"] == "workflow.post-build-acceptance"
        )
        guard["stages"] = ["build-planning"]
        guard["severity"] = "warning"
        guard["detailRefs"] = [
            {
                "file": "requirement-build-handoff.md",
                "heading": "## Review gate",
            }
        ]
        routing_path = self.temp_dir / "mutated-routing.json"
        write_json(routing_path, routing)

        pack = _build_rule_card_pack_for_test(
            EXAMPLE_LAYOUT,
            routing_path=routing_path,
            stages=["build-verification"],
        )
        self.assertEqual("fallback-full", pack["routingMode"])
        self.assertIn(
            "workflow-guard-authority-mismatch:workflow.post-build-acceptance",
            pack["fallbackReasons"],
        )
        restored = next(
            card
            for card in pack["workflowGuardCards"]
            if card["id"] == "workflow.post-build-acceptance"
        )
        self.assertEqual(["build-verification"], restored["stages"])
        self.assertEqual("error", restored["severity"])

    def test_production_api_rejects_caller_supplied_authority_paths(self) -> None:
        routing_copy = self.temp_dir / "routing-copy.json"
        shutil.copy2(DEFAULT_ROUTING, routing_copy)
        with self.assertRaises(TypeError):
            build_rule_card_pack(EXAMPLE_LAYOUT, routing_path=routing_copy)

    def test_missing_routing_config_falls_back(self) -> None:
        pack = _build_rule_card_pack_for_test(
            EXAMPLE_LAYOUT,
            routing_path=self.temp_dir / "missing.json",
        )
        self.assertEqual("fallback-full", pack["routingMode"])
        self.assertIn("routing-config-missing", pack["fallbackReasons"])
        self.assertIsNone(pack["bindings"]["routingConfig"]["sha256"])
        self.assertTrue(set(FALLBACK_WORKFLOW_DOCS).issubset({item["file"] for item in pack["fullDocuments"]}))

    def test_unknown_profile_falls_back(self) -> None:
        layout = load_json(EXAMPLE_LAYOUT)
        layout["profile"]["assetKind"] = "future-screen-kind"
        layout_path = self.temp_dir / "unknown-profile.json"
        write_json(layout_path, layout)

        pack = build_rule_card_pack(layout_path)
        self.assertEqual("fallback-full", pack["routingMode"])
        self.assertIn("layout-profile-unknown", pack["fallbackReasons"])
        legacy = select_rules(layout, load_json(DEFAULT_RULES))
        self.assertEqual([rule["id"] for rule in legacy], pack["selectedRuleIds"])
        self.assertEqual(50, len(pack["fallbackAuthorityRuleIds"]))

    def test_unknown_layout_schema_version_falls_back(self) -> None:
        layout = load_json(EXAMPLE_LAYOUT)
        layout["version"] = "future-version"
        layout_path = self.temp_dir / "unknown-schema.json"
        write_json(layout_path, layout)

        pack = build_rule_card_pack(layout_path)
        self.assertEqual("fallback-full", pack["routingMode"])
        self.assertIn("layout-schema-version-unknown", pack["fallbackReasons"])
        legacy = select_rules(layout, load_json(DEFAULT_RULES))
        self.assertEqual([rule["id"] for rule in legacy], pack["selectedRuleIds"])
        self.assertEqual(50, len(pack["fallbackAuthorityRuleIds"]))
        source_types = [card["sourceType"] for card in pack["fallbackAuthorityRuleCards"]]
        self.assertEqual(
            source_types,
            sorted(
                source_types,
                key={"explicit": 0, "observed": 1, "baseline": 2}.__getitem__,
            ),
        )
        index_rules = load_json(DEFAULT_RULES)["rules"]
        for source_type in ("explicit", "observed", "baseline"):
            expected_within_group = [
                rule["id"]
                for rule in index_rules
                if rule.get("sourceType", "baseline") == source_type
            ]
            actual_within_group = [
                card["id"]
                for card in pack["fallbackAuthorityRuleCards"]
                if card["sourceType"] == source_type
            ]
            self.assertEqual(expected_within_group, actual_within_group)
        authoritative_files = {
            rule["reference"]
            for rule in load_json(DEFAULT_RULES)["rules"]
            if isinstance(rule.get("reference"), str)
        }
        self.assertTrue(
            authoritative_files.issubset({item["file"] for item in pack["fullDocuments"]})
        )

    def test_pack_is_deterministic_and_validates_by_recomputation(self) -> None:
        first = build_rule_card_pack(EXAMPLE_LAYOUT, stages=["build-planning"])
        second = build_rule_card_pack(EXAMPLE_LAYOUT, stages=["build-planning"])
        self.assertEqual(first, second)
        pack_path = self.temp_dir / "rule-card-pack.json"
        write_json(pack_path, first)

        report = validate_rule_card_pack(
            pack_path,
            EXAMPLE_LAYOUT,
            stages=["build-planning"],
        )
        self.assertTrue(report["valid"], report)

    def test_pack_validation_requires_external_expected_stage(self) -> None:
        pack = build_rule_card_pack(EXAMPLE_LAYOUT, stages=["build-planning"])
        pack_path = self.temp_dir / "planning-pack.json"
        write_json(pack_path, pack)
        missing_stage = validate_rule_card_pack(pack_path, EXAMPLE_LAYOUT)
        self.assertFalse(missing_stage["valid"])
        self.assertEqual("stage.expected-required", missing_stage["errors"][0]["code"])
        wrong_stage = validate_rule_card_pack(
            pack_path,
            EXAMPLE_LAYOUT,
            stages=["build-verification"],
        )
        self.assertFalse(wrong_stage["valid"])
        self.assertIn(
            "pack.stale-or-tampered",
            {item["code"] for item in wrong_stage["errors"]},
        )

    def test_tampered_pack_is_rejected(self) -> None:
        pack = build_rule_card_pack(EXAMPLE_LAYOUT, stages=["build-planning"])
        pack["ruleCards"][0]["summary"] += " tampered"
        pack_path = self.temp_dir / "tampered.json"
        write_json(pack_path, pack)

        report = validate_rule_card_pack(
            pack_path,
            EXAMPLE_LAYOUT,
            stages=["build-planning"],
        )
        self.assertFalse(report["valid"])
        self.assertEqual({"pack.digest", "pack.stale-or-tampered"}, {item["code"] for item in report["errors"]})

    def test_reference_change_makes_existing_pack_stale(self) -> None:
        references = self.copy_references()
        pack = _build_rule_card_pack_for_test(
            EXAMPLE_LAYOUT,
            references_dir=references,
            stages=["build-planning"],
        )
        pack_path = self.temp_dir / "rule-card-pack.json"
        write_json(pack_path, pack)
        target = references / "common-widget-rules.md"
        with target.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write("\n<!-- changed after routing -->\n")

        report = _validate_rule_card_pack_for_test(
            pack_path,
            EXAMPLE_LAYOUT,
            references_dir=references,
            stages=["build-planning"],
        )
        self.assertFalse(report["valid"])
        self.assertIn("pack.stale-or-tampered", {item["code"] for item in report["errors"]})

    def test_routed_material_is_smaller_without_token_estimation(self) -> None:
        pack = build_rule_card_pack(EXAMPLE_LAYOUT)
        telemetry = pack["byteTelemetry"]
        self.assertLess(telemetry["instructionBytes"], telemetry["fullFallbackInstructionBytes"])
        self.assertGreater(telemetry["savedBytes"], 0)
        self.assertGreater(telemetry["reductionPercent"], 0)
        self.assertIsNone(pack["tokenTelemetry"]["actualInputTokens"])
        self.assertIsNone(pack["tokenTelemetry"]["actualOutputTokens"])
        self.assertEqual("not-available-no-byte-to-token-conversion", pack["tokenTelemetry"]["measurementStatus"])


if __name__ == "__main__":
    unittest.main()
