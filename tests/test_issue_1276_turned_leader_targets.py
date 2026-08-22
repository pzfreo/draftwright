"""#1276: turned edge treatments and cylindrical notes target profile surfaces."""

import math
import warnings
from pathlib import Path

import pytest
from build123d import Box, Compound, Cylinder, GeomType, Pos, Rotation, import_step
from build123d import chamfer as b3d_chamfer
from build123d import fillet as b3d_fillet

from draftwright import build_drawing
from draftwright.model import boss, chamfer, fillet, finish, groove, note
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


def _chamfered_shaft():
    shaft = Cylinder(10, 40)
    circular_edges = [edge for edge in shaft.edges() if edge.geom_type == GeomType.CIRCLE]
    return b3d_chamfer(circular_edges, 1)


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
    ("part", "kind", "prefix"),
    [
        (Rotation(90, 0, 0) * _chamfered_shaft(), "chamfer", "m_chamfer"),
        (Rotation(90, 0, 0) * _rounded_shaft(), "fillet", "m_fillet"),
    ],
)
def test_y_turned_recognised_sites_rotate_onto_the_visible_side_profile(part, kind, prefix):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        drawing = build_drawing(part, number="Y")

    features = [feature for feature in drawing.model().features if feature.kind == kind]
    name = next(name for name in drawing.annotations() if name.startswith(prefix))
    leader = drawing.get_annotation(name)
    center = part.bounding_box().center()
    axis_site = drawing.at("side", center.X, center.Y, center.Z)[:2]

    assert features and all(feature.turned and feature.axis == "y" for feature in features)
    assert drawing.view_of(name) == "side"
    assert leader.tip[:2] != pytest.approx(axis_site)


def test_detected_parallel_shafts_rotate_about_their_own_axes_not_the_part_bbox():
    shaft = Rotation(90, 0, 0) * _chamfered_shaft()
    part = Compound(children=[Pos(-30, 0, 0) * shaft, Pos(50, 0, 0) * shaft])
    drawing = build_drawing(part, number="Y2")
    features = [feature for feature in drawing.model().features if feature.kind == "chamfer"]
    leader = next(
        drawing.get_annotation(name)
        for name in drawing.annotations()
        if name.startswith("m_chamfer")
    )
    vb = drawing.view_bounds("side")

    assert len(features) == 4
    assert all(
        min(
            abs(((feature.frame.origin[0] - cx) ** 2 + feature.frame.origin[2] ** 2) ** 0.5 - 9.5)
            for cx in (-30.0, 50.0)
        )
        < 1e-6
        for feature in features
    )
    assert vb[0] <= leader.tip[0] <= vb[2]
    assert vb[1] <= leader.tip[1] <= vb[3]
    assert leader.tip[1] != pytest.approx((vb[1] + vb[3]) / 2)


def test_turned_site_ignores_radially_matching_cylinder_at_a_remote_axial_station():
    primary = _chamfered_shaft()
    reference = build_drawing(primary, number="REF")
    feature = next(item for item in reference.model().features if item.kind == "chamfer")
    x, y, _z = feature.frame.origin

    # Its radius makes the remote cylinder an exact radial match for the chamfer site.  The
    # owning shaft is inset by the chamfer and therefore loses if matching ignores axial span.
    remote_axis_y = 40.0
    misleading_radius = math.hypot(x, y - remote_axis_y)
    remote = Pos(0, remote_axis_y, 100) * Cylinder(misleading_radius, 10)
    drawing = build_drawing(Compound(children=[primary, remote]), model=[feature], number="AXIAL")
    name = next(name for name in drawing.annotations() if name.startswith("m_chamfer"))
    leader = drawing.get_annotation(name)

    assert drawing.view_of(name) == "front"
    assert leader.tip[:2] == pytest.approx(drawing.at("front", *feature.frame.origin)[:2])


def test_turned_site_ignores_partial_cylindrical_blend_that_is_not_a_shaft():
    primary = _chamfered_shaft()
    reference = build_drawing(primary, number="REF")
    feature = next(item for item in reference.model().features if item.kind == "chamfer")

    block = Box(4, 4, 2)
    vertical = next(edge for edge in block.edges() if edge.bounding_box().size.Z > 1.9)
    # This quarter-cylinder's local axis is exactly one radius from the feature site and at
    # the same axial station, so an unfiltered cylinder inventory prefers it over the chamfer-
    # inset OD.  It is blend geometry, not a shaft substrate.
    blend = Pos(-8.5, 2, -19.5) * b3d_fillet([vertical], 1)
    drawing = build_drawing(Compound(children=[primary, blend]), model=[feature], number="BLEND")
    name = next(name for name in drawing.annotations() if name.startswith("m_chamfer"))
    leader = drawing.get_annotation(name)

    assert drawing.view_of(name) == "front"
    assert leader.tip[:2] == pytest.approx(drawing.at("front", *feature.frame.origin)[:2])


