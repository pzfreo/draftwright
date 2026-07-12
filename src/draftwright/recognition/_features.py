"""features — geometric feature recognition for build123d parts (#87).

Vendored from ``build123d_drafting.features`` (ADR 0007): draftwright owns
feature recognition, helpers becomes the rendering library. This is the
source of truth from here on — the upstream copy is frozen and deprecated.
Imported via the package surface, :mod:`draftwright.recognition`.

Recognises drilled-hole and boss features from a solid's cylindrical faces:

    from draftwright.recognition import recognise_holes, recognise_bosses
    holes = recognise_holes(part)    # list[HoleRecord]
    bosses = recognise_bosses(part)  # list[BossRecord]

A *hole* is a contiguous coaxial stack of internal full cylinders — the
drilled bore plus optional counterbore and spotface steps — with its bottom
classified by probing the adjacent face (``through`` / ``flat`` /
``drill_point`` / ``unknown``).  A *boss* is an external full cylinder.
Cylinder patches spanning half a turn or less (fillets, rounds, slot end
caps) are never features, but a bore split by a slot or keyway still counts,
and a bore interrupted by a crossing hole is recombined into one feature.

Known approximations: a hole opening onto a slanted or curved surface is
located at the axial extreme of its lip, and its depth includes the lip
overhang; counterbores on the far side of a through hole's bore are not
reported.

This module also hosts the low-level cylinder analysis that
``make_drawing`` builds on (``analyse_cylinders``, ``full_cylinders``).
"""

import math
from dataclasses import dataclass, replace

from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAbs import (
    GeomAbs_Cone,
    GeomAbs_Cylinder,
    GeomAbs_Plane,
    GeomAbs_Sphere,
    GeomAbs_Torus,
)
from OCP.TopAbs import TopAbs_Orientation

from draftwright.recognition.countersinks import CounterSink, recognise_countersinks

# Cylinder patches around one axis spanning half a turn or less in total are
# not holes or bosses: quarter-turn patches are edge blends (fillets/rounds)
# and exactly-half-turn patches are the end caps of milled slots. Real bores
# keep more than half a turn even when a slot or keyway splits them.
_FULL_CYL_MIN_EXTENT = math.pi * 1.05

# Coaxial segments whose axial ranges meet within this gap belong to the same
# stack (a counterbore shoulder is an exact-touch); larger gaps are distinct
# features unless bridged by a shoulder chamfer/fillet or a crossing void
# (see _merge_stacks).
_STACK_GAP_TOL = 0.1

# A counterbore-like step shallower than this fraction of its diameter is a
# spotface (a facing cut, e.g. ø60×5), deeper is a counterbore (e.g. ø18×6).
_SPOTFACE_MAX_RATIO = 0.2


def analyse_cylinders(part):
    """Return (z_cyls, cross_cyls) from OCP cylindrical face analysis.

    Each entry is a dict with keys: diameter, axis (dominant axis letter),
    u_extent (the face's angular span in radians — partial spans are fillets),
    axis_xyz (a point on the cylinder axis), external (True when the face
    is outward-facing — a boss/OD; False for a bore), dir_xyz (unit axis
    direction with its dominant component positive), s_lo/s_hi (the patch's
    axial extent as coordinates along dir_xyz), solid_idx (index of the owning
    solid, keeping coaxial bores in different bodies distinct — see #68), and
    face (the source face).
    z_cyls: cylinders whose axis is approximately Z.
    cross_cyls: cylinders whose axis is approximately X or Y.
    """
    z_cyls: list[dict] = []
    cross_cyls: list[dict] = []
    # Attribute each face to its owning solid so coaxial bores in *different*
    # bodies of a multi-solid assembly are not grouped into one hole — which
    # would measure a depth across the gap between the bodies (#68). A single
    # solid yields one group, i.e. the historical single-body behaviour.
    solids = part.solids()
    faces_by_solid = (
        [(i, f) for i, s in enumerate(solids) for f in s.faces()]
        if solids
        else [(0, f) for f in part.faces()]
    )
    for solid_idx, face in faces_by_solid:
        surf = BRepAdaptor_Surface(face.wrapped)
        if surf.GetType() != GeomAbs_Cylinder:
            continue
        cyl = surf.Cylinder()
        r = cyl.Radius()
        d = cyl.Axis().Direction()
        ap = cyl.Axis().Location()
        comps = [("x", abs(d.X())), ("y", abs(d.Y())), ("z", abs(d.Z()))]
        ax = max(comps, key=lambda t: t[1])[0]
        # Canonical direction (dominant component positive) so coaxial faces
        # report comparable axial coordinates whichever way their frame points
        sign = 1.0 if {"x": d.X(), "y": d.Y(), "z": d.Z()}[ax] > 0 else -1.0
        dir_xyz = (sign * d.X(), sign * d.Y(), sign * d.Z())
        v0, v1 = surf.FirstVParameter(), surf.LastVParameter()
        # s(P) = P·dir for P = ap + v*d  →  s = ap·dir + sign*v
        s_ap = ap.X() * dir_xyz[0] + ap.Y() * dir_xyz[1] + ap.Z() * dir_xyz[2]
        s0, s1 = s_ap + sign * v0, s_ap + sign * v1
        rec = dict(
            diameter=round(r * 2, 2),
            axis=ax,
            solid_idx=solid_idx,
            u_extent=surf.LastUParameter() - surf.FirstUParameter(),
            axis_xyz=(ap.X(), ap.Y(), ap.Z()),
            dir_xyz=dir_xyz,
            s_lo=min(s0, s1),
            s_hi=max(s0, s1),
            face=face,
            # Outward material (boss/OD) vs bore: a right-handed cylinder's
            # natural normal points away from the axis, so FORWARD means
            # external — but mirroring makes the frame left-handed and flips
            # both, so compare against the frame handedness
            external=(face.wrapped.Orientation() == TopAbs_Orientation.TopAbs_FORWARD)
            == cyl.Position().Direct(),
        )
        (z_cyls if ax == "z" else cross_cyls).append(rec)
    return z_cyls, cross_cyls


