"""Semantic completeness for whole-part polygonal stock (#1082, #1371).

Each aggregate ``PolygonalStock`` is one complete regular-hexagonal-prism body with two
manufacturing requirements: its across-flats definition and axial stock length. Principal axis,
physical centre, cap span, and the coupled six-flat support ring join the provider record to
exactly one ``PolygonalStockFeature``. Compiler identities then join both requirements to placed,
explicitly satisfied, suppressed, dropped, missing, or unverifiable outcomes.

Labels, annotation names, views, leader tips, projections, and page coordinates are never
source-to-IR correspondence evidence.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from math import atan2, cos, hypot, isfinite, pi, sin
from typing import Literal

from b123d_recognisers import PolygonalStock, RecognitionResult

from draftwright.linting._registry import satisfaction_ids, satisfaction_of
from draftwright.linting.issues import (
    UNJOINED_PARAMETER_ID,
    LintIssue,
    is_placement_drop,
    requirement_subject,
)

PolygonalStockState = Literal[
    "placed",
    "satisfied_by_structured_note",
    "suppressed",
    "dropped",
    "missing",
    "unverifiable",
]
Point = tuple[float, float, float]
SupportRing = tuple[tuple[Point, Point], ...]

# PolygonalStock publishes flat centres/directions to 0.001 while centre and A/F use
# 0.0001.  Comparing the independently rounded fields therefore needs a component-wise
# allowance that grows with the A/F radius; it must not grow with the world translation.
_SUPPORT_POINT_QUANTIZATION = 5e-4
_SUPPORT_CENTER_QUANTIZATION = 5e-5
_SUPPORT_DIRECTION_QUANTIZATION = 5e-4
_SUPPORT_DIRECTION_ANGULAR_QUANTIZATION = hypot(
    _SUPPORT_DIRECTION_QUANTIZATION, _SUPPORT_DIRECTION_QUANTIZATION
)
_SUPPORT_MINIMUM_GAP_SINE = sin(pi / 3 - pi / 90)
_SUPPORT_AF_QUANTIZATION = 5e-5
_SUPPORT_ABSOLUTE_FLOOR = 2e-3
_SUPPORT_TRANSVERSE_MODEL_TOLERANCE = 0.2


@dataclass(frozen=True)
class PolygonalStockOutcome:
    """The observable engine outcome of one whole-stock measurement."""

    source_at: Point | None
    parameter_id: str
    state: PolygonalStockState
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
        raise ValueError("a polygonal-stock span needs exactly two endpoints")
    return tuple(sorted(endpoints))  # type: ignore[return-value]


def _canonical_support_ring(directions, centres) -> SupportRing:
    """Preserve direction/centre coupling while ignoring ring start and winding."""
    rounded_directions = _points(directions)
    rounded_centres = _points(centres)
    if not rounded_directions or len(rounded_directions) != len(rounded_centres):
        raise ValueError("polygonal-stock supports need paired direction and centre rings")
    pairs = tuple(zip(rounded_directions, rounded_centres, strict=True))
    variants: list[SupportRing] = []
    for winding in (pairs, tuple(reversed(pairs))):
        variants.extend(winding[index:] + winding[:index] for index in range(len(winding)))
    return min(variants)


def _expected_support_offsets(
    directions: tuple[tuple[float, ...], ...], across_flats: float, axis_index: int
) -> tuple[Point, ...]:
    """Rebuild each side midpoint from its coupled adjacent published support lines."""
    in_plane = [index for index in range(3) if index != axis_index]
    normals = []
    for direction in directions:
        length = hypot(direction[in_plane[0]], direction[in_plane[1]])
        if length == 0:
            raise ValueError("a polygonal-stock support direction cannot be zero")
        normals.append((direction[in_plane[0]] / length, direction[in_plane[1]] / length))
    distance = across_flats / 2
    offsets = []
    for index, normal in enumerate(normals):
        intersections = []
        for neighbour_index in ((index - 1) % len(normals), (index + 1) % len(normals)):
            neighbour = normals[neighbour_index]
            determinant = normal[0] * neighbour[1] - normal[1] * neighbour[0]
            if abs(determinant) <= 1e-9:
                raise ValueError("adjacent polygonal-stock support lines must intersect")
            intersections.append(
                (
                    distance * (neighbour[1] - normal[1]) / determinant,
                    distance * (normal[0] - neighbour[0]) / determinant,
                )
            )
        midpoint = (
            (intersections[0][0] + intersections[1][0]) / 2,
            (intersections[0][1] + intersections[1][1]) / 2,
        )
        offset = [0.0, 0.0, 0.0]
        offset[in_plane[0]], offset[in_plane[1]] = midpoint
        offsets.append((offset[0], offset[1], offset[2]))
    return tuple(offsets)


def polygonal_stock_center(stock) -> Point:
    center = getattr(stock, "center", None)
    if center is None:
        center = stock.frame.origin
    return _point(center)


def _validate_polygonal_stock_source(stock: PolygonalStock) -> None:
    """Enforce the installed provider's exact whole-stock schema at intake."""
    side_count = stock.side_count
    if type(side_count) is not int or side_count != 6:
        raise ValueError("the polygonal-stock provider contract requires exactly six sides")
    axis = str(stock.axis)
    if axis not in {"x", "y", "z"}:
        raise ValueError("the polygonal-stock provider contract requires a principal axis")
    axis_index = "xyz".index(axis)
    in_plane = [index for index in range(3) if index != axis_index]
    center = tuple(float(value) for value in stock.center)
    across_flats = float(stock.across_flats)
    base, top = float(stock.base), float(stock.top)
    if (
        len(center) != 3
        or not all(isfinite(value) for value in (*center, across_flats, base, top))
        or across_flats <= 0
        or top <= base
        or abs(center[axis_index] - (base + top) / 2) > 2e-3
    ):
        raise ValueError("the polygonal-stock provider contract requires finite physical bounds")
    directions = tuple(tuple(float(value) for value in point) for point in stock.flat_directions)
    centres = tuple(tuple(float(value) for value in point) for point in stock.flat_centres)
    if any(len(point) != 3 for point in (*directions, *centres)) or not all(
        isfinite(value) for point in (*directions, *centres) for value in point
    ):
        raise ValueError("the polygonal-stock provider contract requires finite supports")
    ring = _canonical_support_ring(directions, centres)
    if len(ring) != side_count:
        raise ValueError("the polygonal-stock provider contract requires six paired supports")
    if len(set(_points(directions))) != side_count or len(set(_points(centres))) != side_count:
        raise ValueError("the polygonal-stock provider contract requires distinct supports")
    angles = []
    angle_tol = pi / 90 + 2e-3
    expected_offsets = _expected_support_offsets(directions, across_flats, axis_index)
    radial_tolerance_base = max(
        _SUPPORT_ABSOLUTE_FLOOR,
        _SUPPORT_TRANSVERSE_MODEL_TOLERANCE
        + _SUPPORT_POINT_QUANTIZATION
        + _SUPPORT_CENTER_QUANTIZATION
        + _SUPPORT_AF_QUANTIZATION / 2
        + across_flats * _SUPPORT_DIRECTION_QUANTIZATION**2,
    )
    tangential_tolerance = max(
        _SUPPORT_ABSOLUTE_FLOOR,
        2 * _SUPPORT_TRANSVERSE_MODEL_TOLERANCE
        + _SUPPORT_POINT_QUANTIZATION
        + _SUPPORT_CENTER_QUANTIZATION
        + across_flats / 2 * _SUPPORT_DIRECTION_ANGULAR_QUANTIZATION / _SUPPORT_MINIMUM_GAP_SINE,
    )
    axial_tolerance = max(
        _SUPPORT_ABSOLUTE_FLOOR,
        _SUPPORT_POINT_QUANTIZATION + _SUPPORT_CENTER_QUANTIZATION,
    )
    for direction, flat_center, expected_offset in zip(
        directions, centres, expected_offsets, strict=True
    ):
        norm = hypot(*direction)
        observed_offset = tuple(flat_center[index] - center[index] for index in range(3))
        residual = tuple(
            observed - expected
            for observed, expected in zip(observed_offset, expected_offset, strict=True)
        )
        radial = sum(residual[index] * direction[index] for index in in_plane) / norm
        tangential = (
            -residual[in_plane[0]] * direction[in_plane[1]]
            + residual[in_plane[1]] * direction[in_plane[0]]
        ) / norm
        expected_tangential = (
            -expected_offset[in_plane[0]] * direction[in_plane[1]]
            + expected_offset[in_plane[1]] * direction[in_plane[0]]
        ) / norm
        radial_tolerance = (
            radial_tolerance_base
            + abs(expected_tangential) * _SUPPORT_DIRECTION_ANGULAR_QUANTIZATION
        )
        if (
            abs(norm - 1.0) > 1e-3
            or abs(direction[axis_index]) > 1e-6
            or abs(radial) > radial_tolerance
            or abs(tangential) > tangential_tolerance
            or abs(residual[axis_index]) > axial_tolerance
        ):
            raise ValueError("the polygonal-stock provider contract requires physical supports")
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
        raise ValueError("the polygonal-stock provider contract requires one regular support ring")


