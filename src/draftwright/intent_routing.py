"""Deferred-intent records and route classification at Drawing's rank (ADR 1 / ADR 2).

Drawing owns recorded edits and validates authored dimension spans. This module
classifies a snapshot of those intents for the canonical placement stages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from draftwright._core import Analysis

# The add verbs that record intents (section/rotational target the whole part).
IntentKind = str


@dataclass
class Intent:
    """One deferred add-verb call, preserving the script's verb order and arguments."""

    kind: IntentKind
    feature: object | None
    kwargs: dict = field(default_factory=dict)


@dataclass(frozen=True)
class _IntentRouting:
    """How :meth:`Drawing.finalize` routes each recorded intent to an auto-pass solver (#426).

    The classification half of finalize (#590 split) decides the route of each recorded
    intent. ``section`` is the pre-planned section (or None);
    the ``*_ids`` are ``id()`` sets of the intents on each route; the ``only_*``/``*_feats`` are
    the feature sets the routed renderers receive.
    """

    section: object
    corridor_ids: set
    callout_ids: set
    dia_ids: set
    len_ids: set
    slot_ids: set
    height_ladder_ids: set
    #: `Drawing.overall_height()` intents — the bbox-fallback overall height, which has no
    #: feature to hang a `dimension(...)` intent on (#889).
    overall_height_ids: set
    step_position_ids: set
    explicit_envelope_height: bool
    user_dim_ids: set
    rotational_ids: set
    off_axis_loc_ids: set
    only_loc: set
    pinned_loc: set
    only_callout: set
    only_dia: set
    only_len: set
    slot_feats: set
    machined_ids_by_kind: dict
    pocket_pattern_ids: set
    slot_pattern_ids: set


