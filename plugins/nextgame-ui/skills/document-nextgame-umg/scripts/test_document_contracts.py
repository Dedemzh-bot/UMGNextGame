#!/usr/bin/env python3
"""Contract tests for the NextGame UMG documentation stage."""

from __future__ import annotations

import binascii
import copy
import hashlib
import json
import os
import shutil
import struct
import unittest
import zipfile
import zlib
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from xml.sax.saxutils import escape

from _document_contract_common import (
    ANALYSIS_ASSETS,
    ASSETS_ROOT,
    BUILD_ACCEPTANCE_SCHEMA,
    DOCUMENT_VERIFICATION_SCHEMA,
    HANDOFF_SCHEMA,
    PROGRAM_DOCUMENT_CONTENT_SCHEMA,
    READBACK_SCHEMA,
    SKILL_ROOT,
    compute_approved_content_sha256,
    load_json,
    project_widget_tree_tables,
    sha256_file,
    validate_schema_instance,
    write_json,
)
from prepare_program_document_contract import build_document_content_contract
from prepare_program_handoff import build_program_handoff
from validate_build_acceptance import validate_build_acceptance
from validate_program_docx import (
    RENDER_EVIDENCE_SCHEMA,
    create_document_verification,
    create_render_evidence,
    expected_coverage,
    validate_document_verification,
)
from validate_program_handoff import validate_program_handoff
from validate_unreal_widget_readback import validate_unreal_widget_readback


FIXTURES = ASSETS_ROOT / "fixtures"
RENDERER_VERSION = "LibreOffice 25.2.0.0"


def make_png(width: int = 128, height: int = 128) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        checksum = binascii.crc32(kind + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)

    scanlines = b"".join(b"\x00" + (b"\xff\xff\xff" * width) for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(scanlines))
        + chunk(b"IEND", b"")
    )


def make_pdf(marker: bytes = b"fixture") -> bytes:
    return b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n% " + marker + b"\n%%EOF\n"


def fake_convert_docx_to_pdf(
    docx_path: Path,
    render_dir: Path,
    _soffice_path: Path,
    _errors: list[dict[str, str]],
    *,
    destination_path: Path | None = None,
) -> Path:
    pdf_path = destination_path or (render_dir / f"{docx_path.stem}.canonical.pdf")
    marker = hashlib.sha256(docx_path.read_bytes()).hexdigest()[:16].encode("ascii")
    pdf_path.write_bytes(make_pdf(marker))
    return pdf_path


def fake_render_pdf_to_review_pages(
    _pdf_path: Path,
    render_dir: Path,
    _pdftoppm_path: Path,
    _errors: list[dict[str, str]],
    *,
    persist: bool,
) -> tuple[dict, list[dict]]:
    payload = make_png()
    page_path = render_dir / "page-1.png"
    if persist:
        page_path.write_bytes(payload)
    pages = [
        {
            "pageNumber": 1,
            "fileName": page_path.name,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "byteSize": len(payload),
            "width": 128,
            "height": 128,
        }
    ]
    return (
        {
            "tool": "pdftoppm",
            "version": "pdftoppm version 26.05.0",
            "dpi": 150,
            "pageCount": 1,
            "authoritativePagesGenerated": True,
        },
        pages,
    )


def error_codes(report: dict) -> set[str]:
    return {item["code"] for item in report.get("errors", [])}


