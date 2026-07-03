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
    _CONCENTRIC_TOL_MM,
    _MIN_LOC_SEP_MM,
    _TB_CLEAR,
    _TB_H,
    Analysis,
    HoleRef,
    _axis_letter,
    _dim,
    _fmt,
    _iso_bbox,
    _log,
)
from draftwright.annotations._common import (
    CROSSABLE_TYPES,
    Escalation,
    _box_hits,
    _geom_box,
    _segment_hits_box,
    carve_free_segments,
    place_strip_candidates,
    strip_obstacles,
)
from draftwright.annotations.from_model import callout_from_spec, hole_callout_spec
from draftwright.layout import StripCandidate, plan_strip
from draftwright.model.ir import HoleFeature, PatternFeature


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


def _record_callout_drop(dwg, view, diam, reason, feat=None):
    """Record a hole callout the layout could not place (#36).

    A warning (the drawing is incomplete, not invalid), whose diameter is
    excluded from ``feature_not_dimensioned`` like the old per-view cap drop —
    so a callout that genuinely doesn't fit is surfaced once, with a reason,
    and not double-reported.

    *feat* is the dropped group's ``PatternFeature`` when it is a fully-surviving
    recognised pattern (the ``pat`` value threaded through ``_annotate_holes``),
    else ``None`` — carried on the Escalation so the resolver can group by pattern
    identity (#351 PR-3).
    """
    dwg._drop_callout_diam(diam)
    dwg._record_build_issue(
        "warning",
        "callout_dropped",
        f"hole callout ø{_fmt(diam)} dropped from the {view} view ({reason})",
    )
    # First-class escalation object alongside the lint code (ADR 0009 Amdt 1, #351 PR-2).
    # The resolver (`_maybe_tabulate_holes`) triggers on these; the lint code stays for
    # coverage. 1:1 with the code emit, so the object trigger is byte-identical.
    dwg._escalations.append(Escalation(kind="callout", view=view, feature=feat, reason=reason))


