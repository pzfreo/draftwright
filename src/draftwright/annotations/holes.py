"""Hole / pattern annotation pass (#138 / ADR 0005, P5d).

The largest annotation capability: per-hole callouts and balloons, location
dimensions (incl. side-drilled #133), pitch/grid pattern dims, hole-chart
furniture, and slots. Pass functions take the drawing duck-typed as `dwg`;
shared placement helpers come from annotations._common. Below annotate, no cycle.
"""

from __future__ import annotations

import math

from build123d_drafting.helpers import (
    CenterlineCircle,
    Leader,
)

from draftwright._core import (
    _MIN_LOC_SEP_MM,
    _TB_CLEAR,
    _TB_H,
    Analysis,
    _axis_letter,
    _dim,
    _fmt,
    _greedy_strip_ys,
    _iso_bbox,
    _log,
)
from draftwright.annotations._common import _anno_box, _box_hits, _occupied_boxes
from draftwright.annotations.from_model import callout_from_spec, hole_callout_spec
from draftwright.layout import LayoutSolver, Placeable
from draftwright.model import plan_dimensions
from draftwright.recognition import (
    BoltCircle,
    HoleSpec,
    LinearArray,
    RectGrid,
)


def _legible_locations(positions, scale):
    """Axis positions far enough apart on the page to dimension legibly.

    Given world-coordinate *positions* along one axis, keep a position only if it
    is at least ``_MIN_LOC_SEP_MM`` page-mm from the previously kept one;
    consecutive holes closer than that produce baseline witness lines that read
    as a single busy cluster (#43). Returns ``(kept, n_too_close)``: the
    positions to dimension and the count dropped for spacing (the caller surfaces
    these via ``location_ref_dropped`` lint; the full-fidelity answer is a detail
    view, #42). Mirrors :func:`_legible_steps` for hole locations.
    """
    kept: list[float] = []
    n_too_close = 0
    last = None
    for p in sorted(positions):
        if last is not None and (p - last) * scale < _MIN_LOC_SEP_MM:
            n_too_close += 1
            continue
        kept.append(p)
        last = p
    return kept, n_too_close


def _record_callout_drop(dwg, view, diam, reason):
    """Record a hole callout the layout could not place (#36).

    A warning (the drawing is incomplete, not invalid), whose diameter is
    excluded from ``feature_not_dimensioned`` like the old per-view cap drop —
    so a callout that genuinely doesn't fit is surfaced once, with a reason,
    and not double-reported.
    """
    dwg._drop_callout_diam(diam)
    dwg._record_build_issue(
        "warning",
        "callout_dropped",
        f"hole callout ø{_fmt(diam)} dropped from the {view} view ({reason})",
    )


