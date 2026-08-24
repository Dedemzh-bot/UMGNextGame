#!/usr/bin/env python3
"""Strict regression tests for DOCX rendering and review evidence."""

from __future__ import annotations

import copy
import hashlib
import os
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from xml.sax.saxutils import escape

from _document_contract_common import write_json
from test_document_contracts import FinalizedSources, RENDERER_VERSION, make_png, write_minimal_docx
from validate_program_docx import (
    _check_forbidden_visibility_evidence,
    _detect_pdftoppm,
    create_document_verification,
    create_render_evidence,
    expected_coverage,
    extract_docx_policy_text,
    extract_docx_text,
    probe_soffice,
    validate_document_verification,
    validate_render_evidence,
)


def codes(errors: list[dict[str, str]]) -> set[str]:
    return {item["code"] for item in errors}


def make_pdf(marker: bytes = b"fixture") -> bytes:
    return b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n% " + marker + b"\n%%EOF\n"


def timestamp_at_or_after_document_mtime(path: Path) -> str:
    """Return a microsecond timestamp that cannot truncate below the file's nanosecond mtime."""

    minimum_microseconds = (path.stat().st_mtime_ns + 999) // 1000
    minimum = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=minimum_microseconds)
    return max(datetime.now(timezone.utc), minimum).isoformat(timespec="microseconds")


def timestamp_at_or_after_iso8601(value: str) -> str:
    minimum = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    return max(datetime.now(timezone.utc), minimum).isoformat(timespec="microseconds")


class DocumentDocxStrictnessTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sources = FinalizedSources()
        self.handoff = self.sources.build_handoff()
        state_model = next(
            state
            for asset in self.handoff["assets"]
            for state in asset["states"]
        )
        axis = state_model["axes"][0]
        state_model["controlInputs"] = [
            {
                "id": "control-role-selection",
                "axisId": axis["id"],
                "kind": "program-state",
                "description": "根据程序状态选择目标状态",
                "targetStateIds": [axis["states"][0]["id"]],
                "acceptedClaimIds": list(state_model["acceptedClaimIds"]),
            }
        ]
        collection_asset = self.handoff["assets"][0]
        collection_asset["collections"].append(
            {
                "id": "collection-role-list",
                "widgetName": "ListRole",
                "widgetClass": "/Script/UIFramework.LuaListView",
                "entryWidgetClass": "/Game/UI/UMG/Role/Widgets/uw_role_item.uw_role_item_C",
                "purpose": "由程序填充",
                "overflowStrategy": "scroll",
                "trace": {
                    "nodeMappingId": "mapping-role-list",
                    "layoutNodeId": "node-role-list",
                    "acceptedClaimIds": ["claim-role-list"],
                },
            }
        )
        self.handoff_path = self.sources.root / "ui-program-handoff.json"
        write_json(self.handoff_path, self.handoff)
        self.docx_path = self.sources.root / self.handoff["output"]["fileName"]
        coverage = expected_coverage(self.handoff)
        self.identifiers = [identifier for values in coverage.values() for identifier in values]
        write_minimal_docx(self.docx_path, "\n".join(self.identifiers))
        self.render_dir = self.sources.root / "pages"
        self.render_dir.mkdir()
        (self.render_dir / "page-1.png").write_bytes(make_png())
        self.soffice = self.sources.root / "soffice.exe"
        self.soffice.write_bytes(b"not an executable")
        self.pdftoppm = self.sources.root / "pdftoppm.exe"
        self.pdftoppm.write_bytes(b"not an executable")
        self.canonical_page_count = 1

    def tearDown(self) -> None:
        self.sources.close()

    def _fake_pdf_conversion(
        self,
        docx_path: Path,
        render_dir: Path,
        soffice_path: Path,
        errors: list[dict[str, str]],
        *,
        destination_path: Path | None = None,
    ) -> Path:
        self.assertEqual(self.docx_path, docx_path)
        self.assertEqual(self.render_dir, render_dir)
        self.assertEqual(self.soffice, soffice_path)
        pdf_path = destination_path or (render_dir / f"{docx_path.stem}.canonical.pdf")
        marker = hashlib.sha256(docx_path.read_bytes()).hexdigest()[:16].encode("ascii")
        pdf_path.write_bytes(make_pdf(marker))
        return pdf_path

    def _fake_page_render(
        self,
        pdf_path: Path,
        render_dir: Path,
        pdftoppm_path: Path,
        errors: list[dict[str, str]],
        *,
        persist: bool,
    ) -> tuple[dict, list[dict]]:
        canonical_pdf = self.render_dir / f"{self.docx_path.stem}.canonical.pdf"
        self.assertTrue(
            pdf_path == canonical_pdf
            or (pdf_path.parent == self.render_dir and pdf_path.name.startswith(f".{self.docx_path.stem}.verification-"))
        )
        self.assertEqual(self.render_dir, render_dir)
        self.assertEqual(self.pdftoppm, pdftoppm_path)
        pages = []
        final_names = set()
        width_offset = hashlib.sha256(pdf_path.read_bytes()).digest()[0] % 3
        for index in range(1, self.canonical_page_count + 1):
            width = 127 + index + width_offset
            payload = make_png(width=width)
            file_name = f"page-{index}.png"
            final_names.add(file_name)
            if persist:
                (render_dir / file_name).write_bytes(payload)
            pages.append(
                {
                    "pageNumber": index,
                    "fileName": file_name,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "byteSize": len(payload),
                    "width": width,
                    "height": 128,
                }
            )
        if persist:
            for path in render_dir.glob("page-*.png"):
                if path.name not in final_names:
                    path.unlink()
        return (
            {
                "tool": "pdftoppm",
                "version": "pdftoppm version 26.05.0",
                "dpi": 150,
                "pageCount": self.canonical_page_count,
                "authoritativePagesGenerated": True,
            },
            pages,
        )

    def _render_evidence(self) -> tuple[dict, Path]:
        with (
            patch("validate_program_docx.probe_soffice", return_value=RENDERER_VERSION),
            patch("validate_program_docx.convert_docx_to_pdf", side_effect=self._fake_pdf_conversion),
            patch("validate_program_docx.render_pdf_to_review_pages", side_effect=self._fake_page_render),
        ):
            evidence, errors = create_render_evidence(
                docx_path=self.docx_path,
                render_dir=self.render_dir,
                soffice_path=self.soffice,
                rendered_at=timestamp_at_or_after_document_mtime(self.docx_path),
                pdftoppm_path=self.pdftoppm,
            )
        self.assertEqual([], errors)
        self.assertIsNotNone(evidence)
        path = self.sources.root / "render-evidence.json"
        write_json(path, evidence)
        return evidence, path

    def _verify(self, evidence: dict, evidence_path: Path, reviewed_pages: list[str], *, output_root: Path | None = None):
        reviewed_at = timestamp_at_or_after_iso8601(evidence["renderedAt"])
        verified_at = timestamp_at_or_after_iso8601(reviewed_at)
        with (
            patch("validate_program_docx.probe_soffice", return_value=RENDERER_VERSION),
            patch("validate_program_docx.convert_docx_to_pdf", side_effect=self._fake_pdf_conversion),
            patch("validate_program_docx.render_pdf_to_review_pages", side_effect=self._fake_page_render),
            patch.dict(os.environ, {"NEXTGAME_UI_PROGRAM_DOCS_ROOT": str(output_root or self.sources.root)}),
        ):
            return create_document_verification(
                self.handoff,
                handoff_path=self.handoff_path,
                build_acceptance=self.sources.acceptance,
                build_acceptance_path=self.sources.acceptance_path,
                requirement=self.sources.requirement,
                requirement_path=self.sources.requirement_path,
                bundle=self.sources.bundle,
                bundle_path=self.sources.bundle_path,
                readback=self.sources.readback,
                readback_path=self.sources.readback_path,
                docx_path=self.docx_path,
                render_dir=self.render_dir,
                render_evidence=evidence,
                render_evidence_path=evidence_path,
                reviewed_by="documents-agent",
                reviewed_at=reviewed_at,
                reviewed_page_files=reviewed_pages,
                soffice_path=self.soffice,
                verified_at=verified_at,
                pdftoppm_path=self.pdftoppm,
            )

    def test_fake_soffice_file_is_not_accepted(self) -> None:
        errors: list[dict[str, str]] = []
        self.assertIsNone(probe_soffice(self.soffice, errors))
        self.assertIn("render.soffice_probe", codes(errors))

    def test_final_create_and_validate_recheck_each_current_source_file(self) -> None:
        evidence, evidence_path = self._render_evidence()
        reviewed_pages = [page["fileName"] for page in evidence["pages"]]
        verification, errors = self._verify(evidence, evidence_path, reviewed_pages)
        self.assertEqual([], errors)
        self.assertIsNotNone(verification)

        expected_codes = {
            self.sources.requirement_path: "binding.requirement",
            self.sources.bundle_path: "binding.bundle",
            self.sources.readback_path: "binding.readback",
        }
        for source_path, expected_code in expected_codes.items():
            with self.subTest(source=source_path.name):
                original = source_path.read_bytes()
                try:
                    source_path.write_bytes(original + b"\n")

                    created, create_errors = self._verify(evidence, evidence_path, reviewed_pages)
                    self.assertIsNone(created)
                    self.assertIn(expected_code, codes(create_errors))

                    with (
                        patch("validate_program_docx.probe_soffice", return_value=RENDERER_VERSION),
                        patch("validate_program_docx.convert_docx_to_pdf", side_effect=self._fake_pdf_conversion),
                        patch("validate_program_docx.render_pdf_to_review_pages", side_effect=self._fake_page_render),
                        patch.dict(os.environ, {"NEXTGAME_UI_PROGRAM_DOCS_ROOT": str(self.sources.root)}),
                    ):
                        report = validate_document_verification(
                            verification,
                            handoff=self.handoff,
                            handoff_path=self.handoff_path,
                            build_acceptance=self.sources.acceptance,
                            build_acceptance_path=self.sources.acceptance_path,
                            requirement=self.sources.requirement,
                            requirement_path=self.sources.requirement_path,
                            bundle=self.sources.bundle,
                            bundle_path=self.sources.bundle_path,
                            readback=self.sources.readback,
                            readback_path=self.sources.readback_path,
                            docx_path=self.docx_path,
                            render_dir=self.render_dir,
                            render_evidence=evidence,
                            render_evidence_path=evidence_path,
                            soffice_path=self.soffice,
                            pdftoppm_path=self.pdftoppm,
                        )
                    self.assertFalse(report["valid"])
                    self.assertIn(expected_code, codes(report["errors"]))
                finally:
                    source_path.write_bytes(original)

    def test_pdftoppm_detection_skips_broken_override_wrapper_for_native_binary(self) -> None:
        dependencies = self.sources.root / "runtime" / "dependencies"
        wrapper = dependencies / "bin" / "override" / "pdftoppm.cmd"
        native = dependencies / "native" / "poppler" / "Library" / "bin" / "pdftoppm.exe"
        wrapper.parent.mkdir(parents=True)
        native.parent.mkdir(parents=True)
        wrapper.write_bytes(b"broken wrapper")
        native.write_bytes(b"native binary")

        def which(name: str) -> str | None:
            return str(wrapper) if name == "pdftoppm" else None

        with (
            patch("validate_program_docx.shutil.which", side_effect=which),
            patch("validate_program_docx._pdftoppm_works", side_effect=lambda path: path.resolve() == native.resolve()),
        ):
            self.assertEqual(native.resolve(), _detect_pdftoppm(None))

    def test_version_only_soffice_cannot_create_render_evidence(self) -> None:
        with patch("validate_program_docx.probe_soffice", return_value=RENDERER_VERSION):
            evidence, errors = create_render_evidence(
                docx_path=self.docx_path,
                render_dir=self.render_dir,
                soffice_path=self.soffice,
                rendered_at=datetime.now().astimezone().isoformat(timespec="microseconds"),
                pdftoppm_path=self.pdftoppm,
            )
        self.assertIsNone(evidence)
        self.assertIn("render.pdf_conversion", codes(errors))

    def test_render_evidence_requires_pdf_rasterizer(self) -> None:
        with (
            patch("validate_program_docx.probe_soffice", return_value=RENDERER_VERSION),
            patch("validate_program_docx.convert_docx_to_pdf", side_effect=self._fake_pdf_conversion),
        ):
            evidence, errors = create_render_evidence(
                docx_path=self.docx_path,
                render_dir=self.render_dir,
                soffice_path=self.soffice,
                rendered_at=datetime.now().astimezone().isoformat(timespec="microseconds"),
                pdftoppm_path=None,
            )
        self.assertIsNone(evidence)
        self.assertIn("render.pdf_rasterizer_missing", codes(errors))

    def test_render_evidence_binds_fresh_canonical_pdf(self) -> None:
        evidence, _ = self._render_evidence()
        self.assertEqual(f"{self.docx_path.stem}.canonical.pdf", evidence["canonicalPdf"]["fileName"])
        canonical_pdf = self.render_dir / evidence["canonicalPdf"]["fileName"]
        canonical_pdf.write_bytes(make_pdf(b"tampered"))
        with (
            patch("validate_program_docx.probe_soffice", return_value=RENDERER_VERSION),
            patch("validate_program_docx.convert_docx_to_pdf", side_effect=self._fake_pdf_conversion),
            patch("validate_program_docx.render_pdf_to_review_pages", side_effect=self._fake_page_render),
        ):
            valid, errors = validate_render_evidence(
                evidence,
                docx_path=self.docx_path,
                render_dir=self.render_dir,
                soffice_path=self.soffice,
                pdftoppm_path=self.pdftoppm,
            )
        self.assertIsNone(valid)
        self.assertIn("render.pdf_mismatch", codes(errors))

    def test_render_evidence_is_bound_to_current_docx_and_fresh_pages(self) -> None:
        evidence, _ = self._render_evidence()
        write_minimal_docx(self.docx_path, "changed after rendering")
        with (
            patch("validate_program_docx.probe_soffice", return_value=RENDERER_VERSION),
            patch("validate_program_docx.convert_docx_to_pdf", side_effect=self._fake_pdf_conversion),
            patch("validate_program_docx.render_pdf_to_review_pages", side_effect=self._fake_page_render),
        ):
            valid, errors = validate_render_evidence(
                evidence,
                docx_path=self.docx_path,
                render_dir=self.render_dir,
                soffice_path=self.soffice,
                pdftoppm_path=self.pdftoppm,
            )
        self.assertIsNone(valid)
        self.assertIn("render.source_mismatch", codes(errors))
        self.assertIn("render.page_stale", codes(errors))
        self.assertIn("render.docx_page_content", codes(errors))

    def test_review_must_explicitly_cover_every_rendered_page(self) -> None:
        self.canonical_page_count = 2
        evidence, evidence_path = self._render_evidence()
        verification, errors = self._verify(evidence, evidence_path, ["page-1.png"])
        self.assertIsNone(verification)
        self.assertIn("review.page_coverage", codes(errors))

    def test_review_page_content_must_match_fresh_canonical_rasterization(self) -> None:
        evidence, _ = self._render_evidence()
        (self.render_dir / "page-1.png").write_bytes(make_png(width=140))
        with (
            patch("validate_program_docx.probe_soffice", return_value=RENDERER_VERSION),
            patch("validate_program_docx.convert_docx_to_pdf", side_effect=self._fake_pdf_conversion),
            patch("validate_program_docx.render_pdf_to_review_pages", side_effect=self._fake_page_render),
        ):
            valid, errors = validate_render_evidence(
                evidence,
                docx_path=self.docx_path,
                render_dir=self.render_dir,
                soffice_path=self.soffice,
                pdftoppm_path=self.pdftoppm,
            )
        self.assertIsNone(valid)
        self.assertIn("render.pdf_page_content", codes(errors))

    def test_entry_class_and_state_control_or_branch_coverage_cannot_be_omitted(self) -> None:
        coverage = expected_coverage(self.handoff)
        omitted = {
            coverage["collectionEntryClasses"][0],
            *coverage["stateBranchWidgetIdentifiers"],
            *coverage["stateControlDescriptions"],
        }
        write_minimal_docx(
            self.docx_path,
            "\n".join(item for item in self.identifiers if not any(value in item for value in omitted)),
        )
        (self.render_dir / "page-1.png").write_bytes(make_png())
        evidence, evidence_path = self._render_evidence()
        verification, errors = self._verify(evidence, evidence_path, ["page-1.png"])
        self.assertIsNone(verification)
        self.assertIn("document.identifier_coverage", codes(errors))

    def test_exact_semantic_relationship_statement_cannot_be_replaced_by_loose_identifiers(self) -> None:
        coverage = expected_coverage(self.handoff)
        statement = next(
            value for value in coverage["semanticRelationshipStatements"] if value.startswith("State control: ")
        )
        write_minimal_docx(self.docx_path, "\n".join(item for item in self.identifiers if item != statement))
        (self.render_dir / "page-1.png").write_bytes(make_png())
        evidence, evidence_path = self._render_evidence()
        verification, errors = self._verify(evidence, evidence_path, ["page-1.png"])
        self.assertIsNone(verification)
        self.assertIn("document.semantic_coverage", codes(errors))

    def test_comments_and_hidden_runs_do_not_satisfy_visible_document_coverage(self) -> None:
        hidden_contract = escape("\n".join(self.identifiers))
        namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        document_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<w:document xmlns:w="{namespace}"><w:body>'
            '<w:p><w:r><w:t>Visible body only</w:t></w:r></w:p>'
            f'<w:p><w:r><w:rPr><w:vanish/></w:rPr><w:t>{hidden_contract}</w:t></w:r></w:p>'
            '</w:body></w:document>'
        )
        comments_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<w:comments xmlns:w="{namespace}"><w:comment w:id="0">'
            f'<w:p><w:r><w:t>{hidden_contract}</w:t></w:r></w:p>'
            '</w:comment></w:comments>'
        )
        with zipfile.ZipFile(self.docx_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("word/document.xml", document_xml)
            archive.writestr("word/comments.xml", comments_xml)

        visible_text = extract_docx_text(self.docx_path)
        policy_text = extract_docx_policy_text(self.docx_path)
        self.assertIn("Visible body only", visible_text)
        self.assertNotIn(self.identifiers[0], visible_text)
        self.assertIn(self.identifiers[0], policy_text)

        evidence, evidence_path = self._render_evidence()
        verification, errors = self._verify(evidence, evidence_path, ["page-1.png"])
        self.assertIsNone(verification)
        self.assertTrue({"document.identifier_coverage", "document.semantic_coverage"} & codes(errors))

    def test_accepted_deviation_and_state_control_gap_cannot_be_omitted(self) -> None:
        excluded_prefixes = ("Accepted deviation: ", "State-control gap: ")
        write_minimal_docx(
            self.docx_path,
            "\n".join(item for item in self.identifiers if not item.startswith(excluded_prefixes)),
        )
        (self.render_dir / "page-1.png").write_bytes(make_png())
        evidence, evidence_path = self._render_evidence()
        verification, errors = self._verify(evidence, evidence_path, ["page-1.png"])
        self.assertIsNone(verification)
        self.assertIn("document.semantic_coverage", codes(errors))

    def test_canonical_relationships_cover_every_required_relation(self) -> None:
        statements = expected_coverage(self.handoff)["semanticRelationshipStatements"]
        self.assertTrue(any(item.startswith("Target asset: ") and "/Game/UI/UMG/" in item for item in statements))
        self.assertTrue(any(item.startswith("Program variable: ") and "widgetName=" in item for item in statements))
        self.assertTrue(any(item.startswith("Collection EntryClass: ") and "entryWidgetClass=" in item for item in statements))
        self.assertTrue(
            any(
                item.startswith("State control: ") and "axisId=" in item and "targetStateIds=" in item
                for item in statements
            )
        )
        self.assertTrue(
            any(
                item.startswith("State branch: ") and "isDefault=" in item and "visibility=" not in item
                for item in statements
            )
        )
        outcome_handoff = copy.deepcopy(self.handoff)
        outcome_model = outcome_handoff["assets"][0]["states"][0]
        outcome_model["implementationStrategy"] = "shared-tree-properties"
        outcome_state = outcome_model["axes"][0]["states"][0]
        outcome_state["runtimeVisibilityOutcomes"] = copy.deepcopy(
            outcome_state["actualSavedVisibilityBindings"]
        )
        outcome_statements = expected_coverage(outcome_handoff)["semanticRelationshipStatements"]
        self.assertTrue(
            any(
                item.startswith("State outcome: ") and "isDefault=" in item and "visibility=" in item
                for item in outcome_statements
            )
        )
        self.assertTrue(any(item.startswith("Accepted deviation: ") for item in statements))
        self.assertTrue(any(item.startswith("State-control gap: ") for item in statements))

    def test_docx_rejects_narrow_bilingual_forbidden_policy_phrases(self) -> None:
        forbidden_phrases = {
            "generated-lifecycle-en": "Generated content data source: RoleService.",
            "generated-lifecycle-zh": "\u7a0b\u5e8f\u586b\u5145\u5185\u5bb9\u7684\u6570\u636e\u6765\u6e90\uff1aRoleService\u3002",
            "runtime-parameter-en": "Runtime parameter default value: 0.",
            "runtime-parameter-zh": "\u8fd0\u884c\u65f6\u53c2\u6570\u9ed8\u8ba4\u503c\uff1a0\u3002",
            "callback-en": "Callback payload: roleId.",
            "callback-zh": "\u56de\u8c03\u8f7d\u8377\uff1aroleId\u3002",
            "list-item-en": "List item data structure: roleId.",
            "list-item-zh": "\u5217\u8868\u6570\u636e\u9879\u7ed3\u6784\uff1aroleId\u3002",
        }
        for name, phrase in forbidden_phrases.items():
            with self.subTest(name=name):
                write_minimal_docx(self.docx_path, "\n".join([*self.identifiers, phrase]))
                (self.render_dir / "page-1.png").write_bytes(make_png())
                evidence, evidence_path = self._render_evidence()
                verification, errors = self._verify(evidence, evidence_path, ["page-1.png"])
                self.assertIsNone(verification)
                self.assertIn("document.forbidden_policy", codes(errors))

    def test_docx_rejects_saved_visibility_evidence_but_allows_canonical_outcomes(self) -> None:
        forbidden_phrases = {
            "saved-en": "Actual saved Visibility: Collapsed.",
            "readback-en": "Readback Visibility is Collapsed.",
            "designer-en": "Designer initial Visibility = Collapsed.",
            "default-en": "Default Visibility: Collapsed.",
            "saved-zh": "设计器默认可见性：Collapsed。",
            "initial-zh": "初始可见性：Collapsed。",
            "state-branch": 'State branch: stateId="state-role-selected"; visibility="Collapsed"',
            "branch-widget-value": "State branch PanelSelected is Collapsed.",
        }
        for name, phrase in forbidden_phrases.items():
            with self.subTest(name=name):
                write_minimal_docx(self.docx_path, "\n".join([*self.identifiers, phrase]))
                (self.render_dir / "page-1.png").write_bytes(make_png())
                evidence, evidence_path = self._render_evidence()
                verification, errors = self._verify(evidence, evidence_path, ["page-1.png"])
                self.assertIsNone(verification)
                self.assertIn("document.saved_visibility_leak", codes(errors))

        shared_handoff = copy.deepcopy(self.handoff)
        shared_model = next(model for asset in shared_handoff["assets"] for model in asset["states"])
        shared_model["implementationStrategy"] = "shared-tree-properties"
        shared_state = shared_model["axes"][0]["states"][0]
        shared_state["runtimeVisibilityOutcomes"] = copy.deepcopy(
            shared_state["actualSavedVisibilityBindings"]
        )
        coverage = expected_coverage(shared_handoff)
        outcome_statements = [
            statement
            for statement in coverage["semanticRelationshipStatements"]
            if statement.startswith("State outcome: ")
        ]
        self.assertTrue(outcome_statements)
        errors: list[dict[str, str]] = []
        allowed_text = "\n".join(
            [
                *outcome_statements,
                "For the default none state, set Visibility to Collapsed.",
            ]
        )
        _check_forbidden_visibility_evidence(allowed_text, shared_handoff, coverage, errors)
        self.assertEqual([], errors)

    def test_allowed_high_level_program_collection_and_state_content_is_not_rejected(self) -> None:
        allowed = (
            "\u7a0b\u5e8f\u586b\u5145 ListRole \u96c6\u5408\u3002\n"
            "Program variable ListRole is runtime-controlled.\n"
            "State control chooses a target state for the accepted axis."
        )
        write_minimal_docx(self.docx_path, "\n".join([*self.identifiers, allowed]))
        (self.render_dir / "page-1.png").write_bytes(make_png())
        evidence, evidence_path = self._render_evidence()
        verification, errors = self._verify(evidence, evidence_path, ["page-1.png"])
        self.assertEqual([], errors)
        self.assertIsNotNone(verification)

    def test_docx_must_be_under_configured_output_root(self) -> None:
        evidence, evidence_path = self._render_evidence()
        allowed = self.sources.root / "allowed-output"
        allowed.mkdir()
        verification, errors = self._verify(evidence, evidence_path, ["page-1.png"], output_root=allowed)
        self.assertIsNone(verification)
        self.assertIn("output.path_scope", codes(errors))


if __name__ == "__main__":
    unittest.main()
