"""The authoritative fail-closed test for the `also_crosses` branch (#1332, #1418).

The former slow CTC-02 regression fixture stopped containing an overlapping pair
that also crosses a label as layout improved, so it no longer reached this branch.
Keeping that stale real-part build would make main red without protecting the remedy.
This fast test instead states the exact geometry, proves the precondition, and runs
in every pull request; deleting either half of the production predicate makes it fail.

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


def test_the_fixture_carries_an_overlap_that_also_crosses():
    """The precondition: without both predicates the tests below prove nothing."""
    issues = lint_drawing(list(_overlapping_and_crossing()))
    assert [issue.code for issue in issues] == ["annotation_overlap"], [
        issue.code for issue in issues
    ]


def test_a_pair_that_also_crosses_is_not_told_to_move_the_text():
    (issue,) = lint_drawing(list(_overlapping_and_crossing()))
    assert "move what is drawn" in issue.message
    assert "use label_offset_x" not in issue.message


def test_an_ordinary_overlap_keeps_the_original_remedy():
    (issue,) = lint_drawing(list(_overlapping_only()))
    assert "use label_offset_x or increase dim offset to separate them" in issue.message
    assert "move what is drawn" not in issue.message


def test_one_pair_reports_one_code():
    """One neighbourhood must not cost both overlap findings."""
    codes = [issue.code for issue in lint_drawing(list(_overlapping_and_crossing()))]
    assert codes.count("annotation_ink_overlap") == 0
    assert codes.count("annotation_overlap") == 1


@pytest.mark.parametrize("factory", [_overlapping_and_crossing, _overlapping_only])
def test_the_suggestion_never_contradicts_the_message(factory):
    """The domain suggestion must agree with the remedy stated to the caller."""
    (issue,) = lint_drawing(list(factory()))
    # `dwg` is used only by richer suggestions for other codes. This branch depends
    # on the message, and the test deliberately avoids constructing a whole Drawing.
    suggestion = _suggest_fix(issue, None)
    if "move what is drawn" in issue.message:
        assert suggestion is None, suggestion
    else:
        assert suggestion and "dwg.remove" in suggestion