def _line_key(c):
    """Coaxial-stack key: the owning solid plus the axis letter and the axis
    point projected onto the plane perpendicular to the axis direction (so it is
    position-independent along the axis, and exact for slanted axes too). The
    solid component keeps coaxial bores in different bodies of an assembly from
    grouping into one hole (#68)."""
    px, py, pz = c["axis_xyz"]
    dx, dy, dz = c["dir_xyz"]
    t = px * dx + py * dy + pz * dz
    return (
        c.get("solid_idx", 0),
        c["axis"],
        round(px - t * dx, 3),
        round(py - t * dy, 3),
        round(pz - t * dz, 3),
    )


def _cyl_group_key(c):
    """Cylinder patches of one hole/boss share an axis line and a diameter."""
    return (*_line_key(c), round(c["diameter"], 2))


def _merge_runs(items, key_fn):
    """Group *items* by *key_fn*, then split each group into runs of
    contiguous axial ranges (gap > _STACK_GAP_TOL starts a new run)."""
    by_key: dict = {}
    for item in items:
        by_key.setdefault(key_fn(item), []).append(item)
    runs = []
    for group in by_key.values():
        group.sort(key=lambda c: c["s_lo"])
        run, hi = [group[0]], group[0]["s_hi"]
        for c in group[1:]:
            if c["s_lo"] <= hi + _STACK_GAP_TOL:
                run.append(c)
                hi = max(hi, c["s_hi"])
            else:
                runs.append(run)
                run, hi = [c], c["s_hi"]
        runs.append(run)
    return runs


def full_cylinders(cyls):
    """The feature-relevant ("full") cylinder records within *cyls*.

    *cyls* is one of the two record lists returned by :func:`analyse_cylinders`
    (the z-axis list or the cross-axis list); the records are the dicts that
    function produces. The result keeps only the records that belong to a hole
    or boss: patches around one axis must total more than half a turn within
    one contiguous axial range, so fillet faces and slot end caps are excluded
    (even coaxial caps at different heights) but a bore split by a slot or
    keyway still counts.

    This is the patch-level filter shared with ``make_drawing``. For the
    higher-level inventory of dimensionable diameters use
    :func:`feature_diameters`, which is built from the recognised
    :func:`recognise_holes` / :func:`recognise_bosses` features instead. Public and
    stable for downstream consumers (e.g. ``draftwright``).
    """
    keep = []
    for run in _merge_runs(cyls, _cyl_group_key):
        if sum(c["u_extent"] for c in run) >= _FULL_CYL_MIN_EXTENT:
            keep.extend(run)
    return keep


# Internal alias retained for the in-module call sites.
_full_cyls = full_cylinders


# ---------------------------------------------------------------------------
# Hole / boss recognition
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CounterBore:
    """A counterbore or spotface step of a hole: its diameter and axial depth."""

    diameter: float
    depth: float


@dataclass(frozen=True)
class HoleRecord:
    """A drilled hole: the bore plus optional counterbore/spotface steps.

    ``axis`` is the drilling direction (unit vector pointing from the opening
    into the hole) and ``location`` the axis point at the opening surface.
    ``diameter``/``depth`` describe the bore itself — the narrowest segment of
    the stack — with ``depth`` measured from the top of the bore to the hole's
    deep end (a bottom relief groove counts, a drill point's cone does not).
    ``bottom`` is ``"through"``, ``"flat"``, ``"drill_point"``, or
    ``"unknown"`` when the adjacent geometry matches none of those.
    """

    axis: tuple
    location: tuple
    diameter: float
    depth: float
    bottom: str
    cbore: CounterBore | None = None
    spotface: CounterBore | None = None
    # A countersink (flat-head seat) coaxial with the bore, or None (#558). Composed
    # from the standalone :func:`recognise_countersinks` so the hole's spec/grouping and
    # the callout-width estimate all see it.
    csink: CounterSink | None = None


@dataclass(frozen=True)
class BossRecord:
    """An external cylindrical boss (including a turned part's OD).

    ``axis`` points from the base toward the free end, ``location`` is the
    axis point at the free end, and ``height`` the axial extent.
    """

    axis: tuple
    location: tuple
    diameter: float
    height: float


def _unit(v):
    """Normalise negative zeros out of a direction tuple."""
    return tuple(0.0 if c == 0 else c for c in v)


def _segments(cyls):
    """Collapse cylinder patches into segments: one per (axis line, diameter,
    contiguous axial range). Keyway-split patches of one bore merge; coaxial
    same-diameter holes from opposite faces stay separate."""
    return [
        dict(
            run[0],
            s_lo=min(p["s_lo"] for p in run),
            s_hi=max(p["s_hi"] for p in run),
            faces=[p["face"] for p in run],
        )
        for run in _merge_runs(cyls, _cyl_group_key)
    ]


def _axis_point(seg, s):
    """The 3D point on *seg*'s axis at axial coordinate *s*."""
    ax, ay, az = seg["axis_xyz"]
    dx, dy, dz = seg["dir_xyz"]
    s_ap = ax * dx + ay * dy + az * dz
    t = s - s_ap
    return (ax + t * dx, ay + t * dy, az + t * dz)


