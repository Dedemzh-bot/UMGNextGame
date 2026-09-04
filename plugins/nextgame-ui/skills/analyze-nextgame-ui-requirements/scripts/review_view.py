#!/usr/bin/env python3
"""Build deterministic, dependency-closed Review Views for pending Requirements.

The view is deliberately not a UIRequirementSpec.  It is a review-only envelope
whose projected ``requirement`` member contains exact copies of selected source
records.  Validation always requires the complete source Draft and rebuilds the
expected view; the projection can therefore never become a second authority.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _contract_common import (  # noqa: E402
    ASSETS_ROOT,
    canonical_sha256,
    issue,
    load_json,
    result,
    sha256_file,
    validate_schema_instance,
)


DEFAULT_REQUIREMENT_SCHEMA = ASSETS_ROOT / "ui-requirement-spec.schema.json"
DEFAULT_VIEW_SCHEMA = ASSETS_ROOT / "review-view.schema.json"

# This digest is a reviewed projection-policy boundary, not merely telemetry.  A
# future Requirement schema must be deliberately audited before projected mode is
# re-enabled.  Until then the safe behavior is an exact full-Draft fallback.
SUPPORTED_REQUIREMENT_SCHEMA_SHA256 = "ab6f0b0cb875046f0a3f03565c7c28a146be6eef89b1e870f6e2b54d46e0c5e8"

VIEW_NOTICE = (
    "Review-only projection; the complete Requirement remains authoritative and is validated after review."
)

ROLE_TO_PROFILE = {
    "state-visual-review": "state-visual-review-v2",
    "schema-feasibility-review": "schema-feasibility-review-v2",
    "coverage-review": "coverage-review-v2",
}
PROFILE_TO_ROLE = {profile: role for role, profile in ROLE_TO_PROFILE.items()}

CANONICAL_ID_PATTERN = re.compile(r"^[a-z][a-z0-9.-]{2,95}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REF_LIKE_KEY_PATTERN = re.compile(r"(?:Id|Ids|Refs)$")


class ReviewViewError(ValueError):
    """Raised when a source cannot safely be treated as a pending Review Draft."""

    def __init__(self, code: str, message: str, *, errors: Sequence[dict[str, str]] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.errors = list(errors or [])


@dataclass(frozen=True)
class Owner:
    """One physically projectable record and every canonical ID it owns."""

    key: str
    kind: str
    path: tuple[str | int, ...]
    record: dict[str, Any]
    identifiers: tuple[tuple[str, str], ...]

    @property
    def primary_id(self) -> str:
        return self.identifiers[0][0]


@dataclass(frozen=True)
class CanonicalBinding:
    canonical_id: str
    canonical_type: str
    owner_key: str


@dataclass(frozen=True)
class ReferenceRule:
    cardinality: str
    expected_types: frozenset[str] | None


@dataclass(frozen=True)
class Reference:
    canonical_id: str
    expected_types: frozenset[str] | None
    path: str


@dataclass
class CanonicalIndex:
    owners: dict[str, Owner]
    bindings: dict[str, CanonicalBinding]
    order: list[str]
    duplicate_ids: list[str]


ANY_TYPE: frozenset[str] | None = None


def _types(*values: str) -> frozenset[str]:
    return frozenset(values)


# Reference fields are registered centrally and are type checked against the
# canonical index.  The additional entries at the end are established opaque
# ``element.properties`` relationships found in retained NextGame contracts.
REFERENCE_RULES: dict[str, ReferenceRule] = {
    "sourceId": ReferenceRule("one", _types("source")),
    "evidenceIds": ReferenceRule("many", _types("evidence")),
    "geometryEvidenceId": ReferenceRule("optional-one", _types("evidence")),
    "boundaryEvidenceIds": ReferenceRule("many", _types("evidence")),
    "sourceScreenEvidenceIds": ReferenceRule("many", _types("evidence")),
    "claimIds": ReferenceRule("many", _types("claim")),
    "claimId": ReferenceRule("one", _types("claim")),
    "affectsClaimIds": ReferenceRule("many", _types("claim")),
    "acceptedClaimIds": ReferenceRule("many", _types("claim")),
    "rejectedClaimIds": ReferenceRule("many", _types("claim")),
    "subjectRefs": ReferenceRule("many", ANY_TYPE),
    "canonicalId": ReferenceRule("one", ANY_TYPE),
    "parentRegionId": ReferenceRule("optional-one", _types("region")),
    "regionId": ReferenceRule("one", _types("region")),
    "coversRegionIds": ReferenceRule("many", _types("region")),
    "memberElementIds": ReferenceRule("many", _types("element")),
    "parentElementId": ReferenceRule("optional-one", _types("element")),
    "elementId": ReferenceRule("one", _types("element")),
    "elementIds": ReferenceRule("many", _types("element")),
    "containerElementId": ReferenceRule("one", _types("element")),
    "completeElementIds": ReferenceRule("many", _types("element")),
    "sharedRootElementId": ReferenceRule("one", _types("element")),
    "panelElementId": ReferenceRule("one", _types("element")),
    "coversElementIds": ReferenceRule("many", _types("element")),
    "outerScrollOwnerElementId": ReferenceRule("one", _types("element")),
    "mergedIntoElementId": ReferenceRule("one", _types("element")),
    "mergedVisualElementIds": ReferenceRule("many", _types("element")),
    "staticDecorationElementIds": ReferenceRule("many", _types("element")),
    "actualHostElementId": ReferenceRule("one", _types("element")),
    "contentRootElementId": ReferenceRule("one", _types("element")),
    "hostLocatorElementId": ReferenceRule("one", _types("element")),
    "labelElementId": ReferenceRule("one", _types("element")),
    "ownerElementId": ReferenceRule("one", _types("element")),
    "representedByElementIds": ReferenceRule("many", _types("element")),
    "sharedFrameElementId": ReferenceRule("one", _types("element")),
    "styleOwnerElementId": ReferenceRule("one", _types("element")),
    "children": ReferenceRule("many", _types("element")),
    "directChild": ReferenceRule("one", _types("element")),
    "orderedChildren": ReferenceRule("many", _types("element")),
    "orderedTextBlocks": ReferenceRule("many", _types("element")),
    "sharedRectLayers": ReferenceRule("many", _types("element")),
    "familyId": ReferenceRule("optional-one", _types("component-family")),
    "entryFamilyId": ReferenceRule("one", _types("component-family")),
    "componentFamilyId": ReferenceRule("one", _types("component-family")),
    "coversCollectionIds": ReferenceRule("many", _types("collection")),
    "representedByCollectionId": ReferenceRule("one", _types("collection")),
    "collectionLocatorFor": ReferenceRule("one", _types("collection")),
    "representedByRuntimeFieldId": ReferenceRule("one", _types("runtime-field")),
    "axisId": ReferenceRule("one", _types("state-axis")),
    "stateAxisId": ReferenceRule("one", _types("state-axis")),
    "targetStateIds": ReferenceRule("many", _types("state")),
    "stateId": ReferenceRule("one", _types("state")),
    "axisStateIds": ReferenceRule("many", _types("state")),
    "representedByStateId": ReferenceRule("one", _types("state")),
    "coversStateModelIds": ReferenceRule("many", _types("state-model")),
    "dependsOnAssetIds": ReferenceRule("many", _types("asset")),
    "entryWidgetClassAssetId": ReferenceRule("one", _types("asset")),
    "ownerIntentId": ReferenceRule("one", _types("responsive-intent")),
    # This key intentionally admits a heterogeneous element/collection sequence.
    "orderedSectionIds": ReferenceRule("many", _types("element", "collection")),
}

# These keys have an ``Id`` suffix but are identity/declaration or local-normalizer
# data, not links into the Requirement canonical graph.
NON_REFERENCE_ID_KEYS = frozenset({"id", "localId", "requestId", "discardedLocalIds"})


FALLBACK_PRIORITY = (
    "unknown-requirement-schema",
    "unknown-field",
    "unknown-reference-shape",
    "duplicate-canonical-id",
    "dangling-reference",
    "reference-type-mismatch",
    "closure-incomplete",
)


def _path_text(path: Iterable[str | int]) -> str:
    text = "$"
    for token in path:
        text += f"[{token}]" if isinstance(token, int) else f".{token}"
    return text


def _resolve_schema(schema: dict[str, Any], root_schema: dict[str, Any]) -> dict[str, Any]:
    reference = schema.get("$ref")
    if not isinstance(reference, str) or not reference.startswith("#/"):
        return schema
    current: Any = root_schema
    for token in reference[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or token not in current:
            return schema
        current = current[token]
    return current if isinstance(current, dict) else schema


def _strip_unknown_fields(
    value: Any,
    schema: dict[str, Any],
    root_schema: dict[str, Any],
    *,
    path: tuple[str | int, ...] = (),
) -> tuple[Any, list[str]]:
    """Remove only closed-schema unknown keys so they can be classified safely."""

    schema = _resolve_schema(schema, root_schema)
    alternatives = schema.get("oneOf") or schema.get("anyOf")
    if isinstance(alternatives, list) and alternatives:
        candidates: list[tuple[int, int, int, Any, list[str]]] = []
        for index, branch in enumerate(alternatives):
            if not isinstance(branch, dict):
                continue
            stripped, unknown = _strip_unknown_fields(value, branch, root_schema, path=path)
            errors = validate_schema_instance(stripped, branch, root_schema=root_schema, path=_path_text(path))
            candidates.append((len(errors), len(unknown), index, stripped, unknown))
        if candidates:
            _, _, _, stripped, unknown = min(candidates, key=lambda item: item[:3])
            # ``oneOf``/``anyOf`` does not replace sibling constraints.  In
            # particular, responsiveIntent combines a branch discriminator with
            # an outer closed object schema.  Process the selected branch first,
            # then the outer schema without its alternatives so an unknown field
            # is classified as a safe full-fallback signal instead of leaking
            # through to a later hard Schema rejection.
            outer_schema = {
                key: copy.deepcopy(child)
                for key, child in schema.items()
                if key not in {"oneOf", "anyOf"}
            }
            if outer_schema:
                stripped, outer_unknown = _strip_unknown_fields(
                    stripped,
                    outer_schema,
                    root_schema,
                    path=path,
                )
                unknown = list(dict.fromkeys([*unknown, *outer_unknown]))
            return stripped, unknown

    if isinstance(value, dict):
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        closed = schema.get("additionalProperties") is False
        stripped_object: dict[str, Any] = {}
        unknown_paths: list[str] = []
        for key, child in value.items():
            child_schema = properties.get(key)
            if closed and child_schema is None:
                unknown_paths.append(_path_text((*path, key)))
                continue
            if isinstance(child_schema, dict):
                clean_child, child_unknown = _strip_unknown_fields(
                    child, child_schema, root_schema, path=(*path, key)
                )
                stripped_object[key] = clean_child
                unknown_paths.extend(child_unknown)
            else:
                stripped_object[key] = copy.deepcopy(child)
        return stripped_object, unknown_paths

    if isinstance(value, list):
        item_schema = schema.get("items")
        if not isinstance(item_schema, dict):
            return copy.deepcopy(value), []
        output: list[Any] = []
        unknown_paths: list[str] = []
        for index, child in enumerate(value):
            clean_child, child_unknown = _strip_unknown_fields(
                child, item_schema, root_schema, path=(*path, index)
            )
            output.append(clean_child)
            unknown_paths.extend(child_unknown)
        return output, unknown_paths

    return copy.deepcopy(value), []


def _dependent_required_errors(
    value: Any,
    schema: dict[str, Any],
    root_schema: dict[str, Any],
    *,
    path: tuple[str | int, ...] = (),
) -> list[dict[str, str]]:
    """Cover the dependentRequired keyword omitted by the local tiny validator."""

    schema = _resolve_schema(schema, root_schema)
    errors: list[dict[str, str]] = []
    alternatives = schema.get("oneOf") or schema.get("anyOf")
    if isinstance(alternatives, list) and alternatives:
        ranked: list[tuple[int, int, dict[str, Any]]] = []
        for index, branch in enumerate(alternatives):
            if isinstance(branch, dict):
                branch_errors = validate_schema_instance(
                    value, branch, root_schema=root_schema, path=_path_text(path)
                )
                ranked.append((len(branch_errors), index, branch))
        if ranked:
            _, _, selected = min(ranked, key=lambda item: item[:2])
            return _dependent_required_errors(value, selected, root_schema, path=path)

    if isinstance(value, dict):
        dependencies = schema.get("dependentRequired")
        if isinstance(dependencies, dict):
            for trigger, required_keys in dependencies.items():
                if trigger not in value or not isinstance(required_keys, list):
                    continue
                for required_key in required_keys:
                    if required_key not in value:
                        errors.append(
                            issue(
                                "schema.dependent_required",
                                _path_text(path),
                                f"Field {trigger!r} requires field {required_key!r}.",
                            )
                        )
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        for key, child in value.items():
            child_schema = properties.get(key)
            if isinstance(child_schema, dict):
                errors.extend(
                    _dependent_required_errors(child, child_schema, root_schema, path=(*path, key))
                )
    elif isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, child in enumerate(value):
            errors.extend(
                _dependent_required_errors(child, schema["items"], root_schema, path=(*path, index))
            )
    return errors


def _load_schema(value: Path | str | Mapping[str, Any]) -> tuple[dict[str, Any], str | None]:
    if isinstance(value, Mapping):
        return copy.deepcopy(dict(value)), None
    path = Path(value)
    return load_json(path), sha256_file(path)


def _validate_source_identity(draft: dict[str, Any], request: dict[str, Any]) -> None:
    if not isinstance(draft, dict) or not isinstance(request, dict):
        raise ReviewViewError("review-view.identity", "Draft and request must both be JSON objects.")
    request_user = request.get("userRequest")
    draft_request = draft.get("request")
    checks = (
        (draft.get("requestId"), request.get("requestId"), "requestId"),
        (draft.get("inputDigest"), request.get("inputDigest"), "inputDigest"),
        (
            draft_request.get("originalText") if isinstance(draft_request, dict) else None,
            request_user.get("originalText") if isinstance(request_user, dict) else None,
            "request.originalText",
        ),
    )
    for actual, expected, label in checks:
        if actual != expected or expected is None:
            raise ReviewViewError(
                "review-view.identity",
                f"Draft {label} does not exactly match the authoritative request packet.",
            )
    gate = draft.get("reviewGate")
    if not isinstance(gate, dict) or gate.get("required") is not True or gate.get("status") != "pending":
        raise ReviewViewError(
            "review-view.pending_required",
            "Review View input must have reviewGate.required=true and reviewGate.status='pending'.",
        )


def _validate_source_schema(
    draft: dict[str, Any], schema: dict[str, Any]
) -> tuple[list[str], list[dict[str, str]]]:
    stripped, unknown_paths = _strip_unknown_fields(draft, schema, schema)
    errors = validate_schema_instance(stripped, schema)
    errors.extend(_dependent_required_errors(stripped, schema, schema))
    if errors:
        raise ReviewViewError(
            "review-view.source_schema",
            "Source Draft has structural or type errors that cannot be made review-safe by full fallback.",
            errors=errors,
        )
    return unknown_paths, []


def _record_identifiers(kind: str, record: dict[str, Any]) -> list[tuple[str, str]]:
    identifier = record.get("id")
    identifiers: list[tuple[str, str]] = []
    if isinstance(identifier, str):
        identifiers.append((identifier, kind))
    if kind != "state-model":
        return identifiers

    for axis in record.get("axes", []) if isinstance(record.get("axes"), list) else []:
        if not isinstance(axis, dict):
            continue
        axis_id = axis.get("id")
        if isinstance(axis_id, str):
            identifiers.append((axis_id, "state-axis"))
        for state in axis.get("states", []) if isinstance(axis.get("states"), list) else []:
            if isinstance(state, dict) and isinstance(state.get("id"), str):
                identifiers.append((state["id"], "state"))
    for control in record.get("controlInputs", []) if isinstance(record.get("controlInputs"), list) else []:
        if isinstance(control, dict) and isinstance(control.get("id"), str):
            identifiers.append((control["id"], "state-control-input"))
    return identifiers


def _iter_owner_records(draft: dict[str, Any]) -> Iterable[tuple[str, str, tuple[str | int, ...], dict[str, Any]]]:
    top_level = (
        ("sources", "source"),
        ("evidence", "evidence"),
        ("claims", "claim"),
    )
    for field, kind in top_level:
        records = draft.get(field)
        if isinstance(records, list):
            for index, record in enumerate(records):
                if isinstance(record, dict):
                    path = (field, index)
                    yield _path_text(path), kind, path, record

    ui_model = draft.get("uiModel")
    ui_sections = (
        ("regions", "region"),
        ("componentFamilies", "component-family"),
        ("elements", "element"),
        ("collections", "collection"),
        ("runtimeFields", "runtime-field"),
        ("responsiveIntent", "responsive-intent"),
    )
    if isinstance(ui_model, dict):
        for field, kind in ui_sections:
            records = ui_model.get(field)
            if isinstance(records, list):
                for index, record in enumerate(records):
                    if isinstance(record, dict):
                        path = ("uiModel", field, index)
                        yield _path_text(path), kind, path, record

    remaining = (
        ("stateModels", "state-model"),
        ("assetPlan", "asset"),
        ("assumptions", "assumption"),
        ("questions", "question"),
        ("acceptanceCriteria", "acceptance-criterion"),
    )
    for field, kind in remaining:
        records = draft.get(field)
        if isinstance(records, list):
            for index, record in enumerate(records):
                if isinstance(record, dict):
                    path = (field, index)
                    yield _path_text(path), kind, path, record


def build_canonical_index(draft: dict[str, Any]) -> CanonicalIndex:
    owners: dict[str, Owner] = {}
    bindings: dict[str, CanonicalBinding] = {}
    order: list[str] = []
    duplicate_ids: list[str] = []
    for key, kind, path, record in _iter_owner_records(draft):
        identifiers = tuple(_record_identifiers(kind, record))
        if not identifiers:
            continue
        owner = Owner(key=key, kind=kind, path=path, record=record, identifiers=identifiers)
        owners[key] = owner
        for canonical_id, canonical_type in identifiers:
            if canonical_id in bindings:
                if canonical_id not in duplicate_ids:
                    duplicate_ids.append(canonical_id)
                continue
            bindings[canonical_id] = CanonicalBinding(canonical_id, canonical_type, key)
            order.append(canonical_id)
    return CanonicalIndex(owners=owners, bindings=bindings, order=order, duplicate_ids=duplicate_ids)


def _parse_reference_values(
    key: str,
    value: Any,
    rule: ReferenceRule,
    path: tuple[str | int, ...],
) -> tuple[list[Reference], list[dict[str, str]]]:
    values: list[Any]
    if rule.cardinality == "many":
        if not isinstance(value, list):
            return [], [
                issue(
                    "review-view.reference_shape",
                    _path_text(path),
                    f"Registered reference field {key!r} must be an array.",
                )
            ]
        values = value
    else:
        if value is None and rule.cardinality == "optional-one":
            return [], []
        if not isinstance(value, str):
            return [], [
                issue(
                    "review-view.reference_shape",
                    _path_text(path),
                    f"Registered reference field {key!r} must be a canonical ID string.",
                )
            ]
        values = [value]

    references: list[Reference] = []
    errors: list[dict[str, str]] = []
    for index, candidate in enumerate(values):
        candidate_path = (*path, index) if rule.cardinality == "many" else path
        if not isinstance(candidate, str) or CANONICAL_ID_PATTERN.fullmatch(candidate) is None:
            errors.append(
                issue(
                    "review-view.reference_shape",
                    _path_text(candidate_path),
                    f"Registered reference field {key!r} contains a non-canonical ID.",
                )
            )
            continue
        references.append(Reference(candidate, rule.expected_types, _path_text(candidate_path)))
    return references, errors


def collect_references(
    value: Any,
    index: CanonicalIndex,
    *,
    path: tuple[str | int, ...] = (),
    opaque: bool = False,
) -> tuple[list[Reference], list[dict[str, str]]]:
    """Collect registered refs and conservatively audit open-schema payloads."""

    references: list[Reference] = []
    errors: list[dict[str, str]] = []
    if isinstance(value, dict):
        is_change = {"elementId", "property", "value"}.issubset(value)
        for key, child in value.items():
            child_path = (*path, key)
            child_opaque = opaque or key == "properties" or (is_change and key == "value")
            rule = REFERENCE_RULES.get(key)
            if rule is not None:
                parsed, parse_errors = _parse_reference_values(key, child, rule, child_path)
                references.extend(parsed)
                errors.extend(parse_errors)
                continue
            if key in NON_REFERENCE_ID_KEYS:
                if opaque:
                    errors.append(
                        issue(
                            "review-view.unknown_reference_shape",
                            _path_text(child_path),
                            f"Identity-like field {key!r} is not registered inside an opaque payload.",
                        )
                    )
                continue
            if REF_LIKE_KEY_PATTERN.search(key):
                errors.append(
                    issue(
                        "review-view.unknown_reference_shape",
                        _path_text(child_path),
                        f"Reference-like field {key!r} is not in the canonical reference registry.",
                    )
                )
                continue
            references_child, errors_child = collect_references(
                child, index, path=child_path, opaque=child_opaque
            )
            references.extend(references_child)
            errors.extend(errors_child)
        return references, errors

    if isinstance(value, list):
        for item_index, child in enumerate(value):
            refs_child, errors_child = collect_references(
                child, index, path=(*path, item_index), opaque=opaque
            )
            references.extend(refs_child)
            errors.extend(errors_child)
        return references, errors

    if opaque and isinstance(value, str) and value in index.bindings:
        errors.append(
            issue(
                "review-view.unknown_reference_shape",
                _path_text(path),
                "Opaque payload contains a canonical ID outside a registered reference field.",
            )
        )
    return references, errors


def _reference_integrity_issues(
    references: Sequence[Reference], index: CanonicalIndex
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    dangling: list[dict[str, str]] = []
    mismatched: list[dict[str, str]] = []
    for reference in references:
        binding = index.bindings.get(reference.canonical_id)
        if binding is None:
            dangling.append(
                issue(
                    "review-view.dangling_reference",
                    reference.path,
                    f"Canonical reference {reference.canonical_id!r} does not resolve in the source Draft.",
                )
            )
            continue
        if reference.expected_types is not None and binding.canonical_type not in reference.expected_types:
            mismatched.append(
                issue(
                    "review-view.reference_type",
                    reference.path,
                    f"Reference {reference.canonical_id!r} resolves to {binding.canonical_type!r}, expected one of "
                    f"{sorted(reference.expected_types)!r}.",
                )
            )
    return dangling, mismatched


def _root_owner_keys(index: CanonicalIndex, profile: str) -> set[str]:
    roots: set[str] = set()
    for owner in index.owners.values():
        if profile == "state-visual-review-v2":
            if owner.kind in {
                "region",
                "state-model",
                "component-family",
                "element",
                "collection",
                "runtime-field",
                "responsive-intent",
                "assumption",
                "question",
                "acceptance-criterion",
            }:
                roots.add(owner.key)
        elif profile == "schema-feasibility-review-v2":
            # This reviewer owns global schema, reference, and buildability
            # feasibility.  It therefore sees every canonical owner; the View
            # still saves the normalization/history payload without weakening
            # the review surface.
            roots.add(owner.key)
        elif profile == "coverage-review-v2":
            if owner.kind not in {"source", "evidence", "claim"}:
                roots.add(owner.key)
    return roots


def _metadata_reference_owner_keys(draft: dict[str, Any], index: CanonicalIndex) -> set[str]:
    """Seed refs from exact, non-canonical metadata retained in every View."""

    owners: set[str] = set()
    for field in ("reviewGate", "reviewResolutions"):
        if field not in draft:
            continue
        references, _ = collect_references(draft[field], index, path=(field,))
        for reference in references:
            binding = index.bindings.get(reference.canonical_id)
            if binding is not None:
                owners.add(binding.owner_key)
    return owners


def _owner_references(owner: Owner, index: CanonicalIndex) -> list[Reference]:
    references, _ = collect_references(owner.record, index, path=owner.path)
    return references


def _coverage_group_companions(index: CanonicalIndex) -> dict[str, set[str]]:
    groups: dict[str, set[str]] = {}
    for owner in index.owners.values():
        if owner.kind != "element":
            continue
        composition = owner.record.get("imageComposition")
        group_key = composition.get("groupKey") if isinstance(composition, dict) else None
        if isinstance(group_key, str):
            groups.setdefault(group_key, set()).add(owner.key)
    return groups


def _fixed_point_closure(
    index: CanonicalIndex,
    root_owner_keys: set[str],
    profile: str,
) -> tuple[set[str], bool]:
    included = set(root_owner_keys)
    groups = _coverage_group_companions(index) if profile == "coverage-review-v2" else {}
    maximum_iterations = max(1, len(index.owners) + 1)
    for _ in range(maximum_iterations):
        before = len(included)
        for owner_key in tuple(included):
            owner = index.owners[owner_key]
            for reference in _owner_references(owner, index):
                binding = index.bindings.get(reference.canonical_id)
                if binding is not None:
                    included.add(binding.owner_key)
            if groups and owner.kind == "element":
                composition = owner.record.get("imageComposition")
                group_key = composition.get("groupKey") if isinstance(composition, dict) else None
                if isinstance(group_key, str):
                    included.update(groups.get(group_key, ()))
        if len(included) == before:
            return included, True
    return included, False


def _filtered_records(
    draft: dict[str, Any], index: CanonicalIndex, included_owner_keys: set[str]
) -> dict[str, Any]:
    """Project exact canonical records while preserving every source array order."""

    projected: dict[str, Any] = {}
    for key in ("version", "revision", "requestId", "inputDigest", "request", "target", "analysisPolicy"):
        if key in draft:
            projected[key] = copy.deepcopy(draft[key])
    if "reviewGate" in draft:
        projected["reviewGate"] = copy.deepcopy(draft["reviewGate"])
    if "reviewResolutions" in draft:
        projected["reviewResolutions"] = copy.deepcopy(draft["reviewResolutions"])

    def select(field_path: tuple[str, ...]) -> list[Any]:
        source: Any = draft
        for token in field_path:
            source = source.get(token) if isinstance(source, dict) else None
        if not isinstance(source, list):
            return []
        output: list[Any] = []
        for item_index, record in enumerate(source):
            owner_key = _path_text((*field_path, item_index))
            if owner_key in included_owner_keys:
                output.append(copy.deepcopy(record))
        return output

    projected["sources"] = select(("sources",))
    projected["evidence"] = select(("evidence",))
    projected["claims"] = select(("claims",))
    projected["uiModel"] = {
        "regions": select(("uiModel", "regions")),
        "componentFamilies": select(("uiModel", "componentFamilies")),
        "elements": select(("uiModel", "elements")),
        "collections": select(("uiModel", "collections")),
        "runtimeFields": select(("uiModel", "runtimeFields")),
        "responsiveIntent": select(("uiModel", "responsiveIntent")),
    }
    projected["stateModels"] = select(("stateModels",))
    projected["assetPlan"] = select(("assetPlan",))
    projected["assumptions"] = select(("assumptions",))
    projected["questions"] = select(("questions",))
    projected["acceptanceCriteria"] = select(("acceptanceCriteria",))
    return projected


def _first_fallback_reason(reasons: set[str]) -> str | None:
    for reason in FALLBACK_PRIORITY:
        if reason in reasons:
            return reason
    return None


def _view_schema_errors(view: dict[str, Any], schema: dict[str, Any]) -> list[dict[str, str]]:
    errors = validate_schema_instance(view, schema)
    role = view.get("agentRole")
    profile = view.get("profile")
    if ROLE_TO_PROFILE.get(role) != profile:
        errors.append(issue("review-view.profile", "$.profile", "Review role/profile pair is inconsistent."))
    mode = view.get("mode")
    fallback = view.get("fallbackReason")
    if mode == "projected" and fallback is not None:
        errors.append(issue("review-view.mode", "$.fallbackReason", "Projected mode requires null fallbackReason."))
    if mode == "full-fallback" and not isinstance(fallback, str):
        errors.append(issue("review-view.mode", "$.fallbackReason", "Full fallback requires a reason."))
    return errors


def build_review_view(
    draft: dict[str, Any],
    request: dict[str, Any],
    role: str,
    *,
    draft_file_sha256: str,
    requirement_schema_path: Path | str = DEFAULT_REQUIREMENT_SCHEMA,
    view_schema_path: Path | str | Mapping[str, Any] = DEFAULT_VIEW_SCHEMA,
) -> tuple[dict[str, Any], str, str | None]:
    """Build one role-specific Review View or an exact full-Draft fallback.

    ``draft_file_sha256`` is supplied by the caller because the source JSON value
    may already have been parsed.  It is a binding only; no source path is emitted.
    """

    if role in PROFILE_TO_ROLE:
        role = PROFILE_TO_ROLE[role]
    if role not in ROLE_TO_PROFILE:
        raise ReviewViewError("review-view.role", f"Unsupported review role/profile: {role!r}")
    if not isinstance(draft_file_sha256, str) or SHA256_PATTERN.fullmatch(draft_file_sha256) is None:
        raise ReviewViewError("review-view.draft_hash", "draft_file_sha256 must be a lowercase SHA-256 digest.")

    _validate_source_identity(draft, request)
    requirement_schema, requirement_schema_sha256 = _load_schema(requirement_schema_path)
    if requirement_schema_sha256 is None:
        raise ReviewViewError(
            "review-view.requirement_schema_path",
            "requirement_schema_path must identify the on-disk Requirement schema so its bytes can be bound.",
        )
    unknown_paths, _ = _validate_source_schema(draft, requirement_schema)

    profile = ROLE_TO_PROFILE[role]
    canonical_index = build_canonical_index(draft)
    references, unknown_reference_issues = collect_references(draft, canonical_index)
    dangling, mismatched = _reference_integrity_issues(references, canonical_index)

    fallback_reasons: set[str] = set()
    if requirement_schema_sha256 != SUPPORTED_REQUIREMENT_SCHEMA_SHA256:
        fallback_reasons.add("unknown-requirement-schema")
    if unknown_paths:
        fallback_reasons.add("unknown-field")
    if unknown_reference_issues:
        fallback_reasons.add("unknown-reference-shape")
    if canonical_index.duplicate_ids:
        fallback_reasons.add("duplicate-canonical-id")
    if dangling:
        fallback_reasons.add("dangling-reference")
    if mismatched:
        fallback_reasons.add("reference-type-mismatch")

    root_owner_keys = _root_owner_keys(canonical_index, profile)
    root_owner_keys.update(_metadata_reference_owner_keys(draft, canonical_index))
    included_owner_keys: set[str]
    stable = True
    if fallback_reasons:
        included_owner_keys = set(canonical_index.owners)
    else:
        included_owner_keys, stable = _fixed_point_closure(canonical_index, root_owner_keys, profile)
        if not stable:
            fallback_reasons.add("closure-incomplete")

    if not fallback_reasons:
        # A second, independent completeness test guards future edits to either the
        # closure or projection code.  Every projected reference must resolve to an
        # ID physically present in the projected View.
        projected = _filtered_records(draft, canonical_index, included_owner_keys)
        projected_index = build_canonical_index(projected)
        projected_refs, projected_unknown = collect_references(projected, projected_index)
        projected_dangling, projected_mismatched = _reference_integrity_issues(projected_refs, projected_index)
        if projected_unknown or projected_dangling or projected_mismatched:
            fallback_reasons.add("closure-incomplete")
    else:
        projected = {}

    fallback_reason = _first_fallback_reason(fallback_reasons)
    mode = "full-fallback" if fallback_reason is not None else "projected"
    if mode == "full-fallback":
        content = copy.deepcopy(draft)
        included_ids = list(canonical_index.order)
        root_ids: list[str] = []
        omitted_count = 0
    else:
        content = projected
        included_ids = [
            canonical_id
            for canonical_id in canonical_index.order
            if canonical_index.bindings[canonical_id].owner_key in included_owner_keys
        ]
        root_ids = [
            canonical_id
            for canonical_id in canonical_index.order
            if canonical_index.bindings[canonical_id].owner_key in root_owner_keys
            and canonical_index.owners[canonical_index.bindings[canonical_id].owner_key].primary_id == canonical_id
        ]
        omitted_count = len(canonical_index.order) - len(included_ids)

    view = {
        "version": "0.1",
        "viewKind": "nextgame-ui-requirement-review-view",
        "notice": VIEW_NOTICE,
        "requestId": draft["requestId"],
        "inputDigest": draft["inputDigest"],
        "agentRole": role,
        "profile": profile,
        "mode": mode,
        "fallbackReason": fallback_reason,
        "bindings": {
            "draftFileSha256": draft_file_sha256,
            "draftCanonicalSha256": canonical_sha256(draft),
            "requirementSchemaId": (
                requirement_schema.get("$id")
                if isinstance(requirement_schema.get("$id"), str) and requirement_schema.get("$id")
                else "unknown"
            ),
            "requirementSchemaSha256": requirement_schema_sha256,
            "viewContentCanonicalSha256": canonical_sha256(content),
        },
        "rootCanonicalIdCount": len(root_ids),
        "includedCanonicalIdCount": len(included_ids),
        "omittedCanonicalIdCount": omitted_count,
        "requirement": content,
    }

    view_schema, _ = _load_schema(view_schema_path)
    view_errors = _view_schema_errors(view, view_schema)
    if view_errors:
        raise ReviewViewError(
            "review-view.internal_schema",
            "Generated Review View failed its own closed schema.",
            errors=view_errors,
        )
    return view, mode, fallback_reason


def validate_review_view(
    view: dict[str, Any],
    *,
    source_draft: dict[str, Any],
    request: dict[str, Any],
    source_draft_file_sha256: str,
    schema: Path | str | Mapping[str, Any] = DEFAULT_VIEW_SCHEMA,
    requirement_schema_path: Path | str = DEFAULT_REQUIREMENT_SCHEMA,
) -> dict[str, Any]:
    """Validate a View by closed schema and exact deterministic reconstruction."""

    view_schema, _ = _load_schema(schema)
    errors = _view_schema_errors(view, view_schema) if isinstance(view, dict) else [
        issue("review-view.type", "$", "Review View must be a JSON object.")
    ]
    if errors:
        return result(errors)

    try:
        expected, expected_mode, expected_reason = build_review_view(
            source_draft,
            request,
            str(view.get("agentRole")),
            draft_file_sha256=source_draft_file_sha256,
            requirement_schema_path=requirement_schema_path,
            view_schema_path=view_schema,
        )
    except ReviewViewError as exc:
        validation_errors = list(exc.errors)
        validation_errors.append(issue(exc.code, "$", str(exc)))
        return result(validation_errors)

    if view != expected:
        errors.append(
            issue(
                "review-view.exact_rebuild",
                "$",
                "Review View does not exactly equal the deterministic rebuild from the complete source Draft.",
            )
        )
    validation = result(errors)
    validation["mode"] = expected_mode
    validation["fallbackReason"] = expected_reason
    validation["viewCanonicalSha256"] = canonical_sha256(view)
    return validation


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _build_cli(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("draft", type=Path)
    parser.add_argument("request", type=Path)
    parser.add_argument("role", choices=tuple(ROLE_TO_PROFILE) + tuple(PROFILE_TO_ROLE))
    parser.add_argument("output", type=Path)
    parser.add_argument("--requirement-schema", type=Path, default=DEFAULT_REQUIREMENT_SCHEMA)
    parser.add_argument("--view-schema", type=Path, default=DEFAULT_VIEW_SCHEMA)
    args = parser.parse_args(argv)
    try:
        view, mode, fallback_reason = build_review_view(
            load_json(args.draft),
            load_json(args.request),
            args.role,
            draft_file_sha256=sha256_file(args.draft),
            requirement_schema_path=args.requirement_schema,
            view_schema_path=args.view_schema,
        )
        _write_json(args.output, view)
    except (OSError, ValueError, ReviewViewError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "valid": True,
                "output": str(args.output.resolve()),
                "mode": mode,
                "fallbackReason": fallback_reason,
                "viewCanonicalSha256": canonical_sha256(view),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_build_cli())
