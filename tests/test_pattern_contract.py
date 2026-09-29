"""Pocket and slot patterns share declared-model and editable-callout behavior."""

import pytest
from _pattern_contract import PATTERNS
from build123d import Box

from draftwright.builder import build_drawing
from draftwright.sheet import Sheet


@pytest.fixture(params=PATTERNS, ids=lambda case: case.kind)
def pattern_case(request):
    return request.param


def _declared_sheet(case, part, *, envelope=False):
    sheet = Sheet(part).auto_dimensions()
    if envelope:
        sheet.envelope()
    getattr(sheet, case.kind)(
        case.member(), kind="linear", count=case.count, pitch=case.pitch, direction=(0, 1, 0)
    )
    return sheet


def _unrendered_drawing(case):
    part = Box(*case.part_size)
    sheet = _declared_sheet(case, part, envelope=True)
    drawing = build_drawing(part, model=sheet.model(), auto_dims=False)
    features = [feature for feature in drawing.model().features if feature.kind == case.kind]
    assert len(features) == 1 and features[0].count == case.count
    assert not [name for name in drawing.annotations() if name.startswith(case.callout_prefix)]
    assert not [name for name in drawing.annotations() if name.startswith(case.pitch_prefix)]
    return drawing, features[0]


def test_model_inspection_sees_the_pattern(pattern_case):
    sheet = _declared_sheet(pattern_case, Box(*pattern_case.part_size))
    assert len([feature for feature in sheet.features if feature.kind == pattern_case.kind]) == 1
    model = sheet.model()
    patterns = [feature for feature in model.features if feature.kind == pattern_case.kind]
    assert len(patterns) == 1 and patterns[0].count == pattern_case.count


def test_manual_callout_verb_places_grouped_callout_and_pitch(pattern_case):
    drawing, feature = _unrendered_drawing(pattern_case)
    name = drawing.callout(feature)
    assert name.startswith(pattern_case.callout_prefix)
    assert drawing.get_annotation(name).label == pattern_case.label
    assert [n for n in drawing.annotations() if n.startswith(pattern_case.pitch_prefix)]


def test_deferred_callout_reconstructs_the_pattern(pattern_case):
    drawing, feature = _unrendered_drawing(pattern_case)
    with drawing.deferred():
        drawing.callout(feature)
        assert not [n for n in drawing.annotations() if n.startswith(pattern_case.callout_prefix)]
    names = drawing.annotations()
    callouts = [n for n in names if n.startswith(pattern_case.callout_prefix)]
    assert len(callouts) == 1
    assert drawing.get_annotation(callouts[0]).label == pattern_case.label
    assert [n for n in names if n.startswith(pattern_case.pitch_prefix)]
    assert not [issue for issue in drawing.lint() if issue.code == "annotation_out_of_bounds"]
