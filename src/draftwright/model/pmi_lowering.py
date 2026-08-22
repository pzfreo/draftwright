"""Geometry-correlated AP242 dimensional PMI lowering (#1116).

The extractor reports source semantics and recognition reports geometry.  This module is the
single correlation seam between them: a supported diameter tolerance enriches the canonical
hole/pattern dimension, while an unproven match remains a materialised authored dimension with
an explicit reason.  It deliberately knows nothing about annotation coordinates or rendering.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from decimal import Decimal

from draftwright.model.ir import (
    AuthoredDimension,
    Feature,
    HoleFeature,
    PartModel,
    PatternFeature,
    ToleranceDecoration,
)

ToleranceValue = float | tuple[float, float]


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


def lower_ap242_hole_tolerances(model: PartModel) -> PartModel:
    """Consume confidently correlated AP242 hole-tolerance dimensions exactly once.

    A count-group is split only where member requirements differ.  A real pattern stays a
    pattern and therefore accepts only a requirement whose referenced geometry covers every
    member.  Those rules preserve machining-spec identity instead of applying one member's
    tolerance to its untoleranced siblings or destroying pattern membership to make a match.
    """
    targets: list[tuple[int, HoleFeature | PatternFeature]] = [
        (index, feature)
        for index, feature in enumerate(model.features)
        if isinstance(feature, (HoleFeature, PatternFeature))
    ]
    target_by_index = dict(targets)
    dimensions = {
        index: feature
        for index, feature in enumerate(model.features)
        if isinstance(feature, AuthoredDimension)
        and feature.dimension_kind == "diameter"
        and feature.source == "ap242_pmi"
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
    if not dimensions:
        return model

    # dim index -> (owner index, selected member indices, tolerance value)
    proposals: dict[int, tuple[int, tuple[int, ...], ToleranceValue]] = {}
    blocked: dict[int, str] = {}
    for dim_index, dim in dimensions.items():
        if dim.lowering_blockers:
            blocked[dim_index] = ""
            continue
        if dim.ref_bbox is None:
            blocked[dim_index] = (
                "unmatched hole correlation: source diameter has no referenced geometry"
            )
            continue
        if dim.dominant_axis not in ("X", "Y", "Z"):
            blocked[dim_index] = (
                "unsupported hole correlation: source diameter has no principal bore axis"
            )
            continue
        matches: list[tuple[int, tuple[int, ...]]] = []
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
                matches.append((owner_index, member_indices))
        if not matches:
            blocked[dim_index] = (
                f"unmatched hole correlation: no {_diameter_text(dim.value)} "
                f"{dim.dominant_axis}-axis hole member lies in the source reference bounds"
            )
            continue
        if len(matches) != 1:
            blocked[dim_index] = (
                f"ambiguous hole correlation: source reference bounds match {len(matches)} "
                "canonical hole/pattern features"
            )
            continue
        owner_index, member_indices = matches[0]
        owner = target_by_index[owner_index]
        if isinstance(owner, PatternFeature) and len(member_indices) != len(_members(owner)):
            blocked[dim_index] = (
                "unsupported hole correlation: AP242 requirement covers only part of a "
                "canonical hole pattern"
            )
            continue
        try:
            value = _requirement(dim)
        except ValueError as exc:
            blocked[dim_index] = f"unsupported hole tolerance: {exc}"
            continue
        assert value is not None
        proposals[dim_index] = (owner_index, member_indices, value)

    # Existing authored ownership wins.  Silently replacing it with imported PMI would make
    # the same parameter have two sources and violate ADR 0011's single-owner decoration map.
    for dim_index, (owner_index, _member_indices, _value) in tuple(proposals.items()):
        owner = target_by_index[owner_index]
        if (owner, "diameter", "bore") in model.decorations or (
            owner,
            "diameter",
        ) in model.decorations:
            blocked[dim_index] = "ambiguous hole tolerance ownership: bore already has a tolerance"
            del proposals[dim_index]

    # A member cannot carry two different imported requirements.  Equal repeats are one
    # requirement with multiple source identities; conflicting ones remain explicit.
    by_member: dict[tuple[int, int], list[int]] = defaultdict(list)
    for dim_index, (owner_index, member_indices, _value) in proposals.items():
        for member_index in member_indices:
            by_member[(owner_index, member_index)].append(dim_index)
    for dim_indices in by_member.values():
        active = [index for index in dim_indices if index in proposals]
        values = {proposals[index][2] for index in active}
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
            dim_indices = sorted({i for indices in member_requirements.values() for i in indices})
            value = proposals[dim_indices[0]][2]
            ids = tuple(
                dict.fromkeys(
                    source_id
                    for dim_index in dim_indices
                    for source_id in _source_ids(dimensions[dim_index])
                )
            )
            rebuilt.append(feature)
            decorations[(feature, "diameter", "bore")] = ToleranceDecoration(
                value=value, source="ap242_pmi", source_ids=ids
            )
            continue

        assert isinstance(feature, HoleFeature)
        points = _members(feature)
        inherited = [
            (key[1:], value)
            for key, value in tuple(decorations.items())
            if isinstance(key, tuple) and key and key[0] == feature
        ]
        for key in [
            key
            for key in tuple(decorations)
            if isinstance(key, tuple) and key and key[0] == feature
        ]:
            del decorations[key]
        # Group members by effective tolerance, retaining first-member order.  ``None`` is
        # the untoleranced remainder; equal member requirements keep their count× callout.
        groups: dict[ToleranceValue | None, list[int]] = {}
        group_sources: dict[ToleranceValue | None, list[int]] = {}
        for member_index in range(len(points)):
            dim_indices = member_requirements.get(member_index, [])
            value = proposals[dim_indices[0]][2] if dim_indices else None
            groups.setdefault(value, []).append(member_index)
            group_sources.setdefault(value, []).extend(dim_indices)
        for value, group_member_indices in groups.items():
            members = tuple(points[index] for index in group_member_indices)
            split = replace(
                feature,
                frame=replace(feature.frame, origin=members[0]),
                count=len(members),
                members=members,
            )
            rebuilt.append(split)
            for tail, inherited_value in inherited:
                decorations[(split, *tail)] = inherited_value
            if value is not None:
                ids = tuple(
                    dict.fromkeys(
                        source_id
                        for dim_index in group_sources[value]
                        for source_id in _source_ids(dimensions[dim_index])
                    )
                )
                decorations[(split, "diameter")] = ToleranceDecoration(
                    value=value, source="ap242_pmi", source_ids=ids
                )

    return replace(model, features=rebuilt, decorations=decorations)


def _diameter_text(value: float) -> str:
    return f"diameter {value:g}"
