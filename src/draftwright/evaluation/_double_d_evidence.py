"""Double-D physical correspondence and downstream evidence for STEP evaluation."""

from __future__ import annotations

import re
from collections import Counter
from math import isclose, isfinite
from typing import Literal, TypeAlias, cast

Outcome: TypeAlias = Literal["supported", "unknown", "unsupported"]


_DOUBLE_D_REQUIREMENTS = frozenset({"bore.diameter", "profile_across_flats.length"})
_DOUBLE_D_GEOMETRY_TOLERANCE = 1e-6
# Released 0.4.10 rounds DoubleDBore.depth to four decimal places and HoleRecord.depth to
# two. Their two half-quanta plus kernel noise are the conservative axial-span ambiguity.
_DOUBLE_D_SPAN_TOLERANCE = 0.5e-4 + 0.5e-2 + _DOUBLE_D_GEOMETRY_TOLERANCE
_DOUBLE_D_CALLOUT_RE = re.compile(
    r"[ø⌀Ø]\s*(?P<major>\d+(?:\.\d+)?)\s+THRU\s+DOUBLE-D\s+"
    r"(?P<across>\d+(?:\.\d+)?)\s+A/F(?![\w/])"
)


def _double_d_number(value, field: str) -> float:
    """Validate one public numeric field before any semantic normalisation."""
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not isfinite(value):
        raise ValueError(f"Double-D {field} must be a finite number")
    return float(value)


def _double_d_vector(value, field: str) -> tuple[float, float, float]:
    """Validate one public vector field without accepting coercible schema impostors."""
    if not isinstance(value, tuple) or len(value) != 3:
        raise ValueError(f"Double-D {field} must be a finite 3-vector")
    return cast(
        tuple[float, float, float],
        tuple(_double_d_number(component, f"{field} component") for component in value),
    )


def _double_d_axis(value) -> str:
    """Return the principal axis letter represented by a public record vector."""
    direction = _double_d_vector(value, "axis")
    magnitude = sum(component * component for component in direction) ** 0.5
    if magnitude <= 1e-12:
        raise ValueError("Double-D axis must be non-zero")
    normal = tuple(component / magnitude for component in direction)
    index = max(range(3), key=lambda candidate: abs(normal[candidate]))
    if not isclose(abs(normal[index]), 1.0, rel_tol=0.0, abs_tol=1e-6) or any(
        abs(component) > 1e-6 for candidate, component in enumerate(normal) if candidate != index
    ):
        raise ValueError("Double-D axis must be principal")
    if not isclose(magnitude, 1.0, rel_tol=0.0, abs_tol=_DOUBLE_D_GEOMETRY_TOLERANCE):
        raise ValueError("Double-D axis must be unit length")
    return "xyz"[index]


def _double_d_direction(value) -> tuple[float, float, float]:
    """Canonicalise the unoriented flat line for semantic comparison."""
    direction = _double_d_vector(value, "flat direction")
    magnitude = sum(component * component for component in direction) ** 0.5
    if magnitude <= 1e-12:
        raise ValueError("Double-D flat direction must be non-zero")
    if not isclose(magnitude, 1.0, rel_tol=0.0, abs_tol=_DOUBLE_D_GEOMETRY_TOLERANCE):
        raise ValueError("Double-D flat direction must be unit length")
    normal = tuple(component / magnitude for component in direction)
    first = next(component for component in normal if abs(component) > 1e-12)
    if first < 0:
        normal = tuple(-component for component in normal)
    return cast(
        tuple[float, float, float],
        tuple(0.0 if abs(component) <= 1e-12 else round(component, 6) for component in normal),
    )


def _double_d_record_key(bore) -> tuple:
    """Exact consumer meaning of one provider Double-D occurrence."""
    from quiddity import DoubleDBore

    if type(bore) is not DoubleDBore:
        raise ValueError("Double-D inventory members must be public DoubleDBore records")
    axis = _double_d_axis(bore.axis)
    if bore.axis["xyz".index(axis)] < 0:
        raise ValueError("Double-D axis must use the canonical positive principal direction")
    location = tuple(
        round(component, 6) for component in _double_d_vector(bore.location, "location")
    )
    values = tuple(
        round(_double_d_number(value, field), 6)
        for value, field in (
            (bore.major_diameter, "major diameter"),
            (bore.across_flats, "across flats"),
            (bore.depth, "depth"),
        )
    )
    if not all(value > 0 for value in values):
        raise ValueError("Double-D sizes must be finite and positive")
    if not isinstance(bore.through, bool):
        raise ValueError("Double-D through must be bool")
    direction = _double_d_direction(bore.flat_direction)
    axis_index = "xyz".index(axis)
    if abs(direction[axis_index]) > 1e-6:
        raise ValueError("Double-D flat direction must lie in the profile plane")
    return (axis, location, *values, bore.through, direction)