def _span(stock) -> tuple[Point, Point]:
    span = getattr(stock, "span", None)
    if span is not None:
        return _canonical_span(span)
    center = list(polygonal_stock_center(stock))
    axis_index = "xyz".index(str(stock.axis))
    start = list(center)
    end = list(center)
    start[axis_index] = _rounded(stock.base)
    end[axis_index] = _rounded(stock.top)
    return _canonical_span((start, end))


def polygonal_stock_key(stock) -> tuple:
    """Every compiler-significant geometric fact for one whole polygonal prism."""
    axis = getattr(stock, "axis", None)
    if axis is None:
        axis = stock.frame.axis
    length = getattr(stock, "length", None)
    if length is None:
        start, end = _span(stock)
        length = abs(end["xyz".index(str(axis))] - start["xyz".index(str(axis))])
    return (
        str(axis),
        polygonal_stock_center(stock),
        int(stock.side_count),
        _rounded(stock.across_flats),
        _rounded(length),
        _span(stock),
        _canonical_support_ring(stock.flat_directions, stock.flat_centres),
    )


def _parameter_ids(feature, source) -> tuple[str, str] | None:
    try:
        parameters = tuple(feature.parameters())
        ids = tuple(parameter.parameter_id for parameter in parameters)
        values = tuple(_rounded(parameter.value) for parameter in parameters)
        required = ("polygon_across_flats.length", "stock_length.length")
        if ids != required:
            return None
        if values != (_rounded(source.across_flats), _rounded(source.length)):
            return None
        if parameters[0].span is not None:
            return None
        length_span = parameters[1].span
        if length_span is None or _canonical_span(length_span) != _span(source):
            return None
        return required
    except (AttributeError, OverflowError, TypeError, ValueError):
        return None


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


