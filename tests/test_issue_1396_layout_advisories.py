"""Layout degradations are available without parsing Python warnings or logs."""

from dataclasses import replace

import pytest
from build123d import Box

from draftwright import Sheet, build_drawing
from draftwright.analysis import _analyse
from draftwright.builder import _assemble, _repack_to_fixed_point
from draftwright.compose import choose_scale


def _codes(drawing):
    return {issue.code for issue in drawing.lint(physical=False)}


def test_legibility_warning_reaches_declared_and_detected_lint():
    part = Box(680, 860, 80)
    for declared in (False, True):
        with pytest.warns(UserWarning, match="legibility floor"):
            if declared:
                drawing = Sheet(part, scale=0.1).authored_dimensions().build()
            else:
                drawing = build_drawing(part, scale=0.1, scale_policy="permissive")
        assert drawing.scale == 0.1
        assert (
            sum(i.code == "legibility_floor_breached" for i in drawing.lint(physical=False)) == 1
        )
        assert (
            "legibility_floor_breached"
            in drawing.lint_summary()["quality"]["legibility"]["by_code"]
        )


def test_legible_explicit_scale_has_no_legibility_finding():
    drawing = Sheet(Box(20, 20, 20), scale=1).authored_dimensions().build()
    assert "legibility_floor_breached" not in _codes(drawing)


@pytest.mark.parametrize("scale,page", [(100, "A4"), (None, (1, 1))])
def test_infeasible_scale_selection_records_the_warning(scale, page, caplog):
    advisories = []
    choose_scale(100, 100, 100, scale=scale, page=page, advisories=advisories)
    assert "fit" in caplog.text
    assert [code for code, _ in advisories] == ["page_fit_uncertain"]


def test_seed_layout_advisory_reaches_assembled_drawing():
    a = _analyse(Box(20, 20, 20), title="", number="", tolerance="", drawn_by="", out="")
    a = replace(a, layout_advisories=(("page_fit_uncertain", "fixture seed fit failure"),))
    drawing = _assemble(a, "", None, None, auto_dims=False)
    assert "page_fit_uncertain" in _codes(drawing)


@pytest.mark.parametrize("limit", [0, 2])
def test_unresolved_repack_is_machine_visible(monkeypatch, limit):
    import draftwright.builder as builder

    a = _analyse(Box(20, 20, 20), title="", number="", tolerance="", drawn_by="", out="")
    drawing = _assemble(a, "", None, None, auto_dims=False)
    monkeypatch.setattr(builder, "_REPACK_MAX_ITER", limit)
    monkeypatch.setattr(builder, "_needs_repack", lambda *args: True)
    monkeypatch.setattr(builder, "_repack", lambda *args, **kwargs: None)
    _repack_to_fixed_point(a, drawing, "", None, None)
    assert "layout_repack_stalled" in _codes(drawing)


def test_unknown_layout_advisory_is_refused():
    from draftwright.builder import _layout_advisory

    with pytest.raises(ValueError, match="unknown layout advisory"):
        _layout_advisory("misspelled_finding", "must not silently evade classification")


def test_computed_scale_is_reported_by_scale_selection():
    advisories = []
    scale, *_ = choose_scale(1e7, 1e7, 1e7, advisories=advisories)
    assert 0 < scale < 0.0001
    assert [code for code, _ in advisories] == ["scale_fallback_applied"]


def test_measured_fit_retracts_seed_uncertainty():
    from draftwright.builder import _repack

    a = _analyse(Box(20, 20, 20), title="", number="", tolerance="", drawn_by="", out="")
    a = replace(a, layout_advisories=(("page_fit_uncertain", "fixture seed fit failure"),))
    drawing = _assemble(a, "", None, None, auto_dims=False)
    assert "page_fit_uncertain" in _codes(drawing)
    assert _repack(a, drawing, "", None, None) is None
    assert "page_fit_uncertain" not in _codes(drawing)
