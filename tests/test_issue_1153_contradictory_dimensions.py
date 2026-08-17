"""#1153 — a drawing that contradicts itself cannot report as passing.

GRM-04 labels a long vertical path `4` while lint measures 27 mm. #1209 is the same on
CTC-04, where two AP242 records draw 260 mm for values of 20 and 25. Both issues make the
same complaint: **lint detects the contradiction and the PDF ships it.**

Export is not in fact silent — it logs every lint issue. What it did was bury a false
measurement among twenty other lines at the same severity, and let the drawing report
`passed: True`. Measured before this change: an authored dimension labelled 99 over a
16 mm path — a 518% contradiction — reported `passed: True` with a quality score of 0.95
on A2, because the only thing failing the drawing was an unrelated `view_out_of_bounds`.

The severities were inverted against manufacturing risk. Every other error leaves the
drawing INCOMPLETE — a view off the page, a callout dropped, a requirement unlowered. A
label that disagrees with its own geometry leaves it actively MISLEADING, and a reader has
no way to tell which of the two numbers to believe. That is now an error.

The threshold is a policy number: below a few percent the two may legitimately differ
(display rounding, projected foreshortening), so the drawing is questionable rather than
false. Every real case observed sits far above it — 15.7%, 70.4%, 90.4%, 92.3%, 275%, 518%.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from build123d import Box

from draftwright import Sheet
from draftwright.linting.structural import (
    _MATERIAL_LABEL_DISCREPANCY,
    _label_readings,
    _lint_dim,
)

_REFS = ((0, -25, 0), (0, -9, 0))  # a 16 mm span


def _sheet_labelled(value, label, page="A2"):
    sheet = Sheet(Box(115, 50, 68), title="T", number="T-1", page=page, scale=1)
    sheet.measured_dimension(
        kind="linear", value=value, label=label, dominant_axis="y", ref_pts=_REFS
    )
    sheet.authored_dimensions()
    return sheet


class TestASelfContradictingDrawingDoesNotPass:
    def test_a_false_measurement_fails_the_drawing(self):
        # A2 deliberately: on A3 this fixture also trips `view_out_of_bounds`, which would
        # fail the drawing for an unrelated reason and prove nothing. Measured before the
        # change: passed=True, score 0.95.
        drawing = _sheet_labelled(99, "99").build()
        summary = drawing.lint_summary()
        assert summary["passed"] is False, (
            "a drawing asserting 99 mm over a 16 mm path reported as passing"
        )
        contradictions = [i for i in drawing.lint() if i.code == "label_vs_measured"]
        assert contradictions and all(i.severity == "error" for i in contradictions)

    def test_the_drawing_is_the_only_thing_wrong_with_it(self):
        # Guards the test above from passing for an unrelated reason — the trap that made
        # the A3 version of this fixture worthless.
        drawing = _sheet_labelled(99, "99").build()
        errors = {i.code for i in drawing.lint() if i.severity == "error"}
        assert errors == {"label_vs_measured"}, (
            f"the fixture has other errors {errors}, so the assertion above is not about "
            f"the contradiction"
        )

    def test_a_truthful_dimension_still_passes(self):
        # The change must be narrow: a dimension whose label matches its geometry is the
        # common case and must not be touched.
        drawing = _sheet_labelled(16, "16").build()
        assert not [i for i in drawing.lint() if i.code == "label_vs_measured"]
        assert drawing.lint_summary()["passed"] is True


class TestMaterialityDecidesTheSeverity:
    @pytest.mark.parametrize(("measured", "expected"), [(20.0, "warning"), (10.0, "error")])
    def test_a_small_discrepancy_warns_and_a_large_one_fails(self, measured, expected):
        # 20.4 vs 20.0 is 2% — display rounding or foreshortening, so the drawing is
        # questionable. 20.4 vs 10.0 is 104% — the drawing is false.
        issues: list = []
        _lint_dim(SimpleNamespace(label="20.4", measured_length=measured), None, issues)
        contradictions = [i for i in issues if i.code == "label_vs_measured"]
        assert contradictions, "the discrepancy stopped being reported at all"
        assert all(i.severity == expected for i in contradictions)

    def test_the_threshold_is_where_the_constant_says(self):
        # Driven either side of the constant rather than at hard-coded percentages, so the
        # policy number stays adjustable without the test quietly measuring something else.
        base = 20.0
        for delta, expected in (
            (_MATERIAL_LABEL_DISCREPANCY / 2, "warning"),
            (_MATERIAL_LABEL_DISCREPANCY * 2, "error"),
        ):
            issues: list = []
            _lint_dim(
                SimpleNamespace(label=f"{base * (1 + delta)}", measured_length=base),
                None,
                issues,
            )
            found = [i for i in issues if i.code == "label_vs_measured"]
            assert found and all(i.severity == expected for i in found), (
                f"a {delta:.0%} discrepancy reported {[i.severity for i in found]}"
            )


class TestAnAmbiguousRepeatLabelIsNotAContradiction:
    """`N× v` is drawn under two conventions in this codebase and the label cannot tell
    them apart: `dim_step_typ` "8× 15" is ONE representative step at 15, while a hole
    pitch "3× 20" spans the whole run at 60.

    Comparing only against the product reported the TYP dimension as a 700% error. That was
    survivable as a warning and became a build failure the moment a material discrepancy
    became one — which is how promoting the severity surfaced a pre-existing false positive
    in the check it was promoting.
    """

    def test_both_readings_are_admissible(self):
        assert set(_label_readings("8× 15")) == {120.0, 15.0}

    def test_a_plain_label_has_one_reading(self):
        assert _label_readings("16") == (16.0,)

    def test_a_counted_diameter_is_not_a_pitch(self):
        # "4× ⌀8.5" counts features of diameter 8.5; the product is meaningless.
        assert _label_readings("4× ⌀8.5") == (8.5,)

    @pytest.mark.parametrize("measured", [15.0, 120.0])
    def test_a_repeat_label_matching_either_reading_is_consistent(self, measured):
        issues: list = []
        _lint_dim(SimpleNamespace(label="8× 15", measured_length=measured), None, issues)
        assert not [i for i in issues if i.code == "label_vs_measured"], (
            f"a TYP/pitch dimension drawn at {measured} was reported as contradictory"
        )

    def test_a_repeat_label_matching_neither_reading_still_fails(self):
        # The ambiguity must not become a blanket exemption: 37 is neither 15 nor 120.
        issues: list = []
        _lint_dim(SimpleNamespace(label="8× 15", measured_length=37.0), None, issues)
        contradictions = [i for i in issues if i.code == "label_vs_measured"]
        assert contradictions and all(i.severity == "error" for i in contradictions)


class TestTheEngineStillProducesTruthfulRepeatDimensions:
    def test_a_uniform_staircase_reports_no_contradiction(self):
        # The real annotation behind the false positive: a TYP step dim labelled `N× v`
        # and drawn at v. If the engine ever draws it at N·v instead, the other reading
        # covers it — but it must not report an error either way.
        from build123d import Pos

        from draftwright import build_drawing

        part = Box(80, 40, 10)
        for i in range(1, 6):
            part += Pos(0, 0, 5 + i * 7.5) * Box(80 - i * 10, 40, 15)
        drawing = build_drawing(part, page="A3")
        contradictions = [i for i in drawing.lint() if i.code == "label_vs_measured"]
        assert not contradictions, [i.message for i in contradictions]
