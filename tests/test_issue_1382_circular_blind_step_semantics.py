"""#1382 — circular-blind-step records have one complete, auditable consumer path."""

from __future__ import annotations

from dataclasses import replace
from sys import float_info
from types import SimpleNamespace

import pytest
from b123d_recognisers import (
    analyse_cylinders,
    build_raw_recognition_result,
    recognise_fillets,
    recognise_turned_steps,
)
from build123d import Axis, Box, Compound, Cylinder, Pos, Rot, fillet
from conftest import counting_calls

from draftwright import Sheet, build_drawing
from draftwright._geometry import _segment_clips_box
from draftwright.annotations import from_model
from draftwright.annotations._common import PlacementContext
from draftwright.linting.circular_blind_step_coverage import (
    circular_blind_step_requirement_outcomes,
    lint_circular_blind_step_coverage,
)
from draftwright.linting.issues import LintIssue
from draftwright.model import (
    CircularBlindStepFeature,
    Frame,
    build_part_model,
    circular_blind_step,
)
from draftwright.model.compiled import DimensionId, compile_dimensions
from draftwright.registry import AnnotationRegistry
from draftwright.sheet_emit import _feature_line


class _DeceptiveFloat(float):
    """A numeric subclass whose conversion hides its stored value."""

    def __float__(self) -> float:
        return 4.0


def _part():
    return Box(40, 30, 20) - Pos(7.5, 15, 10) * Rot(0, 90, 0) * Cylinder(4, 25)


def _feature(drawing) -> CircularBlindStepFeature:
    return next(
        feature for feature in drawing.model().features if feature.kind == "circular_blind_step"
    )


def _annotation(drawing):
    name = next(
        name for name in drawing.annotations() if name.startswith("m_circular_blind_step_")
    )
    return name, drawing.registry.named(name)


def _declare(source) -> CircularBlindStepFeature:
    return circular_blind_step(
        axis=source.axis,
        radius=source.radius,
        length=source.length,
        centreline=source.centreline,
        section=source.section,
    )


def _unchecked_feature(source) -> CircularBlindStepFeature:
    """Forge an invalid IR-shaped value to prove the ledger validates independently."""
    feature = object.__new__(CircularBlindStepFeature)
    try:
        anchor = CircularBlindStepFeature.anchor_for(
            source.axis, source.radius, source.centreline, source.section
        )
    except (IndexError, TypeError, ValueError, ZeroDivisionError):
        anchor = (0.0, 0.0, 0.0)
    for name, value in (
        ("frame", Frame(anchor, source.axis)),
        ("axis", source.axis),
        ("radius", source.radius),
        ("length", source.length),
        ("centreline", source.centreline),
        ("section", source.section),
    ):
        object.__setattr__(feature, name, value)
    return feature


def _assert_owned_callout(drawing, feature, name=None):
    if name is None:
        name, annotation = _annotation(drawing)
    else:
        annotation = drawing.registry.named(name)
    identity = drawing.registry.identity_of(name)
    view = {"x": "side", "y": "front", "z": "plan"}[feature.axis]

    assert identity["view"] == view
    assert {measurement.parameter for measurement in identity["measurement"]} == {
        "circular_step_radius.radius",
        "circular_step_depth.length",
    }
    assert all(measurement.feature is feature for measurement in identity["measurement"])
    assert annotation.tip == pytest.approx(drawing.at(view, *feature.arc_anchor)[:2])
    assert not [issue for issue in drawing.lint() if "circular_blind_step" in issue.code]
    return annotation


def test_aggregate_record_lowers_exactly_to_two_planned_requirements() -> None:
    drawing = build_drawing(_part())
    recognition = drawing.recognition()
    assert recognition is not None
    assert len(recognition.circular_blind_steps) == 1
    source = recognition.circular_blind_steps[0]
    feature = _feature(drawing)

    assert (feature.axis, feature.radius, feature.length) == (
        source.axis,
        source.radius,
        source.length,
    )
    assert (feature.centreline, feature.section) == (source.centreline, source.section)
    assert feature.frame == Frame(feature.arc_anchor, source.axis)
    assert [parameter.parameter_id for parameter in feature.parameters()] == [
        "circular_step_radius.radius",
        "circular_step_depth.length",
    ]
    assert feature.parameters()[1].span == source.centreline

    group = next(iter(compile_dimensions(drawing.model()).of_kind("circular_blind_step")))
    assert group.view == "side"
    assert [dimension.id.parameter for dimension in group.dims] == [
        "circular_step_radius.radius",
        "circular_step_depth.length",
    ]


