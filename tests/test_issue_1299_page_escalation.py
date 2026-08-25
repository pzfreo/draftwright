"""#1299 — automatic axial recovery may escalate to a larger standard sheet."""

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from build123d import Align, Cylinder, Pos, Rotation

import draftwright.builder as builder
from draftwright import build_drawing
from draftwright.linting import LintIssue
from draftwright.model.ir import Frame, StepFeature
from draftwright.model.planner import DimensionId

GRM03 = Path(__file__).parent / "fixtures" / "grm03_thumbwheel_drive_screw_ap242_pmi.step"
GRM03_SHA256 = "4b6462b9cc9f0d419250933bd77fb305f9cfebb7ec2b3f377008732876010a21"


def _five_step_grm_profile():
    align = (Align.CENTER, Align.CENTER, Align.MIN)
    segments = [
        Pos(0, 0, station) * Cylinder(radius, length, align=align)
        for station, radius, length in (
            (-3.2, 2.0, 3.2),
            (0.0, 3.0, 0.5),
            (0.5, 5.0, 2.0),
            (2.5, 2.5, 3.0),
            (5.5, 1.5, 20.0),
        )
    ]
    shaft = segments[0]
    for segment in segments[1:]:
        shaft += segment
    shaft -= Pos(0, 0, -3.2) * Cylinder(0.8, 8.0, align=align)
    return Rotation(0, 90, 0) * shaft


def test_incomplete_same_page_recovers_at_a_larger_scale_before_spending_the_sheet():
    # #1338: this part used to reach A3 without its ISO. The recovery it needed was one
    # scale step on the sheet already selected, which covers every station AND keeps the
    # ISO; the sheet and the optional view are only spent when that fails (see the
    # bounded-page-recovery tests below, and GRM-03 with pmi="annotate" in #1298).
    drawing = build_drawing(_five_step_grm_profile(), pmi="off")

    assert (drawing.page_w, drawing.page_h) == (297.0, 210.0)
    assert drawing.scale == 5.0
    assert "iso" in drawing.views
    assert drawing.scale_decision["status"] == "automatic_replanned"
    assert [
        (
            attempt["page"],
            attempt["status"],
            attempt["reason"],
            attempt.get("rejection"),
        )
        for attempt in drawing.scale_decision["attempts"]
    ] == [
        ((297.0, 210.0), "axial_coverage_incomplete", "remove_optional_iso", None),
        ((297.0, 210.0), "complete", "scale_escalation_on_selected_page", None),
    ]
    assert drawing.scale_decision["attempted_scales"] == (2.0, 5.0)
    assert {
        drawing.get_annotation(name).label
        for name in drawing.annotations()
        if name.startswith("m_steplen")
    } == {"3.2", "0.5", "2", "3", "20"}
    assert not [
        issue
        for issue in drawing.lint()
        if issue.severity == "error"
        or issue.code
        in {
            "axial_length_missing",
            "annotation_overlap",
            "annotation_out_of_bounds",
            "view_overlap",
        }
        or issue.code.endswith("_dropped")
    ]


