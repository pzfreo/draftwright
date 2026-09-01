"""#1382 — released step-family records survive the provider-owned frame end to end."""

from __future__ import annotations

from dataclasses import replace

import pytest
from build123d import Box, Cylinder, Plane, Polygon, Pos, Rot, extrude

from draftwright import build_drawing
from draftwright._geometry import quantised_radius_agrees, quantised_span_agrees
from draftwright.audit import diff_builds
from draftwright.linting.circular_blind_step_coverage import circular_blind_step_key
from draftwright.model import circular_blind_step
from draftwright.sheet_emit import emit_sheet_script


def _circular_blind_step_part():
    return Box(40, 30, 20) - Pos(7.5, 15, 10) * Rot(0, 90, 0) * Cylinder(4, 25)


def _offset_small_radius_circular_blind_step_part():
    radius = 0.5366612138572848
    centre = -42.84624946230272
    return Pos(0, centre - 15, 0) * (
        Box(40, 30, 20) - Pos(7.5, 15, 10) * Rot(0, 90, 0) * Cylinder(radius, 25)
    )


def _large_scale_small_radius_circular_blind_step_part():
    return Box(40, 3000, 20) - Pos(7.5, 1500, 10) * Rot(0, 90, 0) * Cylinder(0.4, 25)


def _paired_ramp_step_part():
    profile = Polygon((0, -8), (0, 8), (-10, 0))
    cutter = Pos(20, 20, 0) * extrude(Plane.XZ * profile, 25)
    return Box(40, 40, 30) - cutter


def _through_step_part():
    # Unequal 18/15 mm legs make an accidental local-axis swap visible in the final ink.
    return Box(40, 30, 20) - Pos(15, 7, 0) * Box(20, 20, 30)


_CASES = (
    pytest.param(
        _circular_blind_step_part,
        "circular_blind_steps",
        "circular_blind_step",
        "circular_blind_step",
        id="circular-blind-step",
    ),
    pytest.param(
        _paired_ramp_step_part,
        "paired_ramp_steps",
        "paired_ramp_step",
        "paired_ramp_step",
        id="paired-ramp-step",
    ),
    pytest.param(
        _through_step_part,
        "through_steps",
        "through_step",
        "through_step",
        id="through-step",
    ),
)

_NO_BUILD_DIFF = {
    "dimensions_lost": {},
    "dimensions_gained": {},
    "dimensions_changed": {},
    "measurements_substituted": {},
    "suppressions_gained": [],
    "suppressions_lost": [],
    "candidate_explanations": {},
}


def _features(drawing, kind: str):
    return tuple(feature for feature in drawing.model().features if feature.kind == kind)


def _semantic_parameter(parameter) -> tuple[str, float, str]:
    parameter_id = parameter.parameter_id
    if parameter_id.startswith("through_step_leg.length."):
        # A provider-owned local frame may relabel a physical leg's principal axis. The
        # manufacturing requirement is the leg length; its local discriminator remains checked
        # independently by the exact framed record/IR and compiler-backed ink comparisons.
        parameter_id = "through_step_leg.length"
    return (parameter_id, round(float(parameter.value), 6), parameter.role)


def _requirements(drawing, kind: str, *, exact: bool = False):
    return sorted(
        (
            (parameter.parameter_id, round(float(parameter.value), 6), parameter.role)
            if exact
            else _semantic_parameter(parameter)
        )
        for feature in _features(drawing, kind)
        for parameter in feature.parameters()
    )


def _ink(drawing, kind: str, *, exact: bool = False):
    rows = []
    for name, annotation in drawing.iter_annotations():
        measurements = tuple(
            measurement
            for measurement in drawing.registry.measurement_of(name)
            if getattr(measurement.feature, "kind", None) == kind
        )
        if measurements:
            rows.append(
                (
                    str(annotation.label),
                    tuple(
                        sorted(
                            measurement.parameter
                            if exact
                            else (
                                "through_step_leg.length"
                                if measurement.parameter.startswith("through_step_leg.length.")
                                else measurement.parameter
                            )
                            for measurement in measurements
                        )
                    ),
                )
            )
    return sorted(rows)


def _execute_generated_drawing(part, source: str):
    body = source.split("drawing.export(", 1)[0]
    namespace: dict[str, object] = {"part": part}
    exec(compile(body, "<framed-step-evidence>", "exec"), namespace)  # noqa: S102
    return namespace["drawing"]


def _generated_drawing(part, model):
    source = emit_sheet_script(model, "part", "framed-step", title="TEST", number="T-1382")
    return _execute_generated_drawing(part, source), source


