"""#1155 — GRM-04 must retain both Ø2.4 requirements on a clean sheet."""

from pathlib import Path

from draftwright import build_drawing

_FIXTURE = Path(__file__).parent / "fixtures" / "grm04_drive_plate.step"


def test_grm04_measured_replan_keeps_diameter_and_location_on_a_clean_sheet():
    drawing = build_drawing(_FIXTURE, title="GRM-04")

    # The A4/2:1 sheet carries both requirements. Larger scales on that page
    # intersect the title block or other view ink, so they are rejected.
    assert (drawing.page_w, drawing.page_h) == (297.0, 210.0)
    assert drawing.scale == 2.0
    assert drawing.scale_decision["status"] == "automatic"
    assert [
        (item["scale"], item["status"], item.get("rejection"))
        for item in drawing.scale_decision["attempts"]
    ] == [
        (2.0, "detail_reservation_conservative", None),
        (5.0, "rejected", "structural_error"),
        (10.0, "rejected", "structural_error"),
    ]

    hole = next(
        feature
        for feature in drawing.model().features
        if feature.kind == "hole" and abs(feature.diameter - 2.4) < 1e-6
    )
    carried = {
        measurement.parameter
        for name in drawing.annotations()
        for measurement in drawing.registry.measurement_of(name)
        if measurement.feature is hole
    }
    assert {
        "bore.diameter",
        "location_off_axis.location.member.0.y",
        "location_off_axis.location.member.0.z",
    } <= carried
    assert any(
        "2.4" in str(getattr(drawing.get_annotation(name), "label", ""))
        for name in drawing.annotations()
    )
    assert not [
        issue
        for issue in drawing.lint()
        if issue.code.endswith("_dropped")
        or "overlap" in issue.code
        or issue.code in {"annotation_out_of_bounds", "view_out_of_bounds"}
    ]
