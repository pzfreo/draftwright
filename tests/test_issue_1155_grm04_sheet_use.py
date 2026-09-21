"""#1155 — GRM-04 must retain both Ø2.4 requirements on a clean sheet."""

from pathlib import Path

from draftwright import build_drawing

_FIXTURE = Path(__file__).parent / "fixtures" / "grm04_drive_plate.step"


def test_grm04_measured_replan_keeps_diameter_and_location_on_a_clean_sheet():
    drawing = build_drawing(_FIXTURE, title="GRM-04")

    # The ISO-7200 title added by #1739 intersects side-view line-work on the former A4/5:1
    # result. Hard settled-layout validity therefore outranks paper economy and selects the
    # first complete, clean proposal instead of preserving that stale page expectation.
    assert (drawing.page_w, drawing.page_h) == (420.0, 297.0)
    assert drawing.scale == 5.0
    assert drawing.scale_decision["status"] == "automatic_replanned"
    assert any(
        item["status"] == "hard_layout_invalid" for item in drawing.scale_decision["attempts"]
    )
    assert drawing.scale_decision["attempts"][-1] == {
        "scale": 5.0,
        "status": "complete",
        "blockers": (),
        "reason": "page_escalation_after_hard_layout",
        "views": ("front", "plan", "side", "iso"),
        "page": (420.0, 297.0),
    }

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
