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
    HoleCallout,
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
from draftwright.layout import LayoutSolver, Placeable
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


def _add_location_dims(dwg, a: Analysis, patterns, holes_in=None):
    """Baseline X/Y location dimensions in the plan view (#93).

    The datum corner is a *default* — the part's minimum-X/minimum-Y corner
    (lower-left in the plan view), per inspection practice; a human/LLM pass
    can re-anchor it. One reference per pattern (bolt-circle centre, array
    first hole) plus each unpatterned hole. There is no fixed cap: dims are
    placed nearest-datum-first (baseline practice) until the above-view tier
    strips fill — a tier that would leave the page is skipped, never
    force-placed, and the unplaced ref surfaces as ``location_ref_dropped``
    (#36). X dims tier above the plan view (below sit dim_width and the front
    view), Y dims tier above the side view. Cross-axis holes are not located
    yet (logged).
    """
    draft = dwg.draft
    all_holes = a.holes if holes_in is None else holes_in
    z_holes = [h for h in all_holes if _axis_letter(h) == "z"]
    if len(z_holes) < len(all_holes):
        _log.info("Cross-axis holes present; their locations are not auto-dimensioned")
    patterned = {h for p in patterns for h in p.holes}
    refs = []  # (world_x, world_y, sort_diameter)
    for p in patterns:
        if _axis_letter(p.holes[0]) != "z":
            continue
        if isinstance(p, BoltCircle):
            refs.append((p.center[0], p.center[1], p.holes[0].diameter))
        else:
            # locate the array's member nearest the datum corner — the pitch
            # dim chains the rest outward (shortest baseline, per practice)
            near = min(
                p.holes,
                key=lambda h: (
                    (h.location[0] - a.bb.min.X) ** 2 + (h.location[1] - a.bb.min.Y) ** 2
                ),
            )
            refs.append((near.location[0], near.location[1], near.diameter))
    refs += [(h.location[0], h.location[1], h.diameter) for h in z_holes if h not in patterned]
    # dedupe coincident references (e.g. a hole at a bolt-circle's centre)
    unique: list = []
    for r in refs:
        if not any(abs(r[0] - u[0]) < 0.5 and abs(r[1] - u[1]) < 0.5 for u in unique):
            unique.append(r)
    refs = unique
    if not refs:
        return

    PX = a.proj.plan_x
    PY = a.proj.plan_y

    plan_top = PY(a.bb.max.Y)
    datum_x, datum_y = a.bb.min.X, a.bb.min.Y
    # Vertical pitch between stacked location dims: the value label (one glyph
    # height) plus clearance above and below, so consecutive tiers pack as
    # tightly as they can without a label touching the next dim line. (Was a
    # looser font_size*3.)
    tier = draft.font_size + 2 * draft.pad_around_text

    # X locations: dims above the plan view, routed through pv_zones.above.
    # Pre-advance the strip past any pitch dims already placed above plan_top.
    x_refs: list = []
    for r in refs:
        if not any(abs(r[0] - u[0]) < 0.5 for u in x_refs):
            x_refs.append(r)
    # Legibility gate (#43): drop X refs whose baseline witness lines would be
    # page-coincident with a kept one — "fits" is not "legible" (cf. #41). Gate
    # only the refs that will actually be drawn: a hole on the datum edge is
    # skipped below, so it must not anchor a cluster and drop a real neighbour.
    _x_drawable = {r[0] for r in x_refs if abs(r[0] - datum_x) * a.SCALE >= 1.0}
    _kept_x, _n_x_close = _legible_locations(_x_drawable, a.SCALE)
    if _n_x_close:
        dwg._record_build_issue(
            "warning",
            "location_ref_dropped",
            f"{_n_x_close} X location dim(s) too closely spaced to dimension legibly "
            "(use a detail view)",
        )
    _kept_x_set = set(_kept_x)
    x_refs = [r for r in x_refs if r[0] not in _x_drawable or r[0] in _kept_x_set]
    for n, ann in dwg._named.items():
        if n.startswith("dim_pitch_plan") and getattr(ann, "dim_level_y", 0) > plan_top:
            a.pv_zones.above.allocate(10.0)  # consume space used by pitch dim
    for i, (rx, ry, _) in enumerate(sorted(x_refs, key=lambda r: abs(r[0] - datum_x))):
        if abs(rx - datum_x) * a.SCALE < 1.0:
            continue  # on the datum edge — nothing to dimension
        _py = a.pv_zones.above.allocate(tier)
        if _py is None:
            _log.info("X location dim for x=%s skipped (no room above plan view)", _fmt(rx))
            dwg._record_build_issue(
                "warning",
                "location_ref_dropped",
                f"X location dim for x={_fmt(rx)} not placed (no room above the plan view)",
            )
            continue
        dwg.add(
            _dim(
                (PX(datum_x), PY(ry), 0),
                (PX(rx), PY(ry), 0),
                "above",
                _py - PY(ry),
                draft,
                label=_fmt(rx - datum_x),
            ),
            f"dim_locx{i}",
            view="plan",
        )

    # Y locations: the side view maps world Y horizontally, and the strip
    # above it is open (the plan view's left margin fits barely one tier) —
    # dims go above the side view, witness lines rising from its top edge at
    # each hole's axis position
    SX = a.proj.side_x
    SZ = a.proj.side_z

    side_top = SZ(a.bb.max.Z)
    iso_x0, iso_y0, _, _ = _iso_bbox(dwg)
    y_refs: list = []
    for rx, ry, dia in refs:
        if not any(abs(ry - u[1]) < 0.5 for u in y_refs):
            y_refs.append((rx, ry, dia))
    # Legibility gate (#43): drop Y refs page-coincident with a kept one. Gate
    # only drawable refs (the placement loop skips datum-edge ones), so the gate
    # never anchors a cluster on a hole that isn't dimensioned.
    _y_drawable = {r[1] for r in y_refs if abs(r[1] - datum_y) * a.SCALE >= 1.0}
    _kept_y, _n_y_close = _legible_locations(_y_drawable, a.SCALE)
    if _n_y_close:
        dwg._record_build_issue(
            "warning",
            "location_ref_dropped",
            f"{_n_y_close} Y location dim(s) too closely spaced to dimension legibly "
            "(use a detail view)",
        )
    _kept_y_set = set(_kept_y)
    y_refs = [r for r in y_refs if r[1] not in _y_drawable or r[1] in _kept_y_set]
    # Y locations: dims above the side view, routed through sv_zones.above.
    # Pre-advance past any pitch dims already placed above side_top.
    for n, ann in dwg._named.items():
        if n.startswith("dim_pitch_side") and getattr(ann, "dim_level_y", 0) > side_top:
            a.sv_zones.above.allocate(10.0)  # consume space used by pitch dim
    # Tighten outer_limit if any witness line approaches the iso view boundary.
    # Guard: only cap if iso_y0-4 is above the strip's current cursor — an iso
    # view that overflows left (too large to fit) can have iso_y0 below
    # sv_top_edge, which would make all allocations return None if applied.
    if y_refs and any(SX(ry) + 10 > iso_x0 - 4 for _, ry, _ in y_refs):
        cap = iso_y0 - 4
        above = a.sv_zones.above
        if cap > above._cursor:
            above.outer_limit = min(above.outer_limit, cap)
        else:
            _log.warning(
                "sv_zones.above cursor %.1f >= iso_y0 cap %.1f: Y-location dims may overlap iso view",
                above._cursor,
                cap,
            )
    for i, (_rx, ry, _) in enumerate(sorted(y_refs, key=lambda r: abs(r[1] - datum_y))):
        if abs(ry - datum_y) * a.SCALE < 1.0:
            continue
        _py = a.sv_zones.above.allocate(tier)
        if _py is None:
            _log.info("Y location dim for y=%s skipped (no room above the side view)", _fmt(ry))
            dwg._record_build_issue(
                "warning",
                "location_ref_dropped",
                f"Y location dim for y={_fmt(ry)} not placed (no room above the side view)",
            )
            continue
        dwg.add(
            _dim(
                (SX(datum_y), SZ(a.bb.max.Z), 0),
                (SX(ry), SZ(a.bb.max.Z), 0),
                "above",
                _py - side_top,
                draft,
                label=_fmt(ry - datum_y),
            ),
            f"dim_locy{i}",
            view="side",
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


def _annotate_slots(dwg, a: Analysis):
    """Dimension milled slots / reduced across-flats sections (#135).

    Each recognised slot (``features.find_slots``) is dimensioned in the
    orthographic view whose two in-plane axes are the slot's ``width_axis`` and
    ``long_axis``: the *width* (the defining size) across ``width_axis``, its
    *length* along ``long_axis``, and one *position* dim from the part datum.
    Width measured along the view's vertical axis is placed in the right strip
    (falling back to left), along the horizontal axis in the above strip
    (falling back to below); length/position take the orthogonal strips.

    Runs after the envelope, turned-diameter and hole passes, so it claims strip
    space last and never evicts a primary dimension.  A dim with no clear room
    is dropped and recorded at info severity under ``slot_dim_dropped`` — the
    sheet stays correct (place-what-fits, as #133/#144); the gap is a
    completeness shortfall, not a drawing defect.
    """
    if not a.slots:
        return
    draft = dwg.draft
    # Two occupancy sets with different collision tests (#146 re-review):
    #  - ``external`` (callouts, envelope dims, hatch) is tested against the
    #    candidate's FULL geometry, so a slot dim's witness/arrow line may not
    #    cross another feature's label even though the label boxes clear;
    #  - ``placed`` (this pass's own slot dims) is tested label-box to label-box,
    #    so sibling dims may still stack in a shared strip corridor.
    external = _occupied_boxes(dwg)
    placed: list = []
    tier = draft.font_size + 2 * draft.pad_around_text

    # (view name, zones, horizontal axis + projector, vertical axis + projector)
    views = {
        frozenset("xy"): ("plan", a.pv_zones, "x", a.proj.plan_x, "y", a.proj.plan_y),
        frozenset("xz"): ("front", a.fv_zones, "x", a.proj.front_x, "z", a.proj.front_z),
        frozenset("yz"): ("side", a.sv_zones, "y", a.proj.side_x, "z", a.proj.side_z),
    }

    def _bb(axis, hi):
        return getattr(a.bb.max if hi else a.bb.min, axis.upper())

    def _drop(kind, idx, view):
        dwg._record_build_issue(
            "info",
            "slot_dim_dropped",
            f"slot{idx} {kind} dim not placed (no room beside the {view} view)",
        )

    for i, s in enumerate(a.slots):
        # width_axis and long_axis are always two distinct orthographic axes
        # (find_slots derives long_axis from the axes other than width_axis), so
        # the pair always selects exactly one view.
        view = views[frozenset((s.width_axis, s.long_axis))]
        name, zones, h_axis, h_proj, _v_axis, v_proj = view

        def _place(
            meas_axis,
            p_lo,
            p_hi,
            perp_lo,
            perp_hi,
            label,
            kind,
            anchor="center",
            vw=view,
            zn=zones,
            ha=h_axis,
            hp=h_proj,
            vp=v_proj,
            idx=i,
        ):
            # Snap the dimension's geometric span to its *displayed* value so the
            # drawn length matches the (1-dp) label exactly — otherwise a true
            # 4.75 mm feature, labelled "4.8", trips the label-vs-measured lint.
            # ``center`` snaps symmetrically (a size dim); ``lo`` keeps p_lo fixed
            # (a position dim anchored on the datum).
            disp = float(_fmt(label))
            sgn = 1.0 if p_hi >= p_lo else -1.0
            if anchor == "center":
                mid = (p_lo + p_hi) / 2
                p_lo, p_hi = mid - sgn * disp / 2, mid + sgn * disp / 2
            else:
                p_hi = p_lo + sgn * disp
            # Horizontal measurement (along the view's h-axis) stacks above the
            # view; vertical (along the v-axis) stacks to the right. Fall back to
            # the opposite strip before giving up.
            if meas_axis == ha:
                meas_proj, perp_proj = hp, vp
                cands = (("above", zn.above, True), ("below", zn.below, False))
            else:
                meas_proj, perp_proj = vp, hp
                cands = (("right", zn.right, True), ("left", zn.left, False))
            for side, strip, hi in cands:
                if strip is None:
                    continue
                # Peek, don't allocate yet: a candidate rejected for collision
                # below must not consume the tier (which would starve later slots
                # and inflate the drop count).
                coord = strip.peek(tier)
                if coord is None:
                    continue
                # Witness the dimension off the slot's OWN edge (its extent on the
                # perpendicular axis), not the part envelope — otherwise a slot's
                # size dim is drawn across at the far edge of the part and reads
                # as an envelope dimension (#146 review).
                witness = perp_proj(perp_hi if hi else perp_lo)
                if side in ("above", "below"):
                    e_lo = (meas_proj(p_lo), witness, 0)
                    e_hi = (meas_proj(p_hi), witness, 0)
                else:
                    e_lo = (witness, meas_proj(p_lo), 0)
                    e_hi = (witness, meas_proj(p_hi), 0)
                dim = _dim(e_lo, e_hi, side, abs(coord - witness), draft, label=_fmt(label))
                gbb = dim.bounding_box()
                full = (gbb.min.X, gbb.min.Y, gbb.max.X, gbb.max.Y)
                if _box_hits(full, external) or _box_hits(_anno_box(dim), placed):
                    continue
                strip.allocate(tier)
                dwg.add(dim, f"slot{idx}_{kind}", view=vw[0])
                placed.append(_anno_box(dim))
                return True
            return False

        # Width — the defining size, measured across width_axis; its witness
        # rides the slot's long-axis extent.
        half = s.width / 2
        if not _place(
            s.width_axis, s.w_center - half, s.w_center + half, s.lo, s.hi, s.width, "width"
        ):
            _drop("width", i, name)

        # Length along the slot's long axis (find_slots excludes full-span open
        # features, so length is always a real, sub-envelope measurement); its
        # witness rides the slot's width extent.
        if not _place(
            s.long_axis, s.lo, s.hi, s.w_center - half, s.w_center + half, s.length, "length"
        ):
            _drop("length", i, name)

        # Position: from the part datum (min on the long axis, the same datum the
        # hole-location dims use) to the slot's near edge — its lo, the edge
        # closer to that datum. Skipped when the slot abuts the datum.
        datum = _bb(s.long_axis, False)
        if (s.lo - datum) * a.SCALE >= 1.0:
            if not _place(
                s.long_axis,
                datum,
                s.lo,
                s.w_center - half,
                s.w_center + half,
                s.lo - datum,
                "pos",
                anchor="lo",
            ):
                _drop("position", i, name)


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
    prior = sum(1 for nm in dwg._named if nm.startswith(f"dim_pitch_{view}"))
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


def _annotate_holes(dwg, a: Analysis, view_of_axis, found_patterns, holes_in=None):
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
        h = holes[0]
        step = h.cbore or h.spotface
        if h.cbore and h.spotface:
            _log.info(
                "Hole ø%s has both cbore and spotface; spotface not in the callout",
                _fmt(h.diameter),
            )
            step = h.cbore
        through = h.bottom == "through"
        if isinstance(pattern, BoltCircle):
            suffix = f"EQ SP ON ø{_fmt(pattern.diameter)} BC"
        elif isinstance(pattern, RectGrid):
            suffix = f"({pattern.rows}×{pattern.cols})"
        else:
            suffix = None
        return HoleCallout(
            _fmt(h.diameter),
            count=len(holes) if len(holes) > 1 else None,
            through=through,
            depth=None if through else _fmt(h.depth),
            cbore_dia=_fmt(step.diameter) if step else None,
            cbore_depth=_fmt(step.depth) if step else None,
            suffix=suffix,
            draft=draft,
        )

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
                specs.append((subholes, _build_callout(subholes, pattern), pattern))
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
