"""Tests for draftwright.make_drawing."""

import math
import warnings
from pathlib import Path

import pytest
from _drawing_helpers import ink_crossings_named as _ink_crossings_named
from _drawing_helpers import sheet_script_drawing as _sheet_script_drawing
from _kernel import B123D_GE_011, SKIP_011
from _parts import crowded_shoulder_part as _crowded_shoulder_part
from _parts import dense_plate as _dense_plate
from _parts import holed_plate as _holed_plate
from _parts import multi_hole_plate as _multi_hole_plate
from _parts import uniform_staircase as _uniform_staircase
from build123d import Align, Axis, Box, Compound, Cylinder, Edge, Pos, Rotation, export_step
from build123d_drafting import Leader
from quiddity import (
    Slot,
    build_raw_recognition_result,
    recognise_slots,
)

from draftwright import build_drawing, make_drawing
from draftwright._core import _MARGIN
from draftwright.linting import LintIssue
from draftwright.model import DimensionId

_skip_011 = pytest.mark.skipif(B123D_GE_011, reason=SKIP_011)


def _ctx_for(dwg):
    """A `PlacementContext` wired to a real `Drawing`'s public seams (#817) — for white-box unit
    tests that call an internal render helper (`ctx.place` routes to `dwg`'s registry + item list)."""
    from draftwright.annotations._common import PlacementContext

    return PlacementContext(registry=dwg.registry, coverage=dwg.coverage, items=dwg.items)


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


@pytest.fixture(scope="module")
def x_shaft_dwg():
    return build_drawing(_x_stepped_shaft())


def _recognised_pocket_fields(part):
    from draftwright.section_recess_contract import recesses_with_kind, section_recess_fields

    inventory = build_raw_recognition_result(part).section_recesses
    return [section_recess_fields(source)[1] for source in recesses_with_kind(inventory, "pocket")]


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




class TestFindSlots:
    """#135: recognition of enclosed through-slots with rectangular walls.

    recognise_slots() is a pure-geometry pass (no projection) so these are fast.
    Scope is deliberately narrow (#148): only through-slots with straight walls.
    """

    def test_through_slot_recognised(self):
        # A 20-long, 8-wide channel milled THROUGH a 60×30×12 bar.
        part = Box(60, 30, 12) - Pos(0, 0, 0) * Box(20, 8, 20)
        slots = recognise_slots(part)
        assert len(slots) == 1
        s = slots[0]
        assert s.width_axis == "y"
        assert s.long_axis == "x"
        assert s.width == 8.0
        assert s.length == 20.0
        assert (s.lo, s.hi) == (-10.0, 10.0)

    def test_plain_box_has_no_slots(self):
        # The stock's own outer faces are parallel and anti-parallel but face
        # AWAY from each other — the facing test must exclude them.
        assert recognise_slots(Box(40, 20, 10)) == []

    def test_single_flat_is_not_a_slot(self):
        # One machined flat has no opposing wall, so it is not a slot.
        part = Box(40, 20, 10) - Pos(0, 12, 0) * Box(40, 10, 10)
        assert recognise_slots(part) == []

    def test_blind_slot_is_not_a_slot(self):
        # A blind slot (cut partway, leaving a floor) is out of scope (#148):
        # the floor test rejects it. Same geometry as the through case but the
        # cutter does not break through the bottom.
        part = Box(60, 30, 12) - Pos(0, 0, 2) * Box(20, 8, 8)
        assert recognise_slots(part) == []

    def test_blind_pocket_is_not_a_slot(self):
        # A rectangular pocket has the same facing rectangular walls as a slot
        # but is capped by a floor — the through/blind test must reject it.
        part = Box(100, 60, 10) - Pos(0, 0, 2) * Box(40, 25, 6)
        assert recognise_slots(part) == []

    def test_split_floor_pocket_is_not_a_slot(self):
        # A blind pocket whose floor is divided into two coplanar faces by a rib
        # (a webbed / twin-cavity pocket): neither floor half covers 50% of the
        # footprint alone, so the floor test must AGGREGATE coverage across both
        # — otherwise the pocket reads as a phantom through-slot (#146 re-review).
        part = (Box(40, 40, 20) - Pos(0, 0, 10) * Box(10, 30, 8)) + Pos(0, 0, 7) * Box(10, 2, 2)
        assert recognise_slots(part) == []

    def test_turned_groove_is_not_a_slot(self):
        # A circumferential groove on a shaft has ANNULAR (circle-bounded) walls;
        # the rectangular-wall test must reject it (otherwise a stepped shaft's
        # circlip groove reads as a slot — the #146 review false positive).
        part = Cylinder(10, 40) - (Cylinder(10, 4) - Cylinder(7, 4))
        assert recognise_slots(part) == []

    def test_arc_walled_slot_in_round_stock_recognised(self):
        # The published cylindrical mouth must survive lowering, including the distinction
        # between the maximum floor-to-mouth depth and a uniform rectangular recess.
        bar = Rotation(0, 90, 0) * Cylinder(20, 80)  # X-axis round bar
        part = bar - Pos(0, 0, 14) * Box(6, 24, 12)  # enclosed slot milled into the top
        (p,) = _recognised_pocket_fields(part)
        assert p["width"] == 6.0
        assert p["width_axis"] == "x"  # width runs ALONG the bar axis → arc-clipped walls
        assert p["length"] == 24.0
        assert p["mouth_axis"] == "x" and p["mouth_radius"] == 20
        assert p["depth"] == 12.0  # maximum; the edge depth is only 8 mm

    def test_arc_wall_relaxation_still_excludes_grooves(self):
        # The relaxation must NOT admit a turned groove's pure-annular wall (CIRCLE
        # edges only, no straight edge) as a slot/pocket wall (#148e) — the very
        # distinction the relaxed test preserves.
        part = Cylinder(10, 40) - (Cylinder(10, 4) - Cylinder(8, 4))
        assert recognise_slots(part) == []
        assert _recognised_pocket_fields(part) == []

    def test_transverse_notch_spanning_bar_is_not_a_slot(self):
        # A notch cut fully ACROSS a round bar exits both sides of the OD — an open
        # feature spanning the part, rejected by the span cap even with arc walls (#148e).
        bar = Rotation(0, 90, 0) * Cylinder(15, 60)
        part = bar - Pos(0, 0, 9) * Box(6, 40, 20)
        assert recognise_slots(part) == []
        assert _recognised_pocket_fields(part) == []

    def test_keyed_groove_does_not_leak_as_a_slot(self):
        # A circlip groove crossed by a wrench flat / keyway notches a straight edge into
        # each annular wall, so a "one straight edge" test would wrongly admit it and the
        # groove would double-report as both a groove AND a phantom slot on a flanged shaft
        # (the span cap can't save it). The annular wall keeps its TWO concentric arcs (OD +
        # floor), so the one-arc cap rejects it (#148e review).
        part = (
            (Box(60, 60, 8) + Pos(0, 0, 34) * Cylinder(10, 60))
            - Pos(0, 0, 34) * (Cylinder(10, 6) - Cylinder(7, 6))
            - Pos(9, 0, 34) * Box(6, 30, 60)
        )
        assert recognise_slots(part) == []
        assert _recognised_pocket_fields(part) == []

    def test_obround_slot_reports_overall_length(self):
        # A radiused-end (obround) slot's flat side walls stop at the straight portion; its
        # length must be the OVERALL length (flat + width, the two semicircular ends), not the
        # flat-wall span (#613). Overall 30, width 8 → flat walls span 22; report 30.
        from build123d import Plane, SlotOverall, extrude

        part = Box(60, 30, 10) - extrude(Plane.XY * SlotOverall(30, 8), 10, both=True)
        (s,) = recognise_slots(part)
        assert s.width == 8.0
        assert s.length == 30.0
        assert (s.lo, s.hi) == (-15.0, 15.0)

    def test_obround_pocket_reports_overall_length(self):
        # The blind counterpart — a floored obround pocket likewise reports overall length (#613).
        from build123d import Plane, SlotOverall, extrude

        part = Box(60, 30, 20) - Pos(0, 0, 5) * extrude(Plane.XY * SlotOverall(30, 8), 12)
        (p,) = _recognised_pocket_fields(part)
        assert p["length"] == 30.0

    def test_rectangular_slot_length_is_unchanged(self):
        # A rectangular slot has no semicircular end caps, so the overall extension is inert —
        # its length is its flat span, already the overall length (#613 must not regress it).
        (s,) = recognise_slots(Box(60, 30, 10) - Box(30, 8, 20))
        assert s.length == 30.0

    def test_recognise_matches_declare_on_obround_length(self):
        # #613 also removes a recognise/declare divergence: declare.slot(obj) reads the overall
        # length off the object bbox (30), while recognise used to report the flat span (22).
        # After the fix both agree on the overall length.
        from build123d import Plane, SlotOverall, extrude

        from draftwright.model import slot as declare_slot

        cutter = extrude(Plane.XY * SlotOverall(30, 8), 10)
        part = Box(60, 30, 10) - extrude(Plane.XY * SlotOverall(30, 8), 20, both=True)
        (s,) = recognise_slots(part)
        declared = declare_slot(cutter, depth_axis="z")
        assert s.length == declared.length == 30.0

    def test_obround_through_slot_shorter_than_wide_is_recognised(self):
        # #816: a stubby obround (overall length < ... no, straight section < width) has flat side
        # walls too short to pair — 8 wide, 13.5 overall → the 5.5 straight walls are narrower than
        # the width, so _candidate rejects them (and the through-thickness is mistaken for length).
        # The end-cap path recovers it. This is the reported failure: recognise_slots returned [].
        from build123d import Plane, SlotOverall, extrude

        part = Box(26, 40, 21) - extrude(Plane.XY * SlotOverall(13.5, 8), 21, both=True)
        (s,) = recognise_slots(part)
        assert s.width == 8.0
        assert s.length == 13.5

    def test_stubby_obround_recognise_matches_declare_length(self):
        # The end-cap path must agree with declare.slot(obj) on the overall length (#816), as the
        # flat-wall obround path does (test_recognise_matches_declare_on_obround_length).
        from build123d import Plane, SlotOverall, extrude

        from draftwright.model import slot as declare_slot

        cutter = extrude(Plane.XY * SlotOverall(13.5, 8), 10)
        part = Box(26, 40, 21) - extrude(Plane.XY * SlotOverall(13.5, 8), 21, both=True)
        (s,) = recognise_slots(part)
        assert s.length == declare_slot(cutter, depth_axis="z").length == 13.5

    def test_row_of_stubby_obround_slots_on_one_centreline_stays_separate(self):
        # #816 "tuner jig": five stubby obround through-slots down one centreline (spaced along the
        # long axis) share a radius/centreline/depth, so all ten end caps land in one group. Pairing
        # by bulge direction (a low end followed by a high end is one slot; the reverse is the solid
        # gap between slots) must keep them five, not merge into one giant slot.
        from build123d import Plane, SlotOverall, extrude

        part = Box(26, 161, 21)
        for cy in (-54, -27, 0, 27, 54):
            part = part - Pos(0, cy, 0) * extrude(
                Plane.XY * SlotOverall(13.5, 8, rotation=90), 21, both=True
            )
        slots = recognise_slots(part)
        assert len(slots) == 5
        assert all(s.width == 8.0 and s.length == 13.5 for s in slots)

    def test_two_through_holes_are_not_an_obround_slot(self):
        # #816 guard: a round through-hole is a FULL cylinder (bbox 2r × 2r), never a half-cylinder
        # obround end — two coaxial holes must not be paired into a slot.
        part = Box(26, 40, 21) - Pos(0, -8, 0) * Cylinder(4, 30) - Pos(0, 8, 0) * Cylinder(4, 30)
        assert recognise_slots(part) == []

    def test_deep_blind_obround_pocket_is_not_a_through_slot(self):
        # #816 review: a blind obround pocket cut 9.5 mm into 10 mm stock has end caps spanning
        # ≥90% of the thickness, so it passes the cheap through pre-filter — only the floor test
        # (a through-slot has no floor) may reject it. It must not be read as a through-slot.
        from build123d import Plane, SlotOverall, extrude

        part = Box(60, 30, 10) - Pos(0, 0, -4.5) * extrude(Plane.XY * SlotOverall(13.5, 8), 9.5)
        assert recognise_slots(part) == []

    def test_two_d_cutouts_across_solid_are_not_a_slot(self):
        # #816 review: two independent D-shaped (half-cylinder) through-cutouts bulging APART with
        # solid stock between their flats have end caps in the same -1,+1 order a real obround does,
        # but no side walls join them — they must not be paired into a phantom slot across solid.
        # The stock is exactly as wide as the caps (8 = 2r), so its OUTWARD-facing exterior side
        # faces sit at w_center ± width/2; only the inward-normal test in _has_side_walls rejects them.
        d1 = Pos(0, -10, 0) * Cylinder(4, 30) & Pos(0, -12, 0) * Box(20, 4, 40)
        d2 = Pos(0, 10, 0) * Cylinder(4, 30) & Pos(0, 12, 0) * Box(20, 4, 40)
        split = Box(8, 40, 21) - d1 - d2
        part = split + Pos(0, 0, 11) * Box(8, 40, 1)
        assert len(part.solids()) == 1  # the exterior bridge keeps the predicate body-local (#958)
        assert recognise_slots(part) == []

    def test_stubby_blind_obround_pocket_is_recognised_not_a_slot(self):
        # #837: the blind counterpart of the stubby through-slot — a floored obround pocket whose
        # straight walls are too short to pair. It must be a Pocket (with depth), not a through Slot.
        from build123d import Plane, SlotOverall, extrude

        part = Box(60, 30, 21) - Pos(0, 0, -8.5) * extrude(Plane.XY * SlotOverall(13.5, 8), 19)
        assert recognise_slots(part) == []
        (p,) = _recognised_pocket_fields(part)
        assert p["width"] == 8.0 and p["length"] == 13.5 and p["depth"] == 19.0

    def test_row_of_stubby_blind_obround_pockets_stays_separate(self):
        # #837: five blind obround pockets down one centreline stay five distinct pockets.
        from build123d import Plane, SlotOverall, extrude

        part = Box(26, 161, 21)
        for cy in (-54, -27, 0, 27, 54):
            part = part - Pos(0, cy, -8.5) * extrude(
                Plane.XY * SlotOverall(13.5, 8, rotation=90), 19
            )
        pockets = _recognised_pocket_fields(part)
        assert len(pockets) == 5
        assert all(
            p["width"] == 8.0 and p["length"] == 13.5 and p["depth"] == 19.0 for p in pockets
        )

    def test_step_imported_rounded_pockets_retain_the_short_straight_sides(self):
        # The released profile has four R3.94 corners and 0.02 mm short straight sides:
        # it is 7.9 mm wide, not the approximate 7.88 mm obround formerly reported here.
        from pathlib import Path

        from build123d import import_step

        step = Path(__file__).parent / "fixtures" / "tuner_jig_blind_obround_pockets.step"
        part = import_step(str(step))
        pockets = _recognised_pocket_fields(part)
        assert recognise_slots(part) == []  # blind, not through
        assert len(pockets) == 5
        for pocket in pockets:
            assert tuple(
                pocket[k] for k in ("width", "length", "depth", "corner_radius")
            ) == pytest.approx((7.9, 13.6, 19.0, 3.94), abs=1e-9, rel=0)

    def test_sealed_internal_obround_void_is_not_a_pocket(self):
        # #837 review: a fully enclosed obround cavity (planar caps at BOTH depth ends, no
        # opening) is not a machinable recess — the end-cap recovery routes on the exact floor
        # count (a pocket has ONE floor + one opening), so a both-ends-capped void is neither a
        # pocket nor a full-thickness-deep phantom.
        from build123d import Plane, SlotOverall, extrude

        void = Box(60, 30, 20) - Pos(0, 0, -1.5) * extrude(Plane.XY * SlotOverall(13.5, 8), 3)
        assert _recognised_pocket_fields(void) == []

    def test_pivot_boss_at_slot_end_does_not_extend_length(self):
        # A rectangular cut interrupted by a cylindrical pivot boss contains solid material
        # inside the proposed recess prism, so it is not a complete slot. Recognisers 0.3.1
        # deliberately rejects the candidate instead of reporting a misleading 22 mm slot.
        part = Box(60, 30, 10) - Box(22, 8, 20) + Pos(11.3, 0, 5) * Cylinder(4, 10)
        assert recognise_slots(part) == []

    def test_coaxial_blind_hole_does_not_extend_pocket(self):
        # A blind pocket with a separate blind hole (radius = width/2) drilled from the far
        # face, coaxial with one pocket end but at a different depth (solid material between),
        # must NOT extend the pocket length — the cap's depth extent must match the slot's (#613 review).
        part = Box(60, 30, 20) - Pos(0, 0, 4) * Box(22, 8, 12) - Pos(11.2, 0, -10) * Cylinder(4, 8)
        (p,) = _recognised_pocket_fields(part)
        assert p["length"] == 22.0

    def test_coaxial_posts_at_both_ends_do_not_extend_length(self):
        # Symmetric coaxial POSTS (added material, radius = width/2) protruding into both slot
        # ends at the slot's own depth pass the radius/axis/centreline/depth checks — but they
        # are CONVEX (material inside the cylinder), not concave void caps. The concavity test
        # must reject them so the flat-ended slot is not extended (#613 2nd-pass review).
        part = (
            (Box(60, 30, 10) - Box(30, 8, 20))
            + Pos(15, 0, 0) * Cylinder(4, 10)
            + Pos(-15, 0, 0) * Cylinder(4, 10)
        )
        (s,) = recognise_slots(part)
        assert (
            s.length == 30.0
        )  # the flat span, NOT 38 (would be if wrongly extended by the posts)
        assert (s.lo, s.hi) == (-15.0, 15.0)

    def test_gap_between_bosses_is_not_a_slot(self):
        # The floored channel between two raised bosses has facing rectangular
        # walls but is not a cut slot — the floor (the base plate) rejects it.
        part = Box(80, 40, 6) + Pos(-15, 0, 9) * Box(10, 40, 12) + Pos(15, 0, 9) * Box(10, 40, 12)
        assert recognise_slots(part) == []

    def test_full_span_through_slot_is_not_a_slot(self):
        # A through-channel that runs the WHOLE length of the part is an open
        # feature (a U-channel), not an enclosed slot — rejected by the span cap.
        part = Box(20, 30, 12) - Pos(0, 0, 0) * Box(20, 8, 20)
        assert recognise_slots(part) == []

    def test_rectangular_slot_reported_once(self):
        # A through rectangular slot is bounded by two orthogonal opposed-wall
        # pairs; the merge must collapse them to a single Slot (the narrower
        # width), not report the same feature twice.
        part = Box(60, 40, 12) - Pos(0, 0, 0) * Box(10, 24, 20)
        slots = recognise_slots(part)
        assert len(slots) == 1
        assert slots[0].width == 10.0  # the narrower of the two opposed pairs

    def test_near_square_slot_runs_along_the_bar(self):
        # A through slot whose x-extent ≈ z-extent: the length is assigned to the
        # part's longer axis (a slot on a bar runs along the bar), not whichever
        # OCC extent is fractionally larger.
        part = Box(80, 20, 6) - Pos(0, 0, 0) * Box(6, 4, 8)
        (s,) = recognise_slots(part)
        assert s.width_axis == "y"
        assert s.width == 4.0
        assert s.long_axis == "x"  # not z, despite z-extent ≈ x-extent locally

    def test_cross_slot_collapses_to_two_channels(self):
        # A + of two intersecting through-channels: the central intersection
        # splits each channel's walls, so the raw scan finds FOUR arm-slots. The
        # collinear-collapse must recombine them into the TWO channels, each
        # spanning its full length (#148d). Thin plate so the arm length exceeds
        # the (through) thickness, else the depth axis is mistaken for length.
        part = Box(80, 60, 10) - Box(50, 12, 20) - Box(14, 44, 20)
        slots = recognise_slots(part)
        assert len(slots) == 2
        by_long = {s.long_axis: s for s in slots}
        assert by_long["x"].width == 12.0
        assert by_long["x"].length == 50.0  # the full x-channel, not a 18mm arm
        assert (by_long["x"].lo, by_long["x"].hi) == (-25.0, 25.0)
        assert by_long["y"].width == 14.0
        assert by_long["y"].length == 44.0  # the full y-channel, not a 16mm arm
        assert (by_long["y"].lo, by_long["y"].hi) == (-22.0, 22.0)

    def test_collinear_slots_with_solid_bridge_stay_separate(self):
        # Two collinear slots on the SAME centreline but separated by solid
        # material (no crossing channel bridging the gap) are distinct features.
        # The collapse must span arms only when a perpendicular channel fills the
        # gap — here it does not, so both slots survive (#148d guard).
        part = (
            Box(120, 40, 10) - Pos(-35, 0, 0) * Box(40, 12, 20) - Pos(35, 0, 0) * Box(40, 12, 20)
        )
        slots = recognise_slots(part)
        assert len(slots) == 2
        assert all(s.length == 40.0 for s in slots)  # not merged into one 110mm run

    def test_arms_not_fused_by_a_channel_that_misses_their_centreline(self):
        # The bridging channel must actually REACH the arms, not merely match the
        # gap's centre and width.  Two collinear x-arms on centreline y=0 with a
        # SOLID gap, plus a perpendicular channel displaced to y∈[10,50] whose
        # x-centre and x-width coincide with the gap but which never crosses y=0.
        # Position-blind bridging would fuse the arms across solid stock (#610
        # review); the run-overlap check keeps all three slots distinct.
        part = (
            Box(120, 100, 10)
            - Pos(-35, 0, 0) * Box(40, 12, 20)
            - Pos(35, 0, 0) * Box(40, 12, 20)
            - Pos(0, 30, 0) * Box(30, 40, 20)
        )
        slots = recognise_slots(part)
        assert len(slots) == 3  # two 40mm x-arms + one y-channel, none merged
        assert sorted(s.length for s in slots) == [40.0, 40.0, 40.0]

    def test_pinwheel_of_slots_around_a_solid_hub_stays_four(self):
        # Four disjoint slots arranged around a SOLID central hub: two collinear
        # x-arms and two collinear y-arms, each opposed pair straddling — but not
        # reaching — the hub. Reasoning only from the neighbouring slots' extents
        # would fuse each opposed pair across the hub; the gap box over the solid
        # hub is not void, so it is not merged and all four survive (#610 re-review).
        from build123d import Align

        def cut(xlo, xhi, ylo, yhi):
            return Pos(xlo, ylo, -5) * Box(
                xhi - xlo, yhi - ylo, 20, align=(Align.MIN, Align.MIN, Align.MIN)
            )

        part = (
            Box(100, 100, 10, align=(Align.MIN, Align.MIN, Align.MIN))
            - cut(10, 40, 47, 53)
            - cut(60, 90, 47, 53)
            - cut(40, 60, 10, 40)
            - cut(40, 60, 60, 90)
        )
        assert len(recognise_slots(part)) == 4  # solid hub keeps all four apart

    def test_incidental_hole_between_aligned_slots_does_not_fuse_them(self):
        # Two separate collinear slots on a shared centreline with an unrelated
        # through-hole centred between them (a natural mounting-hole layout).  The
        # hole makes the gap CENTRE void, but the gap box is mostly solid, so the
        # slots must stay separate — a crossing channel would carve the whole box,
        # a hole only pierces it (#610 re-review).
        from build123d import Cylinder

        part = (
            Box(120, 40, 10)
            - Pos(-35, 0, 0) * Box(40, 12, 20)
            - Pos(35, 0, 0) * Box(40, 12, 20)
            - Cylinder(4, 30)
        )
        slots = recognise_slots(part)
        assert len(slots) == 2  # not fused into one 110mm slot by the hole
        assert all(s.length == 40.0 for s in slots)

    def test_slot_is_frozen_dataclass(self):
        s = recognise_slots(Box(60, 30, 12) - Pos(0, 0, 0) * Box(20, 8, 20))[0]
        assert isinstance(s, Slot)
        with pytest.raises(Exception):
            s.width = 1.0  # frozen

    def test_output_order_is_deterministic(self):
        # Two equal-width through-slots must be ordered by geometry (not OCC face
        # order), so the slot{i} annotation names are stable.
        part = Box(120, 40, 12) - Pos(-30, 0, 0) * Box(8, 20, 20) - Pos(30, 0, 0) * Box(8, 20, 20)
        runs = [[(s.width, s.lo, s.hi) for s in recognise_slots(part)] for _ in range(3)]
        assert runs[0] == runs[1] == runs[2]
        assert len(runs[0]) == 2


