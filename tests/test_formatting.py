"""Formatting behavior used by drawing labels."""

from draftwright._core import _fmt


class TestFmt:
    def test_integer_value(self):
        assert _fmt(36.0) == "36"

    def test_fractional_value(self):
        assert _fmt(14.7) == "14.7"

    def test_zero(self):
        assert _fmt(0.0) == "0"

    def test_step_float_noise(self):
        # STEP-imported bounding boxes carry fp noise; near-integers must label cleanly
        assert _fmt(800.0000000000001) == "800"
        assert _fmt(-5.9999999999999) == "-6"
