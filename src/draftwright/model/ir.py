"""ir — the part-drawing compiler's intermediate representation (ADR 0008).

The narrow waist between recognition and dimensioning. Two protocols carry the
weight:

- `DimParameter` — the universal currency of dimensioning: one measurable quantity
  with a `kind` (diameter / length / depth / …), a semantic `role` (bore /
  counterbore / step / boss / …), the value, the model-space extent it spans, and
  the datums it is measured from. **It carries no rendered label** — formatting
  (and GD&T symbols, which are drawn as geometry, not font text — the pinned font
  has no ⌴/⌵/↧ glyphs) is a renderer concern. `display()` gives a font-safe text
  form for debug/tests.
- `Feature` — anything dimensionable. It exposes `parameters()` and `references()`.
  **Adding a new shape is adding a new `Feature` type** (Open/Closed).

`PartModel` is the whole-part IR the planner consumes. The planner groups a
feature's parameters so compound callouts (a hole's bore + counterbore + depth)
stay one callout.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar, Literal, Protocol, runtime_checkable

from draftwright._geometry import _fmt

if TYPE_CHECKING:
    from draftwright.fits import FitClass

Point = tuple[float, float, float]
ParamKind = Literal["diameter", "length", "depth", "radius", "angle", "location", "thread"]
AUTHORED_DIMENSION_KINDS = frozenset(
    {
        "linear",
        "diameter",
        "radius",
        "angular",
        "curved_dist",
        "oriented",
        "curve_length",
        "thickness",
    }
)
# `role` is the semantic origin of the measurement (open set — new features add
# roles): "bore", "counterbore", "spotface", "od", "step", "boss", "thread",
# "pattern", "slot", "envelope", "location", …
Role = str


@dataclass(frozen=True)
class Frame:
    """A feature's location and dominant orientation in part space (the axis
    letter is enough for now; it generalises to a direction vector)."""

    origin: Point
    axis: str


@dataclass(frozen=True)
class Datum:
    """A reference the planner measures from (a face/axis/point)."""

    id: str
    kind: Literal["plane", "axis", "point"]
    at: Point


@dataclass(frozen=True)
class DimParameter:
    """One measurable quantity a drawing must show. No rendered label — see module
    docstring; use :func:`display` for font-safe text."""

    kind: ParamKind
    role: Role
    value: float
    span: tuple[Point, Point] | None = None
    refs: tuple[str, ...] = ()
    # An authored ± tolerance (ADR 0011 §4 / P2a): a symmetric ``float`` or an
    # ``(lower, upper)`` limit pair; or a resolved fit class (``FitClass``, P2a.2) that
    # renders its own class-code / deviation suffix; ``None`` when untoleranced. Set by the
    # planner from the caller's ``decorations`` — geometry never supplies it.
    tolerance: float | tuple[float, float] | FitClass | None = None


def display(p: DimParameter) -> str:
    """A font-safe text form of a parameter (uses only glyphs the pinned font has;
    GD&T symbols are the renderer's job). For debug and tests, not output."""
    if p.kind == "diameter":
        return f"ø{_fmt(p.value)}"
    if p.kind == "depth":
        return f"{_fmt(p.value)} deep"
    return _fmt(p.value)


@runtime_checkable
class Feature(Protocol):
    """Anything dimensionable. Implementations are frozen dataclasses, so ``kind``
    is a class variable and ``frame`` is read-only."""

    kind: ClassVar[str]

    @property
    def frame(self) -> Frame: ...

    def parameters(self) -> list[DimParameter]: ...

    def references(self) -> list[Datum]: ...


@dataclass(frozen=True)
class HoleFeature:
    """A drilled hole — bore + optional counterbore / spotface steps. The bore,
    counterbore, and spotface share one feature so the planner renders them as one
    compound callout. ``cbore``/``spotface`` are ``(diameter, depth)`` or ``None``
    (plain tuples — the IR stays decoupled from the recogniser's types)."""

    frame: Frame
    diameter: float
    depth: float | None
    through: bool
    count: int = 1
    # Member locations when identical holes are grouped by machining spec into one
    # ``count×`` callout (the engine's grouped-callout rule). Empty for a singleton
    # (the one hole sits at ``frame.origin``). Consumers iterate ``members or
    # (frame.origin,)`` so a centre mark / location dim lands on every hole.
    members: tuple[Point, ...] = ()
    cbore: tuple[float, float] | None = None
    spotface: tuple[float, float] | None = None
    # A countersink (flat-head screw seat): ``(major_diameter, included_angle°)`` or
    # ``None`` — plain tuple, the IR stays decoupled from the recogniser's type (#558).
    csink: tuple[float, float] | None = None
    kind: ClassVar[str] = "hole"

    def parameters(self) -> list[DimParameter]:
        # Location is the group's anchor (Feature.frame), not a parameter.
        ps = [DimParameter("diameter", "bore", self.diameter)]
        if not self.through and self.depth is not None:
            ps.append(DimParameter("depth", "bore", self.depth))
        if self.cbore is not None:
            cd, cdp = self.cbore
            ps.append(DimParameter("diameter", "counterbore", cd))
            ps.append(DimParameter("depth", "counterbore", cdp))
        if self.spotface is not None:
            sd, sdp = self.spotface
            ps.append(DimParameter("diameter", "spotface", sd))
            ps.append(DimParameter("depth", "spotface", sdp))
        if self.csink is not None:
            csd, csa = self.csink
            ps.append(DimParameter("diameter", "countersink", csd))
            ps.append(DimParameter("angle", "countersink", csa))
        return ps

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class StepFeature:
    """One axial segment of a turned profile — its length and its OD."""

    frame: Frame
    length: float
    diameter: float
    span: tuple[Point, Point]
    kind: ClassVar[str] = "step"

    def parameters(self) -> list[DimParameter]:
        return [
            DimParameter("length", "step", self.length, span=self.span),
            DimParameter("diameter", "step", self.diameter),
        ]

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class PatternFeature:
    """A recognised hole pattern (bolt circle / linear array / rect grid) =
    ``count`` × a `member` hole arranged by the pattern. It composes the member
    `HoleFeature` (so the member's bore + counterbore/spotface/depth all come
    along — a counterbored bolt circle keeps its counterbore) and adds the
    pattern-defining dims (BCD / pitch / grid pitches). The member holes are NOT
    emitted individually (the engine's grouped ``n× ø`` callout)."""

    frame: Frame
    pattern: str  # "bolt_circle" | "linear" | "grid"
    count: int
    member: HoleFeature
    members: tuple[Point, ...] = ()  # ordered member-hole centres (raw arrangement)
    bcd: float | None = None  # bolt-circle diameter
    pitch: float | None = None  # linear pitch
    direction: tuple[float, float, float] | None = None  # linear array axis
    grid: tuple[float, float] | None = None  # (row_pitch, col_pitch)
    rows: int | None = None
    cols: int | None = None
    angle: float | None = None  # grid lattice rotation (degrees)
    kind: ClassVar[str] = "pattern"

    def parameters(self) -> list[DimParameter]:
        ps = list(self.member.parameters())  # bore (+ counterbore / spotface / depth)
        if self.bcd is not None:
            ps.append(DimParameter("diameter", "bolt_circle", self.bcd))
        if self.pitch is not None:
            ps.append(DimParameter("length", "pitch", self.pitch))
        if self.grid is not None:
            rp, cp = self.grid
            ps.append(DimParameter("length", "grid_pitch", rp))
            ps.append(DimParameter("length", "grid_pitch", cp))
        return ps

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class EnvelopeFeature:
    """The part's overall bounding dimensions — width (X), height (Z), depth (Y) —
    for a prismatic part. Each is a length parameter whose span is a bbox edge, so
    the renderer places it outside the matching view."""

    frame: Frame
    width: float
    height: float
    depth: float
    bbox_min: Point
    bbox_max: Point
    kind: ClassVar[str] = "envelope"

    def parameters(self) -> list[DimParameter]:
        x0, y0, z0 = self.bbox_min
        x1, y1, z1 = self.bbox_max
        return [
            DimParameter("length", "width", self.width, span=((x0, y0, z0), (x1, y0, z0))),
            DimParameter("length", "height", self.height, span=((x1, y0, z0), (x1, y0, z1))),
            DimParameter("length", "depth", self.depth, span=((x0, y0, z0), (x0, y1, z0))),
        ]

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class SlotFeature:
    """A milled slot / reduced across-flats section — width (the defining size,
    across ``width_axis``) + length (along ``long_axis``). Carries the slot's
    in-plane geometry so the renderer can place the size + position dims in the
    view the two axes span (the recogniser's `Slot`, normalised into the IR)."""

    frame: Frame
    width_axis: str
    long_axis: str
    width: float
    length: float
    w_center: float
    lo: float
    hi: float
    kind: ClassVar[str] = "slot"

    def parameters(self) -> list[DimParameter]:
        return [
            DimParameter("length", "slot_width", self.width),
            DimParameter("length", "slot_length", self.length),
        ]

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class PocketFeature:
    """A blind rectangular recess — a floored slot/pocket (#148a). A `SlotFeature`
    with a third defining size, the ``depth`` from the open face to the floor; the
    in-plane geometry (width/length/position) mirrors a slot so the renderer places
    the callout in the view the two in-plane axes span (the recogniser's `Pocket`,
    normalised into the IR)."""

    frame: Frame
    width_axis: str
    long_axis: str
    width: float
    length: float
    depth: float
    w_center: float
    lo: float
    hi: float
    kind: ClassVar[str] = "pocket"

    @property
    def depth_axis(self) -> str:
        """The axis normal to the opening (into the material) — the view the callout
        reads in is the one normal to it."""
        return next(a for a in "xyz" if a not in (self.width_axis, self.long_axis))

    def parameters(self) -> list[DimParameter]:
        return [
            DimParameter("length", "pocket_width", self.width),
            DimParameter("length", "pocket_length", self.length),
            DimParameter("length", "pocket_depth", self.depth),
        ]

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class BossFeature:
    """An external cylindrical boss/OD on a non-turned part — its diameter."""

    frame: Frame
    diameter: float
    kind: ClassVar[str] = "boss"

    def parameters(self) -> list[DimParameter]:
        return [DimParameter("diameter", "boss", self.diameter)]

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class ChamferFeature:
    """A chamfered (bevelled) edge (#560), called out ``C{leg}`` for an equal-leg 45°
    chamfer or ``{leg} × {angle}°`` otherwise. ``axis`` is the chamfered edge's direction;
    ``leg1``/``leg2`` are the cut depths into the two adjacent faces (equal for 45°);
    ``angle`` is the chamfer angle (degrees). The recogniser recovers both legs from the
    geometry, so an asymmetric chamfer is distinguished from an equal-leg one — the size
    is not estimated from the rendered view (#560 acceptance)."""

    frame: Frame
    axis: str
    leg1: float
    leg2: float
    angle: float
    kind: ClassVar[str] = "chamfer"

    def parameters(self) -> list[DimParameter]:
        return [DimParameter("length", "chamfer", self.leg1)]

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class FilletFeature:
    """A rounded (filleted) edge (#561), called out ``R{radius}`` (grouped ``n× R{radius}``
    for equal radii). ``axis`` is the rounded edge's direction; ``radius`` is the fillet
    radius, recovered from the cylinder geometry — not estimated from the rendered view
    (#561 acceptance). The arc analog of :class:`ChamferFeature`."""

    frame: Frame
    axis: str
    radius: float
    kind: ClassVar[str] = "fillet"

    def parameters(self) -> list[DimParameter]:
        return [DimParameter("radius", "fillet", self.radius)]

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class FlatFeature:
    """A machined flat on round stock (#148b), called out by its across-flats size.
    ``axis`` is the turning axis the stock is coaxial about; ``across`` is the across-flats
    size — flat-to-flat for a face opposed across the axis (double-D / hex A/F), else
    flat-to-opposite-OD (the D height). Recovered from the geometry, not the rendered
    view."""

    frame: Frame
    axis: str
    across: float
    kind: ClassVar[str] = "flat"

    def parameters(self) -> list[DimParameter]:
        return [DimParameter("length", "flat", self.across)]

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class GrooveFeature:
    """A turned / circlip groove on round stock (#148c) — an annular channel dimensioned by
    its ``width`` (axial span) and floor ``diameter``. ``axis`` is the turning axis the stock
    is coaxial about. Recovered from the OD band geometry (a strict local-minimum diameter),
    not the rendered view; distinct from a slot (radial walls) and a plain step (monotonic
    OD)."""

    frame: Frame
    axis: str
    width: float
    diameter: float
    kind: ClassVar[str] = "groove"

    def parameters(self) -> list[DimParameter]:
        return [
            DimParameter("length", "groove", self.width),
            DimParameter("diameter", "groove", self.diameter),
        ]

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class StepLevelFeature:
    """The prismatic height profile — horizontal face levels (Z) dimensioned from the
    base, stacked right of the front view (#237). The turned analogue is `StepFeature`
    (length + OD per segment); this is the prismatic *height* ladder. ``levels`` are the
    interior step Z-coords (ascending); ``base`` is the part's bottom (bbox min Z).

    ``shoulders`` are the in-plane step POSITIONS (#555) — ``(axis, position)`` where a
    step/rebate changes height — so the part is fully constrained (a step is located
    along its axis, not just given two heights). ``datum`` is the part-space min corner
    each shoulder position is measured from (a shoulder at ``pos`` on ``axis`` shows
    ``pos - datum[axis]``)."""

    frame: Frame
    base: float
    levels: tuple[float, ...]
    shoulders: tuple[tuple[str, float], ...] = ()
    datum: Point = (0.0, 0.0, 0.0)
    kind: ClassVar[str] = "step_level"

    def parameters(self) -> list[DimParameter]:
        # Both height and position are correlated SETS routed as a whole through their
        # auto-pass renderers (render_height_ladder / render_step_positions), like the
        # turned-step chain — never flattened into per-value span dims. So neither carries
        # a span; a single `role=` intent rebuilds the whole ladder / all shoulders.
        _di = {"x": 0, "y": 1, "z": 2}
        return [DimParameter("length", "step_height", z - self.base) for z in self.levels] + [
            DimParameter("length", "step_position", pos - self.datum[_di[axis]])
            for axis, pos in self.shoulders
        ]

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class PlateFeature:
    """A thin slab's thickness on a multi-plate prismatic (#559) — the base plate of
    an L-bracket, an upright wall, a rib. ``axis`` is the thin (thickness) axis; the
    slab runs from ``lo`` to ``hi`` along it (``hi - lo`` is the thickness), centred at
    ``u``/``v`` on the other two axes (in axis order). Unlike ``StepLevelFeature`` (Z
    staircase heights from the base) and ``EnvelopeFeature`` (the full bbox), this is a
    *partial* extent along any axis — a plate that spans less than the whole part on its
    thin axis, so ``dim_height``/the envelope do not already cover it."""

    frame: Frame
    axis: str
    lo: float
    hi: float
    u: float
    v: float
    kind: ClassVar[str] = "plate"

    def _span(self) -> tuple[Point, Point]:
        i = "xyz".index(self.axis)
        oi = [j for j in (0, 1, 2) if j != i]
        p0 = [0.0, 0.0, 0.0]
        p1 = [0.0, 0.0, 0.0]
        p0[i], p1[i] = self.lo, self.hi
        p0[oi[0]] = p1[oi[0]] = self.u
        p0[oi[1]] = p1[oi[1]] = self.v
        return (tuple(p0), tuple(p1))  # type: ignore[return-value]

    def parameters(self) -> list[DimParameter]:
        return [DimParameter("length", "thickness", self.hi - self.lo, span=self._span())]

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class RotationalFeature:
    """A turned/rotational part's axial furniture (#237): the outer diameter, the
    rotation-axis centrelines, and the concentric bore diameters (dimensioned by
    centred leaders). Its presence marks the part rotational — the renderer places the
    OD dim + centrelines + bore leaders from it."""

    frame: Frame  # at the rotation axis
    od: float
    bores: tuple[float, ...] = ()  # concentric bore diameters, in display order
    kind: ClassVar[str] = "rotational"

    def parameters(self) -> list[DimParameter]:
        return [
            DimParameter("diameter", "od", self.od),
            *[DimParameter("diameter", "bore", b) for b in self.bores],
        ]

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class AuthoredDimension:
    """A pre-authored drafting dimension imported from an external semantic source.

    This is the concept-shaped IR for AP242 dimensional PMI: the source file may call it
    PMI, but the drawing model sees an authored linear/diameter/radius/etc. dimension with
    baked label and referenced geometry. The normal dimension planner does not derive or
    duplicate it, so ``parameters()`` is empty; renderers consume it directly while keeping
    the source/provenance fields for round-trip and diagnostics."""

    frame: Frame
    dimension_kind: str  # "linear" | "diameter" | "radius" | "angular" | ...
    value: float
    label: str
    dominant_axis: str
    upper_tol: float | None = None
    lower_tol: float | None = None
    ref_bbox: tuple[float, float, float, float, float, float] | None = None
    ref_pts: tuple[Point, ...] = ()
    source: str = "ap242_pmi"
    source_kind: str | None = None
    kind: ClassVar[str] = "authored_dimension"

    @property
    def pmi_kind(self) -> str:
        """Compatibility alias for the existing AP242 renderer until it is renamed."""
        return self.dimension_kind

    def parameters(self) -> list[DimParameter]:
        return []

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class PmiFeature:
    """Raw AP242 PMI fallback for records not yet lowered to drafting concepts.

    Dimensional AP242 PMI should become :class:`AuthoredDimension`; GD&T/datum/surface
    records should eventually lower to ``ControlFrame`` / ``DatumRef`` / ``Finish``. This
    type remains as an explicit provenance-preserving escape hatch so unsupported records
    are visible instead of silently lost."""

    frame: Frame
    pmi_kind: str  # the PMI category: "linear" | "diameter" | "radius" | "angular" | ...
    value: float
    label: str
    dominant_axis: str
    ref_bbox: tuple[float, float, float, float, float, float] | None = None
    ref_pts: tuple[Point, ...] = ()
    kind: ClassVar[str] = "pmi"

    def parameters(self) -> list[DimParameter]:
        return []

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class ControlFrame:
    """A geometric-tolerance feature control frame (ISO 1101) declared on the drawing
    (ADR 0011 §4 aspect side-layer, #61). Placed as a first-class ADR 0009 corridor
    candidate by ``render_gdt`` — NOT through the dimension planner, so ``parameters()``
    is empty (like :class:`PmiFeature`). The target ``(view, side)`` strip and the
    model-space site (``frame.origin``) the leader hangs from are carried explicitly:
    render-core places into that strip's corridor; the Sheet layer (P2c) computes them
    from a build123d face."""

    frame: Frame  # the site the leader hangs from + its axis
    characteristic: str  # ISO 1101 lowercase name: "position" | "flatness" | ...
    tolerance: str  # the tolerance value text, e.g. "0.1"
    view: str  # target view: "front" | "side" | "plan"
    side: str  # target strip: "above" | "below" | "left" | "right"
    datums: tuple[str, ...] = ()
    diameter: bool = False  # ⌀ prefix on the tolerance zone
    modifier: str | None = None  # material-condition modifier: "M" | "L" | "P" | ...
    # The IR feature this frame decorates — recorded as provenance (ADR 0010); ``None``
    # leaves it feature-less. Untyped to avoid an import cycle with the geometric features.
    origin: object | None = None
    kind: ClassVar[str] = "control_frame"

    def parameters(self) -> list[DimParameter]:
        return []

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class DatumRef:
    """A datum feature symbol (ISO 5459) — a boxed letter tagging a surface/axis as a
    datum (#61). Placed as an ADR 0009 corridor candidate by ``render_gdt``, not through
    the dimension planner (``parameters()`` is empty)."""

    frame: Frame
    letter: str
    view: str
    side: str
    origin: object | None = None
    kind: ClassVar[str] = "datum_ref"

    def parameters(self) -> list[DimParameter]:
        return []

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class Finish:
    """A surface-finish symbol (ISO 1302) — a roughness callout on a surface (#61).
    Placed as an ADR 0009 corridor candidate by ``render_gdt``, not through the
    dimension planner (``parameters()`` is empty)."""

    frame: Frame
    ra: str  # roughness value text, e.g. "3.2" (Ra, µm)
    view: str
    side: str
    origin: object | None = None
    kind: ClassVar[str] = "finish"

    def parameters(self) -> list[DimParameter]:
        return []

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class Note:
    """A free-text manufacturing note (#488) hung on a leader to a feature/site — the shop
    callouts detection can't infer (thread specs, ``DEBURR``, chip-relief, knurl). Placed like
    the GD&T items — a first-class ADR 0009 corridor candidate via ``render_gdt`` (its glyph is a
    single-line ``TextBlock``), NOT the dimension planner (``parameters()`` is empty)."""

    frame: Frame
    text: str
    view: str
    side: str
    origin: object | None = None
    kind: ClassVar[str] = "note"

    def parameters(self) -> list[DimParameter]:
        return []

    def references(self) -> list[Datum]:
        return []


@dataclass
class PartModel:
    """The whole-part IR: the oriented part plus its features and datums."""

    bbox: object  # build123d BoundBox
    orientation: str | None  # turning axis if rotational, else None
    features: list[Feature] = field(default_factory=list)
    datums: list[Datum] = field(default_factory=list)
    # Authored aspects the frozen features can't carry (ADR 0011 §4). P2a uses it for
    # per-dimension tolerances: ``{(feature, ParamKind) -> float | (lo, hi)}``. The
    # planner consults it to set ``DimParameter.tolerance``; empty on a detected model.
    decorations: dict = field(default_factory=dict)
