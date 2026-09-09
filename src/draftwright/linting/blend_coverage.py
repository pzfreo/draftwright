"""Exact semantic completeness for released straight/circular Blend paths (#1433/#1438)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal

from quiddity import RecognitionResult

from draftwright._geometry import (
    _blend_profile_arcs,
    _projected_edge_distance,
    _straight_blend_faces,
)
from draftwright.blend_contract import (
    blend_provider_key,
    is_exact_blend_feature,
    validate_blend_fields,
)
from draftwright.linting._registry import satisfaction_ids, satisfaction_of
from draftwright.linting.issues import LintIssue, is_placement_drop

BlendRequirementState = Literal[
    "placed",
    "satisfied_by_structured_note",
    "suppressed",
    "dropped",
    "missing",
    "unverifiable",
]


@dataclass(frozen=True)
class BlendRequirementOutcome:
    """Observable result of one physical Blend chain's radius requirement."""

    source_at: tuple[float, float, float]
    state: BlendRequirementState
    requirement_count: int = 1
    features: tuple = ()
    parameter_id: str = "blend.radius"
    source_records: tuple[object, ...] = field(default=(), repr=False, compare=False, kw_only=True)


def _source_at(key: tuple | None) -> tuple[float, float, float]:
    """Diagnostic location only from a completely validated occurrence key."""
    return key[2] if key is not None else (0.0, 0.0, 0.0)


def blend_feature_key(feature) -> tuple:
    """Validate exact dedicated IR without importing across lint's IR carve-out."""
    if not is_exact_blend_feature(feature):
        raise TypeError("blend completeness requires exact BlendFeature and Frame values")
    frame = feature.frame
    axis, radius, at, side, direction, path_kind, path_radius = validate_blend_fields(
        axis=feature.axis,
        radius=feature.radius,
        at=frame.origin,
        side=feature.side,
        axis_direction=feature.axis_direction,
        path_kind=feature.path_kind,
        path_radius=feature.path_radius,
    )
    if frame.axis != axis:
        raise ValueError("blend feature frame axis disagrees with its public dominant axis")
    return axis, radius, at, side, direction, path_kind, path_radius


def _has_parameter(feature) -> bool:
    try:
        return tuple(parameter.parameter_id for parameter in feature.parameters()) == (
            "blend.radius",
        )
    except (AttributeError, TypeError, ValueError):
        return False


def _index_evidence(registry):
    placed = {
        (id(measurement.feature), measurement.parameter)
        for name in registry.names()
        for measurement in registry.measurement_of(name)
    }
    satisfied = {
        (id(identity.feature), identity.parameter)
        for identity in satisfaction_ids(registry)
        if identity.feature is not None and isinstance(identity.parameter, str)
    }
    dropped = {
        (id(measurement.feature), measurement.parameter)
        for issue in registry.issues
        if is_placement_drop(issue)
        for measurement in getattr(issue, "measurement_ids", ())
        if getattr(measurement, "feature", None) is not None
        and isinstance(getattr(measurement, "parameter", None), str)
    }
    return placed, satisfied, dropped


def blend_requirement_outcomes(
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
) -> list[BlendRequirementOutcome]:
    """Follow every accepted physical Blend occurrence to one explicit engine outcome."""
    if recognition is None:
        return []
    if type(recognition) is not RecognitionResult:
        raise TypeError(
            "blend_requirement_outcomes() requires the exact run RecognitionResult; "
            f"got {type(recognition).__name__}"
        )
    if type(recognition.blends) is not tuple:
        raise TypeError("RecognitionResult.blends must be an immutable tuple")

    keyed_sources: list[tuple[object, tuple | None]] = []
    source_counts: dict[tuple, int] = defaultdict(int)
    for source in recognition.blends:
        try:
            key = blend_provider_key(source)
        except (AttributeError, OverflowError, TypeError, ValueError):
            key = None
        keyed_sources.append((source, key))
        if key is not None:
            source_counts[key] += 1

    ir_by_key: dict[tuple, list] = defaultdict(list)
    for feature in features:
        if getattr(feature, "kind", None) != "blend":
            continue
        try:
            ir_by_key[blend_feature_key(feature)].append(feature)
        except (AttributeError, OverflowError, TypeError, ValueError):
            continue

    source_offsets: dict[tuple, int] = defaultdict(int)
    placed, satisfied, dropped = _index_evidence(registry)
    suppressed = {
        (id(omission.feature), omission.parameter_id)
        for omission in omissions
        if omission.feature is not None and omission.authored
    }
    outcomes: list[BlendRequirementOutcome] = []
    for source_record, key in keyed_sources:
        feature = None
        if key is not None:
            matches = ir_by_key.get(key, ())
            offset = source_offsets[key]
            source_offsets[key] += 1
            if (
                len(matches) == source_counts[key]
                and len({id(match) for match in matches}) == len(matches)
                and offset < len(matches)
            ):
                feature = matches[offset]
        if feature is None or not _has_parameter(feature):
            outcomes.append(
                BlendRequirementOutcome(
                    _source_at(key), "unverifiable", source_records=(source_record,)
                )
            )
            continue
        identity = (id(feature), "blend.radius")
        if identity in placed:
            state: BlendRequirementState = "placed"
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
            BlendRequirementOutcome(
                _source_at(key),
                state,
                features=(feature,),
                source_records=(source_record,),
            )
        )
    return outcomes


