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

from draftwright.model.ir import (
    BossFeature,
    EnvelopeFeature,
    Frame,
    HoleFeature,
    PatternFeature,
    Point,
    SlotFeature,
    StepFeature,
)


def _norm_axis(axis: str) -> str:
    """Normalise a user-supplied axis letter to the lowercase ``{x,y,z}`` the IR uses.
    build123d callers naturally reach for ``"X"``/``"Z"`` (à la ``Axis.X``); the
    lowercase-letter convention is an IR-internal detail, so accept either and fail
    clearly on anything else rather than crashing deep in ``"xyz".index(...)``."""
    a = str(axis).lower()
    if a not in ("x", "y", "z"):
        raise ValueError(f"axis must be one of 'x'/'y'/'z' (got {axis!r})")
    return a


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
    count=1,
    members=(),
) -> HoleFeature:
    """A drilled hole. Either ``hole(tool_cylinder)`` — read ⌀ / axis / location off the
    build123d object you subtracted — or ``hole(diameter=6, at=(20, 10, 0), axis="z")``.

    ``cbore`` / ``spotface`` are ``(diameter, depth)`` pairs; ``count`` + ``members``
    describe a machining-spec group drawn as one ``count×`` callout."""
    if obj is not None:
        axis, diameter, at = _read_cylinder(obj)
    if diameter is None or at is None or axis is None:
        raise ValueError("hole() needs an object, or explicit diameter=, at= and axis=")
    axis = _norm_axis(axis)
    return HoleFeature(
        frame=Frame(origin=at, axis=axis),
        diameter=diameter,
        depth=depth,
        through=through,
        count=count,
        members=tuple(members),
        cbore=cbore,
        spotface=spotface,
    )


def boss(obj=None, *, diameter=None, at=None, axis=None) -> BossFeature:
    """An external cylindrical boss / OD. Either ``boss(cylinder)`` or
    ``boss(diameter=6, at=(0, 0, 0), axis="x")`` (parametric)."""
    if obj is not None:
        axis, diameter, at = _read_cylinder(obj)
    if diameter is None or at is None or axis is None:
        raise ValueError("boss() needs an object, or explicit diameter=, at= and axis=")
    axis = _norm_axis(axis)
    return BossFeature(frame=Frame(origin=at, axis=axis), diameter=diameter)


def step(obj=None, *, diameter=None, length=None, at=None, axis=None, span=None) -> StepFeature:
    """One axial segment of a turned profile — its OD + length. Either ``step(segment)``
    (⌀ from the cylindrical face, length + centre from the bbox along its axis) or
    explicit ``step(diameter=4, length=10, at=(0, 0, 0), axis="x")``. ``span`` (the two
    axial end-points) is derived from ``at`` + ``length`` when not given."""
    if obj is not None:
        axis, diameter, at = _read_cylinder(obj)
        bb = obj.bounding_box()
        length = [bb.size.X, bb.size.Y, bb.size.Z]["xyz".index(axis)]
    if diameter is None or length is None or at is None or axis is None:
        raise ValueError("step() needs an object, or explicit diameter=, length=, at= and axis=")
    axis = _norm_axis(axis)
    if span is None:
        span = _span(at, axis, length)
    return StepFeature(
        frame=Frame(origin=at, axis=axis), length=length, diameter=diameter, span=span
    )


def slot(
    obj=None,
    *,
    width=None,
    length=None,
    long_axis=None,
    width_axis=None,
    w_center=0.0,
    lo=None,
    hi=None,
    at=None,
) -> SlotFeature:
    """A milled slot / reduced across-flats section. From an object the three bbox spans
    are read as long_axis (longest) / width_axis (middle) / depth (shortest, not stored);
    ``lo``/``hi`` are the extent along the long axis and ``w_center`` the centre across the
    width axis. Explicit values override any read.

    Caveat: the object read assumes width > depth (a slot wider than it is deep). For a
    slot cut *deeper than it is wide* the middle/shortest spans swap — pass explicit
    ``width_axis=``/``width=`` for that case."""
    if obj is not None:
        bb = obj.bounding_box()
        c = bb.center()
        spans = sorted(
            (
                ("x", bb.size.X, bb.min.X, bb.max.X, c.X),
                ("y", bb.size.Y, bb.min.Y, bb.max.Y, c.Y),
                ("z", bb.size.Z, bb.min.Z, bb.max.Z, c.Z),
            ),
            key=lambda s: s[1],
            reverse=True,
        )
        (long_axis, length, lo, hi, _), (width_axis, width, _, _, w_center), _ = spans
        at = (c.X, c.Y, c.Z)
    if None in (width, length, long_axis, width_axis, lo, hi):
        raise ValueError(
            "slot() needs an object, or explicit width=, length=, long_axis=, width_axis=, lo= and hi="
        )
    long_axis = _norm_axis(long_axis)
    width_axis = _norm_axis(width_axis)
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
    members = tuple(members)

    # Validate the arrangement up front, *whether or not* members are supplied: the furniture
    # pass reads bcd/pitch/grid to draw the BCD centreline / pitch / grid dims, so a known
    # rendered kind needs its defining dim even when the caller overrides the member layout
    # (a missing or zero dim else crashes the furniture, or collapses computed members onto
    # the centre). Only 'other' — a bare group with no arrangement furniture — is exempt, and
    # it must carry explicit members. Fail loudly, matching the hole/boss/step/slot guards.
    if kind == "bolt_circle" and not bcd:
        raise ValueError("pattern(kind='bolt_circle') needs a nonzero bcd= (or explicit members=)")
    elif kind == "linear" and not pitch:
        raise ValueError("pattern(kind='linear') needs a nonzero pitch= (or explicit members=)")
    elif kind == "grid" and (not grid or not all(grid) or rows is None or cols is None):
        raise ValueError(
            "pattern(kind='grid') needs a nonzero grid= pitch and rows= and cols= "
            "(or explicit members=)"
        )
    elif kind == "other" and not members:
        raise ValueError("pattern(kind='other') needs explicit members=")
    elif kind not in ("bolt_circle", "linear", "grid", "other"):
        raise ValueError(
            f"pattern(kind={kind!r}) is not a known arrangement (bolt_circle / linear / grid / other)"
        )
    if count < 1:
        raise ValueError(f"pattern() needs count >= 1 (got {count!r})")

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
