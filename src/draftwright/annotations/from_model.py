"""from_model — render planner output into placed annotations (ADR 0008).

The renderer back-end of the compiler: a `DimensionGroup` (read *purely from its
planned parameters* + the feature's metadata) becomes placed `HoleCallout` /
`Dimension` annotations via the existing projection (`Drawing.at`), layout search,
and rendering primitives. GD&T symbols (⌴/↧) are the helper's geometry, which is
exactly why the IR carries semantic `role`s, not glyph strings.

This lives in `annotations/` (not `model/`) so the IR package stays pure — it
imports *down* into `model` + `_core`, and is called by the orchestrator (ADR 0008
Amendment 3: one path, this is its render stage). Judged by **correctness** (lint),
not equivalence to the engine. All renderers here (`render_diameters`/`render_envelope`/
`render_locations`/`render_centermarks`/`render_step_lengths`/`render_slots`, and the
shared `hole_callout_spec`/`callout_from_spec` consumed by the holes pass) are wired
into production — the test-only `render_into`/`render_callouts` parallel was retired
once the holes epic landed (#251).
"""

from __future__ import annotations

import math
from typing import Any

from build123d_drafting import DatumFeature, FeatureControlFrame, SurfaceFinish, TextBlock
from build123d_drafting.helpers import (
    DEFAULT_FONT_PATH,
    Centerline,
    CenterMark,
    HoleCallout,
    Leader,
    TitleBlock,
)

from draftwright._core import (
    _DIAM_RE,
    _END_ON,
    _EST_CHAR_WIDTH_EM,
    _MARGIN,
    _MIN_STEP_SEP_MM,
    _SLOT_DIM_DEPTH,
    _SLOT_DIM_HEIGHT,
    _SLOT_DIM_STEP,
    _SLOT_DIM_WIDTH,
    _WITNESS_LIFT_MM,
    DetailRequest,
    _concentric_with_axis,
    _dim,
    _first_free_index,
    _fmt,
    _greedy_strip_ys,
    _iso_bbox,
    _legible_locations,
    _legible_steps,
    _log,
    _solve_strip_ys,
    _text_size,
    _title_block_box,
    _tol_suffix,
)
from draftwright.annotations._common import (
    CROSSABLE_TYPES,
    CorridorCandidate,
    Escalation,
    _anno_box,
    _box_hits,
    _geom_box,
    carve_free_position,
    carve_free_segments,
    dim_footprint,
    place_strip_candidates,
    register_corridor,
    strip_free_span,
    strip_obstacles,
)
from draftwright.layout import StripCandidate, plan_strip
from draftwright.model.ir import AUTHORED_DIMENSION_KINDS, HoleFeature, PatternFeature
from draftwright.model.planner import DimensionGroup, plan_locations


def _first(group: DimensionGroup, kind: str, *roles: str) -> float | None:
    """First parameter value matching *kind* and any of *roles*, in role order."""
    for role in roles:
        for pd in group.dims:
            if pd.param.kind == kind and pd.param.role == role:
                return pd.param.value
    return None


def hole_callout_spec(group: DimensionGroup) -> dict | None:
    """A hole/pattern group's plan → `HoleCallout` kwargs, mirroring the engine's
    convention. ``None`` if not a hole-bearing callout.

    From the plan: bore from `DimParameter` roles; the cbore/spotface *step* with
    counterbore precedence (``step = cbore or spotface``, as the engine does);
    ``through`` inferred from the absence of a bore-depth param; ``count`` and the
    pattern *suffix* (``EQ SP ON ø50 BC`` / ``(3×3)``) from the source feature."""
    feat = group.feature
    if not isinstance(feat, HoleFeature | PatternFeature):
        return None
    bore = _first(group, "diameter", "bore")
    if bore is None:
        return None
    bore_tol = next(
        (
            pd.param.tolerance
            for pd in group.dims
            if pd.param.kind == "diameter" and pd.param.role == "bore"
        ),
        None,
    )
    depth = _first(group, "depth", "bore")
    count = feat.count
    suffix = None
    if isinstance(feat, PatternFeature):
        if feat.pattern == "bolt_circle" and feat.bcd is not None:
            suffix = f"EQ SP ON ø{_fmt(feat.bcd)} BC"
        elif feat.pattern == "grid" and feat.rows and feat.cols:
            suffix = f"({feat.rows}×{feat.cols})"
    return {
        "diameter": bore,
        "count": count if count and count > 1 else None,
        "through": depth is None,
        "depth": depth,
        # counterbore precedence, spotface fallback — the engine's mapping
        "cbore_dia": _first(group, "diameter", "counterbore", "spotface"),
        "cbore_depth": _first(group, "depth", "counterbore", "spotface"),
        "csink_dia": _first(group, "diameter", "countersink"),
        "csink_angle": _first(group, "angle", "countersink"),
        "suffix": suffix,
        "tolerance": bore_tol,  # P2a: ± on the bore ⌀, baked into the callout string below
    }


def callout_from_spec(spec, draft, count) -> HoleCallout | None:
    """Build a `HoleCallout` from a :func:`hole_callout_spec` dict. *count* is passed
    explicitly (the bare/test path uses the spec's own count; the engine pass uses its
    view-local hole count) — the single callout builder both the IR and the migrating
    engine pass share, so the bore/cbore/suffix mapping lives in one place (#238 B1).

    **This is the only place draftwright constructs a `HoleCallout`** — and it MUST
    pass each numeric value as a `_fmt` string, never a raw float: `HoleCallout`
    renders a float (``ø8.0``) wider than the equivalent string (``ø8``), which
    shifts placement and can drop callouts (#261). Keep the formatting here; the IR
    carries clean floats (no baked labels). The robust fix — `HoleCallout` formatting
    its own numeric inputs — is upstream in build123d-drafting-helpers."""
    if spec is None:
        return None

    def f(v):  # see the #261 note above — every value crosses as a formatted string
        return _fmt(v) if v is not None else None

    dia = f(spec["diameter"])
    if dia is not None:
        # P2a: bake the ± tolerance into the bore string (helpers' HoleCallout accepts a
        # diameter carrying tolerance/fit text, "8 ±0.05"); no tolerance → empty suffix.
        dia += _tol_suffix(spec.get("tolerance"), draft)

    return HoleCallout(
        dia,
        count=count,
        through=spec["through"],
        depth=f(spec["depth"]),
        cbore_dia=f(spec["cbore_dia"]),
        cbore_depth=f(spec["cbore_depth"]),
        csink_dia=f(spec.get("csink_dia")),
        # Every value crosses as a _fmt string (the #261 invariant) — a raw float renders
        # "90.0°" and, worse, mismatches the width estimators' `_fmt` (they'd under-reserve).
        # `.get()`: hand-built specs (tolerance/fit tests) omit csk keys.
        csink_angle=f(spec.get("csink_angle")),
        suffix=spec["suffix"],
        draft=draft,
    )


def _record_slot_drop(ctx, dwg, kind, idx, view, feat):
    """Record a slot dim the layout could not place (#135).

    Info severity — a dim with no clear room is dropped as "place what fits",
    not an error. Alongside the lint code, appends a first-class ``Escalation``
    (ADR 0009 Amdt 1, #351 PR-4a) so the drop is object-visible too; slots have
    no natural grouping remedy like a recognised hole pattern, so no resolver
    consumes this yet — purely additive.
    """
    ctx.record_issue(
        "info",
        "slot_dim_dropped",
        f"slot{idx} {kind} dim not placed (no room beside the {view})",
    )
    ctx.escalations.append(
        Escalation(kind="slot", view=view, feature=feat, reason=f"no room beside the {view}")
    )


def render_slots(dwg, model, a, *, ctx, only=None) -> int:
    """Dimension milled slots from the IR — width (the defining size, across
    ``width_axis``) + length (along ``long_axis``) + a position dim from the part
    datum, in the view the two axes span. Places through the engine's zone strips
    (shared infra, ADR 0008 Amend. 4); a dim with no clear room is dropped and
    recorded at info severity (place-what-fits). Sources `SlotFeature`s from the
    model; replaces the engine's `_annotate_slots`. Returns the count placed.

    ``only`` (a set of `SlotFeature`s, #426 Phase 2b) restricts placement to a recorded
    subset for ``finalize()``; ``only=None`` (the auto-pass) places all slots, byte-
    identically. Skips filtered slots **in place** so ``i`` stays the slot's model index
    (the ``m_slot{i}_*`` names must match the auto-pass — never re-enumerate a compacted
    list)."""
    slots = [f for f in model.features if f.kind == "slot"]
    if not slots:
        return 0
    draft = dwg.draft
    tier = draft.font_size + 2 * draft.pad_around_text
    views = {
        frozenset("xy"): ("plan", a.pv_zones, "x", a.proj.plan_x, "y", a.proj.plan_y),
        frozenset("xz"): ("front", a.fv_zones, "x", a.proj.front_x, "z", a.proj.front_z),
        frozenset("yz"): ("side", a.sv_zones, "y", a.proj.side_x, "z", a.proj.side_z),
    }

    def _bb(axis, hi):
        return getattr(a.bb.max if hi else a.bb.min, axis.upper())

    count = 0
    for i, s in enumerate(slots):
        if only is not None and s not in only:
            continue  # #426 Ph2b: skip in place — i must stay the model index
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
            # Raw (pre-snap) endpoints — the dedup key must share a basis with the
            # hole-location key (which uses the raw ref), else the ~0.05 mm snap gap can
            # push a coincident span into an adjacent 0.1 mm page bin and the #345
            # duplicate survives.
            raw_lo, raw_hi = p_lo, p_hi
            # Snap the geometric span to the displayed (1-dp) value so drawn length
            # matches the label (else label-vs-measured lint trips).
            disp = float(_fmt(label))
            sgn = 1.0 if p_hi >= p_lo else -1.0
            if anchor == "center":
                mid = (p_lo + p_hi) / 2
                p_lo, p_hi = mid - sgn * disp / 2, mid + sgn * disp / 2
            else:
                p_hi = p_lo + sgn * disp
            if meas_axis == ha:
                meas_proj, perp_proj = hp, vp
                sides = (("above", zn.above, True), ("below", zn.below, False))
            else:
                meas_proj, perp_proj = vp, hp
                sides = (("right", zn.right, True), ("left", zn.left, False))
            cname = f"m_slot{idx}_{kind}"

            def _cand_for(side, hi):
                # (name, build) for one side; witness is off the slot's own edge (the near
                # edge for the far side, the far edge for the near side).
                witness = perp_proj(perp_hi if hi else perp_lo)
                if side in ("above", "below"):
                    e_lo, e_hi = (meas_proj(p_lo), witness, 0), (meas_proj(p_hi), witness, 0)
                else:
                    e_lo, e_hi = (witness, meas_proj(p_lo), 0), (witness, meas_proj(p_hi), 0)
                return (
                    cname,
                    lambda pos, _el=e_lo, _eh=e_hi, _s=side, _w=witness: _dim(
                        _el, _eh, _s, abs(pos - _w), draft, label=_fmt(label)
                    ),
                )

            # Unified above corridor (ADR 0009 end state, #345/#346): a plan/side slot dim
            # measured along the horizontal axis shares the SAME strip as the hole-location
            # ladder, so it registers into the corridor batch instead of committing here.
            # One solve then dedups a slot POSITION line coincident with a hole location
            # (#345) and orders size + location as segregated, monotonic runs (#346). The
            # on_drop falls through to the below strip (place-what-fits) before recording a
            # genuine drop; a *deduped* position fires no drop (it was never starved).
            if meas_axis == ha and vw[0] in ("plan", "side"):
                is_pos = kind == "pos"
                drop_word = "position" if is_pos else kind
                _, below_strip, below_hi = sides[1]

                def _below_or_drop(nm, _bs=below_strip, _bh=below_hi, _feat=s, _dw=drop_word):
                    if _bs is not None and not place_strip_candidates(
                        dwg,
                        _bs,
                        vw[0],
                        "y",
                        [_cand_for("below", _bh)],
                        tier,
                        features={cname: _feat},
                    ):
                        return  # placed on the below strip
                    _record_slot_drop(ctx, dwg, _dw, idx, vw[0], _feat)

                register_corridor(
                    ctx,
                    (vw[0], "above"),
                    zn.above,
                    vw[0],
                    "y",
                    tier,
                    CorridorCandidate(
                        name=cname,
                        build=_cand_for("above", sides[0][2])[1],
                        # A position nests in the datum-distance location ladder; a size dim
                        # forms the inner run, ordered left-to-right by its span midpoint.
                        order=(
                            (_LOC_SUBCHAIN, disp, cname)
                            if is_pos
                            else (_SIZE_SUBCHAIN, (p_lo + p_hi) / 2, cname)
                        ),
                        on_place=lambda nm: None,
                        on_drop=_below_or_drop,
                        dedup=(
                            (vw[0], round(meas_proj(raw_lo), 1), round(meas_proj(raw_hi), 1))
                            if is_pos
                            else None
                        ),
                        precedence=1 if is_pos else 0,
                        force=False,
                        feature=s,  # provenance (ADR 0010): this dim belongs to the slot
                    ),
                )
                return True  # deferred — the callback owns the drop; caller's else must not fire

            # Immediate path: right/left dims, and any front-view above/below. Try each side
            # through the shared carve; the first that takes the dim wins, else the caller drops.
            for side, strip, hi in sides:
                if strip is None:
                    continue
                axis = "y" if side in ("above", "below") else "x"
                if not place_strip_candidates(
                    dwg, strip, vw[0], axis, [_cand_for(side, hi)], tier, features={cname: s}
                ):
                    return True
            return False

        half = s.width / 2
        if _place(
            s.width_axis, s.w_center - half, s.w_center + half, s.lo, s.hi, s.width, "width"
        ):
            count += 1
        else:
            _record_slot_drop(ctx, dwg, "width", i, name, s)
        if _place(
            s.long_axis, s.lo, s.hi, s.w_center - half, s.w_center + half, s.length, "length"
        ):
            count += 1
        else:
            _record_slot_drop(ctx, dwg, "length", i, name, s)
        datum = _bb(s.long_axis, False)
        if (s.lo - datum) * a.SCALE >= 1.0:
            if _place(
                s.long_axis,
                datum,
                s.lo,
                s.w_center - half,
                s.w_center + half,
                s.lo - datum,
                "pos",
                anchor="lo",
            ):
                count += 1
            else:
                _record_slot_drop(ctx, dwg, "position", i, name, s)
    return count


# Corridor-ladder ordering (ADR 0009 end state, #346): feature-SIZE dims sit nearer the
# view (inner run), datum-referenced LOCATION dims stack outward (a single ascending chain
# by datum distance). Segregating the two runs keeps a slot length from landing mid-ladder.
_SIZE_SUBCHAIN = 0
_LOC_SUBCHAIN = 1
_OVERALL_SUBCHAIN = 2
_MANDATORY_OVERALL_PRIORITY = 100.0

# Minimum half of a bore's PAGE-projected diameter (mm) for its ø dim to fit across the circle
# in-plane; below this the circle is too small to letter inside, so the callout leaders out
# instead. Applied per bore-axis view (plan/side/front).
_MIN_INPLACE_BORE_HALF_MM = 4.0


