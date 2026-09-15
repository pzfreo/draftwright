"""Explicit and automatic drawing scale policy behavior."""

import pytest
from build123d import Box

from draftwright import build_drawing
from draftwright._core import _MIN_VIEW_MM


class TestScaleMinimum:
    """An explicit scale below the legibility floor is warned about and rendered."""

    def test_explicit_illegible_scale_warns_and_renders(self):
        part = Box(680, 860, 80)
        with pytest.warns(UserWarning, match="legibility floor"):
            result = build_drawing(part, scale=0.1, scale_policy="permissive")
        assert result is not None

    def test_warning_suggests_safe_scale(self):
        import re

        part = Box(680, 860, 80)
        with pytest.warns(UserWarning) as record:
            build_drawing(part, scale=0.1, scale_policy="permissive")
        msg = str(record[0].message)
        assert "scale" in msg.lower()
        nums = re.findall(r"\d+\.?\d*", msg)
        safe_scales = [float(n) for n in nums if 0.1 < float(n) < 1.0]
        assert any(s >= _MIN_VIEW_MM / 80 for s in safe_scales)

    def test_degenerate_scale_raises(self):
        part = Box(680, 860, 80)
        with pytest.raises(ValueError, match="geometry degenerates"):
            build_drawing(part, scale=0.001)

    def test_safe_scale_does_not_warn(self):
        import warnings

        part = Box(680, 860, 80)
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            result = build_drawing(part, scale=0.2)
        assert result is not None

    def test_auto_scale_thin_part_does_not_raise(self):
        part = Box(80, 50, 8)
        result = build_drawing(part)
        assert result is not None

    def test_inherently_subfloor_part_does_not_warn(self):
        import warnings

        part = Box(3000, 3000, 15)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = build_drawing(part, scale=0.05)
        assert result is not None
        assert not [w for w in caught if "legibility floor" in str(w.message)]

    def test_sheet_explicit_scale_below_floor_is_honoured(self, tmp_path):
        from draftwright import Sheet

        with pytest.warns(UserWarning, match="legibility floor"):
            sheet = Sheet(
                Box(680, 860, 80), scale="1:10", scale_policy="permissive"
            ).auto_dimensions()
            sheet.export(str(tmp_path / "s"))
        assert (tmp_path / "s.pdf").exists()
