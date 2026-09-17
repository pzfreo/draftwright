"""Structured Part21 facts that OCCT's XCAF PMI transfer currently drops.

This is deliberately a leaf adapter: it understands ISO 10303-21 entities and units, but
knows nothing about XCAF labels, drafting IR, or placement.  ``pmi.py`` owns the overlay.
Geometric-tolerance facts require exact XCAF correspondence; manufacturing-requirement facts
retain their own complete property -> representation -> shape-aspect source chain.
"""

from __future__ import annotations

import math
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

from steputils import p21

_READ_SESSION: ContextVar[dict[Path, object] | None] = ContextVar(
    "draftwright_part21_read_session", default=None
)


@contextmanager
def part21_read_session():
    """Reuse one parsed Part21 document within a single extraction call."""

    if _READ_SESSION.get() is not None:
        yield
        return
    token = _READ_SESSION.set({})
    try:
        yield
    finally:
        _READ_SESSION.reset(token)


def _readfile(step_file: str | Path):
    session = _READ_SESSION.get()
    if session is None:
        return p21.readfile(step_file)
    path = Path(step_file).resolve()
    if path not in session:
        session[path] = p21.readfile(step_file)
    return session[path]


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

# Common-label properties attach to this small AP242 shape-aspect family in the supported
# corpus.  Keep the admission list explicit: PROPERTY_DEFINITION can characterize many other
# entity families, and treating those as geometric labels would invent source relationships.
_COMMON_LABEL_ASPECT_ENTITIES = frozenset(
    {"SHAPE_ASPECT", "DATUM_FEATURE", "COMPOSITE_GROUP_SHAPE_ASPECT"}
)


@dataclass(frozen=True)
class GeometricToleranceFact:
    """One Part21 tolerance characteristic and its independently auditable magnitude."""

    entity_id: str
    semantic_name: str
    kind: str
    value_mm: float | None
    reason: str = ""


@dataclass(frozen=True)
class DimensionDisplayFact:
    """Authored presentation policy for one Part21 dimensional characteristic."""

    entity_id: str
    semantic_name: str
    kind: str
    authored_value: float
    value_mm: float
    unit_factor_mm: float
    value_decimals: int | None
    tolerance_decimals: int | None
    unit_name: str


@dataclass(frozen=True)
class DimensionAssociationFact:
    """One dimensional characteristic and its exact ordered shape-aspect groups."""

    entity_id: str
    kind: str
    semantic_name: str
    presentation_name: str
    shape_aspect_ids: tuple[str, ...]
    reference_item_groups: tuple[tuple[str, ...], ...]
    callout_id: str = ""
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
class DatumDefinitionFact:
    """One authored datum and its physical feature, independent of tolerance use."""

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


@dataclass(frozen=True)
class SurfaceLabelFact:
    """One descriptive label authored against an exact surface-group shape aspect."""

    entity_id: str
    text: str
    shape_aspect_id: str = ""
    representation_id: str = ""
    descriptive_item_id: str = ""
    callout_ids: tuple[str, ...] = ()
    reference_item_ids: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class CommonLabelFact:
    """One AP242 semantic-text occurrence attached to a non-NOTE shape aspect."""

    entity_id: str
    presentation_name: str
    text: str
    shape_aspect_id: str = ""
    representation_id: str = ""
    descriptive_item_id: str = ""
    callout_ids: tuple[str, ...] = ()
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


def _datum_reference_items(step) -> dict[str, tuple[str, ...]]:
    """Return exact representation-item references keyed by datum or datum feature."""
    result: dict[str, list[str]] = {}
    usage_names = ("GEOMETRIC_ITEM_SPECIFIC_USAGE", "ITEM_IDENTIFIED_REPRESENTATION_USAGE")
    for section in step.data:
        for instance in section.instances.values():
            for usage_name in usage_names:
                usage = _entity_named(instance, usage_name)
                if usage is None or len(usage.params) < 5:
                    continue
                subject = usage.params[2]
                if not isinstance(subject, p21.Reference) or not any(
                    _instance_is(step, str(subject), kind) for kind in ("DATUM", "DATUM_FEATURE")
                ):
                    continue
                result.setdefault(str(subject), []).extend(_references(usage.params[4]))
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
    step = _readfile(step_file)
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