def test_detected_build_consumes_aggregate_inventory_without_rescanning() -> None:
    import draftwright.model.detect as detect

    assert not hasattr(detect, "recognise_circular_blind_steps")
    assert _feature(build_drawing(_part())).radius == 4.0


def test_partial_models_use_the_aggregate_single_owner_decision() -> None:
    recognition = build_raw_recognition_result(_part())
    assert len(recognition.circular_blind_steps) == 1
    assert recognition.fillets == ()

    cases = (
        {},
        {"holes": ()},
        {"plates": ()},
        {"prof": None},
        {"step_zs": []},
        {"face_levels": []},
        {"circular_blind_steps": recognition.circular_blind_steps},
    )
    for supplied in cases:
        model = build_part_model(_part(), **supplied)
        kinds = [feature.kind for feature in model.features]
        assert kinds.count("circular_blind_step") == 1, supplied
        assert "fillet" not in kinds, supplied

        drawing = build_drawing(_part(), model=model)
        labels = [
            getattr(drawing.registry.named(name), "label", None) for name in drawing.annotations()
        ]
        assert labels.count("R4 × 25 DEEP") == 1, supplied
        assert "R4" not in labels, supplied
        assert drawing.lint() == [], supplied


def test_partial_models_reject_one_sided_ownership_overrides() -> None:
    part = _part()
    legacy_fillets = tuple(recognise_fillets(part))
    assert legacy_fillets

    with pytest.raises(ValueError, match="fillets and circular_blind_steps"):
        build_part_model(part, fillets=legacy_fillets)
    with pytest.raises(ValueError, match="fillets and circular_blind_steps"):
        build_part_model(part, circular_blind_steps=())

    with pytest.raises(ValueError, match="provider owner identity"):
        build_part_model(
            part,
            fillets=legacy_fillets,
            circular_blind_steps=(),
        )

    second_owner = replace(
        legacy_fillets[0],
        at=tuple(value + 0.25 for value in legacy_fillets[0].at),
    )
    with pytest.raises(ValueError, match="provider owner identity"):
        build_part_model(
            part,
            fillets=(legacy_fillets[0], second_owner),
            circular_blind_steps=(),
        )

    with pytest.raises(ValueError, match="preserve aggregate ownership"):
        build_part_model(
            part,
            fillets=(legacy_fillets[0], legacy_fillets[0]),
            circular_blind_steps=(),
        )

    for unrelated in (
        second_owner,
        replace(legacy_fillets[0], radius=legacy_fillets[0].radius + 0.5),
        replace(legacy_fillets[0], axis="z"),
    ):
        with pytest.raises(ValueError, match="provider owner identity"):
            build_part_model(
                part,
                fillets=(unrelated,),
                circular_blind_steps=(),
            )


def test_translated_paired_legacy_override_fails_closed_without_provider_identity() -> None:
    part = Pos(0.5555, 0.761035, -0.405515) * _part()
    legacy_fillets = tuple(recognise_fillets(part))
    assert len(legacy_fillets) == 1

    with pytest.raises(ValueError, match="provider owner identity"):
        build_part_model(
            part,
            fillets=legacy_fillets,
            circular_blind_steps=(),
        )


def test_owner_free_explicit_injection_accepts_one_family_but_not_both() -> None:
    source = _part()
    recognition = build_raw_recognition_result(source)
    legacy_fillets = tuple(recognise_fillets(source))
    plain = Box(40, 30, 20)

    fillet_model = build_part_model(plain, fillets=legacy_fillets)
    circular_model = build_part_model(
        plain,
        circular_blind_steps=recognition.circular_blind_steps,
    )
    assert [feature.kind for feature in fillet_model.features].count("fillet") == 1
    assert [feature.kind for feature in circular_model.features].count("circular_blind_step") == 1

    with pytest.raises(ValueError, match="cannot be supplied together"):
        build_part_model(
            plain,
            fillets=legacy_fillets,
            circular_blind_steps=recognition.circular_blind_steps,
        )


def test_circular_inventory_order_does_not_change_aggregate_ownership() -> None:
    part = Compound([_part(), Pos(80, 0, 0) * _part()])
    recognition = build_raw_recognition_result(part)
    assert len(recognition.circular_blind_steps) == 2

    model = build_part_model(
        part,
        circular_blind_steps=(record for record in reversed(recognition.circular_blind_steps)),
    )

    assert [feature.kind for feature in model.features].count("circular_blind_step") == 2


def test_unrelated_circular_owner_does_not_authorize_fillet_erasure() -> None:
    rounded = Box(20, 20, 20)
    edge = sorted(
        rounded.edges().filter_by(Axis.Z),
        key=lambda item: item.center().X + item.center().Y,
    )[-1]
    part = Compound([_part(), Pos(80, 0, 0) * fillet(edge, 2)])
    recognition = build_raw_recognition_result(part)

    assert len(recognition.circular_blind_steps) == len(recognition.fillets) == 1
    with pytest.raises(ValueError, match="fillets and blends"):
        build_part_model(part, fillets=())


