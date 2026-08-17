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
from dataclasses import dataclass, replace
from itertools import chain, islice, tee
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
    _END_ON,
    _EST_CHAR_WIDTH_EM,
    _MARGIN,
    _MIN_STEP_DIM_MM,
    _MIN_STEP_SEP_MM,
    _SLOT_DIM_DEPTH,
    _SLOT_DIM_HEIGHT,
    _SLOT_DIM_STEP,
    _SLOT_DIM_WIDTH,
    _WITNESS_LIFT_MM,
    DetailRequest,
    _annotation_diameter_sources,
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
    PRIORITY,
    CorridorCandidate,
    Escalation,
    _anno_box,
    _box_hits,
    _geom_box,
    _hole_location_coverage_fact,
    _same_location_ordinate,
    _with_hole_center_coverage,
    _with_hole_location_coverage,
    annotation_obstacle_boxes,
    carve_free_position,
    dim_footprint,
    full_strip_message,
    place_strip_candidates,
    register_corridor,
    strip_free_span,
    strip_obstacles,
)
from draftwright.annotations.leaders import (
    _GREEDY_MATERIAL_LOOKAHEAD,
    FeatureLeaderJob,
    _assign_by_view,
    collect_feature_leader,
    material_penalty_units,
    view_material,
)
from draftwright.layout import (
    _LEADER_ASSIGN_MAX_JOBS,
    StripCandidate,
    plan_strip,
)

# Re-exported: `annotations/holes.py` and the tests import the spec from here, and the
# renderer is its natural home from a caller's point of view even though the reading
# itself now lives in the IR waist so the page estimator can share it (#875 review).
# `hole_callout_spec` is re-exported: `annotations/holes.py` and the tests reach it here, and
# the renderer is its natural home from a caller's point of view — the READING moved down to the
# IR waist only so the page/scale estimator can share it instead of keeping a second copy that
# drifts (#875 review). `as` form so the re-export is deliberate, not an unused import.
from draftwright.model.callout import _first as _first
from draftwright.model.callout import hole_callout_spec as hole_callout_spec
from draftwright.model.compiled import FeatureRef, resolve_feature
from draftwright.model.ir import (
    AUTHORED_DIMENSION_KINDS,
    HoleFeature,
    PatternFeature,
    PocketFeature,
    SlotFeature,
)


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

    depth = f(spec["depth"])
    cbore_dia = f(spec["cbore_dia"])
    cbore_depth = f(spec["cbore_depth"])
    csink_dia = f(spec.get("csink_dia"))
    csink_angle = f(spec.get("csink_angle"))
    suffix = spec["suffix"]
    callout = HoleCallout(
        dia,
        count=count,
        through=spec["through"],
        depth=depth,
        cbore_dia=cbore_dia,
        cbore_depth=cbore_depth,
        csink_dia=csink_dia,
        # Every value crosses as a _fmt string (the #261 invariant) — a raw float renders
        # "90.0°" and, worse, mismatches the width estimators' `_fmt` (they'd under-reserve).
        # `.get()`: hand-built specs (tolerance/fit tests) omit csk keys.
        csink_angle=csink_angle,
        suffix=suffix,
        draft=draft,
    )
    # ``HoleCallout`` renders ISO symbols as geometry and consequently exposes an empty
    # helper-level label. Preserve an equivalent semantic string at this sole construction
    # seam so critique, audit and downstream tooling can identify what the geometry says.
    # Build it from the exact formatted arguments passed above: a second read from the model
    # or raw numbers here could drift from the visible callout (ADR 0015/0016).
    terms = []
    if count:
        terms.append(f"{count}×")
    terms.append(f"⌀{dia}")
    if spec["through"]:
        terms.append("THRU")
    elif depth is not None:
        terms.extend(("↧", depth))
    if cbore_dia is not None:
        terms.extend(("⌴", f"⌀{cbore_dia}"))
        if cbore_depth is not None:
            terms.extend(("↧", cbore_depth))
    if csink_dia is not None:
        terms.extend(("⌵", f"⌀{csink_dia}"))
        if csink_angle is not None:
            terms.extend(("×", f"{csink_angle}°"))
    if suffix:
        terms.append(suffix)
    callout.label = " ".join(terms)
    callout.measurements = tuple(spec.get("measurements", ()))
    callout.covers_hole_requirements = tuple(
        requirement
        for requirement, covered in (
            ("bore.through", spec["through"]),
            ("grouping.count", bool(count and count > 1)),
        )
        if covered
    )
    profile_coverage = spec.get("profile_coverage")
    callout.covers_profiles = () if profile_coverage is None else (profile_coverage,)
    callout.profile_boundary = spec.get("profile_boundary")
    return callout


def _record_slot_drop(ctx, dwg, kind, idx, view, feat, measurement=None):
    """Record a slot dim the layout could not place (#135).

    Info severity — a dim with no clear room is dropped as "place what fits",
    not an error. Alongside the lint code, appends a first-class ``Escalation``
    (ADR 0009 Amdt 1, #351 PR-4a) so the drop is object-visible too; slots have
    no natural grouping remedy like a recognised hole pattern, so no resolver
    consumes this yet — purely additive.
    """
    feature_kind = getattr(feat, "kind", None)
    noun = feature_kind if feature_kind in ("pad", "pocket") else "slot"
    ctx.record_issue(
        "info",
        f"{noun}_dim_dropped",
        f"{noun}{idx} {kind} dim not placed (no room beside the {view})",
        measurement=measurement,
    )
    ctx.escalations.append(
        Escalation(kind=noun, view=view, feature=feat, reason=f"no room beside the {view}")
    )


