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
from dataclasses import dataclass, field
from math import atan2, cos, hypot, isclose, isfinite, pi
from typing import Literal

from b123d_recognisers import PolygonalBoss, RecognitionResult

from draftwright.linting._registry import satisfaction_ids, satisfaction_of
from draftwright.linting.issues import (
    UNJOINED_PARAMETER_ID,
    LintIssue,
    is_placement_drop,
    requirement_subject,
)

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
    source_records: tuple[object, ...] = field(default=(), repr=False, compare=False, kw_only=True)


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


def _validate_polygonal_boss_source(boss: PolygonalBoss) -> None:
    """Enforce invariants of the installed provider family at source intake."""
    side_count = boss.side_count
    if type(side_count) is not int or side_count != 6:
        raise ValueError("the polygonal-boss provider contract requires exactly six sides")
    axis = str(boss.axis)
    if axis not in {"x", "y", "z"}:
        raise ValueError("the polygonal-boss provider contract requires a principal axis")
    axis_index = "xyz".index(axis)
    in_plane = [index for index in range(3) if index != axis_index]
    center = tuple(float(value) for value in boss.center)
    across_flats = float(boss.across_flats)
    base, top = float(boss.base), float(boss.top)
    if (
        len(center) != 3
        or not all(isfinite(value) for value in (*center, across_flats, base, top))
        or across_flats <= 0
        or top <= base
        or not isclose(center[axis_index], (base + top) / 2, abs_tol=2e-3)
    ):
        raise ValueError("the polygonal-boss provider contract requires finite physical bounds")
    directions = tuple(tuple(float(value) for value in point) for point in boss.flat_directions)
    centres = tuple(tuple(float(value) for value in point) for point in boss.flat_centres)
    if any(len(point) != 3 for point in (*directions, *centres)) or not all(
        isfinite(value) for point in (*directions, *centres) for value in point
    ):
        raise ValueError("the polygonal-boss provider contract requires finite supports")
    ring = _canonical_support_ring(directions, centres)
    if len(ring) != side_count:
        raise ValueError("the polygonal-boss provider contract requires six paired supports")
    if len(set(_points(directions))) != side_count or len(set(_points(centres))) != side_count:
        raise ValueError("the polygonal-boss provider contract requires distinct supports")
    angles = []
    angle_tol = pi / 90 + 2e-3
    for direction, flat_center in zip(directions, centres, strict=True):
        norm = hypot(*direction)
        support = sum(
            (flat_center[index] - center[index]) * direction[index] for index in in_plane
        )
        if (
            not all(isfinite(value) for value in (*direction, *flat_center))
            or abs(norm - 1.0) > 1e-3
            or abs(direction[axis_index]) > 1e-6
            or not isclose(support, across_flats / 2, rel_tol=1e-3, abs_tol=0.202)
            or not base - 1e-6 <= flat_center[axis_index] <= top + 1e-6
        ):
            raise ValueError("the polygonal-boss provider contract requires physical supports")
        angles.append(atan2(direction[in_plane[1]], direction[in_plane[0]]) % (2 * pi))
    expected = 2 * pi / side_count
    gaps = [
        (angles[(index + 1) % side_count] - angles[index]) % (2 * pi)
        for index in range(side_count)
    ]
    counter_clockwise = all(abs(gap - expected) <= angle_tol for gap in gaps)
    clockwise = all(abs(gap - (2 * pi - expected)) <= angle_tol for gap in gaps)
    opposed = all(
        sum(
            directions[index][component] * directions[index + side_count // 2][component]
            for component in range(3)
        )
        <= -cos(angle_tol)
        * hypot(*directions[index])
        * hypot(*directions[index + side_count // 2])
        for index in range(side_count // 2)
    )
    if not ((counter_clockwise or clockwise) and opposed):
        raise ValueError("the polygonal-boss provider contract requires one regular support ring")
    area_vector = tuple(
        sum(
            start[(index + 1) % 3] * end[(index + 2) % 3]
            - start[(index + 2) % 3] * end[(index + 1) % 3]
            for start, end in zip(centres, (*centres[1:], centres[0]), strict=True)
        )
        for index in range(3)
    )
    if sum(component * component for component in area_vector) <= 1e-12:
        raise ValueError(
            "the polygonal-boss provider contract requires a nondegenerate support ring"
        )


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
            _validate_polygonal_boss_source(source)
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
                    UNJOINED_PARAMETER_ID,
                    "unverifiable",
                    requirement_count=2,
                    source_records=(source,),
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
                source_records=(source,),
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
                    f"polygonal boss {location} {requirement_subject(outcome)} {messages[outcome.state]}"
                ),
            )
        )
    return issues
