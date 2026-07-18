"""balloons — the leadered hole-balloon render pass (#111/#516).

Moved down from the ``Drawing`` result object (#699): a render pass belongs in the
render layer, not hosted above it — the orchestrator used to call *up* into
``dwg.add_balloons`` by duck-typing. The band-assignment solver it drives
(:func:`layout._assign_balloon_bands`, min-cost max-flow) lives in ``layout.py``
with the other solvers (ADR 0003/0009). ``Drawing.add_balloons`` remains the
public verb: an owner method that threads the build state into this pass.
"""

from __future__ import annotations

import math

from build123d import Align, Circle, Compound, Location, Mode, Text
from build123d_drafting.helpers import Leader

from draftwright._core import _STRIP_GAP, _STRIP_SPACING
from draftwright.annotations._common import strip_obstacles
from draftwright.fonts import PLEX_MONO
from draftwright.layout import (
    _assign_balloon_bands,
    _greedy_strip_1d,
    _solve_strip_1d,
    _strip_capacity,
)


def render_balloons(dwg, a, view, specs, ctx):
    """Place a leadered balloon for each ``(tag, j, hole)`` in *specs*,
    fitted into the halo the layout reserved around the view (#111).

    Each hole is assigned to a reserved band — left, right, top or (when the
    FV↔PV gap has room) bottom of the plan view — by a global
    max-cardinality/min-cost assignment (:func:`layout._assign_balloon_bands`,
    #516), and the balloons in each band are spread along it with the 1D strip
    solver so none overlap, each pulled toward its hole's coordinate.  A
    :class:`Leader` then runs from the hole rim to the glyph.  Because the
    layout reserved this band before placing the views (:func:`_est_plan_halo`
    / :func:`_will_balloon`), the balloons sit in clear space off the part and
    no leader crosses a neighbouring view.

    The drawing is duck-typed as *dwg* and touched only through its public
    surface; build state rides *a* and *ctx* (ADR 0005 §2 / #639, #699).
    """
    pp = dwg.coords(view).pp
    fs = dwg.draft.font_size
    r = fs * 1.5  # circle comfortably larger than the glyph
    standoff = _STRIP_GAP
    gap = 2 * r + 2 * _STRIP_SPACING  # min centre-to-centre: balloon + padding both sides

    # Plan-view page edges; the reserved bands sit just outside them.
    pl, pr = a.PV_X - a.fv_hw, a.PV_X + a.fv_hw
    pt, pb = a.PV_Y + a.pv_hh, a.PV_Y - a.pv_hh
    sv_left = a.SV_X - a.sv_hw
    margin, ph, pw = a.margin, a.PAGE_H, a.PAGE_W

    # Stack the balloon ring *beyond* the annotations already placed around the
    # plan view, not on top of them (#121). Measure the REAL depth every placed
    # occupant extends into each band from its full rendered footprint — leader
    # shafts, centreline geometry, tables, and bare extension lines included.
    # This intentionally shares the same full-footprint occupancy source as
    # corridor placement (#518), instead of re-growing a per-furniture allowlist.
    top_dim = bot_dim = left_dim = right_dim = 0.0
    for x0, y0, x1, y1 in strip_obstacles(dwg, view=view):
        if x1 > pl and x0 < pr:  # spans the plan's width → top/bottom bands
            if y1 > pt:
                top_dim = max(top_dim, y1 - pt)
            if y0 < pb:
                bot_dim = max(bot_dim, pb - y0)
        if y1 > pb and y0 < pt:  # spans the plan's height → left/right bands
            if x0 < pl:
                left_dim = max(left_dim, pl - x0)
            if x1 > pr:
                right_dim = max(right_dim, x1 - pr)

    # A dense part can stack many pitch dims on one side (holes._place_pitch_dim
    # pushes each successive one 10 mm further out, #92), so the measured depth
    # can exceed the room between the view and the page edge. Clamp each band so
    # its *ring itself* never lands off the drawable area (#349 follow-up) — the
    # ring then sits at the margin and overlaps the far witness lines instead,
    # which is only a tolerated warning (structural.py compares label_bbox, not
    # the full bbox, for overlap), never the out_of_bounds error.
    left_dim = min(left_dim, max(0.0, pl - standoff - 2 * r - margin))
    right_dim = min(right_dim, max(0.0, pw - margin - pr - standoff - 2 * r))
    top_dim = min(top_dim, max(0.0, ph - margin - pt - standoff - 2 * r))
    bot_dim = min(bot_dim, max(0.0, pb - standoff - 2 * r - margin))

    # A bottom band (below PV, beyond the overall-width dim) is usable only
    # when the FV↔PV gap has room for the width dim *and* a balloon row;
    # otherwise bottom-edge holes fall back to the nearest side/top band.
    bottom_line = pb - bot_dim - standoff - r
    has_bottom = pb - (a.FV_Y + a.fv_hh) > bot_dim + standoff + 2 * r

    # left/right balloons vary in Y at a fixed X just outside the part; top
    # and bottom balloons vary in X at a fixed Y just beyond it. Each line is
    # offset by its side's dim depth so the ring sits clear of the dims.
    band_defs = {
        "left": ("y", pl - left_dim - standoff - r, margin + r, ph - margin - r),
        "right": ("y", pr + right_dim + standoff + r, margin + r, ph - margin - r),
        "top": ("x", pt + top_dim + standoff + r, pl - standoff, sv_left - r),
        "bottom": ("x", bottom_line, pl - standoff, sv_left - r),
    }

    # Globally assign holes across the usable reserved bands.  Nearest-band
    # greedy could crowd one side and drop balloons while another side sat
    # empty; the assignment maximises placed balloons first, then minimises
    # leader distance to the ACTUAL post-depth band lines (#516).
    members = []
    choices_by_member = []
    for tag, j, hole in specs:
        cx, cy = pp(*hole.location)
        choices = {
            "left": abs(cx - band_defs["left"][1]),
            "right": abs(band_defs["right"][1] - cx),
            "top": abs(band_defs["top"][1] - cy),
        }
        if has_bottom:
            choices["bottom"] = abs(cy - band_defs["bottom"][1])
        members.append((tag, j, hole, cx, cy))
        choices_by_member.append(choices)

    capacities = {
        name: (_strip_capacity(lo, hi, gap) if name != "bottom" or has_bottom else 0)
        for name, (_axis, _line, lo, hi) in band_defs.items()
    }
    bands, dropped = _assign_balloon_bands(members, choices_by_member, capacities)
    dropped += _place_band(
        dwg,
        view,
        bands["left"],
        *band_defs["left"],
        gap,
        fs,
        r,
        ctx,
    )
    dropped += _place_band(
        dwg,
        view,
        bands["right"],
        *band_defs["right"],
        gap,
        fs,
        r,
        ctx,
    )
    dropped += _place_band(
        dwg,
        view,
        bands["top"],
        *band_defs["top"],
        gap,
        fs,
        r,
        ctx,
    )
    dropped += _place_band(
        dwg,
        view,
        bands["bottom"],
        *band_defs["bottom"],
        gap,
        fs,
        r,
        ctx,
    )
    # A band too crowded to hold every balloon drops its tail (the strip solver's
    # prefix fallback) — record it instead of letting the balloons vanish silently
    # (review follow-up). The resolver keeps the callout_dropped lint for a pattern
    # whose balloon did not land, so a missing pattern balloon is still a coverage gap.
    if dropped:
        ctx.record_issue(
            "warning",
            "balloon_dropped",
            f"{dropped} balloon(s) could not fit their reserved band and were dropped",
        )


