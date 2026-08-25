"""Candidate prevention: dimension ink blocks peer labels before commit (#1334)."""

from build123d_drafting.helpers import Draft

from draftwright._core import _dim
from draftwright._geometry import _boxes_overlap
from draftwright.annotations._common import prevent_dimension_label_ink
from draftwright.linting.ink_overlap import (
    MIN_CROSSING_MM,
    crossable_region,
    crossing_length,
    label_crossings,
    segments_of,
)


def _short_chain():
    draft = Draft(font_size=3.0, arrow_length=2.7, line_width=0.1)
    return [
        (
            "short",
            _dim((20.0, 0.0, 0.0), (22.5, 0.0, 0.0), "above", 11.0, draft, label="0.5"),
        ),
        (
            "next",
            _dim((22.5, 0.0, 0.0), (32.5, 0.0, 0.0), "above", 11.0, draft, label="2"),
        ),
    ]


def _stage1_crossings(batch):
    found = []
    for left_index, (_left_name, left) in enumerate(batch):
        left_segments = segments_of(left)
        left_label = crossable_region(
            left.label_bbox,
            item=left,
            segments=left_segments,
        )
        for _right_name, right in batch[left_index + 1 :]:
            right_segments = segments_of(right)
            right_label = crossable_region(
                right.label_bbox,
                item=right,
                segments=right_segments,
            )
            found.extend(
                label_crossings(
                    left_segments,
                    right_segments,
                    label_a=left_label,
                    label_b=right_label,
                )
            )
    return found


def _foreign_arrow_tips_in_labels(batch):
    found = []
    for target_name, target in batch:
        label = target.label_bbox
        for source_name, source in batch:
            if source_name == target_name:
                continue
            for point in (source._dw_spec.p1, source._dw_spec.p2):
                if label[0] + 0.25 < point[0] < label[2] - 0.25:
                    found.append((source_name, target_name))
    return found


def test_same_batch_dimension_ink_selects_clear_label_candidates():
    natural = _short_chain()
    semantic_evidence = ((object(), "location.location.x", (22.5, 0.0, 0.0)),)
    natural[1][1].covers_hole_locations = semantic_evidence
    assert len(_stage1_crossings(natural)) == 2
    assert _foreign_arrow_tips_in_labels(natural)

    placed = prevent_dimension_label_ink(natural, page=(0.0, 0.0, 100.0, 100.0))

    assert _stage1_crossings(placed) == []
    assert _foreign_arrow_tips_in_labels(placed) == []
    assert placed[1][1].covers_hole_locations == semantic_evidence
    assert any("label_offset_x" in dim._dw_spec.kwargs for _name, dim in placed)

    # The bounded solve is deterministic, including its exact selected offsets.
    replay = prevent_dimension_label_ink(_short_chain(), page=(0.0, 0.0, 100.0, 100.0))
    assert [dim._dw_spec.kwargs for _name, dim in replay] == [
        dim._dw_spec.kwargs for _name, dim in placed
    ]


def test_immutable_label_keeps_deterministic_linted_fallback():
    natural = _short_chain()

    placed = prevent_dimension_label_ink(
        natural,
        page=(0.0, 0.0, 100.0, 100.0),
        immutable={"short", "next"},
    )

    assert placed[0][1] is natural[0][1]
    assert placed[1][1] is natural[1][1]
    assert _stage1_crossings(placed), "an infeasible pin must remain visible to lint"


def test_clean_batch_is_a_zero_construction_fast_path():
    draft = Draft()
    clean = [
        ("left", _dim((0, 0, 0), (20, 0, 0), "above", 11, draft, label="20")),
        ("right", _dim((30, 0, 0), (50, 0, 0), "above", 11, draft, label="20")),
    ]

    placed = prevent_dimension_label_ink(clean, page=(0.0, 0.0, 100.0, 100.0))

    assert [dim for _name, dim in placed] == [dim for _name, dim in clean]
    assert all(placed[index][1] is clean[index][1] for index in range(len(clean)))


def test_candidate_move_does_not_create_contact_with_committed_ink():
    natural = _short_chain()
    # The otherwise-preferred rightward position for ``next`` lands here.  This box
    # represents annotation ink committed before an immediate step chain is built.
    committed = (28.2, 9.0, 29.5, 13.0)
    assert not _boxes_overlap(natural[1][1].label_bbox, committed)

    placed = prevent_dimension_label_ink(
        natural,
        page=(0.0, 0.0, 100.0, 100.0),
        obstacles=(committed,),
    )

    assert _stage1_crossings(placed) == []
    assert not any(_boxes_overlap(dim.label_bbox, committed) for _name, dim in placed)


def test_connected_short_segments_use_the_same_crossing_measure_as_lint():
    draft = Draft(font_size=3.0, arrow_length=2.7, line_width=0.1)
    source = _dim((0, 0, 0), (20, 0, 0), "above", 11, draft, label="20")
    target = _dim((30, 0, 0), (50, 0, 0), "above", 11, draft, label="20")
    # Each renderer piece is shorter than the lint floor, but they join inside the
    # target label and are therefore one 0.6 mm visual stroke.
    source._segments_local = [((39.7, 11.0), (40.0, 11.0)), ((40.0, 11.0), (40.3, 11.0))]
    region = crossable_region(target.label_bbox, item=target, segments=target.segments)
    assert crossing_length(source.segments, region) >= MIN_CROSSING_MM

    placed = prevent_dimension_label_ink(
        [("source", source), ("target", target)],
        page=(0.0, 0.0, 100.0, 100.0),
        immutable={"source"},
    )

    shifted = placed[1][1]
    shifted_region = crossable_region(
        shifted.label_bbox,
        item=shifted,
        segments=shifted.segments,
    )
    assert crossing_length(source.segments, shifted_region) < MIN_CROSSING_MM