def read_surface_labels(step_file: str | Path) -> tuple[SurfaceLabelFact, ...]:
    """Read descriptive NOTE labels and their exact authored geometry associations."""
    step = _readfile(step_file)
    properties: list[tuple[str, str]] = []
    representations: dict[str, list[str]] = {}
    note_aspects: set[str] = set()
    aspect_items: dict[str, list[str]] = {}
    association_callouts: dict[str, list[str]] = {}

    for section in step.data:
        for entity_id, instance in section.instances.items():
            aspect = _entity_named(instance, "SHAPE_ASPECT")
            if (
                aspect is not None
                and len(aspect.params) >= 2
                and _text(aspect.params[1]) == "NOTE"
            ):
                note_aspects.add(entity_id)

            definition = _entity_named(instance, "PROPERTY_DEFINITION")
            if definition is not None and len(definition.params) >= 3:
                target = definition.params[2]
                if isinstance(target, p21.Reference):
                    properties.append((entity_id, str(target)))

            relationship = _entity_named(instance, "PROPERTY_DEFINITION_REPRESENTATION")
            if relationship is not None and len(relationship.params) >= 2:
                definition_ref, representation_ref = relationship.params[:2]
                if isinstance(definition_ref, p21.Reference) and isinstance(
                    representation_ref, p21.Reference
                ):
                    representations.setdefault(str(definition_ref), []).append(
                        str(representation_ref)
                    )

            usage = _entity_named(instance, "GEOMETRIC_ITEM_SPECIFIC_USAGE")
            if usage is not None and len(usage.params) >= 5:
                aspect_ref = usage.params[2]
                if isinstance(aspect_ref, p21.Reference):
                    aspect_items.setdefault(str(aspect_ref), []).extend(
                        _references(usage.params[4])
                    )

            association = _entity_named(instance, "DRAUGHTING_MODEL_ITEM_ASSOCIATION")
            if association is not None and len(association.params) >= 5:
                subject = association.params[2]
                if isinstance(subject, p21.Reference):
                    association_callouts.setdefault(str(subject), []).extend(
                        ref
                        for ref in _references(association.params[4])
                        if _instance_is(step, ref, "DRAUGHTING_CALLOUT")
                    )

    facts = []
    for entity_id, aspect_id in properties:
        if aspect_id not in note_aspects:
            continue
        reasons: list[str] = []
        representation_ids = tuple(dict.fromkeys(representations.get(entity_id, ())))
        representation_id = representation_ids[0] if len(representation_ids) == 1 else ""
        if len(representation_ids) != 1:
            reasons.append(f"surface label has {len(representation_ids)} linked representations")

        descriptive_ids: tuple[str, ...] = ()
        if representation_id:
            representation = _entity_named(step.get(representation_id), "REPRESENTATION")
            if representation is None or len(representation.params) < 2:
                reasons.append(
                    f"linked representation {representation_id} is unavailable or malformed"
                )
            else:
                descriptive_ids = tuple(
                    dict.fromkeys(
                        ref
                        for ref in _references(representation.params[1])
                        if _instance_is(step, ref, "DESCRIPTIVE_REPRESENTATION_ITEM")
                    )
                )
        descriptive_item_id = descriptive_ids[0] if len(descriptive_ids) == 1 else ""
        if representation_id and len(descriptive_ids) != 1:
            reasons.append(f"linked representation has {len(descriptive_ids)} descriptive items")

        text = ""
        if descriptive_item_id:
            item = _entity_named(step.get(descriptive_item_id), "DESCRIPTIVE_REPRESENTATION_ITEM")
            if item is None or len(item.params) < 2:
                reasons.append(f"descriptive item {descriptive_item_id} is malformed")
            else:
                text = _text(item.params[1])
                if not text:
                    reasons.append("surface label has no authoritative text")

        property_callouts = set(association_callouts.get(entity_id, ()))
        aspect_callouts = set(association_callouts.get(aspect_id, ()))
        callout_ids = tuple(sorted(property_callouts & aspect_callouts))
        if not callout_ids:
            reasons.append("surface label has no shared semantic/presentation callout")
        reference_item_ids = tuple(dict.fromkeys(aspect_items.get(aspect_id, ())))
        if not reference_item_ids:
            reasons.append("surface label shape aspect has no representation items")

        facts.append(
            SurfaceLabelFact(
                entity_id=entity_id,
                text=text,
                shape_aspect_id=aspect_id,
                representation_id=representation_id,
                descriptive_item_id=descriptive_item_id,
                callout_ids=callout_ids,
                reference_item_ids=reference_item_ids,
                reason="; ".join(dict.fromkeys(reasons)),
            )
        )
    return tuple(facts)


