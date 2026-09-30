"""Bounded same-batch dimension-label ink repair (ADR 2)."""

from __future__ import annotations

import math
from typing import Any

from draftwright._core import _anno_box, _copy_dimension_spec_riders
from draftwright._geometry import _boxes_overlap, _segment_clip_extent
from draftwright.annotations._dimension_ink import _dimension_probe_ink, _DimensionInkProbe
from draftwright.linting.ink_overlap import MIN_CROSSING_MM, crossable_region, crossing_length
from draftwright.registry import DimensionPlacementSpec, PlacedDimension

_LABEL_INK_CLEARANCE_MM = 0.25


def _placement_spec(dim):
    if isinstance(dim, _DimensionInkProbe):
        return dim.spec
    return dim.placement_spec if isinstance(dim, PlacedDimension) else None


class _InkConflictState:
    """Shared conflict and candidate arithmetic for one bounded dimension batch."""

    def __init__(
        self,
        *,
        original,
        infos,
        box,
        segments,
        arrow_tips,
        obstacles,
        natural_obstacle_hits,
        label_clear,
        natural_centres,
        page,
    ):
        self.original = original
        self.infos = infos
        self.box = box
        self.segments = segments
        self.arrow_tips = arrow_tips
        self.obstacles = obstacles
        self.natural_obstacle_hits = natural_obstacle_hits
        self.label_clear = label_clear
        self.natural_centres = natural_centres
        self.page = page

    DEPENDENT_POSITIONS = {
        "view": (1,),
        "arrow": (1, 3),
        "line": (1, 2),
        "label": (1, 2),
        "fixed": (2,),
    }

    def conflict_involves(self, conflict, index):
        positions = self.DEPENDENT_POSITIONS.get(conflict[0])
        assert positions is not None, f"unknown dimension-ink conflict kind: {conflict[0]!r}"
        return any(conflict[position] == index for position in positions)

    def conflicts(self, batch, *, changed_index=None, previous=()):
        """Stable conflict tokens; their count is the local solve's primary objective."""
        labels = [self.box(dim) for _name, dim in batch]
        segs = [self.segments(dim) for _name, dim in batch]
        regions = [
            crossable_region(label, item=dim, segments=segments)
            for (_name, dim), label, segments in zip(batch, labels, segs, strict=True)
        ]
        tips = [
            self.arrow_tips(dim, info)
            for (_name, dim), info in zip(batch, self.infos, strict=True)
        ]
        found: set[tuple] = (
            set()
            if changed_index is None
            else {
                conflict
                for conflict in previous
                if not self.conflict_involves(conflict, changed_index)
            }
        )
        for target, (label, region) in enumerate(zip(labels, regions, strict=True)):
            if label is None or region is None:
                continue
            target_changed = changed_index is None or target == changed_index
            if target_changed and self.label_clear is not None and not self.label_clear(label):
                found.add(("view", target))
            # Helpers expose no arrow polygons.  The foreign dimension's exact
            # attachment tips still participate, closing the line-metadata gap without
            # guessing the orientation of this dimension's own inside/outside arrows.
            for source, source_tips in enumerate(tips):
                if source == target:
                    continue
                if not target_changed and source != changed_index:
                    continue
                info = self.infos[target]
                if info is None:
                    continue
                axis, other, _spec = info
                for tip_index, tip in enumerate(source_tips):
                    if (
                        label[axis] + _LABEL_INK_CLEARANCE_MM
                        < tip[axis]
                        < label[axis + 2] - _LABEL_INK_CLEARANCE_MM
                        and label[other] - 1e-6 < tip[other] < label[other + 2] + 1e-6
                    ):
                        found.add(("arrow", source, tip_index, target))
            for source, source_segments in enumerate(segs):
                if source == target:
                    continue  # the helper deliberately cuts its own line around its label
                if not target_changed and source != changed_index:
                    continue
                if crossing_length(source_segments, region) >= MIN_CROSSING_MM:
                    found.add(("line", source, target))
            if target_changed:
                found.update(
                    ("fixed", obstacle_index, target)
                    for obstacle_index, obstacle in enumerate(self.obstacles)
                    if obstacle_index not in self.natural_obstacle_hits[target]
                    and _boxes_overlap(label, obstacle)
                )
        left_indices = range(len(labels)) if changed_index is None else (changed_index,)
        for left in left_indices:
            left_box = labels[left]
            if left_box is None:
                continue
            right_indices = (
                range(left + 1, len(labels))
                if changed_index is None
                else (index for index in range(len(labels)) if index != changed_index)
            )
            for right in right_indices:
                right_box = labels[right]
                if right_box is not None and _boxes_overlap(left_box, right_box):
                    found.add(("label", min(left, right), max(left, right)))
        return frozenset(found)

    def objective(self, batch, *, changed_index=None, previous=()):
        conflicts = self.conflicts(batch, changed_index=changed_index, previous=previous)
        fixed_conflicts = sum(conflict[0] == "fixed" for conflict in conflicts)
        offsets = []
        tier_offsets = []
        for (_name, dim), info, natural in zip(
            batch, self.infos, self.natural_centres, strict=True
        ):
            label = self.box(dim)
            offsets.append(
                0.0
                if info is None or natural is None or label is None
                else abs((label[info[0]] + label[info[0] + 2]) / 2.0 - natural)
            )
        for (_name, dim), (_original_name, original_dim) in zip(batch, self.original, strict=True):
            spec = _placement_spec(dim)
            original_spec = _placement_spec(original_dim)
            tier_offsets.append(
                0.0
                if spec is None or original_spec is None
                else abs(float(spec.distance) - float(original_spec.distance))
            )
        return (
            fixed_conflicts,
            len(conflicts),
            sum(value > 1e-9 for value in tier_offsets),
            round(sum(offsets), 9),
            round(max(offsets, default=0.0), 9),
            tuple(round(value, 9) for value in offsets),
        ), conflicts

    def centres_for(self, index, batch):
        info = self.infos[index]
        target = batch[index][1]
        label = self.box(target)
        if info is None or label is None:
            return ()
        axis, other, spec = info
        current_centre = (label[axis] + label[axis + 2]) / 2.0
        half = (label[axis + 2] - label[axis]) / 2.0
        # The centre normally remains attached to its measured span. For a short
        # dimension whose text is wider than that span, allow the conventional
        # outside-label positions beside its witness lines as well: an unrelated
        # dimension may have a witness at the span midpoint, making every centred
        # position illegible even though there is clear space just outside.
        lo, hi = sorted((float(spec.p1[axis]), float(spec.p2[axis])))
        if 2.0 * half > hi - lo:
            lo -= half + _LABEL_INK_CLEARANCE_MM
            hi += half + _LABEL_INK_CLEARANCE_MM
        else:
            lo -= MIN_CROSSING_MM
            hi += MIN_CROSSING_MM
        if self.page is not None:
            lo = max(lo, float(self.page[axis]) + half)
            hi = min(hi, float(self.page[axis + 2]) - half)
        if lo > hi:
            return ()
        choices = {min(max(current_centre, lo), hi), lo, hi}
        for source, ((_name, dim), source_info) in enumerate(zip(batch, self.infos, strict=True)):
            source_label = self.box(dim)
            if source != index and source_label is not None:
                # Existing labels are obstacles too; moving out of line-work must not
                # exchange one illegibility defect for text-on-text.
                if (
                    source_label[other] < label[other + 2]
                    and source_label[other + 2] > label[other]
                ):
                    choices.add(source_label[axis] - half - _LABEL_INK_CLEARANCE_MM)
                    choices.add(source_label[axis + 2] + half + _LABEL_INK_CLEARANCE_MM)
            if source == index:
                continue
            for tip in self.arrow_tips(dim, source_info):
                if label[other] - 1e-6 <= tip[other] <= label[other + 2] + 1e-6:
                    choices.add(tip[axis] - half - _LABEL_INK_CLEARANCE_MM)
                    choices.add(tip[axis] + half + _LABEL_INK_CLEARANCE_MM)
            # Clip each foreign segment only to this label's fixed-axis band.  The
            # resulting projection is the exact interval its centre must clear.
            band = [-1e9, -1e9, 1e9, 1e9]
            band[other], band[other + 2] = label[other], label[other + 2]
            for segment in self.segments(dim):
                clipped = _segment_clip_extent(segment[0], segment[1], tuple(band), pad=0.0)
                if clipped is None:
                    continue
                choices.add(clipped[axis] - half - _LABEL_INK_CLEARANCE_MM)
                choices.add(clipped[axis + 2] + half + _LABEL_INK_CLEARANCE_MM)
        bounded = {min(max(value, lo), hi) for value in choices if math.isfinite(value)}
        # Bound exceptional work.  Nearest candidates are the meaningful drafting
        # alternatives; the two association bounds remain present explicitly.
        nearest = sorted(bounded, key=lambda value: (abs(value - current_centre), value))[:16]
        return tuple(dict.fromkeys([*nearest, lo, hi]))


