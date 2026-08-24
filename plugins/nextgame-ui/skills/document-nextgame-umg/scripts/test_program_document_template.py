#!/usr/bin/env python3
"""Regression tests for the packaged neutral program-document template."""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import struct
import unittest
import uuid
import zipfile
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn


SCRIPT_DIR = Path(__file__).resolve().parent
DOCUMENT_SKILL_DIR = SCRIPT_DIR.parent
SKILLS_DIR = DOCUMENT_SKILL_DIR.parent
TEMPLATE_SKILL_DIR = SKILLS_DIR / "artifact-template-nextgame-umg"
REFERENCE_DOCX = TEMPLATE_SKILL_DIR / "assets" / "reference.docx"
PREVIEW_PNG = TEMPLATE_SKILL_DIR / "assets" / "preview.png"
GENERATOR_PATH = SCRIPT_DIR / "create_program_document_template.py"


def document_text(path: Path) -> str:
    document = Document(path)
    values: list[str] = []
    values.extend(paragraph.text for paragraph in document.paragraphs)
    for table in document.tables:
        for row in table.rows:
            values.extend(cell.text for cell in row.cells)
    for section in document.sections:
        values.extend(paragraph.text for paragraph in section.header.paragraphs)
        values.extend(paragraph.text for paragraph in section.footer.paragraphs)
    return "\n".join(values)


def load_generator_module():
    spec = importlib.util.spec_from_file_location("create_program_document_template", GENERATOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load generator: {GENERATOR_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProgramDocumentTemplateTest(unittest.TestCase):
    def test_template_package_is_complete_and_relative(self) -> None:
        expected = (
            TEMPLATE_SKILL_DIR / "SKILL.md",
            TEMPLATE_SKILL_DIR / "artifact-template.json",
            TEMPLATE_SKILL_DIR / "agents" / "openai.yaml",
            REFERENCE_DOCX,
            PREVIEW_PNG,
        )
        for path in expected:
            self.assertTrue(path.is_file(), path)

        metadata = json.loads((TEMPLATE_SKILL_DIR / "artifact-template.json").read_text(encoding="utf-8"))
        self.assertEqual(1, metadata["schemaVersion"])
        self.assertEqual("document", metadata["kind"])
        self.assertEqual("assets/reference.docx", metadata["reference"])
        self.assertEqual("assets/preview.png", metadata["preview"])
        self.assertFalse(Path(metadata["reference"]).is_absolute())
        self.assertFalse(Path(metadata["preview"]).is_absolute())

    def test_reference_has_expected_geometry_styles_and_section_order(self) -> None:
        document = Document(REFERENCE_DOCX)
        self.assertEqual(1, len(document.sections))
        section = document.sections[0]
        self.assertAlmostEqual(210.0, section.page_width.mm, places=1)
        self.assertAlmostEqual(297.0, section.page_height.mm, places=1)
        self.assertAlmostEqual(1.25, section.left_margin.inches, places=2)
        self.assertAlmostEqual(1.25, section.right_margin.inches, places=2)
        self.assertAlmostEqual(1.0, section.top_margin.inches, places=2)
        self.assertAlmostEqual(1.0, section.bottom_margin.inches, places=2)

        self.assertEqual("000000", str(document.styles["Heading 1"].font.color.rgb))
        self.assertEqual("000000", str(document.styles["Heading 2"].font.color.rgb))
        self.assertEqual("2E75B6", str(document.styles["Tool Section"].font.color.rgb))

        text = document_text(REFERENCE_DOCX)
        headings = (
            "1. 目标资产",
            "2. 资产详细说明",
            "3. 程序变量清单",
            "4. 动态集合与 EntryClass",
            "5. 状态模型",
            "6. 构建偏差",
            "7. 状态控制待接入项",
            "附录 A · 机器可追溯语义关系",
        )
        offsets = [text.index(heading) for heading in headings]
        self.assertEqual(sorted(offsets), offsets)

        header_cell = next(
            cell
            for table in document.tables
            for row in table.rows
            for cell in row.cells
            if cell.text == "资产角色"
        )
        shading = header_cell._tc.get_or_add_tcPr().find(qn("w:shd"))
        self.assertIsNotNone(shading)
        self.assertEqual("E7EFF8", shading.get(qn("w:fill")))

    def test_reference_is_neutral_and_contains_no_retained_media(self) -> None:
        text = document_text(REFERENCE_DOCX)
        for forbidden in ("Weapon", "Settlement", "/Game/", "handoff:"):
            self.assertNotIn(forbidden, text)
        for marker in (
            "[SystemFolder]",
            "[TargetAssetPath]",
            "[WidgetTreeRoot]",
            "[ExactSemanticRelationshipStatement]",
        ):
            self.assertIn(marker, text)

        with zipfile.ZipFile(REFERENCE_DOCX) as package:
            names = set(package.namelist())
            relationship_payloads = [
                package.read(name).decode("utf-8", errors="ignore")
                for name in names
                if name.endswith(".rels")
            ]
        self.assertFalse(any(name.startswith("word/media/") for name in names))
        self.assertFalse(any(name.startswith("customXml/") for name in names))
        self.assertNotIn("word/comments.xml", names)
        self.assertFalse(any('TargetMode="External"' in payload for payload in relationship_payloads))

    def test_generator_recreates_the_packaged_reference(self) -> None:
        module = load_generator_module()
        directory = SCRIPT_DIR / f".nextgame-doc-template-{uuid.uuid4().hex}"
        directory.mkdir()
        generated = directory / "reference.docx"
        try:
            module.build_template(generated)
            self.assertEqual(document_text(REFERENCE_DOCX), document_text(generated))
            packaged = Document(REFERENCE_DOCX)
            rebuilt = Document(generated)
            self.assertEqual(packaged.sections[0].page_width, rebuilt.sections[0].page_width)
            self.assertEqual(packaged.sections[0].page_height, rebuilt.sections[0].page_height)
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def test_preview_is_a_rendered_portrait_page(self) -> None:
        payload = PREVIEW_PNG.read_bytes()
        self.assertTrue(payload.startswith(b"\x89PNG\r\n\x1a\n"))
        width, height = struct.unpack(">II", payload[16:24])
        self.assertGreaterEqual(width, 900)
        self.assertGreaterEqual(height, 1200)
        self.assertGreater(height, width)

    def test_document_skill_uses_template_without_machine_paths(self) -> None:
        document_skill = (DOCUMENT_SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        template_contract = (DOCUMENT_SKILL_DIR / "references" / "program-document-template.md").read_text(
            encoding="utf-8"
        )
        template_skill = (TEMPLATE_SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")

        for required in (
            "references/program-document-template.md",
            "../artifact-template-nextgame-umg/artifact-template.json",
            "program-document-content.json",
            "UIProgramHandoff",
            "clone target rows",
            "paginate tall trees",
        ):
            self.assertIn(required, document_skill)

        for required in (
            "A4 portrait",
            "target assets",
            "asset details",
            "program variables",
            "dynamic collections",
            "state controls",
            "accepted build deviations",
            "handoff gaps",
            "semantic trace appendix",
            "requiredSemanticRelationshipStatements",
        ):
            self.assertIn(required, template_contract)

        machine_path = re.compile(r"(?i)(?:\b[A-Z]:[\\/]|\\\\[^\s]+\\|/Users/|/home/|\.codex[\\/]plugins[\\/]cache)")
        for name, content in (
            ("document skill", document_skill),
            ("template contract", template_contract),
            ("template skill", template_skill),
        ):
            self.assertIsNone(machine_path.search(content), name)


if __name__ == "__main__":
    unittest.main()
