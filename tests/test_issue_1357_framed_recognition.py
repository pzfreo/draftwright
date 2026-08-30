"""#1357: framed recognition is one explicit source→working compiler boundary."""

from __future__ import annotations

from dataclasses import replace

import pytest
from b123d_recognisers import (
    FrameGauge,
    FrameRefusalReason,
    PartFrame,
    RefusedPartFrame,
    build_recognition_result,
)
from build123d import Align, Box, Compound, Cylinder, Pos, Rot, Sphere

from draftwright import build_drawing
from draftwright.audit import diff_builds
from draftwright.model import CylindricalReference
from draftwright.pmi import PmiRecord, reframe_pmi_records
from draftwright.recognition_frame import adapt_recognition

_C = (Align.CENTER, Align.CENTER, Align.CENTER)


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
    """Coordinate-noise-insensitive physical requirement projection of the public IR."""

    rows = []
    for feature in drawing.model().features:
        rows.append(
            (
                feature.kind,
                feature.frame.axis,
                tuple(
                    sorted(
                        (
                            parameter.parameter_id,
                            round(float(parameter.value), 6),
                            parameter.role,
                        )
                        for parameter in feature.parameters()
                    )
                ),
            )
        )
    return sorted(rows)


@pytest.mark.parametrize(
    ("part", "gauge"),
    [
        pytest.param(_full_part(), FrameGauge.FULL, id="full"),
        pytest.param(_orthogonal_part(), FrameGauge.ORTHOGONAL, id="orthogonal"),
        pytest.param(Cylinder(10, 40, align=_C), FrameGauge.AXIAL, id="axial"),
    ],
)
def test_every_successful_gauge_binds_one_local_shape_and_result(part, gauge):
    adapted = adapt_recognition(part)

    assert adapted.status == "framed"
    assert adapted.source_part is part
    assert adapted.part is not part
    assert adapted.frame is not None and adapted.frame.gauge is gauge
    assert adapted.result.cylinders == build_recognition_result(adapted.part).cylinders
    assert adapted.decision == {
        "status": "framed",
        "gauge": gauge.value,
        "refusal_reason": None,
    }


def test_refusal_is_explicit_and_falls_back_once_to_legacy():
    calls = []
    legacy_result = build_recognition_result(Sphere(10))

    def refuse(part, *, rotational=False):
        calls.append(("framed", part, rotational))
        return RefusedPartFrame(FrameRefusalReason.NO_ANALYTIC_DIRECTION)

    def legacy(part, *, rotational=False):
        calls.append(("legacy", part, rotational))
        return legacy_result

    source = Sphere(10)
    adapted = adapt_recognition(source, framed_builder=refuse, legacy_builder=legacy)

    assert [(kind, rotational) for kind, _part, rotational in calls] == [
        ("framed", False),
        ("legacy", False),
    ]
    assert adapted.part is source and adapted.source_part is source
    assert adapted.result is legacy_result and adapted.frame is None
    assert adapted.decision == {
        "status": "legacy_fallback",
        "gauge": None,
        "refusal_reason": "no-analytic-direction",
    }


def test_legacy_route_remains_available_without_frame_inference():
    calls = []
    legacy_result = build_recognition_result(Box(4, 5, 6))

    def framed(*args, **kwargs):  # pragma: no cover - must remain unreachable
        calls.append("framed")
        raise AssertionError("legacy comparison route inferred a frame")

    def legacy(part, *, rotational=False):
        calls.append((part, rotational))
        return legacy_result

    source = Box(4, 5, 6)
    adapted = adapt_recognition(
        source,
        framed=False,
        framed_builder=framed,
        legacy_builder=legacy,
    )

    assert calls == [(source, False)]
    assert adapted.status == "legacy" and adapted.part is source and adapted.frame is None


def test_public_default_retains_legacy_coordinates_and_never_calls_framed_builder(monkeypatch):
    source = Pos(23, -17, 9) * _orthogonal_part()

    def forbidden(*args, **kwargs):  # pragma: no cover - must remain unreachable
        raise AssertionError("default automatic build inferred a part frame")

    monkeypatch.setattr("draftwright.analysis.build_framed_recognition_result", forbidden)
    drawing = build_drawing(source, auto_dims=False)

    assert drawing.recognition_frame_decision == {
        "status": "legacy",
        "gauge": None,
        "refusal_reason": None,
    }
    assert drawing.recognition_frame is None
    assert drawing.part is drawing.working_part


def test_drawing_exposes_distinct_caller_source_and_local_working_part():
    source = Pos(123, -47, 91) * Rot(17, 31, 23) * _full_part()
    drawing = build_drawing(source, auto_dims=False, framed_recognition=True)

    assert drawing.recognition_frame_decision["status"] == "framed"
    assert drawing.recognition_frame is not None
    # The public value is the caller-space BODY. A one-solid Part wrapper is deliberately
    # normalised to its Solid, so topology and placement — not Python wrapper identity — are
    # the contract.
    assert drawing.part.wrapped.IsPartner(source.solids()[0].wrapped)
    public_bbox = drawing.part.bounding_box()
    caller_bbox = source.bounding_box()
    assert tuple(public_bbox.min) == pytest.approx(tuple(caller_bbox.min))
    assert tuple(public_bbox.max) == pytest.approx(tuple(caller_bbox.max))
    assert drawing.part is not drawing.working_part
    working_bbox = drawing.working_part.bounding_box()
    source_bbox = drawing.part.bounding_box()
    assert (tuple(working_bbox.min), tuple(working_bbox.max)) != (
        tuple(source_bbox.min),
        tuple(source_bbox.max),
    )