def _location_candidate(
    dwg,
    ctx,
    name,
    *,
    view,
    span_key,
    distance,
    build,
    feature=None,
    pinned=False,
    footprint=None,
):
    """A :class:`CorridorCandidate` for a datum-referenced hole/pattern location dim.
    Location dims outrank a coincident slot-position line in dedup (#345) and form the
    outer, datum-distance-ordered run of the ladder (#346). Force-kept (policy B): a plan-X
    / side-Y location has no alternate view, so a corridor block keeps it rather than drops
    it; only a physically full strip drops (``location_ref_dropped`` → hole-table escalate)."""

    def _placed(nm):
        ctx.coverage.cover_scattered_hole_doc(nm)
        if pinned:
            dwg.pin(nm)

    def _drop(nm):
        edge = "plan view" if view == "plan" else "side view"
        ctx.record_issue(
            "warning", "location_ref_dropped", f"{nm} not placed (no room above the {edge})"
        )
        ctx.escalations.append(Escalation("location", view, nm, "strip_full"))

    return CorridorCandidate(
        name=name,
        build=build,
        order=(_LOC_SUBCHAIN, distance, name),
        # A placed location may later be replaced by the scattered-hole table (#351 PR-4c).
        on_place=_placed,
        on_drop=_drop,
        dedup=(view, span_key[0], span_key[1]),
        precedence=3 if pinned else 2,
        priority=100.0 if pinned else 0.0,
        force=True,
        feature=feature,  # provenance (ADR 0010): the located hole/pattern
        footprint=footprint,  # analytical measure — no probe build (#602)
    )


def render_locations(dwg, model, a, *, ctx, only=None, pinned=None) -> int:
    """Baseline X/Y hole-location dims from the IR (#238). The planner decides the
    intent (`plan_locations`: which refs, from which datum); this renderer owns the
    layout (Amendment 4) — X dims tier above the plan view, Y dims above the side
    view, nearest-datum-first, legibility-gated, allocated from the existing strips;
    a ref with no room is dropped as `location_ref_dropped`. Replaces the engine's
    `_add_location_dims`. Returns the count placed.

    *only*, when given, restricts placement to refs whose source feature is in the set —
    the #426 finalize() path passes the recorded ``locate`` intents' features so the
    corridor solve runs over the user's edited subset. ``None`` (the auto-pass) places
    every ref, byte-identically.

    *pinned* carries the #511 first slice: deferred user ``locate(..., pin=True)`` calls
    remain first-class corridor candidates, but get higher survival/dedup priority and
    pin their placed names instead of being hand-added after the solve."""
    planned = plan_locations(model)
    if not planned:
        return 0
    draft = dwg.draft
    datum = planned[0].datum
    assert datum is not None  # plan_locations always sets the datum
    datum_x, datum_y = datum.at[0], datum.at[1]
    refs = []
    for pd in planned:
        if only is not None and pd.feature not in only:  # #426: recorded subset only
            continue
        if pd.param.span is None:
            continue
        rx, ry = pd.param.span[1][0], pd.param.span[1][1]
        # A rotational part's on-axis (concentric) *hole* bore is located by the
        # centreline, not a position dim (matches the engine's feature_holes
        # filter). A pattern ref (role "location_pattern" — e.g. a bolt-circle
        # centre) is NOT filtered, even on the axis.
        if pd.param.role == "location" and a.is_rotational and _concentric_with_axis(a, rx, ry):
            continue
        refs.append((rx, ry, pd.feature))  # carry the source feature for provenance (ADR 0010)
    if not refs:
        return 0
    pinned_set = set(pinned or ())
    tier = draft.font_size + 2 * draft.pad_around_text
    n = 0

    # Location-dim names. The auto-pass (only is None) numbers them positionally —
    # m_locx{i}, the historical byte-identical scheme. The finalize() path (only set) may
    # run AFTER live-replayed locate() dims already hold m_loc names, so there it allocates
    # the first FREE index to avoid Drawing.add silently replacing one (#429 review).
    _loc_used = set(ctx.registry.names()) if only is not None else None

    def _loc_name(prefix: str, i: int) -> str:
        if _loc_used is None:
            return f"{prefix}{i}"  # auto-pass: unchanged, byte-identical
        name = f"{prefix}{_first_free_index(prefix, _loc_used)}"
        _loc_used.add(name)
        return name

    # --- X locations: tier above the plan view ---
    PX, PY = a.proj.plan_x, a.proj.plan_y
    x_refs: list = []
    for r in refs:
        for u in x_refs:
            if abs(r[0] - u[0]) < 0.5:
                u[3] = u[3] or r[2] in pinned_set
                break
        else:
            x_refs.append([r[0], r[1], r[2], r[2] in pinned_set])
    _x_drawable = {r[0] for r in x_refs if abs(r[0] - datum_x) * a.SCALE >= 1.0}
    _kept_x, _n_x_close = _legible_locations(_x_drawable, a.SCALE)
    if _n_x_close:
        ctx.record_issue(
            "warning",
            "location_ref_dropped",
            f"{_n_x_close} X location dim(s) too closely spaced to dimension legibly "
            "(use a detail view)",
        )
        ctx.escalations.append(Escalation("location", "plan", None, "illegible"))
    _kept_x_set = set(_kept_x)
    x_refs = [r for r in x_refs if r[0] not in _x_drawable or r[0] in _kept_x_set]
    # Register X-location dims into the shared plan-above corridor (ADR 0009 end state,
    # #345/#346): the slot pass feeds the SAME strip, so a single solve_corridor drain
    # dedups a coincident slot-position line and orders the whole ladder — instead of each
    # pass carving around the other and interleaving. No alternate view for a plan-X
    # location, so a corridor-blocked dim is force-kept (policy B), not relocated; only a
    # physically full strip drops (→ location_ref_dropped, escalates the hole table).
    for i, (rx, ry, feat, pin_ref) in enumerate(sorted(x_refs, key=lambda r: abs(r[0] - datum_x))):
        if abs(rx - datum_x) * a.SCALE < 1.0:
            continue  # on the datum edge — nothing to dimension
        n += 1
        # A single X-location dim shared by two *distinct* features at this X belongs to
        # neither exclusively — leave it unowned so drop() cannot over-strip a sibling's
        # dimension and annotations_of never over-claims it (review #406, ADR 0010).
        _xfeat = None if any(abs(o[0] - rx) < 0.5 and o[2] != feat for o in refs) else feat
        register_corridor(
            ctx,
            ("plan", "above"),
            a.pv_zones.above,
            "plan",
            "y",
            tier,
            _location_candidate(
                dwg,
                ctx,
                _loc_name("m_locx", i),
                view="plan",
                span_key=(round(PX(datum_x), 1), round(PX(rx), 1)),
                distance=abs(rx - datum_x),
                build=lambda pos, _rx=rx, _ry=ry: _dim(
                    (PX(datum_x), PY(_ry), 0),
                    (PX(_rx), PY(_ry), 0),
                    "above",
                    pos - PY(_ry),
                    draft,
                    label=_fmt(_rx - datum_x),
                ),
                feature=_xfeat,
                pinned=pin_ref,
                footprint=lambda pos, _rx=rx, _ry=ry: dim_footprint(
                    (PX(datum_x), PY(_ry), 0),
                    (PX(_rx), PY(_ry), 0),
                    "above",
                    pos - PY(_ry),
                    draft,
                    _fmt(_rx - datum_x),
                ),
            ),
        )

    # --- Y locations: tier above the side view (which maps world-Y horizontally) ---
    SX, SZ = a.proj.side_x, a.proj.side_z
    side_top = SZ(a.bb.max.Z)
    iso_x0, iso_y0, _, _ = _iso_bbox(dwg)
    y_refs: list = []
    for r in refs:
        for u in y_refs:
            if abs(r[1] - u[1]) < 0.5:
                u[3] = u[3] or r[2] in pinned_set
                break
        else:
            y_refs.append([r[0], r[1], r[2], r[2] in pinned_set])
    _y_drawable = {r[1] for r in y_refs if abs(r[1] - datum_y) * a.SCALE >= 1.0}
    _kept_y, _n_y_close = _legible_locations(_y_drawable, a.SCALE)
    if _n_y_close:
        ctx.record_issue(
            "warning",
            "location_ref_dropped",
            f"{_n_y_close} Y location dim(s) too closely spaced to dimension legibly "
            "(use a detail view)",
        )
        ctx.escalations.append(Escalation("location", "side", None, "illegible"))
    _kept_y_set = set(_kept_y)
    y_refs = [r for r in y_refs if r[1] not in _y_drawable or r[1] in _kept_y_set]
    # Cap the side-above strip below the iso view so Y-location dims never run under it
    # (the carve respects outer_limit); the dim_pitch_side dims are obstacles the carve
    # avoids structurally, retiring the old manual allocate(10.0) reservation + cursor.
    if y_refs and any(SX(ry) + 10 > iso_x0 - 4 for _, ry, _feat, _pin in y_refs):
        a.sv_zones.above.outer_limit = min(a.sv_zones.above.outer_limit, iso_y0 - 4)
    for i, (rx, ry, feat, pin_ref) in enumerate(sorted(y_refs, key=lambda r: abs(r[1] - datum_y))):
        if abs(ry - datum_y) * a.SCALE < 1.0:
            continue
        n += 1
        # Shared-Y location dim → unowned (see the X loop; review #406).
        _yfeat = None if any(abs(o[1] - ry) < 0.5 and o[2] != feat for o in refs) else feat
        register_corridor(
            ctx,
            ("side", "above"),
            a.sv_zones.above,
            "side",
            "y",
            tier,
            _location_candidate(
                dwg,
                ctx,
                _loc_name("m_locy", i),
                view="side",
                span_key=(round(SX(datum_y), 1), round(SX(ry), 1)),
                distance=abs(ry - datum_y),
                build=lambda pos, _ry=ry: _dim(
                    (SX(datum_y), SZ(a.bb.max.Z), 0),
                    (SX(_ry), SZ(a.bb.max.Z), 0),
                    "above",
                    pos - side_top,
                    draft,
                    label=_fmt(_ry - datum_y),
                ),
                feature=_yfeat,
                pinned=pin_ref,
                footprint=lambda pos, _ry=ry: dim_footprint(
                    (SX(datum_y), SZ(a.bb.max.Z), 0),
                    (SX(_ry), SZ(a.bb.max.Z), 0),
                    "above",
                    pos - side_top,
                    draft,
                    _fmt(_ry - datum_y),
                ),
            ),
        )
    return n


def render_centermarks(dwg, groups) -> int:
    """A centre mark on every hole (plain holes + each pattern member), in the view
    normal to the hole's axis (`_END_ON`), sized by its diameter — the IR migration
    of the engine's inline centre-mark loop. Returns the count placed."""
    n = 0
    for g in groups:
        feat = g.feature
        if not isinstance(feat, HoleFeature | PatternFeature):
            continue
        dia = _first(g, "diameter", "bore") or 0.0
        size = max(2.5, dia * dwg.scale + 2.0)
        view = _END_ON.get(feat.frame.axis, "plan")
        members = feat.members or (g.anchor,)
        for loc in members:
            px, py, *_ = dwg.at(view, *loc)
            dwg.add(CenterMark((px, py, 0), size, dwg.draft), f"m_cm{n}", view=view, feature=feat)
            n += 1
    return n


def _mentioned_diameters(dwg) -> set[float]:
    """Diameters already called out on the drawing (ø-labels + ``covers_diameters``)
    — so a diameter another annotation already documents is not repeated."""
    diams: set[float] = set()
    for _, ann in dwg.iter_annotations():
        if isinstance(ann, TitleBlock):
            continue
        for m in _DIAM_RE.finditer(getattr(ann, "label", None) or ""):
            diams.add(float(m.group(1)))
        for v in getattr(ann, "covers_diameters", ()):
            diams.add(float(v))
    return diams


def _place_what_fits(specs, axis: int, min_gap: float, lo: float, hi: float):
    """Fit as many ø specs as the strip ``[lo, hi]`` holds at ``min_gap`` spacing,
    dropping the SMALLEST-diameter spec first when the full set overflows — so the
    significant ODs survive and only the finest bands fall to ``feature_not_dimensioned``,
    never the whole row/column (#298). ``specs`` = ``[(tip, dia, label, feat), ...]``;
    ``axis`` selects the strip coordinate of ``tip`` (0 = page-x for the row-below, 1 =
    page-y for the column-left). Returns ``(survivors_in_strip_order, positions)`` —
    ``([], [])`` if not even one fits. A part whose full row already fits keeps every
    spec in strip order, so existing output is unchanged."""
    survivors = sorted(specs, key=lambda s: s[0][axis])
    while survivors:
        naturals = [s[0][axis] for s in survivors]
        pos = _solve_strip_ys(naturals, min_gap, lo, hi) or _greedy_strip_ys(
            naturals, min_gap, lo, hi
        )
        if pos is not None:
            return survivors, pos
        drop = min(range(len(survivors)), key=lambda i: survivors[i][1])
        survivors.pop(drop)
    return [], []


def _diameter_row_below(dwg, items, start: int = 0) -> int:
    """ø-callout row BELOW the front view for X-turned step/boss diameters (#77).
    *items* is ``[(anchor, diameter), ...]``. The row is dropped clear of anything
    already below the profile; labels spread along page-x by the ADR-0003 strip
    solve. Skips (returns 0) if there is no room — the diameters then surface as
    ``feature_not_dimensioned``."""
    if not items:
        return 0
    draft = dwg.draft
    fx0, fy0, fx1, _ = dwg.view_bounds("front")
    obstacle_bottom = fy0
    for o in dwg.items:
        try:
            ob = o.bounding_box()
        except Exception:  # noqa: BLE001 — not every annotation bbox-es cleanly
            continue
        if ob.min.Y < fy0 and ob.max.X > fx0 and ob.min.X < fx1:
            obstacle_bottom = min(obstacle_bottom, ob.min.Y)
    label_y = obstacle_bottom - (draft.font_size + 4 * draft.pad_around_text)
    if label_y < _MARGIN + draft.font_size:
        return 0
    specs = []  # (tip_page, dia, label, feature), tip on the step's bottom silhouette
    for anchor, dia, feat, dtol in items:
        ax, ay, az = anchor
        tip = dwg.at("front", ax, ay, az - dia / 2)
        specs.append((tip, dia, f"ø{_fmt(dia)}{_tol_suffix(dtol, draft)}", feat))
    # Real measured width, not the per-char estimate: helpers >=0.14 label boxes are
    # honest about the rendered string, so an underestimated min_gap here surfaces as a
    # visible annotation_overlap between adjacent labels (hypothesis tier).
    half_w = (
        max(
            _text_size(
                label,
                draft.font_size,
                getattr(draft, "font_path", DEFAULT_FONT_PATH),
                getattr(draft, "font", "Arial"),
            )[0]
            for _, _, label, _ in specs
        )
        / 2
    )
    min_gap = 2 * half_w + 2 * draft.pad_around_text
    # Place what fits; drop the smallest ø first, never the whole row (#298).
    survivors, xs = _place_what_fits(specs, 0, min_gap, fx0 + half_w, fx1 - half_w)
    # A leader whose solved elbow lands LEFT of its tip flips its shelf (helpers'
    # direction rule), extending the label LEFTWARD — the min_gap model assumes
    # rightward labels, so a crowd-shifted elbow can land its flipped label on the
    # previous one. A flip alone is fine (a lone edge leader flips harmlessly); only
    # when direction-aware label intervals actually collide, enforce elbow ≥ tip with
    # a left-to-right min_gap cascade; overflow drops the smallest ø (#298) and
    # re-solves. No-op for ordinary rows.
    _SHELF = 2.0  # helpers Leader: the label hangs one shelf-length off the elbow

    def _label_ivals(svs, positions):
        out = []
        for sp, lx in zip(svs, positions, strict=True):
            w_i = _text_size(
                sp[2],
                draft.font_size,
                getattr(draft, "font_path", DEFAULT_FONT_PATH),
                getattr(draft, "font", "Arial"),
            )[0]
            if lx < sp[0][0]:  # flipped: label extends left of the elbow
                out.append((lx - _SHELF - w_i, lx - _SHELF))
            else:
                out.append((lx + _SHELF, lx + _SHELF + w_i))
        return out

    def _collides(ivals):
        pairs = zip(sorted(ivals), sorted(ivals)[1:])
        return any(a1 > b0 for (_a0, a1), (b0, _b1) in pairs)

    while len(survivors) > 1 and _collides(_label_ivals(survivors, xs)):
        adj: list[float] = []
        for sp, lx in zip(survivors, xs, strict=True):
            v = max(lx, sp[0][0])
            if adj:
                v = max(v, adj[-1] + min_gap)
            adj.append(v)
        if adj[-1] <= fx1 - half_w:
            xs = adj
            break
        drop = min(range(len(survivors)), key=lambda i: survivors[i][1])
        survivors.pop(drop)
        survivors, xs = _place_what_fits(survivors, 0, min_gap, fx0 + half_w, fx1 - half_w)
    for i, ((tip, dia, label, feat), lx) in enumerate(zip(survivors, xs, strict=True)):
        dwg.add(
            Leader(tip=(tip[0], tip[1], 0), elbow=(lx, label_y, 0), label=label, draft=draft),
            f"m_dia_x{start + i}",
            view="front",
            feature=feat,
        )
    return len(survivors)


