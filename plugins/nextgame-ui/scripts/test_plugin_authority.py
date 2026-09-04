#!/usr/bin/env python3
"""Tests for validate_plugin_authority.py."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().with_name("validate_plugin_authority.py")
SPEC = importlib.util.spec_from_file_location("validate_plugin_authority", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Cannot load validate_plugin_authority.py")
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


def create_test_root() -> Path:
    """Create a writable fixture root without Python 3.14's Windows 0700 issue.

    ``tempfile.TemporaryDirectory`` creates its directory with mode 0700.  In
    the restricted Windows runner used by this repository, that directory can
    be created but its children cannot be written.  An atomic UUID directory
    with mode 0755 preserves isolation while remaining writable there.
    """

    configured_root = os.environ.get("NEXTGAME_UI_TEST_TMPDIR")
    base = Path(configured_root) if configured_root else Path(tempfile.gettempdir())
    base = base.resolve()
    base.mkdir(parents=True, exist_ok=True)
    root = base / f"nextgame-plugin-authority-{uuid.uuid4().hex}"
    plugin_root = MODULE_PATH.parent.parent.resolve()
    if root == plugin_root or plugin_root in root.parents:
        raise RuntimeError(
            "NEXTGAME_UI_TEST_TMPDIR must not place authority-test fixtures in the plugin tree"
        )
    root.mkdir(mode=0o755)
    return root


class PluginAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = create_test_root()
        self.addCleanup(shutil.rmtree, self.root)
        self.source = self.root / "nextgame-ui"
        self._make_plugin(self.source)

    @staticmethod
    def _manifest(name: str = "nextgame-ui", version: str = "1.2.3+test.1") -> dict[str, str]:
        return {
            "name": name,
            "version": version,
            "description": "fixture",
            "skills": "./skills/",
        }

    def _make_plugin(
        self,
        root: Path,
        *,
        name: str = "nextgame-ui",
        version: str = "1.2.3+test.1",
    ) -> None:
        (root / ".codex-plugin").mkdir(parents=True)
        (root / ".codex-plugin" / "plugin.json").write_text(
            json.dumps(self._manifest(name, version), sort_keys=True),
            encoding="utf-8",
        )
        (root / "skills" / "demo-skill").mkdir(parents=True)
        (root / "skills" / "demo-skill" / "SKILL.md").write_text(
            "# Demo\n",
            encoding="utf-8",
        )
        (root / "payload.txt").write_text("same\n", encoding="utf-8")

    def _copy_installed(self) -> Path:
        installed = self.root / "installed-nextgame-ui"
        shutil.copytree(self.source, installed)
        return installed

    def _run(self, *arguments: str) -> tuple[int, dict, str]:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = validator.main(list(arguments))
        raw = stdout.getvalue()
        return code, json.loads(raw), raw

    def test_exact_match(self) -> None:
        installed = self._copy_installed()
        code, summary, _ = self._run(
            "--source",
            str(self.source),
            "--installed-plugin",
            str(installed),
        )
        self.assertEqual(validator.EXIT_MATCH, code)
        self.assertEqual("match", summary["status"])
        self.assertEqual(
            {"changed": 0, "installedOnly": 0, "sourceOnly": 0},
            summary["differenceCounts"],
        )
        self.assertEqual(summary["sourceTreeSha256"], summary["installedTreeSha256"])

    def test_changed_source_only_and_installed_only_are_detailed(self) -> None:
        installed = self._copy_installed()
        (self.source / "payload.txt").write_text("source changed\n", encoding="utf-8")
        (self.source / "source-only.txt").write_text("source\n", encoding="utf-8")
        (installed / "installed-only.txt").write_text("installed\n", encoding="utf-8")
        report_path = self.root / "report.json"
        code, summary, _ = self._run(
            "--source",
            str(self.source),
            "--installed-plugin",
            str(installed),
            "--output",
            str(report_path),
        )
        self.assertEqual(validator.EXIT_DRIFT, code)
        self.assertEqual("drift", summary["status"])
        self.assertEqual(
            {"changed": 1, "installedOnly": 1, "sourceOnly": 1},
            summary["differenceCounts"],
        )
        details = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual("payload.txt", details["differences"]["changed"][0]["path"])
        self.assertEqual("source-only.txt", details["differences"]["sourceOnly"][0]["path"])
        self.assertEqual("installed-only.txt", details["differences"]["installedOnly"][0]["path"])

    def test_default_exclusions_do_not_create_drift(self) -> None:
        installed = self._copy_installed()
        (self.source / "__pycache__").mkdir()
        (self.source / "__pycache__" / "module.cpython-312.pyc").write_bytes(b"cache")
        (self.source / "loose.pyc").write_bytes(b"cache")
        (self.source / ".plugin-authority-orphan.tmp").write_bytes(b"temporary")
        (self.source / ".plugin-authority-generated").mkdir()
        (self.source / ".plugin-authority-generated" / "state.json").write_text(
            "generated",
            encoding="utf-8",
        )
        (installed / ".pytest_cache").mkdir()
        (installed / ".pytest_cache" / "state").write_text("state", encoding="utf-8")
        (installed / "editor.swp").write_text("swap", encoding="utf-8")
        code, summary, _ = self._run(
            "--source",
            str(self.source),
            "--installed-plugin",
            str(installed),
        )
        self.assertEqual(validator.EXIT_MATCH, code)
        self.assertEqual("match", summary["status"])

    def test_source_only_manifest_mode_and_standalone_detection(self) -> None:
        standalone_root = self.root / "standalone"
        (standalone_root / "demo-skill").mkdir(parents=True)
        (standalone_root / "demo-skill" / "SKILL.md").write_text("# Duplicate\n", encoding="utf-8")
        code, summary, raw = self._run(
            "--source",
            str(self.source),
            "--source-only",
            "--standalone-skills-root",
            str(standalone_root),
        )
        self.assertEqual(validator.EXIT_MATCH, code)
        self.assertEqual("source-only", summary["mode"])
        self.assertEqual(1, summary["standaloneSkillCount"])
        self.assertNotIn(str(standalone_root), raw)

    def test_mode_must_be_explicit(self) -> None:
        code, summary, raw = self._run("--source", str(self.source))
        self.assertEqual(validator.EXIT_ERROR, code)
        self.assertEqual("error", summary["status"])
        self.assertEqual("arguments.mode_required", summary["error"]["code"])
        self.assertEqual(1, raw.count("\n"))

        unsafe_error_report = self.source / "argument-error.json"
        code, summary, _ = self._run(
            "--source",
            str(self.source),
            "--output",
            str(unsafe_error_report),
        )
        self.assertEqual(validator.EXIT_ERROR, code)
        self.assertEqual("arguments.mode_required", summary["error"]["code"])
        self.assertFalse(unsafe_error_report.exists())

    def test_output_inside_source_or_installed_tree_is_rejected(self) -> None:
        installed = self._copy_installed()
        source_report = self.source / "authority-report.json"
        code, summary, _ = self._run(
            "--source",
            str(self.source),
            "--installed-plugin",
            str(installed),
            "--output",
            str(source_report),
        )
        self.assertEqual(validator.EXIT_ERROR, code)
        self.assertEqual("output.inside_plugin_tree", summary["error"]["code"])
        self.assertFalse(source_report.exists())

        installed_report = installed / "authority-report.json"
        code, summary, _ = self._run(
            "--source",
            str(self.source),
            "--installed-plugin",
            str(installed),
            "--output",
            str(installed_report),
        )
        self.assertEqual(validator.EXIT_ERROR, code)
        self.assertEqual("output.inside_plugin_tree", summary["error"]["code"])
        self.assertFalse(installed_report.exists())

    def test_manifest_name_and_version_problems_are_drift(self) -> None:
        wrong_basename = self.root / "wrong-directory"
        self._make_plugin(wrong_basename, version="not-semver")
        code, summary, _ = self._run(
            "--source",
            str(wrong_basename),
            "--source-only",
        )
        self.assertEqual(validator.EXIT_DRIFT, code)
        self.assertEqual(2, summary["manifestIssueCount"])

        leading_zero = self.root / "nextgame-ui-leading-zero"
        self._make_plugin(leading_zero, name="nextgame-ui-leading-zero", version="1.2.3-01")
        code, summary, _ = self._run(
            "--source",
            str(leading_zero),
            "--source-only",
        )
        self.assertEqual(validator.EXIT_DRIFT, code)
        self.assertEqual(1, summary["manifestIssueCount"])

        installed = self._copy_installed()
        manifest_path = installed / ".codex-plugin" / "plugin.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["name"] = "other-plugin"
        manifest["version"] = "9.9.9"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        code, summary, _ = self._run(
            "--source",
            str(self.source),
            "--installed-plugin",
            str(installed),
        )
        self.assertEqual(validator.EXIT_DRIFT, code)
        self.assertEqual(2, summary["manifestIssueCount"])

    def test_marketplace_root_locates_exact_version(self) -> None:
        cache_root = self.root / "cache"
        installed = cache_root / "personal" / "nextgame-ui" / "1.2.3+test.1"
        shutil.copytree(self.source, installed)
        code, summary, _ = self._run(
            "--source",
            str(self.source),
            "--installed-root",
            str(cache_root),
            "--marketplace-name",
            "personal",
        )
        self.assertEqual(validator.EXIT_MATCH, code)
        self.assertEqual("match", summary["status"])

    def test_stdout_is_one_compact_summary_without_difference_paths(self) -> None:
        installed = self._copy_installed()
        (installed / "very-sensitive-difference-name.txt").write_text("x", encoding="utf-8")
        code, summary, raw = self._run(
            "--source",
            str(self.source),
            "--installed-plugin",
            str(installed),
        )
        self.assertEqual(validator.EXIT_DRIFT, code)
        self.assertEqual(1, raw.count("\n"))
        self.assertLess(len(raw), 1000)
        self.assertNotIn("very-sensitive-difference-name", raw)
        self.assertEqual(1, summary["differenceCounts"]["installedOnly"])

    def test_missing_installed_plugin_is_operational_error(self) -> None:
        code, summary, raw = self._run(
            "--source",
            str(self.source),
            "--installed-plugin",
            str(self.root / "missing"),
        )
        self.assertEqual(validator.EXIT_ERROR, code)
        self.assertEqual("error", summary["status"])
        self.assertEqual(1, raw.count("\n"))


if __name__ == "__main__":
    unittest.main()
