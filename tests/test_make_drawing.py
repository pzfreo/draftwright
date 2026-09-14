"""Tests for draftwright.make_drawing."""

import warnings
from pathlib import Path

import pytest
from _drawing_helpers import ink_crossings_named as _ink_crossings_named
from _kernel import B123D_GE_011, SKIP_011
from _parts import holed_plate as _holed_plate
from _parts import multi_hole_plate as _multi_hole_plate
from build123d import Box, Cylinder, Pos, export_step

from draftwright import build_drawing, make_drawing
from draftwright.linting import LintIssue

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

    def test_annotations_of_returns_a_features_centermarks_and_locations(self, holed_plate_dwg):
        # A hole owns its centre mark(s), location dims (#398c, corridor-placed
        # m_locx/m_locy), and its ⌀ callout (#408, hc_).
        dwg = holed_plate_dwg
        hole = next(f for f in dwg.model().features if f.kind == "hole")
        owned = dwg.annotations_of(hole)
        assert any(n.startswith("m_cm") for n in owned), "hole should own its centre mark(s)"
        assert all(n.startswith(("m_cm", "m_loc", "hc_")) for n in owned)

    def test_drop_removes_a_features_annotations(self):
        dwg = build_drawing(_holed_plate())
        hole = next(f for f in dwg.model().features if f.kind == "hole")
        names = set(dwg.annotations_of(hole))
        removed = dwg.drop(hole)
        assert set(removed) == names
        assert dwg.annotations_of(hole) == {}
        for n in names:
            assert n not in dwg.annotations()  # gone from the registry + render list

    def test_drop_removes_all_slot_dims(self):
        # #398c: slot dims flow through the ADR 2 (was 0009) corridor; provenance now threads it,
        # so drop(slot) removes the whole set (length + width + position).
        from build123d import Box, Mode, Pos

        part = Box(80, 60, 20) - Pos(0, 0, 0) * Box(24, 8, 30, mode=Mode.SUBTRACT)
        dwg = build_drawing(part)
        slot = next(f for f in dwg.model().features if f.kind == "slot")
        owned = set(dwg.annotations_of(slot))
        assert owned and all(n.startswith("m_slot") for n in owned)
        assert set(dwg.drop(slot)) == owned
        assert dwg.annotations_of(slot) == {}

    def test_dimension_adds_and_tags_a_feature(self):
        # #398e: the add verb — dimension a span-carrying param (a turned step's length),
        # tagged with the feature so it pairs with drop/annotations_of.
        from build123d import Cylinder

        shaft = Cylinder(20, 30) + Cylinder(12, 20).translate((0, 0, 25))
        dwg = build_drawing(shaft)
        step = next(f for f in dwg.model().features if f.kind == "step")
        name = dwg.dimension(step, "length")
        assert isinstance(name, str)
        assert name in dwg.annotations()
        assert name in dwg.annotations_of(step)
        assert name in set(dwg.drop(step))
        assert name not in dwg.annotations()  # drop removed it

    def test_dimension_rejects_callout_param(self):
        # A hole/step diameter is a leader callout, not a linear dim — clear ValueError.
        from build123d import Cylinder

        dwg = build_drawing(Cylinder(20, 30) + Cylinder(12, 20).translate((0, 0, 25)))
        step = next(f for f in dwg.model().features if f.kind == "step")
        with pytest.raises(ValueError, match="callout"):
            dwg.dimension(step, "diameter")

    def test_dimension_slot_derives_span_and_tags(self):
        # #411: a slot's dims are value-only; dimension() derives the span from the slot
        # geometry (role= disambiguates length vs width), tags + drops it.
        from build123d import Box, Mode, Pos

        part = Box(80, 60, 20) - Pos(0, 0, 0) * Box(24, 8, 30, mode=Mode.SUBTRACT)
        dwg = build_drawing(part)
        slot = next(f for f in dwg.model().features if f.kind == "slot")
        nl = dwg.dimension(slot, "length", role="slot_length")
        assert dwg.get_annotation(nl).label == "24"  # the long-axis span
        nw = dwg.dimension(slot, "length", role="slot_width")
        assert dwg.get_annotation(nw).label == "8"  # the width-axis span
        assert {nl, nw} <= set(dwg.annotations_of(slot))
        assert nl in dwg.drop(slot)

    def test_deferred_dimension_rebuilds_prismatic_step_height_ladder(self):
        # StepLevelFeature's rungs are a correlated ladder, not independent spans. The
        # deferred dimension intent regenerates the auto-pass ladder on a detect-only build.
        from build123d import Box, Pos

        part = Box(40, 12, 40) - Pos(10, 0, 20) * Box(20, 12, 20)
        dwg = build_drawing(part, auto_dims=False)
        step_level = next(f for f in dwg.model().features if f.kind == "step_level")

        with dwg.deferred():
            dwg.dimension(step_level, "length", role="step_height")

        assert any(n.startswith("dim_step") for n in dwg.annotations())
        assert dwg._intents == []
        # NOT `dim_height`. This asserted the opposite until #934: the drain handed
        # `render_height_ladder` the whole compiled plan, so a step-height intent also drew
        # the overall height. One intent draws one thing — the overall height has its own
        # verb (`overall_height()`), and the conflation meant commenting either line out of a
        # generated script need not have removed what it named.
        #
        # No replay regression rides on this. A part WITH an `EnvelopeFeature` — like this one
        # — emits `dimension(f, "length", role="height")`, which sets `explicit_envelope_height`
        # and takes the overall height out of the compile entirely, so the ladder never
        # carried it there anyway. A part WITHOUT one now emits `dwg.overall_height()`.
        assert "dim_height" not in dwg.annotations()

    def test_callout_adds_a_hole_leader_and_round_trips(self):
        # #414 / #400 Ph2: the callout add verb — detect-only build, then add the hole's
        # ø leader explicitly; it is a leader-attached callout, tagged, and drops.
        from build123d_drafting.helpers import Leader

        dwg = build_drawing(_holed_plate(), auto_dims=False)
        hole = next(f for f in dwg.model().features if f.kind == "hole")
        name = dwg.callout(hole)
        assert name.startswith("hc_")
        assert name in dwg.annotations() and name in dwg.annotations_of(hole)
        assert isinstance(dwg.get_annotation(name), Leader)  # funnels through callout_from_spec
        assert name in set(dwg.drop(hole))
        assert name not in dwg.annotations()  # drop removed it

    def test_callout_rejects_a_non_callout_feature(self):
        # A slot has no ø leader callout (a step/boss now does, #419) — clear ValueError
        # pointing at dimension().
        from build123d import Box, Mode, Pos

        part = Box(80, 60, 20) - Pos(0, 0, 0) * Box(24, 8, 30, mode=Mode.SUBTRACT)
        dwg = build_drawing(part, auto_dims=False)
        slot = next(f for f in dwg.model().features if f.kind == "slot")
        with pytest.raises(ValueError, match="hole"):
            dwg.callout(slot)

    def test_callout_carries_a_pattern_count(self):
        # A bolt circle → one counted callout for the whole pattern, tagged to the pattern.
        import math

        from build123d import Box, Cylinder, Pos

        part = Box(120, 120, 20)
        for k in range(6):
            ang = math.radians(60 * k)
            part -= Pos(35 * math.cos(ang), 35 * math.sin(ang), 0) * Cylinder(4, 20)
        dwg = build_drawing(part, auto_dims=False)
        pat = next(f for f in dwg.model().features if f.kind == "pattern")
        name = dwg.callout(pat)
        assert name in dwg.annotations_of(pat)
        assert name in set(dwg.drop(pat))

    def test_callout_rejects_a_foreign_feature(self):
        # #414 review: a hole from a *different* build is value-similar but not identity-equal,
        # so callout() points at model().features rather than the misleading "exposes none".
        dwg = build_drawing(_holed_plate(), auto_dims=False)
        other = build_drawing(_holed_plate(), auto_dims=False)
        foreign = next(f for f in other.model().features if f.kind == "hole")
        with pytest.raises(ValueError, match="not from this drawing"):
            dwg.callout(foreign)

    def test_callout_rejects_a_non_ortho_view(self):
        # #414 review: "iso" is a rendered view (in _coords) but not a hole-callout view —
        # it must raise a clean ValueError, not a raw KeyError from the placement dict.
        dwg = build_drawing(_holed_plate(), auto_dims=False)
        hole = next(f for f in dwg.model().features if f.kind == "hole")
        with pytest.raises(ValueError, match="hole-callout view"):
            dwg.callout(hole, view="iso")

    def test_locate_adds_position_dims_and_round_trips(self):
        # #418 / #400 Ph2: locate() places datum-referenced X/Y position dims for a
        # Z-hole, tagged + droppable. Centre ø6 at (0,0) vs datum at bbox-min (-40,-30)
        # → X offset 40, Y offset 30.
        from build123d_drafting.helpers import Dimension

        dwg = build_drawing(_holed_plate(), auto_dims=False)
        centre = next(f for f in dwg.model().features if f.kind == "hole" and len(f.members) == 1)
        names = dwg.locate(centre)
        assert len(names) == 2
        assert all(isinstance(dwg.get_annotation(n), Dimension) for n in names)
        assert set(names) <= set(dwg.annotations_of(centre))
        labels = {dwg.get_annotation(n).label for n in names}
        assert labels == {"40", "30"}
        assert set(names) <= set(dwg.drop(centre))
        assert not dwg.annotations_of(centre)  # drop removed them all

    def test_locate_axes_filter(self):
        # axes=("x",) emits only the plan-X position dim.
        dwg = build_drawing(_holed_plate(), auto_dims=False)
        centre = next(f for f in dwg.model().features if f.kind == "hole" and len(f.members) == 1)
        names = dwg.locate(centre, axes=("x",))
        assert len(names) == 1 and names[0].startswith("m_locx")
        assert dwg.get_annotation(names[0]).label == "40"

    def test_locate_pin_marks_live_location_dims(self):
        # #511 slice 1: a user location edit can be declared pinned at creation time, so
        # later repair/finalize work sees the same pin state as if pin(name) ran after.
        dwg = build_drawing(_holed_plate(), auto_dims=False)
        centre = next(f for f in dwg.model().features if f.kind == "hole" and len(f.members) == 1)
        names = dwg.locate(centre, pin=True)
        assert names
        assert set(names) <= dwg.registry.pinned_names()

    def test_locate_dedups_coincident_members(self):
        # The 4 corner ø10 holes group into one HoleFeature (X∈{25,-25}, Y∈{20,-20});
        # locate() places one dim per distinct axis position, not one per member.
        dwg = build_drawing(_holed_plate(), auto_dims=False)
        corners = next(f for f in dwg.model().features if f.kind == "hole" and len(f.members) == 4)
        names = dwg.locate(corners)
        labels = sorted(dwg.get_annotation(n).label for n in names)
        # X offsets 25→65 / -25→15; Y offsets 20→50 / -20→10 — four distinct dims.
        assert labels == ["10", "15", "50", "65"]

    def test_locate_rejects_side_drilled_feature(self):
        # A side-drilled (X-axis) bore has no plan location dim — clear ValueError.
        from build123d import Box, Cylinder, Pos, Rot

        part = Box(120, 90, 40) - Pos(0, 0, 5) * Rot(0, 90, 0) * Cylinder(5, 120)
        dwg = build_drawing(part, auto_dims=False)
        bore = next(f for f in dwg.model().features if f.kind == "hole" and f.frame.axis == "x")
        with pytest.raises(ValueError, match="side-drilled"):
            dwg.locate(bore)

    def test_locate_rejects_a_linear_feature(self):
        # A turned step is not a hole/pattern — point at dimension().
        from build123d import Cylinder

        dwg = build_drawing(
            Cylinder(20, 30) + Cylinder(12, 20).translate((0, 0, 25)), auto_dims=False
        )
        step = next(f for f in dwg.model().features if f.kind == "step")
        with pytest.raises(ValueError, match="dimension"):
            dwg.locate(step)

    def test_locate_rejects_a_foreign_feature(self):
        # A hole from a different build is not identity-equal → point at model().features.
        dwg = build_drawing(_holed_plate(), auto_dims=False)
        other = build_drawing(_holed_plate(), auto_dims=False)
        foreign = next(f for f in other.model().features if f.kind == "hole")
        with pytest.raises(ValueError, match="not from this drawing"):
            dwg.locate(foreign)

    def test_furniture_adds_hole_centre_mark(self):
        # #419: furniture() places a hole's centre mark(s), tagged + droppable.
        from build123d_drafting.helpers import CenterMark

        dwg = build_drawing(_holed_plate(), auto_dims=False)
        centre = next(f for f in dwg.model().features if f.kind == "hole" and len(f.members) == 1)
        names = dwg.furniture(centre)
        assert names and all(n.startswith("m_cm") for n in names)
        assert all(isinstance(dwg.get_annotation(n), CenterMark) for n in names)
        assert set(names) <= set(dwg.annotations_of(centre))
        assert set(names) <= set(dwg.drop(centre))

    def test_furniture_adds_pattern_centre_cross_and_round_trips(self):
        # A bolt circle's furniture is member centre marks + the bc_ centre-cross.
        import math

        from build123d import Box, Cylinder, Pos

        part = Box(120, 120, 20)
        for k in range(6):
            ang = math.radians(60 * k)
            part -= Pos(35 * math.cos(ang), 35 * math.sin(ang), 0) * Cylinder(4, 20)
        dwg = build_drawing(part, auto_dims=False)
        pat = next(f for f in dwg.model().features if f.kind == "pattern")
        names = dwg.furniture(pat)
        assert any(n.startswith("bc_") for n in names)
        assert set(names) <= set(dwg.annotations_of(pat))
        assert set(names) <= set(dwg.drop(pat))
        assert not dwg.annotations_of(pat)  # drop removed them all

    def test_furniture_grid_emits_pitch_dims(self):
        # A rectangular grid's furniture includes both (n-1)× pitch dims.
        from build123d import Box, Cylinder, Pos
        from build123d_drafting.helpers import Dimension

        part = Box(140, 70, 12)
        for r in range(2):
            for c in range(4):
                part -= Pos(-37.5 + c * 25, -10 + r * 20, 0) * Cylinder(4, 12)
        dwg = build_drawing(part, auto_dims=False)
        grid = next(f for f in dwg.model().features if f.kind == "pattern")
        names = dwg.furniture(grid)
        pitch = [n for n in names if n.startswith("dim_pitch_")]
        assert len(pitch) == 2
        assert all(isinstance(dwg.get_annotation(n), Dimension) for n in pitch)
        assert set(names) <= set(dwg.annotations_of(grid))
        assert set(names) <= set(dwg.drop(grid))

    def test_furniture_rejects_a_linear_feature(self):
        # A turned step is not a hole/pattern → point at dimension().
        from build123d import Cylinder

        dwg = build_drawing(
            Cylinder(20, 30) + Cylinder(12, 20).translate((0, 0, 25)), auto_dims=False
        )
        step = next(f for f in dwg.model().features if f.kind == "step")
        with pytest.raises(ValueError, match="dimension"):
            dwg.furniture(step)

    def test_callout_adds_a_turned_step_diameter(self):
        # #419: callout() extended to a turned step's ø leader (Z-turned → column left).
        from build123d import Cylinder
        from build123d_drafting.helpers import Leader

        dwg = build_drawing(
            Cylinder(20, 30) + Cylinder(12, 20).translate((0, 0, 25)), auto_dims=False
        )
        step = next(f for f in dwg.model().features if f.kind == "step")
        name = dwg.callout(step)
        assert name.startswith("m_dia")
        assert isinstance(dwg.get_annotation(name), Leader)
        assert name in dwg.annotations_of(step)
        assert name in dwg.drop(step)

    def test_callout_step_x_turned_uses_row_path(self):
        # The X-turned path places m_dia_x in the row below the front view.
        from build123d import Cylinder, Rot
        from build123d_drafting.helpers import Leader

        shaft = Rot(0, 90, 0) * (Cylinder(20, 30) + Cylinder(12, 20).translate((0, 0, 25)))
        dwg = build_drawing(shaft, auto_dims=False)
        step = next(f for f in dwg.model().features if f.kind == "step")
        name = dwg.callout(step)
        assert name.startswith("m_dia_x")
        assert isinstance(dwg.get_annotation(name), Leader)
        assert name in dwg.drop(step)

    def test_callout_multiple_step_diameters_do_not_collide(self):
        # #419 review F1: each step gets a DISTINCT m_dia name; a second callout() must
        # not clobber the first's leader or raise a false "no room". X-turned uses the
        # row placer (no occupancy gate), so both leaders always land.
        from build123d import Cylinder, Rot

        shaft = Rot(0, 90, 0) * (
            Cylinder(20, 30)
            + Cylinder(14, 24).translate((0, 0, 27))
            + Cylinder(9, 18).translate((0, 0, 48))
        )
        dwg = build_drawing(shaft, auto_dims=False)
        steps = [f for f in dwg.model().features if f.kind == "step"]
        assert len(steps) >= 2
        names = [dwg.callout(s) for s in steps[:2]]
        assert len(set(names)) == 2, f"expected distinct names, got {names}"
        for s, n in zip(steps[:2], names, strict=False):
            assert n in dwg.annotations() and n in dwg.annotations_of(s)
        # dropping the first step leaves the second's leader intact (no clobber)
        dwg.drop(steps[0])
        assert names[1] in dwg.annotations()

    def test_section_reproduces_the_auto_section(self):
        # #420: section() adds the A–A that a counterbored hole triggers, on a
        # detect-only build (the auto-pass would draw it, but auto_dims=False skips it).
        from build123d import Box, Cylinder, Pos

        part = Box(60, 40, 20) - Cylinder(4, 30) - Pos(0, 0, 2) * Cylinder(7, 20)
        dwg = build_drawing(part, auto_dims=False)
        names = dwg.section()
        assert "section_caption" in names and "section_line" in names
        assert dwg.get_annotation("section_caption").label == "SECTION A–A"

    def test_section_is_a_noop_without_a_trigger(self):
        # A plain through-hole plate warrants no section → honest empty list.
        from build123d import Box, Cylinder, Pos

        part = Box(80, 60, 20) - Pos(20, 0, 0) * Cylinder(4, 30)
        dwg = build_drawing(part, auto_dims=False)
        assert dwg.section() == []

    def test_locate_composes_over_every_feature_without_raising(self):
        # #420 flip fix: locate() returns [] (not ValueError) when a feature's datum ref is
        # deduped/concentric — so the emitted script can call it on every hole/pattern. Here
        # the central hole coincides with the bolt-circle centre, so its ref is deduped.
        import math

        from build123d import Box, Cylinder, Pos

        part = Box(100, 100, 20)
        for k in range(6):
            ang = math.radians(60 * k)
            part -= Pos(30 * math.cos(ang), 30 * math.sin(ang), 0) * Cylinder(3, 20)
        part -= Pos(0, 0, 5) * Cylinder(5, 10)  # central hole on the bolt-circle centre
        dwg = build_drawing(part, auto_dims=False)
        holes = [f for f in dwg.model().features if f.kind in ("hole", "pattern")]
        assert len(holes) >= 2
        results = [dwg.locate(f) for f in holes]  # none may raise
        assert all(isinstance(r, list) for r in results)

    @staticmethod
    def _reconstruct(dwg):
        # The per-feature verb dispatch the imperative emitter used to write. That emitter is
        # gone (#940), so this is no longer a mirror of anything generated — it exercises the
        # `Drawing` edit verbs in-process, which remain a hand-use API (ADR 4 (was 0016)).
        for f in dwg.model().features:
            if f.kind in ("hole", "pattern"):
                dwg.callout(f)
                if f.frame.axis == "z":  # locate() is Z-axis only (side-drilled → auto-pass)
                    dwg.locate(f)
                dwg.furniture(f)
            elif f.kind in ("step", "boss"):
                if f.frame.axis in ("x", "z"):  # callout() places X/Z-turned diameters only
                    dwg.callout(f)
            elif f.kind in ("pocket_pattern", "slot_pattern"):
                dwg.callout(f)  # grouped callout + pitch (no locate/furniture); #841
                continue
            elif f.kind == "step_level":
                dwg.dimension(f, "length", role="step_height")
                continue
            for p in f.parameters():
                if p.span is not None or f.kind == "slot":
                    dwg.dimension(f, p.kind, role=p.role)
        dwg.section()

    def test_deferred_reconstruction_avoids_duplicate_prismatic_step_height(self):
        # The envelope owns overall height when the generated reconstruction records it
        # explicitly; the step-level ladder must then emit only the internal rungs.
        from build123d import Box, Pos

        part = Box(60, 12, 40) - Pos(10, 0, 20) * Box(20, 12, 20)
        dwg = build_drawing(part, auto_dims=False)

        with dwg.deferred():
            self._reconstruct(dwg)

        dims = {
            n: getattr(ann, "label", None)
            for n, ann in dwg.iter_annotations()
            if n.startswith(("dim_height", "dim_length", "dim_step"))
        }
        assert "dim_height" not in dims
        assert [name for name, label in dims.items() if label == "40"] == ["dim_length1"]
        assert any(name.startswith("dim_step") for name in dims)

    def test_intent_reconstruction_is_error_free(self):
        # #400 Ph2 soft acceptance: a fully reconstructed prismatic part, after repair(),
        # has no lint ERRORS. Placement WARNINGS from the corridor-free verbs are the
        # documented #424 fidelity gap, not a failure.
        part = (
            Box(80, 60, 12) - Pos(20, 10, 0) * Cylinder(4, 40) - Pos(-20, -10, 0) * Cylinder(4, 40)
        )
        dwg = build_drawing(part, auto_dims=False)
        self._reconstruct(dwg)
        dwg.repair()
        assert dwg.lint_summary()["errors"] == 0, dwg.lint_summary()["by_code"]

    def test_intent_reconstruction_runs_on_a_side_drilled_part(self):
        # #427 review F1: a side-drilled (non-Z) bore is kind="hole" axis!="z". locate()
        # rejects it by contract (#133), so the emitter must NOT emit locate() for it —
        # else the reconstruction crashes. Exercise the (fixed) dispatch: it must not raise.
        from build123d import Box, Cylinder, Pos, Rot

        part = Box(120, 90, 40) - Pos(0, 0, 5) * Rot(0, 90, 0) * Cylinder(5, 120)
        dwg = build_drawing(part, auto_dims=False)
        assert any(f.kind == "hole" and f.frame.axis != "z" for f in dwg.model().features)
        self._reconstruct(dwg)  # must not raise on the side-drilled bore
        dwg.repair()
        assert dwg.lint_summary()["errors"] == 0, dwg.lint_summary()["by_code"]

    def test_intent_reconstruction_runs_on_a_crowded_turned_shaft(self):
        # #427 review F2: callout() on a step/boss must DEGRADE (drop the overflow leader
        # like the auto-pass), not raise "no room" — else a multi-step turned shaft's
        # reconstruction (one callout() per step) aborts. Must run to completion.
        from build123d import Cylinder

        shaft = Cylinder(24, 12)
        for k in range(1, 10):  # 10 stacked, decreasing-radius steps along Z
            shaft += Cylinder(24 - 2 * k, 12).translate((0, 0, 12 * k))
        dwg = build_drawing(shaft, auto_dims=False)
        assert sum(f.kind == "step" for f in dwg.model().features) >= 5
        self._reconstruct(dwg)  # many callout(step) calls — none may raise "no room"
        dwg.repair()  # the script must reach repair(), not abort before it

    def test_intent_reconstruction_comment_drops_exactly_that(self):
        # #400 Ph2 soft acceptance: commenting one verb line drops exactly that annotation.
        # With auto_dims=False nothing is auto-drawn, so omitting callout(f) removes exactly
        # the callout — no double-dimension, no collateral on locate/furniture.
        part = Box(80, 60, 12) - Pos(20, 10, 0) * Cylinder(4, 40)
        full = build_drawing(part, auto_dims=False)
        hole = next(f for f in full.model().features if f.kind == "hole")
        full.callout(hole)
        full.locate(hole)
        full.furniture(hole)
        before = set(full.annotations())

        partial = build_drawing(part, auto_dims=False)
        h2 = next(f for f in partial.model().features if f.kind == "hole")
        partial.locate(h2)  # callout(h2) "commented out"
        partial.furniture(h2)
        dropped = before - set(partial.annotations())
        assert dropped == {n for n in before if n.startswith("hc_")}
        assert dropped, "commenting callout() should drop the hole's leader"

    def test_deferred_verbs_record_intents_without_placing(self):
        # #426 Phase 1: in deferred mode a verb records an Intent and places nothing.
        dwg = build_drawing(_holed_plate(), auto_dims=False)
        base = set(dwg.annotations())  # the detect-only build's title block, no dims
        dwg._defer_intents = True
        hole = next(f for f in dwg.model().features if f.kind == "hole")
        assert dwg.callout(hole) == ""  # nothing placed
        assert dwg.locate(hole) == []
        assert dwg.furniture(hole) == []
        assert len(dwg._intents) == 3
        assert [i.kind for i in dwg._intents] == ["callout", "locate", "furniture"]
        assert set(dwg.annotations()) == base  # recorded, nothing new drawn

    def test_deferred_context_manager_records_then_finalizes(self):
        # #426 Phase 5: `with dwg.deferred()` records inside the block (nothing placed) and
        # runs finalize() on block exit — the record-then-finalize surface the emitter uses.
        dwg = build_drawing(_holed_plate(), auto_dims=False)
        base = set(dwg.annotations())
        hole = next(f for f in dwg.model().features if f.kind == "hole")
        with dwg.deferred():
            dwg.callout(hole)
            dwg.locate(hole)
            dwg.furniture(hole)
            assert set(dwg.annotations()) == base  # still recording inside the block
            assert len(dwg._intents) == 3
        assert dwg._intents == []  # finalize drained them on exit
        assert dwg._defer_intents is False  # mode restored
        assert set(dwg.annotations()) - base  # annotations placed by the batch solve

    def test_deferred_block_keeps_intents_and_skips_finalize_on_raise(self):
        # #426 Phase 5: if the block raises, the recorded intents are left intact and
        # finalize() is NOT run — the error surfaces cleanly and a retry can re-drain.
        dwg = build_drawing(_holed_plate(), auto_dims=False)
        base = set(dwg.annotations())
        hole = next(f for f in dwg.model().features if f.kind == "hole")
        with pytest.raises(RuntimeError, match="boom"):
            with dwg.deferred():
                dwg.callout(hole)
                raise RuntimeError("boom")
        assert [i.kind for i in dwg._intents] == ["callout"]  # kept, not drained
        assert set(dwg.annotations()) == base  # finalize skipped — nothing placed
        assert dwg._defer_intents is False  # mode still restored (finally)

    def test_deferred_reconstruction_is_error_free_and_drained(self):
        # #426 Phase 5 soft acceptance: a full reconstruction through the deferred CM
        # batch-solves to a lint-error-free sheet and drains every recorded intent.
        part = (
            Box(80, 60, 12) - Pos(20, 10, 0) * Cylinder(4, 40) - Pos(-20, -10, 0) * Cylinder(4, 40)
        )
        dwg = build_drawing(part, auto_dims=False)
        with dwg.deferred():
            self._reconstruct(dwg)  # records every intent inside the block
            assert dwg._intents, "verbs must record inside the deferred block"
        assert dwg._intents == []  # finalize drained on exit
        dwg.repair()
        assert dwg.lint_summary()["errors"] == 0, dwg.lint_summary()["by_code"]

    def test_deferred_reconstruction_lint_no_worse_than_auto_pass(self):
        # #426 acceptance: a deferred reconstruction is no worse than the auto-pass — the
        # recorded intents route through the same batch solvers, so lint score is >= the
        # auto drawing's (faithful, not merely runnable).
        part = (
            Box(80, 60, 12) - Pos(20, 10, 0) * Cylinder(4, 40) - Pos(-20, -10, 0) * Cylinder(4, 40)
        )
        auto = build_drawing(part).lint_summary()
        recon = build_drawing(part, auto_dims=False)
        with recon.deferred():
            self._reconstruct(recon)
        recon.repair()
        got = recon.lint_summary()
        assert got["errors"] == 0
        assert got["score"] >= auto["score"], (got["by_code"], auto["by_code"])

    def test_finalize_replay_equals_live_placement(self):
        # #426 Phase 1: record-then-finalize == placing live (identical annotations).
        part = Box(80, 60, 12) - Pos(20, 10, 0) * Cylinder(4, 40)

        live = build_drawing(part, auto_dims=False)
        h = next(f for f in live.model().features if f.kind == "hole")
        live.callout(h)
        live.locate(h)
        live.furniture(h)

        deferred = build_drawing(part, auto_dims=False)
        base = set(deferred.annotations())
        deferred._defer_intents = True
        h2 = next(f for f in deferred.model().features if f.kind == "hole")
        deferred.callout(h2)
        deferred.locate(h2)
        deferred.furniture(h2)
        assert set(deferred.annotations()) == base  # nothing placed yet
        deferred.finalize()
        assert deferred.annotations() == live.annotations()

    def test_finalize_is_idempotent(self):
        # #426 Phase 1: finalize() twice == once — idempotent via the empty-list early-out.
        dwg = build_drawing(_holed_plate(), auto_dims=False)
        dwg._defer_intents = True
        h = next(f for f in dwg.model().features if f.kind == "hole")
        dwg.callout(h)
        dwg.furniture(h)
        dwg.finalize()
        once = set(dwg.annotations())
        dwg.finalize()
        assert set(dwg.annotations()) == once

    def test_finalize_is_a_noop_on_the_live_path(self):
        # #426 Phase 1: the default (non-deferred) build records nothing → finalize no-ops,
        # so the auto-pass / live-verb path is unchanged.
        dwg = build_drawing(_holed_plate())  # auto_dims=True
        assert dwg._defer_intents is False and dwg._intents == []
        before = set(dwg.annotations())
        dwg.finalize()
        assert set(dwg.annotations()) == before

    def test_export_triggers_finalize(self):
        # #426 Phase 1: export() drains recorded intents (calls finalize) before writing.
        import tempfile
        from pathlib import Path

        dwg = build_drawing(_holed_plate(), auto_dims=False)
        base = set(dwg.annotations())
        dwg._defer_intents = True
        h = next(f for f in dwg.model().features if f.kind == "hole")
        dwg.callout(h)
        assert set(dwg.annotations()) == base  # deferred — nothing placed
        with tempfile.TemporaryDirectory() as d:
            dwg.export(str(Path(d) / "x"), formats=("svg", "dxf"))
        assert dwg.annotations()  # finalize ran during export → the callout got placed

    def test_finalize_drains_a_second_batch(self):
        # #428 review: record → finalize → record-more → finalize drains each batch —
        # idempotency is list-draining only, so a second batch is not blocked.
        dwg = build_drawing(_holed_plate(), auto_dims=False)
        dwg._defer_intents = True
        hole = next(f for f in dwg.model().features if f.kind == "hole")
        dwg.callout(hole)
        dwg.finalize()
        after_first = set(dwg.annotations())
        dwg.furniture(hole)  # a second batch recorded after the first finalize
        dwg.finalize()
        assert set(dwg.annotations()) > after_first  # the second batch placed too

    def test_finalize_is_resilient_to_a_raising_intent(self):
        # #428 review: an intent that raises at replay surfaces the error and leaves the
        # remaining intents recorded (not silently dropped), and does not brick the drawing.
        dwg = build_drawing(_holed_plate(), auto_dims=False)
        dwg._defer_intents = True
        hole = next(f for f in dwg.model().features if f.kind == "hole")
        dwg.callout(hole)  # ok
        dwg.dimension(hole, "diameter")  # a hole ø is a callout → raises at replay
        dwg.furniture(hole)  # ok — must survive the raise
        with pytest.raises(ValueError, match="callout"):
            dwg.finalize()
        # the raise leaves everything not-yet-placed recorded — nothing silently dropped
        kinds = [i.kind for i in dwg._intents]
        assert "dimension" in kinds and "furniture" in kinds

    def test_finalize_routes_locations_through_the_corridor_dedup(self):
        # #426 Phase 2a: two DISTINCT holes sharing an X. The live path places a duplicate
        # m_locx (each locate() is independent); finalize routes them through the real
        # ADR 2 (was 0009) corridor solve, which dedups the coincident X span to ONE dim — matching
        # the auto-pass. The crossing-free / dedup win.
        part = (
            Box(100, 80, 20) - Pos(20, 25, 0) * Cylinder(4, 30) - Pos(20, -25, 0) * Cylinder(6, 30)
        )

        live = build_drawing(part, auto_dims=False)
        for h in (f for f in live.model().features if f.kind == "hole"):
            live.locate(h)
        live_locx = [n for n in live.annotations() if n.startswith("m_locx")]

        deferred = build_drawing(part, auto_dims=False)
        deferred._defer_intents = True
        for h in (f for f in deferred.model().features if f.kind == "hole"):
            deferred.locate(h)
        deferred.finalize()
        fin_locx = [n for n in deferred.annotations() if n.startswith("m_locx")]

        assert len(fin_locx) < len(live_locx)  # corridor deduped the coincident X=20 span
        auto = build_drawing(part)  # auto_dims=True — the reference the corridor matches
        assert len(fin_locx) == len([n for n in auto.annotations() if n.startswith("m_locx")])

    def test_finalize_routes_pinned_locate_as_corridor_candidate(self):
        # #511 slice 1: a deferred user locate(pin=True) is not hand-added after layout.
        # It routes through render_locations' corridor candidates and pins the resulting
        # names after the shared solve chooses legal positions.
        dwg = build_drawing(_holed_plate(), auto_dims=False)
        hole = next(f for f in dwg.model().features if f.kind == "hole" and len(f.members) == 1)
        with dwg.deferred():
            dwg.locate(hole, pin=True)
        locs = {n for n in dwg.annotations_of(hole) if n.startswith("m_loc")}
        assert locs
        assert locs <= dwg.registry.pinned_names()
        assert dwg._intents == []

    def test_finalize_pins_shared_location_when_later_ref_requested_pin(self):
        # #511 review: render_locations dedups same-coordinate refs before candidate
        # creation. The pin bit must survive that dedup even when the pinned feature is
        # not the first representative chosen for the shared dimension.
        part = (
            Box(100, 80, 20) - Pos(20, 25, 0) * Cylinder(4, 30) - Pos(20, -25, 0) * Cylinder(6, 30)
        )
        dwg = build_drawing(part, auto_dims=False)
        holes = [f for f in dwg.model().features if f.kind == "hole"]
        dwg._defer_intents = True
        dwg.locate(holes[0])
        dwg.locate(holes[1], pin=True)
        dwg.finalize()
        shared_x = {
            n for n in dwg.annotations() if n.startswith("m_locx") and dwg.get_annotation(n).label
        }
        assert shared_x
        assert shared_x <= dwg.registry.pinned_names()

    def test_finalize_routes_pinned_dimension_as_corridor_candidate(self):
        # #511: a deferred user dimension(pin=True) is a feature intent, not a raw
        # page-coordinate placement after layout. It joins the shared strip solve, remains
        # feature-owned, and pins the placed name only after legal placement.
        dwg = build_drawing(Box(80, 50, 20), auto_dims=False)
        env = next(f for f in dwg.model().features if f.kind == "envelope")
        with dwg.deferred():
            dwg.dimension(
                env,
                "length",
                role="width",
                side="below",
                name="user_width",
                slot=12,
                pin=True,
                priority=25,
            )

        assert "user_width" in dwg.annotations_of(env)
        assert dwg.registry.is_pinned("user_width")
        assert dwg.get_annotation("user_width")._dw_spec.side == "below"
        assert dwg.get_annotation("user_width")._dw_spec.distance == 12
        assert dwg._intents == []

    def test_finalize_resolves_an_implicit_side_before_corridor_routing(self, tmp_path):
        # #1382 review: `dimension()` now leaves the natural side unresolved as None. The
        # routing predicate must accept that state so pin/priority reach the shared solve;
        # `_queue_dimension_intent` resolves the side from the exact parameter span.
        dwg = build_drawing(
            Box(80, 50, 20),
            auto_dims=False,
            out=str(tmp_path / "implicit-side"),
            trace=True,
        )
        env = next(f for f in dwg.model().features if f.kind == "envelope")
        with dwg.deferred():
            dwg.dimension(
                env,
                "length",
                role="width",
                name="implicit_side_width",
                pin=True,
                priority=25,
            )

        assert dwg.registry.is_pinned("implicit_side_width")
        assert dwg.get_annotation("implicit_side_width")._dw_spec.side == "above"
        candidate = next(
            candidate
            for solve in dwg.solve_trace.solves
            for candidate in solve["candidates"]
            if candidate["name"] == "implicit_side_width"
        )
        assert candidate["priority"] == 100
        assert candidate["anchored"] is True

    def test_finalize_mixed_corridor_batch_places_each_exactly_once(self):
        # #699 slice b (Codex review): the drain stages now run in the auto-pass's
        # canonical _PASS_SEQUENCE order, which moved the register-only height-ladder /
        # step-position stages AFTER locations. Registration order decides corridor
        # KEY-creation order (= drain order) and same-priority tie-breaks, so the reorder
        # is observable — pin the mixed-batch outcome: locations, a height rung, a
        # shoulder position and a pinned user dimension all competing in one deferred
        # batch must each place exactly once, with no error-severity lint and nothing
        # left recorded.
        part = (
            Box(80, 60, 30) - Pos(0, -20, 7.5) * Box(80, 20, 15) - Pos(20, 15, 0) * Cylinder(3, 30)
        )
        dwg = build_drawing(part, auto_dims=False)
        step = next(f for f in dwg.model().features if f.kind == "step_level")
        hole = next(f for f in dwg.model().features if f.kind == "hole")
        env = next(f for f in dwg.model().features if f.kind == "envelope")
        with dwg.deferred():
            dwg.locate(hole)  # the locations corridor (plan/side above)
            dwg.dimension(step, "length", role="step_height")  # the front-right ladder
            dwg.dimension(step, "length", role="step_position")  # the shoulder corridor
            dwg.dimension(  # a pinned user dim joins the same drain (ADR 2 (was 0012))
                env, "length", role="width", side="below", name="user_width", pin=True
            )
        assert dwg._intents == []  # every routed intent drained
        assert len([n for n in dwg.annotations() if n.startswith("dim_shoulder")]) == 1
        assert [n for n in dwg.annotations() if n.startswith("dim_step")]  # rung placed
        assert {n for n in dwg.annotations_of(hole) if n.startswith("m_loc")}
        assert "user_width" in dwg.annotations() and dwg.registry.is_pinned("user_width")
        assert [i for i in dwg.lint() if i.severity == "error"] == []

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










