def lint_blend_coverage(
    part,
    *,
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
    assembly=None,
) -> list[LintIssue]:
    """Report Blend requirements without duplicating explicit placement-drop diagnostics."""
    if assembly is None:
        assembly = len(part.solids()) > 1
    severity: Literal["info", "warning"] = "info" if assembly else "warning"
    messages = {
        "suppressed": "was deliberately omitted by the authored dimension set",
        "missing": "has no placed, suppressed, or dropped radius outcome",
        "unverifiable": "cannot be joined to exact Blend provenance without guessing",
    }
    issues = []
    for outcome in blend_requirement_outcomes(recognition, features, registry, omissions):
        if outcome.state in {"placed", "satisfied_by_structured_note", "dropped"}:
            continue
        issues.append(
            LintIssue(
                severity=severity,
                code=f"blend_requirement_{outcome.state}",
                message=f"blend at {outcome.source_at} {messages[outcome.state]}",
            )
        )
    return issues


def lint_blend_leader_targets(*, registry, cylinders, project, evidence=None, ownership=None):
    """Judge placed straight-Blend radius tips against their physical trimmed arcs.

    The renderer's candidate site is not evidence here: lint measures the finished tip against
    the complete trimmed edge. Circular-path toroidal blends have a different target contract.
    """
    issues = []
    for name in sorted(registry.names()):
        measurements = registry.measurement_of(name)
        if not any(measurement.parameter == "blend.radius" for measurement in measurements):
            continue
        feature = registry.feature_of(name)
        annotation = registry.named(name)
        view = registry.view_of(name)
        tip = getattr(annotation, "tip", None)
        if tip is None:
            continue
        arcs = ()
        try:
            key = blend_feature_key(feature)
        except (AttributeError, OverflowError, TypeError, ValueError):
            key = None
        if key is not None and not any(
            measurement.feature is feature and measurement.parameter == "blend.radius"
            for measurement in measurements
        ):
            key = None
        if key is not None and feature.path_kind == "circular":
            continue
        if key is not None and view is not None:
            defining_faces = None
            if ownership is not None:
                defining_faces = ()
                if evidence is not None and ownership.evidence is evidence:
                    defining_faces = tuple(
                        evidence.face(ref)
                        for binding in ownership.bindings
                        if evidence.family(binding.occurrence) == "blends"
                        and any(owner is feature for owner in binding.features)
                        for ref in evidence.defining_faces(binding.occurrence)
                    )
            faces = _straight_blend_faces(
                feature, cylinders, feature.radius, defining_faces=defining_faces
            )
            arcs = _blend_profile_arcs(faces, feature.radius)
        if not arcs:
            if key is None:
                reason = "feature-backed blend measurement unavailable"
            elif view is None:
                reason = "annotation view unavailable"
            else:
                reason = "trimmed profile arc evidence unavailable"
            issues.append(
                LintIssue(
                    severity="warning",
                    code="radius_leader_target_unverifiable",
                    message=f"{name}: the radius leader's physical profile arc cannot be verified",
                    location=tip,
                    measurement_ids=measurements,
                    annotation_name=name,
                    view=view,
                    evidence_reason=reason,
                )
            )
        elif (
            min(
                _projected_edge_distance(edge, tip, lambda x, y, z: project(view, x, y, z))
                for edge in arcs
            )
            > 2e-3
        ):
            issues.append(
                LintIssue(
                    severity="warning",
                    code="radius_leader_target_mismatch",
                    message=f"{name}: the radius leader tip does not touch its trimmed profile arc",
                    location=tip,
                    measurement_ids=measurements,
                    annotation_name=name,
                    view=view,
                    evidence_reason="drawn tip is outside the trimmed physical profile arc tolerance",
                )
            )
    return issues
