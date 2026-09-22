"""Automatic detail-view selection, placement, and annotation behavior."""

import pytest
from _parts import crowded_shoulder_part as _crowded_shoulder_part
from build123d import Box, Cylinder, Pos

from draftwright import Sheet, build_drawing
from draftwright._core import _fmt


@pytest.mark.timeout(120)
class TestDetailView:
    @staticmethod
    def _sectioned_crowded_shoulders():
        return _crowded_shoulder_part() - Pos(0, 0, 12) * Cylinder(2.5, 20)

    @pytest.mark.parametrize(
        ("override", "expected"), [({}, True), ({"detail_view": False}, False)]
    )
    def test_make_drawing_forwards_default_and_explicit_opt_out(
        self, monkeypatch, override, expected
    ):
        import draftwright.builder as builder

        forwarded = []

        class _Drawing:
            def export(self, *, formats):
                assert formats == ("svg", "dxf")
                return {"svg": "out.svg", "dxf": "out.dxf"}

        def _build(*args, **kwargs):
            forwarded.append(kwargs["detail_view"])
            return _Drawing()

        monkeypatch.setattr(builder, "build_drawing", _build)
        assert builder.make_drawing("part.step", **override) == ("out.svg", "out.dxf")
        assert forwarded == [expected]

    def test_detail_view_can_be_disabled_explicitly(self):
        dwg = build_drawing(_crowded_shoulder_part(), detail_view=False)
        assert "detail_a" not in dwg.views
        assert "detail_caption" not in dwg.annotations()
        assert not any(n.startswith("dim_detail") for n in dwg.annotations())

    def test_non_finite_requested_scale_is_rejected_before_geometry_work(self):
        from types import SimpleNamespace

        from draftwright._core import DetailRequest
        from draftwright.annotations.sections import _render_detail

        req = DetailRequest(axis="z", lo=0.0, hi=1.0, scale_needed=float("inf"), redraw=lambda: 1)

        assert not _render_detail(
            None,
            SimpleNamespace(SCALE=1.0),
            req,
            "detail_a",
            "A",
            ctx=None,
        )

    def test_crowded_shoulders_get_a_detail_view_automatically(self):
        from draftwright._core import _legible_steps

        dwg = build_drawing(_crowded_shoulder_part())
        a = dwg._analysis
        # Pin the trigger: the gate must actually drop at least one shoulder at
        # the chosen scale, otherwise the test is not exercising #42.
        _, n_dropped = _legible_steps(a.step_zs, a.bb.min.Z, a.SCALE)
        assert n_dropped >= 1
        # The detail view, its caption, and at least one detail step dim exist.
        assert "detail_a" in dwg.views
        assert "detail_caption_A" in dwg.annotations()
        assert any(n.startswith("dim_detail_a_step") for n in dwg.annotations())
        # Drawn at a larger scale than the sheet.
        assert dwg.coords("detail_a")._scale > a.SCALE
        # The detail resolves against the sheet-scale iso. That orientation aid must not then
        # grow into the defining detail after placement (#915).
        assert dwg.coords("iso")._scale == pytest.approx(a.SCALE)
        # Absolute step heights use the part base as their datum. The detail
        # must include that datum so neither witness endpoint floats outside
        # the visible crop.
        _, detail_y0, _, detail_y1 = dwg.view_bounds("detail_a")
        detail_dims = (
            dwg.get_annotation(n) for n in dwg.annotations() if n.startswith("dim_detail_a_step")
        )
        assert all(
            detail_y0 - 1e-6 <= point[1] <= detail_y1 + 1e-6
            for dim in detail_dims
            for point in (dim._dw_spec.p1, dim._dw_spec.p2)
        )
        # Crop context and source marker are separate: including the base datum
        # must not make the marker claim the whole part as the crowded region.
        marker = dwg.get_annotation("detail_marker_A").bounding_box()
        source_base_y = dwg.at("front", a.cx, a.cy, a.bb.min.Z)[1]
        assert not marker.min.Y <= source_base_y <= marker.max.Y
        # No error-severity lint introduced.
        assert [i for i in dwg.lint() if i.severity == "error"] == []

    def test_automatic_section_and_detail_share_one_identifier_sequence(self):
        drawing = build_drawing(
            self._sectioned_crowded_shoulders(),
            page="A2",
            scale=2,
            title="T",
            number="N",
        )

        assert {name for name in drawing.views if name.startswith(("section_", "detail_"))} == {
            "section_aa",
            "detail_b",
        }
        assert drawing.get_annotation("section_caption").label == "SECTION A–A"
        assert drawing.get_annotation("detail_caption_B").label.startswith("DETAIL B —")
        assert not [
            issue for issue in drawing.lint() if issue.code == "derived_view_identifier_reused"
        ]

    def test_authored_detail_keeps_its_label_before_automatic_derived_views(self):
        sheet = Sheet.from_part(self._sectioned_crowded_shoulders(), page="A2", scale=2).take_over(
            dimensions="automatic",
            principal_views="automatic",
            derived_views="automatic",
        )
        hole = next(feature for feature in sheet.features if feature.kind == "hole")
        sheet.add_detail_view("A", hole).scale(2)

        drawing = sheet.build()

        assert {name for name in drawing.views if name.startswith(("section_", "detail_"))} == {
            "detail_a",
            "section_bb",
            "detail_c",
        }
        assert drawing.get_annotation("detail_caption_A").label.startswith("DETAIL A —")
        assert drawing.get_annotation("section_b_caption").label == "SECTION B–B"
        assert drawing.get_annotation("detail_caption_C").label.startswith("DETAIL C —")

    def test_authored_section_at_the_inferred_cut_satisfies_the_automatic_view(self):
        part = Box(60, 40, 20) - Cylinder(4, 30) - Pos(0, 0, 2) * Cylinder(7, 20)
        sheet = Sheet.from_part(part).take_over(
            dimensions="automatic",
            principal_views="automatic",
            derived_views="automatic",
        )
        sheet.add_section_view("A", at=0)

        drawing = sheet.build()

        assert [name for name in drawing.views if name.startswith("section_")] == ["section_aa"]
        assert drawing.get_annotation("section_caption").label == "SECTION A–A"

    def test_prismatic_detail_gates_on_the_step_escalation_not_raw_legibility(self):
        # #351 PR-4b: _request_prismatic_detail previously recomputed the legibility
        # gate straight from a.step_zs as its own trigger — independent of whether
        # render_height_ladder actually dropped anything. A uniform staircase
        # (_detect_step_repeat) collapses to ONE representative dim with no drop at
        # all even when the raw z-list would look "illegible" in isolation, so the old
        # trigger could queue a spurious, unused detail view. Now it gates on the
        # "step"/"illegible" Escalation render_height_ladder emits instead.
        from types import SimpleNamespace

        from draftwright.annotations._common import Escalation, PlacementContext
        from draftwright.annotations.sections import _request_prismatic_detail
        from draftwright.model.compiled import (
            ApprovedDimension,
            ApprovedLadder,
            RenderableDimensionPlan,
        )

        a = SimpleNamespace(
            step_zs=[1.0, 1.1, 1.2, 1.3],  # tightly spaced — "illegible" if recomputed raw
            bb=SimpleNamespace(min=SimpleNamespace(Z=0.0), max=SimpleNamespace(Z=2.0)),
            SCALE=1.0,
        )
        # The rungs now arrive from the compiled plan (ADR 4 (was 0016 Amdt 1) / #923): the detail
        # redraw used to re-derive them from `dwg.model()` and `a.step_zs`, which let it
        # draw a rung the compiler had withheld. `a.step_zs` is no longer consulted at all —
        # what this test still pins is the GATE: the escalation, not raw legibility.
        plan = RenderableDimensionPlan(
            ladders=(
                ApprovedLadder(
                    "step_height",
                    tuple(
                        ApprovedDimension(
                            id=None,
                            value_text=str(z),
                            value=z,
                            span=((0.0, 0.0, 0.0), (0.0, 0.0, z)),
                            rendered_label=str(z),
                        )
                        for z in (1.0, 1.1, 1.2, 1.3)
                    ),
                ),
            )
        )
        no_escalation = PlacementContext()
        _request_prismatic_detail(None, a, ctx=no_escalation, plan=plan)
        assert no_escalation.detail_requests == []

        ladder = plan.ladder("step_height")
        assert ladder is not None
        empty_escalation = PlacementContext(
            escalations=[Escalation(kind="step", view="front", feature=None, reason="illegible")]
        )
        _request_prismatic_detail(None, a, ctx=empty_escalation, plan=plan)
        assert empty_escalation.detail_requests == []

        with_escalation = PlacementContext(
            escalations=[
                Escalation(
                    kind="step",
                    view="front",
                    feature=None,
                    reason="illegible",
                    targets=(ladder.rungs[-1],),
                )
            ],
        )
        _request_prismatic_detail(None, a, ctx=with_escalation, plan=plan)
        assert len(with_escalation.detail_requests) == 1

    def test_declared_step_base_does_not_replace_physical_detail_crop_base(self):
        """Measurement datum and visible crop geometry are intentionally different."""
        from types import SimpleNamespace

        from draftwright.annotations._common import Escalation, PlacementContext
        from draftwright.annotations.sections import _request_prismatic_detail
        from draftwright.model.compiled import (
            ApprovedDimension,
            ApprovedLadder,
            RenderableDimensionPlan,
        )

        bbox_base = -15.0
        declared_base = 5.0
        rungs = tuple(
            ApprovedDimension(
                id=None,
                value_text=str(z - declared_base),
                value=z - declared_base,
                span=((0.0, 0.0, declared_base), (0.0, 0.0, z)),
                rendered_label=str(z - declared_base),
            )
            for z in (8.0, 9.0, 10.0)
        )
        plan = RenderableDimensionPlan(ladders=(ApprovedLadder("step_height", rungs),))
        analysis = SimpleNamespace(
            bb=SimpleNamespace(
                min=SimpleNamespace(Z=bbox_base),
                max=SimpleNamespace(Z=20.0),
            ),
            SCALE=1.0,
        )
        ctx = PlacementContext(
            escalations=[
                Escalation(
                    kind="step",
                    view="front",
                    feature=None,
                    reason="illegible",
                    targets=(rungs[-1],),
                )
            ]
        )

        _request_prismatic_detail(None, analysis, ctx=ctx, plan=plan)

        assert len(ctx.detail_requests) == 1
        assert ctx.detail_requests[0].crop_lo == bbox_base
        assert ctx.detail_requests[0].crop_lo != declared_base

    def test_partial_level_support_does_not_crop_away_an_unwitnessed_rung(self):
        """Correspondence cropping is all-or-nothing: one fallback rung keeps full context."""
        from types import SimpleNamespace

        from draftwright.annotations._common import Escalation, PlacementContext
        from draftwright.annotations.sections import _request_prismatic_detail
        from draftwright.model.compiled import (
            ApprovedDimension,
            ApprovedLadder,
            RenderableDimensionPlan,
        )

        rungs = tuple(
            ApprovedDimension(
                id=None,
                value_text=str(z),
                value=z,
                span=((x, 0.0, 0.0), (x, 0.0, z)),
                rendered_label=str(z),
                support_bounds=(-20.0, -10.0, x, 10.0) if z < 3.0 else None,
            )
            for z, x in ((1.0, 10.0), (2.0, 20.0), (3.0, 50.0))
        )
        plan = RenderableDimensionPlan(ladders=(ApprovedLadder("step_height", rungs),))
        analysis = SimpleNamespace(
            bb=SimpleNamespace(
                min=SimpleNamespace(X=-50.0, Z=0.0),
                max=SimpleNamespace(X=50.0, Z=4.0),
            ),
            SCALE=1.0,
        )
        ctx = PlacementContext(
            escalations=[
                Escalation(
                    kind="step",
                    view="front",
                    feature=None,
                    reason="illegible",
                    targets=rungs,
                )
            ]
        )

        _request_prismatic_detail(None, analysis, ctx=ctx, plan=plan)

        assert len(ctx.detail_requests) == 1
        request = ctx.detail_requests[0]
        assert (request.cross_axis, request.cross_lo, request.cross_hi) == (None, None, None)

    def test_face_support_recovers_a_shelled_covers_crowded_levels(self):
        # The two levels span most of the cover, but each has a real right-edge witness
        # station. Retaining that correspondence makes a narrow wall detail truthful and
        # avoids preserving the old full-envelope `detail_unplaceable` fallback (#915).
        cover = (
            Box(90, 64, 38)
            - Pos(0, 0, -3) * Box(84, 58, 38)
            + Pos(0, 0, 24) * Cylinder(14, 10)
            - Pos(0, 0, 15) * Cylinder(6, 40)
        )
        off = build_drawing(cover, title="Cover", detail_view=False)
        on = build_drawing(cover, title="Cover", detail_view=True)
        assert off.lint_summary()["by_code"].get("detail_unplaceable", 0) == 0
        assert on.lint_summary()["by_code"].get("detail_unplaceable", 0) == 0
        assert "detail_a" in on.views
        assert [
            annotation.label
            for name, annotation in on.iter_annotations()
            if name.startswith("dim_detail_a_step")
        ] == ["38"]

    def test_plain_part_gets_no_detail_view(self, shared_drawing):
        dwg = shared_drawing("box_60x40x20")
        assert "detail_a" not in dwg.views
        assert "detail_caption" not in dwg.annotations()
        assert not any(n.startswith("dim_detail") for n in dwg.annotations())
        assert [i for i in dwg.lint() if i.severity == "error"] == []

    def test_finalize_places_and_rolls_back_a_detail_view(self, monkeypatch):
        # #661 + #647: the finalize drain queues + resolves detail requests like the
        # auto pass (gated on the persisted detail-view setting). A raise in a LATER
        # stage (tabulate) must roll a placed detail view back — views, coordinates,
        # and its annotations alike — so a retry starts clean and places it once.
        from draftwright.annotations import orchestrator as _orch

        dwg = build_drawing(_crowded_shoulder_part(), auto_dims=False, detail_view=True)
        step = next(f for f in dwg.model().features if f.kind == "step_level")
        dwg._defer_intents = True
        dwg.dimension(step, "length", role="step_height")

        real = _orch._maybe_tabulate_holes
        calls = {"n": 0}

        def _boom(*a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("injected tabulate failure")
            return real(*a, **k)

        monkeypatch.setattr(_orch, "_maybe_tabulate_holes", _boom)
        with pytest.raises(RuntimeError):
            dwg.finalize()  # the details stage placed detail_a; tabulate then raises
        # Rolled back: no detail view, view coordinates, or detail annotations survive.
        assert "detail_a" not in dwg.views
        with pytest.raises(KeyError):
            dwg.coords("detail_a")
        assert not any(n.startswith(("detail_", "dim_detail_")) for n in dwg.annotations())
        assert any(it.kwargs.get("role") == "step_height" for it in dwg._intents)

        dwg.finalize()  # retry from the restored intents — the detail places exactly once
        assert "detail_a" in dwg.views
        assert "detail_caption_A" in dwg.annotations()
        assert any(n.startswith("dim_detail_a_step") for n in dwg.annotations())

    @staticmethod
    def _deferred_height_build(pin):
        """A finalize-path build whose crowded-step detail must fight the explicit
        envelope-height dimension for room (the #661 demotion scenario)."""
        dwg = build_drawing(_crowded_shoulder_part(), auto_dims=False, detail_view=True)
        step = next(f for f in dwg.model().features if f.kind == "step_level")
        env = next(f for f in dwg.model().features if f.kind == "envelope")
        dwg._defer_intents = True
        dwg.dimension(step, "length", role="step_height")
        dwg.dimension(env, "length", role="height", pin=pin)
        dwg.finalize()
        return dwg

    def test_detail_demotion_never_touches_a_pinned_height_dim(self):
        # The detail owns only the crowded intermediate heights; the main view
        # retains the overall height. Therefore the detail no longer needs to
        # demote that height to make room, pinned or otherwise (#897).
        pinned = self._deferred_height_build(pin=True)
        assert "dim_length0" in pinned.annotations()
        assert pinned.registry.is_pinned("dim_length0")
        assert "detail_a" in pinned.views

        unpinned = self._deferred_height_build(pin=False)
        assert "dim_length0" in unpinned.annotations()
        assert "detail_a" in unpinned.views

    def test_finalize_rolls_back_a_raise_between_iso_reproject_and_refit(self, monkeypatch):
        # Codex review of #661: the details stage re-projects the iso at sheet scale,
        # resolves the queue, then refits the iso. A raise INSIDE that window (here:
        # the refit itself, as the finalize path resolves it from the projection
        # module per call) must roll the whole #647 transaction back — the fitted
        # iso, the placed detail view/coords, its annotations, and the intents.
        from draftwright import projection as _proj

        dwg = build_drawing(_crowded_shoulder_part(), auto_dims=False, detail_view=True)
        step = next(f for f in dwg.model().features if f.kind == "step_level")
        dwg._defer_intents = True
        dwg.dimension(step, "length", role="step_height")
        iso_before = dwg.views["iso"]
        names_before = set(dwg.annotations())
        intents_before = len(dwg._intents)

        real = _proj._fit_iso_view
        calls = {"n": 0}

        def _boom(*a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("injected iso refit failure")
            return real(*a, **k)

        monkeypatch.setattr(_proj, "_fit_iso_view", _boom)
        with pytest.raises(RuntimeError):
            dwg.finalize()  # iso re-projected + detail placed, then the refit raises
        # Full rollback: the build's fitted iso is back (the exact snapshot objects),
        # the detail view/coords/annotations are gone, and the intents survive.
        assert dwg.views["iso"] is iso_before
        assert "detail_a" not in dwg.views
        with pytest.raises(KeyError):
            dwg.coords("detail_a")
        assert set(dwg.annotations()) == names_before
        assert len(dwg._intents) == intents_before

        dwg.finalize()  # clean retry: the refit runs and the detail places
        assert calls["n"] == 2
        assert "detail_a" in dwg.views
        assert "detail_caption_A" in dwg.annotations()

    def test_overall_height_name_guards_the_canonical_dim_height(self):
        # user review of #661: the canonical `dim_height` fast path must obey the
        # SAME demotion-safety guards as the generalised dim_length{n} path. Before
        # this fix it returned "dim_height" unconditionally, so a pinned — or
        # user-replaced — canonical height was still handed to the demotion retry and
        # removed. (The existing demotion test only exercises the dim_length0 path.)
        from draftwright.annotations.sections import _overall_height_name

        dwg = build_drawing(_crowded_shoulder_part())  # no detail -> dim_height stays
        a = dwg._analysis
        assert "dim_height" in dwg.annotations()
        assert _overall_height_name(dwg, a) == "dim_height"  # canonical, unpinned, correct

        dwg.pin("dim_height")
        assert _overall_height_name(dwg, a) is None  # a pin is never demoted (ADR 2 (was 0012))
        dwg.unpin("dim_height")
        assert _overall_height_name(dwg, a) == "dim_height"  # unpin restores it

        # A user replacement under the canonical name whose label is no longer the
        # part height fails the identity guard -> not a demotion target.
        replacement = next(
            dwg.registry.named(n)
            for n in dwg.annotations()
            if n != "dim_height"
            and getattr(dwg.registry.named(n), "label", None) not in (None, _fmt(a.z_size))
        )
        dwg._add(replacement, "dim_height", view="front")
        assert _overall_height_name(dwg, a) is None

    def test_detail_does_not_demote_height_dim_or_lose_provenance(self, monkeypatch):
        # #897: the detail no longer redraws the overall height, so it must place
        # in one pass without removing/re-adding the main height dimension.
        from draftwright.annotations import sections as _sec

        dwg = build_drawing(_crowded_shoulder_part(), auto_dims=False, detail_view=True)
        step = next(f for f in dwg.model().features if f.kind == "step_level")
        env = next(f for f in dwg.model().features if f.kind == "envelope")
        dwg._defer_intents = True
        dwg.dimension(step, "length", role="step_height")
        dwg.dimension(env, "length", role="height")  # unpinned -> demotion fires

        real = _sec._render_detail
        calls = {"n": 0}

        def _count(*args, **kwargs):
            calls["n"] += 1
            return real(*args, **kwargs)

        monkeypatch.setattr(_sec, "_render_detail", _count)
        dwg.finalize()

        assert calls["n"] == 1
        assert "detail_a" in dwg.views
        assert "dim_length0" in dwg.annotations()
        assert dwg.registry.feature_of("dim_length0") == env
        assert "dim_length0" in dwg.annotations_of(env)
