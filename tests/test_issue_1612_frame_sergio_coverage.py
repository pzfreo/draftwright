"""The opt-in filing layout retains source-owned frame hole locations."""

from pathlib import Path

from draftwright import build_drawing
from draftwright.linting.hole_coverage import hole_requirement_outcomes

FRAME = Path(__file__).parent / "fixtures" / "issue_1595_whistle_key_frame.step"


def test_sergio_frame_repaired_location_retains_hole_coverage():
    drawing = build_drawing(
        str(FRAME),
        frame=True,
        zones=True,
        margin_left=25,
        margin_right=10,
        margin_top=10,
        margin_bottom=10,
        title_block_width=175,
    )
    outcomes = hole_requirement_outcomes(
        drawing.recognition(),
        drawing.model().features,
        drawing.registry,
        ownership=drawing.recognition_ownership(),
    )
    assert drawing.drawable_bounds == (25, 10, 410, 287)
    assert drawing.get_annotation("title_block").block_bbox["width"] == 175
    assert len(outcomes) == 15
    assert all(row.state == "placed" and row.carriers for row in outcomes)
    assert not any(issue.code == "label_centerline_overlap" for issue in drawing.lint())
