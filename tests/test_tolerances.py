"""P2a — toleranced dimensions (ADR 0011 Phase 2, #28).

A caller attaches a ± / limit tolerance to a declared dimension (via the ``decorations``
side-layer or the ``Sheet.tolerance()`` handle); it rides ``DimParameter.tolerance`` through
the planner and renders on **both** the linear ``Dimension`` path (step length) and the
``Leader`` / ``HoleCallout`` ⌀ path — the latter via draftwright's own ``_tol_suffix`` baked
into the label string, matching what ``Dimension(tolerance=…)`` formats (helpers has no
``tolerance=`` on ``Leader``/``HoleCallout`` yet). Tolerances render at the sheet's decimal
precision (1 dp today), so tests use tolerances that survive 1 dp.
"""

import pytest
from build123d import Axis, Box, Cylinder, Pos, Rot
from build123d import chamfer as b3d_chamfer
from build123d import fillet as b3d_fillet
from build123d_drafting.helpers import draft_preset

from draftwright import Sheet, build_drawing
from draftwright._core import _tol_suffix
from draftwright.annotations.from_model import callout_from_spec, hole_callout_spec
from draftwright.model import (
    PartModel,
    chamfer,
    fillet,
    flat,
    groove,
    hole,
    plate,
    pocket,
    slot,
    step,
)
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

    def test_chamfer_dim_is_leader_with_folded_tolerance(self):
        # #724: the chamfer leg routes through the planner — convention "leader", with an
        # authored decoration folded onto DimParameter.tolerance like every planner-fed kind.
        ch = chamfer(axis="z", leg=12, at=(39, 24, 0))
        model = PartModel(
            bbox=Box(90, 60, 20).bounding_box(),
            orientation=None,
            features=[ch],
            decorations={(ch, "length"): 0.2},
        )
        g = next(g for g in plan_dimensions(model) if g.feature_kind == "chamfer")
        (pd,) = g.dims
        assert pd.convention == "leader"
        assert pd.param.tolerance == 0.2
        assert not pd.suppressed
        assert g.view == "plan"  # frame axis == edge axis; a Z-edge chamfer reads in the plan

    def test_fillet_dim_is_leader_with_folded_tolerance(self):
        # #725: the fillet radius routes through the planner — convention "leader", with an
        # authored decoration folded onto DimParameter.tolerance (keyed by kind "radius").
        fl = fillet(axis="z", radius=8, at=(41, 26, 0))
        model = PartModel(
            bbox=Box(90, 60, 20).bounding_box(),
            orientation=None,
            features=[fl],
            decorations={(fl, "radius"): 0.1},
        )
        g = next(g for g in plan_dimensions(model) if g.feature_kind == "fillet")
        (pd,) = g.dims
        assert pd.convention == "leader"
        assert pd.param.tolerance == 0.1
        assert not pd.suppressed
        assert g.view == "plan"  # frame axis == edge axis; a Z-edge fillet reads in the plan

    def test_flat_dim_is_leader_with_folded_tolerance(self):
        # #726: the across-flats size routes through the planner — convention "leader",
        # with an authored decoration folded onto DimParameter.tolerance (kind "length").
        fl = flat(axis="z", across=17, at=(7, 0, 0))
        model = PartModel(
            bbox=Cylinder(10, 30).bounding_box(),
            orientation=None,
            features=[fl],
            decorations={(fl, "length"): 0.2},
        )
        g = next(g for g in plan_dimensions(model) if g.feature_kind == "flat")
        (pd,) = g.dims
        assert pd.convention == "leader"
        assert pd.param.tolerance == 0.2
        assert not pd.suppressed
        assert g.view == "plan"  # frame axis == stock axis; a Z-bar flat reads in the plan

    def test_groove_dims_are_leaders_with_independent_tolerances(self):
        # #727: the multi-param case — width (kind "length") and floor ø (kind "diameter")
        # are distinct decoration keys, so the one groove callout carries BOTH, each with
        # its own folded tolerance.
        gr = groove(axis="z", width=4, diameter=16, at=(0, 0, 0))
        model = PartModel(
            bbox=Cylinder(10, 40).bounding_box(),
            orientation=None,
            features=[gr],
            decorations={(gr, "length"): 0.1, (gr, "diameter"): 0.5},
        )
        g = next(g for g in plan_dimensions(model) if g.feature_kind == "groove")
        by_key = {(pd.param.role, pd.param.kind): pd for pd in g.dims}
        wpd = by_key[("groove", "length")]
        dpd = by_key[("groove", "diameter")]
        assert wpd.convention == "leader" and dpd.convention == "leader"
        assert wpd.param.tolerance == 0.1
        assert dpd.param.tolerance == 0.5
        assert not wpd.suppressed and not dpd.suppressed

    def test_pocket_dims_are_leaders_one_length_tolerance_folds_onto_all_three(self):
        # #728: a pocket's width/length/depth are three distinct-ROLE params sharing kind
        # "length", and decorations key on (feature, kind) — so ONE authored length
        # tolerance folds onto ALL THREE values (documented behaviour; independent
        # per-role tolerancing is an authoring-surface gap, tracked by #746).
        pk = pocket(width=18, length=30, depth=5, long_axis="x", width_axis="y", lo=-15, hi=15)
        model = PartModel(
            bbox=Box(90, 60, 20).bounding_box(),
            orientation=None,
            features=[pk],
            decorations={(pk, "length"): 0.2},
        )
        g = next(g for g in plan_dimensions(model) if g.feature_kind == "pocket")
        by_key = {(pd.param.role, pd.param.kind): pd for pd in g.dims}
        assert set(by_key) == {
            ("pocket_width", "length"),
            ("pocket_length", "length"),
            ("pocket_depth", "length"),
        }
        for pd in by_key.values():
            assert pd.convention == "leader"
            assert pd.param.tolerance == 0.2
            assert not pd.suppressed

    def test_pocket_role_keyed_tolerance_targets_one_param(self):
        # #746: a ROLE-keyed (feature, kind, role) decoration tolerances ONE param of a
        # multi-param kind — here only the pocket's depth — leaving width/length
        # untoleranced. (The kind-only form still folds onto all three; see the test
        # above.) This is the seam that unlocks per-param tolerancing.
        pk = pocket(width=18, length=30, depth=5, long_axis="x", width_axis="y", lo=-15, hi=15)
        model = PartModel(
            bbox=Box(90, 60, 20).bounding_box(),
            orientation=None,
            features=[pk],
            decorations={(pk, "length", "pocket_depth"): 0.2},
        )
        g = next(g for g in plan_dimensions(model) if g.feature_kind == "pocket")
        tol = {pd.param.role: pd.param.tolerance for pd in g.dims}
        assert tol == {"pocket_width": None, "pocket_length": None, "pocket_depth": 0.2}

    def test_role_keyed_decoration_wins_over_kind_keyed(self):
        # #746: when both are present, the role-specific decoration wins for its param
        # and the kind-only one folds onto the rest.
        pk = pocket(width=18, length=30, depth=5, long_axis="x", width_axis="y", lo=-15, hi=15)
        model = PartModel(
            bbox=Box(90, 60, 20).bounding_box(),
            orientation=None,
            features=[pk],
            decorations={(pk, "length"): 0.2, (pk, "length", "pocket_depth"): 0.05},
        )
        g = next(g for g in plan_dimensions(model) if g.feature_kind == "pocket")
        tol = {pd.param.role: pd.param.tolerance for pd in g.dims}
        assert tol == {"pocket_width": 0.2, "pocket_length": 0.2, "pocket_depth": 0.05}

    def test_rotational_role_keyed_od_vs_bore_tolerance(self):
        # #746/#754: a rotational's OD and bores all share kind "diameter"; a role-keyed
        # decoration tolerances the OD (role "od") independently of the bores (role
        # "bore"). RotationalFeature is detection-only, so this is authored via the raw
        # decorations map (no Sheet handle) — the engine-level unlock behind #754.
        from draftwright.model.ir import Frame, RotationalFeature

        rot = RotationalFeature(frame=Frame((0.0, 0.0, 0.0), "z"), od=30.0, bores=(8.0, 5.0))
        model = PartModel(
            bbox=Box(60, 60, 20).bounding_box(),
            orientation=None,
            features=[rot],
            decorations={(rot, "diameter", "od"): 0.1, (rot, "diameter", "bore"): 0.05},
        )
        g = next(g for g in plan_dimensions(model) if g.feature_kind == "rotational")
        # OD gets 0.1; both bores share role "bore" so each gets 0.05.
        assert [(pd.param.role, pd.param.tolerance) for pd in g.dims] == [
            ("od", 0.1),
            ("bore", 0.05),
            ("bore", 0.05),
        ]

    def test_plate_dim_is_linear_with_folded_tolerance(self):
        # #729: the plate thickness routes through the planner — convention "linear"
        # (the _CONVENTION default; the first non-leader kind migration, so no table
        # entry), with an authored decoration folded onto DimParameter.tolerance
        # (kind "length").
        pl = plate(axis="z", lo=-4, hi=4, u=0, v=0)
        model = PartModel(
            bbox=Box(80, 50, 8).bounding_box(),
            orientation=None,
            features=[pl],
            decorations={(pl, "length"): 0.1},
        )
        g = next(g for g in plan_dimensions(model) if g.feature_kind == "plate")
        (pd,) = g.dims
        assert (pd.param.role, pd.param.kind) == ("thickness", "length")
        assert pd.convention == "linear"
        assert pd.param.value == 8.0  # hi - lo — exactly what the renderer displays
        assert pd.param.tolerance == 0.1
        assert not pd.suppressed

    def test_slot_dims_are_linear_with_folded_tolerance(self):
        # #730: a slot's width + length route through the planner — convention "linear"
        # (explicit _CONVENTION entries per the #744 review rule), each bound by its
        # (role, kind). Both share kind "length" and decorations key on (feature, kind),
        # so ONE authored length tolerance folds onto BOTH values (documented behaviour,
        # the pocket precedent; independent per-role tolerancing is an authoring-surface
        # gap, tracked by #746).
        sl = slot(width=8, length=20, long_axis="x", width_axis="y", lo=-10, hi=10, w_center=0)
        model = PartModel(
            bbox=Box(50, 30, 20).bounding_box(),
            orientation=None,
            features=[sl],
            decorations={(sl, "length"): 0.1},
        )
        g = next(g for g in plan_dimensions(model) if g.feature_kind == "slot")
        by_key = {(pd.param.role, pd.param.kind): pd for pd in g.dims}
        assert set(by_key) == {("slot_width", "length"), ("slot_length", "length")}
        wpd = by_key[("slot_width", "length")]
        lpd = by_key[("slot_length", "length")]
        assert wpd.convention == "linear" and lpd.convention == "linear"
        assert wpd.param.value == 8.0 and lpd.param.value == 20.0
        assert wpd.param.tolerance == 0.1 and lpd.param.tolerance == 0.1
        assert not wpd.suppressed and not lpd.suppressed


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
        return {n: dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("m_dia")}

    def _steplen_tol(self, dwg, name):
        o = dwg.get_annotation(name)
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
        tols = {self._steplen_tol(dwg, n) for n in dwg.annotations() if n.startswith("m_steplen")}
        assert (0.0, 0.2) in tols

    @staticmethod
    def _pocket_part():
        return Box(90, 60, 20) - Pos(0, 0, 6) * Box(30, 18, 8)

    def _pocket_handle(self, s):
        return s.pocket(width=18, length=30, depth=5, long_axis="x", width_axis="y", lo=-15, hi=15)

    def _deep_label(self, dwg):
        return next(
            str(dwg.get_annotation(n).label)
            for n in dwg.annotations()
            if "DEEP" in str(dwg.get_annotation(n).label)
        )

    def test_pocket_role_keyed_depth_tolerance_via_sheet(self):
        # #746 Sheet surface: pocket() returns a role-aware handle; .tolerance(on="depth")
        # tolerances ONLY the depth (5), leaving width×length (18×30) plain.
        s = Sheet(self._pocket_part(), title="P")
        self._pocket_handle(s).tolerance(0.2, on="depth")
        assert self._deep_label(s.build()) == "18 × 30 × 5 ±0.2 DEEP"

    def test_pocket_whole_feature_tolerance_folds_onto_all(self):
        # A bare .tolerance() (no on=) on the handle folds onto every parameter — the
        # kind-keyed back-compat form.
        s = Sheet(self._pocket_part(), title="P")
        self._pocket_handle(s).tolerance(0.2)
        assert self._deep_label(s.build()) == "18 ±0.2 × 30 ±0.2 × 5 ±0.2 DEEP"

    def test_pocket_role_selector_rejects_unknown_name(self):
        # on= must name exactly one parameter role of the feature.
        s = Sheet(self._pocket_part(), title="P")
        with pytest.raises(ValueError):
            self._pocket_handle(s).tolerance(0.2, on="nope")

    def test_params_handle_still_chains_to_further_declarations(self):
        # #807 review: the _Params handle forwards unknown attributes to the sheet, so a
        # verb returning it stays chainable (the declare-then-chain contract holds).
        s = Sheet(self._pocket_part(), title="P")
        dwg = self._pocket_handle(s).envelope().build()  # .envelope()/.build() forward
        assert "front" in dwg.views

    def test_bare_tolerance_supersedes_an_earlier_role_tolerance(self):
        # #807 review: a whole-feature .tolerance() means "all alike" and overrides an
        # earlier per-role one regardless of call order (drops the role-keyed entry).
        s = Sheet(self._pocket_part(), title="P")
        self._pocket_handle(s).tolerance(0.1, on="depth").tolerance(0.2)
        assert self._deep_label(s.build()) == "18 ±0.2 × 30 ±0.2 × 5 ±0.2 DEEP"

    def test_params_handle_is_a_valid_gdt_target(self):
        # #807 re-review: a _Params handle names a real feature, so it must be accepted
        # everywhere a feature handle is — datum/finish/control targets, like _Hole/_Dim.
        s = Sheet(self._pocket_part(), title="P")
        h = self._pocket_handle(s)
        s.datum("A", h)
        s.finish("1.6", h)
        s.control(h).position(0.1)
        s.build()  # must not raise "GD&T target must be an IR feature..."

    def test_step_tolerance_defaults_to_length_not_diameter(self):
        shaft = self._stepped_shaft()
        s = Sheet(shaft)
        s.step(diameter=8, length=20, at=(0, 0, 0), axis="x").tolerance(0.1)
        s.step(diameter=12, length=10, at=(15, 0, 0), axis="x")
        dwg = s.build()
        # the bare .tolerance() went to the length dim; the OD leader stays plain
        assert all("±" not in lbl and "+" not in lbl for lbl in self._dias(dwg).values())
        assert 0.1 in {
            self._steplen_tol(dwg, n) for n in dwg.annotations() if n.startswith("m_steplen")
        }

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
            self._steplen_tol(dwg, n) is None
            for n in dwg.annotations()
            if n.startswith("m_steplen")
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

        assert any(n.startswith("hc_plan") for n in dwg.annotations())
        assert "callout_dropped" not in {i.code for i in dwg.lint()}


