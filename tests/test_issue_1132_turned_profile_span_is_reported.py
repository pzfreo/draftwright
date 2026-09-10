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


# ---------------------------------------------------------------------------
# Span arithmetic — the merge-and-scan's edge cases
#
# Second-stage review of the fix for the overstated-gap defect: the arithmetic was correct on
# all ten of these, and NOTHING in the suite held any of them. Verified behaviour that no test
# guards is behaviour a refactor can take away silently, so they are pinned here.
# ---------------------------------------------------------------------------


class _Feat:
    """A duck-typed stand-in — this module reads features structurally, never by class."""

    def __init__(self, kind, axis="z", span=None, origin=(0, 0, 0), width=None):
        self.kind = kind
        self.span = span
        self.frame = type("_Frame", (), {"axis": axis, "origin": origin})()
        if width is not None:
            self.width = width


def _steps(*pairs):
    return [_Feat("step", span=((0, 0, lo), (0, 0, hi))) for lo, hi in pairs]


def _gaps(features, extent=(0.0, 20.0)):
    """The reported gaps, or None when the check is silent."""
    from draftwright.linting.coverage import lint_turned_profile_span

    found = lint_turned_profile_span(features, extent, orientation="z", single_solid=True)
    if not found:
        return None
    return found[0].message.split("undescribed (")[1].split(")")[0]


@pytest.mark.parametrize(
    ("case", "features", "expected"),
    [
        # Adjacency and ordering must not manufacture a gap that is not there.
        ("touching spans", _steps((0, 10), (10, 20)), None),
        ("nested spans", _steps((0, 20), (5, 10)), None),
        ("unordered input", _steps((10, 20), (0, 10)), None),
        ("zero-length span between two real ones", _steps((0, 10), (10, 10), (10, 20)), None),
        # A span may be authored with its endpoints either way round.
        ("reversed endpoints", [_Feat("step", span=((0, 0, 20), (0, 0, 0)))], None),
        # Overhang past the body is not a shortfall.
        ("span extends beyond the body", _steps((0, 25)), None),
        # A gap inside the float tolerance is not a gap.
        ("sub-tolerance gap", _steps((0, 10), (10.0001, 20)), None),
        # The defect that prompted all this: report EVERY gap, not the first and everything above.
        ("two separate gaps", _steps((0, 5), (10, 15)), "5..10, 15..20"),
        ("leading gap", _steps((5, 20)), "0..5"),
        # A groove is machined INTO the profile, so it covers its own band (#953).
        (
            "groove fills the gap",
            _steps((0, 5), (15, 20)) + [_Feat("groove", origin=(0, 0, 10), width=10.0)],
            None,
        ),
    ],
)
def test_span_arithmetic(case, features, expected):
    assert _gaps(features) == expected, case


@pytest.mark.parametrize(
    "groove",
    [
        _Feat("groove", origin=(0, 0, 10)),  # no width
        type("_NoOrigin", (), {"kind": "groove", "frame": type("_F", (), {"axis": "z"})()})(),
    ],
)
def test_a_malformed_groove_does_not_raise(groove):
    """The first pass at this made the STEP frame read defensive and left the groove branch
    raising two lines below it. Same defect, same function, and the earlier no-frame test
    missed it because it only exercised a step."""
    assert _gaps([*_steps((0, 10)), groove]) is not None  # reports the 10..20 gap, does not raise
