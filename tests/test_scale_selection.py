"""Automatic scale selection and explicit scale/page overrides."""

import pytest

from draftwright.compose import StripDepths, _fits, choose_scale

class TestChooseScale:
    def test_tiny_part_fits_A4(self):
        # 20×20×20 mm — enlargement scales don't fit A4/A3, lands on A4 2:1
        scale, pw, ph, tbw = choose_scale(20, 20, 20)
        assert int(pw) == 297
        assert scale == 2.0

    def test_medium_part_gets_A3(self):
        # 80×80×80 mm — fits A3 1:1 because the view rows clear the title block,
        # so its width no longer forces the jump to A2 (#62)
        scale, pw, ph, tbw = choose_scale(80, 80, 80)
        assert int(pw) == 420

    def test_ctc01_sized_part_gets_A2_not_A1(self):
        # 800×450×150 mm (NIST CTC-01) — iso sits above the title block so tb_w
        # is dropped from the width constraint. A2 fits; A1 is no longer chosen (#103).
        # Pinned to the `columns` arrangement: #103 is an invariant about the layout this
        # part is composed under, and letting the ADR 2 (was 0018) alternatives answer would make it
        # a different claim (see the assertion below for what they answer instead).
        scale, pw, ph, tbw = choose_scale(800, 450, 150, arrangements=("columns",))
        assert scale == pytest.approx(0.2)
        assert int(pw) == 594  # A2 (594 mm), not A1 (841 mm)

    def test_ctc01_compacts_further_when_the_iso_shares_the_title_block_column(self):
        # ADR 2 (was 0018 §5)'s fourth dimension on the part #103 was written about: reclaiming the
        # iso's column takes the same 1:5 drawing down another sheet size. Whether a build
        # KEEPS that is the requirement gate's call, not this one's — `choose_scale` only
        # reports what fits.
        scale, pw, _ph, _tbw = choose_scale(800, 450, 150)
        assert scale == pytest.approx(0.2), "the arrangement must not change the scale"
        assert int(pw) < 594

    def test_large_part_gets_bigger_page(self):
        scale, pw, ph, tbw = choose_scale(300, 300, 300)
        assert pw > 420

    def test_returns_four_values(self):
        result = choose_scale(50, 50, 50)
        assert len(result) == 4

    def test_result_fits_on_page(self):
        # The chosen scale+page should actually fit the layout
        x, y, z = 60, 60, 15
        scale, pw, ph, tbw = choose_scale(x, y, z)
        assert _fits(x, y, z, scale, pw, ph, tbw)

    def test_section_participant_can_reduce_auto_scale(self):
        # A section view is real furniture, not a fixed-offset afterthought. The
        # compact 40×10×20 layout fits A4 at 2:1 without a section, but not once
        # the section block must share the side-view row with the iso and title.
        assert choose_scale(40, 10, 20, section=False)[:3] == (2.0, 297.0, 210.0)
        assert choose_scale(40, 10, 20, section=True)[:3] == (1.0, 297.0, 210.0)

    def test_table_footprint_participates_in_auto_scale_choice(self):
        # The compact 40×10×20 layout fits A4 at 2:1, but once a hole table must
        # share the sheet with the view blocks and iso, A4 has no table slot and
        # the shared fitness model escalates the page instead of dropping it later.
        assert choose_scale(40, 10, 20)[:3] == (2.0, 297.0, 210.0)
        assert choose_scale(40, 10, 20, table_sizes=((100.0, 60.0),))[:3] == (
            2.0,
            420.0,
            297.0,
        )

    def test_table_footprint_uses_composed_view_blocks(self):
        from draftwright.compose import _layout_geometry

        bare = _layout_geometry(
            40, 10, 20, 1.0, 420.0, 297.0, 150.0, None, table_sizes=((100.0, 60.0),)
        )
        stripped = _layout_geometry(
            40,
            10,
            20,
            1.0,
            420.0,
            297.0,
            150.0,
            StripDepths(right=20.0, left=20.0, top=60.0, pv_halo=30.0),
            table_sizes=((100.0, 60.0),),
        )

        assert bare.table_fits
        assert not stripped.table_fits

    # Enlargement scales for small parts (#62)

    def test_small_part_gets_enlargement_scale(self):
        # 28 × 8.5 × 12.5 mm (issue #62 part) → enlarged, and kept on the
        # smallest sheet: 2:1 on A4, not 5:1 on A3.  The ladder is page-major,
        # so a smaller sheet is preferred over a larger enlargement scale.
        scale, pw, ph, tbw = choose_scale(28, 8.5, 12.5)
        assert scale == 2.0
        assert int(pw) == 297

    def test_very_small_part_gets_10x(self):
        scale, pw, ph, tbw = choose_scale(8, 4, 4)
        assert scale == 10.0
        assert int(pw) == 297

    # #350 — never return an overflowing layout for an oversized part.

    def test_oversized_part_gets_a_fitting_reduction(self):
        # 4200 × 1600 × 5400 mm (a civil/weldment-scale part) overflowed A0 1:5 before —
        # the ladder floored at 1:5, so choose_scale returned a layout it had just proved
        # did not fit. It now walks the rest of the ISO 5455 reductions to A0 1:10.
        x, y, z = 4200.0, 1600.0, 5400.0
        scale, pw, ph, tbw = choose_scale(x, y, z)
        assert scale == 0.1 and (pw, ph) == (1189.0, 841.0)  # A0 1:10
        assert _fits(x, y, z, scale, pw, ph, tbw)

    def test_choose_scale_never_overflows_across_the_size_range(self):
        # The invariant: automatic choose_scale never hands back a (scale, page) that
        # _fits reports as overflowing — from tiny to absurdly large (#350).
        for x, y, z in [
            (5, 5, 5),
            (300, 300, 300),
            (4200, 1600, 5400),
            (40000, 2000, 60000),
            (500000, 5000, 800000),
        ]:
            scale, pw, ph, tbw = choose_scale(x, y, z)
            assert _fits(x, y, z, scale, pw, ph, tbw), (
                f"{(x, y, z)} -> {(scale, pw, ph)} overflows"
            )

    def test_backstop_computes_a_fit_beyond_the_ladder(self):
        # A part too large even for A0 1:10000 falls to the bisection backstop and still
        # returns a scale that fits — a non-standard scale is acceptable for an
        # out-of-domain part; anything beats an overflowing layout.
        x, y, z = 20_000_000.0, 5000.0, 30_000_000.0  # ~30 km — deliberately absurd
        scale, pw, ph, tbw = choose_scale(x, y, z)
        assert 0.0 < scale < 0.0001
        assert _fits(x, y, z, scale, pw, ph, tbw)


