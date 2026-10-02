"""Compiler-approved off-axis hole locations through the shared corridor solve."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, NamedTuple

from draftwright._core import (
    _CONCENTRIC_TOL_MM,
    _END_ON,
    _WITNESS_LIFT_MM,
    Analysis,
    _dim,
    layout_frame,
)
from draftwright._core import _legible_locations as _legible_locations
from draftwright.annotations._common import (
    CorridorCandidate,
    _box_hits,
    _geom_box,
    _hole_location_coverage_fact,
    _with_hole_location_coverage,
    corridor_blockers,
    place_strip_candidates,
    register_corridor,
    strip_free_span,
)
from draftwright.model.compiled import resolve_feature, shared_location_text
from draftwright.model.ir import HoleFeature, PatternFeature


class _OffHole(NamedTuple):
    """One side-drilled hole occurrence for the off-axis location pass.

    *approved* maps the MEASURED axis (``"x"``/``"y"``/``"z"``) to the compiled entry for
    that dimension, and is the pass's whole content contract: a member with no entry for an
    axis has no dimension on it, and the value printed is the entry's, not one the renderer
    recomputes from `a.bb`. Two formulas for one number is the drift #923 removed
    everywhere else.
    """

    axis: str
    location: tuple
    feature: HoleFeature | PatternFeature
    approved: dict
    view: str


_Point = tuple[float, float, float]
_HeightRoute = tuple[Any, str, _Point, _Point, float]
_LocationCandidate = tuple[str, Callable[[float], Any]]


@dataclass(frozen=True)
class _HeightFallback:
    """One hole height's saved routes and evidence for a post-solve retry."""

    drawing: Any
    context: Any
    tier: float
    primary: _HeightRoute
    alternates: tuple[_HeightRoute, ...]
    candidate: _LocationCandidate
    feature_map: Callable[[str], dict[str, Any]]
    measurement_map: Callable[[str], dict[str, tuple[Any, ...]]]
    candidate_factory: Callable[[str, _Point, _Point, float], _LocationCandidate]
    height: float
    measurements_by_height: dict[float, list[Any]]

    def __call__(self, _name: str) -> None:
        for alt_strip, alt_view, alt_p_lo, alt_p_hi, alt_edge in self.alternates:
            alt = self.candidate_factory(alt_view, alt_p_lo, alt_p_hi, alt_edge)
            if not _off_axis_emit(
                self.drawing,
                self.tier,
                alt_strip,
                alt_view,
                "x",
                [alt],
                features=self.feature_map(alt_view),
                measurements=self.measurement_map(alt_view),
                ctx=self.context,
                trace=self.context.trace,
            ):
                return
        p_strip, p_view, _p_lo, _p_hi, _edge = self.primary
        if _off_axis_emit(
            self.drawing,
            self.tier,
            p_strip,
            p_view,
            "x",
            [self.candidate],
            force=True,
            features=self.feature_map(p_view),
            measurements=self.measurement_map(p_view),
            ctx=self.context,
            trace=self.context.trace,
        ):
            _off_axis_drop(
                "Z",
                p_view,
                ctx=self.context,
                measurement=tuple(self.measurements_by_height[self.height]),
            )


@dataclass(frozen=True)
class _PlanAlternateBuild:
    """The projected Y witness and approved coverage for one plan retry."""

    p_lo: tuple[float, float, float]
    p_hi: tuple[float, float, float]
    edge: float
    label: str
    source_name: str
    draft: Any
    coverage_by_name: dict[str, list[Any]]

    def __call__(self, pos: float) -> Any:
        return _with_hole_location_coverage(
            _dim(self.p_lo, self.p_hi, "right", pos - self.edge, self.draft, label=self.label),
            self.coverage_by_name[self.source_name],
        )