class TestSlotDimensioning:
    """#135: slots carry width / length / position dims, place-what-fits."""

    @pytest.mark.timeout(60)
    def test_slot_gets_width_length_and_position(self):
        # Through slot at x∈[-10,10] in a 60-long bar (datum x=-30): position to
        # the near (lo) edge is -10-(-30) = 20.
        part = Box(60, 30, 12) - Pos(0, 0, 0) * Box(20, 8, 20)
        dwg = build_drawing(part)
        labels = {
            n: dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("m_slot")
        }
        assert labels.get("m_slot0_width") == "8"
        assert labels.get("m_slot0_length") == "20"
        assert labels.get("m_slot0_pos") == "20"

    @pytest.mark.timeout(60)
    def test_slot_sheet_is_lint_clean(self):
        part = Box(60, 30, 12) - Pos(0, 0, 0) * Box(20, 8, 20)
        dwg = build_drawing(part)
        assert [i for i in dwg.lint() if i.severity != "info"] == []

    @pytest.mark.timeout(60)
    def test_stubby_obround_through_slot_is_dimensioned_end_to_end(self):
        # #816: a recognised stubby obround slot flows through IR → planner → render_slots and
        # carries width + length dims (the reported failure drew it with no slot callouts at all).
        from build123d import Plane, SlotOverall, extrude

        part = Box(60, 30, 12) - extrude(Plane.XY * SlotOverall(13.5, 8), 12, both=True)
        dwg = build_drawing(part)
        assert any(f.kind == "slot" for f in dwg.model().features)
        labels = {
            n: dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("m_slot")
        }
        assert labels.get("m_slot0_width") == "8"
        assert labels.get("m_slot0_length") == "13.5"

    @pytest.mark.timeout(60)
    def test_non_round_width_label_matches_geometry(self):
        # A true 4.75 mm slot labels as "4.8"; the dim geometry must be snapped
        # to the displayed value or the label-vs-measured lint trips (#135).
        part = Box(60, 30, 12) - Pos(0, 0, 0) * Box(20, 4.75, 20)
        dwg = build_drawing(part)
        assert dwg.get_annotation("m_slot0_width").label == "4.8"
        assert [i for i in dwg.lint() if i.code == "label_vs_measured"] == []

    @pytest.mark.timeout(60)
    def test_slot_dims_do_not_overprint_hole_callouts(self):
        # A slot dim's witness/arrow geometry must not cross a hole callout label.
        # The collision gate tests the dim's FULL geometry (not just its label
        # box) against external annotations, which lint is blind to (#146 review).
        part = Box(140, 60, 16) - Pos(0, 0, 0) * Box(10, 40, 24)
        for x, y in [(-45, 20), (45, 20), (-45, -20), (45, -20)]:
            part = part - Pos(x, y, 0) * Cylinder(4, 16)
        dwg = build_drawing(part)

        def overlaps(a, b):
            return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])

        external = [
            o.label_bbox
            for n, o in dwg.iter_annotations()
            if not n.startswith("m_slot") and getattr(o, "label_bbox", None) is not None
        ]
        assert external  # the holes produced callouts
        for n, o in dwg.iter_annotations():
            if not n.startswith("m_slot"):
                continue
            g = o.bounding_box()
            full = (g.min.X, g.min.Y, g.max.X, g.max.Y)
            assert not any(overlaps(full, e) for e in external), f"{n} overprints a callout"


def _place_dim(dwg, *args, **kwargs):
    # White-box: these exercise the private placement primitive directly (#817).
    # The deprecated public shim's warning is covered by
    # test_dimension_does_not_warn_when_using_place_dim_internally.
    return dwg._place_dim(*args, **kwargs)


class TestPlaceDim:
    """#25: the raw page-coordinate dim primitive stacks with the auto-dimension strip."""

    def test_place_dim_adds_named_annotation(self):
        dwg = build_drawing(Box(80, 60, 20))
        p1 = dwg.at("plan", -40, 0, 0)
        p2 = dwg.at("plan", 40, 0, 0)
        _place_dim(dwg, p1, p2, "below", "plan", dwg.draft, name="my_dim", label="80")
        assert "my_dim" in dwg.annotations()
        assert dwg.get_annotation("my_dim").label == "80"

    def test_place_dim_returns_dimension_object(self):
        from build123d_drafting.helpers import Dimension

        dwg = build_drawing(Box(60, 40, 20))
        p1 = dwg.at("front", -30, 0, -10)
        p2 = dwg.at("front", 30, 0, -10)
        result = _place_dim(dwg, p1, p2, "below", "front", dwg.draft)
        assert isinstance(result, Dimension)

    def test_two_place_dim_calls_stack_without_overlap(self):
        # Two dims on the same strip must land at different page positions.
        # Use auto_dims=False so the strip has no prior allocations, and
        # "above" where there is ample headroom for two consecutive allocations.
        dwg = build_drawing(Box(80, 60, 20), auto_dims=False)
        p1 = dwg.at("plan", -40, 0, 0)
        p2 = dwg.at("plan", 40, 0, 0)
        d1 = _place_dim(dwg, p1, p2, "above", "plan", dwg.draft, name="d1")
        d2 = _place_dim(dwg, p1, p2, "above", "plan", dwg.draft, name="d2")
        # dim_level_y is the y-coordinate of the dim line on the page;
        # two stacked dims must land at different y values.
        assert d1.dim_level_y != d2.dim_level_y

    def test_place_dim_no_analysis_falls_back_to_slot(self):
        # _analysis is None → no strip available → falls back to slot offset, no error.
        from build123d_drafting.helpers import Dimension, draft_preset

        from draftwright import Drawing

        d = draft_preset(font_size=3.0, decimal_precision=1)
        dwg = Drawing(
            scale=1.0,
            page_w=297,
            page_h=210,
            tb_w=100,
            draft=d,
            look_at=(0, 0, 0),
            dist=100,
            centroid=(0, 0, 0),
            out="",
        )
        result = _place_dim(dwg, (0, 0, 0), (80, 0, 0), "below", "plan", d, slot=8.0)
        assert isinstance(result, Dimension)

    def test_place_dim_labels_real_world_length_at_non_unity_scale(self):
        # place_dim receives page-coordinate points; at 1:2 the page span is 2× the
        # world size. The auto label must read the real-world length, not the page
        # distance, or it disagrees with the geometry (and trips label_vs_measured).
        from draftwright.linting import lint_drawing

        dwg = build_drawing(Box(80, 60, 20), scale=2.0)
        assert dwg.scale == 2.0
        p1 = dwg.at("plan", -40, 0, 0)
        p2 = dwg.at("plan", 40, 0, 0)
        d = _place_dim(dwg, p1, p2, "below", "plan", dwg.draft, name="w")
        assert d.label == "80"
        assert [
            i for i in lint_drawing([d], drawing_scale=dwg.scale) if i.code == "label_vs_measured"
        ] == []

    def test_place_dim_explicit_label_wins_over_scale_autolabel(self):
        dwg = build_drawing(Box(80, 60, 20), scale=2.0)
        p1 = dwg.at("plan", -40, 0, 0)
        p2 = dwg.at("plan", 40, 0, 0)
        d = _place_dim(dwg, p1, p2, "below", "plan", dwg.draft, label="CUSTOM")
        assert d.label == "CUSTOM"

    def test_dimension_does_not_warn_when_using_place_dim_internally(self):
        dwg = build_drawing(Box(80, 50, 20), auto_dims=False)
        env = next(f for f in dwg.model().features if f.kind == "envelope")
        with pytest.warns(DeprecationWarning) as caught:
            dwg.place_dim(
                dwg.at("front", -40, 0, -10),
                dwg.at("front", 40, 0, -10),
                "below",
                "front",
                dwg.draft,
            )
        assert caught

        with warnings.catch_warnings(record=True) as no_warnings:
            warnings.simplefilter("always")
            dwg.dimension(env, "length", role="width", name="semantic_width")
        assert [
            w
            for w in no_warnings
            if issubclass(w.category, DeprecationWarning) and "place_dim" in str(w.message)
        ] == []


# ---------------------------------------------------------------------------
# Issue #29: lint findings carry a suggested-fix code snippet
# ---------------------------------------------------------------------------


