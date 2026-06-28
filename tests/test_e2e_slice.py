"""End-to-end slice (ADR 0008) — a complete drawing via the new pipeline.

The honest whole-pipeline test the pivot called for: a real part →
detect → model → plan → render → a Drawing whose annotations come from the **new**
pipeline (`build_drawing(auto_dims=False)` gives only the view scaffold), judged by
**correctness** (lint passes, coverage complete, it exports) — *not* by equivalence
to the engine.

Scope today: hole callouts. The slice deliberately surfaces what's still missing —
overall/envelope dims (not yet produced) and layout integration (callouts placed at
naive offsets, so a `view_annotation_overlap` *warning* can appear; that's the next
work — route placement through the ADR-0003 solver). Those are warnings/info, not
errors: the drawing is correct, not yet polished.
"""

import os

from build123d import Box, Cylinder, Pos

from draftwright import build_drawing
from draftwright.model import build_part_model
from draftwright.model.render import render_into


def _plate():
    return Box(100, 60, 12) - Pos(-30, 0, 0) * Cylinder(4, 30) - Pos(30, 0, 0) * Cylinder(4, 30)


def test_complete_drawing_via_model_pipeline():
    part = _plate()
    dwg = build_drawing(part, number="X", auto_dims=False)  # view scaffold only
    n = render_into(dwg, build_part_model(part))
    assert n == 2  # both holes called out by the new pipeline
    s = dwg.lint_summary()
    assert s["passed"]  # no lint ERRORS — the correctness bar (layout is warning-only)
    assert s["by_code"].get("feature_not_dimensioned", 0) == 0  # coverage complete


def test_model_pipeline_drawing_exports(tmp_path):
    part = Box(80, 60, 12) - Pos(0, 0, 0) * Cylinder(4, 30)
    dwg = build_drawing(part, number="X", auto_dims=False)
    render_into(dwg, build_part_model(part))
    svg, dxf = dwg.export(str(tmp_path / "slice"))
    assert os.path.getsize(svg) > 0 and os.path.getsize(dxf) > 0  # renders real geometry
