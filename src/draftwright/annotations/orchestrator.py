"""The annotation orchestrator (#138 / ADR 0005, P5e).

`_auto_annotate` is the single entry point: it builds the IR (`build_part_model`),
plans the dimensions once, and drives the IR renderers (`from_model.render_*`) +
the capability passes (annotations.{sections,turned,pmi,holes}) + the title block.
Takes a duck-typed `dwg` and the `Analysis` namespace `a`. Imports only `_core`,
`layout`, the annotations passes, and third-party libs -- never make_drawing --
so the module graph stays a DAG.

The OD/centreline/bore furniture and the prismatic step-height + overall-height
ladder are now IR renderers (`render_rotational`/`render_height_ladder`, #237), not
inline. What remains inline is the orchestration/classification glue (concentric
bore set, side-drilled locations, the hole table) + the section/PMI passes.
"""

from __future__ import annotations

from types import SimpleNamespace

from draftwright._core import (
    _TABULATE_MIN_HOLES,
    Analysis,
    HoleRef,
    _add_projection_symbol,
    _add_sheet_frame,
    _add_title_block,
    _add_zone_grid,
    _concentric_with_axis,
    _fmt,
    _iso_bbox,
    _log,
    _tag_sequence,
    _tol_suffix,
    _wrap_rows,  # noqa: F401 — re-exported via the annotate facade (#700: one copy, in _core)
    layout_frame,
)
from draftwright.analysis import _sizing_bores
from draftwright.annotations._common import (
    PlacementContext,
    _annotation_hole_features,
    _discard_attempt_annotations,
    _fully_ballooned_features,
    _hole_location_coverage_fact,
    _hole_table_replaceable_annotation,
    _hole_table_replaceable_feature,
    _hole_table_replaceable_location_annotation,
    _register_hole_table_coverage,
    _restore_annotation_transaction,
    _snapshot_annotation_transaction,
    _stash_annotations,
)
from draftwright.annotations.balloons import render_balloons
from draftwright.annotations.from_model import (
    ladder_plan_for,
    render_boss_diameters,
    render_boss_heights,
    render_centermarks,
    render_chamfers,
    render_diameters,
    render_envelope,
    render_fillets,
    render_flats,
    render_gdt,
    render_grooves,
    render_height_ladder,
    render_local_turned_centerlines,
    render_locations,
    render_plates,
    render_pmi,
    render_pockets,
    render_polygonal_bosses,
    render_polygonal_stock,
    render_rotational,
    render_slots,
    render_step_lengths,
    render_step_positions,
)
from draftwright.annotations.holes import (
    _annotate_holes,
    _locate_off_axis_holes,
    build_view_of_axis,
    render_pocket_patterns,
    render_slot_patterns,
)
from draftwright.annotations.leaders import drain_feature_leaders
from draftwright.annotations.sections import (
    _add_section_view,
    _request_prismatic_detail,
    _reserve_section_row,
    _resolve_details,
    feature_hole_keys,
)
from draftwright.model import (
    Frame,
    HoleFeature,
    PatternFeature,
    RotationalFeature,
    build_part_model,
    plan_dimensions,
    plan_sections,
)
from draftwright.model.compiled import compile_dimensions, resolve_feature
from draftwright.repair import reconcile_witness_labels

# ── the ONE auto-pass stage sequence (#699 slice b) ──────────────────────────
# The canonical order of the annotation stages. `_auto_annotate` executes it, and
# `Drawing._drain_intents` (the finalize drain) walks the SAME tuple for its routed
# subset — so a reordering here reorders BOTH build paths, and the hand-mirrored
# "mirroring the auto-pass" divergence class is gone by construction. Stages a path
# does not run are simply absent from its dict ("live_replay"/"user_dims" are
# finalize-only; most render stages are auto-only); an unknown stage key is an
# assertion error in both consumers. Order matters through four mechanisms (see
# the #699 slice-b analysis): immediate placers read live occupancy at call time,
# corridor drain order follows key-creation order, some registrations read strip
# state, and post-drain fallbacks run in registration order.
_PASS_SEQUENCE: tuple[str, ...] = (
    "rotational",
    "centermarks",
    "reserve_section",
    "live_replay",  # finalize-only: recorded verbs replay before the routed solves
    "hole_callouts",
    "locations",
    "height_ladder",
    "plates",
    "step_positions",
    "off_axis_across",
    "envelope",
    "detail_request",
    "boss_diameters",  # documented invariant: before "diameters" (ø then 'mentioned')
    "polygonal_bosses",
    "boss_heights",
    "diameters",
    "step_lengths",
    "off_axis_along",
    "slots",
    "pocket_patterns",  # a pocket ARRAY: grouped callout + pitch dim, placed pre-drain like a
    # hole pattern (the pitch dim needs strip room the post-drain decoration slots lack)
    "slot_patterns",  # a through-slot ARRAY: same grouped callout + pitch, same pre-drain reason
    "user_dims",  # finalize-only: pin/priority dims queue into the shared corridor
    "gdt",
    "pmi",
    "drain",
    # Best-effort machined-feature leader DECORATION places after the drain (#733,
    # generalising the grooves precedent): a principal dim that registers early but
    # places at the drain must never have its strip stolen by an immediate callout —
    # pre-#636 the ladder's early placement enforced this implicitly; post-#636 the
    # ordering must. The callouts' clear-room check sees the full drained occupancy
    # and yields (drops with a warning) where a principal dim now sits.
    "chamfers",
    "fillets",
    "flats",
    "pockets",
    "grooves",
    # One compatible same-view feature-leader inventory (#1166): side/plan
    # hole leaders collected before the corridor drain and the five machined
    # leader passes collected after it commit together here.
    "feature_leaders",
    "section",
    "details",
    "title_block",
    "tabulate",
    "sheet_frame",
    "zone_grid",
    "projection_symbol",
)


def run_stages(stages: dict, sequence: tuple[str, ...] | None = None) -> None:
    """Run the *stages* a path implements in the canonical *sequence* order
    (``_PASS_SEQUENCE``, resolved at call time so a test/instrumentation rebinding
    is honoured — Codex review).

    The shared executor of the one pass list (#699 slice b): both build paths hand
    their name→thunk dict here, so neither can run a stage the sequence does not
    name (assertion) nor in an order of its own."""
    if sequence is None:
        sequence = _PASS_SEQUENCE
    unknown = set(stages) - set(sequence)
    assert not unknown, f"stages not in _PASS_SEQUENCE: {sorted(unknown)}"
    for name in sequence:
        fn = stages.get(name)
        if fn is not None:
            fn()


def drain_and_reconcile(ctx, dwg) -> None:
    """Solve every registered corridor once (ADR 0009 end state, #345/#346/#393),
    then reconcile witness-crossing labels (#690) — the drain step both build
    paths share verbatim (#699 slice b). ``drain_corridors`` is resolved at call
    time (as the pre-#699 function-level imports did), so the #647 transactional-
    rollback tests can still inject a drain failure via ``_common``."""
    from draftwright.annotations import _common

    _common.drain_corridors(ctx, dwg)
    reconcile_witness_labels(dwg)


def _concentric_bore_diams(a: Analysis) -> list:
    """Distinct bore diameters on the rotation axis, in z_diams order (#10).

    Delegates to :func:`analysis._sizing_bores` so the render-time model built here
    uses the SAME bore set the pre-scale sizing model used (#584 WP1 A). ``a.z_diams``
    carries every Z cylinder diameter (incl. off-axis ones); the set is restricted to
    diameters with an *internal* centreline Z cylinder, OD excluded, in z_diams order.
    """
    z_cyls, _ = a.cyls
    return _sizing_bores(z_cyls, a.z_diams, a.od_diam, a.cx, a.cy)


