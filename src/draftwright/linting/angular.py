"""Independent checks of angular claims and their visible arc geometry."""

from __future__ import annotations

import re
from dataclasses import dataclass
from math import atan2, degrees, dist, fsum, hypot, isfinite, pi
from statistics import median
from types import SimpleNamespace

from build123d import GeomType

from draftwright.linting.issues import LintIssue
from draftwright.profile_angles import (
    ProfileAngle,
    profile_angle_repetitions,
    profile_angle_requirements,
)


def is_angular_label(label: str) -> bool:
    """Whether the displayed claim carries a degree marker, including authored ``deg``."""
    return any(mark in label.lower() for mark in ("°", "deg"))


def _has_witness_ink(edges, vertices, vertex, ray, radius, direction=1):
    """Require a visible tip at the arc and a radial extension beyond it."""
    length = hypot(*ray)
    unit = (ray[0] / length, ray[1] / length)

    def coordinates(point):
        x, y = point.X - vertex[0], point.Y - vertex[1]
        return x * unit[0] + y * unit[1], x * unit[1] - y * unit[0]

    tip = any(
        abs(along - radius) <= 0.01 and abs(across) <= 0.01
        for along, across in (coordinates(point.center()) for point in vertices)
    )
    boundaries = []
    for edge in edges:
        if edge.geom_type != GeomType.LINE:
            continue
        start, end = coordinates(edge.position_at(0)), coordinates(edge.position_at(1))
        lo, hi = sorted((start[0], end[0]))
        if (
            abs(start[1] - end[1]) <= 0.01
            and lo >= direction * length - 0.01
            and lo < radius - 0.01
            and hi > radius + 0.01
        ):
            boundaries.append((start[1], lo, hi))
    # A stroked extension has two parallel boundaries straddling the ray.
    # Inspect that geometry rather than assuming the default pen width.
    extension = any(
        abs(offset) <= 0.01
        or any(
            abs(offset + other) <= 0.01 and abs(lo - low) <= 0.01 and abs(hi - high) <= 0.01
            for other, low, high in boundaries
        )
        for offset, lo, hi in boundaries
    )
    return tip and extension