def _approved_off_axis_holes(plan) -> list[_OffHole]:
    """Every side-drilled hole member the compiler approved a position for.

    Replaces `_ir_off_axis_holes`, which walked `model.features` directly. That was the
    last dimensional path outside the boundary: `location_role` said a hole is locatable,
    `plan_locations` said only a Z-normal one is, and this pass drew the X/Y ones from raw
    IR regardless — so an authored set naming only a side-drilled bore's ⌀ still produced
    its offset and height dims (#925 review).

    A filter over approved entries, deliberately, rather than an authored check added here:
    a renderer-side check is the suppression convention this work exists to replace.

    Keyed by ``(ref, member)`` — feature identity AND occurrence, not the coordinate alone.
    A cross-drilled pair (one X-axis bore and one Y-axis bore through the same centre) is
    two features at one point, so keying on the point merged their four entries into a
    single `_OffHole` whose `axis` was whichever feature came first: the other's planar
    dimension sat unused in the merged dict, and the result flipped when the model's feature
    order did (#925 review). The coordinate is geometry; it does not identify a feature.

    The intentional GEOMETRIC dedup still happens, downstream and per axis, in the
    `seen_x`/`seen_y`/`seen_z` sets — which is why the pair keeps both planar dims and
    still shares one height dim. Merging here conflated the two jobs.
    """
    holes: dict[tuple, _OffHole] = {}
    for entry in plan.locations:
        # Derived, not spelled: the role is a CONTRACT between the compiler that mints it
        # and this filter that reads it. A literal here is a second owner — renaming the
        # declaration would make every side-hole position dim vanish (#966).
        if entry.axis == "z" or entry.role not in {
            HoleFeature.LOCATION_OFF_AXIS_STEM,
            PatternFeature.LOCATION_STEM,
        }:
            continue
        assert entry.span is not None  # off-axis entries always carry datum → member
        member = entry.span[1]
        key = (entry.ref, member)
        hole = holes.get(key)
        if hole is None:
            group = plan.group_for(entry.ref)
            view = group.view if group is not None else _END_ON[entry.axis]
            hole = holes[key] = _OffHole(entry.axis, member, resolve_feature(entry.ref), {}, view)
        hole.approved[entry.discriminator] = entry
    return list(holes.values())


def _off_axis_drop(
    axis, view, *, ctx, measurement=(), reason="no room beside the view", short_span=False
):
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
    # drawing, so a drop there stays a warning.) Promoted (#638).
    ctx.record_issue(
        "info",
        "off_axis_location_dropped",
        f"{axis} location dim for a {view}-view hole not placed ({reason})",
        measurement=measurement,
        # This gate is independent of layout. The approved span and model are reused
        # across explicit-scale retries, and a smaller scale cannot restore 1 mm.
        evidence_reason="off_axis_span_below_1_mm" if short_span else None,
    )


def _off_axis_emit(
    dwg,
    tier,
    strip,
    view,
    axis,
    cands,
    force=False,
    features=None,
    measurements=None,
    trace=None,
    *,
    ctx,
):
    # The collect-then-solve strip placer lives in _common as the shared
    # place_strip_candidates (P3, retiring the Strip cursor #150); this thin wrapper
    # binds the pass's dwg + tier. *features* (name -> IR feature) attributes each dim
    # for drop() (#408). Promoted (#638). *trace* (#736): a standalone strip pass —
    # traced as a pass_event like the other standalone placers.
    return place_strip_candidates(
        dwg,
        strip,
        view,
        axis,
        cands,
        tier,
        ctx=ctx,
        force=force,
        features=features,
        measurements=measurements,
        trace=trace,
        trace_label="off_axis_locations",
    )


def _off_axis_queue(
    ctx,
    tier,
    strip,
    view,
    side,
    axis,
    cands,
    *,
    features=None,
    measurements=None,
    force=True,
    on_drop=None,
    order_key=None,
    dedup=None,
):
    # Below/right side-hole locations feed the same corridor batch as envelope, GD&T,
    # and PMI (#477). Their historical policy was force-keep unless the strip is
    # physically full; callers with a relocation path provide an on_drop fallback.
    # Promoted (#638).
    if strip is None:
        for name, _build in cands:
            (on_drop or (lambda _nm: None))(name)
        return
    for i, (name, build) in enumerate(cands):
        register_corridor(
            ctx,
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
                dedup=(dedup or {}).get(name),
                force=force,
                feature=(features or {}).get(name),
                measurement=(measurements or {}).get(name),
            ),
        )


def _off_axis_owner(holes):
    # The IR hole feature owning a side-drilled location dim, or None when the dim's
    # offset is shared by >1 distinct feature (unowned, so drop can't over-strip a
    # sibling — mirrors the #398c shared-coordinate rule). Promoted (#638).
    feats = {hole.feature for hole in holes}
    return next(iter(feats)) if len(feats) == 1 else None