def _diameter_column_left(dwg, items, start: int = 0) -> int:
    """ø-callout column to the LEFT of the front view for Z-turned step/boss
    diameters (#131) — the page-Y mirror of the row-below. A per-label occupancy
    gate drops only a label that would overprint a bore leader / existing callout
    sharing the left region (#144), never the whole column. Returns the count placed."""
    if not items:
        return 0
    draft = dwg.draft
    fx0, fy0, _, fy1 = dwg.view_bounds("front")
    label_w = (
        max(len(f"ø{_fmt(dia)}{_tol_suffix(dtol, draft)}") for _, dia, _, dtol in items)
        * draft.font_size
        * _EST_CHAR_WIDTH_EM
    )
    elbow_x = fx0 - (draft.font_size + 2 * draft.pad_around_text)
    if elbow_x - label_w < _MARGIN:
        return 0
    specs = []  # (tip_page, dia, label, feature), tip on the step's left silhouette
    for anchor, dia, feat, dtol in items:
        ax, ay, az = anchor
        tip = dwg.at("front", ax - dia / 2, ay, az)
        specs.append((tip, dia, f"ø{_fmt(dia)}{_tol_suffix(dtol, draft)}", feat))
    half_h = draft.font_size / 2 + draft.pad_around_text
    min_gap = 2 * half_h
    # Place what fits; drop the smallest ø first, never the whole column (#298).
    survivors, ys = _place_what_fits(specs, 1, min_gap, fy0 + half_h, fy1 - half_h)
    # Full-footprint occupancy (leader shafts, witness/extension lines, hatch) — NOT
    # the label-box-only `_occupied_boxes`, which is blind to a bore callout's leader
    # SHAFT, so a ø label could silently overprint it (the #133/#225/#305 invisible-
    # occupant class, #358). Centre lines stay crossable (a diameter dim may cross one).
    occupied = strip_obstacles(dwg, view="front", crossable=CROSSABLE_TYPES)
    placed = 0
    for i, ((tip, dia, label, feat), ly) in enumerate(zip(survivors, ys, strict=True)):
        ldr = Leader(tip=(tip[0], tip[1], 0), elbow=(elbow_x, ly, 0), label=label, draft=draft)
        if _box_hits(_anno_box(ldr), occupied):
            continue  # would overprint a bore leader / existing callout — drop just this one
        dwg.add(ldr, f"m_dia_z{start + i}", view="front", feature=feat)
        occupied.append(_anno_box(ldr))
        placed += 1
    return placed


def render_diameters(dwg, groups, tol: float = 0.15, *, ctx, only=None) -> int:
    """ø leaders for a turned part's external step/boss diameters, from the IR —
    one distinct callout per diameter, in a tidy row below the front view
    (X-turning) or a column to its left (Z-turning). Orientation is the feature
    frame's axis, not two passes. Replaces the engine's ``_annotate_turned_diameters``
    (ADR 0008 convergence). Diameters another annotation already covers are skipped.

    *only*, when given, restricts placement to step/boss features in the set — the #426
    finalize() path passes the recorded step/boss ``callout`` intents' features. ``None``
    (the auto-pass) places every diameter with the historical 0-based ``m_dia_{x,z}``
    naming, byte-identically."""
    mentioned = _mentioned_diameters(dwg)
    # One distinct callout per (axis, diameter). Accumulate EVERY feature that shares a
    # diameter (insertion-ordered), so provenance (#412) can tag the callout with its
    # single owner — or leave it unowned when two distinct features share the diameter
    # (the #398c/#406 shared-value rule, so drop can't over-strip a sibling).
    row_buckets: dict = {}  # round(dia,2) -> [anchor, dia, {features}, tolerance]  (X-turned)
    col_buckets: dict = {}  # Z-turned
    for g in groups:
        if g.feature_kind not in ("step", "boss"):
            continue
        if only is not None and g.feature not in only:  # #426 finalize: recorded subset
            continue
        dpd = next((pd for pd in g.dims if pd.param.kind == "diameter"), None)
        if dpd is None:
            continue
        dia = dpd.param.value
        if any(abs(dia - m) <= tol for m in mentioned):
            continue
        bucket = {"x": row_buckets, "z": col_buckets}.get(g.feature.frame.axis)
        if bucket is None:
            continue
        dkey = round(dia, 2)
        dtol = dpd.param.tolerance
        # entry = [anchor, dia, {features}, ± tolerance]. A callout is per (axis, ⌀); the
        # first authored tolerance on a shared ⌀ wins (P2a — a single callout, one label).
        entry = bucket.setdefault(dkey, [g.anchor, dia, set(), dtol])
        entry[2].add(g.feature)
        if entry[3] is None:
            entry[3] = dtol

    def _items(buckets):
        return [
            (a, d, next(iter(fs)) if len(fs) == 1 else None, t) for a, d, fs, t in buckets.values()
        ]

    # The placers name leaders m_dia_{x,z}{start+i} CONTIGUOUSLY from one start. The auto-pass
    # (only None) uses start=0 — byte-identical. The finalize path (only set) may run after
    # existing m_dia names (a prior batch), so it starts past the MAX existing index — NOT the
    # first-free (which is unsound for a multi-item run when the names are non-contiguous, e.g.
    # after drop(): a gap below an occupied index would let the run wrap onto it and silently
    # overwrite an earlier leader — #432 review). Starting past the max keeps the whole run free.
    def _next_start(prefix):
        idxs = [
            int(n[len(prefix) :])
            for n in ctx.registry.names()
            if n.startswith(prefix) and n[len(prefix) :].isdigit()
        ]
        return max(idxs) + 1 if idxs else 0

    start_x = _next_start("m_dia_x") if only is not None else 0
    start_z = _next_start("m_dia_z") if only is not None else 0
    return _diameter_row_below(dwg, _items(row_buckets), start=start_x) + _diameter_column_left(
        dwg, _items(col_buckets), start=start_z
    )


def _env_pd(group, role):
    """The PlannedDimension for an envelope role (width/depth/height), or None."""
    return next((pd for pd in group.dims if pd.param.role == role), None)


def envelope_group(groups):
    """The envelope `DimensionGroup` in *groups*, or None."""
    return next((g for g in groups if g.feature_kind == "envelope"), None)


def env_dim_placed(pd) -> bool:
    """Whether :func:`render_envelope` will actually place an envelope dim for the
    PlannedDimension *pd* — present, not suppressed by the planner (square footprint /
    X-turned, #250), and carrying a span. The single source of truth for that
    decision, shared with the orchestrator's side-below tier reservation so the two
    can never drift (#316 review)."""
    return pd is not None and not pd.suppressed and pd.param.span is not None


def _envelope_tier(dwg, strip, view, size):
    """The page-coord at which an envelope dim of *size* stacks OUTSIDE every placed
    obstacle in *view* on *strip* — the outermost free tier that fits, placed at its
    inner (view-facing) edge — or None if no free tier fits.

    Cursor-free (ADR 0009 carve), unlike the ``Strip.allocate`` it replaces: the
    envelope dim lands beyond the feature/location dims already placed on this strip
    (they become obstacles here), giving the ISO 'overall dim outermost' stack **by
    construction**. That is enforced here by choosing the *outermost* fitting free
    segment — NOT merely the one nearest the view, which would land the envelope
    inside any obstacle sitting in a middle/outer tier (a callout label, a leader
    shaft) whenever an inner tier happened to be free, inverting the stack. The old
    ``allocate`` gave the right order only because an earlier pass had advanced a
    shared cursor; that coupling inverted the moment the location pass moved to
    ``plan_strip`` (#321), which never advances the cursor. Reading obstacle boxes
    decouples the two passes. The current renderer queues envelope dims into the shared
    corridor instead; #133 mandatory-dim starvation is guarded by envelope priority.

    Assumes a below/above strip (Y stacking axis) — the only strips ``render_envelope``
    uses; a left/right strip would need the X interval of each obstacle box."""
    lo, hi, inner = strip_free_span(strip)
    obst = strip_obstacles(dwg, view=view, crossable=CROSSABLE_TYPES)
    segs = carve_free_segments(lo, hi, [(b[1], b[3]) for b in obst], strip.spacing)
    # Fit tolerant of float error at the reservation boundary (#133): the guaranteed
    # segment is exactly `size` wide in the saturated worst case.
    fitting = [s for s in segs if s[1] - s[0] >= size - 1e-9]
    if not fitting:
        return None
    if inner == hi:  # below/left: outermost = smallest coords; place at seg inner (hi) edge
        return min(fitting, key=lambda s: s[0])[1]
    return max(fitting, key=lambda s: s[1])[0]  # above/right: outermost = largest coords


def _chamfer_label(ch) -> str:
    """The chamfer callout string: ``C{leg}`` for an equal-leg 45° chamfer, else
    ``{leg} × {angle}°`` (#560). Formatting lives in the render layer, not on the IR
    feature — every other feature's label is formed by the planner/renderer too, so a
    ``ChamferFeature`` stays pure data (ADR 0013 §7)."""
    if abs(ch.leg1 - ch.leg2) < 0.05 and abs(ch.angle - 45.0) < 0.5:
        return f"C{_fmt(ch.leg1)}"
    return f"{_fmt(ch.leg1)} × {_fmt(ch.angle)}°"


# ── Shared machined-feature leader-callout pass (#637) ──────────────────────────────────
# render_chamfers/_fillets/_flats/_pockets/_grooves were the same function five times: pick
# the view an edge/face reads in, lead a diagonal Leader out to a label, and keep it only if
# the LABEL lands in clear margin. They now share one pass and differ only in their label,
# their tip/lead geometry (corner-diagonal vs mid-face radial), and their view map — a sixth
# feature kind is a new thin adapter (or a table row), never a sixth copy.


def _leader_callout_reach(draft) -> float:
    """Outward leader length past the tip (label→elbow) for a machined-feature callout: one
    line height plus six text-pads. Shared so all five callout passes reach the same distance
    into the margin."""
    return float(draft.font_size + 6 * draft.pad_around_text)


def _label_lands_clear(ldr, obstacles, silhouette, page) -> bool:
    """True when a leader's LABEL box sits in clear margin: off other annotations
    (*obstacles*), off the part *silhouette* (the leader line may cross into the view, its
    text may not), and inside the *page* margin box. The single accept test the five callout
    passes share (was the identical six-line guard, copied five times)."""
    label = getattr(ldr, "label_bbox", None) or _anno_box(ldr)
    if label is None:
        return False
    return not (
        _box_hits(label, obstacles)
        or _box_hits(label, [silhouette])
        or label[0] < page[0]
        or label[1] < page[1]
        or label[2] > page[2]
        or label[3] > page[3]
    )


def _corner_candidates(dwg, view, vb, members, reach):
    """Lead candidates for a corner-sitting feature (chamfer/fillet/flat): from each member's
    projected origin, a diagonal from the view centre out through the corner, *reach* beyond
    the tip — a corner clears the silhouette this way. Yields ``(tip, elbow, member)`` in the
    given member order (nearest-clear wins in the pass); a single-feature callout passes a
    one-element *members*."""
    x0, y0, x1, y1 = vb
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    for m in members:
        tip = dwg.at(view, *m.frame.origin)
        dx, dy = tip[0] - cx, tip[1] - cy
        d = math.hypot(dx, dy) or 1.0
        elbow = (tip[0] + dx / d * reach, tip[1] + dy / d * reach, 0)
        yield (tip, elbow, m)


def _leader_callout_pass(dwg, a, jobs, *, noun, drop_code, ctx) -> int:
    """Place one machined-feature leader callout per job (#637). A *job* is
    ``(name, view, vb, label, candidates)`` where *candidates* yields ``(tip, elbow,
    feature)`` lead positions to try in order. Places the first Leader whose label lands clear
    (:func:`_label_lands_clear`), attributed to that candidate's feature; if none of a job's
    candidates land, records ``<noun> callout … not placed`` as ``<drop_code>`` lint (never a
    silent drop). Obstacles are recomputed per job so a callout placed earlier is avoided.
    Returns the count placed."""
    page = (a.margin, a.margin, a.PAGE_W - a.margin, a.PAGE_H - a.margin)
    n = 0
    for name, view, vb, label, candidates in jobs:
        obstacles = strip_obstacles(dwg, view=view, crossable=CROSSABLE_TYPES)
        for tip, elbow, feature in candidates:
            ldr = Leader(tip=(tip[0], tip[1], 0), elbow=elbow, label=label, draft=dwg.draft)
            if _label_lands_clear(ldr, obstacles, vb, page):
                dwg.add(ldr, name, view=view, feature=feature)
                n += 1
                break
        else:
            ctx.record_issue(
                "warning", drop_code, f"{noun} callout {label} not placed (no clear room)"
            )
    return n


def render_chamfers(dwg, model, a, *, ctx) -> int:
    """Chamfer callouts (#560): a leader from each recognised chamfer face to its
    ``C{leg}`` / ``{leg}×{angle}°`` label, in the view normal to the chamfered edge (a Z
    edge reads in the plan, an X edge in the side, a Y edge in the front). The leader runs
    diagonally OUT of the corner the chamfer sits on into clear margin, and is dropped
    (lint, not silently) if it would overprint placed geometry. Returns the count placed."""
    reach = _leader_callout_reach(dwg.draft)
    view_of = {"z": "plan", "x": "side", "y": "front"}
    chamfers = [f for f in model.features if f.kind == "chamfer"]
    jobs = []
    for i, ch in enumerate(sorted(chamfers, key=lambda f: (f.axis, f.frame.origin))):
        view = view_of.get(ch.axis)
        if view is None:
            continue
        vb = dwg.view_bounds(view)
        if vb is None:
            continue
        jobs.append(
            (
                f"m_chamfer_{ch.axis}{i}",
                view,
                vb,
                _chamfer_label(ch),
                _corner_candidates(dwg, view, vb, [ch], reach),
            )
        )
    return _leader_callout_pass(dwg, a, jobs, noun="chamfer", drop_code="chamfer_dropped", ctx=ctx)


def _fillet_label(radius, count) -> str:
    """The fillet callout string: ``R{radius}``, prefixed ``{count}×`` when a set of equal
    fillets shares one callout (#561). Formatting lives in the render layer (ADR 0013 §7)."""
    r = f"R{_fmt(radius)}"
    return f"{count}× {r}" if count > 1 else r


