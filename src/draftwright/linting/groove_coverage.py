"""Semantic completeness for recognised turned grooves (#1372).

Each aggregate ``Groove`` is one physical annular recess with two required measurements:
axial width and floor diameter.  Exact retained geometry joins the source to one
``GrooveFeature``; compiler ``DimensionId`` values and the annotation registry then join both
measurements to placed, explicitly satisfied, suppressed, dropped, missing, or unverifiable
outcomes.  Labels, annotation names, views, projections, and page coordinates are never
correspondence evidence.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

from b123d_recognisers import RecognitionResult

from draftwright.linting._registry import satisfaction_ids, satisfaction_of
from draftwright.linting.issues import LintIssue, is_placement_drop

GrooveRequirementState = Literal[
    "placed",
    "satisfied_by_structured_note",
    "suppressed",
    "dropped",
    "missing",
    "unverifiable",
]


@dataclass(frozen=True)
class GrooveRequirementOutcome:
    """The observable engine outcome of one physical groove measurement."""

    source_at: tuple[float, float, float]
    parameter_id: str
    state: GrooveRequirementState
    requirement_count: int = 1
    features: tuple = ()


def _rounded(value) -> float:
    return round(float(value), 3)


def _point(value) -> tuple[float, float, float]:
    x, y, z = value
    return (_rounded(x), _rounded(y), _rounded(z))


def groove_key(groove) -> tuple:
    """Facts retained identically by the public record and Draftwright IR."""
    at = getattr(groove, "at", None)
    if at is None:
        at = groove.frame.origin
    return (
        str(groove.axis),
        _point(at),
        _rounded(groove.width),
        _rounded(groove.diameter),
    )


def _parameter_ids(feature) -> tuple[str, str] | None:
    try:
        ids = tuple(parameter.parameter_id for parameter in feature.parameters())
    except (AttributeError, TypeError):
        return None
    expected = ("groove.length", "groove.diameter")
    return expected if ids == expected else None


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


def groove_requirement_outcomes(
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
) -> list[GrooveRequirementOutcome]:
    """Follow every recognised groove width and floor diameter to its semantic outcome."""
    if recognition is None:
        return []
    if not isinstance(recognition, RecognitionResult):
        raise TypeError(
            "groove_requirement_outcomes() requires the run's RecognitionResult; "
            f"got {type(recognition).__name__}"
        )
    sources = tuple(recognition.grooves)
    if not sources:
        return []
    source_counts: dict[tuple, int] = defaultdict(int)
    for source in sources:
        source_counts[groove_key(source)] += 1
    ir_by_key: dict[tuple, list] = defaultdict(list)
    for feature in features:
        if getattr(feature, "kind", None) == "groove":
            ir_by_key[groove_key(feature)].append(feature)

    placed, satisfied, dropped = _index_evidence(registry)
    suppressed = {
        (omission.feature, omission.parameter_id)
        for omission in omissions
        if omission.feature is not None and omission.authored
    }
    outcomes: list[GrooveRequirementOutcome] = []
    for source in sources:
        key = groove_key(source)
        matches = ir_by_key.get(key, ())
        feature = matches[0] if len(matches) == source_counts[key] == 1 else None
        parameter_ids = _parameter_ids(feature) if feature is not None else None
        at = key[1]
        if parameter_ids is None:
            outcomes.append(GrooveRequirementOutcome(at, "?", "unverifiable", requirement_count=2))
            continue
        outcomes.extend(
            GrooveRequirementOutcome(
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


def lint_groove_coverage(
    part,
    *,
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
    assembly=None,
) -> list[LintIssue]:
    """Report uncovered groove requirements without duplicating placement drops."""
    if assembly is None:
        assembly = len(part.solids()) > 1
    severity: Literal["info", "warning"] = "info" if assembly else "warning"
    messages = {
        "suppressed": "was deliberately omitted by the authored dimension set",
        "missing": "has no placed, suppressed, or dropped measurement outcome",
        "unverifiable": "cannot be joined to measurement provenance without guessing",
    }
    issues = []
    for outcome in groove_requirement_outcomes(recognition, features, registry, omissions):
        if outcome.state in {"placed", "satisfied_by_structured_note", "dropped"}:
            continue
        issues.append(
            LintIssue(
                severity=severity,
                code=f"groove_requirement_{outcome.state}",
                message=(
                    f"groove at {outcome.source_at} measurement {outcome.parameter_id} "
                    f"{messages[outcome.state]}"
                ),
            )
        )
    return issues
