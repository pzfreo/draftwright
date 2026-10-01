"""Semantic completeness for recognised chamfer requirements (#1374).

Each aggregate ``Chamfer`` is one physical bevel with one manufacturing callout. Exact
axis/location/surface-form geometry joins the provider record to one ``ChamferFeature``;
the compiler's ``chamfer.length`` identity then follows that feature to an explicit drawing
outcome. Labels, annotation names, views, and page coordinates are not correspondence
evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from quiddity import RecognitionResult

from draftwright.contract_values import rounded as _rounded
from draftwright.linting._coverage_common import point3 as _point
from draftwright.linting._parameter_coverage import (
    ParameterRequirementState as ChamferRequirementState,
)
from draftwright.linting._parameter_coverage import (
    lint_parameter_coverage,
    parameter_outcomes,
)
from draftwright.linting._registry import RequirementCarrier
from draftwright.linting.issues import LintIssue


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


def chamfer_requirement_outcomes(
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
) -> list[ChamferRequirementOutcome]:
    """Follow every recognised physical chamfer to its semantic callout outcome."""
    return parameter_outcomes(
        recognition,
        features,
        registry,
        omissions,
        source_inventory="chamfers",
        kind="chamfer",
        key_fn=chamfer_key,
        parameter_ids=("chamfer.length",),
        outcome_type=ChamferRequirementOutcome,
        entrypoint="chamfer_requirement_outcomes",
    )


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
    return lint_parameter_coverage(
        chamfer_requirement_outcomes(recognition, features, registry, omissions),
        issue_factory=lambda outcome, reason: LintIssue(
            severity=severity,
            code=f"chamfer_requirement_{outcome.state}",
            message=f"chamfer at {outcome.source_at} {reason}",
        ),
    )