class TestEscalation:
    """#93: a too-dense plan view auto-escalates to a hole chart + balloons."""

    def test_dense_part_groups_and_types(self, dense_plate_dwg):
        # Sized honestly for its real annotation footprint (#121, ADR 2 (was 0004)), the
        # sheet grows so the X-location dims + grouped spec-callouts fit — so this
        # moderately-dense plate no longer escalates to a per-hole table + balloon
        # ring (the worse representation for a dense varying-diameter field). It
        # group-and-types instead: spec-group callouts (5× ⌀…) + location dims,
        # lint clean. The table/balloon escalation path remains for parts too
        # dense to fit even that — covered by the CTC-02 slow-tier test.
        dwg = dense_plate_dwg
        ann = dwg.annotations()
        assert "hole_table_plan" not in ann
        assert not any(n.startswith("balloon_") for n in ann)
        assert sum(1 for n in ann if n.startswith("hc_plan")) >= 1  # spec-group callouts
        assert any(n.startswith("m_locx") for n in ann)  # location dims placed, not dropped
        remaining = _ink_crossings_named(
            dwg,
            [(str(value), "2× 14.1") for value in (10, 20, 30, 40, 50, 60)],
        )
        warnings = [i for i in remaining if i.severity in ("warning", "error")]
        assert warnings and {issue.code for issue in warnings} == {
            "hole_pattern_dim_dropped",
            # The #1250 summary of that same drop, at error severity so `passed` cannot be
            # true over a sheet the engine would refuse if it were requested explicitly.
            "plan_incomplete",
        }
        assert all(issue.measurement_ids for issue in warnings)
        # The helper's exact rotated label polygon makes these six previously
        # unmeasurable crossings visible without clipping against the inflated
        # 10.197 × 10.197 mm AABB (#1322 review).

    def test_escalation_clears_density_lint(self, dense_plate_dwg):
        # No callout_dropped / location_ref_dropped / count-mismatch warnings
        # survive once the dense plate is dimensioned — whether by group-and-type
        # (now, on the auto-sized sheet) or by the table escalation it used to need.
        dwg = dense_plate_dwg
        warns = {i.code for i in dwg.lint() if i.severity in ("warning", "error")}
        assert "callout_dropped" not in warns
        assert "location_ref_dropped" not in warns
        assert "feature_count_mismatch" not in warns

    def test_sparse_part_is_not_tabulated(self):
        # A sparse plate dimensions every hole individually — no table, unchanged.
        dwg = build_drawing(_multi_hole_plate())
        assert "hole_table_plan" not in dwg.annotations()
        assert not any(n.startswith("balloon_") for n in dwg.annotations())
        assert any(n.startswith("hc_plan") for n in dwg.annotations())

    def test_wrap_rows_reshapes_into_blocks(self):
        from draftwright.annotate import _wrap_rows

        header = ("T", "D")
        data = [("a", "1"), ("b", "2"), ("c", "3"), ("d", "4"), ("e", "5")]
        wide = _wrap_rows(header, data, 2)  # 5 rows → 3 per block, 2 blocks
        assert wide[0] == ("T", "D", "T", "D")  # header repeated per block
        assert wide[1] == ("a", "1", "d", "4")  # row 0 of each block
        assert wide[3] == ("c", "3", "", "")  # ragged tail padded blank


