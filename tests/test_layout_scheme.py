import json
from dataclasses import replace

import pytest
from build123d import Box, Cylinder

from draftwright import build_drawing
from draftwright.annotation_layout_profile import (
    AnnotationLayoutProfile,
    cap_planned_strips,
    current_layout_profile,
    use_layout_profile,
)
from draftwright.compose import StripDepths, _measure_strips, _strips_for_derived_views
from draftwright.layout_scheme import (
    AnnotationDemand,
    AnnotationScheme,
    UnplannedAnnotation,
    pack_annotation_lanes,
    pack_estimated_annotation_lanes,
    plan_annotation_scheme,
)
from draftwright.layout_selection import annotation_demand_carrier_evidence
from draftwright.model import Frame, HoleFeature, PartModel, build_part_model
from draftwright.model.ir import (
    AuthoredDimension,
    ControlFrame,
    DatumRef,
    Finish,
    Note,
    PmiFeature,
)
from draftwright.model.planner import DimensionId
from draftwright.registry import AnnotationRegistry, MeasurementCell


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
    assert scheme.to_dict()["unplanned"] == [
        {
            "identity": "dimension:raw",
            "family": "pmi",
            "feature_index": 4,
            "reason": "raw PMI has no typed corridor",
            "measurements": [],
            "source_ids": ["dimension:raw"],
            "obligation_class": "required",
        }
    ]


def test_obligation_classes_preserve_authored_and_compiled_requirements():
    scheme = plan_annotation_scheme(_model())
    assert all(item.obligation_class == "required" for item in scheme.demands)
    assert all(item.obligation_class == "required" for item in scheme.unplanned)
    assert all(row["obligation_class"] == "required" for row in scheme.to_dict()["demands"])

    automatic = plan_annotation_scheme(build_part_model(Cylinder(10, 30, rotation=(0, 90, 0))))
    assert any(item.measurements for item in automatic.demands)
    assert all(item.obligation_class == "required" for item in automatic.demands)
    assert all(item.obligation_class == "required" for item in automatic.unplanned)


def test_unknown_obligation_is_not_optional_and_source_backed_optional_is_refused():
    demand = AnnotationDemand("unattributed", "generated", "front", "above", 0, (0, 0, 0))
    assert demand.obligation_class == "unknown"
    assert AnnotationScheme((demand,), ()).to_dict()["demands"][0]["obligation_class"] == (
        "unknown"
    )
    optional = replace(demand, obligation_class="optional")
    assert optional.obligation_class == "optional"
    with pytest.raises(ValueError, match="invalid annotation obligation class"):
        replace(demand, obligation_class="invented")
    with pytest.raises(ValueError, match="cannot be optional"):
        UnplannedAnnotation(
            "source",
            "pmi",
            0,
            "no corridor",
            source_ids=("source:1",),
            obligation_class="optional",
        )
    with pytest.raises(ValueError, match="cannot be optional"):
        AnnotationDemand(
            "measure",
            "automatic_dimension",
            "front",
            "above",
            0,
            (0, 0, 0),
            measurements=(DimensionId(_model().features[0], "bore.diameter"),),
            obligation_class="optional",
        )


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


def test_build_scoped_scheme_reservation_caps_only_selected_corridors():
    model = _model()
    strips = _measure_strips(model, 0, model.bbox)
    profile = AnnotationLayoutProfile(
        corridor_scale=1,
        capped_routes=frozenset({("front", "left")}),
    )

    with use_layout_profile(profile):
        capped = cap_planned_strips(strips)
        assert current_layout_profile() is profile
    assert current_layout_profile() is None
    assert capped.left == 17.0
    assert capped.right == strips.right
    assert strips.left == 20.0


def test_completed_drawing_exposes_shadow_report_without_influencing_layout():
    model = _model()
    drawing = build_drawing(
        Box(100, 60, 20),
        model=model,
        auto_dims=False,
        annotation_layout="estimated-strips",
    )

    decision = drawing.annotation_scheme_decision
    assert decision["status"] == "shadow"
    assert decision["influenced_layout"] is False
    assert decision["scale"] == drawing.scale
    assert decision["unplanned_count"] == 1
    assert decision["corridors"]
    assert "carrier_evidence" not in decision  # manual drawing did not render automatic demand


def test_scheme_routes_approved_automatic_envelope_dimensions():
    scheme = plan_annotation_scheme(build_part_model(Box(100, 60, 20)))

    assert scheme.corridor_counts() == {
        ("plan", "below"): 1,
        ("front", "right"): 1,
        ("side", "below"): 1,
    }
    assert not scheme.unplanned
    assert {demand.identity for demand in scheme.demands} == {
        "auto:0:width.length",
        "auto:0:height.length",
        "auto:0:depth.length",
    }
    assert scheme.corridor("plan", "below")[0].model_interval == (-50.0, 50.0)
    assert scheme.to_dict()["demands"][0]["measurements"] == [
        {"feature_index": 0, "parameter": "width.length"}
    ]


