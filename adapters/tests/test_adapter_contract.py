from __future__ import annotations

import copy
import importlib.util
import unittest
from pathlib import Path


ADAPTERS_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = ADAPTERS_ROOT / "validate_adapters.py"
SPEC = importlib.util.spec_from_file_location("validate_adapters", VALIDATOR_PATH)
assert SPEC is not None and SPEC.loader is not None
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)
CONTRACT_PATH = ADAPTERS_ROOT / "adapter-contract.json"
WORKFLOW_PATH = (
    ADAPTERS_ROOT.parent
    / "orchestration"
    / "nextgame-ui.requirements.workflow.json"
)


class AdapterContractTest(unittest.TestCase):
    def test_all_runtime_mappings_share_the_portable_contract(self) -> None:
        self.assertEqual([], VALIDATOR.validate(ADAPTERS_ROOT))

    def test_orchestration_drift_is_detected_for_every_shared_boundary(self) -> None:
        contract = VALIDATOR.load_json(CONTRACT_PATH)
        baseline = VALIDATOR.load_json(WORKFLOW_PATH)
        self.assertEqual(
            [], VALIDATOR.validate_contract_against_workflow(contract, baseline)
        )

        drifted_workflows = []

        role_drift = copy.deepcopy(baseline)
        role_drift["requiredFindingsRoles"].pop()
        drifted_workflows.append(("role set", role_drift))

        output_drift = copy.deepcopy(baseline)
        next(
            step
            for step in output_drift["steps"]
            if step["id"] == "discover-visual-structure"
        )["outputs"] = ["findings/wrong.json"]
        drifted_workflows.append(("findings output", output_drift))

        context_drift = copy.deepcopy(baseline)
        review_step = next(
            step
            for step in context_drift["steps"]
            if step["id"] == "review-state-visual"
        )
        review_step["inputs"] = [
            "contexts/stale-context.json"
            if item == "contexts/normalized-context.json"
            else item
            for item in review_step["inputs"]
        ]
        drifted_workflows.append(("context path", context_drift))

        group_drift = copy.deepcopy(baseline)
        next(
            step
            for step in group_drift["steps"]
            if step["id"] == "analyze-state-modeling"
        )["parallelGroup"] = "wrong-group"
        drifted_workflows.append(("parallel group", group_drift))

        requirement_drift = copy.deepcopy(baseline)
        next(
            step
            for step in requirement_drift["steps"]
            if step["id"] == "synthesize-draft"
        )["outputs"] = ["ui-requirement.json"]
        drifted_workflows.append(("requirement output", requirement_drift))

        first_gate_drift = copy.deepcopy(baseline)
        next(
            step
            for step in first_gate_drift["steps"]
            if step["id"] == "requirements-confirmation"
        )["gate"]["decisionArtifact"] = "wrong-requirement.json"
        drifted_workflows.append(("requirement gate", first_gate_drift))

        second_gate_drift = copy.deepcopy(baseline)
        next(
            step
            for step in second_gate_drift["protectedContinuation"]["steps"]
            if step["id"] == "build-results-confirmation"
        )["gate"]["decisionArtifact"] = "wrong-acceptance.json"
        drifted_workflows.append(("build gate", second_gate_drift))

        for label, workflow in drifted_workflows:
            with self.subTest(boundary=label):
                self.assertTrue(
                    VALIDATOR.validate_contract_against_workflow(contract, workflow),
                    f"{label} drift was not detected",
                )


if __name__ == "__main__":
    unittest.main()
