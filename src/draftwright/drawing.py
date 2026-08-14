"""The Drawing result object + table builder (#138 / ADR 0005, P6).

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
import tempfile
import warnings
from dataclasses import dataclass
from dataclasses import field as dataclasses_field
from typing import TYPE_CHECKING, Any, NamedTuple

if TYPE_CHECKING:
    from draftwright.recognition import RecognitionResult

# PEP 702 @deprecated. A `sys.version_info` guard (not try/except) so the type checker,
# which targets the 3.10 floor, resolves the backport branch instead of `warnings.deprecated`
# (only in 3.13+ typeshed).
if sys.version_info >= (3, 13):
    from warnings import deprecated
else:
    from typing_extensions import deprecated

from build123d import (
    Color,
    ExportSVG,
    LineType,
    Location,
)

from draftwright._core import (
    _MARGIN,
    Analysis,
    _build_table,
    _dim,
    _fmt,
    _log,
    _tag_sequence,
    place_annotation,
)
from draftwright.annotations._common import (
    PlacementContext,
    _register_hole_table_coverage,
    carve_free_position,
    strip_obstacles,
)
from draftwright.annotations.balloons import render_balloons
from draftwright.export import (
    _DraftwrightDXF,
    _export_shape,
    _render_pdf,
    _render_png,
    add_svg_hyperlink,
    add_svg_metadata,
    fix_svg_page_size,
    sanitize_svg_arcs,
    set_dxf_metadata,
    write_dxf,
)
from draftwright.intents import Intent
from draftwright.layout import fit_box
from draftwright.linting import (
    CoverageState,
    LintIssue,
    _suggest_fix,
    lint_axial_coverage,
    lint_boss_height_coverage,
    lint_channel_coverage,
    lint_declaration_reconciliation,
    lint_declared_gear_coverage,
    lint_drawing,
    lint_feature_coverage,
    lint_flat_coverage,
    lint_hole_coverage,
    lint_location_coverage,
    lint_pmi_extraction,
    lint_pmi_ignored,
    lint_pmi_lowering,
    lint_pmi_rendering,
    lint_polygonal_stock_coverage,
    lint_principal_profile_coverage,
    lint_prismatic_coverage,
    lint_profiled_bore_coverage,
    lint_slot_coverage,
    pmi_stage_summary,
)
from draftwright.linting.issues import _collect_issue_aggregation, _current_issue_aggregation
from draftwright.linting.quality import quality_components
from draftwright.projection import (
    project_view_geometry,
)
from draftwright.recognition import analyse_cylinders, build_recognition_result
from draftwright.registry import AnnotationRegistry
from draftwright.repair import repair_drawing

# Codes that check standards/geometry correctness rather than pure page
# layout. Grouped so a caller (and the #30 repair loop) can tell a wrong
# drawing from a merely tight one.
_GEOMETRY_AWARE_CODES = frozenset(
    {
        "feature_not_dimensioned",
        "feature_count_mismatch",
        "feature_not_located",
        "feature_no_centermark",
        "pad_footprint_not_defined",
        "pocket_not_located",
        "unrecognised_defining_geometry",
        "pmi_not_lowered",
        "pmi_not_rendered",
        "axial_length_missing",
        "flat_requirement_suppressed",
        "flat_requirement_missing",
        "flat_requirement_unverifiable",
        "hole_requirement_suppressed",
        "hole_requirement_missing",
        "hole_requirement_unverifiable",
        "missing_principal_dimension",
        "label_vs_measured",
        "dim_inside_part",
        "callout_dropped",
        "location_ref_dropped",
        "off_axis_location_dropped",
        "hole_pattern_dim_dropped",
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


class _HoleInstance(NamedTuple):
    """One hole occurrence for the table / balloon renderers — the IR fields those
    passes read (``location`` + ``diameter`` for a balloon, ``through``/``depth`` for a
    table row), so no ``HoleRecord`` is needed (ADR 0008; #584 WP1). Duck-compatible
    with the recogniser record the orchestrator's balloon path still passes."""

    location: tuple
    diameter: float
    through: bool
    depth: float | None


def _ir_hole_groups(model, target_axis: str) -> list[tuple]:
    """``(owner, spec, [member positions], count)`` groups on *target_axis*.

    One group per IR hole/pattern feature — the spec-grouping + pattern recognition
    detection already did, so no ``HoleRecord``/``HoleSpec`` re-grouping is needed
    (ADR 0008 Am6; #584 WP1). ``spec`` is the representative ``HoleFeature`` (carries
    diameter / through / depth); ``positions`` are its member centres (drive balloon
    placement); ``count`` is the feature's own count (the table QTY / FeatureInfo.count).
    They coincide on the detected path (``members`` is fully populated); ``count`` stays
    faithful for a declared feature whose ``members`` are unspecified (ADR 0011)."""
    groups: list[tuple] = []
    for f in model.features:
        if f.kind == "hole" and f.frame.axis == target_axis:
            groups.append((f, f, list(f.members) or [f.frame.origin], f.count))
        elif f.kind == "pattern" and f.member.frame.axis == target_axis:
            groups.append((f, f.member, list(f.members) or [f.member.frame.origin], f.count))
    return groups


# Machined-feature LEADER callouts (#148): a chamfer/fillet/flat/pocket/groove exposes no
# linear-dim param (its params carry no span — it is a Leader callout, not a Dimension), so
# the reconstruction can't route it through dimension(). callout(f) records a per-feature
# intent that the matching per-kind finalize stage renders through the kind's auto-pass
# renderer, restricted to that feature (only=), at the canonical _PASS_SEQUENCE slot. Each
# name is BOTH the feature.kind and the stage/sequence key. Plate is deliberately EXCLUDED —
# it IS a spanned dimension (corridor-registered + drained, not a direct leader), so it
# reconstructs through dimension(f, "length", role="thickness"), not callout() (#811 review).
_MACHINED_CALLOUT_KINDS = ("chamfer", "fillet", "flat", "pocket", "groove")


@dataclass(frozen=True)
class _IntentRouting:
    """How :meth:`Drawing.finalize` routes each recorded intent to an auto-pass solver (#426).

    The classification half of finalize (#590 split): pure comprehensions over the recorded
    intents deciding which route each takes. ``section`` is the pre-planned section (or None);
    the ``*_ids`` are ``id()`` sets of the intents on each route; the ``only_*``/``*_feats`` are
    the feature sets the routed renderers receive.
    """

    section: object
    corridor_ids: set
    callout_ids: set
    dia_ids: set
    len_ids: set
    slot_ids: set
    height_ladder_ids: set
    #: `Drawing.overall_height()` intents — the bbox-fallback overall height, which has no
    #: feature to hang a `dimension(...)` intent on (#889).
    overall_height_ids: set
    step_position_ids: set
    explicit_envelope_height: bool
    user_dim_ids: set
    rotational_ids: set
    off_axis_loc_ids: set
    only_loc: set
    pinned_loc: set
    only_callout: set
    only_dia: set
    only_len: set
    slot_feats: set
    machined_ids_by_kind: dict
    pocket_pattern_ids: set
    slot_pattern_ids: set


#: Size scalars appended to a feature key when present, in this order. Position alone is not
#: identity: two holes at ONE origin with different bores keyed the same, and that is exactly
#: the coincident-dedup case where the ledger most needs to say which instance lost its
#: location (Codex #996 r4). Rounded, so float noise from a rebuild does not change the key.
_KEY_SCALARS = ("diameter", "depth", "width", "length", "radius")


def feature_key(f) -> str | None:
    """A stable, plain-data identity for a feature in the audit ledger (#996).

    ``kind@(x,y,z)/axis`` plus whichever of :data:`_KEY_SCALARS` the feature carries. The type
    name alone made two holes indistinguishable, so a diff of two builds could not say which
    one lost a measurement, or whether a suppression had moved between instances (Codex r1).
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
    x, y, z = (float(v) for v in (origin[0], origin[1], origin[2]))
    sizes = [
        f"{name}={float(v):.3f}"
        for name in _KEY_SCALARS
        if isinstance(v := getattr(f, name, None), (int, float))
    ]
    tail = ("[" + ",".join(sizes) + "]") if sizes else ""
    return f"{kind}@({x:.3f},{y:.3f},{z:.3f})/{axis}{tail}"


@dataclass
class BuildState:
    """The build context a finished :class:`Drawing` carries (ADR 0005 §2 / #639).

    One typed home for what used to be four loose private attributes:

    - ``analysis`` — the pipeline's :class:`Analysis` namespace.
    - ``part_model`` — the detected/declared ADR-0008 PartModel (read surface for
      semantic edits, #397).
    - ``recognition`` — the ADR 0017 aggregate reused by model detection and critique.
    - ``view_edge_cache`` — lint's per-view edge bboxes, keyed on id(view shape)
      (helpers #143/#164).
    - ``ann_box_cache`` — lint's annotation bounding boxes (#602): identity- AND
      location-token-checked entries (see ``_ann_box``), pruned by ``lint()``.
    - ``principal_profile_cache`` — solid-derived unsupported principal-profile issues
      reused by repeated physical critique (#1058).
    - ``trace`` — the opt-in solve-trace recorder (#736,
      :class:`~draftwright.annotations._common.SolveTrace`), or ``None`` (default:
      tracing off). Carried here so the finalize path traces like the auto pass.
    - ``detail_view`` — the resolved ``build_drawing(detail_view=...)`` setting,
      persisted so the finalize drain gates the prismatic detail request exactly
      as the auto pass does (#661) — on the ``auto_dims=False`` path the flag
      would otherwise be consumed nowhere.

    The builder assembles it at one site; ``recognition`` may also be filled once by
    :meth:`ensure_recognition` for declared-path critique. The compat properties on
    ``Drawing`` read through it, so ``dwg._analysis``-style test inspection keeps working.
    """

    analysis: Analysis | None = None
    recognition: RecognitionResult | None = None
    part_model: object | None = None
    view_edge_cache: dict = dataclasses_field(default_factory=dict)
    ann_box_cache: dict = dataclasses_field(default_factory=dict)
    principal_profile_cache: tuple[object, bool, tuple[LintIssue, ...]] | None = None
    trace: Any = None
    detail_view: bool = False
    #: The compiler's :class:`~draftwright.model.compiled.Omission` records — every
    #: measurement it considered and did not approve, with the rule that stopped it (#996).
    #: The compiled plan was a local in the orchestrator: built, read by the renderers, and
    #: dropped. So the one place recording WHY a dimension is absent did not outlive the
    #: build, and absence had to be inferred from a finished sheet — which is how a wrong
    #: suppression rule produced four issue reports before anyone found the rule (#997).
    omissions: tuple = ()

    def clear_geometry_caches(self) -> None:
        """The one invalidation seam (finalize rollback): view edges + annotation
        boxes together — a rolled-back drawing must re-measure everything."""
        self.view_edge_cache.clear()
        self.ann_box_cache.clear()

    def ensure_recognition(self, part) -> RecognitionResult:
        """The run's recognition aggregate, recognising *part* once if nothing has yet.

        A declared build performs no recognition (ADR 0011 / #1022), so critique on that path
        has no inventory to judge against and must produce one.  It is built **here**, in the
        typed build state, and at most once per drawing: a lint-side or ``Drawing``-side memo
        would make critique a second recognition owner, which is exactly what ADR 0017 exists
        to remove (and what review of #1021 rejected).

        On a detected build ``recognition`` is already filled by the builder, so this returns
        it and recognises nothing.
        """
        if self.recognition is None:
            self.recognition = build_recognition_result(part)
        return self.recognition


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
        # registry (#138 / ADR 0005, Step 2), reached through its own surface
        # (`in reg` / `names()` / `issues`) — the `dwg._named` &c. compat aliases
        # that shadowed them were deleted at the ADR 0005 §4 exit date (#720).
        self._registry = AnnotationRegistry()
        # Lint-side coverage signal (pattern callouts, patterned holes, dropped
        # callout diameters) lives in its own owner (#138 / ADR 0005, Step 3);
        # its `dwg._pattern_callouts` &c. aliases went the same way (#720).
        self._coverage = CoverageState()
        # ADR 0005 §2 (#639): the drawing's build context in ONE typed object —
        # analysis, part model, and the two geometry caches lint persists.
        # Constructed empty here; builder._assemble fills it at a single site.
        # The render passes never read it off the drawing (the empty
        # _DWG_PRIVATE_READ_ALLOW ratchet) — it serves the Drawing's OWN methods
        # (lint / finalize / repair / the edit verbs) and the test surface.
        self._build = BuildState()
        self.svg_path: str | None = None
        self.dxf_path: str | None = None
        # True when the caller SUPPLIED the model (build_drawing(model=…), ADR 0011) rather
        # than it being detected — gates the model-driven hole/pattern render membership so a
        # declared hole draws even where detection missed it, no-op for the detected path (#448).
        self._model_declared: bool = False
        # Deferred placement intents (#426 Phase 1). When _defer_intents is True the add
        # verbs record an Intent instead of placing; finalize() drains them (Phase 1
        # replays through the live helpers). Default off → the live path is unchanged.
        self._intents: list[Intent] = []
        self._defer_intents: bool = False

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

    def _add_view(self, name, shape, camera, up, position, *, look_at=None, scaled=False):
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
            self.scale, name, shape, camera, up, position, look_at=la, scaled=scaled
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

    def recognition(self) -> RecognitionResult | None:
        """The ADR 0017 recognition inventory used to build this drawing.

        This is the geometry-only evidence below the detected/declared :meth:`model` and
        drafting policy.  It is an experimental, read-only result.

        ``None`` for a bare ``Drawing`` that did not pass through :func:`build_drawing`, and
        for a **declared** build that has not yet been critiqued — that path recognises
        nothing (ADR 0011 / #1022) and only builds an aggregate when something asks for
        physical critique.  So ``None`` here means "nothing has needed recognition yet", never
        "this part has no features".
        """

        return self._build.recognition

    # --- build-context compat properties (#639): one BuildState, thin views.
    # _part_model and the two caches are GETTER-ONLY by design (#691 review):
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
        duck-typed against ``dwg`` and, per ADR 0005, reads no private Drawing state.
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

    def suppressions(self) -> list[dict]:
        """Every measurement the compiler considered and did not approve, and why.

        The **audit read** (#996). A finished drawing shows what was drawn; this shows what
        was *not*, separated into the two cases that mean opposite things:

        - ``authored`` — the script's own omission, under ADR 0016's rule that an authored
          set means omission is suppression. Recoverable by adding a ``dimension(...)`` line.
        - otherwise — a **planner rule** decided it, and ``reason`` names which.

        The second is the one worth auditing. A rule that fires where it should not produces
        a drawing that is silently under-defined and lints clean, which is how #997's square
        rule generated four separate issue reports without any of them naming the cause. An
        absent dimension is only defensible if something can say which rule removed it; this
        is that something.

        Returns plain dicts so a harness, a script or an LLM can diff two builds without
        importing IR types. ``feature`` is a **stable key**, not just the type name: a bare
        ``"HoleFeature"`` made two holes indistinguishable, so a diff could not say *which*
        one lost its location, or whether a suppression had moved between instances (Codex
        #996 r1). The key is ``kind@(x,y,z)/axis``, which survives a rebuild because it is
        derived from the geometry rather than from list position.
        """

        return [
            {
                "feature": feature_key(o.feature),
                "parameter_id": o.parameter_id,
                "value": o.value,
                "reason": o.reason,
                "authored": o.authored,
            }
            for o in self._build.omissions
        ]

    def measurement_keys(self, name) -> list[dict]:
        """Which measurements the annotation *name* draws — possibly none (#1002).

        The mirror of :meth:`suppressions` and deliberately the SAME row shape —
        ``{"feature": <stable key>, "parameter_id": ...}`` — so a drawn measurement and a
        suppressed one are directly comparable. Without it the two halves of the audit could
        only be joined by matching an engine-assigned annotation name against a parameter id
        by substring, which attributed losses to unrelated suppressions (Codex #1001 r1).

        A **list**, because one annotation can draw several independently suppressible
        measurements — a compound hole callout renders bore diameter, depth and counterbore
        together (ADR 0016 / #886). Empty means the renderer recorded nothing, **not** that
        the annotation measures nothing. Which renderers record it is enforced by the ratchet
        in `tests/test_audit_differential.py`; treat presence as exact and absence as unknown.

        Exact **within** a build. Across two builds the key cannot match directly, because
        `feature_key` embeds coordinates and scalars a differential deliberately changes —
        `draftwright.audit` joins on the feature's kind instead.
        """
        return [
            {"feature": feature_key(mid.feature), "parameter_id": mid.parameter}
            for mid in self._registry.measurement_of(name)
        ]

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
        """Whether this drawing's model was **declared** by the caller (ADR 0011) rather than
        detected — the public read the annotation pass threads onto its PlacementContext (#639)."""
        return self._model_declared

    @deprecated(
        "Drawing.place_dim() is deprecated for normal editable scripts; use "
        "Drawing.dimension(feature, param, ..., pin=True) or "
        "Drawing.locate(feature, ..., pin=True) for feature-backed edits. "
        "place_dim() remains only as a raw page-coordinate escape hatch. "
        # Not dated with the #817 plumbing: ADR 0012 makes this the sanctioned escape hatch
        # until the full auto-plus-user recompose lands, so it has no replacement to point at.
        # But a bare "not before 0.5.0" is a lower bound, not an exit — and §4's complaint is
        # precisely about surfaces with no exit (Codex #987 r1). So it names BOTH: the
        # prerequisite and a target release, the latter to be revised if #707 slips.
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
            view: ``"front"``, ``"plan"``, or ``"side"``.
            draft: the drawing's :attr:`draft` preset.
            name: optional annotation name for later :meth:`remove` / replace.
            slot: strip slot depth (mm); the perpendicular space reserved per dim.
            feature: optional source IR feature to attribute this dim to, so
                :meth:`drop` / :meth:`annotations_of` can find it (#398).
            **kwargs: forwarded to ``Dimension`` (e.g. ``label=``, ``tolerance=``).

        Deprecated for normal editable scripts: prefer :meth:`dimension` for
        feature-backed linear dimensions and :meth:`locate` for feature-backed
        location dimensions. Both support ``pin=True`` in deferred/finalize mode
        and can participate in the shared layout solve.

        Uses the single-position strip carve, not the ADR-0009 collect-then-solve
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
        return self._add(
            _dim(p1, p2, side, max(dist, 4.0), draft, **kwargs), name, feature=feature
        )

    # -- annotations ----------------------------------------------------------
    def _add(self, obj, name=None, view=None, feature=None):
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
        return place_annotation(self._registry, self.items, obj, name, view, feature)

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
        :func:`finalize_drawing` afterwards (when available) to recompose the sheet."""
        names = self._registry.names_for_feature(feature)
        for n in names:
            self.remove(n)
        return names

    @staticmethod
    def _derive_span(feature, param):
        """Model-space ``(lo, hi)`` endpoints for a value-only *linear* param whose geometry
        the feature carries (#411), or ``None`` for a callout param with no linear span.

        Slots and pads: the width dim spans ``width_axis`` across
        ``w_center ± width/2`` (at the length midpoint); the length dim spans
        ``long_axis`` ``lo → hi`` (at the centre line) — the same endpoints
        ``render_slots`` measures."""
        feature_kind = getattr(feature, "kind", None)
        if feature_kind in ("slot", "pad"):
            ax = {"x": 0, "y": 1, "z": 2}
            li, wi = ax[feature.long_axis], ax[feature.width_axis]
            a = list(feature.frame.origin)
            b = list(feature.frame.origin)
            if param.role == f"{feature_kind}_length":
                a[li], b[li] = feature.lo, feature.hi
                a[wi] = b[wi] = feature.w_center
            elif param.role == f"{feature_kind}_width":
                mid = (feature.lo + feature.hi) / 2
                half = feature.width / 2
                a[wi], b[wi] = feature.w_center - half, feature.w_center + half
                a[li] = b[li] = mid
            else:
                return None
            return tuple(a), tuple(b)
        return None

    def _resolve_dimension_span(self, feature, param, *, role=None, view=None):
        """Return ``(param_record, view, p1, p2)`` for a feature linear dimension."""
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
        chosen = view
        for v in [view] if view else _ortho:
            q1, q2 = self.at(v, *lo), self.at(v, *hi)
            if math.hypot(q2[0] - q1[0], q2[1] - q1[1]) > 1e-6:
                chosen, p1, p2 = v, q1, q2
                break
        if p1 is None:
            raise ValueError(
                f"'{param}' span projects to a point in "
                f"{'the requested view' if view else 'every orthographic view'} — nothing to dimension"
            )
        return matches[0], chosen, p1, p2

    def _queue_dimension_intent(self, it, a, *, ctx, used_names=None) -> bool:
        """Queue a pinned/prioritized feature dimension into a shared corridor."""
        from draftwright.annotations._common import CorridorCandidate, register_corridor

        side = it.kwargs.get("side", "above")
        view = it.kwargs.get("view")
        if side not in ("above", "below", "left", "right"):
            return False
        zones_name = {"front": "fv_zones", "plan": "pv_zones", "side": "sv_zones"}
        rec, view, p1, p2 = self._resolve_dimension_span(
            it.feature,
            it.kwargs["param"],
            role=it.kwargs.get("role"),
            view=view,
        )
        zones = getattr(a, zones_name.get(view, ""), None)
        strip = getattr(zones, side, None) if zones is not None else None
        if strip is None:
            return False

        name = it.kwargs.get("name")
        if name is None:
            used_names = used_names if used_names is not None else set()
            i = 0
            while (name := f"dim_{it.kwargs['param']}{i}") in self._registry or name in used_names:
                i += 1
            used_names.add(name)

        dim_kwargs = {
            k: v
            for k, v in it.kwargs.items()
            if k not in {"param", "role", "side", "view", "name", "pin", "priority", "slot"}
        }
        if "label" not in dim_kwargs:
            page_len = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
            dim_kwargs["label"] = _fmt(page_len / self.scale)
        slot = it.kwargs.get("slot", 8.0)
        axis = "y" if side in ("above", "below") else "x"
        ax = 1 if axis == "y" else 0
        if side in ("right", "above"):
            natural = max(p[ax] for p in (p1, p2)) + slot
        else:
            natural = min(p[ax] for p in (p1, p2)) - slot
        tier = self.draft.font_size + 2 * self.draft.pad_around_text
        p_lo, p_hi = sorted((p1[1 - ax], p2[1 - ax]))

        def _build(pos, _p1=p1, _p2=p2, _side=side, _ax=ax, _kwargs=dim_kwargs):
            if _side in ("right", "above"):
                dist = pos - max(p[_ax] for p in (_p1, _p2))
            else:
                dist = min(p[_ax] for p in (_p1, _p2)) - pos
            return _dim(_p1, _p2, _side, max(dist, 4.0), self.draft, **_kwargs)

        def _placed(nm, _pin=it.kwargs.get("pin", False)):
            if _pin:
                self.pin(nm)

        def _drop(nm):
            self._record_build_issue(
                "warning",
                "dimension_dropped",
                f"{nm} not placed (no room on the {view} {side} strip)",
            )

        priority = float(it.kwargs.get("priority", 0.0) or 0.0)
        if it.kwargs.get("pin"):
            priority = max(priority, 100.0)
        register_corridor(
            ctx,
            (view, side),
            strip,
            view,
            axis,
            tier,
            CorridorCandidate(
                name=name,
                build=_build,
                order=(0, natural, name),
                on_place=_placed,
                on_drop=_drop,
                dedup=(view, side, round(p_lo, 6), round(p_hi, 6), rec.role),
                precedence=4,
                priority=priority,
                anchored=bool(it.kwargs.get("pin")),
                natural=natural,
                feature=it.feature,
            ),
        )
        return True

    def dimension(
        self,
        feature,
        param,
        *,
        role=None,
        side="above",
        view=None,
        name=None,
        pin=False,
        priority=0.0,
        **kwargs,
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
        In deferred mode, ``pin=True`` anchors the dimension at its natural slot coordinate
        inside the shared corridor solve, and ``priority=`` controls over-capacity survival.
        Live placement still uses the single-position escape hatch and pins only the placed
        annotation name.

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
                        "pin": pin,
                        "priority": priority,
                        **kwargs,
                    },
                )
            )
            return ""
        _rec, view, p1, p2 = self._resolve_dimension_span(feature, param, role=role, view=view)
        if name is None:
            i = 0
            while (name := f"dim_{param}{i}") in self._registry:
                i += 1
        self._place_dim(
            p1,
            p2,
            side,
            view,
            self.draft,
            name=name,
            feature=feature,
            **kwargs,
        )
        if pin:
            self.pin(name)
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
        linear param). A machined-feature callout (pocket/fillet/flat/chamfer/groove) is
        auto-named and placed in its characteristic view by the kind's renderer, so
        ``view=``/``name=`` are unsupported for those kinds and raise ``ValueError`` rather
        than being silently ignored (Codex #811). Placed reasonably, not via the auto-pass's
        whole-set solve (byte-identity is not a goal, #400 Ph2) — :meth:`repair` tidies the
        rest. A step/boss diameter that finds no room returns ``""`` (a warning-level drop,
        like the auto-pass), rather than raising, so a reconstruction script never aborts.
        """
        kind = getattr(feature, "kind", None)
        if (kind in _MACHINED_CALLOUT_KINDS or kind in ("pocket_pattern", "slot_pattern")) and (
            view is not None or name is not None
        ):
            raise ValueError(
                f"callout(): a {kind} is auto-named and placed in its characteristic view; "
                "view=/name= are unsupported for machined-feature callouts"
            )
        # There is deliberately NO authored-omission pre-check here.
        #
        # Three versions of one lived at this point, each a hand-written prediction of what
        # a callout would draw given the plan, and each wrong for some kind (#921 rounds
        # 6–8). The last asked "does this feature have ANY approved dimension?", which is
        # sound only in one direction: a turned step with its length authored and its
        # diameter omitted answered yes, and the live path then drew the omitted diameter
        # (#925 review). Adding a fourth prediction would rebuild the per-kind table this
        # boundary exists to delete.
        #
        # The renderers below now consume approved content, so "draws nothing" is what they
        # DO rather than something to forecast — and both paths reach it the same way: the
        # live call returns "" with an `authored_omission` build issue, and the deferred
        # intent drains through the same migrated renderers to the same nothing.
        if self._defer_intents:  # #426: record, don't place — finalize() drains it
            self._intents.append(Intent("callout", feature, {"view": view, "name": name}))
            return ""
        from draftwright.annotations._common import PlacementContext
        from draftwright.annotations.holes import add_feature_callout, add_feature_diameter

        ctx = PlacementContext(registry=self._registry, coverage=self._coverage, items=self.items)
        if kind in ("step", "boss"):
            return add_feature_diameter(self, feature, self._part_model, ctx=ctx)
        if kind in _MACHINED_CALLOUT_KINDS:
            # Machined callouts render through their auto-pass renderer, restricted to THIS
            # feature (only={feature}) so a live call draws exactly one callout — the per-feature
            # `only=` subset the finalize stages also use. The deferred path above routes the
            # recorded intent to the matching per-kind finalize stage instead.
            if self._part_model is None or self._analysis is None:
                raise ValueError(
                    f"callout(): a {kind} callout needs the part model and analysis; "
                    "add it to a drawing built by build_drawing(), not a bare Drawing"
                )
            from draftwright.annotations.from_model import (
                render_chamfers,
                render_fillets,
                render_flats,
                render_grooves,
                render_pockets,
            )

            renderers = {
                "chamfer": render_chamfers,
                "fillet": render_fillets,
                "flat": render_flats,
                "pocket": render_pockets,
                "groove": render_grooves,
            }
            # Return the placed annotation's name (Codex #811) so pin()/drop() can address it.
            # only={feature} places exactly one callout, so at most one name changes. Diff by
            # object IDENTITY, not just the name set, so re-placing over an existing canonical
            # name (or a grouped callout collapsing to an already-present name) is still detected
            # as the placed name (Codex #811 r3). A drop (no clear room) changes nothing and
            # returns "" — the same empty-string drop signal the step/boss diameter branch gives.
            before = {n: id(o) for n, o in self.iter_annotations()}
            # Migrated renderers consume the compiled plan and select by opaque reference.
            from draftwright.model.compiled import FeatureRef as _FR
            from draftwright.model.compiled import compile_dimensions as _cd3

            renderers[kind](
                self,
                _cd3(self._part_model),
                self._analysis,
                ctx=ctx,
                only={_FR(feature)},
            )
            changed = [n for n, o in self.iter_annotations() if before.get(n) != id(o)]
            return changed[0] if len(changed) == 1 else ""
        if kind == "pocket_pattern":
            # A pocket pattern renders through its own auto-pass renderer (grouped size/depth
            # callout + pitch dim(s)), restricted to THIS feature (#841 outcome 3). Unlike the
            # lone machined callouts it places furniture too, so several names change — return
            # the grouped-callout name (m_pocketpat*), the handle pin()/drop() address.
            if self._part_model is None or self._analysis is None:
                raise ValueError(
                    "callout(): a pocket-pattern callout needs the part model and analysis; "
                    "add it to a drawing built by build_drawing(), not a bare Drawing"
                )
            from draftwright.annotations.holes import render_pocket_patterns
            from draftwright.model.compiled import FeatureRef, compile_dimensions

            before = {n: id(o) for n, o in self.iter_annotations()}
            render_pocket_patterns(
                self,
                compile_dimensions(self._part_model),
                self._analysis,
                ctx=ctx,
                only={FeatureRef(feature)},
            )
            placed = [n for n, o in self.iter_annotations() if before.get(n) != id(o)]
            return next((n for n in placed if n.startswith("m_pocketpat")), "")
        if kind == "slot_pattern":
            # A slot pattern renders through its own auto-pass renderer (grouped SLOT W × L
            # callout + pitch dim(s)), restricted to THIS feature (#841). Like the pocket pattern
            # it places furniture too, so several names change — return the grouped-callout name
            # (m_slotpat*), the handle pin()/drop() address.
            if self._part_model is None or self._analysis is None:
                raise ValueError(
                    "callout(): a slot-pattern callout needs the part model and analysis; "
                    "add it to a drawing built by build_drawing(), not a bare Drawing"
                )
            from draftwright.annotations.holes import render_slot_patterns
            from draftwright.model.compiled import FeatureRef, compile_dimensions

            before = {n: id(o) for n, o in self.iter_annotations()}
            render_slot_patterns(
                self,
                compile_dimensions(self._part_model),
                self._analysis,
                ctx=ctx,
                only={FeatureRef(feature)},
            )
            placed = [n for n, o in self.iter_annotations() if before.get(n) != id(o)]
            return next((n for n in placed if n.startswith("m_slotpat")), "")
        return add_feature_callout(
            self, feature, self._part_model, self._analysis, view=view, name=name, ctx=ctx
        )

    def overall_height(self) -> list[str]:
        """Add the part's **overall height** — the one dimension with no feature to name.

        Every other add verb takes a feature, because every other dimension belongs to one.
        The overall height usually does too: a model with an `EnvelopeFeature` carries a
        `height` parameter, and `dimension(env, "length", role="height")` is the verb for it.

        A model WITHOUT one still gets an overall height — the compiler falls back to the
        bounding box, which is a decision only the compiler may make (`_compile_overall_height`).
        There is then no feature to record an intent against, so an intent-level script had no
        way to say "and the 46 mm overall height", and a generated script replayed without it,
        silently and lint-clean (#889).

        This verb is that line. It is deliberately NOT "draw it whenever the compiler approves
        one": `auto_dims=False` means the verbs are the whole drawing, so a dimension nobody
        recorded must not appear — record-then-finalize has to equal placing live.

        Returns the placed names (empty when the compiler withholds the height — a Z-turned
        part whose step chain already tiles it, or an X/Y rotational OD that conveys it).
        """
        model, a = self._part_model, self._analysis
        # BEFORE the deferred/live split, so both routes refuse identically — the shape #925
        # settled for `callout()`: a check on one side of that split makes the answer depend
        # on whether you are inside `deferred()`.
        if model is not None and any(f.kind == "envelope" for f in model.features):
            # This verb exists ONLY for the featureless fallback. On an enveloped model the
            # measurement already has a feature to name, and supporting both spellings gave
            # two: live, `overall_height()` then `dimension(env, …, role="height")` drew the
            # 30 mm height TWICE, while the reverse order and the deferred route drew it once
            # (`explicit_envelope_height` removes the overall ladder from the compile). Order-
            # dependent live and live ≠ deferred, from composing two public spellings of one
            # measurement — the "three spellings of pin" problem (#906) in miniature (#934
            # review). One measurement, one verb.
            raise ValueError(
                "overall_height(): this model declares an envelope, so its height has a "
                'feature to name — use dimension(envelope, "length", role="height"). This '
                "verb is for a model with NO envelope feature, where the height comes from "
                "the bounding box and there is nothing to name."
            )
        if self._defer_intents:  # #426: record, don't place — finalize() drains it
            self._intents.append(Intent("overall_height", None, {}))
            return []
        if model is None or a is None:
            raise ValueError("overall_height(): no detected model — build the drawing first")
        from draftwright._core import layout_frame
        from draftwright.annotations._common import drain_corridors
        from draftwright.annotations.from_model import ladder_plan_for, render_height_ladder
        from draftwright.model.compiled import compile_dimensions

        before = set(self.annotations())
        ctx = PlacementContext(registry=self._registry, coverage=self._coverage, items=self.items)
        # ONLY the overall height: the renderer also draws the step ladder, which is a
        # different intent with its own verb. The drain projects the plan with the same
        # helper, so the two routes cannot disagree about what was asked for (#934 review).
        plan = ladder_plan_for(compile_dimensions(model), step_height=False, overall=True)
        if plan.ladder("overall_height") is not None:
            render_height_ladder(
                self, plan, layout_frame(a), ctx=ctx, detail_view=self._build.detail_view
            )
            drain_corridors(ctx, self)
        return sorted(set(self.annotations()) - before)

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
        from draftwright.annotations._common import PlacementContext
        from draftwright.annotations.holes import add_feature_furniture

        ctx = PlacementContext(registry=self._registry, coverage=self._coverage, items=self.items)
        return add_feature_furniture(
            self, feature, self._part_model, self._analysis, view=view, ctx=ctx
        )

    def rotational(self, feature) -> list[str]:
        """Add a rotational part's **turned furniture** (#424/#426) — the overall OD
        dimension, the axis centrelines, and any concentric-bore leaders.

        The editable handle for the whole-model rotational renderer: where the
        per-feature verbs place callouts/locations, ``rotational`` draws the furniture
        the auto-pass synthesises for a part's ``RotationalFeature`` (a turned /
        cylindrical body). *feature* is the rotational feature from :meth:`model`.
        Placed by the shared :func:`render_rotational` — the same whole-model renderer
        the auto-pass runs, so a script-reconstructed drawing is byte-identical to the
        direct build (no ``only=`` subset, no positional-naming seam: the renderer
        names its own outputs ``dim_od`` / ``centerline_*`` / ``ldr_*``). Returns ``[]``.
        """
        if self._defer_intents:  # #426: record, don't place — finalize() drains it
            self._intents.append(Intent("rotational", feature, {}))
            return []
        from draftwright.annotations._common import PlacementContext
        from draftwright.annotations.from_model import render_rotational
        from draftwright.model.compiled import compile_dimensions

        ctx = PlacementContext(registry=self._registry, coverage=self._coverage, items=self.items)
        render_rotational(self, compile_dimensions(self._part_model), self._analysis, ctx=ctx)
        return []

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

        ctx = PlacementContext(registry=self._registry, coverage=self._coverage, items=self.items)
        return add_section(self, self._part_model, self._analysis, ctx=ctx)

    def locate(self, feature, *, axes=None, pin=False) -> list[str]:
        """Add datum-referenced **X/Y position dimensions** for a Z-axis hole/pattern
        (#418) — the location half of the feature-referenced **add** surface.

        Distinct from :meth:`dimension` (a feature's own intrinsic linear params): a
        location dim measures the *datum → feature-centre* offset, which no feature
        exposes as a parameter. *feature* is a hole/pattern from :meth:`model`; ``axes``
        selects the in-plane axes (default both — ``"x"`` above the plan view, ``"y"``
        above the side view). ``pin=True`` marks the placed dimensions as deliberate user
        edits: in deferred mode they still flow through the shared corridor solve, but
        survive/dedup as high-priority candidates and pin themselves once placed (#511).
        Each dim is tagged with *feature* so :meth:`drop` / :meth:`annotations_of` find it.
        In live mode, returns one placed name per distinct requested in-plane
        ordinate with a real offset. In deferred mode, records the intent and
        returns ``[]``; the names are created when the context finalizes.

        Raises ``ValueError`` if *feature* is not a Z-axis hole/pattern (side-drilled
        bores are placed by the auto-pass). A feature with no datum-referenced ref (a
        datum-less model or a concentric/on-datum bore) returns ``[]``. Live placement
        handles this feature alone; automatic/deferred rendering may coalesce truly
        coincident ordinates while retaining every semantic owner. Placed reasonably, not
        via the auto-pass's corridor solve (byte-identity is not a goal, #400 Ph2).
        """
        if self._defer_intents:  # #426: record, don't place — finalize() drains it
            self._intents.append(Intent("locate", feature, {"axes": axes, "pin": pin}))
            return []
        from draftwright.annotations._common import PlacementContext
        from draftwright.annotations.holes import add_feature_location

        ctx = PlacementContext(registry=self._registry, coverage=self._coverage, items=self.items)
        return add_feature_location(
            self, feature, self._part_model, self._analysis, axes=axes, pin=pin, ctx=ctx
        )

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
        """Classify the recorded placement intents by route — the classification half of
        :meth:`finalize` (#590 split). Pure comprehensions over ``self._intents`` (no placement,
        no mutation) deciding which auto-pass solver each intent drains through; the drain half
        of finalize consumes the returned :class:`_IntentRouting`."""
        from draftwright.annotations.sections import feature_hole_keys
        from draftwright.model import PartModel, plan_sections

        # The section plan (if a section was recorded) — the ONE plan reserved before the
        # callout carve sees its row (Coupling A) and rendered last (Phase 3b).
        _section = None
        if routable and any(it.kind == "section" for it in self._intents):
            assert a is not None and isinstance(model, PartModel)
            _section = plan_sections(model, feature_hole_keys(model, a))
        # Route through the auto-pass solvers when possible (else everything live-replays):
        #  - BOTH-axes locate → the ADR-0009 location corridor. An axes-restricted locate
        #    can't go through the per-feature filter, so it live-replays (#429).
        #  - hole/pattern CALLOUT → _annotate_holes' priority-drop/anchoring solve (the
        #    section row, if any, is reserved first below).
        #  - step/boss ø CALLOUT → render_diameters' row-below/column-left set-solve (Phase 4a).
        corridor_ids = {
            id(it)
            for it in self._intents
            if routable
            and it.kind == "locate"
            and it.kwargs.get("axes") is None
            # Z-plan holes only — render_locations places X/Y position dims. A side-drilled
            # (X/Y-axis) bore's location is a different pass (off_axis_loc_ids below).
            and getattr(getattr(it.feature, "frame", None), "axis", None) == "z"
        }
        # Side-drilled (X/Y-axis) hole locations (#133/#426): a separate whole-model pass
        # (_locate_off_axis_holes), placed at the shared drain like the Z-plan corridor —
        # not render_locations (Z-only, #133) and never live-replayed (add_feature_location
        # raises on non-Z; the intent is routed here before it can reach that verb).
        # NB: no ``axes is None`` guard, unlike corridor_ids — the off-axis pass ignores the
        # axes selector, so EVERY side-drilled locate (incl. a hand-written axes=… one) must
        # route here, else it would live-replay into add_feature_location's ValueError.
        off_axis_loc_ids = {
            id(it)
            for it in self._intents
            if routable
            and it.kind == "locate"
            and getattr(getattr(it.feature, "frame", None), "axis", None) in ("x", "y")
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
            and getattr(getattr(it.feature, "frame", None), "axis", None) in ("x", "y", "z")
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
            and getattr(getattr(it.feature, "frame", None), "axis", None) in ("x", "y", "z")
            and it.kwargs.get("param") == "length"
            and it.kwargs.get("role") == "step"
        }
        # SLOT/PAD dimension intents (#426 Phase 2b / #885) → render_slots' corridor
        # placement. Both record two size dims on one feature; routing either feature
        # regenerates its width + length. Slots also regenerate their historical datum
        # position; pads use a separate locate() intent for their two-axis location.
        # Both share the location corridor,
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
            and getattr(it.feature, "kind", None) in ("slot", "pad")
            and it.kwargs.get("param") == "length"
            and it.kwargs.get("role") in ("slot_width", "slot_length", "pad_width", "pad_length")
        }
        # Prismatic height-ladder intent. StepLevelFeature exposes one value per interior
        # level, but those rungs are a correlated chain whose witness bases leapfrog from
        # the previous placed tier. Treat one semantic dimension intent as "rebuild the
        # whole height ladder" through the existing auto-pass renderer, instead of trying
        # to flatten each rung into an independent corridor candidate.
        height_ladder_ids = {
            id(it)
            for it in self._intents
            if routable
            and it.kind == "dimension"
            and getattr(it.feature, "kind", None) == "step_level"
            and it.kwargs.get("param") == "length"
            and it.kwargs.get("role") in (None, "step_height")
        }
        overall_height_ids = {id(it) for it in self._intents if it.kind == "overall_height"}
        explicit_envelope_height = any(
            routable
            and it.kind == "dimension"
            and getattr(it.feature, "kind", None) == "envelope"
            and it.kwargs.get("param") == "length"
            and it.kwargs.get("role") == "height"
            for it in self._intents
        )
        # Prismatic step POSITIONS (#555) — like the height ladder, one semantic intent
        # means "rebuild all shoulders" through render_step_positions, not per-shoulder
        # span dims (multiple shoulders share role="step_position" and can't be picked
        # apart by the span resolver).
        step_position_ids = {
            id(it)
            for it in self._intents
            if routable
            and it.kind == "dimension"
            and getattr(it.feature, "kind", None) == "step_level"
            and it.kwargs.get("param") == "length"
            and it.kwargs.get("role") == "step_position"
        }

        # User-authored generic feature dimensions with pin/priority join the shared
        # corridor directly. Slot and turned-step length dimensions keep their specialized
        # routes above, because those renderers regenerate correlated measurements as a set.
        already_routed = len_ids | slot_ids | height_ladder_ids | step_position_ids
        user_dim_ids = {
            id(it)
            for it in self._intents
            if self._user_dim_uses_corridor(it, routable, already_routed)
        }
        # Rotational furniture intent (#424/#426): the whole-model render_rotational —
        # no per-feature subset, so just the id set; it drains at the "rotational" slot.
        rotational_ids = {id(it) for it in self._intents if routable and it.kind == "rotational"}
        # Machined-feature leader callout intents (#148): pocket/fillet/flat/chamfer/groove
        # callout()s (plate is a spanned dimension, routed via dimension(), not here). Bucketed
        # per kind so each drains at its own _PASS_SEQUENCE stage, restricted to the recorded
        # features via only= (per-feature, #811). The id union also joins `routed` so
        # live_replay skips these (they route through finalize).
        machined_ids_by_kind: dict = {}
        for it in self._intents:
            if routable and it.kind == "callout":
                k = getattr(it.feature, "kind", None)
                if k in _MACHINED_CALLOUT_KINDS:
                    machined_ids_by_kind.setdefault(k, set()).add(id(it))
        # Pocket-pattern callout()s (#841 outcome 3): one grouped callout + pitch furniture per
        # pattern. Drained at the pre-drain "pocket_patterns" _PASS_SEQUENCE slot (render places
        # the pitch dim directly and needs the strip room the post-drain machined callouts lack),
        # restricted to the recorded feature(s) via only=.
        pocket_pattern_ids = {
            id(it)
            for it in self._intents
            if routable
            and it.kind == "callout"
            and getattr(it.feature, "kind", None) == "pocket_pattern"
        }
        # Slot-pattern callout()s (#841): same as pocket patterns — one grouped callout + pitch
        # furniture, drained at the pre-drain "slot_patterns" _PASS_SEQUENCE slot, only=-restricted.
        slot_pattern_ids = {
            id(it)
            for it in self._intents
            if routable
            and it.kind == "callout"
            and getattr(it.feature, "kind", None) == "slot_pattern"
        }
        only_loc = {it.feature for it in self._intents if id(it) in corridor_ids}
        pinned_loc = {
            it.feature for it in self._intents if id(it) in corridor_ids and it.kwargs.get("pin")
        }
        only_callout = {it.feature for it in self._intents if id(it) in callout_ids}
        only_dia = {it.feature for it in self._intents if id(it) in dia_ids}
        only_len = {it.feature for it in self._intents if id(it) in len_ids}
        slot_feats = {it.feature for it in self._intents if id(it) in slot_ids}
        return _IntentRouting(
            section=_section,
            corridor_ids=corridor_ids,
            callout_ids=callout_ids,
            dia_ids=dia_ids,
            len_ids=len_ids,
            slot_ids=slot_ids,
            height_ladder_ids=height_ladder_ids,
            overall_height_ids=overall_height_ids,
            step_position_ids=step_position_ids,
            explicit_envelope_height=explicit_envelope_height,
            user_dim_ids=user_dim_ids,
            rotational_ids=rotational_ids,
            off_axis_loc_ids=off_axis_loc_ids,
            only_loc=only_loc,
            pinned_loc=pinned_loc,
            only_callout=only_callout,
            only_dia=only_dia,
            only_len=only_len,
            slot_feats=slot_feats,
            machined_ids_by_kind=machined_ids_by_kind,
            pocket_pattern_ids=pocket_pattern_ids,
            slot_pattern_ids=slot_pattern_ids,
        )

    def _user_dim_uses_corridor(self, it, routable, already_routed) -> bool:
        """Does a user-authored generic dimension intent join the shared corridor? The
        promoted predicate of :meth:`_classify_intents`' ``user_dim_ids`` set: a pin/priority
        dimension with a resolvable span and a corridor-side, not already claimed by a
        specialized route (``already_routed`` = ``len_ids | slot_ids | height_ladder_ids |
        step_position_ids``)."""
        if (
            not routable
            or it.kind != "dimension"
            or id(it) in already_routed
            or not (it.kwargs.get("pin") or it.kwargs.get("priority"))
            or it.kwargs.get("side", "above") not in ("above", "below", "left", "right")
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
          first-class candidates, ADR 0012);
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
        # Nothing recorded → nothing to replay (the live/auto-pass path). The corridor batch is
        # a per-run local built below from these intents (#639), so an empty intent list has no
        # pending placement work to strand.
        if not self._intents:
            return
        from draftwright.annotations._common import PlacementContext

        # Fresh per-run placement scratch, threaded to the passes rather than hung on the drawing
        # (ADR 0005 §2, #639): render_locations/_annotate_holes/drain_corridors register/read here.
        # A local — finalize is transactional (#647), so a raised drain rolls the drawing back and
        # the retry re-runs from a clean slate with a new ctx; no cross-call persistence needed.
        ctx = PlacementContext(
            registry=self._registry,
            coverage=self._coverage,
            items=self.items,
            part_model=self.model(),
            model_declared=self.model_declared,
            trace=self._build.trace,  # the finalize drain traces too (#736)
        )
        # The solve trace joins the transaction (#736 review): the recorder appends during
        # the drain, so a rolled-back finalize must also truncate those records — else the
        # trace (and a rewritten file) would describe placements that no longer exist.
        # Snapshot BEFORE opening the finalize phase: the phase counter is trace state too.
        trace_snap = ctx.trace.snapshot() if ctx.trace is not None else None
        if ctx.trace is not None:
            ctx.trace.begin_phase("finalize")

        model, a = self._part_model, self._analysis
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
        # too, else a raised drain leaves the retry solving against a stale cap (#647 review).
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
        """The mutating half of :meth:`finalize` (#638): the drain stages, keyed by the
        orchestrator's canonical ``_PASS_SEQUENCE`` and executed in ITS order via the shared
        :func:`~draftwright.annotations.orchestrator.run_stages` (#699 slice b) — so the
        finalize path can no longer silently diverge from the auto-pass sequencing (the
        pre-#699 hand-mirrored order drained the corridor BEFORE diameters/step lengths and
        registered the ladder before the callouts; both now follow the one list, giving the
        auto-pass's obstacle visibility). ``r`` = the :class:`_IntentRouting` from
        :meth:`_classify_intents`; finalize wraps this call in the snapshot/rollback/finally,
        so a raise here still rolls the drawing back as before."""
        from draftwright.annotations.from_model import (
            render_chamfers,
            render_diameters,
            render_fillets,
            render_flats,
            render_grooves,
            render_height_ladder,
            render_local_turned_centerlines,
            render_locations,
            render_pockets,
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
        from draftwright.annotations.orchestrator import (
            _maybe_tabulate_holes,
            drain_and_reconcile,
            run_stages,
        )
        from draftwright.annotations.sections import (
            _add_section_view,
            _request_prismatic_detail,
            _reserve_section_row,
            _resolve_details,
            feature_hole_keys,
        )
        from draftwright.model import PartModel, plan_dimensions
        from draftwright.model.compiled import compile_dimensions

        routable = model is not None and a is not None
        queued_dim_ids: set = set()

        def _report_authored_omissions(features, before) -> None:
            """Say so when a recorded edit drew nothing because the AUTHOR omitted it.

            The round-6 defect (#921) was silence: the drain recorded the intent, drew
            nothing, dropped the intent unconditionally and reported success, so the edit
            vanished with no annotation, no pending intent and no warning. #925 replaced the
            pre-check that fixed it — a fourth hand-written prediction of what a callout
            would draw, and wrong for a feature with some measurements authored and some not
            — so the report has to move here, where "drew nothing" is observed rather than
            forecast, and matches what the live path records at the same moment.
            """
            if not features or model is None or model.authored_dimensions is None:
                return
            drawn = set(self.annotations()) - before
            for feature in features:
                if drawn & set(self.annotations_of(feature)):
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
                    self._record_build_issue(
                        "info",
                        "authored_omission",
                        f"the recorded edit for this {feature.kind} drew nothing: "
                        f"{omission.parameter_id} is not in the authored dimension set — "
                        "add a dimension(feature, role) line",
                    )

        def _s_rotational():
            # Rotational furniture — OD dim + axis centrelines + concentric-bore leaders —
            # through the shared whole-model render_rotational (#424/#426). Runs FIRST (its
            # "rotational" _PASS_SEQUENCE slot), so on a turned STEPPED part it places before
            # the diameter/callout stages exactly as the auto-pass does. No only= subset and
            # fixed literal output names (dim_od/centerline_*) ⇒ byte-identical to the auto
            # pass, so the reconstruction matches (== not ⊇, unlike the #424 diameter case).
            if r.rotational_ids:
                assert a is not None and isinstance(model, PartModel)
                render_rotational(self, compile_dimensions(model), a, ctx=ctx)
            # Generated/deferred reconstruction starts with auto_dims=False, so
            # add a non-rotational stepped stack's local axis here before the
            # location stages suppress a centered bore as axis-located (#881).
            if routable and (r.dia_ids or r.len_ids or r.off_axis_loc_ids):
                assert a is not None
                render_local_turned_centerlines(self, a, ctx=ctx)
            self._intents = [it for it in self._intents if id(it) not in r.rotational_ids]

        def _s_reserve_section():
            # Reserve the section's cutting-plane row BEFORE the callout carve so the
            # carve sees it as an obstacle (Coupling A, ADR 0009 P5 strand 3); rendered
            # last (the "section" stage).
            if r.section is not None:
                assert a is not None
                _reserve_section_row(self, a, r.section, ctx=ctx)

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
            while i < len(self._intents):
                it = self._intents[i]
                if it.kind == "section" or id(it) in routed:
                    i += 1
                    continue
                self._replay_intent(it)  # resilient: a raise leaves the rest recorded
                self._intents.pop(i)

        def _s_hole_callouts():
            # Hole/pattern callouts through the REAL priority-drop/anchoring solve.
            # Furniture is owned by the replayed furniture() intents → place_furniture=False.
            before_callouts = set(self.annotations())
            if r.only_callout:
                assert a is not None and isinstance(model, PartModel)  # ⟹ routable
                _annotate_holes(
                    self,
                    a,
                    build_view_of_axis(a),
                    plan_dimensions(model),
                    feature_hole_keys(model, a),
                    ctx=ctx,
                    plan=compile_dimensions(model),
                    only=r.only_callout,
                    place_furniture=False,
                )
            _report_authored_omissions(r.only_callout, before_callouts)
            # Drop the placed callout intents NOW — before the fallible later stages — so
            # a raise there can't re-route (and, via first-free hc_ naming, duplicate)
            # them on a retry.
            self._intents = [it for it in self._intents if id(it) not in r.callout_ids]

        def _s_locations():
            # Both-axes locations register into the SHARED location corridor with slots,
            # step positions and the height ladder — one crossing-free ladder, one drain
            # (the "drain" stage), so a slot position coincident with a hole location
            # dedups (#345).
            if r.only_loc:
                assert a is not None and isinstance(model, PartModel)  # ⟹ routable
                render_locations(
                    self,
                    compile_dimensions(model),
                    a,
                    ctx=ctx,
                    only=r.only_loc,
                    pinned=r.pinned_loc,
                )

        def _s_off_axis_across():
            # Side-drilled holes' in-plane (side-below) locations — REGISTER-only, whole-model
            # (_locate_off_axis_holes takes the compiled plan's approved off-axis positions,
            # no only= subset); they place at the shared drain. Whole-model like the auto pass: commenting SOME dwg.locate lines
            # still redraws every side-drilled location; commenting them ALL empties the bucket.
            if r.off_axis_loc_ids:
                assert a is not None
                _locate_off_axis_holes(
                    self, ctx, a, which="across", plan=compile_dimensions(model)
                )

        def _s_off_axis_along():
            # Side-drilled (X/Y-axis) hole HEIGHT locations — register-only, placed at the drain
            # (mirrors the auto pass's off_axis_along stage; after the envelope candidates).
            if r.off_axis_loc_ids:
                assert a is not None
                _locate_off_axis_holes(self, ctx, a, which="along", plan=compile_dimensions(model))

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
                self,
                # Projected to what was RECORDED. Passing the whole compiled plan once either
                # intent was present meant `overall_height()` alone also rebuilt the step
                # rungs — a dimension nobody asked for, and live/deferred divergence in the
                # one change relying on their equivalence (#934 review).
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
                detail_view=self._build.detail_view,
            )

        def _s_step_positions():
            # Prismatic step positions (all shoulders) — registration-only, like the
            # ladder above; placed and dropped at the drain (#639).
            if r.step_position_ids:
                assert a is not None and isinstance(model, PartModel)
                from draftwright._core import layout_frame as _lf
                from draftwright.model.compiled import compile_dimensions as _cd2

                render_step_positions(self, _cd2(model), _lf(a), ctx=ctx)

        def _s_detail_request():
            # Prismatic step-height detail (#661): queue it exactly as the auto pass
            # does — gated on the build's persisted detail_view setting, firing only when
            # the ladder stage above recorded the "step"/"illegible" escalation
            # (_request_prismatic_detail's own check). Resolved in the "details" stage.
            if self._build.detail_view and routable:
                assert a is not None
                from draftwright.model.compiled import compile_dimensions as _cd

                _request_prismatic_detail(self, a, ctx=ctx, plan=_cd(model))

        def _s_diameters():
            # Step/boss ø diameters through render_diameters' set-solve (row-below /
            # column-left) — placed immediately, before the corridor drain, exactly as
            # the auto-pass runs it (#699 slice b — the old drain-first order gave the
            # deferred path different obstacle visibility).
            before_dia = set(self.annotations())
            if r.only_dia:
                assert a is not None and isinstance(model, PartModel)  # ⟹ routable
                from typing import cast as _cast_dia

                from draftwright.model.compiled import FeatureRef as _DiaRef
                from draftwright.model.compiled import compile_dimensions as _compile_dia
                from draftwright.model.ir import Feature as _DiaFeature

                assert all(isinstance(f, _DiaFeature) for f in r.only_dia)
                render_diameters(
                    self,
                    _compile_dia(model),
                    a,
                    ctx=ctx,
                    only={_DiaRef(_cast_dia(_DiaFeature, f)) for f in r.only_dia},
                )
            _report_authored_omissions(r.only_dia, before_dia)
            self._intents = [it for it in self._intents if id(it) not in r.dia_ids]

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
                    self,
                    _compile_len(model),
                    ctx=ctx,
                    only={_LenRef(_cast_len(_LenFeature, f)) for f in r.only_len},
                )
            self._intents = [it for it in self._intents if id(it) not in r.len_ids]

        def _s_slots():
            # Slots regenerate width + length + the model-derived datum position (a
            # superset of the recorded slot intents — auto-pass parity by design) and
            # register into the shared corridor. Planner-fed (#730): the width/length
            # values + tolerances come from the plan, like the auto-pass.
            if r.slot_feats:
                assert a is not None and isinstance(model, PartModel)  # ⟹ routable
                render_slots(self, compile_dimensions(model), a, ctx=ctx, only=r.slot_feats)

        # Machined-feature leader callouts (#148): each recorded callout intent draws exactly
        # its own feature — the renderer is restricted to the surviving intents' features via
        # only= (the render_slots #426 Ph2b subset idiom), so commenting one dwg.callout line
        # drops that one feature (Codex #811) while the full script reproduces the auto pass.
        # Each kind places directly at its own _PASS_SEQUENCE slot (after the drain). Plate is
        # NOT here — it is a spanned corridor dimension, not a direct leader (#811 review).
        def _s_machined(kind, render):
            ids = r.machined_ids_by_kind.get(kind, set())
            feats = {it.feature for it in self._intents if id(it) in ids}
            if feats:
                assert a is not None and isinstance(model, PartModel)  # ⟹ routable
                from typing import cast as _cast

                from draftwright.model.compiled import FeatureRef as _FR2
                from draftwright.model.compiled import compile_dimensions as _cd4
                from draftwright.model.ir import Feature as _Feature

                assert all(isinstance(f, _Feature) for f in feats)
                render(
                    self,
                    _cd4(model),
                    a,
                    ctx=ctx,
                    only={_FR2(_cast(_Feature, f)) for f in feats},
                )
            self._intents = [it for it in self._intents if id(it) not in ids]

        def _s_chamfers():
            _s_machined("chamfer", render_chamfers)

        def _s_fillets():
            _s_machined("fillet", render_fillets)

        def _s_flats():
            _s_machined("flat", render_flats)

        def _s_pockets():
            _s_machined("pocket", render_pockets)

        def _s_grooves():
            _s_machined("groove", render_grooves)

        def _s_pocket_patterns():
            # Pocket-pattern callouts + their pitch furniture (#841 outcome 3), restricted to the
            # recorded feature(s). Keyed "pocket_patterns" so run_stages fires it at that PRE-drain
            # _PASS_SEQUENCE slot — render_pocket_patterns places the pitch dim directly and needs
            # the strip room the post-drain machined callouts lack.
            feats = {it.feature for it in self._intents if id(it) in r.pocket_pattern_ids}
            if feats:
                assert a is not None and isinstance(model, PartModel)  # ⟹ routable
                from typing import cast as _cast_pp

                from draftwright.model.compiled import FeatureRef as _PPRef
                from draftwright.model.compiled import compile_dimensions as _compile_pp
                from draftwright.model.ir import Feature as _PPFeature

                assert all(isinstance(f, _PPFeature) for f in feats)
                render_pocket_patterns(
                    self,
                    _compile_pp(model),
                    a,
                    ctx=ctx,
                    only={_PPRef(_cast_pp(_PPFeature, f)) for f in feats},
                )
            self._intents = [it for it in self._intents if id(it) not in r.pocket_pattern_ids]

        def _s_slot_patterns():
            # Slot-pattern callouts + their pitch furniture (#841), restricted to the recorded
            # feature(s). Keyed "slot_patterns" so run_stages fires it at that PRE-drain
            # _PASS_SEQUENCE slot (same reason as pocket patterns).
            feats = {it.feature for it in self._intents if id(it) in r.slot_pattern_ids}
            if feats:
                assert a is not None and isinstance(model, PartModel)  # ⟹ routable
                from typing import cast as _cast_sp

                from draftwright.model.compiled import FeatureRef as _SPRef
                from draftwright.model.compiled import compile_dimensions as _compile_sp
                from draftwright.model.ir import Feature as _SPFeature

                assert all(isinstance(f, _SPFeature) for f in feats)
                render_slot_patterns(
                    self,
                    _compile_sp(model),
                    a,
                    ctx=ctx,
                    only={_SPRef(_cast_sp(_SPFeature, f)) for f in feats},
                )
            self._intents = [it for it in self._intents if id(it) not in r.slot_pattern_ids]

        def _s_user_dims():
            # User-authored pin/priority dimensions queue into the shared corridor as
            # first-class candidates (ADR 0012).
            used_dim_names: set[str] = set()
            for it in self._intents:
                if id(it) in r.user_dim_ids:
                    if self._queue_dimension_intent(it, a, ctx=ctx, used_names=used_dim_names):
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
                drain_and_reconcile(ctx, self)
            self._intents = [
                it
                for it in self._intents
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

        def _s_section():
            # Render the section, reusing the reserved plan (its room check clears
            # everything right of the side view; _add_section_view clears the
            # reservation). A recorded section with no trigger (r.section is None) is a
            # no-op.
            self._intents = [it for it in self._intents if it.kind != "section"]
            if r.section is not None:
                assert a is not None
                _add_section_view(self, a, r.section, ctx=ctx)

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
                # while the iso still stands at sheet scale (it is fitted into its
                # zone only after _auto_annotate returns), so the free-rectangle
                # search is not blocked by the grown iso. The finalize path inherits
                # the already-fitted iso from the build — re-project it at sheet
                # scale for the resolve, then refit it into its zone (deterministic:
                # same zone + geometry reproduce the build's fit; the #647 snapshot
                # covers views/_coords, so a raise mid-stage rolls the iso back too).
                if "iso" in self.views:
                    _project_iso(self, a, a.SCALE)
                _resolve_details(self, a, ctx=ctx)
                if "iso" in self.views:
                    _fit_iso_view(self, a, annotate=False)

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
                _maybe_tabulate_holes(self, a, ctx=ctx, plan=compile_dimensions(model))

        run_stages(
            {
                "rotational": _s_rotational,
                "off_axis_across": _s_off_axis_across,
                "off_axis_along": _s_off_axis_along,
                "reserve_section": _s_reserve_section,
                "live_replay": _s_live_replay,
                "hole_callouts": _s_hole_callouts,
                "locations": _s_locations,
                "height_ladder": _s_height_ladder,
                "step_positions": _s_step_positions,
                "detail_request": _s_detail_request,
                "diameters": _s_diameters,
                "step_lengths": _s_step_lengths,
                "slots": _s_slots,
                "pocket_patterns": _s_pocket_patterns,
                "slot_patterns": _s_slot_patterns,
                "user_dims": _s_user_dims,
                "drain": _s_drain,
                "chamfers": _s_chamfers,
                "fillets": _s_fillets,
                "flats": _s_flats,
                "pockets": _s_pockets,
                "grooves": _s_grooves,
                "section": _s_section,
                "details": _s_details,
                "tabulate": _s_tabulate,
            }
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

    def note(self, text, at, *, view=None, rotation=0.0, name=None, align=None):
        """Add a free-form text **note** at page position *at* — ``(x, y)`` in mm from the sheet
        origin, the space :meth:`at` / :meth:`view_bounds` return (#817).

        A note is user-positioned free text ("SEE NOTE 1", a general-tolerance line): it carries
        no feature and is not part of the placement solve, so — unlike :meth:`callout` /
        :meth:`dimension`, which the solve places — you give the position. Pass *view* to fold it
        into that view's block for the cross-view repack; ``rotation`` (degrees) and ``align``
        (a build123d ``Align`` pair, default centred on *at*) are forwarded to the note. Returns
        the annotation name. This is the public door for free text — the raw ``Note`` object +
        low-level placement primitive are internal."""
        from build123d import Align
        from build123d_drafting import Note

        n = Note(
            text,
            at,
            self.draft,
            rotation=rotation,
            align=align if align is not None else (Align.CENTER, Align.CENTER),
        )
        if name is None:
            i = 0
            while (name := f"note{i}") in self._registry:
                i += 1
        self._add(n, name, view=view)
        return name

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
        obstacles.extend(strip_obstacles(self))
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
        return self._add(table.locate(Location((pos[0], pos[1], 0))), name)

    def _hole_spec_groups(self, view):
        """Ordered ``(tag, [holes], count)`` spec-groups of *view*'s holes (tags A, B,
        …). The shared basis for the hole table's rows and its balloons, so the
        TAG column and the balloon glyphs line up.

        Sourced from the IR (``model.features``), so each group is one hole/pattern
        feature — a pattern and same-spec loose holes are distinct groups (ADR 0008;
        #584 WP1). Each ``holes`` element is a :class:`_HoleInstance` (one per member
        position, driving a balloon); ``count`` is the feature's declared/detected count
        for the table QTY — equal to ``len(holes)`` on the detected path."""
        model = self._part_model
        target = {"plan": "z", "front": "y", "side": "x"}.get(view)
        if model is None or target is None or view not in self._coords:
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

    def add_balloons(self, view, specs):
        """Place a leadered balloon for each ``(tag, j, hole)`` in *specs*,
        fitted into the halo the layout reserved around the view (#111).

        Public verb over the :mod:`draftwright.annotations.balloons` render pass
        (#699: the pass lives in the render layer; this owner method threads the
        build state in). Each hole is assigned to a reserved band — left, right,
        top or bottom — by a global max-cardinality/min-cost assignment (#516),
        each band is spread with the 1D strip solver, and a :class:`Leader` runs
        from the hole rim to each glyph.
        """
        if view not in self._coords or self._analysis is None:
            return
        ctx = PlacementContext(
            registry=self._registry,
            coverage=self._coverage,
            items=self.items,
            part_model=self._part_model,
        )
        render_balloons(self, self._analysis, view, specs, ctx)

    def _add_balloon(self, view, tag, j, hole):
        """Single-balloon convenience over :meth:`add_balloons` (#111)."""
        self.add_balloons(view, [(tag, j, hole)])

    def add_hole_table(self, view="plan", *, prefer="tr", name=None, balloons=True):
        """Add a hole table for *view*'s holes, placed in a free corner (#93).

        One row per hole spec-group — ``TAG | ⌀ | DEPTH | QTY`` with tags
        ``A, B, …`` — placed via :meth:`add_table`. With *balloons* (the
        default) a circled tag is added at each hole keyed to its row. The table
        carries the same semantic measurement and structured requirement provenance as
        automatic table escalation, so physical hole outcomes count only the facts the
        table visibly states. Returns the table, or ``None`` when *view* has no holes or it
        will not fit.
        """
        groups = self._hole_spec_groups(view)
        if not groups:
            return None
        rows = [("TAG", "⌀", "DEPTH", "QTY")]
        diams = []
        for tag, _owner, holes, count in groups:
            h = holes[0]
            depth = "THRU" if h.through else (_fmt(h.depth) if h.depth else "")
            rows.append((tag, f"ø{_fmt(h.diameter)}", depth, str(count)))
            # Legacy physical-diameter lint counts one structured entry per bore.
            # Repeat the value exactly as many times as the visible QTY asserts, just as
            # automatic escalation does, while the semantic ledger below retains the
            # feature-scoped grouping identity.
            diams.extend([h.diameter] * count)
        table_name = name or f"hole_table_{view}"
        table = self.add_table(rows, prefer=prefer, name=table_name)
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
            self._registry,
            table_name,
            measurements=measurements,
            requirements=requirements,
        )
        if balloons:
            self.add_balloons(
                view,
                [
                    (tag, j, h)
                    for tag, _owner, holes, _count in groups
                    for j, h in enumerate(holes)
                ],
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

    def _record_build_issue(self, severity, code, message):
        """Record a lint issue discovered during construction (e.g. an
        annotation the layout had to drop). Surfaced by :meth:`lint` so a
        dropped feature is never silent."""
        self._registry.record_issue(LintIssue(severity=severity, code=code, message=message))

    # -- repair ---------------------------------------------------------------
    def repair(self, max_iter: int = 3):
        """Close the lint→repair loop: act on violations, don't only report them.

        After the greedy initial placement, re-place the dimensions behind the
        mechanically-clear violations and re-lint, bounded to *max_iter* passes:

        - ``dim_inside_part`` — the offset is on the wrong side; flip it once.
        ``annotation_overlap`` is intentionally not repaired here anymore: the
        corridor/strip solvers own primary placement, and a fixed-step nudge would
        reintroduce a second placement policy.

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
    def lint(self, *, physical: bool = True):
        """Lint all annotations against all views; returns the list of issues.

        When :attr:`part` is set, also runs :func:`lint_feature_coverage`.
        Build-time drops recorded via :meth:`_record_build_issue` are included.

        ``physical=False`` asks for the **placement** critique only — geometry/standards
        checks over what is on the sheet — and skips the feature-coverage half that needs a
        recognition inventory of the solid. That is what the repair loop wants (it acts on
        ``dim_inside_part`` and nothing else, ADR 0002), and on a declared build it is the
        difference between exporting a drawing and recognising the part to no purpose
        (#1022). The default stays the full critique: a caller asking "is this drawing
        right?" means both halves.
        """
        return self._lint(physical=physical, aggregation=_current_issue_aggregation())

    def _lint(self, *, physical: bool = True, aggregation=None):
        """Internal lint path with an optional summary-scoped pair ledger (#1147)."""
        # Drawable area (page minus the standard margin), passed explicitly to
        # lint_drawing for bounds checks — draftwright owns linting now and no
        # longer relies on the helpers set_page module-global (ADR 0007).
        page_bbox = (_MARGIN, _MARGIN, self.page_w - _MARGIN, self.page_h - _MARGIN)
        view_shapes = [vis for vis, _ in self.views.values()]
        # Prune box-cache entries for objects no longer on the sheet, so replaced
        # annotations (repair's _replace_dim swaps in a fresh object) don't keep
        # their OCC geometry strongly referenced for the drawing's lifetime.
        live = {id(i) for i in self.items} | {id(v) for v in view_shapes}
        for stale in [k for k in self._ann_box_cache if k not in live]:
            del self._ann_box_cache[stale]
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
                ann_box_cache=self._ann_box_cache,
                _aggregation=aggregation,
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
                    ann_box_cache=self._ann_box_cache,
                    _aggregation=aggregation,
                )
        if self.part is not None and physical:
            # Reuse the single feature inventory from the build (#244) when present,
            # so lint does not re-detect holes/patterns/turned-steps; fall back to
            # detecting when there is no analysis (a manually-built Drawing, or lint
            # called mid-build before _analysis is attached).
            a = self._analysis
            holes: list | None
            patterns: list | None
            bosses: list | None
            pads: list | None
            pockets: list | None
            recognition: RecognitionResult | None
            prof_kw: dict
            if a is not None and a.recognition is not None:
                cyls = a.cyls
                holes, patterns, bosses = a.holes, a.patterns, a.bosses
                pads, pockets = a.pads, a.pockets
                # The whole aggregate, not a level set: coverage projects the shared riser
                # evidence over recognition's OWN levels, so no caller can narrow the
                # shoulder inventory (#1025).
                recognition = a.recognition
                prof_kw = {"prof": a.prof}
            elif a is not None:
                # A DECLARED build recognised nothing (#1022), so `a.holes` and friends are
                # empty because nothing looked — not because the part has none. Feeding that
                # emptiness to coverage would report every real hole as uncovered, so critique
                # recognises here instead: once per drawing, owned by BuildState.
                rec = self._build.ensure_recognition(self.part)
                cyls = rec.cylinders
                holes = list(rec.holes)
                patterns = list(rec.hole_patterns)
                bosses = list(rec.bosses)
                pads = list(rec.pads)
                pockets = list(rec.pockets)
                # The aggregate, NOT anything off `Analysis`: its levels and risers are
                # geometry-sourced, where the declared `Analysis` carries what the author
                # declared. Critique taking its inventory from the model is what ADR 0015
                # forbids — it would make lint blind to exactly the geometry a sparse
                # declaration omitted, the case `unrecognised_defining_geometry` reports.
                recognition = rec
                # `a.prof` IS declaration-sourced, and here that is right rather than a
                # shortcut: axial critique judges the declared profile's dimensioning, so a
                # declared turned part keeps it without the aggregate.
                prof_kw = {"prof": a.prof}
            else:
                if self._cyl_cache is None:
                    self._cyl_cache = analyse_cylinders(self.part)
                cyls = self._cyl_cache
                holes = patterns = bosses = None
                pads = pockets = recognition = None
                prof_kw = {}
            if recognition is None:
                recognition = self._build.ensure_recognition(self.part)
            profiled_bores = list(recognition.double_d_bores)
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
            model = self._part_model
            # Turned bosses/bands remain in the OD + axial-step policy; this check is
            # specifically for a prismatic boss's independent projection height.
            if model is not None and (a is None or (not a.is_rotational and a.prof is None)):
                issues += lint_boss_height_coverage(
                    self.part,
                    self,
                    getattr(model, "features", ()),
                    assembly=self.assembly,
                )
            issues += lint_location_coverage(
                self.part,
                self,
                cyls=cyls,
                assembly=self.assembly,
                holes=holes,
                patterns=patterns,
                profiled_bores=profiled_bores,
            )
            issues += lint_prismatic_coverage(
                self.part,
                self,
                assembly=self.assembly,
                pads=pads,
                pockets=pockets,
                bbox=a.bb if a is not None else None,
                features=getattr(model, "features", ()) if model is not None else (),
                recognition=recognition,
            )
            resolved_assembly = self.assembly
            if resolved_assembly is None:
                resolved_assembly = len(self.part.solids()) > 1
            profile_cache = self._build.principal_profile_cache
            if (
                profile_cache is None
                or profile_cache[0] is not self.part
                or profile_cache[1] != resolved_assembly
            ):
                profile_issues = tuple(
                    lint_principal_profile_coverage(
                        self.part,
                        assembly=resolved_assembly,
                    )
                )
                profile_cache = (self.part, resolved_assembly, profile_issues)
                self._build.principal_profile_cache = profile_cache
            issues += list(profile_cache[2])
            issues += lint_profiled_bore_coverage(
                self.part,
                self.items,
                recognition=recognition,
                dropped_profiles=self._coverage.dropped_profiles,
                assembly=self.assembly,
            )
            issues += lint_flat_coverage(
                self.part,
                recognition=recognition,
                features=getattr(model, "features", ()) if model is not None else (),
                registry=self._registry,
                omissions=self._build.omissions,
                assembly=self.assembly,
            )
            issues += lint_slot_coverage(
                self.part,
                recognition=recognition,
                features=getattr(model, "features", ()) if model is not None else (),
                registry=self._registry,
                omissions=self._build.omissions,
                assembly=self.assembly,
            )
            issues += lint_hole_coverage(
                self.part,
                recognition=recognition,
                features=getattr(model, "features", ()) if model is not None else (),
                registry=self._registry,
                omissions=self._build.omissions,
                assembly=self.assembly,
            )
            issues += lint_channel_coverage(
                self.part,
                recognition=recognition,
                features=getattr(model, "features", ()) if model is not None else (),
                registry=self._registry,
                omissions=self._build.omissions,
                assembly=self.assembly,
            )
            issues += lint_polygonal_stock_coverage(
                self.part,
                recognition=recognition,
                features=getattr(model, "features", ()) if model is not None else (),
                registry=self._registry,
                omissions=self._build.omissions,
                assembly=self.assembly,
            )
            issues += lint_declared_gear_coverage(
                features=getattr(model, "features", ()) if model is not None else (),
                registry=self._registry,
                profiles=getattr(recognition, "repeating_radial_profiles", None),
                assembly=self.assembly,
            )
            # Reverse direction (#487): a DECLARED feature with no matching geometry (a stale
            # phantom callout). Only for a caller-supplied model — detection can't over-declare.
            # _part_model is typed `object` (deliberately loose, #397); read features duck-typed.
            if self._model_declared and self._part_model is not None:
                features = getattr(self._part_model, "features", ())
                issues += lint_declaration_reconciliation(
                    features,
                    cyls,
                    recognition=recognition,
                )
        if physical and self._analysis is not None:
            issues += lint_pmi_ignored(
                self._analysis.pmi_report,
                self._analysis.pmi_mode,
                defaulted=self._analysis.pmi_defaulted,
            )
            issues += lint_pmi_extraction(self._analysis.pmi_report, self._analysis.pmi_mode)
            issues += lint_pmi_lowering(
                self._analysis.pmi_report,
                getattr(self._part_model, "features", ()),
                self._analysis.pmi_mode,
            )
            issues += lint_pmi_rendering(
                getattr(self._part_model, "features", ()),
                self._registry,
                self._analysis.pmi_mode,
            )
        issues += list(self._registry.issues)
        # Attach a ready-to-paste fix snippet where one is computable (#29).
        # str | None — None when no concrete repair can be inferred.
        for i in issues:
            i.suggestion = _suggest_fix(i, self)
        return issues

    def lint_summary(self) -> dict:
        """Aggregate :meth:`lint` into a JSON-friendly diagnostic summary.

        Gives a non-interactive caller (a script, or an LLM via the API) structured
        diagnostics and independently inspectable components without rendering the SVG:

        - ``passed`` — no error-severity issues;
        - ``score`` — legacy coarse 0–1 diagnostic heuristic (see ``_SCORE_*``);
        - ``diagnostic_score`` — the same value under its honest name;
        - ``quality`` — separable completeness, restraint, and legibility components. No
          composite drawing-quality score is manufactured (#1127). Legibility's existing
          severity/code counts are raw findings; its ``primary_*`` counts and scalar group
          producer-identified pair findings by annotation and failure mechanism (#1147);
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
        with _collect_issue_aggregation() as aggregation:
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
        pmi = (
            pmi_stage_summary(
                self._analysis.pmi_report,
                getattr(self._part_model, "features", ()),
                self._registry,
                self._analysis.pmi_mode,
            )
            if self._analysis is not None
            else None
        )
        quality = quality_components(
            recognition=self._build.recognition,
            features=getattr(self._part_model, "features", ()),
            registry=self._registry,
            omissions=self._build.omissions,
            issues=issues,
            error_penalty=_SCORE_ERROR_PENALTY,
            warning_penalty=_SCORE_WARNING_PENALTY,
            _aggregation=aggregation,
        )
        return {
            "passed": errors == 0,
            "score": score,
            "diagnostic_score": score,
            "quality": quality,
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
                    **({"source_ids": i.source_ids} if i.source_ids else {}),
                    **(
                        {"outcome_stage": i.outcome_stage}
                        if getattr(i, "outcome_stage", None) is not None
                        else {}
                    ),
                }
                for i in issues
            ],
            **({"pmi": pmi} if pmi is not None else {}),
        }

    # The output formats export() understands. PDF renders from the SVG, PNG from the PDF —
    # so requesting pdf/png writes the SVG (and pdf) as intermediates, cleaned up if not asked for.
    _EXPORT_FORMATS = ("svg", "dxf", "pdf", "png")

    def _lint_and_log(self) -> None:
        issues = self.lint()
        if issues:
            _log.warning("Lint issues:")
            for iss in issues:
                _log.warning("  [%s] %s: %s", iss.severity, iss.code, iss.message)
        else:
            _log.info("Lint: OK")

    def _write_svg(self, out: str) -> str:
        """Write the SVG (part/hidden/dims layers, page-size fix, arc sanitise, hyperlink +
        metadata) and return its path. The PDF and PNG renders both read this SVG."""
        blk = Color(0, 0, 0)
        grey = Color(0.5, 0.5, 0.5)
        blue = Color(0, 0.2, 0.7)
        svg_exp = ExportSVG(margin=10)
        svg_exp.add_layer("part", line_color=blk, line_weight=0.5)
        svg_exp.add_layer("hidden", line_color=grey, line_weight=0.25, line_type=LineType.HIDDEN)
        svg_exp.add_layer("dims", line_color=blue, fill_color=blue, line_weight=0.05)
        self._add_shapes(svg_exp)
        svg_path = out + ".svg"
        svg_exp.write(svg_path)
        fix_svg_page_size(svg_path, self.page_w, self.page_h)
        n_arcs = sanitize_svg_arcs(svg_path)
        if n_arcs:
            _log.info("Rewrote %d degenerate (near-zero-radius) arc(s) as line segments", n_arcs)
        link_rect = getattr(self.get_annotation("title_block"), "draftwright_link_rect", None)
        if link_rect is not None:
            add_svg_hyperlink(svg_path, link_rect)
        add_svg_metadata(svg_path)
        _log.info("SVG → %s", svg_path)
        return svg_path

    def _write_dxf(self, out: str) -> str:
        """Write the DXF (part/hidden/dims layers + metadata) and return its path."""
        dxf_exp = _DraftwrightDXF()
        dxf_exp.add_layer("part", line_weight=0.5)
        dxf_exp.add_layer("hidden", line_weight=0.25)
        dxf_exp.add_layer("dims", line_weight=0.05)
        self._add_shapes(dxf_exp)
        set_dxf_metadata(dxf_exp)
        dxf_path = out + ".dxf"
        # #602: skip ExportDXF.write's O(entities) zoom.extents pass — the page window is known.
        write_dxf(dxf_exp, dxf_path, self.page_w, self.page_h)
        _log.info("DXF → %s", dxf_path)
        return dxf_path

    def export(
        self, out=None, *, formats=None, svg=None, dxf=None, dpi: int = 150
    ) -> dict[str, str] | tuple[str | None, str | None]:
        """Lint, then write the requested output *formats*; return ``{format: path}``.

        *formats* is a format name or an iterable from ``("svg", "dxf", "pdf", "png")``. PDF
        renders from the SVG and PNG from the PDF, so the SVG/PDF are written as intermediates
        and removed when not themselves requested. *dpi* sets the PNG raster resolution.

        Omitting *formats* — or passing ``None``, which is indistinguishable from omitting it
        — does **not** default to ``("pdf",)``; that is :meth:`Sheet.export`'s default. Here it
        selects the deprecated legacy path below, which writes SVG + DXF and returns a tuple.

        Legacy (deprecated in 0.3.1, **removed in 0.5.0**): the boolean ``svg=``/``dxf=``
        keywords — and calling ``export()`` with no ``formats`` — select those two vector
        formats and return the old ``(svg_path, dxf_path)`` tuple. Prefer ``formats=[...]``
        (the dict API); ``export_pdf`` is likewise superseded by ``export(formats=("pdf",))``.

        Both legacy shapes now **warn** (#987). They were listed under "Deprecated" in the
        v0.3.1 changelog and then said nothing at runtime for four minor releases, which made
        the planned 0.5.0 removal a silent break — and invisible to
        ``tests/test_deprecation_dates.py``, which can only scan things that warn. A
        deprecation nobody is warned about is documentation, not a deprecation.
        """
        self.finalize()  # #426: drain any recorded intents before export (no-op if none)
        out = out if out is not None else self.out
        for _ext in self._EXPORT_FORMATS:
            if out.endswith("." + _ext):
                out = out[: -(len(_ext) + 1)]
                break
        # Normalise ONCE, here, before anything reads it. `formats` may be a one-shot iterable,
        # and the mixed-API warning below used to build its message with `tuple(formats)` —
        # which consumed a generator, leaving the export loop nothing to iterate: it warned
        # that it would write SVG+DXF and then wrote nothing (Codex #987 r4). Normalising early
        # also makes the message say `('svg',)` rather than `('s', 'v', 'g')` for `formats="svg"`.
        want: list[str] | None = None
        if formats is not None:
            want = [formats.lower()] if isinstance(formats, str) else [f.lower() for f in formats]
            # Validate BEFORE the deprecation warning below. A caller who mistyped a format has
            # a broken call, not a deprecated one — and under `-W error` a warning raised first
            # would surface the deprecation instead of the typo that actually stopped the
            # export. Report the fault that matters.
            unknown = [f for f in want if f not in self._EXPORT_FORMATS]
            if unknown:
                raise ValueError(
                    f"unknown export format(s) {unknown}; choose from {self._EXPORT_FORMATS}"
                )
            # Beside the format check, not down at the PNG render: EVERY reason this call
            # cannot succeed belongs before the work, or the "validate first" rule holds for
            # whichever argument someone remembered (Codex #1029 r2).
            if "png" in want and dpi <= 0:
                raise ValueError(f"png export needs dpi > 0, got {dpi}")

        # AFTER validation, for the same reason validation precedes the deprecation warning
        # above: a call that is about to raise should not first do the work. On a declared
        # drawing this critique builds the recognition aggregate (#1022), so a mistyped format
        # used to scan the whole solid and only then report the typo.
        self._lint_and_log()

        # `formats=` wins over the legacy booleans, which means `export(out, formats=("svg",),
        # svg=False)` writes the SVG the caller just switched off — silently, since the legacy
        # branch below never runs. A caller passing both has a stale mental model, and a
        # deprecated argument that is ignored WITHOUT a word is the exact failure this change
        # exists to fix (#987). Say so; `formats` still wins.
        if want is not None and (svg is not None or dxf is not None):
            _ignored = [f"{k}=" for k, v in (("svg", svg), ("dxf", dxf)) if v is not None]
            warnings.warn(
                f"Drawing.export(): {', '.join(_ignored)} is deprecated and is IGNORED when "
                f"formats= is given — this call writes formats={tuple(want)!r}. Drop it, or "
                "put the format in formats=. Removed in 0.5.0.",
                DeprecationWarning,
                stacklevel=2,
            )

        # --- legacy path: svg=/dxf= keywords → the old (svg, dxf) tuple (back-compat) ---
        if formats is None:
            # Two distinct legacy shapes, warned separately because the fix differs (#987):
            # the booleans SELECT formats, while bare export() is the old DEFAULT whose return
            # type is a tuple. Callers of the first want `formats=[...]`; callers of the second
            # additionally have to stop unpacking two values.
            if svg is not None or dxf is not None:
                # Name the formats THIS call selected, not a fixed ('svg', 'dxf') pair: the
                # booleans can deselect, so `export(out, svg=False, dxf=True)` writes DXF only
                # and a canned suggestion would tell the caller to start writing an SVG they
                # had switched off — advice that changes behaviour (Codex #987 r1).
                _wanted = tuple(
                    f for f, on in (("svg", svg is None or svg), ("dxf", dxf is None or dxf)) if on
                )
                warnings.warn(
                    f"Drawing.export(svg=…, dxf=…) is deprecated; pass formats={_wanted!r} and "
                    "read the {format: path} dict. Removed in 0.5.0.",
                    DeprecationWarning,
                    stacklevel=2,
                )
            else:
                warnings.warn(
                    "Drawing.export() with formats= omitted or None returns the legacy "
                    "(svg, dxf) tuple and is deprecated; pass formats=(...) and read the "
                    "{format: path} dict — e.g. export(out, formats=('svg', 'dxf')). "
                    "Removed in 0.5.0.",
                    DeprecationWarning,
                    stacklevel=2,
                )
            svg_path = self._write_svg(out) if (svg is None or svg) else None
            dxf_path = self._write_dxf(out) if (dxf is None or dxf) else None
            self.svg_path, self.dxf_path = svg_path, dxf_path
            return svg_path, dxf_path

        # --- formats=... → {format: path} (requested order); normalised + validated above ---
        assert want is not None  # formats is not None on this branch
        want_set = set(want)
        paths: dict[str, str] = {}
        # Intermediates — the SVG behind a PDF/PNG, the PDF behind a PNG — go to a temp dir when
        # not themselves requested, NEVER the user's <out>.svg/.pdf. Otherwise a later
        # `export(out, formats="png")` would overwrite then delete an <out>.svg/.pdf an earlier
        # export wrote (#677 review). The temp dir + its contents are removed when the stack closes.
        with contextlib.ExitStack() as stack:
            tmpdir: str | None = None

            def _intermediate_stem() -> str:
                nonlocal tmpdir
                if tmpdir is None:
                    tmpdir = stack.enter_context(
                        tempfile.TemporaryDirectory(prefix="draftwright-export-")
                    )
                return os.path.join(tmpdir, "intermediate")

            svg_path = None
            if want_set & {"svg", "pdf", "png"}:
                svg_path = self._write_svg(out if "svg" in want_set else _intermediate_stem())
            self.svg_path = svg_path if "svg" in want_set else None
            if "svg" in want_set:
                paths["svg"] = svg_path  # type: ignore[assignment]

            self.dxf_path = None
            if "dxf" in want_set:
                self.dxf_path = paths["dxf"] = self._write_dxf(out)

            pdf_path = None
            if want_set & {"pdf", "png"}:
                assert svg_path is not None
                pdf_path = (out if "pdf" in want_set else _intermediate_stem()) + ".pdf"
                _render_pdf(
                    svg_path,
                    pdf_path,
                    getattr(self.get_annotation("title_block"), "draftwright_link_rect", None),
                )
                _log.info("PDF → %s", pdf_path)
                if "pdf" in want_set:
                    paths["pdf"] = pdf_path

            if "png" in want_set:
                assert pdf_path is not None
                paths["png"] = out + ".png"
                _render_png(pdf_path, paths["png"], dpi=dpi)
                _log.info("PNG → %s", paths["png"])
        return {f: paths[f] for f in want}

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

    def _add_shapes(self, exporter):
        """Add every view layer and annotation to *exporter* with error context."""
        for name, (vis, hid) in self.views.items():
            _export_shape(exporter, vis, "part", f"view {name!r}")
            if hid:
                _export_shape(exporter, hid, "hidden", f"view {name!r}")
        names = {id(annotation): name for name, annotation in self.iter_annotations()}
        for ann in self.items:
            identity = names.get(id(ann)) or getattr(ann, "label", "") or type(ann).__name__
            _export_shape(exporter, ann, "dims", f"annotation {identity!r}")
