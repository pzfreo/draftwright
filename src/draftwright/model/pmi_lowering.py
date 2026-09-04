"""Geometry-correlated AP242 dimensional PMI lowering (#1116).

The extractor reports source semantics and recognition reports geometry.  This module is the
single correlation seam between them: a supported diameter tolerance enriches the canonical
hole/pattern dimension, while an unproven match remains a materialised authored dimension with
an explicit reason.  It deliberately knows nothing about annotation coordinates or rendering.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal
from typing import Literal, cast

from draftwright.model.ir import (
    AuthoredDimension,
    BossFeature,
    CylindricalReference,
    Feature,
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


def lower_ap242_hole_tolerances(
    model: PartModel, *, feature_remap: FeatureRemap | None = None
) -> PartModel:
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
    # the same parameter have two sources and violate ADR 4 (was 0011)'s single-owner decoration map.
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
        replacements: list[Feature] = []
        replacement_member_groups: list[tuple[int, ...]] = []
        for value, group_member_indices in groups.items():
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
                        for dim_index in group_sources[value]
                        for source_id in _source_ids(dimensions[dim_index])
                    )
                )
                decorations[(split, "diameter")] = ToleranceDecoration(
                    value=value, source="ap242_pmi", source_ids=ids
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


def lower_ap242_nominal_diameters(model: PartModel) -> PartModel:
    """Give untoleranced Size_Diameter PMI to an existing canonical diameter owner.

    Correlation uses the referenced cylinder's topology axis, line, finite span, polarity,
    and radius. Nominal value is a consistency check, never the correspondence key. A source
    that cannot prove one owner remains an :class:`AuthoredDimension`; its typed cylinder is
    still sufficient for the shared standalone placement path (#1296).
    """
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
        proposals[dimension_index] = (owner, _nominal_owner_key(owner))

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
        if isinstance(existing, NominalRequirement) and _same_number(
            existing.value, dimension.value
        ):
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
        rebuilt.append(feature)
    return _apply_standalone_cylinder_blockers(
        replace(model, features=rebuilt, decorations=decorations)
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
    r"(?P<hand>RH|LH)),\s*(?P<full>\d+(?:\.\d+)?)\s*mm minimum full thread;\s*"
    r"DIA\s+(?P<drill>\d+(?:\.\d+)?)\s+tapping drill\s+x\s+"
    r"(?P<depth>\d+(?:\.\d+)?)\s*mm full-diameter depth;\s*conventional\s+"
    r"(?P<angle>\d+(?:\.\d+)?)\s+degree drill point$",
    re.IGNORECASE,
)
_KNURL = re.compile(
    r"^(?P<pattern>Straight|Diamond) knurl,\s*(?P<pitch>\d+(?:\.\d+)?)\s*mm pitch,\s*"
    r"full width between C(?P<chamfer>\d+(?:\.\d+)?) chamfers,\s*DIA\s+"
    r"(?P<diameter>\d+(?:\.\d+)?)\s*mm maximum after knurling;\s*"
    r"(?P<processes>cut or formed) process permitted$",
    re.IGNORECASE,
)


def _requirement_source_ids(feature: PmiFeature) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(((feature.source_id,) if feature.source_id else ()) + feature.source_ids)
    )


def _thread_requirement(feature: PmiFeature) -> ThreadRequirement:
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
        drill_diameter=(float(values["drill"]) if values.get("drill") else None),
        drill_depth=(float(values["depth"]) if values.get("depth") else None),
        drill_point_angle=(float(values["angle"]) if values.get("angle") else None),
    )


def _knurl_requirement(feature: PmiFeature) -> KnurlRequirement:
    match = _KNURL.fullmatch(feature.label.strip())
    if match is None:
        raise ValueError("unsupported knurl requirement syntax")
    values = match.groupdict()
    return KnurlRequirement(
        pattern=cast(Literal["straight", "diamond"], values["pattern"].lower()),
        pitch=float(values["pitch"]),
        full_width=True,
        edge_chamfer=float(values["chamfer"]),
        maximum_diameter=float(values["diameter"]),
        processes=("cut", "formed"),
        text=feature.label,
        source_ids=_requirement_source_ids(feature),
        part21_id=feature.part21_id,
        shape_aspect_ids=feature.shape_aspect_ids,
        reference_item_ids=feature.reference_item_ids,
        cylindrical_refs=feature.cylindrical_refs,
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
    replacements: dict[int, Feature] = {}
    replacement_pairs: list[tuple[Feature, Feature]] = []
    consumed: set[int] = set()
    blocked: dict[int, str] = {}
    claimed: set[int] = set()
    for index, feature in raw.items():
        if feature.lowering_blockers:
            continue
        try:
            requirement = (
                _knurl_requirement(feature)
                if feature.pmi_kind == "knurl"
                else _thread_requirement(feature)
            )
        except ValueError as exc:
            blocked[index] = str(exc)
            continue
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
            blocked[index] = "manufacturing requirement text disagrees with source cylinder"
            continue
        if (
            isinstance(requirement, ThreadRequirement)
            and requirement.application == "internal"
            and (
                requirement.drill_depth is None
                or not _same_number(
                    reference.axial_interval[1] - reference.axial_interval[0],
                    requirement.drill_depth,
                    abs_tol=0.01,
                )
            )
        ):
            blocked[index] = "manufacturing requirement text disagrees with source cylinder"
            continue
        matches = [
            owner
            for owner in owners
            if _manufacturing_owner_matches(requirement, owner, model.bbox)
        ]
        if len(matches) != 1:
            blocked[index] = (
                "unmatched manufacturing requirement: no canonical feature matches source topology"
                if not matches
                else f"ambiguous manufacturing requirement: source topology matches {len(matches)} canonical features"
            )
            continue
        owner = matches[0]
        if id(owner) in claimed:
            blocked[index] = (
                "ambiguous manufacturing requirement: canonical feature is already claimed"
            )
            continue
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
        replacement_pairs.append((owner, updated))
        claimed.add(id(owner))
        consumed.add(index)

    lowered = _remap_model_features(model, replacements)
    if feature_remap is not None:
        for source, replacement in replacement_pairs:
            feature_remap(source, (replacement,), None)
    rebuilt: list[Feature] = []
    for index, lowered_feature in enumerate(lowered.features):
        if index in consumed:
            continue
        if index in blocked and isinstance(lowered_feature, PmiFeature):
            lowered_feature = _block_requirement(lowered_feature, blocked[index])
        rebuilt.append(lowered_feature)
    return replace(lowered, features=rebuilt)


def lower_ap242_dimensions(
    model: PartModel, *, feature_remap: FeatureRemap | None = None
) -> PartModel:
    """Run every geometry-correlated AP242 lowering at the IR waist."""
    dimensions = lower_ap242_nominal_diameters(
        lower_ap242_hole_tolerances(model, feature_remap=feature_remap)
    )
    return lower_ap242_manufacturing_requirements(dimensions, feature_remap=feature_remap)
