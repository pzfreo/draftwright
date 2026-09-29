"""One build attempt: analyse, resolve views, assemble, repack, and repair."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from pathlib import Path
from typing import cast

from build123d import Shape

from draftwright.analysis import Analysis
from draftwright.annotation_layout_profile import candidate_profile, use_layout_profile
from draftwright.annotations.orchestrator import build_model
from draftwright.build_options import BuildOptions
from draftwright.drawing import Drawing
from draftwright.layout_selection import (
    annotation_demand_carrier_evidence,
    pre_render_view_page_overflow,
)
from draftwright.model import PartModel
from draftwright.model.ir import authored_dimension_target_view
from draftwright.model.planner import plan_dimensions
from draftwright.progress import stage
from draftwright.view_plan import (
    UncoveredViewRequirement,
    ViewPlanIncomplete,
    third_angle_view_names,
)


def build_once(
    step_file: str | Path | Shape,
    options: BuildOptions,
    *,
    scale: float | None,
    page: str | tuple | None,
    _analysis_base: Analysis | None = None,
    _analysis_sink: Callable[[Analysis], None] | None = None,
    _critique_recognition_cache=None,
    _arrangements: tuple[str, ...] | None = None,
    _select_automatic_views: bool = False,
    _candidate_profile_first: bool = False,
    _title_block_cache=None,
    _placement_critique=None,
    _analyse: Callable[..., Analysis],
    _coerce_model: Callable[..., PartModel],
    _automatic_turned_principals: Callable[[Analysis], tuple[str, ...] | None],
    _resolve_trace,
    _assemble: Callable[..., Drawing],
    _repack_to_fixed_point: Callable[..., tuple[Analysis, Drawing] | None],
    choose_pre_render_profile,
    lost_required_derived_view_reservations,
) -> Drawing:
    """Build one drawing attempt from the validated options and chosen scale/page.

    The descriptions below refer to fields of ``options``. The public
    :func:`build_drawing` returns the live :class:`Drawing`; ``make_drawing``
    wraps that front door with export.

    Args:
        auto_dims: pass ``False`` to skip the automatic dimensions,
            centrelines, and leaders (#74) — the automatic set assumes a
            turned part and is wrong for prismatic geometry. Views, scale,
            page, and sheet furniture (title block, and the "ISO VIEW (NTS)"
            note when the iso is rescaled off sheet scale) are still produced;
            add your own annotations before export. (Annotations added by the default can
            also be removed wholesale with :meth:`Drawing.clear_annotations`.)
        detail_view: automatically recover crowded prismatic step dimensions in an
            enlarged detail view. Default ``True``; pass ``False`` to leave them on the
            parent view only and report ``step_dim_dropped`` when they do not fit.
        pmi: AP242 PMI handling. ``None`` (the default) behaves as ``"off"`` but retains
            that it was defaulted so a source containing authored PMI can say annotation is
            disabled by default. Explicit ``"off"`` produces no PMI annotations or per-record
            failures but reports the ignored source inventory; ``"report"`` inventories and
            lowers without rendering; ``"annotate"`` also requires render outcomes.
        repair: run the bounded lint→repair loop (:meth:`Drawing.repair`) after
            placement to fix mechanically-clear violations (a dim on the wrong
            side, two overlapping labels). Default ``True``; a no-op on a clean
            sheet. Pass ``False`` to inspect the raw greedy placement (#30).
        assembly: severity of the feature-coverage lint for a general-arrangement
            drawing. ``None`` (default) auto-detects — a multi-solid part is an
            assembly, whose per-part bores are reported at ``info`` rather than
            ``warning`` (a GA omits them by design). Force with ``True``/``False``
            (#69).
        reproducible: write files that do not carry the run that produced them, so
            two exports of one drawing are byte-identical and a written drawing can be
            diffed or checksummed to see whether its content really changed. Sets the
            default for :meth:`Drawing.export`'s own ``reproducible=``. ``True``:
            a drawing that differs between runs cannot be diffed, checksummed or
            cached, and the cost is small on a part — measured on the NIST CTC-01
            AP242 fixture it is +0.27 s on a 13.4 s job (+3.2% of export, +2.0%
            overall). Pass ``False`` to opt out where it is not small: the cost is
            one bounding box and one edge walk per part, so it grows with part
            count and reached +32% of export on a 358-part assembly. The metadata
            pinning the flag also turns on is ~1 ms either way.
        framed_recognition: opt an automatic build into the provider-owned local recognition
            frame. Caller geometry remains provenance, the exact local solid feeds downstream
            geometry stages, and a typed refusal has one visible top-level raw fallback. Raw
            remains the default; declared ``model=`` builds do not frame or recognise.
        model: a caller-supplied IR (ADR 4 (was 0011)) — a :class:`PartModel`, or a sequence
            of :class:`Feature`\\ s (declared with :func:`draftwright.model.hole`,
            ``boss``, ``step``, … from the objects you built). When given, **feature
            detection is skipped** and the auto-pass dimensions exactly the declared
            features; ``None`` (default) detects normally. Detection and declaration are
            two producers of the same IR — everything downstream is untouched. (Notes:
            sheet scale/zone estimation and the coverage lint still detect independently,
            so a *partial* declaration will flag the undeclared geometry. A declared
            hole/pattern now renders at its declared position even where detection missed
            it (#448); the one remaining detection-dependent bit is the off-axis
            side-drilled hole *location* dim, which needs recogniser-Hole geometry a
            declared feature doesn't carry. See ADR 4 (was 0011).)
        trace: the opt-in **solve-trace / explain mode** (#736): record every strip
            placement decision as ONE JSON file per build (schema ``version`` 2),
            with two record types. ``solves`` — the corridor solves: the candidate
            set, the obstacles that carved the strip (with owning annotation names),
            the free segments, and each candidate's outcome (placed/dropped-with-
            reason/deduped/promoted). ``pass_events`` — everything placed outside a
            corridor solve: the standalone strip passes plus the *immediate* placers
            (the post-drain machined-feature leader callouts and the turned
            diameter/step-length set-solves), each with per-item outcomes. The
            ``jq`` contract — corridor dims vs everything else::

                jq '.solves[].outcomes[] | select(.name == "dim_height")' t.trace.json
                jq '.pass_events[] | select(.label == "pocket_callouts") | .items[]' t.trace.json

            ``True`` writes ``<out>.trace.json`` beside the drawing; a path writes
            there (a directory gets ``<stem>.trace.json`` inside it). Default
            ``None`` consults the ``DRAFTWRIGHT_TRACE`` env var (same
            path-or-directory semantics); ``False`` forces it off. **Zero output
            change**: tracing never alters a placement decision, and off (the
            default) costs nothing. Recording-only: an unwritable trace path logs a
            warning and never aborts the build/export.

    Returns:
        A :class:`Drawing` with the standard front/plan/side/iso views projected
        and the automatic dimensions + title block already added.
    """

    out = options.out
    title = options.title
    number = options.number
    tolerance = options.tolerance
    drawn_by = options.drawn_by
    auto_dims = options.auto_dims
    detail_view = options.detail_view
    pmi = options.pmi
    repair = options.repair
    assembly = options.assembly
    model = options.model
    decorations = options.decorations
    requested = options.requested
    authored = options.authored
    trace = options.trace
    material = options.material
    date = options.date
    revision = options.revision
    company = options.company
    frame = options.frame
    projection = options.projection
    projection_symbol = options.projection_symbol
    zones = options.zones
    reproducible = options.reproducible
    framed_recognition = options.framed_recognition
    text_position = options.text_position
    text_orientation = options.text_orientation
    _views = options._views
    _include_iso = options._include_iso
    _view_constraints = options._view_constraints
    _required_tables = options._required_tables
    _document_input = options._document_input
    source = options.source
    approved_by = options.approved_by
    document_type = options.document_type
    sheet = options.sheet
    margin_left = options.margin_left
    margin_right = options.margin_right
    margin_top = options.margin_top
    margin_bottom = options.margin_bottom
    title_block_width = options.title_block_width
    leader_region = options.leader_region
    stem = "drawing" if isinstance(step_file, Shape) else Path(step_file).stem
    out = out or stem
    for _ext in (".svg", ".dxf"):
        if out.endswith(_ext):
            out = out[: -len(_ext)]
            break
    title = title or stem.replace("_", " ").upper()
    tracer = _resolve_trace(trace, out)

    if model is None and (requested or authored is not None):
        # Both verbs name a DECLARED feature object (ADR 4 (was 0016) / #872, #874), and detection
        # builds its own. Silently dropping them would leave a caller's add_dimension() /
        # dimension() with no effect and no diagnostic — the failure mode this project
        # treats as worse than a visible error (#630/#631/#632). An authored set is the
        # worse of the two to drop: the build would quietly revert to the automatic
        # dimensions the author was replacing (#921).
        verb = "requested=" if requested else "authored="
        raise ValueError(
            f"{verb} names declared features, so it needs model= too; a detected "
            "model builds its own feature objects that no request can target"
        )

    def analyse(
        *,
        reuse,
        views,
        scale_override=None,
        page_override=None,
        arrangements_override=None,
    ):
        return _analyse(
            step_file,
            title,
            number,
            tolerance,
            drawn_by,
            out,
            scale=scale if scale_override is None else scale_override,
            page=page if page_override is None else page_override,
            pmi=pmi,
            source=source,
            model=model,
            decorations=decorations,
            authored=authored,
            requested=requested,
            material=material,
            date=date,
            revision=revision,
            company=company,
            approved_by=approved_by,
            document_type=document_type,
            sheet=sheet,
            margin_left=margin_left,
            margin_right=margin_right,
            margin_top=margin_top,
            margin_bottom=margin_bottom,
            title_block_width=title_block_width,
            frame=frame,
            projection=projection,
            projection_symbol=projection_symbol,
            text_position=text_position,
            text_orientation=text_orientation,
            leader_region=leader_region,
            zones=zones,
            _reuse=reuse,
            _required_tables=_required_tables,
            _arrangements=(
                _arrangements if arrangements_override is None else arrangements_override
            ),
            _views=views,
            _include_iso=_include_iso,
            _view_constraints=_view_constraints,
            _plan_automatic_details=detail_view,
            _framed_recognition=framed_recognition,
            _document_input=_document_input,
            _scale_from_prior_analysis=(
                scale_override is not None and reuse is not None and scale_override == reuse.SCALE
            ),
        )

    with stage("analysis"):
        a = analyse(reuse=_analysis_base, views=_views)
    planned_principals = third_angle_view_names() if _views is None else _views
    # Measured dimensions are model-routed (ADR 1 (was 0015)) and therefore do not enter
    # plan_dimensions' requirement check.  An authored principal set is nevertheless
    # a hard constraint: reject a measured mark targeting an absent projection before
    # corridor placement can misreport the contradiction as a capacity drop.
    explicit_model = (
        _coerce_model(model, a.part, decorations, requested, authored)
        if model is not None
        else cast("PartModel", a.model if a.model is not None else build_model(a))
    )
    uncovered_measured = []
    for feature in explicit_model.features:
        feature_view = authored_dimension_target_view(
            getattr(feature, "dimension_kind", ""),
            getattr(feature, "dominant_axis", ""),
            getattr(feature, "view", None),
            getattr(feature, "side", None),
            getattr(feature, "angular_reference", None),
            getattr(feature, "ref_pts", ()),
        )
        if (
            getattr(feature, "kind", None) != "authored_dimension"
            or not isinstance(feature_view, str)
            or feature_view in set(planned_principals)
        ):
            continue
        uncovered_measured.append(
            UncoveredViewRequirement(
                identity=feature,
                label=getattr(feature, "source_id", "") or "measured_dimension",
                preferred_view=feature_view,
                eligible_views=(feature_view,),
                reason=f"is explicitly placed in `{feature_view}`",
            )
        )
    if uncovered_measured:
        raise ViewPlanIncomplete(planned_principals, uncovered_measured)
    if auto_dims and not {"front", "rear"}.intersection(planned_principals):
        # A model without an envelope can still approve a synthetic bbox height.
        # It has no feature parameter for plan_dimensions to check, so prove its
        # compiled view requirement before projecting a reduced principal set.
        from draftwright.model.compiled import compile_dimensions

        overall = compile_dimensions(explicit_model).ladder("overall_height")
        if overall is not None:
            height = overall.rungs[0]
            raise ViewPlanIncomplete(
                planned_principals,
                [
                    UncoveredViewRequirement(
                        identity=height.id,
                        label="overall_height.length",
                        preferred_view="front",
                        eligible_views=("front", "rear"),
                        reason="requires a planned front or rear view",
                    )
                ],
            )
    view_attempts: tuple[dict[str, object], ...] = ()
    view_status = "selected"
    if _select_automatic_views and _views is None and auto_dims:
        candidate_views = _automatic_turned_principals(a)
        if candidate_views is not None:
            planning_model = (
                _coerce_model(model, a.part, decorations, requested, authored)
                if model is not None
                else cast("PartModel", a.model if a.model is not None else build_model(a))
            )
            # Dimensions are not the only annotations with view requirements.  GD&T,
            # surface-finish, datum, and manufacturing-note aspects carry an explicit target
            # view in the IR and intentionally bypass the dimension planner.  Fail closed
            # before projection when an automatic reduction would erase one of those targets.
            aspect_views = set()
            for feature in planning_model.features:
                view = getattr(feature, "view", None)
                if getattr(feature, "kind", None) == "authored_dimension":
                    view = authored_dimension_target_view(
                        getattr(feature, "dimension_kind", ""),
                        getattr(feature, "dominant_axis", ""),
                        view,
                        getattr(feature, "side", None),
                        getattr(feature, "angular_reference", None),
                        getattr(feature, "ref_pts", ()),
                    )
                if isinstance(view, str) and view in third_angle_view_names():
                    aspect_views.add(view)
            missing_aspect_views = tuple(sorted(aspect_views - set(candidate_views)))
            uncovered = missing_aspect_views
            reason = "annotation_view_required" if uncovered else None
            if not uncovered:
                try:
                    plan_dimensions(planning_model, planned_views=candidate_views)
                except ViewPlanIncomplete as exc:
                    reason = "dimension_requirement_uncovered"
                    uncovered = tuple(item.label for item in exc.uncovered)
            candidate_analysis = None
            if reason is None:
                try:
                    # The analysis sizing model can legitimately carry requirements not in a
                    # caller's authored rendering subset (for example an emitted script with
                    # every dimension line commented out). It is an independent preflight and
                    # must reject the proposal rather than escape the automatic-view gate.
                    candidate_analysis = analyse(reuse=a, views=candidate_views)
                except ViewPlanIncomplete as exc:
                    reason = "dimension_requirement_uncovered"
                    uncovered = tuple(item.label for item in exc.uncovered)
            if reason is not None:
                view_status = "retained_for_requirements"
                view_attempts = (
                    {
                        "views": candidate_views,
                        "status": "rejected",
                        "reason": reason,
                        "uncovered": uncovered,
                        "blockers": (),
                    },
                )
            else:
                # Recognition/model construction has already happened. Re-resolve only the
                # scale/page/view geometry under the candidate before any projection or
                # annotation is built, so the common accepted path still compiles once.
                assert candidate_analysis is not None
                a = candidate_analysis
                view_status = "candidate"
                view_attempts = (
                    {
                        "views": candidate_views,
                        "status": "candidate",
                        "reason": "redundant_radial_view_removed",
                        "blockers": (),
                    },
                )

    selected_profile = None
    pre_render_choice = None
    if _candidate_profile_first:
        # A rejected profile must not donate its arrangement to the conservative
        # recomposition. Keep the settled caller analysis as the common fork.
        pre_profile_analysis = a
        pre_render_choice = choose_pre_render_profile(
            a.layout_strips,
            a.layout_strips.annotation_scheme_shadow_report(a.SCALE),
            page=(a.PAGE_W, a.PAGE_H),
            views=tuple(a.planned_views or third_angle_view_names()),
            auto_dims=auto_dims,
        )
        profile_name = pre_render_choice["profile"]
        if isinstance(profile_name, str):
            selected_profile = candidate_profile(profile_name, a.SCALE)
            with use_layout_profile(selected_profile):
                a = analyse(
                    reuse=a,
                    views=tuple(a.planned_views or third_angle_view_names()),
                    scale_override=a.SCALE,
                    page_override=(a.PAGE_W, a.PAGE_H),
                    arrangements_override=(selected_profile.arrangement or a.arrangement,),
                )
            lost_derived = lost_required_derived_view_reservations(pre_profile_analysis, a)
            if lost_derived:
                # Recomposition is allowed to move a required detail's box, but
                # never erase it while keeping the caller's page/scale pinned.
                # The conservative analysis is already available; this still
                # builds exactly one drawing and needs no baseline comparison.
                selected_profile = None
                a = pre_profile_analysis
                pre_render_choice = {
                    **pre_render_choice,
                    "proposed_profile": profile_name,
                    "profile": None,
                    "reason": "required_derived_view_reservation_lost",
                    "lost_derived_views": list(lost_derived),
                }
            # The protected-gutter profile can worsen a caller-fixed, already
            # overfull sheet by pushing principal views beyond its physical page.
            # Compare only cheap pre-render geometry.  If the established
            # conservative profile reduces at least one per-view overflow and
            # increases none, select it *before* building the one drawing.
            # This is neither a finished-baseline comparison nor a safety
            # admission. FTC09's plan moved 6 mm farther out, withholding a
            # width and PMI carrier that the conservative profile preserves.
            overflow = pre_render_view_page_overflow(a)
            if selected_profile is not None and selected_profile.view_gutters and overflow:
                conservative_profile = candidate_profile("iso-growth", a.SCALE)
                with use_layout_profile(conservative_profile):
                    conservative_analysis = analyse(
                        reuse=pre_profile_analysis,
                        views=tuple(
                            pre_profile_analysis.planned_views or third_angle_view_names()
                        ),
                        scale_override=pre_profile_analysis.SCALE,
                        page_override=(pre_profile_analysis.PAGE_W, pre_profile_analysis.PAGE_H),
                        arrangements_override=(
                            conservative_profile.arrangement or pre_profile_analysis.arrangement,
                        ),
                    )
                conservative_overflow = pre_render_view_page_overflow(conservative_analysis)
                views_to_compare = set(overflow) | set(conservative_overflow)
                no_worse = all(
                    conservative_overflow.get(view, 0.0) <= overflow.get(view, 0.0) + 1e-6
                    for view in views_to_compare
                )
                strictly_better = any(
                    conservative_overflow.get(view, 0.0) < overflow.get(view, 0.0) - 1e-6
                    for view in views_to_compare
                )
                # The overflow comparison protects principal geometry, but a
                # different profile must also retain required derived space.
                # Otherwise this second pre-render choice can undo the guard
                # above without ever building a drawing for the lost detail.
                lost_conservative_derived = lost_required_derived_view_reservations(
                    pre_profile_analysis, conservative_analysis
                )
                if no_worse and strictly_better and not lost_conservative_derived:
                    selected_profile = conservative_profile
                    a = conservative_analysis
                    pre_render_choice = {
                        **pre_render_choice,
                        "proposed_profile": profile_name,
                        "profile": "iso-growth",
                        "reason": "protected_gutters_worsen_off_page_views",
                        "proposed_view_overflow_mm": overflow,
                        "selected_view_overflow_mm": conservative_overflow,
                    }

    # Pass 1: place + annotate from the estimated layout, then measure the real
    # per-view footprints and re-pack the blocks disjoint if a view actually
    # moves (#121, ADR 2 (was 0004) — "lay out, don't predict").  Non-ballooned parts
    # measure ≈ estimate, so they skip pass 2 and stand byte-identical.
    with use_layout_profile(selected_profile) if selected_profile else nullcontext():
        dwg = _assemble(
            a,
            out,
            assembly,
            detail_view,
            auto_dims,
            model=model,
            decorations=decorations,
            requested=requested,
            authored=authored,
            trace=tracer,
            critique_recognition_cache=_critique_recognition_cache,
            reproducible=reproducible,
            title_block_cache=_title_block_cache,
        )
        if auto_dims:
            repacked = _repack_to_fixed_point(
                a,
                dwg,
                out,
                assembly,
                detail_view,
                # The candidate profile was chosen against this settled sheet and
                # scale before rendering. A measured repack may move views within
                # them, but must not search a different sheet/scale behind the
                # recorded choice (even when the caller requested automatic fit).
                scale=a.SCALE if _candidate_profile_first else scale,
                page=(a.PAGE_W, a.PAGE_H) if _candidate_profile_first else page,
                model=model,
                decorations=decorations,
                requested=requested,
                authored=authored,
                trace=tracer,
                critique_recognition_cache=_critique_recognition_cache,
                reproducible=reproducible,
                placement_critique=_placement_critique,
            )
            if repacked is not None:
                a, dwg = repacked
        if repair:
            # Close the loop on the greedy placement: re-place dims behind any
            # mechanically-clear violations (overlap, wrong-side) and re-lint (#30).
            # A no-op on a clean sheet, so default-on costs nothing when there is
            # nothing to fix.
            if _placement_critique is None:
                dwg.repair()
            else:
                dwg.repair(
                    _initial_issues=_placement_critique.get(dwg),
                    _on_settled=lambda issues: _placement_critique.remember(dwg, issues),
                )
    # Reconcile after the final repack/repair, against live registry identities.
    # This is diagnostic evidence only; it cannot substitute for requirement lint.
    scheme = a.layout_strips.scheme
    if auto_dims and scheme is not None:
        dwg.annotation_scheme_decision = {
            **dwg.annotation_scheme_decision,
            "carrier_evidence": annotation_demand_carrier_evidence(scheme, dwg.registry),
        }
    if _candidate_profile_first:
        dwg.annotation_scheme_decision = {
            **dwg.annotation_scheme_decision,
            "policy": "demand-guided",
            "status": "demand_guided" if selected_profile else "no_demand_profile",
            "influenced_layout": selected_profile is not None,
            "admission_ready": False,
            "pre_render_choice": pre_render_choice,
        }
    if tracer is not None:  # one JSON per build; Drawing.finalize() re-writes it (#736)
        tracer.write()
    if _analysis_sink is not None:
        _analysis_sink(a)
    dwg.view_decision = {
        "policy": "automatic" if _select_automatic_views else "selected",
        "status": view_status,
        "chosen": tuple(dwg.view_plan.principal_names),
        "attempts": view_attempts,
    }
    return dwg
