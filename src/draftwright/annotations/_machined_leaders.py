"""Shared lowering and placement of machined feature leader jobs.

The family passes supply compiler-approved content; this owner expands their physical
anchors and submits jobs to the late shared leader inventory (ADR 2).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from itertools import tee
from typing import Any

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
    attribute_annotations: Callable
    boxes_overlap: Callable


@dataclass
class _MachinedJobContext:
    """Shared bindings and placement policy for one machined feature family."""

    dwg: Any
    a: Any
    ctx: Any
    bindings: MachinedLeaderBindings
    noun: str
    drop_code: str
    geom_clear: bool
    expand_lanes: bool
    region_policy: LeaderRegionPolicy
    priority: float
    source_ids_by_name: dict
    source_drop_severity: str
    straight_only_names: frozenset
    late_inventory: bool
    interior_clearance_by_view: dict
    cross_view_clearance: bool
    foreign_boxes_by_view: dict

    def foreign_label_clear(
        self, view: str, label: tuple[float, float, float, float] | None
    ) -> bool:
        if label is None:
            return False
        if view not in self.foreign_boxes_by_view:
            self.foreign_boxes_by_view[view] = tuple(
                (box, has_label)
                for _name, owner, box, has_label in self.bindings.attribute_annotations(self.dwg)
                if owner != view
            )
        pad = self.dwg.draft.pad_around_text
        padded = (label[0] - pad, label[1] - pad, label[2] + pad, label[3] + pad)
        return not any(
            self.bindings.boxes_overlap(
                padded,
                (box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad) if has_label else box,
            )
            for box, has_label in self.foreign_boxes_by_view[view]
        )

    def lower(self, row) -> FeatureLeaderJob:
        """Lower one semantic job in its own closure frame before the next is read."""
        name, view, silhouette, label, raw_candidates, measurement = row
        bindings, dwg = self.bindings, self.dwg
        straight_only = name in self.straight_only_names
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

        def analytical_geometry(tip, elbow, _feature):
            if label_box is None:
                return None
            return bindings.leader_callout_geometry(tip, elbow, dwg.draft, callout_box=label_box)

        effective_region_policy = bindings.effective_leader_region_policy(
            self.region_policy, getattr(self.a, "leader_region", "auto")
        )
        if (
            effective_region_policy is not LeaderRegionPolicy.EXTERIOR
            and view not in self.interior_clearance_by_view
        ):
            self.interior_clearance_by_view[view] = bindings.view_label_clearance(dwg, view)
        interior_label_clear = self.interior_clearance_by_view.get(view)

        source_features = tuple(
            {
                id(getattr(identity, "feature", None)): getattr(identity, "feature", None)
                for identity in measurement
                if getattr(identity, "feature", None) is not None
            }.values()
        )

        def decorate(leader):
            if label != visible_label:
                leader.label = label
                leader.pdf_text = visible_label
            leader.source_features = source_features
            grouping_features = {
                id(identity.feature): identity.feature
                for identity in measurement
                if getattr(identity, "parameter", None) == "grouping.count"
                and getattr(identity, "feature", None) is not None
            }
            if grouping_features:
                leader.covers_count = len(grouping_features)
            return leader

        def build(tip, elbow, _feature):
            return decorate(
                bindings.Leader(
                    tip=(tip[0], tip[1], 0), elbow=elbow, label=visible_label, draft=dwg.draft
                )
            )

        def fallback_accept(candidate, obstacles, page):
            if candidate.region is LeaderCandidateRegion.INTERIOR:
                return True
            return bindings.analytical_leader_lands_clear(
                candidate, obstacles, silhouette, page, label=label, geom_clear=self.geom_clear
            )

        def exterior_floor():
            for raw in fallback_exterior_anchors:
                candidate = raw if isinstance(raw, FeatureLeaderCandidate) else None
                if candidate is None or candidate.region is LeaderCandidateRegion.EXTERIOR:
                    yield raw

        def compact_candidates(anchors, policy):
            for raw in anchors:
                candidate = (
                    raw
                    if isinstance(raw, FeatureLeaderCandidate)
                    else FeatureLeaderCandidate(*raw)
                )
                if (
                    policy is LeaderRegionPolicy.AUTO
                    or (
                        policy is LeaderRegionPolicy.INTERIOR
                        and candidate.region is LeaderCandidateRegion.INTERIOR
                    )
                    or (
                        policy is LeaderRegionPolicy.EXTERIOR
                        and candidate.region is LeaderCandidateRegion.EXTERIOR
                    )
                ):
                    yield candidate if isinstance(raw, FeatureLeaderCandidate) else raw

        source_ids = tuple(self.source_ids_by_name.get(name, ()))

        def on_drop(reason):
            validation = reason == "geometry_validation"
            detail = "rendered geometry validation failed" if validation else "no clear room"
            severity = (
                ("error" if source_ids else "warning")
                if self.source_drop_severity == "source"
                else self.source_drop_severity
            )
            self.ctx.record_issue(
                severity,
                self.drop_code,
                f"{self.noun} callout {label} not placed ({detail})",
                measurement=measurement,
                source=source_ids,
                outcome_stage="validation" if validation else "placement",
            )

        if self.late_inventory:
            candidates = (
                self.lane_candidates(
                    joint_interior_anchors,
                    joint_exterior_anchors,
                    silhouette,
                    analytical_geometry,
                    interior_label_clear,
                    effective_region_policy,
                    straight_only,
                )
                if self.expand_lanes
                else compact_candidates(joint_exterior_anchors, effective_region_policy)
            )
            if effective_region_policy is LeaderRegionPolicy.INTERIOR:
                fallback_candidates = (
                    self.lane_candidates(
                        fallback_interior_anchors,
                        fallback_exterior_anchors,
                        silhouette,
                        analytical_geometry,
                        interior_label_clear,
                        effective_region_policy,
                        straight_only,
                    )
                    if self.expand_lanes
                    else compact_candidates(fallback_exterior_anchors, LeaderRegionPolicy.INTERIOR)
                )
            else:
                fallback_candidates = exterior_floor()
        else:
            candidates = joint_exterior_anchors
            fallback_candidates = exterior_floor()

        def recover():
            return self.recover(
                recovery_anchors, view, visible_label, (label_width, label_height), build, decorate
            )

        def clear_foreign_label(box):
            return self.foreign_label_clear(view, box)

        return FeatureLeaderJob(
            name=name,
            view=view,
            silhouette=silhouette,
            label=label,
            candidates=candidates,
            build=build,
            analytical_geometry=analytical_geometry,
            measurement=tuple(measurement),
            noun=self.noun,
            drop_code=self.drop_code,
            priority=self.priority,
            obligation_class="required" if source_ids else "unknown",
            fallback_candidates=fallback_candidates,
            fallback_accept=fallback_accept,
            interior_label_clear=interior_label_clear,
            foreign_label_clear=clear_foreign_label if self.cross_view_clearance else None,
            allow_policy_b_fixed=True,
            on_drop=(on_drop if source_ids or self.source_drop_severity == "source" else None),
            recover=None if straight_only else recover,
        )

    def lane_candidates(
        self,
        interior_anchors,
        exterior_anchors,
        silhouette,
        analytical_geometry,
        interior_label_clear,
        region_policy,
        straight_only,
    ):
        """Yield interior choices before the exact exterior compatibility lanes."""
        if straight_only:
            yield from exterior_anchors
            return
        dwg, bindings = self.dwg, self.bindings
        spacing = dwg.draft.font_size + 2 * dwg.draft.pad_around_text
        interior_count = 0
        if self.late_inventory and region_policy is not LeaderRegionPolicy.EXTERIOR:
            for candidate in bindings.feature_leader_candidates(
                interior_anchors,
                region_policy=LeaderRegionPolicy.INTERIOR,
                silhouette=silhouette,
                analytical_geometry=analytical_geometry,
                draft=dwg.draft,
                interior_label_clear=interior_label_clear,
            ):
                yield candidate
                interior_count += 1
            if region_policy is LeaderRegionPolicy.INTERIOR:
                return
        for tip, elbow, feature in exterior_anchors:
            if interior_count:
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

    def recover(self, anchors, view, label, label_size, build, decorate):
        """Attempt the original local sheet route after the joint solve."""
        bindings, dwg = self.bindings, self.dwg
        for raw in anchors:
            candidate = (
                raw
                if isinstance(raw, FeatureLeaderCandidate)
                else FeatureLeaderCandidate(raw[0], raw[1], raw[2])
            )
            tip, original_elbow, feature = candidate.tip, candidate.elbow, candidate.feature
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
                    max(dwg.draft.arrow_length, dwg.draft.font_size + dwg.draft.pad_around_text),
                )
                normal_stub = (
                    float(tip[0]) + dx * stub_length / length,
                    float(tip[1]) + dy * stub_length / length,
                )
                search_tip = normal_stub

            def build_at(elbow, _tip=tip, _feature=feature, normal_stub=normal_stub):
                if normal_stub is not None:
                    return decorate(
                        bindings.RoutedLeader(_tip, (normal_stub,), elbow, label, dwg.draft)
                    )
                return build(_tip, (*elbow, 0), _feature)

            def build_routed(bends, elbow, _tip=tip, normal_stub=normal_stub):
                route_bends = (normal_stub, *bends) if normal_stub is not None else bends
                return decorate(bindings.RoutedLeader(_tip, route_bends, elbow, label, dwg.draft))

            annotation = bindings.sheet_leader_fallback(
                dwg, search_tip, view, build_at, build_routed, label_size
            )
            if annotation is not None:
                if self.cross_view_clearance and not self.foreign_label_clear(
                    view, annotation.label_bbox
                ):
                    continue
                return annotation, feature
        return None


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
    cross_view_clearance=False,
    bindings: MachinedLeaderBindings,
) -> int:
    """Lower machined callouts into the shared immediate or late leader solve."""
    source_ids_by_name = source_ids_by_name or {}
    family_region_policy = LeaderRegionPolicy(region_policy)
    late_inventory = joint and getattr(ctx, "feature_leaders", None) is not None
    family = _MachinedJobContext(
        dwg=dwg,
        a=a,
        ctx=ctx,
        bindings=bindings,
        noun=noun,
        drop_code=drop_code,
        geom_clear=geom_clear,
        expand_lanes=expand_lanes,
        region_policy=family_region_policy,
        priority=priority,
        source_ids_by_name=source_ids_by_name,
        source_drop_severity=source_drop_severity,
        straight_only_names=straight_only_names,
        late_inventory=late_inventory,
        interior_clearance_by_view={},
        cross_view_clearance=cross_view_clearance,
        foreign_boxes_by_view={},
    )
    feature_jobs = [family.lower(row) for row in jobs]
    if family.late_inventory:
        for job in feature_jobs:
            bindings.collect_feature_leader(ctx, job)
        return 0
    return bindings.place_feature_leader_jobs(
        dwg, a, ctx, feature_jobs, producer_floor=not family.late_inventory
    )