def _end_partners(seg, s_end, edge_faces, cache=None):
    """The faces beyond one axial end of *seg*: partners of edges that lie at
    that end. An opening edge on a slanted or curved surface dips away from
    the end plane (by the lip sagitta), so edges match within a margin — but
    stay well clear of the segment's other end.

    *cache* (optional) memoises the result per ``(seg, s_end)`` within one
    ``recognise_holes``/``recognise_bosses`` call — the same end is classified several
    times (``_merge_stacks`` plus the main loop), and each scan walks every
    face's edges. The seg is stored in the cached value so an ``is`` check
    rejects (and pins against) any ``id`` reuse."""
    if cache is not None:
        key = ("ep", id(seg), round(s_end, 9))
        hit = cache.get(key)
        if hit is not None and hit[0] is seg:
            return hit[1]
    dx, dy, dz = seg["dir_xyz"]
    margin = max(_STACK_GAP_TOL, min(0.45 * (seg["s_hi"] - seg["s_lo"]), 0.5 * seg["diameter"]))
    partners = []
    for face in seg["faces"]:
        for edge in face.edges():
            pts = [edge.center()] + [v.center() for v in edge.vertices()]
            if not all(abs(p.X * dx + p.Y * dy + p.Z * dz - s_end) <= margin for p in pts):
                continue
            for partner in edge_faces.get(edge, ()):
                if not any(partner.is_same(f) for f in seg["faces"]):
                    partners.append(partner)
    if cache is not None:
        cache[key] = (seg, partners)
    return partners


def _classify_end(seg, s_end, hi_end, edge_faces, cache=None):
    """Cached wrapper over :func:`_classify_end_uncached` (see *cache* there)."""
    if cache is None:
        return _classify_end_uncached(seg, s_end, hi_end, edge_faces)
    key = ("ce", id(seg), round(s_end, 9), hi_end)
    hit = cache.get(key)
    if hit is not None and hit[0] is seg:
        return hit[1]
    result = _classify_end_uncached(seg, s_end, hi_end, edge_faces, cache)
    cache[key] = (seg, result)
    return result


def _classify_end_uncached(seg, s_end, hi_end, edge_faces, cache=None):
    """Classify one axial end of a cylinder segment from the face beyond it.

    Returns ``"open"`` (the bore exits, or the boss's free end), ``"flat"``
    (closed by a plane facing back into the segment, or a boss's base),
    ``"drill_point"`` (a bore closed by a cone), or ``"unknown"``.

    Planes, cones, and tori are decisive; a curved wall (cylinder/sphere) is
    a weak signal — an exit for a bore, a base for a boss — that only counts
    when no decisive partner is present (a crossing port near a flat bottom
    must not outvote the bottom).

    An adjacent cone is read through the segment's internal/external context
    and its apex direction: for a bore, apex outward closes it (drill point)
    while apex inward widens it (an entry chamfer or countersink — open);
    for a boss the senses flip (apex outward is a chamfered free end, apex
    inward a base draft).  Tori follow the corner they round: one curling
    inward (major radius below the segment's) is a closed corner — a blind
    bore's bottom or a boss's base — and one flaring outward is an opening
    lip or a free end.
    """
    dx, dy, dz = seg["dir_xyz"]
    e_sign = 1.0 if hi_end else -1.0
    weak = None
    for partner in _end_partners(seg, s_end, edge_faces, cache):
        surf = BRepAdaptor_Surface(partner.wrapped)
        kind = surf.GetType()
        if kind == GeomAbs_Cone:
            cone = surf.Cone()
            apex = cone.Apex()
            apex_s = apex.X() * dx + apex.Y() * dy + apex.Z() * dz
            outward = (apex_s - s_end) * e_sign > 0
            if not seg["external"]:
                if outward:
                    # A deburr chamfer on a flat floor's rim is also an
                    # apex-outward cone — closed either way, but it has the
                    # floor plane right next to it where a true drill point
                    # has nothing beyond its apex.
                    for e2 in partner.edges():
                        for n in edge_faces.get(e2, ()):
                            if n.is_same(partner) or any(n.is_same(f) for f in seg["faces"]):
                                continue
                            n_surf = BRepAdaptor_Surface(n.wrapped)
                            if n_surf.GetType() != GeomAbs_Plane:
                                continue
                            nv = n.normal_at(n.center())
                            if abs(nv.X * dx + nv.Y * dy + nv.Z * dz) > 0.9:
                                return "flat"
                    return "drill_point"
                return "open"
            return "open" if outward else "flat"
        if kind == GeomAbs_Torus:
            curls_in = surf.Torus().MajorRadius() < seg["diameter"] / 2
            if not seg["external"]:
                return "flat" if curls_in else "open"
            return "open" if curls_in else "flat"
        if kind == GeomAbs_Plane:
            n = partner.normal_at(partner.center())
            dot = (n.X * dx + n.Y * dy + n.Z * dz) * e_sign
            if dot < -0.5:
                return "flat"
            if dot > 0.5:
                return "open"
        elif kind == GeomAbs_Sphere:
            # Convex (material inside the sphere): the bore exits through a
            # spherical surface. Concave (a ball-nose cavity): a closed
            # bottom — reported as "flat" (no rounded-bottom category).
            convex = (
                partner.wrapped.Orientation() == TopAbs_Orientation.TopAbs_FORWARD
            ) == surf.Sphere().Position().Direct()
            if not seg["external"]:
                weak = "open" if convex else "flat"
            else:
                weak = "flat" if convex else "open"
        elif kind == GeomAbs_Cylinder:
            weak = "open" if not seg["external"] else "flat"
    return weak or "unknown"