def render_fillets(dwg, model, a, *, ctx) -> int:
    """Fillet radius callouts (#561): a leader from an external edge fillet to its
    ``R{radius}`` label — the arc analog of :func:`render_chamfers`. Equal-radius fillets on
    the same edge axis share ONE ``n× R`` callout (#561 acceptance), placed in the view
    normal to the rounded edge, led diagonally out of the corner into clear margin, and
    dropped (lint, not silently) if it would overprint placed geometry. Returns the count."""
    reach = _leader_callout_reach(dwg.draft)
    view_of = {"z": "plan", "x": "side", "y": "front"}
    fillets = [f for f in model.features if f.kind == "fillet"]
    groups: dict = {}
    for fl in fillets:
        groups.setdefault((fl.axis, round(fl.radius, 3)), []).append(fl)
    jobs = []
    for gi, ((axis, radius), members) in enumerate(sorted(groups.items())):
        view = view_of.get(axis)
        if view is None:
            continue
        vb = dwg.view_bounds(view)
        if vb is None:
            continue
        # One grouped ``n× R`` callout, but its leader may anchor at ANY of the equal fillets
        # — _corner_candidates tries each corner (nearest-clear first) so the group is not
        # dropped just because its first corner leads into an occupied region.
        ordered = sorted(members, key=lambda f: f.frame.origin)
        jobs.append(
            (
                f"m_fillet_{axis}{gi}",
                view,
                vb,
                _fillet_label(radius, len(members)),
                _corner_candidates(dwg, view, vb, ordered, reach),
            )
        )
    return _leader_callout_pass(dwg, a, jobs, noun="fillet", drop_code="fillet_dropped", ctx=ctx)


def _flat_label(across) -> str:
    """The machined-flat callout string: ``{across} A/F`` (across flats) — the standard
    abbreviation for a spanner-flat / D / hex size (#148b). Formatting lives in the render
    layer, not on the IR feature (ADR 0013 §7)."""
    return f"{_fmt(across)} A/F"


def render_flats(dwg, model, a, *, ctx) -> int:
    """Machined-flat callouts (#148b): a leader from a flat truncating round stock to its
    ``{across} A/F`` label, in the view down the stock axis (a Z-axis bar reads in the plan).
    Flats sharing an axis and across-flats size — the faces of a double-D or hex — share ONE
    callout. The leader runs diagonally out of the flat into clear margin, and is dropped
    (lint, not silently) if it would overprint placed geometry. Returns the count placed."""
    reach = _leader_callout_reach(dwg.draft)
    view_of = {"z": "plan", "x": "side", "y": "front"}
    flats = [f for f in model.features if f.kind == "flat"]
    groups: dict = {}
    for fl in flats:
        groups.setdefault((fl.axis, round(fl.across, 3)), []).append(fl)
    jobs = []
    for gi, ((axis, across), members) in enumerate(sorted(groups.items())):
        view = view_of.get(axis)
        if view is None:
            continue
        vb = dwg.view_bounds(view)
        if vb is None:
            continue
        ordered = sorted(members, key=lambda f: f.frame.origin)
        jobs.append(
            (
                f"m_flat_{axis}{gi}",
                view,
                vb,
                _flat_label(across),
                _corner_candidates(dwg, view, vb, ordered, reach),
            )
        )
    return _leader_callout_pass(dwg, a, jobs, noun="flat", drop_code="flat_dropped", ctx=ctx)


def _groove_label(width, diameter) -> str:
    """The turned/circlip-groove callout string: ``{width} WIDE × ø{diameter}`` — the groove's
    axial width and its floor diameter (#148c). Formatting lives in the render layer, not on
    the IR feature (ADR 0013 §7)."""
    return f"{_fmt(width)} WIDE × ø{_fmt(diameter)}"


def _ray_exit_dist(px, py, ux, uy, rect) -> float:
    """Distance along the unit ray (ux, uy) from (px, py) to where it leaves *rect*
    (x0, y0, x1, y1). For a tip inside the rect this is the positive distance to the
    boundary; clamped to ``>= 0`` so a tip already outside contributes no negative reach."""
    x0, y0, x1, y1 = rect
    ts = []
    if ux > 0:
        ts.append((x1 - px) / ux)
    elif ux < 0:
        ts.append((x0 - px) / ux)
    if uy > 0:
        ts.append((y1 - py) / uy)
    elif uy < 0:
        ts.append((y0 - py) / uy)
    return max(min([t for t in ts if t > 0], default=0.0), 0.0)


def _pocket_label(pk) -> str:
    """The pocket callout string: ``{width} × {length} × {depth} DEEP`` (#148a). The ISO
    depth glyph (↧) is drawn as geometry by the helper's hole callouts, not as font text —
    a plain :class:`Leader` label has no access to it, so this uses the font-safe ``DEEP``
    word (the vendored Plex Mono lacks ↧). Formatting lives in the render layer (ADR
    0013 §7)."""
    return f"{_fmt(pk.width)} × {_fmt(pk.length)} × {_fmt(pk.depth)} DEEP"


# Unit lead directions tried (nearest-clear wins), diagonals first so a central pocket's
# leader exits toward a corner (usually the emptiest margin) before an edge.
_POCKET_LEAD_DIRS = (
    (1, 1),
    (-1, 1),
    (-1, -1),
    (1, -1),
    (1, 0),
    (0, 1),
    (-1, 0),
    (0, -1),
)


def _radial_candidates(dwg, view, vb, feature, reach):
    """Lead candidates for a mid-face feature (pocket/groove): from the feature's projected
    origin, one candidate per :data:`_POCKET_LEAD_DIRS` direction — exit the silhouette along
    it (:func:`_ray_exit_dist`) then *reach* on into the margin, so even a centre-of-view
    feature clears the part. Yields ``(tip, elbow, feature)`` (same feature each time;
    nearest-clear wins in the pass)."""
    x0, y0, x1, y1 = vb
    tip = dwg.at(view, *feature.frame.origin)
    for dx, dy in _POCKET_LEAD_DIRS:
        d = math.hypot(dx, dy)
        ux, uy = dx / d, dy / d
        exit_d = _ray_exit_dist(tip[0], tip[1], ux, uy, (x0, y0, x1, y1))
        elbow = (tip[0] + ux * (exit_d + reach), tip[1] + uy * (exit_d + reach), 0)
        yield (tip, elbow, feature)


def render_pockets(dwg, model, a, *, ctx) -> int:
    """Blind-recess callouts (#148a): a leader from each floored slot/pocket to its
    ``W × L × D DEEP`` label, in the view normal to the recess opening (a Z-depth pocket
    reads in the plan, an X-depth in the side, a Y-depth in the front). A pocket sits
    mid-face, so — unlike a corner chamfer — the leader is tried toward each margin
    direction (nearest clear wins) and the callout is dropped (lint, not silently) if none
    lands in clear room. Returns the count placed."""
    reach = _leader_callout_reach(dwg.draft)
    view_of = {"z": "plan", "x": "side", "y": "front"}
    pockets = [f for f in model.features if f.kind == "pocket"]
    jobs = []
    for i, pk in enumerate(sorted(pockets, key=lambda f: (f.width_axis, f.frame.origin))):
        view = view_of.get(pk.depth_axis)
        if view is None:
            continue
        vb = dwg.view_bounds(view)
        if vb is None:
            continue
        jobs.append(
            (
                f"m_pocket_{pk.width_axis}{pk.long_axis}{i}",
                view,
                vb,
                _pocket_label(pk),
                _radial_candidates(dwg, view, vb, pk, reach),
            )
        )
    return _leader_callout_pass(dwg, a, jobs, noun="pocket", drop_code="pocket_dropped", ctx=ctx)


def render_grooves(dwg, model, a, *, ctx) -> int:
    """Turned/circlip-groove callouts (#148c): a leader from each annular groove in round
    stock to its ``{width} WIDE × ø{diameter}`` label. The groove's width is *axial*, so the
    callout lands in the **profile** view (the one showing the stock axis in-plane, where the
    groove reads as a notch in the silhouette) — a Z or X axis in the front, a Y axis in the
    side. Each groove gets its own callout at its own axial position (like a pocket, not
    collapsed by size — two identical grooves on one shaft or on parallel shafts must each be
    dimensioned). The groove sits on the axis, so the leader exits the silhouette
    (``_ray_exit_dist``) toward each margin (nearest clear wins) and is dropped (lint, not
    silently) if none lands clear. Returns the count placed."""
    reach = _leader_callout_reach(dwg.draft)
    view_of = {"z": "front", "x": "front", "y": "side"}
    grooves = [f for f in model.features if f.kind == "groove"]
    jobs = []
    for gi, gr in enumerate(sorted(grooves, key=lambda f: (f.axis, f.frame.origin))):
        view = view_of.get(gr.axis)
        if view is None:
            continue
        vb = dwg.view_bounds(view)
        if vb is None:
            continue
        jobs.append(
            (
                f"m_groove_{gr.axis}{gi}",
                view,
                vb,
                _groove_label(gr.width, gr.diameter),
                _radial_candidates(dwg, view, vb, gr, reach),
            )
        )
    return _leader_callout_pass(dwg, a, jobs, noun="groove", drop_code="groove_dropped", ctx=ctx)


def render_boss_diameters(dwg, groups, a, *, ctx) -> int:
    """ø leaders for a PRISMATIC part's bosses (#629). A boss reads as a circle looking down its
    axis, so its diameter is called out with a leader to that circle in the view normal to the
    axis — a Z boss in the plan, X in the side, Y in the front — free to exit into clear margin
    (``_ray_exit_dist``), like a pocket/groove.

    The ⌀ + its tolerance/fit come from the planner's ``DimParameter`` (as in ``render_diameters``),
    never raw geometry — formatting ``b.diameter`` directly dropped an authored diameter tolerance.

    A *turned* part keeps the diameter row/column (``render_diameters``): there the boss ø sits
    in the OD stack. The turned column-left strip, applied to a prismatic boss, strands its ø
    whenever that narrow strip is tight — dropping the callout even on a half-empty sheet (#629).
    Run BEFORE ``render_diameters`` so a placed boss ø is 'mentioned' and not re-placed. A boss
    whose ø a coincident feature already carries is skipped; an unplaceable one drops lint-visibly."""
    if a.is_rotational or a.prof is not None:
        # A turned profile means round stock — a band emitted as a boss (#298) belongs in the
        # OD diameter row/column, not an end-on plan leader. Only true prismatic parts qualify.
        return 0
    draft = dwg.draft
    view_of = {"z": "plan", "x": "side", "y": "front"}  # the view looking down the boss axis
    boss_groups = [g for g in groups if g.feature_kind == "boss"]
    mentioned = _mentioned_diameters(dwg)
    page = (a.margin, a.margin, a.PAGE_W - a.margin, a.PAGE_H - a.margin)
    reach = draft.font_size + 6 * draft.pad_around_text
    n = 0
    for bi, g in enumerate(
        sorted(boss_groups, key=lambda g: (g.feature.frame.axis, g.feature.frame.origin))
    ):
        b = g.feature
        dpd = next((pd for pd in g.dims if pd.param.kind == "diameter"), None)
        if dpd is None:
            continue
        dia = dpd.param.value
        dtol = dpd.param.tolerance
        if any(abs(dia - m) <= 0.15 for m in mentioned):
            continue  # a coincident bore / step already carries this ø
        view = view_of.get(b.frame.axis)
        if view is None:
            continue
        vb = dwg.view_bounds(view)
        if vb is None:
            continue
        x0, y0, x1, y1 = vb
        center = dwg.at(view, *b.frame.origin)
        r_page = dia / 2 * a.SCALE  # the boss circle radius in page units
        label_str = f"ø{_fmt(dia)}{_tol_suffix(dtol, draft)}"
        obstacles = strip_obstacles(dwg, view=view, crossable=CROSSABLE_TYPES)
        placed = False
        for dx, dy in _POCKET_LEAD_DIRS:
            d = math.hypot(dx, dy)
            ux, uy = dx / d, dy / d
            arrow = (center[0] + ux * r_page, center[1] + uy * r_page)  # arrowhead on the rim
            exit_d = _ray_exit_dist(center[0], center[1], ux, uy, (x0, y0, x1, y1))
            elbow = (center[0] + ux * (exit_d + reach), center[1] + uy * (exit_d + reach), 0)
            ldr = Leader(tip=(arrow[0], arrow[1], 0), elbow=elbow, label=label_str, draft=draft)
            # _geom_box is the FULL footprint (shaft + arrow + label); the shaft is the invisible
            # occupant a label-only box hides (#133/#305/#358), so it — not _anno_box — is what
            # must clear the other callouts/witnesses. The silhouette/page checks use the label
            # box, since the shaft legitimately starts at the boss rim inside the view.
            geom = _geom_box(ldr)
            label = getattr(ldr, "label_bbox", None) or geom
            if (
                geom is None
                or label is None
                or _box_hits(geom, obstacles)  # shaft or label crosses a callout/witness
                or _box_hits(label, [(x0, y0, x1, y1)])  # label over the part silhouette
                or label[0] < page[0]
                or label[1] < page[1]
                or label[2] > page[2]
                or label[3] > page[3]
            ):
                continue
            dwg.add(ldr, f"m_bossdia_{b.frame.axis}{bi}", view=view, feature=b)
            n += 1
            placed = True
            break
        if not placed:
            ctx.record_issue(
                "warning",
                "boss_dia_dropped",
                f"boss ø{_fmt(dia)} callout not placed (no clear room)",
            )
    return n