class FinalizedSources:
    def __init__(self) -> None:
        self.root = SKILL_ROOT.parents[3] / "Saved" / "CodexUITests" / "document-nextgame-umg"
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir(parents=True)
        for name in (
            "example-composite-tabs-requirement.json",
            "example-composite-tabs-child-layout-spec.json",
            "example-composite-tabs-screen-layout-spec.json",
        ):
            shutil.copy2(ANALYSIS_ASSETS / name, self.root / name)
        self.requirement_path = self.root / "example-composite-tabs-requirement.json"
        self.requirement = load_json(self.requirement_path)

        self.bundle_path = self.root / "example-composite-tabs-build-bundle.json"
        self.bundle = load_json(ANALYSIS_ASSETS / self.bundle_path.name)
        self.bundle["requirement"]["sha256"] = sha256_file(self.requirement_path)
        self.bundle["requirement"]["approvedContentSha256"] = self.requirement["reviewGate"]["approvedContentSha256"]
        for asset in self.bundle["assets"]:
            asset["status"] = "verified"
        self.bundle["execution"] = {
            "status": "completed",
            "buildOrderAssetIds": [asset["id"] for asset in sorted(self.bundle["assets"], key=lambda item: item["buildOrder"])],
            "startedAt": "2026-08-10T10:00:00+08:00",
            "completedAt": "2026-08-10T10:10:00+08:00",
        }
        self.bundle["verification"]["status"] = "passed"
        for check in self.bundle["verification"]["checks"]:
            check["status"] = "passed"
        artifact_path = "unreal-widget-readback.json"
        required_readback_checks = ("widget-tree", "key-properties")
        for check in self.bundle["verification"]["checks"]:
            if check.get("type") in required_readback_checks and check.get("assetId"):
                check["artifactPath"] = artifact_path
        existing_readback_checks = {
            (check.get("assetId"), check.get("type"))
            for check in self.bundle["verification"]["checks"]
            if check.get("type") in required_readback_checks
        }
        for asset in self.bundle["assets"]:
            for check_type in required_readback_checks:
                if (asset["id"], check_type) in existing_readback_checks:
                    continue
                self.bundle["verification"]["checks"].append(
                    {
                        "id": f"check-readback-{check_type}-{asset['id']}",
                        "type": check_type,
                        "assetId": asset["id"],
                        "status": "passed",
                        "details": f"Verified {check_type} from the post-save Unreal readback.",
                        "artifactPath": artifact_path,
                        "requirementRefs": [],
                        "claimIds": [],
                    }
                )
        write_json(self.bundle_path, self.bundle)

        self.readback_path = self.root / "unreal-widget-readback.json"
        self.readback = self._make_readback()
        write_json(self.readback_path, self.readback)
        self.acceptance_path = self.root / "ui-build-acceptance.json"
        self.acceptance = self._make_acceptance()
        write_json(self.acceptance_path, self.acceptance)

    def _make_readback(self) -> dict:
        assets = []
        mappings_by_asset: dict[str, list[dict]] = {}
        for mapping in self.bundle["nodeMappings"]:
            mappings_by_asset.setdefault(mapping["assetId"], []).append(mapping)
        for asset in self.bundle["assets"]:
            layout = load_json(self.root / asset["layoutSpecPath"])
            nodes = {node["id"]: node for node in layout["nodes"]}
            widgets = []
            for node in layout["nodes"]:
                properties = node.get("properties", {})
                parent = nodes.get(node.get("parent")) if isinstance(node.get("parent"), str) else None
                widget = {
                    "widgetName": node["name"],
                    "classPath": f"/Script/UMG.{node.get('role', 'Widget').split('.')[-1].title().replace('-', '')}",
                    "parentWidgetName": parent["name"] if parent else None,
                    "isVariable": node.get("isVariable") is True,
                }
                entry = properties.get("entryWidgetClass")
                if isinstance(entry, dict) and isinstance(entry.get("refPath"), str):
                    widget["entryWidgetClass"] = entry["refPath"]
                if isinstance(properties.get("visibility"), str):
                    widget["visibility"] = properties["visibility"]
                widgets.append(widget)
            assets.append(
                {
                    "assetId": asset["id"],
                    "assetPath": asset["assetPath"],
                    "assetObjectPath": f"{asset['assetPath']}.{asset['assetPath'].rsplit('/', 1)[-1]}",
                    "assetClass": "/Script/UMGEditor.WidgetBlueprint",
                    "parentClassPath": "/Script/UIFramework.GameUserWidget",
                    "status": "verified",
                    "widgets": widgets,
                    "nodeMappings": [
                        {
                            "nodeMappingId": mapping["id"],
                            "layoutNodeId": mapping["layoutNodeId"],
                            "widgetName": nodes[mapping["layoutNodeId"]]["name"],
                        }
                        for mapping in mappings_by_asset[asset["id"]]
                    ],
                }
            )
        return {
            "version": "0.1",
            "readbackId": "readback:role-tabs",
            "capturedAt": "2026-08-10T10:11:00+08:00",
            "capturedFrom": "unreal-editor",
            "acquisition": {"method": "official-unreal-mcp"},
            "status": "verified",
            "requirementBinding": {
                "requestId": self.requirement["requestId"],
                "revision": self.requirement["revision"],
                "approvedContentSha256": self.requirement["reviewGate"]["approvedContentSha256"],
                "sha256": sha256_file(self.requirement_path),
            },
            "bundleBinding": {"bundleId": self.bundle["bundleId"], "sha256": sha256_file(self.bundle_path)},
            "assets": assets,
        }

    def validate_readback(self, value: dict | None = None) -> dict:
        return validate_unreal_widget_readback(
            value or self.readback,
            load_json(READBACK_SCHEMA),
            readback_path=self.readback_path,
            requirement=self.requirement,
            requirement_path=self.requirement_path,
            bundle=self.bundle,
            bundle_path=self.bundle_path,
        )

    def _make_acceptance(self) -> dict:
        return {
            "version": "0.1",
            "acceptanceId": "acceptance:role-tabs",
            "phase": "post-build-ui-review",
            "status": "accepted",
            "reviewer": {"actorType": "user", "confirmationSource": "direct-user-message"},
            "reviewedAt": "2026-08-10T10:11:30+08:00",
            "requirementBinding": {
                "requestId": self.requirement["requestId"],
                "revision": self.requirement["revision"],
                "approvedContentSha256": self.requirement["reviewGate"]["approvedContentSha256"],
                "sha256": sha256_file(self.requirement_path),
            },
            "bundleBinding": {"bundleId": self.bundle["bundleId"], "sha256": sha256_file(self.bundle_path)},
            "readbackBinding": {"readbackId": self.readback["readbackId"], "sha256": sha256_file(self.readback_path)},
            "reviewedAssetIds": [asset["id"] for asset in self.bundle["assets"]],
            "reviewedAssetPaths": [asset["assetPath"] for asset in self.bundle["assets"]],
        }

    def validate_acceptance(self, value: dict | None = None) -> dict:
        return validate_build_acceptance(
            value or self.acceptance,
            load_json(BUILD_ACCEPTANCE_SCHEMA),
            acceptance_path=self.acceptance_path,
            requirement=self.requirement,
            requirement_path=self.requirement_path,
            bundle=self.bundle,
            bundle_path=self.bundle_path,
            readback=self.readback,
            readback_path=self.readback_path,
        )

    def build_handoff(
        self,
        requirement: dict | None = None,
        bundle: dict | None = None,
        readback: dict | None = None,
        acceptance: dict | None = None,
    ) -> dict:
        return build_program_handoff(
            requirement or self.requirement,
            bundle or self.bundle,
            readback or self.readback,
            acceptance or self.acceptance,
            requirement_path=self.requirement_path,
            bundle_path=self.bundle_path,
            readback_path=self.readback_path,
            build_acceptance_path=self.acceptance_path,
            output_date="20260810",
            generated_at="2026-08-10T10:12:00+08:00",
        )

    def close(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)


