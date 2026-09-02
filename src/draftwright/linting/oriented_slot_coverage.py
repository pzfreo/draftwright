"""Semantic completeness for standalone free-direction oriented slots (#1432)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from math import isfinite
from numbers import Real
from typing import Literal

from b123d_recognisers import OrientedSlot, RecognitionResult

from draftwright.feature_identity import is_exact_oriented_slot_feature
from draftwright.linting._registry import satisfaction_ids, satisfaction_of
from draftwright.linting.issues import LintIssue, is_placement_drop
from draftwright.oriented_slot_contract import (
    oriented_slot_provider_key,
    validate_oriented_slot_pattern,
)

_PARAMETERS = ("oriented_slot_width.length", "oriented_slot_length.length")

OrientedSlotRequirementState = Literal[
    "placed",
    "satisfied_by_structured_note",
    "suppressed",
    "dropped",
    "missing",
    "unverifiable",
]


@dataclass(frozen=True)
class OrientedSlotRequirementOutcome:
    source_at: tuple[float, float, float]
    parameter_id: str
    state: OrientedSlotRequirementState
    requirement_count: int = 1


def _real(value, *, name: str, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite real number")
    try:
        result = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite real number") from exc
    if not isfinite(result) or (positive and result <= 0):
        raise ValueError(f"{name} must be a finite real number")
    return result


def _vector(value) -> tuple[float, float, float]:
    if not isinstance(value, tuple) or len(value) != 3:
        raise ValueError("oriented slot correspondence vectors must contain three components")
    components = tuple(
        _real(component, name="oriented slot vector component") for component in value
    )
    return (components[0], components[1], components[2])


def _source_at(source) -> tuple[float, float, float]:
    try:
        return _vector(source.center)
    except (AttributeError, OverflowError, TypeError, ValueError):
        return (float("nan"), float("nan"), float("nan"))


def _provider_key(slot) -> tuple:
    return oriented_slot_provider_key(slot)


def _feature_key(feature) -> tuple:
    if not is_exact_oriented_slot_feature(feature):
        raise TypeError("oriented slot IR members must carry oriented-slot semantics")
    passage = feature.passage
    return (
        _vector(feature.frame.origin),
        feature.frame.axis,
        _vector(feature.width_direction),
        _vector(feature.long_direction),
        _real(feature.width, name="oriented slot IR width"),
        _real(feature.length, name="oriented slot IR length"),
        _vector(passage.origin),
        _vector(passage.run),
        _vector(passage.u),
        _vector(passage.v),
        tuple(
            _real(value, name="oriented slot IR run interval") for value in passage.run_interval
        ),
        tuple(
            (
                (
                    _real(point[0], name="oriented slot IR boundary coordinate"),
                    _real(point[1], name="oriented slot IR boundary coordinate"),
                ),
                _real(bulge, name="oriented slot IR boundary bulge"),
            )
            for point, bulge in passage.boundary
        ),
        passage.low_capped,
        passage.high_capped,
        None
        if passage.body_key is None
        else tuple(_real(value, name="oriented slot IR body key") for value in passage.body_key),
    )


def _standalone_sources(recognition: RecognitionResult) -> tuple[tuple[OrientedSlot, ...], bool]:
    """Consume exact pattern occurrences once; retain all sources if ownership is corrupt."""
    original = recognition.oriented_slots
    remaining = list(original)
    try:
        for pattern in recognition.oriented_slot_patterns:
            for _member, member_key in validate_oriented_slot_pattern(pattern):
                index = next(
                    index
                    for index, candidate in enumerate(remaining)
                    if _provider_key(candidate) == member_key
                )
                remaining.pop(index)
    except (AttributeError, IndexError, OverflowError, StopIteration, TypeError, ValueError):
        return original, True
    return tuple(remaining), False


def _matches(measurement, feature, parameter_id: str) -> bool:
    return (
        getattr(measurement, "feature", None) == feature
        and getattr(measurement, "parameter", None) == parameter_id
    )


def _state(feature, parameter_id, *, placed, satisfied, suppressed, dropped, registry):
    if any(_matches(measurement, feature, parameter_id) for measurement in placed):
        return "placed"
    if any(_matches(identity, feature, parameter_id) for identity in satisfied):
        return "satisfied_by_structured_note"
    if (feature, parameter_id) in suppressed:
        return "suppressed"
    if any(_matches(measurement, feature, parameter_id) for measurement in dropped):
        return "dropped"
    if any(
        not registry.measurement_of(name) and not satisfaction_of(registry, name)
        for name in registry.names_for_feature(feature)
    ):
        return "unverifiable"
    return "missing"


def oriented_slot_requirement_outcomes(
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
) -> list[OrientedSlotRequirementOutcome]:
    """Join each recognised non-pattern member to two compiler-owned outcomes."""
    if recognition is None:
        return []
    if not isinstance(recognition, RecognitionResult):
        raise TypeError(
            "oriented_slot_requirement_outcomes() requires the run RecognitionResult; "
            f"got {type(recognition).__name__}"
        )
    if type(recognition.oriented_slots) is not tuple:
        raise TypeError("RecognitionResult.oriented_slots must be an immutable tuple")
    if type(recognition.oriented_slot_patterns) is not tuple:
        raise TypeError("RecognitionResult.oriented_slot_patterns must be an immutable tuple")
    sources, ownership_invalid = _standalone_sources(recognition)
    if not sources:
        return []

    keyed_sources: list[tuple[OrientedSlot, tuple | None]] = []
    source_counts: dict[tuple, int] = defaultdict(int)
    for source in sources:
        try:
            if not isinstance(source, OrientedSlot):
                raise TypeError
            key = _provider_key(source)
        except (AttributeError, IndexError, OverflowError, TypeError, ValueError):
            key = None
        keyed_sources.append((source, key))
        if key is not None:
            source_counts[key] += 1
    ir_by_key: dict[tuple, list] = defaultdict(list)
    for feature in features:
        if getattr(feature, "kind", None) != "oriented_slot":
            continue
        try:
            ir_by_key[_feature_key(feature)].append(feature)
        except (AttributeError, IndexError, OverflowError, TypeError, ValueError):
            continue

    placed = {
        measurement for name in registry.names() for measurement in registry.measurement_of(name)
    }
    satisfied = satisfaction_ids(registry)
    suppressed = {
        (omission.feature, omission.parameter_id)
        for omission in omissions
        if omission.feature is not None and omission.authored
    }
    dropped = {
        measurement
        for issue in registry.issues
        if is_placement_drop(issue)
        for measurement in getattr(issue, "measurement_ids", ())
    }

    outcomes: list[OrientedSlotRequirementOutcome] = []
    for source, key in keyed_sources:
        matches = ir_by_key.get(key, ()) if key is not None and not ownership_invalid else ()
        feature = (
            matches[0] if key is not None and len(matches) == source_counts[key] == 1 else None
        )
        try:
            parameter_ids = (
                tuple(parameter.parameter_id for parameter in feature.parameters())
                if feature is not None
                else ()
            )
        except (AttributeError, TypeError, ValueError):
            parameter_ids = ()
        if parameter_ids != _PARAMETERS:
            outcomes.extend(
                OrientedSlotRequirementOutcome(
                    _source_at(source),
                    parameter_id,
                    "unverifiable",
                )
                for parameter_id in _PARAMETERS
            )
            continue
        outcomes.extend(
            OrientedSlotRequirementOutcome(
                _source_at(source),
                parameter_id,
                _state(
                    feature,
                    parameter_id,
                    placed=placed,
                    satisfied=satisfied,
                    suppressed=suppressed,
                    dropped=dropped,
                    registry=registry,
                ),
            )
            for parameter_id in parameter_ids
        )
    return outcomes


def lint_oriented_slot_coverage(
    part,
    *,
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
    assembly=None,
) -> list[LintIssue]:
    """Report uncovered standalone oriented-slot requirements."""
    if assembly is None:
        assembly = len(part.solids()) > 1
    severity: Literal["info", "warning"] = "info" if assembly else "warning"
    messages = {
        "suppressed": "was deliberately omitted by the authored dimension set",
        "dropped": "was not placed",
        "missing": "has no annotation outcome",
        "unverifiable": "cannot be joined to exactly one compiler measurement",
    }
    issues = []
    for outcome in oriented_slot_requirement_outcomes(recognition, features, registry, omissions):
        if outcome.state in ("placed", "satisfied_by_structured_note"):
            continue
        issues.append(
            LintIssue(
                severity=severity,
                code=f"oriented_slot_requirement_{outcome.state}",
                message=f"oriented slot {outcome.parameter_id} at {outcome.source_at} "
                f"{messages[outcome.state]}",
            )
        )
    return issues