def polygonal_stock_outcomes(
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
) -> list[PolygonalStockOutcome]:
    """Follow every recognised whole-stock requirement to its semantic outcome."""
    if recognition is None:
        return []
    if not isinstance(recognition, RecognitionResult):
        raise TypeError(
            "polygonal_stock_outcomes() requires the run's RecognitionResult; "
            f"got {type(recognition).__name__}"
        )
    sources: tuple[object, ...] = tuple(recognition.polygonal_stock)
    if not sources:
        return []
    # PolygonalStock is a whole-part proof, so the public aggregate contract is zero-or-one.
    # Any larger inventory is one malformed family observation, never extra denominator credit.
    if len(sources) != 1:
        return [
            PolygonalStockOutcome(
                None,
                UNJOINED_PARAMETER_ID,
                "unverifiable",
                requirement_count=2,
                source_records=sources,
            )
        ]
    source = sources[0]
    try:
        if not isinstance(source, PolygonalStock):
            raise TypeError(f"unexpected polygonal-stock record {type(source).__name__}")
        _validate_polygonal_stock_source(source)
        key = polygonal_stock_key(source)
        at = polygonal_stock_center(source)
    except (AttributeError, OverflowError, TypeError, ValueError):
        return [
            PolygonalStockOutcome(
                None,
                UNJOINED_PARAMETER_ID,
                "unverifiable",
                requirement_count=2,
                source_records=(source,),
            )
        ]

    ir_by_key: dict[tuple, list] = defaultdict(list)
    for feature in features:
        if getattr(feature, "kind", None) != "polygonal_stock":
            continue
        try:
            ir_by_key[polygonal_stock_key(feature)].append(feature)
        except (AttributeError, OverflowError, TypeError, ValueError):
            continue

    placed, satisfied, dropped = _index_evidence(registry)
    suppressed = {
        (omission.feature, omission.parameter_id)
        for omission in omissions
        if omission.feature is not None and omission.authored
    }
    matches = ir_by_key.get(key, ())
    feature = matches[0] if len(matches) == 1 else None
    parameter_ids = _parameter_ids(feature, source) if feature is not None else None
    if parameter_ids is None:
        return [
            PolygonalStockOutcome(
                at,
                UNJOINED_PARAMETER_ID,
                "unverifiable",
                requirement_count=2,
                source_records=(source,),
            )
        ]
    return [
        PolygonalStockOutcome(
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
    ]


def lint_polygonal_stock_coverage(
    part,
    *,
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
    assembly=None,
) -> list[LintIssue]:
    """Require A/F and axial length without duplicating explicit placement drops."""
    if assembly is None:
        assembly = len(part.solids()) > 1
    severity: Literal["info", "warning"] = "info" if assembly else "warning"
    messages = {
        "suppressed": "was deliberately omitted by the authored dimension set",
        "missing": "has no placed, suppressed, or dropped measurement outcome",
        "unverifiable": "cannot be joined to measurement provenance without guessing",
    }
    issues = []
    for outcome in polygonal_stock_outcomes(recognition, features, registry, omissions):
        if outcome.state in {"placed", "satisfied_by_structured_note", "dropped"}:
            continue
        location = (
            "at an unknown location" if outcome.source_at is None else f"at {outcome.source_at}"
        )
        issues.append(
            LintIssue(
                severity=severity,
                code=f"polygonal_stock_requirement_{outcome.state}",
                message=(
                    f"polygonal stock {location} {requirement_subject(outcome)} {messages[outcome.state]}"
                ),
            )
        )
    return issues
