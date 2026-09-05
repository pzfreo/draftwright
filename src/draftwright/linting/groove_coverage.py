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
from dataclasses import dataclass, field
from typing import Literal

from b123d_recognisers import RecognitionResult

from draftwright.linting._registry import satisfaction_ids, satisfaction_of
from draftwright.linting.issues import (
    UNJOINED_PARAMETER_ID,
    LintIssue,
    is_placement_drop,
    requirement_subject,
)
from draftwright.recognition_frame import validated_groove_geometry

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
    #: False when the count above is a placeholder for an inventory whose size cannot be
    #: known (see the corrupt-inventory path below), so a message must not name a number.
    requirement_count_known: bool = True
    features: tuple = ()
    source_records: tuple[object, ...] = field(default=(), repr=False, compare=False, kw_only=True)


def groove_key(groove) -> tuple:
    """Facts retained identically by the public record and Draftwright IR."""
    axis, at, width, diameter = validated_groove_geometry(groove)
    return (
        axis,
        tuple(round(component, 3) for component in at),
        round(width, 3),
        round(diameter, 3),
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
    raw_sources = recognition.grooves
    if isinstance(raw_sources, tuple):
        sources = raw_sources
    else:
        try:
            sources = tuple(raw_sources)
        except Exception:
            # Cardinality is unknowable, so expose one aggregate contract outcome rather
            # than inventing a physical groove count or silently treating corruption as an
            # empty tuple.
            return [
                GrooveRequirementOutcome(
                    (0.0, 0.0, 0.0),
                    UNJOINED_PARAMETER_ID,
                    "unverifiable",
                    requirement_count=1,
                    # `requirement_count` is 1 because this is ONE aggregate contract outcome,
                    # not because one measurement is missing — the comment above says the
                    # cardinality is unknowable, and the denominator must still count the
                    # corruption. Flagging it keeps the message from naming a number that the
                    # count does not mean: it read "its sole physical measurement" until #1469.
                    requirement_count_known=False,
                )
            ]
        return [
            GrooveRequirementOutcome(
                (0.0, 0.0, 0.0),
                UNJOINED_PARAMETER_ID,
                "unverifiable",
                requirement_count=2,
                source_records=(_source,),
            )
            for _source in sources
        ]
    if not sources:
        return []
    source_counts: dict[tuple, int] = defaultdict(int)
    source_keys: list[tuple | None] = []
    for source in sources:
        try:
            key = groove_key(source)
        except (AttributeError, TypeError, ValueError):
            source_keys.append(None)
            continue
        source_keys.append(key)
        source_counts[key] += 1
    ir_by_key: dict[tuple, list] = defaultdict(list)
    malformed_ir = False
    for feature in features:
        if getattr(feature, "kind", None) == "groove":
            try:
                ir_by_key[groove_key(feature)].append(feature)
            except (AttributeError, TypeError, ValueError):
                malformed_ir = True

    placed, satisfied, dropped = _index_evidence(registry)
    suppressed = {
        (omission.feature, omission.parameter_id)
        for omission in omissions
        if omission.feature is not None and omission.authored
    }
    outcomes: list[GrooveRequirementOutcome] = []
    for _source, source_key in zip(sources, source_keys, strict=True):
        if source_key is None:
            outcomes.append(
                GrooveRequirementOutcome(
                    (0.0, 0.0, 0.0),
                    UNJOINED_PARAMETER_ID,
                    "unverifiable",
                    requirement_count=2,
                    source_records=(_source,),
                )
            )
            continue
        matches = ir_by_key.get(source_key, ())
        feature = matches[0] if len(matches) == source_counts[source_key] == 1 else None
        parameter_ids = _parameter_ids(feature) if feature is not None else None
        at = source_key[1]
        if parameter_ids is None or malformed_ir:
            outcomes.append(
                GrooveRequirementOutcome(
                    at,
                    UNJOINED_PARAMETER_ID,
                    "unverifiable",
                    requirement_count=2,
                    source_records=(_source,),
                )
            )
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
                source_records=(_source,),
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
                    f"groove at {outcome.source_at} {requirement_subject(outcome)} {messages[outcome.state]}"
                ),
            )
        )
    return issues
