#!/usr/bin/env python3
"""Regression tests for the pre-mutation build-plan evidence contract."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import unittest
import uuid
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch


SCRIPT_ROOT = Path(__file__).resolve().parents[1] / "scripts"
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import build_plan_evidence as evidence_tool


GENERATED_AT = "2026-09-04T08:00:00Z"


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def write_json(path: Path, value: object) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json_bytes(value)
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


class BuildPlanEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.semantic_patch = patch.object(
            evidence_tool,
            "_validate_semantic_authorities",
            return_value=None,
        )
        self.semantic_patch.start()
        self.addCleanup(self.semantic_patch.stop)
        self.native_plan_patch = patch.object(
            evidence_tool,
            "_build_native_plan",
            side_effect=self.unit_native_plan,
        )
        self.native_plan_patch.start()
        self.addCleanup(self.native_plan_patch.stop)
        test_temp = REPO_ROOT / "Saved" / "CodexUITestTemp"
        test_temp.mkdir(parents=True, exist_ok=True)
        self.test_directory = test_temp / f"nextgame-plan-evidence-{uuid.uuid4().hex}"
        self.test_directory.mkdir()
        self.root = self.test_directory / "run"
        self.root.mkdir()
        self.requirement = {
            "version": "0.4",
            "requestId": "role-test-evidence",
            "revision": 3,
            "reviewGate": {"status": "accepted"},
        }
        self.requirement_sha = write_json(
            self.root / evidence_tool.REQUIREMENT_PATH,
            self.requirement,
        )
        self.view = {
            "version": "0.1",
            "viewKind": "nextgame-ui-accepted-build-view",
            "mode": "projected",
            "buildAllowed": True,
            "bindings": {"requirementFileSha256": self.requirement_sha},
            "coverage": {"status": "complete", "missingCanonicalIds": []},
        }
        write_json(self.root / evidence_tool.ACCEPTED_VIEW_PATH, self.view)

        first_layout = {"version": "0.2", "assetId": "build-role-item", "nodes": []}
        second_layout = {"version": "0.2", "assetId": "build-role-screen", "nodes": []}
        first_layout_sha = write_json(self.root / "layouts/item.layout.json", first_layout)
        second_layout_sha = write_json(self.root / "layouts/screen.layout.json", second_layout)
        self.bundle = {
            "version": "0.3",
            "bundleId": "bundle-role-test-evidence",
            "requirement": {
                "path": "ui-requirement.json",
                "sha256": self.requirement_sha,
            },
            "assets": [
                {
                    "id": "build-role-item",
                    "assetPath": "/Game/UI/UMG/Role/Widgets/uw_role_item",
                    "representationKind": "layout-spec",
                    "layoutSpecPath": "layouts/item.layout.json",
                    "layoutSpecSha256": first_layout_sha,
                    "buildOrder": 0,
                },
                {
                    "id": "build-role-screen",
                    "assetPath": "/Game/UI/UMG/Role/umg_role_screen",
                    "representationKind": "layout-spec",
                    "layoutSpecPath": "layouts/screen.layout.json",
                    "layoutSpecSha256": second_layout_sha,
                    "buildOrder": 1,
                },
            ],
            "execution": {
                "buildOrderAssetIds": ["build-role-item", "build-role-screen"]
            },
        }
        self.write_bundle(refresh_plans=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.test_directory, ignore_errors=False)

    @property
    def evidence_path(self) -> Path:
        return self.root / evidence_tool.EVIDENCE_PATH

    def write_bundle(self, *, refresh_plans: bool = False) -> str:
        bundle_sha = write_json(self.root / evidence_tool.PLANNED_BUNDLE_PATH, self.bundle)
        if refresh_plans:
            self.write_plans()
        return bundle_sha

    def write_plans(self) -> None:
        plan_root = self.root / "plans"
        if plan_root.exists():
            for path in plan_root.glob("*.plan.json"):
                path.unlink()
        for asset in self.bundle["assets"]:
            if asset.get("representationKind") == "reuse-only":
                continue
            asset_id = asset["id"]
            plan = self.unit_native_plan(
                evidence_tool.DEFAULT_PLUGIN_ROOT,
                self.root,
                asset["layoutSpecPath"],
            )
            write_json(plan_root / f"{asset_id}.plan.json", plan)

    def unit_native_plan(
        self,
        _plugin_root: Path,
        _artifact_root: Path,
        layout_path: str,
    ) -> dict:
        asset = next(
            item
            for item in self.bundle["assets"]
            if item.get("layoutSpecPath") == layout_path
        )
        return {
            "version": "0.2",
            "sourceSpec": layout_path,
            "assetPath": asset["assetPath"],
            "steps": [
                {
                    "stepId": "create-blueprint",
                    "operation": "call_tool",
                    "toolsetName": "UMGToolSet.UMGToolSet",
                    "toolName": "CreateWidgetBlueprint",
                    "arguments": {"assetName": asset["assetPath"].rsplit("/", 1)[-1]},
                }
            ],
        }

    def generate(self) -> dict:
        return evidence_tool.generate_evidence(
            self.root,
            generated_at_utc=GENERATED_AT,
        )

    def test_happy_path_is_schema_valid_and_validate_only_is_read_only(self) -> None:
        evidence = self.generate()
        self.assertEqual(evidence, evidence_tool.validate_evidence(self.root))
        self.assertEqual(
            ["build-role-item", "build-role-screen"],
            [entry["assetId"] for entry in evidence["layouts"]],
        )
        self.assertEqual(
            ["build-role-item", "build-role-screen"],
            [entry["assetId"] for entry in evidence["plans"]],
        )
        self.assertTrue(all(evidence["checks"].values()))
        original = self.evidence_path.read_bytes()

        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = evidence_tool.main([str(self.root), "--validate-only"])
        self.assertEqual(0, result, stderr.getvalue())
        self.assertEqual(original, self.evidence_path.read_bytes())
        self.assertIn('"action":"validated"', stdout.getvalue())

        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            return
        schema = json.loads(
            (Path(__file__).resolve().parents[1] / "build-plan-pre-mutation.schema.json").read_text(
                encoding="utf-8"
            )
        )
        Draft202012Validator.check_schema(schema)
        self.assertEqual([], list(Draft202012Validator(schema).iter_errors(evidence)))

    def test_authority_hash_mismatches_are_rejected(self) -> None:
        original_view = copy.deepcopy(self.view)
        original_bundle = copy.deepcopy(self.bundle)
        for name in ("view", "bundle"):
            with self.subTest(name=name):
                self.view = copy.deepcopy(original_view)
                self.bundle = copy.deepcopy(original_bundle)
                if name == "view":
                    self.view["bindings"]["requirementFileSha256"] = "0" * 64
                else:
                    self.bundle["requirement"]["sha256"] = "0" * 64
                write_json(self.root / evidence_tool.ACCEPTED_VIEW_PATH, self.view)
                self.write_bundle()
                with self.assertRaises(evidence_tool.EvidenceError):
                    self.generate()
                self.assertFalse(self.evidence_path.exists())

    def test_layout_hash_mismatch_is_rejected(self) -> None:
        self.bundle["assets"][0]["layoutSpecSha256"] = "0" * 64
        self.write_bundle(refresh_plans=True)
        with self.assertRaisesRegex(evidence_tool.EvidenceError, "layout sidecar hash mismatch"):
            self.generate()
        self.assertFalse(self.evidence_path.exists())

    def test_asset_coverage_and_order_drift_are_rejected(self) -> None:
        mutations = {
            "missing": lambda bundle: bundle["execution"]["buildOrderAssetIds"].pop(),
            "extra": lambda bundle: bundle["execution"]["buildOrderAssetIds"].append("build-role-extra"),
            "duplicate-order": lambda bundle: bundle["execution"].__setitem__(
                "buildOrderAssetIds", ["build-role-item", "build-role-item"]
            ),
            "order-drift": lambda bundle: bundle["execution"]["buildOrderAssetIds"].reverse(),
            "duplicate-asset": lambda bundle: bundle["assets"].append(copy.deepcopy(bundle["assets"][0])),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                bundle = copy.deepcopy(self.bundle)
                mutate(bundle)
                write_json(self.root / evidence_tool.PLANNED_BUNDLE_PATH, bundle)
                with self.assertRaises(evidence_tool.EvidenceError):
                    self.generate()
                self.assertFalse(self.evidence_path.exists())

    def test_duplicate_layout_path_is_rejected(self) -> None:
        self.bundle["assets"][1]["layoutSpecPath"] = self.bundle["assets"][0]["layoutSpecPath"]
        self.bundle["assets"][1]["layoutSpecSha256"] = self.bundle["assets"][0]["layoutSpecSha256"]
        self.write_bundle(refresh_plans=True)
        with self.assertRaisesRegex(evidence_tool.EvidenceError, "enumerated more than once"):
            self.generate()

    def test_extra_layout_or_plan_sidecar_is_rejected(self) -> None:
        for relative_path, payload in (
            ("layouts/undeclared.layout.json", {"version": "0.2"}),
            ("plans/undeclared.plan.json", {"assetId": "build-role-item"}),
        ):
            with self.subTest(relative_path=relative_path):
                extra = self.root / relative_path
                write_json(extra, payload)
                with self.assertRaisesRegex(evidence_tool.EvidenceError, "does not exactly match"):
                    self.generate()
                self.assertFalse(self.evidence_path.exists())
                extra.unlink()

    def test_path_traversal_and_non_posix_paths_are_rejected(self) -> None:
        outside = self.root.parent / "outside.layout.json"
        outside_sha = write_json(outside, {"version": "0.2"})
        for bad_path in ("../outside.layout.json", "layouts/../outside.layout.json", "layouts\\item.layout.json"):
            with self.subTest(path=bad_path):
                bundle = copy.deepcopy(self.bundle)
                bundle["assets"][0]["layoutSpecPath"] = bad_path
                bundle["assets"][0]["layoutSpecSha256"] = outside_sha
                write_json(self.root / evidence_tool.PLANNED_BUNDLE_PATH, bundle)
                with self.assertRaises(evidence_tool.EvidenceError):
                    self.generate()
                self.assertFalse(self.evidence_path.exists())

    def test_symlink_escape_is_rejected(self) -> None:
        outside = self.root.parent / "outside-symlink.layout.json"
        outside_sha = write_json(outside, {"version": "0.2"})
        link = self.root / "layouts/escape.layout.json"
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"symlink creation is unavailable: {error}")
        self.bundle["assets"][0]["layoutSpecPath"] = "layouts/escape.layout.json"
        self.bundle["assets"][0]["layoutSpecSha256"] = outside_sha
        self.write_bundle()
        with self.assertRaisesRegex(evidence_tool.EvidenceError, "escapes the artifact root"):
            self.generate()

    def test_reuse_only_asset_has_explicit_null_skip_records(self) -> None:
        reuse = self.bundle["assets"][0]
        reuse.update(
            {
                "representationKind": "reuse-only",
                "layoutSpecPath": None,
                "layoutSpecSha256": None,
            }
        )
        (self.root / "layouts/item.layout.json").unlink()
        plan_path = self.root / "plans/build-role-item.plan.json"
        plan_path.unlink()
        self.write_bundle(refresh_plans=True)
        evidence = self.generate()
        expected_layout = {
            "assetId": "build-role-item",
            "representationKind": "reuse-only",
            "path": None,
            "sha256": None,
            "skipReason": "reuse-only",
        }
        expected_plan = {
            "assetId": "build-role-item",
            "representationKind": "reuse-only",
            "path": None,
            "sha256": None,
            "sourceSpec": None,
            "assetPath": "/Game/UI/UMG/Role/Widgets/uw_role_item",
            "layoutSpecSha256": None,
            "requirementFileSha256": self.requirement_sha,
            "acceptedBuildViewFileSha256": hashlib.sha256(
                (self.root / evidence_tool.ACCEPTED_VIEW_PATH).read_bytes()
            ).hexdigest(),
            "plannedBundleFileSha256": hashlib.sha256(
                (self.root / evidence_tool.PLANNED_BUNDLE_PATH).read_bytes()
            ).hexdigest(),
            "planCanonicalSha256": None,
            "stepCount": 0,
            "skipReason": "reuse-only",
        }
        self.assertEqual(expected_layout, evidence["layouts"][0])
        self.assertEqual(expected_plan, evidence["plans"][0])
        self.assertEqual(evidence, evidence_tool.validate_evidence(self.root))

    def test_reuse_only_asset_cannot_retain_layout_binding(self) -> None:
        self.bundle["assets"][0]["representationKind"] = "reuse-only"
        self.write_bundle()
        with self.assertRaisesRegex(evidence_tool.EvidenceError, "must have null layout"):
            self.generate()

    def test_missing_or_cross_bound_plan_is_rejected(self) -> None:
        path = self.root / "plans/build-role-item.plan.json"
        for name, action in {
            "missing": lambda: path.unlink(),
            "cross-bound": lambda: write_json(path, {"assetId": "build-role-screen"}),
        }.items():
            with self.subTest(name=name):
                action()
                with self.assertRaises(evidence_tool.EvidenceError):
                    self.generate()
                self.assertFalse(self.evidence_path.exists())
                self.write_plans()

    def test_malformed_empty_and_stale_plans_are_rejected(self) -> None:
        path = self.root / "plans/build-role-item.plan.json"

        def rewrite(mutator) -> None:
            plan = json.loads(path.read_text(encoding="utf-8"))
            mutator(plan)
            write_json(path, plan)

        mutations = {
            "empty-steps": lambda plan: plan.__setitem__("steps", []),
            "stale-source": lambda plan: plan.__setitem__("sourceSpec", "layouts/stale.layout.json"),
            "wrong-target": lambda plan: plan.__setitem__(
                "assetPath", "/Game/UI/UMG/Role/umg_wrong"
            ),
            "unknown-field": lambda plan: plan.__setitem__("unexpected", True),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                self.write_plans()
                rewrite(mutate)
                with self.assertRaises(evidence_tool.EvidenceError):
                    self.generate()
                self.assertFalse(self.evidence_path.exists())

        self.write_plans()
        rewrite(
            lambda plan: plan["steps"][0]["arguments"].__setitem__("tampered", True)
        )
        with self.assertRaisesRegex(evidence_tool.EvidenceError, "not the exact deterministic"):
            self.generate()

    def test_tampered_evidence_is_rejected_exactly(self) -> None:
        original = self.generate()
        mutations = {
            "authority-hash": lambda value: value["bindings"]["plannedBundle"].__setitem__("sha256", "0" * 64),
            "missing-plan": lambda value: value["plans"].pop(),
            "extra-plan": lambda value: value["plans"].append(copy.deepcopy(value["plans"][0])),
            "duplicate-plan": lambda value: value["plans"].__setitem__(1, copy.deepcopy(value["plans"][0])),
            "order": lambda value: value["layouts"].reverse(),
            "false-check": lambda value: value["checks"].__setitem__("planHashesValid", False),
            "unknown-field": lambda value: value.__setitem__("unexpected", True),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                tampered = copy.deepcopy(original)
                mutate(tampered)
                write_json(self.evidence_path, tampered)
                with self.assertRaises(evidence_tool.EvidenceError):
                    evidence_tool.validate_evidence(self.root)

    def test_failed_generation_removes_stale_success(self) -> None:
        self.generate()
        self.assertTrue(self.evidence_path.is_file())
        (self.root / "layouts/item.layout.json").write_text("tampered\n", encoding="utf-8")
        with self.assertRaises(evidence_tool.EvidenceError):
            self.generate()
        self.assertFalse(self.evidence_path.exists())

    def test_cli_error_is_nonzero_and_leaves_no_success_file(self) -> None:
        (self.root / "plans/build-role-screen.plan.json").unlink()
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = evidence_tool.main(
                [str(self.root), "--generated-at-utc", GENERATED_AT]
            )
        self.assertEqual(1, result)
        self.assertFalse(self.evidence_path.exists())
        self.assertIn("error:", stderr.getvalue())


class BuildPlanEvidenceSemanticIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        source_plugin_root = REPO_ROOT / "plugins" / "nextgame-ui"
        assets_root = (
            source_plugin_root
            / "skills"
            / "analyze-nextgame-ui-requirements"
            / "assets"
        )
        test_temp = REPO_ROOT / "Saved" / "CodexUITestTemp"
        test_temp.mkdir(parents=True, exist_ok=True)
        cls.class_root = test_temp / f"plan-evidence-semantic-{uuid.uuid4().hex}"
        cls.plugin_root = cls.class_root / "validator-plugin"
        shutil.copytree(source_plugin_root, cls.plugin_root)
        requirement_schema = (
            cls.plugin_root
            / "skills"
            / "analyze-nextgame-ui-requirements"
            / "assets"
            / "ui-requirement-spec.schema.json"
        )
        requirement_schema.write_bytes(
            requirement_schema.read_bytes().replace(b"\r\n", b"\n")
        )
        schema_sha = hashlib.sha256(requirement_schema.read_bytes()).hexdigest()
        review_view_script = (
            cls.plugin_root
            / "skills"
            / "analyze-nextgame-ui-requirements"
            / "scripts"
            / "review_view.py"
        )
        review_view_text = review_view_script.read_text(encoding="utf-8")
        review_view_text = re.sub(
            r'(?m)^(SUPPORTED_REQUIREMENT_SCHEMA_SHA256 = ")[0-9a-f]{64}(".*)$',
            rf"\g<1>{schema_sha}\g<2>",
            review_view_text,
            count=1,
        )
        review_view_script.write_text(review_view_text, encoding="utf-8", newline="\n")
        cls.base_root = cls.class_root / "base"
        cls.base_root.mkdir(parents=True)

        child_relative = "layouts/example-composite-tabs-child-layout-spec.json"
        screen_relative = "layouts/example-composite-tabs-screen-layout-spec.json"
        requirement = json.loads(
            (assets_root / "example-composite-tabs-requirement.json").read_text(
                encoding="utf-8"
            )
        )
        layout_paths_by_plan = {
            "asset-child-navigation-tab": child_relative,
            "asset-screen-role": screen_relative,
        }
        for asset_plan in requirement["assetPlan"]:
            if asset_plan["id"] in layout_paths_by_plan:
                asset_plan["layoutSpecPath"] = layout_paths_by_plan[asset_plan["id"]]
        approval_material = copy.deepcopy(requirement)
        approval_material["reviewGate"].pop("approvedContentSha256", None)
        requirement["reviewGate"]["approvedContentSha256"] = evidence_tool.canonical_sha256(
            approval_material
        )
        cls.requirement_sha = write_json(
            cls.base_root / evidence_tool.REQUIREMENT_PATH,
            requirement,
        )

        accepted_builder = (
            cls.plugin_root
            / "skills"
            / "analyze-nextgame-ui-requirements"
            / "scripts"
            / "accepted_build_view.py"
        )
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                str(accepted_builder),
                evidence_tool.REQUIREMENT_PATH,
                evidence_tool.ACCEPTED_VIEW_PATH,
            ],
            cwd=cls.base_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "could not build integration Accepted Build View: "
                + (completed.stderr or completed.stdout)
            )

        child_layout = json.loads(
            (assets_root / "example-composite-tabs-child-layout-spec.json").read_text(
                encoding="utf-8"
            )
        )
        child_layout["nodes"][1]["slotLayout"] = {
            "anchors": {"minimum": [0, 0], "maximum": [0, 0]},
            "offsets": {"left": 0, "top": 0, "right": 307, "bottom": 130},
            "alignment": [0, 0],
            "autoSize": False,
        }
        child_layout["nodes"][3]["buttonSlot"] = {
            "padding": [0, 0, 0, 0],
            "horizontalAlignment": "Fill",
            "verticalAlignment": "Fill",
        }
        screen_layout = json.loads(
            (assets_root / "example-composite-tabs-screen-layout-spec.json").read_text(
                encoding="utf-8"
            )
        )
        screen_layout["profile"]["interactive"] = False

        child_sha = write_json(cls.base_root / child_relative, child_layout)
        screen_sha = write_json(cls.base_root / screen_relative, screen_layout)

        bundle = json.loads(
            (assets_root / "example-composite-tabs-build-bundle.json").read_text(
                encoding="utf-8"
            )
        )
        bundle["requirement"]["path"] = evidence_tool.REQUIREMENT_PATH
        bundle["requirement"]["sha256"] = cls.requirement_sha
        bundle["requirement"]["approvedContentSha256"] = requirement["reviewGate"][
            "approvedContentSha256"
        ]
        bundle["assets"][0]["layoutSpecPath"] = child_relative
        bundle["assets"][0]["layoutSpecSha256"] = child_sha
        bundle["assets"][1]["layoutSpecPath"] = screen_relative
        bundle["assets"][1]["layoutSpecSha256"] = screen_sha
        cls.base_bundle = bundle
        write_json(cls.base_root / evidence_tool.PLANNED_BUNDLE_PATH, bundle)

        plan_root = cls.base_root / "plans"
        plan_root.mkdir()
        for asset in bundle["assets"]:
            plan = evidence_tool._build_native_plan(
                cls.plugin_root,
                cls.base_root,
                asset["layoutSpecPath"],
            )
            write_json(plan_root / f"{asset['id']}.plan.json", plan)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.class_root, ignore_errors=True)

    def setUp(self) -> None:
        self.root = self.class_root / f"case-{uuid.uuid4().hex}"
        shutil.copytree(self.base_root, self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def generate(self) -> dict:
        return evidence_tool.generate_evidence(
            self.root,
            generated_at_utc=GENERATED_AT,
            plugin_root=self.plugin_root,
        )

    def test_real_full_validators_and_native_plan_regeneration_pass(self) -> None:
        evidence = self.generate()
        self.assertEqual(
            [len(json.loads((self.root / entry["path"]).read_text(encoding="utf-8"))["steps"]) for entry in evidence["plans"]],
            [entry["stepCount"] for entry in evidence["plans"]],
        )
        self.assertEqual(
            evidence,
            evidence_tool.validate_evidence(
                self.root,
                plugin_root=self.plugin_root,
            ),
        )

    def test_semantically_invalid_layout_is_rejected_even_when_rehashed(self) -> None:
        bundle_path = self.root / evidence_tool.PLANNED_BUNDLE_PATH
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        layout_path = self.root / bundle["assets"][1]["layoutSpecPath"]
        layout = json.loads(layout_path.read_text(encoding="utf-8"))
        layout["profile"]["interactive"] = True
        bundle["assets"][1]["layoutSpecSha256"] = write_json(layout_path, layout)
        write_json(bundle_path, bundle)
        with self.assertRaisesRegex(evidence_tool.EvidenceError, "prepare_build.py rejected"):
            self.generate()
        self.assertFalse((self.root / evidence_tool.EVIDENCE_PATH).exists())

    def test_view_projection_coverage_omission_is_rejected(self) -> None:
        view_path = self.root / evidence_tool.ACCEPTED_VIEW_PATH
        view = json.loads(view_path.read_text(encoding="utf-8"))
        projected = view["coverage"]["acceptedClaims"]["buildProjected"]
        self.assertTrue(projected)
        projected.pop()
        write_json(view_path, view)
        with self.assertRaisesRegex(
            evidence_tool.EvidenceError,
            "Accepted Build View validation",
        ):
            self.generate()

    def test_full_requirement_coverage_omission_is_rejected(self) -> None:
        bundle_path = self.root / evidence_tool.PLANNED_BUNDLE_PATH
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        bundle["nodeMappings"] = [
            mapping
            for mapping in bundle["nodeMappings"]
            if "element-tab-selected-accent" not in mapping.get("requirementRefs", [])
        ]
        write_json(bundle_path, bundle)
        with self.assertRaisesRegex(
            evidence_tool.EvidenceError,
            "UIBuildBundle validation|build-coverage validation",
        ):
            self.generate()

    def test_native_plan_step_tampering_is_rejected(self) -> None:
        path = self.root / "plans/build-child-navigation-tab.plan.json"
        plan = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(plan["steps"])
        plan["steps"].pop()
        write_json(path, plan)
        with self.assertRaisesRegex(evidence_tool.EvidenceError, "exact deterministic prepare_build.py"):
            self.generate()

    def test_native_plan_cross_asset_mismatch_is_rejected(self) -> None:
        child_plan = self.root / "plans/build-child-navigation-tab.plan.json"
        screen_plan = self.root / "plans/build-screen-role.plan.json"
        child_plan.write_bytes(screen_plan.read_bytes())
        with self.assertRaisesRegex(evidence_tool.EvidenceError, "exact deterministic prepare_build.py"):
            self.generate()


if __name__ == "__main__":
    unittest.main()