def write_minimal_docx(path: Path, text: str) -> None:
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body><w:p><w:r><w:t>{escape(text)}</w:t></w:r></w:p></w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
        archive.writestr("word/document.xml", document_xml)


class DocumentContractsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sources = FinalizedSources()

    def tearDown(self) -> None:
        self.sources.close()

    def test_minimal_fixtures_match_schemas(self) -> None:
        pairs = (
            ("minimal-unreal-widget-readback.json", READBACK_SCHEMA),
            ("minimal-ui-build-acceptance.json", BUILD_ACCEPTANCE_SCHEMA),
            ("minimal-ui-program-handoff.json", HANDOFF_SCHEMA),
            ("minimal-render-evidence.json", RENDER_EVIDENCE_SCHEMA),
            ("minimal-document-verification.json", DOCUMENT_VERIFICATION_SCHEMA),
        )
        for name, schema_path in pairs:
            self.assertEqual([], validate_schema_instance(load_json(FIXTURES / name), load_json(schema_path)), name)

        verification_v03 = copy.deepcopy(load_json(FIXTURES / "minimal-document-verification.json"))
        verification_v03["version"] = "0.3"
        verification_v03["documentContent"] = {
            "version": "0.3",
            "fileName": "program-document-content.json",
            "sha256": "f" * 64,
        }
        verification_v03["structure"] = {
            "widgetTreeFormat": "word-native-three-column-table-v1",
            "tableCount": 1,
            "tables": [
                {
                    "assetId": "build-screen-role",
                    "assetPath": "/Game/UI/UMG/Role/umg_role",
                    "rowCount": 1,
                    "rowsSha256": "e" * 64,
                }
            ],
        }
        self.assertEqual([], validate_schema_instance(verification_v03, load_json(DOCUMENT_VERIFICATION_SCHEMA)))

        verification_v04 = copy.deepcopy(verification_v03)
        verification_v04["version"] = "0.4"
        verification_v04["documentContent"]["version"] = "0.4"
        verification_v04["structure"]["widgetTreeFormat"] = "word-native-four-column-asset-detail-table-v2"
        self.assertEqual([], validate_schema_instance(verification_v04, load_json(DOCUMENT_VERIFICATION_SCHEMA)))

        verification_v04["documentContent"]["version"] = "0.3"
        self.assertTrue(validate_schema_instance(verification_v04, load_json(DOCUMENT_VERIFICATION_SCHEMA)))

    def test_accepted_post_build_happy_path_filters_static_widgets_and_tracks_state_gap(self) -> None:
        report = self.sources.validate_readback()
        self.assertTrue(report["valid"], report["errors"])
        acceptance_report = self.sources.validate_acceptance()
        self.assertTrue(acceptance_report["valid"], acceptance_report["errors"])
        handoff = self.sources.build_handoff()
        self.assertEqual([], validate_schema_instance(handoff, load_json(HANDOFF_SCHEMA)))
        variable_names = {
            variable["widgetName"] for asset in handoff["assets"] for variable in asset["programVariables"]
        }
        self.assertEqual({"PanelSelected", "PanelUnselected"}, variable_names)
        self.assertNotIn("TxtSelectedLabel", variable_names)
        state_models = [model for asset in handoff["assets"] for model in asset["states"]]
        self.assertEqual(["state-model-navigation-tab"], [model["id"] for model in state_models])
        self.assertEqual(["state-control-input-missing"], [gap["code"] for gap in handoff["gaps"]])
        self.assertTrue(all(gap["code"].startswith("state-control-input-") for gap in handoff["gaps"]))

        handoff_path = self.sources.root / "ui-program-handoff.json"
        write_json(handoff_path, handoff)
        validation = validate_program_handoff(
            handoff,
            load_json(HANDOFF_SCHEMA),
            handoff_path=handoff_path,
            requirement=self.sources.requirement,
            requirement_path=self.sources.requirement_path,
            bundle=self.sources.bundle,
            bundle_path=self.sources.bundle_path,
            readback=self.sources.readback,
            readback_path=self.sources.readback_path,
            build_acceptance=self.sources.acceptance,
            build_acceptance_path=self.sources.acceptance_path,
        )
        self.assertTrue(validation["valid"], validation["errors"])

    def test_requirement_and_bundle_hash_bindings_are_strict(self) -> None:
        stale_requirement = copy.deepcopy(self.sources.readback)
        stale_requirement["requirementBinding"]["sha256"] = "0" * 64
        report = self.sources.validate_readback(stale_requirement)
        self.assertIn("binding.requirement", error_codes(report))

        stale_bundle = copy.deepcopy(self.sources.readback)
        stale_bundle["bundleBinding"]["sha256"] = "1" * 64
        report = self.sources.validate_readback(stale_bundle)
        self.assertIn("binding.bundle", error_codes(report))

    def test_readback_requires_complete_assets_and_node_mappings(self) -> None:
        missing_asset = copy.deepcopy(self.sources.readback)
        missing_asset["assets"].pop()
        report = self.sources.validate_readback(missing_asset)
        self.assertIn("coverage.assets", error_codes(report))

        missing_mapping = copy.deepcopy(self.sources.readback)
        missing_mapping["assets"][0]["nodeMappings"].pop()
        report = self.sources.validate_readback(missing_mapping)
        self.assertIn("coverage.node_mappings", error_codes(report))

    def test_runtime_variable_requires_actual_is_variable(self) -> None:
        altered = copy.deepcopy(self.sources.readback)
        for asset in altered["assets"]:
            for widget in asset["widgets"]:
                if widget["widgetName"] == "PanelSelected":
                    widget["isVariable"] = False
        report = self.sources.validate_readback(altered)
        self.assertIn("runtime.actual_variable", error_codes(report))

    def test_handoff_projects_complete_exclusive_runtime_mirror_as_two_variables(self) -> None:
        requirement = self.sources.requirement
        bundle = self.sources.bundle
        readback = self.sources.readback
        runtime_field_id = "runtime-tab-label"
        requirement["uiModel"]["runtimeFields"].append(
            {
                "id": runtime_field_id,
                "elementId": "element-tab-unselected-label",
                "valueKind": "text",
                "reason": "The same entry label is rendered by both exclusive visual branches.",
                "inBuildScope": True,
                "evidenceIds": ["evidence-selected-tab", "evidence-unselected-tabs"],
                "claimIds": ["claim-tab-composite-state"],
            }
        )
        elements = {
            element["id"]: element for element in requirement["uiModel"]["elements"]
        }
        for element_id in (
            "element-tab-unselected-label",
            "element-tab-selected-label",
        ):
            properties = elements[element_id].setdefault("properties", {})
            properties["isVariable"] = True
            properties["widgetClass"] = "/Script/UMG.TextBlock"

        mapping_states = {
            "mapping-tab-unselected-label": "state-tab-unselected",
            "mapping-tab-selected-label": "state-tab-selected",
        }
        for mapping in bundle["nodeMappings"]:
            state_id = mapping_states.get(mapping["id"])
            if state_id is None:
                continue
            mapping["requirementRefs"].append(runtime_field_id)
            mapping["stateRefs"] = [state_id]

        child_asset = next(
            asset
            for asset in bundle["assets"]
            if asset["id"] == "build-child-navigation-tab"
        )
        layout_path = self.sources.root / child_asset["layoutSpecPath"]
        layout = load_json(layout_path)
        for node in layout["nodes"]:
            if node["id"] in {"node-tab-unselected-label", "node-tab-selected-label"}:
                node["isVariable"] = True
        write_json(layout_path, layout)
        child_asset["layoutSpecSha256"] = sha256_file(layout_path)

        child_readback = next(
            asset
            for asset in readback["assets"]
            if asset["assetId"] == "build-child-navigation-tab"
        )
        for widget in child_readback["widgets"]:
            if widget["widgetName"] in {"TxtUnselectedLabel", "TxtSelectedLabel"}:
                widget["isVariable"] = True

        write_json(self.sources.requirement_path, requirement)
        bundle["requirement"]["sha256"] = sha256_file(self.sources.requirement_path)
        write_json(self.sources.bundle_path, bundle)
        readback["requirementBinding"]["sha256"] = sha256_file(
            self.sources.requirement_path
        )
        readback["bundleBinding"]["sha256"] = sha256_file(self.sources.bundle_path)
        write_json(self.sources.readback_path, readback)
        self.sources.acceptance["requirementBinding"]["sha256"] = sha256_file(
            self.sources.requirement_path
        )
        self.sources.acceptance["bundleBinding"]["sha256"] = sha256_file(
            self.sources.bundle_path
        )
        self.sources.acceptance["readbackBinding"]["sha256"] = sha256_file(
            self.sources.readback_path
        )
        write_json(self.sources.acceptance_path, self.sources.acceptance)

        handoff = self.sources.build_handoff()
        variables = [
            variable
            for asset in handoff["assets"]
            for variable in asset["programVariables"]
            if variable["trace"]["runtimeFieldId"] == runtime_field_id
        ]
        self.assertEqual(
            ["TxtSelectedLabel", "TxtUnselectedLabel"],
            sorted(variable["widgetName"] for variable in variables),
        )
        self.assertEqual(
            {
                "TxtSelectedLabel": "element-tab-selected-label",
                "TxtUnselectedLabel": "element-tab-unselected-label",
            },
            {
                variable["widgetName"]: variable["trace"]["elementId"]
                for variable in variables
            },
        )
        self.assertEqual([], validate_schema_instance(handoff, load_json(HANDOFF_SCHEMA)))

    def test_nxue_provenance_requires_reason(self) -> None:
        altered = copy.deepcopy(self.sources.readback)
        altered["acquisition"] = {"method": "nxue-agent"}
        errors = validate_schema_instance(altered, load_json(READBACK_SCHEMA))
        self.assertTrue(errors)
        altered["acquisition"]["fallbackReason"] = "Official MCP lacked the required property read tool."
        self.assertEqual([], validate_schema_instance(altered, load_json(READBACK_SCHEMA)))

    def test_visibility_values_use_the_finite_unreal_enum(self) -> None:
        allowed = {
            "Visible",
            "Collapsed",
            "Hidden",
            "HitTestInvisible",
            "SelfHitTestInvisible",
        }
        readback_schema = load_json(READBACK_SCHEMA)
        handoff_schema = load_json(HANDOFF_SCHEMA)
        self.assertEqual(allowed, set(readback_schema["$defs"]["visibility"]["enum"]))
        self.assertEqual(allowed, set(handoff_schema["$defs"]["visibility"]["enum"]))

        for visibility in sorted(allowed):
            with self.subTest(visibility=visibility):
                readback = load_json(FIXTURES / "minimal-unreal-widget-readback.json")
                readback["assets"][0]["widgets"][0]["visibility"] = visibility
                self.assertEqual([], validate_schema_instance(readback, readback_schema))

                handoff = load_json(FIXTURES / "minimal-ui-program-handoff.json")
                state = handoff["assets"][0]["states"][0]["axes"][0]["states"][0]
                state["actualSavedVisibilityBindings"][0]["visibility"] = visibility
                state["runtimeVisibilityOutcomes"][0]["visibility"] = visibility
                self.assertEqual([], validate_schema_instance(handoff, handoff_schema))

        invalid_readback = load_json(FIXTURES / "minimal-unreal-widget-readback.json")
        invalid_readback["assets"][0]["widgets"][0]["visibility"] = "ScreenSpace"
        self.assertTrue(validate_schema_instance(invalid_readback, readback_schema))

        invalid_handoff = load_json(FIXTURES / "minimal-ui-program-handoff.json")
        invalid_handoff["assets"][0]["states"][0]["axes"][0]["states"][0][
            "runtimeVisibilityOutcomes"
        ][0]["visibility"] = "ScreenSpace"
        self.assertTrue(validate_schema_instance(invalid_handoff, handoff_schema))

    def test_exclusive_strategy_rejects_runtime_visibility_outcomes(self) -> None:
        handoff = load_json(FIXTURES / "minimal-ui-program-handoff.json")
        model = handoff["assets"][0]["states"][0]
        model["implementationStrategy"] = "exclusive-panel-branches"
        self.assertTrue(validate_schema_instance(handoff, load_json(HANDOFF_SCHEMA)))
        for axis in model["axes"]:
            for state in axis["states"]:
                state["runtimeVisibilityOutcomes"] = []
        self.assertEqual([], validate_schema_instance(handoff, load_json(HANDOFF_SCHEMA)))

        state = model["axes"][0]["states"][0]
        saved_binding = copy.deepcopy(state["actualSavedVisibilityBindings"][0])
        state["actualSavedVisibilityBindings"] = []
        self.assertTrue(validate_schema_instance(handoff, load_json(HANDOFF_SCHEMA)))
        state["actualSavedVisibilityBindings"] = [saved_binding, copy.deepcopy(saved_binding)]
        self.assertTrue(validate_schema_instance(handoff, load_json(HANDOFF_SCHEMA)))

    def test_collection_and_state_identifiers_are_first_class(self) -> None:
        fixture = load_json(FIXTURES / "minimal-ui-program-handoff.json")
        coverage = expected_coverage(fixture)
        verification_fixture = load_json(FIXTURES / "minimal-document-verification.json")
        self.assertEqual("0.3", fixture["version"])
        self.assertEqual(coverage, verification_fixture["coverage"])
        self.assertEqual(["ListRole", "collection-role-list"], coverage["collectionIdentifiers"])
        self.assertEqual(["state-model-role-selection"], coverage["stateModelIdentifiers"])
        self.assertEqual(["state-role-selected"], coverage["stateIdentifiers"])
        self.assertEqual([], coverage["stateBranchWidgetIdentifiers"])
        self.assertEqual(["ListRole"], coverage["stateOutcomeWidgetIdentifiers"])
        self.assertEqual("由程序填充", fixture["assets"][0]["collections"][0]["purpose"])

    def test_state_control_inputs_are_projected_or_gap_when_unspecified(self) -> None:
        requirement = copy.deepcopy(self.sources.requirement)
        model = requirement["stateModels"][0]
        model["controlInputs"] = [
            {
                "id": "control-tab-selection",
                "axisId": "axis-tab-selection",
                "kind": "program-state",
                "description": "Program chooses the active navigation tab.",
                "targetStateIds": ["state-tab-selected", "state-tab-unselected"],
                "evidenceIds": ["evidence-project-state-rules"],
                "claimIds": ["claim-state-branches-variable"],
            }
        ]
        handoff = self.sources.build_handoff(requirement=requirement)
        states = [state for asset in handoff["assets"] for state in asset["states"]]
        self.assertEqual(["control-tab-selection"], [item["id"] for item in states[0]["controlInputs"]])
        self.assertEqual([], handoff["gaps"])

        model["controlInputs"][0]["kind"] = "unspecified"
        handoff = self.sources.build_handoff(requirement=requirement)
        self.assertEqual("state-control-input-unspecified", handoff["gaps"][0]["code"])

    def test_schema_rejects_all_four_forbidden_contract_categories(self) -> None:
        forbidden = (
            "generatedContentDataSource",
            "runtimeParameterDefaultValue",
            "eventPayload",
            "collectionItemSchema",
        )
        base = load_json(FIXTURES / "minimal-ui-program-handoff.json")
        for key in forbidden:
            altered = copy.deepcopy(base)
            altered["assets"][0]["programVariables"][0][key] = "forbidden"
            errors = validate_schema_instance(altered, load_json(HANDOFF_SCHEMA))
            self.assertTrue(errors, key)

    def test_document_content_contract_exports_exact_safe_relationship_appendix(self) -> None:
        handoff = self.sources.build_handoff()
        handoff_path = self.sources.root / "ui-program-handoff.json"
        write_json(handoff_path, handoff)

        contract = build_document_content_contract(
            handoff,
            handoff_path,
            self.sources.acceptance,
            self.sources.acceptance_path,
            self.sources.requirement,
            self.sources.requirement_path,
            self.sources.bundle,
            self.sources.bundle_path,
            self.sources.readback,
            self.sources.readback_path,
        )
        coverage = expected_coverage(handoff)

        self.assertEqual("0.4", contract["version"])
        self.assertEqual("word-native-four-column-asset-detail-table-v2", contract["widgetTreeTables"]["format"])
        self.assertEqual(["层级 / Widget", "Class", "Is Variable", "程序用途"], contract["widgetTreeTables"]["headers"])
        self.assertTrue(contract["widgetTreeTables"]["assets"])
        self.assertEqual(sha256_file(handoff_path), contract["handoff"]["sha256"])
        self.assertEqual(
            coverage["semanticRelationshipStatements"],
            contract["requiredSemanticRelationshipStatements"],
        )
        self.assertEqual(
            {key: value for key, value in coverage.items() if key != "semanticRelationshipStatements"},
            contract["requiredIdentifiers"],
        )
        self.assertEqual([], validate_schema_instance(contract, load_json(PROGRAM_DOCUMENT_CONTENT_SCHEMA)))
        first_row = contract["widgetTreeTables"]["assets"][0]["treeRows"][0]
        self.assertEqual({"depth", "widgetName", "className", "isVariable", "programPurpose"}, set(first_row))
        self.assertNotIn("parentWidgetName", first_row)
        self.assertNotIn("classPath", first_row)
        handoff_assets = {asset["assetId"]: asset for asset in handoff["assets"]}
        readback_assets = {asset["assetId"]: asset for asset in self.sources.readback["assets"]}
        for tree_asset in contract["widgetTreeTables"]["assets"]:
            self.assertEqual(
                readback_assets[tree_asset["assetId"]]["parentClassPath"],
                tree_asset["parentClassPath"],
            )
            expected_purposes = {
                variable["widgetName"]: variable["purpose"]
                for variable in handoff_assets[tree_asset["assetId"]]["programVariables"]
            }
            actual_rows = {row["widgetName"]: row for row in tree_asset["treeRows"]}
            self.assertEqual(expected_purposes, {name: actual_rows[name]["programPurpose"] for name in expected_purposes})
            self.assertTrue(all(row["programPurpose"] == "" for name, row in actual_rows.items() if name not in expected_purposes))

    def test_widget_tree_projection_rejects_missing_parent_and_cycle(self) -> None:
        order = [{"assetId": "asset-tree", "assetPath": "/Game/UI/UMG/Fight/uw_tree"}]
        base = {
            "assets": [
                {
                    "assetId": "asset-tree",
                    "assetPath": "/Game/UI/UMG/Fight/uw_tree",
                    "parentClassPath": "/Script/UIFramework.GameUserWidget",
                    "representationKind": "layout-spec",
                    "widgets": [
                        {"widgetName": "Root", "classPath": "/Script/UMG.CanvasPanel", "parentWidgetName": None, "isVariable": False},
                        {"widgetName": "Child", "classPath": "/Script/UMG.TextBlock", "parentWidgetName": "Root", "isVariable": True},
                    ],
                }
            ]
        }
        projected, errors = project_widget_tree_tables(base, order)
        self.assertEqual([], errors)
        self.assertEqual([0, 1], [row["depth"] for row in projected["assets"][0]["treeRows"]])
        self.assertEqual("/Script/UIFramework.GameUserWidget", projected["assets"][0]["parentClassPath"])
        self.assertEqual(["CanvasPanel", "TextBlock"], [row["className"] for row in projected["assets"][0]["treeRows"]])
        self.assertEqual(["", ""], [row["programPurpose"] for row in projected["assets"][0]["treeRows"]])

        purpose_order = copy.deepcopy(order)
        purpose_order[0]["programVariables"] = [
            {"widgetName": "Child", "purpose": "程序控制文本内容"}
        ]
        projected, errors = project_widget_tree_tables(base, purpose_order)
        self.assertEqual([], errors)
        self.assertEqual(["", "程序控制文本内容"], [row["programPurpose"] for row in projected["assets"][0]["treeRows"]])
        self.assertTrue(projected["assets"][0]["treeRows"][1]["isVariable"])

        legacy, errors = project_widget_tree_tables(base, purpose_order, content_version="0.3")
        self.assertEqual([], errors)
        self.assertEqual("word-native-three-column-table-v1", legacy["format"])
        self.assertNotIn("parentClassPath", legacy["assets"][0])
        self.assertNotIn("programPurpose", legacy["assets"][0]["treeRows"][1])

        duplicate_order = copy.deepcopy(purpose_order)
        duplicate_order[0]["programVariables"].append(
            {"widgetName": "Child", "purpose": "程序控制可见状态"}
        )
        _, errors = project_widget_tree_tables(base, duplicate_order)
        self.assertIn("document.widget_tree_program_purpose_duplicate", {item["code"] for item in errors})

        missing_order = copy.deepcopy(order)
        missing_order[0]["programVariables"] = [
            {"widgetName": "Missing", "purpose": "程序控制动态内容"}
        ]
        _, errors = project_widget_tree_tables(base, missing_order)
        self.assertIn("document.widget_tree_program_widget_missing", {item["code"] for item in errors})

        missing = copy.deepcopy(base)
        missing["assets"][0]["widgets"][1]["parentWidgetName"] = "Missing"
        _, errors = project_widget_tree_tables(missing, order)
        self.assertIn("document.widget_tree_parent_missing", {item["code"] for item in errors})

        cycle = copy.deepcopy(base)
        cycle["assets"][0]["widgets"][0]["parentWidgetName"] = "Child"
        _, errors = project_widget_tree_tables(cycle, order)
        self.assertIn("document.widget_tree_cycle", {item["code"] for item in errors})

    def test_widget_tree_projection_represents_empty_reuse_only_without_fake_rows(self) -> None:
        order = [{"assetId": "asset-reuse", "assetPath": "/Game/UI/UMG/Fight/Widgets/uw_reuse"}]
        readback = {
            "assets": [
                {
                    "assetId": "asset-reuse",
                    "assetPath": "/Game/UI/UMG/Fight/Widgets/uw_reuse",
                    "parentClassPath": "/Game/UI/UMG/Common/uw_common.uw_common_C",
                    "representationKind": "reuse-only",
                    "widgets": [],
                }
            ]
        }
        projected, errors = project_widget_tree_tables(readback, order)
        self.assertEqual([], errors)
        self.assertEqual([], projected["assets"][0]["treeRows"])
        self.assertEqual("reuse-only-no-owned-widgets", projected["assets"][0]["emptyState"])

    def test_document_verification_binds_hashes_identifiers_pages_and_review(self) -> None:
        handoff = self.sources.build_handoff()
        handoff_path = self.sources.root / "ui-program-handoff.json"
        write_json(handoff_path, handoff)
        docx_path = self.sources.root / handoff["output"]["fileName"]
        coverage = expected_coverage(handoff)
        identifiers = [identifier for values in coverage.values() for identifier in values]
        write_minimal_docx(docx_path, "\n".join(identifiers))
        render_dir = self.sources.root / "pages"
        render_dir.mkdir()
        (render_dir / "page-1.png").write_bytes(make_png())
        soffice = self.sources.root / "soffice.exe"
        soffice.write_bytes(b"fixture")
        pdftoppm = self.sources.root / "pdftoppm.exe"
        pdftoppm.write_bytes(b"fixture")
        now = datetime.now().astimezone().isoformat(timespec="microseconds")
        with (
            patch("validate_program_docx.probe_soffice", return_value=RENDERER_VERSION),
            patch("validate_program_docx.convert_docx_to_pdf", side_effect=fake_convert_docx_to_pdf),
            patch("validate_program_docx.render_pdf_to_review_pages", side_effect=fake_render_pdf_to_review_pages),
        ):
            render_evidence, errors = create_render_evidence(
                docx_path=docx_path,
                render_dir=render_dir,
                soffice_path=soffice,
                rendered_at=now,
                pdftoppm_path=pdftoppm,
            )
            self.assertEqual([], errors)
            self.assertIsNotNone(render_evidence)
            render_evidence_path = self.sources.root / "render-evidence.json"
            write_json(render_evidence_path, render_evidence)
            with patch.dict(os.environ, {"NEXTGAME_UI_PROGRAM_DOCS_ROOT": str(self.sources.root)}):
                verification, errors = create_document_verification(
                    handoff,
                    handoff_path=handoff_path,
                    build_acceptance=self.sources.acceptance,
                    build_acceptance_path=self.sources.acceptance_path,
                    requirement=self.sources.requirement,
                    requirement_path=self.sources.requirement_path,
                    bundle=self.sources.bundle,
                    bundle_path=self.sources.bundle_path,
                    readback=self.sources.readback,
                    readback_path=self.sources.readback_path,
                    docx_path=docx_path,
                    render_dir=render_dir,
                    render_evidence=render_evidence,
                    render_evidence_path=render_evidence_path,
                    reviewed_by="documents-agent",
                    reviewed_at=datetime.now().astimezone().isoformat(timespec="microseconds"),
                    reviewed_page_files=["page-1.png"],
                    soffice_path=soffice,
                    verified_at=datetime.now().astimezone().isoformat(timespec="microseconds"),
                    pdftoppm_path=pdftoppm,
                )
                self.assertEqual([], errors)
                self.assertIsNotNone(verification)
                self.assertEqual("0.2", verification["version"])
                report = validate_document_verification(
                    verification,
                    handoff=handoff,
                    handoff_path=handoff_path,
                    build_acceptance=self.sources.acceptance,
                    build_acceptance_path=self.sources.acceptance_path,
                    requirement=self.sources.requirement,
                    requirement_path=self.sources.requirement_path,
                    bundle=self.sources.bundle,
                    bundle_path=self.sources.bundle_path,
                    readback=self.sources.readback,
                    readback_path=self.sources.readback_path,
                    docx_path=docx_path,
                    render_dir=render_dir,
                    render_evidence=render_evidence,
                    render_evidence_path=render_evidence_path,
                    soffice_path=soffice,
                    pdftoppm_path=pdftoppm,
                )
                self.assertTrue(report["valid"], report["errors"])

                tampered = copy.deepcopy(verification)
                tampered["document"]["sha256"] = "0" * 64
                report = validate_document_verification(
                    tampered,
                    handoff=handoff,
                    handoff_path=handoff_path,
                    build_acceptance=self.sources.acceptance,
                    build_acceptance_path=self.sources.acceptance_path,
                    requirement=self.sources.requirement,
                    requirement_path=self.sources.requirement_path,
                    bundle=self.sources.bundle,
                    bundle_path=self.sources.bundle_path,
                    readback=self.sources.readback,
                    readback_path=self.sources.readback_path,
                    docx_path=docx_path,
                    render_dir=render_dir,
                    render_evidence=render_evidence,
                    render_evidence_path=render_evidence_path,
                    soffice_path=soffice,
                    pdftoppm_path=pdftoppm,
                )
                self.assertIn("verification.mismatch", error_codes(report))

    def test_document_cannot_pass_without_soffice_or_identifier_coverage(self) -> None:
        handoff = self.sources.build_handoff()
        handoff_path = self.sources.root / "ui-program-handoff.json"
        write_json(handoff_path, handoff)
        docx_path = self.sources.root / handoff["output"]["fileName"]
        write_minimal_docx(docx_path, "incomplete")
        render_dir = self.sources.root / "pages"
        render_dir.mkdir()
        (render_dir / "page-1.png").write_bytes(make_png())
        soffice = self.sources.root / "soffice.exe"
        soffice.write_bytes(b"fixture")
        pdftoppm = self.sources.root / "pdftoppm.exe"
        pdftoppm.write_bytes(b"fixture")
        with (
            patch("validate_program_docx.probe_soffice", return_value=RENDERER_VERSION),
            patch("validate_program_docx.convert_docx_to_pdf", side_effect=fake_convert_docx_to_pdf),
            patch("validate_program_docx.render_pdf_to_review_pages", side_effect=fake_render_pdf_to_review_pages),
        ):
            render_evidence, errors = create_render_evidence(
                docx_path=docx_path,
                render_dir=render_dir,
                soffice_path=soffice,
                rendered_at=datetime.now().astimezone().isoformat(timespec="microseconds"),
                pdftoppm_path=pdftoppm,
            )
        self.assertEqual([], errors)
        render_evidence_path = self.sources.root / "render-evidence.json"
        write_json(render_evidence_path, render_evidence)
        with patch.dict(os.environ, {"NEXTGAME_UI_PROGRAM_DOCS_ROOT": str(self.sources.root)}):
            verification, errors = create_document_verification(
                handoff,
                handoff_path=handoff_path,
                build_acceptance=self.sources.acceptance,
                build_acceptance_path=self.sources.acceptance_path,
                requirement=self.sources.requirement,
                requirement_path=self.sources.requirement_path,
                bundle=self.sources.bundle,
                bundle_path=self.sources.bundle_path,
                readback=self.sources.readback,
                readback_path=self.sources.readback_path,
                docx_path=docx_path,
                render_dir=render_dir,
                render_evidence=render_evidence,
                render_evidence_path=render_evidence_path,
                reviewed_by="documents-agent",
                reviewed_at=datetime.now().astimezone().isoformat(timespec="microseconds"),
                reviewed_page_files=[],
                soffice_path=None,
                verified_at=datetime.now().astimezone().isoformat(timespec="microseconds"),
            )
        self.assertIsNone(verification)
        self.assertIn("render.soffice_missing", {item["code"] for item in errors})
        self.assertIn("document.identifier_coverage", {item["code"] for item in errors})


if __name__ == "__main__":
    unittest.main()
