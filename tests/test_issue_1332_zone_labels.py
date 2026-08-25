"""`--zones` must not disable the ink check (#1332, review round 5).

The tightness gate used to compare a label box's shorter side against the
*sheet's own median*. `min(width, height)` on a one-character label is the glyph
**width**, not the text height, so `--zones` — a public CLI flag and `Sheet`
option — put 28 single-character zone labels on the sheet, dragged the median to
1.051 mm, and pushed the threshold below the ordinary 2.166 mm label height.
Every real label was then judged untight and the whole check reported nothing.

Four findings became zero, silently, through a documented user-facing flag. This
pins the property that fixed it: the gate depends on the box alone.
"""

from draftwright.linting.structural import lint_drawing

_CODE = "annotation_ink_overlap"


class _Bounds:
    def __init__(self, box):
        self.min = type("P", (), {"X": box[0], "Y": box[1], "Z": 0.0})()
        self.max = type("P", (), {"X": box[2], "Y": box[3], "Z": 0.0})()


class _Annotation:
    """The small duck-typed surface read by ``lint_drawing``."""

    def __init__(self, label, label_bbox, segments, full=None):
        self.label = label
        self.label_bbox = label_bbox
        self.segments = segments
        self._full = full or label_bbox
        self.elbow = None

    def bounding_box(self):
        return _Bounds(self._full)


def _crossing_pair():
    target = _Annotation("75.5", (10.0, 10.0, 20.0, 12.2), [])
    crosser = _Annotation(
        "35",
        (30.0, 30.0, 35.0, 32.2),
        [((0.0, 11.0), (40.0, 11.0))],
        (0.0, 11.0, 40.0, 32.2),
    )
    return target, crosser


def _zone_labels():
    # Narrow one-character boxes reproduce the population that used to lower the sheet-wide
    # median below an ordinary label's text height. They are separated and carry no ink.
    return [
        _Annotation(chr(65 + index % 26), (100 + 5 * index, 100, 101 + 5 * index, 102.2), [])
        for index in range(28)
    ]


def _crossings(annotations):
    return sorted(issue.message for issue in lint_drawing(annotations) if issue.code == _CODE)


def test_the_fixture_actually_reports_a_crossing():
    assert _crossings(list(_crossing_pair()))


def test_zone_labels_do_not_change_the_findings():
    without_zones = _crossings(list(_crossing_pair()))
    with_zones = _crossings([*_crossing_pair(), *_zone_labels()])
    assert with_zones == without_zones