def build_model(a: Analysis):
    """Build the ADR-0008 :class:`PartModel` from an analysis — the detected feature
    inventory (a pure function of *a*).

    Extracted from :func:`_auto_annotate` so the pipeline can build the model
    **regardless of** ``auto_dims`` (#398): the read surface :meth:`Drawing.model` and
    feature-referenced edits must work even in manual mode, where the annotation pass
    never runs. Concentric bore leaders are a Z-axis construction (bores on the vertical
    rotation axis); a horizontal (X/Y) round body gets OD + centrelines only (#222), so
    its bore set is empty.
    """
    _bores = tuple(_concentric_bore_diams(a)) if a.is_rotational and a.od_axis == "z" else ()
    # This is the DETECTED path's model construction — `_auto_annotate` calls it only when the
    # caller declared no model, and a detected build always has the aggregate. On the declared
    # path `a.recognition` is None (#1022) and there is nothing here to build from, so an
    # absent aggregate means the caller reached this from somewhere it should not have.
    assert a.recognition is not None, (
        "build_model needs the recognition aggregate, which a declared build does not have — "
        "the declared path uses the caller's model (dwg.model()) instead of building one."
    )
    return build_part_model(
        a.part,
        holes=a.holes,
        double_d_bores=a.recognition.double_d_bores,
        patterns=a.patterns,
        bosses=a.bosses,
        polygonal_bosses=a.recognition.polygonal_bosses,
        polygonal_stock=a.recognition.polygonal_stock,
        channels=a.recognition.channels,
        slots=a.slots,
        # The engine's SECOND build_part_model call site; unthreaded, these three were
        # detected again here after _analyse had already recognised them (#1019). Read off
        # the run's one RecognitionResult (ADR 0017); `a.pockets`/`a.pads`/
        # `a.pocket_patterns` are list copies of the same records and would do as well —
        # #1024 collapses that duplication.
        slot_patterns=a.recognition.slot_patterns,
        grooves=a.recognition.grooves,
        risers=a.recognition.risers,
        chamfers=a.recognition.chamfers,
        fillets=a.recognition.fillets,
        plates=a.recognition.plates,
        flats=a.recognition.flats,
        pockets=a.recognition.pockets,
        pocket_patterns=a.recognition.pocket_patterns,
        pads=a.recognition.pads,
        prof=a.prof,
        step_zs=a.step_zs,
        face_levels=a.recognition.step_levels,
        rotational=(a.od_diam, _bores, a.od_axis) if a.is_rotational else None,
        pmi=a.pmi,
        cyls=a.cyls,
    )


def build_rotational_feature(a: Analysis):
    """The rotational furniture feature (OD + centrelines + concentric bores) for *a*,
    or ``None`` when the part isn't rotational. Mirrors the ``rotational`` branch of
    :func:`build_model` / ``detect.build_part_model`` (detect.py) so the declared-model
    path can synthesise the same furniture detection produces: a declared turned shaft
    otherwise renders with no centrelines and its OD as a leader, not a dimension (#472).
    Concentric bores are a Z-axis construction (as in :func:`build_model`)."""
    if not a.is_rotational or a.od_diam is None:
        return None
    bores = tuple(_concentric_bore_diams(a)) if a.od_axis == "z" else ()
    c = a.part.bounding_box().center()
    return RotationalFeature(frame=Frame((c.X, c.Y, c.Z), a.od_axis), od=a.od_diam, bores=bores)


def _declared_feature_keys(groups, a: Analysis) -> set:
    """The :class:`HoleRef` position keys of every DECLARED hole/pattern member (ADR 0011
    #448), so a caller-declared hole/pattern renders at its declared position even where
    detection missed it. Mirrors the member source (``feat.members or g.anchor``) and the
    rotational concentric-bore exclusion of the ``_annotate_holes`` filter so the callout
    gate matches exactly — an on-axis bore stays excluded (dimensioned by the ldr_z
    centreline)."""
    keys: set = set()
    for g in groups:
        feat = g.feature
        if not isinstance(feat, HoleFeature | PatternFeature):
            continue
        for m in feat.members or (g.anchor,):
            if a.is_rotational and feat.frame.axis == "z" and _concentric_with_axis(a, m[0], m[1]):
                continue
            keys.add(HoleRef.of(m))
    return keys