def classify_intents(
    intents, model, a: Analysis | None, routable, user_dim_uses_corridor, machined_callout_kinds
) -> _IntentRouting:
    """Classify the recorded placement intents by route — the classification half of
    :meth:`Drawing.finalize` (#590 split). Classifies recorded intents without
    mutating drawing state or placing annotations. The supplied dimension predicate
    retains Drawing-owned span validation."""
    from draftwright.annotations.sections import feature_hole_keys
    from draftwright.model import PartModel, plan_sections

    # The section plan (if a section was recorded) — the ONE plan reserved before the
    # callout carve sees its row (Coupling A) and rendered last (Phase 3b).
    _section = None
    if routable and any(it.kind == "section" for it in intents):
        assert a is not None and isinstance(model, PartModel)
        _section = plan_sections(model, feature_hole_keys(model, a))
    # Route through the auto-pass solvers when possible (else everything live-replays):
    #  - BOTH-axes locate → the ADR 2 (was 0009) location corridor. An axes-restricted locate
    #    can't go through the per-feature filter, so it live-replays (#429).
    #  - hole/pattern CALLOUT → _annotate_holes' priority-drop/anchoring solve (the
    #    section row, if any, is reserved first below).
    #  - step/boss ø CALLOUT → render_diameters' row-below/column-left set-solve (Phase 4a).
    corridor_ids = {
        id(it)
        for it in intents
        if routable
        and it.kind == "locate"
        and it.kwargs.get("axes") is None
        # Z-plan holes only — render_locations places X/Y position dims. A side-drilled
        # (X/Y-axis) bore's location is a different pass (off_axis_loc_ids below).
        and (
            getattr(it.feature, "kind", None) == "circular_channel"
            or getattr(getattr(it.feature, "frame", None), "axis", None) == "z"
        )
    }
    # Side-drilled (X/Y-axis) hole locations (#133/#426): a separate whole-model pass
    # (_locate_off_axis_holes), placed at the shared drain like the Z-plan corridor —
    # not render_locations (Z-only, #133) and never live-replayed (add_feature_location
    # raises on non-Z; the intent is routed here before it can reach that verb).
    # NB: no ``axes is None`` guard, unlike corridor_ids — the off-axis pass ignores the
    # axes selector, so EVERY side-drilled locate (incl. a hand-written axes=… one) must
    # route here, else it would live-replay into add_feature_location's ValueError.
    off_axis_loc_ids = {
        id(it)
        for it in intents
        if routable
        and it.kind == "locate"
        and getattr(getattr(it.feature, "frame", None), "axis", None) in ("x", "y")
        and getattr(it.feature, "kind", None) != "circular_channel"
    }
    callout_ids = {
        id(it)
        for it in intents
        if routable
        and it.kind == "callout"
        and getattr(it.feature, "kind", None) in ("hole", "pattern")
    }
    dia_ids = {
        id(it)
        for it in intents
        if routable
        and it.kind == "callout"
        and getattr(it.feature, "kind", None) in ("step", "boss")
        and getattr(getattr(it.feature, "frame", None), "axis", None) in ("x", "y", "z")
    }
    # step LENGTH dimension intents (role="step") → render_step_lengths' chain (Phase 4b),
    # but only on a TURNED part (a.profiles is non-empty, mirroring the auto-pass guard) — else
    # they live-replay. Excludes the step's ø (a callout routed in dia_ids above).
    len_ids = {
        id(it)
        for it in intents
        if routable
        and a is not None
        and a.profiles
        and it.kind == "dimension"
        and getattr(it.feature, "kind", None) == "step"
        and getattr(getattr(it.feature, "frame", None), "axis", None) in ("x", "y", "z")
        and it.kwargs.get("param") == "length"
        and it.kwargs.get("role") == "step"
    }
    # SLOT/PAD dimension intents (#426 Phase 2b / #885 / #1752) → render_slots' shared
    # placement. Both record two linear size dims; an obround slot adds its radius
    # leader. Routing any of them regenerates the feature's approved dimensions. Slots
    # also regenerate their historical datum
    # position; pads use a separate locate() intent for their two-axis location.
    # Both share the location corridor,
    # so they register alongside B2's locations and drain in the SAME solve (the #345
    # dedup of a slot position coincident with a hole location needs one combined pass).
    # Match on param/role like len_ids above (#439): a slot exposes the two length
    # parameters plus an optional end radius, so a malformed slot dim (for example
    # dimension(slot, "diameter")) falls through to
    # live replay, where the verb raises the same ValueError instead of being swallowed.
    slot_ids = {
        id(it)
        for it in intents
        if routable
        and it.kind == "dimension"
        and getattr(it.feature, "kind", None) in ("slot", "pad")
        and (
            (
                it.kwargs.get("param") == "length"
                and it.kwargs.get("role")
                in ("slot_width", "slot_length", "pad_width", "pad_length")
            )
            or (
                getattr(it.feature, "kind", None) == "slot"
                and it.kwargs.get("param") == "radius"
                and it.kwargs.get("role") == "slot_end_radius"
            )
        )
    }
    # Prismatic height-ladder intent. StepLevelFeature exposes one value per interior
    # level, but those rungs are a correlated chain whose witness bases leapfrog from
    # the previous placed tier. Treat one semantic dimension intent as "rebuild the
    # whole height ladder" through the existing auto-pass renderer, instead of trying
    # to flatten each rung into an independent corridor candidate.
    height_ladder_ids = {
        id(it)
        for it in intents
        if routable
        and it.kind == "dimension"
        and getattr(it.feature, "kind", None) == "step_level"
        and it.kwargs.get("param") == "length"
        and it.kwargs.get("role") in (None, "step_height")
    }
    overall_height_ids = {id(it) for it in intents if it.kind == "overall_height"}
    explicit_envelope_height = any(
        routable
        and it.kind == "dimension"
        and getattr(it.feature, "kind", None) == "envelope"
        and it.kwargs.get("param") == "length"
        and it.kwargs.get("role") == "height"
        for it in intents
    )
    # Prismatic step POSITIONS (#555) — like the height ladder, one semantic intent
    # means "rebuild all shoulders" through render_step_positions, not per-shoulder
    # span dims (multiple shoulders share role="step_position" and can't be picked
    # apart by the span resolver).
    step_position_ids = {
        id(it)
        for it in intents
        if routable
        and it.kind == "dimension"
        and getattr(it.feature, "kind", None) == "step_level"
        and it.kwargs.get("param") == "length"
        and it.kwargs.get("role") == "step_position"
    }

    # User-authored generic feature dimensions with pin/priority join the shared
    # corridor directly. Slot and turned-step length dimensions keep their specialized
    # routes above, because those renderers regenerate correlated measurements as a set.
    already_routed = len_ids | slot_ids | height_ladder_ids | step_position_ids
    user_dim_ids = {
        id(it) for it in intents if user_dim_uses_corridor(it, routable, already_routed)
    }
    # Rotational furniture intent (#424/#426): the whole-model render_rotational —
    # no per-feature subset, so just the id set; it drains at the "rotational" slot.
    rotational_ids = {id(it) for it in intents if routable and it.kind == "rotational"}
    # Machined-feature leader callout intents (#148): pocket/pad-height/fillet/
    # paired-ramp/flat/chamfer/groove
    # callout()s (plate is a spanned dimension, routed via dimension(), not here). Bucketed
    # per kind so each drains at its own _PASS_SEQUENCE stage, restricted to the recorded
    # features via only= (per-feature, #811). The id union also joins `routed` so
    # live_replay skips these (they route through finalize).
    machined_ids_by_kind: dict = {}
    for it in intents:
        if routable and it.kind == "callout":
            k = getattr(it.feature, "kind", None)
            if k in machined_callout_kinds:
                machined_ids_by_kind.setdefault(k, set()).add(id(it))
    # Pocket-pattern callout()s (#841 outcome 3): one grouped callout + pitch furniture per
    # pattern. Drained at the pre-drain "pocket_patterns" _PASS_SEQUENCE slot (render places
    # the pitch dim directly and needs the strip room the post-drain machined callouts lack),
    # restricted to the recorded feature(s) via only=.
    pocket_pattern_ids = {
        id(it)
        for it in intents
        if routable
        and it.kind == "callout"
        and getattr(it.feature, "kind", None) == "pocket_pattern"
    }
    # Slot-pattern callout()s (#841): same as pocket patterns — one grouped callout + pitch
    # furniture, drained at the pre-drain "slot_patterns" _PASS_SEQUENCE slot, only=-restricted.
    slot_pattern_ids = {
        id(it)
        for it in intents
        if routable
        and it.kind == "callout"
        and getattr(it.feature, "kind", None) == "slot_pattern"
    }
    only_loc = {it.feature for it in intents if id(it) in corridor_ids}
    pinned_loc = {it.feature for it in intents if id(it) in corridor_ids and it.kwargs.get("pin")}
    only_callout = {it.feature for it in intents if id(it) in callout_ids}
    only_dia = {it.feature for it in intents if id(it) in dia_ids}
    only_len = {it.feature for it in intents if id(it) in len_ids}
    slot_feats = {it.feature for it in intents if id(it) in slot_ids}
    return _IntentRouting(
        section=_section,
        corridor_ids=corridor_ids,
        callout_ids=callout_ids,
        dia_ids=dia_ids,
        len_ids=len_ids,
        slot_ids=slot_ids,
        height_ladder_ids=height_ladder_ids,
        overall_height_ids=overall_height_ids,
        step_position_ids=step_position_ids,
        explicit_envelope_height=explicit_envelope_height,
        user_dim_ids=user_dim_ids,
        rotational_ids=rotational_ids,
        off_axis_loc_ids=off_axis_loc_ids,
        only_loc=only_loc,
        pinned_loc=pinned_loc,
        only_callout=only_callout,
        only_dia=only_dia,
        only_len=only_len,
        slot_feats=slot_feats,
        machined_ids_by_kind=machined_ids_by_kind,
        pocket_pattern_ids=pocket_pattern_ids,
        slot_pattern_ids=slot_pattern_ids,
    )