def lint_angular_geometry(item, label_value):
    """Compare degrees with the reference rays, then inspect the actual circular ink.

    This never reads the renderer's ``measured_angle`` as the expected answer.
    Correspondence between declared rays and physical part supports is a separate
    evidence obligation, not something a correct arc on the page proves.
    """
    label = getattr(item, "label", "")
    try:
        first, vertex, second = item.angular_points
        u = tuple(a - b for a, b in zip(first, vertex, strict=True))
        v = tuple(a - b for a, b in zip(second, vertex, strict=True))
        if len(u) != 2 or len(v) != 2 or not all(isfinite(c) for c in (*u, *v)):
            raise ValueError("angular witnesses must be finite page-plane points")
        if min(hypot(*u), hypot(*v)) <= 1e-9:
            raise ValueError("angular witnesses must define two nonzero rays")
        sector = getattr(item, "angular_sector", "minor")
        if sector not in ("minor", "opposite"):
            raise ValueError("angular witnesses have an unsupported selected sector")
        direction = -1 if sector == "opposite" else 1
        u, v = tuple(direction * c for c in u), tuple(direction * c for c in v)
        signed = atan2(u[0] * v[1] - u[1] * v[0], sum(a * b for a, b in zip(u, v, strict=True)))
        angle = abs(signed)
        if not 1e-9 < angle < pi - 1e-9:
            raise ValueError("angular witnesses have no nondegenerate minor sector")
    except (AttributeError, TypeError, ValueError) as exc:
        return [
            LintIssue(
                severity="error",
                code="angular_geometry_mismatch",
                message=f"Angle {label!r} has unusable reference geometry: {exc}",
            )
        ]

    findings = []
    actual = degrees(angle)
    nominal = re.search(r"[+-]?\d+(?:\.(\d+))?\s*(?:°|deg)", label, re.IGNORECASE)
    # Interpret the nominal at its displayed resolution. A fixed percentage
    # both missed false precise claims and rejected correctly rounded small angles.
    rounding = 0.5 * 10 ** -len(nominal.group(1) or "") if nominal else 0.0
    if (
        not is_angular_label(label)
        or label_value is None
        or not isfinite(label_value)
        or abs(label_value - actual) > rounding + 1e-8
    ):
        findings.append(
            LintIssue(
                severity="error",
                code="angular_label_vs_geometry",
                location=tuple(vertex),
                message=f"Angle {label!r} does not agree with its reference rays ({actual:.6g} degrees)",
            )
        )

    halves = set()
    radii = []
    escaped = False
    try:
        edges = item.edges()
        for edge in edges:
            if edge.geom_type != GeomType.CIRCLE:
                continue
            centre = edge.arc_center
            if hypot(centre.X - vertex[0], centre.Y - vertex[1]) > 0.01:
                continue
            midpoint = edge.position_at(0.5)
            ray = (midpoint.X - vertex[0], midpoint.Y - vertex[1])
            offset = atan2(u[0] * ray[1] - u[1] * ray[0], u[0] * ray[0] + u[1] * ray[1])
            offset = (offset * (1 if signed > 0 else -1)) % (2 * pi)
            if offset > angle + 1e-6:
                escaped = True
            else:
                halves.add(offset > angle / 2)
                radii.append(edge.radius)
        witness_ink = bool(radii) and all(
            _has_witness_ink(edges, item.vertices(), vertex, ray, median(radii), direction)
            for ray in (u, v)
        )
    except (AttributeError, TypeError, ValueError, RuntimeError) as exc:
        findings.append(
            LintIssue(
                severity="error",
                code="angular_geometry_mismatch",
                location=tuple(vertex),
                message=f"Angle {label!r} has unreadable circular ink: {exc}",
            )
        )
        return findings
    if escaped or halves != {False, True} or not witness_ink:
        findings.append(
            LintIssue(
                severity="error",
                code="angular_geometry_mismatch",
                location=tuple(vertex),
                message=f"Angle {label!r} lacks circular shafts, arrow tips or extension lines "
                "at both rays of its selected reference sector",
            )
        )
    return findings


def _difference(first, second):
    return tuple(a - b for a, b in zip(first, second, strict=True))


def _cross(first, second):
    return (
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    )


def _dot(first, second):
    return fsum(a * b for a, b in zip(first, second, strict=True))


def _physical_corner(evidence, requirement):
    """Reconstruct the corner from two exact borrowed edges, independently of IR.

    The issued profile establishes adjacency and orientation. Actual edge endpoints
    establish the intersection and finite witnesses; neither the adapter's vertex
    nor the annotation's measured-angle metadata supplies the expected answer.
    """
    source = requirement.source
    supports = source.profile.supports
    first_index, second_index = requirement.first_index, requirement.second_index
    next_index = (first_index + 1) % len(supports)
    rounded = supports[next_index].kind == "arc"
    if second_index != (next_index + int(rounded)) % len(supports):
        raise ValueError("the bound supports are not adjacent profile sides")
    physical = []
    for index in (first_index, second_index):
        support = supports[index]
        if support.kind != "line":
            raise ValueError("the bound angular support is not a straight line")
        edge = evidence.profile_edge(source, index)
        if edge.geom_type != GeomType.LINE:
            raise ValueError("the borrowed angular support is not a straight edge")
        endpoints = tuple(tuple(edge.position_at(t)) for t in (0, 1))
        if dist(endpoints[0], support.start) > dist(endpoints[1], support.start):
            endpoints = endpoints[::-1]
        if dist(endpoints[0], support.start) > 1e-6 or dist(endpoints[1], support.end) > 1e-6:
            raise ValueError("the issued support no longer agrees with its physical edge")
        physical.append(endpoints)
    (a, b), (c, d) = physical
    u, v = _difference(b, a), _difference(d, c)
    normal = _cross(u, v)
    denominator = _dot(normal, normal)
    if denominator <= 1e-16:
        raise ValueError("the physical supports have no unique angular vertex")
    station = _dot(_cross(_difference(c, a), v), normal) / denominator
    vertex = tuple(p + station * direction for p, direction in zip(a, u, strict=True))
    residual = _cross(_difference(vertex, c), v)
    if hypot(*residual) / hypot(*v) > 1e-6:
        raise ValueError("the physical supports do not intersect in one plane")
    axis = max(range(3), key=lambda index: abs(normal[index]))
    if any(abs(value) / hypot(*normal) > 1e-8 for i, value in enumerate(normal) if i != axis):
        raise ValueError("the physical angle has no principal true-angle view")
    witnesses = (b, c) if rounded else (a, d)
    return ("side", "front", "plan")[axis], (witnesses[0], vertex, witnesses[1]), physical


