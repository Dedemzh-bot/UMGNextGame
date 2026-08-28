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
from prepare_program_document_contract import build_document_content_contract
from test_document_contracts import FinalizedSources, RENDERER_VERSION, make_png, write_minimal_docx
from validate_program_docx import (
    WIDGET_TREE_EMPTY_LABEL,
    _check_embedded_identifier_styles,
    _check_forbidden_visibility_evidence,
    _detect_pdftoppm,
    create_document_verification,
    create_render_evidence,
    expected_coverage,
    extract_docx_policy_text,
    extract_docx_text,
    inspect_widget_tree_tables,
    probe_soffice,
    validate_document_verification,
    validate_render_evidence,
)


LEGACY_WIDGET_TREE_TEST_WIDTHS = (3969, 3061, 1247)
WIDGET_TREE_TEST_WIDTHS = (2948, 1928, 1134, 2268)
V04_TEST_PARENT_CLASS = "/Script/UMG.UserWidget"
V04_TEST_FUNCTIONAL_SUMMARY = "用于严格验证测试。"
V04_TEST_PROGRAM_RELATIONSHIP = "由程序读取和控制已验证节点。"


def word_paragraph_xml(text: str, style_id: str | None = None) -> str:
    properties = f'<w:pPr><w:pStyle w:val="{escape(style_id)}"/></w:pPr>' if style_id else ""
    return f"<w:p>{properties}<w:r><w:t>{escape(text)}</w:t></w:r></w:p>"


def write_styled_identifier_docx(path: Path, text: str, style_id: str) -> None:
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f'<w:body><w:p><w:pPr><w:pStyle w:val="{escape(style_id)}"/></w:pPr>'
        f'<w:r><w:t>{escape(text)}</w:t></w:r></w:p></w:body></w:document>'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
        archive.writestr("word/document.xml", document_xml)


