"""Recognition-owned semantic requirement ledgers shared by lint and reports."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

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
from draftwright.linting.slot_coverage import slot_requirement_outcomes
from draftwright.linting.through_step_coverage import through_step_requirement_outcomes
from draftwright.linting.turned_step_coverage import turned_step_requirement_outcomes


def recognized_requirement_outcomes(
    recognition,
    features,
    registry,
    omissions,
    *,
    dimension_plan=None,
    part=None,
) -> Mapping[str, tuple[Any, ...]]:
    """Return typed physical-requirement ledgers shared by lint and reports.

    The denominator is recognition-owned: callers may project or count these outcomes,
    but must not reconstruct physical requirements from final IR parameters.
    """

    outcomes: dict[str, list] = {
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
        "pads": pad_requirement_outcomes(recognition, features, registry, omissions),
        "plates": plate_requirement_outcomes(
            recognition, features, registry, omissions, part=part
        ),
        "polygonal_bosses": polygonal_boss_requirement_outcomes(
            recognition, features, registry, omissions
        ),
        "polygonal_stock": polygonal_stock_outcomes(recognition, features, registry, omissions),
        "pockets": pocket_requirement_outcomes(recognition, features, registry, omissions),
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
    return MappingProxyType({family: tuple(items) for family, items in outcomes.items()})
