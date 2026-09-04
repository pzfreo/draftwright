"""#1382: all recognisers 0.4.6 step families stay visible and audited at runtime."""

from __future__ import annotations

from b123d_recognisers import build_raw_recognition_result
from build123d import Box, Compound, Cylinder, Plane, Polygon, Pos, Rot, extrude

from draftwright import build_drawing


def _through_step():
    return Box(40, 30, 20) - Pos(15, 10, 0) * Box(20, 20, 30)


def _paired_ramp_step():
    profile = Polygon((0, -8), (0, 8), (-10, 0))
    cutter = Pos(20, 20, 0) * extrude(Plane.XZ * profile, 25)
    return Box(40, 40, 30) - cutter


def _circular_blind_step():
    return Box(40, 30, 20) - Pos(7.5, 15, 10) * Rot(0, 90, 0) * Cylinder(4, 25)


def test_circular_blind_step_is_visible_as_two_audited_requirements() -> None:
    part = _circular_blind_step()
    recognition = build_raw_recognition_result(part)
    assert recognition.circular_blind_steps

    completeness = build_drawing(part).lint_summary()["quality"]["completeness"]

    assert completeness["unscored_recognized_families"] == []
    assert completeness["by_family"]["circular_blind_steps"] == 2
    assert completeness["requirements"] == completeness["placed"] == 2
    assert completeness["audited_score"] == 1.0


def test_absent_046_step_families_do_not_pollute_an_ordinary_part():
    completeness = build_drawing(Box(30, 20, 10)).lint_summary()["quality"]["completeness"]

    assert not {
        "circular_blind_steps",
        "paired_ramp_steps",
        "through_steps",
    } & set(completeness["unscored_recognized_families"])


def test_paired_ramps_left_the_undecided_inventory_with_two_audited_requirements():
    completeness = build_drawing(_paired_ramp_step()).lint_summary()["quality"]["completeness"]

    assert completeness["unscored_recognized_families"] == []
    assert completeness["by_family"]["paired_ramp_steps"] == 2
    assert completeness["requirements"] == completeness["placed"] == 2


def test_newly_audited_through_step_stays_visible_beside_an_existing_family():
    pocket = Box(80, 60, 20) - Pos(20, 0, 5) * Box(20, 10, 12)
    mixed = Compound([Pos(-70, 0, 0) * _through_step(), Pos(70, 0, 0) * pocket])

    completeness = build_drawing(mixed).lint_summary()["quality"]["completeness"]

    assert completeness["unscored_recognized_families"] == []
    assert completeness["requirements"] == 7
    assert completeness["placed"] == 7
    assert completeness["audited_score"] == 1.0
    assert completeness["by_family"]["through_steps"] == 2
