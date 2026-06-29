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


def _labels(dwg):
    return sorted(str(o.label) for o in dwg._named.values() if getattr(o, "label", None))


def test_prismatic_plate_complete_and_clean():
    part = _plate()  # 100×60×12 with two ø8 holes
    dwg = build_drawing(part, number="X", auto_dims=False)  # view scaffold only
    render_into(dwg, build_part_model(part))
    s = dwg.lint_summary()
    assert s["passed"] and s["score"] == 1.0 and s["by_code"] == {}  # clean
    # complete: the overall envelope dims are present (plus the two hole callouts)
    assert {"100", "60", "12"} <= set(_labels(dwg))


def test_flange_od_and_pattern_complete_and_clean():
    import math

    part = Cylinder(40, 8)  # round body, OD ø80
    for i in range(6):
        a = i * math.pi / 3
        part -= Pos(25 * math.cos(a), 25 * math.sin(a), 0) * Cylinder(3, 20)
    dwg = build_drawing(part, number="X", auto_dims=False)
    render_into(dwg, build_part_model(part))
    s = dwg.lint_summary()
    assert s["passed"] and s["score"] == 1.0 and s["by_code"] == {}  # OD covered → clean
    assert "ø80" in _labels(dwg)  # the OD, not a width×depth box


def test_dense_plate_callouts_dont_collide():
    # Four holes: collision-aware placement keeps the callouts clear of the views
    # and each other (the layout-solver integration, not fixed offsets).
    part = Box(120, 80, 12)
    for x in (-40, 40):
        for y in (-20, 20):
            part -= Pos(x, y, 0) * Cylinder(4, 30)
    dwg = build_drawing(part, number="X", auto_dims=False)
    n = render_into(dwg, build_part_model(part))
    assert n >= 4  # at least the four hole callouts (plus overall dims)
    s = dwg.lint_summary()
    assert s["by_code"].get("annotation_overlap", 0) == 0
    assert s["by_code"].get("view_annotation_overlap", 0) == 0


def test_model_pipeline_drawing_exports(tmp_path):
    part = Box(80, 60, 12) - Pos(0, 0, 0) * Cylinder(4, 30)
    dwg = build_drawing(part, number="X", auto_dims=False)
    render_into(dwg, build_part_model(part))
    svg, dxf = dwg.export(str(tmp_path / "slice"))
    assert os.path.getsize(svg) > 0 and os.path.getsize(dxf) > 0  # renders real geometry
