"""Slot, pad, and pocket in-plane dimensions and obround-radius candidates.

The compiler approves content; this owner builds witnesses and registers candidates in
shared annotation corridors. The public render pass remains in from_model.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

from draftwright._core import _STRIP_SPACING, Strip, _dim, _tol_suffix
from draftwright.annotations._common import (
    _LOC_SUBCHAIN,
    _SIZE_SUBCHAIN,
    CorridorCandidate,
    Escalation,
    InteriorDimensionJob,
    PlacementContext,
    _ray_exit_dist,
    place_strip_candidates,
    register_corridor,
)
from draftwright.annotations.leaders import (
    FeatureLeaderCandidate,
    LeaderCandidateRegion,
)
from draftwright.model.compiled import DimensionId, FeatureRef, resolve_feature
from draftwright.model.ir import PadFeature, PocketFeature, SlotFeature


@dataclass(frozen=True)
class _SlotDimensionBuilder:
    start: tuple[float, float, int]
    end: tuple[float, float, int]
    side: str
    witness: float
    label: str
    draft: Any
    shared_value: float | None

    def __call__(self, position: float) -> Any:
        dim = _dim(
            self.start,
            self.end,
            self.side,
            abs(position - self.witness),
            self.draft,
            label=self.label,
        )
        if self.shared_value is not None:
            # A shared width counts slots; its witness spans one width.
            dim._dw_spec.label_value = self.shared_value
        return dim


@dataclass
class _SlotLaneDrop:
    ctx: PlacementContext
    dwg: Any
    kind: str
    index: int
    view: str
    feature: SlotFeature | PadFeature | PocketFeature
    measurement: DimensionId | None
    lane: int
    rejections: list[str]

    def __call__(self, _name: str) -> None:
        _record_slot_drop(
            self.ctx,
            self.dwg,
            self.kind,
            self.index,
            self.view,
            self.feature,
            self.measurement,
            lane=self.lane,
            blockers=tuple(self.rejections),
        )


@dataclass
class _SlotFarDrop:
    ctx: PlacementContext
    dwg: Any
    strip: Strip | None
    side: str
    high: bool
    axis: str
    feature: SlotFeature | PadFeature | PocketFeature
    kind: str
    shared_owners: tuple[SlotFeature, ...]
    measurement: DimensionId | None
    view: str
    index: int
    tier: float
    candidate: Callable[[str, bool], tuple[str, _SlotDimensionBuilder]]

    def retry(self, name: str) -> None:
        if self.strip is not None and not place_strip_candidates(
            self.dwg,
            self.strip,
            self.view,
            self.axis,
            [self.candidate(self.side, self.high)],
            self.tier,
            ctx=self.ctx,
            features={name: self.feature},
            measurements={name: self.measurement},
            trace=self.ctx.trace,
            trace_label=f"slot_{self.side}_fallthrough",
        ):
            if len(self.shared_owners) > 1:
                self.dwg.get_annotation(name).source_features = self.shared_owners
            return  # placed on the opposite strip
        _record_slot_drop(
            self.ctx,
            self.dwg,
            self.kind,
            self.index,
            self.view,
            self.feature,
            self.measurement,
        )

    def __call__(self, name: str) -> None:
        # Front-view retries run after all corridors drain, so a retry cannot
        # occupy a later sibling's force candidate before that sibling solves.
        if self.view == "front":
            self.ctx.post_drain.append(partial(self.retry, name))
        else:
            self.retry(name)


def _record_slot_drop(
    ctx,
    dwg,
    kind,
    idx,
    view,
    feat,
    measurement=None,
    *,
    lane=None,
    blockers=(),
):
    """Record a slot dim the layout could not place (#135).

    Info severity — a dim with no clear room is dropped as "place what fits",
    not an error. Alongside the lint code, appends a first-class ``Escalation``
    (ADR 2 (was 0009 Amdt 1), #351 PR-4a) so the drop is object-visible too; slots have
    no natural grouping remedy like a recognised hole pattern, so no resolver
    consumes this yet — purely additive.
    """
    feature_kind = getattr(feat, "kind", None)
    noun = feature_kind if feature_kind in ("pad", "pocket") else "slot"
    lane_reason = "" if lane is None else f"; requested lane {lane} unavailable"
    blocker_reason = "" if not blockers else f"; blockers: {', '.join(blockers)}"
    ctx.record_issue(
        "info",
        (
            "pad_dim_dropped"
            if noun == "pad"
            else "pocket_dim_dropped"
            if noun == "pocket"
            else "slot_dim_dropped"
        ),
        f"{noun}{idx} {kind} dim not placed "
        f"(no room beside the {view}{lane_reason}{blocker_reason})",
        measurement=measurement,
        evidence_reason=(
            None
            if lane is None
            else f"requested_lane_unavailable:{lane}:"
            + ",".join(blockers or ("no_admissible_candidate",))
        ),
    )
    ctx.escalations.append(
        Escalation(kind=noun, view=view, feature=feat, reason=f"no room beside the {view}")
    )


def _place_slot_dimension(
    dwg,
    ctx,
    s,
    draft,
    tier,
    shared_widths,
    _shared_width_key,
    view,
    zones,
    h_axis,
    h_proj,
    v_proj,
    i,
    meas_axis,
    p_lo,
    p_hi,
    perp_lo,
    perp_hi,
    approved,
    kind,
    anchor="center",
    sfx="",
):
    vw, zn, ha, hp, vp, idx = view, zones, h_axis, h_proj, v_proj, i
    # The approved entry supplies the printed value; feature fields only locate
    # witnesses. The compiler has already formatted any authored tolerance.
    lbl = approved.value_text + sfx
    shared_key = _shared_width_key(s, approved) if s.kind == "slot" and kind == "width" else None
    shared_owners = tuple(shared_widths.get(shared_key, ())) if shared_key else ()
    if len(shared_owners) > 1:
        lbl = f"{len(shared_owners)}× {lbl}"
    # Raw (pre-snap) endpoints — the dedup key must share a basis with the
    # hole-location key (which uses the raw ref), else the ~0.05 mm snap gap can
    # push a coincident span into an adjacent 0.1 mm page bin and the
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

        return cname, _SlotDimensionBuilder(
            start=e_lo,
            end=e_hi,
            side=side,
            witness=witness,
            label=lbl,
            draft=draft,
            shared_value=disp if len(shared_owners) > 1 else None,
        )

    # Register into the corridor batch (ADR 2 (was 0014) collect-then-solve). One solve
    # per strip dedups coincident slot and hole positions, orders size and
    # location runs, and arbitrates among occupants by priority when full.
    # Front-view slot and pocket dimensions join that batch with the height
    # ladder so pass order cannot claim its strip first. Plan/side right-left
    # slot and pocket dimensions retain immediate placement: the corridor
    # carve treats their full geometry as an obstruction even when a label
    # fits between extension lines. Pads use the shared corridor solve
    # on every end-on view (ADR 2 (was 0014)).
    use_corridor = (
        s.kind == "pad" or vw[0] == "front" or (meas_axis == ha and vw[0] in ("plan", "side"))
    )
    is_pos = kind.startswith("pos")
    drop_word = "position" if is_pos else kind
    near_side, near_strip, near_hi = sides[0]
    far_side, far_strip, far_hi = sides[1]
    corridor_axis = "y" if near_side in ("above", "below") else "x"

    def _shared_placed(nm, _owners=shared_owners):
        if len(_owners) > 1:
            dwg.get_annotation(nm).source_features = _owners

    # A declared lane is feature-relative: lane 1 is the first drafting-spaced
    # parallel position beyond this dimension's own witness, not the edge of the
    # whole orthographic view.  This lets the shared measured-candidate solve use
    # proven whitespace either inside or outside the view without exposing a page
    # coordinate.  Automatic dimensions retain their established exterior path.
    if approved.lane is not None:
        jobs = getattr(ctx, "interior_dimensions", None)
        if jobs is None or ctx.exterior_dimensions_only:
            return False
        _candidate_name, lane_build = _cand_for(near_side, near_hi)
        witness = perp_proj(perp_hi if near_hi else perp_lo)
        lane_step = tier + (near_strip.spacing if near_strip is not None else _STRIP_SPACING)
        direction = 1.0 if near_side in ("above", "right") else -1.0
        position = witness + direction * (2 * dwg.draft.extension_gap + approved.lane * lane_step)
        lane_rejections: list[str] = []

        jobs.append(
            InteriorDimensionJob(
                name=cname,
                view=vw[0],
                side=near_side,
                build=lane_build,
                on_place=_shared_placed,
                on_drop=_SlotLaneDrop(
                    ctx=ctx,
                    dwg=dwg,
                    kind=drop_word,
                    index=idx,
                    view=vw[0],
                    feature=s,
                    measurement=approved.id,
                    lane=approved.lane,
                    rejections=lane_rejections,
                ),
                lane_step=lane_step,
                feature=s,
                measurement=approved.id,
                interior_build=lane_build,
                explicit_position=position,
                requested_lane=approved.lane,
                rejection_reasons=lane_rejections,
            )
        )
        return True
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
                measurements={cname: approved.id},
                trace=ctx.trace,
                trace_label=f"slot_{side}",
            ):
                return True
        return False

    dedup_key = (
        (vw[0], round(meas_proj(raw_lo), 1), round(meas_proj(raw_hi), 1), lbl)
        if is_pos
        else shared_key
        if len(shared_owners) > 1
        else None
    )

    # The plan/side opposite-strip path runs during on_drop. Front-view retries
    # wait for all corridor solves before considering the opposite strip.
    far_or_drop = _SlotFarDrop(
        ctx=ctx,
        dwg=dwg,
        strip=far_strip,
        side=far_side,
        high=far_hi,
        axis=corridor_axis,
        feature=s,
        kind=drop_word,
        shared_owners=shared_owners,
        measurement=approved.id,
        view=vw[0],
        index=idx,
        tier=tier,
        candidate=_cand_for,
    )

    if near_strip is None:
        # Nothing to register against; the opposite side is the only chance.
        far_or_drop(cname)
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
            on_place=_shared_placed,
            on_drop=far_or_drop,
            measurement=approved.id,
            dedup=dedup_key,
            precedence=1 if is_pos else 0,
            force=False,
            feature=s,  # provenance (ADR 5 (was 0010)): this dim belongs to the slot
        ),
    )
    return True  # deferred — the callback owns the drop; caller's else must not fire


def _render_slot_dimensions(dwg, plan, a, *, ctx, only=None, reach) -> tuple[int, list]:
    """Build compiled slot-family dimensions and collect late radius jobs.

    Slot, pad, and pocket geometry supplies witnesses; every printed size and
    location comes from an approved entry. Filtered builds retain model indices.
    """
    slot_groups = plan.of_kind("slot", "pad", "pocket")
    # Pocket location geometry is rendered in this in-plane pass, but its content
    # remains compiler-authoritative (ADR 1 (was 0015) / ADR 4 (was 0016)): datum and target come from the same
    # approved location consumed by render_locations, including non-Z openings.
    # Keyed by (feature, MEASURED axis): a non-Z pocket is approved one entry per in-plane
    # coordinate, so keying by feature alone would keep whichever came last.
    in_plane_locations = {
        (loc.ref, loc.discriminator): loc
        for loc in plan.locations
        if loc.role in (PocketFeature.LOCATION_STEM, PadFeature.LOCATION_STEM)
        and loc.discriminator is not None
    }
    # A slot's own position dim — datum→near-end along its long axis. Compiled, not
    # computed from `a.bb`: it prints a number, so an authored set that does not name the
    # slot's location must not get one.
    # The compiler and renderer both derive this role from the feature contract;
    # spelling it independently at either end risks silently losing slot locations.
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
    radius_jobs = []
    kind_indices: dict[str, int] = {}
    only_refs = None if only is None else {FeatureRef(f) for f in only}

    # Two separate slots can have the same physical width across the same witness
    # span (the frame's two 17.35 mm openings are one example). Quantify one size
    # statement for that span, but only when both dimensions can enter the same
    # corridor solve. Matching displayed text alone would merge unequal sizes that
    # happen to round alike; matching geometry alone would merge distinct tolerances.
    def _shared_width_key(slot, approved):
        half_width = slot.width / 2
        slot_view = views[frozenset((slot.width_axis, slot.long_axis))]
        return (
            "slot_size",
            slot_view[0],
            slot.width_axis,
            round(slot.w_center - half_width, 3),
            round(slot.w_center + half_width, 3),
            approved.value_text + _tol_suffix(approved.tolerance, draft),
        )

    shared_widths: dict[tuple, list] = {}
    for group in slot_groups:
        if only_refs is not None and group.ref not in only_refs:
            continue
        slot = resolve_feature(group.ref)
        if slot.kind != "slot":
            continue
        slot_view = views[frozenset((slot.width_axis, slot.long_axis))]
        view_name, slot_zones, horizontal_axis = slot_view[:3]
        if view_name != "front" and slot.width_axis != horizontal_axis:
            continue  # immediate placement does not share the corridor dedup
        near_strip = slot_zones.above if slot.width_axis == horizontal_axis else slot_zones.right
        if near_strip is None:
            continue
        width_dim = group.dim(role="slot_width", kind="length")
        if width_dim is not None:
            shared_widths.setdefault(_shared_width_key(slot, width_dim), []).append(slot)

    for g in slot_groups:
        # The slot object supplies WITNESS GEOMETRY only — `lo`/`hi`/`w_center`/`width` fix
        # where the extension lines land, exactly as a centre mark is sized by its hole.
        # Every printed value below comes from an approved entry.
        s = resolve_feature(g.ref)
        i = kind_indices.get(s.kind, 0)
        kind_indices[s.kind] = i + 1
        if only_refs is not None and g.ref not in only_refs:
            continue  # #426 Ph2b: skip in place — i must stay the model index
        view = views[frozenset((s.width_axis, s.long_axis))]
        name, zones, h_axis, h_proj, _v_axis, v_proj = view

        _place = partial(
            _place_slot_dimension,
            dwg,
            ctx,
            s,
            draft,
            tier,
            shared_widths,
            _shared_width_key,
            view,
            zones,
            h_axis,
            h_proj,
            v_proj,
            i,
        )

        # Bind each approved dim explicitly by (role, kind) — never positionally.
        role_prefix = s.kind if s.kind in ("pad", "slot") else ""
        wpd = g.dim(role=f"{role_prefix}_width", kind="length")
        lpd = g.dim(role=f"{role_prefix}_length", kind="length")
        rpd = g.dim(role="slot_end_radius", kind="radius") if s.kind == "slot" else None
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
        if rpd is not None:
            bounds = dwg.view_bounds(name)
            if bounds is not None:
                radius_jobs.append(
                    (
                        f"m_slot{i}_radius",
                        name,
                        bounds,
                        f"2× R{rpd.value_text}{_tol_suffix(rpd.tolerance, draft)}",
                        _slot_end_radius_candidates(
                            dwg,
                            name,
                            bounds,
                            s,
                            rpd.value,
                            reach,
                            provenance=g.ref,
                        ),
                        (rpd.id,),
                    )
                )
        # Pads use compiled locations on both axes; slots use one position
        # dimension along their long axis here.
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
                # The immediate placement path must name the approved position
                # measurement on drop, just as the corridor path does. Otherwise
                # coverage reports the missing position without its identity.
                _record_slot_drop(ctx, dwg, "position", i, name, s, pos.id)
        elif s.kind in ("pocket", "pad") and s.frame.axis != "z":
            # Side-/front-opening pockets and pads need two in-plane coordinates in their
            # end-on view.  The compiler approves one entry PER coordinate, each with its
            # own value and span; Z-normal features use render_locations' X(plan)/Y(side)
            # ladder instead.
            width_lo, width_hi = s.w_center - half, s.w_center + half
            for axis, perp_lo, perp_hi, kind in (
                (s.long_axis, width_lo, width_hi, "pos_long"),
                (s.width_axis, s.lo, s.hi, "pos_width"),
            ):
                entry = in_plane_locations.get((FeatureRef(s), axis))
                if entry is None:
                    continue  # not approved
                index = "xyz".index(axis)
                start, end = entry.span[0][index], entry.span[1][index]
                if abs(end - start) * a.SCALE < 1.0:
                    continue
                if _place(axis, start, end, perp_lo, perp_hi, entry, kind, anchor="lo"):
                    count += 1
                else:
                    # Each non-Z pocket coordinate has its own approved entry. Report
                    # that entry's identity if placement fails, so the drop names the
                    # measurement the dimension would have shown.
                    _record_slot_drop(ctx, dwg, "position", i, name, s, entry.id)
    return count, radius_jobs


def _slot_end_radius_candidates(
    dwg,
    view,
    bounds,
    slot,
    radius,
    reach,
    *,
    provenance,
):
    """Lead normally into either proved semicircular end of an obround slot."""
    depth_axis = next(axis for axis in "xyz" if axis not in (slot.width_axis, slot.long_axis))
    coordinates = dict(zip("xyz", slot.frame.origin, strict=True))
    coordinates[slot.width_axis] = slot.w_center
    coordinates[slot.long_axis] = (slot.lo + slot.hi) / 2
    coordinates[depth_axis] = slot.frame.origin["xyz".index(depth_axis)]
    yield from _obround_radius_candidates(
        dwg,
        view,
        bounds,
        centre=tuple(coordinates[axis] for axis in "xyz"),
        long_axis=slot.long_axis,
        length=slot.hi - slot.lo,
        radius=radius,
        reach=reach,
        provenance=provenance,
    )


def _obround_radius_candidates(
    dwg,
    view,
    bounds,
    *,
    centre,
    long_axis,
    length,
    radius,
    reach,
    provenance,
):
    """Yield end-arc candidates from approved size and structural orientation."""
    coordinates = dict(zip("xyz", centre, strict=True))
    long_centre = coordinates[long_axis]
    for sign in (1.0, -1.0):
        coordinates[long_axis] = long_centre + sign * (length / 2 - radius)
        page_centre = dwg.at(view, *(coordinates[axis] for axis in "xyz"))
        coordinates[long_axis] = long_centre + sign * length / 2
        tip_page = dwg.at(view, *(coordinates[axis] for axis in "xyz"))
        dx, dy = float(tip_page[0] - page_centre[0]), float(tip_page[1] - page_centre[1])
        page_radius = math.hypot(dx, dy)
        if page_radius <= 1e-12:
            continue
        ux, uy = dx / page_radius, dy / page_radius
        tip = (float(tip_page[0]), float(tip_page[1]))
        # A stadium's void is useful proved whitespace too. Offer one fixed candidate
        # pointing back through the cap centre into that opening; it remains normal to
        # the arc while avoiding the exterior strips occupied by the width/length chain.
        yield FeatureLeaderCandidate(
            tip=tip,
            elbow=(tip[0] - ux * reach, tip[1] - uy * reach, 0),
            feature=provenance,
            region=LeaderCandidateRegion.INTERIOR,
        )
        # The whole proved semicircle is a legitimate attachment, not only its apex.
        # Fan across that arc so a crowded view can keep the radius without evicting an
        # unrelated leader; each shaft remains collinear with its local radius.
        for angle in (0.0, math.pi / 4, -math.pi / 4, math.pi / 2, -math.pi / 2):
            cosine, sine = math.cos(angle), math.sin(angle)
            direction = (ux * cosine - uy * sine, ux * sine + uy * cosine)
            arc_tip = (
                float(page_centre[0]) + direction[0] * page_radius,
                float(page_centre[1]) + direction[1] * page_radius,
            )
            exit_distance = _ray_exit_dist(
                arc_tip[0], arc_tip[1], direction[0], direction[1], bounds
            )
            elbow = (
                arc_tip[0] + direction[0] * (exit_distance + reach),
                arc_tip[1] + direction[1] * (exit_distance + reach),
                0,
            )
            yield FeatureLeaderCandidate(
                tip=arc_tip,
                elbow=elbow,
                feature=provenance,
            )
