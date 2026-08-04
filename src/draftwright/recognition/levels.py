"""levels — horizontal face-level recognition (ADR 0007).

``recognise_face_levels`` returns the Z-coords of a part's horizontal planar faces —
the step levels of a *prismatic* part. It is the complement of ``recognise_turned_steps``
(turned.py): a box-stepped part has no cylinders, so the OD-silhouette recogniser
cannot see its steps, while a turned shaft's shoulders are better filtered by the OD
silhouette than by a raw face scan (#191 — the two are dispatched by part class in
`analysis`, not duplicates). Bottom of the recognition DAG: depends only on
build123d/OCP.
"""

from __future__ import annotations

from dataclasses import dataclass

from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepGProp import BRepGProp
from OCP.GeomAbs import GeomAbs_Plane
from OCP.GProp import GProp_GProps

from draftwright.recognition._record import Record


@dataclass(frozen=True, order=True)
class FaceLevel(Record):
    """A recognised horizontal face level — its Z coordinate. A prismatic part's step
    heights are dimensioned from these. ``order=True`` so the recogniser returns a
    deterministically sorted list."""

    z: float


@dataclass(frozen=True, order=True)
class StepShoulder(Record):
    """A recognised step/rebate shoulder (#555). ``axis`` is the riser's normal axis
    ("x"/"y"); ``position`` is the world coord of the shoulder along it. ``order=True``
    so recognisers can return a deterministically sorted list."""

    axis: str
    position: float


@dataclass(frozen=True, order=True)
class RiserEvidence(Record):
    """One candidate step riser, recognised WITHOUT reference to any level set (#1025).

    :func:`recognise_risers` scans the solid once and emits these; each consumer then calls
    :func:`project_step_shoulders` with the level set *it* cares about. The split exists
    because the scan is the cost and the levels are only a filter: model construction
    projects over levels filtered by plate and pocket ownership, while critique must project
    over the unfiltered ones (ADR 0015 forbids lint taking its inventory from the model). One
    scan, two answers — rather than one scan per asker.

    ``z_lo``/``z_hi`` are the riser face's vertical extent, kept raw so the level test stays
    in the projection. ``lo_at_envelope``/``hi_at_envelope`` pre-answer the part of the
    oblique tie-test that does NOT depend on levels — whether that end sits on the part's top
    or bottom — so the projection needs the levels and nothing else about the solid.

    ``order=True`` for a deterministic recogniser return, per ADR 0013.
    """

    vertical: bool
    axis: str
    positions: tuple[float, ...]
    other_axis: str
    other_positions: tuple[float, ...]
    z_lo: float
    z_hi: float
    lo_at_envelope: bool
    hi_at_envelope: bool
    #: The tolerance this evidence was scanned with, so the projection matches it by default.
    #: Without this the split silently broke a non-default ``tol``: the old one-stage call used
    #: one value for the geometric gates AND the level ties, whereas ``recognise_risers(part,
    #: tol=0.1)`` followed by a bare ``project_step_shoulders(...)`` mixed 0.1 with the
    #: projection's own default (Codex #1031 r1). Carried on the record rather than passed
    #: separately because a caller who has the evidence should not have to remember how it was
    #: produced.
    tol: float = 0.5


def recognise_face_levels(
    part, *, tol: float = 0.5, min_area_frac: float = 0.0
) -> list[FaceLevel]:
    """Return the sorted unique horizontal (normal≈±Z) face levels as :class:`FaceLevel`
    records — one per distinct Z of a horizontal planar face.

    Uses tol-bucket deduplication but returns the actual face Z, not the rounded
    bucket centre, so dimension labels match the true geometry.

    When *min_area_frac* > 0, a Z level is kept only if the total area of its
    horizontal faces is at least ``min_area_frac × (x_size × y_size)`` (the
    part's plan footprint). This drops sub-feature faces — e.g. fragments of
    engraved text/numbers — that are not real steps and would otherwise be
    dimensioned as phantom shoulders (staircase.step review).
    """
    buckets: dict = {}  # bucket key -> representative z
    areas: dict = {}  # bucket key -> total horizontal-face area
    for face in part.faces():
        surf = BRepAdaptor_Surface(face.wrapped)
        if surf.GetType() == GeomAbs_Plane:
            ax = surf.Plane().Axis().Direction()
            if abs(ax.Z()) > 0.99:
                z = surf.Plane().Location().Z()
                key = round(z / tol) * tol
                buckets.setdefault(key, z)
                if min_area_frac > 0.0:
                    props = GProp_GProps()
                    BRepGProp.SurfaceProperties_s(face.wrapped, props)
                    areas[key] = areas.get(key, 0.0) + props.Mass()
    if min_area_frac > 0.0:
        bb = part.bounding_box()
        footprint = (bb.max.X - bb.min.X) * (bb.max.Y - bb.min.Y)
        threshold = min_area_frac * footprint
        return sorted(
            FaceLevel(z) for key, z in buckets.items() if areas.get(key, 0.0) >= threshold
        )
    return sorted(FaceLevel(z) for z in buckets.values())


