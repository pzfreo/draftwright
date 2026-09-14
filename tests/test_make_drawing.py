"""Tests for draftwright.make_drawing."""

import pytest
from _kernel import B123D_GE_011, SKIP_011
from _parts import holed_plate as _holed_plate
from _parts import multi_hole_plate as _multi_hole_plate
from build123d import Box, Cylinder, Pos, export_step

from draftwright import build_drawing

_skip_011 = pytest.mark.skipif(B123D_GE_011, reason=SKIP_011)




def _state_snapshot(dwg):
    """The mutable state a read-only test must not touch — annotation count,
    names, pins, and the per-view (visible, hidden) tuples (by identity)."""
    return (
        len(dwg.items),
        frozenset(dwg.annotations()),
        frozenset(dwg.registry.pinned_names()),
        {k: (id(vis), id(hid)) for k, (vis, hid) in dwg.views.items()},
    )


@pytest.fixture(scope="module")
def plain_box_dwg():
    """A built ``Box(60, 40, 20)`` drawing, built once and shared by the
    **read-only** tests in this module (#153 — the hot part is otherwise rebuilt
    dozens of times). A teardown guard asserts the drawing was not mutated, so a
    consumer that accidentally adds/removes/pins an annotation or swaps a view
    fails loudly here instead of silently contaminating its neighbours."""
    dwg = build_drawing(Box(60, 40, 20))
    before = _state_snapshot(dwg)
    yield dwg
    assert _state_snapshot(dwg) == before, (
        "a shared-fixture consumer mutated plain_box_dwg — give that test its "
        "own build_drawing(Box(60, 40, 20)) (see #153)"
    )


# ---------------------------------------------------------------------------
# Pure-function unit tests (fast, no OCP projection)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def small_box_dwg():
    return build_drawing(Box(30, 20, 10))


@pytest.fixture(scope="module")
def holed_plate_dwg():
    return build_drawing(_holed_plate())






# ---------------------------------------------------------------------------
# Phase 2 annotation depth estimators (#118)
# ---------------------------------------------------------------------------


