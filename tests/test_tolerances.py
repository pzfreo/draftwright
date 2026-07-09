"""P2a — toleranced dimensions (ADR 0011 Phase 2, #28).

A caller attaches a ± / limit tolerance to a declared dimension (via the ``decorations``
side-layer or the ``Sheet.tolerance()`` handle); it rides ``DimParameter.tolerance`` through
the planner and renders on **both** the linear ``Dimension`` path (step length) and the
``Leader`` / ``HoleCallout`` ⌀ path — the latter via draftwright's own ``_tol_suffix`` baked
into the label string, matching what ``Dimension(tolerance=…)`` formats (helpers has no
``tolerance=`` on ``Leader``/``HoleCallout`` yet). Tolerances render at the sheet's decimal
precision (1 dp today), so tests use tolerances that survive 1 dp.
"""

from build123d import Box, Cylinder, Pos, Rot
from build123d_drafting.helpers import draft_preset

from draftwright import Sheet
from draftwright._core import _tol_suffix
from draftwright.annotations.from_model import callout_from_spec, hole_callout_spec
from draftwright.model import PartModel, hole, step
from draftwright.model.planner import plan_dimensions


def _spec(diameter, **over):
    base = {
        "diameter": diameter,
        "count": None,
        "through": True,
        "depth": None,
        "cbore_dia": None,
        "cbore_depth": None,
        "suffix": None,
    }
    base.update(over)
    return base


class TestTolSuffix:
    """The owned callout formatter — must byte-match helpers' ``_format_label`` suffix."""

    def test_symmetric_float(self):
        d = draft_preset(font_size=2.5, decimal_precision=2)
        assert _tol_suffix(0.05, d) == " ±0.05"

    def test_symmetric_respects_precision(self):
        d1 = draft_preset(font_size=2.5, decimal_precision=1)
        assert _tol_suffix(0.05, d1) == " ±0.1"  # rounds to the draft precision, like Dimension

    def test_limit_pair_is_plus_upper_minus_lower(self):
        d = draft_preset(font_size=2.5, decimal_precision=1)
        # tuple is (lower, upper) → "+upper -lower" (helpers' convention)
        assert _tol_suffix((0.0, 0.2), d) == " +0.2 -0.0"

    def test_none_is_empty(self):
        d = draft_preset(font_size=2.5, decimal_precision=1)
        assert _tol_suffix(None, d) == ""


class TestPlannerDecorations:
    def test_decoration_sets_param_tolerance_keyed_by_kind(self):
        # A step carries BOTH a length and a diameter param with role="step"; the decoration
        # key is (feature, kind), so length and diameter are toleranced independently.
        part = Rot(0, 90, 0) * Cylinder(4, 20)
        st = step(diameter=8, length=20, at=(0, 0, 0), axis="x")
        model = PartModel(
            bbox=part.bounding_box(),
            orientation="x",
            features=[st],
            decorations={(st, "length"): 0.2, (st, "diameter"): 0.1},
        )
        groups = plan_dimensions(model)
        dims = {pd.param.kind: pd.param.tolerance for pd in groups[0].dims}
        assert dims["length"] == 0.2
        assert dims["diameter"] == 0.1

    def test_no_decoration_leaves_tolerance_none(self):
        part = Rot(0, 90, 0) * Cylinder(4, 20)
        st = step(diameter=8, length=20, at=(0, 0, 0), axis="x")
        model = PartModel(bbox=part.bounding_box(), orientation="x", features=[st])
        groups = plan_dimensions(model)
        assert all(pd.param.tolerance is None for pd in groups[0].dims)


class TestCalloutRendering:
    def test_hole_bore_spec_carries_tolerance_and_widens_callout(self):
        # The bore tolerance rides the spec and bakes into the HoleCallout diameter string,
        # so the rendered callout is geometrically WIDER than the untoleranced one (the
        # HoleCallout exposes no text attribute, so width is the observable end-to-end proof).
        d = draft_preset(font_size=2.5, decimal_precision=1)
        plain = callout_from_spec(_spec(8), d, None)
        toll = callout_from_spec(_spec(8, tolerance=0.1), d, None)
        assert toll.bounding_box().size.X > plain.bounding_box().size.X

    def test_hole_callout_spec_reads_bore_tolerance_from_plan(self):
        h = hole(diameter=8, at=(20, 10, 4), axis="z")
        model = PartModel(
            bbox=Box(40, 40, 8).bounding_box(),
            orientation=None,
            features=[h],
            decorations={(h, "diameter"): 0.1},
        )
        group = next(g for g in plan_dimensions(model) if g.feature_kind == "hole")
        assert hole_callout_spec(group)["tolerance"] == 0.1


