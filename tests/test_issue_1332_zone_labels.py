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

import pytest
from build123d import Box, Cylinder, Pos

from draftwright import build_drawing

_CODE = "annotation_ink_overlap"


def _side_drilled():
    part = Box(80, 40, 30)
    for z, radius in ((-10, 1.0), (-3, 1.2), (4, 1.4), (11, 1.6)):
        part -= Pos(0, 0, z) * Cylinder(radius, 60, rotation=(0, 90, 0))
    return part


@pytest.fixture(scope="module")
def crossings_by_zones():
    part = _side_drilled()
    return {
        zones: sorted(
            issue.message
            for issue in build_drawing(part, zones=zones).lint()
            if issue.code == _CODE
        )
        for zones in (False, True)
    }


def test_the_fixture_actually_reports_crossings(crossings_by_zones):
    """The precondition. A part with no crossings would satisfy the equality
    below while proving nothing about zone labels."""
    assert crossings_by_zones[False], "fixture reports no crossings to compare"


def test_zone_labels_do_not_change_the_findings(crossings_by_zones):
    assert crossings_by_zones[True] == crossings_by_zones[False]
