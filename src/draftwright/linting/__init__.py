"""Linting for draftwright drawings (ADR 0007).

draftwright owns linting; ``build123d-drafting-helpers`` is the rendering
library. This package is the single home for it:

- :mod:`.issues` — ``LintIssue``, the structured lint result.
- :mod:`.structural` — ``lint_drawing``: duck-typed structural checks on a
  composed annotation list (overlap, page bounds, label-vs-measured). Vendored
  from ``build123d_drafting.helpers``; upstream copy frozen and deprecated.
- :mod:`.coverage` — ``lint_feature_coverage`` + ``CoverageState``: the
  feature-coverage completeness check and the signal the passes record.
- :mod:`.suggest` — ``_suggest_fix``: ready-to-paste fix snippets (#29).

Import the public surface from here, not the submodules.
"""

from __future__ import annotations

from draftwright.linting.angled_step_coverage import lint_angled_step_coverage
from draftwright.linting.blend_coverage import lint_blend_coverage
from draftwright.linting.chamfer_coverage import lint_chamfer_coverage
from draftwright.linting.channel_coverage import lint_channel_coverage
from draftwright.linting.circular_blind_step_coverage import lint_circular_blind_step_coverage
from draftwright.linting.coverage import (
    EXAMINABLE_DECLARED_KINDS,
    CoverageState,
    lint_axial_coverage,
    lint_boss_height_coverage,
    lint_declaration_reconciliation,
    lint_feature_coverage,
    lint_location_coverage,
    lint_principal_profile_coverage,
    lint_prismatic_coverage,
)
from draftwright.linting.evidence import (
    ClaimOutcome,
    lint_claimed_representations,
    verify_measurement_claims,
)
from draftwright.linting.fillet_coverage import lint_fillet_coverage
from draftwright.linting.flat_coverage import lint_flat_coverage
from draftwright.linting.gear_coverage import lint_declared_gear_coverage
from draftwright.linting.groove_coverage import lint_groove_coverage
from draftwright.linting.hole_coverage import lint_hole_coverage
from draftwright.linting.issues import LintIssue
from draftwright.linting.pad_coverage import lint_pad_coverage
from draftwright.linting.paired_ramp_step_coverage import lint_paired_ramp_step_coverage
from draftwright.linting.passage_coverage import lint_passage_coverage
from draftwright.linting.plate_coverage import lint_plate_coverage
from draftwright.linting.pmi_coverage import (
    lint_pmi_extraction,
    lint_pmi_ignored,
    lint_pmi_lowering,
    lint_pmi_rendering,
    pmi_stage_summary,
)
from draftwright.linting.pocket_coverage import lint_pocket_coverage
from draftwright.linting.pocket_pattern_coverage import lint_pocket_pattern_coverage
from draftwright.linting.polygonal_boss_coverage import lint_polygonal_boss_coverage
from draftwright.linting.polygonal_stock_coverage import lint_polygonal_stock_coverage
from draftwright.linting.prismatic_pocket_coverage import lint_prismatic_pocket_coverage
from draftwright.linting.profiled_bore_coverage import lint_profiled_bore_coverage
from draftwright.linting.rectangular_blind_slot_coverage import (
    lint_rectangular_blind_slot_coverage,
)
from draftwright.linting.round_bottom_blind_slot_coverage import (
    lint_round_bottom_blind_slot_coverage,
)
from draftwright.linting.slot_coverage import lint_slot_coverage
from draftwright.linting.structural import is_dimension_like, lint_drawing
from draftwright.linting.suggest import _suggest_fix
from draftwright.linting.through_step_coverage import lint_through_step_coverage

__all__ = [
    "ClaimOutcome",
    "CoverageState",
    "EXAMINABLE_DECLARED_KINDS",
    "LintIssue",
    "is_dimension_like",
    "lint_angled_step_coverage",
    "lint_claimed_representations",
    "verify_measurement_claims",
    "lint_hole_coverage",
    "lint_groove_coverage",
    "lint_polygonal_boss_coverage",
    "lint_polygonal_stock_coverage",
    "lint_pocket_coverage",
    "lint_pocket_pattern_coverage",
    "lint_prismatic_pocket_coverage",
    "_suggest_fix",
    "lint_axial_coverage",
    "lint_boss_height_coverage",
    "lint_blend_coverage",
    "lint_channel_coverage",
    "lint_circular_blind_step_coverage",
    "lint_chamfer_coverage",
    "lint_declaration_reconciliation",
    "lint_drawing",
    "lint_feature_coverage",
    "lint_flat_coverage",
    "lint_fillet_coverage",
    "lint_declared_gear_coverage",
    "lint_slot_coverage",
    "lint_through_step_coverage",
    "lint_location_coverage",
    "lint_passage_coverage",
    "lint_pad_coverage",
    "lint_plate_coverage",
    "lint_paired_ramp_step_coverage",
    "lint_pmi_ignored",
    "lint_pmi_extraction",
    "lint_pmi_lowering",
    "lint_pmi_rendering",
    "pmi_stage_summary",
    "lint_principal_profile_coverage",
    "lint_profiled_bore_coverage",
    "lint_prismatic_coverage",
    "lint_rectangular_blind_slot_coverage",
    "lint_round_bottom_blind_slot_coverage",
]