def _on_segment(point, endpoints):
    first, second = endpoints
    direction = _difference(second, first)
    length_squared = _dot(direction, direction)
    if length_squared <= 1e-16:
        return False
    station = _dot(_difference(point, first), direction) / length_squared
    nearest = tuple(p + station * u for p, u in zip(first, direction, strict=True))
    return -1e-8 <= station <= 1 + 1e-8 and dist(point, nearest) <= 1e-6


def _declared_physical_corner(owner, evidence, view=None, *, reference=None):
    """Check a declaration's model-space witnesses without inventing ownership.

    Only a unique issued face-profile corner establishes this bounded proof.
    The finite-edge check permits a witness part-way along a physical edge; a
    collinear point floating beyond that edge is not a supported declaration.
    """
    reference = reference if reference is not None else getattr(owner, "angular_reference", None)
    if reference is None:
        return None
    matches = []
    for face in evidence.faces:
        source = evidence.planar_outer_profile(face)
        profile = getattr(source, "profile", None)
        if profile is None:
            continue
        supports = profile.supports
        for index, support in enumerate(supports):
            if support.kind != "line":
                continue
            following = (index + 1) % len(supports)
            if supports[following].kind == "arc":
                following = (following + 1) % len(supports)
            if supports[following].kind != "line":
                continue
            pair = SimpleNamespace(source=source, first_index=index, second_index=following)
            try:
                actual_view, expected, edges = _physical_corner(evidence, pair)
            except (AttributeError, TypeError, ValueError, RuntimeError, IndexError):
                continue  # This profile cannot establish the declared corner.
            if (view is not None and actual_view != view) or dist(
                reference.vertex, expected[1]
            ) > 1e-6:
                continue
            if any(
                _on_segment(reference.first, first) and _on_segment(reference.second, second)
                for first, second in (edges, edges[::-1])
            ):
                matches.append(
                    (pair, actual_view, (reference.first, reference.vertex, reference.second))
                )
    return matches[0] if len(matches) == 1 else None


def _angular_references(owner):
    if getattr(owner, "angular_reference", None) is None:
        return ()
    parameters = owner.parameters() if callable(getattr(owner, "parameters", None)) else ()
    references = tuple(
        (parameter.parameter_id, parameter.angular_reference)
        for parameter in parameters
        if getattr(parameter, "angular_reference", None) is not None
    )
    return references or (("included.angle", owner.angular_reference),)


def _corner_key(requirement):
    return id(requirement.source), requirement.first_index, requirement.second_index


def _projected_angle(points):
    first, vertex, second = points
    u = tuple(a - b for a, b in zip(first, vertex, strict=True))
    v = tuple(a - b for a, b in zip(second, vertex, strict=True))
    return degrees(
        abs(atan2(u[0] * v[1] - u[1] * v[0], sum(a * b for a, b in zip(u, v, strict=True))))
    )


