"""The `also_crosses` branch, gated on every PR (#1332, review round 10).

`test_issue_1332_overlap_remedy.py` covers this too, but every test there is
`@pytest.mark.slow` and `pyproject.toml` sets `addopts = -m 'not slow'`, while
CLAUDE.md records that the slow tier runs **post-merge on main, not as a PR
gate**. So the only coverage for this branch ran after the damage would be done —
which is how round 6 found it untested in the first place, with the suite green
when either half was deleted.

`lint_drawing` is duck-typed (ADR 0005), so the branch can be exercised directly
with stub annotations: no OCC, no fixture build, and the geometry is stated rather
than hunted for.
"""

import pytest

from draftwright.linting.structural import lint_drawing
from draftwright.linting.suggest import _suggest_fix


class _Bounds:
    def __init__(self, box):
        self.min = type("P", (), {"X": box[0], "Y": box[1], "Z": 0.0})()
        self.max = type("P", (), {"X": box[2], "Y": box[3], "Z": 0.0})()


class _Annotation:
    """Exactly the surface `lint_drawing` reads."""

    def __init__(self, label, label_bbox, segments, full=None):
        self.label = label
        self.label_bbox = label_bbox
        self.segments = segments
        self._full = full or label_bbox
        self.elbow = None

    def bounding_box(self):
        return _Bounds(self._full)


def _overlapping_and_crossing():
    """Labels overlap by >0.5 mm on both axes, AND A's line-work crosses B's label."""
    a = _Annotation(
        "A", (10.0, 10.0, 20.0, 12.2), [((0.0, 11.0), (40.0, 11.0))], (0.0, 8.0, 40.0, 14.0)
    )
    b = _Annotation(
        "B", (12.0, 10.5, 22.0, 12.7), [((30.0, 30.0), (31.0, 30.0))], (12.0, 10.0, 31.0, 31.0)
    )
    return a, b


def _overlapping_only():
    """Labels overlap, and neither draws line-work through the other's label."""
    a = _Annotation(
        "A", (10.0, 10.0, 20.0, 12.2), [((0.0, 40.0), (40.0, 40.0))], (0.0, 10.0, 40.0, 41.0)
    )
    b = _Annotation(
        "B", (12.0, 10.5, 22.0, 12.7), [((30.0, 30.0), (31.0, 30.0))], (12.0, 10.0, 31.0, 31.0)
    )
    return a, b


def test_the_fixture_actually_overlaps_and_crosses():
    """The precondition. Stubs that failed to overlap would satisfy every
    assertion below while proving nothing about the branch."""
    issues = lint_drawing(list(_overlapping_and_crossing()))
    assert [i.code for i in issues] == ["annotation_overlap"], [i.code for i in issues]


def test_a_pair_that_also_crosses_is_not_told_to_move_the_text():
    (issue,) = lint_drawing(list(_overlapping_and_crossing()))
    assert "move what is drawn" in issue.message
    assert "use label_offset_x" not in issue.message


def test_an_ordinary_overlap_keeps_the_original_remedy():
    (issue,) = lint_drawing(list(_overlapping_only()))
    assert "use label_offset_x or increase dim offset to separate them" in issue.message
    assert "move what is drawn" not in issue.message


def test_one_pair_reports_one_code():
    """The `continue`: a pair reporting `annotation_overlap` must not also report
    `annotation_ink_overlap`, or one neighbourhood costs two findings."""
    codes = [i.code for i in lint_drawing(list(_overlapping_and_crossing()))]
    assert codes.count("annotation_ink_overlap") == 0
    assert codes.count("annotation_overlap") == 1


@pytest.mark.parametrize("factory", [_overlapping_and_crossing, _overlapping_only])
def test_the_suggestion_never_contradicts_the_message(factory):
    """`_suggest_fix` dispatches on code alone, so the crossing case used to get a
    snippet telling the caller to re-place the text — on a message saying that is
    not enough. The snippet is what an LLM caller pastes.

    Called directly: `lint_drawing` does not attach suggestions, `Drawing.lint()`
    does (`drawing.py`), and this module deliberately avoids building a drawing.
    """
    (issue,) = lint_drawing(list(factory()))
    # `dwg` is only read for richer suggestions on other codes; the
    # `annotation_overlap` branch uses the message alone.
    suggestion = _suggest_fix(issue, None)
    if "move what is drawn" in issue.message:
        assert suggestion is None, suggestion
    else:
        assert suggestion and "dwg.remove" in suggestion