def test_no_iso_proposal_on_different_page_reselects_scale_for_original_page(monkeypatch):
    """A proposal's scale belongs to its own page, not to the original sheet."""

    calls = []

    class FakeDrawing:
        def __init__(self, *, page, scale, include_iso):
            self.page_w, self.page_h = page
            self.scale = scale
            self.views = {"front": object()}
            if include_iso:
                self.views["iso"] = object()
            self.solve_trace = None

        def model(self):
            return SimpleNamespace(authored_dimensions=None)

        def lint(self, *, physical=False):
            return ()

    def fake_one_pass(
        _step_file,
        *,
        scale,
        page,
        _include_iso,
        _analysis_sink,
        **_kwargs,
    ):
        calls.append({"scale": scale, "page": page, "include_iso": _include_iso})
        _analysis_sink(
            SimpleNamespace(
                arrangement=builder.ARRANGEMENTS[0],
                part=object(),
                prof=object(),
            )
        )
        if _include_iso:
            return FakeDrawing(page=(297.0, 210.0), scale=2.0, include_iso=True)
        if page is None:
            return FakeDrawing(page=(420.0, 297.0), scale=5.0, include_iso=False)
        assert page == (297.0, 210.0)
        return FakeDrawing(page=page, scale=2.0, include_iso=False)

    monkeypatch.setattr(builder, "_build_drawing_once", fake_one_pass)
    monkeypatch.setattr(
        builder,
        "lint_axial_coverage",
        lambda _part, drawing, *, prof: ("gap",) if "iso" in drawing.views else (),
    )

    drawing = builder.build_drawing(object())

    fixed_page_calls = [
        call for call in calls if not call["include_iso"] and call["page"] == (297.0, 210.0)
    ]
    assert fixed_page_calls == [{"scale": None, "page": (297.0, 210.0), "include_iso": False}]
    assert (drawing.page_w, drawing.page_h, drawing.scale) == (297.0, 210.0, 2.0)
    assert [
        (attempt["status"], attempt["reason"], attempt.get("rejection"))
        for attempt in drawing.scale_decision["attempts"]
    ] == [
        ("axial_coverage_incomplete", "remove_optional_iso", None),
        ("rejected", "scale_escalation_on_selected_page", "axial_coverage_incomplete"),
        ("rejected", "scale_escalation_on_selected_page", "axial_coverage_incomplete"),
        ("scale_proposal", "remove_optional_iso", None),
        ("complete", "remove_optional_iso", None),
    ]


def test_required_drop_without_axial_gap_uses_the_same_bounded_page_recovery(monkeypatch):
    """Complete shoulders do not make a sheet complete when a callout was lost."""

    dropped = LintIssue(
        severity="warning",
        code="callout_dropped",
        message="required typed-PMI callout has no route",
        source_ids=("manufacturing_requirement:#2004",),
        outcome_stage="placement",
    )

    class FakeDrawing:
        def __init__(self, *, page, include_iso, issues=()):
            self.page_w, self.page_h = page
            self.scale = 2.0
            self.views = {"front": object()}
            if include_iso:
                self.views["iso"] = object()
            self.solve_trace = None
            self._issues = tuple(issues)

        def model(self):
            return SimpleNamespace(authored_dimensions=None)

        def lint(self, *, physical=False):
            return self._issues

    def fake_one_pass(
        _step_file,
        *,
        page,
        _include_iso,
        _analysis_sink,
        **_kwargs,
    ):
        _analysis_sink(
            SimpleNamespace(
                arrangement=builder.ARRANGEMENTS[0],
                part=object(),
                prof=object(),
            )
        )
        dimensions = {
            None: (297.0, 210.0),
            (297.0, 210.0): (297.0, 210.0),
            "A3": (420.0, 297.0),
        }[page]
        issues = () if page == "A3" else (dropped,)
        return FakeDrawing(page=dimensions, include_iso=_include_iso, issues=issues)

    monkeypatch.setattr(builder, "_build_drawing_once", fake_one_pass)
    monkeypatch.setattr(builder, "lint_axial_coverage", lambda *_args, **_kwargs: ())

    drawing = builder.build_drawing(object())

    assert (drawing.page_w, drawing.page_h) == (420.0, 297.0)
    assert "iso" not in drawing.views
    assert [
        (
            attempt["page"],
            attempt["status"],
            attempt["reason"],
            attempt.get("rejection"),
        )
        for attempt in drawing.scale_decision["attempts"]
    ] == [
        ((297.0, 210.0), "required_outcome_dropped", "remove_optional_iso", None),
        (
            (297.0, 210.0),
            "rejected",
            "scale_escalation_on_selected_page",
            "required_outcome_dropped",
        ),
        (
            (297.0, 210.0),
            "rejected",
            "scale_escalation_on_selected_page",
            "required_outcome_dropped",
        ),
        ((297.0, 210.0), "rejected", "remove_optional_iso", "required_outcome_dropped"),
        ((420.0, 297.0), "complete", "page_escalation_after_optional_iso", None),
    ]