# Minimum horizontal-face area (as a fraction of the plan footprint) for a Z level to count
# as a genuine prismatic step — drops an incidental tiny face (a blind-pocket floor, a small
# pad top) that would otherwise read as a phantom step rung (#578 review / staircase.step).
_STEP_MIN_AREA_FRAC = 0.01


def step_level_zs(part, *, tol: float = 0.6) -> list[float]:
    """The interior prismatic step Z-levels: the area-filtered horizontal face levels strictly
    inside the part height (``base + tol < z < top - tol``). The single source of truth for the
    step-height ladder (``analysis.py``) and the ``declare.step_level`` object flavour — using
    the raw, unfiltered :func:`recognise_face_levels` in one and this gate in the other let a
    tiny incidental face leak in as a phantom level, diverging the two paths (#578 review)."""
    bb = part.bounding_box()
    return [
        fl.z
        for fl in recognise_face_levels(part, min_area_frac=_STEP_MIN_AREA_FRAC)
        if bb.min.Z + tol < fl.z < bb.max.Z - tol
    ]


def recognise_risers(
    part, *, min_area_frac: float = 0.15, tol: float = 0.5
) -> list[RiserEvidence]:
    """Scan *part* once for candidate step risers, independent of any level set (#1025).

    This is the expensive half of the old ``recognise_step_shoulders``: the full
    ``part.faces()`` walk and every geometric gate that does not need levels — planarity,
    in-plane axis, the full-span test that separates a step from a pad or blind pocket, the
    interior-position test, the structural-ramp size floor and the area floor.

    What it deliberately does NOT do is decide which candidates rise from a *recognised*
    level; that is :func:`project_step_shoulders`, because the answer differs per consumer
    and re-scanning per consumer is the cost ADR 0017 exists to remove.

    See :class:`RiserEvidence`. Returns a sorted, deduplicated list.

    ``recognise_face_levels`` recovers the step *heights* (Z); the projection over this
    evidence recovers *where along the part* each shoulder sits, so a stepped block is fully
    constrained (two different shoulder positions no longer draw the same sheet). A shoulder
    is either a vertical riser or an endpoint of a full-span slanted transition. The latter's
    two stations, together with the adjacent height levels, define the ramp without
    a redundant angle dimension (#897).

    The riser must also span the WHOLE part edge-to-edge on its perpendicular in-plane
    axis (reach both envelope edges within *tol*); this is what separates a step/rebate
    from a raised pad/island or a blind pocket, whose walls rise from a level but are
    bounded. The conservative side of that cut: a partial *corner notch* (a step reaching
    only one edge) or a step whose riser is inset from the edges by end fillets/chamfers
    larger than *tol* is not recognised — the alternative, loosening the span test,
    re-admits pads/pockets, so the full-span sharp-edged step is the recognised class
    (partial/filleted-end steps are a future refinement).
    """
    bb = part.bounding_box()
    ext = {"x": bb.max.X - bb.min.X, "y": bb.max.Y - bb.min.Y, "z": bb.max.Z - bb.min.Z}
    lo = {"x": bb.min.X, "y": bb.min.Y}
    hi = {"x": bb.max.X, "y": bb.max.Y}
    out: list[RiserEvidence] = []
    for f in part.faces():
        s = BRepAdaptor_Surface(f.wrapped)
        if s.GetType() != GeomAbs_Plane:
            continue
        try:
            nv = f.normal_at()
        except Exception:  # noqa: BLE001 — a degenerate face has no clean normal
            continue
        vertical = abs(nv.Z) <= 0.01
        axis = (
            "x"
            if abs(nv.X) > 0.01 and abs(nv.Y) <= 0.01
            else ("y" if abs(nv.Y) > 0.01 and abs(nv.X) <= 0.01 else None)
        )
        if axis is None:
            continue
        fb = f.bounding_box()
        other = "y" if axis == "x" else "x"
        # A step/rebate shoulder crosses the WHOLE part edge-to-edge on the
        # perpendicular in-plane axis — its riser reaches both envelope edges. A raised
        # pad / island or a blind pocket has bounded walls that do NOT span the part, so
        # this excludes them (they rise from a level and can clear the area gate, but
        # they are not steps — the level tie alone doesn't separate a blind pocket from a
        # through slot). Without this, a central pad or blind pocket is mis-located as a
        # shoulder (#555 review).
        flo = fb.min.X if other == "x" else fb.min.Y
        fhi = fb.max.X if other == "x" else fb.max.Y
        full_span = flo <= lo[other] + tol and fhi >= hi[other] - tol
        positions: tuple[float, ...]
        other_positions: tuple[float, ...] = ()
        if vertical:
            if not full_span:
                continue  # a bounded vertical wall belongs to a pad/pocket
            loc = s.Plane().Location()
            pos = loc.X() if axis == "x" else loc.Y()
            if not (lo[axis] + tol < pos < hi[axis] - tol):
                continue  # interior only — an envelope face is not a shoulder
            # The "rises from a step level" test lives in project_step_shoulders — it is the
            # one gate whose answer depends on which level set the asker holds (#1025).
            positions = (pos,)
        else:
            # A genuine oblique profile face contributes both transition
            # stations. A bounded slanted interruption also contributes its
            # extrusion endpoints on the perpendicular in-plane axis, defining
            # both the ramp and its width (#897).
            if abs(nv.Z) <= 0.01 or fb.max.Z - fb.min.Z <= tol:
                continue
            axis_positions = (
                fb.min.X if axis == "x" else fb.min.Y,
                fb.max.X if axis == "x" else fb.max.Y,
            )
            if not full_span:
                other_size = fhi - flo
                axis_size = axis_positions[1] - axis_positions[0]
                if (
                    other_size < 0.1 * ext[other]
                    or axis_size < 0.1 * ext[axis]
                    or fb.max.Z - fb.min.Z < 0.1 * ext["z"]
                ):
                    continue  # ordinary edge-break chamfer, not a structural ramp
                other_positions = (flo, fhi)
            positions = axis_positions
        cross = ext[other] * ext["z"]
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(f.wrapped, props)
        area_floor = (
            min_area_frac * cross if full_span else 0.5 * ((fhi - flo) * (fb.max.Z - fb.min.Z))
        )
        if cross <= 0 or props.Mass() < area_floor:
            continue  # a large riser, not an incidental feature face
        out.append(
            RiserEvidence(
                vertical=vertical,
                axis=axis,
                positions=tuple(
                    round(pos, 3) for pos in positions if lo[axis] + tol < pos < hi[axis] - tol
                ),
                other_axis=other,
                other_positions=tuple(
                    round(pos, 3)
                    for pos in other_positions
                    if lo[other] + tol < pos < hi[other] - tol
                ),
                z_lo=fb.min.Z,
                z_hi=fb.max.Z,
                # The level-independent half of the oblique tie-test: an end sitting on the
                # part's top or bottom is structural whatever the level set says.
                lo_at_envelope=abs(fb.min.Z - bb.min.Z) < tol or abs(fb.min.Z - bb.max.Z) < tol,
                hi_at_envelope=abs(fb.max.Z - bb.min.Z) < tol or abs(fb.max.Z - bb.max.Z) < tol,
                tol=tol,
            )
        )
    return sorted(set(out))