def read_common_labels(step_file: str | Path) -> tuple[CommonLabelFact, ...]:
    """Read semantic text attached to datum, feature, or feature-group shape aspects.

    OCCT imports these occurrences as ``CommonLabel`` dimensions, retaining only their
    presentation names.  Part21 owns the authoritative text and the exact shape-aspect chain.
    Composite aspects are expanded only through authored ``SHAPE_ASPECT_RELATIONSHIP`` links;
    geometry is never inferred from label text or proximity.
    """
    step = _readfile(step_file)
    properties: list[tuple[str, str]] = []
    representations: dict[str, list[str]] = {}
    note_aspects: set[str] = set()
    aspect_children: dict[str, list[str]] = {}
    aspect_items: dict[str, list[str]] = {}
    association_callouts: dict[str, list[str]] = {}

    for section in step.data:
        for entity_id, instance in section.instances.items():
            aspect = _entity_named(instance, "SHAPE_ASPECT")
            if (
                aspect is not None
                and len(aspect.params) >= 2
                and _text(aspect.params[1]) == "NOTE"
            ):
                note_aspects.add(entity_id)

            definition = _entity_named(instance, "PROPERTY_DEFINITION")
            if (
                definition is not None
                and len(definition.params) >= 3
                and _text(definition.params[0]).casefold() == "semantic text"
                and isinstance(definition.params[2], p21.Reference)
                and any(
                    entity.name in _COMMON_LABEL_ASPECT_ENTITIES
                    for entity in _entities(step.get(str(definition.params[2])))
                )
            ):
                properties.append((entity_id, str(definition.params[2])))

            link = _entity_named(instance, "PROPERTY_DEFINITION_REPRESENTATION")
            if link is not None and len(link.params) >= 2:
                definition_ref, representation_ref = link.params[:2]
                if isinstance(definition_ref, p21.Reference) and isinstance(
                    representation_ref, p21.Reference
                ):
                    representations.setdefault(str(definition_ref), []).append(
                        str(representation_ref)
                    )

            relationship = _entity_named(instance, "SHAPE_ASPECT_RELATIONSHIP")
            if relationship is not None and len(relationship.params) >= 4:
                parent, child = relationship.params[2:4]
                if isinstance(parent, p21.Reference) and isinstance(child, p21.Reference):
                    aspect_children.setdefault(str(parent), []).append(str(child))

            usage = _entity_named(instance, "GEOMETRIC_ITEM_SPECIFIC_USAGE")
            if usage is not None and len(usage.params) >= 5:
                aspect_ref = usage.params[2]
                if isinstance(aspect_ref, p21.Reference):
                    aspect_items.setdefault(str(aspect_ref), []).extend(
                        _references(usage.params[4])
                    )

            association = _entity_named(instance, "DRAUGHTING_MODEL_ITEM_ASSOCIATION")
            if association is not None and len(association.params) >= 5:
                subject = association.params[2]
                if isinstance(subject, p21.Reference):
                    association_callouts.setdefault(str(subject), []).extend(
                        ref
                        for ref in _references(association.params[4])
                        if _instance_is(step, ref, "DRAUGHTING_CALLOUT")
                    )

    def related_items(root: str) -> tuple[str, ...]:
        pending = [root]
        visited: set[str] = set()
        items: list[str] = []
        while pending:
            aspect_id = pending.pop(0)
            if aspect_id in visited:
                continue
            visited.add(aspect_id)
            items.extend(aspect_items.get(aspect_id, ()))
            pending.extend(aspect_children.get(aspect_id, ()))
        return tuple(dict.fromkeys(items))

    facts: list[CommonLabelFact] = []
    for entity_id, aspect_id in properties:
        if aspect_id in note_aspects:
            continue
        reasons: list[str] = []
        representation_ids = tuple(dict.fromkeys(representations.get(entity_id, ())))
        representation_id = representation_ids[0] if len(representation_ids) == 1 else ""
        if len(representation_ids) != 1:
            reasons.append(f"common label has {len(representation_ids)} linked representations")

        descriptive_ids: tuple[str, ...] = ()
        if representation_id:
            representation = _entity_named(step.get(representation_id), "REPRESENTATION")
            if representation is None or len(representation.params) < 2:
                reasons.append(
                    f"linked representation {representation_id} is unavailable or malformed"
                )
            else:
                descriptive_ids = tuple(
                    dict.fromkeys(
                        ref
                        for ref in _references(representation.params[1])
                        if _instance_is(step, ref, "DESCRIPTIVE_REPRESENTATION_ITEM")
                    )
                )
        descriptive_item_id = descriptive_ids[0] if len(descriptive_ids) == 1 else ""
        if representation_id and len(descriptive_ids) != 1:
            reasons.append(f"linked representation has {len(descriptive_ids)} descriptive items")

        presentation_name = ""
        label_text = ""
        if descriptive_item_id:
            item = _entity_named(step.get(descriptive_item_id), "DESCRIPTIVE_REPRESENTATION_ITEM")
            if item is None or len(item.params) < 2:
                reasons.append(f"descriptive item {descriptive_item_id} is malformed")
            else:
                presentation_name = _text(item.params[0])
                label_text = _text(item.params[1])
                if not presentation_name:
                    reasons.append("common label has no presentation name")
                if not label_text:
                    reasons.append("common label has no authoritative text")

        property_callouts = set(association_callouts.get(entity_id, ()))
        aspect_callouts = set(association_callouts.get(aspect_id, ()))
        callout_ids = tuple(sorted(property_callouts & aspect_callouts))
        if len(callout_ids) != 1:
            reasons.append(
                f"common label has {len(callout_ids)} shared semantic/presentation callouts"
            )
        reference_item_ids = related_items(aspect_id)
        if not reference_item_ids:
            reasons.append("common-label shape aspect has no representation items")

        facts.append(
            CommonLabelFact(
                entity_id=entity_id,
                presentation_name=presentation_name,
                text=label_text,
                shape_aspect_id=aspect_id,
                representation_id=representation_id,
                descriptive_item_id=descriptive_item_id,
                callout_ids=callout_ids,
                reference_item_ids=reference_item_ids,
                reason="; ".join(dict.fromkeys(reasons)),
            )
        )
    return tuple(facts)


