"""Repeated-feature TYP dimension behavior."""

from _parts import uniform_staircase as _uniform_staircase
from build123d import Box, Pos

from draftwright import build_drawing


class TestTypDimensioning:
    """#45: uniform staircase → single representative dim labelled N× rise."""

    def test_uniform_staircase_gets_typ_dim(self):
        dwg = build_drawing(_uniform_staircase(n_treads=8, rise=15.0))
        named = dict(dwg.iter_annotations())
        assert "dim_step_typ" in named, "expected a single representative step dim"
        assert not any(k.startswith("dim_step_") and k != "dim_step_typ" for k in named)
        assert named["dim_step_typ"].label == "8× 15"
        assert "dim_height" in named
        assert [i for i in dwg.lint() if i.severity == "error"] == []

    def test_typ_label_fractional_rise(self):
        dwg = build_drawing(_uniform_staircase(n_treads=5, rise=12.5, going=18.0))
        assert "dim_step_typ" in dwg.annotations()
        assert dwg.get_annotation("dim_step_typ").label == "5× 12.5"

    def test_irregular_staircase_gets_per_step_dims(self):
        # Non-uniform rises → fall back to per-step ladder.
        # Build as union of full-height slabs with decreasing footprint so
        # OpenCASCADE produces horizontal tread faces at each step level.
        cum_zs = [10.0, 30.0, 40.0, 65.0, 80.0]  # deliberately irregular
        n = len(cum_zs)
        going = 20
        part = None
        for i, total_h in enumerate(cum_zs):
            w = (n - i) * going
            b = Pos(w / 2, 0, total_h / 2) * Box(w, 30, total_h)
            part = b if part is None else part + b
        dwg = build_drawing(part)
        assert "dim_step_typ" not in dwg.annotations()
        assert any(k.startswith("dim_step_") and k != "dim_step_typ" for k in dwg.annotations())

    def test_two_step_part_not_detected_as_pattern(self):
        # Only 2 interior steps → below the ≥3 threshold; per-step path used.
        from draftwright.annotate import _detect_step_repeat

        step_zs = [10.0, 20.0]
        result = _detect_step_repeat(step_zs, 0.0, 30.0)
        assert result is None

    def test_detect_step_repeat_uniform(self):
        from draftwright.annotate import _detect_step_repeat

        zs = [15.0, 30.0, 45.0, 60.0, 75.0, 90.0, 105.0]
        n, rise = _detect_step_repeat(zs, 0.0, 120.0)
        assert n == 8
        assert abs(rise - 15.0) < 0.01

    def test_detect_step_repeat_nonuniform(self):
        from draftwright.annotate import _detect_step_repeat

        zs = [10.0, 25.0, 35.0, 60.0]
        assert _detect_step_repeat(zs, 0.0, 70.0) is None

    def test_detect_step_repeat_top_gap_mismatch_excluded_from_count(self):
        # When top gap doesn't match the mean rise, n = len(step_zs) not +1.
        from draftwright.annotate import _detect_step_repeat

        zs = [10.0, 20.0, 30.0]  # 3 equal interior rises of 10mm
        # top gap = 55 - 30 = 25 ≠ 10 → should NOT add 1
        n, rise = _detect_step_repeat(zs, 0.0, 55.0)
        assert n == 3
        assert abs(rise - 10.0) < 0.01

    def test_three_step_part_gets_typ_dim(self):
        # Integration: exactly 3 interior steps (the minimum threshold) → TYP path.
        dwg = build_drawing(_uniform_staircase(n_treads=4, rise=20.0))
        assert "dim_step_typ" in dwg.annotations()
        assert not any(
            k.startswith("dim_step_") and k != "dim_step_typ" for k in dwg.annotations()
        )