def test_standalone_model_runs_one_aggregate_and_one_cylinder_scan(monkeypatch) -> None:
    import draftwright.model.detect as detect

    rotational_flags = []
    real_build = detect.build_raw_recognition_result

    def recording_build(*args, **kwargs):
        rotational_flags.append(kwargs["rotational"])
        return real_build(*args, **kwargs)

    monkeypatch.setattr(detect, "build_raw_recognition_result", recording_build)
    with counting_calls(
        {"turned_steps": recognise_turned_steps, "cylinders": analyse_cylinders}
    ) as counts:
        build_part_model(Box(40, 30, 20))

    assert counts == {"cylinders": 1, "turned_steps": 1}
    assert rotational_flags == [False]

    rotational_flags.clear()
    build_part_model(Cylinder(20, 60))
    assert rotational_flags == [True]


@pytest.mark.parametrize(
    "malformed",
    [
        lambda source: replace(source, radius=10**10000),
        lambda source: replace(source, centreline=((0, 0), (1, 1))),
    ],
)
def test_supplied_malformed_records_fail_at_the_public_model_boundary(malformed) -> None:
    source = build_raw_recognition_result(_part()).circular_blind_steps[0]

    with pytest.raises(ValueError):
        build_part_model(_part(), circular_blind_steps=(malformed(source),))


def test_explicit_declaration_and_generated_line_round_trip_every_fact() -> None:
    source = build_raw_recognition_result(_part()).circular_blind_steps[0]
    declared = _declare(source)
    line = _feature_line(declared)
    assert line == (
        'sheet.circular_blind_step(axis="x", radius=4, length=25, '
        "centreline=((-5, 15, 10), (20, 15, 10)), "
        "section=((11, 10), (15, 10), (15, 6)))"
    )
    namespace = {
        "sheet": type("S", (), {"circular_blind_step": staticmethod(circular_blind_step)})()
    }
    assert eval(line, {"__builtins__": {}}, namespace) == declared  # noqa: S307


def test_generated_line_preserves_sub_millimetre_correspondence_exactly() -> None:
    radius = 4.123456
    length = 10.654321
    centre = (1.234567, -2.345678)
    centreline = (
        (centre[0], centre[1], -5.4321),
        (centre[0], centre[1], -5.4321 + length),
    )
    section = (
        (centre[0] - radius, centre[1]),
        centre,
        (centre[0], centre[1] + radius),
    )
    declared = circular_blind_step(
        axis="z",
        radius=radius,
        length=length,
        centreline=centreline,
        section=section,
    )
    line = _feature_line(declared)
    namespace = {
        "sheet": type("S", (), {"circular_blind_step": staticmethod(circular_blind_step)})()
    }

    assert "4.123456" in line
    assert eval(line, {"__builtins__": {}}, namespace) == declared  # noqa: S307


@pytest.mark.parametrize(
    "bad",
    [
        0,
        -1,
        True,
        float("nan"),
        float("inf"),
        "4.0",
        _DeceptiveFloat(999.0),
        pytest.param(10**10000, id="huge-int"),
    ],
)
def test_declaration_rejects_invalid_sizes_without_numeric_exceptions(bad) -> None:
    source = build_raw_recognition_result(_part()).circular_blind_steps[0]
    with pytest.raises(ValueError):
        circular_blind_step(
            axis=source.axis,
            radius=bad,
            length=source.length,
            centreline=source.centreline,
            section=source.section,
        )
    with pytest.raises(ValueError):
        circular_blind_step(
            axis=source.axis,
            radius=source.radius,
            length=bad,
            centreline=source.centreline,
            section=source.section,
        )


