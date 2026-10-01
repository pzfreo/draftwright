"""Draftwright's consumer metadata for physical and local drafting families.

Provider identifiers, aggregate fields, and issued evidence families are separate:
pattern aggregates do not necessarily issue their own occurrence references. The
provider's record schemas and the rank-7 implementation bindings remain with their
respective contract owners.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

OwnershipClass = Literal[
    "direct", "groupable", "nested", "conditional", "ownerless", "unclassified"
]
PolicyStatus = Literal["supported", "unsupported", "deferred", "evidence_only", "local"]


@dataclass(frozen=True)
class FamilyRow:
    id: str
    provider_id: str | None
    evidence_family: str | None
    result_attr: str | None
    ownership_class: OwnershipClass
    requirement_source: bool = False
    lint_audited: bool = False
    policy_status: PolicyStatus = "supported"
    requirement_key: str | None = None
    quality_key: str | None = None


# One row per semantic family. Local rows have no independent provider capability;
# some are requirement grammars over the same physical section-recess occurrence.
FAMILIES: tuple[FamilyRow, ...] = (
    FamilyRow(
        "angled-steps",
        "angled-steps",
        "angled_steps",
        "angled_steps",
        "ownerless",
        lint_audited=True,
        policy_status="unsupported",
        quality_key="angled_steps",
    ),
    FamilyRow("blends", "blends", "blends", "blends", "direct", True, True, quality_key="blends"),
    FamilyRow("bosses", "bosses", "bosses", "bosses", "conditional", True, quality_key="bosses"),
    FamilyRow(
        "chamfers",
        "chamfers",
        "chamfers",
        "chamfers",
        "direct",
        True,
        True,
        quality_key="chamfers",
    ),
    FamilyRow(
        "circular-blind-steps",
        "circular-blind-steps",
        "circular_blind_steps",
        "circular_blind_steps",
        "direct",
        True,
        True,
        quality_key="circular_blind_steps",
    ),
    FamilyRow("countersinks", "countersinks", "countersinks", "countersinks", "nested"),
    FamilyRow(
        "double-d-bores",
        "double-d-bores",
        "double_d_bores",
        "double_d_bores",
        "direct",
        quality_key="profiled_bores",
    ),
    FamilyRow(
        "step-levels",
        "face-levels",
        "step_levels",
        "step_levels",
        "ownerless",
        policy_status="evidence_only",
    ),
    FamilyRow(
        "fillets", "fillets", "fillets", "fillets", "direct", True, True, quality_key="fillets"
    ),
    FamilyRow("flats", "flats", "flats", "flats", "direct", True, True, quality_key="flats"),
    FamilyRow(
        "grooves", "grooves", "grooves", "grooves", "direct", True, True, quality_key="grooves"
    ),
    FamilyRow(
        "gusset-rib-patterns",
        "gusset-rib-patterns",
        None,
        "gusset_rib_patterns",
        "unclassified",
        quality_key="gusset_ribs",
    ),
    FamilyRow(
        "gusset-ribs",
        "gusset-ribs",
        "gusset_ribs",
        "gusset_ribs",
        "groupable",
        True,
        True,
        quality_key="gusset_ribs",
    ),
    FamilyRow(
        "hole-patterns",
        "hole-patterns",
        "hole_patterns",
        "hole_patterns",
        "conditional",
        True,
        True,
        quality_key="hole_patterns",
    ),
    FamilyRow("holes", "holes", "holes", "holes", "groupable", True, True, quality_key="holes"),
    FamilyRow(
        "oriented-slot-patterns",
        "oriented-slot-patterns",
        None,
        "oriented_slot_patterns",
        "ownerless",
        policy_status="deferred",
    ),
    FamilyRow(
        "oriented-slots",
        "oriented-slots",
        "oriented_slots",
        "oriented_slots",
        "direct",
        True,
        True,
        quality_key="oriented_slots",
    ),
    FamilyRow(
        "paired-ramp-steps",
        "paired-ramp-steps",
        "paired_ramp_steps",
        "paired_ramp_steps",
        "direct",
        True,
        True,
        quality_key="paired_ramp_steps",
    ),
    FamilyRow(
        "plates", "plates", "plates", "plates", "conditional", True, True, quality_key="plates"
    ),
    FamilyRow(
        "polygonal-bosses",
        "polygonal-bosses",
        "polygonal_bosses",
        "polygonal_bosses",
        "direct",
        True,
        True,
        quality_key="polygonal_bosses",
    ),
    FamilyRow(
        "polygonal-stock",
        "polygonal-stock",
        "polygonal_stock",
        "polygonal_stock",
        "direct",
        True,
        True,
        quality_key="polygonal_stock",
    ),
    FamilyRow(
        "pads", "rectangular-pads", "pads", "pads", "direct", True, True, quality_key="pads"
    ),
    FamilyRow(
        "repeating-radial-profiles",
        "repeating-radial-profiles",
        "repeating_radial_profiles",
        "repeating_radial_profiles",
        "ownerless",
        policy_status="evidence_only",
        quality_key="repeating_radial_profiles",
    ),
    FamilyRow("risers", "risers", "risers", "risers", "ownerless", policy_status="evidence_only"),
    FamilyRow(
        "section-recesses",
        "section-recesses",
        "section_recesses",
        "section_recesses",
        "groupable",
        True,
        True,
        quality_key="section_recesses",
    ),
    FamilyRow(
        "slot-patterns",
        "slot-patterns",
        None,
        "slot_patterns",
        "unclassified",
        True,
        True,
        quality_key="slot_patterns",
    ),
    FamilyRow("slots", "slots", "slots", "slots", "groupable", True, True, quality_key="slots"),
    FamilyRow(
        "through-steps",
        "through-steps",
        "through_steps",
        "through_steps",
        "conditional",
        True,
        True,
        quality_key="through_steps",
    ),
    FamilyRow(
        "turned-steps",
        "turned-steps",
        "turned_steps",
        "turned_steps",
        "conditional",
        True,
        True,
        quality_key="turned_steps",
    ),
    FamilyRow("channels", None, None, None, "unclassified", True, True, "local", "channels"),
    FamilyRow(
        "outer-profile-angles",
        None,
        None,
        None,
        "unclassified",
        lint_audited=True,
        policy_status="local",
        requirement_key="outer_profile_angles",
    ),
    FamilyRow(
        "passages",
        None,
        None,
        None,
        "unclassified",
        lint_audited=True,
        policy_status="local",
        requirement_key="passages",
    ),
    FamilyRow("pockets", None, None, None, "unclassified", True, True, "local", "pockets"),
    FamilyRow(
        "pocket-patterns", None, None, None, "unclassified", True, True, "local", "pocket_patterns"
    ),
    FamilyRow(
        "prismatic-pockets",
        None,
        None,
        None,
        "unclassified",
        lint_audited=True,
        policy_status="local",
        requirement_key="prismatic_pockets",
    ),
    FamilyRow(
        "rectangular-blind-slots",
        None,
        None,
        None,
        "unclassified",
        True,
        True,
        "local",
        "rectangular_blind_slots",
    ),
    FamilyRow(
        "round-bottom-blind-slots",
        None,
        None,
        None,
        "unclassified",
        True,
        True,
        "local",
        "round_bottom_blind_slots",
    ),
    FamilyRow(
        "section-recess-patterns",
        None,
        None,
        "section_recess_patterns",
        "unclassified",
        policy_status="local",
        quality_key="pocket_patterns",
    ),
)


def ownership_families(ownership_class: OwnershipClass) -> frozenset[str]:
    """Return established evidence names for one static ownership category."""

    return frozenset(
        row.evidence_family
        for row in FAMILIES
        if row.ownership_class == ownership_class and row.evidence_family is not None
    )
