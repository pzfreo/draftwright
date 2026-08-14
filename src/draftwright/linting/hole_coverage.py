"""Semantic completeness for recognised holes and hole patterns (#1143).

Recognition owns the physical denominator. Exact geometry joins that inventory to the
declared/detected IR, while compiler ``DimensionId`` values join IR requirements to placed,
suppressed, and dropped outcomes. Rendered labels and annotation names are never evidence.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from math import hypot
from typing import Literal

from draftwright._geometry import _is_principal_axis
from draftwright.linting.issues import LintIssue
from draftwright.recognition import HoleSpec, RecognitionResult, countersink_matches_hole

HoleRequirementState = Literal["placed", "suppressed", "dropped", "missing", "unverifiable"]
HoleSourceKind = Literal["hole", "hole_pattern"]
_DIRECTION_PROJECTION_REL_TOL = 1e-6


@dataclass(frozen=True)
class HoleRequirementOutcome:
    """The observable engine outcome of one recognised hole requirement.

    ``representation`` and ``representation_reason`` identify a transactional table win or
    feature-annotation rollback. They remain ``None`` for ordinary/additive evidence and when
    more than one marked representation contributes, so critique never guesses a winner.
    """

    source_kind: HoleSourceKind
    source_at: tuple[float, float, float]
    member_count: int
    parameter_id: str
    state: HoleRequirementState
    requirement_count: int = 1
    representation: str | None = None
    representation_reason: str | None = None


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
        None,  # circular recognition cannot certify a declared structural bore profile
        _is_principal_axis(spec.axis),
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
        getattr(hole, "profile", None),
        True,  # every Frame axis is representable by construction
    )


def _members(source, *, rounded: bool = True) -> tuple[tuple[float, float, float], ...]:
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
        site = [float(component) for component in point]
        # A through hole's opening coordinate along its own axis is not retained
        # consistently at the public IR waist (object declarations may use a tool centre;
        # recognition uses one opening face), and is not a location dimension. Blind-hole
        # axial position *is* load-bearing identity: opposed bores can share the projected
        # axis, diameter and depth while being two distinct machining requirements.
        if through:
            site[index] = 0.0
        sites.append(_point(site) if rounded else (site[0], site[1], site[2]))
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


def _hole_partitions(spec, members, features) -> tuple[tuple[tuple, ...], bool]:
    """Return the forced exact cover and whether any exact owner evidence exists.

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
        return (), bool(eligible)
    owners: dict[tuple[float, float, float], int] = defaultdict(int)
    combined: Counter[tuple[float, float, float]] = Counter()
    for _feature, coverage in eligible:
        combined.update(coverage)
        for member in coverage:
            owners[member] += 1
    if combined != target or any(owners[member] != 1 for member in target):
        return (), bool(eligible)
    return (tuple(feature for feature, _coverage in eligible),), bool(eligible)


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


def _planar_direction(value, axis: str):
    """A linear-pattern direction in its hole opening plane."""
    if value is None:
        return None
    vector = [float(component) for component in value]
    original_norm = hypot(*vector)
    vector["xyz".index(axis)] = 0.0
    # A nominally axial declaration has no meaningful opening-plane direction.  Do not
    # amplify floating-point residue into a unit axis that can certify an unrelated pattern.
    if original_norm == 0.0 or hypot(*vector) <= original_norm * _DIRECTION_PROJECTION_REL_TOL:
        return None
    return _unoriented_direction(vector)


def _pattern_kind(pattern) -> str:
    if hasattr(pattern, "diameter") and hasattr(pattern, "center"):
        return "bolt_circle"
    if hasattr(pattern, "row_pitch"):
        return "grid"
    return "linear"


def _default_linear_direction(members):
    """The declared linear default, derived from sorted materialised members."""
    if len(members) < 2:
        return None
    # Two linear scans find the endpoints of a collinear set without the quadratic
    # farthest-pair search.  Unlike lexicographic endpoints, this remains stable when
    # recogniser-accepted STEP noise perturbs the nominally constant cross-axis coordinate.
    anchor = members[0]

    def distance_squared(a, b):
        return sum((b[index] - a[index]) ** 2 for index in range(3))

    start = max(members, key=lambda point: distance_squared(anchor, point))
    end = max(members, key=lambda point: distance_squared(start, point))
    return tuple(end[i] - start[i] for i in range(3))