class TestChamferTolerance:
    """#724 (the #629 class, latent): a chamfer's authored tolerance must render on the
    placed callout — the pass now consumes the planner's DimensionGroup, whose param
    carries the folded decoration, instead of formatting raw feature fields."""

    @staticmethod
    def _chamfered_plate():
        plate = Box(90, 60, 20)
        e = plate.edges().filter_by(Axis.Z).sort_by(lambda e: e.center().X + e.center().Y)[-1]
        return b3d_chamfer(e, 12)

    def test_authored_chamfer_tolerance_renders_on_callout(self):
        # Declared model (ADR 0011): the caller holds the feature object, so the
        # decoration keys on it. (Sheet.chamfer returns the Sheet, not an aspect handle,
        # so build_drawing(model=…, decorations=…) is the authoring surface here.)
        ch = chamfer(axis="z", leg=12, at=(39, 24, 0))  # bevel midpoint of the cut corner
        dwg = build_drawing(
            self._chamfered_plate(),
            model=[ch],
            decorations={(ch, "length"): 0.2},
            number="X",
        )
        labels = [
            dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("m_chamfer")
        ]
        assert labels == ["C12 ±0.2"], labels

    def test_untolerated_chamfer_label_unchanged(self):
        # No decoration → the planner path is byte-identical to the old raw-field label.
        ch = chamfer(axis="z", leg=12, at=(39, 24, 0))
        dwg = build_drawing(self._chamfered_plate(), model=[ch], number="X")
        labels = [
            dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("m_chamfer")
        ]
        assert labels == ["C12"], labels

    def test_renderer_displays_the_planned_value_not_the_raw_field(self):
        # #724 review: the renderer must be planner-AUTHORITATIVE — the displayed leg is
        # pd.param.value, and the dim is bound by (role, kind), never dims[0]. Feed
        # render_chamfers a hand-built group whose planned value deliberately differs
        # from the feature's raw leg1 (and carries a decoy first dim) and assert the
        # label shows the planned value. This is the seam the remaining #698 kind
        # migrations copy.
        from dataclasses import replace

        from draftwright.annotations._common import PlacementContext
        from draftwright.annotations.from_model import render_chamfers

        ch = chamfer(axis="z", leg=12, at=(39, 24, 0))
        dwg = build_drawing(self._chamfered_plate(), model=[ch], number="X", auto_dims=False)
        (g,) = [g for g in plan_dimensions(dwg.model()) if g.feature_kind == "chamfer"]
        (pd,) = g.dims
        decoy = replace(pd, param=replace(pd.param, role="decoy", value=99.0))
        planned = replace(pd, param=replace(pd.param, value=7.0))  # ≠ ch.leg1 == 12
        g2 = replace(g, dims=(decoy, planned))
        ctx = PlacementContext(registry=dwg.registry, coverage=dwg.coverage, items=dwg.items)
        assert render_chamfers(dwg, [g2], dwg._analysis, ctx=ctx) == 1
        labels = [
            dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("m_chamfer")
        ]
        assert labels == ["7 × 45°"], labels  # planned 7 ≠ leg2 12 → leg×angle form


