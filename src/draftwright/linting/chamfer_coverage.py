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

from quiddity import RecognitionResult

from draftwright.contract_values import rounded as _rounded
from draftwright.linting._coverage_common import index_evidence as _index_evidence
from draftwright.linting._coverage_common import point3 as _point
from draftwright.linting._coverage_common import state as _state
from draftwright.linting._registry import (
    RequirementCarrier,
    with_measurement_carriers,
)
from draftwright.linting.issues import LintIssue

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
    carriers: tuple[RequirementCarrier, ...] = field(default=(), kw_only=True)


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
        state: ChamferRequirementState = _state(
            feature,
            parameter,
            placed=placed,
            satisfied=satisfied,
            suppressed=suppressed,
            dropped=dropped,
            registry=registry,
        )
        outcomes.append(
            ChamferRequirementOutcome(key[1], state, features=(feature,), source_records=(source,))
        )
    return with_measurement_carriers(outcomes, registry)


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
