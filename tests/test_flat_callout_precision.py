"""Mixed physical flat sizes use one readable precision on the frame."""

import pytest

from draftwright import build_drawing
from draftwright.annotations._common import _geom_box, dim_footprint


@pytest.fixture(scope="module")
def flat_precision_drawing():
    """Share the read-only whistle-frame drawing across its precision checks."""
    dwg = build_drawing("tests/fixtures/issue_1595_whistle_key_frame.step")
    names = set(dwg.annotations())
    assert len([name for name in names if name.startswith("m_flat_")]) == 3
    assert {"hc_side0", "dim_loc_side_z1420", "m_locx0", "m_locy0"} <= names
    return dwg


def test_distinct_flat_sizes_keep_distinct_uniformly_formatted_callouts_issue_1601(
    flat_precision_drawing,
):
    dwg = flat_precision_drawing
    labels = {
        dwg.get_annotation(name).label for name in dwg.annotations() if name.startswith("m_flat_")
    }
    assert labels == {"4× 6.00 A/F", "4× 6.05 A/F", "6× 6.50 A/F"}


def test_side_callout_respects_the_future_height_location_label_issue_1601(flat_precision_drawing):
    dwg = flat_precision_drawing
    assert dwg.get_annotation("hc_side0").label == "3× ⌀2.4 ↧ 1.5"
    assert dwg.get_annotation("dim_loc_side_z1420").label == "14.2"
    assert not [
        issue
        for issue in dwg.lint()
        if issue.code in {"annotation_overlap", "annotation_ink_overlap"}
    ]


def test_short_datum_locations_keep_their_text_clear_of_both_terminators_issue_1601(
    flat_precision_drawing,
):
    dwg = flat_precision_drawing
    for name in ("m_locx0", "m_locy0"):
        dim = dwg.get_annotation(name)
        spec = dim.placement_spec
        assert dim.label == "3.5"
        assert dim.label_bbox[0] > spec.p2[0] + spec.draft.arrow_length
        estimate = dim_footprint(
            spec.p1,
            spec.p2,
            spec.side,
            spec.distance,
            spec.draft,
            dim.label,
            label_offset_x=spec.kwargs["label_offset_x"],
        )
        actual = _geom_box(dim)
        assert actual is not None
        assert all(
            bound <= value + 0.15 if index < 2 else bound >= value - 0.15
            for index, (bound, value) in enumerate(zip(estimate, actual, strict=True))
        )
