"""Compose-then-pack layout and repack behavior."""

import logging

import pytest
from _kernel import B123D_GE_011, SKIP_011
from build123d import Box
from build123d_drafting import Leader

from draftwright import build_drawing
from draftwright._core import _MARGIN
from draftwright.compose import StripDepths

_skip_011 = pytest.mark.skipif(B123D_GE_011, reason=SKIP_011)


class TestComposeThenPackRepack:
    """Ownership-based compose-then-pack + measure-and-repack (#121, ADR 2 (was 0004)):
    the cross-view collision detector, the disjoint block packing, the candidate
    floor, and the annotation-ownership map lifecycle.  Pure/fast — no OCP."""

    # --- cross-view collision detector -----------------------------------

    @staticmethod
    def _label(bb):
        from types import SimpleNamespace

        return SimpleNamespace(label_bbox=bb)

    @staticmethod
    def _line(bb):
        # Bare geometry: no label_bbox attribute, bbox via bounding_box().
        from types import SimpleNamespace

        class _Bare:
            def bounding_box(self):
                return SimpleNamespace(
                    min=SimpleNamespace(X=bb[0], Y=bb[1]),
                    max=SimpleNamespace(X=bb[2], Y=bb[3]),
                )

        return _Bare()

    def _fake_dwg(self, named, views):
        from types import SimpleNamespace

        # Mirror Drawing's annotation read surface (#249) so stage helpers that now
        # call dwg.iter_annotations()/view_of() work against the fake.
        return SimpleNamespace(
            _named=named,
            _anno_view=views,
            box_cache={},
            iter_annotations=lambda: named.items(),
            get_annotation=lambda n: named.get(n),
            view_of=lambda n: views.get(n),
            annotations_in_view=lambda v: ((n, o) for n, o in named.items() if views.get(n) == v),
        )

    def test_overlap_counts_label_vs_label_across_views(self):
        from draftwright.builder import _cross_view_overlaps

        dwg = self._fake_dwg(
            {"a": self._label((0, 0, 10, 10)), "b": self._label((5, 5, 15, 15))},
            {"a": "front", "b": "plan"},
        )
        assert _cross_view_overlaps(dwg, None) == 1

    def test_overlap_counts_label_vs_line_across_views(self):
        # The literal #121 case: a plan balloon (bare geometry) over a front-view
        # dimension (label) — counted because at least one side is a label.
        from draftwright.builder import _cross_view_overlaps

        dwg = self._fake_dwg(
            {"dim": self._label((0, 0, 10, 10)), "balloon": self._line((5, 5, 15, 15))},
            {"dim": "front", "balloon": "plan"},
        )
        assert _cross_view_overlaps(dwg, None) == 1

    def test_overlap_ignores_same_view(self):
        from draftwright.builder import _cross_view_overlaps

        dwg = self._fake_dwg(
            {"a": self._label((0, 0, 10, 10)), "b": self._label((5, 5, 15, 15))},
            {"a": "front", "b": "front"},
        )
        assert _cross_view_overlaps(dwg, None) == 0

    def test_overlap_ignores_line_vs_line(self):
        # Two bare lines crossing between views is normal drafting, not a clash.
        from draftwright.builder import _cross_view_overlaps

        dwg = self._fake_dwg(
            {"a": self._line((0, 0, 10, 10)), "b": self._line((5, 5, 15, 15))},
            {"a": "front", "b": "side"},
        )
        assert _cross_view_overlaps(dwg, None) == 0

    def test_overlap_ignores_untagged_furniture(self):
        # An annotation with no ortho-view tag (iso/section/detail/title) is
        # invisible to the detector, even when it overlaps a tagged one.
        from draftwright.builder import _cross_view_overlaps

        dwg = self._fake_dwg(
            {"dim": self._label((0, 0, 10, 10)), "note": self._label((5, 5, 15, 15))},
            {"dim": "front", "note": "iso"},
        )
        assert _cross_view_overlaps(dwg, None) == 0

    def test_nearby_cross_view_labels_reserve_external_text_padding(self):
        from draftwright.builder import _cross_view_overlaps

        # Exact glyph boxes do not overlap, but each label owns the default 2 mm external
        # padding.  A 3 mm gap is therefore not a clear inter-view corridor.
        dwg = self._fake_dwg(
            {"a": self._label((0, 0, 10, 10)), "b": self._label((13, 0, 23, 10))},
            {"a": "front", "b": "side"},
        )
        assert _cross_view_overlaps(dwg, None) == 1

    def test_cross_view_padding_is_not_accumulated_across_comparisons(self):
        from draftwright.builder import _cross_view_overlaps

        # Every pair has a 5 mm glyph gap and therefore clears two 2 mm label bands. The
        # front box participates in both pairs; mutating it in the inner loop inflated it a
        # second time and made the result depend on annotation iteration order.
        dwg = self._fake_dwg(
            {
                "front": self._label((0, 0, 10, 10)),
                "side": self._label((15, 0, 25, 10)),
                "plan": self._label((0, 15, 10, 25)),
            },
            {"front": "front", "side": "side", "plan": "plan"},
        )
        assert _cross_view_overlaps(dwg, None) == 0

    # --- annotation-over-view-linework trigger (#293) ---------------------

    def test_annotation_view_overlap_counts_label_over_other_view(self):
        # The third repack trigger: a view-owned LABEL grown into a *different*
        # view's geometry box (the staggered step chain bumping the plan view).
        # A bare line over another view is normal drafting; a label in its OWN
        # view is fine. Only a label over another view's box counts.
        from types import SimpleNamespace

        from draftwright.builder import _annotation_view_overlaps

        a = SimpleNamespace(
            FV_X=0.0,
            FV_Y=0.0,
            fv_hw=10.0,
            fv_hh=10.0,
            PV_X=0.0,
            PV_Y=40.0,
            pv_hh=10.0,
            SV_X=40.0,
            SV_Y=0.0,
            sv_hw=10.0,
        )  # plan box spans x[-10,10] y[30,50]
        over = self._fake_dwg({"d": self._label((-5, 32, 5, 42))}, {"d": "front"})
        assert _annotation_view_overlaps(over, a) == 1  # front label inside plan box
        bare = self._fake_dwg({"d": self._line((-5, 32, 5, 42))}, {"d": "front"})
        assert _annotation_view_overlaps(bare, a) == 0  # bare line — normal drafting
        own = self._fake_dwg({"d": self._label((-5, -5, 5, 5))}, {"d": "front"})
        assert _annotation_view_overlaps(own, a) == 0  # inside its own view

    def test_annotation_view_clearance_triggers_before_literal_overlap(self):
        from types import SimpleNamespace

        from draftwright.builder import _annotation_view_overlaps

        a = SimpleNamespace(
            FV_X=0.0,
            FV_Y=0.0,
            fv_hw=10.0,
            fv_hh=10.0,
            PV_X=0.0,
            PV_Y=40.0,
            pv_hh=10.0,
            SV_X=40.0,
            SV_Y=0.0,
            sv_hw=10.0,
        )
        # Side view begins at x=30.  The front-owned label stops at 29, leaving 1 mm:
        # non-overlapping geometry, but less than the default 2 mm text clearance.
        near = self._fake_dwg({"d": self._label((20, -2, 29, 2))}, {"d": "front"})
        assert _annotation_view_overlaps(near, a) == 1

    def test_measured_label_band_carries_external_text_padding(self):
        from types import SimpleNamespace

        from draftwright.builder import _measure_blocks

        a = SimpleNamespace(
            FV_X=0.0,
            FV_Y=0.0,
            fv_hw=10.0,
            fv_hh=10.0,
            PV_X=0.0,
            PV_Y=40.0,
            pv_hh=10.0,
            SV_X=40.0,
            SV_Y=0.0,
            sv_hw=10.0,
        )
        dwg = self._fake_dwg({"d": self._label((20, -2, 29, 2))}, {"d": "front"})
        blocks = _measure_blocks(dwg, a)
        assert blocks["front"].right == pytest.approx(21.0)

    # --- out-of-bounds escalation trigger (#92) ---------------------------

    @pytest.mark.parametrize("overflow", [(20, 20, 40, 120), (9.95, 20, 40, 40)])
    def test_out_of_bounds_trigger(self, overflow):
        # The second repack trigger: a view-owned annotation past the drawable
        # (e.g. a ballooned plan view overflowing the page top) escalates even
        # without a cross-view overlap. Untagged overflow is ignored — a repack
        # can only move view-owned annotations.
        from types import SimpleNamespace

        from draftwright.builder import _annotations_out_of_bounds

        a = SimpleNamespace(margin=10.0, PAGE_W=200.0, PAGE_H=100.0)
        inb = self._fake_dwg({"d": self._line((20, 20, 40, 40))}, {"d": "plan"})
        assert not _annotations_out_of_bounds(inb, a)
        over = self._fake_dwg({"d": self._line(overflow)}, {"d": "plan"})
        assert _annotations_out_of_bounds(over, a)
        untagged = self._fake_dwg({"d": self._line(overflow)}, {"d": "iso"})
        assert not _annotations_out_of_bounds(untagged, a)

    def test_block_measurement_and_overflow_share_the_drawing_box_memo(self):
        from types import SimpleNamespace

        from draftwright.builder import _annotations_out_of_bounds, _measure_blocks

        class CountedLine:
            label_bbox = None

            def __init__(self):
                self.calls = 0

            def bounding_box(self):
                self.calls += 1
                return SimpleNamespace(
                    min=SimpleNamespace(X=20.0, Y=20.0),
                    max=SimpleNamespace(X=25.0, Y=25.0),
                )

        line = CountedLine()
        drawing = self._fake_dwg({"d": line}, {"d": "front"})
        analysis = SimpleNamespace(
            FV_X=0.0,
            FV_Y=0.0,
            fv_hw=10.0,
            fv_hh=10.0,
            PV_X=0.0,
            PV_Y=40.0,
            pv_hh=10.0,
            SV_X=40.0,
            SV_Y=0.0,
            sv_hw=10.0,
            margin=10.0,
            PAGE_W=200.0,
            PAGE_H=100.0,
        )

        _measure_blocks(drawing, analysis)
        calls_after_measure = line.calls
        assert not _annotations_out_of_bounds(drawing, analysis)
        assert line.calls == calls_after_measure

    # --- disjoint block packing ------------------------------------------

    def test_repacked_blocks_are_disjoint(self):
        from draftwright.compose import ViewBlock, _layout_geometry

        blocks = {
            "front": ViewBlock(10, 10, top=12, right=12, bottom=12, left=12),
            "plan": ViewBlock(10, 10, top=12, right=12, bottom=12, left=12),
            "side": ViewBlock(10, 10, top=12, right=12, bottom=12, left=12),
        }
        g = _layout_geometry(20, 20, 20, 1.0, 841.0, 594.0, 150.0, None, blocks=blocks)
        # FV and PV share X and stack vertically — PV's bottom must clear FV's top.
        assert (g.PV_Y - g.pv_hh) > (g.FV_Y + g.fv_hh)
        # SV abuts the column to the right — its left edge must clear FV's right.
        assert (g.SV_X - g.sv_hw) > (g.FV_X + g.fv_hw)

    def test_left_corridor_uses_shared_band_not_front_only(self):
        # The MAJOR fix: when the plan view's measured left band is the deeper of
        # the two, the FV/PV column must clear it (col_left), or PV slides off the
        # left margin.  front.left tiny, plan.left huge.
        #
        # The page is sized so the content FILLS it (x_offset == 0): on a wide
        # sheet the centring slack would absorb the mis-anchoring and the buggy
        # code (FV_X anchored on fv.left) would pass anyway.  With x_offset == 0
        # the bug puts the plan-view left edge at margin - 120 = -110 mm.
        from draftwright.compose import ViewBlock, _layout_geometry

        blocks = {
            "front": ViewBlock(10, 10, left=0.0, right=8, top=8, bottom=8),
            "plan": ViewBlock(10, 10, left=120.0, right=8, top=8, bottom=8),
            "side": ViewBlock(10, 10, left=0.0, right=8, top=8, bottom=8),
        }
        g = _layout_geometry(20, 20, 20, 1.0, 380.0, 300.0, 150.0, None, blocks=blocks)
        # Precondition: no centring slack, or the test cannot catch the bug.
        assert g.x_offset == pytest.approx(0.0, abs=0.01), (
            f"test needs x_offset==0 to be meaningful, got {g.x_offset:.1f}"
        )
        pv_left_footprint_edge = g.PV_X - g.fv_hw - 120.0
        assert pv_left_footprint_edge >= _MARGIN - 0.5, (
            f"plan-view left footprint edge {pv_left_footprint_edge:.1f} slid past "
            f"the {_MARGIN} mm margin — column anchored on front.left, not col_left"
        )

    # --- candidate ladder -------------------------------------------------

    def test_repack_candidates_use_the_full_auto_ladder(self):
        from types import SimpleNamespace

        from draftwright._core import _LADDER
        from draftwright.builder import _repack_candidates

        a = SimpleNamespace(SCALE=0.2, PAGE_W=594.0, PAGE_H=420.0, margin=10.0)
        cands = _repack_candidates(a, None, None)
        # #519: repack and choose_scale now share one composed-footprint fit, so
        # repack no longer needs a pass-1 floor to defend against a looser model.
        assert cands == list(_LADDER)
        assert (0.2, 1189.0, 841.0, 150.0) in cands

    def test_auto_repack_fitness_uses_pass1_step_reservations(self):
        from draftwright.compose import ViewBlock, _layout_geometry

        x_size, y_size, z_size = 5.0, 90.0, 100.0
        scale, page_w, page_h, tb_w = 1.0, 420.0, 297.0, 150.0
        blocks = {
            "front": ViewBlock(x_size * scale / 2, z_size * scale / 2),
            "plan": ViewBlock(x_size * scale / 2, y_size * scale / 2),
            "side": ViewBlock(y_size * scale / 2, z_size * scale / 2),
        }

        assert _layout_geometry(
            x_size,
            y_size,
            z_size,
            scale,
            page_w,
            page_h,
            tb_w,
            None,
            0,
            blocks=blocks,
            warn_no_iso=False,
        ).auto_fits
        assert not _layout_geometry(
            x_size,
            y_size,
            z_size,
            scale,
            page_w,
            page_h,
            tb_w,
            None,
            3,
            blocks=blocks,
            warn_no_iso=False,
        ).auto_fits

    def test_section_reservation_sits_beyond_side_right_corridor(self):
        from draftwright.compose import _layout_geometry

        strips = StripDepths(right=42.0, left=10.0)
        g = _layout_geometry(40, 10, 20, 1.0, 420.0, 297.0, 150.0, strips, section=True)
        section_hw = max(g.fv_hw, 12.0)
        side_right = g.SV_X + g.sv_hw
        assert g.SECTION_X - section_hw == pytest.approx(side_right + strips.right + 10.0)

    def test_repack_candidates_honour_fixed_scale_and_page(self):
        from types import SimpleNamespace

        from draftwright.builder import _repack_candidates

        a = SimpleNamespace(SCALE=1.0, PAGE_W=297.0, PAGE_H=210.0, margin=10.0)
        cands = _repack_candidates(a, 2.0, "A3")
        assert len(cands) == 1 and cands[0][0] == 2.0

    def test_repack_to_fixed_point_iterates_until_stable(self, monkeypatch):
        from types import SimpleNamespace

        import draftwright.builder as builder

        a0, d0 = SimpleNamespace(pass_id=0), SimpleNamespace(pass_id=0)
        a1, d1 = SimpleNamespace(pass_id=1), SimpleNamespace(pass_id=1)
        a2, d2 = SimpleNamespace(pass_id=2), SimpleNamespace(pass_id=2)
        returns = [(a1, d1), (a2, d2), None]
        calls = []

        def fake_repack(a, dwg, *args, **kwargs):
            calls.append((a.pass_id, dwg.pass_id))
            return returns.pop(0)

        monkeypatch.setattr(builder, "_repack", fake_repack)
        monkeypatch.setattr(builder, "_needs_repack", lambda dwg, a: False)

        out = builder._repack_to_fixed_point(a0, d0, "out", None, False)

        assert out == (a2, d2)
        assert calls == [(0, 0), (1, 1), (2, 2)]

    def test_repack_to_fixed_point_warns_at_iteration_limit(self, monkeypatch, caplog):
        from types import SimpleNamespace

        import draftwright.builder as builder
        from draftwright.registry import AnnotationRegistry

        calls = []

        def fake_repack(a, dwg, *args, **kwargs):
            calls.append((a.pass_id, dwg.pass_id))
            pass_id = len(calls)
            return SimpleNamespace(
                pass_id=pass_id, registry=AnnotationRegistry()
            ), SimpleNamespace(pass_id=pass_id, registry=AnnotationRegistry())

        monkeypatch.setattr(builder, "_repack", fake_repack)
        monkeypatch.setattr(builder, "_needs_repack", lambda dwg, a: True)

        with caplog.at_level(logging.DEBUG, logger="draftwright._core"):
            out_a, out_dwg = builder._repack_to_fixed_point(
                SimpleNamespace(pass_id=0, registry=AnnotationRegistry()),
                SimpleNamespace(pass_id=0, registry=AnnotationRegistry()),
                "out",
                None,
                False,
            )

        assert len(calls) == builder._REPACK_MAX_ITER
        assert out_a.pass_id == out_dwg.pass_id == builder._REPACK_MAX_ITER
        assert "reached iteration limit" in caplog.text
        assert not [record for record in caplog.records if record.levelno >= logging.WARNING]

    def test_repack_to_fixed_point_warns_on_stalled_trigger(self, monkeypatch, caplog):
        from types import SimpleNamespace

        import draftwright.builder as builder
        from draftwright.registry import AnnotationRegistry

        monkeypatch.setattr(builder, "_repack", lambda *args, **kwargs: None)
        monkeypatch.setattr(builder, "_needs_repack", lambda dwg, a: True)

        with caplog.at_level(logging.DEBUG, logger="draftwright._core"):
            out = builder._repack_to_fixed_point(
                SimpleNamespace(pass_id=0, registry=AnnotationRegistry()),
                SimpleNamespace(pass_id=0, registry=AnnotationRegistry()),
                "out",
                None,
                False,
            )

        assert out is None
        assert "stalled after 0 iteration" in caplog.text
        assert not [record for record in caplog.records if record.levelno >= logging.WARNING]

    @pytest.mark.timeout(120)
    def test_repack_honours_pinned_scale_on_oversized_part(self):
        # #350 review: when no candidate fits the measured layout, the repack backstop
        # bisects for a fitting scale ONLY when the scale is not pinned. A user-pinned
        # scale must be honoured (overflow accepted, as asked) — not silently reduced —
        # and the backstop must never crash on the degenerate no-positive-scale case.
        dwg = build_drawing(Box(4200, 1600, 5400), scale=1, scale_policy="permissive")
        assert dwg.scale == 1.0  # pin honoured, not silently rescaled

    @pytest.mark.timeout(120)
    @_skip_011
    def test_repack_reduces_an_oversized_part_when_scale_is_free(self):
        # The complement: with the scale free, an oversized part is reduced to a scale
        # that fits rather than overflowing (#350) — through the full pass-1 + repack.
        dwg = build_drawing(Box(4200, 1600, 5400))
        assert dwg.scale < 0.2  # a deeper ISO 5455 reduction than the old A0 1:5 floor
        assert not any(i.code.endswith("out_of_bounds") for i in dwg.lint())

    # --- ownership-map lifecycle -----------------------------------------

    @pytest.mark.timeout(60)
    def test_anno_view_lifecycle(self):
        # add(view=) records; re-add view-less clears the stale tag; remove pops;
        # clear_annotations prunes — the map never lags _named (#121).
        dwg = build_drawing(Box(30, 20, 10))

        def _leader(label):
            return Leader(
                tip=dwg.at("front", 0, 0, 0), elbow=(5, 5, 0), label=label, draft=dwg.draft
            )

        dwg._add(_leader("A"), "tag", view="front")
        assert dwg.view_of("tag") == "front"

        dwg._add(_leader("B"), "tag")  # replacement, view-less → clears stale tag
        assert dwg.view_of("tag") is None

        dwg._add(_leader("C"), "tag2", view="plan")
        dwg.remove("tag2")
        assert dwg.view_of("tag2") is None

        dwg._add(_leader("D"), "tag3", view="side")
        dwg._clear_annotations()  # keeps title_block only
        assert dwg.view_of("tag3") is None
