"""``Sheet.section()`` / ``Sheet.detail()`` — the ADR 4 (was 0011) part-level view verbs (#841).

A blind pocket has no counterbore/spotface/blind-Z-hole to auto-trigger a section, so its
floor and depth stay hidden-line-only. ``Sheet.section()`` forces a cut through a chosen
feature (or an explicit Y, or the part centre); ``Sheet.detail()`` exposes the enlarged
detail-view opt-in as a verb. These tests pin the request→cut-plane resolution, that the
forced section actually renders (and is lint-clean), and the no-request canary.
"""

import pytest
from build123d import Box, Cylinder, Pos
from build123d_drafting.helpers import Centerline

from draftwright import build_drawing
from draftwright.annotations._common import PlacementContext
from draftwright.sheet import Sheet


def _blind_pocket_sheet():
    """An 80×50×20 bar with ONE declared blind rectangular pocket (no auto-section trigger)."""
    s = Sheet(Box(80, 50, 20)).auto_dimensions()
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
    mark = dwg.registry.section_of("section_line")
    assert mark is not None and mark.cut_y == 0.0 and mark.view == "section_aa"
    assert not hasattr(dwg.get_annotation("section_line"), "_dw_section_cut_y")
    assert dwg.get_annotation("section_caption").label == "SECTION A–A"
    assert not [x for x in dwg.lint() if x.code == "annotation_out_of_bounds"]


def test_section_lookup_requires_matching_cut_live_line_and_view_issue_1931():
    s, p = _blind_pocket_sheet()
    s.section(p)
    dwg = s.build()
    assert dwg.registry.has_section(0.0, dwg.views)
    assert not dwg.registry.has_section(1.0, dwg.views)

    view = dwg.views.pop("section_aa")
    assert not dwg.registry.has_section(0.0, dwg.views)
    dwg.views["section_aa"] = view
    PlacementContext(registry=dwg.registry, coverage=dwg.coverage, items=dwg.items).place(
        Centerline((0, 0, 0), (1, 0, 0)), "section_line"
    )
    assert not dwg.registry.has_section(0.0, dwg.views)


def test_deferred_section_replay_and_rollback_issue_1931(monkeypatch):
    from draftwright.annotations import orchestrator

    part = Box(60, 40, 20) - Cylinder(4, 30) - Pos(0, 0, 2) * Cylinder(7, 20)
    dwg = build_drawing(part, page="A3", auto_dims=False)
    dwg._defer_intents = True
    assert dwg.section() == []
    assert dwg.registry.section_of("section_line") is None

    real = orchestrator._maybe_tabulate_holes
    calls = 0

    def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            assert dwg.registry.section_of("section_line") is not None
            raise RuntimeError("after section placement")
        return real(*args, **kwargs)

    monkeypatch.setattr(orchestrator, "_maybe_tabulate_holes", fail_once)
    with pytest.raises(RuntimeError, match="after section placement"):
        dwg.finalize()
    assert "section_aa" not in dwg.views
    assert dwg.registry.section_of("section_line") is None

    dwg.finalize()
    assert "section_aa" in dwg.views
    assert dwg.registry.section_of("section_line") is not None
    dwg._defer_intents = False
    line = dwg.get_annotation("section_line")
    mark = dwg.registry.section_of("section_line")
    assert dwg.section() == []
    assert dwg.get_annotation("section_line") is line
    assert dwg.registry.section_of("section_line") is mark


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


def test_section_rejects_a_foreign_handle():
    # A handle from another Sheet indexes the wrong feature list — must raise, not silently
    # cut the wrong feature (or IndexError). Shared _gdt_ref guard (#841 review).
    s_a, p_a = _blind_pocket_sheet()
    s_b, _p_b = _blind_pocket_sheet()
    with pytest.raises(ValueError, match="different Sheet"):
        s_b.section(p_a)


def test_section_rejects_non_finite_and_out_of_range_at():
    s, _p = _blind_pocket_sheet()
    with pytest.raises(ValueError, match="finite"):
        s.section(at=float("nan"))
    with pytest.raises(ValueError, match="finite"):
        s.section(at=float("inf"))
    # An in-range value is fine; an out-of-range plane (the 50-wide bar spans y∈[-25, 25])
    # is rejected at build resolution rather than mislabelling an uncut projection "SECTION A–A".
    s, _p = _blind_pocket_sheet()
    s.section(at=100.0)
    with pytest.raises(ValueError, match="not strictly inside"):
        s._decorations()
    # A grazing plane on the bounding face cuts nothing — rejected too (strict, not inclusive).
    s, _p = _blind_pocket_sheet()
    s.section(at=25.0)  # exactly the +Y face of the 50-wide bar
    with pytest.raises(ValueError, match="not strictly inside"):
        s._decorations()


def test_detail_sets_the_opt_and_chains():
    s, _p = _blind_pocket_sheet()
    assert s.detail() is s
    assert s._opts["detail_view"] is True
    s.build()  # builds without error (a no-op detail on geometry that doesn't warrant one)
