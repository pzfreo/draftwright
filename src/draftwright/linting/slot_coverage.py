"""Semantic completeness for lone-slot and slot-pattern requirements (#1018 Gate 2).

Recognition owns physical cardinality: pattern members are removed from the lone-slot
inventory and represented by one pattern requirement. Engine outcomes are joined through
exact slot geometry, IR features, compiler measurement ids, and the annotation registry.
Presentation is never evidence.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

from draftwright.linting.issues import LintIssue
from draftwright.recognition import RecognitionResult

SlotRequirementState = Literal["placed", "suppressed", "dropped", "missing", "unverifiable"]
SlotSourceKind = Literal["slot", "slot_pattern"]


@dataclass(frozen=True)
class SlotRequirementOutcome:
    """The observable engine outcome of one physical slot measurement."""

    source_kind: SlotSourceKind
    source_at: tuple[float, float, float]
    member_count: int
    parameter_id: str
    state: SlotRequirementState


def _rounded(value) -> float:
    return round(float(value), 3)


def _point(value) -> tuple[float, float, float]:
    x, y, z = value
    return (_rounded(x), _rounded(y), _rounded(z))


def _slot_key(slot) -> tuple:
    """The slot facts both recognition and IR retain; deliberately excludes frame origin."""
    return (
        slot.width_axis,
        slot.long_axis,
        _rounded(slot.width),
        _rounded(slot.length),
        _rounded(slot.w_center),
        _rounded(slot.lo),
        _rounded(slot.hi),
    )


def _slot_spec_key(slot) -> tuple:
    """The representative facts a pattern consumes; its member position is inert."""
    return (
        slot.width_axis,
        slot.long_axis,
        _rounded(slot.width),
        _rounded(slot.length),
    )


def _unoriented_direction(value) -> tuple[float, float, float] | None:
    x, y, z = (float(component) for component in value)
    norm = (x * x + y * y + z * z) ** 0.5
    if norm == 0:
        return None
    direction = (x / norm, y / norm, z / norm)
    first = next((component for component in direction if abs(component) > 1e-9), 1.0)
    if first < 0:
        direction = tuple(-component for component in direction)
    return _point(direction)


def _pattern_direction(value, members) -> tuple[float, float, float] | None:
    """Canonicalise an explicit axis or derive the default axis from its member line."""
    if value is None:
        value = tuple(members[-1][i] - members[0][i] for i in range(3))
    return _unoriented_direction(value)


def _grid_angle(value) -> float:
    """A rectangular lattice is unchanged by reversing both of its basis directions."""
    return _rounded(float(value or 0.0) % 180.0)


def _slot_source_at(slot) -> tuple[float, float, float]:
    return _point(slot.location)


def _pattern_key(pattern) -> tuple:
    source_members = getattr(pattern, "slots", None)
    if source_members is not None:
        members = tuple(sorted(_slot_source_at(slot) for slot in source_members))
        member = source_members[0]
        pitch = getattr(pattern, "pitch", None)
        grid = (
            getattr(pattern, "row_pitch", None),
            getattr(pattern, "col_pitch", None),
        )
        pattern_kind = "grid" if grid[0] is not None else "linear"
    else:
        members = tuple(sorted(_point(point) for point in pattern.members))
        member = pattern.member
        pitch = getattr(pattern, "pitch", None)
        grid = getattr(pattern, "grid", None) or (None, None)
        pattern_kind = pattern.pattern
    direction = getattr(pattern, "direction", None)
    return (
        pattern_kind,
        len(members),
        _slot_spec_key(member),
        members,
        None if pitch is None else _rounded(pitch),
        _pattern_direction(direction, members) if pattern_kind == "linear" else None,
        tuple(None if value is None else _rounded(value) for value in grid),
        getattr(pattern, "rows", None),
        getattr(pattern, "cols", None),
        _grid_angle(getattr(pattern, "angle", None)) if pattern_kind == "grid" else None,
    )


def _pattern_source_at(pattern) -> tuple[float, float, float]:
    center = getattr(pattern, "center", None)
    if center is not None:
        return _point(center)
    points = [_slot_source_at(slot) for slot in pattern.slots]
    return (
        _rounded(sum(point[0] for point in points) / len(points)),
        _rounded(sum(point[1] for point in points) / len(points)),
        _rounded(sum(point[2] for point in points) / len(points)),
    )


def _parameter_ids(feature, *, pattern: bool) -> tuple[str, ...] | None:
    """Derive the compiler vocabulary from the matched feature, not a parallel id table."""
    parameters = tuple(feature.parameters())
    by_role: dict[str, list[str]] = defaultdict(list)
    for parameter in parameters:
        by_role[parameter.role].append(parameter.parameter_id)

    required_roles = ["slot_width", "slot_length"]
    if pattern:
        if feature.pattern == "linear":
            required_roles.append("pitch")
        else:  # exact source correspondence already proved this is the physical grid
            required_roles.extend(("grid_pitch", "grid_pitch"))
        location_stem = getattr(feature, "LOCATION_STEM", None)
        if feature.frame.axis != "z" or location_stem is None:
            return None
    else:
        stem = getattr(feature, "LOCATION_STEM", None)
        if stem is None:
            return None
        by_role[stem].append(f"{stem}.length")
        required_roles.append(stem)

    expected_counts = {role: required_roles.count(role) for role in set(required_roles)}
    if any(len(by_role.get(role, ())) != count for role, count in expected_counts.items()):
        return None
    ids = [parameter.parameter_id for parameter in parameters if parameter.role in expected_counts]
    if pattern:
        ids.extend(f"{feature.LOCATION_STEM}.location.{axis}" for axis in ("x", "y"))
    else:
        ids.append(f"{feature.LOCATION_STEM}.length")
    return tuple(ids)


def _matches(measurement, feature, parameter: str) -> bool:
    return (
        getattr(measurement, "feature", None) == feature
        and getattr(measurement, "parameter", None) == parameter
    )


def _state(feature, parameter, *, placed, suppressed, dropped, registry):
    if any(_matches(measurement, feature, parameter) for measurement in placed):
        return "placed"
    if (feature, parameter) in suppressed:
        return "suppressed"
    if any(_matches(measurement, feature, parameter) for measurement in dropped):
        return "dropped"
    associated = registry.names_for_feature(feature)
    if any(not registry.measurement_of(name) for name in associated):
        return "unverifiable"
    return "missing"


def slot_requirement_outcomes(
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
) -> list[SlotRequirementOutcome]:
    """Follow each recognised lone slot or grouped pattern to compiler/placement outcomes."""
    if recognition is None:
        return []
    if not isinstance(recognition, RecognitionResult):
        raise TypeError(
            "slot_requirement_outcomes() requires the run's RecognitionResult; "
            f"got {type(recognition).__name__}"
        )

    pattern_members = {member for pattern in recognition.slot_patterns for member in pattern.slots}
    sources: list[tuple[SlotSourceKind, object, tuple, tuple[float, float, float], int]] = [
        ("slot", slot, _slot_key(slot), _slot_source_at(slot), 1)
        for slot in recognition.slots
        if slot not in pattern_members
    ]
    sources.extend(
        (
            "slot_pattern",
            pattern,
            _pattern_key(pattern),
            _pattern_source_at(pattern),
            len(pattern.slots),
        )
        for pattern in recognition.slot_patterns
    )
    if not sources:
        return []

    source_counts: dict[tuple[str, tuple], int] = defaultdict(int)
    for kind, _source, key, _at, _count in sources:
        source_counts[(kind, key)] += 1
    ir_by_key: dict[tuple[str, tuple], list] = defaultdict(list)
    for feature in features:
        feature_kind = getattr(feature, "kind", None)
        if feature_kind == "slot":
            ir_by_key[(feature_kind, _slot_key(feature))].append(feature)
        elif feature_kind == "slot_pattern":
            ir_by_key[(feature_kind, _pattern_key(feature))].append(feature)

    placed = {
        measurement for name in registry.names() for measurement in registry.measurement_of(name)
    }
    suppressed = {
        (omission.feature, omission.parameter_id)
        for omission in omissions
        if omission.feature is not None and omission.authored
    }
    dropped = {
        measurement
        for issue in registry.issues
        for measurement in getattr(issue, "measurement_ids", ())
    }

    outcomes: list[SlotRequirementOutcome] = []
    for kind, _source, key, at, member_count in sources:
        matches = ir_by_key.get((kind, key), ())
        feature = matches[0] if len(matches) == source_counts[(kind, key)] == 1 else None
        parameter_ids = (
            _parameter_ids(feature, pattern=kind == "slot_pattern") if feature else None
        )
        if parameter_ids is None:
            # The association or required compiler vocabulary is not provable. Keep the
            # physical item visible as one unchecked outcome rather than inventing ids.
            outcomes.append(SlotRequirementOutcome(kind, at, member_count, "?", "unverifiable"))
            continue
        outcomes.extend(
            SlotRequirementOutcome(
                kind,
                at,
                member_count,
                parameter,
                _state(
                    feature,
                    parameter,
                    placed=placed,
                    suppressed=suppressed,
                    dropped=dropped,
                    registry=registry,
                ),
            )
            for parameter in parameter_ids
        )
    return outcomes


def lint_slot_coverage(
    part,
    *,
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
    assembly=None,
) -> list[LintIssue]:
    """Report uncovered slot requirements without duplicating placement-drop issues."""
    if assembly is None:
        assembly = len(part.solids()) > 1
    severity: Literal["info", "warning"] = "info" if assembly else "warning"
    messages = {
        "suppressed": "was deliberately omitted by the authored dimension set",
        "missing": "has no placed, suppressed, or dropped measurement outcome",
        "unverifiable": "cannot be joined to measurement provenance without guessing",
    }
    issues = []
    for outcome in slot_requirement_outcomes(recognition, features, registry, omissions):
        if outcome.state in {"placed", "dropped"}:
            continue
        noun = "slot" if outcome.source_kind == "slot" else f"{outcome.member_count}-slot pattern"
        issues.append(
            LintIssue(
                severity=severity,
                code=f"slot_requirement_{outcome.state}",
                message=(
                    f"{noun} at {outcome.source_at} measurement {outcome.parameter_id} "
                    f"{messages[outcome.state]}"
                ),
            )
        )
    return issues
