#!/usr/bin/env python3
"""Validate the direct-user post-build acceptance that authorizes documentation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from _document_contract_common import (
    BUILD_ACCEPTANCE_SCHEMA,
    READBACK_SCHEMA,
    issue,
    load_json,
    parse_aware_iso8601,
    result,
    sha256_file,
    validate_schema_instance,
)
from validate_unreal_widget_readback import validate_unreal_widget_readback


def _expected_requirement_binding(requirement: dict[str, Any], requirement_path: Path) -> dict[str, Any]:
    review = requirement.get("reviewGate") if isinstance(requirement.get("reviewGate"), dict) else {}
    return {
        "requestId": requirement.get("requestId"),
        "revision": requirement.get("revision"),
        "approvedContentSha256": review.get("approvedContentSha256"),
        "sha256": sha256_file(requirement_path),
    }


def _expected_bundle_binding(bundle: dict[str, Any], bundle_path: Path) -> dict[str, Any]:
    return {"bundleId": bundle.get("bundleId"), "sha256": sha256_file(bundle_path)}


def _expected_readback_binding(readback: dict[str, Any], readback_path: Path) -> dict[str, Any]:
    return {"readbackId": readback.get("readbackId"), "sha256": sha256_file(readback_path)}


def _asset_pairs_from_bundle(bundle: dict[str, Any]) -> list[tuple[Any, Any]]:
    return [
        (asset.get("id"), asset.get("assetPath"))
        for asset in bundle.get("assets", [])
        if isinstance(asset, dict)
    ]


def validate_build_acceptance(
    acceptance: Any,
    schema: dict[str, Any],
    *,
    acceptance_path: Path,
    requirement: Any,
    requirement_path: Path,
    bundle: Any,
    bundle_path: Path,
    readback: Any,
    readback_path: Path,
) -> dict[str, Any]:
    """Validate acceptance against the exact current files and their complete asset set.

    The repository can verify the declared actor/source fields, timestamps, identities,
    hashes, and coverage. It cannot cryptographically authenticate the originating chat
    message. Workflow policy therefore permits the primary coordinator to create an
    accepted artifact only after a direct user message received after build readback.
    """

    errors = validate_schema_instance(acceptance, schema)
    warnings: list[dict[str, str]] = []

    readback_report = validate_unreal_widget_readback(
        readback,
        load_json(READBACK_SCHEMA),
        readback_path=readback_path,
        requirement=requirement,
        requirement_path=requirement_path,
        bundle=bundle,
        bundle_path=bundle_path,
    )
    if not readback_report["valid"]:
        errors.append(issue("sources.invalid", "$", "Build acceptance requires a valid current Requirement, Bundle, and Unreal readback."))
        errors.extend(readback_report["errors"])

    if not all(isinstance(value, dict) for value in (acceptance, requirement, bundle, readback)):
        return result(errors, warnings)

    if acceptance.get("status") != "accepted":
        errors.append(issue("acceptance.not_accepted", "$.status", "Documentation requires explicit post-build status 'accepted'."))
    reviewer = acceptance.get("reviewer") if isinstance(acceptance.get("reviewer"), dict) else {}
    if reviewer != {"actorType": "user", "confirmationSource": "direct-user-message"}:
        errors.append(issue("acceptance.not_direct_user", "$.reviewer", "Only a direct user message after build review can authorize documentation."))

    reviewed_at = parse_aware_iso8601(acceptance.get("reviewedAt"), "$.reviewedAt", errors)
    captured_at = parse_aware_iso8601(readback.get("capturedAt"), "$.readback.capturedAt", errors)
    if reviewed_at is not None and captured_at is not None and reviewed_at < captured_at:
        errors.append(issue("time.acceptance_before_readback", "$.reviewedAt", "Build acceptance must not precede the bound Unreal readback."))

    expected_requirement = _expected_requirement_binding(requirement, requirement_path)
    if acceptance.get("requirementBinding") != expected_requirement:
        errors.append(issue("binding.requirement", "$.requirementBinding", "Acceptance Requirement binding does not match the actual current Requirement file."))
    expected_bundle = _expected_bundle_binding(bundle, bundle_path)
    if acceptance.get("bundleBinding") != expected_bundle:
        errors.append(issue("binding.bundle", "$.bundleBinding", "Acceptance Bundle binding does not match the actual current Bundle file."))
    expected_readback = _expected_readback_binding(readback, readback_path)
    if acceptance.get("readbackBinding") != expected_readback:
        errors.append(issue("binding.readback", "$.readbackBinding", "Acceptance readback binding does not match the actual current Unreal readback file."))

    expected_pairs = _asset_pairs_from_bundle(bundle)
    reviewed_ids = acceptance.get("reviewedAssetIds") if isinstance(acceptance.get("reviewedAssetIds"), list) else []
    reviewed_paths = acceptance.get("reviewedAssetPaths") if isinstance(acceptance.get("reviewedAssetPaths"), list) else []
    reviewed_pairs = list(zip(reviewed_ids, reviewed_paths)) if len(reviewed_ids) == len(reviewed_paths) else []
    if len(reviewed_ids) != len(reviewed_paths) or set(reviewed_pairs) != set(expected_pairs) or len(reviewed_pairs) != len(expected_pairs):
        errors.append(issue("coverage.assets", "$.reviewedAssetIds", "Reviewed asset IDs and paths must pairwise and exactly cover every Bundle asset, with no extras."))

    return result(errors, warnings)


def validate_acceptance_handoff_binding(
    acceptance: Any,
    acceptance_path: Path,
    handoff: Any,
) -> dict[str, Any]:
    """Prevent document-content generation from bypassing the acceptance artifact."""

    errors = validate_schema_instance(acceptance, load_json(BUILD_ACCEPTANCE_SCHEMA))
    if not isinstance(acceptance, dict) or not isinstance(handoff, dict):
        return result(errors)
    if acceptance.get("status") != "accepted":
        errors.append(issue("acceptance.not_accepted", "$.status", "Document content requires accepted post-build user review."))
    reviewer = acceptance.get("reviewer") if isinstance(acceptance.get("reviewer"), dict) else {}
    if reviewer != {"actorType": "user", "confirmationSource": "direct-user-message"}:
        errors.append(issue("acceptance.not_direct_user", "$.reviewer", "Document content requires direct-user post-build confirmation."))

    sources = handoff.get("sources") if isinstance(handoff.get("sources"), dict) else {}
    expected_acceptance_source = {
        "acceptanceId": acceptance.get("acceptanceId"),
        "sha256": sha256_file(acceptance_path),
    }
    if sources.get("buildAcceptance") != expected_acceptance_source:
        errors.append(issue("binding.acceptance", "$.sources.buildAcceptance", "Handoff is not bound to this exact build-acceptance file."))
    if acceptance.get("requirementBinding") != sources.get("requirement"):
        errors.append(issue("binding.requirement", "$.requirementBinding", "Acceptance Requirement binding differs from the handoff source."))
    if acceptance.get("bundleBinding") != sources.get("bundle"):
        errors.append(issue("binding.bundle", "$.bundleBinding", "Acceptance Bundle binding differs from the handoff source."))
    if acceptance.get("readbackBinding") != sources.get("unrealReadback"):
        errors.append(issue("binding.readback", "$.readbackBinding", "Acceptance readback binding differs from the handoff source."))

    asset_pairs = [
        (asset.get("assetId"), asset.get("assetPath"))
        for asset in handoff.get("assets", [])
        if isinstance(asset, dict)
    ]
    ids = acceptance.get("reviewedAssetIds") if isinstance(acceptance.get("reviewedAssetIds"), list) else []
    paths = acceptance.get("reviewedAssetPaths") if isinstance(acceptance.get("reviewedAssetPaths"), list) else []
    reviewed_pairs = list(zip(ids, paths)) if len(ids) == len(paths) else []
    if len(ids) != len(paths) or set(reviewed_pairs) != set(asset_pairs) or len(reviewed_pairs) != len(asset_pairs):
        errors.append(issue("coverage.assets", "$.reviewedAssetIds", "Acceptance must exactly cover the handoff asset identities and paths."))

    reviewed_at = parse_aware_iso8601(acceptance.get("reviewedAt"), "$.reviewedAt", errors)
    generated_at = parse_aware_iso8601(handoff.get("generatedAt"), "$.handoff.generatedAt", errors)
    if reviewed_at is not None and generated_at is not None and generated_at < reviewed_at:
        errors.append(issue("time.handoff_before_acceptance", "$.handoff.generatedAt", "Program handoff must be generated after post-build acceptance."))
    return result(errors)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("acceptance", type=Path)
    parser.add_argument("--requirement", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--readback", type=Path, required=True)
    parser.add_argument("--schema", type=Path, default=BUILD_ACCEPTANCE_SCHEMA)
    args = parser.parse_args()
    try:
        output = validate_build_acceptance(
            load_json(args.acceptance),
            load_json(args.schema),
            acceptance_path=args.acceptance.resolve(),
            requirement=load_json(args.requirement),
            requirement_path=args.requirement.resolve(),
            bundle=load_json(args.bundle),
            bundle_path=args.bundle.resolve(),
            readback=load_json(args.readback),
            readback_path=args.readback.resolve(),
        )
    except (OSError, json.JSONDecodeError, ValueError) as error:
        output = result([issue("io.read", "$", str(error))])
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if output["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
