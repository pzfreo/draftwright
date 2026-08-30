"""#1382: undecided recognisers 0.4.6 step families stay visible at runtime."""

from __future__ import annotations

import pytest
from b123d_recognisers import build_recognition_result
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


@pytest.mark.parametrize(
    ("inventory", "part_factory"),
    [
        ("circular_blind_steps", _circular_blind_step),
        ("through_steps", _through_step),
    ],
)
def test_each_046_step_inventory_is_visible_in_runtime_completeness(inventory, part_factory):
    """Static registration is insufficient: a real occurrence must reach public quality data."""

    part = part_factory()
    recognition = build_recognition_result(part)
    assert getattr(recognition, inventory), "fixture stopped exercising its provider family"

    completeness = build_drawing(part).lint_summary()["quality"]["completeness"]

    assert completeness["unscored_recognized_families"] == [inventory]
    assert completeness["requirements"] == 0
    assert completeness["available"] is False
    assert completeness["audited_score"] is None
    assert completeness["unsupported"] == 0
    assert inventory not in completeness["by_family"], (
        "undecided evidence is visible but must not invent a requirement denominator"
    )


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


def test_undecided_evidence_stays_visible_beside_a_perfect_audited_family():
    pocket = Box(80, 60, 20) - Pos(20, 0, 5) * Box(20, 10, 12)
    mixed = Compound([Pos(-70, 0, 0) * _through_step(), Pos(70, 0, 0) * pocket])

    completeness = build_drawing(mixed).lint_summary()["quality"]["completeness"]

    assert completeness["unscored_recognized_families"] == ["through_steps"]
    assert completeness["requirements"] == 5
    assert completeness["placed"] == 5
    assert completeness["audited_score"] == 1.0
    assert completeness["reason"] == (
        "audited_score covers recognized requirements in audited families only; it is "
        "not evidence that the drawing is complete"
    )