def _locate_off_axis_holes(dwg, a: Analysis, holes_in=None):
    """Location dimensions for side-drilled holes (#133).

    An X-axis hole is a circle in the SIDE view (locate its Y below the view and
    its Z to the right — the side view has no left strip); a Y-axis hole is a
    circle in the FRONT view (locate its X below and its Z to the right). Each
    offset is allocated from the view's strip so dims stack without overlap, and
    this pass runs AFTER the envelope and turned-diameter passes so it can never
    evict an overall dimension. A tier with no room is dropped and recorded as
    ``off_axis_location_dropped`` — never force-stacked. Holes already covered by
    a pattern callout are skipped, as in the plan path.
    """
    draft = dwg.draft
    all_holes = a.holes if holes_in is None else holes_in
    patterned = {h for p in a.patterns for h in p.holes}
    off = [h for h in all_holes if _axis_letter(h) in ("x", "y") and h not in patterned]
    if not off:
        return
    SX, SZ = a.proj.side_x, a.proj.side_z
    FX, FZ = a.proj.front_x, a.proj.front_z
    dx, dy, dz = a.bb.min.X, a.bb.min.Y, a.bb.min.Z
    tier = draft.font_size + 2 * draft.pad_around_text
    occupied = _occupied_boxes(dwg)

    def _drop(axis, view):
        # Recorded at INFO under a code DISTINCT from the plan path's
        # ``location_ref_dropped`` (which is a warning). Two reasons:
        #  - Severity: a best-effort off-axis location dim that did not fit is not
        #    a drawing DEFECT (the sheet is correct — no overlap, in bounds), it is
        #    a completeness shortfall measured by the separate location-coverage
        #    score (see the eval scoreboard), not by lint. So a valid sheet stays
        #    lint-clean while the gap is still surfaced.
        #  - Distinct code: ``_maybe_tabulate_holes`` triggers the plan-view hole
        #    chart on ``location_ref_dropped`` and then clears it — a side-hole
        #    height that did not fit must not tabulate (or be erased by) the plan
        #    view, so it gets its own code.
        # (The plan path's primary top-view positions are expected on every
        # drawing, so a drop there stays a warning.)
        dwg._record_build_issue(
            "info",
            "off_axis_location_dropped",
            f"{axis} location dim for a {view}-view hole not placed (no room beside the view)",
        )

    def _place(strip, view, p_lo, p_hi, dist, label, name, side):
        # The strip cursor only tracks dims it allocated; the right strips are
        # SHARED with hole callouts (``hc_side``) and the section hatch, which
        # use other placers and are invisible to the cursor (#133). So a clean
        # allocation is necessary but not sufficient — verify the candidate's box
        # does not collide with an already-placed occupant before committing.
        # Returns True on success, False if there was no room/a collision (the
        # caller decides whether to fall back to another strip or drop).
        coord = strip.allocate(tier) if strip is not None else None
        if coord is None:
            return False
        dim = _dim(p_lo, p_hi, side, dist(coord), draft, label=_fmt(label))
        if _box_hits(_anno_box(dim), occupied):
            # The allocated tier is consumed even though we reject it here; that
            # only pushes later same-strip dims one tier outward (which then drop
            # cleanly if they overflow), so it is a benign waste, not a bug.
            return False
        dwg.add(dim, name, view=view)
        occupied.append(_anno_box(dim))
        return True

    def _below(strip, view, p_lo, p_hi, witness, label, axis):
        if not _place(
            strip,
            view,
            p_lo,
            p_hi,
            lambda c: witness - c,
            label,
            f"dim_loc_{view}_{axis}{round(label * 100)}",
            "below",
        ):
            _drop(axis, view)

    # In-plane offset: X-axis hole -> Y below the side view; Y-axis hole -> X
    # below the front view (each view's below strip is its own, uncontended).
    yw, xw = SZ(dz) - 2, FZ(dz) - 2
    seen_y, seen_x = set(), set()
    for h in (h for h in off if _axis_letter(h) == "x"):
        yo = round(abs(h.location[1] - dy), 2)
        if yo * a.SCALE >= 1.0 and yo not in seen_y:
            seen_y.add(yo)
            _below(
                a.sv_zones.below, "side", (SX(dy), yw, 0), (SX(h.location[1]), yw, 0), yw, yo, "y"
            )
    for h in (h for h in off if _axis_letter(h) == "y"):
        xo = round(abs(h.location[0] - dx), 2)
        if xo * a.SCALE >= 1.0 and xo not in seen_x:
            seen_x.add(xo)
            _below(
                a.fv_zones.below, "front", (FX(dx), xw, 0), (FX(h.location[0]), xw, 0), xw, xo, "x"
            )

    # Height offset (Z): a hole's height is visible to the RIGHT of both the side
    # and the front view. Neither right strip is universally free — the side
    # view's is contended by hole callouts (hc_side) + the section hatch, the
    # front view's by the dim_height/dim_step ladder — so try the natural strip
    # first and FALL BACK to the other before giving up (#133 rework). The
    # occupancy check in _place drops a candidate that would overprint a callout
    # or hatch the strip cursor cannot see, so neither strip overprints.
    zr, zrf = SX(a.bb.max.Y), FX(a.bb.max.X)
    seen_z = set()
    for h in off:
        zo = round(abs(h.location[2] - dz), 2)
        if zo * a.SCALE < 1.0 or zo in seen_z:
            continue
        seen_z.add(zo)
        hz = h.location[2]
        side_cand = (a.sv_zones.right, "side", (zr, SZ(dz), 0), (zr, SZ(hz), 0), zr)
        front_cand = (a.fv_zones.right, "front", (zrf, FZ(dz), 0), (zrf, FZ(hz), 0), zrf)
        order = (side_cand, front_cand) if _axis_letter(h) == "x" else (front_cand, side_cand)
        if not any(
            _place(
                strip,
                view,
                p_lo,
                p_hi,
                lambda c, e=edge: c - e,
                zo,
                f"dim_loc_{view}_z{round(zo * 100)}",
                "right",
            )
            for strip, view, p_lo, p_hi, edge in order
        ):
            _drop("Z", order[0][1])


