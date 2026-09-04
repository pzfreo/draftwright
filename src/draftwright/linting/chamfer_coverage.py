"""Semantic completeness for recognised chamfer requirements (#1374).

Each aggregate ``Chamfer`` is one physical bevel with one manufacturing callout. Exact
axis/location/surface-form geometry joins the provider record to one ``ChamferFeature``;
the compiler's ``chamfer.length`` identity then follows that feature to an explicit drawing
outcome. Labels, annotation names, views, and page coordinates are not correspondence
evidence.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal

from b123d_recognisers import RecognitionResult

from draftwright.linting._registry import satisfaction_ids, satisfaction_of
from draftwright.linting.issues import LintIssue, is_placement_drop

ChamferRequirementState = Literal[
    "placed",
    "satisfied_by_structured_note",
    "suppressed",
    "dropped",
    "missing",
    "unverifiable",
]


@dataclass(frozen=True)
class ChamferRequirementOutcome:
    """The observable engine outcome of one physical chamfer callout."""

    source_at: tuple[float, float, float]
    state: ChamferRequirementState
    requirement_count: int = 1
    features: tuple = ()
    parameter_id: str = "chamfer.length"
    source_records: tuple[object, ...] = field(default=(), repr=False, compare=False, kw_only=True)


def _rounded(value) -> float:
    return round(float(value), 3)


def _point(value) -> tuple[float, float, float]:
    x, y, z = value
    return (_rounded(x), _rounded(y), _rounded(z))


def chamfer_key(chamfer) -> tuple:
    """Facts retained identically by the public record and Draftwright IR."""
    at = getattr(chamfer, "at", None)
    if at is None:
        at = chamfer.frame.origin
    return (
        str(chamfer.axis),
        _point(at),
        bool(chamfer.turned),
        _rounded(chamfer.leg1),
        _rounded(chamfer.leg2),
        round(float(chamfer.angle), 2),
    )


def _has_parameter(feature) -> bool:
    try:
        return tuple(parameter.parameter_id for parameter in feature.parameters()) == (
            "chamfer.length",
        )
    except (AttributeError, TypeError):
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


def chamfer_requirement_outcomes(
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
) -> list[ChamferRequirementOutcome]:
    """Follow every recognised physical chamfer to its semantic callout outcome."""
    if recognition is None:
        return []
    if not isinstance(recognition, RecognitionResult):
        raise TypeError(
            "chamfer_requirement_outcomes() requires the run's RecognitionResult; "
            f"got {type(recognition).__name__}"
        )
    sources = tuple(recognition.chamfers)
    if not sources:
        return []

    source_counts: dict[tuple, int] = defaultdict(int)
    for source in sources:
        source_counts[chamfer_key(source)] += 1
    ir_by_key: dict[tuple, list] = defaultdict(list)
    for feature in features:
        if getattr(feature, "kind", None) == "chamfer":
            ir_by_key[chamfer_key(feature)].append(feature)

    placed, satisfied, dropped = _index_evidence(registry)
    suppressed = {
        (omission.feature, omission.parameter_id)
        for omission in omissions
        if omission.feature is not None and omission.authored
    }
    outcomes: list[ChamferRequirementOutcome] = []
    parameter = "chamfer.length"
    for source in sources:
        key = chamfer_key(source)
        matches = ir_by_key.get(key, ())
        feature = matches[0] if len(matches) == source_counts[key] == 1 else None
        if feature is None or not _has_parameter(feature):
            outcomes.append(
                ChamferRequirementOutcome(key[1], "unverifiable", source_records=(source,))
            )
            continue
        identity = (feature, parameter)
        if identity in placed:
            state: ChamferRequirementState = "placed"
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
            ChamferRequirementOutcome(key[1], state, features=(feature,), source_records=(source,))
        )
    return outcomes


def lint_chamfer_coverage(
    part,
    *,
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
    assembly=None,
) -> list[LintIssue]:
    """Report uncovered chamfer requirements without duplicating placement drops."""
    if assembly is None:
        assembly = len(part.solids()) > 1
    severity: Literal["info", "warning"] = "info" if assembly else "warning"
    messages = {
        "suppressed": "was deliberately omitted by the authored dimension set",
        "missing": "has no placed, suppressed, or dropped callout outcome",
        "unverifiable": "cannot be joined to measurement provenance without guessing",
    }
    issues = []
    for outcome in chamfer_requirement_outcomes(recognition, features, registry, omissions):
        if outcome.state in {"placed", "satisfied_by_structured_note", "dropped"}:
            continue
        issues.append(
            LintIssue(
                severity=severity,
                code=f"chamfer_requirement_{outcome.state}",
                message=f"chamfer at {outcome.source_at} {messages[outcome.state]}",
            )
        )
    return issues