def test_ir_rejects_malformed_or_inconsistent_correspondence() -> None:
    source = build_raw_recognition_result(_part()).circular_blind_steps[0]
    valid = _declare(source)

    with pytest.raises(ValueError, match="agree with the feature frame"):
        replace(valid, axis="y")
    with pytest.raises(ValueError, match="matching depth"):
        replace(valid, centreline=(source.centreline[0], (19.0, 15.0, 10.0)))
    with pytest.raises(ValueError, match="canonical quarter arc"):
        replace(valid, section=((12.0, 10.0), source.section[1], source.section[2]))
    with pytest.raises(ValueError, match="section centre"):
        shifted = tuple((point[0] + 1, point[1]) for point in source.section)
        anchor = CircularBlindStepFeature.anchor_for(
            source.axis, source.radius, source.centreline, shifted
        )
        replace(valid, frame=Frame(anchor, source.axis), section=shifted)
    with pytest.raises(ValueError, match="leader anchor"):
        replace(valid, frame=Frame((0, 0, 0), source.axis))
    with pytest.raises(ValueError, match="finite 3D points"):
        replace(valid, centreline=((True, 15, 10), source.centreline[1]))
    with pytest.raises(ValueError, match="finite 3D points"):
        replace(valid, centreline=(("-5", 15, 10), source.centreline[1]))
    with pytest.raises(ValueError, match="finite 2D points"):
        replace(valid, section=((11, 10), (15, 10), (15, float("nan"))))
    with pytest.raises(ValueError, match="finite 2D points"):
        replace(valid, section=(("11", 10), (15, 10), (15, 6)))
    with pytest.raises(ValueError, match="finite 3D points"):
        replace(valid, centreline=None)
    with pytest.raises(ValueError, match="finite 3D points"):
        replace(valid, centreline=((0, 0), (1, 1)))
    with pytest.raises(ValueError, match="finite 2D points"):
        replace(valid, section=None)
    with pytest.raises(ValueError, match="finite 2D points"):
        replace(valid, section=((0, 0), (1, 1)))
    with pytest.raises(ValueError, match="radius must be finite"):
        replace(valid, radius=object())
    with pytest.raises(ValueError, match="radius must be finite"):
        replace(valid, radius=_DeceptiveFloat(999.0))
    with pytest.raises(ValueError, match="depth must be finite"):
        replace(valid, length=object())
    with pytest.raises(ValueError, match="frame origin"):
        CircularBlindStepFeature(
            Frame((True, True, True), "x"),
            "x",
            2**0.5,
            2,
            ((0, 0, 0), (2, 0, 0)),
            ((2**0.5, 0), (0, 0), (0, 2**0.5)),
        )

    translated = circular_blind_step(
        axis="x",
        radius=4,
        length=25,
        centreline=((-5, 1e12, 1e12), (20, 1e12, 1e12)),
        section=((1e12 - 4, 1e12), (1e12, 1e12), (1e12, 1e12 - 4)),
    )
    with pytest.raises(ValueError, match="axis-aligned"):
        circular_blind_step(
            axis="x",
            radius=4,
            length=25,
            centreline=((-5, 1e12, 1e12), (20, 1e12 + 500, 1e12 + 500)),
            section=((1e12 - 4, 1e12), (1e12, 1e12), (1e12, 1e12 - 4)),
        )
    with pytest.raises(ValueError, match="leader anchor"):
        replace(
            translated,
            frame=Frame(
                (
                    translated.frame.origin[0],
                    translated.frame.origin[1] + 500,
                    translated.frame.origin[2] + 500,
                ),
                "x",
            ),
        )
    assert valid.references() == []


def test_max_finite_geometry_derives_a_finite_exactly_joinable_anchor() -> None:
    radius = float_info.max
    source = replace(
        build_raw_recognition_result(_part()).circular_blind_steps[0],
        radius=radius,
        length=1.7e308 - 8e307,
        centreline=((8e307, 0.0, 0.0), (1.7e308, 0.0, 0.0)),
        section=((-radius, 0.0), (0.0, 0.0), (0.0, -radius)),
    )
    feature = _declare(source)
    expected = -radius / 2**0.5

    assert feature.frame.origin == pytest.approx((1.25e308, expected, expected))
    registry = AnnotationRegistry()
    registry.add(
        object(),
        "max-finite",
        "side",
        feature=feature,
        measurement=(
            DimensionId(feature, "circular_step_radius.radius"),
            DimensionId(feature, "circular_step_depth.length"),
        ),
    )
    recognition = replace(build_raw_recognition_result(_part()), circular_blind_steps=(source,))
    assert [
        outcome.state
        for outcome in circular_blind_step_requirement_outcomes(recognition, (feature,), registry)
    ] == ["placed", "placed"]


def test_declaration_reports_malformed_record_geometry_at_its_boundary() -> None:
    source = build_raw_recognition_result(_part()).circular_blind_steps[0]
    with pytest.raises(ValueError, match="centreline must contain"):
        circular_blind_step(
            axis=source.axis,
            radius=source.radius,
            length=source.length,
            centreline=None,
            section=source.section,
        )
    with pytest.raises(ValueError, match="centreline must contain"):
        circular_blind_step(
            axis=source.axis,
            radius=source.radius,
            length=source.length,
            centreline=(("-5", 15, 10), source.centreline[1]),
            section=source.section,
        )
    with pytest.raises(ValueError, match="section must contain"):
        circular_blind_step(
            axis=source.axis,
            radius=source.radius,
            length=source.length,
            centreline=source.centreline,
            section=None,
        )
    with pytest.raises(ValueError, match="section must contain"):
        circular_blind_step(
            axis=source.axis,
            radius=source.radius,
            length=source.length,
            centreline=source.centreline,
            section=(("11", 10), source.section[1], source.section[2]),
        )
    with pytest.raises(ValueError, match="valid centreline"):
        circular_blind_step(
            axis=source.axis,
            radius=source.radius,
            length=source.length,
            centreline=source.centreline,
            section=((0, 0), (0, 0), (0, 0)),
        )


