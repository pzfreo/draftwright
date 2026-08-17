"""#1187 — the leaders that still cut the part have nowhere else to go.

Every placer that weighs routing now shares one material predicate (#798), and the
exact solve reaches dense parts (#1188). What remains is not unfinished work: on the
finished sheet these leaders have **no** route that is both clear of the body and clear
of everything already committed. Policy B keeps them, because a required callout at a
logged cost beats a missing dimension.

This test measures that claim rather than asserting it, so that if a future change frees
up space — or takes some away — the answer moves with the drawing instead of going
stale.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from build123d_drafting import Leader

from draftwright import build_drawing
from draftwright._core import _MARGIN
from draftwright._geometry import _boxes_overlap, material_reentry_span
from draftwright.annotations._common import CROSSABLE_TYPES, _box_hits, _geom_box, strip_obstacles
from draftwright.annotations.leaders import view_material

_BRIDGE = 0.05
_FLOOR = 0.25


def _clear_routes(dwg, name, *, directions=64, reaches=(0.6, 0.8, 1.0, 1.3, 1.7, 2.2)):
    """How many alternative routes for *name* are clear of BOTH the part and the sheet.

    Sweeps the elbow around the tip at several shaft lengths — far more freedom than any
    producer offers — and counts the alternatives that clear the material, land their
    label inside the page and off the part, and collide with no committed annotation.
    The leader under test is removed first so it is not counted as its own obstacle. It
    is not restored: the drawing is a throwaway measurement subject, and re-adding it
    would need the private placement primitive.
    """
    annotation = dwg.get_annotation(name)
    view = dwg.view_of(name)
    field = view_material(dwg, view)
    tip, elbow = annotation.tip, annotation.elbow
    reach = math.hypot(elbow[0] - tip[0], elbow[1] - tip[1])
    silhouette = dwg.view_bounds(view)
    page = (_MARGIN, _MARGIN, dwg.page_w - _MARGIN, dwg.page_h - _MARGIN)
    # Removed and NOT restored: this drawing is a throwaway measurement subject, and the
    # leader must not be counted as its own obstacle.
    dwg.remove(name)
    obstacles = strip_obstacles(dwg, crossable=CROSSABLE_TYPES)
    found = 0
    for step in range(directions):
        angle = 2 * math.pi * step / directions
        for scale in reaches:
            length = reach * scale
            candidate_elbow = (
                tip[0] + length * math.cos(angle),
                tip[1] + length * math.sin(angle),
                0,
            )
            if material_reentry_span(tip[:2], candidate_elbow[:2], field, bridge=_BRIDGE) > _FLOOR:
                continue
            candidate = Leader(
                tip=(tip[0], tip[1], 0),
                elbow=candidate_elbow,
                label=annotation.label,
                draft=dwg.draft,
            )
            box = getattr(candidate, "label_bbox", None) or _geom_box(candidate)
            geometry = _geom_box(candidate)
            if box is None or geometry is None:
                continue
            if box[0] < page[0] or box[1] < page[1] or box[2] > page[2] or box[3] > page[3]:
                continue
            if silhouette is not None and _boxes_overlap(box, silhouette):
                continue
            if _box_hits(geometry, obstacles):
                continue
            found += 1
    return found


@pytest.mark.slow
@pytest.mark.parametrize(
    ("fixture", "name"),
    [
        ("nist_ctc_05_asme1_ap242", "m_pocket_yz3"),
        ("nist_ctc_05_asme1_ap242", "m_bossdia_z2"),
        ("nist_ctc_04_asme1_ap203", "hc_plan3"),
        ("nist_ctc_02_asme1_ap203", "hc_plan8"),
        ("nist_ctc_02_asme1_ap203", "hc_plan12"),
    ],
)
def test_the_retained_crossings_have_no_clear_alternative(fixture, name):
    # Each of these cuts the part, and each has hundreds of routes that clear the
    # MATERIAL — so the shape of the part is not what traps them. Every one of those
    # routes collides with committed ink, the page margin, or the silhouette: the sheet
    # is full. Retaining the crossing is the correct Policy-B outcome, not a placement
    # bug, and it is why #1187 closes without forcing these onto clear routes.
    dwg = build_drawing(step_file=str(Path("tests/fixtures") / f"{fixture}.stp"))
    annotation = dwg.get_annotation(name)
    assert annotation is not None, f"{name} is no longer placed on {fixture}"
    field = view_material(dwg, dwg.view_of(name))
    cut = material_reentry_span(annotation.tip[:2], annotation.elbow[:2], field, bridge=_BRIDGE)
    assert cut > _FLOOR, f"{name} no longer cuts — remove it from this list"
    assert _clear_routes(dwg, name) == 0, (
        f"{name} on {fixture} now HAS a clear route ({cut:.1f} mm cut retained) — the "
        f"sheet has freed up, so this leader should be routed rather than retained"
    )
