import pytest
from b123d_recognisers import (
    EdgeOpenCircularPocket,
    OpenCircularSection,
    OpenCircularSectionSegment,
)
from build123d import Box, BuildPart, BuildSketch, Plane, Polygon, Pos, SlotOverall, extrude

from draftwright import build_drawing
from draftwright.model.compiled import compile_dimensions
from draftwright.model.detect import ConvContext, convert
from draftwright.model.ir import EdgeOpenCircularPocketFeature, PartModel
from draftwright.model.planner import plan_dimensions


def _record(axis: str = "z") -> EdgeOpenCircularPocket:
    section = OpenCircularSection(
        (
            OpenCircularSectionSegment(
                "arc", (3.0, 0.0), (4.0, 1.0), (3.0, 1.0), 1.0, 1.5707963
            ),
            OpenCircularSectionSegment("line", (4.0, 1.0), (4.0, 5.0)),
            OpenCircularSectionSegment(
                "arc", (4.0, 5.0), (2.0, 5.0), (3.0, 5.0), 1.0, 3.1415927
            ),
            OpenCircularSectionSegment("line", (2.0, 5.0), (2.0, 2.0)),
        ),
        ((2.0, 2.0), (3.0, 0.0)),
    )
    return EdgeOpenCircularPocket(axis, (4.0, 12.0), 1, section)


def _part():
    with BuildPart() as stock_builder:
        with BuildSketch(Plane.XY):
            Polygon((-30, -20), (30, -20), (30, 10), (20, 20), (-30, 20))
        extrude(amount=12)
    with BuildPart() as cutter_builder:
        with BuildSketch(Plane.XY.offset(4)):
            SlotOverall(30, 10)
        extrude(amount=10)
    return stock_builder.part - Pos(16, 10, 0) * cutter_builder.part


def test_provider_record_lowers_without_inventing_a_closed_profile_or_length() -> None:
    feature = convert(_record(), ConvContext(Box(20, 20, 20).bounding_box(), None))

    assert isinstance(feature, EdgeOpenCircularPocketFeature)
    assert feature.segments == tuple(
        # The consumer owns its IR values; no provider record crosses the boundary.
        type(feature.segments[0])(
            segment.kind,
            segment.start,
            segment.end,
            segment.center,
            segment.radius,
            segment.sweep,
        )
        for segment in _record().section.segments
    )
    assert feature.opening == _record().section.opening
    assert feature.frame.origin == pytest.approx((3.0, 6.0, 8.0))
    assert {(p.kind, p.role, p.value) for p in feature.parameters()} == {
        ("radius", "edge_open_circular_pocket_radius", 1.0),
        ("length", "edge_open_circular_pocket_depth", 8.0),
    }


def test_planner_keeps_radius_and_depth_together_in_the_axis_end_view() -> None:
    bbox = Box(20, 20, 20).bounding_box()
    feature = convert(_record("x"), ConvContext(bbox, None))

    (group,) = plan_dimensions(PartModel(bbox, None, [feature]))

    assert group.feature is feature
    assert group.view == "side"
    assert {(d.param.kind, d.param.role) for d in group.dims} == {
        ("radius", "edge_open_circular_pocket_radius"),
        ("length", "edge_open_circular_pocket_depth"),
    }


def test_compiler_exposes_no_profile_measurements_as_renderer_facts() -> None:
    bbox = Box(20, 20, 20).bounding_box()
    feature = convert(_record(), ConvContext(bbox, None))

    (group,) = compile_dimensions(PartModel(bbox, None, [feature])).groups

    assert group.facts.frame is feature.frame
    assert group.facts.axis == "z"
    for forbidden in ("segments", "opening", "radius", "run_interval"):
        with pytest.raises(AttributeError):
            getattr(group.facts, forbidden)


def test_consumer_ir_refuses_a_profile_whose_gap_is_not_its_loose_endpoints() -> None:
    feature = convert(_record(), ConvContext(Box(20, 20, 20).bounding_box(), None))

    with pytest.raises(ValueError, match="opening must join the loose endpoints"):
        EdgeOpenCircularPocketFeature(
            feature.frame,
            feature.axis,
            feature.open_sign,
            feature.run_interval,
            feature.segments,
            ((0.0, 0.0), (1.0, 1.0)),
        )


def test_solver_places_one_open_radius_and_depth_callout() -> None:
    part = _part()
    feature = convert(_record(), ConvContext(part.bounding_box(), None))

    drawing = build_drawing(part, model=PartModel(part.bounding_box(), None, [feature]))

    assert [
        name for name in drawing.annotations() if name.startswith("m_edge_open_circular_pocket")
    ] == ["m_edge_open_circular_pocket_z0"]
    assert not [
        issue for issue in drawing.lint() if issue.code == "edge_open_circular_pocket_dropped"
    ]


def test_authored_omission_withholds_the_entire_callout() -> None:
    part = _part()
    feature = convert(_record(), ConvContext(part.bounding_box(), None))
    model = PartModel(
        part.bounding_box(),
        None,
        [feature],
        authored_dimensions=(),
    )

    drawing = build_drawing(part, model=model)

    assert not [
        name for name in drawing.annotations() if name.startswith("m_edge_open_circular_pocket")
    ]
