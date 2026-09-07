"""#1155 — GRM-04 must use A4 and retain both Ø2.4 requirements."""

from pathlib import Path

from draftwright import build_drawing

_FIXTURE = Path(__file__).parent / "fixtures" / "grm04_drive_plate.step"


def test_grm04_measured_replan_keeps_diameter_and_location_on_a4():
    drawing = build_drawing(_FIXTURE, title="GRM-04")

    assert (drawing.page_w, drawing.page_h) == (297.0, 210.0)
    assert drawing.scale == 5.0
    assert drawing.scale_decision["status"] == "automatic_replanned"
    assert drawing.scale_decision["attempted_scales"] == (2.0, 5.0)
    assert [item["reason"] for item in drawing.scale_decision["attempts"]] == [
        "measured_upscale",
        "measured_upscale",
    ]
    assert all(item["page"] == (297.0, 210.0) for item in drawing.scale_decision["attempts"])

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
        or issue.code in {"annotation_overlap", "annotation_out_of_bounds", "view_overlap"}
    ]