class TestFilletTolerance:
    """#725 (the #629 class, latent): a fillet's authored tolerance must render on the
    placed ``R`` callout — the pass now consumes the planner's DimensionGroups. The
    equal-radius ``n×`` collapse stays render-side (#698: planner-side grouping out of
    scope); the displayed radius + tolerance come from the members' planned dims."""

    @staticmethod
    def _filleted_plate():
        plate = Box(90, 60, 20)
        e = plate.edges().filter_by(Axis.Z).sort_by(lambda e: e.center().X + e.center().Y)[-1]
        return b3d_fillet(e, 8)

    def test_authored_fillet_tolerance_renders_on_callout(self):
        # Declared model (ADR 0011): the caller holds the feature object, so the
        # decoration keys on it — (feature, "radius") for a fillet.
        fl = fillet(axis="z", radius=8, at=(41, 26, 0))  # on the rounded corner
        dwg = build_drawing(
            self._filleted_plate(),
            model=[fl],
            decorations={(fl, "radius"): 0.1},
            number="X",
        )
        labels = [
            dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("m_fillet")
        ]
        assert labels == ["R8 ±0.1"], labels

    def test_untolerated_fillet_label_unchanged(self):
        # No decoration → the planner path is byte-identical to the old raw-field label.
        fl = fillet(axis="z", radius=8, at=(41, 26, 0))
        dwg = build_drawing(self._filleted_plate(), model=[fl], number="X")
        labels = [
            dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("m_fillet")
        ]
        assert labels == ["R8"], labels

    def test_collapse_conflicting_tolerances_first_authored_wins(self):
        # #742 review: when equal-radius fillets with DIFFERENT authored tolerances share
        # one n×R callout, the FIRST-AUTHORED tolerance wins (the render_diameters
        # precedent) — never the spatially-first member, whose identity would change if
        # the geometry moved. Author the (+,+)-corner fillet first with ±0.1; the
        # (-,-)-corner one (which sorts spatially FIRST by frame.origin) second with
        # ±0.5. Spatial-first-wins would show ±0.5; authored-first shows ±0.1.
        plate = Box(90, 60, 20)
        es = plate.edges().filter_by(Axis.Z).sort_by(lambda e: e.center().X + e.center().Y)
        part = b3d_fillet([es[0], es[-1]], 8)
        f_pp = fillet(axis="z", radius=8, at=(41, 26, 0))  # authored first
        f_mm = fillet(axis="z", radius=8, at=(-41, -26, 0))  # spatially first
        dwg = build_drawing(
            part,
            model=[f_pp, f_mm],
            decorations={(f_pp, "radius"): 0.1, (f_mm, "radius"): 0.5},
            number="X",
        )
        labels = [
            dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("m_fillet")
        ]
        assert labels == ["2× R8 ±0.1"], labels


