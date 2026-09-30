"""Finished-drawing diagnostic and report coordination (ADRs 1, 3, 5).

Drawing owns mutable state and public method dispatch. This rank-5 owner receives
that state and callbacks explicitly; read-only evidence remains at rank 2.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Mapping
from contextvars import ContextVar
from typing import Any

from draftwright._core import _frame_margins, _log
from draftwright.linting.evidence import compiled_display_precisions
from draftwright.linting.issues import _collect_issue_aggregation, _IssueAggregation
from draftwright.linting.orchestration import LintContext, lint_finished_drawing

# Codes that check standards/geometry correctness rather than pure page
# layout. Grouped so a caller (and the #30 repair loop) can tell a wrong
# drawing from a merely tight one.
_GEOMETRY_AWARE_CODES = frozenset(
    {
        "angled_step_requirement_unsupported",
        "feature_not_dimensioned",
        "feature_count_mismatch",
        "feature_not_located",
        "feature_no_centermark",
        "pad_footprint_not_defined",
        "passage_requirement_unsupported",
        "prismatic_pocket_requirement_unsupported",
        "section_recess_requirement_unsupported",
        "section_recess_recognition_refused",
        "pocket_not_located",
        "unrecognised_defining_geometry",
        "pmi_not_lowered",
        "pmi_not_rendered",
        "manufacturing_reference_unresolved",
        # Registered here for the same reason as the two above: an unverified or
        # fabricated AP242 claim is a statement about the geometry, not about layout
        # (#1563). Leaving them out would let a drawing carrying either report
        # `geometry_issues: 0`, which is the shape of defect this register exists for.
        "pmi_unreconciled",
        "pmi_source_unknown",
        # These REPLACE `pmi_not_rendered` for a record that produced no annotation (#1177);
        # the content is equally missing, so the count must not fall just because the
        # reason improved. `authored_dim_degenerate` suppressed that error without
        # carrying its weight, which is how a lost requirement came to report
        # `geometry_issues: 0` alongside `passed: True`.
        "dimension_kind_unsupported",
        "authored_dim_degenerate",
        "authored_dim_source_unresolved",
        "axial_length_missing",
        # A turned profile that leaves part of the body undescribed (#1132). Registered
        # here for the same reason as `axial_length_missing` beside it: the shortfall is
        # about the part, not about where an annotation landed.
        "turned_profile_not_spanned",
        "flat_requirement_suppressed",
        "flat_requirement_missing",
        "flat_requirement_unverifiable",
        "groove_requirement_suppressed",
        "groove_requirement_missing",
        "groove_requirement_unverifiable",
        "hole_requirement_suppressed",
        "hole_requirement_missing",
        "hole_requirement_unverifiable",
        "pad_requirement_suppressed",
        "pad_requirement_missing",
        "pad_requirement_unverifiable",
        "plate_requirement_suppressed",
        "plate_requirement_missing",
        "plate_requirement_unverifiable",
        "polygonal_boss_requirement_suppressed",
        "polygonal_boss_requirement_missing",
        "polygonal_boss_requirement_unverifiable",
        "pocket_requirement_suppressed",
        "pocket_requirement_missing",
        "pocket_requirement_unverifiable",
        "missing_principal_dimension",
        "label_vs_measured",
        "angular_label_vs_geometry",
        "angular_geometry_mismatch",
        "angular_support_unverifiable",
        "angular_support_mismatch",
        "dim_inside_part",
        "callout_dropped",
        "location_ref_dropped",
        "off_axis_location_dropped",
        "hole_pattern_dim_dropped",
        "pocket_pattern_dim_dropped",
        "step_dim_dropped",
        "plate_thickness_dropped",
        "step_position_dropped",
        "chamfer_dropped",
        "channel_requirement_suppressed",
        "channel_requirement_missing",
        "channel_requirement_unverifiable",
        "channel_width_dropped",
        "flat_dropped",
        "polygonal_boss_dropped",
        "polygonal_stock_dropped",
        "polygonal_stock_length_dropped",
        "pmi_not_extracted",
        "placement_unsatisfiable",
        "pmi_dropped",
        # A withheld approved dimension is missing content, so both codes count as geometry
        # issues, like `missing_principal_dimension` above.
        "step_dim_withheld",
        "overall_dim_withheld",
    }
)

# Coarse 0–1 quality heuristic: a clean sheet scores 1.0; each issue subtracts
# a flat per-severity penalty (clamped at 0). A convenience signal only — the
# severity/code counts in the summary are the authoritative output.
_SCORE_ERROR_PENALTY = 0.2
_SCORE_WARNING_PENALTY = 0.05

# ``Drawing.report()`` and the completeness component must project one identical physical
# requirement roster.  Keep that report-scoped evidence task-local so the public
# ``lint_summary()`` signature and subclass dispatch remain unchanged.
_REPORT_REQUIREMENTS: ContextVar[tuple[object, Mapping[str, tuple[Any, ...]], object] | None] = (
    ContextVar("draftwright_report_requirements", default=None)
)

# A finished-layout assessment needs the raw issues and their summary together. Keep that
# result task-local for the duration of the assessment; Drawing remains editable after build.
_SCOPED_LINT: ContextVar[tuple[object, tuple, object] | None] = ContextVar(
    "draftwright_scoped_lint", default=None
)

# Build policy reads a finished attempt several times before returning the editable Drawing.
# Keep its physical critique only for that call tree; public lint after return remains live.
_BUILD_LINT: ContextVar[dict[object, tuple[tuple, _IssueAggregation]] | None] = ContextVar(
    "draftwright_build_lint", default=None
)
_BUILD_LINT_DRAWING_TYPE: ContextVar[type | None] = ContextVar(
    "draftwright_build_lint_drawing_type", default=None
)


@contextlib.contextmanager
def reuse_finished_build_lint(drawing_type: type):
    """Share physical critique across one build and its nested layout trials."""
    if _BUILD_LINT.get() is not None:
        yield
        return
    token = _BUILD_LINT.set({})
    type_token = _BUILD_LINT_DRAWING_TYPE.set(drawing_type)
    try:
        yield
    finally:
        _BUILD_LINT_DRAWING_TYPE.reset(type_token)
        _BUILD_LINT.reset(token)


@contextlib.contextmanager
def suspend_finished_build_lint():
    """Keep attempt assembly and caller hooks outside the finished read scope."""
    if _BUILD_LINT.get() is None:
        yield
        return
    token = _BUILD_LINT.set(None)
    type_token = _BUILD_LINT_DRAWING_TYPE.set(None)
    try:
        yield
    finally:
        _BUILD_LINT_DRAWING_TYPE.reset(type_token)
        _BUILD_LINT.reset(token)


def _captured_lint(drawing):
    cache = _BUILD_LINT.get() if type(drawing) is _BUILD_LINT_DRAWING_TYPE.get() else None
    if cache is not None and drawing in cache:
        return cache[drawing]
    with _collect_issue_aggregation() as aggregation:
        issues = tuple(drawing.lint())
    result = (issues, aggregation)
    if cache is not None:
        cache[drawing] = result
    return result


def _build_lint_entry(drawing):
    cache = _BUILD_LINT.get()
    if cache is None:
        return None
    return next((entry for owner, entry in cache.items() if owner is drawing), None)


def discard_finished_build_lint(drawing):
    """Forget a scoped physical critique after a finished attempt changes."""
    cache = _BUILD_LINT.get()
    if cache is not None:
        for owner in tuple(cache):
            if owner is drawing:
                del cache[owner]
                break


def finished_build_lint_issues(drawing):
    """Read a finished attempt's physical issues without persisting a Drawing cache."""
    return _captured_lint(drawing)[0]


