"""Automatic and explicit hole-table behavior."""

from pathlib import Path

import pytest
from _parts import dense_plate as _dense_plate
from _parts import multi_hole_plate as _multi_hole_plate
from build123d import Box

from draftwright import build_drawing


@pytest.fixture
def plain_box_dwg(shared_drawing):
    return shared_drawing("box_60x40x20")


class TestHoleTable:
    """#93: hole table placed in a free corner via place_box."""

    class _Boxed:
        def __init__(self, bb):
            from types import SimpleNamespace

            x0, y0, x1, y1 = bb
            self._bb = SimpleNamespace(
                min=SimpleNamespace(X=x0, Y=y0),
                max=SimpleNamespace(X=x1, Y=y1),
            )

        def bounding_box(self):
            return self._bb

    @staticmethod
    def _area(a, b):
        ox = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
        oy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
        return ox * oy

    def _bbox(self, obj):
        bb = obj.bounding_box()
        return (bb.min.X, bb.min.Y, bb.max.X, bb.max.Y)

    def test_top_lane_target_keeps_balanced_share_when_sides_have_room(self):
        from draftwright.annotations.balloons import _top_lane_target

        assert _top_lane_target(18, [41, 41], 3) == 6

    def test_balloon_render_extent_and_compose_reservation_share_geometry(self):
        from draftwright._core import _balloon_halo, _balloon_radius
        from draftwright.annotations.balloons import _band_preference_limit
        from draftwright.compose import _est_plan_halo

        assert _est_plan_halo(3.0) == _balloon_halo(3.0) == 21.5
        assert _band_preference_limit(3.0) == _balloon_halo(3.0)
        assert _balloon_radius(3.0) == 4.5

    def test_top_lane_target_covers_capacity_deficit_on_other_bands(self):
        from draftwright.annotations.balloons import _top_lane_target

        # Balanced share alone is 3, but only three members fit elsewhere: the
        # top lane needs seven or the max-cardinality assignment would drop one.
        assert _top_lane_target(10, [2, 1], 3) == 7

    def test_top_lane_selection_prefers_nearest_sufficient_lane(self):
        from draftwright.annotations.balloons import _select_top_lane

        lanes = [(20.0, [(0.0, 10.0)], 2), (40.0, [(0.0, 30.0)], 4)]
        assert _select_top_lane(lanes, 2, 99.0) == lanes[0]

    def test_top_lane_selection_falls_back_to_nearest_lane(self):
        from draftwright.annotations.balloons import _select_top_lane

        lanes = [
            (20.0, [(0.0, 10.0)], 2),
            (40.0, [(0.0, 30.0)], 4),
            (60.0, [(0.0, 30.0)], 4),
        ]
        assert _select_top_lane(lanes, 8, 99.0) == lanes[0]

    def test_top_lane_selection_handles_no_lane_on_a_constrained_page(self):
        from draftwright.annotations.balloons import _select_top_lane

        assert _select_top_lane([], 1, 99.0) == (99.0, [], 0)

    def test_table_has_a_row_per_spec_group(self):
        dwg = build_drawing(_multi_hole_plate())
        n_groups = len([f for f in dwg.features("plan") if f.type == "hole"])
        assert n_groups == 2  # ø10 (×2) and ø16
        tbl = dwg.add_hole_table("plan")
        assert tbl is not None
        assert "hole_table_plan" in dwg.annotations()
        # header + one row per group; the table is a grid Compound.
        assert tbl.table_size[0] > 0 and tbl.table_size[1] > 0

    def test_a_balloon_per_hole_keyed_to_a_row(self):
        # 3 physical holes (1 ø16 → A, 2 ø10 → B) get 3 balloons; tags A,B exist.
        dwg = build_drawing(_multi_hole_plate())
        dwg.add_hole_table("plan")
        balloons = [n for n in dwg.annotations() if n.startswith("balloon_plan_")]
        assert len(balloons) == 3
        tags = {n.split("_")[2] for n in balloons}
        assert tags == {"A", "B"}

    def test_balloons_false_suppresses_them(self):
        dwg = build_drawing(_multi_hole_plate())
        dwg.add_hole_table("plan", balloons=False)
        assert not any(n.startswith("balloon_") for n in dwg.annotations())

    def test_place_band_reports_dropped_overflow(self, monkeypatch):
        # #1a review follow-up: a band too small for every balloon drops its tail
        # (the strip solver's prefix fallback) — _place_band must REPORT the dropped
        # count so render_balloons can surface it as `balloon_dropped` lint, instead
        # of the balloons vanishing silently. 5 balloons needing a 10 mm gap in a
        # 20 mm band fit only 3 (at 0, 10, 20); the other 2 are dropped and reported.
        import draftwright.annotations.balloons as balloons

        rendered: list = []
        monkeypatch.setattr(balloons, "_render_balloon", lambda *a: rendered.append(a))
        members = [("t", 0, object(), 0.0, float(i)) for i in range(5)]
        dropped = balloons._place_band(
            None, "plan", members, "y", 50.0, 0.0, 20.0, 10.0, 3.0, 5.0, None
        )
        assert dropped == 2 and len(rendered) == 3

    def test_balloon_ring_depth_uses_bare_obstacle_footprints(self, monkeypatch):
        from types import SimpleNamespace

        from draftwright._core import _STRIP_GAP

        calls = []
        a = SimpleNamespace(
            PV_X=50.0,
            PV_Y=50.0,
            fv_hw=20.0,
            pv_hh=10.0,
            SV_X=95.0,
            sv_hw=10.0,
            margin=0.0,
            PAGE_H=120.0,
            PAGE_W=120.0,
            FV_Y=20.0,
            fv_hh=5.0,
        )
        a.pv_zones = SimpleNamespace(
            left=SimpleNamespace(outer_limit=a.margin),
            right=SimpleNamespace(outer_limit=a.SV_X - a.sv_hw),
            below=SimpleNamespace(outer_limit=a.FV_Y + a.fv_hh),
            above=SimpleNamespace(outer_limit=a.PAGE_H - a.margin),
        )
        pt = a.PV_Y + a.pv_hh
        bare_obstacle = self._Boxed((35.0, pt + 2.0, 65.0, pt + 12.0))

        import draftwright.annotations.balloons as balloons

        def place_band(dwg, view, members, axis, line, lo, hi, gap, fs, r, ctx):
            calls.append((view, members, axis, line, lo, hi, gap, fs, r))
            return 0

        monkeypatch.setattr(balloons, "_place_band", place_band)
        coords = {"plan": SimpleNamespace(pp=lambda *_loc: (50.0, 58.0))}
        stub = SimpleNamespace(
            coords=coords.__getitem__,
            draft=SimpleNamespace(font_size=3.0),
            iter_annotations=lambda: iter([("bare_obstacle", bare_obstacle)]),
            view_of=lambda _name: "plan",
        )
        ctx = SimpleNamespace(record_issue=lambda *_args: None)
        hole = SimpleNamespace(location=(0.0, 0.0, 0.0), diameter=4.0)

        balloons.render_balloons(stub, a, "plan", [("A", 0, hole)], ctx)

        top_call = next(call for call in calls if call[2] == "x" and call[1])
        _view, _members, _axis, line, *_rest, fs, r = top_call
        assert line == pytest.approx(pt + 12.0 + _STRIP_GAP + r)
        assert fs == 3.0

    def test_perimeter_top_lane_carves_around_deep_local_obstacle(self, monkeypatch):
        """#901/#125: one tall, narrow occupant must carve the near lane, not
        push the entire top ring beyond the occupant's remote outer edge."""
        from types import SimpleNamespace

        calls = []
        a = SimpleNamespace(
            PV_X=50.0,
            PV_Y=50.0,
            fv_hw=20.0,
            pv_hh=10.0,
            SV_X=110.0,
            sv_hw=10.0,
            margin=0.0,
            PAGE_H=180.0,
            PAGE_W=140.0,
            FV_Y=0.0,
            fv_hh=5.0,
        )
        a.pv_zones = SimpleNamespace(
            left=SimpleNamespace(outer_limit=a.margin),
            right=SimpleNamespace(outer_limit=a.SV_X - a.sv_hw),
            below=SimpleNamespace(outer_limit=a.FV_Y + a.fv_hh),
            above=SimpleNamespace(outer_limit=a.PAGE_H - a.margin),
        )
        pt = a.PV_Y + a.pv_hh
        obstacle = self._Boxed((47.0, pt + 2.0, 53.0, pt + 80.0))

        import draftwright.annotations.balloons as balloons
        from draftwright._core import _balloon_halo, _balloon_radius

        assert a.pv_zones.right.outer_limit - (a.PV_X + a.fv_hw) > _balloon_halo(3.0)
        real_assign = balloons._assign_balloon_bands
        assignment_kwargs = []

        def assign_bands(*args, **kwargs):
            assignment_kwargs.append(kwargs)
            return real_assign(*args, **kwargs)

        def place_band(
            dwg,
            view,
            members,
            axis,
            line,
            lo,
            hi,
            gap,
            fs,
            r,
            ctx,
            *,
            segments=None,
        ):
            calls.append((list(members), axis, line, segments))
            return 0

        monkeypatch.setattr(balloons, "_place_band", place_band)
        monkeypatch.setattr(balloons, "_assign_balloon_bands", assign_bands)
        # Left is naturally ~14 mm cheaper than top for these sites. The bounded
        # perimeter preference must still seed the nearby top band; disabling
        # production preference wiring makes `top_members` below empty.
        coords = {"plan": SimpleNamespace(pp=lambda x, y, _z: (30.0 + x, 50.0 + y))}
        stub = SimpleNamespace(
            coords=coords.__getitem__,
            draft=SimpleNamespace(font_size=3.0),
            iter_annotations=lambda: iter([("local_obstacle", obstacle)]),
            view_of=lambda _name: "plan",
        )
        ctx = SimpleNamespace(record_issue=lambda *_args: None)
        holes = [SimpleNamespace(location=(float(i), 0.0, 0.0), diameter=4.0) for i in range(6)]

        balloons.render_balloons(
            stub,
            a,
            "plan",
            [(str(i), 0, hole) for i, hole in enumerate(holes)],
            ctx,
            perimeter=True,
        )

        top_members, _axis, top_line, segments = next(
            call for call in calls if call[1] == "x" and call[2] > a.PV_Y
        )
        assert top_members
        assert top_line == pytest.approx(pt + _balloon_halo(3.0) - _balloon_radius(3.0))
        assert segments and len(segments) == 2
        assert assignment_kwargs == [
            {
                "prefer_bands": ("left", "right", "top", "bottom"),
                "preference_limit": _balloon_halo(3.0),
            }
        ]

    def test_segmented_band_overflow_uses_prefix_instead_of_dropping_all(self, monkeypatch):
        import draftwright.annotations.balloons as balloons

        rendered = []
        monkeypatch.setattr(balloons, "_render_balloon", lambda *args: rendered.append(args))
        members = [(str(i), 0, object(), float(i * 10), 0.0) for i in range(3)]

        dropped = balloons._place_band(
            None,
            "plan",
            members,
            "x",
            50.0,
            0.0,
            25.0,
            10.0,
            3.0,
            4.5,
            None,
            segments=[(0.0, 5.0), (20.0, 25.0)],
        )

        assert dropped == 1
        assert len(rendered) == 2

    def test_balloon_assignment_rebalances_across_bands_before_dropping(self, monkeypatch):
        from types import SimpleNamespace

        from draftwright._core import (
            _STRIP_GAP,
            _STRIP_SPACING,
            _balloon_radius,
        )
        from draftwright.layout import _strip_capacity

        calls = []
        a = SimpleNamespace(
            PV_X=50.0,
            PV_Y=50.0,
            fv_hw=20.0,
            pv_hh=10.0,
            SV_X=82.0,
            sv_hw=10.0,
            margin=0.0,
            PAGE_H=120.0,
            PAGE_W=120.0,
            FV_Y=30.0,
            fv_hh=5.0,
        )
        a.pv_zones = SimpleNamespace(
            left=SimpleNamespace(outer_limit=a.margin),
            right=SimpleNamespace(outer_limit=a.SV_X - a.sv_hw),
            below=SimpleNamespace(outer_limit=a.FV_Y + a.fv_hh),
            above=SimpleNamespace(outer_limit=a.PAGE_H - a.margin),
        )

        import draftwright.annotations.balloons as balloons

        def place_band(dwg, view, members, axis, line, lo, hi, gap, fs, r, ctx):
            calls.append((view, list(members), axis, line, lo, hi, gap, fs, r))
            return 0

        monkeypatch.setattr(balloons, "_place_band", place_band)
        coords = {"plan": SimpleNamespace(pp=lambda *_loc: (50.0, 58.0))}
        stub = SimpleNamespace(
            coords=coords.__getitem__,
            draft=SimpleNamespace(font_size=3.0),
            iter_annotations=lambda: iter(()),
            view_of=lambda _name: "plan",
        )
        ctx = SimpleNamespace(record_issue=lambda *_args: None)
        holes = [SimpleNamespace(location=(float(i), 0.0, 0.0), diameter=4.0) for i in range(6)]

        balloons.render_balloons(
            stub, a, "plan", [(chr(ord("A") + i), 0, h) for i, h in enumerate(holes)], ctx
        )

        fs = stub.draft.font_size
        r = _balloon_radius(fs)
        gap = 2 * r + 2 * _STRIP_SPACING
        top_cap = _strip_capacity(a.PV_X - a.fv_hw - _STRIP_GAP, a.SV_X - a.sv_hw - r, gap)
        top_members = next(call[1] for call in calls if call[2] == "x" and call[3] > a.PV_Y)
        side_members = [m for call in calls if call[2] == "y" for m in call[1]]

        assert len(top_members) == top_cap
        assert len(side_members) == len(holes) - top_cap

    def test_balloon_assignment_cost_uses_actual_band_line_after_furniture_depth(
        self, monkeypatch
    ):
        from types import SimpleNamespace

        calls = []
        a = SimpleNamespace(
            PV_X=50.0,
            PV_Y=50.0,
            fv_hw=20.0,
            pv_hh=10.0,
            SV_X=34.0,
            sv_hw=10.0,
            margin=0.0,
            PAGE_H=120.0,
            PAGE_W=140.0,
            FV_Y=30.0,
            fv_hh=5.0,
        )
        a.pv_zones = SimpleNamespace(
            left=SimpleNamespace(outer_limit=a.margin),
            right=SimpleNamespace(outer_limit=a.SV_X - a.sv_hw),
            below=SimpleNamespace(outer_limit=a.FV_Y + a.fv_hh),
            above=SimpleNamespace(outer_limit=a.PAGE_H - a.margin),
        )
        right_obstacle = self._Boxed((71.0, 45.0, 115.0, 55.0))

        import draftwright.annotations.balloons as balloons

        def place_band(dwg, view, members, axis, line, lo, hi, gap, fs, r, ctx):
            calls.append((view, list(members), axis, line, lo, hi, gap, fs, r))
            return 0

        monkeypatch.setattr(balloons, "_place_band", place_band)
        coords = {"plan": SimpleNamespace(pp=lambda *_loc: (60.0, 50.0))}
        stub = SimpleNamespace(
            coords=coords.__getitem__,
            draft=SimpleNamespace(font_size=3.0),
            iter_annotations=lambda: iter([("right_obstacle", right_obstacle)]),
            view_of=lambda _name: "plan",
        )
        ctx = SimpleNamespace(record_issue=lambda *_args: None)
        hole = SimpleNamespace(location=(0.0, 0.0, 0.0), diameter=4.0)

        balloons.render_balloons(stub, a, "plan", [("A", 0, hole)], ctx)

        left_members = next(call[1] for call in calls if call[2] == "y" and call[3] < a.PV_X)
        right_members = next(call[1] for call in calls if call[2] == "y" and call[3] > a.PV_X)
        assert [m[0] for m in left_members] == ["A"]
        assert right_members == []

    @pytest.mark.parametrize("method", ["first", "third"])
    def test_table_and_balloons_keep_lint_clean(self, method):
        # One covers_diameters entry per physical bore lets coverage lint verify the
        # table's visible QTY, and the balloons are furniture (is_centerline) so they do
        # not trip overlap lint.
        dwg = build_drawing(_multi_hole_plate(), projection=method)
        before = {i.code for i in dwg.lint()}
        assert before == set()
        assert dwg.scale == 1
        dwg.add_hole_table("plan")
        assert len([n for n in dwg.annotations() if n.startswith("balloon_plan")]) == 3
        assert {i.code for i in dwg.lint()} == before
        assert dwg.get_annotation("hole_table_plan").covers_diameters == (16.0, 10.0, 10.0)

    def test_table_does_not_overlap_views_or_title_block(self):
        dwg = build_drawing(_multi_hole_plate())
        dwg.add_hole_table("plan")
        tb = self._bbox(dwg.get_annotation("hole_table_plan"))
        for v in dwg.views:
            assert self._area(tb, dwg.view_bounds(v)) == 0.0, v
        assert self._area(tb, self._bbox(dwg.get_annotation("title_block"))) == 0.0

    def test_no_holes_in_view_returns_none(self):

        dwg = build_drawing(Box(60, 40, 20))
        assert dwg.add_hole_table("plan") is None
        assert "hole_table_plan" not in dwg.annotations()

    def test_table_dropped_when_it_will_not_fit(self, monkeypatch):
        import sys

        m = sys.modules["draftwright.drawing"]
        monkeypatch.setattr(m, "fit_box", lambda *a, **k: None)
        dwg = build_drawing(_multi_hole_plate())
        assert dwg.add_hole_table("plan") is None
        assert "table_dropped" in {i.code for i in dwg.lint()}

    def test_tag_sequence_rolls_over_past_z(self):
        from draftwright._core import _tag_sequence

        seq = _tag_sequence(28)
        assert seq[:3] == ["A", "B", "C"]
        assert seq[25] == "Z"
        assert seq[26] == "AA"
        assert seq[27] == "AB"
        # The base-26 rollover boundary and uniqueness.
        full = _tag_sequence(703)
        assert full[701] == "ZZ"
        assert full[702] == "AAA"
        assert len(set(full)) == 703  # bijective — no dup or skip

    def test_table_keeps_lint_clean(self, tmp_path):
        # The label-less table must not trip annotation_overlap / view-overlap
        # lint, and the mixed Edge+Text Compound must export cleanly.
        dwg = build_drawing(_multi_hole_plate())
        before = {i.code for i in dwg.lint()}
        dwg.add_hole_table("plan")
        after = {i.code for i in dwg.lint()}
        assert after == before  # no new lint codes from the table
        _p = dwg.export(str(tmp_path / "t"), formats=("svg", "dxf"))
        svg = _p["svg"]
        dxf = _p["dxf"]
        assert Path(svg).stat().st_size > 0 and Path(dxf).stat().st_size > 0

    def test_table_geometry_is_deterministic(self, plain_box_dwg):
        from draftwright._core import _build_table

        rows = [("TAG", "⌀", "QTY"), ("A", "ø10", "2")]
        a = plain_box_dwg.draft
        assert _build_table(rows, a).table_size == _build_table(rows, a).table_size

    def test_generic_add_table_places_arbitrary_rows(self):
        # The builder is generic: a gear/BOM-style param table places like a
        # hole table, clear of the views and title block.
        dwg = build_drawing(_multi_hole_plate())
        rows = [("PARAMETER", "VALUE"), ("MODULE", "0.5"), ("RATIO", "13:1")]
        tbl = dwg.add_table(rows, name="gear_data")
        assert tbl is not None and "gear_data" in dwg.annotations()
        tb = self._bbox(tbl)
        for v in dwg.views:
            assert self._area(tb, dwg.view_bounds(v)) == 0.0, v


@pytest.fixture(scope="module")
def dense_plate_dwg():
    """Shared **read-only** build of ``_dense_plate()`` for the escalation assertions
    that only inspect the finished drawing (#153 — each rebuilt the ~20 s dense-plate
    just to read a different property). Tests that mutate the drawing (append an
    escalation, record an issue, run the resolver) must build their own."""
    return build_drawing(_dense_plate())
