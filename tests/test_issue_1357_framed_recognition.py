"""#1357: framed recognition is one explicit source-to-working boundary."""

from __future__ import annotations

import pytest
from b123d_recognisers import FrameGauge, FrameRefusalReason, PartFrame, RefusedPartFrame
from build123d import Align, Box, Compound, Cylinder, Pos, Rot, Sphere

from draftwright import build_drawing
from draftwright.audit import diff_builds
from draftwright.model import CylindricalReference
from draftwright.pmi import PmiRecord, reframe_pmi_records
from draftwright.recognition_frame import Classification, adapt_recognition

_C = (Align.CENTER, Align.CENTER, Align.CENTER)


def _classify(part, cylinders):
    del part
    return Classification(None, bool(cylinders[0]))


def _orthogonal_part():
    return Box(60, 40, 20, align=_C) - Pos(10, 5, 0) * Cylinder(4, 30, align=_C)


def _full_part():
    return (
        Box(80, 55, 20, align=_C)
        - Pos(17, 8, 0) * Cylinder(4, 30, align=_C)
        - Pos(-22, -11, 4) * Box(10, 8, 8, align=_C)
    )


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


@pytest.mark.parametrize(
    ("part", "gauge"),
    [
        pytest.param(_full_part(), FrameGauge.FULL, id="full"),
        pytest.param(_orthogonal_part(), FrameGauge.ORTHOGONAL, id="orthogonal"),
        pytest.param(Cylinder(10, 40, align=_C), FrameGauge.AXIAL, id="axial"),
    ],
)
def test_every_successful_gauge_binds_one_local_shape_and_result(part, gauge):
    adapted = adapt_recognition(part, framed=True, classify=_classify)

    assert adapted.status == "framed"
    assert adapted.source_part is part
    assert adapted.working_part is not part
    assert adapted.frame is not None and adapted.frame.gauge is gauge
    assert adapted.result.cylinders
    assert adapted.decision == {
        "status": "framed",
        "gauge": gauge.value,
        "refusal_reason": None,
    }


def test_refusal_is_explicit_and_falls_back_once():
    calls = []
    source = Sphere(10)

    def refuse(part):
        calls.append(("prepare", part))
        return RefusedPartFrame(FrameRefusalReason.NO_ANALYTIC_DIRECTION)

    def raw(part, *, cylinders, rotational):
        calls.append(("raw", part, cylinders, rotational))
        from b123d_recognisers import build_raw_recognition_result

        return build_raw_recognition_result(part, cylinders=cylinders, rotational=rotational)

    adapted = adapt_recognition(
        source, framed=True, classify=_classify, prepare=refuse, raw_builder=raw
    )

    assert [call[0] for call in calls] == ["prepare", "raw"]
    assert adapted.working_part is source and adapted.frame is None
    assert adapted.decision == {
        "status": "raw_fallback",
        "gauge": None,
        "refusal_reason": "no-analytic-direction",
    }


def test_default_route_never_prepares_a_frame(monkeypatch):
    source = Pos(23, -17, 9) * _orthogonal_part()

    def forbidden(*args, **kwargs):
        raise AssertionError("default automatic build inferred a part frame")

    monkeypatch.setattr("draftwright.recognition_frame.prepare_framed_part", forbidden)
    drawing = build_drawing(source, auto_dims=False)

    assert drawing.recognition_frame_decision["status"] == "raw"
    assert drawing.recognition_frame is None
    assert drawing.part is drawing.working_part

    decision = drawing.recognition_frame_decision
    decision["status"] = "mutated"
    assert drawing.recognition_frame_decision["status"] == "raw"


def test_drawing_exposes_source_and_local_working_part():
    source = Pos(123, -47, 91) * Rot(17, 31, 23) * _full_part()
    drawing = build_drawing(source, auto_dims=False, framed_recognition=True)

    assert drawing.recognition_frame_decision["status"] == "framed"
    assert drawing.recognition_frame is not None
    assert drawing.part.wrapped.IsPartner(source.solids()[0].wrapped)
    assert tuple(drawing.part.bounding_box().min) == pytest.approx(
        tuple(source.bounding_box().min)
    )
    assert drawing.part is not drawing.working_part


