#!/usr/bin/env python3
"""Emit the exact safe content contract that the NextGame program DOCX must contain."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from _document_contract_common import (
    BUILD_ACCEPTANCE_SCHEMA,
    HANDOFF_SCHEMA,
    load_json,
    sha256_file,
    validate_schema_instance,
    write_json,
)
from validate_build_acceptance import validate_acceptance_handoff_binding, validate_build_acceptance
from validate_program_docx import expected_coverage


def build_document_content_contract(
    handoff: dict[str, Any],
    handoff_path: Path,
    build_acceptance: dict[str, Any],
    build_acceptance_path: Path,
    requirement: dict[str, Any],
    requirement_path: Path,
    bundle: dict[str, Any],
    bundle_path: Path,
    readback: dict[str, Any],
    readback_path: Path,
) -> dict[str, Any]:
    errors = validate_schema_instance(handoff, load_json(HANDOFF_SCHEMA))
    errors.extend(
        validate_build_acceptance(
            build_acceptance,
            load_json(BUILD_ACCEPTANCE_SCHEMA),
            acceptance_path=build_acceptance_path,
            requirement=requirement,
            requirement_path=requirement_path,
            bundle=bundle,
            bundle_path=bundle_path,
            readback=readback,
            readback_path=readback_path,
        )["errors"]
    )
    acceptance_report = validate_acceptance_handoff_binding(
        build_acceptance,
        build_acceptance_path,
        handoff,
    )
    errors.extend(acceptance_report["errors"])
    if errors:
        raise ValueError("Document content requires the exact current three sources and accepted post-build user-review artifact.")
    coverage = expected_coverage(handoff)
    statements = coverage.pop("semanticRelationshipStatements")
    return {
        "version": "0.2",
        "handoff": {
            "fileName": handoff_path.name,
            "sha256": sha256_file(handoff_path),
        },
        "requiredIdentifiers": coverage,
        "requiredSemanticRelationshipStatements": statements,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff", type=Path, required=True)
    parser.add_argument("--build-acceptance", type=Path, required=True)
    parser.add_argument("--requirement", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--readback", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    handoff_path = args.handoff.resolve()
    handoff = load_json(handoff_path)
    errors = validate_schema_instance(handoff, load_json(HANDOFF_SCHEMA))
    acceptance_path = args.build_acceptance.resolve()
    acceptance = load_json(acceptance_path)
    requirement_path = args.requirement.resolve()
    requirement = load_json(requirement_path)
    bundle_path = args.bundle.resolve()
    bundle = load_json(bundle_path)
    readback_path = args.readback.resolve()
    readback = load_json(readback_path)
    try:
        contract = build_document_content_contract(
            handoff,
            handoff_path,
            acceptance,
            acceptance_path,
            requirement,
            requirement_path,
            bundle,
            bundle_path,
            readback,
            readback_path,
        )
    except ValueError as error:
        print(json.dumps({"valid": False, "errors": [{"code": "sources.invalid", "path": "$", "message": str(error)}]}, ensure_ascii=False, indent=2))
        return 1
    write_json(args.output, contract)
    print(
        json.dumps(
            {
                "valid": True,
                "output": str(args.output),
                "semanticRelationshipCount": len(contract["requiredSemanticRelationshipStatements"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
