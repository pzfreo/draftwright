"""When labels overlap AND line-work crosses one, say so (#1332, review round 6).

`structural.py` emits at most one code per pair: if the two label boxes overlap by
more than 0.5 mm in both axes it reports `annotation_overlap` and skips the ink
check. That suppression is deliberate — one pair, one finding — but it means the
surviving message is the only advice the reader gets, and
`annotation_overlap`'s default remedy is "use label_offset_x or increase dim
offset to separate them", which is exactly what #1321 exists to contradict when
line-work is also drawn through the text.

Round 6 of the review found both the reworded remedy and the suppression itself
entirely untested: deleting either changed real output on `nist_ctc_02_asme1_ap242`
and the suite stayed green.
"""

from pathlib import Path

import pytest

from draftwright import build_drawing

_FIXTURE = Path(__file__).parent / "fixtures" / "nist_ctc_02_asme1_ap242.stp"
_MOVE_THE_TEXT = "use label_offset_x or increase dim offset to separate them"
_MOVE_WHAT_IS_DRAWN = "so separating the text is not enough"


@pytest.fixture(scope="module")
def issues():
    return build_drawing(_FIXTURE).lint()


@pytest.mark.slow
def test_the_fixture_carries_an_overlap_that_also_crosses(issues):
    """The precondition. Without a pair in this state the assertions below would
    pass against a build that had lost the branch entirely."""
    reworded = [
        issue
        for issue in issues
        if issue.code == "annotation_overlap" and _MOVE_WHAT_IS_DRAWN in issue.message
    ]
    assert reworded, (
        "no overlapping pair on this sheet also crosses a label — the remedy branch "
        "is unreachable here and this module is asserting nothing"
    )


@pytest.mark.slow
def test_a_pair_that_also_crosses_is_not_told_to_move_the_text(issues):
    for issue in issues:
        if issue.code == "annotation_overlap" and _MOVE_WHAT_IS_DRAWN in issue.message:
            assert _MOVE_THE_TEXT not in issue.message, (
                "the pair draws line-work through a label, so moving the text is "
                f"not the remedy: {issue.message}"
            )


@pytest.mark.slow
def test_an_ordinary_overlap_still_says_to_move_the_text(issues):
    """The other half: a pair whose labels merely overlap keeps the original advice."""
    plain = [
        issue
        for issue in issues
        if issue.code == "annotation_overlap" and _MOVE_WHAT_IS_DRAWN not in issue.message
    ]
    for issue in plain:
        assert _MOVE_THE_TEXT in issue.message, issue.message


@pytest.mark.slow
def test_one_pair_reports_one_code(issues):
    """The suppression: a pair that reports `annotation_overlap` must not ALSO
    report `annotation_ink_overlap`, or the reader gets two findings for one
    neighbourhood and the #1147 ledger counts it twice."""
    overlapping = {
        frozenset(_labels(issue)) for issue in issues if issue.code == "annotation_overlap"
    }
    crossing = {
        frozenset(_labels(issue)) for issue in issues if issue.code == "annotation_ink_overlap"
    }
    assert not (overlapping & crossing), (
        f"pairs reported under both codes: {sorted(map(sorted, overlapping & crossing))}"
    )


def _labels(issue):
    """The quoted labels in a lint message, in the order they appear."""
    import re

    return re.findall(r"'([^']*)'", issue.message)
