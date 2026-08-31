"""#1357: opt-in framed recognition stays coordinate-coherent through Drawing."""

from __future__ import annotations

from b123d_recognisers import FrameRefusalReason
from build123d import Align, Box, Cylinder, Pos, Rot

from draftwright import build_drawing
from draftwright.audit import diff_builds
from draftwright.recognition_frame import FramedDetectionRefusal

_C = (Align.CENTER, Align.CENTER, Align.CENTER)


def _part():
    return (
        Box(80, 55, 20, align=_C)
        - Pos(17, 8, 0) * Cylinder(4, 30, align=_C)
        - Pos(-22, -11, 4) * Box(10, 8, 8, align=_C)
    )


def _x_stepped_shaft():
    return Rot(0, 90, 0) * (Cylinder(15, 40) + Pos(0, 0, 35) * Cylinder(8, 30))


def _pattern_part():
    part = Box(80, 80, 10)
    for x, y in ((25, 25), (-25, 25), (25, -25), (-25, -25)):
        part -= Pos(x, y, 0) * Cylinder(3, 20)
    return part


def _requirements(drawing):
    return sorted(
        (
            feature.kind,
            feature.frame.axis,
            tuple(
                sorted(
                    (parameter.parameter_id, round(float(parameter.value), 6), parameter.role)
                    for parameter in feature.parameters()
                )
            ),
        )
        for feature in drawing.model().features
    )


def test_default_route_never_prepares_a_frame(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("default automatic build inferred a part frame")

    monkeypatch.setattr("draftwright.analysis.prepare_framed_detection", forbidden)
    drawing = build_drawing(_part(), auto_dims=False)

    assert drawing.recognition_frame_decision["status"] == "raw"
    assert drawing.recognition_frame is None
    assert drawing.part is drawing.working_part


def test_drawing_exposes_source_and_local_working_part():
    source = Pos(123, -47, 91) * Rot(17, 31, 23) * _part()
    drawing = build_drawing(source, auto_dims=False, framed_recognition=True)

    assert drawing.recognition_frame_decision["status"] == "framed"
    assert drawing.recognition_frame is not None
    assert drawing.part.wrapped.IsPartner(source.solids()[0].wrapped)
    assert drawing.part is not drawing.working_part


def test_provider_refusal_has_one_visible_top_level_raw_fallback(monkeypatch):
    source = _part()
    calls = 0

    def refuse(part):
        nonlocal calls
        calls += 1
        return FramedDetectionRefusal(part, FrameRefusalReason.NO_ANALYTIC_DIRECTION)

    monkeypatch.setattr("draftwright.analysis.prepare_framed_detection", refuse)
    drawing = build_drawing(source, auto_dims=False, framed_recognition=True)

    assert calls == 1
    assert drawing.part is drawing.working_part
    assert drawing.recognition_frame is None
    assert drawing.recognition_frame_decision == {
        "status": "raw_fallback",
        "gauge": None,
        "refusal_reason": "no-analytic-direction",
    }


def test_decision_is_returned_by_copy():
    drawing = build_drawing(_part(), auto_dims=False)
    decision = drawing.recognition_frame_decision
    decision["status"] = "mutated"
    assert drawing.recognition_frame_decision["status"] == "raw"


def test_scale_retries_reuse_one_framed_preparation(monkeypatch):
    from draftwright import analysis

    calls = 0
    original = analysis.prepare_framed_detection

    def counted(part):
        nonlocal calls
        calls += 1
        return original(part)

    monkeypatch.setattr(analysis, "prepare_framed_detection", counted)
    build_drawing(_part(), scale=5, framed_recognition=True)
    assert calls == 1


def test_rigid_motion_preserves_requirements_and_build_diff():
    part = _part()
    baseline = build_drawing(part, framed_recognition=True)
    moved = build_drawing(Pos(123, -47, 91) * Rot(17, 31, 23) * part, framed_recognition=True)

    assert _requirements(baseline) == _requirements(moved)
    assert baseline.annotations() == moved.annotations()
    assert diff_builds(baseline, moved) == {
        "dimensions_lost": {},
        "dimensions_gained": {},
        "dimensions_changed": {},
        "measurements_substituted": {},
        "suppressions_gained": [],
        "suppressions_lost": [],
        "candidate_explanations": {},
    }
    assert [(issue.severity, issue.code) for issue in baseline.lint()] == [
        (issue.severity, issue.code) for issue in moved.lint()
    ]


def test_framed_cross_axis_stepped_shaft_is_measurement_complete():
    raw = build_drawing(_x_stepped_shaft())
    framed = build_drawing(_x_stepped_shaft(), framed_recognition=True)

    assert [item for item in _requirements(framed) if item[0] == "step"] == [
        item for item in _requirements(raw) if item[0] == "step"
    ]
    assert {
        annotation.label
        for name, annotation in framed.iter_annotations()
        if name.startswith(("dim_od", "m_dia", "m_steplen"))
    } == {"ø30", "ø16", "40", "30"}
    assert framed.lint_summary()["by_code"] == {}


def test_framed_off_axis_pattern_keeps_one_absolute_location_requirement():
    drawing = build_drawing(_pattern_part(), framed_recognition=True)
    pattern = next(feature for feature in drawing.model().features if feature.kind == "pattern")
    location_ids = {
        key["parameter_id"]
        for name, _annotation in drawing.iter_annotations()
        for key in drawing.measurement_keys(name)
        if key["feature"].startswith("pattern") and key["parameter_id"].startswith("location")
    }

    assert pattern.frame.axis in {"x", "y"}
    assert location_ids == {"location_pattern.location"}
    assert drawing.lint() == []