def lint_angular_supports(items, *, registry=None, evidence=None, ownership=None, to_page=None):
    """Check every claimed corner against physical supports, including quantity marks.

    A quantity mark shows one representative but must account for distinct, equal
    physical corners. Detected groups additionally need the provider-profile
    repetition proof; authored groups must name one face's actual corners.
    """
    names = (
        {} if registry is None else {id(registry.named(name)): name for name in registry.names()}
    )
    findings = []
    repetitions = None
    for item in items:
        if getattr(item, "measured_angle", None) is None:
            continue
        name = names.get(id(item))
        owner = registry.feature_of(name) if name is not None else None
        references = dict(_angular_references(owner))
        claims = registry.measurement_of(name) if name is not None else ()
        parameters = tuple(claim.parameter for claim in claims)
        if not claims and len(references) == 1:
            parameters = tuple(references)
        if (
            not parameters
            or len(set(parameters)) != len(parameters)
            or any(claim.feature is not owner for claim in claims)
            or any(parameter not in references for parameter in parameters)
            or evidence is None
            or to_page is None
        ):
            findings.append(
                LintIssue(
                    severity="warning",
                    code="angular_support_unverifiable",
                    message=f"Angle {item.label!r}: no unique same-run physical support binding",
                )
            )
            continue
        try:
            expected_members = []
            pairs = []
            for parameter in parameters:
                reference = references[parameter]
                if ownership is None:
                    declared = _declared_physical_corner(
                        owner,
                        evidence,
                        registry.view_of(name),
                        reference=reference,
                    )
                    if declared is None:
                        raise LookupError("no unique declared physical corner")
                    pair, view, expected = declared
                else:
                    bindings = [
                        binding
                        for binding in ownership.profile_angles
                        if ownership.evidence is evidence
                        and binding.feature is owner
                        and binding.parameter_id == parameter
                    ]
                    if len(bindings) != 1:
                        raise LookupError("no unique same-run physical support binding")
                    pair = bindings[0].requirement
                    view, expected, _edges = _physical_corner(evidence, pair)
                if registry.view_of(name) != view:
                    raise ValueError(f"the physical supports require the {view} true-angle view")
                projected = tuple(tuple(to_page(view, *point)[:2]) for point in expected)
                declared_points = tuple(
                    tuple(to_page(view, *point)[:2])
                    for point in (reference.first, reference.vertex, reference.second)
                )
                if not any(
                    all(
                        dist(a, b) <= 0.01 for a, b in zip(declared_points, candidate, strict=True)
                    )
                    for candidate in (projected, projected[::-1])
                ):
                    raise ValueError(
                        "a member's declared witnesses differ from its physical supports"
                    )
                pairs.append(pair)
                expected_members.append(projected)
            actual = item.angular_points
            if not any(
                all(dist(a, b) <= 0.01 for a, b in zip(actual, candidate, strict=True))
                for expected in expected_members
                for candidate in (expected, expected[::-1])
            ):
                raise ValueError("the drawn vertex or witnesses differ from the physical supports")
            quantity = re.match(r"^\s*([1-9]\d*)\s*×\s*", item.label)
            if (int(quantity.group(1)) if quantity else 1) != len(expected_members):
                raise ValueError(
                    "the displayed quantity does not match the physical corner roster"
                )
            keys = {_corner_key(pair) for pair in pairs}
            if len(keys) != len(pairs) or len({id(pair.source) for pair in pairs}) != 1:
                raise ValueError("a quantity needs distinct corners on one physical face profile")
            if any(
                abs(_projected_angle(expected) - _projected_angle(actual)) > 1e-5
                for expected in expected_members
            ):
                raise ValueError("the represented physical corners have different angles")
            if len(pairs) > 1 and ownership is not None:
                if repetitions is None:
                    repetitions = profile_angle_repetitions(evidence)
                if not any(
                    keys == {_corner_key(member) for member in repetition.members}
                    for repetition in repetitions
                ):
                    raise ValueError("the quantity has no proved physical profile repetition")
        except LookupError as exc:
            findings.append(
                LintIssue(
                    severity="warning",
                    code="angular_support_unverifiable",
                    message=f"Angle {item.label!r}: {exc}",
                    measurement_ids=claims,
                )
            )
        except (AttributeError, TypeError, ValueError, RuntimeError, IndexError) as exc:
            findings.append(
                LintIssue(
                    severity="error",
                    code="angular_support_mismatch",
                    message=f"Angle {item.label!r}: {exc}",
                    measurement_ids=claims,
                )
            )
    return findings


