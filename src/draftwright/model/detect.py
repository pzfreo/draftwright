"""detect — build the part-model IR by running the feature detectors (ADR 1 (was 0008)).

The front-end of the compiler. Each detector is an existing recognition heuristic
(:func:`recognise_holes`, :func:`recognise_turned_steps`, :func:`recognise_bosses`) adapted to
*emit* IR `Feature` objects — their B-rep logic is unchanged; only their output
shape is normalised into the waist. New shapes plug in here as new detectors
emitting new `Feature` types.

Turned profile and bosses are complementary, not competing: a
turned part is described by its `StepFeature`s (length + OD per segment); a
non-turned part's external diameters come from `BossFeature`s. Holes are detected
for any part.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, replace
from math import atan2, degrees, ulp
from typing import Any, Literal, cast

from quiddity import (
    AngledStep,
    Blend,
    BoltCircle,
    BossRecord,
    Chamfer,
    CircularBlindStep,
    CircularFacePattern,
    CounterSink,
    DoubleDBore,
    FaceLevel,
    Fillet,
    Flat,
    FreeformSurface,
    Groove,
    GussetRib,
    GussetRibArray,
    GussetRibMirrorPair,
    HoleRecord,
    HoleSpec,
    InteriorVoid,
    LinearArray,
    ObliqueThroughStep,
    OrientedChamfer,
    OrientedSlot,
    OrientedSlotArray,
    OrientedSlotGrid,
    PairedRampStep,
    Plate,
    PolygonalBoss,
    PolygonalStock,
    RaisedPad,
    RecognitionResult,
    RectangularHoleSet,
    RectGrid,
    RepeatingRadialProfile,
    RiserEvidence,
    SectionRecess,
    SectionRecessArray,
    SectionRecessDocument,
    SectionRecessGrid,
    SectionRecessRefusal,
    SheetMetalBody,
    Slot,
    SlotArray,
    SlotGrid,
    StepShoulder,
    ThinWallBody,
    ThroughStep,
    TurnedStep,
    analyse_cylinders,
    project_step_shoulders,
    recognise_bosses,
    recognise_chamfers,
    recognise_countersinks,
    recognise_double_d_bores,
    recognise_fillets,
    recognise_flats,
    recognise_grooves,
    recognise_hole_patterns,
    recognise_holes,
    recognise_paired_ramp_steps,
    recognise_plates,
    recognise_polygonal_bosses,
    recognise_polygonal_stock,
    recognise_risers,
    recognise_slot_patterns,
    recognise_slots,
    recognise_through_steps,
    step_level_records,
)
from quiddity.evidence import FeatureRef, RecognitionEvidence, build_recognition_evidence

from draftwright._geometry import (
    _axis_letter,
    _fmt_pmi_magnitude,
    _is_principal_axis,
    _xyz,
    plane_axis_names,
)
from draftwright.measurement_support import square_polygonal_boss_pad_owner
from draftwright.model.declare import circular_blind_step, control_frame, datum
from draftwright.model.detect_inventory import (
    DetectionInventory,
    complete_inventory,
    prepare_inventory,
)
from draftwright.model.detect_ownership import (
    PrismaticOwnershipStage,
    prepare_prismatic_ownership,
)
from draftwright.model.detect_ownership import (
    _circular_blind_step_ownership_key as _circular_blind_step_ownership_key,
)
from draftwright.model.detect_ownership import (
    _fillet_blend_ownership_keys as _fillet_blend_ownership_keys,
)
from draftwright.model.detect_ownership import (
    _preserves_ownership_with_unique_additions as _preserves_ownership_with_unique_additions,
)
from draftwright.model.detect_ownership import (
    _same_fillet_blend_partition as _same_fillet_blend_partition,
)
from draftwright.model.detect_ownership import (
    _same_ownership_occurrences as _same_ownership_occurrences,
)
from draftwright.model.ir import (
    AUTHORED_DIMENSION_KINDS,
    AngleFeature,
    AnglePatternFeature,
    AngularReference,
    AuthoredDimension,
    BlendFeature,
    BossFeature,
    ChamferFeature,
    ChannelFeature,
    CircularBlindStepFeature,
    CircularChannelFeature,
    ControlFrame,
    Datum,
    DatumRef,
    Feature,
    FilletFeature,
    FlatFeature,
    Frame,
    GrooveFeature,
    GussetRibFeature,
    HexPocketFeature,
    HoleFeature,
    LevelSupport,
    Note,
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
    standalone_oriented_slots,
)
from draftwright.plate_correspondence import plate_owner_dependencies
from draftwright.profile_angles import profile_angle_repetitions, profile_angle_requirements
from draftwright.recognition_frame import (
    groove_owns_turned_step_band,
    require_unambiguous_groove_owner,
)
from draftwright.recognition_ownership import (
    BOSS_BLEND_DIAMETER_TOL,
    RecognitionOwnershipBuilder,
    bolt_circle_is_corroborated,
    boss_blend_owner_pairs,
    envelope_is_emittable,
)
from draftwright.section_recess_contract import (
    UnsupportedSectionRecess,
    distinct_section_recess_patterns,
    section_recess_fields,
    section_recess_pattern_members,
    section_recess_pocket_fields,
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


def _plane_indices(axis: str) -> tuple[int, int]:
    return {"x": (1, 2), "y": (0, 2), "z": (0, 1)}[axis]


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
    if isinstance(pat, RectangularHoleSet):
        frame = Frame(_xyz(pat.center), axis)
        return PatternFeature(
            frame,
            "grid",
            n,
            _member_hole(members[0], frame),
            members=locs,
            grid=(pat.height, pat.width),
            rows=2,
            cols=2,
            angle=pat.angle,
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


def _boss_groove_floor_candidates(b, grooves, recognition_evidence=None):
    """Exact groove records satisfying the established boss-floor consumer predicate."""
    ax = _axis_letter(b)
    axis_index = "xyz".index(ax)
    return tuple(
        g
        for g in grooves
        if abs(b.diameter - g.diameter) <= _DIA_TOL
        and g.axis == ax
        and (
            recognition_evidence is None
            or _records_share_defining_target(recognition_evidence, "bosses", b, "grooves", g)
            is not False
        )
        and all(
            abs(float(b.location[index]) - float(g.at[index])) <= 0.5
            for index in range(3)
            if index != axis_index
        )
    )


_DIA_TOL = 0.15  # two ø values within this tolerance (mm) are the same diameter
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


def build_pmi_features(
    pmi, bbox
) -> list[AuthoredDimension | PmiFeature | ControlFrame | DatumRef | Note]:
    """Re-home extracted STEP AP242 PMI records into drafting-concept IR (#208).

    Shared by :func:`build_part_model` (the detection path) and the declared-model PMI
    synthesis in ``builder._assemble`` (#472) so both construct features identically.
    Dimensional PMI becomes :class:`AuthoredDimension`, because users edit drafting
    dimensions rather than source-format PMI. Complete, supported geometric tolerances become
    :class:`ControlFrame`; complete datum-feature definitions become :class:`DatumRef`;
    unsupported records remain raw :class:`PmiFeature` fallbacks. Empty/``None`` ``pmi`` →
    ``[]``."""
    out: list[AuthoredDimension | PmiFeature | ControlFrame | DatumRef | Note] = []
    for r in pmi or ():
        if r.ref_bbox is not None:
            x0, y0, z0, x1, y1, z1 = r.ref_bbox
            pmi_origin = ((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2)
        else:
            pmi_origin = (bbox.center().X, bbox.center().Y, bbox.center().Z)
        proven_axis = (
            r.reference_axis
            if r.source_category == "geometric_tolerance" and r.reference_axis
            else r.dominant_axis
        )
        ax = proven_axis.lower() if proven_axis in ("X", "Y", "Z") else "z"
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
                    view=r.view,
                    source_kind=r.kind,
                    source_id=r.source_id,
                    lowering_blockers=r.lowering_blockers,
                    rendering_blockers=tuple(
                        dict.fromkeys((*r.rendering_blockers, *r.source_value_blockers))
                    ),
                    cylindrical_refs=r.cylindrical_refs,
                    angular_reference=r.angular_reference,
                    circular_refs=r.circular_refs,
                    angular_references=getattr(r, "angular_references", ()),
                    angular_member_ids=(
                        tuple(r.shape_aspect_ids) if getattr(r, "angular_references", ()) else ()
                    ),
                    angular_reference_item_groups=(
                        tuple(r.reference_item_groups)
                        if getattr(r, "angular_references", ())
                        else ()
                    ),
                    basic=r.basic,
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
            reference_bboxes=r.reference_bboxes,
            structured_fields=r.structured_fields,
        )
        if r.source_category == "geometric_tolerance" and not r.lowering_blockers:
            material_modifier = None
            if "maximum_material_requirement" in r.gtol_modifiers:
                material_modifier = "M"
            elif "least_material_requirement" in r.gtol_modifiers:
                material_modifier = "L"
            item = control_frame(
                r.kind,
                str(r.value),
                raw,
                # Datum targets may repeat the datum they collectively establish.  An FCF
                # carries the ordered datum precedence, with one compartment per datum.
                datums=tuple(dict.fromkeys(r.datum_refs)),
                diameter="diameter_zone" in r.gtol_modifiers,
                modifier=material_modifier,
            )
            out.append(
                replace(
                    item,
                    all_around="all_around" in r.gtol_modifiers,
                    all_over="all_over" in r.gtol_modifiers,
                    source_id=r.source_id,
                    part21_id=r.part21_id,
                    display_tolerance=_fmt_pmi_magnitude(r.value),
                    spherical_diameter="spherical_diameter_zone" in r.gtol_modifiers,
                )
            )
            continue
        if r.source_category == "datum" and not r.lowering_blockers and r.reference_axis:
            edge_view = {"X": "front", "Y": "side", "Z": "front"}[r.reference_axis]
            center = bbox.center()
            center_point = (center.X, center.Y, center.Z)
            site = r.ref_pts[0] if r.ref_pts else pmi_origin
            axial_index = "XYZ".index(r.reference_axis)
            surface_kind = r.reference_surface_kind
            if not surface_kind:
                # Compatibility for hand-constructed records predating exact kind.
                surface_kind = (
                    "plane"
                    if r.ref_bbox is not None
                    and r.ref_bbox[axial_index + 3] - r.ref_bbox[axial_index] < 1e-4
                    else "cylinder"
                )
            planar = surface_kind == "plane"
            # A plane's leader follows its normal in the edge-on view. A
            # cylindrical datum instead attaches radially to its profile.
            if planar and axial_index < 2:
                side_index = axial_index
                low_side, high_side = "left", "right"
            elif not planar and axial_index == 2:
                side_index = 0
                low_side, high_side = "left", "right"
            else:
                side_index = 2
                low_side, high_side = "below", "above"
            if len(r.reference_normal) == 3 and abs(r.reference_normal[side_index]) < 0.9:
                out.append(
                    replace(
                        raw,
                        lowering_blockers=(
                            "datum surface normal cannot be shown by an axial leader in this view",
                        ),
                    )
                )
                continue
            if len(r.reference_normal) == 3:
                side = high_side if r.reference_normal[side_index] > 0 else low_side
            else:
                side = high_side if site[side_index] >= center_point[side_index] else low_side
            datum_item = datum(
                r.label,
                replace(raw, frame=Frame(origin=site, axis=ax)),
                view=edge_view,
                side=side,
            )
            datum_item = replace(datum_item, reference_surface_kind=surface_kind)
            out.append(
                replace(
                    datum_item,
                    source_id=r.source_id,
                    source_ids=r.source_ids or ((r.source_id,) if r.source_id else ()),
                    part21_id=r.part21_id,
                )
            )
            continue
        if r.kind in ("surface_label", "common_label") and not r.lowering_blockers and r.ref_pts:
            view = {"X": "front", "Y": "side", "Z": "front"}.get(r.dominant_axis, "front")
            label_origin = r.ref_pts[0]
            vertical_index = 2 if view in ("front", "side") else 1
            center = bbox.center()
            center_coord = (center.X, center.Y, center.Z)[vertical_index]
            side = "above" if label_origin[vertical_index] >= center_coord else "below"
            out.append(
                Note(
                    frame=Frame(origin=label_origin, axis=ax),
                    text=r.label,
                    view=view,
                    side=side,
                    origin=raw,
                    source_id=r.source_id,
                    source_ids=r.source_ids or ((r.source_id,) if r.source_id else ()),
                    part21_id=r.part21_id,
                )
            )
            continue
        out.append(raw)
    return out


# ---------------------------------------------------------------------------
# ADR 3 (was 0013) — the typed record→Feature converter registry.
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


@dataclass
class DetectionRun:
    """One ordered lowering run over the completed inventory and ownership projection."""

    part: Any
    inventory: DetectionInventory
    bbox: Any
    ctx: ConvContext
    prismatic: PrismaticOwnershipStage
    features: list[Feature]
    ownership: RecognitionOwnershipBuilder | None
    boss_groups: list[list[object]]
    boss_blend_owner_by_id: dict[int, object]
    envelope_emittable: bool
    plate_features_by_record_id: dict[int, Feature]
    slot_pattern_members_by_feature_id: dict[int, tuple[Slot, ...]]

    def append_direct(self, record: object) -> None:
        """Convert and bind while the exact occurrence-to-IR decision is in hand."""
        feature = convert(record, self.ctx)
        self.features.append(feature)
        if self.ownership is not None:
            self.ownership.bind(record, feature)


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
        end_radius=sl.end_radius,
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
        end_radius=sl.end_radius,
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


def _convert_section_recess_pocket(record: Mapping, *, schema_version: int) -> PocketFeature:
    """Lower the validated schema-2 pocket fields into the existing drafting IR."""
    values = section_recess_pocket_fields(record, schema_version=schema_version)
    return PocketFeature(frame=Frame(values.pop("origin"), values.pop("axis")), **values)


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
    channel: ChannelFeature,
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
    """Lower an exact run-local recess pattern whose members use the pocket grammar."""
    n = len(members)
    axis = members[0].depth_axis
    locs = tuple(m.frame.origin for m in members)
    if type(pat) is SectionRecessGrid:
        long_axis, width_axis = plane_axis_names(axis)
        angle = degrees(
            atan2(
                pat.col_direction["xyz".index(width_axis)],
                pat.col_direction["xyz".index(long_axis)],
            )
        )
        return PocketPatternFeature(
            frame=Frame((pat.center[0], pat.center[1], pat.center[2]), axis),
            pattern="grid",
            count=n,
            member=members[0],
            members=locs,
            grid=(pat.row_pitch, pat.col_pitch),
            rows=pat.rows,
            cols=pat.cols,
            angle=angle,
        )
    center = tuple(sum(at[i] for at in locs) / n for i in range(3))
    return PocketPatternFeature(
        frame=Frame(center, axis),
        pattern="linear",
        count=n,
        member=members[0],
        members=locs,
        pitch=pat.pitch,
        direction=tuple(pat.direction),
    )


def _convert_section_recess(source: SectionRecess, ctx: ConvContext) -> Feature:
    """Lower one published recess into the established drafting vocabulary."""
    kind, values = section_recess_fields(source)
    origin, axis = values.pop("origin"), values.pop("axis")
    constructor: Callable[..., Feature] = {
        "pocket": PocketFeature,
        "channel": ChannelFeature,
        "circular_channel": CircularChannelFeature,
        "hex_pocket": HexPocketFeature,
        "rectangular_blind_slot": RectangularBlindSlotFeature,
        "round_bottom_blind_slot": RoundBottomBlindSlotFeature,
    }[kind]
    if kind in ("rectangular_blind_slot", "round_bottom_blind_slot", "circular_channel"):
        values["axis"] = axis
    return constructor(frame=Frame(origin, axis), **values)


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


def _gusset_feature(
    ribs: tuple[GussetRib, ...],
    pattern: GussetRibArray | GussetRibMirrorPair | None,
    ctx: ConvContext,
) -> GussetRibFeature:
    """Lower only public gusset facts; pattern membership is provider object identity."""
    first = ribs[0]
    if any(
        rib.thickness_axis != first.thickness_axis
        or rib.supports != first.supports
        or rib.legs != first.legs
        or rib.directions != first.directions
        for rib in ribs[1:]
    ):
        raise ValueError("a gusset-rib pattern must have one common section")
    centres = tuple((rib.thickness_bounds[0] + rib.thickness_bounds[1]) / 2 for rib in ribs)
    coordinates = {axis: value for axis, value in first.supports}
    coordinates[first.thickness_axis] = sum(centres) / len(centres)
    if isinstance(pattern, GussetRibArray):
        pattern_kind: Literal["single", "linear", "mirror"] = "linear"
        pitch, mirror_plane = pattern.pitch, None
    elif isinstance(pattern, GussetRibMirrorPair):
        pattern_kind, pitch, mirror_plane = "mirror", None, pattern.mirror_plane
    else:
        pattern_kind, pitch, mirror_plane = "single", None, None
    return GussetRibFeature(
        frame=Frame(
            cast(tuple[float, float, float], tuple(coordinates[axis] for axis in "xyz")),
            first.thickness_axis,
        ),
        axis=first.thickness_axis,
        supports=first.supports,
        legs=first.legs,
        directions=first.directions,
        member_bounds=tuple(rib.thickness_bounds for rib in ribs),
        datum=getattr(ctx.bbox.min, first.thickness_axis.upper()),
        pattern=pattern_kind,
        pitch=pitch,
        mirror_plane=mirror_plane,
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
        profile=groove.profile,
    )


def _convert_oriented_slot(slot: OrientedSlot, ctx: ConvContext) -> OrientedSlotFeature:
    """Lower the complete public free-direction record without topology inference."""

    # Validate the exact released outer and nested schema before reading it.  Completeness
    # uses the same shared boundary, so the adapter cannot accept evidence the observer
    # rejects (ADR 1 (was 0015)).
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
    SectionRecess: _convert_section_recess,
    Slot: _convert_slot,
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
    RectangularHoleSet: _pattern_feature,
    SectionRecessArray: _pocket_pattern_feature,
    SectionRecessGrid: _pocket_pattern_feature,
    SlotArray: _slot_pattern_feature,
    SlotGrid: _slot_pattern_feature,
    GussetRib: _gusset_feature,
    GussetRibArray: _gusset_feature,
    GussetRibMirrorPair: _gusset_feature,
}

# Tier 3 — orchestrated/evidence records: no per-record converter, by design. Each is a
# nested sub-record, aggregated into a correlated feature, or retained solely as independent
# physical evidence; the reason is the residual scope ADR 3 (was 0013) Phase 1 explicitly accepts.
_ORCHESTRATED_RECORDS: dict[type, str] = {
    SectionRecessDocument: (
        "public serialization envelope for recess occurrences, patterns and refusals; "
        "its members are accounted for individually, never as another feature"
    ),
    SectionRecessRefusal: (
        "provider refusal evidence retained in the occurrence ledger with an explicit "
        "unsupported requirement; no accepted geometry exists to convert (#1471)"
    ),
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
# merely because neither has an IR converter.
_UNCONSUMED_RECORDS: dict[type, str] = {
    CircularFacePattern: ("geometric face relation with no reviewed drafting requirement (#1365)"),
    FreeformSurface: ("surface evidence with no reviewed dimension or inspection grammar (#1365)"),
    InteriorVoid: ("body-owned void evidence whose drawing requirements remain undecided (#1365)"),
    ObliqueThroughStep: (
        "free-axis step cannot be represented by the principal-frame ThroughStepFeature (#1365)"
    ),
    OrientedChamfer: ("free-axis chamfer lacks a reviewed IR and view grammar (#1365)"),
    SheetMetalBody: (
        "developed blank and bend-note drafting contract remain under design (#1557)"
    ),
    ThinWallBody: ("shell thickness evidence has no reviewed drafting requirement (#1365)"),
    AngledStep: (
        "an aggregate-reconciled angled blind step whose slanted face has yielded out of "
        "`chamfers`; its available measurements do not choose a truthful general dimension "
        "grammar, so every occurrence has an explicit unsupported completeness outcome (#1247)"
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
        for start, end in zip(step.section, step.section[1:], strict=False):
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


def _evidence_occurrences_for_record(
    evidence: RecognitionEvidence,
    family: str,
    record: object,
) -> tuple[FeatureRef, ...]:
    """Return every exact same-run occurrence, never a value-equal substitute."""

    return tuple(
        occurrence
        for occurrence in evidence.features
        if evidence.family(occurrence) == family and evidence.record(occurrence) is record
    )


def _evidence_occurrence_for_record(
    evidence: RecognitionEvidence,
    family: str,
    record: object,
):
    """Return one unambiguous exact same-run occurrence."""

    matches = _evidence_occurrences_for_record(evidence, family, record)
    return matches[0] if len(matches) == 1 else None


def _records_share_defining_target(
    evidence: RecognitionEvidence,
    first_family: str,
    first_record: object,
    second_family: str,
    second_record: object,
) -> bool | None:
    """Whether two exact same-run records name one non-empty defining-face set.

    Recognition evidence is the provider's physical identity authority. In particular,
    independently inferred axial extents may disagree at tapered transitions even though a
    boss and turned step were derived from the same faces (#1599). ``None`` means at least
    one caller-supplied record is not from this evidence run, so the established public
    geometry contract must decide instead. Empty same-run evidence proves no shared target
    and fails closed.
    """

    first_matches = _evidence_occurrences_for_record(evidence, first_family, first_record)
    second_matches = _evidence_occurrences_for_record(evidence, second_family, second_record)
    if len(first_matches) > 1 or len(second_matches) > 1:
        return False
    if not first_matches or not second_matches:
        return None
    first = first_matches[0]
    second = second_matches[0]
    defining = evidence.defining_faces(first)
    return bool(defining) and defining == evidence.defining_faces(second)


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


def _append_hole_features(
    part,
    *,
    cyls,
    holes: Sequence[HoleRecord] | None,
    patterns: Sequence[BoltCircle | LinearArray | RectGrid | RectangularHoleSet] | None,
    bosses,
    features: list[Feature],
    ownership: RecognitionOwnershipBuilder | None,
) -> None:
    """Lower hole patterns and residual spec groups in inventory order."""
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
            # a Z spread could come back as 0 — a silently wrong drawing.
            #
            # Refused HERE, at the recognition→IR adapter, not in the recogniser: ADR 3 (was 0013) says
            # a recogniser reports the geometry it finds, and `recognise_hole_patterns` finds
            # this one correctly. The limitation is draftwright's IR, so it belongs on
            # draftwright's side of the boundary — which also covers an injected `patterns=`.
            #
            # The members simply stay unpatterned below, so they are still drawn, dimensioned
            # and located. Carrying a full normal on `Frame` would be faithful but widens the
            # ADR 1 (was 0015) waist.
            if ownership is not None:
                ownership.refuse_hole_pattern(pat, reason_code="oblique_pattern_plane")
            continue
        axis_index = max(range(3), key=lambda index: abs(members[0].axis[index]))
        projected_members = {
            tuple(
                round(float(value), 6)
                for index, value in enumerate(member.location)
                if index != axis_index
            )
            for member in members
        }
        if len(projected_members) != len(members):
            # A drafting hole pattern is one set of distinct axes in the opening plane.
            # Quiddity 0.3.2 can also publish a linear relation between coaxial openings
            # separated only along the drilling direction. Those records are useful
            # geometric evidence, but collapsing them into PatternFeature would put all
            # members on one end-view point and state a pitch that cannot be drawn there.
            # Keep the ordinary grouped-hole grammar as owner of those bores.
            if ownership is not None:
                ownership.refuse_hole_pattern(pat, reason_code="noncoplanar_pattern_members")
            continue
        if isinstance(pat, BoltCircle) and not bolt_circle_is_corroborated(
            pat, members, holes, bosses
        ):
            # An uncorroborated bolt circle is not a datum. Three points or
            # four corners of a rectangle always fit a circle; printing
            # `EQ SP ON ø… BC` off it tells the reader to work from a centre that may not
            # exist. A rectangular hole grid can contain four points on a circle
            # centred in empty space.
            #
            # Refused HERE for the same reason the oblique pattern above is: ADR 3 says the
            # recogniser reports the geometry it finds, and a circle through those holes IS
            # findable. Whether it may be STATED as a drafting datum is drafting policy, and
            # that is draftwright's (ADR 3 / AGENTS.md). The members fall through to the
            # un-patterned grouping below, so they are still drawn, counted and located —
            # they simply stop claiming a bolt circle.
            if ownership is not None:
                ownership.refuse_hole_pattern(pat, reason_code="uncorroborated_bolt_circle")
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
            ownership.bind_hole_pattern(pat, hole_pattern_feature)
    # Un-patterned holes: group by machining spec so identical holes share one
    # count× callout (the engine's grouped-callout rule); HoleSpec keys on the
    # snapped axis and the countersink too, so opposite-face drillings and csk-vs-plain
    # holes stay distinct.
    spec_groups: dict[HoleSpec, list[HoleRecord]] = {}
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


def _append_slot_features(
    part,
    *,
    slots,
    slot_patterns,
    ctx: ConvContext,
    features: list[Feature],
    ownership: RecognitionOwnershipBuilder | None,
    slot_pattern_members_by_feature_id: dict[int, tuple[Slot, ...]],
) -> None:
    """Lower grouped and standalone principal-axis slots in inventory order."""
    # Milled slots / reduced across-flats sections (detected for any part). A recognised array
    # of identical slots becomes one SlotPatternFeature (count× SLOT W×L + pitch); its
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


def _append_turned_and_boss_features(
    *,
    profiles,
    grooves,
    bosses,
    boss_groups,
    boss_blend_owner_by_id,
    recognition_evidence: RecognitionEvidence | None,
    ctx: ConvContext,
    features: list[Feature],
    ownership: RecognitionOwnershipBuilder | None,
) -> tuple[list[tuple[TurnedStep, Groove]], list[tuple[object, object, str]]]:
    """Emit turned steps or bosses and retain exact deferred ownership decisions."""
    pending_boss_owners: list[tuple[object, object, str]] = []
    # Body-local turned profiles → step segments; else external bosses → diameters. Profile
    # identity owns the axis line, so parallel shafts never inherit the part bbox centre or
    # each other's groove bands.
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
        # undimensioned. Emit each band the silhouette steps miss as a boss, so
        # render_diameters still gives it a ø callout — aligning the callout inventory
        # with the feature_diameters inventory the coverage lint checks against. A groove
        # floor is likewise a narrow reduced band, but the groove callout already carries its
        # ø, so groove-floor ownership suppresses a duplicate boss ø here.
        boss_step_candidates: list[tuple[object, tuple[object, ...]]] = []
        boss_groove_candidates: list[tuple[object, tuple[object, ...]]] = []
        for b in bosses:
            if owner_blend := boss_blend_owner_by_id.get(id(b)):
                pending_boss_owners.append((b, owner_blend, "boss_blend_owner"))
                continue
            axis = _axis_letter(b)
            axis_index = "xyz".index(axis)
            b_lo, b_hi = sorted(
                (
                    float(b.location[axis_index]),
                    float(b.location[axis_index] - b.axis[axis_index] * b.height),
                )
            )
            candidate_steps = []
            for profile in profiles:
                for step in profile.steps:
                    evidence_match = (
                        _records_share_defining_target(
                            recognition_evidence,
                            "bosses",
                            b,
                            "turned_steps",
                            step,
                        )
                        if recognition_evidence is not None
                        else None
                    )
                    if evidence_match is None:
                        evidence_match = (
                            profile.axis == axis
                            and (
                                profile.profile is None
                                or all(
                                    abs(
                                        float(b.location[index])
                                        - profile.profile.axis_origin[index]
                                    )
                                    <= 0.5
                                    for index in range(3)
                                    if index != axis_index
                                )
                            )
                            and abs(b.diameter - step.diameter) <= _DIA_TOL
                            and abs(b_lo - step.lo) <= 0.5
                            and abs(b_hi - step.hi) <= 0.5
                        )
                    if evidence_match:
                        candidate_steps.append(step)
            boss_step_candidates.append((b, tuple(candidate_steps)))
            owned = bool(candidate_steps)
            if not owned:
                candidate_grooves = _boss_groove_floor_candidates(b, grooves, recognition_evidence)
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
        remaining_boss_groups = []
        for group in boss_groups:
            remaining = []
            for boss in group:
                if owner_blend := boss_blend_owner_by_id.get(id(boss)):
                    pending_boss_owners.append((boss, owner_blend, "boss_blend_owner"))
                else:
                    remaining.append(boss)
            if remaining:
                remaining_boss_groups.append(remaining)
        boss_groove_candidates = [
            (boss, _boss_groove_floor_candidates(boss, grooves, recognition_evidence))
            for group in remaining_boss_groups
            for boss in group
        ]
        groove_claim_counts = Counter(
            id(candidate) for _, candidates in boss_groove_candidates for candidate in candidates
        )
        groove_candidates_by_boss_id = {
            id(boss): candidates for boss, candidates in boss_groove_candidates
        }
        for group in remaining_boss_groups:
            # Diameter equality is a presentation grouping, not physical ownership. Keep
            # every accepted boss as its own IR owner so its axial extent remains an
            # addressable requirement; the renderer groups equal diameter ink later.
            for member in group:
                candidates = groove_candidates_by_boss_id[id(member)]
                if candidates:
                    if (
                        ownership is not None
                        and len(candidates) == 1
                        and groove_claim_counts[id(candidates[0])] == 1
                    ):
                        pending_boss_owners.append((member, candidates[0], "boss_groove_owner"))
                    continue
                boss_feature = convert(member, ctx)
                features.append(boss_feature)
                if ownership is not None:
                    ownership.bind(member, boss_feature, reason_code="boss_adapter")
    return groove_owned_steps, pending_boss_owners


def _append_prismatic_features(
    *,
    bbox,
    profiles,
    rotational,
    envelope_emittable,
    plates,
    multi_plate,
    through_leg_spans,
    ctx,
    features,
    ownership,
    step_zs,
    plate_zs_at_base,
    side_pad_level_zs,
    through_level_zs,
    edge_floor_zs,
    face_levels,
    risers,
    through_shoulder_sites,
    channels,
    recess_features,
    plate_features_by_record_id,
    step_level_owns_channel,
    convert_record,
) -> tuple[Feature | None, Feature | None]:
    """Lower envelope, physical plates, and the ownership-filtered step ladder."""
    envelope_feature: Feature | None = None
    step_level_feature: Feature | None = None
    # Overall envelope dims when neither a whole-part OD nor polygonal stock already conveys
    # the footprint. A local turned profile may coexist with wider prismatic geometry; its
    # mere presence does not own those whole-part extents.
    if envelope_emittable:
        # The same construction the declared verb and the emitter's synthesis use.
        # The declared verb and emitter synthesis use this same envelope construction,
        # keeping all three input paths on one spelling.
        from draftwright.model.declare import _envelope_from_bbox

        envelope_feature = _envelope_from_bbox(bbox)
        features.append(envelope_feature)

    # Plate/wall thicknesses on a multi-plate prismatic — the thin extent of a
    # slab that no other prismatic dim recovers (a wall along X/Y, or a Z base plate too
    # thin for the step-ladder legibility gate). Skipped for turned/rotational parts,
    # whose extents are the OD/length chain, not plate thicknesses.
    #
    # Scope guard: only a GENUINE multi-plate part — slabs on ≥2 distinct axes (a base +
    # an upright wall, i.e. an L/T/U bracket) — is dimensioned this way. A single-axis
    # stack (a base slab under a smaller stacked block) is a *staircase*, owned by the
    # step-height ladder; treating its base as a "plate" would wrongly suppress the step
    # dim. This keeps plate features restricted to genuine multi-plate parts.
    if not profiles and rotational is None:
        if multi_plate:
            for pl in plates:
                if any(
                    pl.axis == axis and abs(pl.lo - lo) <= 1e-6 and abs(pl.hi - hi) <= 1e-6
                    for axis, lo, hi in through_leg_spans
                ):
                    # The aggregate open section is the higher-level owner of this exact
                    # thickness interval. Keeping the plate too prints one physical leg twice.
                    continue
                plate_feature = convert_record(pl, ctx)
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
        # aggregate retains every body-local occurrence for independent completeness.
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
            # not reconstruct a multi-level staircase or a slanted run.
            # Projected over the run's riser evidence, not a fresh scan. `_levels`
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
                    channel_feature = recess_features[id(channel)]
                    assert isinstance(channel_feature, ChannelFeature)
                    if step_level_owns_channel(
                        channel_feature,
                        step_level_feature,
                        face_levels=face_levels,
                        risers=risers,
                    ):
                        ownership.absorb_into(
                            channel,
                            step_level_feature,
                            reason_code="channel_step_level_owner",
                        )

    return envelope_feature, step_level_feature


def _append_gusset_features(*, gusset_ribs, gusset_rib_patterns, ctx, features, ownership) -> None:
    """Lower exact gusset pattern members before their standalone siblings."""
    # A provider gusset pattern is a correlation over the exact
    # physical member objects, so lower it once and bind every occurrence to the shared IR
    # owner.  Unrelated ribs remain independent features.
    assert gusset_ribs is not None and gusset_rib_patterns is not None
    gusset_records = tuple(gusset_ribs)
    patterned_ids: set[int] = set()
    for pattern in gusset_rib_patterns:
        gusset_members = cast(tuple[GussetRib, ...], tuple(pattern.ribs))
        if not gusset_members or any(
            not any(member is rib for rib in gusset_records) for member in gusset_members
        ):
            raise ValueError("gusset-rib pattern members must preserve aggregate identity")
        if any(id(member) in patterned_ids for member in gusset_members):
            raise ValueError("a gusset-rib occurrence cannot belong to two patterns")
        patterned_ids.update(id(member) for member in gusset_members)
        feature = _gusset_feature(gusset_members, pattern, ctx)
        features.append(feature)
        if ownership is not None:
            ownership.absorb(gusset_members, feature, reason_code="gusset_rib_pattern_member")
    for rib in gusset_records:
        if id(rib) in patterned_ids:
            continue
        feature = _gusset_feature((rib,), None, ctx)
        features.append(feature)
        if ownership is not None:
            ownership.bind(rib, feature, reason_code="gusset_rib_adapter")


def _bind_through_step_and_plate_owners(
    *,
    lowered_through_steps,
    through_steps,
    lowered_through_step_ids,
    legacy_through_step_owners,
    envelope_feature,
    step_level_feature,
    plate_features_by_record_id,
    plates,
    features,
    ctx,
    ownership,
    slot_pattern_members_by_feature_id,
    plate_owner_has_evidence_scope,
    convert_record,
) -> None:
    """Bind aggregate through-step owners before legacy plate dependents."""
    # The rectangular open-profile through-step record owns the exact
    # run/anchor/section correspondence; Draftwright lowers its two transverse section legs
    # without rescanning the body or inventing a third through-length requirement.
    #
    # Aggregate ownership precedes its lower-level face-level/riser/plate fragments above: the
    # exact matching transition/thickness is removed from those legacy projections so the local
    # two-leg grammar reaches the sheet once, on every principal run axis.
    for through in lowered_through_steps:
        through_feature = convert_record(through, ctx)
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
            owners = tuple(cast(Feature, feature) for feature, _parameter in dependencies)
            unique_owners = tuple(
                feature
                for index, feature in enumerate(owners)
                if not any(previous is feature for previous in owners[:index])
            )
            owner_kinds: tuple[Any, ...] = tuple(
                getattr(feature, "kind", None) for feature in unique_owners
            )
            reason_code = reason_for_owner_kinds.get(owner_kinds)
            if reason_code is not None and plate_owner_has_evidence_scope(
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


def _append_profile_angle_features(*, recognition_evidence, features, ownership) -> None:
    """Lower ordered profile-angle repetitions and bind source parameters."""
    # Ordered face supports come from the same evidence acquisition as the
    # recognised families. Their drafting requirements use the declared IR;
    # source identity remains in the run-local ledger, outside that IR.
    requirements = profile_angle_requirements(recognition_evidence)

    def corner_key(requirement):
        return id(requirement.source), requirement.first_index, requirement.second_index

    by_key = {corner_key(requirement): requirement for requirement in requirements}
    repeated = {}
    for repetition in profile_angle_repetitions(recognition_evidence):
        keys = tuple(corner_key(member) for member in repetition.members)
        if any(key not in by_key or key in repeated for key in keys):
            continue
        for key in keys:
            repeated[key] = keys
    handled: set[tuple[int, int, int]] = set()
    for requirement in requirements:
        key = corner_key(requirement)
        if key in handled:
            continue
        keys = repeated.get(key, (key,))
        handled.update(keys)
        angle_members = tuple(by_key[member_key] for member_key in keys)
        references = tuple(
            AngularReference(
                vertex=member.vertex,
                first=member.first,
                second=member.second,
                virtual_vertex=member.virtual_vertex,
                sector="opposite",
            )
            for member in angle_members
        )
        feature = (
            AnglePatternFeature(references) if len(references) > 1 else AngleFeature(references[0])
        )
        features.append(feature)
        if ownership is not None:
            for member, parameter in zip(angle_members, feature.parameters(), strict=True):
                ownership.bind_profile_angle(member, feature, parameter_id=parameter.parameter_id)


def _append_polygonal_boss_or_pad_owner(
    boss, pads, pad_features_by_record_id, ownership, append_direct
) -> None:
    """Lower a boss or retain its exact four-sided pad ownership."""
    pad_owner = square_polygonal_boss_pad_owner(
        boss, pads, ownership.evidence if ownership is not None else None
    )
    if pad_owner is None or ownership is None:
        append_direct(boss)
        return
    ownership.absorb_into(
        boss,
        pad_features_by_record_id[id(pad_owner)],
        reason_code="polygonal_boss_pad_owner",
    )


def _append_primary_feature_families(
    run: DetectionRun,
    *,
    scan_double_d_bores,
    scan_polygonal_bosses,
) -> None:
    """Lower holes, slots, recesses, pads and polygonal forms in drawing order."""
    s = run.inventory
    prismatic = run.prismatic
    part = run.part
    cyls = s.cyls
    profiles = s.profiles
    rotational = s.rotational
    multi_plate = prismatic.multi_plate
    channels = prismatic.channels
    recess_features = prismatic.recess_features
    features = run.features
    ownership = run.ownership
    holes = s.holes
    patterns = s.patterns
    bosses = s.bosses
    double_d_bores = s.double_d_bores
    slots = s.slots
    slot_patterns = s.slot_patterns
    ctx = run.ctx
    slot_pattern_members_by_feature_id = run.slot_pattern_members_by_feature_id
    oriented_slots = s.oriented_slots
    oriented_slot_patterns = s.oriented_slot_patterns
    section_recess_patterns = prismatic.section_recess_patterns
    section_recesses = prismatic.section_recesses
    pads = prismatic.pads
    polygonal_bosses = s.polygonal_bosses
    polygonal_stock = s.polygonal_stock
    append_direct = run.append_direct

    if not profiles and rotational is None and multi_plate:
        for channel in channels:
            channel_feature = recess_features[id(channel)]
            features.append(channel_feature)
            if ownership is not None:
                ownership.bind(channel, channel_feature, reason_code="channel_adapter")

    _append_hole_features(
        part,
        cyls=cyls,
        holes=holes,
        patterns=patterns,
        bosses=bosses,
        features=features,
        ownership=ownership,
    )

    # Profiled bores are their own recognition family because full-cylinder recognition
    # cannot see their partial cylindrical faces. They still lower to HoleFeature so the
    # established hole location, GD&T, placement and edit paths remain one implementation.
    if double_d_bores is None:
        double_d_bores = scan_double_d_bores()
    for bore in double_d_bores:
        append_direct(bore)

    _append_slot_features(
        part,
        slots=slots,
        slot_patterns=slot_patterns,
        ctx=ctx,
        features=features,
        ownership=ownership,
        slot_pattern_members_by_feature_id=slot_pattern_members_by_feature_id,
    )

    # Free-direction through slots have a dedicated IR contract. Pattern members remain owned
    # by the separately deferred pattern inventory, so they cannot expand into competing lone
    # callouts while the separate pattern inventory owns their grouping.
    # Both inventories are guaranteed above: either caller-supplied or projected from the one
    # aggregate. Do not retain a fallback rescan here — ADR 3 (was 0017) gives recognition one owner.
    assert oriented_slots is not None
    assert oriented_slot_patterns is not None
    for oriented_slot in standalone_oriented_slots(
        tuple(oriented_slots), tuple(oriented_slot_patterns)
    ):
        append_direct(oriented_slot)

    # Patterns join published occurrence indices to the exact records from this aggregate.
    # Only the pocket grammar currently has a corresponding grouped drawing feature.
    patterned_recesses: set[int] = set()
    for pattern in distinct_section_recess_patterns(section_recess_patterns, section_recesses):
        recess_members = section_recess_pattern_members(pattern, section_recesses)
        member_features = tuple(recess_features.get(id(member)) for member in recess_members)
        if any(getattr(feature, "kind", None) != "pocket" for feature in member_features):
            continue
        if patterned_recesses & {id(member) for member in recess_members}:
            raise ValueError("section recess belongs to multiple drafting patterns")
        patterned_recesses.update(id(member) for member in recess_members)
        pattern_feature = _pocket_pattern_feature(pattern, member_features)
        features.append(pattern_feature)
        if ownership is not None:
            ownership.absorb(recess_members, pattern_feature, reason_code="pocket_pattern_member")
    for source in section_recesses:
        feature = recess_features.get(id(source))
        if feature is None or feature.kind == "channel" or id(source) in patterned_recesses:
            continue
        features.append(feature)
        if ownership is not None:
            ownership.bind(source, feature, reason_code="section_recess_adapter")

    # Bounded rectangular raised pads: footprint sizing, attachment-axis height, and
    # two in-plane locations. A Z attachment level may also enter the general profile
    # ladder, but that datum-to-level fact does not replace the pad's local rise.
    pad_features_by_record_id = {}
    for pad in pads:
        append_direct(pad)
        pad_features_by_record_id[id(pad)] = features[-1]

    # Bounded regular polygonal bosses own an across-flats callout and their direct axial
    # height. They are distinct from circular bosses (diameter semantics); an exact
    # four-sided duplicate of a rectangular pad shares that pad's IR owner.
    if polygonal_bosses is None:
        polygonal_bosses = scan_polygonal_bosses()
    for boss in polygonal_bosses:
        _append_polygonal_boss_or_pad_owner(
            boss, pads, pad_features_by_record_id, ownership, append_direct
        )

    # A whole regular polygonal prism is stock, not a boss: it owns the form/A-F
    # definition and its axial stock length independently of attachment evidence.
    for stock in polygonal_stock:
        append_direct(stock)


def _append_late_feature_families(
    run: DetectionRun,
    *,
    scan_grooves,
    scan_plates,
    scan_paired_ramp_steps,
    scan_flats,
) -> None:
    """Lower turned, prismatic and finishing families in their fixed source order."""
    s = run.inventory
    prismatic = run.prismatic
    bbox = run.bbox
    profiles = s.profiles
    rotational = s.rotational
    grooves = s.grooves
    bosses = s.bosses
    boss_groups = run.boss_groups
    boss_blend_owner_by_id = run.boss_blend_owner_by_id
    recognition_evidence = s.recognition_evidence
    ctx = run.ctx
    features = run.features
    ownership = run.ownership
    envelope_emittable = run.envelope_emittable
    plates = prismatic.plates
    multi_plate = prismatic.multi_plate
    through_leg_spans = prismatic.through_stage.through_leg_spans
    step_zs = prismatic.step_zs
    plate_zs_at_base = prismatic.plate_zs_at_base
    side_pad_level_zs = prismatic.through_stage.side_pad_level_zs
    through_level_zs = prismatic.through_stage.through_level_zs
    edge_floor_zs = prismatic.edge_floor_zs
    face_levels = prismatic.face_levels
    risers = prismatic.risers
    through_shoulder_sites = prismatic.through_stage.through_shoulder_sites
    channels = prismatic.channels
    recess_features = prismatic.recess_features
    plate_features_by_record_id = run.plate_features_by_record_id
    chamfers = s.chamfers
    fillets = s.fillets
    blends = s.blends
    circular_blind_steps = s.circular_blind_steps
    paired_ramp_steps = s.paired_ramp_steps
    orientation = run.ctx.orientation
    gusset_ribs = s.gusset_ribs
    gusset_rib_patterns = s.gusset_rib_patterns
    lowered_through_steps = prismatic.through_stage.lowered_through_steps
    through_steps = s.through_steps
    lowered_through_step_ids = prismatic.through_stage.lowered_through_step_ids
    legacy_through_step_owners = prismatic.through_stage.legacy_through_step_owners
    slot_pattern_members_by_feature_id = run.slot_pattern_members_by_feature_id
    flats = s.flats
    append_direct = run.append_direct

    # Turned / circlip grooves (#148c) — recognised up front so the turned-step chain can
    # exclude any band a groove already dimensions: a groove floor is an annular band, and
    # its two walls read as shoulders, so recognise_turned_steps also delimits it as a
    # middle "step". Emitting both a StepFeature and a GrooveFeature for one band would
    # double-dimension the floor ø (ISO 129) and break ADR 1 (was 0008)'s one-band-one-owner waist.
    if grooves is None:
        grooves = scan_grooves()

    groove_owned_steps, pending_boss_owners = _append_turned_and_boss_features(
        profiles=profiles,
        grooves=grooves,
        bosses=bosses,
        boss_groups=boss_groups,
        boss_blend_owner_by_id=boss_blend_owner_by_id,
        recognition_evidence=recognition_evidence,
        ctx=ctx,
        features=features,
        ownership=ownership,
    )

    if not profiles and rotational is None:
        plates = scan_plates() if plates is None else plates
    envelope_feature, step_level_feature = _append_prismatic_features(
        bbox=bbox,
        profiles=profiles,
        rotational=rotational,
        envelope_emittable=envelope_emittable,
        plates=plates,
        multi_plate=multi_plate,
        through_leg_spans=through_leg_spans,
        ctx=ctx,
        features=features,
        ownership=ownership,
        step_zs=step_zs,
        plate_zs_at_base=plate_zs_at_base,
        side_pad_level_zs=side_pad_level_zs,
        through_level_zs=through_level_zs,
        edge_floor_zs=edge_floor_zs,
        face_levels=face_levels,
        risers=risers,
        through_shoulder_sites=through_shoulder_sites,
        channels=channels,
        recess_features=recess_features,
        plate_features_by_record_id=plate_features_by_record_id,
        step_level_owns_channel=_step_level_owns_channel,
        convert_record=convert,
    )

    # Chamfers are called out C{leg} / {leg}×{angle}°. The package recognises
    # both oblique planar and conical turned forms; both lower through the same converter and
    # IR. An injected aggregate inventory is consumed directly, without a sibling rescan.
    for ch in chamfers:
        append_direct(ch)

    # Fillets are called out R{radius} (grouped n× at render). The package
    # recognises both cylindrical prismatic blends and toroidal turned rounds; both lower
    # through the same converter and IR. An injected aggregate inventory is consumed directly,
    # without a sibling rescan.
    for fl in fillets:
        append_direct(fl)

    # Accepted Blend records are the aggregate remainder after exact Fillet precedence.
    # Preserve their free-axis contract in dedicated IR; never rerun or locally rematch Fillets.
    for blend_record in blends:
        append_direct(blend_record)

    # Quarter-cylindrical corner cuts with one blind terminal. The aggregate
    # supplies the oriented centreline and transverse quarter arc, so radius and depth
    # lower without topology access or a sibling scan.
    for circular_step in circular_blind_steps:
        append_direct(circular_step)

    # Mirror-symmetric paired-ramp steps — the aggregate proves two equal acute
    # cross-section angles and one open-to-terminal run.  Consume the supplied aggregate
    # inventory directly; standalone model detection invokes the same public family once.
    if paired_ramp_steps is None:
        # Match RecognitionResult applicability on the standalone path. A supplied aggregate
        # inventory already embodies that one orchestration decision and is never re-filtered.
        paired_ramp_steps = scan_paired_ramp_steps() if orientation is None else ()
    for ramp in paired_ramp_steps:
        append_direct(ramp)

    _append_gusset_features(
        gusset_ribs=gusset_ribs,
        gusset_rib_patterns=gusset_rib_patterns,
        ctx=ctx,
        features=features,
        ownership=ownership,
    )

    _bind_through_step_and_plate_owners(
        lowered_through_steps=lowered_through_steps,
        through_steps=through_steps,
        lowered_through_step_ids=lowered_through_step_ids,
        legacy_through_step_owners=legacy_through_step_owners,
        envelope_feature=envelope_feature,
        step_level_feature=step_level_feature,
        plate_features_by_record_id=plate_features_by_record_id,
        plates=plates,
        features=features,
        ctx=ctx,
        ownership=ownership,
        slot_pattern_members_by_feature_id=slot_pattern_members_by_feature_id,
        plate_owner_has_evidence_scope=_plate_owner_has_evidence_scope,
        convert_record=convert,
    )

    # Machined flats on round stock (#148b) — a planar face truncating a cylinder,
    # called out by its across-flats size. Detected UNCONDITIONALLY (not gated by the
    # rotational branch): a D-shaft / hex head IS round stock and classifies rotational,
    # yet its flat still needs a callout. The recogniser self-gates on OD adjacency, so a
    # part with no round stock yields none.
    for flat in scan_flats() if flats is None else flats:
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
        # Same-face blends and turned steps are more specific correspondences. Resolve them
        # before the direct groove-floor fallback so the builder's final-owner cardinality
        # guard leaves a disconnected same-diameter boss honestly missing rather than
        # crediting both.
        owner_precedence = {
            "boss_blend_owner": 0,
            "boss_turned_step_owner": 1,
            "boss_groove_owner": 2,
        }
        ordered_boss_owners = sorted(
            pending_boss_owners,
            key=lambda pending: owner_precedence[pending[2]],
        )
        for boss_record, owner_record, reason_code in ordered_boss_owners:
            if ownership.has_owner(owner_record) and not ownership.has_chained_dependent(
                owner_record
            ):
                ownership.absorb_via(boss_record, owner_record, reason_code=reason_code)

    # Rotational furniture — OD + centrelines + concentric bore leaders. Its
    # presence marks the part rotational; emitted from the classification (od, bores).
    if rotational is not None:
        od, bores, rot_axis = rotational
        c = bbox.center()
        features.append(
            RotationalFeature(frame=Frame((c.X, c.Y, c.Z), rot_axis), od=od, bores=tuple(bores))
        )

    _append_profile_angle_features(
        recognition_evidence=recognition_evidence,
        features=features,
        ownership=ownership,
    )


def _prepare_detection_run(
    part,
    s: DetectionInventory,
    bbox,
    *,
    scan_chamfers,
    scan_fillets,
    scan_bosses,
    scan_polygonal_stock,
    scan_through_steps,
    scan_plates,
    scan_risers,
    scan_step_levels,
) -> DetectionRun:
    """Set the conversion frame and prepare ordered ownership from one inventory."""
    if s.profiles is _UNSET:
        assert s.prof is not _UNSET  # both omitted populated the aggregate arm
        s.profiles = () if s.prof is None else (s.prof,)
    s.profiles = () if s.profiles is None else tuple(s.profiles)
    if len(s.profiles) > 1 and any(profile.profile is None for profile in s.profiles):
        raise ValueError("plural turned profiles require body-local profile identity")
    # A supplied rotational classification is the fallback for a body without a profile.
    profile_axes = {profile.axis for profile in s.profiles}
    if len(profile_axes) == 1:
        orientation = next(iter(profile_axes))
    elif profile_axes:
        orientation = None
    else:
        orientation = s.rotational[2] if s.rotational else None
    if s.chamfers is None:
        s.chamfers = scan_chamfers(orientation)
    if s.fillets is None:
        s.fillets = scan_fillets(orientation)
    ctx = ConvContext(bbox=bbox, orientation=orientation)
    # Check whether the envelope will cross the IR waist before legacy ownership is decided.
    if s.bosses is None:
        s.bosses = scan_bosses()
    boss_groups = _groups_by_diameter(s.bosses)
    bosses_d = [group[0] for group in boss_groups]
    if s.polygonal_stock is None:
        s.polygonal_stock = scan_polygonal_stock()
    envelope_emittable = envelope_is_emittable(
        bbox=bbox,
        bosses=bosses_d,
        turned_profiles=s.profiles,
        polygonal_stock=s.polygonal_stock,
    )
    boss_blend_owner_by_id = (
        {
            id(boss): blend
            for boss, blend in boss_blend_owner_pairs(
                s.recognition_evidence,
                tuple(s.bosses),
                tuple(s.blends),
                bbox=bbox,
                envelope_emittable=envelope_emittable,
                diameter_tolerance=BOSS_BLEND_DIAMETER_TOL,
            )
        }
        if s.recognition_evidence is not None
        else {}
    )
    if s.through_steps is None:
        # Match aggregate applicability only on the standalone path.
        s.through_steps = scan_through_steps() if orientation is None else ()
    s.through_steps = tuple(s.through_steps)

    # Provider record conversion is the detector's adapter responsibility. Unsupported
    # occurrences remain in the independent completeness and policy ledgers.
    assert s.section_recesses is not None and s.section_recess_patterns is not None
    s.section_recesses = tuple(s.section_recesses)
    s.section_recess_patterns = tuple(s.section_recess_patterns)
    recess_features: dict[int, Feature] = {}
    convert_record = convert
    for source in s.section_recesses:
        try:
            recess_features[id(source)] = convert_record(source, ctx)
        except UnsupportedSectionRecess:
            continue
    prismatic = prepare_prismatic_ownership(
        bbox=bbox,
        section_recesses=s.section_recesses,
        section_recess_patterns=s.section_recess_patterns,
        recess_features=recess_features,
        profiles=s.profiles,
        rotational=s.rotational,
        plates=s.plates,
        risers=s.risers,
        pads=s.pads,
        step_zs=s.step_zs,
        face_levels=s.face_levels,
        through_steps=s.through_steps,
        recognition_evidence=s.recognition_evidence,
        envelope_emittable=envelope_emittable,
        scan_plates=scan_plates,
        scan_risers=scan_risers,
        scan_step_levels=scan_step_levels,
        plate_owner_record_ids=_through_step_plate_owner_record_ids,
        legacy_complete=_through_step_legacy_complete,
        legacy_owners=_through_step_legacy_owners,
        leg_spans=_through_step_leg_spans,
        level_zs=_through_step_level_zs,
        shoulder_sites=_through_step_shoulder_sites,
    )
    return DetectionRun(
        part=part,
        inventory=s,
        bbox=bbox,
        ctx=ctx,
        prismatic=prismatic,
        features=[],
        ownership=s.ownership,
        boss_groups=boss_groups,
        boss_blend_owner_by_id=boss_blend_owner_by_id,
        envelope_emittable=envelope_emittable,
        plate_features_by_record_id={},
        slot_pattern_members_by_feature_id={},
    )


def build_part_model(
    part,
    *,
    holes: Sequence[HoleRecord] | None = None,
    double_d_bores=None,
    patterns: Sequence[BoltCircle | LinearArray | RectGrid | RectangularHoleSet] | None = None,
    bosses=None,
    polygonal_bosses=None,
    polygonal_stock=None,
    slots=None,
    slot_patterns=None,
    oriented_slots=None,
    oriented_slot_patterns=None,
    risers=None,
    chamfers=None,
    fillets=None,
    blends=None,
    circular_blind_steps=None,
    paired_ramp_steps=None,
    through_steps=None,
    gusset_ribs=None,
    gusset_rib_patterns=None,
    plates=None,
    grooves=None,
    flats=None,
    pads=None,
    section_recesses=None,
    section_recess_patterns=None,
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
    feature inventory (ADR 1 (was 0008 Amendment 5), #244). Omitted sets are detected here,
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
    s = DetectionInventory(
        holes=holes,
        double_d_bores=double_d_bores,
        patterns=patterns,
        bosses=bosses,
        polygonal_bosses=polygonal_bosses,
        polygonal_stock=polygonal_stock,
        slots=slots,
        slot_patterns=slot_patterns,
        oriented_slots=oriented_slots,
        oriented_slot_patterns=oriented_slot_patterns,
        risers=risers,
        chamfers=chamfers,
        fillets=fillets,
        blends=blends,
        circular_blind_steps=circular_blind_steps,
        paired_ramp_steps=paired_ramp_steps,
        through_steps=through_steps,
        gusset_ribs=gusset_ribs,
        gusset_rib_patterns=gusset_rib_patterns,
        plates=plates,
        grooves=grooves,
        flats=flats,
        pads=pads,
        section_recesses=section_recesses,
        section_recess_patterns=section_recess_patterns,
        prof=prof,
        profiles=profiles,
        step_zs=step_zs,
        face_levels=face_levels,
        rotational=rotational,
        cyls=cyls,
    )
    prepare_inventory(s, handoff=_RECOGNITION_HANDOFF.get(), part=part, unset=_UNSET)
    bbox = part.bounding_box()
    complete_inventory(
        s,
        bbox=bbox,
        unset=_UNSET,
        scan_cylinders=lambda: analyse_cylinders(part),
        acquire_evidence=lambda cylinders, is_rotational: build_recognition_evidence(
            part, cylinders=cylinders, rotational=is_rotational
        ),
    )
    run = _prepare_detection_run(
        part,
        s,
        bbox,
        scan_chamfers=lambda orientation: recognise_chamfers(
            part, cyls=s.cyls, include_planar=orientation is None
        ),
        scan_fillets=lambda orientation: recognise_fillets(
            part, cyls=s.cyls, include_cylindrical=orientation is None
        ),
        scan_bosses=lambda: recognise_bosses(part, cyls=s.cyls),
        scan_polygonal_stock=lambda: recognise_polygonal_stock(part),
        scan_through_steps=lambda: recognise_through_steps(part),
        scan_plates=lambda: recognise_plates(part),
        scan_risers=lambda: recognise_risers(part),
        scan_step_levels=lambda: step_level_records(part),
    )
    _append_primary_feature_families(
        run,
        scan_double_d_bores=lambda: recognise_double_d_bores(part),
        scan_polygonal_bosses=lambda: recognise_polygonal_bosses(part),
    )
    _append_late_feature_families(
        run,
        scan_grooves=lambda: recognise_grooves(part, cyls=s.cyls),
        scan_plates=lambda: recognise_plates(part),
        scan_paired_ramp_steps=lambda: recognise_paired_ramp_steps(part),
        scan_flats=lambda: recognise_flats(part, cyls=s.cyls),
    )

    # STEP AP242 PMI enters the drafting-concept IR where possible.
    # Rendered directly by render_pmi; the planner adds nothing.
    run.features.extend(build_pmi_features(pmi, bbox))

    # The default location datum — the part's min-X/min-Y/min-Z corner (lower-left
    # in the plan view), per inspection practice. Hole location dims measure from
    # it.
    datums = [Datum(id="datum_xy", kind="point", at=(bbox.min.X, bbox.min.Y, bbox.min.Z))]
    model = PartModel(
        bbox=bbox,
        orientation=run.ctx.orientation,
        features=run.features,
        datums=datums,
        detected=True,
    )
    # Correlation runs after the complete geometry + PMI inventories exist, at the shared IR
    # waist. Direct drawings and emitted scripts consume the same lowered model.
    from draftwright.model.pmi_lowering import lower_ap242_dimensions

    return (
        lower_ap242_dimensions(
            model,
            feature_remap=run.ownership.remap_feature if run.ownership is not None else None,
        )
        if lower_pmi
        else model
    )
