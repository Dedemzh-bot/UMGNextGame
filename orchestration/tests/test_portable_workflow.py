from __future__ import annotations

import copy
from contextlib import contextmanager
import hashlib
import json
import shutil
import subprocess
import sys
import unittest
import uuid
from pathlib import Path, PurePosixPath


ORCHESTRATION_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ORCHESTRATION_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))

from portable_workflow import (  # noqa: E402
    ContractError,
    load_json,
    render_dispatch_manifest,
    validate_workflow,
)


WORKFLOW_PATH = ORCHESTRATION_ROOT / "nextgame-ui.requirements.workflow.json"
SCHEMA_PATH = ORCHESTRATION_ROOT / "workflow.schema.json"
CLI_PATH = SCRIPTS_ROOT / "portable_workflow.py"


@contextmanager
def writable_test_directory():
    """Avoid tempfile's restrictive Windows ACLs inside managed workspaces."""

    path = Path(__file__).parent / f"_runtime_{uuid.uuid4().hex}"
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path)


class PortableWorkflowContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = load_json(WORKFLOW_PATH)

    def assert_contract_error(self, workflow: dict, expected_text: str) -> None:
        with self.assertRaises(ContractError) as context:
            validate_workflow(workflow)
        self.assertIn(expected_text, str(context.exception))

    def test_valid_workflow(self) -> None:
        validate_workflow(self.workflow)
        self.assertEqual(9, len(self.workflow["requiredFindingsRoles"]))
        self.assertEqual(3, len(self.workflow["parallelGroups"]))

    def test_schema_is_parseable_and_versioned(self) -> None:
        schema = load_json(SCHEMA_PATH)
        self.assertEqual(
            "urn:nextgame-ui:portable-workflow:1.0.0",
            schema["$id"],
        )
        self.assertEqual("1.0.0", schema["properties"]["contractVersion"]["const"])

    def test_requirement_links_are_relative_to_same_run_root(self) -> None:
        all_steps = self.workflow["steps"]
        output_paths = {
            path_text
            for step in all_steps
            for path_text in step["outputs"]
        }
        gate = next(
            step for step in all_steps if step["id"] == "requirements-confirmation"
        )
        self.assertEqual("ui-requirement.json", gate["gate"]["decisionArtifact"])
        self.assertIn("contexts/normalized-context.json", output_paths)
        self.assertNotIn("contexts/review-context.json", output_paths)
        findings = sorted(path_text for path_text in output_paths if path_text.startswith("findings/"))
        self.assertEqual(9, len(findings))

        requirement_parent = PurePosixPath("ui-requirement.json").parent
        for link in findings + ["contexts/normalized-context.json"]:
            resolved_logical = requirement_parent / PurePosixPath(link)
            self.assertNotIn("..", resolved_logical.parts)
            self.assertFalse(resolved_logical.is_absolute())

        context_aware_steps = {
            "analyze-state-modeling",
            "analyze-data-adaptation",
            "analyze-asset-decomposition",
            "review-state-visual",
            "review-schema-feasibility",
            "review-coverage",
        }
        for step in all_steps:
            if step["id"] in context_aware_steps:
                self.assertIn("contexts/normalized-context.json", step["inputs"])
                self.assertNotIn("contexts/review-context.json", step["inputs"])

    def test_missing_findings_role_fails(self) -> None:
        invalid = copy.deepcopy(self.workflow)
        invalid["requiredFindingsRoles"].remove("coverage-review")
        self.assert_contract_error(
            invalid,
            "must contain each of the nine canonical roles exactly once",
        )

    def test_cycle_fails(self) -> None:
        invalid = copy.deepcopy(self.workflow)
        step = next(item for item in invalid["steps"] if item["id"] == "validate-packet")
        step["dependsOn"] = ["requirements-confirmation"]
        self.assert_contract_error(invalid, "dependency cycle detected")

    def test_skipping_first_user_gate_fails(self) -> None:
        invalid = copy.deepcopy(self.workflow)
        build = invalid["protectedContinuation"]["steps"][0]
        build["dependsOn"] = ["strict-validate-requirement"]
        self.assert_contract_error(
            invalid,
            "build-verified-umg' must depend exactly on ['requirements-confirmation']",
        )

    def test_skipping_second_user_gate_fails(self) -> None:
        invalid = copy.deepcopy(self.workflow)
        document = invalid["protectedContinuation"]["steps"][-1]
        document["dependsOn"] = ["present-build-results"]
        self.assert_contract_error(
            invalid,
            "document-program-handoff' must depend exactly on ['build-results-confirmation']",
        )

    def test_output_escape_fails(self) -> None:
        invalid = copy.deepcopy(self.workflow)
        invalid["steps"][1]["outputs"] = ["../visual-structure.json"]
        self.assert_contract_error(invalid, "artifact-root-relative POSIX path")

    def test_unlisted_root_output_fails(self) -> None:
        invalid = copy.deepcopy(self.workflow)
        invalid["steps"][1]["outputs"] = ["undeclared-root-file.json"]
        self.assert_contract_error(invalid, "escapes allowed output locations")

    def test_duplicate_output_owner_fails(self) -> None:
        invalid = copy.deepcopy(self.workflow)
        invalid["steps"][2]["outputs"] = ["findings/visual-structure.json"]
        self.assert_contract_error(invalid, "has multiple owners")

    def test_parallel_group_membership_fails(self) -> None:
        invalid = copy.deepcopy(self.workflow)
        invalid["parallelGroups"][0]["members"].remove("discover-project-pattern")
        self.assert_contract_error(invalid, "three canonical role waves")

    def test_parallel_group_dependency_divergence_fails(self) -> None:
        invalid = copy.deepcopy(self.workflow)
        text_step = next(
            item for item in invalid["steps"] if item["id"] == "discover-text-requirements"
        )
        text_step["dependsOn"] = []
        self.assert_contract_error(invalid, "members must share the same dependencies")

    def test_plan_materializes_paths_without_running_vendor_api(self) -> None:
        with writable_test_directory() as temporary:
            artifact_root = temporary / "run"
            artifact_root.mkdir()
            request_packet = artifact_root / "incoming" / "packet.json"
            request_packet.parent.mkdir()
            request_packet.write_text('{"requestId":"test"}\n', encoding="utf-8")

            manifest = render_dispatch_manifest(
                self.workflow,
                WORKFLOW_PATH,
                artifact_root,
                request_packet,
            )

            self.assertEqual(
                str(request_packet.resolve()), manifest["requestPacket"]["path"]
            )
            self.assertEqual(
                hashlib.sha256(request_packet.read_bytes()).hexdigest(),
                manifest["requestPacket"]["sha256"],
            )
            self.assertFalse(manifest["adapterContract"]["thisManifestCallsVendorApi"])
            self.assertFalse(manifest["protectedContinuation"]["dispatchByDefault"])
            self.assertTrue(
                manifest["authority"]["failClosedOnMissingOrDigestMismatch"]
            )
            visual = next(
                item
                for item in manifest["steps"]
                if item["stepId"] == "discover-visual-structure"
            )
            expected_output = artifact_root / "findings" / "visual-structure.json"
            self.assertEqual(str(expected_output.resolve()), visual["outputs"][0]["path"])

    def test_request_packet_outside_artifact_root_fails(self) -> None:
        with writable_test_directory() as temporary_root:
            artifact_root = temporary_root / "run"
            artifact_root.mkdir()
            request_packet = temporary_root / "outside.json"
            request_packet.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "must resolve inside artifact root"):
                render_dispatch_manifest(
                    self.workflow,
                    WORKFLOW_PATH,
                    artifact_root,
                    request_packet,
                )

    def test_cli_help_and_validate(self) -> None:
        help_result = subprocess.run(
            [sys.executable, str(CLI_PATH), "--help"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, help_result.returncode, help_result.stderr)
        self.assertIn("vendor-neutral", help_result.stdout)

        validate_result = subprocess.run(
            [
                sys.executable,
                str(CLI_PATH),
                "validate",
                "--workflow",
                str(WORKFLOW_PATH),
                "--schema",
                str(SCHEMA_PATH),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, validate_result.returncode, validate_result.stderr)
        self.assertIn("valid workflow", validate_result.stdout)

    def test_cli_plan_writes_manifest_within_root(self) -> None:
        with writable_test_directory() as temporary:
            artifact_root = temporary / "run"
            artifact_root.mkdir()
            packet = artifact_root / "request-packet.json"
            packet.write_text(json.dumps({"requestId": "test"}), encoding="utf-8")
            output = artifact_root / "status" / "dispatch-manifest.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(CLI_PATH),
                    "plan",
                    "--workflow",
                    str(WORKFLOW_PATH),
                    "--artifact-root",
                    str(artifact_root),
                    "--request-packet",
                    str(packet),
                    "--output",
                    str(output),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            manifest = load_json(output)
            self.assertEqual("1.0.0", manifest["manifestVersion"])


if __name__ == "__main__":
    unittest.main()
