"""Leaf consumer policy for accepted recogniser occurrence families."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

OwnerlessDisposition = Literal["unsupported", "deferred", "evidence_only"]

GEOMETRY_ONLY_FAMILY_ID = "repeating-radial-profiles"
GEOMETRY_ONLY_RATIONALE = (
    "Independent repeating-profile evidence critiques a separately authored gear; geometry "
    "alone must not create inferred gear intent."
)

# These aggregate records are released as physical recognition evidence, not feature-census
# entries. Draftwright consumes them as substrate for its correlated StepLevelFeature projection;
# an individual evidence record is not itself a drafting feature or completeness requirement.
EVIDENCE_ONLY_FAMILIES: Mapping[str, str] = MappingProxyType(
    {
        "step-levels": "step_level_projection_evidence",
        "risers": "riser_projection_evidence",
    }
)

# Families the installed package proves but Draftwright does not fully support. This is the
# single consumer-policy source used by both the capability declaration and occurrence ledger.
# It is not a parking bay: an undecided family needs a live decision issue, while a settled
# unsupported boundary needs an explicit outcome. The rank-7 contract's exhaustive manifest join
# ensures a new provider family cannot hide by being absent from this table and the supported map.
UNSUPPORTED_FAMILIES: Mapping[str, tuple[tuple[str, ...], str, str]] = MappingProxyType(
    {
        "oriented-slot-patterns": (
            ("OrientedSlotArray", "OrientedSlotGrid"),
            "https://github.com/pzfreo/draftwright/issues/1430",
            "The derived records group free-axis oriented slots, but the principal-axis "
            "SlotPatternFeature cannot preserve their vector plane, member passage authority, and "
            "pattern identity. A dedicated consumer contract is being reviewed before any grouping "
            "or dimensions are emitted.",
        ),
        "oriented-slots": (
            ("OrientedSlot",),
            "https://github.com/pzfreo/draftwright/issues/1430",
            "The record carries free-axis directions and an authoritative SectionPassage source. "
            "Coercing it into the legacy axis-letter SlotFeature would discard that correspondence, "
            "so its dedicated IR, Sheet vocabulary, drawing grammar, and completeness denominator "
            "remain under review.",
        ),
        "passages": (
            (
                "Passage",
                "PassageEnds",
                "PassageFrame",
                "PassageSection",
                "PassageSectionVertex",
                "SectionPassage",
            ),
            "https://github.com/pzfreo/draftwright/issues/1245",
            "A prismatic through-opening — the internal counterpart to polygonal stock. The rich "
            "record permits arbitrary line/arc sections, so treating its regular-polygon subset as "
            "a supported IR feature or HEX callout would overstate the drawing contract. Draftwright "
            "therefore reports every occurrence as an unsupported completeness requirement.",
        ),
        "prismatic-pockets": (
            ("PrismaticPocket",),
            "https://github.com/pzfreo/draftwright/issues/1246",
            "The aggregate removes candidates also owned by the supported `pockets` family, so each "
            "remaining PrismaticPocket is a distinct recess not superseded by Pocket. Its section may "
            "be any planar polygon: width/length is false for triangles and a regular-polygon A/F "
            "callout is incomplete in the general case. Draftwright therefore reports every "
            "surviving occurrence as an unsupported completeness requirement.",
        ),
        "angled-steps": (
            ("AngledStep",),
            "https://github.com/pzfreo/draftwright/issues/1247",
            "Introduced by 0.2.5 to stop `recognise_chamfers` reporting step slants as chamfers "
            "(precision 44% -> 78%). The aggregate reconciles the shared slanted face in favour of "
            "AngledStep, but its angle, legs and run length do not themselves decide which drawing "
            "requirements or section/detail view are required. Draftwright therefore reports every "
            "occurrence as an unsupported completeness requirement.",
        ),
    }
)

DEFERRED_FAMILIES: frozenset[str] = frozenset({"oriented-slot-patterns", "oriented-slots"})


@dataclass(frozen=True)
class OwnerlessOccurrencePolicy:
    """Settled consumer meaning for a physical occurrence with no IR owner."""

    disposition: OwnerlessDisposition
    reason_code: str
    tracking: str | None


def ownerless_occurrence_policy(family: str) -> OwnerlessOccurrencePolicy | None:
    """Return policy for one public ``RecognitionEvidence.family`` identifier."""

    if type(family) is not str:
        raise TypeError("recognition evidence family must be an exact str")
    family_id = family.replace("_", "-")
    evidence_only = EVIDENCE_ONLY_FAMILIES.get(family_id)
    if evidence_only is not None:
        return OwnerlessOccurrencePolicy(
            disposition="evidence_only",
            reason_code=evidence_only,
            tracking=None,
        )
    if family_id == GEOMETRY_ONLY_FAMILY_ID:
        return OwnerlessOccurrencePolicy(
            disposition="evidence_only",
            reason_code="geometry_only_critique",
            tracking=None,
        )
    spec = UNSUPPORTED_FAMILIES.get(family_id)
    if spec is None:
        return None
    disposition: OwnerlessDisposition = (
        "deferred" if family_id in DEFERRED_FAMILIES else "unsupported"
    )
    return OwnerlessOccurrencePolicy(
        disposition=disposition,
        reason_code=(
            "consumer_semantics_deferred"
            if disposition == "deferred"
            else "consumer_semantics_unsupported"
        ),
        tracking=spec[1],
    )
