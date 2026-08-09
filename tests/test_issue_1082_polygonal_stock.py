"""#1082: whole-part polygonal stock has its own evidence and definition."""

from __future__ import annotations

import math
from dataclasses import replace

import pytest
from build123d import Box, Compound, Cylinder, Polygon, Pos, RegularPolygon, Rot, extrude

from draftwright import build_drawing
from draftwright.compose import _est_right_strip_depth, _n_right_strip_boss_heights
from draftwright.linting import LintIssue
from draftwright.linting.polygonal_stock_coverage import polygonal_stock_outcomes
from draftwright.model import build_part_model
from draftwright.model.ir import RequestedDimension
from draftwright.recognition.polygonal_bosses import (
    PolygonalStock,
    recognise_polygonal_stock,
)
from draftwright.recognition.result import build_recognition_result


def _hex_stock(*, angle: float = 0):
    return Rot(0, 0, angle) * extrude(RegularPolygon(20, 6), 30)


def test_whole_hex_stock_is_not_claimed_as_an_attached_boss():
    (stock,) = recognise_polygonal_stock(_hex_stock())

    assert isinstance(stock, PolygonalStock)
    assert stock.axis == "z"
    assert stock.side_count == 6
    assert stock.center == pytest.approx((0, 0, 15))
    assert stock.across_flats == pytest.approx(40 * math.cos(math.pi / 6))
    assert (stock.base, stock.top, stock.length) == pytest.approx((0, 30, 30))
    assert len(stock.flat_directions) == len(stock.flat_centres) == 6


def test_rotation_preserves_stock_across_flats_instead_of_using_bbox_width():
    plain = recognise_polygonal_stock(_hex_stock())[0]
    rotated = recognise_polygonal_stock(_hex_stock(angle=17))[0]

    assert rotated.across_flats == pytest.approx(plain.across_flats)
    assert rotated.flat_centres != plain.flat_centres
    drawings = [build_drawing(_hex_stock(angle=angle), repair=False) for angle in (0, 17)]
    labels = [
        annotation.label
        for drawing in drawings
        for name, annotation in drawing.iter_annotations()
        if name.startswith("m_polygonal_stock_")
    ]
    assert labels == ["HEX 34.6 A/F", "HEX 34.6 A/F"]


@pytest.mark.parametrize(
    "part",
    [
        Cylinder(20, 30),
        Box(40, 30, 20),
        extrude(Polygon((20, 0), (10, 17), (-10, 17), (-20, 0), (-10, -17), (14, -14)), 30),
        Box(100, 80, 10) + Pos(0, 0, 5) * _hex_stock(),
        Compound(children=[_hex_stock(), Pos(60, 0, 0) * _hex_stock()]),
    ],
    ids=["circular", "rectangular", "irregular", "attached-boss", "assembly"],
)
def test_other_geometry_retains_its_existing_owner(part):
    assert recognise_polygonal_stock(part) == []


@pytest.mark.parametrize(
    ("part", "owner"),
    [
        (Cylinder(20, 30), "boss"),
        (Box(40, 30, 20), "envelope"),
        (
            extrude(
                Polygon((20, 0), (10, 17), (-10, 17), (-20, 0), (-10, -17), (14, -14)),
                30,
            ),
            "envelope",
        ),
        (Box(100, 80, 10) + Pos(0, 0, 5) * _hex_stock(), "polygonal_boss"),
    ],
    ids=["circular", "rectangular", "irregular", "attached-boss"],
)
def test_rejected_stock_classes_keep_their_established_model_owner(part, owner):
    kinds = {feature.kind for feature in build_part_model(part).features}

    assert "polygonal_stock" not in kinds
    assert owner in kinds


def test_extra_topology_fails_closed_instead_of_being_called_plain_stock():
    assert recognise_polygonal_stock(_hex_stock() - Cylinder(3, 30)) == []


def test_stock_ir_validation_does_not_claim_attached_boss_semantics():
    feature = next(
        item for item in build_part_model(_hex_stock()).features if item.kind == "polygonal_stock"
    )

    with pytest.raises(ValueError, match="polygonal stock") as failure:
        replace(feature, side_count=5)

    assert "boss" not in str(failure.value)


