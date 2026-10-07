"""A declared visible-only isometric retains the #2233 axial-section drawing."""

from __future__ import annotations

import runpy
from pathlib import Path

import pytest
from build123d import Box

from draftwright import Sheet
from draftwright.sheet_emit import _adopted_view_block
from draftwright.view_plan import ViewSpec

EXAMPLE = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "docs/examples/quotation_blind_hole.py")
)


def test_hidden_line_choice_is_a_typed_view_request():
    sheet = Sheet(Box(20, 10, 5)).authored_dimensions()
    sheet.view("front")
    iso = sheet.view("iso").hidden_lines(False)
    assert sheet.view_constraints.principals[-1].spec.hidden_lines is False
    assert 'sheet.view("iso").hidden_lines(False)' in "\n".join(
        _adopted_view_block(sheet.view_constraints, {})
    )
    iso.hidden_lines(True)
    assert sheet.view_constraints.principals[-1].spec.hidden_lines is True

    with pytest.raises(TypeError, match="bool"):
        sheet.view("plan").hidden_lines(0)
    with pytest.raises(ValueError, match="principal and isometric"):
        sheet.section_view("A", at=0).hidden_lines(False)
    with pytest.raises(TypeError, match="bool"):
        ViewSpec("iso", "pictorial", hidden_lines="off")


def test_axial_section_keeps_iso_and_requirements_but_omits_its_hidden_edges():
    part = EXAMPLE["turned_blind_hole"]()
    hidden = EXAMPLE["section_with_iso"](part)
    visible_only = EXAMPLE["section_visible_iso"](part)

    assert hidden.view_plan.spec("iso").hidden_lines is True
    assert visible_only.view_plan.spec("iso").hidden_lines is False
    assert "iso" in visible_only.views
    assert len(hidden.views["iso"][1].edges()) > 0
    assert visible_only.views["iso"][1] is None
    assert len(visible_only.views["iso"][0].edges()) == len(hidden.views["iso"][0].edges())
    assert visible_only.section_decision["status"] == "placed"
    assert "section_aa" in visible_only.views
    assert EXAMPLE["measurement_ledger"](visible_only) == EXAMPLE["measurement_ledger"](hidden)
    assert not visible_only.lint_summary()["by_code"]
