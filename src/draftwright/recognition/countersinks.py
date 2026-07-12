"""countersinks — countersink recognition for drilled holes (ADR 0007, #558).

``recognise_countersinks`` recovers the countersinks a plain hole recogniser reports as
mere openings: an internal **cone** that flares from a drilled bore (the minor circle)
out to a larger opening (the major circle), coaxial with a **cylinder** of the drill
radius. That is exactly a countersink for a flat-head screw. Keying on it — a cone with
two distinct-radius circular edges whose smaller radius matches a coaxial drilled
cylinder — excludes drill-point cones (a single circle + apex, not flared) and external
edge chamfers (no coaxial bore).

Geometry-mirrored with ``pzfreo/build123d-mcp``'s ``recognise_countersinks`` so the pair
can converge in the shared ``b123d-recognisers`` package (ADR 0013). Bottom of the
recognition DAG: depends only on build123d/OCP.

Heuristic limits (``recognised`` tier): an edge-break / deburr / lead-in chamfer at a hole
mouth is geometrically a shallow countersink, so a **flare-ratio floor** (``_MIN_MAJOR_RATIO``)
excludes it — a screw seat flares to roughly twice the bore, an edge break barely widens
it; a near-flat cone above ``_MAX_INCLUDED_ANGLE`` (a draft / relief) is excluded; a
countersink clipped by another feature (its edges no longer full circles) is missed.

Known limitations (edge geometries; the common one-face countersink is exact):

- an **external transition chamfer** between two coaxial cylinders (e.g. a stepped
  shaft) presents a flared cone coaxial with a cylinder and can register in the
  standalone recogniser — harmless in draftwright drawings (``_csink_for_hole`` only
  attaches a countersink to a recognised internal bore, and a shaft has none); an
  internal/external face-orientation check would exclude it (deferred to the shared
  ``b123d-recognisers`` extraction);
- a **DIN 332 lathe centre-drill** (a 60° cone flaring from a small pilot bore) is
  geometrically near-indistinguishable from a small countersink and can register;
- a through hole countersunk on **both** faces yields two coaxial countersinks, but the
  single ``HoleRecord.csink`` slot records only one — so it under-reports, and (since
  ``HoleSpec`` keys the csink on size only) a both-face hole can group with a one-face
  hole of the same size. A two-slot csink model would be needed to draw both seats.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from build123d import GeomType

from draftwright.recognition._record import Record

_TOL = 0.05  # mm — dimension match tolerance (matches the other feature matchers)
_COAXIAL_TOL = 0.1  # mm — how far the opening may sit off the drill's axis line
# Real countersinks are ≤120° included (60/82/90/100/120 standards); a near-flat cone is
# a draft/relief/washer face, not a countersink. 160° keeps every real countersink with
# margin while excluding drafts (~176–178° included).
_MAX_INCLUDED_ANGLE = 160.0
# A screw seat flares to roughly twice the bore (a flat-head sits in it); an edge-break /
# deburr / lead-in chamfer on a hole mouth is the same *shape* but barely wider than the
# bore. Require the major to reach this multiple of the drill radius to exclude those —
# else every chamfered hole mouth would be called out as a countersink (#558 review).
_MIN_MAJOR_RATIO = 1.5


@dataclass(frozen=True)
class CounterSink(Record):
    """A recognised countersink. ``axis`` points from the wide opening into the part;
    ``location`` is the opening (major-circle) centre; ``major_diameter`` the outer rim,
    ``drill_diameter`` the bore it sits on; ``included_angle`` the full cone angle
    (degrees); ``depth`` the axial cone depth. Fields mirror the shared-package record."""

    axis: tuple[float, float, float]
    location: tuple[float, float, float]
    major_diameter: float
    drill_diameter: float
    included_angle: float
    depth: float


def _parallel(a, b) -> bool:
    return bool(abs(a[0] * b[0] + a[1] * b[1] + a[2] * b[2]) > 1 - 1e-3)


def _dist_to_line(pt, line_pt, line_dir) -> float:
    v = (pt[0] - line_pt[0], pt[1] - line_pt[1], pt[2] - line_pt[2])
    t = v[0] * line_dir[0] + v[1] * line_dir[1] + v[2] * line_dir[2]
    perp = (v[0] - t * line_dir[0], v[1] - t * line_dir[1], v[2] - t * line_dir[2])
    return math.sqrt(perp[0] ** 2 + perp[1] ** 2 + perp[2] ** 2)


def recognise_countersinks(part) -> list[CounterSink]:
    """Recognise the countersinks of *part* (see module docstring). One
    :class:`CounterSink` per qualifying cone, sorted deterministically; empty when the
    part has none."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface

    cones = list(part.faces().filter_by(GeomType.CONE))
    if not cones:
        return []  # no cones → no countersinks; skip the cylinder scan (perf, #558 review)

    cyls = []
    for cy in part.faces().filter_by(GeomType.CYLINDER):
        ax = BRepAdaptor_Surface(cy.wrapped).Cylinder().Axis()
        p, d = ax.Location(), ax.Direction()
        cyls.append((cy.radius, (p.X(), p.Y(), p.Z()), (d.X(), d.Y(), d.Z())))

    out: list[CounterSink] = []
    for f in cones:
        circles = sorted(f.edges().filter_by(GeomType.CIRCLE), key=lambda e: e.radius)
        if len(circles) < 2:
            continue  # drill-point cone (one circle + apex) or degenerate
        minor_e, major_e = circles[0], circles[-1]
        minor_r, major_r = minor_e.radius, major_e.radius
        if major_r < _MIN_MAJOR_RATIO * minor_r:
            continue  # too little flare — an edge break / deburr, not a screw seat
        cone = BRepAdaptor_Surface(f.wrapped).Cone()
        included_angle = round(2 * abs(math.degrees(cone.SemiAngle())), 2)
        if included_angle > _MAX_INCLUDED_ANGLE:
            continue  # a near-flat cone is a draft/relief/washer face, not a countersink
        opening = major_e.arc_center
        opening_pt = (opening.X, opening.Y, opening.Z)
        mc = minor_e.arc_center
        minor_pt = (mc.X, mc.Y, mc.Z)
        # Axis points INTO the part: from the wide opening toward the drilled bore.
        # (Deterministic — don't trust OCP's cone-axis sign across constructions.)
        av = (
            minor_pt[0] - opening_pt[0],
            minor_pt[1] - opening_pt[1],
            minor_pt[2] - opening_pt[2],
        )
        alen = math.sqrt(av[0] ** 2 + av[1] ** 2 + av[2] ** 2) or 1.0
        axis = (av[0] / alen, av[1] / alen, av[2] / alen)
        # A countersink sits on a drilled bore: a coaxial cylinder of the minor radius.
        if not any(
            abs(r - minor_r) <= _TOL
            and _parallel(axis, ld)
            and _dist_to_line(opening_pt, lp, ld) <= _COAXIAL_TOL
            for r, lp, ld in cyls
        ):
            continue
        out.append(
            CounterSink(
                axis=(round(axis[0], 4), round(axis[1], 4), round(axis[2], 4)),
                location=tuple(round(v, 4) for v in opening_pt),
                major_diameter=round(2 * major_r, 4),
                drill_diameter=round(2 * minor_r, 4),
                included_angle=included_angle,
                depth=round(alen, 4),
            )
        )
    return sorted(out, key=lambda c: (c.location, c.major_diameter))
