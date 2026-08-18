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

    def test_each_axis_publishes_its_own_basis(self):
        # `basis` is what tells a caller WHAT a component measured, and the argument for a
        # separate fidelity axis rests on legibility's basis naming layout. Shipping
        # fidelity with the same string would have made the field stop distinguishing them
        # — the PR contradicting its own evidence.
        quality = _quality(99, "99")
        assert quality["legibility"]["basis"] == "layout_issue_severity_with_info_floor"
        assert quality["fidelity"]["basis"] == "asserted_content_contradicted_by_geometry"

    def test_fidelity_has_no_answer_when_the_drawing_asserts_nothing(self):
        # No evidence is DATA, not a perfect score — the same contract completeness and
        # restraint already follow, and the fail-open this module's docstring argues
        # against. Unlike legibility, where an empty sheet genuinely IS legible,
        # truthfulness of an empty utterance is not the same kind of 1.0.
        from draftwright.linting.quality import quality_components

        component = quality_components(
            recognition=None,
            features=(),
            registry=None,
            omissions=(),
            issues=[],
            error_penalty=0.05,
            warning_penalty=0.05,
            has_asserted_content=False,
        )["fidelity"]
        assert component["available"] is False
        assert component["score"] is None
        assert component["reason"]

    def test_the_full_fidelity_shape_is_pinned(self):
        # Legibility's complete dict is pinned in `test_make_drawing.py`; fidelity's was
        # not, so mutating its error penalty to the warning penalty, or its `available` to
        # `bool(issues)`, passed the entire 4,197-test suite.
        component = _quality(99, "99")["fidelity"]
        assert set(component) == {
            "available",
            "score",
            "errors",
            "warnings",
            "infos",
            "placement_drops",
            "by_code",
            "raw_issues",
            "primary_issues",
            "primary_errors",
            "primary_warnings",
            "primary_infos",
            "primary_by_code",
            "affected_pairs",
            "basis",
            "score_inventory",
        }
        assert component["errors"] == 1 and component["warnings"] == 0
        assert component["score"] == pytest.approx(0.8), (
            "the error penalty changed; a material contradiction must not be priced as a warning"
        )
        assert component["by_code"] == {"label_vs_measured": 1}

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


class TestPlacedContentContradictedByGeometryIsAlsoFalse:
    def test_a_callout_for_a_feature_the_part_lacks_lowers_fidelity(self):
        # The PR's first cut claimed `label_vs_measured` was "currently the only such
        # code". This is worse than the case it was built for: a `⌀6 THRU` leader and a
        # centre mark are DRAWN, pointing at solid material, because the declared hole is
        # not in the part. The sheet tells a machinist to drill something the model does
        # not have, and it scored 1.0.
        sheet = Sheet(Box(80, 60, 20), title="T", number="N-1")
        sheet.hole(at=(10, 10, 0), axis="z", diameter=6, through=True)
        # `auto_dimensions`, not `authored_dimensions`: measured, the authored path draws
        # NOTHING for the absent hole, so nothing false reaches the sheet and there is no
        # falsehood to score. The lie is specific to the automatic path, which renders the
        # callout from the declaration without checking the geometry carries it.
        sheet.auto_dimensions()
        drawing = sheet.build()
        summary = drawing.lint_summary()
        assert [i for i in summary["issues"] if i["code"] == "declared_feature_absent"], (
            "the fixture no longer declares a hole the part lacks"
        )
        drawn = [str(getattr(o, "label", "")) for _n, o in drawing.iter_annotations()]
        assert any("⌀6" in d for d in drawn), (
            f"nothing was drawn for the absent hole, so nothing false reached the sheet: {drawn}"
        )
        assert summary["quality"]["fidelity"]["score"] < 1.0


class TestFidelityIsAboutFalsehoodNotAbsence:
    @pytest.mark.parametrize(
        "code",
        ["dimension_kind_unsupported", "callout_dropped", "feature_not_located"],
    )
    def test_a_missing_thing_is_not_a_false_thing(self, code):
        # An omission is a gap, not a lie. Note this says where those codes do NOT belong,
        # not where they go: measured, `callout_dropped` scores on legibility (via the
        # placement-drop suffix) and `dimension_kind_unsupported` and `feature_not_located`
        # score on NOTHING. No lint code routes to completeness at all — it builds its
        # ledger from requirement outcomes. An earlier comment here claimed they "belong to
        # completeness", which was an invented mechanism.
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