# Phase 3 (#118): dynamic FV→SV corridor
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Two-pass layout (#131): bore callout width drives gap_fv_sv
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Integration test — requires build123d + OCP (slow)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# ViewCoordinates (pure-Python, no OCP needed)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# analyse_cylinders / recognise_face_levels — require OCP (slow)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Drawing builder (build_drawing / Drawing / add_view)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Part classification (#81) — prismatic parts skip turned-part annotations
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Export fallback (#83) — element-wise retry with view/layer context
# ---------------------------------------------------------------------------






# ---------------------------------------------------------------------------
# Feature-coverage lint (#80) — size coverage of hole/boss diameters
# ---------------------------------------------------------------------------














# ---------------------------------------------------------------------------
# Layout-overfitting regression tests (issue #13)
#
# The fixtures above exercise the prismatic path well but leave the turned
# path and several hard-coded thresholds under-tested — which is how the
# overfitting in #10–#12 went unnoticed. These cases pin the *general*
# behaviour the algorithm should have. Where current `main` does not yet
# meet it, the test is marked xfail(strict=True) so it auto-flags (xpass)
# the moment the corresponding fix lands.
# ---------------------------------------------------------------------------








# ---------------------------------------------------------------------------
# Degenerate near-zero-radius arc sanitisation (CTC-02 "black line" fix)
# ---------------------------------------------------------------------------






# ---------------------------------------------------------------------------
# Lint summary + surfacing of build-time annotation drops (#32)
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Layout generalisation guards (#13) — pin the *general* behaviour the
# algorithm should have on turned/hybrid parts and at the step-legibility
# boundary, so the overfitting that #10–#12/#31 removed cannot creep back.
# ---------------------------------------------------------------------------






@pytest.mark.timeout(120)


# ---------------------------------------------------------------------------
# Issue #45: TYP / representative dimensioning for uniform step patterns
# ---------------------------------------------------------------------------






# ---------------------------------------------------------------------------
# Issues #26 + #25: dwg.features() and dwg.place_dim()
# ---------------------------------------------------------------------------


def _model_signature(m):
    """Provenance-agnostic structural signature of a PartModel: orientation + sorted
    (feature-kind, count) + datum count. Byte coordinates differ across a STEP round-trip;
    the semantic structure must not."""
    kinds: dict = {}
    for f in m.features:
        kinds[f.kind] = kinds.get(f.kind, 0) + 1
    return (m.orientation, tuple(sorted(kinds.items())), len(m.datums))


class TestModel:
    """#397: dwg.model() exposes the detected ADR 1 (was 0008) PartModel as the read surface."""

    def test_model_exposes_detected_features(self, holed_plate_dwg):
        m = holed_plate_dwg.model()
        assert m is not None
        assert m.orientation is None  # prismatic plate
        kinds = {f.kind for f in m.features}
        assert "hole" in kinds
        assert len(m.datums) >= 1

    def test_model_present_without_auto_dims(self):
        # #398 detect-hoist: detection runs in the pipeline, not the annotation pass, so a
        # manual-mode build still exposes the model — a script can dimension detected
        # features even when it suppressed the automatic ones.
        m = build_drawing(_holed_plate(), auto_dims=False).model()
        assert m is not None
        assert any(f.kind == "hole" for f in m.features)

    def test_model_identical_across_auto_and_manual(self):
        # Same detection regardless of whether dimensions were auto-placed — the model is
        # a property of the part, not the annotation pass.
        part = _holed_plate()
        assert _model_signature(build_drawing(part).model()) == _model_signature(
            build_drawing(part, auto_dims=False).model()
        )


# Annotation-name prefixes that are always owned by exactly ONE feature (never a shared
# span), so every one of them on the sheet MUST have a provenance owner. Location dims
# (m_locx/m_locy, dim_loc_*) and turned-diameter callouts (m_dia_*) are excluded from the
# blanket rule — a coordinate OR a diameter shared by two distinct features is
# intentionally unowned (#398c/#406/#412). Their owned cases are checked in dedicated tests.
_ALWAYS_OWNED = ("hc_", "bc_", "m_cm", "dim_pitch", "balloon_", "m_slot")


def _assert_drop_is_complete(dwg):
    """The #408 consistency invariant, in two non-tautological parts.

    (1) COMPLETENESS: every single-feature-owned annotation on the sheet has a provenance
    owner — this catches a render pass that stops tagging (which the drop==annotations_of
    check alone cannot, since both derive from the same name set; #410 review).

    (2) CONSISTENCY: for every feature, drop() removes exactly annotations_of() and leaves
    nothing behind. Distinct features never share an owned annotation, so dropping each in
    turn is independent."""
    reg = dwg.registry
    for name in dwg.annotations():
        if name.startswith(_ALWAYS_OWNED):
            assert reg.feature_of(name) is not None, f"{name}: feature annotation left unowned"
    for f in list(dwg.model().features):
        owned = set(dwg.annotations_of(f))
        removed = set(dwg.drop(f))
        assert removed == owned, f"{f.kind}: drop removed {removed} != annotations_of {owned}"
        assert not dwg.annotations_of(f), f"{f.kind}: annotations remain after drop"


class TestFeatureEdits:
    """#398b: first-class feature provenance — drop()/annotations_of() by feature.

    Coverage today is centre marks (the first render pass to carry provenance); slots,
    locations, callouts and diameters thread `feature` in follow-up PRs. annotations_of()
    returns exactly the covered set, so drop() is transparent about what it removes."""

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
        # (#639) Escalations live on the per-run PlacementContext, discarded when finalize
        # returns — no cross-run leak to assert against on the drawing.

    @staticmethod
    def _hc_ys(d):
        return sorted(
            round(d.get_annotation(n).bounding_box().center().Y, 1)
            for n in d.annotations()
            if n.startswith("hc_")
        )

    def test_finalize_routes_callouts_through_annotate_holes(self):
        # #426 Phase 3a: hole/pattern ø callouts route through the auto-pass's _annotate_holes
        # priority-drop/anchoring solve, so the finalize reconstruction reproduces the
        # auto-pass callout layout exactly (not the live per-feature corridor-free placement).
        part = (
            Box(120, 90, 20)
            - Cylinder(5, 30)  # central ø10 → anchored by the auto-pass
            - Pos(40, 30, 0) * Cylinder(3, 30)
            - Pos(-40, -30, 0) * Cylinder(4, 30)
        )
        auto = build_drawing(part)  # auto_dims=True — the reference

        dwg = build_drawing(part, auto_dims=False)
        dwg._defer_intents = True
        for f in (x for x in dwg.model().features if x.kind in ("hole", "pattern")):
            dwg.callout(f)
            dwg.furniture(f)
        dwg.finalize()

        assert self._hc_ys(dwg) and self._hc_ys(dwg) == self._hc_ys(auto)  # batch == auto-pass

    def test_finalize_does_not_double_place_pattern_furniture(self):
        # #426 Phase 3a: _annotate_holes places a pattern's callout but NOT its furniture
        # (place_furniture=False) — the replayed furniture() intent owns it, so bc_ appears
        # exactly once, not doubled.
        import math

        part = Box(120, 120, 20)
        for k in range(6):
            ang = math.radians(60 * k)
            part -= Pos(35 * math.cos(ang), 35 * math.sin(ang), 0) * Cylinder(4, 20)
        dwg = build_drawing(part, auto_dims=False)
        dwg._defer_intents = True
        pat = next(f for f in dwg.model().features if f.kind == "pattern")
        dwg.callout(pat)
        dwg.furniture(pat)
        dwg.finalize()
        assert len([n for n in dwg.annotations() if n.startswith("bc_")]) == 1  # not doubled

    def test_finalize_callouts_survive_a_second_batch(self):
        # #430 review: the batch callout naming is _named-aware, so a second finalize batch
        # doesn't re-emit hc_plan0 and clobber the first batch's callout (cross-batch seam).
        part = (
            Box(120, 80, 20)
            - Pos(-40, 25, 0) * Cylinder(4, 30)
            - Pos(40, -25, 0) * Cylinder(6, 30)
        )
        dwg = build_drawing(part, auto_dims=False)
        holes = [f for f in dwg.model().features if f.kind == "hole"]
        dwg._defer_intents = True
        dwg.callout(holes[0])
        dwg.finalize()  # batch 1
        dwg._defer_intents = True
        dwg.callout(holes[1])
        dwg.finalize()  # batch 2 — must not overwrite batch 1's callout
        assert dwg.annotations_of(holes[0]) and dwg.annotations_of(holes[1])
        assert len([n for n in dwg.annotations() if n.startswith("hc_")]) == 2

    def test_finalize_sectioned_part_reserves_then_renders_section(self):
        # #426 Phase 3b: a sectioned part reserves the section row BEFORE the callout carve
        # (Coupling A), routes callouts through _annotate_holes, and renders the section last
        # — so the reconstruction reproduces the auto-pass callout layout AND places the section.
        part = Box(60, 40, 20) - Cylinder(4, 30) - Pos(0, 0, 2) * Cylinder(7, 20)  # counterbore
        auto = build_drawing(part)  # auto_dims=True — the reference

        dwg = build_drawing(part, auto_dims=False)
        dwg._defer_intents = True
        for f in (x for x in dwg.model().features if x.kind in ("hole", "pattern")):
            dwg.callout(f)
        dwg.section()
        dwg.finalize()  # must not raise
        assert "section_caption" in dwg.annotations() and "section_line" in dwg.annotations()
        # the callout carve saw the reserved section row → callouts match the auto-pass
        assert self._hc_ys(dwg) and self._hc_ys(dwg) == self._hc_ys(auto)

    @staticmethod
    def _dia_ys(d):
        return sorted(
            round(d.get_annotation(n).bounding_box().center().Y, 1)
            for n in d.annotations()
            if n.startswith("m_dia")
        )

    def test_finalize_routes_step_diameters_through_render_diameters(self):
        # #426 Phase 4a: step/boss ø callouts route through render_diameters' row/column
        # set-solve, so each step diameter lands at the same position the auto-pass gives it.
        # finalize may place ONE EXTRA (the OD/base diameter): the auto-pass suppresses it
        # because render_rotational already shows it as dim_od, but that rotational furniture
        # is a gap kind not reconstructed here (#424) — so the auto-pass diameters are a
        # SUBSET of finalize's, matching where they overlap.
        from build123d import Cylinder

        shaft = (
            Cylinder(24, 15)
            + Cylinder(16, 15).translate((0, 0, 15))
            + Cylinder(9, 15).translate((0, 0, 30))
        )
        # Keep the same principal topology as the manual reconstruction below. Automatic
        # turned-view selection is an independent outer-layout decision; this test isolates
        # whether finalize routes the chain through the same renderer.
        auto = build_drawing(shaft, _views=("front", "plan", "side"))

        dwg = build_drawing(shaft, auto_dims=False)
        dwg._defer_intents = True
        for f in (x for x in dwg.model().features if x.kind in ("step", "boss")):
            dwg.callout(f)
        dwg.finalize()
        assert self._dia_ys(dwg) and set(self._dia_ys(auto)) <= set(self._dia_ys(dwg))

    def test_finalize_step_diameters_survive_a_second_batch(self):
        # #426 Phase 4a: render_diameters names m_dia_{x,z} _named-aware when only set, so a
        # second finalize batch does not overwrite the first batch's diameter leader.
        from build123d import Cylinder

        shaft = (
            Cylinder(24, 15)
            + Cylinder(16, 15).translate((0, 0, 15))
            + Cylinder(9, 15).translate((0, 0, 30))
        )
        dwg = build_drawing(shaft, auto_dims=False)
        steps = [f for f in dwg.model().features if f.kind == "step"]
        assert len(steps) >= 2
        dwg._defer_intents = True
        dwg.callout(steps[0])
        dwg.finalize()
        first = {
            n: dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("m_dia")
        }
        dwg._defer_intents = True
        dwg.callout(steps[1])
        dwg.finalize()
        after = {
            n: dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("m_dia")
        }
        for n, lbl in first.items():  # batch 1's leaders survive with their labels
            assert n in after and after[n] == lbl

    def test_finalize_step_diameters_no_overwrite_after_drop(self):
        # #432 review: render_diameters starts past the MAX existing m_dia index (not
        # first-free), so a batch after drop() leaves a GAP can't wrap onto an occupied
        # higher index and silently overwrite an earlier diameter leader.
        from build123d import Cylinder

        shaft = Cylinder(26, 10)
        for k, r in enumerate((22, 18, 14, 10, 6), start=1):
            shaft += Cylinder(r, 10).translate((0, 0, 10 * k))
        dwg = build_drawing(shaft, auto_dims=False)
        steps = [f for f in dwg.model().features if f.kind == "step"]
        assert len(steps) >= 5

        dwg._defer_intents = True
        for s in steps[:3]:
            dwg.callout(s)
        dwg.finalize()  # m_dia_z0/1/2
        dwg.drop(steps[1])  # removes its m_dia → a gap in the index sequence
        survivors = {
            n: dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("m_dia")
        }

        dwg._defer_intents = True
        for s in steps[3:5]:
            dwg.callout(s)
        dwg.finalize()  # must start past the max index, not wrap onto a survivor
        after = {
            n: dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("m_dia")
        }
        for n, lbl in survivors.items():
            assert n in after and after[n] == lbl

    def test_finalize_routes_step_lengths_through_render_step_lengths(self):
        # #426 Phase 4b: a turned shaft's step-length dims route through render_step_lengths'
        # chain, so the finalize reconstruction reproduces the auto-pass step-length layout
        # (m_steplen* at the same positions), not the live per-feature independent dims.
        from build123d import Cylinder, Rot

        shaft = Rot(0, 90, 0) * (
            Cylinder(20, 12)
            + Cylinder(15, 18).translate((0, 0, 12))
            + Cylinder(10, 25).translate((0, 0, 30))
        )
        auto = build_drawing(shaft)  # auto_dims=True — the reference

        dwg = build_drawing(shaft, auto_dims=False)
        dwg._defer_intents = True
        for f in dwg.model().features:
            if f.kind == "step":
                dwg.dimension(f, "length", role="step")
        dwg.finalize()

        def steplen_pos(d):
            _x0, _y0, _x1, front_top = d.view_bounds("front")
            return sorted(
                (
                    n,
                    round(d.get_annotation(n).bounding_box().center().X, 1),
                    # Compose-then-repack may translate the whole view block after the auto
                    # pass measures external text clearance.  Renderer parity is the chain's
                    # position relative to its owning view, not an incidental page ordinate.
                    round(d.get_annotation(n).bounding_box().center().Y - front_top, 1),
                )
                for n in d.annotations()
                if n.startswith("m_steplen")
            )

        assert steplen_pos(dwg) and steplen_pos(dwg) == steplen_pos(auto)  # the chain, not singles
        assert not any(
            n.startswith("dim_length") for n in dwg.annotations()
        )  # no live single dims

    def test_place_dim_feature_kwarg_tags_provenance(self):
        dwg = build_drawing(_holed_plate())
        hole = next(f for f in dwg.model().features if f.kind == "hole")
        p1, p2 = dwg.at("plan", 0, 0, 0), dwg.at("plan", 20, 0, 0)
        with pytest.warns(DeprecationWarning, match="Drawing.place_dim"):
            dwg.place_dim(p1, p2, "above", "plan", dwg.draft, name="mine", feature=hole)
        assert "mine" in dwg.annotations_of(hole)

    def test_drop_hole_clears_its_callout(self):
        # #408 A: a hole owns its ⌀ callout, so drop clears it (not just centre marks).
        dwg = build_drawing(_holed_plate())
        hole = next(f for f in dwg.model().features if f.kind == "hole")
        owned = dwg.annotations_of(hole)
        assert any(n.startswith("hc_") for n in owned), "hole should own its callout"
        assert any(n.startswith("hc_") for n in dwg.drop(hole))

    def test_drop_pattern_clears_its_furniture(self):
        # #408 B: a pattern owns its callout AND its centre line / pitch furniture.
        import math

        from build123d import Box, Cylinder, Pos

        part = Box(120, 120, 20)
        for k in range(6):
            ang = math.radians(60 * k)
            part -= Pos(35 * math.cos(ang), 35 * math.sin(ang), 0) * Cylinder(4, 20)
        dwg = build_drawing(part)
        pat = next(f for f in dwg.model().features if f.kind == "pattern")
        owned = dwg.annotations_of(pat)
        assert any(n.startswith("hc_") for n in owned) and any(n.startswith("bc_") for n in owned)
        removed = set(dwg.drop(pat))
        assert removed == set(owned)

    def test_balloon_is_owned_by_its_hole(self):
        # #408 C: a balloon (which carries a recognition hole) attributes to the IR feature.
        from draftwright.annotations._common import PlacementContext

        dwg = build_drawing(_holed_plate())
        a = dwg._analysis
        hole_obj = a.holes[0]
        # the attribution index lives on the run ctx (#639/#699); build one to query it
        feat = PlacementContext(part_model=dwg.model()).feature_of_hole_at(hole_obj.location)
        assert feat is not None
        dwg.add_balloons("plan", [("A", 0, hole_obj)])
        bln = next(n for n in dwg.annotations() if n.startswith("balloon_"))
        assert bln in dwg.annotations_of(feat)

    def test_drop_is_complete_for_a_multi_feature_prismatic_part(self):
        # #408 audit: holes + bolt pattern + slot — drop(feature) leaves nothing behind.
        import math

        from build123d import Box, Cylinder, Mode, Pos

        part = Box(140, 120, 20) - Pos(-45, 0, 0) * Box(24, 8, 30, mode=Mode.SUBTRACT)
        for k in range(6):
            ang = math.radians(60 * k)
            part -= Pos(40 + 25 * math.cos(ang), 25 * math.sin(ang), 0) * Cylinder(4, 20)
        _assert_drop_is_complete(build_drawing(part))

    def test_drop_is_complete_for_a_turned_part(self):
        # #408 audit: a turned stepped shaft (steps + OD).
        from build123d import Cylinder

        shaft = Cylinder(20, 30) + Cylinder(12, 20).translate((0, 0, 25))
        _assert_drop_is_complete(build_drawing(shaft))

    def test_drop_step_clears_its_diameter_callout(self):
        # #412: a turned step owns its ⌀ callout (m_dia_) — the spec-flattening render pass
        # now carries the feature. Without it, m_dia was feature=None and drop left it.
        from build123d import Cylinder

        dwg = build_drawing(Cylinder(20, 30) + Cylinder(12, 20).translate((0, 0, 25)))
        mdia = [n for n in dwg.annotations() if n.startswith("m_dia")]
        assert mdia, "expected turned-diameter callouts"
        for n in mdia:
            assert dwg.registry.feature_of(n) is not None, f"{n} unowned (#412 regression)"
        owner = dwg.registry.feature_of(mdia[0])
        assert mdia[0] in dwg.drop(owner)

    def test_drop_step_clears_its_diameter_callout_x_turned(self):
        # #413 review: cover the X-row path (m_dia_x, _diameter_row_below) too — the Z-shaft
        # above only exercises the m_dia_z column path.
        from build123d import Cylinder, Rot

        shaft = Rot(0, 90, 0) * (Cylinder(20, 30) + Cylinder(12, 20).translate((0, 0, 25)))
        dwg = build_drawing(shaft)
        mdia_x = [n for n in dwg.annotations() if n.startswith("m_dia_x")]
        assert mdia_x, "expected X-turned diameter callouts (row path)"
        for n in mdia_x:
            assert dwg.registry.feature_of(n) is not None, f"{n} unowned (#412 row path)"

    def test_drop_is_complete_for_side_drilled_holes(self):
        # #410 review F1: a side-drilled (X/Y-axis) hole's location dims (dim_loc_side/
        # front/z) must be owned so drop clears them — they route through
        # _locate_off_axis_holes, which now tags via place_strip_candidates(features=).
        from build123d import Box, Cylinder, Pos, Rot

        part = (
            Box(120, 90, 40)
            - Pos(0, 0, 5) * Rot(0, 90, 0) * Cylinder(5, 120)  # X-axis bore
            - Pos(0, 0, -8) * Rot(90, 0, 0) * Cylinder(5, 90)  # Y-axis bore
        )
        dwg = build_drawing(part)
        side_loc = [n for n in dwg.annotations() if n.startswith("dim_loc_")]
        assert side_loc, "expected side-drilled location dims"
        # Directly: each (distinct-offset) side-drilled dim is owned by its hole — the F1
        # fix. Without it these were feature=None and drop(hole) left them behind.
        for n in side_loc:
            assert dwg.registry.feature_of(n) is not None, f"{n} unowned (F1 regression)"
        _assert_drop_is_complete(dwg)

    def test_dimension_rejects_non_orthographic_view(self):
        # #407 review: a linear dim on the foreshortening iso view mislabels the length.
        from build123d import Cylinder

        dwg = build_drawing(Cylinder(20, 30) + Cylinder(12, 20).translate((0, 0, 25)))
        step = next(f for f in dwg.model().features if f.kind == "step")
        with pytest.raises(ValueError, match="front"):
            dwg.dimension(step, "length", view="iso")

    def test_dimension_unknown_view_raises_valueerror_not_keyerror(self):
        # #407 review: a bad view= must be a clean ValueError, not a bare KeyError.
        from build123d import Cylinder

        dwg = build_drawing(Cylinder(20, 30) + Cylinder(12, 20).translate((0, 0, 25)))
        step = next(f for f in dwg.model().features if f.kind == "step")
        with pytest.raises(ValueError):
            dwg.dimension(step, "length", view="back")

    def test_dimension_ambiguous_kind_requires_role(self):
        # #407 review: an envelope exposes width/height/depth all as 'length' — a bare
        # kind must raise (not silently pick width), and role= must disambiguate.
        from build123d import Box

        dwg = build_drawing(Box(40, 30, 10))
        env = next((f for f in dwg.model().features if f.kind == "envelope"), None)
        assert env is not None
        roles = sorted(q.role for q in env.parameters() if q.kind == "length" and q.span)
        assert len(roles) > 1
        with pytest.raises(ValueError, match="role="):
            dwg.dimension(env, "length")
        name = dwg.dimension(env, "length", role=roles[0])
        assert name in dwg.annotations_of(env)

    def test_shared_coordinate_location_dim_is_unowned(self):
        # #398c review (#406): a single location dim shared by two DISTINCT holes at the
        # same X belongs to neither — it must be unowned so drop(one) can't over-strip the
        # dim the sibling still needs.
        from build123d import Box, Cylinder, Pos

        part = (
            Box(80, 60, 20) - Pos(30, -20, 0) * Cylinder(6, 20) - Pos(30, 20, 0) * Cylinder(4, 20)
        )
        dwg = build_drawing(part)
        holes = [f for f in dwg.model().features if f.kind == "hole"]
        assert len(holes) == 2  # distinct specs → not grouped
        locx = {n for n in dwg.annotations() if n.startswith("m_locx")}
        assert locx, "expected a shared X-location dim"
        # Neither hole owns the shared X dim...
        for h in holes:
            assert not (locx & set(dwg.annotations_of(h)))
        # ...so dropping one leaves it in place for the other.
        dwg.drop(holes[0])
        assert locx <= set(dwg.annotations()), "shared location dim was over-stripped by drop"

    def test_drop_feature_with_no_annotations_is_noop(self):
        dwg = build_drawing(_holed_plate())
        env = next(f for f in dwg.model().features if f.kind == "envelope")
        owned = set(dwg.annotations_of(env))
        assert owned, "the envelope must own its overall dimensions"
        unrelated = set(dwg.annotations()) - owned
        assert set(dwg.drop(env)) == owned
        assert set(dwg.annotations()) == unrelated
        assert dwg.annotations_of(env) == {}
        assert dwg.drop(env) == []

    def test_manual_add_records_feature_provenance(self):
        from build123d_drafting import CenterMark

        dwg = build_drawing(_holed_plate())
        hole = next(f for f in dwg.model().features if f.kind == "hole")
        dwg._add(CenterMark((0, 0, 0), 3.0, dwg.draft), "my_mark", view="plan", feature=hole)
        assert "my_mark" in dwg.annotations_of(hole)

    def test_provenance_survives_repair(self):
        # Snapshot/restore (the repair undo path) must preserve feature ownership.
        dwg = build_drawing(_holed_plate())
        hole = next(f for f in dwg.model().features if f.kind == "hole")
        before = set(dwg.annotations_of(hole))
        dwg.repair()
        assert set(dwg.annotations_of(hole)) == before

    def test_model_structurally_equivalent_across_step_and_b123d_input(self, tmp_path):
        # D5 / the convergence property (ADR 4 (was 0001 Amendment 1)): a STEP import re-tessellates
        # the solid, so coordinates differ — but the DETECTED feature structure must be the
        # same whether the input was a build123d object or a STEP file of that object.
        part = _holed_plate()
        step = tmp_path / "plate.step"
        export_step(part, str(step))
        m_obj = build_drawing(part).model()
        m_step = build_drawing(str(step)).model()
        assert _model_signature(m_obj) == _model_signature(m_step), (
            f"model diverged across provenance: obj={_model_signature(m_obj)} "
            f"step={_model_signature(m_step)}"
        )












# ---------------------------------------------------------------------------
# Issue #29: lint findings carry a suggested-fix code snippet
# ---------------------------------------------------------------------------
