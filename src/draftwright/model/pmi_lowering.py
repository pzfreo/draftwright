"""Geometry-correlated AP242 dimensional PMI lowering (#1116).

The extractor reports source semantics and recognition reports geometry.  This module is the
single correlation seam between them: a supported diameter tolerance enriches the canonical
hole/pattern dimension, while an unproven match remains a materialised authored dimension with
an explicit reason.  It deliberately knows nothing about annotation coordinates or rendering.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import replace
from decimal import Decimal
from typing import Literal, cast

from draftwright.model.ir import (
    AuthoredDimension,
    BossFeature,
    ChamferFeature,
    CylindricalReference,
    DatumRef,
    DefaultSurfaceFinish,
    DocumentNote,
    Feature,
    GeneralTolerance,
    HoleFeature,
    KnurlRequirement,
    NominalRequirement,
    PartModel,
    PatternFeature,
    PmiFeature,
    RotationalFeature,
    StepFeature,
    ThreadRequirement,
    ToleranceDecoration,
)

ToleranceValue = float | tuple[float, float]
FeatureRemap = Callable[[Feature, tuple[Feature, ...], tuple[tuple[int, ...], ...] | None], None]


def _members(feature: HoleFeature | PatternFeature):
    if isinstance(feature, PatternFeature):
        return tuple(feature.members) or (feature.member.frame.origin,)
    return tuple(feature.members) or (feature.frame.origin,)


def _diameter(feature: HoleFeature | PatternFeature) -> float:
    return float(
        feature.member.diameter if isinstance(feature, PatternFeature) else feature.diameter
    )


def _requirement(dim: AuthoredDimension):
    """Return the renderer-facing lower/upper magnitudes, or ``None`` when not toleranced."""
    if (dim.lower_bound is None) != (dim.upper_bound is None):
        raise ValueError("a limit requirement needs both lower and upper bounds")
    if dim.lower_bound is not None and dim.upper_bound is not None:
        nominal = Decimal(str(dim.value))
        lower = float(nominal - Decimal(str(dim.lower_bound)))
        upper = float(Decimal(str(dim.upper_bound)) - nominal)
    elif dim.lower_tol is not None or dim.upper_tol is not None:
        lower = float(dim.lower_tol or 0.0)
        upper = float(dim.upper_tol or 0.0)
    else:
        return None
    if lower < 0 or upper < 0:
        raise ValueError("negative deviation magnitude")
    return lower if lower == upper else (lower, upper)


def _inside(point, bbox, *, pad=1e-6) -> bool:
    return all(bbox[i] - pad <= point[i] <= bbox[i + 3] + pad for i in range(3))


def _block(dim: AuthoredDimension, reason: str) -> AuthoredDimension:
    return replace(dim, lowering_blockers=tuple(dict.fromkeys((*dim.lowering_blockers, reason))))


def _source_ids(dim: AuthoredDimension) -> tuple[str, ...]:
    return (dim.source_id,) if dim.source_id else ()


def _limit_bounds(dim: AuthoredDimension) -> tuple[float, float] | None:
    if dim.lower_bound is None or dim.upper_bound is None:
        return None
    return (float(dim.lower_bound), float(dim.upper_bound))


def _hole_tolerance_proposal(
    dim: AuthoredDimension,
    targets: Iterable[tuple[int, HoleFeature | PatternFeature]],
) -> tuple[int, tuple[int, ...], ToleranceValue] | str:
    """Correlate one source requirement, or give its exact fallback reason."""
    if dim.ref_bbox is None:
        return "unmatched hole correlation: source diameter has no referenced geometry"
    if dim.dominant_axis not in ("X", "Y", "Z"):
        return "unsupported hole correlation: source diameter has no principal bore axis"

    matches: list[tuple[int, HoleFeature | PatternFeature, tuple[int, ...]]] = []
    for owner_index, owner in targets:
        if owner.frame.axis != dim.dominant_axis.lower():
            continue
        if abs(_diameter(owner) - float(dim.value)) > max(1e-6, abs(float(dim.value)) * 1e-6):
            continue
        member_indices = tuple(
            member_index
            for member_index, point in enumerate(_members(owner))
            if _inside(point, dim.ref_bbox)
        )
        if member_indices:
            matches.append((owner_index, owner, member_indices))

    if not matches:
        return (
            f"unmatched hole correlation: no {_diameter_text(dim.value)} "
            f"{dim.dominant_axis}-axis hole member lies in the source reference bounds"
        )
    if len(matches) != 1:
        return (
            f"ambiguous hole correlation: source reference bounds match {len(matches)} "
            "canonical hole/pattern features"
        )
    owner_index, owner, member_indices = matches[0]
    if (
        isinstance(owner, PatternFeature)
        and owner.pattern not in ("grid", "linear")
        and len(member_indices) != len(_members(owner))
    ):
        return (
            "unsupported hole correlation: AP242 requirement covers only part of a "
            "canonical hole pattern"
        )
    try:
        value = _requirement(dim)
    except ValueError as exc:
        return f"unsupported hole tolerance: {exc}"
    assert value is not None
    return owner_index, member_indices, value


def _lower_pattern_member_sizes(
    feature: PatternFeature,
    member_requirements: dict[int, list[int]],
    proposals: dict[int, tuple[int, tuple[int, ...], ToleranceValue]],
    dimensions: dict[int, AuthoredDimension],
    decorations: dict,
    feature_remap: FeatureRemap | None,
) -> PatternFeature:
    points = _members(feature)
    groups = tuple(tuple(member_requirements.get(index, ())) for index in range(len(points)))
    if len(set(groups)) == 1 and not feature.member_size_requirements:
        dim_indices = sorted(set(groups[0]))
        decorations[(feature, "diameter", "bore")] = ToleranceDecoration(
            value=proposals[dim_indices[0]][2],
            source="ap242_pmi",
            source_ids=tuple(
                dict.fromkeys(
                    source_id
                    for dim_index in dim_indices
                    for source_id in _source_ids(dimensions[dim_index])
                )
            ),
            limit_bounds=_limit_bounds(dimensions[dim_indices[0]]),
        )
        return feature
    requirements = list(feature.member_size_requirements or (None,) * len(points))
    for member_index, member_dims in enumerate(groups):
        if not member_dims:
            continue
        first = member_dims[0]
        requirements[member_index] = ToleranceDecoration(
            value=proposals[first][2],
            source="ap242_pmi",
            source_ids=tuple(
                dict.fromkeys(
                    source_id
                    for dim_index in member_dims
                    for source_id in _source_ids(dimensions[dim_index])
                )
            ),
            limit_bounds=_limit_bounds(dimensions[first]),
        )
    replacement = replace(feature, member_size_requirements=tuple(requirements))
    for key, decoration in tuple(decorations.items()):
        if key[0] is feature:
            del decorations[key]
            decorations[(replacement, *key[1:])] = decoration
    if feature_remap is not None:
        feature_remap(feature, (replacement,), (tuple(range(len(points))),))
    return replacement


def _is_internal_toleranced_diameter(feature: AuthoredDimension) -> bool:
    """Select AP242 bore limits without sending external cylinders to the hole join."""
    return (
        feature.dimension_kind == "diameter"
        and feature.source == "ap242_pmi"
        and (
            not feature.cylindrical_refs
            or all(reference.sense == "internal" for reference in feature.cylindrical_refs)
        )
        and any(
            value is not None
            for value in (
                feature.lower_tol,
                feature.upper_tol,
                feature.lower_bound,
                feature.upper_bound,
            )
        )
    )


def _hole_ownership_conflict(
    owner: HoleFeature | PatternFeature, member_indices: tuple[int, ...], decorations: dict
) -> str | None:
    if (owner, "diameter", "bore") in decorations or (owner, "diameter") in decorations:
        return "ambiguous hole tolerance ownership: bore already has a tolerance"
    if isinstance(owner, PatternFeature) and owner.member_size_requirements:
        if any(owner.member_size_requirements[index] is not None for index in member_indices):
            return "ambiguous hole tolerance ownership: member already has a source requirement"
    return None


def lower_ap242_hole_tolerances(
    model: PartModel, *, feature_remap: FeatureRemap | None = None
) -> PartModel:
    """Consume confidently correlated AP242 hole-tolerance dimensions exactly once.

    A count-group is split only where member requirements differ. A grid or linear pattern
    retains its arrangement while individual source sizes remain scoped to exact members.
    Other pattern kinds still require one source requirement to cover the whole group.
    """
    targets: dict[int, HoleFeature | PatternFeature] = {
        index: feature
        for index, feature in enumerate(model.features)
        if isinstance(feature, (HoleFeature, PatternFeature))
    }
    dimensions = {
        index: feature
        for index, feature in enumerate(model.features)
        if isinstance(feature, AuthoredDimension) and _is_internal_toleranced_diameter(feature)
    }
    if not dimensions:
        return model

    # dim index -> (owner index, selected member indices, tolerance value)
    proposals: dict[int, tuple[int, tuple[int, ...], ToleranceValue]] = {}
    blocked: dict[int, str] = {}
    for dim_index, dim in dimensions.items():
        if dim.lowering_blockers:
            blocked[dim_index] = ""
            continue
        proposal = _hole_tolerance_proposal(dim, targets.items())
        if isinstance(proposal, str):
            blocked[dim_index] = proposal
        else:
            proposals[dim_index] = proposal

    # Existing authored ownership wins.  Silently replacing it with imported PMI would make
    # the same parameter have two sources and violate ADR 4 (was 0011)'s single-owner decoration map.
    for dim_index, (owner_index, _member_indices, _value) in tuple(proposals.items()):
        owner = targets[owner_index]
        if conflict := _hole_ownership_conflict(owner, _member_indices, model.decorations):
            blocked[dim_index] = conflict
            del proposals[dim_index]

    # A member cannot carry two different imported requirements.  Equal repeats are one
    # requirement with multiple source identities; conflicting ones remain explicit.
    by_member: dict[tuple[int, int], list[int]] = defaultdict(list)
    for dim_index, (owner_index, member_indices, _value) in proposals.items():
        for member_index in member_indices:
            by_member[(owner_index, member_index)].append(dim_index)
    for dim_indices in by_member.values():
        active = [index for index in dim_indices if index in proposals]
        values = {(proposals[index][2], _limit_bounds(dimensions[index])) for index in active}
        if len(values) > 1:
            for dim_index in active:
                blocked[dim_index] = (
                    "ambiguous hole tolerance ownership: one member has conflicting AP242 requirements"
                )
                proposals.pop(dim_index, None)

    lowered = set(proposals)
    incoming: dict[int, dict[int, list[int]]] = defaultdict(lambda: defaultdict(list))
    for dim_index, (owner_index, member_indices, _value) in proposals.items():
        for member_index in member_indices:
            incoming[owner_index][member_index].append(dim_index)

    decorations = dict(model.decorations)
    rebuilt: list[Feature] = []
    for feature_index, feature in enumerate(model.features):
        if feature_index in dimensions:
            if feature_index in lowered:
                continue
            dimension = dimensions[feature_index]
            rebuilt.append(
                _block(dimension, blocked[feature_index])
                if blocked.get(feature_index)
                else dimension
            )
            continue
        member_requirements = incoming.get(feature_index)
        if not member_requirements:
            rebuilt.append(feature)
            continue

        if isinstance(feature, PatternFeature):
            rebuilt.append(
                _lower_pattern_member_sizes(
                    feature,
                    member_requirements,
                    proposals,
                    dimensions,
                    decorations,
                    feature_remap,
                )
            )
            continue

        assert isinstance(feature, HoleFeature)
        points = _members(feature)
        inherited = [
            (key[1:], value)
            for key, value in tuple(decorations.items())
            if isinstance(key, tuple) and key and key[0] == feature
        ]
        for tail, _value in inherited:
            key = (feature, *tail)
            del decorations[key]
        # Group members by the requirement that owns them, retaining first-member order.
        # One source that references several members may truthfully keep a count× callout.
        # Independent sources remain separate even when their numeric tolerances are equal:
        # merging those would erase which source requirement applies to which physical member.
        GroupKey = tuple[ToleranceValue | None, tuple[int, ...]]
        groups: dict[GroupKey, list[int]] = {}
        for member_index in range(len(points)):
            member_dim_indices = tuple(member_requirements.get(member_index, ()))
            value = proposals[member_dim_indices[0]][2] if member_dim_indices else None
            groups.setdefault((value, member_dim_indices), []).append(member_index)
        replacements: list[Feature] = []
        replacement_member_groups: list[tuple[int, ...]] = []
        for (value, group_dim_indices), group_member_indices in groups.items():
            members = tuple(points[index] for index in group_member_indices)
            split = replace(
                feature,
                frame=replace(feature.frame, origin=members[0]),
                count=len(members),
                members=members,
            )
            rebuilt.append(split)
            replacements.append(split)
            replacement_member_groups.append(tuple(group_member_indices))
            for tail, inherited_value in inherited:
                decorations[(split, *tail)] = inherited_value
            if value is not None:
                ids = tuple(
                    dict.fromkeys(
                        source_id
                        for dim_index in group_dim_indices
                        for source_id in _source_ids(dimensions[dim_index])
                    )
                )
                decorations[(split, "diameter")] = ToleranceDecoration(
                    value=value,
                    source="ap242_pmi",
                    source_ids=ids,
                    limit_bounds=_limit_bounds(dimensions[group_dim_indices[0]]),
                )
        if feature_remap is not None:
            feature_remap(
                feature,
                tuple(replacements),
                tuple(replacement_member_groups),
            )

    return replace(model, features=rebuilt, decorations=decorations)


def _diameter_text(value: float) -> str:
    return f"diameter {value:g}"


def _same_number(left: float, right: float, *, abs_tol: float = 1e-6) -> bool:
    return abs(float(left) - float(right)) <= max(abs_tol, abs(float(right)) * 1e-6)


def _same_axis_line(reference: CylindricalReference, point, axis: str) -> bool:
    axis_index = "xyz".index(axis)
    return all(
        abs(reference.axis_origin[index] - float(point[index])) <= 0.01
        for index in range(3)
        if index != axis_index
    )


def _external_owner_matches(
    reference: CylindricalReference, feature: StepFeature | BossFeature
) -> bool:
    if reference.sense != "external" or reference.principal_axis != feature.frame.axis.upper():
        return False
    if not _same_number(reference.diameter, feature.diameter, abs_tol=0.01):
        return False
    if not _same_axis_line(reference, feature.frame.origin, feature.frame.axis):
        return False
    span = feature.span
    if span is None:
        return False
    axis_index = "xyz".index(feature.frame.axis)
    owner_lo, owner_hi = sorted((float(span[0][axis_index]), float(span[1][axis_index])))
    source_lo, source_hi = reference.axial_interval
    # A cylindrical source face may be a truthful subset of a recognised turned segment
    # (GRM-03's knurled head divides the face topology). It may not cross the segment ends.
    return owner_lo - 0.01 <= source_lo < source_hi <= owner_hi + 0.01


def _internal_member_matches(
    reference: CylindricalReference, feature: HoleFeature, member, bbox
) -> bool:
    if reference.sense != "internal" or reference.principal_axis != feature.frame.axis.upper():
        return False
    if not _same_number(reference.diameter, feature.diameter, abs_tol=0.01):
        return False
    if not _same_axis_line(reference, member, feature.frame.axis):
        return False
    axis_index = "xyz".index(feature.frame.axis)
    lo, hi = reference.axial_interval
    station = float(member[axis_index])
    if feature.through:
        box_lo = (bbox.min.X, bbox.min.Y, bbox.min.Z)[axis_index]
        box_hi = (bbox.max.X, bbox.max.Y, bbox.max.Z)[axis_index]
        if not (lo - 0.01 <= station <= hi + 0.01):
            return False
        if lo < box_lo - 0.01 or hi > box_hi + 0.01:
            return False
        if min(hi, box_hi) - max(lo, box_lo) <= 0.01:
            return False
        return feature.depth is None or _same_number(hi - lo, feature.depth, abs_tol=0.01)
    if feature.depth is None:
        return False
    return (
        _same_number(hi - lo, feature.depth, abs_tol=0.01)
        and min(abs(lo - station), abs(hi - station)) <= 0.01
    )


def _nominal_owner_key(feature) -> tuple:
    parameter = {
        "step": "step.diameter",
        "boss": "boss.diameter",
        "hole": "bore",
        "pattern": "bore",
        "rotational": "od",
    }[feature.kind]
    if "." not in parameter:
        parameter = f"{parameter}.diameter"
    return (feature, "nominal_requirement", parameter)


def _standalone_cylinder_blocker(references: tuple[CylindricalReference, ...]) -> str:
    """Why a cylinder group cannot truthfully anchor one standalone diameter mark."""
    if len(references) < 2:
        return ""
    first = references[0]
    directions = {
        tuple(round(component, 9) for component in reference.axis_direction)
        for reference in references
    }
    axis_lines = {
        tuple(
            round(
                origin
                - direction
                * sum(
                    component * axis_component
                    for component, axis_component in zip(
                        reference.axis_origin, reference.axis_direction, strict=True
                    )
                ),
                6,
            )
            for origin, direction in zip(
                reference.axis_origin, reference.axis_direction, strict=True
            )
        )
        for reference in references
    }
    projected_oblique_pattern = (
        first.principal_axis == "?"
        and len(directions) == 1
        and len(axis_lines) == len(references)
        and min(abs(component) for component in first.axis_direction) <= 1e-6
        and len({round(reference.radius, 6) for reference in references}) == 1
        and len({reference.sense for reference in references}) == 1
    )
    if projected_oblique_pattern:
        # The oblique renderer targets one exact member surface and retains the group as
        # multiplicity. Distinct parallel axis lines are therefore evidence for a pattern,
        # rather than a request to invent the centroid target guarded against below.
        return ""
    if any(
        any(
            abs(left - right) > 0.01
            for left, right in zip(first.axis_origin, ref.axis_origin, strict=True)
        )
        for ref in references[1:]
    ):
        return (
            "standalone diameter fallback references multiple distinct cylinder axis lines; "
            "no single truthful leader target exists"
        )
    return ""


def _apply_standalone_cylinder_blockers(model: PartModel) -> PartModel:
    """Keep the no-invented-centroid decision on every diameter fallback."""
    rebuilt: list[Feature] = []
    changed = False
    for feature in model.features:
        if isinstance(feature, AuthoredDimension) and feature.dimension_kind == "diameter":
            blocker = _standalone_cylinder_blocker(feature.cylindrical_refs)
            if blocker and blocker not in feature.rendering_blockers:
                feature = replace(
                    feature,
                    rendering_blockers=(*feature.rendering_blockers, blocker),
                )
                changed = True
        rebuilt.append(feature)
    return replace(model, features=rebuilt) if changed else model


def _nominal_owner_matches(
    dimension: AuthoredDimension,
    feature: StepFeature | BossFeature | HoleFeature | PatternFeature | RotationalFeature,
    bbox,
) -> bool:
    references = dimension.cylindrical_refs
    if isinstance(feature, (StepFeature, BossFeature)):
        return all(_external_owner_matches(reference, feature) for reference in references)
    if isinstance(feature, RotationalFeature):
        axis = feature.frame.axis
        axis_index = "xyz".index(axis)
        box_lo = (bbox.min.X, bbox.min.Y, bbox.min.Z)[axis_index]
        box_hi = (bbox.max.X, bbox.max.Y, bbox.max.Z)[axis_index]
        return all(
            reference.sense == "external"
            and reference.principal_axis == axis.upper()
            and _same_number(reference.diameter, feature.od, abs_tol=0.01)
            and _same_axis_line(reference, feature.frame.origin, axis)
            and box_lo - 0.01
            <= reference.axial_interval[0]
            < reference.axial_interval[1]
            <= box_hi + 0.01
            for reference in references
        )

    hole = feature.member if isinstance(feature, PatternFeature) else feature
    members = tuple(feature.members) or (feature.frame.origin,)
    covered: set[int] = set()
    for reference in references:
        matches = [
            index
            for index, member in enumerate(members)
            if _internal_member_matches(reference, hole, member, bbox)
        ]
        if len(matches) != 1:
            return False
        covered.add(matches[0])
    # A grouped canonical callout states the requirement for the whole group. Do not attach
    # one member's source ownership to its untargeted siblings; leave that record as the
    # standalone typed-cylinder fallback instead.
    return len(covered) == len(members)


def _pattern_nominal_members(
    dimension: AuthoredDimension, pattern: PatternFeature, bbox
) -> tuple[int, ...]:
    """Match every source cylinder to exactly one physical pattern member."""
    if pattern.pattern not in ("grid", "linear") or not pattern.members:
        return ()
    covered: set[int] = set()
    for reference in dimension.cylindrical_refs:
        matches = [
            index
            for index, member in enumerate(pattern.members)
            if _internal_member_matches(reference, pattern.member, member, bbox)
        ]
        if len(matches) != 1:
            return ()
        covered.add(matches[0])
    return tuple(sorted(covered))


def _lower_pattern_nominal_diameters(
    model: PartModel, *, feature_remap: FeatureRemap | None
) -> PartModel:
    """Give each exact pattern member its source nominal without losing its tolerance."""
    features = list(model.features)
    decorations = dict(model.decorations)
    consumed: set[int] = set()
    blocked: dict[int, str] = {}
    for dim_index, dimension in enumerate(model.features):
        if not (
            isinstance(dimension, AuthoredDimension)
            and dimension.dimension_kind == "diameter"
            and dimension.source == "ap242_pmi"
            and dimension.cylindrical_refs
            and not dimension.lowering_blockers
            and not dimension.rendering_blockers
            and all(
                value is None
                for value in (
                    dimension.lower_tol,
                    dimension.upper_tol,
                    dimension.lower_bound,
                    dimension.upper_bound,
                )
            )
        ):
            continue
        matches = [
            (index, feature, member_indices)
            for index, feature in enumerate(features)
            if isinstance(feature, PatternFeature)
            and (member_indices := _pattern_nominal_members(dimension, feature, model.bbox))
            and (len(member_indices) < feature.count or feature.member_size_requirements)
        ]
        if not matches:
            continue
        other_matches = [
            feature
            for feature in features
            if isinstance(feature, StepFeature | BossFeature | HoleFeature | RotationalFeature)
            and _nominal_owner_matches(dimension, feature, model.bbox)
        ]
        if len(matches) != 1 or other_matches:
            blocked[dim_index] = (
                "ambiguous diameter ownership: source cylinders match multiple features"
            )
            continue
        owner_index, owner, member_indices = matches[0]
        nominal = NominalRequirement(dimension.value, "ap242_pmi", _source_ids(dimension))
        if not nominal.agrees_with(owner.member.diameter):
            blocked[dim_index] = (
                f"source nominal {dimension.value!r} disagrees with canonical "
                f"bore.diameter={owner.member.diameter!r}"
            )
            continue
        if (owner, "diameter", "bore") in decorations or (owner, "diameter") in decorations:
            blocked[dim_index] = (
                "ambiguous diameter ownership: pattern bore has an authored aspect"
            )
            continue
        requirements = list(owner.member_size_requirements or (None,) * owner.count)
        if any(
            requirement is not None and requirement.source != "ap242_pmi"
            for index in member_indices
            if (requirement := requirements[index]) is not None
        ):
            blocked[dim_index] = "ambiguous diameter ownership: member has an authored aspect"
            continue
        for index in member_indices:
            requirement = requirements[index]
            requirements[index] = (
                nominal
                if requirement is None
                else replace(
                    requirement,
                    source_ids=tuple(
                        dict.fromkeys((*requirement.source_ids, *nominal.source_ids))
                    ),
                )
            )
        replacement = replace(owner, member_size_requirements=tuple(requirements))
        for key, value in tuple(decorations.items()):
            if key[0] is owner:
                del decorations[key]
                decorations[(replacement, *key[1:])] = value
        if feature_remap is not None:
            feature_remap(owner, (replacement,), (tuple(range(owner.count)),))
        features[owner_index] = replacement
        consumed.add(dim_index)
    if not consumed and not blocked:
        return model
    return replace(
        model,
        features=[
            _block(feature, blocked[index])
            if index in blocked and isinstance(feature, AuthoredDimension)
            else feature
            for index, feature in enumerate(features)
            if index not in consumed
        ],
        decorations=decorations,
    )


def lower_ap242_external_diameter_tolerances(model: PartModel) -> PartModel:
    """Attach source limits to an exactly matched external cylinder measurement."""
    owners = tuple(
        feature
        for feature in model.features
        if isinstance(feature, StepFeature | BossFeature | RotationalFeature)
    )
    candidates = {
        index: feature
        for index, feature in enumerate(model.features)
        if isinstance(feature, AuthoredDimension)
        and feature.source == "ap242_pmi"
        and feature.dimension_kind == "diameter"
        and feature.cylindrical_refs
        and all(reference.sense == "external" for reference in feature.cylindrical_refs)
        and any(
            value is not None
            for value in (
                feature.lower_tol,
                feature.upper_tol,
                feature.lower_bound,
                feature.upper_bound,
            )
        )
    }
    if not candidates:
        return model
    proposals: dict[
        int, tuple[StepFeature | BossFeature | RotationalFeature, ToleranceDecoration]
    ] = {}
    blocked: dict[int, str] = {}
    for index, dimension in candidates.items():
        if dimension.lowering_blockers or dimension.rendering_blockers:
            continue
        matches = [
            owner
            for owner in owners
            if _nominal_owner_matches(dimension, owner, model.bbox)
            and _same_number(dimension.value, _external_diameter(owner))
        ]
        if any(isinstance(owner, StepFeature) for owner in matches):
            matches = [owner for owner in matches if isinstance(owner, StepFeature)]
        elif any(isinstance(owner, RotationalFeature) for owner in matches):
            matches = [owner for owner in matches if isinstance(owner, RotationalFeature)]
        if len(matches) != 1:
            blocked[index] = (
                "unmatched external diameter ownership: no exact canonical cylinder"
                if not matches
                else f"ambiguous external diameter ownership: {len(matches)} canonical cylinders"
            )
            continue
        try:
            tolerance = _requirement(dimension)
        except ValueError as exc:
            blocked[index] = f"unsupported external diameter tolerance: {exc}"
            continue
        assert tolerance is not None
        proposals[index] = (
            matches[0],
            ToleranceDecoration(
                value=tolerance,
                source="ap242_pmi",
                source_ids=_source_ids(dimension),
                limit_bounds=_limit_bounds(dimension),
            ),
        )
    by_owner: dict[StepFeature | BossFeature | RotationalFeature, list[int]] = defaultdict(list)
    for index, (owner, _requirement_value) in proposals.items():
        by_owner[owner].append(index)
    decorations = dict(model.decorations)
    consumed: set[int] = set()
    for owner, indices in by_owner.items():
        parameter = _external_diameter_parameter(owner)
        key = (owner, "diameter", parameter.role)
        values = {proposals[index][1].value for index in indices}
        bounds = {proposals[index][1].limit_bounds for index in indices}
        if (
            len(values) != 1
            or len(bounds) != 1
            or key in decorations
            or (
                owner,
                "diameter",
            )
            in decorations
        ):
            for index in indices:
                blocked[index] = "ambiguous external diameter tolerance ownership"
            continue
        first = proposals[indices[0]][1]
        decorations[key] = replace(
            first,
            source_ids=tuple(
                dict.fromkeys(
                    source for index in indices for source in proposals[index][1].source_ids
                )
            ),
        )
        consumed.update(indices)
    return replace(
        model,
        features=[
            _block(feature, blocked[index])
            if index in blocked and isinstance(feature, AuthoredDimension)
            else feature
            for index, feature in enumerate(model.features)
            if index not in consumed
        ],
        decorations=decorations,
    )


def _external_diameter(feature: StepFeature | BossFeature | RotationalFeature) -> float:
    return float(feature.od if isinstance(feature, RotationalFeature) else feature.diameter)


def _external_diameter_parameter(feature: StepFeature | BossFeature | RotationalFeature):
    return next(
        parameter
        for parameter in feature.parameters()
        if parameter.kind == "diameter"
        and parameter.role == ("od" if isinstance(feature, RotationalFeature) else feature.kind)
    )


def lower_ap242_nominal_diameters(
    model: PartModel, *, feature_remap: FeatureRemap | None = None
) -> PartModel:
    """Give untoleranced Size_Diameter PMI to an existing canonical diameter owner.

    Correlation uses the referenced cylinder's topology axis, line, finite span, polarity,
    and radius. Nominal value is a consistency check, never the correspondence key. A source
    that cannot prove one owner remains an :class:`AuthoredDimension`; its typed cylinder is
    still sufficient for the shared standalone placement path (#1296).
    """
    model = _lower_pattern_nominal_diameters(model, feature_remap=feature_remap)
    targets: list[
        tuple[
            int,
            StepFeature | BossFeature | HoleFeature | PatternFeature | RotationalFeature,
        ]
    ] = [
        (index, feature)
        for index, feature in enumerate(model.features)
        if isinstance(
            feature,
            (StepFeature, BossFeature, HoleFeature, PatternFeature, RotationalFeature),
        )
    ]
    dimensions = {
        index: feature
        for index, feature in enumerate(model.features)
        if isinstance(feature, AuthoredDimension)
        and feature.dimension_kind == "diameter"
        and feature.source == "ap242_pmi"
        and feature.cylindrical_refs
        and not any(
            value is not None
            for value in (
                feature.lower_tol,
                feature.upper_tol,
                feature.lower_bound,
                feature.upper_bound,
            )
        )
    }
    if not dimensions:
        return _apply_standalone_cylinder_blockers(model)

    proposals: dict[
        int,
        tuple[
            StepFeature | BossFeature | HoleFeature | PatternFeature | RotationalFeature,
            tuple,
        ],
    ] = {}
    blocked: dict[int, str] = {}
    nominal_conflicts: set[int] = set()
    for dimension_index, dimension in dimensions.items():
        if dimension.lowering_blockers or dimension.rendering_blockers:
            continue
        matches = [
            feature
            for _feature_index, feature in targets
            if _nominal_owner_matches(dimension, feature, model.bbox)
        ]
        if any(isinstance(feature, StepFeature) for feature in matches):
            matches = [feature for feature in matches if isinstance(feature, StepFeature)]
        elif any(isinstance(feature, RotationalFeature) for feature in matches):
            matches = [feature for feature in matches if isinstance(feature, RotationalFeature)]
        if not matches:
            blocked[dimension_index] = (
                "unmatched diameter ownership: no canonical feature matches the source "
                "cylinder topology"
            )
            continue
        if len(matches) != 1:
            blocked[dimension_index] = (
                f"ambiguous diameter ownership: source cylinder topology matches "
                f"{len(matches)} canonical features"
            )
            continue
        owner = matches[0]
        key = _nominal_owner_key(owner)
        nominal = NominalRequirement(
            value=dimension.value, source="ap242_pmi", source_ids=_source_ids(dimension)
        )
        parameter = next(p for p in owner.parameters() if p.parameter_id == key[2])
        if not nominal.agrees_with(parameter.value):
            nominal_conflicts.add(dimension_index)
            blocked[dimension_index] = (
                f"source nominal {dimension.value!r} disagrees with canonical "
                f"{parameter.parameter_id}={parameter.value!r}"
            )
            continue
        proposals[dimension_index] = (owner, key)

    decorations = dict(model.decorations)
    consumed: set[int] = set()
    for dimension_index, (owner, key) in proposals.items():
        dimension = dimensions[dimension_index]
        incoming_ids = _source_ids(dimension)
        existing = decorations.get(key)
        if existing is None:
            decorations[key] = NominalRequirement(
                value=dimension.value, source="ap242_pmi", source_ids=incoming_ids
            )
            consumed.add(dimension_index)
            continue
        if isinstance(existing, NominalRequirement) and existing.agrees_with(dimension.value):
            decorations[key] = replace(
                existing,
                source_ids=tuple(dict.fromkeys((*existing.source_ids, *incoming_ids))),
            )
            consumed.add(dimension_index)
            continue
        blocked[dimension_index] = (
            f"ambiguous diameter ownership: {owner.kind} diameter already has an authored aspect"
        )

    rebuilt: list[Feature] = []
    for index, feature in enumerate(model.features):
        if index in consumed:
            continue
        if index in blocked and isinstance(feature, AuthoredDimension):
            feature = _block(feature, blocked[index])
            if index in nominal_conflicts:
                feature = replace(
                    feature, rendering_blockers=(*feature.rendering_blockers, blocked[index])
                )
        rebuilt.append(feature)
    return _apply_standalone_cylinder_blockers(
        replace(model, features=rebuilt, decorations=decorations)
    )


def lower_ap242_nominal_step_lengths(model: PartModel) -> PartModel:
    """Co-own an exact, untoleranced source length with its canonical step dimension.

    A matching value alone is insufficient: two shoulders may have the same length.
    Both authored witness points must coincide with the recognised step's endpoints,
    and there must be exactly one owner. Unmatched records stay standalone PMI.
    """
    steps = tuple(feature for feature in model.features if isinstance(feature, StepFeature))
    if not steps:
        return model
    decorations = dict(model.decorations)
    consumed: set[int] = set()
    for index, dimension in enumerate(model.features):
        if not (
            isinstance(dimension, AuthoredDimension)
            and dimension.source == "ap242_pmi"
            and dimension.dimension_kind == "linear"
            and dimension.source_id
            and not dimension.lowering_blockers
            and not dimension.rendering_blockers
            and dimension.lower_tol is None
            and dimension.upper_tol is None
            and dimension.lower_bound is None
            and dimension.upper_bound is None
            and re.fullmatch(r"\s*\d+(?:\.\d+)?\s*", dimension.label)
            and len(dimension.ref_pts) == 2
            and abs(float(dimension.label) - dimension.value) <= 1e-6
        ):
            continue
        matches = []
        for step in steps:
            if step.span is None or dimension.dominant_axis != step.frame.axis.upper():
                continue
            if abs(dimension.value - step.length) > 1e-6:
                continue
            if all(
                all(abs(a - b) <= 0.01 for a, b in zip(point, endpoint, strict=True))
                for point, endpoint in zip(dimension.ref_pts, step.span, strict=True)
            ) or all(
                all(abs(a - b) <= 0.01 for a, b in zip(point, endpoint, strict=True))
                for point, endpoint in zip(dimension.ref_pts, reversed(step.span), strict=True)
            ):
                matches.append(step)
        if len(matches) != 1:
            continue
        key = (matches[0], "nominal_requirement", "step.length")
        existing = decorations.get(key)
        if existing is None:
            decorations[key] = NominalRequirement(
                dimension.value, "ap242_pmi", _source_ids(dimension)
            )
        elif isinstance(existing, NominalRequirement) and existing.agrees_with(dimension.value):
            decorations[key] = replace(
                existing,
                source_ids=tuple(dict.fromkeys((*existing.source_ids, dimension.source_id))),
            )
        else:
            continue
        consumed.add(index)
    if not consumed:
        return model
    return replace(
        model,
        features=[
            feature for index, feature in enumerate(model.features) if index not in consumed
        ],
        decorations=decorations,
    )


_EXTERNAL_THREAD = re.compile(
    r"^(?P<designation>M(?P<nominal>\d+(?:\.\d+)?)\s*x\s*"
    r"(?P<pitch>\d+(?:\.\d+)?)-(?P<class>[A-Za-z0-9]+)\s+"
    r"(?P<hand>RH|LH)),\s*full available length on nominal DIA\s+"
    r"(?P<region>\d+(?:\.\d+)?)\s+region$",
    re.IGNORECASE,
)
_INTERNAL_THREAD = re.compile(
    r"^(?P<designation>M(?P<nominal>\d+(?:\.\d+)?)\s*x\s*"
    r"(?P<pitch>\d+(?:\.\d+)?)-(?P<class>[A-Za-z0-9]+)\s+"
    r"(?P<hand>RH|LH)),\s*(?:"
    r"(?P<full>\d+(?:\.\d+)?)\s*mm minimum full thread;\s*"
    r"DIA\s+(?P<drill>\d+(?:\.\d+)?)\s+tapping drill\s+x\s+"
    r"(?P<depth>\d+(?:\.\d+)?)\s*mm full-diameter depth"
    r"(?:;\s*conventional\s+(?P<angle>\d+(?:\.\d+)?)\s+degree drill point)?"
    r"|(?P<through>full thread through);\s*DIA\s+"
    r"(?P<through_drill>\d+(?:\.\d+)?)\s+tapping drill through)$",
    re.IGNORECASE,
)
_KNURL = re.compile(
    r"^(?P<pattern>Straight|Diamond) knurl,\s*(?P<pitch>\d+(?:\.\d+)?)\s*mm pitch,\s*"
    r"(?:full width between C(?P<chamfer>\d+(?:\.\d+)?) chamfers,\s*)?DIA\s+"
    r"(?P<diameter>\d+(?:\.\d+)?)\s*mm maximum after knurling;\s*"
    r"(?P<processes>cut or formed) process permitted$",
    re.IGNORECASE,
)

_CHAMFERS = re.compile(
    r"Two head-edge chamfers C(?P<head>\d+(?:\.\d+)?);\s*"
    r"M3 free-end chamfer C(?P<free>\d+(?:\.\d+)?)",
    re.IGNORECASE,
)


def _requirement_source_ids(feature: PmiFeature) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(((feature.source_id,) if feature.source_id else ()) + feature.source_ids)
    )


def _text_thread_requirement(feature: PmiFeature) -> ThreadRequirement:
    pattern = _EXTERNAL_THREAD if feature.pmi_kind == "external_thread" else _INTERNAL_THREAD
    match = pattern.fullmatch(feature.label.strip())
    if match is None:
        raise ValueError(f"unsupported {feature.pmi_kind.replace('_', ' ')} syntax")
    application: Literal["external", "internal"] = (
        "external" if feature.pmi_kind == "external_thread" else "internal"
    )
    values = match.groupdict()
    if application == "external" and not _same_number(
        float(values["nominal"]), float(values["region"])
    ):
        raise ValueError("external thread designation and nominal region disagree")
    return ThreadRequirement(
        application=application,
        designation=values["designation"],
        nominal_diameter=float(values["nominal"]),
        pitch=float(values["pitch"]),
        tolerance_class=values["class"],
        hand=cast(Literal["RH", "LH"], values["hand"].upper()),
        text=feature.label,
        source_ids=_requirement_source_ids(feature),
        part21_id=feature.part21_id,
        shape_aspect_ids=feature.shape_aspect_ids,
        reference_item_ids=feature.reference_item_ids,
        cylindrical_refs=feature.cylindrical_refs,
        full_available_length=application == "external",
        minimum_full_thread=(float(values["full"]) if values.get("full") else None),
        drill_diameter=(
            float(values["drill"] or values["through_drill"])
            if values.get("drill") or values.get("through_drill")
            else None
        ),
        drill_depth=(float(values["depth"]) if values.get("depth") else None),
        drill_point_angle=(float(values["angle"]) if values.get("angle") else None),
        through=bool(values.get("through")),
    )


def _text_knurl_requirement(feature: PmiFeature) -> KnurlRequirement:
    match = _KNURL.fullmatch(feature.label.strip())
    if match is None:
        raise ValueError("unsupported knurl requirement syntax")
    values = match.groupdict()
    return KnurlRequirement(
        pattern=cast(Literal["straight", "diamond"], values["pattern"].lower()),
        pitch=float(values["pitch"]),
        full_width=values.get("chamfer") is not None,
        edge_chamfer=(float(values["chamfer"]) if values.get("chamfer") else None),
        maximum_diameter=float(values["diameter"]),
        processes=("cut", "formed"),
        text=feature.label,
        source_ids=_requirement_source_ids(feature),
        part21_id=feature.part21_id,
        shape_aspect_ids=feature.shape_aspect_ids,
        reference_item_ids=feature.reference_item_ids,
        cylindrical_refs=feature.cylindrical_refs,
    )


def _structured_number(fields: dict[str, str | float], name: str) -> float:
    value = fields.get(name)
    if not isinstance(value, float) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"structured manufacturing {name} needs a positive length measure")
    return value


def _structured_number_alias(fields: dict[str, str | float], name: str, alias: str) -> float:
    if name in fields and alias in fields:
        raise ValueError(f"structured manufacturing has both {name} and {alias}")
    return _structured_number(fields, name if name in fields else alias)


def _structured_text(fields: dict[str, str | float], name: str) -> str:
    value = fields.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"structured manufacturing {name} needs descriptive text")
    return value.strip()


_STRUCTURED_MANUFACTURING_FIELDS = {
    "internal_thread": frozenset(
        {
            "thread side",
            "designation",
            "nominal size",
            "pitch",
            "fit class",
            "hand",
            "through",
            "tapping drill diameter",
            "tapping drill depth",
            "minimum full thread",
            "full thread length",
            "drill diameter",
            "drill depth",
        }
    ),
    "external_thread": frozenset(
        {
            "thread side",
            "designation",
            "nominal size",
            "pitch",
            "fit class",
            "hand",
            "thread length",
        }
    ),
    "knurl": frozenset(
        {"pattern", "diametral pitch", "major diameter", "pitch", "maximum diameter"}
    ),
    "general_tolerances": frozenset({"tolerance class"}),
}


def _structured_fields(feature: PmiFeature) -> dict[str, str | float]:
    names = [name for name, _value in feature.structured_fields]
    if len(names) != len(set(names)) or not all(names):
        raise ValueError("structured manufacturing fields have duplicate or empty names")
    unsupported = sorted(set(names) - _STRUCTURED_MANUFACTURING_FIELDS[feature.pmi_kind])
    if unsupported:
        raise ValueError(f"unsupported structured manufacturing fields: {', '.join(unsupported)}")
    return dict(feature.structured_fields)


def _structured_thread_extent(
    feature: PmiFeature,
    fields: dict[str, str | float],
    application: str,
    through: bool,
    prose: ThreadRequirement | None,
) -> tuple[float | None, float | None, float | None, bool]:
    """Preserve explicit drill and thread extent semantics from structured and paired prose."""
    drill: float | None = None
    depth: float | None = None
    full: float | None = None
    if application == "internal":
        drill = _structured_number_alias(fields, "tapping drill diameter", "drill diameter")
        depth = (
            None
            if through
            else _structured_number_alias(fields, "tapping drill depth", "drill depth")
        )
        if through and ("tapping drill depth" in fields or "drill depth" in fields):
            raise ValueError("structured through tap cannot declare a blind drill depth")
        if "full thread length" in fields:
            if prose is None or prose.minimum_full_thread is None:
                raise ValueError(
                    "structured full thread length needs a matching minimum-full-thread prose requirement"
                )
            full = _structured_number_alias(fields, "minimum full thread", "full thread length")
        else:
            full = (
                _structured_number(fields, "minimum full thread")
                if "minimum full thread" in fields
                else None
            )
        if through and full is not None:
            if len(feature.cylindrical_refs) != 1 or full > (
                feature.cylindrical_refs[0].axial_interval[1]
                - feature.cylindrical_refs[0].axial_interval[0]
                + 0.01
            ):
                raise ValueError("structured minimum full thread exceeds source cylinder")
    else:
        if through:
            raise ValueError("structured external thread cannot be a through tap")
        if "thread length" in fields:
            _structured_number(fields, "thread length")
            # A tip chamfer can shorten the source face. Check its full owner span
            # only after a unique geometric owner match is established.
        elif prose is None or not prose.full_available_length:
            raise ValueError("structured external thread has no length requirement")
    return (
        drill,
        depth,
        full,
        application == "external"
        and ("thread length" in fields or bool(prose and prose.full_available_length)),
    )


def _structured_thread_requirement(
    feature: PmiFeature, prose: ThreadRequirement | None
) -> ThreadRequirement:
    fields = _structured_fields(feature)
    application = _structured_text(fields, "thread side").casefold()
    if application not in ("internal", "external") or feature.pmi_kind != f"{application}_thread":
        raise ValueError("structured thread side disagrees with requirement kind")
    designation_text = _structured_text(fields, "designation")
    designation_match = re.fullmatch(
        r"M(?P<nominal>\d+(?:\.\d+)?)\s*x\s*(?P<pitch>\d+(?:\.\d+)?)",
        designation_text,
        re.IGNORECASE,
    )
    if designation_match is None:
        raise ValueError("structured thread designation or nominal size is unsupported")
    nominal = float(designation_match["nominal"])
    pitch = float(designation_match["pitch"])
    if "nominal size" in fields:
        nominal_match = re.fullmatch(
            r"M(\d+(?:\.\d+)?)", _structured_text(fields, "nominal size"), re.IGNORECASE
        )
        if nominal_match is None or not _same_number(nominal, float(nominal_match.group(1))):
            raise ValueError("structured thread designation disagrees with nominal size or pitch")
    if "pitch" in fields and not _same_number(pitch, _structured_number(fields, "pitch")):
        raise ValueError("structured thread designation disagrees with nominal size or pitch")
    tolerance_class = _structured_text(fields, "fit class")
    if re.fullmatch(r"[A-Za-z0-9]+", tolerance_class) is None:
        raise ValueError("structured thread fit class is unsupported")
    hand_value = _structured_text(fields, "hand").casefold()
    if hand_value not in ("right", "left", "rh", "lh"):
        raise ValueError("structured thread hand is unsupported")
    hand: Literal["RH", "LH"] = "RH" if hand_value in ("right", "rh") else "LH"
    through_value = fields.get("through")
    if through_value is None:
        through = False
    elif isinstance(through_value, str) and through_value.casefold() in ("true", "yes"):
        through = True
    elif isinstance(through_value, str) and through_value.casefold() in ("false", "no"):
        through = False
    else:
        raise ValueError("structured thread through flag is unsupported")
    designation = f"M{nominal:g} x {pitch:g}-{tolerance_class} {hand}"
    drill, depth, full, full_available_length = _structured_thread_extent(
        feature, fields, application, through, prose
    )
    return ThreadRequirement(
        application=cast(Literal["external", "internal"], application),
        designation=designation,
        nominal_diameter=nominal,
        pitch=pitch,
        tolerance_class=tolerance_class,
        hand=hand,
        text=feature.label,
        source_ids=_requirement_source_ids(feature),
        part21_id=feature.part21_id,
        shape_aspect_ids=feature.shape_aspect_ids,
        reference_item_ids=feature.reference_item_ids,
        cylindrical_refs=feature.cylindrical_refs,
        full_available_length=full_available_length,
        minimum_full_thread=full,
        drill_diameter=drill,
        drill_depth=depth,
        through=through,
    )


def _thread_requirement(feature: PmiFeature) -> ThreadRequirement:
    if not feature.structured_fields:
        return _text_thread_requirement(feature)
    try:
        prose = _text_thread_requirement(feature)
    except ValueError as exc:
        if len(feature.source_ids) > 1:
            raise ValueError("structured thread cannot reconcile with prose") from exc
        prose = None
    structured = _structured_thread_requirement(feature, prose)
    if prose is None:
        return structured
    shared = (
        "application",
        "nominal_diameter",
        "pitch",
        "tolerance_class",
        "hand",
        "minimum_full_thread",
        "drill_diameter",
        "drill_depth",
        "through",
        "full_available_length",
    )
    numeric = {"nominal_diameter", "pitch", "minimum_full_thread", "drill_diameter", "drill_depth"}
    if any(
        (
            not _same_number(left, right)
            if name in numeric and left is not None and right is not None
            else left != right
        )
        for name in shared
        for left, right in ((getattr(structured, name), getattr(prose, name)),)
    ):
        raise ValueError("structured thread values disagree with prose")
    return replace(structured, drill_point_angle=prose.drill_point_angle)


def _structured_knurl_requirement(
    feature: PmiFeature, prose: KnurlRequirement | None
) -> KnurlRequirement:
    fields = _structured_fields(feature)
    pattern = _structured_text(fields, "pattern").casefold()
    if pattern not in ("straight", "diamond"):
        raise ValueError("structured knurl pattern is unsupported")
    if prose is None and ("diametral pitch" in fields or "major diameter" in fields):
        # These source labels alone do not establish linear pitch or a maximum
        # after knurling; an explicit prose requirement must give them that meaning.
        raise ValueError("structured knurl aliases need a matching prose requirement")
    return KnurlRequirement(
        pattern=cast(Literal["straight", "diamond"], pattern),
        pitch=_structured_number_alias(fields, "diametral pitch", "pitch"),
        full_width=False,
        maximum_diameter=_structured_number_alias(fields, "major diameter", "maximum diameter"),
        text=feature.label,
        source_ids=_requirement_source_ids(feature),
        part21_id=feature.part21_id,
        shape_aspect_ids=feature.shape_aspect_ids,
        reference_item_ids=feature.reference_item_ids,
        cylindrical_refs=feature.cylindrical_refs,
    )


def _knurl_requirement(feature: PmiFeature) -> KnurlRequirement:
    if not feature.structured_fields:
        return _text_knurl_requirement(feature)
    try:
        prose = _text_knurl_requirement(feature)
    except ValueError as exc:
        if len(feature.source_ids) > 1:
            raise ValueError("structured knurl cannot reconcile with prose") from exc
        prose = None
    structured = _structured_knurl_requirement(feature, prose)
    if prose is None:
        return structured
    if (
        structured.pattern != prose.pattern
        or not _same_number(structured.pitch, prose.pitch)
        or structured.maximum_diameter is None
        or prose.maximum_diameter is None
        or not _same_number(structured.maximum_diameter, prose.maximum_diameter)
    ):
        raise ValueError("structured knurl values disagree with prose")
    return replace(
        structured,
        full_width=prose.full_width,
        edge_chamfer=prose.edge_chamfer,
        processes=prose.processes,
    )


def _block_requirement(feature: PmiFeature, reason: str) -> PmiFeature:
    return replace(
        feature,
        lowering_blockers=tuple(dict.fromkeys((*feature.lowering_blockers, reason))),
    )


def _manufacturing_owner_matches(requirement, feature, bbox) -> bool:
    references = requirement.cylindrical_refs
    if len(references) != 1:
        return False
    reference = references[0]
    if isinstance(requirement, ThreadRequirement) and requirement.application == "internal":
        if requirement.through:
            if isinstance(feature, PatternFeature):
                hole = feature.member
                members = tuple(feature.members) or (hole.frame.origin,)
                return (
                    len(members) == 1
                    and hole.through
                    and _internal_member_matches(reference, hole, members[0], bbox)
                )
            return (
                isinstance(feature, HoleFeature)
                and feature.through
                and _internal_member_matches(
                    reference,
                    feature,
                    (tuple(feature.members) or (feature.frame.origin,))[0],
                    bbox,
                )
            )
        if isinstance(feature, PatternFeature):
            hole = feature.member
            members = tuple(feature.members) or (hole.frame.origin,)
            return (
                len(members) == 1
                and not hole.through
                and hole.depth is not None
                and requirement.drill_depth is not None
                and _same_number(requirement.drill_depth, hole.depth, abs_tol=0.01)
                and _internal_member_matches(reference, hole, members[0], bbox)
            )
        return (
            isinstance(feature, HoleFeature)
            and not feature.through
            and feature.depth is not None
            and requirement.drill_depth is not None
            and _same_number(requirement.drill_depth, feature.depth, abs_tol=0.01)
            and _internal_member_matches(
                reference,
                feature,
                (tuple(feature.members) or (feature.frame.origin,))[0],
                bbox,
            )
        )
    return isinstance(feature, (StepFeature, BossFeature)) and _external_owner_matches(
        reference, feature
    )


def _remap_model_features(model: PartModel, replacements: dict[int, Feature]) -> PartModel:
    if not replacements:
        return model

    def remap(feature):
        return replacements.get(id(feature), feature)

    features = [remap(feature) for feature in model.features]
    decorations = {
        ((remap(key[0]), *key[1:]) if isinstance(key, tuple) and key else key): value
        for key, value in model.decorations.items()
    }
    requested = tuple(
        replace(item, feature=remap(item.feature)) for item in model.requested_dimensions
    )
    authored = (
        None
        if model.authored_dimensions is None
        else tuple(
            replace(item, feature=remap(item.feature)) for item in model.authored_dimensions
        )
    )
    return replace(
        model,
        features=features,
        decorations=decorations,
        requested_dimensions=requested,
        authored_dimensions=authored,
    )


def _turned_tip_chamfers_cover_gap(
    reference: CylindricalReference,
    owner: StepFeature | BossFeature,
    chamfers: tuple[ChamferFeature, ...],
) -> bool:
    """Require recognised equal-leg end chamfers for a shortened source cylinder."""
    span = owner.span
    if span is None:
        return False
    axis_index = "xyz".index(owner.frame.axis)
    owner_lo, owner_hi = sorted((span[0][axis_index], span[1][axis_index]))
    source_lo, source_hi = reference.axial_interval
    gaps = ((owner_lo, source_lo), (source_hi, owner_hi))
    found_gap = False
    for gap_lo, gap_hi in gaps:
        gap = gap_hi - gap_lo
        if gap <= 0.01:
            continue
        found_gap = True
        station = (gap_lo + gap_hi) / 2
        if not any(
            chamfer.turned
            and chamfer.axis == owner.frame.axis
            and _same_number(chamfer.leg1, gap, abs_tol=0.01)
            and _same_number(chamfer.leg2, gap, abs_tol=0.01)
            and _same_number(chamfer.angle, 45.0, abs_tol=0.01)
            and _same_number(chamfer.frame.origin[axis_index], station, abs_tol=0.01)
            and _same_number(
                math.sqrt(
                    sum(
                        (chamfer.frame.origin[index] - reference.axis_origin[index]) ** 2
                        for index in range(3)
                        if index != axis_index
                    )
                ),
                reference.radius - gap / 2,
                abs_tol=0.01,
            )
            for chamfer in chamfers
        ):
            return False
    return found_gap


def _manufacturing_proposal(feature: PmiFeature, owners, chamfers, bbox):
    """Validate one source against its exact cylinder and unique canonical owner."""
    requirement = (
        _knurl_requirement(feature)
        if feature.pmi_kind == "knurl"
        else _thread_requirement(feature)
    )
    reference = requirement.cylindrical_refs[0] if requirement.cylindrical_refs else None
    expected_diameter = (
        requirement.maximum_diameter
        if isinstance(requirement, KnurlRequirement)
        else requirement.drill_diameter
        if requirement.application == "internal"
        else requirement.nominal_diameter
    )
    if (
        reference is None
        or expected_diameter is None
        or not _same_number(reference.diameter, expected_diameter, abs_tol=0.01)
    ):
        raise ValueError("manufacturing requirement text disagrees with source cylinder")
    if (
        isinstance(requirement, ThreadRequirement)
        and requirement.application == "internal"
        and not requirement.through
        and (
            requirement.drill_depth is None
            or not _same_number(
                reference.axial_interval[1] - reference.axial_interval[0],
                requirement.drill_depth,
                abs_tol=0.01,
            )
        )
    ):
        raise ValueError("manufacturing requirement text disagrees with source cylinder")
    matches = [owner for owner in owners if _manufacturing_owner_matches(requirement, owner, bbox)]
    if len(matches) != 1:
        raise ValueError(
            "unmatched manufacturing requirement: no canonical feature matches source topology"
            if not matches
            else f"ambiguous manufacturing requirement: source topology matches {len(matches)} canonical features"
        )
    owner = matches[0]
    if (
        isinstance(requirement, ThreadRequirement)
        and requirement.application == "external"
        and isinstance(owner, (StepFeature, BossFeature))
        and "thread length" in dict(feature.structured_fields)
    ):
        length = _structured_number(dict(feature.structured_fields), "thread length")
        source_length = reference.axial_interval[1] - reference.axial_interval[0]
        span = owner.span
        if span is None:
            raise ValueError("external thread owner has no axial span")
        axis_index = "xyz".index(owner.frame.axis)
        owner_length = abs(span[1][axis_index] - span[0][axis_index])
        if not (
            _same_number(source_length, length, abs_tol=0.01)
            or (
                _same_number(owner_length, length, abs_tol=0.01)
                and _turned_tip_chamfers_cover_gap(reference, owner, chamfers)
            )
        ):
            raise ValueError(
                "structured thread length disagrees with source cylinder or evidenced owner span"
            )
    return owner, requirement


def lower_ap242_manufacturing_requirements(
    model: PartModel, *, feature_remap: FeatureRemap | None = None
) -> PartModel:
    """Lower supported semantic requirements through exact source topology."""
    raw = {
        index: feature
        for index, feature in enumerate(model.features)
        if isinstance(feature, PmiFeature)
        and feature.source_category == "manufacturing_requirement"
        and feature.pmi_kind in {"external_thread", "internal_thread", "knurl"}
    }
    if not raw:
        return model

    owners = [
        feature
        for feature in model.features
        if isinstance(feature, (StepFeature, BossFeature, HoleFeature, PatternFeature))
    ]
    chamfers = tuple(feature for feature in model.features if isinstance(feature, ChamferFeature))
    replacements: dict[int, Feature] = {}
    consumed: set[int] = set()
    blocked: dict[int, str] = {}
    proposals: dict[
        tuple[int, str],
        list[
            tuple[
                int,
                StepFeature | BossFeature | HoleFeature | PatternFeature,
                ThreadRequirement | KnurlRequirement,
            ]
        ],
    ] = {}
    for index, feature in raw.items():
        if feature.lowering_blockers:
            continue
        try:
            owner, requirement = _manufacturing_proposal(feature, owners, chamfers, model.bbox)
        except ValueError as exc:
            blocked[index] = str(exc)
            continue
        aspect = "knurl" if isinstance(requirement, KnurlRequirement) else "thread"
        proposals.setdefault((id(owner), aspect), []).append((index, owner, requirement))

    for (_owner_id, aspect), candidates in proposals.items():
        if len(candidates) != 1:
            for index, _owner, _requirement in candidates:
                blocked[index] = (
                    f"ambiguous {aspect} ownership: multiple manufacturing requirements claim canonical feature"
                )
            continue
        index, owner, requirement = candidates[0]
        current = replacements.get(id(owner), owner)
        updated: Feature
        if isinstance(requirement, KnurlRequirement):
            assert isinstance(current, (StepFeature, BossFeature))
            if getattr(current, "knurl", None) is not None:
                blocked[index] = (
                    "ambiguous knurl ownership: canonical feature already has a knurl aspect"
                )
                continue
            updated = replace(current, knurl=requirement)
        elif isinstance(current, PatternFeature):
            if current.member.thread is not None:
                blocked[index] = (
                    "ambiguous thread ownership: canonical hole already has a thread aspect"
                )
                continue
            updated = replace(current, member=replace(current.member, thread=requirement))
        else:
            assert isinstance(current, (StepFeature, BossFeature, HoleFeature))
            if getattr(current, "thread", None) is not None:
                blocked[index] = (
                    "ambiguous thread ownership: canonical feature already has a thread aspect"
                )
                continue
            updated = replace(current, thread=requirement)
        replacements[id(owner)] = updated
        consumed.add(index)

    lowered = _remap_model_features(model, replacements)
    if feature_remap is not None:
        for owner in owners:
            if (replacement := replacements.get(id(owner))) is not None:
                feature_remap(owner, (replacement,), None)
    rebuilt: list[Feature] = []
    for index, lowered_feature in enumerate(lowered.features):
        if index in consumed:
            continue
        if index in blocked and isinstance(lowered_feature, PmiFeature):
            lowered_feature = _block_requirement(lowered_feature, blocked[index])
        rebuilt.append(lowered_feature)
    return replace(lowered, features=rebuilt)


def _turned_chamfer_matches_box(feature: ChamferFeature, box) -> bool:
    if not feature.turned or feature.axis not in "xyz":
        return False
    axis_index = "xyz".index(feature.axis)
    lo, hi = box[axis_index], box[axis_index + 3]
    if not _same_number(feature.frame.origin[axis_index], (lo + hi) / 2, abs_tol=0.002):
        return False
    if not _same_number(feature.leg1, hi - lo, abs_tol=0.002):
        return False
    transverse = [index for index in range(3) if index != axis_index]
    centers = [(box[index] + box[index + 3]) / 2 for index in transverse]
    outer_radius = max((box[index + 3] - box[index]) / 2 for index in transverse)
    feature_radius = (
        sum(
            (feature.frame.origin[index] - center) ** 2
            for index, center in zip(transverse, centers, strict=True)
        )
        ** 0.5
    )
    return _same_number(
        feature_radius,
        outer_radius - feature.leg2 / 2,
        abs_tol=0.002,
    )


def lower_ap242_chamfer_requirements(
    model: PartModel, *, feature_remap: FeatureRemap | None = None
) -> PartModel:
    """Correlate the supported grouped turned-chamfer requirement from exact face bounds."""
    candidates = [
        (index, feature)
        for index, feature in enumerate(model.features)
        if isinstance(feature, PmiFeature)
        and feature.source_category == "manufacturing_requirement"
        and feature.pmi_kind == "chamfers"
        and not feature.lowering_blockers
    ]
    if not candidates:
        return model
    features = list(model.features)
    chamfers = [feature for feature in features if isinstance(feature, ChamferFeature)]
    replacements: dict[int, Feature] = {}
    consumed = set()
    for index, requirement in candidates:
        match = _CHAMFERS.fullmatch(requirement.label.strip())
        if match is None:
            features[index] = _block_requirement(
                requirement, "unsupported grouped chamfer requirement syntax"
            )
            continue
        if len(requirement.reference_bboxes) != 2:
            features[index] = _block_requirement(
                requirement, "grouped chamfer requirement needs two exact conical references"
            )
            continue
        direct_groups = [
            [chamfer for chamfer in chamfers if _turned_chamfer_matches_box(chamfer, box)]
            for box in requirement.reference_bboxes
        ]
        if any(len(group) != 1 for group in direct_groups):
            features[index] = _block_requirement(
                requirement, "chamfer reference does not match one canonical turned chamfer"
            )
            continue
        direct = [group[0] for group in direct_groups]
        head = float(match.group("head"))
        free = float(match.group("free"))
        head_direct = [item for item in direct if _same_number(item.leg1, head, abs_tol=0.002)]
        free_direct = [item for item in direct if _same_number(item.leg1, free, abs_tol=0.002)]
        if len(head_direct) != 1 or len(free_direct) != 1:
            features[index] = _block_requirement(
                requirement, "chamfer text disagrees with exact conical references"
            )
            continue
        head_feature = head_direct[0]
        knurled = [
            owner
            for owner in features
            if isinstance(owner, (StepFeature, BossFeature))
            and owner.knurl is not None
            and _same_number(owner.knurl.edge_chamfer or 0.0, head, abs_tol=0.002)
            and len(owner.knurl.cylindrical_refs) == 1
        ]
        if len(knurled) != 1:
            features[index] = _block_requirement(
                requirement, "head chamfer pair has no unique source-proven knurled owner"
            )
            continue
        knurl = knurled[0].knurl
        assert knurl is not None
        cylinder = knurl.cylindrical_refs[0]
        adjacent = [
            chamfer
            for chamfer in chamfers
            if chamfer.axis == cylinder.principal_axis.lower()
            and chamfer.turned
            and _same_number(chamfer.leg1, head, abs_tol=0.002)
            and any(
                _same_number(
                    chamfer.frame.origin["xyz".index(chamfer.axis)] + sign * chamfer.leg1 / 2,
                    station,
                    abs_tol=0.002,
                )
                for sign in (-1, 1)
                for station in cylinder.axial_interval
            )
        ]
        if len(adjacent) != 2 or head_feature not in adjacent:
            features[index] = _block_requirement(
                requirement, "source topology does not prove the two head-edge chamfers"
            )
            continue
        matched = [*adjacent, free_direct[0]]
        if len({id(item) for item in matched}) != 3:
            features[index] = _block_requirement(
                requirement, "grouped chamfer members are not three distinct features"
            )
            continue
        source_ids = _requirement_source_ids(requirement)
        pending = {}
        for chamfer in matched:
            if chamfer.source_ids or id(chamfer) in replacements:
                features[index] = _block_requirement(
                    requirement, "canonical chamfer is already claimed by imported provenance"
                )
                break
            replacement = replace(
                chamfer,
                source_ids=source_ids,
                part21_id=requirement.part21_id,
                shape_aspect_ids=requirement.shape_aspect_ids,
                reference_item_ids=requirement.reference_item_ids,
            )
            pending[id(chamfer)] = replacement
        else:
            replacements.update(pending)
            consumed.add(index)

    lowered = _remap_model_features(replace(model, features=features), replacements)
    if feature_remap is not None:
        for source in chamfers:
            if id(source) in replacements:
                feature_remap(source, (replacements[id(source)],), None)
    return replace(
        lowered,
        features=[
            feature
            for position, feature in enumerate(lowered.features)
            if position not in consumed
        ],
    )


_DATUM_AXIS_STATEMENT = re.compile(
    r"datum (?P<letter>[A-Z]) is the axis derived from DIA (?P<diameter>\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_DATUM_SHOULDER_STATEMENT = re.compile(
    r"datum (?P<letter>[A-Z]) is the DIA (?P<first>\d+(?:\.\d+)?)-to-DIA "
    r"(?P<second>\d+(?:\.\d+)?) shoulder face",
    re.IGNORECASE,
)


def _datum_scheme_represented_by_symbols(model: PartModel, text: str) -> tuple[str, ...]:
    """Prove a narrow imported prose scheme is wholly conveyed by typed datum symbols.

    An unrecognised clause, missing source-owned symbol, or geometry mismatch retains
    the source text on the sheet.  This is deliberately not a natural-language guess.
    """
    clauses = [clause.strip() for clause in text.split(";")]
    if not clauses or any(not clause for clause in clauses):
        return ()
    datums = [feature for feature in model.features if isinstance(feature, DatumRef)]
    steps = [feature for feature in model.features if isinstance(feature, StepFeature)]
    represented: list[str] = []
    for clause in clauses:
        axis_match = _DATUM_AXIS_STATEMENT.fullmatch(clause)
        shoulder_match = _DATUM_SHOULDER_STATEMENT.fullmatch(clause)
        match = axis_match or shoulder_match
        if match is None:
            return ()
        candidates = [
            datum
            for datum in datums
            if datum.letter.upper() == match["letter"].upper()
            and datum.source_ids
            and isinstance(datum.origin, PmiFeature)
            and datum.origin.source_category == "datum"
            and datum.origin.reference_item_ids
            and datum.origin.ref_bbox is not None
        ]
        if len(candidates) != 1:
            return ()
        datum = candidates[0]
        origin = datum.origin
        if not isinstance(origin, PmiFeature) or origin.ref_bbox is None:
            return ()
        axis = origin.reference_axis.lower()
        if axis not in "xyz" or len(axis) != 1 or datum.frame.axis != axis:
            return ()
        axial = "xyz".index(axis)
        transverse = [index for index in range(3) if index != axial]
        bbox = origin.ref_bbox
        extents = [bbox[index + 3] - bbox[index] for index in range(3)]
        tol = 0.02  # STEP face bboxes carry ~1e-7 mm numeric padding.
        if axis_match is not None:
            diameter = float(axis_match["diameter"])
            if not (
                extents[axial] > tol
                and all(abs(extents[index] - diameter) <= tol for index in transverse)
                and any(
                    step.frame.axis == axis
                    and abs(step.diameter - diameter) <= tol
                    and min(point[axial] for point in step.span) >= bbox[axial] - tol
                    and max(point[axial] for point in step.span) <= bbox[axial + 3] + tol
                    for step in steps
                )
            ):
                return ()
        else:
            if shoulder_match is None:
                return ()
            first, second = float(shoulder_match["first"]), float(shoulder_match["second"])
            plane = (bbox[axial] + bbox[axial + 3]) / 2
            if extents[axial] > tol or not all(
                min(first, second) - tol <= extents[index] <= max(first, second) + tol
                for index in transverse
            ):
                return ()
            # The datum face must sit at a real adjacent turned-step transition.
            # A small gap is allowed for the intervening head-edge chamfer.
            ordered = sorted(
                (step for step in steps if step.frame.axis == axis),
                key=lambda step: min(point[axial] for point in step.span),
            )
            chamfers = [
                feature
                for feature in model.features
                if isinstance(feature, ChamferFeature) and feature.turned and feature.axis == axis
            ]

            def _verified_transition(
                left: StepFeature,
                right: StepFeature,
                axial=axial,
                tol=tol,
                plane=plane,
                chamfers=chamfers,
            ) -> bool:
                left_end = max(point[axial] for point in left.span)
                right_start = min(point[axial] for point in right.span)
                gap = right_start - left_end
                return (
                    gap >= -tol
                    and min(abs(left_end - plane), abs(right_start - plane)) <= tol
                    and (
                        gap <= tol
                        or any(
                            abs(chamfer.frame.origin[axial] - (left_end + right_start) / 2) <= tol
                            and abs(chamfer.leg1 - gap) <= tol
                            for chamfer in chamfers
                        )
                    )
                )

            if not any(
                (
                    abs(left.diameter - first) <= tol
                    and abs(right.diameter - second) <= tol
                    or abs(left.diameter - second) <= tol
                    and abs(right.diameter - first) <= tol
                )
                and (left.profile == right.profile)
                and _verified_transition(left, right)
                for left, right in zip(ordered, ordered[1:], strict=False)
            ):
                return ()
        represented.extend(datum.source_ids)
    return tuple(dict.fromkeys(represented))


def _general_tolerance_designation(feature: PmiFeature) -> str:
    designation = feature.label.split(";", 1)[0].strip()
    if feature.structured_fields:
        structured = _structured_fields(feature).get("tolerance class")
        if not isinstance(structured, str) or not structured.strip():
            raise ValueError("structured general-tolerance class is missing")
        if feature.label != "general tolerances" and designation != structured:
            raise ValueError("structured general-tolerance class disagrees with prose")
        designation = structured
    if not designation:
        raise ValueError("general-tolerance designation is empty")
    return designation


def lower_ap242_document_requirements(model: PartModel) -> PartModel:
    """Lower source-proven document defaults that have an existing drafting carrier."""
    tolerance_candidates = [
        (index, feature)
        for index, feature in enumerate(model.features)
        if isinstance(feature, PmiFeature)
        and feature.source_category == "manufacturing_requirement"
        and feature.pmi_kind == "general_tolerances"
        and not feature.lowering_blockers
    ]
    if len(tolerance_candidates) > 1:
        features = list(model.features)
        for index, feature in tolerance_candidates:
            features[index] = _block_requirement(
                feature, "ambiguous document default: multiple general-tolerance requirements"
            )
        model = replace(model, features=features)
    elif tolerance_candidates:
        index, feature = tolerance_candidates[0]
        try:
            designation = _general_tolerance_designation(feature)
        except ValueError as exc:
            features = list(model.features)
            features[index] = _block_requirement(feature, str(exc))
            model = replace(model, features=features)
        else:
            tolerance_requirement = GeneralTolerance(
                frame=feature.frame,
                designation=designation,
                statement=feature.label,
                source_id=feature.source_id,
                part21_id=feature.part21_id,
                source_ids=feature.source_ids,
            )
            model = replace(
                model,
                features=[
                    tolerance_requirement if position == index else item
                    for position, item in enumerate(model.features)
                ],
            )

    finish_candidates = [
        (index, feature)
        for index, feature in enumerate(model.features)
        if isinstance(feature, PmiFeature)
        and feature.source_category == "manufacturing_requirement"
        and feature.pmi_kind == "surface_texture"
        and not feature.lowering_blockers
    ]
    if len(finish_candidates) > 1:
        features = list(model.features)
        for index, feature in finish_candidates:
            features[index] = _block_requirement(
                feature, "ambiguous document default: multiple surface-texture requirements"
            )
        model = replace(model, features=features)
    elif finish_candidates:
        index, feature = finish_candidates[0]
        match = re.fullmatch(
            r"\s*Ra\s+(?P<ra>\d+(?:\.\d+)?)\s*(?:um|µm|μm)\s+unless\s+otherwise\s+specified\s*",
            feature.label,
            flags=re.IGNORECASE,
        )
        if match is None:
            features = list(model.features)
            features[index] = _block_requirement(
                feature, "surface-texture requirement is not a supported document default"
            )
            model = replace(model, features=features)
        else:
            finish_requirement = DefaultSurfaceFinish(
                frame=feature.frame,
                ra=match.group("ra"),
                statement=feature.label,
                source_id=feature.source_id,
                part21_id=feature.part21_id,
            )
            model = replace(
                model,
                features=[
                    finish_requirement if position == index else item
                    for position, item in enumerate(model.features)
                ],
            )

    document_kinds = {"datum_scheme", "model_representation"}
    features = list(model.features)
    for index, item in enumerate(features):
        if not (
            isinstance(item, PmiFeature)
            and item.source_category == "manufacturing_requirement"
            and item.pmi_kind in document_kinds
            and not item.lowering_blockers
        ):
            continue
        if not item.label.strip():
            features[index] = _block_requirement(item, "document requirement text is empty")
            continue
        represented_by = (
            _datum_scheme_represented_by_symbols(model, item.label)
            if item.pmi_kind == "datum_scheme"
            else ()
        )
        features[index] = DocumentNote(
            frame=item.frame,
            text=item.label,
            note_kind=item.pmi_kind,
            source_id=item.source_id,
            part21_id=item.part21_id,
            on_drawing=item.pmi_kind != "model_representation" and not represented_by,
            represented_by_source_ids=represented_by,
        )
    return replace(model, features=features)


def lower_ap242_dimensions(
    model: PartModel, *, feature_remap: FeatureRemap | None = None
) -> PartModel:
    """Run every geometry-correlated AP242 lowering at the IR waist."""
    dimensions = lower_ap242_nominal_step_lengths(
        lower_ap242_nominal_diameters(
            lower_ap242_external_diameter_tolerances(
                lower_ap242_hole_tolerances(model, feature_remap=feature_remap)
            ),
            feature_remap=feature_remap,
        )
    )
    manufacturing = lower_ap242_manufacturing_requirements(dimensions, feature_remap=feature_remap)
    chamfers = lower_ap242_chamfer_requirements(manufacturing, feature_remap=feature_remap)
    return lower_ap242_document_requirements(chamfers)