def test_scheme_collapses_compound_feature_leader_to_one_natural_route():
    model = build_part_model(Cylinder(10, 30))
    scheme = plan_annotation_scheme(model)

    leaders = [demand for demand in scheme.demands if demand.family == "feature_leader"]
    assert len(leaders) == 1
    assert leaders[0].identity == "auto:0:feature_leader"
    assert (leaders[0].view, leaders[0].side) == ("plan", "right")
    assert leaders[0].model_interval is None
    assert leaders[0].dedicated_lane is False
    assert leaders[0].measurements
    assert all(identity.feature is model.features[0] for identity in leaders[0].measurements)
    lane_plan = pack_estimated_annotation_lanes(scheme, scale=1, font_size=2.5, padding=1)
    leader_corridor = lane_plan.corridor("plan", "right")
    assert leader_corridor is not None
    assert leader_corridor.lane_count == 0
    assert leader_corridor.shared_depth == pytest.approx(20.1)


def test_compound_hole_demand_tracks_every_addressable_measurement():
    hole = HoleFeature(Frame((0, 0, 0), "z"), 6, 10, False, cbore=(10, 2))
    model = PartModel(Box(20, 20, 15).bounding_box(), "z", [hole])

    scheme = plan_annotation_scheme(model)
    leader = next(demand for demand in scheme.demands if demand.family == "feature_leader")

    assert {identity.parameter for identity in leader.measurements} == {
        "bore.diameter",
        "bore.depth",
        "counterbore.diameter",
        "counterbore.depth",
    }
    assert all(identity.feature is hole for identity in leader.measurements)


def test_y_axis_hole_leader_reserves_the_front_below_band_it_uses():
    hole = HoleFeature(Frame((0, 0, 0), "y"), 4, 10, False)
    model = PartModel(Box(20, 20, 15).bounding_box(), "y", [hole])

    strips = _measure_strips(model, 0, model.bbox)
    leaders = [demand for demand in strips.scheme.demands if demand.family == "feature_leader"]

    assert [(demand.view, demand.side) for demand in leaders] == [("front", "below")]
    assert strips.front_hole_below > 0
    assert strips.fv_bottom == 0  # ordinary no-detail builds retain their old view plan
    assert _strips_for_derived_views(strips, ()) is strips
    planned = _strips_for_derived_views(strips, (("detail_a", 40.0, 30.0),))
    assert planned.fv_bottom == strips.front_hole_below


def test_demand_carrier_evidence_uses_exact_live_measurements_and_table_cells():
    model = _model()
    first = DimensionId(model.features[0], "bore.diameter")
    second = DimensionId(model.features[0], "bore.depth")
    unrelated = DimensionId(model.features[1], "bore.depth")
    demand = AnnotationDemand(
        "compound",
        "feature_leader",
        "front",
        "above",
        0,
        (10, 20, 0),
        measurements=(first, second),
    )
    scheme = AnnotationScheme((demand,), ())
    registry = AnnotationRegistry()
    registry.add(object(), "wrong_feature", "front", measurement=unrelated)
    registry.add(object(), "callout", "front", measurement=first)
    registry.add(object(), "note", "front", satisfaction=first)

    partial = annotation_demand_carrier_evidence(scheme, registry)["demands"][0]
    assert partial["status"] == "partially_represented"
    assert partial["measurements"][0]["carriers"] == [
        {"name": "callout", "kind": "measurement"},
        {"name": "note", "kind": "structured_note"},
    ]
    assert partial["measurements"][1]["carriers"] == []

    registry.add(
        object(),
        "schedule",
        None,
        measurement=second,
        cells=(MeasurementCell("holes", 1, 2, second),),
    )
    represented = annotation_demand_carrier_evidence(scheme, registry)["demands"][0]
    json.dumps(represented)  # build decisions/report consumers require JSON-safe evidence
    assert represented["status"] == "represented"
    assert represented["measurements"][1]["carriers"] == [
        {"name": "schedule", "kind": "measurement"},
        {"name": "schedule", "kind": "table_cell", "schedule": "holes", "row": 1, "column": 2},
    ]
    registry.remove("callout")
    registry.remove("note")
    registry.remove("schedule")
    assert annotation_demand_carrier_evidence(scheme, registry)["demands"][0]["status"] == (
        "unrepresented"
    )


def test_source_only_demand_is_unattributed_not_falsely_satisfied():
    scheme = plan_annotation_scheme(_model())
    evidence = annotation_demand_carrier_evidence(scheme, AnnotationRegistry())

    assert all(row["status"] == "unattributed" for row in evidence["demands"])
    assert evidence["unplanned"] == [
        {
            "identity": "dimension:raw",
            "family": "pmi",
            "feature_index": 4,
            "obligation_class": "required",
            "status": "unattributed",
            "measurements": [],
            "sources": [{"source_id": "dimension:raw", "carriers": []}],
            "unplanned_reason": "raw PMI has no typed corridor",
        }
    ]