def _auto_annotate(dwg, a: Analysis, *, detail_view: bool = False):
    """Add the standard automatic dimensions, centrelines, and title block.

    Returns the compiler's omission diagnostics — every measurement it considered and did not
    approve, with the rule that stopped it (#996). RETURNED rather than written onto the
    drawing: #830 removed the last engine caller that reached for `dwg._attach_*`, and
    `builder._assemble` is BuildState's single fill site (ADR 0005 §2 / #639). Handing them
    back keeps both true — the record outlives the build without `annotations/` touching the
    drawing's privates.
    """
    # Per-run placement scratch (detail requests / escalations / corridor batch) + references to
    # the drawing's build-state stores (registry/coverage), threaded to the passes instead of hung
    # on the Drawing (ADR 0005 §2, #639). Fresh each auto-pass; the corridor batch is drained once
    # at the end (drain_corridors, #345/#346).
    ctx = PlacementContext(
        registry=dwg.registry,
        coverage=dwg.coverage,
        items=dwg.items,  # #817: passes place via ctx.place, not dwg.add
        # The opt-in solve-trace recorder (#736), attached to the drawing's build state
        # by the builder; getattr because dwg is duck-typed in tests. None = off.
        trace=getattr(dwg, "solve_trace", None),
        feature_leaders=[],
    )
    if ctx.trace is not None:
        ctx.trace.begin_phase("auto")  # one phase per annotate run (repack re-runs this)
    # Idempotent: clear build-time lint state so a second annotation pass does
    # not accumulate duplicate drop records.
    ctx.reset_issues()
    ctx.coverage.reset_dropped()

    # Tighten right-strip outer_limits to the actual iso view left edge now
    # that the iso has been projected and fitted.  Always apply so that any
    # future allocations are bounded; warn when the cursor has already passed
    # the limit (dims already placed may overlap the iso view).
    _iso_x0, _iso_y0, _iso_x1, _iso_y1 = _iso_bbox(dwg)
    _iso_x_limit = _iso_x0 - 4
    # Only tighten a right strip when the iso shares the strip's y-range: a strip
    # that abuts the iso horizontally would otherwise lose annotation space, while
    # one sitting entirely above/below the iso (e.g. the SV strip when the iso is
    # in an upper-right zone) must keep its full width — capping it could push the
    # outer_limit below the strip anchor and break all its allocations.
    _right_strips = []
    for _rs, _y0, _y1 in (
        (a.fv_zones.right, a.FV_Y - a.fv_hh, a.FV_Y + a.fv_hh),
        (a.pv_zones.right, a.PV_Y - a.pv_hh, a.PV_Y + a.pv_hh),
        (a.sv_zones.right, a.SV_Y - a.fv_hh, a.SV_Y + a.fv_hh),
    ):
        if _y0 < _iso_y1 and _iso_y0 < _y1:
            _right_strips.append(_rs)
    for _rs in _right_strips:
        _rs.outer_limit = min(_rs.outer_limit, _iso_x_limit)

    # The same guard for the page-reaching ABOVE strips (#1240). The right strips have been
    # iso-clamped since the zone carve; the above strips had only the per-pass m_locy clamp,
    # so every other above-strip resident — GD&T frames, grid pitch dims, #1236's overall-
    # extent fallthrough — could stack under the iso unchecked: `strip_obstacles` is
    # annotations-only by documented design (views enter `late_furniture_obstacles`, not the
    # strip carve), so nothing else stood between them. Same x/y transposition of the same
    # rule, same overlap guard, same reason for it: clamping a strip the iso does not
    # horizontally overlap would cost annotation space for nothing. The m_locy clamp stays —
    # it fires earlier (a 14 mm approach buffer, not bbox overlap) and is strictly tighter.
    _iso_y_limit = _iso_y0 - 4
    for _as, _x0, _x1 in (
        (a.pv_zones.above, a.PV_X - a.fv_hw, a.PV_X + a.fv_hw),
        (a.sv_zones.above, a.SV_X - a.sv_hw, a.SV_X + a.sv_hw),
    ):
        # Clamp only when the iso is actually IN the strip's path — x-overlapping AND above
        # the strip anchor. The right-strip block's warning transposes exactly: an iso that
        # x-overlaps the view from BELOW it (a lower-right zone beside a wide plan) would
        # otherwise push the outer_limit beneath the anchor and break every allocation — four
        # hole/slot fixtures lost their location dims to precisely that in the first cut.
        if _x0 < _iso_x1 and _iso_x0 < _x1 and _iso_y_limit > _as.anchor:
            _as.outer_limit = min(_as.outer_limit, _iso_y_limit)

    # Per-hole annotations from the feature records (#91, #92, #95): each
    # hole is annotated in the view its axis is normal to.
    # to_page maps a model-space *location* (x, y, z) → page coords (IR-typed, not a
    # recogniser Hole — ADR 0008 Amendment 6).
    view_of_axis = build_view_of_axis(a)

    # The part model — the IR-migrated passes (centre marks, turned diameters/lengths)
    # render from it (ADR 0008 convergence / #229). Built once by the pipeline
    # (:func:`build_model`) and filled into BuildState at builder._assemble's single
    # construction site, so the read surface (dwg.model()) works on every real path; the
    # `build_model(a)` fallback covers a direct caller that reached _auto_annotate without a
    # model. #830: this pass no longer RE-attaches the model onto the drawing (the redundant
    # dwg._attach_part_model was the only engine caller) — it threads the ensured model onto
    # the run's ctx so every pass reads it from ctx, not the drawing's privates (#639).
    _model = dwg.model() if dwg.model() is not None else build_model(a)
    ctx.part_model = _model
    ctx.model_declared = dwg.model_declared
    # Plan the dimensions ONCE and thread the groups to every renderer that reads them
    # (was recomputed per renderer, #275). One rule set over DimParameters, literally.
    _groups = plan_dimensions(_model, planned_views=a.planned_views)
    # ONE compiled plan, shared by every migrated consumer (#923 review round 4).
    # Compiling per stage ran the compiler three times and, worse, let the direct
    # ladder, the shoulders and the detail escalation each hold a separately
    # derived decision — three chances to disagree about one drawing.
    _compiled = compile_dimensions(_model, groups=_groups)
    # Placement may release compiler-approved alternatives. Keep that runtime selection
    # separate from the immutable base plan consumed by every ordinary stage.
    _runtime_plan = _compiled

    # Hole callouts, location dims, and the section view fire on *feature
    # presence*, independent of the turned/prismatic class (#10): the
    # classification only selects the base set (OD+centreline+ldr_z vs envelope
    # dims).  A turned flange (round OD + a bolt circle) must get BOTH.
    #
    # On a turned part the concentric, axis-aligned bores are already
    # dimensioned by the ldr_z leaders, so they are excluded here to avoid a
    # duplicate hole callout; only the off-axis features get callouts.  On a
    # prismatic part every hole flows through unchanged.
    # The surviving feature holes' *positions* (concentric bores excluded on rotational
    # parts) — the IR gates callouts/furniture/sections on membership in this set, so no
    # recogniser Hole object crosses into the renderers (Amendment 6, #263/#207).
    # feature_hole_keys reads the IR (`_model.features`) — the single source shared with
    # the section() add verb (#420 / #584 WP1) and the off-axis location pass, which now
    # derives its own side-drilled holes from the IR too (subsystem B3).
    feature_keys = feature_hole_keys(_model, a)
    # ADR 0011 #448: when the caller DECLARED the model (model=), a hole/pattern renders at
    # its declared position even where detection missed it — source the callout membership
    # set from the declared IR groups too, not only a.holes. A no-op for the detection-only
    # path (gated on the declared flag; and on a fully-detected declared part the declared
    # keys already coincide with the detected ones).
    declared_keys: set = set()
    if ctx.model_declared:
        declared_keys = _declared_feature_keys(_groups, a)
        feature_keys = feature_keys | declared_keys
    # Decide the section trigger + cut-plane row now (pure function of _model/
    # feature_keys, no placement dependency); the "reserve_section" stage reserves
    # its row and the "section" stage renders it.
    _section = plan_sections(_model, feature_keys)

    # ── the stage thunks, run in _PASS_SEQUENCE order (#699 slice b) ─────────
    def _s_rotational():
        # Rotational furniture — OD dim + axis centrelines + concentric bore leaders — IR
        # renderer (#237), placed early like the engine's inline block it replaces.
        render_rotational(dwg, _compiled, a, ctx=ctx)
        # A stepped round stack on an otherwise non-rotational flange still needs
        # its local axis shown before centered-bore offsets can be suppressed (#881).
        render_local_turned_centerlines(dwg, a, ctx=ctx)

    def _s_centermarks():
        # Centre marks for every hole (all part classes) — IR renderer.
        render_centermarks(dwg, _groups, ctx=ctx)

    def _s_reserve_section():
        # Reserve the cutting-plane arrows' row BEFORE the plan-view hole callouts
        # place (ADR 0009 P5 strand 3) — the section itself still renders last (its
        # own room check clears everything else placed), this only gives the (now
        # strip_obstacles-aware) callout carve a real obstacle to see and, where a
        # cheap relocation exists, avoid — instead of an invisible one it could
        # never even detect. When avoiding would cost a large relocation, policy B
        # keeps the callout at its natural position and accepts the crossing rather
        # than pay that cost or drop it (holes.py); the `bracket` fixture's known
        # hc_plan0/section_arrow_right overlap (tests/test_layout_cleanliness.py)
        # is exactly this accepted case.
        _reserve_section_row(dwg, a, _section, ctx=ctx)

    def _s_hole_callouts():
        # Any hole/pattern member (declared holes render even where detection missed them).
        if feature_keys:
            _annotate_holes(dwg, a, view_of_axis, _groups, feature_keys, ctx=ctx, plan=_compiled)

    def _s_locations():
        # Hole location dims — IR renderer (planner picks the refs + datum, #238); placed
        # through the existing above-view strips. Replaces the engine's _add_location_dims.
        render_locations(dwg, _compiled, a, ctx=ctx)
        if a.cross_diams and a.is_rotational and not feature_keys:
            _log.info(
                "Cross-hole ø%s detected but not annotated (requires section view)",
                _fmt(a.cross_diams[0]),
            )

    def _s_height_ladder():
        # Front-view right ladder: prismatic step heights + overall height — IR renderer,
        # through fv_zones.right preserving the leapfrog cursor (#237). Replaces the inline
        # dim_step_* + dim_height; the turned step-length chain (render_step_lengths) handles
        # turned parts, and a Z-turned overall height is suppressed there (ISO 129).
        # The ADR 0016 boundary: compile WHAT is drawn, hand the renderer that plus the
        # page geometry it needs to decide WHERE. It no longer sees `_model` or `a`.
        render_height_ladder(
            dwg,
            # `groups=` so the planner runs ONCE per build: the orchestrator already
            # planned, and a compiler re-planning behind it would create a second
            # product that can drift while the migration is partial (#923 review).
            _compiled,
            layout_frame(a),
            ctx=ctx,
            detail_view=detail_view,
        )

    def _s_plates():
        # Plate/wall thicknesses on a multi-plate prismatic (#559): the thin extent of each
        # recognised slab, placed in the view where its thin axis is visible. A single flat
        # plate has none (its thickness IS the envelope height).
        # Planner-fed (#729): consumes the DimensionGroups so an authored tolerance renders.
        render_plates(dwg, _compiled, a, ctx=ctx)

    def _s_step_positions():
        # Prismatic step POSITIONS (#555): where each shoulder sits along its axis, so a
        # stepped block is fully constrained (the heights alone leave the shoulder implicit).
        render_step_positions(dwg, _compiled, layout_frame(a), ctx=ctx)

    def _s_chamfers():
        # Chamfer callouts (#560): C{leg} / {leg}×{angle}° via a leader off each chamfer face.
        # Planner-fed (#724): consumes the DimensionGroups so an authored tolerance renders.
        render_chamfers(dwg, _compiled, a, ctx=ctx)

    def _s_fillets():
        # Fillet callouts (#561): R{radius} (grouped n× R) via a leader off each rounded edge.
        # Planner-fed (#725): consumes the DimensionGroups so an authored tolerance renders.
        render_fillets(dwg, _compiled, a, ctx=ctx)

    def _s_flats():
        # Machined-flat callouts (#148b): {across} A/F via a leader off each flat on round stock.
        # Planner-fed (#726): consumes the DimensionGroups so an authored tolerance renders.
        render_flats(dwg, _compiled, a, ctx=ctx)

    def _s_pockets():
        # Blind-recess callouts (#148a): W × L × D DEEP via a leader off each floored pocket.
        # Planner-fed (#728): consumes the DimensionGroups so authored tolerances render.
        render_pockets(dwg, _compiled, a, ctx=ctx)

    def _s_pocket_patterns():
        # Grouped blind-pocket-array callouts (#841): ONE count× W × L × D DEEP leader + the
        # (n-1)× pitch dim(s), instead of N competing per-pocket size dims. Placed after
        # "pockets" (same leader mechanism); its member pockets are composed into the pattern,
        # so render_pockets never double-renders them.
        render_pocket_patterns(dwg, _compiled, a, ctx=ctx)

    def _s_slot_patterns():
        # Grouped through-slot-array callouts (#841): ONE count× SLOT W × L leader + the (n-1)×
        # pitch dim(s), instead of N competing per-slot size dims (some of which drop, #841
        # behaviour 1). Member slots are composed into the pattern, so render_slots never
        # double-renders them.
        render_slot_patterns(dwg, _compiled, a, ctx=ctx)

    def _s_off_axis_across():
        # Side-drilled holes' in-plane (side-below) locations share the below corridor with
        # the overall envelope depth. They now queue into the same batch; the envelope's
        # later subchain + mandatory priority keeps ISO outermost stacking and prevents
        # best-effort locations from starving the principal depth dimension (#477).
        if feature_keys:
            _locate_off_axis_holes(dwg, ctx, a, which="across", plan=_compiled)

    def _s_envelope():
        # Overall width (plan, below) + depth (side, below) envelope dims — IR renderer,
        # queued into the shared corridor instead of claiming a post-hoc carve tier.
        # Suppression (the rotational OD's cross-axis extents, X/Z-turned) is the planner's
        # decision (#250). No square-footprint rule any more — #997 removed it.
        render_envelope(dwg, _compiled, a, ctx=ctx)

    def _s_detail_request():
        # Prismatic step-height detail: queue it when detail recovery is enabled (the
        # build default; ``detail_view=False`` opts out), then resolve it with every other
        # detail request in the "details" stage (#307).
        if detail_view:
            _request_prismatic_detail(dwg, a, ctx=ctx, plan=_compiled)

    def _s_boss_diameters():
        # Prismatic bosses get a plan-view ø leader BEFORE the turned row/column solve,
        # which then sees the ø as 'mentioned' and skips it (#629 — the column-left strip
        # strands a boss ø when tight, even on a half-empty sheet). No-op on turned parts
        # (they keep the OD stack).
        render_boss_diameters(dwg, _compiled, a, ctx=ctx)

    def _s_boss_heights():
        render_boss_heights(dwg, _compiled, a, ctx=ctx)

    def _s_polygonal_bosses():
        render_polygonal_bosses(dwg, _compiled, a, ctx=ctx)
        render_polygonal_stock(dwg, _compiled, a, ctx=ctx)

    def _s_diameters():
        # Turned-part dimensions via the IR (ADR 0008 convergence). The model is built
        # once and fed to both renderers (#229 — no per-pass rebuild): ø leaders, row
        # below (X) / end-on radial leaders (Y) / column left (Z), one path by
        # frame axis. Replaces
        # _annotate_turned_diameters.
        render_diameters(dwg, _compiled, a, ctx=ctx)

    def _s_step_lengths():
        # The chain that locates every shoulder, X/Y/Z from one path (#223). A crowded
        # X-turned head queues an enlarged detail request (#304/#307) instead of
        # cramming; the envelope dim along the turning axis was suppressed so the chain
        # does not double-dimension the length.
        nonlocal _runtime_plan
        if a.prof is not None:
            placed = render_step_lengths(dwg, _compiled, ctx=ctx)
            if placed == 0:
                released = _runtime_plan.release_contingency("step_length")
                if released is not _runtime_plan:
                    _runtime_plan = released
                    render_height_ladder(
                        dwg,
                        ladder_plan_for(_runtime_plan, step_height=False, overall=True),
                        layout_frame(a),
                        ctx=ctx,
                        detail_view=detail_view,
                    )

    def _s_off_axis_along():
        # Side-drilled (X/Y-axis) hole HEIGHT locations — queued after the mandatory
        # envelope candidates so below/right corridors solve them together with GD&T/PMI
        # at the drain. (The front-right height ladder's leapfrog witness chain — #477 —
        # survives inside its candidates' build closures since #636; nothing here
        # places immediately.)
        if feature_keys:
            _locate_off_axis_holes(dwg, ctx, a, which="along", plan=_compiled)

    def _s_slots():
        # Non-cylindrical machined features: slots / reduced across-flats sections
        # (#135) — IR renderer, placed through the zone strips (shared infra). Runs
        # after every hole/diameter pass so it claims strip space last.
        # Planner-fed (#730): consumes the DimensionGroups so authored tolerances render.
        render_slots(dwg, _compiled, a, ctx=ctx)

    def _s_gdt():
        # Declared GD&T frames / datum symbols / surface finishes (ADR 0011 §4, #61)
        # register into the same strips as first-class candidates BEFORE the drain, so
        # the one solve orders and spaces them crossing-free with locations/slots rather
        # than consuming leftovers as first-fit placements.
        render_gdt(dwg, _model, a, ctx=ctx)

    def _s_pmi():
        # Authored STEP PMI dims (#393) — same pre-drain registration as GD&T above.
        if a.pmi_mode == "annotate" or (
            ctx.model_declared
            and any(f.kind in ("authored_dimension", "pmi") for f in _model.features)
        ):
            render_pmi(dwg, _model, a, ctx=ctx)

    def _s_drain():
        # Now every corridor feeder pass has registered; solve each shared strip once
        # (ADR 0009 end state) + the #690 label reconciliation — BEFORE the
        # section/detail views so they see the placed ladder as an obstacle.
        drain_and_reconcile(ctx, dwg)

    def _s_grooves():
        # Turned/circlip-groove callouts (#148c): {width} WIDE × ø{dia} via a leader off
        # each groove. A groove is a secondary leader-callout on a turned shaft — exactly
        # where the primary turned-length chain runs — so it places into remaining clear
        # room only after the corridor drain has finalised the diameter/step-length
        # furniture (else its room check can't see the not-yet-drained length dims and
        # collides, #148c crowded-shaft).
        # Planner-fed (#727): consumes the DimensionGroups so authored tolerances render.
        render_grooves(dwg, _compiled, a, ctx=ctx)

    def _s_feature_leaders():
        drain_feature_leaders(dwg, a, ctx)

    def _s_section():
        # The section view renders after the corridor-drained furniture exists, so
        # its full strip_obstacles room check can see side callouts, envelope dims,
        # slots, GD&T/PMI, and drained ladder outputs as one occupancy set. Details
        # still render after it and avoid the section view.
        if _section is not None:
            _add_section_view(dwg, a, _section, ctx=ctx)
        else:
            # Recorded, not left at the initial `not_evaluated`: the planner DID run and
            # found no counterbore/spotface/blind Z-hole, which is a different fact from
            # the pass never having run at all (#1190).
            dwg.record_section_decision(
                "not_warranted",
                detail="no counterbore/spotface/blind Z-hole — no section warranted",
            )

    def _s_details():
        # Resolve every queued enlarged-detail request (#307) — prismatic step bands and
        # crowded turned heads alike — through the one generic detailer, now that all
        # views and main-view annotations are placed (so the detail avoids them).
        _resolve_details(dwg, a, ctx=ctx)

    def _s_title_block():
        _add_title_block(dwg, a)

    def _s_sheet_frame():
        # The sheet border, drawn LAST (#767) — gated on the frame opt-in; content already
        # reserved room via the raised a.margin, so this only draws.
        if a.frame:
            _add_sheet_frame(dwg, a)

    def _s_zone_grid():
        # ISO 5457 zone-grid border ruler (#768), on the frame (a.zones implies a.frame).
        if a.zones:
            _add_zone_grid(dwg, a)

    def _s_projection_symbol():
        # ISO 5456-2 projection-method glyph (#769) in the reserved title-block band.
        if a.projection:
            _add_projection_symbol(dwg, a)

    def _s_tabulate():
        # Escalate to a hole table when the plan view is too dense to dimension
        # every hole — runs last so the table avoids every placed annotation
        # including the title block (#93).
        _maybe_tabulate_holes(dwg, a, ctx=ctx, plan=_compiled)

    run_stages(
        {
            "rotational": _s_rotational,
            "centermarks": _s_centermarks,
            "reserve_section": _s_reserve_section,
            "hole_callouts": _s_hole_callouts,
            "locations": _s_locations,
            "height_ladder": _s_height_ladder,
            "plates": _s_plates,
            "step_positions": _s_step_positions,
            "chamfers": _s_chamfers,
            "fillets": _s_fillets,
            "flats": _s_flats,
            "pockets": _s_pockets,
            "pocket_patterns": _s_pocket_patterns,
            "slot_patterns": _s_slot_patterns,
            "off_axis_across": _s_off_axis_across,
            "envelope": _s_envelope,
            "detail_request": _s_detail_request,
            "boss_diameters": _s_boss_diameters,
            "polygonal_bosses": _s_polygonal_bosses,
            "boss_heights": _s_boss_heights,
            "diameters": _s_diameters,
            "step_lengths": _s_step_lengths,
            "off_axis_along": _s_off_axis_along,
            "slots": _s_slots,
            "gdt": _s_gdt,
            "pmi": _s_pmi,
            "drain": _s_drain,
            "grooves": _s_grooves,
            "feature_leaders": _s_feature_leaders,
            "section": _s_section,
            "details": _s_details,
            "title_block": _s_title_block,
            "tabulate": _s_tabulate,
            "sheet_frame": _s_sheet_frame,
            "zone_grid": _s_zone_grid,
            "projection_symbol": _s_projection_symbol,
        }
    )
    retract_resolved_withholdings(dwg, ctx, _runtime_plan)
    if ctx.trace is not None:  # snapshot the run's escalations into the trace (#736)
        ctx.trace.record_escalations(ctx.escalations)
    # The escalations live only on this per-run ctx (#639), discarded when _auto_annotate
    # returns — so nothing carries stale drops into a later deferred edit (#440), and there is
    # no drawing-level list to clear.
    return _runtime_plan.diagnostics