def project_step_shoulders(risers, *, levels, tol: float | None = None) -> list[StepShoulder]:
    """Project :func:`recognise_risers` evidence onto *levels* — the pure half (#1025).

    A candidate riser counts as a step shoulder only if it rises from a level the caller
    recognises: a vertical riser's foot must sit on one, and an oblique ramp's two ends must
    each sit on one or on the part envelope. That is the whole level dependency, and it is
    the whole reason the old ``recognise_step_shoulders`` could not be hoisted into the
    shared aggregate — its answer depends on who is asking.

    Model construction passes levels filtered by plate and pocket ownership; critique passes
    the unfiltered geometry ladder, because ADR 0015 forbids lint taking its inventory from
    the model. Both project the same evidence; neither rescans the solid.

    No *part* argument, by construction: this cannot look at geometry, so it cannot become a
    second recognition site. Returns a sorted, deduplicated list; empty when *levels* is empty
    (a part with no recognised step has no shoulders to locate).

    *tol* defaults to the tolerance the evidence was scanned with, so a two-stage call is
    equivalent to the old single-stage one at ANY tolerance, not just the default. Pass it
    explicitly only to project more or less tightly than the scan deliberately.
    """
    if not levels:
        return []
    risers = list(risers)
    if not risers:
        return []
    if tol is None:
        tol = risers[0].tol

    def tied(z: float, at_envelope: bool) -> bool:
        return at_envelope or any(abs(z - level) < tol for level in levels)

    out: list[StepShoulder] = []
    for r in risers:
        if r.vertical:
            if not any(abs(r.z_lo - level) < tol for level in levels):
                continue  # rises from a step level (not a through slot's wall)
        elif not (tied(r.z_lo, r.lo_at_envelope) and tied(r.z_hi, r.hi_at_envelope)):
            continue  # drafted/incidental face not tied to recognised profile levels
        out.extend(StepShoulder(r.axis, pos) for pos in r.positions)
        out.extend(StepShoulder(r.other_axis, pos) for pos in r.other_positions)
    return sorted(set(out))
