"""Regression coverage for #885: sparse recognition must not imply completeness."""

from build123d import Align, Box, Cylinder, Plane, Pos, SlotOverall, extrude

from draftwright import build_drawing
from draftwright.model import PadFeature, pad
from draftwright.recognition import recognise_rectangular_pads
from draftwright.sheet_emit import emit_sheet_script


def _case_study():
    minimum = (Align.MIN, Align.MIN, Align.MIN)
    part = Box(180, 120, 22, align=minimum)
    for x in (15, 125):
        for y in (0, 102):
            part += Pos(x, y, 22) * Box(40, 18, 14, align=minimum)
    for y in (30, 90):
        part -= Pos(35, y, 14) * extrude(Plane.XY * SlotOverall(42, 18), 8)
    for x in (50, 130):
        part -= Pos(x, 60, -1) * Cylinder(10, 24)
    return part


def test_rectangular_pad_analysis_recovers_distinct_footprints():
    pads = recognise_rectangular_pads(_case_study())
    assert {(p.x0, p.x1, p.y0, p.y1) for p in pads} == {
        (15.0, 55.0, 0.0, 18.0),
        (15.0, 55.0, 102.0, 120.0),
        (125.0, 165.0, 0.0, 18.0),
        (125.0, 165.0, 102.0, 120.0),
    }


def test_full_span_step_is_not_misclassified_as_bounded_pad():
    part = Box(100, 60, 20) + Pos(20, 0, 20) * Box(40, 60, 10)
    assert recognise_rectangular_pads(part) == []


def test_pad_declaration_and_sheet_emission_round_trip_surface():
    feature = pad(Box(40, 18, 14))
    assert isinstance(feature, PadFeature)
    assert (feature.length, feature.width) == (40.0, 18.0)

    model = build_drawing(_case_study()).model()
    source = emit_sheet_script(model, "part", "out", title="T", number="N")
    assert source.count("sheet.pad(") == 4
    compile(source, "<generated-pad-sheet>", "exec")


def test_auto_drawing_defines_pad_footprints_and_pocket_locations():
    drawing = build_drawing(_case_study())
    assert [f.kind for f in drawing.model().features].count("pad") == 4
    names = set(drawing.annotations())
    assert {"m_pad0_width", "m_pad1_width", "m_pad0_length", "m_pad2_length"} <= names
    assert {"m_locy1", "m_locy3"} <= names  # pocket centres at Y=30 and Y=90
    summary = drawing.lint_summary()
    assert "pad_footprint_not_defined" not in summary["by_code"]
    assert "pocket_not_located" not in summary["by_code"]
    assert summary["score"] == 1.0


def test_omitting_furniture_reports_both_coverage_gaps_and_reduces_score():
    summary = build_drawing(_case_study(), auto_dims=False).lint_summary()
    assert summary["by_code"]["pad_footprint_not_defined"] == 1
    assert summary["by_code"]["pocket_not_located"] == 1
    assert summary["warnings"] >= 2
    assert summary["score"] < 1.0
    assert summary["geometry_issues"] >= 2
