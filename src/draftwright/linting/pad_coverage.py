"""Semantic completeness for recognised rectangular raised pads (#1372).

Each aggregate ``RaisedPad`` is one physical protrusion with five requirements: its two
footprint sizes, terminal-to-attachment height, and two in-plane locations.  Exact retained
XYZ bounds plus signed attachment direction join the source to one ``PadFeature``; compiler
``DimensionId`` values and structured directional location facts then join those requirements
to placed, explicitly satisfied, suppressed, dropped, missing, or unverifiable outcomes.
Labels, annotation names, views, projections, and page coordinates are never correspondence
evidence.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from functools import partial
from typing import Literal

from quiddity import RecognitionResult

from draftwright.contract_values import rounded as _rounded
from draftwright.linting._coverage_common import index_pad_pocket_evidence, location_state
from draftwright.linting._coverage_common import point3 as _point
from draftwright.linting._registry import (
    RequirementCarrier,
    RequirementCarrierEvidence,
)
from draftwright.linting.issues import (
    UNJOINED_PARAMETER_ID,
    LintIssue,
    requirement_subject,
)
from draftwright.measurement_support import RequirementExclusion, datum_location_exclusion

_PAD_LOCATION_DATUM_COINCIDENT_CODE = "pad_location_coincident_with_datum"
_PAD_PLANE_AXES = {"x": ("y", "z"), "y": ("z", "x"), "z": ("x", "y")}

PadRequirementState = Literal[
    "placed",
    "satisfied_by_structured_note",
    "suppressed",
    "dropped",
    "missing",
    "unverifiable",
    "inapplicable",
]


@dataclass(frozen=True)
class PadRequirementOutcome:
    """The observable engine outcome of one physical raised-pad measurement."""

    source_at: tuple[float, float, float]
    parameter_id: str
    state: PadRequirementState
    requirement_count: int = 1
    features: tuple = ()
    source_records: tuple[object, ...] = field(default=(), repr=False, compare=False, kw_only=True)
    intrinsic_exclusion: RequirementExclusion | None = field(default=None, kw_only=True)
    carriers: tuple[RequirementCarrier, ...] = field(default=(), kw_only=True)


def _pair(values) -> tuple[float, float]:
    lo, hi = values
    return _rounded(lo), _rounded(hi)


def _bounds(pad) -> dict[str, tuple[float, float]]:
    if hasattr(pad, "bounds"):
        return {axis: _pair(pad.bounds(axis)) for axis in "xyz"}
    return {
        "x": (_rounded(pad.x0), _rounded(pad.x1)),
        "y": (_rounded(pad.y0), _rounded(pad.y1)),
        "z": (_rounded(pad.z0), _rounded(pad.z1)),
    }


def _axis(pad) -> str:
    axis = getattr(pad, "axis", None)
    if axis is None:
        axis = pad.frame.axis
    return str(axis)


def pad_attachment_point(pad) -> tuple[float, float, float]:
    """Signed attachment-plane centre retained by both the source record and IR."""
    bounds = _bounds(pad)
    point = [(bounds[axis][0] + bounds[axis][1]) / 2 for axis in "xyz"]
    axis = _axis(pad)
    normal = "xyz".index(axis)
    point[normal] = bounds[axis][0 if int(pad.direction) > 0 else 1]
    return tuple(_rounded(value) for value in point)  # type: ignore[return-value]


def pad_center(pad) -> tuple[float, float, float]:
    bounds = _bounds(pad)
    return tuple(_rounded((bounds[axis][0] + bounds[axis][1]) / 2) for axis in "xyz")  # type: ignore[return-value]


def pad_key(pad) -> tuple:
    """Every compiler-significant physical pad fact, including semantic axis roles."""
    bounds = _bounds(pad)
    axis = _axis(pad)
    expected_long_axis, expected_width_axis = _PAD_PLANE_AXES[axis]
    if getattr(pad, "kind", None) == "pad":
        long_axis = str(pad.long_axis)
        width_axis = str(pad.width_axis)
        width = _rounded(pad.width)
        length = _rounded(pad.length)
        height = _rounded(pad.height)
        w_center = _rounded(pad.w_center)
        lo = _rounded(pad.lo)
        hi = _rounded(pad.hi)
        origin = _point(pad.frame.origin)
    else:
        long_axis = expected_long_axis
        width_axis = expected_width_axis
        width = _rounded(bounds[width_axis][1] - bounds[width_axis][0])
        length = _rounded(bounds[long_axis][1] - bounds[long_axis][0])
        height = _rounded(bounds[axis][1] - bounds[axis][0])
        w_center = _rounded(sum(bounds[width_axis]) / 2)
        lo, hi = bounds[long_axis]
        origin = pad_center(pad)
    return (
        axis,
        int(pad.direction),
        *(value for axis in "xyz" for value in bounds[axis]),
        long_axis,
        width_axis,
        width,
        length,
        height,
        w_center,
        lo,
        hi,
        origin,
    )


def _height_span(pad) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    bounds = _bounds(pad)
    axis = _axis(pad)
    index = "xyz".index(axis)
    start = list(pad_center(pad))
    end = list(start)
    start[index] = bounds[axis][0 if int(pad.direction) > 0 else 1]
    end[index] = bounds[axis][1 if int(pad.direction) > 0 else 0]
    return _point(start), _point(end)


def _parameter_ids(feature, source) -> tuple[str, ...] | None:
    try:
        parameters = tuple(feature.parameters())
        ids = tuple(parameter.parameter_id for parameter in parameters)
        actual_values = tuple(_rounded(parameter.value) for parameter in parameters)
    except (AttributeError, TypeError, ValueError):
        return None
    required_sizes = ("pad_width.length", "pad_length.length", "pad_height.length")
    if ids != required_sizes:
        return None
    source_bounds = _bounds(source)
    axis = _axis(source)
    long_axis, width_axis = _PAD_PLANE_AXES[axis]
    expected_values = (
        _rounded(source_bounds[width_axis][1] - source_bounds[width_axis][0]),
        _rounded(source_bounds[long_axis][1] - source_bounds[long_axis][0]),
        _rounded(source_bounds[axis][1] - source_bounds[axis][0]),
    )
    if actual_values != expected_values:
        return None
    if parameters[0].span is not None or parameters[1].span is not None:
        return None
    span = parameters[2].span
    if span is None or tuple(_point(point) for point in span) != _height_span(source):
        return None
    stem = getattr(feature, "LOCATION_STEM", None)
    if stem != "location_pad":
        return None
    if axis == "z":
        locations = tuple(f"{stem}.location.{axis}" for axis in ("x", "y"))
    else:
        locations = tuple(f"{stem}.{coord}" for coord in (long_axis, width_axis))
    return (*ids, *locations)


def _index_evidence(registry, *, carriers=None):
    return index_pad_pocket_evidence(registry, family="pad", carriers=carriers)


_state = partial(location_state, location_prefix="location_pad")


def pad_requirement_outcomes(
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
    *,
    datum=None,
) -> list[PadRequirementOutcome]:
    """Follow every recognised raised-pad requirement to its semantic outcome."""
    if recognition is None:
        return []
    if not isinstance(recognition, RecognitionResult):
        raise TypeError(
            "pad_requirement_outcomes() requires the run's RecognitionResult; "
            f"got {type(recognition).__name__}"
        )
    sources = tuple(recognition.pads)
    if not sources:
        return []
    source_counts: dict[tuple, int] = defaultdict(int)
    for source in sources:
        source_counts[pad_key(source)] += 1
    ir_by_key: dict[tuple, list] = defaultdict(list)
    for feature in features:
        if getattr(feature, "kind", None) == "pad":
            try:
                ir_by_key[pad_key(feature)].append(feature)
            except (AttributeError, TypeError, ValueError):
                # A malformed IR record cannot establish correspondence.
                continue

    carriers = RequirementCarrierEvidence(registry)
    placed, locations, satisfied, dropped = _index_evidence(registry, carriers=carriers)
    suppressed = {
        (omission.feature, omission.parameter_id)
        for omission in omissions
        if omission.feature is not None and omission.authored
    }
    inapplicable = {
        (omission.feature, omission.parameter_id)
        for omission in omissions
        if omission.feature is not None
        and getattr(omission, "code", None) == _PAD_LOCATION_DATUM_COINCIDENT_CODE
    }
    outcomes: list[PadRequirementOutcome] = []
    for source in sources:
        key = pad_key(source)
        matches = ir_by_key.get(key, ())
        feature = matches[0] if len(matches) == source_counts[key] == 1 else None
        parameter_ids = _parameter_ids(feature, source) if feature is not None else None
        at = pad_attachment_point(source)
        location = pad_center(source)
        if parameter_ids is None:
            outcomes.append(
                PadRequirementOutcome(
                    at,
                    UNJOINED_PARAMETER_ID,
                    "unverifiable",
                    requirement_count=5,
                    source_records=(source,),
                )
            )
            continue
        outcomes.extend(
            PadRequirementOutcome(
                at,
                parameter,
                _state(
                    feature,
                    parameter,
                    point=location,
                    placed=placed,
                    locations=locations,
                    satisfied=satisfied,
                    suppressed=suppressed,
                    inapplicable=inapplicable,
                    dropped=dropped,
                    registry=registry,
                    carriers=carriers,
                ),
                features=(feature,),
                source_records=(source,),
                intrinsic_exclusion=datum_location_exclusion(feature, source, parameter, datum),
            )
            for parameter in parameter_ids
        )
    return carriers.attach(outcomes)


def lint_pad_coverage(
    part,
    *,
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
    assembly=None,
) -> list[LintIssue]:
    """Report uncovered pad requirements without duplicating placement drops."""
    if assembly is None:
        assembly = len(part.solids()) > 1
    severity: Literal["info", "warning"] = "info" if assembly else "warning"
    messages = {
        "suppressed": "was deliberately omitted by the authored dimension set",
        "missing": "has no placed, suppressed, or dropped measurement outcome",
        "unverifiable": "cannot be joined to measurement provenance without guessing",
    }
    issues = []
    for outcome in pad_requirement_outcomes(recognition, features, registry, omissions):
        if outcome.state in {
            "placed",
            "satisfied_by_structured_note",
            "dropped",
            "inapplicable",
        }:
            continue
        issues.append(
            LintIssue(
                severity=severity,
                code=f"pad_requirement_{outcome.state}",
                message=(
                    f"raised pad at {outcome.source_at} {requirement_subject(outcome)} "
                    f"{messages[outcome.state]}"
                ),
            )
        )
    return issues