def match_common_label(
    facts: tuple[CommonLabelFact, ...], presentation_name: str
) -> tuple[CommonLabelFact | None, str]:
    """Match one XCAF CommonLabel by its exact retained presentation identity."""
    if not presentation_name:
        return None, "XCAF common label has no presentation name"
    matches = tuple(fact for fact in facts if fact.presentation_name == presentation_name)
    if not matches:
        return None, f"Part21 has no common label named {presentation_name!r}"
    if len(matches) != 1:
        ids = ", ".join(fact.entity_id for fact in matches)
        return (
            None,
            f"Part21 common-label correspondence is ambiguous for {presentation_name!r} ({ids})",
        )
    return matches[0], ""


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

    step = _readfile(step_file)
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


def _value_format_decimals(
    step, item_ref: str, qualifications: dict[str, list[str]]
) -> int | None:
    item = step.get(item_ref)
    qualifier_refs = list(qualifications.get(item_ref, ()))
    qualified = _entity_named(item, "QUALIFIED_REPRESENTATION_ITEM")
    if qualified is not None:
        qualifier_refs.extend(_references(qualified.params))
    decimals: set[int] = set()
    for qualifier_ref in qualifier_refs:
        qualifier = _entity_named(step.get(qualifier_ref), "VALUE_FORMAT_TYPE_QUALIFIER")
        if qualifier is None or not qualifier.params or not isinstance(qualifier.params[0], str):
            continue
        match = re.fullmatch(r"NR\d+\s+\d+\.(\d+)", qualifier.params[0].strip())
        if match:
            precision = int(match.group(1))
            if 0 <= precision <= 15:
                decimals.add(precision)
    return next(iter(decimals)) if len(decimals) == 1 else None


