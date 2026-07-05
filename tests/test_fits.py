"""ISO 286 fit-class → deviation table (ADR 0011 P2a.2, #29).

Every expected value below is a published ISO 286 limit deviation (µm → mm), so the
table is pinned against the standard, not against itself.
"""

import pytest

from draftwright.fits import fit_deviation, parse_fit


class TestParseFit:
    def test_hole_and_shaft_case(self):
        assert parse_fit("H7") == ("H", 7)
        assert parse_fit("h6") == ("h", 6)
        assert parse_fit("js6") == ("js", 6)

    def test_malformed_raises(self):
        with pytest.raises(ValueError):
            parse_fit("7")  # no letters
        with pytest.raises(ValueError):
            parse_fit("H")  # no grade


class TestDeviationsAt20mm:
    """The 18–30 mm band (⌀20) — the textbook worked example for every common class."""

    def test_H7(self):
        assert fit_deviation("H7", 20) == pytest.approx((0.0, 0.021))

    def test_h6(self):
        assert fit_deviation("h6", 20) == pytest.approx((-0.013, 0.0))

    def test_g6(self):
        assert fit_deviation("g6", 20) == pytest.approx((-0.020, -0.007))

    def test_f7(self):
        assert fit_deviation("f7", 20) == pytest.approx((-0.041, -0.020))

    def test_k6(self):
        assert fit_deviation("k6", 20) == pytest.approx((0.002, 0.015))

    def test_n6(self):
        assert fit_deviation("n6", 20) == pytest.approx((0.015, 0.028))

    def test_p6(self):
        assert fit_deviation("p6", 20) == pytest.approx((0.022, 0.035))

    def test_js6_is_symmetric(self):
        assert fit_deviation("js6", 20) == pytest.approx((-0.0065, 0.0065))

    def test_H8(self):
        assert fit_deviation("H8", 20) == pytest.approx((0.0, 0.033))


class TestOtherBands:
    def test_H7_small_bore(self):
        # 6–10 mm band, IT7 = 15 µm
        assert fit_deviation("H7", 8) == pytest.approx((0.0, 0.015))

    def test_h6_large(self):
        # 80–120 mm band, IT6 = 22 µm
        assert fit_deviation("h6", 100) == pytest.approx((-0.022, 0.0))

    def test_f8_hole_mirror(self):
        # F8 hole @ 18–30: EI = -es(f) = +20, ES = +20 + IT8(33) = +53
        assert fit_deviation("F8", 20) == pytest.approx((0.020, 0.053))

    def test_band_boundary_is_inclusive_upper(self):
        # nominal exactly on a band's upper bound belongs to that band (over-X, up-to-Y]
        assert fit_deviation("H7", 30) == pytest.approx((0.0, 0.021))  # still 18–30
        assert fit_deviation("H7", 30.5) == pytest.approx((0.0, 0.025))  # now 30–50, IT7=25


class TestFailLoud:
    def test_unsupported_hole_class_raises(self):
        with pytest.raises(ValueError):
            fit_deviation("P7", 20)  # interference hole (delta rule) — not modelled

    def test_unsupported_shaft_class_raises(self):
        with pytest.raises(ValueError):
            fit_deviation("a11", 20)

    def test_grade_outside_table_raises(self):
        with pytest.raises(ValueError):
            fit_deviation("H2", 20)  # IT2 not in the common-grade table

    def test_oversize_nominal_raises(self):
        with pytest.raises(ValueError):
            fit_deviation("H7", 400)  # beyond the ≤250 mm coverage

    def test_nonpositive_nominal_raises(self):
        with pytest.raises(ValueError):
            fit_deviation("H7", 0)