class TestLintSuggestions:
    """#29: each LintIssue carries a `suggestion` (str | None) with a fix snippet."""

    def test_diameter_only_finding_has_advice_without_an_unproven_edit(self):
        part = Box(80, 60, 20) - Pos(20, 15, 0) * Cylinder(5, 20)
        dwg = build_drawing(part, auto_dims=False)
        issue = next(i for i in dwg.lint() if i.code == "feature_not_dimensioned")
        assert "separate" in issue.suggestion
        assert "dwg.callout(" not in issue.suggestion

    @pytest.mark.parametrize("pattern", [False, True])
    def test_missing_bore_suggestion_executes_for_its_verified_owner(self, pattern):
        part = Box(120, 40, 20)
        for x in (-40, -20, 0, 20, 40) if pattern else (15,):
            part -= Pos(x, 0, 0) * Cylinder(4, 20)
        dwg = build_drawing(part, auto_dims=False)
        issue = next(
            i
            for i in dwg.lint()
            if i.code == "hole_requirement_missing"
            and any(parameter == "bore.diameter" for _, parameter in i.hole_requirement_ids)
        )
        assert {f.kind for f, _ in issue.hole_requirement_ids} == {
            "pattern" if pattern else "hole"
        }
        assert "dwg.callout(" in issue.suggestion
        exec(issue.suggestion, {"dwg": dwg})
        assert not any(i.code == "feature_not_dimensioned" for i in dwg.lint())

    def test_clean_drawing_has_no_suggestions(self, plain_box_dwg):
        # A fully auto-dimensioned plain box should lint clean → no suggestions.
        for i in plain_box_dwg.lint():
            assert i.suggestion is None

    def test_lint_summary_omits_none_suggestion(self, plain_box_dwg):
        # A clean box: issue dicts (if any) must not carry a suggestion key.
        for d in plain_box_dwg.lint_summary()["issues"]:
            assert "suggestion" not in d

    def test_lint_summary_includes_present_suggestion(self):
        part = Box(80, 60, 20) - Pos(20, 15, 0) * Cylinder(5, 20)
        dwg = build_drawing(part, auto_dims=False)
        dicts = [d for d in dwg.lint_summary()["issues"] if d["code"] == "feature_not_dimensioned"]
        assert dicts
        assert "suggestion" in dicts[0]
        assert dicts[0]["suggestion"]

    def test_step_dim_dropped_suggestion_mentions_detail_view(self):
        dwg = build_drawing(_crowded_shoulder_part(), detail_view=False)
        issues = [i for i in dwg.lint() if i.code == "step_dim_dropped"]
        assert issues, "crowded shoulders should drop a step dim"
        assert "detail_view=True" in issues[0].suggestion

    def test_annotation_overlap_suggestion_prefers_dimension_with_place_dim_fallback(self):
        # Synthetic issue — exercise the _suggest_fix branch directly.
        from draftwright.linting import LintIssue, _suggest_fix

        dwg = build_drawing(Box(60, 40, 20))
        issue = LintIssue(
            severity="warning",
            message="labels 'dim_width' and 'dim_height' overlap by 3.0×2.0 mm",
            code="annotation_overlap",
        )
        sug = _suggest_fix(issue, dwg)
        assert sug is not None
        assert "dwg.dimension" in sug
        assert "pin=True" in sug
        assert "place_dim" in sug
        assert "dim_width" in sug

    def test_dim_inside_part_suggestion_prefers_dimension_with_place_dim_fallback(
        self, plain_box_dwg
    ):
        from draftwright.linting import LintIssue, _suggest_fix

        dwg = plain_box_dwg
        issue = LintIssue(
            severity="warning",
            message="Dim 'dim_height': annotation bbox overlaps part outline by 40%",
            code="dim_inside_part",
        )
        sug = _suggest_fix(issue, dwg)
        assert sug is not None
        assert "dwg.dimension" in sug
        assert "pin=True" in sug
        assert "place_dim" in sug
        assert "dim_height" in sug

    def test_plate_thickness_dropped_suggestion_authors_a_thickness_dim(self, plain_box_dwg):
        # #641 gap 4: assert the snippet CONTENT, not just its presence — a dropped plate
        # thickness must point at a feature-backed thickness dim, not a raw-coordinate hack.
        from draftwright.linting import LintIssue, _suggest_fix

        dwg = plain_box_dwg
        issue = LintIssue(
            severity="warning",
            message="plate thickness 5.0 dropped",
            code="plate_thickness_dropped",
        )
        sug = _suggest_fix(issue, dwg)
        assert sug is not None
        assert "dwg.dimension" in sug
        assert 'role="thickness"' in sug
        assert "pin=True" in sug

    def test_chamfer_dropped_suggestion_mentions_detail_view(self, plain_box_dwg):
        # #641 gap 4: a dropped chamfer leader should steer the user to an enlarged detail view.
        from draftwright.linting import LintIssue, _suggest_fix

        dwg = plain_box_dwg
        issue = LintIssue(
            severity="warning", message="chamfer callout dropped", code="chamfer_dropped"
        )
        sug = _suggest_fix(issue, dwg)
        assert sug is not None
        assert "detail_view=True" in sug

    def test_step_position_dropped_suggestion_mentions_detail_view(self, plain_box_dwg):
        # #641 gap 4: a dropped shoulder position rebuilds as a set, so the fix is to free strip
        # room / use a detail view — NOT to author one shoulder by hand.
        from draftwright.linting import LintIssue, _suggest_fix

        dwg = plain_box_dwg
        issue = LintIssue(
            severity="warning", message="step position dropped", code="step_position_dropped"
        )
        sug = _suggest_fix(issue, dwg)
        assert sug is not None
        assert "detail_view=True" in sug

    def test_unknown_code_has_no_suggestion(self, plain_box_dwg):
        from draftwright.linting import LintIssue, _suggest_fix

        dwg = plain_box_dwg
        issue = LintIssue(severity="info", message="something", code="some_unhandled_code")
        assert _suggest_fix(issue, dwg) is None

    def test_non_integer_diameter_suggestion_uses_identity_not_rounded_text(self):
        part = Box(80, 60, 20) - Pos(20, 15, 0) * Cylinder(4.111, 20)
        dwg = build_drawing(part, auto_dims=False)
        issue = next(
            i
            for i in dwg.lint()
            if i.code == "hole_requirement_missing"
            and any(parameter == "bore.diameter" for _, parameter in i.hole_requirement_ids)
        )
        assert "ø8.2" in issue.message
        assert "dwg.callout(" in issue.suggestion
        exec(issue.suggestion, {"dwg": dwg})
        assert not any(i.code == "feature_not_dimensioned" for i in dwg.lint())

    def test_feature_count_mismatch_cannot_suggest_a_diameter_total(self, plain_box_dwg):
        from draftwright.linting import LintIssue, _suggest_fix

        issue = LintIssue(
            severity="warning",
            message="4 ø8.5 features on the part but callouts account for 1",
            code="feature_count_mismatch",
        )
        suggestion = _suggest_fix(issue, plain_box_dwg)
        assert "count=" not in suggestion
        assert "HoleCallout(" not in suggestion
        assert "distinct axes" in suggestion


class TestRepair:
    """#30/#521: repair is a narrow safety net, not a second placement engine."""

    def test_repair_does_not_fixed_step_annotation_overlap(self):
        # Two dimensions forced onto the same page location → their labels collide. The
        # solver path owns placement; repair must not hide this with a fixed-step nudge.
        from draftwright._core import _dim

        dwg = build_drawing(Box(60, 40, 20))
        d = dwg.draft
        p1, p2 = (40.0, 20.0, 0.0), (80.0, 20.0, 0.0)
        dwg._add(_dim(p1, p2, "above", 8, d, label="AA"), "ov1")
        dwg._add(_dim(p1, p2, "above", 8, d, label="BB"), "ov2")
        assert [i for i in dwg.lint() if i.code == "annotation_overlap"]

        dwg.repair()
        assert dwg.get_annotation("ov1")._dw_spec.distance == 8
        assert dwg.get_annotation("ov2")._dw_spec.distance == 8
        assert [i for i in dwg.lint() if i.code == "annotation_overlap"]

    def test_repair_dim_inside_part_flips_side(self):
        # dim_inside_part is dormant in the multi-view sheet (lint passes no
        # part_bbox), so drive the repair directly: a wrong-side dim flips to
        # the opposite side and keeps its name binding.
        from draftwright._core import _dim
        from draftwright.linting import LintIssue
        from draftwright.repair import _repair_dim_inside_part

        dwg = build_drawing(Box(60, 40, 20))
        dim = dwg._add(_dim((0, 0, 0), (40, 0, 0), "above", 8, dwg.draft, label="INSIDE"), "x")
        assert dim._dw_spec.side == "above"

        issue = LintIssue(
            severity="warning",
            message="Dim 'INSIDE': annotation bbox overlaps part outline by 40%",
            code="dim_inside_part",
        )
        assert _repair_dim_inside_part(dwg, issue) is True
        new = dwg.get_annotation("x")
        assert new is not dim
        assert new._dw_spec.side == "below"
        assert new in dwg.items and dim not in dwg.items

    def test_repair_inside_part_attempted_once_no_oscillation(self):
        # A side flip that does not help must not be re-flipped (oscillation).
        # The same label is only flipped once across the whole loop.
        from draftwright._core import _dim
        from draftwright.linting import LintIssue

        dwg = build_drawing(Box(60, 40, 20))
        dwg._add(_dim((0, 0, 0), (40, 0, 0), "above", 8, dwg.draft, label="OSC"), "x")

        # Monkeypatch lint to always report the same dim_inside_part.
        issue = LintIssue(
            severity="warning",
            message="Dim 'OSC': annotation bbox overlaps part outline by 40%",
            code="dim_inside_part",
        )
        # `physical=` because repair asks for the placement critique only (#1022).
        dwg.lint = lambda **kw: [issue]
        dwg.repair(max_iter=5)
        # Flipped exactly once → ends on "below", not back to "above".
        assert dwg.get_annotation("x")._dw_spec.side == "below"

    def test_repair_idempotent_on_clean_drawing(self):
        # build_drawing already repairs by default, so a second pass is a no-op:
        # same objects, same order.
        dwg = build_drawing(Box(60, 40, 20))
        before = [id(o) for o in dwg.items]
        assert dwg.repair() is dwg
        assert [id(o) for o in dwg.items] == before

    def test_repair_does_not_increase_issue_counts(self):
        # Acceptance: on the existing fixtures, error+warning counts after the
        # repair pass are <= the raw greedy placement — no regressions.
        def ew(dwg):
            return sum(1 for i in dwg.lint() if i.severity in ("error", "warning"))

        for part in (Box(60, 40, 20), _holed_plate(), _uniform_staircase()):
            raw = ew(build_drawing(part, repair=False))
            fixed = ew(build_drawing(part, repair=True))
            assert fixed <= raw

    def test_repair_ignores_annotation_overlap_without_mutation(self):
        # annotation_overlap is no longer repairable (#521). It remains visible
        # to lint rather than being moved by a fixed-step fallback.
        from draftwright._core import _dim
        from draftwright.linting import LintIssue

        dwg = build_drawing(Box(60, 40, 20))
        orig = dwg._add(_dim((0, 0, 0), (40, 0, 0), "above", 8, dwg.draft, label="RB"), "x")
        overlap = LintIssue(
            severity="warning",
            message="labels 'RB' and 'QQ' overlap",
            code="annotation_overlap",
        )
        calls = {"n": 0}

        def fake_lint(**kw):  # repair lints physical=False (#1022)
            calls["n"] += 1
            return [overlap]

        dwg.lint = fake_lint
        dwg.repair(max_iter=3)
        assert dwg.get_annotation("x") is orig
        assert dwg.get_annotation("x")._dw_spec.distance == 8
        assert calls["n"] == 1

    def test_build_drawing_repair_flag_is_respected(self):
        # repair=False leaves the greedy placement untouched; the default repairs.
        from draftwright._core import _dim

        # A clean part is identical either way (nothing to repair).
        a = build_drawing(Box(60, 40, 20), repair=False)
        b = build_drawing(Box(60, 40, 20), repair=True)
        assert [getattr(o, "label", None) for o in a.items] == [
            getattr(o, "label", None) for o in b.items
        ]
        # The factory tags engine dims so repair can re-place them.
        d = _dim((0, 0, 0), (40, 0, 0), "above", 8, a.draft, label="Z")
        assert d._dw_spec.side == "above"


class TestPin:
    """#89: a pinned annotation is never moved by the engine (repair today)."""

    def _two_overlapping(self):
        from draftwright._core import _dim

        dwg = build_drawing(Box(60, 40, 20))
        p1, p2 = (40.0, 20.0, 0.0), (80.0, 20.0, 0.0)
        dwg._add(_dim(p1, p2, "above", 8, dwg.draft, label="AA"), "a")
        dwg._add(_dim(p1, p2, "above", 8, dwg.draft, label="BB"), "b")
        return dwg

    def test_repair_does_not_move_a_pinned_dim(self):
        # Overlaps are not repaired by fixed-step placement anymore, so pinned and
        # unpinned dimensions alike stay put.
        dwg = self._two_overlapping()
        dwg.pin("a")
        dwg.repair()
        assert dwg.get_annotation("a")._dw_spec.distance == 8
        assert dwg.get_annotation("b")._dw_spec.distance == 8

    def test_unpin_lets_repair_move_it_again(self):
        dwg = self._two_overlapping()
        dwg.pin("a").unpin("a")
        dwg.repair()
        assert dwg.get_annotation("a")._dw_spec.distance == 8
        assert dwg.get_annotation("b")._dw_spec.distance == 8
        assert [i for i in dwg.lint() if i.code == "annotation_overlap"]

    def test_pin_unknown_name_raises(self):
        dwg = build_drawing(Box(60, 40, 20))
        with pytest.raises(KeyError):
            dwg.pin("does_not_exist")

    def test_pin_and_unpin_are_chainable(self):
        dwg = self._two_overlapping()
        assert dwg.pin("a") is dwg
        assert dwg.unpin("a") is dwg

    def test_pinning_both_overlap_labels_is_a_noop(self):
        # Both deliberate → the engine respects both and leaves the overlap.
        dwg = self._two_overlapping()
        dwg.pin("a").pin("b")
        dwg.repair()
        assert dwg.get_annotation("a")._dw_spec.distance == 8
        assert dwg.get_annotation("b")._dw_spec.distance == 8

    def test_pinning_a_non_dim_then_repair_does_not_crash(self):
        # _find_dim builds an id-set over pinned objects of any type; pinning a
        # Leader (not a re-placeable dim) must not break repair.
        dwg = self._two_overlapping()
        dwg._add(Leader((0, 0, 0), (10, 10, 0), "L", dwg.draft), "ldr")
        dwg.pin("ldr")
        dwg.repair()  # must not raise
        assert "ldr" in dwg.annotations()

    def test_removed_then_readded_name_is_not_still_pinned(self):
        from draftwright._core import _dim

        dwg = self._two_overlapping()
        dwg.pin("a")
        dwg.remove("a")
        # Re-add a fresh "a" at the same overlapping spot; it must NOT inherit
        # the old pin, so repair is free to move it.
        dwg._add(
            _dim((40.0, 20.0, 0.0), (80.0, 20.0, 0.0), "above", 8, dwg.draft, label="AA"), "a"
        )
        assert not dwg.registry.is_pinned("a")
        dwg.repair()
        assert dwg.get_annotation("a")._dw_spec.distance == 8


class TestAnnotationsQuery:
    """#27: introspect existing annotations by name and type."""

    def test_annotations_maps_name_to_type(self):
        dwg = build_drawing(Box(60, 40, 20))
        anns = dwg.annotations()
        # A dict keyed by the names actually registered, valued by class name.
        assert isinstance(anns, dict)
        assert anns  # a box drawing has named annotations
        assert all(isinstance(k, str) and isinstance(v, str) for k, v in anns.items())
        # Every key resolves, and its reported type matches the live object.
        for name, type_name in anns.items():
            assert type(dwg.get_annotation(name)).__name__ == type_name

    def test_annotations_omits_unnamed(self):
        from draftwright._core import _dim

        dwg = build_drawing(Box(60, 40, 20))
        before = dict(dwg.annotations())
        dwg._add(_dim((0, 0, 0), (40, 0, 0), "above", 8, dwg.draft, label="U"))  # no name
        # Unnamed annotation lands in items but not in the name→type map.
        assert dwg.annotations() == before
        assert len(dwg.items) == len(before) + 1

    def test_annotations_reflects_add_and_membership(self):
        from draftwright._core import _dim

        dwg = build_drawing(Box(60, 40, 20))
        assert "q_dim" not in dwg.annotations()
        dwg._add(_dim((0, 0, 0), (40, 0, 0), "above", 8, dwg.draft, label="Q"), "q_dim")
        assert dwg.annotations()["q_dim"] == "Dimension"

    def test_get_annotation_returns_object_or_none(self):
        from draftwright._core import _dim

        dwg = build_drawing(Box(60, 40, 20))
        obj = dwg._add(_dim((0, 0, 0), (40, 0, 0), "above", 8, dwg.draft, label="G"), "g")
        assert dwg.get_annotation("g") is obj
        assert dwg.get_annotation("does_not_exist") is None

    def test_get_annotation_follows_remove(self):
        from draftwright._core import _dim

        dwg = build_drawing(Box(60, 40, 20))
        dwg._add(_dim((0, 0, 0), (40, 0, 0), "above", 8, dwg.draft, label="R"), "r")
        assert dwg.get_annotation("r") is not None
        dwg.remove("r")
        assert dwg.get_annotation("r") is None
        assert "r" not in dwg.annotations()


class TestViewBounds:
    """#28: page bounding box of a named view's projected geometry."""

    def test_view_bounds_returns_page_bbox(self, plain_box_dwg):
        dwg = plain_box_dwg
        b = dwg.view_bounds("front")
        assert b is not None and len(b) == 4
        x0, y0, x1, y1 = b
        assert x1 > x0 and y1 > y0
        # Front view (looking along Y) shows X=60 wide, Z=20 tall, at sheet scale.
        assert (x1 - x0) == pytest.approx(60 * dwg.scale, rel=1e-3)
        assert (y1 - y0) == pytest.approx(20 * dwg.scale, rel=1e-3)

    def test_view_bounds_contains_projected_centroid(self, plain_box_dwg):
        # The part centroid (world origin for a centred Box) projects inside.
        dwg = plain_box_dwg
        x0, y0, x1, y1 = dwg.view_bounds("front")
        px, py, _ = dwg.at("front", 0, 0, 0)
        assert x0 <= px <= x1
        assert y0 <= py <= y1

    def test_view_bounds_unknown_view_is_none(self, plain_box_dwg):
        assert plain_box_dwg.view_bounds("does_not_exist") is None

    def test_view_bounds_for_each_standard_view(self, plain_box_dwg):
        dwg = plain_box_dwg
        for v in ("front", "plan", "side", "iso"):
            b = dwg.view_bounds(v)
            assert b is not None, v
            x0, y0, x1, y1 = b
            assert x1 > x0 and y1 > y0, v

    def test_view_bounds_includes_hidden_lines(self):
        # Bounds union the visible and hidden silhouettes. Replace the front
        # view's hidden compound with one that extends past the visible box and
        # confirm the right edge moves out to it.
        dwg = build_drawing(Box(60, 40, 20))
        vis, _ = dwg.views["front"]
        _, _, x1, _ = dwg.view_bounds("front")
        far = Compound(children=[Edge.make_line((x1 + 10, 0, 0), (x1 + 10, 5, 0))])
        dwg.views["front"] = (vis, far)
        assert dwg.view_bounds("front")[2] == pytest.approx(x1 + 10)


