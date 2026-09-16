"""Deferred feature-edit corridor routing and deduplication."""

import pytest
from _parts import holed_plate as _holed_plate
from _parts import multi_hole_plate as _multi_hole_plate
from build123d import Box, Cylinder, Pos

from draftwright import build_drawing


class TestFeatureEditCorridors:
    """Corridor routing, pinning, axes filters, and deduplication."""

    def test_deferred_dimension_generated_names_do_not_collide_in_one_batch(self):
        # #511 review: generated names must be reserved before the corridor drain. Otherwise
        # two same-kind dimensions recorded in one deferred batch both choose dim_length0 and
        # the second add silently replaces the first.
        dwg = build_drawing(Box(80, 50, 20), auto_dims=False)
        env = next(f for f in dwg.model().features if f.kind == "envelope")
        with dwg.deferred():
            dwg.dimension(env, "length", role="width", side="below", pin=True)
            dwg.dimension(env, "length", role="depth", side="below", pin=True)

        names = {n for n in dwg.annotations_of(env) if n.startswith("dim_length")}
        assert names == {"dim_length0", "dim_length1"}
        assert names <= dwg.registry.pinned_names()

    def test_live_dimension_pin_pins_raw_escape_hatch_result(self):
        # #511/ADR 4 (was 0012): live dimension() still uses the single-position page-coordinate
        # escape hatch, but pin=True must persist on the resulting annotation name.
        dwg = build_drawing(Box(80, 50, 20), auto_dims=False)
        env = next(f for f in dwg.model().features if f.kind == "envelope")
        name = dwg.dimension(env, "length", role="width", name="live_width", pin=True)

        assert name == "live_width"
        assert "live_width" in dwg.annotations_of(env)
        assert dwg.registry.is_pinned("live_width")

    def test_malformed_pinned_dimension_still_surfaces_live_valueerror(self):
        # #511 review: pin=True must not make a non-linear hole diameter look corridor
        # routable. It falls through to live replay and leaves the intent recorded.
        dwg = build_drawing(_holed_plate(), auto_dims=False)
        hole = next(f for f in dwg.model().features if f.kind == "hole")
        dwg._defer_intents = True
        dwg.dimension(hole, "diameter", pin=True)

        with pytest.raises(ValueError, match="callout"):
            dwg.finalize()
        assert any(it.kind == "dimension" for it in dwg._intents)

    def test_finalize_honors_locate_axes_restriction(self):
        # #429 review: a recorded locate(f, axes=("x",)) must place only the X dim. The
        # per-feature corridor filter can't express an axis subset, so finalize live-replays
        # axes-restricted locates (routing only both-axes ones through the corridor).
        part = Box(100, 80, 20) - Pos(20, 15, 0) * Cylinder(4, 30)
        dwg = build_drawing(part, auto_dims=False)
        dwg._defer_intents = True
        hole = next(f for f in dwg.model().features if f.kind == "hole")
        dwg.locate(hole, axes=("x",))
        dwg.finalize()
        locs = [n for n in dwg.annotations() if n.startswith("m_loc")]
        assert locs and all(n.startswith("m_locx") for n in locs)  # X only — no m_locy

    def test_finalize_replayed_axes_restricted_locate_can_pin(self):
        # #511 slice 1: axes-restricted locates intentionally bypass the shared corridor
        # filter, but their pin intent must still survive live replay during finalize.
        part = Box(100, 80, 20) - Pos(20, 15, 0) * Cylinder(4, 30)
        dwg = build_drawing(part, auto_dims=False)
        dwg._defer_intents = True
        hole = next(f for f in dwg.model().features if f.kind == "hole")
        dwg.locate(hole, axes=("x",), pin=True)
        dwg.finalize()
        locs = {n for n in dwg.annotations_of(hole) if n.startswith("m_locx")}
        assert locs
        assert locs <= dwg.registry.pinned_names()

    def test_finalize_mixes_axes_restricted_and_both_axes_locate(self):
        # #429 review: an axes-restricted locate (live, names m_locx0) + a both-axes locate
        # (corridor) must NOT collide — the corridor names its dims against _named, so both
        # survive. Regression for the silent-overwrite bug.
        part = (
            Box(120, 80, 20)
            - Pos(-40, 25, 0) * Cylinder(4, 30)
            - Pos(40, -25, 0) * Cylinder(6, 30)
        )
        holes = lambda d: [f for f in d.model().features if f.kind == "hole"]  # noqa: E731

        live = build_drawing(part, auto_dims=False)
        hs = holes(live)
        live.locate(hs[0], axes=("x",))
        live.locate(hs[1])
        live_x = {
            live.get_annotation(n).label for n in live.annotations() if n.startswith("m_locx")
        }

        dwg = build_drawing(part, auto_dims=False)
        dwg._defer_intents = True
        hs2 = holes(dwg)
        dwg.locate(hs2[0], axes=("x",))
        dwg.locate(hs2[1])
        dwg.finalize()
        fin_x = {dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("m_locx")}
        # both features' X location dims survive — none silently overwritten
        assert fin_x == live_x and len(fin_x) == 2

    def test_finalize_routes_slots_through_render_slots(self):
        # #426 Phase 2b: a slot's recorded size dims route through render_slots' corridor
        # placement (m_slot* names), NOT the live dim_length* replay — reaching auto-pass
        # parity. Routing the feature also regenerates its datum POSITION dim (a superset:
        # model-derived, not a recorded intent).
        part = Box(50, 30, 20) - Box(20, 8, 30)  # an enclosed through-slot (#135)

        auto = build_drawing(part)  # auto_dims=True — the reference
        auto_slot = {n for n in auto.annotations() if n.startswith("m_slot")}
        assert auto_slot, "auto-pass must place m_slot* dims"

        dwg = build_drawing(part, auto_dims=False)
        dwg._defer_intents = True
        slot = next(f for f in dwg.model().features if f.kind == "slot")
        dwg.dimension(slot, "length", role="slot_width")
        dwg.dimension(slot, "length", role="slot_length")
        dwg.finalize()
        fin_slot = {n for n in dwg.annotations() if n.startswith("m_slot")}
        assert fin_slot == auto_slot  # same corridor-placed names (m_slot0_width/length/pos)
        # routed, not live-replayed: no dim_length* singles, and both intents drained
        assert not [n for n in dwg.annotations() if n.startswith("dim_length")]
        assert dwg._intents == []

    def test_finalize_malformed_slot_dimension_surfaces_the_live_valueerror(self):
        # #439: slot_ids matches only param="length" + role in (slot_width, slot_length),
        # like len_ids/dia_ids. A malformed slot dim (a param a slot has no parameter for)
        # must NOT be silently routed through render_slots — it falls through to leg-A live
        # replay, where dimension() raises the same ValueError the live (non-deferred) path
        # raises, instead of being swallowed.
        part = Box(50, 30, 20) - Box(20, 8, 30)  # an enclosed through-slot (#135)
        dwg = build_drawing(part, auto_dims=False)
        slot = next(f for f in dwg.model().features if f.kind == "slot")
        with pytest.raises(ValueError):
            with dwg.deferred():
                dwg.dimension(slot, "diameter")  # a slot has no diameter param

    def test_finalize_slot_position_dedups_with_a_coincident_hole_location(self):
        # #426 Phase 2b: slots share the location corridor with hole locates and drain in ONE
        # solve. Here the slot's near edge (x=-10) coincides with a hole's X-location, so the
        # slot POSITION line and that hole location are the SAME datum→10 span — the #345
        # dedup collapses them (no m_slot0_pos survives; the hole's m_locx covers it). The
        # win is exact parity with the auto-pass, which only the combined single drain gives
        # (draining slots and locations separately would place the un-deduped slot position).
        part = (
            Box(60, 40, 20)
            - Box(20, 8, 30)  # slot: long_axis X, near edge x=-10
            - Pos(-10, 14, 0) * Cylinder(3, 30)  # hole X coincides with the slot near edge
            - Pos(20, 14, 0) * Cylinder(3, 30)
            - Pos(8, -14, 0) * Cylinder(3, 30)
        )
        keys = lambda d: {  # noqa: E731
            n for n in d.annotations() if n.startswith("m_slot") or n.startswith("m_loc")
        }
        auto = build_drawing(part)

        dwg = build_drawing(part, auto_dims=False)
        dwg._defer_intents = True
        slot = next(f for f in dwg.model().features if f.kind == "slot")
        dwg.dimension(slot, "length", role="slot_width")
        dwg.dimension(slot, "length", role="slot_length")
        for h in (f for f in dwg.model().features if f.kind in ("hole", "pattern")):
            dwg.locate(h)
        dwg.finalize()

        # exact parity with the auto-pass: the coincident slot-position + hole-location
        # collapsed to one in the shared solve (no stray m_slot0_pos), size dims placed.
        assert keys(dwg) == keys(auto)
        assert "m_slot0_pos" not in dwg.annotations()  # deduped against the coincident hole
        assert dwg._intents == []

    def test_finalize_records_scattered_hole_coverage_without_furniture(self):
        # #426 Phase 4c: finalize routes hole callouts through _annotate_holes with
        # place_furniture=False (furniture is replayed by its own furniture() intents). The
        # scattered-hole-table COVERAGE — which plan callouts _maybe_tabulate_holes may replace
        # — used to be recorded ONLY inside _add_furniture, gated behind place_furniture, so
        # finalize never registered it. The fix records coverage at the callout emit site
        # regardless of the gate; the finalize scattered-doc set must equal the auto-pass set
        # (and be non-empty), so the resolver can find + replace those callouts. Coverage-only,
        # so the auto-pass output is unchanged (guarded by the byte-identity corpus).
        part = _multi_hole_plate()

        def docs(d):
            return {n for n in d.annotations() if d.coverage.is_scattered_hole_doc(n)}

        auto = build_drawing(part)
        assert docs(auto), "auto-pass must register scattered-hole-doc coverage"

        dwg = build_drawing(part, auto_dims=False)
        with dwg.deferred():
            for f in dwg.model().features:
                if getattr(f, "kind", None) in ("hole", "pattern"):
                    dwg.callout(f)
                    dwg.locate(f)
        assert docs(dwg) == docs(auto)  # coverage restored under place_furniture=False
        assert dwg._intents == []  # drained
