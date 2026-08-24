#!/usr/bin/env python3
"""Validate a non-executable, Bundle-local SharedWidget bootstrap snapshot."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from validate_shared_widget_registry import canonical_sha256, issue, load_json, validate_schema_instance


PLUGIN_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCHEMA = PLUGIN_ROOT / "assets" / "shared-widget-bootstrap.schema.json"
ZERO_SHA256 = "0" * 64


def compute_bootstrap_contract_sha256(entry: dict[str, Any]) -> str:
    """Hash only the declared construction intent; no Unreal identity is implied."""

    return canonical_sha256({key: value for key, value in entry.items() if key != "bootstrapContractSha256"})


def validate_bootstrap_snapshot(snapshot: Any, schema: dict[str, Any]) -> dict[str, Any]:
    errors = validate_schema_instance(snapshot, schema, root_schema=schema)
    warnings: list[dict[str, str]] = []
    if not isinstance(snapshot, dict):
        return {"valid": not errors, "errors": errors, "warnings": warnings}

    base_registry = snapshot.get("baseRegistry")
    if isinstance(base_registry, dict) and base_registry.get("registrySha256") == ZERO_SHA256:
        errors.append(
            issue(
                "bootstrap.base_registry_zero_sha256",
                "$.baseRegistry.registrySha256",
                "A planned bootstrap must bind the real base Registry digest; a zero sentinel is forbidden.",
            )
        )

    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    entries = snapshot.get("entries") if isinstance(snapshot.get("entries"), list) else []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        path = f"$.entries[{index}]"
        entry_id = entry.get("id")
        asset_path = entry.get("assetPath")
        if isinstance(entry_id, str):
            if entry_id in seen_ids:
                errors.append(issue("bootstrap.duplicate_id", f"{path}.id", "Bootstrap entry IDs must be unique."))
            seen_ids.add(entry_id)
        if isinstance(asset_path, str):
            if asset_path in seen_paths:
                errors.append(issue("bootstrap.duplicate_asset", f"{path}.assetPath", "Bootstrap asset paths must be unique."))
            seen_paths.add(asset_path)
            asset_name = asset_path.rsplit("/", 1)[-1]
            expected_object = f"{asset_path}.{asset_name}"
            expected_class = f"{expected_object}_C"
            if entry.get("expectedObjectPath") != expected_object:
                errors.append(issue("bootstrap.expected_object_path", f"{path}.expectedObjectPath", f"Expected {expected_object}."))
            if entry.get("expectedGeneratedClassPath") != expected_class:
                errors.append(issue("bootstrap.expected_class_path", f"{path}.expectedGeneratedClassPath", f"Expected {expected_class}."))

        for field in ("layoutSpecSha256", "bootstrapContractSha256"):
            if entry.get(field) == ZERO_SHA256:
                errors.append(issue("bootstrap.zero_sha256", f"{path}.{field}", "Zero SHA-256 sentinels are forbidden."))
        if entry.get("bootstrapContractSha256") != compute_bootstrap_contract_sha256(entry):
            errors.append(
                issue(
                    "bootstrap.contract_sha256",
                    f"{path}.bootstrapContractSha256",
                    f"Bootstrap contract hash mismatch; expected {compute_bootstrap_contract_sha256(entry)}.",
                )
            )

    return {"valid": not errors, "errors": errors, "warnings": warnings}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    args = parser.parse_args()
    try:
        output = validate_bootstrap_snapshot(load_json(args.snapshot), load_json(args.schema))
    except (OSError, json.JSONDecodeError, ValueError) as error:
        output = {"valid": False, "errors": [issue("io.read", "$", str(error))], "warnings": []}
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if output["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
