"""Independent completeness outcomes for the three whole-part envelope axes.

The bounding box is physical geometry, not recognition or compiled intent.  Each non-zero
axis therefore remains in the denominator even when detection forgets to create an envelope
feature.  Final registry identities and explicit compiler omissions supply outcomes only;
annotation names, labels and page coordinates are never evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from draftwright.linting._registry import satisfaction_ids
from draftwright.linting.issues import is_placement_drop

EnvelopeRequirementState = Literal[
    "placed",
    "satisfied_by_structured_note",
    "suppressed",
    "dropped",
    "missing",
]

_AXIS_PARAMETERS = (
    ("x", "width.length"),
    ("y", "depth.length"),
    ("z", "height.length"),
)
_TOL = 1e-3


@dataclass(frozen=True)
class EnvelopeRequirementOutcome:
    """The observable engine outcome of one whole-part bounding extent."""

    axis: str
    parameter_id: str
    extent: float
    state: EnvelopeRequirementState


def _bbox_bounds(part) -> tuple[tuple[float, float], ...]:
    bbox = part.bounding_box()
    return (
        (float(bbox.min.X), float(bbox.max.X)),
        (float(bbox.min.Y), float(bbox.max.Y)),
        (float(bbox.min.Z), float(bbox.max.Z)),
    )


def _identity_interval(identity, bounds, axis_index: int) -> tuple[float, float] | None:
    """Return the physical interval one semantic measurement carries on *axis_index*."""

    feature: Any = getattr(identity, "feature", None)
    try:
        parameter = next(
            item
            for item in feature.parameters()
            if item.parameter_id == getattr(identity, "parameter", None)
        )
        span = parameter.span
    except (AttributeError, StopIteration, TypeError, ValueError):
        return None
    if span is not None:
        try:
            varying = tuple(
                index
                for index in range(3)
                if abs(float(span[0][index]) - float(span[1][index])) > _TOL
            )
            if varying != (axis_index,):
                return None
            lo, hi = sorted((float(span[0][axis_index]), float(span[1][axis_index])))
            return (lo, hi)
        except (IndexError, TypeError, ValueError):
            return None

    # OD dimensions have no two-point span because their circular witness is not a linear
    # feature edge. Their semantic owner still proves a transverse bbox interval when the
    # circle is centred on that axis and its stated diameter reaches both bounds.
    if getattr(feature, "kind", None) not in {"rotational", "boss", "step"} or not str(
        getattr(parameter, "parameter_id", "")
    ).endswith("diameter"):
        return None
    try:
        feature_axis = "xyz".index(str(feature.frame.axis))
        centre = float(feature.frame.origin[axis_index])
        radius = float(parameter.value) / 2.0
    except (AttributeError, TypeError, ValueError):
        return None
    if feature_axis == axis_index:
        return None
    return (centre - radius, centre + radius)


def _intervals_cover_axis(identities, bounds, axis_index: int) -> bool:
    intervals = sorted(
        interval
        for identity in identities
        if (interval := _identity_interval(identity, bounds, axis_index)) is not None
    )
    if not intervals:
        return False
    low, high = bounds[axis_index]
    cursor = low
    for interval_low, interval_high in intervals:
        if interval_high < cursor - _TOL:
            continue
        if interval_low > cursor + _TOL:
            return False
        cursor = max(cursor, interval_high)
        if cursor >= high - _TOL:
            return True
    return False


def _issue_spans_axis(issue, bounds, axis_index: int) -> bool:
    for span in getattr(issue, "measurement_spans", ()):
        try:
            varying = tuple(
                index
                for index in range(3)
                if abs(float(span[0][index]) - float(span[1][index])) > _TOL
            )
            if varying != (axis_index,):
                continue
            lo, hi = sorted((float(span[0][axis_index]), float(span[1][axis_index])))
        except (IndexError, TypeError, ValueError):
            continue
        if abs(lo - bounds[axis_index][0]) <= _TOL and abs(hi - bounds[axis_index][1]) <= _TOL:
            return True
    return False


def envelope_requirement_outcomes(
    part, registry, omissions=()
) -> list[EnvelopeRequirementOutcome]:
    """Classify each physical bbox axis from exact semantic engine evidence."""

    if part is None:
        return []
    bounds = _bbox_bounds(part)
    placed = tuple(
        measurement for name in registry.names() for measurement in registry.measurement_of(name)
    )
    satisfied = satisfaction_ids(registry)
    drop_issues = tuple(issue for issue in registry.issues if is_placement_drop(issue))
    dropped = tuple(
        identity for issue in drop_issues for identity in getattr(issue, "measurement_ids", ())
    )
    outcomes = []
    for axis_index, (axis, parameter_id) in enumerate(_AXIS_PARAMETERS):
        extent = bounds[axis_index][1] - bounds[axis_index][0]
        if extent <= _TOL:
            continue
        if _intervals_cover_axis(placed, bounds, axis_index):
            state: EnvelopeRequirementState = "placed"
        elif _intervals_cover_axis(satisfied, bounds, axis_index):
            state = "satisfied_by_structured_note"
        elif any(
            omission.parameter_id == parameter_id and abs(float(omission.value) - extent) <= _TOL
            for omission in omissions
        ):
            state = "suppressed"
        elif _intervals_cover_axis(dropped, bounds, axis_index) or any(
            _issue_spans_axis(issue, bounds, axis_index) for issue in drop_issues
        ):
            state = "dropped"
        else:
            state = "missing"
        outcomes.append(EnvelopeRequirementOutcome(axis, parameter_id, extent, state))
    return outcomes