def _add_furniture(dwg, a: Analysis, view, j, pattern, to_page):
    """Pattern sheet furniture, added once its callout is placed (#92)."""
    if pattern is not None:
        # Remember the bore-callout name AND the holes it documents, so a later
        # hole-table escalation leaves the grouped pattern callout standing and
        # tabulates only the holes no *placed* pattern callout covers (#92).
        # Recording here (callout already placed) — not from a.patterns — means a
        # pattern dropped for lack of room, or filtered off a rotational part,
        # correctly falls back to the table instead of going undocumented.
        dwg._cover_pattern(f"hc_{view}{j}", pattern.holes)
    if isinstance(pattern, BoltCircle):
        cx = sum(to_page(h)[0] for h in pattern.holes) / len(pattern.holes)
        cy = sum(to_page(h)[1] for h in pattern.holes) / len(pattern.holes)
        dwg.add(CenterlineCircle((cx, cy), pattern.diameter * a.SCALE), f"bc_{view}{j}", view=view)
    elif isinstance(pattern, LinearArray):
        _place_pitch_dim(
            dwg,
            a,
            view,
            pattern.holes[0],
            pattern.holes[-1],
            len(pattern.holes),
            pattern.pitch,
            to_page,
            f"dim_pitch_{view}{j}",
        )
    elif isinstance(pattern, RectGrid):
        _add_grid_pitch_dims(dwg, a, view, j, pattern, to_page)


def _add_grid_pitch_dims(dwg, a: Analysis, view, j, grid, to_page):
    """Both pitch dimensions of a rectangular grid — one along each lattice axis,
    each labelled ``(n-1)× pitch`` (#92).  The two axes are recovered as the two
    shortest near-orthogonal inter-hole page vectors (the recogniser's own
    basis); this is used only to pick the dimension endpoints and the per-axis
    count, not to re-recognise the grid (recognition stays upstream)."""
    pts = [to_page(h) for h in grid.holes]
    diffs = []
    for ia in range(len(pts)):
        for ib in range(len(pts)):
            if ia == ib:
                continue
            dx, dy = pts[ib][0] - pts[ia][0], pts[ib][1] - pts[ia][1]
            length = math.hypot(dx, dy)
            if length > 1e-6:
                diffs.append((length, dx, dy))
    if not diffs:
        return
    diffs.sort()
    l1, ax, ay = diffs[0]
    u1 = (ax / l1, ay / l1)
    basis2 = next(
        (
            (length, dx, dy)
            for length, dx, dy in diffs
            if abs((dx * u1[0] + dy * u1[1]) / length) < 0.2
        ),
        None,
    )
    if basis2 is None:
        return
    l2, bx, by = basis2
    u2 = (bx / l2, by / l2)
    nominals = (grid.row_pitch, grid.col_pitch)

    def _axis_dim(u, pitch_page, sub):
        perp = (-u[1], u[0])

        def along(idx):
            return pts[idx][0] * u[0] + pts[idx][1] * u[1]

        def across(idx):
            return pts[idx][0] * perp[0] + pts[idx][1] * perp[1]

        lo = min(range(len(pts)), key=along)
        # Keep the dimension on ONE lattice line: of the holes sharing lo's
        # perpendicular coordinate, take the far one along u. Picking the global
        # max-projection hole instead lands on the opposite diagonal corner and
        # draws the pitch dim diagonally across the grid (#92).
        # Tolerance must be below the PERPENDICULAR lattice-line spacing — which
        # is the *other* axis' pitch, so use the smaller of the two pitches.
        # (pitch_page * 0.25 fails on a high-aspect grid: for the long axis the
        # perpendicular lines are only the short pitch apart, and a quarter of
        # the long pitch can exceed that, merging two lines → diagonal again.)
        lo_across = across(lo)
        line_tol = min(l1, l2) * 0.25
        line = [idx for idx in range(len(pts)) if abs(across(idx) - lo_across) < line_tol]
        hi = max(line, key=along)
        span = along(hi) - along(lo)
        n = round(span / pitch_page) + 1
        # Label with the recogniser's nominal pitch nearest this axis' page step.
        pitch = min(nominals, key=lambda v: abs(v - pitch_page / a.SCALE))
        _place_pitch_dim(
            dwg,
            a,
            view,
            grid.holes[lo],
            grid.holes[hi],
            n,
            pitch,
            to_page,
            f"dim_pitch_{view}{j}_{sub}",
        )

    _axis_dim(u1, l1, 0)
    _axis_dim(u2, l2, 1)


