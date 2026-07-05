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
    along the hole *axis* from the part's open face to the tool's inner (floor) face — so both
    values track the geometry you built, restating no numbers. The counterbore is taken to open
    on whichever end face of *axis* the tool sits nearer (a top counterbore reads its depth from
    the top face down to the counterbore floor, even when the tool overhangs the part)."""
    axis = _norm_axis(axis)
    dia = _read_cylinder(tool)[1]
    i = "xyz".index(axis)
    pb, tb = part.bounding_box(), tool.bounding_box()
    p_lo, p_hi = [pb.min.X, pb.min.Y, pb.min.Z][i], [pb.max.X, pb.max.Y, pb.max.Z][i]
    t_lo, t_hi = [tb.min.X, tb.min.Y, tb.min.Z][i], [tb.max.X, tb.max.Y, tb.max.Z][i]
    # open on the +axis face when the tool sits toward it, else the -axis face.
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
    count=1,
    members=(),
) -> HoleFeature:
    """A drilled hole. Either ``hole(tool_cylinder)`` — read ⌀ / axis / location off the
    build123d object you subtracted — or ``hole(diameter=6, at=(20, 10, 0), axis="z")``.

    ``cbore`` / ``spotface`` are ``(diameter, depth)`` pairs; ``count`` + ``members``
    describe a machining-spec group drawn as one ``count×`` callout.

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
    )


def boss(obj=None, *, diameter=None, at=None, axis=None) -> BossFeature:
    """An external cylindrical boss / OD. Either ``boss(cylinder)`` or
    ``boss(diameter=6, at=(0, 0, 0), axis="x")`` (parametric). An object supplies
    *defaults*; any explicit keyword overrides that field (#451)."""
    if obj is not None:
        r_axis, r_diameter, r_at = _read_cylinder(obj)
        axis = r_axis if axis is None else axis
        diameter = r_diameter if diameter is None else diameter
        at = r_at if at is None else at
    if diameter is None or at is None or axis is None:
        raise ValueError("boss() needs an object, or explicit diameter=, at= and axis=")
    axis = _norm_axis(axis)
    _require_positive(diameter=diameter)
    _require_point("at", at)
    return BossFeature(frame=Frame(origin=at, axis=axis), diameter=diameter)


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


def slot(
    obj=None,
    *,
    width=None,
    length=None,
    long_axis=None,
    width_axis=None,
    w_center=None,
    lo=None,
    hi=None,
    at=None,
) -> SlotFeature:
    """A milled slot / reduced across-flats section. From an object the three bbox spans
    are read as long_axis (longest) / width_axis (middle) / depth (shortest, not stored);
    ``lo``/``hi`` are the extent along the long axis and ``w_center`` the centre across the
    width axis. An object supplies *defaults*; any explicit keyword overrides that field (#451).

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
        (r_long_axis, r_length, r_lo, r_hi, _), (r_width_axis, r_width, _, _, r_w_center), _ = (
            spans
        )
        long_axis = r_long_axis if long_axis is None else long_axis
        width_axis = r_width_axis if width_axis is None else width_axis
        length = r_length if length is None else length
        width = r_width if width is None else width
        lo = r_lo if lo is None else lo
        hi = r_hi if hi is None else hi
        w_center = r_w_center if w_center is None else w_center
        at = (c.X, c.Y, c.Z) if at is None else at
    if None in (width, length, long_axis, width_axis, lo, hi):
        raise ValueError(
            "slot() needs an object, or explicit width=, length=, long_axis=, width_axis=, lo= and hi="
        )
    long_axis = _norm_axis(long_axis)
    width_axis = _norm_axis(width_axis)
    if long_axis == width_axis:
        raise ValueError(f"slot() long_axis and width_axis must differ (both {long_axis!r})")
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