def write_widget_tree_table_docx(
    path: Path,
    tree_contract: dict,
    *,
    include_drawing: bool = False,
    extra_text: str = "",
    xml_transform=None,
    v04_structure: bool = False,
    body_transform=None,
) -> None:
    namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    widths = (
        LEGACY_WIDGET_TREE_TEST_WIDTHS
        if tree_contract["format"] == "word-native-three-column-table-v1"
        else WIDGET_TREE_TEST_WIDTHS
    )
    total_width = sum(widths)

    def cell(value: str, width: int, *, indent: int = 0, span: int | None = None) -> str:
        span_property = f'<w:gridSpan w:val="{span}"/>' if span is not None else ""
        properties = f'<w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{span_property}</w:tcPr>'
        paragraph_properties = f'<w:pPr><w:ind w:left="{indent}"/></w:pPr>' if indent else ""
        drawing = "<w:r><w:drawing/></w:r>" if include_drawing else ""
        return f"<w:tc>{properties}<w:p>{paragraph_properties}<w:r><w:t>{escape(value)}</w:t></w:r>{drawing}</w:p></w:tc>"

    tables: list[str] = []
    headers = tree_contract["headers"]
    for asset in tree_contract["assets"]:
        header = (
            "<w:tr><w:trPr><w:tblHeader/><w:cantSplit/></w:trPr>"
            + "".join(cell(item, widths[index]) for index, item in enumerate(headers))
            + "</w:tr>"
        )
        rows: list[str] = []
        for row in asset["treeRows"]:
            values = [row["widgetName"], row["className"], "true" if row["isVariable"] else "false"]
            if tree_contract["format"] == "word-native-four-column-asset-detail-table-v2":
                values.append(row["programPurpose"])
            rows.append(
                "<w:tr><w:trPr><w:cantSplit/></w:trPr>"
                + cell(values[0], widths[0], indent=row["depth"] * tree_contract["indentTwipsPerDepth"])
                + "".join(cell(value, widths[index]) for index, value in enumerate(values[1:], start=1))
                + "</w:tr>"
            )
        if not rows:
            rows.append(
                f"<w:tr><w:trPr><w:cantSplit/></w:trPr>"
                f"{cell(WIDGET_TREE_EMPTY_LABEL, total_width, span=len(widths))}</w:tr>"
            )
        table_properties = (
            f'<w:tblPr><w:tblW w:w="{total_width}" w:type="dxa"/>'
            '<w:tblLayout w:type="fixed"/></w:tblPr>'
        )
        table_grid = "<w:tblGrid>" + "".join(
            f'<w:gridCol w:w="{width}"/>' for width in widths
        ) + "</w:tblGrid>"
        tables.append(f"<w:tbl>{table_properties}{table_grid}{header}{''.join(rows)}</w:tbl>")
    extra_paragraphs = "".join(word_paragraph_xml(line) for line in extra_text.splitlines())
    if v04_structure:
        if tree_contract["format"] != "word-native-four-column-asset-detail-table-v2":
            raise ValueError("v04_structure requires the production four-column table format.")
        body_parts = [word_paragraph_xml("1. 资产详细说明", "ToolSection")]
        for asset, table in zip(tree_contract["assets"], tables):
            asset_path = asset["assetPath"]
            basename = asset_path.rsplit("/", 1)[-1]
            parent_class = asset.get("parentClassPath", V04_TEST_PARENT_CLASS)
            body_parts.extend(
                (
                    word_paragraph_xml(f"{basename}测试控件", "Heading2"),
                    word_paragraph_xml(f"资产路径：{asset_path}"),
                    word_paragraph_xml(f"Parent Class：{parent_class}"),
                    word_paragraph_xml(f"功能说明：{V04_TEST_FUNCTIONAL_SUMMARY}"),
                    table,
                    word_paragraph_xml(f"程序接入关系：{V04_TEST_PROGRAM_RELATIONSHIP}"),
                )
            )
        body_xml = "".join(body_parts) + extra_paragraphs
    else:
        body_xml = extra_paragraphs + "".join(tables)
    if body_transform is not None:
        body_xml = body_transform(body_xml)
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{namespace}"><w:body>{body_xml}</w:body></w:document>'
    )
    if xml_transform is not None:
        document_xml = xml_transform(document_xml)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document_xml)


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
        for asset in self.sources.readback["assets"]:
            asset.setdefault("parentClassPath", V04_TEST_PARENT_CLASS)
        write_json(self.sources.readback_path, self.sources.readback)
        self.sources.acceptance["readbackBinding"]["sha256"] = hashlib.sha256(
            self.sources.readback_path.read_bytes()
        ).hexdigest()
        write_json(self.sources.acceptance_path, self.sources.acceptance)
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
        self.identifiers = [
            identifier
            for field, values in coverage.items()
            if field != "semanticRelationshipStatements"
            for identifier in values
        ]
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

    def _verify(
        self,
        evidence: dict,
        evidence_path: Path,
        reviewed_pages: list[str],
        *,
        output_root: Path | None = None,
        document_content: dict | None = None,
        document_content_path: Path | None = None,
    ):
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
                document_content=document_content,
                document_content_path=document_content_path,
            )

    def _build_document_content(self) -> dict:
        return build_document_content_contract(
            self.handoff,
            self.handoff_path,
            self.sources.acceptance,
            self.sources.acceptance_path,
            self.sources.requirement,
            self.sources.requirement_path,
            self.sources.bundle,
            self.sources.bundle_path,
            self.sources.readback,
            self.sources.readback_path,
        )

    def test_fake_soffice_file_is_not_accepted(self) -> None:
        errors: list[dict[str, str]] = []
        self.assertIsNone(probe_soffice(self.soffice, errors))
        self.assertIn("render.soffice_probe", codes(errors))

    def test_collection_and_state_identifiers_cannot_use_title_styles(self) -> None:
        for identifier in ("collection.teammate.entries", "state-model.teammate-life"):
            for style_id in ("ToolAsset", "ToolSection", "Heading2", "Title"):
                with self.subTest(identifier=identifier, style_id=style_id):
                    write_styled_identifier_docx(self.docx_path, f"动态集合：{identifier}", style_id)
                    errors: list[dict[str, str]] = []
                    _check_embedded_identifier_styles(self.docx_path, errors)
                    self.assertIn("document.embedded_identifier_title_style", codes(errors))

            write_styled_identifier_docx(self.docx_path, f"动态集合：{identifier}", "Normal")
            errors = []
            _check_embedded_identifier_styles(self.docx_path, errors)
            self.assertEqual([], errors)

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

    def test_v03_requires_exact_native_widget_tree_tables(self) -> None:
        contract = {
            "format": "word-native-three-column-table-v1",
            "headers": ["层级 / Widget", "Class", "Is Variable"],
            "indentTwipsPerDepth": 180,
            "assets": [
                {
                    "assetId": "asset-tree",
                    "assetPath": "/Game/UI/UMG/Fight/uw_tree",
                    "treeRows": [
                        {"depth": 0, "widgetName": "Root", "className": "CanvasPanel", "isVariable": False},
                        {"depth": 1, "widgetName": "Child", "className": "TextBlock", "isVariable": True},
                    ],
                }
            ],
        }
        content = {"widgetTreeTables": contract}
        write_widget_tree_table_docx(self.docx_path, contract)
        errors: list[dict[str, str]] = []
        summaries = inspect_widget_tree_tables(self.docx_path, content, errors)
        self.assertEqual([], errors)
        self.assertEqual(2, summaries[0]["rowCount"])

        write_minimal_docx(self.docx_path, "Root CanvasPanel false Child TextBlock true")
        errors = []
        inspect_widget_tree_tables(self.docx_path, content, errors)
        self.assertIn("document.widget_tree_table_missing", codes(errors))

    def test_v04_requires_exact_four_column_purpose_content_and_geometry(self) -> None:
        contract = {
            "format": "word-native-four-column-asset-detail-table-v2",
            "headers": ["层级 / Widget", "Class", "Is Variable", "程序用途"],
            "indentTwipsPerDepth": 180,
            "assets": [
                {
                    "assetId": "asset-tree",
                    "assetPath": "/Game/UI/UMG/Fight/uw_tree",
                    "treeRows": [
                        {
                            "depth": 0,
                            "widgetName": "Root",
                            "className": "CanvasPanel",
                            "isVariable": False,
                            "programPurpose": "",
                        },
                        {
                            "depth": 1,
                            "widgetName": "Child",
                            "className": "TextBlock",
                            "isVariable": True,
                            "programPurpose": "程序控制文本内容",
                        },
                    ],
                }
            ],
        }
        content = {"widgetTreeTables": contract}
        write_widget_tree_table_docx(self.docx_path, contract)
        errors: list[dict[str, str]] = []
        summaries = inspect_widget_tree_tables(self.docx_path, content, errors)
        self.assertEqual([], errors)
        self.assertEqual(2, summaries[0]["rowCount"])

        write_widget_tree_table_docx(
            self.docx_path,
            contract,
            xml_transform=lambda xml: xml.replace("程序控制文本内容", "", 1),
        )
        errors = []
        inspect_widget_tree_tables(self.docx_path, content, errors)
        self.assertIn("document.widget_tree_row_content", codes(errors))

        write_widget_tree_table_docx(
            self.docx_path,
            contract,
            xml_transform=lambda xml: xml.replace('w:w="2268"', 'w:w="2267"', 1),
        )
        errors = []
        inspect_widget_tree_tables(self.docx_path, content, errors)
        self.assertIn("document.widget_tree_table_grid", codes(errors))

        empty = copy.deepcopy(contract)
        empty["assets"][0]["treeRows"] = []
        empty["assets"][0]["emptyState"] = "reuse-only-no-owned-widgets"
        write_widget_tree_table_docx(self.docx_path, empty)
        errors = []
        inspect_widget_tree_tables(self.docx_path, {"widgetTreeTables": empty}, errors)
        self.assertEqual([], errors)

    def test_v03_rejects_drawing_inside_table_and_accepts_fixed_empty_reuse_row(self) -> None:
        non_empty = {
            "format": "word-native-three-column-table-v1",
            "headers": ["层级 / Widget", "Class", "Is Variable"],
            "indentTwipsPerDepth": 180,
            "assets": [
                {
                    "assetId": "asset-tree",
                    "assetPath": "/Game/UI/UMG/Fight/uw_tree",
                    "treeRows": [{"depth": 0, "widgetName": "Root", "className": "CanvasPanel", "isVariable": False}],
                }
            ],
        }
        write_widget_tree_table_docx(self.docx_path, non_empty, include_drawing=True)
        errors: list[dict[str, str]] = []
        inspect_widget_tree_tables(self.docx_path, {"widgetTreeTables": non_empty}, errors)
        self.assertIn("document.widget_tree_image_substitution", codes(errors))

        empty = copy.deepcopy(non_empty)
        empty["assets"][0]["treeRows"] = []
        empty["assets"][0]["emptyState"] = "reuse-only-no-owned-widgets"
        write_widget_tree_table_docx(self.docx_path, empty)
        errors = []
        summaries = inspect_widget_tree_tables(self.docx_path, {"widgetTreeTables": empty}, errors)
        self.assertEqual([], errors)
        self.assertEqual("reuse-only-no-owned-widgets", summaries[0]["emptyState"])

    def test_v03_enforces_exact_widget_tree_table_geometry(self) -> None:
        contract = {
            "format": "word-native-three-column-table-v1",
            "headers": ["层级 / Widget", "Class", "Is Variable"],
            "indentTwipsPerDepth": 180,
            "assets": [
                {
                    "assetId": "asset-tree",
                    "assetPath": "/Game/UI/UMG/Fight/uw_tree",
                    "treeRows": [
                        {"depth": 0, "widgetName": "Root", "className": "CanvasPanel", "isVariable": False},
                        {"depth": 1, "widgetName": "Child", "className": "TextBlock", "isVariable": True},
                    ],
                }
            ],
        }
        table_grid = (
            '<w:tblGrid><w:gridCol w:w="3969"/><w:gridCol w:w="3061"/>'
            '<w:gridCol w:w="1247"/></w:tblGrid>'
        )
        cases = [
            (
                "missing-layout",
                lambda xml: xml.replace('<w:tblLayout w:type="fixed"/>', "", 1),
                "document.widget_tree_table_layout",
            ),
            (
                "wrong-layout",
                lambda xml: xml.replace('<w:tblLayout w:type="fixed"/>', '<w:tblLayout w:type="autofit"/>', 1),
                "document.widget_tree_table_layout",
            ),
            (
                "missing-table-width",
                lambda xml: xml.replace('<w:tblW w:w="8277" w:type="dxa"/>', "", 1),
                "document.widget_tree_table_width",
            ),
            (
                "wrong-table-width",
                lambda xml: xml.replace('<w:tblW w:w="8277" w:type="dxa"/>', '<w:tblW w:w="8276" w:type="dxa"/>', 1),
                "document.widget_tree_table_width",
            ),
            (
                "missing-grid",
                lambda xml: xml.replace(table_grid, "", 1),
                "document.widget_tree_table_grid",
            ),
            (
                "wrong-grid",
                lambda xml: xml.replace(table_grid, table_grid.replace('w:w="3061"', 'w:w="3060"'), 1),
                "document.widget_tree_table_grid",
            ),
            (
                "missing-header-cell-width",
                lambda xml: xml.replace('<w:tcW w:w="3969" w:type="dxa"/>', "", 1),
                "document.widget_tree_cell_width",
            ),
            (
                "wrong-header-cell-width",
                lambda xml: xml.replace(
                    '<w:tcW w:w="3969" w:type="dxa"/>',
                    '<w:tcW w:w="3968" w:type="dxa"/>',
                    1,
                ),
                "document.widget_tree_cell_width",
            ),
            (
                "wrong-data-cell-width",
                lambda xml: xml.replace(
                    '<w:tcW w:w="3061" w:type="dxa"/>',
                    '<w:tcW w:w="3060" w:type="dxa"/>',
                    2,
                ).replace('<w:tcW w:w="3060" w:type="dxa"/>', '<w:tcW w:w="3061" w:type="dxa"/>', 1),
                "document.widget_tree_cell_width",
            ),
        ]
        for name, transform, expected_code in cases:
            with self.subTest(name=name):
                write_widget_tree_table_docx(self.docx_path, contract, xml_transform=transform)
                errors: list[dict[str, str]] = []
                inspect_widget_tree_tables(self.docx_path, {"widgetTreeTables": contract}, errors)
                self.assertIn(expected_code, codes(errors))

    def test_v03_enforces_empty_row_width_header_split_and_no_row_heights(self) -> None:
        non_empty = {
            "format": "word-native-three-column-table-v1",
            "headers": ["层级 / Widget", "Class", "Is Variable"],
            "indentTwipsPerDepth": 180,
            "assets": [
                {
                    "assetId": "asset-tree",
                    "assetPath": "/Game/UI/UMG/Fight/uw_tree",
                    "treeRows": [{"depth": 0, "widgetName": "Root", "className": "CanvasPanel", "isVariable": False}],
                }
            ],
        }
        cases = [
            (
                "header-cant-split",
                non_empty,
                lambda xml: xml.replace('<w:tblHeader/><w:cantSplit/>', '<w:tblHeader/>', 1),
                "document.widget_tree_header_split",
            ),
            (
                "header-height",
                non_empty,
                lambda xml: xml.replace('<w:tblHeader/><w:cantSplit/>', '<w:tblHeader/><w:cantSplit/><w:trHeight w:val="240"/>', 1),
                "document.widget_tree_row_height",
            ),
            (
                "data-height",
                non_empty,
                lambda xml: xml.replace('<w:trPr><w:cantSplit/></w:trPr>', '<w:trPr><w:cantSplit/><w:trHeight w:val="240"/></w:trPr>', 1),
                "document.widget_tree_row_height",
            ),
        ]
        empty = copy.deepcopy(non_empty)
        empty["assets"][0]["treeRows"] = []
        empty["assets"][0]["emptyState"] = "reuse-only-no-owned-widgets"
        cases.extend(
            [
                (
                    "empty-height",
                    empty,
                    lambda xml: xml.replace('<w:trPr><w:cantSplit/></w:trPr>', '<w:trPr><w:cantSplit/><w:trHeight w:val="240"/></w:trPr>', 1),
                    "document.widget_tree_row_height",
                ),
                (
                    "empty-cell-width",
                    empty,
                    lambda xml: xml.replace('<w:tcW w:w="8277" w:type="dxa"/>', '<w:tcW w:w="8276" w:type="dxa"/>', 1),
                    "document.widget_tree_cell_width",
                ),
            ]
        )
        for name, contract, transform, expected_code in cases:
            with self.subTest(name=name):
                write_widget_tree_table_docx(self.docx_path, contract, xml_transform=transform)
                errors: list[dict[str, str]] = []
                inspect_widget_tree_tables(self.docx_path, {"widgetTreeTables": contract}, errors)
                self.assertIn(expected_code, codes(errors))

    def test_v03_rejects_negative_and_non_integer_widget_tree_indents(self) -> None:
        contract = {
            "format": "word-native-three-column-table-v1",
            "headers": ["层级 / Widget", "Class", "Is Variable"],
            "indentTwipsPerDepth": 180,
            "assets": [
                {
                    "assetId": "asset-tree",
                    "assetPath": "/Game/UI/UMG/Fight/uw_tree",
                    "treeRows": [
                        {"depth": 0, "widgetName": "Root", "className": "CanvasPanel", "isVariable": False},
                        {"depth": 1, "widgetName": "Child", "className": "TextBlock", "isVariable": True},
                    ],
                }
            ],
        }
        cases = [
            (
                "negative-root",
                lambda xml: xml.replace(
                    '<w:p><w:r><w:t>Root</w:t>',
                    '<w:p><w:pPr><w:ind w:left="-180"/></w:pPr><w:r><w:t>Root</w:t>',
                    1,
                ),
            ),
            (
                "non-integer-child",
                lambda xml: xml.replace('<w:ind w:left="180"/>', '<w:ind w:left="18x"/>', 1),
            ),
        ]
        for name, transform in cases:
            with self.subTest(name=name):
                write_widget_tree_table_docx(self.docx_path, contract, xml_transform=transform)
                errors: list[dict[str, str]] = []
                inspect_widget_tree_tables(self.docx_path, {"widgetTreeTables": contract}, errors)
                self.assertIn("document.widget_tree_depth", codes(errors))

    def test_v04_create_and_revalidate_with_real_content_contract_and_native_tables(self) -> None:
        document_content = self._build_document_content()
        document_content_path = self.sources.root / "program-document-content.json"
        write_json(document_content_path, document_content)
        write_widget_tree_table_docx(
            self.docx_path,
            document_content["widgetTreeTables"],
            extra_text="\n".join(self.identifiers),
            v04_structure=True,
        )
        evidence, evidence_path = self._render_evidence()
        reviewed_pages = [page["fileName"] for page in evidence["pages"]]
        verification, errors = self._verify(
            evidence,
            evidence_path,
            reviewed_pages,
            document_content=document_content,
            document_content_path=document_content_path,
        )
        self.assertEqual([], errors)
        self.assertIsNotNone(verification)
        self.assertEqual("0.4", verification["version"])
        self.assertEqual("word-native-four-column-asset-detail-table-v2", verification["structure"]["widgetTreeFormat"])
        self.assertEqual(len(document_content["widgetTreeTables"]["assets"]), verification["structure"]["tableCount"])

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
                document_content=document_content,
                document_content_path=document_content_path,
            )
        self.assertTrue(report["valid"], report["errors"])

        first_asset = document_content["widgetTreeTables"]["assets"][0]
        asset_path_paragraph = word_paragraph_xml(f"资产路径：{first_asset['assetPath']}")
        parent_class = first_asset.get("parentClassPath", V04_TEST_PARENT_CLASS)
        parent_class_paragraph = word_paragraph_xml(f"Parent Class：{parent_class}")
        write_widget_tree_table_docx(
            self.docx_path,
            document_content["widgetTreeTables"],
            extra_text="\n".join(self.identifiers),
            v04_structure=True,
            body_transform=lambda body: body.replace(
                asset_path_paragraph + parent_class_paragraph,
                parent_class_paragraph + asset_path_paragraph,
                1,
            ),
        )
        with (
            patch("validate_program_docx.probe_soffice", return_value=RENDERER_VERSION),
            patch("validate_program_docx.convert_docx_to_pdf", side_effect=self._fake_pdf_conversion),
            patch("validate_program_docx.render_pdf_to_review_pages", side_effect=self._fake_page_render),
            patch.dict(os.environ, {"NEXTGAME_UI_PROGRAM_DOCS_ROOT": str(self.sources.root)}),
        ):
            stale_report = validate_document_verification(
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
                document_content=document_content,
                document_content_path=document_content_path,
            )
        self.assertFalse(stale_report["valid"])
        self.assertIn("document.asset_block_order", codes(stale_report["errors"]))

    def test_v04_rejects_invalid_asset_detail_structure(self) -> None:
        document_content = self._build_document_content()
        document_content_path = self.sources.root / "program-document-content.json"
        write_json(document_content_path, document_content)
        first_asset = document_content["widgetTreeTables"]["assets"][0]
        asset_path_paragraph = word_paragraph_xml(f"资产路径：{first_asset['assetPath']}")
        parent_class = first_asset.get("parentClassPath", V04_TEST_PARENT_CLASS)
        parent_class_paragraph = word_paragraph_xml(f"Parent Class：{parent_class}")
        functional_summary_paragraph = word_paragraph_xml(f"功能说明：{V04_TEST_FUNCTIONAL_SUMMARY}")

        cases = (
            (
                "wrong-order",
                lambda body: body.replace(
                    asset_path_paragraph + parent_class_paragraph,
                    parent_class_paragraph + asset_path_paragraph,
                    1,
                ),
                "document.asset_block_order",
            ),
            (
                "missing-functional-summary",
                lambda body: body.replace(functional_summary_paragraph, "", 1),
                "document.functional_summary",
            ),
            (
                "legacy-program-variable-heading",
                lambda body: body + word_paragraph_xml("2. 程序变量清单", "ToolSection"),
                "document.forbidden_heading",
            ),
            (
                "empty-other-assets-module",
                lambda body: body + word_paragraph_xml("2. 其他资产程序说明", "ToolSection"),
                "document.other_assets_empty",
            ),
            (
                "legacy-owner-field",
                lambda body: body + word_paragraph_xml("所属资产：/Game/UI/UMG/Role/umg_role"),
                "document.forbidden_asset_field",
            ),
        )
        for name, transform, expected_code in cases:
            with self.subTest(name=name):
                write_widget_tree_table_docx(
                    self.docx_path,
                    document_content["widgetTreeTables"],
                    extra_text="\n".join(self.identifiers),
                    v04_structure=True,
                    body_transform=transform,
                )
                evidence, evidence_path = self._render_evidence()
                reviewed_pages = [page["fileName"] for page in evidence["pages"]]
                verification, errors = self._verify(
                    evidence,
                    evidence_path,
                    reviewed_pages,
                    document_content=document_content,
                    document_content_path=document_content_path,
                )
                self.assertIsNone(verification)
                self.assertIn(expected_code, codes(errors))

    def test_v03_content_and_verification_remain_revalidatable(self) -> None:
        document_content = self._build_document_content()
        document_content["version"] = "0.3"
        document_content["widgetTreeTables"]["format"] = "word-native-three-column-table-v1"
        document_content["widgetTreeTables"]["headers"] = ["层级 / Widget", "Class", "Is Variable"]
        for asset in document_content["widgetTreeTables"]["assets"]:
            asset.pop("parentClassPath", None)
            for row in asset["treeRows"]:
                row.pop("programPurpose")
        document_content_path = self.sources.root / "program-document-content.json"
        write_json(document_content_path, document_content)
        write_widget_tree_table_docx(
            self.docx_path,
            document_content["widgetTreeTables"],
            extra_text="\n".join(self.identifiers),
        )
        evidence, evidence_path = self._render_evidence()
        reviewed_pages = [page["fileName"] for page in evidence["pages"]]
        verification, errors = self._verify(
            evidence,
            evidence_path,
            reviewed_pages,
            document_content=document_content,
            document_content_path=document_content_path,
        )
        self.assertEqual([], errors)
        self.assertIsNotNone(verification)
        self.assertEqual("0.3", verification["version"])
        self.assertEqual("word-native-three-column-table-v1", verification["structure"]["widgetTreeFormat"])

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
                document_content=document_content,
                document_content_path=document_content_path,
            )
        self.assertTrue(report["valid"], report["errors"])

    def test_machine_semantic_relationship_statements_are_not_required_in_docx(self) -> None:
        coverage = expected_coverage(self.handoff)
        self.assertTrue(
            any(value.startswith("State control: ") for value in coverage["semanticRelationshipStatements"])
        )
        write_minimal_docx(self.docx_path, "\n".join(self.identifiers))
        (self.render_dir / "page-1.png").write_bytes(make_png())
        evidence, evidence_path = self._render_evidence()
        verification, errors = self._verify(evidence, evidence_path, ["page-1.png"])
        self.assertEqual([], errors)
        self.assertIsNotNone(verification)

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
        self.assertIn("document.identifier_coverage", codes(errors))

    def test_accepted_deviation_and_state_control_gap_identifiers_cannot_be_omitted(self) -> None:
        coverage = expected_coverage(self.handoff)
        omitted = {
            *coverage["acceptedDeviationIdentifiers"],
            *coverage["stateControlGapIdentifiers"],
        }
        write_minimal_docx(
            self.docx_path,
            "\n".join(item for item in self.identifiers if item not in omitted),
        )
        (self.render_dir / "page-1.png").write_bytes(make_png())
        evidence, evidence_path = self._render_evidence()
        verification, errors = self._verify(evidence, evidence_path, ["page-1.png"])
        self.assertIsNone(verification)
        self.assertIn("document.identifier_coverage", codes(errors))

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
