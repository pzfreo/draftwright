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
from draftwright.projection import _place_iso_nts_note


class _Unlabelled:
    """An annotation with no `label_bbox` but a large geometric bbox — a sheet frame.

    It carries the real `is_sheet_frame` marker, because that flag is exactly how the
    engine tells a page-spanning rider from bounded unlabelled furniture like the title
    block (`_core.overlap_exempt`). A stand-in without it is not a sheet frame; it is
    an annotation genuinely occupying the whole page, and treating it as an obstacle
    is right. `test_the_real_sheet_frame_carries_the_marker` keeps this honest.
    """

    label_bbox = None
    is_sheet_frame = True

    def __init__(self, box):
        self._box = box

    def bounding_box(self):
        x0, y0, x1, y1 = self._box
        return SimpleNamespace(
            min=SimpleNamespace(X=x0, Y=y0, Z=0.0), max=SimpleNamespace(X=x1, Y=y1, Z=0.0)
        )


class _FakeView:
    """A projected view stand-in exposing only `bounding_box()`."""

    def __init__(self, box):
        self._box = box

    def bounding_box(self):
        x0, y0, x1, y1 = self._box
        return SimpleNamespace(min=SimpleNamespace(X=x0, Y=y0), max=SimpleNamespace(X=x1, Y=y1))


def _harness(
    obstacle_boxes,
    view_boxes=(),
    iso_x=200.0,
    unlabelled_boxes=(),
    furniture=(),
    shafts=(),
):
    """A duck-typed drawing carrying only what the caption placer reads.

    *obstacle_boxes* are annotation label boxes; *view_boxes* are projected views,
    which live in `dwg.views` rather than `dwg.items` and must be obstacles too;
    *furniture* names real annotations to append to `items` (the title block is bounded
    page furniture with NO label box, so only the hull fallback reaches it); *shafts*
    are drawn strokes belonging to an otherwise remote label, as a leader's are.
    """
    from draftwright.registry import AnnotationRegistry

    drawing = build_drawing(Box(40, 30, 8), page="A3", auto_dims=False)
    blockers = [SimpleNamespace(label_bbox=box) for box in obstacle_boxes]
    # Riders with no `label_bbox` — the sheet frame and zone grid are these. `_anno_box`
    # would return their page-spanning geometry; the placer must not.
    blockers += [_Unlabelled(box) for box in unlabelled_boxes]
    # A label parked out of the way, whose SHAFT crosses the sheet: the occupancy a
    # label-box-only obstacle set cannot see.
    blockers += [
        SimpleNamespace(label_bbox=(0.0, 0.0, 0.1, 0.1), segments=[segment]) for segment in shafts
    ]
    stand_in = SimpleNamespace(
        draft=drawing.draft,
        # A FRESH registry: the caption placer replaces any prior `note_iso_nts`, and
        # reusing the built drawing's would make it try to drop that drawing's note
        # from this stand-in's item list.
        registry=AnnotationRegistry(),
        items=list(blockers),
        box_cache={},
        views={f"v{index}": (_FakeView(box), None) for index, box in enumerate(view_boxes)},
    )
    for name in furniture:
        real = drawing.get_annotation(name)
        assert real is not None, f"fixture no longer draws {name!r}"
        assert getattr(real, "label_bbox", None) is None, (
            f"{name!r} now carries a label box — it is no longer the unlabelled-furniture "
            f"case this test exists for"
        )
        stand_in.items.append(real)
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
        _place_iso_nts_note(stand_in, analysis, (180.0, 120.0, 220.0, 160.0))
        box = _caption_box(stand_in)
        font = stand_in.draft.font_size
        assert box[1] == pytest.approx(120.0 - 2 * font, abs=font)

    def test_a_blocked_natural_position_moves(self):
        # Mutation: delete the obstacle test and the caption lands inside `blocker`.
        blocker = (150.0, 100.0, 260.0, 122.0)  # covers the natural position
        stand_in, analysis = _harness([blocker])
        _place_iso_nts_note(stand_in, analysis, (180.0, 120.0, 220.0, 160.0))
        box = _caption_box(stand_in)
        assert not _boxes_overlap(box, blocker), (
            f"caption at {box} still overlaps the annotation at {blocker}"
        )

    def test_the_caption_is_kept_even_when_nothing_is_clear(self):
        # Policy B: a caption saying which view is not to scale is required content. It
        # is never dropped to avoid an overlap — the overlap is reported instead.
        everywhere = (0.0, 0.0, 420.0, 297.0)
        stand_in, analysis = _harness([everywhere])
        _place_iso_nts_note(stand_in, analysis, (180.0, 120.0, 220.0, 160.0))
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
        blocker = (300.0, 100.0, 402.0, 170.0)

        control, control_analysis = _harness([], iso_x=377.5)
        _place_iso_nts_note(control, control_analysis, iso)
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

        stand_in, analysis = _harness([blocker], iso_x=377.5)
        _place_iso_nts_note(stand_in, analysis, iso)
        box = _caption_box(stand_in)
        assert box[2] <= analysis.PAGE_W - analysis.margin + 1e-6, (
            f"caption at {box} runs past the {analysis.PAGE_W - analysis.margin} margin"
        )
        assert box[0] >= analysis.margin - 1e-6