def render_plates(dwg, model, a, *, ctx) -> int:
    """Plate/wall thicknesses (#559): the thin extent of each recognised slab
    (`PlateFeature`), placed in the view where its thin axis is characteristic — a Z
    plate (horizontal slab) as a vertical dim left of the front elevation, a Y plate
    (upright wall) as a horizontal dim above the side (end) view where the L-profile
    shows it edge-on, an X plate below the front view. Base and wall land in different
    views so the two legs of a multi-plate prismatic read as distinct features rather
    than the overall envelope. A slab whose strip is full is dropped with a lint code
    (like the step ladder), not silently. Returns the count placed."""
    draft = dwg.draft
    tier = draft.font_size + 2 * draft.pad_around_text
    plates = [f for f in model.features if f.kind == "plate"]
    n = 0
    counts: dict = {"x": 0, "y": 0, "z": 0}
    for pl in sorted(plates, key=lambda f: (f.axis, f.lo, f.hi)):
        val = pl.hi - pl.lo
        i = counts[pl.axis]
        counts[pl.axis] += 1
        if pl.axis == "z":
            # Horizontal slab (base plate): vertical dim on the front-elevation left strip.
            # For a Z plate the in-plane centroids are (u=X, v=Y); the front view discards
            # Y, so the depth arg is inert, but pass the Y-centroid (pl.v) for correctness.
            view, strip, stack, side = "front", a.fv_zones.left, "x", "left"
            p1 = dwg.at(view, a.bb.min.X, pl.v, pl.lo)
            p2 = dwg.at(view, a.bb.min.X, pl.v, pl.hi)
            edge = p1[0]
            pa, pb = (edge, p1[1], 0), (edge, p2[1], 0)
            # Right-strip fallthrough anchors (helpers ≥0.14): a tight-span thickness
            # dim's witness hull overlaps the below strip's at the view corner at EVERY
            # position (AABB artifact — the ink never touches), so a full left strip
            # retries on the opposite side before dropping.
            q1 = dwg.at(view, a.bb.max.X, pl.v, pl.lo)
            s1 = dwg.at("side", a.bb.min.X, a.bb.max.Y, pl.lo)
            s2 = dwg.at("side", a.bb.min.X, a.bb.max.Y, pl.hi)
            alt = [
                (
                    "front",
                    "right",
                    a.fv_zones.right,
                    "x",
                    (q1[0], p1[1], 0),
                    (q1[0], p2[1], 0),
                    q1[0],
                ),
                (
                    "side",
                    "right",
                    a.sv_zones.right,
                    "x",
                    (s1[0], s1[1], 0),
                    (s1[0], s2[1], 0),
                    s1[0],
                ),
            ]
        elif pl.axis == "y":
            # Upright wall: horizontal dim above the side (end) view, which shows the
            # wall edge-on on the L-profile — a different view from the Z base plate.
            # Witness from the view's top edge (like the Z/X plates anchor at their view
            # outline) so the extension lines don't originate mid-view.
            view, strip, stack, side = "side", a.sv_zones.above, "y", "above"
            p1 = dwg.at(view, a.bb.min.X, pl.lo, a.bb.max.Z)
            p2 = dwg.at(view, a.bb.min.X, pl.hi, a.bb.max.Z)
            edge = p1[1]
            pa, pb = (p1[0], edge, 0), (p2[0], edge, 0)
            alt = None
        else:  # x — thin wall along X → horizontal dim below the front view
            view, strip, stack, side = "front", a.fv_zones.below, "y", "below"
            p1 = dwg.at(view, pl.lo, pl.u, a.bb.min.Z)
            p2 = dwg.at(view, pl.hi, pl.u, a.bb.min.Z)
            edge = p1[1]
            pa, pb = (p1[0], edge, 0), (p2[0], edge, 0)
            alt = None
        name = f"dim_plate_{pl.axis}{i}"

        def _build(pos, pa=pa, pb=pb, side=side, edge=edge, val=val):
            return _dim(pa, pb, side, pos - edge, draft, label=_fmt(val))

        def _foot(pos, pa=pa, pb=pb, side=side, edge=edge, val=val):
            return dim_footprint(pa, pb, side, pos - edge, draft, _fmt(val))

        def _drop(nm, val=val, view=view, stack=stack, alt=alt, feat=pl):  # noqa: B008
            # Opposite-strip fallthrough (mirrors the GD&T #481 pattern), DEFERRED to
            # ctx.post_drain so it runs after EVERY corridor has drained (#684 review):
            # a mid-drain carve could occupy a corner a later sibling's force candidate
            # needs; post-drain, carve_free_position sees all placed annotations.
            def _retry(nm=nm, val=val, view=view, stack=stack, alt=alt, feat=feat):
                for view2, side2, strip2, axis2, qa, qb, edge2 in alt or ():
                    if strip2 is None:
                        continue
                    foot0 = dim_footprint(qa, qb, side2, tier, draft, _fmt(val))
                    perp = (foot0[1], foot0[3]) if axis2 == "x" else (foot0[0], foot0[2])
                    pos = carve_free_position(dwg, strip2, view2, axis2, tier, perp)
                    if pos is not None:
                        # Accept-time validation (#684 r2): the carve accepted the
                        # ANALYTICAL footprint — build once and re-check the real box
                        # against live obstacles + the page before adding (the same
                        # contract as the corridor's validation fallback). A miss
                        # tries the next alternate.
                        dim = _dim(qa, qb, side2, pos - edge2, draft, label=_fmt(val))
                        real = _geom_box(dim)
                        page = (_MARGIN, _MARGIN, a.PAGE_W - _MARGIN, a.PAGE_H - _MARGIN)
                        if real is not None and (
                            _box_hits(
                                real, strip_obstacles(dwg, view=view2, crossable=CROSSABLE_TYPES)
                            )
                            or real[0] < page[0]
                            or real[1] < page[1]
                            or real[2] > page[2]
                            or real[3] > page[3]
                        ):
                            continue
                        dwg.add(dim, nm, view=view2, feature=feat)
                        return
                ctx.record_issue(
                    "warning",
                    "plate_thickness_dropped",
                    f"plate thickness {_fmt(val)} not dimensioned ({view} {stack}-strip full)",
                )

            # Queued retries run in registration order (deterministic; plates sort by
            # axis/lo/hi) and pick their first viable alternate greedily — two plates
            # contending for the same two alternates could in principle assign
            # suboptimally (#684 r2, accepted: joint-solving deferred retries belongs
            # to the L-shaped-occupancy/corner follow-up).
            ctx.post_drain.append(_retry)

        # ADR 0009 corridor candidate (#636): a plate thickness is a size dim bound to one
        # view/strip (no alternate view), so it is force-kept and dropped only when the strip
        # is physically full — the same outcome the prior solver-invisible carve gave, but now
        # co-solved with the locations/steps that share this strip.
        register_corridor(
            ctx,
            (view, side),
            strip,
            view,
            stack,
            tier,
            CorridorCandidate(
                name=name,
                build=_build,
                order=(_SIZE_SUBCHAIN, i, name),
                on_place=lambda nm: None,
                on_drop=_drop,
                force=True,
                feature=pl,
                footprint=_foot,  # analytical measure — no probe build (#602)
            ),
        )
        n += 1
    return n


def render_envelope(dwg, groups, a, *, ctx) -> int:
    """Overall width (plan, below) + depth (side, below) envelope dims via the IR,
    registered into the same below-strip corridor as feature/location/GD&T/PMI candidates.
    The overall dims use the last ladder subchain so they stack outermost by construction,
    while their mandatory priority prevents best-effort below-strip occupants from starving
    principal dimensions. The **planner** decides suppression (square footprint / X-turned;
    #250); this renderer just skips suppressed dims and queues the rest. Returns the count
    queued."""
    env = envelope_group(groups)
    if env is None:
        return 0
    n = 0

    def _queue(name, strip, view, tier, distance, build, footprint=None):
        register_corridor(
            ctx,
            (view, "below"),
            strip,
            view,
            "y",
            tier,
            CorridorCandidate(
                name=name,
                build=build,
                order=(_OVERALL_SUBCHAIN, distance, name),
                on_place=lambda _nm: None,
                on_drop=lambda _nm: None,
                priority=_MANDATORY_OVERALL_PRIORITY,
                force=True,
                footprint=footprint,  # analytical measure — no probe build (#602)
            ),
        )

    width = _env_pd(env, "width")
    if env_dim_placed(width):
        (x0, y0, z0), (x1, _, _) = width.param.span
        p1, p2 = dwg.at("plan", x0, y0, z0), dwg.at("plan", x1, y0, z0)
        witness = p1[1] - _WITNESS_LIFT_MM
        _queue(
            "m_env_width",
            a.pv_zones.below,
            "plan",
            _SLOT_DIM_WIDTH,
            abs(x1 - x0),
            lambda pos, _p1=p1, _p2=p2, _w=witness, _v=width.param.value: _dim(
                (_p1[0], _w, 0),
                (_p2[0], _w, 0),
                "below",
                _w - pos,
                dwg.draft,
                label=_fmt(_v),
            ),
            footprint=lambda pos, _p1=p1, _p2=p2, _w=witness, _v=width.param.value: dim_footprint(
                (_p1[0], _w, 0), (_p2[0], _w, 0), "below", _w - pos, dwg.draft, _fmt(_v)
            ),
        )
        n += 1
    depth = _env_pd(env, "depth")
    if env_dim_placed(depth):
        (x0, y0, z0), (_, y1, _) = depth.param.span
        p1, p2 = dwg.at("side", x0, y0, z0), dwg.at("side", x0, y1, z0)
        witness = p1[1] - _WITNESS_LIFT_MM
        _queue(
            "m_env_depth",
            a.sv_zones.below,
            "side",
            _SLOT_DIM_DEPTH,
            abs(y1 - y0),
            lambda pos, _p1=p1, _p2=p2, _w=witness, _v=depth.param.value: _dim(
                (_p1[0], _w, 0),
                (_p2[0], _w, 0),
                "below",
                _w - pos,
                dwg.draft,
                label=_fmt(_v),
            ),
            footprint=lambda pos, _p1=p1, _p2=p2, _w=witness, _v=depth.param.value: dim_footprint(
                (_p1[0], _w, 0), (_p2[0], _w, 0), "below", _w - pos, dwg.draft, _fmt(_v)
            ),
        )
        n += 1
    return n


def _record_step_chain_drop(dwg, why: str, *, ctx) -> None:
    """Record the ``step_dim_dropped`` lint warning when a turned step-length chain
    is dropped whole (#362). These drops were silent (debug log only) — the user got
    a drawing with no step-length dimensioning and no signal. Mirrors
    ``render_height_ladder``'s prismatic drop, but records ONLY the lint code (not an
    ``Escalation(kind="step")``): that escalation is consumed by
    ``_request_prismatic_detail`` (sections.py), which would redraw *prismatic*
    height-above-base dims for a *turned* chain — the wrong semantics #351 PR-4b
    removed. A Z-turned-appropriate detail-view remedy is a tracked follow-up."""
    ctx.record_issue(
        "warning",
        "step_dim_dropped",
        f"step-length chain dropped: {why} at this scale (use a detail view)",
    )


def _draw_step_chain(
    dwg, view, segs, name_prefix, detail_scale=None, allow_collapse=True, *, ctx, start=0
) -> int:
    """Place a turned step-length chain in *view* from *segs* — each ``(pa, pb,
    value)`` already projected to *view*'s page coords, in axis order. Orientation is
    data (the projected span direction): horizontal → chain above the view, vertical
    → chain to the right. A uniform run collapses to one ``N× v`` dim (#230); else a
    per-segment chain, staggered into a near/far tier only when crowded (ISO 129-1,
    #293); skipped if even two tiers can't separate the labels, or if any dim would
    fall off the page. ``detail_scale`` tags the dims for label-vs-measured lint when
    drawing inside a scaled detail view. ``allow_collapse=False`` disables the ``N× v``
    collapse — used when the chain mixes a synthetic head-*block* with real steps, where
    a uniform-staircase representative would be a false claim of N equal steps (#307
    review). Returns the count placed."""
    if not segs:
        return 0
    vb = dwg.view_bounds(view)
    if vb is None:
        return 0
    x0, y0, x1, y1 = vb
    draft = dwg.draft
    gap = draft.font_size + 4 * draft.pad_around_text
    horizontal = abs(segs[0][1][0] - segs[0][0][0]) >= abs(segs[0][1][1] - segs[0][0][1])
    vals = [s[2] for s in segs]  # value is index 2 (segs are 4-tuples: pa, pb, value, tol)
    mean_v = sum(vals) / len(vals)
    if allow_collapse and len(segs) >= 3 and (max(vals) - min(vals)) <= 0.10 * mean_v:
        # A uniform run collapses to one "N× v" dim; a per-step ± would be a false claim on
        # N equal steps, so the collapse carries NO tolerance (#28 / P2a).
        label = f"{len(segs)}× {_fmt(mean_v)}"
        xs = [p[0] for pa, pb, *_ in segs for p in (pa, pb)]
        ys = [p[1] for pa, pb, *_ in segs for p in (pa, pb)]
        if horizontal:
            dim = _dim((min(xs), y1, 0), (max(xs), y1, 0), "above", gap, draft, label=label)
        else:
            dim = _dim((x1, min(ys), 0), (x1, max(ys), 0), "right", gap, draft, label=label)
        typ_name = f"{name_prefix}_typ" if start == 0 else f"{name_prefix}_typ{start}"
        candidates = [(typ_name, dim)]
    else:
        tier_step = draft.font_size + 2 * draft.pad_around_text
        tiers = [0] * len(segs)
        if horizontal:
            cw = [
                ((pa[0] + pb[0]) / 2, len(_fmt(v)) * draft.font_size * _EST_CHAR_WIDTH_EM)
                for pa, pb, v, *_ in segs
            ]

            def _clear(items):  # (center, width) pairs in x order — labels don't overlap
                return all(
                    c2 - c1 >= (w1 + w2) / 2 + draft.pad_around_text
                    for (c1, w1), (c2, w2) in zip(items, items[1:])
                )

            if _clear(cw):
                pass  # one tier suffices — no needless zig-zag for a roomy chain
            elif _clear(cw[0::2]) and _clear(cw[1::2]):
                tiers = [i % 2 for i in range(len(segs))]  # alternate to make room
            else:
                _log.info("step-length chain skipped: too dense even when staggered")
                _record_step_chain_drop(
                    dwg, "shoulders too dense to dimension even when staggered", ctx=ctx
                )
                return 0
        else:
            shoulder_ys = sorted({c for pa, pb, *_ in segs for c in (pa[1], pb[1])})
            if any(b - a < tier_step for a, b in zip(shoulder_ys, shoulder_ys[1:])):
                _log.info("step-length chain skipped: shoulders too close to dimension")
                _record_step_chain_drop(
                    dwg, "turned shoulders too closely spaced to dimension", ctx=ctx
                )
                return 0

        candidates = []
        for i, (pa, pb, value, seg_tol) in enumerate(segs):
            if horizontal:
                p1, p2, side = (pa[0], y1, 0), (pb[0], y1, 0), "above"
                dist = gap + tiers[i] * tier_step
            else:
                p1, p2, side = (x1, pa[1], 0), (x1, pb[1], 0), "right"
                dist = gap
            candidates.append(
                (
                    f"{name_prefix}{start + i}",
                    _dim(p1, p2, side, dist, draft, label=_fmt(value), tolerance=seg_tol),
                )
            )

    # Room guard: if any dim would fall off the drawable page, place NONE.
    page = (_MARGIN, _MARGIN, dwg.page_w - _MARGIN, dwg.page_h - _MARGIN)
    for _, dim in candidates:
        box = _anno_box(dim)
        if box is not None and not (
            page[0] <= box[0] and box[2] <= page[2] and page[1] <= box[1] and box[3] <= page[3]
        ):
            _record_step_chain_drop(dwg, "a dimension would fall off the drawable page", ctx=ctx)
            return 0
    for name, dim in candidates:
        if detail_scale is not None:
            dim._dw_scale = detail_scale
        dwg.add(dim, name, view=view)
    return len(candidates)


def _next_steplen_start(ctx, prefix: str = "m_steplen") -> int:
    """First free m_steplen index past the MAX existing one — the #426 finalize path names
    the chain as a contiguous run from one start, so it must clear every existing name (max+1,
    not first-free: a gap below an occupied index would let the run wrap onto it, #432)."""
    idxs: list[int] = []
    for n in ctx.registry.names():
        if not n.startswith(prefix):
            continue
        rest = n[len(prefix) :]
        if rest.isdigit():
            idxs.append(int(rest))
        elif rest.startswith("_typ"):  # the N× collapse name m_steplen_typ{start}
            tail = rest[4:]
            idxs.append(int(tail) if tail.isdigit() else 0)
    return max(idxs) + 1 if idxs else 0