def test_auto_callout_carries_both_identities_at_the_curved_wall() -> None:
    drawing = build_drawing(_part())
    feature = _feature(drawing)
    _name, annotation = _annotation(drawing)

    assert annotation.label == "R4 × 25 DEEP"
    _assert_owned_callout(drawing, feature)


def test_collected_circular_job_keeps_only_prevalidated_routes(monkeypatch) -> None:
    drawing = build_drawing(_part())
    ctx = PlacementContext(
        registry=drawing.registry,
        coverage=drawing.coverage,
        items=drawing.items,
        feature_leaders=[],
    )
    from_model.render_circular_blind_steps(
        drawing,
        compile_dimensions(drawing.model()),
        None,
        ctx=ctx,
    )

    assert ctx.feature_leaders is not None
    (job,) = ctx.feature_leaders
    candidates = list(job.candidates)
    assert 0 < len(candidates) <= 4
    for candidate in candidates:
        geometry = job.analytical_geometry(*candidate)
        assert geometry is not None
        _label_box, segments = geometry
        for other_view in drawing.views:
            if other_view == job.view:
                continue
            other = drawing.view_bounds(other_view)
            assert other is not None
            pad = max(drawing.draft.line_width, drawing.draft.arrow_length) / 2
            padded = (other[0] - pad, other[1] - pad, other[2] + pad, other[3] + pad)
            assert not any(_segment_clips_box(first, second, padded) for first, second in segments)

    blocked_candidate = candidates[0]
    tip, elbow, _owner = blocked_candidate
    midpoint = ((tip[0] + elbow[0]) / 2, (tip[1] + elbow[1]) / 2)
    blocker = (midpoint[0] - 1, midpoint[1] - 1, midpoint[0] + 1, midpoint[1] + 1)
    assert _segment_clips_box(tip, elbow, blocker)
    target_bounds = drawing.view_bounds(job.view)
    assert target_bounds is not None
    blocker_view = next(view for view in drawing.views if view != job.view)

    def blocked_view_bounds(view):
        if view == job.view:
            return target_bounds
        if view == blocker_view:
            return blocker
        return None

    monkeypatch.setattr(drawing, "view_bounds", blocked_view_bounds)
    blocked_ctx = PlacementContext(
        registry=drawing.registry,
        coverage=drawing.coverage,
        items=drawing.items,
        feature_leaders=[],
    )
    from_model.render_circular_blind_steps(
        drawing,
        compile_dimensions(drawing.model()),
        None,
        ctx=blocked_ctx,
    )

    assert blocked_ctx.feature_leaders is not None
    (blocked_job,) = blocked_ctx.feature_leaders
    assert blocked_candidate not in list(blocked_job.candidates)


@pytest.mark.parametrize(
    ("part", "axis", "view"),
    [
        (_part(), "x", "side"),
        (Rot(0, 0, -90) * _part(), "y", "front"),
        (Rot(0, 90, 0) * _part(), "z", "plan"),
    ],
)
def test_end_view_routing_and_correspondence_work_on_every_principal_axis(
    part, axis, view
) -> None:
    drawing = build_drawing(part)
    source = drawing.recognition().circular_blind_steps[0]  # type: ignore[union-attr]
    feature = _feature(drawing)

    assert feature.axis == source.axis == axis
    assert (feature.centreline, feature.section) == (source.centreline, source.section)
    name, _annotation_object = _annotation(drawing)
    assert drawing.registry.identity_of(name)["view"] == view
    _assert_owned_callout(drawing, feature, name)
    assert drawing.lint() == []


@pytest.mark.parametrize(
    "part",
    [
        _part(),
        Rot(180, 0, 0) * _part(),
        Rot(0, 180, 0) * _part(),
        Rot(180, 180, 0) * _part(),
    ],
)
def test_all_quadrants_accept_provider_endpoint_order_and_anchor_the_arc(part) -> None:
    drawing = build_drawing(part)
    source = drawing.recognition().circular_blind_steps[0]  # type: ignore[union-attr]
    feature = _feature(drawing)

    assert feature.section == source.section
    assert feature.frame.origin == pytest.approx(feature.arc_anchor)
    _assert_owned_callout(drawing, feature)


