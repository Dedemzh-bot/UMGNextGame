#!/usr/bin/env python3
"""Integration tests for the optional Accepted Build View Bundle gate."""

from __future__ import annotations

import copy
import io
import json
import shutil
import sys
import unittest
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import validate_requirement_coverage as coverage_validator
from _contract_common import ASSETS_ROOT, load_json, sha256_file
from accepted_build_view import build_accepted_build_view
from validate_build_bundle import DEFAULT_SCHEMA as BUNDLE_SCHEMA, validate_build_bundle
from validate_requirement_coverage import validate_requirement_coverage
from validate_requirement_spec import DEFAULT_SCHEMA as REQUIREMENT_SCHEMA


EXAMPLE_REQUIREMENT = ASSETS_ROOT / "example-composite-tabs-requirement.json"
EXAMPLE_BUNDLE = ASSETS_ROOT / "example-composite-tabs-build-bundle.json"


def error_codes(validation: dict) -> set[str]:
    return {
        entry["code"]
        for entry in validation.get("errors", [])
        if isinstance(entry, dict) and isinstance(entry.get("code"), str)
    }


class AcceptedBuildViewBundleIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.requirement = load_json(EXAMPLE_REQUIREMENT)
        cls.bundle = load_json(EXAMPLE_BUNDLE)
        cls.bundle_schema = load_json(BUNDLE_SCHEMA)
        cls.requirement_schema = load_json(REQUIREMENT_SCHEMA)
        cls.requirement_file_sha256 = sha256_file(EXAMPLE_REQUIREMENT)
        cls.view, cls.view_mode, cls.fallback_reason = build_accepted_build_view(EXAMPLE_REQUIREMENT)

    def validate(
        self,
        bundle: dict,
        *,
        requirement: dict | None = None,
        requirement_path: Path | None = EXAMPLE_REQUIREMENT,
        requirement_schema: dict | None = None,
        requirement_schema_path: Path | None = None,
        accepted_build_view: dict | None = None,
        accepted_build_view_path: Path | None = None,
        check_linked_files: bool = True,
    ) -> dict:
        return validate_build_bundle(
            bundle,
            self.bundle_schema,
            bundle_path=EXAMPLE_BUNDLE,
            requirement_spec=copy.deepcopy(self.requirement) if requirement is None else requirement,
            requirement_path=requirement_path,
            requirement_schema=self.requirement_schema if requirement_schema is None else requirement_schema,
            requirement_schema_path=requirement_schema_path,
            accepted_build_view=accepted_build_view,
            accepted_build_view_path=accepted_build_view_path,
            check_linked_files=check_linked_files,
        )

    def test_valid_projected_view_is_an_additional_gate(self) -> None:
        self.assertEqual("projected", self.view_mode)
        self.assertIsNone(self.fallback_reason)
        self.assertTrue(self.view["buildAllowed"])
        validation = self.validate(copy.deepcopy(self.bundle), accepted_build_view=copy.deepcopy(self.view))
        self.assertTrue(validation["valid"], validation)

    def test_old_programmatic_api_without_view_remains_compatible(self) -> None:
        validation = self.validate(copy.deepcopy(self.bundle))
        self.assertTrue(validation["valid"], validation)

    def test_bundle_schema_source_hash_is_unchanged(self) -> None:
        self.assertEqual(
            sha256_file(Path(BUNDLE_SCHEMA)),
            "afe7fd803dd3a36ddaf48d206ef40f1191e016afe48b23b20ad1250642898291",
        )

    def test_view_tampering_is_rejected_by_exact_rebuild(self) -> None:
        view = copy.deepcopy(self.view)
        view["notice"] = "Forged execution projection."
        validation = self.validate(copy.deepcopy(self.bundle), accepted_build_view=view)
        self.assertIn("accepted-build-view.invalid", error_codes(validation))

    def test_memory_and_file_view_comparison_is_type_strict(self) -> None:
        file_view = copy.deepcopy(self.view)
        file_view["buildAllowed"] = 1
        # Ordinary Python equality treats True and 1 as equal.  Supplying both
        # representations must still expose the invalid physical sidecar.
        self.assertEqual(file_view, self.view)
        workspace_temp = Path.cwd() / "Saved" / "CodexUITestTemp"
        workspace_temp.mkdir(parents=True, exist_ok=True)
        temp_root = workspace_temp / f"accepted-view-sidecar-{uuid.uuid4().hex}"
        temp_root.mkdir()
        try:
            view_path = temp_root / "accepted-build-view.json"
            view_path.write_text(json.dumps(file_view, ensure_ascii=False), encoding="utf-8")
            validation = self.validate(
                copy.deepcopy(self.bundle),
                accepted_build_view=copy.deepcopy(self.view),
                accepted_build_view_path=view_path,
            )
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)
        self.assertIn("accepted-build-view.sidecar_value_mismatch", error_codes(validation))

    def test_view_identity_and_requirement_sha_must_match_bundle_link(self) -> None:
        mutations = {
            "accepted-build-view.request_id": lambda view: view.__setitem__("requestId", "different-request"),
            "accepted-build-view.revision": lambda view: view.__setitem__("revision", view["revision"] + 1),
            "accepted-build-view.approval_digest": lambda view: view["bindings"].__setitem__("approvedContentSha256", "0" * 64),
            "accepted-build-view.requirement_file_sha": lambda view: view["bindings"].__setitem__("requirementFileSha256", "0" * 64),
        }
        for expected_code, mutate in mutations.items():
            with self.subTest(expected_code=expected_code):
                view = copy.deepcopy(self.view)
                mutate(view)
                validation = self.validate(copy.deepcopy(self.bundle), accepted_build_view=view)
                self.assertIn(expected_code, error_codes(validation))

    def test_full_fallback_or_build_blocked_view_never_authorizes_bundle(self) -> None:
        view = copy.deepcopy(self.view)
        view["mode"] = "full-fallback"
        view["fallbackReason"] = "projection-coverage-incomplete"
        view["buildAllowed"] = False
        validation = self.validate(copy.deepcopy(self.bundle), accepted_build_view=view)
        self.assertTrue(
            {
                "accepted-build-view.invalid",
                "accepted-build-view.mode",
                "accepted-build-view.build_allowed",
            }.issubset(error_codes(validation)),
            validation,
        )

    def test_skip_linked_files_does_not_skip_requirement_sha_when_view_is_present(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        bundle["requirement"]["sha256"] = "0" * 64
        validation = self.validate(
            bundle,
            accepted_build_view=copy.deepcopy(self.view),
            check_linked_files=False,
        )
        self.assertIn("requirement.sha256", error_codes(validation))
        self.assertIn("accepted-build-view.requirement_file_sha", error_codes(validation))

    def test_view_requires_the_physical_full_requirement_even_with_data(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        bundle["requirement"]["path"] = "missing-full-requirement.json"
        validation = self.validate(
            bundle,
            requirement=copy.deepcopy(self.requirement),
            requirement_path=None,
            accepted_build_view=copy.deepcopy(self.view),
            check_linked_files=False,
        )
        self.assertIn("accepted-build-view.requirement_file_required", error_codes(validation))

    def test_programmatic_requirement_value_must_equal_bound_file(self) -> None:
        different_requirement_path = EXAMPLE_BUNDLE
        different_file_sha256 = sha256_file(different_requirement_path)
        bundle = copy.deepcopy(self.bundle)
        bundle["requirement"]["sha256"] = different_file_sha256
        validation = self.validate(
            bundle,
            requirement=copy.deepcopy(self.requirement),
            requirement_path=different_requirement_path,
            accepted_build_view=copy.deepcopy(self.view),
        )
        self.assertIn("accepted-build-view.requirement_value_mismatch", error_codes(validation))

    def test_programmatic_requirement_comparison_is_type_strict(self) -> None:
        requirement = copy.deepcopy(self.requirement)
        properties = requirement["uiModel"]["elements"][7]["properties"]
        self.assertEqual(properties["buttonSlotPadding"], 0)
        properties["buttonSlotPadding"] = False
        # Ordinary Python equality treats False and 0 as equal.  The binding
        # boundary must retain JSON types and reject this substitution.
        self.assertEqual(requirement, self.requirement)
        validation = self.validate(
            copy.deepcopy(self.bundle),
            requirement=requirement,
            accepted_build_view=copy.deepcopy(self.view),
        )
        self.assertIn("accepted-build-view.requirement_value_mismatch", error_codes(validation))

    def test_full_validation_and_view_rebuild_share_one_schema_authority(self) -> None:
        altered_schema = copy.deepcopy(self.requirement_schema)
        altered_schema["title"] = "Unbound alternate Requirement schema"
        validation = self.validate(
            copy.deepcopy(self.bundle),
            requirement_schema=altered_schema,
            accepted_build_view=copy.deepcopy(self.view),
        )
        self.assertIn(
            "accepted-build-view.requirement_schema_value_mismatch",
            error_codes(validation),
        )

    def test_accepted_build_view_cannot_be_used_as_requirement_spec(self) -> None:
        validation = self.validate(
            copy.deepcopy(self.bundle),
            requirement=copy.deepcopy(self.view),
            accepted_build_view=copy.deepcopy(self.view),
        )
        self.assertIn("requirement.invalid", error_codes(validation))

    def test_bundle_and_coverage_still_find_omissions_from_full_requirement(self) -> None:
        bundle = copy.deepcopy(self.bundle)
        bundle["nodeMappings"] = [
            mapping
            for mapping in bundle["nodeMappings"]
            if "element-tab-selected-accent" not in mapping.get("requirementRefs", [])
        ]
        bundle_validation = self.validate(bundle, accepted_build_view=copy.deepcopy(self.view))
        self.assertIn("coverage.missing", error_codes(bundle_validation))
        coverage_validation = validate_requirement_coverage(bundle, copy.deepcopy(self.requirement))
        self.assertIn("coverage.missing", error_codes(coverage_validation))

    def test_coverage_cli_forwards_view_while_loading_full_requirement(self) -> None:
        view_path = ASSETS_ROOT / "accepted-build-view.sidecar-for-cli-test.json"
        stdout = io.StringIO()
        with (
            patch.object(
                coverage_validator,
                "validate_build_bundle",
                return_value={"valid": True, "errors": [], "warnings": []},
            ) as bundle_gate,
            patch.object(
                sys,
                "argv",
                [
                    str(Path(coverage_validator.__file__).resolve()),
                    str(EXAMPLE_BUNDLE),
                    "--requirement",
                    str(EXAMPLE_REQUIREMENT),
                    "--accepted-build-view",
                    str(view_path),
                ],
            ),
            redirect_stdout(stdout),
        ):
            return_code = coverage_validator.main()
        self.assertEqual(0, return_code, stdout.getvalue())
        self.assertEqual(view_path.resolve(), bundle_gate.call_args.kwargs["accepted_build_view_path"])
        self.assertTrue(json.loads(stdout.getvalue())["valid"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
