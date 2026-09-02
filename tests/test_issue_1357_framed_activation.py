"""#1357: opt-in framed recognition stays coordinate-coherent through Drawing."""

from __future__ import annotations

import pytest
from b123d_recognisers import FrameRefusalReason
from build123d import Align, Box, Compound, Cylinder, Pos, Rot
from conftest import recognition_consumer_calls

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


def _parallel_stepped_shafts():
    shaft = Cylinder(15, 20) + Pos(0, 0, 20) * Cylinder(10, 20)
    return Compound(children=[Pos(-50, 0, 0) * shaft, Pos(50, 0, 0) * shaft])


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
    with recognition_consumer_calls() as recognition_calls:
        drawing = build_drawing(source, auto_dims=False, framed_recognition=True)

    assert calls == 1
    assert recognition_calls == {"build_recognition_evidence": 1}
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


@pytest.mark.parametrize("framed_recognition", (False, True))
def test_plural_profiles_survive_raw_and_framed_scale_retries(monkeypatch, framed_recognition):
    from draftwright import analysis

    calls = 0
    original = analysis.prepare_framed_detection

    def counted(part):
        nonlocal calls
        calls += 1
        return original(part)

    monkeypatch.setattr(analysis, "prepare_framed_detection", counted)
    drawing = build_drawing(
        _parallel_stepped_shafts(),
        scale=10,
        framed_recognition=framed_recognition,
    )

    assert calls == int(framed_recognition)
    assert drawing.recognition_frame_decision["status"] == (
        "framed" if framed_recognition else "raw"
    )
    assert drawing.scale_decision["status"] == "fallback"
    assert len(drawing.scale_decision["attempted_scales"]) > 1
    steps = [feature for feature in drawing.model().features if feature.kind == "step"]
    assert len(steps) == 4
    profiles = {feature.profile for feature in steps}
    assert len(profiles) == 2
    assert len([name for name in drawing.annotations() if name.startswith("m_steplen")]) == 4
    assert not [issue for issue in drawing.lint() if issue.code == "axial_length_missing"]

    target_profile = next(iter(profiles))
    target_names = {
        name
        for name in drawing.annotations()
        if name.startswith("m_steplen")
        and any(
            identity.feature.profile == target_profile
            for identity in drawing.registry.measurement_of(name)
        )
    }
    assert len(target_names) == 2
    for name in target_names:
        drawing.remove(name)
    issues = [issue for issue in drawing.lint() if issue.code == "axial_length_missing"]
    assert len(issues) == 1
    assert "only 0 step length(s) dimensioned" in issues[0].message


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
