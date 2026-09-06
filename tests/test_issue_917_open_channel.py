"""#917: an open U-channel is one fact with a non-redundant defining chain."""

from dataclasses import replace

import pytest
from build123d import Align, Box, Cylinder, Pos, Rot
from quiddity import build_raw_recognition_result

from draftwright import build_drawing
from draftwright.builder import detect_part_model
from draftwright.model import ChannelFeature, HoleFeature, PartModel, plan_dimensions
from draftwright.section_recess_contract import section_recess_fields


def _recesses(part, *, kind):
    return [
        source
        for source in build_raw_recognition_result(part).section_recesses
        if source.classification.feature_kind == kind
    ]


def _u_channel(*, lower_wall=12.5, upper_wall=12.5, pierced=True):
    width = 50.0
    lower_center = -width / 2 + lower_wall / 2
    upper_center = width / 2 - upper_wall / 2
    part = (
        Box(50, width, 12)
        + Pos(0, lower_center, 15) * Box(50, lower_wall, 18)
        + Pos(0, upper_center, 15) * Box(50, upper_wall, 18)
    )
    if not pierced:
        return part
    part -= Cylinder(2, 12)
    part -= Pos(0, 0, 4) * Cylinder(6, 4)
    part -= (
        Pos(0, 0, 15)
        * Rot(90, 0, 0)
        * Cylinder(4, 60, align=(Align.CENTER, Align.CENTER, Align.CENTER))
    )
    return part


def _labels(drawing, prefix):
    return {
        name: drawing.get_annotation(name).label
        for name in drawing.annotations()
        if name.startswith(prefix)
    }


@pytest.mark.xfail(
    strict=True,
    reason="Quiddity 0.2.2 known limitation: https://github.com/pzfreo/quiddity/issues/538",
)
def test_corrected_fixture_has_one_channel_and_one_independent_wall_thickness():
    part = _u_channel()
    channels = _recesses(part, kind="channel")
    assert len(channels) == 1
    fields = section_recess_fields(channels[0])[1]
    assert tuple(
        fields[key] for key in ("long_axis", "width_axis", "width", "lo", "hi", "d_lo", "d_hi")
    ) == ("x", "y", 25.0, -25.0, 25.0, 6.0, 24.0)
    assert _recesses(part, kind="pocket") == []

    drawing = build_drawing(part)
    detected = detect_part_model(_u_channel())
    assert tuple(detected.features) == tuple(drawing.model().features)
    (channel,) = [
        feature for feature in drawing.model().features if isinstance(feature, ChannelFeature)
    ]
    assert channel.width == 25.0
    assert _labels(drawing, "dim_channel") == {"dim_channel_y0": "25"}
    assert _labels(drawing, "dim_plate_y") == {"dim_plate_y0": "12.5"}
    assert _labels(drawing, "m_env_") == {"m_env_width": "50", "m_env_depth": "50"}
    assert drawing.measurement_keys("dim_channel_y0")[0]["parameter_id"] == (
        "channel_width.length"
    )

    holes = [feature for feature in drawing.model().features if isinstance(feature, HoleFeature)]
    base_hole = next(feature for feature in holes if feature.frame.axis == "z")
    cross_hole = next(feature for feature in holes if feature.frame.axis == "y")
    assert (base_hole.diameter, base_hole.through, base_hole.cbore) == (4.0, True, (12.0, 4.0))
    assert (cross_hole.diameter, cross_hole.through) == (8.0, True)

    first = [(issue.severity, issue.code) for issue in drawing.lint()]
    second = [(issue.severity, issue.code) for issue in drawing.lint()]
    # `step_dim_withheld` (#1216): the channel floor is a step level whose page span is below
    # the dimensioning floor, so the compiler approves a rung the sheet does not carry. The
    # point of this pair of reads is that lint is IDEMPOTENT, which the equality still asserts.
    assert first == second == [("info", "step_dim_withheld")]


@pytest.mark.xfail(
    strict=True,
    reason="Quiddity 0.2.2 known limitation: https://github.com/pzfreo/quiddity/issues/538",
)
def test_every_independent_channel_chain_measurement_has_actionable_lint():
    expected = {
        "dim_channel_y0": "channel.channel_width.length",
        "dim_plate_y0": "plate.thickness.length",
        "m_env_depth": "envelope.depth.length",
    }
    for name, role in expected.items():
        drawing = build_drawing(_u_channel())
        drawing.remove(name)
        issues = [issue for issue in drawing.lint() if issue.code == "channel_requirement_missing"]
        assert len(issues) == 1
        assert role in issues[0].message
        assert "open channel" in issues[0].message


@pytest.mark.xfail(
    strict=True,
    reason="Quiddity 0.2.2 known limitation: https://github.com/pzfreo/quiddity/issues/538",
)
def test_asymmetric_walls_keep_lower_wall_and_derive_the_opposite_wall():
    drawing = build_drawing(_u_channel(lower_wall=10.0, upper_wall=15.0))
    assert _labels(drawing, "dim_channel") == {"dim_channel_y0": "25"}
    assert _labels(drawing, "dim_plate_y") == {"dim_plate_y0": "10"}
    assert not [issue for issue in drawing.lint() if issue.severity != "info"]