def _double_d_span(
    source, *, ordinary_hole: bool
) -> tuple[
    str,
    tuple[tuple[float, float, float], tuple[float, float, float]],
]:
    """Canonical unoriented axial span for competing through-opening representations."""
    field = "ordinary-hole" if ordinary_hole else "bore"
    direction = _double_d_vector(source.axis, f"{field} axis")
    axis = _double_d_axis(direction)
    axis_index = "xyz".index(axis)
    if not ordinary_hole and direction[axis_index] < 0:
        raise ValueError("Double-D bore axis must use the canonical positive direction")
    location = _double_d_vector(source.location, f"{field} location")
    depth = _double_d_number(source.depth, f"{field} depth")
    if depth <= 0:
        raise ValueError(f"Double-D {field} depth must be positive")
    # HoleRecord.axis points from its selected opening into the void. DoubleDBore.axis is the
    # canonical positive principal direction and its location is the high-axis opening. Use the
    # exact principal basis after validating the public vector, so accepted unit-rounding noise
    # cannot grow with depth.
    travel = (1.0 if direction[axis_index] > 0 else -1.0) * depth if ordinary_hole else -depth
    other_values = list(location)
    other_values[axis_index] += travel
    other = cast(tuple[float, float, float], tuple(other_values))
    endpoints = tuple(sorted((location, other)))
    return axis, cast(tuple[tuple[float, float, float], tuple[float, float, float]], endpoints)


def _double_d_spans_match(left, right) -> bool:
    if left[0] != right[0]:
        return False
    axis_index = "xyz".index(left[0])
    return all(
        isclose(
            left_component,
            right_component,
            rel_tol=0.0,
            abs_tol=(
                _DOUBLE_D_SPAN_TOLERANCE
                if component_index == axis_index
                else _DOUBLE_D_GEOMETRY_TOLERANCE
            ),
        )
        for left_point, right_point in zip(left[1], right[1], strict=True)
        for component_index, (left_component, right_component) in enumerate(
            zip(left_point, right_point, strict=True)
        )
    )


def _double_d_exclusive_owners(bores, holes) -> list[bool]:
    """Refuse Double-D credit at a frame also claimed by the ordinary-hole aggregate."""
    hole_spans = [_double_d_span(hole, ordinary_hole=True) for hole in holes]
    bore_spans = [_double_d_span(bore, ordinary_hole=False) for bore in bores]
    return [
        not any(_double_d_spans_match(bore_span, hole_span) for hole_span in hole_spans)
        for bore_span in bore_spans
    ]


def _double_d_feature_key(feature) -> tuple | None:
    if getattr(feature, "kind", None) != "hole" or getattr(feature, "profile", None) != "double_d":
        return None
    try:
        if not isinstance(feature.frame.axis, str):
            raise ValueError("Double-D feature axis must be a string")
        axis = feature.frame.axis.lower()
        if axis not in {"x", "y", "z"}:
            raise ValueError("Double-D feature axis must be principal")
        location = tuple(
            round(component, 6)
            for component in _double_d_vector(feature.frame.origin, "feature location")
        )
        values = tuple(
            round(_double_d_number(value, field), 6)
            for value, field in (
                (feature.diameter, "feature diameter"),
                (feature.across_flats, "feature across flats"),
                (feature.depth, "feature depth"),
            )
        )
        if not all(value > 0 for value in values) or not isinstance(feature.through, bool):
            raise ValueError("malformed Double-D feature")
        direction = _double_d_direction(feature.profile_direction)
        if abs(direction["xyz".index(axis)]) > 1e-6:
            raise ValueError("Double-D feature direction must lie in the profile plane")
        return (axis, location, *values, feature.through, direction)
    except (AttributeError, TypeError, ValueError):
        return None