#: Codes that say "the compiler approved this measurement and it is not on the sheet". They are
#: recorded by the pass that could not place the mark, which is the only place that knows WHY —
#: which strip was full and who filled it. It is not the place that knows whether some LATER
#: pass drew the measurement anyway.
_WITHHOLDING_CODES = ("step_dim_withheld", "overall_dim_withheld")


def _approved_per_measurement(plan) -> dict:
    """How many marks the largest approved set under each `DimensionId` carries.

    Not one, in general. A `step_height` ladder gives EVERY rung the same
    `_dim_id(step, "step_height.length")` — five rungs, one id (ADR 0016 Amdt 3: the parameter
    id is the canonical spelling, and a per-level identity does not exist). So "this id is
    claimed by some annotation" cannot mean "this measurement is on the sheet" for a ladder,
    and a retraction written that way withdraws the whole report as soon as ONE rung places
    (#1216 review r10, F1). Counting is what the collapse leaves available.

    The MAXIMUM over containers, not the sum: the plan represents the same five step heights
    twice, once as a `step_height` ladder and once as five `step_level` group dims, so summing
    expects ten marks for five measurements and no drawing can ever satisfy it. That arithmetic
    is why the first count-based cut retracted nothing at all.
    """
    counts: dict = {}

    def _tally(entries) -> None:
        per: dict = {}
        for entry in entries:
            if entry.id is not None:
                per[entry.id] = per.get(entry.id, 0) + 1
        for mid, n in per.items():
            counts[mid] = max(counts.get(mid, 0), n)

    for group in plan.groups:
        _tally(group.dims)
    for ladder in plan.ladders:
        _tally(ladder.rungs)
    return counts


