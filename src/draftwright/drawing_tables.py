"""Drawing note, table and balloon placement over explicitly supplied state.

Drawing retains the public verbs and private state; this owner uses the same
shared page-fit and balloon solvers through a snapshot of those inputs.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, NamedTuple

from build123d import Align, Location
from build123d_drafting.helpers import DEFAULT_FONT_PATH

from draftwright._core import (
    SheetMargins,
    _analysis_margins,
    _build_table,
    _fmt,
    _font_safe_text,
    _tag_sequence,
    _text_line_spacing_em,
    _tol_suffix,
)
from draftwright.annotations._common import (
    PlacementContext,
    _register_hole_table_coverage,
    late_furniture_obstacles,
)
from draftwright.annotations.balloons import render_balloons
from draftwright.layout import FitBoxTrace
from draftwright.linting import LintIssue


@dataclass(frozen=True)
class DrawingTableState:
    drawing: object
    draft: object
    registry: object
    analysis: object
    model: object
    coords: object
    coverage: object
    items: list
    page_w: float
    page_h: float
    document_member: bool
    document_source_annotation_ids: frozenset[int]
    add: Callable
    add_table: Callable
    add_balloons: Callable
    hole_spec_groups: Callable
    fit_auxiliary_box: Callable


class _HoleInstance(NamedTuple):
    """One hole occurrence for the table / balloon renderers — the IR fields those
    passes read (``location`` + ``diameter`` for a balloon, ``through``/``depth`` for a
    table row), so no ``HoleRecord`` is needed (ADR 1 (was 0008); #584 WP1). Duck-compatible
    with the recogniser record the orchestrator's balloon path still passes."""

    location: tuple
    diameter: float
    through: bool
    depth: float | None


def _ir_hole_groups(model, target_axis: str) -> list[tuple]:
    """``(owner, spec, [member positions], count)`` groups on *target_axis*.

    One group per IR hole/pattern feature — the spec-grouping + pattern recognition
    detection already did, so no ``HoleRecord``/``HoleSpec`` re-grouping is needed
    (ADR 1 (was 0008 Am6); #584 WP1). ``spec`` is the representative ``HoleFeature`` (carries
    diameter / through / depth); ``positions`` are its member centres (drive balloon
    placement); ``count`` is the feature's own count (the table QTY / FeatureInfo.count).
    They coincide on the detected path (``members`` is fully populated); ``count`` stays
    faithful for a declared feature whose ``members`` are unspecified (ADR 4 (was 0011))."""
    groups: list[tuple] = []
    for f in model.features:
        if f.kind == "hole" and f.frame.axis == target_axis:
            groups.append((f, f, list(f.members) or [f.frame.origin], f.count))
        elif f.kind == "pattern" and f.member.frame.axis == target_axis:
            groups.append((f, f.member, list(f.members) or [f.member.frame.origin], f.count))
    return groups


def note(state, text, at, *, view=None, rotation=0.0, name=None, align=None):
    """Add a free-form text **note** at page position *at* — ``(x, y)`` in mm from the sheet
    origin, the space :meth:`at` / :meth:`view_bounds` return (#817).

    A note is user-positioned free text ("SEE NOTE 1", a general-tolerance line): it carries
    no feature and is not part of the placement solve, so — unlike :meth:`callout` /
    :meth:`dimension`, which the solve places — you give the position. Pass *view* to fold it
    into that view's block for the cross-view repack; ``rotation`` (degrees) and ``align``
    (a build123d ``Align`` pair, default centred on *at*) are forwarded to the note. Returns
    the annotation name. This is the public door for free text — the raw ``Note`` object +
    low-level placement primitive are internal."""
    from build123d_drafting import Note

    n = Note(
        _font_safe_text(text),
        at,
        state.draft,
        rotation=rotation,
        align=align if align is not None else (Align.CENTER, Align.CENTER),
    )
    # Keep the exact string shown by the drafting font for the PDF semantic
    # overlay (notably its established ⌀ -> ø compatibility substitution).
    n.pdf_text = _font_safe_text(text)
    n.pdf_text_rotation = float(rotation)
    n.pdf_text_line_spacing = _text_line_spacing_em(
        state.draft.font_size,
        getattr(state.draft, "font_path", DEFAULT_FONT_PATH),
        getattr(state.draft, "font", "Arial"),
    )
    if name is None:
        i = 0
        while (name := f"note{i}") in state.registry:
            i += 1
    state.add(n, name, view=view)
    return name


def add_table(
    state,
    rows,
    *,
    prefer="tr",
    name="table",
    block_cols=None,
    _source_id: str | None = None,
    _source_ids: tuple[str, ...] = (),
    _features: tuple[object, ...] = (),
    _drop_code: str = "table_dropped",
    _drop_severity: Literal["error", "warning", "info"] = "warning",
    _cells=(),
    _left_align_cols=(),
):
    """Add a generic data table in the preferred available sheet region (#93/#1145).

    *rows* is a list of equal-length string tuples (``rows[0]`` is the
    header). The measured page-space footprint is positioned by :func:`fit_box`
    clear of the views, title block, and existing annotations by the drafting
    preset's external text clearance; *prefer* ranks candidates by their
    distance from that page corner but does not restrict placement to the
    corner. Returns the table annotation, or ``None`` if it has no rows or
    will not fit. A failed solve records ``table_dropped`` with the footprint,
    attempted candidate regions, and their named blockers/clearance bands.
    Gear-data, BOM, and revision tables all go through here;
    :meth:`add_hole_table` is the hole-specific convenience built on it.
    """
    if not rows:
        return None
    if _cells and name in state.registry:
        raise ValueError(f"measured schedule name {name!r} already belongs to an annotation")
    table = _build_table(
        rows, state.draft, block_cols=block_cols, left_align_cols=_left_align_cols
    )
    # Keep the rows the table draws, so its content is readable back off the annotation
    # (#1217). A table renders as compound geometry with no `label`, so without this a
    # hole table's measurement claims can be neither confirmed nor refuted — and the
    # claims it carries are exactly the ones coverage relies on when the engine withdraws
    # the individual callouts. Mirrors `gear_requirement_rows`.
    table.table_rows = tuple(tuple(str(cell) for cell in row) for row in rows)
    if _cells:
        table.measurement_schedule = _cells[0].schedule
    table.table_block_cols = block_cols
    w, h = table.table_size
    a = state.analysis
    margins = _analysis_margins(a) if a is not None else SheetMargins()
    pw = a.PAGE_W if a is not None else state.page_w
    ph = a.PAGE_H if a is not None else state.page_h
    region = margins.bounds(pw, ph)
    # The shared post-fit occupancy policy — views, decomposed annotation ink, minus
    # the page-spanning riders, plus the title-block hull. Extracted so the NTS
    # caption places against the same set (#1197); every hand-rolled copy of it has
    # dropped one of the four parts.
    obstacles = late_furniture_obstacles(state.drawing, named=True)

    trace = FitBoxTrace()
    pos = state.fit_auxiliary_box(
        (w, h),
        region,
        obstacles,
        prefer,
        clearance=state.draft.pad_around_text,
        trace=trace,
    )
    if pos is None:
        measured = f"width={w:.1f} mm, height={h:.1f} mm"
        detail = trace.violation
        if detail is None and trace.rejected:
            shown = trace.rejected[:4]
            rejected = []
            for attempt in shown:
                x0, y0, x1, y1 = attempt.region
                rejected.append(
                    f"[{x0:.1f},{y0:.1f}–{x1:.1f},{y1:.1f}] blocked within "
                    f"{trace.clearance:.1f} mm clearance by {', '.join(attempt.blockers)}"
                )
            remaining = trace.rejected_candidates - len(shown)
            suffix = f"; +{remaining} more" if remaining else ""
            detail = (
                f"attempted {trace.attempted_candidates} candidate regions; rejected: "
                f"{'; '.join(rejected)}{suffix}"
            )
        if detail is None:
            detail = "solver returned no placement trace"
        state.registry.record_issue(
            LintIssue(
                severity=_drop_severity,
                code=_drop_code,
                message=(
                    f"table {name!r} did not fit the sheet; measured page-space footprint "
                    f"{measured}; {detail}"
                ),
                source_ids=tuple(
                    dict.fromkeys(((_source_id,) if _source_id is not None else ()) + _source_ids)
                ),
                measurement_ids=tuple(cell.measurement for cell in _cells),
            )
        )
        return None
    placed = table.locate(Location((pos[0], pos[1], 0)))
    placed.source_features = _features
    if not _cells:
        return state.add(placed, name)
    snapshot = state.registry.snapshot()
    items = list(state.items)
    issues = state.registry.issues
    try:
        return state.add(placed, name, cells=_cells)
    except BaseException:
        state.items[:] = items
        state.registry.restore(snapshot)
        state.registry.restore_issues(issues)
        raise


def _hole_spec_groups(state, view):
    """Ordered ``(tag, [holes], count)`` spec-groups of *view*'s holes (tags A, B,
    …). The shared basis for the hole table's rows and its balloons, so the
    TAG column and the balloon glyphs line up.

    Sourced from the IR (``model.features``), so each group is one hole/pattern
    feature — a pattern and same-spec loose holes are distinct groups (ADR 1 (was 0008);
    #584 WP1). Each ``holes`` element is a :class:`_HoleInstance` (one per member
    position, driving a balloon); ``count`` is the feature's declared/detected count
    for the table QTY — equal to ``len(holes)`` on the detected path."""
    model = state.model
    target = {"plan": "z", "front": "y", "side": "x"}.get(view)
    if model is None or target is None or view not in state.coords:
        return []

    glist = [
        (
            owner,
            [_HoleInstance(pos, spec.diameter, spec.through, spec.depth) for pos in positions],
            count,
        )
        for owner, spec, positions, count in _ir_hole_groups(model, target)
    ]
    return [
        (tag, owner, holes, count)
        for tag, (owner, holes, count) in zip(_tag_sequence(len(glist)), glist, strict=True)
    ]


def add_balloons(state, view, specs):
    """Place a leadered balloon for each ``(tag, j, hole)`` in *specs*,
    fitted into the halo the layout reserved around the view (#111).

    Public verb over the :mod:`draftwright.annotations.balloons` render pass
    (#699: the pass lives in the render layer; this owner method threads the
    build state in). Each hole is assigned to a reserved band — left, right,
    top or bottom — by a global max-cardinality/min-cost assignment (#516),
    each band is spread with the 1D strip solver, and a :class:`Leader` runs
    from the hole rim to each glyph.
    """
    if view not in state.coords or state.analysis is None:
        return
    ctx = PlacementContext(
        registry=state.registry,
        coverage=state.coverage,
        items=state.items,
        part_model=state.model,
        document_member=state.document_member,
        document_source_annotation_ids=state.document_source_annotation_ids,
    )
    render_balloons(state.drawing, state.analysis, view, specs, ctx, avoid_annotation_labels=True)


def add_hole_table(state, view="plan", *, prefer="tr", name=None, balloons=True):
    """Add a hole table for *view*'s holes, placed in a free corner (#93).

    One row per hole spec-group — ``TAG | ⌀ | DEPTH | QTY`` with tags
    ``A, B, …`` — placed via :meth:`add_table`. With *balloons* (the
    default) a circled tag is added at each hole keyed to its row. The table
    carries the same semantic measurement and structured requirement provenance as
    automatic table escalation, so physical hole outcomes count only the facts the
    table visibly states. Returns the table, or ``None`` when *view* has no holes or it
    will not fit.
    """
    from draftwright.model.callout import resolved_through_indicator

    groups = state.hole_spec_groups(view)
    if not groups:
        return None
    # The compiler's text for every cell this table prints, keyed the way the coverage
    # registration below already keys it. A table row IS a dimension — `⌀ 8 ±0.05` in a
    # cell states exactly what `⌀8 ±0.05` states beside a leader — but this verb formatted
    # its own numbers off the recognised geometry, so an authored tolerance was approved,
    # claimed by the table's provenance, and never printed. Read from
    # `_part_model` rather than `model()`: an attribute, so a declared build is not made
    # to recognise anything by adding a table (ADR 3 (was 0017)).
    approved: dict = {}
    omitted: set = set()
    if state.model is not None:
        from draftwright.model.compiled import compile_dimensions as _compile_table
        from draftwright.model.compiled import resolve_feature as _resolve_table

        _plan = _compile_table(state.model)
        approved = {
            (_resolve_table(group.ref), dim.parameter_id): dim
            for group in _plan.of_kind("hole")
            for dim in group.dims
        }
        # What the compiler REFUSED, separately from what it merely has no entry for.
        # `Omission.authored` means the author left a measurement out. Printing it here
        # would violate the compiled plan's suppression decision.
        omitted = {
            (omission.feature, omission.parameter_id)
            for omission in _plan.diagnostics
            if omission.authored
        }

    def _cell(owner, parameter, fallback):
        """The plan's text for *owner*'s *parameter*, else *fallback*.

        The fallback covers the one case it is for: `_hole_spec_groups` is geometry-derived
        and can group holes the compiler has NO entry for at all, and an empty cell there
        would be worse than the measured value. It does not cover a measurement the author
        omitted — that is a decision, and it is honoured by printing nothing, which is what
        the escalated table has always done for the same case.
        """
        if (owner, parameter) in omitted:
            return ""
        dim = approved.get((owner, parameter))
        if dim is None:
            return fallback
        return f"{dim.value_text}{_tol_suffix(dim.tolerance, state.draft)}"

    rows = [("TAG", "⌀", "DEPTH", "QTY")]
    diams = []
    for tag, owner, holes, count in groups:
        h = holes[0]
        dia = _cell(owner, "bore.diameter", _fmt(h.diameter))
        # An empty diameter empties the whole cell and takes `THRU` with it, exactly as the
        # escalated table does (`orchestrator._table_row`): a bare `ø` with no number, or a
        # `THRU` qualifying a diameter that is not printed, states less than nothing.
        depth = (
            (resolved_through_indicator(owner) if dia else "")
            if h.through
            else (_cell(owner, "bore.depth", _fmt(h.depth)) if h.depth else "")
        )
        rows.append((tag, f"ø{dia}" if dia else "", depth, str(count)))
        # Legacy physical-diameter lint counts one structured entry per bore.
        # Repeat the value exactly as many times as the visible QTY asserts, just as
        # automatic escalation does, while the semantic ledger below retains the
        # feature-scoped grouping identity.
        diams.extend([h.diameter] * count)
    table_name = name or f"hole_table_{view}"
    table = state.add_table(rows, prefer=prefer, name=table_name)
    if table is None:
        return None
    # The table documents these diameters — let lint see that (#93).
    table.covers_diameters = tuple(diams)
    from draftwright.model.compiled import DimensionId

    # Calling the public verb is an explicit edit: the table itself authors every
    # measurement it visibly prints, even when the original dimension set omitted a
    # generated callout. Construct the same stable identities the compiler uses so
    # holes and patterns join the physical outcome ledger through one seam.
    measurements = tuple(
        DimensionId(owner, parameter)
        for _tag, owner, holes, _count in groups
        for parameter in (
            ("bore.diameter",)
            if holes[0].through or holes[0].depth is None
            else ("bore.diameter", "bore.depth")
        )
    )
    requirements = tuple(
        (owner, "bore.through", 1) for _tag, owner, holes, _count in groups if holes[0].through
    ) + tuple(
        (owner, "grouping.count", count) for _tag, owner, _holes, count in groups if count > 1
    )
    _register_hole_table_coverage(
        table,
        state.registry,
        table_name,
        measurements=measurements,
        requirements=requirements,
    )
    if balloons:
        state.add_balloons(
            view,
            [(tag, j, h) for tag, _owner, holes, _count in groups for j, h in enumerate(holes)],
        )
    return table
