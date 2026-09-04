#!/usr/bin/env python3
"""Determinism and authority tests for the production DOCX builder."""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import time
import unittest
import uuid
import zipfile
from pathlib import Path

from docx import Document

from build_program_docx import (
    add_widget_tree_table,
    asset_relationship_summary,
    build_program_docx,
    load_template_helpers,
    resolve_retained_reference,
    visible_docx_text,
)
from create_program_document_template import build_template
from _document_contract_common import SKILL_ROOT, sha256_file, write_json
from prepare_program_document_contract import build_document_content_contract
from test_document_contracts import FinalizedSources
from validate_program_docx import (
    _check_embedded_identifier_styles,
    _check_forbidden_document_policy,
    _check_forbidden_visibility_evidence,
    _check_identifier_coverage,
    _check_v04_asset_structure,
    expected_coverage,
    extract_docx_policy_text,
    extract_docx_text,
    inspect_widget_tree_tables,
)


class ProgramDocxBuilderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sources = FinalizedSources()
        self.handoff_path = self.sources.root / "ui-program-handoff.json"
        self.handoff = self.sources.build_handoff()
        write_json(self.handoff_path, self.handoff)
        self.content_path = self.sources.root / "program-document-content.json"
        self.content = build_document_content_contract(
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
        write_json(self.content_path, self.content)
        self.root = SKILL_ROOT.parents[3] / "Saved" / "CodexUITests" / "program-docx-builder" / uuid.uuid4().hex
        self.root.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
        self.sources.close()

    def _build(self, output_root: Path):
        return build_program_docx(
            handoff_path=self.handoff_path,
            content_path=self.content_path,
            requirement_path=self.sources.requirement_path,
            bundle_path=self.sources.bundle_path,
            readback_path=self.sources.readback_path,
            build_acceptance_path=self.sources.acceptance_path,
            output_root_override=output_root,
        )

    def _cli(self, output_root: Path, hash_seed: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["NEXTGAME_UI_PROGRAM_DOCS_ROOT"] = str(output_root)
        environment["PYTHONHASHSEED"] = hash_seed
        return subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve().parent / "build_program_docx.py"),
                "--handoff",
                str(self.handoff_path),
                "--document-content",
                str(self.content_path),
                "--requirement",
                str(self.sources.requirement_path),
                "--bundle",
                str(self.sources.bundle_path),
                "--readback",
                str(self.sources.readback_path),
                "--build-acceptance",
                str(self.sources.acceptance_path),
            ],
            cwd=output_root,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )

    def test_repeated_and_cross_process_builds_are_byte_identical(self) -> None:
        first_root = self.root / "first"
        second_root = self.root / "second"
        third_root = self.root / "third"
        first = self._build(first_root)
        self.assertTrue(first.changed)
        first_bytes = first.output.read_bytes()
        first_mtime = first.output.stat().st_mtime_ns

        time.sleep(0.01)
        unchanged = self._build(first_root)
        self.assertFalse(unchanged.changed)
        self.assertEqual(first_bytes, unchanged.output.read_bytes())
        self.assertEqual(first_mtime, unchanged.output.stat().st_mtime_ns)

        second_root.mkdir(parents=True)
        third_root.mkdir(parents=True)
        second = self._cli(second_root, "1")
        third = self._cli(third_root, "987654")
        self.assertEqual(0, second.returncode, second.stderr or second.stdout)
        self.assertEqual(0, third.returncode, third.stderr or third.stdout)
        second_output = second_root / self.handoff["output"]["fileName"]
        third_output = third_root / self.handoff["output"]["fileName"]
        self.assertEqual(first_bytes, second_output.read_bytes())
        self.assertEqual(first_bytes, third_output.read_bytes())
        self.assertNotIn(str(second_root), second.stdout)
        self.assertNotIn(str(third_root), third.stdout)

        names: list[str]
        with zipfile.ZipFile(first.output, "r") as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            self.assertEqual(sorted(names), names)
            self.assertTrue(all(info.date_time == (2000, 1, 1, 0, 0, 0) for info in infos))
            self.assertTrue(all(info.compress_type == zipfile.ZIP_STORED for info in infos))
            self.assertTrue(all(info.create_system == 0 and not info.extra and not info.comment for info in infos))
            package_text = "\n".join(
                archive.read(name).decode("utf-8", errors="ignore")
                for name in names
                if name.endswith(".xml") or name.endswith(".rels")
            )
        self.assertFalse(any(name.startswith("customXml/") for name in names))
        self.assertNotIn(str(first_root), package_text)
        self.assertNotIn("requiredSemanticRelationshipStatements", visible_docx_text(first.output))

        strict_errors: list[dict[str, str]] = []
        coverage = expected_coverage(self.handoff)
        _check_identifier_coverage(extract_docx_text(first.output), coverage, strict_errors)
        policy_text = extract_docx_policy_text(first.output)
        _check_forbidden_document_policy(policy_text, strict_errors)
        _check_forbidden_visibility_evidence(policy_text, self.handoff, coverage, strict_errors)
        _check_embedded_identifier_styles(first.output, strict_errors)
        _check_v04_asset_structure(first.output, self.content, strict_errors)
        summaries = inspect_widget_tree_tables(first.output, self.content, strict_errors)
        self.assertEqual([], strict_errors)
        self.assertEqual(len(self.content["widgetTreeTables"]["assets"]), len(summaries))

    def test_stale_content_fails_before_writing_output(self) -> None:
        stale = copy.deepcopy(self.content)
        stale["requiredIdentifiers"]["programVariableIdentifiers"] = []
        stale_path = self.root / "stale-content.json"
        write_json(stale_path, stale)
        output_root = self.root / "stale-output"
        with self.assertRaisesRegex(ValueError, "exact projection"):
            build_program_docx(
                handoff_path=self.handoff_path,
                content_path=stale_path,
                requirement_path=self.sources.requirement_path,
                bundle_path=self.sources.bundle_path,
                readback_path=self.sources.readback_path,
                build_acceptance_path=self.sources.acceptance_path,
                output_root_override=output_root,
            )
        self.assertFalse(output_root.exists())

    def test_cli_redacts_output_root_from_io_failures(self) -> None:
        blocked_root = self.root / "not-a-directory"
        blocked_root.write_text("occupied", encoding="utf-8")
        environment = os.environ.copy()
        environment["NEXTGAME_UI_PROGRAM_DOCS_ROOT"] = str(blocked_root)
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve().parent / "build_program_docx.py"),
                "--handoff",
                str(self.handoff_path),
                "--document-content",
                str(self.content_path),
                "--requirement",
                str(self.sources.requirement_path),
                "--bundle",
                str(self.sources.bundle_path),
                "--readback",
                str(self.sources.readback_path),
                "--build-acceptance",
                str(self.sources.acceptance_path),
            ],
            cwd=self.root,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(1, result.returncode)
        payload = json.loads(result.stdout)
        self.assertEqual({"valid": False, "errorCode": "io-error", "errorType": "FileExistsError"}, payload)
        self.assertNotIn(str(blocked_root), result.stdout)
        self.assertNotIn(str(blocked_root), result.stderr)

    def test_output_filename_symlink_fails_closed_without_overwriting_target(self) -> None:
        output_root = self.root / "symlink-output"
        output_root.mkdir()
        target = output_root / "existing-other.docx"
        sentinel = b"unrelated-docx-sentinel"
        target.write_bytes(sentinel)
        destination = output_root / self.handoff["output"]["fileName"]
        try:
            destination.symlink_to(target.name)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"Symbolic links are unavailable on this platform: {type(error).__name__}")
        with self.assertRaisesRegex(ValueError, "symbolic link or junction"):
            self._build(output_root)
        self.assertEqual(sentinel, target.read_bytes())
        self.assertTrue(destination.is_symlink())

    def test_reuse_only_table_uses_full_merged_width(self) -> None:
        _, helper_path = resolve_retained_reference()
        helpers = load_template_helpers(helper_path)
        document = Document()
        helpers._configure_document(document)
        tree_asset = {
            "assetId": "asset.reuse",
            "assetPath": "/Game/UI/UMG/Test/Widgets/uw_test_reuse",
            "parentClassPath": "/Game/UI/Base/BaseReuse.BaseReuse_C",
            "treeRows": [],
            "emptyState": "reuse-only-no-owned-widgets",
        }
        add_widget_tree_table(document, tree_asset, helpers)
        output = self.root / "reuse-only.docx"
        document.save(output)
        content = {
            "widgetTreeTables": {
                "format": "word-native-four-column-asset-detail-table-v2",
                "headers": ["层级 / Widget", "Class", "Is Variable", "程序用途"],
                "indentTwipsPerDepth": 180,
                "assets": [tree_asset],
            }
        }
        errors: list[dict[str, str]] = []
        summaries = inspect_widget_tree_tables(output, content, errors)
        self.assertEqual([], errors)
        self.assertEqual(1, len(summaries))

    def test_program_variables_are_not_duplicated_in_relationship_summary(self) -> None:
        asset = next(value for value in self.handoff["assets"] if value.get("programVariables"))
        summary = asset_relationship_summary(asset)
        self.assertIn("精确定位与用途仅见上表", summary)
        for variable in asset["programVariables"]:
            self.assertNotIn(variable["widgetName"], summary)
            self.assertNotIn(variable["purpose"], summary)

    def test_template_generation_is_byte_identical_to_packaged_reference(self) -> None:
        reference, _ = resolve_retained_reference()
        first = self.root / "reference-a.docx"
        second = self.root / "reference-b.docx"
        build_template(first)
        build_template(second)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(sha256_file(reference), sha256_file(first))


if __name__ == "__main__":
    unittest.main()