def render_step_lengths(dwg, groups, *, ctx, only=None) -> int:
    """Unified turned step-length chain (ADR 0008 #223): each `StepFeature`'s length
    span projects into the front view and joins the chain that tiles the turning axis
    so every shoulder is located. X-turned → horizontal chain above the view;
    Z-turned → vertical chain to the right.

    A crowded **X-turned head** — a contiguous run of steps too short to dimension
    legibly even staggered (shoulders below the page arrowhead floor) — is not crammed
    in line: the main view locates that run as one *block* dim and an enlarged
    `DetailRequest` (#304/#307) is queued to break it down. If the detail later can't
    place, the block still locates the head extent and lint reports the un-located
    interior shoulders — never worse than the prior skip. Returns the count placed on
    the front view."""
    rows = []  # (a_world, b_world, value) in axis order
    for g in groups:
        if g.feature_kind != "step":
            continue
        if only is not None and g.feature not in only:  # #426 finalize: recorded subset
            continue
        length = next(
            (pd.param for pd in g.dims if pd.param.kind == "length" and pd.param.span is not None),
            None,
        )
        if length is None or length.span is None:
            continue
        rows.append((length.span[0], length.span[1], length.value, length.tolerance))
    if not rows:
        return 0
    draft = dwg.draft
    # only=None (auto-pass) → start=0, historical m_steplen naming, byte-identical. The
    # finalize path (only set) starts past existing m_steplen names (#426 naming seam).
    start = _next_steplen_start(ctx) if only is not None else 0
    fsegs = [(dwg.at("front", *a), dwg.at("front", *b), v, t) for a, b, v, t in rows]
    horizontal = abs(fsegs[0][1][0] - fsegs[0][0][0]) >= abs(fsegs[0][1][1] - fsegs[0][0][1])

    # X-turned crowded-head detour (#307): split off each contiguous *run of ≥2*
    # sub-floor steps (segment narrower than two arrowheads on the page), locate it as
    # a block, and queue an enlarged detail. A single isolated thin step is left in the
    # main chain — a one-step block would just be that step at its sub-floor width
    # (#307 review). The legible steps + blocks stay as the main chain.
    if horizontal:
        floor_pg = 2 * draft.arrow_length
        sub = [i for i, (pa, pb, *_) in enumerate(fsegs) if abs(pb[0] - pa[0]) < floor_pg]
        runs: list[list[int]] = []
        for j in sub:
            (runs[-1].append(j) if runs and j == runs[-1][-1] + 1 else runs.append([j]))
        heads = [run for run in runs if len(run) >= 2]
        if heads:
            blocks = []
            for run in heads:
                ra = [rows[i] for i in run]
                hlo = min(min(a[0], b[0]) for a, b, *_ in ra)
                hhi = max(max(a[0], b[0]) for a, b, *_ in ra)
                minlen = min(r[2] for r in ra)  # value is index 2 (rows are 4-tuples: a,b,v,tol)
                # World→page scale for the detail (no sheet factor — detail_scale is an
                # absolute world→page scale). (#307 review)
                scale_needed = _MIN_STEP_SEP_MM / minlen if minlen > 0 else float("inf")
                # A head *block* is a synthetic span, not one toleranced step — carry no ± (None).
                blocks.append(
                    (dwg.at("front", hlo, 0, 0), dwg.at("front", hhi, 0, 0), hhi - hlo, None)
                )

                def _redraw(dwg, view, detail_scale, _hw=ra):
                    # View-scoped name prefix so two detail views never collide (#307 review).
                    hsegs = [(dwg.at(view, *a), dwg.at(view, *b), v, t) for a, b, v, t in _hw]
                    return _draw_step_chain(
                        dwg, view, hsegs, f"dim_{view}_steplen", detail_scale, ctx=ctx
                    )

                ctx.detail_requests.append(
                    DetailRequest(
                        axis="x",
                        lo=hlo,
                        hi=hhi,
                        scale_needed=scale_needed,
                        redraw=_redraw,
                        pad_top=2 * (draft.font_size + 2 * draft.pad_around_text)
                        + draft.arrow_length,
                        kind="turned-head",
                    )
                )
            head = {i for run in heads for i in run}
            main = [fsegs[i] for i in range(len(fsegs)) if i not in head] + blocks
            main.sort(key=lambda s: s[0][0])
            # The chain now mixes head-block(s) with real steps — never collapse it to a
            # uniform "N× v" representative (a block is not a repeated step, #307 review).
            return _draw_step_chain(
                dwg, "front", main, "m_steplen", allow_collapse=False, ctx=ctx, start=start
            )

    return _draw_step_chain(dwg, "front", fsegs, "m_steplen", ctx=ctx, start=start)


def _detect_step_repeat(step_zs, bb_min_z, bb_max_z, tol_frac=0.10):
    """Return (n, rise) if *step_zs* form a uniform staircase, else None.

    A uniform staircase has all inter-step rises (including from bb_min_z to the
    first step) within *tol_frac* of their mean. Requires >=3 detected interior
    steps to avoid false positives. *n* is len(step_zs) + 1 when the top gap
    (bb_max_z - last step) also matches the mean, otherwise len(step_zs).
    """
    if len(step_zs) < 3:
        return None
    sorted_zs = sorted(step_zs)
    rises = [sorted_zs[0] - bb_min_z] + [
        sorted_zs[i + 1] - sorted_zs[i] for i in range(len(sorted_zs) - 1)
    ]
    mean_rise = sum(rises) / len(rises)
    if mean_rise <= 0:
        return None
    if not all(abs(r - mean_rise) / mean_rise <= tol_frac for r in rises):
        return None
    top_gap = bb_max_z - sorted_zs[-1]
    n = len(rises) + (1 if abs(top_gap - mean_rise) / mean_rise <= tol_frac else 0)
    return n, mean_rise


def render_height_ladder(dwg, model, a, *, ctx, include_overall: bool = True) -> int:
    """Front-view right ladder: prismatic step heights (from `StepLevelFeature`)
    stacked inner→outer, then the overall height outermost — through `fv_zones.right`,
    preserving the leapfrog witness cursor (#237). Replaces the engine's inline
    `dim_step_*` + `dim_height`. A turned part has no `StepLevelFeature` (its steps are
    the IR length chain); a Z-turned part suppresses the overall height (the chain
    tiles it, ISO 129). Returns the count placed."""
    draft = dwg.draft
    FX, FZ = a.proj.front_x, a.proj.front_z
    right_ladder = FX(a.bb.max.X) + 2
    n = 0
    step = next((f for f in model.features if f.kind == "step_level"), None)
    levels = list(step.levels) if step is not None else []
    # Uniform staircase → one representative "N× rise" dim; else the per-step ladder
    # (legibility-gated). Turned parts have no levels, so neither fires.
    rep = _detect_step_repeat(levels, a.bb.min.Z, a.bb.max.Z) if levels else None
    if rep is not None:
        n_rep, rise = rep
        first = sorted(levels)[0]
        perp = tuple(sorted((FZ(a.bb.min.Z), FZ(first))))
        px = carve_free_position(dwg, a.fv_zones.right, "front", "x", _SLOT_DIM_STEP, perp)
        if px is not None:
            dwg.add(
                _dim(
                    (right_ladder, FZ(a.bb.min.Z), 0),
                    (right_ladder, FZ(first), 0),
                    "right",
                    px - right_ladder,
                    draft,
                    label=f"{n_rep}× {_fmt(rise)}",
                ),
                "dim_step_typ",
                view="front",
            )
            right_ladder = px
            n += 1
        else:
            ctx.record_issue(
                "error",
                "placement_unsatisfiable",
                "representative step-height dimension dropped (front-view right strip full)",
            )
    elif levels:
        kept, n_close = _legible_steps(levels, a.bb.min.Z, a.SCALE)
        if n_close:
            ctx.record_issue(
                "warning",
                "step_dim_dropped",
                f"{n_close} step height(s) too closely spaced to dimension at this scale "
                "(use a detail view)",
            )
            # First-class escalation alongside the lint code (ADR 0009 Amdt 1, #351
            # PR-4b) — `_request_prismatic_detail` (sections.py) triggers the detail-view
            # remedy on this instead of independently recomputing the same legibility
            # gate, which previously could queue a spurious detail even when the uniform-
            # staircase branch above already fully documented the part with one
            # representative dim (a real bug this routing fixes as a side effect).
            ctx.escalations.append(
                Escalation(kind="step", view="front", feature=step, reason="illegible")
            )
        for col, z in enumerate(kept):
            perp = tuple(sorted((FZ(a.bb.min.Z), FZ(z))))
            px = carve_free_position(dwg, a.fv_zones.right, "front", "x", _SLOT_DIM_STEP, perp)
            if px is None:
                ctx.record_issue(
                    "error",
                    "placement_unsatisfiable",
                    f"{len(kept) - col} step-height dimension(s) dropped "
                    "(front-view right strip full)",
                )
                break
            dwg.add(
                _dim(
                    (right_ladder, FZ(a.bb.min.Z), 0),
                    (right_ladder, FZ(z), 0),
                    "right",
                    px - right_ladder,
                    draft,
                    label=_fmt(z - a.bb.min.Z),
                ),
                f"dim_step_{col}",
                view="front",
            )
            right_ladder = px
            n += 1

    # Overall height — placed last so it sits OUTERMOST; suppressed for a Z-turned
    # part (its IR step-length chain already tiles the full height, ISO 129) and for
    # an X/Y rotational body (its Z extent IS the OD, dimensioned by render_rotational
    # — #222).
    rot = next((f for f in model.features if f.kind == "rotational"), None)
    od_is_height = rot is not None and rot.frame.axis in ("x", "y")
    suppress_height = (not include_overall) or model.orientation == "z" or od_is_height
    px = (
        None
        if suppress_height
        else carve_free_position(
            dwg,
            a.fv_zones.right,
            "front",
            "x",
            _SLOT_DIM_HEIGHT,
            tuple(sorted((FZ(a.bb.min.Z), FZ(a.bb.max.Z)))),
            outermost=True,
        )
    )
    if px is not None:
        dwg.add(
            _dim(
                (right_ladder, FZ(a.bb.min.Z), 0),
                (right_ladder, FZ(a.bb.max.Z), 0),
                "right",
                px - right_ladder,
                draft,
                label=_fmt(a.z_size),
            ),
            "dim_height",
            view="front",
        )
        n += 1
    elif not suppress_height:
        _log.warning("dim_height skipped: fv_zones.right strip full")
    return n


def render_step_positions(dwg, model, a, *, ctx) -> int:
    """Prismatic step POSITIONS (#555): where each shoulder sits along its axis,
    dimensioned from the part datum so a stepped block is fully constrained (the step
    heights alone leave the shoulder location implicit — two geometries draw the same
    sheet). A Y shoulder is a horizontal dim above the side (end) view (which maps Y
    horizontally, where the step profile reads); an X shoulder above the plan view —
    the same axis→view mapping the hole-location ladder uses. A shoulder whose strip is
    full drops with a lint code, not silently. Returns the count placed."""
    step = next((f for f in model.features if f.kind == "step_level"), None)
    if step is None or not step.shoulders:
        return 0
    draft = dwg.draft
    tier = draft.font_size + 2 * draft.pad_around_text
    n = 0
    counts: dict = {"x": 0, "y": 0}
    for axis, pos in sorted(step.shoulders):
        di = {"x": 0, "y": 1}[axis]
        datum = step.datum[di]
        val = abs(pos - datum)
        i = counts[axis]
        counts[axis] += 1
        if axis == "y":
            view, strip = "side", a.sv_zones.above
            p1 = dwg.at(view, a.bb.min.X, datum, a.bb.max.Z)
            p2 = dwg.at(view, a.bb.min.X, pos, a.bb.max.Z)
        else:  # x — shoulder along X → above the plan view
            view, strip = "plan", a.pv_zones.above
            p1 = dwg.at(view, datum, a.bb.max.Y, a.bb.min.Z)
            p2 = dwg.at(view, pos, a.bb.max.Y, a.bb.min.Z)
        edge = p1[1]
        name = f"dim_shoulder_{axis}{i}"

        def _build(pos, p1=p1, p2=p2, edge=edge, val=val):
            return _dim(
                (p1[0], edge, 0), (p2[0], edge, 0), "above", pos - edge, draft, label=_fmt(val)
            )

        def _drop(nm, val=val, view=view):
            ctx.record_issue(
                "warning",
                "step_position_dropped",
                f"step position {_fmt(val)} not dimensioned ({view} above-strip full)",
            )

        # ADR 0009 corridor candidate (#636): a shoulder position is a datum-referenced
        # location dim — force-kept in the datum-distance ladder, co-solving with the hole
        # locations that share this above-view strip (was a solver-invisible carve).
        # No cross-dedup against hole locations (dedup=None): a hole at the shoulder's exact
        # station sits on the full-span riser and suppresses the shoulder's own recognition,
        # so a live shoulder and a coincident hole-location dim never co-exist — there is no
        # duplicate to collapse, and deduping would only couple the shoulder to the hole's
        # lifecycle for no benefit.
        register_corridor(
            ctx,
            (view, "above"),
            strip,
            view,
            "y",
            tier,
            CorridorCandidate(
                name=name,
                build=_build,
                order=(_LOC_SUBCHAIN, val, name),
                on_place=lambda nm: None,
                on_drop=_drop,
                force=True,
                feature=step,
            ),
        )
        n += 1
    return n


def render_rotational(dwg, model, a, *, ctx) -> int:
    """Rotational furniture from the IR `RotationalFeature` (#237): the OD dim (above
    the front view), the rotation-axis centrelines (front + side), and the concentric
    bore leaders stacked to the left of the front view. Replaces the engine's inline
    OD / centreline / `ldr_z` blocks. Returns the count placed."""
    rot = next((f for f in model.features if f.kind == "rotational"), None)
    if rot is None:
        return 0
    draft = dwg.draft
    FX, FZ = a.proj.front_x, a.proj.front_z
    SX, SZ = a.proj.side_x, a.proj.side_z
    PX, PY = a.proj.plan_x, a.proj.plan_y
    n = 0
    od = rot.od
    axis = rot.frame.axis

    if axis == "z":
        # Vertical turning axis (the common case): OD across the top of the front
        # (profile) view; axis centrelines vertical on front + side.
        dwg.add(
            _dim(
                (FX(a.cx - od / 2), FZ(a.bb.max.Z) + 2, 0),
                (FX(a.cx + od / 2), FZ(a.bb.max.Z) + 2, 0),
                "above",
                8,
                draft,
                label=f"ø{_fmt(od)}",
            ),
            "dim_od",
            view="front",
        )
        n += 1
        dwg.add(
            Centerline((FX(a.cx), FZ(a.bb.min.Z) - 5, 0), (FX(a.cx), FZ(a.bb.max.Z) + 5, 0)),
            "centerline_front",
            view="front",
        )
        dwg.add(
            Centerline((SX(a.cy), SZ(a.bb.min.Z) - 5, 0), (SX(a.cy), SZ(a.bb.max.Z) + 5, 0)),
            "centerline_side",
            view="side",
        )

        # Concentric bore leaders to the left of the front view, centred on the axis.
        if rot.bores:
            left_edge = FX(a.bb.min.X)
            if left_edge - a.margin >= a.DIM_PAD:
                elbow_x = left_edge - a.DIM_PAD * 0.6
                pitch = max(10.0, draft.font_size * 3.0)
                # Bound the leader stack to the front-view height and space it via the shared
                # solve (#374). The old fixed `tip_z = cz + (i-(nb-1)/2)*pitch` had no bound,
                # so enough concentric bores overran the view (the CTC-02 defect shape). Each
                # leader keeps that same symmetric natural, so plan_strip reproduces the old
                # positions exactly when there is room (zero displacement) and only compresses /
                # drops (larger bore outranks smaller, priority=d) when the band is over capacity.
                nb = len(rot.bores)
                z_lo, z_hi = a.FV_Y - a.fv_hh, a.FV_Y + a.fv_hh
                cands = [
                    StripCandidate(
                        key=f"{i:03d}",
                        anchor=(elbow_x, FZ(a.cz) + (i - (nb - 1) / 2) * pitch),
                        size=(draft.font_size * 3, pitch),
                        priority=d,
                    )
                    for i, d in enumerate(rot.bores)
                ]
                placed = plan_strip(cands, z_lo, z_hi, pitch, axis="y").placed
                for i, d in enumerate(rot.bores):
                    tip_z = placed.get(f"{i:03d}")
                    if tip_z is None:
                        continue  # over the front-view capacity — dropped (ranked), logged below
                    dwg.add(
                        Leader(
                            tip=(FX(a.cx - d / 2), tip_z, 0),
                            elbow=(elbow_x, tip_z, 0),
                            label=f"ø{_fmt(d)}",
                            draft=draft,
                        ),
                        f"ldr_z{i}",
                        view="front",
                    )
                    n += 1
                dropped = [d for i, d in enumerate(rot.bores) if placed.get(f"{i:03d}") is None]
                for d in dropped:
                    ctx.coverage.drop_diam(
                        d
                    )  # exclude from coverage — else double-reported (#374 rev)
                if dropped:
                    ctx.record_issue(
                        "warning",
                        "callout_dropped",
                        f"{len(dropped)} concentric-bore diameter(s) {dropped} not annotated "
                        "(front-view height full) — use a detail view",
                    )
            else:
                _log.info(
                    "Additional diameters %s not annotated (insufficient left margin)",
                    list(rot.bores),
                )
    elif axis == "x":
        # Horizontal turning axis along X (#222): the OD is the Z extent — a vertical
        # ø dim left of the front (profile) view; axis centrelines run horizontally
        # through z=cz on front and y=cy on plan.
        dwg.add(
            _dim(
                (FX(a.bb.min.X) - 2, FZ(a.cz - od / 2), 0),
                (FX(a.bb.min.X) - 2, FZ(a.cz + od / 2), 0),
                "left",
                8,
                draft,
                label=f"ø{_fmt(od)}",
            ),
            "dim_od",
            view="front",
        )
        n += 1
        dwg.add(
            Centerline((FX(a.bb.min.X) - 5, FZ(a.cz), 0), (FX(a.bb.max.X) + 5, FZ(a.cz), 0)),
            "centerline_front",
            view="front",
        )
        dwg.add(
            Centerline((PX(a.bb.min.X) - 5, PY(a.cy), 0), (PX(a.bb.max.X) + 5, PY(a.cy), 0)),
            "centerline_plan",
            view="plan",
        )
    elif axis == "y":
        # Horizontal turning axis along Y (#222): the OD is the Z extent — a vertical
        # ø dim left of the side (profile) view; axis centrelines run horizontally
        # through z=cz on side and vertically through x=cx on plan.
        dwg.add(
            _dim(
                (SX(a.bb.min.Y) - 2, SZ(a.cz - od / 2), 0),
                (SX(a.bb.min.Y) - 2, SZ(a.cz + od / 2), 0),
                "left",
                8,
                draft,
                label=f"ø{_fmt(od)}",
            ),
            "dim_od",
            view="side",
        )
        n += 1
        dwg.add(
            Centerline((SX(a.bb.min.Y) - 5, SZ(a.cz), 0), (SX(a.bb.max.Y) + 5, SZ(a.cz), 0)),
            "centerline_side",
            view="side",
        )
        dwg.add(
            Centerline((PX(a.cx), PY(a.bb.min.Y) - 5, 0), (PX(a.cx), PY(a.bb.max.Y) + 5, 0)),
            "centerline_plan",
            view="plan",
        )
    return n


