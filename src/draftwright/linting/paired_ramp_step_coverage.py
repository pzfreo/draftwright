"""Semantic completeness for recognised paired-ramp-step requirements (#1382).

Each aggregate ``PairedRampStep`` owns two independently auditable requirements: the equal
ramp angle and the open-to-terminal run.  Exact axis/ridge/angle/run geometry joins the
provider record to one IR feature; parameter identities, never labels or page coordinates,
then follow each requirement to its drawing outcome.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal

from quiddity import PairedRampStep, RecognitionResult

from draftwright.contract_values import rounded as _rounded
from draftwright.linting._coverage_common import index_evidence as _index_evidence
from draftwright.linting._coverage_common import point3 as _point
from draftwright.linting._coverage_common import state as _state
from draftwright.linting._registry import (
    RequirementCarrier,
    with_measurement_carriers,
)
from draftwright.linting.issues import LintIssue

PairedRampRequirementState = Literal[
    "placed",
    "satisfied_by_structured_note",
    "suppressed",
    "dropped",
    "missing",
    "unverifiable",
]


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


def _has_parameters(feature) -> bool:
    try:
        return tuple(parameter.parameter_id for parameter in feature.parameters()) == (
            "ramp_angle.angle",
            "ramp_run.length",
        )
    except (AttributeError, TypeError):
        return False


def paired_ramp_step_requirement_outcomes(
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
) -> list[PairedRampRequirementOutcome]:
    """Follow both requirements of every recognised paired ramp to semantic outcomes."""
    if recognition is None:
        return []
    if not isinstance(recognition, RecognitionResult):
        raise TypeError(
            "paired_ramp_step_requirement_outcomes() requires the run's RecognitionResult; "
            f"got {type(recognition).__name__}"
        )
    sources = tuple(recognition.paired_ramp_steps)
    if not sources:
        return []

    keyed_sources: list[tuple[PairedRampStep, tuple | None]] = []
    source_counts: dict[tuple, int] = defaultdict(int)
    for source in sources:
        try:
            key = paired_ramp_step_key(source)
        except (AttributeError, TypeError, ValueError):
            key = None
        keyed_sources.append((source, key))
        if key is not None:
            source_counts[key] += 1

    ir_by_key: dict[tuple, list] = defaultdict(list)
    for feature in features:
        if getattr(feature, "kind", None) != "paired_ramp_step":
            continue
        try:
            ir_by_key[paired_ramp_step_key(feature)].append(feature)
        except (AttributeError, TypeError, ValueError):
            continue

    placed, satisfied, dropped = _index_evidence(registry)
    suppressed = {
        (omission.feature, omission.parameter_id)
        for omission in omissions
        if omission.feature is not None and omission.authored
    }
    outcomes: list[PairedRampRequirementOutcome] = []
    parameters = ("ramp_angle.angle", "ramp_run.length")
    for source, key in keyed_sources:
        matches = ir_by_key.get(key, ()) if key is not None else ()
        feature = (
            matches[0] if key is not None and len(matches) == source_counts[key] == 1 else None
        )
        if feature is None or not _has_parameters(feature):
            outcomes.extend(
                PairedRampRequirementOutcome(
                    _source_at(source),
                    parameter,
                    "unverifiable",
                    source_records=(source,),
                )
                for parameter in parameters
            )
            continue
        for parameter in parameters:
            state: PairedRampRequirementState = _state(
                feature,
                parameter,
                placed=placed,
                satisfied=satisfied,
                suppressed=suppressed,
                dropped=dropped,
                registry=registry,
            )
            outcomes.append(
                PairedRampRequirementOutcome(
                    _source_at(source),
                    parameter,
                    state,
                    features=(feature,),
                    source_records=(source,),
                )
            )
    return with_measurement_carriers(outcomes, registry)


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
    messages = {
        "suppressed": "was deliberately omitted by the authored dimension set",
        "missing": "has no placed, suppressed, or dropped callout outcome",
        "unverifiable": "cannot be joined to measurement provenance without guessing",
    }
    issues = []
    for outcome in paired_ramp_step_requirement_outcomes(
        recognition, features, registry, omissions
    ):
        if outcome.state in {"placed", "satisfied_by_structured_note", "dropped"}:
            continue
        issues.append(
            LintIssue(
                severity=severity,
                code=f"paired_ramp_step_requirement_{outcome.state}",
                message=(
                    f"paired-ramp {outcome.parameter_id} at {outcome.source_at} "
                    f"{messages[outcome.state]}"
                ),
            )
        )
    return issues
