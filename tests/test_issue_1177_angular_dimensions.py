"""#1177 — an angular dimension must not be drawn as a linear one, or linted as one.

An authored 60° dovetail flank rendered as a horizontal linear dimension, and lint then
compared the degree label against the projected path length in millimetres:

    Dim '60°': label value 60.000 differs from measured path length 16.000
    by 275.0% — possible axis swap or wrong endpoint

Two defects in one line. The drawing asserted a length where the author stated an angle,
and the check that exists to catch a wrong value was itself comparing degrees to
millimetres — so it reported a units mismatch as an axis swap.

The issue's minimal contract allows either rendering a genuine angular dimension or
failing loudly as unsupported *before* producing the misleading annotation. The rendering
library has no angular primitive at all (`Dimension`, `DimensionLine`, `SafeDimension`),
so drawing one correctly is work in `build123d-drafting-helpers`; this refuses instead,
and says so.

Refused at the RENDERER, not at `Sheet.measured_dimension`: `angular` reaches the IR from
two sources — the authored façade and detected AP242 PMI — so a façade guard would leave
the imported path drawing the false dimension, while refusing at import would make a
legitimate AP242 file unreadable.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from build123d import Box

from draftwright import Sheet
from draftwright.annotations.from_model import (
    _renderable_pmi_records,
    _unsupported_kind_records,
)
from draftwright.linting.structural import _is_angular_label, _lint_dim

_DOVETAIL = ((0, -25, 0), (0, -9, 0), (0, -13.619, 8))

#: Kinds whose value IS a straight span, so the linear renderer states them truthfully.
#: Verified by measurement, not assumption: each draws a length equal to its value.
_RENDERABLE = frozenset({"linear", "thickness", "diameter", "radius"})


def _sheet_with_angular():
    sheet = Sheet(Box(115, 50, 68), title="T", number="T-1")
    sheet.measured_dimension(
        kind="angular",
        value=60,
        label="60°",
        dominant_axis="y",
        ref_pts=_DOVETAIL,
    )
    sheet.authored_dimensions()
    return sheet


class TestTheDrawingNoLongerAssertsALengthForAnAngle:
    def test_no_linear_dimension_is_drawn_for_an_angular_declaration(self):
        # The reported reproduction. Before: `pmi_y_0`, a `Dimension` with
        # `measured_length=16.0` carrying the label '60°'.
        drawing = _sheet_with_angular().build()
        drawn = [
            name
            for name, annotation in drawing.iter_annotations()
            if getattr(annotation, "measured_length", None) is not None
            and "60" in str(getattr(annotation, "label", ""))
        ]
        assert drawn == [], f"an angular declaration was drawn as a linear dimension: {drawn}"

    def test_the_omission_is_reported_rather_than_silent(self):
        # "Fail loudly as unsupported" — the alternative the issue permits. Refusing
        # without saying so would trade a wrong drawing for a quietly incomplete one.
        drawing = _sheet_with_angular().build()
        reported = [i for i in drawing.lint() if i.code == "dimension_kind_unsupported"]
        assert reported, "the angular dimension vanished with no diagnostic"
        assert "angular" in reported[0].message

    def test_the_units_mismatch_is_no_longer_reported_as_an_axis_swap(self):
        drawing = _sheet_with_angular().build()
        assert not [i for i in drawing.lint() if i.code == "label_vs_measured"], (
            "lint still compares the degree label against a millimetre path length"
        )

    def test_the_refusal_is_a_validation_outcome_not_a_placement_drop(self):
        # #1190's lesson, reused: an optional or unsupported outcome marked as a PLACEMENT
        # drop makes every scale infeasible, so an explicit `scale=` request burns the ISO
        # ladder and raises where it used to return a drawing. An unsupported category is
        # a fact about the renderer, not a reason to declare the page unusable.
        from draftwright.linting.issues import is_placement_drop

        drawing = _sheet_with_angular().build()
        refusals = [i for i in drawing.lint() if i.code == "dimension_kind_unsupported"]
        assert refusals
        assert not any(is_placement_drop(i) for i in refusals)

    def test_an_explicit_scale_survives_an_angular_declaration(self):
        # The consequence of the above, driven rather than asserted.
        sheet = Sheet(Box(115, 50, 68), title="T", number="T-1", scale=1)
        sheet.measured_dimension(
            kind="angular", value=60, label="60°", dominant_axis="y", ref_pts=_DOVETAIL
        )
        sheet.authored_dimensions()
        assert sheet.build().scale == 1


class TestALinearDeclarationIsUnaffected:
    def test_a_linear_dimension_still_renders_and_still_lints(self):
        # The refusal must be narrow. A linear authored dimension is the common case and
        # must be untouched — including its `label_vs_measured` check, which is the whole
        # reason that check exists.
        sheet = Sheet(Box(115, 50, 68), title="T", number="T-1")
        sheet.measured_dimension(
            kind="linear",
            value=16,
            label="16",
            dominant_axis="y",
            ref_pts=((0, -25, 0), (0, -9, 0)),
        )
        sheet.authored_dimensions()
        drawing = sheet.build()
        assert not [i for i in drawing.lint() if i.code == "dimension_kind_unsupported"], (
            "a linear declaration was refused as an unsupported category"
        )
        drawn = [
            name
            for name, annotation in drawing.iter_annotations()
            if getattr(annotation, "measured_length", None) is not None
        ]
        assert drawn, "the linear declaration was not drawn either"


class TestTheRecordFiltersAgree:
    """`_renderable_pmi_records` and `_unsupported_kind_records` must partition the
    category refusal exactly — a record must not be both drawn and reported, nor silently
    neither."""

    def _record(self, kind, value=60.0, refs=2):
        return SimpleNamespace(
            kind="authored_dimension",
            pmi_kind=kind,
            value=value,
            ref_pts=tuple(range(refs)),
            label=f"{kind}-rec",
            source_id="s",
        )

    def test_an_angular_record_is_refused_and_reported(self):
        records = [self._record("angular")]
        assert _renderable_pmi_records(records) == []
        assert len(_unsupported_kind_records(records)) == 1

    def test_a_linear_record_is_drawn_and_not_reported(self):
        records = [self._record("linear")]
        assert len(_renderable_pmi_records(records)) == 1
        assert _unsupported_kind_records(records) == []

    @pytest.mark.parametrize(("value", "refs"), [(0.0, 2), (60.0, 1)])
    def test_a_record_refused_for_another_reason_is_not_reported_as_a_category(self, value, refs):
        # A zero-value or single-reference record is refused for a different reason and
        # has its own diagnostic. Reporting it as an unsupported CATEGORY would send a
        # reader to the wrong constraint — the mistake #1190 made with `no_room` and the
        # title block.
        records = [self._record("angular", value=value, refs=refs)]
        assert _renderable_pmi_records(records) == []
        assert _unsupported_kind_records(records) == []


class TestEveryDimensionKindIsClassified:
    """Fail-closed, in the shape ADR 0017 phase 1 gave the recogniser manifest: a new kind
    cannot be added to the IR and silently admitted to a renderer that cannot draw it.

    Nothing iterated `AUTHORED_DIMENSION_KINDS` before, so a ninth kind would have gone
    straight into the linear path — which is exactly how `curve_length`, `curved_dist` and
    `oriented` came to be drawn 25-57% wrong without anyone noticing.
    """

    def test_every_kind_is_either_renderable_or_explicitly_refused(self):
        from draftwright.annotations.from_model import _UNRENDERABLE_DIMENSION_KINDS
        from draftwright.model.ir import AUTHORED_DIMENSION_KINDS

        unclassified = set(AUTHORED_DIMENSION_KINDS) - _RENDERABLE - _UNRENDERABLE_DIMENSION_KINDS
        assert not unclassified, (
            f"dimension kind(s) {sorted(unclassified)} are neither known-truthful nor "
            f"refused — they will be drawn through the LINEAR path, which measures a "
            f"straight projected span. Verify what the kind measures and add it to one set."
        )

    def test_every_refused_kind_explains_what_it_measures(self):
        from draftwright.annotations.from_model import (
            _MEASUREMENT_BASIS,
            _UNRENDERABLE_DIMENSION_KINDS,
        )

        assert _UNRENDERABLE_DIMENSION_KINDS <= set(_MEASUREMENT_BASIS), (
            "a refused kind has no stated measurement basis, so its diagnostic falls back "
            "to a generic phrase"
        )

    @pytest.mark.parametrize(
        ("kind", "value"),
        [("curve_length", 25.133), ("curved_dist", 25.133), ("oriented", 20.0)],
    )
    def test_a_kind_measured_on_another_basis_is_not_drawn(self, kind, value):
        # Measured before the fix: each drew a 16.0 mm straight span for these values —
        # 57%, 57% and 25% out. Same defect as angular; both sides happen to be
        # millimetres, so lint reported an actionable-looking magnitude error instead of
        # an axis swap.
        sheet = Sheet(Box(115, 50, 68), title="T", number="T-1", scale=1)
        sheet.measured_dimension(
            kind=kind,
            value=value,
            label=str(value),
            dominant_axis="y",
            ref_pts=((0, -25, 0), (0, -9, 0)),
        )
        sheet.authored_dimensions()
        drawing = sheet.build()
        drawn = [
            getattr(o, "measured_length", None)
            for _n, o in drawing.iter_annotations()
            if getattr(o, "measured_length", None) is not None
        ]
        assert drawn == [], f"{kind} was drawn as a straight span of {drawn}"
        assert [i for i in drawing.lint() if i.code == "dimension_kind_unsupported"]

    def test_a_truthful_kind_is_still_drawn(self):
        # `thickness` measures a straight span and drew 16.0 for a value of 16.0. The
        # refusal must not widen to kinds that are honest.
        sheet = Sheet(Box(115, 50, 68), title="T", number="T-1", scale=1)
        sheet.measured_dimension(
            kind="thickness",
            value=16,
            label="16",
            dominant_axis="y",
            ref_pts=((0, -25, 0), (0, -9, 0)),
        )
        sheet.authored_dimensions()
        drawing = sheet.build()
        assert not [i for i in drawing.lint() if i.code == "dimension_kind_unsupported"]


class TestTheOmissionIsReportedOnce:
    """`lint_pmi_rendering` runs only in `pmi="annotate"` mode, so a Sheet-authored build
    cannot exercise it — the first version of this class asserted the property on one and
    passed with the suppression removed. Driven directly."""

    def _reconcile(self, issue_code):
        from draftwright.linting.pmi_coverage import lint_pmi_rendering

        feature = SimpleNamespace(kind="authored_dimension", source_id="S1", source_ids=("S1",))
        registry = SimpleNamespace(
            issues=[SimpleNamespace(code=issue_code, source_ids=("S1",))],
            names_for_feature=lambda _f: (),
        )
        return [i.code for i in lint_pmi_rendering([feature], registry, "annotate")]

    @pytest.mark.parametrize(
        "explained", ["dimension_kind_unsupported", "authored_dim_degenerate"]
    )
    def test_an_explained_omission_is_not_reported_a_second_time(self, explained):
        # One omission, two reporters at different severities is the defect #1190 was
        # opened for. `authored_dim_degenerate` had it too — its own docstring says it
        # exists so "a caller sees a specific reason instead of a misleading 'no room'",
        # and it was then accompanied by exactly that vaguer error.
        assert "pmi_not_rendered" not in self._reconcile(explained), (
            f"a source already explained by {explained} is reported again as unexplained"
        )

    def test_an_unexplained_omission_is_still_reported(self):
        # The suppression must stay narrow: a record that produced no annotation for a
        # reason nobody recorded is exactly what this check exists to surface.
        assert "pmi_not_rendered" in self._reconcile("some_unrelated_code")

    def test_an_explained_omission_is_not_also_reported_as_unexplained(self):
        # `lint_pmi_rendering` reported the same source a SECOND time, as an error, having
        # already been told about it as a warning — one omission, two reporters, different
        # severities, which is the defect #1190 was opened for.
        drawing = _sheet_with_angular().build()
        codes = [i.code for i in drawing.lint()]
        assert "dimension_kind_unsupported" in codes
        assert "pmi_not_rendered" not in codes, (
            "the refusal is reported twice, once with the real reason and once without"
        )


class TestTheLintDiscriminatesByUnit:
    """The lint half stands alone: it must never compare an angle to a length, whatever
    produced the annotation. The rendered object carries no `dimension_kind`, so the label
    is the only thing an item reaching lint can be asked about."""

    @pytest.mark.parametrize("label", ["60°", "60 °", "60deg", "60 DEG", "1.5°"])
    def test_a_degree_label_is_recognised(self, label):
        assert _is_angular_label(label)

    @pytest.mark.parametrize("label", ["60", "16.0", "ø12", "R6", "60 mm"])
    def test_a_length_label_is_not(self, label):
        assert not _is_angular_label(label)

    def test_an_angle_is_never_compared_against_a_path_length(self):
        issues: list = []
        _lint_dim(SimpleNamespace(label="60°", measured_length=16.0), None, issues)
        assert not [i for i in issues if i.code == "label_vs_measured"], (
            "degrees were compared against millimetres"
        )

    def test_a_genuine_linear_discrepancy_is_still_caught(self):
        # The check must stay load-bearing for the case it exists for: skipping too much
        # would trade a false positive for a blind spot.
        issues: list = []
        _lint_dim(SimpleNamespace(label="60", measured_length=16.0), None, issues)
        assert [i for i in issues if i.code == "label_vs_measured"], (
            "a real 60-vs-16 linear mismatch is no longer reported"
        )