def _locate_across(dwg, ctx, a: Analysis, off):
    """The "across" phase (#133): an X-axis hole's Y position below the SIDE view, queued
    with the envelope so the overall depth dim stacks outside it (ISO order). Confined to the
    side view: a Y-axis hole's X position contends the FRONT-below strip with the
    turned-diameter ø-row, so it stays in the "along" phase. Promoted out of
    _locate_off_axis_holes (#638)."""
    draft = dwg.draft
    SX, SZ = a.proj.side_x, a.proj.side_z
    PX, PY = a.proj.plan_x, a.proj.plan_y
    dy, dz = a.bb.min.Y, a.bb.min.Z
    tier = draft.font_size + 2 * draft.pad_around_text
    yw = SZ(dz) - _WITNESS_LIFT_MM
    seen_y: set = set()
    cands = []
    order_y: dict = {}
    loc_by_name: dict = {}  # dim name -> contributing hole locations (for provenance)
    mids_by_name: dict = {}
    coverage_by_name: dict = {}
    plan_alternates: dict = {}
    for h in (h for h in off if h.axis == "x"):
        # The VALUE is the approved entry's; `dy` survives only as the witness anchor.
        entry = h.approved.get("y")
        if entry is None:
            continue  # not approved — the compiler withheld this position
        yo = round(entry.value, 2)
        if yo * a.SCALE < 1.0:
            if abs(entry.value) > 1e-9:
                _off_axis_drop(
                    "Y",
                    "side",
                    ctx=ctx,
                    measurement=entry.id,
                    reason="span shorter than 1 mm at this scale",
                    short_span=True,
                )
            continue
        name = f"dim_loc_side_y{round(yo * 100)}"
        loc_by_name.setdefault(name, []).append(h)
        label = shared_location_text(hole.approved["y"] for hole in loc_by_name[name])
        mids_by_name.setdefault(name, []).append(entry.id)
        coverage_by_name.setdefault(name, []).append(_hole_location_coverage_fact(entry))
        order_y[name] = yo
        if yo not in seen_y:
            seen_y.add(yo)
            p_lo, p_hi = (SX(dy), yw, 0), (SX(h.location[1]), yw, 0)
            cands.append(
                (
                    name,
                    # The label is the approved entry's text; `yo` survives only as the
                    # name key and the spacing order.
                    lambda pos, pl=p_lo, ph=p_hi, lb=label, nm=name: _with_hole_location_coverage(
                        _dim(pl, ph, "below", yw - pos, draft, label=lb),
                        coverage_by_name[nm],
                    ),
                )
            )
            # #1155: the same Y ordinate reads vertically in plan.  Keep the
            # side-view location as the natural first choice, but retain this
            # requirement-driven alternate for a full below-side corridor (GRM-04
            # at 5:1 shares that short strip with the overall depth).  This is a
            # view reassignment through the shared strip solver, not a raw position.
            plan_edge = PX(a.bb.max.X)
            plan_lo = (plan_edge, PY(dy), 0)
            plan_hi = (plan_edge, PY(h.location[1]), 0)
            alt_name = f"dim_loc_plan_y{round(yo * 100)}"
            plan_alternates[name] = (
                alt_name,
                _PlanAlternateBuild(
                    plan_lo, plan_hi, plan_edge, label, name, draft, coverage_by_name
                ),
            )
    feats = {nm: _off_axis_owner(holes) for nm, holes in loc_by_name.items()}
    measurements = {name: tuple(ids) for name, ids in mids_by_name.items()}

    def _fallback(name):
        # Every queued side candidate is created in the same block as its plan
        # alternate above; a missing key is an internal invariant violation.
        alt = plan_alternates[name]
        alt_name = alt[0]

        def _retry():
            # A fallback is never allowed to create an annotation in a view the
            # resolved plan omitted.  It also runs only after every shared corridor
            # has drained, so its solitary carve cannot preempt a later candidate.
            if "plan" not in dwg.views:
                _off_axis_drop(
                    "y",
                    "side",
                    ctx=ctx,
                    measurement=measurements.get(name, ()),
                )
                return
            if not _off_axis_emit(
                dwg,
                tier,
                a.pv_zones.right,
                "plan",
                "x",
                [alt],
                force=False,
                features={alt_name: feats.get(name)},
                measurements={alt_name: measurements.get(name, ())},
                ctx=ctx,
                trace=ctx.trace,
            ):
                return
            _off_axis_drop(
                "y",
                "side",
                ctx=ctx,
                measurement=measurements.get(name, ()),
            )

        ctx.post_drain.append(_retry)

    _off_axis_queue(
        ctx,
        tier,
        a.sv_zones.below,
        "side",
        "below",
        "y",
        cands,
        features=feats,
        measurements=measurements,
        on_drop=_fallback,
        order_key=lambda nm, _i: order_y.get(nm, _i),
    )


