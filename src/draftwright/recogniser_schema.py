"""Consumer-owned record schema versions shared below the recogniser contract.

The exact dependency pin selects the installed provider version.  This leaf records only the
public record schemas Draftwright's adapters consume; it does not inspect provider internals or
resolve engine implementations.
"""

from __future__ import annotations

_SCHEMA_1_RECORDS = {
    ("slots", "Slot"),
    ("repeating-radial-profiles", "RepeatingRadialProfile"),
    ("polygonal-bosses", "PolygonalBoss"),
    ("slot-patterns", "SlotGrid"),
    ("flats", "Flat"),
    ("oriented-slot-patterns", "OrientedSlotGrid"),
    ("angled-steps", "AngledStep"),
    ("double-d-bores", "DoubleDBore"),
    ("oriented-slots", "OrientedSlot"),
    ("hole-patterns", "RectGrid"),
    ("paired-ramp-steps", "PairedRampStep"),
    ("circular-blind-steps", "CircularBlindStep"),
    ("hole-patterns", "LinearArray"),
    ("holes", "HoleSpec"),
    ("bosses", "BossRecord"),
    ("countersinks", "CounterSink"),
    ("risers", "StepShoulder"),
    ("holes", "HoleRecord"),
    ("polygonal-stock", "PolygonalStock"),
    ("slot-patterns", "SlotArray"),
    ("oriented-slot-patterns", "OrientedSlotArray"),
    ("hole-patterns", "BoltCircle"),
    ("holes", "CounterBore"),
}

_RECORD_SCHEMA_VERSIONS: dict[tuple[str, str], tuple[int, ...]] = {
    **{key: (1,) for key in _SCHEMA_1_RECORDS},
    ("blends", "Blend"): (3,),
    ("blends", "CircularBlendPath"): (1,),
    ("blends", "StraightBlendPath"): (1,),
    ("chamfers", "Chamfer"): (2,),
    ("fillets", "Fillet"): (2,),
    ("rectangular-pads", "RaisedPad"): (2,),
    ("turned-steps", "TurnedProfile"): (2,),
    ("turned-steps", "TurnedStep"): (2,),
    ("face-levels", "FaceLevel"): (2,),
    ("grooves", "Groove"): (2,),
    ("plates", "Plate"): (2,),
    ("risers", "RiserEvidence"): (3,),
    ("section-recesses", "ClosedSectionProfile"): (1,),
    ("section-recesses", "OpenSectionProfile"): (1,),
    ("section-recesses", "PassageFrame"): (1,),
    ("section-recesses", "PassageSection"): (2,),
    ("section-recesses", "PassageSectionVertex"): (1,),
    ("section-recesses", "SectionEnd"): (1,),
    ("section-recesses", "SectionRecess"): (1,),
    ("section-recesses", "SectionRecessArray"): (1,),
    ("section-recesses", "SectionRecessBodyRef"): (1,),
    ("section-recesses", "SectionRecessClassification"): (1,),
    ("section-recesses", "SectionRecessDocument"): (1,),
    ("section-recesses", "SectionRecessEnds"): (1,),
    ("section-recesses", "SectionRecessEvidence"): (1,),
    ("section-recesses", "SectionRecessFaceRef"): (1,),
    ("section-recesses", "SectionRecessGeometry"): (1,),
    ("section-recesses", "SectionRecessGrid"): (1,),
    ("section-recesses", "SectionRecessRefusal"): (1,),
    ("through-steps", "ThroughStep"): (2,),
    ("turned-steps", "TurnedProfileKey"): (2,),
}


def consumed_record_schema_versions(family_id: str, record_type: str) -> tuple[int, ...]:
    """Return the exact public schema versions consumed for one family record type."""

    return _RECORD_SCHEMA_VERSIONS.get((family_id, record_type), ())


def consumed_record_schema_versions_for_type(record_type: str) -> tuple[int, ...]:
    """Return all consumed versions for a public record type, empty when it is unknown."""

    versions = {
        version
        for (_family_id, name), declared in _RECORD_SCHEMA_VERSIONS.items()
        if name == record_type
        for version in declared
    }
    return tuple(sorted(versions))


__all__ = [
    "consumed_record_schema_versions",
    "consumed_record_schema_versions_for_type",
]
