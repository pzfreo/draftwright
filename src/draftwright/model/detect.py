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

from collections import Counter
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, replace
from math import isfinite, ulp
from numbers import Real
from typing import Any, Literal

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
    RecognitionResult,
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
from b123d_recognisers.evidence import RecognitionEvidence, build_recognition_evidence

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
    BlendFeature,
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
from draftwright.plate_correspondence import plate_owner_dependencies
from draftwright.recognition_frame import (
    groove_owns_turned_step_band,
    require_unambiguous_groove_owner,
)
from draftwright.recognition_ownership import RecognitionOwnershipBuilder


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


def _groups_by_diameter(bosses, tol: float = 0.15):
    """Group bosses exactly as the existing diameter representative projection does."""
    out: dict[float, list[object]] = {}
    for b in bosses:
        key = next((k for k in out if abs(k - b.diameter) <= tol), b.diameter)
        out.setdefault(key, []).append(b)
    return list(out.values())


def _boss_is_groove_floor(b, grooves) -> bool:
    """A recognised boss coinciding with a groove floor — same turning axis and (floor) ø — is
    that floor. The groove callout already dimensions it, so it must not also get a boss ø
    (#148c review; applies whether or not the part read as a turned profile)."""
    return bool(_boss_groove_floor_candidates(b, grooves))


def _boss_groove_floor_candidates(b, grooves):
    """Exact groove records satisfying the established boss-floor consumer predicate."""
    ax = _axis_letter(b)
    axis_index = "xyz".index(ax)
    return tuple(
        g
        for g in grooves
        if abs(b.diameter - g.diameter) <= _DIA_TOL
        and g.axis == ax
        and all(
            abs(float(b.location[index]) - float(g.at[index])) <= 0.5
            for index in range(3)
            if index != axis_index
        )
    )


_DIA_TOL = 0.15  # two ø values within this (mm) are the same diameter (#298)
_UNSET = object()  # sentinel: distinguishes "not supplied" from a valid prof=None


@dataclass(frozen=True)
class _RecognitionHandoff:
    """One internal, task-local binding between a part and its completed aggregate."""

    part: object
    result: RecognitionResult
    evidence: RecognitionEvidence | None = None
    ownership: RecognitionOwnershipBuilder | None = None


_RECOGNITION_HANDOFF: ContextVar[_RecognitionHandoff | None] = ContextVar(
    "draftwright_recognition_handoff", default=None
)


def _build_part_model_from_recognition(
    part,
    recognition_result: RecognitionResult,
    *,
    evidence: RecognitionEvidence | None = None,
    ownership: RecognitionOwnershipBuilder | None = None,
    **kwargs,
) -> PartModel:
    """Internal detected-path entry that binds aggregate provenance to its source part."""
    if type(recognition_result) is not RecognitionResult:
        raise TypeError("recognition_result must be an exact RecognitionResult")
    if ownership is not None and ownership.result is not recognition_result:
        raise ValueError("recognition ownership and result must come from the same run")
    if evidence is None and ownership is not None:
        evidence = ownership.evidence
    if evidence is not None and evidence.result is not recognition_result:
        raise ValueError("recognition evidence and result must come from the same run")
    token = _RECOGNITION_HANDOFF.set(
        _RecognitionHandoff(part, recognition_result, evidence, ownership)
    )
    try:
        return build_part_model(part, **kwargs)
    finally:
        _RECOGNITION_HANDOFF.reset(token)


def _fillet_ownership_key(record) -> tuple:
    """Strict primitive-only key for aggregate Fillet/Blend partition comparisons."""
    if type(record) is not Fillet:
        raise TypeError("fillet inventory members must be exact Fillet records")
    if type(record.axis) is not str or record.axis not in ("x", "y", "z"):
        raise ValueError("fillet axis must be exactly 'x', 'y', or 'z'")
    if type(record.turned) is not bool:
        raise ValueError("fillet turned must be an exact bool")
    if type(record.radius) not in (int, float):
        raise ValueError("fillet radius must be an exact non-boolean int or float")
    if type(record.at) is not tuple or len(record.at) != 3:
        raise ValueError("fillet at must be an immutable 3-vector")
    if any(type(component) not in (int, float) for component in record.at):
        raise ValueError("fillet at components must be exact non-boolean ints or floats")
    try:
        radius = float(record.radius)
        at = tuple(float(component) for component in record.at)
    except (OverflowError, ValueError) as exc:
        raise ValueError("fillet radius and at must be finite") from exc
    if radius <= 0.0 or not isfinite(radius) or not all(isfinite(component) for component in at):
        raise ValueError("fillet radius and at must be finite, with a positive radius")
    return record.axis, radius, at, record.turned


def _fillet_blend_ownership_keys(fillets, blends) -> tuple[tuple, tuple]:
    """Validate both public inventories before comparing only built-in primitive values."""
    from draftwright.blend_contract import blend_provider_key

    return (
        tuple(_fillet_ownership_key(record) for record in fillets),
        tuple(blend_provider_key(record) for record in blends),
    )


def _preserves_ownership_with_unique_additions(
    supplied_keys: tuple, aggregate_keys: tuple
) -> bool:
    """Keep every aggregate occurrence without cloning an existing or added owner."""
    supplied_counts = Counter(supplied_keys)
    aggregate_counts = Counter(aggregate_keys)
    if not (aggregate_counts <= supplied_counts):
        return False
    additions = supplied_counts - aggregate_counts
    return all(count == 1 and key not in aggregate_counts for key, count in additions.items())


def _same_ownership_occurrences(supplied_keys: tuple, aggregate_keys: tuple) -> bool:
    """Compare order-independent occurrence inventories while retaining multiplicity."""
    return Counter(supplied_keys) == Counter(aggregate_keys)


def _same_fillet_blend_partition(
    supplied: tuple[tuple, tuple], aggregate: tuple[tuple, tuple]
) -> bool:
    """Compare both aggregate sibling inventories as occurrence multisets."""
    return all(
        _same_ownership_occurrences(supplied_family, aggregate_family)
        for supplied_family, aggregate_family in zip(supplied, aggregate, strict=True)
    )


