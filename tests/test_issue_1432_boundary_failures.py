"""Malformed slot witnesses must neither enter the IR nor certify drawn measurements."""

from copy import copy, deepcopy
from dataclasses import replace

import pytest
from build123d import Box, Rot

from draftwright import build_drawing
from draftwright.linting.oriented_slot_coverage import oriented_slot_requirement_outcomes
from draftwright.oriented_slot_contract import (
    oriented_slot_provider_key,
    standalone_oriented_slots,
)


@pytest.fixture(scope="module")
def drawing():
    result = build_drawing(Box(120, 90, 10) - Rot(0, 0, 30) * Box(24, 6, 20))
    assert len(result.recognition().oriented_slots) == 1
    feature = next(f for f in result.model().features if f.kind == "oriented_slot")
    outcomes = oriented_slot_requirement_outcomes(
        result.recognition(), (feature,), result.registry
    )
    assert [o.state for o in outcomes] == ["placed", "placed"]
    return result


def _assert_unverifiable(drawing, *, source=None, feature=None):
    recognition = drawing.recognition()
    if source is not None:
        recognition = replace(recognition, oriented_slots=(source,))
    features = drawing.model().features if feature is None else (feature,)
    outcomes = oriented_slot_requirement_outcomes(recognition, features, drawing.registry)
    assert [o.parameter_id for o in outcomes] == [
        "oriented_slot_width.length",
        "oriented_slot_length.length",
    ]
    assert [o.state for o in outcomes] == ["unverifiable", "unverifiable"]
    assert all(o.source_records[0] is recognition.oriented_slots[0] for o in outcomes)


@pytest.mark.parametrize(
    ("points", "bulge", "reason"),
    [
        (((-3, -12), (3, -12), (3, 12), (-3, 12)), 0.1, "straight"),
        (((-2, -12), (4, -12), (4, 12), (-2, 12)), 0.0, "origin-centred"),
        (((-0.001, -12), (0.001, -12), (0.001, 12), (-0.001, 12)), 0.0, "too short"),
        (((-4, -12), (4, -12), (3, 12), (-3, 12)), 0.0, "opposite.*length"),
        (((-4, -12), (2, -12), (4, 12), (-2, 12)), 0.0, "orthogonal"),
        (((-3, -3), (3, -3), (3, 3), (-3, 3)), 0.0, "distinct"),
    ],
    ids=["curved", "offset", "degenerate", "trapezoid", "skew", "square"],
)
def test_non_slot_sections_are_refused_by_both_front_doors(drawing, points, bulge, reason):
    source = deepcopy(drawing.recognition().oriented_slots[0])
    vertices = tuple(copy(v) for v in source.source.section.boundary)
    for vertex, point in zip(vertices, points, strict=True):
        object.__setattr__(vertex, "point", point)
        object.__setattr__(vertex, "bulge", bulge)
    object.__setattr__(source.source.section, "boundary", vertices)
    # The public Section value also checks centering before our rectangle
    # check; offset sections and this trapezoid fail at that earlier boundary.
    provider_reason = (
        "canonical public ordering" if reason in ("origin-centred", "opposite.*length") else reason
    )
    with pytest.raises(ValueError, match=provider_reason):
        oriented_slot_provider_key(source)
    _assert_unverifiable(drawing, source=source)

    feature = next(f for f in drawing.model().features if f.kind == "oriented_slot")
    with pytest.raises(ValueError, match=reason):
        replace(feature.passage, boundary=tuple((p, bulge) for p in points))


@pytest.mark.parametrize(
    ("path", "value", "reason"),
    [
        (("source", "run_interval"), (-5,), "two values"),
        (("source", "run_interval"), (5, -5), "increase"),
        (("source", "section", "boundary"), (), "four vertices"),
        (("source", "section", "boundary"), (object(),) * 4, "vertex records"),
        (("source", "ends", "low_capped"), 0, "booleans"),
        (("source", "ends", "low_capped"), True, "open through"),
        (("source", "ends", "high_capped"), True, "open through"),
        (("width_direction",), (0.866025, 0.5, 0), "orthogonal"),
        (("width_direction",), (0, 0, 1), "perpendicular"),
    ],
    ids=[
        "interval-size",
        "interval-order",
        "empty-boundary",
        "vertex-type",
        "end-type",
        "low-cap",
        "high-cap",
        "parallel-dimensions",
        "axial-dimension",
    ],
)
def test_inconsistent_provider_witness_never_certifies_existing_ink(drawing, path, value, reason):
    source = deepcopy(drawing.recognition().oriented_slots[0])
    target = source
    for name in path[:-1]:
        target = getattr(target, name)
    object.__setattr__(target, path[-1], value)
    with pytest.raises((TypeError, ValueError), match=reason):
        oriented_slot_provider_key(source)
    _assert_unverifiable(drawing, source=source)


@pytest.mark.parametrize(
    ("updates", "reason"),
    [
        ({"low_capped": True}, "open through"),
        ({"high_capped": True}, "open through"),
        ({"u": (0, 0, 1)}, "orthogonal"),
        ({"v": (0, -1, 0)}, "right handed"),
        ({"origin": (0, 0, 1)}, "run-axis foot"),
    ],
)
def test_declared_passage_refuses_incompatible_geometry(drawing, updates, reason):
    feature = next(f for f in drawing.model().features if f.kind == "oriented_slot")
    with pytest.raises(ValueError, match=reason):
        replace(feature.passage, **updates)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 10**400])
def test_nonfinite_ir_cannot_reuse_previously_placed_measurements(drawing, value):
    feature = copy(next(f for f in drawing.model().features if f.kind == "oriented_slot"))
    object.__setattr__(feature, "width", value)
    _assert_unverifiable(drawing, feature=feature)


def test_broken_parameter_inventory_keeps_both_source_requirements(drawing):
    feature = copy(next(f for f in drawing.model().features if f.kind == "oriented_slot"))

    def unavailable():
        raise ValueError("parameter inventory unavailable")

    object.__setattr__(feature, "parameters", unavailable)
    _assert_unverifiable(drawing, feature=feature)


@pytest.mark.parametrize("mutable", ["slots", "patterns"])
def test_standalone_selection_refuses_mutable_run_inventories(drawing, mutable):
    slots = drawing.recognition().oriented_slots
    with pytest.raises(TypeError, match="exact immutable tuples"):
        standalone_oriented_slots(
            list(slots) if mutable == "slots" else slots, [] if mutable == "patterns" else ()
        )


def test_principal_rectangle_cannot_claim_the_oriented_slot_family(drawing):
    points = ((-12, -3), (12, -3), (12, 3), (-12, 3))
    source = deepcopy(drawing.recognition().oriented_slots[0])
    object.__setattr__(source, "width_direction", (0, 1, 0))
    object.__setattr__(source, "long_direction", (1, 0, 0))
    for vertex, point in zip(source.source.section.boundary, points, strict=True):
        object.__setattr__(vertex, "point", point)
    with pytest.raises(ValueError, match="legacy slot family"):
        oriented_slot_provider_key(source)
    _assert_unverifiable(drawing, source=source)

    feature = next(f for f in drawing.model().features if f.kind == "oriented_slot")
    passage = replace(feature.passage, boundary=tuple((p, 0) for p in points))
    with pytest.raises(ValueError, match="legacy slot family"):
        replace(feature, passage=passage, width_direction=(0, 1, 0), long_direction=(1, 0, 0))
