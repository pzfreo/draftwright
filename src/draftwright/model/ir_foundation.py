"""Foundational IR values and common feature records (ADR 1).

Records remain available under ``draftwright.model.ir`` for existing callers and
serialized models. This leaf owns their constructors and validation.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import atan2, cos, hypot, isfinite, pi, radians, sin
from typing import TYPE_CHECKING, ClassVar, Literal, Protocol, runtime_checkable

from draftwright import contract_values
from draftwright._geometry import (
    _fmt,
    plane_axes,
)
from draftwright.feature_identity import (
    register_envelope_feature_type,
)
from draftwright.model.dimension_intent import (
    ParameterId,
    ParamKind,
    Role,
)
from draftwright.section_recess_contract import validate_pocket_mouth

if TYPE_CHECKING:
    from draftwright.fits import FitClass

Point = tuple[float, float, float]
CylinderSense = Literal["external", "internal"]


class TurnedProfileIdentity(Protocol):
    """Draftwright's structural view of a provider-owned turned-profile key."""

    @property
    def axis(self) -> str: ...

    @property
    def axis_origin(self) -> Point: ...

    @property
    def body_bounds(self) -> tuple[float, float, float, float, float, float]: ...


def _finite_point3(name: str, value) -> Point:
    try:
        result = tuple(float(component) for component in value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite 3-vector") from exc
    if len(result) != 3 or not all(isfinite(component) for component in result):
        raise ValueError(f"{name} must be a finite 3-vector")
    return (result[0], result[1], result[2])


def _strict_finite_real(name: str, value) -> float:
    """Return one public numeric fact without accepting coercible impostors."""
    return contract_values.finite_real(value, name=name)


@dataclass(frozen=True)
class CylindricalReference:
    """Kernel-free evidence for one authored reference to a finite cylindrical face.

    ``axis_origin`` is the perpendicular foot from world origin to the cylinder axis.
    ``axis_direction`` is a canonical unit vector whose largest absolute component is
    positive, and ``axial_interval`` is measured from that origin along the direction.
    Those choices remove the arbitrary axis-location and direction signs a geometry kernel
    may report, so equivalent faces retain stable value identity across STEP round-trips.
    """

    axis_origin: Point
    axis_direction: Point
    radius: float
    axial_interval: tuple[float, float]
    sense: CylinderSense

    def __post_init__(self) -> None:
        origin = _finite_point3("axis_origin", self.axis_origin)
        direction = _finite_point3("axis_direction", self.axis_direction)
        norm = hypot(*direction)
        if abs(norm - 1.0) > 2e-6:
            raise ValueError("axis_direction must be unit length")
        dominant = max(range(3), key=lambda index: abs(direction[index]))
        if direction[dominant] <= 0:
            raise ValueError("axis_direction must have a positive dominant component")
        if abs(sum(a * b for a, b in zip(origin, direction, strict=True))) > 2e-6:
            raise ValueError("axis_origin must be perpendicular to axis_direction")
        try:
            lo, hi = (float(value) for value in self.axial_interval)
        except (TypeError, ValueError) as exc:
            raise ValueError("axial_interval must contain two finite values") from exc
        if not (isfinite(lo) and isfinite(hi) and hi > lo):
            raise ValueError("axial_interval must be finite and increasing")
        if isinstance(self.radius, bool):
            raise ValueError("radius must be finite and positive")
        radius = float(self.radius)
        if not isfinite(radius) or radius <= 0:
            raise ValueError("radius must be finite and positive")
        if self.sense not in ("external", "internal"):
            raise ValueError("sense must be 'external' or 'internal'")
        object.__setattr__(self, "axis_origin", origin)
        object.__setattr__(self, "axis_direction", direction)
        object.__setattr__(self, "radius", radius)
        object.__setattr__(self, "axial_interval", (lo, hi))

    @classmethod
    def canonical(
        cls,
        *,
        axis_point,
        axis_direction,
        radius: float,
        local_interval,
        sense: CylinderSense,
    ) -> CylindricalReference:
        """Canonicalise a kernel axis point/direction and its local finite interval."""
        point = _finite_point3("axis_point", axis_point)
        raw = _finite_point3("axis_direction", axis_direction)
        norm = hypot(*raw)
        if norm <= 1e-12:
            raise ValueError("axis_direction must be non-zero")
        unit = tuple(component / norm for component in raw)
        dominant = max(range(3), key=lambda index: abs(unit[index]))
        sign = -1.0 if unit[dominant] < 0 else 1.0
        direction = tuple(sign * component for component in unit)
        along = sum(a * b for a, b in zip(point, direction, strict=True))
        origin = tuple(
            coordinate - along * component
            for coordinate, component in zip(point, direction, strict=True)
        )
        try:
            first, last = (float(value) for value in local_interval)
        except (TypeError, ValueError) as exc:
            raise ValueError("local_interval must contain two finite values") from exc
        if not (isfinite(first) and isfinite(last) and last > first):
            raise ValueError("local_interval must be finite and increasing")
        # A local parameter endpoint is ``point + v * raw_unit``. Project both world
        # endpoints onto the canonical axis so a negative kernel direction is harmless.
        stations = tuple(along + sign * value for value in (first, last))
        interval = (min(stations), max(stations))
        clean_origin = tuple(0.0 if abs(value) < 1e-12 else value for value in origin)
        clean_direction = tuple(0.0 if abs(value) < 1e-12 else value for value in direction)
        return cls(
            axis_origin=clean_origin,  # type: ignore[arg-type]
            axis_direction=clean_direction,  # type: ignore[arg-type]
            radius=radius,
            axial_interval=interval,
            sense=sense,
        )

    @property
    def diameter(self) -> float:
        return 2.0 * self.radius

    @property
    def principal_axis(self) -> str:
        """``'X'``/``'Y'``/``'Z'`` when the topology axis is orthographic, else ``'?'``."""
        dominant = max(range(3), key=lambda index: abs(self.axis_direction[index]))
        if abs(self.axis_direction[dominant] - 1.0) > 1e-6 or any(
            abs(self.axis_direction[index]) > 1e-6 for index in range(3) if index != dominant
        ):
            return "?"
        return "XYZ"[dominant]

    @property
    def midpoint(self) -> Point:
        station = sum(self.axial_interval) / 2.0
        return tuple(
            origin + station * direction
            for origin, direction in zip(self.axis_origin, self.axis_direction, strict=True)
        )  # type: ignore[return-value]


@dataclass(frozen=True)
class CircularReference:
    """Kernel-free evidence for one authored circular-edge dimension support."""

    center: Point
    normal: Point
    radius: float

    def __post_init__(self) -> None:
        center = _finite_point3("center", self.center)
        normal = _finite_point3("normal", self.normal)
        length = hypot(*normal)
        if abs(length - 1.0) > 2e-6:
            raise ValueError("normal must be unit length")
        dominant = max(range(3), key=lambda index: abs(normal[index]))
        if normal[dominant] <= 0:
            raise ValueError("normal must have a positive dominant component")
        if isinstance(self.radius, bool):
            raise ValueError("radius must be finite and positive")
        radius = float(self.radius)
        if not isfinite(radius) or radius <= 0:
            raise ValueError("radius must be finite and positive")
        object.__setattr__(self, "center", center)
        object.__setattr__(self, "normal", normal)
        object.__setattr__(self, "radius", radius)

    @classmethod
    def canonical(cls, *, center, normal, radius: float) -> CircularReference:
        """Canonicalise a kernel circle centre and unoriented unit normal."""
        point = _finite_point3("center", center)
        raw = _finite_point3("normal", normal)
        length = hypot(*raw)
        if length <= 1e-12:
            raise ValueError("normal must be non-zero")
        unit = tuple(component / length for component in raw)
        dominant = max(range(3), key=lambda index: abs(unit[index]))
        sign = -1.0 if unit[dominant] < 0 else 1.0
        direction = tuple(sign * component for component in unit)
        clean_center = tuple(0.0 if abs(value) < 1e-12 else value for value in point)
        clean_direction = tuple(0.0 if abs(value) < 1e-12 else value for value in direction)
        return cls(
            center=clean_center,  # type: ignore[arg-type]
            normal=clean_direction,  # type: ignore[arg-type]
            radius=radius,
        )

    @property
    def diameter(self) -> float:
        return 2.0 * self.radius

    @property
    def principal_axis(self) -> str:
        """``'X'``/``'Y'``/``'Z'`` when the circle normal is orthographic, else ``'?'``."""
        dominant = max(range(3), key=lambda index: abs(self.normal[index]))
        if abs(self.normal[dominant] - 1.0) > 1e-6 or any(
            abs(self.normal[index]) > 1e-6 for index in range(3) if index != dominant
        ):
            return "?"
        return "XYZ"[dominant]


@dataclass(frozen=True)
class AngularReference:
    """Two oriented model-space rays bounding the non-reflex angular sector.

    ``first`` and ``second`` are witness points on supports through ``vertex``.
    ``minor`` selects the rays towards them; ``opposite`` extends both supports
    through the vertex and selects the vertically opposite non-reflex sector.
    Reversing witness order reverses the plane normal, not the measured angle.
    A virtual vertex explicitly describes intersecting extended supports; this
    declaration alone does not certify correspondence to physical part edges.
    No field describes annotation placement or an arc radius on the sheet.
    """

    vertex: Point
    first: Point
    second: Point
    sector: Literal["minor", "opposite"] = "minor"
    virtual_vertex: bool = False

    def __post_init__(self) -> None:
        for name in ("vertex", "first", "second"):
            object.__setattr__(self, name, _finite_point3(name, getattr(self, name)))
        if self.sector not in ("minor", "opposite"):
            raise ValueError(
                "angular reference supports only minor or opposite non-reflex sectors"
            )
        if type(self.virtual_vertex) is not bool:
            raise ValueError("angular reference virtual_vertex must be a bool")
        first, second = self.rays
        cross = self._cross(first, second)
        if hypot(*cross) <= 1e-9:
            raise ValueError("angular reference rays must not be parallel or collinear")

    @property
    def rays(self) -> tuple[Point, Point]:
        result = []
        for point in (self.first, self.second):
            ray = tuple(p - v for p, v in zip(point, self.vertex, strict=True))
            length = hypot(*ray)
            if not isfinite(length) or length <= 1e-9:
                raise ValueError("angular reference needs two finite nonzero rays")
            sign = -1 if self.sector == "opposite" else 1
            result.append((sign * ray[0] / length, sign * ray[1] / length, sign * ray[2] / length))
        return result[0], result[1]

    @staticmethod
    def _cross(first: Point, second: Point) -> Point:
        x, y, z = first
        u, v, w = second
        return y * w - z * v, z * u - x * w, x * v - y * u

    @property
    def normal(self) -> Point:
        cross = self._cross(*self.rays)
        length = hypot(*cross)
        return cross[0] / length, cross[1] / length, cross[2] / length

    @property
    def angle_degrees(self) -> float:
        first, second = self.rays
        return (
            atan2(
                hypot(*self._cross(first, second)),
                sum(a * b for a, b in zip(first, second, strict=True)),
            )
            * 180
            / pi
        )

    @property
    def principal_axis(self) -> str:
        """Normal axis of a true-angle principal projection, or ``?`` if oblique."""
        normal = self.normal
        dominant = max(range(3), key=lambda index: abs(normal[index]))
        if any(abs(normal[index]) > 1e-6 for index in range(3) if index != dominant):
            return "?"
        return "XYZ"[dominant]

    @property
    def measurement_key(self) -> tuple:
        """Referenced geometry and sector, invariant under witness-order reversal."""
        return (
            self.vertex,
            tuple(sorted((self.first, self.second))),
            self.sector,
            self.virtual_vertex,
        )


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
    # An authored ± tolerance (ADR 4 (was 0011 §4) / P2a): a symmetric ``float`` or an
    # ``(lower, upper)`` limit pair; or a resolved fit class (``FitClass``, P2a.2) that
    # renders its own class-code / deviation suffix; ``None`` when untoleranced. Set by the
    # planner from the caller's ``decorations`` — geometry never supplies it.
    tolerance: float | tuple[float, float] | FitClass | None = None
    # A semantic discriminator for measurements ``(role, kind)`` cannot tell apart
    # (ADR 4 (was 0016) identity, tier 2). Today's sole instance is a grid pattern's two pitches
    # (``"row"`` / ``"col"``). ``None`` wherever role + kind already identify the thing.
    discriminator: str | None = None
    # Angular geometry travels with the approved measurement, not through the
    # structural feature facts where suppression could be bypassed.
    angular_reference: AngularReference | None = None
    # Imported requirement identities carried with a decorated parameter.  Geometry-derived
    # parameters leave this empty.  The renderer uses it to preserve source ownership when
    # deciding whether otherwise identical physical callouts may be consolidated.
    source_ids: tuple[str, ...] = ()
    limit_bounds: tuple[float, float] | None = None

    @property
    def parameter_id(self) -> ParameterId:
        """This parameter's semantic identity (ADR 4 (was 0016)) — ``role.kind``, plus the
        discriminator where one is needed: ``bore.diameter``, ``bore.depth``,
        ``grid_pitch.length.row``.

        **Derived, never hand-authored**, so the ~40 `DimParameter(...)` construction
        sites cannot drift a literal away from the ``role=`` beside it.

        ``kind`` is included *always*, not only where it disambiguates. Dropping it when
        a role happens to be unique within its feature would make an id depend on its
        **sibling** parameters — so adding a new parameter to a feature would silently
        repoint every intent aimed at an existing one, destroying the stability the id
        exists for. Uniform costs some prettiness (``step_height.length``) and buys the
        one property that is load-bearing.
        """
        base = f"{self.role}.{self.kind}"
        return f"{base}.{self.discriminator}" if self.discriminator else base


@dataclass(frozen=True)
class ToleranceDecoration:
    """A tolerance aspect plus the external requirement it came from.

    Ordinary declared tolerances remain plain numbers/tuples.  Imported requirements use
    this wrapper so AP242 provenance survives the concept-lowering and generated-Sheet seams
    without changing the renderer-facing :attr:`DimParameter.tolerance` value type.
    """

    value: float | tuple[float, float] | FitClass
    source: str
    source_ids: tuple[str, ...] = ()
    # Absolute lower/upper values when the source authored a limit dimension. ``value``
    # remains the deviation form used by tolerance algebra; this field preserves the
    # distinct presentation semantics through canonical-feature lowering.
    limit_bounds: tuple[float, float] | None = None


@dataclass(frozen=True)
class NominalRequirement:
    """External ownership of an already-canonical nominal dimension.

    Unlike :class:`ToleranceDecoration`, this adds no printed suffix: the feature's normal
    planned diameter is already the truthful visual annotation.  The wrapper records which
    imported semantic sources own that existing annotation, so provenance survives lowering,
    lint reconciliation, and generated-Sheet round trips without creating a duplicate.
    """

    value: float
    source: str
    source_ids: tuple[str, ...]

    def agrees_with(self, value: float) -> bool:
        """Whether a canonical value can carry this nominal without changing its meaning."""
        return abs(float(value) - self.value) <= 1e-6

    def __post_init__(self) -> None:
        if isinstance(self.value, bool):
            raise ValueError("nominal requirement value must be finite and positive")
        value = float(self.value)
        if not isfinite(value) or value <= 0:
            raise ValueError("nominal requirement value must be finite and positive")
        source = str(self.source).strip()
        if not source:
            raise ValueError("nominal requirement source must be a non-empty string")
        ids = tuple(
            dict.fromkeys(str(item).strip() for item in self.source_ids if str(item).strip())
        )
        if not ids:
            raise ValueError("nominal requirement needs at least one source id")
        object.__setattr__(self, "value", value)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "source_ids", ids)


