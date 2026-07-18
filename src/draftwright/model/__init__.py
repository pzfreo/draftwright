"""The part-drawing compiler IR (ADR 0008).

A stable intermediate representation (`PartModel` of `Feature` objects exposing
`DimParameter`s) sitting between the feature *detectors* (front-ends adapting the
recognition heuristics) and a *dimensioning planner* (back-end). The narrow waist
that lets new shapes be new types, not new branches.

**Status:** the ADR 0008 convergence is **complete — one path** (2026-06-30).
`build_part_model` runs once per build from `_analyse`'s single feature
inventory (Amendment 5); every render pass consumes the IR — the
`annotations/from_model.py` renderers (turned/diameters, centre marks,
envelope, step lengths, height ladder, PMI, GD&T, …), the hole/section passes
(`annotations/holes.py` / `sections.py` via `plan_dimensions` /
`plan_sections`), and page/scale sizing (Amendment 8). Coverage lint alone
reads recognised geometry, by design (ground truth, not the plan).

- :mod:`.ir` — the IR: `DimParameter`, `Datum`, `Frame`, the `Feature` protocol,
  the concrete feature types, and `PartModel`.
- :mod:`.detect` — `build_part_model`: run the detectors, collect features.
- :mod:`.planner` — `plan_dimensions`: convention rules over `DimParameter`s.
"""

from __future__ import annotations

from draftwright.model.declare import (
    authored_dimension,
    boss,
    chamfer,
    control_frame,
    datum,
    envelope,
    fillet,
    finish,
    flat,
    groove,
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
    FlatFeature,
    Frame,
    GrooveFeature,
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
    "FlatFeature",
    "GrooveFeature",
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
    "authored_dimension",
    "build_part_model",
    "build_pmi_features",
    "boss",
    "chamfer",
    "fillet",
    "control_frame",
    "datum",
    "envelope",
    "finish",
    "flat",
    "groove",
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
