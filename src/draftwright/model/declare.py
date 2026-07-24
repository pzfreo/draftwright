"""declare — build IR features directly from known build123d objects (ADR 0011).

The normal path detects features from the finished solid's silhouettes
(``recognition/`` → ``build_part_model``). When you *built* the part you already
know its features, so re-detecting them is redundant — and, on nested/exotic
geometry, unreliable (cf. #298). These constructors turn a known build123d object
(or explicit values) into the same IR ``Feature`` the detector would emit, so you
can hand a ``model=[...]`` to :func:`draftwright.build_drawing` (or a
:class:`draftwright.Sheet`) and skip detection.

Every constructor has two flavours:

- **reference an object** — ``hole(tool_cylinder)`` reads the geometry (⌀ from the
  cylindrical *face*, axis + location from the bounding box); or
- **explicit values** — ``hole(diameter=6, at=(20, 10, 0), axis="z")`` for
  parametric code that never built a discrete tool.

Geometry read is deliberately conservative: the diameter comes from the true
cylindrical-face radius (robust on a chamfered / partial cylinder, where a bbox
would drift), while axis + centre come from the bounding box — enough for the IR's
letter-based :class:`~draftwright.model.ir.Frame`. A non-axis-aligned feature, or a
shape with no cylindrical face, should use the explicit flavour.
"""

from __future__ import annotations

import math
import warnings

from draftwright.model.ir import (
    AUTHORED_DIMENSION_KINDS,
    AuthoredDimension,
    BossFeature,
    ChamferFeature,
    ControlFrame,
    DatumRef,
    EnvelopeFeature,
    Feature,
    FilletFeature,
    Finish,
    FlatFeature,
    Frame,
    GrooveFeature,
    HoleFeature,
    Note,
    PatternFeature,
    PlateFeature,
    PocketFeature,
    PocketPatternFeature,
    Point,
    SlotFeature,
    StepFeature,
    StepLevelFeature,
)

# Fractional tolerance below which a slot object's two longest bbox spans count as "near-equal",
# making the long-vs-width axis read a coin-flip (#490). Mirrors recognition/slots.py's tie frac.
_SLOT_AMBIGUOUS_FRAC = 0.05


def _norm_axis(axis: str) -> str:
    """Normalise a user-supplied axis letter to the lowercase ``{x,y,z}`` the IR uses.
    build123d callers naturally reach for ``"X"``/``"Z"`` (à la ``Axis.X``); the
    lowercase-letter convention is an IR-internal detail, so accept either and fail
    clearly on anything else rather than crashing deep in ``"xyz".index(...)``."""
    a = str(axis).lower()
    if a not in ("x", "y", "z"):
        raise ValueError(f"axis must be one of 'x'/'y'/'z' (got {axis!r})")
    return a


