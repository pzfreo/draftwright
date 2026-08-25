"""#1262 — automatic turned drawings omit the repeated longitudinal projection."""

from pathlib import Path

from build123d import Cylinder, Pos, Rot

from draftwright import build_drawing
from draftwright.builder import _automatic_turned_principals


def _x_shaft():
    return Rot(0, 90, 0) * (Cylinder(10, 40) + Pos(0, 0, 40) * Cylinder(6, 30))


def test_the_candidate_follows_the_turning_axis():
    class Analysis:
        prof = None
        is_rotational = True

        def __init__(self, axis):
            self.od_axis = axis

    assert _automatic_turned_principals(Analysis("x")) == ("front", "side")
    assert _automatic_turned_principals(Analysis("y")) == ("front", "side")
    assert _automatic_turned_principals(Analysis("z")) == ("front", "plan")


def test_plain_turned_parts_get_profile_end_and_iso_instead_of_a_repeated_view():
    drawing = build_drawing(_x_shaft(), title="SHAFT", number="1")

    assert tuple(drawing.views) == ("front", "side", "iso")
    assert drawing.view_decision == {
        "policy": "automatic",
        "status": "reduced",
        "chosen": ("front", "side"),
        "attempts": (
            {
                "views": ("front", "side"),
                "status": "chosen",
                "reason": "redundant_radial_view_removed",
                "blockers": (),
            },
        ),
    }
    assert "centerline_front" in drawing.annotations()
    assert "centerline_plan" not in drawing.annotations()
    assert not [issue for issue in drawing.lint() if issue.code.endswith("_dropped")]


def test_an_explicit_scale_still_selects_the_smallest_complete_view_set():
    drawing = build_drawing(_x_shaft(), title="SHAFT", number="1", scale=1)

    assert tuple(drawing.views) == ("front", "side", "iso")
    assert drawing.scale == 1
    assert drawing.view_decision["policy"] == "automatic"
    assert drawing.view_decision["status"] == "reduced"
    assert drawing.view_decision["chosen"] == ("front", "side")
    assert drawing.view_decision["attempts"][0]["status"] == "chosen"


def test_a_radial_feature_vetoes_reduction_before_any_annotation_can_disappear():
    shaft = Rot(0, 90, 0) * Cylinder(12, 80)
    cross_hole = Pos(0, 0, -15) * Cylinder(3, 30)
    drawing = build_drawing(shaft - cross_hole, title="CROSS DRILLED SHAFT", number="2")

    assert tuple(drawing.views) == ("front", "plan", "side", "iso")
    assert drawing.view_decision["status"] == "retained_for_requirements"
    attempt = drawing.view_decision["attempts"][0]
    assert attempt["status"] == "rejected"
    assert attempt["reason"] == "dimension_requirement_uncovered"
    assert set(attempt["uncovered"]) == {
        "hole_1.bore.diameter",
        "hole_1.bore.depth",
        "hole_1.location",
    }


def test_grm03_callout_keeps_external_text_clearance_from_the_end_view():
    fixture = Path("tests/fixtures/grm03_thumbwheel_drive_screw_ap242_pmi.step")
    drawing = build_drawing(fixture, pmi="off")
    callout = drawing.get_annotation("m_chamfer_x1")
    side_left = drawing.view_bounds("side")[0]

    assert callout.label == "C0.5"
    assert side_left - callout.label_bbox[2] >= drawing.draft.pad_around_text - 1e-6
