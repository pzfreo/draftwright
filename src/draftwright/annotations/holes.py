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
    CenterMark,
    Leader,
)

from draftwright._core import (
    _CONCENTRIC_TOL_MM,
    _END_ON,
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
    CorridorCandidate,
    Escalation,
    _box_hits,
    _geom_box,
    _segment_hits_box,
    carve_free_position,
    carve_free_segments,
    clear_label_of_centerlines,
    place_strip_candidates,
    register_corridor,
    strip_obstacles,
)
from draftwright.annotations.from_model import (
    _diameter_column_left,
    _diameter_row_below,
    callout_from_spec,
    hole_callout_spec,
)
from draftwright.layout import StripCandidate, plan_strip
from draftwright.model import plan_dimensions
from draftwright.model.ir import HoleFeature, PatternFeature
from draftwright.model.planner import plan_locations


def add_feature_callout(dwg, feature, *, view: str | None = None, name: str | None = None) -> str:
    """Add a hole/pattern ø-depth **leader callout** for *feature* — the #414 add verb,
    the callout-mechanism half of the editable surface (symmetric with :meth:`Drawing.drop`).

    Funnels into the same :func:`hole_callout_spec` / :func:`callout_from_spec` the
    auto-pass uses, so the callout text (ø, ``n×``, through/depth, cbore, pattern suffix)
    is identical. Placement is a single reasonable leader beside the feature's end-on view
    — not the auto-pass's whole-set priority solve (byte-identity is not a goal, #400 Ph2):
    a lone added callout goes into free strip space and leans on :meth:`Drawing.repair` /
    the coverage lint for the rest. The leader is tagged with *feature* so :meth:`drop` /
    :meth:`annotations_of` find it. Returns the annotation name.

    Raises ``ValueError`` if the drawing has no detected model, *feature* is not in it, or
    the feature exposes no hole callout (use :meth:`dimension` for a linear param instead).
    """
    model = getattr(dwg, "_part_model", None)
    if model is None:
        raise ValueError("callout(): no detected model — build the drawing first")
    if not any(f is feature for f in model.features):
        raise ValueError(
            "callout(): feature is not from this drawing's model — "
            "pass one from dwg.model().features"
        )
    group = next((g for g in plan_dimensions(model) if g.feature is feature), None)
    spec = hole_callout_spec(group) if group is not None else None
    if spec is None:
        raise ValueError(
            f"callout() draws a hole/pattern ø-depth leader callout; "
            f"{type(feature).__name__} exposes none — use dimension() for a linear param"
        )
    draft = dwg.draft
    a = getattr(dwg, "_analysis", None)
    members = feature.members or (feature.frame.origin,)
    # count comes from the spec (== feat.count) — the same source the auto-pass's
    # bare path uses — not re-derived from len(members) (#414 review).
    callout = callout_from_spec(spec, draft, spec["count"])
    assert callout is not None  # spec is non-None here, so callout_from_spec returns one
    view = view or _END_ON[feature.frame.axis]
    if view not in _END_ON.values():  # front/plan/side — the ortho end-on views only
        raise ValueError(f"callout(): view {view!r} is not a hole-callout view (front/plan/side)")
    gap = draft.pad_around_text
    w = callout.callout_width
    tier = draft.font_size + 2 * gap
    dia = spec["diameter"]

    def _rim_tip(centre, elbow):
        # Pull the leader tip from the hole centre to its circumference (bore dia mm).
        r = dia * dwg.scale / 2
        dx, dy = elbow[0] - centre[0], elbow[1] - centre[1]
        norm = math.hypot(dx, dy)
        return centre if norm <= r else (centre[0] + dx / norm * r, centre[1] + dy / norm * r)

    vb = dwg.view_bounds(view) or (0.0, 0.0, 0.0, 0.0)
    vx0, vy0, vx1, vy1 = vb
    # tip on the member nearest the placement side (rightmost in page X)
    centre = dwg.at(view, *max(members, key=lambda m: dwg.at(view, *m)[0]))[:2]

    if view == "front":  # below the view (matches the auto-pass's front-callout side)
        zones = getattr(a, "fv_zones", None) if a is not None else None
        strip = getattr(zones, "below", None) if zones is not None else None
        elbow_y = vy0 - max(tier, 0.6 * getattr(a, "DIM_PAD", 12.0))
        if strip is not None:
            coord = carve_free_position(
                dwg, strip, view, "y", tier, (centre[0], centre[0] + gap + w)
            )
            if coord is not None:
                elbow_y = coord
        elbow = (centre[0], elbow_y)
        room_right = (a.PAGE_W - a.margin) if a is not None else centre[0] + gap + w
        tside = "right" if centre[0] + gap + w <= room_right else "left"
    else:  # plan / side → to the right of the view
        zones = (
            getattr(a, {"plan": "pv_zones", "side": "sv_zones"}[view], None)
            if a is not None
            else None
        )
        strip = getattr(zones, "right", None) if zones is not None else None
        elbow_x = vx1 + gap
        if strip is not None:
            coord = carve_free_position(
                dwg, strip, view, "x", tier, (centre[1] - tier / 2, centre[1] + tier / 2)
            )
            if coord is not None:
                elbow_x = coord
        elbow = (elbow_x, centre[1])
        tside = "right"

    if name is None:
        i = 0
        name = f"hc_{view}0"
        while name in dwg._named:
            i += 1
            name = f"hc_{view}{i}"
    tip = _rim_tip(centre, elbow)
    dwg.add(
        Leader(
            tip=(tip[0], tip[1], 0),
            elbow=(elbow[0], elbow[1], 0),
            label="",
            draft=draft,
            text_side=tside,
            callout=callout,
        ),
        name,
        view=view,
        feature=feature,
    )
    return name


