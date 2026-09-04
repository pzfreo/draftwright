"""Round-bottom blind-slot consumer semantics (#1421, b123d-recognisers 0.4.10)."""

from __future__ import annotations

from dataclasses import replace
from fractions import Fraction

import pytest
from b123d_recognisers import build_raw_recognition_result
from build123d import (
    Axis,
    Box,
    BuildLine,
    BuildSketch,
    Line,
    Pos,
    RadiusArc,
    Vector,
    extrude,
    make_face,
)

from draftwright import Sheet, build_drawing
from draftwright.annotations import from_model
from draftwright.annotations._common import PlacementContext
from draftwright.builder import detect_part_model
from draftwright.model import Frame, RoundBottomBlindSlotFeature, round_bottom_blind_slot
from draftwright.model.compiled import compile_dimensions
from draftwright.model.detect import build_part_model
from draftwright.sheet_emit import _feature_block, _feature_line


def _part():
    width, radius, length = 10.0, 3.0, 20.0
    half_width = width / 2
    half_flat = (width - 2 * radius) / 2
    with BuildLine() as boundary:
        Line((-half_width, 0), (half_width, 0))
        RadiusArc((half_width, 0), (half_flat, -radius), radius)
        Line((half_flat, -radius), (-half_flat, -radius))
        RadiusArc((-half_flat, -radius), (-half_width, 0), radius)
    with BuildSketch() as sketch:
        make_face(boundary.line)
    stock = Pos(0, -5, 0) * Box(30, 10, 40)
    tool = extrude(sketch.sketch, amount=length, dir=Vector(0, 0, 1))
    return stock - tool


def _record():
    result = build_raw_recognition_result(_part())
    assert result.slots == result.pockets == result.channels == ()
    assert len(result.round_bottom_blind_slots) == 1
    return result.round_bottom_blind_slots[0]


def _declared(record=None):
    source = _record() if record is None else record
    return round_bottom_blind_slot(
        axis=source.axis,
        open_sign=source.open_sign,
        length=source.length,
        width_axis=source.width_axis,
        depth_axis=source.depth_axis,
        depth_sign=source.depth_sign,
        radius=source.radius,
        flat_width=source.flat_width,
        at=source.at,
    )


def test_aggregate_record_lowers_without_slot_pocket_or_channel_coownership() -> None:
    source = _record()
    model = detect_part_model(_part())
    blind_slots = [
        feature for feature in model.features if feature.kind == "round_bottom_blind_slot"
    ]

    assert len(blind_slots) == 1
    feature = blind_slots[0]
    assert feature == _declared(source)
    assert (feature.axis, feature.open_sign) == (source.axis, source.open_sign)
    assert (feature.width_axis, feature.depth_axis, feature.depth_sign) == (
        source.width_axis,
        source.depth_axis,
        source.depth_sign,
    )
    assert (feature.length, feature.radius, feature.flat_width) == (
        source.length,
        source.radius,
        source.flat_width,
    )
    assert not ({"slot", "pocket", "channel"} & {item.kind for item in model.features})


def test_explicit_sheet_word_and_generated_line_round_trip_the_exact_ir() -> None:
    source = _record()
    sheet = Sheet(_part()).authored_dimensions()
    sheet.round_bottom_blind_slot(
        axis=source.axis,
        open_sign=source.open_sign,
        length=source.length,
        width_axis=source.width_axis,
        depth_axis=source.depth_axis,
        depth_sign=source.depth_sign,
        radius=source.radius,
        flat_width=source.flat_width,
        at=source.at,
    )
    declared = sheet.model().features[0]
    assert declared == _declared(source)

    line = _feature_line(declared)
    assert line.startswith("sheet.round_bottom_blind_slot(")
    assert "open_sign=1" in line
    assert "depth_sign=1" in line
    namespace = {
        "sheet": type(
            "GeneratedSheet",
            (),
            {"round_bottom_blind_slot": staticmethod(round_bottom_blind_slot)},
        )()
    }
    assert eval(line.split("   #", 1)[0], {"__builtins__": {}}, namespace) == declared  # noqa: S307