def _locate_along_planar(dwg, ctx, a: Analysis, off, *, view="front"):
    """The "along" phase's planar dim for a Y-axis hole.

    Rear callouts occupy the below strip; their vertical shafts and labels share the
    X-location witness corridor. Route rear X locations above the same view so both
    authored measurements and callout ink remain legible.
    """
    draft = dwg.draft
    FX, FZ = (a.proj.rear_x, a.proj.rear_z) if view == "rear" else (a.proj.front_x, a.proj.front_z)
    dx, dz = a.bb.min.X, a.bb.min.Z
    tier = draft.font_size + 2 * draft.pad_around_text
    above = view == "rear"
    xw = FZ(a.bb.max.Z) + _WITNESS_LIFT_MM if above else FZ(dz) - _WITNESS_LIFT_MM
    side = "above" if above else "below"
    seen_x: set = set()
    x_cands = []
    order_x: dict = {}
    x_loc_by_name: dict = {}
    x_mids_by_name: dict = {}
    x_coverage_by_name: dict = {}
    for h in (h for h in off if h.axis == "y"):
        entry = h.approved.get("x")
        if entry is None:
            continue  # not approved
        xo = round(entry.value, 2)
        if xo * a.SCALE < 1.0:
            if abs(entry.value) > 1e-9:
                _off_axis_drop(
                    "X",
                    view,
                    ctx=ctx,
                    measurement=entry.id,
                    reason="span shorter than 1 mm at this scale",
                    short_span=True,
                )
            continue
        name = f"dim_loc_{view}_x{round(xo * 100)}"
        x_loc_by_name.setdefault(name, []).append(h)
        label = shared_location_text(hole.approved["x"] for hole in x_loc_by_name[name])
        x_mids_by_name.setdefault(name, []).append(entry.id)
        x_coverage_by_name.setdefault(name, []).append(_hole_location_coverage_fact(entry))
        order_x[name] = xo
        if xo not in seen_x:
            seen_x.add(xo)
            p_lo, p_hi = (FX(dx), xw, 0), (FX(h.location[0]), xw, 0)
            x_cands.append(
                (
                    name,
                    lambda pos, pl=p_lo, ph=p_hi, lb=label, nm=name: _with_hole_location_coverage(
                        _dim(pl, ph, side, pos - xw if above else xw - pos, draft, label=lb),
                        x_coverage_by_name[nm],
                    ),
                )
            )
    x_feats = {nm: _off_axis_owner(holes) for nm, holes in x_loc_by_name.items()}
    x_measurements = {name: tuple(ids) for name, ids in x_mids_by_name.items()}
    _off_axis_queue(
        ctx,
        tier,
        layout_frame(a).zones(view).above if above else layout_frame(a).zones(view).below,
        view,
        side,
        "y",
        x_cands,
        features=x_feats,
        measurements=x_measurements,
        on_drop=lambda nm: _off_axis_drop(
            "x", view, ctx=ctx, measurement=x_measurements.get(nm, ())
        ),
        order_key=lambda nm, _i: order_x.get(nm, _i),
    )


