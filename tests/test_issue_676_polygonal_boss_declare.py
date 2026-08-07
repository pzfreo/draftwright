"""#676 slice 2: declared and editable polygonal-boss parity."""

from __future__ import annotations

import math
from dataclasses import replace

import pytest
from build123d import Box, Pos, RegularPolygon, extrude

from draftwright import Sheet
from draftwright._geometry import plane_axes
from draftwright.model import PolygonalBossFeature, polygonal_boss
from draftwright.sheet_emit import _feature_line


def _evidence():
    across = 40 * math.cos(math.pi / 6)
    directions = tuple(
        (math.cos(angle), math.sin(angle), 0.0)
        for angle in (0, math.pi / 3, 2 * math.pi / 3, math.pi, 4 * math.pi / 3, 5 * math.pi / 3)
    )
    centres = tuple(
        (across / 2 * direction[0], across / 2 * direction[1], 20.0) for direction in directions
    )
    return across, directions, centres


def _kwargs():
    across, directions, centres = _evidence()
    return {
        "side_count": 6,
        "across_flats": across,
        "height": 30.0,
        "at": (0.0, 0.0, 20.0),
        "axis": "z",
        "flat_directions": directions,
        "flat_centres": centres,
    }


def _part():
    return Box(100, 80, 10) + Pos(0, 0, 5) * extrude(RegularPolygon(20, 6), 30)


def test_the_public_constructor_derives_span_and_normalises_the_axis():
    feature = polygonal_boss(**{**_kwargs(), "axis": "Z"})

    assert isinstance(feature, PolygonalBossFeature)
    assert feature.frame.axis == "z"
    assert feature.frame.origin == (0.0, 0.0, 20.0)
    assert feature.span == ((0.0, 0.0, 5.0), (0.0, 0.0, 35.0))
    assert [parameter.parameter_id for parameter in feature.parameters()] == [
        "polygon_across_flats.length",
        "boss_height.length",
    ]


def test_the_constructor_materialises_one_shot_evidence_iterables_once():
    kw = _kwargs()
    expected_directions = kw.pop("flat_directions")
    expected_centres = kw.pop("flat_centres")

    feature = polygonal_boss(
        **kw,
        flat_directions=(direction for direction in expected_directions),
        flat_centres=(centre for centre in expected_centres),
    )

    assert feature.flat_directions == expected_directions
    assert feature.flat_centres == expected_centres


def test_clockwise_and_counter_clockwise_regular_rings_are_equivalent_evidence():
    forward = polygonal_boss(**_kwargs())
    kw = _kwargs()
    kw["flat_directions"] = tuple(reversed(kw["flat_directions"]))
    kw["flat_centres"] = tuple(reversed(kw["flat_centres"]))

    reverse = polygonal_boss(**kw)

    assert reverse.across_flats == forward.across_flats


def test_the_ir_does_not_narrow_the_recognisers_documented_evidence_tolerance():
    kw = _kwargs()
    directions = list(kw["flat_directions"])
    centres = list(kw["flat_centres"])
    angle = math.radians(1.5)
    directions[0] = (math.cos(angle), math.sin(angle), 0.0)
    centres[0] = tuple(
        kw["at"][index] + (kw["across_flats"] / 2 + 0.15) * directions[0][index]
        for index in range(3)
    )
    centres[3] = tuple(
        kw["at"][index] + (kw["across_flats"] / 2 - 0.15) * directions[3][index]
        for index in range(3)
    )

    feature = polygonal_boss(
        **{**kw, "flat_directions": tuple(directions), "flat_centres": tuple(centres)}
    )

    assert feature.side_count == 6


def test_adjacent_angle_tolerance_cannot_accumulate_into_non_opposed_flats():
    kw = _kwargs()
    angles = tuple(math.radians(degrees) for degrees in (0, 62, 124, 186, 244, 302))
    directions = tuple((math.cos(angle), math.sin(angle), 0.0) for angle in angles)
    centres = tuple(
        tuple(kw["at"][index] + kw["across_flats"] / 2 * direction[index] for index in range(3))
        for direction in directions
    )

    with pytest.raises(ValueError, match="opposed pairs"):
        polygonal_boss(**{**kw, "flat_directions": directions, "flat_centres": centres})