def test_source_only_pmi_demand_traces_live_owner_without_claiming_coverage():
    model = _model()
    scheme = plan_annotation_scheme(model)
    registry = AnnotationRegistry()
    registry.add(object(), "wrong_datum", "front", feature=model.features[1])
    # The real GD&T renderer owns the controlled feature and separately records
    # the declared PMI item; its source identity lives on that declaration.
    registry.add(object(), "gdt_frame", "front", declaration=model.features[0])

    evidence = annotation_demand_carrier_evidence(scheme, registry)
    (gdt,) = [row for row in evidence["demands"] if row["identity"] == "gdt:1"]
    assert evidence["coverage_authority"] is False
    assert gdt["status"] == "source_carried_unverified"
    assert gdt["measurements"] == []
    assert gdt["sources"] == [
        {
            "source_id": "gdt:1",
            "carriers": [{"name": "gdt_frame", "kind": "source_owner", "view": "front"}],
        }
    ]
    (datum,) = [row for row in evidence["demands"] if row["identity"] == "datum:1"]
    assert datum["sources"][0]["carriers"] == [
        {"name": "wrong_datum", "kind": "source_owner", "view": "front"}
    ]
    json.dumps(evidence)

    registry.remove("gdt_frame")
    (missing,) = [
        row
        for row in annotation_demand_carrier_evidence(scheme, registry)["demands"]
        if row["identity"] == "gdt:1"
    ]
    assert missing["status"] == "unattributed"
    assert missing["sources"] == [{"source_id": "gdt:1", "carriers": []}]


def test_multi_occurrence_datum_trace_keeps_every_source_identity():
    model = _model()
    datum = replace(model.features[1], source_id="", source_ids=("datum:2", "datum:1"))
    model.features[1] = datum
    scheme = plan_annotation_scheme(model)
    (demand,) = [item for item in scheme.demands if item.family == "datum_ref"]
    assert demand.source_ids == ("datum:1", "datum:2")

    registry = AnnotationRegistry()
    registry.add(object(), "datum", "front", declaration=datum)
    (row,) = [
        item
        for item in annotation_demand_carrier_evidence(scheme, registry)["demands"]
        if item["family"] == "datum_ref"
    ]
    assert row["status"] == "source_carried_unverified"
    assert [source["source_id"] for source in row["sources"]] == ["datum:1", "datum:2"]
    assert all(source["carriers"][0]["name"] == "datum" for source in row["sources"])

    registry.remove("datum")
    registry.add(
        object(),
        "partial_datum",
        "front",
        declaration=replace(datum, source_id="datum:1", source_ids=()),
    )
    (partial,) = [
        item
        for item in annotation_demand_carrier_evidence(scheme, registry)["demands"]
        if item["family"] == "datum_ref"
    ]
    assert partial["status"] == "source_partially_carried_unverified"
    assert [bool(source["carriers"]) for source in partial["sources"]] == [True, False]


def test_scheme_does_not_route_compound_leader_to_missing_side_left_strip():
    scheme = plan_annotation_scheme(build_part_model(Cylinder(10, 30, rotation=(0, 90, 0))))

    leaders = [demand for demand in scheme.demands if demand.family == "feature_leader"]
    assert [(leader.view, leader.side) for leader in leaders] == [("side", "right")]
    assert leaders[0].dedicated_lane is False


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


@pytest.mark.parametrize("method", ["estimated_paper_span", "estimated_paper_depth"])
@pytest.mark.parametrize(
    ("font_size", "padding", "message"),
    [
        (0, 0, "font size"),
        (float("inf"), 0, "font size"),
        (2.5, -1, "padding"),
        (2.5, float("nan"), "padding"),
    ],
)
def test_demand_estimates_reject_invalid_typography(method, font_size, padding, message):
    with pytest.raises(ValueError, match=message):
        getattr(_demand("invalid", (0, 0, 0)), method)(font_size, padding)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"tier": 0}, "tier"),
        ({"tier": 1, "gap": -1}, "gap"),
        ({"tier": 1, "spacing": float("nan")}, "spacing"),
    ],
)
def test_corridor_depths_reject_invalid_spacing_inputs(kwargs, message):
    plan = pack_annotation_lanes(
        AnnotationScheme((_demand("one", (0, 0, 0)),), ()),
        scale=1,
        span_for=lambda demand: 1,
    )

    with pytest.raises(ValueError, match=message):
        plan.corridor_depths(**kwargs)


@pytest.mark.parametrize(
    ("demand", "message"),
    [
        (_demand("short-site", (0, 0), side="right"), "three coordinates"),
        (_demand("text-site", ("bad", 0, 0)), "three coordinates"),
        (
            replace(_demand("text-support", (0, 0, 0)), model_interval=("bad", 1)),
            "two coordinates",
        ),
        (
            replace(_demand("infinite-support", (0, 0, 0)), model_interval=(0, float("inf"))),
            "support.*finite",
        ),
    ],
)
def test_lane_packing_rejects_malformed_sites_and_supports(demand, message):
    with pytest.raises(ValueError, match=message):
        pack_annotation_lanes(
            AnnotationScheme((demand,), ()),
            scale=1,
            span_for=lambda item: 1,
        )


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