def _place_pitch_dim(dwg, a: Analysis, view, h1, h2, n, pitch, to_page, name):
    """Pitch dimension between two hole centres ``h1``→``h2``, labelled
    ``(n-1)× pitch``, placed just outside the view on the side of the row's
    outward perpendicular (#92)."""
    p1 = to_page(h1)
    p2 = to_page(h2)
    ux, uy = p2[0] - p1[0], p2[1] - p1[1]
    norm = math.hypot(ux, uy)
    if norm < 1e-9:
        return
    ux, uy = ux / norm, uy / norm
    mid = ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)
    # view extents in page coordinates, to push the dim line outside
    if view == "plan":
        corners = [
            (a.proj.plan_x(x), a.proj.plan_y(y))
            for x in (a.bb.min.X, a.bb.max.X)
            for y in (a.bb.min.Y, a.bb.max.Y)
        ]
    elif view == "front":
        corners = [
            (a.proj.front_x(x), a.proj.front_z(z))
            for x in (a.bb.min.X, a.bb.max.X)
            for z in (a.bb.min.Z, a.bb.max.Z)
        ]
    else:
        corners = [
            (a.proj.side_x(y), a.proj.side_z(z))
            for y in (a.bb.min.Y, a.bb.max.Y)
            for z in (a.bb.min.Z, a.bb.max.Z)
        ]
    # Pick the perpendicular side from the page layout, not raw distance:
    # below the plan view sit dim_width and the front view, above the front
    # view sits the plan — so plan dims go up, front dims go down, and
    # vertical rows go left (callouts own the right strip). The side view
    # alone uses the shorter reach. A row far from its chosen side simply
    # gets long extension lines — standard practice when the near side is
    # occupied.
    reach_pos = max((c[0] - mid[0]) * -uy + (c[1] - mid[1]) * ux for c in corners)
    reach_neg = max((c[0] - mid[0]) * uy + (c[1] - mid[1]) * -ux for c in corners)
    cands = (((-uy, ux, 0), reach_pos), ((uy, -ux, 0), reach_neg))
    if view == "side":
        side, reach = min(cands, key=lambda c: c[1])
    else:
        pref = (-0.3, 1.0) if view == "plan" else (-0.3, -1.0)
        side, reach = max(cands, key=lambda c: c[0][0] * pref[0] + c[0][1] * pref[1])
    # stack further pitch dims in this view on outer tiers
    prior = sum(1 for nm, _ in dwg.iter_annotations() if nm.startswith(f"dim_pitch_{view}"))
    offset = reach + 8 + 10 * prior
    # never force-place: skip (and log) when the dim line would leave the page
    ox = mid[0] + side[0] * (offset + 6)
    oy = mid[1] + side[1] * (offset + 6)
    if not (a.margin <= ox <= a.PAGE_W - a.margin and a.margin <= oy <= a.PAGE_H - a.margin):
        _log.info(
            "Pitch dimension for the %s× %s array skipped (no room)",
            n,
            _fmt(pitch),
        )
        return
    dwg.add(
        _dim(
            (p1[0], p1[1], 0),
            (p2[0], p2[1], 0),
            side,
            offset,
            dwg.draft,
            label=f"{n - 1}× {_fmt(pitch)}",
        ),
        name,
        view=view,
    )


