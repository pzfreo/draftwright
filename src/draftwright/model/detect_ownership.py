"""Ownership decisions over one acquired recognition inventory.

These stages compare provider records and reconcile legacy projections. The geometry
recognition lifecycle remains in ``detect.build_part_model``.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from math import isfinite

from quiddity import (
    CircularBlindStep,
    FaceLevel,
    Fillet,
    RaisedPad,
    has_multi_axis_plates,
    project_step_shoulders,
)

from draftwright.model.declare import circular_blind_step
from draftwright.model.ir import PocketFeature


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


def _validate_aggregate_radius_ownership(
    recognition,
    *,
    fillets,
    blends,
    circular_blind_steps,
    fillets_supplied: bool,
    blends_supplied: bool,
    circular_blind_steps_supplied: bool,
) -> None:
    """Refuse partial overrides that would change the aggregate radius partition."""
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
        _circular_blind_step_ownership_key(record) for record in recognition.circular_blind_steps
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
        if not recognition.circular_blind_steps and not _preserves_ownership_with_unique_additions(
            supplied_circular_keys, aggregate_circular_keys
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
            ) or not _same_ownership_occurrences(supplied_circular_keys, aggregate_circular_keys):
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


@dataclass(frozen=True)
class ThroughStepOwnershipStage:
    side_pad_level_zs: set[float]
    lowered_through_steps: tuple
    lowered_through_step_ids: set[int]
    legacy_through_step_owners: dict
    through_leg_spans: tuple
    through_level_zs: tuple
    through_shoulder_sites: tuple


def resolve_through_step_ownership(
    *,
    bbox,
    step_zs,
    face_levels,
    pads,
    plate_zs_at_base,
    edge_floor_zs,
    profiles,
    risers,
    through_steps,
    ownership_plates,
    recognition_evidence,
    envelope_emittable,
    plate_owner_record_ids,
    legacy_complete,
    legacy_owners,
    leg_spans,
    level_zs,
    shoulder_sites,
) -> ThroughStepOwnershipStage:
    """Resolve exact legacy owners after pad and edge-floor exclusions."""

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
            id(step): plate_owner_record_ids(
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
        or not legacy_complete(
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
        owned_spans = leg_spans(lowered_through_steps)
        owned_levels = level_zs(lowered_through_steps)
        owned_shoulders = shoulder_sites(lowered_through_steps, bbox)
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
            and not legacy_complete(
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
        id(step): legacy_owners(
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
    through_leg_spans = leg_spans(lowered_through_steps)
    through_level_zs = level_zs(lowered_through_steps)
    through_shoulder_sites = shoulder_sites(lowered_through_steps, bbox)
    return ThroughStepOwnershipStage(
        side_pad_level_zs,
        lowered_through_steps,
        lowered_through_step_ids,
        legacy_through_step_owners,
        through_leg_spans,
        through_level_zs,
        through_shoulder_sites,
    )


@dataclass(frozen=True)
class PrismaticOwnershipStage:
    section_recesses: tuple
    section_recess_patterns: tuple
    recess_features: dict
    channels: tuple
    plates: object
    multi_plate: bool
    edge_floor_zs: set
    plate_zs_at_base: set
    risers: object
    pads: tuple
    step_zs: object
    face_levels: tuple
    through_stage: ThroughStepOwnershipStage


def prepare_prismatic_ownership(
    *,
    bbox,
    section_recesses,
    section_recess_patterns,
    recess_features,
    profiles,
    rotational,
    plates,
    risers,
    pads,
    step_zs,
    face_levels,
    through_steps,
    recognition_evidence,
    envelope_emittable,
    scan_plates,
    scan_risers,
    scan_step_levels,
    plate_owner_record_ids,
    legacy_complete,
    legacy_owners,
    leg_spans,
    level_zs,
    shoulder_sites,
) -> PrismaticOwnershipStage:
    """Prepare recess and plate projections before exact through-step ownership."""
    assert section_recesses is not None and section_recess_patterns is not None
    section_recesses = tuple(section_recesses)
    section_recess_patterns = tuple(section_recess_patterns)
    channels = tuple(
        source
        for source in section_recesses
        if getattr(recess_features.get(id(source)), "kind", None) == "channel"
    )
    pockets = tuple(
        source
        for source in section_recesses
        if getattr(recess_features.get(id(source)), "kind", None) == "pocket"
    )
    # A full-span floored gap also describes a monolithic centred rebate, whose two
    # shoulders are already owned as one correlated StepLevelFeature position set. The
    # #917 channel scheme applies only where plate recognition proves a multi-axis
    # U-bracket: base + walls. Use the same evidence as the plate-emission gate below so
    # the two domains cannot both dimension one profile.
    if not profiles and rotational is None and plates is None:
        plates = scan_plates()
    multi_plate = has_multi_axis_plates(plates or ())
    # Ownership evidence must be eligible to cross the recognition→IR boundary.  A lone
    # single-axis plate is deliberately not emitted below (it is staircase evidence, not the
    # multi-plate bracket grammar), so letting it preempt a ThroughStep would leave the claimed
    # leg with no IR owner at all.
    ownership_plates = (
        tuple(plates or ()) if not profiles and rotational is None and multi_plate else ()
    )
    # The same lowered edge-open pocket geometry controls step-floor ownership.
    edge_floor_zs = {
        feature.frame.origin[2] - feature.open_sign * feature.depth / 2
        for source in pockets
        for feature in (recess_features[id(source)],)
        if isinstance(feature, PocketFeature)
        and feature.depth_axis == "z"
        and feature.edge_anchored
    }
    plate_zs_at_base = {
        round(pl.hi, 3)
        for pl in ownership_plates
        if pl.axis == "z" and abs(pl.lo - bbox.min.Z) < 0.5
    }
    if risers is None:
        risers = scan_risers()
    # RaisedPad v2 is an all-principal-axis occurrence. Resolve it before lowering the legacy
    # Z-level grammar so a side-normal pad's two Z footprint edges cannot masquerade as
    # prismatic HEIGHT levels. Any omitted inventory was filled from the one aggregate above;
    # do not add a family rescan at this consumer boundary (ADR 3 (was 0017)).
    assert pads is not None
    pads = tuple(pads)
    # The detected orchestration supplies its filtered aggregate levels. A standalone
    # ``build_part_model`` has no aggregate, so obtain the same records once and use them for
    # BOTH ownership and emitted IR.  Suppressing a ThroughStep because a legacy owner exists
    # only as private evidence would return a model containing neither owner.
    if step_zs is None:
        standalone_face_levels = tuple(scan_step_levels())
        step_zs = tuple(level.z for level in standalone_face_levels)
        if face_levels is None:
            face_levels = standalone_face_levels
    face_levels = tuple(face_levels or ())

    through_stage = resolve_through_step_ownership(
        bbox=bbox,
        step_zs=step_zs,
        face_levels=face_levels,
        pads=pads,
        plate_zs_at_base=plate_zs_at_base,
        edge_floor_zs=edge_floor_zs,
        profiles=profiles,
        risers=risers,
        through_steps=through_steps,
        ownership_plates=ownership_plates,
        recognition_evidence=recognition_evidence,
        envelope_emittable=envelope_emittable,
        plate_owner_record_ids=plate_owner_record_ids,
        legacy_complete=legacy_complete,
        legacy_owners=legacy_owners,
        leg_spans=leg_spans,
        level_zs=level_zs,
        shoulder_sites=shoulder_sites,
    )
    return PrismaticOwnershipStage(
        section_recesses=section_recesses,
        section_recess_patterns=section_recess_patterns,
        recess_features=recess_features,
        channels=channels,
        plates=plates,
        multi_plate=multi_plate,
        edge_floor_zs=edge_floor_zs,
        plate_zs_at_base=plate_zs_at_base,
        risers=risers,
        pads=pads,
        step_zs=step_zs,
        face_levels=face_levels,
        through_stage=through_stage,
    )
