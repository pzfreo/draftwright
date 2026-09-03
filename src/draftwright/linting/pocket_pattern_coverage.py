"""Semantic completeness for recognised blind-pocket patterns (#1372).

Recognition owns one physical pattern requirement rather than N member-pocket requirements.
Exact retained member geometry joins that source to one ``PocketPatternFeature``; compiler
``DimensionId`` values, directional location facts, and explicit count provenance then join
the feature to placed, suppressed, dropped, missing, or unverifiable outcomes. Presentation,
annotation names, views, and page coordinates are never correspondence evidence.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from math import hypot
from typing import Literal

from b123d_recognisers import RecognitionResult

from draftwright._core import _decode_hole_location_fact
from draftwright.linting._registry import satisfaction_ids, satisfaction_of
from draftwright.linting.issues import LintIssue, is_placement_drop

PocketPatternRequirementState = Literal[
    "placed",
    "satisfied_by_structured_note",
    "suppressed",
    "dropped",
    "missing",
    "unverifiable",
]


@dataclass(frozen=True)
class PocketPatternRequirementOutcome:
    """The observable engine outcome of one physical pocket-pattern measurement."""

    source_at: tuple[float, float, float]
    member_count: int
    parameter_id: str
    state: PocketPatternRequirementState
    requirement_count: int = 1
    members: tuple[tuple[float, float, float], ...] = ()
    features: tuple = ()
    source_records: tuple[object, ...] = field(default=(), repr=False, compare=False, kw_only=True)


def _rounded(value) -> float:
    # Pocket records retain their profile coordinates at 0.01 mm. Comparing in that same
    # public-record space admits a declared rotated lattice without widening the join beyond
    # what recognition itself can distinguish.
    return round(float(value), 2)


def _point(value) -> tuple[float, float, float]:
    x, y, z = value
    return (_rounded(x), _rounded(y), _rounded(z))


def _depth_axis(pocket) -> str:
    return next(axis for axis in "xyz" if axis not in (pocket.width_axis, pocket.long_axis))


def _member_spec(pocket) -> tuple:
    return (
        pocket.width_axis,
        pocket.long_axis,
        _depth_axis(pocket),
        _rounded(pocket.width),
        _rounded(pocket.length),
        _rounded(pocket.depth),
        int(getattr(pocket, "open_sign", 1)),
        bool(getattr(pocket, "edge_anchored", False)),
    )


def _unoriented_direction(value) -> tuple[float, float, float] | None:
    if value is None:
        return None
    vector = tuple(float(component) for component in value)
    norm = hypot(*vector)
    if norm == 0.0:
        return None
    direction = tuple(component / norm for component in vector)
    first = next((component for component in direction if abs(component) > 1e-9), 1.0)
    if first < 0:
        direction = tuple(-component for component in direction)
    return _point(direction)


def pocket_pattern_kind(pattern) -> str:
    if hasattr(pattern, "row_pitch"):
        return "grid"
    return str(getattr(pattern, "pattern", "linear"))


def pocket_pattern_members(pattern) -> tuple[tuple[float, float, float], ...]:
    source = getattr(pattern, "pockets", None)
    points = (
        (pocket.location for pocket in source)
        if source is not None
        else (point for point in pattern.members)
    )
    return tuple(sorted(_point(point) for point in points))


def pocket_pattern_source_at(pattern) -> tuple[float, float, float]:
    center = getattr(pattern, "center", None)
    if center is not None:
        return _point(center)
    members = pocket_pattern_members(pattern)
    return tuple(
        _rounded(sum(point[index] for point in members) / len(members)) for index in range(3)
    )  # type: ignore[return-value]


def pocket_pattern_key(pattern) -> tuple:
    """Facts retained identically by public recognition records and the Draftwright IR."""
    source = getattr(pattern, "pockets", None)
    member = source[0] if source is not None else pattern.member
    kind = pocket_pattern_kind(pattern)
    pitch = getattr(pattern, "pitch", None)
    if source is not None:
        grid = (
            getattr(pattern, "row_pitch", None),
            getattr(pattern, "col_pitch", None),
        )
    else:
        grid = getattr(pattern, "grid", None) or (None, None)
    return (
        kind,
        len(pocket_pattern_members(pattern)),
        _member_spec(member),
        pocket_pattern_members(pattern),
        None if pitch is None else _rounded(pitch),
        _unoriented_direction(getattr(pattern, "direction", None)) if kind == "linear" else None,
        tuple(None if value is None else _rounded(value) for value in grid),
        getattr(pattern, "rows", None),
        getattr(pattern, "cols", None),
        _rounded(getattr(pattern, "angle", 0.0) or 0.0) if kind == "grid" else None,
    )


def _parameter_ids(feature) -> tuple[str, ...] | None:
    """Derive the required vocabulary from the matched feature and its compiler contract."""
    try:
        parameters = tuple(feature.parameters())
    except (AttributeError, TypeError):
        return None
    expected = [
        "pocket_width.length",
        "pocket_length.length",
        "pocket_depth.length",
    ]
    if feature.pattern == "linear":
        expected.append("pitch.length")
    else:
        expected.extend(("grid_pitch.length.row", "grid_pitch.length.col"))
    if [parameter.parameter_id for parameter in parameters] != expected:
        return None
    stem = getattr(feature, "LOCATION_STEM", None)
    # Off-axis pattern locations have never had a compiler owner (ADR 0016). Retain the
    # recognised physical source as unverifiable rather than certifying a partial drawing.
    if feature.frame.axis != "z" or not isinstance(stem, str):
        return None
    return (
        "grouping.count",
        *expected,
        f"{stem}.location.x",
        f"{stem}.location.y",
    )


def _evidence_parameter(parameter: str) -> str:
    if parameter.startswith("location_pocket_pattern.location."):
        return "location_pocket_pattern.location"
    return parameter


def _index_evidence(registry):
    placed = {
        (measurement.feature, measurement.parameter)
        for name in registry.names()
        for measurement in registry.measurement_of(name)
    }
    locations: dict[tuple[object, str], set[tuple[float, float, float]]] = defaultdict(set)
    counts: dict[object, set[int]] = defaultdict(set)
    for name in registry.names():
        annotation = registry.named(name)
        owner = registry.feature_of(name)
        count = getattr(annotation, "covers_count", None)
        if getattr(owner, "kind", None) == "pocket_pattern" and isinstance(count, int):
            counts[owner].add(count)
        for fact in getattr(annotation, "covers_hole_locations", ()):
            decoded = _decode_hole_location_fact(fact)
            if decoded is None:
                continue
            feature, parameter, point = decoded
            if getattr(feature, "kind", None) == "pocket_pattern":
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
    return placed, locations, counts, satisfied, dropped


def _state(
    feature,
    parameter,
    *,
    point,
    member_count,
    placed,
    locations,
    counts,
    satisfied,
    suppressed,
    dropped,
    registry,
):
    evidence_parameter = _evidence_parameter(parameter)
    if parameter == "grouping.count":
        if counts.get(feature) == {member_count}:
            return "placed"
        if (feature, parameter) in satisfied:
            return "satisfied_by_structured_note"
        size_ids = {
            "pocket_width.length",
            "pocket_length.length",
            "pocket_depth.length",
        }
        if size_ids & {item for owner, item in suppressed if owner == feature}:
            return "suppressed"
        if size_ids & {item for owner, item in dropped if owner == feature}:
            return "dropped"
    elif parameter.startswith("location_pocket_pattern.location."):
        if point in locations.get((feature, parameter), ()):
            return "placed"
        if (feature, "location") in satisfied or (feature, evidence_parameter) in satisfied:
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
    # Count + W/L/D + one/two pitches + two absolute in-plane locations.
    return 7 if pocket_pattern_kind(source) == "linear" else 8


def pocket_pattern_requirement_outcomes(
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
) -> list[PocketPatternRequirementOutcome]:
    """Follow every recognised pocket-pattern requirement to its semantic outcome."""
    if recognition is None:
        return []
    if not isinstance(recognition, RecognitionResult):
        raise TypeError(
            "pocket_pattern_requirement_outcomes() requires the run's RecognitionResult; "
            f"got {type(recognition).__name__}"
        )
    sources = tuple(recognition.pocket_patterns)
    if not sources:
        return []
    source_counts: dict[tuple, int] = defaultdict(int)
    for source in sources:
        source_counts[pocket_pattern_key(source)] += 1
    ir_by_key: dict[tuple, list] = defaultdict(list)
    for feature in features:
        if getattr(feature, "kind", None) == "pocket_pattern":
            ir_by_key[pocket_pattern_key(feature)].append(feature)

    placed, locations, counts, satisfied, dropped = _index_evidence(registry)
    suppressed = {
        (omission.feature, omission.parameter_id)
        for omission in omissions
        if omission.feature is not None and omission.authored
    }
    outcomes: list[PocketPatternRequirementOutcome] = []
    for source in sources:
        key = pocket_pattern_key(source)
        matches = ir_by_key.get(key, ())
        feature = matches[0] if len(matches) == source_counts[key] == 1 else None
        parameter_ids = _parameter_ids(feature) if feature is not None else None
        at = pocket_pattern_source_at(source)
        members = pocket_pattern_members(source)
        member_count = len(members)
        if parameter_ids is None:
            outcomes.append(
                PocketPatternRequirementOutcome(
                    at,
                    member_count,
                    "?",
                    "unverifiable",
                    requirement_count=_physical_requirement_count(source),
                    members=members,
                    source_records=tuple(source.pockets),
                )
            )
            continue
        outcomes.extend(
            PocketPatternRequirementOutcome(
                at,
                member_count,
                parameter,
                _state(
                    feature,
                    parameter,
                    point=at,
                    member_count=member_count,
                    placed=placed,
                    locations=locations,
                    counts=counts,
                    satisfied=satisfied,
                    suppressed=suppressed,
                    dropped=dropped,
                    registry=registry,
                ),
                members=members,
                features=(feature,),
                source_records=tuple(source.pockets),
            )
            for parameter in parameter_ids
        )
    return outcomes


def lint_pocket_pattern_coverage(
    part,
    *,
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
    assembly=None,
) -> list[LintIssue]:
    """Report uncovered pattern requirements without duplicating placement drops."""
    if assembly is None:
        assembly = len(part.solids()) > 1
    severity: Literal["info", "warning"] = "info" if assembly else "warning"
    messages = {
        "suppressed": "was deliberately omitted by the authored dimension set",
        "missing": "has no placed, suppressed, or dropped measurement outcome",
        "unverifiable": "cannot be joined to measurement provenance without guessing",
    }
    issues = []
    for outcome in pocket_pattern_requirement_outcomes(recognition, features, registry, omissions):
        if outcome.state in {"placed", "satisfied_by_structured_note", "dropped"}:
            continue
        issues.append(
            LintIssue(
                severity=severity,
                code=f"pocket_pattern_requirement_{outcome.state}",
                message=(
                    f"{outcome.member_count}-pocket pattern at {outcome.source_at} "
                    f"measurement {outcome.parameter_id} {messages[outcome.state]}"
                ),
            )
        )
    return issues