class TestFlatTolerance:
    """#726 (the #629 class, latent): a machined flat's authored across-flats tolerance
    must render on the placed A/F callout — the pass now consumes the planner's
    DimensionGroups. The suffix interleaves after the value (the tolerance rides the
    number, not the A/F qualifier)."""

    @staticmethod
    def _flatted_bar():
        # A D-shaft: Z round stock with one milled flat at x = 7 (across = 7 + 10 = 17).
        return Cylinder(10, 30) - Pos(12, 0, 0) * Box(10, 40, 40)

    def test_authored_flat_tolerance_renders_on_callout(self):
        fl = flat(axis="z", across=17, at=(7, 0, 0))  # the flat face centre
        dwg = build_drawing(
            self._flatted_bar(),
            model=[fl],
            decorations={(fl, "length"): 0.2},
            number="X",
        )
        labels = [dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("m_flat")]
        assert labels == ["17 ±0.2 A/F"], labels

    def test_untolerated_flat_label_unchanged(self):
        # No decoration → the planner path is byte-identical to the old raw-field label.
        fl = flat(axis="z", across=17, at=(7, 0, 0))
        dwg = build_drawing(self._flatted_bar(), model=[fl], number="X")
        labels = [dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("m_flat")]
        assert labels == ["17 A/F"], labels


