"""Semantic completeness for recognised holes and hole patterns (#1143).

Recognition owns the physical denominator. Exact geometry joins that inventory to the
declared/detected IR, while compiler ``DimensionId`` values join IR requirements to placed,
suppressed, and dropped outcomes. Rendered labels and annotation names are never evidence.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Literal

from draftwright.linting.issues import LintIssue
from draftwright.recognition import HoleSpec, RecognitionResult, countersink_matches_hole

HoleRequirementState = Literal["placed", "suppressed", "dropped", "missing", "unverifiable"]
HoleSourceKind = Literal["hole", "hole_pattern"]


@dataclass(frozen=True)
class HoleRequirementOutcome:
    """The observable engine outcome of one recognised hole requirement."""

    source_kind: HoleSourceKind
    source_at: tuple[float, float, float]
    member_count: int
    parameter_id: str
    state: HoleRequirementState
    requirement_count: int = 1


def _rounded(value) -> float:
    return round(float(value), 3)


def _point(value) -> tuple[float, float, float]:
    x, y, z = value
    return (_rounded(x), _rounded(y), _rounded(z))


def _axis_letter(axis) -> str:
    return max(zip("xyz", axis, strict=True), key=lambda item: abs(float(item[1])))[0]


def _signed_axis(axis) -> tuple[float, float, float]:
    """Canonical signed drilling direction for recognition-owned grouping."""
    vector = tuple(float(component) for component in axis)
    norm = sum(component * component for component in vector) ** 0.5
    return _point(tuple(component / norm for component in vector))


def _recess_key(value):
    if value is None:
        return None
    if hasattr(value, "diameter"):
        diameter, depth = value.diameter, value.depth
    else:
        diameter, depth = value
    return (_rounded(diameter), _rounded(depth))


def _recognised_spec(hole) -> tuple:
    spec = HoleSpec.from_hole(hole)
    return (
        _axis_letter(spec.axis),
        _rounded(spec.diameter),
        None if spec.depth is None else _rounded(spec.depth),
        spec.bottom == "through",
        _recess_key(spec.cbore),
        _recess_key(spec.spotface),
        None if spec.csink is None else tuple(_rounded(value) for value in spec.csink),
    )


def _feature_spec(feature) -> tuple:
    hole = getattr(feature, "member", feature)
    return (
        hole.frame.axis,
        _rounded(hole.diameter),
        None if hole.through or hole.depth is None else _rounded(hole.depth),
        bool(hole.through),
        _recess_key(hole.cbore),
        _recess_key(hole.spotface),
        None if hole.csink is None else tuple(_rounded(value) for value in hole.csink),
    )


def _members(source) -> tuple[tuple[float, float, float], ...]:
    recognised = getattr(source, "holes", None)
    if recognised is not None:
        points = tuple(hole.location for hole in recognised)
        axis = _axis_letter(recognised[0].axis)
        through = all(HoleSpec.from_hole(hole).bottom == "through" for hole in recognised)
    elif hasattr(source, "location"):
        points = (source.location,)
        axis = _axis_letter(source.axis)
        through = HoleSpec.from_hole(source).bottom == "through"
    else:
        points = getattr(source, "members", ()) or (source.frame.origin,)
        hole = getattr(source, "member", source)
        axis = hole.frame.axis
        through = bool(hole.through)
    index = "xyz".index(axis)
    sites = []
    for point in points:
        site = list(_point(point))
        # A through hole's opening coordinate along its own axis is not retained
        # consistently at the public IR waist (object declarations may use a tool centre;
        # recognition uses one opening face), and is not a location dimension. Blind-hole
        # axial position *is* load-bearing identity: opposed bores can share the projected
        # axis, diameter and depth while being two distinct machining requirements.
        if through:
            site[index] = 0.0
        sites.append((site[0], site[1], site[2]))
    return tuple(sorted(sites))


def _tool_center_members(source, members, depth) -> tuple[tuple[float, float, float], ...]:
    """Expected cutter centres for a recognised blind source's opening members."""
    recognised = getattr(source, "holes", None)
    axis = recognised[0].axis if recognised is not None else source.axis
    direction = _signed_axis(axis)
    distance = float(depth) / 2.0
    return tuple(
        sorted(
            _point(tuple(member[index] + direction[index] * distance for index in range(3)))
            for member in members
        )
    )