def test_drawing_places_one_solver_owned_open_slot_callout_with_all_sizes() -> None:
    drawing = build_drawing(_part())
    feature = next(
        feature
        for feature in drawing.model().features
        if feature.kind == "round_bottom_blind_slot"
    )
    annotations = drawing.annotations_of(feature)
    assert len(annotations) == 1
    name, annotation = next(iter(annotations.items()))
    assert annotation.label == "ROUND-BOTTOM OPEN SLOT 4 BOTTOM FLAT × R3 × 20 LONG"
    assert drawing.view_of(name) == "front"
    assert {key["parameter_id"] for key in drawing.measurement_keys(name)} == {
        "round_bottom_blind_slot_length.length",
        "round_bottom_blind_slot_flat_width.length",
        "round_bottom_blind_slot_radius.radius",
    }
    assert not {
        issue.code
        for issue in drawing.lint()
        if issue.code in {"round_bottom_blind_slot_dropped", "annotation_overlap"}
    }


@pytest.mark.parametrize(
    ("parameters", "expected_label"),
    [
        (("round_bottom_blind_slot_flat_width.length",), "ROUND-BOTTOM OPEN SLOT 4 BOTTOM FLAT"),
        (("round_bottom_blind_slot_radius.radius",), "ROUND-BOTTOM OPEN SLOT R3"),
        (("round_bottom_blind_slot_length.length",), "ROUND-BOTTOM OPEN SLOT 20 LONG"),
        (
            (
                "round_bottom_blind_slot_flat_width.length",
                "round_bottom_blind_slot_length.length",
            ),
            "ROUND-BOTTOM OPEN SLOT 4 BOTTOM FLAT × 20 LONG",
        ),
        (
            (
                "round_bottom_blind_slot_flat_width.length",
                "round_bottom_blind_slot_radius.radius",
            ),
            "ROUND-BOTTOM OPEN SLOT 4 BOTTOM FLAT × R3",
        ),
        (
            (
                "round_bottom_blind_slot_length.length",
                "round_bottom_blind_slot_radius.radius",
            ),
            "ROUND-BOTTOM OPEN SLOT R3 × 20 LONG",
        ),
        (
            (
                "round_bottom_blind_slot_flat_width.length",
                "round_bottom_blind_slot_length.length",
                "round_bottom_blind_slot_radius.radius",
            ),
            "ROUND-BOTTOM OPEN SLOT 4 BOTTOM FLAT × R3 × 20 LONG",
        ),
    ],
)
def test_every_nonempty_authored_parameter_subset_survives_rendering(
    parameters, expected_label
) -> None:
    source = _record()
    sheet = Sheet(_part()).authored_dimensions()
    handle = sheet.round_bottom_blind_slot(
        axis=source.axis,
        open_sign=source.open_sign,
        length=source.length,
        width_axis=source.width_axis,
        depth_axis=source.depth_axis,
        depth_sign=source.depth_sign,
        radius=source.radius,
        flat_width=source.flat_width,
        at=source.at,
    )
    for parameter in parameters:
        sheet.dimension(handle, parameter)

    drawing = sheet.build()
    feature = next(
        feature
        for feature in drawing.model().features
        if feature.kind == "round_bottom_blind_slot"
    )
    annotations = drawing.annotations_of(feature)

    assert len(annotations) == 1
    name, annotation = next(iter(annotations.items()))
    assert annotation.label == expected_label
    assert {key["parameter_id"] for key in drawing.measurement_keys(name)} == set(parameters)
    issues = drawing.lint()
    assert len(issues) == 3 - len(parameters)
    assert {issue.code for issue in issues} <= {"round_bottom_blind_slot_requirement_suppressed"}