def _place_band(dwg, view, members, axis, line, lo, hi, gap, fs, r, ctx) -> int:
    """Spread *members* (``(tag, j, hole, cx, cy)``) along one reserved band
    with the strip solver, then render a leadered balloon for each (#111).

    *axis* is the band's free axis (``"y"`` for the left/right bands, ``"x"``
    for the top); *line* is the fixed coordinate of the other axis.  Overflow
    beyond ``[lo, hi]`` drops the tail rather than running balloons off-page.
    Returns the number of members dropped, so the caller can surface it as lint
    (a silently truncated balloon leaves a hole undocumented — the resolver
    must know, review follow-up).
    """
    if not members:
        return 0
    k = 4 if axis == "y" else 3  # index of cy / cx in the member tuple
    members.sort(key=lambda m: m[k])
    naturals = [m[k] for m in members]
    coords = (
        _solve_strip_1d(naturals, gap, lo, hi)
        or _greedy_strip_1d(naturals, gap, lo, hi)
        or _greedy_strip_1d(naturals, gap, lo, hi, prefix=True)
    )
    for (tag, j, hole, cx, cy), c in zip(members, coords):
        bx, by = (line, c) if axis == "y" else (c, line)
        _render_balloon(dwg, view, tag, j, hole, cx, cy, bx, by, fs, r, ctx)
    return len(members) - len(coords)


def _render_balloon(dwg, view, tag, j, hole, cx, cy, bx, by, fs, r, ctx):
    """Build and add one balloon glyph + leader at solved centre ``(bx, by)``
    for hole ``(cx, cy)`` (#111)."""
    loc = Location((bx, by, 0))
    # The annotation layer fills closed paths, so a circle edge renders as a
    # disc. A thin annular FACE fills as a ring — i.e. a circle outline.
    ring_faces = [f.moved(loc) for f in (Circle(r) - Circle(r - 0.35)).faces()]
    text = Text(
        txt=tag,
        font_size=fs,
        font_path=PLEX_MONO,
        align=(Align.CENTER, Align.CENTER),
        mode=Mode.PRIVATE,
    ).locate(loc)
    parts = [*ring_faces, *text.faces()]
    # Leader from the hole rim to the balloon's near edge — the glyph is the
    # label, so label="".  Skipped when the balloon could not clear the hole
    # (degenerate fallback), where a leader would be a stub through the ring.
    dx, dy = bx - cx, by - cy
    dist = math.hypot(dx, dy)
    hole_r = hole.diameter * dwg.scale / 2
    if dist > hole_r + r:
        ux, uy = dx / dist, dy / dist
        tip = (cx + ux * hole_r, cy + uy * hole_r, 0)
        elbow = (bx - ux * r, by - uy * r, 0)
        parts.append(Leader(tip, elbow, "", dwg.draft))
    balloon = Compound(children=parts)
    # Furniture that legitimately sits on the view geometry — exempt from the
    # annotation-overlap / centreline lint, as the section arrows do.
    balloon.is_centerline = True
    dwg.add(
        balloon,
        f"balloon_{view}_{tag}_{j}",
        view=view,
        feature=ctx.feature_of_hole_at(hole.location),
    )
