"""Independent checks of angular claims and their visible arc geometry."""

from __future__ import annotations

import re
from math import atan2, degrees, hypot, isfinite, pi
from statistics import median

from build123d import GeomType

from draftwright.linting.issues import LintIssue


def is_angular_label(label: str) -> bool:
    """Whether the displayed claim carries a degree marker, including authored ``deg``."""
    return any(mark in label.lower() for mark in ("°", "deg"))


def _has_witness_ink(edges, vertices, vertex, ray, radius):
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
            and lo >= length - 0.01
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
            _has_witness_ink(edges, item.vertices(), vertex, ray, median(radii)) for ray in (u, v)
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


def lint_angular_supports(items):
    """Keep physical support assurance explicit until the provider contract exists.

    Quiddity #579 requests body-owned outer-profile supports. An authored vertex
    and two points cannot prove that such edges exist on the part, even if the
    resulting annotation is internally consistent. Do not rescan topology here.
    """
    return [
        LintIssue(
            severity="warning",
            code="angular_support_unverifiable",
            message=f"Angle {item.label!r}: correspondence of the declared rays to physical "
            "part supports is unavailable (Quiddity #579)",
        )
        for item in items
        if getattr(item, "measured_angle", None) is not None
    ]
