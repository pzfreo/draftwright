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