def add_feature_location(
    dwg, feature, *, axes: tuple[str, ...] | None = None, pin: bool = False
) -> list[str]:
    """Add datum-referenced **X/Y position dimensions** for a Z-axis hole/pattern —
    the #418 ``locate()`` add verb (symmetric with :meth:`Drawing.drop`).

    Distinct from :meth:`dimension` (a feature's own *intrinsic* linear params):
    a location dim measures the *datum → feature-centre* offset, which no feature
    exposes as a ``parameter()``. Reuses the planner's :func:`plan_locations`
    *intent* (which ref, from which datum) and this renderer's projection, then
    places each dim into free strip space beside the view (corridor-free, like
    :meth:`callout` — the auto-pass's shared corridor batch does not exist on a
    detect-only build). X dims tier above the plan view, Y dims above the side
    view; each is tagged with *feature* so :meth:`drop` / :meth:`annotations_of`
    find it. Returns the placed names (0–2 — one per in-plane axis with a real
    offset; empty for a concentric/on-datum bore that has nothing to dimension).

    ``axes`` selects the in-plane axes to emit (default both); ``"x"`` = the plan-X
    position, ``"y"`` = the side-Y position. ``pin=True`` records each placed dim as a
    deliberate user edit so later repair/finalize work leaves its placement fixed (#511).

    Raises ``ValueError`` if the drawing has no detected model/analysis, *feature*
    is not in the model, or is not a Z-axis hole/pattern (side-drilled bores are placed
    by the auto-pass). A feature with no datum-referenced ref (a datum-less model, a
    concentric/on-datum bore, or a ref deduped against a sibling) returns ``[]``.
    """
    model = getattr(dwg, "_part_model", None)
    if model is None:
        raise ValueError("locate(): no detected model — build the drawing first")
    if not any(f is feature for f in model.features):
        raise ValueError(
            "locate(): feature is not from this drawing's model — "
            "pass one from dwg.model().features"
        )
    if not isinstance(feature, HoleFeature | PatternFeature):
        raise ValueError(
            f"locate() places a hole/pattern's datum-referenced position; "
            f"{type(feature).__name__} exposes none — use dimension() for a linear param"
        )
    if feature.frame.axis != "z":
        raise ValueError(
            "locate(): only Z-axis holes/patterns have plan location dims; a side-drilled "
            "bore is located by the auto-pass, not this verb"
        )
    a = getattr(dwg, "_analysis", None)
    if a is None:
        raise ValueError("locate(): no analysis — build the drawing first")
    want = {"x", "y"} if axes is None else {ax.lower() for ax in axes}
    if not want <= {"x", "y"}:
        raise ValueError("locate(): axes must be a subset of ('x', 'y')")

    # A feature with no datum-referenced ref — a concentric/on-axis bore (located by a
    # centre mark), an on-datum hole, or one whose ref coincides with another feature's
    # (deduped by plan_locations) — has nothing to dimension here. An honest empty
    # result (as the docstring promises), not an error, so the verb composes: the emitted
    # #400 Ph2 script calls locate() on every hole and this no-ops the ones the auto-pass
    # would also skip, matching its dedup rather than crashing.
    mine = [pd for pd in plan_locations(model) if pd.feature is feature]
    if not mine:
        return []
    draft = dwg.draft
    datum = mine[0].datum
    assert datum is not None  # plan_locations always sets the datum
    dx, dy = datum.at[0], datum.at[1]
    tier = draft.font_size + 2 * draft.pad_around_text
    PX, PY = a.proj.plan_x, a.proj.plan_y
    SX, SZ = a.proj.side_x, a.proj.side_z

    def _uniq(prefix: str) -> str:
        j = 0
        nm = f"{prefix}{j}"
        while nm in dwg._named:
            j += 1
            nm = f"{prefix}{j}"
        return nm

    def _place(view: str, strip, p1, p2, baseline, label: str) -> str:
        perp = (min(p1, p2), max(p1, p2))
        pos = carve_free_position(dwg, strip, view, "y", tier, perp) if strip is not None else None
        if pos is None:  # strip full / absent — fall back just above the view
            vb = dwg.view_bounds(view) or (0.0, 0.0, 0.0, baseline)
            pos = vb[3] + tier
        nm = _uniq("m_locx" if view == "plan" else "m_locy")
        dwg.add(
            _dim(
                (p1, baseline, 0), (p2, baseline, 0), "above", pos - baseline, draft, label=label
            ),
            nm,
            view=view,
            feature=feature,
        )
        if pin:
            dwg.pin(nm)
        return nm

    names: list[str] = []
    seen_x: list[float] = []
    seen_y: list[float] = []
    for pd in mine:
        if pd.param.span is None:
            continue
        rx, ry = pd.param.span[1][0], pd.param.span[1][1]
        # A rotational part's on-axis *hole* is located by the centreline, not a
        # position dim (matches render_locations); a pattern ref is never filtered.
        if (
            pd.param.role == "location"
            and a.is_rotational
            and math.hypot(rx - a.cx, ry - a.cy) <= _CONCENTRIC_TOL_MM
        ):
            continue
        # Coincident X (or Y) across this feature's own members → one dim, not a stack
        # of identical position dims (matches render_locations' x_refs/y_refs dedup).
        if (
            "x" in want
            and abs(rx - dx) * a.SCALE >= 1.0
            and not any(abs(rx - s) < 0.5 for s in seen_x)
        ):
            seen_x.append(rx)
            names.append(
                _place(
                    "plan",
                    getattr(a.pv_zones, "above", None),
                    PX(dx),
                    PX(rx),
                    PY(ry),
                    _fmt(rx - dx),
                )
            )
        if (
            "y" in want
            and abs(ry - dy) * a.SCALE >= 1.0
            and not any(abs(ry - s) < 0.5 for s in seen_y)
        ):
            seen_y.append(ry)
            names.append(
                _place(
                    "side",
                    getattr(a.sv_zones, "above", None),
                    SX(dy),
                    SX(ry),
                    SZ(a.bb.max.Z),
                    _fmt(ry - dy),
                )
            )
    return names