def _hole_partitions(spec, members, features) -> tuple[tuple, ...]:
    """Return the one forced exact feature cover of physical members, if it exists.

    A loose recognition group may correspond to one grouped ``HoleFeature`` or to one
    object-backed declared feature per member. Every physical member must have exactly one
    compatible declared owner and the union of those owners must equal the physical
    multiset. Any overlap or extra owner is ambiguous and fails closed. This is the same
    conservative contract as forced-choice propagation, expressed as one linear incidence
    pass rather than repeatedly rescanning the shrinking inventory.
    """
    target = Counter(members)

    def fits(coverage, available):
        # ``Counter.__le__`` scans the union of both counters.  Here each declared owner
        # is normally a singleton, so checking only its own support preserves the same
        # multiset relation without turning N singleton candidates into O(N²) work.
        return all(count <= available[member] for member, count in coverage.items())

    eligible = []
    for feature in features:
        if _feature_spec(feature) != spec:
            continue
        coverage = Counter(_members(feature))
        if coverage and fits(coverage, target):
            eligible.append((feature, coverage))
    if len({id(feature) for feature, _coverage in eligible}) != len(eligible):
        return ()
    owners: dict[tuple[float, float, float], int] = defaultdict(int)
    combined: Counter[tuple[float, float, float]] = Counter()
    for _feature, coverage in eligible:
        combined.update(coverage)
        for member in coverage:
            owners[member] += 1
    if combined != target or any(owners[member] != 1 for member in target):
        return ()
    return (tuple(feature for feature, _coverage in eligible),)


def _unoriented_direction(value):
    if value is None:
        return None
    vector = tuple(float(component) for component in value)
    norm = sum(component * component for component in vector) ** 0.5
    if norm == 0:
        return None
    direction = tuple(component / norm for component in vector)
    first = next((component for component in direction if abs(component) > 1e-9), 1.0)
    if first < 0:
        direction = tuple(-component for component in direction)
    return _point(direction)


def _pattern_kind(pattern) -> str:
    if hasattr(pattern, "diameter") and hasattr(pattern, "center"):
        return "bolt_circle"
    if hasattr(pattern, "row_pitch"):
        return "grid"
    return "linear"


def _default_linear_direction(pattern):
    """The declared linear default, derived from its already-materialised members."""
    members = _members(pattern)
    if len(members) < 2:
        return None
    start, end = max(
        ((a, b) for index, a in enumerate(members) for b in members[index + 1 :]),
        key=lambda pair: sum((pair[1][i] - pair[0][i]) ** 2 for i in range(3)),
    )
    return tuple(end[i] - start[i] for i in range(3))


def _pattern_key(pattern) -> tuple:
    recognised = getattr(pattern, "holes", None)
    if recognised is not None:
        kind = _pattern_kind(pattern)
        member = recognised[0]
        bcd = getattr(pattern, "diameter", None) if kind == "bolt_circle" else None
        pitch = getattr(pattern, "pitch", None) if kind == "linear" else None
        grid = (
            (getattr(pattern, "row_pitch"), getattr(pattern, "col_pitch"))
            if kind == "grid"
            else None
        )
        direction = getattr(pattern, "direction", None)
        rows = getattr(pattern, "rows", None)
        cols = getattr(pattern, "cols", None)
        angle = getattr(pattern, "angle", None)
        spec = _recognised_spec(member)
    else:
        kind = pattern.pattern
        bcd = getattr(pattern, "bcd", None)
        pitch = getattr(pattern, "pitch", None)
        grid = getattr(pattern, "grid", None)
        direction = getattr(pattern, "direction", None)
        rows = getattr(pattern, "rows", None)
        cols = getattr(pattern, "cols", None)
        angle = getattr(pattern, "angle", None)
        spec = _feature_spec(pattern)
    if kind == "linear" and direction is None:
        direction = _default_linear_direction(pattern)
    if kind == "grid" and angle is None:
        angle = 0.0
    if (
        kind == "grid"
        and grid is not None
        and rows is not None
        and cols is not None
        and angle is not None
    ):
        row_pitch, col_pitch = (_rounded(value) for value in grid)
        direct = (int(rows), int(cols), row_pitch, col_pitch, _rounded(float(angle) % 180.0))
        transposed = (
            int(cols),
            int(rows),
            col_pitch,
            row_pitch,
            _rounded((float(angle) + 90.0) % 180.0),
        )
        rows, cols, row_pitch, col_pitch, angle = min(direct, transposed)
        grid = (row_pitch, col_pitch)
    return (
        kind,
        spec,
        _members(pattern),
        None if bcd is None else _rounded(bcd),
        None if pitch is None else _rounded(pitch),
        None if grid is None else tuple(_rounded(value) for value in grid),
        _unoriented_direction(direction) if kind == "linear" else None,
        rows,
        cols,
        None if angle is None else _rounded(float(angle) % 180.0),
    )


