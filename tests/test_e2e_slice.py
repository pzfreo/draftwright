"""End-to-end slice (ADR 0008) — a complete drawing via the new pipeline.

The honest whole-pipeline test the pivot called for: a real part →
detect → model → plan → render → a Drawing whose annotations come from the **new**
pipeline (`build_drawing(auto_dims=False)` gives only the view scaffold), judged by
**correctness** (lint passes, coverage complete, it exports) — *not* by equivalence
to the engine.

Scope: the IR-driven hole callouts + envelope width/depth (`render_into`), **placed
clear of the views and of each other** via the layout search. The drawing lints
clean for prismatic plates and simple rotational parts. `render_into` is the
test-only demonstration path (production uses the per-feature renderers wired into
the orchestrator); it is retired once the holes epic supersedes it (#251).
"""

import os

from build123d import Box, Cylinder, Pos

from draftwright import build_drawing
from draftwright.annotations.from_model import render_into
from draftwright.model import build_part_model


def _plate():
    return Box(100, 60, 12) - Pos(-30, 0, 0) * Cylinder(4, 30) - Pos(30, 0, 0) * Cylinder(4, 30)


def _labels(dwg):
    return sorted(str(o.label) for o in dwg._named.values() if getattr(o, "label", None))


def test_prismatic_plate_sized_and_error_free():
    part = _plate()  # 100×60×12 with two ø8 holes
    dwg = build_drawing(part, number="X", auto_dims=False)  # view scaffold only
    render_into(dwg, build_part_model(part))
    s = dwg.lint_summary()
    assert s["passed"]  # no lint ERRORS
    assert s["by_code"].get("feature_not_dimensioned", 0) == 0  # all sizes covered
    assert {"100", "60", "12"} <= set(_labels(dwg))  # overall dims present
    # The strengthened lint (#218) correctly flags the new pipeline's remaining
    # completeness gap — no center marks / location dims. #220 closes these; this
    # assertion documents the gap and flips when it lands.
    assert s["by_code"].get("feature_no_centermark", 0) == 1  # one aggregated issue
    assert s["by_code"].get("feature_not_located", 0) == 1


def test_flange_od_sized_and_error_free():
    import math

    part = Cylinder(40, 8)  # round body, OD ø80
    for i in range(6):
        a = i * math.pi / 3
        part -= Pos(25 * math.cos(a), 25 * math.sin(a), 0) * Cylinder(3, 20)
    dwg = build_drawing(part, number="X", auto_dims=False)
    render_into(dwg, build_part_model(part))
    s = dwg.lint_summary()
    assert s["passed"]  # no errors
    assert s["by_code"].get("feature_not_dimensioned", 0) == 0  # OD covered
    assert "ø80" in _labels(dwg)  # the OD, not a width×depth box
    # bolt-circle holes are located by the pattern, so no feature_not_located;
    # they still lack center marks until #220.
    assert s["by_code"].get("feature_no_centermark", 0) == 1  # one issue, covers 6 holes
    assert s["by_code"].get("feature_not_located", 0) == 0


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
