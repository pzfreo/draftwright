"""Signed pad drawing, dimension, and corridor behavior."""

from __future__ import annotations

import pytest
from _pad_rendering_fixtures import _box, _signed_pad

import draftwright.analysis as analysis_mod
from draftwright import Drawing, Sheet, build_drawing
from draftwright.model import PadFeature, ViewPlanIncomplete
from draftwright.model.planner import plan_dimensions
from draftwright.sheet_emit import emit_sheet_script

_END_ON = {"x": "side", "y": "front", "z": "plan"}


@pytest.mark.parametrize("axis", ["x", "y", "z"])
@pytest.mark.parametrize("direction", [-1, 1])
def test_all_signed_pad_records_reach_complete_solver_owned_drawings(axis, direction):
    part = _signed_pad(axis, direction)
    drawing = build_drawing(part)
    (record,) = drawing.recognition().pads
    pad = next(feature for feature in drawing.model().features if isinstance(feature, PadFeature))

    assert (record.axis, record.direction) == (axis, direction)
    assert (pad.frame.axis, pad.direction) == (axis, direction)
    assert {pad.frame.axis, pad.long_axis, pad.width_axis} == {"x", "y", "z"}
    assert tuple(pad.bounds(name) for name in "xyz") == (
        (record.x0, record.x1),
        (record.y0, record.y1),
        (record.z0, record.z1),
    )
    expected_axes = {"x": ("y", "z"), "y": ("z", "x"), "z": ("x", "y")}
    assert (pad.long_axis, pad.width_axis) == expected_axes[axis]
    record_bounds = {
        "x": (record.x0, record.x1),
        "y": (record.y0, record.y1),
        "z": (record.z0, record.z1),
    }
    parameters = {parameter.parameter_id: parameter for parameter in pad.parameters()}
    assert parameters["pad_length.length"].value == pytest.approx(
        record_bounds[pad.long_axis][1] - record_bounds[pad.long_axis][0]
    )
    assert parameters["pad_width.length"].value == pytest.approx(
        record_bounds[pad.width_axis][1] - record_bounds[pad.width_axis][0]
    )
    attachment = list(pad.frame.origin)
    terminal = list(pad.frame.origin)
    normal_index = "xyz".index(axis)
    attachment[normal_index] = record_bounds[axis][0 if direction > 0 else 1]
    terminal[normal_index] = record_bounds[axis][1 if direction > 0 else 0]
    assert parameters["pad_height.length"].span == (tuple(attachment), tuple(terminal))
    assert plan_dimensions(drawing.model())[0].view == _END_ON[axis]

    expected = {"pad_width.length", "pad_length.length", "pad_height.length"}
    expected_locations = (
        {"location_pad.location"}
        if axis == "z"
        else {f"location_pad.{pad.long_axis}", f"location_pad.{pad.width_axis}"}
    )
    placed = {
        key["parameter_id"]
        for name in drawing.annotations_of(pad)
        for key in drawing.measurement_keys(name)
    }
    assert expected | expected_locations <= placed
    assert not [issue for issue in drawing.lint() if issue.severity in {"warning", "error"}]


@pytest.mark.parametrize("axis", ["x", "y", "z"])
@pytest.mark.parametrize("direction", [-1, 1])
def test_signed_pad_sheet_code_executes_with_exact_ir_and_measurement_parity(axis, direction):
    part = _signed_pad(axis, direction)
    direct = build_drawing(part)
    source = emit_sheet_script(direct.model(), "part", "out", title="PAD", number="1392")

    assert f"axis='{axis}'" in source
    assert f"direction={direction}" in source
    marker = "\ndrawing = sheet.build()"
    assert source.count(marker) == 1
    namespace = {"part": part}
    exec(source[: source.index(marker)] + "\nrebuilt = sheet.build()\n", namespace)  # noqa: S102
    rebuilt: Drawing = namespace["rebuilt"]

    direct_pad = next(feature for feature in direct.model().features if feature.kind == "pad")
    rebuilt_pad = next(feature for feature in rebuilt.model().features if feature.kind == "pad")
    assert rebuilt_pad == direct_pad

    def evidence(drawing, pad):
        return sorted(
            (drawing.view_of(name), drawing.get_annotation(name).label, key["parameter_id"])
            for name in drawing.annotations_of(pad)
            for key in drawing.measurement_keys(name)
        )

    assert evidence(rebuilt, rebuilt_pad) == evidence(direct, direct_pad)


