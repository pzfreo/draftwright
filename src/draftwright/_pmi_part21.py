"""Structured Part21 facts that OCCT's XCAF PMI transfer currently drops.

This is deliberately a leaf adapter: it understands ISO 10303-21 entities and units, but
knows nothing about XCAF labels, drafting IR, or placement.  ``pmi.py`` owns the overlay.
Geometric-tolerance facts require exact XCAF correspondence; manufacturing-requirement facts
retain their own complete property -> representation -> shape-aspect source chain.
"""

from __future__ import annotations

import math
import re
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

# Entity names are invariant ASCII Part21 syntax; string parameters are not.  Keying this
# guard on the literal category text silently missed valid ``\X2\...\X0\`` encodings before
# steputils had a chance to decode them.  A file with no property definitions cannot carry
# the requirement chain, while any file that does is parsed conservatively (#1297).  Match
# only the invariant token: Part 21 comments may legally separate it from the opening ``(``.
_PROPERTY_DEFINITION_MARKER = re.compile(rb"\bPROPERTY_DEFINITION\b", re.IGNORECASE)


@dataclass(frozen=True)
class GeometricToleranceFact:
    """One Part21 tolerance characteristic and its independently auditable magnitude."""

    entity_id: str
    semantic_name: str
    kind: str
    value_mm: float | None
    reason: str = ""


@dataclass(frozen=True)
class DatumOccurrenceFact:
    """One datum reference used by one Part21 geometric-tolerance context.

    ``datum_feature_id`` identifies the authored physical feature; several occurrence facts
    may therefore share it. ``reference_item_ids`` are the exact representation items bound
    to that feature, retained for correspondence rather than interpreted as page geometry.
    """

    tolerance_id: str
    tolerance_name: str
    tolerance_kind: str
    datum_feature_id: str
    datum_id: str
    letter: str
    reference_item_ids: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class ManufacturingRequirementFact:
    """One authoritative semantic manufacturing requirement from Part 21.

    ``entity_id`` is the ``PROPERTY_DEFINITION`` identity, while ``text`` comes from the
    linked ``DESCRIPTIVE_REPRESENTATION_ITEM`` rather than presentation glyphs.  The
    remaining IDs preserve the semantic-callout -> shape-aspect -> representation-item
    association for concept lowering; this leaf adapter deliberately does not interpret
    those items as drafting features.
    """

    entity_id: str
    semantic_name: str
    text: str
    representation_id: str = ""
    descriptive_item_id: str = ""
    callout_ids: tuple[str, ...] = ()
    shape_aspect_ids: tuple[str, ...] = ()
    reference_item_ids: tuple[str, ...] = ()
    reason: str = ""


def _entities(instance) -> tuple:
    if instance is None:
        return ()
    entity = getattr(instance, "entity", None)
    if entity is not None:
        return (entity,)
    return tuple(instance.entities)


def _entity_named(instance, name: str):
    return next((entity for entity in _entities(instance) if entity.name == name), None)


def _measure_with_unit(instance):
    return next(
        (
            entity
            for entity in _entities(instance)
            if entity.name.endswith("MEASURE_WITH_UNIT") and len(entity.params) >= 2
        ),
        None,
    )


def _references(value) -> tuple[str, ...]:
    """Return Part21 references nested in a parameter, preserving source order."""
    if isinstance(value, p21.Reference):
        return (str(value),)
    if isinstance(value, p21.TypedParameter):
        return _references(value.param)
    if isinstance(value, (tuple, list)):
        return tuple(ref for item in value for ref in _references(item))
    return ()


def _instance_is(step, ref: str, name: str) -> bool:
    instance = step.get(ref)
    return instance is not None and _entity_named(instance, name) is not None


