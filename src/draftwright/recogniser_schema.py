"""Consumer-owned record schema versions shared below the recogniser contract.

The exact dependency pin selects the installed provider version.  This leaf records only the
public record schemas Draftwright's adapters consume; it does not inspect provider internals or
resolve engine implementations.
"""

from __future__ import annotations

_SCHEMA_1_RECORDS = {
    ("angled-steps", "AngledStep"),
    ("blends", "Blend"),
    ("bosses", "BossRecord"),
    ("channels", "Channel"),
    ("circular-blind-steps", "CircularBlindStep"),
    ("countersinks", "CounterSink"),
    ("double-d-bores", "DoubleDBore"),
    ("face-levels", "FaceLevel"),
    ("flats", "Flat"),
    ("grooves", "Groove"),
    ("hole-patterns", "BoltCircle"),
    ("hole-patterns", "LinearArray"),
    ("hole-patterns", "RectGrid"),
    ("holes", "CounterBore"),
    ("holes", "HoleRecord"),
    ("holes", "HoleSpec"),
    ("oriented-slot-patterns", "OrientedSlotArray"),
    ("oriented-slot-patterns", "OrientedSlotGrid"),
    ("oriented-slots", "OrientedSlot"),
    ("paired-ramp-steps", "PairedRampStep"),
    ("passages", "Passage"),
    ("passages", "PassageEnds"),
    ("passages", "PassageFrame"),
    ("passages", "PassageSection"),
    ("passages", "PassageSectionVertex"),
    ("passages", "SectionPassage"),
    ("plates", "Plate"),
    ("pocket-patterns", "PocketArray"),
    ("pocket-patterns", "PocketGrid"),
    ("pockets", "Pocket"),
    ("polygonal-bosses", "PolygonalBoss"),
    ("polygonal-stock", "PolygonalStock"),
    ("prismatic-pockets", "PrismaticPocket"),
    ("rectangular-blind-slots", "RectangularBlindSlot"),
    ("repeating-radial-profiles", "RepeatingRadialProfile"),
    ("risers", "StepShoulder"),
    ("round-bottom-blind-slots", "RoundBottomBlindSlot"),
    ("slot-patterns", "SlotArray"),
    ("slot-patterns", "SlotGrid"),
    ("slots", "Slot"),
    ("through-steps", "ThroughStep"),
    ("turned-steps", "TurnedProfileKey"),
}

_RECORD_SCHEMA_VERSIONS: dict[tuple[str, str], tuple[int, ...]] = {
    **{key: (1,) for key in _SCHEMA_1_RECORDS},
    ("chamfers", "Chamfer"): (2,),
    ("fillets", "Fillet"): (2,),
    ("rectangular-pads", "RaisedPad"): (2,),
    ("risers", "RiserEvidence"): (2,),
    ("turned-steps", "TurnedProfile"): (2,),
    ("turned-steps", "TurnedStep"): (2,),
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
