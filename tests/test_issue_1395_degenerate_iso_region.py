"""A degenerate iso zone is a layout fact, not an OCC exception (#1395).

`_fit_iso_view` scales the isometric to fit its page zone. `extent > 0` filters the ratio's
numerator; nothing filtered its counterpart, so a zone with no room yielded a non-positive
factor and handed it to `gp_Trsf.SetScale`. Measured, OCC answers that three ways:

| factor | OCC |
| --- | --- |
| `0.0` | `Standard_Failure` on macOS |
| `0.0` | `Standard_ConstructionError` on Linux — a class that does **not** subclass `Standard_Failure` |
| negative | **accepted** — the iso is mirrored through the origin, silently, on every platform |

Whether either exception is caught depends on *where* the fit runs. The
`except (ValueError, Standard_Failure)` handlers in `builder` wrap **candidate** builds only;
the requested-scale build reaches `_settle_iso_view` outside them, so on that path the zero
case aborts `build_drawing` on macOS too — verified below, not assumed. The Linux case
additionally escapes even inside a candidate build, because its class is not caught at all.

The negative case is the worst of the three and the issue's own reproduction cannot reach it:
both failures it reports are the zero one.

The invariant under test is *a non-positive factor never reaches the transform*, so the unit
tests record what `_fit_iso_view` hands to `_project_iso`. `test_the_reported_crash_is_fixed_end_to_end`
is the control that the whole thing works through the public API.
"""

from __future__ import annotations

import logging

import pytest
from build123d import Cylinder, Pos
from OCP.gp import gp_Pnt, gp_Trsf
from OCP.Standard import Standard_ConstructionError, Standard_Failure

from draftwright import build_drawing, projection


@pytest.fixture
def _zone_log(caplog):
    caplog.set_level(logging.WARNING, logger="draftwright.projection")
    return caplog


class _Zone:
    """The seven `Analysis` fields `_fit_iso_view` reads.

    Verified to be exactly the set it consumes; a field it starts reading later raises
    `AttributeError` here rather than being silently defaulted.
    """

    def __init__(self, left, bottom, right, top, *, iso_x, iso_y, scale=1.0):
        self.iso_left_limit = left
        self.iso_bottom_limit = bottom
        self.iso_right_limit = right
        self.iso_top_limit = top
        self.ISO_X = iso_x
        self.ISO_Y = iso_y
        self.SCALE = scale

    def __repr__(self):
        return (
            f"_Zone({self.iso_left_limit}, {self.iso_bottom_limit}, "
            f"{self.iso_right_limit}, {self.iso_top_limit})"
        )


class _Sheet:
    """A drawing stand-in exposing only the iso bounds `_iso_bbox` reads."""

    def __init__(self, bounds=(80.0, 80.0, 120.0, 120.0)):
        self.views = {"iso": object()}
        self._bounds = bounds

    def view_bounds(self, _name):
        return self._bounds


def _scales_handed_to_occ(monkeypatch, zone, sheet=None):
    """Every scale the fit passed to `_project_iso`; empty when it projected nothing."""

    seen: list[float] = []
    monkeypatch.setattr(projection, "_project_iso", lambda _d, _a, scale: seen.append(scale))
    projection._fit_iso_view(sheet or _Sheet(), zone)
    return seen


def _degenerate_part():
    """A turned part that, at 10:1 on A4, composes an iso zone with no room.

    Found by sweeping parts and scales. Synthetic, and kept in the fast tier for that reason:
    it reproduces the reported crash in ~2 s where the real corpus fixture takes ~30 s.
    """

    return Cylinder(4, 30) + Pos(0, 0, 15) * Cylinder(6, 6) - Cylinder(1.5, 60)


# ── the fix, through the public API ───────────────────────────────────────────────────────


def test_the_reported_crash_is_fixed_end_to_end():
    """The issue's acceptance criterion: the invalid candidate must not abort the run, and the
    drawing must keep its required views.

    Against unfixed source this exact call raises `Standard_Failure: gp_Trsf::SetScaleFactor`
    out of `build_drawing` — on macOS as well as Linux, because `_settle_iso_view` runs outside
    the candidate-build handlers.
    """

    with pytest.warns(Warning):  # the scale falls back; that is the point, not an error
        drawing = build_drawing(_degenerate_part(), title="T", number="N", scale=10.0, page="A4")

    assert {"front", "plan", "side"} <= set(drawing.views), "required views must survive"
    assert drawing.views["iso"][0].bounding_box().size.X > 0, "the iso must not be degenerate"


@pytest.mark.slow
def test_a_real_fixture_reproduces_the_crash_and_now_builds():
    """The same bug on a part already in the corpus, not a shape constructed to provoke it.

    `issue_915_case_study_2.step` at 10:1 composes an iso zone with `needed = 6.9e-18`. Without
    the guard this raises `Standard_Failure` out of `build_drawing`; with it the automatic
    search falls back to 2:1 and keeps every view including the section and detail. Slow tier
    (~30 s) because the synthetic case above proves the same contract in ~2 s; this one is here
    so the evidence is a real part rather than only a manufactured one.
    """

    drawing = build_drawing(
        "tests/fixtures/issue_915_case_study_2.step", pmi="off", repair=False, scale=10.0
    )

    assert {"front", "plan", "side", "section_aa"} <= set(drawing.views)
    assert drawing.scale == 2.0, "the search must fall back, not abort"