class TestGrooveTolerance:
    """#727 (the #629 class, latent): a groove's authored width/floor-ø tolerances must
    render on the placed callout — the pass now consumes the planner's DimensionGroups,
    binding EACH of the two params explicitly by (role, kind). Each tolerance suffix
    interleaves after its own value."""

    @staticmethod
    def _grooved_shaft():
        # Z round stock with one annular groove at mid-height (floor ø16, 4 wide).
        return Cylinder(10, 40) - (Cylinder(10.5, 4) - Cylinder(8, 4))

    def test_authored_groove_tolerances_render_on_callout(self):
        gr = groove(axis="z", width=4, diameter=16, at=(0, 0, 0))
        dwg = build_drawing(
            self._grooved_shaft(),
            model=[gr],
            decorations={(gr, "length"): 0.1, (gr, "diameter"): 0.5},
            number="X",
        )
        labels = [
            dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("m_groove")
        ]
        assert labels == ["4 ±0.1 WIDE × ø16 ±0.5"], labels

    def test_untolerated_groove_label_unchanged(self):
        # No decoration → the planner path is byte-identical to the old raw-field label.
        gr = groove(axis="z", width=4, diameter=16, at=(0, 0, 0))
        dwg = build_drawing(self._grooved_shaft(), model=[gr], number="X")
        labels = [
            dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("m_groove")
        ]
        assert labels == ["4 WIDE × ø16"], labels

    def test_renderer_displays_the_planned_values_not_the_raw_fields(self):
        # #742 review — the multi-param decoy proof for groove (the #724/#728 shape): the
        # renderer must be planner-AUTHORITATIVE, binding width and floor ø each by
        # (role, kind), never positionally. A decoy first dim plus a planned width
        # deliberately different from the raw feature field must render the planned value.
        from dataclasses import replace

        from draftwright.annotations._common import PlacementContext
        from draftwright.annotations.from_model import render_grooves

        gr = groove(axis="z", width=4, diameter=16, at=(0, 0, 0))
        dwg = build_drawing(self._grooved_shaft(), model=[gr], number="X", auto_dims=False)
        (g,) = [g for g in plan_dimensions(dwg.model()) if g.feature_kind == "groove"]
        by_key = {(pd.param.role, pd.param.kind): pd for pd in g.dims}
        wpd = by_key[("groove", "length")]
        decoy = replace(wpd, param=replace(wpd.param, role="decoy", value=99.0))
        planned_w = replace(wpd, param=replace(wpd.param, value=7.0))  # ≠ gr.width == 4
        g2 = replace(g, dims=(decoy, planned_w, by_key[("groove", "diameter")]))
        ctx = PlacementContext(registry=dwg.registry, coverage=dwg.coverage, items=dwg.items)
        assert render_grooves(dwg, [g2], dwg._analysis, ctx=ctx) == 1
        labels = [
            dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("m_groove")
        ]
        assert labels == ["7 WIDE × ø16"], labels