@pytest.mark.parametrize(("make_part", "family", "kind", "sheet_word"), _CASES)
def test_raw_and_framed_step_families_preserve_requirements_dsl_and_ink_under_rigid_motion(
    make_part, family: str, kind: str, sheet_word: str
) -> None:
    part = make_part()
    moved_part = Pos(123, -47, 91) * Rot(17, 31, 23) * part

    raw = build_drawing(part)
    framed = build_drawing(part, framed_recognition=True)
    moved = build_drawing(moved_part, framed_recognition=True)

    assert raw.recognition_frame_decision["status"] == "raw"
    assert framed.recognition_frame_decision["status"] == "framed"
    assert moved.recognition_frame_decision["status"] == "framed"
    assert len(getattr(raw.recognition(), family)) == 1
    assert getattr(framed.recognition(), family) == getattr(moved.recognition(), family)

    raw_features = _features(raw, kind)
    framed_features = _features(framed, kind)
    moved_features = _features(moved, kind)
    assert len(raw_features) == len(framed_features) == 1
    assert framed_features == moved_features
    assert _requirements(raw, kind) == _requirements(framed, kind) == _requirements(moved, kind)
    assert _ink(raw, kind) == _ink(framed, kind) == _ink(moved, kind)
    assert raw.lint() == framed.lint() == moved.lint() == []
    assert diff_builds(framed, moved) == _NO_BUILD_DIFF

    generated, source = _generated_drawing(moved.working_part, moved.model())
    assert f"sheet.{sheet_word}(" in source
    assert (
        tuple(feature for feature in generated.model().features if feature.kind == kind)
        == moved_features
    )
    assert _requirements(generated, kind, exact=True) == _requirements(moved, kind, exact=True)
    assert _ink(generated, kind, exact=True) == _ink(moved, kind, exact=True)
    assert generated.lint() == []


def test_generated_through_step_replay_exposes_a_duplicated_leg_dimension() -> None:
    source_part = Pos(123, -47, 91) * Rot(17, 31, 23) * _through_step_part()
    drawing = build_drawing(source_part, framed_recognition=True)
    source = emit_sheet_script(
        drawing.model(), "part", "framed-step", title="TEST", number="T-1382"
    )
    names = [
        line.split(" = ", 1)[0] for line in source.splitlines() if "sheet.through_step(" in line
    ]
    assert len(names) == 1
    handle = names[0]
    dimensions = [
        line for line in source.splitlines() if line.startswith(f"sheet.dimension({handle}, ")
    ]
    assert len(dimensions) == 2
    first_parameter = dimensions[0].split('"', 2)[1]
    second_parameter = dimensions[1].split('"', 2)[1]
    assert first_parameter != second_parameter

    damaged = source.replace(second_parameter, first_parameter, 1)
    assert damaged != source
    replayed = _execute_generated_drawing(drawing.working_part, damaged)
    assert {
        issue.code
        for issue in replayed.lint()
        if issue.code.startswith("through_step_requirement")
    } == {"through_step_requirement_suppressed"}


def test_circular_step_quantisation_tolerance_is_bounded_and_rejects_real_disagreement() -> None:
    drawing = build_drawing(_circular_blind_step_part(), framed_recognition=True)
    (source,) = drawing.recognition().circular_blind_steps
    (feature,) = _features(drawing, "circular_blind_step")

    # The released record independently quantises its endpoints, radius and length to six
    # significant figures. Their derived spans differ only in the last places and must remain a
    # valid public Sheet declaration rather than becoming a framed-only consumer failure.
    assert abs(abs(source.centreline[1][2] - source.centreline[0][2]) - source.length) > 1e-6
    declared = circular_blind_step(
        axis=source.axis,
        radius=source.radius,
        length=source.length,
        centreline=source.centreline,
        section=source.section,
    )
    assert declared == feature

    run_index = "xyz".index(feature.axis)
    # These independently chosen mutations sit just beyond the sum of the published values'
    # six-significant-figure half-cells.  A fixed tolerance relative to depth/radius accepted
    # them even though no underlying values in those cells can describe the corrupted relation.
    outside_end = list(feature.centreline[1])
    outside_end[run_index] += 0.0002
    with pytest.raises(ValueError, match="matching depth"):
        replace(feature, centreline=(feature.centreline[0], tuple(outside_end)))

    outside_first = list(feature.section[0])
    changed = next(
        index for index in (0, 1) if feature.section[0][index] != feature.section[1][index]
    )
    outside_first[changed] += 0.0002
    with pytest.raises(ValueError, match="canonical quarter arc"):
        replace(feature, section=(tuple(outside_first), *feature.section[1:]))

    # Material disagreement remains rejected independently of the close boundary checks above.
    wrong_end = list(feature.centreline[1])
    wrong_end[run_index] += 0.01
    with pytest.raises(ValueError, match="matching depth"):
        replace(feature, centreline=(feature.centreline[0], tuple(wrong_end)))

    wrong_first = list(feature.section[0])
    wrong_first[changed] += 0.01
    with pytest.raises(ValueError, match="canonical quarter arc"):
        replace(feature, section=(tuple(wrong_first), *feature.section[1:]))