def test_opposition_is_angular_not_sensitive_to_serialised_vector_magnitude():
    kw = _kwargs()
    kw["flat_directions"] = tuple(
        tuple(0.9995 * component for component in direction) for direction in kw["flat_directions"]
    )

    feature = polygonal_boss(**kw)

    assert feature.side_count == 6


@pytest.mark.parametrize("axis", "xyz")
def test_explicit_declaration_supports_each_axis_aligned_frame(axis):
    origin = (10.0, 20.0, 30.0)
    across = 24.0
    u, v = plane_axes(axis)
    directions = tuple(
        tuple(math.cos(angle) * u[i] + math.sin(angle) * v[i] for i in range(3))
        for angle in (0, math.pi / 2, math.pi, 3 * math.pi / 2)
    )
    centres = tuple(
        tuple(origin[i] + across / 2 * direction[i] for i in range(3)) for direction in directions
    )

    feature = polygonal_boss(
        side_count=4,
        across_flats=across,
        height=12,
        at=origin,
        axis=axis,
        flat_directions=directions,
        flat_centres=centres,
    )

    assert feature.frame.axis == axis
    assert feature.span[0]["xyz".index(axis)] == pytest.approx(origin["xyz".index(axis)] - 6)
    assert feature.span[1]["xyz".index(axis)] == pytest.approx(origin["xyz".index(axis)] + 6)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"side_count": 5}, "even side_count"),
        ({"side_count": 2}, "even side_count"),
        ({"side_count": True}, "even side_count"),
        ({"across_flats": 0.0}, "finite positive"),
        ({"across_flats": float("inf")}, "finite positive"),
        ({"across_flats": True}, "finite positive"),
        ({"height": None}, "finite positive"),
        ({"height": float("inf")}, "finite positive"),
        ({"frame": None}, "needs a Frame"),
        ({"frame": replace(polygonal_boss(**_kwargs()).frame, axis="?")}, "axis must be"),
        ({"frame": replace(polygonal_boss(**_kwargs()).frame, axis="")}, "axis must be"),
        ({"frame": replace(polygonal_boss(**_kwargs()).frame, axis=None)}, "axis must be"),
        ({"frame": replace(polygonal_boss(**_kwargs()).frame, origin=None)}, "finite 3-vector"),
        (
            {"frame": replace(polygonal_boss(**_kwargs()).frame, origin=(0.0, 20.0))},
            "finite 3-vector",
        ),
        (
            {"frame": replace(polygonal_boss(**_kwargs()).frame, origin=(False, 0.0, 20.0))},
            "finite 3-vector",
        ),
        (
            {"frame": replace(polygonal_boss(**_kwargs()).frame, origin=("bad", 0.0, 20.0))},
            "finite 3-vector",
        ),
        (
            {
                "frame": replace(
                    polygonal_boss(**_kwargs()).frame, origin=(float("nan"), 0.0, 20.0)
                )
            },
            "finite 3-vector",
        ),
        ({"span": None}, "two finite 3-vector endpoints"),
        ({"span": ((0.0, 0.0, 5.0),)}, "two finite 3-vector endpoints"),
        ({"span": ((0.0, 0.0, 5.0), (1.0, 0.0, 35.0))}, "axis line"),
        ({"span": ((0.0, 0.0, 6.0), (0.0, 0.0, 36.0))}, "centred on frame.origin"),
        ({"span": ((0.0, 0.0, 4.5), (0.0, 0.0, 35.5))}, "must equal height"),
        ({"flat_directions": _evidence()[1][:-1]}, "one direction"),
        ({"flat_directions": None}, "one direction"),
        ({"flat_centres": _evidence()[2][:-1]}, "one physical anchor"),
        ({"flat_centres": None}, "one physical anchor"),
        (
            {"flat_directions": ((0.0, 0.0, 1.0), *_evidence()[1][1:])},
            "unit vector perpendicular",
        ),
        (
            {"flat_centres": ((0.0, 0.0, 20.0), *_evidence()[2][1:])},
            "support plane",
        ),
        (
            {"flat_centres": ((*_evidence()[2][0][:2], 36.0), *_evidence()[2][1:])},
            "within the polygonal boss span",
        ),
        (
            {
                "flat_directions": (
                    _evidence()[1][0],
                    _evidence()[1][2],
                    _evidence()[1][1],
                    *_evidence()[1][3:],
                ),
                "flat_centres": (
                    _evidence()[2][0],
                    _evidence()[2][2],
                    _evidence()[2][1],
                    *_evidence()[2][3:],
                ),
            },
            "ordered as one regular polygon ring",
        ),
    ],
)
def test_every_invalid_ir_route_fails_at_the_one_waist(changes, message):
    feature = polygonal_boss(**_kwargs())

    with pytest.raises(ValueError, match=message):
        replace(feature, **changes)


