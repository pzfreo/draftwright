"""The Drawing result object + table builder (#138 / ADR 0005, P6).

`Drawing` is the composable build result: it owns the render list and view
map and delegates identity to the registry, coverage to lint, and exposes
`.lint()/.add()/.place_dim()/.repair()/.export*()`. Sits below the builder
(which constructs it) — imports only the stage modules + `_core`, never
`builder`/`make_drawing`.
"""

from __future__ import annotations

import contextlib
import math
from dataclasses import dataclass

from build123d import (
    Align,
    Circle,
    Color,
    Compound,
    Edge,
    ExportDXF,
    ExportSVG,
    LineType,
    Location,
    Mode,
    Text,
    Vector,
)
from build123d_drafting.helpers import (
    Dimension,
    Leader,
    SafeDimension,
    ViewCoordinates,
    annotate,
    view_axes,
)

from draftwright._core import (
    _MARGIN,
    _STRIP_GAP,
    _STRIP_SPACING,
    Analysis,
    _axis_letter,
    _dim,
    _fmt,
    _log,
    _tag_sequence,
    _text_width,
)
from draftwright.annotations._common import carve_free_position
from draftwright.export import (
    _export_shape,
    _render_pdf,
    add_svg_hyperlink,
    add_svg_metadata,
    fix_svg_page_size,
    sanitize_svg_arcs,
    set_dxf_metadata,
)
from draftwright.fonts import PLEX_MONO
from draftwright.intents import Intent
from draftwright.layout import (
    _greedy_strip_1d,
    _solve_strip_1d,
    fit_box,
)
from draftwright.linting import (
    CoverageState,
    LintIssue,
    _suggest_fix,
    lint_axial_coverage,
    lint_declaration_reconciliation,
    lint_drawing,
    lint_feature_coverage,
    lint_location_coverage,
)
from draftwright.projection import (
    _exactify_silhouettes,
    _raw_view_projector,
)
from draftwright.recognition import (
    HoleSpec,
    analyse_cylinders,
)
from draftwright.registry import AnnotationRegistry
from draftwright.repair import repair_drawing

_TB_W = 150.0
# Minimum acceptable projected view dimension (page-mm).  Below this, annotation
# geometry (leader wires, centre marks, bore callout elbows) can degenerate and
# cause OCCT Standard_DomainError / SIGABRT (#129).


# ---------------------------------------------------------------------------
# SVG post-processing
# ---------------------------------------------------------------------------


# Equidistance tolerance (page-mm) for accepting a sampled silhouette spline as
# a circle about a known projected axis.  Loose enough to swallow HLR's spline
# approximation error, tight enough not to round a genuinely off-axis curve.