def render_slots(dwg, plan, a, *, ctx, only=None) -> int:
    """Dimension milled slots from the IR — width (the defining size, across
    ``width_axis``) + length (along ``long_axis``) + a position dim from the part
    datum, in the view the two axes span. Places through the engine's zone strips
    (shared infra, ADR 0008 Amend. 4); a dim with no clear room is dropped and
    recorded at info severity (place-what-fits). Sources slot `DimensionGroup`s
    from the plan; replaces the engine's `_annotate_slots`. Returns the count placed.

    Planner-fed (#730 / #698): the width + length VALUES and their tolerances come
    from the planner's ``DimParameter``s, bound explicitly by ``(role, kind)`` —
    ``("slot_width", "length")`` / ``("slot_length", "length")`` — never positionally.
    Formatting ``s.width``/``s.length`` directly dropped an authored tolerance (the
    #629 class). Placement mechanics are untouched: which dims register into the
    (view, "above") corridor vs place immediately, the ``on_drop`` below-strip
    fallthrough, the position-vs-hole-location dedup key, and the witness geometry
    (still the feature's raw fields) all behave as before. The datum POSITION dim
    stays model-derived (datum → ``s.lo`` is drawing state, not a feature parameter
    — `SlotFeature.parameters()` has no position param) and carries no tolerance.
    The pass KEEPS its own axis→view map (the view the slot's two in-plane axes
    span); ``g.view`` is ``_END_ON`` of the frame axis, which is not that view.

    ``only`` (a set of `SlotFeature`s, #426 Phase 2b) restricts placement to a recorded
    subset for ``finalize()``; ``only=None`` (the auto-pass) places all slots, byte-
    identically. Skips filtered slots **in place** so ``i`` stays the slot's model index
    (the ``m_slot{i}_*`` names must match the auto-pass — never re-enumerate a compacted
    list; `plan_dimensions` emits one group per slot in model order, so enumerating the
    slot groups preserves that index)."""
    slot_groups = plan.of_kind("slot", "pad", "pocket")
    if not slot_groups:
        return 0
    # Pocket location geometry is rendered in this in-plane pass, but its content
    # remains compiler-authoritative (ADR 0015/0016): datum and target come from the same
    # approved location consumed by render_locations, including non-Z openings.
    # Keyed by (feature, MEASURED axis): a non-Z pocket is approved one entry per in-plane
    # coordinate, so keying by feature alone would keep whichever came last.
    pocket_locations = {
        (loc.ref, loc.discriminator): loc
        for loc in plan.locations
        if loc.role == PocketFeature.LOCATION_STEM and loc.discriminator is not None
    }
    # A slot's own position dim — datum→near-end along its long axis. Compiled, not
    # computed from `a.bb`: it prints a number, so an authored set that does not name the
    # slot's location must not get one (#925).
    # Derived, not spelled: the name is a CONTRACT between the compiler that mints it and
    # this renderer that reads it, and until #966 neither end derived it — so renaming the
    # declaration silently dropped every slot location dim rather than renaming it.
    slot_positions = {
        loc.ref: loc for loc in plan.locations if loc.role == SlotFeature.LOCATION_STEM
    }
    draft = dwg.draft
    tier = draft.font_size + 2 * draft.pad_around_text
    views = {
        frozenset("xy"): ("plan", a.pv_zones, "x", a.proj.plan_x, "y", a.proj.plan_y),
        frozenset("xz"): ("front", a.fv_zones, "x", a.proj.front_x, "z", a.proj.front_z),
        frozenset("yz"): ("side", a.sv_zones, "y", a.proj.side_x, "z", a.proj.side_z),
    }

    count = 0
    kind_indices: dict[str, int] = {}
    only_refs = None if only is None else {FeatureRef(f) for f in only}
    for g in slot_groups:
        # The slot object supplies WITNESS GEOMETRY only — `lo`/`hi`/`w_center`/`width` fix
        # where the extension lines land, exactly as a centre mark is sized by its hole
        # (#875). Every PRINTED value below comes from an approved entry.
        s = resolve_feature(g.ref)
        i = kind_indices.get(s.kind, 0)
        kind_indices[s.kind] = i + 1
        if only_refs is not None and g.ref not in only_refs:
            continue  # #426 Ph2b: skip in place — i must stay the model index
        view = views[frozenset((s.width_axis, s.long_axis))]
        name, zones, h_axis, h_proj, _v_axis, v_proj = view

        def _place(
            meas_axis,
            p_lo,
            p_hi,
            perp_lo,
            perp_hi,
            approved,
            kind,
            anchor="center",
            sfx="",
            vw=view,
            zn=zones,
            ha=h_axis,
            hp=h_proj,
            vp=v_proj,
            idx=i,
        ):
            # The rendered label is the APPROVED entry's own text plus its pre-formatted
            # tolerance suffix (#730 planner-authoritative, #925 compiler-formatted): the
            # renderer never turns a number into printed text of its own.
            lbl = approved.value_text + sfx
            # Raw (pre-snap) endpoints — the dedup key must share a basis with the
            # hole-location key (which uses the raw ref), else the ~0.05 mm snap gap can
            # push a coincident span into an adjacent 0.1 mm page bin and the #345
            # duplicate survives.
            raw_lo, raw_hi = p_lo, p_hi
            # Snap the geometric span to the displayed (1-dp) value so drawn length
            # matches the label (else label-vs-measured lint trips).
            disp = float(approved.value_text)
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
            prefix = s.kind if s.kind in ("pad", "pocket") else "slot"
            cname = f"m_{prefix}{idx}_{kind}"

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
                    lambda pos, _el=e_lo, _eh=e_hi, _s=side, _w=witness, _l=lbl: _dim(
                        _el, _eh, _s, abs(pos - _w), draft, label=_l
                    ),
                )

            # Register into the corridor batch (ADR 0014 collect-then-solve). One solve
            # per strip dedups a POSITION line coincident with a hole location (#345),
            # orders size + location as segregated monotonic runs (#346), and — the part
            # that matters here — arbitrates against every other occupant by `priority`
            # when the strip is over capacity.
            #
            # #894: the FRONT view used to *commit* instead, via a direct
            # `place_strip_candidates` loop — a local solve that spaces only its own
            # call's candidates and then takes the space. Whichever pass ran first won,
            # so #888's pocket location dims filled the front-right strip and the height
            # ladder, registered later, found it gone: both `dim_step_0` and `dim_height`
            # dropped on CTC-03/04. Registering is most of the fix; the ladder also needed
            # a priority, since once co-solved it merely TIED with the pocket dims and lost
            # on the generated key. (`_MANDATORY_OVERALL_PRIORITY` is the envelope dims'
            # rank, not this ladder's — the base trace records both height candidates at 0.)
            #
            # SCOPED TO THE FRONT VIEW deliberately. The plan/side right-left case keeps
            # the immediate path, because the corridor carve is strictly more conservative:
            # it marks a region obstructed from FULL annotation geometry, where a label can
            # legitimately sit between two extension lines. Routing those dims through it
            # cost #885's part two pad size dims that place cleanly today — verified by
            # label-box measurement, not inferred. Closing that gap needs the carve to
            # reason about label boxes, which is its own change; tracked on #894.
            # The plan/side horizontal case has been corridor-routed since #345/#346 and
            # stays that way; the front view joins it here. Only plan/side right-left
            # keeps the immediate path.
            use_corridor = vw[0] == "front" or (meas_axis == ha and vw[0] in ("plan", "side"))
            if not use_corridor:
                for side, strip, hi in sides:
                    if strip is None:
                        continue
                    axis = "y" if side in ("above", "below") else "x"
                    if not place_strip_candidates(
                        dwg,
                        strip,
                        vw[0],
                        axis,
                        [_cand_for(side, hi)],
                        tier,
                        ctx=ctx,
                        features={cname: s},
                        measurements={cname: approved.id},  # #1002
                        trace=ctx.trace,
                        trace_label=f"slot_{side}",
                    ):
                        return True
                return False

            is_pos = kind.startswith("pos")
            drop_word = "position" if is_pos else kind
            near_side, near_strip, near_hi = sides[0]
            far_side, far_strip, far_hi = sides[1]
            corridor_axis = "y" if near_side in ("above", "below") else "x"

            def _far_or_drop(
                nm,
                _fs=far_strip,
                _fsd=far_side,
                _fh=far_hi,
                _ax=corridor_axis,
                _feat=s,
                _dw=drop_word,
            ):
                # Opposite-strip fallthrough. On the FRONT view — the path this change
                # adds — it is DEFERRED to ctx.post_drain so it runs after every corridor
                # has drained (the #684 rule, and the shape `render_plates` / `render_gdt`
                # use): placing mid-drain could occupy space a later sibling's force
                # candidate needs, since that sibling has not solved yet.
                #
                # The plan/side path keeps placing synchronously, as it has since
                # #345/#346. Deferring it too is the architecturally consistent end state
                # and was tried — it costs #885's part a pad length dim, because by the
                # time a post-drain retry runs the opposite strip has filled. Changing
                # behaviour that was not broken, at a measured cost, is not this PR's job;
                # the migration is tracked on #894.
                def _retry(_fs=_fs, _fsd=_fsd, _fh=_fh, _ax=_ax, _feat=_feat, _dw=_dw) -> None:
                    if _fs is not None and not place_strip_candidates(
                        dwg,
                        _fs,
                        vw[0],
                        _ax,
                        [_cand_for(_fsd, _fh)],
                        tier,
                        ctx=ctx,
                        features={cname: _feat},
                        measurements={cname: approved.id},  # #1002
                        trace=ctx.trace,
                        trace_label=f"slot_{_fsd}_fallthrough",
                    ):
                        return  # placed on the opposite strip
                    _record_slot_drop(ctx, dwg, _dw, idx, vw[0], _feat, approved.id)

                if vw[0] == "front":
                    ctx.post_drain.append(_retry)
                else:
                    _retry()

            if near_strip is None:
                # Nothing to register against; the opposite side is the only chance.
                _far_or_drop(cname)
                return True

            register_corridor(
                ctx,
                (vw[0], near_side),
                near_strip,
                vw[0],
                corridor_axis,
                tier,
                CorridorCandidate(
                    name=cname,
                    build=_cand_for(near_side, near_hi)[1],
                    # A position nests in the datum-distance location ladder; a size dim
                    # forms the inner run, ordered left-to-right by its span midpoint.
                    order=(
                        (_LOC_SUBCHAIN, disp, cname)
                        if is_pos
                        else (_SIZE_SUBCHAIN, (p_lo + p_hi) / 2, cname)
                    ),
                    on_place=lambda nm: None,
                    on_drop=_far_or_drop,
                    measurement=approved.id,  # #1002
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

        # Bind each approved dim explicitly by (role, kind) — never positionally (#730).
        role_prefix = s.kind if s.kind in ("pad", "slot") else ""
        wpd = g.dim(role=f"{role_prefix}_width", kind="length")
        lpd = g.dim(role=f"{role_prefix}_length", kind="length")
        half = s.width / 2
        if wpd is not None:
            if _place(
                s.width_axis,
                s.w_center - half,
                s.w_center + half,
                s.lo,
                s.hi,
                wpd,
                "width",
                sfx=_tol_suffix(wpd.tolerance, draft),
            ):
                count += 1
            else:
                _record_slot_drop(ctx, dwg, "width", i, name, s, wpd.id)
        if lpd is not None:
            if _place(
                s.long_axis,
                s.lo,
                s.hi,
                s.w_center - half,
                s.w_center + half,
                lpd,
                "length",
                sfx=_tol_suffix(lpd.tolerance, draft),
            ):
                count += 1
            else:
                _record_slot_drop(ctx, dwg, "length", i, name, s, lpd.id)
        # Pads are located by the compiled location set on both axes; slots retain their
        # historical single-axis position dimension here.
        pos = slot_positions.get(g.ref)
        if pos is not None and pos.value * a.SCALE >= 1.0:
            axis_i = "xyz".index(s.long_axis)
            if _place(
                s.long_axis,
                pos.span[0][axis_i],
                pos.span[1][axis_i],
                s.w_center - half,
                s.w_center + half,
                pos,
                "pos",
                anchor="lo",
            ):
                count += 1
            else:
                _record_slot_drop(ctx, dwg, "position", i, name, s)
        elif s.kind == "pocket" and s.frame.axis != "z":
            # Side-/front-opening pockets need two in-plane coordinates in their
            # end-on view.  The compiler approves one entry PER coordinate, each with its
            # own value and span; Z-opening pockets use render_locations' X(plan)/Y(side)
            # ladder instead.
            width_lo, width_hi = s.w_center - half, s.w_center + half
            for axis, perp_lo, perp_hi, kind in (
                (s.long_axis, width_lo, width_hi, "pos_long"),
                (s.width_axis, s.lo, s.hi, "pos_width"),
            ):
                entry = pocket_locations.get((FeatureRef(s), axis))
                if entry is None:
                    continue  # not approved
                index = "xyz".index(axis)
                start, end = entry.span[0][index], entry.span[1][index]
                if abs(end - start) * a.SCALE < 1.0:
                    continue
                if _place(axis, start, end, perp_lo, perp_hi, entry, kind, anchor="lo"):
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
_MANDATORY_OVERALL_PRIORITY = PRIORITY.MANDATORY
_PRINCIPAL_CHAIN_PRIORITY = PRIORITY.PRINCIPAL

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
    measurement=None,
    pinned=False,
    footprint=None,
    location_coverage=(),
    hole_requirements=(),
):
    """A :class:`CorridorCandidate` for a datum-referenced hole/pattern location dim.
    Location dims outrank a coincident slot-position line in dedup (#345) and form the
    outer, datum-distance-ordered run of the ladder (#346). Force-kept (policy B): a plan-X
    / side-Y location has no alternate view, so a corridor block keeps it rather than drops
    it; only a physically full strip drops (``location_ref_dropped`` → hole-table escalate)."""

    def _placed(nm):
        # Only loose HoleFeatures have rows in the automatic scattered-hole table.
        # Pattern locations remain documented by their own dimensions/furniture; marking
        # them replaceable lets an unrelated successful table silently delete their
        # provenance (#1143 adversarial review).
        if getattr(feature, "kind", None) == "hole":
            ctx.coverage.cover_scattered_hole_doc(nm)
        if pinned:
            dwg.pin(nm)

    def _drop(nm):
        edge = "plan view" if view == "plan" else "side view"
        ctx.record_issue(
            "warning",
            "location_ref_dropped",
            f"{nm} not placed (no room above the {edge})",
            measurement=measurement,
            hole_requirements=hole_requirements,
        )
        ctx.escalations.append(Escalation("location", view, nm, "strip_full"))

    return CorridorCandidate(
        name=name,
        build=lambda pos: _with_hole_location_coverage(build(pos), location_coverage),
        order=(_LOC_SUBCHAIN, distance, name),
        # A placed location may later be replaced by the scattered-hole table (#351 PR-4c).
        on_place=_placed,
        on_drop=_drop,
        dedup=(view, span_key[0], span_key[1]),
        precedence=3 if pinned else 2,
        priority=PRIORITY.MANDATORY if pinned else PRIORITY.AUTO,
        force=True,
        feature=feature,  # provenance (ADR 0010): the located hole/pattern
        measurement=measurement,  # which of its measurements this is (#1002)
        footprint=footprint,  # analytical measure — no probe build (#602)
    )


def render_locations(dwg, plan, a, *, ctx, only=None, pinned=None) -> int:
    """Baseline X/Y hole-location dims from the compiled plan (#238). The compiler decides
    the content (`plan.locations`: which refs survived, from which datum); this renderer owns
    the layout (Amendment 4) — X dims tier above the plan view, Y dims above the side
    view, nearest-datum-first, legibility-gated, allocated from the existing strips;
    a ref with no room is dropped as `location_ref_dropped`. Replaces the engine's
    `_add_location_dims`. Returns the count placed.

    Reads `plan.locations`, not `plan_locations(model)`: a location prints a number, so an
    authored set that does not name one must not get one, and the only way to guarantee
    that for every renderer at once is for the withheld entries never to arrive (#925).
    Before this the pass took the raw planner list and drew every ref regardless — an
    authored set naming a single bore still produced its X/Y position dims.

    *only*, when given, restricts placement to refs whose source feature is in the set —
    the #426 finalize() path passes the recorded ``locate`` intents' features so the
    corridor solve runs over the user's edited subset. ``None`` (the auto-pass) places
    every ref, byte-identically.

    *pinned* carries the #511 first slice: deferred user ``locate(..., pin=True)`` calls
    remain first-class corridor candidates, but get higher survival/dedup priority and
    pin their placed names instead of being hand-added after the solve."""
    approved = plan.locations
    if not approved:
        return 0
    draft = dwg.draft
    datum_x, datum_y = approved[0].span[0][0], approved[0].span[0][1]
    only_refs = None if only is None else {FeatureRef(f) for f in only}
    refs = []
    for loc in approved:
        if only_refs is not None and loc.ref not in only_refs:  # #426: recorded subset only
            continue
        # This ladder is specifically plan-X / side-Y for Z-normal features.
        # Non-Z pockets are still compiler-backed, but their two in-plane spans
        # are rendered in their end-on view by render_slots.
        if loc.axis != "z":
            continue
        rx, ry = loc.span[1][0], loc.span[1][1]
        # A rotational part's on-axis (concentric) *hole* bore is located by the
        # centreline, not a position dim (matches the engine's feature_holes
        # filter). A pattern ref (role "location_pattern" — e.g. a bolt-circle
        # centre) is NOT filtered, even on the axis. A renderer-side filter only ever
        # REMOVES an approved entry (a drop), so it does not breach the boundary.
        if (
            loc.role == HoleFeature.LOCATION_STEM
            and a.is_rotational
            and _concentric_with_axis(a, rx, ry)
        ):
            continue
        # Provenance (ADR 0010): the located feature. `resolve_feature` is the sanctioned
        # seam for exactly this — the corridor's feature map keys drop()/annotations_of().
        # `loc.id` rides along as the measurement identity (#1002): the compiler already
        # minted it for this very entry, so the renderer records WHICH measurement it drew
        # rather than leaving the audit to infer it from the annotation's name.
        refs.append(
            (
                rx,
                ry,
                resolve_feature(loc.ref),
                loc.id,
                loc.discriminator,
                _hole_location_coverage_fact(loc),
            )
        )
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
            if _same_location_ordinate(r[0], u[0]):
                u[3] = u[3] or r[2] in pinned_set
                # Collapsing coincident Xs into one dim must ACCUMULATE what it draws
                # (#1002 r4): the survivor genuinely measures every collapsed feature's X.
                if r[4] in (None, "x") and r[3] is not None and r[3] not in u[4]:
                    u[4].append(r[3])
                if r[4] in (None, "x") and r[3] is not None:
                    fact = r[5]
                    if fact not in u[5]:
                        u[5].append(fact)
                break
        else:
            x_refs.append(
                [
                    r[0],
                    r[1],
                    r[2],
                    r[2] in pinned_set,
                    [r[3]] if r[3] is not None and r[4] in (None, "x") else [],
                    [r[5]] if r[3] is not None and r[4] in (None, "x") else [],
                ]
            )
    _x_drawable = {r[0] for r in x_refs if abs(r[0] - datum_x) * a.SCALE >= 1.0}
    _kept_x, _n_x_close = _legible_locations(_x_drawable, a.SCALE)
    if _n_x_close:
        dropped_x = [r for r in x_refs if r[0] in _x_drawable and r[0] not in set(_kept_x)]
        ctx.record_issue(
            "warning",
            "location_ref_dropped",
            f"{_n_x_close} X location dim(s) too closely spaced to dimension legibly "
            "(use a detail view)",
            measurement=tuple(mid for ref in dropped_x for mid in ref[4]),
            hole_requirements=tuple(
                (feature, parameter) for ref in dropped_x for feature, parameter, _point in ref[5]
            ),
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
    for i, (rx, ry, feat, pin_ref, mids, location_facts) in enumerate(
        sorted(x_refs, key=lambda r: abs(r[0] - datum_x))
    ):
        if abs(rx - datum_x) * a.SCALE < 1.0:
            continue  # on the datum edge — nothing to dimension
        n += 1
        # A single X-location dim shared by two *distinct* features at this X belongs to
        # neither exclusively — leave it unowned so drop() cannot over-strip a sibling's
        # dimension and annotations_of never over-claims it (review #406, ADR 0010).
        _shared_x = any(_same_location_ordinate(o[0], rx) and o[2] != feat for o in refs)
        _xfeat = None if _shared_x else feat
        # The measurement does NOT follow the feature (#1002 r4). Feature-unowned is an
        # ADR 0010 *ownership* rule — it stops drop(feature) stripping a sibling's dim. It
        # says nothing about what the dim measures, and a shared dim measures BOTH features'
        # X location. The first cut dropped the id here as though naming one feature were the
        # only option; recording all of them is both true and exactly what the tuple-valued
        # channel exists for (ADR 0016 / #886). Discarding it made the audit blind on an
        # ordinary dedup path.
        # One ADR 0016 feature-level location identity per collapsed owner; the structured
        # location facts below carry that this particular visible member is X (#883).
        _xmid = tuple(mids)
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
                measurement=_xmid,
                location_coverage=location_facts,
                hole_requirements=tuple(
                    (feature, parameter) for feature, parameter, _point in location_facts
                ),
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
            if _same_location_ordinate(r[1], u[1]):
                u[3] = u[3] or r[2] in pinned_set
                if r[4] in (None, "y") and r[3] is not None and r[3] not in u[4]:
                    u[4].append(r[3])  # accumulate, as in the X loop (#1002 r4)
                if r[4] in (None, "y") and r[3] is not None:
                    fact = r[5]
                    if fact not in u[5]:
                        u[5].append(fact)
                break
        else:
            y_refs.append(
                [
                    r[0],
                    r[1],
                    r[2],
                    r[2] in pinned_set,
                    [r[3]] if r[3] is not None and r[4] in (None, "y") else [],
                    [r[5]] if r[3] is not None and r[4] in (None, "y") else [],
                ]
            )
    _y_drawable = {r[1] for r in y_refs if abs(r[1] - datum_y) * a.SCALE >= 1.0}
    _kept_y, _n_y_close = _legible_locations(_y_drawable, a.SCALE)
    if _n_y_close:
        dropped_y = [r for r in y_refs if r[1] in _y_drawable and r[1] not in set(_kept_y)]
        ctx.record_issue(
            "warning",
            "location_ref_dropped",
            f"{_n_y_close} Y location dim(s) too closely spaced to dimension legibly "
            "(use a detail view)",
            measurement=tuple(mid for ref in dropped_y for mid in ref[4]),
            hole_requirements=tuple(
                (feature, parameter) for ref in dropped_y for feature, parameter, _point in ref[5]
            ),
        )
        ctx.escalations.append(Escalation("location", "side", None, "illegible"))
    _kept_y_set = set(_kept_y)
    y_refs = [r for r in y_refs if r[1] not in _y_drawable or r[1] in _kept_y_set]
    # Cap the side-above strip below the iso view so Y-location dims never run under it
    # (the carve respects outer_limit); the dim_pitch_side dims are obstacles the carve
    # avoids structurally, retiring the old manual allocate(10.0) reservation + cursor.
    if y_refs and any(SX(ry) + 10 > iso_x0 - 4 for _, ry, _feat, _pin, _mids, _facts in y_refs):
        a.sv_zones.above.outer_limit = min(a.sv_zones.above.outer_limit, iso_y0 - 4)
    for i, (rx, ry, feat, pin_ref, mids, location_facts) in enumerate(
        sorted(y_refs, key=lambda r: abs(r[1] - datum_y))
    ):
        if abs(ry - datum_y) * a.SCALE < 1.0:
            continue
        n += 1
        # Shared-Y location dim → unowned (see the X loop; review #406).
        _shared_y = any(_same_location_ordinate(o[1], ry) and o[2] != feat for o in refs)
        _yfeat = None if _shared_y else feat
        # Every collapsed feature-level location; the structured facts carry Y (see X above).
        _ymid = tuple(mids)
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
                measurement=_ymid,
                location_coverage=location_facts,
                hole_requirements=tuple(
                    (feature, parameter) for feature, parameter, _point in location_facts
                ),
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


def render_centermarks(dwg, furniture_groups, *, ctx) -> int:
    """A centre mark on every hole (plain holes + each pattern member), in the view
    normal to the hole's axis (`_END_ON`), sized by its diameter — the IR migration
    of the engine's inline centre-mark loop. Returns the count placed.

    The size comes off the FEATURE, not the planned bore parameter (ADR 0016 / #875 review).
    A centre mark is furniture derived from the hole's physical size; it is not a displayed
    value, so suppressing the bore dimension must not shrink it. Reading the parameter here
    made a suppressed ⌀20 collapse from a 42 mm mark to the 2.5 mm floor — the governing rule
    (facts live on the feature, parameters carry display values) applied to geometry rather
    than to text."""
    n = 0
    for g in furniture_groups:
        feat = g.feature
        if not isinstance(feat, HoleFeature | PatternFeature):
            continue
        hole = feat.member if isinstance(feat, PatternFeature) else feat
        dia = hole.diameter or 0.0
        size = max(2.5, dia * dwg.scale + 2.0)
        view = _END_ON.get(feat.frame.axis, "plan")
        members = feat.members or (g.anchor,)
        for loc in members:
            px, py, *_ = dwg.at(view, *loc)
            ctx.place(
                _with_hole_center_coverage(
                    CenterMark((px, py, 0), size, dwg.draft), feat, loc, view
                ),
                f"m_cm{n}",
                view=view,
                feature=feat,
            )
            n += 1
    return n


def _mentioned_diameters(dwg) -> set[float]:
    """Diameters already called out on the drawing.

    Structured ``covers_diameters`` is authoritative when present; only genuinely
    unstructured labels are parsed.  A geometric hole callout's semantic label may also
    contain a bolt-circle or grid suffix diameter, which locates the pattern but does not
    cover a physical feature of that diameter (#1142 / ADR 0017).
    """
    diams: set[float] = set()
    for _, ann in dwg.iter_annotations():
        if isinstance(ann, TitleBlock):
            continue
        structured_diameters, text_diameters = _annotation_diameter_sources(ann)
        diams.update(structured_diameters)
        diams.update(text_diameters)
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


def _diameter_row_below(dwg, items, start: int = 0, trace=None, *, ctx) -> int:
    """ø-callout row BELOW the front view for X-turned step/boss diameters (#77).
    *items* is ``[(anchor, diameter), ...]``. The row is dropped clear of anything
    already below the profile; labels spread along page-x by the ADR-0003 strip
    solve. Skips (returns 0) if there is no room — the diameters then surface as
    ``feature_not_dimensioned``. *trace* (#736): one ``pass_events`` record with a
    placed/dropped item per callout."""
    if not items:
        return 0
    ev = trace.pass_event("diameter_row_below", view="front") if trace is not None else None
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
        if ev is not None:
            ev["items"].extend(
                {"label": f"ø{_fmt(d)}", "outcome": "dropped", "reason": "no_room_below"}
                for _, d, _, _, _ in items
            )
        return 0
    specs = []  # (tip_page, dia, label, feature), tip on the step's bottom silhouette,
    for anchor, dia, feat, dtol, thr in items:  # centred along the feature's length (not a corner)
        ax, ay, az = anchor
        tip = dwg.at("front", ax, ay, az - dia / 2)
        label = f"ø{_fmt(dia)}{_tol_suffix(dtol, draft)}" + (f" {thr}" if thr else "")  # #859
        specs.append((tip, dia, label, feat))
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
    _SHELF = draft.pad_around_text  # helpers Leader shelf_len = gap = draft.pad_around_text

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
    if ev is not None:  # the specs the fit solve squeezed out (#298 smallest-first drops)
        kept = {id(s) for s in survivors}
        ev["items"].extend(
            {"label": s[2], "outcome": "dropped", "reason": "squeezed_out"}
            for s in specs
            if id(s) not in kept
        )
    for i, ((tip, dia, label, feat), lx) in enumerate(zip(survivors, xs, strict=True)):
        ctx.place(
            Leader(tip=(tip[0], tip[1], 0), elbow=(lx, label_y, 0), label=label, draft=draft),
            f"m_dia_x{start + i}",
            view="front",
            feature=feat,
        )
        if ev is not None:
            ev["items"].append(
                {
                    "name": f"m_dia_x{start + i}",
                    "label": label,
                    "outcome": "placed",
                    "pos": [lx, label_y],
                }
            )
    return len(survivors)


def _diameter_column_left(dwg, items, start: int = 0, trace=None, *, ctx) -> int:
    """ø-callout column to the LEFT of the front view for Z-turned step/boss
    diameters (#131) — the page-Y mirror of the row-below. A per-label occupancy
    gate drops only a label that would overprint a bore leader / existing callout
    sharing the left region (#144), never the whole column. Returns the count placed.
    *trace* (#736): one ``pass_events`` record with a placed/dropped item per callout."""
    if not items:
        return 0
    ev = trace.pass_event("diameter_column_left", view="front") if trace is not None else None
    draft = dwg.draft
    fx0, fy0, _, fy1 = dwg.view_bounds("front")
    # Real measured width, not the per-char estimate (#859): a thread spec is arbitrary text,
    # so `len * 0.62 em` can underestimate a wide label and let it cross the margin
    # (annotation_out_of_bounds). Measure the completed label like the row-below path does.
    label_w = max(
        _text_size(
            f"ø{_fmt(dia)}{_tol_suffix(dtol, draft)}" + (f" {thr}" if thr else ""),
            draft.font_size,
            getattr(draft, "font_path", DEFAULT_FONT_PATH),
            getattr(draft, "font", "Arial"),
        )[0]
        for _, dia, _, dtol, thr in items
    )
    elbow_x = fx0 - (draft.font_size + 2 * draft.pad_around_text)
    # A left-directed leader hangs its label a shelf-length PAST the elbow, so the label's left
    # edge sits at elbow_x - shelf - label_w; the guard must reserve the shelf or a near-boundary
    # label overshoots the margin. The shelf is the helpers Leader's gap = draft.pad_around_text,
    # not a fixed 2.0 (#859, Codex #862 r4/r5).
    if elbow_x - draft.pad_around_text - label_w < _MARGIN:
        if ev is not None:
            ev["items"].extend(
                {"label": f"ø{_fmt(d)}", "outcome": "dropped", "reason": "no_room_left"}
                for _, d, _, _, _ in items
            )
        return 0
    specs = []  # (tip_page, dia, label, feature), tip on the step's left silhouette,
    for anchor, dia, feat, dtol, thr in items:  # centred along the feature's length (not a corner)
        ax, ay, az = anchor
        tip = dwg.at("front", ax - dia / 2, ay, az)
        label = f"ø{_fmt(dia)}{_tol_suffix(dtol, draft)}" + (f" {thr}" if thr else "")  # #859
        specs.append((tip, dia, label, feat))
    half_h = draft.font_size / 2 + draft.pad_around_text
    min_gap = 2 * half_h
    # Place what fits; drop the smallest ø first, never the whole column (#298).
    survivors, ys = _place_what_fits(specs, 1, min_gap, fy0 + half_h, fy1 - half_h)
    # Full-footprint occupancy (leader shafts, witness/extension lines, hatch) — NOT
    # a label-box-only view, which is blind to a bore callout's leader SHAFT, so a
    # ø label could silently overprint it (the #133/#225/#305 invisible-occupant
    # class, #358). Centre lines stay crossable (a diameter dim may cross one).
    if ev is not None:  # the specs the fit solve squeezed out (#298 smallest-first drops)
        kept = {id(s) for s in survivors}
        ev["items"].extend(
            {"label": s[2], "outcome": "dropped", "reason": "squeezed_out"}
            for s in specs
            if id(s) not in kept
        )
    occupied = strip_obstacles(dwg, view="front", crossable=CROSSABLE_TYPES)
    placed = 0
    for i, ((tip, dia, label, feat), ly) in enumerate(zip(survivors, ys, strict=True)):
        ldr = Leader(tip=(tip[0], tip[1], 0), elbow=(elbow_x, ly, 0), label=label, draft=draft)
        if _box_hits(_anno_box(ldr), occupied):
            if ev is not None:
                ev["items"].append(
                    {"label": label, "outcome": "dropped", "reason": "label_occupied"}
                )
            continue  # would overprint a bore leader / existing callout — drop just this one
        ctx.place(ldr, f"m_dia_z{start + i}", view="front", feature=feat)
        if ev is not None:
            ev["items"].append(
                {
                    "name": f"m_dia_z{start + i}",
                    "label": label,
                    "outcome": "placed",
                    "pos": [elbow_x, ly],
                }
            )
        occupied.append(_anno_box(ldr))
        placed += 1
    return placed


def _diameter_step_anchor(anchor, groups):
    """Anchor point for a turned ⌀ leader tip.

    Centre the leader at the MID-LENGTH of the STEP carrying this diameter, rather
    than the frame origin passed in (which for a boss or a shared ⌀'s first-bucketed
    step can be an end corner). Only STEP spans are used: a step round-trips its
    length + centre through the emitted Sheet script (declared ``length=``/``at=``),
    so direct and scripted builds agree (#707 parity); a boss is re-synthesised
    without a declared span and keeps its frame origin in both paths (``anchor``).

    When a ⌀ is shared by several steps, select ONE — the longest run, leftmost on
    ties — and use THAT step's OWN origin (radial + axial). Never the convex-hull
    midpoint (which for disjoint ⌀A–⌀B–⌀A steps falls in the ⌀B gap, off any
    silhouette) and never the bucket ``anchor``'s radial (which for non-coaxial
    same-⌀ steps belongs to a different feature). The selection quantises both the
    length and the lower end to the emitter's 3-dp values, so direct and scripted
    pick the same run. In the #426 finalize/``only=`` path this centres over the
    recorded subset, as the pre-#794 first-recorded-origin anchor also did.
    """
    steps = [
        (g, g.dim(kind="length"))
        for g in groups
        if g.feature_kind == "step" and g.dim(kind="length") is not None
    ]
    steps = [(g, d) for g, d in steps if d.span is not None]
    if not steps:
        return anchor
    idx = {"x": 0, "y": 1, "z": 2}[steps[0][0].facts.frame.axis]

    def _key(item):
        _g, dim = item
        ends = sorted(end[idx] for end in dim.span)
        length = round(ends[1] - ends[0], 3)  # emitter rounds step length and centre (`at`) to
        at = round((ends[0] + ends[1]) / 2, 3)  # 3dp — quantise both so the selection round-trips
        return (length, -(at - length / 2))

    step, length = max(steps, key=_key)
    ends = sorted(end[idx] for end in length.span)
    centred = list(step.facts.frame.origin)  # the SELECTED step's own origin (radial + axial)
    centred[idx] = (ends[0] + ends[1]) / 2
    return tuple(centred)


def render_diameters(dwg, plan, a, tol: float = 0.15, *, ctx, only=None) -> int:
    """ø leaders for a turned part's external step/boss diameters, from the IR —
    one distinct callout per diameter, in a tidy row below the front view
    (X-turning), a column to its left (Z-turning), or as radial leaders in the
    end-on front view (Y-turning). Orientation is the feature frame's axis, not
    separate detection paths. Replaces the engine's
    ``_annotate_turned_diameters`` (ADR 0008 convergence). Diameters another
    annotation already covers are skipped.

    *only*, when given, restricts placement to step/boss features in the set — the #426
    finalize() path passes the recorded step/boss ``callout`` intents' features.
    ``None`` (the auto-pass) places every diameter with the historical 0-based
    ``m_dia_{x,z}`` naming; Y-axis leaders use ``m_dia_y``."""
    mentioned = _mentioned_diameters(dwg)
    # One distinct callout per (axis, diameter). Accumulate EVERY feature that shares a
    # diameter (insertion-ordered), so provenance (#412) can tag the callout with its
    # single owner — or leave it unowned when two distinct features share the diameter
    # (the #398c/#406 shared-value rule, so drop can't over-strip a sibling).
    row_buckets: dict = {}  # round(dia,2) -> [anchor, dia, {features}, tolerance]  (X-turned)
    col_buckets: dict = {}  # Z-turned
    end_buckets: dict = {}  # Y-turned: radial leaders in the end-on front view
    for g in plan.of_kind("step", "boss"):
        if only is not None and g.ref not in only:  # #426 finalize: recorded subset
            continue
        dpd = g.dim(kind="diameter")
        if dpd is None:
            continue
        dia = dpd.value
        # An EXTERNAL thread (#859) makes a distinct callout: a threaded ⌀6 ("ø6 M6x1") and a
        # plain ⌀6 are NOT the same label, so the bucket keys on (⌀, thread) — this never drops a
        # thread on a shared ⌀, and a threadless model keys every entry on (⌀, None) exactly as
        # before (byte-identical). entry = [anchor, dia, {features}, ± tolerance, thread]. A
        # callout is per (axis, ⌀, thread); the first authored tolerance on a shared ⌀ wins.
        thr = g.facts.get("thread")
        # A coincident plain ⌀ already drawn (a bore, another step) dedups only an UNTHREADED ⌀;
        # a threaded ⌀ is a distinct callout, so a bare ⌀8 mention must not suppress ø8 M8x1.25.
        if thr is None and any(abs(dia - m) <= tol for m in mentioned):
            continue
        bucket = {"x": row_buckets, "y": end_buckets, "z": col_buckets}.get(g.facts.frame.axis)
        if bucket is None:
            continue
        dtol = dpd.tolerance
        dkey = (round(dia, 2), thr)
        entry = bucket.setdefault(dkey, [g.anchor, dia, set(), dtol, thr, []])
        entry[2].add(g.ref)
        entry[5].append(g)
        if entry[3] is None:
            entry[3] = dtol

    def _items(buckets):
        return [
            (
                _diameter_step_anchor(a, gs),
                d,
                next(iter(refs)) if len(refs) == 1 else None,
                t,
                thr,
            )
            for a, d, refs, t, thr, gs in buckets.values()
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
    start_y = _next_start("m_dia_y") if only is not None else 0
    start_z = _next_start("m_dia_z") if only is not None else 0
    trace = getattr(ctx, "trace", None)  # the immediate placers report to the trace too (#736)
    placed = _diameter_row_below(
        dwg, _items(row_buckets), start=start_x, trace=trace, ctx=ctx
    ) + _diameter_column_left(dwg, _items(col_buckets), start=start_z, trace=trace, ctx=ctx)

    # A Y-axis step is end-on in the front view, so the X/Z profile-strip
    # leaders are geometrically inapplicable. Place one radial leader per
    # distinct diameter around the concentric circles instead. Keep shared-value
    # provenance honest: a diameter owned by several steps has no single feature.
    if end_buckets:
        vb = dwg.view_bounds("front")
        if vb is not None:
            reach = dwg.draft.font_size + 6 * dwg.draft.pad_around_text
            hole_circles = []
            for g in plan.of_kind("hole", "pattern"):
                feature = g.facts
                if feature.frame.axis != "y":
                    continue
                bore = g.dim(kind="diameter", role="bore")
                if bore is None:
                    continue
                diameter = bore.value
                locations = feature.members or (feature.frame.origin,)
                for location in locations:
                    px, py, *_ = dwg.at("front", *location)
                    hole_circles.append((px, py, diameter / 2 * a.SCALE))
            jobs = []
            covered_by_name = {}
            for i, (_anchor, dia, refs, dtol, thr, feature_groups) in enumerate(
                end_buckets.values()
            ):
                representative = feature_groups[0].facts
                owner = next(iter(refs)) if len(refs) == 1 else None
                # Materialise now: a generator expression would close over ``owner``
                # and all jobs would read the final loop iteration's feature when
                # _leader_callout_pass consumes them (#890).
                candidates = [
                    (tip, elbow, owner)
                    for tip, elbow, _feature in _radial_candidates(
                        dwg,
                        "front",
                        vb,
                        representative,
                        reach,
                        rim=dia / 2 * a.SCALE,
                    )
                ]
                # This pre-drain consumer deliberately retains the exact #890
                # greedy semantics: try hole-clear rays first, but preserve the
                # obstructed Policy-B tail in case every clear ray is blocked by
                # fixed annotation/page occupancy. Joint filtering here would
                # change the diameter coverage seen by later semantic passes.
                candidates.sort(
                    key=lambda candidate: _leader_hole_clearance(candidate, hole_circles),
                    reverse=True,
                )
                label = f"ø{_fmt(dia)}{_tol_suffix(dtol, dwg.draft)}"
                if thr:
                    label += f" {thr}"
                name = f"m_dia_y{start_y + i}"
                # One ø callout stands for every step sharing this diameter, so it draws
                # each of their diameter dims (#1002). Empty when a group planned none —
                # recorded as unknown, which is the honest answer, not a guessed id.
                mids = tuple(
                    pd.id for gp in feature_groups for pd in gp.dims if pd.kind == "diameter"
                )
                jobs.append((name, "front", vb, label, candidates, mids))
                covered_by_name[name] = dia
            placed += _leader_callout_pass(
                dwg,
                a,
                jobs,
                noun="Y-axis step diameter",
                drop_code="diameter_dropped",
                ctx=ctx,
                geom_clear=True,
            )
            # These leaders intentionally start on an internal concentric circle
            # and exit the outer silhouette, just like a bore callout. Structured
            # diameter coverage both credits completeness lint and exempts that
            # legitimate shaft crossing from leader_crosses_silhouette.
            for name, dia in covered_by_name.items():
                ann = ctx.registry.named(name)
                if ann is not None:
                    ann.covers_diameters = (dia,)
    # #798: a ⌀ leader the row/column solve sent DIAGONALLY into the body — cutting
    # the silhouette, or an end feature whose diagonal merely grazes it — is re-routed
    # to the clear side (the margin the feature sits at). Auto-pass only: the finalize
    # (only=) path replays recorded verbs and must not disturb pinned user dims.
    if only is None:
        _reroute_crossing_diameters(dwg, ctx=ctx)
    return placed


_REROUTE_EDGE_TOL = 2.0  # page mm: a tip this close to an axial end sits AT that end
_REROUTE_SLACK = 1.0  # page mm: an elbow displaced this far toward the interior is "into the body"


def _reroute_crossing_diameters(dwg, *, ctx) -> int:
    """Route a turned ⌀ leader that heads INTO the part body (#798) out to the
    nearest CLEAR margin, rather than diagonally into the body.

    Two triggers, both re-routed to the clear side:

    * The shaft CUTS THROUGH the body — measured on the shared filled material field,
      the same predicate the router and the critique use (#798). It used to use the
      outline-crossing heuristic the critique has since retired, which made this a third
      consumer on a rule the other two no longer trust: a shaft merely passing over a
      through-hole was re-routed here while the critique would not have flagged it.
    * An END feature (tip at the part's axial extreme, e.g. a ⌀6 boss stub) whose
      row/column-solved elbow diagonals back INTO the body — a near-miss that reads
      as clipping the outline even where it does not strictly cross.

    Axis-aware: an ``m_dia_x`` (X-turned) leader's axial run is page-X and its clear
    margins are left/right; an ``m_dia_z`` (Z-turned) leader's axial run is page-Y
    and its margins are bottom/top. Candidates (near margin → straight out either way
    along the radial axis) are kept only when the new shaft clears the outline AND the
    rebuilt leader's LABEL stays inside the page and off every other view/annotation
    box — so a re-route never trades an info crossing for an out-of-bounds or overlap
    error (the shaft itself, like the row/column placers, is gated only on the
    silhouette). If nothing is both clear and safe the leader is restored unchanged
    (Phase-1 then flags it). A PINNED leader (ADR 0012) is never moved. Returns the
    number re-routed."""
    field = view_material(dwg, "front")
    if field is None:
        return 0

    def _cuts(tip_point, elbow_point) -> bool:
        return bool(material_penalty_units(tip_point, elbow_point, field))

    fb = dwg.view_bounds("front")
    if not fb:
        return 0
    draft = dwg.draft
    gap = draft.font_size + 2 * draft.pad_around_text
    page = (_MARGIN, _MARGIN, dwg.page_w - _MARGIN, dwg.page_h - _MARGIN)

    def _within_page(box):
        return box[0] >= page[0] and box[1] >= page[1] and box[2] <= page[2] and box[3] <= page[3]

    rerouted = 0
    for name in [n for n in dwg.annotations() if n.startswith(("m_dia_x", "m_dia_z"))]:
        ldr = dwg.get_annotation(name)
        if ldr is None or getattr(ldr, "elbow", None) is None:
            continue
        if dwg.registry.is_pinned(name):  # a pin is the user's "stays put" (ADR 0012)
            continue
        tip, elbow = ldr.tip, ldr.elbow
        crosses = _cuts(tip, elbow)
        ax = 0 if name.startswith("m_dia_x") else 1  # axial page axis (X row / Y column)
        rad = 1 - ax
        lo_b, hi_b = fb[ax], fb[ax + 2]
        at_lo = tip[ax] - lo_b <= _REROUTE_EDGE_TOL
        at_hi = hi_b - tip[ax] <= _REROUTE_EDGE_TOL
        into_body = (at_lo and elbow[ax] > tip[ax] + _REROUTE_SLACK) or (
            at_hi and elbow[ax] < tip[ax] - _REROUTE_SLACK
        )
        if not (crosses or into_body):
            continue
        # Clear-side elbows toward the CLEAR end only: the near axial margin (the end
        # the feature sits at) first, then straight out either way along the radial
        # axis. The far axial margin is deliberately NOT a candidate — a leader run
        # the whole length of the part to the opposite end reads worse than the
        # near-miss it replaces; restore-and-flag is the honest fallback instead.
        near_a = lo_b - gap if tip[ax] - lo_b <= hi_b - tip[ax] else hi_b + gap

        def _pt(a_val, r_val, _ax=ax):
            p = [0.0, 0.0]
            p[_ax], p[1 - _ax] = a_val, r_val
            return (p[0], p[1])

        candidates = (
            _pt(near_a, tip[rad]),
            _pt(tip[ax], fb[rad] - gap),
            _pt(tip[ax], fb[rad + 2] + gap),
        )
        ident = dwg.registry.identity_of(name)  # every axis, as a unit (#1002)
        old = dwg.remove(name)  # remove first so obstacles exclude the leader being replaced
        placed_it = False
        try:
            obstacles = strip_obstacles(dwg, crossable=CROSSABLE_TYPES)
            for vis, hid in dwg.views.values():
                for shp in (vis, hid):
                    if shp is None:
                        continue
                    b = shp.bounding_box()
                    obstacles.append((b.min.X, b.min.Y, b.max.X, b.max.Y))
            for ex, ey in candidates:
                if _cuts(tip, (ex, ey)):
                    continue
                cand = Leader(
                    tip=(tip[0], tip[1], 0), elbow=(ex, ey, 0), label=ldr.label, draft=draft
                )
                box = _anno_box(cand)
                if box is None or not _within_page(box) or _box_hits(box, obstacles):
                    continue
                ctx.place(cand, name, view="front")
                dwg.registry.reapply(name, ident)  # the re-routed leader draws the same thing
                rerouted += 1
                placed_it = True
                break
        except Exception:  # noqa: BLE001 — a re-route error must never lose the leader
            placed_it = False
        if not placed_it and dwg.get_annotation(name) is None:
            ctx.place(old, name, view="front")  # restore (Phase-1 flags it)
            dwg.registry.reapply(name, ident)
    return rerouted


def _chamfer_label(leg_text, leg, ch) -> str:
    """The chamfer callout string: ``C{leg}`` for an equal-leg 45° chamfer, else
    ``{leg} × {angle}°`` (#560).

    Takes the leg TWICE, on purpose, because printing it and testing its form are different
    jobs: *leg_text* is the compiler's own `value_text` and is what appears on the sheet,
    while *leg* is the number the equal-leg comparison needs. The feature supplies only the
    geometric form discriminators (``leg2``/``angle``), and a ``ChamferFeature`` stays pure
    data (ADR 0013 §7)."""
    if abs(leg - ch.leg2) < 0.05 and abs(ch.angle - 45.0) < 0.5:
        return f"C{leg_text}"
    # `ch.angle` is a FORM discriminator, not a planned parameter — `ChamferFeature.
    # parameters()` emits only the leg — so it has no approved text to consume. That is the
    # IR gap `_FACTS` records, and it is why this line stays in the provenance budget.
    return f"{leg_text} × {_fmt(ch.angle)}°"


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


def _label_lands_clear(ldr, obstacles, silhouette, page, *, geom_clear=False) -> bool:
    """True when a leader's LABEL box sits in clear margin: off other annotations
    (*obstacles*), off the part *silhouette* (the leader line may cross into the view, its
    text may not), and inside the *page* margin box. The single accept test the callout
    passes share (was the identical six-line guard, copied five times).

    With *geom_clear* the obstacle test uses the FULL ``_geom_box`` footprint (shaft +
    arrow + label) instead of the label box — the shaft is the invisible occupant a
    label-only box hides (#133/#305/#358), so a rim-anchored boss ø leader must clear
    the other callouts/witnesses along its whole length (#629/#700). The silhouette/
    page checks stay on the label box, since such a shaft legitimately starts inside
    the view."""
    geom = _geom_box(ldr) if geom_clear else None
    label = getattr(ldr, "label_bbox", None) or (geom if geom_clear else _anno_box(ldr))
    if label is None or (geom_clear and geom is None):
        return False
    return not (
        _box_hits(geom if geom_clear else label, obstacles)
        or _box_hits(label, [silhouette])
        or label[0] < page[0]
        or label[1] < page[1]
        or label[2] > page[2]
        or label[3] > page[3]
    )


def _corner_candidates(dwg, view, vb, members, reach, *, provenances=None):
    """Lead candidates for a corner-sitting feature (chamfer/fillet/flat): from each member's
    projected origin, a diagonal from the view centre out through the corner, *reach* beyond
    the tip — a corner clears the silhouette this way. Yields ``(tip, elbow, member)`` in the
    given member order, which is the stable tie-break after #740's cardinality/length solve;
    a single-feature callout passes a one-element *members*."""
    x0, y0, x1, y1 = vb
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    owners = provenances if provenances is not None else members
    for m, owner in zip(members, owners):
        tip = dwg.at(view, *m.frame.origin)
        dx, dy = tip[0] - cx, tip[1] - cy
        d = math.hypot(dx, dy) or 1.0
        elbow = (tip[0] + dx / d * reach, tip[1] + dy / d * reach, 0)
        yield (tip, elbow, owner)


def _corner_escape_candidates(dwg, view, vb, members, reach, *, provenances=None):
    """Corner leaders plus silhouette-outward horizontal/vertical escapes.

    The diagonal remains the stable first choice.  The two axis-aligned rays
    leave the same corner away from the view centre, so they add boundary/lane
    alternatives without introducing #798's through-silhouette routing problem.
    """

    members = list(members)
    owners = list(provenances if provenances is not None else members)
    yield from _corner_candidates(dwg, view, vb, members, reach, provenances=owners)
    x0, y0, x1, y1 = vb
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    for member, owner in zip(members, owners, strict=True):
        tip = dwg.at(view, *member.frame.origin)
        # Two axis escapes only. Widening this fan to five directions was measured
        # (#1187): it does find clear routes, but the extra candidates push dense parts
        # back over the per-view candidate budget and out of the exact solve, which cost
        # more than it bought — +2 callouts placed, +2 cuts, and three fixtures lost
        # `joint`. Candidate richness trades against solve reach; this is the balance.
        directions = (
            (1.0 if tip[0] >= cx else -1.0, 0.0),
            (0.0, 1.0 if tip[1] >= cy else -1.0),
        )
        for ux, uy in directions:
            exit_distance = _ray_exit_dist(tip[0], tip[1], ux, uy, vb)
            yield (
                tip,
                (
                    tip[0] + ux * (exit_distance + reach),
                    tip[1] + uy * (exit_distance + reach),
                    0,
                ),
                owner,
            )


def _flat_candidates(dwg, view, vb, members, reach, *, provenances):
    """Keep the established centre-out lead first, then try true margin escapes.

    A flat is a corner feature on its own stock, but not necessarily on the aggregate view:
    parallel stock can put an inner lobe well inside ``vb``.  The old single candidate moved
    only *reach* from that lobe and left the label intersecting the aggregate silhouette.
    ``_radial_candidates`` advances to the view boundary before adding the same reach, so its
    fallbacks use available margin without changing the preferred placement of ordinary flats.
    """
    members = list(members)
    provenances = list(provenances)
    yield from _corner_candidates(
        dwg,
        view,
        vb,
        members,
        reach,
        provenances=provenances,
    )
    for member, provenance in zip(members, provenances):
        yield from _radial_candidates(
            dwg,
            view,
            vb,
            member,
            reach,
            provenance=provenance,
        )


_LEADER_ASSIGN_MAX_CANDIDATES = 256
_LEADER_ASSIGN_MAX_PAIR_PROBES = 100_000


@dataclass(frozen=True)
class _LeaderPassCandidate:
    """One measured alternative for the bounded #740 within-pass assignment."""

    tip: tuple
    elbow: tuple
    feature: Any
    leader: Any
    raw_index: int
    check_box: tuple
    obstacle_boxes: tuple
    cost: float


def _leader_candidate_check_box(leader, *, geom_clear):
    """The footprint the candidate itself must keep clear of earlier leaders.

    This is the obstacle half of :func:`_label_lands_clear`, factored so the
    joint assignment uses exactly the same rule as the former sequential pass.
    """

    geom = _geom_box(leader) if geom_clear else None
    label = getattr(leader, "label_bbox", None) or (geom if geom_clear else _anno_box(leader))
    return geom if geom_clear else label


def _leader_callout_pass(
    dwg,
    a,
    jobs,
    *,
    noun,
    drop_code,
    ctx,
    geom_clear=False,
    joint=False,
) -> int:
    """Place one machined-feature leader callout per job (#637/#740). A *job* is
    ``(name, view, vb, label, candidates, measurement)`` where *candidates* yields ``(tip,
    elbow, feature)`` lead positions to try in order and *measurement* is the tuple of
    `DimensionId`s the callout's label draws — several, because these callouts are compound:
    a pocket prints width × length × depth, a groove width × ⌀ (#1002 r3, which found the
    whole machined-feature group recording nothing at all).
    The five scoped post-drain adapters pass ``joint=True``. Their candidate geometry is
    collected before anything is emitted. Fixed-obstacle eligibility and pairwise conflicts
    use the same rendered occupancy as the old first-clear loop; the geometry-only layout
    solver then maximises placed jobs, minimises total leader length, and uses candidate order
    as the deterministic final tie-break. Alternative construction, pass size, pair
    construction, and the exact search are bounded. If any budget is reached, the legacy
    greedy result is retained, so resource pressure can never place fewer callouts than before
    #740. Pre-drain diameter/pattern consumers leave ``joint=False`` because their winners
    affect later semantic passes; #740 deliberately does not change that stage boundary.

    A selected Leader is attributed to that candidate's feature; every unselected job records
    ``<noun> callout … not placed`` as ``<drop_code>`` lint (never a silent drop). Returns the
    count placed.

    Traced (#736): with ``ctx.trace`` on, one ``pass_events`` record
    (``<noun>_callouts``) carries a per-job item — name, view, label, candidates
    tried, obstacle count, placed tip/elbow or the drop reason. Post-#734 these
    callouts place after the drain, so without this the #733 story ("why did this
    pocket callout land here / drop") would be invisible to the trace."""
    # Keep alternatives lazy until the bounded joint tier is known to be usable.
    # This matters for one grouped job with many members: counting jobs or
    # cross-job pairs alone cannot protect its collect-all OCC construction.
    jobs = [(*job[:4], iter(job[4]), job[5]) for job in jobs]
    if not jobs:
        return 0
    if joint and getattr(ctx, "feature_leaders", None) is not None:
        for name, view, vb, label, raw_candidates, measurement in jobs:
            joint_candidates, fallback_candidates = tee(raw_candidates)

            def _lane_candidates(_raw=joint_candidates):
                spacing = dwg.draft.font_size + 2 * dwg.draft.pad_around_text
                for tip, elbow, feature in _raw:
                    dx, dy = float(elbow[0]) - float(tip[0]), float(elbow[1]) - float(tip[1])
                    length = math.hypot(dx, dy)
                    if length <= 1e-12:
                        yield (tip, elbow, feature)
                        continue
                    ux, uy = dx / length, dy / length
                    px, py = -dy / length, dx / length
                    for outward, lane in (
                        (0, 0),
                        (0, 1),
                        (0, -1),
                        (0, 2),
                        (0, -2),
                        (1, 0),
                        (2, 0),
                        (1, 1),
                        (1, -1),
                    ):
                        yield (
                            tip,
                            (
                                float(elbow[0]) + ux * spacing * outward + px * spacing * lane,
                                float(elbow[1]) + uy * spacing * outward + py * spacing * lane,
                                0,
                            ),
                            feature,
                        )

            def _build(tip, elbow, _feature, *, _label=label):
                return Leader(
                    tip=(tip[0], tip[1], 0),
                    elbow=elbow,
                    label=_label,
                    draft=dwg.draft,
                )

            def _fallback_accept(
                candidate,
                obstacles,
                page,
                *,
                _vb=vb,
                _geom_clear=geom_clear,
            ):
                return _label_lands_clear(
                    candidate.annotation,
                    obstacles,
                    _vb,
                    page,
                    geom_clear=_geom_clear,
                )

            collect_feature_leader(
                ctx,
                FeatureLeaderJob(
                    name=name,
                    view=view,
                    silhouette=vb,
                    label=label,
                    candidates=_lane_candidates(),
                    build=_build,
                    measurement=tuple(measurement),
                    noun=noun,
                    drop_code=drop_code,
                    fallback_candidates=fallback_candidates,
                    fallback_accept=_fallback_accept,
                    # Fixed dimensions/witnesses are exact-ink constraints in
                    # the shared solve.  If every relocation is worse, retain
                    # the established semantic callout only through explicit
                    # Policy B: the solver penalises the crossing and the
                    # emitter records a machine-readable warning (#1166).
                    allow_policy_b_fixed=True,
                ),
            )
        return 0
    trace = getattr(ctx, "trace", None)
    ev = trace.pass_event(f"{noun}_callouts") if trace is not None else None
    page = (a.margin, a.margin, a.PAGE_W - a.margin, a.PAGE_H - a.margin)

    def trace_item(
        name,
        view,
        label,
        raw_count,
        obstacle_count,
        candidate=None,
        *,
        viable=None,
        drop_reason="no_clear_room",
    ):
        if ev is None:
            return
        item = {
            "name": name,
            "view": view,
            "label": label,
            "candidates_tried": raw_count,
            "obstacles": obstacle_count,
        }
        if viable is not None:
            item["viable_candidates"] = viable
        if candidate is None:
            item.update({"outcome": "dropped", "reason": drop_reason})
        else:
            item.update(
                {
                    "outcome": "placed",
                    "candidate": candidate.raw_index,
                    "tip": [candidate.tip[0], candidate.tip[1]],
                    "elbow": [candidate.elbow[0], candidate.elbow[1]],
                }
            )
        ev["items"].append(item)

    def place(candidate, job) -> None:
        name, view, _vb, _label, _raw, measurement = job
        # Compiled renderers carry opaque FeatureRefs so they cannot recover
        # measurements from the source feature. Provenance is the one seam where
        # the registry intentionally needs the source object itself (#1002).
        ctx.place(
            candidate.leader,
            name,
            view=view,
            feature=resolve_feature(candidate.feature),
            measurement=measurement,
        )

    def greedy(reason: str) -> int:
        """The exact pre-#740 first-clear loop, retained as the bounded fallback."""

        if ev is not None:
            ev.update({"assignment": reason, "optimal": False})
        placed_count = 0
        for job in jobs:
            name, view, vb, label, raw_candidates, measurement = job
            obstacles = strip_obstacles(dwg, view=view, crossable=CROSSABLE_TYPES)
            field = view_material(dwg, view)
            tried = 0
            chosen = None
            # Acceptable-but-cutting alternatives, held while a bounded lookahead searches
            # for one that clears the body (#1187). Empty in the common case, where the
            # first acceptable route already clears and this breaks exactly where the
            # pre-#798 loop did.
            held: list[tuple[int, int, Any]] = []
            examined_since_accept = None
            for raw_index, (tip, elbow, feature) in enumerate(raw_candidates):
                tried = raw_index + 1
                if examined_since_accept is not None:
                    examined_since_accept += 1
                    if examined_since_accept > _GREEDY_MATERIAL_LOOKAHEAD:
                        break
                leader = Leader(tip=(tip[0], tip[1], 0), elbow=elbow, label=label, draft=dwg.draft)
                if not _label_lands_clear(leader, obstacles, vb, page, geom_clear=geom_clear):
                    continue
                candidate = _LeaderPassCandidate(
                    tip,
                    elbow,
                    feature,
                    leader,
                    raw_index,
                    _leader_candidate_check_box(leader, geom_clear=geom_clear),
                    tuple(annotation_obstacle_boxes(dwg, leader)),
                    math.hypot(elbow[0] - tip[0], elbow[1] - tip[1]),
                )
                units = material_penalty_units(tip, elbow, field)
                if units:
                    # Eligible, but it cuts the part. Policy B keeps a required callout at
                    # a logged cost rather than dropping it, so this is only ever a
                    # preference — the candidate stays available if nothing clearer is.
                    held.append((units, raw_index, candidate))
                    if examined_since_accept is None:
                        examined_since_accept = 0
                    continue
                chosen = candidate
                break
            if chosen is None and held:
                chosen = min(held, key=lambda entry: entry[:2])[2]
            if chosen is not None:
                place(chosen, job)
                trace_item(name, view, label, tried, len(obstacles), chosen)
                placed_count += 1
            else:
                ctx.record_issue(
                    "warning",
                    drop_code,
                    f"{noun} callout {label} not placed (no clear room)",
                    measurement=measurement,
                )
                trace_item(name, view, label, tried, len(obstacles))
        return placed_count

    if not joint:
        return greedy("greedy_legacy_scope")

    # Apply the pure solver's recursion bound before constructing any OCC
    # candidate geometry. The fallback creates at most the alternatives the old
    # first-clear loop actually visits, so an oversized pass cannot pay the
    # collect-all cost merely to discover that it is outside the exact tier.
    if len(jobs) > _LEADER_ASSIGN_MAX_JOBS:
        return greedy("greedy_job_budget")

    # Materialise at most the cheap tuple alternatives allowed into the joint
    # tier. On the first excess item, restore the consumed prefix in front of the
    # still-lazy tail and run the old loop. The fallback therefore constructs at
    # most as many OCC Leaders as pre-#740 did, even for one enormous grouped job.
    candidate_count = 0
    for job_index, job in enumerate(jobs):
        remaining = _LEADER_ASSIGN_MAX_CANDIDATES - candidate_count
        raw_candidates = job[4]
        prefix = list(islice(raw_candidates, remaining + 1))
        if len(prefix) > remaining:
            jobs[job_index] = (*job[:4], chain(prefix, raw_candidates), job[5])
            return greedy("greedy_candidate_budget")
        jobs[job_index] = (*job[:4], prefix, job[5])
        candidate_count += len(prefix)

    fixed_obstacles = {
        view: strip_obstacles(dwg, view=view, crossable=CROSSABLE_TYPES)
        for view in dict.fromkeys(job[1] for job in jobs)
    }
    candidates_by_job: list[list[_LeaderPassCandidate]] = []
    for _name, view, vb, label, raw_candidates, _measurement in jobs:
        viable = []
        for raw_index, (tip, elbow, feature) in enumerate(raw_candidates):
            leader = Leader(tip=(tip[0], tip[1], 0), elbow=elbow, label=label, draft=dwg.draft)
            if not _label_lands_clear(
                leader,
                fixed_obstacles[view],
                vb,
                page,
                geom_clear=geom_clear,
            ):
                continue
            check_box = _leader_candidate_check_box(leader, geom_clear=geom_clear)
            viable.append(
                _LeaderPassCandidate(
                    tip,
                    elbow,
                    feature,
                    leader,
                    raw_index,
                    check_box,
                    tuple(annotation_obstacle_boxes(dwg, leader)),
                    # Leader adds the same 2 mm landing segment to every
                    # alternative. Cardinality is the primary objective, so
                    # only this variable tip-to-elbow length affects ranking.
                    math.hypot(elbow[0] - tip[0], elbow[1] - tip[1]),
                )
            )
        candidates_by_job.append(viable)

    # Only same-view candidates can become obstacles to one another. Apply the
    # deterministic probe budget before entering the quadratic geometry loop.
    pair_probes = 0
    prior_by_view: dict[str, int] = {}
    for job, candidates in zip(jobs, candidates_by_job, strict=True):
        view = job[1]
        pair_probes += prior_by_view.get(view, 0) * len(candidates)
        prior_by_view[view] = prior_by_view.get(view, 0) + len(candidates)
        if pair_probes > _LEADER_ASSIGN_MAX_PAIR_PROBES:
            return greedy("greedy_pair_budget")

    conflicts = []
    for later_job, later_candidates in enumerate(candidates_by_job):
        later_view = jobs[later_job][1]
        for earlier_job in range(later_job):
            if jobs[earlier_job][1] != later_view:
                continue
            for earlier_index, earlier in enumerate(candidates_by_job[earlier_job]):
                for later_index, later in enumerate(later_candidates):
                    if _box_hits(later.check_box, earlier.obstacle_boxes):
                        conflicts.append((earlier_job, earlier_index, later_job, later_index))

    # Same material term and same per-view decomposition as the shared inventory
    # (#798/#1188): a within-pass callout is no more entitled to cut the part than a
    # shared one, and its conflicts are likewise same-view only.
    assignment = _assign_by_view(
        [job[1] for job in jobs],
        [[candidate.cost for candidate in candidates] for candidates in candidates_by_job],
        conflicts,
        priorities=[0.0] * len(jobs),
        penalties_by_job=[
            [
                material_penalty_units(candidate.tip, candidate.elbow, view_material(dwg, job[1]))
                for candidate in candidates
            ]
            for job, candidates in zip(jobs, candidates_by_job, strict=True)
        ],
    )
    if ev is not None:
        ev.update(
            {
                "assignment": "joint" if assignment.optimal else "joint_budget_incumbent",
                "optimal": assignment.optimal,
                "states": assignment.states,
                "pair_probes": pair_probes,
            }
        )

    placed_count = 0
    for job_index, (job, choice) in enumerate(zip(jobs, assignment.choices, strict=True)):
        name, view, _vb, label, raw_candidates, measurement = job
        candidates = candidates_by_job[job_index]
        if choice is not None:
            candidate = candidates[choice]
            place(candidate, job)
            trace_item(
                name,
                view,
                label,
                len(raw_candidates),
                len(fixed_obstacles[view]),
                candidate,
                viable=len(candidates),
            )
            placed_count += 1
            continue
        ctx.record_issue(
            "warning",
            drop_code,
            f"{noun} callout {label} not placed (no clear room)",
            measurement=measurement,
        )
        trace_item(
            name,
            view,
            label,
            len(raw_candidates),
            len(fixed_obstacles[view]),
            viable=len(candidates),
            drop_reason="assignment_conflict" if candidates else "no_clear_room",
        )
    return placed_count


def render_chamfers(dwg, plan, a, *, ctx, only=None) -> int:
    """Chamfer callouts (#560): a leader from each recognised chamfer face to its
    ``C{leg}`` / ``{leg}×{angle}°`` label, in the view normal to the chamfered edge (a Z
    edge reads in the plan, an X edge in the side, a Y edge in the front). The leader runs
    diagonally OUT of the corner the chamfer sits on into clear margin, and is dropped
    (lint, not silently) if it would overprint placed geometry. Returns the count placed.

    Planner-fed (#724 / #698): the leg VALUE + its tolerance come from the planner's
    ``DimParameter`` (as in ``render_boss_diameters``), never raw geometry — formatting
    ``ch.leg1`` directly dropped an authored chamfer tolerance (the #629 class). The dim
    is bound explicitly by ``(role, kind)``, never positionally. Only the C-vs-leg×angle
    *form* discriminators (``leg2``/``angle``) read off the feature;
    ``g.view`` is safe because a ChamferFeature's frame axis IS its edge axis
    (both ``detect.py`` and ``declare.chamfer`` build ``Frame(…, ch.axis)``), and
    ``_END_ON`` matches the pass's old z→plan / x→side / y→front map exactly."""
    draft = dwg.draft
    reach = _leader_callout_reach(draft)
    chamfer_groups = list(plan.of_kind("chamfer"))
    jobs = []
    for i, g in enumerate(
        sorted(chamfer_groups, key=lambda g: (g.facts.axis, g.facts.frame.origin))
    ):
        ch = g.facts
        if only is not None and g.ref not in only:
            continue  # #426 Ph2b subset (finalize): skip in place — i stays the model index
        # Bind the intended planned dim EXPLICITLY by (role, kind), never dims[0]
        # (#724 review): the pattern the remaining #698 migrations copy must not
        # silently grab the wrong dimension on a multi-parameter kind.
        pd = next(
            (d for d in g.dims if (d.role, d.kind) == ("chamfer", "length")),
            None,
        )
        if pd is None:
            continue
        view = g.view
        vb = dwg.view_bounds(view)
        if vb is None:
            continue
        jobs.append(
            (
                f"m_chamfer_{ch.axis}{i}",
                view,
                vb,
                _chamfer_label(pd.value_text, pd.value, ch) + _tol_suffix(pd.tolerance, draft),
                _corner_escape_candidates(dwg, view, vb, [ch], reach, provenances=[g.ref]),
                (pd.id,),
            )
        )
    return _leader_callout_pass(
        dwg,
        a,
        jobs,
        noun="chamfer",
        drop_code="chamfer_dropped",
        ctx=ctx,
        joint=True,
    )


def _fillet_label(radius_text, count) -> str:
    """The fillet callout string: ``R{radius}``, prefixed ``{count}×`` when a set of equal
    fillets shares one callout (#561). Formatting lives in the render layer (ADR 0013 §7)."""
    r = f"R{radius_text}"
    return f"{count}× {r}" if count > 1 else r


def render_fillets(dwg, plan, a, *, ctx, only=None) -> int:
    """Fillet radius callouts (#561): a leader from an external edge fillet to its
    ``R{radius}`` label — the arc analog of :func:`render_chamfers`. Equal-radius fillets
    share ONE ``n× R`` callout (#561 acceptance), placed in the view normal to a
    representative rounded edge, led diagonally out of the corner into clear margin, and
    dropped (lint, not silently) if it would overprint placed geometry. Returns the count.

    Planner-fed (#725 / #698): the radius VALUE + its tolerance come from the planner's
    ``DimParameter`` (as in ``render_chamfers``), bound explicitly by ``(role, kind)``,
    never positionally — formatting ``fl.radius`` directly dropped an authored tolerance
    (the #629 class). The equal-radius ``n× R`` COLLAPSE stays render-side (grouping by
    radius here) — planner-side grouping is a structural change,
    explicitly out of scope for this migration (#698). Where grouped members' authored
    tolerances differ, the first (member-order) authored one wins — the documented
    ``render_diameters`` shared-ø precedent. ``g.view`` is safe: a FilletFeature's frame
    axis IS its edge axis (both ``detect.py`` and ``declare.fillet`` build
    ``Frame(…, axis)``), and ``_END_ON`` matches the pass's old z→plan / x→side /
    y→front map exactly."""
    draft = dwg.draft
    reach = _leader_callout_reach(draft)
    collapse: dict = {}
    for g in plan.of_kind("fillet"):
        pd = next(
            (d for d in g.dims if (d.role, d.kind) == ("fillet", "radius")),
            None,
        )
        if pd is None:
            continue
        collapse.setdefault(round(pd.value, 3), []).append((g, pd))
    jobs = []
    for gi, (_radius, members) in enumerate(sorted(collapse.items())):
        if only is not None:
            # #426 Ph2b subset (finalize): filter members AFTER the collapse is enumerated so
            # gi stays the full-drawing group index — a survivor keeps its m_fillet name even
            # when a sibling group is dropped (Codex #811). The n× count reflects survivors.
            members = [gp for gp in members if gp[0].ref in only]
            if not members:
                continue
        # Point the leader at one coherent visible set. Members on other edge axes still
        # contribute to the printed count and semantic measurements, but mixing their 3-D
        # origins into this view could point at unrelated projected corners. Prefer the
        # axis with the most members; ties are deterministic.
        by_axis: dict[str, list] = {}
        for gp in members:
            by_axis.setdefault(gp[0].facts.axis, []).append(gp)
        axis, visible = min(by_axis.items(), key=lambda item: (-len(item[1]), item[0]))
        ordered = sorted(visible, key=lambda gp: gp[0].facts.frame.origin)
        view = ordered[0][0].view
        vb = dwg.view_bounds(view)
        if vb is None:
            continue
        # First-AUTHORED tolerance wins: scan `members` in planner/model order (the
        # render_diameters precedent — Codex review), not the spatially-sorted
        # `ordered`, whose winner would change if the geometry moved.
        tol = next((pd.tolerance for _, pd in members if pd.tolerance is not None), None)
        jobs.append(
            (
                f"m_fillet_{axis}{gi}",
                view,
                vb,
                _fillet_label(members[0][1].value_text, len(members)) + _tol_suffix(tol, draft),
                _corner_escape_candidates(
                    dwg,
                    view,
                    vb,
                    [g.facts for g, _ in ordered],
                    reach,
                    provenances=[g.ref for g, _ in ordered],
                ),
                # One `n× R` callout stands for EVERY collapsed member, so it draws all of
                # their radii — the tuple storage exists for exactly this (#1002).
                tuple(pd.id for _, pd in members),
            )
        )
    return _leader_callout_pass(
        dwg,
        a,
        jobs,
        noun="fillet",
        drop_code="fillet_dropped",
        ctx=ctx,
        joint=True,
    )


def _flat_label(across_text, sfx="") -> str:
    """The machined-flat callout string: ``{across} A/F`` (across flats) — the standard
    abbreviation for a spanner-flat / D / hex size (#148b). *across* is the PLANNED value
    (``pd.param.value``, #726); *sfx* is the pre-formatted tolerance suffix, interleaved
    after the value (``17 ±0.2 A/F`` — the tolerance rides the number, not the ``A/F``
    qualifier). Formatting lives in the render layer, not on the IR feature (ADR 0013 §7)."""
    return f"{across_text}{sfx} A/F"


def render_flats(dwg, plan, a, *, ctx, only=None) -> int:
    """Machined-flat callouts (#148b): a leader from a flat truncating round stock to its
    ``{across} A/F`` label, in the view down the stock axis (a Z-axis bar reads in the plan).
    Flats sharing an axis and across-flats size — the faces of a double-D or hex — share ONE
    callout. The leader runs diagonally out of the flat into clear margin, and is dropped
    (lint, not silently) if it would overprint placed geometry. Returns the count placed.

    Planner-fed (#726 / #698): the across-flats VALUE + its tolerance come from the
    planner's ``DimParameter``, bound explicitly by ``(role, kind)`` — formatting
    ``fl.across`` directly dropped an authored tolerance (the #629 class). The shared
    double-D/hex collapse stays render-side (planner-side grouping out of scope, #698);
    first-authored tolerance wins across grouped members (the ``render_diameters``
    precedent). ``g.view`` is safe: a FlatFeature's frame axis IS its stock axis (both
    ``detect.py`` and ``declare.flat`` build ``Frame(…, axis)``), and ``_END_ON`` matches
    the pass's old z→plan / x→side / y→front map exactly."""
    draft = dwg.draft
    reach = _leader_callout_reach(draft)
    collapse: dict = {}
    for g in plan.of_kind("flat"):
        pd = next(
            (d for d in g.dims if (d.role, d.kind) == ("flat", "length")),
            None,
        )
        if pd is None:
            continue
        # Grouped by the STOCK IDENTITY as well as the size (#1013). Two same-sized flats on
        # one piece of stock are the two faces of one double-D — one A/F definition, one
        # callout. On different stock they are independent, each needing its own; the axis
        # letter alone cannot tell those apart, so a part with two parallel 25 A/F lobes used
        # to get one callout and leave the second undefined on the sheet.
        #
        # Identity is the axis line AND the axial span, because neither alone is enough:
        # parallel lobes share a span but not a line, and coaxial stacked stock shares a line
        # but not a span. Grouping on the line alone merged the coaxial case silently — the
        # same defect this fix is about, one arrangement over (Codex #1035 r1).
        collapse.setdefault(
            (
                g.facts.axis,
                g.facts.presentation_axis,
                g.facts.axis_direction,
                g.facts.axis_line,
                g.facts.stock_span,
                round(pd.value, 3),
            ),
            [],
        ).append((g, pd))
    jobs = []
    for gi, ((_axis, presentation_axis, _direction, _line, _span, _across), members) in enumerate(
        sorted(collapse.items())
    ):
        if only is not None:
            # #426 Ph2b subset (finalize): filter members AFTER enumerating the collapse so gi
            # stays the full-drawing group index (Codex #811) — see render_fillets.
            members = [gp for gp in members if gp[0].ref in only]
            if not members:
                continue
        ordered = sorted(members, key=lambda gp: gp[0].facts.frame.origin)
        view = ordered[0][0].view
        vb = dwg.view_bounds(view)
        if vb is None:
            continue
        # First-AUTHORED tolerance wins (planner/model order, not spatial — see the
        # fillet pass / render_diameters precedent, Codex review).
        tol = next((pd.tolerance for _, pd in members if pd.tolerance is not None), None)
        # The established centre-out positions remain first. Boundary-aware fallbacks are
        # now safe for every flat because direction + perpendicular line position + span make
        # aligned and slanted stock groups canonical (#1036); they also cover a lone flat
        # under local placement pressure, not only #1034's multiple-stock case.
        candidates = _flat_candidates(
            dwg,
            view,
            vb,
            [g.facts for g, _ in ordered],
            reach,
            provenances=[g.ref for g, _ in ordered],
        )
        jobs.append(
            (
                f"m_flat_{presentation_axis}{gi}",
                view,
                vb,
                _flat_label(members[0][1].value_text, _tol_suffix(tol, draft)),
                candidates,
                tuple(pd.id for _, pd in ordered),  # the grouped callout draws every member
            )
        )
    return _leader_callout_pass(
        dwg,
        a,
        jobs,
        noun="flat",
        drop_code="flat_dropped",
        ctx=ctx,
        joint=True,
    )


def _groove_label(width_text, diameter_text, wsfx="", dsfx="") -> str:
    """The turned/circlip-groove callout string: ``{width} WIDE × ø{diameter}`` — the groove's
    axial width and its floor diameter (#148c). *width*/*diameter* are the PLANNED values
    (``pd.param.value``, #727); *wsfx*/*dsfx* are each value's pre-formatted tolerance
    suffix, interleaved so a tolerance rides its own number (``4 ±0.1 WIDE × ø16 ±0.05``) —
    the two params carry independent tolerances (kinds "length"/"diameter"). Formatting
    lives in the render layer, not on the IR feature (ADR 0013 §7)."""
    return f"{width_text}{wsfx} WIDE × ø{diameter_text}{dsfx}"


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


def _pocket_label(width_text, length_text, depth_text, wsfx="", lsfx="", dsfx="") -> str:
    """The pocket callout string: ``{width} × {length} × {depth} DEEP`` (#148a). The values
    are the PLANNED ones (``pd.param.value``, #728); *wsfx*/*lsfx*/*dsfx* are each value's
    pre-formatted tolerance suffix, interleaved so a tolerance rides its own number. (All
    three params share kind ``"length"``, so today one authored decoration folds onto all
    three — the per-value suffixes render that honestly; independent tolerancing needs an
    authoring-surface change, #698.) The ISO depth glyph (↧) is drawn as geometry by the
    helper's hole callouts, not as font text — a plain :class:`Leader` label has no access
    to it, so this uses the font-safe ``DEEP`` word (the vendored Plex Mono lacks ↧).
    Formatting lives in the render layer (ADR 0013 §7)."""
    return f"{width_text}{wsfx} × {length_text}{lsfx} × {depth_text}{dsfx} DEEP"


def _slot_label(width_text, length_text, wsfx="", lsfx="") -> str:
    """The grouped slot-array callout string: ``SLOT {width} × {length}`` (#841). A slot has no
    depth, so — unlike :func:`_pocket_label` — there is no ``× depth DEEP``; the ``SLOT`` prefix
    names the feature the way a lone slot's linear dims otherwise would."""
    return f"SLOT {width_text}{wsfx} × {length_text}{lsfx}"


# Unit lead directions. Diagonals remain first as the stable tie-break, while #740's
# within-pass assignment normally selects the shortest jointly compatible ray.
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


def _radial_candidates(
    dwg,
    view,
    vb,
    feature,
    reach,
    *,
    rim=0.0,
    source_bounds=None,
    directions=_POCKET_LEAD_DIRS,
    provenance=None,
):
    """Lead candidates for a mid-face feature (pocket/groove/boss ø): from the feature's
    projected origin, one candidate per *directions* entry (pocket-style diagonals
    first by default) — exit the silhouette along it (:func:`_ray_exit_dist`) then
    *reach* on into the margin, so even a centre-of-view feature clears the part. A
    non-zero *rim* (page units) advances the arrow tip that far along the lead direction,
    so a boss ø leader's arrowhead lands on the boss circle rather than its centre
    (#629/#700). ``source_bounds`` instead advances to the edge of a rectangular feature
    opening; a pocket leader starting at its centre crosses both the pocket rim and the outer
    silhouette, while a rim tip has only the one legitimate outward exit (#916). Yields
    ``(tip, elbow, feature)`` (same feature each time); #740 assigns the jointly compatible
    set with minimum total length, using this direction order only as the final tie-break."""
    x0, y0, x1, y1 = vb
    origin = dwg.at(view, *feature.frame.origin)
    for dx, dy in directions:
        d = math.hypot(dx, dy)
        ux, uy = dx / d, dy / d
        tip_offset = (
            _ray_exit_dist(origin[0], origin[1], ux, uy, source_bounds)
            if source_bounds is not None
            else rim
        )
        tip = (origin[0] + ux * tip_offset, origin[1] + uy * tip_offset)
        exit_d = _ray_exit_dist(tip[0], tip[1], ux, uy, (x0, y0, x1, y1))
        elbow = (tip[0] + ux * (exit_d + reach), tip[1] + uy * (exit_d + reach), 0)
        yield (tip, elbow, provenance if provenance is not None else feature)


def _pocket_rim_bounds(
    dwg, view, pocket, *, length: float, width: float
) -> tuple[float, float, float, float]:
    """Projected bounds of a rectangular pocket opening."""
    centre = list(pocket.frame.origin)
    points = []
    for long_sign in (-1, 1):
        for width_sign in (-1, 1):
            corner = centre.copy()
            corner["xyz".index(pocket.long_axis)] += long_sign * length / 2
            corner["xyz".index(pocket.width_axis)] += width_sign * width / 2
            points.append(dwg.at(view, *corner))
    return (
        min(point[0] for point in points),
        min(point[1] for point in points),
        max(point[0] for point in points),
        max(point[1] for point in points),
    )


def _leader_hole_clearance(
    candidate: tuple[tuple[float, float], tuple[float, float, float], Any],
    circles: list[tuple[float, float, float]],
) -> float:
    """Minimum page-space clearance from a leader shaft to projected hole circles.

    Ranking by the complete tip→elbow segment (not just the arrow tip) prevents a
    mathematically correct diameter-rim target from visually identifying a bolt
    hole farther along the same ray. With no holes every direction ties and retains
    the established candidate order.
    """
    if not circles:
        return math.inf
    tip, elbow, _feature = candidate
    ax, ay = tip[0], tip[1]
    bx, by = elbow[0], elbow[1]
    vx, vy = bx - ax, by - ay
    length2 = vx * vx + vy * vy
    clearances = []
    for cx, cy, radius in circles:
        if length2:
            t = max(0.0, min(1.0, ((cx - ax) * vx + (cy - ay) * vy) / length2))
        else:
            t = 0.0
        nearest_x, nearest_y = ax + t * vx, ay + t * vy
        clearances.append(math.hypot(cx - nearest_x, cy - nearest_y) - radius)
    return min(clearances)


def render_pockets(dwg, plan, a, *, ctx, only=None) -> int:
    """Blind-recess callouts (#148a): a leader from each floored slot/pocket to its
    ``W × L × D DEEP`` label, in the view normal to the recess opening (a Z-depth pocket
    reads in the plan, an X-depth in the side, a Y-depth in the front). A pocket sits
    mid-face, so — unlike a corner chamfer — it contributes alternatives toward each margin
    to the shared within-pass assignment; the callout is dropped (lint, not silently) if none
    lands in clear room. Returns the count placed.

    Planner-fed (#728 / #698): the multi-parameter case — width, length AND depth in one
    label — so EACH value + its tolerance is bound explicitly by its ``(role, kind)``
    (``pocket_width``/``pocket_length``/``pocket_depth``, all kind ``"length"``), never
    positionally, never ``dims[0]``. Formatting ``pk.width``… directly dropped an authored
    tolerance (the #629 class). The pass KEEPS its own axis→view map: ``g.view`` keys on
    the frame axis, which is the pocket's LONG axis, but the callout reads in the view
    normal to the DEPTH axis — not identical, so the map stays."""
    draft = dwg.draft
    reach = _leader_callout_reach(draft)
    view_of = {"z": "plan", "x": "side", "y": "front"}
    pocket_groups = list(plan.of_kind("pocket"))
    jobs = []
    for i, g in enumerate(
        sorted(pocket_groups, key=lambda g: (g.facts.width_axis, g.facts.frame.origin))
    ):
        pk = g.facts
        if only is not None and g.ref not in only:
            continue  # #426 Ph2b subset (finalize): skip in place — i stays the model index
        by_key = {(pd.role, pd.kind): pd for pd in g.dims}
        wpd = by_key.get(("pocket_width", "length"))
        lpd = by_key.get(("pocket_length", "length"))
        dpd = by_key.get(("pocket_depth", "length"))
        if wpd is None or lpd is None or dpd is None:
            continue
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
                _pocket_label(
                    wpd.value_text,
                    lpd.value_text,
                    dpd.value_text,
                    wsfx=_tol_suffix(wpd.tolerance, draft),
                    lsfx=_tol_suffix(lpd.tolerance, draft),
                    dsfx=_tol_suffix(dpd.tolerance, draft),
                ),
                _radial_candidates(
                    dwg,
                    view,
                    vb,
                    pk,
                    reach,
                    source_bounds=_pocket_rim_bounds(
                        dwg, view, pk, length=lpd.value, width=wpd.value
                    ),
                    provenance=g.ref,
                ),
                (wpd.id, lpd.id, dpd.id),  # width × length × depth, one callout (#1002)
            )
        )
    return _leader_callout_pass(
        dwg,
        a,
        jobs,
        noun="pocket",
        drop_code="pocket_dropped",
        ctx=ctx,
        joint=True,
    )


def render_grooves(dwg, plan, a, *, ctx, only=None) -> int:
    """Turned/circlip-groove callouts (#148c): a leader from each annular groove in round
    stock to its ``{width} WIDE × ø{diameter}`` label. The groove's width is *axial*, so the
    callout lands in the **profile** view (the one showing the stock axis in-plane, where the
    groove reads as a notch in the silhouette) — a Z or X axis in the front, a Y axis in the
    side. Each groove gets its own callout at its own axial position (like a pocket, not
    collapsed by size — two identical grooves on one shaft or on parallel shafts must each be
    dimensioned). The groove sits on the axis, so the leader contributes silhouette-exiting
    alternatives (``_ray_exit_dist``) toward each margin to the shared within-pass assignment
    and is dropped (lint, not silently) if none lands clear. Returns the count placed.

    Planner-fed (#727 / #698): a groove is the multi-parameter case — width AND floor ø in
    one label — so EACH value + its tolerance is bound explicitly by its ``(role, kind)``
    (``("groove", "length")`` / ``("groove", "diameter")``), never positionally, never
    ``dims[0]``. Formatting ``gr.width``/``gr.diameter`` directly dropped an authored
    tolerance (the #629 class). The pass KEEPS its own axis→view map: ``g.view`` is
    ``_END_ON`` (end-on: z→plan), but a groove reads in the PROFILE view where its axial
    width is visible — not provably identical, so the map stays."""
    draft = dwg.draft
    reach = _leader_callout_reach(draft)
    view_of = {"z": "front", "x": "front", "y": "side"}
    groove_groups = list(plan.of_kind("groove"))
    jobs = []
    for gi, g in enumerate(
        sorted(groove_groups, key=lambda g: (g.facts.axis, g.facts.frame.origin))
    ):
        gr = g.facts
        if only is not None and g.ref not in only:
            continue  # #426 Ph2b subset (finalize): skip in place — gi stays the model index
        wpd = next((d for d in g.dims if (d.role, d.kind) == ("groove", "length")), None)
        dpd = next((d for d in g.dims if (d.role, d.kind) == ("groove", "diameter")), None)
        if wpd is None or dpd is None:
            continue
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
                _groove_label(
                    wpd.value_text,
                    dpd.value_text,
                    wsfx=_tol_suffix(wpd.tolerance, draft),
                    dsfx=_tol_suffix(dpd.tolerance, draft),
                ),
                _radial_candidates(dwg, view, vb, gr, reach, provenance=g.ref),
                (wpd.id, dpd.id),  # one callout, two measurements (#1002)
            )
        )
    return _leader_callout_pass(
        dwg,
        a,
        jobs,
        noun="groove",
        drop_code="groove_dropped",
        ctx=ctx,
        joint=True,
    )