def _tool_center_pattern_key(pattern) -> tuple:
    """Pattern identity at the cutter centre implied by its recognised opening."""
    key = list(_pattern_key(pattern))
    depth = key[1][2]
    if depth is None or key[1][3]:
        return tuple(key)
    key[2] = _tool_center_members(pattern, key[2], depth)
    return tuple(key)


def _source_at(source) -> tuple[float, float, float]:
    center = getattr(source, "center", None)
    if center is not None:
        return _point(center)
    points = _members(source)
    return (
        _rounded(sum(point[0] for point in points) / len(points)),
        _rounded(sum(point[1] for point in points) / len(points)),
        _rounded(sum(point[2] for point in points) / len(points)),
    )


def _recognised_turned_axis_center(recognition, axis):
    """Recover the one external-cylinder axis that supports the turned-step ladder."""
    steps = tuple(step for step in recognition.turned_steps if step.axis == axis)
    if not steps:
        return None
    perpendicular = tuple(i for i, letter in enumerate("xyz") if letter != axis)
    support: dict[tuple[float, float], tuple[set[int], float]] = {}
    for cylinder in (item for group in recognition.cylinders for item in group):
        if not cylinder.get("external") or cylinder.get("axis") != axis:
            continue
        center_values = tuple(
            round(float(cylinder["axis_xyz"][index]), 3) for index in perpendicular
        )
        center = (center_values[0], center_values[1])
        matched, overlap = support.setdefault(center, (set(), 0.0))
        for index, step in enumerate(steps):
            if abs(float(cylinder["diameter"]) - float(step.diameter)) > 1e-3:
                continue
            shared = max(
                0.0,
                min(float(cylinder["s_hi"]), float(step.hi))
                - max(float(cylinder["s_lo"]), float(step.lo)),
            )
            if shared > 0:
                matched.add(index)
                overlap += shared
        support[center] = (matched, overlap)
    ranked = {
        center: (len(matched), round(overlap, 3))
        for center, (matched, overlap) in support.items()
        if matched
    }
    if not ranked:
        return None
    best = max(ranked.values())
    winners = [center for center, score in ranked.items() if score == best]
    return winners[0] if len(winners) == 1 else None


def _member_coaxial_with_turned_profile(feature, member, recognition) -> bool:
    axis = feature.frame.axis
    perpendicular = tuple(i for i, letter in enumerate("xyz") if letter != axis)
    center = _recognised_turned_axis_center(recognition, axis)
    if center is None:
        return False
    return all(
        abs(center[offset] - member[index]) <= 1e-3 for offset, index in enumerate(perpendicular)
    )


def _coaxial_with_turned_profile(feature, recognition) -> bool:
    return all(
        _member_coaxial_with_turned_profile(feature, member, recognition)
        for member in _members(feature)
    )