def read_dimension_display_facts(step_file: str | Path) -> tuple[DimensionDisplayFact, ...]:
    """Read exact authored units and decimal policies for semantic length dimensions."""
    step = _readfile(step_file)
    qualifications: dict[str, list[str]] = {}
    tolerance_items: dict[str, list[str]] = {}
    representations: dict[str, tuple[str, ...]] = {}
    links: list[tuple[str, str]] = []
    for section in step.data:
        for entity_id, instance in section.instances.items():
            qualification = _entity_named(instance, "MEASURE_QUALIFICATION")
            if qualification is not None and len(qualification.params) >= 4:
                measure_ref = qualification.params[2]
                if isinstance(measure_ref, p21.Reference):
                    qualifications.setdefault(str(measure_ref), []).extend(
                        _references(qualification.params[3])
                    )
            tolerance = _entity_named(instance, "PLUS_MINUS_TOLERANCE")
            if tolerance is not None and len(tolerance.params) >= 2:
                value_ref, characteristic_ref = tolerance.params[:2]
                if isinstance(value_ref, p21.Reference) and isinstance(
                    characteristic_ref, p21.Reference
                ):
                    tolerance_value = _entity_named(step.get(str(value_ref)), "TOLERANCE_VALUE")
                    if tolerance_value is not None:
                        tolerance_items.setdefault(str(characteristic_ref), []).extend(
                            _references(tolerance_value.params)
                        )
            representation = _entity_named(instance, "SHAPE_DIMENSION_REPRESENTATION")
            if representation is not None and len(representation.params) >= 2:
                representations[entity_id] = _references(representation.params[1])
            link = _entity_named(instance, "DIMENSIONAL_CHARACTERISTIC_REPRESENTATION")
            if link is not None and len(link.params) >= 2:
                characteristic_ref, representation_ref = link.params[:2]
                if isinstance(characteristic_ref, p21.Reference) and isinstance(
                    representation_ref, p21.Reference
                ):
                    links.append((str(characteristic_ref), str(representation_ref)))

    facts: list[DimensionDisplayFact] = []
    for characteristic_ref, representation_ref in links:
        characteristic = step.get(characteristic_ref)
        size = _entity_named(characteristic, "DIMENSIONAL_SIZE")
        location = _entity_named(characteristic, "DIMENSIONAL_LOCATION")
        if size is not None and len(size.params) >= 2:
            semantic_name = _text(size.params[1])
            kind = {"diameter": "diameter", "thickness": "thickness"}.get(
                semantic_name.casefold(), ""
            )
        elif location is not None and location.params:
            semantic_name = _text(location.params[0])
            kind = "linear" if semantic_name.casefold() == "linear distance" else ""
        else:
            continue
        if not kind:
            continue
        nominal_refs = representations.get(representation_ref, ())
        nominal_ref = ""
        for ref in nominal_refs:
            representation_item = _entity_named(step.get(ref), "REPRESENTATION_ITEM")
            if (
                representation_item is not None
                and representation_item.params
                and _text(representation_item.params[0]) == "nominal value"
            ):
                nominal_ref = ref
                break
        measure = _measure_with_unit(step.get(nominal_ref))
        if measure is None:
            continue
        typed_value, unit_ref = measure.params[:2]
        if not isinstance(typed_value, p21.TypedParameter) or not isinstance(
            unit_ref, p21.Reference
        ):
            continue
        if typed_value.type_name not in {"LENGTH_MEASURE", "POSITIVE_LENGTH_MEASURE"}:
            continue
        try:
            authored_value = float(typed_value.param)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(authored_value):
            continue
        factor, reason = _unit_factor_mm(step, str(unit_ref))
        if factor is None or reason:
            continue
        conversion = _entity_named(step.get(str(unit_ref)), "CONVERSION_BASED_UNIT")
        unit_name = (
            _text(conversion.params[0]) if conversion is not None and conversion.params else ""
        )
        tolerance_decimals = {
            decimals
            for ref in tolerance_items.get(characteristic_ref, ())
            if (decimals := _value_format_decimals(step, ref, qualifications)) is not None
        }
        facts.append(
            DimensionDisplayFact(
                characteristic_ref,
                semantic_name,
                kind,
                authored_value,
                authored_value * factor,
                factor,
                _value_format_decimals(step, nominal_ref, qualifications),
                next(iter(tolerance_decimals)) if len(tolerance_decimals) == 1 else None,
                unit_name,
            )
        )
    return tuple(facts)