class TestPocketTolerance:
    """#728 (the #629 class, latent): a pocket's authored tolerance must render on the
    placed W × L × D callout — the pass now consumes the planner's DimensionGroups,
    binding EACH of the three params explicitly by (role, kind). All three share kind
    "length", so one authored decoration suffixes all three values (see the planner
    test); the decoy test below proves the explicit multi-param binding."""

    @staticmethod
    def _pocketed_plate():
        # A blind 30 × 18 × 5 recess in the top face of a plate.
        return Box(90, 60, 20) - Pos(0, 0, 7.5) * Box(30, 18, 5)

    @staticmethod
    def _pocket_feature():
        return pocket(width=18, length=30, depth=5, long_axis="x", width_axis="y", lo=-15, hi=15)

    def test_authored_pocket_tolerance_renders_on_callout(self):
        pk = self._pocket_feature()
        dwg = build_drawing(
            self._pocketed_plate(),
            model=[pk],
            decorations={(pk, "length"): 0.2},
            number="X",
        )
        labels = [
            dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("m_pocket")
        ]
        assert labels == ["18 ±0.2 × 30 ±0.2 × 5 ±0.2 DEEP"], labels

    def test_untolerated_pocket_label_unchanged(self):
        # No decoration → the planner path is byte-identical to the old raw-field label.
        pk = self._pocket_feature()
        dwg = build_drawing(self._pocketed_plate(), model=[pk], number="X")
        labels = [
            dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("m_pocket")
        ]
        assert labels == ["18 × 30 × 5 DEEP"], labels

    def test_renderer_displays_the_planned_values_not_the_raw_fields(self):
        # #698 multi-param binding proof (the #724 decoy shape): the renderer must be
        # planner-AUTHORITATIVE — each displayed value is its pd.param.value, bound by
        # (role, kind), never positionally. Feed render_pockets a hand-built group with a
        # decoy first dim and a planned width deliberately different from the feature's
        # raw field, and assert the label shows the planned value in the width slot.
        from dataclasses import replace

        from draftwright.annotations._common import PlacementContext
        from draftwright.annotations.from_model import render_pockets

        pk = self._pocket_feature()
        dwg = build_drawing(self._pocketed_plate(), model=[pk], number="X", auto_dims=False)
        (g,) = [g for g in plan_dimensions(dwg.model()) if g.feature_kind == "pocket"]
        by_key = {(pd.param.role, pd.param.kind): pd for pd in g.dims}
        wpd = by_key[("pocket_width", "length")]
        decoy = replace(wpd, param=replace(wpd.param, role="decoy", value=99.0))
        planned_w = replace(wpd, param=replace(wpd.param, value=7.0))  # ≠ pk.width == 18
        g2 = replace(
            g,
            dims=(
                decoy,
                planned_w,
                by_key[("pocket_length", "length")],
                by_key[("pocket_depth", "length")],
            ),
        )
        ctx = PlacementContext(registry=dwg.registry, coverage=dwg.coverage, items=dwg.items)
        assert render_pockets(dwg, [g2], dwg._analysis, ctx=ctx) == 1
        labels = [
            dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("m_pocket")
        ]
        assert labels == ["7 × 30 × 5 DEEP"], labels