class TestSheetTolerance:
    @staticmethod
    def _stepped_shaft():
        # a genuine 2-diameter turned shaft: distinct shoulders → both a step chain and ⌀ leaders
        return (Rot(0, 90, 0) * Cylinder(4, 20)) + (
            Pos(15, 0, 0) * Rot(0, 90, 0) * Cylinder(6, 10)
        )

    def _dias(self, dwg):
        return {n: dwg._named[n].label for n in dwg._named if n.startswith("m_dia")}

    def _steplen_tol(self, dwg, name):
        o = dwg._named[name]
        return o._dw_spec.kwargs.get("tolerance")

    def test_boss_diameter_tolerance_renders_on_leader(self):
        shaft = self._stepped_shaft()
        s = Sheet(shaft)
        s.step(diameter=8, length=20, at=(0, 0, 0), axis="x")
        s.diameter(diameter=12, at=(15, 0, 0), axis="x").tolerance(0.1)
        dwg = s.build()
        assert any(lbl == "ø12 ±0.1" for lbl in self._dias(dwg).values()), self._dias(dwg)

    def test_boss_limit_pair_renders(self):
        shaft = self._stepped_shaft()
        s = Sheet(shaft)
        s.step(diameter=8, length=20, at=(0, 0, 0), axis="x")
        s.diameter(diameter=12, at=(15, 0, 0), axis="x").tolerance(0.0, 0.2)
        dwg = s.build()
        assert any(lbl == "ø12 +0.2 -0.0" for lbl in self._dias(dwg).values()), self._dias(dwg)

    def test_step_length_tolerance_reaches_dimension(self):
        shaft = self._stepped_shaft()
        s = Sheet(shaft)
        s.step(diameter=8, length=20, at=(0, 0, 0), axis="x").tolerance(0.0, 0.2)
        s.step(diameter=12, length=10, at=(15, 0, 0), axis="x")
        dwg = s.build()
        tols = {self._steplen_tol(dwg, n) for n in dwg._named if n.startswith("m_steplen")}
        assert (0.0, 0.2) in tols

    def test_step_tolerance_defaults_to_length_not_diameter(self):
        shaft = self._stepped_shaft()
        s = Sheet(shaft)
        s.step(diameter=8, length=20, at=(0, 0, 0), axis="x").tolerance(0.1)
        s.step(diameter=12, length=10, at=(15, 0, 0), axis="x")
        dwg = s.build()
        # the bare .tolerance() went to the length dim; the OD leader stays plain
        assert all("±" not in lbl and "+" not in lbl for lbl in self._dias(dwg).values())
        assert 0.1 in {self._steplen_tol(dwg, n) for n in dwg._named if n.startswith("m_steplen")}

    def test_step_on_diameter_tolerances_the_od(self):
        shaft = self._stepped_shaft()
        s = Sheet(shaft)
        s.step(diameter=8, length=20, at=(0, 0, 0), axis="x").tolerance(0.1, on="diameter")
        s.step(diameter=12, length=10, at=(15, 0, 0), axis="x")
        dwg = s.build()
        assert any(lbl == "ø8 ±0.1" for lbl in self._dias(dwg).values()), self._dias(dwg)

    def test_no_tolerance_is_inert(self):
        # The same declared model without any .tolerance() carries no ± anywhere and leaves
        # every step dim untoleranced — the tolerance path is a no-op without decorations.
        shaft = self._stepped_shaft()
        s = Sheet(shaft)
        s.step(diameter=8, length=20, at=(0, 0, 0), axis="x")
        s.step(diameter=12, length=10, at=(15, 0, 0), axis="x")
        dwg = s.build()
        assert all("±" not in lbl and "+" not in lbl for lbl in self._dias(dwg).values())
        assert all(
            self._steplen_tol(dwg, n) is None for n in dwg._named if n.startswith("m_steplen")
        )

    def test_toleranced_hole_callout_participates_in_layout_sizing(self):
        # #450: a Sheet-authored bore tolerance widens the HoleCallout. Layout
        # sizing must reserve that declared footprint before the plan/side/iso
        # blocks are placed; otherwise the real callout is later dropped from the
        # iso-bounded plan strip even though a wider corridor would fit.
        plate = Box(120, 90, 8)
        part = plate - (Pos(0, 0, 4) * Cylinder(4, 8))

        s = Sheet(part)
        s.envelope(plate)
        s.hole(diameter=8, at=(0, 0, 4), axis="z").tolerance(0.1)
        dwg = s.build()

        assert any(n.startswith("hc_plan") for n in dwg._named)
        assert "callout_dropped" not in {i.code for i in dwg.lint()}


class TestToleranceHandle:
    def test_hole_tolerance_survives_feature_replacement(self):
        # .depth() replaces the feature object; the tolerance is keyed by index, so it still
        # lands on the final (blind) hole's bore.
        plate = Box(60, 40, 8)
        h = Pos(0, 0, 4) * Cylinder(4, 6)
        part = plate - h
        s = Sheet(part)
        s.hole(diameter=8, at=(0, 0, 4), axis="z").depth(6).tolerance(0.1)
        model = s.build().model()
        hf = next(f for f in model.features if f.kind == "hole")
        assert s._tolerances == {(0, "diameter"): 0.1}
        # the decoration resolves to the FINAL feature (through=False after .depth)
        assert hf.through is False


class TestCoerceModelPurity:
    def test_verbatim_partmodel_is_not_mutated_by_decorations(self):
        # A PartModel is a reusable public input (ADR 0011); _coerce_model must merge
        # decorations into a COPY, never mutate the caller's object — else a second build
        # (with no decorations) inherits stale tolerances from the first.
        from draftwright.builder import _coerce_model

        st = step(diameter=8, length=20, at=(0, 0, 0), axis="x")
        pm = PartModel(bbox=None, orientation="x", features=[st])
        # the verbatim-PartModel path never reads `a`; None keeps the test focused.
        out = _coerce_model(pm, None, decorations={(st, "length"): 0.2})
        assert out.decorations == {(st, "length"): 0.2}
        assert pm.decorations == {}, "caller's PartModel was mutated in place"

        # a subsequent bare build sees no leaked tolerance
        bare = _coerce_model(pm, None)
        assert bare.decorations == {}

    def test_verbatim_partmodel_decorations_merge_not_replace(self):
        from draftwright.builder import _coerce_model

        st = step(diameter=8, length=20, at=(0, 0, 0), axis="x")
        pm = PartModel(
            bbox=None, orientation="x", features=[st], decorations={(st, "diameter"): 0.1}
        )
        out = _coerce_model(pm, None, decorations={(st, "length"): 0.2})
        assert out.decorations == {(st, "diameter"): 0.1, (st, "length"): 0.2}
