"""The "ISO VIEW (NTS)" caption must not land on top of existing annotations.

It was placed at a fixed offset below the iso bbox with **no collision check of any
kind**, and `_fit_iso_view` runs after `_auto_annotate` — so every dimension and
callout was already committed when the caption dropped. Neither could see the other:
the annotations avoided a caption that did not exist yet, and the caption never looked
at them. On a sparse sheet nothing collides and the fault is invisible; where callouts
reach under the iso, the caption lands on one.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from build123d import Box

from draftwright import build_drawing
from draftwright._core import _anno_box
from draftwright._geometry import _boxes_overlap
from draftwright.annotations._common import place_iso_nts_note


def _bbox(box):
    x0, y0, x1, y1 = box
    return SimpleNamespace(
        min=SimpleNamespace(X=x0, Y=y0, Z=0.0), max=SimpleNamespace(X=x1, Y=y1, Z=0.0)
    )


class _Blocker:
    """A placed annotation occupying *box*. No `.segments`, so the shared occupancy
    walk falls back to its rendered hull — which is what an ordinary label does."""

    label_bbox = None

    def __init__(self, box):
        self._box = box

    def bounding_box(self):
        return _bbox(self._box)


class _Rider(_Blocker):
    """A page-spanning rider — the sheet frame and zone ruler are these.

    It carries the real `is_sheet_frame` marker, because that flag is exactly how
    `late_furniture_obstacles` tells a rider from bounded unlabelled furniture like the
    title block. A stand-in without it is not a sheet frame; it is an annotation
    genuinely covering the page, and treating it as an obstacle would be right.
    `test_the_real_sheet_frame_carries_the_marker` keeps this faithful.
    """

    is_sheet_frame = True


class _Shaft:
    """A label parked out of the way whose drawn STROKE crosses the sheet — a leader.

    This is the 'invisible occupant': its label box says one place, its ink another.
    """

    def __init__(self, segment):
        self.label_bbox = (0.0, 0.0, 0.1, 0.1)
        self.segments = [segment]

    def bounding_box(self):
        return _bbox((0.0, 0.0, 0.1, 0.1))


class _StandIn:
    """A duck-typed drawing exposing what `late_furniture_obstacles` reads.

    Richer than the placer alone needs, deliberately: the caption now places against the
    same shared occupancy the tables do, so the harness has to present a drawing-shaped
    surface. Views live in `views`/`view_bounds`, NOT in `items`.
    """

    def __init__(self, draft, blockers, view_boxes, page_w, page_h):
        from draftwright.registry import AnnotationRegistry

        # A FRESH registry: the placer replaces any prior `note_iso_nts`, and reusing a
        # built drawing's would make it drop that drawing's note from this item list.
        self.registry = AnnotationRegistry()
        self.draft = draft
        self.items = list(blockers)
        self.page_w, self.page_h = page_w, page_h
        self._names = {f"blocker{i}": o for i, o in enumerate(blockers)}
        self.views = {f"v{i}": box for i, box in enumerate(view_boxes)}

    def iter_annotations(self):
        yield from self._names.items()
        yield from ((name, self.registry.named(name)) for name in self.registry.names())

    def get_annotation(self, name):
        return self._names.get(name) or self.registry.named(name)

    def view_bounds(self, view):
        return self.views.get(view)


def _harness(obstacle_boxes, view_boxes=(), iso_x=200.0, rider_boxes=(), furniture=(), shafts=()):
    """A stand-in drawing plus the Analysis fields the placer reads.

    *obstacle_boxes* are placed annotations; *view_boxes* are projected views; *rider_boxes*
    are page-spanning riders; *furniture* names real annotations to include (the title block
    is bounded furniture with NO label box, reached only by the hull fallback); *shafts* are
    drawn strokes whose label sits elsewhere.
    """
    drawing = build_drawing(Box(40, 30, 8), page="A3", auto_dims=False)
    blockers = [_Blocker(box) for box in obstacle_boxes]
    blockers += [_Rider(box) for box in rider_boxes]
    blockers += [_Shaft(segment) for segment in shafts]
    for name in furniture:
        real = drawing.get_annotation(name)
        assert real is not None, f"fixture no longer draws {name!r}"
        assert getattr(real, "label_bbox", None) is None, (
            f"{name!r} now carries a label box — it is no longer the unlabelled-furniture "
            f"case this test exists for"
        )
        blockers.append(real)
    stand_in = _StandIn(drawing.draft, blockers, view_boxes, drawing.page_w, drawing.page_h)
    # Real furniture keeps its REAL name — `late_furniture_obstacles` reaches the title
    # block by name, so filing it as "blocker7" would make it invisible for the wrong
    # reason and quietly hollow out the test that depends on it.
    for name in furniture:
        stand_in._names[name] = stand_in._names.pop(
            next(k for k, v in stand_in._names.items() if v is drawing.get_annotation(name))
        )
    analysis = SimpleNamespace(
        ISO_X=iso_x, margin=10.0, PAGE_W=drawing.page_w, PAGE_H=drawing.page_h
    )
    return stand_in, analysis


def _caption_box(stand_in):
    note = stand_in.registry.named("note_iso_nts")
    assert note is not None, "the caption was not placed at all"
    return _anno_box(note)


class TestTheCaptionAvoidsWhatIsAlreadyPlaced:
    def test_a_clear_natural_position_is_kept(self):
        # The common case must not move: today's placement is right whenever it is free,
        # and a fix that relocated every caption would be a gratuitous output change.
        stand_in, analysis = _harness([])
        place_iso_nts_note(stand_in, analysis, (180.0, 120.0, 220.0, 160.0))
        box = _caption_box(stand_in)
        font = stand_in.draft.font_size
        assert box[1] == pytest.approx(120.0 - 2 * font, abs=font)

    def test_a_blocked_natural_position_moves(self):
        # Mutation: delete the obstacle test and the caption lands inside `blocker`.
        blocker = (150.0, 100.0, 260.0, 122.0)  # covers the natural position
        stand_in, analysis = _harness([blocker])
        place_iso_nts_note(stand_in, analysis, (180.0, 120.0, 220.0, 160.0))
        box = _caption_box(stand_in)
        assert not _boxes_overlap(box, blocker), (
            f"caption at {box} still overlaps the annotation at {blocker}"
        )

    def test_the_caption_is_kept_even_when_nothing_is_clear(self):
        # Policy B: a caption saying which view is not to scale is required content. It
        # is never dropped to avoid an overlap — the overlap is reported instead.
        everywhere = (0.0, 0.0, 420.0, 297.0)
        stand_in, analysis = _harness([everywhere])
        place_iso_nts_note(stand_in, analysis, (180.0, 120.0, 220.0, 160.0))
        assert stand_in.registry.named("note_iso_nts") is not None

    def test_a_sideways_fallback_is_refused_when_it_would_leave_the_page(self):
        # The margin guard, driven rather than assumed. An iso block hard against the
        # right margin puts the right-hand fallback off the sheet, and every OTHER
        # candidate is blocked — so the guard is the only thing between the caption and
        # the sheet edge, and the caption falls all the way back to the (overlapping,
        # Policy-B) natural position rather than stepping outside the page.
        #
        # Two earlier versions of this test were wrong about their own scenario: the
        # first blocked every candidate including the off-page one, so it held whether or
        # not the guard existed; the second's comment claimed the caption "takes the LEFT
        # one" when tracing showed the left candidate was inside the blocker too. The
        # scenario is now asserted rather than described.
        iso = (355.0, 120.0, 400.0, 160.0)
        blocker = (300.0, 100.0, 400.9, 170.0)

        control, control_analysis = _harness([], iso_x=377.5)
        place_iso_nts_note(control, control_analysis, iso)
        natural = _caption_box(control)
        font = control.draft.font_size
        width = natural[2] - natural[0]
        right_edge = iso[2] + font + width
        assert right_edge > control_analysis.PAGE_W - control_analysis.margin, (
            f"the right-hand candidate ends at {right_edge:.1f}, inside the margin — "
            f"there is nothing here for the guard to refuse"
        )
        assert _boxes_overlap(natural, blocker), "the natural position is not blocked"
        assert iso[0] - font - width > blocker[0], "the left-hand candidate is not blocked"
        # ...and the off-page candidate must be left UNBLOCKED, keep-clear band included.
        # Adding the 2 mm band pushed the right candidate's guard edge back to 401.0 while
        # the blocker still ended at 402.0, so the blocker rejected it and the margin check
        # was never reached — the third time this test has been wrong about its own setup.
        clearance = control.draft.pad_around_text
        right_keep_clear = (
            iso[2] + font - clearance,
            natural[1] - clearance,
            right_edge + clearance,
            natural[3] + clearance,
        )
        assert not _boxes_overlap(right_keep_clear, blocker), (
            f"the blocker reaches the off-page candidate's keep-clear band "
            f"{right_keep_clear}, so it — not the margin guard — is what rejects it"
        )

        stand_in, analysis = _harness([blocker], iso_x=377.5)
        place_iso_nts_note(stand_in, analysis, iso)
        box = _caption_box(stand_in)
        assert box[2] <= analysis.PAGE_W - analysis.margin + 1e-6, (
            f"caption at {box} runs past the {analysis.PAGE_W - analysis.margin} margin"
        )
        assert box[0] >= analysis.margin - 1e-6


class TestTheCaptionIsPlacedBeforeTheFreelyPositionedFurniture:
    """`builder` places the caption, then the tables. The reason is that the caption is
    tied to the iso block it labels while a table may sit anywhere the sheet has room, so
    the constrained one must claim its space first and become an obstacle for the free
    one — not the reverse.

    That rationale was written in a comment and guarded by nothing: swapping the two
    calls passed the entire fast tier (4,072 tests). Which is exactly the defect the
    previous review found in the candidate order, reintroduced one level up.
    """

    def test_the_gear_table_sees_the_caption_as_an_obstacle(self):
        from build123d import Cylinder

        import draftwright.builder as builder_module
        from draftwright import Sheet
        from draftwright.annotations._common import late_furniture_obstacles

        seen = {}
        original = builder_module.render_gear_tables

        def spy(dwg, model):
            seen["owners"] = {owner for owner, _box in late_furniture_obstacles(dwg, named=True)}
            return original(dwg, model)

        sheet = Sheet(Cylinder(8, 10))
        sheet.external_spur_gear(
            at=(0, 0, 5),
            axis="z",
            tooth_count=13,
            module=1.25,
            pressure_angle=20,
            profile_shift=0,
            face_width=10,
            tooth_thickness=1.9634954084936207,
            tooth_thickness_tolerance=(-0.03, 0.01),
            flank_tolerance_class=7,
        )
        sheet.authored_dimensions()
        builder_module.render_gear_tables = spy
        try:
            drawing = sheet.build()
        finally:
            builder_module.render_gear_tables = original

        assert drawing.get_annotation("note_iso_nts") is not None, (
            "this fixture no longer produces an NTS caption, so it cannot show the ordering at all"
        )
        assert "annotation:note_iso_nts" in seen.get("owners", set()), (
            "the gear table was placed before the caption existed, so it could be put "
            "straight on top of it"
        )


class TestTheCaptionKeepsTheSameClearanceAsOtherLateFurniture:
    def test_a_flush_but_non_overlapping_neighbour_still_displaces_it(self):
        # `add_table` gives its late furniture `draft.pad_around_text` (2 mm) of keep-clear.
        # The caption accepted on strict overlap, so a neighbour 1 mm away counted as
        # clear — and the Policy-B backstop could not report it either: `annotation_overlap`
        # fires only past 0.5 mm in BOTH axes, so a flush caption was invisible twice over.
        # Mutation: set `clearance = 0.0` and the caption stays flush against the blocker.
        iso = (180.0, 120.0, 220.0, 160.0)
        control, control_analysis = _harness([])
        place_iso_nts_note(control, control_analysis, iso)
        natural = _caption_box(control)
        clearance = control.draft.pad_around_text

        gap = 1.0
        assert gap < clearance, "the neighbour is already outside the keep-clear band"
        blocker = (natural[0] - 20.0, natural[1] - 20.0, natural[2] + 20.0, natural[1] - gap)
        assert not _boxes_overlap(natural, blocker), (
            "the blocker overlaps the natural position outright, so a strict-overlap "
            "check would reject it too and the clearance band is not what is tested"
        )

        stand_in, analysis = _harness([blocker])
        place_iso_nts_note(stand_in, analysis, iso)
        box = _caption_box(stand_in)
        keep_clear = (
            box[0] - clearance,
            box[1] - clearance,
            box[2] + clearance,
            box[3] + clearance,
        )
        assert not _boxes_overlap(keep_clear, blocker), (
            f"caption at {box} sits inside the {clearance} mm keep-clear band of the "
            f"annotation at {blocker}"
        )


class TestTheCandidateOrderIsTheStatedOrder:
    """The order encodes a drafting convention, and a convention no test asserts is a
    comment. Adversarial review confirmed the pre-review order (above-the-iso tried
    second) passed the ENTIRE fast tier — the rationale was written down and guarded by
    nothing, so a future tidy-up could reorder the list and see 4,069 tests agree.
    """

    def _positions(self, stand_in, analysis, iso):
        control, control_analysis = _harness([], iso_x=analysis.ISO_X)
        place_iso_nts_note(control, control_analysis, iso)
        natural = _caption_box(control)
        return natural

    def test_a_free_below_position_beats_a_free_above_one(self):
        # A caption printed over the view it labels is against drawing convention. With
        # the natural row blocked and BOTH the further-below and above-the-iso positions
        # free, it must go down. Mutation: move the above candidate back to second and
        # this fails.
        iso = (180.0, 120.0, 220.0, 160.0)
        natural = self._positions(*_harness([], iso_x=200.0), iso)
        blocker = (natural[0] - 2.0, natural[1] - 1.0, natural[2] + 2.0, natural[3] + 1.0)
        stand_in, analysis = _harness([blocker])
        place_iso_nts_note(stand_in, analysis, iso)
        box = _caption_box(stand_in)
        assert box[3] < iso[1], (
            f"caption at {box} went ABOVE the iso block {iso} while a position below it was free"
        )

    def test_directly_below_beats_beside(self):
        # A caption beside the iso reads as captioning whatever view is next to it. With
        # the natural row blocked and further-below, left and right all free, the one
        # still under the iso wins. Mutation: reorder the two sideways candidates ahead
        # of the further-below one and this fails.
        iso = (180.0, 120.0, 220.0, 160.0)
        natural = self._positions(*_harness([], iso_x=200.0), iso)
        blocker = (natural[0] - 2.0, natural[1] - 1.0, natural[2] + 2.0, natural[3] + 1.0)
        stand_in, analysis = _harness([blocker])
        place_iso_nts_note(stand_in, analysis, iso)
        box = _caption_box(stand_in)
        assert box[0] == pytest.approx(natural[0], abs=1e-6), (
            f"caption at {box} stepped sideways to x={box[0]:.1f} while the column "
            f"directly under the iso (x={natural[0]:.1f}) was free"
        )


class TestUnlabelledFurnitureIsStillAnObstacle:
    """`title_block` carries `label_bbox is None`, so a label-box-only obstacle set is
    blind to it — and the further-below candidate walks DOWN with only the page margin
    as a floor. Adversarial review of #1197 reproduced the caption landing at y 19.0-21.7
    inside the title-block hull while both sideways candidates were free, trading the
    callout overlap this fix removes for a title-block overlap it had room to avoid."""

    def test_the_caption_does_not_settle_inside_the_title_block(self):
        # The block's decomposed grid lines bound TEXT-FILLED CELLS, so the stroke
        # occupancy alone leaves free pockets inside it — 1,584 of them are big enough to
        # hold this caption on an A3 sheet at zero clearance. With the 2 mm keep-clear
        # band, measured, **none** survives: the grid lines cover every pocket, and the
        # whole-block hull is defence in depth rather than the thing doing the work.
        #
        # An earlier version of this test claimed the hull was what rejected the caption
        # and offered a mutation recipe for it. That was already false when written — the
        # clearance band had just been added — and adversarial review showed the arm is
        # killed by no test in the repo. The hull is now pinned STRUCTURALLY, by
        # `test_the_shared_set_carries_the_title_block_as_one_hull`, which is the honest
        # scope: the policy is asserted, not a placement consequence it no longer has.
        stand_in, analysis = _harness([], furniture=("title_block",), iso_x=272.6)
        hull = _anno_box(stand_in.get_annotation("title_block"))
        iso = (260.0, 19.77, 300.0, 60.0)

        control, control_analysis = _harness([], iso_x=272.6)
        place_iso_nts_note(control, control_analysis, iso)
        assert _boxes_overlap(_caption_box(control), hull), (
            "the unobstructed position is not inside the title block, so this asserts "
            "nothing about avoiding it"
        )

        place_iso_nts_note(stand_in, analysis, iso)
        box = _caption_box(stand_in)
        assert not _boxes_overlap(box, hull), (
            f"caption at {box} settled inside the title block at {hull}"
        )


class TestLeaderShaftsAreOccupancyToo:
    """The `annotation_overlap` lint compares LABEL extents, so a caption struck through
    by a leader shaft is neither avoided by a label-box obstacle set nor reported by the
    Policy-B backstop this fallback appeals to. Same defect as the one being fixed, one
    layer down (#685 is why `occupancy_boxes` decomposes strokes at all)."""

    def test_a_shaft_whose_label_is_elsewhere_still_blocks(self):
        # The label sits at the origin, far from every candidate; only the stroke reaches
        # the natural position. Mutation: drop `segment_boxes` from the obstacle set and
        # the caption stays on the shaft.
        #
        # The scenario is verified rather than assumed: an unobstructed CONTROL placement
        # establishes where the caption goes, and the shaft is only a valid test if it
        # crosses that. The first version put the shaft at y=111 — 1.6 mm clear of the
        # natural box — so the caption never had to move and the test survived deleting
        # the guard it claimed to cover.
        iso = (180.0, 120.0, 220.0, 160.0)
        control, control_analysis = _harness([])
        place_iso_nts_note(control, control_analysis, iso)
        natural = _caption_box(control)

        row = (natural[1] + natural[3]) / 2
        shaft = ((natural[0] - 40.0, row), (natural[2] + 40.0, row))
        struck = (shaft[0][0] - 1.35, row - 1.35, shaft[1][0] + 1.35, row + 1.35)
        assert _boxes_overlap(natural, struck), (
            f"the shaft at {shaft} misses the natural caption position {natural} — "
            f"this test would pass without the guard it claims to cover"
        )

        stand_in, analysis = _harness([], shafts=[shaft])
        place_iso_nts_note(stand_in, analysis, iso)
        box = _caption_box(stand_in)
        assert not _boxes_overlap(box, struck), (
            f"caption at {box} was placed across the leader shaft at {shaft}"
        )


class TestTheCaptionAvoidsViewsToo:
    def test_a_view_blocks_a_fallback_position(self):
        # Projected views live in `dwg.views`, NOT `dwg.items`, so an annotations-only
        # obstacle set relocates the caption onto a view's line-work and never notices.
        #
        # This test WENT VACUOUS once and a mutation caught it: it originally parked the
        # view at the above-the-iso position, which was candidate 2 at the time. When
        # that candidate moved to last, the caption settled somewhere earlier and the
        # view was never consulted — deleting views from the obstacle set left it green.
        # The view now sits on the FURTHER-BELOW position, which is candidate 2, and the
        # scenario is asserted rather than described.
        iso = (180.0, 120.0, 220.0, 160.0)
        control, control_analysis = _harness([])
        place_iso_nts_note(control, control_analysis, iso)
        natural = _caption_box(control)
        font = control.draft.font_size
        height = natural[3] - natural[1]
        below_y = natural[1] - font - height  # candidate 2 sits one font-height lower
        view_blocker = (natural[0] - 8.0, below_y - 1.0, natural[2] + 8.0, below_y + height + 1.0)
        annotation_blocker = (
            natural[0] - 2.0,
            natural[1] - 1.0,
            natural[2] + 2.0,
            natural[3] + 1.0,
        )
        assert not _boxes_overlap(annotation_blocker, view_blocker), (
            "the annotation blocker already covers the further-below position, so the "
            "view is not what rejects it"
        )

        stand_in, analysis = _harness([annotation_blocker], view_boxes=[view_blocker])
        place_iso_nts_note(stand_in, analysis, iso)
        box = _caption_box(stand_in)
        assert not _boxes_overlap(box, view_blocker), (
            f"caption at {box} landed on the view at {view_blocker}"
        )
        assert not _boxes_overlap(box, annotation_blocker)


class TestRealDrawingsAreUnchangedWhereTheyWereClear:
    def test_a_real_caption_keeps_its_natural_position(self):
        # A2 was parametrised here and produces NO caption at all (the iso stays within
        # 5% of sheet scale, so `_fit_iso_view` returns before captioning), so it
        # asserted nothing about this change. A3 does caption, and the claim worth
        # making is placement, not just lint: an unobstructed caption must sit exactly
        # where it always did.
        dwg = build_drawing(Box(80, 40, 12), page="A3")
        note = dwg.get_annotation("note_iso_nts")
        assert note is not None, "fixture no longer produces an NTS caption"
        from draftwright._core import _iso_bbox

        box = _anno_box(note)
        iso_bottom = _iso_bbox(dwg)[1]
        gap = iso_bottom - box[3]
        assert 0 <= gap <= 4 * dwg.draft.font_size, (
            f"caption sits {gap:.1f} mm below the iso block — it left its natural "
            f"position (~2 font heights) on a sheet with nothing in the way"
        )
        assert not [i for i in dwg.lint() if i.code == "annotation_overlap"]

    def test_a_page_spanning_rider_does_not_neutralise_the_check(self):
        # THE finding-1 case as a unit. `sheet_frame` has no `label_bbox`, so an
        # `_anno_box` obstacle set gets the whole page back and rejects every candidate
        # — the check silently becomes a no-op whenever `frame=True` or `zones=True`.
        # Mutation: swap the obstacle set back to `_anno_box` and this stops relocating.
        page_rider = (10.0, 10.0, 410.0, 287.0)
        blocker = (150.0, 100.0, 260.0, 122.0)
        stand_in, analysis = _harness([blocker], rider_boxes=[page_rider])
        place_iso_nts_note(stand_in, analysis, (180.0, 120.0, 220.0, 160.0))
        box = _caption_box(stand_in)
        assert not _boxes_overlap(box, blocker), (
            f"caption at {box} stayed inside the blocker — a page-spanning rider "
            f"rejected every candidate and the check became a no-op"
        )

    def test_the_real_sheet_frame_carries_the_marker(self):
        # The `_Rider` stand-in asserts nothing unless the real frame is filtered the same
        # way. If the engine stops flagging it, its page-spanning hull becomes an obstacle
        # and every caption candidate is rejected — the silent no-op, back again.
        dwg = build_drawing(Box(80, 40, 12), page="A3", frame=True)
        frames = [o for _n, o in dwg.iter_annotations() if getattr(o, "is_sheet_frame", False)]
        assert frames, "no annotation identifies itself as the sheet frame"
        assert all(getattr(f, "segments", None) in (None, (), []) for f in frames), (
            "the frame gained segments — it would now decompose rather than need filtering, "
            "so this guard is measuring the wrong thing"
        )

    def test_the_shared_set_carries_the_title_block_as_one_hull(self):
        # Membership by NAME is not enough and an earlier version of this test only
        # checked that: `strip_obstacles` already yields ten boxes named `title_block`
        # (its grid lines), so `"annotation:title_block" in owners` held with the hull arm
        # deleted. The policy being pinned is that the block enters as ONE box covering
        # the whole of it — furniture, not a lattice of free pockets (#1145) — so the
        # assertion is on the box.
        from draftwright.annotations._common import late_furniture_obstacles

        dwg = build_drawing(Box(80, 40, 12), page="A3", frame=True)
        obstacles = late_furniture_obstacles(dwg, named=True)
        hull = _anno_box(dwg.get_annotation("title_block"))
        assert any(
            owner == "annotation:title_block" and box == pytest.approx(hull)
            for owner, box in obstacles
        ), (
            f"no obstacle covers the whole title block {hull}; the decomposed grid lines "
            f"leave its text cells open"
        )

    def test_the_shared_set_leaves_out_the_page_spanning_frame(self):
        from draftwright.annotations._common import late_furniture_obstacles

        dwg = build_drawing(Box(80, 40, 12), page="A3", frame=True)
        owners = {owner for owner, _box in late_furniture_obstacles(dwg, named=True)}
        assert not [o for o in owners if "sheet_frame" in o], (
            f"the page-spanning frame is in the obstacle set: {sorted(owners)}"
        )

    def test_a_framed_sheet_is_also_clean(self):
        # `frame=True` draws a page-spanning rider. Before the label-box obstacle set it
        # rejected every candidate and made the whole check a no-op.
        dwg = build_drawing(Box(80, 40, 12), page="A3", frame=True)
        assert not [i for i in dwg.lint() if i.code == "annotation_overlap"]


class _Unmeasurable:
    """A duck-typed item whose `bounding_box()` raises — the compatibility-surface
    object the two `except Exception` arms exist for."""

    label_bbox = None

    def bounding_box(self):
        raise RuntimeError("Standard_DomainError")


class TestAnUnmeasurableItemDoesNotTakeTheWholeSetDownWithIt:
    """Three fail-open paths that the docstrings promise and nothing exercised. Each is a
    duck-typed object misbehaving, which is exactly when a placer must degrade rather
    than raise: the caller is mid-build with a drawing half-assembled.
    """

    def test_an_anonymous_item_that_cannot_be_measured_is_skipped(self):
        # `Drawing.add(obj)` (supported until 0.5.0) yields items with no registry
        # identity, measured by full bbox. One that raises must not abort the walk and
        # lose every LATER obstacle with it.
        from draftwright.annotations._common import late_furniture_obstacles

        stand_in, _analysis = _harness([(150.0, 100.0, 260.0, 122.0)])
        # In `items` but NOT in `_names`: that is what "anonymous" means here.
        stand_in.items.insert(0, _Unmeasurable())
        stand_in.items.append(_Blocker((10.0, 10.0, 20.0, 20.0)))

        boxes = late_furniture_obstacles(stand_in)
        assert (10.0, 10.0, 20.0, 20.0) in boxes, (
            "an unmeasurable item earlier in the list swallowed the ones after it"
        )

    def test_an_unmeasurable_title_block_leaves_the_rest_of_the_set_intact(self):
        # The hull is the fallback for a block with no label box; when even the hull
        # cannot be measured the decomposed grid lines remain, so the set degrades
        # rather than raising.
        from draftwright.annotations._common import late_furniture_obstacles

        stand_in, _analysis = _harness([(150.0, 100.0, 260.0, 122.0)])
        stand_in._names["title_block"] = _Unmeasurable()

        boxes = late_furniture_obstacles(stand_in)
        assert (150.0, 100.0, 260.0, 122.0) in boxes

    def test_a_caption_whose_own_box_is_unresolvable_is_still_placed(self, monkeypatch):
        # Policy B at its limit: if the caption cannot be measured it cannot be placed
        # *carefully*, but it is still required content and must reach the sheet. The
        # alternative — returning without placing — silently drops the one annotation
        # that says the view is not to scale.
        import draftwright.annotations._common as common

        monkeypatch.setattr(common, "_anno_box", lambda _o: None)
        stand_in, analysis = _harness([])
        place_iso_nts_note(stand_in, analysis, (180.0, 120.0, 220.0, 160.0))
        assert stand_in.registry.named("note_iso_nts") is not None, (
            "an unmeasurable caption was dropped instead of placed"
        )
