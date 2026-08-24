#!/usr/bin/env python3
"""Validate a NextGame UI RequestPacket 0.1 without third-party packages."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from _contract_common import (
    ASSETS_ROOT,
    compute_request_input_digest,
    issue,
    load_json,
    resolve_contract_path,
    result,
    sha256_file,
    validate_schema_instance,
)


DEFAULT_SCHEMA = ASSETS_ROOT / "request-packet.schema.json"


def validate_request_packet(
    packet: Any,
    schema: dict[str, Any],
    *,
    packet_path: Path | None = None,
) -> dict[str, Any]:
    errors = validate_schema_instance(packet, schema)
    warnings: list[dict[str, str]] = []
    if not isinstance(packet, dict):
        return result(errors, warnings)

    sources = packet.get("sources")
    if isinstance(sources, list):
        source_keys: set[str] = set()
        for index, source in enumerate(sources):
            if not isinstance(source, dict):
                continue
            source_key = source.get("sourceKey")
            if isinstance(source_key, str):
                if source_key in source_keys:
                    errors.append(issue("source.duplicate", f"$.sources[{index}].sourceKey", "sourceKey must be unique."))
                source_keys.add(source_key)
            kind = source.get("kind")
            locator_kind = source.get("locatorKind")
            if kind == "image":
                if not isinstance(source.get("path"), str):
                    errors.append(issue("source.image_path", f"$.sources[{index}].path", "Image sources require path."))
                if not isinstance(source.get("imageSize"), list):
                    errors.append(issue("source.image_size", f"$.sources[{index}].imageSize", "Image sources require imageSize."))
            if kind == "user-text" and not isinstance(source.get("content"), str):
                errors.append(issue("source.text_content", f"$.sources[{index}].content", "user-text sources require content."))
            if kind in {"project-asset", "project-rule"} and not isinstance(source.get("path"), str):
                errors.append(issue("source.path", f"$.sources[{index}].path", f"{kind} sources require path."))
            if locator_kind in {"local-file", "unreal-object"} and not isinstance(source.get("contentSha256"), str):
                errors.append(
                    issue(
                        "source.content_digest",
                        f"$.sources[{index}].contentSha256",
                        f"{locator_kind} sources require contentSha256 so changed source content invalidates old findings.",
                    )
                )
            if locator_kind == "inline" and not isinstance(source.get("content"), str):
                errors.append(issue("source.inline_content", f"$.sources[{index}].content", "Inline sources require content."))
            digest_path: str | None = None
            if locator_kind == "local-file":
                if not isinstance(source.get("path"), str):
                    errors.append(issue("source.local_path", f"$.sources[{index}].path", "local-file sources require path."))
                elif not Path(source["path"]).is_absolute():
                    errors.append(issue("source.local_absolute", f"$.sources[{index}].path", "local-file source paths must be absolute."))
                else:
                    digest_path = source["path"]
            elif locator_kind == "unreal-object":
                if not isinstance(source.get("path"), str) or not source["path"].startswith("/Game/"):
                    errors.append(issue("source.unreal_path", f"$.sources[{index}].path", "unreal-object sources require a /Game/... object path."))
                if not isinstance(source.get("snapshotPath"), str):
                    errors.append(issue("source.snapshot_path", f"$.sources[{index}].snapshotPath", "unreal-object sources require a readback snapshotPath."))
                else:
                    snapshot = Path(source["snapshotPath"])
                    if snapshot.is_absolute() or ".." in snapshot.parts:
                        errors.append(issue("source.snapshot_scope", f"$.sources[{index}].snapshotPath", "snapshotPath must be relative to the RequestPacket directory and cannot contain '..'."))
                    else:
                        digest_path = source["snapshotPath"]
            if packet_path is not None and digest_path is not None:
                source_path = resolve_contract_path(packet_path, digest_path)
                if not source_path.is_file():
                    errors.append(issue("source.file_missing", f"$.sources[{index}]", f"Source or snapshot file does not exist: {source_path}"))
                else:
                    actual_source_hash = sha256_file(source_path)
                    if source.get("contentSha256") != actual_source_hash:
                        errors.append(
                            issue(
                                "source.file_digest",
                                f"$.sources[{index}].contentSha256",
                                f"Source content hash mismatch; expected {actual_source_hash}.",
                            )
                        )

    project_rule_paths = {
        source.get("path")
        for source in packet.get("sources", [])
        if isinstance(source, dict)
        and source.get("kind") == "project-rule"
        and isinstance(source.get("path"), str)
        and isinstance(source.get("contentSha256"), str)
    }
    for index, rule_ref in enumerate(packet.get("projectRuleRefs", [])):
        if isinstance(rule_ref, dict) and rule_ref.get("path") not in project_rule_paths:
            errors.append(
                issue(
                    "rule.source_ref",
                    f"$.projectRuleRefs[{index}].path",
                    "Every projectRuleRef must correspond to a hashed project-rule source.",
                )
            )

    user_request = packet.get("userRequest") if isinstance(packet.get("userRequest"), dict) else {}
    original_text = user_request.get("originalText") if isinstance(user_request.get("originalText"), list) else []
    mirrored_text = [
        source.get("content")
        for source in packet.get("sources", [])
        if isinstance(source, dict) and source.get("kind") == "user-text"
    ]
    if mirrored_text != original_text:
        errors.append(
            issue(
                "request.text_sources",
                "$.sources",
                "Each originalText entry must be mirrored in order by one user-text source.",
            )
        )

    digest = packet.get("inputDigest")
    if isinstance(digest, str):
        try:
            expected = compute_request_input_digest(packet)
        except (TypeError, ValueError) as error:
            expected = None
            errors.append(issue("request.input_digest_input", "$", f"Request input is not canonically serializable: {error}"))
        if expected is not None and digest != expected:
            errors.append(
                issue(
                    "request.input_digest",
                    "$.inputDigest",
                    f"inputDigest does not match canonical input material; expected {expected}.",
                )
            )

    hints = packet.get("targetHints")
    if isinstance(hints, dict):
        if hints.get("assetKind") == "screen" and hints.get("designCanvas") != [2560, 1440]:
            errors.append(
                issue(
                    "target.screen_resolution",
                    "$.targetHints.designCanvas",
                    "NextGame complete screens must use [2560, 1440].",
                )
            )
        if hints.get("mode") == "production" and hints.get("productionAuthorized") is not True:
            errors.append(
                issue(
                    "target.production_authorization",
                    "$.targetHints.productionAuthorized",
                    "Production mode requires explicit authorization.",
                )
            )

    return result(errors, warnings)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packet", type=Path, help="Path to RequestPacket JSON.")
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    args = parser.parse_args()
    try:
        output = validate_request_packet(
            load_json(args.packet),
            load_json(args.schema),
            packet_path=args.packet.resolve(),
        )
    except (OSError, json.JSONDecodeError, ValueError) as error:
        output = result([issue("io.read", "$", str(error))])
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if output["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
