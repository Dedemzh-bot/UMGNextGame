#!/usr/bin/env python3
"""Shared, dependency-free helpers for NextGame UI requirement contracts."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Iterable


SKILL_ROOT = Path(__file__).resolve().parent.parent
ASSETS_ROOT = SKILL_ROOT / "assets"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(
            handle,
            parse_constant=lambda token: (_ for _ in ()).throw(ValueError(f"Non-finite JSON number is forbidden: {token}")),
        )


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256_bytes(payload)


def compute_request_input_digest(packet: dict[str, Any]) -> str:
    """Hash only immutable input material, excluding envelope identity fields."""

    material = {
        "userRequest": packet.get("userRequest"),
        "sources": packet.get("sources"),
        "targetHints": packet.get("targetHints"),
        "projectRuleRefs": packet.get("projectRuleRefs"),
    }
    return canonical_sha256(material)


def compute_approved_content_sha256(spec: dict[str, Any]) -> str:
    """Hash the complete RequirementSpec except the self-referential approval field."""

    material = copy.deepcopy(spec)
    review = material.get("reviewGate")
    if isinstance(review, dict):
        review.pop("approvedContentSha256", None)
    return canonical_sha256(material)


def collect_canonical_ids(value: Any) -> set[str]:
    """Collect canonical `id` values from a normalized context document."""

    ids: set[str] = set()
    if isinstance(value, dict):
        identifier = value.get("id")
        if isinstance(identifier, str) and not identifier.startswith("local-"):
            ids.add(identifier)
        for child in value.values():
            ids.update(collect_canonical_ids(child))
    elif isinstance(value, list):
        for child in value:
            ids.update(collect_canonical_ids(child))
    return ids


def issue(code: str, path: str, message: str) -> dict[str, str]:
    return {"code": code, "path": path, "message": message}


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def _resolve_ref(root_schema: dict[str, Any], reference: str) -> dict[str, Any] | None:
    if not reference.startswith("#/"):
        return None
    current: Any = root_schema
    for token in reference[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or token not in current:
            return None
        current = current[token]
    return current if isinstance(current, dict) else None


def validate_schema_instance(
    value: Any,
    schema: dict[str, Any],
    *,
    root_schema: dict[str, Any] | None = None,
    path: str = "$",
) -> list[dict[str, str]]:
    """Validate the JSON-Schema subset used by this skill without jsonschema."""

    root_schema = root_schema or schema
    errors: list[dict[str, str]] = []

    reference = schema.get("$ref")
    if isinstance(reference, str):
        resolved = _resolve_ref(root_schema, reference)
        if resolved is None:
            return [issue("schema.ref", path, f"Unresolvable schema reference: {reference}")]
        return validate_schema_instance(value, resolved, root_schema=root_schema, path=path)

    for keyword in ("allOf",):
        branches = schema.get(keyword)
        if isinstance(branches, list):
            for branch in branches:
                if isinstance(branch, dict):
                    errors.extend(validate_schema_instance(value, branch, root_schema=root_schema, path=path))

    if isinstance(schema.get("anyOf"), list):
        alternatives = [
            validate_schema_instance(value, branch, root_schema=root_schema, path=path)
            for branch in schema["anyOf"]
            if isinstance(branch, dict)
        ]
        if alternatives and all(result for result in alternatives):
            errors.append(issue("schema.any_of", path, "Value does not match any allowed shape."))
            return errors

    if isinstance(schema.get("oneOf"), list):
        alternatives = [
            validate_schema_instance(value, branch, root_schema=root_schema, path=path)
            for branch in schema["oneOf"]
            if isinstance(branch, dict)
        ]
        if sum(not result for result in alternatives) != 1:
            errors.append(issue("schema.one_of", path, "Value must match exactly one allowed shape."))
            return errors

    expected = schema.get("type")
    expected_types = [expected] if isinstance(expected, str) else expected
    if isinstance(expected_types, list) and not any(_matches_type(value, item) for item in expected_types):
        errors.append(issue("schema.type", path, f"Expected type {expected_types}, got {type(value).__name__}."))
        return errors

    if "const" in schema and value != schema["const"]:
        errors.append(issue("schema.const", path, f"Value must equal {schema['const']!r}."))
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        errors.append(issue("schema.enum", path, f"Value must be one of {enum!r}."))

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if isinstance(required, list):
            for key in required:
                if key not in value:
                    errors.append(issue("schema.required", path, f"Missing required field: {key}"))
        if schema.get("additionalProperties") is False and isinstance(properties, dict):
            for key in value:
                if key not in properties:
                    errors.append(issue("schema.additional_property", f"{path}.{key}", "Unknown field is not allowed."))
        if isinstance(properties, dict):
            for key, child in value.items():
                child_schema = properties.get(key)
                if isinstance(child_schema, dict):
                    errors.extend(
                        validate_schema_instance(child, child_schema, root_schema=root_schema, path=f"{path}.{key}")
                    )
        minimum_properties = schema.get("minProperties")
        if isinstance(minimum_properties, int) and len(value) < minimum_properties:
            errors.append(issue("schema.min_properties", path, f"Expected at least {minimum_properties} properties."))

    if isinstance(value, list):
        minimum_items = schema.get("minItems")
        maximum_items = schema.get("maxItems")
        if isinstance(minimum_items, int) and len(value) < minimum_items:
            errors.append(issue("schema.min_items", path, f"Expected at least {minimum_items} items."))
        if isinstance(maximum_items, int) and len(value) > maximum_items:
            errors.append(issue("schema.max_items", path, f"Expected at most {maximum_items} items."))
        if schema.get("uniqueItems") is True:
            serialized = [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in value]
            if len(serialized) != len(set(serialized)):
                errors.append(issue("schema.unique_items", path, "Array items must be unique."))
        prefix_items = schema.get("prefixItems")
        if isinstance(prefix_items, list):
            for index, child_schema in enumerate(prefix_items):
                if index < len(value) and isinstance(child_schema, dict):
                    errors.extend(
                        validate_schema_instance(
                            value[index], child_schema, root_schema=root_schema, path=f"{path}[{index}]"
                        )
                    )
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, child in enumerate(value):
                errors.extend(
                    validate_schema_instance(child, item_schema, root_schema=root_schema, path=f"{path}[{index}]")
                )

    if isinstance(value, str):
        minimum_length = schema.get("minLength")
        maximum_length = schema.get("maxLength")
        if isinstance(minimum_length, int) and len(value) < minimum_length:
            errors.append(issue("schema.min_length", path, f"String must contain at least {minimum_length} characters."))
        if isinstance(maximum_length, int) and len(value) > maximum_length:
            errors.append(issue("schema.max_length", path, f"String must contain at most {maximum_length} characters."))
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.fullmatch(pattern, value) is None:
            errors.append(issue("schema.pattern", path, f"String does not match required pattern: {pattern}"))

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):
            errors.append(issue("schema.finite_number", path, "NaN and Infinity are not valid contract numbers."))
            return errors
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        exclusive_minimum = schema.get("exclusiveMinimum")
        exclusive_maximum = schema.get("exclusiveMaximum")
        if isinstance(minimum, (int, float)) and value < minimum:
            errors.append(issue("schema.minimum", path, f"Value must be >= {minimum}."))
        if isinstance(maximum, (int, float)) and value > maximum:
            errors.append(issue("schema.maximum", path, f"Value must be <= {maximum}."))
        if isinstance(exclusive_minimum, (int, float)) and value <= exclusive_minimum:
            errors.append(issue("schema.exclusive_minimum", path, f"Value must be > {exclusive_minimum}."))
        if isinstance(exclusive_maximum, (int, float)) and value >= exclusive_maximum:
            errors.append(issue("schema.exclusive_maximum", path, f"Value must be < {exclusive_maximum}."))

    return errors


def resolve_contract_path(owner_path: Path, raw_path: str) -> Path:
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = owner_path.parent / candidate
    return candidate.resolve()


def find_forbidden_keys(value: Any, forbidden: Iterable[str], path: str = "$") -> list[tuple[str, str]]:
    forbidden_set = set(forbidden)
    matches: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key in forbidden_set:
                matches.append((key, child_path))
            matches.extend(find_forbidden_keys(child, forbidden_set, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            matches.extend(find_forbidden_keys(child, forbidden_set, f"{path}[{index}]"))
    return matches


def result(errors: list[dict[str, str]], warnings: list[dict[str, str]] | None = None) -> dict[str, Any]:
    warnings = warnings or []
    return {"valid": not errors, "errors": errors, "warnings": warnings}
