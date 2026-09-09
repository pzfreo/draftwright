"""Compatibility reads for optional annotation-registry provenance axes."""

from __future__ import annotations

from typing import Any, TypeVar, cast

from draftwright.measurement_support import RequirementCarrier

_Outcome = TypeVar("_Outcome")


def satisfaction_of(registry, name) -> tuple:
    """Read one optional satisfaction axis, or return empty for a pre-axis registry."""
    reader = getattr(registry, "satisfaction_of", None)
    if registry is None or not callable(reader):
        return ()
    return tuple(reader(name))


def satisfaction_ids(registry) -> set:
    """Return all placed structured-note authority from any registry-shaped object."""
    if registry is None:
        return set()
    return {identity for name in registry.names() for identity in satisfaction_of(registry, name)}


def annotation_owner(registry, annotation):
    """Return the feature owning *annotation*, if the registry exposes that identity."""
    named = getattr(registry, "named", None)
    feature_of = getattr(registry, "feature_of", None)
    if registry is None or not callable(named) or not callable(feature_of):
        return None
    for name in registry.names():
        if named(name) is annotation:
            owner = feature_of(name)
            if owner is not None:
                return owner
    return None


def requirement_measurements(outcome) -> tuple[tuple[object, str], ...]:
    """Read the exact identities published by a typed physical requirement outcome."""
    explicit = tuple(getattr(outcome, "measurement_ids", ()))
    if explicit:
        return explicit
    representation = getattr(outcome, "representation_feature", None)
    parameter = getattr(outcome, "representation_parameter", None)
    if representation is not None and isinstance(parameter, str):
        return ((representation, parameter),)
    parameter = getattr(outcome, "parameter_id", None)
    if not isinstance(parameter, str) or parameter == "?":
        return ()
    return tuple((feature, parameter) for feature in getattr(outcome, "features", ()))


def measurement_carrier_index(registry):
    """Preserve names alongside the registry's exact measurement/satisfaction axes."""
    result: dict[tuple[int, str], tuple[object, list[RequirementCarrier]]] = {}
    if registry is None:
        return result
    for name in sorted(registry.names()):
        for kind, identities in (
            ("measurement", registry.measurement_of(name)),
            ("structured_note", satisfaction_of(registry, name)),
        ):
            for identity in identities:
                feature, parameter = (
                    getattr(identity, "feature", None),
                    getattr(identity, "parameter", None),
                )
                record_measurement_carrier(result, name, feature, parameter, kind)
    return result


def record_measurement_carrier(index, name, feature, parameter, kind):
    """Retain a name alongside already-read identity fields without rereading the claim."""
    if feature is not None and isinstance(parameter, str):
        entry = index.setdefault((id(feature), parameter), (feature, []))
        if entry[0] is feature:
            carrier = RequirementCarrier(name, kind)
            if carrier not in entry[1]:
                entry[1].append(carrier)


def exact_measurement_carriers(index, measurements):
    """Project only producer-selected exact identities; no parameter aliases inferred."""
    carriers = []
    for feature, parameter in measurements:
        entry = index.get((id(feature), parameter))
        if entry is not None and entry[0] is feature:
            for carrier in entry[1]:
                if carrier not in carriers:
                    carriers.append(carrier)
    return tuple(carriers)


def with_measurement_carriers(outcomes: list[_Outcome], registry) -> list[_Outcome]:
    """Retain ordinary exact-ID evidence; special producer predicates add their own."""
    from dataclasses import replace

    index = measurement_carrier_index(registry)
    return [
        cast(
            _Outcome,
            replace(
                cast(Any, outcome),
                carriers=exact_measurement_carriers(index, requirement_measurements(outcome)),
            ),
        )
        for outcome in outcomes
    ]


class RequirementCarrierEvidence:
    """Names retained by a producer alongside its accepted physical evidence."""

    def __init__(self, registry):
        from collections import defaultdict

        self.measurements = measurement_carrier_index(registry)
        self.locations: dict[tuple, list[RequirementCarrier]] = defaultdict(list)
        self.counts: dict[tuple, list[RequirementCarrier]] = defaultdict(list)
        self.outcomes: dict[tuple, tuple[RequirementCarrier, ...]] = {}

    def accept(self, feature, parameter, state, *, parameters=(), physical=()):
        kind = "measurement" if state == "placed" else "structured_note"
        exact = exact_measurement_carriers(
            self.measurements, ((feature, item) for item in parameters)
        )
        self.outcomes[(feature, parameter)] = tuple(
            dict.fromkeys(
                (
                    *self.outcomes.get((feature, parameter), ()),
                    *physical,
                    *(item for item in exact if item.kind == kind),
                )
            )
        )
        return state

    def attach(self, outcomes: list[_Outcome]) -> list[_Outcome]:
        from dataclasses import replace

        return [
            cast(
                _Outcome,
                replace(
                    cast(Any, outcome),
                    carriers=tuple(
                        carrier
                        for feature in getattr(outcome, "features", ())
                        for carrier in self.outcomes.get(
                            (feature, getattr(outcome, "parameter_id", None)), ()
                        )
                    ),
                ),
            )
            for outcome in outcomes
        ]
