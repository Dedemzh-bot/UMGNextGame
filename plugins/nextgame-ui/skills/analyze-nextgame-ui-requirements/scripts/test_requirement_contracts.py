#!/usr/bin/env python3
"""Regression tests for NextGame UI requirement-analysis JSON contracts."""

from __future__ import annotations

import copy
import json
import shutil
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

from _contract_common import (
    ASSETS_ROOT,
    canonical_sha256,
    compute_approved_content_sha256,
    compute_request_input_digest,
    load_json,
    sha256_file,
)
from validate_agent_findings import DEFAULT_SCHEMA as FINDINGS_SCHEMA, validate_agent_findings
from validate_build_bundle import DEFAULT_SCHEMA as BUNDLE_SCHEMA, validate_build_bundle
from validate_request_packet import DEFAULT_SCHEMA as PACKET_SCHEMA, validate_request_packet
from validate_requirement_coverage import validate_requirement_coverage
from validate_requirement_spec import DEFAULT_SCHEMA as REQUIREMENT_SCHEMA, validate_requirement_spec


SCRIPTS_ROOT = Path(__file__).resolve().parent
EXAMPLE_REQUIREMENT = ASSETS_ROOT / "example-composite-tabs-requirement.json"
EXAMPLE_BUNDLE = ASSETS_ROOT / "example-composite-tabs-build-bundle.json"
EXAMPLE_PACKET = ASSETS_ROOT / "example-composite-tabs-request-packet.json"


def error_codes(validation: dict) -> set[str]:
    return {entry["code"] for entry in validation["errors"]}


def warning_codes(validation: dict) -> set[str]:
    return {entry["code"] for entry in validation["warnings"]}


@contextmanager
def writable_test_directory():
    path = Path(tempfile.gettempdir()) / "nextgame-ui-requirement-tests" / f"case-{uuid4().hex}"
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def refresh_approval(spec: dict) -> None:
    spec["reviewGate"]["approvedContentSha256"] = compute_approved_content_sha256(spec)


def enable_state_control_input(spec: dict, kind: str = "user-interaction") -> dict:
    model = spec["stateModels"][0]
    control_input = {
        "id": "control-tab-selection",
        "axisId": "axis-tab-selection",
        "kind": kind,
        "description": "Selecting a navigation tab chooses the corresponding selection appearance.",
        "targetStateIds": ["state-tab-selected", "state-tab-unselected"],
        "evidenceIds": ["evidence-selected-tab", "evidence-unselected-tabs"],
        "claimIds": ["claim-tab-composite-state"],
    }
    model["controlInputs"] = [control_input]
    supporting_claim = next(item for item in spec["claims"] if item["id"] == "claim-tab-composite-state")
    if control_input["id"] not in supporting_claim["subjectRefs"]:
        supporting_claim["subjectRefs"].append(control_input["id"])
    spec["analysisPolicy"] = {
        "geometryEvidenceRequired": False,
        "listPriorityRequired": False,
        "stateControlInputRequired": True,
    }
    refresh_approval(spec)
    return control_input


def make_request_packet() -> dict:
    project_rule_path = str((SCRIPTS_ROOT / "_contract_common.py").resolve())
    packet = {
        "version": "0.1",
        "requestId": "role-tab-states-20260803",
        "inputDigest": "0" * 64,
        "userRequest": {
            "originalText": ["Create a Role screen with explicit selected and unselected tab states."],
            "language": "zh-CN",
        },
        "sources": [
            {
                "sourceKey": "source-role-image",
                "kind": "image",
                "locatorKind": "local-file",
                "description": "Role UI reference.",
                "path": "C:/References/role-screen.png",
                "mediaType": "image/png",
                "imageSize": [2048, 1152],
                "contentSha256": "c" * 64,
            },
            {
                "sourceKey": "source-user-request",
                "kind": "user-text",
                "locatorKind": "inline",
                "description": "Raw user request.",
                "content": "Create a Role screen with explicit selected and unselected tab states.",
            },
            {
                "sourceKey": "source-project-rules",
                "kind": "project-rule",
                "locatorKind": "local-file",
                "description": "NextGame project rules.",
                "path": project_rule_path,
                "contentSha256": "d" * 64,
            },
        ],
        "targetHints": {
            "system": "role",
            "systemFolder": "Role",
            "assetKind": "screen",
            "mode": "production",
            "designCanvas": [2560, 1440],
            "targetAssetPaths": ["/Game/UI/UMG/Role/umg_role"],
            "productionAuthorized": True,
        },
        "projectRuleRefs": [{"path": project_rule_path, "section": "UMG Asset Naming", "required": True}],
    }
    packet["inputDigest"] = compute_request_input_digest(packet)
    return packet


def make_packet_for_requirement(spec: dict) -> dict:
    packet_sources = []
    for source in spec["sources"]:
        packet_source = {
            key: copy.deepcopy(value)
            for key, value in source.items()
            if key not in {"id", "dimensions"}
        }
        if "dimensions" in source:
            packet_source["imageSize"] = copy.deepcopy(source["dimensions"])
        packet_sources.append(packet_source)
    packet = {
        "version": "0.1",
        "requestId": spec["requestId"],
        "inputDigest": "0" * 64,
        "userRequest": {"originalText": copy.deepcopy(spec["request"]["originalText"]), "language": "en-US"},
        "sources": packet_sources,
        "targetHints": copy.deepcopy(spec["target"]),
        "projectRuleRefs": [
            {"path": source["path"], "section": "NextGame UMG rules", "required": True}
            for source in packet_sources
            if source["kind"] == "project-rule"
        ],
    }
    packet["inputDigest"] = compute_request_input_digest(packet)
    return packet


def bind_requirement_to_packet(spec: dict, packet: dict) -> None:
    spec["inputDigest"] = packet["inputDigest"]
    for findings_input in spec["normalization"]["findingsInputs"]:
        findings_input["inputDigest"] = packet["inputDigest"]
    refresh_approval(spec)