def _x_stepped_shaft():
    """A turned shaft lying along X: ø30 (len 40) then ø16 (len 30).

    Built about Z then rotated so the turning axis is X — the orientation that
    is *not* flagged rotational (the OD logic is Z-centric), exercising #77.
    """
    return Rotation(0, 90, 0) * (Cylinder(15, 40) + Pos(0, 0, 35) * Cylinder(8, 30))


def _compiled_step_length_ids(dwg):
    from draftwright.model.compiled import compile_dimensions

    ids = {
        dimension.id
        for group in compile_dimensions(dwg.model()).of_kind("step")
        if (dimension := group.dim(kind="length")) is not None
    }
    assert None not in ids
    return ids


class TestPrismaticBossDiameter:
    """#629: a boss on a PRISMATIC part gets its ø as a plan-view leader to the boss
    circle (names ``m_bossdia_*``), free to exit into clear margin — not the turned
    column-left strip (``m_dia_z``), which strands the ø when that narrow strip is
    tight even on a half-empty sheet. Turned parts keep the OD-stack column."""

    @staticmethod
    def _box_boss():
        return Box(90, 64, 38) + Pos(0, 0, 24) * Cylinder(14, 10)

    @staticmethod
    def _shelled_cover():
        # The #629 report: a shelled cover whose front view hugs the left margin, so
        # the column-left ø strip has no room and the boss ø28 was dropped.
        return (
            Box(90, 64, 38)
            - Pos(0, 0, -3) * Box(84, 58, 38)
            + Pos(0, 0, 24) * Cylinder(14, 10)
            - Pos(0, 0, 15) * Cylinder(6, 40)
        )

    def test_prismatic_boss_diameter_is_a_plan_leader(self):
        dwg = build_drawing(self._box_boss())
        # the boss ø routes to the plan-view leader path, not the turned column
        assert any(n.startswith("m_bossdia_") for n in dwg.annotations())
        boss = next(o for n, o in dwg.iter_annotations() if n.startswith("m_bossdia_"))
        assert boss.label == "ø28"
        # and it is not ALSO emitted by the turned column (no double-dimensioning)
        assert not any(
            o.label == "ø28" for n, o in dwg.iter_annotations() if n.startswith("m_dia_z")
        )

    def test_prismatic_boss_height_is_dimensioned_in_profile(self):
        # #632: the boss's own 10 mm axial extent is independent of the 48 mm
        # overall envelope and must be modeled and rendered explicitly.
        dwg = build_drawing(self._box_boss())
        heights = [(n, o) for n, o in dwg.iter_annotations() if n.startswith("m_bossheight_")]
        assert len(heights) == 1
        name, dim = heights[0]
        assert dim.label == "10"
        assert dwg.view_of(name) == "front"
        assert not any(i.code == "boss_height_missing" for i in dwg.lint())

    def test_removed_boss_height_is_linted_from_live_drawing(self):
        # Coverage is drawing-derived: deleting the rendered height must expose the
        # gap even though the overall envelope and boss diameter remain present.
        dwg = build_drawing(self._box_boss())
        height_name = next(n for n in dwg.annotations() if n.startswith("m_bossheight_"))
        dwg.remove(height_name)
        issues = [i for i in dwg.lint() if i.code == "boss_height_missing"]
        assert len(issues) == 1
        assert issues[0].severity == "warning"

    def test_unplaceable_boss_height_reports_one_authoritative_issue(self):
        # Adversarial review of #632: a physically absent corridor forces the
        # candidate's drop path. Reconciliation must report the omission once,
        # rather than adding boss_height_dropped + boss_height_missing for one gap.
        from types import SimpleNamespace

        from draftwright.annotations._common import PlacementContext, drain_corridors
        from draftwright.annotations.from_model import render_boss_heights
        from draftwright.model import plan_dimensions
        from draftwright.model.compiled import compile_dimensions

        dwg = build_drawing(self._box_boss(), auto_dims=False)
        analysis = dwg._analysis
        constrained = SimpleNamespace(
            **{
                **vars(analysis),
                "fv_zones": SimpleNamespace(**{**vars(analysis.fv_zones), "right": None}),
            }
        )
        ctx = PlacementContext(registry=dwg.registry, coverage=dwg.coverage, items=dwg.items)
        groups = plan_dimensions(dwg.model())
        render_boss_heights(
            dwg, compile_dimensions(dwg.model(), groups=groups), constrained, ctx=ctx
        )
        drain_corridors(ctx, dwg)

        boss_issues = [i for i in dwg.lint() if i.code.startswith("boss_height_")]
        assert [i.code for i in boss_issues] == ["boss_height_missing"]

    def test_declared_boss_object_carries_height_into_the_model(self):
        from draftwright import Sheet

        boss_obj = Pos(0, 0, 24) * Cylinder(14, 10)
        sheet = Sheet.from_part(Box(90, 64, 38) + boss_obj)
        sheet.boss(boss_obj)
        feature = next(f for f in sheet.build().model().features if f.kind == "boss")
        assert feature.height == pytest.approx(10)
        assert feature.span is not None

    def test_issue_632_declarative_cover_reconciles_rendered_boss_height(self):
        """The original #632 repro stays covered end-to-end: declaration → IR →
        planner → renderer → lint, including its shell, bore, and pocket declarations."""
        from draftwright import Sheet

        wall, length, width, height = 3.0, 90, 64, 38
        pocket = Pos(0, 0, -wall) * Box(length - 2 * wall, width - 2 * wall, height)
        boss = Pos(0, 0, height / 2 + 5) * Cylinder(14, 10)
        bore = Pos(0, 0, 15) * Cylinder(6, 40)
        cover = Box(length, width, height) - pocket + boss - bore

        sheet = Sheet(cover, title="C").auto_dimensions()
        sheet.envelope()
        sheet.boss(boss)
        sheet.hole(bore).through()
        sheet.pocket(pocket)
        dwg = sheet.build()

        height_name = next(n for n in dwg.annotations() if n.startswith("m_bossheight_"))
        assert dwg.get_annotation(height_name).label == "10"
        assert dwg.lint_summary()["by_code"].get("boss_height_missing", 0) == 0

        dwg.remove(height_name)
        assert dwg.lint_summary()["by_code"]["boss_height_missing"] == 1

    def test_issue_631_step_on_boss_is_reported(self):
        # #1132: this reports instead of raising. `generate_sheet_script` settles its
        # layout through the same predicate, so raising meant a part whose recognised
        # profile does not tile produced no script and no drawing at all. A generated
        # script is indistinguishable from hand-written code when re-run, so there is
        # no provenance to branch on. #631's substance is preserved: the misuse is
        # still detected and still named — loudly in lint rather than as an exception.
        # #631: reaching for .step(boss) declared a z-turned segment at the boss cylinder,
        # which flipped the height ladder into turned-suppression and silently dropped the
        # overall height (net −1 annotation, no height dim). The wrong-verb misuse must fail
        # loudly instead — .boss() is the verb for a boss. Covers both the lone .step(boss)
        # and the .boss(boss)+.step(boss) collision (both z-oriented on a prismatic body).
        from draftwright import Sheet

        boss = Pos(0, 0, 24) * Cylinder(14, 10)
        part = Box(90, 64, 38) + boss
        for extra_boss in (False, True):
            sheet = Sheet(part, title="C").auto_dimensions()
            sheet.envelope()
            if extra_boss:
                sheet.boss(boss)
            sheet.step(boss)
            codes = sheet.build().lint_summary()["by_code"]
            assert codes.get("turned_profile_not_spanned") == 1

    def test_issue_631_stepped_boss_on_plate_is_reported(self):
        # #1132: this reports instead of raising. `generate_sheet_script` settles its
        # layout through the same predicate, so raising meant a part whose recognised
        # profile does not tile produced no script and no drawing at all. A generated
        # script is indistinguishable from hand-written code when re-run, so there is
        # no provenance to branch on. #631's substance is preserved: the misuse is
        # still detected and still named — loudly in lint rather than as an exception.
        # The guard keys on the exact suppression premise (do the steps span the full
        # height?), not a rotational classifier — so even a stepped boss whose two stacked
        # cylinders read as a turned PROFILE, sat on a square plate, is caught: the steps
        # cover only the boss, not the plate below, so the overall height would be dropped.
        from draftwright import Sheet

        # Exact face contact makes this one physical solid: the old 25/35 placements left a
        # 10 mm air gap above the plate and accidentally tested a valid multi-solid compound.
        lower = Pos(0, 0, 15) * Cylinder(35, 10)
        upper = Pos(0, 0, 25) * Cylinder(25, 10)
        part = Box(100, 100, 20) + lower + upper
        sheet = Sheet(part, title="C").auto_dimensions()
        sheet.envelope()
        sheet.step(lower)
        sheet.step(upper)
        codes = sheet.build().lint_summary()["by_code"]
        assert codes.get("turned_profile_not_spanned") == 1

    def test_issue_631_interior_gap_between_steps_is_reported(self):
        # #1132: this reports instead of raising. `generate_sheet_script` settles its
        # layout through the same predicate, so raising meant a part whose recognised
        # profile does not tile produced no script and no drawing at all. A generated
        # script is indistinguishable from hand-written code when re-run, so there is
        # no provenance to branch on. #631's substance is preserved: the misuse is
        # still detected and still named — loudly in lint rather than as an exception.
        # Coverage is a union tiling, not a reach-to-each-end check: two z-steps that touch
        # both ends of a z=[0,40] body but leave a 15..25 interior gap do NOT convey the
        # full height (the gap length is unmeasured), so this must still raise.
        from draftwright.model.declare import step

        part = Pos(0, 0, 20) * Cylinder(10, 40)  # z-extent [0, 40]
        model = [
            step(diameter=20, length=15, at=(0, 0, 7.5), axis="z"),  # z [0, 15]
            step(diameter=20, length=15, at=(0, 0, 32.5), axis="z"),  # z [25, 40]
        ]
        codes = build_drawing(part, model=model, number="X").lint_summary()["by_code"]
        assert codes.get("turned_profile_not_spanned") == 1

    def test_shelled_cover_boss_diameter_not_dropped(self):
        # The regression: even forced onto A4 (scale 0.5, front view against the left
        # margin) the boss ø28 places into the clear sheet, so it never lints uncovered.
        dwg = build_drawing(self._shelled_cover(), page="A4")
        assert any(
            o.label == "ø28" for _, o in dwg.iter_annotations() if getattr(o, "label", None)
        )
        assert dwg.lint_summary()["by_code"].get("feature_not_dimensioned", 0) == 0

    def test_boss_diameter_carries_authored_tolerance(self):
        # The pass must consume the planner's DimParameter (value + tolerance/fit), not raw
        # geometry — formatting b.diameter directly dropped an authored ⌀ tolerance, and then
        # blocked render_diameters via `mentioned`, losing it silently (gpt-5.6-sol review).
        from draftwright import Sheet

        s = Sheet.from_part(Box(90, 64, 38) + Pos(0, 0, 24) * Cylinder(14, 10))
        s.of(Pos(0, 0, 24) * Cylinder(14, 10)).tolerance(0.0, 0.1)  # tolerance the boss ⌀28
        dwg = s.build()
        labels = [o.label for n, o in dwg.iter_annotations() if n.startswith("m_bossdia_")]
        assert labels and any("0.1" in str(lbl) for lbl in labels), labels

    def test_turned_part_boss_stays_in_the_column(self):
        # A rotational shaft's step/OD diameters keep the m_dia column — render_boss_diameters
        # is a prismatic-only pass and must not fire on a turned body.
        dwg = build_drawing(Cylinder(15, 40) + Pos(0, 0, 35) * Cylinder(10, 30))
        assert not any(n.startswith("m_bossdia_") for n in dwg.annotations())
        assert any(n.startswith("m_dia_z") for n in dwg.annotations())
        assert not any(i.code == "boss_height_missing" for i in dwg.lint())


