"""The part-drawing compiler IR — prototype (ADR 0008).

A vertical slice proving the architecture: a stable intermediate representation
(`PartModel` of `Feature` objects exposing `DimParameter`s) sitting between the
feature *detectors* (front-ends adapting the recognition heuristics) and a
*dimensioning planner* (back-end). The narrow waist that lets new shapes be new
types, not new branches.

**Prototype status:** not yet wired into `build_drawing`. It builds a model from a
real solid and plans dimensions, exercised by `tests/test_part_model.py`, to
de-risk the protocol before any production rewiring (ADR 0008 migration step 1).

- :mod:`.ir` — the IR: `DimParameter`, `Datum`, `Frame`, the `Feature` protocol,
  the concrete feature types, and `PartModel`.
- :mod:`.detect` — `build_part_model`: run the detectors, collect features.
- :mod:`.planner` — `plan_dimensions`: convention rules over `DimParameter`s.
"""

from __future__ import annotations

from draftwright.model.detect import build_part_model
from draftwright.model.ir import (
    BossFeature,
    Datum,
    DimParameter,
    Feature,
    Frame,
    HoleFeature,
    PartModel,
    PatternFeature,
    StepFeature,
    display,
)
from draftwright.model.planner import DimensionGroup, PlannedDimension, plan_dimensions

__all__ = [
    "BossFeature",
    "Datum",
    "DimParameter",
    "DimensionGroup",
    "Feature",
    "Frame",
    "HoleFeature",
    "PartModel",
    "PatternFeature",
    "PlannedDimension",
    "StepFeature",
    "build_part_model",
    "display",
    "plan_dimensions",
]
