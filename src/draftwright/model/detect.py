"""detect — build the part-model IR by running the feature detectors (ADR 0008).

The front-end of the compiler. Each detector is an existing recognition heuristic
(:func:`recognise_holes`, :func:`recognise_turned_steps`, :func:`recognise_bosses`) adapted to
*emit* IR `Feature` objects — their B-rep logic is unchanged; only their output
shape is normalised into the waist. New shapes plug in here as new detectors
emitting new `Feature` types.

Turned profile and bosses are complementary, not competing (the #191 review): a
turned part is described by its `StepFeature`s (length + OD per segment); a
non-turned part's external diameters come from `BossFeature`s. Holes are detected
for any part.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from numbers import Real
from typing import Any

from b123d_recognisers import (
    AngledStep,
    Blend,
    BoltCircle,
    BossRecord,
    Chamfer,
    Channel,
    CircularBlindStep,
    CounterSink,
    DoubleDBore,
    FaceLevel,
    Fillet,
    Flat,
    Groove,
    HoleRecord,
    HoleSpec,
    LinearArray,
    OrientedSlot,
    OrientedSlotArray,
    OrientedSlotGrid,
    PairedRampStep,
    Passage,
    Plate,
    Pocket,
    PocketArray,
    PocketGrid,
    PolygonalBoss,
    PolygonalStock,
    PrismaticPocket,
    RaisedPad,
    RectangularBlindSlot,
    RectGrid,
    RepeatingRadialProfile,
    RiserEvidence,
    RoundBottomBlindSlot,
    SectionPassage,
    Slot,
    SlotArray,
    SlotGrid,
    StepShoulder,
    ThroughStep,
    TurnedStep,
    analyse_cylinders,
    build_raw_recognition_result,
    has_multi_axis_plates,
    project_step_shoulders,
    recognise_bosses,
    recognise_chamfers,
    recognise_channels,
    recognise_countersinks,
    recognise_double_d_bores,
    recognise_fillets,
    recognise_flats,
    recognise_grooves,
    recognise_hole_patterns,
    recognise_holes,
    recognise_oriented_slot_patterns,
    recognise_paired_ramp_steps,
    recognise_plates,
    recognise_pocket_patterns,
    recognise_pockets,
    recognise_polygonal_bosses,
    recognise_polygonal_stock,
    recognise_risers,
    recognise_slot_patterns,
    recognise_slots,
    recognise_through_steps,
    step_level_records,
)

from draftwright._geometry import (
    _axis_letter,
    _classify_rotational_cylinders,
    _is_principal_axis,
    _xyz,
    plane_axis_names,
)
from draftwright.model.declare import circular_blind_step, control_frame, datum
from draftwright.model.ir import (
    AUTHORED_DIMENSION_KINDS,
    AuthoredDimension,
    BossFeature,
    ChamferFeature,
    ChannelFeature,
    CircularBlindStepFeature,
    ControlFrame,
    Datum,
    DatumRef,
    Feature,
    FilletFeature,
    FlatFeature,
    Frame,
    GrooveFeature,
    HoleFeature,
    LevelSupport,
    OrientedSlotFeature,
    OrientedSlotPassage,
    PadFeature,
    PairedRampStepFeature,
    PartModel,
    PatternFeature,
    PlateFeature,
    PmiFeature,
    PocketFeature,
    PocketPatternFeature,
    PolygonalBossFeature,
    PolygonalStockFeature,
    RectangularBlindSlotFeature,
    RotationalFeature,
    RoundBottomBlindSlotFeature,
    SlotFeature,
    SlotPatternFeature,
    StepFeature,
    StepLevelFeature,
    ThroughStepFeature,
)
from draftwright.oriented_slot_contract import (
    oriented_slot_provider_key,
    validate_oriented_slot_pattern,
)
from draftwright.recognition_frame import (
    groove_owns_turned_step_band,
    require_unambiguous_groove_owner,
)


def _member_hole(h, frame: Frame, members: tuple = (), count: int = 1) -> HoleFeature:
    """A recogniser hole → an IR `HoleFeature` (bore + counterbore/spotface/countersink).
    When *h* represents a machining-spec group of identical holes, *members* are their
    locations and *count* their number. The countersink rides on the HoleRecord (#558)."""
    return HoleFeature(
        frame=frame,
        diameter=h.diameter,
        depth=h.depth,
        through=(h.bottom == "through"),
        count=count,
        members=members,
        cbore=(h.cbore.diameter, h.cbore.depth) if h.cbore else None,
        spotface=(h.spotface.diameter, h.spotface.depth) if h.spotface else None,
        csink=(h.csink.major_diameter, h.csink.included_angle) if h.csink else None,
    )


def _convert_double_d_bore(bore: DoubleDBore, ctx: ConvContext) -> HoleFeature:
    return HoleFeature(
        frame=Frame(origin=_xyz(bore.location), axis=_axis_letter(bore)),
        diameter=bore.major_diameter,
        depth=bore.depth,
        through=bore.through,
        profile="double_d",
        across_flats=bore.across_flats,
        profile_direction=bore.flat_direction,
    )


def _pattern_feature(pat, members) -> PatternFeature:
    """Map a recognised pattern + its member holes to a `PatternFeature`,
    composing a representative member hole so its counterbore/spotface survive."""
    axis = _axis_letter(members[0])
    n = len(members)
    locs = tuple(_xyz(m.location) for m in members)  # raw arrangement — never discarded
    if isinstance(pat, BoltCircle):
        frame = Frame(_xyz(pat.center), axis)
        return PatternFeature(
            frame,
            "bolt_circle",
            n,
            _member_hole(members[0], frame),
            members=locs,
            bcd=pat.diameter,
        )
    if isinstance(pat, LinearArray):
        c = (
            sum(m.location[0] for m in members) / n,
            sum(m.location[1] for m in members) / n,
            sum(m.location[2] for m in members) / n,
        )
        frame = Frame(c, axis)
        return PatternFeature(
            frame,
            "linear",
            n,
            _member_hole(members[0], frame),
            members=locs,
            pitch=pat.pitch,
            direction=pat.direction,
        )
    if isinstance(pat, RectGrid):
        frame = Frame(_xyz(pat.center), axis)
        return PatternFeature(
            frame,
            "grid",
            n,
            _member_hole(members[0], frame),
            members=locs,
            grid=(pat.row_pitch, pat.col_pitch),
            rows=pat.rows,
            cols=pat.cols,
            angle=pat.angle,
        )
    frame = Frame(_xyz(members[0].location), axis)  # unknown type — plain count× callout
    return PatternFeature(frame, "other", n, _member_hole(members[0], frame), members=locs)


def _distinct_by_diameter(bosses, tol: float = 0.15):
    """One representative boss per distinct external diameter."""
    out: dict[float, object] = {}
    for b in bosses:
        key = next((k for k in out if abs(k - b.diameter) <= tol), b.diameter)
        out.setdefault(key, b)
    return list(out.values())


def _boss_is_groove_floor(b, grooves) -> bool:
    """A recognised boss coinciding with a groove floor — same turning axis and (floor) ø — is
    that floor. The groove callout already dimensions it, so it must not also get a boss ø
    (#148c review; applies whether or not the part read as a turned profile)."""
    ax = _axis_letter(b)
    axis_index = "xyz".index(ax)
    return any(
        abs(b.diameter - g.diameter) <= _DIA_TOL
        and g.axis == ax
        and all(
            abs(float(b.location[index]) - float(g.at[index])) <= 0.5
            for index in range(3)
            if index != axis_index
        )
        for g in grooves
    )


_DIA_TOL = 0.15  # two ø values within this (mm) are the same diameter (#298)
_UNSET = object()  # sentinel: distinguishes "not supplied" from a valid prof=None


def build_pmi_features(
    pmi, bbox
) -> list[AuthoredDimension | PmiFeature | ControlFrame | DatumRef]:
    """Re-home extracted STEP AP242 PMI records into drafting-concept IR (#208).

    Shared by :func:`build_part_model` (the detection path) and the declared-model PMI
    synthesis in ``builder._assemble`` (#472) so both construct features identically.
    Dimensional PMI becomes :class:`AuthoredDimension`, because users edit drafting
    dimensions rather than source-format PMI. Complete, supported geometric tolerances become
    :class:`ControlFrame`; complete datum-feature definitions become :class:`DatumRef`;
    unsupported records remain raw :class:`PmiFeature` fallbacks. Empty/``None`` ``pmi`` →
    ``[]``."""
    out: list[AuthoredDimension | PmiFeature | ControlFrame | DatumRef] = []
    for r in pmi or ():
        if r.ref_bbox is not None:
            x0, y0, z0, x1, y1, z1 = r.ref_bbox
            pmi_origin = ((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2)
        else:
            pmi_origin = (bbox.center().X, bbox.center().Y, bbox.center().Z)
        ax = r.dominant_axis.lower() if r.dominant_axis in ("X", "Y", "Z") else "z"
        if r.kind in AUTHORED_DIMENSION_KINDS:
            out.append(
                AuthoredDimension(
                    frame=Frame(origin=pmi_origin, axis=ax),
                    dimension_kind=r.kind,
                    value=r.value,
                    label=r.label,
                    dominant_axis=r.dominant_axis,
                    upper_tol=r.upper_tol,
                    lower_tol=r.lower_tol,
                    lower_bound=r.lower_bound,
                    upper_bound=r.upper_bound,
                    ref_bbox=r.ref_bbox,
                    ref_pts=tuple(r.ref_pts),
                    source_kind=r.kind,
                    source_id=r.source_id,
                    lowering_blockers=r.lowering_blockers,
                    rendering_blockers=r.rendering_blockers,
                    cylindrical_refs=r.cylindrical_refs,
                )
            )
            continue
        raw = PmiFeature(
            frame=Frame(origin=pmi_origin, axis=ax),
            pmi_kind=r.kind,
            value=r.value,
            label=r.label,
            dominant_axis=r.dominant_axis,
            ref_bbox=r.ref_bbox,
            ref_pts=tuple(r.ref_pts),
            source_id=r.source_id,
            datum_refs=r.datum_refs,
            part21_id=r.part21_id,
            source_category=r.source_category,
            gtol_modifiers=r.gtol_modifiers,
            lowering_blockers=r.lowering_blockers,
            source_ids=r.source_ids,
            datum_contexts=r.datum_contexts,
            reference_item_ids=r.reference_item_ids,
            reference_axis=r.reference_axis,
            semantic_name=r.semantic_name,
            shape_aspect_ids=r.shape_aspect_ids,
            cylindrical_refs=r.cylindrical_refs,
        )
        if r.source_category == "geometric_tolerance" and not r.lowering_blockers:
            item = control_frame(
                r.kind,
                str(r.value),
                raw,
                datums=r.datum_refs,
            )
            out.append(
                replace(
                    item,
                    all_around="all_around" in r.gtol_modifiers,
                    all_over="all_over" in r.gtol_modifiers,
                    source_id=r.source_id,
                    part21_id=r.part21_id,
                )
            )
            continue
        if r.source_category == "datum" and not r.lowering_blockers and r.reference_axis:
            edge_view = {"X": "front", "Y": "side", "Z": "front"}[r.reference_axis]
            vertical_index = 2 if edge_view in ("front", "side") else 1
            center = bbox.center()
            center_coord = (center.X, center.Y, center.Z)[vertical_index]
            side = "above" if pmi_origin[vertical_index] >= center_coord else "below"
            datum_item = datum(r.label, raw, view=edge_view, side=side)
            out.append(
                replace(
                    datum_item,
                    source_id=r.source_id,
                    source_ids=r.source_ids or ((r.source_id,) if r.source_id else ()),
                    part21_id=r.part21_id,
                )
            )
            continue
        out.append(raw)
    return out


# ---------------------------------------------------------------------------
# ADR 0013 Phase 1c — the typed record→Feature converter registry (#752).
#
# `build_part_model` below owns the *assembly* — which records become features
# (pattern/hole grouping, groove/plate suppression, the classification-fed
# rotational/envelope/step-ladder furniture). *How* a single recognition record
# becomes an IR `Feature` lives here, one typed converter per record type,
# dispatched through the registry. The completeness/uniqueness of this table is
# machine-enforced by tests/test_detect_registry.py: every recognition record
# type has exactly one home across the three tiers below, so a new recogniser
# cannot silently produce features with no converter.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConvContext:
    """Shared build context threaded to every uniform record→Feature converter.

    A converter is a pure function of ``(record, ctx)``; ``bbox`` supplies the
    part's centre/extents for the off-axis frame coords and ``orientation`` the
    turning axis a :class:`StepFeature` span is laid along. Edge-treatment records carry their
    own package-owned ``turned`` discriminator; converters never infer surface type from an axis
    coincidence or re-inspect the solid."""

    bbox: Any  # build123d BoundBox (kept untyped so detect stays build123d-import-light)
    orientation: str | None


# A uniform converter: a pure function of one recognition record + the shared context.
Converter = Callable[[Any, "ConvContext"], Feature]


def _convert_slot(sl: Slot, ctx: ConvContext) -> SlotFeature:
    idx = "xyz".index(sl.long_axis)
    c = ctx.bbox.center()
    origin = [c.X, c.Y, c.Z]
    origin[idx] = (sl.lo + sl.hi) / 2
    return SlotFeature(
        frame=Frame(origin=(origin[0], origin[1], origin[2]), axis=sl.long_axis),
        width_axis=sl.width_axis,
        long_axis=sl.long_axis,
        width=sl.width,
        length=sl.length,
        w_center=sl.w_center,
        lo=sl.lo,
        hi=sl.hi,
    )


def _member_slot(sl: Slot) -> SlotFeature:
    """The representative member of a `SlotPatternFeature` — its width/length/axes drive the
    grouped callout. Built at the slot's own centroid (not `_convert_slot`'s bbox-centred frame,
    which is a lone-slot dim-placement convention): `render_slot_patterns` anchors the leader at
    the PATTERN centre and reads only the member's size/axes, so the member frame origin is
    inert (#841)."""
    c = {
        sl.long_axis: (sl.lo + sl.hi) / 2,
        sl.width_axis: sl.w_center,
        sl.depth_axis: (sl.d_lo + sl.d_hi) / 2,
    }
    return SlotFeature(
        frame=Frame(origin=(c["x"], c["y"], c["z"]), axis=sl.long_axis),
        width_axis=sl.width_axis,
        long_axis=sl.long_axis,
        width=sl.width,
        length=sl.length,
        w_center=sl.w_center,
        lo=sl.lo,
        hi=sl.hi,
    )


def _slot_pattern_feature(pat, members) -> SlotPatternFeature:
    """Map a recognised slot array + its member slots to a `SlotPatternFeature` (#841) — the
    through-slot analog of :func:`_pocket_pattern_feature`. Composes a representative member slot
    (its width/length drive the grouped ``count× SLOT W × L`` callout) and keeps the member
    centres as the raw arrangement the pitch furniture indexes. The frame axis is the members'
    shared THROUGH axis, matching the declared `slot_pattern`."""
    n = len(members)
    axis = members[0].depth_axis  # the through axis — the face plane the array lies in
    locs = tuple(_xyz(m.location) for m in members)  # raw arrangement — never discarded
    if isinstance(pat, SlotGrid):
        frame = Frame(_xyz(pat.center), axis)
        return SlotPatternFeature(
            frame=frame,
            pattern="grid",
            count=n,
            member=_member_slot(members[0]),
            members=locs,
            grid=(pat.row_pitch, pat.col_pitch),
            rows=pat.rows,
            cols=pat.cols,
            angle=pat.angle,
        )
    # SlotArray (linear) — the frame sits at the array centroid (no separate centre field).
    c = (
        sum(m.location[0] for m in members) / n,
        sum(m.location[1] for m in members) / n,
        sum(m.location[2] for m in members) / n,
    )
    return SlotPatternFeature(
        frame=Frame(c, axis),
        pattern="linear",
        count=n,
        member=_member_slot(members[0]),
        members=locs,
        pitch=pat.pitch,
        direction=tuple(pat.direction),
    )


def _member_pocket(pk: Pocket) -> PocketFeature:
    """A recogniser `Pocket` → an IR `PocketFeature`. The representative member of a
    `PocketPatternFeature` too (its size/axes drive the grouped callout), so it is factored
    out of :func:`_convert_pocket` and reused by :func:`_pocket_pattern_feature` (#841)."""
    # Frame at the recess centroid — in-plane centre + mid-depth. The render leader
    # projects into the view normal to the depth axis, so the depth coord is inert,
    # but a true centroid keeps the frame honest.
    c = {
        pk.long_axis: (pk.lo + pk.hi) / 2,
        pk.width_axis: pk.w_center,
        pk.depth_axis: (pk.d_lo + pk.d_hi) / 2,
    }
    return PocketFeature(
        frame=Frame(origin=(c["x"], c["y"], c["z"]), axis=pk.depth_axis),
        width_axis=pk.width_axis,
        long_axis=pk.long_axis,
        width=pk.width,
        length=pk.length,
        depth=pk.depth,
        w_center=pk.w_center,
        lo=pk.lo,
        hi=pk.hi,
        edge_anchored=pk.edge_anchored,
        open_sign=pk.open_sign,
    )


def _convert_pocket(pk: Pocket, ctx: ConvContext) -> PocketFeature:
    return _member_pocket(pk)


def _convert_channel(channel: Channel, ctx: ConvContext) -> ChannelFeature:
    c = channel.location
    return ChannelFeature(
        frame=Frame(origin=c, axis=channel.long_axis),
        width_axis=channel.width_axis,
        long_axis=channel.long_axis,
        width=channel.width,
        w_center=channel.w_center,
        lo=channel.lo,
        hi=channel.hi,
        d_lo=channel.d_lo,
        d_hi=channel.d_hi,
        open_sign=channel.open_sign,
    )


def _convert_pad(pad: RaisedPad, ctx: ConvContext) -> PadFeature:
    """A recognised bounded island → the dimensioning IR."""
    bounds = {
        "x": (pad.x0, pad.x1),
        "y": (pad.y0, pad.y1),
        "z": (pad.z0, pad.z1),
    }
    long_axis, width_axis = plane_axis_names(pad.axis)
    long_lo, long_hi = bounds[long_axis]
    width_lo, width_hi = bounds[width_axis]
    normal_lo, normal_hi = bounds[pad.axis]
    return PadFeature(
        frame=Frame(
            ((pad.x0 + pad.x1) / 2, (pad.y0 + pad.y1) / 2, (pad.z0 + pad.z1) / 2),
            pad.axis,
        ),
        width_axis=width_axis,
        long_axis=long_axis,
        width=width_hi - width_lo,
        length=long_hi - long_lo,
        w_center=(width_lo + width_hi) / 2,
        lo=long_lo,
        hi=long_hi,
        z0=normal_lo,
        z1=normal_hi,
        direction=pad.direction,
    )


def _pocket_pattern_feature(pat, members) -> PocketPatternFeature:
    """Map a recognised pocket array + its member pockets to a `PocketPatternFeature` (#841) —
    the recess analog of :func:`_pattern_feature`. Composes a representative member pocket (its
    width/length/depth drive the grouped ``count× W×L×D DEEP`` callout) and keeps the member
    centres as the raw arrangement the pitch furniture indexes. The frame axis is the members'
    shared DEPTH axis (the opening normal), matching the declared `pocket_pattern`."""
    n = len(members)
    axis = members[0].depth_axis  # the opening normal — the plane the array lies in
    locs = tuple(_xyz(m.location) for m in members)  # raw arrangement — never discarded
    if isinstance(pat, PocketGrid):
        frame = Frame(_xyz(pat.center), axis)
        return PocketPatternFeature(
            frame=frame,
            pattern="grid",
            count=n,
            member=_member_pocket(members[0]),
            members=locs,
            grid=(pat.row_pitch, pat.col_pitch),
            rows=pat.rows,
            cols=pat.cols,
            angle=pat.angle,
        )
    # PocketArray (linear) — the frame sits at the array centroid (no separate centre field).
    c = (
        sum(m.location[0] for m in members) / n,
        sum(m.location[1] for m in members) / n,
        sum(m.location[2] for m in members) / n,
    )
    return PocketPatternFeature(
        frame=Frame(c, axis),
        pattern="linear",
        count=n,
        member=_member_pocket(members[0]),
        members=locs,
        pitch=pat.pitch,
        direction=tuple(pat.direction),
    )


def _convert_step(s: TurnedStep, ctx: ConvContext) -> StepFeature:
    axis = s.axis
    idx = "xyz".index(axis)
    if s.profile is None:
        c = ctx.bbox.center()
        base = [c.X, c.Y, c.Z]
    else:
        base = list(s.profile.axis_origin)
    s_mid = (s.lo + s.hi) / 2
    lo = list(base)
    hi = list(base)
    lo[idx] = s.lo
    hi[idx] = s.hi
    mid = list(base)
    mid[idx] = s_mid
    return StepFeature(
        frame=Frame(origin=(mid[0], mid[1], mid[2]), axis=axis),
        length=s.length,
        diameter=s.diameter,
        span=((lo[0], lo[1], lo[2]), (hi[0], hi[1], hi[2])),
        profile=s.profile,
    )


def _convert_boss(b: BossRecord, ctx: ConvContext) -> BossFeature:
    return BossFeature(
        frame=Frame(origin=_xyz(b.location), axis=_axis_letter(b)),
        diameter=b.diameter,
        height=b.height,
        span=(
            (
                float(b.location[0] - b.axis[0] * b.height),
                float(b.location[1] - b.axis[1] * b.height),
                float(b.location[2] - b.axis[2] * b.height),
            ),
            _xyz(b.location),
        ),
    )


def _convert_polygonal_boss(boss: PolygonalBoss, ctx: ConvContext) -> PolygonalBossFeature:
    centre = boss.center
    lo = list(centre)
    hi = list(centre)
    axis_index = "xyz".index(boss.axis)
    lo[axis_index] = boss.base
    hi[axis_index] = boss.top
    return PolygonalBossFeature(
        frame=Frame(origin=centre, axis=boss.axis),
        side_count=boss.side_count,
        across_flats=boss.across_flats,
        height=boss.height,
        span=((lo[0], lo[1], lo[2]), (hi[0], hi[1], hi[2])),
        flat_directions=boss.flat_directions,
        flat_centres=boss.flat_centres,
    )


def _convert_polygonal_stock(stock: PolygonalStock, ctx: ConvContext) -> PolygonalStockFeature:
    centre = stock.center
    lo = list(centre)
    hi = list(centre)
    axis_index = "xyz".index(stock.axis)
    lo[axis_index] = stock.base
    hi[axis_index] = stock.top
    return PolygonalStockFeature(
        frame=Frame(origin=centre, axis=stock.axis),
        side_count=stock.side_count,
        across_flats=stock.across_flats,
        length=stock.length,
        span=((lo[0], lo[1], lo[2]), (hi[0], hi[1], hi[2])),
        flat_directions=stock.flat_directions,
        flat_centres=stock.flat_centres,
    )


def _convert_plate(pl: Plate, ctx: ConvContext) -> PlateFeature:
    c = ctx.bbox.center()
    return PlateFeature(
        frame=Frame((c.X, c.Y, c.Z), pl.axis),
        axis=pl.axis,
        lo=pl.lo,
        hi=pl.hi,
        u=pl.u,
        v=pl.v,
    )


def _convert_chamfer(ch: Chamfer, ctx: ConvContext) -> ChamferFeature:
    return ChamferFeature(
        frame=Frame((ch.at[0], ch.at[1], ch.at[2]), ch.axis),
        axis=ch.axis,
        leg1=ch.leg1,
        leg2=ch.leg2,
        angle=ch.angle,
        turned=ch.turned,
    )


def _convert_fillet(fl: Fillet, ctx: ConvContext) -> FilletFeature:
    return FilletFeature(
        frame=Frame((fl.at[0], fl.at[1], fl.at[2]), fl.axis),
        axis=fl.axis,
        radius=fl.radius,
        turned=fl.turned,
    )


def _convert_circular_blind_step(
    step: CircularBlindStep, ctx: ConvContext
) -> CircularBlindStepFeature:
    return circular_blind_step(
        axis=step.axis,
        radius=step.radius,
        length=step.length,
        centreline=step.centreline,
        section=step.section,
    )


def _convert_paired_ramp_step(step: PairedRampStep, ctx: ConvContext) -> PairedRampStepFeature:
    return PairedRampStepFeature(
        frame=Frame((step.at[0], step.at[1], step.at[2]), step.axis),
        axis=step.axis,
        angle=step.angle,
        length=step.length,
    )


def _convert_through_step(step: ThroughStep, ctx: ConvContext) -> ThroughStepFeature:
    return ThroughStepFeature(
        frame=Frame((step.at[0], step.at[1], step.at[2]), step.axis),
        axis=step.axis,
        length=step.length,
        section=step.section,
    )


def _convert_flat(flat: Flat, ctx: ConvContext) -> FlatFeature:
    at = flat.at
    return FlatFeature(
        frame=Frame((at[0], at[1], at[2]), flat.axis),
        axis=flat.axis,
        across=flat.across,
        axis_line=flat.axis_line,
        stock_span=flat.stock_span,
        axis_direction=flat.axis_direction,
    )


def _convert_groove(groove: Groove, ctx: ConvContext) -> GrooveFeature:
    at = groove.at
    return GrooveFeature(
        frame=Frame((at[0], at[1], at[2]), groove.axis),
        axis=groove.axis,
        width=groove.width,
        diameter=groove.diameter,
    )


def _convert_rectangular_blind_slot(
    slot: RectangularBlindSlot, ctx: ConvContext
) -> RectangularBlindSlotFeature:
    """Lower the provider's complete, principal-axis blind-slot record without rescanning."""
    if (
        not isinstance(slot.at, tuple)
        or len(slot.at) != 3
        or any(isinstance(value, bool) or not isinstance(value, Real) for value in slot.at)
    ):
        raise ValueError("rectangular blind slot at must be an immutable real-number 3-vector")
    for name in ("width", "length", "depth"):
        value = getattr(slot, name)
        if isinstance(value, bool) or not isinstance(value, Real):
            raise ValueError(f"rectangular blind slot {name} must be a real number")
    return RectangularBlindSlotFeature(
        frame=Frame((slot.at[0], slot.at[1], slot.at[2]), slot.axis),
        axis=slot.axis,
        open_sign=slot.open_sign,
        width_axis=slot.width_axis,
        depth_axis=slot.depth_axis,
        depth_sign=slot.depth_sign,
        width=slot.width,
        length=slot.length,
        depth=slot.depth,
    )


def _convert_round_bottom_blind_slot(
    slot: RoundBottomBlindSlot, ctx: ConvContext
) -> RoundBottomBlindSlotFeature:
    """Lower the provider's complete round-bottom record without rescanning geometry."""
    if (
        not isinstance(slot.at, tuple)
        or len(slot.at) != 3
        or any(isinstance(value, bool) or not isinstance(value, Real) for value in slot.at)
    ):
        raise ValueError("round-bottom blind slot at must be an immutable real-number 3-vector")
    for name in ("length", "radius", "flat_width"):
        value = getattr(slot, name)
        if isinstance(value, bool) or not isinstance(value, Real):
            raise ValueError(f"round-bottom blind slot {name} must be a real number")
    return RoundBottomBlindSlotFeature(
        frame=Frame((slot.at[0], slot.at[1], slot.at[2]), slot.axis),
        axis=slot.axis,
        open_sign=slot.open_sign,
        width_axis=slot.width_axis,
        depth_axis=slot.depth_axis,
        depth_sign=slot.depth_sign,
        length=slot.length,
        radius=slot.radius,
        flat_width=slot.flat_width,
    )


def _convert_oriented_slot(slot: OrientedSlot, ctx: ConvContext) -> OrientedSlotFeature:
    """Lower the complete public free-direction record without topology inference."""

    # Validate the exact released outer and nested schema before reading it.  Completeness
    # uses the same shared boundary, so the adapter cannot accept evidence the observer
    # rejects (ADR 0015 / #1432).
    oriented_slot_provider_key(slot)

    def vec3(value) -> tuple[float, float, float]:
        x, y, z = value
        return (x, y, z)

    def pair2(value) -> tuple[float, float]:
        x, y = value
        return (x, y)

    source = slot.source
    run = vec3(source.frame.run)
    dominant = max(range(3), key=lambda index: abs(run[index]))
    passage = OrientedSlotPassage(
        origin=vec3(source.frame.origin),
        run=run,
        u=vec3(source.frame.u),
        v=vec3(source.frame.v),
        run_interval=pair2(source.run_interval),
        boundary=tuple((pair2(vertex.point), vertex.bulge) for vertex in source.section.boundary),
        low_capped=source.ends.low_capped,
        high_capped=source.ends.high_capped,
        body_key=None if slot.body_key is None else tuple(slot.body_key),
    )
    return OrientedSlotFeature(
        frame=Frame(vec3(slot.center), "xyz"[dominant]),
        width_direction=vec3(slot.width_direction),
        long_direction=vec3(slot.long_direction),
        run_direction=run,
        width=slot.width,
        length=slot.length,
        passage=passage,
    )


# Tier 1 — uniform converters: a pure (record, ctx) -> Feature mapping.
_CONVERTERS: dict[type, Converter] = {
    DoubleDBore: _convert_double_d_bore,
    Channel: _convert_channel,
    Slot: _convert_slot,
    Pocket: _convert_pocket,
    RaisedPad: _convert_pad,
    TurnedStep: _convert_step,
    BossRecord: _convert_boss,
    PolygonalBoss: _convert_polygonal_boss,
    PolygonalStock: _convert_polygonal_stock,
    Plate: _convert_plate,
    Chamfer: _convert_chamfer,
    Fillet: _convert_fillet,
    CircularBlindStep: _convert_circular_blind_step,
    PairedRampStep: _convert_paired_ramp_step,
    ThroughStep: _convert_through_step,
    Flat: _convert_flat,
    Groove: _convert_groove,
    RectangularBlindSlot: _convert_rectangular_blind_slot,
    RoundBottomBlindSlot: _convert_round_bottom_blind_slot,
    OrientedSlot: _convert_oriented_slot,
}

# Tier 2 — derived converters: not a 1:1 record map. A hole callout groups identical
# holes (members + count) and a pattern composes a representative member hole, so their
# converters take per-group extras the orchestration computes — they cannot go through
# the uniform `convert()` dispatcher, but they are still the record type's one converter.
_DERIVED_CONVERTERS: dict[type, Callable[..., Feature]] = {
    HoleRecord: _member_hole,
    BoltCircle: _pattern_feature,
    LinearArray: _pattern_feature,
    RectGrid: _pattern_feature,
    PocketArray: _pocket_pattern_feature,
    PocketGrid: _pocket_pattern_feature,
    SlotArray: _slot_pattern_feature,
    SlotGrid: _slot_pattern_feature,
}

# Tier 3 — orchestrated/evidence records: no per-record converter, by design. Each is a
# nested sub-record, aggregated into a correlated feature, or retained solely as independent
# physical evidence; the reason is the residual scope ADR 0013 Phase 1 explicitly accepts.
_ORCHESTRATED_RECORDS: dict[type, str] = {
    CounterSink: "a nested sub-record of HoleRecord — rides on the hole callout, never a top-level feature",
    FaceLevel: "aggregated into a single StepLevelFeature step ladder (one feature per part, not per level)",
    StepShoulder: "aggregated into StepLevelFeature.shoulders (in-plane step positions, not a standalone feature)",
    RiserEvidence: "pre-projection evidence (#1025) — projected to StepShoulder per consumer, never converted directly",
    RepeatingRadialProfile: (
        "geometry-only critique evidence (#1087) — validates a separately authored gear "
        "declaration and must never become an inferred IR feature"
    ),
}


# Tier 4 — records the installed package proves and this consumer deliberately does NOT convert.
# Not "orchestrated": these are records from unsupported feature families, rather than nested
# sub-records or aggregate substrate. Authoritative outputs remain dimension-relevant physical
# evidence; accepted-only projections are identified individually below. Some dispositions remain
# undecided and others are reviewed unsupported outcomes. Each is declared in
# `recogniser_contract._UNSUPPORTED` against the issue recording that disposition. Kept as its own
# tier so neither unsupported evidence nor its compatibility records disappear into substrate
# merely because neither has an IR converter (#1244).
_UNCONSUMED_RECORDS: dict[type, str] = {
    AngledStep: (
        "an aggregate-reconciled angled blind step whose slanted face has yielded out of "
        "`chamfers`; its available measurements do not choose a truthful general dimension "
        "grammar, so every occurrence has an explicit unsupported completeness outcome (#1247)"
    ),
    Blend: (
        "a public convex-blend-chain record whose precedence and defining-face ownership against "
        "the supported Fillet family remain undecided, so it cannot yet create or suppress a "
        "radius requirement (#1430)"
    ),
    OrientedSlotArray: (
        "a derived free-axis slot array whose member correspondence and vector pattern plane "
        "cannot be represented by SlotPatternFeature; its consumer semantics remain undecided "
        "(#1430)"
    ),
    OrientedSlotGrid: (
        "a derived free-axis slot grid whose member correspondence, vector plane, and lattice "
        "identity cannot be represented by SlotPatternFeature; its consumer semantics remain "
        "undecided (#1430)"
    ),
    Passage: (
        "the accepted-only compatibility projection of authoritative SectionPassage; it is not "
        "a second physical requirement and has no converter (#1245)"
    ),
    SectionPassage: (
        "the authoritative physical prismatic-opening evidence introduced in 0.4.0; its complete "
        "line/arc section has no truthful general drafting grammar, so every occurrence has an "
        "explicit unsupported completeness outcome (#1245)"
    ),
    PrismaticPocket: (
        "an aggregate-reconciled polygonal blind recess not owned by `Pocket`; its arbitrary "
        "section has no truthful general Draftwright dimension grammar, so every occurrence has "
        "an explicit unsupported completeness outcome (#1246)"
    ),
}


def convert(record, ctx: ConvContext) -> Feature:
    """Dispatch a recognition record to its IR :class:`Feature` via the typed registry.

    Fail-closed: a record type with no uniform converter raises, so a new
    recogniser cannot silently emit features the registry never learned to
    convert (the derived/orchestrated tiers are handled inline by
    :func:`build_part_model`, not here)."""
    try:
        conv = _CONVERTERS[type(record)]
    except KeyError:
        raise TypeError(
            f"no IR converter registered for recognition record {type(record).__name__} (#752)"
        ) from None
    return conv(record, ctx)


def _through_step_leg_spans(steps) -> tuple[tuple[str, float, float], ...]:
    """Physical transverse intervals owned by aggregate ThroughStep records."""
    spans = []
    for step in steps:
        axes = tuple(axis for axis in "xyz" if axis != step.axis)
        for start, end in zip(step.section, step.section[1:]):
            changed = next(index for index in (0, 1) if start[index] != end[index])
            lo, hi = sorted((float(start[changed]), float(end[changed])))
            spans.append((axes[changed], lo, hi))
    return tuple(spans)


def _through_step_level_zs(steps) -> tuple[float, ...]:
    """Z transitions whose higher-level owner is an X/Y-run ThroughStep."""
    levels = []
    for step in steps:
        axes = tuple(axis for axis in "xyz" if axis != step.axis)
        if "z" in axes:
            levels.append(float(step.section[1][axes.index("z")]))
    return tuple(levels)


def _through_step_shoulder_sites(steps, bbox) -> tuple[tuple[str, float], ...]:
    """Legacy datum-shoulder sites made redundant by aggregate local-leg ownership."""
    bounds = {
        "x": (float(bbox.min.X), float(bbox.max.X)),
        "y": (float(bbox.min.Y), float(bbox.max.Y)),
    }
    sites = []
    for axis, lo, hi in _through_step_leg_spans(steps):
        if axis not in bounds:
            continue
        bound_lo, bound_hi = bounds[axis]
        if abs(lo - bound_lo) < 0.5:
            sites.append((axis, hi))
        elif abs(hi - bound_hi) < 0.5:
            sites.append((axis, lo))
    return tuple(sites)


def _through_step_legacy_complete(
    step, bbox, step_zs, shoulders, plates, *, envelope_emittable: bool
) -> bool:
    """Whether the established Z-up grammar directly defines both open-section legs.

    A face level or shoulder is measured from the part's minimum datum; only when the envelope
    is itself emittable does that pair also define the complementary maximum-side interval.
    Plate thicknesses are already direct intervals, with the same envelope requirement for a
    complement. This is deliberately coordinate-exact drafting evidence, not an axis-only
    family preference: if either physical leg has no owner the aggregate record must lower
    instead of disappearing from completeness (#1382).
    """
    bounds = {
        "x": (float(bbox.min.X), float(bbox.max.X)),
        "y": (float(bbox.min.Y), float(bbox.max.Y)),
        "z": (float(bbox.min.Z), float(bbox.max.Z)),
    }
    intervals: list[tuple[str, float, float]] = []
    for z in step_zs or ():
        intervals.append(("z", *sorted((bounds["z"][0], z))))
        if envelope_emittable:
            intervals.append(("z", *sorted((z, bounds["z"][1]))))
    for shoulder in shoulders:
        lo, hi = bounds[shoulder.axis]
        intervals.append((shoulder.axis, *sorted((lo, shoulder.position))))
        if envelope_emittable:
            intervals.append((shoulder.axis, *sorted((shoulder.position, hi))))
    for plate in plates or ():
        axis = plate.axis
        plate_lo, plate_hi = sorted((plate.lo, plate.hi))
        intervals.append((axis, plate_lo, plate_hi))
        if envelope_emittable:
            bound_lo, bound_hi = bounds[axis]
            if abs(plate_lo - bound_lo) < 0.5:
                intervals.append((axis, plate_hi, bound_hi))
            if abs(plate_hi - bound_hi) < 0.5:
                intervals.append((axis, bound_lo, plate_lo))

    def _owned(axis: str, lo: float, hi: float) -> bool:
        return any(
            owner_axis == axis and abs(owner_lo - lo) < 0.5 and abs(owner_hi - hi) < 0.5
            for owner_axis, owner_lo, owner_hi in intervals
        )

    return all(_owned(axis, lo, hi) for axis, lo, hi in _through_step_leg_spans((step,)))


def build_part_model(
    part,
    *,
    holes=None,
    double_d_bores=None,
    patterns=None,
    bosses=None,
    polygonal_bosses=None,
    polygonal_stock=None,
    channels=None,
    slots=None,
    slot_patterns=None,
    oriented_slots=None,
    oriented_slot_patterns=None,
    risers=None,
    chamfers=None,
    fillets=None,
    circular_blind_steps=None,
    paired_ramp_steps=None,
    through_steps=None,
    plates=None,
    grooves=None,
    flats=None,
    pockets=None,
    pocket_patterns=None,
    rectangular_blind_slots=None,
    round_bottom_blind_slots=None,
    pads=None,
    prof=_UNSET,
    profiles=_UNSET,
    step_zs=None,
    face_levels=None,
    rotational=None,
    pmi=None,
    lower_pmi: bool = True,
    cyls=None,
) -> PartModel:
    """Run the detectors and assemble the :class:`PartModel` IR for *part*.

    The detected feature sets may be **supplied** by the caller (from `_analyse`,
    which already ran them) so detection happens **once per build** — the single
    feature inventory (ADR 0008 Amendment 5, #244). Omitted sets are detected here,
    so a standalone ``build_part_model(part)`` still works. ``profiles`` is the plural
    body-local turned-profile input; the compatible singular ``prof`` remains accepted,
    and both use a sentinel because ``None`` is a valid non-turned value.

    ``step_zs`` (prismatic horizontal face levels), their optional ``face_levels`` records
    carrying support bounds, and ``rotational`` (``(od, bores)`` or ``None``) are
    *classification* inputs from `_analyse` — feeding the prismatic step ladder (#237/#915)
    and the rotational OD/bore furniture (#237).

    ``cyls`` is a precomputed ``analyse_cylinders(part)`` result threaded into every
    cylinder-substrate recogniser called here (holes/bosses/turned/grooves/flats), so
    the solid is scanned once per build (#703); a standalone/partial call derives it once
    before its aggregate run. ``lower_pmi=False`` retains extracted PMI as materialised/report-only IR;
    annotate mode uses the default and correlates supported requirements onto canonical
    feature parameters (#1116)."""
    fillets_supplied = fillets is not None
    circular_blind_steps_supplied = circular_blind_steps is not None
    derive_hole_patterns = holes is not None and patterns is None
    derive_slot_patterns = slots is not None and slot_patterns is None
    derive_oriented_slot_patterns = oriented_slots is not None and oriented_slot_patterns is None
    derive_pocket_patterns = pockets is not None and pocket_patterns is None
    if prof is not _UNSET and profiles is not _UNSET:
        raise ValueError("supply profiles= or the compatible singular prof=, not both")
    needs_aggregate = (
        (prof is _UNSET and profiles is _UNSET)
        or step_zs is None
        or face_levels is None
        or any(
            inventory is None
            for inventory in (
                holes,
                double_d_bores,
                patterns,
                bosses,
                polygonal_bosses,
                polygonal_stock,
                channels,
                slots,
                slot_patterns,
                oriented_slots,
                oriented_slot_patterns,
                risers,
                chamfers,
                fillets,
                circular_blind_steps,
                paired_ramp_steps,
                through_steps,
                plates,
                grooves,
                flats,
                pockets,
                pocket_patterns,
                rectangular_blind_slots,
                round_bottom_blind_slots,
                pads,
            )
        )
    )
    bbox = part.bounding_box()
    features: list[Feature] = []

    # Turned-profile classification up front so the shared convert-context carries the
    # part's turning axis (the StepFeature span axis). Pure detection — no feature is
    # emitted here; the turned/boss branch below reads the same plural inventory.
    #
    # Any omitted family or classification input is filled from one public RecognitionResult,
    # preserving cross-family ownership for documented partial-inventory calls.  Derive the
    # aggregate's applicability flag from the shared cylinder substrate rather than probing a
    # public family first; the aggregate remains the only family orchestration (ADR 0017).
    if needs_aggregate:
        if cyls is None:
            cyls = analyse_cylinders(part)
        centre = bbox.center()
        cylinder_class = _classify_rotational_cylinders(
            cyls,
            sizes=(bbox.size.X, bbox.size.Y, bbox.size.Z),
            centre=(centre.X, centre.Y, centre.Z),
        )
        recognition = build_raw_recognition_result(
            part,
            cylinders=cyls,
            rotational=(
                rotational is not None
                or (profiles is not _UNSET and bool(profiles))
                or (prof is not _UNSET and prof is not None)
                or cylinder_class.is_rotational
            ),
        )
        # A circular blind step and its legacy fillet projection compete for the same
        # curved wall.  The aggregate resolves that ownership atomically.  A partial caller
        # may still supply either inventory when it agrees with the aggregate (or when no
        # competing aggregate owner exists), but a divergent one-sided override is ambiguous:
        # accepting it could emit two radius requirements or silently emit neither.  Require
        # both inventories for an intentional ownership override and fail closed otherwise.
        if fillets_supplied and not circular_blind_steps_supplied:
            fillets = tuple(fillets)
            aggregate_fillets = tuple(recognition.fillets)
            if recognition.circular_blind_steps and any(
                record not in aggregate_fillets for record in fillets
            ):
                raise ValueError(
                    "fillets and circular_blind_steps must be supplied together when "
                    "overriding aggregate ownership"
                )
        elif circular_blind_steps_supplied and not fillets_supplied:
            circular_blind_steps = tuple(circular_blind_steps)
            if (
                recognition.circular_blind_steps or recognition.fillets
            ) and circular_blind_steps != tuple(recognition.circular_blind_steps):
                raise ValueError(
                    "fillets and circular_blind_steps must be supplied together when "
                    "overriding aggregate ownership"
                )
        holes = recognition.holes if holes is None else holes
        double_d_bores = recognition.double_d_bores if double_d_bores is None else double_d_bores
        patterns = recognition.hole_patterns if patterns is None else patterns
        bosses = recognition.bosses if bosses is None else bosses
        polygonal_bosses = (
            recognition.polygonal_bosses if polygonal_bosses is None else polygonal_bosses
        )
        polygonal_stock = (
            recognition.polygonal_stock if polygonal_stock is None else polygonal_stock
        )
        channels = recognition.channels if channels is None else channels
        slots = recognition.slots if slots is None else slots
        slot_patterns = recognition.slot_patterns if slot_patterns is None else slot_patterns
        oriented_slots = recognition.oriented_slots if oriented_slots is None else oriented_slots
        oriented_slot_patterns = (
            recognition.oriented_slot_patterns
            if oriented_slot_patterns is None
            else oriented_slot_patterns
        )
        risers = recognition.risers if risers is None else risers
        chamfers = recognition.chamfers if chamfers is None else chamfers
        fillets = recognition.fillets if fillets is None else fillets
        circular_blind_steps = (
            recognition.circular_blind_steps
            if circular_blind_steps is None
            else circular_blind_steps
        )
        paired_ramp_steps = (
            recognition.paired_ramp_steps if paired_ramp_steps is None else paired_ramp_steps
        )
        through_steps = recognition.through_steps if through_steps is None else through_steps
        plates = recognition.plates if plates is None else plates
        grooves = recognition.grooves if grooves is None else grooves
        flats = recognition.flats if flats is None else flats
        pockets = recognition.pockets if pockets is None else pockets
        pocket_patterns = (
            recognition.pocket_patterns if pocket_patterns is None else pocket_patterns
        )
        rectangular_blind_slots = (
            recognition.rectangular_blind_slots
            if rectangular_blind_slots is None
            else rectangular_blind_slots
        )
        round_bottom_blind_slots = (
            recognition.round_bottom_blind_slots
            if round_bottom_blind_slots is None
            else round_bottom_blind_slots
        )
        pads = recognition.pads if pads is None else pads
        # Pattern inventories are projections of their supplied member inventories.  Preserve
        # that documented partial-input relationship instead of combining caller-owned members
        # with patterns derived from the aggregate's separately detected members.
        if derive_hole_patterns:
            patterns = recognise_hole_patterns(holes)
        if derive_slot_patterns:
            slot_patterns = recognise_slot_patterns(slots)
        if derive_oriented_slot_patterns:
            oriented_slot_patterns = recognise_oriented_slot_patterns(oriented_slots)
        if derive_pocket_patterns:
            pocket_patterns = recognise_pocket_patterns(pockets)
        if prof is _UNSET and profiles is _UNSET:
            profiles = recognition.turned_profiles
        if step_zs is None:
            step_zs = recognition.step_ladder_for_z_span(bbox.min.Z, bbox.max.Z)
        if face_levels is None:
            face_levels = recognition.step_levels
        cyls = recognition.cylinders
    if profiles is _UNSET:
        assert prof is not _UNSET  # both omitted always took and populated the aggregate arm
        profiles = () if prof is None else (prof,)
    profiles = () if profiles is None else tuple(profiles)
    if len(profiles) > 1 and any(profile.profile is None for profile in profiles):
        raise ValueError("plural turned profiles require body-local profile identity")
    # ``rotational`` is the classification fallback for a single-diameter turned body whose
    # step profile is absent. It is already supplied by the one analysis orchestration, so
    # carrying its axis into conversion is not a geometry rescan (#1276 / ADR 0017).
    profile_axes = {profile.axis for profile in profiles}
    if len(profile_axes) == 1:
        orientation = next(iter(profile_axes))
    elif profile_axes:
        orientation = None
    else:
        orientation = rotational[2] if rotational else None
    # Standalone detection applies the same surface-family gate as RecognitionResult. Supplied
    # records carry the recogniser's explicit surface-family discriminator themselves.
    if chamfers is None:
        chamfers = recognise_chamfers(
            part,
            cyls=cyls,
            include_planar=orientation is None,
        )
    if fillets is None:
        fillets = recognise_fillets(
            part,
            cyls=cyls,
            include_cylindrical=orientation is None,
        )
    ctx = ConvContext(bbox=bbox, orientation=orientation)
    # A legacy min-datum measurement proves its complementary max-side interval only together
    # with the overall envelope. Establish whether that feature will really cross the IR waist
    # before aggregate ownership is decided; the bbox alone is private geometry, not ink.
    if bosses is None:
        bosses = recognise_bosses(part, cyls=cyls)
    bosses_d = _distinct_by_diameter(bosses)
    if polygonal_stock is None:
        polygonal_stock = recognise_polygonal_stock(part)
    envelope_emittable = bool(
        not profiles and not _is_round(bbox, bosses_d) and not polygonal_stock
    )
    if through_steps is None:
        # Match RecognitionResult applicability on the standalone path. A supplied aggregate
        # inventory already embodies that one orchestration decision and is never re-filtered.
        through_steps = recognise_through_steps(part) if orientation is None else ()
    through_steps = tuple(through_steps)

    if channels is None:
        channels = recognise_channels(part)
    # A full-span floored gap also describes a monolithic centred rebate, whose two
    # shoulders are already owned as one correlated StepLevelFeature position set. The
    # #917 channel scheme applies only where plate recognition proves a multi-axis
    # U-bracket: base + walls. Use the same evidence as the plate-emission gate below so
    # the two domains cannot both dimension one profile.
    if not profiles and rotational is None and plates is None:
        plates = recognise_plates(part)
    multi_plate = has_multi_axis_plates(plates or ())
    # Ownership evidence must be eligible to cross the recognition→IR boundary.  A lone
    # single-axis plate is deliberately not emitted below (it is staircase evidence, not the
    # multi-plate bracket grammar), so letting it preempt a ThroughStep would leave the claimed
    # leg with no IR owner at all.
    ownership_plates = (
        tuple(plates or ()) if not profiles and rotational is None and multi_plate else ()
    )
    # Pocket recognition is consumed later, but its edge-open floors participate in the
    # step-level emission gate. Detect once up front so aggregate takeover is decided against
    # the same final legacy inventory that will actually cross the IR boundary.
    if pockets is None:
        pockets = recognise_pockets(part)
    edge_floor_zs = {
        pk.d_lo if pk.open_sign > 0 else pk.d_hi
        for pk in pockets
        if pk.depth_axis == "z" and pk.edge_anchored
    }
    plate_zs_at_base = {
        round(pl.hi, 3)
        for pl in ownership_plates
        if pl.axis == "z" and abs(pl.lo - bbox.min.Z) < 0.5
    }
    if risers is None:
        risers = recognise_risers(part)
    # RaisedPad v2 is an all-principal-axis occurrence. Resolve it before lowering the legacy
    # Z-level grammar so a side-normal pad's two Z footprint edges cannot masquerade as
    # prismatic HEIGHT levels. Any omitted inventory was filled from the one aggregate above;
    # do not add a family rescan at this consumer boundary (ADR 0017).
    assert pads is not None
    pads = tuple(pads)
    # The detected orchestration supplies its filtered aggregate levels. A standalone
    # ``build_part_model`` has no aggregate, so obtain the same records once and use them for
    # BOTH ownership and emitted IR.  Suppressing a ThroughStep because a legacy owner exists
    # only as private evidence would return a model containing neither owner.
    if step_zs is None:
        standalone_face_levels = tuple(step_level_records(part))
        step_zs = tuple(level.z for level in standalone_face_levels)
        if face_levels is None:
            face_levels = standalone_face_levels
    face_levels = tuple(face_levels or ())

    def _side_pad_owns_level(level: FaceLevel, pad: RaisedPad) -> bool:
        if pad.axis == "z" or level.x_span is None or level.y_span is None:
            return False
        return (
            any(abs(level.z - bound) < 0.5 for bound in (pad.z0, pad.z1))
            and all(
                abs(actual - expected) < 0.5
                for actual, expected in zip(level.x_span, (pad.x0, pad.x1), strict=True)
            )
            and all(
                abs(actual - expected) < 0.5
                for actual, expected in zip(level.y_span, (pad.y0, pad.y1), strict=True)
            )
        )

    # Remove a Z level only when every physical support record at that ordinate belongs to a
    # side-normal pad. A genuine independent stair sharing the same Z remains an owner.
    side_pad_level_zs = {
        level.z
        for level in face_levels
        if any(_side_pad_owns_level(level, pad) for pad in pads)
        and not any(
            other.z == level.z and not any(_side_pad_owns_level(other, pad) for pad in pads)
            for other in face_levels
        )
    }
    ownership_step_zs = (
        tuple(
            z
            for z in step_zs
            if round(z, 3) not in plate_zs_at_base
            and not any(abs(z - owned) < 0.5 for owned in side_pad_level_zs)
            and not any(abs(z - floor) < 0.5 for floor in edge_floor_zs)
        )
        if not profiles
        else ()
    )
    shoulders = project_step_shoulders(risers, levels=list(ownership_step_zs))
    # Z-run records are the native through-step projection. X/Y-run records remain with the
    # established Z-up grammar only when that grammar proves BOTH exact physical legs; a
    # partial legacy projection is replaced by the complete aggregate owner.
    lowered_through_steps = tuple(
        step
        for step in through_steps
        if step.axis == "z"
        or not _through_step_legacy_complete(
            step,
            bbox,
            ownership_step_zs,
            shoulders,
            ownership_plates,
            envelope_emittable=envelope_emittable,
        )
    )
    # Ownership is a fixed point, not a per-record vote over the unfiltered inventory. One
    # aggregate occurrence can remove a globally shared legacy level/shoulder/plate that a
    # sibling occurrence initially relied on. Promote every newly uncovered sibling and repeat
    # until the surviving legacy grammar still proves both legs for every preempted record.
    while True:
        owned_spans = _through_step_leg_spans(lowered_through_steps)
        owned_levels = _through_step_level_zs(lowered_through_steps)
        owned_shoulders = _through_step_shoulder_sites(lowered_through_steps, bbox)
        remaining_levels = tuple(
            z for z in ownership_step_zs if not any(abs(z - owned) < 0.5 for owned in owned_levels)
        )
        # Re-project after removing aggregate-owned levels. A riser is a shoulder only while
        # its foot remains in the emitted level set; filtering the original shoulders by site
        # alone could preserve an owner that the final StepLevelFeature never receives.
        remaining_shoulders = tuple(
            shoulder
            for shoulder in project_step_shoulders(risers, levels=list(remaining_levels))
            if not any(
                shoulder.axis == axis and abs(shoulder.position - position) < 0.5
                for axis, position in owned_shoulders
            )
        )
        remaining_plates = tuple(
            plate
            for plate in ownership_plates
            if not any(
                plate.axis == axis and abs(plate.lo - lo) <= 1e-6 and abs(plate.hi - hi) <= 1e-6
                for axis, lo, hi in owned_spans
            )
        )
        promoted = tuple(
            step
            for step in through_steps
            if step not in lowered_through_steps
            and not _through_step_legacy_complete(
                step,
                bbox,
                remaining_levels,
                remaining_shoulders,
                remaining_plates,
                envelope_emittable=envelope_emittable,
            )
        )
        if not promoted:
            break
        lowered_through_steps += promoted
    through_leg_spans = _through_step_leg_spans(lowered_through_steps)
    through_level_zs = _through_step_level_zs(lowered_through_steps)
    through_shoulder_sites = _through_step_shoulder_sites(lowered_through_steps, bbox)
    if not profiles and rotational is None and multi_plate:
        features.extend(convert(channel, ctx) for channel in channels)

    # Holes and hole patterns. A recognised pattern becomes one PatternFeature
    # (count× member-diameter + pattern dims); its member holes are NOT also
    # emitted individually — the grouped-callout rule the engine uses.
    if holes is None:
        holes = recognise_holes(part, cyls=cyls, csinks=recognise_countersinks(part))
    if patterns is None:
        patterns = recognise_hole_patterns(holes)
    patterned: set[int] = set()
    for pat in patterns:
        members = list(pat.holes)
        if not _is_principal_axis(members[0].axis):
            # An OBLIQUE pattern plane has no faithful `PatternFeature`: `Frame.axis` is a
            # LETTER, so declaration lays the lattice out in that letter's canonical plane and
            # a 40 mm Z spread comes back as 0 — a silently wrong drawing (#971).
            #
            # Refused HERE, at the recognition→IR adapter, not in the recogniser: ADR 0013 says
            # a recogniser reports the geometry it finds, and `recognise_hole_patterns` finds
            # this one correctly. The limitation is draftwright's IR, so it belongs on
            # draftwright's side of the boundary — which also covers an injected `patterns=`.
            #
            # The members simply stay unpatterned below, so they are still drawn, dimensioned
            # and located. Carrying a full normal on `Frame` would be faithful but widens the
            # ADR 0015 waist; that option stays recorded on #971.
            continue
        patterned.update(id(h) for h in members)
        features.append(_pattern_feature(pat, members))
    # Un-patterned holes: group by machining spec so identical holes share one
    # count× callout (the engine's grouped-callout rule); HoleSpec keys on the
    # snapped axis and the countersink too, so opposite-face drillings and csk-vs-plain
    # holes stay distinct.
    spec_groups: dict = {}
    for h in holes:
        if id(h) in patterned:
            continue
        spec_groups.setdefault(HoleSpec.from_hole(h), []).append(h)
    for grp in spec_groups.values():
        rep = grp[0]
        frame = Frame(origin=_xyz(rep.location), axis=_axis_letter(rep))
        mem_locs = tuple(_xyz(h.location) for h in grp)
        features.append(_member_hole(rep, frame, members=mem_locs, count=len(grp)))

    # Profiled bores are their own recognition family because full-cylinder recognition
    # cannot see their partial cylindrical faces. They still lower to HoleFeature so the
    # established hole location, GD&T, placement and edit paths remain one implementation.
    if double_d_bores is None:
        double_d_bores = recognise_double_d_bores(part)
    features.extend(convert(bore, ctx) for bore in double_d_bores)

    # Milled slots / reduced across-flats sections (detected for any part). A recognised array
    # of identical slots becomes ONE SlotPatternFeature (count× SLOT W×L + pitch, #841); its
    # member slots are NOT also emitted individually — the same grouped-callout rule as pockets
    # below (member exclusion by VALUE-set, robust to injected value-copy inventories).
    if slots is None:
        slots = recognise_slots(part)
    if slot_patterns is None:
        slot_patterns = recognise_slot_patterns(slots)
    patterned_sl: set = set()
    for pat in slot_patterns:
        patterned_sl.update(pat.slots)
        features.append(_slot_pattern_feature(pat, list(pat.slots)))
    for sl in slots:
        if sl in patterned_sl:
            continue
        features.append(convert(sl, ctx))

    # Free-direction through slots have a dedicated IR contract. Pattern members remain owned
    # by the separately deferred pattern inventory, so they cannot expand into competing lone
    # callouts while that grouping grammar is still under review (#1432).
    # Both inventories are guaranteed above: either caller-supplied or projected from the one
    # aggregate. Do not retain a fallback rescan here — ADR 0017 gives recognition one owner.
    assert oriented_slots is not None
    assert oriented_slot_patterns is not None
    standalone_oriented = list(oriented_slots)
    ownership_valid = True
    try:
        for pattern in oriented_slot_patterns:
            pattern_members = validate_oriented_slot_pattern(pattern)
            for _member, member_key in pattern_members:
                at = next(
                    index
                    for index, candidate in enumerate(standalone_oriented)
                    if oriented_slot_provider_key(candidate) == member_key
                )
                standalone_oriented.pop(at)
    except (AttributeError, OverflowError, StopIteration, TypeError, ValueError):
        ownership_valid = False
    if not ownership_valid:
        standalone_oriented = list(oriented_slots)
    for oriented_slot in standalone_oriented:
        features.append(convert(oriented_slot, ctx))

    # Capped, edge-open rectangular U-section slots (#1421). The aggregate has already
    # reconciled their topology against ordinary through slots, pockets, channels and passage
    # evidence. Consume that exact inventory as a dedicated semantic feature; do not rescan or
    # coerce it into any of those grammars.
    for blind_slot in rectangular_blind_slots:
        features.append(convert(blind_slot, ctx))

    # Capped, edge-open U-section slots with a straight floor joined by equal round sides.
    # This released aggregate inventory owns the physical family; ordinary and rectangular
    # slots, pockets, channels and passages must not regain ownership downstream.
    for blind_slot in round_bottom_blind_slots:
        features.append(convert(blind_slot, ctx))

    # Blind rectangular recesses — floored slots/pockets (#148a). A recognised array of
    # identical pockets becomes ONE PocketPatternFeature (count× W×L×D + pitch, #841); its
    # member pockets are NOT also emitted individually — the same grouped-callout rule as
    # hole patterns above (member exclusion by id()).
    if pocket_patterns is None:
        pocket_patterns = recognise_pocket_patterns(pockets)
    # Exclude members by VALUE, not id(): `Pocket` is a frozen (hashable) value record and two
    # distinct pockets can never be value-equal (their positions differ), so a value-set
    # excludes members even when `pocket_patterns=` is INJECTED from value-equal copies whose
    # ids differ from `pockets` (Codex #849) — where an id-set would emit both the pattern and
    # the individual pockets, restoring the competing dims this grouping removes.
    patterned_pk: set = set()
    for pat in pocket_patterns:
        patterned_pk.update(pat.pockets)
        features.append(_pocket_pattern_feature(pat, list(pat.pockets)))
    for pk in pockets:
        if pk in patterned_pk:
            continue
        features.append(convert(pk, ctx))

    # Bounded rectangular raised pads: footprint sizing, attachment-axis height, and
    # two in-plane locations. A Z attachment level may also enter the general profile
    # ladder, but that datum-to-level fact does not replace the pad's local rise.
    features.extend(convert(pad, ctx) for pad in pads)

    # Bounded regular polygonal bosses own an across-flats callout and their direct axial
    # height. They are distinct from circular bosses (diameter semantics) and rectangular
    # pads (two orthogonal footprint sizes).
    if polygonal_bosses is None:
        polygonal_bosses = recognise_polygonal_bosses(part)
    features.extend(convert(boss, ctx) for boss in polygonal_bosses)

    # A whole regular polygonal prism is stock, not a boss: it owns the form/A-F
    # definition and its axial stock length independently of attachment evidence.
    features.extend(convert(stock, ctx) for stock in polygonal_stock)

    # Turned / circlip grooves (#148c) — recognised up front so the turned-step chain can
    # exclude any band a groove already dimensions: a groove floor is an annular band, and
    # its two walls read as shoulders, so recognise_turned_steps also delimits it as a
    # middle "step". Emitting both a StepFeature and a GrooveFeature for one band would
    # double-dimension the floor ø (ISO 129) and break ADR 0008's one-band-one-owner waist.
    if grooves is None:
        grooves = recognise_grooves(part, cyls=cyls)

    # Body-local turned profiles → step segments; else external bosses → diameters. Profile
    # identity owns the axis line, so parallel shafts never inherit the part bbox centre or
    # each other's groove bands (#1357).
    if profiles:
        grooves_by_profile: dict[int, list[Groove]] = {id(profile): [] for profile in profiles}
        for groove in grooves:
            owners = require_unambiguous_groove_owner(groove, profiles)
            if owners:
                grooves_by_profile[id(owners[0])].append(groove)
        for profile in profiles:
            for s in profile.steps:
                # Skip the band a groove owns (its callout dimensions width + floor ø). Match
                # on axial POSITION, not diameter: a narrow groove's step is reported at the
                # WALL OD (local_od's pad engulfs both walls when the groove is < ~1.4 mm), so
                # a floor-ø match would silently miss the common circlip case. The groove centre
                # lies within its own step span; the short-length guard keeps a merged shaft run
                # from matching.
                if any(
                    groove_owns_turned_step_band(groove, s)
                    for groove in grooves_by_profile[id(profile)]
                ):
                    continue
                features.append(convert(s, ctx))
        # A narrow external band nested under / beside a larger OD reads as that OD in
        # local_od's max(), so it never becomes a step diameter and goes silently
        # undimensioned (#298). Emit each band the silhouette steps miss as a boss, so
        # render_diameters still gives it a ø callout — aligning the callout inventory
        # with the feature_diameters inventory the coverage lint checks against. A groove
        # floor is likewise a narrow reduced band, but the groove callout already carries its
        # ø, so it is suppressed here (_boss_is_groove_floor) to avoid a duplicate boss ø.
        for b in bosses:
            axis = _axis_letter(b)
            axis_index = "xyz".index(axis)
            b_lo, b_hi = sorted(
                (
                    float(b.location[axis_index]),
                    float(b.location[axis_index] - b.axis[axis_index] * b.height),
                )
            )
            owned = any(
                profile.axis == axis
                and (
                    profile.profile is None
                    or all(
                        abs(float(b.location[index]) - profile.profile.axis_origin[index]) <= 0.5
                        for index in range(3)
                        if index != axis_index
                    )
                )
                and any(
                    abs(b.diameter - step.diameter) <= _DIA_TOL
                    and abs(b_lo - step.lo) <= 0.5
                    and abs(b_hi - step.hi) <= 0.5
                    for step in profile.steps
                )
                for profile in profiles
            )
            if not owned and not _boss_is_groove_floor(b, grooves):
                features.append(convert(b, ctx))
    else:
        for b in bosses_d:
            # A grooved round body can still fail the turned-step squareness gate (e.g. a
            # shaft with a rectangular flange) and land here with prof=None. Suppress the
            # groove-floor boss so its ø is not dimensioned twice — boss ø + groove callout
            # (#148c 3rd-pass review).
            if _boss_is_groove_floor(b, grooves):
                continue
            features.append(convert(b, ctx))
        # Overall envelope dims for a *prismatic* part — not a round single-OD body
        # (a boss whose diameter fills the footprint is the body, dimensioned by its
        # OD, not a box).
        if not _is_round(bbox, bosses_d) and not polygonal_stock:
            # The same construction the declared verb and the emitter's synthesis use.
            # Detection was the REFERENCE the other two were fixed to match (#977/#976); with
            # three independent producers, "matches the detector" was a property to re-verify
            # rather than one the code held. Now there is one spelling.
            from draftwright.model.declare import _envelope_from_bbox

            features.append(_envelope_from_bbox(bbox))

    # Plate/wall thicknesses on a multi-plate prismatic (#559) — the thin extent of a
    # slab that no other prismatic dim recovers (a wall along X/Y, or a Z base plate too
    # thin for the step-ladder legibility gate). Skipped for turned/rotational parts,
    # whose extents are the OD/length chain, not plate thicknesses.
    #
    # Scope guard: only a GENUINE multi-plate part — slabs on ≥2 distinct axes (a base +
    # an upright wall, i.e. an L/T/U bracket) — is dimensioned this way. A single-axis
    # stack (a base slab under a smaller stacked block) is a *staircase*, owned by the
    # step-height ladder; treating its base as a "plate" would wrongly suppress the step
    # dim (#559 review). This keeps the plate feature to the issue's stated domain.
    if not profiles and rotational is None:
        plates = recognise_plates(part) if plates is None else plates
        if multi_plate:
            for pl in plates:
                if any(
                    pl.axis == axis and abs(pl.lo - lo) <= 1e-6 and abs(pl.hi - hi) <= 1e-6
                    for axis, lo, hi in through_leg_spans
                ):
                    # The aggregate open section is the higher-level owner of this exact
                    # thickness interval. Keeping the plate too prints one physical leg twice.
                    continue
                features.append(convert(pl, ctx))

    # Prismatic step-height ladder — horizontal face levels on a NON-turned part
    # (a turned part's steps are StepFeatures, dimensioned by the IR length chain).
    if not profiles and step_zs:
        c = bbox.center()
        # FaceLevel v2 is an occurrence roster: disjoint bodies may establish the same scalar
        # Z height independently. The current StepLevelFeature is the global height-requirement
        # projection and requires unique rungs, so equal values become one dimension while the
        # aggregate retains every body-local occurrence for independent completeness (#1357).
        _levels = tuple(
            sorted(
                {
                    z
                    for z in step_zs
                    if round(z, 3) not in plate_zs_at_base
                    and not any(abs(z - owned) < 0.5 for owned in side_pad_level_zs)
                    and not any(abs(z - owned) < 0.5 for owned in through_level_zs)
                }
            )
        )
        if _levels:
            # An edge-open blind interruption owns its floor through the pocket
            # depth callout; it is not a global profile level. Interior pockets
            # retain the established level IR for compatibility.
            _levels = tuple(
                z for z in _levels if not any(abs(z - floor) < 0.5 for floor in edge_floor_zs)
            )
        if _levels:
            support_by_level = {
                level.z: LevelSupport(level.z, level.x_span, level.y_span)
                for level in (face_levels or ())
                if level.x_span is not None and level.y_span is not None
            }
            _level_supports = tuple(support_by_level[z] for z in _levels if z in support_by_level)
            # Every profile transition needs an in-plane station. Heights alone do
            # not reconstruct a multi-level staircase or a slanted run (#897).
            # Projected over the run's riser evidence, not a fresh scan (#1025). `_levels`
            # is the OWNERSHIP-FILTERED set — plate and pocket floors removed — which is a
            # model decision and stays here; the evidence underneath is shared with critique,
            # which projects the same risers over its own unfiltered levels.
            _shoulders = tuple(
                (s.axis, s.position)
                for s in project_step_shoulders(
                    risers,
                    levels=list(_levels),
                )
                if not any(
                    s.axis == owner_axis and abs(s.position - owner_position) < 0.5
                    for owner_axis, owner_position in through_shoulder_sites
                )
            )
            features.append(
                StepLevelFeature(
                    frame=Frame((c.X, c.Y, bbox.min.Z), "z"),
                    base=bbox.min.Z,
                    levels=_levels,
                    shoulders=_shoulders,
                    datum=(bbox.min.X, bbox.min.Y, bbox.min.Z),
                    level_supports=_level_supports,
                )
            )

    # Chamfers (#560/#1254) — called out C{leg} / {leg}×{angle}°. The package recognises
    # both oblique planar and conical turned forms; both lower through the same converter and
    # IR. An injected aggregate inventory is consumed directly, without a sibling rescan.
    for ch in chamfers:
        features.append(convert(ch, ctx))

    # Fillets (#561/#1281) — called out R{radius} (grouped n× at render). The package
    # recognises both cylindrical prismatic blends and toroidal turned rounds; both lower
    # through the same converter and IR. An injected aggregate inventory is consumed directly,
    # without a sibling rescan.
    for fl in fillets:
        features.append(convert(fl, ctx))

    # Quarter-cylindrical corner cuts with one blind terminal (#1382). The aggregate
    # supplies the oriented centreline and transverse quarter arc, so radius and depth
    # lower without topology access or a sibling scan.
    for circular_step in circular_blind_steps:
        features.append(convert(circular_step, ctx))

    # Mirror-symmetric paired-ramp steps (#1382) — the aggregate proves two equal acute
    # cross-section angles and one open-to-terminal run.  Consume the supplied aggregate
    # inventory directly; standalone model detection invokes the same public family once.
    if paired_ramp_steps is None:
        # Match RecognitionResult applicability on the standalone path. A supplied aggregate
        # inventory already embodies that one orchestration decision and is never re-filtered.
        paired_ramp_steps = recognise_paired_ramp_steps(part) if orientation is None else ()
    for ramp in paired_ramp_steps:
        features.append(convert(ramp, ctx))

    # Rectangular open-profile through steps (#1382).  The aggregate record owns the exact
    # run/anchor/section correspondence; Draftwright lowers its two transverse section legs
    # without rescanning the body or inventing a third through-length requirement.
    #
    # Aggregate ownership precedes its lower-level face-level/riser/plate fragments above: the
    # exact matching transition/thickness is removed from those legacy projections so the local
    # two-leg grammar reaches the sheet once, on every principal run axis.
    for through in lowered_through_steps:
        features.append(convert(through, ctx))

    # Machined flats on round stock (#148b) — a planar face truncating a cylinder,
    # called out by its across-flats size. Detected UNCONDITIONALLY (not gated by the
    # rotational branch): a D-shaft / hex head IS round stock and classifies rotational,
    # yet its flat still needs a callout. The recogniser self-gates on OD adjacency, so a
    # part with no round stock yields none.
    for flat in recognise_flats(part, cyls=cyls) if flats is None else flats:
        features.append(convert(flat, ctx))

    # Turned / circlip grooves on round stock (#148c) — an annular channel (a strict
    # local-minimum OD band) dimensioned by width + floor diameter, recognised above so the
    # turned-step chain can exclude the coincident band. Also UNCONDITIONAL: a grooved shaft
    # is round stock and classifies rotational, yet the groove still needs its own callout.
    # The recogniser self-gates on external OD bands, so a prismatic part yields none.
    for groove in grooves:
        features.append(convert(groove, ctx))

    # Rotational furniture — OD + centrelines + concentric bore leaders (#237). Its
    # presence marks the part rotational; emitted from the classification (od, bores).
    if rotational is not None:
        od, bores, rot_axis = rotational
        c = bbox.center()
        features.append(
            RotationalFeature(frame=Frame((c.X, c.Y, c.Z), rot_axis), od=od, bores=tuple(bores))
        )

    # STEP AP242 PMI — re-homed into drafting-concept IR where possible (#208).
    # Rendered directly by render_pmi; the planner adds nothing.
    features.extend(build_pmi_features(pmi, bbox))

    # The default location datum — the part's min-X/min-Y/min-Z corner (lower-left
    # in the plan view), per inspection practice. Hole location dims measure from
    # it (#238); a human/LLM pass can re-anchor.
    datums = [Datum(id="datum_xy", kind="point", at=(bbox.min.X, bbox.min.Y, bbox.min.Z))]
    model = PartModel(bbox=bbox, orientation=orientation, features=features, datums=datums)
    # Correlation runs after the complete geometry + PMI inventories exist, at the shared IR
    # waist.  Direct drawings and emitted scripts therefore consume the same lowered model
    # instead of the emitter inventing a second ownership decision (#1116).
    from draftwright.model.pmi_lowering import lower_ap242_dimensions

    return lower_ap242_dimensions(model) if lower_pmi else model


def _is_round(bbox, bosses, tol: float = 0.5) -> bool:
    """True when a boss's OD fills the part footprint — a round body of revolution,
    dimensioned by its OD rather than a width×depth box."""
    return any(
        abs(b.diameter - bbox.size.X) <= tol and abs(b.diameter - bbox.size.Y) <= tol
        for b in bosses
    )