def _is_positive(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0


def _require_positive(**named) -> None:
    """Each supplied (non-``None``) value must be a positive number. These constructors are
    a public compiler input (ADR 0011); a negative/zero size must fail at declaration time
    with a clear ``ValueError``, not later in layout or as a misleading drawing (#452). A
    ``None`` is skipped (the field is optional) — use :func:`_positive` for a required one."""
    for name, v in named.items():
        if v is not None and not _is_positive(v):
            raise ValueError(f"{name} must be a positive number (got {v!r})")


def _positive(name: str, v) -> None:
    """A *required* positive number — ``None`` fails too (a missing defining dim, #452)."""
    if not _is_positive(v):
        raise ValueError(f"{name} must be a positive number (got {v!r})")


def _require_count(name: str, count) -> None:
    """A count must be a positive *int* — a fractional/None count is nonsensical and would
    otherwise store silently or crash later in ``range(count)`` with a raw TypeError (#452)."""
    if not (isinstance(count, int) and not isinstance(count, bool) and count >= 1):
        raise ValueError(f"{name} needs count >= 1 as an int (got {count!r})")


def _require_point(name: str, pt) -> None:
    """A location/point kwarg must be an ``(x, y, z)`` triple of numbers."""
    if not (
        isinstance(pt, (tuple, list))
        and len(pt) == 3
        and all(isinstance(c, (int, float)) and not isinstance(c, bool) for c in pt)
    ):
        raise ValueError(f"{name} must be an (x, y, z) tuple of numbers (got {pt!r})")


def _require_pair_positive(name: str, pair) -> None:
    """A ``(diameter, depth)`` pair (``cbore`` / ``spotface``) must be two positive numbers."""
    if pair is None:
        return
    if not (isinstance(pair, (tuple, list)) and len(pair) == 2):
        raise ValueError(f"{name} must be a (diameter, depth) pair (got {pair!r})")
    _positive(f"{name} diameter", pair[0])  # both required — a None slot must fail too
    _positive(f"{name} depth", pair[1])


def _require_csink(name: str, csink) -> None:
    """A ``(major_diameter, included_angle)`` pair — both positive, the angle a real cone
    (< 180°). The second slot is an ANGLE, not a depth, so it gets its own message."""
    if csink is None:
        return
    if not (isinstance(csink, (tuple, list)) and len(csink) == 2):
        raise ValueError(f"{name} must be a (major_diameter, included_angle) pair (got {csink!r})")
    _positive(f"{name} major_diameter", csink[0])
    _positive(f"{name} included_angle", csink[1])
    if not csink[1] < 180:
        raise ValueError(f"{name} included_angle must be < 180° (got {csink[1]})")


def _bbox_axis_dia(obj) -> tuple[str, float, Point]:
    """Read an axis-aligned cylinder's (axis, diameter, centre) off its bounding box:
    the two near-equal spans are the diameter; the odd one out is the bore/OD axis."""
    bb = obj.bounding_box()
    sizes = [bb.size.X, bb.size.Y, bb.size.Z]
    # The equal pair (smallest span-difference) is the diameter; its complement is the axis.
    i, j, k = min([(0, 1, 2), (0, 2, 1), (1, 2, 0)], key=lambda p: abs(sizes[p[0]] - sizes[p[1]]))
    dia = (sizes[i] + sizes[j]) / 2
    c = bb.center()
    return "xyz"[k], dia, (c.X, c.Y, c.Z)


def _read_cylinder(obj) -> tuple[str, float, Point]:
    """(axis, diameter, centre) for a cylindrical tool. The diameter is taken from the
    largest cylindrical *face* — robust where a bbox drifts (a chamfered or partial
    cylinder); axis + centre come from the bbox (sufficient for the letter-based IR).

    Caveat: on an object with several cylindrical faces (a counterbore, a tube) the
    *largest* radius wins — it reads the counterbore / OD, not the bore. Pass an explicit
    ``diameter=`` for those (a counterbore is declared via the ``cbore=`` param anyway)."""
    axis, dia, center = _bbox_axis_dia(obj)
    try:
        from build123d import GeomType

        faces = obj.faces().filter_by(GeomType.CYLINDER)
        if faces:
            dia = 2 * max(f.radius for f in faces)
    except Exception:
        pass  # no B-rep face query available — fall back to the bbox diameter
    return axis, dia, center


def read_bore_step(part, tool, axis: str) -> tuple[float, float]:
    """``(diameter, depth)`` of a counterbore / spotface *tool* cut into *part* (ADR 0011 #462).

    ⌀ comes from the tool's cylindrical face (like :func:`_read_cylinder`); **depth** is measured
    along the hole *axis* from the part's **open face at the hole** to the tool's inner (floor)
    face — so both values track the geometry you built, restating no numbers.

    The open face is found *locally*: the part is intersected with an axial prism over the tool's
    cross-section, so a rib / wall / boss elsewhere on the part doesn't skew the reading (the
    global bounding box would). The counterbore opens on whichever end of that local column the
    tool sits nearer, even when the tool overhangs the part."""
    from build123d import Box, Pos

    axis = _norm_axis(axis)
    dia = _read_cylinder(tool)[1]
    i = "xyz".index(axis)
    tb, pb = tool.bounding_box(), part.bounding_box()
    t_lo, t_hi = [tb.min.X, tb.min.Y, tb.min.Z][i], [tb.max.X, tb.max.Y, tb.max.Z][i]

    # A prism over the tool's cross-section, spanning the part along the axis; intersect it with
    # the part to get the LOCAL material column under the tool (the open face is its axial edge).
    size = [tb.size.X, tb.size.Y, tb.size.Z]
    cen = [tb.center().X, tb.center().Y, tb.center().Z]
    size[i] = [pb.size.X, pb.size.Y, pb.size.Z][i] * 3 or 1.0
    cen[i] = [pb.center().X, pb.center().Y, pb.center().Z][i]
    try:
        column = part & (Pos(*cen) * Box(*size))
        cb = column.bounding_box()
        p_lo, p_hi = [cb.min.X, cb.min.Y, cb.min.Z][i], [cb.max.X, cb.max.Y, cb.max.Z][i]
    except Exception:  # noqa: BLE001 — degenerate boolean; fall back to the global bbox
        p_lo, p_hi = [pb.min.X, pb.min.Y, pb.min.Z][i], [pb.max.X, pb.max.Y, pb.max.Z][i]

    if (t_lo + t_hi) / 2 >= (p_lo + p_hi) / 2:
        depth = p_hi - t_lo  # floor at the tool bottom, open at the max face
    else:
        depth = t_hi - p_lo  # floor at the tool top, open at the min face
    return (round(dia, 3), round(depth, 3))


def _span(at: Point, axis: str, length: float) -> tuple[Point, Point]:
    """The two axial end-points of a segment of *length* centred at *at* along *axis*."""
    idx = "xyz".index(axis)
    lo, hi = list(at), list(at)
    lo[idx] = at[idx] - length / 2
    hi[idx] = at[idx] + length / 2
    return (lo[0], lo[1], lo[2]), (hi[0], hi[1], hi[2])


def hole(
    obj=None,
    *,
    diameter=None,
    at=None,
    axis=None,
    through=True,
    depth=None,
    cbore=None,
    spotface=None,
    csink=None,
    thread=None,
    count=1,
    members=(),
) -> HoleFeature:
    """A drilled hole. Either ``hole(tool_cylinder)`` — read ⌀ / axis / location off the
    build123d object you subtracted — or ``hole(diameter=6, at=(20, 10, 0), axis="z")``.

    ``cbore`` / ``spotface`` are ``(diameter, depth)`` pairs; ``csink`` is a
    ``(major_diameter, included_angle)`` pair (a flat-head seat, callout ``⌵ Ø.. × ..°``);
    ``thread`` is a tap/thread spec string (e.g. ``"M3x0.5"``) folded onto the callout
    (#764); ``count`` + ``members`` describe a machining-spec group drawn as one ``count×``.

    An object supplies *defaults*; any explicit keyword overrides that field (#451)."""
    if obj is not None:
        r_axis, r_diameter, r_at = _read_cylinder(obj)
        axis = r_axis if axis is None else axis
        diameter = r_diameter if diameter is None else diameter
        at = r_at if at is None else at
    if diameter is None or at is None or axis is None:
        raise ValueError("hole() needs an object, or explicit diameter=, at= and axis=")
    axis = _norm_axis(axis)
    _require_positive(diameter=diameter, depth=depth)
    _require_pair_positive("cbore", cbore)
    _require_pair_positive("spotface", spotface)
    _require_csink("csink", csink)  # (major_diameter, included_angle)
    _require_point("at", at)
    _require_count("hole()", count)
    return HoleFeature(
        frame=Frame(origin=at, axis=axis),
        diameter=diameter,
        depth=depth,
        through=through,
        count=count,
        members=tuple(members),
        cbore=cbore,
        spotface=spotface,
        csink=csink,
        thread=thread,
    )


def read_countersink(cone) -> tuple[float, float]:
    """``(major_diameter, included_angle°)`` of a countersink **cone** tool — the larger rim ⌀
    and the full cone angle, read off its conical **face** (not a removed edge, #576 lesson).
    The cone geometry is the recogniser's own
    :func:`~draftwright.recognition.countersinks.cone_rims` (#704), so a declared
    countersink reads identically to a detected one by construction."""
    from build123d import GeomType

    from draftwright.recognition import cone_rims

    faces = cone.faces().filter_by(GeomType.CONE)
    if not faces:
        raise ValueError("countersink(cone=...) needs a conical tool (a build123d Cone)")
    rims = cone_rims(faces[0])
    if rims is None:
        raise ValueError(
            "countersink(cone=...) needs a flared cone with two distinct-radius rims; a "
            "single-rim drill-point cone is not a countersink"
        )
    _minor_e, major_e, included = rims
    return round(2 * major_e.radius, 4), included


def boss(obj=None, *, diameter=None, height=None, at=None, axis=None, span=None) -> BossFeature:
    """An external cylindrical boss / OD. Either ``boss(cylinder)`` or
    ``boss(diameter=6, at=(0, 0, 0), axis="x")`` (parametric). An object supplies
    *defaults*; any explicit keyword overrides that field (#451)."""
    if obj is not None:
        r_axis, r_diameter, r_at = _read_cylinder(obj)
        axis = r_axis if axis is None else axis
        diameter = r_diameter if diameter is None else diameter
        at = r_at if at is None else at
        if height is None:
            bb = obj.bounding_box()
            height = [bb.size.X, bb.size.Y, bb.size.Z]["xyz".index(_norm_axis(axis))]
    if diameter is None or at is None or axis is None:
        raise ValueError("boss() needs an object, or explicit diameter=, at= and axis=")
    axis = _norm_axis(axis)
    _require_positive(diameter=diameter)
    if height is not None:
        _require_positive(height=height)
    _require_point("at", at)
    if height is not None and span is None:
        span = _span(at, axis, height)
    return BossFeature(
        frame=Frame(origin=at, axis=axis), diameter=diameter, height=height, span=span
    )


def step(obj=None, *, diameter=None, length=None, at=None, axis=None, span=None) -> StepFeature:
    """One axial segment of a turned profile — its OD + length. Either ``step(segment)``
    (⌀ from the cylindrical face, length + centre from the bbox along its axis) or
    explicit ``step(diameter=4, length=10, at=(0, 0, 0), axis="x")``. ``span`` (the two
    axial end-points) is derived from ``at`` + ``length`` when not given. An object supplies
    *defaults*; any explicit keyword overrides that field (#451)."""
    if obj is not None:
        r_axis, r_diameter, r_at = _read_cylinder(obj)
        axis = r_axis if axis is None else axis
        diameter = r_diameter if diameter is None else diameter
        at = r_at if at is None else at
        if length is None:
            bb = obj.bounding_box()
            length = [bb.size.X, bb.size.Y, bb.size.Z]["xyz".index(_norm_axis(axis))]
    if diameter is None or length is None or at is None or axis is None:
        raise ValueError("step() needs an object, or explicit diameter=, length=, at= and axis=")
    axis = _norm_axis(axis)
    _require_positive(diameter=diameter, length=length)
    _require_point("at", at)
    if span is None:
        span = _span(at, axis, length)
    return StepFeature(
        frame=Frame(origin=at, axis=axis), length=length, diameter=diameter, span=span
    )


def _read_chamfer_face(face) -> tuple[str, float, float, Point]:
    """Read a chamfer off its **oblique planar bevel face**: the axis (the edge the chamfer
    runs along), the two legs (the face's in-plane extents), and a point **on the bevel** (the
    face centre — not the removed sharp corner). The classification + leg geometry is the
    recogniser's own :func:`~draftwright.recognition.chamfers.classify_bevel` (#704), so a
    declared chamfer reads identically to a detected one by construction; only the
    user-facing error messages live here."""
    from draftwright.recognition import BevelReject, classify_bevel

    try:
        edge_i, _nv, _span, hi, lo = classify_bevel(face)
    except BevelReject as e:
        if e.reason == "aligned":
            raise ValueError(
                "chamfer(face=...) needs an OBLIQUE planar face (the bevel); an axis-aligned "
                "face is not a chamfer — declare with axis=, leg=, at= instead"
            ) from None
        if e.reason == "compound":
            raise ValueError(
                "chamfer(face=...): the bevel must run along one principal axis; "
                "use axis=, leg=, at="
            ) from None
        raise ValueError("chamfer(face=...) needs an oblique planar bevel face") from None
    c = face.center()
    # No angle: it is always derivable from the legs, so returning it would let a leg-only
    # override leave a stale, contradicting angle (#580 review). chamfer() derives it.
    return "xyz"[edge_i], round(hi, 3), round(lo, 3), (round(c.X, 4), round(c.Y, 4), round(c.Z, 4))


def chamfer(
    obj=None, *, axis=None, leg=None, leg1=None, leg2=None, angle=None, at=None
) -> ChamferFeature:
    """A chamfer (bevelled edge, #560/#576). Either ``chamfer(bevel_face)`` — the oblique
    chamfer face supplies axis, both legs and a leader point **on the bevel** — or explicit
    ``chamfer(axis="z", leg=6, at=(x, y, z))``. ``leg`` is an equal-leg 45° chamfer (callout
    ``C{leg}``); give **both** ``leg1``/``leg2`` for an asymmetric one (``{leg} × {angle}°``).
    The angle is always derived from the legs; an explicit ``angle=`` is only *validated*
    against them (a one-leg-plus-angle spec is not yet supported — #581). An object supplies
    *defaults*; any explicit keyword overrides (#451)."""
    if obj is not None:
        r_axis, r_leg1, r_leg2, r_at = _read_chamfer_face(obj)
        axis = r_axis if axis is None else axis
        leg1 = r_leg1 if leg1 is None else leg1
        leg2 = r_leg2 if leg2 is None else leg2
        at = r_at if at is None else at  # angle is never seeded — always derived from legs
    if leg is not None:  # explicit equal-leg shorthand
        leg1 = leg2 = leg
    elif leg1 is not None and leg2 is None:
        leg2 = leg1
    if leg1 is None or axis is None or at is None:
        raise ValueError(
            "chamfer() needs a bevel face, or explicit axis=, at= and leg= (or leg1=/leg2=)"
        )
    axis = _norm_axis(axis)
    _require_positive(leg1=leg1, leg2=leg2)
    _require_point("at", at)
    hi, lo = max(leg1, leg2), min(leg1, leg2)
    derived = 45.0 if abs(hi - lo) < 1e-9 else round(math.degrees(math.atan2(lo, hi)), 2)
    if angle is None:
        angle = derived
    elif not 0 < angle < 90:
        raise ValueError(f"chamfer angle must be in (0, 90)°, got {angle}")
    elif abs(angle - derived) > 0.5:
        raise ValueError(
            f"chamfer angle {angle}° contradicts legs {leg1}/{leg2} (which imply {derived}°)"
        )
    return ChamferFeature(
        frame=Frame(origin=at, axis=axis), axis=axis, leg1=hi, leg2=lo, angle=angle
    )


def _read_fillet_face(face) -> tuple[str, float, Point]:
    """Read a fillet off its **cylindrical blend face**: the axis (the edge the fillet runs
    along), the radius (the cylinder radius), and a point **on the round** (mid angular/axial of
    the trimmed face — the ``R`` leader's tip). Agrees with the recogniser (recognition/fillets.py)
    in the two in-plane (placement) coordinates; the along-edge coordinate is view depth."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder

    s = BRepAdaptor_Surface(face.wrapped)
    if s.GetType() != GeomAbs_Cylinder:
        raise ValueError(
            "fillet(face=...) needs a cylindrical blend face (the round); an edge or flat "
            "face is not a fillet — declare with axis=, radius=, at= instead"
        )
    d = s.Cylinder().Axis().Direction()
    comp = (abs(d.X()), abs(d.Y()), abs(d.Z()))
    if max(comp) <= 0.99:
        raise ValueError(
            "fillet(face=...): the round must run along one principal axis; use axis=, radius=, at="
        )
    edge_i = max(range(3), key=lambda i: comp[i])
    # Anchor on the curved radius surface itself — the recogniser's own fillet_anchor
    # (#622 lesson: never the bbox centre), so a declared fillet's leader tip is
    # identical to the detected one's by construction (#704).
    from draftwright.recognition import fillet_anchor

    p = fillet_anchor(s)
    return (
        "xyz"[edge_i],
        round(s.Cylinder().Radius(), 3),
        (
            round(p[0], 4),
            round(p[1], 4),
            round(p[2], 4),
        ),
    )


def fillet(obj=None, *, axis=None, radius=None, at=None) -> FilletFeature:
    """A fillet (rounded edge, #561). Either ``fillet(round_face)`` — the cylindrical blend
    face supplies axis, radius and a leader point **on the round** — or explicit
    ``fillet(axis="z", radius=3, at=(x, y, z))``. Called out ``R{radius}`` (grouped ``n× R``
    for equal radii). An object supplies *defaults*; any explicit keyword overrides (#451)."""
    if obj is not None:
        r_axis, r_radius, r_at = _read_fillet_face(obj)
        axis = r_axis if axis is None else axis
        radius = r_radius if radius is None else radius
        at = r_at if at is None else at
    if radius is None or axis is None or at is None:
        raise ValueError("fillet() needs a round face, or explicit axis=, radius= and at=")
    axis = _norm_axis(axis)
    _require_positive(radius=radius)
    _require_point("at", at)
    return FilletFeature(frame=Frame(origin=at, axis=axis), axis=axis, radius=round(radius, 3))


def _read_flat_face(face) -> Point:
    """Read the leader point of a machined flat off its **planar face**: the face centre (the
    recogniser's anchor, recognition/flats.py). The across-flats size and the stock's turning
    axis are NOT recoverable from the plane alone — a plane does not carry its stock's radius,
    and its normal is perpendicular to *two* axes — so ``flat(face, ...)`` still needs
    ``axis=`` and ``across=``."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Plane

    if BRepAdaptor_Surface(face.wrapped).GetType() != GeomAbs_Plane:
        raise ValueError(
            "flat(face=...) needs the planar flat face; declare with axis=, across=, at= instead"
        )
    c = face.center()
    return (round(c.X, 4), round(c.Y, 4), round(c.Z, 4))


def flat(obj=None, *, axis=None, across=None, at=None) -> FlatFeature:
    """A machined flat on round stock (#148b). Either ``flat(flat_face)`` — the planar face
    supplies the leader point ``at`` (``axis=`` and ``across=`` still required, being
    unrecoverable from a plane) — or fully explicit ``flat(axis="z", across=15, at=(x, y,
    z))``. Called out ``{across} A/F`` (across flats). An object supplies the ``at`` default;
    any explicit keyword overrides (#451)."""
    if obj is not None:
        at = _read_flat_face(obj) if at is None else at
    if across is None or axis is None or at is None:
        raise ValueError("flat() needs axis=, across= and at= (a flat face supplies only at=)")
    axis = _norm_axis(axis)
    _require_positive(across=across)
    _require_point("at", at)
    return FlatFeature(frame=Frame(origin=at, axis=axis), axis=axis, across=round(across, 3))


def _read_groove_face(face) -> tuple[str, float, float, Point]:
    """Read a groove off its **floor cylindrical face** (the reduced-OD band): the turning
    axis (the cylinder direction), the width (the face's axial span), the floor diameter (the
    cylinder radius doubled), and the groove centre on the axis (the face bbox centre — the
    leader tip). Agrees with the recogniser (recognition/grooves.py), which anchors on the
    same bbox centre and band span."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder

    s = BRepAdaptor_Surface(face.wrapped)
    if s.GetType() != GeomAbs_Cylinder:
        raise ValueError(
            "groove(face=...) needs the floor cylindrical face (the reduced-OD band); declare "
            "with axis=, width=, diameter=, at= instead"
        )
    d = s.Cylinder().Axis().Direction()
    comp = (abs(d.X()), abs(d.Y()), abs(d.Z()))
    if max(comp) <= 0.99:
        raise ValueError(
            "groove(face=...): the stock must run along one principal axis; use axis=, width=, "
            "diameter=, at="
        )
    axis_i = max(range(3), key=lambda i: comp[i])
    bb = face.bounding_box()
    span = ((bb.min.X, bb.max.X), (bb.min.Y, bb.max.Y), (bb.min.Z, bb.max.Z))[axis_i]
    # The leader tip is the recogniser's own floor_face_anchor (#704), so a declared
    # groove anchors identically to a detected one by construction.
    from draftwright.recognition import floor_face_anchor

    c = floor_face_anchor(face)
    return (
        "xyz"[axis_i],
        round(span[1] - span[0], 3),
        round(s.Cylinder().Radius() * 2, 3),
        (round(c[0], 4), round(c[1], 4), round(c[2], 4)),
    )


def groove(obj=None, *, axis=None, width=None, diameter=None, at=None) -> GrooveFeature:
    """A turned / circlip groove on round stock (#148c). Either ``groove(floor_face)`` — the
    reduced-OD floor face supplies axis, width, diameter and the leader point ``at`` — or
    fully explicit ``groove(axis="z", width=3, diameter=16, at=(x, y, z))``. Called out
    ``{width} WIDE × ø{diameter}``. An object supplies *defaults*; any explicit keyword
    overrides (#451)."""
    if obj is not None:
        r_axis, r_width, r_diameter, r_at = _read_groove_face(obj)
        axis = r_axis if axis is None else axis
        width = r_width if width is None else width
        diameter = r_diameter if diameter is None else diameter
        at = r_at if at is None else at
    if width is None or diameter is None or axis is None or at is None:
        raise ValueError(
            "groove() needs a floor face, or explicit axis=, width=, diameter= and at="
        )
    axis = _norm_axis(axis)
    _require_positive(width=width, diameter=diameter)
    _require_point("at", at)
    return GrooveFeature(
        frame=Frame(origin=at, axis=axis),
        axis=axis,
        width=round(width, 3),
        diameter=round(diameter, 3),
    )


def _read_plate(obj) -> tuple[str, float, float, float, float]:
    """Read a thin slab off its bounding box: the thin (thickness) axis is the smallest span;
    ``lo``/``hi`` its extent along that axis; ``u``/``v`` the slab centre on the other two axes
    (in axis order). The slab is present material, so the bbox reads it directly."""
    bb = obj.bounding_box()
    sizes = [bb.size.X, bb.size.Y, bb.size.Z]
    i = min(range(3), key=lambda k: sizes[k])
    oi = [j for j in range(3) if j != i]
    span = ((bb.min.X, bb.max.X), (bb.min.Y, bb.max.Y), (bb.min.Z, bb.max.Z))
    c = (bb.center().X, bb.center().Y, bb.center().Z)
    return (
        "xyz"[i],
        round(span[i][0], 4),
        round(span[i][1], 4),
        round(c[oi[0]], 4),
        round(c[oi[1]], 4),
    )


def plate(obj=None, *, axis=None, lo=None, hi=None, u=None, v=None) -> PlateFeature:
    """A thin slab's thickness (#559/#577) — a base plate, an upright wall, a rib. Either
    ``plate(slab_box)`` — the thin axis, its ``lo``/``hi`` extent, and the ``u``/``v`` slab
    centre read off the object's bbox — or explicit ``plate(axis="z", lo=0, hi=4, u=10, v=5)``.
    ``hi - lo`` is the thickness; ``u``/``v`` locate the thickness dim on the other two axes
    (in axis order). An object supplies *defaults*; any explicit keyword overrides (#451)."""
    if obj is not None:
        r_axis, r_lo, r_hi, r_u, r_v = _read_plate(obj)
        axis = r_axis if axis is None else axis
        lo = r_lo if lo is None else lo
        hi = r_hi if hi is None else hi
        u = r_u if u is None else u
        v = r_v if v is None else v
    if None in (axis, lo, hi, u, v):
        raise ValueError("plate() needs a slab object, or explicit axis=, lo=, hi=, u= and v=")
    axis = _norm_axis(axis)
    if not hi > lo:
        raise ValueError(f"plate() needs hi > lo (a positive thickness); got lo={lo}, hi={hi}")
    i = "xyz".index(axis)
    oi = [j for j in range(3) if j != i]
    origin = [0.0, 0.0, 0.0]
    origin[i] = (lo + hi) / 2
    origin[oi[0]], origin[oi[1]] = u, v
    return PlateFeature(
        frame=Frame(origin=(origin[0], origin[1], origin[2]), axis=axis),
        axis=axis,
        lo=lo,
        hi=hi,
        u=u,
        v=v,
    )


def _read_step_levels(
    obj,
) -> tuple[float, tuple[float, ...], tuple[tuple[str, float], ...], Point, Point]:
    """Read a prismatic height ladder off a part: ``base`` (bbox min Z), the interior step
    ``levels`` (the shared area-filtered :func:`recognition.step_level_zs` — so the object
    flavour reads exactly the levels ``analysis.py``/``detect.py`` do, not phantom incidental
    faces, #578 review), the step ``shoulders`` (in-plane ``(axis, position)`` risers — only
    for a single-level rebate, mirroring ``model/detect.py``), the ``datum`` (bbox min corner
    the positions measure from) and ``at`` (the frame anchor — bbox centre X/Y at ``base``,
    matching ``detect.py`` so an object round-trips to the same IR)."""
    from draftwright.recognition import recognise_step_shoulders, step_level_zs

    bb = obj.bounding_box()
    base = round(bb.min.Z, 3)
    levels = tuple(sorted(round(z, 3) for z in step_level_zs(obj)))
    shoulders = (
        tuple((s.axis, s.position) for s in recognise_step_shoulders(obj, levels=list(levels)))
        if len(levels) == 1
        else ()
    )
    c = bb.center()
    return (
        base,
        levels,
        shoulders,
        (round(bb.min.X, 3), round(bb.min.Y, 3), base),
        (round(c.X, 3), round(c.Y, 3), base),
    )


def step_level(
    obj=None, *, base=None, levels=None, shoulders=None, datum=None, at=None
) -> StepLevelFeature:
    """A prismatic height ladder + step-position shoulders (#555/#578) — a rebated / stepped
    block. Either ``step_level(part)`` — ``base``, the interior ``levels``, the ``(axis,
    position)`` ``shoulders``, the ``datum`` and the frame anchor ``at`` read off the part — or
    explicit ``step_level(base=0, levels=(10,), shoulders=(("x", 30),))``. ``levels`` are the
    interior step Z-coords (unique, strictly increasing, each above ``base``); a ``shoulder`` is
    *where* a step changes height, its position measured from ``datum`` along a horizontal
    ``axis`` (x/y). ``at`` is the IR frame origin (like every sibling constructor); it defaults
    to the ``datum`` X/Y at ``base`` and is inert for step rendering. An object supplies
    *defaults*; any explicit keyword overrides that field (#451)."""
    if obj is not None:
        r_base, r_levels, r_shoulders, r_datum, r_at = _read_step_levels(obj)
        base = r_base if base is None else base
        levels = r_levels if levels is None else levels
        shoulders = r_shoulders if shoulders is None else shoulders
        datum = r_datum if datum is None else datum
        at = r_at if at is None else at
    if datum is None:
        datum = (0.0, 0.0, 0.0)
    if shoulders is None:
        shoulders = ()
    if base is None or levels is None:
        raise ValueError("step_level() needs a part, or explicit base= and levels=")
    if not (isinstance(base, (int, float)) and not isinstance(base, bool) and math.isfinite(base)):
        raise ValueError(f"step_level() base must be a number (got {base!r})")
    levels = tuple(levels)
    if not levels:
        raise ValueError("step_level() needs at least one step level above the base")
    for z in levels:
        if not (isinstance(z, (int, float)) and not isinstance(z, bool) and math.isfinite(z)):
            raise ValueError(f"step_level() level must be a number (got {z!r})")
        if not z > base:
            raise ValueError(f"step_level() level {z} must be above base {base}")
    levels = tuple(sorted(levels))
    # Levels are a correlated ladder: a duplicate or non-increasing level double-dimensions a
    # rung and skews the shoulder-suppression count — reject rather than silently accept (#578).
    for lo, hi in zip(levels, levels[1:]):
        if not hi > lo:
            raise ValueError(
                f"step_level() levels must be unique and strictly increasing: {levels}"
            )
    _require_point("datum", datum)
    if at is None:
        at = (datum[0], datum[1], base)
    _require_point("at", at)
    norm_shoulders = []
    for sh in shoulders:
        if not (isinstance(sh, (tuple, list)) and len(sh) == 2):
            raise ValueError(
                f"step_level() shoulder must be an (axis, position) pair (got {sh!r})"
            )
        ax = _norm_axis(sh[0])
        if ax == "z":  # a shoulder POSITION is horizontal; Z is the height, not a position
            raise ValueError(
                "step_level() shoulder axis must be 'x' or 'y' (a horizontal position)"
            )
        p = sh[1]
        if not (isinstance(p, (int, float)) and not isinstance(p, bool) and math.isfinite(p)):
            raise ValueError(f"step_level() shoulder position must be a number (got {p!r})")
        norm_shoulders.append((ax, float(p)))
    return StepLevelFeature(
        frame=Frame(origin=(at[0], at[1], at[2]), axis="z"),
        base=base,
        levels=levels,
        shoulders=tuple(norm_shoulders),
        datum=(datum[0], datum[1], datum[2]),
    )


def slot(
    obj=None,
    *,
    width=None,
    length=None,
    long_axis=None,
    width_axis=None,
    depth_axis=None,
    w_center=None,
    lo=None,
    hi=None,
    at=None,
) -> SlotFeature:
    """A milled slot / reduced across-flats section. From an object the depth (through, not
    stored) axis defaults to the *shortest* bbox span; the two remaining axes are read as
    long_axis (the longer) / width_axis (the shorter). ``lo``/``hi`` are the extent along the
    long axis and ``w_center`` the centre across the width axis. An object supplies *defaults*;
    any explicit keyword overrides that field (#451).

    Pass ``depth_axis=`` when the cutter's through span is *not* the shortest — a through-Z
    milled slot cut by a tall cutter has Z as its longest span, so the shortest-span default
    would mistake Z for the long axis (#490). Naming the depth axis excludes it, so long/width
    are read from the two in-plane axes."""
    if obj is not None:
        bb = obj.bounding_box()
        c = bb.center()
        # (span, min, max, centre) per axis, so every measurement is read from its RESOLVED
        # axis — not a sort position — which keeps an explicit long/width/depth override honest
        # (the pre-#490 code overrode only the axis label, not its length/lo/hi).
        by = {
            "x": (bb.size.X, bb.min.X, bb.max.X, c.X),
            "y": (bb.size.Y, bb.min.Y, bb.max.Y, c.Y),
            "z": (bb.size.Z, bb.min.Z, bb.max.Z, c.Z),
        }
        order = sorted("xyz", key=lambda a: by[a][0], reverse=True)  # longest span first
        # Normalise any explicit override up front (accept build123d's "X"/"Z" per _norm_axis)
        # BEFORE using it as a lowercase `by` key — else an uppercase override KeyErrors here.
        r_depth_axis = _norm_axis(depth_axis) if depth_axis is not None else order[-1]
        r_long_axis = _norm_axis(long_axis) if long_axis is not None else None
        r_width_axis = _norm_axis(width_axis) if width_axis is not None else None
        # Fill each unspecified in-plane axis from the spans left after removing the depth axis
        # and whatever the caller already named — longest-first. So an explicit long_axis= (or
        # width_axis=) that happens to name the shorter in-plane axis still leaves the OTHER axis
        # free for width (or long) instead of colliding into a misleading "must differ" (#490 rev).
        taken = {r_depth_axis, r_long_axis, r_width_axis} - {None}
        free = [a for a in order if a not in taken]  # unclaimed, depth excluded, longest-first
        if r_long_axis is None:
            r_long_axis = free.pop(0)
        if r_width_axis is None:
            r_width_axis = free.pop(0)
        # Warn on a genuinely ambiguous read. Roles (long, width, depth) are assigned purely by
        # span magnitude, so ANY two *auto* axes (neither caller-pinned) with near-equal spans are
        # a silent coin-flip — a tiny perturbation swaps their roles. A caller-pinned axis fixes
        # its own role, so only the still-unnamed axes can flip. Check every pair of auto axes, not
        # just order-adjacent spans: pinning the MIDDLE span leaves the two OUTER axes auto and
        # non-adjacent, yet their long/width (or width/depth) split is still a tie.
        pinned = {_norm_axis(a) for a in (long_axis, width_axis, depth_axis) if a is not None}
        auto = [a for a in order if a not in pinned]
        auto_pairs = [
            (auto[i], auto[j]) for i in range(len(auto)) for j in range(i + 1, len(auto))
        ]
        if any(
            math.isclose(by[a][0], by[b][0], rel_tol=_SLOT_AMBIGUOUS_FRAC) for a, b in auto_pairs
        ):
            # stacklevel=2 attributes a direct declare.slot() call; via the Sheet.slot
            # forwarder it lands one frame shallow, but no single value serves both entry
            # points and the message is self-contained (it names the fix).
            warnings.warn(
                f"slot() object has near-equal bbox spans (x={by['x'][0]:.3g}, "
                f"y={by['y'][0]:.3g}, z={by['z'][0]:.3g}); the long/width/depth axis read is "
                "ambiguous — pass long_axis=/width_axis=/depth_axis= to disambiguate",
                stacklevel=2,
            )
        long_axis = r_long_axis
        width_axis = r_width_axis
        length = by[r_long_axis][0] if length is None else length
        lo = by[r_long_axis][1] if lo is None else lo
        hi = by[r_long_axis][2] if hi is None else hi
        width = by[r_width_axis][0] if width is None else width
        w_center = by[r_width_axis][3] if w_center is None else w_center
        at = (c.X, c.Y, c.Z) if at is None else at
    if None in (width, length, long_axis, width_axis, lo, hi):
        raise ValueError(
            "slot() needs an object, or explicit width=, length=, long_axis=, width_axis=, lo= and hi="
        )
    long_axis = _norm_axis(long_axis)
    width_axis = _norm_axis(width_axis)
    if long_axis == width_axis:
        raise ValueError(f"slot() long_axis and width_axis must differ (both {long_axis!r})")
    if depth_axis is not None and _norm_axis(depth_axis) in (long_axis, width_axis):
        raise ValueError(
            f"slot() depth_axis must differ from long_axis/width_axis (got {depth_axis!r})"
        )
    _require_positive(width=width, length=length)
    if not lo < hi:
        raise ValueError(f"slot() needs lo < hi (got lo={lo!r}, hi={hi!r})")
    if not math.isclose(hi - lo, length, rel_tol=1e-6, abs_tol=1e-6):
        raise ValueError(f"slot() length={length!r} must equal hi - lo ({hi - lo!r})")
    w_center = 0.0 if w_center is None else w_center
    if at is None:
        # Centre on the long axis at the slot midpoint; other coords irrelevant to the size dims.
        origin = [0.0, 0.0, 0.0]
        origin["xyz".index(long_axis)] = (lo + hi) / 2
        origin["xyz".index(width_axis)] = w_center
        at = (origin[0], origin[1], origin[2])
    return SlotFeature(
        frame=Frame(origin=at, axis=long_axis),
        width_axis=width_axis,
        long_axis=long_axis,
        width=width,
        length=length,
        w_center=w_center,
        lo=lo,
        hi=hi,
    )


def pocket(
    obj=None,
    *,
    width=None,
    length=None,
    depth=None,
    long_axis=None,
    width_axis=None,
    depth_axis=None,
    w_center=None,
    lo=None,
    hi=None,
    at=None,
) -> PocketFeature:
    """A blind rectangular recess — a floored slot/pocket, dimensioned width × length ×
    depth (#148a). The blind counterpart of :func:`slot`: unlike a through-slot the depth
    IS a stored size, read from the object's span along the ``depth_axis``. From an object
    the depth axis defaults to the *shortest* bbox span (a shallow recess); the two
    remaining axes are long_axis (the longer) / width_axis (the shorter). Pass
    ``depth_axis=`` when the recess is deeper than it is wide (#490-style). ``lo``/``hi`` are
    the extent along the long axis and ``w_center`` the centre across the width axis. An
    object supplies *defaults*; any explicit keyword overrides that field."""
    if obj is not None:
        bb = obj.bounding_box()
        c = bb.center()
        by = {
            "x": (bb.size.X, bb.min.X, bb.max.X, c.X),
            "y": (bb.size.Y, bb.min.Y, bb.max.Y, c.Y),
            "z": (bb.size.Z, bb.min.Z, bb.max.Z, c.Z),
        }
        order = sorted("xyz", key=lambda a: by[a][0], reverse=True)  # longest span first
        r_depth_axis = _norm_axis(depth_axis) if depth_axis is not None else order[-1]
        r_long_axis = _norm_axis(long_axis) if long_axis is not None else None
        r_width_axis = _norm_axis(width_axis) if width_axis is not None else None
        taken = {r_depth_axis, r_long_axis, r_width_axis} - {None}
        free = [a for a in order if a not in taken]  # unclaimed, depth excluded, longest-first
        if r_long_axis is None:
            r_long_axis = free.pop(0)
        if r_width_axis is None:
            r_width_axis = free.pop(0)
        # A pocket has three distinct-role spans; any two *auto* (neither caller-pinned) axes
        # with near-equal spans make the role read a silent coin-flip — same guard as slot().
        pinned = {_norm_axis(a) for a in (long_axis, width_axis, depth_axis) if a is not None}
        auto = [a for a in order if a not in pinned]
        auto_pairs = [
            (auto[i], auto[j]) for i in range(len(auto)) for j in range(i + 1, len(auto))
        ]
        if any(
            math.isclose(by[a][0], by[b][0], rel_tol=_SLOT_AMBIGUOUS_FRAC) for a, b in auto_pairs
        ):
            warnings.warn(
                f"pocket() object has near-equal bbox spans (x={by['x'][0]:.3g}, "
                f"y={by['y'][0]:.3g}, z={by['z'][0]:.3g}); the long/width/depth axis read is "
                "ambiguous — pass long_axis=/width_axis=/depth_axis= to disambiguate",
                stacklevel=2,
            )
        long_axis = r_long_axis
        width_axis = r_width_axis
        length = by[r_long_axis][0] if length is None else length
        lo = by[r_long_axis][1] if lo is None else lo
        hi = by[r_long_axis][2] if hi is None else hi
        width = by[r_width_axis][0] if width is None else width
        w_center = by[r_width_axis][3] if w_center is None else w_center
        depth = by[r_depth_axis][0] if depth is None else depth
        at = (c.X, c.Y, c.Z) if at is None else at
    if None in (width, length, depth, long_axis, width_axis, lo, hi):
        raise ValueError(
            "pocket() needs an object, or explicit width=, length=, depth=, "
            "long_axis=, width_axis=, lo= and hi="
        )
    long_axis = _norm_axis(long_axis)
    width_axis = _norm_axis(width_axis)
    if long_axis == width_axis:
        raise ValueError(f"pocket() long_axis and width_axis must differ (both {long_axis!r})")
    if depth_axis is not None and _norm_axis(depth_axis) in (long_axis, width_axis):
        raise ValueError(
            f"pocket() depth_axis must differ from long_axis/width_axis (got {depth_axis!r})"
        )
    _require_positive(width=width, length=length, depth=depth)
    if not lo < hi:
        raise ValueError(f"pocket() needs lo < hi (got lo={lo!r}, hi={hi!r})")
    if not math.isclose(hi - lo, length, rel_tol=1e-6, abs_tol=1e-6):
        raise ValueError(f"pocket() length={length!r} must equal hi - lo ({hi - lo!r})")
    w_center = 0.0 if w_center is None else w_center
    if at is None:
        origin = [0.0, 0.0, 0.0]
        origin["xyz".index(long_axis)] = (lo + hi) / 2
        origin["xyz".index(width_axis)] = w_center
        at = (origin[0], origin[1], origin[2])
    return PocketFeature(
        frame=Frame(origin=at, axis=long_axis),
        width_axis=width_axis,
        long_axis=long_axis,
        width=width,
        length=length,
        depth=depth,
        w_center=w_center,
        lo=lo,
        hi=hi,
    )


def _plane_axes(axis: str) -> tuple[Point, Point]:
    """The two in-plane unit directions for a pattern lying perpendicular to *axis*."""
    return {
        "x": ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        "y": ((0.0, 0.0, 1.0), (1.0, 0.0, 0.0)),
        "z": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
    }[axis]


def _pattern_members(
    kind, center: Point, axis: str, count, *, bcd, pitch, direction, grid, rows, cols, angle
) -> tuple[Point, ...]:
    """The member-hole centres for an arrangement, so a declared pattern is shaped like a
    detected one (``detect._pattern_feature`` always populates ``members``; the balloon /
    BCD / pitch furniture index them). Points are laid out about *center* — matching the
    detector's convention that a bolt-circle / grid frame origin is the pattern centre."""
    u, v = _plane_axes(axis)

    def pt(du: float, dv: float) -> Point:
        return tuple(center[k] + du * u[k] + dv * v[k] for k in range(3))  # type: ignore[return-value]

    if kind == "bolt_circle":
        r = (bcd or 0.0) / 2
        a0 = math.radians(angle or 0.0)
        return tuple(
            pt(
                r * math.cos(a0 + 2 * math.pi * i / count),
                r * math.sin(a0 + 2 * math.pi * i / count),
            )
            for i in range(count)
        )
    if kind == "linear":
        d = direction or u
        n = math.sqrt(sum(c * c for c in d)) or 1.0
        d = tuple(c / n for c in d)
        p = pitch or 0.0
        return tuple(
            tuple(center[k] + (i - (count - 1) / 2) * p * d[k] for k in range(3))
            for i in range(count)
        )
    if kind == "grid":
        rp, cp = grid or (0.0, 0.0)
        a0 = math.radians(angle or 0.0)
        pts = []
        for rr in range(rows or 1):
            for cc in range(cols or 1):
                gx = (cc - ((cols or 1) - 1) / 2) * cp
                gy = (rr - ((rows or 1) - 1) / 2) * rp
                pts.append(
                    pt(
                        gx * math.cos(a0) - gy * math.sin(a0),
                        gx * math.sin(a0) + gy * math.cos(a0),
                    )
                )
        return tuple(pts)
    return ()  # "other" — no defined arrangement; the caller must pass members explicitly


def pattern(
    member: HoleFeature,
    *,
    kind,
    count,
    at=None,
    axis=None,
    members=(),
    bcd=None,
    pitch=None,
    direction=None,
    grid=None,
    rows=None,
    cols=None,
    angle=None,
) -> PatternFeature:
    """A hole pattern = ``count`` × a *member* hole (build one with :func:`hole`). The
    arrangement (``bolt_circle`` / ``linear`` / ``grid``) and its defining dims (``bcd`` /
    ``pitch`` / ``grid`` + ``rows``/``cols``/``angle``) are supplied, not read.

    ``at`` (the pattern **centre**) and ``axis`` default to the member's frame. The member
    centres are computed from the arrangement so the pattern renders like a detected one
    (its ``count×`` balloon, BCD centreline and pitch dims anchor on ``members``); pass
    ``members=`` explicitly to override the computed layout (required for ``kind="other"``)."""
    axis = _norm_axis(axis or member.frame.axis)
    center = at if at is not None else member.frame.origin
    _require_point("at", center)
    members = tuple(members)
    for m in members:
        _require_point("members", m)

    # Validate the arrangement up front, *whether or not* members are supplied: the furniture
    # pass reads bcd/pitch/grid to draw the BCD centreline / pitch / grid dims, so a known
    # rendered kind needs its defining dim even when the caller overrides the member layout
    # (a missing or zero dim else crashes the furniture, or collapses computed members onto
    # the centre). Only 'other' — a bare group with no arrangement furniture — is exempt, and
    # it must carry explicit members. Fail loudly, matching the hole/boss/step/slot guards.
    if kind not in ("bolt_circle", "linear", "grid", "other"):
        raise ValueError(
            f"pattern(kind={kind!r}) is not a known arrangement (bolt_circle / linear / grid / other)"
        )
    _require_count("pattern()", count)
    if members and len(members) != count:
        raise ValueError(f"pattern() count={count} must equal len(members)={len(members)}")

    if kind == "bolt_circle":
        _positive("pattern(kind='bolt_circle') bcd=", bcd)  # furniture reads it even w/ members=
    elif kind == "linear":
        _positive("pattern(kind='linear') pitch=", pitch)  # pitch dim reads it even w/ members=
        if direction is not None:
            _require_point("direction", direction)  # a (dx, dy, dz) triple of numbers
            if not any(direction):
                raise ValueError("pattern(kind='linear') direction= must be nonzero")
    elif kind == "grid":
        if grid is None or rows is None or cols is None:
            raise ValueError("pattern(kind='grid') needs grid= pitch and rows= and cols=")
        if not (isinstance(grid, (tuple, list)) and len(grid) == 2):
            raise ValueError(
                f"pattern() grid= must be a (row_pitch, col_pitch) pair (got {grid!r})"
            )
        _positive("pattern() grid row pitch", grid[0])
        _positive("pattern() grid col pitch", grid[1])
        if not (isinstance(rows, int) and isinstance(cols, int) and rows >= 1 and cols >= 1):
            raise ValueError(
                f"pattern() rows= and cols= must be positive ints (got rows={rows!r}, cols={cols!r})"
            )
        if rows * cols != count:
            raise ValueError(
                f"pattern(kind='grid') needs rows*cols == count ({rows}*{cols} != {count})"
            )
    elif kind == "other" and not members:
        raise ValueError("pattern(kind='other') needs explicit members=")

    if not members:
        members = _pattern_members(
            kind,
            center,
            axis,
            count,
            bcd=bcd,
            pitch=pitch,
            direction=direction,
            grid=grid,
            rows=rows,
            cols=cols,
            angle=angle,
        )
    return PatternFeature(
        frame=Frame(origin=center, axis=axis),
        pattern=kind,
        count=count,
        member=member,
        members=members,
        bcd=bcd,
        pitch=pitch,
        direction=direction,
        grid=grid,
        rows=rows,
        cols=cols,
        angle=angle,
    )


def pocket_pattern(
    member: PocketFeature,
    *,
    kind="linear",
    count,
    at=None,
    members=(),
    pitch=None,
    direction=None,
    grid=None,
    rows=None,
    cols=None,
    angle=None,
) -> PocketPatternFeature:
    """``count`` × an identical blind pocket in a ``linear`` / ``grid`` array (#841) — the
    recess analog of :func:`pattern`. *member* is one representative pocket (build it with
    :func:`pocket`); the array renders as ONE grouped ``N× W × L × D DEEP`` callout plus the
    ``(n-1)× pitch`` dim(s), instead of N competing size dims.

    The arrangement lies in the pocket's OPENING plane (perpendicular to its depth axis), so
    the members are laid out about *at* (default the member's own centre) in that plane —
    pass ``members=`` to override the computed layout. ``pitch`` (linear) / ``grid`` +
    ``rows``/``cols`` (grid) define the spacing and are read by the pitch-dim furniture."""
    axis = member.depth_axis  # the opening normal — the plane the array lies in
    axis_idx = {"x": 0, "y": 1, "z": 2}[axis]
    center = at if at is not None else member.frame.origin
    _require_point("at", center)
    members = tuple(members)
    for m in members:
        _require_point("members", m)

    if kind not in ("linear", "grid"):
        raise ValueError(
            f"pocket_pattern(kind={kind!r}) is not a known arrangement (linear / grid)"
        )
    _require_count("pocket_pattern()", count)
    if members and len(members) != count:
        raise ValueError(f"pocket_pattern() count={count} must equal len(members)={len(members)}")
    # The arrangement lies in the pocket's OPENING plane (perpendicular to its depth axis) —
    # explicit members must be coplanar in that plane, or the grouped callout would claim an
    # in-plane array while the members march into the material (Codex #848).
    if members:
        depths = [m[axis_idx] for m in members]
        if max(depths) - min(depths) > 1e-6:
            raise ValueError(
                "pocket_pattern() members must lie in the opening plane (equal "
                f"{axis}-depth); got depths spanning {max(depths) - min(depths):.3g}"
            )

    if kind == "linear":
        _positive("pocket_pattern(kind='linear') pitch=", pitch)  # the pitch dim reads it
        if direction is not None:
            _require_point("direction", direction)
            if not any(direction):
                raise ValueError("pocket_pattern(kind='linear') direction= must be nonzero")
            if abs(direction[axis_idx]) > 1e-9:  # same opening-plane constraint (Codex #848)
                raise ValueError(
                    "pocket_pattern(kind='linear') direction= must lie in the opening plane "
                    f"(no {axis}-depth component)"
                )
    else:  # grid
        if grid is None or rows is None or cols is None:
            raise ValueError("pocket_pattern(kind='grid') needs grid= pitch and rows= and cols=")
        if not (isinstance(grid, (tuple, list)) and len(grid) == 2):
            raise ValueError(
                f"pocket_pattern() grid= must be a (row_pitch, col_pitch) pair (got {grid!r})"
            )
        _positive("pocket_pattern() grid row pitch", grid[0])
        _positive("pocket_pattern() grid col pitch", grid[1])
        if not (isinstance(rows, int) and isinstance(cols, int) and rows >= 1 and cols >= 1):
            raise ValueError(
                f"pocket_pattern() rows= and cols= must be positive ints (got rows={rows!r}, cols={cols!r})"
            )
        if rows * cols != count:
            raise ValueError(
                f"pocket_pattern(kind='grid') needs rows*cols == count ({rows}*{cols} != {count})"
            )

    if not members:
        members = _pattern_members(
            kind,
            center,
            axis,
            count,
            bcd=None,
            pitch=pitch,
            direction=direction,
            grid=grid,
            rows=rows,
            cols=cols,
            angle=angle,
        )
    return PocketPatternFeature(
        frame=Frame(origin=center, axis=axis),
        pattern=kind,
        count=count,
        member=member,
        members=members,
        pitch=pitch,
        direction=direction,
        grid=grid,
        rows=rows,
        cols=cols,
        angle=angle,
    )


def envelope(obj) -> EnvelopeFeature:
    """The overall bounding box of *obj* as width (X) / height (Z) / depth (Y),
    matching the detector's prismatic envelope."""
    bb = obj.bounding_box()
    c = bb.center()
    return EnvelopeFeature(
        frame=Frame((c.X, c.Y, bb.min.Z), "z"),
        width=bb.size.X,
        height=bb.size.Z,
        depth=bb.size.Y,
        bbox_min=(bb.min.X, bb.min.Y, bb.min.Z),
        bbox_max=(bb.max.X, bb.max.Y, bb.max.Z),
    )


# -- GD&T aspect targets (ADR 0011 P2c, #479) -------------------------------------
# The P2b IR items (ControlFrame/DatumRef/Finish) carry (view, side, site) explicitly.
# These constructors DERIVE that target from a reference — an IR feature (site = its axis)
# or a build123d planar face (site = its centre, axis = its normal) — the geometric work
# P2b deferred. Derivation runs at declaration time (no Analysis), so it is purely
# geometric; `view=`/`side=` overrides always win (a best-effort default + an escape hatch).

# A feature's axis points AT the viewer in this view (a z-hole is a circle in plan).
_FACE_ON_VIEW = {"x": "side", "y": "front", "z": "plan"}
# A planar face whose normal is this axis shows as an EDGE here (prefer front, else side).
_EDGE_ON_VIEW = {"x": "front", "y": "side", "z": "front"}
# Default strip side for a FEATURE target, per its face-on view — the empirically roomiest
# one: the plan's below strip always carries the overall-width envelope dim (so use above),
# while the front/side above strips are the shallow gaps between stacked views (so use below).
# A congested default still drops-with-warning; ``side=`` overrides. Fallthrough is #481.
_FEATURE_SIDE = {"plan": "above", "front": "below", "side": "below"}
# The model-space axis a view stacks its above/below strips along (its vertical).
_VERTICAL_MODEL_AXIS = {"plan": 1, "front": 2, "side": 2}
_VALID_SIDES = ("above", "below", "left", "right")


def _axis_from_vec(v) -> str:
    """The dominant axis letter of a direction vector, or a clear error if it is not
    axis-aligned (a skew face has no letter-based Frame — pass an explicit ``view``/``axis``)."""
    comps = [abs(v.X), abs(v.Y), abs(v.Z)]
    k = max(range(3), key=lambda i: comps[i])
    others = sum(comps) - comps[k]
    if comps[k] < 1e-6 or others > 0.1 * comps[k]:
        raise ValueError(
            "GD&T target face is not axis-aligned — pass an explicit view=/side= (or use an "
            f"axis-aligned face); normal was ({v.X:.3g}, {v.Y:.3g}, {v.Z:.3g})"
        )
    return "xyz"[k]


def _side_for(site: Point, part, view: str) -> str:
    """Default strip side for a face target: above/below by the site's position vs the part
    centre along the view's vertical axis (mirrors render_pmi's centre comparison)."""
    vi = _VERTICAL_MODEL_AXIS[view]
    c = part.bounding_box().center()
    return "above" if site[vi] >= (c.X, c.Y, c.Z)[vi] else "below"


def gdt_target(ref, part=None, *, view=None, side=None) -> tuple[str, str, Point, str]:
    """Resolve a GD&T target *ref* to ``(view, side, site, axis)``.

    *ref* is either an IR :class:`~draftwright.model.ir.Feature` (site = its ``frame.origin``,
    axis = its ``frame.axis``, placed face-on and below by default) or a build123d **planar
    face** (site = its centre, axis = its normal, placed edge-on with the side by position).
    *part* is needed only to derive a face's side (skip with an explicit ``side``).
    ``view``/``side`` override the derived defaults."""
    if isinstance(ref, Feature):
        site = ref.frame.origin
        axis = _norm_axis(ref.frame.axis)
        d_view = _FACE_ON_VIEW[axis]
        d_side = _FEATURE_SIDE[d_view]
    else:  # a build123d planar face
        try:
            n, c = ref.normal_at(), ref.center()
        except AttributeError as e:
            raise ValueError("GD&T target must be an IR feature or a build123d planar face") from e
        axis = _axis_from_vec(n)
        site = (c.X, c.Y, c.Z)
        d_view = _EDGE_ON_VIEW[axis]
        d_side = side or (_side_for(site, part, view or d_view) if part is not None else "below")
    v, s = view or d_view, side or d_side
    if v not in ("plan", "front", "side"):
        raise ValueError(f"view must be 'plan'/'front'/'side' (got {v!r})")
    if s not in _VALID_SIDES:
        raise ValueError(f"side must be one of {_VALID_SIDES} (got {s!r})")
    return v, s, site, axis


def datum(letter: str, ref, part=None, *, view=None, side=None) -> DatumRef:
    """A datum feature symbol (ISO 5459) on *ref* — a feature or a planar face (ADR 0011 P2c)."""
    if not (isinstance(letter, str) and letter.strip()):
        raise ValueError(f"datum needs a non-empty letter (got {letter!r})")
    v, s, site, axis = gdt_target(ref, part, view=view, side=side)
    origin = ref if isinstance(ref, Feature) else None
    return DatumRef(frame=Frame(site, axis), letter=letter.strip(), view=v, side=s, origin=origin)


def finish(ra, ref, part=None, *, view=None, side=None) -> Finish:
    """A surface-finish symbol (ISO 1302, Ra) on *ref* — a feature or a planar face (P2c)."""
    ra = str(ra).strip()
    if not ra:
        raise ValueError("finish needs a roughness value (e.g. '3.2')")
    v, s, site, axis = gdt_target(ref, part, view=view, side=side)
    origin = ref if isinstance(ref, Feature) else None
    return Finish(frame=Frame(site, axis), ra=ra, view=v, side=s, origin=origin)


def note(text, ref, part=None, *, view=None, side=None) -> Note:
    """A free-text manufacturing note (#488) on a leader to *ref* — a feature or a planar face
    (ADR 0011 P2c). The shop callouts detection can't infer: thread specs (``M3x0.5 TAP``),
    ``DEBURR``, chip-relief, knurl. Placed like the GD&T items (a first-class ADR 0009 corridor
    candidate), not the dimension planner."""
    text = str(text).strip()
    if not text:
        raise ValueError("note needs text (e.g. 'M3x0.5 TAP')")
    v, s, site, axis = gdt_target(ref, part, view=view, side=side)
    origin = ref if isinstance(ref, Feature) else None
    return Note(frame=Frame(site, axis), text=text, view=v, side=s, origin=origin)


def control_frame(
    characteristic: str,
    tolerance,
    ref,
    part=None,
    *,
    datums=(),
    diameter=False,
    modifier=None,
    view=None,
    side=None,
) -> ControlFrame:
    """A geometric-tolerance feature control frame (ISO 1101) on *ref* — a feature or a planar
    face (ADR 0011 P2c.2). *characteristic* is a lowercase ISO 1101 name (``"position"`` …);
    *datums* the referenced datum letters; *diameter* prefixes the zone with ``⌀``; *modifier*
    a material-condition symbol (``"M"``/``"L"``/``"P"``)."""
    tol = str(tolerance).strip()
    if not tol:
        raise ValueError("control frame needs a tolerance value")
    v, s, site, axis = gdt_target(ref, part, view=view, side=side)
    origin = ref if isinstance(ref, Feature) else None
    return ControlFrame(
        frame=Frame(site, axis),
        characteristic=str(characteristic),
        tolerance=tol,
        view=v,
        side=s,
        datums=tuple(str(d).strip() for d in datums),
        diameter=bool(diameter),
        modifier=modifier,
        origin=origin,
    )


def _point3(name: str, p) -> Point:
    vals = tuple(float(c) for c in p)
    if len(vals) != 3:
        raise ValueError(f"dimension() {name} must be a 3-tuple")
    return (vals[0], vals[1], vals[2])


def authored_dimension(
    *,
    kind: str,
    value: float,
    label: str,
    dominant_axis: str,
    ref_pts,
    ref_bbox=None,
    at=None,
    axis: str | None = None,
    upper_tol: float | None = None,
    lower_tol: float | None = None,
    source: str = "sheet",
    source_kind: str | None = None,
) -> AuthoredDimension:
    """A pre-authored drafting dimension from explicit measured values — the IR constructor
    behind :meth:`Sheet.dimension` (#704: extracted so ``build_drawing(model=…)`` callers can
    author one without the façade). Validates the kind against
    :data:`~draftwright.model.ir.AUTHORED_DIMENSION_KINDS`, needs ≥2 ``ref_pts``, and derives
    ``at`` (the ``ref_bbox`` centre, else the ``ref_pts`` centroid) when not given."""
    _require_positive(value=value)
    dim_kind = str(kind).lower()
    if dim_kind not in AUTHORED_DIMENSION_KINDS:
        allowed = ", ".join(sorted(AUTHORED_DIMENSION_KINDS))
        raise ValueError(f"dimension() kind must be one of: {allowed}")
    pts = tuple(_point3("ref_pts item", p) for p in ref_pts)
    if len(pts) < 2:
        raise ValueError("dimension() needs at least two ref_pts")
    bbox = None if ref_bbox is None else tuple(float(c) for c in ref_bbox)
    if bbox is not None and len(bbox) != 6:
        raise ValueError("dimension() ref_bbox must be a 6-tuple")
    dom = str(dominant_axis).upper()
    if dom not in ("X", "Y", "Z"):
        if not (dom == "?" and dim_kind in ("diameter", "radius") and bbox is not None):
            raise ValueError("dimension() dominant_axis must be X, Y, or Z")
    if at is None:
        if bbox is not None:
            x0, y0, z0, x1, y1, z1 = bbox
            at = ((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2)
        else:
            n = len(pts)
            at = tuple(sum(p[i] for p in pts) / n for i in range(3))
    origin = _point3("at", at)
    ax = _norm_axis(axis or (dom.lower() if dom in ("X", "Y", "Z") else "z"))
    return AuthoredDimension(
        frame=Frame(origin, ax),
        dimension_kind=dim_kind,
        value=float(value),
        label=str(label),
        dominant_axis=dom,
        upper_tol=upper_tol,
        lower_tol=lower_tol,
        ref_bbox=bbox,
        ref_pts=pts,
        source=source,
        source_kind=source_kind or dim_kind,
    )