def test_quantisation_cells_cover_cancellation_and_reject_five_quantums() -> None:
    cancellation = circular_blind_step(
        axis="z",
        radius=1.0,
        length=1.00038,
        centreline=((0.0, 0.0, 1000.0), (0.0, 0.0, 1001.0)),
        section=((1.0, 0.0), (0.0, 0.0), (0.0, 1.0)),
    )
    assert circular_blind_step_key(cancellation)

    five_quantums = type(
        "PublishedCircularBlindStep",
        (),
        {
            "axis": "z",
            "radius": 9_000_000_000.0,
            "length": 9_000_000_000.0,
            "centreline": ((0, 0, 0), (0, 0, 9_000_050_000.0)),
            "section": ((9_000_050_000.0, 0), (0, 0), (0, 9_000_000_000.0)),
        },
    )()
    with pytest.raises(ValueError):
        circular_blind_step_key(five_quantums)
    with pytest.raises(ValueError, match="matching depth"):
        circular_blind_step(
            axis=five_quantums.axis,
            radius=five_quantums.radius,
            length=five_quantums.length,
            centreline=five_quantums.centreline,
            section=five_quantums.section,
        )


def test_radius_cells_reject_an_impossible_component_despite_orthogonal_uncertainty() -> None:
    anisotropic = type(
        "PublishedCircularBlindStep",
        (),
        {
            "axis": "z",
            "radius": 1.0,
            "length": 1.0,
            "centreline": ((0.0, 9_000_000_000.0, 0.0), (0.0, 9_000_000_000.0, 1.0)),
            "section": (
                (10_000.0, 9_000_000_000.0),
                (0.0, 9_000_000_000.0),
                (0.0, 9_000_010_000.0),
            ),
        },
    )()

    with pytest.raises(ValueError):
        circular_blind_step_key(anisotropic)
    with pytest.raises(ValueError, match="canonical quarter arc"):
        circular_blind_step(
            axis=anisotropic.axis,
            radius=anisotropic.radius,
            length=anisotropic.length,
            centreline=anisotropic.centreline,
            section=anisotropic.section,
        )


def test_span_cells_respect_decimal_decade_boundaries() -> None:
    decade_span = type(
        "PublishedCircularBlindStep",
        (),
        {
            "axis": "z",
            "radius": 1.0,
            "length": 1.008,
            "centreline": ((0.0, 0.0, 1000.0), (0.0, 0.0, 1001.0)),
            "section": ((1.0, 0.0), (0.0, 0.0), (0.0, 1.0)),
        },
    )()

    with pytest.raises(ValueError):
        circular_blind_step_key(decade_span)
    with pytest.raises(ValueError, match="matching depth"):
        circular_blind_step(
            axis=decade_span.axis,
            radius=decade_span.radius,
            length=decade_span.length,
            centreline=decade_span.centreline,
            section=decade_span.section,
        )


def test_radius_cells_respect_decimal_decade_boundaries() -> None:
    decade_radius = type(
        "PublishedCircularBlindStep",
        (),
        {
            "axis": "z",
            "radius": 10.0,
            "length": 1.0,
            "centreline": ((0.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
            "section": ((9.99998, 0.0), (0.0, 0.0), (0.0, 10.0)),
        },
    )()

    with pytest.raises(ValueError):
        circular_blind_step_key(decade_radius)
    with pytest.raises(ValueError, match="canonical quarter arc"):
        circular_blind_step(
            axis=decade_radius.axis,
            radius=decade_radius.radius,
            length=decade_radius.length,
            centreline=decade_radius.centreline,
            section=decade_radius.section,
        )


@pytest.mark.parametrize(
    ("start", "end"),
    ((0.0, 10.0), (10.0, 0.0), (0.0, -10.0), (-10.0, 0.0)),
)
def test_span_decade_cells_accept_the_adjacent_value_but_not_the_next(start, end) -> None:
    assert quantised_span_agrees(start, end, 9.99999)
    assert not quantised_span_agrees(start, end, 9.99998)


@pytest.mark.parametrize("coordinate", (10.0, -10.0))
def test_radius_decade_cells_accept_the_adjacent_value_but_not_the_next(coordinate) -> None:
    assert quantised_radius_agrees((coordinate, 0.0), (0.0, 0.0), 9.99999)
    assert not quantised_radius_agrees((coordinate, 0.0), (0.0, 0.0), 9.99998)


@pytest.mark.parametrize(
    "make_part",
    [
        _offset_small_radius_circular_blind_step_part,
        _large_scale_small_radius_circular_blind_step_part,
    ],
)
def test_quantisation_cells_follow_absolute_coordinates_for_an_offset_small_radius(
    make_part,
) -> None:
    part = make_part()
    moved_part = Pos(123, -47, 91) * Rot(17, 31, 23) * part

    raw = build_drawing(part)
    framed = build_drawing(part, framed_recognition=True)
    moved = build_drawing(moved_part, framed_recognition=True)

    assert len(raw.recognition().circular_blind_steps) == 1
    assert framed.recognition().circular_blind_steps == moved.recognition().circular_blind_steps
    assert (
        _requirements(raw, "circular_blind_step")
        == _requirements(framed, "circular_blind_step")
        == _requirements(moved, "circular_blind_step")
    )
    assert _features(framed, "circular_blind_step") == _features(moved, "circular_blind_step")
    assert raw.lint() == framed.lint() == moved.lint() == []