def _require_source_identity(name: str, source_ids) -> tuple[str, ...]:
    ids = tuple(dict.fromkeys(str(item).strip() for item in source_ids if str(item).strip()))
    if not ids:
        raise ValueError(f"{name} needs at least one source id")
    return ids


@dataclass(frozen=True)
class ThreadRequirement:
    """A source-authored internal or external metric-thread requirement.

    The parsed fields drive callout composition and consistency checks. ``text`` remains the
    authoritative source wording, while the Part21 identities and finite cylinders retain the
    exact geometry association used to choose the canonical owner.  No nominal-value lookup is
    needed—or permitted—after this aspect has been formed.
    """

    application: Literal["external", "internal"]
    designation: str
    nominal_diameter: float
    pitch: float
    tolerance_class: str
    hand: Literal["RH", "LH"]
    text: str
    source_ids: tuple[str, ...]
    part21_id: str
    shape_aspect_ids: tuple[str, ...]
    reference_item_ids: tuple[str, ...]
    cylindrical_refs: tuple[CylindricalReference, ...]
    full_available_length: bool = False
    minimum_full_thread: float | None = None
    drill_diameter: float | None = None
    drill_depth: float | None = None
    drill_point_angle: float | None = None
    source: str = "ap242_pmi"

    def __post_init__(self) -> None:
        if self.application not in ("external", "internal"):
            raise ValueError("thread application must be 'external' or 'internal'")
        if not str(self.designation).strip():
            raise ValueError("thread designation must be non-empty")
        for name, raw in (("nominal_diameter", self.nominal_diameter), ("pitch", self.pitch)):
            value = float(raw)
            if not isfinite(value) or value <= 0:
                raise ValueError(f"thread {name} must be finite and positive")
            object.__setattr__(self, name, value)
        for name in ("minimum_full_thread", "drill_diameter", "drill_depth", "drill_point_angle"):
            raw = getattr(self, name)
            if raw is None:
                continue
            value = float(raw)
            if not isfinite(value) or value <= 0:
                raise ValueError(f"thread {name} must be finite and positive")
            object.__setattr__(self, name, value)
        if (
            self.application == "internal"
            and self.minimum_full_thread is not None
            and self.drill_depth is not None
            and self.minimum_full_thread > self.drill_depth
        ):
            raise ValueError("thread minimum full thread cannot exceed drill depth")
        if not str(self.text).strip():
            raise ValueError("thread source text must be non-empty")
        if not str(self.part21_id).strip():
            raise ValueError("thread requirement needs its Part21 identity")
        if not self.cylindrical_refs:
            raise ValueError("thread requirement needs finite-cylinder topology evidence")
        object.__setattr__(
            self, "source_ids", _require_source_identity("thread requirement", self.source_ids)
        )

    @property
    def callout_suffix(self) -> str:
        """The source terms not already stated by the owner's canonical dimensions."""
        if self.application == "external":
            length = ", FULL AVAILABLE LENGTH" if self.full_available_length else ""
            return f"{self.designation}{length}"
        terms = [self.designation]
        if self.minimum_full_thread is not None:
            terms.append(f"{_fmt(self.minimum_full_thread)} MIN FULL THREAD")
        if self.drill_point_angle is not None:
            terms.append(f"{_fmt(self.drill_point_angle)}° CONVENTIONAL DRILL POINT")
        return "; ".join(terms)