def add_feature_furniture(dwg, feature, *, view: str | None = None) -> list[str]:
    """Add a hole/pattern's non-dimensional **sheet furniture** — the #419 ``furniture()``
    add verb (symmetric with :meth:`Drawing.drop`).

    Unifies the two geometric marks a feature carries that no other verb emits: per-hole
    **centre marks** (every member) and, for a pattern, its **centre-cross** (bolt circle)
    or **pitch/grid dimensions** (linear/grid array). Funnels into the same
    :func:`render_centermarks` centre-mark math and :func:`_add_furniture` the auto-pass
    uses — both already corridor-free and feature-tagged — so a detect-only build can
    reconstruct them. Each mark is tagged with *feature* so :meth:`drop` /
    :meth:`annotations_of` find it. Returns the placed names (varies by pattern kind).

    Raises ``ValueError`` if the drawing has no detected model/analysis, *feature* is not
    in it, or *feature* is not a hole/pattern (use :meth:`dimension` for a linear param).
    """
    model = getattr(dwg, "_part_model", None)
    if model is None:
        raise ValueError("furniture(): no detected model — build the drawing first")
    if not any(f is feature for f in model.features):
        raise ValueError(
            "furniture(): feature is not from this drawing's model — "
            "pass one from dwg.model().features"
        )
    if not isinstance(feature, HoleFeature | PatternFeature):
        raise ValueError(
            f"furniture() draws a hole/pattern's centre marks + pattern furniture; "
            f"{type(feature).__name__} exposes none — use dimension() for a linear param"
        )
    a = getattr(dwg, "_analysis", None)
    if a is None:
        raise ValueError("furniture(): no analysis — build the drawing first")
    view = view or _END_ON[feature.frame.axis]
    before = set(dwg.annotations())

    # Centre marks — one per member, mirroring render_centermarks' size/placement.
    dia = feature.member.diameter if isinstance(feature, PatternFeature) else feature.diameter
    size = max(2.5, dia * dwg.scale + 2.0)
    for loc in feature.members or (feature.frame.origin,):
        px, py, *_ = dwg.at(view, *loc)
        j = 0
        while (nm := f"m_cm{j}") in dwg._named:
            j += 1
        dwg.add(CenterMark((px, py, 0), size, dwg.draft), nm, view=view, feature=feature)

    # Pattern furniture — bolt-circle centre-cross / linear-or-grid pitch dims.
    if isinstance(feature, PatternFeature):
        # Scan for a free furniture slot j across all three name shapes: a bolt-circle
        # centre-cross (bc_{view}{j}), a linear pitch (dim_pitch_{view}{j}), and a grid's
        # two suffixed pitch dims (dim_pitch_{view}{j}_0/_1) — the bare key is never used
        # by a grid, so probing it alone would collide on a second grid (#419 review F4).
        j = 0
        while any(
            nm in (f"bc_{view}{j}", f"dim_pitch_{view}{j}")
            or nm.startswith(f"dim_pitch_{view}{j}_")
            for nm in dwg._named
        ):
            j += 1
        _add_furniture(dwg, a, view, j, feature, lambda loc: dwg.at(view, *loc))

    return sorted(set(dwg.annotations()) - before)


