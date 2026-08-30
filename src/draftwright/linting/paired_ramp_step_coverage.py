"""Semantic completeness for recognised paired-ramp-step requirements (#1382).

Each aggregate ``PairedRampStep`` owns two independently auditable requirements: the equal
ramp angle and the open-to-terminal run.  Exact axis/ridge/angle/run geometry joins the
provider record to one IR feature; parameter identities, never labels or page coordinates,
then follow each requirement to its drawing outcome.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

from b123d_recognisers import PairedRampStep, RecognitionResult

from draftwright.linting._registry import satisfaction_ids, satisfaction_of
from draftwright.linting.issues import LintIssue, is_placement_drop

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


def _rounded(value) -> float:
    return round(float(value), 3)


def _point(value) -> tuple[float, float, float]:
    x, y, z = value
    return (_rounded(x), _rounded(y), _rounded(z))


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
                PairedRampRequirementOutcome(_source_at(source), parameter, "unverifiable")
                for parameter in parameters
            )
            continue
        for parameter in parameters:
            identity = (feature, parameter)
            if identity in placed:
                state: PairedRampRequirementState = "placed"
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
                PairedRampRequirementOutcome(
                    _source_at(source), parameter, state, features=(feature,)
                )
            )
    return outcomes


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
