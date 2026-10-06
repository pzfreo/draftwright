"""The Specify AP242 plate keeps source locations ahead of generated grid pitch (#2192)."""

from pathlib import Path

from draftwright import build_drawing

FIXTURE = Path(__file__).parent / "fixtures/specify_plate_2192_ap242.step"
FORTY_SOURCES = {
    "dimension:0:1:4:2",
    "dimension:0:1:4:13",
    "dimension:0:1:4:15",
}


def test_source_basic_locations_and_generated_grid_pitch_share_default_sheet():
    drawing = build_drawing(FIXTURE, pmi="annotate", out=None)
    authored = {
        feature.source_id: feature
        for feature in drawing.model().features
        if feature.kind == "authored_dimension" and feature.source_id in FORTY_SOURCES
    }

    assert set(authored) == FORTY_SOURCES
    for feature in authored.values():
        ink = drawing.annotations_of(feature)
        assert len(ink) == 1
        assert next(iter(ink.values())).label == "40"
    assert {"dim_pitch_plan0_0", "dim_pitch_plan0_1"} <= set(drawing.annotations())
    assert "m_gdt4" in drawing.annotations()
    assert not [
        issue
        for issue in drawing.lint()
        if issue.code
        in {
            "pmi_dropped",
            "hole_pattern_dim_dropped",
            "annotation_overlap",
            "annotation_ink_overlap",
        }
    ]
