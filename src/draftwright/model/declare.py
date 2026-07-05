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
    cylinder); axis + centre come from the bbox (sufficient for the letter-based IR)."""
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
    assert diameter is not None and at is not None and axis is not None
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
    assert diameter is not None and at is not None and axis is not None
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
    assert diameter is not None and length is not None and at is not None and axis is not None
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
    width axis. Explicit values override any read."""
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
    assert (
        width is not None
        and length is not None
        and long_axis is not None
        and width_axis is not None
        and lo is not None
        and hi is not None
    )
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
    """A hole pattern = ``count`` × a *member* hole (build one with :func:`hole`).
    Explicit only — the arrangement (``bolt_circle`` / ``linear`` / ``grid``) and its
    defining dims (``bcd`` / ``pitch`` / ``grid``) are supplied, not read. ``at`` / ``axis``
    default to the member's frame."""
    frame = Frame(origin=at, axis=axis) if at is not None and axis is not None else member.frame
    return PatternFeature(
        frame=frame,
        pattern=kind,
        count=count,
        member=member,
        members=tuple(members),
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