def _circular_blind_step_ownership_key(record) -> tuple:
    """Strict canonical key for circular-step ownership and multiplicity comparisons."""
    if type(record) is not CircularBlindStep:
        raise TypeError(
            "circular_blind_steps inventory members must be exact CircularBlindStep records"
        )
    feature = circular_blind_step(
        axis=record.axis,
        radius=record.radius,
        length=record.length,
        centreline=record.centreline,
        section=record.section,
    )
    return (
        feature.axis,
        feature.radius,
        feature.length,
        feature.centreline,
        feature.section,
    )


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


_CHANNEL_PUBLICATION_HALF_CELL = 0.005
_CHANNEL_DERIVED_SHOULDER_RAW_TOL = 0.0075
_CHANNEL_DERIVED_SHOULDER_PROJECTED_TOL = 0.008


def _channel_coordinate_matches(
    published: float,
    raw: float,
    *,
    tolerance: float = _CHANNEL_PUBLICATION_HALF_CELL,
) -> bool:
    """Match published and raw evidence within a semantic cell plus one float ULP."""

    return abs(published - raw) <= tolerance + max(ulp(published), ulp(raw))


def _channel_coordinate_in_span(
    published: float,
    raw_span: tuple[float, float],
    *,
    tolerance: float,
) -> bool:
    """Whether a published coordinate lies within a raw span modulo publication loss."""

    return (
        published >= raw_span[0]
        or _channel_coordinate_matches(published, raw_span[0], tolerance=tolerance)
    ) and (
        published <= raw_span[1]
        or _channel_coordinate_matches(published, raw_span[1], tolerance=tolerance)
    )


def _step_level_owns_channel(
    channel: Channel,
    feature: StepLevelFeature,
    *,
    face_levels: tuple[FaceLevel, ...],
    risers: tuple[RiserEvidence, ...],
) -> bool:
    """Whether one body-local support carries the channel into the final Z ladder."""

    if channel.depth_axis != feature.frame.axis:
        return False
    floor = channel.d_lo if channel.open_sign > 0 else channel.d_hi
    shoulder_positions = (
        channel.w_center - channel.width / 2,
        channel.w_center + channel.width / 2,
    )
    if not any(_channel_coordinate_matches(floor, level) for level in feature.levels):
        return False

    # Scalar floor/shoulder values are not sufficient ownership evidence: a disconnected
    # body may happen to establish the same values.  Couple the facts through one retained
    # FaceLevel support and the risers whose public body_levels provenance names it.
    long_span = (channel.lo, channel.hi)
    for support in face_levels:
        if (
            support.x_span is None
            or support.y_span is None
            or not _channel_coordinate_matches(floor, support.z)
        ):
            continue
        support_spans = {"x": support.x_span, "y": support.y_span}
        if any(
            not _channel_coordinate_matches(expected, actual)
            for actual, expected in zip(support_spans[channel.long_axis], long_span, strict=True)
        ):
            continue
        width_span = support_spans[channel.width_axis]
        if not all(
            _channel_coordinate_in_span(
                shoulder,
                width_span,
                tolerance=_CHANNEL_DERIVED_SHOULDER_RAW_TOL,
            )
            for shoulder in shoulder_positions
        ):
            continue
        if not any(
            abs(retained.level - support.z) <= 1e-6
            and all(
                abs(actual - expected) <= 1e-6
                for actual, expected in zip(retained.x_span, support.x_span, strict=True)
            )
            and all(
                abs(actual - expected) <= 1e-6
                for actual, expected in zip(retained.y_span, support.y_span, strict=True)
            )
            for retained in feature.level_supports
        ):
            continue

        body_risers = tuple(
            riser
            for riser in risers
            if riser.body_levels is not None
            and any(body_level is support for body_level in riser.body_levels)
            and _channel_coordinate_matches(channel.d_lo, riser.z_lo)
            and _channel_coordinate_matches(channel.d_hi, riser.z_hi)
        )
        body_shoulders = tuple(
            (shoulder.axis, shoulder.position)
            for shoulder in project_step_shoulders(body_risers, levels=list(feature.levels))
        )
        if all(
            any(
                axis == channel.width_axis
                and _channel_coordinate_matches(
                    shoulder,
                    position,
                    tolerance=_CHANNEL_DERIVED_SHOULDER_PROJECTED_TOL,
                )
                for axis, position in body_shoulders
            )
            and any(
                axis == channel.width_axis
                and _channel_coordinate_matches(
                    shoulder,
                    position,
                    tolerance=_CHANNEL_DERIVED_SHOULDER_PROJECTED_TOL,
                )
                for axis, position in feature.shoulders
            )
            for shoulder in shoulder_positions
        ):
            return True
    return False


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


