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


def test_disjoint_pads_at_three_heights_keep_their_local_bases():
    minimum = (Align.MIN, Align.MIN, Align.MIN)
    part = Box(140, 50, 10, align=minimum)
    for x, height in ((10, 5), (60, 10), (110, 15)):
        part += Pos(x, 15, 10) * Box(20, 20, height, align=minimum)
    pads = recognise_rectangular_pads(part)
    assert [(p.z0, p.z1) for p in pads] == [(10.0, 15.0), (10.0, 20.0), (10.0, 25.0)]


def test_nested_staircase_ledges_are_not_independent_pads():
    parts = [Pos(0, 0, 3) * Box(20, 16, 6)]
    z = 6
    for width in (16, 13, 10, 7, 5):
        parts.append(Pos(0, 0, z + 1.5) * Box(width, 12, 3))
        z += 3
    part = parts[0]
    for tier in parts[1:]:
        part += tier
    assert recognise_rectangular_pads(part) == []


def test_pad_declaration_and_sheet_emission_round_trip_surface():
    feature = pad(Box(40, 18, 14))
    assert isinstance(feature, PadFeature)
    assert (feature.length, feature.width) == (40.0, 18.0)

    model = build_drawing(_case_study()).model()
    source = emit_sheet_script(model, "part", "out", title="T", number="N")
    assert source.count("sheet.pad(") == 4
    compile(source, "<generated-pad-sheet>", "exec")
    # Execute the generated declarations as well: syntax alone cannot catch a
    # PadFeature argument/order drift between the emitter and Sheet facade.
    executable = source.rsplit("\nsheet.export(", 1)[0] + "\nround_tripped = sheet.build()\n"
    namespace = {"part": _case_study()}
    exec(executable, namespace)  # noqa: S102 — exercising our generated source
    rebuilt = namespace["round_tripped"].model()
    assert [f for f in rebuilt.features if f.kind == "pad"] == [
        f for f in model.features if f.kind == "pad"
    ]


def test_auto_drawing_defines_pad_footprints_and_pocket_locations():
    drawing = build_drawing(_case_study())
    assert [f.kind for f in drawing.model().features].count("pad") == 4
    names = set(drawing.annotations())
    assert len({name for name in names if name.startswith("m_pad")}) == 8
    assert {"m_locy1", "m_locy3"} <= names  # pocket centres at Y=30 and Y=90
    summary = drawing.lint_summary()
    assert "pad_footprint_not_defined" not in summary["by_code"]
    assert "pocket_not_located" not in summary["by_code"]
    assert summary["score"] == 1.0


def test_removing_one_pad_size_is_not_credited_from_an_aligned_sibling():
    drawing = build_drawing(_case_study())
    drawing.remove("m_pad3_width")
    summary = drawing.lint_summary()
    assert summary["by_code"]["pad_footprint_not_defined"] == 1
    assert summary["score"] < 1.0


def test_side_opening_pocket_gets_two_in_plane_location_dimensions():
    part = Box(60, 50, 40) - Pos(20, 8, 5) * Box(20, 16, 18)
    drawing = build_drawing(part)
    assert {"m_pocket0_pos_long", "m_pocket0_pos_width"} <= set(drawing.annotations())
    assert "pocket_not_located" not in drawing.lint_summary()["by_code"]


def test_unrelated_dimension_endpoint_does_not_locate_side_pocket():
    part = Box(60, 50, 40) - Pos(20, 8, 5) * Box(20, 16, 18)
    drawing = build_drawing(part)
    drawing.remove("m_pocket0_pos_long")
    assert drawing.lint_summary()["by_code"]["pocket_not_located"] == 1


def test_omitting_furniture_reports_both_coverage_gaps_and_reduces_score():
    summary = build_drawing(_case_study(), auto_dims=False).lint_summary()
    assert summary["by_code"]["pad_footprint_not_defined"] == 1
    assert summary["by_code"]["pocket_not_located"] == 1
    assert summary["warnings"] >= 2
    assert summary["score"] < 1.0
    assert summary["geometry_issues"] >= 2
