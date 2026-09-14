"""Solid analysis and sizing-convergence regressions."""

import logging

import pytest
from quiddity import recognise_face_levels

from draftwright.analysis import _converge_step_sizing
from draftwright.compose import StripDepths
from draftwright.drawing import analyse_cylinders


@pytest.mark.timeout(60)
def test_analyse_cylinders_box_has_no_z_cylinders():
    from build123d import Box

    box = Box(30, 20, 10)
    z_cyls, cross_cyls = analyse_cylinders(box)
    assert z_cyls == []
    assert cross_cyls == []


@pytest.mark.timeout(60)
def test_analyse_cylinders_finds_cylinder():
    from build123d import Cylinder

    cyl = Cylinder(5, 20)  # radius=5, height=20 → diameter=10
    z_cyls, cross_cyls = analyse_cylinders(cyl)
    assert len(z_cyls) >= 1
    diameters = [c["diameter"] for c in z_cyls]
    assert any(abs(d - 10.0) < 0.5 for d in diameters)


@pytest.mark.timeout(60)
def test_analyse_face_levels_box():
    from build123d import Box

    box = Box(30, 20, 10)
    levels = recognise_face_levels(box)
    # Box centred at origin has Z faces at -5 and +5
    assert any(abs(fl.z - (-5.0)) < 0.1 for fl in levels)
    assert any(abs(fl.z - 5.0) < 0.1 for fl in levels)
    assert all(fl.x_span == pytest.approx((-15.0, 15.0)) for fl in levels)
    assert all(fl.y_span == pytest.approx((-10.0, 10.0)) for fl in levels)


@pytest.mark.timeout(60)
def test_analyse_face_levels_returns_sorted():
    from build123d import Box

    box = Box(30, 20, 10)
    levels = recognise_face_levels(box)
    assert levels == sorted(levels)


@pytest.mark.timeout(60)
def test_analyse_face_levels_area_filter_drops_tiny_faces():
    # A sub-feature horizontal face (e.g. a fragment of engraved text) is far
    # smaller than the plan footprint and must not be counted as a real step.
    # staircase.step review: a 0.57 mm² digit face was dimensioned as z=6.4.
    from build123d import Box, Pos

    # 30×20 footprint (600 mm²); a 1×1 pip on top (1 mm² top face at z=7).
    part = Box(30, 20, 10) + Pos(0, 0, 6) * Box(1, 1, 2)

    # Without the filter the tiny face shows up as a phantom level.
    unfiltered = recognise_face_levels(part)
    assert any(abs(fl.z - 7.0) < 0.1 for fl in unfiltered)

    # With a 1%-of-footprint threshold (6 mm²) the 1 mm² face is dropped,
    # leaving only the real slab faces.
    filtered = recognise_face_levels(part, min_area_frac=0.01)
    assert not any(abs(fl.z - 7.0) < 0.1 for fl in filtered)
    assert any(abs(fl.z - 5.0) < 0.1 for fl in filtered)
    assert any(abs(fl.z - (-5.0)) < 0.1 for fl in filtered)


class TestStepSizingConvergence:
    def test_step_sizing_converges_past_the_old_three_pass_limit(self):
        measure_calls = []

        def measure(n_steps):
            measure_calls.append(n_steps)
            return StripDepths(right=float(n_steps), left=0.0)

        def pick(n_steps, strips):
            assert strips.right == pytest.approx(n_steps)
            return float(n_steps), 297.0, 210.0, 120.0

        def legible_count(scale):
            return {7.0: 5, 5.0: 4, 4.0: 2, 2.0: 2}[scale]

        pick_result, strips, n_steps = _converge_step_sizing(7, measure, pick, legible_count)

        assert pick_result == (2.0, 297.0, 210.0, 120.0)
        assert strips.right == pytest.approx(2.0)
        assert n_steps == 2
        assert measure_calls == [7, 5, 4, 2]

    def test_step_sizing_cycle_uses_the_larger_reservation(self, caplog):
        measure_calls = []

        def measure(n_steps):
            measure_calls.append(n_steps)
            return StripDepths(right=float(n_steps), left=0.0)

        def pick(n_steps, strips):
            assert strips.right == pytest.approx(n_steps)
            return float(n_steps), 297.0, 210.0, 120.0

        def legible_count(scale):
            return {4.0: 2, 2.0: 4}[scale]

        with caplog.at_level(logging.WARNING, logger="draftwright.analysis"):
            pick_result, strips, n_steps = _converge_step_sizing(4, measure, pick, legible_count)

        assert pick_result == (4.0, 297.0, 210.0, 120.0)
        assert strips.right == pytest.approx(4.0)
        assert n_steps == 4
        assert measure_calls == [4, 2, 4]
        assert "did not converge" in caplog.text
