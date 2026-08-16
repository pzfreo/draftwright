"""Fast regressions for the slanted/blind stepped-profile case (#897/#898).

The fixture is synthetic: it preserves the dimension-planning challenge without
vendoring the uploaded customer STEP file.
"""

from __future__ import annotations

from collections import Counter
from types import SimpleNamespace

import pytest
from b123d_recognisers import levels as levels_module

# These privates moved from `slots` to `_recess_core` in b123d-recognisers 0.2.2's
# seam decomposition. Reaching across a package boundary into private names is
# fragile by construction — ADR 0013's contract is the PUBLIC uniform recogniser
# surface — but this test drives a notch-recognition corner case that has no public
# entry point. If it breaks again, the fix is to move the case upstream, not to chase
# the private a third time.
from b123d_recognisers._recess_core import _Face, _recognise_corner_notches
from b123d_recognisers.levels import project_step_shoulders, recognise_risers
from build123d import (
    Align,
    Box,
    BuildPart,
    BuildSketch,
    Plane,
    Polygon,
    Pos,
    extrude,
)

from draftwright import build_drawing
from draftwright.model.declare import envelope


@pytest.fixture(scope="module")
def slanted_blind_step():
    align_min = (Align.MIN, Align.MIN, Align.MIN)
    with BuildPart() as part:
        with BuildSketch(Plane.XZ):
            Polygon(
                (0, 0),
                (50, 0),
                (50, 14),
                (40, 14),
                (40, 19),
                (32, 19),
                (24, 25),
                (0, 25),
            )
        extrude(amount=50)
    solid = part.part
    bb = solid.bounding_box()
    low = Pos(bb.min.X, bb.min.Y, bb.min.Z) * Box(8, 12, 5, align=align_min)
    high = Pos(bb.min.X, bb.max.Y - 12, bb.max.Z - 5) * Box(8, 12, 5, align=align_min)
    return solid - low - high


@pytest.fixture(scope="module")
def bounded_slanted_recess():
    """A partial-width ramp whose ends are defining profile transitions."""
    align_min = (Align.MIN, Align.MIN, Align.MIN)
    base = Box(50, 50, 25, align=align_min)
    with BuildPart() as cut:
        with BuildSketch(Plane.XZ):
            Polygon((0, 15), (5, 15), (15, 25), (0, 25))
        extrude(amount=12)
    return base - Pos(0, 12, 0) * cut.part


@pytest.mark.timeout(120)
def test_slanted_profile_and_blind_interruptions_are_recognised(slanted_blind_step):
    dwg = build_drawing(slanted_blind_step, detail_view=True)
    pockets = [f for f in dwg.model().features if f.kind == "pocket"]
    steps = [f for f in dwg.model().features if f.kind == "step_level"]

    assert len(pockets) == 2
    assert all(p.edge_anchored for p in pockets)
    assert {(p.width, p.length, p.depth) for p in pockets} == {(8.0, 12.0, 5.0)}
    # Exact internal coordinates are kernel-normalised; the following test
    # verifies the stable public dimensions and labels.
    assert steps


@pytest.mark.timeout(120)
def test_slanted_blind_step_gets_reconstructable_dimension_plan(slanted_blind_step):
    dwg = build_drawing(slanted_blind_step, detail_view=True)
    names = set(dwg.annotations())

    assert "m_env_width" in names
    assert {"dim_shoulder_x0", "dim_shoulder_x1", "dim_shoulder_x2"} <= names
    assert {"m_pocket_xy0", "m_pocket_xy1"} <= names
    assert "detail_a" in dwg.views

    detail_labels = Counter(
        str(getattr(ann, "label", ""))
        for name, ann in dwg.annotations_in_view("detail_a")
        if name.startswith("dim_detail_a_step")
    )
    main_labels = Counter(
        str(getattr(ann, "label", ""))
        for name, ann in dwg.annotations_in_view("front")
        if name.startswith("dim_step")
    )
    assert main_labels["14"] == 1
    assert detail_labels["19"] == 1
    assert not main_labels & detail_labels
    assert getattr(dwg.get_annotation("dim_height"), "label", None) == "25"
    assert not [i for i in dwg.lint() if i.severity in ("warning", "error")]


@pytest.mark.timeout(120)
def test_lint_flags_source_geometry_omitted_from_declared_ir(slanted_blind_step):
    # Simulate the pre-#897 sparse inventory: the B-rep still has both notches and
    # every profile transition, but the supplied model declares only the envelope.
    dwg = build_drawing(slanted_blind_step, model=[envelope(slanted_blind_step)])
    issues = [i for i in dwg.lint() if i.code == "unrecognised_defining_geometry"]

    assert len(issues) == 1
    assert "bounded blind recess" in issues[0].message
    assert "profile transition" in issues[0].message


def test_bounded_slanted_recess_contributes_both_ramp_ends(bounded_slanted_recess):
    dwg = build_drawing(bounded_slanted_recess)
    shoulders = {
        shoulder
        for step in dwg.model().features
        if step.kind == "step_level"
        for shoulder in step.shoulders
    }

    assert len({position for axis, position in shoulders if axis == "x"}) >= 2
    assert ("y", 12.0) in shoulders


def test_small_edge_break_is_not_promoted_to_structural_ramp():
    align_min = (Align.MIN, Align.MIN, Align.MIN)
    base = Box(50, 50, 25, align=align_min)
    with BuildPart() as cut:
        with BuildSketch(Plane.XZ):
            Polygon((0, 20), (1, 20), (2, 25), (0, 25))
        extrude(amount=2)
    dwg = build_drawing(base - Pos(0, 2, 0) * cut.part)
    shoulders = {
        shoulder
        for step in dwg.model().features
        if step.kind == "step_level"
        for shoulder in step.shoulders
    }

    assert ("x", 2.0) not in shoulders


