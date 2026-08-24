#!/usr/bin/env python3
"""Policy-projection tests for the NextGame UMG program handoff."""

from __future__ import annotations

import copy
import json
import unittest

from _document_contract_common import HANDOFF_SCHEMA, load_json, sha256_file, validate_schema_instance, write_json
from prepare_program_handoff import PROGRAM_VARIABLE_PURPOSE_BY_VALUE_KIND, STATE_CONTROL_DESCRIPTION_BY_KIND
from test_document_contracts import FinalizedSources, error_codes
from validate_program_docx import expected_coverage
from validate_program_handoff import validate_program_handoff


EXCLUDED_MARKERS = (
    "LEAK_GENERATED_CONTENT_SOURCE_OWNER_REFRESH",
    "LEAK_RUNTIME_PARAMETER_TYPE_DEFAULT_TIMING",
    "LEAK_EVENT_CALLBACK_NAME_PAYLOAD",
    "LEAK_COLLECTION_ITEM_SCHEMA",
)

SAFE_DEVIATION_KEYS = {
    "id",
    "status",
    "impact",
    "affectedAssetIds",
    "affectedRequirementRefs",
}


class DocumentPolicyProjectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sources = FinalizedSources()

    def tearDown(self) -> None:
        self.sources.close()

    def _sources_with_excluded_text(self) -> tuple[dict, dict]:
        requirement = copy.deepcopy(self.sources.requirement)
        runtime_reason = " | ".join(EXCLUDED_MARKERS[:2])
        for runtime_field in requirement["uiModel"]["runtimeFields"]:
            runtime_field["reason"] = runtime_reason

        requirement["stateModels"][0]["controlInputs"] = [
            {
                "id": "control-tab-selection",
                "axisId": "axis-tab-selection",
                "kind": "program-state",
                "description": EXCLUDED_MARKERS[2],
                "targetStateIds": ["state-tab-selected", "state-tab-unselected"],
                "evidenceIds": ["evidence-project-state-rules"],
                "claimIds": ["claim-state-branches-variable"],
            }
        ]

        bundle = copy.deepcopy(self.sources.bundle)
        deviation = bundle["verification"]["deviations"][0]
        deviation["expected"] = f"{EXCLUDED_MARKERS[0]} | {EXCLUDED_MARKERS[3]}"
        deviation["actual"] = EXCLUDED_MARKERS[1]
        deviation["reason"] = EXCLUDED_MARKERS[2]
        return requirement, bundle

    def test_excluded_source_text_is_not_projected_or_reported_as_gap(self) -> None:
        requirement, bundle = self._sources_with_excluded_text()
        handoff = self.sources.build_handoff(requirement=requirement, bundle=bundle)

        self.assertEqual([], validate_schema_instance(handoff, load_json(HANDOFF_SCHEMA)))
        serialized = json.dumps(handoff, ensure_ascii=False, sort_keys=True)
        for marker in EXCLUDED_MARKERS:
            self.assertNotIn(marker, serialized)

        variables = [item for asset in handoff["assets"] for item in asset["programVariables"]]
        self.assertEqual({"程序控制可见状态"}, {item["purpose"] for item in variables})

        controls = [
            control
            for asset in handoff["assets"]
            for state_model in asset["states"]
            for control in state_model["controlInputs"]
        ]
        self.assertEqual(["根据程序状态选择目标状态"], [control["description"] for control in controls])
        self.assertEqual(
            {
                "id",
                "axisId",
                "kind",
                "description",
                "targetStateIds",
                "acceptedClaimIds",
            },
            set(controls[0]),
        )

        self.assertTrue(handoff["deviations"])
        self.assertTrue(all(set(deviation) == SAFE_DEVIATION_KEYS for deviation in handoff["deviations"]))
        self.assertEqual([], handoff["gaps"])

    def test_finite_projection_maps_match_schema_enums(self) -> None:
        schema = load_json(HANDOFF_SCHEMA)
        definitions = schema["$defs"]
        self.assertEqual(
            {"text", "image", "visibility", "progress", "state", "collection", "other"},
            set(PROGRAM_VARIABLE_PURPOSE_BY_VALUE_KIND),
        )
        self.assertEqual(
            set(PROGRAM_VARIABLE_PURPOSE_BY_VALUE_KIND.values()),
            set(definitions["programVariable"]["properties"]["purpose"]["enum"]),
        )
        self.assertEqual(
            {"user-interaction", "data-condition", "program-state", "external-state"},
            set(STATE_CONTROL_DESCRIPTION_BY_KIND),
        )
        self.assertEqual(
            set(STATE_CONTROL_DESCRIPTION_BY_KIND.values()),
            set(definitions["controlInput"]["properties"]["description"]["enum"]),
        )
        self.assertEqual(
            {"exclusive-panel-branches", "shared-tree-properties"},
            {
                branch["properties"]["implementationStrategy"]["const"]
                for branch in definitions["stateModel"]["oneOf"]
            },
        )

    def test_exclusive_branches_bind_only_variable_runtime_panel_roots(self) -> None:
        bundle = copy.deepcopy(self.sources.bundle)
        for mapping in bundle["nodeMappings"]:
            if mapping["id"] in {"mapping-tab-selected-background", "mapping-tab-selected-label"}:
                mapping["stateRefs"] = ["state-tab-selected"]

        handoff = self.sources.build_handoff(bundle=bundle)
        states = {
            state["id"]: state
            for asset in handoff["assets"]
            for model in asset["states"]
            for axis in model["axes"]
            for state in axis["states"]
        }
        self.assertEqual("0.3", handoff["version"])
        state_model = next(model for asset in handoff["assets"] for model in asset["states"])
        self.assertEqual("exclusive-panel-branches", state_model["implementationStrategy"])
        self.assertEqual(
            ["PanelSelected"],
            [item["widgetName"] for item in states["state-tab-selected"]["actualSavedVisibilityBindings"]],
        )
        self.assertEqual([], states["state-tab-selected"]["runtimeVisibilityOutcomes"])

        coverage = expected_coverage(handoff)
        branch_statements = [
            item for item in coverage["semanticRelationshipStatements"] if item.startswith("State branch: ")
        ]
        self.assertTrue(branch_statements)
        self.assertTrue(all("visibility=" not in item for item in branch_statements))
        self.assertFalse(any(item.startswith("State outcome: ") for item in coverage["semanticRelationshipStatements"]))

    def test_shared_tree_outcomes_use_only_explicit_accepted_visibility_overrides(self) -> None:
        requirement = copy.deepcopy(self.sources.requirement)
        model = requirement["stateModels"][0]
        model["implementation"] = {
            "strategy": "shared-tree-properties",
            "axisId": "axis-tab-selection",
            "sharedRootElementId": "element-tab-content-panel",
            "stateOverrides": [
                {
                    "stateId": "state-tab-unselected",
                    "changes": [
                        {
                            "elementId": "element-tab-unselected-panel",
                            "property": "visibility",
                            "value": "Hidden",
                        },
                        {
                            "elementId": "element-tab-unselected-panel",
                            "property": "RenderOpacity",
                            "value": 0.5,
                        },
                    ],
                },
                {
                    "stateId": "state-tab-selected",
                    "changes": [
                        {
                            "elementId": "element-tab-selected-panel",
                            "property": "VISIBILITY",
                            "value": "SelfHitTestInvisible",
                        }
                    ],
                },
            ],
        }

        handoff = self.sources.build_handoff(requirement=requirement)
        state_model = next(model for asset in handoff["assets"] for model in asset["states"])
        self.assertEqual("shared-tree-properties", state_model["implementationStrategy"])
        states = {
            state["id"]: state
            for asset in handoff["assets"]
            for state_model in asset["states"]
            for axis in state_model["axes"]
            for state in axis["states"]
        }
        self.assertEqual(
            ["SelfHitTestInvisible"],
            [item["visibility"] for item in states["state-tab-unselected"]["actualSavedVisibilityBindings"]],
        )
        self.assertEqual(
            ["Hidden"],
            [item["visibility"] for item in states["state-tab-unselected"]["runtimeVisibilityOutcomes"]],
        )
        self.assertEqual(
            ["Collapsed"],
            [item["visibility"] for item in states["state-tab-selected"]["actualSavedVisibilityBindings"]],
        )
        self.assertEqual(
            ["SelfHitTestInvisible"],
            [item["visibility"] for item in states["state-tab-selected"]["runtimeVisibilityOutcomes"]],
        )

        statements = expected_coverage(handoff)["semanticRelationshipStatements"]
        outcome_statements = [item for item in statements if item.startswith("State outcome: ")]
        self.assertEqual(2, len(outcome_statements))
        self.assertTrue(any('visibility="Hidden"' in item for item in outcome_statements))
        self.assertTrue(any('visibility="SelfHitTestInvisible"' in item for item in outcome_statements))
        branch_statements = [item for item in statements if item.startswith("State branch: ")]
        self.assertEqual([], branch_statements)

    def test_unresolvable_exclusive_or_shared_visibility_target_fails_projection(self) -> None:
        requirement = copy.deepcopy(self.sources.requirement)
        target_element = next(
            element
            for element in requirement["uiModel"]["elements"]
            if element["id"] == "element-tab-selected-panel"
        )
        target_element["claimIds"] = ["claim-not-accepted"]
        with self.assertRaisesRegex(ValueError, "state-tab-selected.*element-tab-selected-panel"):
            self.sources.build_handoff(requirement=requirement)

        runtime_requirement = copy.deepcopy(self.sources.requirement)
        runtime_element = next(
            element
            for element in runtime_requirement["uiModel"]["elements"]
            if element["id"] == "element-tab-selected-panel"
        )
        runtime_element["runtimeControlled"] = False
        with self.assertRaisesRegex(ValueError, "accepted, in scope, and runtimeControlled"):
            self.sources.build_handoff(requirement=runtime_requirement)

        readback = copy.deepcopy(self.sources.readback)
        for asset in readback["assets"]:
            for widget in asset["widgets"]:
                if widget["widgetName"] == "PanelSelected":
                    widget["isVariable"] = False
        with self.assertRaisesRegex(ValueError, "state-tab-selected.*element-tab-selected-panel"):
            self.sources.build_handoff(readback=readback)

        ambiguous_bundle = copy.deepcopy(self.sources.bundle)
        selected_mapping = next(
            mapping
            for mapping in ambiguous_bundle["nodeMappings"]
            if mapping["id"] == "mapping-tab-selected-panel"
        )
        duplicate_mapping = copy.deepcopy(selected_mapping)
        duplicate_mapping["id"] = "mapping-tab-selected-panel-duplicate"
        ambiguous_bundle["nodeMappings"].append(duplicate_mapping)
        with self.assertRaisesRegex(ValueError, "expected exactly one Bundle mapping.*found 2"):
            self.sources.build_handoff(bundle=ambiguous_bundle)

        shared_requirement = copy.deepcopy(self.sources.requirement)
        shared_requirement["stateModels"][0]["implementation"] = {
            "strategy": "shared-tree-properties",
            "axisId": "axis-tab-selection",
            "sharedRootElementId": "element-tab-content-panel",
            "stateOverrides": [
                {
                    "stateId": "state-tab-unselected",
                    "changes": [
                        {
                            "elementId": "element-tab-unselected-panel",
                            "property": "Visibility",
                            "value": "SelfHitTestInvisible",
                        }
                    ],
                },
                {
                    "stateId": "state-tab-selected",
                    "changes": [
                        {
                            "elementId": "element-tab-selected-panel",
                            "property": "Visibility",
                            "value": "SelfHitTestInvisible",
                        }
                    ],
                },
            ],
        }
        bundle = copy.deepcopy(self.sources.bundle)
        selected_mapping = next(
            mapping for mapping in bundle["nodeMappings"] if mapping["id"] == "mapping-tab-selected-panel"
        )
        selected_mapping["stateRefs"] = []
        with self.assertRaisesRegex(ValueError, "state-tab-selected.*element-tab-selected-panel"):
            self.sources.build_handoff(requirement=shared_requirement, bundle=bundle)

        shared_readback = copy.deepcopy(self.sources.readback)
        selected_widget = next(
            widget
            for asset in shared_readback["assets"]
            for widget in asset["widgets"]
            if widget["widgetName"] == "PanelSelected"
        )
        selected_widget.pop("visibility")
        with self.assertRaisesRegex(ValueError, "must report saved Visibility evidence"):
            self.sources.build_handoff(requirement=shared_requirement, readback=shared_readback)

        layout_bundle = copy.deepcopy(self.sources.bundle)
        child_asset = next(asset for asset in layout_bundle["assets"] if asset["id"] == "build-child-navigation-tab")
        layout_path = self.sources.root / child_asset["layoutSpecPath"]
        layout = load_json(layout_path)
        selected_node = next(node for node in layout["nodes"] if node["id"] == "node-tab-selected-panel")
        selected_node["isVariable"] = False
        write_json(layout_path, layout)
        child_asset["layoutSpecSha256"] = sha256_file(layout_path)
        with self.assertRaisesRegex(ValueError, "UILayoutSpec node and actual Unreal Widget must both be variables"):
            self.sources.build_handoff(bundle=layout_bundle)

    def test_schema_and_exact_projection_reject_free_text_reintroduction(self) -> None:
        handoff = self.sources.build_handoff()

        invalid_purpose = copy.deepcopy(handoff)
        invalid_purpose["assets"][0]["programVariables"][0]["purpose"] = EXCLUDED_MARKERS[1]
        self.assertTrue(validate_schema_instance(invalid_purpose, load_json(HANDOFF_SCHEMA)))

        invalid_deviation = copy.deepcopy(handoff)
        invalid_deviation["deviations"][0]["reason"] = EXCLUDED_MARKERS[3]
        self.assertTrue(validate_schema_instance(invalid_deviation, load_json(HANDOFF_SCHEMA)))

        projection_mismatch = copy.deepcopy(handoff)
        projection_mismatch["assets"][0]["programVariables"][0]["purpose"] = "程序控制动态内容"
        handoff_path = self.sources.root / "ui-program-handoff-policy-test.json"
        write_json(handoff_path, projection_mismatch)
        report = validate_program_handoff(
            projection_mismatch,
            load_json(HANDOFF_SCHEMA),
            handoff_path=handoff_path,
            requirement=self.sources.requirement,
            requirement_path=self.sources.requirement_path,
            bundle=self.sources.bundle,
            bundle_path=self.sources.bundle_path,
            readback=self.sources.readback,
            readback_path=self.sources.readback_path,
            build_acceptance=self.sources.acceptance,
            build_acceptance_path=self.sources.acceptance_path,
        )
        self.assertIn("projection.mismatch", error_codes(report))

        forbidden_key = copy.deepcopy(handoff)
        forbidden_key["assets"][0]["programVariables"][0]["eventPayload"] = "forbidden"
        write_json(handoff_path, forbidden_key)
        report = validate_program_handoff(
            forbidden_key,
            load_json(HANDOFF_SCHEMA),
            handoff_path=handoff_path,
            requirement=self.sources.requirement,
            requirement_path=self.sources.requirement_path,
            bundle=self.sources.bundle,
            bundle_path=self.sources.bundle_path,
            readback=self.sources.readback,
            readback_path=self.sources.readback_path,
            build_acceptance=self.sources.acceptance,
            build_acceptance_path=self.sources.acceptance_path,
        )
        self.assertIn("content.forbidden_field", error_codes(report))


if __name__ == "__main__":
    unittest.main()
