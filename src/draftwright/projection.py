"""View projection — silhouette recovery and the isometric view (#138 / ADR 0005, P2).

Two concerns, both *below* `make_drawing` in the DAG (it imports these, never the
reverse): silhouette recovery (`_exactify_silhouettes`/`_raw_view_projector` —
replace HLR's faceted silhouette splines with exact circles/arcs for turned
features, #67) and the isometric view (`_project_iso`/`_fit_iso_view` — (re-)project
and fit the orientation iso into its page zone). `dwg` is duck-typed
(views/draft/add/add_view/_coords), so this module imports only `_core` + build123d.
"""

from __future__ import annotations

import logging
import math

import numpy as np
from build123d import Compound, Edge, GeomType, Location, Plane, ThreePointArc, Vector
from build123d_drafting.helpers import Note, ViewCoordinates, view_axes
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAbs import (
    GeomAbs_Cone,
    GeomAbs_Cylinder,
    GeomAbs_Sphere,
    GeomAbs_SurfaceOfRevolution,
    GeomAbs_Torus,
)

from draftwright._core import Analysis, _iso_bbox, place_annotation

_log = logging.getLogger(__name__)


_SILHOUETTE_TOL = 0.12


def _raw_view_projector(axes, look_at_scaled):
    """Build a ``gp_Pnt -> (x, y)`` projector into raw viewport coordinates.

    ``project_to_viewport`` returns edges centred on ``look_at`` with the world
    axes mapped to page X/Y per :func:`view_axes`.  This reproduces that 2D
    mapping for an arbitrary point on the *scaled* solid, so a revolution axis's
    location can be projected into the same frame as the projected edges (before
    the view is placed at its page position).
    """
    idx = {"world_X": 0, "world_Y": 1, "world_Z": 2}
    x_terms = [(idx[w], s) for w, (p, s) in axes.items() if p == "page_X"]
    y_terms = [(idx[w], s) for w, (p, s) in axes.items() if p == "page_Y"]
    lx, ly, lz = look_at_scaled
    center = (lx, ly, lz)

    def proj(pnt):
        c = (pnt.X(), pnt.Y(), pnt.Z())
        x = sum((c[i] - center[i]) * s for i, s in x_terms)
        y = sum((c[i] - center[i]) * s for i, s in y_terms)
        return (x, y)

    return proj