def test_oblique_degenerate_and_small_bounded_faces_are_rejected(monkeypatch):
    class Face:
        wrapped = object()

        def __init__(self, normal, bb):
            self._normal = SimpleNamespace(
                X=normal[0],
                Y=normal[1],
                Z=normal[2],
            )
            self._bb = bb

        def normal_at(self):
            return self._normal

        def bounding_box(self):
            return self._bb

    class Part:
        def __init__(self, face):
            self._face = face

        def bounding_box(self):
            return _box(0, 50, 0, 50, 0, 25)

        def faces(self):
            return [self._face]

    class PlaneSurface:
        @staticmethod
        def GetType():
            return levels_module.GeomAbs_Plane

    monkeypatch.setattr(
        levels_module,
        "BRepAdaptor_Surface",
        lambda wrapped: PlaneSurface(),
    )

    shallow = Face((1.0, 0.0, 0.02), _box(2, 3, 0, 10, 24.75, 25))
    small = Face((1.0, 0.0, 0.5), _box(2, 3, 0, 2, 20, 25))

    assert project_step_shoulders(recognise_risers(Part(shallow)), levels=[24.75]) == []
    assert project_step_shoulders(recognise_risers(Part(small)), levels=[20]) == []


def test_completeness_lint_can_report_transitions_without_blind_recesses(
    bounded_slanted_recess,
):
    dwg = build_drawing(
        bounded_slanted_recess,
        model=[envelope(bounded_slanted_recess)],
    )
    issues = [i for i in dwg.lint() if i.code == "unrecognised_defining_geometry"]

    assert len(issues) == 1
    assert "profile transition" in issues[0].message
    assert "blind recess" not in issues[0].message


def test_corner_notch_preserves_long_axis_when_x_span_is_larger():
    align_min = (Align.MIN, Align.MIN, Align.MIN)
    solid = Box(50, 50, 25, align=align_min) - Pos(0, 0, 20) * Box(12, 8, 5, align=align_min)
    dwg = build_drawing(solid)
    pocket = next(f for f in dwg.model().features if f.kind == "pocket")

    assert pocket.edge_anchored
    assert (pocket.width_axis, pocket.long_axis) == ("y", "x")
    assert (pocket.width, pocket.length, pocket.depth) == (8.0, 12.0, 5.0)


def _box(x0, x1, y0, y1, z0, z1):
    def point(x, y, z):
        return SimpleNamespace(X=x, Y=y, Z=z)

    return SimpleNamespace(min=point(x0, y0, z0), max=point(x1, y1, z1))


def test_corner_notch_rejects_degenerate_or_incomplete_face_sets():
    part_bb = _box(0, 50, 0, 50, 0, 25)
    degenerate_floor = _Face((0, 0, 1), "z", _box(0, 0, 0, 8, 20, 20), True)
    floor = _Face((0, 0, 1), "z", _box(0, 12, 0, 8, 20, 20), True)
    shallow_x_wall = _Face((-1, 0, 0), "x", _box(12, 12, 0, 8, 10, 15), True)
    shallow_y_wall = _Face((0, -1, 0), "y", _box(0, 12, 8, 8, 10, 15), True)

    assert _recognise_corner_notches([degenerate_floor], part_bb) == []
    assert _recognise_corner_notches([floor], part_bb) == []
    assert (
        _recognise_corner_notches(
            [floor, shallow_x_wall, shallow_y_wall],
            part_bb,
        )
        == []
    )


@pytest.mark.timeout(120)
def test_short_first_step_uses_external_dimension_instead_of_disappearing():
    with BuildPart() as part:
        with BuildSketch(Plane.XZ):
            Polygon((0, 0), (50, 0), (50, 1), (25, 1), (25, 20), (0, 20))
        extrude(amount=30)

    dwg = build_drawing(part.part)
    assert "dim_step_0" in dwg.annotations()
    assert getattr(dwg.get_annotation("dim_step_0"), "label", None) == "1"


def test_unplaceable_short_step_records_left_strip_failure(monkeypatch):
    from draftwright.annotations import from_model

    original = from_model.register_corridor

    def drop_left(ctx, key, strip, view, axis, tier, candidate):
        if key == ("front", "left"):
            candidate.on_drop(candidate.name)
            return
        original(ctx, key, strip, view, axis, tier, candidate)

    monkeypatch.setattr(from_model, "register_corridor", drop_left)
    with BuildPart() as part:
        with BuildSketch(Plane.XZ):
            Polygon((0, 0), (50, 0), (50, 1), (25, 1), (25, 20), (0, 20))
        extrude(amount=30)

    dwg = build_drawing(part.part)
    issue = next(i for i in dwg.lint() if i.code == "placement_unsatisfiable")

    assert "short step-height dimension dropped" in issue.message


@pytest.mark.timeout(120)
def test_crowded_step_warning_guides_to_detail_and_clears_after_recovery(
    slanted_blind_step,
):
    plain = build_drawing(slanted_blind_step, detail_view=False)
    dropped = [i for i in plain.lint() if i.code == "step_dim_dropped"]
    assert len(dropped) == 1
    assert "detail_view=True" in dropped[0].suggestion

    detailed = build_drawing(slanted_blind_step, detail_view=True)
    assert not [i for i in detailed.lint() if i.code == "step_dim_dropped"]