def _datum_feature_relationships(step) -> dict[str, tuple[str, tuple[str, ...]]]:
    """Return ``datum id -> (letter, datum-feature ids)`` without hiding ambiguity."""
    datums: dict[str, str] = {}
    relationships: list[tuple[str, str]] = []
    for section in step.data:
        for entity_id, instance in section.instances.items():
            datum = _entity_named(instance, "DATUM")
            if datum is not None and len(datum.params) >= 5:
                letter = datum.params[4]
                datums[entity_id] = str(letter).strip() if isinstance(letter, str) else ""
            relationship = _entity_named(instance, "SHAPE_ASPECT_RELATIONSHIP")
            if relationship is not None and len(relationship.params) >= 4:
                left, right = relationship.params[2:4]
                if isinstance(left, p21.Reference) and isinstance(right, p21.Reference):
                    relationships.append((str(left), str(right)))

    result: dict[str, list[str]] = {datum_id: [] for datum_id in datums}
    for left, right in relationships:
        if left in datums and _instance_is(step, right, "DATUM_FEATURE"):
            result[left].append(right)
        elif right in datums and _instance_is(step, left, "DATUM_FEATURE"):
            result[right].append(left)
    return {
        datum_id: (datums[datum_id], tuple(dict.fromkeys(feature_ids)))
        for datum_id, feature_ids in result.items()
    }


def _datum_feature_items(step) -> dict[str, tuple[str, ...]]:
    """Return exact representation-item references for each authored datum feature."""
    result: dict[str, list[str]] = {}
    usage_names = ("GEOMETRIC_ITEM_SPECIFIC_USAGE", "ITEM_IDENTIFIED_REPRESENTATION_USAGE")
    for section in step.data:
        for instance in section.instances.values():
            for usage_name in usage_names:
                usage = _entity_named(instance, usage_name)
                if usage is None or len(usage.params) < 5:
                    continue
                feature = usage.params[2]
                if not isinstance(feature, p21.Reference) or not _instance_is(
                    step, str(feature), "DATUM_FEATURE"
                ):
                    continue
                result.setdefault(str(feature), []).extend(_references(usage.params[4]))
    return {key: tuple(dict.fromkeys(values)) for key, values in result.items()}


def _text(value) -> str:
    return str(value).strip() if isinstance(value, str) else ""


def _name_tokens(value: str) -> frozenset[str]:
    """Normalise semantic names for callout correspondence, not requirement inference."""
    words = []
    for raw in value.lower().replace("_", " ").replace("-", " ").split():
        word = "".join(character for character in raw if character.isalnum())
        if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        if word and word != "requirement":
            words.append(word)
    return frozenset(words)


def _manufacturing_graph(step):
    """Collect the Part21 indexes shared by manufacturing-requirement facts."""
    definitions: list[tuple[str, object]] = []
    representations: dict[str, list[str]] = {}
    callout_names: dict[str, str] = {}
    callout_aspects: dict[str, list[str]] = {}
    aspect_items: dict[str, list[str]] = {}

    for section in step.data:
        for entity_id, instance in section.instances.items():
            definition = _entity_named(instance, "PROPERTY_DEFINITION")
            if (
                definition is not None
                and len(definition.params) >= 2
                and _text(definition.params[0]).lower() == "manufacturing requirement"
            ):
                definitions.append((entity_id, definition))

            relationship = _entity_named(instance, "PROPERTY_DEFINITION_REPRESENTATION")
            if relationship is not None and len(relationship.params) >= 2:
                definition_ref, representation_ref = relationship.params[:2]
                if isinstance(definition_ref, p21.Reference) and isinstance(
                    representation_ref, p21.Reference
                ):
                    representations.setdefault(str(definition_ref), []).append(
                        str(representation_ref)
                    )

            callout = _entity_named(instance, "DRAUGHTING_CALLOUT")
            if callout is not None and callout.params:
                callout_names[entity_id] = _text(callout.params[0])

            association = _entity_named(instance, "DRAUGHTING_MODEL_ITEM_ASSOCIATION")
            if association is not None and len(association.params) >= 5:
                aspect_ref = association.params[2]
                if isinstance(aspect_ref, p21.Reference) and _instance_is(
                    step, str(aspect_ref), "SHAPE_ASPECT"
                ):
                    for callout_ref in _references(association.params[4]):
                        if _instance_is(step, callout_ref, "DRAUGHTING_CALLOUT"):
                            callout_aspects.setdefault(callout_ref, []).append(str(aspect_ref))

            usage = _entity_named(instance, "GEOMETRIC_ITEM_SPECIFIC_USAGE")
            if usage is not None and len(usage.params) >= 5:
                aspect_ref = usage.params[2]
                if isinstance(aspect_ref, p21.Reference) and _instance_is(
                    step, str(aspect_ref), "SHAPE_ASPECT"
                ):
                    aspect_items.setdefault(str(aspect_ref), []).extend(
                        _references(usage.params[4])
                    )

    return definitions, representations, callout_names, callout_aspects, aspect_items


