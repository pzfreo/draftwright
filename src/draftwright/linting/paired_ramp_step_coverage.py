"""Semantic completeness for recognised paired-ramp-step requirements (#1382).

Each aggregate ``PairedRampStep`` owns two independently auditable requirements: the equal
ramp angle and the open-to-terminal run.  Exact axis/ridge/angle/run geometry joins the
provider record to one IR feature; parameter identities, never labels or page coordinates,
then follow each requirement to its drawing outcome.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from quiddity import RecognitionResult

from draftwright.contract_values import rounded as _rounded
from draftwright.linting._coverage_common import point3 as _point
from draftwright.linting._parameter_coverage import (
    ParameterRequirementState as PairedRampRequirementState,
)
from draftwright.linting._parameter_coverage import (
    lint_parameter_coverage,
    parameter_outcomes,
)
from draftwright.linting._registry import RequirementCarrier
from draftwright.linting.issues import LintIssue


@dataclass(frozen=True)
class PairedRampRequirementOutcome:
    """The observable engine outcome of one angle or run requirement."""

    source_at: tuple[float, float, float]
    parameter_id: str
    state: PairedRampRequirementState
    requirement_count: int = 1
    features: tuple = ()
    source_records: tuple[object, ...] = field(default=(), repr=False, compare=False, kw_only=True)
    carriers: tuple[RequirementCarrier, ...] = field(default=(), kw_only=True)


def paired_ramp_step_key(step) -> tuple:
    """Facts retained identically by the public record and Draftwright IR."""
    at = getattr(step, "at", None)
    if at is None:
        at = step.frame.origin
    return (
        str(step.axis),
        _point(at),
        _rounded(step.angle),
        _rounded(step.length),
    )


def _source_at(source) -> tuple[float, float, float]:
    try:
        return _point(source.at)
    except (AttributeError, TypeError, ValueError):
        return (float("nan"), float("nan"), float("nan"))


def paired_ramp_step_requirement_outcomes(
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
) -> list[PairedRampRequirementOutcome]:
    """Follow both requirements of every recognised paired ramp to semantic outcomes."""
    return parameter_outcomes(
        recognition,
        features,
        registry,
        omissions,
        source_inventory="paired_ramp_steps",
        kind="paired_ramp_step",
        key_fn=paired_ramp_step_key,
        parameter_ids=("ramp_angle.angle", "ramp_run.length"),
        outcome_type=PairedRampRequirementOutcome,
        entrypoint="paired_ramp_step_requirement_outcomes",
        source_at_fn=_source_at,
        key_errors=(AttributeError, TypeError, ValueError),
    )


def lint_paired_ramp_step_coverage(
    part,
    *,
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
    assembly=None,
) -> list[LintIssue]:
    """Report uncovered paired-ramp requirements without duplicating placement drops."""
    if assembly is None:
        assembly = len(part.solids()) > 1
    severity: Literal["info", "warning"] = "info" if assembly else "warning"
    return lint_parameter_coverage(
        paired_ramp_step_requirement_outcomes(recognition, features, registry, omissions),
        issue_factory=lambda outcome, reason: LintIssue(
            severity=severity,
            code=f"paired_ramp_step_requirement_{outcome.state}",
            message=(f"paired-ramp {outcome.parameter_id} at {outcome.source_at} {reason}"),
        ),
    )
