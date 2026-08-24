#!/usr/bin/env python3
"""Run the repository's portable, plugin, and adapter regression checks."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "nextgame-ui"


def run(label: str, *args: str) -> None:
    print(f"\n== {label} ==", flush=True)
    completed = subprocess.run([sys.executable, *args], cwd=ROOT, check=False)
    if completed.returncode:
        raise SystemExit(f"{label} failed with exit code {completed.returncode}")


def run_all_script_tests(label: str, directory: Path, *, exclude: set[str] | None = None) -> None:
    excluded = exclude or set()
    tests = [path for path in sorted(directory.glob("test_*.py")) if path.name not in excluded]
    if not tests:
        raise SystemExit(f"{label}: no test_*.py files found in {directory}")
    for test in tests:
        run(f"{label}: {test.name}", str(test))


def validate_json_files() -> None:
    count = 0
    for path in sorted(ROOT.rglob("*.json")):
        if ".git" in path.parts or "__pycache__" in path.parts:
            continue
        with path.open("r", encoding="utf-8") as handle:
            json.load(handle)
        count += 1
    print(f"Validated {count} JSON files.")


def validate_release_boundaries() -> None:
    forbidden_parts = {"Saved", "Intermediate", "DerivedDataCache", "__pycache__"}
    forbidden_suffixes = {".pyc", ".pyo", ".uasset", ".umap"}
    runtime_names = {
        "dispatch-manifest.json",
        "document-verification.json",
        "program-document-content.json",
        "request-packet.json",
        "render-evidence.json",
        "ui-build-acceptance.json",
        "ui-build-bundle.json",
        "ui-program-handoff.json",
        "ui-requirement.draft.json",
        "ui-requirement.json",
        "ui-requirement.pending.json",
        "unreal-widget-readback.json",
        "verification.json",
    }
    allowed_roots = {
        ".agents",
        ".gitattributes",
        ".github",
        ".gitignore",
        "adapters",
        "AGENTS.md",
        "NOTICE.md",
        "orchestration",
        "plugins",
        "README.md",
        "requirements-optional.txt",
        "scripts",
    }
    offenders: list[str] = []
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout.decode("utf-8").split("\0")
    for item in listed:
        if not item:
            continue
        relative = Path(item)
        if relative.parts[0] not in allowed_roots:
            offenders.append(relative.as_posix())
            continue
        if (
            any(part in forbidden_parts for part in relative.parts)
            or relative.suffix.lower() in forbidden_suffixes
            or relative.name in runtime_names
        ):
            offenders.append(relative.as_posix())
    if offenders:
        raise SystemExit("Release boundary contains generated/project assets:\n" + "\n".join(offenders))


def main() -> int:
    run(
        "portable workflow contract",
        "orchestration/scripts/portable_workflow.py",
        "validate",
        "--workflow",
        "orchestration/nextgame-ui.requirements.workflow.json",
        "--schema",
        "orchestration/workflow.schema.json",
    )
    run("portable workflow tests", "-m", "unittest", "discover", "-s", "orchestration/tests", "-p", "test_*.py")
    run("runtime adapter contract", "adapters/validate_adapters.py")
    run("runtime adapter tests", "-m", "unittest", "discover", "-s", "adapters/tests", "-p", "test_*.py")
    run("shared registry tests", "-m", "unittest", "discover", "-s", str(PLUGIN / "scripts"), "-p", "test_*.py")
    run(
        "requirement-analysis tests",
        "-m",
        "unittest",
        "discover",
        "-s",
        str(PLUGIN / "skills" / "analyze-nextgame-ui-requirements" / "scripts"),
        "-p",
        "test_*.py",
    )
    run_all_script_tests("UMG build tests", PLUGIN / "skills" / "build-nextgame-umg" / "scripts")

    document_scripts = PLUGIN / "skills" / "document-nextgame-umg" / "scripts"
    document_excludes: set[str] = set()
    if importlib.util.find_spec("docx") is None:
        document_excludes.add("test_program_document_template.py")
        print("\npython-docx is unavailable; skipping only test_program_document_template.py.")
    run_all_script_tests("document tests", document_scripts, exclude=document_excludes)

    validate_json_files()
    validate_release_boundaries()
    print("\nRelease validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