def render_boss_diameters(dwg, plan, a, *, ctx) -> int:
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
    whose ø a coincident feature already carries is skipped; an unplaceable one drops lint-visibly.

    Placement rides the shared :func:`_leader_callout_pass` adapter (#700 — never a sixth copy
    of the ray-exit loop, #637): rim-anchored :func:`_radial_candidates`, accepted with
    ``geom_clear`` (the full shaft, not just the label, must clear other annotations)."""
    if a.is_rotational or a.prof is not None:
        # A turned profile means round stock — a band emitted as a boss (#298) belongs in the
        # OD diameter row/column, not an end-on plan leader. Only true prismatic parts qualify.
        return 0
    draft = dwg.draft
    view_of = {"z": "plan", "x": "side", "y": "front"}  # the view looking down the boss axis
    boss_groups = list(plan.of_kind("boss"))
    mentioned = _mentioned_diameters(dwg)
    reach = draft.font_size + 6 * draft.pad_around_text
    jobs = []
    for bi, g in enumerate(
        sorted(boss_groups, key=lambda g: (g.facts.frame.axis, g.facts.frame.origin))
    ):
        b = g.facts
        dpd = next((pd for pd in g.dims if pd.kind == "diameter"), None)
        if dpd is None:
            continue
        dia = dpd.value
        dtol = dpd.tolerance
        thr = getattr(b, "thread", None)  # external thread appends to the OD callout (#859)
        # A coincident plain ⌀ dedups only an UNTHREADED boss; a threaded ⌀ is a distinct callout,
        # so a bare ⌀8 mention (a bore, a step) must not suppress ø8 M8x1.25 (#859).
        if thr is None and any(abs(dia - m) <= 0.15 for m in mentioned):
            continue
        view = view_of.get(b.frame.axis)
        if view is None:
            continue
        vb = dwg.view_bounds(view)
        if vb is None:
            continue
        jobs.append(
            (
                f"m_bossdia_{b.frame.axis}{bi}",
                view,
                vb,
                f"ø{_fmt(dia)}{_tol_suffix(dtol, draft)}" + (f" {thr}" if thr else ""),
                # arrowhead on the boss circle's rim, not its centre
                _radial_candidates(
                    dwg, view, vb, b, reach, rim=dia / 2 * a.SCALE, provenance=g.ref
                ),
                (dpd.id,),
            )
        )
    return _leader_callout_pass(
        dwg, a, jobs, noun="boss", drop_code="boss_dia_dropped", ctx=ctx, geom_clear=True
    )


def _polygonal_boss_candidates(dwg, view, vb, boss, reach, *, provenance):
    """Leader candidates anchored on the recognised boss's physical side faces."""
    origin = dwg.at(view, *boss.frame.origin)
    for flat_centre, flat_direction in zip(boss.flat_centres, boss.flat_directions, strict=True):
        tip = dwg.at(view, *flat_centre)
        direction_end = dwg.at(
            view,
            boss.frame.origin[0] + flat_direction[0],
            boss.frame.origin[1] + flat_direction[1],
            boss.frame.origin[2] + flat_direction[2],
        )
        dx, dy = direction_end[0] - origin[0], direction_end[1] - origin[1]
        distance = math.hypot(dx, dy)
        if distance <= 1e-9:
            continue
        ux, uy = dx / distance, dy / distance
        exit_distance = _ray_exit_dist(tip[0], tip[1], ux, uy, vb)
        elbow = (
            tip[0] + ux * (exit_distance + reach),
            tip[1] + uy * (exit_distance + reach),
            0,
        )
        yield (tip, elbow, provenance)


def _render_polygonal_prisms(
    dwg, plan, a, *, ctx, kind: str, noun: str, name_prefix: str, drop_code: str
) -> int:
    draft = dwg.draft
    reach = _leader_callout_reach(draft)
    jobs = []
    groups = sorted(
        plan.of_kind(kind),
        key=lambda group: (group.facts.frame.axis, group.facts.frame.origin),
    )
    for index, group in enumerate(groups):
        boss = group.facts
        dimension = next(
            (
                item
                for item in group.dims
                if (item.role, item.kind) == ("polygon_across_flats", "length")
            ),
            None,
        )
        if dimension is None:
            continue
        view = _END_ON.get(boss.frame.axis)
        if view is None:
            continue
        bounds = dwg.view_bounds(view)
        if bounds is None:
            continue
        prefix = "HEX" if boss.side_count == 6 else f"{boss.side_count}-SIDED"
        label = f"{prefix} {dimension.value_text}{_tol_suffix(dimension.tolerance, draft)} A/F"
        jobs.append(
            (
                f"{name_prefix}_{boss.frame.axis}{index}",
                view,
                bounds,
                label,
                _polygonal_boss_candidates(
                    dwg,
                    view,
                    bounds,
                    boss,
                    reach,
                    provenance=group.ref,
                ),
                (dimension.id,),
            )
        )
    return _leader_callout_pass(
        dwg,
        a,
        jobs,
        noun=noun,
        drop_code=drop_code,
        ctx=ctx,
        geom_clear=True,
    )


def render_polygonal_bosses(dwg, plan, a, *, ctx) -> int:
    """Across-flats leaders for bounded regular polygonal bosses (#676)."""
    return _render_polygonal_prisms(
        dwg,
        plan,
        a,
        ctx=ctx,
        kind="polygonal_boss",
        noun="polygonal boss",
        name_prefix="m_polygonal_boss",
        drop_code="polygonal_boss_dropped",
    )


def render_polygonal_stock(dwg, plan, a, *, ctx) -> int:
    """Across-flats leaders for whole regular polygonal stock (#1082)."""
    return _render_polygonal_prisms(
        dwg,
        plan,
        a,
        ctx=ctx,
        kind="polygonal_stock",
        noun="polygonal stock",
        name_prefix="m_polygonal_stock",
        drop_code="polygonal_stock_dropped",
    )


def render_boss_heights(dwg, plan, a, *, ctx) -> int:
    """Queue prismatic boss heights and polygonal-stock lengths in a profile corridor."""
    if a.is_rotational or a.prof is not None:
        return 0
    tier = dwg.draft.font_size + 2 * dwg.draft.pad_around_text
    specs = {
        "z": ("front", "right", a.fv_zones.right, "x"),
        "x": ("front", "above", a.fv_zones.above, "y"),
        "y": ("side", "above", a.sv_zones.above, "y"),
    }
    n = 0
    for bi, g in enumerate(
        sorted(
            [
                *plan.of_kind("boss"),
                *plan.of_kind("polygonal_boss"),
                *plan.of_kind("polygonal_stock"),
            ],
            key=lambda g: (g.facts.frame.axis, g.facts.frame.origin),
        )
    ):
        b = g.facts
        role = "stock_length" if g.ref.kind == "polygonal_stock" else "boss_height"
        pd = next((d for d in g.dims if (d.role, d.kind) == (role, "length")), None)
        if pd is None or pd.span is None:
            continue
        spec = specs.get(b.frame.axis)
        if spec is None:
            continue
        view, side, strip, stack = spec
        p1 = dwg.at(view, *pd.span[0])
        p2 = dwg.at(view, *pd.span[1])
        edge = max(p1[0], p2[0]) if side == "right" else max(p1[1], p2[1])
        label = pd.value_text + _tol_suffix(pd.tolerance, dwg.draft)
        prefix = "m_stocklength" if g.ref.kind == "polygonal_stock" else "m_bossheight"
        name = f"{prefix}_{b.frame.axis}{bi}"

        def build(pos, p1=p1, p2=p2, side=side, edge=edge, label=label):
            return _dim(p1, p2, side, abs(pos - edge), dwg.draft, label=label)

        def footprint(pos, p1=p1, p2=p2, side=side, edge=edge, label=label):
            return dim_footprint(p1, p2, side, abs(pos - edge), dwg.draft, label)

        def dropped(_name, *, stock=g.ref.kind == "polygonal_stock", measurement=pd.id):
            if stock:
                ctx.record_issue(
                    "warning",
                    "polygonal_stock_length_dropped",
                    "polygonal stock axial length was not placed (profile strip full)",
                    measurement=measurement,
                )

        register_corridor(
            ctx,
            (view, side),
            strip,
            view,
            stack,
            tier,
            CorridorCandidate(
                name=name,
                build=build,
                order=(_SIZE_SUBCHAIN, bi, name),
                on_place=lambda _nm: None,
                # Boss heights retain their historical reconciliation-only outcome. Stock
                # length additionally records the compiler measurement identity so its
                # completeness check can distinguish a placement drop from missing output.
                on_drop=dropped,
                force=True,
                feature=g.ref,
                measurement=pd.id,
                footprint=footprint,
            ),
        )
        n += 1
    return n


def render_plates(dwg, plan, a, *, ctx) -> int:
    """Plate/wall thicknesses and open-channel widths (#559/#917).

    Plate thickness is the thin extent of each recognised slab
    (`PlateFeature`), placed in the view where its thin axis is characteristic — a Z
    plate (horizontal slab) as a vertical dim left of the front elevation, a Y plate
    (upright wall) as a horizontal dim above the side (end) view where the L-profile
    shows it edge-on, an X plate below the front view. Base and wall land in different
    views so the two legs of a multi-plate prismatic read as distinct features rather
    than the overall envelope. A slab whose strip is full is dropped with a lint code
    (like the step ladder), not silently. Returns the count placed.

    Planner-fed (#729 / #698): the thickness VALUE + its tolerance come from the
    planner's ``DimParameter``, bound explicitly by ``(role, kind)`` —
    ``("thickness", "length")`` — never ``dims[0]``. Formatting ``hi - lo``
    directly dropped an authored tolerance (the #629 class). Placement mechanics
    (strips, tier stacking, the allowlisted carve fallthrough) are untouched. The
    pass KEEPS its own axis→view map: ``g.view`` is ``_END_ON`` (z→plan / y→front /
    x→side), not the edge-on profile view a thickness dim reads in (z→front-left,
    y→side-above, x→front-below)."""
    draft = dwg.draft
    tier = draft.font_size + 2 * draft.pad_around_text
    # Migrated to the ADR 0016 boundary: approved entries only, and the plate's `lo`/`hi`
    # come from the thickness dim's SPAN rather than the feature — they are the two ends of
    # the measurement, so the span is where they belong. `axis` stays a fact because no span
    # says which way a slab is thin.
    n = 0
    counts: dict = {"x": 0, "y": 0, "z": 0}
    plate_groups = [
        (g, pd)
        for g in plan.of_kind("plate")
        if (pd := g.dim(role="thickness", kind="length")) is not None and pd.span is not None
    ]

    # Preserve the pre-boundary stable identity order: axis, then the plate's lower and
    # upper coordinates ALONG that thin axis. Sorting whole points would compare their
    # in-plane coordinates first and silently swap dim_plate_{axis}{i} names when two
    # same-axis plates move sideways (#923 adversarial review).
    def _plate_order(gp):
        axis = gp[0].facts.axis
        idx = "xyz".index(axis)
        return (axis, gp[1].span[0][idx], gp[1].span[1][idx])

    for g, pd in sorted(plate_groups, key=_plate_order):
        axis = g.facts.axis
        # The span's two ends ARE the plate's lo/hi along its thin axis, and its other two
        # coordinates are the in-plane centroids the witness sits at — `PlateFeature._span`
        # builds it from exactly those. So the renderer reads points, not a feature.
        lo_pt, hi_pt = pd.span
        oi = [j for j in (0, 1, 2) if j != "xyz".index(axis)]
        lo, hi = lo_pt["xyz".index(axis)], hi_pt["xyz".index(axis)]
        u, v = lo_pt[oi[0]], lo_pt[oi[1]]
        val = pd.value
        lbl = pd.value_text + _tol_suffix(pd.tolerance, draft)
        i = counts[axis]
        counts[axis] += 1
        if axis == "z":
            # Horizontal slab (base plate): vertical dim on the front-elevation left strip.
            # For a Z plate the in-plane centroids are (u=X, v=Y); the front view discards
            # Y, so the depth arg is inert, but pass the Y-centroid (v) for correctness.
            view, strip, stack, side = "front", a.fv_zones.left, "x", "left"
            p1 = dwg.at(view, a.bb.min.X, v, lo)
            p2 = dwg.at(view, a.bb.min.X, v, hi)
            edge = p1[0]
            pa, pb = (edge, p1[1], 0), (edge, p2[1], 0)
            # Right-strip fallthrough anchors (helpers ≥0.14): a tight-span thickness
            # dim's witness hull overlaps the below strip's at the view corner at EVERY
            # position (AABB artifact — the ink never touches), so a full left strip
            # retries on the opposite side before dropping.
            q1 = dwg.at(view, a.bb.max.X, v, lo)
            s1 = dwg.at("side", a.bb.min.X, a.bb.max.Y, lo)
            s2 = dwg.at("side", a.bb.min.X, a.bb.max.Y, hi)
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
        elif axis == "y":
            # Upright wall: horizontal dim above the side (end) view, which shows the
            # wall edge-on on the L-profile — a different view from the Z base plate.
            # Witness from the view's top edge (like the Z/X plates anchor at their view
            # outline) so the extension lines don't originate mid-view.
            view, strip, stack, side = "side", a.sv_zones.above, "y", "above"
            p1 = dwg.at(view, a.bb.min.X, lo, a.bb.max.Z)
            p2 = dwg.at(view, a.bb.min.X, hi, a.bb.max.Z)
            edge = p1[1]
            pa, pb = (p1[0], edge, 0), (p2[0], edge, 0)
            alt = None
        else:  # x — thin wall along X → horizontal dim below the front view
            view, strip, stack, side = "front", a.fv_zones.below, "y", "below"
            p1 = dwg.at(view, lo, u, a.bb.min.Z)
            p2 = dwg.at(view, hi, u, a.bb.min.Z)
            edge = p1[1]
            pa, pb = (p1[0], edge, 0), (p2[0], edge, 0)
            alt = None
        name = f"dim_plate_{axis}{i}"

        def _build(pos, pa=pa, pb=pb, side=side, edge=edge, lbl=lbl):
            return _dim(pa, pb, side, pos - edge, draft, label=lbl)

        def _foot(pos, pa=pa, pb=pb, side=side, edge=edge, lbl=lbl):
            return dim_footprint(pa, pb, side, pos - edge, draft, lbl)

        def _drop(nm, val=val, lbl=lbl, view=view, stack=stack, alt=alt, feat=g.ref, mid=pd.id):  # noqa: B008
            # Opposite-strip fallthrough (mirrors the GD&T #481 pattern), DEFERRED to
            # ctx.post_drain so it runs after EVERY corridor has drained (#684 review):
            # a mid-drain carve could occupy a corner a later sibling's force candidate
            # needs; post-drain, carve_free_position sees all placed annotations.
            # `mid` is bound as a DEFAULT like every sibling here: `pd` is the enclosing
            # loop's variable and these retries run post-drain, so reading it live would
            # record the LAST plate's identity on every one of them (#1002).
            def _retry(
                nm=nm, val=val, lbl=lbl, view=view, stack=stack, alt=alt, feat=feat, mid=mid
            ):
                for view2, side2, strip2, axis2, qa, qb, edge2 in alt or ():
                    if strip2 is None:
                        continue
                    foot0 = dim_footprint(qa, qb, side2, tier, draft, lbl)
                    perp = (foot0[1], foot0[3]) if axis2 == "x" else (foot0[0], foot0[2])
                    pos = carve_free_position(dwg, strip2, view2, axis2, tier, perp)
                    if pos is not None:
                        # Accept-time validation (#684 r2): the carve accepted the
                        # ANALYTICAL footprint — build once and re-check the real box
                        # against live obstacles + the page before adding (the same
                        # contract as the corridor's validation fallback). A miss
                        # tries the next alternate.
                        dim = _dim(qa, qb, side2, pos - edge2, draft, label=lbl)
                        real = _geom_box(dim)
                        page = (_MARGIN, _MARGIN, a.PAGE_W - _MARGIN, a.PAGE_H - _MARGIN)
                        if real is None or (
                            _box_hits(
                                real, strip_obstacles(dwg, view=view2, crossable=CROSSABLE_TYPES)
                            )
                            or real[0] < page[0]
                            or real[1] < page[1]
                            or real[2] > page[2]
                            or real[3] > page[3]
                        ):
                            continue
                        ctx.place(dim, nm, view=view2, feature=feat, measurement=mid)
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
                feature=g.ref,  # opaque provenance handle
                measurement=pd.id,  # #1002
                footprint=_foot,  # analytical measure — no probe build (#602)
            ),
        )
        n += 1

    channel_groups = [
        (g, pd)
        for g in plan.of_kind("channel")
        if (pd := g.dim(role="channel_width", kind="length")) is not None and pd.span is not None
    ]
    channel_counts: dict[str, int] = {"x": 0, "y": 0, "z": 0}
    view_for_long_axis = {"x": "side", "y": "front", "z": "plan"}
    zones_for_view = {"front": a.fv_zones, "side": a.sv_zones, "plan": a.pv_zones}
    for g, pd in sorted(
        channel_groups,
        key=lambda gp: (
            gp[0].facts.long_axis,
            gp[0].facts.width_axis,
            gp[1].span[0],
            gp[1].span[1],
        ),
    ):
        facts = g.facts
        view = view_for_long_axis[facts.long_axis]
        p1 = dwg.at(view, *pd.span[0])
        p2 = dwg.at(view, *pd.span[1])

        outward = list(pd.span[0])
        depth_index = "xyz".index(facts.depth_axis)
        outward[depth_index] += facts.open_sign
        q = dwg.at(view, *outward)
        depth_dx, depth_dy = q[0] - p1[0], q[1] - p1[1]
        width_dx, width_dy = p2[0] - p1[0], p2[1] - p1[1]
        if abs(width_dx) >= abs(width_dy):
            side = "above" if depth_dy > 0 else "below"
            stack = "y"
            edge = p1[1]
            pa, pb = (p1[0], edge, 0), (p2[0], edge, 0)
        else:
            side = "right" if depth_dx > 0 else "left"
            stack = "x"
            edge = p1[0]
            pa, pb = (edge, p1[1], 0), (edge, p2[1], 0)
        strip = getattr(zones_for_view[view], side)
        if strip is None:
            # Some sheet layouts abut one side of a view directly against its sibling and
            # therefore expose no corridor on the channel's opening side. The opposite
            # profile corridor still dimensions the same two wall witnesses; use it rather
            # than treating an unavailable strip as a physically full one.
            side = {"above": "below", "below": "above", "left": "right", "right": "left"}[side]
            strip = getattr(zones_for_view[view], side)
        label = pd.value_text + _tol_suffix(pd.tolerance, draft)
        index = channel_counts[facts.width_axis]
        channel_counts[facts.width_axis] += 1
        name = f"dim_channel_{facts.width_axis}{index}"

        def _build(pos, pa=pa, pb=pb, side=side, edge=edge, label=label):
            return _dim(pa, pb, side, pos - edge, draft, label=label)

        def _foot(pos, pa=pa, pb=pb, side=side, edge=edge, label=label):
            return dim_footprint(pa, pb, side, pos - edge, draft, label)

        def _drop_channel(_name, value=pd.value, view=view, side=side, mid=pd.id):
            ctx.record_issue(
                "warning",
                "channel_width_dropped",
                f"channel width {_fmt(value)} not dimensioned ({view} {side}-strip full)",
                measurement=mid,
            )

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
                order=(_SIZE_SUBCHAIN, index, name),
                on_place=lambda _name: None,
                on_drop=_drop_channel,
                force=True,
                feature=g.ref,
                measurement=pd.id,
                footprint=_foot,
            ),
        )
        n += 1
    return n