class TestTurnedDiameters:
    """External turned diameters get ø leader callouts through the IR renderer."""

    @staticmethod
    def _issue_881_y_step_flange():
        """A non-rotational four-lug flange with a coaxial Y-axis stepped stack."""
        part = Cylinder(21, 4)
        for x in (-18, 18):
            for y in (-18, 18):
                part = part + Pos(x, y, 2) * Box(
                    10,
                    10,
                    4,
                    align=(Align.CENTER, Align.CENTER, Align.CENTER),
                )
        # Deliberate overlap keeps this one solid while leaving distinct axial bands.
        part = part + Pos(0, 0, 2) * Cylinder(15.5, 12)
        part = part + Pos(0, 0, 10) * Cylinder(12.5, 12)
        part = part - Cylinder(8, 30)
        for x in (-18, 18):
            for y in (-18, 18):
                part = part - Pos(x, y, 0) * Cylinder(2, 10)
        return part.rotate(Axis.X, 90)

    @staticmethod
    def _issue_890_cardinal_hole_flange():
        """The same stepped stack with bolt holes on end-view cardinal rays."""
        locations = ((18, 0), (-18, 0), (0, 18), (0, -18))
        part = Cylinder(21, 4)
        for x, y in locations:
            part += Pos(x, y, 2) * Box(
                10,
                10,
                4,
                align=(Align.CENTER, Align.CENTER, Align.CENTER),
            )
        part += Pos(0, 0, 2) * Cylinder(15.5, 12)
        part += Pos(0, 0, 10) * Cylinder(12.5, 12)
        part -= Cylinder(8, 30)
        for x, y in locations:
            part -= Pos(x, y, 0) * Cylinder(2, 10)
        return part.rotate(Axis.X, 90)

    @staticmethod
    def _issue_892_y_chain(*, axis_z=0.0, rotation=90):
        b = Align.MIN
        part = Cylinder(22.5, 3, align=(Align.CENTER, Align.CENTER, b))
        part += Pos(0, 0, 3) * Cylinder(17, 5.5, align=(Align.CENTER, Align.CENTER, b))
        part += Pos(0, 0, 8.5) * Cylinder(14, 3.5, align=(Align.CENTER, Align.CENTER, b))
        return Pos(0, 0, axis_z) * part.rotate(Axis.X, rotation)

    @staticmethod
    def _assert_y_diameter_leaders_clear_holes(dwg):
        circles = []
        for feature in dwg.model().features:
            if feature.frame.axis != "y":
                continue
            if feature.kind == "hole":
                diameter = feature.diameter
                locations = feature.members or (feature.frame.origin,)
            elif feature.kind == "pattern":
                diameter = feature.member.diameter
                locations = feature.members or (feature.frame.origin,)
            else:
                continue
            for location in locations:
                x, y, *_ = dwg.at("front", *location)
                circles.append((x, y, diameter / 2 * dwg.scale))

        for name in (n for n in dwg.annotations() if n.startswith("m_dia_y")):
            ann = dwg.get_annotation(name)
            ax, ay = ann.tip[:2]
            bx, by = ann.elbow[:2]
            vx, vy = bx - ax, by - ay
            length2 = vx * vx + vy * vy
            for cx, cy, radius in circles:
                t = max(0.0, min(1.0, ((cx - ax) * vx + (cy - ay) * vy) / length2))
                distance = math.hypot(cx - (ax + t * vx), cy - (ay + t * vy))
                assert distance > radius

    def test_issue_881_y_axis_steps_render_without_half_envelope_locations(self):
        dwg = build_drawing(self._issue_881_y_step_flange())

        steps = [f for f in dwg.model().features if f.kind == "step"]
        assert steps and {f.frame.axis for f in steps} == {"y"}

        y_diameters = {
            dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("m_dia_y")
        }
        assert y_diameters == {"ø25", "ø31", "ø42"}
        # #890: each queued radial leader retains its own StepFeature provenance;
        # a lazy generator previously captured the final loop iteration's owner.
        diameter_owners = [
            feature for feature in dwg.model().features if feature.kind in {"step", "boss"}
        ]
        # Four since quiddity 0.2.8, which coalesces contiguous equal-diameter bands: the
        # flange's profile is four tiling segments (ø25 -16..-8, ø31 -8..-2, ø42 -2..2,
        # ø31 2..4) where the provider previously reported each split in two. #890's subject
        # is per-leader provenance, which four distinct owners exercise exactly as eight did.
        assert len(diameter_owners) == 4
        for name in (n for n in dwg.annotations() if n.startswith("m_dia_y")):
            ann = dwg.get_annotation(name)
            owner = dwg.registry.feature_of(name)
            diameter = float(ann.label.removeprefix("ø"))
            matches = [feature for feature in diameter_owners if feature.diameter == diameter]
            assert any(owner is match for match in matches)
            (identity,) = dwg.registry.measurement_of(name)
            assert identity.feature is owner and identity.parameter == f"{owner.kind}.diameter"
        assert all(
            dwg.registry.has_measurement(DimensionId(owner, f"{owner.kind}.diameter"))
            for owner in diameter_owners
        )

        self._assert_y_diameter_leaders_clear_holes(dwg)
        step_labels = {dwg.get_annotation(n).label for n in dwg.annotations() if "steplen" in n}
        assert step_labels >= {"8", "4"}
        assert "2" in step_labels or "4× 2" in step_labels
        assert {dwg.view_of(n) for n in dwg.annotations() if n.startswith("m_steplen")} == {"side"}
        # Since quiddity 0.2.8 the chain is 8 | 6 | 4 | 2, and the 2 mm segment is below the
        # legibility floor, so this fixture now routes to an enlarged detail. That splits the
        # provenance in two, exactly as `test_issue_892_short_y_step_chain_moves_to_enlarged_
        # side_detail` documents: the detail carries one claimed dimension per step, and the
        # main view keeps the aggregate block, which claims nothing because it "is not any one
        # approved step measurement". Assert both halves rather than only the surviving one.
        detail_steps = [n for n in dwg.annotations() if n.startswith("dim_detail_a_steplen")]
        assert detail_steps, "the crowded chain must be sized somewhere"
        assert all(len(dwg.measurement_keys(n)) == 1 for n in detail_steps)
        for name in (n for n in dwg.annotations() if n.startswith("m_steplen")):
            label = dwg.get_annotation(name).label
            if label == "4× 2":
                expected = 4
            elif "detail_a" in dwg.views:
                expected = 0  # the aggregate block
            else:
                expected = 1
            assert len(dwg.measurement_keys(name)) == expected

        # The central Y-axis bore shares the detected turned-profile axis. Its
        # centreline locates it; generic minimum-edge offsets would redundantly
        # show half the 46 mm envelope in both X and Z (#881).
        assert dwg.view_of("centerline_side") == "side"
        # RaisedPad v2 no longer misclassifies two rotated flange lugs as pads. With that
        # false requirement gone, ADR 2 (was 0018) omits the redundant plan view and its furniture;
        # the front profile plus side end view still define the Y-axis stack completely.
        assert dwg.view_of("centerline_plan") is None
        assert "plan" not in dwg.views
        assert not any(n.startswith("dim_loc_front_") for n in dwg.annotations())
        assert not any(n.startswith("dim_loc_side_") for n in dwg.annotations())

        codes = {issue.code for issue in dwg.lint()}
        assert "feature_not_dimensioned" not in codes
        assert "axial_length_missing" not in codes

        # #798: the bolt-circle callout spends 27.6 mm of its 49 mm shaft inside the
        # flange body. This assertion used to read `not in codes` and passed only
        # because the check was blind: the outline-crossing form exempted every
        # `covers_diameters` annotation wholesale, and this is a hole callout. Measured
        # against the filled material field the cut is real, and pinning WHICH leader
        # is a stronger guard than the absence it replaces. Front-view hole callouts
        # keep their specialised placer under ADR 2 (was 0014), so routing this one clear is
        # #798's own remaining work.
        silhouette = [i for i in dwg.lint() if i.code == "leader_crosses_silhouette"]
        assert len(silhouette) == 1, [i.message for i in silhouette]
        assert "4× ⌀4 THRU" in silhouette[0].message

        for name in tuple(dwg.annotations()):
            if "steplen" in name:
                dwg.remove(name)
        assert "axial_length_missing" in {issue.code for issue in dwg.lint()}

    @pytest.mark.parametrize(
        ("overall", "step", "expect_height", "expect_rungs"),
        [
            (False, False, False, False),
            (True, False, True, False),
            (False, True, False, True),
            (True, True, True, True),
        ],
        ids=["neither", "overall-only", "step-only", "both"],
    )
    def test_each_ladder_intent_draws_only_what_it_recorded(
        self, overall, step, expect_height, expect_rungs
    ):
        """`render_height_ladder` draws TWO independent things, and the drain passed it the
        whole compiled plan once EITHER intent was present.

        So `overall_height()` alone also rebuilt the step rungs — a dimension nobody recorded,
        and live/deferred divergence in the change that relies on their equivalence (#934
        review). The converse held too: a step-height intent carried the overall height along,
        so commenting out `dwg.overall_height()` in a generated script need not have removed it.

        A model with BOTH approved ladders is required to see this at all. Every earlier
        fixture exposes one, which is why cross-contamination was invisible: with a single
        ladder, "pass everything" and "pass what was asked for" are the same plan.

        Asserted on WHICH ladders appear, not how many marks: with both recorded, the strip
        legitimately drops a rung for room, and pinning counts would make this test fail on
        placement changes that have nothing to do with the property.
        """
        from draftwright.model.ir import Frame, PartModel, StepLevelFeature

        part = Box(80, 60, 30)
        model = PartModel(
            bbox=part.bounding_box(),
            orientation="prismatic",
            features=[
                StepLevelFeature(
                    Frame((0, 0, 0), "z"),
                    base=-15,
                    levels=(-5, 5),
                    shoulders=(("x", 0),),
                    datum=(-40, -30, -15),
                )
            ],
            datums=[],
        )
        dwg = build_drawing(part, model=model, auto_dims=False)
        feature = dwg.model().features[0]
        with dwg.deferred():
            if overall:
                dwg.overall_height()
            if step:
                dwg.dimension(feature, "length", role="step_height")

        drawn = {n for n, _ in dwg.iter_annotations()}
        assert ("dim_height" in drawn) is expect_height
        assert any(n.startswith("dim_step") for n in drawn) is expect_rungs

    def test_overall_height_live_and_deferred_agree_with_a_step_ladder_present(self):
        """The equivalence, on the model that can break it.

        `test_the_overall_height_intent_is_commentable_not_injected` uses a part with no
        step_level, so its live and deferred results agreed even while the drain was drawing
        every ladder it could find. The ladder itself is deferred-only by construction (a
        correlated set, routed at the drain), so the overall height is the half where live and
        deferred are both reachable — and therefore the half that can disagree."""
        from draftwright.model.ir import Frame, PartModel, StepLevelFeature

        part = Box(80, 60, 30)

        def _model():
            return PartModel(
                bbox=part.bounding_box(),
                orientation="prismatic",
                features=[
                    StepLevelFeature(
                        Frame((0, 0, 0), "z"),
                        base=-15,
                        levels=(-5, 5),
                        shoulders=(("x", 0),),
                        datum=(-40, -30, -15),
                    )
                ],
                datums=[],
            )

        live = build_drawing(part, model=_model(), auto_dims=False)
        live.overall_height()
        deferred = build_drawing(part, model=_model(), auto_dims=False)
        with deferred.deferred():
            deferred.overall_height()
        assert deferred.annotations() == live.annotations()

    @pytest.mark.parametrize("deferred", [False, True], ids=["live", "deferred"])
    def test_overall_height_is_refused_when_the_model_declares_an_envelope(self, deferred):
        """One measurement, one verb.

        `overall_height()` exists ONLY for the featureless fallback — a model with no
        `EnvelopeFeature`, whose height comes from the bounding box and has nothing to name.
        It did not enforce that, so on an enveloped model both public spellings were
        available and composing them drew the height twice:

            live,  overall_height() then dimension(env, …, role="height")  →  BOTH
            live,  the reverse order                                       →  one
            deferred, either order                                         →  one

        Order-dependent live AND live ≠ deferred, from two spellings of one measurement —
        the "three spellings of pin" problem (#906) in miniature (#934 review).

        Refused before the deferred/live split, so both routes answer identically. That is
        the shape #925 settled for `callout()`: a check on one side makes the answer depend
        on whether you are inside `deferred()`.
        """
        from draftwright.model.declare import envelope

        part = Box(80, 60, 30)
        dwg = build_drawing(part, model=[envelope(part)], auto_dims=False)
        with pytest.raises(ValueError, match="declares an envelope"):
            if deferred:
                with dwg.deferred():
                    dwg.overall_height()
            else:
                dwg.overall_height()

    @pytest.mark.parametrize("deferred", [False, True], ids=["live", "deferred"])
    def test_an_enveloped_height_is_drawn_once_by_its_feature_verb(self, deferred):
        """The false-positive half: refusing the second spelling must not cost the first.

        The enveloped model's height is still dimensionable — through the feature that owns
        it — and exactly once, on both routes."""
        from draftwright.model.declare import envelope

        part = Box(80, 60, 30)
        dwg = build_drawing(part, model=[envelope(part)], auto_dims=False)
        feature = dwg.model().features[0]
        if deferred:
            with dwg.deferred():
                dwg.dimension(feature, "length", role="height")
        else:
            dwg.dimension(feature, "length", role="height")
        heights = [
            a.label
            for n, a in dwg.iter_annotations()
            if n.startswith(("dim_height", "dim_length"))
        ]
        assert heights == ["30"], f"the height should be drawn once, got {heights}"

    def test_the_overall_height_intent_is_commentable_not_injected(self):
        """The half that the first fix got wrong, and the reason there is a verb at all.

        Making the drain draw the overall height whenever the compiler approved one restored
        #889's parity — and broke record-then-finalize == place-live, because `auto_dims=False`
        means the recorded verbs ARE the drawing and an automatic dimension nobody asked for
        appeared in the deferred result. `test_finalize_replay_equals_live_placement` caught it.

        So it is an INTENT: absent unless recorded, which also makes it commentable, which is
        the property the whole intent-level script rests on (ADR 4 (was 0016), "the script records
        intent").
        """
        # The verb's actual domain: a part with NO `EnvelopeFeature`, so the height comes
        # from the bounding-box fallback and there is nothing to name. (An enveloped model
        # refuses this verb and uses its feature instead — see the test above; this fixture
        # asserted the property on an enveloped plate, which is the overlap itself.)
        part = self._issue_881_y_step_flange()
        assert not any(f.kind == "envelope" for f in build_drawing(part).model().features)

        silent = build_drawing(part, auto_dims=False)
        assert "dim_height" not in silent.annotations(), "not recorded ⇒ not drawn"

        live = build_drawing(part, auto_dims=False)
        assert live.overall_height() == ["dim_height"]

        deferred = build_drawing(part, auto_dims=False)
        with deferred.deferred():
            deferred.overall_height()
        assert deferred.annotations() == live.annotations(), "record-then-finalize == live"

    def test_overall_height_round_trips_through_generated_script(self, tmp_path):
        """#889: the replay dropped the automatic overall height, silently and lint-clean.

        Two different things share `render_height_ladder`, and the drain gated BOTH on the
        step-ladder intent. The step-height LADDER is a `step_level` feature's correlated
        rungs, so one recorded intent meaning "rebuild the whole chain" is right. The OVERALL
        HEIGHT is envelope furniture — and on a part with no `EnvelopeFeature` it comes from
        the compiler's bounding-box fallback, so there is NO feature for a script to record an
        intent against. It could never be replayed, only lost.

        The Y-axis stepped flange is the case that exposes it: `step` features but no
        `step_level`, so no ladder intent exists to carry the overall height along.

        Asserted as full annotation-set parity rather than "dim_height is present", because
        the acceptance is that replay matches the automatic drawing — and the risk on the
        other side is duplicating the Y-step length chain, which a presence check would miss.

        Retargeted onto the Sheet script by #940. This fixture also carries what
        `test_issue_881_generated_script_emits_y_step_intents` used to assert about the
        imperative script's TEXT: that suite's executable half — side/plan centrelines, no
        front/side location dims, the step-length chain on the side view — is folded in
        below, since the source-text half described a file that no longer exists.
        """
        part = self._issue_881_y_step_flange()
        from math import pi

        for x in (-18, 18):
            for z in (-18, 18):
                lug = Pos(x, -2, z) * Box(10, 4, 10)
                assert (part & lug).volume == pytest.approx(400 - pi * 2**2 * 4)
        auto = build_drawing(part)
        assert not any(f.kind == "pocket" for f in auto.model().features)
        assert not any(f.kind == "envelope" for f in auto.model().features), (
            "the fixture must have NO envelope feature — the bbox fallback is the case "
            "with no intent to record"
        )

        _source, replayed = _sheet_script_drawing(part, tmp_path, "flange")

        automatic = {n for n, _ in auto.iter_annotations()}
        replay = {n for n, _ in replayed.iter_annotations()}
        assert "dim_height" in automatic, "the fixture must draw an overall height to lose"
        assert replay == automatic, (
            f"replay differs — missing {sorted(automatic - replay)}, "
            f"extra {sorted(replay - automatic)}"
        )
        assert (
            auto.get_annotation("dim_height").label == replayed.get_annotation("dim_height").label
        )
        # The off-axis four-hole pattern has relative pitch/count but no absolute X/Z
        # location dimensions. The hole-family ledger added by #1143 reports those two
        # physical requirements honestly on both paths; reconstruction must preserve the
        # same critique as well as the same annotation set.
        # The `leader_crosses_silhouette` entry is the #798 bolt-circle cut described in
        # test_issue_881_...; it appears on BOTH paths, which is what this test is
        # actually about — the replay reproduces the same critique, defects included.
        # Candidate prevention (#1334) removes the same-batch step-chain crossings, and
        # measured block clearance now clears the former cross-producer ink crossing. The
        # remaining critique is reproduced on both paths.
        # Quiddity refuses four recess proposals at the solid mounting lugs. The
        # material-volume checks above prohibit reviving the old false pocket claims.
        assert (
            auto.lint_summary()["by_code"]
            == replayed.lint_summary()["by_code"]
            == {
                "hole_requirement_missing": 2,
                "leader_crosses_silhouette": 1,
                "section_recess_recognition_refused": 4,
            }
        )

        # #1512's two crossings no longer occur on this fixture. They were the restored 6 mm
        # and 4 mm boss-height witnesses cutting the step chain's `4× 2` repeat label, and
        # quiddity 0.2.8 leaves neither on this view: the coalesced 8 | 6 | 4 | 2 chain routes
        # to an enlarged detail, which carries all four as claimed dimensions. Nothing was
        # dropped to achieve that — the detail assertions above account for every step.
        #
        # This is the reproduction disappearing, NOT the placement debt being paid: the
        # bounded same-batch ink solver still never considers the combined cross-pass set,
        # which is what #1512 is actually about. Asserted as absence so a reappearance here
        # fails rather than passing quietly under a laxer check.
        for drawing in (auto, replayed):
            assert [i for i in drawing.lint() if i.code == "annotation_ink_overlap"] == []

        # ── from #881: the Y-step furniture lands in the right views on the replay ──
        assert replayed.view_of("centerline_side") == "side"
        assert replayed.view_of("centerline_plan") is None
        assert "plan" not in replayed.views
        assert not any(n.startswith(("dim_loc_front_", "dim_loc_side_")) for n in replay)
        assert {replayed.view_of(n) for n in replay if n.startswith("m_steplen")} == {"side"}

    @pytest.mark.parametrize(("axis_z", "rotation"), [(0.0, 90), (17.0, 90), (-11.0, -90)])
    def test_issue_892_short_y_step_chain_moves_to_enlarged_side_detail(self, axis_z, rotation):
        # P10-base axial profile: 3 | 5.5 | 3.5 mm along Y.  Centred labels crowd at
        # 1:1, but lifting the middle 5.5 onto a far tier makes it read like an
        # overall dimension. Keep only the 12 mm block on the side view and redraw
        # the three shoulder-to-shoulder links in a genuine enlarged side detail.
        dwg = build_drawing(
            self._issue_892_y_chain(axis_z=axis_z, rotation=rotation),
            scale=1.0,
            scale_policy="permissive",
        )

        main = {n: o for n, o in dwg.iter_annotations() if n.startswith("m_steplen")}
        assert {o.label for o in main.values()} == {"12"}
        assert {dwg.view_of(n) for n in main} == {"side"}
        assert "detail_a" in dwg.views

        detail = {n: o for n, o in dwg.iter_annotations() if n.startswith("dim_detail_a_steplen")}
        assert {o.label for o in detail.values()} == {"3", "5.5", "3.5"}
        assert {dwg.view_of(n) for n in detail} == {"detail_a"}
        assert len({round(o._dw_spec.distance, 6) for o in detail.values()}) == 1
        assert all(dwg.measurement_keys(name) == [] for name in main), (
            "the aggregate block is not any one approved step measurement"
        )
        assert all(len(dwg.measurement_keys(name)) == 1 for name in detail)

        labels = sorted((o.label_bbox for o in detail.values()), key=lambda bb: bb[0])
        assert all(
            left[2] + dwg.draft.pad_around_text <= right[0] + 1e-6
            for left, right in zip(labels, labels[1:])
        )
        marker = dwg.get_annotation("detail_marker_A").bounding_box()
        axis_page_y = dwg.at("side", 0, 0, axis_z)[1]
        assert marker.min.Y <= axis_page_y <= marker.max.Y

    def test_issue_892_toleranced_labels_drive_detail_scale_from_rendered_text(self):
        from draftwright import Sheet

        part = self._issue_892_y_chain(axis_z=9.0)
        sheet = Sheet(part, scale=1.0, page="A2", scale_policy="permissive").auto_dimensions()
        sheet.step(diameter=45, length=3, at=(0, -1.5, 9), axis="y").tolerance(0.2)
        sheet.step(diameter=34, length=5.5, at=(0, -5.75, 9), axis="y").tolerance(0.0, 0.3)
        sheet.step(diameter=28, length=3.5, at=(0, -10.25, 9), axis="y")
        dwg = sheet.build()

        detail = [o for n, o in dwg.iter_annotations() if n.startswith("dim_detail_a_steplen")]
        assert len(detail) == 3
        assert {o._dw_scale for o in detail} == {10.0}
        labels = sorted((o.label_bbox for o in detail), key=lambda bb: bb[0])
        assert all(
            left[2] + dwg.draft.pad_around_text <= right[0] + 1e-6
            for left, right in zip(labels, labels[1:])
        )

    def test_issue_892_clear_labels_but_tight_arrows_still_request_detail(self):
        # Single-digit labels clear one another at 1:1, but the 3/5/4 mm spans
        # cannot contain text plus two inside arrowheads. Outside-arrow tails on
        # the principal chain would intrude into neighbouring links.
        b = Align.MIN
        part = Cylinder(12, 3, align=(Align.CENTER, Align.CENTER, b))
        part += Pos(0, 0, 3) * Cylinder(10, 5, align=(Align.CENTER, Align.CENTER, b))
        part += Pos(0, 0, 8) * Cylinder(8, 4, align=(Align.CENTER, Align.CENTER, b))
        dwg = build_drawing(part.rotate(Axis.X, 90), scale=1.0, scale_policy="permissive")

        assert "detail_a" in dwg.views
        detail = {
            o.label for n, o in dwg.iter_annotations() if n.startswith("dim_detail_a_steplen")
        }
        assert detail == {"3", "5", "4"}

    def test_issue_892_no_detail_room_keeps_block_and_reports_uncovered_shoulders(self):
        # Transactional failure: a deliberately undersized sheet has no rectangle
        # for the enlarged profile. Do not leave half a detail or reinstate the
        # ambiguous stagger; retain the overall block and let coverage lint expose
        # that the interior shoulders are not located.
        dwg = build_drawing(
            self._issue_892_y_chain(),
            scale=1.0,
            page="140x100",
            scale_policy="permissive",
        )
        assert "detail_a" not in dwg.views
        main = [o for n, o in dwg.iter_annotations() if n.startswith("m_steplen")]
        assert [o.label for o in main] == ["12"]
        assert dwg.lint_summary()["by_code"].get("axial_length_missing", 0) == 1

    def test_issue_890_rotated_bolt_pattern_selects_clear_diameter_rays(self):
        self._assert_y_diameter_leaders_clear_holes(
            build_drawing(self._issue_890_cardinal_hole_flange())
        )

    def test_issue_881_y_axis_steps_replay_through_deferred_intents(self):
        dwg = build_drawing(self._issue_881_y_step_flange(), auto_dims=False)
        steps = [f for f in dwg.model().features if f.kind == "step"]

        with dwg.deferred():
            for feature in steps:
                dwg.callout(feature)
                dwg.dimension(feature, "length", role="step")

        assert {dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("m_dia_y")}
        assert {
            dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("m_steplen")
        }
        assert dwg.view_of("centerline_side") == "side"
        assert dwg.view_of("centerline_plan") == "plan"

    def test_each_external_diameter_gets_a_callout(self, x_shaft_dwg):
        dwg = x_shaft_dwg
        labels = {o.label for n, o in dwg.iter_annotations() if n.startswith("m_dia")}
        assert "ø30" in labels
        assert "ø16" in labels

    def test_no_feature_not_dimensioned_left(self, x_shaft_dwg):
        # The whole point: the external diameters no longer lint as uncovered.
        dwg = x_shaft_dwg
        codes = dwg.lint_summary()["by_code"]
        assert codes.get("feature_not_dimensioned", 0) == 0

    def test_callouts_are_leaders_on_the_constraint_solver(self, x_shaft_dwg):
        # Placed via _solve_strip_ys (ADR 2 (was 0003) layer-2), so two distinct
        # diameters never share an x and never collide: label xs are min_gap
        # apart and inside the front view's page bounds.
        dwg = x_shaft_dwg
        leaders = [o for n, o in dwg.iter_annotations() if n.startswith("m_dia")]
        assert len(leaders) >= 2
        xs = sorted(ldr.elbow[0] for ldr in leaders)
        assert all(b - a > 1.0 for a, b in zip(xs, xs[1:]))  # spread, not stacked

    def test_z_rotational_part_is_untouched(self):
        # A plain Z disc's OD is covered by dim_od (rotational), so render_diameters
        # skips it (already mentioned) — no m_dia callouts appear.
        dwg = build_drawing(Cylinder(15, 40))  # plain Z disc/shaft
        assert not any(n.startswith("m_dia") for n in dwg.annotations())

    def test_horizontal_round_body_od_on_profile(self):
        # A horizontal (X-axis) single-OD cylinder shows its OD as a clean profile-view
        # diameter dim (dim_od) — not an end-on/corner boss leader — with the envelope
        # dims that duplicate the OD suppressed, so no double-dimensioning (#222).
        from build123d import Rot

        for rot, axis in ((Rot(0, 90, 0), "x"), (Rot(90, 0, 0), "y")):
            dwg = build_drawing(rot * Cylinder(25, 40), number="X")
            assert dwg._analysis.od_axis == axis
            assert "dim_od" in dwg.annotations(), f"{axis}: OD not on profile"
            assert not any(n.startswith("m_dia") for n in dwg.annotations()), (
                f"{axis}: end-on leader"
            )
            # the OD (50) appears once (ø50), not also as a bare envelope "50"
            labels = [str(o.label) for _, o in dwg.iter_annotations() if getattr(o, "label", None)]
            assert "50" not in labels, f"{axis}: OD double-dimensioned as envelope"
            assert [i for i in dwg.lint() if i.severity != "info"] == []

    def test_unfittable_row_recovers_diameters_without_crashing(self, monkeypatch):
        # A failed row must reach the shared leader solve without crashing on
        # a None unpack. Both physical diameter requirements remain covered.
        import sys

        # render_diameters looks the strip solvers up in its own module's namespace
        # (annotations.from_model) — patch them there.
        m = sys.modules["draftwright.annotations.from_model"]
        monkeypatch.setattr(m, "_solve_strip_ys", lambda *a, **k: None)
        monkeypatch.setattr(m, "_greedy_strip_ys", lambda *a, **k: None)
        dwg = build_drawing(_x_stepped_shaft())  # must not raise
        marks = [(name, item) for name, item in dwg.iter_annotations() if name.startswith("m_dia")]
        assert {item.label for _, item in marks} == {"ø30", "ø16"}
        assert all(dwg.registry.measurement_of(name) for name, _ in marks)
        assert not [issue for issue in dwg.lint() if issue.code == "feature_not_dimensioned"]

    def test_nested_band_under_silhouette_gets_a_callout(self):
        # #298: a narrow ø6 external band sits under the ø30 flange silhouette, so
        # recognise_turned_steps' local_od max() reads it as ø30 and it never becomes a step
        # diameter. detect.py now emits the missed band as a boss, so it still gets a ø
        # callout (matching the feature_diameters coverage inventory) and the part lints
        # clean. The overall part is large enough for all three callouts to fit the row.
        from build123d import Align

        def cyl(r, h, z):
            return Pos(0, 0, z) * Cylinder(r, h, align=(Align.CENTER, Align.CENTER, Align.MIN))

        part = Rotation(0, 90, 0) * (cyl(3, 0.5, 0.0) + cyl(15, 20, 0.5) + cyl(10, 15, 20.5))
        dwg = build_drawing(part)
        labels = {o.label for n, o in dwg.iter_annotations() if n.startswith("m_dia")}
        assert {"ø6", "ø30", "ø20"} <= labels  # the nested ø6 is now called out
        assert dwg.lint_summary()["by_code"].get("feature_not_dimensioned", 0) == 0

    def test_diameter_row_places_what_fits_not_all_or_nothing(self):
        # #298/#1505: a partial row retains the ODs that fit and sends the
        # remaining band to the shared leader solve. No measurement disappears
        # merely because the first presentation ran out of capacity.
        from build123d import Align

        def cyl(r, h, z):
            return Pos(0, 0, z) * Cylinder(r, h, align=(Align.CENTER, Align.CENTER, Align.MIN))

        # A ~4 mm shaft: ø6 tip, ø10 flange, ø8 body — three callouts won't fit the row.
        part = Rotation(0, 90, 0) * (cyl(3, 0.5, 0.0) + cyl(5, 1.7, 0.5) + cyl(4, 2.0, 2.2))
        dwg = build_drawing(part)
        labels = {o.label for n, o in dwg.iter_annotations() if n.startswith("m_dia")}
        assert {"ø10", "ø8", "ø6"} <= labels
        assert not [issue for issue in dwg.lint() if issue.code == "feature_not_dimensioned"]
        for feature in dwg.model().features:
            if feature.kind not in {"step", "boss"}:
                continue
            matches = [
                name
                for name, _ in dwg.iter_annotations()
                for identity in dwg.registry.measurement_of(name)
                if identity.feature is feature and identity.parameter.endswith(".diameter")
            ]
            assert len(matches) == 1, "each physical diameter has exactly one owning mark"

    def test_leader_tip_on_the_edge_centred_on_the_feature_length(self, x_shaft_dwg):
        # The ø leader lands on the step's silhouette EDGE — a full radius off the
        # turning axis, not on it (an arrow floating on the centre line reads
        # wrong) — and is CENTRED along the feature's length, not at a step
        # corner (a boss/free-end anchor would otherwise put it on an end face).
        dwg = x_shaft_dwg
        fb = dwg.view_bounds("front")
        axis_y = (fb[1] + fb[3]) / 2
        by_dia = {
            f.diameter: f
            for f in dwg.model().features
            if getattr(f, "diameter", None) and getattr(f, "frame", None) and f.frame.axis == "x"
        }
        tips = [
            (o, float(str(o.label)[1:]))
            for n, o in dwg.iter_annotations()
            if n.startswith("m_dia")
        ]
        assert len(tips) >= 2
        for ldr, dia in tips:
            # radial: on the bottom edge (one radius below the axis), NOT on it
            assert abs((axis_y - ldr.tip[1]) - dwg.scale * dia / 2) < 1e-6, (
                f"{ldr.label} off the edge"
            )
            # axial: at the mid-length of the feature(s) sharing this diameter
            ends = [e[0] for e in by_dia[dia].span]
            exp_x = dwg.at("front", (min(ends) + max(ends)) / 2, 0, 0)[0]
            assert abs(ldr.tip[0] - exp_x) < 1e-6, f"{ldr.label} not centred on its length"

    def test_boss_leader_keeps_its_frame_origin_for_script_parity(self):
        # Only STEP diameters are centred on their span. A boss is re-synthesised
        # WITHOUT a declared span in the emitted-Sheet path, so — unlike a step —
        # its ø leader must anchor at the frame origin (which round-trips) rather
        # than a span mid, or the direct and scripted builds diverge (#707).
        # (nested ø6 boss under the ø30 silhouette.)
        from build123d import Align

        def cyl(r, h, z):
            return Pos(0, 0, z) * Cylinder(r, h, align=(Align.CENTER, Align.CENTER, Align.MIN))

        dwg = build_drawing(
            Rotation(0, 90, 0) * (cyl(3, 0.5, 0.0) + cyl(15, 20, 0.5) + cyl(10, 15, 20.5))
        )
        boss = next(
            f
            for f in dwg.model().features
            if getattr(f, "diameter", None) == 6.0 and f.frame.axis == "x"
        )
        origin_x = dwg.at("front", boss.frame.origin[0], 0, 0)[0]
        tip_x = next(
            o.tip[0] for n, o in dwg.iter_annotations() if str(getattr(o, "label", "")) == "ø6"
        )
        assert abs(tip_x - origin_x) < 1e-6, "ø6 boss leader should anchor at its frame origin"

    def test_equal_diameter_leaders_keep_each_disjoint_runs_own_support(self):
        # The same diameter on two disjoint, unequal runs is independently
        # editable. Each arrow must land on its own band's midpoint, never
        # the convex-hull midpoint in the intervening larger-diameter band.
        part = (
            Cylinder(10, 5)
            + Pos(0, 0, 7.5) * Cylinder(15, 10)
            + Pos(0, 0, 22.5) * Cylinder(10, 20)
        )
        dwg = build_drawing(part, number="X")
        marks = [
            (name, item)
            for name, item in dwg.iter_annotations()
            if str(getattr(item, "label", "")) == "ø20"
        ]
        assert len(marks) == 2
        on_long = dwg.at("front", 0, 0, 22.5)[1]
        on_short = dwg.at("front", 0, 0, 0)[1]
        in_gap = dwg.at("front", 0, 0, 7.5)[1]
        assert sorted(item.tip[1] for _, item in marks) == pytest.approx(
            sorted((on_short, on_long))
        )
        for name, item in marks:
            owner = dwg.registry.feature_of(name)
            assert owner is not None
            assert item.tip[1] == pytest.approx(dwg.at("front", *owner.frame.origin)[1])
            assert abs(item.tip[1] - in_gap) > 1e-6

    def test_z_column_leader_lands_on_the_left_edge(self):
        # Cover the Z-turned column placer too (mirror of the X row): its tips sit
        # a radius to the LEFT of the axis, on the silhouette, not on the axis.
        dwg = build_drawing(Cylinder(15, 40) + Pos(0, 0, 35) * Cylinder(10, 30))
        fb = dwg.view_bounds("front")
        axis_x = (fb[0] + fb[2]) / 2
        tips = [
            (o, float(str(o.label)[1:]))
            for n, o in dwg.iter_annotations()
            if n.startswith("m_dia")
        ]
        assert tips, "expected a Z-column ø callout"
        for ldr, dia in tips:
            assert abs((axis_x - ldr.tip[0]) - dwg.scale * dia / 2) < 1e-6, (
                f"{ldr.label} off the left edge"
            )


