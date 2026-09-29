"""Deferred intent stage execution at Drawing's rank (ADR 1 / ADR 4).

Drawing owns the transaction and its private state. This module receives one explicit
per-drain state and runs the canonical annotation stage order against the public drawing
surface and the shared placement context.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from draftwright.annotations._common import annotation_ink_obstacles
from draftwright.intent_routing import Intent


@dataclass
class IntentDrainState:
    intents: list[Intent]
    detail_view: bool
    replay: Callable[[Intent], None]
    queue_dimension: Callable[..., bool]
    record_issue: Callable[..., None]


@dataclass
class _DrainRun:
    target: Any
    ctx: Any
    model: Any
    analysis: Any
    routing: Any
    state: IntentDrainState
    routable: bool
    queued_dim_ids: set[int]
    derived_identifiers: Any
    section: Any


def _prepare_drain(target, ctx, model, a, r, state: IntentDrainState) -> _DrainRun:
    from draftwright.annotations.sections import _has_rendered_section
    from draftwright.view_plan import DerivedViewIdentifierPool, derived_view_identifier

    routable = model is not None and a is not None
    queued_dim_ids: set = set()
    existing_identifiers = []
    for view_name in target.views:
        kind = (
            "section"
            if view_name.startswith("section_")
            else "detail"
            if view_name.startswith("detail_")
            else ""
        )
        identifier = derived_view_identifier(kind, view_name)
        if identifier is not None:
            existing_identifiers.append(identifier)
    derived_identifiers = DerivedViewIdentifierPool(existing_identifiers)
    section = r.section
    if section is not None and _has_rendered_section(target, section):
        section = None
    elif section is not None:
        section_label = derived_identifiers.allocate()
        if section_label is None:
            raise ValueError(
                "recorded section cannot be named: derived-view identifiers exhausted"
            )
        section = replace(section, label=section_label)
    return _DrainRun(
        target,
        ctx,
        model,
        a,
        r,
        state,
        routable,
        queued_dim_ids,
        derived_identifiers,
        section,
    )


def _report_authored_omissions(run: _DrainRun, features, before) -> None:
    """Say so when a recorded edit drew nothing because the AUTHOR omitted it.

    The round-6 defect (#921) was silence: the drain recorded the intent, drew
    nothing, dropped the intent unconditionally and reported success, so the edit
    vanished with no annotation, no pending intent and no warning. #925 replaced the
    pre-check that fixed it — a fourth hand-written prediction of what a callout
    would draw, and wrong for a feature with some measurements authored and some not
    — so the report has to move here, where "drew nothing" is observed rather than
    forecast, and matches what the live path records at the same moment.
    """
    from draftwright.model.compiled import compile_dimensions

    target, model, state = run.target, run.model, run.state
    if not features or model is None or model.authored_dimensions is None:
        return
    drawn = set(target.annotations()) - before
    for feature in features:
        if drawn & set(target.annotations_of(feature)):
            continue
        omission = next(
            (
                o
                for o in compile_dimensions(model).diagnostics
                if o.feature is feature and o.authored
            ),
            None,
        )
        if omission is not None:
            state.record_issue(
                "info",
                "authored_omission",
                f"the recorded edit for this {feature.kind} drew nothing: "
                f"{omission.parameter_id} is not in the authored dimension set — "
                "add a dimension(feature, role) line",
            )


def _early_stages(run: _DrainRun) -> dict[str, Callable[[], None]]:
    from draftwright.annotations.from_model import (
        render_local_turned_centerlines,
        render_locations,
        render_rotational,
    )
    from draftwright.annotations.holes import _annotate_holes, build_view_of_axis
    from draftwright.annotations.sections import _reserve_section_row, feature_hole_keys
    from draftwright.model import PartModel, plan_dimensions
    from draftwright.model.compiled import compile_dimensions

    target, ctx, model, a, r, state = (
        run.target,
        run.ctx,
        run.model,
        run.analysis,
        run.routing,
        run.state,
    )
    routable, section = run.routable, run.section

    def _s_rotational():
        # Rotational furniture — OD dim + axis centrelines + concentric-bore leaders —
        # through the shared whole-model render_rotational (#424/#426). Runs FIRST (its
        # "rotational" _PASS_SEQUENCE slot), so on a turned STEPPED part it places before
        # the diameter/callout stages exactly as the auto-pass does. No only= subset and
        # fixed literal output names (dim_od/centerline_*) ⇒ byte-identical to the auto
        # pass, so the reconstruction matches (== not ⊇, unlike the #424 diameter case).
        if r.rotational_ids:
            assert a is not None and isinstance(model, PartModel)
            render_rotational(target, compile_dimensions(model), a, ctx=ctx)
        # Generated/deferred reconstruction starts with auto_dims=False, so
        # add a non-rotational stepped stack's local axis here before the
        # location stages suppress a centered bore as axis-located (#881).
        if routable and (r.dia_ids or r.len_ids or r.off_axis_loc_ids):
            assert a is not None
            render_local_turned_centerlines(target, a, ctx=ctx)
        state.intents = [it for it in state.intents if id(it) not in r.rotational_ids]

    def _s_reserve_section():
        # Reserve the section's cutting-plane row BEFORE the callout carve so the
        # carve sees it as an obstacle (Coupling A, ADR 2 (was 0009) P5 strand 3); rendered
        # last (the "section" stage).
        if section is not None:
            assert a is not None
            _reserve_section_row(target, a, section, ctx=ctx)

    def _s_live_replay():
        # Live-replay every intent EXCEPT the routed callouts/locates and section
        # (furniture, step/boss callouts, dimensions, axes-restricted locates).
        routed = (
            r.corridor_ids
            | r.callout_ids
            | r.dia_ids
            | r.len_ids
            | r.slot_ids
            | r.height_ladder_ids
            | r.overall_height_ids  # drained by _s_height_ladder
            | r.step_position_ids
            | r.user_dim_ids
            | r.rotational_ids  # drained by _s_rotational; no _replay_intent branch
            | r.off_axis_loc_ids  # drained by the off-axis stages; locate() raises on non-Z
            | {i for ids in r.machined_ids_by_kind.values() for i in ids}  # _s_<machined kind>
            | r.pocket_pattern_ids  # _s_pocket_patterns (pre-drain)
            | r.slot_pattern_ids  # _s_slot_patterns (pre-drain)
        )
        i = 0
        while i < len(state.intents):
            it = state.intents[i]
            if it.kind == "section" or id(it) in routed:
                i += 1
                continue
            state.replay(it)  # resilient: a raise leaves the rest recorded
            state.intents.pop(i)

    def _s_hole_callouts():
        # Hole/pattern callouts through the REAL priority-drop/anchoring solve.
        # Furniture is owned by the replayed furniture() intents → place_furniture=False.
        before_callouts = set(target.annotations())
        if r.only_callout:
            assert a is not None and isinstance(model, PartModel)  # ⟹ routable
            _annotate_holes(
                target,
                a,
                build_view_of_axis(a),
                plan_dimensions(model, planned_views=tuple(target.views)),
                feature_hole_keys(model, a),
                ctx=ctx,
                plan=compile_dimensions(model, planned_views=tuple(target.views)),
                only=r.only_callout,
                place_furniture=False,
            )
        _report_authored_omissions(run, r.only_callout, before_callouts)
        # Drop the placed callout intents NOW — before the fallible later stages — so
        # a raise there can't re-route (and, via first-free hc_ naming, duplicate)
        # them on a retry.
        state.intents = [it for it in state.intents if id(it) not in r.callout_ids]

    def _s_locations():
        # Both-axes locations register into the SHARED location corridor with slots,
        # step positions and the height ladder — one crossing-free ladder, one drain
        # (the "drain" stage), so a slot position coincident with a hole location
        # dedups (#345).
        if r.only_loc:
            assert a is not None and isinstance(model, PartModel)  # ⟹ routable
            render_locations(
                target,
                compile_dimensions(model, planned_views=tuple(target.views)),
                a,
                ctx=ctx,
                only=r.only_loc,
                pinned=r.pinned_loc,
            )

    return {
        "rotational": _s_rotational,
        "reserve_section": _s_reserve_section,
        "live_replay": _s_live_replay,
        "hole_callouts": _s_hole_callouts,
        "locations": _s_locations,
    }


def _dimension_stages(run: _DrainRun) -> dict[str, Callable[[], None]]:
    from draftwright.annotations.from_model import (
        render_diameters,
        render_height_ladder,
        render_slots,
        render_step_lengths,
        render_step_positions,
    )
    from draftwright.annotations.holes import _locate_off_axis_holes
    from draftwright.annotations.sections import _request_prismatic_detail
    from draftwright.model import PartModel
    from draftwright.model.compiled import compile_dimensions

    target, ctx, model, a, r, state = (
        run.target,
        run.ctx,
        run.model,
        run.analysis,
        run.routing,
        run.state,
    )

    routable = run.routable

    def _s_off_axis_across():
        # Side-drilled holes' in-plane (side-below) locations — REGISTER-only, whole-model
        # (_locate_off_axis_holes takes the compiled plan's approved off-axis positions,
        # no only= subset); they place at the shared drain. Whole-model like the auto pass: commenting SOME dwg.locate lines
        # still redraws every side-drilled location; commenting them ALL empties the bucket.
        if r.off_axis_loc_ids:
            assert a is not None
            _locate_off_axis_holes(
                target,
                ctx,
                a,
                which="across",
                plan=compile_dimensions(model, planned_views=tuple(target.views)),
            )

    def _s_off_axis_along():
        # Side-drilled (X/Y-axis) hole HEIGHT locations — register-only, placed at the drain
        # (mirrors the auto pass's off_axis_along stage; after the envelope candidates).
        if r.off_axis_loc_ids:
            assert a is not None
            _locate_off_axis_holes(
                target,
                ctx,
                a,
                which="along",
                plan=compile_dimensions(model, planned_views=tuple(target.views)),
            )

    def _s_height_ladder():
        # Prismatic step-height ladder through the auto-pass renderer. (#636) This
        # only REGISTERS ladder candidates; they place at the drain. Their intents
        # drop there (with step positions), NOT here — a raise before the drain
        # leaves them recorded so a retry rebuilds the batch (#639).
        # Two things share this renderer, and they are gated separately (#889).
        #
        # The step-height LADDER is a `step_level` feature's correlated rungs, so one
        # recorded intent means "rebuild the whole chain". The OVERALL HEIGHT is envelope
        # furniture — and on a part with NO `EnvelopeFeature` it comes from the compiler's
        # bounding-box fallback, so there is no feature to record `dimension(env,
        # "length", role="height")` against. `Drawing.overall_height()` is the verb for
        # that case, and `r.overall_height_ids` is its intent.
        #
        # Deliberately NOT "draw it whenever the compiler approves one": `auto_dims=False`
        # means the verbs below add everything, so injecting an automatic dimension nobody
        # recorded breaks record-then-finalize == place-live, which is the whole contract
        # of the deferred path (caught by `test_finalize_replay_equals_live_placement`).
        if not (r.height_ladder_ids or r.overall_height_ids):
            return
        assert a is not None and isinstance(model, PartModel)
        from draftwright._core import layout_frame
        from draftwright.annotations.from_model import ladder_plan_for
        from draftwright.model.compiled import compile_dimensions

        render_height_ladder(
            target,
            # Projected to what was RECORDED. Passing the whole compiled plan once either
            # intent was present meant `overall_height()` alone also rebuilt the step
            # rungs — a dimension nobody asked for, and live/deferred divergence in the
            # one change relying on their equivalence (#934).
            #
            # `include_overall` is drawing state, so it is an input to the COMPILE
            # (whether the overall height is in the set) rather than something the
            # renderer decides after the fact. An explicit `role="height"` intent draws it
            # through the dimension path instead, so the compile must leave it out.
            ladder_plan_for(
                compile_dimensions(model, include_overall=not r.explicit_envelope_height),
                step_height=bool(r.height_ladder_ids),
                overall=bool(r.overall_height_ids),
            ),
            layout_frame(a),
            ctx=ctx,
            detail_view=state.detail_view,
        )

    def _s_step_positions():
        # Prismatic step positions (all shoulders) — registration-only, like the
        # ladder above; placed and dropped at the drain (#639).
        if r.step_position_ids:
            assert a is not None and isinstance(model, PartModel)
            from draftwright._core import layout_frame as _lf
            from draftwright.model.compiled import compile_dimensions as _cd2

            render_step_positions(target, _cd2(model), _lf(a), ctx=ctx)

    def _s_detail_request():
        # Prismatic step-height detail (#661): queue it exactly as the auto pass
        # does — gated on the build's persisted detail_view setting, firing only when
        # the ladder stage above recorded the "step"/"illegible" escalation
        # (_request_prismatic_detail's own check). Resolved in the "details" stage.
        if state.detail_view and routable:
            assert a is not None
            from draftwright.model.compiled import compile_dimensions as _cd

            _request_prismatic_detail(target, a, ctx=ctx, plan=_cd(model))

    def _s_diameters():
        # Step/boss ø diameters through render_diameters' set-solve (row-below /
        # column-left) — placed immediately, before the corridor drain, exactly as
        # the auto-pass runs it (#699 slice b — the old drain-first order gave the
        # deferred path different obstacle visibility).
        before_dia = set(target.annotations())
        if r.only_dia:
            assert a is not None and isinstance(model, PartModel)  # ⟹ routable
            from typing import cast as _cast_dia

            from draftwright.model.compiled import FeatureRef as _DiaRef
            from draftwright.model.compiled import compile_dimensions as _compile_dia
            from draftwright.model.ir import Feature as _DiaFeature

            assert all(isinstance(f, _DiaFeature) for f in r.only_dia)
            render_diameters(
                target,
                _compile_dia(model),
                a,
                ctx=ctx,
                only={_DiaRef(_cast_dia(_DiaFeature, f)) for f in r.only_dia},
            )
        _report_authored_omissions(run, r.only_dia, before_dia)
        state.intents = [it for it in state.intents if id(it) not in r.dia_ids]

    def _s_step_lengths():
        # Turned step-length CHAIN through render_step_lengths (N× collapse /
        # staggered tiers) — after diameters, as in the auto-pass.
        if r.only_len:
            assert a is not None and isinstance(model, PartModel)  # ⟹ routable
            from typing import cast as _cast_len

            from draftwright.model.compiled import FeatureRef as _LenRef
            from draftwright.model.compiled import compile_dimensions as _compile_len
            from draftwright.model.ir import Feature as _LenFeature

            assert all(isinstance(f, _LenFeature) for f in r.only_len)
            render_step_lengths(
                target,
                _compile_len(model),
                ctx=ctx,
                only={_LenRef(_cast_len(_LenFeature, f)) for f in r.only_len},
            )
        state.intents = [it for it in state.intents if id(it) not in r.len_ids]

    def _s_slots():
        # Slots regenerate width + length + optional end radius + the model-derived datum position (a
        # superset of the recorded slot intents — auto-pass parity by design) and
        # register into the shared solves. Planner-fed (#730): the width/length/radius
        # values + tolerances come from the plan, like the auto-pass.
        if r.slot_feats:
            assert a is not None and isinstance(model, PartModel)  # ⟹ routable
            render_slots(target, compile_dimensions(model), a, ctx=ctx, only=r.slot_feats)

    return {
        "off_axis_across": _s_off_axis_across,
        "off_axis_along": _s_off_axis_along,
        "height_ladder": _s_height_ladder,
        "step_positions": _s_step_positions,
        "detail_request": _s_detail_request,
        "diameters": _s_diameters,
        "step_lengths": _s_step_lengths,
        "slots": _s_slots,
    }


def _feature_stages(run: _DrainRun) -> dict[str, Callable[[], None]]:
    from draftwright.annotations.from_model import (
        render_blends,
        render_chamfers,
        render_circular_blind_steps,
        render_circular_channels,
        render_fillets,
        render_flats,
        render_grooves,
        render_hex_pockets,
        render_oriented_slots,
        render_pad_heights,
        render_paired_ramp_steps,
        render_pockets,
        render_rectangular_blind_slots,
        render_round_bottom_blind_slots,
    )
    from draftwright.annotations.holes import render_pocket_patterns, render_slot_patterns
    from draftwright.annotations.leaders import drain_feature_leaders
    from draftwright.annotations.orchestrator import drain_and_reconcile
    from draftwright.model import PartModel

    target, ctx, model, a, r, state = (
        run.target,
        run.ctx,
        run.model,
        run.analysis,
        run.routing,
        run.state,
    )

    routable, queued_dim_ids = run.routable, run.queued_dim_ids

    # Machined-feature leader callouts (#148): each recorded callout intent draws exactly
    # its own feature — the renderer is restricted to the surviving intents' features via
    # only= (the render_slots #426 Ph2b subset idiom), so commenting one dwg.callout line
    # drops that one feature (#811) while the full script reproduces the auto pass.
    # Each kind places directly at its own _PASS_SEQUENCE slot (after the drain). Plate is
    # NOT here — it is a spanned corridor dimension, not a direct leader (#811).
    def _s_machined(kind, render):
        ids = r.machined_ids_by_kind.get(kind, set())
        feats = {it.feature for it in state.intents if id(it) in ids}
        if feats:
            assert a is not None and isinstance(model, PartModel)  # ⟹ routable
            from typing import cast as _cast

            from draftwright.model.compiled import FeatureRef as _FR2
            from draftwright.model.compiled import compile_dimensions as _cd4
            from draftwright.model.ir import Feature as _Feature

            assert all(isinstance(f, _Feature) for f in feats)
            render(
                target,
                _cd4(model),
                a,
                ctx=ctx,
                only={_FR2(_cast(_Feature, f)) for f in feats},
            )
        state.intents = [it for it in state.intents if id(it) not in ids]

    def _s_chamfers():
        _s_machined("chamfer", render_chamfers)

    def _s_circular_blind_steps():
        _s_machined("circular_blind_step", render_circular_blind_steps)

    def _s_hex_pockets():
        _s_machined("hex_pocket", render_hex_pockets)

    def _s_circular_channels():
        _s_machined("circular_channel", render_circular_channels)

    def _s_fillets():
        _s_machined("fillet", render_fillets)

    def _s_blends():
        _s_machined("blend", render_blends)

    def _s_paired_ramp_steps():
        _s_machined("paired_ramp_step", render_paired_ramp_steps)

    def _s_flats():
        _s_machined("flat", render_flats)

    def _s_pockets():
        _s_machined("pocket", render_pockets)

    def _s_rectangular_blind_slots():
        _s_machined("rectangular_blind_slot", render_rectangular_blind_slots)

    def _s_round_bottom_blind_slots():
        _s_machined("round_bottom_blind_slot", render_round_bottom_blind_slots)

    def _s_oriented_slots():
        _s_machined("oriented_slot", render_oriented_slots)

    def _s_pad_heights():
        _s_machined("pad", render_pad_heights)

    def _s_grooves():
        _s_machined("groove", render_grooves)

    def _s_feature_leaders():
        if routable:
            assert a is not None
            drain_feature_leaders(target, a, ctx)

    def _s_pocket_patterns():
        # Pocket-pattern callouts + their pitch furniture (#841 outcome 3), restricted to the
        # recorded feature(s). Keyed "pocket_patterns" so run_stages fires it at that PRE-drain
        # _PASS_SEQUENCE slot — render_pocket_patterns places the pitch dim directly and needs
        # the strip room the post-drain machined callouts lack.
        feats = {it.feature for it in state.intents if id(it) in r.pocket_pattern_ids}
        if feats:
            assert a is not None and isinstance(model, PartModel)  # ⟹ routable
            from typing import cast as _cast_pp

            from draftwright.model.compiled import FeatureRef as _PPRef
            from draftwright.model.compiled import compile_dimensions as _compile_pp
            from draftwright.model.ir import Feature as _PPFeature

            assert all(isinstance(f, _PPFeature) for f in feats)
            render_pocket_patterns(
                target,
                _compile_pp(model),
                a,
                ctx=ctx,
                only={_PPRef(_cast_pp(_PPFeature, f)) for f in feats},
            )
        state.intents = [it for it in state.intents if id(it) not in r.pocket_pattern_ids]

    def _s_slot_patterns():
        # Slot-pattern callouts + their pitch furniture (#841), restricted to the recorded
        # feature(s). Keyed "slot_patterns" so run_stages fires it at that PRE-drain
        # _PASS_SEQUENCE slot (same reason as pocket patterns).
        feats = {it.feature for it in state.intents if id(it) in r.slot_pattern_ids}
        if feats:
            assert a is not None and isinstance(model, PartModel)  # ⟹ routable
            from typing import cast as _cast_sp

            from draftwright.model.compiled import FeatureRef as _SPRef
            from draftwright.model.compiled import compile_dimensions as _compile_sp
            from draftwright.model.ir import Feature as _SPFeature

            assert all(isinstance(f, _SPFeature) for f in feats)
            render_slot_patterns(
                target,
                _compile_sp(model),
                a,
                ctx=ctx,
                only={_SPRef(_cast_sp(_SPFeature, f)) for f in feats},
            )
        state.intents = [it for it in state.intents if id(it) not in r.slot_pattern_ids]

    def _s_user_dims():
        # User-authored pin/priority dimensions queue into the shared corridor as
        # first-class candidates (ADR 4 (was 0012)).
        used_dim_names: set[str] = set()
        for it in state.intents:
            if id(it) in r.user_dim_ids:
                if state.queue_dimension(it, a, ctx=ctx, used_names=used_dim_names):
                    queued_dim_ids.add(id(it))

    def _s_drain():
        # One drain places everything the register-only stages queued; the corridor-
        # routed intents are dropped only AFTER it succeeds, so a raise leaves them
        # recorded for a clean retry (#639). Includes the #690 label reconciliation,
        # shared verbatim with the auto-pass (drain_and_reconcile).
        if (
            r.only_loc
            or r.off_axis_loc_ids
            or r.slot_feats
            or r.user_dim_ids
            or r.step_position_ids
            or r.height_ladder_ids
            or r.overall_height_ids
        ):
            assert a is not None and isinstance(model, PartModel)  # either ⟹ routable
            drain_and_reconcile(ctx, target)
        state.intents = [
            it
            for it in state.intents
            if id(it)
            not in (
                r.corridor_ids
                | r.off_axis_loc_ids
                | r.slot_ids
                | queued_dim_ids
                | r.step_position_ids
                | r.height_ladder_ids
                | r.overall_height_ids
            )
        ]

    return {
        "chamfers": _s_chamfers,
        "circular_blind_steps": _s_circular_blind_steps,
        "circular_channels": _s_circular_channels,
        "hex_pockets": _s_hex_pockets,
        "fillets": _s_fillets,
        "blends": _s_blends,
        "paired_ramp_steps": _s_paired_ramp_steps,
        "flats": _s_flats,
        "pockets": _s_pockets,
        "rectangular_blind_slots": _s_rectangular_blind_slots,
        "round_bottom_blind_slots": _s_round_bottom_blind_slots,
        "oriented_slots": _s_oriented_slots,
        "pad_heights": _s_pad_heights,
        "grooves": _s_grooves,
        "feature_leaders": _s_feature_leaders,
        "pocket_patterns": _s_pocket_patterns,
        "slot_patterns": _s_slot_patterns,
        "user_dims": _s_user_dims,
        "drain": _s_drain,
    }


def _late_stages(run: _DrainRun) -> dict[str, Callable[[], None]]:
    from draftwright.annotations.orchestrator import _maybe_tabulate_holes
    from draftwright.annotations.sections import _add_section_view, _resolve_details
    from draftwright.model.compiled import compile_dimensions

    target, ctx, model, a, state = (
        run.target,
        run.ctx,
        run.model,
        run.analysis,
        run.state,
    )
    routable, section = run.routable, run.section
    derived_identifiers = run.derived_identifiers

    def _s_section():
        # Render the section, reusing the reserved plan. The room check carves the
        # view row into free segments and takes the leftmost that fits and clears
        # the title block (#1190) — it does NOT simply start past everything already
        # placed, which let one remote occupant veto the whole band.
        # `_add_section_view` clears the reservation and records the outcome. A
        # recorded section with no trigger (r.section is None) is a no-op.
        state.intents = [it for it in state.intents if it.kind != "section"]
        if section is not None:
            assert a is not None
            if not _add_section_view(target, a, section, ctx=ctx):
                derived_identifiers.release(section.label)

    def _s_details():
        # Resolve every queued enlarged-detail request (#661): the prismatic
        # request queued above and the crowded turned-head requests
        # render_step_lengths queues (the requests live only on this per-run
        # ctx, so an unresolved queue would die with it). Same position as the
        # auto pass: after the drain + section, so the detail's free-rectangle
        # search sees every placed annotation as an obstacle.
        if ctx.detail_requests:
            assert a is not None
            from draftwright.projection import _fit_iso_view, _project_iso

            # Mirror the auto pass's iso ordering (#661): there, details resolve
            # while the ordinary iso still stands at sheet scale (it is fitted into its
            # zone only after _auto_annotate returns), so the free-rectangle
            # search is not blocked by the grown iso. The finalize path inherits
            # the already-fitted iso from the build — re-project it at sheet
            # scale for the resolve, then refit it into its zone. An authored iso
            # scale is hard, so it is reprojected and retained at that exact factor.
            # The ordinary refit is deterministic:
            # same zone + geometry reproduce the build's fit; the #647 snapshot
            # covers views/_coords, so a raise mid-stage rolls the iso back too).
            if "iso" in target.views:
                _project_iso(target, a, a.SCALE * (a.planned_iso_scale or 1.0))
            _resolve_details(target, a, ctx=ctx, identifiers=derived_identifiers)
            if "iso" in target.views and a.planned_iso_scale is None:
                # Obstacles as on the build path (#1240) — inert today, since a details
                # refit disables the grow branch, but the call must not drift from the
                # builder's shape or the next grow-path change silently loses the cap.
                _fit_iso_view(target, a, obstacles=annotation_ink_obstacles(target))

    def _s_tabulate():
        # Dense-scattered plan-view holes escalate to the hole TABLE + balloon ring —
        # last, so the resolver sees the section + title block as obstacles. It reads
        # ctx.escalations (the callout/location drops collected above) and the
        # scattered-hole coverage recorded at the hole emit site even under
        # place_furniture=False (#426 Ph4c) to find + replace the plan callouts. The
        # density gate counts ALL analysis holes (a.holes), so this is a FULL-
        # reconstruction escalation: a partial hand-edit that drops some callout()
        # lines still tabulates the full count (#434); the escalations live only on
        # this per-run ctx (#639), discarded when finalize returns (#440).
        if routable:
            assert a is not None
            _maybe_tabulate_holes(target, a, ctx=ctx, plan=compile_dimensions(model))

    return {
        "section": _s_section,
        "details": _s_details,
        "tabulate": _s_tabulate,
    }


def drain_intents(target, ctx, model, a, r, state: IntentDrainState) -> list[Intent]:
    """Run deferred annotation stages in the canonical order.

    Drawing owns the snapshot, rollback, and transaction lifetime. This mutating
    half keeps the same staged intent drops and corridor drain as the auto pass.
    """
    from draftwright.annotations.holes import _coalesce_aligned_linear_pitch_dims
    from draftwright.annotations.orchestrator import retract_resolved_withholdings, run_stages
    from draftwright.model.compiled import compile_dimensions

    run = _prepare_drain(target, ctx, model, a, r, state)
    stages = _early_stages(run) | _dimension_stages(run) | _feature_stages(run) | _late_stages(run)
    run_stages(stages)
    if a is not None:
        _coalesce_aligned_linear_pitch_dims(target, a, ctx=ctx)
    # The same close-out the auto pass runs. A withholding is recorded by the pass that
    # could not place the mark and must be withdrawn if a later stage drew it — and the
    # declared route runs its own copy of the stage list, so leaving the retraction on the
    # auto path alone made the two fail in OPPOSITE directions: auto retracted, declared
    # never did, and `_crowded_staircase` finalised with every rung on the sheet and the
    # build still claiming one was withheld. That is an ADR 4 (was 0011) round-trip parity break
    # (#1216).
    if model is not None:
        retract_resolved_withholdings(target, ctx, compile_dimensions(model))
    return state.intents