def read_manufacturing_requirements(
    step_file: str | Path,
) -> tuple[ManufacturingRequirementFact, ...]:
    """Read authoritative semantic requirements and retain their geometry associations.

    The property-definition chain owns the full requirement text.  A draughting callout
    with the same representation name (or, where the presentation adds a qualifier such as
    ``Straight``, the same semantic-name tokens) supplies the shape-aspect association.
    This is correspondence between two source semantic identities; no nominal value or
    B-rep measurement is guessed here.
    """
    # A file with no property-definition entity cannot carry this semantic chain, so avoid a
    # third full Part21 parse there.  Once any property definition exists, parse
    # conservatively: its string category may use a valid escape encoding that a byte-level
    # literal check cannot interpret.
    if _PROPERTY_DEFINITION_MARKER.search(Path(step_file).read_bytes()) is None:
        return ()
    step = p21.readfile(step_file)
    (
        definitions,
        representations,
        callout_names,
        callout_aspects,
        aspect_items,
    ) = _manufacturing_graph(step)
    facts: list[ManufacturingRequirementFact] = []

    for entity_id, definition in definitions:
        semantic_name = _text(definition.params[1])
        reasons: list[str] = []
        if not semantic_name:
            reasons.append("manufacturing requirement has no semantic name")

        representation_ids = tuple(dict.fromkeys(representations.get(entity_id, ())))
        representation_id = representation_ids[0] if len(representation_ids) == 1 else ""
        if len(representation_ids) != 1:
            reasons.append(
                f"manufacturing requirement has {len(representation_ids)} linked representations"
            )

        representation_name = ""
        descriptive_ids: tuple[str, ...] = ()
        if representation_id:
            representation = _entity_named(step.get(representation_id), "REPRESENTATION")
            if representation is None or len(representation.params) < 2:
                reasons.append(
                    f"linked representation {representation_id} is unavailable or malformed"
                )
            else:
                representation_name = _text(representation.params[0])
                descriptive_ids = tuple(
                    dict.fromkeys(
                        ref
                        for ref in _references(representation.params[1])
                        if _instance_is(step, ref, "DESCRIPTIVE_REPRESENTATION_ITEM")
                    )
                )

        descriptive_item_id = descriptive_ids[0] if len(descriptive_ids) == 1 else ""
        text = ""
        if representation_id and len(descriptive_ids) != 1:
            reasons.append(f"linked representation has {len(descriptive_ids)} descriptive items")
        if descriptive_item_id:
            item = _entity_named(step.get(descriptive_item_id), "DESCRIPTIVE_REPRESENTATION_ITEM")
            if item is None or len(item.params) < 2:
                reasons.append(f"descriptive item {descriptive_item_id} is malformed")
            else:
                text = _text(item.params[1])
                if not text:
                    reasons.append("manufacturing requirement has no authoritative text")

        exact_callouts = tuple(
            callout_id
            for callout_id, name in callout_names.items()
            if representation_name and name.casefold() == representation_name.casefold()
        )
        semantic_tokens = _name_tokens(semantic_name)
        callout_ids = exact_callouts or tuple(
            callout_id
            for callout_id, name in callout_names.items()
            if semantic_tokens and semantic_tokens <= _name_tokens(name)
        )
        missing_callout_associations = tuple(
            callout_id for callout_id in callout_ids if not callout_aspects.get(callout_id)
        )
        if missing_callout_associations:
            reasons.append(
                "matched semantic callout(s) have no shape-aspect association: "
                + ", ".join(missing_callout_associations)
            )
        shape_aspect_ids = tuple(
            dict.fromkeys(
                aspect_id
                for callout_id in callout_ids
                for aspect_id in callout_aspects.get(callout_id, ())
            )
        )
        missing_aspect_items = tuple(
            aspect_id for aspect_id in shape_aspect_ids if not aspect_items.get(aspect_id)
        )
        if missing_aspect_items:
            reasons.append(
                "associated shape aspect(s) have no representation items: "
                + ", ".join(missing_aspect_items)
            )
        reference_item_ids = tuple(
            dict.fromkeys(
                item_id
                for aspect_id in shape_aspect_ids
                for item_id in aspect_items.get(aspect_id, ())
            )
        )
        facts.append(
            ManufacturingRequirementFact(
                entity_id=entity_id,
                semantic_name=semantic_name,
                text=text,
                representation_id=representation_id,
                descriptive_item_id=descriptive_item_id,
                callout_ids=tuple(dict.fromkeys(callout_ids)),
                shape_aspect_ids=shape_aspect_ids,
                reference_item_ids=reference_item_ids,
                reason="; ".join(dict.fromkeys(reasons)),
            )
        )
    return tuple(facts)


