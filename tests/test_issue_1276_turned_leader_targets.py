"""#1276: turned edge treatments and cylindrical notes target profile surfaces."""

import warnings
from pathlib import Path

import pytest
from build123d import Box, Cylinder, GeomType, Pos, import_step
from build123d import fillet as b3d_fillet

from draftwright import build_drawing
from draftwright.model import boss, chamfer, groove, note
from draftwright.sheet import Sheet

GRM03 = Path(__file__).parent / "fixtures" / "grm03_thumbwheel_drive_screw.step"


def _annotation_by_label(drawing, prefix):
    return {
        drawing.get_annotation(name).label: (name, drawing.get_annotation(name))
        for name in drawing.annotations()
        if name.startswith(prefix)
    }


def _rounded_shaft():
    shaft = Pos(0, 0, 20) * Cylinder(15, 40) + Pos(0, 0, 55) * Cylinder(8, 30)
    circular_edges = [edge for edge in shaft.edges() if edge.geom_type == GeomType.CIRCLE]
    return b3d_fillet(circular_edges, 0.8)


def test_grm03_chamfers_read_in_profile_and_land_on_distinct_edge_sites():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        drawing = build_drawing(import_step(GRM03), number="GRM-03")

    features = [feature for feature in drawing.model().features if feature.kind == "chamfer"]
    assert len(features) == 3
    assert all(feature.turned for feature in features)
    profile_sites = [drawing.at("front", *feature.frame.origin)[:2] for feature in features]
    assert profile_sites[0] != pytest.approx(profile_sites[1])

    callouts = _annotation_by_label(drawing, "m_chamfer")
    grouped = callouts["2× C0.3"][1]
    single = callouts["C0.5"][1]
    assert drawing.view_of(callouts["2× C0.3"][0]) == "front"
    assert drawing.view_of(callouts["C0.5"][0]) == "front"
    assert grouped.tip[:2] == pytest.approx(profile_sites[0])
    assert single.tip[:2] == pytest.approx(profile_sites[2])
    assert "feature_leader_crossing" not in {issue.code for issue in drawing.lint()}


def test_rounded_shaft_fillets_read_in_profile_and_keep_the_physical_site():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        drawing = build_drawing(_rounded_shaft(), number="X")

    features = [feature for feature in drawing.model().features if feature.kind == "fillet"]
    assert len(features) == 4
    assert all(feature.turned for feature in features)
    name, leader = _annotation_by_label(drawing, "m_fillet")["4× R0.8"]
    assert drawing.view_of(name) == "front"
    assert leader.tip[:2] == pytest.approx(drawing.at("front", *features[0].frame.origin)[:2])
    assert not drawing.lint()


@pytest.mark.parametrize(
    ("axis", "view"),
    [("x", "front"), ("y", "side"), ("z", "front")],
)
def test_declared_turned_chamfer_has_xyz_profile_parity(axis, view):
    feature = chamfer(axis=axis, leg=1, at=(30, 20, 10), turned=True)
    drawing = build_drawing(Box(80, 60, 40), model=[feature], number="X")
    name, leader = next(
        (name, drawing.get_annotation(name))
        for name in drawing.annotations()
        if name.startswith("m_chamfer")
    )
    assert drawing.view_of(name) == view
    assert leader.tip[:2] == pytest.approx(drawing.at(view, *feature.frame.origin)[:2])


@pytest.mark.parametrize(
    ("axis", "view", "surface_site"),
    [
        ("x", "front", (0.0, 0.0, 5.0)),
        ("y", "side", (0.0, 0.0, 5.0)),
        ("z", "front", (5.0, 0.0, 0.0)),
    ],
)
def test_cylindrical_notes_have_xyz_profile_parity(axis, view, surface_site):
    feature = boss(diameter=10, height=20, at=(0, 0, 0), axis=axis)
    target = note("KNURL 0.8 STRAIGHT", feature, Box(80, 60, 40))
    assert target.view == view
    assert target.frame.origin == surface_site
    assert target.frame.origin != feature.frame.origin


def test_unbounded_cylindrical_note_offsets_from_its_declared_axis_site():
    feature = boss(diameter=10, at=(30, 20, 10), axis="z")
    target = note("POLISH", feature, Box(80, 60, 40))
    assert target.view == "front"
    assert target.frame.origin == (35.0, 20.0, 10.0)


def test_turned_edge_surface_note_keeps_its_physical_site_and_validates_overrides():
    feature = chamfer(axis="z", leg=1, at=(30, 20, 10), turned=True)
    target = note("BREAK EDGE", feature, Box(80, 60, 40))
    assert target.view == "front"
    assert target.frame.origin == feature.frame.origin

    with pytest.raises(ValueError, match="view must be"):
        note("BREAK EDGE", feature, view="isometric")
    with pytest.raises(ValueError, match="side must be"):
        note("BREAK EDGE", feature, side="corner")


@pytest.mark.parametrize(
    ("axis", "view", "floor_site"),
    [
        ("x", "front", (0.0, 0.0, 8.0)),
        ("y", "side", (0.0, 0.0, 8.0)),
        ("z", "front", (8.0, 0.0, 0.0)),
    ],
)
def test_groove_notes_land_on_the_floor_with_xyz_profile_parity(axis, view, floor_site):
    feature = groove(axis=axis, width=4, diameter=16, at=(0, 0, 0))
    target = note("POLISH GROOVE", feature, Box(80, 60, 40))
    assert target.view == view
    assert target.frame.origin == floor_site
    assert target.frame.origin != feature.frame.origin


def test_sheet_knurl_leader_lands_on_the_cylindrical_surface():
    shaft = Cylinder(5, 20)
    sheet = Sheet(shaft).auto_dimensions()
    handle = sheet.diameter(shaft)
    handle.knurl("0.8")
    target = next(feature for feature in sheet.features if feature.kind == "note")
    source = target.origin
    drawing = sheet.build()
    leader = drawing.get_annotation("m_gdt0")

    assert target.view == "front"
    assert target.frame.origin != source.frame.origin
    assert drawing.view_of("m_gdt0") == "front"
    assert leader.tip[:2] == pytest.approx(drawing.at("front", *target.frame.origin)[:2])
    codes = {issue.code for issue in drawing.lint()}
    assert "gdt_dropped" not in codes
    assert "feature_leader_crossing" not in codes