class TestPatternGroupBalloon:
    """#351 PR-3 (ADR 2 (was 0009 Amdt 1) decision 1, the #348 fix): a dropped ISO
    pattern callout gets ONE balloon tagging the whole pattern, not one per
    member. Exercised directly against the resolver with a synthetic dropped
    Escalation — forcing a real drop needs a part crowded enough that even the
    auto-grown page can't fit it (the CTC-02 slow-tier fixture is the
    naturally-occurring case)."""

    @staticmethod
    def _fake_pattern(count, diameter, origin=(0.0, 0.0, 0.0)):
        from draftwright.model import Frame, HoleFeature, PatternFeature

        member = HoleFeature(
            frame=Frame(origin=origin, axis="z"), diameter=diameter, depth=None, through=True
        )
        # Real recognised patterns always populate `members` (detect.py's
        # `_pattern_feature`) — the resolver anchors the balloon on a real member,
        # not the pattern's abstract centre, so the fixture must match.
        members = tuple((origin[0] + i, origin[1], origin[2]) for i in range(count))
        return PatternFeature(
            frame=Frame(origin=origin, axis="z"),
            pattern="bolt_circle",
            count=count,
            member=member,
            members=members,
        )

    def test_dropped_pattern_gets_one_grouped_balloon(self, monkeypatch):
        from dataclasses import replace

        from draftwright.annotations._common import Escalation, PlacementContext
        from draftwright.annotations.orchestrator import _maybe_tabulate_holes

        dwg = build_drawing(_multi_hole_plate())  # sparse — density gate stays shut
        before = set(dwg.annotations())
        feat = self._fake_pattern(count=6, diameter=5.0)
        overlapping_hole = replace(
            feat.member, frame=replace(feat.member.frame, origin=feat.members[0])
        )
        correct_model = replace(dwg.model(), features=[*dwg.model().features, feat])
        ctx = PlacementContext(
            registry=dwg.registry,
            coverage=dwg.coverage,
            items=dwg.items,
            part_model=replace(
                correct_model, features=[*correct_model.features, overlapping_hole]
            ),
            escalations=[
                Escalation(kind="callout", view="plan", feature=feat, reason="strip_full")
            ],
        )
        # Mirror what _record_callout_drop does in production, so clearing it below
        # actually exercises the resolve path rather than trivially passing.
        issue = LintIssue(
            severity="warning", code="callout_dropped", message="synthetic plan-view drop"
        )
        dwg.registry.record_issue(issue)
        analysis = dwg._analysis

        # The last IR owner at the shared member position wins balloon attribution. A name
        # landing is not enough: the wrong feature must leave the pattern drop unresolved.
        _maybe_tabulate_holes(dwg, analysis, ctx=ctx)
        balloon_name = "balloon_plan_6×A_0"
        assert dwg.registry.feature_of(balloon_name) == overlapping_hole
        assert issue in dwg.registry.issues

        dwg.remove(balloon_name)
        ctx = PlacementContext(
            registry=dwg.registry,
            coverage=dwg.coverage,
            items=dwg.items,
            part_model=correct_model,
            escalations=[
                Escalation(kind="callout", view="plan", feature=feat, reason="strip_full")
            ],
        )
        _maybe_tabulate_holes(dwg, analysis, ctx=ctx)

        assert "hole_table_plan" not in dwg.annotations()  # density gate untouched
        new_balloons = [
            n for n in dwg.annotations() if n.startswith("balloon_") and n not in before
        ]
        assert len(new_balloons) == 1
        assert new_balloons[0].split("_")[2] == "6×A"
        assert dwg.registry.feature_of(new_balloons[0]) == feat
        # ADR 2 (was 0009) retains one grouped visual marker, but ``6×A`` has no
        # defining table/legend and therefore cannot certify the dropped
        # diameter/depth/pattern requirements.
        assert issue in dwg.registry.issues
        assert dwg.measurement_keys(new_balloons[0]) == []

        # A pattern-only escalation has no scattered-hole table. It must still use
        # the automatic retained-label guard before a landed name may clear the
        # original callout drop (#1144).
        import draftwright.annotations.balloons as balloons

        dwg.remove(new_balloons[0])
        guarded_issue = LintIssue(
            severity="warning", code="callout_dropped", message="guarded pattern drop"
        )
        dwg.registry.record_issue(guarded_issue)
        monkeypatch.setattr(
            balloons,
            "balloon_annotation_label_boxes",
            lambda *_args: ((-1000.0, -1000.0, 1000.0, 1000.0),),
        )
        _maybe_tabulate_holes(dwg, analysis, ctx=ctx)

        assert not [name for name in dwg.annotations() if name.startswith("balloon_plan_6×A")]
        assert guarded_issue in dwg.registry.issues
        assert "balloon_dropped" in {finding.code for finding in dwg.registry.issues}

    def test_multiple_dropped_patterns_get_distinct_non_overlapping_balloons(self):
        from draftwright.annotations._common import Escalation, PlacementContext
        from draftwright.annotations.orchestrator import _maybe_tabulate_holes

        dwg = build_drawing(_multi_hole_plate())
        feats = [
            self._fake_pattern(count=4, diameter=3.0, origin=(-15.0, -8.0, 0.0)),
            self._fake_pattern(count=6, diameter=5.0, origin=(15.0, 8.0, 0.0)),
        ]
        ctx = PlacementContext(
            registry=dwg.registry, coverage=dwg.coverage, part_model=dwg.model(), items=dwg.items
        )
        for feat in feats:
            ctx.escalations.append(
                Escalation(kind="callout", view="plan", feature=feat, reason="strip_full")
            )
        _maybe_tabulate_holes(dwg, dwg._analysis, ctx=ctx)

        balloons = [n for n in dwg.annotations() if n.startswith("balloon_plan_")]
        assert {n.split("_")[2] for n in balloons} == {"4×A", "6×B"}
        # The shared-band placement (one _add_balloons call) must not stack them.
        boxes = [dwg.get_annotation(n).bounding_box() for n in balloons]
        b0, b1 = boxes
        overlaps = (
            b0.min.X < b1.max.X
            and b1.min.X < b0.max.X
            and (b0.min.Y < b1.max.Y and b1.min.Y < b0.max.Y)
        )
        assert not overlaps

    def test_unresolved_pattern_in_other_view_keeps_the_drop_lint(self):
        # A pattern drop the resolver does not cover (a non-plan view) must not
        # have its callout_dropped warning silently cleared.
        from draftwright.annotations._common import Escalation, PlacementContext
        from draftwright.annotations.orchestrator import _maybe_tabulate_holes

        dwg = build_drawing(_multi_hole_plate())
        feat = self._fake_pattern(count=3, diameter=4.0)
        ctx = PlacementContext(
            registry=dwg.registry,
            coverage=dwg.coverage,
            items=dwg.items,
            part_model=dwg.model(),
            escalations=[
                Escalation(kind="callout", view="front", feature=feat, reason="front strip full")
            ],
        )
        dwg.registry.record_issue(
            LintIssue(
                severity="warning", code="callout_dropped", message="synthetic front-view drop"
            )
        )
        _maybe_tabulate_holes(dwg, dwg._analysis, ctx=ctx)

        assert not any(n.startswith("balloon_") for n in dwg.annotations())
        assert "callout_dropped" in {i.code for i in dwg.lint()}


