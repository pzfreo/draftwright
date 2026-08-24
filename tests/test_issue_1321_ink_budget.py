"""The ink-overlap budget must say when it stopped looking (#1321).

``MAX_INK_BOOLEANS`` bounds a pathological sheet, and on the NIST CTC parts it is
reached in practice: measured through ``build_drawing`` over the 23 STEP fixtures
in ``tests/fixtures``, CTC-02 and CTC-04 exhaust it in both protocols. Those are
the sheets the check matters most on, so a sheet that goes quiet because the
budget ran out must not be indistinguishable from a sheet with no collisions.
"""

from pathlib import Path

import pytest

from draftwright import build_drawing
from draftwright.linting import structural

_FIXTURE = Path(__file__).parent / "fixtures" / "evaluation" / "topology-a.step"
_CODE = "annotation_ink_overlap_truncated"


@pytest.fixture(scope="module")
def drawing():
    return build_drawing(_FIXTURE)


def test_the_fixture_actually_spends_the_budget(drawing, monkeypatch):
    """The precondition, without which the test below passes against any code.

    A sheet where no pair ever reaches the ink check would report nothing whether
    or not truncation is reported, so assert this fixture reaches it at least once.
    """
    spent = []
    real = structural.worst_shared_place
    monkeypatch.setattr(
        structural,
        "worst_shared_place",
        lambda a, b, **kw: (spent.append(1), real(a, b, **kw))[1],
    )
    drawing.lint()
    assert spent, "fixture reaches no ink comparison — it cannot exercise the budget"


def test_an_untruncated_sheet_does_not_claim_truncation(drawing):
    assert _CODE not in {issue.code for issue in drawing.lint()}


def test_an_exhausted_budget_is_reported(drawing, monkeypatch):
    monkeypatch.setattr(structural, "MAX_INK_BOOLEANS", 0)
    issues = [issue for issue in drawing.lint() if issue.code == _CODE]
    assert issues, "the budget was exhausted and lint said nothing"
    assert "unchecked" in issues[0].message


def test_the_truncation_code_is_classified(drawing, monkeypatch):
    """Every code the engine emits must reach exactly one quality register."""
    monkeypatch.setattr(structural, "MAX_INK_BOOLEANS", 0)
    unscored = drawing.lint_summary()["quality"]["unscored"]
    assert _CODE in unscored["by_code"]
    assert _CODE not in unscored["unclassified"]
