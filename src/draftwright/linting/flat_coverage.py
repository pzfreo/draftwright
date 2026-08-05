"""Semantic completeness for machined-flat A/F requirements (#1018 Gate 1).

Physical ground truth comes from the shared recognition result. Engine outcomes are joined
through the existing IR ``FlatFeature`` and compiler ``DimensionId``; labels, leader tips,
projected geometry, annotation names, and page coordinates are never evidence.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

from draftwright._geometry import _canonical_axis_direction
from draftwright.linting.issues import LintIssue
from draftwright.recognition import RecognitionResult

FlatRequirementState = Literal["placed", "suppressed", "dropped", "missing", "unverifiable"]


@dataclass(frozen=True, order=True)
class _FlatRequirementKey:
    axis: str
    axis_direction: tuple[float, float, float]
    axis_line: tuple[float, float]
    stock_span: tuple[float, float]
    across: float


@dataclass(frozen=True)
class FlatRequirementOutcome:
    """The observable engine outcome of one physical A/F requirement."""

    axis: str
    axis_direction: tuple[float, float, float]
    axis_line: tuple[float, float]
    stock_span: tuple[float, float]
    across: float
    state: FlatRequirementState


def _rounded_pair(values) -> tuple[float, float]:
    first, second = values
    return (round(float(first), 3), round(float(second), 3))


def _key(flat) -> _FlatRequirementKey:
    return _FlatRequirementKey(
        axis=flat.axis,
        axis_direction=_canonical_axis_direction(flat.axis, getattr(flat, "axis_direction", None)),
        axis_line=_rounded_pair(flat.axis_line),
        stock_span=_rounded_pair(flat.stock_span),
        across=round(float(flat.across), 3),
    )


def _source_point(flat) -> tuple[float, float, float]:
    point = getattr(flat, "at", None)
    if point is None:
        point = flat.frame.origin
    x, y, z = point
    return (round(float(x), 3), round(float(y), 3), round(float(z), 3))


def _matches(measurement, feature, parameter: str) -> bool:
    """Match a compiler identity without coupling linting to the model package."""
    return (
        getattr(measurement, "feature", None) == feature
        and getattr(measurement, "parameter", None) == parameter
    )


def flat_requirement_outcomes(
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
) -> list[FlatRequirementOutcome]:
    """Follow each recognised flat requirement to an explicit engine outcome.

    Two faces of one double-D/hex share a requirement; parallel, differently-directed, or
    disjoint coaxial stock does not. Direction + line + span identity and A/F define that
    physical grouping; each group's recognised 3-D face anchors then join to IR exactly. If
    that record-to-IR correspondence is absent, the result is ``unverifiable`` rather than a
    fuzzy match.
    """
    if recognition is None:
        return []
    if not isinstance(recognition, RecognitionResult):
        raise TypeError(
            "flat_requirement_outcomes() requires the run's RecognitionResult; "
            f"got {type(recognition).__name__}"
        )

    physical: dict[_FlatRequirementKey, set[tuple[float, float, float]]] = defaultdict(set)
    for flat in recognition.flats:
        physical[_key(flat)].add(_source_point(flat))
    if not physical:
        return []

    # Linting's ADR 0015 carve-out forbids importing the model package. The model remains
    # downstream evidence, not the physical inventory, and is read through the same duck-typed
    # boundary as the other coverage checks.
    ir_by_requirement: dict[_FlatRequirementKey, list] = defaultdict(list)
    for feature in features:
        if getattr(feature, "kind", None) != "flat":
            continue
        requirement = _key(feature)
        if _source_point(feature) in physical.get(requirement, ()):
            ir_by_requirement[requirement].append(feature)

    placed = {
        measurement for name in registry.names() for measurement in registry.measurement_of(name)
    }
    authored_suppressions = {
        (omission.feature, omission.parameter_id)
        for omission in omissions
        if omission.feature is not None and omission.authored
    }
    dropped = {
        measurement
        for issue in registry.issues
        if issue.code == "flat_dropped"
        for measurement in getattr(issue, "measurement_ids", ())
    }

    outcomes: list[FlatRequirementOutcome] = []
    for requirement in sorted(physical):
        matched = ir_by_requirement.get(requirement, [])
        if not matched:
            state: FlatRequirementState = "unverifiable"
        else:
            expected = [(feature, "flat.length") for feature in matched]
            if any(
                _matches(measurement, *requirement_id)
                for requirement_id in expected
                for measurement in placed
            ):
                state = "placed"
            elif all(requirement_id in authored_suppressions for requirement_id in expected):
                state = "suppressed"
            elif any(
                _matches(measurement, *requirement_id)
                for requirement_id in expected
                for measurement in dropped
            ):
                state = "dropped"
            else:
                associated_names = {
                    name for feature in matched for name in registry.names_for_feature(feature)
                }
                state = (
                    "unverifiable"
                    if any(not registry.measurement_of(name) for name in associated_names)
                    else "missing"
                )
        outcomes.append(
            FlatRequirementOutcome(
                axis=requirement.axis,
                axis_direction=requirement.axis_direction,
                axis_line=requirement.axis_line,
                stock_span=requirement.stock_span,
                across=requirement.across,
                state=state,
            )
        )
    return outcomes


def lint_flat_coverage(
    part,
    *,
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
    assembly=None,
) -> list[LintIssue]:
    """Report uncovered A/F requirements without duplicating placement-drop issues."""
    if assembly is None:
        assembly = len(part.solids()) > 1
    severity: Literal["info", "warning"] = "info" if assembly else "warning"
    issues: list[LintIssue] = []
    for outcome in flat_requirement_outcomes(recognition, features, registry, omissions):
        # `flat_dropped` is already a user-facing build issue. Its measurement identity is
        # what proves this outcome; emitting a second warning here would score one defect
        # twice, contrary to the existing coverage/drop de-duplication contract.
        if outcome.state in {"placed", "dropped"}:
            continue
        stock = (
            f"{outcome.axis.upper()} stock along {outcome.axis_direction} at axis line "
            f"{outcome.axis_line}, "
            f"span {outcome.stock_span}"
        )
        messages = {
            "suppressed": "was deliberately omitted by the authored dimension set",
            "missing": "has no placed, suppressed, or dropped A/F measurement outcome",
            "unverifiable": "cannot be joined to measurement provenance without guessing",
        }
        issues.append(
            LintIssue(
                severity=severity,
                code=f"flat_requirement_{outcome.state}",
                message=f"machined flat {outcome.across:g} A/F on {stock} {messages[outcome.state]}",
            )
        )
    return issues
