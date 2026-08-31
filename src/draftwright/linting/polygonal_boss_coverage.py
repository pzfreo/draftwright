"""Semantic completeness for recognised polygonal-boss requirements (#1372).

Each aggregate ``PolygonalBoss`` is one attached regular-hexagonal-prism occurrence with two
manufacturing requirements: its across-flats definition and its attachment-to-terminal
height.  Principal axis, physical centre, the six-side schema invariant, axial span, ordered flat directions
and physical flat centres join the provider record to exactly one ``PolygonalBossFeature``.
Compiler ``DimensionId`` values then join both requirements to placed, explicitly satisfied,
suppressed, dropped, missing, or unverifiable outcomes.  Labels, annotation names, views,
leader tips, projections and page coordinates are never correspondence evidence.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

from b123d_recognisers import PolygonalBoss, RecognitionResult

from draftwright.linting._registry import satisfaction_ids, satisfaction_of
from draftwright.linting.issues import LintIssue, is_placement_drop

PolygonalBossRequirementState = Literal[
    "placed",
    "satisfied_by_structured_note",
    "suppressed",
    "dropped",
    "missing",
    "unverifiable",
]
Point = tuple[float, float, float]
SupportRing = tuple[tuple[Point, Point], ...]


@dataclass(frozen=True)
class PolygonalBossRequirementOutcome:
    """The observable engine outcome of one physical polygonal-boss measurement."""

    source_at: Point | None
    parameter_id: str
    state: PolygonalBossRequirementState
    requirement_count: int = 1
    features: tuple = ()


def _rounded(value) -> float:
    return round(float(value), 3)


def _point(values) -> Point:
    x, y, z = values
    return _rounded(x), _rounded(y), _rounded(z)


def _points(values) -> tuple[Point, ...]:
    return tuple(_point(value) for value in values)


def _canonical_span(values) -> tuple[Point, Point]:
    endpoints = _points(values)
    if len(endpoints) != 2:
        raise ValueError("a polygonal-boss span needs exactly two endpoints")
    return tuple(sorted(endpoints))  # type: ignore[return-value]


def _canonical_support_ring(directions, centres) -> SupportRing:
    """Preserve direction/centre coupling while ignoring ring start and winding."""
    rounded_directions = _points(directions)
    rounded_centres = _points(centres)
    if not rounded_directions or len(rounded_directions) != len(rounded_centres):
        raise ValueError("polygonal-boss supports need paired direction and centre rings")
    pairs = tuple(zip(rounded_directions, rounded_centres, strict=True))
    variants: list[SupportRing] = []
    for winding in (pairs, tuple(reversed(pairs))):
        variants.extend(winding[index:] + winding[:index] for index in range(len(winding)))
    return min(variants)


def polygonal_boss_center(boss) -> Point:
    """Return the physical prism centre retained at both sides of the compiler waist."""
    center = getattr(boss, "center", None)
    if center is None:
        center = boss.frame.origin
    return _point(center)


def _span(boss) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    span = getattr(boss, "span", None)
    if span is not None:
        return _canonical_span(span)
    center = list(polygonal_boss_center(boss))
    axis_index = "xyz".index(str(boss.axis))
    start = list(center)
    end = list(center)
    start[axis_index] = _rounded(boss.base)
    end[axis_index] = _rounded(boss.top)
    return _canonical_span((start, end))


def polygonal_boss_key(boss) -> tuple:
    """Every compiler-significant geometric fact for one attached polygonal prism."""
    axis = getattr(boss, "axis", None)
    if axis is None:
        axis = boss.frame.axis
    height = getattr(boss, "height", None)
    if height is None:
        start, end = _span(boss)
        height = abs(end["xyz".index(str(axis))] - start["xyz".index(str(axis))])
    return (
        str(axis),
        polygonal_boss_center(boss),
        int(boss.side_count),
        _rounded(boss.across_flats),
        _rounded(height),
        _span(boss),
        _canonical_support_ring(boss.flat_directions, boss.flat_centres),
    )


def _parameter_ids(feature, source) -> tuple[str, str] | None:
    try:
        parameters = tuple(feature.parameters())
        ids = tuple(parameter.parameter_id for parameter in parameters)
        values = tuple(_rounded(parameter.value) for parameter in parameters)
    except (AttributeError, TypeError, ValueError):
        return None
    required = ("polygon_across_flats.length", "boss_height.length")
    if ids != required:
        return None
    if values != (_rounded(source.across_flats), _rounded(source.height)):
        return None
    if parameters[0].span is not None:
        return None
    height_span = parameters[1].span
    if height_span is None or _canonical_span(height_span) != _span(source):
        return None
    return required


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


def _state(feature, parameter, *, placed, satisfied, suppressed, dropped, registry):
    if (feature, parameter) in placed:
        return "placed"
    if (feature, parameter) in satisfied:
        return "satisfied_by_structured_note"
    if (feature, parameter) in suppressed:
        return "suppressed"
    if (feature, parameter) in dropped:
        return "dropped"
    associated = registry.names_for_feature(feature)
    if any(
        not registry.measurement_of(name) and not satisfaction_of(registry, name)
        for name in associated
    ):
        return "unverifiable"
    return "missing"


def polygonal_boss_requirement_outcomes(
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
) -> list[PolygonalBossRequirementOutcome]:
    """Follow every recognised polygonal-boss requirement to its semantic outcome."""
    if recognition is None:
        return []
    if not isinstance(recognition, RecognitionResult):
        raise TypeError(
            "polygonal_boss_requirement_outcomes() requires the run's RecognitionResult; "
            f"got {type(recognition).__name__}"
        )
    sources: tuple[object, ...] = tuple(recognition.polygonal_bosses)
    if not sources:
        return []

    source_counts: dict[tuple, int] = defaultdict(int)
    keyed_sources: list[tuple[object, tuple | None, Point | None]] = []
    for source in sources:
        try:
            if not isinstance(source, PolygonalBoss):
                raise TypeError(f"unexpected polygonal-boss record {type(source).__name__}")
            key = polygonal_boss_key(source)
            at = polygonal_boss_center(source)
        except (AttributeError, TypeError, ValueError):
            key = None
            at = None
        keyed_sources.append((source, key, at))
        if key is not None:
            source_counts[key] += 1

    ir_by_key: dict[tuple, list] = defaultdict(list)
    for feature in features:
        if getattr(feature, "kind", None) != "polygonal_boss":
            continue
        try:
            ir_by_key[polygonal_boss_key(feature)].append(feature)
        except (AttributeError, TypeError, ValueError):
            continue

    placed, satisfied, dropped = _index_evidence(registry)
    suppressed = {
        (omission.feature, omission.parameter_id)
        for omission in omissions
        if omission.feature is not None and omission.authored
    }
    outcomes: list[PolygonalBossRequirementOutcome] = []
    for source, key, at in keyed_sources:
        matches = ir_by_key.get(key, ()) if key is not None else ()
        feature = (
            matches[0] if key is not None and len(matches) == source_counts[key] == 1 else None
        )
        parameter_ids = (
            _parameter_ids(feature, source)
            if feature is not None and isinstance(source, PolygonalBoss)
            else None
        )
        if parameter_ids is None:
            outcomes.append(
                PolygonalBossRequirementOutcome(
                    at,
                    "?",
                    "unverifiable",
                    requirement_count=2,
                )
            )
            continue
        outcomes.extend(
            PolygonalBossRequirementOutcome(
                at,
                parameter,
                _state(
                    feature,
                    parameter,
                    placed=placed,
                    satisfied=satisfied,
                    suppressed=suppressed,
                    dropped=dropped,
                    registry=registry,
                ),
                features=(feature,),
            )
            for parameter in parameter_ids
        )
    return outcomes


def lint_polygonal_boss_coverage(
    part,
    *,
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
    assembly=None,
) -> list[LintIssue]:
    """Report uncovered polygonal-boss requirements without duplicating placement drops."""
    if assembly is None:
        assembly = len(part.solids()) > 1
    severity: Literal["info", "warning"] = "info" if assembly else "warning"
    messages = {
        "suppressed": "was deliberately omitted by the authored dimension set",
        "missing": "has no placed, suppressed, or dropped measurement outcome",
        "unverifiable": "cannot be joined to measurement provenance without guessing",
    }
    issues = []
    for outcome in polygonal_boss_requirement_outcomes(recognition, features, registry, omissions):
        if outcome.state in {"placed", "satisfied_by_structured_note", "dropped"}:
            continue
        location = (
            "at an unknown location" if outcome.source_at is None else f"at {outcome.source_at}"
        )
        issues.append(
            LintIssue(
                severity=severity,
                code=f"polygonal_boss_requirement_{outcome.state}",
                message=(
                    f"polygonal boss {location} measurement "
                    f"{outcome.parameter_id} {messages[outcome.state]}"
                ),
            )
        )
    return issues