def test_the_public_constructor_rejects_a_malformed_explicit_span_before_indexing_it():
    with pytest.raises(ValueError, match="span must contain two"):
        polygonal_boss(**_kwargs(), span=((0.0, 0.0, 5.0),))


def test_the_sheet_verb_returns_a_dimensionable_multi_parameter_handle():
    sheet = Sheet(_part()).authored_dimensions()
    handle = sheet.polygonal_boss(**_kwargs()).tolerance(0.1, on="polygon_across_flats")

    assert handle.dimension_ids() == (
        "boss_height.length",
        "polygon_across_flats.length",
    )
    sheet.dimension(handle, "polygon_across_flats.length")
    drawing = sheet.build()

    labels = [
        annotation.label
        for name, annotation in drawing.iter_annotations()
        if name.startswith("m_polygonal_boss_")
    ]
    assert labels == ["HEX 34.6 ±0.1 A/F"]
    assert not any(name.startswith("m_bossheight_") for name in drawing.annotations())


def test_declared_intent_can_name_an_octagon_without_weakening_automatic_recognition():
    side_count = 8
    radius = 20.0
    across = 2 * radius * math.cos(math.pi / side_count)
    directions = tuple(
        (math.cos(angle), math.sin(angle), 0.0)
        for angle in (2 * math.pi * index / side_count for index in range(side_count))
    )
    centres = tuple(
        (across / 2 * direction[0], across / 2 * direction[1], 20.0) for direction in directions
    )
    part = Box(100, 80, 10) + Pos(0, 0, 5) * extrude(RegularPolygon(radius, side_count), 30)
    sheet = Sheet(part).authored_dimensions()
    handle = sheet.polygonal_boss(
        side_count=side_count,
        across_flats=across,
        height=30,
        at=(0, 0, 20),
        axis="z",
        flat_directions=directions,
        flat_centres=centres,
    )
    sheet.dimension(handle, "polygon_across_flats.length")

    drawing = sheet.build()

    labels = [
        annotation.label
        for name, annotation in drawing.iter_annotations()
        if name.startswith("m_polygonal_boss_")
    ]
    assert labels == ["8-SIDED 37.0 A/F"]


def test_the_emitter_uses_the_public_verb_and_round_trips_every_field():
    kw = _kwargs()
    kw["across_flats"] = round(kw["across_flats"], 3)
    kw["flat_directions"] = tuple(
        tuple(round(component, 3) for component in direction)
        for direction in kw["flat_directions"]
    )
    kw["flat_centres"] = tuple(
        tuple(round(component, 3) for component in centre) for centre in kw["flat_centres"]
    )
    original = polygonal_boss(**kw)
    line = _feature_line(original)

    assert line.startswith("sheet.polygonal_boss(")
    assert "PolygonalBossFeature" not in line and "Frame(" not in line
    sheet = Sheet(_part())
    exec(compile(line, "<polygonal-boss-emit>", "exec"), {"sheet": sheet})  # noqa: S102

    assert sheet.features == [original]


def test_neither_public_declaration_surface_guesses_from_a_detached_prism():
    prism = extrude(RegularPolygon(20, 6), 30)

    with pytest.raises(TypeError):
        polygonal_boss(prism)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        Sheet(_part()).polygonal_boss(prism)  # type: ignore[arg-type]