def test_unrelated_drop_on_a_typed_owner_does_not_borrow_its_source(monkeypatch):
    """A step-length loss is not evidence that its surviving thread callout was lost."""

    from collections import namedtuple

    TypedAspect = namedtuple("TypedAspect", ("source_ids",))
    threaded_step = StepFeature(
        Frame((5.0, 0.0, 0.0), "x"),
        10.0,
        3.0,
        ((0.0, 0.0, 0.0), (10.0, 0.0, 0.0)),
        thread=TypedAspect(("manufacturing_requirement:#thread",)),
    )
    dropped = LintIssue(
        severity="warning",
        code="step_dim_dropped",
        message="axial step dimension has no route",
        measurement_ids=(DimensionId(threaded_step, "step.length"),),
        outcome_stage="placement",
    )
    calls = []

    class FakeDrawing:
        page_w, page_h, scale = 297.0, 210.0, 2.0
        views = {"front": object(), "iso": object()}
        solve_trace = None

        def __init__(self):
            from draftwright.registry import AnnotationRegistry

            self.registry = AnnotationRegistry()

        def model(self):
            return SimpleNamespace(authored_dimensions=None)

        def lint(self, *, physical=False):
            return (dropped,)

    def fake_one_pass(_step_file, *, _include_iso, _analysis_sink, **_kwargs):
        calls.append(_include_iso)
        _analysis_sink(
            SimpleNamespace(
                arrangement=builder.ARRANGEMENTS[0],
                part=object(),
                prof=object(),
            )
        )
        return FakeDrawing()

    monkeypatch.setattr(builder, "_build_drawing_once", fake_one_pass)
    monkeypatch.setattr(builder, "lint_axial_coverage", lambda *_args, **_kwargs: ())

    with pytest.warns(builder.ScaleCompletenessWarning):
        drawing = builder.build_drawing(object())

    assert calls == [True]
    assert drawing.scale_decision["status"] == "incomplete"
    assert drawing.scale_decision["blockers"][0]["source_ids"] == ()


def test_complete_detail_drawing_stays_on_its_original_page(monkeypatch):
    """#1299 does not broaden recovery beyond incomplete axial/source-owned plans."""

    class FakeDrawing:
        def __init__(self, *, page, scale, include_iso, detail):
            self.page_w, self.page_h = page
            self.scale = scale
            self.views = {"front": object()}
            if include_iso:
                self.views["iso"] = object()
            if detail:
                self.views["detail_a"] = object()
            self.solve_trace = None

        def model(self):
            return SimpleNamespace(authored_dimensions=None)

        def lint(self, *, physical=False):
            return ()

    def fake_one_pass(
        _step_file,
        *,
        scale,
        page,
        _include_iso,
        _analysis_sink,
        **_kwargs,
    ):
        _analysis_sink(
            SimpleNamespace(
                arrangement=builder.ARRANGEMENTS[0],
                part=object(),
                prof=object(),
            )
        )
        if page == "A3":
            return FakeDrawing(
                page=(420.0, 297.0),
                scale=5.0,
                include_iso=_include_iso,
                detail=False,
            )
        assert page in {None, (297.0, 210.0)}
        return FakeDrawing(
            page=(297.0, 210.0),
            scale=2.0 if scale is None else scale,
            include_iso=_include_iso,
            detail=True,
        )

    monkeypatch.setattr(builder, "_build_drawing_once", fake_one_pass)
    monkeypatch.setattr(builder, "lint_axial_coverage", lambda *_args, **_kwargs: ())

    drawing = builder.build_drawing(object())

    assert (drawing.page_w, drawing.page_h, drawing.scale) == (297.0, 210.0, 2.0)
    assert "detail_a" in drawing.views
    assert [
        (
            attempt["page"],
            attempt["scale"],
            attempt["status"],
            attempt["reason"],
            attempt.get("rejection"),
        )
        for attempt in drawing.scale_decision["attempts"]
    ] == [
        (
            (297.0, 210.0),
            2.0,
            "detail_reservation_conservative",
            "measured_upscale",
            None,
        ),
        ((297.0, 210.0), 5.0, "rejected", "measured_upscale", "recovery_detail_retained"),
        ((297.0, 210.0), 10.0, "rejected", "measured_upscale", "recovery_detail_retained"),
    ]


