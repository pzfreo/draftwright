"""The Drawing result object (#138 / ADR 1 (was 0005), P6).

`Drawing` is the composable build result: it owns the render list and view
map and delegates identity to the registry, coverage to lint, and exposes
`.lint()/.add()/.dimension()/.repair()/.export*()`. Sits below the builder
(which constructs it) — imports only the stage modules + `_core`, never
`builder`/`make_drawing`.
"""

from __future__ import annotations

import contextlib
import math
import os
import sys
import warnings
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from quiddity import RecognitionResult
    from quiddity.evidence import RecognitionEvidence

    from draftwright.recognition_ownership import RecognitionOwnership

from draftwright.pdf_text import pdf_text_runs
from draftwright.progress import observed_stage

# PEP 702 @deprecated. A `sys.version_info` guard (not try/except) so the type checker,
# which targets the 3.10 floor, resolves the backport branch instead of `warnings.deprecated`
# (only in 3.13+ typeshed).
if sys.version_info >= (3, 13):
    from warnings import deprecated
else:
    from typing_extensions import deprecated


from draftwright._core import (
    Analysis,
    _dim,
    _fmt,
    _font_safe_text,
    _frame_margins,
    _tol_suffix,
    place_annotation,
)
from draftwright.annotations._common import (
    carve_free_position,
)
from draftwright.auxiliary_layout import fit_auxiliary_box
from draftwright.drawing_diagnostics import (
    _GEOMETRY_AWARE_CODES as _GEOMETRY_AWARE_CODES,
)
from draftwright.drawing_diagnostics import (
    DiagnosticOperations,
)
from draftwright.drawing_diagnostics import (
    lint_snapshot as lint_snapshot,
)
from draftwright.drawing_edits import EditOperations
from draftwright.drawing_export import (
    add_shapes as add_export_shapes,
)
from draftwright.drawing_export import (
    export_drawing,
)
from draftwright.drawing_export import (
    preview_annotation as preview_export_annotation,
)
from draftwright.drawing_export import (
    write_dxf as write_drawing_dxf,
)
from draftwright.drawing_export import (
    write_svg as write_drawing_svg,
)
from draftwright.drawing_state import _MATERIAL_MESH_UNSET, BuildState
from draftwright.drawing_tables import (
    DrawingTableState,
    _ir_hole_groups,
)
from draftwright.drawing_tables import (
    _hole_spec_groups as drawing_hole_spec_groups,
)
from draftwright.drawing_tables import (
    add_balloons as drawing_add_balloons,
)
from draftwright.drawing_tables import (
    add_hole_table as drawing_add_hole_table,
)
from draftwright.drawing_tables import (
    add_table as drawing_add_table,
)
from draftwright.drawing_tables import (
    note as drawing_note,
)
from draftwright.intent_drain import IntentDrainState, drain_intents
from draftwright.intent_routing import _IntentRouting, classify_intents
from draftwright.intents import Intent
from draftwright.linting import (
    CoverageState,
    LintIssue,
)
from draftwright.linting.issues import _current_issue_aggregation
from draftwright.projection import (
    part_material_mesh,
    project_view_geometry,
    view_material_field,
)
from draftwright.registry import AnnotationRegistry
from draftwright.repair import repair_drawing
from draftwright.view_plan import VIEW_AXES


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


# Machined-feature LEADER callouts (#148): these features use semantic leader callouts. Most
# expose no spanned linear parameter; paired-ramp's run is explicitly part of its compound
# leader convention. The reconstruction therefore can't route them through dimension().
# callout(f) records a per-feature
# intent that the matching per-kind finalize stage renders through the kind's auto-pass
# renderer, restricted to that feature (only=), at the canonical _PASS_SEQUENCE slot. Each
# name is BOTH the feature.kind and the stage/sequence key. Plate is deliberately EXCLUDED —
# it IS a spanned dimension (corridor-registered + drained, not a direct leader), so it
# reconstructs through dimension(f, "length", role="thickness"), not callout().
_MACHINED_CALLOUT_KINDS = (
    "chamfer",
    "circular_blind_step",
    "circular_channel",
    "hex_pocket",
    "fillet",
    "blend",
    "paired_ramp_step",
    "flat",
    "pocket",
    "rectangular_blind_slot",
    "round_bottom_blind_slot",
    "oriented_slot",
    "pad",
    "groove",
)


#: Size scalars appended to a feature key when present, in this order. Position alone is not
#: identity: coincident holes with different bores need distinct ledger keys. Rounded, so
#: float noise from a rebuild does not change the key.
_KEY_SCALARS = ("diameter", "depth", "width", "length", "radius")


def feature_key(f) -> str | None:
    """A stable, plain-data identity for a feature in the audit ledger (#996).

    ``kind@(x,y,z)/axis`` plus whichever of :data:`_KEY_SCALARS` the feature carries. The type
    name alone made two holes indistinguishable, so a diff of two builds could not say which
    one lost a measurement, or whether a suppression moved between instances.
    Derived from the geometry, so it survives a rebuild that reorders the feature list — list
    position would not.

    **Its limit, stated rather than implied:** two features of one kind sharing an origin,
    an axis *and* every scalar above are indistinguishable here. They are the same measurement
    to the compiler, so nothing in the ledger could separate them — but a caller diffing builds
    should know the key is a description, not a handle.
    """
    if f is None:
        return None
    kind = getattr(f, "kind", None) or type(f).__name__
    frame = getattr(f, "frame", None)
    if frame is None:
        return kind
    origin = getattr(frame, "origin", None)
    axis = getattr(frame, "axis", "")
    if origin is None:
        return f"{kind}/{axis}" if axis else kind

    def clean(value) -> float:
        rounded = round(float(value), 3)
        return 0.0 if rounded == 0 else rounded

    x, y, z = (clean(v) for v in (origin[0], origin[1], origin[2]))
    sizes = [
        f"{name}={clean(v):.3f}"
        for name in _KEY_SCALARS
        if isinstance(v := getattr(f, name, None), (int, float))
    ]
    tail = ("[" + ",".join(sizes) + "]") if sizes else ""
    return f"{kind}@({x:.3f},{y:.3f},{z:.3f})/{axis}{tail}"


class ViewNotPlanned(KeyError):
    """A projection was requested in a view this drawing does not have.

    Subclasses :class:`KeyError` so existing handlers keep working — the point is not a new
    control-flow contract but a named, inspectable one: `view` is what was asked for and
    `planned` is what the sheet actually carries, so a caller can report the miss instead of
    re-deriving it from a message (ADR 2 (was 0018 §6)).
    """

    def __init__(self, view: str, planned: tuple[str, ...] = ()):
        self.view = view
        self.planned = tuple(planned)
        super().__init__(f"view {view!r} is not on this sheet; planned views are {self.planned}")

    def __str__(self) -> str:
        return self.args[0] if self.args else ""


def _analysis_with_iso_centre(a: Analysis, x: float, y: float) -> Analysis:
    """Use the projected ISO centre for one drain without replacing build state."""
    if abs(x - a.ISO_X) <= 1e-6 and abs(y - a.ISO_Y) <= 1e-6:
        return a
    return replace(a, ISO_X=x, ISO_Y=y)


