"""Unit tests for CoverageState — the lint-side coverage-signal owner
(#138 / ADR 0005, Step 3)."""

import pytest

from draftwright.linting import CoverageState

# Pure unit tests — no OCC builds — so they join the build-light `smoke` set (#153).
pytestmark = pytest.mark.smoke


def test_cover_pattern_records_callout_and_holes():
    c = CoverageState()
    c.cover_pattern("hc_plan0", ["h1", "h2", "h3"])
    assert c.is_pattern_callout("hc_plan0")
    assert not c.is_pattern_callout("hc_plan1")
    assert c.is_hole_patterned("h2")
    assert not c.is_hole_patterned("h9")


def test_cover_pattern_accumulates_across_calls():
    c = CoverageState()
    c.cover_pattern("a", ["h1"])
    c.cover_pattern("b", ["h2", "h3"])
    assert c.is_pattern_callout("a") and c.is_pattern_callout("b")
    assert all(c.is_hole_patterned(h) for h in ("h1", "h2", "h3"))


def test_dropped_diams_append_read_reset():
    c = CoverageState()
    assert c.dropped_diams == []
    c.drop_diam(8.0)
    c.drop_diam(5.0)
    assert c.dropped_diams == [8.0, 5.0]
    c.reset_dropped()
    assert c.dropped_diams == []
