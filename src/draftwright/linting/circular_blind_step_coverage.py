"""Semantic completeness for recognised circular-blind-step requirements (#1382).

Each aggregate ``CircularBlindStep`` owns two independently auditable requirements: the
quarter-cylinder radius and its terminal-to-open blind depth. Exact oriented centreline and
section facts join the provider record to one IR feature; measurement identities then follow
each requirement to its drawing outcome without using labels or page coordinates.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from math import hypot, isclose, isfinite
from typing import Literal

from b123d_recognisers import CircularBlindStep, RecognitionResult

from draftwright.linting._registry import satisfaction_ids, satisfaction_of
from draftwright.linting.issues import LintIssue, is_placement_drop

CircularBlindStepRequirementState = Literal[
    "placed",
    "satisfied_by_structured_note",
    "suppressed",
    "dropped",
    "missing",
    "unverifiable",
]


@dataclass(frozen=True)
class CircularBlindStepRequirementOutcome:
    """The observable engine outcome of one radius or blind-depth requirement."""

    source_at: tuple[float, float, float]
    parameter_id: str
    state: CircularBlindStepRequirementState
    requirement_count: int = 1
    features: tuple = ()


def _rounded(value) -> float:
    return round(float(value), 3)


def _point(value) -> tuple:
    return tuple(_rounded(component) for component in value)


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
    ) or not isclose(
        abs(centreline[1][run_index] - centreline[0][run_index]),
        length,
        rel_tol=0.0,
        abs_tol=1e-6,
    ):
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
        and isclose(hypot(*first_delta), radius, rel_tol=0.0, abs_tol=1e-6)
        and isclose(hypot(*last_delta), radius, rel_tol=0.0, abs_tol=1e-6)
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
        return _point(source.centreline[0])
    except (AttributeError, IndexError, TypeError, ValueError, OverflowError):
        return (float("nan"), float("nan"), float("nan"))


def _has_parameters(feature) -> bool:
    try:
        return tuple(parameter.parameter_id for parameter in feature.parameters()) == (
            "circular_step_radius.radius",
            "circular_step_depth.length",
        )
    except (AttributeError, TypeError, ValueError, OverflowError):
        return False


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


def circular_blind_step_requirement_outcomes(
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
) -> list[CircularBlindStepRequirementOutcome]:
    """Follow both requirements of every recognised circular blind step."""
    if recognition is None:
        return []
    if not isinstance(recognition, RecognitionResult):
        raise TypeError(
            "circular_blind_step_requirement_outcomes() requires the run's RecognitionResult; "
            f"got {type(recognition).__name__}"
        )
    sources = tuple(recognition.circular_blind_steps)
    if not sources:
        return []

    keyed_sources: list[tuple[CircularBlindStep, tuple | None]] = []
    source_counts: dict[tuple, int] = defaultdict(int)
    for source in sources:
        try:
            if not isinstance(source, CircularBlindStep):
                raise TypeError
            key = circular_blind_step_key(source)
        except (
            AttributeError,
            IndexError,
            OverflowError,
            TypeError,
            ValueError,
            ZeroDivisionError,
        ):
            key = None
        keyed_sources.append((source, key))
        if key is not None:
            source_counts[key] += 1

    ir_by_key: dict[tuple, list] = defaultdict(list)
    for feature in features:
        if getattr(feature, "kind", None) != "circular_blind_step":
            continue
        try:
            ir_by_key[circular_blind_step_key(feature, require_frame=True)].append(feature)
        except (
            AttributeError,
            IndexError,
            OverflowError,
            TypeError,
            ValueError,
            ZeroDivisionError,
        ):
            continue

    placed, satisfied, dropped = _index_evidence(registry)
    suppressed = {
        (omission.feature, omission.parameter_id)
        for omission in omissions
        if omission.feature is not None and omission.authored
    }
    outcomes: list[CircularBlindStepRequirementOutcome] = []
    parameters = ("circular_step_radius.radius", "circular_step_depth.length")
    for source, key in keyed_sources:
        matches = ir_by_key.get(key, ()) if key is not None else ()
        feature = (
            matches[0] if key is not None and len(matches) == source_counts[key] == 1 else None
        )
        if feature is None or not _has_parameters(feature):
            outcomes.extend(
                CircularBlindStepRequirementOutcome(_source_at(source), parameter, "unverifiable")
                for parameter in parameters
            )
            continue
        for parameter in parameters:
            identity = (feature, parameter)
            if identity in placed:
                state: CircularBlindStepRequirementState = "placed"
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
                CircularBlindStepRequirementOutcome(
                    _source_at(source), parameter, state, features=(feature,)
                )
            )
    return outcomes


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
    messages = {
        "suppressed": "was deliberately omitted by the authored dimension set",
        "missing": "has no placed, suppressed, or dropped callout outcome",
        "unverifiable": "cannot be joined to measurement provenance without guessing",
    }
    issues = []
    for outcome in circular_blind_step_requirement_outcomes(
        recognition, features, registry, omissions
    ):
        if outcome.state in {"placed", "satisfied_by_structured_note", "dropped"}:
            continue
        issues.append(
            LintIssue(
                severity=severity,
                code=f"circular_blind_step_requirement_{outcome.state}",
                message=(
                    f"circular blind step {outcome.parameter_id} at {outcome.source_at} "
                    f"{messages[outcome.state]}"
                ),
            )
        )
    return issues