@pytest.mark.parametrize(
    "parameter",
    [
        "pad_width.length",
        "pad_length.length",
        "pad_height.length",
        "location_pad.y",
        "location_pad.z",
    ],
)
def test_each_missing_side_pad_requirement_has_an_explicit_lint_outcome(parameter):
    drawing = build_drawing(_signed_pad("x", 1))
    pad = next(feature for feature in drawing.model().features if feature.kind == "pad")
    name = next(
        name
        for name in drawing.annotations_of(pad)
        if parameter in {key["parameter_id"] for key in drawing.measurement_keys(name)}
    )

    drawing.remove(name)

    issues = [issue for issue in drawing.lint() if issue.code == "pad_footprint_not_defined"]
    assert len(issues) == 1
    assert "height" in issues[0].message and "in-plane location" in issues[0].message


@pytest.mark.parametrize("direction", [-1, 1])
def test_missing_z_pad_height_has_an_explicit_lint_outcome(direction):
    drawing = build_drawing(_signed_pad("z", direction))
    pad = next(feature for feature in drawing.model().features if feature.kind == "pad")
    name = next(
        name
        for name in drawing.annotations_of(pad)
        if "pad_height.length" in {key["parameter_id"] for key in drawing.measurement_keys(name)}
    )

    drawing.remove(name)

    issues = [issue for issue in drawing.lint() if issue.code == "pad_footprint_not_defined"]
    assert len(issues) == 1
    assert "height" in issues[0].message


@pytest.mark.parametrize("view", ["plan", "side"])
def test_each_missing_z_pad_location_ordinate_has_an_explicit_lint_outcome(view):
    drawing = build_drawing(_signed_pad("z", 1))
    pad = next(feature for feature in drawing.model().features if feature.kind == "pad")
    name = next(
        name
        for name in drawing.annotations_of(pad)
        if drawing.view_of(name) == view
        and "location_pad.location"
        in {key["parameter_id"] for key in drawing.measurement_keys(name)}
    )

    drawing.remove(name)

    issues = [issue for issue in drawing.lint() if issue.code == "pad_footprint_not_defined"]
    assert len(issues) == 1
    assert "in-plane location" in issues[0].message


def test_side_pad_height_and_footprint_require_the_end_on_view():
    drawing = build_drawing(_signed_pad("x", 1))

    with pytest.raises(ViewPlanIncomplete) as caught:
        plan_dimensions(drawing.model(), planned_views=("front", "plan"))

    uncovered = {(item.identity.parameter, item.preferred_view) for item in caught.value.uncovered}
    assert {
        ("pad_width.length", "side"),
        ("pad_length.length", "side"),
        ("pad_height.length", "side"),
        ("location_pad.location", "side"),
    } <= uncovered


def test_x_pad_compose_reserves_both_end_on_dimension_bands():
    """Scale selection must account for the side view's outward right corridor."""
    part = _box((10, 20, 20), (0, 0, 0)) + _box((5, 9, 9), (10, 4, 4))

    drawing = build_drawing(part)
    pad = next(feature for feature in drawing.model().features if feature.kind == "pad")
    placed = {
        key["parameter_id"]
        for name in drawing.annotations_of(pad)
        for key in drawing.measurement_keys(name)
    }

    assert {
        "pad_width.length",
        "pad_length.length",
        "pad_height.length",
        "location_pad.y",
        "location_pad.z",
    } <= placed
    assert not [issue for issue in drawing.lint() if issue.severity in {"warning", "error"}]


