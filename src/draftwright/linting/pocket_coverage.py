"""Semantic completeness for recognised lone blind pockets (#1372).

Pattern members are owned by the pocket-pattern family and excluded here. Physical pockets
join to IR through exact retained geometry, then to compiler/placement outcomes through
``DimensionId`` values and structured directional location facts. Labels, annotation names,
views, projections, and page coordinates are never evidence.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal

from b123d_recognisers import RecognitionResult

from draftwright._core import _decode_hole_location_fact
from draftwright.linting._registry import satisfaction_ids, satisfaction_of
from draftwright.linting.issues import LintIssue, is_placement_drop, requirement_subject

_POCKET_LOCATION_DATUM_COINCIDENT_CODE = "pocket_location_coincident_with_datum"

PocketRequirementState = Literal[
    "placed",
    "satisfied_by_structured_note",
    "suppressed",
    "dropped",
    "missing",
    "unverifiable",
    "inapplicable",
]


@dataclass(frozen=True)
class PocketRequirementOutcome:
    """The observable engine outcome of one physical lone-pocket measurement."""

    source_at: tuple[float, float, float]
    parameter_id: str
    state: PocketRequirementState
    requirement_count: int = 1
    features: tuple = ()
    source_records: tuple[object, ...] = field(default=(), repr=False, compare=False, kw_only=True)


def _rounded(value) -> float:
    return round(float(value), 3)


def _point(value) -> tuple[float, float, float]:
    x, y, z = value
    return (_rounded(x), _rounded(y), _rounded(z))


def _depth_axis(pocket) -> str:
    return next(axis for axis in "xyz" if axis not in (pocket.width_axis, pocket.long_axis))


def _key(pocket) -> tuple:
    """Every retained physical pocket fact, including its opening side."""
    return (
        pocket.width_axis,
        pocket.long_axis,
        _rounded(pocket.width),
        _rounded(pocket.length),
        _rounded(pocket.depth),
        _rounded(pocket.w_center),
        _rounded(pocket.lo),
        _rounded(pocket.hi),
        _point(pocket.location if hasattr(pocket, "location") else pocket.frame.origin),
        int(getattr(pocket, "open_sign", 1)),
        bool(getattr(pocket, "edge_anchored", False)),
    )


def _parameter_ids(feature) -> tuple[str, ...] | None:
    try:
        ids = tuple(parameter.parameter_id for parameter in feature.parameters())
    except (AttributeError, TypeError):
        return None
    required_sizes = (
        "pocket_width.length",
        "pocket_length.length",
        "pocket_depth.length",
    )
    if ids != required_sizes:
        return None
    stem = getattr(feature, "LOCATION_STEM", None)
    if not isinstance(stem, str):
        return None
    if feature.depth_axis == "z":
        locations = tuple(f"{stem}.location.{axis}" for axis in ("x", "y"))
    else:
        locations = tuple(f"{stem}.{axis}" for axis in (feature.long_axis, feature.width_axis))
    return (*ids, *locations)


def _evidence_parameter(parameter: str) -> str:
    # Location is one feature-level authored intent (ADR 4 (was 0016)), even when its rendered
    # evidence has directional identities.  Z-normal pockets use
    # ``location_pocket.location.x/y`` facts while side/front-opening pockets use
    # ``location_pocket.<axis>`` measurements, but omission from an authored set is
    # recorded once as ``location_pocket.location`` for both forms.
    if parameter.startswith("location_pocket."):
        return "location_pocket.location"
    return parameter


def _is_location(parameter: str) -> bool:
    return parameter.startswith("location_pocket.")


def _index_evidence(registry):
    placed = {
        (measurement.feature, measurement.parameter)
        for name in registry.names()
        for measurement in registry.measurement_of(name)
    }
    locations: dict[tuple[object, str], set[tuple[float, float, float]]] = defaultdict(set)
    for name in registry.names():
        annotation = registry.named(name)
        for fact in getattr(annotation, "covers_hole_locations", ()):
            decoded = _decode_hole_location_fact(fact)
            if decoded is None:
                continue
            feature, parameter, point = decoded
            if getattr(feature, "kind", None) == "pocket":
                locations[(feature, parameter)].add(_point(point))
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
    dropped.update(
        (feature, parameter)
        for issue in registry.issues
        if is_placement_drop(issue)
        for feature, parameter in getattr(issue, "hole_requirement_ids", ())
        if getattr(feature, "kind", None) == "pocket"
    )
    return placed, locations, satisfied, dropped


def _state(
    feature,
    parameter,
    *,
    point,
    placed,
    locations,
    satisfied,
    suppressed,
    inapplicable,
    dropped,
    registry,
):
    evidence_parameter = _evidence_parameter(parameter)
    if (feature, parameter) in inapplicable:
        return "inapplicable"
    if _is_location(parameter):
        if point in locations.get((feature, parameter), ()):
            return "placed"
        if (feature, parameter) in placed:
            return "placed"
        if (feature, "location") in satisfied:
            return "satisfied_by_structured_note"
    elif (feature, parameter) in placed:
        return "placed"
    elif (feature, parameter) in satisfied:
        return "satisfied_by_structured_note"
    if (feature, evidence_parameter) in suppressed:
        return "suppressed"
    if (feature, parameter) in dropped or (feature, evidence_parameter) in dropped:
        return "dropped"
    associated = registry.names_for_feature(feature)
    if any(
        not registry.measurement_of(name) and not satisfaction_of(registry, name)
        for name in associated
    ):
        return "unverifiable"
    return "missing"


def _physical_requirement_count(source) -> int:
    return 3 if source.edge_anchored else 5


def pocket_requirement_outcomes(
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
) -> list[PocketRequirementOutcome]:
    """Follow every recognised lone-pocket requirement to its semantic outcome."""
    if recognition is None:
        return []
    if not isinstance(recognition, RecognitionResult):
        raise TypeError(
            "pocket_requirement_outcomes() requires the run's RecognitionResult; "
            f"got {type(recognition).__name__}"
        )

    pattern_members = {
        member for pattern in recognition.pocket_patterns for member in pattern.pockets
    }
    sources = tuple(pocket for pocket in recognition.pockets if pocket not in pattern_members)
    if not sources:
        return []
    source_counts: dict[tuple, int] = defaultdict(int)
    for source in sources:
        source_counts[_key(source)] += 1
    ir_by_key: dict[tuple, list] = defaultdict(list)
    for feature in features:
        if getattr(feature, "kind", None) == "pocket":
            ir_by_key[_key(feature)].append(feature)

    placed, locations, satisfied, dropped = _index_evidence(registry)
    suppressed = {
        (omission.feature, omission.parameter_id)
        for omission in omissions
        if omission.feature is not None and omission.authored
    }
    inapplicable = {
        (omission.feature, omission.parameter_id)
        for omission in omissions
        if omission.feature is not None
        and getattr(omission, "code", None) == _POCKET_LOCATION_DATUM_COINCIDENT_CODE
    }
    outcomes: list[PocketRequirementOutcome] = []
    for source in sources:
        key = _key(source)
        matches = ir_by_key.get(key, ())
        feature = matches[0] if len(matches) == source_counts[key] == 1 else None
        parameter_ids = _parameter_ids(feature) if feature is not None else None
        at = _point(source.location)
        if parameter_ids is None:
            outcomes.append(
                PocketRequirementOutcome(
                    at,
                    "?",
                    "unverifiable",
                    requirement_count=_physical_requirement_count(source),
                    source_records=(source,),
                )
            )
            if source.edge_anchored:
                outcomes.extend(
                    PocketRequirementOutcome(
                        at, parameter, "inapplicable", source_records=(source,)
                    )
                    for parameter in (
                        "location_pocket.location.x",
                        "location_pocket.location.y",
                    )
                )
            continue
        if source.edge_anchored:
            inapplicable.update((feature, parameter) for parameter in parameter_ids[3:])
        outcomes.extend(
            PocketRequirementOutcome(
                at,
                parameter,
                _state(
                    feature,
                    parameter,
                    point=at,
                    placed=placed,
                    locations=locations,
                    satisfied=satisfied,
                    suppressed=suppressed,
                    inapplicable=inapplicable,
                    dropped=dropped,
                    registry=registry,
                ),
                features=(feature,),
                source_records=(source,),
            )
            for parameter in parameter_ids
        )
    return outcomes


def lint_pocket_coverage(
    part,
    *,
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
    assembly=None,
) -> list[LintIssue]:
    """Report uncovered lone-pocket requirements without duplicating placement drops."""
    if assembly is None:
        assembly = len(part.solids()) > 1
    severity: Literal["info", "warning"] = "info" if assembly else "warning"
    messages = {
        "suppressed": "was deliberately omitted by the authored dimension set",
        "missing": "has no placed, suppressed, or dropped measurement outcome",
        "unverifiable": "cannot be joined to measurement provenance without guessing",
    }
    issues = []
    for outcome in pocket_requirement_outcomes(recognition, features, registry, omissions):
        if outcome.state in {
            "placed",
            "satisfied_by_structured_note",
            "dropped",
            "inapplicable",
        }:
            continue
        issues.append(
            LintIssue(
                severity=severity,
                code=f"pocket_requirement_{outcome.state}",
                message=(
                    f"blind pocket at {outcome.source_at} {_subject(outcome)} "
                    f"{messages[outcome.state]}"
                ),
            )
        )
    return issues


def _subject(outcome) -> str:
    """Name this outcome's requirement, or say plainly that no id exists (#1397)."""

    return requirement_subject(outcome.parameter_id, outcome.requirement_count, noun="measurement")