class TestUnlabelledFurnitureIsStillAnObstacle:
    """`title_block` carries `label_bbox is None`, so a label-box-only obstacle set is
    blind to it — and the further-below candidate walks DOWN with only the page margin
    as a floor. Adversarial review of #1197 reproduced the caption landing at y 19.0-21.7
    inside the title-block hull while both sideways candidates were free, trading the
    callout overlap this fix removes for a title-block overlap it had room to avoid."""

    def test_the_caption_does_not_walk_into_the_title_block(self):
        # Mutation: drop `title_block` from `_CAPTION_FURNITURE` and the caption takes
        # the further-below candidate, which is inside the block.
        stand_in, analysis = _harness([], furniture=("title_block",), iso_x=340.0)
        title_block = _anno_box(stand_in.items[-1])
        # An iso block low enough that the further-below candidate dips into the title
        # block, but high enough that the natural position clears it — otherwise the
        # natural fallback would satisfy this assertion for the wrong reason.
        iso = (300.0, 36.0, 380.0, 85.0)
        font = stand_in.draft.font_size
        assert iso[1] - 2 * font > title_block[3], "the natural position is not clear"
        # ...with the natural position blocked by an annotation, so a candidate must be
        # chosen at all, and narrow enough to leave the left-hand one free.
        stand_in.items.append(SimpleNamespace(label_bbox=(298.0, 28.0, 400.0, 34.0)))
        _place_iso_nts_note(stand_in, analysis, iso)
        box = _caption_box(stand_in)
        assert not _boxes_overlap(box, title_block), (
            f"caption at {box} landed inside the title block at {title_block}"
        )
        assert box[2] <= iso[0], (
            f"caption at {box} did not take the free left-hand position beside the iso"
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
        _place_iso_nts_note(control, control_analysis, iso)
        natural = _caption_box(control)

        row = (natural[1] + natural[3]) / 2
        shaft = ((natural[0] - 40.0, row), (natural[2] + 40.0, row))
        struck = (shaft[0][0] - 1.35, row - 1.35, shaft[1][0] + 1.35, row + 1.35)
        assert _boxes_overlap(natural, struck), (
            f"the shaft at {shaft} misses the natural caption position {natural} — "
            f"this test would pass without the guard it claims to cover"
        )

        stand_in, analysis = _harness([], shafts=[shaft])
        _place_iso_nts_note(stand_in, analysis, iso)
        box = _caption_box(stand_in)
        assert not _boxes_overlap(box, struck), (
            f"caption at {box} was placed across the leader shaft at {shaft}"
        )


class TestTheCaptionAvoidsViewsToo:
    def test_a_view_blocks_a_fallback_position(self):
        # Projected views live in `dwg.views`, NOT `dwg.items`, so an annotations-only
        # obstacle set relocates the caption onto a view's line-work and never notices.
        # The natural position is blocked by an annotation here, and the position it
        # would otherwise fall back to is occupied by a view.
        # Narrow enough that a sideways fallback stays free — otherwise every
        # candidate is blocked and the placer correctly keeps the caption at its
        # natural position, which would prove nothing about views.
        annotation_blocker = (190.0, 100.0, 215.0, 122.0)  # the centre column
        view_blocker = (150.0, 160.0, 260.0, 200.0)  # the above-the-iso position
        stand_in, analysis = _harness([annotation_blocker], view_boxes=[view_blocker])
        _place_iso_nts_note(stand_in, analysis, (180.0, 120.0, 220.0, 160.0))
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
        stand_in, analysis = _harness([blocker], unlabelled_boxes=[page_rider])
        _place_iso_nts_note(stand_in, analysis, (180.0, 120.0, 220.0, 160.0))
        box = _caption_box(stand_in)
        assert not _boxes_overlap(box, blocker), (
            f"caption at {box} stayed inside the blocker — a page-spanning rider "
            f"rejected every candidate and the check became a no-op"
        )

    def test_the_real_sheet_frame_carries_the_marker(self):
        # The stand-in above asserts nothing unless the real frame is exempted the same
        # way. If the engine ever stops flagging it, the page-spanning rider becomes an
        # obstacle again and every caption candidate is rejected — a silent no-op.
        from draftwright._core import overlap_exempt

        dwg = build_drawing(Box(80, 40, 12), page="A3", frame=True)
        frames = [o for _n, o in dwg.iter_annotations() if getattr(o, "is_sheet_frame", False)]
        assert frames, "no annotation identifies itself as the sheet frame"
        assert all(overlap_exempt(f) for f in frames)

    def test_bounded_unlabelled_furniture_is_not_exempted(self):
        # The other half: `overlap_exempt` must NOT swallow the title block, or the fix
        # for the caption walking into it goes with it.
        from draftwright._core import overlap_exempt

        dwg = build_drawing(Box(80, 40, 12), page="A3")
        title_block = dwg.get_annotation("title_block")
        assert title_block is not None
        assert not overlap_exempt(title_block)

    def test_a_framed_sheet_is_also_clean(self):
        # `frame=True` draws a page-spanning rider. Before the label-box obstacle set it
        # rejected every candidate and made the whole check a no-op.
        dwg = build_drawing(Box(80, 40, 12), page="A3", frame=True)
        assert not [i for i in dwg.lint() if i.code == "annotation_overlap"]