def add_feature_diameter(dwg, feature) -> str:
    """Add a turned **step/boss diameter** ø-leader — the #419 extension of the callout
    add verb to :class:`StepFeature` / :class:`BossFeature`.

    A turned step's diameter is a leader callout the hole-callout path does not reach and
    :meth:`dimension` rejects (its diameter param carries no span). Funnels into the same
    :func:`_diameter_row_below` (X-turned) / :func:`_diameter_column_left` (Z-turned) the
    auto-pass uses — already corridor-free and feature-tagged (#412). Returns the name.

    Raises ``ValueError`` if the feature exposes no step/boss diameter, its turning axis
    is unsupported, or there is no room to place the leader.
    """
    model = getattr(dwg, "_part_model", None)
    if model is None:
        raise ValueError("callout(): no detected model — build the drawing first")
    if not any(f is feature for f in model.features):
        raise ValueError(
            "callout(): feature is not from this drawing's model — "
            "pass one from dwg.model().features"
        )
    group = next((g for g in plan_dimensions(model) if g.feature is feature), None)
    dpd = (
        next((pd for pd in group.dims if pd.param.kind == "diameter"), None)
        if group is not None
        else None
    )
    dia = dpd.param.value if dpd is not None else None
    if group is None or dpd is None or dia is None:
        raise ValueError(
            f"callout(): {type(feature).__name__} exposes no step/boss diameter callout"
        )
    axis = feature.frame.axis
    if axis not in ("x", "z"):
        raise ValueError(
            f"callout(): a {axis!r}-turned step/boss diameter is not placeable "
            "(only X- and Z-turned parts)"
        )
    # 4-tuple (anchor, dia, feature, tolerance): a manual callout honours a declared ±
    # tolerance too, like the auto-pass (P2a, #28).
    items = [(group.anchor, dia, feature, dpd.param.tolerance)]
    # The row/column placers name leaders m_dia_{x,z}{start+i} — pass the first FREE
    # index so a second callout() (or a call on an already-annotated turned part) never
    # collides on m_dia_x0/z0 and clobbers an existing leader (#419 review F1).
    prefix = "m_dia_x" if axis == "x" else "m_dia_z"
    start = 0
    while f"{prefix}{start}" in dwg._named:
        start += 1
    before = set(dwg.annotations())
    if axis == "x":
        _diameter_row_below(dwg, items, start=start)
    else:
        _diameter_column_left(dwg, items, start=start)
    new = sorted(set(dwg.annotations()) - before)
    if not new:
        # No room — degrade like the auto-pass (render_diameters places what fits and drops
        # the overflow to feature_not_dimensioned), NOT a raise: the emitted reconstruction
        # calls callout() per step, so a crowded turned shaft must not abort (#427 review).
        _log.info("Step/boss ø%s callout skipped (no room)", _fmt(dia))
        return ""
    return str(new[0])


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
      forced the shorter location dim's arrows outside. The envelope and these
      best-effort dims now co-solve in one corridor; envelope priority prevents
      starvation.
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

    def _emit(strip, view, axis, cands, force=False, features=None):
        # The collect-then-solve strip placer now lives in _common as the shared
        # place_strip_candidates (P3, retiring the Strip cursor #150); this thin wrapper
        # binds the pass's dwg + tier so the across/along/Z callers below are unchanged.
        # *features* (name -> IR feature) attributes each dim for drop() (#408).
        return place_strip_candidates(
            dwg, strip, view, axis, cands, tier, force=force, features=features
        )

    def _queue(
        strip,
        view,
        side,
        axis,
        cands,
        *,
        features=None,
        force=True,
        on_drop=None,
        order_key=None,
    ):
        # Below/right side-hole locations now feed the same corridor batch as envelope,
        # GD&T, and PMI (#477). Their historical policy was force-keep unless the strip is
        # physically full; callers that have a relocation path provide an on_drop fallback.
        if strip is None:
            for name, _build in cands:
                (on_drop or (lambda _nm: None))(name)
            return
        for i, (name, build) in enumerate(cands):
            register_corridor(
                dwg,
                (view, side),
                strip,
                view,
                axis,
                tier,
                CorridorCandidate(
                    name=name,
                    build=build,
                    order=(1, order_key(name, i) if order_key is not None else i, name),
                    on_place=lambda _nm: None,
                    on_drop=(on_drop or (lambda _nm: None)),
                    force=force,
                    feature=(features or {}).get(name),
                ),
            )

    def _off_axis_owner(hole_locs):
        # The IR hole feature owning a side-drilled location dim, or None when the dim's
        # offset is shared by >1 distinct feature (unowned, so drop can't over-strip a
        # sibling — mirrors the #398c shared-coordinate rule). *hole_locs* are the model
        # locations of every hole that contributed to this (deduped-by-offset) dim.
        feats = {dwg._feature_of_hole_at(loc) for loc in hole_locs}
        feats.discard(None)
        return next(iter(feats)) if len(feats) == 1 else None

    # "across" phase — an X-axis hole's Y position below the SIDE view, queued with
    # the envelope so the overall depth dim stacks outside it (ISO order). Confined to
    # the side view: a Y-axis hole's X position contends the FRONT-below strip with the
    # turned-diameter ø-row, so it stays in the "along" phase (after the diameter pass),
    # preserving the #133 priority.
    if which == "across":
        yw = SZ(dz) - 2
        seen_y: set = set()
        cands = []
        order_y: dict = {}
        loc_by_name: dict = {}  # dim name -> contributing hole locations (for provenance)
        for h in (h for h in off if _axis_letter(h) == "x"):
            yo = round(abs(h.location[1] - dy), 2)
            if yo * a.SCALE < 1.0:
                continue
            name = f"dim_loc_side_y{round(yo * 100)}"
            loc_by_name.setdefault(name, []).append(h.location)
            order_y[name] = yo
            if yo not in seen_y:
                seen_y.add(yo)
                p_lo, p_hi = (SX(dy), yw, 0), (SX(h.location[1]), yw, 0)
                cands.append(
                    (
                        name,
                        lambda pos, pl=p_lo, ph=p_hi, lb=yo: _dim(
                            pl, ph, "below", yw - pos, draft, label=_fmt(lb)
                        ),
                    )
                )
        feats = {nm: _off_axis_owner(locs) for nm, locs in loc_by_name.items()}
        _queue(
            a.sv_zones.below,
            "side",
            "below",
            "y",
            cands,
            features=feats,
            on_drop=lambda _nm: _drop("y", "side"),
            order_key=lambda nm, _i: order_y.get(nm, _i),
        )
        return

    # "along" phase (after the envelope + turned-diameter passes): a Y-axis hole's X
    # position below the FRONT view, then every hole's height (Z) to the right.
    xw = FZ(dz) - 2
    seen_x: set = set()
    x_cands = []
    order_x: dict = {}
    x_loc_by_name: dict = {}
    for h in (h for h in off if _axis_letter(h) == "y"):
        xo = round(abs(h.location[0] - dx), 2)
        if xo * a.SCALE < 1.0:
            continue
        name = f"dim_loc_front_x{round(xo * 100)}"
        x_loc_by_name.setdefault(name, []).append(h.location)
        order_x[name] = xo
        if xo not in seen_x:
            seen_x.add(xo)
            p_lo, p_hi = (FX(dx), xw, 0), (FX(h.location[0]), xw, 0)
            x_cands.append(
                (
                    name,
                    lambda pos, pl=p_lo, ph=p_hi, lb=xo: _dim(
                        pl, ph, "below", xw - pos, draft, label=_fmt(lb)
                    ),
                )
            )
    x_feats = {nm: _off_axis_owner(locs) for nm, locs in x_loc_by_name.items()}
    _queue(
        a.fv_zones.below,
        "front",
        "below",
        "y",
        x_cands,
        features=x_feats,
        on_drop=lambda _nm: _drop("x", "front"),
        order_key=lambda nm, _i: order_x.get(nm, _i),
    )

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
    z_locs: dict = {}  # z-offset -> contributing hole locations (for provenance)
    for h in off:
        zo = round(abs(h.location[2] - dz), 2)
        if zo * a.SCALE >= 1.0:
            z_locs.setdefault(zo, []).append(h.location)
    seen_z = set()
    for h in off:
        zo = round(abs(h.location[2] - dz), 2)
        if zo * a.SCALE < 1.0 or zo in seen_z:
            continue
        seen_z.add(zo)
        hz = h.location[2]
        owner = _off_axis_owner(z_locs[zo])

        def _zc(view, p_lo, p_hi, edge, _zo=zo):
            return (
                f"dim_loc_{view}_z{round(_zo * 100)}",
                lambda pos, pl=p_lo, ph=p_hi, e=edge: _dim(
                    pl, ph, "right", pos - e, draft, label=_fmt(_zo)
                ),
            )

        def _zf(view, _zo=zo, _owner=owner):  # provenance map for the z dim in this view
            return {f"dim_loc_{view}_z{round(_zo * 100)}": _owner}

        side_cand = (a.sv_zones.right, "side", (zr, SZ(dz), 0), (zr, SZ(hz), 0), zr)
        front_cand = (a.fv_zones.right, "front", (zrf, FZ(dz), 0), (zrf, FZ(hz), 0), zrf)
        order = (side_cand, front_cand) if _axis_letter(h) == "x" else (front_cand, side_cand)
        primary, *alternates = order
        strip, view, p_lo, p_hi, edge = primary
        primary_cand = _zc(view, p_lo, p_hi, edge)

        def _fallback(
            _nm,
            _primary=primary,
            _alts=tuple(alternates),
            _cand=primary_cand,
            _feature_map=_zf,
        ):
            for alt_strip, alt_view, alt_p_lo, alt_p_hi, alt_edge in _alts:
                alt = _zc(alt_view, alt_p_lo, alt_p_hi, alt_edge)
                if not _emit(alt_strip, alt_view, "x", [alt], features=_feature_map(alt_view)):
                    return
            p_strip, p_view, _p_lo, _p_hi, _edge = _primary
            if _emit(p_strip, p_view, "x", [_cand], force=True, features=_feature_map(p_view)):
                _drop("Z", p_view)

        _queue(
            strip,
            view,
            "right",
            "x",
            [primary_cand],
            features=_zf(view),
            force=False,
            on_drop=_fallback,
            order_key=lambda _nm, _i, _zo=zo: _zo,
        )


