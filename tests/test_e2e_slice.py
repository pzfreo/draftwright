"""End-to-end slice (ADR 0008) — a complete drawing via the compiler pipeline.

The honest whole-pipeline test the pivot called for: a real part → detect → model →
plan → render, judged by **correctness** (lint passes, coverage complete, it exports)
— not by equivalence to the engine. These now drive the **production** path
(`build_drawing`), which *is* the IR pipeline (detectors → `PartModel` → planner →
the `from_model` renderers); the test-only `render_into` parallel was retired in #251.
"""

import os

from build123d import Box, Cylinder, Pos

from draftwright import build_drawing


def _plate():
    return Box(100, 60, 12) - Pos(-30, 0, 0) * Cylinder(4, 30) - Pos(30, 0, 0) * Cylinder(4, 30)


def _labels(dwg):
    return sorted(str(o.label) for _, o in dwg.iter_annotations() if getattr(o, "label", None))


def test_prismatic_plate_sized_and_error_free():
    part = _plate()  # 100×60×12 with two ø8 holes
    dwg = build_drawing(part, number="X")
    s = dwg.lint_summary()
    assert s["passed"]  # no lint ERRORS
    assert s["by_code"].get("feature_not_dimensioned", 0) == 0  # all sizes covered
    assert {"100", "60", "12"} <= set(_labels(dwg))  # overall dims present
    # The pipeline now places centre marks + location dims for every hole, so the
    # completeness gaps the early slice documented are closed.
    assert s["by_code"].get("feature_no_centermark", 0) == 0
    assert s["by_code"].get("feature_not_located", 0) == 0


def test_flange_od_sized_and_error_free():
    import math

    part = Cylinder(40, 8)  # round body, OD ø80
    for i in range(6):
        a = i * math.pi / 3
        part -= Pos(25 * math.cos(a), 25 * math.sin(a), 0) * Cylinder(3, 20)
    dwg = build_drawing(part, number="X")
    s = dwg.lint_summary()
    assert s["passed"]  # no errors
    assert s["by_code"].get("feature_not_dimensioned", 0) == 0  # OD covered
    assert "ø80" in _labels(dwg)  # the OD, not a width×depth box
    assert s["by_code"].get("feature_no_centermark", 0) == 0
    assert s["by_code"].get("feature_not_located", 0) == 0


def test_dense_plate_callouts_dont_collide():
    # Four holes: collision-aware placement keeps the callouts clear of the views
    # and each other (the layout-solver integration, not fixed offsets).
    part = Box(120, 80, 12)
    for x in (-40, 40):
        for y in (-20, 20):
            part -= Pos(x, y, 0) * Cylinder(4, 30)
    dwg = build_drawing(part, number="X")
    assert any(n.startswith("hc_") for n in dwg.annotations())  # hole callouts placed
    s = dwg.lint_summary()
    assert s["by_code"].get("annotation_overlap", 0) == 0
    assert s["by_code"].get("view_annotation_overlap", 0) == 0


def test_model_pipeline_drawing_exports(tmp_path):
    part = Box(80, 60, 12) - Pos(0, 0, 0) * Cylinder(4, 30)
    dwg = build_drawing(part, number="X")
    svg, dxf = dwg.export(str(tmp_path / "slice"))
    assert os.path.getsize(svg) > 0 and os.path.getsize(dxf) > 0  # renders real geometry