def read_dimension_associations(step_file: str | Path) -> tuple[DimensionAssociationFact, ...]:
    """Read exact Part21 topology groups for size and location characteristics.

    Group order is semantic: a size owns one aspect, while a location owns its relating and
    related aspects in the order authored by ``DIMENSIONAL_LOCATION``. Composite expansion
    follows only explicit shape-aspect relationships and retains stable source order.
    """
    step = _readfile(step_file)
    characteristics: list[tuple[str, str, str, tuple[str, ...]]] = []
    aspect_children: dict[str, list[str]] = {}
    aspect_items: dict[str, list[str]] = {}
    association_callouts: dict[str, list[str]] = {}
    callout_names: dict[str, str] = {}

    for section in step.data:
        for entity_id, instance in section.instances.items():
            size = _entity_named(instance, "DIMENSIONAL_SIZE")
            location = _entity_named(instance, "DIMENSIONAL_LOCATION")
            if (
                size is not None
                and len(size.params) >= 2
                and isinstance(size.params[0], p21.Reference)
            ):
                semantic_name = _text(size.params[1])
                characteristics.append((entity_id, "size", semantic_name, (str(size.params[0]),)))
            elif (
                location is not None
                and len(location.params) >= 4
                and isinstance(location.params[2], p21.Reference)
                and isinstance(location.params[3], p21.Reference)
            ):
                characteristics.append(
                    (
                        entity_id,
                        "location",
                        _text(location.params[0]),
                        (str(location.params[2]), str(location.params[3])),
                    )
                )

            relationship = _entity_named(instance, "SHAPE_ASPECT_RELATIONSHIP")
            if relationship is not None and len(relationship.params) >= 4:
                parent, child = relationship.params[2:4]
                if isinstance(parent, p21.Reference) and isinstance(child, p21.Reference):
                    aspect_children.setdefault(str(parent), []).append(str(child))

            usage = _entity_named(instance, "GEOMETRIC_ITEM_SPECIFIC_USAGE")
            if usage is not None and len(usage.params) >= 5:
                aspect_ref = usage.params[2]
                if isinstance(aspect_ref, p21.Reference):
                    aspect_items.setdefault(str(aspect_ref), []).extend(
                        _references(usage.params[4])
                    )

            callout = _entity_named(instance, "DRAUGHTING_CALLOUT")
            if callout is not None and callout.params:
                callout_names[entity_id] = _text(callout.params[0])

            association = _entity_named(instance, "DRAUGHTING_MODEL_ITEM_ASSOCIATION")
            if association is not None and len(association.params) >= 5:
                subject = association.params[2]
                if isinstance(subject, p21.Reference):
                    association_callouts.setdefault(str(subject), []).extend(
                        ref
                        for ref in _references(association.params[4])
                        if _instance_is(step, ref, "DRAUGHTING_CALLOUT")
                    )

    def related_items(root: str) -> tuple[str, ...]:
        pending = [root]
        visited: set[str] = set()
        items: list[str] = []
        while pending:
            aspect_id = pending.pop(0)
            if aspect_id in visited:
                continue
            visited.add(aspect_id)
            items.extend(aspect_items.get(aspect_id, ()))
            pending.extend(aspect_children.get(aspect_id, ()))
        return tuple(dict.fromkeys(items))

    facts: list[DimensionAssociationFact] = []
    for entity_id, kind, semantic_name, shape_aspect_ids in characteristics:
        reasons: list[str] = []
        callout_ids = tuple(dict.fromkeys(association_callouts.get(entity_id, ())))
        callout_id = callout_ids[0] if len(callout_ids) == 1 else ""
        if len(callout_ids) != 1:
            reasons.append(
                f"dimension characteristic has {len(callout_ids)} linked presentation callouts"
            )
        presentation_name = callout_names.get(callout_id, "") if callout_id else ""
        if callout_id and not presentation_name:
            reasons.append("dimension presentation callout has no name")
        item_groups = tuple(related_items(aspect_id) for aspect_id in shape_aspect_ids)
        for index, item_group in enumerate(item_groups, start=1):
            if not item_group:
                reasons.append(f"dimension reference group {index} has no representation items")
        facts.append(
            DimensionAssociationFact(
                entity_id=entity_id,
                kind=kind,
                semantic_name=semantic_name,
                presentation_name=presentation_name,
                shape_aspect_ids=shape_aspect_ids,
                reference_item_groups=item_groups,
                callout_id=callout_id,
                reason="; ".join(dict.fromkeys(reasons)),
            )
        )
    return tuple(facts)


