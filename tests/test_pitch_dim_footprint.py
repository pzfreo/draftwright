"""#602: the pitch-dim fallback probes with analytical footprints, not built geometry.

Two guards on the perf fix:

1. ``dim_footprint`` must stay metrically faithful to the real ``Dimension`` bbox —
   the probe loop's accept/reject decisions ride on it, so a drifting estimate either
   reintroduces per-probe OCC builds (via constant validation failures) or shifts
   placements. Tolerance 0.15 mm: the estimate is exact except for a deliberate
   uniform ``line_width/2`` stroke pad (±0.05 mm at preset weights).

2. The grid benchmark part must place its pitch dims in a *bounded* number of
   ``Dimension`` constructions — the issue's acceptance criterion ("prefer asserting
   geometry-construction counts over flaky wall-clock limits"). Pre-#602 this part
   built 88 dimensions for 4 placed pitch dims; the bound allows the accept build,
   the optional centre-line label-shift rebuild, and one validation-fallback retry
   per placed dim.

The label-wider-than-the-line case (helpers relocates the label externally) is a
documented footprint mismatch: the estimate under-covers, the accept-time validation
build rejects, and the loop resumes — correctness holds, one probe is wasted. It is
deliberately not asserted tight here.
"""

from __future__ import annotations

import pytest
from build123d import Box, Cylinder, Pos
from build123d_drafting import draft_preset

from draftwright import build_drawing
from draftwright._core import _dim
from draftwright.annotations._common import _geom_box, dim_footprint


@pytest.mark.parametrize(
    ("p1", "p2", "side", "distance", "label"),
    [
        pytest.param((10, 20, 0), (40, 20, 0), (0, -1, 0), 15, "2× 15", id="horiz-below"),
        pytest.param((10, 20, 0), (40, 20, 0), (0, 1, 0), 22.5, "4× 7.5", id="horiz-above"),
        pytest.param((30, 10, 0), (30, 60, 0), (-1, 0, 0), 18, "2× 25", id="vert-left"),
        pytest.param((30, 10, 0), (30, 60, 0), (1, 0, 0), 40, "3× 16.7", id="vert-right"),
        pytest.param((10, 10, 0), (40, 30, 0), (-0.5547, 0.83205, 0), 20, "2× 18", id="diagonal"),
    ],
)
def test_dim_footprint_matches_real_geometry(p1, p2, side, distance, label):
    draft = draft_preset(font_size=3.5, decimal_precision=1)
    real = _geom_box(_dim(p1, p2, side, distance, draft, label=label))
    est = dim_footprint(p1, p2, side, distance, draft, label)
    assert real is not None
    assert max(abs(e - r) for e, r in zip(est, real)) <= 0.15


def test_dim_footprint_matches_name_resolved_font():
    # font_path=None opts out of path-pinning: the renderer resolves the font *name*
    # through the OS stack, and the footprint must measure with the same resolution
    # (est and real go through the same fallback, so they agree on any platform).
    draft = draft_preset(font_size=3.5, decimal_precision=1, font_path=None)
    real = _geom_box(_dim((10, 20, 0), (40, 20, 0), (0, -1, 0), 15, draft, label="2× 15"))
    est = dim_footprint((10, 20, 0), (40, 20, 0), (0, -1, 0), 15, draft, "2× 15")
    assert real is not None
    assert max(abs(e - r) for e, r in zip(est, real)) <= 0.15


def _grid_plate():
    # The #602 benchmark part (same as the refactor-golden fixture): 15 holes in two
    # regular grids, whose pitch dims exhaust the strip carve and exercise the
    # bounded-offset fallback.
    plate = Box(120, 80, 10)
    for i in range(3):
        for j in range(3):
            plate -= Pos(-45 + i * 15, -15 + j * 15, 0) * Cylinder(2.5, 10)
    for i in range(2):
        for j in range(3):
            plate -= Pos(25 + i * 20, -20 + j * 18, 0) * Cylinder(4, 10)
    return plate


def _scattered_plate():
    # The #602 second benchmark (same as the refactor-golden fixture): ten holes, four
    # diameters, no pattern → a large corridor-candidate set through
    # place_strip_candidates' measure/plan/build seam.
    plate = Box(140, 90, 12)
    spots = [
        (-55, -30, 3),
        (-40, 25, 4),
        (-20, -10, 2.5),
        (-5, 35, 5),
        (10, -35, 3),
        (25, 10, 4),
        (40, -20, 2.5),
        (55, 30, 5),
        (60, -38, 3),
        (-60, 38, 4),
    ]
    for x, y, r in spots:
        plate -= Pos(x, y, 0) * Cylinder(r, 12)
    return plate


def test_corridor_dim_constructions_bounded(monkeypatch):
    # #602 queue item 2: corridor candidates are measured analytically (footprint
    # callables) or by ONE probe build; evaluation runs on predicted boxes; a placed
    # dim is built once. So total Dimension constructions must stay close to the
    # PLACED count — before the seam this part built 59 for 21 placed (probe per
    # candidate per pass + a rebuild per refill-loop iteration).
    import draftwright._core as core

    builds = 0
    real_dimension = core.Dimension

    def counting_dimension(p1, p2, side, distance, draft, **kwargs):
        nonlocal builds
        builds += 1
        return real_dimension(p1, p2, side, distance, draft, **kwargs)

    monkeypatch.setattr(core, "Dimension", counting_dimension)
    dwg = build_drawing(_scattered_plate())

    from build123d_drafting.helpers import Dimension

    placed = [name for name, o in dwg.iter_annotations() if isinstance(o, Dimension)]
    assert placed, "fixture no longer places dimensions — the guard lost its subject"
    assert builds <= len(placed) + 5, (
        f"{builds} Dimension builds for {len(placed)} placed — the #602 corridor "
        f"measure/build separation regressed to probing or re-evaluating with built geometry"
    )


def test_grid_pitch_dim_constructions_bounded(monkeypatch):
    # Count at the construction site (_core.Dimension, which every _dim call resolves
    # at call time) rather than white-box patching annotations/ internals — the
    # test_private_test_imports ratchet forbids new private imports from holes.
    import draftwright._core as core

    pitch_builds = 0
    real_dimension = core.Dimension

    def counting_dimension(p1, p2, side, distance, draft, **kwargs):
        nonlocal pitch_builds
        if "× " in (kwargs.get("label") or ""):
            pitch_builds += 1
        return real_dimension(p1, p2, side, distance, draft, **kwargs)

    monkeypatch.setattr(core, "Dimension", counting_dimension)
    dwg = build_drawing(_grid_plate())

    placed = [name for name, _ in dwg.iter_annotations() if name.startswith("dim_pitch_")]
    assert placed, "fixture no longer places pitch dims — the guard lost its subject"
    assert pitch_builds <= 3 * len(placed), (
        f"{pitch_builds} Dimension builds for {len(placed)} placed pitch dims — the "
        f"#602 footprint probe regressed to constructing geometry for rejected offsets"
    )
