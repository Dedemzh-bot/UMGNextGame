#!/usr/bin/env python3
"""Regression tests for the mandatory post-build direct-user acceptance gate."""

from __future__ import annotations

import copy
import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from _document_contract_common import BUILD_ACCEPTANCE_SCHEMA, HANDOFF_SCHEMA, load_json, validate_schema_instance, write_json
from prepare_program_document_contract import build_document_content_contract
from prepare_program_document_contract import main as prepare_document_contract_main
from prepare_program_handoff import main as prepare_handoff_main
from test_document_contracts import FinalizedSources, error_codes
from validate_program_handoff import validate_program_handoff
from validate_program_handoff import main as validate_handoff_main
from validate_program_docx import main as validate_docx_main


class BuildAcceptanceGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sources = FinalizedSources()

    def tearDown(self) -> None:
        self.sources.close()

    def _validate_handoff(self, handoff: dict, acceptance: dict | None = None) -> dict:
        handoff_path = self.sources.root / "ui-program-handoff.json"
        write_json(handoff_path, handoff)
        return validate_program_handoff(
            handoff,
            load_json(HANDOFF_SCHEMA),
            handoff_path=handoff_path,
            requirement=self.sources.requirement,
            requirement_path=self.sources.requirement_path,
            bundle=self.sources.bundle,
            bundle_path=self.sources.bundle_path,
            readback=self.sources.readback,
            readback_path=self.sources.readback_path,
            build_acceptance=acceptance or self.sources.acceptance,
            build_acceptance_path=self.sources.acceptance_path,
        )

    def test_schema_supports_decisions_but_gate_only_accepts_accepted(self) -> None:
        schema = load_json(BUILD_ACCEPTANCE_SCHEMA)
        for status in ("pending", "accepted", "rejected"):
            with self.subTest(status=status):
                value = copy.deepcopy(self.sources.acceptance)
                value["status"] = status
                self.assertEqual([], validate_schema_instance(value, schema))
                report = self.sources.validate_acceptance(value)
                if status == "accepted":
                    self.assertTrue(report["valid"], report["errors"])
                else:
                    self.assertIn("acceptance.not_accepted", error_codes(report))

    def test_direct_user_actor_and_post_readback_time_are_mandatory(self) -> None:
        wrong_actor = copy.deepcopy(self.sources.acceptance)
        wrong_actor["reviewer"] = {"actorType": "agent", "confirmationSource": "generated-artifact"}
        self.assertIn("acceptance.not_direct_user", error_codes(self.sources.validate_acceptance(wrong_actor)))

        early = copy.deepcopy(self.sources.acceptance)
        early["reviewedAt"] = "2026-08-10T10:10:59+08:00"
        self.assertIn("time.acceptance_before_readback", error_codes(self.sources.validate_acceptance(early)))

        no_timezone = copy.deepcopy(self.sources.acceptance)
        no_timezone["reviewedAt"] = "2026-08-10T10:12:00"
        self.assertIn("time.timezone", error_codes(self.sources.validate_acceptance(no_timezone)))

    def test_all_three_file_bindings_are_exact(self) -> None:
        fields = ("requirementBinding", "bundleBinding", "readbackBinding")
        expected_codes = ("binding.requirement", "binding.bundle", "binding.readback")
        for field, code in zip(fields, expected_codes):
            with self.subTest(field=field):
                stale = copy.deepcopy(self.sources.acceptance)
                stale[field]["sha256"] = "0" * 64
                self.assertIn(code, error_codes(self.sources.validate_acceptance(stale)))

    def test_asset_ids_and_paths_must_pairwise_exactly_cover_bundle(self) -> None:
        missing = copy.deepcopy(self.sources.acceptance)
        missing["reviewedAssetIds"].pop()
        missing["reviewedAssetPaths"].pop()
        self.assertIn("coverage.assets", error_codes(self.sources.validate_acceptance(missing)))

        crossed = copy.deepcopy(self.sources.acceptance)
        crossed["reviewedAssetPaths"].reverse()
        self.assertIn("coverage.assets", error_codes(self.sources.validate_acceptance(crossed)))

        extra = copy.deepcopy(self.sources.acceptance)
        extra["reviewedAssetIds"].append("build-extra")
        extra["reviewedAssetPaths"].append("/Game/UI/UMG/Role/umg_extra")
        self.assertIn("coverage.assets", error_codes(self.sources.validate_acceptance(extra)))

    def test_handoff_03_requires_exact_acceptance_source(self) -> None:
        handoff = self.sources.build_handoff()
        self.assertEqual("0.3", handoff["version"])
        self.assertIn("buildAcceptance", handoff["sources"])
        self.assertTrue(self._validate_handoff(handoff)["valid"])

        old_shape = copy.deepcopy(handoff)
        old_shape["version"] = "0.2"
        old_shape["sources"].pop("buildAcceptance")
        self.assertTrue(validate_schema_instance(old_shape, load_json(HANDOFF_SCHEMA)))

        stale_source = copy.deepcopy(handoff)
        stale_source["sources"]["buildAcceptance"]["sha256"] = "f" * 64
        report = self._validate_handoff(stale_source)
        self.assertIn("binding.acceptance", error_codes(report))
        self.assertIn("projection.mismatch", error_codes(report))

        early_handoff = copy.deepcopy(handoff)
        early_handoff["generatedAt"] = "2026-08-10T10:11:00+08:00"
        report = self._validate_handoff(early_handoff)
        self.assertIn("time.handoff_before_acceptance", error_codes(report))

    def test_linked_layout_change_invalidates_the_acceptance_gate(self) -> None:
        asset = self.sources.bundle["assets"][0]
        layout_path = self.sources.root / asset["layoutSpecPath"]
        layout_path.write_text(layout_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        report = self.sources.validate_acceptance()
        self.assertFalse(report["valid"])
        self.assertTrue(
            any(code.startswith("bundle.") or code.startswith("layout.") for code in error_codes(report)),
            report["errors"],
        )

    def test_all_document_gate_clis_require_build_acceptance(self) -> None:
        invocations = (
            (
                prepare_handoff_main,
                [
                    "prepare_program_handoff.py",
                    "--requirement", str(self.sources.requirement_path),
                    "--bundle", str(self.sources.bundle_path),
                    "--readback", str(self.sources.readback_path),
                    "--output", str(self.sources.root / "handoff.json"),
                ],
            ),
            (
                validate_handoff_main,
                [
                    "validate_program_handoff.py",
                    str(self.sources.root / "handoff.json"),
                    "--requirement", str(self.sources.requirement_path),
                    "--bundle", str(self.sources.bundle_path),
                    "--readback", str(self.sources.readback_path),
                ],
            ),
            (
                prepare_document_contract_main,
                [
                    "prepare_program_document_contract.py",
                    "--handoff", str(self.sources.root / "handoff.json"),
                    "--output", str(self.sources.root / "program-document-content.json"),
                ],
            ),
        )
        for cli_main, argv in invocations:
            with self.subTest(command=argv[0]), patch.object(sys, "argv", argv), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    cli_main()
                self.assertEqual(2, raised.exception.code)

    def test_content_contract_cli_requires_every_current_source(self) -> None:
        handoff_path = self.sources.root / "ui-program-handoff.json"
        write_json(handoff_path, self.sources.build_handoff())
        complete = [
            "prepare_program_document_contract.py",
            "--handoff", str(handoff_path),
            "--build-acceptance", str(self.sources.acceptance_path),
            "--requirement", str(self.sources.requirement_path),
            "--bundle", str(self.sources.bundle_path),
            "--readback", str(self.sources.readback_path),
            "--output", str(self.sources.root / "program-document-content.json"),
        ]
        for flag in ("--handoff", "--build-acceptance", "--requirement", "--bundle", "--readback", "--output"):
            with self.subTest(missing=flag):
                index = complete.index(flag)
                argv = complete[:index] + complete[index + 2 :]
                with patch.object(sys, "argv", argv), redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        prepare_document_contract_main()
                    self.assertEqual(2, raised.exception.code)

    def test_final_document_verification_cli_requires_every_current_source(self) -> None:
        handoff_path = self.sources.root / "ui-program-handoff.json"
        write_json(handoff_path, self.sources.build_handoff())
        complete = [
            "validate_program_docx.py",
            "--handoff", str(handoff_path),
            "--build-acceptance", str(self.sources.acceptance_path),
            "--requirement", str(self.sources.requirement_path),
            "--bundle", str(self.sources.bundle_path),
            "--readback", str(self.sources.readback_path),
            "--docx", str(self.sources.root / "placeholder.docx"),
            "--render-dir", str(self.sources.root / "pages"),
            "--verification", str(self.sources.root / "document-verification.json"),
            "--render-evidence", str(self.sources.root / "render-evidence.json"),
        ]
        for flag in ("--handoff", "--build-acceptance", "--requirement", "--bundle", "--readback", "--render-evidence"):
            with self.subTest(missing=flag):
                index = complete.index(flag)
                argv = complete[:index] + complete[index + 2 :]
                with (
                    patch.object(sys, "argv", argv),
                    patch("validate_program_docx._detect_soffice", return_value=None),
                    patch("validate_program_docx._detect_pdftoppm", return_value=None),
                    redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(1, validate_docx_main())

    def test_document_content_cannot_bypass_or_reuse_stale_acceptance(self) -> None:
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
        self.assertEqual("0.2", contract["version"])

        stale = copy.deepcopy(self.sources.acceptance)
        stale["status"] = "rejected"
        with self.assertRaisesRegex(ValueError, "accepted post-build"):
            build_document_content_contract(
                handoff,
                handoff_path,
                stale,
                self.sources.acceptance_path,
                self.sources.requirement,
                self.sources.requirement_path,
                self.sources.bundle,
                self.sources.bundle_path,
                self.sources.readback,
                self.sources.readback_path,
            )

    def test_document_content_revalidates_each_current_source_file(self) -> None:
        handoff = self.sources.build_handoff()
        handoff_path = self.sources.root / "ui-program-handoff.json"
        write_json(handoff_path, handoff)
        for source_path in (
            self.sources.requirement_path,
            self.sources.bundle_path,
            self.sources.readback_path,
        ):
            with self.subTest(source=source_path.name):
                original = source_path.read_bytes()
                try:
                    source_path.write_bytes(original + b"\n")
                    with self.assertRaisesRegex(ValueError, "exact current three sources"):
                        build_document_content_contract(
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
                finally:
                    source_path.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