def _edge_face_map(part):
    """Map every edge of *part* to the faces that share it."""
    edge_faces: dict = {}
    for f in part.faces():
        for e in f.edges():
            edge_faces.setdefault(e, []).append(f)
    return edge_faces


def _shared_transition(a, b, edge_faces, cache=None):
    """True when a cone or torus face spans the gap between segment *a*'s
    high end and segment *b*'s low end — the shoulder chamfer or fillet that
    makes the two segments steps of one hole. The transition face touches
    one segment directly and may reach the other through the shoulder ring
    plane, so one adjacency hop is followed. Solid material between two
    unrelated coaxial features has no such connecting face."""
    a_partners = _end_partners(a, a["s_hi"], edge_faces, cache)
    b_partners = _end_partners(b, b["s_lo"], edge_faces, cache)
    for own, other in ((a_partners, b_partners), (b_partners, a_partners)):
        for t in own:
            if BRepAdaptor_Surface(t.wrapped).GetType() not in (GeomAbs_Cone, GeomAbs_Torus):
                continue
            if any(t.is_same(o) for o in other):
                return True
            neighbours = [f for e in t.edges() for f in edge_faces.get(e, ())]
            if any(n.is_same(o) for n in neighbours for o in other):
                return True
    return False


def _merge_stacks(stacks, edge_faces, cache=None):
    """Recombine coaxial stacks that are one hole:

    - same bore diameter on both sides of a crossing void, neither facing
      end closed (a flat bottom or drill point means genuinely separate
      holes, e.g. blind holes drilled from opposite faces);
    - different diameters whose gap is bridged by a shoulder chamfer or
      fillet face (the steps of a counterbored hole with a deburred
      shoulder).
    """
    by_line: dict = {}
    for stack in stacks:
        by_line.setdefault(_line_key(stack[0]), []).append(stack)
    merged = []
    for line_stacks in by_line.values():
        line_stacks.sort(key=lambda st: min(s["s_lo"] for s in st))
        cur = line_stacks[0]
        for nxt in line_stacks[1:]:
            a = max(cur, key=lambda s: s["s_hi"])
            b = min(nxt, key=lambda s: s["s_lo"])
            closed = ("flat", "drill_point")
            if (
                abs(a["diameter"] - b["diameter"]) < 0.01
                and _classify_end(a, a["s_hi"], True, edge_faces, cache) not in closed
                and _classify_end(b, b["s_lo"], False, edge_faces, cache) not in closed
            ):
                joined = dict(a, s_hi=b["s_hi"], faces=a["faces"] + b["faces"])
                cur = [s for s in cur if s is not a] + [joined] + [s for s in nxt if s is not b]
            elif b["s_lo"] - a["s_hi"] <= _STACK_GAP_TOL + abs(
                a["diameter"] - b["diameter"]
            ) and _shared_transition(a, b, edge_faces, cache):
                cur = cur + nxt
            else:
                merged.append(cur)
                cur = nxt
        merged.append(cur)
    return merged


_CSK_DIA_TOL = 0.2  # mm — countersink drill ⌀ vs bore ⌀
_CSK_COAX_TOL = 0.2  # mm — countersink opening off the bore axis line


def _csink_for_hole(h: HoleRecord, csinks: list[CounterSink]) -> CounterSink | None:
    """The countersink coaxial with hole *h* — parallel axis, opening on the bore's axis
    line, matching drill ⌀ — or None (#558)."""
    hx = h.axis
    for cs in csinks:
        v = tuple(cs.location[i] - h.location[i] for i in range(3))
        t = sum(v[i] * hx[i] for i in range(3))
        perp = math.hypot(*(v[i] - t * hx[i] for i in range(3)))
        parallel = abs(sum(hx[i] * cs.axis[i] for i in range(3))) > 1 - 1e-3
        if (
            parallel
            and perp <= _CSK_COAX_TOL
            and abs(cs.drill_diameter - h.diameter) <= _CSK_DIA_TOL
        ):
            return cs
    return None