def test_partial_od_site_is_not_redirected_to_a_remote_complete_shaft():
    half_shaft = Cylinder(10, 40) & (Pos(-10, 0, 0) * Box(20, 10, 40))
    circles = [edge for edge in half_shaft.edges() if edge.geom_type == GeomType.CIRCLE]
    primary = b3d_chamfer(circles, 1)
    reference = build_drawing(primary, number="REF")
    feature = next(item for item in reference.model().features if item.kind == "chamfer")
    remote = Pos(0, 100, 0) * Cylinder(10, 40)

    drawing = build_drawing(
        Compound(children=[primary, remote]), model=[feature], number="PARTIAL"
    )
    name = next(name for name in drawing.annotations() if name.startswith("m_chamfer"))
    leader = drawing.get_annotation(name)

    assert drawing.view_of(name) == "front"
    assert leader.tip[:2] == pytest.approx(drawing.at("front", *feature.frame.origin)[:2])


@pytest.mark.parametrize(
    ("feature", "prefix"),
    [
        (chamfer(axis="z", leg=1, at=(0, 9.5, 20), turned=True), "m_chamfer"),
        (fillet(axis="z", radius=1, at=(0, 9.5, 20), turned=True), "m_fillet"),
    ],
)
def test_declared_turned_edge_leader_rotates_off_the_axis_in_profile(feature, prefix):
    drawing = build_drawing(Cylinder(10, 40), model=[feature], number="Z")
    name = next(name for name in drawing.annotations() if name.startswith(prefix))
    leader = drawing.get_annotation(name)

    assert drawing.view_of(name) == "front"
    assert leader.tip[:2] == pytest.approx(drawing.at("front", 9.5, 0, 20)[:2])
    assert leader.tip[:2] != pytest.approx(drawing.at("front", 0, 0, 20)[:2])


@pytest.mark.parametrize("aspect", [note, finish])
def test_turned_surface_aspect_view_override_recanonicalises_for_that_view(aspect):
    feature = chamfer(axis="z", leg=1, at=(9.5, 0, 20), turned=True)
    target = (
        aspect("BREAK EDGE", feature, Cylinder(10, 40), view="side")
        if aspect is note
        else aspect("3.2", feature, Cylinder(10, 40), view="side")
    )
    drawing = build_drawing(Cylinder(10, 40), model=[feature, target], number="Z")
    leader = drawing.get_annotation("m_gdt0")

    assert target.frame.origin == (9.5, 0, 20)
    assert leader.tip[:2] == pytest.approx(drawing.at("side", 0, 9.5, 20)[:2])
    assert leader.tip[:2] != pytest.approx(drawing.at("side", 0, 0, 20)[:2])


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
        ("x", "front", (0.0, 0.0, -5.0)),
        ("y", "side", (0.0, 0.0, -5.0)),
        ("z", "front", (5.0, 0.0, 0.0)),
    ],
)
def test_cylindrical_notes_have_xyz_profile_parity(axis, view, surface_site):
    feature = boss(diameter=10, height=20, at=(0, 0, 0), axis=axis)
    target = note("KNURL 0.8 STRAIGHT", feature, Box(80, 60, 40))
    assert target.view == view
    assert target.frame.origin == surface_site
    assert target.frame.origin != feature.frame.origin


@pytest.mark.parametrize(
    ("axis", "view", "surface_site", "side"),
    [
        ("z", "side", (0.0, 5.0, 0.0), "below"),
        ("x", "plan", (0.0, 5.0, 0.0), "above"),
    ],
)
def test_cylindrical_view_overrides_choose_a_radial_axis_visible_in_that_view(
    axis, view, surface_site, side
):
    feature = boss(diameter=10, height=20, at=(0, 0, 0), axis=axis)
    target = note("POLISH", feature, Box(80, 60, 40), view=view)
    assert target.view == view
    assert target.side == side
    assert target.frame.origin == surface_site


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
        ("x", "front", (0.0, 0.0, -8.0)),
        ("y", "side", (0.0, 0.0, -8.0)),
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


@pytest.mark.parametrize(
    ("factory", "prefix"),
    [(chamfer, "m_chamfer"), (fillet, "m_fillet")],
)
def test_mixed_turned_and_prismatic_groups_offer_candidates_only_in_their_own_view(
    factory, prefix
):
    kwargs = {"leg": 1} if factory is chamfer else {"radius": 1}
    features = [
        factory(axis="z", at=(-10, -10, 0), turned=False, **kwargs),
        factory(axis="z", at=(10, 0, 5), turned=True, **kwargs),
    ]
    drawing = build_drawing(Box(40, 40, 20), model=features, number="MIX")
    name = next(name for name in drawing.annotations() if name.startswith(prefix))
    leader = drawing.get_annotation(name)

    assert drawing.view_of(name) == "front"
    assert leader.tip[:2] == pytest.approx(drawing.at("front", *features[1].frame.origin)[:2])