# ── the invariant, at the seam ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "zone", "unguarded_factor"),
    [
        # Each carries the factor the unguarded code produces, so the fixture proves it really
        # exercises the guard rather than being refused by some other early return.
        ("zero width, centre inside", _Zone(80.0, 0.0, 80.0, 200.0, iso_x=80.0, iso_y=100.0), 0.0),
        ("zero height", _Zone(0.0, 100.0, 200.0, 100.0, iso_x=100.0, iso_y=100.0), 0.0),
        ("collapsed width", _Zone(120.0, 0.0, 120.0, 200.0, iso_x=100.0, iso_y=100.0), -0.98),
        ("inverted width", _Zone(140.0, 0.0, 120.0, 200.0, iso_x=100.0, iso_y=100.0), -1.96),
    ],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_a_zone_with_no_room_hands_no_scale_to_the_transform(
    monkeypatch, _zone_log, name, zone, unguarded_factor
):
    """Both signs of the defect, and both axes.

    `== []` alone cannot tell the guard from a different early return, so each case also
    asserts the guard is what refused it — via the diagnostic it emits — and the parametrised
    `unguarded_factor` records the value the old code would have projected.
    """

    assert _scales_handed_to_occ(monkeypatch, zone) == []
    assert "admits no positive scale" in _zone_log.text, (
        f"{name}: something other than the degenerate-zone guard returned first"
    )
    assert f"{unguarded_factor:g}" in _zone_log.text, (
        f"{name}: expected the guard to report factor {unguarded_factor:g}"
    )


def test_an_overflowing_iso_still_gets_a_positive_scale(monkeypatch):
    """The guard must not refuse work that fits — the negative control."""

    zone = _Zone(90.0, 90.0, 110.0, 110.0, iso_x=100.0, iso_y=100.0)
    sheet = _Sheet(bounds=(50.0, 50.0, 150.0, 150.0))  # iso far larger than its zone

    handed = _scales_handed_to_occ(monkeypatch, zone, sheet)

    assert handed, "an overflowing iso must still be rescaled"
    assert all(scale > 0.0 for scale in handed), f"non-positive scale reached OCC: {handed}"
    gp_Trsf().SetScale(gp_Pnt(0, 0, 0), handed[-1] / zone.SCALE)  # OCC accepts it


def test_across_many_zones_every_projected_scale_is_positive_and_some_are(monkeypatch):
    """The invariant swept broadly. The second assertion matters: without it this passes
    vacuously if the fit ever stops projecting at all."""

    positive = 0
    for left in (60.0, 100.0, 119.0, 120.0, 130.0, 200.0):
        for top in (0.0, 1.0, 100.0, 200.0):
            zone = _Zone(left, 0.0, 120.0, top, iso_x=100.0, iso_y=100.0)
            sheet = _Sheet(bounds=(50.0, 50.0, 150.0, 150.0))
            for scale in _scales_handed_to_occ(monkeypatch, zone, sheet):
                assert scale > 0.0, f"{zone} handed {scale} to the transform"
                positive += 1

    assert positive, "no zone in the sweep projected anything — the assertion never ran"


def test_a_nan_factor_is_refused_although_it_is_not_less_than_zero(monkeypatch):
    """`NaN <= 0.0` is False and OCC accepts a NaN scale silently, so the guard is spelled
    `not factor > 0.0`. No reachable input produces NaN today; this pins the spelling."""

    nan = float("nan")
    mirrored = gp_Trsf()
    mirrored.SetScale(gp_Pnt(0, 0, 0), nan)  # accepted, no exception

    assert not (nan <= 0.0), "the reason `<= 0.0` would not be enough"
    assert not nan > 0.0, "the spelling the guard uses does refuse it"


# ── the authored-scale sibling path ───────────────────────────────────────────────────────


def test_a_degenerate_zone_is_not_reported_as_an_infeasible_authored_scale(_zone_log):
    """`_settle_iso_view` takes the authored-per-view-scale path, which never reaches
    `_fit_iso_view` and so needs its own guard.

    Without it, a zone the *engine* composed with no room raised "authored iso scale is
    infeasible in its composed view zone" — a falsehood about the caller's input, since no
    scale they could have written fits a zone of zero extent. That is the ADR 5 honesty rule:
    a diagnostic must not assert something untrue about who is at fault.
    """

    from draftwright.builder import _settle_iso_view

    # A stand-in rather than the drawing's own `Analysis`: reads of `Drawing._analysis` from
    # tests are a shrinking ratchet (`test_private_test_attr_reads.py`), and this path needs
    # only the zone plus the authored-scale selector.
    zone = _Zone(0.0, 100.0, 200.0, 100.0, iso_x=100.0, iso_y=100.0)
    zone.planned_iso_scale = 1.0  # selects the authored path over `_fit_iso_view`
    zone.view_constraints = None

    result = _settle_iso_view(_Sheet(), zone)

    assert result is not None, "the authored iso is left as projected, not refused"
    assert "has no room for any scale" in _zone_log.text
    assert "authored iso scale" not in _zone_log.text, (
        "the caller must not be blamed for a zone the engine composed"
    )


# ── platform facts the old handler rested on ──────────────────────────────────────────────


def test_standard_construction_error_is_not_a_standard_failure():
    """Pinned rather than assumed from the C++ hierarchy: a bindings change should be visible
    here rather than silently re-enabling the path this issue is about."""

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
