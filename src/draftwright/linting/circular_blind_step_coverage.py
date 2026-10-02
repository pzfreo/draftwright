"""Semantic completeness for recognised circular-blind-step requirements (#1382).

Each aggregate ``CircularBlindStep`` owns two independently auditable requirements: the
quarter-cylinder radius and its terminal-to-open blind depth. Exact oriented centreline and
section facts join the provider record to one IR feature; measurement identities then follow
each requirement to its drawing outcome without using labels or page coordinates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import hypot, isclose, isfinite
from typing import Literal, cast

from quiddity import CircularBlindStep, RecognitionResult

from draftwright._geometry import quantised_radius_agrees, quantised_span_agrees
from draftwright.linting._coverage_common import rounded_point as _point
from draftwright.linting._parameter_coverage import (
    ParameterRequirementState as CircularBlindStepRequirementState,
)
from draftwright.linting._parameter_coverage import (
    lint_parameter_coverage,
    parameter_outcomes,
)
from draftwright.linting._registry import RequirementCarrier
from draftwright.linting.issues import LintIssue


@dataclass(frozen=True)
class CircularBlindStepRequirementOutcome:
    """The observable engine outcome of one radius or blind-depth requirement."""

    source_at: tuple[float, float, float]
    parameter_id: str
    state: CircularBlindStepRequirementState
    requirement_count: int = 1
    features: tuple = ()
    source_records: tuple[object, ...] = field(default=(), repr=False, compare=False, kw_only=True)
    carriers: tuple[RequirementCarrier, ...] = field(default=(), kw_only=True)


def circular_blind_step_key(step, *, require_frame: bool = False) -> tuple:
    """Validated public correspondence facts retained identically by record and IR.

    Coercion alone is not schema evidence: booleans compare equal to 0/1, arbitrary axis
    strings compare equal to themselves, and short point tuples can otherwise join. Rebuild
    the public IR value to apply the complete geometry contract before admitting a key.
    """
    axis = step.axis
    if axis not in ("x", "y", "z"):
        raise ValueError
    if type(step.radius) not in (int, float) or type(step.length) not in (int, float):
        raise ValueError
    radius, length = float(step.radius), float(step.length)
    if not (isfinite(radius) and radius > 0 and isfinite(length) and length > 0):
        raise ValueError
    if any(type(value) not in (int, float) for point in step.centreline for value in point):
        raise ValueError
    centreline = tuple(tuple(float(value) for value in point) for point in step.centreline)
    if (
        len(centreline) != 2
        or any(len(point) != 3 for point in centreline)
        or not all(isfinite(value) for point in centreline for value in point)
    ):
        raise ValueError
    run_index = "xyz".index(axis)
    if any(
        not isclose(
            centreline[0][index],
            centreline[1][index],
            rel_tol=0.0,
            abs_tol=1e-9,
        )
        for index in range(3)
        if index != run_index
    ) or not quantised_span_agrees(centreline[0][run_index], centreline[1][run_index], length):
        raise ValueError
    if any(type(value) not in (int, float) for point in step.section for value in point):
        raise ValueError
    section = tuple(tuple(float(value) for value in point) for point in step.section)
    if (
        len(section) != 3
        or any(len(point) != 2 for point in section)
        or not all(isfinite(value) for point in section for value in point)
    ):
        raise ValueError
    first, centre, last = section
    first_delta = (first[0] - centre[0], first[1] - centre[1])
    last_delta = (last[0] - centre[0], last[1] - centre[1])
    first_changes = [
        index
        for index, value in enumerate(first_delta)
        if not isclose(value, 0, rel_tol=0.0, abs_tol=1e-9)
    ]
    last_changes = [
        index
        for index, value in enumerate(last_delta)
        if not isclose(value, 0, rel_tol=0.0, abs_tol=1e-9)
    ]
    if not (
        len(first_changes) == len(last_changes) == 1
        and first_changes[0] != last_changes[0]
        and quantised_radius_agrees(first, centre, radius)
        and quantised_radius_agrees(last, centre, radius)
    ):
        raise ValueError
    transverse = [index for index in range(3) if index != run_index]
    if any(
        not isclose(
            centreline[0][coordinate],
            centre[pair],
            rel_tol=0.0,
            abs_tol=1e-6,
        )
        for pair, coordinate in enumerate(transverse)
    ):
        raise ValueError

    radial = (
        (first[0] - centre[0]) + (last[0] - centre[0]),
        (first[1] - centre[1]) + (last[1] - centre[1]),
    )
    radial_scale = max(abs(radial[0]), abs(radial[1]))
    if not isfinite(radial_scale) or radial_scale == 0:  # pragma: no cover - schema guard
        raise ValueError
    unit = (radial[0] / radial_scale, radial[1] / radial_scale)
    unit_norm = hypot(*unit)
    radial_distance = radius / unit_norm
    section_point = (
        centre[0] + unit[0] * radial_distance,
        centre[1] + unit[1] * radial_distance,
    )
    anchor = [
        start + (end - start) / 2 if (start >= 0) == (end >= 0) else (start + end) / 2
        for start, end in zip(centreline[0], centreline[1], strict=True)
    ]
    anchor[transverse[0]], anchor[transverse[1]] = section_point
    if not all(isfinite(value) for value in anchor):  # pragma: no cover - stable finite maths
        raise ValueError
    if require_frame:
        if step.frame.axis != axis:
            raise ValueError
        if any(type(value) not in (int, float) for value in step.frame.origin):
            raise ValueError
        origin = tuple(float(value) for value in step.frame.origin)
        if len(origin) != 3 or not all(isfinite(value) for value in origin):
            raise ValueError
        if any(
            not isclose(actual, expected, rel_tol=0.0, abs_tol=1e-6)
            for actual, expected in zip(origin, anchor, strict=True)
        ):
            raise ValueError
    return (
        axis,
        radius,
        length,
        centreline,
        section,
    )


def _source_at(source) -> tuple[float, float, float]:
    try:
        return cast(tuple[float, float, float], _point(source.centreline[0]))
    except (AttributeError, IndexError, TypeError, ValueError, OverflowError):
        return (float("nan"), float("nan"), float("nan"))


def circular_blind_step_requirement_outcomes(
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
) -> list[CircularBlindStepRequirementOutcome]:
    """Follow both requirements of every recognised circular blind step."""
    return parameter_outcomes(
        recognition,
        features,
        registry,
        omissions,
        source_inventory="circular_blind_steps",
        kind="circular_blind_step",
        key_fn=circular_blind_step_key,
        feature_key_fn=lambda feature: circular_blind_step_key(feature, require_frame=True),
        source_at_fn=_source_at,
        source_type=CircularBlindStep,
        key_errors=(
            AttributeError,
            IndexError,
            OverflowError,
            TypeError,
            ValueError,
            ZeroDivisionError,
        ),
        parameter_errors=(AttributeError, TypeError, ValueError, OverflowError),
        parameter_ids=("circular_step_radius.radius", "circular_step_depth.length"),
        outcome_type=CircularBlindStepRequirementOutcome,
        entrypoint="circular_blind_step_requirement_outcomes",
    )


def lint_circular_blind_step_coverage(
    part,
    *,
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
    assembly=None,
) -> list[LintIssue]:
    """Report uncovered circular-step requirements without duplicating placement drops."""
    if assembly is None:
        assembly = len(part.solids()) > 1
    severity: Literal["info", "warning"] = "info" if assembly else "warning"
    return lint_parameter_coverage(
        circular_blind_step_requirement_outcomes(recognition, features, registry, omissions),
        issue_factory=lambda outcome, reason: LintIssue(
            severity=severity,
            code=f"circular_blind_step_requirement_{outcome.state}",
            message=(
                f"circular blind step {outcome.parameter_id} at {outcome.source_at} {reason}"
            ),
        ),
    )
