"""Semantic completeness for recognised rectangular blind slots (#1421).

Each aggregate ``RectangularBlindSlot`` owns three independently auditable requirements:
the U-section width, the capped mouth-to-terminal run and the flat-bottom depth.  Every
released structural fact joins the provider record to one IR feature; exact parameter
identities then follow those requirements to drawing outcomes.  Labels, annotation names,
views, projected geometry, leader tips and page coordinates are never correspondence evidence.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from math import isfinite
from numbers import Real
from typing import Literal

from b123d_recognisers import RecognitionResult, RectangularBlindSlot

from draftwright.linting._registry import satisfaction_ids, satisfaction_of
from draftwright.linting.issues import LintIssue, is_placement_drop

RectangularBlindSlotRequirementState = Literal[
    "placed",
    "satisfied_by_structured_note",
    "suppressed",
    "dropped",
    "missing",
    "unverifiable",
]

_PARAMETERS = (
    "rectangular_blind_slot_width.length",
    "rectangular_blind_slot_length.length",
    "rectangular_blind_slot_depth.length",
)


@dataclass(frozen=True)
class RectangularBlindSlotRequirementOutcome:
    """The observable engine outcome of one width, run or depth requirement."""

    source_at: tuple[float, float, float]
    parameter_id: str
    state: RectangularBlindSlotRequirementState
    requirement_count: int = 1
    features: tuple = ()
    source_records: tuple[object, ...] = field(default=(), repr=False, compare=False, kw_only=True)


def _rounded(value) -> float:
    try:
        result = round(float(value), 3)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError from exc
    if not isfinite(result):
        raise ValueError
    return result


def _positive(value) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError
    result = _rounded(value)
    if result <= 0:
        raise ValueError
    return result


def _point(value) -> tuple[float, float, float]:
    if (
        not isinstance(value, tuple)
        or len(value) != 3
        or any(
            isinstance(component, bool) or not isinstance(component, Real) for component in value
        )
    ):
        raise ValueError
    return (_rounded(value[0]), _rounded(value[1]), _rounded(value[2]))


def rectangular_blind_slot_key(slot, *, require_frame: bool = False) -> tuple:
    """Return all public structural facts after validating their exact schema.

    Rounding is limited to the generated Sheet program's documented 0.001 mm precision.
    Ambiguous duplicate keys fail closed in :func:`rectangular_blind_slot_requirement_outcomes`.
    """

    axes = (slot.axis, slot.width_axis, slot.depth_axis)
    if (
        any(not isinstance(axis, str) or axis not in {"x", "y", "z"} for axis in axes)
        or len(set(axes)) != 3
    ):
        raise ValueError
    for sign in (slot.open_sign, slot.depth_sign):
        if not isinstance(sign, int) or isinstance(sign, bool) or sign not in (-1, 1):
            raise ValueError
    if require_frame:
        if slot.frame.axis != slot.axis:
            raise ValueError
        at = _point(slot.frame.origin)
    else:
        at = _point(slot.at)
    return (
        slot.axis,
        slot.open_sign,
        slot.width_axis,
        slot.depth_axis,
        slot.depth_sign,
        _positive(slot.width),
        _positive(slot.length),
        _positive(slot.depth),
        at,
    )


def _source_at(source) -> tuple[float, float, float]:
    try:
        return _point(source.at)
    except (AttributeError, OverflowError, TypeError, ValueError):
        return (float("nan"), float("nan"), float("nan"))


def _span(at, axis: str, value: float) -> tuple[tuple[float, float, float], ...]:
    lo = list(at)
    hi = list(at)
    index = "xyz".index(axis)
    lo[index] -= value / 2
    hi[index] += value / 2
    return (_point(tuple(lo)), _point(tuple(hi)))


def _parameter_ids(feature, source) -> tuple[str, ...] | None:
    try:
        parameters = tuple(feature.parameters())
        observed = {}
        for parameter in parameters:
            parameter_id = parameter.parameter_id
            if not isinstance(parameter_id, str) or parameter_id in observed:
                return None
            span = (
                tuple(_point(point) for point in parameter.span)
                if parameter.span is not None
                else None
            )
            observed[parameter_id] = (_positive(parameter.value), span)
        source_at = _point(source.at)
        expected_values = (
            _positive(source.width),
            _positive(source.length),
            _positive(source.depth),
        )
        expected_spans = (
            _span(source_at, source.width_axis, expected_values[0]),
            _span(source_at, source.axis, expected_values[1]),
            _span(source_at, source.depth_axis, expected_values[2]),
        )
        expected = dict(zip(_PARAMETERS, zip(expected_values, expected_spans), strict=True))
    except (AttributeError, IndexError, OverflowError, TypeError, ValueError):
        return None
    if observed != expected:
        return None
    return _PARAMETERS


def _index_evidence(registry):
    placed = {
        (measurement.feature, measurement.parameter)
        for name in registry.names()
        for measurement in registry.measurement_of(name)
    }
    satisfied = {
        (identity.feature, identity.parameter)
        for identity in satisfaction_ids(registry)
        if identity.feature is not None and isinstance(identity.parameter, str)
    }
    dropped = {
        (measurement.feature, measurement.parameter)
        for issue in registry.issues
        if is_placement_drop(issue)
        for measurement in getattr(issue, "measurement_ids", ())
        if getattr(measurement, "feature", None) is not None
        and isinstance(getattr(measurement, "parameter", None), str)
    }
    return placed, satisfied, dropped


def rectangular_blind_slot_requirement_outcomes(
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
) -> list[RectangularBlindSlotRequirementOutcome]:
    """Follow all three requirements of every recognised rectangular blind slot."""

    if recognition is None:
        return []
    if not isinstance(recognition, RecognitionResult):
        raise TypeError(
            "rectangular_blind_slot_requirement_outcomes() requires the run's "
            f"RecognitionResult; got {type(recognition).__name__}"
        )
    if not isinstance(recognition.rectangular_blind_slots, tuple):
        raise TypeError("RecognitionResult.rectangular_blind_slots must be an immutable tuple")
    sources = recognition.rectangular_blind_slots
    if not sources:
        return []

    keyed_sources: list[tuple[RectangularBlindSlot, tuple | None]] = []
    source_counts: dict[tuple, int] = defaultdict(int)
    for source in sources:
        try:
            if not isinstance(source, RectangularBlindSlot):
                raise TypeError
            key = rectangular_blind_slot_key(source)
        except (AttributeError, IndexError, OverflowError, TypeError, ValueError):
            key = None
        keyed_sources.append((source, key))
        if key is not None:
            source_counts[key] += 1

    ir_by_key: dict[tuple, list] = defaultdict(list)
    for feature in features:
        if getattr(feature, "kind", None) != "rectangular_blind_slot":
            continue
        try:
            ir_by_key[rectangular_blind_slot_key(feature, require_frame=True)].append(feature)
        except (AttributeError, IndexError, OverflowError, TypeError, ValueError):
            continue

    placed, satisfied, dropped = _index_evidence(registry)
    suppressed = {
        (omission.feature, omission.parameter_id)
        for omission in omissions
        if omission.feature is not None and omission.authored
    }
    outcomes: list[RectangularBlindSlotRequirementOutcome] = []
    for source, key in keyed_sources:
        matches = ir_by_key.get(key, ()) if key is not None else ()
        feature = (
            matches[0] if key is not None and len(matches) == source_counts[key] == 1 else None
        )
        parameter_ids = _parameter_ids(feature, source) if feature is not None else None
        if parameter_ids is None:
            outcomes.extend(
                RectangularBlindSlotRequirementOutcome(
                    _source_at(source),
                    parameter,
                    "unverifiable",
                    source_records=(source,),
                )
                for parameter in _PARAMETERS
            )
            continue
        for parameter in parameter_ids:
            identity = (feature, parameter)
            if identity in placed:
                state: RectangularBlindSlotRequirementState = "placed"
            elif identity in satisfied:
                state = "satisfied_by_structured_note"
            elif identity in suppressed:
                state = "suppressed"
            elif identity in dropped:
                state = "dropped"
            else:
                associated = registry.names_for_feature(feature)
                state = (
                    "unverifiable"
                    if any(
                        not registry.measurement_of(name) and not satisfaction_of(registry, name)
                        for name in associated
                    )
                    else "missing"
                )
            outcomes.append(
                RectangularBlindSlotRequirementOutcome(
                    _source_at(source),
                    parameter,
                    state,
                    features=(feature,),
                    source_records=(source,),
                )
            )
    return outcomes


def lint_rectangular_blind_slot_coverage(
    part,
    *,
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
    assembly=None,
) -> list[LintIssue]:
    """Report uncovered rectangular blind-slot requirements without duplicate drops."""

    if assembly is None:
        assembly = len(part.solids()) > 1
    severity: Literal["info", "warning"] = "info" if assembly else "warning"
    messages = {
        "suppressed": "was deliberately omitted by the authored dimension set",
        "missing": "has no placed, suppressed, or dropped measurement outcome",
        "unverifiable": "cannot be joined to measurement provenance without guessing",
    }
    issues = []
    for outcome in rectangular_blind_slot_requirement_outcomes(
        recognition, features, registry, omissions
    ):
        if outcome.state in {"placed", "satisfied_by_structured_note", "dropped"}:
            continue
        issues.append(
            LintIssue(
                severity=severity,
                code=f"rectangular_blind_slot_requirement_{outcome.state}",
                message=(
                    f"rectangular blind slot {outcome.parameter_id} at {outcome.source_at} "
                    f"{messages[outcome.state]}"
                ),
            )
        )
    return issues