def _record_pmi_drop(ctx, dwg, ax, label, rec):
    """Record a PMI dim the layout could not place (#208).

    Previously silent (#351 PR-4a) — a PMI dim that found no strip space just
    vanished with no trace beyond a debug log line, unlike every other placer.
    Now records a warning-severity lint code plus a first-class ``Escalation``
    (ADR 0009 Amdt 1). No resolver remedy yet — purely additive visibility.

    *ax* is ``rec.dominant_axis`` (resolved, never ``"?"`` — see the bore-diameter
    call site). The view table differs by ``rec.pmi_kind``: a bore diameter/radius
    is placed in the view where the bore appears as a circle (Z→plan, X→side,
    Y→front — the bbox-perpendicular view), while a linear dim follows the
    dominant-axis table above (X/Z→front, Y→side primary). Conflating the two
    mislabels every dropped bore diameter/radius (review finding, #351 PR-4a).
    """
    if rec.pmi_kind in ("diameter", "radius"):
        view = {"Z": "plan", "X": "side", "Y": "front"}.get(ax, "front")
    else:
        view = "front" if ax in ("X", "Z") else "side"
    ctx.record_issue(
        "warning", "pmi_dropped", f"PMI {label!r} not placed (no room beside the {view})"
    )
    ctx.escalations.append(
        Escalation(kind="pmi", view=view, feature=rec, reason="no room beside the view")
    )


def _record_pmi_unrenderable(dwg, label, rec, *, ctx):
    """Record an authored dimension whose reference geometry can't form a witness (fewer
    than two distinct reference points, or a sub-legible span). Distinct from
    ``pmi_dropped`` (a *placement* failure): this is a *validation* failure, so a caller
    sees a specific reason instead of a misleading "no room" — an authored dim is only
    ``pmi_dropped`` after a real candidate reaches the corridor solver and cannot fit (#562)."""
    ctx.record_issue(
        "warning",
        "authored_dim_degenerate",
        f"authored dimension {label!r} has degenerate reference geometry (needs two "
        "distinct reference points spanning a legible distance)",
    )


def _bore_half_span(pmi_kind: str, value: float) -> float:
    """Half the perpendicular span of a bore-size dim from the bore centroid — the
    distance to each witness base point. A ``"diameter"`` record stores the full
    diameter (so half = radius = value/2); a ``"radius"`` record already stores the
    radius (half = value). Keyed on the drafting dimension category, NOT ``.kind`` (the
    IR feature kind) — the #360 bug used the latter, so the diameter branch was dead and
    every diameter dim spanned ±diameter (2× wide)."""
    return value / 2 if pmi_kind == "diameter" else value


# PMI is pre-authored manufacturing intent from the STEP file. When a strip is over
# capacity it should survive ahead of auto-generated dims (priority 0), like declared
# GD&T. It still lives in the outer run so it does not land between size/location dims.
_PMI_SUBCHAIN = 3
_PMI_CORRIDOR_PRIORITY = 1.0
_PMI_SLOT = 10.0  # mm — slot size for PMI dim lines in the strip


def _renderable_pmi_records(records):
    """PMI records the dimension renderer may place.

    Raw ``PmiFeature`` fallbacks can preserve unsupported AP242 records. Do not render those
    just because they happen to carry a numeric value and references; only drafting dimension
    categories belong in this placement path.
    """
    return [
        r
        for r in records
        if r.pmi_kind in AUTHORED_DIMENSION_KINDS and r.value > 0 and len(r.ref_pts) >= 2
    ]


def _bore_info(rec):
    """For Size_Diameter / Size_Radius records, return (bore_axis, cx, cy, cz).

    bore_axis is the bbox's LONGEST extent (the bore's depth direction).
    Reuses rec.dominant_axis set by extract_pmi; falls back to re-sorting
    the bbox spans only when dominant_axis is '?' (degenerate bbox).
    The diameter/radius is then placed perpendicular to the bore axis in the
    view where the bore appears as a circle.  Returns None if ref_bbox absent.
    """
    bb = rec.ref_bbox
    if bb is None:
        return None
    bore_axis = rec.dominant_axis
    if bore_axis == "?":
        xmin, ymin, zmin, xmax, ymax, zmax = bb
        spans = sorted(
            [("X", abs(xmax - xmin)), ("Y", abs(ymax - ymin)), ("Z", abs(zmax - zmin))],
            key=lambda t: t[1],
            reverse=True,
        )
        bore_axis = spans[0][0]
    cx_f = sum(p[0] for p in rec.ref_pts) / len(rec.ref_pts) if rec.ref_pts else 0.0
    cy_f = sum(p[1] for p in rec.ref_pts) / len(rec.ref_pts) if rec.ref_pts else 0.0
    cz_f = sum(p[2] for p in rec.ref_pts) / len(rec.ref_pts) if rec.ref_pts else 0.0
    return bore_axis, cx_f, cy_f, cz_f


def _pmi_witness_from_bbox(rec, view: str, a):
    """Witness points from the outer edges of the combined reference bbox.

    Gives the correct span for linear dims where both ref faces are flush
    (e.g. two parallel faces of a slot or step).  Not suitable for bore
    diameters — use _bore_info instead.

    When the record carries no ``ref_bbox`` (an authored ``Sheet.dimension()`` with
    only ``ref_pts``, #562), the span is derived from the ref points — so a ref_pts-only
    dimension renders instead of silently vanishing.
    """
    FX = a.proj.front_x
    FZ = a.proj.front_z
    SX = a.proj.side_x
    SZ = a.proj.side_z
    PX = a.proj.plan_x
    PY = a.proj.plan_y
    bb = rec.ref_bbox
    if bb is None:
        pts = rec.ref_pts
        if not pts or len(pts) < 2:
            return None
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        zs = [p[2] for p in pts]
        bb = (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))
    xmin, ymin, zmin, xmax, ymax, zmax = bb
    ax = rec.dominant_axis

    if view == "front" and ax == "X":
        p1 = (FX(xmin), FZ((zmin + zmax) / 2), 0)
        p2 = (FX(xmax), FZ((zmin + zmax) / 2), 0)
        avg_t = FZ((zmin + zmax) / 2)
    elif view == "front" and ax == "Z":
        p1 = (FX((xmin + xmax) / 2), FZ(zmin), 0)
        p2 = (FX((xmin + xmax) / 2), FZ(zmax), 0)
        avg_t = FX((xmin + xmax) / 2)
    elif view == "side" and ax == "Y":
        p1 = (SX(ymin), SZ((zmin + zmax) / 2), 0)
        p2 = (SX(ymax), SZ((zmin + zmax) / 2), 0)
        avg_t = SZ((zmin + zmax) / 2)
    elif view == "plan" and ax == "Y":
        avg_x = (xmin + xmax) / 2
        p1 = (PX(avg_x), PY(ymin), 0)
        p2 = (PX(avg_x), PY(ymax), 0)
        avg_t = PX(avg_x)
    else:
        return None

    span = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
    if span < 3:
        return None
    return p1, p2, avg_t


def _pmi_dim_spec(p1, p2, strip, label, name, view, side, draft):
    if strip is None:
        return None
    if side in ("above", "below"):
        axis = "y"
        perp = tuple(sorted((p1[0], p2[0])))
        witness = max(p1[1], p2[1]) + 2 if side == "above" else min(p1[1], p2[1]) - 2
        q1, q2 = (p1[0], witness, 0), (p2[0], witness, 0)
    else:
        axis = "x"
        perp = tuple(sorted((p1[1], p2[1])))
        witness = max(p1[0], p2[0]) + 2 if side == "right" else min(p1[0], p2[0]) - 2
        q1, q2 = (witness, p1[1], 0), (witness, p2[1], 0)
    lo, hi, _inner = strip_free_span(strip)
    if side in ("above", "right") and hi <= witness:
        return None
    if side in ("below", "left") and lo >= witness:
        return None

    def _build(pos, _q1=q1, _q2=q2, _side=side, _w=witness, _label=label):
        dist = pos - _w if _side in ("above", "right") else _w - pos
        return _dim(_q1, _q2, _side, dist, draft, label=_label)

    order_coord = min(perp)
    return {
        "name": name,
        "build": _build,
        "strip": strip,
        "view": view,
        "side": side,
        "axis": axis,
        "perp": perp,
        "order": (_PMI_SUBCHAIN, order_coord, name),
    }


def _pmi_leader_spec(tip, strip, label, name, view, side, draft):
    if strip is None:
        return None
    axis = "y" if side in ("above", "below") else "x"
    perp = (tip[0], tip[0]) if axis == "y" else (tip[1], tip[1])
    order_coord = tip[0] if axis == "y" else tip[1]

    def _build(pos, _tip=tip, _axis=axis, _label=label):
        elbow = (_tip[0], pos, 0) if _axis == "y" else (pos, _tip[1], 0)
        return Leader(_tip, elbow, _label, draft)

    return {
        "name": name,
        "build": _build,
        "strip": strip,
        "view": view,
        "side": side,
        "axis": axis,
        "perp": perp,
        "order": (_PMI_SUBCHAIN, order_coord, name),
    }


def _pmi_place_one(dwg, spec, rec):
    left = place_strip_candidates(
        dwg,
        spec["strip"],
        spec["view"],
        spec["axis"],
        [(spec["name"], spec["build"])],
        _PMI_SLOT,
        force=True,
        features={spec["name"]: rec},
        priorities={spec["name"]: _PMI_CORRIDOR_PRIORITY},
    )
    return not left


def _pmi_queue_options(dwg, ctx, options, ax, label, rec):
    specs = [s for s in options if s is not None]
    if not specs:
        return False
    primary, alternates = specs[0], specs[1:]

    def _drop(nm, _alts=alternates, _ax=ax, _label=label, _rec=rec):
        for alt in _alts:
            if _pmi_place_one(dwg, alt, _rec):
                _log.info(
                    "PMI dim %s placed on fallback %s/%s",
                    nm,
                    alt["view"],
                    alt["side"],
                )
                return
        _record_pmi_drop(ctx, dwg, _ax, _label, _rec)

    register_corridor(
        ctx,
        (primary["view"], primary["side"]),
        primary["strip"],
        primary["view"],
        primary["axis"],
        _PMI_SLOT,
        CorridorCandidate(
            name=primary["name"],
            build=primary["build"],
            order=primary["order"],
            on_place=lambda nm, _ax=ax, _label=label, _rec=rec: _log.info(
                "PMI dim %s %.3g → annotated (%s)", _ax, _rec.value, _label
            ),
            on_drop=_drop,
            priority=_PMI_CORRIDOR_PRIORITY,
            force=True,
            feature=rec,
        ),
    )
    return True


def _pmi_front_linear(dwg, a, ctx, rec, ax, label, name, primary, secondary, center):
    """An X- or Z-dominant linear PMI dim in the FRONT view (the two share one shape): the
    witness spans the ref bbox; place ``[primary, secondary]`` when the perpendicular
    midpoint sits on the primary side of the view centre, else fall back to ``[secondary]``
    alone. Returns True/False placed, or ``None`` for a degenerate (no-witness) reference
    so the caller can report it as a validation failure."""
    draft = dwg.draft
    wp = _pmi_witness_from_bbox(rec, "front", a)
    if wp is None:
        return None
    p1, p2, avg = wp
    zones = {
        "above": a.fv_zones.above,
        "below": a.fv_zones.below,
        "right": a.fv_zones.right,
        "left": a.fv_zones.left,
    }
    placed = False
    if avg >= center:
        placed = _pmi_queue_options(
            dwg,
            ctx,
            [
                _pmi_dim_spec(p1, p2, zones[primary], label, name, "front", primary, draft),
                _pmi_dim_spec(p1, p2, zones[secondary], label, name, "front", secondary, draft),
            ],
            ax,
            label,
            rec,
        )
    if not placed:
        placed = _pmi_queue_options(
            dwg,
            ctx,
            [_pmi_dim_spec(p1, p2, zones[secondary], label, name, "front", secondary, draft)],
            ax,
            label,
            rec,
        )
    return placed


