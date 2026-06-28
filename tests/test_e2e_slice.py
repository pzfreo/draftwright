"""End-to-end slice (ADR 0008) — a complete drawing via the new pipeline.

The honest whole-pipeline test the pivot called for: a real part →
detect → model → plan → render → a Drawing whose annotations come from the **new**
pipeline (`build_drawing(auto_dims=False)` gives only the view scaffold), judged by
**correctness** (lint passes, coverage complete, it exports) — *not* by equivalence
to the engine.

Scope today: hole callouts, **placed clear of the views and of each other** via the
layout search (no more naive fixed offsets). The remaining gap the slice surfaces is
overall/envelope + OD dims, which the new path doesn't produce yet — so a part with
an un-dimensioned OD (e.g. a flange) still lints with `feature_not_dimensioned`.
"""

import os

from build123d import Box, Cylinder, Pos

from draftwright import build_drawing
from draftwright.model import build_part_model
from draftwright.model.render import render_into


def _plate():
    return Box(100, 60, 12) - Pos(-30, 0, 0) * Cylinder(4, 30) - Pos(30, 0, 0) * Cylinder(4, 30)


def test_complete_drawing_via_model_pipeline_is_lint_clean():
    part = _plate()
    dwg = build_drawing(part, number="X", auto_dims=False)  # view scaffold only
    n = render_into(dwg, build_part_model(part))
    assert n == 2  # both holes called out by the new pipeline
    s = dwg.lint_summary()
    assert s["passed"] and s["score"] == 1.0  # fully clean — callouts cleared the views
    assert s["by_code"] == {}  # no overlap, no missing coverage


def test_dense_plate_callouts_dont_collide():
    # Four holes: collision-aware placement keeps the callouts clear of the views
    # and each other (the layout-solver integration, not fixed offsets).
    part = Box(120, 80, 12)
    for x in (-40, 40):
        for y in (-20, 20):
            part -= Pos(x, y, 0) * Cylinder(4, 30)
    dwg = build_drawing(part, number="X", auto_dims=False)
    assert render_into(dwg, build_part_model(part)) == 4
    s = dwg.lint_summary()
    assert s["by_code"].get("annotation_overlap", 0) == 0
    assert s["by_code"].get("view_annotation_overlap", 0) == 0


def test_model_pipeline_drawing_exports(tmp_path):
    part = Box(80, 60, 12) - Pos(0, 0, 0) * Cylinder(4, 30)
    dwg = build_drawing(part, number="X", auto_dims=False)
    render_into(dwg, build_part_model(part))
    svg, dxf = dwg.export(str(tmp_path / "slice"))
    assert os.path.getsize(svg) > 0 and os.path.getsize(dxf) > 0  # renders real geometry