def test_compound_keeps_each_body_owned_step_and_live_replay_is_per_feature() -> None:
    part = Compound([Pos(0, -60, 0) * _part(), Pos(0, 60, 0) * _part()])
    drawing = build_drawing(part)
    features = [
        feature for feature in drawing.model().features if feature.kind == "circular_blind_step"
    ]
    names = [name for name in drawing.annotations() if name.startswith("m_circular_blind_step_")]
    completeness = drawing.lint_summary()["quality"]["completeness"]

    assert len(drawing.recognition().circular_blind_steps) == 2  # type: ignore[union-attr]
    assert len(features) == len(names) == 2
    assert completeness["by_family"]["circular_blind_steps"] == 4
    assert completeness["placed"] == completeness["requirements"] == 4
    name_by_feature = {
        drawing.registry.identity_of(name)["measurement"][0].feature: name for name in names
    }
    assert set(name_by_feature) == set(features)
    for feature in features:
        _assert_owned_callout(drawing, feature, name_by_feature[feature])

    first = features[0]
    drawing.drop(first)
    replayed = drawing.callout(first)
    assert replayed.startswith("m_circular_blind_step_")
    _assert_owned_callout(drawing, first, replayed)


@pytest.mark.parametrize(
    ("parameter", "tolerance_role", "expected", "suppressed"),
    [
        (
            "circular_step_radius.radius",
            "radius",
            "R4 ±0.2",
            "circular_step_depth.length",
        ),
        (
            "circular_step_depth.length",
            "depth",
            "25 ±0.2 DEEP",
            "circular_step_radius.radius",
        ),
    ],
)
def test_authored_partial_sets_do_not_resurrect_omitted_content(
    parameter, tolerance_role, expected, suppressed
) -> None:
    part = _part()
    recognition = build_raw_recognition_result(part)
    source = recognition.circular_blind_steps[0]
    sheet = Sheet(part)
    handle = sheet.circular_blind_step(
        axis=source.axis,
        radius=source.radius,
        length=source.length,
        centreline=source.centreline,
        section=source.section,
    )
    handle.tolerance(0.2, on=tolerance_role)
    sheet.authored_dimensions().dimension(handle, parameter)

    drawing = sheet.build()
    _name, annotation = _annotation(drawing)
    outcomes = circular_blind_step_requirement_outcomes(
        recognition,
        drawing.model().features,
        drawing.registry,
        compile_dimensions(drawing.model()).diagnostics,
    )

    assert annotation.label == expected
    states = {outcome.parameter_id: outcome.state for outcome in outcomes}
    assert states == {parameter: "placed", suppressed: "suppressed"}


def test_ledger_distinguishes_outcomes_and_fails_closed_on_ambiguity() -> None:
    drawing = build_drawing(_part())
    recognition = drawing.recognition()
    assert recognition is not None
    feature = _feature(drawing)

    assert [
        outcome.state
        for outcome in circular_blind_step_requirement_outcomes(
            recognition, drawing.model().features, drawing.registry
        )
    ] == ["placed", "placed"]

    empty = AnnotationRegistry()
    assert [
        outcome.state
        for outcome in circular_blind_step_requirement_outcomes(
            recognition, drawing.model().features, empty
        )
    ] == ["missing", "missing"]

    omission = SimpleNamespace(
        feature=feature,
        parameter_id="circular_step_radius.radius",
        authored=True,
    )
    assert [
        outcome.state
        for outcome in circular_blind_step_requirement_outcomes(
            recognition, drawing.model().features, empty, (omission,)
        )
    ] == ["suppressed", "missing"]

    dropped_registry = AnnotationRegistry()
    dropped_registry.record_issue(
        LintIssue(
            "warning",
            "synthetic placement failure",
            code="circular_blind_step_dropped",
            measurement_ids=(DimensionId(feature, "circular_step_depth.length"),),
            outcome_stage="placement",
        )
    )
    assert [
        outcome.state
        for outcome in circular_blind_step_requirement_outcomes(
            recognition, drawing.model().features, dropped_registry
        )
    ] == ["missing", "dropped"]

    satisfied_registry = AnnotationRegistry()
    satisfied_registry.add(
        object(),
        "structured_note",
        "side",
        feature=feature,
        satisfaction=DimensionId(feature, "circular_step_radius.radius"),
    )
    assert [
        outcome.state
        for outcome in circular_blind_step_requirement_outcomes(
            recognition, drawing.model().features, satisfied_registry
        )
    ] == ["satisfied_by_structured_note", "missing"]

    duplicated = replace(
        recognition,
        circular_blind_steps=(
            recognition.circular_blind_steps[0],
            recognition.circular_blind_steps[0],
        ),
    )
    ambiguous = circular_blind_step_requirement_outcomes(
        duplicated, drawing.model().features, empty
    )
    assert len(ambiguous) == 4
    assert {outcome.state for outcome in ambiguous} == {"unverifiable"}