def _add_furniture(dwg, a: Analysis, view, j, feat: PatternFeature | None, to_page):
    """Pattern sheet furniture, added once its callout is placed (#92). Driven by the
    IR `PatternFeature` *feat* (members / bcd / pitch / grid), not a recogniser
    `Pattern` — ADR 0008 Amendment 6. Plain (unpatterned) plan callouts carry no
    furniture; their scattered-hole-table coverage is recorded at the emit site (not
    here) so it survives finalize's ``place_furniture=False`` (#426 Ph4c)."""
    if feat is None:
        return
    members = feat.members or (feat.frame.origin,)  # guard a declared pattern's empty members
    # Remember the bore-callout name AND the holes it documents (by position), so a
    # later hole-table escalation leaves the grouped pattern callout standing and
    # tabulates only the holes no *placed* pattern callout covers (#92).
    dwg._cover_pattern(f"hc_{view}{j}", [HoleRef.of(m) for m in members])
    if feat.pattern == "bolt_circle":
        assert feat.bcd is not None  # a bolt circle always carries its BCD
        cx = sum(to_page(m)[0] for m in members) / len(members)
        cy = sum(to_page(m)[1] for m in members) / len(members)
        # Furniture provenance (#408): the pattern owns its centre line + pitch dims.
        dwg.add(
            CenterlineCircle((cx, cy), feat.bcd * a.SCALE),
            f"bc_{view}{j}",
            view=view,
            feature=feat,
        )
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
            feature=feat,
        )
    elif feat.pattern == "grid":
        assert feat.grid is not None  # a grid always carries its (row, col) pitch
        _add_grid_pitch_dims(dwg, a, view, j, members, feat.grid, to_page, feature=feat)


def _add_grid_pitch_dims(dwg, a: Analysis, view, j, members, nominals, to_page, feature=None):
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
            feature=feature,
        )

    _axis_dim(u1, l1, 0)
    _axis_dim(u2, l2, 1)