def _unit_factor_mm(
    step, unit_ref: str, *, _seen: frozenset[str] = frozenset()
) -> tuple[float | None, str]:
    if unit_ref in _seen:
        return None, f"length unit {unit_ref} has a cyclic conversion"
    unit = step.get(unit_ref)
    if unit is None:
        return None, f"length unit {unit_ref} is missing"
    si_unit = _entity_named(unit, "SI_UNIT")
    if si_unit is not None and len(si_unit.params) >= 2:
        prefix, unit_name = (str(param).upper() for param in si_unit.params[:2])
        if unit_name == ".METRE." and prefix in _SI_METRE_TO_MM:
            return _SI_METRE_TO_MM[prefix], ""
        return None, f"length unit {unit_ref} is not a supported SI metre unit"

    conversion = _entity_named(unit, "CONVERSION_BASED_UNIT")
    if (
        conversion is None
        or len(conversion.params) < 2
        or _entity_named(unit, "LENGTH_UNIT") is None
    ):
        return None, f"length unit {unit_ref} is not a supported length unit"
    conversion_ref = conversion.params[1]
    if not isinstance(conversion_ref, p21.Reference):
        return None, f"length unit {unit_ref} has no referenced conversion factor"
    measure = step.get(str(conversion_ref))
    measure_with_unit = _measure_with_unit(measure)
    if measure_with_unit is None or len(measure_with_unit.params) < 2:
        return None, f"length unit {unit_ref} has no usable conversion factor"
    typed_value, base_unit_ref = measure_with_unit.params[:2]
    if (
        not isinstance(typed_value, p21.TypedParameter)
        or typed_value.type_name != "LENGTH_MEASURE"
    ):
        return None, f"length unit {unit_ref} conversion factor is not a length measure"
    if not isinstance(base_unit_ref, p21.Reference):
        return None, f"length unit {unit_ref} conversion factor has no referenced length unit"
    try:
        value = float(typed_value.param)
    except (TypeError, ValueError):
        return None, f"length unit {unit_ref} conversion factor is not numeric"
    if not math.isfinite(value) or value <= 0:
        return None, f"length unit {unit_ref} conversion factor must be finite and positive"
    base_factor, reason = _unit_factor_mm(step, str(base_unit_ref), _seen=_seen | {unit_ref})
    if base_factor is None:
        return None, reason
    factor = value * base_factor
    if not math.isfinite(factor) or factor <= 0:
        return None, (
            f"length unit {unit_ref} conversion factor must be finite and positive in millimetres"
        )
    return factor, ""


