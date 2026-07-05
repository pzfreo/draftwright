"""ISO 286 fit-class → deviation table (ADR 0011 P2a.2, #29).

Every expected value below is a published ISO 286 limit deviation (µm → mm), so the
table is pinned against the standard, not against itself.
"""

import pytest
from build123d import Box, Cylinder, Pos, Rot

from draftwright import Sheet
from draftwright.annotations.from_model import callout_from_spec, hole_callout_spec
from draftwright.fits import FitClass, fit_class, fit_deviation, parse_fit
from draftwright.model import PartModel, hole
from draftwright.model.planner import plan_dimensions


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

    def test_k_grade_dependency(self):
        # ISO 286: shaft k fundamental deviation ei applies only for IT4–7; for coarser
        # grades ei = 0. k7 @ 50–80 keeps ei=+2; k8 @ 50–80 drops to ei=0 (#29 review).
        assert fit_deviation("k7", 60) == pytest.approx((0.002, 0.032))  # ei=+2, IT7=30
        assert fit_deviation("k8", 60) == pytest.approx((0.0, 0.046))  # ei=0, IT8=46
        assert fit_deviation("k9", 60) == pytest.approx((0.0, 0.074))  # ei=0, IT9=74

    def test_n_and_p_are_grade_independent(self):
        # only k has the grade cutoff; n/p keep their tabulated ei at every grade
        assert fit_deviation("n8", 20) == pytest.approx((0.015, 0.048))  # ei=+15, IT8=33
        assert fit_deviation("p8", 20) == pytest.approx((0.022, 0.055))  # ei=+22, IT8=33

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


class TestFitClass:
    def test_class_suffix_is_the_code(self):
        assert fit_class("H7", 20).suffix() == " H7"

    def test_deviation_suffix_is_upper_over_lower(self):
        # H7 @ 20 = (0, +0.021) → upper/lower = "+0.021/0"
        assert fit_class("H7", 20, show="deviation").suffix() == " +0.021/0"

    def test_deviation_suffix_both_negative(self):
        # g6 @ 20 = (-0.020, -0.007) → "-0.007/-0.020" (upper over lower)
        assert fit_class("g6", 20, show="deviation").suffix() == " -0.007/-0.020"

    def test_deviation_keeps_half_micron_precision(self):
        # js6 @ 20 = ±0.0065 — must NOT round to the sheet's 1 dp
        assert fit_class("js6", 20, show="deviation").suffix() == " +0.0065/-0.0065"

    def test_carries_the_resolved_deviations(self):
        f = fit_class("H7", 20)
        assert isinstance(f, FitClass)
        assert (f.lower, f.upper) == pytest.approx((0.0, 0.021))

    def test_bad_show_raises(self):
        with pytest.raises(ValueError):
            fit_class("H7", 20, show="both")

    def test_bad_code_raises_at_resolution(self):
        with pytest.raises(ValueError):
            fit_class("Z9", 20)


class TestPlannerFit:
    def test_fit_decoration_sets_param_tolerance(self):
        h = hole(diameter=8, at=(20, 10, 4), axis="z")
        model = PartModel(
            bbox=Box(40, 40, 8).bounding_box(),
            orientation=None,
            features=[h],
            decorations={(h, "diameter"): fit_class("H7", 8)},
        )
        group = next(g for g in plan_dimensions(model) if g.feature_kind == "hole")
        bore = next(pd for pd in group.dims if pd.param.kind == "diameter")
        assert isinstance(bore.param.tolerance, FitClass)
        assert bore.param.tolerance.code == "H7"


class TestCalloutFit:
    @staticmethod
    def _spec(diameter, **over):
        base = {
            "diameter": diameter,
            "count": None,
            "through": True,
            "depth": None,
            "cbore_dia": None,
            "cbore_depth": None,
            "suffix": None,
            "tolerance": None,
        }
        base.update(over)
        return base

    def test_hole_bore_callout_carries_the_fit_class(self):
        from build123d_drafting.helpers import draft_preset

        d = draft_preset(font_size=2.5, decimal_precision=1)
        plain = callout_from_spec(self._spec(8), d, None)
        fitted = callout_from_spec(self._spec(8, tolerance=fit_class("H7", 8)), d, None)
        # the fit widens the callout exactly like a ± tolerance (label carries " H7")
        assert fitted.bounding_box().size.X > plain.bounding_box().size.X

    def test_hole_callout_spec_reads_the_bore_fit(self):
        h = hole(diameter=8, at=(20, 10, 4), axis="z")
        model = PartModel(
            bbox=Box(40, 40, 8).bounding_box(),
            orientation=None,
            features=[h],
            decorations={(h, "diameter"): fit_class("H7", 8)},
        )
        group = next(g for g in plan_dimensions(model) if g.feature_kind == "hole")
        assert hole_callout_spec(group)["tolerance"].code == "H7"


class TestSheetFit:
    @staticmethod
    def _stepped_shaft():
        return (Rot(0, 90, 0) * Cylinder(4, 20)) + (
            Pos(15, 0, 0) * Rot(0, 90, 0) * Cylinder(6, 10)
        )

    def _dias(self, dwg):
        return {n: dwg._named[n].label for n in dwg._named if n.startswith("m_dia")}

    def test_boss_fit_class_renders_on_leader(self):
        s = Sheet(self._stepped_shaft())
        s.step(diameter=8, length=20, at=(0, 0, 0), axis="x")
        s.diameter(diameter=12, at=(15, 0, 0), axis="x").fit("g6")
        dwg = s.build()
        assert any(lbl == "ø12 g6" for lbl in self._dias(dwg).values()), self._dias(dwg)

    def test_boss_fit_deviation_renders_on_leader(self):
        s = Sheet(self._stepped_shaft())
        s.step(diameter=8, length=20, at=(0, 0, 0), axis="x")
        # h6 @ ⌀12 (10–18 band, IT6=11) = (-0.011, 0) → "0/-0.011"
        s.diameter(diameter=12, at=(15, 0, 0), axis="x").fit("h6", show="deviation")
        dwg = s.build()
        assert any(lbl == "ø12 0/-0.011" for lbl in self._dias(dwg).values()), self._dias(dwg)

    def test_fit_bad_class_raises_at_declaration(self):
        s = Sheet(self._stepped_shaft())
        d = s.diameter(diameter=12, at=(15, 0, 0), axis="x")
        with pytest.raises(ValueError):
            d.fit("Z9")  # unknown class