@dataclass(frozen=True)
class ThreadOperation:
    """An authored thread/tap operation with an independently dimensioned depth.

    A plain thread string remains the compact form for an unspecified/full-depth thread.
    This value object carries an explicit tap depth through the public IR as
    ``thread.depth`` instead of burying the manufacturing value in prose (ADR 4 (was 0011)).
    """

    designation: str
    depth: float

    def __post_init__(self) -> None:
        if not isinstance(self.designation, str):
            raise ValueError("thread designation must be a non-empty string")
        designation = self.designation.strip()
        if not designation:
            raise ValueError("thread designation must be a non-empty string")
        if (
            not isinstance(self.depth, (int, float))
            or isinstance(self.depth, bool)
            or not isfinite(self.depth)
            or self.depth <= 0
        ):
            raise ValueError("thread depth must be finite and positive")
        depth = float(self.depth)
        object.__setattr__(self, "designation", designation)
        object.__setattr__(self, "depth", depth)

    @property
    def callout_suffix(self) -> str:
        return self.designation


@dataclass(frozen=True)
class KnurlRequirement:
    """A source-authored knurl aspect on a finite cylindrical region."""

    pattern: Literal["straight", "diamond"]
    pitch: float
    full_width: bool
    text: str
    source_ids: tuple[str, ...]
    part21_id: str
    shape_aspect_ids: tuple[str, ...]
    reference_item_ids: tuple[str, ...]
    cylindrical_refs: tuple[CylindricalReference, ...]
    edge_chamfer: float | None = None
    maximum_diameter: float | None = None
    processes: tuple[Literal["cut", "formed"], ...] = ()
    source: str = "ap242_pmi"

    def __post_init__(self) -> None:
        if self.pattern not in ("straight", "diamond"):
            raise ValueError("knurl pattern must be 'straight' or 'diamond'")
        pitch = float(self.pitch)
        if not isfinite(pitch) or pitch <= 0:
            raise ValueError("knurl pitch must be finite and positive")
        object.__setattr__(self, "pitch", pitch)
        for name in ("edge_chamfer", "maximum_diameter"):
            raw = getattr(self, name)
            if raw is None:
                continue
            value = float(raw)
            if not isfinite(value) or value <= 0:
                raise ValueError(f"knurl {name} must be finite and positive")
            object.__setattr__(self, name, value)
        if not str(self.text).strip():
            raise ValueError("knurl source text must be non-empty")
        if not str(self.part21_id).strip():
            raise ValueError("knurl requirement needs its Part21 identity")
        if not self.cylindrical_refs:
            raise ValueError("knurl requirement needs finite-cylinder topology evidence")
        if any(process not in ("cut", "formed") for process in self.processes):
            raise ValueError("knurl processes must be 'cut' and/or 'formed'")
        object.__setattr__(
            self, "source_ids", _require_source_identity("knurl requirement", self.source_ids)
        )

    @property
    def callout_suffix(self) -> str:
        """Compact drawing terms following the canonical diameter.

        The authoritative source sentence remains in :attr:`text`; the leader uses standard
        shop-floor abbreviations so it can share a solved annotation field without turning a
        semantic requirement into an unplaceable paragraph.
        """
        terms = []
        if self.maximum_diameter is not None:
            terms.append("MAX AFTER KNURL")
        knurl = f"{self.pattern.upper()} KNURL P{_fmt(self.pitch)}"
        if self.full_width:
            knurl += " FULL WIDTH"
            if self.edge_chamfer is not None:
                knurl += f" TO C{_fmt(self.edge_chamfer)} CHAMFERS"
        terms.append(knurl)
        if self.processes:
            terms.append("/".join(process.upper() for process in self.processes) + " PERMITTED")
        return "; ".join(terms)


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
    """A bore — circular or structurally profiled — with optional counterbore / spotface
    steps. The bore, counterbore, and spotface share one feature so the planner renders them
    as one compound callout. ``cbore``/``spotface`` are ``(diameter, depth)`` or ``None``
    (plain tuples — the IR stays decoupled from the recogniser's types)."""

    #: The compiled stem this feature's position is minted under (#966). Declared HERE,
    #: beside the feature, rather than in a table in the planner, because the planner's
    #: table was one of TWO owners: the mint sites in `model/compiled.py` and the readers
    #: in `annotations/` spelled the same name again as a literal, so renaming the table
    #: made a dimension VANISH rather than change name. One owner of the stem.
    #:
    #: The SUFFIX is still chosen at each mint site, which is how
    #: `location_pocket.location` and `location_slot.length` came to disagree — that half
    #: of #966 is not fixed here. This declaration owns the stem and nothing more.
    LOCATION_STEM: ClassVar[str] = "location"

    #: The stem a SIDE-DRILLED bore's two positions are minted under — a separate
    #: measurement from the Z-normal ladder above (bounding-box datum, one entry per
    #: measured axis, compiled in `_compile_off_axis_hole_locations`), so it is a separate
    #: declaration rather than a suffix rule over `LOCATION_STEM`. Declared for the same
    #: reason: compiler and renderer both read it instead of restating the literal.
    LOCATION_OFF_AXIS_STEM: ClassVar[str] = "location_off_axis"

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
    # A thread spec (tap/thread), e.g. ``"M3x0.5"`` — free text folded onto the hole's
    # compound callout (#764), or ``ThreadOperation`` when an authored tap depth must remain
    # independently addressable. A declaration-only aspect (ADR 4 (was 0011) side-layer): threads
    # are cosmetic, rarely modelled as geometry, so there is no recogniser — declare + emit.
    thread: str | ThreadOperation | ThreadRequirement | None = None
    # A structural bore profile. ``None`` is the ordinary circular bore; ``double_d``
    # means the bore diameter is its parent-circle major diameter and the independently
    # planned A/F parameter below defines the two chord flats (#1061).
    profile: Literal["double_d"] | None = None
    across_flats: float | None = None
    # Canonical unit normal to the parallel flats in part coordinates. This is orientation,
    # not a printable measurement, and is retained for declaration/script fidelity.
    profile_direction: Point | None = None
    # None uses default wording; an explicitly empty string omits only the
    # printed indicator. This is declared content, separate from through/blind.
    through_indicator: str | None = None
    kind: ClassVar[str] = "hole"

    def __post_init__(self) -> None:
        """Keep profiled-bore geometry complete at the public IR waist.

        Detection, declaration and direct ``PartModel`` construction are equal producers
        (ADR 4 (was 0011)). Validating here prevents a direct model from planning ``DOUBLE-D`` with
        no A/F value, or carrying an orientation that the profile cannot have.
        """
        if self.through_indicator is not None:
            if not self.through:
                raise ValueError("a blind hole cannot carry a through_indicator")
            if not isinstance(self.through_indicator, str) or (
                self.through_indicator
                and (
                    not self.through_indicator.isprintable() or not self.through_indicator.strip()
                )
            ):
                raise ValueError(
                    "through_indicator must be printable single-line text, or '' to omit"
                )
        if (
            isinstance(self.thread, ThreadOperation)
            and self.depth is not None
            and self.thread.depth > self.depth
        ):
            raise ValueError("thread depth cannot exceed bore depth")
        if self.profile is None:
            if self.across_flats is not None or self.profile_direction is not None:
                raise ValueError(
                    "across_flats and profile_direction require a structural bore profile"
                )
            return
        if self.profile != "double_d":
            raise ValueError(f"unsupported structural bore profile {self.profile!r}")
        if not self.through:
            raise ValueError("the supported double-D profile is through-only")
        if self.across_flats is None:
            raise ValueError("a double-D bore requires across_flats")
        major = float(self.diameter)
        across = float(self.across_flats)
        if not (isfinite(major) and isfinite(across) and 0 < across < major):
            raise ValueError(
                "a double-D bore requires 0 < across_flats < its finite major diameter"
            )
        if self.profile_direction is None:
            raise ValueError("a double-D bore requires profile_direction")
        direction = tuple(float(v) for v in self.profile_direction)
        norm = hypot(*direction)
        if len(direction) != 3 or not isfinite(norm) or norm <= 1e-12:
            raise ValueError("profile_direction must be a finite non-zero 3-vector")
        direction = tuple(v / norm for v in direction)
        if abs(direction["xyz".index(self.frame.axis)]) > 1e-6:
            raise ValueError("profile_direction must be perpendicular to the bore axis")
        first = next(v for v in direction if abs(v) > 1e-12)
        if first < 0:
            direction = tuple(-v for v in direction)
        object.__setattr__(self, "profile_direction", direction)

    def parameters(self) -> list[DimParameter]:
        # Location is the group's anchor (Feature.frame), not a parameter.
        ps = [DimParameter("diameter", "bore", self.diameter)]
        if self.profile == "double_d" and self.across_flats is not None:
            ps.append(DimParameter("length", "profile_across_flats", self.across_flats))
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
        if isinstance(self.thread, ThreadOperation):
            ps.append(DimParameter("depth", "thread", self.thread.depth))
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
    # An EXTERNAL thread spec, e.g. ``"M3x0.5"`` — free text appended to the OD (⌀) callout
    # (#859). The turned analog of ``HoleFeature.thread``: a declaration-only aspect, threads
    # are cosmetic and rarely modelled as geometry, so there is no recogniser — declare + render.
    thread: str | ThreadRequirement | None = None
    knurl: KnurlRequirement | None = None
    #: Body-local recognition ownership. Structural provenance, not a printable quantity;
    #: declared/legacy features may leave it absent and retain axis-line grouping (#1357).
    profile: TurnedProfileIdentity | None = None
    #: Draftwright-owned, serializable physical-profile identity. Generated Sheet programs use
    #: this opaque token instead of exposing the provider's ``TurnedProfileKey``; ordinary
    #: declarations may omit it and retain geometric grouping.
    profile_group: str | None = None
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

    #: The compiled stem this feature's position is minted under — see
    #: :attr:`HoleFeature.LOCATION_STEM` for why it is declared here (#966).
    LOCATION_STEM: ClassVar[str] = "location_pattern"

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
            # Same role AND kind, semantically distinct — the ADR 4 (was 0016) tier-2 case that
            # forces a discriminator. "row"/"col" (not "x"/"y"): `angle` may rotate the
            # lattice, so a row pitch is not an X pitch in general. Mapping a user-facing
            # `axis=` selector onto these is the facade's job, not the IR's.
            ps.append(DimParameter("length", "grid_pitch", rp, discriminator="row"))
            ps.append(DimParameter("length", "grid_pitch", cp, discriminator="col"))
        return ps

    def references(self) -> list[Datum]:
        return []