def recognise_holes(part, *, cyls=None) -> list[HoleRecord]:
    """Recognise drilled holes on *part* (see :class:`HoleRecord`).

    Coaxial internal cylinders are grouped into stacks — drill + optional
    counterbore + optional spotface become one hole, and a bore interrupted
    by a crossing hole is recombined.  The bottom is classified by probing
    the face adjacent to the deep end.  Countersinks are not recognised as
    steps (the cone is treated as an opening); steps on the far side of the
    bore (e.g. a second counterbore from the back face) are not reported.

    Pass *cyls* — a precomputed ``analyse_cylinders(part)`` result — to avoid
    re-scanning the solid (mirrors ``lint_feature_coverage``'s parameter).
    """
    z_cyls, cross_cyls = cyls if cyls is not None else analyse_cylinders(part)
    internal = [c for c in _full_cyls(z_cyls) + _full_cyls(cross_cyls) if not c["external"]]
    if not internal:
        return []
    edge_faces = _edge_face_map(part)
    # one end-classification cache for the whole call: the same (seg, end) is
    # classified by _merge_stacks and again in the loop below, each scan walking
    # every face's edges (#150).
    cache: dict = {}
    stacks = _merge_stacks(_merge_runs(_segments(internal), _line_key), edge_faces, cache)

    holes = []
    for stack in stacks:
        d = stack[0]["dir_xyz"]
        lo_seg = min(stack, key=lambda s: s["s_lo"])
        hi_seg = max(stack, key=lambda s: s["s_hi"])
        lo_state = _classify_end(lo_seg, lo_seg["s_lo"], False, edge_faces, cache)
        hi_state = _classify_end(hi_seg, hi_seg["s_hi"], True, edge_faces, cache)

        # The opening is the open end; with both ends open (a through hole)
        # prefer the wider segment's end (counterbores sit at the opening),
        # falling back to the high-coordinate end (drilled from the top).
        if lo_state == "open" and hi_state != "open":
            from_hi = False
        elif hi_state == "open" and lo_state != "open":
            from_hi = True
        else:
            from_hi = hi_seg["diameter"] >= lo_seg["diameter"]
        opening_seg, opening_s = (hi_seg, hi_seg["s_hi"]) if from_hi else (lo_seg, lo_seg["s_lo"])
        bottom_state = lo_state if from_hi else hi_state
        bottom = {"open": "through"}.get(bottom_state, bottom_state)

        # Order segments from the opening inward; the bore is the narrowest
        # (not the farthest — a through hole counterbored from both sides has
        # a step beyond the bore) and only steps on the opening side count.
        ordered = sorted(stack, key=lambda s: s["s_hi"], reverse=from_hi)
        bore_i = min(range(len(ordered)), key=lambda i: ordered[i]["diameter"])
        bore = ordered[bore_i]

        # Steps narrow monotonically from the opening to the bore; a wider
        # segment between same-diameter lands is a groove (e.g. an O-ring
        # gland inside a counterbore), not a step. Lands of one step span
        # their groove.
        spans: dict = {}
        step_order = []
        min_d = math.inf
        for step in ordered[:bore_i]:
            if step["diameter"] > min_d + 0.01:
                continue
            min_d = step["diameter"]
            key = round(step["diameter"], 2)
            if key not in spans:
                spans[key] = [step["s_lo"], step["s_hi"]]
                step_order.append(key)
            else:
                spans[key][0] = min(spans[key][0], step["s_lo"])
                spans[key][1] = max(spans[key][1], step["s_hi"])
        cbore = spotface = None
        for key in step_order:
            lo, hi = spans[key]
            spec = CounterBore(key, round(hi - lo, 2))
            if spec.depth < _SPOTFACE_MAX_RATIO * spec.diameter:
                spotface = spotface or spec
            else:
                cbore = cbore or spec

        # The bore's depth runs from its top to the hole's deep end: bore
        # lands span a mid-bore groove, and a blind hole's depth includes a
        # bottom relief groove — but not a through hole's far-side steps.
        bore_segs = [s for s in stack if abs(s["diameter"] - bore["diameter"]) < 0.01]
        deep_segs = bore_segs if bottom == "through" else stack
        if from_hi:
            depth = max(s["s_hi"] for s in bore_segs) - min(s["s_lo"] for s in deep_segs)
        else:
            depth = max(s["s_hi"] for s in deep_segs) - min(s["s_lo"] for s in bore_segs)
        holes.append(
            HoleRecord(
                axis=_unit(tuple(-c for c in d) if from_hi else d),
                location=_axis_point(opening_seg, opening_s),
                diameter=bore["diameter"],
                depth=round(depth, 2),
                bottom=bottom,
                cbore=cbore,
                spotface=spotface,
            )
        )
    # Compose countersinks (#558): a coaxial cone flaring from the bore is a hole
    # attribute (like a counterbore), so it rides on the HoleRecord — HoleSpec grouping
    # and the callout-width estimate then see it for free.
    csinks = recognise_countersinks(part)
    if csinks:
        holes = [
            (replace(h, csink=cs) if (cs := _csink_for_hole(h, csinks)) is not None else h)
            for h in holes
        ]
    return holes


def recognise_bosses(part, *, cyls=None) -> list[BossRecord]:
    """Recognise external cylindrical bosses on *part* (one
    :class:`BossRecord` per coaxial external cylinder segment, including a
    turned part's OD — callers wanting only local bosses can filter on
    diameter against the part envelope).

    Pass *cyls* — a precomputed ``analyse_cylinders(part)`` result — to avoid
    re-scanning the solid (mirrors ``recognise_holes``'s parameter, so a caller
    computing both holes and bosses can share one analysis).
    """
    z_cyls, cross_cyls = cyls if cyls is not None else analyse_cylinders(part)
    external = [c for c in _full_cyls(z_cyls) + _full_cyls(cross_cyls) if c["external"]]
    if not external:
        return []
    edge_faces = _edge_face_map(part)
    cache: dict = {}

    bosses = []
    for seg in _segments(external):
        d = seg["dir_xyz"]
        lo_state = _classify_end(seg, seg["s_lo"], False, edge_faces, cache)
        hi_state = _classify_end(seg, seg["s_hi"], True, edge_faces, cache)
        # The free end is the open one (its cap faces away from the segment);
        # default to the high end when both or neither are open.
        from_hi = not (lo_state == "open" and hi_state != "open")
        bosses.append(
            BossRecord(
                axis=_unit(d if from_hi else tuple(-c for c in d)),
                location=_axis_point(seg, seg["s_hi"] if from_hi else seg["s_lo"]),
                diameter=seg["diameter"],
                height=round(seg["s_hi"] - seg["s_lo"], 2),
            )
        )
    return bosses


