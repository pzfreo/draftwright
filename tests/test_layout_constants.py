"""Layout constants derived from drawing text metrics."""

import pytest
from build123d import Box, Cylinder, Pos

from draftwright import build_drawing


class TestDerivedLayoutConstants:
    """Slots and callout widths derive from text metrics, not bare millimetres."""

    def test_slots_derive_from_font_metrics(self):
        from draftwright._core import (
            _FONT_SIZE,
            _PAD,
            _SLOT_DIM_DEPTH,
            _SLOT_DIM_HEIGHT,
            _SLOT_DIM_STEP,
            _SLOT_DIM_WIDTH,
        )

        assert _SLOT_DIM_WIDTH == pytest.approx(2 * _FONT_SIZE + _PAD)
        assert _SLOT_DIM_DEPTH == pytest.approx(2 * _FONT_SIZE + _PAD)
        assert _SLOT_DIM_HEIGHT == pytest.approx(2 * _FONT_SIZE + 2 * _PAD)
        assert _SLOT_DIM_STEP == pytest.approx(4 * _FONT_SIZE + _PAD)
        assert (2 * (2 * _FONT_SIZE) + 2 * _PAD) > _SLOT_DIM_HEIGHT

    def test_text_width_returns_real_glyph_metrics(self):
        from draftwright._core import _text_width

        assert _text_width("", 3.0) == 0.0
        w1 = _text_width("8", 3.0)
        w3 = _text_width("888", 3.0)
        assert 0.0 < w1 < w3
        assert _text_width("WXYZ", 3.0) > _text_width("iiii", 3.0)

    def test_bore_callout_width_scales_with_font_size(self):
        from build123d_drafting.helpers import draft_preset

        from draftwright.annotations.orchestrator import build_model
        from draftwright.compose import _est_planned_bore_callout_width
        from draftwright.model import plan_dimensions

        part = Box(60, 40, 12) - Pos(0, 0, 6) * Cylinder(3, 12)
        groups = plan_dimensions(build_model(build_drawing(part, number="X")._analysis))
        draft = draft_preset(decimal_precision=1)
        small = _est_planned_bore_callout_width(groups, draft, font_size=3.0)
        large = _est_planned_bore_callout_width(groups, draft, font_size=6.0)
        assert large > small
