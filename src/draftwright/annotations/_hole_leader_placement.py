"""Hole callout semantics and bounded shared-leader recovery.

The hole producer owns strip choice and furniture transactions. This owner builds
leaders with the rendered callout claim and tries the established sheet fallback.
"""

from __future__ import annotations

import math

from build123d_drafting.helpers import Leader

from draftwright.annotations.from_model import _sheet_leader_fallback
from draftwright.annotations.leaders import (
    _FeatureLeaderInvariantError,
    material_penalty_units,
    view_material,
)
from draftwright.annotations.routed import RoutedLeader


def _copy_callout_semantics(leader, callout):
    """Keep a rendered geometric callout's claim on either leader shape.

    The helpers' ``Leader`` copies its native diameter/count coverage from ``HoleCallout``;
    its geometric-callout path does not copy the callout's semantic label. Profiled-bore
    coverage and that label are draftwright-owned, so both are forwarded explicitly at this
    one construction seam.
    """
    semantic_label = getattr(callout, "label", "")
    if not semantic_label:
        raise _FeatureLeaderInvariantError(
            "a rendered hole callout must carry a non-empty semantic label"
        )
    leader.label = semantic_label
    leader.pdf_text_relative_specs = tuple(getattr(callout, "pdf_text_relative_specs", ()))
    leader.covers_profiles = getattr(callout, "covers_profiles", ())
    leader.covers_hole_requirements = getattr(callout, "covers_hole_requirements", ())
    leader.covers_hole_requirements_by_feature = getattr(
        callout, "covers_hole_requirements_by_feature", ()
    )
    leader.source_features = tuple(getattr(callout, "source_features", ()))
    leader.source_ids = tuple(getattr(callout, "source_ids", ()))
    leader.source_measurements = tuple(getattr(callout, "source_measurements", ()))
    leader.geometry_measurements = tuple(getattr(callout, "geometry_measurements", ()))
    leader.geometry_qualifiers = tuple(getattr(callout, "geometry_qualifiers", ()))
    return leader


def _profiled_callout_leader(*, callout, **kw):
    """Build a normal leader with the rendered callout's semantic claim."""

    return _copy_callout_semantics(Leader(callout=callout, **kw), callout)


def _recover_hole_leader(raw_candidates, build_leader, callout, callout_box, view, dwg, draft):
    """Try bounded sheet fallback for an unplaced shared hole leader."""
    if callout_box is None:
        return None
    size = (callout_box[2] - callout_box[0], callout_box[3] - callout_box[1])
    for index, candidate in enumerate(raw_candidates()):
        if index >= 4:
            break
        tip, feature = candidate.tip, candidate.feature
        if candidate.radial_target is not None:
            centre = candidate.radial_target.center
            radius = candidate.radial_target.radius

            def radial_tip(elbow, _centre=centre, _radius=radius):
                dx = float(elbow[0]) - _centre[0]
                dy = float(elbow[1]) - _centre[1]
                length = math.hypot(dx, dy)
                if length <= _radius:
                    return _centre
                return (
                    _centre[0] + dx * _radius / length,
                    _centre[1] + dy * _radius / length,
                )

            field = view_material(dwg, view)

            def clear_material(annotation, _field=field):
                return material_penalty_units(annotation.tip, annotation.elbow, _field) == 0

            def build_radial(elbow, _feature=feature):
                return build_leader(radial_tip(elbow), (*elbow, 0), _feature)

            annotation = _sheet_leader_fallback(
                dwg,
                centre,
                view,
                build_radial,
                label_size=size,
                tip_for_elbow=radial_tip,
                accept_candidate=clear_material,
            )
            if annotation is not None:
                return annotation, feature
            continue

        def build_at(elbow, _tip=tip, _feature=feature):
            return build_leader(_tip, (*elbow, 0), _feature)

        def build_routed(bends, elbow, _tip=tip):
            return _copy_callout_semantics(
                RoutedLeader(_tip, bends, elbow, "", draft, callout=callout),
                callout,
            )

        annotation = _sheet_leader_fallback(dwg, tip, view, build_at, build_routed, size)
        if annotation is not None:
            return annotation, feature
    return None
