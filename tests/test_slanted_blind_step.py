"""Fast regressions for the slanted/blind stepped-profile case (#897/#898).

The fixture is synthetic: it preserves the dimension-planning challenge without
vendoring the uploaded customer STEP file.
"""

from __future__ import annotations

import pytest
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


@pytest.mark.timeout(120)
def test_slanted_profile_and_blind_interruptions_are_recognised(slanted_blind_step):
    dwg = build_drawing(slanted_blind_step, detail_view=True)
    pockets = [f for f in dwg.model().features if f.kind == "pocket"]
    step = next(f for f in dwg.model().features if f.kind == "step_level")

    assert len(pockets) == 2
    assert all(p.edge_anchored for p in pockets)
    assert {(p.width, p.length, p.depth) for p in pockets} == {(8.0, 12.0, 5.0)}
    assert step.levels == (14.0, 19.0)
    assert step.shoulders == (("x", 24.0), ("x", 32.0), ("x", 40.0))


@pytest.mark.timeout(120)
def test_slanted_blind_step_gets_reconstructable_dimension_plan(slanted_blind_step):
    dwg = build_drawing(slanted_blind_step, detail_view=True)
    names = set(dwg.annotations())

    assert "m_env_width" in names
    assert {"dim_shoulder_x0", "dim_shoulder_x1", "dim_shoulder_x2"} <= names
    assert {"m_pocket_xy0", "m_pocket_xy1"} <= names
    assert "detail_a" in dwg.views

    detail_labels = {
        str(getattr(ann, "label", ""))
        for name, ann in dwg.annotations_in_view("detail_a")
        if name.startswith("dim_detail_a_step")
    }
    assert {"14", "19"} <= detail_labels
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


@pytest.mark.timeout(120)
def test_short_first_step_uses_external_dimension_instead_of_disappearing():
    with BuildPart() as part:
        with BuildSketch(Plane.XZ):
            Polygon((0, 0), (50, 0), (50, 1), (25, 1), (25, 20), (0, 20))
        extrude(amount=30)

    dwg = build_drawing(part.part)
    assert "dim_step_0" in dwg.annotations()
    assert getattr(dwg.get_annotation("dim_step_0"), "label", None) == "1"


@pytest.mark.timeout(120)
def test_crowded_step_warning_guides_to_detail_and_clears_after_recovery(
    slanted_blind_step,
):
    plain = build_drawing(slanted_blind_step)
    dropped = [i for i in plain.lint() if i.code == "step_dim_dropped"]
    assert len(dropped) == 1
    assert "detail_view=True" in dropped[0].suggestion

    detailed = build_drawing(slanted_blind_step, detail_view=True)
    assert not [i for i in detailed.lint() if i.code == "step_dim_dropped"]