def _locate_along_z(dwg, ctx, a: Analysis, off, *, front_view="front"):
    """The "along" phase's height dim: a hole's height (Z) is visible to the RIGHT of both the
    side and the front view. Neither right strip is universally free, so try the natural strip
    first, then RELOCATE to the other view if a bore-callout leader sits in the natural
    corridor; if neither takes it cleanly, KEEP it on the natural view (force) accepting the
    same-feature crossing — never drop a real dim (policy B, #133). Promoted (#638)."""
    draft = dwg.draft
    SX, SZ = a.proj.side_x, a.proj.side_z
    FX, FZ = (
        (a.proj.rear_x, a.proj.rear_z)
        if front_view == "rear"
        else (a.proj.front_x, a.proj.front_z)
    )
    dz = a.bb.min.Z
    tier = draft.font_size + 2 * draft.pad_around_text
    zr = SX(a.bb.max.Y)
    zrf = FX(a.bb.min.X if front_view == "rear" else a.bb.max.X)
    z_locs: dict = {}  # z-offset -> contributing hole locations (for provenance)
    z_mids: dict = {}
    z_coverage: dict = {}
    for h in off:
        entry = h.approved.get("z")
        if entry is not None and round(entry.value, 2) * a.SCALE >= 1.0:
            z_locs.setdefault(round(entry.value, 2), []).append(h)
            z_mids.setdefault(round(entry.value, 2), []).append(entry.id)
            z_coverage.setdefault(round(entry.value, 2), []).append(
                _hole_location_coverage_fact(entry)
            )
    seen_z = set()
    for h in off:
        entry = h.approved.get("z")
        if entry is None:
            continue  # not approved
        zo = round(entry.value, 2)
        if zo * a.SCALE < 1.0:
            if abs(entry.value) > 1e-9:
                _off_axis_drop(
                    "Z",
                    front_view if h.axis == "y" else "side",
                    ctx=ctx,
                    measurement=entry.id,
                    reason="span shorter than 1 mm at this scale",
                    short_span=True,
                )
            continue
        if zo in seen_z:
            continue
        seen_z.add(zo)
        hz = h.location[2]
        owner = _off_axis_owner(z_locs[zo])
        label = shared_location_text(hole.approved["z"] for hole in z_locs[zo])

        def _zc(view, p_lo, p_hi, edge, _zo=zo, _lbl=label):
            return (
                f"dim_loc_{view}_z{round(_zo * 100)}",
                lambda pos, pl=p_lo, ph=p_hi, e=edge: _with_hole_location_coverage(
                    _dim(pl, ph, "right", pos - e, draft, label=_lbl),
                    z_coverage[_zo],
                ),
            )

        def _zf(view, _zo=zo, _owner=owner):  # provenance map for the z dim in this view
            return {f"dim_loc_{view}_z{round(_zo * 100)}": _owner}

        def _zm(view, _zo=zo):
            return {f"dim_loc_{view}_z{round(_zo * 100)}": tuple(z_mids[_zo])}

        side_cand = (a.sv_zones.right, "side", (zr, SZ(dz), 0), (zr, SZ(hz), 0), zr)
        front_cand = (
            layout_frame(a).zones(front_view).right,
            front_view,
            (zrf, FZ(dz), 0),
            (zrf, FZ(hz), 0),
            zrf,
        )
        order = [side_cand, front_cand] if h.axis == "x" else [front_cand, side_cand]
        if a.planned_views is not None:
            order = [candidate for candidate in order if candidate[1] in a.planned_views]
        primary, *alternates = order
        strip, view, p_lo, p_hi, edge = primary
        primary_cand = _zc(view, p_lo, p_hi, edge)

        # Choose the alternate BEFORE the shared corridor solve when the natural
        # witness corridor is already known to cross a placed leader. A post-drain
        # relocation cannot join the alternate view's existing ladder, so it used to
        # land outside the overall height and cross its witness line. The inner-tier
        # footprint is decisive: moving a right-side dimension farther out only grows
        # the corridor back to the view and therefore cannot clear an existing blocker.
        if strip is not None and alternates:
            _lo, _hi, inner = strip_free_span(strip)
            blocked = _box_hits(_geom_box(primary_cand[1](inner)), corridor_blockers(dwg, view))
            if blocked:
                alt_strip, alt_view, alt_p_lo, alt_p_hi, alt_edge = alternates[0]
                alt_cand = _zc(alt_view, alt_p_lo, alt_p_hi, alt_edge)
                alt_blocked = alt_strip is None
                if alt_strip is not None:
                    _alo, _ahi, alt_inner = strip_free_span(alt_strip)
                    alt_blocked = _box_hits(
                        _geom_box(alt_cand[1](alt_inner)),
                        corridor_blockers(dwg, alt_view),
                    )
                    # Do not pre-route into a corridor that cannot hold its existing
                    # shared ladder plus this candidate. In that case the historical
                    # alternate/force path remains the honest policy-B fallback.
                    existing = len(
                        ctx.corridor_batch.get((alt_view, "right"), {}).get("cands", ())
                    )
                    pad = tier + alt_strip.spacing
                    usable = max(0.0, _ahi - _alo - tier)
                    capacity = int(usable / pad) + 1
                    alt_blocked = alt_blocked or capacity < existing + 1
                if not alt_blocked:
                    original = primary
                    primary = alternates[0]
                    alternates = [original, *alternates[1:]]
                    strip, view, p_lo, p_hi, edge = primary
                    primary_cand = alt_cand

        _off_axis_queue(
            ctx,
            tier,
            strip,
            view,
            "right",
            "x",
            [primary_cand],
            features=_zf(view),
            measurements=_zm(view),
            force=False,
            # Bind this loop member's factory now: two dropped heights must not
            # retry the final height twice and lose the first measurement.
            on_drop=_HeightFallback(
                dwg,
                ctx,
                tier,
                primary,
                tuple(alternates),
                primary_cand,
                _zf,
                _zm,
                _zc,
                zo,
                z_mids,
            ),
            order_key=lambda _nm, _i, _zo=zo: _zo,
            dedup={primary_cand[0]: (view, round(p_lo[1], 1), round(p_hi[1], 1), label)},
        )