def render_envelope(dwg, plan, a, *, ctx) -> int:
    """Overall width (plan, below) + depth (side, below) envelope dims via the IR,
    registered into the same below-strip corridor as feature/location/GD&T/PMI candidates.
    The overall dims use the last ladder subchain so they stack outermost by construction,
    while their mandatory priority prevents best-effort below-strip occupants from starving
    principal dimensions. The **planner** decides suppression (the rotational OD's cross-axis
    extents, X/Z-turned; #250) — there is no square-footprint rule since #997, so a square
    part arrives with both extents. Suppressed entries never arrive. Returns the count
    queued."""
    envs = plan.of_kind("envelope")
    env = envs[0] if envs else None
    if env is None:
        return 0
    n = 0

    def _queue(name, strip, view, tier, distance, build, footprint=None, measurement=None):
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
                measurement=measurement,  # which envelope extent this is (#1002)
                footprint=footprint,  # analytical measure — no probe build (#602)
            ),
        )

    width = env.dim(role="width")
    if width is not None and width.span is not None:
        (x0, y0, z0), (x1, _, _) = width.span
        p1, p2 = dwg.at("plan", x0, y0, z0), dwg.at("plan", x1, y0, z0)
        witness = p1[1] - _WITNESS_LIFT_MM
        _queue(
            "m_env_width",
            a.pv_zones.below,
            "plan",
            _SLOT_DIM_WIDTH,
            abs(x1 - x0),
            lambda pos, _p1=p1, _p2=p2, _w=witness, _v=width.value_text: _dim(
                (_p1[0], _w, 0),
                (_p2[0], _w, 0),
                "below",
                _w - pos,
                dwg.draft,
                label=_v,
            ),
            footprint=lambda pos, _p1=p1, _p2=p2, _w=witness, _v=width.value_text: dim_footprint(
                (_p1[0], _w, 0), (_p2[0], _w, 0), "below", _w - pos, dwg.draft, _v
            ),
            measurement=width.id,
        )
        n += 1
    depth = env.dim(role="depth")
    if depth is not None and depth.span is not None:
        (x0, y0, z0), (_, y1, _) = depth.span
        p1, p2 = dwg.at("side", x0, y0, z0), dwg.at("side", x0, y1, z0)
        witness = p1[1] - _WITNESS_LIFT_MM
        _queue(
            "m_env_depth",
            a.sv_zones.below,
            "side",
            _SLOT_DIM_DEPTH,
            abs(y1 - y0),
            lambda pos, _p1=p1, _p2=p2, _w=witness, _v=depth.value_text: _dim(
                (_p1[0], _w, 0),
                (_p2[0], _w, 0),
                "below",
                _w - pos,
                dwg.draft,
                label=_v,
            ),
            footprint=lambda pos, _p1=p1, _p2=p2, _w=witness, _v=depth.value_text: dim_footprint(
                (_p1[0], _w, 0), (_p2[0], _w, 0), "below", _w - pos, dwg.draft, _v
            ),
            measurement=depth.id,
        )
        n += 1
    return n