def lint_snapshot(drawing):
    """Return issues and summary from one lint without exposing a mutable cache scope."""
    # A nested assessment must not expose its outer snapshot to lint overrides that
    # call lint_summary() while the inner public lint method is still running.
    mask = _SCOPED_LINT.set(None)
    try:
        issues, aggregation = _captured_lint(drawing)
    finally:
        _SCOPED_LINT.reset(mask)
    token = _SCOPED_LINT.set((drawing, issues, aggregation))
    try:
        summary = drawing.lint_summary()
    finally:
        _SCOPED_LINT.reset(token)
    return issues, summary


@contextlib.contextmanager
def _reuse_report_requirements(
    owner: object, outcomes: Mapping[str, tuple[Any, ...]], dimension_plan: object
):
    token = _REPORT_REQUIREMENTS.set((owner, outcomes, dimension_plan))
    try:
        yield
    finally:
        _REPORT_REQUIREMENTS.reset(token)


class DiagnosticOperations:
    """One short-lived diagnostic port over explicit Drawing-owned state."""

    def __init__(
        self,
        drawing,
        *,
        analysis,
        model,
        build,
        registry,
        coverage,
        working_part,
        model_declared,
        view_edge_cache,
        ann_box_cache,
        cyl_cache,
        set_cyl_cache,
    ) -> None:
        self.drawing = drawing
        self.analysis = analysis
        self.part_model = model
        self.build = build
        self.registry = registry
        self.coverage = coverage
        self.working_part = working_part
        self.model_declared = model_declared
        self.view_edge_cache = view_edge_cache
        self.ann_box_cache = ann_box_cache
        self.cyl_cache = cyl_cache
        self.set_cyl_cache = set_cyl_cache
        self.views = drawing.views
        self.items = drawing.items
        self.scale = drawing.scale
        self.assembly = drawing.assembly
        self.page_w = drawing.page_w
        self.page_h = drawing.page_h
        self.drawable_bounds = drawing.drawable_bounds
        self.model = drawing.model
        self.recognition_evidence = drawing.recognition_evidence
        self.recognition_ownership = drawing.recognition_ownership
        self.lint = drawing.lint
        self.material_fields = drawing.material_fields
        self.view_bounds = drawing.view_bounds
        self.iter_annotations = drawing.iter_annotations
        self.annotations = drawing.annotations

    def report(self) -> dict[str, object]:
        """Return the versioned machine-readable recognition and drawing report.

        Schema version 3 projects accepted raw recognition occurrences, their exact run-local
        consumer dispositions, final IR owners, recognition-owned semantic requirement outcomes,
        profile-support requirements, and the existing structured lint summary.
        Report IDs are deterministic within this document only; they are not topology or durable
        feature identifiers. ``bounded-clear`` is not manufacturing readiness because recognition
        can miss geometry and material, process, finish, fit, and tolerance intent remains authored.

        A declared drawing uses schema version 8: its final IR is the authority, and the report
        preserves lint/quality observations plus page/view/annotation layout evidence while
        stating when detailed opt-in placement evidence is unavailable. It never reconstructs
        occurrences from declared values. A framed or bare
        drawing whose exact occurrence ownership is unavailable, or a raw drawing with an
        unclassified accepted occurrence, raises
        :class:`draftwright.ReportUnavailableError` rather than inventing correspondence or
        shrinking the denominator. Calling this method never changes rendered drawing content.
        """

        from draftwright.reporting import declared_drawing_report, drawing_report

        if self.model_declared:
            source = (
                getattr(self.analysis, "step_file", None) if self.analysis is not None else None
            )
            lint = self.drawing.lint_summary()
            report = declared_drawing_report(
                model=self.model(),
                lint=lint,
                source=source,
                registry=self.registry,
                drawing=self.drawing,
            )
            # Document sheets are authored views over one source-owned detected model.
            # Keep schema 8's declared layout evidence, but do not discard the exact
            # occurrence/requirement ledger that the document bound to this member.
            if (
                self.recognition_evidence() is not None
                and self.recognition_ownership() is not None
            ):
                snapshot = self.drawing.requirement_snapshot()
                source_report = drawing_report(
                    evidence=snapshot.evidence,
                    ownership=snapshot.ownership,
                    model=snapshot.model,
                    lint=lint,
                    source=snapshot.source,
                    registry=snapshot.registry,
                    omissions=snapshot.omissions,
                    dimension_plan=snapshot.dimension_plan,
                    part=snapshot.part,
                    requirement_outcomes=snapshot.outcomes,
                    detail_decisions=tuple(self.drawing.detail_decisions),
                )
                report["recognition"] = source_report["recognition"]
            return report

        snapshot = self.drawing.requirement_snapshot()
        with _reuse_report_requirements(self.drawing, snapshot.outcomes, snapshot.dimension_plan):
            lint = self.drawing.lint_summary()
        return drawing_report(
            evidence=snapshot.evidence,
            ownership=snapshot.ownership,
            model=snapshot.model,
            lint=lint,
            source=snapshot.source,
            registry=snapshot.registry,
            omissions=snapshot.omissions,
            dimension_plan=snapshot.dimension_plan,
            part=snapshot.part,
            requirement_outcomes=snapshot.outcomes,
            detail_decisions=tuple(self.drawing.detail_decisions),
        )

    def requirement_snapshot(self, *, include_lint=False):
        """Capture live source-owned outcomes for single-sheet and document review.

        This reuses the report's exact-authority validation and existing producers.
        It neither recognizes geometry nor derives requirements from the compiled plan.
        The returned references belong to this build and must not be persisted or
        combined with another recognition run. Serialize edits and snapshot reads.
        """
        from draftwright.reporting import RequirementSnapshot, validate_report_inputs

        analysis = self.analysis
        source = getattr(analysis, "step_file", None) if analysis is not None else None
        evidence, ownership, model = validate_report_inputs(
            self.recognition_evidence(), self.recognition_ownership(), self.model()
        )
        from draftwright.linting.requirements import recognized_requirement_outcomes
        from draftwright.model.compiled import compile_dimensions

        dimension_plan = compile_dimensions(model)
        omissions = tuple(self.build.omissions)
        outcomes = recognized_requirement_outcomes(
            evidence.result,
            tuple(model.features),
            self.registry,
            omissions,
            dimension_plan=dimension_plan,
            part=self.working_part,
            evidence=evidence,
            ownership=ownership,
            datum=next((datum for datum in model.datums if datum.id == "datum_xy"), None),
        )
        lint = None
        if include_lint:
            with _reuse_report_requirements(self.drawing, outcomes, dimension_plan):
                lint = self.drawing.lint_summary()
        return RequirementSnapshot(
            evidence,
            ownership,
            model,
            source,
            self.registry,
            omissions,
            dimension_plan,
            self.working_part,
            outcomes,
            lint,
        )

    def write_report(self, path: str | os.PathLike[str]) -> str:
        """Atomically write :meth:`report` as deterministic UTF-8 JSON.

        The destination is replaced only after the complete strict-JSON document has been
        flushed to a temporary file in the same directory. A report or filesystem failure leaves
        an existing destination untouched; temporary-file cleanup is best-effort when the
        filesystem itself refuses it. This method does not export or modify any visual drawing
        artefact.
        """

        from draftwright.reporting import write_json_document

        return write_json_document(self.drawing.report(), path)

    def _lint(self, *, physical: bool = True, aggregation=None):
        """Internal lint path with an optional summary-scoped pair ledger (#1147)."""
        page_bbox = self.drawable_bounds
        # The model compiler remains above linting's independent rank-2 boundary.
        model = self.part_model
        dimension_plan = None
        if model is not None:
            requests = (
                model.authored_dimensions
                if model.authored_dimensions is not None
                else model.requested_dimensions
            )
            if physical or any(request.display_decimals is not None for request in requests):
                from draftwright.model.compiled import compile_dimensions

                dimension_plan = compile_dimensions(model)
        display_decimals = (
            compiled_display_precisions(self.registry, dimension_plan)
            if dimension_plan is not None
            else None
        )
        ctx = LintContext(
            drawing=self.drawing,
            page_bbox=page_bbox,
            views=self.views,
            items=self.items,
            scale=self.scale,
            material_fields=self.material_fields,
            registry=self.registry,
            working_part=self.working_part,
            analysis=self.analysis,
            build=self.build,
            model=model,
            coverage=self.coverage,
            assembly=self.assembly,
            model_declared=self.model_declared,
            view_edge_cache=self.view_edge_cache,
            ann_box_cache=self.ann_box_cache,
            cyl_cache=self.cyl_cache,
        )
        try:
            return lint_finished_drawing(
                ctx,
                physical=physical,
                aggregation=aggregation,
                dimension_plan=dimension_plan,
                display_decimals=display_decimals,
            )
        finally:
            if ctx.cyl_cache is not self.cyl_cache:
                self.set_cyl_cache(ctx.cyl_cache)

    def layout_utilization(self) -> dict:
        """Conservative page-space utilization evidence for layout decisions.

        The evidence uses clipped view and annotation bounding boxes. It therefore
        overestimates sparse line-work by design, but it is deterministic, cross-family,
        and sufficient to expose a large unused sheet or empty quadrant without parsing
        an export (#1797).
        """
        from draftwright.drawing_evidence import layout_utilization

        page = _frame_margins(self.analysis).bounds(self.page_w, self.page_h)
        return layout_utilization(page, self.views, self.view_bounds, self.iter_annotations)

    def lint_summary(self) -> dict:
        """Aggregate :meth:`lint` into a JSON-friendly diagnostic summary.

        Gives a non-interactive caller (a script, or an LLM via the API) structured
        diagnostics and independently inspectable components without rendering the SVG:

        - ``passed`` — no error-severity issues;
        - ``score`` — legacy coarse 0–1 diagnostic heuristic (see ``_SCORE_*``);
        - ``diagnostic_score`` — the same value under its honest name;
        - ``quality`` — separable completeness, restraint, legibility and fidelity components. No
          composite drawing-quality score is manufactured (#1127). Legibility's existing
          severity/code counts are raw findings; its ``primary_*`` counts and scalar group
          producer-identified pair findings by annotation and failure mechanism (#1147);
        - ``review`` — concise explanations of those existing observations and their limits;
        - ``errors`` / ``warnings`` / ``infos`` — counts by severity;
        - ``by_code`` — per-check counts;
        - ``geometry_issues`` — count of standards/geometry-correctness issues
          as opposed to pure layout (see ``_GEOMETRY_AWARE_CODES``);
        - ``issues`` — the full list, each as a plain dict.
        - ``pmi`` — when source PMI exists, source-to-render stage counts derived from the
          extraction report, final IR, annotation registry, and structured placement drops.
        """
        # Keep dispatch through the documented public critique method: subclasses and callers
        # may extend ``lint``. The context is task-local, and only the base implementation
        # records pair evidence; custom issues remain independent (fail closed).
        scoped = _SCOPED_LINT.get()
        if scoped is not None and scoped[0] is self.drawing:
            issues, aggregation = scoped[1], scoped[2]
        elif (captured := _build_lint_entry(self.drawing)) is not None:
            issues, aggregation = captured
        else:
            issues, aggregation = _captured_lint(self.drawing)
        from draftwright.drawing_evidence import lint_summary as project_lint_summary

        candidate = _REPORT_REQUIREMENTS.get()
        report_requirements = (
            candidate if candidate is not None and candidate[0] is self.drawing else None
        )
        return project_lint_summary(
            issues,
            aggregation,
            analysis=self.analysis,
            model=self.part_model,
            registry=self.registry,
            recognition=self.build.recognition,
            evidence=self.build.recognition_evidence,
            ownership=self.build.recognition_ownership,
            omissions=self.build.omissions,
            working_part=self.working_part,
            items=self.drawing.items,
            model_declared=self.model_declared,
            layout_utilization=self.drawing.layout_utilization,
            report_requirements=report_requirements,
            geometry_aware_codes=_GEOMETRY_AWARE_CODES,
            score_error_penalty=_SCORE_ERROR_PENALTY,
            score_warning_penalty=_SCORE_WARNING_PENALTY,
        )

    def _lint_and_log(self) -> None:
        issues = self.lint()
        if issues:
            _log.warning("Lint issues:")
            for iss in issues:
                _log.warning("  [%s] %s: %s", iss.severity, iss.code, iss.message)
        else:
            _log.info("Lint: OK")
