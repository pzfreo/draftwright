"""Evidence and outcome operations shared by coverage checks."""

from math import isfinite
from numbers import Real

from draftwright.contract_values import rounded as _rounded
from draftwright.linting._registry import satisfaction_ids, satisfaction_of
from draftwright.linting.issues import is_placement_drop


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


def index_evidence(registry):
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


def state(feature, parameter, *, placed, satisfied, suppressed, dropped, registry):
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