def _length_value_mm(step, measure_ref: str) -> tuple[float | None, str]:
    measure = step.get(measure_ref)
    if measure is None:
        return None, f"tolerance magnitude {measure_ref} is missing"
    measure_with_unit = _measure_with_unit(measure)
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
    value_mm = value * factor
    if not math.isfinite(value_mm) or value_mm <= 0:
        return None, (
            f"tolerance magnitude {measure_ref} must be finite and positive in millimetres"
        )
    return value_mm, ""


def read_dimension_length_factor(step_file: str | Path) -> tuple[float | None, str]:
    """Return one unambiguous authored length-dimension scale to millimetres.

    XCAF exposes dimension nominal values in their authored unit while its reference geometry
    and tolerance values are already normalized to millimetres.  A document-wide factor is
    safe only when every semantic length-dimension representation names the same scale.
    Angular and presentation-only representation items are ignored.
    """

    step = p21.readfile(step_file)
    factors: list[float] = []
    for section in step.data:
        for instance in section.instances.values():
            representation = _entity_named(instance, "SHAPE_DIMENSION_REPRESENTATION")
            if representation is None:
                continue
            if len(representation.params) < 2:
                return None, f"shape-dimension representation {instance.ref} has no items"
            measure_refs = _references(representation.params[1])
            if not measure_refs:
                return None, f"shape-dimension representation {instance.ref} has no items"
            for measure_ref in measure_refs:
                measure = step.get(measure_ref)
                measure_with_unit = _measure_with_unit(measure)
                if measure_with_unit is None:
                    item_kinds = {entity.name for entity in _entities(measure)}
                    if item_kinds and item_kinds <= {
                        "COMPOUND_REPRESENTATION_ITEM",
                        "DESCRIPTIVE_REPRESENTATION_ITEM",
                    }:
                        continue
                    return None, f"shape-dimension item {measure_ref} has no measure with unit"
                if len(measure_with_unit.params) < 2:
                    return None, f"shape-dimension item {measure_ref} has no unit"
                typed_value, unit_ref = measure_with_unit.params[:2]
                if not isinstance(typed_value, p21.TypedParameter):
                    return None, f"shape-dimension item {measure_ref} has no typed measure"
                if typed_value.type_name == "PLANE_ANGLE_MEASURE":
                    continue
                if typed_value.type_name not in {"LENGTH_MEASURE", "POSITIVE_LENGTH_MEASURE"}:
                    return None, (
                        f"shape-dimension item {measure_ref} uses unsupported measure type "
                        f"{typed_value.type_name}"
                    )
                if not isinstance(unit_ref, p21.Reference):
                    return None, f"shape-dimension item {measure_ref} has no unit reference"
                factor, reason = _unit_factor_mm(step, str(unit_ref))
                if factor is None:
                    return None, reason
                factors.append(factor)

    if not factors:
        return None, "no authored length-dimension unit is available"
    reference = factors[0]
    if any(not math.isclose(factor, reference, rel_tol=1e-12) for factor in factors[1:]):
        distinct = tuple(dict.fromkeys(factors))
        return None, f"length dimensions use multiple unit scales: {distinct!r}"
    return reference, ""


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