@pytest.mark.parametrize(
    ("part", "expected_axis", "expected_open_sign"),
    [
        pytest.param(_part(), "z", 1, id="z-open-positive"),
        pytest.param(_part().rotate(Axis.X, 180), "z", -1, id="z-open-negative"),
        pytest.param(_part().rotate(Axis.Y, 90), "x", 1, id="x-open-positive"),
        pytest.param(_part().rotate(Axis.Y, -90), "x", -1, id="x-open-negative"),
        pytest.param(_part().rotate(Axis.X, 90), "y", -1, id="y-open-negative"),
        pytest.param(_part().rotate(Axis.X, -90), "y", 1, id="y-open-positive"),
    ],
)
def test_automatic_leader_tip_targets_material_never_the_open_mouth(
    part, expected_axis, expected_open_sign
) -> None:
    drawing = build_drawing(part)
    feature = next(
        feature
        for feature in drawing.model().features
        if feature.kind == "round_bottom_blind_slot"
    )
    assert (feature.axis, feature.open_sign) == (expected_axis, expected_open_sign)
    name, annotation = next(iter(drawing.annotations_of(feature).items()))
    view = drawing.view_of(name)
    origin = list(feature.frame.origin)
    axis_index = "xyz".index(feature.axis)
    width_index = "xyz".index(feature.width_axis)

    mouth = origin.copy()
    mouth[axis_index] += feature.open_sign * feature.length / 2
    mouth_page = drawing.at(view, *mouth)[:2]

    cap = origin.copy()
    cap[axis_index] -= feature.open_sign * feature.length / 2
    material_targets = [cap]
    for side_sign in (-1, 1):
        floor_end = origin.copy()
        floor_end[width_index] += side_sign * feature.flat_width / 2
        side = origin.copy()
        side[width_index] += side_sign * feature.width / 2
        corner = cap.copy()
        corner[width_index] += side_sign * feature.width / 2
        material_targets.extend((floor_end, side, corner))
    material_page_targets = [drawing.at(view, *target)[:2] for target in material_targets]

    assert annotation.tip != pytest.approx(mouth_page)
    assert any(annotation.tip == pytest.approx(target) for target in material_page_targets)
    assert not [
        issue
        for issue in drawing.lint()
        if issue.code in {"round_bottom_blind_slot_dropped", "annotation_overlap"}
    ]


def test_live_and_deferred_callout_verbs_reuse_the_same_renderer() -> None:
    expected_parameters = {
        "round_bottom_blind_slot_length.length",
        "round_bottom_blind_slot_flat_width.length",
        "round_bottom_blind_slot_radius.radius",
    }

    signatures = []
    for mode in ("live", "deferred"):
        drawing = build_drawing(_part())
        feature = next(
            feature
            for feature in drawing.model().features
            if feature.kind == "round_bottom_blind_slot"
        )
        drawing.drop(feature)

        if mode == "live":
            name = drawing.callout(feature)
        else:
            with drawing.deferred():
                assert drawing.callout(feature) == ""
            name = next(iter(drawing.annotations_of(feature)))

        annotations = drawing.annotations_of(feature)
        assert list(annotations) == [name]
        assert annotations[name].label == "ROUND-BOTTOM OPEN SLOT 4 BOTTOM FLAT × R3 × 20 LONG"
        assert drawing.view_of(name) == "front"
        parameter_ids = {key["parameter_id"] for key in drawing.measurement_keys(name)}
        assert parameter_ids == expected_parameters
        signatures.append((annotations[name].label, drawing.view_of(name), parameter_ids))
        if mode == "deferred":
            assert drawing.lint() == []
        else:
            assert not [
                issue
                for issue in drawing.lint()
                if issue.code == "round_bottom_blind_slot_dropped"
            ]

    assert signatures[0] == signatures[1]


def test_renderer_fails_closed_for_an_excluded_feature_or_missing_view(monkeypatch) -> None:
    drawing = build_drawing(_part())
    plan = compile_dimensions(drawing.model())

    def collected() -> PlacementContext:
        return PlacementContext(
            registry=drawing.registry,
            coverage=drawing.coverage,
            items=drawing.items,
            feature_leaders=[],
        )

    excluded = collected()
    from_model.render_round_bottom_blind_slots(drawing, plan, None, ctx=excluded, only=set())
    assert excluded.feature_leaders == []

    with monkeypatch.context() as patch:
        patch.setattr(from_model, "_END_ON", {})
        unmapped = collected()
        from_model.render_round_bottom_blind_slots(drawing, plan, None, ctx=unmapped)
        assert unmapped.feature_leaders == []

    with monkeypatch.context() as patch:
        patch.setattr(drawing, "view_bounds", lambda _view: None)
        absent = collected()
        from_model.render_round_bottom_blind_slots(drawing, plan, None, ctx=absent)
        assert absent.feature_leaders == []


def test_generated_block_preserves_a_role_specific_tolerance_after_the_call() -> None:
    source = _record()
    sheet = Sheet(_part()).authored_dimensions()
    handle = sheet.round_bottom_blind_slot(
        axis=source.axis,
        open_sign=source.open_sign,
        length=source.length,
        width_axis=source.width_axis,
        depth_axis=source.depth_axis,
        depth_sign=source.depth_sign,
        radius=source.radius,
        flat_width=source.flat_width,
        at=source.at,
    )
    handle.tolerance(0, 0.1, on="round_bottom_blind_slot_radius.radius")
    original = sheet.model()
    lines, _names = _feature_block(original.features, decorations=original.decorations)
    declaration = next(line for line in lines if "sheet.round_bottom_blind_slot(" in line)
    assert ").tolerance(0, 0.1, on='round_bottom_blind_slot_radius.radius')" in declaration
    assert declaration.index(".tolerance(") < declaration.index("   # round-bottom open slot")

    replay = Sheet(_part()).authored_dimensions()
    exec(declaration, {"sheet": replay})  # noqa: S102
    rebuilt = replay.model()
    assert rebuilt.features == original.features
    assert rebuilt.decorations == original.decorations