def _pattern_key(pattern) -> tuple:
    raw_members = _members(pattern, rounded=False)
    members = tuple(sorted(_point(point) for point in raw_members))
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
        direction = _default_linear_direction(raw_members)
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
    center = None
    if kind == "bolt_circle":
        raw_center = getattr(pattern, "center", None) if recognised is not None else None
        center_point = list(_point(pattern.frame.origin if raw_center is None else raw_center))
        if spec[3]:
            center_point["xyz".index(spec[0])] = 0.0
        center = (center_point[0], center_point[1], center_point[2])
    return (
        kind,
        spec,
        members,
        center,
        None if bcd is None else _rounded(bcd),
        None if pitch is None else _rounded(pitch),
        None if grid is None else tuple(_rounded(value) for value in grid),
        _planar_direction(direction, spec[0]) if kind == "linear" else None,
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
    if key[3] is not None:
        key[3] = _tool_center_members(pattern, (key[3],), depth)[0]
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


def _member_coaxial_with_turned_profile(feature, member, turned_axis_centers) -> bool:
    axis = feature.frame.axis
    perpendicular = tuple(i for i, letter in enumerate("xyz") if letter != axis)
    center = turned_axis_centers.get(axis)
    if center is None:
        return False
    return all(
        abs(center[offset] - member[index]) <= 1e-3 for offset, index in enumerate(perpendicular)
    )


def _coaxial_with_turned_profile(feature, turned_axis_centers) -> bool:
    return all(
        _member_coaxial_with_turned_profile(feature, member, turned_axis_centers)
        for member in _members(feature)
    )


def _parameter_ids(
    feature, *, member_count: int | None = None, turned_axis_centers
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
        if _coaxial_with_turned_profile(feature, turned_axis_centers):
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


def _evidence_parameter(parameter: str) -> str:
    if parameter in {"bore.through", "grouping.count"}:
        return "bore.diameter"
    if ".centerline." in parameter:
        # The physical ledger distinguishes a turned-axis location from an ordinary
        # numeric ordinate, but the compiler deliberately keeps the canonical Y/Z
        # measurement identities.  Join both placed drops and authored omissions back to
        # those compiler-owned ids instead of inventing a second suppression vocabulary.
        return parameter.replace(".centerline.", ".")
    if parameter.startswith(("location.location.", "location_pattern.location.")):
        # X/Y are independent physical critique requirements, while ADR 0016 / #883 keeps
        # their authored addressability as one feature-level location unit.
        return parameter.rsplit(".", 1)[0]
    return parameter


def _location_members(feature, parameter: str):
    return _members(feature)


@dataclass
class _HoleEvidence:
    placed: set[tuple[object, str]]
    dropped: set[tuple[object, str]]
    requirement_counts: dict[tuple[object, str], set[int]]
    locations: dict[tuple[object, str], set[tuple[float, float, float]]]
    centers: dict[object, set[tuple[tuple[float, float, float], str]]]
    representations: dict[tuple[object, str], set[tuple[str, str]]]
    unmarked_representations: set[tuple[object, str]]


def _normalised_location(feature, point) -> tuple[float, float, float]:
    normalized = list(_point(point))
    hole = getattr(feature, "member", feature)
    if hole.through:
        normalized["xyz".index(feature.frame.axis)] = 0.0
    return (normalized[0], normalized[1], normalized[2])


def _index_hole_evidence(registry) -> _HoleEvidence:
    placed = set()
    requirement_counts: dict[tuple[object, str], set[int]] = defaultdict(set)
    locations: dict[tuple[object, str], set[tuple[float, float, float]]] = defaultdict(set)
    centers: dict[object, set[tuple[tuple[float, float, float], str]]] = defaultdict(set)
    representations: dict[tuple[object, str], set[tuple[str, str]]] = defaultdict(set)
    unmarked_representations: set[tuple[object, str]] = set()
    for name in registry.names():
        annotation = registry.named(name)
        representation = getattr(annotation, "hole_representation", None)
        representation_reason = getattr(annotation, "hole_representation_reason", None)
        feature_representations: dict[object, set[tuple[str, str]]] = defaultdict(set)
        for feature, feature_representation, reason in getattr(
            annotation, "covers_hole_representations_by_feature", ()
        ):
            feature_representations[feature].add((str(feature_representation), str(reason)))
        requirement_representations: dict[tuple[object, str], set[tuple[str, str]]] = defaultdict(
            set
        )
        for feature, parameter, feature_representation, reason in getattr(
            annotation, "covers_hole_representations_by_requirement", ()
        ):
            requirement_representations[(feature, parameter)].add(
                (str(feature_representation), str(reason))
            )

        def record_representation(feature, parameter):
            if (feature, parameter) in requirement_representations:
                representations[(feature, parameter)].update(
                    requirement_representations[(feature, parameter)]
                )
            elif feature in feature_representations:
                representations[(feature, parameter)].update(feature_representations[feature])
            elif representation is not None and representation_reason is not None:
                representations[(feature, parameter)].add(
                    (str(representation), str(representation_reason))
                )
            else:
                # An ordinary feature annotation is still contributing semantic
                # evidence. Keep that absence explicit: if another owner in the same
                # recognised physical source was replaced by a table, the outcome is a
                # mixed representation and no scalar transaction winner is truthful.
                unmarked_representations.add((feature, parameter))

        measurements = tuple(registry.measurement_of(name))
        diameter_features = set()
        for measurement in measurements:
            feature = getattr(measurement, "feature", None)
            parameter = getattr(measurement, "parameter", None)
            if feature is None or parameter is None:
                continue
            placed.add((feature, parameter))
            record_representation(feature, parameter)
            if parameter == "bore.diameter":
                diameter_features.add(feature)
        for feature, requirement, count in getattr(
            annotation, "covers_hole_requirements_by_feature", ()
        ):
            requirement_counts[(feature, requirement)].add(int(count))
            record_representation(feature, requirement)
        for feature in diameter_features:
            for requirement in getattr(annotation, "covers_hole_requirements", ()):
                requirement_counts[(feature, requirement)].add(1)
                record_representation(feature, requirement)
            requirement_counts[(feature, "grouping.count")].add(
                int(getattr(annotation, "covers_count", 1) or 1)
            )
            record_representation(feature, "grouping.count")
        for fact in getattr(annotation, "covers_hole_locations", ()):
            if len(fact) == 3:
                feature, parameter, point = fact
            else:  # compatibility with legacy/external structured facts
                measurement, point = fact
                feature = getattr(measurement, "feature", None)
                parameter = getattr(measurement, "parameter", None)
            if getattr(feature, "kind", None) in {"hole", "pattern"} and parameter is not None:
                locations[(feature, parameter)].add(_normalised_location(feature, point))
                record_representation(feature, parameter)
        for feature, point, view in getattr(annotation, "covers_hole_centers", ()):
            if getattr(feature, "kind", None) in {"hole", "pattern"}:
                centers[feature].add((_normalised_location(feature, point), view))
    dropped = {
        (feature, parameter)
        for issue in registry.issues
        for measurement in getattr(issue, "measurement_ids", ())
        for feature, parameter in (
            (getattr(measurement, "feature", None), getattr(measurement, "parameter", None)),
        )
        if feature is not None and parameter is not None
    }
    dropped.update(
        (feature, parameter)
        for issue in registry.issues
        for feature, parameter in getattr(issue, "hole_requirement_ids", ())
    )
    return _HoleEvidence(
        placed,
        dropped,
        requirement_counts,
        locations,
        centers,
        representations,
        unmarked_representations,
    )


def _placed_representation(evidence, features, parameter, state):
    """The transactional representation that supplied a placed requirement, if any."""
    if state != "placed":
        return (None, None)
    if any((feature, parameter) in evidence.unmarked_representations for feature in features):
        return (None, None)
    markers = {
        marker
        for feature in features
        for marker in evidence.representations.get((feature, parameter), ())
    }
    if len(markers) != 1:
        return (None, None)
    return next(iter(markers))


def _structured_locations_placed(evidence, features, parameter: str, turned_axis_centers) -> bool:
    if parameter.startswith("location_pattern.location."):
        for feature in features:
            if getattr(feature, "pattern", None) == "bolt_circle":
                valid = {_normalised_location(feature, feature.frame.origin)}
            else:
                # Linear/grid absolute location is compiled from one member nearest the
                # datum. Any member is a truthful anchor because pitch/lattice facts locate
                # the rest; requiring every member would mistake one pattern-location
                # dimension for N independently addressable locations (#883).
                valid = {_normalised_location(feature, point) for point in _members(feature)}
            if not valid.intersection(evidence.locations.get((feature, parameter), ())):
                return False
        return bool(features)
    expected = {
        (feature, point) for feature in features for point in _location_members(feature, parameter)
    }
    covered = {
        (feature, point)
        for feature in features
        for point in evidence.locations.get((feature, parameter), ())
    }
    for feature in features:
        if getattr(feature, "kind", None) != "hole":
            continue
        for point, view in evidence.centers.get(feature, ()):
            if view != {"x": "side", "y": "front", "z": "plan"}[feature.frame.axis]:
                continue
            if _member_coaxial_with_turned_profile(feature, point, turned_axis_centers):
                covered.add((feature, point))
    return bool(expected) and expected <= covered


def _synthetic_placed(evidence, features, parameter: str, member_count: int) -> bool:
    if parameter == "bore.through":
        return any((feature, parameter) in evidence.requirement_counts for feature in features)
    if parameter != "grouping.count":
        return False
    expected = {feature: len(_members(feature)) for feature in features}
    return sum(expected.values()) == member_count and all(
        count in evidence.requirement_counts.get((feature, parameter), ())
        for feature, count in expected.items()
    )


def _state(features, parameter, *, member_count, evidence_index, suppressed, turned_axis_centers):
    evidence_parameter = _evidence_parameter(parameter)
    if parameter.startswith(
        ("location.location.", "location_pattern.location.", "location_off_axis.")
    ):
        if _structured_locations_placed(evidence_index, features, parameter, turned_axis_centers):
            return "placed"
    elif parameter in {"bore.through", "grouping.count"}:
        if _synthetic_placed(evidence_index, features, parameter, member_count):
            return "placed"
    else:
        per_feature = [
            (feature, evidence_parameter) in evidence_index.placed for feature in features
        ]
        # A common machining specification needs one truthful statement; a location
        # requirement needs every separately declared physical member tied to the mark.
        if all(per_feature) if "location" in parameter else any(per_feature):
            return "placed"
    if all((feature, evidence_parameter) in suppressed for feature in features):
        return "suppressed"
    drop_parameter = (
        parameter
        if parameter.startswith(("location.location.", "location_pattern.location."))
        else evidence_parameter
    )
    if any((feature, drop_parameter) in evidence_index.dropped for feature in features):
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

    pattern_member_ids = {
        id(hole) for pattern in recognition.hole_patterns for hole in pattern.holes
    }
    loose_groups: dict[tuple, list] = defaultdict(list)
    for hole in recognition.holes:
        if id(hole) not in pattern_member_ids:
            # HoleSpec's signed axis is part of machining identity. Keep opposite-face
            # and distinct oblique bores in separate source groups even when every printed
            # size matches. Use recognition's 6 dp identity rather than critique's 3 dp
            # geometry normalization.
            loose_groups[(_recognised_spec(hole), HoleSpec.from_hole(hole).axis)].append(hole)

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
    owners_by_exact_member: dict[tuple, list] = defaultdict(list)
    for feature in features:
        feature_kind = getattr(feature, "kind", None)
        if feature_kind == "hole":
            spec = _feature_spec(feature)
            members = _members(feature)
            ir_by_key[(feature_kind, (spec, members))].append(feature)
            for member in members:
                owners_by_exact_member[(spec, member)].append(feature)
        elif feature_kind == "pattern":
            pattern_key = _pattern_key(feature)
            ir_by_key[("hole_pattern", pattern_key)].append(feature)
            _pattern_kind_name, spec, members = pattern_key[:3]
            for member in members:
                owners_by_exact_member[(spec, member)].append(feature)

    def owner_ids_at(spec, members):
        return {
            id(feature)
            for member in members
            for feature in owners_by_exact_member.get((spec, member), ())
        }

    hole_features = tuple(
        feature for feature in features if getattr(feature, "kind", None) == "hole"
    )
    hole_features_by_spec: dict[tuple, list] = defaultdict(list)
    for feature in hole_features:
        hole_features_by_spec[_feature_spec(feature)].append(feature)

    # Map declared owners back to every physical source whose exact opening members they
    # touch. One owner may cover several members of one source, but touching two distinct
    # recognition-owned sources is ambiguous even when only one source would otherwise
    # propose it as an exact match.
    owner_source_indices: dict[int, set[int]] = defaultdict(set)
    for index, (kind, _source, key, _member_count) in enumerate(sources):
        if kind == "hole":
            spec, members = key
        else:
            _pattern_kind_name, spec, members = key[:3]
        for member in members:
            for feature in owners_by_exact_member.get((spec, member), ()):
                owner_source_indices[id(feature)].add(index)
    ambiguous_owner_ids = {
        feature_id
        for feature_id, source_indices in owner_source_indices.items()
        if len(source_indices) > 1
    }
    exact_overlap_feature_ids = set(owner_source_indices)

    # Establish exact correspondences first, then solve the residual projected fallback.
    # Object-backed blind declarations may retain a cutter centre while recognition owns
    # its opening. Projection is safe only when, after exact owners are consumed, one
    # remaining source has one remaining feature. This is a global bijection rather than a
    # per-source uniqueness guess: an exact declaration on one face can disambiguate the
    # valid tool-centred declaration on the opposed face, while two projected-only owners
    # remain unverifiable.
    exact_proposals: list[tuple] = []
    exact_evidence: list[bool] = []
    for kind, _source, key, _member_count in sources:
        if kind == "hole":
            source_spec, source_members = key
            spec_features = hole_features_by_spec.get(source_spec, ())
            partitions, evidence = _hole_partitions(
                source_spec,
                source_members,
                spec_features,
            )
            overlapping = owner_ids_at(source_spec, source_members)
            candidates = partitions[0] if len(partitions) == 1 else ()
            if candidates:
                candidate_ids = {id(feature) for feature in candidates}
                if overlapping != candidate_ids or candidate_ids & ambiguous_owner_ids:
                    candidates = ()
            evidence = evidence or bool(overlapping)
        else:
            direct = tuple(ir_by_key.get((kind, key), ()))
            _pattern_kind_name, spec, members = key[:3]
            # A shortened or otherwise partial declared pattern has a different full key,
            # but its exact opening members are still contradictory ownership evidence.
            # Index those sites once so dense inventories remain proportional to their
            # member incidence rather than rescanning every declared pattern per source.
            overlapping = owner_ids_at(spec, members)
            evidence = bool(overlapping)
            candidates = (
                direct
                if len(direct) == 1
                and overlapping == {id(direct[0])}
                and id(direct[0]) not in ambiguous_owner_ids
                else ()
            )
        exact_proposals.append(candidates)
        exact_evidence.append(evidence)

    exact_claims = Counter(id(feature) for proposal in exact_proposals for feature in proposal)
    matches_by_source = [
        proposal if all(exact_claims[id(feature)] == 1 for feature in proposal) else ()
        for proposal in exact_proposals
    ]
    used_feature_ids = {id(feature) for matches in matches_by_source for feature in matches}
    # Exact owners are reserved from the axial-coordinate fallback. This spans loose holes
    # and patterns: one declaration cannot be an exact/partial claim on one source and
    # simultaneously certify another source at its projected cutter centre.

    residual_proposals: dict[int, tuple] = {}
    for index, (kind, source, key, _member_count) in enumerate(sources):
        if matches_by_source[index]:
            continue
        # A partial, duplicate, or multiply-claimed exact owner is contradictory
        # evidence, not permission to retry the same physical source at its cutter
        # centre.  Fail closed before the object-backed fallback.
        if exact_evidence[index]:
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
            partition_result = (
                _hole_partitions(source_spec, expected_centres, candidate_features)
                if expected_centres
                else ((), False)
            )
            partitions, _evidence = partition_result
            candidates = partitions[0] if len(partitions) == 1 else ()
            if candidates and owner_ids_at(source_spec, expected_centres) != {
                id(feature) for feature in candidates
            }:
                candidates = ()
        else:
            projected_key = _tool_center_pattern_key(source)
            direct = tuple(
                feature
                for feature in ir_by_key.get((kind, projected_key), ())
                if id(feature) not in used_feature_ids
                and id(feature) not in exact_overlap_feature_ids
            )
            _pattern_kind_name, spec, members = projected_key[:3]
            overlapping = owner_ids_at(spec, members)
            candidates = direct if len(direct) == 1 and overlapping == {id(direct[0])} else ()
        if candidates:
            residual_proposals[index] = candidates

    residual_claims = Counter(
        id(feature) for proposal in residual_proposals.values() for feature in proposal
    )
    for index, proposal in residual_proposals.items():
        if all(residual_claims[id(feature)] == 1 for feature in proposal):
            matches_by_source[index] = proposal

    evidence_index = _index_hole_evidence(registry)
    turned_axis_centers = {
        axis: _recognised_turned_axis_center(recognition, axis) for axis in "xyz"
    }
    suppressed = {
        (omission.feature, omission.parameter_id)
        for omission in omissions
        if omission.feature is not None and omission.authored
    }
    outcomes = []
    for index, (kind, source, _key, member_count) in enumerate(sources):
        matched = matches_by_source[index]
        representative = matched[0] if matched else None
        parameters = (
            _parameter_ids(
                representative,
                member_count=member_count,
                turned_axis_centers=turned_axis_centers,
            )
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
        for parameter in parameters:
            state = _state(
                matched,
                parameter,
                member_count=member_count,
                evidence_index=evidence_index,
                suppressed=suppressed,
                turned_axis_centers=turned_axis_centers,
            )
            representation, representation_reason = _placed_representation(
                evidence_index, matched, parameter, state
            )
            outcomes.append(
                HoleRequirementOutcome(
                    kind,
                    at,
                    member_count,
                    parameter,
                    state,
                    representation=representation,
                    representation_reason=representation_reason,
                )
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