def feature_diameters(part, cyls=None, holes=None, bosses=None) -> list:
    """Sorted unique diameters of the *recognised* dimensionable cylindrical
    features on *part*: every hole bore, each hole's counterbore/spotface step,
    and every boss.

    This is the inventory to use for coverage checks ("is each dimensionable
    diameter called out?"). It is deliberately built from
    :func:`recognise_holes` / :func:`recognise_bosses`, not the raw :func:`full_cylinders`
    patch list, so partial cylinders that never become a real feature — slot ends and
    interrupted recesses (an exact half-cylinder pair sums to a full turn and
    fools an angle-only test, but is not a bore) — are excluded, while genuine
    counterbore/spotface steps are kept. (#158)

    Pass *cyls* — a precomputed ``analyse_cylinders(part)`` result — to share one
    scan between ``recognise_holes`` and ``recognise_bosses``. Pass *holes* — a precomputed
    ``recognise_holes`` result — to reuse the single feature inventory instead of
    re-detecting (ADR 0008 Amendment 5, #244).
    """
    cyls = analyse_cylinders(part) if cyls is None else cyls
    if holes is None:
        holes = recognise_holes(part, cyls=cyls)
    diams: list[float] = []
    for h in holes:
        diams.append(h.diameter)
        if h.cbore is not None:
            diams.append(h.cbore.diameter)
        if h.spotface is not None:
            diams.append(h.spotface.diameter)
    for b in recognise_bosses(part, cyls=cyls) if bosses is None else bosses:
        diams.append(b.diameter)
    return sorted(set(diams))


# ---------------------------------------------------------------------------
# Hole patterns — bolt circles, linear arrays, and rectangular grids
# (#92; sub-clustering and grids #126/#144)
# ---------------------------------------------------------------------------

# A pattern's holes must share a radius (bolt circle) or pitch (linear array)
# to within this fraction of the nominal, plus a small absolute floor.
_PATTERN_REL_TOL = 0.02
_PATTERN_ABS_TOL = 0.1
# Bolt-circle angular spacing must be even to within this fraction of 2π/n.
# Tight on purpose: at 15% a 100×80 rectangle reads as an equally spaced
# bolt circle; real patterns (even from noisy STEP) are within a fraction
# of a degree.
_BC_SPACING_TOL = 0.04


@dataclass(frozen=True)
class BoltCircle:
    """≥3 identical holes equally spaced on a circle.

    ``center`` is the world point at the holes' opening plane, ``diameter``
    the bolt-circle diameter (BCD), ``holes`` the member features.
    """

    holes: tuple
    center: tuple
    diameter: float


@dataclass(frozen=True)
class LinearArray:
    """≥3 identical holes collinear at constant pitch.

    ``direction`` is the unit vector from the first hole toward the last
    (members are ordered along it).
    """

    holes: tuple
    pitch: float
    direction: tuple


@dataclass(frozen=True)
class RectGrid:
    """A fully-populated rectangular grid of identical holes (an N×M lattice).

    ``rows``×``cols`` holes sit on a regular rectangular lattice with
    ``row_pitch`` spacing along the first lattice axis and ``col_pitch`` along
    the second; every lattice position is occupied (``rows * cols == len(holes)``).
    ``angle`` is the first axis's orientation in degrees within the holes'
    opening plane, normalised to ``[0, 90)``. ``center`` is the world point at
    the grid centroid (opening plane).

    A rectangular *ring* / perimeter (holes only around the edge, interior
    empty) is not a grid — it is reported as its constituent edge
    :class:`LinearArray` rows instead.
    """

    holes: tuple
    rows: int
    cols: int
    row_pitch: float
    col_pitch: float
    angle: float
    center: tuple


def _pattern_tol(nominal: float) -> float:
    return _PATTERN_REL_TOL * nominal + _PATTERN_ABS_TOL


@dataclass(frozen=True)
class HoleSpec:
    """The machining spec shared by holes that are the *same drilled feature*.

    Two holes drilled with the same tool, in the same direction, with the same
    counterbore/spotface stack have equal :class:`HoleSpec` values (a through
    drill is the same spec whatever wall it pierces). Because the dataclass is
    frozen it hashes and compares by value, so it is a stable dict/set key for
    grouping holes — pattern detection and callout grouping agree when they key
    on the same :class:`HoleSpec`.

    Build one with :meth:`from_hole`; do not construct the fields by hand (the
    normalisation in :meth:`from_hole` is part of the contract). ``axis`` is the
    drilling direction snapped to 6 dp (boolean ops leave ~1e-16 noise on the
    components, and exact float keys would split a pattern silently). ``depth``
    is ``None`` for a through hole — its depth is irrelevant to the spec —
    otherwise the bore depth. Public and stable for downstream consumers (e.g.
    ``draftwright``).
    """

    axis: tuple
    diameter: float
    depth: float | None
    bottom: str
    cbore: CounterBore | None
    spotface: CounterBore | None
    # The countersink's *size* only — ``(major_diameter, included_angle)`` — never its
    # location, so identical countersunk holes at different positions share one spec (#558).
    csink: tuple[float, float] | None = None

    @classmethod
    def from_hole(cls, hole: HoleRecord) -> "HoleSpec":
        """The :class:`HoleSpec` for *hole* (a :class:`HoleRecord`)."""
        depth = None if hole.bottom == "through" else hole.depth
        axis = tuple(0.0 if abs(c) < 1e-6 else round(c, 6) for c in hole.axis)
        csink = (hole.csink.major_diameter, hole.csink.included_angle) if hole.csink else None
        return cls(axis, hole.diameter, depth, hole.bottom, hole.cbore, hole.spotface, csink)


def _spec_key(h):
    return HoleSpec.from_hole(h)


def _plane_uv(axis):
    """Two unit vectors spanning the plane perpendicular to *axis*."""
    ax, ay, az = axis
    ref = (0.0, 0.0, 1.0) if abs(az) < 0.9 else (1.0, 0.0, 0.0)
    ux = ay * ref[2] - az * ref[1]
    uy = az * ref[0] - ax * ref[2]
    uz = ax * ref[1] - ay * ref[0]
    n = math.hypot(ux, uy, uz)
    u = (ux / n, uy / n, uz / n)
    v = (
        ay * u[2] - az * u[1],
        az * u[0] - ax * u[2],
        ax * u[1] - ay * u[0],
    )
    return u, v


