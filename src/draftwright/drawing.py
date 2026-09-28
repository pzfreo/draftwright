"""The Drawing result object + table builder (#138 / ADR 1 (was 0005), P6).

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
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass, replace
from dataclasses import field as dataclasses_field
from typing import TYPE_CHECKING, Any, Literal, NamedTuple

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

from build123d import (
    Align,
    Location,
)
from build123d_drafting.helpers import DEFAULT_FONT_PATH

from draftwright._core import (
    Analysis,
    SheetMargins,
    _analysis_margins,
    _build_table,
    _dim,
    _fmt,
    _font_safe_text,
    _frame_margins,
    _log,
    _tag_sequence,
    _text_line_spacing_em,
    _tol_suffix,
    place_annotation,
)
from draftwright._geometry import _END_ON
from draftwright.annotations._common import (
    PlacementContext,
    _register_hole_table_coverage,
    carve_free_position,
    late_furniture_obstacles,
)
from draftwright.annotations.balloons import render_balloons
from draftwright.auxiliary_layout import fit_auxiliary_box
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
from draftwright.intent_drain import IntentDrainState, drain_intents
from draftwright.intent_routing import _IntentRouting, classify_intents
from draftwright.intents import Intent
from draftwright.layout import FitBoxTrace
from draftwright.linting import (
    CoverageState,
    LintIssue,
)
from draftwright.linting.evidence import compiled_display_precisions
from draftwright.linting.issues import _collect_issue_aggregation, _current_issue_aggregation
from draftwright.linting.orchestration import LintContext, lint_finished_drawing
from draftwright.projection import (
    part_material_mesh,
    project_view_geometry,
    view_material_field,
)
from draftwright.recognition_cache import RecognitionCache
from draftwright.registry import AnnotationRegistry
from draftwright.repair import repair_drawing
from draftwright.view_plan import PRINCIPAL_VIEW_NAMES, VIEW_AXES

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
# result task-local for the duration of the assessment; Drawing exposes mutable annotations,
# so a persistent generation cache needs a wider mutation contract (follow-on #1945).
_SCOPED_LINT: ContextVar[tuple[object, tuple, object] | None] = ContextVar(
    "draftwright_scoped_lint", default=None
)


def lint_snapshot(drawing):
    """Return issues and summary from one lint without exposing a mutable cache scope."""
    # A nested assessment must not expose its outer snapshot to lint overrides that
    # call lint_summary() while the inner public lint method is still running.
    mask = _SCOPED_LINT.set(None)
    try:
        with _collect_issue_aggregation() as aggregation:
            issues = tuple(drawing.lint())
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


#: Distinguishes "the mesh has not been attempted" from "it was attempted and failed".
#: ``None`` is a real, cacheable outcome here, so it cannot double as the unset marker.
_MATERIAL_MESH_UNSET = object()


@dataclass
class BuildState:
    """The build context a finished :class:`Drawing` carries (ADR 1 (was 0005 §2) / #639).

    One typed home for what used to be four loose private attributes:

    - ``analysis`` — the pipeline's :class:`Analysis` namespace.
    - ``part_model`` — the detected/declared ADR 1 (was 0008) PartModel (read surface for
      semantic edits, #397).
    - ``recognition`` — the ADR 3 (was 0017) aggregate reused by model detection and critique.
    - ``recognition_ownership`` — same-run represented, grouped/pattern, nested, conditional
      aggregate, and ownerless occurrence outcomes captured while conversion makes the decision;
      provider references never enter the IR waist.
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
    recognition_cache: RecognitionCache = dataclasses_field(default_factory=RecognitionCache)
    recognition_ownership: RecognitionOwnership | None = None
    part_model: object | None = None
    view_edge_cache: dict = dataclasses_field(default_factory=dict)
    ann_box_cache: dict = dataclasses_field(default_factory=dict)
    #: The title block's deterministic page-space footprint, measured before it is
    #: drawn so strip placement can avoid it (#1593). None until the builder sets it.
    pending_title_block_box: tuple | None = None
    #: Constructed title blocks shared by assembly and measured repack passes of this build.
    title_block_cache: dict = dataclasses_field(default_factory=dict)
    #: Imported document default selected as the title-block tolerance carrier. ``None``
    #: when the caller supplied any explicit value, even identical display text.
    general_tolerance_source: object | None = None
    #: Imported document-wide surface finish rendered as title-block furniture.
    default_surface_finish_source: object | None = None
    #: Per-view filled projected material (#798) as ``{id(view_shape): (shape, field)}``.
    #: Keyed by shape identity because the projected shapes carry no view label (lint
    #: takes their names from ``Drawing.views`` since #1196), and holding the shape
    #: alongside lets a reused ``id`` be detected — the same guard
    #: ``_view_edge_entries`` carries (#143).
    material_fields: dict = dataclasses_field(default_factory=dict)
    #: The one tessellation behind those fields, or ``None`` once it has been attempted
    #: and failed. Memoised separately so an unmeshable part is not re-meshed on every
    #: lint, and so views added by later stages can be lowered without redoing it.
    material_mesh: Any = _MATERIAL_MESH_UNSET
    principal_profile_cache: tuple[object, bool, tuple[LintIssue, ...]] | None = None
    trace: Any = None
    detail_view: bool = False
    #: The ADR 2 (was 0018) :class:`~draftwright.view_plan.ResolvedViewPlan` — which views this drawing
    #: has and where their blocks sit. ONE typed attachment, filled once by the builder at the
    #: same site it creates the views, because the alternative the ADR names explicitly is what
    #: the topology was before: the answer spread across `Analysis` fields, three hardcoded
    #: `_add_view` calls and a docstring, with no single thing to read or replace.
    view_plan: Any = None
    #: The compiler's :class:`~draftwright.model.compiled.Omission` records — every
    #: measurement it considered and did not approve, with the rule that stopped it (#996).
    #: The compiled plan was a local in the orchestrator: built, read by the renderers, and
    #: dropped. So the one place recording WHY a dimension is absent did not outlive the
    #: build, and absence had to be inferred from a finished sheet — which is how a wrong
    #: suppression rule produced four issue reports before anyone found the rule (#997).
    omissions: tuple = ()

    @property
    def recognition(self) -> RecognitionResult | None:
        """The immutable result held by Draftwright's consumer-owned lifecycle cache."""

        return self.recognition_cache.result

    @recognition.setter
    def recognition(self, value: RecognitionResult | None) -> None:
        self.recognition_cache.seed(value)
        self.recognition_ownership = None

    def attach_recognition(
        self,
        result: RecognitionResult | None,
        *,
        evidence: RecognitionEvidence | None = None,
        cache: RecognitionCache | None = None,
        ownership: RecognitionOwnership | None = None,
    ) -> None:
        """Attach one coherent acquisition at the builder's single fill site.

        A rebuilt drawing either receives the prior run's complete cache or a result/evidence
        pair from its current analysis. Mixing both sources would make run ownership ambiguous
        and therefore fails closed.
        """

        if cache is not None:
            if result is not None or evidence is not None or ownership is not None:
                raise ValueError("cannot attach both a recognition cache and a new acquisition")
            self.recognition_cache = cache
            self.recognition_ownership = None
            return
        if ownership is not None and ownership.evidence is not evidence:
            raise ValueError("recognition ownership and evidence must come from the same run")
        self.recognition_cache.seed(result, evidence=evidence)
        self.recognition_ownership = ownership

    @property
    def recognition_evidence(self) -> RecognitionEvidence | None:
        """Run-scoped provider evidence paired with :attr:`recognition`, when available."""

        return self.recognition_cache.evidence

    def clear_geometry_caches(self) -> None:
        """The one invalidation seam (finalize rollback): view edges + annotation
        boxes together — a rolled-back drawing must re-measure everything."""
        self.view_edge_cache.clear()
        self.ann_box_cache.clear()
        self.material_fields.clear()
        self.material_mesh = _MATERIAL_MESH_UNSET

    def ensure_recognition(self, part, *, cylinders=None) -> RecognitionResult:
        """The run's recognition aggregate, recognising *part* once if nothing has yet.

        A declared build performs no recognition (ADR 4 (was 0011) / #1022), so critique on that path
        has no inventory to judge against and must produce one.  It is built **here**, in the
        typed build state, and at most once per drawing: a lint-side or ``Drawing``-side memo
        would make critique a second recognition owner, contrary to ADR 3.

        On a detected build ``recognition`` is already filled by the builder, so this returns
        it and recognises nothing.
        """
        return self.recognition_cache.ensure(part, cylinders=cylinders)


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

        if self._model_declared:
            source = (
                getattr(self._analysis, "step_file", None) if self._analysis is not None else None
            )
            lint = self.lint_summary()
            report = declared_drawing_report(
                model=self.model(),
                lint=lint,
                source=source,
                registry=self._registry,
                drawing=self,
            )
            # Document sheets are authored views over one source-owned detected model.
            # Keep schema 8's declared layout evidence, but do not discard the exact
            # occurrence/requirement ledger that the document bound to this member.
            if (
                self.recognition_evidence() is not None
                and self.recognition_ownership() is not None
            ):
                snapshot = self.requirement_snapshot()
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
                    detail_decisions=tuple(self.detail_decisions),
                )
                report["recognition"] = source_report["recognition"]
            return report

        snapshot = self.requirement_snapshot()
        with _reuse_report_requirements(self, snapshot.outcomes, snapshot.dimension_plan):
            lint = self.lint_summary()
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
            detail_decisions=tuple(self.detail_decisions),
        )

    def requirement_snapshot(self, *, include_lint=False):
        """Capture live source-owned outcomes for single-sheet and document review.

        This reuses the report's exact-authority validation and existing producers.
        It neither recognizes geometry nor derives requirements from the compiled plan.
        The returned references belong to this build and must not be persisted or
        combined with another recognition run. Serialize edits and snapshot reads.
        """
        from draftwright.reporting import RequirementSnapshot, validate_report_inputs

        analysis = self._analysis
        source = getattr(analysis, "step_file", None) if analysis is not None else None
        evidence, ownership, model = validate_report_inputs(
            self.recognition_evidence(), self.recognition_ownership(), self.model()
        )
        from draftwright.linting.requirements import recognized_requirement_outcomes
        from draftwright.model.compiled import compile_dimensions

        dimension_plan = compile_dimensions(model)
        omissions = tuple(self._build.omissions)
        outcomes = recognized_requirement_outcomes(
            evidence.result,
            tuple(model.features),
            self.registry,
            omissions,
            dimension_plan=dimension_plan,
            part=self._working_part,
            evidence=evidence,
            ownership=ownership,
            datum=next((datum for datum in model.datums if datum.id == "datum_xy"), None),
        )
        lint = None
        if include_lint:
            with _reuse_report_requirements(self, outcomes, dimension_plan):
                lint = self.lint_summary()
        return RequirementSnapshot(
            evidence,
            ownership,
            model,
            source,
            self.registry,
            omissions,
            dimension_plan,
            self._working_part,
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

        return write_json_document(self.report(), path)

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
        _ortho = PRINCIPAL_VIEW_NAMES
        if view is not None and view not in _ortho:
            raise ValueError(
                f"view must be one of {_ortho}, not {view!r} (it foreshortens the span)"
            )
        parameters = feature.parameters()
        exact = [q for q in parameters if param in (q.parameter_id, q.discriminator)]
        matches = (
            [q for q in exact if role is None or q.role == role]
            if exact
            else [q for q in parameters if q.kind == param and (role is None or q.role == role)]
        )
        if not matches:
            r = f"/{role!r}" if role else ""
            raise ValueError(
                f"{type(feature).__name__} has no '{param}'{r} parameter to dimension"
            )
        if len(matches) > 1:
            ids = sorted(q.parameter_id for q in matches)
            raise ValueError(
                f"{type(feature).__name__} has {len(matches)} '{param}' params {ids} — pass "
                "role= or an exact parameter id/discriminator to choose one"
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
        automatic_views = tuple(name for name in _ortho if name in self.views)
        if view is None and getattr(feature, "kind", None) == "through_step":
            automatic_views = (_END_ON[feature.axis],)
        for v in [view] if view else automatic_views:
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

    def _resolve_dimension_side(self, feature, param, view, p1, p2, side):
        """Choose a feature's natural corridor when the caller leaves ``side`` implicit."""
        if side is not None:
            return side
        if getattr(feature, "kind", None) != "through_step":
            return "above"
        changed_axis = param.discriminator
        perpendicular = next(axis for axis in "xyz" if axis not in (feature.axis, changed_axis))
        outside = dict(feature.outside_directions)
        probe_world = [(a + b) / 2 for a, b in zip(param.span[0], param.span[1], strict=True)]
        probe_world["xyz".index(perpendicular)] += outside[perpendicular]
        exterior = self.at(view, *probe_world)
        if abs(p2[0] - p1[0]) >= abs(p2[1] - p1[1]):
            return "above" if exterior[1] > (p1[1] + p2[1]) / 2 else "below"
        return "right" if exterior[0] > (p1[0] + p2[0]) / 2 else "left"

    def _angular_dimension_plan(self, feature, options):
        """Compile a referential angle edit with the model's existing decorations."""
        from dataclasses import replace

        from draftwright.model.compiled import compile_dimensions
        from draftwright.model.ir import RequestedDimension

        parameters = feature.parameters()
        matches = [
            parameter
            for parameter in parameters
            if options["param"] == parameter.parameter_id
            or (options["param"] == "angle" and len(parameters) == 1)
        ]
        if len(matches) != 1 or options.get("role") not in (None, "included"):
            raise ValueError(
                "angle edit needs one included-angle measurement: "
                + ", ".join(parameter.parameter_id for parameter in parameters)
            )
        extra = set(options) - {"param", "role", "view", "side", "name", "pin", "priority"}
        if extra:
            raise ValueError(
                f"unsupported angular edit controls: {sorted(extra)}; declare content on Sheet"
            )
        model = self.model()
        if model is None or not any(owner is feature for owner in model.features):
            raise ValueError("angular edit must name an exact feature in the drawing model")
        request = RequestedDimension(
            feature,
            matches[0].parameter_id,
            view=options.get("view"),
            side=options.get("side"),
        )
        return compile_dimensions(
            replace(model, authored_dimensions=(request,), requested_dimensions=())
        )

    def _queue_dimension_intent(self, it, a, *, ctx, used_names=None) -> bool:
        """Queue a pinned/prioritized feature dimension into a shared corridor."""
        from draftwright.annotations._common import CorridorCandidate, register_corridor

        if getattr(it.feature, "kind", None) == "angle":
            from draftwright.annotations.from_model import render_angular_dimensions
            from draftwright.model.compiled import FeatureRef

            plan = self._angular_dimension_plan(it.feature, it.kwargs)
            name = it.kwargs.get("name")
            used_names = used_names if used_names is not None else set()
            if name is None:
                index = 0
                while (name := f"dim_angle{index}") in self._registry or name in used_names:
                    index += 1
            used_names.add(name)
            render_angular_dimensions(
                self,
                plan,
                a,
                ctx=ctx,
                only={FeatureRef(it.feature)},
                name=name,
                pin=bool(it.kwargs.get("pin")),
                priority=float(it.kwargs.get("priority") or 0.0),
            )
            return True

        side = it.kwargs.get("side")
        view = it.kwargs.get("view")
        zones_name = {
            "front": "fv_zones",
            "plan": "pv_zones",
            "side": "sv_zones",
            "rear": "rv_zones",
        }
        rec, view, p1, p2 = self._resolve_dimension_span(
            it.feature,
            it.kwargs["param"],
            role=it.kwargs.get("role"),
            view=view,
        )
        side = self._resolve_dimension_side(it.feature, rec, view, p1, p2, side)
        if side not in ("above", "below", "left", "right"):
            return False
        from draftwright.model.compiled import DimensionId

        measurement = DimensionId(it.feature, rec.parameter_id)
        measurement_span = rec.span or self._derive_span(it.feature, rec)
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
        # Match `_place_dim`: the deferred corridor path must keep authored tolerance in its
        # label even when `pin=True` or `priority=` selects this route.
        tolerance = dim_kwargs.pop("tolerance", None)
        if dim_kwargs.get("label") is None:  # `None` is "auto"; see `_place_dim`.
            page_len = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
            dim_kwargs["label"] = _fmt(page_len / self.scale)
        dim_kwargs["label"] = _font_safe_text(
            f"{dim_kwargs['label']}{_tol_suffix(tolerance, self.draft)}"
        )
        slot = it.kwargs.get("slot", 8.0)
        axis = "y" if side in ("above", "below") else "x"
        ax = 1 if axis == "y" else 0
        if side in ("right", "above"):
            natural = max(p[ax] for p in (p1, p2)) + slot
        else:
            natural = min(p[ax] for p in (p1, p2)) - slot
        tier = self.draft.font_size + 2 * self.draft.pad_around_text
        p_lo, p_hi = sorted((p1[1 - ax], p2[1 - ax]))

        def _build(
            pos,
            _p1=p1,
            _p2=p2,
            _side=side,
            _ax=ax,
            _kwargs=dim_kwargs,
            _measurement_span=measurement_span,
        ):
            if _side in ("right", "above"):
                dist = pos - max(p[_ax] for p in (_p1, _p2))
            else:
                dist = min(p[_ax] for p in (_p1, _p2)) - pos
            dim = _dim(_p1, _p2, _side, max(dist, 4.0), self.draft, **_kwargs)
            dim._dw_measurement_span = _measurement_span
            return dim

        def _placed(nm, _pin=it.kwargs.get("pin", False)):
            if _pin:
                self.pin(nm)

        def _drop(nm):
            self._record_build_issue(
                "warning",
                "dimension_dropped",
                f"{nm} not placed (no room on the {view} {side} strip)",
                measurement=measurement,
                measurement_span=measurement_span,
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
                measurement=measurement,
            ),
        )
        return True

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
        """Add a dimension for *feature*'s *param*, attributed to the feature (#398e).

        The feature-referenced **add** verb: pair to :meth:`drop`. *feature* is an IR
        feature from :meth:`model`; *param* is a **linear** parameter kind, exact parameter
        id, or discriminator it exposes — a turned step's ``"length"`` or a through step's
        ``"through_step_leg.length.x"``/``"x"`` (value-only slot geometry is derived here
        via :meth:`_derive_span`).
        The dimension is placed into free strip space and tagged with *feature*, so
        :meth:`drop` / :meth:`annotations_of` find it. Returns the annotation name.

        A feature may expose several params of one kind (an envelope's width/height/depth,
        or a slot's ``slot_width``/``slot_length``, are all ``"length"``); pass ``role=`` or
        an exact parameter id/discriminator to pick one — an ambiguous kind raises rather
        than guessing.

        ``view`` is chosen from the selected principal views (``"front"``/``"plan"``/
        ``"side"``/``"rear"``) where the span projects non-degenerate — a length along the turning
        axis vanishes in its end-on view, so the view follows the geometry. Through-step legs
        share their semantic axis end view and natural outside-corner sides. Pass ``view=``
        to select a principal explicitly (a non-orthographic view foreshortens the span and is
        rejected). An implicit ``side`` is ``"above"`` except for through-step legs, whose
        missing corner selects the natural outside corridor. ``kwargs`` forward to the dimension
        — except ``tolerance=``, which is folded into the label (see :meth:`place_dim`),
        because helpers discard a forwarded tolerance whenever a label is present.
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
        if getattr(feature, "kind", None) == "angle":
            options = dict(
                param=param,
                role=role,
                side=side,
                view=view,
                name=name,
                pin=pin,
                priority=priority,
                **kwargs,
            )
            self._angular_dimension_plan(feature, options)
            if name is None:
                index = 0
                while (name := f"dim_angle{index}") in self._registry:
                    index += 1
            with self.deferred():
                self.dimension(
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
            return name
        rec, view, p1, p2 = self._resolve_dimension_span(feature, param, role=role, view=view)
        side = self._resolve_dimension_side(feature, rec, view, p1, p2, side)
        from draftwright.model.compiled import DimensionId

        measurement = DimensionId(feature, rec.parameter_id)
        if name is None:
            i = 0
            while (name := f"dim_{param}{i}") in self._registry:
                i += 1
        annotation = self._place_dim(
            p1,
            p2,
            side,
            view,
            self.draft,
            name=name,
            feature=feature,
            measurement=measurement,
            **kwargs,
        )
        # A correlated ladder intentionally shares one public ``DimensionId`` across its
        # members. Preserve the compiler-owned world span at the public edit boundary so
        # completeness can tell which exact occurrence this visible replacement asserts.
        annotation._dw_measurement_span = rec.span or self._derive_span(feature, rec)
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
        linear param). A machined-feature callout
        (pocket/pad-height/circular-blind-step/fillet/blend/paired-ramp/flat/chamfer/groove) is
        auto-named and placed in its characteristic view by the kind's renderer, so
        ``view=``/``name=`` are unsupported for those kinds and raise ``ValueError`` rather
        than being silently ignored. Placed reasonably, not via the auto-pass's
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
        # A pre-check for ANY approved dimension cannot prove this callout has approved
        # content: a turned step can have its length authored and diameter omitted. The
        # renderer must decide from the compiled plan what it can draw.
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

        ctx = PlacementContext(
            registry=self._registry,
            coverage=self._coverage,
            items=self.items,
            document_member=self._document_member,
            document_source_annotation_ids=self._document_source_annotation_ids,
        )
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
                render_blends,
                render_chamfers,
                render_circular_blind_steps,
                render_circular_channels,
                render_fillets,
                render_flats,
                render_grooves,
                render_hex_pockets,
                render_oriented_slots,
                render_pad_heights,
                render_paired_ramp_steps,
                render_pockets,
                render_rectangular_blind_slots,
                render_round_bottom_blind_slots,
            )

            renderers = {
                "blend": render_blends,
                "chamfer": render_chamfers,
                "circular_blind_step": render_circular_blind_steps,
                "circular_channel": render_circular_channels,
                "hex_pocket": render_hex_pockets,
                "fillet": render_fillets,
                "paired_ramp_step": render_paired_ramp_steps,
                "flat": render_flats,
                "pocket": render_pockets,
                "rectangular_blind_slot": render_rectangular_blind_slots,
                "round_bottom_blind_slot": render_round_bottom_blind_slots,
                "oriented_slot": render_oriented_slots,
                "pad": render_pad_heights,
                "groove": render_grooves,
            }
            # Return the placed annotation's name so pin()/drop() can address it.
            # only={feature} places exactly one callout, so at most one name changes. Diff by
            # object IDENTITY, not just the name set, so re-placing over an existing canonical
            # name (or a grouped callout collapsing to an already-present name) is still detected
            # as the placed name. A drop (no clear room) changes nothing and
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
            # measurement. One measurement, one verb.
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
        ctx = PlacementContext(
            registry=self._registry,
            coverage=self._coverage,
            items=self.items,
            document_member=self._document_member,
            document_source_annotation_ids=self._document_source_annotation_ids,
        )
        # ONLY the overall height: the renderer also draws the step ladder, which is a
        # different intent with its own verb. The drain projects the plan with the same
        # helper, so the two routes cannot disagree about what was asked for.
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

        ctx = PlacementContext(
            registry=self._registry,
            coverage=self._coverage,
            items=self.items,
            document_member=self._document_member,
            document_source_annotation_ids=self._document_source_annotation_ids,
        )
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

        ctx = PlacementContext(
            registry=self._registry,
            coverage=self._coverage,
            items=self.items,
            document_member=self._document_member,
            document_source_annotation_ids=self._document_source_annotation_ids,
        )
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
        warranted or there is no room. Call it *after* the per-feature verbs — the room
        check carves the view row around whatever is already placed and takes the
        leftmost gap that fits, so it needs the occupancy to be complete. The outcome
        is recorded on :attr:`section_decision` either way (#1190).
        """
        if self._defer_intents:  # #426: record, don't place — finalize() drains it
            self._intents.append(Intent("section", None, {}))
            return []
        from draftwright.annotations.sections import add_section

        ctx = PlacementContext(
            registry=self._registry,
            coverage=self._coverage,
            items=self.items,
            document_member=self._document_member,
            document_source_annotation_ids=self._document_source_annotation_ids,
        )
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

        Circular channels also accept this verb: their X/Y/Z offsets locate the seat
        axis from the stock bounding-box minimum, and ``axes`` may select any subset
        of those three coordinates. They use the shared profile corridor solve.

        Raises ``ValueError`` for an unsupported feature (side-drilled
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

        ctx = PlacementContext(
            registry=self._registry,
            coverage=self._coverage,
            items=self.items,
            document_member=self._document_member,
            document_source_annotation_ids=self._document_source_annotation_ids,
        )
        if getattr(feature, "kind", None) == "circular_channel":
            from draftwright.annotations._common import drain_corridors
            from draftwright.annotations.from_model import render_circular_channel_locations
            from draftwright.model.compiled import compile_dimensions

            if self._part_model is None or not any(
                item is feature for item in self._part_model.features
            ):
                raise ValueError("locate(): feature is not from this drawing's model")
            before = set(self.annotations())
            render_circular_channel_locations(
                self,
                compile_dimensions(self._part_model, planned_views=tuple(self.views)),
                self._analysis,
                ctx=ctx,
                only={feature},
                pinned={feature} if pin else None,
                axes=axes,
            )
            drain_corridors(ctx, self)
            return [
                name
                for name in self.annotations()
                if name not in before and name.startswith("m_seatloc_")
            ]
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
        from build123d_drafting import Note

        n = Note(
            _font_safe_text(text),
            at,
            self.draft,
            rotation=rotation,
            align=align if align is not None else (Align.CENTER, Align.CENTER),
        )
        # Keep the exact string shown by the drafting font for the PDF semantic
        # overlay (notably its established ⌀ -> ø compatibility substitution).
        n.pdf_text = _font_safe_text(text)
        n.pdf_text_rotation = float(rotation)
        n.pdf_text_line_spacing = _text_line_spacing_em(
            self.draft.font_size,
            getattr(self.draft, "font_path", DEFAULT_FONT_PATH),
            getattr(self.draft, "font", "Arial"),
        )
        if name is None:
            i = 0
            while (name := f"note{i}") in self._registry:
                i += 1
        self._add(n, name, view=view)
        return name

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
        if _cells and name in self._registry:
            raise ValueError(f"measured schedule name {name!r} already belongs to an annotation")
        table = _build_table(
            rows, self.draft, block_cols=block_cols, left_align_cols=_left_align_cols
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
        a = self._analysis
        margins = _analysis_margins(a) if a is not None else SheetMargins()
        pw = a.PAGE_W if a is not None else self.page_w
        ph = a.PAGE_H if a is not None else self.page_h
        region = margins.bounds(pw, ph)
        # The shared post-fit occupancy policy — views, decomposed annotation ink, minus
        # the page-spanning riders, plus the title-block hull. Extracted so the NTS
        # caption places against the same set (#1197); every hand-rolled copy of it has
        # dropped one of the four parts.
        obstacles = late_furniture_obstacles(self, named=True)

        trace = FitBoxTrace()
        pos = fit_auxiliary_box(
            (w, h),
            region,
            obstacles,
            prefer,
            clearance=self.draft.pad_around_text,
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
            self._registry.record_issue(
                LintIssue(
                    severity=_drop_severity,
                    code=_drop_code,
                    message=(
                        f"table {name!r} did not fit the sheet; measured page-space footprint "
                        f"{measured}; {detail}"
                    ),
                    source_ids=tuple(
                        dict.fromkeys(
                            ((_source_id,) if _source_id is not None else ()) + _source_ids
                        )
                    ),
                    measurement_ids=tuple(cell.measurement for cell in _cells),
                )
            )
            return None
        placed = table.locate(Location((pos[0], pos[1], 0)))
        placed.source_features = _features
        if not _cells:
            return self._add(placed, name)
        snapshot = self._registry.snapshot()
        items = list(self.items)
        issues = self._registry.issues
        try:
            return self._add(placed, name, cells=_cells)
        except BaseException:
            self.items[:] = items
            self._registry.restore(snapshot)
            self._registry.restore_issues(issues)
            raise

    def _hole_spec_groups(self, view):
        """Ordered ``(tag, [holes], count)`` spec-groups of *view*'s holes (tags A, B,
        …). The shared basis for the hole table's rows and its balloons, so the
        TAG column and the balloon glyphs line up.

        Sourced from the IR (``model.features``), so each group is one hole/pattern
        feature — a pattern and same-spec loose holes are distinct groups (ADR 1 (was 0008);
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
            document_member=self._document_member,
            document_source_annotation_ids=self._document_source_annotation_ids,
        )
        render_balloons(self, self._analysis, view, specs, ctx, avoid_annotation_labels=True)

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
        from draftwright.model.callout import resolved_through_indicator

        groups = self._hole_spec_groups(view)
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
        if self._part_model is not None:
            from draftwright.model.compiled import compile_dimensions as _compile_table
            from draftwright.model.compiled import resolve_feature as _resolve_table

            _plan = _compile_table(self._part_model)
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
            return f"{dim.value_text}{_tol_suffix(dim.tolerance, self.draft)}"

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
        """Internal lint path with an optional summary-scoped pair ledger (#1147)."""
        page_bbox = self.drawable_bounds
        # The model compiler remains above linting's independent rank-2 boundary.
        model = self._part_model
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
            compiled_display_precisions(self._registry, dimension_plan)
            if dimension_plan is not None
            else None
        )
        ctx = LintContext(
            drawing=self,
            page_bbox=page_bbox,
            views=self.views,
            items=self.items,
            scale=self.scale,
            material_fields=self.material_fields,
            registry=self._registry,
            working_part=self._working_part,
            analysis=self._analysis,
            build=self._build,
            model=model,
            coverage=self._coverage,
            assembly=self.assembly,
            model_declared=self._model_declared,
            view_edge_cache=self._view_edge_cache,
            ann_box_cache=self._ann_box_cache,
            cyl_cache=self._cyl_cache,
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
            if ctx.cyl_cache is not self._cyl_cache:
                self._cyl_cache = ctx.cyl_cache

    def layout_utilization(self) -> dict:
        """Conservative page-space utilization evidence for layout decisions.

        The evidence uses clipped view and annotation bounding boxes. It therefore
        overestimates sparse line-work by design, but it is deterministic, cross-family,
        and sufficient to expose a large unused sheet or empty quadrant without parsing
        an export (#1797).
        """
        from draftwright.drawing_evidence import layout_utilization

        page = _frame_margins(self._analysis).bounds(self.page_w, self.page_h)
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
        if scoped is not None and scoped[0] is self:
            issues, aggregation = scoped[1], scoped[2]
        else:
            with _collect_issue_aggregation() as aggregation:
                issues = self.lint()
        from draftwright.drawing_evidence import lint_summary as project_lint_summary

        candidate = _REPORT_REQUIREMENTS.get()
        report_requirements = candidate if candidate is not None and candidate[0] is self else None
        return project_lint_summary(
            issues,
            aggregation,
            analysis=self._analysis,
            model=self._part_model,
            registry=self._registry,
            recognition=self._build.recognition,
            evidence=self._build.recognition_evidence,
            ownership=self._build.recognition_ownership,
            omissions=self._build.omissions,
            working_part=self._working_part,
            items=self.items,
            model_declared=self._model_declared,
            layout_utilization=self.layout_utilization,
            report_requirements=report_requirements,
            geometry_aware_codes=_GEOMETRY_AWARE_CODES,
            score_error_penalty=_SCORE_ERROR_PENALTY,
            score_warning_penalty=_SCORE_WARNING_PENALTY,
        )

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