def test_four_flat_polygonal_occurrence_shares_its_exact_pad_owner():
    from dataclasses import replace

    from draftwright.linting.pad_coverage import pad_requirement_outcomes
    from draftwright.linting.polygonal_boss_coverage import (
        _square_pad_owner,
        polygonal_boss_requirement_outcomes,
    )
    from draftwright.measurement_support import square_polygonal_boss_pad_owner

    part = _box((10, 20, 20), (0, 0, 0)) + _box((5, 9, 9), (10, 4, 4))
    drawing = build_drawing(part)
    evidence = drawing.recognition_evidence()
    ownership = drawing.recognition_ownership()
    assert evidence is not None and ownership is not None
    boss_occurrences = tuple(
        item for item in evidence.features if evidence.family(item) == "polygonal_bosses"
    )
    pad_occurrences = tuple(item for item in evidence.features if evidence.family(item) == "pads")
    assert len(boss_occurrences) == len(pad_occurrences) == 1
    boss_faces = evidence.defining_faces(boss_occurrences[0])
    assert evidence.record(boss_occurrences[0]).side_count == 4
    assert boss_faces and boss_faces < evidence.defining_faces(pad_occurrences[0])
    assert (
        square_polygonal_boss_pad_owner(
            replace(evidence.record(boss_occurrences[0])), drawing.recognition().pads, evidence
        )
        is None
    )

    boss_binding = ownership.binding_for(boss_occurrences[0])
    pad_binding = ownership.binding_for(pad_occurrences[0])
    assert boss_binding is not None and pad_binding is not None
    assert boss_binding.reason_code == "polygonal_boss_pad_owner"
    assert boss_binding.feature is pad_binding.feature
    assert not [
        feature for feature in drawing.model().features if feature.kind == "polygonal_boss"
    ]
    outcomes = polygonal_boss_requirement_outcomes(
        drawing.recognition(), drawing.model().features, drawing.registry, evidence=evidence
    )
    assert outcomes == []
    copied_boss = replace(evidence.record(boss_occurrences[0]))
    assert _square_pad_owner(evidence.record(boss_occurrences[0]), drawing.recognition(), evidence)
    assert _square_pad_owner(copied_boss, drawing.recognition(), evidence) is None
    copied_recognition = replace(drawing.recognition(), polygonal_bosses=(copied_boss,))
    copied_outcomes = polygonal_boss_requirement_outcomes(
        copied_recognition, (), drawing.registry, evidence=evidence
    )
    assert len(copied_outcomes) == 1
    assert copied_outcomes[0].state == "unverifiable"
    assert copied_outcomes[0].requirement_count == 2
    assert copied_outcomes[0].source_records == (copied_boss,)
    pad_outcomes = pad_requirement_outcomes(
        drawing.recognition(), drawing.model().features, drawing.registry
    )
    assert {outcome.parameter_id: outcome.state for outcome in pad_outcomes}.items() >= {
        "pad_width.length": "placed",
        "pad_length.length": "placed",
        "pad_height.length": "placed",
    }.items()
    without_pad = tuple(feature for feature in drawing.model().features if feature.kind != "pad")
    assert (
        polygonal_boss_requirement_outcomes(
            drawing.recognition(), without_pad, drawing.registry, evidence=evidence
        )
        == []
    )
    assert "unverifiable" in {
        outcome.state
        for outcome in pad_requirement_outcomes(
            drawing.recognition(), without_pad, drawing.registry
        )
    }


def test_x_pad_side_strip_consumes_its_reserved_band_at_scale_two():
    """The side strip starts at geometry, not beyond its reserved outer footprint."""
    part = _box((10, 12, 12), (0, 0, 0)) + _box((5, 5.4, 5.4), (10, 2.4, 2.4))

    drawing = build_drawing(part)
    pad = next(feature for feature in drawing.model().features if feature.kind == "pad")
    placed = {
        key["parameter_id"]
        for name in drawing.annotations_of(pad)
        for key in drawing.measurement_keys(name)
    }

    assert drawing.scale == 2
    assert {
        "pad_width.length",
        "pad_length.length",
        "pad_height.length",
        "location_pad.y",
        "location_pad.z",
    } <= placed
    assert not [issue for issue in drawing.lint() if issue.severity in {"warning", "error"}]


