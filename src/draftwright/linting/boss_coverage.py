"""Recognition-owned completeness for cylindrical boss requirements (#1788)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from draftwright.linting._registry import measurement_outcome_index, with_measurement_carriers
from draftwright.linting.issues import UNJOINED_PARAMETER_ID
from draftwright.measurement_support import RequirementCarrier

BossRequirementState = Literal[
    "placed",
    "satisfied_by_structured_note",
    "suppressed",
    "dropped",
    "missing",
    "unverifiable",
    "inapplicable",
]


@dataclass(frozen=True)
class BossRequirementOutcome:
    """The observable outcome of one source-owned cylindrical-boss requirement."""

    source_at: tuple[float, float, float] | None
    parameter_id: str
    state: BossRequirementState
    requirement_count: int = 1
    features: tuple = ()
    source_records: tuple[object, ...] = field(default=(), repr=False, compare=False, kw_only=True)
    carriers: tuple[RequirementCarrier, ...] = field(default=(), kw_only=True)


def _point(value) -> tuple[float, float, float]:
    point = tuple(round(float(component), 3) for component in value)
    if len(point) != 3:
        raise ValueError("boss point must have three coordinates")
    return point


def _axis(value) -> str:
    vector = tuple(float(component) for component in value)
    if len(vector) != 3:
        raise ValueError("boss axis must have three components")
    index = max(range(3), key=lambda item: abs(vector[item]))
    if abs(abs(vector[index]) - 1.0) > 1e-6 or any(
        abs(component) > 1e-6 for item, component in enumerate(vector) if item != index
    ):
        raise ValueError("boss axis must be principal")
    return "xyz"[index]


def _source_values(source) -> tuple[str, tuple[float, float, float], float, float]:
    axis = _axis(source.axis)
    location = _point(source.location)
    diameter = round(float(source.diameter), 3)
    height = round(float(source.height), 3)
    if diameter <= 0 or height <= 0:
        raise ValueError("boss diameter and height must be positive")
    return axis, location, diameter, height


def _feature_values(feature) -> tuple[str, tuple[float, float, float], float, float]:
    axis = str(feature.frame.axis)
    location = _point(feature.frame.origin)
    diameter = round(float(feature.diameter), 3)
    height = round(float(feature.height), 3)
    if axis not in "xyz" or len(axis) != 1 or diameter <= 0 or height <= 0:
        raise ValueError("boss feature is incomplete")
    if tuple(parameter.parameter_id for parameter in feature.parameters()) != (
        "boss.diameter",
        "boss_height.length",
    ):
        raise ValueError("boss feature has an invalid parameter grammar")
    return axis, location, diameter, height


def _state(features, parameter, *, registry, omissions) -> BossRequirementState:
    placed, satisfied, dropped = measurement_outcome_index(registry)
    identities = tuple((feature, parameter) for feature in features)
    if parameter == "grouping.count":
        placed_as_one_count = any(
            int(getattr(registry.named(name), "covers_count", 1) or 1) == len(features)
            and all(
                identity
                in {
                    (measurement.feature, measurement.parameter)
                    for measurement in registry.measurement_of(name)
                }
                for identity in identities
            )
            for name in registry.names()
        )
    else:
        placed_as_one_count = False
    if identities and (
        placed_as_one_count
        if parameter == "grouping.count"
        else all(identity in placed for identity in identities)
    ):
        return "placed"
    if identities and all(identity in satisfied for identity in identities):
        return "satisfied_by_structured_note"
    suppressed = {
        (omission.feature, omission.parameter_id)
        for omission in omissions
        if omission.feature is not None and omission.authored
    }
    if identities and all(identity in suppressed for identity in identities):
        return "suppressed"
    if any(identity in dropped for identity in identities):
        return "dropped"
    return "missing"


def boss_requirement_outcomes(
    recognition,
    features,
    registry,
    omissions=(),
    *,
    evidence,
    ownership,
) -> list[BossRequirementOutcome]:
    """Project every accepted boss through its exact conversion-time owner."""
    if recognition is None:
        return []
    if evidence.result is not recognition or ownership.evidence is not evidence:
        raise ValueError("boss requirements need one recognition ownership authority")

    final_features = {id(feature): feature for feature in features}
    outcomes: list[BossRequirementOutcome] = []
    represented: list[tuple[object, object, str, float]] = []
    for reference in evidence.features:
        if evidence.family(reference) != "bosses":
            continue
        source = evidence.record(reference)
        binding = ownership.binding_for(reference)
        try:
            axis, location, diameter, height = _source_values(source)
        except (AttributeError, TypeError, ValueError):
            outcomes.append(
                BossRequirementOutcome(
                    None,
                    UNJOINED_PARAMETER_ID,
                    "unverifiable",
                    requirement_count=2,
                    source_records=(source,),
                )
            )
            continue
        if binding is None:
            outcomes.append(
                BossRequirementOutcome(
                    location,
                    UNJOINED_PARAMETER_ID,
                    "unverifiable",
                    requirement_count=2,
                    source_records=(source,),
                )
            )
            continue
        if binding.reason_code != "boss_adapter":
            outcomes.append(
                BossRequirementOutcome(
                    location,
                    "?",
                    "inapplicable",
                    features=tuple(binding.features),
                    source_records=(source,),
                )
            )
            continue
        feature: Any = binding.feature if len(binding.features) == 1 else None
        try:
            if feature is None:
                raise ValueError("boss owner is ambiguous")
            if (
                final_features.get(id(feature)) is not feature
                or getattr(feature, "kind", None) != "boss"
            ):
                raise ValueError("boss owner is absent from final IR")
            if _feature_values(feature) != (axis, location, diameter, height):
                raise ValueError("boss owner changed source geometry")
        except (AttributeError, TypeError, ValueError):
            outcomes.append(
                BossRequirementOutcome(
                    location,
                    UNJOINED_PARAMETER_ID,
                    "unverifiable",
                    requirement_count=2,
                    source_records=(source,),
                )
            )
            continue
        for parameter in ("boss.diameter", "boss_height.length"):
            outcomes.append(
                BossRequirementOutcome(
                    location,
                    parameter,
                    _state((feature,), parameter, registry=registry, omissions=omissions),
                    features=(feature,),
                    source_records=(source,),
                )
            )
        represented.append((source, feature, axis, diameter))

    groups: dict[tuple[str, float], list[tuple[object, object]]] = defaultdict(list)
    for source, feature, axis, diameter in represented:
        groups[(axis, diameter)].append((source, feature))
    for members in groups.values():
        if len(members) < 2:
            continue
        sources = tuple(source for source, _feature in members)
        owners = tuple(feature for _source, feature in members)
        outcomes.append(
            BossRequirementOutcome(
                _point(getattr(sources[0], "location")),
                "grouping.count",
                _state(owners, "grouping.count", registry=registry, omissions=omissions),
                features=owners,
                source_records=sources,
            )
        )
    enriched = with_measurement_carriers(outcomes, registry)
    return [
        replace(
            outcome,
            carriers=tuple(
                carrier
                for carrier in outcome.carriers
                if int(getattr(registry.named(carrier.annotation), "covers_count", 1) or 1)
                == len(outcome.features)
            ),
        )
        if outcome.parameter_id == "grouping.count"
        else outcome
        for outcome in enriched
    ]