@dataclass(frozen=True)
class ProfileAngleOutcome:
    """One issued profile corner, even when its adapter or annotation is absent."""

    source_profile: ProfileAngle
    state: str
    features: tuple = ()
    parameter_id: str = "included.angle"


def profile_angle_requirement_outcomes(evidence, ownership, features, registry, omissions=()):
    """Count physical profile requirements independently of the final IR and ink.

    Conversion-time bindings establish detected ownership. Declarations can
    represent a unique physical corner without acquiring detected ownership.
    A lost detected owner is missing even if stale annotation provenance survives.
    """
    bindings = (
        ownership.profile_angles
        if ownership is not None and ownership.evidence is evidence
        else ()
    )
    declared = []
    if ownership is None and evidence is not None:
        for feature in features:
            for parameter_id, reference in _angular_references(feature):
                proof = _declared_physical_corner(feature, evidence, reference=reference)
                if proof is not None:
                    declared.append((feature, parameter_id, proof[0]))
    outcomes = []
    for requirement in profile_angle_requirements(evidence):
        matches = [
            binding
            for binding in bindings
            if binding.requirement.source is requirement.source
            and binding.requirement.first_index == requirement.first_index
            and binding.requirement.second_index == requirement.second_index
        ]
        owners = (matches[0].feature,) if len(matches) == 1 else ()
        parameter_id = matches[0].parameter_id if len(matches) == 1 else "included.angle"
        if ownership is None:
            representations = [
                (feature, parameter)
                for feature, parameter, pair in declared
                if pair.source is requirement.source
                and pair.first_index == requirement.first_index
                and pair.second_index == requirement.second_index
            ]
            owners = (representations[0][0],) if len(representations) == 1 else ()
            if len(representations) == 1:
                parameter_id = representations[0][1]
        final = tuple(owner for owner in owners if any(owner is feature for feature in features))
        state = "unverifiable"
        if owners and not final:
            state = "missing"
        elif final:
            (owner,) = final

            def matches_id(identity):
                return identity.feature is owner and identity.parameter == parameter_id

            if any(
                hasattr(registry.named(name), "angular_points")
                and any(matches_id(identity) for identity in registry.measurement_of(name))
                for name in registry.names()
            ):
                state = "placed"
            elif any(
                matches_id(identity)
                for name in registry.names()
                for identity in registry.satisfaction_of(name)
            ):
                state = "satisfied_by_structured_note"
            elif any(
                omission.feature is owner
                and omission.parameter_id == parameter_id
                and omission.authored
                for omission in omissions
            ):
                state = "suppressed"
            elif any(
                matches_id(identity)
                for issue in registry.issues
                if issue.code == "angular_dimension_dropped"
                for identity in issue.measurement_ids
            ):
                state = "dropped"
            else:
                state = "missing"
        outcomes.append(ProfileAngleOutcome(requirement, state, final, parameter_id))
    return outcomes


def lint_profile_angle_coverage(evidence, ownership, features, registry, omissions=()):
    """Expose missing or unbound profile angles, retaining authored omissions."""
    findings = []
    for outcome in profile_angle_requirement_outcomes(
        evidence, ownership, features, registry, omissions
    ):
        if outcome.state in {"placed", "satisfied_by_structured_note", "dropped"}:
            continue
        requirement = outcome.source_profile
        findings.append(
            LintIssue(
                severity="info" if outcome.state == "suppressed" else "warning",
                code=f"angular_requirement_{outcome.state}",
                message=(
                    f"Outer-profile angle at {requirement.vertex}, supports "
                    f"{requirement.first_index}/{requirement.second_index}: {outcome.state}"
                ),
            )
        )
    return findings