class TestDraftwrightAttribution:
    """draftwright self-attribution in the title block + clickable SVG link."""

    def test_author_appends_draftwright(self):
        from draftwright._core import _attribution_author

        assert _attribution_author("P. Fremantle") == "P. Fremantle / draftwright"

    def test_author_defaults_to_draftwright(self):
        from draftwright._core import _attribution_author

        assert _attribution_author("") == "draftwright"
        assert _attribution_author(None) == "draftwright"
        assert _attribution_author("   ") == "draftwright"

    def test_link_rect_sits_over_the_drawn_by_cell(self, plain_box_dwg):
        # The hyperlink rect must cover the "drawn by" cell of the *rendered*
        # title block: bottom row (half the two-row block height), from the
        # drawn-by cell's left edge to the block's right edge. The left edge is
        # derived from the block's public cell bbox (#139); everything is asserted
        # against the placed block's bounding box so it catches drift if the rect
        # or the upstream TitleBlock layout ever diverge.
        dwg = plain_box_dwg
        tb = dwg.get_annotation("title_block")
        # The rect rides the title-block annotation (#699 slice d), not the drawing.
        x0, y0, x1, y1 = tb.draftwright_link_rect
        bb = tb.bounding_box()
        cell = tb.drawn_by_cell_bbox()  # build-frame; block min corner is at bb.min
        # Both edges come from the block's own cell bbox, not its extents: under
        # the ISO 7200 layout the drawn-by cell no longer reaches the right edge
        # (DATE, REV and SHEET sit to its right) and is one row of four, not two.
        assert x0 == pytest.approx(bb.min.X + cell["min_x"], abs=0.5)
        assert x1 == pytest.approx(bb.min.X + cell["max_x"], abs=0.5)
        assert y0 == pytest.approx(bb.min.Y + cell["min_y"], abs=0.5)
        assert y1 == pytest.approx(bb.min.Y + cell["max_y"], abs=0.5)
        assert 0 < x0 < x1 <= dwg.page_w and 0 < y0 < y1 <= dwg.page_h

    def test_add_svg_hyperlink_injects_anchor(self, tmp_path):
        from draftwright.export import _DRAFTWRIGHT_URL, add_svg_hyperlink

        svg = tmp_path / "x.svg"
        svg.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 -100 200 100"><g/></svg>',
            encoding="utf-8",
        )
        add_svg_hyperlink(str(svg), (150.0, 10.0, 190.0, 18.0))
        out = svg.read_text(encoding="utf-8")
        assert "xmlns:xlink" in out  # namespace declared so xlink:href is valid
        assert f'href="{_DRAFTWRIGHT_URL}"' in out
        # page (x, y) -> svg (x, -y): rect top-left = (150, -18), size 40 x 8
        assert 'x="150.000" y="-18.000" width="40.000" height="8.000"' in out
        assert 'pointer-events="all"' in out

    def test_export_svg_carries_the_clickable_link(self, tmp_path):
        dwg = build_drawing(Box(60, 40, 20))
        _p = dwg.export(str(tmp_path / "out"), formats=("svg", "dxf"))
        svg_path = _p["svg"]
        svg = Path(svg_path).read_text(encoding="utf-8")
        assert "github.com/pzfreo/draftwright" in svg
        assert "<a " in svg and "</a>" in svg

    def test_export_embeds_metadata_in_svg_and_dxf(self, tmp_path):
        dwg = build_drawing(Box(60, 40, 20), drawn_by="P. Fremantle")
        _p = dwg.export(str(tmp_path / "m"), formats=("svg", "dxf"))
        svg_path = _p["svg"]
        dxf_path = _p["dxf"]
        svg = Path(svg_path).read_text(encoding="utf-8")
        assert "<dc:creator>draftwright</dc:creator>" in svg
        assert "Generated by draftwright" in svg
        dxf = Path(dxf_path).read_text(encoding="utf-8", errors="ignore")
        assert "GeneratedBy" in dxf and "draftwright" in dxf

    def test_export_pdf_carries_clickable_link(self, tmp_path):
        # Exercises the load-bearing SVG->PDF coordinate transform + reportlab
        # link annotation. svglib + reportlab are core deps (pure Python, no
        # native cairo), so this runs on every platform. The URI may live in a
        # FlateDecode object stream, so scan the decompressed streams too.
        import re as _re
        import zlib

        dwg = build_drawing(Box(60, 40, 20))
        # Via export(formats=("pdf",)), not the deprecated export_pdf: this test needs *a PDF*
        # to check the link, not that particular verb, and routing it through a surface slated
        # for removal in 0.5.0 would take the test with it. The deprecation keeps its own
        # coverage in test_export_pdf_is_deprecated_but_still_works.
        pdf_path = dwg.export(str(tmp_path / "p"), formats=("pdf",))["pdf"]
        data = Path(pdf_path).read_bytes()
        found = b"pzfreo/draftwright" in data
        for m in _re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, _re.S):
            try:
                chunk = zlib.decompress(m.group(1))
            except Exception:
                continue
            if b"/URI" in chunk and b"pzfreo" in chunk:
                found = True
        assert found, "PDF must embed a clickable draftwright URI link annotation"

    def test_export_pdf_notes_and_tables_are_searchable_unicode_text(self, tmp_path):
        import pypdfium2 as pdfium

        dwg = build_drawing(Box(60, 40, 20), auto_dims=False)
        dwg.note("INSPECT SURFACE ±0.1", (65, 55), name="inspection_note")
        dwg.note("ROTATED NOTE", (100, 55), rotation=45, name="rotated_note")
        dwg.add_table(
            [("NOTES", "SIZE"), ("DEBURR", "⌀5 ±0.1")],
            name="notes_table",
        )

        pdf_path = dwg.export(str(tmp_path / "searchable"), formats=("pdf",))["pdf"]
        pdf = pdfium.PdfDocument(pdf_path)
        try:
            page = pdf[0]
            text_page = page.get_textpage()
            extracted = text_page.get_text_range()
            first_char_box = text_page.get_charbox(extracted.index("INSPECT SURFACE"))
            rotated_char_box = text_page.get_charbox(extracted.index("ROTATED NOTE"))
        finally:
            pdf.close()

        assert "INSPECT SURFACE ±0.1" in extracted
        assert "ROTATED NOTE" in extracted
        assert "NOTES" in extracted and "DEBURR" in extracted
        # The visible drafting font's longstanding CAD compatibility glyph is
        # ø for source ⌀; copied text must match what the engineer sees.
        assert "ø5 ±0.1" in extracted
        # It is not merely an off-page search index: the selectable character
        # geometry lies over the visible note on the drawing.
        note_box = dwg.get_annotation("inspection_note").bounding_box()
        k = 72.0 / 25.4
        left, bottom, right, top = first_char_box
        assert left < note_box.max.X * k and right > note_box.min.X * k
        assert bottom < note_box.max.Y * k and top > note_box.min.Y * k
        rotated_box = dwg.get_annotation("rotated_note").bounding_box()
        left, bottom, right, top = rotated_char_box
        assert left < rotated_box.max.X * k and right > rotated_box.min.X * k
        assert bottom < rotated_box.max.Y * k and top > rotated_box.min.Y * k

    def test_export_pdf_does_not_silently_discard_semantic_text(self, tmp_path, monkeypatch):
        from reportlab.pdfgen.canvas import Canvas

        dwg = build_drawing(Box(30, 20, 10), auto_dims=False)
        dwg.note("MUST BE SEARCHABLE", (50, 50))

        def fail_semantic_text(_self, _text):
            raise RuntimeError("semantic text failure")

        monkeypatch.setattr(Canvas, "drawText", fail_semantic_text)
        with pytest.raises(RuntimeError, match="semantic text failure"):
            dwg.export(str(tmp_path / "broken"), formats=("pdf",))