def test_ordinary_bounded_pocket_does_not_become_a_channel():
    part = Box(80, 60, 20) - Pos(0, 0, 6) * Box(30, 20, 8)
    assert _recesses(part, kind="channel") == []
    assert len(_recesses(part, kind="pocket")) == 1


def test_monolithic_centered_rebate_stays_with_the_step_ladder():
    part = Box(80, 60, 30) - Pos(0, 0, 7.5) * Box(80, 20, 15)
    assert len(_recesses(part, kind="channel")) == 1  # geometry census remains honest
    drawing = build_drawing(part)
    assert not [feature for feature in drawing.model().features if feature.kind == "channel"]
    assert sorted(_labels(drawing, "dim_shoulder").values()) == ["20", "40"]
    assert not [issue for issue in drawing.lint() if issue.severity != "info"]


def test_shorter_walls_retain_the_actual_channel_run_and_both_wall_dimensions():
    part = (
        Box(50, 50, 12)
        + Pos(0, -18.75, 15) * Box(40, 12.5, 18)
        + Pos(0, 18.75, 15) * Box(40, 12.5, 18)
    )
    drawing = build_drawing(part)
    (source,) = drawing.recognition().section_recesses
    assert source.classification.feature_kind == "channel"
    kind, fields = section_recess_fields(source)
    assert kind == "channel"
    assert tuple(fields[k] for k in ("lo", "hi", "width", "d_lo", "d_hi")) == (-20, 20, 25, 6, 24)
    (channel,) = [f for f in drawing.model().features if isinstance(f, ChannelFeature)]
    assert (channel.lo, channel.hi, channel.width) == (-20, 20, 25)
    assert _labels(drawing, "dim_channel") == {"dim_channel_y0": "25"}
    assert _labels(drawing, "dim_plate_y") == {"dim_plate_y0": "12.5", "dim_plate_y1": "12.5"}
    assert sorted(_labels(drawing, "m_pad").values()) == [
        "12.5",
        "12.5",
        "18 HIGH",
        "18 HIGH",
        "40",
        "40",
    ]
    assert not [f for f in drawing.model().features if f.kind == "pocket"]
    assert not [i for i in drawing.lint() if i.code.startswith("channel_requirement_")]


def test_one_shortened_end_does_not_invent_a_channel_record():
    part = (
        Box(50, 50, 12)
        + Pos(-2.5, -18.75, 15) * Box(45, 12.5, 18)
        + Pos(-2.5, 18.75, 15) * Box(45, 12.5, 18)
    )
    assert _recesses(part, kind="channel") == []


def test_channel_requires_a_floor_and_opposed_inner_walls():
    assert _recesses(Box(50, 50, 12), kind="channel") == []
    through_gap = Box(50, 50, 30) - Box(50, 25, 30)
    assert _recesses(through_gap, kind="channel") == []


def test_channel_width_places_for_principal_axis_rotations_and_both_open_signs():
    base = (
        Box(50, 50, 12)
        + Pos(0, -18.75, 15) * Box(50, 12.5, 18)
        + Pos(0, 18.75, 15) * Box(50, 12.5, 18)
    )
    inverted = (
        Box(50, 50, 12)
        + Pos(0, -18.75, -15) * Box(50, 12.5, 18)
        + Pos(0, 18.75, -15) * Box(50, 12.5, 18)
    )
    for part in (base, Rot(0, 90, 0) * base, Rot(90, 0, 0) * base, inverted):
        drawing = build_drawing(part)
        assert list(_labels(drawing, "dim_channel").values()) == ["25"]
        assert not [issue for issue in drawing.lint() if issue.severity != "info"]


def test_wrong_channel_identity_cannot_clear_the_physical_transition_warning():
    part = _u_channel()
    detected = detect_part_model(part)
    features = [
        replace(feature, d_hi=feature.d_hi - 1) if isinstance(feature, ChannelFeature) else feature
        for feature in detected.features
    ]
    declared = PartModel(
        bbox=detected.bbox,
        orientation=detected.orientation,
        features=features,
        datums=list(detected.datums),
    )
    drawing = build_drawing(part, model=declared)
    transitions = [
        issue for issue in drawing.lint() if issue.code == "unrecognised_defining_geometry"
    ]
    assert len(transitions) == 1
    assert "2 slanted/stepped profile transition(s)" in transitions[0].message


def test_declared_non_full_span_channel_does_not_suppress_a_wall():
    detected = detect_part_model(_u_channel(pierced=False))
    assert len([f for f in detected.features if isinstance(f, ChannelFeature)]) == 1
    original_plate_dims = [
        dimension
        for group in plan_dimensions(detected)
        if group.feature_kind == "plate" and group.feature.axis == "y"
        for dimension in group.dims
    ]
    assert len(original_plate_dims) == 2
    assert sum(d.suppressed for d in original_plate_dims) == 1
    features = [
        replace(feature, lo=feature.lo + 1, hi=feature.hi - 1)
        if isinstance(feature, ChannelFeature)
        else feature
        for feature in detected.features
    ]
    model = PartModel(
        bbox=detected.bbox,
        orientation=detected.orientation,
        features=features,
        datums=list(detected.datums),
    )
    plate_dims = [
        dimension
        for group in plan_dimensions(model)
        if group.feature_kind == "plate" and group.feature.axis == "y"
        for dimension in group.dims
    ]
    assert len(plate_dims) == 2
    assert not any(dimension.suppressed for dimension in plate_dims)
