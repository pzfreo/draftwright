"""The Drawing result object + table builder (#138 / ADR 0005, P6).

`Drawing` is the composable build result: it owns the render list and view
map and delegates identity to the registry, coverage to lint, and exposes
`.lint()/.add()/.place_dim()/.repair()/.export*()`. Sits below the builder
(which constructs it) — imports only the stage modules + `_core`, never
`builder`/`make_drawing`.
"""

from __future__ import annotations

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
    Leader,
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

    def _is_pattern_callout(self, name) -> bool:
        """Is *name* a placed pattern (grouped ``n× ⌀``) callout?"""
        return self._coverage.is_pattern_callout(name)

    def _is_hole_patterned(self, hole) -> bool:
        """Is *hole* already documented by a placed pattern callout?"""
        return self._coverage.is_hole_patterned(hole)

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

    def place_dim(self, p1, p2, side, view, draft, *, name=None, slot=8.0, **kwargs):
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
            **kwargs: forwarded to ``Dimension`` (e.g. ``label=``, ``tolerance=``).

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
            coord = strip.allocate(slot)
            if coord is not None:
                ax = 0 if side in ("left", "right") else 1
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
        return self.add(_dim(p1, p2, side, max(dist, 4.0), draft, **kwargs), name)

    # -- annotations ----------------------------------------------------------
    def add(self, obj, name=None, view=None):
        """Register an annotation so lint and export include it; returns ``obj``.

        Re-using an existing ``name`` replaces the previously added object (it is
        dropped from :attr:`items`), so a name always maps to one object.

        ``view`` records which orthographic view ("front"/"plan"/"side") owns
        this annotation, so the layout can compose each view with its own
        annotations as a single footprint block (#121).  Pass ``None`` for
        drawing-level marks (title block, iso/section notes) that belong to no
        single view.
        """
        displaced = self._registry.named(name) if name is not None else None
        if displaced is not None:
            self.items.remove(displaced)
        annotate(obj, name)
        self.items.append(obj)
        # The registry records name -> obj and the owning view, and drops any pin
        # the replaced name carried — a replacement is a fresh object (#89) — and
        # clears a stale ownership tag when re-added view-less (#121).
        self._registry.add(obj, name, view)
        return obj

    def remove(self, name):
        """Remove a previously named annotation. Raises ``KeyError`` if absent."""
        obj = self._registry.remove(name)  # forgets object, view, and pin (#89)
        if obj is None:
            raise KeyError(f"no annotation named {name!r}")
        self.items.remove(obj)
        return obj

    def annotations(self) -> dict:
        """Return ``{name: type_name}`` for every *named* annotation (#27).

        Lets a script introspect what is already on the drawing before adding
        more — e.g. ``if "dim_width" not in dwg.annotations()`` — so it can do
        incremental edits without risking a silent name-collision replace.
        Unnamed annotations are omitted; iterate :attr:`items` for those.
        """
        return self._registry.annotations()

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
        margin, ph = a.margin, a.PAGE_H

        # Stack the balloon ring *beyond* the annotations already placed around
        # the plan view, not on top of them (#121).
        za = a.pv_zones
        bot_dim = za.below.depth_used if za and za.below else 0.0
        left_dim = za.left.depth_used if za and za.left else 0.0
        right_dim = za.right.depth_used if za and za.right else 0.0
        # The TOP band is the one that goes stale: the hole-table escalation
        # deletes the X-location dims but never rewinds the above-strip cursor, so
        # za.above.depth_used keeps their high-water mark (~240 mm of phantom
        # corridor on CTC-02) and the top ring floats ~150 mm over empty space
        # (#125). Top balloons vary in X at a fixed Y, so they must clear the
        # DIMENSIONS spanning the plan's width above it — measure the real depth
        # of those (pitch dims dim_* AND PMI bore dims pmi_*). Construction
        # centrelines (bc_*) are crossable, not obstructions, so they are
        # excluded — their bolt-circle bbox would otherwise re-inflate the band.
        # Left/right/bottom are NOT stale: the deleted X-location dims only ever
        # allocated into the above strip (dim_locy tiers above the *side* view,
        # the width dim below is never removed), so those keep their correct
        # shallow strip depth.
        top_dim = 0.0
        for nm, obj in self._named.items():
            if not nm.startswith(("dim_", "pmi_")) or self._anno_view.get(nm) != view:
                continue
            try:
                ob = obj.bounding_box()
            except Exception:  # noqa: BLE001 — a mark with no bbox can't obstruct
                continue
            if ob.max.X > pl and ob.min.X < pr and ob.max.Y > pt:
                top_dim = max(top_dim, ob.max.Y - pt)

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
        self._place_band(
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
        self._place_band(
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
        self._place_band(
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
        self._place_band(
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

    def _place_band(self, view, members, axis, line, lo, hi, gap, fs, r):
        """Spread *members* (``(tag, j, hole, cx, cy)``) along one reserved band
        with the strip solver, then render a leadered balloon for each (#111).

        *axis* is the band's free axis (``"y"`` for the left/right bands, ``"x"``
        for the top); *line* is the fixed coordinate of the other axis.  Overflow
        beyond ``[lo, hi]`` drops the tail rather than running balloons off-page.
        """
        if not members:
            return
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
        self.add(balloon, f"balloon_{view}_{tag}_{j}", view=view)

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
            if self._cyl_cache is None:
                self._cyl_cache = analyse_cylinders(self.part)
            issues += lint_feature_coverage(
                self.part,
                self.items,
                cyls=self._cyl_cache,
                exclude=self._coverage.dropped_diams,
                assembly=self.assembly,
            )
            issues += lint_axial_coverage(
                self.part,
                self,
                assembly=self.assembly,
            )
            issues += lint_location_coverage(
                self.part,
                self,
                cyls=self._cyl_cache,
                assembly=self.assembly,
            )
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

    def export(self, out=None) -> tuple[str, str]:
        """Lint, then write SVG and DXF. Returns ``(svg_path, dxf_path)``."""
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

        svg = ExportSVG(margin=10)
        svg.add_layer("part", line_color=blk, line_weight=0.5)
        svg.add_layer("hidden", line_color=grey, line_weight=0.25, line_type=LineType.HIDDEN)
        svg.add_layer("dims", line_color=blue, fill_color=blue, line_weight=0.05)
        self._add_shapes(svg)
        svg_path = out + ".svg"
        svg.write(svg_path)
        fix_svg_page_size(svg_path, self.page_w, self.page_h)
        n_arcs = sanitize_svg_arcs(svg_path)
        if n_arcs:
            _log.info("Rewrote %d degenerate (near-zero-radius) arc(s) as line segments", n_arcs)
        link_rect = getattr(self, "_draftwright_link_rect", None)
        if link_rect is not None:
            add_svg_hyperlink(svg_path, link_rect)
        add_svg_metadata(svg_path)
        _log.info("SVG → %s", svg_path)

        dxf = ExportDXF()
        dxf.add_layer("part", line_weight=0.5)
        dxf.add_layer("hidden", line_weight=0.25)
        dxf.add_layer("dims", line_weight=0.05)
        self._add_shapes(dxf)
        set_dxf_metadata(dxf)
        dxf_path = out + ".dxf"
        dxf.write(dxf_path)
        _log.info("DXF → %s", dxf_path)

        self.svg_path = svg_path
        self.dxf_path = dxf_path
        return svg_path, dxf_path

    def export_pdf(self, out=None) -> str:
        """Write a PDF rendered from the SVG.  Requires ``cairosvg`` (install with
        ``pip install draftwright[pdf]``).  Calls :meth:`export` first if the SVG
        hasn't been written yet.  Returns the PDF path.

        The PDF carries a 'generated by draftwright' Creator metadata field and,
        over the title-block URL row, a clickable hyperlink to the project (a
        real PDF link annotation — the SVG ``<a>`` element is not understood by
        the PDF renderer, so it is added here via cairo)."""
        try:
            import cairosvg  # noqa: F401  (import-guard; the real work is in _render_pdf)
        except ImportError as exc:
            raise ImportError(
                "PDF export requires cairosvg. Install it with:  pip install draftwright[pdf]"
            ) from exc

        svg_path: str
        if not hasattr(self, "svg_path") or self.svg_path is None:
            svg_path, _ = self.export(out=out)
        else:
            svg_path = self.svg_path
        pdf_path = svg_path[:-4] + ".pdf" if svg_path.endswith(".svg") else svg_path + ".pdf"
        _render_pdf(svg_path, pdf_path, self.page_h, getattr(self, "_draftwright_link_rect", None))
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
