#!/usr/bin/env python3
"""Build and validate deterministic execution Views of accepted Requirements.

An Accepted Build View is deliberately not a ``UIRequirementSpec``.  It is a
dependency-closed projection for build planning plus an explicit scope ledger.
Every validation rebuilds it from the complete accepted Requirement, whose
physical bytes and canonical value remain the sole authority.
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
    sha256_bytes,
    validate_schema_instance,
)
from review_view import (  # noqa: E402
    SUPPORTED_REQUIREMENT_SCHEMA_SHA256,
    CanonicalIndex,
    Owner,
    Reference,
    build_canonical_index,
    collect_references,
)
from validate_requirement_spec import validate_requirement_spec  # noqa: E402


DEFAULT_REQUIREMENT_SCHEMA = ASSETS_ROOT / "ui-requirement-spec.schema.json"
DEFAULT_VIEW_SCHEMA = ASSETS_ROOT / "accepted-build-view.schema.json"

VIEW_NOTICE = (
    "Build-planning projection only; the complete accepted Requirement remains authoritative for every validator."
)
BUILD_PLANNING_DISPATCH_CONTRACT = {
    "role": "build-planning",
    "objective": (
        "Create the accepted UILayoutSpec set and staged UIBuildBundle without "
        "connecting to or mutating Unreal Editor."
    ),
    "inputContract": {
        "visibleArtifact": "self",
        "viewKind": "nextgame-ui-accepted-build-view",
        "completeRequirementVisible": False,
    },
    "outputContract": {
        "bundlePath": "ui-build-bundle.planned.json",
        "layoutPathPrefix": "layouts/",
        "layoutPathSelector": "assets[].layoutSpecPath",
        "layoutDigestSelector": "assets[].layoutSpecSha256",
        "exclusive": True,
    },
    "forbiddenActions": [
        "read-complete-requirement",
        "inherit-conversation-history",
        "connect-unreal-editor",
        "mutate-unreal-assets",
        "write-outside-declared-outputs",
    ],
    "completionContract": {
        "requiredMode": "projected",
        "requireBuildAllowed": True,
        "requireCompleteCoverage": True,
        "receiptAfterFilesWritten": True,
        "failurePolicy": "stop-without-editor-mutation",
    },
}
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

FALLBACK_PRIORITY = (
    "unknown-requirement-schema",
    "unknown-reference-shape",
    "duplicate-canonical-id",
    "dangling-reference",
    "reference-type-mismatch",
    "scope-classification-incomplete",
    "closure-incomplete",
    "projection-coverage-incomplete",
)


class AcceptedBuildViewError(ValueError):
    """Raised when no safe View can be derived from the source Requirement."""

    def __init__(self, code: str, message: str, *, errors: Sequence[dict[str, str]] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.errors = list(errors or [])


def _load_json_bytes(payload: bytes) -> Any:
    """Parse the exact UTF-8 bytes whose physical digest is being bound."""

    return json.loads(
        payload.decode("utf-8"),
        parse_constant=lambda token: (_ for _ in ()).throw(
            ValueError(f"Non-finite JSON number is forbidden: {token}")
        ),
    )


@dataclass(frozen=True)
class RequirementSnapshot:
    """Immutable bytes snapshot used to parse and hash one Requirement atomically."""

    raw_bytes: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.raw_bytes, bytes):
            raise TypeError("RequirementSnapshot.raw_bytes must be bytes.")
        object.__setattr__(self, "raw_bytes", bytes(self.raw_bytes))
        # Fail while the snapshot is created, not later after an unrelated file
        # could have changed.  Parsing again is deterministic and never reopens a
        # path.
        _load_json_bytes(self.raw_bytes)

    @classmethod
    def from_path(cls, path: Path | str) -> "RequirementSnapshot":
        return cls(Path(path).read_bytes())

    @property
    def file_sha256(self) -> str:
        return sha256_bytes(self.raw_bytes)

    def load(self) -> Any:
        return _load_json_bytes(self.raw_bytes)


def _coerce_requirement_snapshot(value: RequirementSnapshot | Path | str | bytes) -> RequirementSnapshot:
    if type(value) is RequirementSnapshot:
        # `frozen=True` blocks ordinary assignment but Python callers can still
        # use `object.__setattr__`.  Normalize into a fresh base instance and a
        # plain bytes object so neither a mutated snapshot nor a bytes subclass
        # can make parsing observe different content from hashing.
        return RequirementSnapshot(bytes(value.raw_bytes))
    if isinstance(value, RequirementSnapshot):
        raise AcceptedBuildViewError(
            "accepted-build-view.unsafe_source_binding",
            "RequirementSnapshot subclasses are forbidden at the authority boundary.",
        )
    if isinstance(value, bytes):
        return RequirementSnapshot(value)
    if isinstance(value, (Path, str)):
        return RequirementSnapshot.from_path(value)
    raise AcceptedBuildViewError(
        "accepted-build-view.unsafe_source_binding",
        "Accepted Build View requires a Requirement path, exact bytes, or RequirementSnapshot; "
        "an in-memory JSON value plus a caller-supplied file digest cannot prove physical-file identity.",
    )


def _load_schema(
    value: Path | str | Mapping[str, Any] | RequirementSnapshot,
) -> tuple[dict[str, Any], str | None]:
    if type(value) is dict:
        return copy.deepcopy(dict(value)), None
    if isinstance(value, Mapping):
        raise AcceptedBuildViewError(
            "accepted-build-view.unsafe_schema_binding",
            "Schema Mapping subclasses are forbidden at the authority boundary.",
        )
    if type(value) is RequirementSnapshot:
        payload = bytes(value.raw_bytes)
        source_label = "immutable JSON snapshot"
    elif isinstance(value, RequirementSnapshot):
        raise AcceptedBuildViewError(
            "accepted-build-view.unsafe_schema_binding",
            "RequirementSnapshot subclasses are forbidden at the Schema authority boundary.",
        )
    else:
        path = Path(value)
        payload = path.read_bytes()
        source_label = str(path)
    schema = _load_json_bytes(payload)
    if not isinstance(schema, dict):
        raise ValueError(f"Schema must be a JSON object: {source_label}")
    return schema, sha256_bytes(payload)


def _validate_source_requirement(requirement: Any, schema: dict[str, Any]) -> None:
    validation = validate_requirement_spec(requirement, schema)
    if not validation.get("valid"):
        raise AcceptedBuildViewError(
            "accepted-build-view.source_requirement",
            "The complete Requirement failed UIRequirementSpec validation.",
            errors=validation.get("errors", []),
        )
    if not isinstance(requirement, dict):
        raise AcceptedBuildViewError(
            "accepted-build-view.source_type",
            "The complete Requirement must be a JSON object.",
        )
    review = requirement.get("reviewGate")
    if not isinstance(review, dict) or review.get("status") != "accepted":
        raise AcceptedBuildViewError(
            "accepted-build-view.accepted_required",
            "Accepted Build View input must have reviewGate.status='accepted'.",
        )
    approved = review.get("approvedContentSha256")
    if not isinstance(approved, str) or SHA256_PATTERN.fullmatch(approved) is None:
        # The Requirement validator normally catches this first.  Keep the
        # boundary explicit so this function never emits an unbound View.
        raise AcceptedBuildViewError(
            "accepted-build-view.approval_required",
            "Accepted Build View input requires a valid approvedContentSha256 binding.",
        )


def _reference_integrity_issues(
    references: Sequence[Reference], index: CanonicalIndex
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Type-check the shared registry's references against the shared index."""

    dangling: list[dict[str, str]] = []
    mismatched: list[dict[str, str]] = []
    for reference in references:
        binding = index.bindings.get(reference.canonical_id)
        if binding is None:
            dangling.append(
                issue(
                    "accepted-build-view.dangling_reference",
                    reference.path,
                    f"Canonical reference {reference.canonical_id!r} does not resolve in the complete Requirement.",
                )
            )
        elif reference.expected_types is not None and binding.canonical_type not in reference.expected_types:
            mismatched.append(
                issue(
                    "accepted-build-view.reference_type",
                    reference.path,
                    f"Reference {reference.canonical_id!r} resolves to {binding.canonical_type!r}, expected one of "
                    f"{sorted(reference.expected_types)!r}.",
                )
            )
    return dangling, mismatched


