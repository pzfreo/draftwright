"""Semantic completeness for recognised fillet requirements (#1374).

Each aggregate ``Fillet`` is one physical rounded edge with one radius callout. Exact
axis/location/surface-form geometry joins the provider record to one ``FilletFeature``;
the compiler's ``fillet.radius`` identity then follows that feature to an explicit drawing
outcome. Labels, annotation names, views, and page coordinates are not correspondence evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from quiddity import RecognitionResult

from draftwright.contract_values import rounded as _rounded
from draftwright.linting._coverage_common import point3 as _point
from draftwright.linting._parameter_coverage import (
    ParameterRequirementState as FilletRequirementState,
)
from draftwright.linting._parameter_coverage import (
    lint_parameter_coverage,
    parameter_outcomes,
)
from draftwright.linting._registry import RequirementCarrier
from draftwright.linting.issues import LintIssue


@dataclass(frozen=True)
class FilletRequirementOutcome:
    """The observable engine outcome of one physical fillet-radius callout."""

    source_at: tuple[float, float, float]
    state: FilletRequirementState
    requirement_count: int = 1
    features: tuple = ()
    parameter_id: str = "fillet.radius"
    source_records: tuple[object, ...] = field(default=(), repr=False, compare=False, kw_only=True)
    carriers: tuple[RequirementCarrier, ...] = field(default=(), kw_only=True)


def fillet_key(fillet) -> tuple:
    """Facts retained identically by the public record and Draftwright IR."""
    at = getattr(fillet, "at", None)
    if at is None:
        at = fillet.frame.origin
    return (
        str(fillet.axis),
        _point(at),
        bool(fillet.turned),
        _rounded(fillet.radius),
    )


def fillet_requirement_outcomes(
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
) -> list[FilletRequirementOutcome]:
    """Follow every recognised physical fillet to its semantic callout outcome."""
    return parameter_outcomes(
        recognition,
        features,
        registry,
        omissions,
        source_inventory="fillets",
        kind="fillet",
        key_fn=fillet_key,
        parameter_ids=("fillet.radius",),
        outcome_type=FilletRequirementOutcome,
        entrypoint="fillet_requirement_outcomes",
    )


def lint_fillet_coverage(
    part,
    *,
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
    assembly=None,
) -> list[LintIssue]:
    """Report uncovered fillet requirements without duplicating placement drops."""
    if assembly is None:
        assembly = len(part.solids()) > 1
    severity: Literal["info", "warning"] = "info" if assembly else "warning"
    return lint_parameter_coverage(
        fillet_requirement_outcomes(recognition, features, registry, omissions),
        issue_factory=lambda outcome, reason: LintIssue(
            severity=severity,
            code=f"fillet_requirement_{outcome.state}",
            message=f"fillet at {outcome.source_at} {reason}",
        ),
    )
