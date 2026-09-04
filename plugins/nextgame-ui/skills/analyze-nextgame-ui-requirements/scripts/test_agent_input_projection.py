#!/usr/bin/env python3
"""Regression tests for no-history role packets and domain projections."""

from __future__ import annotations

import copy
import json
import shutil
import sys
import unittest
import uuid
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _contract_common import ASSETS_ROOT, canonical_sha256, compute_request_input_digest, load_json, sha256_file
from prepare_agent_inputs import (
    ALL_ROLES,
    AgentInputError,
    CONTEXT_ROLES,
    DEFAULT_PACKET_SCHEMA,
    DEFAULT_ROLE_CARDS,
    DISCOVERY_ROLES,
    PLUGIN_ROOT,
    REVIEW_ROLES,
    build_context_projection,
    build_registry_shortlist,
    prepare_packets,
    validate_role_packet,
)
from validate_agent_findings import DEFAULT_SCHEMA as FINDINGS_SCHEMA
from validate_agent_findings import validate_agent_findings
from validate_request_packet import DEFAULT_SCHEMA as REQUEST_SCHEMA


class AgentInputProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        test_temp_root = Path.cwd() / "Saved" / "CodexUITestTemp"
        test_temp_root.mkdir(parents=True, exist_ok=True)
        self.root = test_temp_root / f"nextgame-agent-input-{uuid.uuid4().hex}"
        self.root.mkdir()
        rule = self.root / "project-rule.md"
        rule.write_text("# Fixture rule\n\nUse even sizes.\n", encoding="utf-8")
        image = self.root / "reference.png"
        image.write_bytes(b"opaque-image-fixture")
        self.request_path = self.root / "request-packet.json"
        request = {
            "version": "0.1",
            "requestId": "fixture-request",
            "inputDigest": "0" * 64,
            "userRequest": {"originalText": ["Build the supplied screen."], "language": "en"},
            "sources": [
                {
                    "sourceKey": "source-user-text",
                    "kind": "user-text",
                    "locatorKind": "inline",
                    "description": "Exact user request.",
                    "content": "Build the supplied screen.",
                },
                {
                    "sourceKey": "source-image",
                    "kind": "image",
                    "locatorKind": "local-file",
                    "description": "Opaque image fixture.",
                    "path": str(image.resolve()),
                    "contentSha256": sha256_file(image),
                    "imageSize": [2560, 1440],
                },
                {
                    "sourceKey": "source-project-rule",
                    "kind": "project-rule",
                    "locatorKind": "local-file",
                    "description": "Project rule fixture.",
                    "path": str(rule.resolve()),
                    "contentSha256": sha256_file(rule),
                },
            ],
            "targetHints": {
                "system": "fixture",
                "systemFolder": "Fixture",
                "assetKind": "screen",
                "mode": "production",
                "designCanvas": [2560, 1440],
                "targetAssetPaths": ["/Game/UI/UMG/Fixture/umg_fixture"],
                "productionAuthorized": True,
            },
            "projectRuleRefs": [{"path": str(rule.resolve()), "section": "Fixture rule", "required": True}],
        }
        request["inputDigest"] = compute_request_input_digest(request)
        self.request = request
        self.request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")

        self.context_path = self.root / "contexts" / "normalized-context.json"
        self.context_path.parent.mkdir(parents=True)
        context = {
            "version": "0.1",
            "contextKind": "normalized-first-round",
            "authoritative": True,
            "notice": "Fixture normalized context.",
            "requestId": request["requestId"],
            "inputDigest": request["inputDigest"],
            "sources": [
                {"id": "src.user", "sourceKey": "source-user-text"},
                {"id": "src.image", "sourceKey": "source-image"},
                {"id": "src.rule", "sourceKey": "source-project-rule"},
            ],
            "target": {"system": "fixture", "assetKind": "screen"},
            "evidence": [{"id": "ev.image", "sourceKey": "source-image"}],
            "preliminaryClaims": [{"id": "cl.scope", "evidenceIds": ["ev.image"]}],
            "regions": [{"id": "reg.main", "evidenceIds": ["ev.image"]}],
            "componentFamilies": [{"id": "fam.item", "evidenceIds": ["ev.image"]}],
            "elements": [{"id": "el.item", "familyId": "fam.item", "evidenceIds": ["ev.image"]}],
            "collections": [{"id": "col.items"}],
            "runtimeFields": [{"id": "field.value"}],
            "responsiveIntents": [{"id": "resp.main"}],
            "stateModels": [{"id": "state-model.item"}],
            "plannedAssets": [{"id": "asset.screen"}],
            "reuseCandidates": [{"id": "reuse.none"}],
            "acceptanceCriteria": [{"id": "accept.coverage"}],
            "questions": [{"id": "q.none"}],
            "firstRoundTrace": {
                "aliases": [{"canonicalId": "el.item"}],
                "findingsRefs": [],
                "auditNotes": ["trace-detail-" * 400],
            },
        }
        self.context = context
        self.context_path.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
        self.draft_path = self.root / "ui-requirement.draft.json"
        draft = load_json(ASSETS_ROOT / "example-composite-tabs-requirement.json")
        draft["requestId"] = request["requestId"]
        draft["inputDigest"] = request["inputDigest"]
        draft["request"]["originalText"] = copy.deepcopy(request["userRequest"]["originalText"])
        draft["reviewGate"] = {
            "required": True,
            "status": "pending",
            "acceptedClaimIds": [],
            "rejectedClaimIds": [],
        }
        for findings_input in draft["normalization"]["findingsInputs"]:
            findings_input["inputDigest"] = request["inputDigest"]
        self.draft_path.write_text(
            json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.coverage_path = self.root / "coverage-report.json"
        coverage_report = {
            "version": "0.1",
            "status": "no-medium-high-gaps-detected",
            "sourceSha256": self.request["sources"][1]["contentSha256"],
            "summary": {
                "candidateCount": 0,
                "highCount": 0,
                "mediumCount": 0,
                "mappedCount": 0,
                "excludedCount": 0,
                "unresolvedCount": 0,
                "uncoveredHighOrMediumSalienceCount": 0,
                "openReviewClusterCount": 0,
            },
            "gate": {
                "dispositionCompleteness": 0,
                "nonExcludedMappingRecallMediumHigh": 0,
                "weightedUncoveredRatioMediumHigh": 0,
                "uncoveredHighOrMediumSalienceCount": 0,
                "passesDraftGate": True,
                "note": "Fixture coverage gate.",
            },
            "uncoveredCandidates": [],
            "reviewClusters": [],
            "knownLimitations": ["Fixture report."],
        }
        self.coverage_path.write_text(
            json.dumps(coverage_report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.root)

    def prepare(
        self,
        output_name: str = "agent-inputs",
        *,
        roles: tuple[str, ...] = ALL_ROLES,
        role_attachments: dict[str, list[tuple[str, Path]]] | None = None,
        coverage_evidence: Path | None = None,
    ) -> dict:
        return prepare_packets(
            request_path=self.request_path,
            output_dir=self.root / output_name,
            roles=roles,
            context_path=self.context_path,
            draft_requirement=self.draft_path,
            coverage_evidence=coverage_evidence,
            attachments=[],
            role_cards_path=DEFAULT_ROLE_CARDS,
            packet_schema_path=DEFAULT_PACKET_SCHEMA,
            role_attachments=role_attachments,
        )

    def test_all_nine_packets_are_no_history_and_valid(self) -> None:
        summary = self.prepare()
        self.assertEqual(len(summary["roles"]), 9)
        for record in summary["roles"]:
            packet_path = self.root / record["packetRef"]
            packet = load_json(packet_path)
            self.assertEqual(packet["historyPolicy"], {"mode": "none", "forkTurns": "none", "inheritsConversation": False})
            validation = validate_role_packet(
                packet,
                packet_path=packet_path,
                request_root=self.root,
                request_path=self.request_path,
                base_context_path=self.context_path,
                draft_requirement_path=self.draft_path,
                packet_schema=load_json(DEFAULT_PACKET_SCHEMA),
            )
            self.assertTrue(validation["valid"], validation)
            additional_kinds = {
                item["kind"] for item in packet.get("additionalInputs", [])
            }
            self.assertNotIn("draft-requirement", additional_kinds)
            if packet["agentRole"] in REVIEW_ROLES:
                self.assertIn("review-view", additional_kinds)
                binding = next(
                    item
                    for item in packet["additionalInputs"]
                    if item["kind"] == "review-view"
                )
                view = load_json(self.root / binding["ref"])
                self.assertEqual(view["agentRole"], packet["agentRole"])

    def test_agent_view_does_not_disclose_authoritative_sidecar_paths(self) -> None:
        self.prepare()
        request_name = self.request_path.name
        base_context_name = self.context_path.name
        draft_name = self.draft_path.name
        for role in ALL_ROLES:
            packet = load_json(self.root / "agent-inputs" / f"{role}.json")
            self.assertEqual(set(packet["requestBinding"]), {"sha256"})
            packet_text = json.dumps(packet, ensure_ascii=False)
            self.assertNotIn(request_name, packet_text)
            self.assertNotIn(base_context_name, packet_text)
            self.assertNotIn(draft_name, packet_text)
            if role in CONTEXT_ROLES:
                projection = load_json(self.root / packet["context"]["ref"])
                self.assertNotIn("baseContextRef", projection["projection"])
                self.assertNotIn("baseContextFileSha256", projection["projection"])
            if role in REVIEW_ROLES:
                view_binding = next(
                    item
                    for item in packet["additionalInputs"]
                    if item["kind"] == "review-view"
                )
                view_text = json.dumps(
                    load_json(self.root / view_binding["ref"]), ensure_ascii=False
                )
                self.assertNotIn(draft_name, view_text)

    def test_round_one_source_isolation_is_exact(self) -> None:
        self.prepare()
        expected = {
            "visual-structure": ["source-image"],
            "text-requirements": ["source-user-text"],
            "project-pattern": ["source-project-rule"],
        }
        for role in DISCOVERY_ROLES:
            packet = load_json(self.root / "agent-inputs" / f"{role}.json")
            self.assertEqual(packet["sourceScope"], expected[role])
            self.assertEqual([source["sourceKey"] for source in packet["sources"]], expected[role])
            self.assertNotIn("context", packet)

    def test_unlisted_role_attachment_kind_is_rejected(self) -> None:
        shortlist = self.root / "shared-widget-shortlist.json"
        shortlist.write_text('{"valid":true}\n', encoding="utf-8")
        with self.assertRaises(AgentInputError):
            self.prepare(
                roles=("project-pattern",),
                role_attachments={
                    "project-pattern": [("domain-context", shortlist)],
                }
            )

    def test_registry_shortlist_attachment_is_recomputed_from_bound_source(self) -> None:
        registry_source = PLUGIN_ROOT / "assets" / "shared-widget-registry.json"
        registry_copy = self.root / "shared-widget-registry.json"
        registry = load_json(registry_source)
        registry["entries"] = [
            copy.deepcopy(
                next(
                    entry
                    for entry in registry["entries"]
                    if entry["id"] == "shared.common.bag-item"
                )
            )
        ]
        registry_copy.write_text(
            json.dumps(registry, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        self.request["sources"].append(
            {
                "sourceKey": "source-shared-widget-registry",
                "kind": "project-asset",
                "locatorKind": "local-file",
                "description": "Immutable Registry fixture.",
                "path": str(registry_copy.resolve()),
                "contentSha256": sha256_file(registry_copy),
            }
        )
        self.request["inputDigest"] = compute_request_input_digest(self.request)
        self.request_path.write_text(
            json.dumps(self.request, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.context["inputDigest"] = self.request["inputDigest"]
        self.context_path.write_text(
            json.dumps(self.context, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        shortlist_path = self.root / "shared-widget-shortlist.json"
        shortlist = build_registry_shortlist(
            {"queryText": "bag item"}, registry_path=registry_copy
        )
        shortlist_path.write_text(
            json.dumps(shortlist, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.prepare(
            roles=("project-pattern",),
            role_attachments={
                "project-pattern": [("registry-shortlist", shortlist_path)],
            }
        )
        packet_path = self.root / "agent-inputs" / "project-pattern.json"
        packet = load_json(packet_path)
        tampered = copy.deepcopy(shortlist)
        tampered["cards"][0]["purpose"] += " forged"
        shortlist_path.write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")
        packet["attachedInputs"][0]["sha256"] = sha256_file(shortlist_path)
        validation = validate_role_packet(
            packet,
            packet_path=packet_path,
            request_root=self.root,
            request_path=self.request_path,
            base_context_path=self.context_path,
            draft_requirement_path=self.draft_path,
            packet_schema=load_json(DEFAULT_PACKET_SCHEMA),
        )
        self.assertIn(
            "packet.registry_shortlist",
            {entry["code"] for entry in validation["errors"]},
        )

    def test_role_projection_profiles_preserve_values_and_omit_only_safe_sections(self) -> None:
        self.prepare()
        for role in CONTEXT_ROLES:
            projection = load_json(self.root / "contexts" / "roles" / f"{role}.json")
            self.assertNotIn("firstRoundTrace", projection)
            self.assertEqual(projection["evidence"], self.context["evidence"])
            self.assertEqual(projection["preliminaryClaims"], self.context["preliminaryClaims"])
            self.assertEqual(projection["elements"], self.context["elements"])
            self.assertEqual(projection["projection"]["agentRole"], role)
            self.assertEqual(projection["projection"]["mode"], "projected")
        self.assertNotIn("plannedAssets", load_json(self.root / "contexts" / "roles" / "state-modeling.json"))
        self.assertIn("plannedAssets", load_json(self.root / "contexts" / "roles" / "data-adaptation.json"))
        self.assertIn("reuseCandidates", load_json(self.root / "contexts" / "roles" / "asset-decomposition.json"))

    def test_unknown_section_falls_back_to_full_context(self) -> None:
        context = copy.deepcopy(self.context)
        context["futureSemanticSection"] = [{"id": "future.one"}]
        projected, mode, reason = build_context_projection(
            context,
            self.request,
            "state-modeling",
            base_context_ref="contexts/normalized-context.json",
            base_file_sha256="1" * 64,
        )
        self.assertEqual(mode, "full-fallback")
        self.assertIn("unknown-top-level-sections", reason or "")
        self.assertEqual(projected["futureSemanticSection"], context["futureSemanticSection"])
        self.assertIn("firstRoundTrace", projected)

    def test_invalid_authoritative_base_context_is_rejected_not_projected(self) -> None:
        invalid_contexts: list[tuple[str, dict]] = []
        wrong_kind = copy.deepcopy(self.context)
        wrong_kind["contextKind"] = "normalized-role-projection"
        invalid_contexts.append(("wrong-kind", wrong_kind))
        non_authoritative = copy.deepcopy(self.context)
        non_authoritative["authoritative"] = False
        invalid_contexts.append(("not-authoritative", non_authoritative))
        wrong_request = copy.deepcopy(self.context)
        wrong_request["requestId"] = "another-request"
        invalid_contexts.append(("wrong-request", wrong_request))
        missing_section = copy.deepcopy(self.context)
        missing_section.pop("regions")
        invalid_contexts.append(("missing-section", missing_section))
        dangling_base = copy.deepcopy(self.context)
        dangling_base["elements"][0]["assetId"] = "asset.missing"
        invalid_contexts.append(("dangling-base", dangling_base))

        for label, context in invalid_contexts:
            with self.subTest(label=label), self.assertRaises(AgentInputError):
                build_context_projection(
                    context,
                    self.request,
                    "state-modeling",
                    base_context_ref="contexts/normalized-context.json",
                    base_file_sha256="1" * 64,
                )

    def test_duplicate_canonical_id_is_rejected(self) -> None:
        context = copy.deepcopy(self.context)
        duplicate = copy.deepcopy(context["elements"][0])
        context["elements"].append(duplicate)
        with self.assertRaisesRegex(AgentInputError, "duplicate canonical ids"):
            build_context_projection(
                context,
                self.request,
                "state-modeling",
                base_context_ref="contexts/normalized-context.json",
                base_file_sha256="1" * 64,
            )

    def test_unreviewed_reference_like_field_is_rejected(self) -> None:
        context = copy.deepcopy(self.context)
        context["elements"][0]["futureOwnerIds"] = [context["regions"][0]["id"]]
        with self.assertRaisesRegex(AgentInputError, "unreviewed reference-like fields"):
            build_context_projection(
                context,
                self.request,
                "state-modeling",
                base_context_ref="contexts/normalized-context.json",
                base_file_sha256="1" * 64,
            )

    def test_unknown_shape_inside_an_omitted_domain_section_falls_back(self) -> None:
        context = copy.deepcopy(self.context)
        context["plannedAssets"][0]["futureStateSemantics"] = {"required": True}
        projected, mode, reason = build_context_projection(
            context,
            self.request,
            "state-modeling",
            base_context_ref="contexts/normalized-context.json",
            base_file_sha256="1" * 64,
        )
        self.assertEqual("full-fallback", mode)
        self.assertIn("unknown-omittable-section-shape:plannedAssets", reason or "")
        self.assertIn("plannedAssets", projected)

    def test_dangling_reference_expands_to_full_context(self) -> None:
        context = copy.deepcopy(self.context)
        context["elements"][0]["assetId"] = "asset.screen"
        projected, mode, reason = build_context_projection(
            context,
            self.request,
            "state-modeling",
            base_context_ref="contexts/normalized-context.json",
            base_file_sha256="1" * 64,
        )
        self.assertEqual(mode, "full-fallback")
        self.assertIn("dangling-canonical-references", reason or "")
        self.assertIn("plannedAssets", projected)

    def test_generation_is_byte_deterministic_at_same_paths(self) -> None:
        self.prepare()
        first = {
            path.relative_to(self.root).as_posix(): sha256_file(path)
            for path in sorted((self.root / "agent-inputs").glob("*.json"))
        }
        self.prepare()
        second = {
            path.relative_to(self.root).as_posix(): sha256_file(path)
            for path in sorted((self.root / "agent-inputs").glob("*.json"))
        }
        self.assertEqual(first, second)

    def test_history_or_scope_tampering_is_rejected(self) -> None:
        self.prepare()
        packet_path = self.root / "agent-inputs" / "visual-structure.json"
        packet = load_json(packet_path)
        packet["conversationHistory"] = []
        packet["sourceScope"] = ["source-user-text"]
        validation = validate_role_packet(
            packet,
            packet_path=packet_path,
            request_root=self.root,
            request_path=self.request_path,
            base_context_path=self.context_path,
            draft_requirement_path=self.draft_path,
            packet_schema=load_json(DEFAULT_PACKET_SCHEMA),
        )
        codes = {entry["code"] for entry in validation["errors"]}
        self.assertIn("packet.history_forbidden", codes)
        self.assertIn("packet.source_scope", codes)

    def test_context_hash_tampering_is_rejected(self) -> None:
        self.prepare()
        packet_path = self.root / "agent-inputs" / "state-modeling.json"
        packet = load_json(packet_path)
        context_path = self.root / packet["context"]["ref"]
        context = load_json(context_path)
        context["notice"] = "tampered"
        context_path.write_text(json.dumps(context), encoding="utf-8")
        validation = validate_role_packet(
            packet,
            packet_path=packet_path,
            request_root=self.root,
            request_path=self.request_path,
            base_context_path=self.context_path,
            draft_requirement_path=self.draft_path,
            packet_schema=load_json(DEFAULT_PACKET_SCHEMA),
        )
        codes = {entry["code"] for entry in validation["errors"]}
        self.assertTrue({"packet.context_projection", "packet.context_binding"} & codes)

    def test_review_packets_reject_swapped_valid_review_views(self) -> None:
        roles = ("state-visual-review", "schema-feasibility-review")
        self.prepare(roles=roles)
        packets: dict[str, tuple[Path, dict]] = {}
        review_bindings: dict[str, dict] = {}
        for role in roles:
            packet_path = self.root / "agent-inputs" / f"{role}.json"
            packet = load_json(packet_path)
            packets[role] = (packet_path, packet)
            review_bindings[role] = copy.deepcopy(
                next(
                    item
                    for item in packet["additionalInputs"]
                    if item["kind"] == "review-view"
                )
            )

        for role, other_role in zip(roles, reversed(roles)):
            packet_path, packet = packets[role]
            binding = next(
                item
                for item in packet["additionalInputs"]
                if item["kind"] == "review-view"
            )
            binding.update(review_bindings[other_role])
            validation = validate_role_packet(
                packet,
                packet_path=packet_path,
                request_root=self.root,
                request_path=self.request_path,
                base_context_path=self.context_path,
                draft_requirement_path=self.draft_path,
                packet_schema=load_json(DEFAULT_PACKET_SCHEMA),
            )
            codes = {entry["code"] for entry in validation["errors"]}
            self.assertIn("packet.review_view_role_binding", codes)
            self.assertIn("packet.review_view_profile_binding", codes)

    def test_review_prepare_allows_nested_one_of_unknown_only_via_exact_fallback(self) -> None:
        draft = load_json(self.draft_path)
        draft["uiModel"]["elements"][0]["imageComposition"] = {
            "groupKey": "imggrp.future-shape",
            "role": "complete",
            "adaptation": "independent",
            "futureFlag": True,
        }
        self.draft_path.write_text(
            json.dumps(draft, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        summary = self.prepare(
            output_name="unknown-review-fallback",
            roles=REVIEW_ROLES,
        )
        self.assertEqual(len(summary["roles"]), len(REVIEW_ROLES))
        for role in REVIEW_ROLES:
            view = load_json(self.root / "review-views" / f"{role}.review-view.json")
            self.assertEqual(view["mode"], "full-fallback")
            self.assertEqual(view["fallbackReason"], "unknown-field")
            self.assertEqual(view["requirement"], draft)

    def test_agent_findings_can_bind_role_packet_and_projection(self) -> None:
        self.prepare()
        packet_path = self.root / "agent-inputs" / "state-modeling.json"
        packet = load_json(packet_path)
        context_path = self.root / packet["context"]["ref"]
        context = load_json(context_path)
        findings = {
            "version": "0.1",
            "requestId": self.request["requestId"],
            "agentRole": "state-modeling",
            "inputDigest": self.request["inputDigest"],
            "contextDigest": canonical_sha256(context),
            "sourceScope": packet["sourceScope"],
            "findings": [],
            "evidence": [],
            "questionCandidates": [],
        }
        validation = validate_agent_findings(
            findings,
            load_json(FINDINGS_SCHEMA),
            self.request,
            load_json(REQUEST_SCHEMA),
            context,
            self.request_path,
            context_path,
            packet,
            packet_path,
            self.context_path,
        )
        self.assertTrue(validation["valid"], validation)

    def test_review_packet_rejects_draft_from_another_request(self) -> None:
        draft = load_json(self.draft_path)
        draft["requestId"] = "another-request"
        self.draft_path.write_text(
            json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        with self.assertRaisesRegex(AgentInputError, "requestId does not match"):
            self.prepare(roles=("state-visual-review",))

    def test_coverage_evidence_is_schema_and_request_image_bound(self) -> None:
        valid = self.prepare(
            "coverage-valid",
            roles=("coverage-review",),
            coverage_evidence=self.coverage_path,
        )
        self.assertEqual("valid", valid["status"])

        report = load_json(self.coverage_path)
        report["sourceSha256"] = "f" * 64
        self.coverage_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        with self.assertRaisesRegex(AgentInputError, "packet.coverage_evidence_invalid"):
            self.prepare(
                "coverage-wrong-image",
                roles=("coverage-review",),
                coverage_evidence=self.coverage_path,
            )

        report["unexpected"] = True
        self.coverage_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        with self.assertRaisesRegex(AgentInputError, "packet.coverage_evidence_invalid"):
            self.prepare(
                "coverage-open-shape",
                roles=("coverage-review",),
                coverage_evidence=self.coverage_path,
            )

    def test_byte_proxy_reports_reduction_without_token_conversion(self) -> None:
        summary = self.prepare()
        comparison = summary["byteProxyComparison"]
        self.assertLess(comparison["optimizedBytes"], comparison["legacyBytes"])
        self.assertGreater(comparison["reductionRatio"], 0)
        self.assertIsNone(summary["historyTelemetry"]["actualModelTokens"])
        review = summary["reviewViewByteProxyComparison"]
        self.assertEqual(review["reviewRoleCount"], 3)
        self.assertEqual(review["legacyBytes"], self.draft_path.stat().st_size * 3)
        self.assertEqual(len(review["views"]), 3)
        self.assertEqual(
            review["optimizedBytes"],
            sum(item["bytes"] for item in review["views"]),
        )
        self.assertIsNone(review["actualModelTokens"])


if __name__ == "__main__":
    unittest.main()