def retract_resolved_withholdings(dwg, ctx, plan) -> None:
    """Withdraw a withholding whose measurement reached the sheet after all.

    `render_height_ladder` runs long before the detail view exists, so a rung it could not fit
    in the front-right strip may still be dimensioned in an enlarged detail. Reported at record
    time and never revisited, `step_dim_withheld` fired on `_crowded_staircase` — a part whose
    five approved rungs are ALL claimed, by `dim_detail_a_step0..2` and `dim_step_0..1` — and
    said they "are not dimensioned at this scale", which was false (#1216 review r9, F5).

    The same shape as the `callout_dropped` and `location_ref_dropped` retractions above, and
    the same rule `solve_corridor` applies to a deduped loser: a drop is only a drop if the
    measurement is still absent when the run finishes.

    Fails closed, and this is the part that took two rounds to get right. The predicate is
    "as many annotations claim this id as the plan approved entries under it", not "some
    annotation claims it": with one id per ladder the second retracts a five-rung withholding
    on the strength of one drawn rung, which is the silent omission the report exists to end,
    restored by its own fix (#1216 review r10, F1).
    """
    approved = _approved_per_measurement(plan)
    drawn: dict = {}
    for name in dwg.registry.names():
        for mid in dwg.registry.measurement_of(name) or ():
            drawn[mid] = drawn.get(mid, 0) + 1
    for code in _WITHHOLDING_CODES:
        ctx.drop_issues_where(
            code,
            lambda issue: (
                bool(issue.measurement_ids)
                and all(drawn.get(mid, 0) >= approved.get(mid, 1) for mid in issue.measurement_ids)
            ),
        )