def match_dimension_display(
    facts: tuple[DimensionDisplayFact, ...], semantic_name: str, kind: str, authored_value: float
) -> DimensionDisplayFact | None:
    """Return one unambiguous display policy; equivalent duplicate policies may collapse."""
    matches = [
        fact
        for fact in facts
        if fact.semantic_name == semantic_name
        and fact.kind == kind
        and math.isclose(fact.authored_value, authored_value, rel_tol=1e-9, abs_tol=1e-12)
    ]
    policies = {(fact.value_decimals, fact.tolerance_decimals, fact.unit_name) for fact in matches}
    return matches[0] if matches and len(policies) == 1 else None


def read_geometric_tolerances(step_file: str | Path) -> tuple[GeometricToleranceFact, ...]:
    """Read supported geometric-tolerance facts without inferring entity order.

    Syntax/read failures propagate to the XCAF overlay, which records them as a partial
    extraction reason rather than turning a broken Part21 pass into a report-level failure.
    """

    step = _readfile(step_file)
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
    step = _readfile(step_file)
    datum_features = _datum_feature_relationships(step)
    reference_items = _datum_reference_items(step)
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
                    feature_items = reference_items.get(feature_id, ())
                    datum_items = reference_items.get(datum_id, ())
                    items = feature_items or datum_items
                    item_reason = (
                        "" if items else f"datum feature {feature_id} has no representation items"
                    )
                    facts.append(
                        DatumOccurrenceFact(
                            tolerance_id,
                            tolerance_name,
                            kind,
                            feature_id,
                            datum_id,
                            letter,
                            items,
                            item_reason,
                        )
                    )
    return tuple(facts)


def read_datum_definitions(step_file: str | Path) -> tuple[DatumDefinitionFact, ...]:
    """Read every authored datum definition, including those unused by a tolerance."""
    step = _readfile(step_file)
    datum_features = _datum_feature_relationships(step)
    reference_items = _datum_reference_items(step)
    facts: list[DatumDefinitionFact] = []
    for datum_id, (letter, feature_ids) in datum_features.items():
        if len(feature_ids) != 1:
            facts.append(
                DatumDefinitionFact(
                    "",
                    datum_id,
                    letter,
                    reason=f"datum {datum_id} has {len(feature_ids)} related DATUM_FEATUREs",
                )
            )
            continue
        (feature_id,) = feature_ids
        items = reference_items.get(feature_id, ()) or reference_items.get(datum_id, ())
        facts.append(
            DatumDefinitionFact(
                feature_id,
                datum_id,
                letter,
                items,
                "" if items else f"datum feature {feature_id} has no representation items",
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