def _grid_points_match(actual: tuple[Point, ...], expected: list[Point]) -> bool:
    """Match each member to one lattice site within provider rounding tolerance."""
    choices = [
        [
            index
            for index, target in enumerate(expected)
            if all(abs(point[axis] - target[axis]) <= 0.01 for axis in range(3))
        ]
        for point in actual
    ]
    owners = [-1] * len(expected)

    def assign(member: int, seen: set[int]) -> bool:
        for site in choices[member]:
            if site in seen:
                continue
            seen.add(site)
            if owners[site] == -1 or assign(owners[site], seen):
                owners[site] = member
                return True
        return False

    return all(assign(member, set()) for member in range(len(actual)))


def grid_has_centre_datum(feature: PatternFeature) -> bool:
    """Accept a grid centre only when its members prove the declared lattice."""
    if (
        feature.pattern != "grid"
        or feature.grid is None
        or feature.rows is None
        or feature.cols is None
        or feature.rows * feature.cols != feature.count
        or len(feature.members) != feature.count
    ):
        return False
    row_pitch, col_pitch = feature.grid
    angle = radians(feature.angle or 0.0)
    u, v = plane_axes(feature.frame.axis)
    centre = feature.frame.origin
    expected: list[Point] = []
    for row in range(feature.rows):
        for col in range(feature.cols):
            across = (col - (feature.cols - 1) / 2) * col_pitch
            along = (row - (feature.rows - 1) / 2) * row_pitch
            du = across * cos(angle) - along * sin(angle)
            dv = across * sin(angle) + along * cos(angle)
            expected.append(
                (
                    centre[0] + du * u[0] + dv * v[0],
                    centre[1] + du * u[1] + dv * v[1],
                    centre[2] + du * u[2] + dv * v[2],
                )
            )
    return _grid_points_match(feature.members, expected)


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


