"""Recognition-owned semantic requirement ledgers shared by lint and reports."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from draftwright.linting.angular import profile_angle_requirement_outcomes
from draftwright.linting.blend_coverage import blend_requirement_outcomes
from draftwright.linting.chamfer_coverage import chamfer_requirement_outcomes
from draftwright.linting.channel_coverage import channel_requirement_outcomes
from draftwright.linting.circular_blind_step_coverage import (
    circular_blind_step_requirement_outcomes,
)
from draftwright.linting.fillet_coverage import fillet_requirement_outcomes
from draftwright.linting.flat_coverage import flat_requirement_outcomes
from draftwright.linting.groove_coverage import groove_requirement_outcomes
from draftwright.linting.hole_coverage import hole_requirement_outcomes
from draftwright.linting.oriented_slot_coverage import oriented_slot_requirement_outcomes
from draftwright.linting.pad_coverage import pad_requirement_outcomes
from draftwright.linting.paired_ramp_step_coverage import paired_ramp_step_requirement_outcomes
from draftwright.linting.plate_coverage import plate_requirement_outcomes
from draftwright.linting.pocket_coverage import pocket_requirement_outcomes
from draftwright.linting.pocket_pattern_coverage import pocket_pattern_requirement_outcomes
from draftwright.linting.polygonal_boss_coverage import polygonal_boss_requirement_outcomes
from draftwright.linting.polygonal_stock_coverage import polygonal_stock_outcomes
from draftwright.linting.rectangular_blind_slot_coverage import (
    rectangular_blind_slot_requirement_outcomes,
)
from draftwright.linting.round_bottom_blind_slot_coverage import (
    round_bottom_blind_slot_requirement_outcomes,
)
from draftwright.linting.section_recess_coverage import unsupported_section_recess_outcomes
from draftwright.linting.slot_coverage import slot_requirement_outcomes
from draftwright.linting.through_step_coverage import through_step_requirement_outcomes
from draftwright.linting.turned_step_coverage import turned_step_requirement_outcomes

# Source-family coverage of this collector, independent of which outcomes survived
# projection. Parameter grammars remain exclusively in their existing producers.
REQUIREMENT_SOURCE_FAMILIES = frozenset(
    {
        "section_recesses",
        "chamfers",
        "blends",
        "channels",
        "circular_blind_steps",
        "fillets",
        "paired_ramp_steps",
        "through_steps",
        "turned_steps",
        "flats",
        "grooves",
        "holes",
        "hole_patterns",
        "oriented_slots",
        "pads",
        "plates",
        "polygonal_bosses",
        "polygonal_stock",
        "pockets",
        "pocket_patterns",
        "rectangular_blind_slots",
        "round_bottom_blind_slots",
        "slots",
        "slot_patterns",
    }
)


def requirement_source_census(evidence, ownership):
    """Exact accepted records whose families have a physical requirement ledger."""
    if ownership.evidence is not evidence:
        raise ValueError("requirement census needs the same ownership authority")
    required = []
    for reference in evidence.features:
        family = evidence.family(reference)
        if family not in REQUIREMENT_SOURCE_FAMILIES:
            continue
        binding = ownership.binding_for(reference)
        reason = binding.reason_code if binding is not None else None
        # Exact conversion-owned absorption proofs account for these records in a
        # different physical grammar; they do not acquire another parameter ledger.
        if (family, reason) in {
            ("section_recesses", "channel_step_level_owner"),
            ("turned_steps", "turned_step_groove_owner"),
        }:
            continue
        if family == "section_recesses" and ownership.status(reference) == "unexpectedly_missing":
            # An unresolved accepted recess has no established drafting grammar.
            # Its source and missing-owner disposition remain in the occurrence
            # report and cannot earn credit or a bounded-clear result.
            continue
        required.append((reference, evidence.record(reference)))
    return tuple(required)


def recognized_requirement_outcomes(
    recognition,
    features,
    registry,
    omissions,
    *,
    dimension_plan=None,
    part=None,
    evidence=None,
    ownership=None,
    datum=None,
) -> Mapping[str, tuple[Any, ...]]:
    """Return typed physical-requirement ledgers shared by lint and reports.

    The denominator is recognition-owned: callers may project or count these outcomes,
    but must not reconstruct physical requirements from final IR parameters.
    """

    if evidence is not None and evidence.result is not recognition:
        raise ValueError("requirement evidence and recognition must belong to the same run")

    outcomes: dict[str, list] = {
        "section_recesses": unsupported_section_recess_outcomes(recognition),
        "chamfers": chamfer_requirement_outcomes(recognition, features, registry, omissions),
        "blends": blend_requirement_outcomes(recognition, features, registry, omissions),
        "channels": channel_requirement_outcomes(recognition, features, registry, omissions),
        "circular_blind_steps": circular_blind_step_requirement_outcomes(
            recognition, features, registry, omissions
        ),
        "fillets": fillet_requirement_outcomes(recognition, features, registry, omissions),
        "paired_ramp_steps": paired_ramp_step_requirement_outcomes(
            recognition, features, registry, omissions
        ),
        "through_steps": through_step_requirement_outcomes(
            recognition, features, registry, omissions, plan=dimension_plan
        ),
        "turned_steps": turned_step_requirement_outcomes(
            recognition, features, registry, omissions
        ),
        "flats": flat_requirement_outcomes(recognition, features, registry, omissions),
        "grooves": groove_requirement_outcomes(recognition, features, registry, omissions),
        "holes": [],
        "hole_patterns": [],
        "oriented_slots": oriented_slot_requirement_outcomes(
            recognition, features, registry, omissions
        ),
        "pads": pad_requirement_outcomes(recognition, features, registry, omissions, datum=datum),
        "plates": plate_requirement_outcomes(
            recognition, features, registry, omissions, part=part
        ),
        "polygonal_bosses": polygonal_boss_requirement_outcomes(
            recognition, features, registry, omissions
        ),
        "polygonal_stock": polygonal_stock_outcomes(recognition, features, registry, omissions),
        "pockets": pocket_requirement_outcomes(
            recognition, features, registry, omissions, datum=datum
        ),
        "pocket_patterns": pocket_pattern_requirement_outcomes(
            recognition, features, registry, omissions
        ),
        "rectangular_blind_slots": rectangular_blind_slot_requirement_outcomes(
            recognition, features, registry, omissions
        ),
        "round_bottom_blind_slots": round_bottom_blind_slot_requirement_outcomes(
            recognition, features, registry, omissions
        ),
        "slots": [],
        "slot_patterns": [],
    }
    for outcome in slot_requirement_outcomes(recognition, features, registry, omissions):
        outcomes["slot_patterns" if outcome.source_kind == "slot_pattern" else "slots"].append(
            outcome
        )
    for hole_outcome in hole_requirement_outcomes(recognition, features, registry, omissions):
        outcomes[
            "hole_patterns" if hole_outcome.source_kind == "hole_pattern" else "holes"
        ].append(hole_outcome)
    if evidence is not None:
        outcomes["outer_profile_angles"] = profile_angle_requirement_outcomes(
            evidence, ownership, features, registry, omissions
        )
    return MappingProxyType({family: tuple(items) for family, items in outcomes.items()})
