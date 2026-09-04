from __future__ import annotations

import copy
import importlib.util
import re
import unittest
import uuid
from pathlib import Path


ADAPTERS_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = ADAPTERS_ROOT / "validate_adapters.py"
SPEC = importlib.util.spec_from_file_location("validate_adapters", VALIDATOR_PATH)
assert SPEC is not None and SPEC.loader is not None
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)
CONTRACT_PATH = ADAPTERS_ROOT / "adapter-contract.json"
CODEX_MAP_PATH = ADAPTERS_ROOT / "codex" / "runtime-map.json"
WORKBUDDY_ANALYSIS_PATH = (
    ADAPTERS_ROOT / "workbuddy" / "nextgame-ui-requirement-analysis.md"
)
WORKBUDDY_BUILD_PATH = (
    ADAPTERS_ROOT / "workbuddy" / "nextgame-ui-build-acceptance.md"
)
WORKFLOW_PATH = (
    ADAPTERS_ROOT.parent
    / "orchestration"
    / "nextgame-ui.requirements.workflow.json"
)


def step(workflow: dict, step_id: str) -> dict:
    for item in workflow["steps"]:
        if item["id"] == step_id:
            return item
    for item in workflow["protectedContinuation"]["steps"]:
        if item["id"] == step_id:
            return item
    raise KeyError(step_id)


