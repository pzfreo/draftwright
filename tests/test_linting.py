"""Unit tests for CoverageState — the lint-side coverage-signal owner
(#138 / ADR 0005, Step 3)."""

import pytest

from draftwright._core import HoleRef
from draftwright.linting import CoverageState

# Pure unit tests — no OCC builds — so they join the build-light `smoke` set (#153).
pytestmark = pytest.mark.smoke

_H1, _H2, _H3 = (HoleRef.of((1, 0, 0)), HoleRef.of((2, 0, 0)), HoleRef.of((3, 0, 0)))
_H9 = HoleRef.of((9, 0, 0))


def test_cover_pattern_records_callout_and_holes():
    c = CoverageState()
    c.cover_pattern("hc_plan0", [_H1, _H2, _H3])
    assert c.is_pattern_callout("hc_plan0")
    assert not c.is_pattern_callout("hc_plan1")
    assert c.is_hole_patterned(_H2)
    assert not c.is_hole_patterned(_H9)


def test_cover_pattern_accumulates_across_calls():
    c = CoverageState()
    c.cover_pattern("a", [_H1])
    c.cover_pattern("b", [_H2, _H3])
    assert c.is_pattern_callout("a") and c.is_pattern_callout("b")
    assert all(c.is_hole_patterned(h) for h in (_H1, _H2, _H3))


def test_dropped_diams_append_read_reset():
    c = CoverageState()
    assert c.dropped_diams == []
    c.drop_diam(8.0)
    c.drop_diam(5.0)
    assert c.dropped_diams == [8.0, 5.0]
    c.reset_dropped()
    assert c.dropped_diams == []