def test_x_pad_height_leader_stays_out_of_adjacent_front_view_ink():
    """A side-view HIGH label must not enter the front/side annotation corridor."""
    part = _box((10, 60, 16), (0, 0, 0)) + _box((5, 27, 7.2), (10, 12, 3.2))

    drawing = build_drawing(part)

    assert not [issue for issue in drawing.lint() if issue.severity in {"warning", "error"}]


def test_mixed_x_and_opposed_z_pads_keep_their_own_location_ordinates():
    """An off-axis pad cannot become the datum source for the Z-pad location ladder."""
    part = (
        _box((40, 40, 20), (0, 0, 0))
        + _box((5, 10, 8), (40, 5, 5))
        + _box((10, 8, 5), (20, 25, -5))
        + _box((10, 8, 5), (20, 25, 20))
    )

    drawing = build_drawing(part)
    locations = {
        drawing.view_of(name): (annotation.label, drawing.measurement_keys(name))
        for name, annotation in drawing.iter_annotations()
        if name.startswith("m_loc")
    }

    assert {view: label for view, (label, _keys) in locations.items()} == {
        "plan": "25",
        "side": "29",
    }
    for _label, keys in locations.values():
        assert len(keys) == 2
        assert {key["parameter_id"] for key in keys} == {"location_pad.location"}
        assert all("/z[" in key["feature"] for key in keys)
    assert not [issue for issue in drawing.lint() if issue.severity in {"warning", "error"}]


@pytest.mark.parametrize("axis", ["x", "y", "z"])
@pytest.mark.parametrize("direction", [-1, 1])
def test_authored_pad_height_does_not_require_suppressed_footprint_measurements(axis, direction):
    part = _signed_pad(axis, direction)
    automatic = build_drawing(part)
    source_pad = next(feature for feature in automatic.model().features if feature.kind == "pad")
    x0, x1 = source_pad.bounds("x")
    y0, y1 = source_pad.bounds("y")
    z0, z1 = source_pad.bounds("z")
    sheet = Sheet(part).authored_dimensions()
    handle = sheet.pad(
        x0=x0,
        x1=x1,
        y0=y0,
        y1=y1,
        z0=z0,
        z1=z1,
        axis=axis,
        direction=direction,
        at=source_pad.frame.origin,
    )
    handle.tolerance(0.1, on="pad_height")
    sheet.dimension(handle, "pad_height.length")

    drawing = sheet.build()
    pad = next(feature for feature in drawing.model().features if feature.kind == "pad")
    height_names = [
        name
        for name in drawing.annotations_of(pad)
        if "pad_height.length" in {key["parameter_id"] for key in drawing.measurement_keys(name)}
    ]

    assert len(height_names) == 1
    assert drawing.get_annotation(height_names[0]).label == "5 ±0.1 HIGH"

    source = emit_sheet_script(drawing.model(), "part", "out", title="PAD", number="1392")
    marker = "\ndrawing = sheet.build()"
    namespace = {"part": part}
    exec(source[: source.index(marker)] + "\nrebuilt = sheet.build()\n", namespace)  # noqa: S102
    rebuilt: Drawing = namespace["rebuilt"]
    rebuilt_pad = next(feature for feature in rebuilt.model().features if feature.kind == "pad")
    rebuilt_height = next(
        name
        for name in rebuilt.annotations_of(rebuilt_pad)
        if "pad_height.length" in {key["parameter_id"] for key in rebuilt.measurement_keys(name)}
    )
    assert rebuilt.get_annotation(rebuilt_height).label == "5 ±0.1 HIGH"