def test_whole_stock_compiles_its_form_and_axial_length_through_shared_placement():
    drawing = build_drawing(_hex_stock(), repair=False)
    (feature,) = [item for item in drawing.model().features if item.kind == "polygonal_stock"]

    assert [parameter.parameter_id for parameter in feature.parameters()] == [
        "polygon_across_flats.length",
        "stock_length.length",
    ]
    assert not [item for item in drawing.model().features if item.kind == "envelope"]
    assert "dim_height" not in drawing.annotations()
    assert _n_right_strip_boss_heights(drawing.model()) == 0
    assert _est_right_strip_depth(
        0, _n_right_strip_boss_heights(drawing.model())
    ) == pytest.approx(_est_right_strip_depth(0))
    assert [
        (drawing.view_of(name), annotation.label)
        for name, annotation in drawing.iter_annotations()
        if name.startswith("m_polygonal_stock_")
    ] == [("plan", "HEX 34.6 A/F")]
    assert [
        (drawing.view_of(name), annotation.label)
        for name, annotation in drawing.iter_annotations()
        if name.startswith("m_stocklength_")
    ] == [("front", "30")]
    assert not [issue for issue in drawing.lint() if issue.code.startswith("polygonal_stock_")]


def test_missing_stock_definition_is_not_mistaken_for_a_clean_sparse_drawing():
    drawing = build_drawing(_hex_stock(), repair=False)
    callout = next(name for name in drawing.annotations() if name.startswith("m_polygonal_stock_"))
    drawing.remove(callout)

    assert [issue.code for issue in drawing.lint()].count(
        "polygonal_stock_requirement_missing"
    ) == 1


def test_stock_geometry_without_one_ir_owner_fails_closed_as_unverifiable():
    part = _hex_stock()
    sparse = replace(build_part_model(part), features=())

    drawing = build_drawing(part, model=sparse, repair=False)

    assert [issue.code for issue in drawing.lint()].count(
        "polygonal_stock_requirement_unverifiable"
    ) == 2


def test_duplicate_stock_ir_correspondence_is_ambiguous_not_first_match_wins():
    part = _hex_stock()
    drawing = build_drawing(part, repair=False)
    stock = next(
        feature for feature in drawing.model().features if feature.kind == "polygonal_stock"
    )

    outcomes = polygonal_stock_outcomes(
        build_recognition_result(part),
        (stock, stock),
        drawing.registry,
    )

    assert [outcome.state for outcome in outcomes] == ["unverifiable", "unverifiable"]


def test_authored_suppression_is_distinct_from_missing_stock_placement():
    part = _hex_stock()
    model = build_part_model(part)
    stock = next(feature for feature in model.features if feature.kind == "polygonal_stock")
    authored = replace(
        model,
        authored_dimensions=(
            RequestedDimension(feature=stock, role="polygon_across_flats.length"),
        ),
    )

    drawing = build_drawing(part, model=authored, repair=False)

    assert any(name.startswith("m_polygonal_stock_") for name in drawing.annotations())
    assert not any(name.startswith("m_stocklength_") for name in drawing.annotations())
    assert "dim_height" not in drawing.annotations()
    assert [issue.code for issue in drawing.lint()].count(
        "polygonal_stock_requirement_suppressed"
    ) == 1
    assert "polygonal_stock_requirement_missing" not in [issue.code for issue in drawing.lint()]


def test_explicit_stock_drop_is_not_reported_again_as_missing():
    drawing = build_drawing(_hex_stock(), repair=False)
    name = next(name for name in drawing.annotations() if name.startswith("m_stocklength_"))
    measurement = next(iter(drawing.registry.measurement_of(name)))
    drawing.remove(name)
    drawing.registry.record_issue(
        LintIssue(
            severity="warning",
            code="polygonal_stock_length_dropped",
            message="forced rejection",
            measurement_ids=(measurement,),
        )
    )

    codes = [issue.code for issue in drawing.lint()]
    assert codes.count("polygonal_stock_length_dropped") == 1
    assert "polygonal_stock_requirement_missing" not in codes


def test_rejected_stock_length_candidate_records_the_drop_with_measurement_identity(monkeypatch):
    import draftwright.annotations.from_model as from_model

    register = from_model.register_corridor

    def reject_stock_length(ctx, key, strip, view, axis, tier, candidate):
        if candidate.name.startswith("m_stocklength_"):
            candidate.on_drop(candidate.name)
            return
        register(ctx, key, strip, view, axis, tier, candidate)

    monkeypatch.setattr(from_model, "register_corridor", reject_stock_length)

    drawing = build_drawing(_hex_stock(), repair=False)
    codes = [issue.code for issue in drawing.lint()]

    assert not any(name.startswith("m_stocklength_") for name in drawing.annotations())
    assert codes.count("polygonal_stock_length_dropped") == 1
    assert "polygonal_stock_requirement_missing" not in codes
