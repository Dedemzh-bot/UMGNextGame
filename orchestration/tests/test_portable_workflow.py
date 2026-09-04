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
from pathlib import Path

try:
    from jsonschema import Draft202012Validator
except ImportError:  # Optional release dependency; core orchestration stays stdlib-only.
    Draft202012Validator = None


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

ROLES = (
    "visual-structure",
    "text-requirements",
    "project-pattern",
    "state-modeling",
    "data-adaptation",
    "asset-decomposition",
    "state-visual-review",
    "schema-feasibility-review",
    "coverage-review",
)
ROLE_TO_STEP = {
    "visual-structure": "discover-visual-structure",
    "text-requirements": "discover-text-requirements",
    "project-pattern": "discover-project-pattern",
    "state-modeling": "analyze-state-modeling",
    "data-adaptation": "analyze-data-adaptation",
    "asset-decomposition": "analyze-asset-decomposition",
    "state-visual-review": "review-state-visual",
    "schema-feasibility-review": "review-schema-feasibility",
    "coverage-review": "review-coverage",
}
FOCUSED_AND_REVIEW_ROLES = ROLES[3:]
REVIEW_ROLES = ROLES[6:]
POLICY = {
    "promptContract": "packet-path-only",
    "historyPolicy": "none",
    "forkTurns": "none",
    "inheritsConversation": False,
    "allowModelOverride": False,
    "allowReasoningOverride": False,
}
BUILD_POLICY = {
    "promptContract": "single-validated-artifact-path",
    "historyPolicy": "none",
    "forkTurns": "none",
    "inheritsConversation": False,
    "allowModelOverride": False,
    "allowReasoningOverride": False,
}
EVIDENCE_CONTRACT = {
    "artifact": "status/ui-build-plan.pre-mutation-valid.json",
    "schemaRef": "build-plan-pre-mutation.schema.json",
    "toolRef": "scripts/build_plan_evidence.py",
    "generationMode": "generate-and-self-validate",
    "revalidationMode": "--validate-only",
    "requiredBeforeEditorMutation": True,
}


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

    def step(self, workflow: dict, step_id: str) -> dict:
        steps = workflow["steps"] + workflow["protectedContinuation"]["steps"]
        return next(item for item in steps if item["id"] == step_id)

    def assert_contract_error(self, workflow: dict, expected_text: str) -> None:
        with self.assertRaises(ContractError) as context:
            validate_workflow(workflow)
        self.assertIn(expected_text, str(context.exception))

    def test_workflow_loader_rejects_duplicate_keys_at_any_depth(self) -> None:
        with writable_test_directory() as directory:
            duplicate = directory / "duplicate-workflow.json"
            duplicate.write_text(
                '{"workflow":{"contractVersion":"2.0.0",'
                '"contractVersion":"bypassed"}}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ContractError, r"duplicate JSON object key 'contractVersion'"
            ):
                load_json(duplicate)

    def test_valid_workflow_is_closed_v2_contract(self) -> None:
        validate_workflow(self.workflow)
        self.assertEqual("2.0.0", self.workflow["contractVersion"])
        self.assertEqual("2.0.0", self.workflow["workflowVersion"])
        self.assertEqual(POLICY, self.workflow["agentDispatchPolicy"])
        self.assertEqual(
            BUILD_POLICY,
            self.workflow["protectedContinuation"]["agentDispatchPolicy"],
        )
        self.assertEqual(
            EVIDENCE_CONTRACT,
            self.workflow["protectedContinuation"]["preMutationEvidenceContract"],
        )
        self.assertEqual(9, len(self.workflow["requiredFindingsRoles"]))
        self.assertEqual(17, len(self.workflow["steps"]))
        self.assertEqual(8, len(self.workflow["protectedContinuation"]["steps"]))

    def test_schema_is_parseable_and_closes_v2_fields(self) -> None:
        schema = load_json(SCHEMA_PATH)
        self.assertEqual("urn:nextgame-ui:portable-workflow:2.0.0", schema["$id"])
        self.assertEqual("2.0.0", schema["properties"]["contractVersion"]["const"])
        self.assertEqual("2.0.0", schema["properties"]["workflowVersion"]["const"])
        self.assertIn("agentDispatchPolicy", schema["required"])
        self.assertIn("agentInputs", schema["$defs"]["step"]["required"])
        policy_schema = schema["$defs"]["agentDispatchPolicy"]
        self.assertFalse(policy_schema["additionalProperties"])
        for key, value in POLICY.items():
            self.assertEqual(value, policy_schema["properties"][key]["const"])
        build_policy_schema = schema["$defs"]["buildAgentDispatchPolicy"]
        self.assertFalse(build_policy_schema["additionalProperties"])
        for key, value in BUILD_POLICY.items():
            self.assertEqual(value, build_policy_schema["properties"][key]["const"])
        evidence_schema = schema["$defs"]["preMutationEvidenceContract"]
        self.assertFalse(evidence_schema["additionalProperties"])
        for key, value in EVIDENCE_CONTRACT.items():
            self.assertEqual(value, evidence_schema["properties"][key]["const"])
        self.assertEqual(
            {"planned-layout-specs", "deterministic-build-plans"},
            set(schema["$defs"]["compositeOutput"]["properties"]["id"]["enum"]),
        )

    def test_three_preparations_are_merged_into_existing_steps(self) -> None:
        ids = {step["id"] for step in self.workflow["steps"]}
        self.assertTrue(
            {
                "prepare-discovery-packets",
                "prepare-focused-packets",
                "prepare-review-packets",
            }.isdisjoint(ids)
        )
        self.assertEqual(
            [
                "status/request-packet.validated.json",
                "inputs/shared-widget-shortlist.json",
                "agent-inputs/visual-structure.json",
                "agent-inputs/text-requirements.json",
                "agent-inputs/project-pattern.json",
            ],
            self.step(self.workflow, "validate-packet")["outputs"],
        )
        self.assertEqual(
            [
                "contexts/normalized-context.json",
                "contexts/roles/state-modeling.json",
                "contexts/roles/data-adaptation.json",
                "contexts/roles/asset-decomposition.json",
                "agent-inputs/state-modeling.json",
                "agent-inputs/data-adaptation.json",
                "agent-inputs/asset-decomposition.json",
            ],
            self.step(self.workflow, "normalize-identities")["outputs"],
        )
        draft_outputs = self.step(self.workflow, "synthesize-draft")["outputs"]
        self.assertEqual("ui-requirement.draft.json", draft_outputs[0])
        self.assertEqual(3, len([p for p in draft_outputs if p.startswith("review-views/")]))
        self.assertEqual(
            {f"contexts/roles/{role}.json" for role in REVIEW_ROLES},
            {p for p in draft_outputs if p.startswith("contexts/roles/")},
        )
        self.assertEqual(
            {f"agent-inputs/{role}.json" for role in REVIEW_ROLES},
            {p for p in draft_outputs if p.startswith("agent-inputs/")},
        )

    def test_each_findings_worker_sees_exactly_one_unique_role_packet(self) -> None:
        packets = []
        for role, step_id in ROLE_TO_STEP.items():
            step = self.step(self.workflow, step_id)
            expected = [f"agent-inputs/{role}.json"]
            self.assertEqual(expected, step["agentInputs"])
            self.assertTrue(set(step["agentInputs"]).issubset(step["inputs"]))
            packets.extend(step["agentInputs"])
        self.assertEqual(9, len(packets))
        self.assertEqual(9, len(set(packets)))

    def test_strict_validation_covers_all_hidden_authority(self) -> None:
        strict = self.step(self.workflow, "strict-validate-requirement")
        required = {
            "ui-requirement.draft.json",
            *(f"agent-inputs/{role}.json" for role in ROLES),
            *(f"contexts/roles/{role}.json" for role in FOCUSED_AND_REVIEW_ROLES),
            *(f"review-views/{role}.review-view.json" for role in REVIEW_ROLES),
        }
        self.assertTrue(required.issubset(strict["inputs"]))
        self.assertEqual([], strict["agentInputs"])
        self.assertIn(
            "--review-draft ui-requirement.draft.json", strict["instruction"]
        )

    def test_protected_build_orders_planning_validation_and_mutation(self) -> None:
        continuation = self.workflow["protectedContinuation"]["steps"]
        self.assertEqual("prepare-accepted-build-view", continuation[0]["id"])
        prepare = continuation[0]
        self.assertEqual(["requirements-confirmation"], prepare["dependsOn"])
        self.assertEqual(["accepted-build-view.json"], prepare["outputs"])
        self.assertEqual("accepted-build-view", prepare["contractValidator"])
        self.assertIn("ui-requirement.json", prepare["inputs"])
        for token in ("mode projected", "buildAllowed true", "full-fallback"):
            self.assertIn(token, prepare["instruction"])

        plan = self.step(self.workflow, "plan-umg-build")
        self.assertEqual(["prepare-accepted-build-view"], plan["dependsOn"])
        self.assertEqual(
            ["ui-requirement.json", "accepted-build-view.json"], plan["inputs"]
        )
        self.assertEqual(["accepted-build-view.json"], plan["agentInputs"])
        self.assertEqual(["ui-build-bundle.planned.json"], plan["outputs"])
        self.assertIn("View's exact dispatchContract", plan["instruction"])
        self.assertIn("performs no Unreal Editor connection or mutation", plan["instruction"])

        validate_plan = self.step(self.workflow, "validate-build-plan")
        self.assertEqual(["plan-umg-build"], validate_plan["dependsOn"])
        self.assertIn(
            "validate_build_bundle.py ui-build-bundle.planned.json",
            validate_plan["instruction"],
        )
        self.assertEqual(
            ["status/ui-build-plan.pre-mutation-valid.json"],
            validate_plan["outputs"],
        )
        self.assertIn(
            "orchestration/scripts/build_plan_evidence.py",
            validate_plan["instruction"],
        )
        self.assertIn("native prepare_build.py v0.2", validate_plan["instruction"])

        build = self.step(self.workflow, "build-verified-umg")
        self.assertEqual(["validate-build-plan"], build["dependsOn"])
        self.assertEqual([], build["agentInputs"])
        self.assertIn(
            "status/ui-build-plan.pre-mutation-valid.json", build["inputs"]
        )
        self.assertIn("--validate-only", build["instruction"])
        self.assertIn("only after validating", build["instruction"])

    def test_asset_decomposition_dispatch_preserves_design_mode_contract(self) -> None:
        instruction = self.step(
            self.workflow, "analyze-asset-decomposition"
        )["instruction"]
        for token in (
            "designSizeModeDecision",
            "mode",
            "basis",
            "reason",
            "evidenceIds",
            "claimId",
            "asset-decomposition claim",
            "project-umg-rule",
            "umg_* always",
            "Only uw_*",
            "fallback-unclear",
            "FillScreen",
            "Desired",
            "assetKind",
        ):
            self.assertIn(token, instruction)

    def test_dispatch_policy_drift_fails(self) -> None:
        invalid = copy.deepcopy(self.workflow)
        invalid["agentDispatchPolicy"]["historyPolicy"] = "all"
        self.assert_contract_error(invalid, "packet-path-only/no-history/fork-none")

    def test_build_dispatch_policy_drift_fails(self) -> None:
        invalid = copy.deepcopy(self.workflow)
        invalid["protectedContinuation"]["agentDispatchPolicy"][
            "historyPolicy"
        ] = "inherited"
        self.assert_contract_error(
            invalid, "single-validated-artifact-path/no-history/fork-none"
        )

    def test_pre_mutation_evidence_contract_drift_fails(self) -> None:
        invalid = copy.deepcopy(self.workflow)
        invalid["protectedContinuation"]["preMutationEvidenceContract"][
            "requiredBeforeEditorMutation"
        ] = False
        self.assert_contract_error(invalid, "closed Schema, builder/validator")

    def test_composite_layout_output_contract_drift_fails(self) -> None:
        invalid = copy.deepcopy(self.workflow)
        invalid["artifactExchange"]["compositeOutputs"][0][
            "requireRunRootContainment"
        ] = False
        self.assert_contract_error(invalid, "exact planned-layout descriptor")

    def test_worker_agent_input_drift_fails(self) -> None:
        invalid = copy.deepcopy(self.workflow)
        worker = self.step(invalid, "review-coverage")
        worker["agentInputs"] = ["ui-requirement.draft.json"]
        self.assert_contract_error(invalid, "agentInputs must be exactly")

    def test_step_execution_owner_drift_fails(self) -> None:
        invalid = copy.deepcopy(self.workflow)
        self.step(invalid, "validate-focused")["execution"] = "worker"
        self.assert_contract_error(
            invalid, "step 'validate-focused' must use execution 'coordinator'"
        )

        planner = copy.deepcopy(self.workflow)
        self.step(planner, "plan-umg-build")["execution"] = "coordinator"
        self.assert_contract_error(
            planner, "step 'plan-umg-build' must use execution 'worker'"
        )

    def test_agent_input_must_also_be_scheduler_input(self) -> None:
        invalid = copy.deepcopy(self.workflow)
        worker = self.step(invalid, "discover-visual-structure")
        worker["inputs"].remove("agent-inputs/visual-structure.json")
        self.assert_contract_error(invalid, "must also be declared in inputs")

    def test_every_step_input_and_output_list_is_exact(self) -> None:
        missing_barrier_input = copy.deepcopy(self.workflow)
        self.step(missing_barrier_input, "validate-discovery")["inputs"].remove(
            "findings/text-requirements.json"
        )
        self.assert_contract_error(
            missing_barrier_input,
            "step 'validate-discovery' inputs must be exactly",
        )

        extra_output = copy.deepcopy(self.workflow)
        self.step(extra_output, "validate-focused")["outputs"].append(
            "status/uncontracted.json"
        )
        self.assert_contract_error(
            extra_output,
            "step 'validate-focused' outputs must be exactly",
        )

        redirected_output = copy.deepcopy(self.workflow)
        self.step(redirected_output, "post-save-unreal-readback")["outputs"] = [
            "status/fake-readback.json"
        ]
        self.assert_contract_error(
            redirected_output,
            "step 'post-save-unreal-readback' outputs must be exactly",
        )

    def test_artifact_output_allowlists_are_exact_and_ordered(self) -> None:
        extra_prefix = copy.deepcopy(self.workflow)
        extra_prefix["artifactExchange"]["allowedOutputPrefixes"].append("scratch")
        self.assert_contract_error(extra_prefix, "exact closed v2 list")

        reordered_roots = copy.deepcopy(self.workflow)
        roots = reordered_roots["artifactExchange"]["allowedRootOutputs"]
        roots[0], roots[1] = roots[1], roots[0]
        self.assert_contract_error(reordered_roots, "exact closed v2 list")

    def test_missing_strict_review_view_fails(self) -> None:
        invalid = copy.deepcopy(self.workflow)
        strict = self.step(invalid, "strict-validate-requirement")
        strict["inputs"].remove(
            "review-views/schema-feasibility-review.review-view.json"
        )
        self.assert_contract_error(invalid, "must cover the immutable Draft")

    @unittest.skipIf(Draft202012Validator is None, "jsonschema is not installed")
    def test_workflow_conforms_to_its_draft_2020_12_schema(self) -> None:
        schema = load_json(SCHEMA_PATH)
        Draft202012Validator.check_schema(schema)
        errors = sorted(
            Draft202012Validator(schema).iter_errors(self.workflow),
            key=lambda item: tuple(str(part) for part in item.absolute_path),
        )
        self.assertEqual([], [error.message for error in errors])

    def test_build_planner_cannot_receive_full_requirement(self) -> None:
        invalid = copy.deepcopy(self.workflow)
        plan = self.step(invalid, "plan-umg-build")
        plan["agentInputs"].append("ui-requirement.json")
        self.assert_contract_error(invalid, "agentInputs must be exactly")

    def test_missing_findings_role_fails(self) -> None:
        invalid = copy.deepcopy(self.workflow)
        invalid["requiredFindingsRoles"].remove("coverage-review")
        self.assert_contract_error(
            invalid, "must contain each of the nine canonical roles exactly once"
        )

    def test_cycle_fails(self) -> None:
        invalid = copy.deepcopy(self.workflow)
        self.step(invalid, "validate-packet")["dependsOn"] = [
            "requirements-confirmation"
        ]
        self.assert_contract_error(invalid, "dependency cycle detected")

    def test_reordering_closed_steps_fails(self) -> None:
        invalid = copy.deepcopy(self.workflow)
        invalid["steps"][1], invalid["steps"][2] = (
            invalid["steps"][2],
            invalid["steps"][1],
        )
        self.assert_contract_error(invalid, "analysis step order is closed")

    def test_missing_or_extra_unique_step_fails(self) -> None:
        missing = copy.deepcopy(self.workflow)
        missing["steps"] = [
            item for item in missing["steps"] if item["id"] != "validate-focused"
        ]
        self.assert_contract_error(missing, "analysis step set is closed")

        extra = copy.deepcopy(self.workflow)
        extra["steps"].append(
            {
                "id": "uncontracted-validation",
                "kind": "validation",
                "execution": "coordinator",
                "dependsOn": ["strict-validate-requirement"],
                "inputs": ["status/ui-requirement.strict-valid.json"],
                "agentInputs": [],
                "outputs": ["status/uncontracted.valid.json"],
                "instruction": "Uncontracted extension.",
            }
        )
        self.assert_contract_error(extra, "analysis step set is closed")

    def test_skipping_first_user_gate_fails(self) -> None:
        invalid = copy.deepcopy(self.workflow)
        prepare = self.step(invalid, "prepare-accepted-build-view")
        prepare["dependsOn"] = ["strict-validate-requirement"]
        self.assert_contract_error(
            invalid,
            "prepare-accepted-build-view' must depend exactly on ['requirements-confirmation']",
        )

    def test_skipping_accepted_view_gate_fails(self) -> None:
        invalid = copy.deepcopy(self.workflow)
        plan = self.step(invalid, "plan-umg-build")
        plan["dependsOn"] = ["requirements-confirmation"]
        self.assert_contract_error(
            invalid,
            "plan-umg-build' must depend exactly on ['prepare-accepted-build-view']",
        )

    def test_editor_build_cannot_skip_pre_mutation_validation(self) -> None:
        invalid = copy.deepcopy(self.workflow)
        build = self.step(invalid, "build-verified-umg")
        build["dependsOn"] = ["plan-umg-build"]
        self.assert_contract_error(
            invalid,
            "build-verified-umg' must depend exactly on ['validate-build-plan']",
        )

    def test_skipping_second_user_gate_fails(self) -> None:
        invalid = copy.deepcopy(self.workflow)
        document = self.step(invalid, "document-program-handoff")
        document["dependsOn"] = ["present-build-results"]
        self.assert_contract_error(
            invalid,
            "document-program-handoff' must depend exactly on ['build-results-confirmation']",
        )

    def test_output_escape_and_duplicate_owner_fail(self) -> None:
        escaped = copy.deepcopy(self.workflow)
        self.step(escaped, "discover-visual-structure")["outputs"] = [
            "../visual-structure.json"
        ]
        self.assert_contract_error(escaped, "artifact-root-relative POSIX path")

        duplicate = copy.deepcopy(self.workflow)
        self.step(duplicate, "discover-text-requirements")["outputs"] = [
            "findings/visual-structure.json"
        ]
        self.assert_contract_error(duplicate, "has multiple owners")

    def test_parallel_group_membership_fails(self) -> None:
        invalid = copy.deepcopy(self.workflow)
        invalid["parallelGroups"][0]["members"].remove("discover-project-pattern")
        self.assert_contract_error(invalid, "three canonical role waves")

    def test_plan_materializes_separate_agent_inputs_without_vendor_api(self) -> None:
        with writable_test_directory() as temporary:
            artifact_root = temporary / "run"
            artifact_root.mkdir()
            request_packet = artifact_root / "incoming" / "packet.json"
            request_packet.parent.mkdir()
            request_packet.write_text('{"requestId":"test"}\n', encoding="utf-8")

            manifest = render_dispatch_manifest(
                self.workflow, WORKFLOW_PATH, artifact_root, request_packet
            )

            self.assertEqual("2.0.0", manifest["manifestVersion"])
            self.assertEqual(POLICY, manifest["agentDispatchPolicy"])
            self.assertEqual(
                BUILD_POLICY,
                manifest["protectedContinuation"]["agentDispatchPolicy"],
            )
            self.assertEqual(
                EVIDENCE_CONTRACT,
                manifest["protectedContinuation"]["preMutationEvidenceContract"],
            )
            self.assertEqual(
                str(request_packet.resolve()), manifest["requestPacket"]["path"]
            )
            self.assertEqual(
                hashlib.sha256(request_packet.read_bytes()).hexdigest(),
                manifest["requestPacket"]["sha256"],
            )
            self.assertFalse(manifest["adapterContract"]["thisManifestCallsVendorApi"])
            self.assertIn("agentInputs only", manifest["adapterContract"]["agentInputRule"])
            self.assertEqual(
                "planned-layout-specs",
                manifest["authority"]["compositeOutputs"][0]["id"],
            )
            self.assertEqual(
                "deterministic-build-plans",
                manifest["authority"]["compositeOutputs"][1]["id"],
            )
            visual = next(
                item
                for item in manifest["steps"]
                if item["stepId"] == "discover-visual-structure"
            )
            self.assertEqual(1, len(visual["agentInputs"]))
            self.assertEqual(
                "agent-inputs/visual-structure.json",
                visual["agentInputs"][0]["logicalPath"],
            )
            plan = next(
                item for item in manifest["steps"] if item["stepId"] == "plan-umg-build"
            )
            self.assertEqual(
                ["accepted-build-view.json"],
                [item["logicalPath"] for item in plan["agentInputs"]],
            )

    def test_request_packet_outside_artifact_root_fails(self) -> None:
        with writable_test_directory() as temporary_root:
            artifact_root = temporary_root / "run"
            artifact_root.mkdir()
            request_packet = temporary_root / "outside.json"
            request_packet.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "must resolve inside artifact root"):
                render_dispatch_manifest(
                    self.workflow, WORKFLOW_PATH, artifact_root, request_packet
                )

    def test_cli_validate_and_plan_emit_v2(self) -> None:
        validate_result = subprocess.run(
            [
                sys.executable,
                "-B",
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
        self.assertIn("nextgame-ui-requirements@2.0.0", validate_result.stdout)

        with writable_test_directory() as temporary:
            artifact_root = temporary / "run"
            artifact_root.mkdir()
            packet = artifact_root / "request-packet.json"
            packet.write_text(json.dumps({"requestId": "test"}), encoding="utf-8")
            output = artifact_root / "status" / "dispatch-manifest.json"
            plan_result = subprocess.run(
                [
                    sys.executable,
                    "-B",
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
            self.assertEqual(0, plan_result.returncode, plan_result.stderr)
            manifest = load_json(output)
            self.assertEqual("2.0.0", manifest["manifestVersion"])


if __name__ == "__main__":
    unittest.main()
