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


def _harness(obstacle_boxes):
    """A duck-typed drawing carrying only what the caption placer reads."""
    from draftwright.registry import AnnotationRegistry

    drawing = build_drawing(Box(40, 30, 8), page="A3", auto_dims=False)
    blockers = [SimpleNamespace(label_bbox=box) for box in obstacle_boxes]
    stand_in = SimpleNamespace(
        draft=drawing.draft,
        # A FRESH registry: the caption placer replaces any prior `note_iso_nts`, and
        # reusing the built drawing's would make it try to drop that drawing's note
        # from this stand-in's item list.
        registry=AnnotationRegistry(),
        items=list(blockers),
        box_cache={},
    )
    analysis = SimpleNamespace(
        ISO_X=200.0, margin=10.0, PAGE_W=drawing.page_w, PAGE_H=drawing.page_h
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

    def test_the_caption_stays_inside_the_page(self):
        # Fallback positions must not solve an overlap by leaving the sheet.
        blocker = (0.0, 100.0, 420.0, 200.0)
        stand_in, analysis = _harness([blocker])
        _place_iso_nts_note(stand_in, analysis, (180.0, 120.0, 220.0, 160.0))
        box = _caption_box(stand_in)
        assert box[0] >= analysis.margin - 1e-6
        assert box[2] <= analysis.PAGE_W - analysis.margin + 1e-6


class TestRealDrawingsAreUnchangedWhereTheyWereClear:
    @pytest.mark.parametrize("page", ["A3", "A2"])
    def test_an_ordinary_part_keeps_a_clean_sheet(self, page):
        dwg = build_drawing(Box(80, 40, 12), page=page)
        assert not [i for i in dwg.lint() if i.code == "annotation_overlap"]