class AdapterContractTest(unittest.TestCase):
    def test_all_runtime_mappings_share_the_portable_contract(self) -> None:
        self.assertEqual([], VALIDATOR.validate(ADAPTERS_ROOT))

    def test_json_and_workbuddy_front_matter_reject_duplicate_keys(self) -> None:
        json_path = ADAPTERS_ROOT / "tests" / f"_duplicate_{uuid.uuid4().hex}.json"
        workbuddy_path = (
            ADAPTERS_ROOT / "tests" / f"_duplicate_{uuid.uuid4().hex}.md"
        )
        try:
            json_path.write_text(
                '{"outer":{"historyPolicy":"none","historyPolicy":"all"}}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ValueError, r"duplicate JSON object key 'historyPolicy'"
            ):
                VALIDATOR.load_json(json_path)

            workbuddy_path.write_text(
                '---\n{"policy":{"reuse_agent":false,"reuse_agent":true}}\n---\nbody\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ValueError, r"front matter duplicate JSON object key 'reuse_agent'"
            ):
                VALIDATOR.load_json_front_matter(workbuddy_path)
        finally:
            json_path.unlink(missing_ok=True)
            workbuddy_path.unlink(missing_ok=True)

    def test_contract_declares_exact_packet_context_and_view_paths(self) -> None:
        contract = VALIDATOR.load_json(CONTRACT_PATH)
        roles = {item["role"]: item for item in contract["roles"]}
        self.assertEqual(VALIDATOR.EXPECTED_ROLES, set(roles))
        for role, record in roles.items():
            self.assertEqual(f"agent-inputs/{role}.json", record["packetPath"])
            if role in VALIDATOR.DISCOVERY_ROLES:
                self.assertIsNone(record["contextPath"])
                self.assertNotIn("reviewViewPath", record)
            else:
                self.assertEqual(f"contexts/roles/{role}.json", record["contextPath"])
            if role in VALIDATOR.REVIEW_ROLES:
                self.assertEqual(
                    f"review-views/{role}.review-view.json",
                    record["reviewViewPath"],
                )
        authority = contract["artifactAuthority"]
        self.assertEqual(
            {
                "no-history role packets",
                "role context projections",
                "review views",
                "accepted build view",
            },
            set(authority["validatedDispatchArtifacts"]),
        )
        self.assertNotIn("accepted build view", authority["authoritative"])
        self.assertIn("accepted build view", authority["nonAuthoritative"])
        self.assertEqual(
            VALIDATOR.EXPECTED_BUILD_DISPATCH_POLICY,
            contract["protectedBuildProjection"]["agentDispatchPolicy"],
        )

    def test_protected_build_policy_contract_drift_is_fail_closed(self) -> None:
        contract = VALIDATOR.load_json(CONTRACT_PATH)
        workflow = VALIDATOR.load_json(WORKFLOW_PATH)
        drifted = copy.deepcopy(contract)
        del drifted["protectedBuildProjection"]["agentDispatchPolicy"]
        self.assertTrue(
            VALIDATOR.validate_contract_against_workflow(drifted, workflow)
        )

        evidence = copy.deepcopy(contract)
        evidence["protectedBuildProjection"]["preMutationEvidenceContract"][
            "requiredBeforeEditorMutation"
        ] = False
        self.assertTrue(
            VALIDATOR.validate_contract_against_workflow(evidence, workflow)
        )

    def test_contract_stage_and_authority_sets_are_closed(self) -> None:
        contract = VALIDATOR.load_json(CONTRACT_PATH)
        workflow = VALIDATOR.load_json(WORKFLOW_PATH)
        self.assertEqual(VALIDATOR.EXPECTED_CONTRACT_STAGES, contract["stages"])
        self.assertEqual(
            VALIDATOR.EXPECTED_ARTIFACT_AUTHORITY,
            contract["artifactAuthority"],
        )

        drifted_contracts: list[tuple[str, dict]] = []
        extra = copy.deepcopy(contract)
        extra["stages"].append(
            {"id": "uncontracted", "owner": "coordinator", "dependsOn": []}
        )
        drifted_contracts.append(("extra stage", extra))

        reordered = copy.deepcopy(contract)
        reordered["stages"][1], reordered["stages"][2] = (
            reordered["stages"][2],
            reordered["stages"][1],
        )
        drifted_contracts.append(("reordered stage", reordered))

        owner = copy.deepcopy(contract)
        next(item for item in owner["stages"] if item["id"] == "validate-focused")[
            "owner"
        ] = "delegated-roles"
        drifted_contracts.append(("barrier owner", owner))

        receipt = copy.deepcopy(contract)
        receipt["artifactAuthority"]["nonAuthoritative"].remove(
            "runtime task status"
        )
        drifted_contracts.append(("missing non-authoritative receipt", receipt))

        moved = copy.deepcopy(contract)
        moved["artifactAuthority"]["authoritative"].append("chat summaries")
        drifted_contracts.append(("message promoted to authority", moved))

        for label, drifted in drifted_contracts:
            with self.subTest(boundary=label):
                self.assertTrue(
                    VALIDATOR.validate_contract_against_workflow(
                        drifted, workflow
                    )
                )

    def test_gate_actor_and_recorder_drift_is_fail_closed(self) -> None:
        contract = VALIDATOR.load_json(CONTRACT_PATH)
        workflow = VALIDATOR.load_json(WORKFLOW_PATH)
        for field, value in (
            ("decisionActor", "coordinator"),
            ("artifactRecorder", "user"),
        ):
            drifted = copy.deepcopy(contract)
            drifted["humanGates"][0][field] = value
            with self.subTest(field=field):
                self.assertTrue(
                    VALIDATOR.validate_contract_against_workflow(drifted, workflow)
                )

    def test_human_gate_raw_list_and_keys_are_unique(self) -> None:
        contract = VALIDATOR.load_json(CONTRACT_PATH)
        workflow = VALIDATOR.load_json(WORKFLOW_PATH)

        duplicate_record = copy.deepcopy(contract)
        duplicate_record["humanGates"].append(
            copy.deepcopy(duplicate_record["humanGates"][0])
        )

        duplicate_id = copy.deepcopy(contract)
        duplicate_id["humanGates"][1]["id"] = duplicate_id["humanGates"][0]["id"]

        duplicate_workflow_step = copy.deepcopy(contract)
        duplicate_workflow_step["humanGates"][1]["workflowStep"] = (
            duplicate_workflow_step["humanGates"][0]["workflowStep"]
        )

        for label, drifted in (
            ("duplicate raw record", duplicate_record),
            ("duplicate id", duplicate_id),
            ("duplicate workflowStep", duplicate_workflow_step),
        ):
            with self.subTest(boundary=label):
                self.assertTrue(
                    VALIDATOR.validate_contract_against_workflow(drifted, workflow),
                    f"{label} was not rejected",
                )

    def test_codex_retry_and_ownership_drift_are_fail_closed(self) -> None:
        contract = VALIDATOR.load_json(CONTRACT_PATH)
        workflow = VALIDATOR.load_json(WORKFLOW_PATH)
        baseline = VALIDATOR.load_json(CODEX_MAP_PATH)
        self.assertEqual(
            [], VALIDATOR.validate_codex_runtime_map(baseline, workflow, contract)
        )

        drifted_maps: list[tuple[str, dict]] = []
        correction = copy.deepcopy(baseline)
        correction["correctActive"] = "send_message"
        drifted_maps.append(("history-reusing correction", correction))

        retry = copy.deepcopy(baseline)
        retry["retryPolicy"]["reuseAgent"] = True
        drifted_maps.append(("history-reusing retry", retry))

        build_retry = copy.deepcopy(baseline)
        build_retry["buildPlanningRetryPolicy"]["reuseAgent"] = True
        drifted_maps.append(("history-reusing build retry", build_retry))

        build_dispatch_contract = copy.deepcopy(baseline)
        build_dispatch_contract["buildPlanningDispatchContract"] = (
            "unbound-prompt"
        )
        drifted_maps.append(("unbound build task contract", build_dispatch_contract))

        coordinator = copy.deepcopy(baseline)
        coordinator["coordinatorOwnedSteps"].remove("validate-focused")
        drifted_maps.append(("missing coordinator barrier", coordinator))

        gate = copy.deepcopy(baseline)
        gate["humanOwnedGateSteps"].remove("requirements-confirmation")
        drifted_maps.append(("misowned human gate", gate))

        receipt = copy.deepcopy(baseline)
        receipt["receiptIsAuthoritative"] = True
        drifted_maps.append(("authoritative task receipt", receipt))

        build_policy = copy.deepcopy(baseline)
        build_policy["buildPlanningAgentDispatchPolicy"]["historyPolicy"] = "all"
        drifted_maps.append(("history-reusing build planner", build_policy))

        pre_mutation = copy.deepcopy(baseline)
        pre_mutation["preMutationValidationStep"] = "build-verified-umg"
        drifted_maps.append(("pre-mutation validator bypass", pre_mutation))

        evidence_contract = copy.deepcopy(baseline)
        evidence_contract["preMutationEvidenceContract"]["revalidationMode"] = (
            "trust-existing"
        )
        drifted_maps.append(("pre-mutation evidence bypass", evidence_contract))

        for label, runtime_map in drifted_maps:
            with self.subTest(boundary=label):
                self.assertTrue(
                    VALIDATOR.validate_codex_runtime_map(
                        runtime_map, workflow, contract
                    )
                )

    def test_workbuddy_analysis_proof_drift_is_fail_closed(self) -> None:
        workflow = VALIDATOR.load_json(WORKFLOW_PATH)
        baseline, _ = VALIDATOR.load_json_front_matter(WORKBUDDY_ANALYSIS_PATH)
        self.assertEqual(
            [], VALIDATOR.validate_workbuddy_analysis_proofs(baseline, workflow)
        )

        projection = copy.deepcopy(baseline)
        next(item for item in projection["steps"] if item["id"] == "normalize-context")[
            "result_schema"
        ]["properties"]["artifact_paths"]["const"].pop()
        self.assertTrue(
            VALIDATOR.validate_workbuddy_analysis_proofs(projection, workflow)
        )

        strict = copy.deepcopy(baseline)
        next(
            item
            for item in strict["steps"]
            if item["id"] == "strict-requirement-validation"
        )["result_schema"]["properties"]["linked_packet_count"]["const"] = 8
        self.assertTrue(VALIDATOR.validate_workbuddy_analysis_proofs(strict, workflow))

        optional_status = copy.deepcopy(baseline)
        next(
            item
            for item in optional_status["steps"]
            if item["id"] == "strict-requirement-validation"
        )["result_schema"]["required"].remove("artifact_path")
        self.assertTrue(
            VALIDATOR.validate_workbuddy_analysis_proofs(optional_status, workflow)
        )

        gate_inputs = copy.deepcopy(baseline)
        next(
            item for item in gate_inputs["steps"] if item["id"] == "requirement-user-gate"
        )["inputs"].remove("status/ui-requirement.strict-valid.json")
        self.assertTrue(
            VALIDATOR.validate_workbuddy_analysis_proofs(gate_inputs, workflow)
        )

        duplicate = copy.deepcopy(baseline)
        duplicate["steps"].append(copy.deepcopy(duplicate["steps"][0]))
        self.assertTrue(
            VALIDATOR.validate_workbuddy_analysis_proofs(duplicate, workflow)
        )

        for label, role in (
            ("focused retry", "state-modeling"),
            ("review retry", "coverage-review"),
        ):
            reusable = copy.deepcopy(baseline)
            reusable["role_retry_policy"]["reuse_agent"] = True
            with self.subTest(boundary=label, role=role):
                self.assertTrue(
                    VALIDATOR.validate_workbuddy_analysis_proofs(
                        reusable, workflow
                    )
                )

    def test_workbuddy_build_view_bypasses_are_fail_closed(self) -> None:
        contract = VALIDATOR.load_json(CONTRACT_PATH)
        workflow = VALIDATOR.load_json(WORKFLOW_PATH)
        baseline, _ = VALIDATOR.load_json_front_matter(WORKBUDDY_BUILD_PATH)
        self.assertEqual(
            [],
            VALIDATOR.validate_workbuddy_build_mapping(
                baseline, workflow, contract
            ),
        )

        drifted_builds: list[tuple[str, dict]] = []
        delegated_authority = copy.deepcopy(baseline)
        prepare = next(
            item
            for item in delegated_authority["steps"]
            if item["id"] == "prepare-accepted-build-view"
        )
        prepare["execution"] = "subagent"
        prepare["agent_role"] = "unsafe-preparer"
        drifted_builds.append(("full authority delegated", delegated_authority))

        view_bypass = copy.deepcopy(baseline)
        next(
            item
            for item in view_bypass["steps"]
            if item["id"] == "plan-umg-build"
        )["depends_on"] = []
        drifted_builds.append(("View dependency bypass", view_bypass))

        raw_requirement = copy.deepcopy(baseline)
        next(
            item
            for item in raw_requirement["steps"]
            if item["id"] == "plan-umg-build"
        )["inputs"] = ["ui-requirement.json"]
        drifted_builds.append(("full Requirement Agent input", raw_requirement))

        dispatch_contract = copy.deepcopy(baseline)
        dispatch_contract["build_planning_dispatch_contract"] = "side-channel"
        drifted_builds.append(("unbound View dispatch contract", dispatch_contract))

        retry_policy = copy.deepcopy(baseline)
        retry_policy["build_planning_retry_policy"]["reuse_agent"] = True
        drifted_builds.append(("history-reusing build retry", retry_policy))

        weak_validation = copy.deepcopy(baseline)
        next(
            item
            for item in weak_validation["steps"]
            if item["id"] == "validate-build-plan"
        )["inputs"].remove("ui-requirement.json")
        drifted_builds.append(("full Requirement validator bypass", weak_validation))

        fallback = copy.deepcopy(baseline)
        next(
            item
            for item in fallback["steps"]
            if item["id"] == "prepare-accepted-build-view"
        )["result_schema"]["properties"]["mode"]["const"] = "full-fallback"
        drifted_builds.append(("buildable full fallback", fallback))

        duplicate = copy.deepcopy(baseline)
        duplicate["steps"].append(copy.deepcopy(duplicate["steps"][0]))
        drifted_builds.append(("duplicate build step", duplicate))

        early_mutation = copy.deepcopy(baseline)
        next(
            item for item in early_mutation["steps"] if item["id"] == "plan-umg-build"
        )["result_schema"]["properties"]["editor_mutation_performed"]["const"] = True
        drifted_builds.append(("planner Editor mutation", early_mutation))

        skip_prevalidation = copy.deepcopy(baseline)
        next(
            item
            for item in skip_prevalidation["steps"]
            if item["id"] == "build-verified-umg"
        )["depends_on"] = ["plan-umg-build"]
        drifted_builds.append(("Editor mutation before validation", skip_prevalidation))

        optional_validation = copy.deepcopy(baseline)
        next(
            item
            for item in optional_validation["steps"]
            if item["id"] == "validate-build-plan"
        )["result_schema"]["required"].remove("valid")
        drifted_builds.append(("optionalized validation proof", optional_validation))

        evidence_contract = copy.deepcopy(baseline)
        evidence_contract["pre_mutation_evidence_contract"][
            "requiredBeforeEditorMutation"
        ] = False
        drifted_builds.append(("pre-mutation evidence contract bypass", evidence_contract))

        missing_presentation_status = copy.deepcopy(baseline)
        next(
            item
            for item in missing_presentation_status["steps"]
            if item["id"] == "build-user-gate"
        )["inputs"].remove("status/build-results.presented.json")
        drifted_builds.append(("unbound presented build gate", missing_presentation_status))

        for label, build_workflow in drifted_builds:
            with self.subTest(boundary=label):
                self.assertTrue(
                    VALIDATOR.validate_workbuddy_build_mapping(
                        build_workflow, workflow, contract
                    )
                )

    def test_adapter_prose_cannot_reopen_history_or_prompt_channels(self) -> None:
        documents = {
            Path("codex/README.md"): (
                ADAPTERS_ROOT / "codex" / "README.md"
            ).read_text(encoding="utf-8"),
            Path("hermes/nextgame-ui-portable/SKILL.md"): (
                ADAPTERS_ROOT / "hermes" / "nextgame-ui-portable" / "SKILL.md"
            ).read_text(encoding="utf-8"),
            Path("hermes/nextgame-ui-portable/references/artifact-contract.md"): (
                ADAPTERS_ROOT
                / "hermes"
                / "nextgame-ui-portable"
                / "references"
                / "artifact-contract.md"
            ).read_text(encoding="utf-8"),
            Path("README.md"): (ADAPTERS_ROOT / "README.md").read_text(
                encoding="utf-8"
            ),
            Path("workbuddy/nextgame-ui-requirement-analysis.md"): (
                WORKBUDDY_ANALYSIS_PATH.read_text(encoding="utf-8")
            ),
            Path("workbuddy/nextgame-ui-build-acceptance.md"): (
                WORKBUDDY_BUILD_PATH.read_text(encoding="utf-8")
            ),
        }
        for relative, text in documents.items():
            with self.subTest(document=str(relative)):
                self.assertEqual(
                    [], VALIDATOR.validate_prompt_contract_text(relative, text)
                )

        self.assertTrue(
            VALIDATOR.validate_prompt_contract_text(
                Path("codex/README.md"),
                documents[Path("codex/README.md")] + "\nuse followup_task\n",
            )
        )
        self.assertTrue(
            VALIDATOR.validate_prompt_contract_text(
                Path("hermes/nextgame-ui-portable/references/artifact-contract.md"),
                documents[
                    Path("hermes/nextgame-ui-portable/references/artifact-contract.md")
                ]
                + "\nretry its owner\n",
            )
        )
        self.assertTrue(
            VALIDATOR.validate_prompt_contract_text(
                Path("hermes/nextgame-ui-portable/SKILL.md"),
                documents[Path("hermes/nextgame-ui-portable/SKILL.md")]
                + "\nroot agent may execute each role\n",
            )
        )
        for contradiction in (
            "resume existing task with clarified instructions",
            "reuse the same delegation",
            "continue the failed agent",
            "append extra prompt",
            "correct the active task",
        ):
            with self.subTest(contradiction=contradiction):
                self.assertTrue(
                    VALIDATOR.validate_prompt_contract_text(
                        Path("hermes/nextgame-ui-portable/SKILL.md"),
                        documents[Path("hermes/nextgame-ui-portable/SKILL.md")]
                        + f"\n{contradiction}\n",
                    )
                )

        for relative in (
            Path("workbuddy/nextgame-ui-requirement-analysis.md"),
            Path("workbuddy/nextgame-ui-build-acceptance.md"),
        ):
            with self.subTest(workbuddy_document=str(relative)):
                self.assertTrue(
                    VALIDATOR.validate_prompt_contract_text(
                        relative,
                        documents[relative]
                        + "\nresume existing task with clarified instructions\n",
                    )
                )

        authority_contradictions = (
            (
                Path("workbuddy/nextgame-ui-build-acceptance.md"),
                "The Accepted Build View replaces the complete Requirement.",
            ),
            (
                Path("workbuddy/nextgame-ui-build-acceptance.md"),
                "The final Bundle validator reads only the Accepted Build View.",
            ),
            (
                Path("hermes/nextgame-ui-portable/SKILL.md"),
                "Skip the final Bundle validators and trust the View.",
            ),
            (
                Path(
                    "hermes/nextgame-ui-portable/references/artifact-contract.md"
                ),
                "Use the Review View instead of the full Requirement.",
            ),
        )
        for relative, contradiction in authority_contradictions:
            with self.subTest(document=str(relative), contradiction=contradiction):
                self.assertTrue(
                    VALIDATOR.validate_prompt_contract_text(
                        relative,
                        documents[relative] + f"\n{contradiction}\n",
                    )
                )

        for relative in (
            Path("workbuddy/nextgame-ui-build-acceptance.md"),
            Path("hermes/nextgame-ui-portable/SKILL.md"),
        ):
            with self.subTest(document=str(relative), safe_prohibition=True):
                self.assertEqual(
                    [],
                    VALIDATOR.validate_prompt_contract_text(
                        relative,
                        documents[relative]
                        + "\nThe Accepted Build View must never replace the complete "
                        "Requirement. Do not skip the final Bundle validators.\n",
                    ),
                )

    def test_hermes_protected_build_mapping_tokens_are_machine_locked(self) -> None:
        documents = {
            Path("hermes/nextgame-ui-portable/SKILL.md"): (
                ADAPTERS_ROOT / "hermes" / "nextgame-ui-portable" / "SKILL.md"
            ).read_text(encoding="utf-8"),
            Path("hermes/nextgame-ui-portable/references/artifact-contract.md"): (
                ADAPTERS_ROOT
                / "hermes"
                / "nextgame-ui-portable"
                / "references"
                / "artifact-contract.md"
            ).read_text(encoding="utf-8"),
        }
        protected_tokens = {
            Path("hermes/nextgame-ui-portable/SKILL.md"): (
                "`dispatchContract`",
                "native `prepare_build.py` v0.2 plans",
                "`orchestration/scripts/build_plan_evidence.py`",
                "`--validate-only`",
                "final Bundle validators still read the complete accepted Requirement as authority",
            ),
            Path("hermes/nextgame-ui-portable/references/artifact-contract.md"): (
                "`dispatchContract`",
                "native `prepare_build.py` v0.2 plans",
                "`orchestration/scripts/build_plan_evidence.py`",
                "`--validate-only` mode",
                "Requirement remains final Bundle validator authority",
            ),
        }
        for relative, text in documents.items():
            self.assertEqual([], VALIDATOR.validate_prompt_contract_text(relative, text))
            for token in protected_tokens[relative]:
                with self.subTest(document=str(relative), removed=token):
                    pattern = r"\s+".join(re.escape(part) for part in token.split())
                    mutated, replacement_count = re.subn(pattern, "", text, count=1)
                    self.assertEqual(1, replacement_count)
                    self.assertTrue(
                        VALIDATOR.validate_prompt_contract_text(
                            relative,
                            mutated,
                        )
                    )

    def test_orchestration_drift_is_detected_for_every_shared_boundary(self) -> None:
        contract = VALIDATOR.load_json(CONTRACT_PATH)
        baseline = VALIDATOR.load_json(WORKFLOW_PATH)
        self.assertEqual(
            [], VALIDATOR.validate_contract_against_workflow(contract, baseline)
        )

        drifted_workflows: list[tuple[str, dict]] = []

        role_drift = copy.deepcopy(baseline)
        role_drift["requiredFindingsRoles"].pop()
        drifted_workflows.append(("role set", role_drift))

        output_drift = copy.deepcopy(baseline)
        step(output_drift, "discover-visual-structure")["outputs"] = [
            "findings/wrong.json"
        ]
        drifted_workflows.append(("findings output", output_drift))

        agent_input_drift = copy.deepcopy(baseline)
        step(agent_input_drift, "analyze-state-modeling")["agentInputs"] = [
            "request-packet.json",
            "contexts/normalized-context.json",
        ]
        drifted_workflows.append(("raw authority Agent input", agent_input_drift))

        packet_drift = copy.deepcopy(baseline)
        step(packet_drift, "discover-text-requirements")["agentInputs"] = [
            "agent-inputs/visual-structure.json"
        ]
        drifted_workflows.append(("wrong role packet", packet_drift))

        policy_drift = copy.deepcopy(baseline)
        policy_drift["agentDispatchPolicy"]["historyPolicy"] = "inherited"
        drifted_workflows.append(("history inheritance", policy_drift))

        build_policy_drift = copy.deepcopy(baseline)
        build_policy_drift["protectedContinuation"]["agentDispatchPolicy"][
            "historyPolicy"
        ] = "inherited"
        drifted_workflows.append(("build planner history inheritance", build_policy_drift))

        composite_drift = copy.deepcopy(baseline)
        composite_drift["artifactExchange"]["compositeOutputs"][1][
            "requireDigestMatch"
        ] = False
        drifted_workflows.append(("composite plan digest bypass", composite_drift))

        context_drift = copy.deepcopy(baseline)
        focused = step(context_drift, "analyze-data-adaptation")
        focused["inputs"] = [
            "contexts/roles/stale.json"
            if item == "contexts/roles/data-adaptation.json"
            else item
            for item in focused["inputs"]
        ]
        drifted_workflows.append(("role context path", context_drift))

        view_drift = copy.deepcopy(baseline)
        reviewer = step(view_drift, "review-coverage")
        reviewer["inputs"] = [
            "review-views/stale.review-view.json"
            if item == "review-views/coverage-review.review-view.json"
            else item
            for item in reviewer["inputs"]
        ]
        drifted_workflows.append(("Review View path", view_drift))

        missing_review_output = copy.deepcopy(baseline)
        step(missing_review_output, "synthesize-draft")["outputs"].remove(
            "review-views/schema-feasibility-review.review-view.json"
        )
        drifted_workflows.append(("Review View producer", missing_review_output))

        strict_drift = copy.deepcopy(baseline)
        step(strict_drift, "strict-validate-requirement")["inputs"].remove(
            "ui-requirement.draft.json"
        )
        drifted_workflows.append(("strict Draft sidecar", strict_drift))

        strict_command_drift = copy.deepcopy(baseline)
        strict_command = step(strict_command_drift, "strict-validate-requirement")
        strict_command["instruction"] = strict_command["instruction"].replace(
            "--review-draft ui-requirement.draft.json", ""
        )
        drifted_workflows.append(("strict Draft-aware command", strict_command_drift))

        group_drift = copy.deepcopy(baseline)
        step(group_drift, "analyze-state-modeling")["parallelGroup"] = "wrong-group"
        drifted_workflows.append(("parallel group", group_drift))

        requirement_drift = copy.deepcopy(baseline)
        step(requirement_drift, "finalize-review-resolutions")["outputs"] = [
            "ui-requirement.json"
        ]
        drifted_workflows.append(("requirement output", requirement_drift))

        accepted_view_drift = copy.deepcopy(baseline)
        step(accepted_view_drift, "prepare-accepted-build-view")["outputs"] = [
            "wrong-view.json"
        ]
        drifted_workflows.append(("Accepted Build View output", accepted_view_drift))

        build_input_drift = copy.deepcopy(baseline)
        step(build_input_drift, "plan-umg-build")["agentInputs"] = [
            "ui-requirement.json"
        ]
        drifted_workflows.append(("build full Requirement exposure", build_input_drift))

        build_dependency_drift = copy.deepcopy(baseline)
        step(build_dependency_drift, "plan-umg-build")["dependsOn"] = [
            "requirements-confirmation"
        ]
        drifted_workflows.append(("Accepted Build View bypass", build_dependency_drift))

        prevalidation_drift = copy.deepcopy(baseline)
        step(prevalidation_drift, "build-verified-umg")["dependsOn"] = [
            "plan-umg-build"
        ]
        drifted_workflows.append(("pre-mutation validation bypass", prevalidation_drift))

        first_gate_drift = copy.deepcopy(baseline)
        step(first_gate_drift, "requirements-confirmation")["gate"][
            "decisionArtifact"
        ] = "wrong-requirement.json"
        drifted_workflows.append(("requirement gate", first_gate_drift))

        second_gate_drift = copy.deepcopy(baseline)
        step(second_gate_drift, "build-results-confirmation")["gate"][
            "decisionArtifact"
        ] = "wrong-acceptance.json"
        drifted_workflows.append(("build gate", second_gate_drift))

        for label, workflow in drifted_workflows:
            with self.subTest(boundary=label):
                self.assertTrue(
                    VALIDATOR.validate_contract_against_workflow(contract, workflow),
                    f"{label} drift was not detected",
                )


if __name__ == "__main__":
    unittest.main()