def _place_pmi_record(dwg, a, ctx, rec, idx, bore_cfg, draft) -> bool:
    """Place one PMI record; returns True when it was queued/placed on a strip.

    The old per-record dispatch of ``render_pmi``: diameter/radius via ``bore_cfg`` (the
    ``_bore`` table), X/Z linears via the shared front-view shape, Y linears via the
    side-then-plan fallback. A degenerate reference is recorded unrenderable and returns
    False without escalating to the bottom drop.
    """
    ax = rec.dominant_axis
    label = rec.label
    placed = False
    name_x = f"pmi_x_{idx}"
    name_z = f"pmi_z_{idx}"
    name_y = f"pmi_y_{idx}"
    name_d = f"pmi_d_{idx}"

    if rec.pmi_kind in ("diameter", "radius"):
        # Bore size: centroid ± value/2 perpendicular to bore axis.
        info = _bore_info(rec)
        if info is None:
            _log.debug("PMI dim[%d] diam: no ref_bbox, skip", idx)
            return False
        bore_axis, cx_f, cy_f, cz_f = info
        # Resolved axis (handles _bore_info's '?' degenerate-bbox fallback); the diameter
        # view table (Z→plan, X→side, Y→front) differs from the linear-dim one (#351 PR-4a).
        ax = bore_axis
        half = _bore_half_span(rec.pmi_kind, rec.value)
        # Narrow bores (page span < text width) lead out to a shelf; bracket dims only
        # when the span fits the label. An unresolved axis matches no cfg → bottom drop.
        half_pg = half * a.SCALE  # bore radius on page (mm)
        cfg = bore_cfg.get(bore_axis)
        if cfg is not None:
            u, v = cfg["centre"](cx_f, cy_f, cz_f)
            if half_pg >= _MIN_INPLACE_BORE_HALF_MM:
                p1, p2 = cfg["span"](cx_f, cy_f, cz_f, half)
                placed = _pmi_queue_options(
                    dwg,
                    ctx,
                    [
                        _pmi_dim_spec(
                            p1, p2, cfg["zones"][s], label, name_d, cfg["view"], s, draft
                        )
                        for s in cfg["order"]
                    ],
                    ax,
                    label,
                    rec,
                )
            else:
                placed = _pmi_queue_options(
                    dwg,
                    ctx,
                    [
                        _pmi_leader_spec(
                            (u, v + (half_pg if s == "above" else -half_pg), 0),
                            cfg["zones"][s],
                            label,
                            name_d,
                            cfg["view"],
                            s,
                            draft,
                        )
                        for s in cfg["leader_order"]
                    ],
                    ax,
                    label,
                    rec,
                )

    elif ax == "X":
        placed = _pmi_front_linear(dwg, a, ctx, rec, ax, label, name_x, "above", "below", a.FV_Y)
        if placed is None:
            _log.debug("PMI dim[%d] X: degenerate reference", idx)
            _record_pmi_unrenderable(dwg, label, rec, ctx=ctx)
            return False

    elif ax == "Z":
        placed = _pmi_front_linear(dwg, a, ctx, rec, ax, label, name_z, "right", "left", a.FV_X)
        if placed is None:
            _log.debug("PMI dim[%d] Z: degenerate reference", idx)
            _record_pmi_unrenderable(dwg, label, rec, ctx=ctx)
            return False

    elif ax == "Y":
        # A degenerate reference (no witness in EITHER candidate view) is a validation
        # failure, not a placement one — report it distinctly (#562).
        if (
            _pmi_witness_from_bbox(rec, "side", a) is None
            and _pmi_witness_from_bbox(rec, "plan", a) is None
        ):
            _log.debug("PMI dim[%d] Y: degenerate reference", idx)
            _record_pmi_unrenderable(dwg, label, rec, ctx=ctx)
            return False
        # Try side view (Y maps to SX horizontal).
        wp = _pmi_witness_from_bbox(rec, "side", a)
        if wp is not None:
            p1, p2, avg_sz = wp
            if avg_sz >= a.SV_Y:
                placed = _pmi_queue_options(
                    dwg,
                    ctx,
                    [
                        _pmi_dim_spec(
                            p1, p2, a.sv_zones.above, label, name_y, "side", "above", draft
                        ),
                        _pmi_dim_spec(
                            p1, p2, a.sv_zones.below, label, name_y, "side", "below", draft
                        ),
                    ],
                    ax,
                    label,
                    rec,
                )
            if not placed:
                placed = _pmi_queue_options(
                    dwg,
                    ctx,
                    [
                        _pmi_dim_spec(
                            p1, p2, a.sv_zones.below, label, name_y, "side", "below", draft
                        )
                    ],
                    ax,
                    label,
                    rec,
                )
        # Fall back: plan view (Y maps to PY vertical).
        if not placed:
            wp = _pmi_witness_from_bbox(rec, "plan", a)
            if wp is not None:
                p1, p2, _ = wp
                placed = _pmi_queue_options(
                    dwg,
                    ctx,
                    [
                        _pmi_dim_spec(
                            p1, p2, a.pv_zones.below, label, name_y, "plan", "below", draft
                        )
                    ],
                    ax,
                    label,
                    rec,
                )

    if placed:
        return True
    _log.info("PMI dim[%d] %s %.3g → no viable strip", idx, ax, rec.value)
    _record_pmi_drop(ctx, dwg, ax, label, rec)
    return False


def render_pmi(dwg, model, a, *, ctx) -> int:
    """Render imported authored dimensions from concept IR as first-class candidates.

    AP242 dimensional PMI lowers to ``AuthoredDimension``; unsupported raw PMI fallback
    records still ride as ``PmiFeature`` so they remain visible to diagnostics (#208/#393).
    Replaces the engine's ``_annotate_pmi``.

    Called from ``_auto_annotate`` before ``drain_corridors`` so authored PMI
    co-solves with automatic strip candidates. Skips records whose page
    projection is degenerate (< 3 mm span).

    View assignment:
    - dominant X → front view, fv_zones.above / fv_zones.below
    - dominant Z → front view, fv_zones.right / fv_zones.left
    - dominant Y → side view, sv_zones.above / sv_zones.below
                   (falls back to pv_zones.below for Y dims that are
                    too compressed in the side view)
    """
    draft = dwg.draft
    pmi = [f for f in model.features if f.kind in ("authored_dimension", "pmi")]
    usable = _renderable_pmi_records(pmi)
    n_gtol = sum(1 for r in pmi if r.pmi_kind not in AUTHORED_DIMENSION_KINDS and r.value > 0)
    if n_gtol:
        _log.debug("PMI annotate: %d gtol/datum record(s) not yet annotatable (Phase 4)", n_gtol)
    if not usable:
        _log.info("PMI annotate: no usable records (value>0 with 2+ ref pts)")
        return 0

    FX = a.proj.front_x
    FZ = a.proj.front_z
    SX = a.proj.side_x
    SZ = a.proj.side_z
    PX = a.proj.plan_x
    PY = a.proj.plan_y

    # Per-bore-axis ø/R placement as DATA (ADR 0008 orientation-as-data): each bore reads as a
    # circle in ONE view, dimensioned across it in-plane when the page span fits the label, else
    # led out to a shelf. This one table replaces three near-identical Z/X/Y blocks. `order` is
    # the in-place above/below fallback; `leader_order` the narrow-bore one (Y historically
    # prefers below first). `centre`/`span` project the circle centre and its ±half endpoints.
    _bore: dict[str, dict[str, Any]] = {
        "Z": {
            "view": "plan",
            "zones": {"above": a.pv_zones.above, "below": a.pv_zones.below},
            "order": ("above", "below"),
            "leader_order": ("above", "below"),
            "centre": lambda cx, cy, cz: (PX(cx), PY(cy)),
            "span": lambda cx, cy, cz, h: ((PX(cx - h), PY(cy), 0), (PX(cx + h), PY(cy), 0)),
        },
        "X": {
            "view": "side",
            "zones": {"above": a.sv_zones.above, "below": a.sv_zones.below},
            "order": ("above", "below"),
            "leader_order": ("above", "below"),
            "centre": lambda cx, cy, cz: (SX(cy), SZ(cz)),
            "span": lambda cx, cy, cz, h: ((SX(cy - h), SZ(cz), 0), (SX(cy + h), SZ(cz), 0)),
        },
        "Y": {
            "view": "front",
            "zones": {"above": a.fv_zones.above, "below": a.fv_zones.below},
            "order": ("above", "below"),
            "leader_order": ("below", "above"),
            "centre": lambda cx, cy, cz: (FX(cx), FZ(cz)),
            "span": lambda cx, cy, cz, h: ((FX(cx - h), FZ(cz), 0), (FX(cx + h), FZ(cz), 0)),
        },
    }

    queued = 0
    for idx, rec in enumerate(usable):
        if _place_pmi_record(dwg, a, ctx, rec, idx, _bore, draft):
            queued += 1
    _log.info("PMI annotate: %d/%d dims queued", queued, len(usable))
    return queued


# GD&T aspect side-layer (ADR 0011 §4, #61) — declared feature control frames / datum
# feature symbols / surface finishes. Placed as first-class ADR 0009 corridor candidates,
# NOT through the dimension planner (their IR items carry no DimParameters). "note" is a
# free-text manufacturing note (#488) — the same leader-into-a-strip mechanism, glyph = text.
_GDT_KINDS = ("control_frame", "datum_ref", "finish", "note")
# Authored-intent run of the shared corridor ladder: GD&T frames tier BEYOND the
# feature-size (_SIZE_SUBCHAIN=0), datum-location (_LOC_SUBCHAIN=1), and overall
# envelope (_OVERALL_SUBCHAIN=2) dim runs, so a frame never lands mid-ladder among
# the dimensions it annotates.
_GDT_SUBCHAIN = 3
# Over-capacity survival rank for an authored GD&T frame (#357): a declared control frame /
# datum / finish / note is deliberate intent, so on a strip too full for every candidate it is
# kept over the auto dims (locations/slots, priority 0) rather than dropped by stacking-key order.
_GDT_CORRIDOR_PRIORITY = 1.0
# Minimum GD&T leader shaft length (page-mm). A zero-length Leader (site == solved tier)
# makes OCC's edge builder raise; nudging to this keeps `_build` total (#61 review).
_MIN_LEADER = 0.05


def _gdt_glyph(item, draft):
    """Build the ISO 1101/5459/1302 glyph sketch for one GD&T IR item at the origin
    (the :class:`Leader` repositions it). A fresh sketch per call — the leader translate
    must not alias a shared object across the strip solve's repeated probe builds."""
    if item.kind == "control_frame":
        return FeatureControlFrame(
            item.characteristic,
            item.tolerance,
            datums=item.datums,
            draft=draft,
            diameter=item.diameter,
            modifier=item.modifier,
        )
    if item.kind == "datum_ref":
        return DatumFeature(item.letter, draft=draft)
    if item.kind == "note":  # free-text manufacturing note (#488) — a single-line text glyph
        return TextBlock([item.text], position=(0.0, 0.0), draft=draft)
    return SurfaceFinish(item.ra, position=(0.0, 0.0), draft=draft)


def render_gdt(dwg, model, a, *, ctx) -> int:
    """Place declared GD&T frames / datum symbols / surface finishes (#61) as first-class
    ADR 0009 corridor candidates — registered into the SAME strip the feature's dimensions
    use, BEFORE ``drain_corridors``, so one solve orders and spaces them crossing-free with
    the dims. Each item carries its target ``(view, side)`` strip + model-space site; the
    leader hangs the glyph off the site into that strip. The strip footprint is the GLYPH's
    own box — NOT the leader+glyph box, whose shaft back to the feature would inflate the
    stacking extent (the same reason dims reserve one label-height). Cross-view separation
    is the compose-then-pack repack's job (ADR 0004): every placed frame is ``view=``-tagged,
    so ``_measure_blocks`` folds it into the block. Returns the count registered."""
    items = [f for f in model.features if f.kind in _GDT_KINDS]
    if not items:
        return 0
    draft = dwg.draft
    tier = draft.font_size + 2 * draft.pad_around_text
    # (zones, h-projector, v-projector, h-model-index, v-model-index) per view.
    views = {
        "plan": (a.pv_zones, a.proj.plan_x, a.proj.plan_y, 0, 1),
        "front": (a.fv_zones, a.proj.front_x, a.proj.front_z, 0, 2),
        "side": (a.sv_zones, a.proj.side_x, a.proj.side_z, 1, 2),
    }
    # The title block (bottom-right) is added AFTER drain_corridors, so strip placement can't
    # see it — a below/right strip runs down into its region. Its box is deterministic, so
    # reject any GD&T placement that would land on it (BOTH the primary corridor path, via the
    # candidate's `forbid`, AND the fallthrough) else the frame overlaps 'DRAWING' (#481 review).
    tb_box = _title_block_box(dwg, a)
    n = 0
    for i, item in enumerate(items):
        name = f"m_gdt{i}"
        vk = views.get(item.view)
        if vk is None or item.side not in ("above", "below", "left", "right"):
            ctx.record_issue(
                "warning", "gdt_dropped", f"{name}: bad target {item.view!r}/{item.side!r}"
            )
            continue
        zones, hproj, vproj, hi, vi = vk
        strip = getattr(zones, item.side)
        o = item.frame.origin
        px, py = hproj(o[hi]), vproj(o[vi])
        horizontal = item.side in ("above", "below")  # frame stacks along y
        axis = "y" if horizontal else "x"
        # The IR is public input (ADR 0011), so an invalid glyph spec (a mistyped
        # characteristic, a bad tolerance) must drop THIS item with a warning — never crash
        # the whole drawing build. The helper raises on a bad spec; catch it at the measure
        # (the first build) and drop. `_build` below re-runs `_gdt_glyph` with the same args
        # (so a spec error can't reappear there) AND is made total against the OTHER raise
        # source — a zero-length Leader shaft (see the min-leader guard in `_build`).
        try:
            gb = _gdt_glyph(item, draft).bounding_box().size
        except Exception as e:  # noqa: BLE001 — any glyph-spec error drops one item, not the build
            ctx.record_issue(
                "warning", "gdt_dropped", f"{name}: cannot render ({type(e).__name__}: {e})"
            )
            continue
        size = (gb.X, gb.Y)

        def _build(pos, _px=px, _py=py, _hz=horizontal, _it=item):
            g = _gdt_glyph(_it, draft)
            tip = (_px, _py)
            # A zero-length leader shaft (the projected site coincides with the solved tier —
            # `pos == py` above/below, `pos == px` left/right) makes OCC's edge builder raise,
            # which would crash the whole build on a public-IR declaration. Guarantee a
            # minimum shaft along the stacking axis (nudge outward; 0.05 mm is invisible) so
            # `_build` is total — the drop-don't-crash invariant holds for every build() call.
            if _hz:
                dy = pos - _py
                pos = (
                    pos if abs(dy) >= _MIN_LEADER else _py + math.copysign(_MIN_LEADER, dy or 1.0)
                )
                elbow = (_px, pos)
            else:
                dx = pos - _px
                pos = (
                    pos if abs(dx) >= _MIN_LEADER else _px + math.copysign(_MIN_LEADER, dx or 1.0)
                )
                elbow = (pos, _py)
            return Leader(tip=tip, elbow=elbow, label="", draft=draft, callout=g)

        def _drop(
            nm,
            _v=item.view,
            _s=item.side,
            _zones=zones,
            _px=px,
            _py=py,
            _hz=horizontal,
            _sz=size,
            _bld=_build,
            _feat=item.origin,
            _tb=tb_box,
        ):
            # Fallthrough (#481): the declared/derived side is full — try the OPPOSITE side of
            # the same view before dropping, so a congested default still places somewhere
            # legible rather than vanishing. Best-effort (mirrors render_slots' below-fallthrough):
            # carve a free tier on the alternate strip and place there; carve_free_position only
            # ever sees already-placed annotations, so it can't collide with a not-yet-drained
            # sibling corridor. Force semantics (no corridor-cross check) match the primary path,
            # BUT reject a spot over the (not-yet-placed) title block — a below/right strip runs
            # into it, and carve can't see it (#481 review).
            alt = {"above": "below", "below": "above", "left": "right", "right": "left"}[_s]
            alt_strip = getattr(_zones, alt, None)
            if alt_strip is not None:
                axis2 = "y" if alt in ("above", "below") else "x"
                extent = _sz[1] if axis2 == "y" else _sz[0]  # the glyph's stacking-axis size
                perp = (_px, _px + _sz[0]) if _hz else (_py - _sz[1] / 2, _py + _sz[1] / 2)
                pos = carve_free_position(dwg, alt_strip, _v, axis2, max(tier, extent), perp)
                if pos is not None:
                    dim = _bld(pos)
                    if not _box_hits(_anno_box(dim), (_tb,)):  # clear of the title block
                        dwg.add(dim, nm, view=_v, feature=_feat)  # placed on the alternate side
                        return
            ctx.record_issue(
                "warning",
                "gdt_dropped",
                f"{nm} not placed (no room in the {_v} {_s} strip or its opposite)",
            )

        register_corridor(
            ctx,
            (item.view, item.side),
            strip,
            item.view,
            axis,
            tier,
            CorridorCandidate(
                name=name,
                build=_build,
                order=(_GDT_SUBCHAIN, px if horizontal else py, name),
                on_place=lambda nm: None,
                on_drop=_drop,
                dedup=None,
                precedence=0,
                priority=_GDT_CORRIDOR_PRIORITY,  # authored intent outranks auto dims (#357)
                # A declared frame has no alternate view — force-keep (policy B) rather than
                # drop a user-authored annotation; only a physically full strip drops.
                force=True,
                feature=item.origin,  # provenance (ADR 0010): the decorated feature
                size=size,
                # Even a force-kept frame must not stack into the title block (#481 review) —
                # place_strip_candidates rejects a placement hitting this box, then on_drop's
                # fallthrough tries the other side.
                forbid=tb_box,
            ),
        )
        n += 1
    return n