def _parameter_ids(
    feature, *, member_count: int | None = None, recognition
) -> tuple[str, ...] | None:
    try:
        parameters = tuple(feature.parameters())
    except (AttributeError, TypeError):
        return None
    ids = [parameter.parameter_id for parameter in parameters]
    hole = getattr(feature, "member", feature)
    bore_id = "bore.diameter"
    if bore_id not in ids:
        return None
    if hole.through:
        ids.append("bore.through")
    if int(member_count if member_count is not None else getattr(feature, "count", 1) or 1) > 1:
        ids.append("grouping.count")
    if feature.frame.axis == "z":
        stem = getattr(feature, "LOCATION_STEM", None)
        if stem is None:
            return None
        ids.extend(f"{stem}.location.{axis}" for axis in ("x", "y"))
    elif getattr(feature, "kind", None) == "hole":
        stem = getattr(feature, "LOCATION_OFF_AXIS_STEM", None)
        if stem is None:
            return None
        if _coaxial_with_turned_profile(feature, recognition):
            # The axis line constrains both otherwise-independent in-plane positions. Keep
            # two outcomes so a declaration mismatch cannot shrink the recognition-owned
            # denominator merely because the visible evidence is one centreline.
            in_plane = "y" if feature.frame.axis == "x" else "x"
            ids.extend((f"{stem}.centerline.{in_plane}", f"{stem}.centerline.z"))
        else:
            in_plane = "y" if feature.frame.axis == "x" else "x"
            ids.extend((f"{stem}.{in_plane}", f"{stem}.z"))
    else:
        # Pitch/direction/count define only relative arrangement. The current compiler has
        # no off-axis pattern location producer, so retain both absolute in-plane physical
        # requirements as explicit missing outcomes instead of deleting them from the
        # recognition denominator. These stable ids extend the feature-owned location stem;
        # a future compiler/renderer can make them placed without changing the ledger schema.
        stem = getattr(feature, "LOCATION_STEM", None)
        if stem is None:
            return None
        in_plane = "y" if feature.frame.axis == "x" else "x"
        ids.extend((f"{stem}.location.{in_plane}", f"{stem}.location.z"))
    return tuple(ids)


def _matches(measurement, feature, parameter: str) -> bool:
    return (
        getattr(measurement, "feature", None) == feature
        and getattr(measurement, "parameter", None) == parameter
    )


def _evidence_parameter(parameter: str) -> str:
    if parameter in {"bore.through", "grouping.count"}:
        return "bore.diameter"
    if ".centerline." in parameter:
        # The physical ledger distinguishes a turned-axis location from an ordinary
        # numeric ordinate, but the compiler deliberately keeps the canonical Y/Z
        # measurement identities.  Join both placed drops and authored omissions back to
        # those compiler-owned ids instead of inventing a second suppression vocabulary.
        return parameter.replace(".centerline.", ".")
    return parameter


def _location_members(feature, parameter: str):
    if parameter.startswith("location_pattern."):
        point = list(_point(feature.frame.origin))
        if getattr(feature, "member", feature).through:
            point["xyz".index(feature.frame.axis)] = 0.0
        return ((point[0], point[1], point[2]),)
    return _members(feature)


def _structured_locations_placed(registry, features, parameter: str, recognition) -> bool:
    expected = {
        (feature, point) for feature in features for point in _location_members(feature, parameter)
    }
    covered = set()
    for name in registry.names():
        for measurement, point in getattr(registry.named(name), "covers_hole_locations", ()):
            feature = getattr(measurement, "feature", None)
            if feature not in features or getattr(measurement, "parameter", None) != parameter:
                continue
            assert feature is not None
            normalized = list(_point(point))
            hole = getattr(feature, "member", feature)
            if hole.through:
                normalized["xyz".index(feature.frame.axis)] = 0.0
            covered.add((feature, (normalized[0], normalized[1], normalized[2])))
        for feature, point, view in getattr(registry.named(name), "covers_hole_centers", ()):
            if feature not in features or getattr(feature, "kind", None) != "hole":
                continue
            if view != {"x": "side", "y": "front", "z": "plan"}[feature.frame.axis]:
                continue
            normalized = list(_point(point))
            hole = getattr(feature, "member", feature)
            if hole.through:
                normalized["xyz".index(feature.frame.axis)] = 0.0
            normalized_point = (normalized[0], normalized[1], normalized[2])
            if _member_coaxial_with_turned_profile(feature, normalized_point, recognition):
                covered.add((feature, normalized_point))
    return bool(expected) and expected <= covered


