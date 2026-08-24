#!/usr/bin/env python3
"""Validate a UIProgramHandoff and its accepted post-build source projection."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from _document_contract_common import (
    BUILD_ACCEPTANCE_SCHEMA,
    DOCX_NAME_PATTERN,
    FORBIDDEN_HANDOFF_KEYS,
    HANDOFF_SCHEMA,
    canonical_sha256,
    find_forbidden_keys,
    issue,
    load_json,
    parse_iso8601,
    result,
    validate_schema_instance,
)
from prepare_program_handoff import build_program_handoff
from validate_build_acceptance import validate_acceptance_handoff_binding, validate_build_acceptance


def validate_program_handoff(
    handoff: Any,
    schema: dict[str, Any],
    *,
    handoff_path: Path,
    requirement: Any,
    requirement_path: Path,
    bundle: Any,
    bundle_path: Path,
    readback: Any,
    readback_path: Path,
    build_acceptance: Any,
    build_acceptance_path: Path,
) -> dict[str, Any]:
    errors = validate_schema_instance(handoff, schema)
    warnings: list[dict[str, str]] = []
    for key, path in find_forbidden_keys(handoff, FORBIDDEN_HANDOFF_KEYS):
        errors.append(issue("content.forbidden_field", path, f"Forbidden program-contract field: {key}"))
    if not isinstance(handoff, dict):
        return result(errors, warnings)
    parse_iso8601(handoff.get("generatedAt"), "$.generatedAt", errors)

    acceptance_report = validate_build_acceptance(
        build_acceptance,
        load_json(BUILD_ACCEPTANCE_SCHEMA),
        acceptance_path=build_acceptance_path,
        requirement=requirement,
        requirement_path=requirement_path,
        bundle=bundle,
        bundle_path=bundle_path,
        readback=readback,
        readback_path=readback_path,
    )
    if not acceptance_report["valid"]:
        errors.append(issue("sources.invalid", "$.sources", "One or more bound inputs or the post-build user acceptance fail the documentation gate."))
        errors.extend(acceptance_report["errors"])
    errors.extend(validate_acceptance_handoff_binding(build_acceptance, build_acceptance_path, handoff)["errors"])

    output = handoff.get("output") if isinstance(handoff.get("output"), dict) else {}
    match = DOCX_NAME_PATTERN.fullmatch(output.get("fileName", ""))
    if match is not None and isinstance(requirement, dict):
        try:
            expected = build_program_handoff(
                requirement,
                bundle,
                readback,
                build_acceptance,
                requirement_path=requirement_path,
                bundle_path=bundle_path,
                readback_path=readback_path,
                build_acceptance_path=build_acceptance_path,
                output_date=match.group(1),
                generated_at=handoff["generatedAt"],
            )
            if canonical_sha256(handoff) != canonical_sha256(expected):
                errors.append(issue("projection.mismatch", "$", "UIProgramHandoff is not the exact projection of its bound Requirement, Bundle, Unreal readback, and build acceptance."))
        except (OSError, ValueError, KeyError, TypeError) as error:
            errors.append(issue("projection.failed", "$", str(error)))
    return result(errors, warnings)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("handoff", type=Path)
    parser.add_argument("--requirement", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--readback", type=Path, required=True)
    parser.add_argument("--build-acceptance", type=Path, required=True)
    parser.add_argument("--schema", type=Path, default=HANDOFF_SCHEMA)
    args = parser.parse_args()
    try:
        output = validate_program_handoff(
            load_json(args.handoff),
            load_json(args.schema),
            handoff_path=args.handoff.resolve(),
            requirement=load_json(args.requirement),
            requirement_path=args.requirement.resolve(),
            bundle=load_json(args.bundle),
            bundle_path=args.bundle.resolve(),
            readback=load_json(args.readback),
            readback_path=args.readback.resolve(),
            build_acceptance=load_json(args.build_acceptance),
            build_acceptance_path=args.build_acceptance.resolve(),
        )
    except (OSError, json.JSONDecodeError, ValueError) as error:
        output = result([issue("io.read", "$", str(error))])
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if output["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
