"""Ordered finished-drawing lint orchestration (#1928).

The Drawing wrapper supplies build-owned state explicitly. This module does not import
Drawing or the model package; structural and physical checks remain independent of the
compiler's approved plan except where the caller deliberately supplies that plan.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from quiddity import RecognitionResult, analyse_cylinders

from draftwright.linting import (
    _suggest_fix,
    lint_angled_step_coverage,
    lint_axial_coverage,
    lint_blend_coverage,
    lint_blend_leader_targets,
    lint_boss_height_coverage,
    lint_chamfer_coverage,
    lint_channel_coverage,
    lint_circular_blind_step_coverage,
    lint_claimed_representations,
    lint_declaration_reconciliation,
    lint_declared_gear_coverage,
    lint_drawing,
    lint_feature_coverage,
    lint_fillet_coverage,
    lint_flat_coverage,
    lint_groove_coverage,
    lint_gusset_rib_coverage,
    lint_hole_coverage,
    lint_location_coverage,
    lint_manufacturing_references,
    lint_oriented_slot_coverage,
    lint_pad_coverage,
    lint_paired_ramp_step_coverage,
    lint_plate_coverage,
    lint_pmi_extraction,
    lint_pmi_ignored,
    lint_pmi_lowering,
    lint_pmi_rendering,
    lint_pmi_source_unknown,
    lint_pmi_unreconciled,
    lint_pocket_coverage,
    lint_pocket_pattern_coverage,
    lint_polygonal_boss_coverage,
    lint_polygonal_stock_coverage,
    lint_principal_profile_coverage,
    lint_prismatic_coverage,
    lint_profiled_bore_coverage,
    lint_rectangular_blind_slot_coverage,
    lint_round_bottom_blind_slot_coverage,
    lint_slot_coverage,
    lint_through_step_coverage,
    lint_turned_profile_span,
)
from draftwright.linting.angular import lint_angular_supports, lint_profile_angle_coverage
from draftwright.linting.section_recess_coverage import (
    lint_circular_channel_coverage,
    lint_hex_pocket_coverage,
    lint_section_recess_coverage,
)
from draftwright.registry import PlacedDimension


@dataclass
class LintContext:
    """Current drawing surfaces and build-owned caches supplied at the wrapper boundary."""

    drawing: Any
    page_bbox: tuple[float, float, float, float]
    views: dict
    items: list
    scale: float
    material_fields: Any
    registry: Any
    working_part: Any
    analysis: Any
    build: Any
    model: Any
    coverage: Any
    assembly: bool | None
    model_declared: bool
    view_edge_cache: dict
    ann_box_cache: dict
    cyl_cache: Any


@dataclass
class _PhysicalEvidence:
    """Recognition inventory selected for one physical critique."""

    analysis: Any
    cyls: Any
    holes: list | None
    patterns: list | None
    bosses: list | None
    pads: list | None
    recognition: RecognitionResult
    prof_kw: dict


def _lint_structure(ctx: LintContext, aggregation: Any, display_decimals: Any) -> list:
    """Judge settled page and annotation ink in one paired pass."""
    # Names and shapes come out of ONE traversal. Two comprehensions over an
    # unmutated dict would in fact agree — Python guarantees the iteration order —
    # so this is defensive style, not a fixed hazard: it keeps the positional
    # contract lint relies on visible in one line instead of implied across two.
    view_items = list(ctx.views.items())
    view_names = [name for name, _pair in view_items]
    view_shapes = [vis for _name, (vis, _hidden) in view_items]
    # Prune box-cache entries for objects no longer on the sheet, so replaced
    # annotations (repair's _replace_dim swaps in a fresh object) don't keep
    # their OCC geometry strongly referenced for the drawing's lifetime.
    live = {id(i) for i in ctx.items} | {id(v) for v in view_shapes}
    for stale in [k for k in ctx.ann_box_cache if k not in live]:
        del ctx.ann_box_cache[stale]
    # ONE call over every annotation. Most annotations are at sheet scale; a non-sheet-scale
    # view (the enlarged detail view, #42) records each dimension's scale in the registry,
    # and `_lint_dim` receives it so `label_vs_measured` still compares each
    # annotation against ITS OWN scale.
    #
    # This used to pre-split the items by scale and call `lint_drawing` once per group. The
    # split made the PAIRWISE checks — `annotation_overlap`, `label_centerline_overlap`,
    # `leader_line_through_text` — blind across groups, because each call only ever saw one
    # group's items. A detail view's dimensions and its own caption are always in different
    # groups AND spatially adjacent by construction, so the pair most likely to collide was
    # the one pair never compared (#1216).
    #
    # Collapsing it also retires #1204's first-group restriction: `view_overlap` and
    # `view_out_of_bounds` compare views to each other and to the page, so passing the views
    # to every group emitted their findings once PER GROUP and made `by_code`, the
    # error/warning counts and the quality score a function of how annotations happened to be
    # grouped. With a single call there are no groups to double-count.
    named_specs = {
        id(obj): ctx.registry.dimension_spec_of(name)
        for name, obj in ctx.registry.iter_named()
        if isinstance(obj, PlacedDimension)
    }
    # The raw unnamed placement verb has no registry identity. Its owned construction
    # spec is the only source for the same label/precision critique.
    unnamed_specs = {
        id(obj): obj.placement_spec
        for obj in ctx.items
        if isinstance(obj, PlacedDimension) and id(obj) not in named_specs
    }
    issues = lint_drawing(
        ctx.items,
        page_bbox=ctx.page_bbox,
        drawing_scale=ctx.scale,
        view_shapes=view_shapes,
        view_names=view_names,
        view_edge_cache=ctx.view_edge_cache,
        ann_box_cache=ctx.ann_box_cache,
        view_material_fields=ctx.material_fields(),
        _aggregation=aggregation,
        display_decimals=display_decimals,
        annotation_names={id(obj): name for name, obj in ctx.registry.iter_named()},
        annotation_views={
            id(obj): view
            for name, obj in ctx.registry.iter_named()
            if (view := ctx.registry.view_of(name)) is not None
        },
        annotation_regions={
            id(obj): ctx.registry.candidate_region_of(name)
            for name, obj in ctx.registry.iter_named()
        },
        annotation_scales={
            id(obj): scale
            for name, obj in ctx.registry.iter_named()
            if (scale := ctx.registry.scale_of(name)) is not None
        },
        annotation_specs=named_specs | unnamed_specs,
    )
    return issues


def _physical_inventory(ctx: LintContext) -> _PhysicalEvidence:
    """Use build recognition or request it once for independent physical critique."""
    working_part = ctx.working_part
    # Reuse the single feature inventory from the build (#244) when present,
    # so lint does not re-detect holes/patterns/turned-steps; fall back to
    # detecting when there is no analysis (a manually-built Drawing, or lint
    # called mid-build before _analysis is attached).
    a = ctx.analysis
    holes: list | None
    patterns: list | None
    bosses: list | None
    pads: list | None
    recognition: RecognitionResult | None
    prof_kw: dict
    if a is not None and a.recognition is not None:
        cyls = a.cyls
        holes, patterns, bosses = a.holes, a.patterns, a.bosses
        pads = a.pads
        # The whole aggregate, not a level set: coverage projects the shared riser
        # evidence over recognition's OWN levels, so no caller can narrow the
        # shoulder inventory (#1025).
        recognition = a.recognition
        prof_kw = {"profiles": a.profiles}
    elif a is not None:
        # A DECLARED build recognised nothing (#1022), so `a.holes` and friends are
        # empty because nothing looked — not because the part has none. Feeding that
        # emptiness to coverage would report every real hole as uncovered, so critique
        # recognises here instead: once per drawing, owned by BuildState.
        rec = ctx.build.ensure_recognition(working_part, cylinders=a.cyls)
        cyls = rec.cylinders
        holes = list(rec.holes)
        patterns = list(rec.hole_patterns)
        bosses = list(rec.bosses)
        pads = list(rec.pads)
        # The aggregate, NOT anything off `Analysis`: its levels and risers are
        # geometry-sourced, where the declared `Analysis` carries what the author
        # declared. Critique taking its inventory from the model is what ADR 1 (was 0015)
        # forbids — it would make lint blind to exactly the geometry a sparse
        # declaration omitted, the case `unrecognised_defining_geometry` reports.
        recognition = rec
        # These profiles ARE declaration-sourced, and here that is right rather than a
        # shortcut: axial critique judges each declared profile's dimensioning, so a
        # declared turned part keeps them without importing detected coordinates.
        prof_kw = {"profiles": a.profiles}
    else:
        if ctx.cyl_cache is None:
            ctx.cyl_cache = analyse_cylinders(working_part)
        cyls = ctx.cyl_cache
        holes = patterns = bosses = None
        pads = recognition = None
        prof_kw = {}
    if recognition is None:
        recognition = ctx.build.ensure_recognition(working_part, cylinders=cyls)
    return _PhysicalEvidence(a, cyls, holes, patterns, bosses, pads, recognition, prof_kw)


def _lint_physical_common(ctx: LintContext, evidence: _PhysicalEvidence, registry: Any) -> list:
    """Check angular, dimensional, prismatic, and recess coverage in order."""
    issues = []
    working_part = ctx.working_part
    model = ctx.model
    a = evidence.analysis
    cyls = evidence.cyls
    holes = evidence.holes
    patterns = evidence.patterns
    bosses = evidence.bosses
    pads = evidence.pads
    recognition = evidence.recognition
    prof_kw = evidence.prof_kw
    physical_registry = registry
    issues += lint_angular_supports(
        ctx.items,
        registry=ctx.registry,
        evidence=ctx.build.recognition_evidence,
        ownership=ctx.build.recognition_ownership,
        to_page=ctx.drawing.at,
    )
    issues += lint_profile_angle_coverage(
        ctx.build.recognition_evidence,
        ctx.build.recognition_ownership,
        getattr(ctx.model, "features", ()),
        physical_registry,
        ctx.build.omissions,
    )
    profiled_bores = list(recognition.double_d_bores)
    issues += lint_feature_coverage(
        working_part,
        ctx.items,
        cyls=cyls,
        exclude=ctx.coverage.dropped_diams,
        assembly=ctx.assembly,
        holes=holes,
        bosses=bosses,
        blends=recognition.blends,
        recognition_evidence=ctx.build.recognition_evidence,
        # The same profile set `detect` used, so the boss/blend absorption decision
        # is one decision rather than two that can disagree. `prof_kw` is empty only
        # where detect also falls back to the aggregate.
        **({"turned_profiles": prof_kw["profiles"]} if "profiles" in prof_kw else {}),
        registry=physical_registry,
        # Counts belong to lint_hole_coverage's operation/ownership ledger.
        check_hole_counts=False,
    )
    issues += lint_axial_coverage(
        working_part,
        ctx.drawing,
        assembly=ctx.assembly,
        registry=physical_registry,
        recognition=recognition,
        **prof_kw,
    )
    if model is not None:
        issues += lint_boss_height_coverage(
            working_part,
            ctx.drawing,
            getattr(model, "features", ()),
            assembly=ctx.assembly,
            registry=physical_registry,
            omissions=ctx.build.omissions,
        )
    issues += lint_location_coverage(
        working_part,
        ctx.drawing,
        cyls=cyls,
        assembly=ctx.assembly,
        holes=holes,
        patterns=patterns,
        profiled_bores=profiled_bores,
        registry=physical_registry,
    )
    issues += lint_prismatic_coverage(
        working_part,
        ctx.drawing,
        assembly=ctx.assembly,
        registry=physical_registry,
        pads=pads,
        section_recesses=recognition.section_recesses,
        bbox=a.bb if a is not None else None,
        features=getattr(model, "features", ()) if model is not None else (),
        recognition=recognition,
    )
    issues += lint_pad_coverage(
        working_part,
        recognition=recognition,
        features=getattr(model, "features", ()) if model is not None else (),
        registry=physical_registry,
        omissions=ctx.build.omissions,
        assembly=ctx.assembly,
    )
    issues += lint_plate_coverage(
        working_part,
        recognition=recognition,
        features=getattr(model, "features", ()) if model is not None else (),
        registry=physical_registry,
        omissions=ctx.build.omissions,
        assembly=ctx.assembly,
    )
    issues += lint_polygonal_boss_coverage(
        working_part,
        recognition=recognition,
        features=getattr(model, "features", ()) if model is not None else (),
        registry=physical_registry,
        omissions=ctx.build.omissions,
        evidence=ctx.build.recognition_evidence,
        assembly=ctx.assembly,
    )
    issues += lint_section_recess_coverage(recognition)
    issues += lint_hex_pocket_coverage(
        recognition,
        getattr(model, "features", ()),
        physical_registry,
        ctx.build.omissions,
    )
    issues += lint_circular_channel_coverage(
        recognition,
        getattr(model, "features", ()),
        physical_registry,
        ctx.build.omissions,
        bbox=a.bb if a is not None else None,
    )
    issues += lint_angled_step_coverage(recognition)
    return issues


def _lint_physical_profiles(ctx: LintContext, evidence: _PhysicalEvidence, registry: Any) -> list:
    """Check principal profiles, edge families, and blind features in order."""
    issues = []
    working_part = ctx.working_part
    model = ctx.model
    cyls = evidence.cyls
    recognition = evidence.recognition
    physical_registry = registry
    resolved_assembly = ctx.assembly
    if resolved_assembly is None:
        resolved_assembly = len(working_part.solids()) > 1
    profile_cache = ctx.build.principal_profile_cache
    if (
        profile_cache is None
        or profile_cache[0] is not working_part
        or profile_cache[1] != resolved_assembly
    ):
        profile_issues = tuple(
            lint_principal_profile_coverage(
                working_part,
                assembly=resolved_assembly,
                double_d_bores=recognition.double_d_bores,
                section_recesses=recognition.section_recesses,
            )
        )
        profile_cache = (working_part, resolved_assembly, profile_issues)
        ctx.build.principal_profile_cache = profile_cache
    issues += list(profile_cache[2])
    issues += lint_profiled_bore_coverage(
        working_part,
        ctx.items,
        recognition=recognition,
        features=getattr(model, "features", ()) if model is not None else (),
        registry=physical_registry,
        dropped_profiles=ctx.coverage.dropped_profiles,
        dropped_profile_evidence=ctx.coverage.dropped_profile_evidence,
        assembly=ctx.assembly,
    )
    issues += lint_flat_coverage(
        working_part,
        recognition=recognition,
        features=getattr(model, "features", ()) if model is not None else (),
        registry=physical_registry,
        omissions=ctx.build.omissions,
        assembly=ctx.assembly,
    )
    issues += lint_groove_coverage(
        working_part,
        recognition=recognition,
        features=getattr(model, "features", ()) if model is not None else (),
        registry=physical_registry,
        omissions=ctx.build.omissions,
        assembly=ctx.assembly,
    )
    issues += lint_chamfer_coverage(
        working_part,
        recognition=recognition,
        features=getattr(model, "features", ()) if model is not None else (),
        registry=physical_registry,
        omissions=ctx.build.omissions,
        assembly=ctx.assembly,
    )
    issues += lint_fillet_coverage(
        working_part,
        recognition=recognition,
        features=getattr(model, "features", ()) if model is not None else (),
        registry=physical_registry,
        omissions=ctx.build.omissions,
        assembly=ctx.assembly,
    )
    issues += lint_blend_leader_targets(
        registry=physical_registry,
        cylinders=cyls,
        project=ctx.drawing.at,
        evidence=ctx.build.recognition_evidence,
        ownership=ctx.build.recognition_ownership,
    )
    issues += lint_blend_coverage(
        working_part,
        recognition=recognition,
        features=getattr(model, "features", ()) if model is not None else (),
        registry=physical_registry,
        omissions=ctx.build.omissions,
        assembly=ctx.assembly,
    )
    issues += lint_paired_ramp_step_coverage(
        working_part,
        recognition=recognition,
        features=getattr(model, "features", ()) if model is not None else (),
        registry=physical_registry,
        omissions=ctx.build.omissions,
        assembly=ctx.assembly,
    )
    issues += lint_gusset_rib_coverage(
        working_part,
        recognition=recognition,
        features=getattr(model, "features", ()) if model is not None else (),
        registry=physical_registry,
        omissions=ctx.build.omissions,
        evidence=ctx.build.recognition_evidence,
        ownership=ctx.build.recognition_ownership,
        assembly=ctx.assembly,
    )
    issues += lint_circular_blind_step_coverage(
        working_part,
        recognition=recognition,
        features=getattr(model, "features", ()) if model is not None else (),
        registry=physical_registry,
        omissions=ctx.build.omissions,
        assembly=ctx.assembly,
    )
    issues += lint_rectangular_blind_slot_coverage(
        working_part,
        recognition=recognition,
        features=getattr(model, "features", ()) if model is not None else (),
        registry=physical_registry,
        omissions=ctx.build.omissions,
        assembly=ctx.assembly,
    )
    issues += lint_round_bottom_blind_slot_coverage(
        working_part,
        recognition=recognition,
        features=getattr(model, "features", ()) if model is not None else (),
        registry=physical_registry,
        omissions=ctx.build.omissions,
        assembly=ctx.assembly,
    )
    issues += lint_oriented_slot_coverage(
        working_part,
        recognition=recognition,
        features=getattr(model, "features", ()) if model is not None else (),
        registry=physical_registry,
        omissions=ctx.build.omissions,
        assembly=ctx.assembly,
    )
    return issues


def _lint_physical_remaining(
    ctx: LintContext, evidence: _PhysicalEvidence, registry: Any, dimension_plan: Any
) -> list:
    """Check through features, holes, pockets, and declared correspondence."""
    issues = []
    working_part = ctx.working_part
    model = ctx.model
    cyls = evidence.cyls
    recognition = evidence.recognition
    physical_registry = registry
    issues += lint_through_step_coverage(
        working_part,
        recognition=recognition,
        features=getattr(model, "features", ()) if model is not None else (),
        registry=physical_registry,
        omissions=ctx.build.omissions,
        assembly=ctx.assembly,
        plan=dimension_plan,
    )
    issues += lint_slot_coverage(
        working_part,
        recognition=recognition,
        features=getattr(model, "features", ()) if model is not None else (),
        registry=physical_registry,
        omissions=ctx.build.omissions,
        assembly=ctx.assembly,
    )
    issues += lint_hole_coverage(
        working_part,
        recognition=recognition,
        features=getattr(model, "features", ()) if model is not None else (),
        registry=physical_registry,
        omissions=ctx.build.omissions,
        assembly=ctx.assembly,
        project=ctx.drawing.at,
        evidence=ctx.build.recognition_evidence,
        ownership=ctx.build.recognition_ownership,
        declared=ctx.model_declared,
    )
    issues += lint_channel_coverage(
        working_part,
        recognition=recognition,
        features=getattr(model, "features", ()) if model is not None else (),
        registry=physical_registry,
        omissions=ctx.build.omissions,
        assembly=ctx.assembly,
    )
    issues += lint_polygonal_stock_coverage(
        working_part,
        recognition=recognition,
        features=getattr(model, "features", ()) if model is not None else (),
        registry=physical_registry,
        omissions=ctx.build.omissions,
        assembly=ctx.assembly,
    )
    issues += lint_pocket_coverage(
        working_part,
        recognition=recognition,
        features=getattr(model, "features", ()) if model is not None else (),
        registry=physical_registry,
        omissions=ctx.build.omissions,
        assembly=ctx.assembly,
    )
    issues += lint_pocket_pattern_coverage(
        working_part,
        recognition=recognition,
        features=getattr(model, "features", ()) if model is not None else (),
        registry=physical_registry,
        omissions=ctx.build.omissions,
        assembly=ctx.assembly,
    )
    issues += lint_declared_gear_coverage(
        features=getattr(model, "features", ()) if model is not None else (),
        registry=physical_registry,
        profiles=getattr(recognition, "repeating_radial_profiles", None),
        assembly=ctx.assembly,
    )
    # Reverse direction (#487): a DECLARED feature with no matching geometry (a stale
    # phantom callout). Only for a caller-supplied model — detection can't over-declare.
    # _part_model is typed `object` (deliberately loose, #397); read features duck-typed.
    if ctx.model_declared and ctx.model is not None:
        features = getattr(ctx.model, "features", ())
        issues += lint_declaration_reconciliation(
            features,
            cyls,
            recognition=recognition,
        )
        # Declared-vs-geometry in the axial direction (#1132): a z-turned profile that
        # leaves part of the body undescribed. Gated with the reconciliation check
        # above because both are only meaningful for a caller-declared model — the
        # detection path is NOT immune — it is scoped this way for a different reason.
        #
        # Measured on CADGenBench 132: a plain `build_drawing(<step file>)` detects
        # z-steps covering 0..113 of a 140 mm body and reports nothing here, while this
        # predicate applied to that same detected model returns the warning. Same part,
        # same shortfall, one door silent. It is scoped to the declared model because
        # that is where the raise it replaces lived, so this change alters no automatic
        # drawing. Widening it is a real question and a separate one.
        issues += lint_turned_profile_span(
            features,
            (ctx.analysis.bb.min.Z, ctx.analysis.bb.max.Z),
            orientation=getattr(ctx.model, "orientation", None),
            single_solid=len(ctx.analysis.part.solids()) == 1,
        )
    return issues


def _lint_pmi(ctx: LintContext) -> list:
    """Reconcile extracted PMI and rendered marks in census order."""
    issues = []
    issues += lint_pmi_ignored(
        ctx.analysis.pmi_report,
        ctx.analysis.pmi_mode,
        defaulted=ctx.analysis.pmi_defaulted,
    )
    issues += lint_pmi_extraction(ctx.analysis.pmi_report, ctx.analysis.pmi_mode)
    issues += lint_pmi_lowering(
        ctx.analysis.pmi_report,
        getattr(ctx.model, "features", ()),
        ctx.analysis.pmi_mode,
        decorations=getattr(ctx.model, "decorations", {}),
    )
    issues += lint_pmi_rendering(
        getattr(ctx.model, "features", ()),
        ctx.registry,
        ctx.analysis.pmi_mode,
        decorations=getattr(ctx.model, "decorations", {}),
        report=ctx.analysis.pmi_report,
    )
    # The two directions the four checks above cannot cover, because each of them
    # reasons FROM the census: content whose census is missing entirely, and content
    # claiming an identity the census does not contain (#1563). Both take the report
    # itself rather than the mode — an absent census is the condition, not a setting.
    issues += lint_pmi_unreconciled(
        ctx.analysis.pmi_report,
        getattr(ctx.model, "features", ()),
        decorations=getattr(ctx.model, "decorations", {}),
    )
    issues += lint_pmi_source_unknown(
        ctx.analysis.pmi_report,
        getattr(ctx.model, "features", ()),
        decorations=getattr(ctx.model, "decorations", {}),
    )
    return issues


def lint_finished_drawing(
    ctx: LintContext,
    *,
    physical: bool,
    aggregation: Any,
    dimension_plan: Any,
    display_decimals: Any,
) -> list:
    """Run structural, physical, PMI, and build-issue checks in their established order."""
    issues = _lint_structure(ctx, aggregation, display_decimals)
    if physical and ctx.working_part is None:
        issues += lint_angular_supports(ctx.items)
    if physical and ctx.working_part is not None:
        evidence = _physical_inventory(ctx)
        if dimension_plan is not None:
            issues += lint_claimed_representations(ctx.registry, dimension_plan)
        from draftwright.linting.schedule_evidence import verified_schedule_registry

        physical_registry = verified_schedule_registry(ctx.registry, dimension_plan)
        issues += _lint_physical_common(ctx, evidence, physical_registry)
        issues += _lint_physical_profiles(ctx, evidence, physical_registry)
        issues += _lint_physical_remaining(ctx, evidence, physical_registry, dimension_plan)
    if physical and ctx.analysis is not None:
        issues += _lint_pmi(ctx)
    issues += lint_manufacturing_references(ctx.registry)
    issues += list(ctx.registry.issues)
    # Attach a ready-to-paste fix snippet where one is computable (#29).
    # str | None — None when no concrete repair can be inferred.
    for i in issues:
        i.suggestion = _suggest_fix(i, ctx.drawing)
    return issues