def _synthetic_placed(registry, features, parameter: str, member_count: int) -> bool:
    if ".centerline." in parameter:
        return any(
            getattr(registry.named(name), "is_centerline", False)
            for feature in features
            for name in registry.names_for_feature(feature)
        )
    counts_by_feature: dict[object, set[int]] = defaultdict(set)
    for name in registry.names():
        for feature, requirement, count in getattr(
            registry.named(name), "covers_hole_requirements_by_feature", ()
        ):
            if feature not in features or requirement != parameter:
                continue
            if parameter == "bore.through":
                return True
            if parameter == "grouping.count":
                counts_by_feature[feature].add(int(count))
    for feature in features:
        for name in registry.names_for_feature(feature):
            annotation = registry.named(name)
            if not any(
                _matches(measurement, feature, "bore.diameter")
                for measurement in registry.measurement_of(name)
            ):
                continue
            covered = getattr(annotation, "covers_hole_requirements", ())
            if parameter == "bore.through" and parameter in covered:
                return True
            if parameter == "grouping.count":
                counts_by_feature[feature].add(int(getattr(annotation, "covers_count", 1) or 1))
    # Cardinality is a definition, not minimum coverage: 3× cannot truthfully certify a
    # physical two-hole group, and duplicate count-bearing annotations must fail closed.
    if parameter != "grouping.count":
        return False
    # One exact group claim is sufficient.  A declared model may instead retain one
    # independently called-out feature per physical member, so accept an exact partition
    # across distinct feature owners.  Duplicate annotations of the same feature add no
    # cardinality; conflicting claims remain alternatives rather than being summed.
    possible = {0}
    for claims in counts_by_feature.values():
        possible |= {subtotal + claim for subtotal in possible for claim in claims}
    return member_count in possible


def _state(
    features, parameter, *, member_count, placed, suppressed, dropped, registry, recognition
):
    evidence = _evidence_parameter(parameter)
    if parameter.startswith(("location.location.", "location_off_axis.")):
        if _structured_locations_placed(registry, features, parameter, recognition):
            return "placed"
    elif parameter in {"bore.through", "grouping.count"}:
        if _synthetic_placed(registry, features, parameter, member_count):
            return "placed"
    else:
        per_feature = [
            any(_matches(measurement, feature, evidence) for measurement in placed)
            for feature in features
        ]
        # A common machining specification needs one truthful statement; a location
        # requirement needs every separately declared physical member tied to the mark.
        if all(per_feature) if "location" in parameter else any(per_feature):
            return "placed"
    if all((feature, evidence) in suppressed for feature in features):
        return "suppressed"
    if any(
        _matches(measurement, feature, evidence) for feature in features for measurement in dropped
    ):
        return "dropped"
    return "missing"


def _physical_requirement_count(kind: HoleSourceKind, source, member_count: int) -> int:
    hole = source.holes[0] if kind == "hole_pattern" else source
    count = 2  # bore diameter + THRU or blind depth
    count += 2 if hole.cbore is not None else 0
    count += 2 if hole.spotface is not None else 0
    count += 2 if hole.csink is not None else 0
    count += 1 if member_count > 1 else 0
    count += 2  # independent datum-location axes
    if kind == "hole_pattern":
        pattern_kind = _pattern_kind(source)
        count += 2 if pattern_kind == "grid" else 1
    return count


