"""Dropping declared features must not improve the diagnostics (#1126).

On a geometry-only import there is no source-PMI baseline, and the legacy `score` is a
severity penalty over findings. When every finding was a *placement* failure the denominator
was whatever the script asked for, so the two cheapest ways to raise the number were both
harmful — ask for fewer annotations, or enlarge the page — and an agent loop optimising it
degraded the drawing. draftwright-io#432 recorded the consequence in production: a reviewer
accepted the removal of a location callout once its lint findings disappeared.

What closed it is the recognition-owned requirement ledgers: a feature the part HAS and the
script does not dimension is now a finding of its own, so removing a declaration trades a
placement warning for a completeness one instead of buying silence. This module pins that
direction, because the fix is a property of which findings exist and a future ledger change
could quietly restore the inversion.
"""

from __future__ import annotations

import pytest
from build123d import Align, Box, Cylinder, Pos

_C = (Align.CENTER, Align.CENTER, Align.CENTER)

#: Geometry only — an in-memory solid carries no AP242 document, which is the path #1126 is
#: about. These three bores are in the part whatever the script declares; the variants below
#: differ only in how many of them they ask the drawing to state.
_HOLES = ((-25.0, -15.0), (0.0, 15.0), (25.0, -15.0))


def _part():
    part = Box(90, 50, 16, align=_C)
    for x, y in _HOLES:
        part -= Pos(x, y, 0) * Cylinder(4, 40, align=_C)
    return part


def _sheet(declared: int):
    from draftwright import Sheet

    sheet = Sheet(_part(), title="PLATE", page="A3")
    for x, y in _HOLES[:declared]:
        sheet.hole(diameter=8, at=(x, y, 8.0), axis="z").through()
    sheet.auto_dimensions()
    return sheet


@pytest.fixture(scope="module")
def variants():
    """One build per variant, shared: each is a real OCC build."""
    return {
        name: _sheet(declared).build().lint_summary()
        for name, declared in {"everything": 3, "one_dropped": 2, "nothing": 0}.items()
    }


def test_the_fixture_is_a_geometry_only_import(variants):
    """Precondition. With a source-PMI baseline the score already behaved correctly, so a run
    against a PMI-bearing part would prove nothing about the path this issue is on. The key is
    omitted entirely when there is no census, so test for absence rather than a None value."""
    assert "pmi" not in variants["everything"]


def test_dropping_declarations_never_improves_the_score(variants):
    everything = variants["everything"]["score"]
    for name in ("one_dropped", "nothing"):
        assert variants[name]["score"] <= everything, (
            f"declaring less scored better: {name} {variants[name]['score']} > "
            f"everything {everything} — the denominator is the script's again (#1126)"
        )


def test_dropping_declarations_costs_findings_rather_than_buying_silence(variants):
    """The mechanism, not just the outcome — and the half that actually bites.

    Measured: silencing the completeness findings that removal now costs
    (`feature_not_located`, `feature_not_dimensioned`, `hole_requirement_unverifiable`, …)
    leaves the monotonicity test above still passing, because every variant then scores 1.00
    and non-increasing is trivially true of a constant. It kills this test and the
    `geometry_issues` one. The three are load-bearing only together."""
    assert variants["nothing"]["warnings"] > variants["everything"]["warnings"], (
        "dropping every declaration produced no additional finding — the requirement ledger "
        "is not reporting the features the part still physically has"
    )
    gained = set(variants["nothing"]["by_code"]) - set(variants["everything"]["by_code"])
    assert gained, "no new issue code appeared when the declarations were removed"


def test_the_geometry_issue_count_rises_when_content_is_dropped(variants):
    """`geometry_issues` is what a consumer reads to separate standards/geometry faults from
    layout noise, so the loss must land there and not only in the raw warning count."""
    assert variants["nothing"]["geometry_issues"] > variants["everything"]["geometry_issues"]


def test_the_summary_says_the_score_is_not_a_quality_verdict(variants):
    """The other half of #1126: a consumer reaching for `score` is told, in the payload
    itself, that it is not what they want."""
    review = variants["everything"]["review"]["summary"]
    assert "not a drawing-quality or completeness score" in review
    assert variants["everything"]["diagnostic_score"] == variants["everything"]["score"]