def _build_table(rows, draft, block_cols=None):
    """Build a generic data-table annotation at the origin (bottom-left ``(0, 0)``).

    *rows* is a list of equal-length string tuples; ``rows[0]`` is the header,
    drawn at the top. Returns a :class:`Compound` of grid rules + cell text,
    carrying ``.table_size = (w, h)`` so it can be positioned via
    :func:`fit_box`. Column widths are sized to their content via real glyph
    metrics (:func:`_text_width`). Generic — the hole table, and gear/BOM/
    revision tables, all build through here.

    When the rows are a wrapped chart of *block_cols*-wide side-by-side blocks
    (see :func:`_wrap_rows`), each block is drawn as its own bordered grid with
    a whitespace gap between them, so the blocks read as separate tables rather
    than one run-on grid.
    """
    fs = draft.font_size
    pad = draft.pad_around_text
    row_h = fs + 2 * pad
    ncol = len(rows[0])
    # Only treat as multi-block when block_cols evenly divides the row width.
    bc = block_cols if (block_cols and ncol % block_cols == 0 and block_cols < ncol) else ncol
    block_gap = 3 * pad  # whitespace between side-by-side blocks
    col_w = [
        max(max(_text_width(str(r[c]), fs) for r in rows) + 2 * pad, fs * 2.5) for c in range(ncol)
    ]
    # Per-column left/right edges, inserting block_gap before each new block.
    lefts, rights, cursor = [], [], 0.0
    for c in range(ncol):
        if c > 0 and c % bc == 0:
            cursor += block_gap
        lefts.append(cursor)
        cursor += col_w[c]
        rights.append(cursor)
    total_w = cursor
    total_h = row_h * len(rows)
    ys = [i * row_h for i in range(len(rows) + 1)]
    children = []
    for b in range(ncol // bc):  # one bordered grid per block
        cols = range(b * bc, b * bc + bc)
        bl, br = lefts[b * bc], rights[b * bc + bc - 1]
        for x in [lefts[c] for c in cols] + [br]:  # column rules + block edges
            children.append(Edge.make_line(Vector(x, 0, 0), Vector(x, total_h, 0)))
        for y in ys:  # horizontal rules stop at the block edge, not the gap
            children.append(Edge.make_line(Vector(bl, y, 0), Vector(br, y, 0)))
    for ri, row in enumerate(rows):  # rows[0] (header) sits at the top
        cy = total_h - (ri + 0.5) * row_h
        for ci, cell in enumerate(row):
            if not str(cell):
                continue
            cx = (lefts[ci] + rights[ci]) / 2
            text = Text(
                txt=str(cell),
                font_size=fs,
                font_path=PLEX_MONO,
                align=(Align.CENTER, Align.CENTER),
                mode=Mode.PRIVATE,
            ).locate(Location((cx, cy, 0)))
            children.extend(text.faces())
    table = Compound(children=children)
    table.table_size = (total_w, total_h)
    return table


# Codes that check standards/geometry correctness rather than pure page
# layout. Grouped so a caller (and the #30 repair loop) can tell a wrong
# drawing from a merely tight one.
_GEOMETRY_AWARE_CODES = frozenset(
    {
        "feature_not_dimensioned",
        "feature_count_mismatch",
        "feature_not_located",
        "feature_no_centermark",
        "axial_length_missing",
        "missing_principal_dimension",
        "label_vs_measured",
        "dim_inside_part",
        "callout_dropped",
        "location_ref_dropped",
        "step_dim_dropped",
        "placement_unsatisfiable",
        "pmi_dropped",
    }
)

# Coarse 0–1 quality heuristic: a clean sheet scores 1.0; each issue subtracts
# a flat per-severity penalty (clamped at 0). A convenience signal only — the
# severity/code counts in the summary are the authoritative output.
_SCORE_ERROR_PENALTY = 0.2
_SCORE_WARNING_PENALTY = 0.05


@dataclass
class FeatureInfo:
    """A detected geometric feature, expressed in page coordinates.

    Returned by :meth:`Drawing.features`.  ``page_pos`` is in the coordinate
    system of the view passed to that call.
    """

    type: str
    page_pos: tuple
    diameter: float
    through: bool
    depth: float | None
    count: int


class Drawing:
    """A composable technical drawing — the editable form of :func:`make_drawing`.

    A ``Drawing`` holds the projected views, the annotation list, and per-view
    coordinate helpers. :func:`build_drawing` returns one pre-populated with the
    standard 4-view layout and automatic dimensions; you then add or remove
    annotations, add section/auxiliary views, and finally :meth:`export`.

    Attributes:
        scale: drawing scale factor (e.g. ``2.0`` for 2:1).
        page_w, page_h: sheet size in mm.
        tb_w: title-block width in mm.
        draft: the shared ``Draft`` preset used by the automatic annotations.
        look_at: scaled centroid ``(x, y, z)`` — the default ``look_at`` and a
            building block for custom view cameras (see :meth:`add_view`).
        dist: orthographic camera distance in scaled space.
        centroid: unscaled centroid ``(x, y, z)``.
        views: ``{name: (visible_compound, hidden_compound_or_None)}``.
        items: ordered list of annotation objects (mutable).
        part: the source solid, when known — enables the feature-coverage lint.
        assembly: feature-coverage severity control — ``None`` auto-detects a
            multi-solid part as an assembly (per-part bores at ``info``),
            ``True``/``False`` forces it (#69).

    The constructor also accepts ``cyls``, a precomputed
    ``analyse_cylinders(part)`` result (cached privately; computed lazily on
    first :meth:`lint` otherwise).
    """

    def __init__(
        self,
        *,
        scale,
        page_w,
        page_h,
        tb_w,
        draft,
        look_at,
        dist,
        centroid,
        out,
        part=None,
        cyls=None,
        assembly=None,
    ):
        self.scale = scale
        self.part = part
        self._cyl_cache = cyls
        # None → the coverage lint auto-detects a multi-solid part as an
        # assembly; True/False forces assembly/strict severity (#69).
        self.assembly = assembly
        self.page_w = page_w
        self.page_h = page_h
        self.tb_w = tb_w
        self.draft = draft
        self.look_at = look_at
        self.dist = dist
        self.centroid = centroid
        self.out = out
        self.views: dict = {}
        self.items: list = []
        self._coords: dict = {}
        # Annotation identity, ownership, pins, and build issues live in the
        # registry (#138 / ADR 0005, Step 2). `_named` / `_anno_view` / `_pinned`
        # / `_build_issues` remain reachable as properties (below) so tests and
        # helpers that read through them keep working during the migration.
        self._registry = AnnotationRegistry()
        # Lint-side coverage signal (pattern callouts, patterned holes, dropped
        # callout diameters) lives in its own owner (#138 / ADR 0005, Step 3).
        # _pattern_callouts / _patterned_holes / _dropped_callout_diams remain
        # reachable as properties below.
        self._coverage = CoverageState()
        # One per-drawing cache for lint_drawing's per-view edge bboxes, keyed on
        # id(view shape). repair() / lint_summary() lint the SAME projected view
        # objects (self.views) repeatedly, so persisting it recomputes each
        # view's edges once instead of every lint (helpers #143/#164).
        self._view_edge_cache: dict = {}
        self.svg_path: str | None = None
        self.dxf_path: str | None = None
        self._analysis: Analysis | None = None
        # The detected ADR-0008 PartModel this drawing was built from (attached by
        # the annotation orchestrator). Read surface for semantic edits (#397).
        self._part_model: object | None = None
        # True when the caller SUPPLIED the model (build_drawing(model=…), ADR 0011) rather
        # than it being detected — gates the model-driven hole/pattern render membership so a
        # declared hole draws even where detection missed it, no-op for the detected path (#448).
        self._model_declared: bool = False
        # Lazy model-location → IR hole/pattern feature index (#408), so a balloon —
        # which holds a recognition hole, not the IR feature — can attribute itself.
        self._hole_feature_index: dict | None = None
        # Deferred placement intents (#426 Phase 1). When _defer_intents is True the add
        # verbs record an Intent instead of placing; finalize() drains them (Phase 1
        # replays through the live helpers). Default off → the live path is unchanged.
        self._intents: list[Intent] = []
        self._defer_intents: bool = False

    # -- annotation registry (compat accessors, ADR 0005 §4) ------------------
    # The registry owns these four; they are exposed as their live containers so
    # code that reads or mutates ``dwg._named`` / ``_anno_view`` / ``_pinned`` /
    # ``_build_issues`` keeps working until those call sites are redirected.
    @property
    def _named(self) -> dict:
        return self._registry._named

    @_named.setter
    def _named(self, value) -> None:
        self._registry._named = value

    @property
    def _anno_view(self) -> dict:
        return self._registry._anno_view

    @_anno_view.setter
    def _anno_view(self, value) -> None:
        self._registry._anno_view = value

    @property
    def _pinned(self) -> set:
        return self._registry._pinned

    @_pinned.setter
    def _pinned(self, value) -> None:
        self._registry._pinned = value

    @property
    def _build_issues(self) -> list:
        return self._registry._build_issues

    @_build_issues.setter
    def _build_issues(self, value) -> None:
        self._registry._build_issues = value

    # -- coverage state (compat accessors, ADR 0005 §4) -----------------------
    # The CoverageState owner holds these three; exposed as their live containers
    # so code reading dwg._pattern_callouts / _patterned_holes /
    # _dropped_callout_diams keeps working until those sites are redirected.
    @property
    def _pattern_callouts(self) -> set:
        return self._coverage._pattern_callouts

    @_pattern_callouts.setter
    def _pattern_callouts(self, value) -> None:
        self._coverage._pattern_callouts = value

    @property
    def _patterned_holes(self) -> set:
        return self._coverage._patterned_holes

    @_patterned_holes.setter
    def _patterned_holes(self, value) -> None:
        self._coverage._patterned_holes = value

    @property
    def _dropped_callout_diams(self) -> list:
        return self._coverage._dropped_callout_diams

    @_dropped_callout_diams.setter
    def _dropped_callout_diams(self, value) -> None:
        self._coverage._dropped_callout_diams = value

    # -- coverage state operations (used by the annotation passes) ------------
    def _cover_pattern(self, callout_name, holes):
        """Record that placed *callout_name* documents *holes* (#92)."""
        self._coverage.cover_pattern(callout_name, holes)

    def _is_hole_patterned(self, hole) -> bool:
        """Is *hole* already documented by a placed pattern callout?"""
        return self._coverage.is_hole_patterned(hole)

    def _cover_scattered_hole_doc(self, name) -> None:
        """Record that placed *name* is a scattered hole callout / location dim (#351 PR-4c)."""
        self._coverage.cover_scattered_hole_doc(name)

    def _is_scattered_hole_doc(self, name) -> bool:
        """Is *name* a placed scattered hole callout / location dim?"""
        return self._coverage.is_scattered_hole_doc(name)

    def _reset_dropped_callout_diams(self):
        """Clear dropped-diameter tracking (top of :func:`_auto_annotate`)."""
        self._coverage.reset_dropped()

    def _drop_callout_diam(self, diam):
        """Record a diameter dropped by the per-view callout cap."""
        self._coverage.drop_diam(diam)

    # -- views ----------------------------------------------------------------
    def add_view(self, name, shape, camera, up, position, *, look_at=None, scaled=False):
        """Project ``shape`` from ``camera`` and place it at ``position``.

        Args:
            name: view name (key in :attr:`views`); also used for coordinate lookups.
            shape: a build123d ``Shape`` to project. Given in world (unscaled)
                coordinates and scaled internally unless ``scaled=True``.
            camera, up, look_at: viewport parameters in **scaled** space (the same
                convention the standard views use). ``look_at`` defaults to
                :attr:`look_at` (the scaled centroid). Compose custom cameras from
                :attr:`look_at` and :attr:`dist`.
            position: ``(x, y)`` page position for the view centre, in mm.
            scaled: set ``True`` if ``shape`` is already scaled by :attr:`scale`.

        Returns:
            The :class:`ViewCoordinates` for this view (also via :meth:`coords`),
            for mapping world points to page coordinates.
        """
        la = self.look_at if look_at is None else look_at
        shape_s = shape if scaled else shape.scale(self.scale)
        vis, hid = shape_s.project_to_viewport(camera, up, la)
        vl, hl = list(vis), list(hid)
        if not vl and not hl:
            raise ValueError(
                f"project_to_viewport returned empty geometry for view {name!r} "
                f"(camera {camera}) — check the camera position and look_at."
            )
        axes = view_axes(camera, up, la)
        # Recover exact circles for revolution silhouettes that HLR projected as
        # approximating splines (#67) — a no-op when no revolution axis is
        # parallel to the view direction (e.g. iso/section views).
        if vl:
            vd = Vector(la[0] - camera[0], la[1] - camera[1], la[2] - camera[2])
            vd = vd.normalized()
            proj = _raw_view_projector(axes, la)
            vl, n_circ = _exactify_silhouettes(vl, shape_s.faces(), (vd.X, vd.Y, vd.Z), proj)
            if n_circ:
                _log.info("  %s: %d silhouette spline(s) refit to circles", name, n_circ)
        loc = Location((position[0], position[1], 0))
        placed = Compound(children=vl).locate(loc)
        placed_hid = Compound(children=hl).locate(loc) if hl else None
        self.views[name] = (placed, placed_hid)
        cx, cy, cz = la[0] / self.scale, la[1] / self.scale, la[2] / self.scale
        self._coords[name] = ViewCoordinates(
            axes, position[0], position[1], cx, cy, cz, self.scale
        )
        _log.info("  %s: %d visible / %d hidden", name, len(vl), len(hl))
        return self._coords[name]

    def coords(self, view):
        """Return the :class:`ViewCoordinates` for a named view."""
        return self._coords[view]

    def at(self, view, x, y, z):
        """Map a world point to a page point ``(px, py, 0)`` in ``view``."""
        px, py = self._coords[view].pp(x, y, z)
        return (px, py, 0.0)

    def view_bounds(self, view):
        """Return ``(x_min, y_min, x_max, y_max)`` of the projected geometry in
        *view*, or ``None`` if the view is unknown (#28).

        The box is the tight bounding box of the placed silhouette — visible
        plus hidden lines — in page coordinates (mm from the sheet origin), the
        same space :meth:`at` returns. Use it to place free-form notes, leader
        elbows and the like just outside a view without guessing offsets::

            x0, y0, x1, y1 = dwg.view_bounds("front")
            dwg.add(Note("SEE NOTE 1", (x1 + 5, (y0 + y1) / 2), dwg.draft))
        """
        placed = self.views.get(view)
        if placed is None:
            return None
        vis, hid = placed
        bb = vis.bounding_box()
        x0, y0, x1, y1 = bb.min.X, bb.min.Y, bb.max.X, bb.max.Y
        if hid:
            hb = hid.bounding_box()
            x0, y0 = min(x0, hb.min.X), min(y0, hb.min.Y)
            x1, y1 = max(x1, hb.max.X), max(y1, hb.max.Y)
        return (x0, y0, x1, y1)

    def features(self, view="front"):
        """Return detected geometric features in page coordinates for *view*.

        Holes are grouped by machining spec (diameter + depth + cbore) and
        returned as :class:`FeatureInfo` objects with ``count`` set to the
        number of identical holes at that spec.  Each group's ``page_pos``
        is the page position of the first hole in the group.

        The view determines which holes appear as circles (and are therefore
        annotatable from that view):

        - ``"plan"``  → Z-axis holes
        - ``"front"`` → Y-axis holes
        - ``"side"``  → X-axis holes

        Returns an empty list when no analysis is available or the view name
        is unrecognised.
        """
        a = self._analysis
        if a is None:
            return []

        _axis_for_view = {"plan": "z", "front": "y", "side": "x"}
        target_axis = _axis_for_view.get(view)
        if target_axis is None:
            return []

        if view not in self._coords:
            return []

        def _to_page(h):
            return self._coords[view].pp(*h.location)

        groups: dict = {}
        for h in a.holes:
            if _axis_letter(h) != target_axis:
                continue
            groups.setdefault(HoleSpec.from_hole(h), []).append(h)

        result = []
        for group in groups.values():
            rep = group[0]
            result.append(
                FeatureInfo(
                    type="hole",
                    page_pos=_to_page(rep),
                    diameter=rep.diameter,
                    through=rep.bottom == "through",
                    depth=None if rep.bottom == "through" else rep.depth,
                    count=len(group),
                )
            )
        return result

    def model(self):
        """The detected **PartModel** this drawing was built from (ADR 0008 IR) — the
        read surface for semantic edits (#397, ADR 0001 Amendment 1).

        Both input scenarios converge here: a STEP file and a build123d solid both
        normalise to a solid, are detected once, and produce the *same* feature model
        (``.features`` — holes/slots/steps/patterns, ``.datums``, ``.orientation``,
        ``.bbox``). This is the provenance-agnostic "what is in this drawing and why"
        — richer than :meth:`features` (grouped holes, per view) and the future target
        for feature-referenced edits (#398).

        **Read-only** — a view of what was built; mutating it does not change the
        drawing. **Experimental**: exposes the raw IR dataclasses, which may still
        evolve (a stabilised public projection is deferred to the write surface #398).

        Populated for every built drawing, including a manual-mode (``auto_dims=False``)
        build — detection runs in the pipeline, not the annotation pass (#398), so a
        script can dimension detected features even when it suppressed the automatic
        ones. ``None`` only on a bare, unbuilt ``Drawing``.
        """
        return self._part_model

    def place_dim(self, p1, p2, side, view, draft, *, name=None, slot=8.0, feature=None, **kwargs):
        """Add a :class:`~build123d_drafting.helpers.Dimension` that stacks cleanly
        with the auto-generated dimensions by delegating to the same strip-allocation
        system (:class:`Strip`) that :func:`build_drawing` uses internally.

        Args:
            p1, p2: page-coordinate tuples ``(px, py, 0)`` — use :meth:`at` to
                convert world coordinates.
            side: ``"above"``, ``"below"``, ``"left"``, or ``"right"``.
            view: ``"front"``, ``"plan"``, or ``"side"``.
            draft: the drawing's :attr:`draft` preset.
            name: optional annotation name for later :meth:`remove` / replace.
            slot: strip slot depth (mm); the perpendicular space reserved per dim.
            feature: optional source IR feature to attribute this dim to, so
                :meth:`drop` / :meth:`annotations_of` can find it (#398).
            **kwargs: forwarded to ``Dimension`` (e.g. ``label=``, ``tolerance=``).

        Uses the single-position strip carve (obstacle-aware: it clears every
        placed annotation), **not** the ADR-0009 collect-then-solve path the
        automatic placers use — **by design** (#396). ``place_dim`` is an
        *incremental edit* run after the build's corridor is already committed, so
        it deliberately does not re-solve the strip: a full solve would reorder or
        dedup **already-placed** dims (surprising for a deliberate edit). It
        therefore has no cross-pass priority selection, crossing-free re-ordering,
        or dedup against a coincident auto dim — those are properties of the batch
        build, recovered by a *recompose* (:func:`finalize_drawing`, when
        available) or a rebuild, not of a single incremental placement. Prefer
        :meth:`dimension` for a feature-referenced edit.

        Falls back to a fixed ``slot`` offset when the strip is full or when no
        layout analysis is available (e.g. when ``auto_dims=False`` was not used
        with :func:`build_drawing`).
        """
        a = self._analysis
        _view_zones = {"front": "fv_zones", "plan": "pv_zones", "side": "sv_zones"}
        strip = None
        if a is not None:
            zones = getattr(a, _view_zones.get(view, ""), None)
            if zones is not None:
                strip = getattr(zones, side, None)
        dist = slot
        if strip is not None:
            # Cursor-free tier placement (ADR 0009, #150): find a free tier that clears
            # every placed annotation, replacing Strip.allocate. axis = the stacking axis
            # (X for left/right, Y for above/below); perp_span = the dim's cross-axis span
            # so a perpendicular-disjoint occupant does not false-block.
            ax = 0 if side in ("left", "right") else 1
            axis = "x" if ax == 0 else "y"
            perp = tuple(sorted((p1[1 - ax], p2[1 - ax])))
            coord = carve_free_position(self, strip, view, axis, slot, perp)
            if coord is not None:
                if side in ("right", "above"):
                    dist = coord - max(p[ax] for p in (p1, p2))
                else:
                    dist = min(p[ax] for p in (p1, p2)) - coord
        # p1/p2 are page coordinates; Dimension labels the raw page distance when
        # no label is given, which is scale-too-big at non-1:1 scales. Supply the
        # real-world length (page distance ÷ drawing scale) unless the caller set
        # an explicit label.
        if "label" not in kwargs:
            page_len = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
            kwargs["label"] = _fmt(page_len / self.scale)
        return self.add(_dim(p1, p2, side, max(dist, 4.0), draft, **kwargs), name, feature=feature)

    # -- annotations ----------------------------------------------------------
    def add(self, obj, name=None, view=None, feature=None):
        """Register an annotation so lint and export include it; returns ``obj``.

        Re-using an existing ``name`` replaces the previously added object (it is
        dropped from :attr:`items`), so a name always maps to one object.

        ``view`` records which orthographic view ("front"/"plan"/"side") owns
        this annotation, so the layout can compose each view with its own
        annotations as a single footprint block (#121).  Pass ``None`` for
        drawing-level marks (title block, iso/section notes) that belong to no
        single view.

        ``feature`` records the source IR feature this annotation was rendered for
        (#398), so :meth:`drop` / :meth:`annotations_of` can operate by feature. The
        render layer passes it (it knows the feature); ``None`` for part-level marks.
        """
        displaced = self._registry.named(name) if name is not None else None
        if displaced is not None:
            self.items.remove(displaced)
        annotate(obj, name)
        self.items.append(obj)
        # The registry records name -> obj, the owning view, and the source feature,
        # and drops any pin the replaced name carried — a replacement is a fresh object
        # (#89) — and clears stale view/feature tags when re-added without them (#121/#398).
        self._registry.add(obj, name, view, feature)
        return obj

    def remove(self, name):
        """Remove a previously named annotation. Raises ``KeyError`` if absent."""
        obj = self._registry.remove(name)  # forgets object, view, feature, and pin (#89)
        if obj is None:
            raise KeyError(f"no annotation named {name!r}")
        self.items.remove(obj)
        return obj

    def annotations_of(self, feature) -> dict:
        """``{name: object}`` for every annotation rendered for *feature* (#398).

        *feature* is an IR feature from :meth:`model` (``dwg.model().features[i]``).
        Matched by value, so the exact object is not required. Empty if the feature has
        no annotations (or its render pass does not yet tag provenance — coverage grows
        as passes are migrated)."""
        return {n: self._registry.named(n) for n in self._registry.names_for_feature(feature)}

    def drop(self, feature) -> list:
        """Remove every annotation rendered for *feature* (#398) — the semantic curation
        verb: "stop dimensioning this feature". Returns the removed names.

        Use a feature from :meth:`model`: ``dwg.drop(dwg.model().features[0])``. Removing
        a feature's callout/centre-mark/size-dims is a page-level edit; call
        :func:`finalize_drawing` afterwards (when available) to recompose the sheet."""
        names = self._registry.names_for_feature(feature)
        for n in names:
            self.remove(n)
        return names

    @staticmethod
    def _derive_span(feature, param):
        """Model-space ``(lo, hi)`` endpoints for a value-only *linear* param whose geometry
        the feature carries (#411), or ``None`` for a callout param with no linear span.

        Slots: the width dim spans ``width_axis`` across ``w_center ± width/2`` (at the
        length midpoint); the length dim spans ``long_axis`` ``lo → hi`` (at the centre
        line) — the same endpoints ``render_slots`` measures."""
        if getattr(feature, "kind", None) == "slot":
            ax = {"x": 0, "y": 1, "z": 2}
            li, wi = ax[feature.long_axis], ax[feature.width_axis]
            a = list(feature.frame.origin)
            b = list(feature.frame.origin)
            if param.role == "slot_length":
                a[li], b[li] = feature.lo, feature.hi
                a[wi] = b[wi] = feature.w_center
            elif param.role == "slot_width":
                mid = (feature.lo + feature.hi) / 2
                half = feature.width / 2
                a[wi], b[wi] = feature.w_center - half, feature.w_center + half
                a[li] = b[li] = mid
            else:
                return None
            return tuple(a), tuple(b)
        return None

    def dimension(
        self, feature, param, *, role=None, side="above", view=None, name=None, **kwargs
    ):
        """Add a dimension for *feature*'s *param*, attributed to the feature (#398e).

        The feature-referenced **add** verb: pair to :meth:`drop`. *feature* is an IR
        feature from :meth:`model`; *param* is a **linear** parameter kind it exposes — a
        turned step's ``"length"`` or a slot's ``"length"``/``"width"`` (which the feature
        carries as value-only geometry, derived here via :meth:`_derive_span`).
        The dimension is placed into free strip space and tagged with *feature*, so
        :meth:`drop` / :meth:`annotations_of` find it. Returns the annotation name.

        A feature may expose several params of one kind (an envelope's width/height/depth,
        or a slot's ``slot_width``/``slot_length``, are all ``"length"``); pass ``role=`` to
        pick one — a bare kind matching more than one raises rather than guessing.

        ``view`` is chosen automatically as the orthographic view (``"front"``/``"plan"``/
        ``"side"``) where the span projects non-degenerate — a length along the turning
        axis vanishes in its end-on view, so the view follows the geometry. Pass ``view=``
        to force one of those three (a non-orthographic view foreshortens the span and is
        rejected). ``side`` defaults to ``"above"``; ``kwargs`` forward to the dimension.

        Raises ``ValueError`` if the feature has no such param, the kind is ambiguous, or
        *view* is not orthographic. A hole's ``"diameter"``/``"depth"`` are **leader
        callouts**, not linear dimensions, so they raise here — a callout add verb is a
        separate mechanism, tracked apart from this one.
        """
        if self._defer_intents:  # #426: record, don't place — finalize() drains it
            self._intents.append(
                Intent(
                    "dimension",
                    feature,
                    {
                        "param": param,
                        "role": role,
                        "side": side,
                        "view": view,
                        "name": name,
                        **kwargs,
                    },
                )
            )
            return ""
        _ortho = ("front", "plan", "side")
        if view is not None and view not in _ortho:
            raise ValueError(
                f"view must be one of {_ortho}, not {view!r} (it foreshortens the span)"
            )
        matches = [
            q for q in feature.parameters() if q.kind == param and (role is None or q.role == role)
        ]
        if not matches:
            r = f"/{role!r}" if role else ""
            raise ValueError(
                f"{type(feature).__name__} has no '{param}'{r} parameter to dimension"
            )
        if len(matches) > 1:
            roles = sorted(q.role for q in matches)
            raise ValueError(
                f"{type(feature).__name__} has {len(matches)} '{param}' params (roles {roles}) "
                f"— pass role= to choose one"
            )
        # A span-carrying param (a step length, a location) gives its endpoints directly;
        # a value-only linear param (a slot's dims) derives them from the feature geometry
        # (#411). A callout param (a hole's diameter/depth) has no linear span at all.
        span = matches[0].span or self._derive_span(feature, matches[0])
        if span is None:
            raise ValueError(
                f"'{param}' (role {matches[0].role!r}) is a leader-callout parameter, not a "
                f"linear dimension — dimension() draws linear dims only (a callout add verb "
                f"is tracked separately)"
            )
        (lo, hi) = span
        p1 = p2 = None
        for v in [view] if view else _ortho:
            q1, q2 = self.at(v, *lo), self.at(v, *hi)
            if math.hypot(q2[0] - q1[0], q2[1] - q1[1]) > 1e-6:
                view, p1, p2 = v, q1, q2
                break
        if p1 is None:
            raise ValueError(
                f"'{param}' span projects to a point in "
                f"{'the requested view' if view else 'every orthographic view'} — nothing to dimension"
            )
        if name is None:
            i = 0
            while (name := f"dim_{param}{i}") in self._named:
                i += 1
        self.place_dim(p1, p2, side, view, self.draft, name=name, feature=feature, **kwargs)
        return name

    def callout(self, feature, *, view=None, name=None) -> str:
        """Add a **ø leader callout** for *feature* (#414/#419) — the callout half of the
        feature-referenced **add** surface, symmetric with :meth:`drop`.

        Where :meth:`dimension` draws a linear dim, ``callout`` draws a leader: for a
        **hole/pattern**, the ø / ``n×`` / through-or-depth / counterbore callout (the same
        text the auto-pass builds), placed beside the feature's end-on view (``view``
        defaults to it); for a turned **step/boss**, the ``ø…`` diameter leader in the row
        below (X-turned) or column left of (Z-turned) the front view. Tagged with *feature*
        so :meth:`drop` / :meth:`annotations_of` find it. Returns the annotation name.

        Raises ``ValueError`` if *feature* exposes no callout (use :meth:`dimension` for a
        linear param). Placed reasonably, not via the auto-pass's whole-set solve
        (byte-identity is not a goal, #400 Ph2) — :meth:`repair` tidies the rest. A
        step/boss diameter that finds no room returns ``""`` (a warning-level drop, like
        the auto-pass), rather than raising, so a reconstruction script never aborts.
        """
        if self._defer_intents:  # #426: record, don't place — finalize() drains it
            self._intents.append(Intent("callout", feature, {"view": view, "name": name}))
            return ""
        from draftwright.annotations.holes import add_feature_callout, add_feature_diameter

        if getattr(feature, "kind", None) in ("step", "boss"):
            return add_feature_diameter(self, feature)
        return add_feature_callout(self, feature, view=view, name=name)

    def furniture(self, feature, *, view=None) -> list[str]:
        """Add a hole/pattern's non-dimensional **sheet furniture** (#419) — centre marks
        (every member) plus a pattern's centre-cross (bolt circle) or pitch/grid dims.

        The geometric marks a feature carries that no other verb emits: where
        :meth:`callout` draws the ø leader and :meth:`locate` the position dims, ``furniture``
        draws the centre marks and pattern furniture. *feature* is a hole/pattern from
        :meth:`model`; ``view`` defaults to its end-on view. Each mark is tagged with
        *feature* so :meth:`drop` / :meth:`annotations_of` find it. Returns the placed names
        (varies by pattern kind — a bolt circle emits a centre-cross, a linear/grid array a
        pitch dim).

        Raises ``ValueError`` if *feature* is not a hole/pattern (use :meth:`dimension`).
        """
        if self._defer_intents:  # #426: record, don't place — finalize() drains it
            self._intents.append(Intent("furniture", feature, {"view": view}))
            return []
        from draftwright.annotations.holes import add_feature_furniture

        return add_feature_furniture(self, feature, view=view)

    def section(self) -> list[str]:
        """Add the automatic full **section A–A** (#420) — the section half of the
        editable surface.

        Part-level, unlike the per-feature verbs: a section fires when a Z-axis
        hole/pattern has a counterbore, spotface, or blind bottom (its internal
        profile is hidden-line-only in every ortho view), cutting through the densest
        qualifying row. Takes no argument (the auto A–A) and is **not** feature-tagged
        or :meth:`drop`-compatible — a section is atomic, so it is dropped by commenting
        the call. Returns the placed annotation names, or ``[]`` when no section is
        warranted or there is no room. Call it *after* the per-feature verbs — the
        section's room check clears whatever is already placed right of the side view.
        """
        if self._defer_intents:  # #426: record, don't place — finalize() drains it
            self._intents.append(Intent("section", None, {}))
            return []
        from draftwright.annotations.sections import add_section

        return add_section(self)

    def locate(self, feature, *, axes=None) -> list[str]:
        """Add datum-referenced **X/Y position dimensions** for a Z-axis hole/pattern
        (#418) — the location half of the feature-referenced **add** surface.

        Distinct from :meth:`dimension` (a feature's own intrinsic linear params): a
        location dim measures the *datum → feature-centre* offset, which no feature
        exposes as a parameter. *feature* is a hole/pattern from :meth:`model`; ``axes``
        selects the in-plane axes (default both — ``"x"`` above the plan view, ``"y"``
        above the side view). Each dim is placed into free space beside its view and
        tagged with *feature* so :meth:`drop` / :meth:`annotations_of` find it. Returns
        the placed names (0–2 — one per axis with a real offset).

        Raises ``ValueError`` if *feature* is not a Z-axis hole/pattern (side-drilled
        bores are placed by the auto-pass). A feature with no datum-referenced ref (a
        datum-less model, a concentric/on-datum bore, or a ref deduped against a sibling)
        returns ``[]``. Placed reasonably, not via the auto-pass's corridor solve
        (byte-identity is not a goal, #400 Ph2).
        """
        if self._defer_intents:  # #426: record, don't place — finalize() drains it
            self._intents.append(Intent("locate", feature, {"axes": axes}))
            return []
        from draftwright.annotations.holes import add_feature_location

        return add_feature_location(self, feature, axes=axes)

    @contextlib.contextmanager
    def deferred(self):
        """Record add-verb calls as placement intents, then batch-solve on exit (#426).

        Inside the ``with`` block the add verbs (:meth:`callout`/:meth:`locate`/
        :meth:`furniture`/:meth:`dimension`/:meth:`section`) **record** their intent
        instead of placing it live; on normal exit :meth:`finalize` drains them through
        the auto-pass's own solvers, so a reconstruction reaches auto-pass placement
        quality (crossing-free locations, the priority-drop callout solve, the turned
        diameter/step-length set-solves) rather than greedy live placement. This is the
        record-then-finalize surface the generated ``--script`` builds on.

        ``finalize()`` runs on **normal** exit only — if the block raises, the recorded
        intents are left intact (finalize is skipped) so the error surfaces cleanly and a
        retry can re-drain. Restores the prior ``_defer_intents`` on exit. Idempotent: a
        later :meth:`export` (which also finalizes) no-ops once the intents are drained.

        Do **not** nest ``deferred()`` blocks: ``finalize()`` drains the whole recorded
        list on every exit, so an inner block would place the outer block's still-pending
        intents early. One block per reconstruction (what the ``--script`` emitter does).
        """
        prev, self._defer_intents = self._defer_intents, True
        try:
            yield self
        finally:
            self._defer_intents = prev
        self.finalize()

    def finalize(self) -> None:
        """Drain the recorded placement intents (#426).

        When the drawing was built in **deferred** mode (``_defer_intents``), the add
        verbs recorded :class:`~draftwright.intents.Intent`\\s instead of placing. This
        drains them, routing what it can through the auto-pass's own solvers:

        * **(A)** live-replays furniture, non-slot dimensions, and axes-restricted locates
          (in recorded order, pop-after-success);
        * **(reserve, Phase 3b)** if a ``section`` was recorded, its cutting-plane row is
          reserved first so the callout carve sees it as an obstacle (Coupling A);
        * **(B1, Phase 3a)** hole/pattern ø **callouts** through ``_annotate_holes`` — the
          real priority-drop / central-bore-anchoring solve;
        * **(B2, Phase 2a+2b)** both-axes **locations** (``render_locations``) and
          **slots** (``render_slots``) through the SHARED corridor + one ``drain_corridors``
          — one crossing-free, deduped, monotone ladder (a slot position coincident with a
          hole location collapses to one dim, #345);
        * **(B3, Phase 4a)** X/Z-turned step/boss ø **diameters** through ``render_diameters``
          — the row-below / column-left set-solve;
        * **(B3b, Phase 4b)** a turned shaft's step-length **chain** through
          ``render_step_lengths`` — the unified chain / ``N× v`` collapse / staggered tiers;
        * **(C)** the ``section`` renders last (its room check clears the side view's right);
        * **(D, Phase 4c)** dense-scattered plan holes escalate to the hole **table** + balloon
          ring via ``_maybe_tabulate_holes`` — last, so it sees the section + title block as
          obstacles. The density gate counts *all* analysis holes, so this is a full-
          reconstruction escalation (a partial hand-edit still tabulates the full count, #434);
          ``_escalations`` is cleared after so a repeat batch can't re-fire the fixed-name table.

        A slot records two size dims (``slot_width``/``slot_length``) on one feature; routing
        the feature also regenerates its model-derived datum **position** dim, so finalize
        places a *superset* of the recorded slot intents (auto-pass parity by design —
        commenting one of a slot's two lines still routes the feature). An unsupported-axis
        (Y-turned) step/boss callout live-replays, so it surfaces the same ValueError the
        live verb raises. Only ``only``-set routing is used here; the auto-pass path is
        untouched.

        Idempotent (draining empties the list; a repeat call — or ``export()`` then
        ``export_pdf()`` — no-ops) and a no-op when nothing was recorded (the live/auto-pass
        path), so ``export()`` calls it unconditionally. **Resilient:** a live-replayed
        intent is removed only after it places, so a verb that raises surfaces the error
        and leaves the rest recorded. A record → finalize → record-more → finalize
        sequence drains each batch (#428 review).
        """
        if not self._intents:
            return
        from draftwright.annotations._common import drain_corridors
        from draftwright.annotations.from_model import (
            render_diameters,
            render_locations,
            render_slots,
            render_step_lengths,
        )
        from draftwright.annotations.holes import _annotate_holes, build_view_of_axis
        from draftwright.annotations.orchestrator import _maybe_tabulate_holes
        from draftwright.annotations.sections import (
            _add_section_view,
            _reserve_section_row,
            feature_hole_keys,
        )
        from draftwright.model import PartModel, plan_dimensions, plan_sections

        # Corridor state the auto-pass creates in _auto_annotate S0 but a detect-only build
        # lacks — render_locations/_annotate_holes/drain_corridors register/read here.
        if not hasattr(self, "_corridor_batch"):
            self._corridor_batch: dict = {}
        if not hasattr(self, "_escalations"):
            self._escalations: list = []
        if not hasattr(self, "_detail_requests"):
            self._detail_requests: list = []

        model, a = self._part_model, self._analysis
        routable = model is not None and a is not None
        # The section plan (if a section was recorded) — the ONE plan reserved before the
        # callout carve sees its row (Coupling A) and rendered last (Phase 3b).
        _section = None
        if routable and any(it.kind == "section" for it in self._intents):
            assert a is not None and isinstance(model, PartModel)
            _section = plan_sections(model, feature_hole_keys(a))
        # Route through the auto-pass solvers when possible (else everything live-replays):
        #  - BOTH-axes locate → the ADR-0009 location corridor. An axes-restricted locate
        #    can't go through the per-feature filter, so it live-replays (#429).
        #  - hole/pattern CALLOUT → _annotate_holes' priority-drop/anchoring solve (the
        #    section row, if any, is reserved first below).
        #  - step/boss ø CALLOUT → render_diameters' row-below/column-left set-solve (Phase 4a).
        corridor_ids = {
            id(it)
            for it in self._intents
            if routable and it.kind == "locate" and it.kwargs.get("axes") is None
        }
        callout_ids = {
            id(it)
            for it in self._intents
            if routable
            and it.kind == "callout"
            and getattr(it.feature, "kind", None) in ("hole", "pattern")
        }
        dia_ids = {
            id(it)
            for it in self._intents
            if routable
            and it.kind == "callout"
            and getattr(it.feature, "kind", None) in ("step", "boss")
            # X/Z-turned only — render_diameters can't place a Y-turned diameter, so leave
            # it on live replay where callout() raises the same clear error (#432 review).
            and getattr(getattr(it.feature, "frame", None), "axis", None) in ("x", "z")
        }
        # step LENGTH dimension intents (role="step") → render_step_lengths' chain (Phase 4b),
        # but only on a TURNED part (a.prof is not None, mirroring the auto-pass guard) — else
        # they live-replay. Excludes the step's ø (a callout routed in dia_ids above).
        len_ids = {
            id(it)
            for it in self._intents
            if routable
            and a is not None
            and a.prof is not None
            and it.kind == "dimension"
            and getattr(it.feature, "kind", None) == "step"
            and it.kwargs.get("param") == "length"
            and it.kwargs.get("role") == "step"
        }
        # SLOT dimension intents (#426 Phase 2b) → render_slots' corridor placement. A slot
        # records TWO dims (slot_width + slot_length) on ONE SlotFeature; both route the
        # feature, which regenerates width + length + the datum position (a superset — the
        # position dim is model-derived, not recorded). Slots share the location corridor,
        # so they register alongside B2's locations and drain in the SAME solve (the #345
        # dedup of a slot position coincident with a hole location needs one combined pass).
        # Match on param/role like len_ids above (#439): a slot exposes only the two length
        # params, so a malformed slot dim (e.g. dimension(slot, "diameter")) falls through to
        # live replay, where the verb raises the same ValueError instead of being swallowed.
        slot_ids = {
            id(it)
            for it in self._intents
            if routable
            and it.kind == "dimension"
            and getattr(it.feature, "kind", None) == "slot"
            and it.kwargs.get("param") == "length"
            and it.kwargs.get("role") in ("slot_width", "slot_length")
        }
        only_loc = {it.feature for it in self._intents if id(it) in corridor_ids}
        only_callout = {it.feature for it in self._intents if id(it) in callout_ids}
        only_dia = {it.feature for it in self._intents if id(it) in dia_ids}
        only_len = {it.feature for it in self._intents if id(it) in len_ids}
        slot_feats = {it.feature for it in self._intents if id(it) in slot_ids}

        deferred, self._defer_intents = self._defer_intents, False  # replay must place
        try:
            # Reserve the section's cutting-plane row BEFORE the callout carve so the carve
            # sees it as an obstacle (Coupling A, ADR 0009 P5 strand 3); rendered last (leg C).
            if _section is not None:
                assert a is not None
                _reserve_section_row(self, a, _section)
            # (A) live-replay every intent EXCEPT the routed callouts/locates and section
            #     (furniture, step/boss callouts, dimensions, axes-restricted locates).
            i = 0
            while i < len(self._intents):
                it = self._intents[i]
                if (
                    it.kind == "section"
                    or id(it) in corridor_ids | callout_ids | dia_ids | len_ids | slot_ids
                ):
                    i += 1
                    continue
                self._replay_intent(it)  # resilient: a raise leaves the rest recorded
                self._intents.pop(i)
            # (B1) hole/pattern callouts through the REAL priority-drop/anchoring solve.
            #      Furniture is owned by the replayed furniture() intents → place_furniture=False.
            if only_callout:
                assert a is not None and isinstance(model, PartModel)  # only_callout ⟹ routable
                _annotate_holes(
                    self,
                    a,
                    build_view_of_axis(a),
                    plan_dimensions(model),
                    feature_hole_keys(a),
                    only=only_callout,
                    place_furniture=False,
                )
            # Drop the placed callout intents NOW — before the fallible B2 — so a raise in
            # B2 can't re-route (and, via first-free hc_ naming, duplicate) them on a retry.
            self._intents = [it for it in self._intents if id(it) not in callout_ids]
            # (B2) both-axes locations + slots through the SHARED location corridor — one
            #      crossing-free ladder, one drain (auto-pass registers locations then slots,
            #      then a single drain_corridors, so a slot position coincident with a hole
            #      location dedups, #345). Register both, then drain once (Phase 2a + 2b).
            if only_loc or slot_feats:
                assert a is not None and isinstance(model, PartModel)  # either ⟹ routable
                if only_loc:
                    render_locations(self, model, a, only=only_loc)
                if slot_feats:
                    render_slots(self, model, a, only=slot_feats)
                drain_corridors(self)
            self._intents = [it for it in self._intents if id(it) not in corridor_ids | slot_ids]
            # (B3) step/boss ø diameters through render_diameters' set-solve (row-below /
            #      column-left) — auto-pass S11b, after callouts/locations, before section.
            if only_dia:
                assert a is not None and isinstance(model, PartModel)  # only_dia ⟹ routable
                render_diameters(self, plan_dimensions(model), only=only_dia)
            self._intents = [it for it in self._intents if id(it) not in dia_ids]
            # (B3b) turned step-length CHAIN through render_step_lengths (N× collapse /
            #       staggered tiers) — auto-pass S11b, after diameters, before section.
            if only_len:
                assert a is not None and isinstance(model, PartModel)  # only_len ⟹ routable
                render_step_lengths(self, plan_dimensions(model), only=only_len)
            self._intents = [it for it in self._intents if id(it) not in len_ids]
            # (C) render the section LAST, reusing the reserved plan (its room check clears
            #     everything right of the side view; _add_section_view clears the reservation).
            #     A recorded section with no trigger (_section is None) is a no-op.
            self._intents = [it for it in self._intents if it.kind != "section"]
            if _section is not None:
                assert a is not None
                _add_section_view(self, a, _section)
            # (D, Phase 4c) dense-scattered plan-view holes escalate to the hole TABLE +
            #     balloon ring — LAST, mirroring the auto-pass (_maybe_tabulate_holes runs
            #     last in _auto_annotate) so the resolver sees the section + title block as
            #     obstacles. It reads _escalations (the callout/location drops B1/B2 collected)
            #     and the scattered-hole coverage recorded at the hole emit site even under
            #     place_furniture=False (#426 Ph4c) to find + replace the plan callouts. The
            #     density gate counts ALL analysis holes (a.holes), so this is a FULL-
            #     reconstruction escalation: a partial hand-edit that drops some callout() lines
            #     still tabulates the full count (documented full-reconstruction scope, #434).
            #     Clear _escalations after so a repeat finalize batch can't re-fire against stale
            #     drops and collide on the fixed name hole_table_plan.
            if routable:
                assert a is not None
                _maybe_tabulate_holes(self, a)
                self._escalations = []
        finally:
            self._defer_intents = deferred

    def _replay_intent(self, it: Intent) -> None:
        """Place one recorded intent by calling its live verb (#426 Phase 1)."""
        if it.kind == "callout":
            self.callout(it.feature, **it.kwargs)
        elif it.kind == "locate":
            self.locate(it.feature, **it.kwargs)
        elif it.kind == "furniture":
            self.furniture(it.feature, **it.kwargs)
        elif it.kind == "dimension":
            self.dimension(it.feature, **it.kwargs)
        elif it.kind == "section":
            self.section()

    def annotations(self) -> dict:
        """Return ``{name: type_name}`` for every *named* annotation (#27).

        Lets a script introspect what is already on the drawing before adding
        more — e.g. ``if "dim_width" not in dwg.annotations()`` — so it can do
        incremental edits without risking a silent name-collision replace.
        Unnamed annotations are omitted; iterate :attr:`items` for those.
        """
        return self._registry.annotations()

    def iter_annotations(self):
        """Iterate ``(name, annotation object)`` for every named annotation.

        The encapsulated read path for production code (lint, sheet, sections,
        renderers): use this instead of reaching into ``dwg._named`` directly so the
        registry stays the single owner of annotation identity (#241).
        """
        return self._registry.iter_named()

    def view_of(self, name):
        """The owning orthographic view for *name* ("front"/"plan"/"side"), or
        ``None`` — instead of reading ``dwg._anno_view`` directly (#241)."""
        return self._registry.view_of(name)

    def annotations_in_view(self, view):
        """Yield ``(name, annotation object)`` for the named annotations owned by
        *view* — the common filter-by-view read (#241)."""
        return (
            (n, o) for n, o in self._registry.iter_named() if self._registry.view_of(n) == view
        )

    def get_annotation(self, name):
        """Return the named annotation object, or ``None`` if no such name (#27)."""
        return self._registry.named(name)

    def add_table(self, rows, *, prefer="tr", name="table", block_cols=None):
        """Add a generic data table, placed in a free corner (#93).

        *rows* is a list of equal-length string tuples (``rows[0]`` is the
        header). The table is positioned by :func:`fit_box` clear of the views,
        title block, and existing annotations; *prefer* is the page corner to sit
        nearest. Returns the table annotation, or ``None`` if it has no rows or
        will not fit (recorded as ``table_dropped`` lint). Gear-data, BOM, and
        revision tables all go through here; :meth:`add_hole_table` is the
        hole-specific convenience built on it.
        """
        if not rows:
            return None
        table = _build_table(rows, self.draft, block_cols=block_cols)
        w, h = table.table_size
        a = self._analysis
        margin = a.margin if a is not None else 10.0
        pw = a.PAGE_W if a is not None else self.page_w
        ph = a.PAGE_H if a is not None else self.page_h
        region = (margin, margin, pw - margin, ph - margin)
        obstacles = [b for v in self.views if (b := self.view_bounds(v)) is not None]
        for o in self.items:
            try:
                bb = o.bounding_box()
            except Exception:  # noqa: BLE001 — not every annotation bbox-es cleanly
                continue
            obstacles.append((bb.min.X, bb.min.Y, bb.max.X, bb.max.Y))
        pos = fit_box((w, h), region, obstacles, prefer)
        if pos is None:
            self._record_build_issue(
                "warning", "table_dropped", f"table {name!r} did not fit the sheet"
            )
            return None
        return self.add(table.locate(Location((pos[0], pos[1], 0))), name)

    def _hole_spec_groups(self, view):
        """Ordered ``(tag, [holes])`` spec-groups of *view*'s holes (tags A, B,
        …). The shared basis for the hole table's rows and its balloons, so the
        TAG column and the balloon glyphs line up."""
        a = self._analysis
        target = {"plan": "z", "front": "y", "side": "x"}.get(view)
        if a is None or target is None or view not in self._coords:
            return []

        groups: dict = {}
        for h in a.holes:
            if _axis_letter(h) == target:
                groups.setdefault(HoleSpec.from_hole(h), []).append(h)
        glist = list(groups.values())
        return list(zip(_tag_sequence(len(glist)), glist, strict=True))

    def _add_balloons(self, view, specs):
        """Place a leadered balloon for each ``(tag, j, hole)`` in *specs*,
        fitted into the halo the layout reserved around the view (#111).

        Each hole is assigned to the nearest reserved band — left, right, or top
        of the plan view (never the bottom: the front view abuts it there) — and
        the balloons in each band are spread along it with the 1D strip solver so
        none overlap, each pulled toward its hole's coordinate.  A :class:`Leader`
        then runs from the hole rim to the glyph.  Because the layout reserved
        this band before placing the views (:func:`_est_plan_halo` /
        :func:`_will_balloon`), the balloons sit in clear space off the part and
        no leader crosses a neighbouring view.
        """
        a = self._analysis
        if view not in self._coords or a is None:
            return
        pp = self._coords[view].pp
        fs = self.draft.font_size
        r = fs * 1.5  # circle comfortably larger than the glyph
        standoff = _STRIP_GAP
        gap = 2 * r + 2 * _STRIP_SPACING  # min centre-to-centre: balloon + padding both sides

        # Plan-view page edges; the reserved bands sit just outside them.
        pl, pr = a.PV_X - a.fv_hw, a.PV_X + a.fv_hw
        pt, pb = a.PV_Y + a.pv_hh, a.PV_Y - a.pv_hh
        sv_left = a.SV_X - a.sv_hw
        margin, ph, pw = a.margin, a.PAGE_H, a.PAGE_W

        # Stack the balloon ring *beyond* the annotations already placed around the
        # plan view, not on top of them (#121). Measure the REAL depth the dimensions
        # extend into each band from their placed bounding boxes — the strip cursor's
        # `depth_used` is retired (ADR 0009 / #150) and would report a constant gap.
        # (This is what the top band already did because `depth_used` went stale under
        # the hole-table escalation, #125; now every side measures the same way.) Match
        # on DIMENSION type (not a `dim_` name prefix — that would drop the m_env_*/
        # m_loc*/m_slot* dims below/beside the view), plus PMI leaders (pmi_*). Balloons,
        # the table, construction centrelines (bc_*) and callouts are not Dimensions and
        # are correctly excluded (centrelines are crossable; callouts route separately).
        top_dim = bot_dim = left_dim = right_dim = 0.0
        for nm, obj in self._named.items():
            if self._anno_view.get(nm) != view:
                continue
            if not isinstance(obj, (Dimension, SafeDimension)) and not nm.startswith("pmi_"):
                continue
            try:
                ob = obj.bounding_box()
            except Exception:  # noqa: BLE001 — a mark with no bbox can't obstruct
                continue
            if ob.max.X > pl and ob.min.X < pr:  # spans the plan's width → top/bottom bands
                if ob.max.Y > pt:
                    top_dim = max(top_dim, ob.max.Y - pt)
                if ob.min.Y < pb:
                    bot_dim = max(bot_dim, pb - ob.min.Y)
            if ob.max.Y > pb and ob.min.Y < pt:  # spans the plan's height → left/right bands
                if ob.min.X < pl:
                    left_dim = max(left_dim, pl - ob.min.X)
                if ob.max.X > pr:
                    right_dim = max(right_dim, ob.max.X - pr)

        # A dense part can stack many pitch dims on one side (holes._place_pitch_dim
        # pushes each successive one 10 mm further out, #92), so the measured depth
        # can exceed the room between the view and the page edge. Clamp each band so
        # its *ring itself* never lands off the drawable area (#349 follow-up) — the
        # ring then sits at the margin and overlaps the far witness lines instead,
        # which is only a tolerated warning (structural.py compares label_bbox, not
        # the full bbox, for overlap), never the out_of_bounds error.
        left_dim = min(left_dim, max(0.0, pl - standoff - 2 * r - margin))
        right_dim = min(right_dim, max(0.0, pw - margin - pr - standoff - 2 * r))
        top_dim = min(top_dim, max(0.0, ph - margin - pt - standoff - 2 * r))
        bot_dim = min(bot_dim, max(0.0, pb - standoff - 2 * r - margin))

        # A bottom band (below PV, beyond the overall-width dim) is usable only
        # when the FV↔PV gap has room for the width dim *and* a balloon row;
        # otherwise bottom-edge holes fall back to the nearest side/top band.
        bottom_line = pb - bot_dim - standoff - r
        has_bottom = pb - (a.FV_Y + a.fv_hh) > bot_dim + standoff + 2 * r

        # Assign each hole to the nearest reserved band.
        bands: dict = {"left": [], "right": [], "top": [], "bottom": []}
        for tag, j, hole in specs:
            cx, cy = pp(*hole.location)
            choices = {"left": cx - pl, "right": pr - cx, "top": pt - cy}
            if has_bottom:
                choices["bottom"] = cy - pb
            bands[min(choices, key=lambda s: choices[s])].append((tag, j, hole, cx, cy))

        # left/right balloons vary in Y at a fixed X just outside the part; top
        # and bottom balloons vary in X at a fixed Y just beyond it. Each line is
        # offset by its side's dim depth so the ring sits clear of the dims.
        dropped = 0
        dropped += self._place_band(
            view,
            bands["left"],
            "y",
            pl - left_dim - standoff - r,
            margin + r,
            ph - margin - r,
            gap,
            fs,
            r,
        )
        dropped += self._place_band(
            view,
            bands["right"],
            "y",
            pr + right_dim + standoff + r,
            margin + r,
            ph - margin - r,
            gap,
            fs,
            r,
        )
        dropped += self._place_band(
            view,
            bands["top"],
            "x",
            pt + top_dim + standoff + r,
            pl - standoff,
            sv_left - r,
            gap,
            fs,
            r,
        )
        dropped += self._place_band(
            view,
            bands["bottom"],
            "x",
            bottom_line,
            pl - standoff,
            sv_left - r,
            gap,
            fs,
            r,
        )
        # A band too crowded to hold every balloon drops its tail (the strip solver's
        # prefix fallback) — record it instead of letting the balloons vanish silently
        # (review follow-up). The resolver keeps the callout_dropped lint for a pattern
        # whose balloon did not land, so a missing pattern balloon is still a coverage gap.
        if dropped:
            self._record_build_issue(
                "warning",
                "balloon_dropped",
                f"{dropped} balloon(s) could not fit their reserved band and were dropped",
            )

    def _place_band(self, view, members, axis, line, lo, hi, gap, fs, r) -> int:
        """Spread *members* (``(tag, j, hole, cx, cy)``) along one reserved band
        with the strip solver, then render a leadered balloon for each (#111).

        *axis* is the band's free axis (``"y"`` for the left/right bands, ``"x"``
        for the top); *line* is the fixed coordinate of the other axis.  Overflow
        beyond ``[lo, hi]`` drops the tail rather than running balloons off-page.
        Returns the number of members dropped, so the caller can surface it as lint
        (a silently truncated balloon leaves a hole undocumented — the resolver
        must know, review follow-up).
        """
        if not members:
            return 0
        k = 4 if axis == "y" else 3  # index of cy / cx in the member tuple
        members.sort(key=lambda m: m[k])
        naturals = [m[k] for m in members]
        coords = (
            _solve_strip_1d(naturals, gap, lo, hi)
            or _greedy_strip_1d(naturals, gap, lo, hi)
            or _greedy_strip_1d(naturals, gap, lo, hi, prefix=True)
        )
        for (tag, j, hole, cx, cy), c in zip(members, coords):
            bx, by = (line, c) if axis == "y" else (c, line)
            self._render_balloon(view, tag, j, hole, cx, cy, bx, by, fs, r)
        return len(members) - len(coords)

    def _feature_of_hole_at(self, location):
        """The IR hole/pattern feature whose member sits at model-space *location*, or
        ``None`` (#408). Attributes a balloon (which carries a recognition hole, not the
        IR feature) to its feature so :meth:`drop` clears it. Cached — the model is fixed
        after build."""
        m = self._part_model
        if m is None:
            return None
        if self._hole_feature_index is None:
            idx: dict = {}
            for f in getattr(m, "features", []):
                if getattr(f, "kind", None) in ("hole", "pattern"):
                    for loc in getattr(f, "members", None) or (f.frame.origin,):
                        idx[tuple(round(c, 3) for c in loc)] = f
            self._hole_feature_index = idx
        return self._hole_feature_index.get(tuple(round(c, 3) for c in location))

    def _render_balloon(self, view, tag, j, hole, cx, cy, bx, by, fs, r):
        """Build and add one balloon glyph + leader at solved centre ``(bx, by)``
        for hole ``(cx, cy)`` (#111)."""
        loc = Location((bx, by, 0))
        # The annotation layer fills closed paths, so a circle edge renders as a
        # disc. A thin annular FACE fills as a ring — i.e. a circle outline.
        ring_faces = [f.moved(loc) for f in (Circle(r) - Circle(r - 0.35)).faces()]
        text = Text(
            txt=tag,
            font_size=fs,
            font_path=PLEX_MONO,
            align=(Align.CENTER, Align.CENTER),
            mode=Mode.PRIVATE,
        ).locate(loc)
        parts = [*ring_faces, *text.faces()]
        # Leader from the hole rim to the balloon's near edge — the glyph is the
        # label, so label="".  Skipped when the balloon could not clear the hole
        # (degenerate fallback), where a leader would be a stub through the ring.
        dx, dy = bx - cx, by - cy
        dist = math.hypot(dx, dy)
        hole_r = hole.diameter * self.scale / 2
        if dist > hole_r + r:
            ux, uy = dx / dist, dy / dist
            tip = (cx + ux * hole_r, cy + uy * hole_r, 0)
            elbow = (bx - ux * r, by - uy * r, 0)
            parts.append(Leader(tip, elbow, "", self.draft))
        balloon = Compound(children=parts)
        # Furniture that legitimately sits on the view geometry — exempt from the
        # annotation-overlap / centreline lint, as the section arrows do.
        balloon.is_centerline = True
        self.add(
            balloon,
            f"balloon_{view}_{tag}_{j}",
            view=view,
            feature=self._feature_of_hole_at(hole.location),
        )

    def _add_balloon(self, view, tag, j, hole):
        """Single-balloon convenience over :meth:`_add_balloons` (#111)."""
        self._add_balloons(view, [(tag, j, hole)])

    def add_hole_table(self, view="plan", *, prefer="tr", name=None, balloons=True):
        """Add a hole table for *view*'s holes, placed in a free corner (#93).

        One row per hole spec-group — ``TAG | ⌀ | DEPTH | QTY`` with tags
        ``A, B, …`` — placed via :meth:`add_table`. With *balloons* (the
        default) a circled tag is added at each hole keyed to its row. The table
        carries ``covers_diameters`` so the coverage lint counts the tabulated
        holes as dimensioned. Returns the table, or ``None`` when *view* has no
        holes or it will not fit.
        """
        groups = self._hole_spec_groups(view)
        if not groups:
            return None
        rows = [("TAG", "⌀", "DEPTH", "QTY")]
        diams = []
        for tag, holes in groups:
            h = holes[0]
            depth = "THRU" if h.bottom == "through" else (_fmt(h.depth) if h.depth else "")
            rows.append((tag, f"ø{_fmt(h.diameter)}", depth, str(len(holes))))
            diams.append(h.diameter)
        table = self.add_table(rows, prefer=prefer, name=name or f"hole_table_{view}")
        if table is None:
            return None
        # The table documents these diameters — let lint see that (#93).
        table.covers_diameters = tuple(diams)
        if balloons:
            self._add_balloons(
                view,
                [(tag, j, h) for tag, holes in groups for j, h in enumerate(holes)],
            )
        return table

    def pin(self, name):
        """Pin a named annotation so the engine never moves it (#89).

        A deliberate placement — by you or an AI — must win over automatic
        layout. :meth:`repair` will not re-place a pinned annotation, and the
        constraint solver (ADR 0003) treats it as fixed. Pinning fixes the
        *position*, not existence: :meth:`remove` and :meth:`clear_annotations`
        still apply. Raises ``KeyError`` if *name* is not a known annotation.
        Returns ``self`` for chaining.
        """
        if name not in self._registry:
            raise KeyError(f"no annotation named {name!r}")
        self._registry.pin(name)
        return self

    def unpin(self, name):
        """Release a pin so the engine may move *name* again (#89). Returns
        ``self``; a no-op if *name* was not pinned."""
        self._registry.unpin(name)
        return self

    def clear_annotations(self, keep=("title_block",)):
        """Remove all annotations except those named in *keep* (#74).

        Wholesale removal that does not depend on the automatic naming
        scheme — ``dwg.clear_annotations()`` strips every automatic dimension,
        leader, and centreline but keeps the title block.

        Returns:
            The list of removed annotation objects.
        """
        kept_named = self._registry.clear(keep)  # prunes names, views, and pins
        kept_ids = {id(o) for o in kept_named.values()}
        removed = [o for o in self.items if id(o) not in kept_ids]
        self.items = [o for o in self.items if id(o) in kept_ids]
        return removed

    def _record_build_issue(self, severity, code, message):
        """Record a lint issue discovered during construction (e.g. an
        annotation the layout had to drop). Surfaced by :meth:`lint` so a
        dropped feature is never silent."""
        self._registry.record_issue(LintIssue(severity=severity, code=code, message=message))

    def _reset_build_issues(self):
        """Clear build-time issues — called at the top of :func:`_auto_annotate`
        so re-annotation does not accumulate them."""
        self._registry.reset_issues()

    def _drop_build_issues(self, *codes):
        """Drop recorded build issues whose code is in *codes* (a fallback that
        restored tentatively-dropped annotations un-records their drop)."""
        self._registry.drop_issues(codes)

    # -- repair ---------------------------------------------------------------
    def repair(self, max_iter: int = 3):
        """Close the lint→repair loop: act on violations, don't only report them.

        After the greedy initial placement, re-place the dimensions behind the
        mechanically-clear violations and re-lint, bounded to *max_iter* passes:

        - ``dim_inside_part`` — the offset is on the wrong side; flip it once.
        - ``annotation_overlap`` — two labels collide; push one further out.

        Only engine-built dimensions (carrying ``_dw_spec``) are re-placeable;
        leaders, callouts and standards-judgement issues (e.g.
        ``missing_principal_dimension``) are left for the caller. Each side flip
        is attempted at most once and overlap pushes only move outward, so the
        loop terminates and a clean drawing is returned unchanged.

        A pass that would *net-increase* the issue count (e.g. an overlap push
        that shoves a label out of frame on a tight sheet) is rolled back and the
        loop stops, so :meth:`repair` never makes a drawing worse.

        Returns ``self`` for chaining.
        """
        return repair_drawing(self, max_iter)

    # -- output ---------------------------------------------------------------
    def lint(self):
        """Lint all annotations against all views; returns the list of issues.

        When :attr:`part` is set, also runs :func:`lint_feature_coverage`.
        Build-time drops recorded via :meth:`_record_build_issue` are included.
        """
        # Drawable area (page minus the standard margin), passed explicitly to
        # lint_drawing for bounds checks — draftwright owns linting now and no
        # longer relies on the helpers set_page module-global (ADR 0007).
        page_bbox = (_MARGIN, _MARGIN, self.page_w - _MARGIN, self.page_h - _MARGIN)
        view_shapes = [vis for vis, _ in self.views.values()]
        # Most annotations are at sheet scale, but a non-sheet-scale view (the
        # enlarged detail view, #42) tags its dims with `_dw_scale`. Lint each
        # scale group with its own drawing_scale so label-vs-measured is correct
        # per view. The common single-scale case is byte-identical to before.
        by_scale: dict = {}
        for ann in self.items:
            by_scale.setdefault(getattr(ann, "_dw_scale", self.scale), []).append(ann)
        if len(by_scale) <= 1:
            issues = lint_drawing(
                self.items,
                page_bbox=page_bbox,
                drawing_scale=self.scale,
                view_shapes=view_shapes,
                view_edge_cache=self._view_edge_cache,
            )
        else:
            issues = []
            for _scale, _anns in by_scale.items():
                issues += lint_drawing(
                    _anns,
                    page_bbox=page_bbox,
                    drawing_scale=_scale,
                    view_shapes=view_shapes,
                    view_edge_cache=self._view_edge_cache,
                )
        if self.part is not None:
            # Reuse the single feature inventory from the build (#244) when present,
            # so lint does not re-detect holes/patterns/turned-steps; fall back to
            # detecting when there is no analysis (a manually-built Drawing, or lint
            # called mid-build before _analysis is attached).
            a = self._analysis
            holes: list | None
            patterns: list | None
            bosses: list | None
            prof_kw: dict
            if a is not None:
                cyls = a.cyls
                holes, patterns, bosses = a.holes, a.patterns, a.bosses
                prof_kw = {"prof": a.prof}
            else:
                if self._cyl_cache is None:
                    self._cyl_cache = analyse_cylinders(self.part)
                cyls = self._cyl_cache
                holes = patterns = bosses = None
                prof_kw = {}
            issues += lint_feature_coverage(
                self.part,
                self.items,
                cyls=cyls,
                exclude=self._coverage.dropped_diams,
                assembly=self.assembly,
                holes=holes,
                bosses=bosses,
            )
            issues += lint_axial_coverage(
                self.part,
                self,
                assembly=self.assembly,
                **prof_kw,
            )
            issues += lint_location_coverage(
                self.part,
                self,
                cyls=cyls,
                assembly=self.assembly,
                holes=holes,
                patterns=patterns,
            )
            # Reverse direction (#487): a DECLARED feature with no matching geometry (a stale
            # phantom callout). Only for a caller-supplied model — detection can't over-declare.
            # _part_model is typed `object` (deliberately loose, #397); read features duck-typed.
            if self._model_declared and self._part_model is not None:
                features = getattr(self._part_model, "features", ())
                issues += lint_declaration_reconciliation(features, cyls)
        issues += list(self._build_issues)
        # Attach a ready-to-paste fix snippet where one is computable (#29).
        # str | None — None when no concrete repair can be inferred.
        for i in issues:
            i.suggestion = _suggest_fix(i, self)
        return issues

    def lint_summary(self) -> dict:
        """Aggregate :meth:`lint` into a JSON-friendly quality summary.

        Gives a non-interactive caller (a script, or an LLM via the API) a
        single signal to gate and optimise on without rendering the SVG:

        - ``passed`` — no error-severity issues;
        - ``score`` — coarse 0–1 quality heuristic (see ``_SCORE_*``);
        - ``errors`` / ``warnings`` / ``infos`` — counts by severity;
        - ``by_code`` — per-check counts;
        - ``geometry_issues`` — count of standards/geometry-correctness issues
          as opposed to pure layout (see ``_GEOMETRY_AWARE_CODES``);
        - ``issues`` — the full list, each as a plain dict.
        """
        issues = self.lint()
        errors = sum(1 for i in issues if i.severity == "error")
        warnings = sum(1 for i in issues if i.severity == "warning")
        infos = sum(1 for i in issues if i.severity == "info")
        by_code: dict[str, int] = {}
        for i in issues:
            by_code[i.code] = by_code.get(i.code, 0) + 1
        score = max(
            0.0,
            1.0 - errors * _SCORE_ERROR_PENALTY - warnings * _SCORE_WARNING_PENALTY,
        )
        return {
            "passed": errors == 0,
            "score": score,
            "errors": errors,
            "warnings": warnings,
            "infos": infos,
            "by_code": by_code,
            "geometry_issues": sum(1 for i in issues if i.code in _GEOMETRY_AWARE_CODES),
            "issues": [
                {
                    "severity": i.severity,
                    "code": i.code,
                    "message": i.message,
                    "location": i.location,
                    # Omit suggestion when None to keep the JSON non-breaking (#29).
                    **(
                        {"suggestion": s}
                        if (s := getattr(i, "suggestion", None)) is not None
                        else {}
                    ),
                }
                for i in issues
            ],
        }

    def export(self, out=None, *, svg=True, dxf=True) -> tuple[str | None, str | None]:
        """Lint, then write the requested vector formats. Returns
        ``(svg_path, dxf_path)`` — each is ``None`` when that format is skipped.
        Both default on (the unchanged one-shot behaviour; the CLI's ``--format``
        selector is the only caller that turns one off)."""
        self.finalize()  # #426: drain any recorded intents before export (no-op if none)
        out = out if out is not None else self.out
        for _ext in (".svg", ".dxf"):
            if out.endswith(_ext):
                out = out[: -len(_ext)]
                break

        issues = self.lint()
        if issues:
            _log.warning("Lint issues:")
            for iss in issues:
                _log.warning("  [%s] %s: %s", iss.severity, iss.code, iss.message)
        else:
            _log.info("Lint: OK")

        blk = Color(0, 0, 0)
        grey = Color(0.5, 0.5, 0.5)
        blue = Color(0, 0.2, 0.7)

        svg_path = None
        if svg:
            svg_exp = ExportSVG(margin=10)
            svg_exp.add_layer("part", line_color=blk, line_weight=0.5)
            svg_exp.add_layer(
                "hidden", line_color=grey, line_weight=0.25, line_type=LineType.HIDDEN
            )
            svg_exp.add_layer("dims", line_color=blue, fill_color=blue, line_weight=0.05)
            self._add_shapes(svg_exp)
            svg_path = out + ".svg"
            svg_exp.write(svg_path)
            fix_svg_page_size(svg_path, self.page_w, self.page_h)
            n_arcs = sanitize_svg_arcs(svg_path)
            if n_arcs:
                _log.info(
                    "Rewrote %d degenerate (near-zero-radius) arc(s) as line segments", n_arcs
                )
            link_rect = getattr(self, "_draftwright_link_rect", None)
            if link_rect is not None:
                add_svg_hyperlink(svg_path, link_rect)
            add_svg_metadata(svg_path)
            _log.info("SVG → %s", svg_path)

        dxf_path = None
        if dxf:
            dxf_exp = ExportDXF()
            dxf_exp.add_layer("part", line_weight=0.5)
            dxf_exp.add_layer("hidden", line_weight=0.25)
            dxf_exp.add_layer("dims", line_weight=0.05)
            self._add_shapes(dxf_exp)
            set_dxf_metadata(dxf_exp)
            dxf_path = out + ".dxf"
            dxf_exp.write(dxf_path)
            _log.info("DXF → %s", dxf_path)

        self.svg_path = svg_path
        self.dxf_path = dxf_path
        return svg_path, dxf_path

    def export_pdf(self, out=None) -> str:
        """Write a PDF rendered from the SVG (via ``svglib`` + ``reportlab``, both
        core dependencies — pure Python, no native cairo, so PDF works on every
        platform).  Calls :meth:`export` first if the SVG hasn't been written yet.
        Returns the PDF path.

        The PDF carries a 'generated by draftwright' Creator metadata field and,
        over the title-block URL row, a clickable hyperlink to the project (a
        real PDF link annotation — the SVG ``<a>`` element is not understood by
        the PDF renderer, so it is added here via reportlab)."""
        try:
            import reportlab  # noqa: F401  (import-guard; the real work is in _render_pdf)
            import svglib  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "PDF export requires svglib + reportlab (core dependencies); reinstall draftwright"
            ) from exc

        svg_path = getattr(self, "svg_path", None)
        if svg_path is None:
            svg_path, _ = self.export(out=out)
        assert svg_path is not None  # export() writes the SVG by default
        pdf_path = svg_path[:-4] + ".pdf" if svg_path.endswith(".svg") else svg_path + ".pdf"
        _render_pdf(svg_path, pdf_path, getattr(self, "_draftwright_link_rect", None))
        _log.info("PDF → %s", pdf_path)
        return pdf_path

    def _add_shapes(self, exporter):
        """Add every view layer and annotation to *exporter* with error context."""
        for name, (vis, hid) in self.views.items():
            _export_shape(exporter, vis, "part", f"view {name!r}")
            if hid:
                _export_shape(exporter, hid, "hidden", f"view {name!r}")
        for ann in self.items:
            label = getattr(ann, "label", "") or type(ann).__name__
            _export_shape(exporter, ann, "dims", f"annotation {label!r}")


# 1D strip placement now lives in draftwright.layout (ADR 0003 phase 1, #79).
# These aliases keep the existing axis-specific callers and their tests working
# while the primitive is axis-neutral; later phases route through LayoutSolver.


# A view centre must move by more than this (mm) for the measure-and-repack
# pass to re-assemble.  Below it, the estimate already matched the measured
# footprint and pass 1 stands (the common, non-ballooned case).