def hole_requirement_outcomes(
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
) -> list[HoleRequirementOutcome]:
    """Follow every recognised hole requirement to an exact compiler/placement outcome."""
    if recognition is None:
        return []
    if not isinstance(recognition, RecognitionResult):
        raise TypeError(
            "hole_requirement_outcomes() requires the run's RecognitionResult; "
            f"got {type(recognition).__name__}"
        )

    pattern_members = {hole for pattern in recognition.hole_patterns for hole in pattern.holes}
    loose_groups: dict[tuple, list] = defaultdict(list)
    for hole in recognition.holes:
        if hole not in pattern_members:
            # HoleSpec's signed axis is part of machining identity. Keep opposite-face
            # blind bores in separate source groups even when every printed size matches.
            loose_groups[(_recognised_spec(hole), _signed_axis(hole.axis))].append(hole)

    sources: list[tuple[HoleSourceKind, object, tuple, int]] = []
    for (spec, _direction), holes in loose_groups.items():
        axis_index = "xyz".index(spec[0])
        through = spec[3]
        member_sites = []
        for hole in holes:
            site = list(_point(hole.location))
            if through:
                site[axis_index] = 0.0
            member_sites.append((site[0], site[1], site[2]))
        members = tuple(sorted(member_sites))
        sources.append(("hole", holes[0], (spec, members), len(holes)))
    sources.extend(
        ("hole_pattern", pattern, _pattern_key(pattern), len(pattern.holes))
        for pattern in recognition.hole_patterns
    )
    attached_countersinks = Counter(
        hole.csink for hole in recognition.holes if getattr(hole, "csink", None) is not None
    )
    unmatched_countersinks = []
    for countersink in recognition.countersinks:
        if attached_countersinks[countersink]:
            attached_countersinks[countersink] -= 1
        elif any(countersink_matches_hole(countersink, hole) for hole in recognition.holes):
            unmatched_countersinks.append(countersink)

    ir_by_key: dict[tuple[str, tuple], list] = defaultdict(list)
    for feature in features:
        feature_kind = getattr(feature, "kind", None)
        if feature_kind == "hole":
            ir_by_key[(feature_kind, (_feature_spec(feature), _members(feature)))].append(feature)
        elif feature_kind == "pattern":
            ir_by_key[("hole_pattern", _pattern_key(feature))].append(feature)
    hole_features = tuple(
        feature for feature in features if getattr(feature, "kind", None) == "hole"
    )
    hole_features_by_spec: dict[tuple, list] = defaultdict(list)
    for feature in hole_features:
        hole_features_by_spec[_feature_spec(feature)].append(feature)
    pattern_features = tuple(
        feature for feature in features if getattr(feature, "kind", None) == "pattern"
    )
    # Establish exact correspondences first, then solve the residual projected fallback.
    # Object-backed blind declarations may retain a cutter centre while recognition owns
    # its opening. Projection is safe only when, after exact owners are consumed, one
    # remaining source has one remaining feature. This is a global bijection rather than a
    # per-source uniqueness guess: an exact declaration on one face can disambiguate the
    # valid tool-centred declaration on the opposed face, while two projected-only owners
    # remain unverifiable.
    exact_proposals: list[tuple] = []
    for kind, _source, key, _member_count in sources:
        if kind == "hole":
            source_spec, source_members = key
            partitions = _hole_partitions(
                source_spec,
                source_members,
                hole_features_by_spec.get(source_spec, ()),
            )
            candidates = partitions[0] if len(partitions) == 1 else ()
        else:
            direct = tuple(ir_by_key.get((kind, key), ()))
            candidates = direct if len(direct) == 1 else ()
        exact_proposals.append(candidates)

    exact_claims = Counter(id(feature) for proposal in exact_proposals for feature in proposal)
    matches_by_source = [
        proposal if all(exact_claims[id(feature)] == 1 for feature in proposal) else ()
        for proposal in exact_proposals
    ]
    used_feature_ids = {id(feature) for matches in matches_by_source for feature in matches}
    # Reserve every declared owner that overlaps an exact physical source before the
    # axial-coordinate fallback.  Index the source inventory once by machining spec: a
    # dense N-member group with N singleton declarations must remain linear rather than
    # rebuilding the N-member source set for every candidate.
    exact_source_members_by_spec: dict[tuple, set[tuple[float, float, float]]] = defaultdict(set)
    for kind, _source, key, _member_count in sources:
        if kind == "hole":
            exact_source_members_by_spec[key[0]].update(key[1])
    exact_overlap_feature_ids = set()
    for feature in hole_features:
        source_members = exact_source_members_by_spec.get(_feature_spec(feature))
        if source_members is not None and any(
            member in source_members for member in _members(feature)
        ):
            exact_overlap_feature_ids.add(id(feature))

    residual_proposals: dict[int, tuple] = {}
    for index, (kind, source, key, _member_count) in enumerate(sources):
        if matches_by_source[index]:
            continue
        if kind == "hole":
            source_spec, source_members = key
            candidate_features = tuple(
                feature
                for feature in hole_features_by_spec.get(source_spec, ())
                if id(feature) not in used_feature_ids
                and id(feature) not in exact_overlap_feature_ids
            )
            depth = source_spec[2]
            expected_centres = (
                _tool_center_members(source, source_members, depth)
                if depth is not None and not source_spec[3]
                else ()
            )
            partitions = (
                _hole_partitions(source_spec, expected_centres, candidate_features)
                if expected_centres
                else ()
            )
            candidates = partitions[0] if len(partitions) == 1 else ()
        else:
            projected_key = _tool_center_pattern_key(source)
            candidates = tuple(
                feature
                for feature in pattern_features
                if id(feature) not in used_feature_ids and _pattern_key(feature) == projected_key
            )
            if len(candidates) != 1:
                candidates = ()
        if candidates:
            residual_proposals[index] = candidates

    residual_claims = Counter(
        id(feature) for proposal in residual_proposals.values() for feature in proposal
    )
    for index, proposal in residual_proposals.items():
        if all(residual_claims[id(feature)] == 1 for feature in proposal):
            matches_by_source[index] = proposal

    placed = {
        measurement for name in registry.names() for measurement in registry.measurement_of(name)
    }
    suppressed = {
        (omission.feature, omission.parameter_id)
        for omission in omissions
        if omission.feature is not None and omission.authored
    }
    dropped = {
        measurement
        for issue in registry.issues
        for measurement in getattr(issue, "measurement_ids", ())
    }

    outcomes = []
    for index, (kind, source, _key, member_count) in enumerate(sources):
        matched = matches_by_source[index]
        representative = matched[0] if matched else None
        parameters = (
            _parameter_ids(representative, member_count=member_count, recognition=recognition)
            if representative is not None
            else None
        )
        at = _source_at(source)
        if parameters is None:
            outcomes.append(
                HoleRequirementOutcome(
                    kind,
                    at,
                    member_count,
                    "?",
                    "unverifiable",
                    requirement_count=_physical_requirement_count(kind, source, member_count),
                )
            )
            continue
        outcomes.extend(
            HoleRequirementOutcome(
                kind,
                at,
                member_count,
                parameter,
                _state(
                    matched,
                    parameter,
                    member_count=member_count,
                    placed=placed,
                    suppressed=suppressed,
                    dropped=dropped,
                    registry=registry,
                    recognition=recognition,
                ),
            )
            for parameter in parameters
        )
    # The current HoleRecord waist has one countersink slot.  A second seat on the
    # opposite face is still a recognised physical requirement, but cannot be joined to
    # IR/compiler provenance without guessing which face the single slot represents.
    # Keep both of its dimensional facts in the denominator as explicit unverifiable
    # outcomes instead of hiding the standalone recognition inventory as a duplicate.
    for countersink in unmatched_countersinks:
        at = _point(countersink.location)
        outcomes.extend(
            HoleRequirementOutcome("hole", at, 1, parameter, "unverifiable")
            for parameter in ("countersink.diameter", "countersink.angle")
        )
    return outcomes


def lint_hole_coverage(
    part,
    *,
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
    assembly=None,
) -> list[LintIssue]:
    """Report unaccounted hole requirements without duplicating explicit drop findings."""
    if assembly is None:
        assembly = len(part.solids()) > 1
    severity: Literal["info", "warning"] = "info" if assembly else "warning"
    messages = {
        "suppressed": "was deliberately omitted by the authored dimension set",
        "missing": "has no placed, suppressed, or dropped measurement outcome",
        "unverifiable": "cannot be joined to measurement provenance without guessing",
    }
    issues = []
    for outcome in hole_requirement_outcomes(recognition, features, registry, omissions):
        if outcome.state in {"placed", "dropped"}:
            continue
        noun = "hole" if outcome.source_kind == "hole" else f"{outcome.member_count}-hole pattern"
        issues.append(
            LintIssue(
                severity=severity,
                code=f"hole_requirement_{outcome.state}",
                message=(
                    f"{noun} at {outcome.source_at} requirement {outcome.parameter_id} "
                    f"{messages[outcome.state]}"
                ),
            )
        )
    return issues