def test_compound_levels_and_risers_remain_body_local():
    body = Box(20, 20, 10, align=_C) + Pos(0, 0, 8) * Box(10, 10, 6, align=_C)
    source = Compound([Pos(-25, 0, 0) * body, Pos(25, 0, 0) * body])
    adapted = adapt_recognition(source, framed=True, classify=_classify)

    assert len(adapted.working_part.solids()) == 2
    assert len(adapted.result.step_levels) == 4
    assert len(adapted.result.risers) == 2
    assert all(len(riser.body_levels) == 2 for riser in adapted.result.risers)
    spans = {tuple(level.y_span) for level in adapted.result.step_levels}
    assert len(spans) == 2
    assert all(not (lo < 0 < hi) for lo, hi in spans), "a level crossed the air gap"


def test_compound_turned_profiles_keep_distinct_axis_lines():
    shaft = Cylinder(10, 20, align=_C) + Pos(0, 0, 15) * Cylinder(7, 10, align=_C)
    source = Compound([Pos(-30, 0, 0) * shaft, Pos(30, 0, 0) * shaft])
    adapted = adapt_recognition(source, framed=True, classify=_classify)

    assert len(adapted.result.turned_profiles) == 2
    assert len({profile.profile.axis_origin for profile in adapted.result.turned_profiles}) == 2
    # Draftwright's current drawing-wide profile projection must decline this compound rather
    # than joining its two shafts across air; family-specific inventories remain available.
    drawing = build_drawing(source, framed_recognition=True)
    assert not [feature for feature in drawing.model().features if feature.kind == "step"]


def test_arbitrary_frame_preserves_pmi_direction_evidence():
    frame = PartFrame(
        origin=(10.0, 20.0, 30.0),
        x=(0.0, 1.0, 0.0),
        y=(0.0, 0.0, 1.0),
        z=(1.0, 0.0, 0.0),
        gauge=FrameGauge.FULL,
    )
    cylinder = CylindricalReference.canonical(
        axis_point=(10.0, 22.0, 33.0),
        axis_direction=(1.0, 0.0, 0.0),
        radius=4.0,
        local_interval=(0.0, 4.0),
        sense="internal",
    )
    record = PmiRecord(
        kind="diameter",
        type_code=15,
        value=8.0,
        ref_pts=((10.0, 22.0, 33.0), (14.0, 22.0, 33.0)),
        ref_bbox=(10.0, 20.0, 30.0, 14.0, 25.0, 36.0),
        dominant_axis="?",
        reference_axis="?",
        reference_direction=(0.0, 1.0, 0.0),
        cylindrical_refs=(cylinder,),
        source_id="dimension:1",
    )

    local = reframe_pmi_records([record], frame)[0]

    assert local.ref_pts == ((2.0, 3.0, 0.0), (2.0, 3.0, 4.0))
    assert local.ref_bbox == (0.0, 0.0, 0.0, 5.0, 6.0, 4.0)
    assert local.dominant_axis == "Z"
    assert local.reference_axis == "X"
    assert local.reference_direction == (1.0, 0.0, 0.0)
    assert local.cylindrical_refs[0].principal_axis == "Z"
    assert local.cylindrical_refs[0].midpoint == pytest.approx((2.0, 3.0, 2.0))


def test_framed_off_axis_pattern_keeps_its_absolute_location():
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


def test_declared_build_stays_in_caller_coordinates_and_skips_recognition(monkeypatch):
    automatic = build_drawing(_full_part(), framed_recognition=True)

    def forbidden(*args, **kwargs):
        raise AssertionError("declared rendering performed recognition")

    monkeypatch.setattr("draftwright.recognition_frame.prepare_framed_part", forbidden)
    declared = build_drawing(automatic.working_part, model=automatic.model())

    assert declared.recognition_frame_decision["status"] == "declared"
    assert declared.recognition_frame is None
    assert declared.part is declared.working_part
    assert _requirements(declared) == _requirements(automatic)
    assert declared.annotations() == automatic.annotations()


def test_scale_convergence_reuses_one_framed_preparation(monkeypatch):
    from draftwright import recognition_frame as boundary

    calls = 0
    original = boundary.prepare_framed_part

    def counted(part):
        nonlocal calls
        calls += 1
        return original(part)

    monkeypatch.setattr(boundary, "prepare_framed_part", counted)
    build_drawing(_full_part(), framed_recognition=True)

    assert calls == 1


@pytest.mark.parametrize(
    "part",
    [_full_part(), _orthogonal_part(), _pattern_part(), Cylinder(10, 40, align=_C)],
    ids=("full", "orthogonal", "pattern", "axial"),
)
def test_rigid_motion_preserves_requirements_sheet_and_lint(part):
    baseline = build_drawing(part, framed_recognition=True)
    moved = build_drawing(Pos(123, -47, 91) * Rot(17, 31, 23) * part, framed_recognition=True)

    assert baseline.recognition_frame_decision == moved.recognition_frame_decision
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