def _place_pitch_dim(dwg, a: Analysis, view, loc1, loc2, n, pitch, to_page, name, feature=None):
    """Pitch dimension between two hole-centre *locations* ``loc1``→``loc2``, labelled
    ``(n-1)× pitch``, placed just outside the view on the side of the row's
    outward perpendicular (#92). *feature* attributes it to the source pattern (#408)."""
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
    fallback_sides = [(side, reach)] + [c for c in cands if c[0] != side]

    def _make(off, side_vec=side, label_offset_x=0.0):
        return _dim(
            (p1[0], p1[1], 0),
            (p2[0], p2[1], 0),
            side_vec,
            off,
            dwg.draft,
            label=f"{n - 1}× {_fmt(pitch)}",
            label_offset_x=label_offset_x,
        )

    def _clear(off, side_vec, dim=None):
        # Nudge the LABEL (not the line — a dim line crossing a centre line is
        # fine, ISO 128) off any centre line / bolt-circle already placed in this
        # view (#129): a turned part's axis Centerline, or a pattern's own
        # CenterlineCircle. Not a complete guarantee — furniture for OTHER
        # patterns sharing this view may render after this dim, so a sibling
        # pattern's CenterlineCircle can still be missed; #129 only covers the
        # cases verified reachable (this dim's own pattern + any turned-axis line).
        # Returns (final, unshifted): `_make` builds real OCC geometry (a boolean
        # fuse per dim, #129 review — a production part hit a 120s single-op
        # timeout after this went from one build to three per placement), so a
        # caller that already has the unshifted dim passes it in as `dim` rather
        # than have it rebuilt here.
        dim = dim if dim is not None else _make(off, side_vec)
        centerlines = [
            o for _, o in dwg.annotations_in_view(view) if getattr(o, "is_centerline", False)
        ]
        lox = clear_label_of_centerlines(dim.label_bbox, centerlines, gap=1.0)
        if not lox:
            return dim, dim
        return _make(off, side_vec, label_offset_x=lox), dim

    def _clear_and_validate(off, side_vec, page_box, obstacles, dim=None):
        # The clearing shift moves label ink only — the dimension LINE (p1..p2, the
        # bulk of _geom_box's footprint) is unchanged and was never gated against
        # `obstacles` on this path in the first place (the strip carve's own
        # occupancy model is what makes the line's tier safe, a coarser guarantee
        # `_box_hits` doesn't share — checking the full geometry here rejected valid
        # shifts whenever the line's own extension routing already crossed another
        # dim's, which is normal and unrelated to the label). So re-check only the
        # LABEL's own bbox — the thing that actually moved — against the page and
        # every real obstacle, falling back to the unshifted dim otherwise.
        cleared, unshifted = _clear(off, side_vec, dim)
        if cleared is unshifted:
            return unshifted  # no shift applied — nothing to validate
        lbb = cleared.label_bbox
        if (
            lbb is not None
            and lbb[0] >= page_box[0]
            and lbb[1] >= page_box[1]
            and lbb[2] <= page_box[2]
            and lbb[3] <= page_box[3]
            and not _box_hits(lbb, obstacles)
        ):
            return cleared
        return unshifted

    def _place(off, side_vec=side):
        page_box = (a.margin, a.margin, a.PAGE_W - a.margin, a.PAGE_H - a.margin)
        obstacles = strip_obstacles(dwg, view=view, crossable=CROSSABLE_TYPES)
        dwg.add(
            _clear_and_validate(off, side_vec, page_box, obstacles),
            name,
            view=view,
            feature=feature,
        )

    # Place onto the zone strip for the chosen side (#374): each side is its own strip, so the
    # obstacle-aware carve stacks this dim clear of placed content — where an arbitrary-direction
    # 1-D search would be defeated by a dim on a *different* side (rotated/two-axis grids). An
    # axis-aligned side maps to a populated strip; a diagonal side, the side view's absent left
    # strip, or a genuinely full strip fall through to the bounded vector placement below
    # (behaviour unchanged for those residual cases).
    # Only a GENUINELY axis-aligned row uses the strip carve: its dim is a clean horizontal /
    # vertical line occupying one strip tier, and the coord bridge is exact. `_dim` places the
    # line at `mid + side*distance`, so the witness is the MIDPOINT component on the strip axis and
    # `sgn` the exact outward sign (±1); `distance = sgn*(pos - mid[axis])` then lands the line at
    # the carved `pos`. The tight threshold (≈1.6°) keeps a tilted row off this path — its dim
    # isn't axis-aligned, so it can't cleanly occupy a tier — routing it to the vector fallback.
    zones = {"plan": a.pv_zones, "front": a.fv_zones, "side": a.sv_zones}[view]
    sx, sy = side[0], side[1]
    strip = axis = perp = None
    witness = sgn = 0.0
    if abs(sx) > 0.9996:  # horizontal side ⟂ a vertical row → left/right strip, stacks along x
        strip, axis, perp, witness, sgn = (
            (zones.right if sx > 0 else zones.left),
            "x",
            tuple(sorted((p1[1], p2[1]))),
            mid[0],
            math.copysign(1.0, sx),
        )
    elif abs(sy) > 0.9996:  # vertical side ⟂ a horizontal row → above/below strip, stacks along y
        strip, axis, perp, witness, sgn = (
            (zones.above if sy > 0 else zones.below),
            "y",
            tuple(sorted((p1[0], p2[0]))),
            mid[1],
            math.copysign(1.0, sy),
        )
    if strip is not None:
        tier = max(10.0, dwg.draft.font_size * 3.0)
        pos = carve_free_position(dwg, strip, view, axis, tier, perp)
        if pos is not None:
            _place(sgn * (pos - witness), side)
            return

    # Fallback: diagonal side / absent strip / full strip. This cannot cleanly occupy an
    # axis-aligned strip tier, so search bounded offsets along the chosen outward vector and
    # test the full generated dimension footprint against this view's placed obstacles (#514).
    # Rotated grids can need the two perpendicular pitch dims on opposite sides, so try the
    # preferred side first and then its opposite before declaring the row genuinely full.
    step = max(2.5, dwg.draft.font_size)
    limit = math.hypot(a.PAGE_W, a.PAGE_H)
    page_box = (a.margin, a.margin, a.PAGE_W - a.margin, a.PAGE_H - a.margin)
    obstacles = strip_obstacles(dwg, view=view, crossable=CROSSABLE_TYPES)
    for side_vec, reach_i in fallback_sides:
        base = reach_i + 8
        for k in range(int(limit / step) + 1):
            offset = base + k * step
            line_x = mid[0] + side_vec[0] * (offset + 6)
            line_y = mid[1] + side_vec[1] * (offset + 6)
            if (
                line_x < page_box[0]
                or line_x > page_box[2]
                or line_y < page_box[1]
                or line_y > page_box[3]
            ):
                if (
                    (line_x < page_box[0] and side_vec[0] <= 0)
                    or (line_x > page_box[2] and side_vec[0] >= 0)
                    or (line_y < page_box[1] and side_vec[1] <= 0)
                    or (line_y > page_box[3] and side_vec[1] >= 0)
                ):
                    break
                continue
            probe = _make(offset, side_vec)
            bb = _geom_box(probe)
            if bb is None:
                continue
            if (
                bb[0] < page_box[0]
                or bb[1] < page_box[1]
                or bb[2] > page_box[2]
                or bb[3] > page_box[3]
            ):
                continue
            if _box_hits(bb, obstacles):
                continue
            dwg.add(
                _clear_and_validate(offset, side_vec, page_box, obstacles, probe),
                name,
                view=view,
                feature=feature,
            )
            return
    _log.info("Pitch dimension for the %s× %s array skipped (no room)", n, _fmt(pitch))


def build_view_of_axis(a: Analysis):
    """The ``{axis: (view_name, to_page)}`` map ``_annotate_holes`` consumes — each hole
    is annotated in the view normal to its axis; ``to_page`` projects a model-space
    location to page coords. Shared by the auto-pass (``_auto_annotate``) and the #426
    ``finalize()`` callout routing so both build it identically."""
    p = a.proj
    return {
        "z": ("plan", lambda loc: (p.plan_x(loc[0]), p.plan_y(loc[1]))),
        "y": ("front", lambda loc: (p.front_x(loc[0]), p.front_z(loc[2]))),
        "x": ("side", lambda loc: (p.side_x(loc[1]), p.side_z(loc[2]))),
    }


