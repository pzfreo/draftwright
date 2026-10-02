"""Shared annotation-placement helpers (#138 / ADR 1 (was 0005), P5).

Page-box geometry the passes share: an annotation's bbox (`_anno_box`), the
complete strip occupancy (`strip_obstacles`), and an AABB overlap test
(`_box_hits`). Bottom of the annotations DAG.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from types import SimpleNamespace
from typing import Any

from build123d_drafting.helpers import Dimension, SafeDimension

from draftwright._core import (  # noqa: F401 — _anno_box re-exported
    _STRIP_SPACING,
    _analysis_margins,
    _anno_box,
    _copy_dimension_spec_riders,
    _decode_hole_location_fact,
    _dim,
    _drawing_bounds,
    place_annotation,
)
from draftwright._geometry import (  # noqa: F401
    _boxes_overlap,
    _segment_clip_extent,
    _segment_clips_box,
    _segment_crosses_box,
    _segments_cross_or_overlap,
)
from draftwright.annotation_layout_profile import layout_flag
from draftwright.annotations._dimension_ink import (  # noqa: F401 — stable _common imports
    AnalyticalDimensionInk,
    DimensionInkCandidate,
    _dimension_probe_ink,
    _DimensionInkProbe,
    _styled_dimension_footprint,
    _subtract_probe_span,
    dim_footprint,
    dimension_candidate_geometry,
    short_dimension_label_offset,
)
from draftwright.annotations._dimension_ink_repair import _prevent_dimension_label_ink
from draftwright.annotations._placement_geometry import (
    _STROKE_PAD as _STROKE_PAD,
)
from draftwright.annotations._placement_geometry import (
    _box_hits as _box_hits,
)
from draftwright.annotations._placement_geometry import (
    _geom_box as _geom_box,
)
from draftwright.annotations._placement_geometry import (
    _log as _log,
)
from draftwright.annotations._placement_geometry import (
    analytical_leader_lands_clear as analytical_leader_lands_clear,
)
from draftwright.annotations._placement_geometry import (
    carve_free_segments as carve_free_segments,
)
from draftwright.annotations._placement_geometry import (
    clear_label_of_centerlines as clear_label_of_centerlines,
)
from draftwright.annotations._placement_geometry import (
    leader_callout_geometry as leader_callout_geometry,
)
from draftwright.annotations._placement_geometry import (
    leader_footprint as leader_footprint,
)
from draftwright.annotations._placement_geometry import (
    occupancy_boxes as occupancy_boxes,
)
from draftwright.annotations._placement_occupancy import (  # noqa: F401 — stable _common imports
    _PAGE_SPANNING_RIDERS,
    CROSSABLE_TYPES,
    annotation_ink_clear,
    annotation_ink_obstacles,
    annotation_obstacle_boxes,
    annotation_text_ink_clear,
    balloon_annotation_label_boxes,
    balloon_geometry_hits_annotation_labels,
    box_within_page_and_clear,
    corridor_blockers,
    full_strip_message,
    is_page_spanning_rider,
    late_furniture_obstacles,
    pending_title_block_box,
    strip_dimension_occupancy,
    strip_free_span,
    strip_obstacles,
    strip_occupants,
    view_label_clearance,
)
from draftwright.annotations._placement_occupancy import (
    place_iso_nts_note as _place_iso_nts_note,
)
from draftwright.annotations._strip_postsolve import (
    _commit_strip_candidate_run as _commit_strip_candidate_run,
)
from draftwright.annotations._strip_postsolve import (
    _resolve_required_strip_ink as _resolve_required_strip_ink_owner,
)
from draftwright.annotations.angular import AngularDimension
from draftwright.annotations.solve_trace import SolveTrace, _never_aborts  # noqa: F401
from draftwright.layout import (
    ObligationClass,
    StripCandidate,
    _assign_leader_candidates,
    obligation_rank,
    plan_strip,
)
from draftwright.linting.issues import LintIssue
from draftwright.model.compiled import resolve_feature
from draftwright.model.ir import HoleFeature, PatternFeature
from draftwright.model.planner import hole_location_parameter_id
from draftwright.registry import PlacedDimension

# Shared corridor ordering: feature sizes near the view, datum locations outside.
_SIZE_SUBCHAIN = 0
_LOC_SUBCHAIN = 1


def _ray_exit_dist(px, py, ux, uy, rect) -> float:
    """Distance along the unit ray (ux, uy) from (px, py) to where it leaves *rect*
    (x0, y0, x1, y1). For a tip inside the rect this is the positive distance to the
    boundary; clamped to ``>= 0`` so a tip already outside contributes no negative reach."""
    x0, y0, x1, y1 = rect
    ts = []
    if ux > 0:
        ts.append((x1 - px) / ux)
    elif ux < 0:
        ts.append((x0 - px) / ux)
    if uy > 0:
        ts.append((y1 - py) / uy)
    elif uy < 0:
        ts.append((y0 - py) / uy)
    return max(min([t for t in ts if t > 0], default=0.0), 0.0)


@dataclass(frozen=True)
class DerivedViewReservation:
    """An engine-owned, non-rendered hard keep-out for a required derived view.

    This occupies the *whole* planned view/annotation/caption box, including
    blank space between strokes. It is not a provisional section arrow: required
    leaders must not override it. The detail pass must remove it before export.
    """

    box: tuple[float, float, float, float]

    def __post_init__(self):
        x0, y0, x1, y1 = self.box
        if not all(math.isfinite(value) for value in self.box) or x0 >= x1 or y0 >= y1:
            raise ValueError("derived-view reservation needs a finite nonempty page box")

    def bounding_box(self):
        x0, y0, x1, y1 = self.box
        return SimpleNamespace(
            min=SimpleNamespace(X=x0, Y=y0),
            max=SimpleNamespace(X=x1, Y=y1),
        )


def _clear_derived_view_reservation(dwg, name: str, *, required: bool = False):
    """Permanently consume one private planning placeholder, never user ink."""
    if name not in dwg.annotations():
        if required:
            raise KeyError(f"required derived-view reservation {name} is missing")
        return None
    reservation = dwg.get_annotation(name)
    if not isinstance(reservation, DerivedViewReservation):
        raise TypeError(f"{name} is not a derived-view reservation")
    dwg.remove(name)
    return reservation.box


def _with_hole_location_coverage(annotation, coverage):
    """Attach exact compiler location/member facts to a rendered annotation.

    Both the automatic corridor pass and live edit verbs use this seam.  Keeping it
    below the individual renderers prevents one placement path from carrying only a
    feature-level measurement identity while another carries the member it locates.
    """
    annotation.covers_hole_locations = tuple(coverage)
    return annotation


def _hole_location_coverage_fact(location):
    """The directional physical fact rendered by one compiled hole location member.

    The physical requirement vocabulary is independent of the public member address.
    Its point identifies the physical member; the compiler's declaration-local index
    must not replace that independent evidence.
    """
    feature = resolve_feature(location.ref)
    if feature is None and location.id is not None:
        feature = location.id.feature
    assert feature is not None and location.span is not None
    if isinstance(feature, HoleFeature | PatternFeature):
        parameter = location.physical_location_component
        assert parameter is not None
        return (feature, parameter, tuple(location.span[1]))
    parameter = location.id.parameter if location.id is not None else location.parameter_id
    if (
        parameter
        in {
            "location.location",
            "location_pattern.location",
            "location_pad.location",
            "location_pocket.location",
            "location_pocket_pattern.location",
        }
        and location.discriminator
    ):
        parameter = f"{parameter}.{location.discriminator}"
    return (feature, parameter, tuple(location.span[1]))


def _with_hole_center_coverage(annotation, feature, member, view):
    """Attach the physical member identified by hole/pattern centre furniture."""
    annotation.covers_hole_centers = ((feature, tuple(member), view),)
    return annotation


def _same_location_ordinate(left, right) -> bool:
    """Whether two compiled locations state the same physical ordinate.

    Layout may reject distinct dimensions that are too close to print legibly, but that
    spacing policy must never merge their semantic provenance.  Compiler coordinates are
    stable to six decimals; this tolerance absorbs only floating-point construction noise.
    """
    return abs(float(left) - float(right)) <= 1e-6


def _register_hole_table_coverage(
    table,
    registry,
    name,
    *,
    measurements=(),
    locations=(),
    requirements=(),
    representation_reason=None,
    representation_requirements=(),
):
    """Register the semantic facts visibly carried by a placed hole table.

    Automatic escalation and the public table verb share this seam so the table object,
    registry measurement inventory, requirement-scoped replacement evidence, and physical hole
    ledger cannot drift apart.
    """
    table.covers_hole_locations = tuple(locations)
    table.covers_hole_requirements_by_feature = tuple(requirements)
    table.covers_hole_representations_by_requirement = tuple(
        (feature, parameter, "hole_table", representation_reason)
        for feature, parameter in representation_requirements
        if representation_reason is not None
    )
    identity = registry.identity_of(name)
    identity["measurement"] = tuple(measurements)
    registry.reapply(name, identity)
    return table


def _annotation_hole_features(registry, name, annotation) -> frozenset:
    """Semantic hole/pattern owners carried by one placed annotation.

    Registry ownership is intentionally singular and may be empty for a visible mark shared
    by several features. Measurement and structured-coverage provenance are therefore the
    authoritative union used by replacement transactions.
    """
    features = set()
    owner = registry.feature_of(name)
    if getattr(owner, "kind", None) in {"hole", "pattern"}:
        features.add(owner)
    for measurement in registry.measurement_of(name):
        feature = getattr(measurement, "feature", None)
        if getattr(feature, "kind", None) in {"hole", "pattern"}:
            features.add(feature)
    for fact in getattr(annotation, "covers_hole_locations", ()):
        decoded = _decode_hole_location_fact(fact)
        if decoded is not None:
            feature = decoded[0]
        else:
            # Replacement ownership is a broader question than whether a fact can
            # prove axis-specific coverage.  Legacy/external riders may carry only
            # ``(measurement, point)``; retain that measurement's semantic owner
            # even when it has no parameter and the strict decoder rightly refuses
            # to treat it as location evidence.
            try:
                candidate = fact[0]
            except (IndexError, TypeError):
                continue
            feature = (
                candidate
                if getattr(candidate, "kind", None) in {"hole", "pattern"}
                else getattr(candidate, "feature", None)
            )
        if getattr(feature, "kind", None) in {"hole", "pattern"}:
            features.add(feature)
    for feature, _requirement, _count in getattr(
        annotation, "covers_hole_requirements_by_feature", ()
    ):
        if getattr(feature, "kind", None) in {"hole", "pattern"}:
            features.add(feature)
    for feature, _parameter, _representation, _reason in getattr(
        annotation, "covers_hole_representations_by_requirement", ()
    ):
        if getattr(feature, "kind", None) in {"hole", "pattern"}:
            features.add(feature)
    for feature, _point, _view in getattr(annotation, "covers_hole_centers", ()):
        if getattr(feature, "kind", None) in {"hole", "pattern"}:
            features.add(feature)
    return frozenset(features)


def _table_location_parameters(feature) -> set[str]:
    if not isinstance(feature, HoleFeature) or feature.frame.axis != "z":
        return set()
    return {
        hole_location_parameter_id(feature, member, axis)
        for member in range(len(feature.members or (feature.frame.origin,)))
        for axis in ("x", "y")
    }


def _hole_table_replaceable_annotation(registry, name, annotation) -> bool:
    """Whether a table can replace *all* semantic facts carried by an annotation.

    The current hole-table schema states only a plain circular bore's diameter/depth,
    through state, quantity, and (on the automatic table) X/Y positions. A compound
    callout is indivisible: counterbores, spotfaces, countersinks, profiles, threads, and
    pattern geometry must keep their leader even when a useful table row is also added.
    """
    features = _annotation_hole_features(registry, name, annotation)
    if not features:
        return False
    for feature in features:
        if getattr(feature, "kind", None) != "hole":
            return False
        if any(
            getattr(feature, attribute, None) is not None
            for attribute in ("cbore", "spotface", "csink", "thread", "profile")
        ):
            return False
    table_parameters = {
        "bore.diameter",
        "bore.depth",
        "location.location",
        "location_pattern.location",
    }
    for feature in features:
        table_parameters.update(_table_location_parameters(feature))
    return all(
        getattr(measurement, "parameter", None) in table_parameters
        for measurement in registry.measurement_of(name)
    )


def _hole_table_replaceable_location_annotation(registry, name, annotation) -> bool:
    """Whether *annotation* carries only table-rendered X/Y location facts.

    Location eligibility is intentionally independent of bore-callout eligibility: a
    fitted, toleranced, threaded, or compound hole keeps its manufacturing callout while
    the table may still replace a separately dropped ordinate for the same feature.
    """
    features = _annotation_hole_features(registry, name, annotation)
    if not features or any(getattr(feature, "kind", None) != "hole" for feature in features):
        return False
    measurements = tuple(registry.measurement_of(name))
    return bool(measurements) and all(
        getattr(measurement, "parameter", None) == "location.location"
        or getattr(measurement, "parameter", None)
        in _table_location_parameters(getattr(measurement, "feature", None))
        for measurement in measurements
    )


def _hole_table_replaceable_feature(feature, dimensions=()) -> bool:
    """Whether the current table schema can replace *feature*'s compiled callout facts.

    Annotation metadata alone is insufficient: an authored tolerance or fit lives on the
    compiler-approved dimension, and an automatic callout may have dropped before any
    annotation object existed. Keep this predicate feature/plan based so the automatic
    transaction does not parse rendered text.
    """
    if getattr(feature, "kind", None) != "hole":
        return False
    if any(
        getattr(feature, attribute, None) is not None
        for attribute in ("cbore", "spotface", "csink", "thread", "profile")
    ):
        return False
    return all(getattr(dimension, "tolerance", None) is None for dimension in dimensions)


@dataclass(frozen=True)
class _StashedAnnotation:
    """One reversible annotation removal with its complete registry identity."""

    annotation: Any
    identity: dict
    features: frozenset


_MISSING_REPRESENTATION = object()


@dataclass(frozen=True)
class _AnnotationTransactionSnapshot:
    """Complete mutable drawing state needed by an annotation rollback."""

    registry: dict
    issues: tuple
    items: tuple
    coverage: Any
    representations: tuple[tuple[Any, Any, Any], ...]


def _snapshot_annotation_transaction(dwg, coverage) -> _AnnotationTransactionSnapshot:
    """Capture identity/order, issues, render order, and semantic coverage."""
    return _AnnotationTransactionSnapshot(
        registry=dwg.registry.snapshot(),
        issues=dwg.registry.issues,
        items=tuple(dwg.items),
        coverage=coverage.snapshot(),
        representations=tuple(
            (
                annotation,
                getattr(annotation, "hole_representation", _MISSING_REPRESENTATION),
                getattr(
                    annotation,
                    "hole_representation_reason",
                    _MISSING_REPRESENTATION,
                ),
            )
            for _name, annotation in dwg.iter_annotations()
        ),
    )


def _restore_annotation_transaction(dwg, coverage, snapshot, stashed, *, reason=None) -> None:
    """Restore *snapshot* exactly, optionally marking visible fallback semantics."""
    for annotation, representation, representation_reason in snapshot.representations:
        for attribute, value in (
            ("hole_representation", representation),
            ("hole_representation_reason", representation_reason),
        ):
            if value is _MISSING_REPRESENTATION:
                if hasattr(annotation, attribute):
                    delattr(annotation, attribute)
            else:
                setattr(annotation, attribute, value)
    dwg.registry.restore(snapshot.registry)
    dwg.registry.restore_issues(snapshot.issues)
    dwg.items[:] = snapshot.items
    coverage.restore(snapshot.coverage)
    if reason is not None:
        for record in stashed.values():
            record.annotation.hole_representation = "feature_annotation"
            record.annotation.hole_representation_reason = reason


def _stash_annotations(dwg, names) -> dict[str, _StashedAnnotation]:
    """Remove *names* while retaining enough state for an exact semantic rollback."""
    stashed = {}
    for name in names:
        annotation = dwg.registry.named(name)
        if annotation is None:
            continue
        identity = dwg.registry.identity_of(name)
        features = _annotation_hole_features(dwg.registry, name, annotation)
        stashed[name] = _StashedAnnotation(
            dwg.remove(name),
            identity,
            features,
        )
    return stashed


def _fully_ballooned_features(view, tagged_holes, placed_names, registry, expected_counts) -> set:
    """Features whose exact-cardinality balloons landed in this render attempt.

    A name that happened to exist before the attempt is not evidence. Nor is a landed
    balloon owned by another semantic feature. Both checks are deliberately made here,
    beside the declared-QTY cardinality check, so every automatic commit uses one predicate.
    """
    attempted = set(placed_names)
    names_by_feature: dict[object, set[str]] = {}
    for tag, member_index, _hole, feature in tagged_holes:
        names_by_feature.setdefault(feature, set()).add(f"balloon_{view}_{tag}_{member_index}")
    return {
        feature
        for feature, required_names in names_by_feature.items()
        if len(required_names) == expected_counts.get(feature, 0)
        and all(
            name in attempted and registry.feature_of(name) == feature for name in required_names
        )
    }


def _discard_attempt_annotations(dwg, names) -> set[str]:
    """Remove annotations created by an uncommitted placement attempt."""
    removed = set()
    for name in names:
        if dwg.registry.named(name) is not None:
            dwg.remove(name)
            removed.add(name)
    return removed


@dataclass(frozen=True)
class Escalation:
    """A first-class "could not place this here" signal (ADR 2 (was 0009 Amendment 1), P5-strand-2).

    Placers *collect* one of these into the run's ``PlacementContext.escalations`` at the point
    of failure —
    instead of recording a stringly-typed ``*_dropped`` lint code and letting the escalators
    grep for it — and one later resolver pass groups them by ``(view, feature-or-pattern)``,
    picks a remedy per group (ISO pattern-grouped balloon / table / detail / drop), and emits
    the ``*_dropped`` lint codes only for what stays unresolved (so coverage lint + the
    cleanliness ratchet keep working). See the ADR / epic #351.

    The hole callout/location placers emit these (#351 PR-2); the resolver in
    ``annotations/orchestrator.py`` (``_maybe_tabulate_holes``) consumes them, including
    the ISO pattern-grouped balloon fallback for a dropped pattern callout (#351 PR-3).

    Attributes:
        kind:     what could not be placed — ``"callout" | "location" | "slot" | "step" | "pmi"``.
        view:     the owning orthographic view (``None`` for drawing-level).
        feature:  reference to the IR feature / ``HoleRef`` / key it belongs to — carries the
                  pattern membership the resolver groups on (a ``"callout"`` escalation's
                  feature is the dropped group's ``PatternFeature`` when it is a
                  fully-surviving recognised pattern, else ``None``). Left untyped to keep
                  this module a leaf (no dependency on ``model.ir``).
        reason:   why placement failed — ``"strip_full" | "illegible" | "corridor_blocked" | "no_room"``.
        remedies: ranked candidate remedies the resolver may pick, e.g.
                  ``("group_balloon", "table", "detail", "drop")``. Empty = resolver's default ladder.
        targets:  opaque approved items the failed placement could not draw. Empty for
                  escalations whose remedy reconstructs a whole feature/group.
    """

    kind: str
    view: str | None
    feature: object
    reason: str
    remedies: tuple[str, ...] = field(default_factory=tuple)
    targets: tuple[object, ...] = field(default_factory=tuple)


# Keep the historical import and pickle identity for the private trace recorder.
SolveTrace.__module__ = __name__


def place_iso_nts_note(dwg, a, bb) -> None:
    # Keep the live _common._anno_box patch seam used by rendering and tests.
    _place_iso_nts_note(dwg, a, bb, anno_box=_anno_box)


place_iso_nts_note.__doc__ = _place_iso_nts_note.__doc__


def prevent_dimension_label_ink(
    dimensions,
    *,
    page=None,
    immutable=(),
    obstacles=(),
    perpendicular_step=None,
    label_clear=None,
):
    """Resolve same-batch dimension label contacts through the bounded ink solve."""
    # Pass the live builder binding used by the rest of this corridor owner.
    return _prevent_dimension_label_ink(
        dimensions,
        page=page,
        immutable=immutable,
        obstacles=obstacles,
        perpendicular_step=perpendicular_step,
        label_clear=label_clear,
        dimension_builder=_dim,
    )


prevent_dimension_label_ink.__doc__ = _prevent_dimension_label_ink.__doc__


# ── The corridor priority ladder ─────────────────────────────────────────
# `CorridorCandidate.priority` is an over-capacity survival rank, and a rank only
# means anything RELATIVE to the other rungs. Defining the whole ladder here — beside
# the field it ranks, at the bottom of the annotations DAG every pass imports from —
# is what makes it reviewable: a bare `priority=0.5` at a call site cannot be judged
# without knowing what else sits at 0.5.
#
# The principal front-view chain must outrank pocket location candidates;
# an implicit default would tie them and let a generated key decide which survives.
#
# Rungs are ordered, and the gaps are deliberate — insert between rather than
# renumbering, so existing relative order is never disturbed.
class PRIORITY:
    """Corridor survival ranks, lowest to highest."""

    #: Ordinary auto dims — feature sizes, feature locations. The engine's own choices.
    AUTO = 0.0
    #: Principal part dims — step heights, the overall height. What a print is least
    #: usable without, so they outrank ordinary auto dims when a strip is full.
    PRINCIPAL = 0.5
    #: Authored intent — GD&T frames, imported PMI. The user asked for these by name,
    #: so they outrank anything the engine chose for itself.
    AUTHORED = 1.0
    #: When datum leaders share one projected stem, reserve its near end first.
    #: Farther datums can still use their normal post-drain side/sheet fallback.
    AUTHORED_DATUM_STEM_STEP = 0.01
    #: Never-drop: the mandatory envelope dims, and a user-pinned intent (ADR 2 (was 0012)).
    #: Two distinct meanings that deliberately share a rung — both are non-negotiable.
    MANDATORY = 100.0


@dataclass
class CorridorCandidate:
    """One datum-referenced linear dim collected for a shared corridor's single solve
    (ADR 2 (was 0009) end state, #345/#346). Multiple render passes (`render_locations`,
    `render_slots`) feed the SAME above-view strip; committing per-pass interleaves the
    dims and cannot dedup coincident spans. Each pass instead registers a candidate here;
    one :func:`solve_corridor` per strip dedups, orders, and places the whole set.

    Attributes:
        name/build: the ``(name, pos->Dimension)`` pair :func:`place_strip_candidates`
            consumes — unchanged.
        order:      sort key placing the candidate in the corridor ladder. Location dims
            key on datum distance (the monotonic ISO ladder); size dims form a separate
            contiguous run so a slot length never lands mid-ladder (#346).
        dedup:      coincidence key on the MEASURED axis, or ``None`` to keep both.
            Location keys also include their approved label: coincident geometry with
            different stated precision/tolerance must not silently collapse. Equal keys
            share one physical statement; the higher-``precedence`` one survives (#345).
        precedence: dedup survivor rank — a hole *location* dim (feeds coverage/table
            escalation) outranks a coincident slot *position* line.
        priority:   within-class over-capacity survival rank (#357). When a strip cannot
            hold every candidate, :func:`plan_strip` drops the lowest semantic class,
            then ``(priority, key)``. Pick a rung from :data:`PRIORITY` below rather than a bare
            number: the value only means anything *relative to the others*, so a literal at
            a call site cannot be reviewed on its own.
        anchored/natural: when ``anchored`` is true, the strip solve keeps this candidate
            near its own natural stacking-axis page coordinate instead of the segment edge.
            This is how user-authored pinned dimension intents join the shared solve
            without being invalidated by a later first-fit pass.
        on_place/on_drop: the pass's own post-placement bookkeeping — coverage
            registration / drop lint + `Escalation`, or a slot's below-side fallthrough.
        force:      policy-B force-keep after the corridor-respecting pass (locations have
            no alternate view); size/position slot dims fall through instead (``on_drop``).
    """

    name: str
    build: object
    order: tuple
    on_place: object
    on_drop: object
    dedup: tuple | None = None
    precedence: int = 0
    priority: float = 0
    obligation_class: ObligationClass = "unknown"
    anchored: bool = False
    natural: float | None = None
    force: bool = False
    # The source IR feature this dim was rendered for — recorded as provenance when the
    # dim is placed at drain (ADR 5 (was 0010)). ``None`` leaves the annotation feature-less.
    feature: object | None = None
    # The `DimensionId` this candidate draws, where the renderer holds an
    # `ApprovedDimension` to take it from. Peer to `feature` and recorded the same
    # way at drain, one axis finer: `feature` says which hole, this says which of its
    # measurements. ``None`` for a candidate whose renderer places directly.
    measurement: object | None = None
    measurement_span: object | None = None
    # Structured-note authority carried by this placed annotation, kept distinct
    # from dimensional ink in the registry.
    satisfaction: object | None = None
    # The exact declared IR item that produced this ink. Distinct from ``feature`` for a
    # structured note: its physical owner is the origin, while its editable declaration is
    # the Note itself.
    declaration: object | None = None
    # Real stacking-axis + perpendicular footprint ``(w, h)`` in page-mm, or ``None`` to
    # use the dimension default ``(tier, tier)``. Wide/tall occupants (a GD&T feature
    # control frame is ~24×6 mm) set this so the strip solve reserves their true extent
    # instead of one label-height (ADR 2 (was 0009)). A dim leaves
    # it ``None`` — byte-identical to the pre-plumbing placement.
    size: tuple | None = None
    # An ``(x0, y0, x1, y1)`` page-box this candidate must NOT overlap even when force-kept —
    # the title block, which is placed after the corridor drain so the strip carve can't see
    # it. ``None`` (every dim) skips the check.
    forbid: object | None = None
    # Analytical ``pos -> (x0, y0, x1, y1)`` footprint of the geometry ``build(pos)``
    # would produce: lets the strip solve measure and evaluate this candidate
    # without constructing OCC geometry at all (see :func:`dim_footprint`). ``None``
    # falls back to one probe build at the strip edge + the box-shift model. CONTRACT:
    # the footprint must be accurate — its PERPENDICULAR extent feeds the obstacle
    # band filter, so an underestimate hides obstacles from the carve. The solve
    # re-validates each built survivor against the blockers, the forbid box and the
    # band-filtered-out obstacles, so a miss costs a wasted build and a retry, but
    # keeping footprints truthful (``dim_footprint``, ±0.05 mm) is what keeps that
    # fallback rare and the placement identical to the probe path.
    footprint: object | None = None
    # A candidate can need a minimum geometric clearance (e.g. an angular arc
    # must carry its complete label). Reject an infeasible tier before building
    # OCC ink; the caller's normal drop/fallback path remains authoritative.
    valid_position: object | None = None
    # Bounded curved-ink alternatives, checked against actual ink after the
    # conservative strip solve. They preserve the approved content and sector.
    compact_candidates: object | None = None
    # Whole-ink alternatives for a required candidate that still conflicts after the
    # shared batch's dimension-label repair and ordinary compaction. The same strip
    # placement stage checks these against settled and same-batch ink; an infeasible
    # item returns to its normal on_drop/fallthrough path rather than printing a
    # known collision. GD&T uses this seam; other annotation families can join it.
    ink_repair_candidates: object | None = None
    require_clear_ink: bool = False
    # Optional whole-annotation fallback inside the owning view.  The normal
    # corridor solve remains first and unchanged; only a genuine strip failure
    # contributes this candidate to the shared interior-dimension solve.
    interior_view: str | None = None
    interior_side: str | None = None
    interior_build: object | None = None
    interior_geometry: object | None = None

    @property
    def effective_obligation_class(self) -> ObligationClass:
        """Approved measurements and declarations are required even if not marked."""
        obligation_rank(self.obligation_class)
        if self.measurement is not None or self.declaration is not None:
            if self.obligation_class == "optional":
                raise ValueError("approved or authored annotation cannot be optional")
            return "required"
        return self.obligation_class


@dataclass(frozen=True)
class InteriorDimensionJob:
    """One whole dimension eligible for shared interior/exterior candidate assignment.

    Automatic jobs arrive after an exterior-corridor failure and try bounded view-relative
    interior lanes. Declared jobs carry one compiler-derived, feature-relative position;
    that candidate may prove wholly interior or wholly exterior, but never straddle the
    view boundary.
    """

    name: str
    view: str
    side: str
    build: object
    on_place: object
    on_drop: object
    lane_step: float
    priority: float = 0.0
    feature: object | None = None
    measurement: object | None = None
    measurement_span: object | None = None
    interior_build: object | None = None
    analytical_geometry: object | None = None
    # A declared feature-relative lane can resolve to one exact candidate position.
    # The position is compiler-derived from the witness and drafting spacing; it is
    # never accepted from the public API.
    explicit_position: float | None = None
    requested_lane: int | None = None
    # Mutable, caller-owned sink for bounded rejection categories. Declared lane
    # producers use it to return actionable failure evidence without widening the
    # public API to coordinates or retaining CAD objects in lint/report state.
    rejection_reasons: list[str] | None = field(
        default=None,
        compare=False,
        hash=False,
        repr=False,
    )


class DimensionCandidateRegion(str, Enum):
    """Explicit provenance for a measured whole-dimension alternative."""

    INTERIOR = "interior"
    EXTERIOR = "exterior"


@dataclass(frozen=True)
class InteriorDimensionCandidate:
    """One complete rendered dimension at a proven-clear candidate lane."""

    annotation: Any
    position: float
    region: DimensionCandidateRegion = DimensionCandidateRegion.INTERIOR


def _dimension_region(label, bounds, side) -> DimensionCandidateRegion | None:
    """Classify the complete label against its owning view before placement or commit."""
    if (
        label[0] >= bounds[0]
        and label[1] >= bounds[1]
        and label[2] <= bounds[2]
        and label[3] <= bounds[3]
    ):
        return DimensionCandidateRegion.INTERIOR
    if _dimension_exterior(label, bounds, side):
        return DimensionCandidateRegion.EXTERIOR
    return None


def _dimension_exterior(label, bounds, side) -> bool:
    """Require a label to sit wholly beyond its selected view boundary."""
    return bool(
        {
            "above": label[1] >= bounds[3],
            "below": label[3] <= bounds[1],
            "right": label[0] >= bounds[2],
            "left": label[2] <= bounds[0],
        }[side]
    )


def _interior_dimension_geometry(job, position, axis):
    """Build one lane's complete annotation through its available geometry seam."""
    if job.analytical_geometry is not None and job.interior_build is not None:
        dimension = job.analytical_geometry(position)
        label = None if dimension is None else dimension.label_bbox
        box = None if dimension is None else dimension.box
    elif job.explicit_position is not None and job.interior_build is not None:
        dimension = job.interior_build(position)
        label = getattr(dimension, "label_bbox", None)
        box = _geom_box(dimension)
    else:
        # Compatibility path for direct/internal callers without analytical intent.
        # Production candidates carry it, so rejected lanes never construct OCC.
        specimen = job.build(position)
        spec = specimen.placement_spec if isinstance(specimen, PlacedDimension) else None
        if spec is None:
            return None
        opposite = {
            "above": "below",
            "below": "above",
            "right": "left",
            "left": "right",
        }[job.side]
        base = (float(spec.p1[axis]) + float(spec.p2[axis])) / 2.0
        dimension = _dim(
            spec.p1,
            spec.p2,
            opposite,
            abs(position - base),
            spec.draft,
            **spec.kwargs,
        )
        for attr, value in vars(specimen).items():
            if attr.startswith("covers_"):
                setattr(dimension, attr, value)
        _copy_dimension_spec_riders(specimen, dimension)
        label = getattr(dimension, "label_bbox", None)
        box = _geom_box(dimension)
    return dimension, label, box


def _record_interior_dimension_trace(
    trace_event, rejections, job, candidates, costs, choice, outcome, reason=None
) -> None:
    """Record the chosen lane beside the complete candidate inventory."""
    if trace_event is None:
        return
    trace_event["items"].append(
        {
            "name": job.name,
            "outcome": outcome,
            "reason": reason,
            "priority": job.priority,
            "rejections": sorted(rejections[id(job)]),
            "candidate_inventory": [
                {
                    "view": job.view,
                    "region": candidate.region.value,
                    "position": candidate.position,
                    "cost": cost,
                    "outcome": (
                        ("selected" if outcome == "placed" else "failed_commit")
                        if index == choice
                        else "available"
                    ),
                }
                for index, (candidate, cost) in enumerate(zip(candidates, costs, strict=True))
            ],
        }
    )


def _interior_dimension_conflicts(dwg, jobs, candidates_by_job):
    """Enumerate pairs whose complete dimension ink cannot coexist in one view."""
    conflicts: list[tuple[int, int, int, int]] = []
    for right_job, right_candidates in enumerate(candidates_by_job):
        for left_job in range(right_job):
            if jobs[left_job].view != jobs[right_job].view:
                continue
            for left_index, left_candidate in enumerate(candidates_by_job[left_job]):
                for right_index, right_candidate in enumerate(right_candidates):
                    if not annotation_ink_clear(
                        dwg,
                        left_candidate.annotation,
                        additional=(right_candidate.annotation,),
                    ):
                        conflicts.append((left_job, left_index, right_job, right_index))
    return conflicts


def _ordered_corridor_candidates(cands, *, ctx, key, trace):
    """Keep dedup and lane order separate to meet #1956's function-size limit."""
    # Dedup: keep the highest-precedence candidate per coincidence key (tie-break on name,
    # deterministic — ADR 4 (was 0001)). A displaced duplicate is a *loser*: while its winner is
    # drawn it is silently dropped (never starved, so firing its pass's drop lint would be a
    # false report) — but if the winner itself fails to place, the top loser is promoted so
    # the measurement still gets its pass's fallthrough/drop handling (no silent vanish).
    winners: dict = {}
    for c in cands:
        if c.dedup is None:
            continue
        prev = winners.get(c.dedup)
        # Winner: highest precedence, ties broken by the lexicographically smaller name.
        if (
            prev is None
            or c.precedence > prev.precedence
            or (c.precedence == prev.precedence and c.name < prev.name)
        ):
            winners[c.dedup] = c
    kept = [c for c in cands if c.dedup is None or winners.get(c.dedup) is c]
    losers: dict = {}  # dedup key → its displaced candidates (highest precedence first)
    for c in cands:
        if c.dedup is not None and winners.get(c.dedup) is not c:
            losers.setdefault(c.dedup, []).append(c)
    for group in losers.values():
        group.sort(key=lambda c: (-c.precedence, c.name))

    def _planned_order(candidate):
        lanes = None if ctx is None else ctx.annotation_lanes
        model = None if ctx is None else ctx.part_model
        corridor = None
        if lanes is not None and key is not None and len(key) == 2:
            corridor = lanes.corridor(*key)
        # Prefer the compiler-owned measurement identity. Reaching through an opaque
        # FeatureRef here would let a renderer recover withheld model content merely to
        # improve ordering, violating the compiled-plan boundary. Candidates without a
        # measurement may still carry a raw provenance feature; opaque provenance alone
        # deliberately does not participate in this optional ordering refinement.
        feature = getattr(candidate.measurement, "feature", candidate.feature)
        if model is None or (feature is not None and feature not in model.features):
            feature = None
        if corridor is not None and model is not None and feature is not None:
            matches = [
                reservation
                for reservation in corridor.reservations
                if model.features[reservation.demand.feature_index] is feature
            ]
            if matches:
                planned = min(matches, key=lambda item: (item.lane, item.start, item.end))
                return (
                    candidate.order[0],
                    0,
                    planned.lane,
                    planned.start,
                    candidate.order,
                )
        return (candidate.order[0], 1, 0, 0.0, candidate.order)

    kept.sort(key=_planned_order)
    if trace is not None:  # record who lost each dedup group (a loser never starves)
        for dk, group in losers.items():
            for loser in group:
                trace.record_outcome(loser.name, "deduped", winner=winners[dk].name)

    return kept, losers


def _queue_interior_corridor_retry(
    candidate, lane_step, ctx, losers, group_owners, group_measurements, restore_identity, trace
) -> bool:
    """Queue a complete interior fallback only after an eligible exterior failure."""
    interior_jobs = getattr(ctx, "interior_dimensions", None)
    displaced = losers.get(candidate.dedup, ()) if candidate.dedup is not None else ()
    if (
        (ctx is not None and ctx.exterior_dimensions_only)
        or interior_jobs is None
        or candidate.interior_view is None
        or candidate.interior_side is None
        or displaced
    ):
        return False
    owners = group_owners(candidate)
    measurements = group_measurements(candidate)

    def _interior_placed(_name, _candidate=candidate):
        _candidate.on_place(_name)

    def _interior_dropped(_name, _candidate=candidate):
        _candidate.on_drop(_name)
        restore_identity(_candidate)

    interior_jobs.append(
        InteriorDimensionJob(
            name=candidate.name,
            view=candidate.interior_view,
            side=candidate.interior_side,
            build=candidate.build,
            on_place=_interior_placed,
            on_drop=_interior_dropped,
            lane_step=lane_step,
            priority=candidate.priority,
            feature=owners[0] if len(owners) == 1 else None,
            measurement=measurements or candidate.measurement,
            measurement_span=candidate.measurement_span,
            interior_build=candidate.interior_build,
            analytical_geometry=candidate.interior_geometry,
        )
    )
    if trace is not None:
        trace.record_outcome(candidate.name, "deferred", reason="interior_retry")
    return True


def solve_corridor(dwg, strip, view, axis, cands, tier, corner_reserves=(), *, key=None, ctx=None):
    """One collect-then-solve over every :class:`CorridorCandidate` a shared strip
    accumulated across passes (ADR 2 (was 0009) end state). Dedup → order → one non-force
    :func:`place_strip_candidates` pass → a force pass for the force-eligible leftovers →
    dispatch each candidate's ``on_place``/``on_drop``. This is what removes the duplicate
    span (#345) and the interleaved ladder (#346) by construction: a single solve sees the
    full set, so coincident spans collapse and the order is one monotonic chain.

    *key* (the corridor's ``(view, side)``) and *ctx* are threaded by
    :func:`drain_corridors` for the opt-in solve trace (#736, ``ctx.trace``) — when
    tracing is off (``ctx`` is ``None`` or carries no trace) both are inert."""
    if not cands:
        return
    trace = None if ctx is None else ctx.trace
    if trace is not None:
        trace.begin_solve(key, view, axis, tier, strip, cands)
    kept, losers = _ordered_corridor_candidates(cands, ctx=ctx, key=key, trace=trace)

    def _dedup_group(candidate):
        return (candidate, *losers.get(candidate.dedup, ()))

    def _group_measurements(candidate) -> tuple:
        return tuple(
            dict.fromkeys(
                measurement
                for item in _dedup_group(candidate)
                for value in (item.measurement,)
                for measurement in (value if isinstance(value, (list, tuple)) else (value,))
                if measurement is not None
            )
        )

    def _group_owners(candidate) -> tuple:
        return tuple(
            dict.fromkeys(
                resolve_feature(item.feature)
                for item in _dedup_group(candidate)
                if item.feature is not None
            )
        )

    def _winner_placed(c) -> bool:
        """Did this candidate's measurement reach the sheet after all? `on_drop` may
        have rescued it onto the opposite strip, in which case a coincident loser must
        stay suppressed rather than draw the same span twice (#894)."""
        return c.name in dwg.annotations()

    def _restore_shared_identity(candidate, *, visible_name=None) -> None:
        """Give a fallback survivor the same provenance as the primary-strip survivor."""
        name = candidate.name if visible_name is None else visible_name
        if ctx is None or ctx.registry is None or name not in dwg.annotations():
            return
        identity = ctx.registry.identity_of(name)
        measurements = _group_measurements(candidate)
        if measurements:
            identity["measurement"] = measurements
        owners = _group_owners(candidate)
        identity["feature"] = owners[0] if len(owners) == 1 else None
        ctx.registry.reapply(name, identity)

    def _promote_losers(dropped_winner):
        # The winner did not place → hand its measurement to the best surviving loser
        # (e.g. the slot position's below-strip fallthrough), then stop.
        for loser in losers.get(dropped_winner.dedup, ()):
            pending = ctx.post_drain if ctx is not None else None
            n_deferred = len(pending) if pending is not None else 0
            loser.on_drop(loser.name)
            _restore_shared_identity(dropped_winner, visible_name=loser.name)
            # A front-view loser's opposite-strip retry is deferred.  At this moment
            # there is no visible annotation to restore, so queue the aggregated
            # identity immediately behind the retry that creates it.  This mirrors the
            # deferred winner path below and keeps a promoted survivor from retaining
            # only its own measurement.
            if pending is not None and len(pending) > n_deferred:
                pending.append(
                    lambda _c=dropped_winner, _name=loser.name: _restore_shared_identity(
                        _c, visible_name=_name
                    )
                )
            if trace is not None:
                trace.record_outcome(loser.name, "promoted")
            break

    if strip is None:  # no such strip on this drawing — every candidate drops
        for c in kept:
            if _queue_interior_corridor_retry(
                c,
                tier + _STRIP_SPACING,
                ctx,
                losers,
                _group_owners,
                _group_measurements,
                _restore_shared_identity,
                trace,
            ):
                continue
            c.on_drop(c.name)
            _restore_shared_identity(c)
            if trace is not None:
                trace.record_outcome(c.name, "dropped", reason="no_strip")
            # Same rule as the solved path below: `on_drop` may have rescued this
            # measurement onto the OPPOSITE strip, which can exist even when this one
            # does not. Promoting a coincident loser then draws the span twice.
            if c.dedup is not None and not _winner_placed(c):
                _promote_losers(c)
        if trace is not None:
            trace.end_solve()
        return
    pairs = [(c.name, c.build) for c in kept]
    # The ADR 5 (was 0010) provenance seam — and one of the two places a `FeatureRef` is
    # legitimately resolved back to its feature, because here the object IS the point.
    # A migrated renderer passes the opaque handle through; `resolve_feature` is a
    # no-op for the unmigrated ones that still pass the feature itself.
    feats = {
        candidate.name: owners[0]
        for candidate in kept
        for owners in (_group_owners(candidate),)
        if len(owners) == 1
    }
    # A dedup survivor visibly states every coincident candidate's measurement, not only
    # the winner's. Losing those identities makes a truthful shared dimension look like
    # silent missing content to critique (two nested pockets with the same X/Z location
    # are the concrete case). The dedup key is the solver's declaration that the dimensions
    # are physically identical, so this is structured provenance rather than geometric
    # inference after placement.
    meas = {
        candidate.name: measurements
        for candidate in kept
        for measurements in (_group_measurements(candidate),)
        if measurements
    }
    spans = {c.name: c.measurement_span for c in kept if c.measurement_span is not None}
    satisfactions = {c.name: c.satisfaction for c in kept if c.satisfaction is not None}
    declarations = {c.name: c.declaration for c in kept if c.declaration is not None}
    sizes = {c.name: c.size for c in kept if c.size is not None}  # real footprint
    forbid = {c.name: c.forbid for c in kept if c.forbid is not None}  # title-block box
    prio = {c.name: c.priority for c in kept if c.priority}  # over-capacity survival rank
    obligation_classes = {c.name: c.effective_obligation_class for c in kept}
    anchored = {c.name: c.anchored for c in kept if c.anchored}
    naturals = {c.name: c.natural for c in kept if c.natural is not None}
    foots = {c.name: c.footprint for c in kept if c.footprint is not None}  # analytical
    valid_positions = {c.name: c.valid_position for c in kept if c.valid_position is not None}
    compactions = {c.name: c.compact_candidates for c in kept if c.compact_candidates is not None}
    ink_repairs = {
        c.name: c.ink_repair_candidates for c in kept if c.ink_repair_candidates is not None
    }
    ink_required = {c.name for c in kept if c.require_clear_ink}
    ink_displaced: set[str] = set()
    left = {
        n
        for n, _ in place_strip_candidates(
            dwg,
            strip,
            view,
            axis,
            pairs,
            tier,
            ctx=ctx,
            features=feats,
            measurements=meas,
            measurement_spans=spans,
            satisfactions=satisfactions,
            declarations=declarations,
            sizes=sizes,
            forbid=forbid,
            priorities=prio,
            obligation_classes=obligation_classes,
            anchored=anchored,
            naturals=naturals,
            footprints=foots,
            valid_positions=valid_positions,
            compact_candidates=compactions,
            ink_repair_candidates=ink_repairs,
            require_clear_ink=ink_required,
            ink_displaced=ink_displaced,
            corner_reserves=corner_reserves,
            trace=trace,
        )
    }
    force_pairs = [
        (c.name, c.build)
        for c in kept
        if c.name in left and c.force and c.name not in ink_displaced
    ]
    still = (
        {
            n
            for n, _ in place_strip_candidates(
                dwg,
                strip,
                view,
                axis,
                force_pairs,
                tier,
                ctx=ctx,
                force=True,
                footprints=foots,
                valid_positions=valid_positions,
                compact_candidates=compactions,
                ink_repair_candidates=ink_repairs,
                require_clear_ink=ink_required,
                corner_reserves=corner_reserves,
                features=feats,
                measurements=meas,
                measurement_spans=spans,
                satisfactions=satisfactions,
                sizes=sizes,
                forbid=forbid,
                priorities=prio,
                obligation_classes=obligation_classes,
                anchored=anchored,
                naturals=naturals,
                trace=trace,
            )
        }
        if force_pairs
        else set()
    )
    for c in kept:
        placed = c.name not in left or (
            c.force and c.name not in still and c.name not in ink_displaced
        )
        if placed:
            c.on_place(c.name)  # placed in the corridor-respecting pass or the force pass
            if trace is not None:
                trace.record_outcome(c.name, "placed")
        else:
            if _queue_interior_corridor_retry(
                c,
                tier + getattr(strip, "spacing", _STRIP_SPACING),
                ctx,
                losers,
                _group_owners,
                _group_measurements,
                _restore_shared_identity,
                trace,
            ):
                continue
            pending = ctx.post_drain if ctx is not None else None
            n_deferred = len(pending) if pending is not None else 0
            c.on_drop(c.name)  # dropped / not force-kept — the pass's drop handler runs
            _restore_shared_identity(c)
            deferred = pending is not None and len(pending) > n_deferred
            if trace is not None:  # did on_drop queue a post-drain fallthrough?
                trace.record_outcome(c.name, "dropped", deferred_post_drain=deferred)
            if c.dedup is not None:
                # A deduped winner that did not place hands its measurement to the best
                # surviving loser — but ONLY if the measurement is genuinely absent.
                # `on_drop` may have rescued it onto the opposite strip, and promoting
                # then draws the same span twice.
                #
                # The predicate is "winner still absent", not "did on_drop defer" — a
                # SYNCHRONOUS retry has resolved by now, and if it succeeded the loser
                # must stay suppressed just the same. Only the *moment* of the check
                # differs: immediately when the retry already ran, or queued behind it
                # when it was deferred (appended after, and post_drain runs in order).
                if deferred and pending is not None:
                    pending.append(lambda _c=c: _restore_shared_identity(_c))
                    pending.append(
                        lambda _c=c: None if _winner_placed(_c) else _promote_losers(_c)
                    )
                elif not _winner_placed(c):
                    _promote_losers(c)
    if trace is not None:
        trace.end_solve()


@dataclass
class PlacementContext:
    """The per-run placement scratch a build's passes share — plus references to the drawing's
    build-state stores (the ``registry`` build-issue sink + ``coverage`` bookkeeping) — threaded
    to the passes explicitly instead of hung on the ``Drawing`` result object (ADR 1 (was 0005 §2), #639):
    the corridor batch
    (:func:`register_corridor`/:func:`drain_corridors`), the escalation list (ADR 2 (was 0009 Amdt 1),
    #351), and the enlarged-detail request list (#307).

    All three are per-run — both entry paths (:func:`_auto_annotate` and ``Drawing.finalize``)
    make a fresh one each build and discard it after draining/consuming. The corridor batch is a
    pure function of the still-present intents; escalations/detail-requests need no cross-retry
    persistence either, because finalize is transactional (#647): a raised drain rolls the drawing
    back and the retry re-runs from a clean slate, re-generating them."""

    corridor_batch: dict = field(default_factory=dict)
    escalations: list = field(default_factory=list)
    detail_requests: list = field(default_factory=list)
    # Fallthrough callbacks a pass's on_drop queues to run AFTER every corridor has
    # drained: a mid-drain carve could occupy space a later sibling
    # corridor's force candidate needs; deferral makes "post-drain" literally true.
    post_drain: list = field(default_factory=list)
    # Whole dimensions whose ordinary exterior corridor was genuinely full.
    # The automatic/finalize entry points opt in with ``[]`` and drain them as
    # one bounded interior assignment after every exterior fallthrough settles.
    interior_dimensions: list | None = None
    # Compatible automatic/deferred feature-callout jobs collected across the
    # hole + post-drain machined passes. ``None`` is intentional: direct
    # renderer calls and finished-sheet live verbs keep their immediate behavior;
    # the orchestrator/finalize paths opt in with ``[]`` and drain once.
    feature_leaders: list | None = None
    # Optional pre-render annotation lanes. Automatic builds attach the scheme's
    # deterministic plan; live edits leave this unset and retain their existing order.
    annotation_lanes: Any = None
    # A drafter-style scheme treats exterior dimension lanes as a hard contract.
    exterior_dimensions_only: bool = False
    # Automatic placement may reserve a dense internal section row. Only that
    # run needs the extended hole-leader resource-floor routing preference.
    dense_internal_section: bool = False
    # The opt-in solve-trace recorder — a :class:`SolveTrace` threaded off the
    # drawing's build state by both entry paths, or ``None`` (the default: tracing off,
    # every hook a bare None check). Ctx state, not a module global, so the finalize
    # path traces exactly like the auto pass.
    trace: Any = None
    # The drawing's build-state stores, referenced (not owned) by the run's passes.
    # Duck-typed as ``Any`` — matching the untyped ``Drawing._record_build_issue`` they replace —
    # so mypy does not reject the delegating calls below.
    registry: Any = None  # the drawing's AnnotationRegistry: build-issue sink + names
    coverage: Any = None  # the drawing's CoverageState
    # The drawing's render list: :meth:`place` appends here, so a render pass places an
    # annotation through the ctx seam (``ctx.place(...)``) instead of reaching into the drawing.
    items: Any = None
    # The ensured PartModel (ADR 1 (was 0008) IR) the run's passes read, threaded off the drawing so
    # they do not reach into ``dwg._part_model``. Both entry paths set it from the
    # PUBLIC ``dwg.model()`` after the model is ensured/attached.
    part_model: Any = None
    # Whether the model was DECLARED (vs detected) — the ADR 4 (was 0011) gate the orchestrator reads,
    # threaded off ``getattr(dwg, "_model_declared")``.
    model_declared: bool = False
    # Document models are declared for common-geometry authority, while their imported PMI
    # remains discovery-sourced and obeys the member's pmi= policy.
    document_member: bool = False
    document_source_annotation_ids: frozenset[int] = frozenset()
    # Activated only after the complete imported manufacturing table fits.
    manufacturing_tags: dict[str, str] | None = None
    # Per-run cache for :meth:`feature_of_hole_at` — the model is fixed after build, so a
    # per-ctx (per-run) index is correct (mirrors the old ``Drawing._hole_feature_index``).
    _hole_feature_index: Any = field(default=None, repr=False)

    def place(
        self,
        obj,
        name=None,
        view=None,
        feature=None,
        measurement=None,
        satisfaction=None,
        declaration=None,
        candidate_region=None,
        scale: float | None = None,
        measurement_span: object | None = None,
    ):
        """Place an annotation onto the drawing through this context (#817) — the render passes'
        door to the placement primitive, so a pass never reaches into the ``Drawing`` (ADR 1 (was 0005)
        §2). Registers *obj* under *name* (owning *view* + source *feature*) and appends it to the
        render list. Opaque compiler provenance is resolved here, at the placement boundary,
        so public ``annotations_of(IR feature)`` / ``drop(IR feature)`` keep their value-based
        contract for immediate passes as well as corridor-drained candidates. Returns *obj*."""
        if self.items is None:
            raise ValueError(
                "PlacementContext.place() needs the drawing's render list — construct the context "
                "with items=dwg.items (the orchestrator and Drawing verbs already do; a unit test "
                "calling a render helper must pass it too)."
            )
        return place_annotation(
            self.registry,
            self.items,
            obj,
            name,
            view,
            resolve_feature(feature),
            measurement,
            satisfaction,
            declaration=declaration,
            candidate_region=candidate_region,
            scale=scale,
            measurement_span=measurement_span,
        )

    def feature_of_hole_at(self, location):
        """The IR hole/pattern feature whose member sits at model-space *location*, or ``None``
        (#408/#639). Attributes a balloon (which carries a recognition hole, not the IR feature)
        to its feature so :meth:`Drawing.drop` clears it. Cached on the run's ctx — the model is
        fixed after build."""
        m = self.part_model
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

    def record_issue(
        self,
        severity,
        code,
        message,
        *,
        measurement=None,
        measurement_span=None,
        measurement_spans=(),
        hole_requirements=(),
        source=None,
        outcome_stage=None,
        evidence_reason=None,
        annotation_name=None,
        view=None,
        related_annotation_names=(),
    ) -> None:
        """Record a build-time lint issue on the run's registry (#639). Replaces the passes'
        old `dwg._record_build_issue`."""
        ids: tuple
        if measurement is None:
            ids = ()
        elif isinstance(measurement, (list, tuple)):
            ids = tuple(item for item in measurement if item is not None)
        else:
            ids = (measurement,)
        spans = (
            tuple(measurement_spans)
            if measurement_spans
            else ((measurement_span,) if measurement_span is not None else ())
        )
        if isinstance(source, (list, tuple)):
            source_ids = tuple(str(item) for item in source if item)
        else:
            source_ids = (str(source),) if source else ()
        self.registry.record_issue(
            LintIssue(
                severity=severity,
                code=code,
                message=message,
                measurement_ids=ids,
                hole_requirement_ids=tuple(hole_requirements),
                source_ids=source_ids,
                outcome_stage=outcome_stage,
                measurement_spans=spans,
                evidence_reason=evidence_reason,
                annotation_name=annotation_name,
                view=view,
                related_annotation_names=tuple(related_annotation_names),
            )
        )

    def reset_issues(self) -> None:
        self.registry.reset_issues()

    def drop_issues(self, *codes) -> None:
        self.registry.drop_issues(codes)

    def drop_issues_where(self, code, predicate) -> None:
        """Resolve only *code* findings accepted by *predicate*.

        Fallbacks can cover one semantic subset of a shared layout pass. Keeping this
        selection in the issue owner prevents a renderer from rebuilding the issue list.
        """
        self.registry.drop_issues_where((code,), predicate)


def register_corridor(ctx, key, strip, view, axis, tier, cand):
    """Queue a :class:`CorridorCandidate` under a shared corridor *key* so one
    :func:`drain_corridors` places the whole cross-pass set together (ADR 2 (was 0009) end state).
    The first registration for a key fixes its ``(strip, view, axis)``; mixed producers on
    the same corridor use the largest requested tier so spacing is not registration-order
    dependent."""
    b = ctx.corridor_batch.setdefault(
        key, {"strip": strip, "view": view, "axis": axis, "tier": tier, "cands": []}
    )
    b["tier"] = max(b["tier"], tier)
    b["cands"].append(cand)


def _drain_interior_dimensions(ctx, dwg) -> None:
    """Place automatic fallbacks and declared lanes as one whole-annotation batch.

    Automatic jobs enter only after a genuine exterior failure. Declared jobs contribute
    one compiler-derived feature-relative position. The stage moves complete dimensions,
    admits candidates only when their label is wholly interior or wholly exterior, and
    requires the complete annotation to clear fixed ink. The generic exact assignment
    then arbitrates pairwise conflicts; a job with no survivor calls its original drop
    handler unchanged. When tracing is enabled, the same stage records candidate
    positions and rejection categories without running a second placement solve.
    """

    jobs = getattr(ctx, "interior_dimensions", None)
    if not jobs:
        return
    ctx.interior_dimensions = []
    trace_event = (
        ctx.trace.pass_event("interior_dimension_assignment", assignment="joint")
        if ctx.trace is not None
        else None
    )
    trace_rejections: dict[int, set[str]] | None = (
        {id(job): set() for job in jobs} if trace_event is not None else None
    )
    page = _drawing_bounds(dwg)
    candidates_by_job: list[tuple[InteriorDimensionCandidate, ...]] = []
    costs_by_job: list[tuple[float, ...]] = []

    def reject(job, reason: str) -> None:
        if job.rejection_reasons is not None and reason not in job.rejection_reasons:
            job.rejection_reasons.append(reason)
        if trace_rejections is not None:
            trace_rejections[id(job)].add(reason)

    def record(job, job_candidates, job_costs, choice, outcome, reason=None) -> None:
        _record_interior_dimension_trace(
            trace_event,
            trace_rejections,
            job,
            job_candidates,
            job_costs,
            choice,
            outcome,
            reason,
        )

    for job in jobs:
        bounds = dwg.view_bounds(job.view)
        label_clear = view_label_clearance(dwg, job.view)
        if bounds is None or label_clear is None:
            reject(job, "view_geometry_unavailable")
            candidates_by_job.append(())
            costs_by_job.append(())
            continue
        axis = 1 if job.side in {"above", "below"} else 0
        boundary = {
            "above": bounds[3],
            "below": bounds[1],
            "right": bounds[2],
            "left": bounds[0],
        }[job.side]
        inward = -1.0 if job.side in {"above", "right"} else 1.0
        candidates: list[InteriorDimensionCandidate] = []
        costs: list[float] = []
        # Eight view-relative automatic lanes bound both OCC construction and the shared
        # exact assignment. A declared lane contributes one compiler-derived position
        # relative to its physical witness instead; no public coordinate crosses this seam.
        lane_positions = (
            ((job.requested_lane, job.explicit_position),)
            if job.explicit_position is not None and job.requested_lane is not None
            else tuple((lane, boundary + inward * lane * job.lane_step) for lane in range(1, 9))
        )
        for lane, position in lane_positions:
            if position is None:
                reject(job, "candidate_position_unavailable")
                continue
            if job.explicit_position is None and not bounds[axis] < position < bounds[axis + 2]:
                break
            try:
                geometry = _interior_dimension_geometry(job, position, axis)
            except Exception:  # noqa: BLE001 — an optional lane must fail closed
                reject(job, "candidate_construction_failed")
                continue
            if geometry is None:
                continue
            dimension, label, box = geometry
            if label is None or box is None:
                reject(job, "candidate_geometry_unavailable")
                continue
            label = tuple(float(value) for value in label)
            region = _dimension_region(label, bounds, job.side)
            if region is None:
                reject(job, "view_boundary_straddle")
                continue
            if box[0] < page[0] or box[1] < page[1] or box[2] > page[2] or box[3] > page[3]:
                reject(job, "page_bounds")
                continue
            if region is DimensionCandidateRegion.INTERIOR and not label_clear(label):
                reject(job, "projected_view_ink")
                continue
            # View ownership is provenance, not a clipping boundary: ink owned
            # by an adjacent projection may still cross this view in page space.
            # Interior candidates therefore prove clearance against the complete
            # settled sheet inventory.
            if not annotation_ink_clear(dwg, dimension):
                reject(job, "settled_annotation_ink")
                continue
            candidates.append(InteriorDimensionCandidate(dimension, position, region))
            costs.append(float(lane))
        candidates_by_job.append(tuple(candidates))
        costs_by_job.append(tuple(costs))

    assignment = _assign_leader_candidates(
        costs_by_job,
        _interior_dimension_conflicts(dwg, jobs, candidates_by_job),
        priorities=[job.priority for job in jobs],
    )
    for job, job_candidates, job_costs, choice in zip(
        jobs, candidates_by_job, costs_by_job, assignment.choices, strict=True
    ):
        if choice is None:
            if job.explicit_position is not None and job_candidates:
                reject(job, "candidate_assignment_conflict")
            record(
                job,
                job_candidates,
                job_costs,
                choice,
                "dropped",
                "assignment_conflict" if job_candidates else "no_clear_candidate",
            )
            job.on_drop(job.name)
            continue
        dimension = job_candidates[choice].annotation
        if job.interior_build is not None:
            try:
                dimension = job.interior_build(job_candidates[choice].position)
                label = getattr(dimension, "label_bbox", None)
                box = _geom_box(dimension)
                selected_bounds = dwg.view_bounds(job.view)
                selected_label_clear = view_label_clearance(dwg, job.view)
            except Exception:  # noqa: BLE001 — optional fallback must fail closed
                reject(job, "candidate_rebuild_failed")
                record(
                    job, job_candidates, job_costs, choice, "dropped", "candidate_rebuild_failed"
                )
                job.on_drop(job.name)
                continue
            selected_region = job_candidates[choice].region
            if (
                label is None
                or box is None
                or selected_bounds is None
                or selected_label_clear is None
                or box[0] < page[0]
                or box[1] < page[1]
                or box[2] > page[2]
                or box[3] > page[3]
                or (
                    selected_region is DimensionCandidateRegion.INTERIOR
                    and _dimension_region(label, selected_bounds, job.side)
                    is not DimensionCandidateRegion.INTERIOR
                )
                or (
                    selected_region is DimensionCandidateRegion.EXTERIOR
                    and not _dimension_exterior(label, selected_bounds, job.side)
                )
                or (
                    selected_region is DimensionCandidateRegion.INTERIOR
                    and not selected_label_clear(label)
                )
                or not annotation_ink_clear(dwg, dimension)
            ):
                reject(job, "candidate_changed_during_commit")
                record(
                    job,
                    job_candidates,
                    job_costs,
                    choice,
                    "dropped",
                    "candidate_changed_during_commit",
                )
                job.on_drop(job.name)
                continue
        ctx.place(
            dimension,
            job.name,
            view=job.view,
            feature=job.feature,
            measurement=job.measurement,
            measurement_span=job.measurement_span,
            candidate_region=job_candidates[choice].region.value,
        )
        record(job, job_candidates, job_costs, choice, "placed")
        job.on_place(job.name)


def drain_corridors(ctx, dwg):
    """Solve every registered corridor (one :func:`solve_corridor` per strip), then clear
    the batch. Called once, after all corridor-feeding passes have registered. Takes both the
    scratch *ctx* (the batch) and *dwg* (the drawing :func:`solve_corridor` places onto).

    Corner coordination (helpers ≥0.14): perpendicular strips of one view contest the
    view corners — a tight-span dim's outside-arrow tails overhang past the view edge
    into the sibling strip's band (an 8 mm plate-thickness dim on the left strip dips
    below the view bottom; the below strip's own tight dim pokes left of the view edge).
    Solved sequentially and blind, the first drain fills the corner and the second's
    force-kept candidate hard-drops. So each solve receives the *innermost-tier
    footprint boxes* of every not-yet-drained same-view sibling's **force** candidates
    as extra obstacles: the earlier drain places clear of the corner the later one
    provably needs. Reservation is exact (the candidate's own analytical footprint, at
    the innermost position it would take), restricted to force candidates — principal
    dims that would otherwise drop rather than relocate — so best-effort occupants
    never lose capacity to it. Already-drained siblings need nothing: their dims are
    real obstacles via :func:`strip_obstacles`."""
    batches = list(ctx.corridor_batch.items())
    for i, (key, b) in enumerate(batches):
        reserves = []
        for _sk, sib in batches[i + 1 :]:
            if sib["view"] != b["view"] or sib["strip"] is None:
                continue
            s = sib["strip"]
            inner_pos = s.anchor + s.direction * s.gap
            for c in sib["cands"]:
                if c.force and c.footprint is not None:
                    box = c.footprint(inner_pos)
                    if box is not None:
                        reserves.append(box)
        solve_corridor(
            dwg,
            b["strip"],
            b["view"],
            b["axis"],
            b["cands"],
            b["tier"],
            corner_reserves=reserves,
            key=key,  # corridor identity + trace threading
            ctx=ctx,
        )
    ctx.corridor_batch = {}
    # Deferred fallthroughs (opposite-strip retries) run once every strip has drained,
    # so a retry can never preempt a corner a later sibling's force candidate needs.
    # A deferred winner retry can fail and promote a coincident loser whose own
    # opposite-strip retry is also deferred.  Drain in waves until no callback remains:
    # every wave still runs after all corridors, while a second-generation fallback
    # cannot be stranded in ``ctx.post_drain``.
    while ctx.post_drain:
        pending, ctx.post_drain = ctx.post_drain, []
        for cb in pending:
            cb()
    _drain_interior_dimensions(ctx, dwg)


def _strip_outward_reserve(name, sizes, probe_boxes, *, axis, inner_is_low, probe_origin, tier):
    size = (sizes or {}).get(name)
    if size is not None:
        idx = 1 if axis == "y" else 0
        return max(tier, size[idx] if axis == "x" else size[idx] / 2)
    box = probe_boxes.get(name)
    if box is None:
        return tier
    idx = 1 if axis == "y" else 0
    return max(tier, box[idx + 2] - probe_origin if inner_is_low else probe_origin - box[idx])


def _reserved_strip_segments(lo, hi, occupied, idx, pad):
    blocked = [(box[idx], box[idx + 2]) for box in occupied]
    segments = carve_free_segments(lo, hi, blocked, pad)
    # An exact-edge fit has one legal dimension-line coordinate. The generic
    # carve omits point segments, so retain it when no obstacle covers the point.
    if hi == lo and not any(start - pad < lo < end + pad for start, end in blocked):
        return [(lo, hi)]
    return segments


def _prepare_strip_candidate_run(run) -> None:
    """Measure footprints, carve occupied tiers, and retain hard blockers."""
    dwg, strip, view, axis, cands, tier = (
        run.dwg,
        run.strip,
        run.view,
        run.axis,
        run.cands,
        run.tier,
    )
    force, sizes, footprints = run.force, run.sizes, run.footprints
    priorities, obligation_classes = run.priorities, run.obligation_classes
    forbid, corner_reserves = run.forbid, run.corner_reserves
    trace, trace_label = run.trace, run.trace_label

    def _survival_rank(name):
        return (
            obligation_rank((obligation_classes or {}).get(name, "unknown")),
            (priorities or {}).get(name, 0.0),
        )

    tp = (
        trace.begin_pass(force=force, label=trace_label, strip=strip, view=view, axis=axis)
        if trace is not None
        else None
    )
    lo, hi, inner = strip_free_span(strip)
    idx = 1 if axis == "y" else 0
    probe_origin = lo
    probe_boxes = {
        name: (
            footprints[name](probe_origin)
            if name in (footprints or {})
            else _geom_box(build(probe_origin))
        )
        for name, build in cands
    }

    # plan_strip bounds dimension-line positions, while rendered labels extend beyond
    # them. Reserve at least one tier, and more when the measured ink needs it. The
    # existing tier minimum preserves established choices on roomy sheets; opaque
    # probes use it as their fallback. The probe origin stays fixed when the low edge moves.
    reserve = max(
        _strip_outward_reserve(
            name,
            sizes,
            probe_boxes,
            axis=axis,
            inner_is_low=inner == probe_origin,
            probe_origin=probe_origin,
            tier=tier,
        )
        for name, _build in cands
    )
    if inner == lo:
        hi -= reserve
    else:
        lo += reserve
    perp = 0 if axis == "y" else 1  # the axis the dims do NOT stack along
    pad = tier + strip.spacing  # min separation between stacked dim lines
    # Perpendicular band of these candidates. The 1-D carve projects obstacles onto the
    # stacking axis only, so an obstacle on ANOTHER strip of this view — disjoint in the
    # perpendicular axis, never actually touching — would falsely block (e.g. the overall
    # width dim below the view blocking a slot-width dim on the right strip). Filter such
    # obstacles out first. The perpendicular extent is independent of the tier position,
    # so a single probe build per candidate suffices; the corridor check below already
    # uses the full 2-D box, so it needs no such filter.
    #
    # That one probe build is also this call's entire measurement step: every
    # candidate is a fixed feature-side anchor (witness origin / leader shaft end)
    # plus a dim line that translates with the tier position, so its box at position
    # ``pos`` is the probe box with the OUTWARD stacking-axis edge shifted by
    # ``pos - lo`` — no further geometry is built to evaluate a position. The
    # segment loop below re-solves and re-checks on these predicted boxes only;
    # each finally-accepted candidate is built once and its real box re-validated
    # (a prediction miss degrades to a later-segment retry, never a collision).
    # A candidate with an analytical footprint needs no probe build at
    # all — its box at any position is computed, not measured.
    pbands = [(b[perp], b[perp + 2]) for b in probe_boxes.values() if b is not None]

    def _predicted_box(name, pos):
        fp = (footprints or {}).get(name)
        if fp is not None:
            return fp(pos)
        pb = probe_boxes.get(name)
        if pb is None:
            return None
        box = list(pb)
        # The moving edge is the one AWAY from the view (`inner`); the feature-side
        # edge is anchored geometry and stays put.
        box[idx + 2 if inner == lo else idx] += pos - probe_origin
        return tuple(box)

    named, exact_ink, exact_boxes = strip_dimension_occupancy(dwg, view, axis, exact=not sizes)
    occupied = [box for _name, box in named]
    owners = {id(box): name for name, box in named} if tp is not None else {}
    # Obstacles OUTSIDE the batch's predicted perpendicular band are invisible to the
    # carve below by design — but that makes the band prediction itself load-bearing: a
    # candidate whose real geometry exceeds its predicted band could land on one with no
    # check ever seeing it. Keep the filtered-out set: the post-build
    # validation re-checks each survivor's REAL box against it. In-band overlaps are NOT
    # validated — witness lines legitimately cross the boxes of dims stacked further in
    # (ISO 129-1), which is exactly why the carve projects onto the stacking axis only.
    out_of_band: list = []
    if pbands:
        band_lo, band_hi = min(p[0] for p in pbands), max(p[1] for p in pbands)
        in_band = [b for b in occupied if b[perp] < band_hi and b[perp + 2] > band_lo]
        out_of_band = [b for b in occupied if not (b[perp] < band_hi and b[perp + 2] > band_lo)]
        occupied = in_band
        # Corner reserves (drain_corridors): projected boxes of not-yet-drained sibling
        # corridors' force candidates. Same band relevance filter as real obstacles, but
        # NEVER in out_of_band — a reserve is a projection, not geometry, so a survivor's
        # real box must not be failed against it.
        occupied += [
            r
            for r in corner_reserves
            if r is not None and r[perp] < band_hi and r[perp + 2] > band_lo
        ]
    blockers = () if force else corridor_blockers(dwg, view, exact_leaders=bool(exact_ink))
    # The title block is drawn near the end of `_PASS_SEQUENCE`, so it is
    # never in `occupied` above. `pending_title_block_box` knows its fixed box
    # from the sheet geometry, so a strip placer can honour it regardless.
    #
    # A hard 2-D box checked against each candidate's REAL footprint in
    # `_real_box_conflict` below, the way `forbid` guards this same block for GD&T frames
    # — not an entry in the carve. The carve inflates by `pad`, the separation two
    # dimension LINES need from each other, and projects onto the stacking axis, claiming
    # every position at that coordinate; against a block this large both over-claim
    # badly, refusing dims that clear it by a millimetre. A 2-D test against the
    # candidate's own footprint refuses exactly the dims that land on it.
    #
    # `forbid` also pre-checks the PREDICTED box inside the segment solve, so a rejection
    # frees its slot for a refill in the same pass. Not mirrored here: on this corpus
    # either check alone catches every case (measured by deleting each in turn), and an
    # untested second branch is worth less than the packing it might win. The real-box
    # check is the one kept because it cannot be defeated by a prediction miss.
    #
    # Honoured under `force` too, for the reason `forbid` is: a dim kept on its natural
    # view as a last resort must still not print over the block.
    _tb = pending_title_block_box(dwg)
    keep_out = (_tb,) if _tb is not None else ()
    segs = _reserved_strip_segments(lo, hi, occupied, idx, pad)
    # Fill innermost-first (nearest the view), matching the old cursor's stack order.
    segs.sort(key=lambda s: abs((s[0] if inner == lo else s[1]) - inner))
    if tp is not None:  # the diagnosis payload: what carved this strip, and what's left
        tp["span"] = [lo, hi]
        tp["obstacles"] = [
            # owner None = a corner reserve (a projection, not placed geometry)
            {"owner": owners.get(id(b)), "box": list(b)}
            for b in occupied
        ]
        tp["out_of_band"] = len(out_of_band)
        tp["free_segments"] = [list(s) for s in segs]
    todo = list(cands)

    def _real_box_conflict(name, real, annotation=None):
        """Return the hard-obstacle reason for a built survivor, if any."""
        if real is None:
            return None
        if not force and _box_hits(real, blockers):
            return "real_box_corridor_blocked"
        fb = (forbid or {}).get(name)
        if fb is not None and _box_hits(real, (fb,)):
            return "real_box_forbid"
        if _box_hits(real, out_of_band):
            return "real_box_out_of_band"
        if _box_hits(real, keep_out):
            return "real_box_title_block"
        if (
            annotation is not None
            and not force
            and _box_hits(real, exact_boxes)
            and not annotation_ink_clear(dwg, annotation, view=view, against=exact_ink)
        ):
            return "real_ink_leader_blocked"
        return None

    run.tp, run.lo, run.hi, run.inner, run.idx, run.pad = tp, lo, hi, inner, idx, pad
    run.segs, run.todo = segs, todo
    run.blockers, run.out_of_band, run.keep_out = blockers, out_of_band, keep_out
    run.survival_rank = _survival_rank
    run.predicted_box = _predicted_box
    run.real_box_conflict = _real_box_conflict


def _solve_strip_candidate_segments(run) -> None:
    """Rank, solve, refill, and verify each free segment before accepting ink."""
    axis, tier, sizes, naturals = run.axis, run.tier, run.sizes, run.naturals
    priorities, obligation_classes, anchored = (
        run.priorities,
        run.obligation_classes,
        run.anchored,
    )
    valid_positions, forbid, force = run.valid_positions, run.forbid, run.force
    lo, inner, pad, tp, blockers = run.lo, run.inner, run.pad, run.tp, run.blockers
    _survival_rank = run.survival_rank
    _predicted_box = run.predicted_box
    _real_box_conflict = run.real_box_conflict
    todo, segs = run.todo, run.segs

    def _take_for_segment(items, n):
        if len(items) <= n:
            return items, []
        # Do not let segment-cap slicing preempt the ranked selection step.
        # `plan_strip` drops the lowest (priority, generated-key), but a narrow segment
        # can only see the candidates we hand it. Preselect the highest-priority members
        # for this segment, preserving their original order for crossing-free placement;
        # ties mirror the generated key below (inner=lo keeps later candidates, inner=hi
        # keeps earlier candidates).
        ranked = sorted(
            enumerate(items),
            key=lambda item: (
                *_survival_rank(item[1][0]),
                item[0] if inner == lo else -item[0],
            ),
            reverse=True,
        )
        chosen = {i for i, _ in ranked[:n]}
        take = [nb for i, nb in enumerate(items) if i in chosen]
        rest = [nb for i, nb in enumerate(items) if i not in chosen]
        return take, rest

    def _evaluate_segment(take, seg_lo, seg_hi):
        nat = seg_lo if inner == lo else seg_hi
        # Keys order the tiers so the FIRST candidate lands on the inner tier: for an
        # inner=lo strip that is the lowest position (ascending keys); for a below strip
        # (inner=hi) it is the highest, so the keys reverse.
        triples = [
            (
                StripCandidate(
                    f"{(k if inner == lo else len(take) - 1 - k):04d}",
                    (
                        (0.0, (naturals or {}).get(nb[0], nat))
                        if axis == "y"
                        else ((naturals or {}).get(nb[0], nat), 0.0)
                    ),
                    (sizes or {}).get(nb[0], (tier, tier)),
                    priority=(priorities or {}).get(nb[0], 0.0),
                    obligation_class=(obligation_classes or {}).get(nb[0], "unknown"),
                    anchored=(anchored or {}).get(nb[0], False),
                ),
                nb,
            )
            for k, nb in enumerate(take)
        ]
        res = plan_strip([sc for sc, _ in triples], seg_lo, seg_hi, pad, axis=axis)
        accepted = []
        rejected = []

        def _reject(name, reason):  # trace-only: why this candidate left this segment
            if tp is not None:
                tp["rejected"].append(
                    {"name": name, "reason": reason, "segment": [seg_lo, seg_hi]}
                )

        for sc, (name, build) in triples:
            pos = res.placed.get(sc.key)
            if pos is None:  # segment over its estimated capacity (shouldn't occur)
                _reject(name, "over_capacity")
                rejected.append((name, build))
                continue
            valid_position = (valid_positions or {}).get(name)
            if valid_position is not None and not valid_position(pos):
                _reject(name, "geometric_clearance")
                rejected.append((name, build))
                continue
            # Predicted box, not built geometry: the refill loop re-evaluates
            # every already-accepted candidate each iteration, so building here made
            # the drain quadratic in OCC builds.
            box = _predicted_box(name, pos)
            if box is None:  # probe didn't bbox — measure the old way, once per check
                box = _geom_box(build(pos))
            if (
                not force and box is not None and _box_hits(box, blockers)
            ):  # corridor crosses a leader
                _reject(name, "corridor_blocked")
                rejected.append((name, build))
                continue
            # A forbidden box (the title block) is rejected even under force — it is
            # placed after the drain, so the strip carve can't see it; a force-kept GD&T frame
            # must still not stack onto it. `forbid` maps names to their box (only GD&T sets it,
            # so dims are byte-identical). Returned unplaced → the caller's on_drop fallthrough.
            fb = (forbid or {}).get(name)
            if fb is not None and box is not None and _box_hits(box, (fb,)):
                _reject(name, "forbid_box")
                rejected.append((name, build))
                continue
            accepted.append(((name, build), pos))
        return accepted, rejected

    solved = []
    placed_positions = {}
    for seg_lo, seg_hi in segs:
        if not todo:
            break
        cap = int((seg_hi - seg_lo) / pad) + 1
        take, todo = _take_for_segment(todo, cap)
        rejected_total = []
        while take:
            accepted, rejected = _evaluate_segment(take, seg_lo, seg_hi)
            rejected_total.extend(rejected)
            vacancies = cap - len(accepted)
            if vacancies <= 0 or not todo:
                break
            fill, todo = _take_for_segment(todo, vacancies)
            take = [nb for nb, _pos in accepted] + fill
        # Build each survivor once at its solved position and re-validate the real box:
        # a prediction miss is returned to the pool for
        # the next segment — exactly where a same-segment rejection would have sent it.
        placed = []
        for (name, build), pos in accepted:
            dim = build(pos)
            real = _geom_box(dim)
            if reason := _real_box_conflict(name, real, dim):
                if tp is not None:
                    tp["rejected"].append({"name": name, "reason": reason})
                rejected_total.append((name, build))
                continue
            placed.append((name, dim))
            placed_positions[name] = pos
            if tp is not None:
                tp["placed"].append({"name": name, "pos": pos})
        todo = todo + rejected_total
        solved.extend(placed)

    run.solved, run.placed_positions, run.todo = solved, placed_positions, todo


def _adjust_strip_candidate_labels(run) -> None:
    """Reuse clear lateral tiers and shift dimension labels as one batch."""
    dwg, view, axis, cands = (
        run.dwg,
        run.view,
        run.axis,
        run.cands,
    )
    anchored, valid_positions = run.anchored, run.valid_positions
    lo, hi, inner, pad, tp = run.lo, run.hi, run.inner, run.pad, run.tp
    placed_positions, solved = run.placed_positions, run.solved
    _real_box_conflict = run.real_box_conflict
    if solved:
        assert len({name for name, _dim_obj in solved}) == len(solved), (
            "strip survivor names must be unique before label selection"
        )
        # A dimension consumes a tier only where its actual horizontal footprint lies.
        # The strip solve above gives every item a separate height; on the candidate
        # path, let a later dimension share an inner height when its full X extent is
        # disjoint from every occupant there. Build and check the real geometry before
        # accepting the move: labels, witness lines, page bounds and fixed furniture
        # all participate, while the baseline layout keeps its established result.
        if axis == "y" and layout_flag(
            "lateral_tier_reuse", "DRAFTWRIGHT_EXPERIMENT_LATERAL_TIER_REUSE"
        ):
            builds = dict(cands)
            page = (
                _drawing_bounds(dwg) if hasattr(dwg, "page_w") and hasattr(dwg, "page_h") else None
            )
            for lane_index in sorted(
                range(len(solved)),
                key=lambda i: abs(placed_positions[solved[i][0]] - inner),
            ):
                name, original = solved[lane_index]
                if not isinstance(original, (Dimension, SafeDimension)) or (anchored or {}).get(
                    name, False
                ):
                    continue
                current = placed_positions[name]
                # A prior corridor may have consumed the inner tier and forced
                # this entire batch two tiers out. After the shared-height move,
                # probe the vacant tier immediately inward as well as occupied
                # tiers. The real-ink check below decides whether that gap is
                # usable; a projected strip carve alone cannot know.
                targets = set(placed_positions.values())
                if layout_flag(
                    "vacant_tier_compaction", "DRAFTWRIGHT_EXPERIMENT_VACANT_TIER_COMPACTION"
                ):
                    compact_target = current + (pad if inner > current else -pad)
                    if lo - 1e-6 <= compact_target <= hi + 1e-6:
                        targets.add(compact_target)
                for target in sorted(targets, key=lambda pos: abs(pos - inner)):
                    if abs(target - inner) >= abs(current - inner) - 1e-6:
                        break
                    if not layout_flag(
                        "vacant_tier_compaction", "DRAFTWRIGHT_EXPERIMENT_VACANT_TIER_COMPACTION"
                    ) and not any(
                        abs(pos - target) < 1e-6
                        for key, pos in placed_positions.items()
                        if key != name
                    ):
                        continue
                    valid = (valid_positions or {}).get(name)
                    if valid is not None and not valid(target):
                        continue
                    candidate = builds[name](target)
                    box = _geom_box(candidate)
                    if box is None or _real_box_conflict(name, box, candidate):
                        continue
                    if page is not None and not (
                        page[0] <= box[0]
                        and page[1] <= box[1]
                        and box[2] <= page[2]
                        and box[3] <= page[3]
                    ):
                        continue
                    occupants = [
                        dim
                        for key, dim in solved
                        if key != name and abs(placed_positions[key] - target) < 1e-6
                    ]
                    if not all(
                        (other_box := _geom_box(other)) is not None
                        and (box[2] + 0.5 <= other_box[0] or other_box[2] + 0.5 <= box[0])
                        for other in occupants
                    ):
                        continue
                    if not annotation_ink_clear(
                        dwg,
                        candidate,
                        view=view,
                        additional=[dim for key, dim in solved if key != name],
                    ):
                        continue
                    solved[lane_index] = (name, candidate)
                    placed_positions[name] = target
                    if tp is not None:
                        next(item for item in tp["placed"] if item["name"] == name)["pos"] = target
                        tp.setdefault("lateral_tier_reuse", []).append(name)
                    break
        natural_solved = dict(solved)
        # Local dimensions already participate through strip occupancy, and dimensions
        # owned by another view retain their independent corridor/repair contract.  The
        # missing class is non-dimension ink whose semantic owner is another view: an
        # outboard leader can physically enter this corridor even though the projected
        # view blocks are disjoint.
        committed = []
        for name, annotation in dwg.iter_annotations():
            owner = dwg.view_of(name)
            if (
                owner is not None
                and owner != view
                and not isinstance(annotation, (Dimension, SafeDimension, AngularDimension))
                and type(annotation).__name__ not in CROSSABLE_TYPES
            ):
                committed.append((name, annotation))
        committed_names = {name for name, _annotation in committed}
        adjusted_batch = prevent_dimension_label_ink(
            [*committed, *solved],
            page=(
                _drawing_bounds(dwg) if hasattr(dwg, "page_w") and hasattr(dwg, "page_h") else None
            ),
            immutable={
                *committed_names,
                *(name for name, _dim_obj in solved if (anchored or {}).get(name, False)),
            },
            perpendicular_step=pad,
        )
        adjusted = adjusted_batch[len(committed) :]
        # A label shift normally stays inside the dimension's measured span and
        # therefore inside its already-validated footprint.  Keep the validation
        # contract explicit nevertheless: if an unusual helper/style grows the real
        # box into a hard obstacle, retain the natural survivor.  The lint backstop then
        # records the infeasible ink conflict instead of this prevention step trading
        # it for a structural collision.
        solved = []
        for name, dim in adjusted:
            natural = natural_solved[name]
            if dim is natural:
                solved.append((name, dim))
                continue
            real = _geom_box(dim)
            solved.append((name, natural if _real_box_conflict(name, real, dim) else dim))
    run.solved = solved


def _compact_strip_candidate_ink(run) -> None:
    """Try bounded contraction against settled and same-batch ink."""
    dwg, view, cands = run.dwg, run.view, run.cands
    compact_candidates, anchored, forbid = (run.compact_candidates, run.anchored, run.forbid)
    idx, tp, solved, todo = run.idx, run.tp, run.solved, run.todo
    # Some annotation ink can enclose large empty rectangles. After the shared
    # strip solve, try a bounded contraction using actual segments and labels
    # against both committed ink and this batch. Never move an anchored item.
    active_names = {name for name, _build in cands}
    label_clear = view_label_clearance(dwg, view) if compact_candidates else None
    for name, alternatives in (compact_candidates or {}).items():
        if name not in active_names:
            continue
        if (anchored or {}).get(name, False):
            continue
        index = next((i for i, (key, _dim) in enumerate(solved) if key == name), None)
        original = solved[index][1] if index is not None else None
        others = [item for key, item in solved if key != name]
        for candidate in alternatives(original):
            if (
                original is not None
                and hasattr(candidate, "arc_radius")
                and hasattr(original, "arc_radius")
                and candidate.arc_radius >= original.arc_radius - 1e-6
            ):
                break
            box = _geom_box(candidate)
            if (
                box is None
                or box[0] < _drawing_bounds(dwg)[0]
                or box[1] < _drawing_bounds(dwg)[1]
                or box[2] > _drawing_bounds(dwg)[2]
                or box[3] > _drawing_bounds(dwg)[3]
                or ((forbid or {}).get(name) is not None and _box_hits(box, (forbid[name],)))
                or (label_clear is not None and not label_clear(candidate.label_bbox))
                or not annotation_ink_clear(dwg, candidate, additional=others)
            ):
                continue
            if index is None:
                solved.append((name, candidate))
                todo = [(key, build) for key, build in todo if key != name]
                if tp is not None:
                    label = candidate.label_bbox
                    tp["placed"].append({"name": name, "pos": (label[idx] + label[idx + 2]) / 2})
            else:
                solved[index] = (name, candidate)
                if tp is not None:
                    entry = next(item for item in tp["placed"] if item["name"] == name)
                    label = candidate.label_bbox
                    entry["strip_pos"] = entry["pos"]
                    entry["pos"] = (label[idx] + label[idx + 2]) / 2
            if tp is not None:
                trace_field = (
                    "angular_contractions"
                    if hasattr(candidate, "arc_radius")
                    else "ink_contractions"
                )
                tp.setdefault(trace_field, []).append(name)
            break
    run.solved, run.todo = solved, todo


def _resolve_required_strip_ink(run) -> None:
    """Resolve required ink with the live shared-placement helpers."""
    _resolve_required_strip_ink_owner(
        run,
        annotation_ink_clear=annotation_ink_clear,
        _geom_box=_geom_box,
        _drawing_bounds=_drawing_bounds,
    )


def place_strip_candidates(
    dwg,
    strip,
    view,
    axis,
    cands,
    tier,
    *,
    ctx,
    force=False,
    features=None,
    measurements=None,
    measurement_spans=None,
    satisfactions=None,
    declarations=None,
    sizes=None,
    forbid=None,
    priorities=None,
    obligation_classes=None,
    anchored=None,
    naturals=None,
    footprints=None,
    valid_positions=None,
    compact_candidates=None,
    ink_repair_candidates=None,
    require_clear_ink=(),
    ink_displaced=None,
    corner_reserves=(),
    trace=None,
    trace_label=None,
):
    """Collect-then-solve placement of location/feature dims on one strip (ADR 2 (was 0009)).
    The single shared strip placer that retires the ``Strip.allocate`` cursor (#150,
    P3): each candidate in *cands* — an ``(name, build(pos)->dim)`` pair — is spaced by
    one :func:`plan_strip` solve per free segment of the CARVED strip (`strip` carved
    around :func:`strip_obstacles`), replacing the per-dim ``allocate`` + ``_box_hits``
    tier-retry. *tier* is the label height (sets the inter-dim gap ``tier + spacing``).

    Occupancy is THIS view's own placed annotations plus the drawing-level obstacles no
    ortho view owns (the section hatch), recomputed per call so a dim placed earlier in
    the pass is avoided; other ortho views are disjoint (ADR 2 (was 0004)) and excluded so their
    rows never over-carve this strip. This makes the old post-hoc collision retry
    structural: a dim can never land on a bore-callout leader shaft the label-only
    occupancy missed (#133/#225/#305).

    A right/below dim also occupies the 2-D corridor back to the view edge, which the
    1-D strip carve cannot represent: a leader in that corridor is crossed no matter how
    far out the dim line lands. By default such a placement is rejected so the caller can
    route the dim to the other view (its disjoint block cannot cross this leader).

    *sizes* maps a candidate's name to its real page-mm footprint ``(w, h)``; absent
    names use the dimension default ``(tier, tier)``. A wide/tall occupant (a GD&T
    frame, #61) sets it so :func:`plan_strip` enforces its true stacking gap — over
    capacity it is relocated to the next segment or dropped, never overlapped.

    *priorities* maps a candidate's name to its within-class survival rank (#357);
    absent names default to 0. When a segment is over capacity :func:`plan_strip` drops
    the lowest ``(obligation class, priority, key)``, so a required obligation survives
    optional generated ink before authored priority decides within its class.
    An authored GD&T frame is not dropped for a lower-value auto dim purely by
    stacking-key order.

    *anchored* and *naturals* opt individual candidates into the weighted anchoring
    mode in :func:`plan_strip`. This preserves the old segment-edge natural for every
    caller that does not pass them, while letting authored pinned candidates express the
    page coordinate they asked for inside the same shared solve.

    *require_clear_ink* names candidates whose entire ink must clear both settled and
    same-batch annotations before commit. *ink_repair_candidates* supplies bounded
    feature-relative alternatives for those names; a still-conflicting candidate is
    returned to its normal drop/fallthrough path. *ink_displaced* records lower-priority
    siblings yielded to required ink so a force retry cannot restore the conflict.

    ``force=True`` skips that corridor check — the caller's last resort when no view took
    the dim cleanly: keep it on its natural view and accept the (same-feature) leader
    crossing rather than drop a real dimension (policy B). Candidates that find no strip
    tier AT ALL are still returned (a physically full strip — the caller records the
    genuine drop).

    *trace* is the opt-in :class:`SolveTrace` recorder (#736), ``None`` (default) = off
    with nil cost; :func:`solve_corridor` threads it for corridor solves, and a
    standalone caller may pass ``trace=ctx.trace`` with a *trace_label* naming its
    pass. The recorded pass carries the carved span, the in-band obstacles with their
    owning annotation names, the free segments, per-candidate placements/rejections
    (with reasons), and the unplaced leftovers."""
    if strip is None or not cands:
        return list(cands)
    run = SimpleNamespace(
        dwg=dwg,
        strip=strip,
        view=view,
        axis=axis,
        cands=cands,
        tier=tier,
        ctx=ctx,
        force=force,
        features=features,
        measurements=measurements,
        measurement_spans=measurement_spans,
        satisfactions=satisfactions,
        declarations=declarations,
        sizes=sizes,
        forbid=forbid,
        priorities=priorities,
        obligation_classes=obligation_classes,
        anchored=anchored,
        naturals=naturals,
        footprints=footprints,
        valid_positions=valid_positions,
        compact_candidates=compact_candidates,
        ink_repair_candidates=ink_repair_candidates,
        require_clear_ink=require_clear_ink,
        ink_displaced=ink_displaced,
        corner_reserves=corner_reserves,
        trace=trace,
        trace_label=trace_label,
    )
    _prepare_strip_candidate_run(run)
    _solve_strip_candidate_segments(run)
    _adjust_strip_candidate_labels(run)
    _compact_strip_candidate_ink(run)
    _resolve_required_strip_ink(run)
    return _commit_strip_candidate_run(run)


def carve_free_position(dwg, strip, view, axis, tier, perp_span, *, outermost=False):
    """The single free tier POSITION on *strip* at which a dim of height *tier* spanning
    *perp_span* ``(lo, hi)`` on the perpendicular axis clears every placed obstacle in
    *view* — the innermost (nearest the view) tier by default, or the outermost fitting
    one when *outermost*. Returns the dim-line page coord, or None if the strip is full.

    The position-returning counterpart of :func:`place_strip_candidates` (which batches,
    builds and adds): a caller that needs a dim's assigned position BEFORE building the
    next — the height-ladder leapfrog chain, where each step dim's witness base is the
    previous dim's line — uses this. Same carve: outer-label tier reservation, the
    perpendicular-band filter (*perp_span* drops obstacles disjoint from this dim's own
    perpendicular extent), and innermost-first fill.

    **No corridor check, by construction — not just omission.** This avoids obstacle
    *tiers* on the strip but does not reject a position whose witness *corridor* (feature
    → dim line, across *perp_span*) crosses a leader/callout. Crucially, a single-position
    return *cannot* fix a corridor crossing by choosing a different tier: every tier on
    one side shares that corridor, and a farther tier's corridor is a **superset** of a
    nearer one's, so the innermost free tier this already returns has the shortest
    corridor and the fewest crossings — moving outward only adds crossings. Corridor
    avoidance is therefore inherently a **relocation** problem (reject this position →
    place on another view/side), which is :func:`place_strip_candidates`' job and out of
    scope for a position return. Per caller: the height-ladder chain has no alternate
    view (correct to omit); public ``Drawing.place_dim`` takes the view AND side from the
    caller, so it cannot relocate; the PMI dim helpers already fall through sides
    (``_try_above(...) or _try_below(...)``) and are where a corridor-reject would go if
    ever wanted. Left as a documented known-limitation — the crossing is unobserved on
    the corpus (the cleanliness ratchet would catch it)."""
    if strip is None:
        return None
    lo, hi, inner = strip_free_span(strip)
    idx = 1 if axis == "y" else 0
    perp = 0 if axis == "y" else 1
    pad = tier + strip.spacing
    band_lo, band_hi = perp_span
    occ = [
        b
        for b in strip_obstacles(dwg, view=view, crossable=CROSSABLE_TYPES)
        if b[perp] < band_hi and b[perp + 2] > band_lo
    ]
    segs = carve_free_segments(lo, hi, [(b[idx], b[idx + 2]) for b in occ], pad)
    # A segment holds the dim iff it is at least `tier` wide (the label height). This IS
    # the outer-label reservation — inclusive at the boundary (a strip exactly `gap+tier`
    # wide fits one dim, as the old `allocate` did) — so it must NOT be combined with a
    # separate `hi -= tier` pull-in, which would double-reserve and drop that dim.
    fitting = [s for s in segs if s[1] - s[0] >= tier - 1e-9]
    if not fitting:
        return None
    if inner == lo:  # inner edge = seg lo; outermost = the segment reaching furthest out
        seg = max(fitting, key=lambda s: s[1]) if outermost else min(fitting, key=lambda s: s[0])
        return seg[0]
    seg = min(fitting, key=lambda s: s[0]) if outermost else max(fitting, key=lambda s: s[1])
    return seg[1]
