"""Semantic completeness for recognised multi-plate slab requirements (#1373).

Each aggregate ``Plate`` is one body-local slab whose thickness is not already owned by the
whole-part envelope.  Axis, axial interval and the provider's physical witness locate exactly
one ``PlateFeature``; the compiler's ``thickness.length`` identity then joins that requirement
to a placed, explicitly satisfied, suppressed, dropped, missing, or unverifiable outcome.
Annotation names, views, labels, projections and page coordinates are never correspondence
evidence.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from math import isfinite
from typing import Literal

from b123d_recognisers import RecognitionResult

from draftwright.linting._registry import satisfaction_ids, satisfaction_of
from draftwright.linting.issues import LintIssue, is_placement_drop
from draftwright.plate_correspondence import (
    _between,
    _depth_axis,
    _envelope,
    _envelope_owned_dependencies,
    _polygonal_boss_dependencies,
    _rounded,
    _same,
    _slot_pattern_dependencies,
    _step_level_dependencies,
    plate_center,
    plate_key,
    plate_span,
)

PlateRequirementState = Literal[
    "placed",
    "satisfied_by_structured_note",
    "suppressed",
    "inapplicable",
    "dropped",
    "missing",
    "unverifiable",
]
Point = tuple[float, float, float]


@dataclass(frozen=True)
class PlateRequirementOutcome:
    """The observable engine outcome of one physical slab-thickness requirement."""

    source_at: Point | None
    parameter_id: str
    state: PlateRequirementState
    requirement_count: int = 1
    features: tuple = ()
    dependencies: tuple[tuple[object, str], ...] = ()


def _parameter_id(feature, source) -> str | None:
    try:
        parameters = tuple(feature.parameters())
        if len(parameters) != 1:
            return None
        (parameter,) = parameters
        if parameter.parameter_id != "thickness.length":
            return None
        if _rounded(parameter.value) != _rounded(source.hi - source.lo):
            return None
        if parameter.span is None:
            return None
        span = tuple(tuple(_rounded(component) for component in point) for point in parameter.span)
        if span != plate_span(source):
            return None
    except (AttributeError, TypeError, ValueError):
        return None
    return "thickness.length"


def _index_evidence(registry):
    placed_measurements = [
        (measurement.feature, measurement.parameter)
        for name in registry.names()
        for measurement in registry.measurement_of(name)
    ]
    placed = set(placed_measurements)
    satisfied = {
        (identity.feature, identity.parameter)
        for identity in satisfaction_ids(registry)
        if identity.feature is not None and isinstance(identity.parameter, str)
    }
    dropped = {
        (measurement.feature, measurement.parameter)
        for issue in registry.issues
        if is_placement_drop(issue)
        for measurement in getattr(issue, "measurement_ids", ())
        if getattr(measurement, "feature", None) is not None
        and isinstance(getattr(measurement, "parameter", None), str)
    }
    return placed, Counter(placed_measurements), satisfied, dropped


def _recognised_slot_pattern_supports_source(source, pattern, validation_cache=None) -> bool:
    """Use the public source-body bounds retained on provider Slot records."""
    try:
        representative = _validated_recognised_pattern(pattern, validation_cache)
        if representative is None:
            return False
        slots = tuple(pattern.slots)
        depth_axis = _depth_axis(representative.width_axis, representative.long_axis)
        assert depth_axis is not None
        point = plate_center(source)
        body_key = tuple(representative.body_key)
        if len(body_key) < 6:
            return False
        body_bounds = tuple(zip(body_key[:3], body_key[3:6], strict=True))
        return any(
            _between(
                point["xyz".index(str(slot.long_axis))],
                (slot.lo, slot.hi),
            )
            and _between(point["xyz".index(depth_axis)], (slot.d_lo, slot.d_hi))
            for slot in slots
        ) and all(_between(point[index], bounds) for index, bounds in enumerate(body_bounds))
    except (AttributeError, IndexError, KeyError, OverflowError, TypeError, ValueError):
        return False


def _validated_recognised_slots(slots):
    """Return one representative only when every provider member has one body-local schema."""
    try:
        values = tuple(slots)
        if len(values) < 3:
            return None
        representative = values[0]
        width_axis = str(representative.width_axis)
        long_axis = str(representative.long_axis)
        depth_axis = _depth_axis(width_axis, long_axis)
        body_key = tuple(_rounded(value) for value in representative.body_key)
        if (
            depth_axis is None
            or len(body_key) < 6
            or not all(isfinite(value) for value in body_key)
        ):
            return None
        bounds = tuple(zip(body_key[:3], body_key[3:6], strict=True))
        if any(lo > hi for lo, hi in bounds):
            return None
        shared = (
            width_axis,
            long_axis,
            body_key,
            _rounded(representative.width),
            _rounded(representative.length),
        )
        if shared[3] <= 0 or shared[4] <= 0:
            return None
        width_index = "xyz".index(width_axis)
        long_index = "xyz".index(long_axis)
        depth_index = "xyz".index(depth_axis)
        locations = set()
        for slot in values:
            lo, hi = _rounded(slot.lo), _rounded(slot.hi)
            d_lo, d_hi = _rounded(slot.d_lo), _rounded(slot.d_hi)
            current = (
                str(slot.width_axis),
                str(slot.long_axis),
                tuple(_rounded(value) for value in slot.body_key),
                _rounded(slot.width),
                _rounded(slot.length),
            )
            width = current[3]
            center = float(slot.w_center)
            cut = (center - width / 2, center + width / 2)
            if (
                current != shared
                or _depth_axis(slot.width_axis, slot.long_axis) != depth_axis
                or not all(
                    isfinite(value)
                    for value in (*current[2], *current[3:], lo, hi, d_lo, d_hi, center)
                )
                or width <= 0
                or lo > hi
                or d_lo > d_hi
                or not _same(current[4], hi - lo)
                or not bounds[width_index][0] <= cut[0] < cut[1] <= bounds[width_index][1]
                or not bounds[long_index][0] <= lo < hi <= bounds[long_index][1]
                or not bounds[depth_index][0] <= d_lo < d_hi <= bounds[depth_index][1]
            ):
                return None
            locations.add((_rounded(center), _rounded((lo + hi) / 2), _rounded((d_lo + d_hi) / 2)))
        if len(locations) != len(values):
            return None
        return representative
    except (AttributeError, IndexError, OverflowError, TypeError, ValueError):
        return None


def _provider_reproduces_slot_pattern(pattern, slots) -> bool:
    """Reapply the public part-less derived projection to reject stale aggregate metadata."""
    from b123d_recognisers import recognise_slot_patterns

    return pattern in recognise_slot_patterns(slots)


def _validate_recognised_pattern_uncached(pattern):
    """Validate every member and require an exact public-provider replay."""
    try:
        slots = tuple(pattern.slots)
        representative = _validated_recognised_slots(slots)
        if representative is None:
            return None

        is_linear = all(hasattr(pattern, name) for name in ("pitch", "direction"))
        is_grid = all(
            hasattr(pattern, name)
            for name in ("rows", "cols", "row_pitch", "col_pitch", "angle", "center")
        )
        if is_linear == is_grid:
            return None
        # Pattern metadata is provider-owned and rounded for serialization. Reconstructing a
        # dense lattice from that rounded pitch accumulates error and can reject the provider's
        # own record. Exact equality with a fresh public, part-less projection validates both
        # the member geometry and every aggregate field without a second tolerance contract.
        return representative if _provider_reproduces_slot_pattern(pattern, slots) else None
    except (AttributeError, IndexError, OverflowError, TypeError, ValueError):
        return None


def _validated_recognised_pattern(pattern, validation_cache=None):
    """Validate one aggregate once per caller-owned completeness-ledger invocation."""
    key = id(pattern)
    if validation_cache is not None and key in validation_cache:
        return validation_cache[key]
    result = _validate_recognised_pattern_uncached(pattern)
    if validation_cache is not None:
        validation_cache[key] = result
    return result


def _valid_polygonal_boss_source(boss) -> bool:
    try:
        from draftwright.linting.polygonal_boss_coverage import (
            _validate_polygonal_boss_source,
        )

        _validate_polygonal_boss_source(boss)
        return True
    except (AttributeError, OverflowError, TypeError, ValueError):
        return False


class _SolidMembership:
    """Cache physical-witness ownership for one completeness-ledger invocation."""

    def __init__(self, part) -> None:
        self.solids = tuple(part.solids()) if part is not None else ()
        self._owners: dict[Point, tuple[int, ...]] = {}

    def owners(self, point) -> tuple[int, ...]:
        values = tuple(float(value) for value in point)
        if len(values) != 3 or not all(isfinite(value) for value in values):
            raise ValueError("a solid-membership witness must be a 3-vector")
        key = (values[0], values[1], values[2])
        if key not in self._owners:
            from build123d import Vector

            witness = Vector(*key)
            self._owners[key] = tuple(
                index for index, solid in enumerate(self.solids) if solid.is_inside(witness)
            )
        return self._owners[key]

    def within_only_solid_bounds(self, point) -> bool:
        """Reject source witnesses that cannot belong to the sole physical body."""
        if len(self.solids) != 1:
            return False
        values = tuple(float(value) for value in point)
        if len(values) != 3 or not all(isfinite(value) for value in values):
            return False
        bounds = self.solids[0].bounding_box()
        minimum = tuple(float(value) for value in bounds.min)
        maximum = tuple(float(value) for value in bounds.max)
        return all(
            minimum[index] - 1e-6 <= value <= maximum[index] + 1e-6
            for index, value in enumerate(values)
        )


def _boss_material_witnesses(boss) -> tuple[Point, ...]:
    """Move just inside each outer flat so a coaxial bore cannot erase boss ownership."""
    points = []
    for direction, centre in zip(boss.flat_directions, boss.flat_centres, strict=True):
        normals = tuple(float(normal) for normal in direction)
        coordinates = tuple(float(component) for component in centre)
        if len(normals) != 3 or len(coordinates) != 3:
            raise ValueError("a boss material witness must be a paired 3-vector")
        points.append(
            tuple(
                float(component) - 1e-3 * float(normal)
                for component, normal in zip(coordinates, normals, strict=True)
            )
        )
    return tuple((point[0], point[1], point[2]) for point in points)


def _shares_one_solid(membership, source, boss) -> bool:
    """Prove the complete boss ring and one material Plate witness share one solid."""
    if (
        membership is None
        or len(membership.solids) != 1
        or not membership.within_only_solid_bounds(plate_center(source))
    ):
        return False
    try:
        witnesses = _boss_material_witnesses(boss)
        if not witnesses:
            return False
        boss_owners = tuple(membership.owners(point) for point in witnesses)
        if any(len(owners) != 1 or owners != boss_owners[0] for owners in boss_owners):
            return False
        source_center_owners = membership.owners(plate_center(source))
        if source_center_owners:
            return len(source_center_owners) == 1 and source_center_owners == boss_owners[0]
        # With one physical solid, complete boss-ring ownership proves the Plate and boss share
        # it even when a bore removes the retained Plate centroid.  With plural solids the Plate
        # record has no body key, so a point inside/nearest another body is not correspondence.
        return bool(boss_owners[0] == (0,))
    except (AttributeError, OverflowError, TypeError, ValueError):
        return False


def _alternate_dependencies(
    source,
    features,
    counts,
    satisfied,
) -> tuple[tuple[object, str], ...]:
    for establish in (
        _envelope_owned_dependencies,
        _step_level_dependencies,
        _polygonal_boss_dependencies,
        _slot_pattern_dependencies,
    ):
        if (dependencies := establish(source, features)) and _dependencies_are_evidenced(
            dependencies, counts, satisfied
        ):
            return dependencies
    return ()


def _without_provider_owned_ir(recognition, features, validation_cache=None) -> tuple:
    """Keep declared owners while withholding exact IR compiled from provider records."""
    from draftwright.linting.polygonal_boss_coverage import polygonal_boss_key

    boss_keys = []
    boss_sources = tuple(recognition.polygonal_bosses)
    boss_inventory_untrusted = False
    for source in boss_sources:
        try:
            if not _valid_polygonal_boss_source(source):
                boss_inventory_untrusted = True
                continue
            boss_keys.append(polygonal_boss_key(source))
        except (AttributeError, IndexError, KeyError, OverflowError, TypeError, ValueError):
            boss_inventory_untrusted = True
            continue
    slot_sources = tuple(recognition.slot_patterns)
    slot_inventory_untrusted = any(
        _validated_recognised_pattern(source, validation_cache) is None for source in slot_sources
    )
    remaining = []
    for feature in features:
        kind = getattr(feature, "kind", None)
        try:
            provider_owned = bool(
                kind == "polygonal_boss"
                and (boss_inventory_untrusted or polygonal_boss_key(feature) in boss_keys)
            ) or bool(
                kind == "slot_pattern"
                and (
                    slot_inventory_untrusted
                    or any(_slot_pattern_corresponds(source, feature) for source in slot_sources)
                )
            )
        except (AttributeError, IndexError, KeyError, OverflowError, TypeError, ValueError):
            provider_owned = False
        if not provider_owned:
            remaining.append(feature)
    return tuple(remaining)


def _slot_pattern_corresponds(source, feature) -> bool:
    """Join provider pattern IR exactly, with a partial-location fallback for malformed members."""
    from draftwright.linting.slot_coverage import _pattern_key, _slot_spec_key

    try:
        return _pattern_key(source) == _pattern_key(feature)
    except (AttributeError, IndexError, KeyError, OverflowError, TypeError, ValueError):
        pass
    try:
        slots = tuple(source.slots)
        if (
            not slots
            or feature.pattern != "linear"
            or feature.count != len(slots)
            or _slot_spec_key(slots[0]) != _slot_spec_key(feature.member)
            or _rounded(source.pitch) != _rounded(feature.pitch)
        ):
            return False
        retained_locations = []
        for slot in slots:
            try:
                retained_locations.append(tuple(_rounded(value) for value in slot.location))
            except (AttributeError, KeyError, OverflowError, TypeError, ValueError):
                continue
        feature_locations = {
            tuple(_rounded(value) for value in point) for point in feature.members
        }
        return bool(retained_locations) and all(
            location in feature_locations for location in retained_locations
        )
    except (AttributeError, IndexError, KeyError, OverflowError, TypeError, ValueError):
        return False


def _dependencies_are_evidenced(dependencies, counts, satisfied) -> bool:
    required = Counter(dependencies)
    return all(
        dependency in satisfied or counts[dependency] >= count
        for dependency, count in required.items()
    )


def _recognition_owner_supersedes_plate(
    source,
    recognition,
    features,
    membership=None,
    validation_cache=None,
) -> bool:
    """Exact aggregate ownership keeps derived Plate fragments out of a second denominator."""
    envelope = _envelope(features)
    if envelope is None:
        return False
    try:
        axis = str(source.axis)
        index = "xyz".index(axis)
        envelope_interval = (
            _rounded(envelope.bbox_min[index]),
            _rounded(envelope.bbox_max[index]),
        )
        source_interval = (_rounded(source.lo), _rounded(source.hi))

        for boss in recognition.polygonal_bosses:
            if str(boss.axis) != axis or not _valid_polygonal_boss_source(boss):
                continue
            boss_interval = (_rounded(boss.base), _rounded(boss.top))
            supports_below = (
                source_interval[0] == envelope_interval[0]
                and source_interval[1] == boss_interval[0]
                and boss_interval[1] == envelope_interval[1]
            )
            supports_above = (
                boss_interval[0] == envelope_interval[0]
                and boss_interval[1] == source_interval[0]
                and source_interval[1] == envelope_interval[1]
            )
            if (supports_below or supports_above) and _shares_one_solid(membership, source, boss):
                return True

        for pattern in recognition.slot_patterns:
            slots = tuple(pattern.slots)
            representative = _validated_recognised_pattern(pattern, validation_cache)
            if representative is None or str(representative.width_axis) != axis:
                continue
            if not _recognised_slot_pattern_supports_source(source, pattern, validation_cache):
                continue
            body_key = tuple(representative.body_key)
            pattern_interval = (_rounded(body_key[index]), _rounded(body_key[index + 3]))
            point = plate_center(source)
            depth_axis = _depth_axis(representative.width_axis, representative.long_axis)
            if depth_axis is None:
                continue
            long_index = "xyz".index(str(representative.long_axis))
            depth_index = "xyz".index(depth_axis)
            crossing = (
                slot
                for slot in slots
                if _between(point[long_index], (slot.lo, slot.hi))
                and _between(point[depth_index], (slot.d_lo, slot.d_hi))
            )
            cuts = sorted(
                {
                    (
                        _rounded(slot.w_center - slot.width / 2),
                        _rounded(slot.w_center + slot.width / 2),
                    )
                    for slot in crossing
                }
            )
            if not cuts:
                continue
            material = []
            cursor = pattern_interval[0]
            for cut_lo, cut_hi in cuts:
                if cut_lo > cursor:
                    material.append((cursor, cut_lo))
                cursor = max(cursor, cut_hi)
            if cursor < pattern_interval[1]:
                material.append((cursor, pattern_interval[1]))
            if source_interval in material:
                return True
    except (AttributeError, TypeError, ValueError):
        return False
    return False


def _derived_opposite_wall_dependencies(features, feature) -> tuple[tuple[object, str], ...]:
    """Return the exact placed facts that derive one U-channel's upper wall."""
    try:
        channels = [item for item in features if getattr(item, "kind", None) == "channel"]
        envelopes = [item for item in features if getattr(item, "kind", None) == "envelope"]
        if len(channels) != 1 or len(envelopes) != 1:
            return ()
        channel = channels[0]
        envelope = envelopes[0]
        axis = str(channel.width_axis)
        if feature.axis != axis:
            return ()
        index = "xyz".index(axis)
        long_index = "xyz".index(str(channel.long_axis))
        bbox_lo = float(envelope.bbox_min[index])
        bbox_hi = float(envelope.bbox_max[index])
        if (
            abs(float(channel.lo) - float(envelope.bbox_min[long_index])) > 1e-6
            or abs(float(channel.hi) - float(envelope.bbox_max[long_index])) > 1e-6
        ):
            return ()
        channel_lo = float(channel.w_center) - float(channel.width) / 2
        channel_hi = float(channel.w_center) + float(channel.width) / 2
        same_axis = [
            item
            for item in features
            if getattr(item, "kind", None) == "plate" and item.axis == axis
        ]
        lower = [
            plate
            for plate in same_axis
            if abs(float(plate.lo) - bbox_lo) <= 1e-6 and abs(float(plate.hi) - channel_lo) <= 1e-6
        ]
        upper = [
            plate
            for plate in same_axis
            if abs(float(plate.lo) - channel_hi) <= 1e-6 and abs(float(plate.hi) - bbox_hi) <= 1e-6
        ]
        if len(lower) != 1 or len(upper) != 1 or feature != upper[0]:
            return ()
        if not (_same(lower[0].u, feature.u) and _same(lower[0].v, feature.v)):
            return ()
        centre = plate_center(feature)
        depth_axis = _depth_axis(axis, channel.long_axis)
        if depth_axis is None:
            return ()
        if not (
            _between(centre[long_index], (channel.lo, channel.hi))
            and _between(centre["xyz".index(depth_axis)], (channel.d_lo, channel.d_hi))
        ):
            return ()
        envelope_parameter = {"x": "width.length", "y": "depth.length", "z": "height.length"}[axis]
    except (AttributeError, TypeError, ValueError):
        return ()
    return (
        (lower[0], "thickness.length"),
        (channel, "channel_width.length"),
        (envelope, envelope_parameter),
    )