def _maybe_tabulate_holes(dwg, a: Analysis, *, ctx, plan=None):
    """Run hole-table escalation as one exception-atomic annotation transaction.

    Ordinary fit/balloon failures roll the shared table transaction back as a unit.
    An exception additionally restores the outer snapshot before re-raising, so none
    of the attempted table state or temporary semantic markers may survive it.
    """
    if not any(escalation.kind in {"callout", "location"} for escalation in ctx.escalations):
        return
    snapshot = _snapshot_annotation_transaction(dwg, ctx.coverage)
    try:
        return _maybe_tabulate_holes_impl(dwg, a, ctx=ctx, plan=plan)
    except BaseException:
        _restore_annotation_transaction(dwg, ctx.coverage, snapshot, {})
        raise


def _maybe_tabulate_holes_impl(dwg, a: Analysis, *, ctx, plan=None):
    """Escalate to a per-instance hole table + balloons when the plan view is too
    dense to dimension every hole individually (#93); a dropped ISO pattern
    callout gets one grouped balloon of its own (#351 PR-3, ADR 0009 Amdt 1
    decision 1 — the #348 fix).

    When callouts or location references had to be dropped, the individual
    plan-view callouts and X/Y location dims are removed and replaced by a
    complete **hole chart** — one row per hole (``TAG | ⌀ | DEPTH | X | Y``, X/Y
    from the min-corner datum) and a uniquely-tagged balloon at each hole. The table
    carries ``covers_diameters`` so the coverage lint still counts the holes.
    Sparse parts drop nothing, so this is a no-op for them — unchanged.

    If the table itself will not fit, nothing is removed and the drop lint is
    kept — the sheet is never left with neither.

    Independent of that density gate: a recognised pattern (bolt circle / linear
    array / grid) whose own grouped ``n×`` callout could not be placed inline
    gets **one balloon tagging the whole pattern**, not one balloon per member —
    a dropped pattern is a real coverage gap on any part, not just a dense one.
    Both kinds of balloon share one strip-solved band per side (one
    ``_add_balloons`` call) so they never overlap each other.
    """
    # Trigger on the first-class Escalation objects the hole placers collect (ADR 0009
    # Amdt 1, #351 PR-2), not by grepping the `*_dropped` lint strings. Byte-identical:
    # a "callout"/"location" Escalation is emitted 1:1 with each callout_dropped/
    # location_ref_dropped code. The lint codes stay as the coverage surface.
    escalations = ctx.escalations
    if not any(e.kind in ("callout", "location") for e in escalations):
        return

    # A "callout" escalation's feature is the dropped group's PatternFeature only when
    # it is a fully-surviving recognised pattern (_annotate_holes's `pat`, holes.py) —
    # scoped to the plan view, the only one `_add_balloons`'s halo covers.
    pattern_feats = [
        e.feature
        for e in escalations
        if e.kind == "callout" and e.view == "plan" and isinstance(e.feature, PatternFeature)
    ]

    # Tabulate only the genuinely UNpatterned plan-view holes: holes in a
    # recognised pattern are documented by their grouped ``n× ⌀`` callout +
    # pattern dimension, so they must not become table rows or per-hole balloons
    # (#92).  Excluding them is also what keeps a densely-but-regularly drilled
    # part (e.g. NIST CTC-02) off the 61-row escalation (#111). Sourced from the IR —
    # a loose z-axis HoleFeature is by construction not a pattern member, so no
    # HoleRecord crosses here (ADR 0008 Am6; #584 WP1 B4).
    _model = ctx.part_model
    holes = [
        SimpleNamespace(
            location=pos,
            diameter=f.diameter,
            depth=f.depth,
            through=f.through,
            feature=f,
        )
        for f in (_model.features if _model is not None else ())
        if f.kind == "hole" and f.frame.axis == "z"
        for pos in (f.members or (f.frame.origin,))
    ]
    # A chart is warranted only for a *genuinely* dense plan view — a part that
    # merely dropped one too-close location ref keeps its individual dims (the
    # legibility gate already handled it). #93.
    tabulate_scattered = len(holes) >= _TABULATE_MIN_HOLES
    if not tabulate_scattered and not pattern_feats:
        return

    n_scattered = len(holes) if tabulate_scattered else 0
    tags = _tag_sequence(n_scattered + len(pattern_feats))
    scattered_tags, pattern_tags = tags[:n_scattered], tags[n_scattered:]
    # One balloon per pattern, tagged with its member count so the ring reads
    # "6×A" rather than one glyph per member (#348).  Without a table/legend the
    # marker does not state the pattern's diameter, depth or arrangement; it is
    # deliberately non-certifying and cannot clear the original callout drop.
    pattern_specs = [
        (
            f"{feat.count}×{tag}",
            0,
            # Anchor on an actual member hole, not `feat.frame.origin` — for a
            # bolt circle / grid that's the pattern's geometric centre, which
            # isn't a hole, so the leader would point at solid material instead
            # of the pattern it documents.  Fall back to the centre only if a
            # declared pattern left `members` empty (detected ones never do).
            SimpleNamespace(
                location=(feat.members or (feat.frame.origin,))[0],
                diameter=feat.member.diameter,
            ),
        )
        for tag, feat in zip(pattern_tags, pattern_feats, strict=True)
    ]

    scattered_specs: list = []
    table_placed = False
    table = None
    table_features: tuple = ()
    replaceable_callout_features: set = set()
    table_callout_replacement_features: set = set()
    table_location_replacement_features: set = set()
    replaced = {}
    table_transaction_snap = None

    # Automatic/finalize escalation uses deterministic internal names. A sanctioned
    # public edit may already own one of them; replacing that object would destroy user
    # state and a pre-existing name is not evidence from this placement attempt. Fail
    # closed before stashing anything. A later naming-policy change can allocate a fresh
    # namespace here without weakening the attempt-local ownership contract.
    reserved_attempt_names = {f"balloon_plan_{tag}_0" for tag in scattered_tags} | {
        f"balloon_plan_{feature.count}×{tag}_0"
        for tag, feature in zip(pattern_tags, pattern_feats, strict=True)
    }
    if tabulate_scattered:
        reserved_attempt_names.add("hole_table_plan")
    colliding_names = reserved_attempt_names.intersection(dwg.registry.names())
    if colliding_names:
        names = ", ".join(sorted(colliding_names))
        if tabulate_scattered:
            ctx.record_issue(
                "warning",
                "table_dropped",
                f"hole table reserved annotation name(s) already exist: {names}",
            )
        else:
            ctx.record_issue(
                "warning",
                "balloon_dropped",
                f"hole balloon reserved annotation name(s) already exist: {names}",
            )
        return

    if tabulate_scattered:
        compiled = plan if plan is not None else compile_dimensions(_model)
        approved_hole_dimensions = {
            resolve_feature(group.ref): {
                dimension.parameter_id: dimension for dimension in group.dims
            }
            for group in compiled.of_kind("hole")
        }
        compiled_dimensions_by_feature: dict[object, list] = {
            resolve_feature(group.ref): list(group.dims) for group in compiled.of_kind("hole")
        }
        for location in compiled.locations:
            feature = resolve_feature(location.ref)
            if getattr(feature, "kind", None) == "hole":
                compiled_dimensions_by_feature.setdefault(feature, []).append(location)
        replaceable_callout_features = {
            hole.feature
            for hole in holes
            if _hole_table_replaceable_feature(
                hole.feature, compiled_dimensions_by_feature.get(hole.feature, ())
            )
        }
        # No `_tol_suffix` here, deliberately. A location cannot be toleranced: `location` is
        # not among any feature's `parameters()`, so there is no key to author one against, and
        # `_compile_locations` / `_compile_off_axis_hole_locations` assign no tolerance.
        # Measured across the guard corpus, decorating every parameter of every feature through
        # both key shapes: 204 compiled locations, 0 with a tolerance. The first cut of this
        # composed the suffix here anyway — a reader with no writer, which is precisely the
        # dead-code defect this PR filed against #1234's `depth_tol` (#1216 review r9, F3).
        approved_locations = {
            (resolve_feature(location.ref), tuple(location.span[1]), location.discriminator): (
                location.value_text
            )
            for location in compiled.locations
            if location.span is not None
        }

        def _approved_hole_text(hole, parameter):
            """A table cell's text, authored tolerance included (#1216 review r9).

            A table row is a dimension: `⌀ 8` in a hole-table cell states the same
            requirement `⌀8` states beside a leader, so it carries the same ±. Escalating
            a toleranced callout into a table used to drop the tolerance on the way — the
            requirement left the sheet because the drawing got denser, which is #1215's
            failure mode at a site its sweep could not see (the guard reads `label`, and a
            table's text lives in its rows).
            """
            dimension = approved_hole_dimensions.get(hole.feature, {}).get(parameter)
            if dimension is None:
                return ""
            return f"{dimension.value_text}{_tol_suffix(dimension.tolerance, dwg.draft)}"

        def _table_row(tag, hole):
            diameter_text = _approved_hole_text(hole, "bore.diameter")
            depth_text = (
                "THRU"
                if hole.through and diameter_text
                else _approved_hole_text(hole, "bore.depth")
            )
            location = tuple(hole.location)
            return (
                tag,
                f"ø{diameter_text}" if diameter_text else "",
                depth_text,
                approved_locations.get((hole.feature, location, "x"), ""),
                approved_locations.get((hole.feature, location, "y"), ""),
            )

        header = ("TAG", "⌀", "DEPTH", "X", "Y")
        data = [_table_row(tag, h) for tag, h in zip(scattered_tags, holes, strict=True)]
        # Remove the callouts and location dims the table replaces FIRST: it frees
        # their space for the table and shrinks the obstacle set fit_box scans (the
        # dense parts have dozens), which is the dominant cost on heavy sheets (#93).
        # Structured coverage state (registered at placement time, #351 PR-4c), not
        # an annotation-name-prefix grep — the last stringly-typed inference this
        # resolver relied on (ADR 0009 Amdt 1).
        table_transaction_snap = _snapshot_annotation_transaction(dwg, ctx.coverage)
        replaced = _stash_annotations(
            dwg,
            [
                n
                for n, annotation in list(dwg.iter_annotations())
                if ctx.coverage.is_scattered_hole_doc(n)
                and (
                    _hole_table_replaceable_location_annotation(dwg.registry, n, annotation)
                    or (
                        _hole_table_replaceable_annotation(dwg.registry, n, annotation)
                        and _annotation_hole_features(dwg.registry, n, annotation)
                        <= replaceable_callout_features
                    )
                )
            ],
        )

        # Widen the chart into more column-blocks until it fits the page.
        table_issue_base = dwg.registry.issues
        for ncols in (1, 2, 3, 4):
            table = dwg.add_table(
                _wrap_rows(header, data, ncols), name="hole_table_plan", block_cols=len(header)
            )
            if table is not None:
                break
        # ``add_table`` records one diagnostic for every rejected wrapping attempt.
        # Restore the exact pre-attempt issue inventory instead of clearing every
        # table_dropped finding: an unrelated failed public table remains actionable.
        dwg.registry.restore_issues(table_issue_base)
        if table is None:
            # Even wrapped it will not fit — restore the callouts/dims and keep the
            # drop lint, so the sheet is never left with neither. The pattern
            # balloons below are unaffected — nothing of theirs was removed.
            assert table_transaction_snap is not None
            _restore_annotation_transaction(
                dwg,
                ctx.coverage,
                table_transaction_snap,
                replaced,
                reason="table_not_placed",
            )
            ctx.record_issue("warning", "table_dropped", "hole table did not fit the sheet")
        else:
            table_features = tuple(dict.fromkeys(h.feature for h in holes))
            scattered_specs = [(tag, 0, h) for tag, h in zip(scattered_tags, holes, strict=True)]
            table_placed = True

    balloon_specs = scattered_specs + pattern_specs
    placed_names: set = set()
    balloon_issue_base = dwg.registry.issues

    def _place_balloon_attempt(specs, *, perimeter, required_count=0):
        nonlocal placed_names
        attempted = {f"balloon_plan_{tag}_{member_index}" for tag, member_index, _ in specs}
        dwg.registry.restore_issues(balloon_issue_base)
        _discard_attempt_annotations(dwg, attempted)
        previous_objects = {name: dwg.registry.named(name) for name in attempted}
        render_balloons(
            dwg,
            a,
            "plan",
            specs,
            ctx,
            perimeter=perimeter,
            # Every automatic escalation balloon is replacement evidence. Pattern-only
            # attempts have no table, but must still fail closed against retained labels
            # before their landed name/owner may clear a callout drop.
            avoid_annotation_labels=True,
            required_count=required_count,
        )
        placed_names = {
            name
            for name, previous in previous_objects.items()
            if dwg.registry.named(name) is not None and dwg.registry.named(name) is not previous
        }

    if balloon_specs:
        # One call: the strip solver must see every band member together, or two
        # independent render_balloons calls could stack a pattern balloon on a
        # per-hole one in the same band. Called sideways into the render layer
        # (#699) — no longer an upward duck-typed dwg.add_balloons call.
        # A scattered-hole table is a visual escalation, not merely another
        # balloon request: spread its tags around the usable perimeter so a
        # deep occupied strip cannot collapse the ring onto two near sides
        # (#901). Pattern-only and public-verb balloons keep nearest-band cost.
        _place_balloon_attempt(
            balloon_specs,
            perimeter=table_placed,
            required_count=len(scattered_specs),
        )

    # Commit the shared table replacement only after every balloon that maps its
    # visible rows back to physical holes has landed (#1144).
    table_success_features: set = set()
    if table_placed and table is not None:
        table_tagged_holes = [
            (tag, 0, hole, hole.feature) for tag, hole in zip(scattered_tags, holes, strict=True)
        ]
        expected_counts = {
            feature: int(feature.count or len(feature.members) or 1) for feature in table_features
        }
        table_success_features = _fully_ballooned_features(
            "plan",
            table_tagged_holes,
            placed_names,
            dwg.registry,
            expected_counts,
        )
        # A hole table is one shared visual/index artifact. If even one visible
        # row lacks its required feature-owned balloon, keeping a partial table
        # creates a drawing-level object that cannot participate honestly in
        # per-feature fallback placement—and outer compose-then-pack may later
        # move a view into that orphan footprint. Fail the shared transaction
        # closed: restore every feature-backed fallback and discard every table
        # balloon. This still satisfies partial-result semantics because no
        # uncovered requirement is suppressed; the complete original inventory
        # wins as a unit.
        if table_success_features != set(table_features):
            assert table_transaction_snap is not None
            _restore_annotation_transaction(
                dwg,
                ctx.coverage,
                table_transaction_snap,
                replaced,
                reason="required_balloon_not_placed",
            )
            ctx.record_issue(
                "warning",
                "table_dropped",
                "hole table lacked complete feature-owned balloon evidence",
            )
            ctx.record_issue(
                "warning",
                "balloon_dropped",
                "one or more required hole-table balloons could not be placed",
            )
            table_placed = False
            table = None
            table_success_features = set()
            placed_names = set()
            if pattern_specs:
                balloon_issue_base = dwg.registry.issues
                _place_balloon_attempt(pattern_specs, perimeter=False)

    if table_placed and table is not None:
        ordered_table_success_features = tuple(
            feature for feature in table_features if feature in table_success_features
        )

        # One entry per successfully keyed hole (with repeats) so the legacy count check
        # and the semantic ledger agree about the exact committed subset.
        table.covers_diameters = tuple(
            h.diameter
            for h in holes
            if h.feature in table_success_features
            and "bore.diameter" in approved_hole_dimensions.get(h.feature, {})
        )
        table_measurements = tuple(
            dict.fromkeys(
                [
                    dim.id
                    for group in compiled.of_kind("hole")
                    if resolve_feature(group.ref) in table_success_features
                    for dim in group.dims
                    if dim.id is not None and dim.parameter_id in {"bore.diameter", "bore.depth"}
                ]
                + [
                    location.id
                    for location in compiled.locations
                    if location.id is not None
                    and resolve_feature(location.ref) in table_success_features
                ]
            )
        )
        table_locations = tuple(
            _hole_location_coverage_fact(location)
            for location in compiled.locations
            if location.id is not None
            and location.span is not None
            and resolve_feature(location.ref) in table_success_features
        )
        table_requirements = tuple(
            (feature, "bore.through", 1)
            for feature in ordered_table_success_features
            if feature.through and "bore.diameter" in approved_hole_dimensions.get(feature, {})
        ) + tuple(
            (
                feature,
                "grouping.count",
                int(feature.count or len(feature.members) or 1),
            )
            for feature in ordered_table_success_features
            if int(feature.count or len(feature.members) or 1) > 1
            and "bore.diameter" in approved_hole_dimensions.get(feature, {})
        )
        stashed_callout_features = {
            feature
            for record in replaced.values()
            if any(
                isinstance(parameter := getattr(measurement, "parameter", None), str)
                and parameter.startswith("bore.")
                for measurement in record.identity.get("measurement", ())
            )
            for feature in record.features
        }
        stashed_location_features = {
            feature
            for record in replaced.values()
            if any(
                isinstance(parameter := getattr(measurement, "parameter", None), str)
                and parameter.startswith("location")
                for measurement in record.identity.get("measurement", ())
            )
            for feature in record.features
        }
        escalated_callout_features = {
            escalation.feature
            for escalation in escalations
            if escalation.kind == "callout"
            and escalation.view == "plan"
            and escalation.feature in replaceable_callout_features
        }
        escalated_location_features = {
            escalation.feature
            for escalation in escalations
            if escalation.kind == "location"
            and escalation.view == "plan"
            and escalation.feature in table_features
        }
        dropped_callout_features = {
            feature
            for issue in ctx.registry.issues
            if issue.code == "callout_dropped"
            for feature in (
                *(getattr(measurement, "feature", None) for measurement in issue.measurement_ids),
                *(requirement[0] for requirement in issue.hole_requirement_ids),
            )
            if feature in replaceable_callout_features
        }
        dropped_location_features = {
            feature
            for issue in ctx.registry.issues
            if issue.code == "location_ref_dropped"
            for feature in (
                *(getattr(measurement, "feature", None) for measurement in issue.measurement_ids),
                *(requirement[0] for requirement in issue.hole_requirement_ids),
            )
            if feature in table_features
        }
        table_callout_replacement_features = table_success_features & (
            stashed_callout_features | escalated_callout_features | dropped_callout_features
        )
        table_location_replacement_features = table_success_features & (
            stashed_location_features | escalated_location_features | dropped_location_features
        )
        representation_requirements = tuple(
            dict.fromkeys(
                [
                    (measurement.feature, measurement.parameter)
                    for measurement in table_measurements
                    if (
                        measurement.parameter.startswith("location")
                        and measurement.feature in table_location_replacement_features
                    )
                    or (
                        not measurement.parameter.startswith("location")
                        and measurement.feature in table_callout_replacement_features
                    )
                ]
                + [
                    (feature, parameter)
                    for feature, parameter, _point in table_locations
                    if feature in table_location_replacement_features
                ]
                + [
                    (feature, parameter)
                    for feature, parameter, _count in table_requirements
                    if feature in table_callout_replacement_features
                ]
            )
        )
        _register_hole_table_coverage(
            table,
            dwg.registry,
            "hole_table_plan",
            measurements=table_measurements,
            locations=table_locations,
            requirements=table_requirements,
            representation_reason="required_balloons_placed",
            representation_requirements=representation_requirements,
        )

        # Resolve only drops whose complete semantic requirement set belongs to the
        # successfully keyed subset. Unrelated or partially covered failures remain honest.
        ctx.drop_issues_where(
            "location_ref_dropped",
            lambda issue: (
                bool(issue.hole_requirement_ids)
                and all(
                    requirement[0] in table_location_replacement_features
                    for requirement in issue.hole_requirement_ids
                )
            ),
        )

    # Clear `callout_dropped` only when the complete dropped callout is now documented
    # by a successfully keyed scattered-hole table row.  A grouped pattern marker such
    # as ``6×A`` has no defining table row and therefore remains deliberately
    # non-certifying: it may provide the ADR-0009 visual grouping cue, but the original
    # callout drop and its physical-requirement outcomes must remain actionable.
    # A drop this resolver does not cover — a table that didn't fit, a balloon that
    # didn't land, or any callout dropped in a non-plan view — leaves the lint standing.
    callout_escalations = [e for e in escalations if e.kind == "callout"]
    available_issues = [issue for issue in ctx.registry.issues if issue.code == "callout_dropped"]
    resolved_issue_ids = set()
    for escalation in callout_escalations:
        candidates = [
            issue
            for issue in available_issues
            if tuple(issue.measurement_ids) == tuple(escalation.targets)
        ]
        if len(candidates) != 1:
            continue  # ambiguous producer correspondence fails closed
        issue = candidates[0]
        available_issues = [candidate for candidate in available_issues if candidate is not issue]
        if escalation.view != "plan":
            continue
        if isinstance(escalation.feature, PatternFeature):
            continue
        issue_features = {
            getattr(measurement, "feature", None) for measurement in issue.measurement_ids
        }
        issue_features.discard(None)
        if issue_features and issue_features <= table_callout_replacement_features:
            resolved_issue_ids.add(id(issue))
    ctx.drop_issues_where("callout_dropped", lambda issue: id(issue) in resolved_issue_ids)
