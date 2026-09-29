"""Shared lowering and placement of machined feature leader jobs.

The family passes supply compiler-approved content; this owner expands their physical
anchors and submits jobs to the late shared leader inventory (ADR 2).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from itertools import tee

from build123d_drafting.helpers import DEFAULT_FONT_PATH

from draftwright.annotations.leaders import (
    FeatureLeaderCandidate,
    FeatureLeaderJob,
    LeaderCandidateRegion,
    LeaderRegionPolicy,
)


@dataclass(frozen=True)
class MachinedLeaderBindings:
    """Live render seams supplied by the public from_model pass."""

    wrap_callout_text: Callable
    text_size: Callable
    leader_callout_geometry: Callable
    effective_leader_region_policy: Callable
    view_label_clearance: Callable
    feature_leader_candidates: Callable
    Leader: Callable
    RoutedLeader: Callable
    sheet_leader_fallback: Callable
    analytical_leader_lands_clear: Callable
    collect_feature_leader: Callable
    place_feature_leader_jobs: Callable[..., int]


def place_machined_leader_jobs(
    dwg,
    a,
    jobs,
    *,
    noun,
    drop_code,
    ctx,
    geom_clear=False,
    joint=False,
    expand_lanes=True,
    region_policy=LeaderRegionPolicy.EXTERIOR,
    source_ids_by_name=None,
    source_drop_severity="warning",
    priority=0.0,
    straight_only_names=frozenset(),
    bindings: MachinedLeaderBindings,
) -> int:
    """Lower every machined callout to the one shared ``FeatureLeaderJob`` path.

    Post-drain families join the canonical late inventory.  Pre-drain families
    are solved immediately through that same analytical machinery with its lazy
    producer floor, preserving both their semantic stage and first-clear order.
    ``region_policy`` lets a later compiler stage opt a complete feature family
    into the shared region adapter without changing other producers.
    """

    source_ids_by_name = source_ids_by_name or {}
    family_region_policy = LeaderRegionPolicy(region_policy)
    late_inventory = joint and getattr(ctx, "feature_leaders", None) is not None
    feature_jobs = []
    interior_clearance_by_view = {}
    for name, view, silhouette, label, raw_candidates, measurement in jobs:
        straight_only = name in straight_only_names
        visible_label = bindings.wrap_callout_text(str(label), dwg.draft.font_size)
        (
            joint_interior_anchors,
            joint_exterior_anchors,
            fallback_interior_anchors,
            fallback_exterior_anchors,
            recovery_anchors,
        ) = tee(iter(raw_candidates), 5)
        label_width, label_height = bindings.text_size(
            visible_label,
            float(dwg.draft.font_size),
            getattr(dwg.draft, "font_path", DEFAULT_FONT_PATH),
            getattr(dwg.draft, "font", "Arial"),
        )
        label_box = (
            (0.0, 0.0, label_width, label_height)
            if label_width > 0.0 and label_height > 0.0
            else None
        )

        def _analytical_geometry(tip, elbow, _feature, *, _label_box=label_box):
            if _label_box is None:
                return None
            return bindings.leader_callout_geometry(tip, elbow, dwg.draft, callout_box=_label_box)

        effective_region_policy = bindings.effective_leader_region_policy(
            family_region_policy,
            getattr(a, "leader_region", "auto"),
        )
        if (
            effective_region_policy is not LeaderRegionPolicy.EXTERIOR
            and view not in interior_clearance_by_view
        ):
            interior_clearance_by_view[view] = bindings.view_label_clearance(dwg, view)
        interior_label_clear = interior_clearance_by_view.get(view)

        def _lane_candidates(
            _interior_anchors=joint_interior_anchors,
            _exterior_anchors=joint_exterior_anchors,
            _silhouette=silhouette,
            _analytical_geometry=_analytical_geometry,
            _interior_label_clear=interior_label_clear,
            _region_policy=effective_region_policy,
            _straight_only=straight_only,
        ):
            if _straight_only:
                yield from _exterior_anchors
                return
            spacing = dwg.draft.font_size + 2 * dwg.draft.pad_around_text
            interior_count = 0
            if late_inventory and _region_policy is not LeaderRegionPolicy.EXTERIOR:
                # Expand the complete semantic job in one call.  Calling the adapter
                # once per physical anchor would turn its per-feature cap into
                # ``anchors × cap`` for grouped fillets and polygonal bosses.
                for candidate in bindings.feature_leader_candidates(
                    _interior_anchors,
                    region_policy=LeaderRegionPolicy.INTERIOR,
                    silhouette=_silhouette,
                    analytical_geometry=_analytical_geometry,
                    draft=dwg.draft,
                    interior_label_clear=_interior_label_clear,
                ):
                    yield candidate
                    interior_count += 1
                if _region_policy is LeaderRegionPolicy.INTERIOR:
                    return
            for tip, elbow, feature in _exterior_anchors:
                if interior_count:
                    # Once this job has proven projected-clear interior options,
                    # retain one exterior alternative per semantic anchor instead
                    # of multiplying every anchor by nine compatibility lanes.  A
                    # job with no interior option keeps all exterior lanes below;
                    # AUTO's resource fallback preserves those exterior options.
                    yield (tip, elbow, feature)
                    continue
                dx, dy = float(elbow[0]) - float(tip[0]), float(elbow[1]) - float(tip[1])
                length = math.hypot(dx, dy)
                if length <= 1e-12:
                    yield (tip, elbow, feature)
                    continue
                ux, uy = dx / length, dy / length
                px, py = -dy / length, dx / length
                for outward, lane in (
                    (0, 0),
                    (0, 1),
                    (0, -1),
                    (0, 2),
                    (0, -2),
                    (1, 0),
                    (2, 0),
                    (1, 1),
                    (1, -1),
                ):
                    yield (
                        tip,
                        (
                            float(elbow[0]) + ux * spacing * outward + px * spacing * lane,
                            float(elbow[1]) + uy * spacing * outward + py * spacing * lane,
                            0,
                        ),
                        feature,
                    )

        source_features = tuple(
            {
                id(getattr(identity, "feature", None)): getattr(identity, "feature", None)
                for identity in measurement
                if getattr(identity, "feature", None) is not None
            }.values()
        )

        def _decorate(
            leader,
            *,
            _source_features=source_features,
            _measurement=measurement,
            _semantic_label=label,
            _visible_label=visible_label,
        ):
            if _semantic_label != _visible_label:
                leader.label = _semantic_label
                leader.pdf_text = _visible_label
            leader.source_features = _source_features
            grouping_features = {
                id(identity.feature): identity.feature
                for identity in _measurement
                if getattr(identity, "parameter", None) == "grouping.count"
                and getattr(identity, "feature", None) is not None
            }
            if grouping_features:
                leader.covers_count = len(grouping_features)
            return leader

        def _build(tip, elbow, _feature, *, _label=visible_label, _decorate_fn=_decorate):
            return _decorate_fn(
                bindings.Leader(
                    tip=(tip[0], tip[1], 0), elbow=elbow, label=_label, draft=dwg.draft
                )
            )

        def _recover(
            _anchors=recovery_anchors,
            _view=view,
            _label=visible_label,
            _label_size=(label_width, label_height),
            _build_fn=_build,
            _decorate_fn=_decorate,
        ):
            for raw in _anchors:
                candidate = (
                    raw
                    if isinstance(raw, FeatureLeaderCandidate)
                    else FeatureLeaderCandidate(raw[0], raw[1], raw[2])
                )
                tip, original_elbow, feature = (
                    candidate.tip,
                    candidate.elbow,
                    candidate.feature,
                )
                search_tip = tip
                normal_stub = None
                if candidate.radial_target is not None:
                    dx = float(original_elbow[0]) - float(tip[0])
                    dy = float(original_elbow[1]) - float(tip[1])
                    length = math.hypot(dx, dy)
                    if length <= 1e-9:
                        continue
                    stub_length = min(
                        length,
                        max(
                            dwg.draft.arrow_length, dwg.draft.font_size + dwg.draft.pad_around_text
                        ),
                    )
                    normal_stub = (
                        float(tip[0]) + dx * stub_length / length,
                        float(tip[1]) + dy * stub_length / length,
                    )
                    search_tip = normal_stub

                def build_at(elbow, _tip=tip, _feature=feature):
                    if normal_stub is not None:
                        return _decorate_fn(
                            bindings.RoutedLeader(_tip, (normal_stub,), elbow, _label, dwg.draft)
                        )
                    return _build_fn(_tip, (*elbow, 0), _feature)

                def build_routed(bends, elbow, _tip=tip):
                    route_bends = (normal_stub, *bends) if normal_stub is not None else bends
                    return _decorate_fn(
                        bindings.RoutedLeader(_tip, route_bends, elbow, _label, dwg.draft)
                    )

                annotation = bindings.sheet_leader_fallback(
                    dwg,
                    search_tip,
                    _view,
                    build_at,
                    build_routed,
                    _label_size,
                )
                if annotation is not None:
                    return annotation, feature
            return None

        def _fallback_accept(
            candidate,
            obstacles,
            page,
            *,
            _silhouette=silhouette,
            _geom_clear=geom_clear,
            _label=label,
        ):
            if candidate.region is LeaderCandidateRegion.INTERIOR:
                return True
            return bindings.analytical_leader_lands_clear(
                candidate,
                obstacles,
                _silhouette,
                page,
                label=_label,
                geom_clear=_geom_clear,
            )

        source_ids = tuple(source_ids_by_name.get(name, ()))

        def _exterior_floor(_anchors=fallback_exterior_anchors):
            for raw in _anchors:
                candidate = raw if isinstance(raw, FeatureLeaderCandidate) else None
                if candidate is None or candidate.region is LeaderCandidateRegion.EXTERIOR:
                    yield raw

        def _compact_candidates(
            _anchors,
            *,
            _policy=effective_region_policy,
        ):
            for raw in _anchors:
                candidate = (
                    raw
                    if isinstance(raw, FeatureLeaderCandidate)
                    else FeatureLeaderCandidate(*raw)
                )
                if (
                    _policy is LeaderRegionPolicy.AUTO
                    or (
                        _policy is LeaderRegionPolicy.INTERIOR
                        and candidate.region is LeaderCandidateRegion.INTERIOR
                    )
                    or (
                        _policy is LeaderRegionPolicy.EXTERIOR
                        and candidate.region is LeaderCandidateRegion.EXTERIOR
                    )
                ):
                    yield candidate if isinstance(raw, FeatureLeaderCandidate) else raw

        def _on_drop(
            reason,
            *,
            _source_ids=source_ids,
            _label=label,
            _measurement=measurement,
        ):
            validation = reason == "geometry_validation"
            detail = "rendered geometry validation failed" if validation else "no clear room"
            severity = (
                ("error" if _source_ids else "warning")
                if source_drop_severity == "source"
                else source_drop_severity
            )
            ctx.record_issue(
                severity,
                drop_code,
                f"{noun} callout {_label} not placed ({detail})",
                measurement=_measurement,
                source=_source_ids,
                outcome_stage="validation" if validation else "placement",
            )

        if late_inventory:
            candidates = (
                _lane_candidates() if expand_lanes else _compact_candidates(joint_exterior_anchors)
            )
            if effective_region_policy is LeaderRegionPolicy.INTERIOR:
                fallback_candidates = (
                    _lane_candidates(
                        _interior_anchors=fallback_interior_anchors,
                        _exterior_anchors=fallback_exterior_anchors,
                    )
                    if expand_lanes
                    else _compact_candidates(
                        fallback_exterior_anchors,
                        _policy=LeaderRegionPolicy.INTERIOR,
                    )
                )
            else:
                # AUTO's bounded-resource fallback remains the exact established
                # exterior floor; extra interior anchors must not displace a complete
                # incumbent. EXTERIOR naturally uses that same floor.
                fallback_candidates = _exterior_floor()
        else:
            candidates = joint_exterior_anchors
            fallback_candidates = _exterior_floor()

        feature_jobs.append(
            FeatureLeaderJob(
                name=name,
                view=view,
                silhouette=silhouette,
                label=label,
                candidates=candidates,
                build=_build,
                analytical_geometry=_analytical_geometry,
                measurement=tuple(measurement),
                noun=noun,
                drop_code=drop_code,
                priority=priority,
                fallback_candidates=fallback_candidates,
                fallback_accept=_fallback_accept,
                interior_label_clear=interior_label_clear,
                allow_policy_b_fixed=True,
                on_drop=(_on_drop if source_ids or source_drop_severity == "source" else None),
                recover=None if straight_only else _recover,
            )
        )

    if late_inventory:
        for job in feature_jobs:
            bindings.collect_feature_leader(ctx, job)
        return 0
    return bindings.place_feature_leader_jobs(
        dwg,
        a,
        ctx,
        feature_jobs,
        producer_floor=not late_inventory,
    )