def _iter_nested_states(requirement: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for model in requirement.get("stateModels", []):
        if not isinstance(model, dict):
            continue
        for axis in model.get("axes", []):
            if not isinstance(axis, dict):
                continue
            for state in axis.get("states", []):
                if isinstance(state, dict):
                    yield state


def _scope_partition(records: Iterable[dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    build_projected: list[str] = []
    explicit_non_build: list[dict[str, str]] = []
    incomplete: list[str] = []
    for record in records:
        identifier = record.get("id")
        if not isinstance(identifier, str):
            continue
        in_scope = record.get("inBuildScope")
        if in_scope is True:
            build_projected.append(identifier)
        elif in_scope is False and isinstance(record.get("scopedOutReason"), str) and record["scopedOutReason"]:
            explicit_non_build.append(
                {"canonicalId": identifier, "reason": record["scopedOutReason"]}
            )
        else:
            incomplete.append(identifier)
    return {
        "buildProjected": build_projected,
        "explicitNonBuild": explicit_non_build,
    }, incomplete


def _coverage_ledger(requirement: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    claims = [item for item in requirement.get("claims", []) if isinstance(item, dict)]
    accepted_claim_ids = [
        item["id"]
        for item in claims
        if item.get("status") == "accepted" and isinstance(item.get("id"), str)
    ]
    elements = requirement.get("uiModel", {}).get("elements", [])
    element_partition, missing_elements = _scope_partition(
        item for item in elements if isinstance(item, dict)
    )
    state_partition, missing_states = _scope_partition(_iter_nested_states(requirement))
    criteria_partition, missing_criteria = _scope_partition(
        item for item in requirement.get("acceptanceCriteria", []) if isinstance(item, dict)
    )
    incomplete = [*missing_elements, *missing_states, *missing_criteria]
    return {
        "status": "complete",
        "acceptedClaims": {
            "buildProjected": accepted_claim_ids,
            "explicitNonBuild": [],
        },
        "elements": element_partition,
        "states": state_partition,
        "acceptanceCriteria": criteria_partition,
        "missingCanonicalIds": [],
    }, incomplete


def _root_canonical_ids(
    requirement: dict[str, Any], coverage: dict[str, Any], index: CanonicalIndex
) -> list[str]:
    """Return execution roots in deterministic source order without guessing scope."""

    roots: list[str] = []
    roots.extend(coverage["acceptedClaims"]["buildProjected"])

    ui_model = requirement.get("uiModel") if isinstance(requirement.get("uiModel"), dict) else {}
    for field in ("regions", "elements", "collections", "runtimeFields", "responsiveIntent"):
        for record in ui_model.get(field, []) if isinstance(ui_model.get(field), list) else []:
            if isinstance(record, dict) and record.get("inBuildScope") is True and isinstance(record.get("id"), str):
                roots.append(record["id"])

    roots.extend(coverage["states"]["buildProjected"])
    for asset in requirement.get("assetPlan", []):
        if isinstance(asset, dict) and asset.get("inBuildScope") is True and isinstance(asset.get("id"), str):
            roots.append(asset["id"])
    roots.extend(coverage["acceptanceCriteria"]["buildProjected"])
    # Accepted assumptions and answered questions are reviewed execution
    # decisions.  Their references point toward claims, so ordinary forward
    # closure cannot discover them from a claim root; seed them explicitly.
    for assumption in requirement.get("assumptions", []):
        if (
            isinstance(assumption, dict)
            and assumption.get("status") == "accepted"
            and isinstance(assumption.get("id"), str)
        ):
            roots.append(assumption["id"])
    for question in requirement.get("questions", []):
        if (
            isinstance(question, dict)
            and question.get("status") == "answered"
            and isinstance(question.get("id"), str)
        ):
            roots.append(question["id"])
    # Review-resolution records are copied as accepted execution semantics but
    # are not themselves canonical owners.  Seed every typed reference they
    # carry so the copied metadata can never point outside the closure.
    for resolution_index, resolution in enumerate(requirement.get("reviewResolutions", [])):
        if not isinstance(resolution, dict):
            continue
        resolution_references, _ = collect_references(
            resolution,
            index,
            path=f"$.reviewResolutions[{resolution_index}]",
        )
        roots.extend(reference.canonical_id for reference in resolution_references)
    return list(dict.fromkeys(roots))


def _fixed_point_closure(
    index: CanonicalIndex, root_ids: Sequence[str]
) -> tuple[set[str], bool, list[str]]:
    included_owner_keys: set[str] = set()
    missing_roots: list[str] = []
    for canonical_id in root_ids:
        binding = index.bindings.get(canonical_id)
        if binding is None:
            missing_roots.append(canonical_id)
        else:
            included_owner_keys.add(binding.owner_key)

    maximum_iterations = max(1, len(index.owners) + 1)
    for _ in range(maximum_iterations):
        before = len(included_owner_keys)
        # Owner iteration follows source order.  The result is a set, but stable
        # traversal makes diagnostics deterministic as well.
        for owner_key in [key for key in index.owners if key in included_owner_keys]:
            owner: Owner = index.owners[owner_key]
            references, _ = collect_references(owner.record, index, path=owner.path)
            for reference in references:
                binding = index.bindings.get(reference.canonical_id)
                if binding is not None:
                    included_owner_keys.add(binding.owner_key)
        if len(included_owner_keys) == before:
            return included_owner_keys, True, missing_roots
    return included_owner_keys, False, missing_roots


def _project_requirement(
    requirement: dict[str, Any], included_owner_keys: set[str]
) -> dict[str, Any]:
    """Copy dependency-closed records exactly, preserving every source order."""

    projected: dict[str, Any] = {}
    for key in (
        "version",
        "revision",
        "requestId",
        "inputDigest",
        "request",
        "target",
        "analysisPolicy",
    ):
        if key in requirement:
            projected[key] = copy.deepcopy(requirement[key])

    def select(field_path: tuple[str, ...]) -> list[Any]:
        source: Any = requirement
        for token in field_path:
            source = source.get(token) if isinstance(source, dict) else None
        if not isinstance(source, list):
            return []
        output: list[Any] = []
        for item_index, record in enumerate(source):
            path = "$" + "".join(f".{token}" for token in field_path) + f"[{item_index}]"
            if path in included_owner_keys:
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
    # A nested state, axis, control input, or model maps to the same physical
    # owner key, so selection here copies the complete state model atomically.
    projected["stateModels"] = select(("stateModels",))
    projected["assetPlan"] = select(("assetPlan",))
    projected["assumptions"] = select(("assumptions",))
    projected["questions"] = select(("questions",))
    projected["acceptanceCriteria"] = select(("acceptanceCriteria",))
    # Resolved review decisions can contain execution gates or constraints that
    # are more specific than their linked claim.  They are accepted semantics,
    # not merely reviewer telemetry, so retain the complete ordered records.
    if "reviewResolutions" in requirement:
        projected["reviewResolutions"] = copy.deepcopy(requirement["reviewResolutions"])
    return projected


def _audit_coverage_ledger(
    requirement: dict[str, Any],
    projected_index: CanonicalIndex,
    coverage: dict[str, Any],
) -> tuple[list[str], list[dict[str, str]]]:
    """Independently prove exact four-universe classification and projection."""

    claims = [
        item
        for item in requirement.get("claims", [])
        if isinstance(item, dict) and item.get("status") == "accepted"
    ]
    ui_model = requirement.get("uiModel") if isinstance(requirement.get("uiModel"), dict) else {}
    groups: tuple[tuple[str, list[dict[str, Any]], bool], ...] = (
        ("acceptedClaims", claims, True),
        (
            "elements",
            [item for item in ui_model.get("elements", []) if isinstance(item, dict)],
            False,
        ),
        ("states", list(_iter_nested_states(requirement)), False),
        (
            "acceptanceCriteria",
            [item for item in requirement.get("acceptanceCriteria", []) if isinstance(item, dict)],
            False,
        ),
    )
    missing_set: set[str] = set()
    errors: list[dict[str, str]] = []
    for group_name, records, claims_only in groups:
        source_ids = [record["id"] for record in records if isinstance(record.get("id"), str)]
        if claims_only:
            expected_build = list(source_ids)
            expected_non_build: list[dict[str, str]] = []
        else:
            expected_build = [
                record["id"]
                for record in records
                if record.get("inBuildScope") is True and isinstance(record.get("id"), str)
            ]
            expected_non_build = [
                {"canonicalId": record["id"], "reason": record["scopedOutReason"]}
                for record in records
                if record.get("inBuildScope") is False
                and isinstance(record.get("id"), str)
                and isinstance(record.get("scopedOutReason"), str)
            ]

        partition = coverage.get(group_name) if isinstance(coverage.get(group_name), dict) else {}
        actual_build = partition.get("buildProjected") if isinstance(partition.get("buildProjected"), list) else []
        actual_non_build = (
            partition.get("explicitNonBuild") if isinstance(partition.get("explicitNonBuild"), list) else []
        )
        actual_non_build_ids = [
            marker.get("canonicalId")
            for marker in actual_non_build
            if isinstance(marker, dict) and isinstance(marker.get("canonicalId"), str)
        ]
        actual_build_ids = [item for item in actual_build if isinstance(item, str)]
        source_set = set(source_ids)
        build_set = set(actual_build_ids)
        non_build_set = set(actual_non_build_ids)

        if (
            actual_build != expected_build
            or actual_non_build != expected_non_build
            or len(actual_build_ids) != len(build_set)
            or len(actual_non_build_ids) != len(non_build_set)
            or build_set & non_build_set
            or build_set | non_build_set != source_set
        ):
            errors.append(
                issue(
                    "accepted-build-view.coverage_partition",
                    f"$.coverage.{group_name}",
                    "Coverage partition must exactly classify its complete source universe in source order, "
                    "with no duplicates, overlap, extras, or implicit omissions.",
                )
            )
            missing_set.update(source_set - (build_set | non_build_set))

        for canonical_id in expected_build:
            if canonical_id not in projected_index.bindings:
                missing_set.add(canonical_id)

    source_index = build_canonical_index(requirement)
    missing = [canonical_id for canonical_id in source_index.order if canonical_id in missing_set]
    missing.extend(sorted(missing_set - set(missing)))
    return missing, errors


def _audit_projection(
    projected: dict[str, Any],
    root_ids: Sequence[str],
    expected_owner_keys: set[str],
    source_index: CanonicalIndex,
    requirement: dict[str, Any],
    coverage: dict[str, Any],
) -> tuple[list[str], list[dict[str, str]], list[dict[str, str]]]:
    projected_index = build_canonical_index(projected)
    missing_set = {identifier for identifier in root_ids if identifier not in projected_index.bindings}
    references, unknown = collect_references(projected, projected_index)
    dangling, mismatched = _reference_integrity_issues(references, projected_index)

    # An included source owner may own multiple nested IDs.  The projection must
    # not silently mutate their canonical types while copying whole records.
    reference_issues = [*unknown, *dangling, *mismatched]
    expected_primary_ids = {
        source_index.owners[owner_key].primary_id
        for owner_key in expected_owner_keys
        if owner_key in source_index.owners
    }
    projected_primary_ids = {
        owner.primary_id for owner in projected_index.owners.values()
    }
    if projected_primary_ids != expected_primary_ids:
        reference_issues.append(
            issue(
                "accepted-build-view.projection_owner_set",
                "$.requirement",
                "Projected physical owners must exactly equal the dependency-closure owner set; "
                "extra audit owners and implicit omissions are forbidden.",
            )
        )
        missing_set.update(expected_primary_ids - projected_primary_ids)

    for header in (
        "version",
        "revision",
        "requestId",
        "inputDigest",
        "request",
        "target",
        "analysisPolicy",
        "reviewResolutions",
    ):
        if (header in projected) != (header in requirement) or (
            header in requirement and projected.get(header) != requirement.get(header)
        ):
            reference_issues.append(
                issue(
                    "accepted-build-view.projection_header",
                    f"$.requirement.{header}",
                    f"Retained header {header!r} must be an exact copy of the complete Requirement value.",
                )
            )
    for canonical_id, binding in projected_index.bindings.items():
        source_binding = source_index.bindings.get(canonical_id)
        if source_binding is None or source_binding.canonical_type != binding.canonical_type:
            reference_issues.append(
                issue(
                    "accepted-build-view.projection_identity",
                    "$",
                    f"Projected canonical identity {canonical_id!r} does not match the complete Requirement.",
                )
            )
            missing_set.add(canonical_id)
    for projected_owner in projected_index.owners.values():
        source_binding = source_index.bindings.get(projected_owner.primary_id)
        source_owner = (
            source_index.owners.get(source_binding.owner_key)
            if source_binding is not None
            else None
        )
        if source_owner is not None and projected_owner.record != source_owner.record:
            reference_issues.append(
                issue(
                    "accepted-build-view.projection_value",
                    projected_owner.key,
                    f"Projected owner {projected_owner.primary_id!r} is not an exact copy of the complete Requirement record.",
                )
            )
    ledger_missing, ledger_issues = _audit_coverage_ledger(requirement, projected_index, coverage)
    missing_set.update(ledger_missing)
    missing = [canonical_id for canonical_id in source_index.order if canonical_id in missing_set]
    missing.extend(sorted(missing_set - set(missing)))
    return missing, reference_issues, ledger_issues


def _first_fallback_reason(reasons: set[str]) -> str | None:
    for reason in FALLBACK_PRIORITY:
        if reason in reasons:
            return reason
    return None


def compute_view_content_canonical_sha256(view: dict[str, Any]) -> str:
    """Hash the complete View while removing its one self-referential field."""

    material = copy.deepcopy(view)
    bindings = material.get("bindings")
    if isinstance(bindings, dict):
        bindings.pop("viewContentCanonicalSha256", None)
    return canonical_sha256(material)


def _json_values_strict_equal(left: Any, right: Any) -> bool:
    """Compare only plain JSON runtime types without overloadable containers."""

    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return left.keys() == right.keys() and all(
            _json_values_strict_equal(left[key], right[key]) for key in left
        )
    if type(left) is list:
        return len(left) == len(right) and all(
            _json_values_strict_equal(left_item, right_item)
            for left_item, right_item in zip(left, right)
        )
    if type(left) in (str, int, float, bool, type(None)):
        return left == right
    return False


def _view_schema_errors(view: Any, schema: dict[str, Any]) -> list[dict[str, str]]:
    errors = validate_schema_instance(view, schema)
    if not isinstance(view, dict):
        return errors
    mode = view.get("mode")
    build_allowed = view.get("buildAllowed")
    fallback = view.get("fallbackReason")
    coverage = view.get("coverage") if isinstance(view.get("coverage"), dict) else {}
    status = coverage.get("status")
    missing = coverage.get("missingCanonicalIds")
    if not _json_values_strict_equal(
        view.get("dispatchContract"), BUILD_PLANNING_DISPATCH_CONTRACT
    ):
        errors.append(
            issue(
                "accepted-build-view.dispatch_contract",
                "$.dispatchContract",
                "Build-planning dispatch contract must exactly preserve the validated no-history, declared-output, and no-Editor boundary.",
            )
        )
    if mode == "projected":
        if build_allowed is not True or fallback is not None or status != "complete" or missing != []:
            errors.append(
                issue(
                    "accepted-build-view.projected_mode",
                    "$",
                    "Projected mode requires buildAllowed=true, complete coverage, no missing IDs, and null fallbackReason.",
                )
            )
    elif mode == "full-fallback":
        if build_allowed is not False or not isinstance(fallback, str) or status == "complete":
            errors.append(
                issue(
                    "accepted-build-view.fallback_mode",
                    "$",
                    "Full fallback requires buildAllowed=false, a stable fallbackReason, and non-complete coverage.",
                )
            )
    bindings = view.get("bindings") if isinstance(view.get("bindings"), dict) else {}
    actual_hash = bindings.get("viewContentCanonicalSha256")
    try:
        expected_hash = compute_view_content_canonical_sha256(view)
    except (TypeError, ValueError):
        expected_hash = None
    if expected_hash is None or actual_hash != expected_hash:
        errors.append(
            issue(
                "accepted-build-view.content_digest",
                "$.bindings.viewContentCanonicalSha256",
                "View content digest does not match the canonical View with its self-reference removed.",
            )
        )
    return errors


def _build_accepted_build_view_from_value(
    requirement: dict[str, Any],
    *,
    requirement_file_sha256: str,
    requirement_schema_path: Path | str | RequirementSnapshot = DEFAULT_REQUIREMENT_SCHEMA,
    view_schema_path: Path | str | Mapping[str, Any] | RequirementSnapshot = DEFAULT_VIEW_SCHEMA,
) -> tuple[dict[str, Any], str, str | None]:
    """Build a safe execution projection or an exact, build-blocked fallback."""

    if not isinstance(requirement_file_sha256, str) or SHA256_PATTERN.fullmatch(requirement_file_sha256) is None:
        raise AcceptedBuildViewError(
            "accepted-build-view.requirement_hash",
            "requirement_file_sha256 must be a lowercase SHA-256 digest.",
        )

    requirement_schema, requirement_schema_sha256 = _load_schema(requirement_schema_path)
    if requirement_schema_sha256 is None:
        raise AcceptedBuildViewError(
            "accepted-build-view.requirement_schema_path",
            "requirement_schema_path must identify the on-disk Requirement schema so its bytes can be bound.",
        )
    _validate_source_requirement(requirement, requirement_schema)

    index = build_canonical_index(requirement)
    references, unknown_reference_issues = collect_references(requirement, index)
    dangling, mismatched = _reference_integrity_issues(references, index)
    coverage, incomplete_scope_ids = _coverage_ledger(requirement)
    root_ids = _root_canonical_ids(requirement, coverage, index)

    fallback_reasons: set[str] = set()
    if requirement_schema_sha256 != SUPPORTED_REQUIREMENT_SCHEMA_SHA256:
        fallback_reasons.add("unknown-requirement-schema")
    if unknown_reference_issues:
        fallback_reasons.add("unknown-reference-shape")
    if index.duplicate_ids:
        fallback_reasons.add("duplicate-canonical-id")
    if dangling:
        fallback_reasons.add("dangling-reference")
    if mismatched:
        fallback_reasons.add("reference-type-mismatch")
    if incomplete_scope_ids:
        fallback_reasons.add("scope-classification-incomplete")

    projection_missing: list[str] = []
    projection_issues: list[dict[str, str]] = []
    ledger_issues: list[dict[str, str]] = []
    projected: dict[str, Any] = {}
    if not fallback_reasons:
        included_owner_keys, stable, missing_roots = _fixed_point_closure(index, root_ids)
        if not stable:
            fallback_reasons.add("closure-incomplete")
        if missing_roots:
            projection_missing.extend(missing_roots)
            fallback_reasons.add("projection-coverage-incomplete")
        if not fallback_reasons:
            projected = _project_requirement(requirement, included_owner_keys)
            projection_missing, projection_issues, ledger_issues = _audit_projection(
                projected, root_ids, included_owner_keys, index, requirement, coverage
            )
            if projection_missing or ledger_issues:
                fallback_reasons.add("projection-coverage-incomplete")
            elif projection_issues:
                fallback_reasons.add("closure-incomplete")

    fallback_reason = _first_fallback_reason(fallback_reasons)
    mode = "full-fallback" if fallback_reason is not None else "projected"
    build_allowed = mode == "projected"
    if build_allowed:
        content = projected
        coverage["status"] = "complete"
        coverage["missingCanonicalIds"] = []
    else:
        content = copy.deepcopy(requirement)
        if fallback_reason == "projection-coverage-incomplete":
            coverage["status"] = "incomplete"
            coverage["missingCanonicalIds"] = list(dict.fromkeys(projection_missing))
        else:
            coverage["status"] = "indeterminate"
            coverage["missingCanonicalIds"] = list(dict.fromkeys(incomplete_scope_ids))

    review = requirement["reviewGate"]
    view = {
        "version": "0.1",
        "viewKind": "nextgame-ui-accepted-build-view",
        "notice": VIEW_NOTICE,
        "requestId": requirement["requestId"],
        "inputDigest": requirement["inputDigest"],
        "revision": requirement["revision"],
        "mode": mode,
        "buildAllowed": build_allowed,
        "fallbackReason": fallback_reason,
        "dispatchContract": copy.deepcopy(BUILD_PLANNING_DISPATCH_CONTRACT),
        "bindings": {
            "requirementFileSha256": requirement_file_sha256,
            "requirementCanonicalSha256": canonical_sha256(requirement),
            "approvedContentSha256": review["approvedContentSha256"],
            "requirementSchemaId": (
                requirement_schema.get("$id")
                if isinstance(requirement_schema.get("$id"), str) and requirement_schema.get("$id")
                else "unknown"
            ),
            "requirementSchemaSha256": requirement_schema_sha256,
        },
        "coverage": coverage,
        "requirement": content,
    }
    view["bindings"]["viewContentCanonicalSha256"] = compute_view_content_canonical_sha256(view)

    view_schema, _ = _load_schema(view_schema_path)
    schema_errors = _view_schema_errors(view, view_schema)
    if schema_errors:
        raise AcceptedBuildViewError(
            "accepted-build-view.internal_schema",
            "Generated Accepted Build View failed its own closed schema.",
            errors=schema_errors,
        )
    return view, mode, fallback_reason


def build_accepted_build_view(
    requirement_source: RequirementSnapshot | Path | str | bytes,
    *,
    requirement_file_sha256: str | None = None,
    requirement_schema_path: Path | str | RequirementSnapshot = DEFAULT_REQUIREMENT_SCHEMA,
    view_schema_path: Path | str | Mapping[str, Any] | RequirementSnapshot = DEFAULT_VIEW_SCHEMA,
) -> tuple[dict[str, Any], str, str | None]:
    """Build from one self-authenticating bytes snapshot.

    ``requirement_file_sha256`` remains only as an explicit fail-closed guard for
    callers of the unreleased legacy API.  Accepting that naked value would let a
    caller bind bytes from a different file to an in-memory Requirement.
    """

    if requirement_file_sha256 is not None:
        raise AcceptedBuildViewError(
            "accepted-build-view.unsafe_source_binding",
            "Caller-supplied requirement_file_sha256 is forbidden; pass the Requirement path, exact bytes, "
            "or a RequirementSnapshot so parsing and hashing use the same bytes.",
        )
    snapshot = _coerce_requirement_snapshot(requirement_source)
    requirement = snapshot.load()
    return _build_accepted_build_view_from_value(
        requirement,
        requirement_file_sha256=snapshot.file_sha256,
        requirement_schema_path=requirement_schema_path,
        view_schema_path=view_schema_path,
    )


def validate_accepted_build_view(
    view: dict[str, Any],
    *,
    source_requirement: RequirementSnapshot | Path | str | bytes | Any,
    source_requirement_file_sha256: str | None = None,
    schema: Path | str | Mapping[str, Any] | RequirementSnapshot = DEFAULT_VIEW_SCHEMA,
    requirement_schema_path: Path | str | RequirementSnapshot = DEFAULT_REQUIREMENT_SCHEMA,
) -> dict[str, Any]:
    """Validate a View by Schema and exact rebuild from the full Requirement."""

    # The complete Requirement is authoritative even when the supplied View or
    # its Schema is malformed.  Freeze and validate the Requirement bytes and
    # the Requirement Schema authority before reading the candidate View
    # contract, so no View-side failure can bypass that gate.
    try:
        if source_requirement_file_sha256 is not None:
            raise AcceptedBuildViewError(
                "accepted-build-view.unsafe_source_binding",
                "Caller-supplied source_requirement_file_sha256 is forbidden; validation must parse and hash "
                "the same Requirement bytes snapshot.",
            )
        source_snapshot = _coerce_requirement_snapshot(source_requirement)
        bound_requirement_schema = _coerce_requirement_snapshot(requirement_schema_path)
        requirement_schema, _ = _load_schema(bound_requirement_schema)
        source_value = source_snapshot.load()
        _validate_source_requirement(source_value, requirement_schema)
    except AcceptedBuildViewError as exc:
        errors = list(exc.errors)
        errors.append(issue(exc.code, "$", str(exc)))
        validation = result(errors)
        validation.update({"buildAllowed": False, "mode": None, "fallbackReason": None})
        return validation

    bound_view_schema = (
        copy.deepcopy(schema)
        if type(schema) is dict
        else _coerce_requirement_snapshot(schema)
    )
    view_schema, _ = _load_schema(bound_view_schema)
    errors = _view_schema_errors(view, view_schema)
    try:
        expected, expected_mode, expected_reason = _build_accepted_build_view_from_value(
            source_value,
            requirement_file_sha256=source_snapshot.file_sha256,
            requirement_schema_path=bound_requirement_schema,
            view_schema_path=view_schema,
        )
    except AcceptedBuildViewError as exc:
        errors.extend(exc.errors)
        errors.append(issue(exc.code, "$", str(exc)))
        validation = result(errors)
        validation.update({"buildAllowed": False, "mode": None, "fallbackReason": None})
        return validation

    if not _json_values_strict_equal(view, expected):
        errors.append(
            issue(
                "accepted-build-view.exact_rebuild",
                "$",
                "Accepted Build View does not exactly equal the deterministic rebuild from the complete Requirement.",
            )
        )
    validation = result(errors)
    validation["buildAllowed"] = bool(validation["valid"] and expected.get("buildAllowed") is True)
    validation["mode"] = expected_mode
    validation["fallbackReason"] = expected_reason
    validation["viewCanonicalSha256"] = canonical_sha256(view)
    return validation


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Agent-facing execution data stays compact.  Dict insertion order is
    # deterministic and preserves the source contract's semantic array order;
    # a trailing newline keeps the artifact friendly to ordinary file tools.
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
        handle.write("\n")


def _build_cli(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("requirement", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--requirement-schema", type=Path, default=DEFAULT_REQUIREMENT_SCHEMA)
    parser.add_argument("--view-schema", type=Path, default=DEFAULT_VIEW_SCHEMA)
    args = parser.parse_args(argv)
    try:
        view, mode, fallback_reason = build_accepted_build_view(
            RequirementSnapshot.from_path(args.requirement),
            requirement_schema_path=args.requirement_schema,
            view_schema_path=args.view_schema,
        )
        _write_json(args.output, view)
    except (OSError, ValueError, AcceptedBuildViewError) as exc:
        payload: dict[str, Any] = {"valid": False, "error": str(exc)}
        if isinstance(exc, AcceptedBuildViewError) and exc.errors:
            payload["errors"] = exc.errors
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "valid": True,
                "buildAllowed": view["buildAllowed"],
                "output": str(args.output.resolve()),
                "mode": mode,
                "fallbackReason": fallback_reason,
                "viewCanonicalSha256": canonical_sha256(view),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if view["buildAllowed"] else 2


def _validate_cli(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate an Accepted Build View against its complete Requirement.")
    parser.add_argument("view", type=Path)
    parser.add_argument("--requirement", type=Path, required=True)
    parser.add_argument("--schema", type=Path, default=DEFAULT_VIEW_SCHEMA)
    parser.add_argument("--requirement-schema", type=Path, default=DEFAULT_REQUIREMENT_SCHEMA)
    args = parser.parse_args(argv)
    try:
        validation = validate_accepted_build_view(
            load_json(args.view),
            source_requirement=RequirementSnapshot.from_path(args.requirement),
            schema=args.schema,
            requirement_schema_path=args.requirement_schema,
        )
    except (OSError, ValueError) as exc:
        print(json.dumps({"valid": False, "buildAllowed": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(validation, ensure_ascii=False, indent=2))
    return 0 if validation.get("valid") and validation.get("buildAllowed") else 2


if __name__ == "__main__":
    raise SystemExit(_build_cli())
