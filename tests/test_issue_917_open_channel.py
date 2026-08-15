"""#917: an open U-channel is one fact with a non-redundant defining chain."""

from dataclasses import replace

from b123d_recognisers import recognise_channels, recognise_pockets
from build123d import Align, Box, Cylinder, Pos, Rot

from draftwright import build_drawing
from draftwright.model import ChannelFeature, HoleFeature, PartModel, plan_dimensions


def _u_channel(*, lower_wall=12.5, upper_wall=12.5):
    width = 50.0
    lower_center = -width / 2 + lower_wall / 2
    upper_center = width / 2 - upper_wall / 2
    part = (
        Box(50, width, 12)
        + Pos(0, lower_center, 15) * Box(50, lower_wall, 18)
        + Pos(0, upper_center, 15) * Box(50, upper_wall, 18)
    )
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


def test_corrected_fixture_has_one_channel_and_one_independent_wall_thickness():
    part = _u_channel()
    channels = recognise_channels(part)
    assert len(channels) == 1
    assert (
        channels[0].long_axis,
        channels[0].width_axis,
        channels[0].width,
        channels[0].lo,
        channels[0].hi,
        channels[0].d_lo,
        channels[0].d_hi,
    ) == ("x", "y", 25.0, -25.0, 25.0, 6.0, 24.0)
    assert recognise_pockets(part) == []

    drawing = build_drawing(part)
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
    assert first == second == []


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


def test_asymmetric_walls_keep_lower_wall_and_derive_the_opposite_wall():
    drawing = build_drawing(_u_channel(lower_wall=10.0, upper_wall=15.0))
    assert _labels(drawing, "dim_channel") == {"dim_channel_y0": "25"}
    assert _labels(drawing, "dim_plate_y") == {"dim_plate_y0": "10"}
    assert not [issue for issue in drawing.lint() if issue.severity != "info"]


def test_ordinary_bounded_pocket_does_not_become_a_channel():
    part = Box(80, 60, 20) - Pos(0, 0, 6) * Box(30, 20, 8)
    assert recognise_channels(part) == []
    assert len(recognise_pockets(part)) == 1


def test_monolithic_centered_rebate_stays_with_the_step_ladder():
    part = Box(80, 60, 30) - Pos(0, 0, 7.5) * Box(80, 20, 15)
    assert len(recognise_channels(part)) == 1  # geometry census remains honest
    drawing = build_drawing(part)
    assert not [feature for feature in drawing.model().features if feature.kind == "channel"]
    assert sorted(_labels(drawing, "dim_shoulder").values()) == ["20", "40"]
    assert not [issue for issue in drawing.lint() if issue.severity != "info"]


def test_channel_must_reach_both_longitudinal_envelope_ends():
    for length, x_center in ((40, 0), (45, -2.5)):
        part = (
            Box(50, 50, 12)
            + Pos(x_center, -18.75, 15) * Box(length, 12.5, 18)
            + Pos(x_center, 18.75, 15) * Box(length, 12.5, 18)
        )
        assert recognise_channels(part) == []


def test_channel_requires_a_floor_and_opposed_inner_walls():
    assert recognise_channels(Box(50, 50, 12)) == []
    through_gap = Box(50, 50, 30) - Box(50, 25, 30)
    assert recognise_channels(through_gap) == []


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
    detected = build_drawing(part, auto_dims=False).model()
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
    detected = build_drawing(_u_channel(), auto_dims=False).model()
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