def _solve_strip_via_layout(naturals, min_gap, lo, hi, key_prefix):
    """Place a pre-sorted, uniform-gap 1D stack through the shared LayoutSolver
    (ADR 0003 phase 2, #80), returning positions in input order, or ``None`` if
    the stack does not fit.

    *naturals* must be ascending (the caller sorts the queue), so the solver's
    ``(natural, key)`` ordering — with the zero-padded keys built here — is the
    identity, and the result is byte-identical to the bare ``_solve_strip_1d``
    this replaces. The label width is irrelevant to a vertical stack, so each
    placeable carries the uniform ``min_gap`` as its height.
    """
    solver = LayoutSolver()
    keys = [f"{key_prefix}{j:04d}" for j in range(len(naturals))]
    for key, nat in zip(keys, naturals, strict=True):
        solver.register(
            Placeable(
                key=key,
                anchors=((0.0, nat),),
                size=(0.0, min_gap),
                dof_axis="y",
                natural=nat,
                min_gap=min_gap,
            )
        )
    # greedy_fallback=False so this returns exactly what the bare primitive did:
    # None when the strip is full, leaving the caller's prefix-drop to fire (#80).
    placed = solver.solve_strip(lo=lo, hi=hi, axis="y", greedy_fallback=False)
    if placed is None:
        return None
    return [placed[k] for k in keys]


