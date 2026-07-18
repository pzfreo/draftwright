"""#685: L-shaped occupancy — decomposed obstacle boxes, not one hull."""

from __future__ import annotations

from build123d_drafting import draft_preset

from draftwright._core import _dim
from draftwright.annotations._common import _geom_box, occupancy_boxes


def test_dim_decomposes_and_frees_the_empty_corner():
    draft = draft_preset(font_size=3.5, decimal_precision=1)
    dim = _dim((10, 20, 0), (18, 20, 0), (0, -1, 0), 10, draft, label="8")  # tight span
    hull = _geom_box(dim)
    boxes = occupancy_boxes(dim)
    assert len(boxes) >= 4  # strokes + label, not one hull
    # every piece lies within the hull (+ the junction pad)
    for b in boxes:
        assert b[0] >= hull[0] - 1.3 and b[2] <= hull[2] + 1.3
        assert b[1] >= hull[1] - 1.3 and b[3] <= hull[3] + 1.3
    # the hull's empty corner (between a witness and the dim-line band) is NOT
    # claimed by any piece — the point the one-box model wrongly blocked.
    corner = (hull[0] + 1.0, hull[3] - 1.0)
    assert not any(b[0] <= corner[0] <= b[2] and b[1] <= corner[1] <= b[3] for b in boxes)


def test_segmentless_annotation_keeps_its_hull():
    class Plain:
        def bounding_box(self):
            class B:
                class min:
                    X, Y = 1.0, 2.0

                class max:
                    X, Y = 5.0, 6.0

            return B()

    assert occupancy_boxes(Plain()) == [(1.0, 2.0, 5.0, 6.0)]