def test_ledger_rejects_wrong_runs_and_malformed_correspondence() -> None:
    drawing = build_drawing(_part())
    recognition = drawing.recognition()
    assert recognition is not None

    assert circular_blind_step_requirement_outcomes(None, (), AnnotationRegistry()) == []
    assert (
        circular_blind_step_requirement_outcomes(
            replace(recognition, circular_blind_steps=()), (), AnnotationRegistry()
        )
        == []
    )
    with pytest.raises(TypeError, match="requires the run's RecognitionResult"):
        circular_blind_step_requirement_outcomes(SimpleNamespace(), (), AnnotationRegistry())

    source = recognition.circular_blind_steps[0]
    malformed_source = SimpleNamespace(
        axis=source.axis,
        radius=10**10000,
        length=source.length,
        centreline=source.centreline,
        section=source.section,
    )
    malformed_run = replace(recognition, circular_blind_steps=(malformed_source,))
    malformed = circular_blind_step_requirement_outcomes(
        malformed_run, drawing.model().features, AnnotationRegistry()
    )
    assert [outcome.state for outcome in malformed] == ["unverifiable", "unverifiable"]
    assert malformed[0].source_at == source.centreline[0]

    wrong_source = replace(source, radius=source.radius + 1)
    wrong_run = replace(recognition, circular_blind_steps=(wrong_source,))
    assert [
        outcome.state
        for outcome in circular_blind_step_requirement_outcomes(
            wrong_run, drawing.model().features, AnnotationRegistry()
        )
    ] == ["unverifiable", "unverifiable"]

    no_location = SimpleNamespace(
        axis=source.axis,
        radius=source.radius,
        length=source.length,
        centreline=None,
        section=source.section,
    )
    no_location_run = replace(recognition, circular_blind_steps=(no_location,))
    no_location_outcomes = circular_blind_step_requirement_outcomes(
        no_location_run, drawing.model().features, AnnotationRegistry()
    )
    assert all(value != value for value in no_location_outcomes[0].source_at)

    broken_ir = SimpleNamespace(
        kind="circular_blind_step",
        axis=source.axis,
        radius=source.radius,
        length=source.length,
        centreline=source.centreline,
        section=source.section,
        frame=_feature(drawing).frame,
        parameters=lambda: (_ for _ in ()).throw(TypeError),
    )
    broken_outcomes = circular_blind_step_requirement_outcomes(
        recognition,
        (SimpleNamespace(kind="circular_blind_step"), broken_ir),
        AnnotationRegistry(),
    )
    assert [outcome.state for outcome in broken_outcomes] == [
        "unverifiable",
        "unverifiable",
    ]

    issues = lint_circular_blind_step_coverage(
        _part(),
        recognition=recognition,
        features=drawing.model().features,
        registry=AnnotationRegistry(),
        assembly=False,
    )
    assert {issue.code for issue in issues} == {"circular_blind_step_requirement_missing"}


def test_ledger_validates_source_and_ir_schemas_before_crediting_matching_ink() -> None:
    recognition = build_raw_recognition_result(_part())
    source = recognition.circular_blind_steps[0]
    translated_misaligned = replace(
        source,
        centreline=((-5, 1e12, 1e12), (20, 1e12 + 500, 1e12 + 500)),
        section=((1e12 - 4, 1e12), (1e12, 1e12), (1e12, 1e12 - 4)),
    )

    class FloatLike:
        def __float__(self):
            return source.radius

    malformed_sources = (
        replace(source, radius=True),
        replace(source, radius=str(source.radius)),
        replace(source, radius=FloatLike()),
        replace(source, radius=_DeceptiveFloat(999.0)),
        replace(source, radius=-1),
        replace(source, radius=float("inf")),
        replace(source, length=True),
        replace(source, axis=""),
        replace(source, centreline=((True, 15, 10), (20, 15, 10))),
        replace(source, centreline=(("-5", 15, 10), (20, 15, 10))),
        replace(source, centreline=((0, 0), (1, 1))),
        replace(source, centreline=((0, 0, 0), (1, 1, 1))),
        replace(source, section=((True, 10), (15, 10), (15, 6))),
        replace(source, section=(("11", 10), (15, 10), (15, 6))),
        replace(source, section=((11, 10), (15, 10))),
        replace(source, section=((11, 10), (15, 10), (15, 10))),
        replace(source, section=((11, 10), (14, 10), (14, 6))),
        replace(source, section=((10, 10), (14, 10), (14, 6))),
        translated_misaligned,
    )

    for malformed_source in malformed_sources:
        forged = _unchecked_feature(malformed_source)
        registry = AnnotationRegistry()
        registry.add(
            object(),
            "forged",
            "side",
            feature=forged,
            measurement=(
                DimensionId(forged, "circular_step_radius.radius"),
                DimensionId(forged, "circular_step_depth.length"),
            ),
        )
        malformed_run = replace(recognition, circular_blind_steps=(malformed_source,))

        assert [
            outcome.state
            for outcome in circular_blind_step_requirement_outcomes(
                malformed_run, (forged,), registry
            )
        ] == ["unverifiable", "unverifiable"]


