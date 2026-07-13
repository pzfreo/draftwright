"""The part-drawing compiler IR (ADR 0008).

A stable intermediate representation (`PartModel` of `Feature` objects exposing
`DimParameter`s) sitting between the feature *detectors* (front-ends adapting the
recognition heuristics) and a *dimensioning planner* (back-end). The narrow waist
that lets new shapes be new types, not new branches.

**Status:** wired into `build_drawing` in production (ADR 0008 convergence,
in progress). `build_part_model` is built once per build from `_analyse`'s single
feature inventory (Amendment 5) and consumed by the renderers in
`annotations/from_model.py` (turned step lengths/diameters, centre marks, envelope
width/depth, slots) and by the lint coverage checks. Remaining engine passes
(holes, sections, PMI, the prismatic step-ladder) are migrating onto it — see
`docs/plans/0008-convergence-roadmap.md`.

- :mod:`.ir` — the IR: `DimParameter`, `Datum`, `Frame`, the `Feature` protocol,
  the concrete feature types, and `PartModel`.
- :mod:`.detect` — `build_part_model`: run the detectors, collect features.
- :mod:`.planner` — `plan_dimensions`: convention rules over `DimParameter`s.
"""

from __future__ import annotations

from draftwright.model.declare import (
    boss,
    chamfer,
    control_frame,
    datum,
    envelope,
    fillet,
    finish,
    hole,
    note,
    pattern,
    plate,
    pocket,
    slot,
    step,
    step_level,
)
from draftwright.model.detect import build_part_model, build_pmi_features
from draftwright.model.ir import (
    AUTHORED_DIMENSION_KINDS,
    AuthoredDimension,
    BossFeature,
    ChamferFeature,
    Datum,
    DimParameter,
    EnvelopeFeature,
    Feature,
    FilletFeature,
    Frame,
    HoleFeature,
    PartModel,
    PatternFeature,
    PlateFeature,
    PmiFeature,
    PocketFeature,
    RotationalFeature,
    SlotFeature,
    StepFeature,
    StepLevelFeature,
    display,
)
from draftwright.model.planner import (
    DimensionGroup,
    PlannedDimension,
    SectionPlan,
    plan_dimensions,
    plan_sections,
)

__all__ = [
    "AuthoredDimension",
    "AUTHORED_DIMENSION_KINDS",
    "BossFeature",
    "ChamferFeature",
    "FilletFeature",
    "Datum",
    "DimParameter",
    "EnvelopeFeature",
    "DimensionGroup",
    "Feature",
    "Frame",
    "HoleFeature",
    "PartModel",
    "PatternFeature",
    "PmiFeature",
    "PocketFeature",
    "RotationalFeature",
    "PlannedDimension",
    "SlotFeature",
    "StepFeature",
    "PlateFeature",
    "StepLevelFeature",
    "build_part_model",
    "build_pmi_features",
    "boss",
    "chamfer",
    "fillet",
    "control_frame",
    "datum",
    "envelope",
    "finish",
    "hole",
    "note",
    "pattern",
    "plate",
    "pocket",
    "slot",
    "step",
    "step_level",
    "display",
    "SectionPlan",
    "plan_dimensions",
    "plan_sections",
]