def make_static_variant_fixture() -> tuple[dict, dict]:
    """Return a dependency-free bundle proving static variant state assignment."""

    spec = load_json(EXAMPLE_REQUIREMENT)
    bundle = load_json(EXAMPLE_BUNDLE)
    base_plan, screen_plan = copy.deepcopy(spec["assetPlan"])
    selected_plan = copy.deepcopy(base_plan)
    selected_plan.update(
        {
            "id": "asset-child-navigation-tab-selected",
            "assetPath": "/Game/UI/UMG/Role/Widgets/uw_role_navigation_tab_selected",
            "layoutSpecPath": "static-selected-layout.json",
            "buildOrder": 0,
            "coversElementIds": [
                "element-tab-template-button",
                "element-tab-content-panel",
                "element-tab-selected-panel",
                "element-tab-selected-background",
                "element-tab-selected-accent",
                "element-tab-selected-label",
            ],
        }
    )
    unselected_plan = copy.deepcopy(base_plan)
    unselected_plan.update(
        {
            "id": "asset-child-navigation-tab-unselected",
            "assetPath": "/Game/UI/UMG/Role/Widgets/uw_role_navigation_tab_unselected",
            "layoutSpecPath": "static-unselected-layout.json",
            "buildOrder": 1,
            "coversElementIds": [
                "element-tab-template-button",
                "element-tab-content-panel",
                "element-tab-unselected-panel",
                "element-tab-unselected-background",
                "element-tab-unselected-label",
            ],
        }
    )
    screen_plan["buildOrder"] = 2
    screen_plan["dependsOnAssetIds"] = [selected_plan["id"], unselected_plan["id"]]
    spec["revision"] = 2
    spec["assetPlan"] = [selected_plan, unselected_plan, screen_plan]
    spec["target"]["targetAssetPaths"] = [
        selected_plan["assetPath"],
        unselected_plan["assetPath"],
        screen_plan["assetPath"],
    ]
    for claim_id in ("claim-asset-decomposition", "claim-tab-composite-state"):
        claim = next(item for item in spec["claims"] if item["id"] == claim_id)
        claim["subjectRefs"] = [ref for ref in claim["subjectRefs"] if ref != "asset-child-navigation-tab"]
        claim["subjectRefs"].extend([selected_plan["id"], unselected_plan["id"]])
    refresh_approval(spec)

    old_child_asset, screen_asset = copy.deepcopy(bundle["assets"])
    selected_asset = copy.deepcopy(old_child_asset)
    selected_asset.update(
        {
            "id": "build-child-navigation-tab-selected",
            "assetPlanId": selected_plan["id"],
            "assetPath": selected_plan["assetPath"],
            "layoutSpecPath": selected_plan["layoutSpecPath"],
            "layoutSpecSha256": "1" * 64,
            "buildOrder": 0,
        }
    )
    unselected_asset = copy.deepcopy(old_child_asset)
    unselected_asset.update(
        {
            "id": "build-child-navigation-tab-unselected",
            "assetPlanId": unselected_plan["id"],
            "assetPath": unselected_plan["assetPath"],
            "layoutSpecPath": unselected_plan["layoutSpecPath"],
            "layoutSpecSha256": "2" * 64,
            "buildOrder": 1,
        }
    )
    screen_asset["buildOrder"] = 2
    screen_asset["dependsOnAssetIds"] = [selected_asset["id"], unselected_asset["id"]]
    bundle["assets"] = [selected_asset, unselected_asset, screen_asset]

    old_mappings = copy.deepcopy(bundle["nodeMappings"])
    selected_mapping_ids = {
        "mapping-child-root",
        "mapping-child-region",
        "mapping-tab-button",
        "mapping-tab-content-panel",
        "mapping-tab-selected-panel",
        "mapping-tab-selected-background",
        "mapping-tab-selected-accent",
        "mapping-tab-selected-label",
    }
    unselected_mapping_ids = {
        "mapping-tab-unselected-panel",
        "mapping-tab-unselected-background",
        "mapping-tab-unselected-label",
    }
    selected_mappings = [item for item in old_mappings if item["id"] in selected_mapping_ids]
    unselected_mappings = [item for item in old_mappings if item["id"] in unselected_mapping_ids]
    screen_mappings = [item for item in old_mappings if item["assetId"] == "build-screen-role"]
    for mapping in selected_mappings:
        mapping["assetId"] = selected_asset["id"]
        if mapping["id"].startswith("mapping-tab-selected-"):
            mapping["stateRefs"] = ["state-tab-selected"]
            if "claim-tab-composite-state" not in mapping["claimIds"]:
                mapping["claimIds"].append("claim-tab-composite-state")
    for mapping in unselected_mappings:
        mapping["assetId"] = unselected_asset["id"]
        mapping["stateRefs"] = ["state-tab-unselected"]
        if "claim-tab-composite-state" not in mapping["claimIds"]:
            mapping["claimIds"].append("claim-tab-composite-state")
    bundle["nodeMappings"] = selected_mappings + unselected_mappings + screen_mappings

    for operation in bundle["crossAssetOperations"]:
        operation["sourceAssetId"] = (
            selected_asset["id"]
            if operation["id"] == "operation-integrate-role-tab"
            else unselected_asset["id"]
        )
        operation["stateHandling"] = {
            "strategy": "static-variant-asset",
            "stateRefs": copy.deepcopy(operation["stateHandling"]["stateRefs"]),
        }
    bundle["execution"]["buildOrderAssetIds"] = [selected_asset["id"], unselected_asset["id"], screen_asset["id"]]
    bundle["verification"]["checks"][0]["assetId"] = selected_asset["id"]
    bundle["verification"]["checks"][2]["assetId"] = selected_asset["id"]
    bundle["verification"]["deviations"] = []
    bundle["requirement"].update(
        {
            "sha256": "0" * 64,
            "revision": spec["revision"],
            "approvedContentSha256": spec["reviewGate"]["approvedContentSha256"],
        }
    )
    return spec, bundle


def make_agent_findings(packet: dict) -> dict:
    return {
        "version": "0.1",
        "requestId": packet["requestId"],
        "agentRole": "visual-structure",
        "inputDigest": packet["inputDigest"],
        "sourceScope": ["source-role-image"],
        "findings": [
            {
                "localId": "local-finding-tabs",
                "category": "component-family",
                "statement": "Four tabs share one visual family.",
                "evidenceRefs": ["local-evidence-tabs"],
                "confidence": 0.98,
                "impact": "high",
            }
        ],
        "evidence": [
            {
                "localId": "local-evidence-tabs",
                "sourceKey": "source-role-image",
                "kind": "cross-instance",
                "description": "The four labels occupy matching repeated positions.",
                "bounds": [0.1, 0.1, 0.15, 0.6],
                "confidence": 0.98,
            }
        ],
        "questionCandidates": [],
    }


class RequestPacketTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = load_json(PACKET_SCHEMA)

    def test_valid_packet_uses_canonical_input_digest(self) -> None:
        packet = make_request_packet()
        self.assertTrue(validate_request_packet(packet, self.schema)["valid"])

    def test_changed_source_digest_invalidates_packet(self) -> None:
        packet = make_request_packet()
        packet["sources"][0]["contentSha256"] = "e" * 64
        validation = validate_request_packet(packet, self.schema)
        self.assertFalse(validation["valid"])
        self.assertIn("request.input_digest", error_codes(validation))

    def test_local_file_requires_content_digest(self) -> None:
        packet = make_request_packet()
        del packet["sources"][0]["contentSha256"]
        packet["inputDigest"] = compute_request_input_digest(packet)
        validation = validate_request_packet(packet, self.schema)
        self.assertFalse(validation["valid"])
        self.assertIn("source.content_digest", error_codes(validation))

    def test_screen_hint_requires_project_resolution(self) -> None:
        packet = make_request_packet()
        packet["targetHints"]["designCanvas"] = [1920, 1080]
        packet["inputDigest"] = compute_request_input_digest(packet)
        validation = validate_request_packet(packet, self.schema)
        self.assertIn("target.screen_resolution", error_codes(validation))

    def test_local_source_file_is_rehashed_at_validation_time(self) -> None:
        packet = make_request_packet()
        packet["sources"][0] = {
            "sourceKey": "source-role-image",
            "kind": "other",
            "locatorKind": "local-file",
            "description": "Stable local contract fixture.",
            "path": str(Path(__file__).resolve()),
            "contentSha256": sha256_file(Path(__file__).resolve()),
        }
        rule_path = SCRIPTS_ROOT / "_contract_common.py"
        packet["sources"][2]["path"] = str(rule_path.resolve())
        packet["sources"][2]["contentSha256"] = sha256_file(rule_path)
        packet["projectRuleRefs"][0]["path"] = str(rule_path.resolve())
        packet["inputDigest"] = compute_request_input_digest(packet)
        owner = SCRIPTS_ROOT / "request-packet.json"
        self.assertTrue(validate_request_packet(packet, self.schema, packet_path=owner)["valid"])
        packet["sources"][0]["contentSha256"] = "0" * 64
        packet["inputDigest"] = compute_request_input_digest(packet)
        validation = validate_request_packet(packet, self.schema, packet_path=owner)
        self.assertIn("source.file_digest", error_codes(validation))

    def test_local_source_paths_must_be_absolute(self) -> None:
        packet = make_request_packet()
        packet["sources"][0]["path"] = "relative/role-screen.png"
        packet["inputDigest"] = compute_request_input_digest(packet)
        self.assertIn("source.local_absolute", error_codes(validate_request_packet(packet, self.schema)))

    def test_nonfinite_request_data_returns_structured_error(self) -> None:
        packet = make_request_packet()
        packet["sources"][0]["imageSize"][0] = float("nan")
        validation = validate_request_packet(packet, self.schema)
        self.assertFalse(validation["valid"])
        self.assertTrue({"schema.finite_number", "request.input_digest_input"} & error_codes(validation))


class AgentFindingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = load_json(FINDINGS_SCHEMA)
        cls.packet_schema = load_json(PACKET_SCHEMA)

    def test_valid_findings_share_packet_digest(self) -> None:
        packet = make_request_packet()
        findings = make_agent_findings(packet)
        self.assertTrue(validate_agent_findings(findings, self.schema, packet, self.packet_schema)["valid"])

    def test_stale_findings_digest_is_rejected(self) -> None:
        packet = make_request_packet()
        findings = make_agent_findings(packet)
        findings["inputDigest"] = "f" * 64
        validation = validate_agent_findings(findings, self.schema, packet, self.packet_schema)
        self.assertIn("packet.input_digest", error_codes(validation))

    def test_canonical_ids_are_forbidden_before_normalization(self) -> None:
        packet = make_request_packet()
        findings = make_agent_findings(packet)
        findings["findings"][0]["canonicalId"] = "family-navigation-tab"
        validation = validate_agent_findings(findings, self.schema, packet, self.packet_schema)
        self.assertIn("findings.canonical_id_forbidden", error_codes(validation))

    def test_finding_requires_valid_local_evidence(self) -> None:
        packet = make_request_packet()
        findings = make_agent_findings(packet)
        findings["findings"][0]["evidenceRefs"] = ["local-evidence-missing"]
        validation = validate_agent_findings(findings, self.schema, packet, self.packet_schema)
        self.assertIn("ref.evidence", error_codes(validation))

    def test_round_two_finding_binds_dotted_canonical_subject_and_context(self) -> None:
        packet = make_request_packet()
        context = {
            "requestId": packet["requestId"],
            "inputDigest": packet["inputDigest"],
            "regions": [{"id": "reg.navigation"}],
        }
        findings = make_agent_findings(packet)
        findings["agentRole"] = "state-modeling"
        findings["contextDigest"] = canonical_sha256(context)
        findings["findings"][0]["subjectRefs"] = ["reg.navigation"]
        validation = validate_agent_findings(findings, self.schema, packet, self.packet_schema, context)
        self.assertTrue(validation["valid"], validation)

    def test_round_two_context_digest_mismatch_is_rejected(self) -> None:
        packet = make_request_packet()
        context = {"requestId": packet["requestId"], "inputDigest": packet["inputDigest"], "regions": [{"id": "reg.navigation"}]}
        findings = make_agent_findings(packet)
        findings["agentRole"] = "state-modeling"
        findings["contextDigest"] = "0" * 64
        findings["findings"][0]["subjectRefs"] = ["reg.navigation"]
        validation = validate_agent_findings(findings, self.schema, packet, self.packet_schema, context)
        self.assertIn("findings.context_digest", error_codes(validation))

    def test_round_one_role_cannot_read_wrong_source_kind(self) -> None:
        packet = make_request_packet()
        findings = make_agent_findings(packet)
        findings["sourceScope"] = ["source-user-request"]
        findings["evidence"][0]["sourceKey"] = "source-user-request"
        validation = validate_agent_findings(findings, self.schema, packet, self.packet_schema)
        self.assertIn("findings.role_source_scope", error_codes(validation))

    def test_visual_role_must_receive_every_image_source(self) -> None:
        packet = make_request_packet()
        packet["sources"].insert(
            1,
            {
                "sourceKey": "source-role-detail-image",
                "kind": "image",
                "locatorKind": "local-file",
                "description": "Second visual reference.",
                "path": "C:/References/role-detail.png",
                "mediaType": "image/png",
                "imageSize": [800, 600],
                "contentSha256": "e" * 64,
            },
        )
        packet["inputDigest"] = compute_request_input_digest(packet)
        findings = make_agent_findings(packet)
        self.assertIn(
            "findings.role_source_scope",
            error_codes(validate_agent_findings(findings, self.schema, packet, self.packet_schema)),
        )

    def test_role_with_no_relevant_sources_must_and_can_emit_empty_output(self) -> None:
        packet = make_request_packet()
        packet["sources"][0]["kind"] = "other"
        packet["inputDigest"] = compute_request_input_digest(packet)
        findings = make_agent_findings(packet)
        findings["sourceScope"] = []
        findings["findings"] = []
        findings["evidence"] = []
        self.assertTrue(validate_agent_findings(findings, self.schema, packet, self.packet_schema)["valid"])
        findings["questionCandidates"] = [
            {
                "localId": "local-question-no-image",
                "question": "Is there an image?",
                "reason": "No visual source exists.",
                "impact": "high",
                "relatedFindingRefs": [],
            }
        ]
        self.assertIn(
            "findings.empty_scope_output",
            error_codes(validate_agent_findings(findings, self.schema, packet, self.packet_schema)),
        )

    def test_nonfinite_round_two_context_returns_structured_error(self) -> None:
        packet = make_request_packet()
        context = {"requestId": packet["requestId"], "inputDigest": packet["inputDigest"], "regions": [{"id": "reg.navigation", "score": float("nan")}]}
        findings = make_agent_findings(packet)
        findings["agentRole"] = "state-modeling"
        findings["contextDigest"] = "0" * 64
        findings["findings"][0]["subjectRefs"] = ["reg.navigation"]
        validation = validate_agent_findings(findings, self.schema, packet, self.packet_schema, context)
        self.assertIn("findings.context_digest_input", error_codes(validation))


class RequirementSpecTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = load_json(REQUIREMENT_SCHEMA)
        cls.example = load_json(EXAMPLE_REQUIREMENT)

    def validate(self, spec: dict) -> dict:
        return validate_requirement_spec(spec, self.schema)

    def enable_static_visual_coverage(self, spec: dict) -> None:
        policy = spec.setdefault("analysisPolicy", {})
        policy["geometryEvidenceRequired"] = policy.get("geometryEvidenceRequired", False)
        policy["listPriorityRequired"] = policy.get("listPriorityRequired", False)
        policy["staticVisualCoverageRequired"] = True
        refresh_approval(spec)

    def validate_with_visual_element_finding(
        self,
        *,
        canonical_id: str = "element-tab-selected-background",
        discard: bool = False,
        measured: bool = True,
    ) -> dict:
        with writable_test_directory() as root:
            shutil.copytree(ASSETS_ROOT / "findings", root / "findings")
            packet_path = root / "request-packet.json"
            shutil.copy2(EXAMPLE_PACKET, packet_path)
            findings_path = root / "findings" / "visual-structure.json"
            findings = load_json(findings_path)
            findings["findings"].append(
                {
                    "localId": "local-selected-backplate",
                    "category": "element",
                    "statement": "The selected tab has an independently authored backplate layer.",
                    "evidenceRefs": ["local-selected-backplate-geometry"],
                    "confidence": 0.96,
                    "impact": "high",
                }
            )
            geometry = {
                "localId": "local-selected-backplate-geometry",
                "sourceKey": "source-role-image",
                "kind": "direct-observation",
                "description": "Measured selected-tab backplate bounds.",
                "bounds": [0.1, 0.1, 0.15, 0.15],
                "confidence": 0.96,
            }
            if measured:
                geometry.update(
                    {
                        "sourceDimensions": [2000, 1000],
                        "pixelBounds": [200, 100, 300, 150],
                        "measurementMethod": "image-measurement",
                    }
                )
            findings["evidence"].append(geometry)
            findings_path.write_text(json.dumps(findings, ensure_ascii=False, indent=2), encoding="utf-8")

            spec = copy.deepcopy(self.example)
            self.enable_static_visual_coverage(spec)
            visual_input = next(
                item
                for item in spec["normalization"]["findingsInputs"]
                if item["agentRole"] == "visual-structure"
            )
            visual_input["findingsSha256"] = sha256_file(findings_path)
            trace = {
                "agentRole": "visual-structure",
                "findingsRef": "findings/visual-structure.json",
                "localId": "local-selected-backplate",
            }
            if discard:
                trace["reason"] = "Incorrectly treated as implementation detail."
                spec["normalization"]["discardedLocalIds"].append(trace)
            else:
                trace["canonicalId"] = canonical_id
                spec["normalization"]["aliases"].append(trace)
            spec["normalization"]["aliases"].append(
                {
                    "agentRole": "visual-structure",
                    "findingsRef": "findings/visual-structure.json",
                    "localId": "local-selected-backplate-geometry",
                    "canonicalId": "evidence-selected-tab",
                }
            )
            refresh_approval(spec)
            spec_path = root / "ui-requirement.json"
            spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
            return validate_requirement_spec(
                spec,
                self.schema,
                load_json(packet_path),
                load_json(PACKET_SCHEMA),
                packet_path,
                spec_path,
                True,
            )

    def test_composite_tab_example_is_valid(self) -> None:
        self.assertTrue(self.validate(copy.deepcopy(self.example))["valid"])

    def test_legacy_spec_without_state_control_policy_remains_valid(self) -> None:
        spec = copy.deepcopy(self.example)
        spec.pop("analysisPolicy", None)
        spec["stateModels"][0].pop("controlInputs", None)
        refresh_approval(spec)
        self.assertTrue(self.validate(spec)["valid"])

    def test_legacy_spec_without_static_visual_policy_keeps_historical_behavior(self) -> None:
        spec = copy.deepcopy(self.example)
        selected_background = next(
            item
            for item in spec["uiModel"]["elements"]
            if item["id"] == "element-tab-selected-background"
        )
        selected_background["parentElementId"] = "element-navigation-panel"
        spec["assetPlan"][0]["coversElementIds"].remove(selected_background["id"])
        spec["stateModels"][0]["implementation"]["branches"][1]["completeElementIds"].remove(
            selected_background["id"]
        )
        refresh_approval(spec)
        codes = error_codes(self.validate(spec))
        self.assertFalse(any(code.startswith("visual.") for code in codes), codes)

    def test_static_visual_policy_accepts_complete_example_composition(self) -> None:
        spec = copy.deepcopy(self.example)
        self.enable_static_visual_coverage(spec)
        self.assertTrue(self.validate(spec)["valid"])

    def test_static_visual_policy_requires_family_entry_composition(self) -> None:
        spec = copy.deepcopy(self.example)
        selected_background = next(
            item
            for item in spec["uiModel"]["elements"]
            if item["id"] == "element-tab-selected-background"
        )
        selected_background["parentElementId"] = "element-navigation-panel"
        selected_background["regionId"] = "region-content"
        self.enable_static_visual_coverage(spec)
        self.assertIn("visual.family_composition", error_codes(self.validate(spec)))

    def test_static_visual_policy_requires_asset_coverage(self) -> None:
        spec = copy.deepcopy(self.example)
        spec["assetPlan"][0]["coversElementIds"].remove("element-tab-selected-background")
        self.enable_static_visual_coverage(spec)
        self.assertIn("visual.asset_composition", error_codes(self.validate(spec)))

    def test_static_visual_policy_reports_state_branch_omission(self) -> None:
        spec = copy.deepcopy(self.example)
        spec["stateModels"][0]["implementation"]["branches"][1]["completeElementIds"].remove(
            "element-tab-selected-background"
        )
        self.enable_static_visual_coverage(spec)
        self.assertIn("visual.state_branch_composition", error_codes(self.validate(spec)))

    def test_visual_element_finding_requires_type_preserving_alias(self) -> None:
        validation = self.validate_with_visual_element_finding(canonical_id="region-navigation")
        self.assertIn("visual.element_alias_type", error_codes(validation))

    def test_visual_element_finding_cannot_be_discarded(self) -> None:
        validation = self.validate_with_visual_element_finding(discard=True)
        self.assertIn("visual.element_discard_forbidden", error_codes(validation))

    def test_high_impact_visual_element_finding_requires_measured_geometry(self) -> None:
        validation = self.validate_with_visual_element_finding(measured=False)
        self.assertIn("visual.element_geometry_required", error_codes(validation))

    def test_measured_visual_element_finding_accepts_element_alias(self) -> None:
        validation = self.validate_with_visual_element_finding()
        self.assertTrue(validation["valid"], validation)

    def test_legacy_spec_without_policy_does_not_require_interaction_button(self) -> None:
        spec = copy.deepcopy(self.example)
        enable_state_control_input(spec)
        spec.pop("analysisPolicy", None)
        button = next(item for item in spec["uiModel"]["elements"] if item["id"] == "element-tab-template-button")
        button["kind"] = "panel"
        refresh_approval(spec)
        self.assertTrue(self.validate(spec)["valid"])

    def test_state_control_input_policy_accepts_unspecified_as_nonblocking_gap(self) -> None:
        spec = copy.deepcopy(self.example)
        enable_state_control_input(spec, kind="unspecified")
        validation = self.validate(spec)
        self.assertTrue(validation["valid"], validation)
        self.assertIn("state.control_input_unspecified", warning_codes(validation))

    def test_user_interaction_control_accepts_in_scope_same_family_button(self) -> None:
        spec = copy.deepcopy(self.example)
        enable_state_control_input(spec)
        self.assertTrue(self.validate(spec)["valid"])

    def test_user_interaction_control_requires_button_element(self) -> None:
        spec = copy.deepcopy(self.example)
        enable_state_control_input(spec)
        button = next(item for item in spec["uiModel"]["elements"] if item["id"] == "element-tab-template-button")
        button["kind"] = "panel"
        refresh_approval(spec)
        self.assertIn("state.control_input_button_required", error_codes(self.validate(spec)))

    def test_unrelated_button_cannot_satisfy_user_interaction_control(self) -> None:
        spec = copy.deepcopy(self.example)
        enable_state_control_input(spec)
        button = next(item for item in spec["uiModel"]["elements"] if item["id"] == "element-tab-template-button")
        button["familyId"] = None
        refresh_approval(spec)
        self.assertIn("state.control_input_button_required", error_codes(self.validate(spec)))

    def test_state_control_input_policy_requires_one_input_per_model(self) -> None:
        spec = copy.deepcopy(self.example)
        spec["analysisPolicy"] = {
            "geometryEvidenceRequired": False,
            "listPriorityRequired": False,
            "stateControlInputRequired": True,
        }
        refresh_approval(spec)
        self.assertIn("state.control_input_required", error_codes(self.validate(spec)))

    def test_state_control_input_refs_stay_with_its_model(self) -> None:
        spec = copy.deepcopy(self.example)
        control_input = enable_state_control_input(spec)
        control_input["axisId"] = "axis-missing"
        refresh_approval(spec)
        validation = self.validate(spec)
        self.assertIn("state.control_input_axis", error_codes(validation))
        self.assertIn("ref.state", error_codes(validation))

        spec = copy.deepcopy(self.example)
        control_input = enable_state_control_input(spec)
        control_input["targetStateIds"] = ["state-missing"]
        refresh_approval(spec)
        self.assertIn("ref.state", error_codes(self.validate(spec)))

        spec = copy.deepcopy(self.example)
        control_input = enable_state_control_input(spec)
        control_input["evidenceIds"] = ["evidence-user-static"]
        refresh_approval(spec)
        self.assertIn("state.control_input_evidence", error_codes(self.validate(spec)))

        spec = copy.deepcopy(self.example)
        control_input = enable_state_control_input(spec)
        control_input["claimIds"] = ["claim-screen-resolution"]
        refresh_approval(spec)
        self.assertIn("state.control_input_claim", error_codes(self.validate(spec)))

    def test_accepted_state_control_input_requires_accepted_claim(self) -> None:
        spec = copy.deepcopy(self.example)
        enable_state_control_input(spec)
        supporting_claim = next(item for item in spec["claims"] if item["id"] == "claim-tab-composite-state")
        supporting_claim["status"] = "proposed"
        spec["reviewGate"]["acceptedClaimIds"].remove(supporting_claim["id"])
        refresh_approval(spec)
        self.assertIn("review.state_control_input_claim", error_codes(self.validate(spec)))

    def test_state_control_input_rejects_code_interface_fields(self) -> None:
        spec = copy.deepcopy(self.example)
        control_input = enable_state_control_input(spec)
        control_input["eventName"] = "OnTabSelected"
        refresh_approval(spec)
        self.assertIn("schema.additional_property", error_codes(self.validate(spec)))

    def test_ids_are_globally_unique(self) -> None:
        spec = copy.deepcopy(self.example)
        spec["uiModel"]["elements"][0]["id"] = spec["uiModel"]["regions"][0]["id"]
        self.assertIn("id.duplicate", error_codes(self.validate(spec)))

    def test_claim_requires_valid_evidence_chain(self) -> None:
        spec = copy.deepcopy(self.example)
        spec["claims"][0]["evidenceIds"] = ["evidence-missing"]
        self.assertIn("ref.evidence", error_codes(self.validate(spec)))

    def test_review_list_exactly_matches_accepted_claims(self) -> None:
        spec = copy.deepcopy(self.example)
        spec["reviewGate"]["acceptedClaimIds"].remove("claim-screen-resolution")
        self.assertIn("review.accepted_claims", error_codes(self.validate(spec)))

    def test_exclusive_axis_requires_one_default(self) -> None:
        spec = copy.deepcopy(self.example)
        states = spec["stateModels"][0]["axes"][0]["states"]
        states[1]["isDefault"] = True
        self.assertIn("state.default", error_codes(self.validate(spec)))

    def test_exclusive_panel_strategy_requires_every_branch(self) -> None:
        spec = copy.deepcopy(self.example)
        spec["stateModels"][0]["implementation"]["branches"].pop()
        self.assertIn("state.branch_complete", error_codes(self.validate(spec)))

    def test_branch_panel_must_be_runtime_controlled(self) -> None:
        spec = copy.deepcopy(self.example)
        selected = next(item for item in spec["uiModel"]["elements"] if item["id"] == "element-tab-selected-panel")
        selected["runtimeControlled"] = False
        self.assertIn("state.branch_variable", error_codes(self.validate(spec)))

    def test_simple_property_states_can_use_shared_tree(self) -> None:
        spec = copy.deepcopy(self.example)
        model = spec["stateModels"][0]
        for state in model["axes"][0]["states"]:
            state["composition"] = {"mode": "node-overrides", "elementIds": ["element-tab-selected-label"]}
        model["implementation"] = {
            "strategy": "shared-tree-properties",
            "axisId": "axis-tab-selection",
            "sharedRootElementId": "element-tab-selected-label",
            "stateOverrides": [
                {
                    "stateId": "state-tab-unselected",
                    "changes": [{"elementId": "element-tab-selected-label", "property": "labelColor", "value": "subdued"}],
                },
                {
                    "stateId": "state-tab-selected",
                    "changes": [{"elementId": "element-tab-selected-label", "property": "labelColor", "value": "bright"}],
                },
            ],
        }
        refresh_approval(spec)
        self.assertTrue(self.validate(spec)["valid"])

    def test_screen_must_use_2560_by_1440(self) -> None:
        spec = copy.deepcopy(self.example)
        spec["target"]["designCanvas"] = [1920, 1080]
        screen = next(item for item in spec["assetPlan"] if item["assetKind"] == "screen")
        screen["referenceSize"] = [1920, 1080]
        validation = self.validate(spec)
        self.assertIn("target.screen_resolution", error_codes(validation))
        self.assertIn("asset.screen_resolution", error_codes(validation))

    def test_dynamic_collection_requires_runtime_list_container(self) -> None:
        spec = copy.deepcopy(self.example)
        spec["uiModel"]["collections"].append(
            {
                "id": "collection-tabs",
                "regionId": "region-navigation",
                "containerElementId": "element-navigation-panel",
                "entryFamilyId": "family-navigation-tab",
                "dynamic": True,
                "overflowStrategy": "scroll",
                "inBuildScope": False,
                "scopedOutReason": "Collection behavior is a contract-only example.",
                "evidenceIds": ["evidence-unselected-tabs"],
                "claimIds": ["claim-tab-family"],
            }
        )
        self.assertIn("collection.dynamic_container", error_codes(self.validate(spec)))

    def test_fixed_repetition_uses_structural_panel(self) -> None:
        spec = copy.deepcopy(self.example)
        spec["uiModel"]["collections"].append(
            {
                "id": "collection-tabs",
                "regionId": "region-navigation",
                "containerElementId": "element-navigation-panel",
                "entryFamilyId": "family-navigation-tab",
                "dynamic": False,
                "overflowStrategy": "fixed",
                "inBuildScope": False,
                "scopedOutReason": "The repeated decoration is not a runtime collection.",
                "evidenceIds": ["evidence-unselected-tabs"],
                "claimIds": ["claim-tab-family"],
            }
        )
        refresh_approval(spec)
        self.assertTrue(self.validate(spec)["valid"])

    def test_inactive_hidden_branch_requires_and_accepts_layout_reason(self) -> None:
        spec = copy.deepcopy(self.example)
        selected_branch = spec["stateModels"][0]["implementation"]["branches"][1]
        selected_branch["visibility"] = "Hidden"
        selected_branch["preserveLayoutReason"] = "Keep tab height stable during deferred state activation."
        refresh_approval(spec)
        self.assertTrue(self.validate(spec)["valid"])

    def test_active_branch_must_remain_self_hit_test_invisible(self) -> None:
        spec = copy.deepcopy(self.example)
        active_branch = spec["stateModels"][0]["implementation"]["branches"][0]
        active_branch["visibility"] = "Hidden"
        active_branch["preserveLayoutReason"] = "Invalid for the active branch."
        refresh_approval(spec)
        self.assertIn("state.branch_visibility", error_codes(self.validate(spec)))

    def test_in_scope_entity_cannot_mix_proposed_claim(self) -> None:
        spec = copy.deepcopy(self.example)
        spec["uiModel"]["elements"][0]["claimIds"].append("claim-hover-state")
        refresh_approval(spec)
        self.assertIn("review.in_scope_claim", error_codes(self.validate(spec)))

    def test_approval_digest_detects_post_review_mutation(self) -> None:
        spec = copy.deepcopy(self.example)
        spec["request"]["purpose"] = "Mutated after approval."
        self.assertIn("review.content_digest", error_codes(self.validate(spec)))

    def test_rejected_claim_status_matches_review_audit(self) -> None:
        spec = copy.deepcopy(self.example)
        hover = next(claim for claim in spec["claims"] if claim["id"] == "claim-hover-state")
        hover["status"] = "rejected"
        spec["reviewGate"]["rejectedClaimIds"] = ["claim-hover-state"]
        refresh_approval(spec)
        self.assertTrue(self.validate(spec)["valid"])

    def test_asset_dependency_requires_earlier_build_order(self) -> None:
        spec = copy.deepcopy(self.example)
        spec["assetPlan"][0]["buildOrder"] = 1
        spec["assetPlan"][1]["buildOrder"] = 0
        refresh_approval(spec)
        self.assertIn("asset.dependency_order", error_codes(self.validate(spec)))

    def test_scoped_out_entity_requires_reason(self) -> None:
        spec = copy.deepcopy(self.example)
        spec["uiModel"]["elements"][0]["inBuildScope"] = False
        self.assertIn("scope.reason", error_codes(self.validate(spec)))

    def test_each_entity_claim_must_name_that_entity(self) -> None:
        spec = copy.deepcopy(self.example)
        claim = next(item for item in spec["claims"] if item["id"] == "claim-tab-composite-state")
        claim["subjectRefs"].remove("element-tab-role-instance")
        refresh_approval(spec)
        self.assertIn("review.claim_subject", error_codes(self.validate(spec)))

    def test_claim_subject_must_reciprocally_list_claim(self) -> None:
        spec = copy.deepcopy(self.example)
        element = next(item for item in spec["uiModel"]["elements"] if item["id"] == "element-tab-role-instance")
        element["claimIds"].remove("claim-tab-composite-state")
        refresh_approval(spec)
        self.assertIn("review.claim_subject_reverse", error_codes(self.validate(spec)))

    def test_normalization_requires_all_nine_roles_once(self) -> None:
        spec = copy.deepcopy(self.example)
        spec["normalization"]["findingsInputs"].pop()
        refresh_approval(spec)
        self.assertIn("normalization.role_coverage", error_codes(self.validate(spec)))
        spec = copy.deepcopy(self.example)
        spec["normalization"]["findingsInputs"][1]["agentRole"] = "visual-structure"
        refresh_approval(spec)
        validation = self.validate(spec)
        self.assertTrue({"normalization.role_duplicate", "normalization.role_coverage"} <= error_codes(validation))

    def test_normalization_alias_requires_existing_canonical_entity(self) -> None:
        spec = copy.deepcopy(self.example)
        spec["normalization"]["aliases"][0]["canonicalId"] = "element.missing"
        refresh_approval(spec)
        self.assertIn("normalization.canonical_ref", error_codes(self.validate(spec)))

    def test_accepted_normalization_cannot_drop_all_trace_records(self) -> None:
        spec = copy.deepcopy(self.example)
        spec["normalization"]["aliases"] = []
        spec["normalization"]["discardedLocalIds"] = []
        refresh_approval(spec)
        self.assertIn("normalization.empty_trace", error_codes(self.validate(spec)))

    def test_strict_findings_check_validates_all_nine_linked_documents(self) -> None:
        packet = load_json(EXAMPLE_PACKET)
        validation = validate_requirement_spec(
            copy.deepcopy(self.example),
            self.schema,
            packet,
            load_json(PACKET_SCHEMA),
            EXAMPLE_PACKET,
            EXAMPLE_REQUIREMENT,
            True,
        )
        self.assertTrue(validation["valid"], validation)

    def test_strict_findings_check_requires_complete_local_id_trace(self) -> None:
        spec = copy.deepcopy(self.example)
        spec["normalization"]["aliases"] = [
            alias
            for alias in spec["normalization"]["aliases"]
            if alias["localId"] != "local-visual-tabs"
        ]
        refresh_approval(spec)
        validation = validate_requirement_spec(
            spec,
            self.schema,
            load_json(EXAMPLE_PACKET),
            load_json(PACKET_SCHEMA),
            EXAMPLE_PACKET,
            EXAMPLE_REQUIREMENT,
            True,
        )
        self.assertIn("normalization.local_id_coverage", error_codes(validation))

    def test_request_packet_and_requirement_are_exactly_bound(self) -> None:
        spec = copy.deepcopy(self.example)
        packet = make_packet_for_requirement(spec)
        bind_requirement_to_packet(spec, packet)
        validation = validate_requirement_spec(spec, self.schema, packet, load_json(PACKET_SCHEMA))
        self.assertTrue(validation["valid"], validation)

    def test_pending_empty_target_allows_screen_packet_hint(self) -> None:
        spec = copy.deepcopy(self.example)
        spec["reviewGate"]["status"] = "pending"
        spec["reviewGate"].pop("reviewedBy")
        spec["reviewGate"].pop("reviewedAt")
        spec["reviewGate"].pop("approvedContentSha256")
        spec["target"]["targetAssetPaths"] = []
        spec["assetPlan"] = []
        for claim in spec["claims"]:
            claim["subjectRefs"] = [ref for ref in claim["subjectRefs"] if not ref.startswith("asset-")]
        packet = make_packet_for_requirement(self.example)
        spec["inputDigest"] = packet["inputDigest"]
        for findings_input in spec["normalization"]["findingsInputs"]:
            findings_input["inputDigest"] = packet["inputDigest"]
        validation = validate_requirement_spec(spec, self.schema, packet, load_json(PACKET_SCHEMA))
        self.assertTrue(validation["valid"], validation)

    def test_accepted_expanded_target_allows_screen_packet_hint(self) -> None:
        spec, _ = make_static_variant_fixture()
        packet = make_packet_for_requirement(spec)
        screen_path = next(asset["assetPath"] for asset in spec["assetPlan"] if asset["assetKind"] == "screen")
        packet["targetHints"]["targetAssetPaths"] = [screen_path]
        packet["inputDigest"] = compute_request_input_digest(packet)
        bind_requirement_to_packet(spec, packet)
        validation = validate_requirement_spec(spec, self.schema, packet, load_json(PACKET_SCHEMA))
        self.assertTrue(validation["valid"], validation)

    def test_accepted_expanded_target_cannot_omit_screen_packet_hint(self) -> None:
        spec, _ = make_static_variant_fixture()
        packet = make_packet_for_requirement(spec)
        screen_path = next(asset["assetPath"] for asset in spec["assetPlan"] if asset["assetKind"] == "screen")
        packet["targetHints"]["targetAssetPaths"] = [screen_path]
        packet["inputDigest"] = compute_request_input_digest(packet)
        spec["target"]["targetAssetPaths"] = [path for path in spec["target"]["targetAssetPaths"] if path != screen_path]
        bind_requirement_to_packet(spec, packet)
        self.assertIn(
            "packet.target_hint",
            error_codes(validate_requirement_spec(spec, self.schema, packet, load_json(PACKET_SCHEMA))),
        )

    def test_request_packet_original_source_and_target_tampering_is_rejected(self) -> None:
        for mutation, expected_code in (
            (lambda spec: spec["request"]["originalText"].__setitem__(0, "Changed text."), "packet.original_text"),
            (lambda spec: spec["sources"][0].__setitem__("description", "Changed description."), "packet.source_field"),
            (lambda spec: spec["target"].__setitem__("system", "bag"), "packet.target_hint"),
        ):
            with self.subTest(expected_code=expected_code):
                spec = copy.deepcopy(self.example)
                packet = make_packet_for_requirement(spec)
                bind_requirement_to_packet(spec, packet)
                mutation(spec)
                refresh_approval(spec)
                validation = validate_requirement_spec(spec, self.schema, packet, load_json(PACKET_SCHEMA))
                self.assertIn(expected_code, error_codes(validation))

    def test_full_state_branch_cannot_include_non_descendant_or_overlap(self) -> None:
        spec = copy.deepcopy(self.example)
        branches = spec["stateModels"][0]["implementation"]["branches"]
        branches[0]["completeElementIds"].append("element-screen-root")
        self.assertIn("state.branch_descendants", error_codes(self.validate(spec)))
        spec = copy.deepcopy(self.example)
        model = spec["stateModels"][0]
        model["implementation"]["branches"][0]["completeElementIds"].append("element-tab-selected-label")
        model["axes"][0]["states"][0]["composition"]["elementIds"].append("element-tab-selected-label")
        self.assertIn("state.branch_overlap", error_codes(self.validate(spec)))

    def test_region_and_element_indirect_parent_cycles_are_rejected(self) -> None:
        spec = copy.deepcopy(self.example)
        spec["uiModel"]["regions"][0]["parentRegionId"] = "region-navigation"
        self.assertIn("region.parent_cycle", error_codes(self.validate(spec)))
        spec = copy.deepcopy(self.example)
        root = next(item for item in spec["uiModel"]["elements"] if item["id"] == "element-screen-root")
        root["parentElementId"] = "element-navigation-panel"
        self.assertIn("element.parent_cycle", error_codes(self.validate(spec)))

    def test_list_and_tile_elements_cannot_have_static_children(self) -> None:
        spec = copy.deepcopy(self.example)
        navigation = next(element for element in spec["uiModel"]["elements"] if element["id"] == "element-navigation-panel")
        navigation["kind"] = "list"
        refresh_approval(spec)
        self.assertIn("element.list_leaf", error_codes(self.validate(spec)))

    def test_requirement_element_cannot_have_duplicate_state_assignments(self) -> None:
        spec = copy.deepcopy(self.example)
        spec["stateModels"][0]["stateAssignments"].append(copy.deepcopy(spec["stateModels"][0]["stateAssignments"][0]))
        refresh_approval(spec)
        self.assertIn("state.assignment_duplicate", error_codes(self.validate(spec)))

    def test_in_scope_asset_cannot_depend_on_scoped_out_asset(self) -> None:
        spec = copy.deepcopy(self.example)
        child, screen = spec["assetPlan"]
        child["inBuildScope"] = False
        child["scopedOutReason"] = "Child asset is deferred."
        child["buildOrder"] = None
        screen["buildOrder"] = 0
        spec["target"]["targetAssetPaths"] = [screen["assetPath"]]
        refresh_approval(spec)
        self.assertIn("asset.scoped_out_dependency", error_codes(self.validate(spec)))

    def test_standard_system_asset_boundary_policy(self) -> None:
        spec = copy.deepcopy(self.example)
        spec["analysisPolicy"] = {
            "geometryEvidenceRequired": False,
            "listPriorityRequired": False,
            "assetBoundaryRequired": True,
            "standardSystemBoundaryRequired": True,
        }
        child, screen = spec["assetPlan"]
        child["boundaryClassification"] = "reusable-widget"
        child["boundaryEvidenceIds"] = [child["evidenceIds"][0]]
        screen["boundaryClassification"] = "screen-root"
        screen["boundaryEvidenceIds"] = [screen["evidenceIds"][0]]
        refresh_approval(spec)
        self.assertTrue(self.validate(spec)["valid"])

        missing = copy.deepcopy(spec)
        del missing["assetPlan"][0]["boundaryClassification"]
        refresh_approval(missing)
        self.assertIn("asset.boundary.required", error_codes(self.validate(missing)))

        generic_wrapper = copy.deepcopy(spec)
        generic_wrapper["assetPlan"][0]["boundaryClassification"] = "statically-referenced"
        refresh_approval(generic_wrapper)
        self.assertIn(
            "asset.boundary.standard_system",
            error_codes(self.validate(generic_wrapper)),
        )

        missing_evidence = copy.deepcopy(spec)
        missing_evidence["assetPlan"][0]["boundaryEvidenceIds"] = ["evidence-missing"]
        refresh_approval(missing_evidence)
        self.assertIn("ref.evidence", error_codes(self.validate(missing_evidence)))

        stale = copy.deepcopy(spec)
        stale["assetPlan"][0]["boundaryClassification"] = "stale-candidate"
        refresh_approval(stale)
        self.assertIn("asset.stale.final", error_codes(self.validate(stale)))
    def test_pending_review_can_retain_unresolved_target(self) -> None:
        spec = copy.deepcopy(self.example)
        spec["reviewGate"]["status"] = "pending"
        spec["reviewGate"].pop("reviewedBy")
        spec["reviewGate"].pop("reviewedAt")
        spec["reviewGate"].pop("approvedContentSha256")
        spec["target"].update(
            {
                "system": None,
                "systemFolder": None,
                "mode": None,
                "assetKind": None,
                "designCanvas": None,
                "targetAssetPaths": [],
                "productionAuthorized": None,
            }
        )
        spec["assetPlan"] = []
        for claim in spec["claims"]:
            claim["subjectRefs"] = [ref for ref in claim["subjectRefs"] if not ref.startswith("asset-")]
        self.assertTrue(self.validate(spec)["valid"])

    def test_pending_review_rejects_proposed_or_unresolved_claim_on_in_scope_entity(self) -> None:
        for claim_status in ("proposed", "unresolved"):
            with self.subTest(claim_status=claim_status):
                spec = copy.deepcopy(self.example)
                spec["reviewGate"]["status"] = "pending"
                spec["reviewGate"].pop("reviewedBy")
                spec["reviewGate"].pop("reviewedAt")
                spec["reviewGate"].pop("approvedContentSha256")
                hover_claim = next(claim for claim in spec["claims"] if claim["id"] == "claim-hover-state")
                hover_claim["status"] = claim_status
                hover_claim["subjectRefs"] = ["element-screen-root"]
                spec["uiModel"]["elements"][0]["claimIds"].append("claim-hover-state")
                self.assertIn("review.in_scope_claim", error_codes(self.validate(spec)))

    def test_pending_review_allows_explicit_screen_target_backed_by_accepted_claims(self) -> None:
        spec = copy.deepcopy(self.example)
        spec["reviewGate"]["status"] = "pending"
        spec["reviewGate"].pop("reviewedBy")
        spec["reviewGate"].pop("reviewedAt")
        spec["reviewGate"].pop("approvedContentSha256")
        self.assertTrue(self.validate(spec)["valid"])

    def test_nonfinite_requirement_data_returns_structured_error(self) -> None:
        spec = copy.deepcopy(self.example)
        spec["claims"][0]["confidence"] = float("nan")
        validation = self.validate(spec)
        self.assertFalse(validation["valid"])
        self.assertTrue({"schema.finite_number", "review.content_digest_input"} & error_codes(validation))

    def test_accepted_review_rejects_high_open_question(self) -> None:
        spec = copy.deepcopy(self.example)
        spec["questions"][0]["impact"] = "high"
        spec["questions"][0]["blocksBuild"] = True
        refresh_approval(spec)
        self.assertIn("review.high_open_question", error_codes(self.validate(spec)))

    def test_list_priority_requires_collection_for_fixed_visible_data_rows(self) -> None:
        spec = copy.deepcopy(self.example)
        spec["analysisPolicy"] = {"geometryEvidenceRequired": False, "listPriorityRequired": True}
        family = spec["uiModel"]["componentFamilies"][0]
        family["repetition"] = {"classification": "data-driven", "varyingFields": ["text", "image"]}
        refresh_approval(spec)
        self.assertIn("collection.list_required", error_codes(self.validate(spec)))

    def test_measured_top_region_rejects_geometry_drift(self) -> None:
        spec = copy.deepcopy(self.example)
        spec["analysisPolicy"] = {"geometryEvidenceRequired": True, "listPriorityRequired": False}
        spec["evidence"].append(
            {
                "id": "evidence-navigation-geometry-test",
                "sourceId": "source-role-image",
                "kind": "direct-observation",
                "description": "Measured navigation envelope.",
                "bounds": [0.08, 0.08, 0.14, 0.72],
                "sourceDimensions": [2048, 1152],
                "pixelBounds": [163.84, 92.16, 286.72, 829.44],
                "measurementMethod": "image-measurement",
            }
        )
        navigation = next(region for region in spec["uiModel"]["regions"] if region["id"] == "region-navigation")
        navigation["geometryEvidenceId"] = "evidence-navigation-geometry-test"
        refresh_approval(spec)
        self.assertTrue(self.validate(spec)["valid"])
        navigation["bounds"][2] = 0.3
        refresh_approval(spec)
        self.assertIn("geometry.requirement_drift", error_codes(self.validate(spec)))

    def test_top_level_region_overlap_is_rejected(self) -> None:
        spec = copy.deepcopy(self.example)
        spec["analysisPolicy"] = {"geometryEvidenceRequired": False, "listPriorityRequired": False}
        navigation = next(region for region in spec["uiModel"]["regions"] if region["id"] == "region-navigation")
        identity = copy.deepcopy(navigation)
        identity.update({"id": "region-identity", "nameHint": "PanelIdentity", "purpose": "identity", "bounds": [0.18, 0.08, 0.14, 0.72]})
        spec["uiModel"]["regions"].append(identity)
        claim = next(item for item in spec["claims"] if item["id"] == "claim-navigation-region")
        claim["subjectRefs"].append("region-identity")
        refresh_approval(spec)
        self.assertIn("geometry.sibling_overlap", error_codes(self.validate(spec)))

    def test_high_open_review_finding_blocks_accepted_gate(self) -> None:
        spec = copy.deepcopy(self.example)
        spec["reviewResolutions"] = [
            {
                "agentRole": "schema-feasibility-review",
                "findingsRef": "findings/schema-feasibility-review.json",
                "localId": "local-layout-defect",
                "impact": "high",
                "status": "open",
                "resolution": "Await geometry correction.",
            }
        ]
        refresh_approval(spec)
        self.assertIn("review.high_open_finding", error_codes(self.validate(spec)))

    def test_observed_state_model_requires_preview_assignments(self) -> None:
        spec = copy.deepcopy(self.example)
        family = spec["uiModel"]["componentFamilies"][0]
        family["repetition"] = {
            "classification": "static-repeat",
            "varyingFields": [],
            "staticRepeatReason": "Reference shows a fixed navigation set.",
        }
        spec["stateModels"][0]["stateAssignments"] = []
        refresh_approval(spec)
        self.assertIn("state.assignment_preview_coverage", error_codes(self.validate(spec)))

    def test_data_driven_dynamic_family_allows_empty_preview_assignments(self) -> None:
        spec = copy.deepcopy(self.example)
        family = spec["uiModel"]["componentFamilies"][0]
        family["repetition"] = {"classification": "data-driven", "varyingFields": ["text", "runtime-state"]}
        navigation = next(element for element in spec["uiModel"]["elements"] if element["id"] == "element-navigation-panel")
        navigation["kind"] = "list"
        for element in spec["uiModel"]["elements"]:
            if element.get("familyId") == family["id"] and element["id"].endswith("-instance"):
                element["parentElementId"] = None
        spec["uiModel"]["collections"].append(
            {
                "id": "collection-navigation-tabs",
                "regionId": "region-navigation",
                "containerElementId": navigation["id"],
                "entryFamilyId": family["id"],
                "dynamic": True,
                "overflowStrategy": "show-all",
                "inBuildScope": True,
                "evidenceIds": ["evidence-selected-tab", "evidence-unselected-tabs"],
                "claimIds": ["claim-tab-family"],
            }
        )
        claim = next(item for item in spec["claims"] if item["id"] == "claim-tab-family")
        claim["subjectRefs"].append("collection-navigation-tabs")
        spec["stateModels"][0]["stateAssignments"] = []
        refresh_approval(spec)
        validation = self.validate(spec)
        self.assertTrue(validation["valid"], validation)


class BuildBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle_schema = load_json(BUNDLE_SCHEMA)
        cls.requirement_schema = load_json(REQUIREMENT_SCHEMA)
        cls.requirement = load_json(EXAMPLE_REQUIREMENT)
        cls.bundle = load_json(EXAMPLE_BUNDLE)

    def validate(self, bundle: dict, requirement: dict | None = None) -> dict:
        return validate_build_bundle(
            bundle,
            self.bundle_schema,
            bundle_path=EXAMPLE_BUNDLE,
            requirement_spec=requirement or copy.deepcopy(self.requirement),
            requirement_path=EXAMPLE_REQUIREMENT,
            requirement_schema=self.requirement_schema,
            check_linked_files=True,
        )

    def make_user_interaction_fixture(self) -> tuple[dict, dict]:
        requirement = copy.deepcopy(self.requirement)
        enable_state_control_input(requirement)
        bundle = copy.deepcopy(self.bundle)
        bundle["requirement"]["approvedContentSha256"] = requirement["reviewGate"]["approvedContentSha256"]

        # Generated support nodes describe structure, not the Button realization itself.
        for mapping in bundle["nodeMappings"]:
            if mapping["id"] not in {"mapping-child-root", "mapping-child-region"}:
                continue
            mapping["requirementRefs"] = [
                requirement_ref
                for requirement_ref in mapping["requirementRefs"]
                if requirement_ref != "element-tab-template-button"
            ]
            mapping["claimIds"] = [claim_id for claim_id in mapping["claimIds"] if claim_id != "claim-tab-family"]
            if not mapping["requirementRefs"]:
                mapping["requirementRefs"] = ["region-navigation"]
            if "claim-navigation-region" not in mapping["claimIds"]:
                mapping["claimIds"].append("claim-navigation-region")
        return requirement, bundle

    def validate_with_child_layout(self, bundle: dict, requirement: dict, child_layout: dict) -> dict:
        original_load_json = load_json

        def load_linked_json(path: Path) -> dict:
            if Path(path).name == "example-composite-tabs-child-layout-spec.json":
                return copy.deepcopy(child_layout)
            return original_load_json(path)

        with patch("validate_build_bundle.load_json", side_effect=load_linked_json):
            return self.validate(bundle, requirement)

    def test_build_bundle_example_and_linked_hashes_are_valid(self) -> None:
        self.assertTrue(self.validate(copy.deepcopy(self.bundle))["valid"])

    def test_user_interaction_button_mapping_is_valid(self) -> None:
        requirement, bundle = self.make_user_interaction_fixture()
        self.assertTrue(self.validate(bundle, requirement)["valid"])

    def test_user_interaction_button_element_requires_mapping(self) -> None:
        requirement, bundle = self.make_user_interaction_fixture()
        bundle["nodeMappings"] = [
            mapping for mapping in bundle["nodeMappings"] if mapping["id"] != "mapping-tab-button"
        ]
        validation = self.validate(bundle, requirement)
        self.assertIn("mapping.control_input_button_missing", error_codes(validation))

    def test_user_interaction_button_mapping_requires_input_button_role(self) -> None:
        requirement, bundle = self.make_user_interaction_fixture()
        child_layout = load_json(ASSETS_ROOT / "example-composite-tabs-child-layout-spec.json")
        next(node for node in child_layout["nodes"] if node["id"] == "node-tab-button")["role"] = "container.canvas"
        # An unrelated Btn node elsewhere in the layout must not satisfy the element-specific mapping contract.
        next(node for node in child_layout["nodes"] if node["id"] == "node-child-region")["role"] = "input.button"
        validation = self.validate_with_child_layout(bundle, requirement, child_layout)
        self.assertIn("mapping.control_input_button_role", error_codes(validation))

    def test_bundle_rejects_nonaccepted_review(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        bundle["requirement"]["reviewStatus"] = "pending"
        self.assertIn("requirement.review", error_codes(self.validate(bundle)))

    def test_bundle_rejects_requirement_hash_mismatch(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        bundle["requirement"]["sha256"] = "0" * 64
        self.assertIn("requirement.sha256", error_codes(self.validate(bundle)))

    def test_bundle_rejects_requirement_revision_mismatch(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        bundle["requirement"]["revision"] += 1
        self.assertIn("requirement.revision", error_codes(self.validate(bundle)))

    def test_bundle_rejects_approval_digest_mismatch(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        bundle["requirement"]["approvedContentSha256"] = "0" * 64
        self.assertIn("requirement.approval_digest", error_codes(self.validate(bundle)))

    def test_bundle_rejects_layout_hash_mismatch(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        bundle["assets"][0]["layoutSpecSha256"] = "0" * 64
        self.assertIn("layout.sha256", error_codes(self.validate(bundle)))

    def test_only_reviewed_accepted_claims_enter_node_mappings(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        bundle["nodeMappings"][0]["claimIds"].append("claim-hover-state")
        self.assertIn("mapping.claim_status", error_codes(self.validate(bundle)))

    def test_layout_nodes_require_complete_mapping(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        bundle["nodeMappings"].pop(0)
        self.assertIn("mapping.layout_coverage", error_codes(self.validate(bundle)))

    def test_asset_dependencies_must_match_requirement_plan(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        bundle["assets"][1]["dependsOnAssetIds"] = []
        self.assertIn("asset.dependencies", error_codes(self.validate(bundle)))

    def test_screen_bundle_asset_requires_project_resolution(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        bundle["assets"][1]["referenceSize"] = [1920, 1080]
        self.assertIn("asset.screen_resolution", error_codes(self.validate(bundle)))

    def test_child_instance_state_assignment_requires_handling(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        del bundle["crossAssetOperations"][0]["stateHandling"]
        validation = self.validate(bundle)
        self.assertTrue(
            {"state.assignment_handling", "state.handling_required"} & error_codes(validation)
        )

    def test_state_handling_requires_reverse_requirement_assignment(self) -> None:
        requirement = copy.deepcopy(self.requirement)
        requirement["stateModels"][0]["stateAssignments"] = [
            assignment
            for assignment in requirement["stateModels"][0]["stateAssignments"]
            if assignment["elementId"] != "element-tab-role-instance"
        ]
        refresh_approval(requirement)
        bundle = copy.deepcopy(self.bundle)
        bundle["requirement"]["approvedContentSha256"] = requirement["reviewGate"]["approvedContentSha256"]
        validation = self.validate(bundle, requirement)
        self.assertIn("state.assignment_missing", error_codes(validation))

    def test_runtime_dependent_state_requires_accepted_deviation(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        bundle["verification"]["deviations"][0]["status"] = "pending"
        bundle["verification"]["deviations"][0].pop("approvedBy")
        bundle["verification"]["deviations"][0].pop("approvedAt")
        self.assertIn("state.runtime_deviation", error_codes(self.validate(bundle)))

    def test_high_impact_deviation_requires_approval(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        deviation = bundle["verification"]["deviations"][0]
        deviation["impact"] = "high"
        deviation["status"] = "pending"
        deviation.pop("approvedBy")
        deviation.pop("approvedAt")
        self.assertIn("deviation.high_approval", error_codes(self.validate(bundle)))

    def test_node_mapping_rejects_unrelated_accepted_claim(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        bundle["nodeMappings"][0]["claimIds"].append("claim-screen-resolution")
        self.assertIn("mapping.unrelated_claim", error_codes(self.validate(bundle)))

    def test_cross_asset_operation_rejects_unrelated_accepted_claim(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        bundle["crossAssetOperations"][0]["claimIds"] = ["claim-state-branches-variable"]
        self.assertIn("operation.unrelated_claim", error_codes(self.validate(bundle)))

    def test_static_variant_fixture_is_executable_without_runtime_deviation(self) -> None:
        requirement, bundle = make_static_variant_fixture()
        validation = validate_build_bundle(
            bundle,
            self.bundle_schema,
            bundle_path=EXAMPLE_BUNDLE,
            requirement_spec=requirement,
            requirement_schema=self.requirement_schema,
            check_linked_files=False,
        )
        self.assertTrue(validation["valid"], validation)

    def test_static_variant_must_cover_only_its_complete_assigned_state(self) -> None:
        requirement, bundle = make_static_variant_fixture()
        selected_mapping = next(item for item in bundle["nodeMappings"] if item["id"] == "mapping-tab-selected-label")
        selected_mapping["stateRefs"] = ["state-tab-selected", "state-tab-unselected"]
        validation = validate_build_bundle(
            bundle,
            self.bundle_schema,
            bundle_path=EXAMPLE_BUNDLE,
            requirement_spec=requirement,
            requirement_schema=self.requirement_schema,
            check_linked_files=False,
        )
        self.assertTrue({"state.static_variant", "state.handling_refs"} & error_codes(validation))

    def test_acceptance_criterion_coverage_comes_from_verification_checks(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        bundle["verification"]["checks"][2]["requirementRefs"] = []
        bundle["verification"]["checks"][2]["claimIds"] = []
        self.assertIn("coverage.missing", error_codes(self.validate(bundle)))

    def test_completed_and_passed_lifecycle_requires_consistent_assets_and_checks(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        bundle["execution"].update(
            {
                "status": "completed",
                "startedAt": "2026-08-03T15:00:00+08:00",
                "completedAt": "2026-08-03T15:01:00+08:00",
            }
        )
        bundle["verification"]["status"] = "passed"
        for asset in bundle["assets"]:
            asset["status"] = "failed"
        validation = self.validate(bundle)
        self.assertTrue(
            {
                "execution.asset_status",
                "execution.check_status",
                "verification.asset_status",
                "verification.check_status",
            }.issubset(error_codes(validation))
        )

    def test_completed_verified_lifecycle_is_valid(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        bundle["execution"].update(
            {
                "status": "completed",
                "startedAt": "2026-08-03T15:00:00+08:00",
                "completedAt": "2026-08-03T15:01:00+08:00",
            }
        )
        bundle["verification"]["status"] = "passed"
        for asset in bundle["assets"]:
            asset["status"] = "verified"
        for check in bundle["verification"]["checks"]:
            check["status"] = "passed"
        self.assertTrue(self.validate(bundle)["valid"])


class CoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.requirement = load_json(EXAMPLE_REQUIREMENT)
        cls.bundle = load_json(EXAMPLE_BUNDLE)

    def test_example_has_full_accepted_requirement_coverage(self) -> None:
        self.assertTrue(validate_requirement_coverage(copy.deepcopy(self.bundle), copy.deepcopy(self.requirement))["valid"])

    def test_missing_element_mapping_is_reported(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        bundle["nodeMappings"] = [
            mapping
            for mapping in bundle["nodeMappings"]
            if "element-tab-selected-accent" not in mapping["requirementRefs"]
        ]
        validation = validate_requirement_coverage(bundle, copy.deepcopy(self.requirement))
        self.assertIn("coverage.missing", error_codes(validation))

    def test_out_of_scope_requirement_must_not_be_mapped(self) -> None:
        requirement = copy.deepcopy(self.requirement)
        element = next(item for item in requirement["uiModel"]["elements"] if item["id"] == "element-tab-selected-accent")
        element["inBuildScope"] = False
        element["scopedOutReason"] = "Accent is deferred."
        validation = validate_requirement_coverage(copy.deepcopy(self.bundle), requirement)
        self.assertIn("coverage.out_of_scope_mapped", error_codes(validation))

    def test_region_runtime_responsive_state_and_acceptance_coverage_are_independent(self) -> None:
        cases = {
            "region-navigation": ("nodeMappings", "requirementRefs"),
            "runtime-tab-selected-visibility": ("nodeMappings", "requirementRefs"),
            "responsive-navigation-left": ("nodeMappings", "requirementRefs"),
            "state-tab-selected": ("nodeMappings", "stateRefs"),
            "criterion-composite-branches": ("checks", "requirementRefs"),
        }
        for requirement_id, (section, field) in cases.items():
            with self.subTest(requirement_id=requirement_id):
                bundle = copy.deepcopy(self.bundle)
                records = bundle["nodeMappings"] if section == "nodeMappings" else bundle["verification"]["checks"]
                for record in records:
                    if requirement_id in record[field]:
                        record[field].remove(requirement_id)
                self.assertIn(
                    "coverage.missing",
                    error_codes(validate_requirement_coverage(bundle, copy.deepcopy(self.requirement))),
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