class Drawing:
    """A composable technical drawing — the editable form of :func:`make_drawing`.

    A ``Drawing`` holds the projected views, the annotation list, and per-view
    coordinate helpers. :func:`build_drawing` returns one pre-populated with the
    automatically selected principal/pictorial views and dimensions; you then add or remove
    annotations, add section/auxiliary views, and finally :meth:`export`.

    Attributes:
        scale: drawing scale factor (e.g. ``2.0`` for 2:1).
        scale_decision: JSON-friendly resolution of an automatic or explicit scale request,
            including the requested/effective scales and any required placement blockers.
        annotation_scheme_decision: JSON-friendly corridor comparison and a versioned,
            observational profile recommendation from pre-render demand. With
            ``annotation_layout="compare"`` it also records verified layout trials and the
            selected result plus provisional safety evidence; ``influenced_layout`` says
            whether a candidate won. Provisional evidence is not an admission gate.
        view_decision: JSON-friendly resolution of automatic principal-view selection.
            ``chosen`` is the final principal set and ``attempts`` records a reduced candidate
            and why it was accepted or rejected.
        arrangement_decision: JSON-friendly record of how the sheet arrangement was resolved
            (#1130) — ``chosen`` names the arrangement the sheet was composed under, and
            ``attempts`` lists each one built, in order, with the required placement blockers
            that rejected it. One entry means one compile.
        section_decision: JSON-friendly record of the section A-A outcome (#1190) —
            ``status`` is ``"placed"``, ``"skipped"``, ``"not_warranted"``, or
            ``"not_evaluated"`` when the section pass never ran (``auto_dims=False``),
            with a stable ``reason`` code and human-readable ``detail`` when skipped.
        detail_decisions: JSON-friendly outcomes for requested detail views, including
            model-space crop bounds, physical support evidence, resolved scale, and a
            named refusal reason. Observational only; requirement lint remains authoritative.
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
        reproducible: default for :meth:`export`'s ``reproducible=`` — when true, two
            exports of this drawing are byte-identical. ``True`` by default; pass
            ``False`` to trade that for export speed on a part-heavy sheet (see
            :func:`draftwright.export._elements`).

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
        working_part=None,
        cyls=None,
        assembly=None,
        reproducible=True,
    ):
        self.scale = scale
        # Public, JSON-friendly record of how the requested drawing scale was resolved
        # (#1146). The build wrapper replaces this automatic default for explicit policies.
        self.scale_decision = {
            "policy": "automatic",
            "requested_scale": None,
            "effective_scale": scale,
            "status": "automatic",
            "blockers": (),
            "attempted_scales": (),
            "attempts": (),
        }
        # The builder replaces this after analysis; the selector adds trials and a
        # decision when the caller requests the best verified layout.
        self.annotation_scheme_decision: dict[str, object] = {
            "status": "not_evaluated",
            "influenced_layout": False,
            "scale": scale,
            "unplanned_count": 0,
            "corridors": (),
            "under_reserved": 0,
            "over_reserved": 0,
        }
        # The builder replaces this neutral value after the requested/automatic view policy
        # has settled.  Always present so callers never have to infer whether the repeated
        # projection was considered from logs or from the final view count (#1262).
        self.view_decision: dict[str, object] = {
            "policy": "not_evaluated",
            "status": "not_evaluated",
            "chosen": (),
            "attempts": (),
        }
        # Public, JSON-friendly record of how the sheet ARRANGEMENT was resolved — ADR 2 (was 0018)
        # §5's fourth dimension and §6's "infeasibility is a first-class result" (#1130).
        # Always present for the same reason as `section_decision`: a caller must not have to
        # infer from a log line whether an alternative was tried and rejected. `attempts` is
        # also the honest compile count, since proving an alternative preserves every
        # requirement costs a real build (ADR 2 (was 0014 Amdt 3) — measure, do not predict).
        self.arrangement_decision = {
            "chosen": "columns",
            "attempts": ({"arrangement": "columns", "status": "chosen", "blockers": ()},),
        }
        # Public, JSON-friendly record of what happened to section A-A (#1190). Always
        # present, so a caller never has to infer the outcome from a log line that one
        # code path emits and another does not — which is exactly what happened before:
        # the title-block skip logged at INFO and the no-room skip at WARNING, so the
        # same omission was visible on one part and silent on another.
        # Starts NEUTRAL. The section pass only runs under `_auto_annotate`, which the
        # builder gates on `auto_dims`, so a `build_drawing(part, auto_dims=False)` never
        # evaluates whether a section is warranted. Claiming "no counterbore" there would
        # be a wrong answer about the geometry — worse than no answer, since the whole
        # point of this field is that a caller trusts it instead of reading logs.
        self.section_decision = {
            "status": "not_evaluated",
            "reason": None,
            "detail": "the section pass has not run",
        }
        self.detail_decisions: list[dict[str, object]] = []
        self.part = part
        self._working_part = part if working_part is None else working_part
        self._cyl_cache = cyls
        # None → the coverage lint auto-detects a multi-solid part as an
        # assembly; True/False forces assembly/strict severity (#69).
        self.assembly = assembly
        # Default for `export(reproducible=…)`: settle element order and the
        # metadata the exporters take from the clock, so two runs write the same
        # bytes. Off by default because the ordering costs about a third of DXF
        # export time again; a caller who wants to diff or checksum its output
        # turns it on, here or per export call.
        self.reproducible = reproducible
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
        self._iso_projection_scale: float | None = None
        # Annotation identity, ownership, pins, and build issues live in the
        # registry (#138 / ADR 1 (was 0005), Step 2), reached through its own surface
        # (`in reg` / `names()` / `issues`) — the `dwg._named` &c. compat aliases
        # that shadowed them were deleted at the ADR 1 (was 0005 §4) exit date (#720).
        self._registry = AnnotationRegistry()
        # Lint-side coverage signal (pattern callouts, patterned holes, dropped
        # callout diameters) lives in its own owner (#138 / ADR 1 (was 0005), Step 3);
        # its `dwg._pattern_callouts` &c. aliases went the same way (#720).
        self._coverage = CoverageState()
        # ADR 1 (was 0005 §2) (#639): the drawing's build context in ONE typed object —
        # analysis, part model, and the two geometry caches lint persists.
        # Constructed empty here; builder._assemble fills it at a single site.
        # The render passes never read it off the drawing (the empty
        # _DWG_PRIVATE_READ_ALLOW ratchet) — it serves the Drawing's OWN methods
        # (lint / finalize / repair / the edit verbs) and the test surface.
        self._build = BuildState()
        self.svg_path: str | None = None
        self.dxf_path: str | None = None
        # True when the caller SUPPLIED the model (build_drawing(model=…), ADR 4 (was 0011)) rather
        # than it being detected — gates the model-driven hole/pattern render membership so a
        # declared hole draws even where detection missed it, no-op for the detected path (#448).
        self._model_declared: bool = False
        self._document_member: bool = False
        self._document_source_annotation_ids: frozenset[int] = frozenset()
        # Deferred placement intents (#426 Phase 1). When _defer_intents is True the add
        # verbs record an Intent instead of placing; finalize() drains them (Phase 1
        # replays through the live helpers). Default off → the live path is unchanged.
        self._intents: list[Intent] = []
        self._defer_intents: bool = False

    @property
    def view_plan(self):
        """The resolved view plan (ADR 2 (was 0018)), or ``None`` before the views are created.

        READ ONLY, and there is no setter: a resolved plan that a caller can rebind is
        indistinguishable from an authored request, which is the confusion ADR 2 (was 0018 §1) exists to
        prevent. Editing means converting it into constraints explicitly and resolving again.
        """
        return self._build.view_plan

    @property
    def working_part(self):
        """The coordinate-coherent compiler/projection solid (read-only)."""
        return self._working_part

    @property
    def iso_projection_scale(self) -> float | None:
        """The scale of the final projected isometric view, if one was projected."""
        return self._iso_projection_scale

    def set_iso_projection_scale(self, scale: float) -> None:
        """Record an isometric projection or reprojection from the projection stage."""
        self._iso_projection_scale = float(scale)

    @property
    def recognition_frame(self):
        """The provider caller-to-working frame, or ``None`` outside a framed build."""
        return getattr(self._build.analysis, "recognition_frame", None)

    @property
    def leader_region(self) -> str:
        """The resolved feature-leader region policy for this drawing."""
        return getattr(self._build.analysis, "leader_region", "auto")

    @property
    def pmi_mode(self) -> str:
        """The imported PMI presentation policy resolved for this drawing."""
        return getattr(self._build.analysis, "pmi_mode", "off")

    @property
    def general_tolerance_source(self):
        """The document default attached to the title-block tolerance, if any."""
        return self._build.general_tolerance_source

    @property
    def default_surface_finish_source(self):
        """The document-wide finish attached to sheet furniture, if any."""
        return self._build.default_surface_finish_source

    @property
    def document_member(self) -> bool:
        """Whether this drawing belongs to an imported document."""
        return self._document_member

    @property
    def document_source_annotation_ids(self) -> frozenset[int]:
        """Source PMI identifiers already owned by the imported document."""
        return self._document_source_annotation_ids

    def attach_document_context(self, member: bool, source_annotation_ids) -> None:
        """Attach imported-document policy once, before annotation passes run."""
        self._document_member = bool(member)
        self._document_source_annotation_ids = frozenset(source_annotation_ids)

    @property
    def recognition_frame_decision(self) -> dict[str, object]:
        """A copy of the explicit framed/raw/refusal selection outcome."""
        decision = getattr(self._build.analysis, "recognition_frame_decision", None)
        return (
            dict(decision)
            if decision is not None
            else {"status": "not_evaluated", "gauge": None, "refusal_reason": None}
        )

    @property
    def drawable_bounds(self) -> tuple[float, float, float, float]:
        """Physical margins as ``(left, bottom, right, top)`` page coordinates in mm.

        A frame uses this rectangle; view placement reserves additional clearance inside it.
        """
        return _frame_margins(self._analysis).bounds(self.page_w, self.page_h)

    @property
    def registry(self):
        """The AnnotationRegistry (identity/build-issue store) — the build handle the passes' PlacementContext references (#639)."""
        return self._registry

    @property
    def coverage(self):
        """The CoverageState — referenced by the run's PlacementContext (#639)."""
        return self._coverage

    # -- coverage state operations (used by the annotation passes) ------------
    def _is_scattered_hole_doc(self, name) -> bool:
        """Is *name* a placed scattered hole callout / location dim?"""
        return self._coverage.is_scattered_hole_doc(name)

    # -- views ----------------------------------------------------------------
    @deprecated(
        "Drawing.add_view() is deprecated (#817): view projection is engine plumbing. Custom "
        "section/auxiliary views come from the section verb; the raw projector is now private "
        "(_add_view). Removed in 0.5.0."
    )
    def add_view(self, name, shape, camera, up, position, *, look_at=None, scaled=False):
        """DEPRECATED (#817): the raw view projector is now private (:meth:`_add_view`)."""
        return self._add_view(name, shape, camera, up, position, look_at=look_at, scaled=scaled)

    def _add_view(
        self, name, shape, camera, up, position, *, look_at=None, scaled=False, bounds_cache=None
    ):
        """Project ``shape`` from ``camera`` and place it at ``position``.

        Args:
            name: view name (key in :attr:`views`); also used for coordinate lookups.
            shape: a build123d ``Shape`` to project. Given in world (unscaled)
                coordinates and scaled internally unless ``scaled=True``.
            camera: camera direction in **scaled** space.
            up: camera up direction in **scaled** space.
            look_at: viewport target in **scaled** space. Defaults to
                :attr:`look_at` (the scaled centroid). Compose custom cameras from
                :attr:`look_at` and :attr:`dist`.
            position: ``(x, y)`` page position for the view centre, in mm.
            scaled: set ``True`` if ``shape`` is already scaled by :attr:`scale`.

        Returns:
            The :class:`ViewCoordinates` for this view (also via :meth:`coords`),
            for mapping world points to page coordinates.
        """
        la = self.look_at if look_at is None else look_at
        placed, placed_hid, coords = project_view_geometry(
            self.scale,
            name,
            shape,
            camera,
            up,
            position,
            look_at=la,
            scaled=scaled,
            bounds_cache=bounds_cache,
        )
        self.views[name] = (placed, placed_hid)
        self._coords[name] = coords
        return self._coords[name]

    def coords(self, view):
        """Return the :class:`ViewCoordinates` for a named view."""
        return self._coords[view]

    @deprecated(
        "Drawing.set_view_coordinates() is deprecated (#817): view-coordinate plumbing is "
        "engine-internal; the mutator is now private (_set_view_coordinates). Removed in 0.5.0."
    )
    def set_view_coordinates(self, view, coords) -> None:
        """DEPRECATED (#817): now private (:meth:`_set_view_coordinates`)."""
        self._set_view_coordinates(view, coords)

    def _set_view_coordinates(self, view, coords) -> None:
        """Override a view's projected coordinates (a repositioned detail/section band, #307)."""
        self._coords[view] = coords

    @deprecated(
        "Drawing.drop_view_coordinates() is deprecated (#817): view-coordinate plumbing is "
        "engine-internal; the mutator is now private (_drop_view_coordinates). Removed in 0.5.0."
    )
    def drop_view_coordinates(self, view) -> None:
        """DEPRECATED (#817): now private (:meth:`_drop_view_coordinates`)."""
        self._drop_view_coordinates(view)

    def _drop_view_coordinates(self, view) -> None:
        """Remove a view's projected coordinates (a bailed detail/section, #307)."""
        self._coords.pop(view, None)

    def at(self, view, x, y, z):
        """Map a world point to a page point ``(px, py, 0)`` in ``view``.

        Raises :class:`ViewNotPlanned` when *view* is not on the sheet. A bare ``KeyError``
        from inside whichever render pass happened to ask first is not a usable answer to
        "this drawing does not have that view" — ADR 2 (was 0018 §6) wants an absent view to be a
        named result, because view-set selection makes asking for one the normal case rather
        than a bug (#1130).
        """
        coords = self._coords.get(view)
        if coords is None:
            raise ViewNotPlanned(view, tuple(self._coords))
        px, py = coords.pp(x, y, z)
        return (px, py, 0.0)

    def view_bounds(self, view):
        """Return ``(x_min, y_min, x_max, y_max)`` of the projected geometry in
        *view*, or ``None`` if the view is unknown (#28).

        The box is the tight bounding box of the placed silhouette — visible
        plus hidden lines — in page coordinates (mm from the sheet origin), the
        same space :meth:`at` returns. Use it to place free-form notes, leader
        elbows and the like just outside a view without guessing offsets::

            x0, y0, x1, y1 = dwg.view_bounds("front")
            dwg.note("SEE NOTE 1", (x1 + 5, (y0 + y1) / 2))
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
        - ``"front"`` / ``"rear"`` → Y-axis holes
        - ``"side"``  → X-axis holes

        Returns an empty list when no analysis is available or the view name
        is unrecognised.
        """
        a = self._analysis
        if a is None:
            return []

        _axis_for_view = {
            name: next(axis for axis in "xyz" if axis not in axes)
            for name, axes in VIEW_AXES.items()
        }
        target_axis = _axis_for_view.get(view)
        if target_axis is None:
            return []

        if view not in self._coords:
            return []

        model = self._part_model
        if model is None:
            return []

        result = []
        for _owner, spec, positions, count in _ir_hole_groups(model, target_axis):
            result.append(
                FeatureInfo(
                    type="hole",
                    page_pos=self._coords[view].pp(*positions[0]),
                    diameter=spec.diameter,
                    through=spec.through,
                    depth=None if spec.through else spec.depth,
                    count=count,
                )
            )
        return result

    def model(self):
        """The detected **PartModel** this drawing was built from (ADR 1 (was 0008) IR) — the
        read surface for semantic edits (#397, ADR 4 (was 0001 Amendment 1)).

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

    def recognition(self) -> RecognitionResult | None:
        """The ADR 3 (was 0017) recognition inventory used to build this drawing.

        This is the geometry-only evidence below the detected/declared :meth:`model` and
        drafting policy.  It is an experimental, read-only result.

        ``None`` for a bare ``Drawing`` that did not pass through :func:`build_drawing`, and
        for a **declared** build that has not yet been critiqued — that path recognises
        nothing (ADR 4 (was 0011) / #1022) and only builds an aggregate when something asks for
        physical critique.  So ``None`` here means "nothing has needed recognition yet", never
        "this part has no features".
        """

        return self._build.recognition

    def recognition_evidence(self) -> RecognitionEvidence | None:
        """The run-scoped provider evidence paired with :meth:`recognition`.

        This experimental, read-only view is available for raw automatic recognition and
        after the first physical critique of a declared drawing. It is ``None`` before that
        lazy critique, for a bare drawing, and for the framed path, which has not adopted
        the provider's framed-evidence contract. Draftwright never reruns recognition merely to fill
        this value. The returned evidence borrows exact faces from the source part, so callers
        must not mutate that part while using the evidence view.
        """

        return self._build.recognition_evidence

    def recognition_ownership(self) -> RecognitionOwnership | None:
        """Run-local accepted-occurrence ownership captured during detected conversion.

        This experimental, read-only ledger is available only when raw automatic recognition
        supplied :meth:`recognition_evidence`. It currently classifies unconditional one-to-one
        adapters; singleton/grouped/pattern holes, slots, and pockets; nested countersinks; and
        settled ownerless unsupported, deferred, and evidence-only policy. Remaining nested and
        classification-only families stay explicitly unclassified. It carries opaque provider
        references and therefore cannot be serialized or used as persistent feature identity.
        """

        return self._build.recognition_ownership

    def report(self) -> dict[str, object]:

        return self._diagnostics().report()

    def requirement_snapshot(self, *, include_lint=False):

        return self._diagnostics().requirement_snapshot(include_lint=include_lint)

    def write_report(self, path: str | os.PathLike[str]) -> str:

        return self._diagnostics().write_report(path)

    # --- build-context compat properties (#639): one BuildState, thin views.
    # _part_model and the two caches are GETTER-ONLY by design:
    # zero assignment sites exist in src/ or tests/, and a future wholesale
    # replacement must go through BuildState (attach_part_model / the caches'
    # in-place mutation) so it fails loudly instead of silently forking the
    # single-writer inventory the encapsulation guard pins. _analysis keeps a
    # setter for manual-construction flows (also inventoried by the guard).
    @property
    def _analysis(self):
        return self._build.analysis

    @_analysis.setter
    def _analysis(self, a) -> None:
        self._build.analysis = a

    @property
    def _part_model(self):
        return self._build.part_model

    @property
    def _view_edge_cache(self) -> dict:
        return self._build.view_edge_cache

    @property
    def _ann_box_cache(self) -> dict:
        return self._build.ann_box_cache

    def record_section_decision(self, status: str, *, reason=None, detail: str = "") -> None:
        """Record what happened to section A–A (#1190).

        A public verb rather than an attribute the render pass assigns, so the
        annotations layer stays off ``Drawing`` internals (ADR 1 (was 0005)) and every outcome
        lands in one shape. ``status`` is ``"placed"``, ``"skipped"`` or
        ``"not_warranted"``; ``reason`` is a stable code for the skipped case.
        """
        if status not in {"placed", "skipped", "not_warranted", "not_evaluated"}:
            raise ValueError(f"unknown section status {status!r}")
        self.section_decision = {"status": status, "reason": reason, "detail": detail}

    def material_fields(self) -> dict:
        """The per-view filled projected material of this drawing, keyed by ``id(shape)``.

        The ADR 2 (was 0014) leader routing and the ``leader_crosses_silhouette`` critique must
        agree on what counts as travelling through the part, so both read this ONE
        lowering rather than each deriving the answer from the projected outline. An empty
        dict means the part could not be meshed, which callers must read as "no material
        known" rather than "clear" — inventing clearance from a failed lowering is how a
        silent geometry failure becomes a confident wrong answer.

        The tessellation behind it is memoised (including its failure, so an unmeshable
        part is not retried on every lint), but the per-view fields are reconciled against
        the CURRENT views on each call, for two reasons:

        * This is first called mid-build, by the feature-leader stage — which runs before
          the section and detail stages. Fields built once would permanently omit every
          view added afterwards, and leaders in a detail view would silently never be
          checked for cutting.
        * Entries are identity-checked, like ``_view_edge_entries``' (#143), because a
          replaced view shape can be collected and its ``id`` reused — which would hand
          back another view's material.
        """
        cache = self._build.material_fields
        analysis = self._build.analysis
        part = getattr(analysis, "part", None) if analysis is not None else None
        if part is None:
            return {}
        mesh = self._build.material_mesh
        if mesh is _MATERIAL_MESH_UNSET:
            mesh = part_material_mesh(part, self.scale)
            self._build.material_mesh = mesh
        if mesh is None:
            return {}
        fields = {}
        for name, placed in self.views.items():
            if not placed or placed[0] is None:
                continue
            shape = placed[0]
            key = id(shape)
            hit = cache.get(key)
            if hit is None or hit[0] is not shape:
                hit = (
                    shape,
                    view_material_field(mesh, lambda point, view=name: self.at(view, *point)[:2]),
                )
                cache[key] = hit
            fields[key] = hit[1]
        # Evict entries for shapes no longer on the sheet. Each holds a strong reference
        # to its view shape, and `_fit_iso_view` re-projects (once per build, again on
        # finalize), so without this every superseded iso Compound is pinned for the
        # drawing's lifetime — the same reason `_lint` prunes `_ann_box_cache`.
        for stale in [key for key in cache if key not in fields]:
            del cache[stale]
        return fields

    @property
    def box_cache(self) -> dict:
        """The ONE annotation bounding-box memo for this build (#1138).

        Placement and lint both measure annotations, and an *optimal* ``bounding_box()``
        tessellates (~16 ms for a Leader), so anything measured on both paths is worth
        measuring once. Sharing one memo makes that hold by construction.

        Keep its measured scope in mind before attributing a build's cost to it: on a
        plate build it holds **two** entries, on a flange **three**, all leaders and
        their callouts. Dimensions are *not* among them and cannot be — ``strip_obstacles``
        decomposes anything exposing ``.segments`` instead of boxing it whole, and
        ``corridor_blockers`` skips ``Dimension``/``SafeDimension`` outright. The memo is
        worth tens of milliseconds, not a phase; the leader work in #1138 is what moved
        the number.

        Exposed publicly (not as ``_ann_box_cache``) because the annotations layer is
        duck-typed against ``dwg`` and, per ADR 1 (was 0005), reads no private Drawing state.
        Sharing the dict rather than adding a second memo also means ``lint()``'s
        existing liveness prune — which drops entries for objects no longer on the
        sheet — covers placement-seeded entries for free; a separate placement cache
        would have to re-implement that pruning, and a missed prune keeps OCC geometry
        alive for the drawing's lifetime.
        """
        return self._build.ann_box_cache

    @deprecated(
        "Drawing.attach_part_model() is deprecated (#817): build-state attach is engine "
        "plumbing; the mutator is now private (_attach_part_model). Removed in 0.5.0."
    )
    def attach_part_model(self, model) -> None:
        """DEPRECATED (#817): now private (:meth:`_attach_part_model`)."""
        self._attach_part_model(model)

    def _attach_part_model(self, model) -> None:
        """Attach the built PartModel so ``model()`` and feature edits see it. Lets the
        orchestrator hand the model back without an ``annotations/`` attribute write (#639)."""
        self._build.part_model = model

    def pending_title_block_box(self):
        """The title block's page-space footprint before it has been drawn, or ``None``.

        ``"title_block"`` sits near the end of ``_PASS_SEQUENCE``, so it is absent from
        ``iter_annotations`` while strips place — which is why a below/right strip ran
        into its region and only the paths asking ``_title_block_box`` directly ever
        avoided it (#481 did that for GD&T; #1593 was the same gap for dimensions).
        Its footprint is deterministic before it exists, so the builder measures it
        once and hands it over here rather than ``annotations/`` probing the drawing
        (ADR 1 (was 0005 §2): the drawing is not the state bus).
        """
        return self._build.pending_title_block_box

    def title_block_for(self, key, factory):
        """Return the build-owned title block for its page, fields and typography."""
        cache = self._build.title_block_cache
        if key not in cache:
            cache[key] = factory()
        return cache[key]

    def suppressions(self) -> list[dict]:
        """Every measurement the compiler considered and did not approve, and why.

        The **audit read** (#996). A finished drawing shows what was drawn; this shows what
        was *not*, separated into the two cases that mean opposite things:

        - ``authored`` — the script's own omission, under ADR 4 (was 0016)'s rule that an authored
          set means omission is suppression. Recoverable by adding a ``dimension(...)`` line.
        - otherwise — a **planner rule** decided it, and ``reason`` names which.

        ``conveyed_by`` distinguishes a measurement that was WITHHELD from one that was
        CONSOLIDATED (#1154): when it is set, the fact is still on the sheet, stated by the
        dimension it names. "This is not drawn" and "this is drawn over there" are different
        answers, and an audit that flattened them would read a de-duplication as a gap. It
        rides the same stable ``kind@(x,y,z)/axis`` key as ``feature``, so the two halves of
        one consolidation can be matched up without importing IR types. ``None`` wherever
        nothing takes the fact over — which is NOT the same question as ``authored``: an
        authored omission carries a ``conveyed_by`` whenever the author's set keeps the
        owner, since the author chooses which dimensions are drawn and not where the
        geometry states a fact.

        The second is the one worth auditing. A rule that fires where it should not produces
        a drawing that is silently under-defined and lints clean, which is how #997's square
        rule generated four separate issue reports without any of them naming the cause. An
        absent dimension is only defensible if something can say which rule removed it; this
        is that something.

        Returns plain dicts so a harness, a script or an LLM can diff two builds without
        importing IR types. ``feature`` is a **stable key**, not just the type name: a bare
        ``"HoleFeature"`` made two holes indistinguishable, so a diff could not say *which*
        one lost its location, or whether a suppression moved between instances. The key is
        ``kind@(x,y,z)/axis``, which survives a rebuild because it is
        derived from the geometry rather than from list position.
        """
        from draftwright.drawing_evidence import suppression_rows

        return suppression_rows(self._build.omissions, feature_key)

    def measurement_keys(self, name) -> list[dict]:
        """Which measurements the annotation *name* draws — possibly none (#1002).

        The mirror of :meth:`suppressions` and deliberately the SAME row shape —
        ``{"feature": <stable key>, "parameter_id": ...}`` — so a drawn measurement and a
        suppressed one are directly comparable. Without it the two halves of the audit could
        only be joined by matching an engine-assigned annotation name against a parameter id
        by substring, which attributed losses to unrelated suppressions.

        A **list**, because one annotation can draw several independently suppressible
        measurements — a compound hole callout renders bore diameter, depth and counterbore
        together (ADR 4 (was 0016) / #886). Empty means the renderer recorded nothing, **not** that
        the annotation measures nothing. Which renderers record it is enforced by the ratchet
        in `tests/test_audit_differential.py`; treat presence as exact and absence as unknown.

        These keys describe geometry and have the collision limits of ``feature_key``.
        For declaration-local ownership and meaning comparisons, capture
        :meth:`measurement_snapshot`; geometry descriptions alone do not prove correspondence.
        """
        from draftwright.drawing_evidence import measurement_keys

        return measurement_keys(self._registry, name, feature_key)

    def measurement_snapshot(self):
        """Capture named measurement claims for a declaration-local edit comparison.

        Use :func:`draftwright.audit.compare_measurements` to compare snapshots. Capture
        before mutating a drawing. Owner references stay local to this process; separately
        rebuilt declarations need an explicit correspondence, never a geometry-key guess.
        This read compiles the existing model and reads recorded claim text, without recognition.
        """
        from draftwright.drawing_evidence import measurement_snapshot

        model = self.model()
        if model is None:
            return measurement_snapshot(None, None, None, None)
        return measurement_snapshot(model, self.registry, self.annotations, self.scale)

    @property
    def solve_trace(self):
        """The opt-in solve-trace recorder threaded through this build (#736), or
        ``None`` (the default — tracing off). See ``build_drawing(trace=...)``."""
        return self._build.trace

    @deprecated(
        "Drawing.attach_solve_trace() is deprecated (#817): build-state attach is engine "
        "plumbing; the mutator is now private (_attach_solve_trace). Removed in 0.5.0."
    )
    def attach_solve_trace(self, trace) -> None:
        """DEPRECATED (#817): now private (:meth:`_attach_solve_trace`)."""
        self._attach_solve_trace(trace)

    def _attach_solve_trace(self, trace) -> None:
        """Attach the #736 :class:`~draftwright.annotations._common.SolveTrace` recorder
        so the annotate and finalize paths thread it onto their per-run
        ``PlacementContext`` (build state flows through a named method, #639)."""
        self._build.trace = trace

    @property
    def model_declared(self) -> bool:
        """Whether this drawing's model was **declared** by the caller (ADR 4 (was 0011)) rather than
        detected — the public read the annotation pass threads onto its PlacementContext (#639)."""
        return self._model_declared

    @deprecated(
        "Drawing.place_dim() is deprecated for normal editable scripts; use "
        "Drawing.dimension(feature, param, ..., pin=True) or "
        "Drawing.locate(feature, ..., pin=True) for feature-backed edits. "
        "place_dim() remains only as a raw page-coordinate escape hatch. "
        # ADR 4 permits this raw-coordinate escape hatch until full recompose is available.
        # Name both the prerequisite and target release so callers know its planned exit.
        "Removal gated on #707 (full recompose); target 0.6.0."
    )
    def place_dim(
        self,
        p1,
        p2,
        side,
        view,
        draft,
        *,
        name=None,
        slot=8.0,
        feature=None,
        **kwargs,
    ):
        """Deprecated low-level page-coordinate dimension escape hatch.

        Add a :class:`~build123d_drafting.helpers.Dimension` that stacks cleanly
        with the auto-generated dimensions by delegating to the same strip-allocation
        system (:class:`Strip`) that :func:`build_drawing` uses internally.

        Args:
            p1: first page-coordinate tuple ``(px, py, 0)`` — use :meth:`at` to
                convert world coordinates.
            p2: second page-coordinate tuple ``(px, py, 0)``.
            side: ``"above"``, ``"below"``, ``"left"``, or ``"right"``.
            view: ``"front"``, ``"plan"``, ``"side"``, or ``"rear"``.
            draft: the drawing's :attr:`draft` preset.
            name: optional annotation name for later :meth:`remove` / replace.
            slot: strip slot depth (mm); the perpendicular space reserved per dim.
            feature: optional source IR feature to attribute this dim to, so
                :meth:`drop` / :meth:`annotations_of` can find it (#398).
            **kwargs: forwarded to ``Dimension`` (e.g. ``label=``). ``tolerance=`` is
                folded into the label rather than forwarded — your own ``label=`` included —
                because helpers do ``rendered = label if label is not None else …``, so an
                explicit label DISCARDS a forwarded tolerance and a label is always
                present here (#1234).

        Deprecated for normal editable scripts: prefer :meth:`dimension` for
        feature-backed linear dimensions and :meth:`locate` for feature-backed
        location dimensions. Both support ``pin=True`` in deferred/finalize mode
        and can participate in the shared layout solve.

        Uses the single-position strip carve, not the ADR 2 (was 0009) collect-then-solve
        path the automatic placers use — fine for adding a dimension into free
        space, but it does not re-solve the strip or dedup against existing dims
        (#396). Prefer :meth:`dimension` for a feature-referenced edit.

        Falls back to a fixed ``slot`` offset when the strip is full or when no
        layout analysis is available (e.g. when ``auto_dims=False`` was not used
        with :func:`build_drawing`).

        The ``@deprecated`` (PEP 702) decorator both emits the runtime
        ``DeprecationWarning`` and lets type checkers/IDEs flag call sites statically (#817).
        """
        return self._place_dim(
            p1, p2, side, view, draft, name=name, slot=slot, feature=feature, **kwargs
        )

    def _place_dim(
        self,
        p1,
        p2,
        side,
        view,
        draft,
        *,
        name=None,
        slot=8.0,
        feature=None,
        measurement=None,
        **kwargs,
    ):
        """Raw page-coordinate dimension placement **primitive** (#817).

        Private: the public door for dimensions is :meth:`dimension`/:meth:`locate`
        (feature-backed, solve-aware). This single-position strip carve is the escape
        hatch behind the deprecated public :meth:`place_dim` shim and the internal
        :meth:`dimension` raw-span fallback. See :meth:`place_dim` for the argument and
        behaviour notes.
        """
        a = self._analysis
        _view_zones = {
            "front": "fv_zones",
            "plan": "pv_zones",
            "side": "sv_zones",
            "rear": "rv_zones",
        }
        strip = None
        if a is not None:
            zones = getattr(a, _view_zones.get(view, ""), None)
            if zones is not None:
                strip = getattr(zones, side, None)
        dist = slot
        if strip is not None:
            # Cursor-free tier placement (ADR 2 (was 0009), #150): find a free tier that clears
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
        # Compose the tolerance into either an explicit or generated label. Helpers render an
        # explicit label verbatim, so forwarding `tolerance=` would silently discard it.
        tolerance = kwargs.pop("tolerance", None)
        # An explicit `label=None` requests the automatic label too.
        if kwargs.get("label") is None:
            page_len = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
            kwargs["label"] = _fmt(page_len / self.scale)
        kwargs["label"] = _font_safe_text(f"{kwargs['label']}{_tol_suffix(tolerance, draft)}")
        return self._add(
            _dim(p1, p2, side, max(dist, 4.0), draft, **kwargs),
            name,
            view=view,
            feature=feature,
            measurement=measurement,
        )

    # -- annotations ----------------------------------------------------------
    def _add(self, obj, name=None, view=None, feature=None, measurement=None, *, cells=()):
        """Register an annotation so lint and export include it; returns ``obj``. The
        annotation-placement **primitive** (#817) — private, because the public door is the
        placement verbs (:meth:`callout`/:meth:`dimension`/:meth:`note`/:meth:`add_table`/…) and
        the render passes place through the ``PlacementContext`` seam (``ctx.place``, #639), never
        by reaching into the drawing.

        Re-using an existing ``name`` replaces the previously added object (dropped from
        :attr:`items`), so a name always maps to one object. ``view`` records the owning
        orthographic view so the layout composes each view + its annotations as one footprint
        block (#121); ``None`` for drawing-level marks. ``feature`` records the source IR feature
        (#398) so :meth:`drop` / :meth:`annotations_of` work by feature.
        """
        return place_annotation(
            self._registry,
            self.items,
            obj,
            name,
            view,
            feature,
            measurement,
            cells=cells,
        )

    @deprecated(
        "Drawing.add() is deprecated (#817): use the placement verbs (callout/dimension/note/"
        "add_table/…); free text is note(). The raw add primitive is now private (_add). "
        "Removed in 0.5.0."
    )
    def add(self, obj, name=None, view=None, feature=None):
        """DEPRECATED (#817): the raw placement primitive is now private (:meth:`_add`). Use the
        placement **verbs** — :meth:`callout`/:meth:`dimension`/:meth:`note`/:meth:`add_table`/
        :meth:`add_balloons` — which route through the solve; :meth:`note` is the door for free
        text. The public wrapper remains one release for compatibility.

        The ``@deprecated`` (PEP 702) decorator both emits the runtime ``DeprecationWarning`` and
        lets type checkers/IDEs flag call sites statically (#817)."""
        return self._add(obj, name, view, feature)

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
        :func:`finalize_drawing` afterwards (when available) to recompose the sheet.
        A measured schedule shared by other features requires editing its declared
        rows and rebuilding; partial removal refuses before changing any ink."""
        names = self._registry.names_for_feature(feature)
        for name in names:
            if any(
                cell.measurement.feature is not feature for cell in self._registry.cells_of(name)
            ):
                raise ValueError(
                    f"schedule {name!r} also measures other features; edit its declared "
                    "rows and rebuild, or remove the whole table explicitly by name"
                )
        survivors: list = []
        for name in names:
            for owner in getattr(self._registry.named(name), "source_features", ()):
                if owner != feature and not any(owner is existing for existing in survivors):
                    survivors.append(owner)
        for n in names:
            self.remove(n)
        if survivors:
            # A shared callout is indivisible ink. Re-emit only its other owners
            # through the same deferred callout path after removing that ink.
            if self._defer_intents:
                for owner in survivors:
                    self.callout(owner)
            else:
                with self.deferred():
                    for owner in survivors:
                        self.callout(owner)
        return names

    def _edit_ops(self) -> EditOperations:
        """Pass Drawing-owned mutable state explicitly to one edit operation."""
        return EditOperations(
            self,
            analysis=self._analysis,
            model=self._part_model,
            build=self._build,
            registry=self._registry,
            coverage=self._coverage,
            intents=self._intents,
            defer_intents=self._defer_intents,
            document_member=self._document_member,
            document_source_annotation_ids=self._document_source_annotation_ids,
            record_build_issue=self._record_build_issue,
            place_dim=self._place_dim,
            machined_callout_kinds=_MACHINED_CALLOUT_KINDS,
        )

    @staticmethod
    def _derive_span(feature, param):
        return EditOperations._derive_span(feature, param)

    def _resolve_dimension_span(self, feature, param, *, role=None, view=None):
        return self._edit_ops()._resolve_dimension_span(feature, param, role=role, view=view)

    def _resolve_dimension_side(self, feature, param, view, p1, p2, side):
        return self._edit_ops()._resolve_dimension_side(feature, param, view, p1, p2, side)

    def _angular_dimension_plan(self, feature, options):
        return self._edit_ops()._angular_dimension_plan(feature, options)

    def _queue_dimension_intent(self, it, a, *, ctx, used_names=None) -> bool:
        return self._edit_ops()._queue_dimension_intent(it, a, ctx=ctx, used_names=used_names)

    def dimension(
        self,
        feature,
        param,
        *,
        role=None,
        side=None,
        view=None,
        name=None,
        pin=False,
        priority=0.0,
        **kwargs,
    ):
        return self._edit_ops().dimension(
            feature,
            param,
            role=role,
            side=side,
            view=view,
            name=name,
            pin=pin,
            priority=priority,
            **kwargs,
        )

    def callout(self, feature, *, view=None, name=None) -> str:
        return self._edit_ops().callout(feature, view=view, name=name)

    def overall_height(self) -> list[str]:
        return self._edit_ops().overall_height()

    def furniture(self, feature, *, view=None) -> list[str]:
        return self._edit_ops().furniture(feature, view=view)

    def rotational(self, feature) -> list[str]:
        return self._edit_ops().rotational(feature)

    def section(self) -> list[str]:
        return self._edit_ops().section()

    def locate(self, feature, *, axes=None, pin=False) -> list[str]:
        return self._edit_ops().locate(feature, axes=axes, pin=pin)

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

    def _classify_intents(self, model, a, routable) -> _IntentRouting:
        """Classify recorded edits for the shared placement stage order."""
        return classify_intents(
            self._intents,
            model,
            a,
            routable,
            self._user_dim_uses_corridor,
            _MACHINED_CALLOUT_KINDS,
        )

    def _user_dim_uses_corridor(self, it, routable, already_routed) -> bool:
        """Does a user-authored generic dimension intent join the shared corridor? The
        promoted predicate of :meth:`_classify_intents`' ``user_dim_ids`` set: a pin/priority
        dimension with a resolvable span and a corridor-side, not already claimed by a
        specialized route (``already_routed`` = ``len_ids | slot_ids | height_ladder_ids |
        step_position_ids``)."""
        if routable and it.kind == "dimension" and getattr(it.feature, "kind", None) == "angle":
            self._angular_dimension_plan(it.feature, it.kwargs)
            return True
        if (
            not routable
            or it.kind != "dimension"
            or id(it) in already_routed
            or not (it.kwargs.get("pin") or it.kwargs.get("priority"))
            or (
                it.kwargs.get("side") is not None
                and it.kwargs.get("side") not in ("above", "below", "left", "right")
            )
        ):
            return False
        try:
            self._resolve_dimension_span(
                it.feature,
                it.kwargs["param"],
                role=it.kwargs.get("role"),
                view=it.kwargs.get("view"),
            )
        except ValueError:
            return False
        return True

    @observed_stage("edit")
    def finalize(self) -> None:
        """Drain the recorded placement intents (#426).

        When the drawing was built in **deferred** mode (``_defer_intents``), the add
        verbs recorded :class:`~draftwright.intents.Intent`\\s instead of placing. This
        drains them, routing what it can through the auto-pass's own solvers — in the
        auto-pass's own ORDER: the drain stages are keyed by the orchestrator's canonical
        ``_PASS_SEQUENCE`` and executed by the shared ``run_stages`` (#699 slice b), so
        the two build paths cannot silently diverge in sequencing. The routed stages:

        * **reserve_section** — a recorded ``section``'s cutting-plane row is reserved
          first so the callout carve sees it as an obstacle (Coupling A);
        * **live_replay** — furniture, non-routed dimensions, and axes-restricted locates
          replay in recorded order (pop-after-success);
        * **hole_callouts** — hole/pattern ø callouts through ``_annotate_holes`` — the
          real priority-drop / central-bore-anchoring solve;
        * **locations / height_ladder / step_positions / slots / user_dims** — the
          register-only stages queue into the SHARED corridor (a slot position coincident
          with a hole location collapses to one dim, #345; pin/priority user dims join as
          first-class candidates, ADR 4 (was 0012));
        * **detail_request** — when detail recovery is enabled (the automatic default,
          persisted on ``BuildState``) and the ladder stage recorded a "step"/"illegible"
          escalation, the prismatic step-height detail is queued, exactly as the auto
          pass gates it (#661);
        * **diameters / step_lengths** — the X/Z-turned set-solves place immediately,
          before the drain, exactly as the auto-pass runs them (a crowded X-turned
          head queues its enlarged ``DetailRequest`` here, #304/#307);
        * **drain** — one ``drain_and_reconcile`` places every queued candidate
          (crossing-free, deduped, monotone ladder) + the #690 label reconciliation;
        * **section** — renders after the drained furniture exists (its room check clears
          the side view's right);
        * **details** — every queued detail request resolves through the one generic
          detailer, after the drain + section so it avoids everything placed (#661 —
          pre-fix the finalize path never resolved the queue, so the edit path
          produced no detail views);
        * **tabulate** — dense-scattered plan holes escalate to the hole **table** +
          balloon ring via ``_maybe_tabulate_holes`` — last, so it sees the section +
          title block as obstacles. The density gate counts *all* analysis holes, so this
          is a full-reconstruction escalation (a partial hand-edit still tabulates the
          full count, #434); the escalations live only on the per-run ctx, so a repeat
          batch starts clean (#639).

        A slot records width/length and, for an obround, ``slot_end_radius`` on one feature;
        routing the feature also regenerates its model-derived datum **position** dim, so finalize
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
        sequence drains each batch.
        """
        # Nothing recorded → nothing to replay (the live/auto-pass path). The corridor batch is
        # a per-run local built below from these intents (#639), so an empty intent list has no
        # pending placement work to strand.
        if not self._intents:
            return
        from draftwright.annotations._common import PlacementContext

        # Fresh per-run placement scratch, threaded to the passes rather than hung on the drawing
        # (ADR 1 (was 0005 §2), #639): render_locations/_annotate_holes/drain_corridors register/read here.
        # A local — finalize is transactional (#647), so a raised drain rolls the drawing back and
        # the retry re-runs from a clean slate with a new ctx; no cross-call persistence needed.
        ctx = PlacementContext(
            registry=self._registry,
            coverage=self._coverage,
            items=self.items,
            part_model=self.model(),
            model_declared=self.model_declared,
            document_member=self._document_member,
            document_source_annotation_ids=self._document_source_annotation_ids,
            trace=self._build.trace,  # the finalize drain traces too (#736)
            feature_leaders=[],
            interior_dimensions=[],
        )
        # The solve trace joins the transaction: the recorder appends during
        # the drain, so a rolled-back finalize must also truncate those records — else the
        # trace (and a rewritten file) would describe placements that no longer exist.
        # Snapshot BEFORE opening the finalize phase: the phase counter is trace state too.
        trace_snap = ctx.trace.snapshot() if ctx.trace is not None else None
        if ctx.trace is not None:
            ctx.trace.begin_phase("finalize")

        # The section outcome is transaction state too (#1190): the drain can place or
        # withhold a section, and a rolled-back drain that leaves `section_decision`
        # saying "placed" while `views_snap` has removed the view is worse than no
        # record at all — a caller branches on this field precisely because it is
        # supposed to be the reliable one.
        section_snap = dict(self.section_decision)
        detail_snap = list(self.detail_decisions)

        model, a = self._part_model, self._analysis
        if a is not None and "iso" in self.views and "iso" in self._coords:
            # ISO growth may relocate the projected view after the immutable Analysis
            # was attached at the single build-state fill site. Deferred refits need
            # the actual centre, which the view coordinates already own; derive a
            # local analysis instead of replacing Drawing's private build state.
            iso_x, iso_y, _ = self.at("iso", a.cx, a.cy, a.cz)
            a = _analysis_with_iso_centre(a, iso_x, iso_y)
        routable = model is not None and a is not None
        r = self._classify_intents(model, a, routable)

        # (#647) Finalize is transactional: snapshot every collection it mutates so a raise
        # part-way (a corridor solve, a replayed verb) rolls the drawing back to its pre-finalize
        # state — a retry then starts from a clean slate and re-runs, rather than operating against
        # half-committed annotations/intents (the duplicate-corridor / partial-commit defects
        # #647 describes). The registry owns identity (named/view/feature/pins); items/intents/
        # views/build-issues/coverage/coords are the rest (a section view adds to BOTH views and
        # _coords; the passes mutate coverage); the edge cache is recomputable so it is just cleared.
        reg_snap = self._registry.snapshot()
        issues_snap = self._registry.issues
        items_snap = list(self.items)
        intents_snap = list(self._intents)
        views_snap = dict(self.views)
        coords_snap = dict(self._coords)
        coverage_snap = self._coverage.snapshot()
        # render_locations narrows the side-above strip's outer_limit IN PLACE on the shared
        # Analysis (from_model.py) — the one Analysis field a replay mutates. Capture + restore it
        # too, else a raised drain leaves the retry solving against a stale cap.
        sv_above = a.sv_zones.above if a is not None else None
        sv_above_limit = sv_above.outer_limit if sv_above is not None else None
        deferred, self._defer_intents = self._defer_intents, False  # replay must place
        try:
            self._drain_intents(ctx, model, a, r)
        except BaseException:
            # (#647) Roll back a partial finalize so a corrected retry starts clean.
            # BaseException (not just Exception) so a KeyboardInterrupt/SystemExit mid-drain
            # still restores the drawing before propagating — the transaction is all-or-nothing
            # on *any* raise, matching the guarantee this block advertises.
            self._registry.restore(reg_snap)
            self._registry.restore_issues(issues_snap)
            self.items = items_snap
            self._intents = intents_snap
            self.views = views_snap
            self._coords = coords_snap
            self._coverage.restore(coverage_snap)
            self.section_decision = section_snap
            self.detail_decisions = detail_snap
            if sv_above is not None:
                sv_above.outer_limit = sv_above_limit
            if trace_snap is not None:  # roll the failed drain's records out of the trace
                ctx.trace.restore(trace_snap)
            self._build.clear_geometry_caches()
            raise
        finally:
            self._defer_intents = deferred
        # Re-write the trace file only AFTER a successful drain (#736) — never mid-drain,
        # so a rollback cannot leave a file describing rolled-back placements.
        if ctx.trace is not None:
            ctx.trace.write()

    def _drain_intents(self, ctx, model, a, r) -> None:
        """Run the deferred stages inside :meth:`finalize`'s transaction."""
        self._intents = drain_intents(
            self,
            ctx,
            model,
            a,
            r,
            IntentDrainState(
                intents=list(self._intents),
                detail_view=self._build.detail_view,
                replay=self._replay_intent,
                queue_dimension=self._queue_dimension_intent,
                record_issue=self._record_build_issue,
            ),
        )

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
        """The owning orthographic view for *name* ("front"/"plan"/"side"/"rear"), or
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

    def preview_annotation(self, name: str, path: str | os.PathLike) -> str:
        return preview_export_annotation(
            name,
            path,
            deferred_pending=bool(self._defer_intents or self._intents),
            get_annotation=self.get_annotation,
            view_of=self.view_of,
            view_bounds=self.view_bounds,
            views=self.views,
            write_svg=self._write_svg,
        )

    def _table_state(self) -> DrawingTableState:
        return DrawingTableState(
            drawing=self,
            draft=self.draft,
            registry=self._registry,
            analysis=self._analysis,
            model=self._part_model,
            coords=self._coords,
            coverage=self._coverage,
            items=self.items,
            page_w=self.page_w,
            page_h=self.page_h,
            document_member=self._document_member,
            document_source_annotation_ids=self._document_source_annotation_ids,
            add=self._add,
            add_table=self.add_table,
            add_balloons=self.add_balloons,
            hole_spec_groups=self._hole_spec_groups,
            fit_auxiliary_box=fit_auxiliary_box,
        )

    def note(self, text, at, *, view=None, rotation=0.0, name=None, align=None):
        """Add user-positioned free text and return its annotation name."""
        return drawing_note(
            self._table_state(), text, at, view=view, rotation=rotation, name=name, align=align
        )

    def add_table(
        self,
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
        """Fit a data table in available sheet space and report any placement drop."""
        return drawing_add_table(
            self._table_state(),
            rows,
            prefer=prefer,
            name=name,
            block_cols=block_cols,
            _source_id=_source_id,
            _source_ids=_source_ids,
            _features=_features,
            _drop_code=_drop_code,
            _drop_severity=_drop_severity,
            _cells=_cells,
            _left_align_cols=_left_align_cols,
        )

    def _hole_spec_groups(self, view):
        return drawing_hole_spec_groups(self._table_state(), view)

    def add_balloons(self, view, specs):
        """Place leadered balloons in the view's reserved halo."""
        return drawing_add_balloons(self._table_state(), view, specs)

    def _add_balloon(self, view, tag, j, hole):
        """Single-balloon convenience over :meth:`add_balloons` (#111)."""
        self.add_balloons(view, [(tag, j, hole)])

    def add_hole_table(self, view="plan", *, prefer="tr", name=None, balloons=True):
        """Add a hole table and optional matching balloons for a view."""
        return drawing_add_hole_table(
            self._table_state(), view=view, prefer=prefer, name=name, balloons=balloons
        )

    def pin(self, name):
        """Pin a named annotation so the engine never moves it (#89).

        A deliberate placement — by you or an AI — must win over automatic
        layout. :meth:`repair` will not re-place a pinned annotation, and the
        constraint solver (ADR 2 (was 0003)) treats it as fixed. Pinning fixes the
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

    @deprecated(
        "Drawing.clear_annotations() is deprecated (#817): wholesale annotation removal is a "
        "footgun for user scripts — use the feature-scoped verbs (drop/remove). The primitive "
        "is now private (_clear_annotations). Removed in 0.5.0."
    )
    def clear_annotations(self, keep=("title_block",)):
        """DEPRECATED (#817): now private (:meth:`_clear_annotations`)."""
        return self._clear_annotations(keep)

    def _clear_annotations(self, keep=("title_block",)):
        """Remove all annotations except those named in *keep* (#74).

        Wholesale removal that does not depend on the automatic naming
        scheme — ``_clear_annotations()`` strips every automatic dimension,
        leader, and centreline but keeps the title block.

        Returns:
            The list of removed annotation objects.
        """
        kept_named = self._registry.clear(keep)  # prunes names, views, and pins
        kept_ids = {id(o) for o in kept_named.values()}
        removed = [o for o in self.items if id(o) not in kept_ids]
        self.items = [o for o in self.items if id(o) in kept_ids]
        return removed

    def _record_build_issue(
        self, severity, code, message, *, measurement=None, measurement_span=None
    ):
        """Record a lint issue discovered during construction (e.g. an
        annotation the layout had to drop). Surfaced by :meth:`lint` so a
        dropped feature is never silent."""
        self._registry.record_issue(
            LintIssue(
                severity=severity,
                code=code,
                message=message,
                measurement_ids=(measurement,) if measurement is not None else (),
                outcome_stage="placement" if measurement is not None else None,
                measurement_spans=(measurement_span,) if measurement_span is not None else (),
            )
        )

    # -- repair ---------------------------------------------------------------
    @observed_stage("repair")
    def repair(self, max_iter: int = 3, *, _initial_issues=None, _on_settled=None):
        """Close the lint→repair loop: act on violations, don't only report them.

        After the greedy initial placement, re-place the dimensions behind the
        mechanically-clear violations and re-lint, bounded to *max_iter* passes:

        - ``dim_inside_part`` — the offset is on the wrong side; flip it once.
        - ``annotation_ink_overlap`` / ``annotation_overlap`` — try the shared dimension candidate solver
          once, with along-span choices and at most one existing stacking tier.
          Pins, authored sides, annotation membership and confirmed measurements
          survive; every lint-code/severity component must stay the same or improve,
          and at least one must improve. An infeasible candidate leaves the findings.

        Only engine-built dimensions (carrying ``_dw_spec``) are re-placeable;
        leaders, callouts and standards-judgement issues (e.g.
        ``missing_principal_dimension``) are left for the caller. Each side flip
        is attempted at most once; a clean drawing is returned unchanged.

        Wrong-side flips retain their issue-count rollback guard. Rejected ink
        candidates restore the original items and registry; an exception during
        candidate critique also restores them before propagating the error.

        Returns ``self`` for chaining.
        """
        from draftwright.annotations._common import prevent_dimension_label_ink

        def ink_candidates(dimensions, pins):
            return prevent_dimension_label_ink(
                dimensions,
                page=self.drawable_bounds,
                immutable=pins,
                perpendicular_step=self.draft.font_size + 2 * self.draft.pad_around_text,
            )

        return repair_drawing(
            self,
            max_iter,
            ink_candidates=ink_candidates,
            initial_issues=_initial_issues,
            on_settled=_on_settled,
        )

    def _diagnostics(self) -> DiagnosticOperations:
        """Pass Drawing-owned state and public dispatch hooks to a diagnostic operation."""
        return DiagnosticOperations(
            self,
            analysis=self._analysis,
            model=self._part_model,
            build=self._build,
            registry=self._registry,
            coverage=self._coverage,
            working_part=self._working_part,
            model_declared=self._model_declared,
            view_edge_cache=self._view_edge_cache,
            ann_box_cache=self._ann_box_cache,
            cyl_cache=self._cyl_cache,
            set_cyl_cache=lambda cache: setattr(self, "_cyl_cache", cache),
        )

    # -- output ---------------------------------------------------------------
    @observed_stage("lint")
    def lint(self, *, physical: bool = True):
        """Lint all annotations against all views; returns the list of issues.

        When :attr:`part` is set, also runs :func:`lint_feature_coverage`.
        Build-time drops recorded via :meth:`_record_build_issue` are included.

        ``physical=False`` asks for the **placement** critique only — geometry/standards
        checks over what is on the sheet — and skips the feature-coverage half that needs a
        recognition inventory of the solid. That is what the repair loop wants (it acts on
        the allowlisted placement codes in ADR 5), and on a declared build it is the
        difference between exporting a drawing and recognising the part to no purpose
        (#1022). The default stays the full critique: a caller asking "is this drawing
        right?" means both halves.
        """
        return self._lint(physical=physical, aggregation=_current_issue_aggregation())

    def _lint(self, *, physical: bool = True, aggregation=None):

        return self._diagnostics()._lint(physical=physical, aggregation=aggregation)

    def layout_utilization(self) -> dict:

        return self._diagnostics().layout_utilization()

    def lint_summary(self) -> dict:

        return self._diagnostics().lint_summary()

    # The output formats export() understands. PDF renders from the SVG, PNG from the PDF —
    # so requesting pdf/png writes the SVG (and pdf) as intermediates, cleaned up if not asked for.
    _EXPORT_FORMATS = ("svg", "dxf", "pdf", "png")

    def _lint_and_log(self) -> None:

        return self._diagnostics()._lint_and_log()

    def _write_svg(self, out: str, *, reproducible: bool = True) -> str:
        return write_drawing_svg(
            out,
            page_w=self.page_w,
            page_h=self.page_h,
            add_shapes=self._add_shapes,
            title_block=lambda: self.get_annotation("title_block"),
            reproducible=reproducible,
        )

    def _write_dxf(self, out: str, *, reproducible: bool = True) -> str:
        return write_drawing_dxf(
            out,
            page_w=self.page_w,
            page_h=self.page_h,
            add_shapes=self._add_shapes,
            reproducible=reproducible,
        )

    def _pdf_text_runs(self):
        """Return semantic PDF text runs in page reading order."""
        return pdf_text_runs(self.draft, self._registry.iter_named())

    @observed_stage("export")
    def export(
        self,
        out=None,
        *,
        formats=None,
        svg=None,
        dxf=None,
        dpi: int = 150,
        reproducible: bool | None = None,
    ) -> dict[str, str] | tuple[str | None, str | None]:
        return export_drawing(
            self,
            out,
            formats=formats,
            svg=svg,
            dxf=dxf,
            dpi=dpi,
            reproducible=reproducible,
            supported_formats=self._EXPORT_FORMATS,
            lint_and_log=self._lint_and_log,
            write_svg=self._write_svg,
            write_dxf=self._write_dxf,
            pdf_text_runs=self._pdf_text_runs,
        )

    def export_pdf(self, out=None) -> str:
        """Deprecated — use ``export(out, formats=("pdf",))["pdf"]``. Renders a PDF (svglib +
        reportlab) with the draftwright metadata + clickable title-block link."""
        warnings.warn(
            "Drawing.export_pdf() is deprecated; use export(formats=('pdf',))['pdf']. "
            "Removed in 0.5.0.",
            DeprecationWarning,
            stacklevel=2,
        )
        paths = self.export(out, formats=("pdf",))
        assert isinstance(paths, dict)  # formats=... always returns the {format: path} dict
        return paths["pdf"]

    def _add_shapes(self, exporter, *, ordered: bool = False):
        return add_export_shapes(
            exporter,
            views=self.views,
            items=self.items,
            iter_annotations=self.iter_annotations,
            ordered=ordered,
        )


# Keep the public operation documentation on its observed Drawing facade.
Drawing.export.__doc__ = export_drawing.__doc__
Drawing.preview_annotation.__doc__ = preview_export_annotation.__doc__

# Preserve operation documentation on the Drawing facade.
Drawing._derive_span.__doc__ = EditOperations._derive_span.__doc__
Drawing._resolve_dimension_span.__doc__ = EditOperations._resolve_dimension_span.__doc__
Drawing._resolve_dimension_side.__doc__ = EditOperations._resolve_dimension_side.__doc__
Drawing._angular_dimension_plan.__doc__ = EditOperations._angular_dimension_plan.__doc__
Drawing._queue_dimension_intent.__doc__ = EditOperations._queue_dimension_intent.__doc__
Drawing.dimension.__doc__ = EditOperations.dimension.__doc__
Drawing.callout.__doc__ = EditOperations.callout.__doc__
Drawing.overall_height.__doc__ = EditOperations.overall_height.__doc__
Drawing.furniture.__doc__ = EditOperations.furniture.__doc__
Drawing.rotational.__doc__ = EditOperations.rotational.__doc__
Drawing.section.__doc__ = EditOperations.section.__doc__
Drawing.locate.__doc__ = EditOperations.locate.__doc__

# Preserve the public diagnostic operation documentation on the result facade.
Drawing.report.__doc__ = DiagnosticOperations.report.__doc__
Drawing.requirement_snapshot.__doc__ = DiagnosticOperations.requirement_snapshot.__doc__
Drawing.write_report.__doc__ = DiagnosticOperations.write_report.__doc__
Drawing._lint.__doc__ = DiagnosticOperations._lint.__doc__
Drawing.layout_utilization.__doc__ = DiagnosticOperations.layout_utilization.__doc__
Drawing.lint_summary.__doc__ = DiagnosticOperations.lint_summary.__doc__
