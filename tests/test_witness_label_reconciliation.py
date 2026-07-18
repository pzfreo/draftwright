"""#690: no dimension label is crossed by a FOREIGN transverse stroke after a build.

The perpendicular-axis conflict class tier co-solving cannot fix: a location dim's
witness must cross the whole strip to reach its tier, so any inner label at that
height gets crossed wherever the tiers land. ``reconcile_witness_labels`` (repair.py)
runs after every corridor drains and shifts the crossed label along its own dim line
(the #129 remedy). The dshape fixture is the proven case: pre-#690 its height label
sat directly on the z-location dim's witness.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_layout_cleanliness import CORPUS  # noqa: E402

from draftwright import build_drawing  # noqa: E402
from draftwright.repair import reconcile_witness_labels  # noqa: E402


def _label_crossings(dwg):
    hits = []
    dims = [
        (n, o)
        for n, o in dwg.iter_annotations()
        if getattr(o, "_dw_spec", None) is not None and getattr(o, "label_bbox", None) is not None
    ]
    for name, dim in dims:
        s = dim._dw_spec
        lb = dim.label_bbox
        vertical = abs(s.p2[1] - s.p1[1]) > abs(s.p2[0] - s.p1[0])
        ax = 1 if vertical else 0
        oth = 1 - ax
        for other, oo in dwg.iter_annotations():
            if other == name:
                continue
            for (x0, y0), (x1, y1) in getattr(oo, "segments", None) or ():
                if (abs(y1 - y0) > abs(x1 - x0)) == vertical:
                    continue  # parallel: stacked shafts, not a crossing
                sb = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
                t = (sb[ax] + sb[ax + 2]) / 2.0
                if (
                    lb[ax] + 0.3 < t < lb[ax + 2] - 0.3
                    and min(sb[oth + 2], lb[oth + 2]) - max(sb[oth], lb[oth]) > 0.5
                ):
                    hits.append((name, other))
    return hits


def test_dshape_height_label_clears_the_location_witness():
    dwg = build_drawing(CORPUS["dshape"]())
    assert _label_crossings(dwg) == [], "a foreign transverse stroke crosses a dim label"
    # Idempotent: nothing left to shift on a reconciled drawing.
    assert reconcile_witness_labels(dwg) == 0