def _locate_off_axis_holes(dwg, a: Analysis, holes_in=None, *, which):
    """Location dimensions for side-drilled holes (#133).

    An X-axis hole is a circle in the SIDE view (locate its Y below the view and
    its Z to the right — the side view has no left strip); a Y-axis hole is a
    circle in the FRONT view (locate its X below and its Z to the right). Each
    view's strip is carved around the annotations already placed on it and the dims
    are spaced within the free segments by one ``plan_strip`` solve (ADR 0009 / #321
    P1b — the collect-then-solve seam replacing the old ``allocate`` + ``_box_hits``
    tier-retry). A dim that finds no room is dropped and recorded as
    ``off_axis_location_dropped`` — never force-stacked. Holes already covered by a
    pattern callout are skipped.

    Run in two phases (``which`` is ``"across"`` or ``"along"``) so each dim stacks in
    the ISO order — overall dim OUTERMOST, feature/location dims nearer the view:

    - ``"across"`` — an X-axis hole's in-plane (Y, side-below) location, placed BEFORE
      the envelope so the overall depth dim lands outside it (the side-view
      counterpart of the plan view, where location dims already precede the
      envelope). Fixes the inverted stack where the overall dim sat innermost and
      forced the shorter location dim's arrows outside. The orchestrator reserves the
      envelope's tier first, so these best-effort dims can't starve it.
    - ``"along"`` — a Y-axis hole's X (front-below) location and every hole's height
      (Z, right-strip), placed AFTER the envelope and the turned-diameter passes so
      they never evict those overall dims from the contended front-below / right
      strips (#133).
    """
    draft = dwg.draft
    all_holes = a.holes if holes_in is None else holes_in
    patterned = {h for p in a.patterns for h in p.holes}

    def _coaxial(h):
        # The turning-axis bore of a rotational part is located by its centreline, not
        # a position dim (#309) — mirrors render_locations' concentric filter (the
        # Z-turned/plan case) and coverage.py's coaxial exemption, for the X/Y-turned
        # case whose dims come through THIS path. Suppresses the redundant offset+height
        # dims; coverage already credits the bore via its centre mark, so lint stays
        # clean. Non-rotational parts and genuine off-centre side-drilled holes keep
        # their dims (the a.od_axis + perpendicular-centre gates).
        if not a.is_rotational or _axis_letter(h) != a.od_axis:
            return False
        centre = (a.cx, a.cy, a.cz)
        return all(
            abs(h.location[i] - centre[i]) <= _CONCENTRIC_TOL_MM
            for i, ax in enumerate("xyz")
            if ax != a.od_axis
        )

    off = [
        h
        for h in all_holes
        if _axis_letter(h) in ("x", "y") and h not in patterned and not _coaxial(h)
    ]
    if not off:
        return
    SX, SZ = a.proj.side_x, a.proj.side_z
    FX, FZ = a.proj.front_x, a.proj.front_z
    dx, dy, dz = a.bb.min.X, a.bb.min.Y, a.bb.min.Z
    tier = draft.font_size + 2 * draft.pad_around_text

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

    def _emit(strip, view, axis, cands, force=False):
        # The collect-then-solve strip placer now lives in _common as the shared
        # place_strip_candidates (P3, retiring the Strip cursor #150); this thin wrapper
        # binds the pass's dwg + tier so the across/along/Z callers below are unchanged.
        return place_strip_candidates(dwg, strip, view, axis, cands, tier, force=force)

    # "across" phase — an X-axis hole's Y position below the SIDE view, placed BEFORE
    # the envelope so the overall depth dim stacks outside it (ISO order). Confined to
    # the side view: a Y-axis hole's X position contends the FRONT-below strip with the
    # turned-diameter ø-row, so it stays in the "along" phase (after the diameter pass),
    # preserving the #133 priority. The orchestrator reserves one sv_zones.below tier
    # for the mandatory envelope depth before calling this, so these best-effort
    # location dims can never starve it.
    if which == "across":
        yw = SZ(dz) - 2
        seen_y: set = set()
        cands = []
        for h in (h for h in off if _axis_letter(h) == "x"):
            yo = round(abs(h.location[1] - dy), 2)
            if yo * a.SCALE >= 1.0 and yo not in seen_y:
                seen_y.add(yo)
                p_lo, p_hi = (SX(dy), yw, 0), (SX(h.location[1]), yw, 0)
                cands.append(
                    (
                        f"dim_loc_side_y{round(yo * 100)}",
                        lambda pos, pl=p_lo, ph=p_hi, lb=yo: _dim(
                            pl, ph, "below", yw - pos, draft, label=_fmt(lb)
                        ),
                    )
                )
        leftover = _emit(a.sv_zones.below, "side", "y", cands)
        leftover = _emit(a.sv_zones.below, "side", "y", leftover, force=True)  # keep, don't drop
        for _ in leftover:
            _drop("y", "side")
        return

    # "along" phase (after the envelope + turned-diameter passes): a Y-axis hole's X
    # position below the FRONT view, then every hole's height (Z) to the right.
    xw = FZ(dz) - 2
    seen_x: set = set()
    x_cands = []
    for h in (h for h in off if _axis_letter(h) == "y"):
        xo = round(abs(h.location[0] - dx), 2)
        if xo * a.SCALE >= 1.0 and xo not in seen_x:
            seen_x.add(xo)
            p_lo, p_hi = (FX(dx), xw, 0), (FX(h.location[0]), xw, 0)
            x_cands.append(
                (
                    f"dim_loc_front_x{round(xo * 100)}",
                    lambda pos, pl=p_lo, ph=p_hi, lb=xo: _dim(
                        pl, ph, "below", xw - pos, draft, label=_fmt(lb)
                    ),
                )
            )
    x_leftover = _emit(a.fv_zones.below, "front", "y", x_cands)
    x_leftover = _emit(a.fv_zones.below, "front", "y", x_leftover, force=True)  # keep, don't drop
    for _ in x_leftover:
        _drop("x", "front")

    # Height offset (Z): a hole's height is visible to the RIGHT of both the side and
    # the front view. Neither right strip is universally free — the side view's is
    # contended by hole callouts (hc_side) + the section hatch, the front view's by the
    # dim_height/dim_step ladder — so try the natural strip first, then RELOCATE to the
    # other view (a disjoint block that cannot cross the natural view's leader) if a
    # bore-callout leader sits in the natural corridor. If neither view takes it cleanly,
    # KEEP it on the natural view (force) accepting the same-feature leader crossing —
    # never drop a real dimension (policy B, #133 rework); only a physically full strip
    # still drops.
    zr, zrf = SX(a.bb.max.Y), FX(a.bb.max.X)
    seen_z = set()
    for h in off:
        zo = round(abs(h.location[2] - dz), 2)
        if zo * a.SCALE < 1.0 or zo in seen_z:
            continue
        seen_z.add(zo)
        hz = h.location[2]

        def _zc(view, p_lo, p_hi, edge, _zo=zo):
            return (
                f"dim_loc_{view}_z{round(_zo * 100)}",
                lambda pos, pl=p_lo, ph=p_hi, e=edge: _dim(
                    pl, ph, "right", pos - e, draft, label=_fmt(_zo)
                ),
            )

        side_cand = (a.sv_zones.right, "side", (zr, SZ(dz), 0), (zr, SZ(hz), 0), zr)
        front_cand = (a.fv_zones.right, "front", (zrf, FZ(dz), 0), (zrf, FZ(hz), 0), zrf)
        order = (side_cand, front_cand) if _axis_letter(h) == "x" else (front_cand, side_cand)
        for strip, view, p_lo, p_hi, edge in order:
            if not _emit(strip, view, "x", [_zc(view, p_lo, p_hi, edge)]):
                break
        else:
            strip, view, p_lo, p_hi, edge = order[0]
            if _emit(strip, view, "x", [_zc(view, p_lo, p_hi, edge)], force=True):
                _drop("Z", view)