def test_authored_pad_width_does_not_restore_omitted_height():
    part = _signed_pad("x", 1)
    automatic = build_drawing(part)
    source_pad = next(feature for feature in automatic.model().features if feature.kind == "pad")
    assert "pad_height.length" in {parameter.parameter_id for parameter in source_pad.parameters()}
    x0, x1 = source_pad.bounds("x")
    y0, y1 = source_pad.bounds("y")
    z0, z1 = source_pad.bounds("z")

    sheet = Sheet(part).authored_dimensions()
    handle = sheet.pad(
        x0=x0,
        x1=x1,
        y0=y0,
        y1=y1,
        z0=z0,
        z1=z1,
        axis="x",
        direction=1,
        at=source_pad.frame.origin,
    )
    sheet.dimension(handle, "pad_width.length")
    drawing = sheet.build()
    pad = next(feature for feature in drawing.model().features if feature.kind == "pad")
    placed = {
        key["parameter_id"]
        for name in drawing.annotations_of(pad)
        for key in drawing.measurement_keys(name)
    }
    assert "pad_width.length" in placed
    assert "pad_height.length" not in placed
    assert not [name for name in drawing.annotations() if name.startswith("m_pad_height")]


def test_authored_pad_height_does_not_reserve_suppressed_side_pad_bands(monkeypatch):
    """Suppressed footprint/location marks cannot reduce the selected drawing scale."""
    measured_side_strips = []
    real_measure = analysis_mod._measure_strips

    def record_measure(*args, **kwargs):
        strips = real_measure(*args, **kwargs)
        measured_side_strips.append((strips.sv_top, strips.sv_right))
        return strips

    monkeypatch.setattr(analysis_mod, "_measure_strips", record_measure)
    part = _box((20, 20, 22), (0, 0, 0)) + _box((5, 6, 6), (20, 7, 8))
    sheet = Sheet(part, page="A4").authored_dimensions()
    pad = sheet.pad(
        x0=20,
        x1=25,
        y0=7,
        y1=13,
        z0=8,
        z1=14,
        axis="x",
        at=(22.5, 10, 11),
    )
    sheet.dimension(pad, "pad_height.length")

    drawing = sheet.build()
    drawn_pad = next(feature for feature in drawing.model().features if feature.kind == "pad")
    parameter_ids = {
        key["parameter_id"]
        for name in drawing.annotations_of(drawn_pad)
        for key in drawing.measurement_keys(name)
    }

    assert drawing.scale == 2
    assert measured_side_strips
    assert set(measured_side_strips) == {(0.0, 0.0)}
    assert parameter_ids == {"pad_height.length"}
    assert {issue.code for issue in drawing.lint() if issue.severity in {"warning", "error"}} == {
        "pad_footprint_not_defined",
        "pad_requirement_suppressed",
    }


def test_generated_sheet_preserves_each_independent_pad_tolerance():
    part = _signed_pad("z", 1)
    sheet = Sheet(part).authored_dimensions()
    handle = sheet.pad(x0=5, x1=20, y0=8, y1=18, z0=10, z1=15)
    for parameter, tolerance in (
        ("pad_width.length", 0.1),
        ("pad_length.length", 0.2),
        ("pad_height.length", 0.3),
    ):
        handle.tolerance(tolerance, on=parameter)
        sheet.dimension(handle, parameter)
    direct = sheet.build()

    source = emit_sheet_script(direct.model(), "part", "out", title="PAD", number="1392")
    for parameter in ("pad_width.length", "pad_length.length", "pad_height.length"):
        assert f"on={parameter!r}" in source
    marker = "\ndrawing = sheet.build()"
    namespace = {"part": part}
    exec(source[: source.index(marker)] + "\nrebuilt = sheet.build()\n", namespace)  # noqa: S102
    rebuilt: Drawing = namespace["rebuilt"]

    direct_pad = next(feature for feature in direct.model().features if feature.kind == "pad")
    rebuilt_pad = next(feature for feature in rebuilt.model().features if feature.kind == "pad")

    def labels(drawing, pad):
        return sorted(
            (key["parameter_id"], drawing.get_annotation(name).label)
            for name in drawing.annotations_of(pad)
            for key in drawing.measurement_keys(name)
            if key["parameter_id"].startswith("pad_")
        )

    assert labels(rebuilt, rebuilt_pad) == labels(direct, direct_pad)
