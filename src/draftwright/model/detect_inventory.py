"""Supplied and aggregate recognition inventories for the model adapter.

The public detector retains each standalone part-scan site and passes it as a
late-bound callback. This owner preserves the one aggregate projection and its
partial-inventory contracts before any feature crosses the IR waist.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, cast

from quiddity import (
    BoltCircle,
    HoleRecord,
    LinearArray,
    RectGrid,
    recognise_hole_patterns,
    recognise_oriented_slot_patterns,
    recognise_slot_patterns,
)

from draftwright._geometry import _classify_rotational_cylinders
from draftwright.model.detect_ownership import (
    _fillet_blend_ownership_keys,
    _same_fillet_blend_partition,
    _validate_aggregate_radius_ownership,
)
from draftwright.progress import stage


@dataclass
class DetectionInventory:
    holes: Sequence[HoleRecord] | None
    double_d_bores: Any
    patterns: Sequence[BoltCircle | LinearArray | RectGrid] | None
    bosses: Any
    polygonal_bosses: Any
    polygonal_stock: Any
    slots: Any
    slot_patterns: Any
    oriented_slots: Any
    oriented_slot_patterns: Any
    risers: Any
    chamfers: Any
    fillets: Any
    blends: Any
    circular_blind_steps: Any
    paired_ramp_steps: Any
    through_steps: Any
    gusset_ribs: Any
    gusset_rib_patterns: Any
    plates: Any
    grooves: Any
    flats: Any
    pads: Any
    section_recesses: Any
    section_recess_patterns: Any
    prof: Any
    profiles: Any
    step_zs: Any
    face_levels: Any
    rotational: Any
    cyls: Any
    blends_supplied: Any = None
    circular_blind_steps_supplied: Any = None
    derive_hole_patterns: Any = None
    derive_oriented_slot_patterns: Any = None
    derive_slot_patterns: Any = None
    fillets_supplied: Any = None
    needs_aggregate: Any = None
    ownership: Any = None
    recognition_evidence: Any = None
    recognition_result: Any = None


def prepare_inventory(s: DetectionInventory, *, handoff: Any, part: Any, unset: object) -> None:
    """Materialize supplied inventories and validate provenance before acquisition."""
    s.fillets_supplied = s.fillets is not None
    s.blends_supplied = s.blends is not None
    if s.fillets_supplied:
        s.fillets = tuple(s.fillets)
    if s.blends_supplied:
        s.blends = tuple(s.blends)
    handoff_matches = handoff is not None and handoff.part is part
    s.recognition_result = handoff.result if handoff_matches and handoff is not None else None
    s.recognition_evidence = handoff.evidence if handoff_matches and handoff is not None else None
    s.ownership = (
        handoff.ownership if s.recognition_result is not None and handoff is not None else None
    )
    s.circular_blind_steps_supplied = s.circular_blind_steps is not None
    if s.circular_blind_steps_supplied:
        s.circular_blind_steps = tuple(s.circular_blind_steps)
    if s.section_recesses is not None and s.section_recess_patterns is None:
        raise ValueError("injected section recesses require explicit section_recess_patterns")
    s.derive_hole_patterns = s.holes is not None and s.patterns is None
    s.derive_slot_patterns = s.slots is not None and s.slot_patterns is None
    # Pattern projection and conversion share one materialised caller inventory. A
    # generator would otherwise be exhausted before standalone records reach the adapter.
    if s.oriented_slots is not None:
        s.oriented_slots = tuple(s.oriented_slots)
    s.derive_oriented_slot_patterns = (
        s.oriented_slots is not None and s.oriented_slot_patterns is None
    )
    if s.prof is not unset and s.profiles is not unset:
        raise ValueError("supply profiles= or the compatible singular prof=, not both")
    s.needs_aggregate = (
        (s.prof is unset and s.profiles is unset)
        or s.step_zs is None
        or s.face_levels is None
        or any(
            inventory is None
            for inventory in (
                s.holes,
                s.double_d_bores,
                s.patterns,
                s.bosses,
                s.polygonal_bosses,
                s.polygonal_stock,
                s.slots,
                s.slot_patterns,
                s.oriented_slots,
                s.oriented_slot_patterns,
                s.risers,
                s.chamfers,
                s.fillets,
                s.blends,
                s.circular_blind_steps,
                s.paired_ramp_steps,
                s.through_steps,
                s.gusset_ribs,
                s.gusset_rib_patterns,
                s.plates,
                s.grooves,
                s.flats,
                s.pads,
                s.section_recesses,
                s.section_recess_patterns,
            )
        )
    )
    if (
        not s.needs_aggregate
        and s.fillets_supplied
        and s.blends_supplied
        and s.recognition_result is None
    ):
        raise ValueError(
            "fully supplied fillets and blends require aggregate recognition_result provenance"
        )
    if not s.needs_aggregate and s.recognition_result is not None:
        if not _same_fillet_blend_partition(
            _fillet_blend_ownership_keys(s.fillets, s.blends),
            _fillet_blend_ownership_keys(
                s.recognition_result.fillets, s.recognition_result.blends
            ),
        ):
            raise ValueError("fillets and blends must preserve aggregate ownership exactly")


def complete_inventory(
    s: DetectionInventory,
    *,
    bbox: Any,
    unset: object,
    scan_cylinders: Callable[[], Any],
    acquire_evidence: Callable[[Any, bool], Any],
) -> None:
    """Fill omitted families from one source-part-bound aggregate in fixed order."""
    # Turned-profile classification up front so the shared convert-context carries the
    # part's turning axis (the StepFeature span axis). Pure detection — no feature is
    # emitted here; the turned/boss branch below reads the same plural inventory.
    #
    # Any omitted family or classification input is filled from one public RecognitionResult,
    # preserving cross-family ownership for documented partial-inventory calls.  Derive the
    # aggregate's applicability flag from the shared cylinder substrate rather than probing a
    # public family first; the aggregate remains the only family orchestration (ADR 3 (was 0017)).
    if s.needs_aggregate:
        recognition = s.recognition_result
        if recognition is None:
            if s.cyls is None:
                s.cyls = scan_cylinders()
            centre = bbox.center()
            cylinder_class = _classify_rotational_cylinders(
                s.cyls,
                sizes=(bbox.size.X, bbox.size.Y, bbox.size.Z),
                centre=(centre.X, centre.Y, centre.Z),
            )
            with stage("recognition"):
                s.recognition_evidence = acquire_evidence(
                    s.cyls,
                    (
                        s.rotational is not None
                        or (s.profiles is not unset and bool(s.profiles))
                        or (s.prof is not unset and s.prof is not None)
                        or cylinder_class.is_rotational
                    ),
                )
            recognition = s.recognition_evidence.result
        else:
            s.cyls = recognition.cylinders
        _validate_aggregate_radius_ownership(
            recognition,
            fillets=s.fillets,
            blends=s.blends,
            circular_blind_steps=s.circular_blind_steps,
            fillets_supplied=s.fillets_supplied,
            blends_supplied=s.blends_supplied,
            circular_blind_steps_supplied=s.circular_blind_steps_supplied,
        )
        s.holes = recognition.holes if s.holes is None else s.holes
        s.double_d_bores = (
            recognition.double_d_bores if s.double_d_bores is None else s.double_d_bores
        )
        s.patterns = recognition.hole_patterns if s.patterns is None else s.patterns
        s.bosses = recognition.bosses if s.bosses is None else s.bosses
        s.polygonal_bosses = (
            recognition.polygonal_bosses if s.polygonal_bosses is None else s.polygonal_bosses
        )
        s.polygonal_stock = (
            recognition.polygonal_stock if s.polygonal_stock is None else s.polygonal_stock
        )
        s.slots = recognition.slots if s.slots is None else s.slots
        s.slot_patterns = recognition.slot_patterns if s.slot_patterns is None else s.slot_patterns
        s.oriented_slots = (
            recognition.oriented_slots if s.oriented_slots is None else s.oriented_slots
        )
        s.oriented_slot_patterns = (
            recognition.oriented_slot_patterns
            if s.oriented_slot_patterns is None
            else s.oriented_slot_patterns
        )
        s.risers = recognition.risers if s.risers is None else s.risers
        s.chamfers = recognition.chamfers if s.chamfers is None else s.chamfers
        s.fillets = recognition.fillets if s.fillets is None else s.fillets
        s.blends = recognition.blends if s.blends is None else s.blends
        s.circular_blind_steps = (
            recognition.circular_blind_steps
            if s.circular_blind_steps is None
            else s.circular_blind_steps
        )
        s.paired_ramp_steps = (
            recognition.paired_ramp_steps if s.paired_ramp_steps is None else s.paired_ramp_steps
        )
        s.through_steps = recognition.through_steps if s.through_steps is None else s.through_steps
        s.gusset_ribs = recognition.gusset_ribs if s.gusset_ribs is None else s.gusset_ribs
        s.gusset_rib_patterns = (
            recognition.gusset_rib_patterns
            if s.gusset_rib_patterns is None
            else s.gusset_rib_patterns
        )
        s.plates = recognition.plates if s.plates is None else s.plates
        s.grooves = recognition.grooves if s.grooves is None else s.grooves
        s.flats = recognition.flats if s.flats is None else s.flats
        s.section_recesses = (
            recognition.section_recesses if s.section_recesses is None else s.section_recesses
        )
        s.section_recess_patterns = (
            recognition.section_recess_patterns
            if s.section_recess_patterns is None
            else s.section_recess_patterns
        )
        s.pads = recognition.pads if s.pads is None else s.pads
        # Pattern inventories are projections of their supplied member inventories.  Preserve
        # that documented partial-input relationship instead of combining caller-owned members
        # with patterns derived from the aggregate's separately detected members.
        if s.derive_hole_patterns:
            s.patterns = recognise_hole_patterns(cast(Sequence[HoleRecord], s.holes))
        if s.derive_slot_patterns:
            s.slot_patterns = recognise_slot_patterns(s.slots)
        if s.derive_oriented_slot_patterns:
            s.oriented_slot_patterns = recognise_oriented_slot_patterns(s.oriented_slots)
        if s.prof is unset and s.profiles is unset:
            s.profiles = recognition.turned_profiles
        if s.step_zs is None:
            s.step_zs = recognition.step_ladder_for_z_span(bbox.min.Z, bbox.max.Z)
        if s.face_levels is None:
            s.face_levels = recognition.step_levels
        s.cyls = recognition.cylinders