def _convert_blend(blend: Blend, ctx: ConvContext) -> BlendFeature:
    from draftwright.blend_contract import blend_provider_key

    axis, radius, at, side, direction, path_kind, path_radius = blend_provider_key(blend)
    return BlendFeature(
        frame=Frame(at, axis),
        axis=axis,
        radius=radius,
        side=side,
        axis_direction=direction,
        path_kind=path_kind,
        path_radius=path_radius,
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
    Blend: _convert_blend,
    CircularBlindStep: _convert_circular_blind_step,
    PairedRampStep: _convert_paired_ramp_step,
    ThroughStep: _convert_through_step,
    Flat: _convert_flat,
    Groove: _convert_groove,
    RectangularBlindSlot: _convert_rectangular_blind_slot,
    RoundBottomBlindSlot: _convert_round_bottom_blind_slot,
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
    OrientedSlot: (
        "a free-axis slot with authoritative SectionPassage correspondence that the legacy "
        "axis-letter SlotFeature cannot preserve; its dedicated consumer semantics remain "
        "undecided (#1430)"
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


@dataclass(frozen=True)
class _ThroughStepLegacyOwner:
    """One exact semantic input selected to own a through-step leg projection."""

    kind: Literal["envelope", "plate", "step_level"]
    record: Plate | None = None


def _through_step_plate_owner_record_ids(
    evidence: RecognitionEvidence,
    step: ThroughStep,
    plates: tuple[Plate, ...],
) -> frozenset[int] | None:
    """Same-run plate scope, or ``None`` when *step* is not from that evidence run."""

    step_occurrences = tuple(
        occurrence
        for occurrence in evidence.features
        if evidence.family(occurrence) == "through_steps" and evidence.record(occurrence) is step
    )
    if len(step_occurrences) != 1:
        return None
    defining_faces = evidence.defining_faces(step_occurrences[0])
    candidate_by_id = {id(plate): plate for plate in plates}
    owners: set[int] = set()
    for occurrence in evidence.features:
        if evidence.family(occurrence) != "plates":
            continue
        record = evidence.record(occurrence)
        candidate = candidate_by_id.get(id(record))
        if candidate is record and defining_faces & evidence.defining_faces(occurrence):
            owners.add(id(record))
    return frozenset(owners)


def _evidence_occurrence_for_record(
    evidence: RecognitionEvidence,
    family: str,
    record: object,
):
    """Return one exact same-run occurrence, never a value-equal substitute."""

    matches = tuple(
        occurrence
        for occurrence in evidence.features
        if evidence.family(occurrence) == family and evidence.record(occurrence) is record
    )
    return matches[0] if len(matches) == 1 else None


def _plate_owner_has_evidence_scope(
    evidence: RecognitionEvidence,
    plate: Plate,
    owners: tuple[Feature, ...],
    *,
    slot_pattern_members: dict[int, tuple[Slot, ...]],
) -> bool:
    """Require an exact AAG lineage for every cross-family Plate absorption."""

    occurrence = _evidence_occurrence_for_record(evidence, "plates", plate)
    if occurrence is None:
        return False
    defining_faces = evidence.defining_faces(occurrence)
    kinds = tuple(owner.kind for owner in owners)

    def shares_defining_face(family: str, record: object) -> bool:
        candidate = _evidence_occurrence_for_record(evidence, family, record)
        return candidate is not None and bool(defining_faces & evidence.defining_faces(candidate))

    if kinds == ("step_level",):
        step = owners[0]
        if not isinstance(step, StepLevelFeature):
            return False
        try:
            return any(
                shares_defining_face("step_levels", level)
                and any(
                    abs(retained.level - level.z) <= 1e-6
                    and retained.x_span == level.x_span
                    and retained.y_span == level.y_span
                    for retained in step.level_supports
                )
                for level in evidence.result.step_levels
            )
        except (AttributeError, TypeError, ValueError):
            return False

    if kinds == ("envelope", "step_level"):
        step = owners[1]
        if not isinstance(step, StepLevelFeature):
            return False
        try:
            boundaries = {round(float(plate.lo), 3), round(float(plate.hi), 3)}
            return any(
                shares_defining_face("risers", riser)
                and any(
                    round(float(position), 3) in boundaries
                    and any(
                        axis == str(plate.axis) and abs(float(shoulder) - float(position)) <= 1e-6
                        for axis, shoulder in step.shoulders
                    )
                    for position in riser.positions
                )
                for riser in evidence.result.risers
                if str(riser.axis) == str(plate.axis)
            )
        except (AttributeError, TypeError, ValueError):
            return False

    if kinds == ("envelope", "slot_pattern"):
        return any(
            shares_defining_face("slots", member)
            for member in slot_pattern_members.get(id(owners[1]), ())
        )

    # Whole-envelope and polygonal-boss derivations have no released AAG relation that proves
    # the selected final IR belongs to this exact Plate occurrence. Keep them visibly missing
    # instead of recovering correspondence from coordinates or traversal order.
    return False


def _through_step_legacy_owners(
    step,
    bbox,
    step_zs,
    shoulders,
    plates,
    *,
    envelope_emittable: bool,
    plate_owner_record_ids: frozenset[int] | None = None,
) -> tuple[_ThroughStepLegacyOwner, ...] | None:
    """Return the exact legacy projection inputs that jointly define both section legs."""

    bounds = {
        "x": (float(bbox.min.X), float(bbox.max.X)),
        "y": (float(bbox.min.Y), float(bbox.max.Y)),
        "z": (float(bbox.min.Z), float(bbox.max.Z)),
    }
    step_level_owner = _ThroughStepLegacyOwner("step_level")
    envelope_owner = _ThroughStepLegacyOwner("envelope")
    intervals: list[tuple[str, float, float, tuple[_ThroughStepLegacyOwner, ...]]] = []
    for z in step_zs or ():
        intervals.append(("z", *sorted((bounds["z"][0], z)), (step_level_owner,)))
        if envelope_emittable:
            intervals.append(
                (
                    "z",
                    *sorted((z, bounds["z"][1])),
                    (step_level_owner, envelope_owner),
                )
            )
    for shoulder in shoulders:
        lo, hi = bounds[shoulder.axis]
        intervals.append((shoulder.axis, *sorted((lo, shoulder.position)), (step_level_owner,)))
        if envelope_emittable:
            intervals.append(
                (
                    shoulder.axis,
                    *sorted((shoulder.position, hi)),
                    (step_level_owner, envelope_owner),
                )
            )
    for plate in plates or ():
        if plate_owner_record_ids is not None and id(plate) not in plate_owner_record_ids:
            continue
        axis = plate.axis
        plate_lo, plate_hi = sorted((plate.lo, plate.hi))
        plate_owner = _ThroughStepLegacyOwner("plate", plate)
        intervals.append((axis, plate_lo, plate_hi, (plate_owner,)))
        if envelope_emittable:
            bound_lo, bound_hi = bounds[axis]
            if abs(plate_lo - bound_lo) < 0.5:
                intervals.append((axis, plate_hi, bound_hi, (plate_owner, envelope_owner)))
            if abs(plate_hi - bound_hi) < 0.5:
                intervals.append((axis, bound_lo, plate_lo, (plate_owner, envelope_owner)))

    selected: list[_ThroughStepLegacyOwner] = []
    selected_keys: set[tuple[str, int | None]] = set()
    for axis, lo, hi in _through_step_leg_spans((step,)):
        matches = tuple(
            owners
            for owner_axis, owner_lo, owner_hi, owners in intervals
            if owner_axis == axis and abs(owner_lo - lo) < 0.5 and abs(owner_hi - hi) < 0.5
        )
        if not matches:
            return None
        matching_plate_ids = {
            id(owner.record) for owners in matches for owner in owners if owner.kind == "plate"
        }
        if plate_owner_record_ids is not None and len(matching_plate_ids) > 1:
            # Equal spans in distinct body-local plates are not enough evidence to choose a
            # physical owner when the same-run evidence scope is authoritative. Evidence-less
            # framed/injected paths retain the established unscoped compatibility decision.
            return None
        for owners in matches:
            for owner in owners:
                key = (owner.kind, id(owner.record) if owner.record is not None else None)
                if key not in selected_keys:
                    selected_keys.add(key)
                    selected.append(owner)
    return tuple(selected)


def _through_step_legacy_complete(
    step,
    bbox,
    step_zs,
    shoulders,
    plates,
    *,
    envelope_emittable: bool,
    plate_owner_record_ids: frozenset[int] | None = None,
) -> bool:
    """Whether the established Z-up grammar directly defines both open-section legs.

    A face level or shoulder is measured from the part's minimum datum; only when the envelope
    is itself emittable does that pair also define the complementary maximum-side interval.
    Plate thicknesses are already direct intervals, with the same envelope requirement for a
    complement. This is deliberately coordinate-exact drafting evidence, not an axis-only
    family preference: if either physical leg has no owner the aggregate record must lower
    instead of disappearing from completeness (#1382).
    """
    return (
        _through_step_legacy_owners(
            step,
            bbox,
            step_zs,
            shoulders,
            plates,
            envelope_emittable=envelope_emittable,
            plate_owner_record_ids=plate_owner_record_ids,
        )
        is not None
    )


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
    risers=None,
    chamfers=None,
    fillets=None,
    blends=None,
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

    The internal detected path carries its completed aggregate in a task-local handoff.
    ``cyls`` is a precomputed ``analyse_cylinders(part)`` result threaded into every
    cylinder-substrate recogniser called here (holes/bosses/turned/grooves/flats), so
    the solid is scanned once per build (#703); a standalone/partial call derives it once
    before its aggregate run. ``lower_pmi=False`` retains extracted PMI as materialised/report-only IR;
    annotate mode uses the default and correlates supported requirements onto canonical
    feature parameters (#1116)."""
    fillets_supplied = fillets is not None
    blends_supplied = blends is not None
    if fillets_supplied:
        fillets = tuple(fillets)
    if blends_supplied:
        blends = tuple(blends)
    handoff = _RECOGNITION_HANDOFF.get()
    handoff_matches = handoff is not None and handoff.part is part
    recognition_result = handoff.result if handoff_matches and handoff is not None else None
    recognition_evidence = handoff.evidence if handoff_matches and handoff is not None else None
    ownership = (
        handoff.ownership if recognition_result is not None and handoff is not None else None
    )
    circular_blind_steps_supplied = circular_blind_steps is not None
    if circular_blind_steps_supplied:
        circular_blind_steps = tuple(circular_blind_steps)
    derive_hole_patterns = holes is not None and patterns is None
    derive_slot_patterns = slots is not None and slot_patterns is None
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
                risers,
                chamfers,
                fillets,
                blends,
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
    if not needs_aggregate and fillets_supplied and blends_supplied and recognition_result is None:
        raise ValueError(
            "fully supplied fillets and blends require aggregate recognition_result provenance"
        )
    if not needs_aggregate and recognition_result is not None:
        if not _same_fillet_blend_partition(
            _fillet_blend_ownership_keys(fillets, blends),
            _fillet_blend_ownership_keys(recognition_result.fillets, recognition_result.blends),
        ):
            raise ValueError("fillets and blends must preserve aggregate ownership exactly")
    bbox = part.bounding_box()
    features: list[Feature] = []
    envelope_feature: Feature | None = None
    plate_features_by_record_id: dict[int, Feature] = {}
    slot_pattern_members_by_feature_id: dict[int, tuple[Slot, ...]] = {}
    step_level_feature: Feature | None = None

    def append_direct(record: object) -> None:
        """Convert and bind while the exact occurrence-to-IR decision is in hand."""

        feature = convert(record, ctx)
        features.append(feature)
        if ownership is not None:
            ownership.bind(record, feature)

    # Turned-profile classification up front so the shared convert-context carries the
    # part's turning axis (the StepFeature span axis). Pure detection — no feature is
    # emitted here; the turned/boss branch below reads the same plural inventory.
    #
    # Any omitted family or classification input is filled from one public RecognitionResult,
    # preserving cross-family ownership for documented partial-inventory calls.  Derive the
    # aggregate's applicability flag from the shared cylinder substrate rather than probing a
    # public family first; the aggregate remains the only family orchestration (ADR 0017).
    if needs_aggregate:
        recognition = recognition_result
        if recognition is None:
            if cyls is None:
                cyls = analyse_cylinders(part)
            centre = bbox.center()
            cylinder_class = _classify_rotational_cylinders(
                cyls,
                sizes=(bbox.size.X, bbox.size.Y, bbox.size.Z),
                centre=(centre.X, centre.Y, centre.Z),
            )
            recognition_evidence = build_recognition_evidence(
                part,
                cylinders=cyls,
                rotational=(
                    rotational is not None
                    or (profiles is not _UNSET and bool(profiles))
                    or (prof is not _UNSET and prof is not None)
                    or cylinder_class.is_rotational
                ),
            )
            recognition = recognition_evidence.result
        else:
            cyls = recognition.cylinders
        # Fillet and Blend are two projections of the same rounded-chain geometry. The
        # aggregate owns their exact defining-face precedence, so a divergent one-sided
        # override can double-own or erase a radius requirement. A one-sided value must
        # preserve every aggregate-owned occurrence; additions remain available for legacy
        # explicit injection only when the sibling aggregate is empty. A fully supplied pair
        # is accepted only through the detected path's source-part-bound aggregate handoff.
        if fillets_supplied and not blends_supplied:
            supplied_keys = _fillet_blend_ownership_keys(fillets, ())[0]
            aggregate_keys = _fillet_blend_ownership_keys(recognition.fillets, ())[0]
            ownership_changed = (
                not _same_ownership_occurrences(supplied_keys, aggregate_keys)
                if recognition.blends
                else not _preserves_ownership_with_unique_additions(supplied_keys, aggregate_keys)
            )
            if ownership_changed:
                raise ValueError("fillets and blends must preserve aggregate ownership exactly")
        elif blends_supplied and not fillets_supplied:
            supplied_keys = _fillet_blend_ownership_keys((), blends)[1]
            aggregate_keys = _fillet_blend_ownership_keys((), recognition.blends)[1]
            ownership_changed = (
                not _same_ownership_occurrences(supplied_keys, aggregate_keys)
                if recognition.fillets
                else not _preserves_ownership_with_unique_additions(supplied_keys, aggregate_keys)
            )
            if ownership_changed:
                raise ValueError("fillets and blends must preserve aggregate ownership exactly")
        elif fillets_supplied and blends_supplied:
            if not _same_fillet_blend_partition(
                _fillet_blend_ownership_keys(fillets, blends),
                _fillet_blend_ownership_keys(recognition.fillets, recognition.blends),
            ):
                raise ValueError("fillets and blends must preserve aggregate ownership exactly")
        # A circular blind step and its legacy fillet projection compete for the same
        # curved wall.  The aggregate resolves that ownership atomically.  A partial caller
        # may still supply either inventory when it agrees with the aggregate (or when no
        # competing aggregate owner exists), but a divergent one-sided override is ambiguous:
        # accepting it could emit two radius requirements or silently emit neither. The two
        # public record families are independently quantised and carry no shared provider
        # owner identity, so even a paired divergent override cannot be reconciled safely.
        # Preserve the aggregate partition exactly whenever either family owns geometry.
        aggregate_fillet_keys = _fillet_blend_ownership_keys(recognition.fillets, ())[0]
        aggregate_circular_keys = tuple(
            _circular_blind_step_ownership_key(record)
            for record in recognition.circular_blind_steps
        )
        if fillets_supplied and not circular_blind_steps_supplied:
            supplied_fillet_keys = _fillet_blend_ownership_keys(fillets, ())[0]
            if recognition.circular_blind_steps and not _same_ownership_occurrences(
                supplied_fillet_keys, aggregate_fillet_keys
            ):
                raise ValueError(
                    "fillets and circular_blind_steps must be supplied together when "
                    "overriding aggregate ownership"
                )
        elif circular_blind_steps_supplied and not fillets_supplied:
            supplied_circular_keys = tuple(
                _circular_blind_step_ownership_key(record) for record in circular_blind_steps
            )
            if (
                recognition.circular_blind_steps or recognition.fillets
            ) and not _same_ownership_occurrences(supplied_circular_keys, aggregate_circular_keys):
                raise ValueError(
                    "fillets and circular_blind_steps must be supplied together when "
                    "overriding aggregate ownership"
                )
            if (
                not recognition.circular_blind_steps
                and not _preserves_ownership_with_unique_additions(
                    supplied_circular_keys, aggregate_circular_keys
                )
            ):
                raise ValueError("circular_blind_steps must not duplicate an ownership occurrence")
        elif fillets_supplied and circular_blind_steps_supplied:
            supplied_fillet_keys = _fillet_blend_ownership_keys(fillets, ())[0]
            supplied_circular_keys = tuple(
                _circular_blind_step_ownership_key(record) for record in circular_blind_steps
            )
            if recognition.circular_blind_steps or recognition.fillets:
                if not _same_ownership_occurrences(
                    supplied_fillet_keys, aggregate_fillet_keys
                ) or not _same_ownership_occurrences(
                    supplied_circular_keys, aggregate_circular_keys
                ):
                    raise ValueError(
                        "fillets and circular_blind_steps must preserve aggregate ownership "
                        "exactly; divergent paired overrides require provider owner identity"
                    )
            elif fillets and circular_blind_steps:
                raise ValueError(
                    "nonempty fillets and circular_blind_steps cannot be supplied together "
                    "without provider owner identity"
                )
            elif not _preserves_ownership_with_unique_additions(
                supplied_circular_keys, aggregate_circular_keys
            ):
                raise ValueError("circular_blind_steps must not duplicate an ownership occurrence")
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
        risers = recognition.risers if risers is None else risers
        chamfers = recognition.chamfers if chamfers is None else chamfers
        fillets = recognition.fillets if fillets is None else fillets
        blends = recognition.blends if blends is None else blends
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
    boss_groups = _groups_by_diameter(bosses)
    bosses_d = [group[0] for group in boss_groups]
    pending_boss_owners: list[tuple[object, object, str]] = []
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
    through_step_plate_owner_ids = (
        {
            id(step): _through_step_plate_owner_record_ids(
                recognition_evidence,
                step,
                ownership_plates,
            )
            for step in through_steps
        }
        if recognition_evidence is not None
        else {}
    )
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
            plate_owner_record_ids=through_step_plate_owner_ids.get(id(step)),
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
            if all(step is not lowered for lowered in lowered_through_steps)
            and not _through_step_legacy_complete(
                step,
                bbox,
                remaining_levels,
                remaining_shoulders,
                remaining_plates,
                envelope_emittable=envelope_emittable,
                plate_owner_record_ids=through_step_plate_owner_ids.get(id(step)),
            )
        )
        if not promoted:
            break
        lowered_through_steps += promoted
    lowered_through_step_ids = {id(step) for step in lowered_through_steps}
    legacy_through_step_owners = {
        id(step): _through_step_legacy_owners(
            step,
            bbox,
            remaining_levels,
            remaining_shoulders,
            remaining_plates,
            envelope_emittable=envelope_emittable,
            plate_owner_record_ids=through_step_plate_owner_ids.get(id(step)),
        )
        for step in through_steps
        if id(step) not in lowered_through_step_ids
    }
    through_leg_spans = _through_step_leg_spans(lowered_through_steps)
    through_level_zs = _through_step_level_zs(lowered_through_steps)
    through_shoulder_sites = _through_step_shoulder_sites(lowered_through_steps, bbox)
    if not profiles and rotational is None and multi_plate:
        for channel in channels:
            channel_feature = convert(channel, ctx)
            features.append(channel_feature)
            if ownership is not None:
                ownership.bind(channel, channel_feature, reason_code="channel_adapter")

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
        hole_pattern_feature = _pattern_feature(pat, members)
        features.append(hole_pattern_feature)
        if ownership is not None:
            ownership.absorb(
                tuple(members),
                hole_pattern_feature,
                reason_code="hole_pattern_member",
            )
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
        hole_feature = _member_hole(rep, frame, members=mem_locs, count=len(grp))
        features.append(hole_feature)
        if ownership is not None:
            if len(grp) == 1:
                ownership.bind(
                    rep,
                    hole_feature,
                    reason_code="hole_adapter",
                    member_index=0,
                )
            else:
                ownership.absorb(
                    tuple(grp),
                    hole_feature,
                    reason_code="grouped_hole_member",
                )
    if ownership is not None:
        for hole in holes:
            if hole.csink is not None:
                ownership.absorb_nested(
                    hole.csink,
                    hole,
                    reason_code="countersink_hole_owner",
                )

    # Profiled bores are their own recognition family because full-cylinder recognition
    # cannot see their partial cylindrical faces. They still lower to HoleFeature so the
    # established hole location, GD&T, placement and edit paths remain one implementation.
    if double_d_bores is None:
        double_d_bores = recognise_double_d_bores(part)
    for bore in double_d_bores:
        append_direct(bore)

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
        slot_pattern_feature = _slot_pattern_feature(pat, list(pat.slots))
        features.append(slot_pattern_feature)
        slot_pattern_members_by_feature_id[id(slot_pattern_feature)] = tuple(pat.slots)
        if ownership is not None:
            ownership.absorb(
                tuple(pat.slots),
                slot_pattern_feature,
                reason_code="slot_pattern_member",
            )
    for sl in slots:
        if sl in patterned_sl:
            continue
        slot_feature = convert(sl, ctx)
        features.append(slot_feature)
        if ownership is not None:
            ownership.bind(sl, slot_feature, reason_code="slot_adapter")

    # Capped, edge-open rectangular U-section slots (#1421). The aggregate has already
    # reconciled their topology against ordinary through slots, pockets, channels and passage
    # evidence. Consume that exact inventory as a dedicated semantic feature; do not rescan or
    # coerce it into any of those grammars.
    for blind_slot in rectangular_blind_slots:
        append_direct(blind_slot)

    # Capped, edge-open U-section slots with a straight floor joined by equal round sides.
    # This released aggregate inventory owns the physical family; ordinary and rectangular
    # slots, pockets, channels and passages must not regain ownership downstream.
    for blind_slot in round_bottom_blind_slots:
        append_direct(blind_slot)

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
        pocket_pattern_feature = _pocket_pattern_feature(pat, list(pat.pockets))
        features.append(pocket_pattern_feature)
        if ownership is not None:
            ownership.absorb(
                tuple(pat.pockets),
                pocket_pattern_feature,
                reason_code="pocket_pattern_member",
            )
    for pk in pockets:
        if pk in patterned_pk:
            continue
        pocket_feature = convert(pk, ctx)
        features.append(pocket_feature)
        if ownership is not None:
            ownership.bind(pk, pocket_feature, reason_code="pocket_adapter")

    # Bounded rectangular raised pads: footprint sizing, attachment-axis height, and
    # two in-plane locations. A Z attachment level may also enter the general profile
    # ladder, but that datum-to-level fact does not replace the pad's local rise.
    for pad in pads:
        append_direct(pad)

    # Bounded regular polygonal bosses own an across-flats callout and their direct axial
    # height. They are distinct from circular bosses (diameter semantics) and rectangular
    # pads (two orthogonal footprint sizes).
    if polygonal_bosses is None:
        polygonal_bosses = recognise_polygonal_bosses(part)
    for boss in polygonal_bosses:
        append_direct(boss)

    # A whole regular polygonal prism is stock, not a boss: it owns the form/A-F
    # definition and its axial stock length independently of attachment evidence.
    for stock in polygonal_stock:
        append_direct(stock)

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
    groove_owned_steps: list[tuple[TurnedStep, Groove]] = []
    if profiles:
        grooves_by_profile: dict[int, list[Groove]] = {id(profile): [] for profile in profiles}
        for groove in grooves:
            owners = require_unambiguous_groove_owner(groove, profiles)
            if owners:
                grooves_by_profile[id(owners[0])].append(groove)
        for profile in profiles:
            step_groove_candidates = tuple(
                (
                    step,
                    tuple(
                        groove
                        for groove in grooves_by_profile[id(profile)]
                        if groove_owns_turned_step_band(groove, step)
                    ),
                )
                for step in profile.steps
            )
            groove_candidate_counts = Counter(
                id(groove)
                for _step, candidate_grooves in step_groove_candidates
                for groove in candidate_grooves
            )
            for s, step_groove_owners in step_groove_candidates:
                # Skip the band a groove owns (its callout dimensions width + floor ø). Match
                # on axial POSITION, not diameter: a narrow groove's step is reported at the
                # WALL OD (local_od's pad engulfs both walls when the groove is < ~1.4 mm), so
                # a floor-ø match would silently miss the common circlip case. The groove centre
                # lies within its own step span; the short-length guard keeps a merged shaft run
                # from matching. Require a one-to-one relation in both directions: a sub-mm
                # neighbour can fall within the position tolerance of the same groove, whose
                # width/floor diameter cannot represent both accepted physical bands.
                if step_groove_owners:
                    if (
                        len(step_groove_owners) == 1
                        and groove_candidate_counts[id(step_groove_owners[0])] == 1
                    ):
                        groove_owned_steps.append((s, step_groove_owners[0]))
                    continue
                step_feature = convert(s, ctx)
                features.append(step_feature)
                if ownership is not None:
                    ownership.bind(s, step_feature, reason_code="turned_step_adapter")
        # A narrow external band nested under / beside a larger OD reads as that OD in
        # local_od's max(), so it never becomes a step diameter and goes silently
        # undimensioned (#298). Emit each band the silhouette steps miss as a boss, so
        # render_diameters still gives it a ø callout — aligning the callout inventory
        # with the feature_diameters inventory the coverage lint checks against. A groove
        # floor is likewise a narrow reduced band, but the groove callout already carries its
        # ø, so it is suppressed here (_boss_is_groove_floor) to avoid a duplicate boss ø.
        boss_step_candidates: list[tuple[object, tuple[object, ...]]] = []
        boss_groove_candidates: list[tuple[object, tuple[object, ...]]] = []
        for b in bosses:
            axis = _axis_letter(b)
            axis_index = "xyz".index(axis)
            b_lo, b_hi = sorted(
                (
                    float(b.location[axis_index]),
                    float(b.location[axis_index] - b.axis[axis_index] * b.height),
                )
            )
            candidate_steps = tuple(
                step
                for profile in profiles
                if profile.axis == axis
                and (
                    profile.profile is None
                    or all(
                        abs(float(b.location[index]) - profile.profile.axis_origin[index]) <= 0.5
                        for index in range(3)
                        if index != axis_index
                    )
                )
                for step in profile.steps
                if (
                    abs(b.diameter - step.diameter) <= _DIA_TOL
                    and abs(b_lo - step.lo) <= 0.5
                    and abs(b_hi - step.hi) <= 0.5
                )
            )
            boss_step_candidates.append((b, candidate_steps))
            owned = bool(candidate_steps)
            if not owned:
                candidate_grooves = _boss_groove_floor_candidates(b, grooves)
                boss_groove_candidates.append((b, candidate_grooves))
                if not candidate_grooves:
                    boss_feature = convert(b, ctx)
                    features.append(boss_feature)
                    if ownership is not None:
                        ownership.bind(b, boss_feature, reason_code="boss_adapter")
        if ownership is not None:
            step_claim_counts = Counter(
                id(candidate) for _, candidates in boss_step_candidates for candidate in candidates
            )
            pending_boss_owners.extend(
                (boss, candidates[0], "boss_turned_step_owner")
                for boss, candidates in boss_step_candidates
                if len(candidates) == 1 and step_claim_counts[id(candidates[0])] == 1
            )
            groove_claim_counts = Counter(
                id(candidate)
                for _, candidates in boss_groove_candidates
                for candidate in candidates
            )
            pending_boss_owners.extend(
                (boss, candidates[0], "boss_groove_owner")
                for boss, candidates in boss_groove_candidates
                if len(candidates) == 1 and groove_claim_counts[id(candidates[0])] == 1
            )
    else:
        boss_groove_candidates = [
            (boss, _boss_groove_floor_candidates(boss, grooves)) for boss in bosses
        ]
        groove_claim_counts = Counter(
            id(candidate) for _, candidates in boss_groove_candidates for candidate in candidates
        )
        groove_candidates_by_boss_id = {
            id(boss): candidates for boss, candidates in boss_groove_candidates
        }
        for group in boss_groups:
            b = group[0]
            # A grooved round body can still fail the turned-step squareness gate (e.g. a
            # shaft with a rectangular flange) and land here with prof=None. Suppress the
            # groove-floor boss so its ø is not dimensioned twice — boss ø + groove callout
            # (#148c 3rd-pass review).
            if _boss_is_groove_floor(b, grooves):
                if ownership is not None:
                    pending_boss_owners.extend(
                        (member, candidates[0], "boss_groove_owner")
                        for member in group
                        if len(candidates := groove_candidates_by_boss_id[id(member)]) == 1
                        and groove_claim_counts[id(candidates[0])] == 1
                    )
                continue
            boss_feature = convert(b, ctx)
            features.append(boss_feature)
            if ownership is not None:
                represented = tuple(
                    member for member in group if not groove_candidates_by_boss_id[id(member)]
                )
                if len(represented) == 1:
                    ownership.bind(represented[0], boss_feature, reason_code="boss_adapter")
                else:
                    ownership.absorb(
                        represented,
                        boss_feature,
                        reason_code="boss_diameter_group_member",
                    )
                pending_boss_owners.extend(
                    (member, candidates[0], "boss_groove_owner")
                    for member in group
                    if len(candidates := groove_candidates_by_boss_id[id(member)]) == 1
                    and groove_claim_counts[id(candidates[0])] == 1
                )
        # Overall envelope dims for a *prismatic* part — not a round single-OD body
        # (a boss whose diameter fills the footprint is the body, dimensioned by its
        # OD, not a box).
        if not _is_round(bbox, bosses_d) and not polygonal_stock:
            # The same construction the declared verb and the emitter's synthesis use.
            # Detection was the REFERENCE the other two were fixed to match (#977/#976); with
            # three independent producers, "matches the detector" was a property to re-verify
            # rather than one the code held. Now there is one spelling.
            from draftwright.model.declare import _envelope_from_bbox

            envelope_feature = _envelope_from_bbox(bbox)
            features.append(envelope_feature)

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
                plate_feature = convert(pl, ctx)
                features.append(plate_feature)
                plate_features_by_record_id[id(pl)] = plate_feature
                if ownership is not None:
                    ownership.bind(pl, plate_feature, reason_code="plate_adapter")

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
            step_level_feature = StepLevelFeature(
                frame=Frame((c.X, c.Y, bbox.min.Z), "z"),
                base=bbox.min.Z,
                levels=_levels,
                shoulders=_shoulders,
                datum=(bbox.min.X, bbox.min.Y, bbox.min.Z),
                level_supports=_level_supports,
            )
            features.append(step_level_feature)
            if ownership is not None and not multi_plate:
                for channel in channels:
                    if _step_level_owns_channel(
                        channel,
                        step_level_feature,
                        face_levels=face_levels,
                        risers=risers,
                    ):
                        ownership.absorb_into(
                            channel,
                            step_level_feature,
                            reason_code="channel_step_level_owner",
                        )

    # Chamfers (#560/#1254) — called out C{leg} / {leg}×{angle}°. The package recognises
    # both oblique planar and conical turned forms; both lower through the same converter and
    # IR. An injected aggregate inventory is consumed directly, without a sibling rescan.
    for ch in chamfers:
        append_direct(ch)

    # Fillets (#561/#1281) — called out R{radius} (grouped n× at render). The package
    # recognises both cylindrical prismatic blends and toroidal turned rounds; both lower
    # through the same converter and IR. An injected aggregate inventory is consumed directly,
    # without a sibling rescan.
    for fl in fillets:
        append_direct(fl)

    # Accepted Blend records are the aggregate remainder after exact Fillet precedence.
    # Preserve their free-axis contract in dedicated IR; never rerun or locally rematch Fillets.
    for blend_record in blends:
        append_direct(blend_record)

    # Quarter-cylindrical corner cuts with one blind terminal (#1382). The aggregate
    # supplies the oriented centreline and transverse quarter arc, so radius and depth
    # lower without topology access or a sibling scan.
    for circular_step in circular_blind_steps:
        append_direct(circular_step)

    # Mirror-symmetric paired-ramp steps (#1382) — the aggregate proves two equal acute
    # cross-section angles and one open-to-terminal run.  Consume the supplied aggregate
    # inventory directly; standalone model detection invokes the same public family once.
    if paired_ramp_steps is None:
        # Match RecognitionResult applicability on the standalone path. A supplied aggregate
        # inventory already embodies that one orchestration decision and is never re-filtered.
        paired_ramp_steps = recognise_paired_ramp_steps(part) if orientation is None else ()
    for ramp in paired_ramp_steps:
        append_direct(ramp)

    # Rectangular open-profile through steps (#1382).  The aggregate record owns the exact
    # run/anchor/section correspondence; Draftwright lowers its two transverse section legs
    # without rescanning the body or inventing a third through-length requirement.
    #
    # Aggregate ownership precedes its lower-level face-level/riser/plate fragments above: the
    # exact matching transition/thickness is removed from those legacy projections so the local
    # two-leg grammar reaches the sheet once, on every principal run axis.
    for through in lowered_through_steps:
        through_feature = convert(through, ctx)
        features.append(through_feature)
        if ownership is not None:
            ownership.bind(
                through,
                through_feature,
                reason_code="through_step_adapter",
            )
    if ownership is not None:
        for through in through_steps:
            if id(through) in lowered_through_step_ids:
                continue
            claims = legacy_through_step_owners.get(id(through))
            if claims is None:
                continue
            owner_features: list[Feature] = []
            unresolved = False
            for claim in claims:
                owner_feature = (
                    envelope_feature
                    if claim.kind == "envelope"
                    else step_level_feature
                    if claim.kind == "step_level"
                    else plate_features_by_record_id.get(id(claim.record))
                )
                if owner_feature is None:
                    unresolved = True
                    break
                if not any(existing is owner_feature for existing in owner_features):
                    owner_features.append(owner_feature)
            if not unresolved:
                ownership.bind_many(
                    through,
                    tuple(owner_features),
                    reason_code="through_step_legacy_projection",
                )

        reason_for_owner_kinds = {
            ("step_level",): "plate_step_level_owner",
            ("envelope", "step_level"): "plate_step_ladder_owner",
            ("envelope", "slot_pattern"): "plate_slot_pattern_owner",
        }
        for plate in plates or ():
            if ownership.has_owner(plate):
                continue
            dependencies = plate_owner_dependencies(plate, features)
            owners = tuple(feature for feature, _parameter in dependencies)
            unique_owners = tuple(
                feature
                for index, feature in enumerate(owners)
                if not any(previous is feature for previous in owners[:index])
            )
            owner_kinds: tuple[Any, ...] = tuple(
                getattr(feature, "kind", None) for feature in unique_owners
            )
            reason_code = reason_for_owner_kinds.get(owner_kinds)
            if reason_code is not None and _plate_owner_has_evidence_scope(
                ownership.evidence,
                plate,
                unique_owners,
                slot_pattern_members=slot_pattern_members_by_feature_id,
            ):
                ownership.absorb_into_many(
                    plate,
                    unique_owners,
                    reason_code=reason_code,
                )

    # Machined flats on round stock (#148b) — a planar face truncating a cylinder,
    # called out by its across-flats size. Detected UNCONDITIONALLY (not gated by the
    # rotational branch): a D-shaft / hex head IS round stock and classifies rotational,
    # yet its flat still needs a callout. The recogniser self-gates on OD adjacency, so a
    # part with no round stock yields none.
    for flat in recognise_flats(part, cyls=cyls) if flats is None else flats:
        append_direct(flat)

    # Turned / circlip grooves on round stock (#148c) — an annular channel (a strict
    # local-minimum OD band) dimensioned by width + floor diameter, recognised above so the
    # turned-step chain can exclude the coincident band. Also UNCONDITIONAL: a grooved shaft
    # is round stock and classifies rotational, yet the groove still needs its own callout.
    # The recogniser self-gates on external OD bands, so a prismatic part yields none.
    for groove in grooves:
        groove_feature = convert(groove, ctx)
        features.append(groove_feature)
        if ownership is not None:
            ownership.bind(groove, groove_feature)
            for step, owner_groove in groove_owned_steps:
                if owner_groove is groove:
                    ownership.absorb_into(
                        step,
                        groove_feature,
                        reason_code="turned_step_groove_owner",
                    )

    if ownership is not None:
        # A turned step is the more specific correspondence. Resolve it before the direct
        # groove-floor fallback so the builder's final-owner cardinality guard leaves a
        # disconnected same-diameter boss honestly missing rather than crediting both.
        ordered_boss_owners = sorted(
            pending_boss_owners,
            key=lambda pending: pending[2] != "boss_turned_step_owner",
        )
        for boss_record, owner_record, reason_code in ordered_boss_owners:
            if ownership.has_owner(owner_record) and not ownership.has_chained_dependent(
                owner_record
            ):
                ownership.absorb_via(boss_record, owner_record, reason_code=reason_code)

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

    return (
        lower_ap242_dimensions(
            model,
            feature_remap=ownership.remap_feature if ownership is not None else None,
        )
        if lower_pmi
        else model
    )


def _is_round(bbox, bosses, tol: float = 0.5) -> bool:
    """True when a boss's OD fills the part footprint — a round body of revolution,
    dimensioned by its OD rather than a width×depth box."""
    return any(
        abs(b.diameter - bbox.size.X) <= tol and abs(b.diameter - bbox.size.Y) <= tol
        for b in bosses
    )
