"""Structured Part21 facts that OCCT's XCAF PMI transfer currently drops.

This is deliberately a leaf adapter: it understands ISO 10303-21 entities and units, but
knows nothing about XCAF labels, drafting IR, or placement.  ``pmi.py`` owns the overlay and
may use a fact only after :func:`match_geometric_tolerance` proves exact correspondence.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from steputils import p21

_GTOL_ENTITY_KIND: dict[str, str] = {
    "ANGULARITY_TOLERANCE": "angularity",
    "CIRCULAR_RUNOUT_TOLERANCE": "circular_runout",
    "ROUNDNESS_TOLERANCE": "circularity",
    "COAXIALITY_TOLERANCE": "coaxiality",
    "CONCENTRICITY_TOLERANCE": "concentricity",
    "CYLINDRICITY_TOLERANCE": "cylindricity",
    "FLATNESS_TOLERANCE": "flatness",
    "PARALLELISM_TOLERANCE": "parallelism",
    "PERPENDICULARITY_TOLERANCE": "perpendicularity",
    "POSITION_TOLERANCE": "position",
    "LINE_PROFILE_TOLERANCE": "profile_line",
    "SURFACE_PROFILE_TOLERANCE": "profile_surface",
    "STRAIGHTNESS_TOLERANCE": "straightness",
    "SYMMETRY_TOLERANCE": "symmetry",
    "TOTAL_RUNOUT_TOLERANCE": "total_runout",
}

# SI prefix → millimetres per prefixed metre.  The mapping is definition-level standard
# data rather than a fixture inference; unsupported/non-SI units still fail closed.
_SI_METRE_TO_MM: dict[str, float] = {
    "$": 1e3,
    ".EXA.": 1e21,
    ".PETA.": 1e18,
    ".TERA.": 1e15,
    ".GIGA.": 1e12,
    ".MEGA.": 1e9,
    ".KILO.": 1e6,
    ".HECTO.": 1e5,
    ".DECA.": 1e4,
    ".DECI.": 1e2,
    ".CENTI.": 1e1,
    ".MILLI.": 1.0,
    ".MICRO.": 1e-3,
    ".NANO.": 1e-6,
    ".PICO.": 1e-9,
    ".FEMTO.": 1e-12,
    ".ATTO.": 1e-15,
}


@dataclass(frozen=True)
class GeometricToleranceFact:
    """One Part21 tolerance characteristic and its independently auditable magnitude."""

    entity_id: str
    semantic_name: str
    kind: str
    value_mm: float | None
    reason: str = ""


def _entities(instance) -> tuple:
    entity = getattr(instance, "entity", None)
    if entity is not None:
        return (entity,)
    return tuple(instance.entities)


def _entity_named(instance, name: str):
    return next((entity for entity in _entities(instance) if entity.name == name), None)


def _unit_factor_mm(step, unit_ref: str) -> tuple[float | None, str]:
    unit = step.get(unit_ref)
    if unit is None:
        return None, f"length unit {unit_ref} is missing"
    si_unit = _entity_named(unit, "SI_UNIT")
    if si_unit is None or len(si_unit.params) < 2:
        return None, f"length unit {unit_ref} is not a supported SI metre unit"
    prefix, unit_name = (str(param).upper() for param in si_unit.params[:2])
    if unit_name != ".METRE." or prefix not in _SI_METRE_TO_MM:
        return None, f"length unit {unit_ref} is not a supported SI metre unit"
    return _SI_METRE_TO_MM[prefix], ""


def _length_value_mm(step, measure_ref: str) -> tuple[float | None, str]:
    measure = step.get(measure_ref)
    if measure is None:
        return None, f"tolerance magnitude {measure_ref} is missing"
    measure_with_unit = _entity_named(measure, "MEASURE_WITH_UNIT")
    if measure_with_unit is None:
        entity = next(
            (
                candidate
                for candidate in _entities(measure)
                if candidate.name.endswith("MEASURE_WITH_UNIT") and len(candidate.params) >= 2
            ),
            None,
        )
        measure_with_unit = entity
    if measure_with_unit is None or len(measure_with_unit.params) < 2:
        return None, f"tolerance magnitude {measure_ref} is not a measure with unit"

    typed_value, unit_ref = measure_with_unit.params[:2]
    if (
        not isinstance(typed_value, p21.TypedParameter)
        or typed_value.type_name != "LENGTH_MEASURE"
    ):
        return None, f"tolerance magnitude {measure_ref} is not a length measure"
    if not isinstance(unit_ref, p21.Reference):
        return None, f"tolerance magnitude {measure_ref} has no referenced length unit"
    try:
        value = float(typed_value.param)
    except (TypeError, ValueError):
        return None, f"tolerance magnitude {measure_ref} is not numeric"
    if not math.isfinite(value) or value <= 0:
        return None, f"tolerance magnitude {measure_ref} must be finite and positive"

    factor, reason = _unit_factor_mm(step, str(unit_ref))
    if factor is None:
        return None, reason
    return value * factor, ""


def read_geometric_tolerances(step_file: str | Path) -> tuple[GeometricToleranceFact, ...]:
    """Read supported geometric-tolerance facts without inferring entity order.

    Syntax/read failures propagate to the XCAF overlay, which records them as a partial
    extraction reason rather than turning a broken Part21 pass into a report-level failure.
    """

    step = p21.readfile(step_file)
    facts: list[GeometricToleranceFact] = []
    for section in step.data:
        for entity_id, instance in section.instances.items():
            entities = _entities(instance)
            characteristic_names = [
                entity.name for entity in entities if entity.name in _GTOL_ENTITY_KIND
            ]
            if not characteristic_names:
                continue
            if len(characteristic_names) != 1:
                facts.append(
                    GeometricToleranceFact(
                        entity_id,
                        "",
                        "",
                        None,
                        "geometric tolerance has multiple supported characteristics",
                    )
                )
                continue

            characteristic_name = characteristic_names[0]
            kind = _GTOL_ENTITY_KIND[characteristic_name]
            base = _entity_named(instance, "GEOMETRIC_TOLERANCE")
            if base is None:
                base = _entity_named(instance, characteristic_name)
            if base is None or len(base.params) < 3:
                facts.append(
                    GeometricToleranceFact(
                        entity_id,
                        "",
                        kind,
                        None,
                        "geometric tolerance has no name/magnitude tuple",
                    )
                )
                continue

            name_param, magnitude_param = base.params[0], base.params[2]
            semantic_name = str(name_param).strip() if isinstance(name_param, str) else ""
            if not isinstance(magnitude_param, p21.Reference):
                facts.append(
                    GeometricToleranceFact(
                        entity_id,
                        semantic_name,
                        kind,
                        None,
                        "geometric tolerance has no referenced magnitude",
                    )
                )
                continue
            value_mm, reason = _length_value_mm(step, str(magnitude_param))
            facts.append(GeometricToleranceFact(entity_id, semantic_name, kind, value_mm, reason))
    return tuple(facts)


def match_geometric_tolerance(
    facts: tuple[GeometricToleranceFact, ...], semantic_name: str, kind: str
) -> tuple[GeometricToleranceFact | None, str]:
    """Require one exact, nonblank ``(semantic name, kind)`` correspondence."""

    name = semantic_name.strip()
    if not name:
        return None, "XCAF geometric tolerance has no semantic name"
    matches = [fact for fact in facts if fact.semantic_name == name and fact.kind == kind]
    if not matches:
        return None, f"Part21 has no {kind} geometric tolerance named {name!r}"
    if len(matches) > 1:
        entity_ids = ", ".join(fact.entity_id for fact in matches)
        return None, f"Part21 correspondence is ambiguous for {kind} {name!r} ({entity_ids})"
    fact = matches[0]
    return fact, fact.reason
