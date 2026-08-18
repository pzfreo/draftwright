"""#1176 — a drawing that says something false must not score perfect.

The reported case is a PPP0101 bracket scoring lint 1.0 while omitting its defining
radii and the whole dovetail. Its STEP is not in this repo, so this slice takes the half
that is reproducible here and is the same defect one axis over: `quality` had no component
asking whether the drawing is TRUE.

Measured before this change, on a drawing labelled 99 over a 16 mm path — a 518%
contradiction:

    completeness  available=False   (no auditable recognised requirements)
    restraint     available=False   (provenance incomplete)
    legibility    1.0

`legibility` is 1.0 correctly — its own basis field says
`layout_issue_severity_with_info_floor`, it scores LAYOUT, and a false dimension is
perfectly legible. With the other two unavailable, 1.0 was the only number a caller saw.

So the four axes now answer four different questions: did required content land
(completeness), is there too much of it (restraint), can a reader make it out (legibility),
and is what it says true (fidelity). A drawing can pass the first three and still lie.
"""

from __future__ import annotations

import pytest
from build123d import Box

from draftwright import Sheet
from draftwright.linting.quality import _FIDELITY_CODES, _LEGIBILITY_CODES

_REFS = ((0, -25, 0), (0, -9, 0))  # a 16 mm span


def _quality(value, label, page="A2"):
    sheet = Sheet(Box(115, 50, 68), title="T", number="T-1", page=page, scale=1)
    sheet.measured_dimension(
        kind="linear", value=value, label=label, dominant_axis="y", ref_pts=_REFS
    )
    sheet.authored_dimensions()
    return sheet.build().lint_summary()["quality"]


class TestFidelityIsReportedSeparately:
    def test_a_false_dimension_lowers_fidelity(self):
        # Mutation: drop `label_vs_measured` from `_FIDELITY_CODES` and this returns 1.0.
        quality = _quality(99, "99")
        assert quality["fidelity"]["available"] is True
        assert quality["fidelity"]["score"] < 1.0, (
            "a drawing asserting 99 mm over a 16 mm path scored perfect on fidelity"
        )

    def test_a_truthful_dimension_keeps_fidelity_perfect(self):
        # The axis must be narrow: an ordinary correct drawing is unaffected.
        quality = _quality(16, "16")
        assert quality["fidelity"]["score"] == 1.0

    def test_legibility_is_unchanged_and_still_says_the_drawing_is_readable(self):
        # The point of a separate axis. A false dimension IS legible, so legibility is
        # right to report 1.0 — folding fidelity into it would have made a layout score
        # answer a truthfulness question, which is the category error that produced the
        # hole in the first place.
        quality = _quality(99, "99")
        assert quality["legibility"]["score"] == 1.0
        assert quality["legibility"]["basis"] == "layout_issue_severity_with_info_floor"

    def test_the_axes_do_not_share_codes(self):
        # A code counted twice would penalise one defect on two axes and make them look
        # correlated when they are independent observations.
        assert not (_FIDELITY_CODES & _LEGIBILITY_CODES), (
            f"{sorted(_FIDELITY_CODES & _LEGIBILITY_CODES)} is scored on two axes"
        )


class TestFidelityIsAboutFalsehoodNotAbsence:
    @pytest.mark.parametrize(
        "code",
        ["dimension_kind_unsupported", "callout_dropped", "feature_not_located"],
    )
    def test_a_missing_thing_is_not_a_false_thing(self, code):
        # An omission is a gap, not a lie: it belongs to completeness. Admitting the
        # `*_dropped` family here would make fidelity a second completeness score and
        # leave nothing measuring truthfulness — which is the state this slice fixes.
        assert code not in _FIDELITY_CODES

    def test_an_unsupported_dimension_does_not_lower_fidelity(self):
        # #1207 refuses to draw an angular dimension rather than drawing it as a linear
        # one. Nothing false reaches the sheet, so fidelity is intact; the content is
        # missing, which is a different axis.
        sheet = Sheet(Box(115, 50, 68), title="T", number="T-1", page="A2", scale=1)
        sheet.measured_dimension(
            kind="angular",
            value=60,
            label="60°",
            dominant_axis="y",
            ref_pts=((0, -25, 0), (0, -9, 0), (0, -13.619, 8)),
        )
        sheet.authored_dimensions()
        summary = sheet.build().lint_summary()
        assert [i for i in summary["issues"] if i["code"] == "dimension_kind_unsupported"], (
            "the fixture no longer produces a refused dimension"
        )
        assert summary["quality"]["fidelity"]["score"] == 1.0, (
            "refusing to draw something was scored as drawing something false"
        )