class TestChooseScaleOverrides:
    def test_scale_and_page_used_verbatim(self):
        assert choose_scale(28, 8.5, 12.5, scale=5, page="A3") == (5.0, 420.0, 297.0, 150.0)

    def test_scale_and_page_honoured_even_when_too_small(self):
        # Explicit overrides win even if the layout doesn't fit (warning only)
        scale, pw, ph, tbw = choose_scale(300, 300, 300, scale=1, page="A4")
        assert (scale, pw) == (1.0, 297.0)

    def test_page_only_picks_largest_fitting_scale(self):
        scale, pw, ph, tbw = choose_scale(28, 8.5, 12.5, page="A3")
        assert (pw, ph) == (420.0, 297.0)
        assert scale == 5.0

    def test_specified_page_enlarges_long_short_part_via_2d_iso(self):
        # A long, short part (100 × 10 × 11, e.g. a staircase) fills a specified
        # A3 sheet at 2:1. The automatic path stays conservative, but fixed-page
        # requests use the packed verdict exposed by the same layout geometry.
        from draftwright.compose import _fits

        assert _fits(100, 10, 11, 2.0, 420.0, 297.0, 150.0, pack_iso_2d=True)
        assert not _fits(100, 10, 11, 2.0, 420.0, 297.0, 150.0, pack_iso_2d=False)
        scale, pw, ph, _ = choose_scale(100, 10, 11, page="A3")
        assert scale == 2.0
        assert (pw, ph) == (420.0, 297.0)
        # Automatic selection remains page-major: A4 at 1:1 is tried before A3 at 2:1.
        assert choose_scale(100, 10, 11)[:3] == (1.0, 297.0, 210.0)

    def test_scale_only_picks_smallest_fitting_page(self):
        scale, pw, ph, tbw = choose_scale(28, 8.5, 12.5, scale=2)
        assert scale == 2.0
        assert int(pw) == 297

    def test_scale_only_enlarges_long_short_part_via_2d_iso(self):
        # Fixed scale, no page: choose_scale walks the page list with the packed
        # verdict exposed by the shared geometry. At 2:1 the part overruns A4
        # but fits A3.
        from draftwright.compose import _fits

        assert not _fits(100, 10, 11, 2.0, 297.0, 210.0, 120.0, pack_iso_2d=True)
        assert _fits(100, 10, 11, 2.0, 420.0, 297.0, 150.0, pack_iso_2d=True)
        assert not _fits(100, 10, 11, 2.0, 420.0, 297.0, 150.0)
        assert choose_scale(100, 10, 11, scale=2) == (2.0, 420.0, 297.0, 150.0)

    def test_page_tuple(self):
        scale, pw, ph, tbw = choose_scale(10, 10, 10, page=(420, 297))
        assert (pw, ph, tbw) == (420.0, 297.0, 150.0)

    def test_page_wxh_string(self):
        scale, pw, ph, tbw = choose_scale(10, 10, 10, page="420x297")
        assert (pw, ph) == (420.0, 297.0)

    def test_page_name_case_insensitive(self):
        scale, pw, ph, tbw = choose_scale(10, 10, 10, page="a3")
        assert (pw, ph) == (420.0, 297.0)

    def test_unknown_page_raises(self):
        with pytest.raises(ValueError, match="page size"):
            choose_scale(10, 10, 10, page="B5")

    def test_nonpositive_scale_raises(self):
        with pytest.raises(ValueError, match="scale"):
            choose_scale(10, 10, 10, scale=0)