def test_expected_larger_page_build_failure_is_recorded_before_next_page(monkeypatch):
    class FakeDrawing:
        def __init__(self, *, page, include_iso):
            self.page_w, self.page_h = page
            self.scale = 2.0
            self.views = {"front": object()}
            if include_iso:
                self.views["iso"] = object()
            self.solve_trace = None

        def model(self):
            return SimpleNamespace(authored_dimensions=None)

        def lint(self, *, physical=False):
            return ()

    def fake_one_pass(
        _step_file,
        *,
        page,
        _include_iso,
        _analysis_sink,
        **_kwargs,
    ):
        _analysis_sink(
            SimpleNamespace(
                arrangement=builder.ARRANGEMENTS[0],
                part=object(),
                prof=object(),
            )
        )
        if page == "A3":
            raise ValueError("drawing geometry degenerates on speculative A3")
        dimensions = {
            None: (297.0, 210.0),
            (297.0, 210.0): (297.0, 210.0),
            "A2": (594.0, 420.0),
        }[page]
        return FakeDrawing(page=dimensions, include_iso=_include_iso)

    monkeypatch.setattr(builder, "_build_drawing_once", fake_one_pass)
    monkeypatch.setattr(
        builder,
        "lint_axial_coverage",
        lambda _part, drawing, *, prof: ("gap",) if drawing.page_w == 297.0 else (),
    )

    drawing = builder.build_drawing(object())

    assert (drawing.page_w, drawing.page_h) == (594.0, 420.0)
    assert [
        (
            attempt["page"],
            attempt["status"],
            attempt["reason"],
            attempt.get("error"),
        )
        for attempt in drawing.scale_decision["attempts"]
    ] == [
        ((297.0, 210.0), "axial_coverage_incomplete", "remove_optional_iso", None),
        ((297.0, 210.0), "rejected", "scale_escalation_on_selected_page", None),
        ((297.0, 210.0), "rejected", "scale_escalation_on_selected_page", None),
        ((297.0, 210.0), "rejected", "remove_optional_iso", None),
        (
            (420.0, 297.0),
            "error",
            "page_escalation_after_optional_iso",
            "drawing geometry degenerates on speculative A3",
        ),
        ((594.0, 420.0), "complete", "page_escalation_after_optional_iso", None),
    ]

    def unexpected_one_pass(*args, page, **kwargs):
        if page == "A3":
            raise ValueError("invalid declared model")
        return fake_one_pass(*args, page=page, **kwargs)

    monkeypatch.setattr(builder, "_build_drawing_once", unexpected_one_pass)
    with pytest.raises(ValueError, match="invalid declared model"):
        builder.build_drawing(object())


def test_explicit_a4_remains_fixed_instead_of_escalating():
    drawing = build_drawing(_five_step_grm_profile(), page="A4", pmi="off")

    assert (drawing.page_w, drawing.page_h) == (297.0, 210.0)
    assert all(attempt["page"] == (297.0, 210.0) for attempt in drawing.scale_decision["attempts"])
    # #1338: pinning the sheet pins the SHEET. The scale is still automatic, so the same
    # larger-scale recovery applies and the pinned A4 now carries every station instead of
    # returning the incomplete 2:1 layout.
    assert drawing.scale_decision["status"] == "automatic_replanned"
    assert drawing.scale == 5.0
    assert not [issue for issue in drawing.lint() if issue.code == "axial_length_missing"]


def test_exact_grm03_recovers_all_axial_stations_on_a4_with_pmi_off():
    assert hashlib.sha256(GRM03.read_bytes()).hexdigest() == GRM03_SHA256
    drawing = build_drawing(GRM03, pmi="off")

    # #1338: was A3 without the ISO; the same stations are covered on A4 with it.
    assert (drawing.page_w, drawing.page_h) == (297.0, 210.0)
    assert drawing.scale == 5.0
    assert "iso" in drawing.views
    assert {
        drawing.get_annotation(name).label
        for name in drawing.annotations()
        if name.startswith("m_steplen")
    } == {"3.2", "0.5", "2", "3", "20"}
    assert not [
        issue
        for issue in drawing.lint()
        if issue.code == "axial_length_missing" or issue.code.endswith("_dropped")
    ]