def _prevent_dimension_label_ink(
    dimensions,
    *,
    page=None,
    immutable=(),
    obstacles=(),
    perpendicular_step=None,
    label_clear=None,
    dimension_builder,
):
    """Choose small along-line label offsets for a just-built dimension batch.

    Corridor candidates used to reserve only their stacking tier while they were being
    solved.  Their *eventual* decomposed ink became an obstacle after commit, but siblings
    built in the same batch could therefore put an extension line or an external-arrow tip
    through one another's labels (#1334).  Immediate step-length chains have the identical
    gap: they build the whole row before any member is visible to strip occupancy.

    This is the shared, bounded candidate-selection seam for both producers.  It inspects
    the same public ``segments``/exact-label-region arithmetic as the lint backstop.
    A clean batch returns the same objects immediately.  Only a conflicting batch explores
    a bounded set of analytically-derived label centres, rebuilding the selected survivors
    through their ``placement_spec``.  No full lint scan and no CAD boolean participates.

    The label *centre* normally stays within half a millimetre of its measured span
    (rather than requiring the whole label to fit inside it). For a short dimension
    whose text is wider than the span, conventional outside-label positions are also
    tried; this can clear a foreign witness through the span midpoint. If the bounded
    choices cannot improve the batch, the natural deterministic
    placement survives and the normal ``annotation_ink_overlap`` lint remains explicit
    evidence of the infeasible fallback.  Names in *immutable* are never shifted (pins win).
    When ``perpendicular_step`` is supplied, a conflicting dimension may also move one
    established stacking tier away from the view. This is the generic fallback for a chain
    whose line-work cannot be cleared by moving labels along their measured spans.
    Fixed *obstacles* do not make an existing contact this local batch's responsibility, but
    no selected move may introduce a new label contact with one.
    ``label_clear`` optionally checks labels against projected part ink. Unlike
    pre-existing annotation contacts, these are conflicts to resolve in this batch.

    Returns ``[(name, dimension), ...]`` in input order.
    """

    original = list(dimensions)
    if not original or (len(original) < 2 and label_clear is None):
        return original
    immutable = set(immutable)
    obstacles = tuple(obstacles)

    def _axis_info(dim):
        spec = _placement_spec(dim)
        label = getattr(dim, "label_bbox", None)
        if spec is None or label is None:
            return None
        dx = float(spec.p2[0]) - float(spec.p1[0])
        dy = float(spec.p2[1]) - float(spec.p1[1])
        if min(abs(dx), abs(dy)) > 0.1 or max(abs(dx), abs(dy)) <= 1e-9:
            return None  # rotated labels need polygonal motion, not an AABB-axis solve
        axis = 1 if abs(dy) > abs(dx) else 0
        other = 1 - axis
        return axis, other, spec

    infos = [_axis_info(dim) for _name, dim in original]
    if not any(info is not None for info in infos):
        return original

    box_cache: dict[int, Any] = {}
    segments_cache: dict[int, Any] = {}
    tips_cache: dict[int, Any] = {}

    def _box(dim):
        key = id(dim)
        if key in box_cache:
            return box_cache[key]
        raw = getattr(dim, "label_bbox", None)
        box_cache[key] = tuple(float(value) for value in raw) if raw is not None else None
        return box_cache[key]

    def _segments(dim):
        key = id(dim)
        if key in segments_cache:
            return segments_cache[key]
        try:
            segments_cache[key] = tuple(
                (
                    (float(first[0]), float(first[1])),
                    (float(second[0]), float(second[1])),
                )
                for first, second in (getattr(dim, "segments", ()) or ())
            )
        except Exception:  # noqa: BLE001 — optional metadata fails closed to lint
            segments_cache[key] = ()
        return segments_cache[key]

    def _arrow_tips(dim, info):
        """Dimension terminator tips from its measured endpoints and dim-line ordinate.

        Helpers expose line pieces but not the filled arrow triangles.  Their tips remain
        exactly the two measured ordinates on the label's dimension line; treating the tip
        attachment as label-blocking catches the short-span arrow-through-digit case without
        inflating every shaft into a coarse full-geometry box.
        """
        key = id(dim)
        if key in tips_cache:
            return tips_cache[key]
        if info is None or (label := _box(dim)) is None:
            return ()
        axis, other, spec = info
        line = (label[other] + label[other + 2]) / 2.0
        result = []
        for point in (spec.p1, spec.p2):
            tip = [0.0, 0.0]
            tip[axis] = float(point[axis])
            tip[other] = line
            result.append(tuple(tip))
        tips_cache[key] = tuple(result)
        return tips_cache[key]

    natural_obstacle_hits = [
        frozenset(
            obstacle_index
            for obstacle_index, obstacle in enumerate(obstacles)
            if label is not None and _boxes_overlap(label, obstacle)
        )
        for _name, dim in original
        for label in (_box(dim),)
    ]

    natural_centres = []
    for (_name, dim), info in zip(original, infos, strict=True):
        label = _box(dim)
        natural_centres.append(
            None if info is None or label is None else (label[info[0]] + label[info[0] + 2]) / 2.0
        )

    state = _InkConflictState(
        original=original,
        infos=infos,
        box=_box,
        segments=_segments,
        arrow_tips=_arrow_tips,
        obstacles=obstacles,
        natural_obstacle_hits=natural_obstacle_hits,
        label_clear=label_clear,
        natural_centres=natural_centres,
        page=page,
    )
    current = list(original)
    current_objective, conflicts = state.objective(current)
    if not conflicts:
        return current  # overwhelmingly common path: no extra Dimension construction

    cache: dict[tuple[int, float, float], Any] = {}

    def _rebuild(index, centre, distance_delta=0.0):
        key = (index, round(centre, 6), round(distance_delta, 6))
        if key in cache:
            return cache[key]
        name, dim = original[index]
        info = infos[index]
        natural = natural_centres[index]
        if info is None or natural is None:
            return dim
        axis, _other, spec = info
        direction = 1.0 if float(spec.p2[axis]) >= float(spec.p1[axis]) else -1.0
        kwargs = dict(spec.kwargs)
        kwargs["label_offset_x"] = (
            kwargs.get("label_offset_x", 0.0) + (centre - natural) * direction
        )
        distance = spec.distance + distance_delta
        rebuilt = None
        # These are the exact fields this local solve reads; styled or custom
        # dimensions keep the rendered path until their ink has an exact model.
        if (
            isinstance(spec.side, str)
            and spec.side in ("above", "below", "left", "right")
            and isinstance(kwargs.get("label"), str)
            and kwargs["label"]
            and set(kwargs) <= {"label", "label_offset_x"}
            and (
                getattr(spec.draft, "text_position", "inline"),
                getattr(spec.draft, "text_orientation", "aligned"),
            )
            == ("inline", "aligned")
            and distance > 0.0
        ):
            ink = _dimension_probe_ink(
                spec.p1,
                spec.p2,
                spec.side,
                distance,
                spec.draft,
                kwargs["label"],
                kwargs["label_offset_x"],
            )
            if ink is not None:
                rebuilt = _DimensionInkProbe(
                    ink[0],
                    ink[1],
                    ink[2],
                    DimensionPlacementSpec(
                        p1=spec.p1,
                        p2=spec.p2,
                        side=spec.side,
                        distance=distance,
                        draft=spec.draft,
                        kwargs=kwargs,
                    ),
                )
        if rebuilt is None:
            rebuilt = dimension_builder(
                spec.p1,
                spec.p2,
                spec.side,
                distance,
                spec.draft,
                **kwargs,
            )
        # The producer's coverage evidence belongs on the final rendered Dimension.
        # A cheap probe carries only collision metadata until selection completes.
        if not isinstance(rebuilt, _DimensionInkProbe):
            for attr, value in vars(dim).items():
                if attr.startswith("covers_"):
                    setattr(rebuilt, attr, value)
            _copy_dimension_spec_riders(dim, rebuilt)
        cache[key] = rebuilt
        return rebuilt

    # Deterministic steepest descent.  Every accepted move strictly improves the
    # lexicographic objective, so it terminates even when the layout is infeasible.
    for _iteration in range(max(1, 2 * len(current))):
        involved: set[int] = set()
        for conflict in conflicts:
            if conflict[0] == "arrow":
                involved.update((conflict[1], conflict[3]))  # source + crossed label
            elif conflict[0] == "line":
                involved.update((conflict[1], conflict[2]))  # source + crossed label
            elif conflict[0] == "fixed":
                involved.add(conflict[2])
            elif conflict[0] == "view":
                involved.add(conflict[1])
            else:  # label/label
                involved.update((conflict[1], conflict[2]))
        best = None
        for index in sorted(involved):
            name, _dim_obj = original[index]
            if name in immutable or infos[index] is None:
                continue
            distances = (0.0,) if perpendicular_step is None else (0.0, perpendicular_step)
            for distance_delta in distances:
                for centre in state.centres_for(index, current):
                    rebuilt = _rebuild(index, centre, distance_delta)
                    if page is not None:
                        box = _anno_box(rebuilt)
                        if box is not None and not (
                            page[0] <= box[0]
                            and box[2] <= page[2]
                            and page[1] <= box[1]
                            and box[3] <= page[3]
                        ):
                            continue
                    trial = list(current)
                    trial[index] = (name, rebuilt)
                    objective, trial_conflicts = state.objective(
                        trial, changed_index=index, previous=conflicts
                    )
                    key = (
                        objective,
                        index,
                        round(distance_delta, 9),
                        round(centre, 9),
                    )
                    if objective < current_objective and (best is None or key < best[0]):
                        best = (key, trial, objective, trial_conflicts)
        if best is None:
            break
        _key, current, current_objective, conflicts = best
        if not conflicts:
            break
    for index, (name, candidate) in enumerate(current):
        if not isinstance(candidate, _DimensionInkProbe):
            continue
        spec = candidate.spec
        rendered = dimension_builder(
            spec.p1, spec.p2, spec.side, spec.distance, spec.draft, **spec.kwargs
        )
        for attr, value in vars(original[index][1]).items():
            if attr.startswith("covers_"):
                setattr(rendered, attr, value)
        _copy_dimension_spec_riders(original[index][1], rendered)
        current[index] = (name, rendered)
    return current