def _locate_off_axis_holes(dwg, ctx, a: Analysis, *, which, plan):
    """Location dimensions for side-drilled holes (#133).

    An X-axis hole is a circle in the SIDE view (locate its Y below the view and
    its Z to the right — the side view has no left strip); a Y-axis hole is a
    circle in the FRONT view (locate its X below and its Z to the right). Each
    view's strip is carved around the annotations already placed on it and the dims
    are spaced within the free segments by one ``plan_strip`` solve (ADR 2 (was 0009) / #321
    P1b — the collect-then-solve seam replacing the old ``allocate`` + ``_box_hits``
    tier-retry). A dim that finds no room is dropped and recorded as
    ``off_axis_location_dropped`` — never force-stacked. Holes already covered by a
    pattern callout are skipped.

    Run in two phases (``which`` is ``"across"`` or ``"along"``) so each dim stacks in
    the ISO order — overall dim OUTERMOST, feature/location dims nearer the view. The
    ``across`` phase is `_locate_across`; the ``along`` phase is `_locate_along_planar`
    (a Y-hole's X) then `_locate_along_z` (every hole's height), split out at #638.
    """

    def _coaxial(h):
        # The turning-axis bore of a rotational part is located by its centreline, not
        # a position dim (#309) — mirrors render_locations' concentric filter (the
        # Z-turned/plan case) and coverage.py's coaxial exemption, for the X/Y-turned
        # case whose dims come through THIS path. Suppresses the redundant offset+height
        # dims; coverage already credits the bore via its centre mark, so lint stays
        # clean. Non-rotational parts and genuine off-centre side-drilled holes keep
        # their dims (the a.od_axis + perpendicular-centre gates).
        # A globally non-rotational flange can still carry a detected coaxial
        # turned stack (for example a round stepped centre with mounting lugs).
        # In that case ``a.prof`` is the stronger local relationship: the bore
        # lies on the profile axis even though the outer silhouette correctly
        # prevents ``a.is_rotational``. Treat either classification as a valid
        # turning axis so the centreline, not redundant half-envelope offsets,
        # locates the bore (#881).
        if isinstance(h.feature, PatternFeature):
            return False
        turning_axis = a.od_axis if a.is_rotational else getattr(a.prof, "axis", None)
        if turning_axis is None or h.axis != turning_axis:
            return False
        centre = (a.cx, a.cy, a.cz)
        return all(
            abs(h.location[i] - centre[i]) <= _CONCENTRIC_TOL_MM
            for i, ax in enumerate("xyz")
            if ax != turning_axis
        )

    # Off-axis holes sourced from the COMPILED PLAN — the turning-axis concentric bore
    # excluded (located by its centreline, not a position dim). That exclusion stays here
    # because it only ever REMOVES an approved entry, which is a drop, not a leak.
    off = [h for h in _approved_off_axis_holes(plan) if not _coaxial(h)]
    if not off:
        return
    if which == "across":
        _locate_across(dwg, ctx, a, off)
        return
    for view in ("front", "rear"):
        selected = [h for h in off if (h.view == "rear") == (view == "rear")]
        if selected:
            _locate_along_planar(dwg, ctx, a, selected, view=view)
            _locate_along_z(dwg, ctx, a, selected, front_view=view)
