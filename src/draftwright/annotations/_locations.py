"""Compiler-approved hole and seat-axis location corridor producers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from build123d_drafting.helpers import CenterMark

from draftwright._core import _concentric_with_axis, _first_free_index, _legible_locations
from draftwright.annotation_layout_profile import layout_flag
from draftwright.annotations._common import (
    _LOC_SUBCHAIN,
    PRIORITY,
    CorridorCandidate,
    Escalation,
    _hole_location_coverage_fact,
    _same_location_ordinate,
    _with_hole_location_coverage,
    dim_footprint,
    dimension_candidate_geometry,
    short_dimension_label_offset,
)
from draftwright.model.compiled import (
    ApprovedDimension,
    FeatureRef,
    resolve_feature,
    shared_location_text,
)
from draftwright.model.ir import CircularChannelFeature, HoleFeature, SlotFeature
from draftwright.model.ir_foundation import Point


@dataclass(frozen=True, slots=True)
class _CircularChannelLocationGeometry:
    dwg: Any
    dim_builder: Callable[..., Any]
    p1: tuple[float, float, float]
    p2: tuple[float, float, float]
    side: str
    edge: float
    label: str

    def build(self, pos: float) -> Any:
        return self.dim_builder(
            self.p1, self.p2, self.side, abs(pos - self.edge), self.dwg.draft, label=self.label
        )

    def footprint(self, pos: float) -> Any:
        return dim_footprint(
            self.p1, self.p2, self.side, abs(pos - self.edge), self.dwg.draft, self.label
        )


@dataclass(frozen=True, slots=True)
class _XLocationGeometry:
    """Projected X witnesses and the exterior/interior label choices for one rung."""

    project_x: Callable[[float], float]
    project_y: Callable[[float], float]
    datum_x: float
    rx: float
    ry: float
    side: str
    label: str
    label_offset: float
    draft: Any
    dim_builder: Callable[..., Any]

    def anchors(self) -> tuple[Point, Point]:
        return (
            (self.project_x(self.datum_x), self.project_y(self.ry), 0),
            (self.project_x(self.rx), self.project_y(self.ry), 0),
        )

    def exterior_distance(self, pos: float) -> float:
        y = self.project_y(self.ry)
        return y - pos if self.side == "below" else pos - y

    def build(self, pos: float) -> Any:
        pa, pb = self.anchors()
        return self.dim_builder(
            pa,
            pb,
            self.side,
            self.exterior_distance(pos),
            self.draft,
            label=self.label,
            label_offset_x=self.label_offset,
        )

    def footprint(self, pos: float) -> Any:
        pa, pb = self.anchors()
        return dim_footprint(
            pa,
            pb,
            self.side,
            self.exterior_distance(pos),
            self.draft,
            self.label,
            label_offset_x=self.label_offset,
        )

    def interior_build(self, pos: float) -> Any:
        pa, pb = self.anchors()
        return self.dim_builder(
            pa,
            pb,
            "below",
            abs(pos - self.project_y(self.ry)),
            self.draft,
            label=self.label,
            label_offset_x=self.label_offset,
        )

    def interior_geometry(self, pos: float) -> Any:
        pa, pb = self.anchors()
        return dimension_candidate_geometry(
            pa,
            pb,
            "below",
            abs(pos - self.project_y(self.ry)),
            self.draft,
            self.label,
            label_offset_x=self.label_offset,
        )


@dataclass(frozen=True, slots=True)
class _YLocationGeometry:
    """Fixed Y witnesses and opposite-side interior candidate for one rung."""

    pa: Point
    pb: Point
    side: str
    interior_side: str
    edge: float
    label: str
    label_offset: float
    draft: Any
    dim_builder: Callable[..., Any]

    def build(self, pos: float) -> Any:
        return self.dim_builder(
            self.pa,
            self.pb,
            self.side,
            abs(pos - self.edge),
            self.draft,
            label=self.label,
            label_offset_x=self.label_offset,
        )

    def footprint(self, pos: float) -> Any:
        return dim_footprint(
            self.pa,
            self.pb,
            self.side,
            abs(pos - self.edge),
            self.draft,
            self.label,
            label_offset_x=self.label_offset,
        )

    def interior_build(self, pos: float) -> Any:
        return self.dim_builder(
            self.pa,
            self.pb,
            self.interior_side,
            abs(pos - self.edge),
            self.draft,
            label=self.label,
            label_offset_x=self.label_offset,
        )

    def interior_geometry(self, pos: float) -> Any:
        return dimension_candidate_geometry(
            self.pa,
            self.pb,
            self.interior_side,
            abs(pos - self.edge),
            self.draft,
            self.label,
            label_offset_x=self.label_offset,
        )


def _location_candidate(
    dwg,
    ctx,
    name,
    *,
    view,
    span_key,
    label,
    distance,
    build,
    feature=None,
    measurement=None,
    pinned=False,
    footprint=None,
    interior_build=None,
    interior_geometry=None,
    location_coverage=(),
    hole_requirements=(),
    placement_side="above",
):
    """A :class:`CorridorCandidate` for a datum-referenced hole/pattern location dim.
    Location dims outrank a coincident slot-position line in dedup (#345) and form the
    outer, datum-distance-ordered run of the ladder (#346). After view planning has assigned
    the member to plan/side, it is force-kept within that view (policy B); only a physically
    full strip drops (``location_ref_dropped`` → hole-table escalate)."""

    def _placed(nm):
        # Only loose HoleFeatures have rows in the automatic scattered-hole table.
        # Pattern locations remain documented by their own dimensions/furniture; marking
        # them replaceable lets an unrelated successful table silently delete their
        # provenance.
        if getattr(feature, "kind", None) == "hole":
            ctx.coverage.cover_scattered_hole_doc(nm)
        if pinned:
            dwg.pin(nm)

    def _drop(nm):
        edge = "plan view" if view == "plan" else "side view"
        where = "beside" if placement_side in {"left", "right"} else placement_side
        ctx.record_issue(
            "warning",
            "location_ref_dropped",
            f"{nm} not placed (no room {where} the {edge})",
            measurement=measurement,
            hole_requirements=hole_requirements,
        )
        ctx.escalations.append(Escalation("location", view, nm, "strip_full"))

    return CorridorCandidate(
        name=name,
        build=lambda pos: _with_hole_location_coverage(build(pos), location_coverage),
        order=(_LOC_SUBCHAIN, distance, name),
        # A placed location may later be replaced by the scattered-hole table.
        on_place=_placed,
        on_drop=_drop,
        dedup=(view, span_key[0], span_key[1], label),
        precedence=3 if pinned else 2,
        priority=PRIORITY.MANDATORY if pinned else PRIORITY.AUTO,
        force=True,
        feature=feature,  # provenance (ADR 5 (was 0010)): the located hole/pattern
        measurement=measurement,  # which of its measurements this is
        footprint=footprint,  # analytical measure — no probe build
        interior_view=None if pinned else view,
        interior_side=None if pinned else placement_side,
        interior_build=(
            None
            if pinned or interior_build is None
            else lambda pos: _with_hole_location_coverage(interior_build(pos), location_coverage)
        ),
        interior_geometry=None if pinned else interior_geometry,
    )


def _circular_channel_axis_marks(dwg, ctx, dimensions):
    """Give approved axis coordinates a visible natural reference in each projection."""
    existing = {
        getattr(ctx.registry.named(name), "axis_reference_point", None)
        for name in ctx.registry.names()
        if name.startswith("m_seat_axis_")
    }
    for dimension in dimensions:
        view = dimension.view
        if view not in dwg.views:
            continue
        point = dwg.at(view, *dimension.span[1])
        key = (view, round(point[0], 9), round(point[1], 9))
        if key in existing:
            continue
        existing.add(key)
        prefix = f"m_seat_axis_{view}"
        name = f"{prefix}{_first_free_index(prefix, ctx.registry.names())}"
        mark = CenterMark(point, 2 * dwg.draft.arrow_length, dwg.draft)
        mark.axis_reference_point = key
        # Coaxial seats share this geometric reference; it carries no measurement credit
        # and belongs to no single feature whose drop could erase the other seats' axis.
        ctx.place(mark, name, view=view)


def render_circular_channel_locations(
    dwg, plan, a, *, ctx, only=None, pinned=None, axes=None, dim_builder, register, axis_marks
) -> int:
    """Register approved seat-axis offsets in the shared profile corridors."""
    only_refs = None if only is None else {FeatureRef(feature) for feature in only}
    pinned_refs = {FeatureRef(feature) for feature in (pinned or ())}
    want = {"x", "y", "z"} if axes is None else {axis.lower() for axis in axes}
    if not want <= {"x", "y", "z"}:
        raise ValueError("locate(): axes must be a subset of ('x', 'y', 'z')")
    groups: dict[tuple, list[ApprovedDimension]] = {}
    for dimension in plan.locations:
        if (
            dimension.role != CircularChannelFeature.LOCATION_STEM
            or dimension.discriminator not in want
        ):
            continue
        if only_refs is not None and dimension.ref not in only_refs:
            continue
        # Coaxial seats can share an ordinate even when their run stations differ.
        # Group only equal physical datum/axis ordinates in the same projection.
        axis = "xyz".index(dimension.discriminator)
        key = (dimension.view, axis, dimension.span[0][axis], dimension.span[1][axis])
        groups.setdefault(key, []).append(dimension)
    if not groups:
        return 0
    axis_marks(dwg, ctx, (entry for entries in groups.values() for entry in entries))
    used = set(ctx.registry.names())
    tier = dwg.draft.font_size + 2 * dwg.draft.pad_around_text
    for (view, axis, _start, _end), dimensions in groups.items():
        dimension = dimensions[0]
        mids = tuple(entry.id for entry in dimensions)
        label = shared_location_text(dimensions)
        side, stack = ("right", "x") if axis == 2 else ("above", "y")
        zones = {"front": a.fv_zones, "side": a.sv_zones}[view]
        prefix = f"m_seatloc_{'xyz'[axis]}"
        name = f"{prefix}{_first_free_index(prefix, used)}"
        used.add(name)

        def dropped(_name, mids=mids):
            ctx.record_issue(
                "warning",
                "circular_channel_location_dropped",
                "seat axis location was not placed (profile strip unavailable or full)",
                measurement=mids,
            )

        if view not in dwg.views:
            dropped(name)
            continue
        assert dimension.span is not None
        p1, p2 = (dwg.at(view, *point) for point in dimension.span)
        edge = max(p1[0], p2[0]) if side == "right" else max(p1[1], p2[1])
        pinned_group = any(entry.ref in pinned_refs for entry in dimensions)

        geometry = _CircularChannelLocationGeometry(dwg, dim_builder, p1, p2, side, edge, label)

        def placed(name, pinned_group=pinned_group):
            if pinned_group:
                dwg.pin(name)

        register(
            ctx,
            (view, side),
            getattr(zones, side),
            view,
            stack,
            tier,
            CorridorCandidate(
                name=name,
                build=geometry.build,
                footprint=geometry.footprint,
                order=(_LOC_SUBCHAIN, dimension.value, name),
                on_place=placed,
                on_drop=dropped,
                # Hole and seat axes can share a physical datum ordinate. The
                # shared corridor keeps one visible location while retaining
                # both compiled measurement identities.
                dedup=(
                    view,
                    round(p1[0 if side == "above" else 1], 1),
                    round(p2[0 if side == "above" else 1], 1),
                    label,
                ),
                force=True,
                priority=PRIORITY.MANDATORY if pinned_group else PRIORITY.AUTO,
                feature=dimension.ref if len({entry.ref for entry in dimensions}) == 1 else None,
                measurement=mids,
            ),
        )
    return len(groups)


def render_locations(
    dwg,
    plan,
    a,
    *,
    ctx,
    only=None,
    pinned=None,
    dim_builder,
    register,
    iso_bbox,
    seat_locations: Callable[..., int],
    location_candidate,
) -> int:
    """Register compiler-approved location dimensions in shared corridors."""
    # This ladder consumes only Z-normal feature locations.  Non-Z pocket/pad entries
    # carry axis-local in-plane spans whose first endpoint is deliberately not the global
    # XY datum on both coordinates.  Derive the ladder datum only after excluding those
    # entries; taking it from ``plan.locations[0]`` lets feature ordering shift every
    # later Z-pad ordinate.
    n = seat_locations(
        dwg,
        plan,
        a,
        ctx=ctx,
        only=only,
        pinned=pinned,
    )
    approved = [
        loc for loc in plan.locations if loc.axis == "z" and loc.role != SlotFeature.LOCATION_STEM
    ]
    if not approved:
        return n
    draft = dwg.draft
    datum_x, datum_y = approved[0].span[0][0], approved[0].span[0][1]
    only_refs = None if only is None else {FeatureRef(f) for f in only}
    refs = []
    for loc in approved:
        if only_refs is not None and loc.ref not in only_refs:  # recorded subset only
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
        # A slot's position is drawn by `render_slots`, from this same entry (it reads
        # `slot_positions` by role). Reaching it here too is not a second view of one
        # measurement, it is a DIFFERENT measurement with no compiled backing: this ladder
        # takes the plan X/Y of `span[1]` and prints an offset from the datum, while the
        # entry measures along the slot's long axis. It only ever arrived because the
        # `loc.axis != "z"` filter above reads `axis` as "Z-normal", and for a slot `axis`
        # is the LONG axis — so a Z-long slot fell through and an X- or Y-long one did not.
        # Exclude every slot orientation here so its position has one renderer.
        # Provenance (ADR 5 (was 0010)): the located feature. `resolve_feature` is the sanctioned
        # seam for exactly this — the corridor's feature map keys drop/annotations_of.
        # `loc.id` rides along as the measurement identity: the compiler already
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
                loc,
            )
        )
    if not refs:
        return n
    pinned_set = set(pinned or ())
    tier = draft.font_size + 2 * draft.pad_around_text

    # The automatic pass numbers location dimensions positionally. Finalize may
    # run after live locate dimensions already hold names, so it allocates the
    # first free index to avoid Drawing.add replacing one.
    _loc_used = set(ctx.registry.names()) if only is not None else None

    def _loc_name(prefix: str, i: int) -> str:
        if _loc_used is None:
            return f"{prefix}{i}"  # automatic positional name
        name = f"{prefix}{_first_free_index(prefix, _loc_used)}"
        _loc_used.add(name)
        return name

    def _discard_short_refs(refs, coordinate, datum, view):
        # Nearness on paper is not coincidence with the physical datum. A required
        # nonzero span that cannot be drawn must remain a recorded placement failure.
        short = [
            ref
            for ref in refs
            if 1e-6 < abs(ref[coordinate] - datum) and abs(ref[coordinate] - datum) * a.SCALE < 1.0
        ]
        if short:
            axis = "X" if coordinate == 0 else "Y"
            ctx.record_issue(
                "warning",
                "location_ref_dropped",
                f"{len(short)} {axis} location dim(s) project to less than 1 mm (use a detail view)",
                measurement=tuple(mid for ref in short for mid in ref[4]),
                hole_requirements=tuple(
                    (feature, parameter) for ref in short for feature, parameter, _point in ref[5]
                ),
            )
            ctx.escalations.append(Escalation("location", view, None, "illegible"))
        return [ref for ref in refs if ref not in short]

    # --- X locations: tier above the plan view ---
    PX, PY = a.proj.plan_x, a.proj.plan_y
    x_refs: list = []
    for r in refs:
        if r[4] not in (None, "x"):
            continue
        for u in x_refs:
            if _same_location_ordinate(r[0], u[0]):
                u[6].append(r[6])
                u[3] = u[3] or r[2] in pinned_set
                # Collapsing coincident Xs into one dim must ACCUMULATE what it draws
                # The survivor genuinely measures every collapsed feature's X.
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
                    [r[6]],
                ]
            )
    x_refs = _discard_short_refs(x_refs, 0, datum_x, "plan")
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
    # Register X-location dims into the shared plan-above corridor (ADR 2 (was 0009) end state),
    # so the slot pass feeds the SAME strip: a single solve_corridor drain
    # dedups a coincident slot-position line and orders the whole ladder — instead of each
    # pass carving around the other and interleaving. No alternate view for a plan-X
    # location, so a corridor-blocked dim is force-kept (policy B), not relocated; only a
    # physically full strip drops (→ location_ref_dropped, escalates the hole table).
    for i, (rx, ry, feat, pin_ref, mids, location_facts, location_entries) in enumerate(
        sorted(x_refs, key=lambda r: abs(r[0] - datum_x))
    ):
        if abs(rx - datum_x) * a.SCALE < 1.0:
            continue  # on the datum edge — nothing to dimension
        label = shared_location_text(location_entries)
        n += 1
        label_offset = short_dimension_label_offset(
            (PX(datum_x), PY(ry), 0), (PX(rx), PY(ry), 0), draft, label
        )
        # A single X-location dim shared by two *distinct* features at this X belongs to
        # neither exclusively — leave it unowned so drop cannot over-strip a sibling's
        # dimension and annotations_of never over-claims it (ADR 5 (was 0010)).
        _shared_x = any(
            o[4] in (None, "x") and _same_location_ordinate(o[0], rx) and o[2] != feat
            for o in refs
        )
        _xfeat = None if _shared_x else feat
        # The measurement does NOT follow the feature. Feature-unowned is an
        # ADR 5 (was 0010) *ownership* rule — it stops drop(feature) stripping a sibling's dim. It
        # says nothing about what the dim measures, and a shared dim measures BOTH features'
        # X location. Record every approved measurement in the tuple-valued
        # channel (ADR 4 (was 0016)) so audit can credit all owners.
        # One ADR 4 (was 0016) feature-level location identity per collapsed owner; the structured
        # location facts below carry that this particular visible member is X.
        _xmid = tuple(mids)
        # On the experimental staggered layout the plan can abut the top sheet margin.
        # The farther X stations may use the free exterior strip below the plan while
        # the nearest station keeps its established tier. Both are solver-owned strips.
        x_below = (
            layout_flag("plan_x_below", "DRAFTWRIGHT_EXPERIMENTAL_PLAN_X_BELOW")
            and i > 0
            and a.pv_zones.above.available < a.pv_zones.above.gap + tier
            and a.pv_zones.below.available >= a.pv_zones.below.gap + tier
        )
        x_side = "below" if x_below else "above"
        x_zone = a.pv_zones.below if x_below else a.pv_zones.above
        geometry = _XLocationGeometry(
            PX, PY, datum_x, rx, ry, x_side, label, label_offset, draft, dim_builder
        )
        register(
            ctx,
            ("plan", x_side),
            x_zone,
            "plan",
            "y",
            tier,
            location_candidate(
                dwg,
                ctx,
                _loc_name("m_locx", i),
                view="plan",
                span_key=(round(PX(datum_x), 1), round(PX(rx), 1)),
                label=label,
                distance=abs(rx - datum_x),
                build=geometry.build,
                feature=_xfeat,
                measurement=_xmid,
                location_coverage=location_facts,
                hole_requirements=tuple(
                    (feature, parameter) for feature, parameter, _point in location_facts
                ),
                pinned=pin_ref,
                footprint=geometry.footprint,
                interior_build=geometry.interior_build,
                interior_geometry=geometry.interior_geometry,
            ),
        )

    return _register_y_locations(
        dwg,
        a,
        ctx,
        refs,
        datum_y,
        pinned_set,
        tier,
        draft,
        PX,
        PY,
        _loc_name,
        _discard_short_refs,
        n,
        dim_builder=dim_builder,
        register=register,
        iso_bbox=iso_bbox,
        location_candidate=location_candidate,
    )


def _register_y_locations(
    dwg,
    a,
    ctx,
    refs,
    datum_y,
    pinned_set,
    tier,
    draft,
    PX,
    PY,
    _loc_name,
    _discard_short_refs,
    n: int,
    *,
    dim_builder,
    register,
    iso_bbox,
    location_candidate,
) -> int:
    """Register the Y-ordinate corridor after the X corridor has been queued."""
    # --- Y locations: above side, or vertically beside plan when side is absent ---

    # Both are the same face-on Z-feature location requirement. The fixed topology preferred
    # side because Y runs horizontally there; ADR 2 (was 0018) reduced view sets must not retain side
    # solely for that presentation choice, so plan's vertical Y axis is the fallback.
    side_planned = "side" in dwg.views
    SX, SZ = a.proj.side_x, a.proj.side_z
    side_top = SZ(a.bb.max.Z) if side_planned else 0.0
    iso_x0, iso_y0, _, _ = iso_bbox(dwg)
    y_refs: list = []
    for r in refs:
        if r[4] not in (None, "y"):
            continue
        for u in y_refs:
            if _same_location_ordinate(r[1], u[1]):
                u[6].append(r[6])
                u[3] = u[3] or r[2] in pinned_set
                if r[4] in (None, "y") and r[3] is not None and r[3] not in u[4]:
                    u[4].append(r[3])  # accumulate, as in the X loop
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
                    [r[6]],
                ]
            )
    y_refs = _discard_short_refs(y_refs, 1, datum_y, "side" if side_planned else "plan")
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
        ctx.escalations.append(
            Escalation("location", "side" if side_planned else "plan", None, "illegible")
        )
    _kept_y_set = set(_kept_y)
    y_refs = [r for r in y_refs if r[1] not in _y_drawable or r[1] in _kept_y_set]
    # Cap the side-above strip below the iso view so Y-location dims never run under it
    # (the carve respects outer_limit); the dim_pitch_side dims are obstacles
    # the carve avoids directly.
    if (
        side_planned
        and y_refs
        and any(SX(ry) + 10 > iso_x0 - 4 for _, ry, _feat, _pin, _mids, _facts, _entries in y_refs)
    ):
        a.sv_zones.above.outer_limit = min(a.sv_zones.above.outer_limit, iso_y0 - 4)
    for i, (_rx, ry, feat, pin_ref, mids, location_facts, location_entries) in enumerate(
        sorted(y_refs, key=lambda r: abs(r[1] - datum_y))
    ):
        if abs(ry - datum_y) * a.SCALE < 1.0:
            continue
        label = shared_location_text(location_entries)
        n += 1
        # A shared Y location is unowned for the same reason as shared X.
        _shared_y = any(
            o[4] in (None, "y") and _same_location_ordinate(o[1], ry) and o[2] != feat
            for o in refs
        )
        _yfeat = None if _shared_y else feat
        # Every collapsed feature-level location; the structured facts carry Y (see X above).
        _ymid = tuple(mids)
        if side_planned:
            view, direction, strip, stack = "side", "above", a.sv_zones.above, "y"
            edge = side_top
            pa = (SX(datum_y), edge, 0)
            pb = (SX(ry), edge, 0)
            span_key = (round(pa[0], 1), round(pb[0], 1))
        else:
            view, stack = "plan", "x"
            if a.pv_zones.right is not None:
                direction, strip = "right", a.pv_zones.right
                edge = PX(a.bb.max.X)
            else:
                direction, strip = "left", a.pv_zones.left
                edge = PX(a.bb.min.X)
            pa = (edge, PY(datum_y), 0)
            pb = (edge, PY(ry), 0)
            span_key = (round(pa[1], 1), round(pb[1], 1))
        label_offset = (
            short_dimension_label_offset(pa, pb, draft, label) if view == "side" else 0.0
        )
        interior_direction = {
            "above": "below",
            "below": "above",
            "right": "left",
            "left": "right",
        }[direction]
        geometry = _YLocationGeometry(
            pa, pb, direction, interior_direction, edge, label, label_offset, draft, dim_builder
        )
        register(
            ctx,
            (view, direction),
            strip,
            view,
            stack,
            tier,
            location_candidate(
                dwg,
                ctx,
                _loc_name("m_locy", i),
                view=view,
                span_key=span_key,
                label=label,
                distance=abs(ry - datum_y),
                build=geometry.build,
                feature=_yfeat,
                measurement=_ymid,
                location_coverage=location_facts,
                hole_requirements=tuple(
                    (feature, parameter) for feature, parameter, _point in location_facts
                ),
                placement_side=direction,
                pinned=pin_ref,
                footprint=geometry.footprint,
                interior_build=geometry.interior_build,
                interior_geometry=geometry.interior_geometry,
            ),
        )
    return n
