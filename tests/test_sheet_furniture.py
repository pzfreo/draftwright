"""Sheet frame, projection symbol, and zone-grid furniture."""

from build123d import Box

from draftwright import build_drawing
from draftwright._core import _MARGIN


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