def _add_furniture(dwg, a: Analysis, view, j, feat: PatternFeature | None, to_page):
    """Pattern sheet furniture, added once its callout is placed (#92). Driven by the
    IR `PatternFeature` *feat* (members / bcd / pitch / grid), not a recogniser
    `Pattern` — ADR 0008 Amendment 6."""
    if feat is None:
        if view == "plan":
            # A plain (unpatterned) plan-view callout — a candidate the scattered-hole
            # table may replace (#351 PR-4c). Scoped to plan only, matching the table's
            # own scope: front/side callouts are never table-replaceable.
            dwg._cover_scattered_hole_doc(f"hc_{view}{j}")
        return
    members = feat.members
    # Remember the bore-callout name AND the holes it documents (by position), so a
    # later hole-table escalation leaves the grouped pattern callout standing and
    # tabulates only the holes no *placed* pattern callout covers (#92).
    dwg._cover_pattern(f"hc_{view}{j}", [HoleRef.of(m) for m in members])
    if feat.pattern == "bolt_circle":
        assert feat.bcd is not None  # a bolt circle always carries its BCD
        cx = sum(to_page(m)[0] for m in members) / len(members)
        cy = sum(to_page(m)[1] for m in members) / len(members)
        dwg.add(CenterlineCircle((cx, cy), feat.bcd * a.SCALE), f"bc_{view}{j}", view=view)
    elif feat.pattern == "linear":
        assert feat.pitch is not None  # a linear array always carries its pitch
        _place_pitch_dim(
            dwg,
            a,
            view,
            members[0],
            members[-1],
            len(members),
            feat.pitch,
            to_page,
            f"dim_pitch_{view}{j}",
        )
    elif feat.pattern == "grid":
        assert feat.grid is not None  # a grid always carries its (row, col) pitch
        _add_grid_pitch_dims(dwg, a, view, j, members, feat.grid, to_page)


def _add_grid_pitch_dims(dwg, a: Analysis, view, j, members, nominals, to_page):
    """Both pitch dimensions of a rectangular grid — one along each lattice axis,
    each labelled ``(n-1)× pitch`` (#92).  The two axes are recovered as the two
    shortest near-orthogonal inter-hole page vectors (the recogniser's own
    basis); this is used only to pick the dimension endpoints and the per-axis
    count, not to re-recognise the grid (recognition stays upstream). *members* are
    the grid's member locations; *nominals* is ``(row_pitch, col_pitch)``."""
    pts = [to_page(m) for m in members]
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
            members[lo],
            members[hi],
            n,
            pitch,
            to_page,
            f"dim_pitch_{view}{j}_{sub}",
        )

    _axis_dim(u1, l1, 0)
    _axis_dim(u2, l2, 1)