class TestDiameterStepAnchor:
    """Unit tests for the shared-diameter leader anchor (#794 review)."""

    @staticmethod
    def _step(axis, origin, lo, hi):
        from draftwright.model.ir import Frame, StepFeature

        idx = {"x": 0, "y": 1, "z": 2}[axis]
        p_lo, p_hi = [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
        p_lo[idx], p_hi[idx] = lo, hi
        return StepFeature(
            frame=Frame(tuple(origin), axis),
            length=hi - lo,
            diameter=10.0,
            span=(tuple(p_lo), tuple(p_hi)),
        )

    def test_uses_the_longest_runs_own_origin_for_non_coaxial_steps(self):
        # Two X-axis ø10 steps on DIFFERENT axes: a short run centred at y=0 and a
        # longer run centred at y=20. The anchor must be the LONG step's own point
        # (axial mid 30, radial y=20), not a hybrid that takes the axial from the
        # long step and the radial from the bucket's first (short) step — which
        # would land the arrow off the selected step's silhouette.

        from draftwright.annotations.from_model import _diameter_step_anchor
        from draftwright.model.compiled import compile_dimensions
        from draftwright.model.ir import PartModel
        from draftwright.model.planner import plan_dimensions

        short = self._step("x", (0.0, 0.0, 0.0), -2.5, 2.5)  # len 5, centre x=0
        long = self._step("x", (30.0, 20.0, 0.0), 20.0, 40.0)  # len 20, centre x=30, y=20
        # A real BoundBox, not a namespace modelling whichever fields one caller happens to
        # read. `_compile_overall_height` now mints the height's identity from the bbox (#1230)
        # via `_envelope_from_bbox`, which calls `center()`. Two successive stub versions broke
        # here — the first lacked X and Y, the second added them but asserted an impossible
        # geometry (`size.Y=30` with `max.Y-min.Y=15`) and still had no `center()`. A stub of a
        # value type should be the value type (#1233 review).
        from build123d import Box, Location

        bbox = (Location((0, 7.5, 10)) * Box(80, 15, 20)).bounding_box()
        model = PartModel(bbox=bbox, orientation="x", features=[short, long])
        groups = plan_dimensions(model)
        plan = compile_dimensions(model, groups=groups)
        # `anchor` is the FIRST-bucketed feature's origin (the short step, y=0).
        got = _diameter_step_anchor(short.frame.origin, plan.of_kind("step"))
        assert got == (30.0, 20.0, 0.0), f"hybrid/wrong anchor: {got}"

    def test_no_step_in_the_group_falls_back_to_the_given_anchor(self):
        # A boss-only ⌀ (no step span to centre on) keeps the frame origin, which
        # round-trips through the emitted script (#707).
        from draftwright.annotations.from_model import _diameter_step_anchor

        assert _diameter_step_anchor((1.0, 2.0, 3.0), set()) == (1.0, 2.0, 3.0)


class TestLeaderCrossesSilhouette:
    """#796 Phase 1: an info-level lint notice when a ⌀ leader shaft cuts through
    the part body. Integration-level (real projected views); the discriminator
    itself is unit-tested in test_lint_structural."""

    @staticmethod
    def _cyl(r, h, z):
        from build123d import Align

        return Pos(0, 0, z) * Cylinder(r, h, align=(Align.CENTER, Align.CENTER, Align.MIN))

    def test_groove_leader_uses_the_clear_single_exit_candidate(self):
        # The shared #1166 inventory can choose the vertical candidate that exits
        # the thin neck once, instead of the old diagonal route through a flange.
        # A single silhouette exit is legitimate and must remain lint-clean.
        part = Rotation(0, 90, 0) * (
            self._cyl(15, 10, 0.0) + self._cyl(3, 2, 10) + self._cyl(15, 10, 12)
        )
        dwg = build_drawing(part, number="X")
        issues = [i for i in dwg.lint() if i.code == "leader_crosses_silhouette"]
        assert issues == []
        groove = next(
            annotation
            for name, annotation in dwg.iter_annotations()
            if name.startswith("m_groove")
        )
        assert groove.segments[0][0][0] == pytest.approx(groove.segments[0][1][0])

    def test_nested_boss_diameter_routes_to_the_clear_side(self):
        # #798: the ø6 boss stub whose row-solved leader would cut through the ø30
        # flange is re-routed to the clear margin instead — no crossing survives, and
        # the ø6 is still called out on the main view as a normal m_dia leader whose
        # elbow now sits past the part's left edge (the clear side), not in the body.
        part = Rotation(0, 90, 0) * (
            self._cyl(3, 0.5, 0.0) + self._cyl(15, 20, 0.5) + self._cyl(10, 15, 20.5)
        )
        dwg = build_drawing(part)
        assert dwg.lint_summary()["by_code"].get("leader_crosses_silhouette", 0) == 0
        ldr = next(
            o
            for n, o in dwg.iter_annotations()
            if n.startswith("m_dia") and str(getattr(o, "label", "")) == "ø6"
        )
        fb = dwg.view_bounds("front")
        assert ldr.elbow[0] <= fb[0], "ø6 leader should route to the clear left margin"

    def test_grm03_end_boss_routes_off_the_body(self):
        # The GRM-03 ø6 end boss: its row-solved leader diagonals back INTO the disc
        # (the near-miss clip). #798 pulls an end-boss leader out to its clear margin,
        # so its elbow ends up LEFT of the tip, not diagonally right into the body.
        fixture = Path(__file__).parent / "fixtures" / "grm03_thumbwheel_drive_screw.step"
        dwg = build_drawing(step_file=str(fixture))
        ldr = next(
            o
            for n, o in dwg.iter_annotations()
            if n.startswith("m_dia") and str(getattr(o, "label", "")) == "ø6"
        )
        assert ldr.elbow[0] < ldr.tip[0], "ø6 end-boss leader should route left, off the body"

    def test_z_turned_end_boss_routes_to_the_axial_margin(self):
        # #801 review: an m_dia_z (Z-turned) leader's axial run is page-Y, so a
        # Z-turned end boss must route to the TOP/BOTTOM margin, not left/right. A ø6
        # boss stub on top of a ø30 disc routes straight up to the top margin (same X
        # as the tip) — the X-only trigger would move it sideways instead.
        from build123d import Align

        b = Align.MIN
        part = Cylinder(15, 20, align=(Align.CENTER, Align.CENTER, b)) + Pos(0, 0, 20) * Cylinder(
            3, 0.5, align=(Align.CENTER, Align.CENTER, b)
        )
        dwg = build_drawing(part)
        assert dwg.lint_summary()["by_code"].get("leader_crosses_silhouette", 0) == 0
        ldr = next(
            o
            for n, o in dwg.iter_annotations()
            if n.startswith("m_dia_z") and str(getattr(o, "label", "")) == "ø6"
        )
        fb = dwg.view_bounds("front")
        assert ldr.elbow[1] >= fb[3], "ø6 Z-turned boss should route to the top margin"
        assert abs(ldr.elbow[0] - ldr.tip[0]) < 1e-6, "routed along the wrong (radial) axis"

    def _crossing_boss_drawing(self):
        # A built nested-boss drawing whose ø6 leader has been forced back to a
        # crossing diagonal (build_drawing already re-routes it, so put it back).
        from build123d_drafting import Leader

        part = Rotation(0, 90, 0) * (
            self._cyl(3, 0.5, 0.0) + self._cyl(15, 20, 0.5) + self._cyl(10, 15, 20.5)
        )
        dwg = build_drawing(part)
        tip = dwg.get_annotation("m_dia_x0").tip
        dwg.remove("m_dia_x0")
        crossing = (tip[0] + 3.0, tip[1] - 15.0, 0.0)  # diagonal down into the flange body
        dwg._add(
            Leader(tip=(tip[0], tip[1], 0), elbow=crossing, label="ø6", draft=dwg.draft),
            "m_dia_x0",
            view="front",
        )
        return dwg, crossing

    def test_reroute_skips_a_pinned_leader(self):
        # A pin is the user's "this stays put" (ADR 2 (was 0012)) — the re-router must never
        # move a pinned ø leader, even one that crosses.
        from draftwright.annotations.from_model import _reroute_crossing_diameters

        dwg, crossing = self._crossing_boss_drawing()
        dwg.pin("m_dia_x0")
        _reroute_crossing_diameters(dwg, ctx=_ctx_for(dwg))
        moved = dwg.get_annotation("m_dia_x0").elbow
        assert (moved[0], moved[1]) == crossing[:2], "pinned leader moved"

    def test_reroute_restores_the_leader_when_no_candidate_is_clear(self, monkeypatch):
        # If no candidate is clear+safe, the original leader is RESTORED (Phase-1 then
        # flags it) — a re-route must never lose the dimension.
        from draftwright.annotations.from_model import _reroute_crossing_diameters

        dwg, crossing = self._crossing_boss_drawing()
        # Force every route to read as cutting, so no candidate is ever accepted. The
        # re-router now measures against the shared material field (#798), so this
        # patches that predicate rather than the retired outline-crossing one.
        monkeypatch.setattr(
            "draftwright.annotations.from_model.material_penalty_units", lambda *a, **k: 1
        )
        _reroute_crossing_diameters(dwg, ctx=_ctx_for(dwg))
        restored = dwg.get_annotation("m_dia_x0")
        assert restored is not None, "leader lost by the re-route"
        assert (restored.elbow[0], restored.elbow[1]) == crossing[:2], "leader not restored"

    def test_plain_stepped_shaft_is_not_flagged(self):
        # A clean turned shaft: every ⌀ leader runs outward to the row, none cut
        # through the body.
        dwg = build_drawing(
            Rotation(0, 90, 0) * (Cylinder(15, 40) + Pos(0, 0, 35) * Cylinder(8, 30))
        )
        assert not any(i.code == "leader_crosses_silhouette" for i in dwg.lint())

    def test_hole_callout_exiting_the_part_is_not_flagged(self):
        # GRM-03: the side-view hole callout legitimately exits the disc from an
        # internal hole (covers_diameters), and the ⌀ leaders don't cut the body.
        fixture = Path(__file__).parent / "fixtures" / "grm03_thumbwheel_drive_screw.step"
        dwg = build_drawing(step_file=str(fixture))
        assert not any(i.code == "leader_crosses_silhouette" for i in dwg.lint())


class TestTurnedLengths:
    """Axial step-length chain for X-axis turned parts (the drive-screw gap:
    every diameter dimensioned, no shoulder locatable)."""

    def test_each_step_length_is_dimensioned(self, x_shaft_dwg):
        dwg = x_shaft_dwg  # ø30 l40 then ø16 l30
        labels = {o.label for n, o in dwg.iter_annotations() if n.startswith("m_steplen")}
        assert labels == {"40", "30"}

    @pytest.mark.parametrize(
        "shaft",
        [
            pytest.param(_x_stepped_shaft(), id="x-turned"),
            pytest.param(Cylinder(15, 30) + Pos(0, 0, 30) * Cylinder(8, 30), id="z-turned"),
        ],
    )
    def test_each_step_length_records_its_measurement_identity(self, shaft):
        dwg = build_drawing(shaft)
        names = [name for name in dwg.annotations() if name.startswith("m_steplen")]
        assert names
        for name in names:
            keys = dwg.measurement_keys(name)
            assert len(keys) == 1, f"{name} must identify exactly the step length it draws"
            assert keys[0]["feature"].startswith("step@")
            assert keys[0]["parameter_id"] == "step.length"
        recorded = {mid for name in names for mid in dwg.registry.measurement_of(name)}
        assert recorded == _compiled_step_length_ids(dwg)

    def test_overall_width_suppressed_for_turned_part(self, x_shaft_dwg):
        # The complete chain conveys the overall length, so the envelope width dim
        # is dropped — no double dimensioning (ISO 129).
        dwg = x_shaft_dwg
        assert "m_env_width" not in dwg.annotations()

    def test_turned_part_lints_clean(self, x_shaft_dwg):
        dwg = x_shaft_dwg
        codes = dwg.lint_summary()["by_code"]
        assert codes.get("axial_length_missing", 0) == 0
        assert codes.get("annotation_overlap", 0) == 0

    def test_three_step_shaft_dimensions_all_steps(self):
        # Non-uniform step lengths (10/8/12), base-stacked so they sit flush → each
        # segment dimensioned individually (the uniform-run collapse, #230, is
        # exercised separately below).
        from build123d import Align, Cylinder, Pos, Rotation

        b = Align.MIN
        stack = Cylinder(10, 10, align=(Align.CENTER, Align.CENTER, b))
        stack += Pos(0, 0, 10) * Cylinder(7, 8, align=(Align.CENTER, Align.CENTER, b))
        stack += Pos(0, 0, 18) * Cylinder(4, 12, align=(Align.CENTER, Align.CENTER, b))
        dwg = build_drawing(Rotation(0, 90, 0) * stack)
        assert len([n for n in dwg.annotations() if n.startswith("m_steplen")]) == 3

    def test_uniform_staircase_collapses_to_n_times(self):
        # A uniform run (4 equal-length steps) collapses to one "N× length" dim
        # instead of four identical segment dims (#230) — and the collapsed dim must
        # still satisfy axial coverage (lint clean, every shoulder located).
        from build123d import Align, Cylinder, Pos

        b = Align.MIN
        shaft = Cylinder(30, 10, align=(Align.CENTER, Align.CENTER, b))
        for i, r in enumerate([25, 20, 15], start=1):
            shaft += Pos(0, 0, 10 * i) * Cylinder(r, 10, align=(Align.CENTER, Align.CENTER, b))
        dwg = build_drawing(shaft)
        steplen = {n: o.label for n, o in dwg.iter_annotations() if n.startswith("m_steplen")}
        assert steplen == {"m_steplen_typ": "4× 10"}, steplen
        assert "axial_length_missing" not in {i.code for i in dwg.lint()}

        keys = dwg.measurement_keys("m_steplen_typ")
        assert len(keys) == 4, "the collapsed annotation draws all four step lengths"
        assert len({key["feature"] for key in keys}) == 4
        assert {key["parameter_id"] for key in keys} == {"step.length"}
        assert set(dwg.registry.measurement_of("m_steplen_typ")) == _compiled_step_length_ids(dwg)

    def test_prismatic_part_has_no_step_lengths(self):
        dwg = build_drawing(Box(80, 60, 20))
        assert not any(n.startswith("m_steplen") for n in dwg.annotations())

    def test_grooved_shaft_step_chain_not_flagged_axial_missing(self):
        # A groove band is excluded from the step-length chain (#606) and dimensioned by its
        # WIDTH callout instead. The axial-coverage lint counts prof.steps (which still includes
        # the groove band), so it must credit the rendered groove-width callout as covering that
        # band — else an otherwise fully-dimensioned grooved shaft false-fires axial_length_missing
        # (#628, a regression from the #606 groove exclusion).
        from build123d import Cylinder, Pos

        shaft = (
            Pos(0, 0, 7.5) * Cylinder(30, 15)
            + Pos(0, 0, 32) * Cylinder(20, 34)
            + Pos(0, 0, 53) * Cylinder(13, 8)  # ø26 local-minimum band → recognised as a groove
            + Pos(0, 0, 74) * Cylinder(20, 34)
            + Pos(0, 0, 107) * Cylinder(14, 32)
        ) - Pos(0, 0, 61.5) * Cylinder(8, 123)
        dwg = build_drawing(shaft, number="X")
        assert any(n.startswith("m_groove") for n in dwg.annotations())  # the ø26 band IS a groove
        assert dwg.lint_summary()["by_code"].get("axial_length_missing", 0) == 0

    def test_chain_skips_gracefully_when_no_room(self):
        # Forced onto a too-small page, the chain must SKIP rather than run off the
        # page edge (the parity guard the diameter row has). Lint then reports the
        # gap instead of the engine emitting off-page dims.
        from build123d import Cylinder, Pos, Rotation

        z = 0.0
        part = None
        for i in range(10):
            seg = Pos(0, 0, z + 1.0) * Cylinder((12 - 0.6 * i) / 2, 2.0)
            part = seg if part is None else part + seg
            z += 2.0
        dwg = build_drawing(
            Rotation(0, 90, 0) * part,
            page="90x70",
            scale=4.0,
            scale_policy="permissive",
        )
        assert not any(
            n.startswith("m_steplen") for n in dwg.annotations()
        )  # skipped, not off-page
        assert dwg.lint_summary()["by_code"].get("axial_length_missing", 0) >= 1

    def test_dense_chain_skips_instead_of_cramming(self):
        # A genuinely dense turned shaft (many fine non-uniform steps) whose labels
        # cannot be spaced legibly must SKIP the chain, not overprint a wall of
        # overlapping dims (#293). Any placed step-length dims must not overlap.
        from build123d import Align, Cylinder, Pos, Rotation

        from draftwright.annotations._common import _anno_box

        b = Align.MIN
        shaft = None
        z = 0.0
        for i in range(16):
            d = 20 if i % 2 == 0 else 16  # alternating ø → truly stepped, fine pitch
            ln = 3.0 + (i % 3) * 0.4  # non-uniform (no N× collapse)
            seg = Pos(0, 0, z) * Cylinder(d / 2, ln, align=(Align.CENTER, Align.CENTER, b))
            shaft = seg if shaft is None else shaft + seg
            z += ln
        dwg = build_drawing(Rotation(0, 90, 0) * shaft)
        boxes = [_anno_box(o) for n, o in dwg.iter_annotations() if n.startswith("m_steplen")]

        def overlap(a, c):
            return a and c and not (a[2] <= c[0] or a[0] >= c[2] or a[3] <= c[1] or a[1] >= c[3])

        assert not any(
            overlap(boxes[i], boxes[j])
            for i in range(len(boxes))
            for j in range(i + 1, len(boxes))
        ), "step-length dims overprint — chain crammed instead of skipping"

    def test_crowded_chain_staggers_into_two_tiers_at_current_scale(self):
        # A *moderately* crowded chain — steps just ABOVE the arrowhead floor (so no
        # detail view is triggered), but with labels that would collide on one tier.
        # Rather than cram, the chain staggers successive dims between a near and a far
        # tier (ISO 129-1) so every step length stays legible at the drawing's own
        # scale (#293). Scale pinned so the crowding regime is deterministic.
        from build123d import Align, Cylinder, Pos, Rotation

        b = Align.MIN
        specs = [(8, 3.1), (12, 2.9), (8, 3.2), (12, 2.8), (6, 3.0)]  # ~3 mm, > floor
        shaft = None
        z = 0.0
        for d, ln in specs:
            seg = Pos(0, 0, z) * Cylinder(d / 2, ln, align=(Align.CENTER, Align.CENTER, b))
            shaft = seg if shaft is None else shaft + seg
            z += ln
        dwg = build_drawing(Rotation(0, 90, 0) * shaft, scale=2.0)
        assert "detail_a" not in dwg.views  # above floor → no detail, staggered in place
        steps = {n: o for n, o in dwg.iter_annotations() if n.startswith("m_steplen")}
        assert len(steps) == 5  # every segment dimensioned, none dropped
        assert dwg.lint_summary()["by_code"].get("axial_length_missing", 0) == 0
        # Two tiers: the dims sit at (at least) two distinct offset rows.
        rows = {round(o._dw_spec.distance, 6) for o in steps.values()}
        assert len(rows) >= 2, "chain did not stagger into multiple tiers"

        # Labels don't overprint each other.
        boxes = [o.label_bbox for o in steps.values()]

        def overlap(a, c):
            return a and c and not (a[2] <= c[0] or a[0] >= c[2] or a[3] <= c[1] or a[1] >= c[3])

        assert not any(
            overlap(boxes[i], boxes[j])
            for i in range(len(boxes))
            for j in range(i + 1, len(boxes))
        ), "staggered step-length labels overprint"

    def test_subfloor_head_gets_detail_view(self):
        # A fine head (sub-floor steps) + a long shaft (the GRM-03 pattern). The head
        # can't be dimensioned legibly in line, so the unified detail pipeline (#307)
        # locates it as one block on the main view + breaks it down in DETAIL A, with
        # axial coverage satisfied across the two views (no double-dimensioning).
        from build123d import Align, Cylinder, Pos, Rotation

        b = Align.MIN
        specs = [(4, 1.5), (6, 2.0), (4, 2.5), (3, 25.0)]  # non-uniform sub-floor head
        shaft = None
        z = 0.0
        for d, ln in specs:
            seg = Pos(0, 0, z) * Cylinder(d / 2, ln, align=(Align.CENTER, Align.CENTER, b))
            shaft = seg if shaft is None else shaft + seg
            z += ln
        dwg = build_drawing(Rotation(0, 90, 0) * shaft, scale=2.0)
        assert "detail_a" in dwg.views  # crowded head → enlarged detail
        main = {n: o for n, o in dwg.iter_annotations() if n.startswith("m_steplen")}
        assert "25" in {o.label for o in main.values()}
        block = next(name for name, obj in main.items() if obj.label != "25")
        assert dwg.measurement_keys(block) == [], "the synthetic head extent is not one step"
        detail = [n for n in dwg.annotations() if n.startswith("dim_detail_a_steplen")]
        assert len(detail) >= 3
        assert all(len(dwg.measurement_keys(name)) == 1 for name in detail)
        assert dwg.lint_summary()["by_code"].get("axial_length_missing", 0) == 0

    def test_two_sub_floor_runs_get_separate_non_colliding_details(self):
        # Two separated fine-step clusters → two detail views (A, B). Their dims use
        # view-scoped names, so detail B's dims don't evict detail A's (the #307-review
        # name-collision regression) and axial coverage holds across all views.
        from build123d import Align, Cylinder, Pos, Rotation

        b = Align.MIN
        specs = [(4, 1.5), (6, 2.0), (4, 2.5), (3, 22), (6, 1.5), (4, 2.0), (5, 2.5), (2, 22)]
        shaft = None
        z = 0.0
        for d, ln in specs:
            seg = Pos(0, 0, z) * Cylinder(d / 2, ln, align=(Align.CENTER, Align.CENTER, b))
            shaft = seg if shaft is None else shaft + seg
            z += ln
        dwg = build_drawing(Rotation(0, 90, 0) * shaft, page="A2", scale=2.0)
        assert {"detail_a", "detail_b"} <= set(dwg.views)
        names = [n for n in dwg.annotations() if "steplen" in n and "detail" in n]
        assert len(names) == len(set(names))  # no eviction — all detail dims survive
        assert any(n.startswith("dim_detail_a_") for n in names)
        assert any(n.startswith("dim_detail_b_") for n in names)
        assert dwg.lint_summary()["by_code"].get("axial_length_missing", 0) == 0

    def test_head_block_does_not_collapse_main_chain_to_n_times(self):
        # When the head-block extent happens to match the legible step lengths, the main
        # chain (block + steps) must NOT collapse to a uniform "N× v" — the block is a
        # compound region, not a repeated step, and "N× v" would be a false claim of N
        # equal steps (#307 review).
        from build123d import Align, Cylinder, Pos, Rotation

        b = Align.MIN
        # head 1.5/2.0/2.5 (sub-floor, sums to 6) + two legible 6 mm steps
        specs = [(4, 1.5), (6, 2.0), (4, 2.5), (7, 6.0), (5, 6.0)]
        shaft = None
        z = 0.0
        for d, ln in specs:
            seg = Pos(0, 0, z) * Cylinder(d / 2, ln, align=(Align.CENTER, Align.CENTER, b))
            shaft = seg if shaft is None else shaft + seg
            z += ln
        dwg = build_drawing(Rotation(0, 90, 0) * shaft, scale=2.0)
        main = {o.label for n, o in dwg.iter_annotations() if n.startswith("m_steplen")}
        assert not any("×" in v for v in main)  # no false uniform-staircase collapse
        assert dwg.lint_summary()["by_code"].get("axial_length_missing", 0) == 0

    def test_disjoint_coaxial_bodies_do_not_form_a_non_contiguous_turned_profile(self):
        # Recognisers 0.4.9 makes turned-profile membership body-local. Two coaxial discs with
        # an axial air gap are therefore two single-diameter bosses, not one invented stepped
        # shaft. The old cross-body profile exercised #797's shoulder lookup; the truthful
        # body-local projection has no axial step chain and must still lint without crashing.
        from build123d import Align

        b = Align.MIN
        part = Rotation(0, 90, 0) * (
            Cylinder(15, 10, align=(Align.CENTER, Align.CENTER, b))
            + Pos(0, 0, 20) * Cylinder(10, 10, align=(Align.CENTER, Align.CENTER, b))
        )
        dwg = build_drawing(part, number="X")
        codes = dwg.lint_summary()["by_code"]  # must not raise KeyError
        assert not [n for n in dwg.annotations() if n.startswith("m_steplen")]
        assert {feature.kind for feature in dwg.model().features} == {"boss"}
        assert codes.get("axial_length_missing", 0) == 0


class TestStepLadderRecognition:
    """ADR 1 (was 0008) step 1: the Z step-height ladder draws its step levels from the
    unified turned-step model, which filters by the OD silhouette."""

    def test_blind_bore_floor_is_not_a_phantom_shoulder(self):
        from build123d import Cylinder, Pos

        # Two OD steps (one real interior shoulder at z=15) + a blind axial bore
        # whose flat floor sits at z=30. The floor must NOT be dimensioned as a
        # step height — that was the area-filter phantom the model removes.
        shaft = Cylinder(15, 30) + Pos(0, 0, 30) * Cylinder(8, 30)
        part = shaft - Pos(0, 0, 45) * Cylinder(5, 30)
        dwg = build_drawing(part, number="D-1")
        # The turned part is now dimensioned by the unified IR step-length chain
        # (#223): two real OD segments (each length 30), and crucially NO '45'
        # bore-floor phantom — recognise_turned_steps excludes the internal bore.
        labels = [o.label for n, o in dwg.iter_annotations() if n.startswith("m_steplen")]
        assert labels == ["30", "30"]  # both real segments
        assert "45" not in labels  # no bore-floor phantom

    def test_plain_z_stepped_shaft_dimensioned_by_ir_chain(self):
        from build123d import Cylinder, Pos

        # A Z-turned stepped shaft is now located by the unified IR step-length
        # chain (#223), not the old engine ladder. Both segments are dimensioned.
        dwg = build_drawing(Cylinder(15, 30) + Pos(0, 0, 30) * Cylinder(8, 30), number="D-1")
        labels = [o.label for n, o in dwg.iter_annotations() if n.startswith("m_steplen")]
        assert labels == ["30", "30"]
        assert not any(
            n.startswith("dim_step") for n in dwg.annotations()
        )  # ladder retired for turned


class TestAxialCoverageLint:
    """lint_axial_coverage — the scoring signal for undimensioned turned steps,
    now counted from the drawing (not the CoverageState side channel, #219)."""

    def test_flags_uncovered_turned_part(self):
        from draftwright.linting import lint_axial_coverage

        # A bare scaffold (views, no step-length dims) → all steps uncovered.
        part = _x_stepped_shaft()
        dwg = build_drawing(part, number="D-1", auto_dims=False)
        issues = lint_axial_coverage(part, dwg)
        assert [i.code for i in issues] == ["axial_length_missing"]
        assert issues[0].severity == "warning"

    def test_clean_when_all_steps_covered(self):
        from draftwright.linting import lint_axial_coverage

        # The engine places the full step-length chain → drawing-derived coverage
        # finds every step located.
        part = _x_stepped_shaft()
        dwg = build_drawing(part, number="D-1")
        assert lint_axial_coverage(part, dwg) == []

    def test_silent_for_non_turned_part(self):
        from draftwright.linting import lint_axial_coverage

        part = Box(80, 60, 20)
        dwg = build_drawing(part, number="D-1", auto_dims=False)
        assert lint_axial_coverage(part, dwg) == []

    def test_z_turned_chain_is_covered(self):
        # A Z-turned shaft is now located by the vertical IR chain (#223), so axial
        # coverage must recognise it (no false positive on a correctly chained Z part).
        from build123d import Cylinder, Pos

        from draftwright.linting import lint_axial_coverage

        part = Cylinder(15, 30) + Pos(0, 0, 30) * Cylinder(8, 30)
        dwg = build_drawing(part, number="D-1")
        assert lint_axial_coverage(part, dwg) == []

    def test_z_turned_flags_when_uncovered(self):
        # The X-only restriction is gone (#223): a Z-turned shaft with no chain
        # (bare scaffold) is flagged, not silently under-dimensioned.
        from build123d import Cylinder, Pos

        from draftwright.linting import lint_axial_coverage

        part = Cylinder(15, 30) + Pos(0, 0, 30) * Cylinder(8, 30)
        dwg = build_drawing(part, number="D-1", auto_dims=False)
        assert [i.code for i in lint_axial_coverage(part, dwg)] == ["axial_length_missing"]

    def test_coverage_survives_repair_and_is_idempotent(self):
        # Drawing-derived coverage must stay clean after the repair loop re-places
        # dims (witnesses stay anchored to geometry) and across repeated lint()s.
        part = _x_stepped_shaft()
        dwg = build_drawing(part, number="D-1")  # repair on
        first = [i.code for i in dwg.lint() if i.code == "axial_length_missing"]
        again = [i.code for i in dwg.lint() if i.code == "axial_length_missing"]
        assert first == [] and again == []

    def test_axial_length_missing_is_geometry_aware(self):
        # It is a completeness/standards code, so lint_summary must count it under
        # geometry_issues, not as layout (#226 review follow-through).
        from draftwright.drawing import _GEOMETRY_AWARE_CODES

        assert "axial_length_missing" in _GEOMETRY_AWARE_CODES


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
        from build123d import Box

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


class TestSheetFrame:
    """#767: an opt-in drawn sheet border (Option B) — the border is the content boundary,
    so turning it on RESERVES clearance that flows through scale/page selection (ADR 2 (was 0004)),
    not a rectangle drawn over content. Default off ⇒ byte-identical (guarded elsewhere)."""

    def test_frame_off_by_default(self):
        dwg = build_drawing(Box(80, 60, 20))
        assert "sheet_frame" not in dwg.annotations()
        assert dwg._analysis.margin == _MARGIN  # no reservation

    def test_frame_drawn_at_the_margin_and_content_clears_it(self):
        dwg = build_drawing(Box(80, 60, 20), frame=True)
        a = dwg._analysis
        fr = dwg.get_annotation("sheet_frame")
        assert fr is not None and getattr(fr, "is_sheet_frame", False)
        # the border is at the _MARGIN inset, strictly within the page
        b = fr.bounding_box()
        assert abs(b.min.X - _MARGIN) < 0.5 and abs(b.min.Y - _MARGIN) < 0.5
        assert (
            abs(b.max.X - (a.PAGE_W - _MARGIN)) < 0.5 and abs(b.max.Y - (a.PAGE_H - _MARGIN)) < 0.5
        )
        # content reserved a band inside the border: a.margin is raised, and every view +
        # annotation clears the inner rectangle (not merely the page).
        assert a.margin > _MARGIN
        inner = (a.margin, a.margin, a.PAGE_W - a.margin, a.PAGE_H - a.margin)
        for n, o in dwg.iter_annotations():
            if n in ("sheet_frame", "title_block"):
                continue
            bb = o.bounding_box()
            assert bb.min.X >= inner[0] - 0.5 and bb.min.Y >= inner[1] - 0.5
            assert bb.max.X <= inner[2] + 0.5 and bb.max.Y <= inner[3] + 0.5

    def test_reservation_flows_through_scale_selection(self):
        # The border consumes layout budget BEFORE choose_scale, so the framed scale is never
        # larger than the unframed one (monotone reservation), and the margin proves it is active.
        part = Box(180, 130, 40)
        a0_scale = build_drawing(part).scale
        a1 = build_drawing(part, frame=True)._analysis
        assert a1.margin == _MARGIN + 6.0  # _FRAME_BAND reserved
        assert a1.SCALE <= a0_scale + 1e-9

    def test_frame_build_is_lint_clean(self):
        dwg = build_drawing(Box(80, 60, 20), frame=True)
        by_code = dwg.lint_summary()["by_code"]
        # the page-spanning border must not trip overlap / bounds lint
        assert by_code.get("annotation_overlap", 0) == 0
        assert by_code.get("annotation_out_of_bounds", 0) == 0
        assert by_code.get("view_annotation_overlap", 0) == 0


class TestProjectionSymbol:
    """#769: the ISO 5456-2 projection-method glyph (third/first-angle) in the reserved
    title-block band, from the helpers 0.14.1 ProjectionSymbol primitive."""

    def test_third_angle_symbol_by_default(self):
        dwg = build_drawing(Box(80, 60, 20))
        assert dwg.get_annotation("projection_symbol").method == "third"
        assert dwg._analysis.projection is None

    def test_third_renders_in_the_title_block_band(self):
        from draftwright._core import _TB_CLEAR, _TB_H

        dwg = build_drawing(Box(80, 60, 20), projection="third")
        ps = dwg.get_annotation("projection_symbol")
        assert ps is not None and getattr(ps, "is_projection_symbol", False)
        b = ps.bounding_box()
        a = dwg._analysis
        # within the page, and in the reserved title-block column/band (above the block)
        assert b.min.X >= _MARGIN and b.max.X <= a.PAGE_W - _MARGIN
        assert b.min.Y <= _TB_CLEAR + _TB_H and b.max.Y <= _TB_CLEAR + _TB_H
        assert b.min.X >= a.PAGE_W - a.TB_W - _TB_CLEAR  # the title-block column

    def test_projection_build_is_lint_clean(self):
        dwg = build_drawing(Box(80, 60, 20), projection="third")
        by_code = dwg.lint_summary()["by_code"]
        assert by_code.get("annotation_overlap", 0) == 0
        assert by_code.get("annotation_out_of_bounds", 0) == 0
        assert by_code.get("view_annotation_overlap", 0) == 0


class TestZoneGrid:
    """#768: the ISO 5457 zone-grid border ruler — numbers along top/bottom, letters (skip
    I/O) down the sides, in the band between the frame and the page edge. Implies a frame."""

    def test_off_by_default(self):
        dwg = build_drawing(Box(80, 60, 20))
        assert "zone_grid" not in dwg.annotations()
        assert not any(n.startswith("zone_") for n in dwg.annotations())
        assert dwg._analysis.zones is False

    def test_zones_imply_frame_and_have_iso_counts(self):
        from draftwright._core import _zone_divisions

        dwg = build_drawing(Box(80, 60, 20), zones=True)
        a = dwg._analysis
        assert a.zones and a.frame  # zones imply the frame the ticks sit on
        assert "zone_grid" in dwg.annotations()
        cols, rows = _zone_divisions(a.PAGE_W, a.PAGE_H)
        nums = [n for n in dwg.annotations() if n.startswith("zone_num_")]
        ltrs = [n for n in dwg.annotations() if n.startswith("zone_ltr_")]
        assert len(nums) == cols * 2 and len(ltrs) == rows * 2  # both edges

    def test_labels_sit_in_the_border_band(self):
        dwg = build_drawing(Box(80, 60, 20), zones=True)
        # a bottom number is below the frame (in the [0, _MARGIN] band); a right letter is
        # right of the frame (in the [PAGE_W - _MARGIN, PAGE_W] band).
        nb = dwg.get_annotation("zone_num_b_0").bounding_box()
        assert 0 <= (nb.min.Y + nb.max.Y) / 2 <= _MARGIN
        lr = dwg.get_annotation("zone_ltr_r_0").bounding_box()
        assert dwg.page_w - _MARGIN <= (lr.min.X + lr.max.X) / 2 <= dwg.page_w

    def test_letters_skip_i_and_o(self):
        # A1 has 12 rows → A..H then J,K,L,M (I skipped). Force the page so the count is stable.
        dwg = build_drawing(Box(700, 500, 40), page="A1", zones=True)
        letters = {
            dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("zone_ltr_l_")
        }
        assert "I" not in letters and "O" not in letters
        assert {"H", "J"} <= letters  # J follows H (I skipped)

    def test_zone_build_is_lint_clean(self):
        dwg = build_drawing(Box(80, 60, 20), zones=True)
        by_code = dwg.lint_summary()["by_code"]
        assert by_code.get("annotation_overlap", 0) == 0
        assert by_code.get("annotation_out_of_bounds", 0) == 0
        assert by_code.get("view_annotation_overlap", 0) == 0

    def test_custom_page_divisions_are_safe(self):
        # Codex review: match a standard on BOTH dims (a same-width custom page must not borrow
        # the A-series count), and clamp rows to the available letters so a tall page can't
        # index past _ZONE_LETTERS.
        from draftwright._core import _ZONE_DIVISIONS, _ZONE_LETTERS, _zone_divisions

        assert _zone_divisions(420, 297) == _ZONE_DIVISIONS[(420, 297)]  # A3 unchanged
        assert _zone_divisions(297, 100) != _ZONE_DIVISIONS[(297, 210)]  # not the A4 count
        _cols, rows = _zone_divisions(500, 3000)  # absurdly tall
        assert rows <= len(_ZONE_LETTERS)




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
