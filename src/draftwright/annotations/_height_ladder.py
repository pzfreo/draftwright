"""Compiled height-ladder corridor candidates (ADR 1 and ADR 2)."""

from draftwright._core import _dim, _tol_suffix
from draftwright.annotations._common import (
    _LOC_SUBCHAIN,
    _SIZE_SUBCHAIN,
    PRIORITY,
    CorridorCandidate,
    dim_footprint,
    full_strip_message,
)
from draftwright.annotations._common import (
    register_corridor as _register_corridor,
)

_OVERALL_SUBCHAIN = 2


def register_height_ladder_candidates(
    dwg,
    frame,
    ctx,
    view,
    chain,
    order_values,
    short_rungs,
    overall,
    step,
    zspan,
    *,
    register_corridor=_register_corridor,
) -> int:
    """Submit the approved ladder to the shared corridor solve in chain order."""
    draft = dwg.draft
    _left, right, _bottom, _top = frame.edges(view)
    edge2 = right + 2
    tier = draft.font_size + 2 * draft.pad_around_text
    # Compose authored tolerances into the rendered label. Passing one as
    # `Dimension(tolerance=...)` renders nothing when an explicit label is supplied.

    # Key tolerances per rung. A ± on the `N× rise` representative would
    # claim the same tolerance for every level; plain ladder rungs each state
    # their own measurement and can carry their own tolerance.
    _tolerances = {c[0]: c[8] for c in chain}

    names = [c[0] for c in chain]
    sides = {
        name: (overall.rungs[0].side or "right")
        if name == "dim_height" and overall is not None
        else "right"
        for name in names
    }
    solved: dict[str, float] = {}
    for k, (
        name,
        zbase,
        ztop,
        label,
        _tsize,
        drop_msg,
        mid,
        per_unit,
        _rt,
        measurement_span,
    ) in enumerate(chain):
        side = sides[name]
        direction = 1 if side == "right" else -1
        edge = edge2 if side == "right" else _left - 2
        strip = frame.zones(view).right if side == "right" else frame.zones(view).left
        predecessors = [pn for pn in names[:k] if sides[pn] == side]

        def _witness_base(pos, predecessors=predecessors, direction=direction, edge=edge):
            base = edge
            for pn in reversed(predecessors):
                if pn in solved:
                    base = solved[pn]
                    break
            # A retry can revisit the inner position after a predecessor was built.
            # Prediction and rendering must use the same non-degenerate witness.
            return edge if direction * (pos - base) < 0.5 else base

        def _build(
            pos,
            name=name,
            zbase=zbase,
            ztop=ztop,
            label=label,
            witness_base=_witness_base,
            side=side,
            direction=direction,
            authored_side=overall.rungs[0].side
            if name == "dim_height" and overall is not None
            else None,
            per_unit=per_unit,
            _tol=_tolerances.get(name),
            measurement_span=measurement_span,
        ):
            base = witness_base(pos)
            solved[name] = pos
            dim = _dim(
                (base, zbase, 0),
                (base, ztop, 0),
                side,
                direction * (pos - base),
                draft,
                label=label + _tol_suffix(_tol, draft),
            )
            if per_unit is not None:
                # What this dimension's `N× v` label actually measures. Lint reads it in
                # preference to parsing the label, because `N× v` is drawn under two
                # conventions here and the string cannot tell them apart: this one is ONE
                # step, while a hole pitch spans the whole run. Same seam as `_dw_scale`.
                dim._dw_label_value = per_unit
            dim._dw_measurement_span = measurement_span
            if authored_side is not None:
                dim._dw_authored_side = authored_side
            return dim

        # The footprint measures the RENDERED string, so it carries the same suffix the
        # Dimension draws. Correctness, not a measured failure mode — see the note in
        # `render_envelope`; the invented "packs the strip too tightly" claim is withdrawn.
        def _foot(
            pos,
            zbase=zbase,
            ztop=ztop,
            label=label + _tol_suffix(_tolerances.get(name), draft),
            witness_base=_witness_base,
            side=side,
            direction=direction,
        ):
            # Predecessor-aware prediction: the conservative edge-anchored
            # witness can falsely exhaust the strip when an inner obstacle sits in the
            # already-traversed region. Use the build chain's witness calculation.
            base = witness_base(pos)
            return dim_footprint(
                (base, zbase, 0), (base, ztop, 0), side, direction * (pos - base), draft, label
            )

        def _drop(
            nm,
            drop_msg=drop_msg.replace("front-view", f"{view}-view").replace(
                "right strip", f"{side} strip"
            ),
            strip=strip,
            name=name,
            measurement=mid,
            measurement_span=measurement_span,
        ):
            solved.pop(name, None)
            # Name what filled the strip so the diagnosis shows the
            # lint message.
            msg = full_strip_message(drop_msg, dwg, strip, view, "x")
            ctx.record_issue(
                "error",
                "placement_unsatisfiable",
                msg,
                measurement=measurement,
                measurement_span=measurement_span,
                outcome_stage="placement",
            )

        register_corridor(
            ctx,
            (view, side),
            strip,
            view,
            "x",
            tier,
            CorridorCandidate(
                name=name,
                build=_build,
                # Steps stack inner→outer in chain order; the overall height rides the
                # OVERALL subchain so it lands outermost by construction (as the envelope
                # dims do). Ordinary rungs join the same value-ordered baseline run as
                # off-axis-hole heights, outside the inner boss-size run, instead
                # of retaining producer registration order.
                order=(
                    (_OVERALL_SUBCHAIN, 0, name)
                    if name == "dim_height"
                    else (_LOC_SUBCHAIN, order_values[name], name)
                ),
                on_place=lambda nm: None,
                on_drop=_drop,
                force=True,  # principal dims: only a physically full strip drops them
                # …and when it IS physically full, they outrank ordinary auto dims rather
                # than tying with them at 0 and losing on the generated key.
                priority=PRIORITY.PRINCIPAL,
                require_clear_ink=name.startswith("dim_step_"),
                feature=step
                if name != "dim_height"
                else overall.ref
                if overall is not None
                else None,
                measurement=mid,  # the rung's own compiled id
                footprint=_foot,
            ),
        )
    _register_short_rungs(
        dwg,
        frame,
        ctx,
        view,
        short_rungs,
        step,
        zspan,
        tier,
        _left,
        register_corridor=register_corridor,
    )
    return len(chain) + len(short_rungs)