register_envelope_feature_type(EnvelopeFeature)


@dataclass(frozen=True)
class SlotFeature:
    """A milled slot / reduced across-flats section — width (the defining size,
    across ``width_axis``) + length (along ``long_axis``). Carries the slot's
    in-plane geometry so the renderer can place the size + position dims in the
    view the two axes span (the recogniser's `Slot`, normalised into the IR)."""

    #: The compiled stem this feature's position is minted under — see
    #: :attr:`HoleFeature.LOCATION_STEM` for why it is declared here (#966).
    LOCATION_STEM: ClassVar[str] = "location_slot"

    frame: Frame
    width_axis: str
    long_axis: str
    width: float
    length: float
    w_center: float
    lo: float
    hi: float
    end_radius: float | None = None
    kind: ClassVar[str] = "slot"

    def parameters(self) -> list[DimParameter]:
        parameters = [
            DimParameter("length", "slot_width", self.width),
            DimParameter("length", "slot_length", self.length),
        ]
        if self.end_radius is not None:
            parameters.append(DimParameter("radius", "slot_end_radius", self.end_radius))
        return parameters

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class PocketFeature:
    """A blind pocket with width, length and uniform or maximum depth.

    A positive ``corner_radius`` describes four equal tangent quarter-circle corners
    with positive straight sides. The constituent Blend features own their radii.
    ``mouth_axis/radius/at`` retain a principal cylindrical opening; its ``depth``
    is the maximum from the planar floor, exposed as ``pocket_max_depth.length``.
    """

    #: The compiled stem this feature's position is minted under — see
    #: :attr:`HoleFeature.LOCATION_STEM` for why it is declared here (#966).
    LOCATION_STEM: ClassVar[str] = "location_pocket"

    frame: Frame
    width_axis: str
    long_axis: str
    width: float
    length: float
    depth: float
    w_center: float
    lo: float
    hi: float
    edge_anchored: bool = False
    # Which depth end is the opening. Recognition retains this physical distinction;
    # dropping it here makes equal opposed-face pockets indistinguishable to correspondence.
    open_sign: int = 1
    corner_radius: float = 0.0
    mouth_axis: str | None = None
    mouth_radius: float | None = None
    mouth_at: Point | None = None
    kind: ClassVar[str] = "pocket"

    def __post_init__(self) -> None:
        if type(self.open_sign) is not int or self.open_sign not in (-1, 1):
            raise ValueError(f"pocket open_sign must be -1 or 1 (got {self.open_sign!r})")
        if self.mouth_axis is not None and (self.corner_radius or self.edge_anchored):
            raise ValueError("cylindrical mouth requires a closed rectangular pocket")
        validate_pocket_mouth(
            axis=self.mouth_axis,
            radius=self.mouth_radius,
            at=self.mouth_at,
            origin=self.frame.origin,
            depth_axis=self.depth_axis,
            width_axis=self.width_axis,
            long_axis=self.long_axis,
            width=self.width,
            length=self.length,
            depth=self.depth,
            open_sign=self.open_sign,
        )
        radius = _strict_finite_real("pocket corner_radius", self.corner_radius)
        if radius < 0 or (
            radius and (self.edge_anchored or 2 * radius >= min(self.width, self.length))
        ):
            raise ValueError(
                "pocket corner_radius requires a closed profile with positive straight sides"
            )

    @property
    def depth_axis(self) -> str:
        """The axis normal to the opening (into the material) — the view the callout
        reads in is the one normal to it."""
        return next(a for a in "xyz" if a not in (self.width_axis, self.long_axis))

    def parameters(self) -> list[DimParameter]:
        result = [
            DimParameter("length", "pocket_width", self.width),
            DimParameter("length", "pocket_length", self.length),
        ]

        if self.mouth_axis is None:
            result.append(DimParameter("length", "pocket_depth", self.depth))
        else:
            result.append(DimParameter("length", "pocket_max_depth", self.depth))
        return result

    def references(self) -> list[Datum]:
        return []


# Keep the historical qualified names used by pickle and public reflection.
for _record in (
    TurnedProfileIdentity,
    AngularReference,
    CylindricalReference,
    CircularReference,
    Frame,
    Datum,
    DimParameter,
    ToleranceDecoration,
    NominalRequirement,
    ThreadRequirement,
    ThreadOperation,
    KnurlRequirement,
    Feature,
    HoleFeature,
    StepFeature,
    PatternFeature,
    EnvelopeFeature,
    SlotFeature,
    PocketFeature,
):
    _record.__module__ = "draftwright.model.ir"
del _record
