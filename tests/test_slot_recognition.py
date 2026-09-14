"""Slot recognition and automatic slot dimensioning."""

from pathlib import Path

import pytest
from build123d import Align, Box, Cylinder, Pos, Rotation
from quiddity import (
    Slot,
    build_raw_recognition_result,
    recognise_slots,
)

from draftwright import build_drawing


def _recognised_pocket_fields(part):
    from draftwright.section_recess_contract import recesses_with_kind, section_recess_fields

    inventory = build_raw_recognition_result(part).section_recesses
    return [section_recess_fields(source)[1] for source in recesses_with_kind(inventory, "pocket")]


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
