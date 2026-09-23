from dataclasses import replace

import pytest
from build123d import Box

from draftwright import build_drawing
from draftwright.compose import StripDepths, _measure_strips
from draftwright.layout_scheme import (
    AnnotationDemand,
    AnnotationScheme,
    pack_annotation_lanes,
    pack_estimated_annotation_lanes,
    plan_annotation_scheme,
)
from draftwright.model import Frame, PartModel
from draftwright.model.ir import (
    AuthoredDimension,
    ControlFrame,
    DatumRef,
    Finish,
    Note,
    PmiFeature,
)


def _model():
    solid = Box(100, 60, 20)
    frame = Frame((10, 20, 0), "z")
    features = [
        ControlFrame(frame, "position", "0.1", "front", "above", source_id="gdt:1"),
        DatumRef(frame, "A", "front", "above", source_id="datum:1"),
        Note(frame, "Ra 3.2", "side", "below", source_id="finish:1"),
        Finish(frame, "1.6", "front", "left"),
        PmiFeature(frame, "location", 10.0, "?", "X", source_id="dimension:raw"),
    ]
    return PartModel(solid.bounding_box(), "z", features)


def test_scheme_groups_explicit_semantics_by_view_corridor_without_coordinates():
    scheme = plan_annotation_scheme(_model())

    assert scheme.corridor_counts() == {
        ("front", "above"): 2,
        ("side", "below"): 1,
        ("front", "left"): 1,
    }
    assert [demand.identity for demand in scheme.corridor("front", "above")] == [
        "gdt:1",
        "datum:1",
    ]
    assert scheme.demands[0].model_site == (10.0, 20.0, 0.0)
    assert [(item.identity, item.reason) for item in scheme.unplanned] == [
        ("dimension:raw", "raw PMI has no typed corridor")
    ]
    finish = scheme.corridor("front", "left")[0]
    assert finish.family == "finish"
    assert finish.estimated_paper_span(font_size=2.5, padding=1) == 8.25


def test_strip_measurement_carries_the_scheme_without_changing_depths():
    model = _model()
    strips = _measure_strips(model, 0, model.bbox)

    assert strips.scheme == plan_annotation_scheme(model)
    assert strips.right == 20.0
    assert strips.left == 20.0
    assert strips.planned_corridor_depths(1) == {
        ("front", "above"): 26.5,
        ("front", "left"): 17.0,
        ("side", "below"): 17.0,
    }
    comparisons = {
        (item.view, item.side): (item.planned, item.reserved, item.delta)
        for item in strips.compare_planned_corridors(1)
    }
    assert comparisons == {
        ("front", "above"): (26.5, 2.0, 24.5),
        ("front", "left"): (17.0, 20.0, -3.0),
        ("side", "below"): (17.0, 0.0, 17.0),
    }
    report = strips.annotation_scheme_shadow_report(1)
    assert report.unplanned_count == 1
    assert [(item.view, item.side) for item in report.under_reserved] == [
        ("front", "above"),
        ("side", "below"),
    ]
    assert [(item.view, item.side) for item in report.over_reserved] == [("front", "left")]
    assert report.to_dict()["under_reserved"] == 2


def test_completed_drawing_exposes_shadow_report_without_influencing_layout():
    model = _model()
    drawing = build_drawing(Box(100, 60, 20), model=model, auto_dims=False)

    decision = drawing.annotation_scheme_decision
    assert decision["status"] == "shadow"
    assert decision["influenced_layout"] is False
    assert decision["scale"] == drawing.scale
    assert decision["unplanned_count"] == 1
    assert decision["corridors"]


def _demand(identity, site, *, view="front", side="above", index=0):
    return AnnotationDemand(identity, "authored_dimension", view, side, index, site)


def test_lane_packing_separates_overlaps_and_reuses_the_first_available_lane():
    scheme = AnnotationScheme(
        (
            _demand("middle", (2, 0, 0), index=1),
            _demand("right", (10, 0, 0), index=2),
            _demand("left", (0, 0, 0), index=0),
        ),
        (),
    )

    plan = pack_annotation_lanes(scheme, scale=1, span_for=lambda demand: 6)
    corridor = plan.corridor("front", "above")

    assert corridor is not None
    assert corridor.lane_count == 2
    assert [(item.demand.identity, item.lane) for item in corridor.reservations] == [
        ("left", 0),
        ("middle", 1),
        ("right", 0),
    ]
    assert plan.corridor_depths(tier=6, gap=10, spacing=2.5) == {("front", "above"): 24.5}