@pytest.mark.parametrize(
    "part",
    [_full_part(), _orthogonal_part(), _pattern_part(), Cylinder(10, 40, align=_C)],
    ids=("full", "orthogonal", "pattern", "axial"),
)
def test_rigid_motion_preserves_requirements_finished_sheet_and_lint(part):
    baseline = build_drawing(part, framed_recognition=True)
    moved = build_drawing(
        Pos(123, -47, 91) * Rot(17, 31, 23) * part,
        framed_recognition=True,
    )

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


def test_framed_off_axis_pattern_keeps_both_location_requirements_and_clean_lint():
    drawing = build_drawing(_pattern_part(), framed_recognition=True)
    pattern = next(feature for feature in drawing.model().features if feature.kind == "pattern")
    location_ids = {
        key["parameter_id"]
        for name, _annotation in drawing.iter_annotations()
        for key in drawing.measurement_keys(name)
        if key["feature"].startswith("pattern") and key["parameter_id"].startswith("location")
    }

    assert pattern.frame.axis in {"x", "y"}, "fixture stopped exercising framed off-axis output"
    assert location_ids == {"location_pattern.location"}
    assert drawing.lint() == []


def test_compound_ownership_and_face_topology_survive_provider_normalisation():
    left = Pos(-35, 0, 0) * (
        Box(30, 24, 12, align=_C) - Pos(0, 0, 4) * Box(12, 8, 8, align=_C)
    )
    right = Pos(35, 8, 4) * (
        Box(24, 18, 10, align=_C) - Pos(0, 0, 3) * Box(8, 6, 6, align=_C)
    )
    source = Compound([left, right])
    adapted = adapt_recognition(source)

    assert len(source.solids()) == len(adapted.part.solids()) == 2
    assert len(adapted.result.pockets) == 2
    assert len({pocket.body_key for pocket in adapted.result.pockets}) == 2
    # TopLoc normalisation changes placement, not the underlying TShapes: a face-backed
    # association can therefore be reconciled against the exact working solid from #292.
    assert all(
        any(source_face.wrapped.IsPartner(local_face.wrapped) for local_face in adapted.part.faces())
        for source_face in source.faces()
    )


def test_ap242_geometry_and_finite_cylinder_provenance_move_through_one_adapter():
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
        type_code=2,
        value=8.0,
        ref_pts=((10.0, 22.0, 33.0), (14.0, 22.0, 33.0)),
        ref_bbox=(10.0, 20.0, 30.0, 14.0, 25.0, 36.0),
        dominant_axis="X",
        reference_axis="Y",
        cylindrical_refs=(cylinder,),
        source_id="dimension:1",
    )

    local = reframe_pmi_records([record], frame)[0]

    assert local.value == record.value and local.source_id == record.source_id
    assert local.ref_pts == ((2.0, 3.0, 0.0), (2.0, 3.0, 4.0))
    assert local.ref_bbox == (0.0, 0.0, 0.0, 5.0, 6.0, 4.0)
    assert local.dominant_axis == "Z" and local.reference_axis == "X"
    assert len(local.cylindrical_refs) == 1
    local_cylinder = local.cylindrical_refs[0]
    assert local_cylinder.principal_axis == "Z"
    assert local_cylinder.midpoint == pytest.approx((2.0, 3.0, 2.0))
    assert local_cylinder.radius == cylinder.radius and local_cylinder.sense == cylinder.sense


def test_detected_model_can_be_declared_again_in_the_same_working_coordinates(monkeypatch):
    automatic = build_drawing(_full_part(), framed_recognition=True)

    def forbidden(*args, **kwargs):
        raise AssertionError("declared rendering performed recognition")

    monkeypatch.setattr("draftwright.analysis.build_framed_recognition_result", forbidden)
    declared = build_drawing(automatic.working_part, model=automatic.model())

    assert declared.recognition_frame_decision["status"] == "declared"
    assert declared.recognition_frame is None
    assert _requirements(declared) == _requirements(automatic)
    assert declared.annotations() == automatic.annotations()


def test_sheet_declared_surface_stays_in_caller_coordinates(monkeypatch):
    from draftwright import Sheet

    source = Pos(23, -17, 9) * _orthogonal_part()

    def forbidden(*args, **kwargs):  # pragma: no cover - must remain unreachable
        raise AssertionError("Sheet declaration inferred a part frame")

    monkeypatch.setattr("draftwright.analysis.build_framed_recognition_result", forbidden)
    drawing = Sheet.from_part(source).build()

    assert drawing.recognition_frame_decision["status"] == "declared"
    assert drawing.recognition_frame is None
    assert drawing.part is drawing.working_part
    assert tuple(drawing.working_part.bounding_box().min) == pytest.approx(
        tuple(source.bounding_box().min)
    )


def test_feature_key_canonicalises_framed_negative_zero():
    from draftwright.drawing import feature_key

    feature = next(
        f
        for f in build_drawing(
            Cylinder(10, 40, align=_C), framed_recognition=True
        ).model().features
        if f.kind == "envelope"
    )
    negative = replace(feature, frame=replace(feature.frame, origin=(0.0, 0.0, -1e-12)))
    positive = replace(feature, frame=replace(feature.frame, origin=(0.0, 0.0, 1e-12)))

    assert feature_key(negative) == feature_key(positive)
