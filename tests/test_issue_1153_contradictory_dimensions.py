"""#1153 — a drawing that contradicts itself cannot report as passing.

GRM-04 labels a long vertical path `4` while lint measures 27 mm. #1209 is the same on
CTC-04, where two AP242 records draw 260 mm for values of 20 and 25. Both issues make the
same complaint: **lint detects the contradiction and the PDF ships it.**

Export is not in fact silent — it logs every lint issue. What it did was bury a false
measurement among twenty other lines at the same severity, and let the drawing report
`passed: True`. Measured before this change on A2: an authored dimension labelled 99 over a
16 mm path — a 518% contradiction — reported `passed: True`, `errors: 0`, legacy `score`
0.95, with that contradiction as the only finding.

The severities were inverted against manufacturing risk. Every other error leaves the
drawing INCOMPLETE — a view off the page, a callout dropped, a requirement unlowered. A
label that disagrees with its own geometry leaves it actively MISLEADING, and a reader has
no way to tell which of the two numbers to believe. That is now an error.

The threshold is a policy number: below a few percent the two may legitimately differ
(display rounding, projected foreshortening), so the drawing is questionable rather than
false. Measured on the DEFAULT page, the corpus produces nine discrepancies — 15.7, 90.4,
92.3, 96.1 x3, 97.6 x2, 99.1 per cent — so the smallest real one is three times the
threshold and there is no sub-threshold case to calibrate against.

An earlier version of this docstring listed "15.7%, 70.4%, 90.4%, 92.3%, 275%, 518%". Three
of those do not survive checking: 70.4% is not reproducible anywhere in the corpus, 275% is
the dovetail ANGULAR case that #1207 now refuses before anything measures it, and 518% is
the synthetic fixture below rather than an observation.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from build123d import Box

from draftwright import Sheet
from draftwright.linting.structural import (
    _MATERIAL_LABEL_DISCREPANCY,
    _label_reading,
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

    def test_exactly_at_the_threshold_is_an_error(self):
        # The boundary was unpinned: `>=` -> `>` passed the whole suite. 5% is exactly
        # representable and reachable — a label of 21 over a 20 mm path — and the prose
        # said "above" while the code said at-or-above.
        issues: list = []
        _lint_dim(SimpleNamespace(label="21", measured_length=20.0), None, issues)
        found = [i for i in issues if i.code == "label_vs_measured"]
        assert found and all(i.severity == "error" for i in found), (
            "a discrepancy exactly at the threshold is not treated as material"
        )

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


class TestTheProducerSaysWhatItsRepeatLabelMeasures:
    """`N× v` is drawn under two conventions here and the string cannot tell them apart:
    `dim_step_typ` "8× 15" is ONE representative step at 15, while a hole pitch "3× 20"
    spans the whole run at 60.

    A first cut resolved that by admitting BOTH readings for every repeat label. Adversarial
    review showed it silently disabled the check on the four product-convention producers:
    the per-unit value is precisely the length you get from spanning one member instead of
    the run — the most likely wrong endpoint for a pitch dim, and exactly the defect #1153
    and #1209 report. The engine really does draw a "3× 30" pitch dimension — truthfully, at
    180 mm — and a `3× 30` measured across one pitch went from a 200% warning to no finding
    at all, in a PR whose subject is detecting contradictions. (That failing case is
    constructed: no drawing in the corpus draws one wrongly. The label is real, the defect
    is the one #1153 reports, and the masking is what the first cut made possible.)

    So the producer declares it instead, carrying the compiler's own number rather than
    re-deriving a convention from the rendered string — which is what ADR 0016 Amendment 1
    asks for, and the seam `_dw_scale` already uses.
    """

    def test_an_untagged_label_means_what_it_says(self):
        assert _label_reading(SimpleNamespace(), "8× 15") == 120.0

    def test_a_tagged_label_means_what_the_producer_declared(self):
        tagged = SimpleNamespace(_dw_label_value=15.0)
        assert _label_reading(tagged, "8× 15") == 15.0

    def test_a_counted_diameter_is_unaffected(self):
        # "4× ⌀8.5" counts features of diameter 8.5; the product is meaningless and
        # `_label_value` already handles it.
        assert _label_reading(SimpleNamespace(), "4× ⌀8.5") == 8.5

    def test_a_pitch_dim_spanning_one_member_is_still_caught(self):
        # THE detection the first cut lost. Untagged, so the label means the run.
        issues: list = []
        _lint_dim(SimpleNamespace(label="3× 30", measured_length=30.0), None, issues)
        found = [i for i in issues if i.code == "label_vs_measured"]
        assert found and all(i.severity == "error" for i in found), (
            "a pitch dimension drawn across one member instead of the run went unreported"
        )

    def test_a_tagged_typ_dim_drawn_at_its_step_is_clean(self):
        issues: list = []
        _lint_dim(
            SimpleNamespace(label="8× 15", measured_length=15.0, _dw_label_value=15.0),
            None,
            issues,
        )
        assert not [i for i in issues if i.code == "label_vs_measured"]

    def test_a_tagged_dim_drawn_at_the_wrong_length_still_fails(self):
        # Tagging declares the meaning; it is not an exemption.
        issues: list = []
        _lint_dim(
            SimpleNamespace(label="8× 15", measured_length=37.0, _dw_label_value=15.0),
            None,
            issues,
        )
        found = [i for i in issues if i.code == "label_vs_measured"]
        assert found and all(i.severity == "error" for i in found)


class TestOnlyTheProducerThatMeansItTags:
    """A wrongly-TAGGED producer silently disables the check for its labels — the exact
    regression this PR removed, arriving from the other direction. Mis-tagging the
    step-length-chain collapse passed all 4,182 tests; the equivalent mis-tag on the
    hole-pitch producer was killed only incidentally, by unrelated `lint() == []`
    assertions. One producer of four was actually guarded.
    """

    @pytest.mark.parametrize(
        ("name", "part"),
        [
            ("hole row", "row"),
            ("hole grid", "grid"),
        ],
    )
    def test_a_product_convention_dimension_is_not_tagged(self, name, part):
        from build123d import Cylinder, Pos

        from draftwright import build_drawing

        plate = Box(160, 80, 12)
        if part == "row":
            for i in range(4):
                plate -= Pos(-45 + i * 30, 0, 0) * Cylinder(radius=4, height=40)
        else:
            for x in (-40, -10, 20, 50):
                for y in (-20, 20):
                    plate -= Pos(x, y, 0) * Cylinder(radius=3, height=40)
        drawing = build_drawing(plate, page="A3")
        repeats = [
            o
            for o in drawing.items
            if "×" in str(getattr(o, "label", "")) and getattr(o, "measured_length", None)
        ]
        assert repeats, f"the {name} fixture drew no repeat-labelled dimension"
        tagged = [o.label for o in repeats if getattr(o, "_dw_label_value", None) is not None]
        assert not tagged, (
            f"{name}: {tagged} claim per-unit semantics, so lint would read their labels as "
            f"one member instead of the run and stop detecting a wrong-endpoint span"
        )


class TestTheTagSurvivesARebuild:
    def test_a_repaired_dimension_keeps_what_its_label_measures(self):
        # `repair()` runs on EVERY build and a tagged `dim_step_typ` is a legal
        # `dim_inside_part` target. `_replace_dim` carried `_dw_scale` across the rebuild
        # and not this tag, so a repair that moved the dimension would turn a correct
        # drawing into a FAILING one — lint would read '5× 10' as a 50 mm span over a
        # 10 mm path and call it a material contradiction.
        #
        # Latent, not live: instrumenting the whole fast tier showed no tagged dim is
        # replaced today. That is exactly why it needs a test rather than a measurement.
        from types import SimpleNamespace as NS

        from draftwright.repair import _replace_dim

        old = NS(_dw_label_value=10.0, _dw_scale=2.0)
        new = NS()
        registry = NS(
            named=lambda _n: None,
            names=lambda: (),
            name_of=lambda _o: None,
            replace_object=lambda *a, **k: None,
        )
        drawing = NS(items=[old], registry=registry, _registry=registry)
        # No `try` here: the real `_replace_dim` completes on this stand-in (verified), and
        # swallowing would let a future rewrite raise before the copy while the test stayed
        # green — the exact vacuity this file keeps finding elsewhere.
        _replace_dim(drawing, old, new)
        assert getattr(new, "_dw_label_value", None) == 10.0, (
            "a rebuilt dimension lost the number its `N×` label multiplies"
        )
        assert getattr(new, "_dw_scale", None) == 2.0, "the sibling tag regressed"


class TestTheEngineStillProducesTruthfulRepeatDimensions:
    def test_a_uniform_staircase_reports_no_contradiction(self):
        # The real annotation behind the false positive. The first version of this test used
        # rises of [25, 7.5, 7.5, 7.5, 7.5] — NOT uniform, so `_step_repeat` produced no
        # representative rung, no `N×` label was drawn at all, and it asserted "no
        # contradictions" on a drawing with no findings whatsoever. It survived the one
        # mutation it existed to catch.
        from build123d import Pos

        from draftwright import build_drawing

        part = Box(100, 40, 10)
        for i in range(1, 5):
            part += Pos(0, 0, 10 * i) * Box(100 - 20 * i, 40, 10)
        drawing = build_drawing(part, page="A3")
        typ = [
            o
            for o in drawing.items
            if "×" in str(getattr(o, "label", "")) and getattr(o, "measured_length", None)
        ]
        assert typ, "the fixture no longer draws a repeat-labelled dimension"
        assert all(getattr(o, "_dw_label_value", None) is not None for o in typ), (
            "the TYP producer stopped declaring what its label measures, so lint would read "
            "the label as a span and report a false contradiction"
        )
        contradictions = [i for i in drawing.lint() if i.code == "label_vs_measured"]
        assert not contradictions, [i.message for i in contradictions]
