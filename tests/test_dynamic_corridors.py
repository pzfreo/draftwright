"""Dynamic front-to-side corridor behavior."""

import pytest
from build123d import Box

from draftwright import build_drawing


@pytest.fixture(scope="module")
def plain_box_dwg():
    return build_drawing(Box(60, 40, 20))


class TestDynamicCorridors:
    """Phase 3 (#118): SV_X and _fits() use the depth estimator for the FV→SV gap."""

    def test_fits_widens_required_space_for_stepped_part(self):
        # x=5, y=90, z=100 at 1:1 on A3 (420×297, tb=150):
        #   n_steps=0 (gap_fv_sv=20): fits.
        #   n_steps=3 (gap_fv_sv=69.5): auto_fits rejects the conservative
        #     composed row, using the same verdict later used by auto repack (#519).
        from draftwright.compose import _fits

        assert _fits(5.0, 90.0, 100.0, 1.0, 420.0, 297.0, 150.0, n_steps=0)
        assert not _fits(5.0, 90.0, 100.0, 1.0, 420.0, 297.0, 150.0, n_steps=3)

    def test_fits_zero_steps_same_as_default(self):
        # n_steps=0 must produce the same result as the old signature (no kwarg).
        from draftwright.compose import _fits

        page_w, page_h, tb = 297.0, 210.0, 120.0
        scale, x_size, y_size, z_size = 1.0, 20.0, 20.0, 20.0
        assert _fits(x_size, y_size, z_size, scale, page_w, page_h, tb, n_steps=0) == _fits(
            x_size, y_size, z_size, scale, page_w, page_h, tb
        )

    def test_gap_fv_sv_equals_dim_pad_for_flat_part(self, plain_box_dwg):
        # A plain box (no step faces) → sv_left - fv_right == _DIM_PAD.

        from draftwright._core import _DIM_PAD

        a = plain_box_dwg._analysis
        assert len(a.step_zs) == 0
        sv_left = a.SV_X - a.sv_hw
        fv_right = a.FV_X + a.fv_hw
        assert sv_left - fv_right == pytest.approx(_DIM_PAD, abs=0.1)

    def test_choose_scale_picks_larger_page_for_deep_step_corridor(self):
        # With n_steps=0, x=5 y=90 z=100 fits A3 at 1:1 (420 mm wide).
        # With n_steps=3, gap_fv_sv jumps to 69.5 mm — the shared auto_fits
        # verdict rejects A3 and choose_scale returns A2.
        from draftwright.compose import choose_scale

        # Pinned to `columns`: the corridor claim is about the layout whose width the
        # corridor competes for. Under the ADR 2 (was 0018) alternative the reclaimed iso column
        # absorbs the corridor and both land on the same sheet, which is a fact about that
        # arrangement rather than a refutation of this one.
        _, page_w_flat, _, _ = choose_scale(5.0, 90.0, 100.0, n_steps=0, arrangements=("columns",))
        _, page_w_deep, _, _ = choose_scale(5.0, 90.0, 100.0, n_steps=3, arrangements=("columns",))
        assert page_w_deep > page_w_flat, (
            "n_steps=3 corridor must force a larger page than n_steps=0"
        )

    def test_gap_fv_sv_widens_for_stepped_part(self):
        # A part with one step ≥20 mm tall (so dim_step is actually placed) gets
        # gap = _est_right_strip_depth(1) = 36 mm.  The ≥20 mm gate matches what
        # _auto_annotate applies — bore floors or shallow faces don't count.
        from build123d import Box, Pos

        from draftwright import build_drawing
        from draftwright.compose import _est_right_strip_depth

        # Box(60, 40, 50): Z -25..+25.  Carve top-right quadrant so the step
        # floor is at Z=0, giving a 25 mm step height (≥20 mm threshold).
        cutout = Pos(15, 0, 12.5) * Box(30, 40, 25)
        part = Box(60, 40, 50) - cutout

        a = build_drawing(part)._analysis
        assert len(a.step_zs) >= 1, "expected at least one step face"
        # The 25 mm step height passes the dim_step ≥20 mm gate → n_steps=1 → gap=36 mm
        expected_gap = _est_right_strip_depth(1)
        sv_left = a.SV_X - a.sv_hw
        fv_right = a.FV_X + a.fv_hw
        assert sv_left - fv_right == pytest.approx(expected_gap, abs=0.1)
