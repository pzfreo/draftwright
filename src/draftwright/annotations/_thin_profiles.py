"""Compiler-approved plate thickness and open-channel width corridor candidates.

Both producers register candidates with the shared strip solve. The public render pass
stays in ``from_model`` and preserves its placement order and drop evidence.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from draftwright._core import _END_ON, Analysis, _dim, _fmt, _tol_suffix
from draftwright.annotations._common import (
    _SIZE_SUBCHAIN,
    CorridorCandidate,
    PlacementContext,
    dim_footprint,
    register_corridor,
)
from draftwright.model.compiled import ApprovedDimension
from draftwright.model.ir_foundation import Point


class _ChannelWidthCandidate(NamedTuple):
    """One approved channel width's deferred corridor callbacks."""

    pa: Point
    pb: Point
    side: str
    edge: float
    label: str
    view: str
    dimension: ApprovedDimension
    draft: Any
    ctx: PlacementContext

    def build(self, pos: float) -> Any:
        return _dim(self.pa, self.pb, self.side, pos - self.edge, self.draft, label=self.label)

    def footprint(self, pos: float) -> Any:
        return dim_footprint(self.pa, self.pb, self.side, pos - self.edge, self.draft, self.label)

    def drop(self, _name: str) -> None:
        self.ctx.record_issue(
            "warning",
            "channel_width_dropped",
            f"channel width {_fmt(self.dimension.value)} not dimensioned "
            f"({self.view} {self.side}-strip full)",
            measurement=self.dimension.id,
        )


class _PlateThicknessCandidate(NamedTuple):
    """One approved plate thickness's deferred geometry callbacks."""

    pa: Point
    pb: Point
    side: str
    edge: float
    label: str
    dimension: ApprovedDimension
    draft: Any

    def build(self, pos: float) -> Any:
        dim = _dim(self.pa, self.pb, self.side, pos - self.edge, self.draft, label=self.label)
        return dim

    def footprint(self, pos: float) -> Any:
        return dim_footprint(self.pa, self.pb, self.side, pos - self.edge, self.draft, self.label)


def register_plate_thickness(dwg, plan, a: Analysis, *, ctx, drop_factory) -> int:
    """Plate/wall thicknesses (#559).

    Plate thickness is the thin extent of each recognised slab
    (`PlateFeature`), placed in the view where its thin axis is characteristic — a Z
    plate (horizontal slab) as a vertical dim left of the front elevation, a Y plate
    (upright wall) as a horizontal dim above the side (end) view where the L-profile
    shows it edge-on, an X plate below the front view. Base and wall land in different
    views so the two legs of a multi-plate prismatic read as distinct features rather
    than the overall envelope. A slab whose strip is full is dropped with a lint code
    (like the step ladder), not silently. Returns the count registered.

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
    # Use only approved entries (ADR 4 (was 0016)); the plate's `lo`/`hi`
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

    # Sort identities by axis, then the plate's lower and
    # upper coordinates ALONG that thin axis. Sorting whole points would compare their
    # in-plane coordinates first and silently swap dim_plate_{axis}{i} names when two
    # same-axis plates move sideways.
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

        candidate_state = _PlateThicknessCandidate(pa, pb, side, edge, lbl, pd, draft)

        # ADR 2 (was 0009) corridor candidate: a plate thickness is a size dim bound to one
        # view/strip (no alternate view), so it is force-kept and dropped only when the strip
        # is physically full. Co-solve it with the locations and steps sharing the strip.
        register_corridor(
            ctx,
            (view, side),
            strip,
            view,
            stack,
            tier,
            CorridorCandidate(
                name=name,
                build=candidate_state.build,
                order=(_SIZE_SUBCHAIN, i, name),
                on_place=lambda nm: None,
                on_drop=drop_factory(
                    val=val,
                    lbl=lbl,
                    view=view,
                    stack=stack,
                    alt=alt,
                    feat=g.ref,
                    mid=pd.id,
                    measurement_span=pd.span,
                ),
                force=True,
                feature=g.ref,  # opaque provenance handle
                measurement=pd.id,
                measurement_span=pd.span,
                footprint=candidate_state.footprint,  # analytical measure — no probe build
            ),
        )
        n += 1
    return n


def register_channel_width(dwg, plan, a: Analysis, *, ctx) -> int:
    """Queue approved open-channel widths in their end-on view corridor."""
    draft = dwg.draft
    tier = draft.font_size + 2 * draft.pad_around_text
    n = 0
    channel_groups = [
        (g, pd)
        for g in plan.of_kind("channel")
        if (pd := g.dim(role="channel_width", kind="length")) is not None and pd.span is not None
    ]
    channel_counts: dict[str, int] = {"x": 0, "y": 0, "z": 0}
    view_for_long_axis = _END_ON  # looking down the long axis IS reading it end-on
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

        candidate_state = _ChannelWidthCandidate(pa, pb, side, edge, label, view, pd, draft, ctx)

        register_corridor(
            ctx,
            (view, side),
            strip,
            view,
            stack,
            tier,
            CorridorCandidate(
                name=name,
                build=candidate_state.build,
                order=(_SIZE_SUBCHAIN, index, name),
                on_place=lambda _name: None,
                on_drop=candidate_state.drop,
                force=True,
                feature=g.ref,
                measurement=pd.id,
                footprint=candidate_state.footprint,
            ),
        )
        n += 1
    return n
