"""Tests for find_turned_steps — axial step recognition for turned parts (ADR 0007).

Geometry-level: build stepped shafts with build123d and assert the recognised
step lengths, ignoring the OCC face-iteration order. Fixtures lie along X (the
orientation that is not flagged Z-rotational), mirroring _x_stepped_shaft.
"""

import pytest
from build123d import Box, Cylinder, GeomType, Pos, Rotation

from draftwright.recognition import TurnedProfile, find_turned_steps


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


def _lengths(profile: TurnedProfile):
    return sorted(round(s.length, 2) for s in profile.steps)


class TestFindTurnedSteps:
    def test_two_step_shaft(self):
        prof = find_turned_steps(_shaft_x((30, 40), (16, 30)))
        assert prof is not None
        assert prof.axis == "x"
        assert _lengths(prof) == [30.0, 40.0]

    def test_three_step_shaft(self):
        prof = find_turned_steps(_shaft_x((20, 10), (14, 10), (8, 10)))
        assert prof is not None
        assert _lengths(prof) == [10.0, 10.0, 10.0]

    def test_steps_tile_the_axis_and_sum_to_overall(self):
        prof = find_turned_steps(_shaft_x((30, 40), (16, 30)))
        # contiguous: each step's hi is the next step's lo
        for a, b in zip(prof.steps, prof.steps[1:]):
            assert a.hi == pytest.approx(b.lo)
        assert sum(s.length for s in prof.steps) == pytest.approx(70.0)

    def test_axial_bore_is_ignored(self):
        # A through-bore down the centre must not add a step or shift shoulders.
        shaft = _shaft_x((30, 40), (16, 30)) - Rotation(0, 90, 0) * Cylinder(4, 200)
        prof = find_turned_steps(shaft)
        assert prof is not None
        assert _lengths(prof) == [30.0, 40.0]

    def test_chamfered_shoulders_keep_true_lengths(self):
        # Chamfer the shoulder edges; the step lengths must stay shoulder-to-
        # shoulder (the chamfer shortens the cylindrical face, not the step).
        shaft = _shaft_x((30, 40), (16, 30))
        edges = [e for e in shaft.edges() if e.geom_type == GeomType.CIRCLE]
        try:
            shaft = shaft.chamfer(0.8, None, edges)
        except Exception:
            pytest.skip("chamfer not constructible on this fixture")
        prof = find_turned_steps(shaft)
        assert prof is not None
        assert _lengths(prof) == [30.0, 40.0]

    def test_diameter_per_step(self):
        prof = find_turned_steps(_shaft_x((30, 40), (16, 30)))
        by_len = {round(s.length): round(s.diameter) for s in prof.steps}
        assert by_len == {40: 30, 30: 16}

    def test_shoulders_property(self):
        prof = find_turned_steps(_shaft_x((30, 40), (16, 30)))
        sh = prof.shoulders
        assert len(sh) == 3
        assert list(sh) == sorted(sh)  # sorted shoulder positions
        # consecutive shoulders delimit the steps
        diffs = sorted(round(b - a, 2) for a, b in zip(sh, sh[1:]))
        assert diffs == [30.0, 40.0]

    def test_plain_cylinder_is_none(self):
        assert find_turned_steps(Cylinder(15, 40)) is None

    def test_prismatic_box_is_none(self):
        assert find_turned_steps(Box(40, 40, 10)) is None
