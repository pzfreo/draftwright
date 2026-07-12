"""Tests for recognise_turned_steps — axial step recognition for turned parts (ADR 0007).

Geometry-level: build stepped shafts with build123d and assert the recognised
step lengths, ignoring the OCC face-iteration order. Fixtures lie along X (the
orientation that is not flagged Z-rotational), mirroring _x_stepped_shaft.
"""

import pytest
from build123d import Box, Cylinder, GeomType, Pos, Rotation

from draftwright.recognition import TurnedProfile, recognise_turned_steps


def _shaft_x(*sections):
    """A shaft along X from a list of (diameter, length) sections, stacked +Z
    then rotated so the turning axis is X."""
    z = 0.0
    solid = None
    for dia, length in sections:
        seg = Pos(0, 0, z + length / 2) * Cylinder(dia / 2, length)
        solid = seg if solid is None else solid + seg
        z += length
    return Rotation(0, 90, 0) * solid


def _lengths(steps):
    return sorted(round(s.length, 2) for s in steps)


class TestFindTurnedSteps:
    def test_two_step_shaft(self):
        steps = recognise_turned_steps(_shaft_x((30, 40), (16, 30)))
        assert steps
        assert _lengths(steps) == [30.0, 40.0]

    def test_each_step_is_a_self_contained_record_carrying_its_axis(self):
        # The shape fix (#568): a TurnedStep carries its turning axis, so it is
        # interpretable on its own — no TurnedProfile wrapper needed for that.
        steps = recognise_turned_steps(_shaft_x((30, 40), (16, 30)))
        assert steps
        assert all(s.axis == "x" for s in steps)

    def test_three_step_shaft(self):
        steps = recognise_turned_steps(_shaft_x((20, 10), (14, 10), (8, 10)))
        assert steps
        assert _lengths(steps) == [10.0, 10.0, 10.0]

    def test_steps_tile_the_axis_and_sum_to_overall(self):
        steps = recognise_turned_steps(_shaft_x((30, 40), (16, 30)))
        # contiguous: each step's hi is the next step's lo
        for a, b in zip(steps, steps[1:]):
            assert a.hi == pytest.approx(b.lo)
        assert sum(s.length for s in steps) == pytest.approx(70.0)

    def test_axial_bore_is_ignored(self):
        # A through-bore down the centre must not add a step or shift shoulders.
        shaft = _shaft_x((30, 40), (16, 30)) - Rotation(0, 90, 0) * Cylinder(4, 200)
        steps = recognise_turned_steps(shaft)
        assert steps
        assert _lengths(steps) == [30.0, 40.0]

    def test_chamfered_shoulders_keep_true_lengths(self):
        # Chamfer the shoulder edges; the step lengths must stay shoulder-to-
        # shoulder (the chamfer shortens the cylindrical face, not the step).
        shaft = _shaft_x((30, 40), (16, 30))
        edges = [e for e in shaft.edges() if e.geom_type == GeomType.CIRCLE]
        try:
            shaft = shaft.chamfer(0.8, None, edges)
        except Exception:
            pytest.skip("chamfer not constructible on this fixture")
        steps = recognise_turned_steps(shaft)
        assert steps
        assert _lengths(steps) == [30.0, 40.0]

    def test_diameter_per_step(self):
        steps = recognise_turned_steps(_shaft_x((30, 40), (16, 30)))
        by_len = {round(s.length): round(s.diameter) for s in steps}
        assert by_len == {40: 30, 30: 16}

    def test_shoulders_aggregate(self):
        # `shoulders` is a TurnedProfile-aggregate concern, built from the steps.
        prof = TurnedProfile.from_steps(recognise_turned_steps(_shaft_x((30, 40), (16, 30))))
        assert prof is not None and prof.axis == "x"
        sh = prof.shoulders
        assert len(sh) == 3
        assert list(sh) == sorted(sh)  # sorted shoulder positions
        # consecutive shoulders delimit the steps
        diffs = sorted(round(b - a, 2) for a, b in zip(sh, sh[1:]))
        assert diffs == [30.0, 40.0]

    def test_plain_cylinder_is_empty(self):
        assert recognise_turned_steps(Cylinder(15, 40)) == []
        assert TurnedProfile.from_steps(recognise_turned_steps(Cylinder(15, 40))) is None

    def test_prismatic_box_is_empty(self):
        assert recognise_turned_steps(Box(40, 40, 10)) == []
