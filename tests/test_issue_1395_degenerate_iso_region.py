"""A degenerate iso zone is a layout fact, not an OCC exception (#1395).

`_fit_iso_view` measures how much room the isometric has and scales it to fit. Nothing bounded
the *available* side of that ratio, so a collapsed or inverted zone produced a non-positive
scale factor and handed it to `gp_Trsf.SetScale`, which answers three different ways:

| factor | OCC |
| --- | --- |
| `0.0` | `Standard_Failure` on macOS — caught, so the candidate is rejected |
| `0.0` | `Standard_ConstructionError` on Linux — **not** a `Standard_Failure` subclass, so the builder's handler misses it and the whole build aborts |
| negative | **accepted** — the iso is mirrored through the origin onto a delivered sheet, silently, on every platform |

The third is the worst, and the issue's own reproduction cannot reach it: both cases it reports
are the zero one.

The invariant under test is *a non-positive factor never reaches the transform*, so these tests
record what `_fit_iso_view` hands to `_project_iso` rather than inspecting the drawing
afterwards. That also keeps them off `Drawing`'s private `_analysis`, whose test-side reads are
a shrinking ratchet (`test_private_test_attr_reads.py`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pytest
from OCP.gp import gp_Pnt, gp_Trsf
from OCP.Standard import Standard_ConstructionError, Standard_Failure

from draftwright import projection


@dataclass
class _Zone:
    """The seven `Analysis` fields `_fit_iso_view` reads. Explicit, so a future field it starts
    reading fails loudly here rather than being silently defaulted."""

    iso_left_limit: float
    iso_bottom_limit: float
    iso_right_limit: float
    iso_top_limit: float
    ISO_X: float
    ISO_Y: float
    SCALE: float = 1.0


class _Sheet:
    """A drawing stand-in exposing only the iso bounds `_iso_bbox` reads."""

    def __init__(self, bounds=(80.0, 80.0, 120.0, 120.0)):
        self.views = {"iso": object()}
        self._bounds = bounds

    def view_bounds(self, _name):
        return self._bounds


def _factor_handed_to_occ(monkeypatch, zone: _Zone, sheet: _Sheet | None = None):
    """Run the fit and return every scale it passed to `_project_iso`, or [] if it passed none."""

    seen: list[float] = []
    monkeypatch.setattr(projection, "_project_iso", lambda _d, _a, scale: seen.append(scale))
    projection._fit_iso_view(sheet or _Sheet(), zone)
    return seen


def test_standard_construction_error_is_not_a_standard_failure():
    """The platform fact the old `except (ValueError, Standard_Failure)` rested on.

    In these bindings `Standard_ConstructionError` inherits straight from `Exception`, so a
    handler naming only `Standard_Failure` cannot catch it. Pinned rather than assumed from the
    C++ hierarchy: a bindings change should be visible here rather than silently re-enabling
    the path this issue is about.
    """

    assert not issubclass(Standard_ConstructionError, Standard_Failure)
    assert Standard_ConstructionError.__mro__[1] is Exception


def test_occ_rejects_a_zero_scale_but_silently_accepts_a_negative_one():
    """Why a non-positive factor must never reach OCC: only one of the two is loud."""

    with pytest.raises((Standard_Failure, Standard_ConstructionError)):
        gp_Trsf().SetScale(gp_Pnt(0, 0, 0), 0.0)

    mirrored = gp_Trsf()
    mirrored.SetScale(gp_Pnt(0, 0, 0), -1.96)
    assert mirrored.ScaleFactor() == pytest.approx(-1.96), (
        "a negative scale is accepted and mirrors the geometry — no exception on any platform"
    )


@pytest.mark.parametrize(
    ("needed", "factor"),
    [
        (0.0, 0.0),  # collapsed zone
        (1e-9, 0.0),  # rounds to zero at the shrink branch's 4 dp
        (0.004, 0.0039),  # smallest positive that survives that rounding
        (-2.0, -1.96),  # inverted zone
    ],
)
def test_the_shrink_arithmetic_can_produce_a_non_positive_factor(needed, factor):
    """The arithmetic, isolated. `extent > 0` filters the ratio's numerator; nothing filtered
    its counterpart, so a non-positive `needed` flowed straight through."""

    assert math.floor(needed * 0.98 * 10000) / 10000 == pytest.approx(factor)


def test_a_collapsed_zone_hands_no_scale_to_the_transform(monkeypatch):
    zone = _Zone(120.0, 0.0, 120.0, 200.0, ISO_X=100.0, ISO_Y=100.0)

    assert _factor_handed_to_occ(monkeypatch, zone) == [], (
        "a zero-width zone has no scale that fits; the fit must return rather than project"
    )


def test_an_inverted_zone_hands_no_scale_to_the_transform(monkeypatch):
    """A section sharing the iso's y-range raises the zone's left edge with no upper bound, so
    it can pass the right edge. Before the guard this produced a NEGATIVE factor, which OCC
    accepts — a mirrored iso on a delivered sheet, with no error on any platform."""

    zone = _Zone(140.0, 0.0, 120.0, 200.0, ISO_X=100.0, ISO_Y=100.0)

    assert _factor_handed_to_occ(monkeypatch, zone) == []


def test_a_zero_height_zone_hands_no_scale_to_the_transform(monkeypatch):
    zone = _Zone(0.0, 100.0, 200.0, 100.0, ISO_X=100.0, ISO_Y=100.0)

    assert _factor_handed_to_occ(monkeypatch, zone) == []


def test_an_overflowing_iso_still_gets_a_positive_scale(monkeypatch):
    """The guard must not refuse work that fits. A genuinely overflowing iso in a real zone is
    the shrink branch's own case, and it must still reach the transform — with a factor OCC
    will accept."""

    zone = _Zone(90.0, 90.0, 110.0, 110.0, ISO_X=100.0, ISO_Y=100.0)
    sheet = _Sheet(bounds=(50.0, 50.0, 150.0, 150.0))  # iso is far larger than its zone

    handed = _factor_handed_to_occ(monkeypatch, zone, sheet)

    assert handed, "an overflowing iso must still be rescaled"
    assert all(scale > 0.0 for scale in handed), f"non-positive scale reached OCC: {handed}"
    gp_Trsf().SetScale(gp_Pnt(0, 0, 0), handed[-1] / zone.SCALE)  # OCC accepts it


def test_every_zone_either_projects_a_positive_scale_or_projects_nothing(monkeypatch):
    """The invariant itself, swept over degenerate and ordinary zones together."""

    zones = [
        _Zone(left, 0.0, 120.0, top, ISO_X=100.0, ISO_Y=100.0)
        for left in (60.0, 100.0, 119.0, 120.0, 130.0, 200.0)
        for top in (0.0, 1.0, 100.0, 200.0)
    ]

    for zone in zones:
        for scale in _factor_handed_to_occ(monkeypatch, zone):
            assert scale > 0.0, f"zone {zone} handed {scale} to the transform"
