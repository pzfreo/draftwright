"""Regression coverage for hybrid whole-part envelope declarations (#1000)."""

from __future__ import annotations

import pytest
from build123d import Box

from draftwright import Sheet, SoftDeprecationWarning


def _envelopes(sheet: Sheet) -> list:
    return [feature for feature in sheet.features if feature.kind == "envelope"]


def test_from_part_no_arg_envelope_reuses_the_detected_feature_and_handle():
    part = Box(30, 20, 5)
    sheet = Sheet.from_part(part)
    detected = _envelopes(sheet)
    assert len(detected) == 1, "the fixture must start with detection's whole-part envelope"

    whole = sheet.envelope()
    whole.tolerance(0.05, on="width")
    sheet.dimension(whole, "width.length")

    model = sheet.model()
    assert _envelopes(sheet) == detected, "the no-arg declaration must not append a duplicate"
    assert len(model.authored_dimensions) == 1
    assert model.authored_dimensions[0].feature is model.features[0]
    assert model.authored_dimensions[0].role == "width.length"

    decorations = list(model.decorations.items())
    assert len(decorations) == 1
    (feature, kind, role), tolerance = decorations[0]
    assert feature is model.features[0], "the returned handle must retain the detected token"
    assert (kind, role, tolerance) == ("length", "width", 0.05)


def test_reused_envelope_does_not_create_phantom_suppressions():
    sheet = Sheet.from_part(Box(30, 20, 5))
    sheet.dimension(sheet.envelope(), "width.length")

    rows = sheet.build().suppressions()
    assert sorted(row["parameter_id"] for row in rows) == ["depth.length", "height.length"]
    assert all(row["authored"] for row in rows)


def test_plain_sheet_no_arg_envelope_still_declares_one():
    with pytest.warns(SoftDeprecationWarning):
        sheet = Sheet(Box(30, 20, 5)).auto_dimensions()

    sheet.envelope()
    assert len(_envelopes(sheet)) == 1


def test_explicit_subobject_envelope_remains_a_distinct_declaration():
    sheet = Sheet.from_part(Box(30, 20, 5))

    sheet.envelope(Box(10, 8, 3))

    assert [(feature.width, feature.depth, feature.height) for feature in _envelopes(sheet)] == [
        (30.0, 20.0, 5.0),
        (10.0, 8.0, 3.0),
    ]


def test_explicit_equal_object_remains_a_distinct_declaration():
    part = Box(30, 20, 5)
    sheet = Sheet.from_part(part)

    sheet.envelope(part)

    envelopes = _envelopes(sheet)
    assert len(envelopes) == 2
    assert envelopes[0] == envelopes[1], (
        "the explicit-object form declares separately even when its geometry is whole-part equal"
    )


def test_no_arg_envelope_reuses_by_equality_not_kind():
    sheet = Sheet.from_part(Box(30, 20, 5))
    sheet.envelope(Box(10, 8, 3))
    sheet.features.reverse()  # put the sub-envelope before the whole-part envelope

    whole = sheet.envelope()
    whole.tolerance(0.1, on="width")

    assert len(_envelopes(sheet)) == 2
    ((feature, kind, role), tolerance) = next(iter(sheet.model().decorations.items()))
    assert feature.width == 30.0, "a kind-only lookup would bind the handle to the sub-envelope"
    assert (kind, role, tolerance) == ("length", "width", 0.1)