def _annotate_holes(
    dwg, a: Analysis, view_of_axis, groups, feature_keys, *, only=None, place_furniture=True
):
    """Leader-attached HoleCallouts, one per distinct hole spec per view (#91).

    Identical holes share one callout with an ``n×`` count prefix (#92's
    grouping half) — through holes group on diameter and steps regardless of
    wall thickness. The leader tip lands on the hole's circumference, on the
    group's hole nearest the callout.

    Placement: plan- and side-view callouts go to the right of their view
    (the strip before the iso view / page margin; plan falls back to its
    left, the side view has no usable left strip), front-view callouts go
    below the front view through the strip solver. Each callout is width-checked;
    anything that fits nowhere is logged and skipped — never force-placed — and
    then surfaces through the coverage lint as ``feature_not_dimensioned``.
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
    # Callout → source IR feature, for provenance (#408 / ADR 0010). The callout object
    # flows unchanged from here through the by_view/queue tuples to both emit sites, so an
    # id() map tags it there without threading a feature through the placement machinery.
    _feat_of_callout: dict[int, object] = {}
    for g in groups:
        feat = g.feature
        if not isinstance(feat, HoleFeature | PatternFeature):
            continue
        if only is not None and feat not in only:  # #426 finalize: recorded callout subset
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
        _feat_of_callout[id(callout)] = feat  # provenance (#408)
        by_view.setdefault(view, []).append((locs, dia, callout, pat))

    def _rim_tip(centre, elbow, dia):
        """Pull the tip from the hole centre to its circumference (bore *dia* mm)."""
        r = dia * a.SCALE / 2
        dx, dy = elbow[0] - centre[0], elbow[1] - centre[1]
        norm = math.hypot(dx, dy)
        if norm <= r:
            return centre
        return (centre[0] + dx / norm * r, centre[1] + dy / norm * r)

    def _hc_name(view, i):
        # The auto-pass (only is None) numbers callouts positionally hc_{view}{i} — the
        # historical byte-identical scheme. The #426 finalize path (only set) may run after
        # a prior batch already placed hc_ names on this view, so it allocates the first FREE
        # index to avoid Drawing.add silently replacing an earlier callout (#430 review).
        if only is None:
            return f"hc_{view}{i}"
        j = 0
        while f"hc_{view}{j}" in _hc_used:
            j += 1
        name = f"hc_{view}{j}"
        _hc_used.add(name)
        return name

    _hc_used = set(dwg._named)

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
            _hc_name(view, i),
            view=view,
            feature=_feat_of_callout.get(id(callout)),
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
            # Below the view, vertical shafts. Rows are solved as one strip batch rather
            # than assigned by `i * min_gap` (#513). Candidate order stays right-to-left
            # so inner-to-outer rows preserve the historical crossing guard shape, but
            # over-capacity is now priority-ranked by bore diameter.
            specs.sort(key=lambda s: max(to_page(loc)[0] for loc in s[0]), reverse=True)
            cands = []
            features = {}
            priorities = {}
            forbid = {}
            furniture = {}
            meta = {}
            tb_box = (tb_left, _TB_CLEAR, a.PAGE_W - _TB_CLEAR, tb_top)
            for i, (locs, dia, callout, feat) in enumerate(specs):
                w = callout.callout_width
                centre = to_page(max(locs, key=lambda loc: to_page(loc)[0]))
                if centre[0] + gap + w <= a.PAGE_W - a.margin:
                    side = "right"
                elif centre[0] - gap - w >= a.margin:
                    side = "left"
                else:
                    _log.info("Hole callout ø%s skipped (no room)", _fmt(dia))
                    _record_callout_drop(dwg, view, dia, "no room beside the view", feat)
                    continue

                name = _hc_name(view, i)

                def _build(
                    pos,
                    _centre=centre,
                    _dia=dia,
                    _side=side,
                    _callout=callout,
                ):
                    elbow = (_centre[0], pos)
                    tip = _rim_tip(_centre, elbow, _dia)
                    return Leader(
                        tip=(tip[0], tip[1], 0),
                        elbow=(elbow[0], elbow[1], 0),
                        label="",
                        draft=draft,
                        text_side=_side,
                        callout=_callout,
                    )

                cands.append((name, _build))
                features[name] = _feat_of_callout.get(id(callout))
                priorities[name] = dia
                forbid[name] = tb_box
                furniture[name] = (i, feat)
                meta[name] = (dia, feat)

            left = place_strip_candidates(
                dwg,
                a.fv_zones.below,
                "front",
                "y",
                cands,
                min_gap,
                features=features,
                priorities=priorities,
                forbid=forbid,
            )
            left_names = {name for name, _ in left}
            for name, _build in cands:
                if name in left_names:
                    dia, feat = meta[name]
                    _log.info("Hole callout ø%s skipped (front strip full)", _fmt(dia))
                    _record_callout_drop(dwg, view, dia, "front strip full", feat)
                    continue
                if place_furniture:  # #426: finalize's furniture() replay owns furniture
                    idx, feat = furniture[name]
                    _add_furniture(dwg, a, view, idx, feat, to_page)
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
        # "⌀… ↓…" text may not sit on, folded into `_place_queue`'s obstacle carve
        # (ADR 0009 Amendment 9, #381) so the spacing solve avoids them by
        # construction (retiring the old `_coaxial_lift` pre-solve nudge, and — since
        # Amendment 9 — the separate banded-DP solve). Each is `(centre, half_width)`.
        # Two causes, keyed on the crossing line, not the part's shape:
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
        band_intervals = [(c - h, c + h) for c, h in forbidden]

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

            # Natural Y is the bore's own row; keep-out-band avoidance is now
            # `_place_queue`'s carve (`band_intervals`, below), not a pre-solve lift.
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

            # Carve [y_min, y_max] around a set of keep-out intervals, assign each
            # candidate to its nearest free segment, then solve each segment
            # independently with the plain PAVA solve — the ONE carve+assign+solve
            # mechanism, reused below both for the bands-only baseline and for
            # bands+drawing-obstacles together (ADR 0009 Amendment 9, #381). This
            # retires the separate banded-DP solve: an anchored candidate assigned
            # to its own band-free segment never needs cross-segment reasoning to
            # stay off a reserved row. *intervals* must already carry their own
            # clearance (pre-inflated) — this carves with `pad=0`.
            def _carve_and_place(cands_in, intervals, key_prefix_local, *, allow_snap=True):
                segs = carve_free_segments(y_min, y_max, intervals, 0.0)
                if not segs:
                    if not allow_snap:
                        # bands+obstacles combined leave no free segment — unlike a
                        # band-only strip (below), this has no single-pass escape that
                        # is safe to trust: a snap chosen to clear ONE blocking
                        # interval isn't rechecked against the others, so it can still
                        # land inside a different one (a real drawing-level obstacle,
                        # e.g. the section cutting-plane letter, unlike a band, is
                        # exactly the kind of thing that produces a visible overlap,
                        # not just a theoretical one). Returning nothing here (instead
                        # of a wrong position) defers every candidate cleanly to the
                        # bands-only baseline below, matching what a fully-obstacle-
                        # blocked carve already did before this helper existed.
                        return {}, set()

                    # *intervals* covers [y_min, y_max] entirely — a band wider than
                    # the whole strip (a shallow view, e.g. the `dshape` side strip).
                    # Rather than drop a real callout to honour it (policy B), snap
                    # each natural to the strip edge farthest from the row (the same
                    # minimal-residual choice the old `_coaxial_lift`/
                    # `_snap_out_of_bands` fallback made) and solve once over the
                    # whole, still-unavoidable range.
                    def _snap(ny):
                        p = ny
                        for a0, b0 in intervals:
                            if a0 < p < b0:
                                p = b0 if (y_max - ny) >= (ny - y_min) else a0
                        return min(max(p, y_min), y_max)

                    cands = [
                        StripCandidate(
                            key=f"{key_prefix_local}{j:04d}",
                            anchor=(edge, _snap(s[4])),
                            size=(s[2].callout_width, min_gap),
                            priority=s[1],
                            anchored=_is_central(s),
                        )
                        for j, s in enumerate(cands_in)
                    ]
                    res = plan_strip(cands, y_min, y_max, min_gap)
                    by_key = {c.key: s for c, s in zip(cands, cands_in, strict=True)}
                    return (
                        {id(by_key[k]): y for k, y in res.placed.items()},
                        {id(by_key[k]) for k in res.dropped},
                    )

                def _seg_order(ny):
                    """Segments nearest-to-farthest from *ny* — the containing
                    segment (if any) first, else by edge distance; ties (equidistant
                    between two segments) break on segment start for determinism."""

                    def _dist(seg):
                        lo, hi = seg
                        return (
                            (0.0, lo) if lo <= ny <= hi else (min(abs(ny - lo), abs(ny - hi)), lo)
                        )

                    return sorted(segs, key=_dist)

                def _cand(s, j):
                    return StripCandidate(
                        key=f"{key_prefix_local}{j:04d}",
                        anchor=(edge, s[4]),
                        size=(s[2].callout_width, min_gap),
                        priority=s[1],  # bore diameter — largest wins over-capacity (D3)
                        anchored=_is_central(s),
                    )

                # Selection (ADR 0009 P2) must be GLOBAL across every carved segment,
                # not per-segment — a candidate that overflows its nearest segment may
                # still fit a farther one with spare room (#381: the retired banded-DP
                # tried this but approximated feasibility by placed-count alone, which
                # lost track of *where* things were placed; this instead re-runs the
                # real per-segment solve on each trial, so it can't reintroduce that
                # bug). Process candidates highest-priority-first so a segment already
                # holding only >= priority members can, on overflow, only ever be
                # asked to drop the newcomer being tried — never evict a prior
                # commitment — which is exactly what "trial has zero drops" verifies
                # below: on a rare priority TIE it's conservative (rejects rather than
                # risking evicting an existing member), never wrong.
                order = sorted(cands_in, key=lambda s: (-s[1], s[4]))
                members: dict = {seg: [] for seg in segs}
                dropped_ids: set = set()
                for s in order:
                    for seg in _seg_order(s[4]):
                        trial = members[seg] + [s]
                        cands = [_cand(m, j) for j, m in enumerate(trial)]
                        res = plan_strip(cands, seg[0], seg[1], min_gap)
                        if not res.dropped:
                            members[seg] = trial
                            break
                    else:
                        dropped_ids.add(id(s))

                y_by_id: dict = {}
                for seg, mem in members.items():
                    if not mem:
                        continue
                    cands = [_cand(m, j) for j, m in enumerate(mem)]
                    res = plan_strip(cands, seg[0], seg[1], min_gap)
                    by_key = {c.key: m for c, m in zip(cands, mem, strict=True)}
                    for k, y in res.placed.items():
                        y_by_id[id(by_key[k])] = y
                    dropped_ids.update(id(by_key[k]) for k in res.dropped)
                return y_by_id, dropped_ids

            # Baseline: bands only, ignoring drawing-level obstacles entirely —
            # every candidate pulled toward its own natural Y, respecting only
            # min_gap from its queue siblings and the keep-out rows. Used below
            # as the "cost of NOT avoiding [obstacles]" reference a carve-based
            # relocation must beat to be worth taking.
            base_y, base_dropped = _carve_and_place(queue, band_intervals, key_prefix)

            # Carve around drawing-level obstacles this column's leaders would
            # cross too (e.g. the section cutting-plane arrow — #351 P5 strand
            # 3): a Y-only solve can't see an obstacle it never measures, the
            # textbook invisible-occupant defect. Probed at each candidate's OWN
            # natural Y, not a shared reference — a callout's leader shaft is
            # position-dependent geometry (it runs from the fixed hole location
            # to the elbow), so probing everyone at one far-away Y badly
            # misjudges it.
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
            # Obstacles get their own min_gap clearance, pre-inflated here (same
            # amount the old dedicated carve applied); bands already carry their
            # clearance in `band_intervals`'s half-width. Combining both into one
            # carve call needs each pre-inflated by its own amount, not a single
            # shared pad, so `_carve_and_place` above always carves with `pad=0`.
            obstacle_intervals = [
                (max(y_min, o[1] - min_gap), min(y_max, o[3] + min_gap)) for o in occupied
            ]
            seg_y, _seg_dropped = _carve_and_place(
                queue, band_intervals + obstacle_intervals, key_prefix, allow_snap=False
            )

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
                name = _hc_name(view, i)
                dwg.add(leader, name, view=view, feature=_feat_of_callout.get(id(callout)))
                # A plain (unpatterned) plan callout is a scattered-hole-table candidate
                # (#351): record its coverage against the ACTUAL placed name, regardless of
                # place_furniture, so finalize (place_furniture=False) still lets
                # _maybe_tabulate_holes find + replace it (#426 Ph4c). Coverage-only, so the
                # auto-pass (place_furniture=True) set is unchanged → byte-identical.
                if view == "plan" and feat is None:
                    dwg._cover_scattered_hole_doc(name)
                if place_furniture:  # #426: finalize's furniture() replay owns furniture
                    _add_furniture(dwg, a, view, i, feat, to_page)
                i += 1
            return i

        next_i = _place_queue(right_queue, edge_right, "right", "hc_r", 0)
        assert edge_left is not None or not left_queue  # populated only when edge_left is set
        _place_queue(left_queue, edge_left, "left", "hc_l", next_i)