def _place_pitch_dim(dwg, a: Analysis, view, loc1, loc2, n, pitch, to_page, name):
    """Pitch dimension between two hole-centre *locations* ``loc1``→``loc2``, labelled
    ``(n-1)× pitch``, placed just outside the view on the side of the row's
    outward perpendicular (#92)."""
    p1 = to_page(loc1)
    p2 = to_page(loc2)
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


def _annotate_holes(dwg, a: Analysis, view_of_axis, groups, feature_keys):
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
    def _needs_section(feat: HoleFeature | PatternFeature) -> bool:
        bore = feat.member if isinstance(feat, PatternFeature) else feat
        return bore.cbore is not None or bore.spotface is not None or not bore.through

    will_have_section_line = any(
        isinstance(g.feature, HoleFeature | PatternFeature)
        and g.feature.frame.axis == "z"
        and _needs_section(g.feature)
        for g in groups
    )

    # The IR is the single grouping + geometry authority (#238 B2/B3, Amendment 6):
    # build_part_model already split the holes into one DimensionGroup per pattern +
    # one per machining-spec group of un-patterned holes. Iterate those groups and
    # assemble each view's callout specs from IR data only — *feature_keys* (the
    # surviving feature-hole positions, supplied by the orchestrator) gates which
    # members are dimensioned; no recogniser Hole/Pattern object is used.
    by_view: dict = {}
    for g in groups:
        feat = g.feature
        if not isinstance(feat, HoleFeature | PatternFeature):
            continue
        members = feat.members or (g.anchor,)
        # surviving member *locations* (IR geometry — no recogniser Hole, Amendment 6)
        locs = [m for m in members if HoleRef.of(m) in feature_keys]
        if not locs:  # all members filtered out (e.g. concentric bore, rotational)
            continue
        # A pattern earns its sheet furniture (centre-line / pitch dims) only if ALL
        # its members survived the feature-holes filter — the engine's feature_patterns
        # gate. Otherwise the surviving members are placed as plain holes.
        pat = feat if isinstance(feat, PatternFeature) and len(locs) == len(members) else None
        spec = hole_callout_spec(g)
        if spec is None:  # not a hole-bearing callout
            continue
        # A pattern only partially surviving the feature-holes filter (a member is a
        # concentric bore on a rotational part) is rendered as plain holes — drop its
        # pattern suffix too, so the callout doesn't claim "EQ SP ON … BC" / "(r×c)"
        # for a subset with no centre-line/pitch furniture (#262; matches the engine).
        if isinstance(feat, PatternFeature) and pat is None:
            spec = {**spec, "suffix": None}
        dia = spec["diameter"]  # bore diameter (mm), for the leader rim tip
        count = len(locs) if len(locs) > 1 else None
        callout = callout_from_spec(spec, draft, count)
        if callout is None:
            continue
        view = view_of_axis[feat.frame.axis][0]
        by_view.setdefault(view, []).append((locs, dia, callout, pat))

    def _rim_tip(centre, elbow, dia):
        """Pull the tip from the hole centre to its circumference (bore *dia* mm)."""
        r = dia * a.SCALE / 2
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
        specs = list(view_groups)  # (locs, dia, callout, feat), from the IR groups
        # No fixed cap (#36): every spec is attempted; the per-view placement
        # bounds below (front-view shaft rows, plan/side strip Y-solver) are the
        # real limit, and any callout that genuinely doesn't fit surfaces as
        # callout_dropped. Largest diameters first so the most significant
        # features win the available room.
        specs.sort(key=lambda s: s[1], reverse=True)

        if view == "front":
            # Below the view, vertical shafts. Rows are assigned right-to-
            # left so a deeper row's shaft never crosses a shallower row's
            # right-running label; left-side labels get an explicit guard.
            specs.sort(key=lambda s: max(to_page(loc)[0] for loc in s[0]), reverse=True)
            occupied: list[tuple] = []  # (x0, x1, row_y) of placed labels
            for i, (locs, dia, callout, feat) in enumerate(specs):
                w = callout.callout_width
                centre = to_page(max(locs, key=lambda loc: to_page(loc)[0]))
                elbow_y = front_bottom - 0.6 * a.DIM_PAD - i * min_gap
                if centre[0] + gap + w <= a.PAGE_W - a.margin:
                    side, x0, x1 = "right", centre[0] + gap, centre[0] + gap + w
                elif centre[0] - gap - w >= a.margin:
                    side, x0, x1 = "left", centre[0] - gap - w, centre[0] - gap
                else:
                    _log.info("Hole callout ø%s skipped (no room)", _fmt(dia))
                    _record_callout_drop(dwg, view, dia, "no room beside the view", feat)
                    continue
                # the title block only constrains rows that reach its x-range
                floor = (tb_top + 4) if x1 > tb_left - 4 else a.margin + 4
                if elbow_y < floor:
                    _log.info("Hole callout ø%s skipped (front strip full)", _fmt(dia))
                    _record_callout_drop(dwg, view, dia, "front strip full", feat)
                    continue
                if any(
                    ox0 <= centre[0] <= ox1 and row_y > elbow_y for ox0, ox1, row_y in occupied
                ):
                    _log.info(
                        "Hole callout ø%s skipped (shaft would cross another callout)", _fmt(dia)
                    )
                    _record_callout_drop(dwg, view, dia, "shaft would cross another callout", feat)
                    continue
                elbow = (centre[0], elbow_y)
                occupied.append((x0, x1, elbow_y))
                _add(view, i, _rim_tip(centre, elbow, dia), elbow, side, callout)
                _add_furniture(dwg, a, view, i, feat, to_page)
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

        # Round view's horizontal centre axis (page coords).
        view_cx = a.PV_X if view == "plan" else a.SV_X
        view_cy = a.PV_Y if view == "plan" else a.SV_Y

        # Keep-out bands (ADR 0009 Amendment 5, P4c, #318) — the page rows a callout's
        # "⌀… ↓…" text may not sit on, handed to `plan_strip(forbidden=...)` so the
        # spacing solve avoids them by construction (retiring the old `_coaxial_lift`
        # pre-solve nudge). Each is `(centre, half_width)`. Two causes, keyed on the
        # crossing line, not the part's shape:
        #  - location-dim extension lines: the rows where `_locate_off_axis_holes`
        #    will draw the off-axis bores' height/offset dims. Computed from hole
        #    geometry (the callout carries no feature link; its own row equals its
        #    bore's row). Patterned holes are skipped to match `_locate_off_axis_holes`
        #    (`h not in patterned`) — they carry no per-hole location dim, so reserving
        #    their rows would only force needless avoidance. A superset guard, not an
        #    exact match: rows for dims that later dedup/drop/sub-mm-gate stay
        #    over-reserved (conservative — a spurious avoidance is still valid), never
        #    under; and
        #  - the centre line of a turned/rotational round view — a coaxial bore led
        #    out along it has its callout text crossed by the centre mark / centreline
        #    (#305). Only a near-centre callout is close enough for the band to move.
        clr = draft.font_size + 3 * draft.pad_around_text  # clearance off a crossing line
        patterned = {h for p in a.patterns for h in p.holes}
        off_axis_letter = {"side": "x", "front": "y"}.get(view)
        reserved_rows = (
            [
                to_page(h.location)[1]
                for h in a.holes
                if _axis_letter(h) == off_axis_letter and h not in patterned
            ]
            if off_axis_letter
            else []
        )
        forbidden = [(r, clr) for r in reserved_rows]
        if a.is_rotational or a.prof is not None:
            forbidden.append((view_cy, clr))

        # --- Pass 1: boundary assignment ---
        right_queue = []  # (locs, dia, callout, feat, natural_y, rep)
        left_queue = []

        for locs, dia, callout, feat in specs:
            w = callout.callout_width
            rep_r = max(locs, key=lambda loc: to_page(loc)[0])
            centre_r = to_page(rep_r)
            d_right = edge_right - centre_r[0]

            if edge_left is not None:
                rep_l = min(locs, key=lambda loc: to_page(loc)[0])
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
                _log.info("Hole callout ø%s skipped (no room)", _fmt(dia))
                _record_callout_drop(dwg, view, dia, "no room beside the view", feat)
                continue

            # Natural Y is the bore's own row; keep-out-band avoidance is now the
            # solve's job (`forbidden`, below), not a pre-solve lift.
            if can_right and (not can_left or d_right <= d_left):
                right_queue.append((locs, dia, callout, feat, centre_r[1], rep_r))
            else:
                left_queue.append((locs, dia, callout, feat, centre_l[1], rep_l))

        # Sort each queue by natural Y so leaders don't cross.
        right_queue.sort(key=lambda s: s[4])
        left_queue.sort(key=lambda s: s[4])

        # --- Pass 2: Y placement + selection via the collect-then-solve seam ---
        # Each queued callout becomes a StripCandidate (a measured render-intent);
        # one plan_strip per side does the site-ordered spacing and the over-capacity
        # drop (ADR 0009 / #321 P1a). This is the first *production* placer routed
        # through plan_strip — it replaces the bespoke _solve_strip_via_layout + the
        # greedy prefix-drop. plan_strip bottoms out in the min-leader PAVA solve
        # (_solve_strip_1d_pava, Amendment 4), and
        # the queue is pre-sorted by natural Y (so plan_strip's (anchor_y, key) order is
        # the queue order → leaders stay crossing-free). Over-capacity selection is now
        # by real per-feature priority — the hole DIAMETER (D3/#322): when the strip
        # cannot hold every callout, the smallest-bore features drop first so the most
        # significant survive (the same "largest wins" policy the front-view shaft rows
        # already use, line 638). Ties (equal bore) fall back to key = natural-Y order.
        def _build_leader_at(s, edge, side, y):
            """The Leader `_add` would draw for queue entry *s* at elbow-Y *y* —
            built but not placed, so its footprint can be checked before
            committing (ADR 0009 P5 strand 3). Returns ``(leader, tip, elbow)``."""
            _locs, dia, callout, _feat, _ny, rep = s
            centre = to_page(rep)
            if side == "right":
                elbow = (edge + elbow_dx, y)
                tip = _rim_tip(centre, elbow, dia)
                tip = (min(tip[0], edge - draft.arrow_length), tip[1])
            else:
                elbow = (edge - elbow_dx, y)
                tip = _rim_tip(centre, elbow, dia)
                tip = (max(tip[0], edge + draft.arrow_length), tip[1])
            leader = Leader(
                tip=(tip[0], tip[1], 0),
                elbow=(elbow[0], elbow[1], 0),
                label="",
                draft=draft,
                text_side=side,
                callout=callout,
            )
            return leader, tip, elbow

        def _leader_hits(leader, tip, elbow, side, obstacles):
            """True when *leader*'s rendered footprint truly overlaps any
            *obstacles* box — split into the diagonal tip→elbow SHAFT (checked
            precisely via `_segment_hits_box`, since its AABB over-claims the
            empty triangle it doesn't occupy — #305, precise angled-leader
            geometry is P4/#318) and the elbow→label shelf+text (genuinely
            axis-aligned, so the coarse AABB check there is already exact)."""
            full_box = _geom_box(leader)
            if full_box is None or not _box_hits(full_box, obstacles):
                return False  # fast reject: nowhere near any obstacle
            if any(_segment_hits_box(tip, elbow, o) for o in obstacles):
                return True
            label_box = (
                (elbow[0], full_box[1], full_box[2], full_box[3])
                if side == "right"
                else (full_box[0], full_box[1], elbow[0], full_box[3])
            )
            return _box_hits(label_box, obstacles)

        def _is_central(s):
            """The coaxial hole whose callout belongs *on* the view-centre row, and
            so is anchored there (ADR 0009 Amendment 4) — the exact minimum-leader
            spacing solve can't then slide it off centre on a tie (the equal-cost
            placements differ only in *which* callout absorbs the shift, and for a
            central feature that must not be the central one).

            Prismatic parts only: on a turned/rotational round view the centre-line
            *itself* runs through this row, so the coaxial bore must be pushed
            **off** it (the ``forbidden`` centreline band) — the opposite of
            anchoring. The two are mutually exclusive by part class, so anchoring
            is gated off exactly when the centreline band is on (ADR 0009
            Amendment 5, P4c). ``s[5]`` is the callout's representative hole
            location (``None`` only for a left-queue entry with no left edge, which
            never happens for a placed callout)."""
            rep = s[5]
            if rep is None or a.is_rotational or a.prof is not None:
                return False
            cx, cy = to_page(rep)
            tol = draft.font_size
            return abs(cx - view_cx) < tol and abs(cy - view_cy) < tol

        def _place_queue(queue, edge, side, key_prefix, start_i):
            if not queue:
                return start_i

            # Baseline: the single full-range solve the pre-#351-P5-strand-3 code
            # always did, ignoring drawing-level obstacles entirely — every
            # candidate pulled toward its own natural Y, respecting only min_gap
            # from its queue siblings. Used below as the "cost of NOT avoiding"
            # reference a carve-based relocation must beat to be worth taking.
            base_cands = [
                StripCandidate(
                    key=f"{key_prefix}{j:04d}",
                    anchor=(edge, s[4]),
                    size=(s[2].callout_width, min_gap),
                    priority=s[1],
                    anchored=_is_central(s),
                )
                for j, s in enumerate(queue)
            ]
            base_res = plan_strip(base_cands, y_min, y_max, min_gap, forbidden=forbidden)
            base_by_key = {c.key: s for c, s in zip(base_cands, queue, strict=True)}
            base_y = {id(base_by_key[k]): y for k, y in base_res.placed.items()}
            base_dropped = {id(base_by_key[k]) for k in base_res.dropped}

            # Carve [y_min, y_max] around drawing-level obstacles this column's
            # leaders would cross (e.g. the section cutting-plane arrow — #351 P5
            # strand 3): a Y-only solve can't see an obstacle it never measures,
            # the textbook invisible-occupant defect. Probed at each candidate's
            # OWN natural Y, not a shared reference — a callout's leader shaft is
            # position-dependent geometry (it runs from the fixed hole location to
            # the elbow), so probing everyone at one far-away Y badly misjudges it.
            def _probe_box(s):
                # Unlike the old code (which only ever built a Leader for a
                # candidate already chosen for placement), this probes EVERY
                # queued candidate up front, including ones that may end up
                # dropped — so a degenerate geometry unreachable before now
                # (e.g. a hole essentially coincident with the strip edge) gets
                # a defensive catch here too, matching _geom_box's own
                # established "not every annotation bbox-es cleanly" idiom.
                try:
                    leader, _, _ = _build_leader_at(s, edge, side, s[4])
                except Exception as exc:  # noqa: BLE001 — geometry construction raises broadly
                    _log.debug("plan/side %s strip: probe leader failed (%s); omitted", side, exc)
                    return None
                return _geom_box(leader)

            probe_boxes = [b for s in queue if (b := _probe_box(s)) is not None]
            occupied = strip_obstacles(dwg, view=view, crossable=CROSSABLE_TYPES)
            if probe_boxes:
                band_lo = min(b[0] for b in probe_boxes)
                band_hi = max(b[2] for b in probe_boxes)
                occupied = [o for o in occupied if o[0] < band_hi and o[2] > band_lo]
            segs = carve_free_segments(y_min, y_max, [(o[1], o[3]) for o in occupied], min_gap)

            # Assign each candidate to the free segment nearest its natural Y
            # (containing it if possible, else the closer boundary) — the
            # multi-segment analogue of the single plan_strip call this replaces.
            # In practice at most one obstacle (the section arrow) ever splits
            # this column, so a per-segment solve stays close to the
            # single-segment optimum while never overprinting the carved gap.
            def _nearest_seg(ny):
                for lo, hi in segs:
                    if lo <= ny <= hi:
                        return (lo, hi)
                return min(
                    segs, key=lambda sg: min(abs(ny - sg[0]), abs(ny - sg[1])), default=None
                )

            by_seg: dict = {seg: [] for seg in segs}
            for s in queue:
                seg = _nearest_seg(s[4])
                if seg is not None:
                    by_seg[seg].append(s)
            # A candidate whose natural Y fell outside every free segment (the
            # whole column blocked, or between two far-apart obstacles) is simply
            # absent from `by_seg` — the final per-candidate loop below already
            # falls back to the baseline position for it (no special "unassigned"
            # bucket needed; `plan_strip`'s own dropped set, from a segment truly
            # out of capacity, is likewise just absent from `seg_y` below).

            seg_y: dict = {}  # id(s) -> elbow_y, from the carve-aware per-segment solves

            def _solve_segment(cands_in, lo, hi, key_prefix_local):
                cands = [
                    StripCandidate(
                        key=f"{key_prefix_local}{j:04d}",
                        anchor=(edge, s[4]),
                        size=(s[2].callout_width, min_gap),
                        priority=s[1],  # bore diameter — largest wins over-capacity (D3)
                        anchored=_is_central(s),
                    )
                    for j, s in enumerate(cands_in)
                ]
                res = plan_strip(cands, lo, hi, min_gap, forbidden=forbidden)
                by_key = {c.key: s for c, s in zip(cands, cands_in, strict=True)}
                for k, y in res.placed.items():
                    seg_y[id(by_key[k])] = y

            for (seg_lo, seg_hi), members in by_seg.items():
                if members:
                    _solve_segment(members, seg_lo, seg_hi, key_prefix)
            # A candidate whose natural Y fell outside every free segment (the
            # whole column blocked, or between two far-apart obstacles) never
            # entered `by_seg` — it has no carve-aware position to compare below,
            # so the baseline (with crossing accepted, policy B) is its only shot.

            # Decide per candidate: take the carve-aware (obstacle-avoiding)
            # position ONLY when a free one exists AND it doesn't cost much more
            # displacement than simply accepting a crossing at the natural-pull
            # baseline (user, 2026-07-02 — a large relocation to dodge a thin
            # obstacle is worse than a small, visible, correctable crossing,
            # matching the existing side_drilled corridor-relocate precedent).
            # A tight avoidance win is still taken: it is a genuine improvement.
            # One row's worth of nudge (min_gap, the smallest spacing unit this
            # placer already reasons in) is "cheap"; more than that is a real
            # repositioning, not a nudge.
            _RELOCATE_TOLERANCE = min_gap
            placed: list = []  # (s, elbow_y, leader) — leader built once, reused at emit
            crossing: list = []  # ditto, kept despite an obstacle crossing (policy B)
            dropped: list = []  # s — genuinely no room anywhere, not just a crossing
            for s in queue:
                sid = id(s)
                natural = s[4]
                cand_y = seg_y.get(sid)
                base_ok = sid in base_y and sid not in base_dropped
                if cand_y is not None and base_ok:
                    d_seg = abs(cand_y - natural)
                    d_base = abs(base_y[sid] - natural)
                    y = cand_y if d_seg <= d_base + _RELOCATE_TOLERANCE else base_y[sid]
                elif cand_y is not None:
                    y = cand_y  # baseline dropped it outright — the carve saved it
                elif base_ok:
                    y = base_y[sid]  # no free segment took it — fall back to baseline
                else:
                    dropped.append(s)
                    continue
                leader, tip, elbow = _build_leader_at(s, edge, side, y)
                if _leader_hits(leader, tip, elbow, side, occupied):
                    crossing.append((s, y, leader))
                else:
                    placed.append((s, y, leader))

            if dropped:
                _log.warning(
                    "plan/side %s strip: %d of %d bore callouts skipped (strip full)",
                    side,
                    len(dropped),
                    len(queue),
                )
                for s in dropped:
                    _record_callout_drop(dwg, view, s[1], f"{side} strip full", s[3])
            if crossing:
                _log.info(
                    "plan/side %s strip: %d bore callout(s) placed despite crossing an "
                    "obstacle (policy B — kept, not dropped)",
                    side,
                    len(crossing),
                )
            placed.extend(crossing)
            # Emit survivors in natural-Y order so the hc_{view}{i} names + centre-
            # mark indices land on the same callouts as the old queue-order emit
            # (the queue itself was already sorted by natural Y before Pass 2).
            i = start_i
            for s, _elbow_y, leader in sorted(placed, key=lambda p: p[0][4]):
                _locs, dia, callout, feat, _ny, rep = s
                dwg.add(leader, f"hc_{view}{i}", view=view)
                _add_furniture(dwg, a, view, i, feat, to_page)
                i += 1
            return i

        next_i = _place_queue(right_queue, edge_right, "right", "hc_r", 0)
        assert edge_left is not None or not left_queue  # populated only when edge_left is set
        _place_queue(left_queue, edge_left, "left", "hc_l", next_i)