class TestPlateTolerance:
    """#729 (the #629 class, latent): a plate's authored thickness tolerance must render
    on the placed linear dim — the pass now consumes the planner's DimensionGroups,
    binding the dim explicitly by (role, kind) == ("thickness", "length"). Only the
    VALUE and TOLERANCE source changed; the strip/tier placement mechanics (including
    the allowlisted carve fallthrough) are untouched."""

    @staticmethod
    def _flat_plate():
        return Box(80, 50, 8)

    @staticmethod
    def _plate_feature():
        return plate(axis="z", lo=-4, hi=4, u=0, v=0)

    def test_authored_plate_tolerance_renders_on_dim(self):
        # Declared model (ADR 0011): detection's ≥2-axis scope guard doesn't apply, so a
        # single declared slab renders its thickness dim; the decoration keys on
        # (feature, "length").
        pl = self._plate_feature()
        dwg = build_drawing(
            self._flat_plate(),
            model=[pl],
            decorations={(pl, "length"): 0.1},
            number="X",
        )
        labels = [
            dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("dim_plate")
        ]
        assert labels == ["8 ±0.1"], labels

    def test_tolerance_survives_the_post_drain_carve_fallthrough(self):
        # #744 review: when the primary corridor candidate DROPS, the deferred carve
        # retry rebuilds the dim from its own captured label — the tolerance must ride
        # along, not be reconstructed from the raw value. Drive the fallthrough
        # deterministically: register via render_plates, fire the candidate's on_drop
        # (what the solve does on a full strip), then run the deferred post_drain
        # retries — the relocated dim must still read "8 ±0.1".
        from draftwright.annotations._common import PlacementContext
        from draftwright.annotations.from_model import render_plates

        pl = self._plate_feature()
        dwg = build_drawing(
            self._flat_plate(),
            model=[pl],
            decorations={(pl, "length"): 0.1},
            number="X",
            auto_dims=False,
        )
        (g,) = [g for g in plan_dimensions(dwg.model()) if g.feature_kind == "plate"]
        ctx = PlacementContext(registry=dwg.registry, coverage=dwg.coverage, items=dwg.items)
        assert render_plates(dwg, [g], dwg._analysis, ctx=ctx) == 1
        (cand,) = [
            c
            for b in ctx.corridor_batch.values()
            for c in b["cands"]
            if c.name.startswith("dim_plate")
        ]
        cand.on_drop(cand.name)  # the solve's full-strip signal
        for cb in ctx.post_drain:  # the deferred opposite-strip retries
            cb()
        labels = [
            dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("dim_plate")
        ]
        assert labels == ["8 ±0.1"], labels

    def test_untolerated_plate_label_unchanged(self):
        # No decoration → the planner path is byte-identical to the old raw-field label.
        pl = self._plate_feature()
        dwg = build_drawing(self._flat_plate(), model=[pl], number="X")
        labels = [
            dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("dim_plate")
        ]
        assert labels == ["8"], labels

    def test_renderer_displays_the_planned_value_not_the_raw_fields(self):
        # #698 binding proof (the #724 decoy shape): the renderer must be
        # planner-AUTHORITATIVE — the displayed thickness is pd.param.value, bound by
        # (role, kind), never dims[0]. Feed render_plates a hand-built group with a decoy
        # first dim and a planned value deliberately different from the feature's raw
        # hi - lo, and assert the placed dim shows the planned value. Unlike the leader
        # passes, render_plates registers corridor candidates, so the batch is drained
        # before reading the label.
        from dataclasses import replace

        from draftwright.annotations._common import PlacementContext, drain_corridors
        from draftwright.annotations.from_model import render_plates

        pl = self._plate_feature()
        dwg = build_drawing(self._flat_plate(), model=[pl], number="X", auto_dims=False)
        (g,) = [g for g in plan_dimensions(dwg.model()) if g.feature_kind == "plate"]
        (pd,) = g.dims
        decoy = replace(pd, param=replace(pd.param, role="decoy", value=99.0))
        planned = replace(pd, param=replace(pd.param, value=7.0))  # ≠ pl.hi - pl.lo == 8
        g2 = replace(g, dims=(decoy, planned))
        ctx = PlacementContext(registry=dwg.registry, coverage=dwg.coverage, items=dwg.items)
        assert render_plates(dwg, [g2], dwg._analysis, ctx=ctx) == 1
        drain_corridors(ctx, dwg)
        labels = [
            dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("dim_plate")
        ]
        assert labels == ["7"], labels