def read_datum_occurrences(step_file: str | Path) -> tuple[DatumOccurrenceFact, ...]:
    """Read exact datum-feature uses from Part21 without collapsing repeated occurrences."""
    step = p21.readfile(step_file)
    datum_features = _datum_feature_relationships(step)
    feature_items = _datum_feature_items(step)
    facts: list[DatumOccurrenceFact] = []

    for section in step.data:
        for tolerance_id, instance in section.instances.items():
            entities = _entities(instance)
            characteristic_names = [
                entity.name for entity in entities if entity.name in _GTOL_ENTITY_KIND
            ]
            if len(characteristic_names) != 1:
                continue
            characteristic_name = characteristic_names[0]
            kind = _GTOL_ENTITY_KIND[characteristic_name]
            base = _entity_named(instance, "GEOMETRIC_TOLERANCE") or _entity_named(
                instance, characteristic_name
            )
            if base is None or not base.params:
                continue
            name_param = base.params[0]
            tolerance_name = str(name_param).strip() if isinstance(name_param, str) else ""

            system_refs = tuple(
                dict.fromkeys(
                    ref
                    for entity in entities
                    for param in entity.params
                    for ref in _references(param)
                    if _instance_is(step, ref, "DATUM_SYSTEM")
                )
            )
            for system_ref in system_refs:
                system = _entity_named(step.get(system_ref), "DATUM_SYSTEM")
                compartment_refs = tuple(
                    ref
                    for param in system.params
                    for ref in _references(param)
                    if _instance_is(step, ref, "DATUM_REFERENCE_COMPARTMENT")
                )
                for compartment_ref in compartment_refs:
                    compartment = _entity_named(
                        step.get(compartment_ref), "DATUM_REFERENCE_COMPARTMENT"
                    )
                    datum_refs = tuple(
                        ref
                        for param in compartment.params
                        for ref in _references(param)
                        if _instance_is(step, ref, "DATUM")
                    )
                    if len(datum_refs) != 1:
                        facts.append(
                            DatumOccurrenceFact(
                                tolerance_id,
                                tolerance_name,
                                kind,
                                "",
                                "",
                                "",
                                reason=(
                                    f"datum compartment {compartment_ref} has "
                                    f"{len(datum_refs)} datum references"
                                ),
                            )
                        )
                        continue
                    datum_id = datum_refs[0]
                    letter, feature_ids = datum_features[datum_id]
                    if len(feature_ids) != 1:
                        facts.append(
                            DatumOccurrenceFact(
                                tolerance_id,
                                tolerance_name,
                                kind,
                                "",
                                datum_id,
                                letter,
                                reason=(
                                    f"datum {datum_id} has {len(feature_ids)} related "
                                    "DATUM_FEATUREs"
                                ),
                            )
                        )
                        continue
                    (feature_id,) = feature_ids
                    items = feature_items.get(feature_id, ())
                    facts.append(
                        DatumOccurrenceFact(
                            tolerance_id,
                            tolerance_name,
                            kind,
                            feature_id,
                            datum_id,
                            letter,
                            items,
                            ""
                            if items
                            else f"datum feature {feature_id} has no representation items",
                        )
                    )
    return tuple(facts)


def match_datum_occurrence(
    facts: tuple[DatumOccurrenceFact, ...], tolerance_name: str, letter: str
) -> tuple[DatumOccurrenceFact | None, str]:
    """Require one exact datum occurrence for an XCAF tolerance context and letter."""
    name, datum_letter = tolerance_name.strip(), letter.strip()
    if not name:
        return None, "XCAF datum occurrence has no tolerance context"
    if not datum_letter:
        return None, "XCAF datum occurrence has no letter"
    matches = [
        fact for fact in facts if fact.tolerance_name == name and fact.letter == datum_letter
    ]
    if not matches:
        return None, f"Part21 has no datum {datum_letter!r} in tolerance {name!r}"
    if len(matches) > 1:
        ids = ", ".join(f"{fact.tolerance_id}/{fact.datum_feature_id}" for fact in matches)
        return (
            None,
            f"Part21 datum correspondence is ambiguous for {name!r}/{datum_letter!r} ({ids})",
        )
    fact = matches[0]
    return fact, fact.reason


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