def _double_d_correspondence(bores, features) -> list[tuple[bool, object | None]]:
    """Match the complete provider inventory to exact IR owners, preserving multiplicity.

    Full axial locations distinguish disconnected coaxial bodies.  Equal coincident records may
    still prove multiplicity when the IR has the same number of owners; the benchmark corpus does
    not pretend to identify otherwise indistinguishable occurrences.  Any extra, malformed or
    missing Double-D IR owner invalidates the boundary instead of being ignored.
    """
    bores = tuple(bores)
    features = tuple(features)
    record_keys = [_double_d_record_key(bore) for bore in bores]
    candidate_features = [
        feature
        for feature in features
        if getattr(feature, "kind", None) == "hole"
        and getattr(feature, "profile", None) == "double_d"
    ]
    feature_keys = [_double_d_feature_key(feature) for feature in candidate_features]
    repeated_records = len({id(bore) for bore in bores}) != len(bores)
    repeated_features = len({id(feature) for feature in candidate_features}) != len(
        candidate_features
    )
    if (
        repeated_records
        or repeated_features
        or any(key is None for key in feature_keys)
        or Counter(record_keys) != Counter(feature_keys)
    ):
        return [(False, None) for _bore in bores]

    by_key: dict[tuple, list[object]] = {}
    for key, feature in zip(feature_keys, candidate_features, strict=True):
        assert key is not None
        by_key.setdefault(key, []).append(feature)
    return [(True, by_key[key].pop(0)) for key in record_keys]


def _double_d_model_outcomes(bores, features) -> list[Outcome]:
    return [
        "supported" if exact else "unknown"
        for exact, _feature in _double_d_correspondence(bores, features)
    ]


def _double_d_callout_has_roles(annotation, feature) -> bool:
    label = getattr(annotation, "label", None)
    match = _DOUBLE_D_CALLOUT_RE.search(str(label)) if label else None
    if match is None:
        return False
    try:
        major = float(match.group("major"))
        across = float(match.group("across"))
        expected_major = _double_d_number(feature.diameter, "feature diameter")
        expected_across = _double_d_number(feature.across_flats, "feature across flats")
        return isclose(major, expected_major, rel_tol=0.0, abs_tol=1e-6) and isclose(
            across, expected_across, rel_tol=0.0, abs_tol=1e-6
        )
    except (AttributeError, TypeError, ValueError):
        return False


def _double_d_drawing_outcomes(bores, drawing) -> list[Outcome]:
    """Require both exact compiler claims on one live role-specific profile callout."""
    from draftwright.linting.evidence import verify_measurement_claims
    from draftwright.model.compiled import compile_dimensions

    model = drawing.model()
    correspondence = _double_d_correspondence(bores, model.features)
    plan = compile_dimensions(model)
    by_annotation: dict[tuple[int, str], set[str]] = {}
    for claim in verify_measurement_claims(drawing.registry, plan):
        measurement = claim.measurement
        parameter = str(getattr(measurement, "parameter", ""))
        feature = getattr(measurement, "feature", None)
        if (
            claim.state == "confirmed"
            and feature is not None
            and claim.annotation is not None
            and parameter in _DOUBLE_D_REQUIREMENTS
        ):
            by_annotation.setdefault((id(feature), claim.annotation), set()).add(parameter)

    results: list[Outcome] = []
    for exact, feature in correspondence:
        if not exact or feature is None:
            results.append("unknown")
            continue
        supported = any(
            owner == id(feature)
            and requirements == _DOUBLE_D_REQUIREMENTS
            and _double_d_callout_has_roles(drawing.registry.named(annotation), feature)
            for (owner, annotation), requirements in by_annotation.items()
        )
        results.append("supported" if supported else "unsupported")
    return results


def _declared_double_d_model(part, bores):
    """Declare public provider records through ``Sheet.double_d_bore`` and return its IR."""
    from draftwright.sheet import Sheet

    sheet = Sheet(part)
    sheet.authored_dimensions()
    for bore in bores:
        sheet.double_d_bore(
            major_diameter=bore.major_diameter,
            across_flats=bore.across_flats,
            at=bore.location,
            axis=_double_d_axis(bore.axis),
            depth=bore.depth,
            through=bore.through,
            profile_direction=bore.flat_direction,
        )
    return sheet.model()
