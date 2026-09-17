"""Regression coverage for truthful AP242 linear-reference witnesses (#1209)."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from draftwright import extract_pmi_report
from draftwright.annotations.from_model import _pmi_witness_from_bbox
from draftwright.pmi import _dimension_geometry_blockers, _linear_reference_stations

CTC04 = Path(__file__).parent / "fixtures" / "nist_ctc_04_asme1_ap242.stp"
CTC03 = Path(__file__).parent / "fixtures" / "nist_ctc_03_asme1_ap242.stp"


@pytest.mark.parametrize(
    "value, stations",
    [
        (3.2, ((-3.2, 0.0, 0.0), (0.0, 0.0, 0.0))),
        (0.5, ((0.0, 0.0, 0.0), (0.5, 0.0, 0.0))),
        (2.0, ((0.5, 0.0, 0.0), (2.5, 0.0, 0.0))),
        (3.0, ((2.5, 0.0, 0.0), (5.5, 0.0, 0.0))),
        (20.0, ((5.5, 0.0, 0.0), (25.5, 0.0, 0.0))),
    ],
)
def test_grm03_coaxial_end_faces_establish_their_x_station_span(value, stations):
    points, axis, blockers = _linear_reference_stations(stations, value)

    assert points == stations
    assert axis == "X"
    assert blockers == ()
    assert abs(points[1][0] - points[0][0]) == pytest.approx(value)


def test_an_oblique_or_one_sided_relationship_fails_closed_without_nominal_guessing():
    points, axis, blockers = _linear_reference_stations(
        ((0.0, 149.98174079291, -70.32125981157614), (0.0, 141.5491012236, -52.1456568216)),
        20.0,
    )
    assert len(points) == 2
    assert axis == "?"
    assert blockers and "not principal-axis aligned" in blockers[0]

    points, axis, blockers = _linear_reference_stations(
        ((0.0, 149.98174079291, -70.32125981157614), None), 25.0
    )
    assert len(points) == 1
    assert axis == "?"
    assert blockers == ("linear dimension needs two measurable authored reference groups",)

    points, axis, blockers = _linear_reference_stations(((0.0, 0.0, 0.0), (10.0, 0.0, 0.0)), 20.0)
    assert len(points) == 2
    assert axis == "X"
    assert blockers == ("linear reference-station span 10 mm differs from nominal 20 mm",)


def test_a_partially_measured_group_cannot_render_from_its_incomplete_subset():
    missing = "one referenced shape is unavailable"
    assert _dimension_geometry_blockers("linear", (missing,), ()) == (missing,)
    assert _dimension_geometry_blockers(
        "thickness", (missing,), ("thickness reference groups occupy the same station",)
    ) == (missing, "thickness reference groups occupy the same station")
    # Canonical-correlation blockers are added after extraction and deliberately never enter
    # this source-geometry gate; #1116's standalone fallback therefore remains renderable.
    assert _dimension_geometry_blockers("diameter", (missing,), ()) == ()


def test_ctc04_uses_authored_groups_and_reports_the_two_untruthful_records():
    report = extract_pmi_report(CTC04)
    records = {record.source_id: record for record in report.records if record.kind == "linear"}
    all_records = {record.source_id: record for record in report.records}

    oblique_diameter = all_records["dimension:0:1:4:25"]
    assert len(oblique_diameter.cylindrical_refs) == 8
    assert oblique_diameter.circular_refs == ()
    assert oblique_diameter.lowering_blockers == ()
    assert oblique_diameter.rendering_blockers == (
        "diameter cylindrical-reference axis is not principal-axis aligned",
    )

    circular_pattern = all_records["dimension:0:1:4:28"]
    assert circular_pattern.cylindrical_refs == ()
    assert len(circular_pattern.circular_refs) == 30
    assert {reference.diameter for reference in circular_pattern.circular_refs} == {20.0}
    assert {reference.principal_axis for reference in circular_pattern.circular_refs} == {"Z"}
    assert circular_pattern.lowering_blockers == ()
    assert circular_pattern.rendering_blockers == ()

    truthful = records["dimension:0:1:4:22"]
    assert truthful.value == 75.0
    assert truthful.dominant_axis == "Y"
    assert truthful.ref_pts == ((230.0, 285.0, 13.5), (230.0, 210.0, 13.5))
    assert truthful.lowering_blockers == ()

    oblique = records["dimension:0:1:4:26"]
    assert oblique.part21_id == "#19540"
    assert oblique.shape_aspect_ids == ("#19510", "#19520")
    assert oblique.reference_item_groups == (
        ("#13620", "#13329"),
        ("#2978", "#3570", "#3110", "#3438"),
    )
    assert oblique.dominant_axis == "?"
    assert oblique.lowering_blockers == ()
    assert "not principal-axis aligned" in oblique.rendering_blockers[0]

    one_sided = records["dimension:0:1:4:29"]
    assert one_sided.part21_id == "#20263"
    assert one_sided.shape_aspect_ids == ("#19510", "#20243")
    assert one_sided.reference_item_groups == (
        ("#13620", "#13329"),
        ("#17569", "#17576", "#17583", "#17590"),
    )
    assert one_sided.dominant_axis == "?"
    assert one_sided.ref_pts[0] == pytest.approx(
        (0.0, 149.9817407929034, -70.3212598115755), abs=1e-8
    )
    assert one_sided.ref_pts[1] == pytest.approx((0.0, 139.6760682565, -47.50973754875))
    assert one_sided.lowering_blockers == ()
    assert "not principal-axis aligned" in one_sided.rendering_blockers[0]

    outcomes = {source.source_id: source for source in report.sources}
    assert outcomes[truthful.source_id].outcome == "extracted"
    assert outcomes[oblique.source_id].outcome == "partially_extracted"
    assert outcomes[one_sided.source_id].outcome == "partially_extracted"


def test_exact_part21_groups_supersede_direct_xcaf_reference_failures(monkeypatch):
    import draftwright.pmi as pmi_module

    original = pmi_module._reference_geometry_with_groups

    def incomplete_xcaf_geometry(*args, **kwargs):
        points, bbox, axis, reasons, stations = original(*args, **kwargs)
        return (
            points,
            bbox,
            axis,
            (*reasons, "one referenced shape is unavailable"),
            stations,
        )

    monkeypatch.setattr(pmi_module, "_reference_geometry_with_groups", incomplete_xcaf_geometry)
    report = extract_pmi_report(CTC04)
    record = next(record for record in report.records if record.source_id == "dimension:0:1:4:22")
    source = next(source for source in report.sources if source.source_id == record.source_id)

    assert record.lowering_blockers == ()
    assert record.rendering_blockers == ()
    assert source.outcome == "extracted"
    assert source.reason == ""


def test_ap242_thickness_without_two_proven_groups_fails_closed():
    record = next(
        record for record in extract_pmi_report(CTC03).records if record.kind == "thickness"
    )

    assert record.source_id == "dimension:0:1:4:47"
    assert record.value == pytest.approx(20.828)
    assert record.label == "0.82 ±0.06 inch"
    assert record.dominant_axis == "?"
    assert record.lowering_blockers == ()
    assert record.rendering_blockers == (
        "thickness dimension needs two measurable authored reference groups",
    )


def test_render_gate_distinguishes_correlation_from_geometry_blockers():
    from build123d import Box

    from draftwright import Sheet

    sheet = Sheet(Box(40, 30, 20), number="1209")
    sheet.measured_dimension(
        kind="linear",
        value=16,
        label="16",
        dominant_axis="Y",
        ref_pts=((0, -8, 0), (0, 8, 0)),
        source_id="dimension:correlation-fallback",
        lowering_blockers=("unmatched hole correlation: no canonical owner",),
    )
    sheet.measured_dimension(
        kind="linear",
        value=25,
        label="25",
        dominant_axis="?",
        ref_pts=((0, 0, 0),),
        source_id="dimension:geometry-blocked",
        rendering_blockers=("linear dimension needs two measurable authored reference groups",),
    )
    sheet.authored_dimensions()
    drawing = sheet.build()

    features = {feature.source_id: feature for feature in drawing.model().features}
    fallback_feature = features["dimension:correlation-fallback"]
    blocked_feature = features["dimension:geometry-blocked"]
    assert drawing.registry.names_for_feature(fallback_feature)
    assert drawing.registry.names_for_feature(blocked_feature) == []
    unresolved = [
        issue for issue in drawing.lint() if issue.code == "authored_dim_source_unresolved"
    ]
    assert [issue.source_ids for issue in unresolved] == [("dimension:geometry-blocked",)]


def test_linear_witness_uses_stations_while_bbox_supplies_only_transverse_support():
    def identity(value):
        return value

    analysis = SimpleNamespace(
        proj=SimpleNamespace(
            front_x=identity,
            front_z=identity,
            side_x=identity,
            side_z=identity,
            plan_x=identity,
            plan_y=identity,
        )
    )
    record = SimpleNamespace(
        dominant_axis="Y",
        ref_pts=((230.0, 285.0, 13.5), (230.0, 210.0, 13.5)),
        # The old witness used this outer Y span: 292 - 203 = 89, contradicting label 75.
        ref_bbox=(223.0, 203.0, 0.0, 237.0, 292.0, 27.0),
    )

    p1, p2, support = _pmi_witness_from_bbox(record, "side", analysis)

    assert abs(p2[0] - p1[0]) == 75.0
    assert support == 13.5

    short = SimpleNamespace(
        dominant_axis="X",
        ref_pts=((0.0, 0.0, 0.0), (0.5, 0.0, 0.0)),
        ref_bbox=(0.0, -5.0, -5.0, 0.5, 5.0, 5.0),
    )
    p1, p2, _support = _pmi_witness_from_bbox(short, "front", analysis)
    assert abs(p2[0] - p1[0]) == 0.5