def _annotate_holes(dwg, a: Analysis, view_of_axis, found_patterns, model, holes_in=None):
    """Leader-attached HoleCallouts, one per distinct hole spec per view (#91).

    Identical holes share one callout with an ``n×`` count prefix (#92's
    grouping half) — through holes group on diameter and steps regardless of
    wall thickness. The leader tip lands on the hole's circumference, on the
    group's hole nearest the callout.

    Placement: plan- and side-view callouts go to the right of their view
    (the strip before the iso view / page margin; plan falls back to its
    left, the side view has no usable left strip), front-view callouts go
    below the front view, deconflicted so no leader shaft crosses an earlier
    callout's text. Each callout is width-checked; anything that fits
    nowhere is logged and skipped — never force-placed — and then surfaces
    through the coverage lint as ``feature_not_dimensioned``.
    """
    draft = dwg.draft
    gap = draft.pad_around_text
    # Minimum vertical separation between stacked bore-callout labels: one label
    # height (font_size) plus pad_around_text clearance above and below, so
    # adjacent labels never touch.  Derived from text metrics rather than a bare
    # font-size ratio (#31).
    min_gap = draft.font_size + 2 * gap
    # Group on the same machining-spec key pattern detection uses (snapped
    # axis vector included): blind holes drilled from opposite faces are
    # different operations and get separate callouts, and a spec group's
    # hole set therefore lines up exactly with find_hole_patterns' groups.
    groups: dict = {}
    for h in a.holes if holes_in is None else holes_in:
        groups.setdefault(HoleSpec.from_hole(h), []).append(h)

    by_view: dict = {}
    for holes in groups.values():
        by_view.setdefault(view_of_axis[_axis_letter(holes[0])][0], []).append(holes)

    _, iso_y0, _, _ = _iso_bbox(dwg)
    plan_right = a.proj.plan_x(a.bb.max.X)
    plan_left = a.proj.plan_x(a.bb.min.X)
    side_right = a.proj.side_x(a.bb.max.Y)
    front_bottom = a.proj.front_z(a.bb.min.Z)
    tb_left = a.PAGE_W - a.TB_W - _TB_CLEAR
    tb_top = _TB_CLEAR + _TB_H

    # A section line will be placed when the part has z-axis holes with
    # counterbores, spotfaces, or blind bottoms (_add_section_view trigger).
    # When present, its extension lines overhang the plan view boundary by
    # ~arrow_length, so plan-view elbow must sit that far outside to clear them.
    # Room-check failures may still skip the section, but the offset is harmless.
    will_have_section_line = any(
        _axis_letter(h) == "z" and (h.cbore or h.spotface or h.bottom != "through")
        for h in a.holes
    )

    # v0.12.0 sub-clusters a machining-spec group into >=0 patterns: a filled
    # lattice -> one RectGrid, a rectangular perimeter -> its edge LinearArray
    # rows, plus a same-spec second bolt circle, etc.  Each hole belongs to at
    # most one pattern, so map hole -> pattern and split every spec group into
    # one callout PER pattern + one for the leftover unpatterned holes (#92).
    hole_pattern = {h: p for p in found_patterns for h in p.holes}

    # Map each hole location → its IR hole/pattern DimensionGroup so the callout's
    # bore/cbore/suffix come from the single IR spec (hole_callout_spec), not a
    # second engine-side extraction (#238 B1). The IR groups holes the same way the
    # engine does (HoleSpec / find_hole_patterns), so every hole has a group.
    _loc_to_group: dict = {}
    for g in plan_dimensions(model):
        if g.feature_kind not in ("hole", "pattern"):
            continue
        for m in getattr(g.feature, "members", ()) or (g.anchor,):
            _loc_to_group[(round(m[0], 3), round(m[1], 3), round(m[2], 3))] = g

    def _subspecs(holes):
        """Split a spec group's holes into ``(subholes, pattern)`` entries — one
        per recognised pattern (its full hole set) plus a trailing ``(rest,
        None)`` for any holes no pattern claimed."""
        by_pat: dict = {}
        remainder = []
        for h in holes:
            p = hole_pattern.get(h)
            if p is None:
                remainder.append(h)
            else:
                by_pat.setdefault(p, []).append(h)
        out = [(list(p.holes), p) for p in by_pat]
        if remainder:
            out.append((remainder, None))
        return out

    def _build_callout(holes, pattern):
        """The `HoleCallout` for a spec group, built from its IR group's planned spec
        (bore/cbore/through/suffix) with the engine's view-local hole count. The
        cbore-precedence and pattern-suffix logic lives once, in `hole_callout_spec`
        (#238 B1). *pattern* is unused here — the IR group carries the suffix; it
        stays in the spec tuple for placement + sheet furniture."""
        key = holes[0].location
        group = _loc_to_group.get((round(key[0], 3), round(key[1], 3), round(key[2], 3)))
        if group is None:  # every hole is in build_part_model; guard, don't crash
            _log.warning("no IR group for hole at %s; callout skipped", _fmt(holes[0].diameter))
            return None
        count = len(holes) if len(holes) > 1 else None
        return callout_from_spec(hole_callout_spec(group), draft, count)

    def _rim_tip(centre, elbow, holes):
        """Pull the tip from the hole centre to its circumference."""
        r = holes[0].diameter * a.SCALE / 2
        dx, dy = elbow[0] - centre[0], elbow[1] - centre[1]
        norm = math.hypot(dx, dy)
        if norm <= r:
            return centre
        return (centre[0] + dx / norm * r, centre[1] + dy / norm * r)

    def _add(view, i, tip, elbow, side, callout):
        dwg.add(
            Leader(
                tip=(tip[0], tip[1], 0),
                elbow=(elbow[0], elbow[1], 0),
                label="",
                draft=draft,
                text_side=side,
                callout=callout,
            ),
            f"hc_{view}{i}",
            view=view,
        )

    for view, view_groups in by_view.items():
        to_page = view_of_axis[{"plan": "z", "front": "y", "side": "x"}[view]][1]
        specs = []
        for holes in view_groups:
            for subholes, pattern in _subspecs(holes):
                callout = _build_callout(subholes, pattern)
                if callout is not None:
                    specs.append((subholes, callout, pattern))
        # No fixed cap (#36): every spec is attempted; the per-view placement
        # bounds below (front-view shaft rows, plan/side strip Y-solver) are the
        # real limit, and any callout that genuinely doesn't fit surfaces as
        # callout_dropped. Largest diameters first so the most significant
        # features win the available room.
        specs.sort(key=lambda s: s[0][0].diameter, reverse=True)

        if view == "front":
            # Below the view, vertical shafts. Rows are assigned right-to-
            # left so a deeper row's shaft never crosses a shallower row's
            # right-running label; left-side labels get an explicit guard.
            specs.sort(key=lambda s: max(to_page(h)[0] for h in s[0]), reverse=True)
            occupied: list[tuple] = []  # (x0, x1, row_y) of placed labels
            for i, (holes, callout, pattern) in enumerate(specs):
                w = callout.callout_width
                centre = to_page(max(holes, key=lambda h: to_page(h)[0]))
                elbow_y = front_bottom - 0.6 * a.DIM_PAD - i * min_gap
                if centre[0] + gap + w <= a.PAGE_W - a.margin:
                    side, x0, x1 = "right", centre[0] + gap, centre[0] + gap + w
                elif centre[0] - gap - w >= a.margin:
                    side, x0, x1 = "left", centre[0] - gap - w, centre[0] - gap
                else:
                    _log.info("Hole callout ø%s skipped (no room)", _fmt(holes[0].diameter))
                    _record_callout_drop(dwg, view, holes[0].diameter, "no room beside the view")
                    continue
                # the title block only constrains rows that reach its x-range
                floor = (tb_top + 4) if x1 > tb_left - 4 else a.margin + 4
                if elbow_y < floor:
                    _log.info(
                        "Hole callout ø%s skipped (front strip full)", _fmt(holes[0].diameter)
                    )
                    _record_callout_drop(dwg, view, holes[0].diameter, "front strip full")
                    continue
                if any(
                    ox0 <= centre[0] <= ox1 and row_y > elbow_y for ox0, ox1, row_y in occupied
                ):
                    _log.info(
                        "Hole callout ø%s skipped (shaft would cross another callout)",
                        _fmt(holes[0].diameter),
                    )
                    _record_callout_drop(
                        dwg, view, holes[0].diameter, "shaft would cross another callout"
                    )
                    continue
                elbow = (centre[0], elbow_y)
                occupied.append((x0, x1, elbow_y))
                _add(view, i, _rim_tip(centre, elbow, holes), elbow, side, callout)
                _add_furniture(dwg, a, view, i, pattern, to_page)
            continue

        # plan / side: two-pass leader placement.
        # Pass 1 — boundary assignment: each spec goes to the nearest strip
        #   boundary (right or left) whose label fits within the page.
        # Pass 2 — Y placement via Cassowary: leaders stay within the view's
        #   Y extent, are at least min_gap apart, and stay near their natural
        #   (hole-centre) Y position.
        edge_right = plan_right if view == "plan" else side_right
        edge_left = plan_left if view == "plan" else None

        right_strip = a.pv_zones.right if view == "plan" else a.sv_zones.right
        # Elbow offset past the view boundary: only needed in the plan view when
        # a section line will be placed (its extension lines overhang by
        # ~arrow_length).  Side view and section-free plan views use 0 so the
        # shaft terminates at the boundary instead of crossing it.
        elbow_dx = draft.arrow_length if view == "plan" and will_have_section_line else 0.0

        # Y bounds: elbows must stay within the view's projected Y extent.
        if view == "plan":
            y_min, y_max = a.PV_Y - a.pv_hh, a.PV_Y + a.pv_hh
        else:
            y_min, y_max = a.SV_Y - a.fv_hh, a.SV_Y + a.fv_hh

        # --- Pass 1: boundary assignment ---
        right_queue = []  # (holes, callout, pattern, natural_y, rep)
        left_queue = []

        for holes, callout, pattern in specs:
            w = callout.callout_width
            rep_r = max(holes, key=lambda h: to_page(h)[0])
            centre_r = to_page(rep_r)
            d_right = edge_right - centre_r[0]

            if edge_left is not None:
                rep_l = min(holes, key=lambda h: to_page(h)[0])
                centre_l = to_page(rep_l)
                d_left = centre_l[0] - edge_left
            else:
                rep_l = centre_l = None
                d_left = float("inf")

            # Side callouts below the iso view (always the case in practice) may
            # reach the full page width; plan callouts are constrained by the iso.
            right_limit = (
                right_strip.outer_limit
                if view == "plan" or centre_r[1] >= iso_y0 - draft.font_size
                else a.PAGE_W - a.margin
            )
            can_right = (edge_right + elbow_dx) + gap + w <= right_limit
            can_left = edge_left is not None and (edge_left - elbow_dx) - gap - w >= a.margin

            if not can_right and not can_left:
                _log.info("Hole callout ø%s skipped (no room)", _fmt(holes[0].diameter))
                _record_callout_drop(dwg, view, holes[0].diameter, "no room beside the view")
                continue

            if can_right and (not can_left or d_right <= d_left):
                right_queue.append((holes, callout, pattern, centre_r[1], rep_r))
            else:
                left_queue.append((holes, callout, pattern, centre_l[1], rep_l))

        # Sort each queue by natural Y so leaders don't cross.
        right_queue.sort(key=lambda s: s[3])
        left_queue.sort(key=lambda s: s[3])

        # --- Pass 2: Y placement (through the LayoutSolver, #80) ---
        right_ys = _solve_strip_via_layout(
            [s[3] for s in right_queue], min_gap, y_min, y_max, "hc_r"
        )
        left_ys = _solve_strip_via_layout(
            [s[3] for s in left_queue], min_gap, y_min, y_max, "hc_l"
        )

        if right_ys is None and right_queue:
            right_ys = _greedy_strip_ys(
                [s[3] for s in right_queue], min_gap, y_min, y_max, prefix=True
            )
            n_drop = len(right_queue) - len(right_ys)
            if n_drop:
                _log.warning(
                    "plan/side right strip: %d of %d bore callouts skipped (strip full)",
                    n_drop,
                    len(right_queue),
                )
                for holes, *_ in right_queue[len(right_ys) :]:
                    _record_callout_drop(dwg, view, holes[0].diameter, "right strip full")
            right_queue = right_queue[: len(right_ys)]
        if left_ys is None and left_queue:
            left_ys = _greedy_strip_ys(
                [s[3] for s in left_queue], min_gap, y_min, y_max, prefix=True
            )
            n_drop = len(left_queue) - len(left_ys)
            if n_drop:
                _log.warning(
                    "plan/side left strip: %d of %d bore callouts skipped (strip full)",
                    n_drop,
                    len(left_queue),
                )
                for holes, *_ in left_queue[len(left_ys) :]:
                    _record_callout_drop(dwg, view, holes[0].diameter, "left strip full")
            left_queue = left_queue[: len(left_ys)]

        for i, ((holes, callout, pattern, _, rep), elbow_y) in enumerate(
            zip(right_queue, right_ys, strict=True)
        ):
            centre = to_page(rep)
            elbow = (edge_right + elbow_dx, elbow_y)
            tip = _rim_tip(centre, elbow, holes)
            # Safety clamp: arrowhead must sit inside the view boundary.
            tip = (min(tip[0], edge_right - draft.arrow_length), tip[1])
            _add(view, i, tip, elbow, "right", callout)
            _add_furniture(dwg, a, view, i, pattern, to_page)

        assert edge_left is not None or not left_queue  # populated only when edge_left is set
        for i, ((holes, callout, pattern, _, rep), elbow_y) in enumerate(
            zip(left_queue, left_ys, strict=True), start=len(right_queue)
        ):
            centre = to_page(rep)
            elbow = (edge_left - elbow_dx, elbow_y)  # type: ignore[operator]
            tip = _rim_tip(centre, elbow, holes)
            tip = (max(tip[0], edge_left + draft.arrow_length), tip[1])
            _add(view, i, tip, elbow, "left", callout)
            _add_furniture(dwg, a, view, i, pattern, to_page)