class TestExportFormats:
    """The unified export(formats=...) → {format: path} API + PNG raster export
    (permissive pypdfium2 + Pillow; SVG→PDF→PNG, no native cairo)."""

    def test_export_returns_dict_of_requested_paths(self, tmp_path):
        dwg = build_drawing(Box(60, 40, 20))
        paths = dwg.export(str(tmp_path / "d"), formats=("svg", "dxf", "pdf", "png"))
        assert set(paths) == {"svg", "dxf", "pdf", "png"}
        for fmt, p in paths.items():
            assert Path(p).exists() and Path(p).stat().st_size > 0, fmt
            assert p.endswith("." + fmt)

    def test_export_png_is_valid_and_cleans_intermediates(self, tmp_path):
        dwg = build_drawing(Box(60, 40, 20))
        paths = dwg.export(str(tmp_path / "p"), formats="png")  # single-format string accepted
        assert set(paths) == {"png"}
        assert Path(paths["png"]).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic
        # the SVG + PDF written only to render the PNG are removed
        assert not (tmp_path / "p.svg").exists() and not (tmp_path / "p.pdf").exists()

    def test_png_export_does_not_clobber_existing_outputs(self, tmp_path):
        # #677 review (blocking): a later png-only export to the same stem must NOT overwrite
        # then delete an SVG/PDF an earlier export wrote — intermediates go to a temp dir.
        dwg = build_drawing(Box(60, 40, 20))
        stem = str(tmp_path / "part")
        dwg.export(stem, formats=("svg", "pdf"))
        svg, pdf = tmp_path / "part.svg", tmp_path / "part.pdf"
        svg_bytes, pdf_bytes = svg.read_bytes(), pdf.read_bytes()
        dwg.export(stem, formats="png")  # must not touch the existing svg/pdf
        assert (tmp_path / "part.png").exists()
        assert svg.exists() and svg.read_bytes() == svg_bytes  # untouched, not deleted
        assert pdf.exists() and pdf.read_bytes() == pdf_bytes

    def test_export_png_dpi_scales_the_raster(self, tmp_path):
        dwg = build_drawing(Box(60, 40, 20))
        lo = Path(dwg.export(str(tmp_path / "lo"), formats="png", dpi=72)["png"]).stat().st_size
        hi = Path(dwg.export(str(tmp_path / "hi"), formats="png", dpi=220)["png"]).stat().st_size
        assert hi > lo  # more pixels → a larger file

    def test_export_unknown_format_raises(self, tmp_path):
        dwg = build_drawing(Box(30, 20, 10))
        with pytest.raises(ValueError, match="unknown export format"):
            dwg.export(str(tmp_path / "x"), formats=("svg", "jpeg"))

    def test_export_format_is_case_insensitive(self, tmp_path):
        # #677 review: a single-string format must normalise like an iterable one.
        dwg = build_drawing(Box(30, 20, 10))
        assert set(dwg.export(str(tmp_path / "u"), formats="PNG")) == {"png"}
        assert set(dwg.export(str(tmp_path / "v"), formats=("SVG", "Dxf"))) == {"svg", "dxf"}

    def test_export_png_zero_dpi_raises(self, tmp_path):
        # #677 review: reject dpi<=0 before writing, so no intermediates are stranded.
        dwg = build_drawing(Box(30, 20, 10))
        with pytest.raises(ValueError, match="dpi > 0"):
            dwg.export(str(tmp_path / "z"), formats="png", dpi=0)

    def test_export_pdf_is_deprecated_but_still_works(self, tmp_path):
        dwg = build_drawing(Box(30, 20, 10))
        with pytest.warns(DeprecationWarning, match="export_pdf"):
            pdf = dwg.export_pdf(str(tmp_path / "old"))
        assert Path(pdf).exists() and pdf.endswith(".pdf")

    def test_the_legacy_export_shapes_warn_and_still_work(self, tmp_path):
        """#987: both were "Deprecated" in the v0.3.1 changelog and silent at runtime for four
        minor releases, which made their 0.5.0 removal a silent break.

        `test_deprecation_dates` only counts that a warning EXISTS and names a version, so it
        cannot catch any of what is asserted here (Codex review): the two shapes are told apart,
        the advice matches what the call actually selected, the caller is blamed rather than
        draftwright, and the legacy return value is unchanged."""
        dwg = build_drawing(Box(30, 20, 10))

        # Bare export(): the old default. Distinct message, and still the (svg, dxf) tuple.
        with pytest.warns(DeprecationWarning, match="omitted or None") as rec:
            legacy = dwg.export(str(tmp_path / "bare"))
        assert isinstance(legacy, tuple) and len(legacy) == 2
        assert all(p and Path(p).exists() for p in legacy)
        # Blamed on THIS file, not drawing.py — a warning pointing at the library tells the
        # reader nothing about which of their lines to change (#965).
        assert Path(rec[0].filename).name == Path(__file__).name

        # The booleans deselect, so the suggested formats must be what this call ASKED for.
        # A canned ('svg', 'dxf') would tell the caller to start writing an SVG they had
        # switched off — advice that changes behaviour.
        with pytest.warns(DeprecationWarning, match=r"formats=\('dxf',\)") as rec2:
            svg_path, dxf_path = dwg.export(str(tmp_path / "dxfonly"), svg=False, dxf=True)
        assert svg_path is None and dxf_path is not None and Path(dxf_path).exists()
        assert "omitted or None" not in str(rec2[0].message)  # the other shape's message

        # `formats=None` is indistinguishable from omitting it — None IS the default sentinel —
        # so it takes the same path and must get the same message, not one claiming no formats
        # argument was passed (Codex r3).
        with pytest.warns(DeprecationWarning, match="omitted or None"):
            assert isinstance(dwg.export(str(tmp_path / "none"), formats=None), tuple)

        # The supported call is silent and returns the dict.
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            paths = dwg.export(str(tmp_path / "new"), formats=("svg", "dxf"))
        assert sorted(paths) == ["dxf", "svg"]

    def test_a_legacy_boolean_alongside_formats_says_it_is_ignored(self, tmp_path):
        """`formats=` wins over the booleans, so `formats=("svg",), svg=False` writes the very
        SVG the caller switched off — and the legacy branch never runs, so before this it
        happened in total silence.

        A deprecated argument that is ignored without a word is the failure this change exists
        to fix, so it warns. `formats` still wins: the warning reports the outcome rather than
        changing it."""
        dwg = build_drawing(Box(30, 20, 10))
        with pytest.warns(DeprecationWarning, match="IGNORED when formats=") as rec:
            paths = dwg.export(str(tmp_path / "both"), formats=("svg",), svg=False)
        assert sorted(paths) == ["svg"], "formats= still decides what is written"
        assert "svg=" in str(rec[0].message)  # names which argument was dropped
        assert Path(rec[0].filename).name == Path(__file__).name  # blames the caller

    def test_a_one_shot_formats_iterable_survives_the_warning(self, tmp_path):
        """Building the warning message must not CONSUME `formats` (Codex #987 r4).

        It read `tuple(formats)` to say what would be written, and the export then iterated the
        same object again — so a generator warned "writes ('svg', 'dxf')" and then wrote
        nothing, returning {}. The warning silently broke the call it was describing, which is
        a worse failure than the silence it was added to fix. `formats` is normalised once now.
        """
        dwg = build_drawing(Box(30, 20, 10))
        with pytest.warns(DeprecationWarning, match=r"formats=\('svg', 'dxf'\)"):
            paths = dwg.export(
                str(tmp_path / "gen"), formats=(f for f in ("svg", "dxf")), svg=False
            )
        assert sorted(paths) == ["dxf", "svg"], "the generator was consumed by the warning"
        assert all(Path(p).exists() for p in paths.values())

        # And a plain string is one format, not its letters: `tuple("svg")` said ('s','v','g').
        with pytest.warns(DeprecationWarning, match=r"formats=\('svg',\)"):
            assert sorted(dwg.export(str(tmp_path / "str"), formats="svg", dxf=True)) == ["svg"]

    def test_a_bad_format_reports_the_typo_not_the_deprecation(self, tmp_path):
        """A mistyped format is a broken call, not a deprecated one. Normalising `formats`
        earlier put the deprecation warning ahead of the format validation, so a caller
        promoting DeprecationWarning to an error would see the deprecation instead of the typo
        that actually stopped their export. Validation runs first."""
        dwg = build_drawing(Box(30, 20, 10))
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with pytest.raises(ValueError, match="unknown export format"):
                dwg.export(str(tmp_path / "bad"), formats=("nope",), svg=False)
        assert not [w for w in caught if issubclass(w.category, DeprecationWarning)], (
            "the deprecation fired before the ValueError that matters"
        )

    def test_make_drawing_is_not_on_the_legacy_export_path(self, tmp_path):
        """#987: `make_drawing` used to call `.export()` with no formats, so warning on that
        path would have fired for every caller of the headline API — blaming draftwright's own
        line for a call they never made. It passes `formats=` now, and its documented tuple
        return is unchanged."""
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            svg, dxf = make_drawing(Box(30, 20, 10), out=str(tmp_path / "mk"))
        assert Path(svg).exists() and svg.endswith(".svg")
        assert Path(dxf).exists() and dxf.endswith(".dxf")
