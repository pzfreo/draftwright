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
    BossFeature,
    ControlFrame,
    DatumRef,
    EnvelopeFeature,
    Feature,
    Finish,
    Frame,
    HoleFeature,
    Note,
    PatternFeature,
    Point,
    SlotFeature,
    StepFeature,
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