def _register_short_rungs(
    dwg, frame, ctx, view, short_rungs, step, zspan, tier, left, *, register_corridor
) -> None:
    draft = dwg.draft
    left_edge = left - 2
    # A short rise needs external arrows, whose ink occupies most of the usual right-hand
    # height ladder. Solve these exceptional rungs in the equivalent left strip so they do
    # not make the mandatory overall height infeasible.
    for i, rung in enumerate(short_rungs):
        name = f"dim_step_{i}"
        zbase, ztop = zspan(rung)
        # Same composition as the main chain: this is the SHORT-RISE escape, solved in the left
        # strip because external arrows would swamp the right one. It is the same measurement
        # by another route, and it dropped the tolerance.
        label = rung.final_label + _tol_suffix(rung.tolerance, draft)

        def _build_left(pos, zbase=zbase, ztop=ztop, label=label, measurement_span=rung.span):
            dim = _dim(
                (left_edge, zbase, 0),
                (left_edge, ztop, 0),
                "left",
                left_edge - pos,
                draft,
                label=label,
            )
            dim._dw_measurement_span = measurement_span
            return dim

        def _drop_left(nm, measurement=rung.id, measurement_span=rung.span):
            msg = full_strip_message(
                "short step-height dimension dropped (front-view left strip full)",
                dwg,
                frame.zones(view).left,
                view,
                "x",
            )
            ctx.record_issue(
                "error",
                "placement_unsatisfiable",
                msg,
                measurement=measurement,
                measurement_span=measurement_span,
                outcome_stage="placement",
            )

        register_corridor(
            ctx,
            (view, "left"),
            frame.zones(view).left,
            view,
            "x",
            tier,
            CorridorCandidate(
                name=name,
                build=_build_left,
                order=(_SIZE_SUBCHAIN, i, name),
                on_place=lambda nm: None,
                on_drop=_drop_left,
                force=True,
                require_clear_ink=True,
                feature=step,
                measurement=rung.id,
                footprint=lambda pos, zbase=zbase, ztop=ztop, label=label: dim_footprint(
                    (left_edge, zbase, 0),
                    (left_edge, ztop, 0),
                    "left",
                    left_edge - pos,
                    draft,
                    label,
                ),
            ),
        )
