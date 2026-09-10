"""The partial-turned-profile shortfall is reported, and reported as geometry (#1132).

`builder._assemble` used to raise here. `generate_sheet_script` settles its layout through the
same predicate, so raising meant a part whose recognised profile does not tile produced no
script and no drawing — the one failure a consumer cannot route around.

Two properties beyond "a warning appears", both of which a mutation showed the suite did not
otherwise hold: the code must stay in `_GEOMETRY_AWARE_CODES`, and it must stay at warning
severity. Dropping the registration and downgrading to `info` together left the whole fast tier
green, so `geometry_issues` could quietly stop counting a shortfall about the part itself.
"""

from __future__ import annotations

import pytest
from build123d import Box, Cylinder, Pos

from draftwright import Sheet


def _boss_declared_as_step():
    """#631's exact misuse: `.step()` on a boss sitting on a prismatic body."""
    boss = Pos(0, 0, 24) * Cylinder(14, 10)
    sheet = Sheet(Box(90, 64, 38) + boss, title="C").auto_dimensions()
    sheet.envelope()
    sheet.step(boss)
    return sheet.build()


@pytest.fixture(scope="module")
def misuse():
    return _boss_declared_as_step()


def test_the_shortfall_is_reported_rather_than_raised(misuse):
    assert misuse.lint_summary()["by_code"].get("turned_profile_not_spanned") == 1


def test_it_is_counted_as_a_geometry_issue(misuse):
    """`geometry_issues` is what separates a fact about the part from layout noise. A
    consumer reading it must see this one; registration is not decorative."""
    assert misuse.lint_summary()["geometry_issues"] >= 1
    issue = next(i for i in misuse.lint() if i.code == "turned_profile_not_spanned")
    assert issue.severity == "warning", "info severity would drop it below a caller's filter"


def test_the_reported_gap_is_the_real_one():
    """A forward walk that stops at the first gap overstates the shortfall when later steps
    resume — on CADGenBench 109 it named 76.87 mm where 23.69 mm is undescribed. Two steps
    either side of a middle gap pin the arithmetic."""
    from draftwright.linting.coverage import lint_turned_profile_span

    class _F:
        def __init__(self, kind, axis, span=None):
            self.kind = kind
            self.frame = type("_Fr", (), {"axis": axis, "origin": (0, 0, 0)})()
            self.span = span

    features = [
        _F("step", "z", ((0, 0, 0.0), (0, 0, 10.0))),
        _F("step", "z", ((0, 0, 30.0), (0, 0, 40.0))),
    ]
    (issue,) = lint_turned_profile_span(features, (0.0, 40.0), orientation="z", single_solid=True)
    assert "20 mm" in issue.message
    assert "10..30" in issue.message
    assert "0..40" not in issue.message, "the whole part is not the gap"


def test_a_fully_spanned_profile_is_silent():
    """Precondition for the others: the check must not fire on a correct declaration, or
    every assertion above passes for the wrong reason."""
    from draftwright.linting.coverage import lint_turned_profile_span

    class _F:
        def __init__(self, kind, axis, span=None):
            self.kind = kind
            self.frame = type("_Fr", (), {"axis": axis, "origin": (0, 0, 0)})()
            self.span = span

    features = [
        _F("step", "z", ((0, 0, 0.0), (0, 0, 20.0))),
        _F("step", "z", ((0, 0, 20.0), (0, 0, 40.0))),
    ]
    assert (
        lint_turned_profile_span(features, (0.0, 40.0), orientation="z", single_solid=True) == []
    )


def test_a_feature_without_a_frame_does_not_raise():
    """A lint check that raises on a malformed caller model replaces a diagnostic with a
    crash — which is the defect this whole issue is about."""
    from draftwright.linting.coverage import lint_turned_profile_span

    broken = type("_B", (), {"kind": "step"})()
    assert (
        lint_turned_profile_span([broken], (0.0, 40.0), orientation="z", single_solid=True) == []
    )