def _as_bolt_circle(holes, pts):
    """BoltCircle when *pts* (2D) are equally spaced on a common circle."""
    n = len(pts)
    cx = sum(p[0] for p in pts) / n
    cy = sum(p[1] for p in pts) / n
    radii = [math.hypot(p[0] - cx, p[1] - cy) for p in pts]
    r = sum(radii) / n
    if r < _PATTERN_ABS_TOL or max(abs(ri - r) for ri in radii) > _pattern_tol(r):
        return None
    angles = sorted(math.atan2(p[1] - cy, p[0] - cx) for p in pts)
    gaps = [angles[i + 1] - angles[i] for i in range(n - 1)]
    gaps.append(2 * math.pi - (angles[-1] - angles[0]))
    even = 2 * math.pi / n
    if max(abs(g - even) for g in gaps) > _BC_SPACING_TOL * even:
        return None
    center = tuple(sum(c) / n for c in zip(*(h.location for h in holes), strict=True))
    return BoltCircle(holes=tuple(holes), center=center, diameter=round(2 * r, 2))


def _as_linear_array(holes, pts):
    """LinearArray when *pts* (2D) are collinear at constant pitch."""
    n = len(pts)
    # endpoints are the farthest-apart pair: robust for any orientation. A
    # lexicographic (x, y) sort would pick the wrong ends for a near-axis row
    # whose coordinates carry the sub-micron noise real STEP geometry always
    # has — an interior point sorts first, halving the span and pitch and
    # rejecting a perfectly good array.
    i0, i1 = max(
        ((i, j) for i in range(n) for j in range(i + 1, n)),
        key=lambda ij: math.dist(pts[ij[0]], pts[ij[1]]),
    )
    first, last = pts[i0], pts[i1]
    dx, dy = last[0] - first[0], last[1] - first[1]
    span = math.hypot(dx, dy)
    if span < _PATTERN_ABS_TOL:
        return None
    ux, uy = dx / span, dy / span
    # collinearity: every point within tolerance of the first→last line —
    # scaled to the pitch, not the span (a long row must not absorb holes
    # millimetres off-line)
    line_tol = _pattern_tol(span / (n - 1))
    if any(abs((p[0] - first[0]) * -uy + (p[1] - first[1]) * ux) > line_tol for p in pts):
        return None
    # project each point onto the first→last axis once (used both for the pitch
    # check, sorted, and to order the members below)
    proj = [(p[0] - first[0]) * ux + (p[1] - first[1]) * uy for p in pts]
    ts = sorted(proj)
    pitches = [ts[i + 1] - ts[i] for i in range(n - 1)]
    pitch = span / (n - 1)
    if max(abs(p - pitch) for p in pitches) > _pattern_tol(pitch):
        return None
    # order members along the array, in world coordinates
    ordered = sorted(zip(proj, holes, strict=True), key=lambda t: t[0])
    w0 = ordered[0][1].location
    w1 = ordered[-1][1].location
    d = tuple(b - a for a, b in zip(w0, w1, strict=True))
    norm = math.hypot(d[0], d[1], d[2])
    return LinearArray(
        holes=tuple(h for _, h in ordered),
        pitch=round(pitch, 2),
        direction=_unit(tuple(c / norm for c in d)),
    )


def _circumcircle(p0, p1, p2):
    """Centre and radius ``(cx, cy, r)`` of the circle through three 2D points,
    or ``None`` when they are collinear (so a collinear triple can never seed a
    bolt circle — collinearity must win, per :func:`recognise_hole_patterns`)."""
    ax, ay = p0
    bx, by = p1
    cx, cy = p2
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-9:
        return None
    a2, b2, c2 = ax * ax + ay * ay, bx * bx + by * by, cx * cx + cy * cy
    ux = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / d
    uy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / d
    return ux, uy, math.hypot(ax - ux, ay - uy)


def _bolt_circle_candidates(members, pts):
    """All bolt circles within a spec group: every triple seeds a candidate
    circle, the group's points lying on it are gathered, and the set is kept
    only if :func:`_as_bolt_circle` confirms it is fully, evenly populated.
    Returns ``(BoltCircle, frozenset(member indices))`` candidates."""
    n = len(pts)
    out, seen = [], set()
    for i in range(n):
        for j in range(i + 1, n):
            for k in range(j + 1, n):
                circ = _circumcircle(pts[i], pts[j], pts[k])
                if circ is None:
                    continue
                cx, cy, r = circ
                if r < _PATTERN_ABS_TOL:
                    continue
                key = (round(cx, 2), round(cy, 2), round(r, 2))
                if key in seen:
                    continue
                seen.add(key)
                tol = _pattern_tol(r)
                idx = [
                    m
                    for m in range(n)
                    if abs(math.hypot(pts[m][0] - cx, pts[m][1] - cy) - r) <= tol
                ]
                if len(idx) < 3:
                    continue
                pat = _as_bolt_circle([members[m] for m in idx], [pts[m] for m in idx])
                if pat is not None:
                    out.append((pat, frozenset(idx)))
    return out


