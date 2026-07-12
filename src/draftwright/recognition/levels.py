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


@dataclass(frozen=True, order=True)
class FaceLevel:
    """A recognised horizontal face level — its Z coordinate. A prismatic part's step
    heights are dimensioned from these. ``order=True`` so the recogniser returns a
    deterministically sorted list."""

    z: float


@dataclass(frozen=True, order=True)
class StepShoulder:
    """A recognised step/rebate shoulder (#555). ``axis`` is the riser's normal axis
    ("x"/"y"); ``position`` is the world coord of the shoulder along it. ``order=True``
    so recognisers can return a deterministically sorted list."""

    axis: str
    position: float


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


def recognise_step_shoulders(
    part, *, levels, min_area_frac: float = 0.15, tol: float = 0.5
) -> list[StepShoulder]:
    """Return the in-plane positions of a prismatic part's step shoulders — the
    ``(axis, position)`` where a step/rebate changes height (#555).

    ``recognise_face_levels`` recovers the step *heights* (Z); this recovers *where along
    the part* each shoulder sits, so a stepped block is fully constrained (two different
    shoulder positions no longer draw the same sheet). A shoulder is the **riser**: an
    interior, large, *planar* vertical face (normal in the XY plane) whose lower Z edge
    rests on one of the given *levels* (the raised region rises from that level). That
    ties it to a genuine step and, by requiring a planar face, excludes a cylindrical
    counterbore/bore wall; requiring the lower edge at a step level excludes a slot's
    walls (a through slot has no step level). ``axis`` is the riser's normal axis
    ("x"/"y"); ``position`` is the world coord of the shoulder along it.

    The riser must also span the WHOLE part edge-to-edge on its perpendicular in-plane
    axis (reach both envelope edges within *tol*); this is what separates a step/rebate
    from a raised pad/island or a blind pocket, whose walls rise from a level but are
    bounded. The conservative side of that cut: a partial *corner notch* (a step reaching
    only one edge) or a step whose riser is inset from the edges by end fillets/chamfers
    larger than *tol* is not recognised — the alternative, loosening the span test,
    re-admits pads/pockets, so the full-span sharp-edged step is the recognised class
    (partial/filleted-end steps are a future refinement).

    Returns a sorted, deduplicated list. Empty when *levels* is empty (no step) or no
    riser qualifies.
    """
    if not levels:
        return []
    bb = part.bounding_box()
    ext = {"x": bb.max.X - bb.min.X, "y": bb.max.Y - bb.min.Y, "z": bb.max.Z - bb.min.Z}
    lo = {"x": bb.min.X, "y": bb.min.Y}
    hi = {"x": bb.max.X, "y": bb.max.Y}
    out: list[StepShoulder] = []
    for f in part.faces():
        s = BRepAdaptor_Surface(f.wrapped)
        if s.GetType() != GeomAbs_Plane:
            continue
        try:
            nv = f.normal_at()
        except Exception:  # noqa: BLE001 — a degenerate face has no clean normal
            continue
        if abs(nv.Z) > 0.01:
            continue  # a riser is vertical (in-plane normal)
        axis = "x" if abs(nv.X) > 0.99 else ("y" if abs(nv.Y) > 0.99 else None)
        if axis is None:
            continue
        loc = s.Plane().Location()
        pos = loc.X() if axis == "x" else loc.Y()
        if not (lo[axis] + tol < pos < hi[axis] - tol):
            continue  # interior only — an envelope face is not a shoulder
        fb = f.bounding_box()
        if not any(abs(fb.min.Z - z) < tol for z in levels):
            continue  # rises from a step level (not a through slot's wall)
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
        if flo > lo[other] + tol or fhi < hi[other] - tol:
            continue  # not full-span → a pad/pocket wall, not a step shoulder
        cross = ext[other] * ext["z"]
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(f.wrapped, props)
        if cross <= 0 or props.Mass() < min_area_frac * cross:
            continue  # a large riser, not an incidental feature face
        out.append(StepShoulder(axis, round(pos, 3)))
    return sorted(set(out))