class TestSlotTolerance:
    """#730 (the #629 class, latent): a slot's authored width/length tolerances must
    render on the placed linear dims — the pass now consumes the planner's
    DimensionGroups, binding each dim explicitly by (role, kind) ==
    ("slot_width"/"slot_length", "length"). Only the VALUE and TOLERANCE source
    changed; the placement mechanics — corridor registration for the horizontal
    plan/side dims, immediate strip placement for the rest, the below-strip on_drop
    fallthrough, the position dedup key, and the model-derived datum position dim —
    are untouched. (Sheet.slot returns Sheet, not a tolerance handle, so the
    declared-model decorations path is the authoring surface, as for the siblings.)"""

    @staticmethod
    def _slotted_block():
        return Box(50, 30, 20) - Box(20, 8, 30)  # enclosed through-slot (#135)

    @staticmethod
    def _slot_feature():
        return slot(width=8, length=20, long_axis="x", width_axis="y", lo=-10, hi=10, w_center=0)

    def test_authored_slot_tolerance_renders_on_dims(self):
        # Declared model (ADR 0011); the decoration keys on (feature, "length"), which
        # BOTH slot params share — so one authored tolerance rides both the width dim
        # (immediate right/left placement) and the length dim (corridor-drained). The
        # model-derived datum position dim has no parameter, so it stays untoleranced.
        sl = self._slot_feature()
        dwg = build_drawing(
            self._slotted_block(),
            model=[sl],
            decorations={(sl, "length"): 0.1},
            number="X",
        )
        labels = {
            n: dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("m_slot")
        }
        assert labels == {
            "m_slot0_width": "8 ±0.1",
            "m_slot0_length": "20 ±0.1",
            "m_slot0_pos": "15",
        }, labels

    def test_untolerated_slot_labels_unchanged(self):
        # No decoration → the planner path is byte-identical to the old raw-field labels.
        sl = self._slot_feature()
        dwg = build_drawing(self._slotted_block(), model=[sl], number="X")
        labels = {
            n: dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("m_slot")
        }
        assert labels == {
            "m_slot0_width": "8",
            "m_slot0_length": "20",
            "m_slot0_pos": "15",
        }, labels

    def test_tolerance_survives_the_below_strip_fallthrough(self):
        # #744 review lesson, applied to the slot pass: when the corridor candidate
        # DROPS, _below_or_drop rebuilds the dim from its own captured label — the
        # tolerance must ride along, not be reconstructed from the raw field. Drive the
        # fallthrough deterministically: register via render_slots, then fire the length
        # candidate's on_drop (the solve's full-strip signal) — the relocated below-strip
        # dim must still read "20 ±0.1".
        from draftwright.annotations._common import PlacementContext
        from draftwright.annotations.from_model import render_slots

        sl = self._slot_feature()
        dwg = build_drawing(
            self._slotted_block(),
            model=[sl],
            decorations={(sl, "length"): 0.1},
            number="X",
            auto_dims=False,
        )
        ctx = PlacementContext(registry=dwg.registry, coverage=dwg.coverage, items=dwg.items)
        assert render_slots(dwg, plan_dimensions(dwg.model()), dwg._analysis, ctx=ctx) == 3
        (cand,) = [
            c
            for b in ctx.corridor_batch.values()
            for c in b["cands"]
            if c.name == "m_slot0_length"
        ]
        cand.on_drop(cand.name)  # the solve's full-strip signal
        assert dwg.get_annotation("m_slot0_length").label == "20 ±0.1"

    def test_renderer_displays_the_planned_values_not_the_raw_fields(self):
        # #698 binding proof (the #724 decoy shape): the renderer must be
        # planner-AUTHORITATIVE — each displayed value is its pd.param.value, bound by
        # (role, kind), never positionally. Feed render_slots a hand-built group with a
        # decoy first dim and planned width/length deliberately different from the raw
        # feature fields; the width places immediately, the length via the corridor
        # drain — both must show the planned value.
        from dataclasses import replace

        from draftwright.annotations._common import PlacementContext, drain_corridors
        from draftwright.annotations.from_model import render_slots

        sl = self._slot_feature()
        dwg = build_drawing(self._slotted_block(), model=[sl], number="X", auto_dims=False)
        (g,) = [g for g in plan_dimensions(dwg.model()) if g.feature_kind == "slot"]
        by_key = {(pd.param.role, pd.param.kind): pd for pd in g.dims}
        wpd = by_key[("slot_width", "length")]
        lpd = by_key[("slot_length", "length")]
        decoy = replace(wpd, param=replace(wpd.param, role="decoy", value=99.0))
        planned_w = replace(wpd, param=replace(wpd.param, value=7.0))  # ≠ sl.width == 8
        planned_l = replace(lpd, param=replace(lpd.param, value=19.0))  # ≠ sl.length == 20
        g2 = replace(g, dims=(decoy, planned_w, planned_l))
        ctx = PlacementContext(registry=dwg.registry, coverage=dwg.coverage, items=dwg.items)
        assert render_slots(dwg, [g2], dwg._analysis, ctx=ctx) == 3
        drain_corridors(ctx, dwg)
        assert dwg.get_annotation("m_slot0_width").label == "7"
        assert dwg.get_annotation("m_slot0_length").label == "19"


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