def _linear_array_candidates(members, pts):
    """All linear arrays within a spec group: every pair seeds a line, the
    group's collinear points are gathered and sorted, and each maximal
    constant-pitch run of ≥3 becomes a candidate. Returns
    ``(LinearArray, frozenset(member indices))`` candidates."""
    n = len(pts)
    out, seen = [], set()
    for i in range(n):
        for j in range(i + 1, n):
            dx, dy = pts[j][0] - pts[i][0], pts[j][1] - pts[i][1]
            span = math.hypot(dx, dy)
            if span < _PATTERN_ABS_TOL:
                continue
            ux, uy = dx / span, dy / span
            tol = _pattern_tol(span)
            online = [
                m
                for m in range(n)
                if abs(-(pts[m][1] - pts[i][1]) * ux + (pts[m][0] - pts[i][0]) * uy) <= tol
            ]
            order = sorted(
                online,
                key=lambda m: (pts[m][0] - pts[i][0]) * ux + (pts[m][1] - pts[i][1]) * uy,
            )
            ts = [(pts[m][0] - pts[i][0]) * ux + (pts[m][1] - pts[i][1]) * uy for m in order]
            # split the sorted collinear points into maximal constant-pitch runs
            a = 0
            while a < len(order) - 2:
                pitch = ts[a + 1] - ts[a]
                b = a + 1
                while b + 1 < len(order) and abs((ts[b + 1] - ts[b]) - pitch) <= _pattern_tol(
                    pitch
                ):
                    b += 1
                run = order[a : b + 1]
                run_key = frozenset(run)
                if len(run) >= 3 and run_key not in seen:
                    seen.add(run_key)
                    pat = _as_linear_array([members[m] for m in run], [pts[m] for m in run])
                    if pat is not None:
                        out.append((pat, run_key))
                a = b  # a broken pitch starts the next run at the break point
    return out


def _rect_grid(members, pts):
    """A :class:`RectGrid` when the whole spec group fills a regular N×M
    rectangular lattice, else ``None``. The two shortest near-orthogonal
    pairwise vectors define the lattice basis; every point must land on an
    integer cell and every cell must be occupied (no holes, no extras). 2×2 is
    excluded — four lattice corners are a rectangle, not a grid."""
    n = len(pts)
    if n < 6:
        return None
    diffs = []
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            dx, dy = pts[j][0] - pts[i][0], pts[j][1] - pts[i][1]
            length = math.hypot(dx, dy)
            if length > _PATTERN_ABS_TOL:
                diffs.append((length, dx, dy))
    if not diffs:
        return None
    diffs.sort()
    l1, b1x, b1y = diffs[0]
    u1 = (b1x / l1, b1y / l1)
    basis2 = next(
        (
            (length, dx, dy)
            for length, dx, dy in diffs
            if abs((dx * u1[0] + dy * u1[1]) / length) < 0.2
        ),
        None,
    )
    if basis2 is None:
        return None
    l2, b2x, b2y = basis2
    u2 = (b2x / l2, b2y / l2)
    a0 = min(p[0] * u1[0] + p[1] * u1[1] for p in pts)
    b0 = min(p[0] * u2[0] + p[1] * u2[1] for p in pts)
    cells = []
    for p in pts:
        da = p[0] * u1[0] + p[1] * u1[1] - a0
        db = p[0] * u2[0] + p[1] * u2[1] - b0
        ci, cj = round(da / l1), round(db / l2)
        if abs(da - ci * l1) > _pattern_tol(l1) or abs(db - cj * l2) > _pattern_tol(l2):
            return None
        cells.append((ci, cj))
    if len(set(cells)) != n:
        return None
    rows = max(c[0] for c in cells) + 1
    cols = max(c[1] for c in cells) + 1
    if rows < 2 or cols < 2 or max(rows, cols) < 3 or rows * cols != n:
        return None
    center = tuple(sum(c) / n for c in zip(*(h.location for h in members), strict=True))
    return RectGrid(
        holes=tuple(members),
        rows=rows,
        cols=cols,
        row_pitch=round(l1, 2),
        col_pitch=round(l2, 2),
        angle=round(math.degrees(math.atan2(u1[1], u1[0])) % 90.0, 2),
        center=center,
    )


def recognise_hole_patterns(holes) -> list:
    """Recognise :class:`BoltCircle`, :class:`LinearArray`, and
    :class:`RectGrid` patterns among *holes* (``HoleRecord`` records, e.g.
    from :func:`recognise_holes`).

    Holes are grouped by machining spec and drilling axis, then each group is
    *sub-clustered* — a single spec can contribute several patterns (two
    separate bolt circles, the rows of a rectangular perimeter, a grid). All
    candidate sub-patterns are enumerated and allocated greedily largest-first,
    so each hole belongs to at most one pattern and the richest interpretation
    wins. A filled N×M lattice becomes one :class:`RectGrid`; a rectangular
    ring or perimeter is reported as its edge :class:`LinearArray` rows.

    Collinearity is tested ahead of concyclicity (any three points are
    concyclic, so a 3-hole "bolt circle" must really be an equilateral
    triangle); unpatterned holes are simply absent from the result.
    """
    groups: dict = {}
    for h in holes:
        groups.setdefault(_spec_key(h), []).append(h)

    patterns = []
    for spec, members in groups.items():
        if len(members) < 3:
            continue
        u, v = _plane_uv(spec.axis)
        pts = [
            (
                sum(a * b for a, b in zip(h.location, u, strict=True)),
                sum(a * b for a, b in zip(h.location, v, strict=True)),
            )
            for h in members
        ]
        candidates: list = []
        grid = _rect_grid(members, pts)
        if grid is not None:
            candidates.append((grid, frozenset(range(len(members)))))
        candidates += _bolt_circle_candidates(members, pts)
        candidates += _linear_array_candidates(members, pts)
        # allocate largest-first; a hole used by one pattern is off the table
        # for the rest (stable sort keeps grids ahead of circles ahead of rows
        # at equal size)
        candidates.sort(key=lambda c: -len(c[1]))
        used: set = set()
        for pattern, idx in candidates:
            if idx & used:
                continue
            patterns.append(pattern)
            used |= idx
    return patterns