@dataclass(frozen=True)
class _StepChainSegment:
    """One step-length claim as it moves from part space through page projection.

    The old positional tuples lost the compiled measurement id when they were rebuilt by
    projection, repeat-run collapse, and detail redraws. Keeping the claim named makes those
    transformations state explicitly whether they preserve, combine, or intentionally omit
    identity (#1004).
    """

    pa: tuple[float, float, float]
    pb: tuple[float, float, float]
    value: float
    tolerance: Any = None
    measurements: tuple[Any, ...] = ()
    label: str | None = None


def _step_measurements(segs: list[_StepChainSegment]) -> tuple[Any, ...]:
    """Stable union of the measurements represented by *segs*."""
    result: list[Any] = []
    for seg in segs:
        for measurement in seg.measurements:
            if measurement not in result:
                result.append(measurement)
    return tuple(result)


def _record_step_chain_drop(dwg, why: str, *, ctx, measurement=()) -> None:
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
        measurement=measurement,
    )


def _draw_step_chain(
    dwg, view, segs, name_prefix, detail_scale=None, allow_collapse=True, *, ctx, start=0
) -> int:
    """Place a turned step-length chain in *view* from structured *segs*, each already
    projected to *view*'s page coords in axis order. Orientation is
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
    trace = getattr(ctx, "trace", None)  # the immediate placers report to the trace too (#736)
    ev = trace.pass_event("step_length_chain", view=view) if trace is not None else None
    x0, y0, x1, y1 = vb
    draft = dwg.draft
    gap = draft.font_size + 4 * draft.pad_around_text
    horizontal = abs(segs[0].pb[0] - segs[0].pa[0]) >= abs(segs[0].pb[1] - segs[0].pa[1])
    vals = [seg.value for seg in segs]
    labels = [seg.label if seg.label is not None else _fmt(seg.value) for seg in segs]
    mean_v = sum(vals) / len(vals)
    if (
        allow_collapse
        and all(seg.label is None for seg in segs)
        and len(segs) >= 3
        and (max(vals) - min(vals)) <= 0.10 * mean_v
    ):
        # A uniform run collapses to one "N× v" dim; a per-step ± would be a false claim on
        # N equal steps, so the collapse carries NO tolerance (#28 / P2a).
        label = f"{len(segs)}× {_fmt(mean_v)}"
        xs = [p[0] for seg in segs for p in (seg.pa, seg.pb)]
        ys = [p[1] for seg in segs for p in (seg.pa, seg.pb)]
        if horizontal:
            dim = _dim((min(xs), y1, 0), (max(xs), y1, 0), "above", gap, draft, label=label)
        else:
            dim = _dim((x1, min(ys), 0), (x1, max(ys), 0), "right", gap, draft, label=label)
        typ_name = f"{name_prefix}_typ" if start == 0 else f"{name_prefix}_typ{start}"
        candidates = [(typ_name, dim, _step_measurements(segs))]
    else:
        tier_step = draft.font_size + 2 * draft.pad_around_text
        tiers = [0] * len(segs)
        if horizontal:
            cw = [
                (
                    (seg.pa[0] + seg.pb[0]) / 2,
                    len(labels[i]) * draft.font_size * _EST_CHAR_WIDTH_EM,
                )
                for i, seg in enumerate(segs)
            ]

            def _clear(items):
                return all(
                    c2 - c1 >= (w1 + w2) / 2 + draft.pad_around_text
                    for (c1, w1), (c2, w2) in zip(items, items[1:])
                )

            if _clear(cw):
                pass
            elif _clear(cw[0::2]) and _clear(cw[1::2]):
                tiers = [i % 2 for i in range(len(segs))]
            else:
                _log.info("step-length chain skipped: too dense even when staggered")
                _record_step_chain_drop(
                    dwg,
                    "shoulders too dense to dimension even when staggered",
                    ctx=ctx,
                    measurement=_step_measurements(segs),
                )
                if ev is not None:
                    ev["items"].append(
                        {"name": name_prefix, "outcome": "dropped", "reason": "too_dense"}
                    )
                return 0
        else:
            shoulder_ys = sorted({c for seg in segs for c in (seg.pa[1], seg.pb[1])})
            if any(b - a < tier_step for a, b in zip(shoulder_ys, shoulder_ys[1:])):
                _log.info("step-length chain skipped: shoulders too close to dimension")
                _record_step_chain_drop(
                    dwg,
                    "turned shoulders too closely spaced to dimension",
                    ctx=ctx,
                    measurement=_step_measurements(segs),
                )
                if ev is not None:
                    ev["items"].append(
                        {
                            "name": name_prefix,
                            "outcome": "dropped",
                            "reason": "shoulders_too_close",
                        }
                    )
                return 0

        candidates = []
        for i, seg in enumerate(segs):
            if horizontal:
                p1, p2, side = (seg.pa[0], y1, 0), (seg.pb[0], y1, 0), "above"
                dist = gap + tiers[i] * tier_step
            else:
                p1, p2, side = (x1, seg.pa[1], 0), (x1, seg.pb[1], 0), "right"
                dist = gap
            candidates.append(
                (
                    f"{name_prefix}{start + i}",
                    _dim(
                        p1,
                        p2,
                        side,
                        dist,
                        draft,
                        label=labels[i],
                        tolerance=seg.tolerance,
                    ),
                    seg.measurements,
                )
            )

    # Room guard: if any dim would fall off the drawable page, place NONE.
    page = (_MARGIN, _MARGIN, dwg.page_w - _MARGIN, dwg.page_h - _MARGIN)
    for _, dim, _measurements in candidates:
        box = _anno_box(dim)
        if box is not None and not (
            page[0] <= box[0] and box[2] <= page[2] and page[1] <= box[1] and box[3] <= page[3]
        ):
            _record_step_chain_drop(
                dwg,
                "a dimension would fall off the drawable page",
                ctx=ctx,
                measurement=_step_measurements(segs),
            )
            if ev is not None:
                ev["items"].append(
                    {"name": name_prefix, "outcome": "dropped", "reason": "off_page"}
                )
            return 0
    for name, dim, measurements in candidates:
        if detail_scale is not None:
            dim._dw_scale = detail_scale
        ctx.place(dim, name, view=view, measurement=measurements)
        if ev is not None:
            b = _anno_box(dim)
            ev["items"].append(
                {"name": name, "outcome": "placed", "box": list(b) if b is not None else None}
            )
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


def render_step_lengths(dwg, plan, *, ctx, only=None) -> int:
    """Unified turned step-length chain (ADR 0008 #223): each `StepFeature`'s length
    span projects into the profile view and joins the chain that tiles the turning
    axis so every shoulder is located. X-turned → horizontal chain above the front
    view; Z-turned → vertical chain to its right; Y-turned → horizontal chain above
    the side view.

    A crowded **X-turned head** — a contiguous run of steps too short to dimension
    legibly even staggered (shoulders below the page arrowhead floor) — is not crammed
    in line: the main view locates that run as one *block* dim and an enlarged
    `DetailRequest` (#304/#307) is queued to break it down. If the detail later can't
    place, the block still locates the head extent and lint reports the un-located
    interior shoulders — never worse than the prior skip. Returns the count placed."""
    rows: list[tuple[str, _StepChainSegment]] = []
    step_origins = []
    for g in plan.of_kind("step"):
        if g.facts.frame.axis not in ("x", "y", "z"):
            continue
        if only is not None and g.ref not in only:  # #426 finalize: recorded subset
            continue
        length = g.dim(kind="length")
        if length is None or length.span is None:
            continue
        rows.append(
            (
                g.facts.frame.axis,
                _StepChainSegment(
                    length.span[0],
                    length.span[1],
                    length.value,
                    length.tolerance,
                    (length.id,) if length.id is not None else (),
                ),
            )
        )
        step_origins.append(g.facts.frame.origin)
    if not rows:
        return 0
    draft = dwg.draft
    # only=None (auto-pass) → start=0, historical m_steplen naming, byte-identical. The
    # finalize path (only set) starts past existing m_steplen names (#426 naming seam).
    start = _next_steplen_start(ctx) if only is not None else 0
    axes = {axis for axis, _seg in rows}
    if len(axes) != 1:
        _log.warning(
            "step-length chain has mixed axes; rendering each axis requires separate groups"
        )
        return 0
    turn_axis = next(iter(axes))
    view = "side" if turn_axis == "y" else "front"
    bare_rows = [seg for _axis, seg in rows]
    fsegs = [replace(seg, pa=dwg.at(view, *seg.pa), pb=dwg.at(view, *seg.pb)) for seg in bare_rows]
    horizontal = abs(fsegs[0].pb[0] - fsegs[0].pa[0]) >= abs(fsegs[0].pb[1] - fsegs[0].pa[1])

    # A Y-turned chain that would need near/far staggering is ambiguous in the
    # narrow side view: an interior far-tier segment reads like an overall
    # dimension (#892). Keep one overall block on the side view and redraw the
    # complete shoulder chain in a true enlarged side-profile detail instead.
    if horizontal and turn_axis == "y" and len(fsegs) >= 2:
        label_widths = [
            _text_size(
                _fmt(seg.value) + _tol_suffix(seg.tolerance, draft),
                draft.font_size,
                font=getattr(draft, "font", "Arial"),
            )[0]
            for seg in bare_rows
        ]
        cw = sorted(((seg.pa[0] + seg.pb[0]) / 2, label_widths[i]) for i, seg in enumerate(fsegs))
        labels_clear = all(
            c2 - c1 >= (w1 + w2) / 2 + draft.pad_around_text
            for (c1, w1), (c2, w2) in zip(cw, cw[1:])
        )
        inside_arrows_fit = all(
            abs(seg.pb[0] - seg.pa[0])
            >= label_widths[i] + 2 * draft.arrow_length + 2 * draft.pad_around_text
            for i, seg in enumerate(fsegs)
        )
        # A long repeated-pitch tail can be stated once on the main view. This
        # removes several competing short labels and may make the remaining
        # isolated links readable without an enlarged detail (#881/#896).
        ordered = sorted(fsegs, key=lambda seg: (seg.pa[0] + seg.pb[0]) / 2)
        compact: list[_StepChainSegment] = []
        collapsed = False
        j = 0
        while j < len(ordered):
            repeat_run = [ordered[j]]
            k = j + 1
            while k < len(ordered):
                prev, cur = repeat_run[-1], ordered[k]
                contiguous = abs(max(prev.pa[0], prev.pb[0]) - min(cur.pa[0], cur.pb[0])) <= 1e-4
                if not (
                    contiguous
                    and _fmt(cur.value) == _fmt(repeat_run[0].value)
                    and prev.tolerance is None
                    and cur.tolerance is None
                ):
                    break
                repeat_run.append(cur)
                k += 1
            if len(repeat_run) >= 3:
                xs = [p[0] for seg in repeat_run for p in (seg.pa, seg.pb)]
                y = repeat_run[0].pa[1]
                pitch = sum(seg.value for seg in repeat_run) / len(repeat_run)
                compact.append(
                    _StepChainSegment(
                        (min(xs), y, 0.0),
                        (max(xs), y, 0.0),
                        sum(seg.value for seg in repeat_run),
                        measurements=_step_measurements(repeat_run),
                        label=f"{len(repeat_run)}× {_fmt(pitch)}",
                    )
                )
                collapsed = True
            else:
                compact.extend(repeat_run)
            j = k
        if collapsed:
            return _draw_step_chain(
                dwg,
                view,
                compact,
                "m_steplen",
                allow_collapse=False,
                ctx=ctx,
                start=start,
            )
        if not (labels_clear and inside_arrows_fit):
            axis_lo = min(min(seg.pa[1], seg.pb[1]) for seg in bare_rows)
            axis_hi = max(max(seg.pa[1], seg.pb[1]) for seg in bare_rows)
            page_xs = [p[0] for seg in fsegs for p in (seg.pa, seg.pb)]
            page_y = fsegs[0].pa[1]
            block = [
                _StepChainSegment(
                    (min(page_xs), page_y, 0.0),
                    (max(page_xs), page_y, 0.0),
                    axis_hi - axis_lo,
                )
            ]
            scale_needed = max(
                (w + 2 * draft.arrow_length + 2 * draft.pad_around_text) / row.value
                for w, row in zip(label_widths, bare_rows)
                if row.value > 0
            )

            # Use the detected turning axis, not the sheet/bounding-box centroid:
            # an eccentric shaft's profile may be nowhere near the latter. A single
            # side-profile detail is valid only for a coaxial chain.
            axis_xs = [origin[0] for origin in step_origins]
            axis_zs = [origin[2] for origin in step_origins]
            coaxial = max(axis_xs) - min(axis_xs) <= 0.5 and max(axis_zs) - min(axis_zs) <= 0.5
            if not coaxial:
                return _draw_step_chain(dwg, view, fsegs, "m_steplen", ctx=ctx, start=start)
            axis_z = sum(axis_zs) / len(axis_zs)

            # Choose the same standard scale family as the detail renderer, then
            # crop a geometry-relative strip: at most one quarter of the side-view
            # silhouette and at most 12 page-mm tall. The view rectangle is layout
            # geometry; unlike the old `StepFeature.diameter` read it cannot recover a
            # withheld printable diameter from the source feature.
            detail_target = dwg.scale * 10
            for factor in (2, 5, 10):
                candidate = dwg.scale * factor
                if candidate >= scale_needed:
                    detail_target = candidate
                    break
            _sx0, sy0, _sx1, sy1 = dwg.view_bounds("side")
            silhouette_quarter = abs(sy1 - sy0) / (4 * dwg.scale)
            cross_half = max(0.1, min(silhouette_quarter, 6.0 / detail_target))

            def _redraw_y(dwg, detail_view, coords, detail_scale, _rows=bare_rows):
                def _at(x, y, z):
                    px, py = coords.pp(x, y, z)
                    return (px, py, 0.0)

                dpairs = [
                    (replace(seg, pa=_at(*seg.pa), pb=_at(*seg.pb)), label_widths[i])
                    for i, seg in enumerate(_rows)
                ]
                dpairs.sort(key=lambda item: (item[0].pa[0] + item[0].pb[0]) / 2)
                dsegs: list[_StepChainSegment] = []
                detail_widths: list[float] = []
                j = 0
                while j < len(dpairs):
                    seg0, _width0 = dpairs[j]
                    run = [dpairs[j]]
                    k = j + 1
                    while k < len(dpairs):
                        prev, _prev_width = run[-1]
                        cur, _cur_width = dpairs[k]
                        contiguous = (
                            abs(max(prev.pa[0], prev.pb[0]) - min(cur.pa[0], cur.pb[0])) <= 1e-4
                        )
                        # A repeated-pitch claim must be true at the drawing's
                        # displayed precision, not merely "within 10%".
                        equal = _fmt(cur.value) == _fmt(seg0.value)
                        untoleranced = prev.tolerance is None and cur.tolerance is None
                        if not (contiguous and equal and untoleranced):
                            break
                        run.append(dpairs[k])
                        k += 1
                    if len(run) >= 3:
                        run_segs = [seg for seg, _width in run]
                        xs = [p[0] for seg in run_segs for p in (seg.pa, seg.pb)]
                        y = run_segs[0].pa[1]
                        pitch = sum(seg.value for seg in run_segs) / len(run_segs)
                        label = f"{len(run_segs)}× {_fmt(pitch)}"
                        dsegs.append(
                            _StepChainSegment(
                                (min(xs), y, 0.0),
                                (max(xs), y, 0.0),
                                sum(seg.value for seg in run_segs),
                                measurements=_step_measurements(run_segs),
                                label=label,
                            )
                        )
                        detail_widths.append(
                            _text_size(
                                label,
                                draft.font_size,
                                font=getattr(draft, "font", "Arial"),
                            )[0]
                        )
                    else:
                        for seg, width in run:
                            dsegs.append(seg)
                            detail_widths.append(width)
                    j = k
                # Never recreate the ambiguous stagger inside a detail. Validate
                # both text clearance and full inside-arrow capacity at the actual
                # fitted scale; returning zero transactionally drops the detail.
                dcw = sorted(
                    ((seg.pa[0] + seg.pb[0]) / 2, detail_widths[i]) for i, seg in enumerate(dsegs)
                )
                if not all(
                    c2 - c1 >= (w1 + w2) / 2 + draft.pad_around_text
                    for (c1, w1), (c2, w2) in zip(dcw, dcw[1:])
                ):
                    _log.info(
                        "Y-chain detail rejected at scale %.3g: labels still collide",
                        detail_scale,
                    )
                    return 0
                if any(
                    abs(seg.pb[0] - seg.pa[0])
                    < detail_widths[i] + 2 * draft.arrow_length + 2 * draft.pad_around_text
                    for i, seg in enumerate(dsegs)
                ):
                    _log.info(
                        "Y-chain detail rejected at scale %.3g: label + inside arrows "
                        "do not fit a segment",
                        detail_scale,
                    )
                    return 0
                return _draw_step_chain(
                    dwg,
                    detail_view,
                    dsegs,
                    f"dim_{detail_view}_steplen",
                    detail_scale,
                    ctx=ctx,
                )

            ctx.detail_requests.append(
                DetailRequest(
                    axis="y",
                    lo=axis_lo,
                    hi=axis_hi,
                    scale_needed=scale_needed,
                    redraw=_redraw_y,
                    pad_top=draft.font_size + 2 * draft.pad_around_text + draft.arrow_length,
                    source_view="side",
                    cross_axis="z",
                    cross_lo=axis_z - cross_half,
                    cross_hi=axis_z + cross_half,
                    kind="y-turned-chain",
                )
            )
            return _draw_step_chain(
                dwg,
                "side",
                block,
                "m_steplen",
                allow_collapse=False,
                ctx=ctx,
                start=start,
            )

    # X-turned crowded-head detour (#307): split off each contiguous *run of ≥2*
    # sub-floor steps (segment narrower than two arrowheads on the page), locate it as
    # a block, and queue an enlarged detail. A single isolated thin step is left in the
    # main chain — a one-step block would just be that step at its sub-floor width
    # (#307 review). The legible steps + blocks stay as the main chain.
    if horizontal and turn_axis == "x":
        floor_pg = 2 * draft.arrow_length
        sub = [i for i, seg in enumerate(fsegs) if abs(seg.pb[0] - seg.pa[0]) < floor_pg]
        runs: list[list[int]] = []
        for j in sub:
            (runs[-1].append(j) if runs and j == runs[-1][-1] + 1 else runs.append([j]))
        heads = [run for run in runs if len(run) >= 2]
        if heads:
            blocks = []
            for run in heads:
                ra = [bare_rows[i] for i in run]
                hlo = min(min(seg.pa[0], seg.pb[0]) for seg in ra)
                hhi = max(max(seg.pa[0], seg.pb[0]) for seg in ra)
                minlen = min(seg.value for seg in ra)
                # World→page scale for the detail (no sheet factor — detail_scale is an
                # absolute world→page scale). (#307 review)
                scale_needed = _MIN_STEP_SEP_MM / minlen if minlen > 0 else float("inf")
                # A head *block* is a synthetic span, not one toleranced step — carry no ± (None).
                blocks.append(
                    _StepChainSegment(
                        dwg.at("front", hlo, 0, 0),
                        dwg.at("front", hhi, 0, 0),
                        hhi - hlo,
                    )
                )

                def _redraw(dwg, view, coords, detail_scale, _hw=ra):
                    # View-scoped name prefix so two detail views never collide (#307 review).
                    # Map world→page against the detail coords (not a live dwg.at) so the view can be
                    # committed only after these dims land — no place-then-roll-back (#840).
                    def _at(x, y, z):
                        px, py = coords.pp(x, y, z)
                        return (px, py, 0.0)

                    hsegs = [replace(seg, pa=_at(*seg.pa), pb=_at(*seg.pb)) for seg in _hw]
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
            main.sort(key=lambda seg: seg.pa[0])
            # The chain now mixes head-block(s) with real steps — never collapse it to a
            # uniform "N× v" representative (a block is not a repeated step, #307 review).
            return _draw_step_chain(
                dwg, "front", main, "m_steplen", allow_collapse=False, ctx=ctx, start=start
            )

    return _draw_step_chain(dwg, view, fsegs, "m_steplen", ctx=ctx, start=start)


def ladder_plan_for(plan, *, step_height: bool, overall: bool):
    """*plan* narrowed to the ladders a caller actually asked :func:`render_height_ladder` for.

    The renderer draws TWO independent things — a `step_level` feature's correlated rungs and
    the envelope/bbox overall height — and reads a third, `step_position`, only for its
    PRESENCE (short-rung placement). Handing it a plan containing more than was asked for
    draws more than was asked for: the #889 drain passed the whole compiled plan once either
    intent was recorded, so `overall_height()` alone also rebuilt the step rungs — a
    dimension nobody recorded, and live/deferred divergence in the one PR relying on their
    equivalence (#934 review).

    Exists so the live verb and the finalize drain project the plan the SAME way. Two
    spellings of "which ladders did they ask for" is how they diverged in the first place.

    `step_position` rides `step_height`: it is not content here, it is how those rungs are
    placed, so it is meaningless without them.
    """
    kinds = []
    if step_height:
        kinds += ["step_height", "step_position"]
    if overall:
        kinds.append("overall_height")
    return replace(plan, ladders=tuple(lad for lad in plan.ladders if lad.kind in kinds))


def render_height_ladder(dwg, plan, frame, *, ctx, detail_view: bool = False) -> int:
    """Front-view right ladder: prismatic step heights stacked inner→outer, then the overall
    height outermost — registered as :class:`CorridorCandidate`s in the shared
    ``(front, right)`` corridor (#636). The leapfrog witness cursor (#237) survives as a
    *build-time chain*: candidates share a ``solved`` position map, and each dim's witness
    anchors on its nearest already-built predecessor's line (the view edge for the first).

    **The first renderer migrated to the ADR 0016 boundary.** It takes the compiled
    :class:`RenderableDimensionPlan` and a :class:`LayoutFrame`, not the `PartModel` and the
    `Analysis`. Everything it used to decide about WHAT to draw — which rungs exist, their
    values and labels, whether a uniform staircase collapses to one ``n×`` mark, whether the
    overall height is drawn at all and what its value is — now arrives already decided. It
    could previously reach `StepLevelFeature.levels` and `a.bb` and rebuild all of it, and
    for four review rounds it did exactly that in defiance of the plan.

    What stays here is placement, and it is a real job: legibility at this scale, the
    leapfrog chain, corridor registration, the left-strip escape for short rises, and the
    lint code when the strip is physically full. Note the two kinds of "not drawn" that meet
    here and must not be confused — the compiler's omission (never arrives; reported through
    the plan's diagnostics) and this pass's drop (arrived, did not fit; reported as
    ``placement_unsatisfiable``). Returns the count REGISTERED."""
    draft = dwg.draft
    _left, right, _bottom, _top = frame.edges("front")
    edge2 = right + 2
    tier = draft.font_size + 2 * draft.pad_around_text

    def _zspan(entry):
        """An entry's witness ends, projected from ITS OWN span.

        Anchoring every rung at the view's bottom edge instead was wrong the moment the
        compiler started measuring from `StepLevelFeature.base`: a declared base above the
        part's bottom made the drawn line span the full part while the label read the
        shorter distance, so the dimension said one thing and measured another (#923
        review). The span is the compiler's statement of what is being measured; projecting
        both ends of it is what keeps line and label the same claim."""
        return (
            frame.project("front", entry.span[0])[1],
            frame.project("front", entry.span[1])[1],
        )

    strip = frame.fv_zones.right

    rung_set = plan.ladder("step_height")
    rungs = list(rung_set.rungs) if rung_set is not None else []
    # An OPAQUE handle, passed straight through to the corridor candidate and the
    # escalation. This pass never resolves it: the feature behind it carries the levels
    # and the base, which is the content the compiler already ruled on (#923 review).
    step = rung_set.ref if rung_set is not None else None
    has_shoulders = plan.ladder("step_position") is not None
    short_rungs: list = []

    # The chain, inner→outer: (name, page-z span, label, tier size, drop message, dim id,
    # per-unit value). The last is the number the LABEL's `N×` prefix multiplies — set only
    # for the representative rung, whose "8× 15" is one 15 mm step rather than a 120 mm run
    # (#1153). Carried from `ApprovedDimension.value` so lint compares against the
    # compiler's own number instead of re-deriving a convention from the rendered string,
    # which is the pattern ADR 0016 Amendment 1 exists to stop.
    chain: list = []
    if rung_set is not None and rung_set.representative:
        (rep,) = rungs
        chain.append(
            (
                "dim_step_typ",
                *_zspan(rep),
                rep.final_label,
                _SLOT_DIM_STEP,
                "representative step-height dimension dropped (front-view right strip full)",
                rep.id,
                rep.value,
            )
        )
    elif rungs:
        # Legibility is a PLACEMENT decision — whether two rungs are too close to dimension
        # depends on the page, not the model — so it stays here while the rung set itself
        # comes from the compiler. Both bounds come off the approved span, not the bbox.
        kept_z, n_close = _legible_steps(
            [r.span[1][2] for r in rungs],
            rungs[0].span[0][2],
            frame.scale,
            allow_short=has_shoulders,
        )
        kept_level_set = set(kept_z)
        if n_close:
            # When detail recovery is enabled the enlarged view owns the omitted rungs.
            # Report the source-view drop only when no recovery was requested; a failed
            # detail records ``detail_unplaceable`` instead.
            if not detail_view:
                ctx.record_issue(
                    "warning",
                    "step_dim_dropped",
                    f"{n_close} step height(s) too closely spaced to dimension at this scale "
                    "(use a detail view)",
                )
            # First-class escalation alongside the lint code (ADR 0009 Amdt 1, #351 PR-4b) —
            # `_request_prismatic_detail` (sections.py) consumes this instead of recomputing
            # the legibility gate.
            ctx.escalations.append(
                Escalation(
                    kind="step",
                    view="front",
                    feature=step,
                    reason="illegible",
                    targets=tuple(rung for rung in rungs if rung.span[1][2] not in kept_level_set),
                )
            )
        kept = [r for r in rungs if r.span[1][2] in kept_level_set]
        for col, rung in enumerate(kept):
            # A short structural rise needs external arrows, whose ink would swamp the usual
            # right-hand ladder; it goes to the left strip below (#565).
            if has_shoulders and rung.value * frame.scale < _MIN_STEP_DIM_MM:
                short_rungs.append(rung)
                continue
            chain.append(
                (
                    f"dim_step_{col}",
                    *_zspan(rung),
                    rung.final_label,
                    _SLOT_DIM_STEP,
                    "step-height dimension dropped (front-view right strip full)",
                    rung.id,
                    None,  # no `N×` prefix: the label states the span itself
                )
            )

    overall = plan.ladder("overall_height")
    if overall is not None:
        (height,) = overall.rungs
        chain.append(
            (
                "dim_height",
                *_zspan(height),
                height.final_label,
                _SLOT_DIM_HEIGHT,
                "overall height dimension dropped (front-view right strip full)",
                height.id,
                None,  # no `N×` prefix
            )
        )

    names = [c[0] for c in chain]
    solved: dict[str, float] = {}
    for k, (name, zbase, ztop, label, _tsize, drop_msg, mid, per_unit) in enumerate(chain):

        def _build(pos, name=name, zbase=zbase, ztop=ztop, label=label, k=k, per_unit=per_unit):
            base = edge2
            for pn in reversed(names[:k]):  # nearest already-built predecessor's line
                if pn in solved:
                    base = solved[pn]
                    break
            solved[name] = pos
            dim = _dim((base, zbase, 0), (base, ztop, 0), "right", pos - base, draft, label=label)
            if per_unit is not None:
                # What this dimension's `N× v` label actually measures. Lint reads it in
                # preference to parsing the label, because `N× v` is drawn under two
                # conventions here and the string cannot tell them apart: this one is ONE
                # step, while a hole pitch spans the whole run. Same seam as `_dw_scale`.
                dim._dw_label_value = per_unit
            return dim

        def _foot(pos, zbase=zbase, ztop=ztop, label=label, k=k):
            # Predecessor-aware prediction (#689 review): the conservative edge-anchored
            # witness can falsely exhaust the strip when an inner obstacle sits in the
            # already-traversed region. Use the same solved map the build chain uses.
            base = edge2
            for pn in reversed(names[:k]):
                if pn in solved:
                    base = solved[pn]
                    break
            if pos - base < 0.5:  # degenerate guard: never model a zero-length offset
                base = edge2
            return dim_footprint(
                (base, zbase, 0), (base, ztop, 0), "right", pos - base, draft, label
            )

        def _drop(nm, drop_msg=drop_msg, name=name):
            solved.pop(name, None)
            # Name what filled the strip (#736): the #733 diagnosis becomes a glance at the
            # lint message.
            msg = full_strip_message(drop_msg, dwg, strip, "front", "x")
            ctx.record_issue("error", "placement_unsatisfiable", msg)

        register_corridor(
            ctx,
            ("front", "right"),
            strip,
            "front",
            "x",
            tier,
            CorridorCandidate(
                name=name,
                build=_build,
                # Steps stack inner→outer in chain order; the overall height rides the
                # OVERALL subchain so it lands outermost by construction (as the envelope
                # dims do), replacing the old carve's outermost=True.
                order=(
                    (_OVERALL_SUBCHAIN, 0, name)
                    if name == "dim_height"
                    else (_SIZE_SUBCHAIN, k, name)
                ),
                on_place=lambda nm: None,
                on_drop=_drop,
                force=True,  # principal dims: only a physically full strip drops them
                # …and when it IS physically full, they outrank ordinary auto dims rather
                # than tying with them at 0 and losing on the generated key (#894).
                priority=_PRINCIPAL_CHAIN_PRIORITY,
                feature=step if name != "dim_height" else None,
                measurement=mid,  # the rung's own compiled id (#1002)
                footprint=_foot,
            ),
        )
    # A short rise needs external arrows, whose ink occupies most of the usual right-hand
    # height ladder. Solve these exceptional rungs in the equivalent left strip so they do
    # not make the mandatory overall height infeasible.
    left_edge = _left - 2
    for i, rung in enumerate(short_rungs):
        name = f"dim_step_{i}"
        zbase, ztop = _zspan(rung)
        label = rung.final_label

        def _build_left(pos, zbase=zbase, ztop=ztop, label=label):
            return _dim(
                (left_edge, zbase, 0),
                (left_edge, ztop, 0),
                "left",
                left_edge - pos,
                draft,
                label=label,
            )

        def _drop_left(nm):
            msg = full_strip_message(
                "short step-height dimension dropped (front-view left strip full)",
                dwg,
                frame.fv_zones.left,
                "front",
                "x",
            )
            ctx.record_issue("error", "placement_unsatisfiable", msg)

        register_corridor(
            ctx,
            ("front", "left"),
            frame.fv_zones.left,
            "front",
            "x",
            tier,
            CorridorCandidate(
                name=name,
                build=_build_left,
                order=(_SIZE_SUBCHAIN, i, name),
                on_place=lambda nm: None,
                on_drop=_drop_left,
                force=True,
                feature=step,
                measurement=rung.id,  # #1002
                footprint=lambda pos, zbase=zbase, ztop=ztop, label=label: dim_footprint(
                    (left_edge, zbase, 0),
                    (left_edge, ztop, 0),
                    "left",
                    left_edge - pos,
                    draft,
                    label,
                ),
            ),
        )
    return len(chain) + len(short_rungs)


def render_step_positions(dwg, plan, frame, *, ctx) -> int:
    """Prismatic step POSITIONS (#555): where each shoulder sits along its axis,
    dimensioned from the part datum so a stepped block is fully constrained (the step
    heights alone leave the shoulder location implicit — two geometries draw the same
    sheet). A Y shoulder is a horizontal dim on the side (end) view (which maps Y
    horizontally, where the step profile reads); an X shoulder is above the plan view —
    the same axis→view mapping the hole-location ladder uses. Mixed-axis transitions use
    the side-below strip so they do not collide with the isometric furniture above.
    A shoulder whose strip is full drops with a lint code, not silently.

    Migrated to the ADR 0016 boundary: the shoulder chain arrives as the compiled plan's
    ``step_position`` :class:`ApprovedLadder`, and each rung's span carries the datum and
    the station it runs between, so this pass never reaches for `step.shoulders` or the
    bounding box. Returns the count placed."""
    ladder = plan.ladder("step_position")
    rungs = list(ladder.rungs) if ladder is not None else []
    if not rungs:
        return 0
    draft = dwg.draft

    def _axis_of(rung):
        """The compiler-owned shoulder direction.

        A span normally reveals its varying coordinate, but a shoulder on its own datum
        has a degenerate span and reveals no direction at all. Axis is therefore explicit
        structural content on the approved rung, not inferred placement policy."""
        if rung.axis not in ("x", "y"):
            raise AssertionError(f"step-position rung has no X/Y axis: {rung.axis!r}")
        return rung.axis

    axes = {_axis_of(r) for r in rungs}
    mixed_axes = len(axes) > 1
    # Dense transition ladders need only one text tier: arrowhead clearance is
    # along the measured axis, not between outward ladder tiers (#897). Retain
    # the established spacing for ordinary single-axis stepped profiles.
    tier = draft.font_size + (
        draft.pad_around_text if len(rungs) > 2 else 2 * draft.pad_around_text
    )
    n = 0
    counts: dict = {"x": 0, "y": 0}
    # Page-space view edges: the ladder anchors on the view silhouette, which is layout,
    # while the STATIONS come from the approved spans, which is content.
    _sl, _sr, side_bottom, side_top = frame.edges("side")
    _pl, _pr, _pb, plan_top = frame.edges("plan")
    for rung in rungs:
        axis = _axis_of(rung)
        lo, hi = rung.span
        val = rung.value
        i = counts[axis]
        counts[axis] += 1
        if axis == "y" and mixed_axes:
            # Keep mixed-axis Y-profile stations below the side view. The iso
            # caption lives above it and is emitted after the corridor drain,
            # so an above ladder could not see/avoid that furniture (#897).
            view, strip, direction = "side", frame.sv_zones.below, "below"
            p1 = (frame.project(view, lo)[0], side_bottom)
            p2 = (frame.project(view, hi)[0], side_bottom)
        elif axis == "y":
            view, strip, direction = "side", frame.sv_zones.above, "above"
            p1 = (frame.project(view, lo)[0], side_top)
            p2 = (frame.project(view, hi)[0], side_top)
        else:  # x — shoulder along X → above the plan view
            view, strip, direction = "plan", frame.pv_zones.above, "above"
            p1 = (frame.project(view, lo)[0], plan_top)
            p2 = (frame.project(view, hi)[0], plan_top)
        edge = p1[1]
        name = f"dim_shoulder_{axis}{i}"

        def _build(pos, p1=p1, p2=p2, edge=edge, label=rung.final_label, direction=direction):
            return _dim(
                (p1[0], edge, 0),
                (p2[0], edge, 0),
                direction,
                pos - edge if direction == "above" else edge - pos,
                draft,
                label=label,
            )

        def _drop(nm, label=rung.final_label, view=view, direction=direction):
            ctx.record_issue(
                "warning",
                "step_position_dropped",
                f"step position {label} not dimensioned ({view} {direction}-strip full)",
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
            (view, direction),
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
                # The opaque provenance handle, passed straight through.
                feature=ladder.ref,
                measurement=rung.id,  # #1002
            ),
        )
        n += 1
    return n


def _global_axis_centerline(first, second):
    """A turning-axis centreline whose tip attachment is intentional (#1166)."""

    centerline = Centerline(first, second)
    centerline.is_global_axis_centerline = True
    return centerline


def render_rotational(dwg, plan, a, *, ctx) -> int:
    """Rotational furniture from the IR `RotationalFeature` (#237): the OD dim (above
    the front view), the rotation-axis centrelines (front + side), and the concentric
    bore leaders stacked to the left of the front view. Returns the count placed.

    The OD/bore dimensions consume only approved compiled entries. Suppressed entries
    therefore cannot reach either a label or the geometry used to place that label.
    Axis centrelines are furniture and remain even when every diameter is omitted."""
    g = next(iter(plan.of_kind("rotational")), None)
    if g is None:
        return 0
    draft = dwg.draft
    FX, FZ = a.proj.front_x, a.proj.front_z
    SX, SZ = a.proj.side_x, a.proj.side_z
    PX, PY = a.proj.plan_x, a.proj.plan_y
    n = 0
    axis = g.facts.frame.axis
    od_dim = g.dim(kind="diameter", role="od")
    bore_dims = [d for d in g.dims if d.kind == "diameter" and d.role == "bore"]

    def _dia_label(dim):
        # Planner-fed value + authored tolerance/fit suffix (#754).
        return f"ø{dim.value_text}{_tol_suffix(dim.tolerance, draft)}"

    if axis == "z":
        # Vertical turning axis (the common case): OD across the top of the front
        # (profile) view; axis centrelines vertical on front + side.
        if od_dim is not None:
            od = od_dim.value
            ctx.place(
                _dim(
                    (FX(a.cx - od / 2), FZ(a.bb.max.Z) + 2, 0),
                    (FX(a.cx + od / 2), FZ(a.bb.max.Z) + 2, 0),
                    "above",
                    8,
                    draft,
                    label=_dia_label(od_dim),
                ),
                "dim_od",
                view="front",
            )
            n += 1
        ctx.place(
            _global_axis_centerline(
                (FX(a.cx), FZ(a.bb.min.Z) - 5, 0),
                (FX(a.cx), FZ(a.bb.max.Z) + 5, 0),
            ),
            "centerline_front",
            view="front",
        )
        ctx.place(
            _global_axis_centerline(
                (SX(a.cy), SZ(a.bb.min.Z) - 5, 0),
                (SX(a.cy), SZ(a.bb.max.Z) + 5, 0),
            ),
            "centerline_side",
            view="side",
        )

        # Concentric bore leaders to the left of the front view, centred on the axis.
        if bore_dims:
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
                nb = len(bore_dims)
                z_lo, z_hi = a.FV_Y - a.fv_hh, a.FV_Y + a.fv_hh
                cands = [
                    StripCandidate(
                        key=f"{i:03d}",
                        anchor=(elbow_x, FZ(a.cz) + (i - (nb - 1) / 2) * pitch),
                        size=(draft.font_size * 3, pitch),
                        priority=d,
                    )
                    for i, dim in enumerate(bore_dims)
                    for d in (dim.value,)
                ]
                placed = plan_strip(cands, z_lo, z_hi, pitch, axis="y").placed
                for i, dim in enumerate(bore_dims):
                    d = dim.value
                    tip_z = placed.get(f"{i:03d}")
                    if tip_z is None:
                        continue  # over the front-view capacity — dropped (ranked), logged below
                    ctx.place(
                        Leader(
                            tip=(FX(a.cx - d / 2), tip_z, 0),
                            elbow=(elbow_x, tip_z, 0),
                            label=_dia_label(dim),
                            draft=draft,
                        ),
                        f"ldr_z{i}",
                        view="front",
                    )
                    n += 1
                dropped = [
                    dim.value for i, dim in enumerate(bore_dims) if placed.get(f"{i:03d}") is None
                ]
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
                    [dim.value for dim in bore_dims],
                )
    elif axis == "x":
        # Horizontal turning axis along X (#222): the OD is the Z extent — a vertical
        # ø dim left of the front (profile) view; axis centrelines run horizontally
        # through z=cz on front and y=cy on plan.
        if od_dim is not None:
            od = od_dim.value
            ctx.place(
                _dim(
                    (FX(a.bb.min.X) - 2, FZ(a.cz - od / 2), 0),
                    (FX(a.bb.min.X) - 2, FZ(a.cz + od / 2), 0),
                    "left",
                    8,
                    draft,
                    label=_dia_label(od_dim),
                ),
                "dim_od",
                view="front",
            )
            n += 1
        ctx.place(
            _global_axis_centerline(
                (FX(a.bb.min.X) - 5, FZ(a.cz), 0),
                (FX(a.bb.max.X) + 5, FZ(a.cz), 0),
            ),
            "centerline_front",
            view="front",
        )
        ctx.place(
            _global_axis_centerline(
                (PX(a.bb.min.X) - 5, PY(a.cy), 0),
                (PX(a.bb.max.X) + 5, PY(a.cy), 0),
            ),
            "centerline_plan",
            view="plan",
        )
    elif axis == "y":
        # Horizontal turning axis along Y (#222): the OD is the Z extent — a vertical
        # ø dim left of the side (profile) view; axis centrelines run horizontally
        # through z=cz on side and vertically through x=cx on plan.
        if od_dim is not None:
            od = od_dim.value
            ctx.place(
                _dim(
                    (SX(a.bb.min.Y) - 2, SZ(a.cz - od / 2), 0),
                    (SX(a.bb.min.Y) - 2, SZ(a.cz + od / 2), 0),
                    "left",
                    8,
                    draft,
                    label=_dia_label(od_dim),
                ),
                "dim_od",
                view="side",
            )
            n += 1
        ctx.place(
            _global_axis_centerline(
                (SX(a.bb.min.Y) - 5, SZ(a.cz), 0),
                (SX(a.bb.max.Y) + 5, SZ(a.cz), 0),
            ),
            "centerline_side",
            view="side",
        )
        ctx.place(
            _global_axis_centerline(
                (PX(a.cx), PY(a.bb.min.Y) - 5, 0),
                (PX(a.cx), PY(a.bb.max.Y) + 5, 0),
            ),
            "centerline_plan",
            view="plan",
        )
    return n


def render_local_turned_centerlines(dwg, a, *, ctx) -> int:
    """Show the axis of a local turned stack on a non-rotational part.

    Mounting lugs can prevent the complete part from classifying as rotational
    while ``a.prof`` still identifies a coaxial stepped stack. Its centered bore
    may be located by that axis only when the axis is actually present on the
    drawing. Mirror the two centerlines from :func:`render_rotational` without
    adding an overall-OD dimension for the non-rotational envelope (#881).
    """
    prof = a.prof
    if a.is_rotational or prof is None:
        return 0
    axis = prof.axis
    FX, FZ = a.proj.front_x, a.proj.front_z
    SX, SZ = a.proj.side_x, a.proj.side_z
    PX, PY = a.proj.plan_x, a.proj.plan_y
    placed = 0

    def _place(item, name, view):
        nonlocal placed
        if ctx.registry.named(name) is not None:
            return
        ctx.place(item, name, view=view)
        placed += 1

    if axis == "x":
        _place(
            _global_axis_centerline(
                (FX(a.bb.min.X) - 5, FZ(a.cz), 0),
                (FX(a.bb.max.X) + 5, FZ(a.cz), 0),
            ),
            "centerline_front",
            "front",
        )
        _place(
            _global_axis_centerline(
                (PX(a.bb.min.X) - 5, PY(a.cy), 0),
                (PX(a.bb.max.X) + 5, PY(a.cy), 0),
            ),
            "centerline_plan",
            "plan",
        )
    elif axis == "y":
        _place(
            _global_axis_centerline(
                (SX(a.bb.min.Y) - 5, SZ(a.cz), 0),
                (SX(a.bb.max.Y) + 5, SZ(a.cz), 0),
            ),
            "centerline_side",
            "side",
        )
        _place(
            _global_axis_centerline(
                (PX(a.cx), PY(a.bb.min.Y) - 5, 0),
                (PX(a.cx), PY(a.bb.max.Y) + 5, 0),
            ),
            "centerline_plan",
            "plan",
        )
    else:
        return 0
    return placed


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
        "warning",
        "pmi_dropped",
        f"PMI {label!r} not placed (no room beside the {view})",
        source=getattr(rec, "source_id", ""),
        outcome_stage="placement",
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
    source_id = getattr(rec, "source_id", "")
    # `error` for a source-bearing record, for the same reason as
    # `_record_unsupported_dimension_kind`: this SUPPRESSES the sibling `pmi_not_rendered`
    # error, so leaving it a warning turned a lost AP242 requirement into `passed: True`.
    # Adding the suppression without raising the severity was the very downgrade #1177's
    # own commit message argued must not happen — committed one file away from where it
    # said so.
    ctx.record_issue(
        "error" if source_id else "warning",
        "authored_dim_degenerate",
        f"authored dimension {label!r} has degenerate reference geometry (needs two "
        "distinct reference points spanning a legible distance)",
        source=source_id,
    )


def _record_pmi_no_candidate(ctx, label, rec):
    """Record authored PMI for which the renderer could not form any placement candidate."""
    source_id = getattr(rec, "source_id", "")
    ctx.record_issue(
        "error" if source_id else "warning",
        "pmi_not_rendered",
        f"authored dimension {label!r} produced no viable render candidate",
        source=source_id,
    )


def _bore_span_offsets(pmi_kind: str, value: float) -> tuple[float, float]:
    """Signed offsets from the bore centroid to each witness base point.

    The invariant is that the DRAWN LENGTH equals the LABELLED VALUE, for both kinds:

    * a ``"diameter"`` record stores the full diameter and its dimension spans the bore,
      ``(-value/2, +value/2)`` — length ``value``;
    * a ``"radius"`` record stores the radius and its dimension runs from the centre to
      the surface, ``(0, +value)`` — length ``value``.

    The predecessor returned a single half-span that the caller applied symmetrically, so
    a radius record drew ``centre ± value``: an R6 record produced a **12 mm** line
    labelled R6 (#1208). The diameter branch was correct, and the radius branch beside it
    had the same shape of error #360 fixed one line along — that fix keyed on the drafting
    category rather than the IR ``.kind``, and never questioned what radius should span.

    Keyed on the drafting dimension category, NOT ``.kind``: the #360 bug used the latter,
    so the diameter branch was dead and every diameter dim spanned ±diameter (2× wide).
    """
    if pmi_kind == "diameter":
        return (-value / 2, value / 2)
    return (0.0, value)


# PMI is pre-authored manufacturing intent from the STEP file. When a strip is over
# capacity it should survive ahead of auto-generated dims (priority 0), like declared
# GD&T. It still lives in the outer run so it does not land between size/location dims.
_PMI_SUBCHAIN = 3
_PMI_CORRIDOR_PRIORITY = PRIORITY.AUTHORED
_PMI_SLOT = 10.0  # mm — slot size for PMI dim lines in the strip


#: Dimension categories the IR admits but this renderer cannot draw TRUTHFULLY. `Dimension`
#: measures a straight projected path, so a record whose value is measured on some other
#: basis renders as an annotation whose geometry contradicts its own label — a drawing that
#: asserts something false (#1177). Measured on a 1:1 sheet, value against drawn length:
#:
#:   angular       60      ->  16.0   label states degrees, geometry states millimetres
#:   curve_length  25.133  ->  16.0   arc length against its chord (57% out)
#:   curved_dist   25.133  ->  16.0   same
#:   oriented      20.0    ->  16.0   along a stated direction, not the projected axis (25% out)
#:
#: The criterion is whether the value is measured on a basis THIS RENDERER RECEIVES. It
#: draws the projected span between reference points along the dominant axis, so:
#: `linear`, `thickness` and `diameter` are that span by definition and stay. An arc length
#: is not (`curve_length`, `curved_dist`), nor is an angle (`angular`). `oriented` is a
#: straight span, but along a direction the record states and the renderer is never given —
#: refused for a different reason from its neighbours, which an earlier version of this
#: comment lumped together as "needs an arc".
#:
#: `radius` is NOT refused: its value is a straight span, so it was fixable rather than
#: unsupported, and #1208 fixed it in the same PR — a radius now runs centre-to-surface and
#: draws its labelled length. Refusing it would have filed a rendering bug under
#: "unsupported category" and misdescribed it. An earlier version of this comment still
#: described the bug in the present tense, naming a function the same PR had deleted.
_UNRENDERABLE_DIMENSION_KINDS = frozenset({"angular", "curve_length", "curved_dist", "oriented"})

#: What each refused category actually measures, for the diagnostic. Keyed by kind so the
#: message stays true as the set grows: an earlier version hard-coded "the label states an
#: angle", which would have been wrong the moment `curve_length` was added.
_MEASUREMENT_BASIS = {
    "angular": "an angle in degrees",
    "curve_length": "a length along a curve",
    "curved_dist": "a distance along a curve",
    "oriented": "a distance along a stated direction",
}


def _authored_with_usable_references(record) -> bool:
    """Whether *record* is an authored dimension with enough geometry to draw at all.

    Shared by the renderable and refused filters so they cannot drift: a predicate added to
    one only would make a record silently NEITHER drawn nor reported.
    """
    return record.kind == "authored_dimension" and record.value > 0 and len(record.ref_pts) >= 2


def _record_unsupported_dimension_kind(ctx, rec):
    """Record an authored dimension whose CATEGORY this renderer cannot draw truthfully.

    A validation outcome, not a placement one — the same distinction
    :func:`_record_pmi_unrenderable` draws, and for the same reason as #1190: an optional or
    unsupported outcome marked as a placement drop makes every scale infeasible, so an
    explicit ``scale=`` request burns the whole ladder and raises where it used to return a
    drawing.
    """
    basis = _MEASUREMENT_BASIS[rec.pmi_kind]
    source_id = getattr(rec, "source_id", "")
    # `error` for a source-bearing record, matching `_record_pmi_no_candidate` and the
    # three `lint_pmi_*` checks: in annotate mode a requirement that came from the AP242
    # file and is absent from the drawing is an error, and suppressing the sibling
    # `pmi_not_rendered` must not quietly downgrade it. An authored declaration with no
    # source is the author's own, and a warning.
    ctx.record_issue(
        "error" if source_id else "warning",
        "dimension_kind_unsupported",
        f"authored {rec.pmi_kind} dimension {getattr(rec, 'label', '')!r} is not drawn: it "
        f"states {basis}, and this renderer measures only a straight projected path",
        source=source_id,
        outcome_stage="validation",
    )


def _renderable_pmi_records(records):
    """PMI records the dimension renderer may place.

    Raw ``PmiFeature`` fallbacks can preserve unsupported AP242 records. Do not render those
    just because they happen to carry a numeric value and references; only drafting dimension
    categories belong in this placement path — and only those this renderer can actually
    draw (see :data:`_UNRENDERABLE_DIMENSION_KINDS`).
    """
    return [
        r
        for r in records
        if _authored_with_usable_references(r)
        and r.pmi_kind in AUTHORED_DIMENSION_KINDS
        and r.pmi_kind not in _UNRENDERABLE_DIMENSION_KINDS
    ]


def _unsupported_kind_records(records):
    """Authored records refused purely because of their category, so the omission can be
    reported. Deliberately not folded into :func:`_renderable_pmi_records`: a record with a
    zero value or one reference point is refused for a different reason and already has its
    own diagnostic."""
    return [
        r
        for r in records
        if _authored_with_usable_references(r)
        and r.pmi_kind in AUTHORED_DIMENSION_KINDS
        and r.pmi_kind in _UNRENDERABLE_DIMENSION_KINDS
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

    When the record carries no ``ref_bbox`` (an authored ``Sheet.measured_dimension()`` with
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


def _pmi_place_one(dwg, spec, rec, *, ctx, trace=None):
    # *trace* (#736): a PMI dim's post-drop fallback is a standalone strip pass —
    # traced as a pass_event like the other standalone placers.
    left = place_strip_candidates(
        dwg,
        spec["strip"],
        spec["view"],
        spec["axis"],
        [(spec["name"], spec["build"])],
        _PMI_SLOT,
        ctx=ctx,
        force=True,
        features={spec["name"]: rec},
        priorities={spec["name"]: _PMI_CORRIDOR_PRIORITY},
        trace=trace,
        trace_label="pmi_fallback",
    )
    return not left


def _pmi_queue_options(dwg, ctx, options, ax, label, rec):
    specs = [s for s in options if s is not None]
    if not specs:
        return False
    primary, alternates = specs[0], specs[1:]

    def _drop(nm, _alts=alternates, _ax=ax, _label=label, _rec=rec):
        for alt in _alts:
            if _pmi_place_one(dwg, alt, _rec, ctx=ctx, trace=ctx.trace):
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
        # Bore size: a diameter spans centroid ± value/2; a radius runs centroid → +value
        # (#1208). See `_bore_span_offsets`.
        info = _bore_info(rec)
        if info is None:
            _log.debug("PMI dim[%d] diam: no ref_bbox, skip", idx)
            _record_pmi_no_candidate(ctx, label, rec)
            return False
        bore_axis, cx_f, cy_f, cz_f = info
        # Resolved axis (handles _bore_info's '?' degenerate-bbox fallback); the diameter
        # view table (Z→plan, X→side, Y→front) differs from the linear-dim one (#351 PR-4a).
        ax = bore_axis
        lo, hi = _bore_span_offsets(rec.pmi_kind, rec.value)
        # TWO different quantities, which #1208 briefly conflated:
        #
        # * `half_span_pg` — half the DRAWN span, which is what the legibility gate asks
        #   about ("does the label fit between the witness bases"). A radius dim is `value`
        #   long, not `2 * value`, so the two kinds no longer share it.
        # * `surface_pg` — the distance from the bore centre to its SURFACE, which is `hi`
        #   for BOTH kinds and is where the leader's arrow must point.
        #
        # Redefining the single old `half` for the gate silently moved the arrow with it, so
        # a radius leader pointed halfway between the centre and the arc — into the bore
        # void — on a case that was previously right. And halving the gate value doubled the
        # traffic onto that path. One variable, two consumers three lines apart, and the
        # comment on the intervening line still asserting the old meaning.
        half_span_pg = ((hi - lo) / 2) * a.SCALE
        surface_pg = hi * a.SCALE  # centre-to-surface on the page (mm), both kinds
        # Narrow bores (page span < text width) lead out to a shelf; bracket dims only
        # when the span fits the label. An unresolved axis matches no cfg → bottom drop.
        cfg = bore_cfg.get(bore_axis)
        if cfg is not None:
            u, v = cfg["centre"](cx_f, cy_f, cz_f)
            if half_span_pg >= _MIN_INPLACE_BORE_HALF_MM:
                p1, p2 = cfg["span"](cx_f, cy_f, cz_f, lo, hi)
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
                            (u, v + (surface_pg if s == "above" else -surface_pg), 0),
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
    # No candidate reached the shared solve. Source reconciliation reports this as
    # ``pmi_not_rendered``; ``pmi_dropped`` is reserved for a queued candidate rejected by
    # placement capacity, via ``_pmi_queue_options``'s on-drop callback (#623).
    _log.info("PMI dim[%d] %s %.3g → no viable render candidate", idx, ax, rec.value)
    _record_pmi_no_candidate(ctx, label, rec)
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
    for refused in _unsupported_kind_records(pmi):
        _record_unsupported_dimension_kind(ctx, refused)
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
    # prefers below first). `centre`/`span` project the circle centre and its two span
    # endpoints — symmetric for a diameter, centre-to-surface for a radius (#1208).
    _bore: dict[str, dict[str, Any]] = {
        "Z": {
            "view": "plan",
            "zones": {"above": a.pv_zones.above, "below": a.pv_zones.below},
            "order": ("above", "below"),
            "leader_order": ("above", "below"),
            "centre": lambda cx, cy, cz: (PX(cx), PY(cy)),
            "span": lambda cx, cy, cz, lo, hi: (
                (PX(cx + lo), PY(cy), 0),
                (PX(cx + hi), PY(cy), 0),
            ),
        },
        "X": {
            "view": "side",
            "zones": {"above": a.sv_zones.above, "below": a.sv_zones.below},
            "order": ("above", "below"),
            "leader_order": ("above", "below"),
            "centre": lambda cx, cy, cz: (SX(cy), SZ(cz)),
            "span": lambda cx, cy, cz, lo, hi: (
                (SX(cy + lo), SZ(cz), 0),
                (SX(cy + hi), SZ(cz), 0),
            ),
        },
        "Y": {
            "view": "front",
            "zones": {"above": a.fv_zones.above, "below": a.fv_zones.below},
            "order": ("above", "below"),
            "leader_order": ("below", "above"),
            "centre": lambda cx, cy, cz: (FX(cx), FZ(cz)),
            "span": lambda cx, cy, cz, lo, hi: (
                (FX(cx + lo), FZ(cz), 0),
                (FX(cx + hi), FZ(cz), 0),
            ),
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
_GDT_CORRIDOR_PRIORITY = PRIORITY.AUTHORED
# Minimum GD&T leader shaft length (page-mm). A zero-length Leader (site == solved tier)
# makes OCC's edge builder raise; nudging to this keeps `_build` total (#61 review).
_MIN_LEADER = 0.05


def _pmi_source_ids(item) -> tuple[str, ...]:
    plural = tuple(getattr(item, "source_ids", ()))
    singular = getattr(item, "source_id", "")
    return tuple(dict.fromkeys(((singular,) if singular else ()) + plural))


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
    items = [
        f
        for f in model.features
        if f.kind in _GDT_KINDS
        # Automatic AP242 discovery keeps typed IR available in report mode, but report must
        # not draw it. Explicit/round-tripped models remain declarations and therefore render.
        and (not _pmi_source_ids(f) or a.pmi_mode == "annotate" or ctx.model_declared)
    ]
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
        source_ids = _pmi_source_ids(item)
        vk = views.get(item.view)
        if vk is None or item.side not in ("above", "below", "left", "right"):
            ctx.record_issue(
                "warning",
                "pmi_dropped" if source_ids else "gdt_dropped",
                f"{name}: bad target {item.view!r}/{item.side!r}",
                source=source_ids,
                outcome_stage="validation",
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
                "warning",
                "pmi_dropped" if source_ids else "gdt_dropped",
                f"{name}: cannot render ({type(e).__name__}: {e})",
                source=source_ids,
                outcome_stage="validation",
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
            return Leader(
                tip=tip,
                elbow=elbow,
                label="",
                draft=draft,
                callout=g,
                all_around=getattr(_it, "all_around", False),
                all_over=getattr(_it, "all_over", False),
            )

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
            _feat=item.origin or item,
            _tb=tb_box,
            _source=source_ids,
        ):
            # Fallthrough (#481): the declared/derived side is full — try the OPPOSITE side of
            # the same view before dropping, so a congested default still places somewhere
            # legible rather than vanishing. DEFERRED via ctx.post_drain (#636, the plate
            # pattern): the carve then runs after EVERY corridor has drained, so it cannot
            # preempt a corner a later sibling's force candidate needs. Force semantics
            # (no corridor-cross check) match the primary path, BUT reject a spot over the
            # (not-yet-placed) title block — a below/right strip runs into it, and the
            # carve can't see it (#481 review).
            def _retry(
                nm=nm,
                _v=_v,
                _s=_s,
                _zones=_zones,
                _px=_px,
                _py=_py,
                _sz=_sz,
                _bld=_bld,
                _feat=_feat,
                _tb=_tb,
            ):
                # Auto-relax the requested side (#841 outcome C): the requested strip is full, so
                # try the OPPOSITE side, then the two PERPENDICULAR sides, placing on the first
                # with room. A note the caller asked to see should appear somewhere legible
                # rather than vanish; when the requested strip has no room, an explicit `side=`
                # is a preference, not a hard constraint. A perpendicular side flips the leader
                # orientation (`_bld(pos, _hz=hz)`). If the placement lands on a side other than
                # requested, record an INFO issue so the relaxation is visible, not silent — the
                # #841 goal is that a requested annotation is never *silently* lost.
                relax_order = {
                    "above": ("below", "right", "left"),
                    "below": ("above", "right", "left"),
                    "left": ("right", "above", "below"),
                    "right": ("left", "above", "below"),
                }[_s]
                for alt in relax_order:
                    alt_strip = getattr(_zones, alt, None)
                    if alt_strip is None:
                        continue
                    hz = alt in ("above", "below")  # perpendicular sides flip the leader axis
                    axis2 = "y" if hz else "x"
                    extent = _sz[1] if axis2 == "y" else _sz[0]  # the glyph's stacking-axis size
                    perp = (_px, _px + _sz[0]) if hz else (_py - _sz[1] / 2, _py + _sz[1] / 2)
                    pos = carve_free_position(dwg, alt_strip, _v, axis2, max(tier, extent), perp)
                    if pos is None:
                        continue
                    dim = _bld(pos, _hz=hz)
                    if _box_hits(_anno_box(dim), (_tb,)):  # would overlap the title block — skip
                        continue
                    ctx.place(dim, nm, view=_v, feature=_feat)  # relaxed side
                    ctx.record_issue(
                        "info",
                        "gdt_side_relaxed",
                        f"{nm}: the {_v} {_s} strip was full — placed on {alt} instead",
                    )
                    return
                ctx.record_issue(
                    "warning",
                    "pmi_dropped" if _source else "gdt_dropped",
                    f"{nm} not placed (no room in any {_v} strip)",
                    source=_source,
                    outcome_stage="placement",
                )

            ctx.post_drain.append(_retry)

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
                # Declared frames belong to their decorated feature. An imported frame has
                # external source provenance but no separately-owned geometric IR feature.
                feature=item.origin or item,
                size=size,
                # Even a force-kept frame must not stack into the title block (#481 review) —
                # place_strip_candidates rejects a placement hitting this box, then on_drop's
                # fallthrough tries the other side.
                forbid=tb_box,
            ),
        )
        n += 1
    return n