def test_raw_native_and_framed_rigid_motion_preserve_sizes_and_drawing_semantics() -> None:
    raw = build_drawing(_part())
    moved = _part().rotate(Axis.X, 23).rotate(Axis.Z, 31)
    framed = build_drawing(moved, framed_recognition=True)

    raw_feature = next(
        feature for feature in raw.model().features if feature.kind == "round_bottom_blind_slot"
    )
    framed_feature = next(
        feature for feature in framed.model().features if feature.kind == "round_bottom_blind_slot"
    )
    assert (raw_feature.length, raw_feature.radius, raw_feature.flat_width) == (
        framed_feature.length,
        framed_feature.radius,
        framed_feature.flat_width,
    )
    raw_labels = {annotation.label for annotation in raw.annotations_of(raw_feature).values()}
    framed_labels = {
        annotation.label for annotation in framed.annotations_of(framed_feature).values()
    }
    assert raw_labels == framed_labels == {"ROUND-BOTTOM OPEN SLOT 4 BOTTOM FLAT × R3 × 20 LONG"}
    raw_name = next(iter(raw.annotations_of(raw_feature)))
    framed_name = next(iter(framed.annotations_of(framed_feature)))
    assert (
        {key["parameter_id"] for key in raw.measurement_keys(raw_name)}
        == {key["parameter_id"] for key in framed.measurement_keys(framed_name)}
        == {
            "round_bottom_blind_slot_length.length",
            "round_bottom_blind_slot_flat_width.length",
            "round_bottom_blind_slot_radius.radius",
        }
    )


@pytest.mark.parametrize(
    "change",
    [
        {"axis": "x", "width_axis": "x"},
        {"axis": ""},
        {"axis": "xy"},
        {"width_axis": "yz"},
        {"open_sign": 0},
        {"depth_sign": True},
        {"flat_width": True},
        {"flat_width": "10"},
        {"flat_width": 0},
        {"length": float("inf")},
        {"length": Fraction(10**10_000, 1)},
        {"radius": float("nan")},
        {"radius": "not-a-number"},
        {"frame": Frame(("0", 0, 0), "z")},
        {"frame": Frame([0, 7.5, 10], "z")},
        {"frame": Frame((Fraction(10**10_000, 1), 0, 0), "z")},
        {"frame": Frame((0, float("nan"), 0), "z")},
    ],
)
def test_ir_rejects_malformed_axes_signs_and_sizes(change) -> None:
    valid = _declared()
    with pytest.raises(ValueError, match="round-bottom blind slot"):
        replace(valid, **change)


def test_injected_public_record_uses_the_same_converter_and_validation() -> None:
    source = _record()
    model = build_part_model(_part(), round_bottom_blind_slots=(source,))
    assert _declared(source) in model.features

    malformed = replace(source, radius=-1)
    with pytest.raises(ValueError, match="round-bottom blind slot radius"):
        build_part_model(_part(), round_bottom_blind_slots=(malformed,))


@pytest.mark.parametrize(
    "change",
    [
        {"flat_width": "10"},
        {"length": Fraction(10**10_000, 1)},
        {"at": ("0", 7.5, 10)},
        {"at": [0, 7.5, 10]},
        {"at": (Fraction(10**10_000, 1), 7.5, 10)},
    ],
)
def test_injected_public_record_rejects_schema_coercions(change) -> None:
    with pytest.raises(ValueError, match="round-bottom blind slot"):
        build_part_model(_part(), round_bottom_blind_slots=(replace(_record(), **change),))


def test_hand_built_ir_requires_frame_and_run_axes_to_agree() -> None:
    with pytest.raises(ValueError, match="frame axis must equal"):
        RoundBottomBlindSlotFeature(
            frame=Frame((0, 0, 0), "x"),
            axis="y",
            open_sign=1,
            width_axis="x",
            depth_axis="z",
            depth_sign=-1,
            length=12,
            radius=3,
            flat_width=6,
        )


def test_ir_has_no_implicit_datum_references() -> None:
    assert _declared().references() == []