def _exactify_silhouettes(edges, faces, view_dir, proj_fn, tol=_SILHOUETTE_TOL):
    """Replace faceted silhouette splines with exact circles/arcs (#67).

    ``project_to_viewport``'s HLR emits the silhouette ("outline") of a curved
    face as an approximating BSpline (typically a rational degree 2–4 curve), so
    an imported-STEP turned feature — or the concentric arc of a gear-tooth tip —
    projects as a spline rather than a true circle, even though it is
    geometrically circular.  A surface of revolution viewed along its axis has a
    circular silhouette whose CENTRE we know exactly (the projected axis); we
    replace a spline only when points sampled along it are equidistant from such
    a known centre within ``tol`` (page-mm) — the radius comes from the samples,
    the centre is never fitted.  Equidistance holds at any parametrisation, so
    the test is independent of the spline's degree.  Inapplicable edges are
    returned untouched.

    Candidate centres are gathered per revolution axis, not pooled across all
    faces: pooling lets one feature's axis turn a neighbouring feature's grazing
    silhouette into a spurious circle.

    Args:
        edges: projected edges from ``project_to_viewport`` (raw view coords).
        faces: faces of the projected (scaled) solid.
        view_dir: unit world direction of the view axis, e.g. ``(0, 1, 0)``.
        proj_fn: ``gp_Pnt -> (x, y)`` into the same raw view coords as ``edges``.

    Returns:
        ``(new_edges, replaced_count)``.
    """
    centres = []
    seen = set()

    def _add_centre(pnt):
        # Coaxial faces (every counterbore step, fillet-adjacent wall, …) project
        # to the same centre; dedup so the per-edge test loop below stays short.
        c = np.array(proj_fn(pnt))
        key = (round(float(c[0]), 3), round(float(c[1]), 3))
        if key not in seen:
            seen.add(key)
            centres.append(c)

    for f in faces:
        surf = BRepAdaptor_Surface(f.wrapped)
        st = surf.GetType()
        if st == GeomAbs_Sphere:
            _add_centre(surf.Sphere().Location())
            continue
        elif st == GeomAbs_Torus:
            ax = surf.Torus().Position().Axis()
        elif st == GeomAbs_SurfaceOfRevolution:
            ax = surf.AxeOfRevolution()
        elif st in (GeomAbs_Cylinder, GeomAbs_Cone):
            ax = (surf.Cylinder() if st == GeomAbs_Cylinder else surf.Cone()).Axis()
        else:
            continue
        d = ax.Direction()
        if abs(d.X() * view_dir[0] + d.Y() * view_dir[1] + d.Z() * view_dir[2]) > 0.999:
            _add_centre(ax.Location())

    if not centres:
        return list(edges), 0

    # A silhouette circle cannot exceed the part's own projected footprint;
    # this guards the degenerate-fragment case below.
    gb = Compound(children=list(edges)).bounding_box()

    def replacement(e):
        if e.geom_type != GeomType.BSPLINE:
            return None  # real circles/lines/arcs are already exact
        # Sample the curve and test equidistance from a known centre (see the
        # function docstring) — robust to the spline's degree and knot spacing.
        n_samp = 33
        try:
            pts = np.array([[(p := e @ (i / (n_samp - 1))).X, p.Y, p.Z] for i in range(n_samp)])
        except Exception:
            return None
        for c2 in centres:
            dist = np.linalg.norm(pts[:, :2] - c2, axis=1)
            if dist.max() - dist.min() < tol:
                R = dist.mean()
                z = pts[0, 2]
                # A real silhouette circle cannot exceed the part's own projected
                # footprint.  A sliver that happens to be equidistant from a
                # distant axis would otherwise fit a giant circle/arc.
                if (
                    c2[0] - R < gb.min.X - 2
                    or c2[0] + R > gb.max.X + 2
                    or c2[1] - R < gb.min.Y - 2
                    or c2[1] + R > gb.max.Y + 2
                ):
                    continue

                def snap(p, c2=c2, R=R, z=z):
                    v = p[:2] - c2
                    v = v / np.linalg.norm(v) * R
                    return (c2[0] + v[0], c2[1] + v[1], z)

                if np.linalg.norm(pts[0, :2] - pts[-1, :2]) < tol:
                    span = max(np.ptp(pts[:, 0]), np.ptp(pts[:, 1]))
                    # A closed loop must actually span the circle it claims;
                    # a tiny closed sliver is a grazing fragment, not a rim.
                    if span < 4 * tol or abs(span - 2 * R) > 4 * tol:
                        continue
                    return Edge.make_circle(R, Plane((c2[0], c2[1], z)))
                try:
                    return ThreePointArc(snap(pts[0]), snap(pts[len(pts) // 2]), snap(pts[-1]))
                except Exception:
                    return None  # degenerate (collinear/coincident) — keep the polyline
        return None

    out, n = [], 0
    for e in edges:
        rep = replacement(e)
        out.append(rep if rep is not None else e)
        n += rep is not None
    return out, n


# Upper bound on how far the iso view may grow beyond sheet scale when fitted to
# its zone.  The iso is an orientation aid, not a measured view: left uncapped it
# fills the (now often large) empty rectangle and can dwarf the dimensioned
# orthographic views (up to ~8× on an oversized sheet).  Capped just above sheet
# scale so it still fills modest zones without dominating.  Shrinking to fit a
# small zone is never capped.
_ISO_MAX_GROW = 1.3


def project_view_geometry(scale, name, shape, camera, up, position, *, look_at, scaled):
    """Project *shape* into a view's placed geometry + coordinates — the pure core of
    :meth:`Drawing._add_view`, returning ``(placed, placed_hid, ViewCoordinates)`` WITHOUT mutating
    a Drawing (#830). ``Drawing._add_view`` wraps it (stores the result under ``name``); the detail
    renderer projects a band into a scratch with it and commits the view only if the feature draws
    legible dims — so no view is ever placed-then-rolled-back.

    *scale* is the world→page scale the coordinates encode; *shape* is in world (unscaled) space
    unless *scaled* is True. *camera*/*up*/*look_at* are in scaled space (the standard-view
    convention). Raises ``ValueError`` when the projection is empty (bad camera/look_at)."""
    shape_s = shape if scaled else shape.scale(scale)
    vis, hid = shape_s.project_to_viewport(camera, up, look_at)
    vl, hl = list(vis), list(hid)
    if not vl and not hl:
        raise ValueError(
            f"project_to_viewport returned empty geometry for view {name!r} "
            f"(camera {camera}) — check the camera position and look_at."
        )
    axes = view_axes(camera, up, look_at)
    # Recover exact circles for revolution silhouettes that HLR projected as approximating splines
    # (#67) — a no-op when no revolution axis is parallel to the view direction (iso/section views).
    if vl:
        vd = Vector(look_at[0] - camera[0], look_at[1] - camera[1], look_at[2] - camera[2])
        vd = vd.normalized()
        proj = _raw_view_projector(axes, look_at)
        vl, n_circ = _exactify_silhouettes(vl, shape_s.faces(), (vd.X, vd.Y, vd.Z), proj)
        if n_circ:
            _log.info("  %s: %d silhouette spline(s) refit to circles", name, n_circ)
    loc = Location((position[0], position[1], 0))
    placed = Compound(children=vl).locate(loc)
    placed_hid = Compound(children=hl).locate(loc) if hl else None
    cx, cy, cz = look_at[0] / scale, look_at[1] / scale, look_at[2] / scale
    coords = ViewCoordinates(axes, position[0], position[1], cx, cy, cz, scale)
    _log.info("  %s: %d visible / %d hidden", name, len(vl), len(hl))
    return placed, placed_hid, coords


def _bbox_within(bb, region, tol: float = 0.5) -> bool:
    """True if (min_x, min_y, max_x, max_y) *bb* fits inside *region* within *tol*."""
    return bool(
        bb[0] >= region[0] - tol
        and bb[1] >= region[1] - tol
        and bb[2] <= region[2] + tol
        and bb[3] <= region[3] + tol
    )


def _project_iso(dwg, a: Analysis, scale, shape_s=None):
    """(Re-)project the iso view at *scale* (an absolute factor, not a fraction).

    Pass *shape_s* when the part is already scaled by *scale* to skip the copy.
    """
    la = (a.cx * scale, a.cy * scale, a.cz * scale)
    off = (a.bbox_max * scale + 100) / math.sqrt(3)
    # View from +X, -Y, +Z so the iso is orientation-consistent with the orthographic set —
    # front (-Y), plan (+Z), right (+X). A +Y camera would show the *rear* Y face against a
    # front view, mirroring asymmetric features (#620).
    camera = (la[0] + off, la[1] - off, la[2] + off)
    dwg._add_view(
        "iso",
        shape_s if shape_s is not None else a.part.scale(scale),
        camera,
        (0, 0, 1),
        (a.ISO_X, a.ISO_Y),
        look_at=la,
        scaled=True,
    )
    # add_view builds ViewCoordinates from a collapsed view_axes() mapping, which
    # helpers (>=0.11) cannot project for the oblique iso (pp() needs the full
    # foreshortening basis). Rebuild from the raw viewport so dwg.at("iso", ...)
    # maps world points correctly — also covers an iso re-projected at a
    # different scale than the sheet. Through the public override verb, not a
    # direct _coords poke (#699 slice d).
    dwg._set_view_coordinates(
        "iso",
        ViewCoordinates.from_viewport(
            camera, (0, 0, 1), la, a.ISO_X, a.ISO_Y, a.cx, a.cy, a.cz, scale
        ),
    )


def _fit_iso_view(dwg, a: Analysis, annotate: bool = True):
    """Scale the iso view to fill its page zone, captioning it NTS when the
    scale differs from sheet scale.  Pass ``annotate=False`` to suppress the
    NTS note — used on the finalize detail-refit, which re-fits an iso whose
    note the build already placed (both the auto and ``auto_dims=False`` build
    paths label it, so a second add would be redundant).

    The iso is always centred at (ISO_X, ISO_Y) which sits at the centre of
    the available zone.  The projection is linear, so the factor needed to
    fill the zone can be computed from the measured extents without iteration.

    - Overflow (needed < 1): shrink with 2 % safety margin.
    - Under-fill (needed > 1): grow to 90 % of zone, leaving breathing room.
    - Within 5 % of sheet scale: leave as-is (no NTS label).
    """
    # Use the precomputed iso zone (largest empty rectangle).  A section view
    # only constrains the iso region when it shares the iso's y-range; one that
    # sits entirely below the iso (e.g. when the iso is in an upper-right zone)
    # leaves the region's left edge untouched.
    region_left = a.iso_left_limit
    if "section_aa" in dwg.views:
        sec_vis, sec_hid = dwg.views["section_aa"]
        sec_bb = sec_vis.bounding_box()
        sec_right = sec_bb.max.X
        sec_y0, sec_y1 = sec_bb.min.Y, sec_bb.max.Y
        if sec_hid:
            shb = sec_hid.bounding_box()
            sec_right = max(sec_right, shb.max.X)
            sec_y0, sec_y1 = min(sec_y0, shb.min.Y), max(sec_y1, shb.max.Y)
        if sec_y0 < a.iso_top_limit and a.iso_bottom_limit < sec_y1:
            region_left = max(region_left, sec_right + 4)
    region = (region_left, a.iso_bottom_limit, a.iso_right_limit, a.iso_top_limit)
    bb = _iso_bbox(dwg)
    ratios = [
        avail / extent
        for extent, avail in (
            (a.ISO_X - bb[0], a.ISO_X - region[0]),
            (bb[2] - a.ISO_X, region[2] - a.ISO_X),
            (a.ISO_Y - bb[1], a.ISO_Y - region[1]),
            (bb[3] - a.ISO_Y, region[3] - a.ISO_Y),
        )
        if extent > 0
    ]
    needed = min(ratios, default=1.0)
    if needed >= 1.0:
        # Iso fits; grow to 90 % of zone — leaves comfortable breathing room.
        margin_pct = 0.90
    else:
        # Iso overflows; shrink to just fit with 2 % safety margin.
        margin_pct = 0.98
    factor = math.floor(needed * margin_pct * 10000) / 10000
    if needed >= 1.0:
        factor = max(factor, 1.0)  # grow branch must never shrink
        factor = min(factor, _ISO_MAX_GROW)  # never dwarf the dimensioned views
    if abs(factor - 1.0) < 0.05:
        return  # within 5 % of sheet scale — no rescale, no NTS label
    _project_iso(dwg, a, a.SCALE * factor)
    bb = _iso_bbox(dwg)
    if factor < 1.0 and not _bbox_within(bb, region):
        _log.warning("Iso view still overflows its page region at %g× sheet scale", factor)
    if annotate:
        font = dwg.draft.font_size
        place_annotation(
            dwg.registry,
            dwg.items,
            Note(
                "ISO VIEW (NTS)",
                (a.ISO_X, max(bb[1] - 2 * font, a.margin + font)),
                dwg.draft,
            ),
            "note_iso_nts",
        )
    _log.info("Iso view scaled to %g× sheet scale%s", factor, " (NTS)" if annotate else "")
