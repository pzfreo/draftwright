"""Evidence and outcome operations shared by coverage checks."""

from collections import defaultdict
from math import isfinite
from numbers import Real
from typing import Literal

from draftwright.contract_values import rounded as _rounded
from draftwright.linting._registry import (
    RequirementCarrier,
    measurement_outcome_index,
    satisfaction_of,
)
from draftwright.linting.issues import is_placement_drop
from draftwright.section_recess_contract import recesses_with_kind, section_recess_fields

# Stable coverage import path; the registry evidence has one implementation owner.
index_evidence = measurement_outcome_index


def section_recess_sources(recognition, kind: str) -> tuple:
    """Select one published recess grammar from an immutable recognition inventory."""
    if not isinstance(recognition.section_recesses, tuple):
        raise TypeError("RecognitionResult.section_recesses must be an immutable tuple")
    return recesses_with_kind(recognition.section_recesses, kind)


def index_pad_pocket_evidence(registry, *, family: Literal["pad", "pocket"], carriers=None):
    """Index exact claims and family-owned physical locations and drops."""
    from draftwright._core import _decode_hole_location_fact

    if family not in ("pad", "pocket"):
        raise ValueError(f"unsupported pad/pocket evidence family: {family!r}")
    placed, satisfied, dropped = measurement_outcome_index(registry)
    locations: dict[tuple[object, str], set[tuple[float, float, float]]] = defaultdict(set)
    for name in registry.names():
        annotation = registry.named(name)
        for fact in getattr(annotation, "covers_hole_locations", ()):
            decoded = _decode_hole_location_fact(fact)
            if decoded is None:
                continue
            feature, parameter, point = decoded
            if getattr(feature, "kind", None) == family:
                locations[(feature, parameter)].add(point3(point))
                if carriers is not None:
                    carriers.locations[(feature, parameter, point3(point))].append(
                        RequirementCarrier(name, "physical_location")
                    )
    dropped.update(
        (feature, parameter)
        for issue in registry.issues
        if is_placement_drop(issue)
        for feature, parameter in getattr(issue, "hole_requirement_ids", ())
        if getattr(feature, "kind", None) == family
    )
    return placed, locations, satisfied, dropped


def rounded_point(value) -> tuple[float, ...]:
    return tuple(_rounded(component) for component in value)


def point3(value) -> "tuple[float, float, float]":
    x, y, z = value
    return (_rounded(x), _rounded(y), _rounded(z))


def recess_rounded(value) -> float:
    """Round a blind-slot fact, rejecting non-finite and unconvertible values."""
    try:
        result = round(float(value), 3)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError from exc
    if not isfinite(result):
        raise ValueError
    return result


def recess_positive(value) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError
    result = recess_rounded(value)
    if result <= 0:
        raise ValueError
    return result


def recess_point(value) -> tuple[float, float, float]:
    if (
        not isinstance(value, tuple)
        or len(value) != 3
        or any(
            isinstance(component, bool) or not isinstance(component, Real) for component in value
        )
    ):
        raise ValueError
    return (recess_rounded(value[0]), recess_rounded(value[1]), recess_rounded(value[2]))


def recess_source_at(source) -> tuple[float, float, float]:
    try:
        return recess_point(section_recess_fields(source)[1]["origin"])
    except (AttributeError, OverflowError, TypeError, ValueError):
        return (float("nan"), float("nan"), float("nan"))


def recess_span(at, axis: str, value: float) -> tuple[tuple[float, float, float], ...]:
    lo = list(at)
    hi = list(at)
    index = "xyz".index(axis)
    lo[index] -= value / 2
    hi[index] += value / 2
    return (recess_point(tuple(lo)), recess_point(tuple(hi)))


def measurement_matches(measurement, feature, parameter: str) -> bool:
    return (
        getattr(measurement, "feature", None) == feature
        and getattr(measurement, "parameter", None) == parameter
    )


def state(
    feature, parameter, *, placed, satisfied, suppressed, dropped, registry
) -> Literal[
    "placed", "satisfied_by_structured_note", "suppressed", "dropped", "unverifiable", "missing"
]:
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


def location_state(
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
    carriers=None,
    location_prefix: str,
):
    """Resolve pad/pocket state, treating directional location omission as one authored intent."""

    location = parameter.startswith(f"{location_prefix}.")
    evidence_parameter = f"{location_prefix}.location" if location else parameter
    if (feature, parameter) in inapplicable:
        return "inapplicable"
    point_placed = location and point in locations.get((feature, parameter), ())
    exact_placed = (feature, parameter) in placed
    note_parameter = "location" if location else parameter
    note_satisfied = (feature, note_parameter) in satisfied
    if carriers is not None:
        carriers.accept(
            feature,
            parameter,
            "placed",
            parameters=(parameter,) if exact_placed else (),
            physical=carriers.locations[(feature, parameter, point)] if point_placed else (),
        )
        carriers.accept(
            feature,
            parameter,
            "satisfied_by_structured_note",
            parameters=(note_parameter,) if note_satisfied else (),
        )
    if point_placed or exact_placed:
        return "placed"
    if note_satisfied:
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