def test_ledger_validates_ir_frame_before_crediting_matching_source() -> None:
    recognition = build_raw_recognition_result(_part())
    source = recognition.circular_blind_steps[0]
    feature = _declare(source)
    malformed_frames = (
        Frame(feature.frame.origin, "y"),
        Frame((True, feature.frame.origin[1], feature.frame.origin[2]), source.axis),
        Frame((str(feature.frame.origin[0]), *feature.frame.origin[1:]), source.axis),
        Frame((feature.frame.origin[0], feature.frame.origin[1]), source.axis),
        Frame((float("nan"), feature.frame.origin[1], feature.frame.origin[2]), source.axis),
        Frame((0, 0, 0), source.axis),
    )

    for malformed_frame in malformed_frames:
        forged = _unchecked_feature(source)
        object.__setattr__(forged, "frame", malformed_frame)
        assert [
            outcome.state
            for outcome in circular_blind_step_requirement_outcomes(
                recognition, (forged,), AnnotationRegistry()
            )
        ] == ["unverifiable", "unverifiable"]


def test_ledger_does_not_round_distinct_valid_occurrences_into_one_identity() -> None:
    recognition = build_raw_recognition_result(_part())
    source = recognition.circular_blind_steps[0]
    shifted_centreline = tuple(
        (point[0] + 0.0004, point[1], point[2]) for point in source.centreline
    )
    shifted = circular_blind_step(
        axis=source.axis,
        radius=source.radius,
        length=source.length,
        centreline=shifted_centreline,
        section=source.section,
    )
    registry = AnnotationRegistry()
    registry.add(
        object(),
        "near-neighbour",
        "side",
        feature=shifted,
        measurement=(
            DimensionId(shifted, "circular_step_radius.radius"),
            DimensionId(shifted, "circular_step_depth.length"),
        ),
    )

    assert [
        outcome.state
        for outcome in circular_blind_step_requirement_outcomes(recognition, (shifted,), registry)
    ] == ["unverifiable", "unverifiable"]


def test_quality_counts_both_requirements_and_family_is_no_longer_unscored() -> None:
    completeness = build_drawing(_part()).lint_summary()["quality"]["completeness"]

    assert completeness["by_family"]["circular_blind_steps"] == 2
    assert completeness["placed"] == completeness["requirements"] == 2
    assert completeness["audited_score"] == 1.0
    assert "circular_blind_steps" not in completeness["unscored_recognized_families"]


@pytest.mark.parametrize(
    "axis_rotation",
    [
        Rot(0, 0, 0),
        Rot(0, 0, -90),
        Rot(0, 90, 0),
    ],
)
@pytest.mark.parametrize(
    "quadrant_rotation",
    [
        Rot(0, 0, 0),
        Rot(180, 0, 0),
        Rot(0, 180, 0),
        Rot(180, 180, 0),
    ],
)
def test_live_and_deferred_callout_verbs_reuse_the_same_renderer(
    axis_rotation, quadrant_rotation
) -> None:
    part = axis_rotation * (quadrant_rotation * _part())
    live = build_drawing(part)
    assert live.lint() == []
    live_feature = _feature(live)
    live.drop(live_feature)
    live_name = live.callout(live_feature)
    assert _assert_owned_callout(live, live_feature, live_name).label == "R4 × 25 DEEP"
    assert live.lint() == []

    deferred = build_drawing(part)
    assert deferred.lint() == []
    deferred_feature = _feature(deferred)
    deferred.drop(deferred_feature)
    with deferred.deferred():
        deferred.callout(deferred_feature)
    deferred_name, _annotation_object = _annotation(deferred)
    assert _assert_owned_callout(deferred, deferred_feature, deferred_name).label == "R4 × 25 DEEP"
    assert deferred.lint() == []