def test_lane_packing_is_input_order_independent_and_uses_corridor_axis():
    demands = (
        _demand("front", (9, 0, 1), view="front", side="right", index=0),
        _demand("side", (0, 3, 10), view="side", side="below", index=1),
    )

    forward = pack_annotation_lanes(
        AnnotationScheme(demands, ()), scale=2, span_for=lambda demand: 4
    )
    reverse = pack_annotation_lanes(
        AnnotationScheme(tuple(reversed(demands)), ()), scale=2, span_for=lambda demand: 4
    )

    def intervals(plan):
        return {
            item.demand.identity: (item.start, item.end, item.lane)
            for corridor in plan.corridors
            for item in corridor.reservations
        }

    # A vertical front corridor follows model z; a horizontal side corridor follows model y.
    assert intervals(forward) == {"front": (0, 4, 0), "side": (4, 8, 0)}
    assert intervals(reverse) == intervals(forward)


def test_corridor_comparison_uses_effective_composed_reservations():
    scheme = AnnotationScheme((_demand("below", (0, 0, 0), side="below"),), ())
    comparison = StripDepths(right=0, left=0, scheme=scheme).compare_planned_corridors(1)[0]

    assert comparison.planned == 17.0
    assert comparison.reserved == 20.0
    assert comparison.delta == -3.0


def test_lane_packing_rejects_invalid_scale_clearance_and_spans():
    scheme = AnnotationScheme((_demand("one", (0, 0, 0)),), ())

    for kwargs in ({"scale": 0}, {"scale": 1, "clearance": -1}):
        with pytest.raises(ValueError):
            pack_annotation_lanes(scheme, span_for=lambda demand: 1, **kwargs)

    with pytest.raises(ValueError):
        pack_annotation_lanes(scheme, scale=1, span_for=lambda demand: float("nan"))

    bad_route = AnnotationScheme((_demand("route", (0, 0, 0), view="iso"),), ())
    with pytest.raises(ValueError, match="principal view and side"):
        pack_annotation_lanes(bad_route, scale=1, span_for=lambda demand: 1)

    bad_site = AnnotationScheme((_demand("site", (float("inf"), 0, 0)),), ())
    with pytest.raises(ValueError, match="must be finite"):
        pack_annotation_lanes(bad_site, scale=1, span_for=lambda demand: 1)


def test_authored_dimension_support_widens_its_lane_reservation():
    dimension = AuthoredDimension(
        Frame((50, 0, 0), "z"),
        "linear",
        40,
        "40",
        "X",
        ref_pts=((10, 0, 0), (50, 0, 0)),
        source_id="dimension:wide",
        view="front",
        side="above",
    )
    model = PartModel(Box(100, 60, 20).bounding_box(), "z", [dimension])

    scheme = plan_annotation_scheme(model)
    plan = pack_annotation_lanes(scheme, scale=0.5, span_for=lambda demand: 8)
    reservation = plan.corridor("front", "above").reservations[0]

    assert scheme.demands[0].model_interval == (10.0, 50.0)
    assert (reservation.start, reservation.end) == (5.0, 25.0)


def test_estimated_ink_span_tracks_corridor_orientation_without_render_geometry():
    horizontal = _demand("horizontal", (0, 0, 0), side="above")
    vertical = _demand("vertical", (0, 0, 0), side="right")
    horizontal = replace(horizontal, estimated_ink_em=(6.0, 2.0))
    vertical = replace(vertical, estimated_ink_em=(6.0, 2.0))

    assert horizontal.estimated_paper_span(2.5, padding=1) == 17.0
    assert vertical.estimated_paper_span(2.5, padding=1) == 7.0

    plan = pack_estimated_annotation_lanes(
        AnnotationScheme((horizontal, vertical), ()), scale=1, font_size=2.5, padding=1
    )
    assert plan.corridor("front", "above").reservations[0].end == 8.5
    assert plan.corridor("front", "right").reservations[0].end == 3.5
