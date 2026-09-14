"""Fit propagation through planning, callouts, and declared drawings."""

import pytest
from build123d import Box, Cylinder, Pos, Rot

from draftwright import Sheet
from draftwright.annotations.from_model import callout_from_spec, hole_callout_spec
from draftwright.fits import FitClass, fit_class
from draftwright.model import PartModel, hole
from draftwright.model.planner import plan_dimensions


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
        return {n: dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("m_dia")}

    def test_boss_fit_class_renders_on_leader(self):
        s = Sheet(self._stepped_shaft()).auto_dimensions()
        s.step(diameter=8, length=20, at=(0, 0, 0), axis="x")
        s.diameter(diameter=12, at=(15, 0, 0), axis="x").fit("g6")
        dwg = s.build()
        assert any(lbl == "ø12 g6" for lbl in self._dias(dwg).values()), self._dias(dwg)

    def test_boss_fit_deviation_renders_on_leader(self):
        s = Sheet(self._stepped_shaft()).auto_dimensions()
        s.step(diameter=8, length=20, at=(0, 0, 0), axis="x")
        # h6 @ ⌀12 (10–18 band, IT6=11) = (-0.011, 0) → "0/-0.011"
        s.diameter(diameter=12, at=(15, 0, 0), axis="x").fit("h6", show="deviation")
        dwg = s.build()
        assert any(lbl == "ø12 0/-0.011" for lbl in self._dias(dwg).values()), self._dias(dwg)

    def test_fit_bad_class_raises_at_declaration(self):
        s = Sheet(self._stepped_shaft()).auto_dimensions()
        d = s.diameter(diameter=12, at=(15, 0, 0), axis="x")
        with pytest.raises(ValueError):
            d.fit("Z9")  # unknown class
