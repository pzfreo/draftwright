"""Section ink describes the actual cut, not parallel retained surfaces (#1530)."""

import pytest
from build123d import Box, Compound, Cylinder, Plane, Pos, Rot, section

from draftwright import Drawing, Sheet
from draftwright.annotations.sections import _section_hatch_edges


def _part():
    # The rear support faces toward the camera but is not cut at Y=0. The
    # detached bar gives a second cut region; the transverse bore gives a void.
    base = Box(20, 16, 8) - Pos(-5, 0, 0) * Rot(90, 0, 0) * Cylinder(2, 16)
    supported = base + Pos(5, 5, 7) * Box(4, 4, 6)
    return Compound(children=[supported, Pos(18, 0, 0) * Box(4, 16, 4)])


def _sheet(part, projection, cut_y):
    sheet = Sheet(part, page="A2", scale=2, projection=projection)
    sheet.authored_dimensions().authored_views()
    envelope = sheet.envelope(part)
    for parameter in envelope.dimension_ids():
        sheet.dimension(envelope, parameter)
    for name in ("front", "plan", "side"):
        sheet.view(name)
    sheet.section_view("A", at=cut_y)
    return sheet


@pytest.mark.parametrize("projection", ["first", "third"])
@pytest.mark.parametrize("translation", [(0, 0, 0), (12, 37, 9)])
def test_section_regions_camera_and_indicators_agree(monkeypatch, projection, translation):
    part = Compound(children=[Pos(*translation) * solid for solid in _part().solids()])
    cut_y = translation[1]
    plane = Plane(origin=(0, cut_y, 0), x_dir=(1, 0, 0), z_dir=(0, -1, 0))
    expected = section(part, section_by=plane)
    captured_faces = []
    captured_views = []
    add_view = Drawing._add_view

    def capture_hatch(face, sx, sz, spacing):
        captured_faces.append(face)
        return _section_hatch_edges(face, sx, sz, spacing)

    def capture_view(drawing, name, shape, camera, up, position, **kwargs):
        if name == "section_aa":
            captured_views.append((shape, camera, drawing.look_at))
        return add_view(drawing, name, shape, camera, up, position, **kwargs)

    monkeypatch.setattr("draftwright.annotations.sections._section_hatch_edges", capture_hatch)
    monkeypatch.setattr(Drawing, "_add_view", capture_view)
    drawing = _sheet(part, projection, cut_y).build()

    assert drawing.section_decision["status"] == "placed"
    assert len(expected.faces()) == 2 and any(f.inner_wires() for f in expected.faces())
    assert len(captured_faces) == 2
    hatched = Compound(children=captured_faces)
    # Both spatial differences must be empty. Equal areas alone could hide a
    # missing region replaced by a wrongly hatched region of the same size.
    assert (hatched - expected).area == pytest.approx(0, abs=1e-7)
    assert (expected - hatched).area == pytest.approx(0, abs=1e-7)
    retained, camera, target = captured_views[-1]
    assert retained.bounding_box().min.Y == pytest.approx(cut_y)
    assert retained.bounding_box().max.Z == pytest.approx(translation[2] + 10)
    assert camera[0] == target[0] and camera[2] == target[2]
    assert camera[1] < target[1]  # viewing toward +Y, into the retained half
    line_y = drawing.get_annotation("section_line").bounding_box().center().Y
    for side in ("left", "right"):
        arrow = drawing.get_annotation(f"section_arrow_{side}")
        wing = drawing.get_annotation(f"section_wing_{side}")
        assert wing.bounding_box().min.Y == pytest.approx(line_y)
        assert arrow.bounding_box().min.Y > line_y
        # The arrow's unique leading vertex points in projected +Y too.
        vertices = [(v.X, v.Y) for v in arrow.vertices()]
        tip_y = max(y for _, y in vertices)
        assert sum(abs(y - tip_y) < 1e-7 for _, y in vertices) == 1
        assert wing.bounding_box().max.Y == pytest.approx(arrow.bounding_box().min.Y)
    assert "section_hatch" in drawing.annotations()
    assert not any(i.code == "section_dropped" for i in drawing.lint())


@pytest.mark.parametrize("cut_y", [-9, -8, 8, 9])
def test_a_cut_outside_the_open_extent_is_refused(cut_y):
    with pytest.raises(ValueError, match="not strictly inside"):
        _sheet(Box(20, 16, 8), "third", cut_y).build()


@pytest.mark.parametrize("cut_y", [-4, 0, 4])
def test_an_internal_gap_or_tangent_cut_has_an_honest_outcome(cut_y):
    part = Compound(children=[Pos(0, y, 0) * Box(20, 4, 8) for y in (-6, 6)])
    drawing = _sheet(part, "third", cut_y).build()
    assert drawing.section_decision["status"] == "skipped"
    assert drawing.section_decision["reason"] == "cut_no_intersection"
    assert "section_aa" not in drawing.views
    assert not any(name.startswith("section_") for name in drawing.annotations())
    assert any(i.code == "section_dropped" for i in drawing.lint())


@pytest.mark.parametrize("case", ["attached", "detached", "adjacent", "notch"])
def test_preexisting_coplanar_boundaries_are_not_new_cut_faces(monkeypatch, case):
    base = Box(20, 16, 8)
    boss = Pos(5, 4, 7) * Box(4, 8, 6)
    if case == "attached":
        part = base + boss
    elif case == "detached":
        part = Compound(children=[base, Pos(30, 0, 0) * boss])
    elif case == "adjacent":
        part = base + Pos(10, 4, 0) * Box(4, 8, 8)
    else:
        part = base - Pos(5, -4, 2) * Box(10, 8, 4)
        # The pocket back wall was already exposed. The independent expected
        # cut uses a through notch so that wall cannot enter its plane section.
        base = base - Pos(5, 0, 2) * Box(10, 16, 4)
    expected = section(base, section_by=Plane.XZ)
    faces = []

    def capture(face, *args):
        faces.append(face)
        return _section_hatch_edges(face, *args)

    monkeypatch.setattr("draftwright.annotations.sections._section_hatch_edges", capture)
    drawing = _sheet(part, "third", 0).build()
    assert drawing.section_decision["status"] == "placed"
    assert faces
    actual = Compound(children=faces)
    assert (actual - expected).area == pytest.approx(0, abs=1e-7)
    assert (expected - actual).area == pytest.approx(0, abs=1e-7)