def _state(
    feature,
    parameter,
    *,
    placed,
    satisfied,
    suppressed,
    inapplicable,
    dropped,
    registry,
):
    if (feature, parameter) in inapplicable:
        return "inapplicable"
    if (feature, parameter) in placed:
        return "placed"
    if (feature, parameter) in satisfied:
        return "satisfied_by_structured_note"
    if (feature, parameter) in suppressed:
        return "suppressed"
    if (feature, parameter) in dropped:
        return "dropped"
    associated = registry.names_for_feature(feature)
    if any(
        not registry.measurement_of(name) and not satisfaction_of(registry, name)
        for name in associated
    ):
        return "unverifiable"
    return "missing"


def plate_requirement_outcomes(
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
    *,
    part=None,
) -> list[PlateRequirementOutcome]:
    """Follow every recognised body-local slab thickness to its semantic outcome."""
    if recognition is None:
        return []
    if not isinstance(recognition, RecognitionResult):
        raise TypeError(
            "plate_requirement_outcomes() requires the run's RecognitionResult; "
            f"got {type(recognition).__name__}"
        )
    sources = tuple(recognition.plates)
    if not sources:
        return []

    keyed_sources: list[tuple[object, tuple | None, Point | None]] = []
    source_counts: dict[tuple, int] = defaultdict(int)
    for source in sources:
        try:
            key = plate_key(source)
            at = plate_center(source)
        except (AttributeError, TypeError, ValueError):
            key = None
            at = None
        keyed_sources.append((source, key, at))
        if key is not None:
            source_counts[key] += 1

    ir_by_key: dict[tuple, list] = defaultdict(list)
    for feature in features:
        if getattr(feature, "kind", None) != "plate":
            continue
        try:
            ir_by_key[plate_key(feature)].append(feature)
        except (AttributeError, TypeError, ValueError):
            continue

    placed, placed_counts, satisfied, dropped = _index_evidence(registry)
    suppressed = {
        (omission.feature, omission.parameter_id)
        for omission in omissions
        if omission.feature is not None and omission.authored
    }
    evidenced = placed | satisfied
    derived_dependencies = {
        feature: dependencies
        for feature in features
        if (dependencies := _derived_opposite_wall_dependencies(features, feature))
        and all(dependency in evidenced for dependency in dependencies)
    }
    inapplicable = {(feature, "thickness.length") for feature in derived_dependencies}
    membership = _SolidMembership(part) if part is not None else None
    pattern_validations: dict[int, object | None] = {}
    alternate_features = _without_provider_owned_ir(recognition, features, pattern_validations)
    outcomes: list[PlateRequirementOutcome] = []
    for source_record, key, at in keyed_sources:
        matches = ir_by_key.get(key, ()) if key is not None else ()
        feature = (
            matches[0] if key is not None and len(matches) == source_counts[key] == 1 else None
        )
        parameter = _parameter_id(feature, source_record) if feature is not None else None
        if parameter is None:
            recognised_owner = _recognition_owner_supersedes_plate(
                source_record,
                recognition,
                features,
                membership,
                pattern_validations,
            )
            dependencies = _alternate_dependencies(
                source_record,
                alternate_features,
                placed_counts,
                satisfied,
            )
            if dependencies or recognised_owner:
                outcomes.append(
                    PlateRequirementOutcome(
                        at,
                        "thickness.length",
                        "inapplicable",
                        dependencies=dependencies,
                    )
                )
                continue
            outcomes.append(PlateRequirementOutcome(at, "?", "unverifiable"))
            continue
        outcomes.append(
            PlateRequirementOutcome(
                at,
                parameter,
                _state(
                    feature,
                    parameter,
                    placed=placed,
                    satisfied=satisfied,
                    suppressed=suppressed,
                    inapplicable=inapplicable,
                    dropped=dropped,
                    registry=registry,
                ),
                features=(feature,),
                dependencies=derived_dependencies.get(feature, ()),
            )
        )
    return outcomes


def lint_plate_coverage(
    part,
    *,
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
    assembly=None,
) -> list[LintIssue]:
    """Report uncovered Plate requirements without duplicating placement drops."""
    if assembly is None:
        assembly = len(part.solids()) > 1
    severity: Literal["info", "warning"] = "info" if assembly else "warning"
    messages = {
        "suppressed": "was deliberately omitted by the authored dimension set",
        "missing": "has no placed, suppressed, or dropped measurement outcome",
        "unverifiable": "cannot be joined to measurement provenance without guessing",
    }
    issues = []
    for outcome in plate_requirement_outcomes(
        recognition, features, registry, omissions, part=part
    ):
        if outcome.state in {
            "placed",
            "satisfied_by_structured_note",
            "dropped",
            "inapplicable",
        }:
            continue
        issues.append(
            LintIssue(
                severity=severity,
                code=f"plate_requirement_{outcome.state}",
                message=(
                    f"plate at {outcome.source_at} measurement {outcome.parameter_id} "
                    f"{messages[outcome.state]}"
                ),
            )
        )
    return issues
