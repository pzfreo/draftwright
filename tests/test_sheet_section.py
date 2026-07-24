"""``Sheet.section()`` / ``Sheet.detail()`` — the ADR 0011 part-level view verbs (#841).

A blind pocket has no counterbore/spotface/blind-Z-hole to auto-trigger a section, so its
floor and depth stay hidden-line-only. ``Sheet.section()`` forces a cut through a chosen
feature (or an explicit Y, or the part centre); ``Sheet.detail()`` exposes the enlarged
detail-view opt-in as a verb. These tests pin the request→cut-plane resolution, that the
forced section actually renders (and is lint-clean), and the no-request canary.
"""

import pytest
from build123d import Box

from draftwright.sheet import Sheet


def _blind_pocket_sheet():
    """An 80×50×20 bar with ONE declared blind rectangular pocket (no auto-section trigger)."""
    s = Sheet(Box(80, 50, 20))
    s.envelope()
    p = s.pocket(
        width=8.0,
        length=14.0,
        depth=12.0,
        long_axis="x",
        width_axis="y",
        depth_axis="z",
        lo=-7.0,
        hi=7.0,
        w_center=0.0,
        at=(0.0, 0.0, 4.0),
    )
    return s, p


def test_no_section_without_the_verb():
    # Canary: a plain blind pocket does NOT auto-section — proving the verb is what forces it.
    s, _p = _blind_pocket_sheet()
    assert "section_aa" not in s.build().views


def test_section_through_feature_renders():
    s, p = _blind_pocket_sheet()
    assert s.section(p) is s  # chainable
    dwg = s.build()
    assert "section_aa" in dwg.views
    assert dwg.get_annotation("section_caption").label == "SECTION A–A"
    assert not [x for x in dwg.lint() if x.code == "annotation_out_of_bounds"]


def test_cut_plane_resolution():
    # feature → its centre Y; explicit at= → that Y; bare section() → part-centre Y.
    s, p = _blind_pocket_sheet()
    s.section(p)
    assert s._decorations()["section"] == 0.0  # the pocket sits at y=0

    s, _p = _blind_pocket_sheet()
    s.section(at=12.0)
    assert s._decorations()["section"] == 12.0

    s, _p = _blind_pocket_sheet()
    s.section()
    assert s._decorations()["section"] == 0.0  # the 80×50×20 box centre


def test_section_needs_a_declared_feature():
    s, _p = _blind_pocket_sheet()
    with pytest.raises(ValueError, match="needs a declared feature"):
        s.section(object())  # a bare build123d-ish object is not on this sheet


def test_detail_sets_the_opt_and_chains():
    s, _p = _blind_pocket_sheet()
    assert s.detail() is s
    assert s._opts["detail_view"] is True
    s.build()  # builds without error (a no-op detail on geometry that doesn't warrant one)
