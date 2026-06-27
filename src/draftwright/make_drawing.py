"""make_drawing — Zero-AI STEP-to-technical-drawing pipeline.

Produces a 4-view third-angle technical drawing (front, plan, side, isometric)
with automatic dimension selection from face-geometry analysis.

Typical usage::

    from build123d_drafting.make_drawing import make_drawing
    svg_path, dxf_path = make_drawing("part.step", title="BRACKET", number="DWG-042")

CLI (registered as ``make-drawing``)::

    make-drawing part.step
    make-drawing part.step --title "BRACKET" --number DWG-042
    make-drawing part.step --script   # write editable .py instead
    make-drawing part.step --out /tmp/output
"""

from __future__ import annotations

import argparse
import functools
import logging
import math
import re
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

import numpy as np
from build123d import (
    Align,
    Circle,
    Color,
    Compound,
    Edge,
    ExportDXF,
    ExportSVG,
    GeomType,
    LineType,
    Location,
    Mode,
    Plane,
    Shape,
    Text,
    ThreePointArc,
    Vector,
)
from build123d_drafting.features import (
    BoltCircle,
    HoleSpec,
    RectGrid,
    analyse_cylinders,
    feature_diameters,
    find_hole_patterns,
    find_holes,
    full_cylinders,
)
from build123d_drafting.helpers import (
    Leader,
    LintIssue,
    Note,
    TitleBlock,
    ViewCoordinates,
    annotate,
    draft_preset,
    lint_drawing,
    set_page,
    view_axes,
)
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepGProp import BRepGProp
from OCP.GeomAbs import (
    GeomAbs_Cone,
    GeomAbs_Cylinder,
    GeomAbs_Plane,
    GeomAbs_Sphere,
    GeomAbs_SurfaceOfRevolution,
    GeomAbs_Torus,
)
from OCP.GProp import GProp_GProps
from OCP.IFSelect import IFSelect_ReturnStatus
from OCP.STEPControl import STEPControl_Reader

from draftwright._core import (
    _DIAM_RE,
    _FONT_SIZE,
    _MARGIN,
    _SLOT_DIM_HEIGHT,
    _SLOT_DIM_STEP,
    _SLOT_DIM_WIDTH,
    _TABULATE_MIN_HOLES,
    _TB_CLEAR,
    _TB_H,
    Analysis,
    Strip,
    ViewZones,
    _add_title_block,
    _axis_letter,
    _dim,
    _fmt,
    _iso_bbox,
    _largest_empty_rect,
    _legible_steps,
    _log,
    _Projector,
    _tag_sequence,
)
from draftwright.annotate import _auto_annotate
from draftwright.export import (
    _export_shape,
    _render_pdf,
    add_svg_hyperlink,
    add_svg_metadata,
    fix_svg_page_size,
    sanitize_svg_arcs,
    set_dxf_metadata,
)
from draftwright.features import find_slots
from draftwright.fonts import PLEX_MONO
from draftwright.layout import (
    _greedy_strip_1d,
    _solve_strip_1d,
    fit_box,
)
from draftwright.linting import CoverageState
from draftwright.registry import AnnotationRegistry

_TB_W = 150.0
_DIM_PAD = 18.0
# Minimum acceptable projected view dimension (page-mm).  Below this, annotation
# geometry (leader wires, centre marks, bore callout elbows) can degenerate and
# cause OCCT Standard_DomainError / SIGABRT (#129).
_MIN_VIEW_MM = 10.0

_PAGE_SIZES = {
    "A4": (297.0, 210.0),
    "A3": (420.0, 297.0),
    "A2": (594.0, 420.0),
    "A1": (841.0, 594.0),
    "A0": (1189.0, 841.0),
}


# ---------------------------------------------------------------------------
# SVG post-processing
# ---------------------------------------------------------------------------


# Equidistance tolerance (page-mm) for accepting a sampled silhouette spline as
# a circle about a known projected axis.  Loose enough to swallow HLR's spline
# approximation error, tight enough not to round a genuinely off-axis curve.
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


def _import_step(path) -> Compound:
    """Read solid geometry from a STEP file via OCCT's ``STEPControl_Reader``.

    build123d's ``import_step`` uses the XCAF reader (colours, names, PMI), which
    **segfaults** on some AP242 files carrying semantic PMI — e.g. NIST CTC-02
    AP242 (#20) — before any Python code can intervene. draftwright needs only
    the solid geometry (it drops PMI presentation data anyway), so we read the
    geometry directly. Verified to produce identical shapes (solids, edges, bbox)
    to ``import_step`` on the files that read in both, minus the unused metadata.
    """
    reader = STEPControl_Reader()
    if reader.ReadFile(str(path)) != IFSelect_ReturnStatus.IFSelect_RetDone:
        raise ValueError(f"could not read STEP file {path!r}")
    reader.TransferRoots()
    return Compound(reader.OneShape())


# ---------------------------------------------------------------------------
# Geometry analysis
# ---------------------------------------------------------------------------


def dedup_diams(cyls, tol: float = 0.15) -> list:
    """Return sorted-descending deduplicated diameter list from cylinder records."""
    raw = sorted({c["diameter"] for c in cyls}, reverse=True)
    merged: list[float] = []
    for d in raw:
        if not merged or abs(d - merged[-1]) > tol:
            merged.append(round(d, 2))
    return merged


@functools.lru_cache(maxsize=512)
def _text_width(text: str, font_size: float, font_path: str = PLEX_MONO) -> float:
    """Measured rendered width (page-mm) of *text* at *font_size*.

    Uses build123d's ``Text`` — the same primitive ``Dimension``/``HoleCallout``
    stroke their labels with — so callout-width estimates use real glyph metrics
    instead of a character-count fudge (#31).  Pinned to a vendored font **file**
    (``font_path``), not a system font *name*: name resolution substitutes a
    different font on Linux, which makes this estimate — and the layout it feeds —
    platform-variant (#149). The default is the same face the annotations render
    with, so estimate and render agree.  Cached because the same numeric labels
    recur across holes and the rasterisation is the costly part.
    """
    if not text:
        return 0.0
    return (
        Text(
            txt=text,
            font_size=font_size,
            font_path=font_path,
            align=(Align.CENTER, Align.CENTER),
            mode=Mode.PRIVATE,
        )
        .bounding_box()
        .size.X
    )


def _build_table(rows, draft, block_cols=None):
    """Build a generic data-table annotation at the origin (bottom-left ``(0, 0)``).

    *rows* is a list of equal-length string tuples; ``rows[0]`` is the header,
    drawn at the top. Returns a :class:`Compound` of grid rules + cell text,
    carrying ``.table_size = (w, h)`` so it can be positioned via
    :func:`fit_box`. Column widths are sized to their content via real glyph
    metrics (:func:`_text_width`). Generic — the hole table, and gear/BOM/
    revision tables, all build through here.

    When the rows are a wrapped chart of *block_cols*-wide side-by-side blocks
    (see :func:`_wrap_rows`), each block is drawn as its own bordered grid with
    a whitespace gap between them, so the blocks read as separate tables rather
    than one run-on grid.
    """
    fs = draft.font_size
    pad = draft.pad_around_text
    row_h = fs + 2 * pad
    ncol = len(rows[0])
    # Only treat as multi-block when block_cols evenly divides the row width.
    bc = block_cols if (block_cols and ncol % block_cols == 0 and block_cols < ncol) else ncol
    block_gap = 3 * pad  # whitespace between side-by-side blocks
    col_w = [
        max(max(_text_width(str(r[c]), fs) for r in rows) + 2 * pad, fs * 2.5) for c in range(ncol)
    ]
    # Per-column left/right edges, inserting block_gap before each new block.
    lefts, rights, cursor = [], [], 0.0
    for c in range(ncol):
        if c > 0 and c % bc == 0:
            cursor += block_gap
        lefts.append(cursor)
        cursor += col_w[c]
        rights.append(cursor)
    total_w = cursor
    total_h = row_h * len(rows)
    ys = [i * row_h for i in range(len(rows) + 1)]
    children = []
    for b in range(ncol // bc):  # one bordered grid per block
        cols = range(b * bc, b * bc + bc)
        bl, br = lefts[b * bc], rights[b * bc + bc - 1]
        for x in [lefts[c] for c in cols] + [br]:  # column rules + block edges
            children.append(Edge.make_line(Vector(x, 0, 0), Vector(x, total_h, 0)))
        for y in ys:  # horizontal rules stop at the block edge, not the gap
            children.append(Edge.make_line(Vector(bl, y, 0), Vector(br, y, 0)))
    for ri, row in enumerate(rows):  # rows[0] (header) sits at the top
        cy = total_h - (ri + 0.5) * row_h
        for ci, cell in enumerate(row):
            if not str(cell):
                continue
            cx = (lefts[ci] + rights[ci]) / 2
            text = Text(
                txt=str(cell),
                font_size=fs,
                font_path=PLEX_MONO,
                align=(Align.CENTER, Align.CENTER),
                mode=Mode.PRIVATE,
            ).locate(Location((cx, cy, 0)))
            children.extend(text.faces())
    table = Compound(children=children)
    table.table_size = (total_w, total_h)
    return table


# Turned-part classification (#81): a rotational part's bounding box is
# square in XY to within _SQUARENESS_TOL, and its OD — the largest full
# *external* Z cylinder — fills at least _OD_FILL_MIN of that envelope, with
# its axis within _OD_AXIS_TOL of the envelope centre. Anything else is
# prismatic — its Z cylinders are holes or local bosses, not an OD.
_SQUARENESS_TOL = 0.05
_OD_FILL_MIN = 0.8
_OD_AXIS_TOL = 0.05


def _is_rotational(x_size, y_size, od_diam, od_axis_offset) -> bool:
    """True for turned parts: an outward-facing Z cylinder, concentric with
    the bounding box, filling a square envelope.

    ``od_diam`` is the largest full *external* Z-cylinder diameter (``None``
    when there is none — bores never qualify as an OD) and
    ``od_axis_offset`` that cylinder's axis distance from the bbox centre.
    """
    if od_diam is None:
        return False
    envelope = max(x_size, y_size)
    return (
        abs(x_size - y_size) <= _SQUARENESS_TOL * envelope
        and od_diam >= _OD_FILL_MIN * envelope
        and od_axis_offset <= _OD_AXIS_TOL * envelope
    )


# A hole is "concentric" with a turned part's rotation axis when its drilling
# axis is the Z (OD) axis and its opening sits on the part centreline.  Such
# bores are already dimensioned by the ldr_z bore leaders, so they must not
# also receive a hole callout / location dim (#10).  Off-axis holes (a bolt
# circle, a cross-hole) fall through to the feature-presence path.


def lint_feature_coverage(
    part, annotations, tol: float = 0.15, cyls=None, exclude=None, assembly=None
) -> list:
    """Coarse completeness check: report part diameters with no callout (#80).

    ``exclude`` is an optional iterable of diameters already accounted for by a
    more specific build-time lint (e.g. the per-view callout cap's
    ``callout_dropped``); these are skipped here so a dropped callout is not
    double-reported as ``feature_not_dimensioned``.

    ``assembly`` controls severity for a general-arrangement drawing of a
    multi-body part. A GA deliberately omits each part's bores (they belong on
    detail sheets), so demanding a callout for every cylinder is noise. When
    ``assembly`` is ``True`` the coverage codes (``feature_not_dimensioned`` /
    ``feature_count_mismatch``) are emitted at ``info`` severity instead of
    ``warning`` — kept queryable but out of the warning count and quality score.
    ``None`` (the default) auto-detects: a multi-solid ``part`` is treated as an
    assembly. Pass ``False`` to force strict single-part severity (#69).

    Builds a feature inventory from *part*'s hole/boss diameters (cylinder
    patches spanning at least ~half a turn around their axis in total, so
    fillets are ignored) and diffs it against every ø value mentioned in the
    annotations' labels, plus the structured ``covers_diameters`` metadata on
    annotations that draw their values geometrically (e.g. ``HoleCallout``).
    Radius callouts are *not* counted — "R5 TYP" fillet notes would otherwise
    mask an undimensioned ø10 bore. Title blocks are skipped — part numbers
    like "BRACKET R8" are not callouts. Each uncovered diameter yields one
    ``feature_not_dimensioned`` warning.

    ``cyls`` accepts a precomputed ``analyse_cylinders(part)`` result so
    repeated lint runs need not re-scan the solid.

    Counts are checked too (#92): the part's holes (via ``find_holes``) give
    a required count per diameter (each bore, counterbore, and spotface
    occurrence counts one), and structured callouts declare how many holes
    they dimension (``covers_count`` — the ``n×`` prefix). A shortfall
    yields a ``feature_count_mismatch`` warning. A diameter covered by any
    free-text ø-label is exempt from the count check — text labels carry no
    count semantics. Location coverage remains out of scope (#93).
    """
    z_cyls, cross_cyls = cyls if cyls is not None else analyse_cylinders(part)
    # Coverage inventory: the *recognised* dimensionable diameters (bores,
    # cbore/spotface steps, bosses) from feature_diameters — built via
    # find_holes/find_bosses, so slot ends and interrupted recesses (partial
    # cylinders that an angle-only test mistakes for full bores) are excluded.
    # Replaces the raw full_cylinders patch list, which over-reported those as
    # undimensioned features (helpers #158/#159).
    inventory = feature_diameters(part, cyls=(z_cyls, cross_cyls))

    if assembly is None:
        assembly = len(part.solids()) > 1
    coverage_severity = "info" if assembly else "warning"

    mentioned: set[float] = set()
    text_mentioned: set[float] = set()
    provided: dict[float, int] = {}
    for ann in annotations:
        if isinstance(ann, TitleBlock):
            continue
        label = getattr(ann, "label", None) or ""
        for m in _DIAM_RE.finditer(label):
            mentioned.add(float(m.group(1)))
            text_mentioned.add(float(m.group(1)))
        count = getattr(ann, "covers_count", 1)
        for v in getattr(ann, "covers_diameters", ()):
            mentioned.add(float(v))
            provided[float(v)] = provided.get(float(v), 0) + count

    exclude = exclude or ()
    issues = [
        LintIssue(
            severity=coverage_severity,
            code="feature_not_dimensioned",
            message=f"cylindrical feature ø{_fmt(d)} has no diameter callout on the sheet",
        )
        for d in inventory
        if not any(abs(d - v) <= tol for v in mentioned)
        and not any(abs(d - e) <= tol for e in exclude)
    ]

    required: dict[float, int] = {}
    for h in find_holes(part, cyls=(z_cyls, cross_cyls)):
        for d in (h.diameter, *(s.diameter for s in (h.cbore, h.spotface) if s)):
            key = next((k for k in required if abs(k - d) <= tol), d)
            required[key] = required.get(key, 0) + 1
    for d, need in sorted(required.items(), reverse=True):
        if any(abs(d - v) <= tol for v in text_mentioned):
            continue  # free-text coverage carries no count to check against
        have = sum(c for v, c in provided.items() if abs(d - v) <= tol)
        if 0 < have < need:
            issues.append(
                LintIssue(
                    severity=coverage_severity,
                    code="feature_count_mismatch",
                    message=(
                        f"{need} ø{_fmt(d)} features on the part but callouts account for {have}"
                    ),
                )
            )
    return issues


# --- lint scoring (see Drawing.lint_summary) -------------------------------
# Codes that check standards/geometry correctness rather than pure page
# layout. Grouped so a caller (and the #30 repair loop) can tell a wrong
# drawing from a merely tight one.
_GEOMETRY_AWARE_CODES = frozenset(
    {
        "feature_not_dimensioned",
        "feature_count_mismatch",
        "missing_principal_dimension",
        "label_vs_measured",
        "dim_inside_part",
        "callout_dropped",
        "location_ref_dropped",
        "step_dim_dropped",
        "placement_unsatisfiable",
    }
)

# Coarse 0–1 quality heuristic: a clean sheet scores 1.0; each issue subtracts
# a flat per-severity penalty (clamped at 0). A convenience signal only — the
# severity/code counts in the summary are the authoritative output.
_SCORE_ERROR_PENALTY = 0.2
_SCORE_WARNING_PENALTY = 0.05

# Quote a label parsed from a lint message safely back into a snippet.
_QUOTED_RE = re.compile(r"'([^']*)'")

# Lint codes the #30 repair loop can mechanically resolve, and the side flip
# used to move a dimension that landed on the wrong side of its witness points.
_REPAIRABLE_CODES = frozenset({"annotation_overlap", "dim_inside_part"})
_OPPOSITE_SIDE = {"above": "below", "below": "above", "left": "right", "right": "left"}


# Tolerance for matching a lint message's reported diameter (dedup
# representative at tol 0.15, formatted to 1 dp) back to a raw feature
# diameter when generating a fix snippet (#29).
_DIAM_MATCH_TOL = 0.2


def _suggest_fix(issue, dwg) -> str | None:
    """Return a ready-to-paste code snippet that addresses *issue*, or None.

    The snippet is a hint, not necessarily runnable verbatim (``...`` stands in
    for args the engine cannot infer). It uses the public domain API
    (:meth:`Drawing.features`, :meth:`Drawing.at`, :meth:`Drawing.place_dim`)
    so a caller or LLM can paste and fill the gaps trivially (#29).
    """
    code = issue.code

    if code == "feature_not_dimensioned":
        # Message: "cylindrical feature ø8 has no diameter callout on the sheet".
        m = _DIAM_RE.search(issue.message)
        if m is None:
            return None
        d = float(m.group(1))
        # The reported diameter is the dedup representative (tol 0.15) formatted
        # to 1 dp, so match raw feature diameters with that combined slack — a
        # 1e-6 match would silently miss every non-integer bore.
        for view in ("plan", "front", "side"):
            if any(abs(f.diameter - d) < _DIAM_MATCH_TOL for f in dwg.features(view)):
                tag = _fmt(d).replace(".", "_")
                return (
                    f"# ø{_fmt(d)} has no callout. Locate it via features() and add a leader:\n"
                    f'for f in dwg.features("{view}"):\n'
                    f"    if abs(f.diameter - {_fmt(d)}) < {_DIAM_MATCH_TOL}:\n"
                    f"        callout = HoleCallout(f.diameter, count=f.count,\n"
                    f"                              through=f.through, depth=f.depth, draft=dwg.draft)\n"
                    f"        elbow = (f.page_pos[0] + 15, f.page_pos[1] + 10, 0)\n"
                    f'        leader = Leader((*f.page_pos, 0), elbow, "", dwg.draft, callout=callout)\n'
                    f'        dwg.add(leader, name="hole_{tag}")'
                )
        return None

    if code == "feature_count_mismatch":
        # Message: "4 ø8 features on the part but callouts account for 1".
        # `need` is the leading count; anchor it so diameter digits never
        # interfere regardless of message word order.
        m = _DIAM_RE.search(issue.message)
        need_m = re.match(r"\s*(\d+)", issue.message)
        if m is None or need_m is None:
            return None
        need = need_m.group(1)
        return (
            f"# Only some ø{m.group(1)} holes are counted. Set count={need} on the "
            f"callout so it covers them all:\n"
            f"# HoleCallout(..., count={need}, draft=dwg.draft)"
        )

    if code == "annotation_overlap":
        # Message: "labels 'A' and 'B' overlap by ...".
        labels = _QUOTED_RE.findall(issue.message)
        first = labels[0] if labels else "<dim>"
        return (
            f"# Re-add the dimension with place_dim so it auto-stacks in the "
            f"layout strip instead of overlapping:\n"
            f'dwg.remove("{first}")  # if it was named\n'
            f'dwg.place_dim(p1, p2, "below", "plan", dwg.draft, name="{first}")'
        )

    if code == "dim_inside_part":
        # Message: "Dim 'X': annotation bbox overlaps part outline by ...".
        labels = _QUOTED_RE.findall(issue.message)
        first = labels[0] if labels else "<dim>"
        return (
            f"# The dim sits inside the view — its offset is on the wrong side. "
            f"Re-place it on the opposite side via place_dim (auto-stacks clear "
            f"of the part):\n"
            f'dwg.remove("{first}")  # if it was named\n'
            f'dwg.place_dim(p1, p2, "right", "front", dwg.draft, name="{first}")'
        )

    if code == "step_dim_dropped":
        # Steps too closely spaced to dimension at sheet scale (#41/#42).
        return (
            "# Re-build with an enlarged detail view so the crowded shoulders are "
            "dimensionable:\n"
            "dwg = build_drawing(part, detail_view=True)"
        )

    return None


def analyse_face_levels(part, tol: float = 0.5, min_area_frac: float = 0.0) -> list:
    """Return sorted unique Z-coords of horizontal (normal≈±Z) planar faces.

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
        return sorted(z for key, z in buckets.items() if areas.get(key, 0.0) >= threshold)
    return sorted(buckets.values())


# Automatic scale/page preference ladder, first-fit.  The enlargement/unity
# region is page-major: every standard scale on the smallest sheet (A4) is tried
# before moving to the next sheet, so a part lands on the smallest sheet it fits
# at the largest scale that sheet allows — e.g. a 20×15×10 part gets 2:1 on A4,
# not 5:1 on A3.  Reductions (below 1:1) keep their legibility-vs-sheet balance
# (least reduction first) so a large part is not over-reduced onto a small sheet.
_LADDER = [
    # A4 — smallest sheet first, largest scale first
    (10.0, 297.0, 210.0, 120.0),  # A4 10:1
    (5.0, 297.0, 210.0, 120.0),  # A4 5:1
    (2.0, 297.0, 210.0, 120.0),  # A4 2:1
    (1.0, 297.0, 210.0, 120.0),  # A4 1:1
    # A3
    (5.0, 420.0, 297.0, 150.0),  # A3 5:1
    (2.0, 420.0, 297.0, 150.0),  # A3 2:1
    (1.0, 420.0, 297.0, 150.0),  # A3 1:1
    # A2
    (2.0, 594.0, 420.0, 150.0),  # A2 2:1
    (1.0, 594.0, 420.0, 150.0),  # A2 1:1
    # A1
    (1.0, 841.0, 594.0, 150.0),  # A1 1:1
    # Reductions — least reduction first, so a too-big part is not crammed onto a
    # small sheet at an illegible scale.
    (0.5, 594.0, 420.0, 150.0),  # A2 1:2
    (0.2, 420.0, 297.0, 150.0),  # A3 1:5
    (0.2, 594.0, 420.0, 150.0),  # A2 1:5
    (0.5, 841.0, 594.0, 150.0),  # A1 1:2
    (0.2, 841.0, 594.0, 150.0),  # A1 1:5
    (0.5, 1189.0, 841.0, 150.0),  # A0 1:2
    (0.2, 1189.0, 841.0, 150.0),  # A0 1:5
]

_SCALES = [10.0, 5.0, 2.0, 1.0, 0.5, 0.2]


# ---------------------------------------------------------------------------
# Strip / zone layout model
# ---------------------------------------------------------------------------


def _tb_width(page_w: float) -> float:
    """Title-block width for a page: 120 mm on A4, 150 mm on A3 and larger."""
    return 120.0 if page_w <= 297.0 else 150.0


def _parse_page(page) -> tuple:
    """Resolve a page spec to ``(PAGE_W, PAGE_H, TB_W)``.

    Accepts an ISO name (``"A4"``…``"A0"``, case-insensitive), a
    ``"WIDTHxHEIGHT"`` string in mm (e.g. ``"420x297"``), or a
    ``(width, height)`` tuple in mm.
    """
    if isinstance(page, str):
        name = page.strip().upper()
        if name in _PAGE_SIZES:
            pw, ph = _PAGE_SIZES[name]
        else:
            m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*[xX×]\s*(\d+(?:\.\d+)?)", page.strip())
            if not m:
                raise ValueError(
                    f"unknown page size {page!r} — expected one of "
                    f"{', '.join(_PAGE_SIZES)} or WIDTHxHEIGHT in mm (e.g. '420x297')"
                )
            pw, ph = float(m.group(1)), float(m.group(2))
    else:
        try:
            pw, ph = float(page[0]), float(page[1])
        except (TypeError, ValueError, IndexError):
            raise ValueError(
                f"invalid page size {page!r} — expected an ISO name, "
                f"'WIDTHxHEIGHT', or a (width, height) tuple in mm"
            ) from None
    if pw <= 0 or ph <= 0:
        raise ValueError(f"page dimensions must be positive, got {page!r}")
    return pw, ph, _tb_width(pw)


_STRIP_GAP = 8.0
_STRIP_SPACING = 4.0

# Horizontal page budget to reserve for the isometric view during scale
# selection and view placement, as a fraction of bbox_max * scale.  This is a
# deliberate *under-estimate*, not the true projected size (a cube's iso
# projection is ~1.63*bbox_max wide): the iso is the last column and is fitted
# to the actual largest-empty-rect afterwards by _fit_iso_view(), which shrinks
# it to whatever space is genuinely left.  A true fit test here is circular —
# the empty rect depends on the very view positions this estimate feeds — so
# the budget stays a single, named factor rather than a recomputed fit (#31).
_ISO_WIDTH_BUDGET = 0.7

# Scale selection accepts a layout when the largest empty rectangle left for the
# iso view can hold a square of at least this fraction of the iso's natural size
# (bbox_max * scale * _ISO_WIDTH_BUDGET).  Below 1.0 because _fit_iso_view scales
# the iso down to whatever space remains, so a modestly smaller rectangle still
# renders a legible iso — letting a long/short part enlarge onto a sheet (e.g.
# 2:1 on A3) where the strict row model would have under-scaled it.
_ISO_MIN_FIT_FRAC = 0.6

# Upper bound on how far the iso view may grow beyond sheet scale when fitted to
# its zone.  The iso is an orientation aid, not a measured view: left uncapped it
# fills the (now often large) empty rectangle and can dwarf the dimensioned
# orthographic views (up to ~8× on an oversized sheet).  Capped just above sheet
# scale so it still fills modest zones without dominating.  Shrinking to fit a
# small zone is never capped.
_ISO_MAX_GROW = 1.3

# Slot sizes for the annotations that allocate from fv/pv/sv strips.
# Shared between the depth estimators below and the allocate() call-sites in
# _auto_annotate() so that a slot-size change is automatically reflected in
# the estimator-driven corridor widths.
#
# A slot is the perpendicular depth (page-mm) reserved for one Dimension: its
# dim-line offset from the view edge plus the label, which sits exactly
# pad_around_text beyond the line (measured: a "right"/"below" Dimension's
# perpendicular span equals offset + pad_around_text - extension_gap, and
# pad == extension_gap in the draft preset).  Each slot is therefore derived
# from text metrics (font_size + pad_around_text), like _MIN_STEP_DIM_MM, so it
# rescales with _FONT_SIZE instead of being a bare mm guess (#31).
# Single overall dim: two glyph-heights of line offset + the outboard label pad.
# The overall height dim leads the right ladder, so it carries an extra pad of
# clearance from the view above the first step dim's witness.
# Stacked step dims sit deeper so each ladder rung's label clears the rung below.

# A plan view with at least this many holes escalates to a hole chart when it is
# too dense to dimension every hole individually (#93). Below it, a dropped ref
# stays a legibility drop rather than tabulating a handful of holes.

# Smallest projected step height (page-mm) that can still carry a *legible*
# stacked dimension between its two extension lines.  Derived from what has to
# fit vertically: the label (font height) plus an arrowhead at each end plus
# the text clearance above and below — not an arbitrary page-mm cutoff (#13).
# Used as the single gate in BOTH _analyse (n_steps) and _auto_annotate
# (dim_step placement) so the two can never diverge.

# Minimum page-mm separation between two *consecutive* dimensioned step heights.
# Shoulders closer than this on the page read as one, so only the first of such
# a cluster is dimensioned and the rest surface via lint (#41). Sized to the
# value-label footprint (one glyph height + clearance) — enough to tell two
# stacked step dims apart, without dropping genuinely-distinct shoulders.

# A horizontal face counts as a real step only if its area is at least this
# fraction of the part's plan footprint (x_size × y_size). Filters out tiny
# faces from engraved text/numbers that are not steps (staircase.step review).
_STEP_MIN_AREA_FRAC = 0.01

# Minimum page-mm separation between two *consecutive* hole-location dimensions
# along one axis. Stacked location dims sit on separate tiers, so their value
# labels never collide (the tier pitch handles that); the legibility limit is the
# extension lines / arrowheads merging when two holes share almost the same
# position on that axis. Sized to one arrowhead plus clearance — smaller than the
# step-spacing gate, which also stacks labels in one column. Holes closer than
# this read as one, so only the first of such a run is dimensioned and the rest
# surface via lint (#43): "fits" is not the same as "legible".


# ---------------------------------------------------------------------------
# Annotation depth estimators (Phase 2 of #118)
#
# These pure functions estimate the strip depth (mm) required for each
# inter-view boundary BEFORE view positions are fixed.  They are intentionally
# conservative (may over-estimate slightly).  Used by _analyse() (Phase 3) to
# set minimum corridor widths, and by _fits() (Phase 3) for consistent sheet
# selection.
# ---------------------------------------------------------------------------


def _est_right_strip_depth(n_steps: int) -> float:
    """Depth needed to the right of the front view.

    Always includes dim_height (1 slot).  *n_steps* dim_step slots follow if
    any step levels are present.  Returns the minimum corridor width (from view
    edge to outer_limit) that makes all those allocations succeed.
    """
    n = 1 + max(n_steps, 0)  # dim_height + one slot per step dim
    # gap + dim_height + (n-1) step slots each preceded by one spacing
    return _STRIP_GAP + _SLOT_DIM_HEIGHT + (n - 1) * (_STRIP_SPACING + _SLOT_DIM_STEP)


def _est_pv_below_depth() -> float:
    """Depth needed below the plan view: dim_width (always one slot)."""
    return _STRIP_GAP + _SLOT_DIM_WIDTH


def _est_pv_above_depth(
    holes, patterns, font_size: float = _FONT_SIZE, pad_around_text: float = 2.0
) -> float:
    """Estimate the depth above the plan view consumed by X-location dims, which
    tier one per distinct datum-X reference (#36) — so the layout can reserve it
    (and a balloon row beyond) *before* placing views, instead of letting the
    tiers spill into headroom (#121).

    WIP estimate standing in for ADR 0004's "lay out, don't predict": a
    conservative upper bound (one spare tier for the pitch dim / rounding), which
    the packer absorbs by scale rather than under-reserving and overlapping.
    Scale-independent (tier height is fixed page-mm).
    """
    z_refs_x: list[float] = []
    patterned = {h for p in patterns for h in p.holes}
    for p in patterns:
        if _axis_letter(p.holes[0]) != "z":
            continue
        z_refs_x.append(p.center[0] if isinstance(p, BoltCircle) else p.holes[0].location[0])
    z_refs_x += [h.location[0] for h in holes if _axis_letter(h) == "z" and h not in patterned]
    distinct: list[float] = []
    for x in sorted(z_refs_x):
        if not distinct or abs(x - distinct[-1]) > 0.5:
            distinct.append(x)
    if not distinct:
        return 0.0
    tier = font_size + 2 * pad_around_text
    return (len(distinct) + 1) * tier  # +1 tier: pitch dim / rounding headroom


def _est_plan_halo(font_size: float = _FONT_SIZE) -> float:
    """Per-side standoff band (page-mm) reserved around the plan view when its
    holes will be ballooned, so the leadered balloon ring sits in clear space
    off the part instead of jamming the views together (#111).

    Scale-independent (font_size is fixed page-mm), like the strip depths: a
    leader standoff + one balloon diameter (``2·r = 3·font_size``) + clearance.
    """
    return _STRIP_GAP + 3 * font_size + _STRIP_SPACING


def _will_balloon(holes, patterns) -> bool:
    """A-priori (pre-layout) prediction that the plan view will escalate to a
    leadered hole-chart, so its balloon halo can be reserved before the views
    are placed (#111, approach A).

    Conservative and scale-independent: fires when there are at least
    ``_TABULATE_MIN_HOLES`` plan-view holes that are *not* mostly covered by a
    detected pattern (a patterned set is grouped into one ``n× ⌀`` callout +
    pattern dim, so it does not balloon).  May occasionally over-reserve (a
    little wasted corridor) or, if the runtime trigger fires anyway, fall back
    to placing balloons in the unreserved margin — both are graceful.
    """
    z = [h for h in holes if _axis_letter(h) == "z"]
    if len(z) < _TABULATE_MIN_HOLES:
        return False
    covered = sum(len(p.holes) for p in patterns if p.holes and _axis_letter(p.holes[0]) == "z")
    return covered < 0.8 * len(z)


# Inter-constant invariant: the gap between the front view top edge and the
# plan view bottom edge equals _DIM_PAD.  The pv_zones.below strip occupies
# that gap, so _DIM_PAD must be at least as wide as the depth pv_below needs.
# If _DIM_PAD is shrunk below _est_pv_below_depth(), dim_width would silently
# overlap the front view rather than failing an allocate().
assert _DIM_PAD >= _est_pv_below_depth(), (
    f"_DIM_PAD ({_DIM_PAD}) is smaller than pv_below slot depth "
    f"({_est_pv_below_depth()}); bump _DIM_PAD or shrink the slot constants."
)


# ---------------------------------------------------------------------------
# Two-pass layout — Pass 1: annotation strip depth measurement (#131)
#
# font_size = 3.0 mm is a fixed page-mm constant, so all annotation depths
# are scale-independent and can be computed before choose_scale() is called.
# ---------------------------------------------------------------------------


def _est_bore_callout_width(
    holes, font_size: float = _FONT_SIZE, patterns=None, pad_around_text: float = 2.0
) -> float:
    """Estimate the maximum bore callout label width (page-mm) across all holes.

    Groups holes by machining spec (same as _annotate_holes), then estimates
    the HoleCallout token sequence width using a character-based formula.
    Includes the BoltCircle suffix ("EQ SP ON ø… BC") when patterns are supplied.
    Returns the label width only — elbow_dx and gap clearance are NOT included;
    callers that need the full strip depth should add those overheads separately.
    Returns 0.0 when the hole list is empty.
    *pad_around_text* should come from ``draft_preset(...).pad_around_text``.
    """
    if not holes:
        return 0.0
    groups: dict = {}
    for h in holes:
        groups.setdefault(HoleSpec.from_hole(h), []).append(h)

    # Map spec_key → the widest pattern-callout suffix, so a spec's grouped
    # callout reserves room for it. A spec can sub-cluster into several patterns
    # (#92); the BoltCircle "EQ SP ON ø… BC" is wider than a RectGrid "(r×c)",
    # so the widest wins (the corridor must hold the longest callout).
    suffix_by_spec: dict = {}
    if patterns:
        for p in patterns:
            if isinstance(p, BoltCircle):
                s = f"EQ SP ON ø{_fmt(p.diameter)} BC"
            elif isinstance(p, RectGrid):
                s = f"({p.rows}×{p.cols})"
            else:
                continue
            key = HoleSpec.from_hole(p.holes[0])
            if len(s) > len(suffix_by_spec.get(key, "")):
                suffix_by_spec[key] = s

    h_fs = font_size
    # gap (inter-token spacing) and sym_w (geometry-symbol cell width) mirror
    # HoleCallout's own internal layout constants so the estimate matches what
    # the primitive actually strokes.  Variable text tokens are measured with
    # real glyph metrics (_text_width) rather than a character-count fudge (#31).
    gap = 0.45 * h_fs
    sym_w = h_fs
    pad = pad_around_text

    max_w = 0.0
    for spec_key, group in groups.items():
        rep = group[0]
        count = len(group) if len(group) > 1 else None
        through = rep.bottom == "through"
        step = rep.cbore or rep.spotface

        token_w: list[float] = []
        if count:
            token_w.append(_text_width(f"{count}×", h_fs))
        token_w.append(sym_w)  # ⌀ symbol
        token_w.append(_text_width(_fmt(rep.diameter), h_fs))
        if through:
            token_w.append(_text_width("THRU", h_fs))
        elif rep.depth:
            token_w.append(sym_w)  # depth symbol
            token_w.append(_text_width(_fmt(rep.depth), h_fs))
        if step:
            token_w.append(sym_w)  # counterbore/spotface symbol
            token_w.append(sym_w)  # ⌀
            token_w.append(_text_width(_fmt(step.diameter), h_fs))
            if step.depth:
                token_w.append(sym_w)  # depth symbol
                token_w.append(_text_width(_fmt(step.depth), h_fs))

        # Pattern callout suffix ("EQ SP ON ø… BC" / "(r×c)"), when this spec
        # group is recognised as a pattern.
        suffix = suffix_by_spec.get(spec_key)
        if suffix is not None:
            token_w.append(_text_width(suffix, h_fs))

        n = len(token_w)
        w = sum(token_w) + max(n - 1, 0) * gap + pad
        max_w = max(max_w, w)

    return max_w


@dataclass
class StripDepths:
    """Annotation strip depths (page-mm) computed before view positions are fixed.

    Drives the inter-view corridor widths in the two-pass layout (#131).
    """

    right: float  # horizontal corridor right of FV/PV → gap_fv_sv
    left: float  # horizontal corridor left of FV/PV
    top: float = 0.0  # band above PV for tiered X-location dims (#121)
    pv_halo: float = 0.0  # balloon standoff band reserved around the plan view (#111)


def _measure_strips(
    holes,
    patterns,
    n_steps: int,
    bb,
    font_size: float = _FONT_SIZE,
    arrow_length: float = 2.7,
    pad_around_text: float = 2.0,
) -> StripDepths:
    """Compute annotation strip depths from hole geometry (Pass 1 of #131).

    All annotation sizes are scale-independent because font_size is a fixed
    page-mm constant, so there is no circularity with choose_scale().
    *arrow_length* and *pad_around_text* should come from ``draft_preset(...)``.
    """
    bore_depth = _est_bore_callout_width(
        holes, font_size, patterns=patterns, pad_around_text=pad_around_text
    )
    # Add elbow clearance and leader-to-label gap so gap_fv_sv fully contains
    # the composed leader: elbow_dx (= draft.arrow_length) + gap
    # (= draft.pad_around_text), always present.
    if bore_depth > 0:
        bore_depth += pad_around_text + arrow_length
    right = max(_est_right_strip_depth(n_steps), bore_depth)
    left = max(_DIM_PAD, bore_depth)
    top = _est_pv_above_depth(holes, patterns, font_size, pad_around_text)
    pv_halo = _est_plan_halo(font_size) if _will_balloon(holes, patterns) else 0.0
    return StripDepths(right=right, left=left, top=top, pv_halo=pv_halo)


@dataclass(frozen=True)
class AnnoBox:
    """A composed annotation band as a page-mm box (#112, ADR 0004 Step 4).

    ``side`` is the view side the band sits on (``"right"``/``"left"`` of the
    front/plan views, or ``"plan_halo"`` for the balloon standoff ring);
    ``depth`` is the band's perpendicular extent from the view edge.  A view's
    footprint is the deepest band per side — see ``_footprint_from_boxes``.

    This is the box-model expression of the scalar corridor reservation that
    ``_measure_strips`` computes (Step 4a): every band that can drive a
    ``StripDepths`` field is emitted as an ``AnnoBox``, and the deepest band per
    side wins (see ``_footprint_from_boxes``).  Today the depths are the same
    estimates ``_measure_strips`` uses, so the two are interchangeable
    (byte-identical); later steps replace the estimates with depths measured
    from the real placement.
    """

    side: str
    depth: float


def _compose_anno_boxes(
    holes,
    patterns,
    n_steps: int,
    font_size: float = _FONT_SIZE,
    arrow_length: float = 2.7,
    pad_around_text: float = 2.0,
) -> list[AnnoBox]:
    """Compose a drawing's annotation bands as ``AnnoBox`` boxes (#112, Step 4a).

    Mirrors ``_measure_strips`` exactly, but emits each contributing band as a
    box rather than folding them into three scalars up front.
    ``_footprint_from_boxes`` reduces these back to the identical
    ``StripDepths``.
    """
    boxes = [AnnoBox("right", _est_right_strip_depth(n_steps))]  # FV right dim ladder
    bore_depth = _est_bore_callout_width(
        holes, font_size, patterns=patterns, pad_around_text=pad_around_text
    )
    if bore_depth > 0:
        # elbow clearance + leader-to-label gap, as in _measure_strips
        bore_depth += pad_around_text + arrow_length
        boxes.append(AnnoBox("right", bore_depth))  # FV/PV right bore callouts
        boxes.append(AnnoBox("left", bore_depth))  # FV/PV left bore callouts
    above = _est_pv_above_depth(holes, patterns, font_size, pad_around_text)
    if above > 0:
        boxes.append(AnnoBox("above", above))  # tiered X-location dims above PV (#121)
    if _will_balloon(holes, patterns):
        boxes.append(AnnoBox("plan_halo", _est_plan_halo(font_size)))
    return boxes


def _footprint_from_boxes(boxes: list[AnnoBox]) -> StripDepths:
    """Reduce composed ``AnnoBox`` bands to per-side corridor depths (Step 4a).

    Each ``StripDepths`` field is the deepest band on its side; ``left`` keeps
    the ``_DIM_PAD`` floor it has in ``_measure_strips``.
    """

    def deepest(side: str) -> float:
        return max((b.depth for b in boxes if b.side == side), default=0.0)

    return StripDepths(
        right=deepest("right"),
        left=max(_DIM_PAD, deepest("left")),
        top=deepest("above"),
        pv_halo=deepest("plan_halo"),
    )


def _fits(
    x_size,
    y_size,
    z_size,
    scale,
    page_w,
    page_h,
    tb_w,
    n_steps: int = 0,
    strips: StripDepths | None = None,
    pack_iso_2d: bool = False,
) -> bool:
    """True if the 4-view layout fits the page at this scale.

    Default (``pack_iso_2d=False``) is the conservative row model used by
    automatic scale selection: the iso view is charged a column in the view row
    alongside the title block.  This deliberately over-reserves horizontal space,
    which keeps annotation-heavy parts on a sheet large enough to place all their
    dimensions rather than dropping some onto a tighter sheet.

    When ``pack_iso_2d=True`` — used when the caller fixes the page or scale —
    the iso is instead fitted into the largest empty rectangle the placement
    engine actually uses (:func:`_layout_geometry`), so it may occupy vertical
    headroom above the views rather than a row column.  A long, short part can
    then be enlarged onto the requested sheet (e.g. 2:1 on A3), where the row
    model would have under-scaled it.

    The title block occupies only the bottom ``_TB_H`` mm of the sheet, so the
    views may extend over it horizontally as long as they clear it vertically.
    """
    bbox_max = max(x_size, y_size, z_size)
    halo = strips.pv_halo if strips else 0.0
    gap_fv_sv = max(_DIM_PAD, strips.right if strips else _est_right_strip_depth(n_steps), halo)
    gap_left = max(_DIM_PAD, strips.left if strips else _DIM_PAD, halo)
    # PV top band: when ballooned, hold the tiered X-location dims + a balloon row
    # beyond them (#121); otherwise the historic DIM_PAD (tiers spill into
    # headroom). Mirror _layout_geometry's pv.top so scale/page is sized for it.
    strip_top = strips.top if strips else 0.0
    pv_top = (max(_DIM_PAD, strip_top) + halo) if halo > 0 else _DIM_PAD
    h = _MARGIN + pv_top + y_size * scale + _DIM_PAD + z_size * scale + _DIM_PAD + _MARGIN
    if h > page_h:
        return False

    if not pack_iso_2d:
        # Conservative row model: iso + title block both charged to the row.
        w = (
            _MARGIN
            + gap_left
            + x_size * scale
            + gap_fv_sv
            + y_size * scale
            + _DIM_PAD
            + bbox_max * scale * _ISO_WIDTH_BUDGET
            + _DIM_PAD
            + tb_w
            + _MARGIN
        )
        if w <= page_w:
            return True
        views_bottom = max(0.0, (page_h - h) / 2) + _MARGIN + _DIM_PAD
        # When views clear the title block row, the iso sits above it and the
        # title block no longer constrains horizontal space — drop tb_w from w.
        return w - tb_w <= page_w and views_bottom >= _MARGIN + _TB_H

    # 2D packing: views + title block fit the row; the iso fits leftover space.
    w_views_tb = (
        _MARGIN
        + gap_left
        + x_size * scale
        + gap_fv_sv
        + y_size * scale
        + _DIM_PAD
        + tb_w
        + _MARGIN
    )
    if w_views_tb > page_w:
        views_bottom = max(0.0, (page_h - h) / 2) + _MARGIN + _DIM_PAD
        if not (w_views_tb - tb_w <= page_w and views_bottom >= _MARGIN + _TB_H):
            return False
    g = _layout_geometry(x_size, y_size, z_size, scale, page_w, page_h, tb_w, strips, n_steps)
    if not g.iso_valid:
        return False
    iso_fit = min(g.iso_right - g.iso_left, g.iso_top - g.iso_bottom)
    return iso_fit >= _ISO_MIN_FIT_FRAC * g.iso_natural


def choose_scale(
    x_size: float,
    y_size: float,
    z_size: float,
    n_steps: int = 0,
    scale=None,
    page=None,
    strips: StripDepths | None = None,
) -> tuple:
    """Return (SCALE, PAGE_W, PAGE_H, TB_W) for a 4-view layout.

    Layout columns: [front(x×z)] [side(y×z)] [iso(~0.7*max)] [title block].
    Rows: [plan(x×y)] above [front/side].
    Tries ISO A-series pages (A4→A3→A2→A1→A0) at preferred scales, including
    ISO 5455 enlargement scales (10:1, 5:1) so small parts get legible views.
    A4 uses a 120 mm title block; A3+ use 150 mm. The title block only
    constrains row width when the view rows would overlap it vertically.

    Args:
        scale: optional fixed scale factor (e.g. ``5`` for 5:1, ``0.5`` for
            1:2); the page is then chosen as the smallest A-series sheet that
            fits.
        page: optional fixed page — an ISO name (``"A3"``), ``"WIDTHxHEIGHT"``
            in mm, or a ``(width, height)`` tuple; the scale is then chosen as
            the largest standard scale that fits. When both ``scale`` and
            ``page`` are given they are used as-is (a warning is logged if the
            layout does not fit).
    """
    if scale is not None and float(scale) <= 0:
        raise ValueError(f"scale must be positive, got {scale!r}")
    if scale is not None and page is not None:
        pw, ph, tb = _parse_page(page)
        if not _fits(
            x_size,
            y_size,
            z_size,
            float(scale),
            pw,
            ph,
            tb,
            n_steps=n_steps,
            strips=strips,
            pack_iso_2d=True,
        ):
            _log.warning(
                "Requested scale %s on %s page may not fit the 4-view layout", scale, page
            )
        return float(scale), pw, ph, tb
    # When the caller fixes the page or scale, pack the iso into 2D space so the
    # largest scale that genuinely fits the requested sheet is chosen (the iso
    # may sit in vertical headroom).  Automatic selection stays on the
    # conservative row model, which reserves enough space for all annotations.
    if page is not None:
        pw, ph, tb = _parse_page(page)
        candidates = [(s, pw, ph, tb) for s in _SCALES]
        pack_iso_2d = True
    elif scale is not None:
        candidates = [(float(scale), pw, ph, _tb_width(pw)) for pw, ph in _PAGE_SIZES.values()]
        pack_iso_2d = True
    else:
        candidates = _LADDER
        pack_iso_2d = False
    for cand in candidates:
        if _fits(
            x_size, y_size, z_size, *cand, n_steps=n_steps, strips=strips, pack_iso_2d=pack_iso_2d
        ):
            return cand
    _log.warning(
        "No layout fits %.0f × %.0f × %.0f mm; falling back to %s",
        x_size,
        y_size,
        z_size,
        candidates[-1],
    )
    return candidates[-1]


# ---------------------------------------------------------------------------
# Shared analysis step
# ---------------------------------------------------------------------------


def _view_geom(a) -> dict:
    """The three orthographic geometry boxes as ``{view: (cx, cy, hw, hh)}``."""
    return {
        "front": (a.FV_X, a.FV_Y, a.fv_hw, a.fv_hh),
        "plan": (a.PV_X, a.PV_Y, a.fv_hw, a.pv_hh),
        "side": (a.SV_X, a.SV_Y, a.sv_hw, a.fv_hh),
    }


def _anno_bbox(o):
    """Page-space bbox of an annotation: its text ``label_bbox`` if it has one,
    else its geometric bounding box; ``None`` if neither resolves."""
    lb = getattr(o, "label_bbox", None)
    if lb is not None:
        return lb
    try:
        b = o.bounding_box()
        return (b.min.X, b.min.Y, b.max.X, b.max.Y)
    except Exception as exc:  # noqa: BLE001 — not every annotation bbox-es cleanly
        # Fails open: an un-bbox-able annotation drops out of the overlap count and
        # the measured footprint. Surface it so a silently-missed repack trigger is
        # debuggable rather than invisible (#121).
        _log.debug("annotation %r has no resolvable bbox: %s", type(o).__name__, exc)
        return None


def _attribute_annotations(dwg, a):
    """Yield ``(name, view, bbox, is_label)`` for every annotation OWNED by an
    orthographic view, per the view recorded at creation (``dwg._anno_view``).

    Ownership is authoritative — the annotation pass that drew it knew which view
    it belonged to and tagged it (#121) — so a front-view step dimension sitting
    in the front↔plan gap is the *front* view's, never recovered (and mis-bucketed)
    from page coordinates.  Annotations with no recorded ortho view (title block,
    iso/section/detail furniture) belong to no block and are skipped.  ``is_label``
    is true when the annotation carries a text ``label_bbox`` (a dimension value
    or balloon tag) rather than bare geometry (a centreline/leader line).
    """
    for name, o in dwg._named.items():
        view = dwg._anno_view.get(name)
        if view not in ("front", "plan", "side"):
            continue
        label = getattr(o, "label_bbox", None)
        bb = label if label is not None else _anno_bbox(o)
        if bb is None:
            continue
        yield name, view, bb, label is not None


def _cross_view_overlaps(dwg, a) -> int:
    """Count pairs of annotations attributed to *different* views whose boxes
    overlap — the #121 failure (a plan-view balloon over a front-view dimension).

    This is the repack trigger: a clean sheet (no cross-view overlap) is left
    exactly as pass 1 placed it, so well-estimated parts stay byte-identical;
    only a sheet with a real collision is re-packed (ADR 0004).
    """
    items = list(_attribute_annotations(dwg, a))
    n = 0
    for i in range(len(items)):
        _, vi, bi, li = items[i]
        for j in range(i + 1, len(items)):
            _, vj, bj, lj = items[j]
            # Only a collision involving a text label matters — two bare lines
            # (extension/leader) crossing between views is normal drafting.
            if vi == vj or not (li or lj):
                continue
            if min(bi[2], bj[2]) > max(bi[0], bj[0]) and min(bi[3], bj[3]) > max(bi[1], bj[1]):
                n += 1
    return n


def _annotations_out_of_bounds(dwg, a, tol: float = 1.0) -> bool:
    """True when any view-owned annotation's footprint extends past the drawable
    area — the second repack trigger besides cross-view overlap.  A ballooned
    plan view can overflow the page top (the balloon ring) without crossing
    another view, so the page must still escalate; the measure-and-repack pass
    re-sizes it because the overflowing balloons are part of the plan footprint
    (#92).  Only view-owned annotations count — those are what a repack can move
    by escalating the sheet."""
    lo, hi_x, hi_y = a.margin, a.PAGE_W - a.margin, a.PAGE_H - a.margin
    for name, o in dwg._named.items():
        if dwg._anno_view.get(name) not in ("front", "plan", "side"):
            continue
        # Match the lint, which tests each item's FULL bounding_box (extension
        # lines, arrowheads, leader + balloon ring) — not just the label rect —
        # so a dimension whose extension lines overrun the page is caught too.
        try:
            b = o.bounding_box()
            bb = (b.min.X, b.min.Y, b.max.X, b.max.Y)
        except Exception:  # noqa: BLE001 — fall back to the label rect, else skip
            lb = getattr(o, "label_bbox", None)
            if lb is None:
                continue
            bb = lb
        if bb[0] < lo - tol or bb[1] < lo - tol or bb[2] > hi_x + tol or bb[3] > hi_y + tol:
            return True
    return False


def _measure_blocks(dwg, a) -> dict:
    """Measure each orthographic view's *actual* annotation footprint from the
    laid-out drawing (#121, ADR 0004 — "lay out, don't predict").

    Each view's four band depths are how far its annotations extend beyond its
    geometry box, **measured** from what the annotation passes produced — not
    estimated. Every annotation is attributed to the nearest view (by its
    label/box centre), and the band depth on a side is the furthest that view's
    annotations reach past the geometry edge there. Returns ``{view_name:
    ViewBlock}`` whose bands the packer can place disjoint, no ``_est_*`` needed.
    """
    geom = _view_geom(a)
    ext: dict = {v: None for v in geom}
    for _name, v, bb, _label in _attribute_annotations(dwg, a):
        e = ext[v]
        ext[v] = (
            bb
            if e is None
            else (min(e[0], bb[0]), min(e[1], bb[1]), max(e[2], bb[2]), max(e[3], bb[3]))
        )

    blocks: dict = {}
    for v, (cx, cy, hw, hh) in geom.items():
        e = ext[v]
        if e is None:
            blocks[v] = ViewBlock(hw, hh)
            continue
        blocks[v] = ViewBlock(
            hw,
            hh,
            top=max(0.0, e[3] - (cy + hh)),
            right=max(0.0, e[2] - (cx + hw)),
            bottom=max(0.0, (cy - hh) - e[1]),
            left=max(0.0, (cx - hw) - e[0]),
        )
    return blocks


@dataclass(frozen=True)
class ViewBlock:
    """A view's composite footprint (#112): its geometry half-extents plus the
    reserved annotation-band depth on each side (page-mm).

    The block's outer box is the geometry box inflated by its bands; the layout
    packs these blocks rather than padding bare views with scalar corridors.
    Two blocks that *abut* are separated by ``bandA + bandB``; two that *share*
    a corridor (a band against a common wall or neighbour) by ``max(bandA,
    bandB)`` — see the gap→band map in #112.
    """

    hw: float  # geometry half-width
    hh: float  # geometry half-height
    top: float = 0.0
    right: float = 0.0
    bottom: float = 0.0
    left: float = 0.0

    def footprint(self, cx, cy):
        """Outer box of this block placed at centre (cx, cy): the geometry box
        inflated by the per-side bands.  This is what the layout packs and what
        other blocks are placed around — the padding lives on the block, not on
        the caller building the obstacle."""
        return (
            cx - self.hw - self.left,
            cy - self.hh - self.bottom,
            cx + self.hw + self.right,
            cy + self.hh + self.top,
        )


def _padded_box(cx, cy, hw, hh, pad=_DIM_PAD):
    """Footprint of a fixed block at (cx, cy) with a uniform `pad` clearance band.

    The clearance is expressed as the block's own bands (see
    ``ViewBlock.footprint``) — the obstacle the iso is placed around is the
    block's footprint, not an ad-hoc inflation done by the caller.
    """
    return ViewBlock(hw, hh, pad, pad, pad, pad).footprint(cx, cy)


def _layout_geometry(
    x_size, y_size, z_size, scale, page_w, page_h, tb_w, strips, n_steps=0, blocks=None
):
    """Compute the 4-view layout geometry for a part at a given scale/page.

    Single source of truth shared by scale selection (:func:`_fits`) and view
    placement (:func:`_analyse`): the orthographic FV/PV/SV view centres and
    half-sizes, the annotation-strip gaps, and the largest empty rectangle the
    isometric view is fitted into.  Returns a :class:`SimpleNamespace`.

    When *strips* is ``None`` the annotation-corridor gaps fall back to the
    step-count estimate (used during scale selection before strips are
    measured); otherwise the measured strip depths are used.
    """
    margin = _MARGIN
    DIM_PAD = _DIM_PAD
    bbox_max = max(x_size, y_size, z_size)
    fv_hw = x_size * scale / 2
    fv_hh = z_size * scale / 2
    pv_hh = y_size * scale / 2
    sv_hw = y_size * scale / 2

    # Compose each view as a block: geometry half-extents + reserved annotation
    # bands per side (#112).  The front and plan views form a vertical column
    # sharing the left/right corridors (max of the two); the side view shares
    # the FV↔SV corridor; the front↔plan gap is the abutting pair
    # (fv.top + pv.bottom).  When the plan view is ballooned (halo > 0) its halo
    # becomes explicit per-side bands so the ballooned plan view is placed as a
    # unit — including a BOTTOM band that pushes the front view down so balloons
    # ring the part below it, not just left/right/top (#111/#112 Phase 2).  All
    # bands reduce to today's arithmetic when halo = 0 (byte-identical).
    halo = strips.pv_halo if strips else 0.0
    strip_top = strips.top if strips else 0.0
    gap_fv_sv = max(DIM_PAD, strips.right if strips else _est_right_strip_depth(n_steps), halo)
    gap_left = max(DIM_PAD, strips.left if strips else DIM_PAD, halo)
    pv_below = _est_pv_below_depth()
    # Top band above PV. When the plan view is ballooned, the ring sits beyond the
    # tiered X-location dims, so reserve their real depth (strip_top) PLUS a
    # balloon row — otherwise the ring overruns the page (#121). When NOT
    # ballooned, keep the historic DIM_PAD: the dim tiers spill harmlessly into
    # the headroom above PV, and reserving more would needlessly grow the layout
    # (and can starve the section view of its leftover space).
    pv_top = (max(DIM_PAD, strip_top) + halo) if halo > 0 else DIM_PAD
    # Estimated blocks (always built): the scale-derived geometry half-extents
    # plus the heuristic per-side corridor depths.
    est_fv = ViewBlock(
        fv_hw, fv_hh, top=DIM_PAD - pv_below, right=gap_fv_sv, bottom=DIM_PAD, left=gap_left
    )
    est_pv = ViewBlock(
        fv_hw,
        pv_hh,
        top=pv_top,
        right=gap_fv_sv,
        bottom=max(pv_below, halo),  # band below PV holds the width dim + a balloon row
        left=gap_left,
    )
    est_sv = ViewBlock(sv_hw, fv_hh, right=DIM_PAD)
    if blocks is not None:
        # Measure-and-repack pass (#121, ADR 0004): pack the *measured* per-view
        # footprints disjoint.  Floor each measured band at the estimate — the
        # repack may only GROW a corridor to fit annotations the estimate
        # under-sized (the documented FV-top vs PV-balloon overlap), never shrink
        # below the clearance the estimate guarantees.  The geometry half-extents
        # stay scale-derived (the estimate), not the measured block.
        def _merge(est, meas):
            return ViewBlock(
                est.hw,
                est.hh,
                top=max(est.top, meas.top),
                right=max(est.right, meas.right),
                bottom=max(est.bottom, meas.bottom),
                left=max(est.left, meas.left),
            )

        fv = _merge(est_fv, blocks["front"])
        pv = _merge(est_pv, blocks["plan"])
        sv = _merge(est_sv, blocks["side"])
    else:
        fv, pv, sv = est_fv, est_pv, est_sv
    # Per-side corridor depths from the (possibly measured) blocks. The front and
    # plan views stack vertically (same X, different Y) so they SHARE the left and
    # right corridors — the deeper of the two facing bands. The side view ABUTS
    # the column, so its gap is that column band PLUS its own facing band (sum) —
    # disjoint by construction (#121). Byte-identical for the estimator path,
    # where fv/pv bands are equal and sv.left == 0.
    col_left = max(fv.left, pv.left)
    col_right = max(fv.right, pv.right)

    # Bottom balloon band: rather than pushing the front view down (which would
    # cascade into the iso/table and the scale choice), LIFT the plan view up
    # into the empty top headroom above it — the front/side views, iso and title
    # block stay anchored, so the table is undisturbed.  The lift is implicit:
    # the vertical stack is centred with the BASE front↔plan gap (so FV/SV centre
    # exactly as when halo = 0), while PV is positioned with the full ballooned
    # gap, leaving it max(0, halo - pv_below) higher.  Byte-identical when
    # halo = 0.  (#112, ADR 0004.)
    # FV↔PV vertical gap = fv.top + pv.bottom (abutting → sum). The estimator path
    # keeps its lift trick (centre on the base gap, place PV on the full gap);
    # the measured path uses the real gap directly.
    base_gap = (fv.top + pv.bottom) if blocks is not None else (fv.top + pv_below)
    total_h = 2 * margin + fv.bottom + 2 * fv.hh + base_gap + 2 * pv.hh + pv.top
    y_offset = max(0.0, (page_h - total_h) / 2)

    total_content_w = (
        col_left
        + col_right
        + x_size * scale
        + y_size * scale
        + max(2 * DIM_PAD, sv.right + DIM_PAD)
        + bbox_max * scale * _ISO_WIDTH_BUDGET
    )
    x_offset = max(0.0, (page_w - 2 * margin - tb_w - total_content_w) / 2)

    # Anchor the FV/PV column on the SHARED left corridor (col_left), not fv.left
    # alone: when the measured plan-view left band is the deeper of the two, the
    # column must clear it or PV slides left of the centred region — and off the
    # margin (#121). Byte-identical on the estimator path (col_left == fv.left),
    # and symmetric with SV_X's use of col_right below.
    FV_X = margin + x_offset + col_left + fv.hw
    FV_Y = y_offset + margin + fv.bottom + fv.hh
    PV_X = FV_X
    # PV uses the full (ballooned) front↔plan gap while FV/SV were centred with
    # the base gap — so the plan view sits pv_lift higher: lifted into the
    # headroom, front view anchored.
    PV_Y = FV_Y + fv.hh + (fv.top + pv.bottom) + pv.hh
    # SV abuts the FV/PV column: gap = column right band + SV's own left band
    # (disjoint sum). Byte-identical to the old max(fv.right, sv.left) on the
    # estimator path (fv.right == pv.right == col_right, sv.left == 0).
    SV_X = FV_X + fv.hw + col_right + sv.left + sv.hw
    SV_Y = FV_Y
    sv_right = SV_X + sv.hw + sv.right
    sv_right_wall = (
        (page_w - margin) if (PV_Y - pv_hh) > (margin + _TB_H) else (page_w - tb_w - margin)
    )

    drawable = (margin, margin, page_w - margin, page_h - margin)

    # Title block: a PINNED block.  Its lower-left corner sits _TB_CLEAR in from
    # the right page edge and _TB_CLEAR up from the bottom, _TB_H tall — the same
    # pin the renderer uses in _add_title_block.  Its clearance is the block's
    # own bands: DIM_PAD on the three free sides, and only down to the page
    # margin below (it abuts the bottom sheet edge).  Everything else is laid
    # out to work around its footprint.  (#112, ADR 0004.)
    title_block = ViewBlock(
        tb_w / 2,
        _TB_H / 2,
        top=DIM_PAD,
        right=DIM_PAD,
        bottom=_TB_CLEAR - margin,
        left=DIM_PAD,
    )
    tb_cx, tb_cy = page_w - _TB_CLEAR - tb_w / 2, _TB_CLEAR + _TB_H / 2

    # The iso is the one *placed* block: it takes the largest gap the fixed
    # blocks' footprints leave.  On the repack path use the MEASURED footprints
    # (bands may exceed DIM_PAD), so the iso stays clear of real annotations
    # rather than just the estimate's padded box (#121); the estimator path keeps
    # the DIM_PAD-padded boxes for byte-identity.
    if blocks is not None:
        obstacles = [
            fv.footprint(FV_X, FV_Y),
            pv.footprint(PV_X, PV_Y),
            sv.footprint(SV_X, SV_Y),
            title_block.footprint(tb_cx, tb_cy),
        ]
    else:
        obstacles = [
            _padded_box(FV_X, FV_Y, fv_hw, fv_hh),
            _padded_box(PV_X, PV_Y, fv_hw, pv_hh),
            _padded_box(SV_X, SV_Y, sv_hw, fv_hh),
            title_block.footprint(tb_cx, tb_cy),
        ]
    iso_left, iso_bottom, iso_right, iso_top = _largest_empty_rect(drawable, obstacles)
    # _largest_empty_rect falls back to the full drawable when the obstacles
    # leave no genuine gap; detect that (rect overlaps an obstacle) so callers
    # can treat "no room for the iso" as not-fitting rather than a huge phantom.
    iso_valid = not any(
        iso_left < o[2] and o[0] < iso_right and iso_bottom < o[3] and o[1] < iso_top
        for o in obstacles
    )

    # Does the packed disjoint layout actually fit the sheet? — the fitness the
    # (scale, page) search optimises (#121, ADR 0004).  The union of the three
    # view *footprints* (geometry + bands) must sit inside the drawable area; the
    # orthographic views must clear the title block (stay left of its column
    # unless their bottom is above it); and the iso must have a real gap.  This is
    # what tells the repack to escalate to a larger sheet when the measured
    # footprints no longer fit the estimate's page.
    _view_boxes = [
        fv.footprint(FV_X, FV_Y),
        pv.footprint(PV_X, PV_Y),
        sv.footprint(SV_X, SV_Y),
    ]
    cx0 = min(b[0] for b in _view_boxes)
    cy0 = min(b[1] for b in _view_boxes)
    cx1 = max(b[2] for b in _view_boxes)
    cy1 = max(b[3] for b in _view_boxes)
    _tol = 0.5
    _clears_tb = cy0 >= (_TB_CLEAR + _TB_H)
    _right_limit = (page_w - margin) if _clears_tb else (page_w - tb_w - margin)
    fits = (
        iso_valid
        and cy0 >= margin - _tol
        and cy1 <= page_h - margin + _tol
        and cx0 >= margin - _tol
        and cx1 <= _right_limit + _tol
    )

    return SimpleNamespace(
        x_offset=x_offset,
        fv_hw=fv_hw,
        fv_hh=fv_hh,
        pv_hh=pv_hh,
        sv_hw=sv_hw,
        FV_X=FV_X,
        FV_Y=FV_Y,
        PV_X=PV_X,
        PV_Y=PV_Y,
        SV_X=SV_X,
        SV_Y=SV_Y,
        sv_right=sv_right,
        sv_right_wall=sv_right_wall,
        iso_left=iso_left,
        iso_bottom=iso_bottom,
        iso_right=iso_right,
        iso_top=iso_top,
        ISO_X=(iso_left + iso_right) / 2,
        ISO_Y=(iso_bottom + iso_top) / 2,
        iso_valid=iso_valid,
        iso_natural=bbox_max * scale * _ISO_WIDTH_BUDGET,
        fits=fits,
    )


def _build_zones(g, margin, page_h):
    """Construct the FV/PV/SV annotation :class:`ViewZones` from a placement
    namespace *g* (the return of :func:`_layout_geometry`).

    Factored out of :func:`_analyse` so the measure-and-repack pass (#121) can
    rebuild the zones from the repacked geometry with the same arithmetic — the
    zones must track the moved view centres, not the pass-1 placement.
    """
    FV_X, FV_Y, fv_hw, fv_hh = g.FV_X, g.FV_Y, g.fv_hw, g.fv_hh
    PV_X, PV_Y, pv_hh = g.PV_X, g.PV_Y, g.pv_hh
    SV_X, SV_Y, sv_hw = g.SV_X, g.SV_Y, g.sv_hw

    fv_right_edge = FV_X + fv_hw
    fv_left_edge = FV_X - fv_hw
    fv_top_edge = FV_Y + fv_hh
    fv_bottom_edge = FV_Y - fv_hh
    pv_right_edge = PV_X + fv_hw  # plan has the same X half-width as front
    pv_left_edge = PV_X - fv_hw
    pv_top_edge = PV_Y + pv_hh
    pv_bottom_edge = PV_Y - pv_hh  # = fv_top_edge + DIM_PAD
    sv_top_edge = SV_Y + fv_hh  # side view has the same Z height as front
    # Outer limit for fv/pv right strips: must not enter the side view.
    sv_left_edge = SV_X - sv_hw  # = fv_right_edge + gap_fv_sv

    fv_zones = ViewZones(
        right=Strip(fv_right_edge, sv_left_edge, direction=1),
        left=Strip(fv_left_edge, margin, direction=-1),
        # Stop the front-view 'above' strip short of pv_bottom_edge by the
        # slack the pv_below slot leaves in the gap, derived (not re-typed) so
        # it tracks _DIM_PAD and the slot constants.
        above=Strip(fv_top_edge, pv_bottom_edge - (_DIM_PAD - _est_pv_below_depth()), direction=1),
        below=Strip(fv_bottom_edge, margin, direction=-1),
    )
    pv_zones = ViewZones(
        # Outer limit = sv_left_edge (not iso_right_limit) so bore callouts in
        # the plan view are bounded by the same hard wall as the FV right strip,
        # preventing labels from crossing dim_locy extension lines in the side
        # view.  gap_fv_sv is sized by _measure_strips to accommodate the widest
        # callout, so well-estimated labels will always fit within this bound.
        right=Strip(pv_right_edge, sv_left_edge, direction=1),
        left=Strip(pv_left_edge, margin, direction=-1),
        above=Strip(pv_top_edge, page_h - margin, direction=1),
        # gap_fv_pv = _DIM_PAD; pv_below needs _est_pv_below_depth() mm,
        # leaving (_DIM_PAD - _est_pv_below_depth()) mm slack (assert above).
        below=Strip(pv_bottom_edge, fv_top_edge, direction=-1),
    )
    sv_bottom_edge = SV_Y - fv_hh  # same as fv_bottom_edge; side and front share Z height
    sv_zones = ViewZones(
        # sv_right already includes DIM_PAD; anchor here so the strip never
        # places annotations inside that gap
        right=Strip(g.sv_right, g.sv_right_wall, direction=1),
        left=None,  # immediately abuts the front view's right edge
        above=Strip(sv_top_edge, page_h - margin, direction=1),
        below=Strip(sv_bottom_edge, margin, direction=-1),
    )
    return fv_zones, pv_zones, sv_zones


def _analyse(
    step_file, title, number, tolerance, drawn_by, out, scale=None, page=None, pmi="off"
) -> Analysis:
    """Load STEP or use a build123d Shape, analyse geometry, compute layout.

    Returns an :class:`Analysis`.
    """
    if isinstance(step_file, Shape):
        part = step_file
        src = "build123d object"
    else:
        part = _import_step(step_file)
        src = str(step_file)
    # AP242 STEP files carry PMI presentation geometry (annotation-plane
    # border wires, leader curves) beside the solid; left in, it draws as
    # phantom rectangles in every view and inflates the bounding box —
    # corrupting the scale choice and the envelope dimensions. The drawing
    # is of the solids.
    solids = part.solids()
    if solids:
        body = solids[0] if len(solids) == 1 else Compound(children=list(solids))
        if body.bounding_box().size != part.bounding_box().size or len(part.edges()) != len(
            body.edges()
        ):
            _log.info(
                "Dropping non-solid geometry from %s (PMI presentation data)",
                src,
            )
        part = body

    # Semantic PMI extraction (AP242 only; separate read-only pass).
    pmi_records: list = []
    if pmi != "off" and not isinstance(step_file, Shape):
        try:
            from draftwright.pmi import extract_pmi

            pmi_records = extract_pmi(step_file)
        except Exception as exc:
            _log.warning("PMI extraction failed: %s", exc)

    bb = part.bounding_box()
    x_size = bb.max.X - bb.min.X
    y_size = bb.max.Y - bb.min.Y
    z_size = bb.max.Z - bb.min.Z
    cx = (bb.min.X + bb.max.X) / 2
    cy = (bb.min.Y + bb.max.Y) / 2
    cz = (bb.min.Z + bb.max.Z) / 2
    bbox_max = max(x_size, y_size, z_size)

    _log.info("Loaded %s  bbox: %.2f × %.2f × %.2f mm", src, x_size, y_size, z_size)

    z_cyls, cross_cyls = analyse_cylinders(part)
    # Partial (fillet) faces are not features: they would pollute the OD,
    # the bore leaders, and the rotational classification alike (#81)
    full_z = full_cylinders(z_cyls)
    z_diams = dedup_diams(full_z)
    cross_diams = dedup_diams(full_cylinders(cross_cyls))

    _log.info("Z-axis diameters: %s", z_diams)
    if cross_diams:
        _log.info("Cross-hole diams: %s", cross_diams)

    od_cyl = max((c for c in full_z if c["external"]), key=lambda c: c["diameter"], default=None)
    od_diam = None
    if od_cyl:
        # Snap to the dedup_diams representative so comparisons against
        # z_diams entries (bore-leader exclusion, labels) are exact even if
        # the cylinder records ever carry unrounded OCCT diameters (#86)
        raw_od = od_cyl["diameter"]
        od_diam = min(z_diams, key=lambda d: abs(d - raw_od))
    od_axis_offset = (
        math.hypot(od_cyl["axis_xyz"][0] - cx, od_cyl["axis_xyz"][1] - cy) if od_cyl else 0.0
    )
    is_rotational = _is_rotational(x_size, y_size, od_diam, od_axis_offset)
    if z_diams and not is_rotational:
        _log.info("Part classified prismatic; skipping OD/centreline/bore annotations")

    face_zs = analyse_face_levels(part, min_area_frac=_STEP_MIN_AREA_FRAC)
    step_zs = [z for z in face_zs if z > bb.min.Z + 0.6 and z < bb.max.Z - 0.6]

    # Pass 1 (two-pass layout, #131): measure annotation strip depths before
    # view positions are fixed.  font_size=3.0 is a fixed page-mm constant so
    # all annotation sizes are scale-independent — no circularity.
    # Construct the same draft preset used later in build_drawing() to read
    # arrow_length and pad_around_text from their authoritative source rather
    # than re-stating them as magic literals in the estimators.
    _draft_est = draft_preset(font_size=_FONT_SIZE, decimal_precision=1)
    _arrow_length = _draft_est.arrow_length
    _pad_around_text = _draft_est.pad_around_text
    holes = find_holes(part, cyls=(z_cyls, cross_cyls))
    patterns = find_hole_patterns(holes)
    slots = find_slots(part)

    # Choose scale/page, iterating so the reserved step corridor matches the
    # number of steps the legibility gate will actually place (#1) — not the raw
    # face count. Otherwise a part with many sub-legible faces (e.g. a staircase
    # with 15 tiny treads) reserves a phantom step ladder that blocks a larger
    # scale. Seed conservatively (all faces), then re-gate at the chosen scale;
    # converges in a couple of rounds.
    n_for_sizing = len(step_zs)
    strips_i = None
    for _ in range(3):
        strips_i = _measure_strips(
            holes,
            patterns,
            n_for_sizing,
            bb,
            arrow_length=_arrow_length,
            pad_around_text=_pad_around_text,
        )
        SCALE, PAGE_W, PAGE_H, TB_W = choose_scale(
            x_size, y_size, z_size, n_steps=n_for_sizing, scale=scale, page=page, strips=strips_i
        )
        n_next = len(_legible_steps(step_zs, bb.min.Z, SCALE)[0])
        if n_next == n_for_sizing:
            break
        n_for_sizing = n_next
    if scale is not None:
        auto_scale, _, _, _ = choose_scale(
            x_size, y_size, z_size, n_steps=n_for_sizing, scale=None, page=page, strips=strips_i
        )
        if SCALE < auto_scale:
            min_dim = min(x_size, y_size, z_size)
            min_view = min_dim * SCALE
            if min_view < _MIN_VIEW_MM:
                safe = _MIN_VIEW_MM / min_dim
                raise ValueError(
                    f"scale {SCALE!r} projects the smallest part dimension "
                    f"({min_dim:.0f} mm) to {min_view:.1f} mm — "
                    f"annotation geometry degenerates below {_MIN_VIEW_MM:.0f} mm "
                    f"(OCCT Standard_DomainError / SIGABRT). "
                    f"Use scale ≥ {safe:.3g} or omit --scale for automatic selection."
                )
    DIM_PAD = _DIM_PAD
    margin = _MARGIN
    # Refine: apply the same legibility gate _auto_annotate uses for dim_step.
    n_steps = len(_legible_steps(step_zs, bb.min.Z, SCALE)[0])
    strips = _measure_strips(
        holes,
        patterns,
        n_steps,
        bb,
        arrow_length=_arrow_length,
        pad_around_text=_pad_around_text,
    )
    # View positions + iso empty-rectangle, shared with scale selection (_fits)
    # via _layout_geometry so placement and fit never diverge (#11).  _fit_iso_view
    # later scales the iso to fill its rectangle.
    _g = _layout_geometry(x_size, y_size, z_size, SCALE, PAGE_W, PAGE_H, TB_W, strips, n_steps)
    fv_hw = _g.fv_hw
    fv_hh = _g.fv_hh
    pv_hh = _g.pv_hh
    sv_hw = _g.sv_hw
    x_offset = _g.x_offset
    FV_X = _g.FV_X
    FV_Y = _g.FV_Y
    PV_X = _g.PV_X
    PV_Y = _g.PV_Y
    SV_X = _g.SV_X
    SV_Y = _g.SV_Y
    sv_right = _g.sv_right
    iso_left_limit = _g.iso_left
    iso_bottom_limit = _g.iso_bottom
    iso_right_limit = _g.iso_right
    iso_top_limit = _g.iso_top
    ISO_X = _g.ISO_X
    ISO_Y = _g.ISO_Y

    # ------------------------------------------------------------------
    # Strip / zone construction.
    # Phase 1: defines regions only — annotation functions still use their
    # own hard-coded offsets.  Later phases will route each annotation
    # through strip.allocate().  The iso view's outer limits are conservative
    # here (PAGE_H - margin / iso_right_limit); _auto_annotate() tightens
    # them once the iso has been projected.
    fv_zones, pv_zones, sv_zones = _build_zones(_g, margin, PAGE_H)

    page_label = {297: "A4", 420: "A3", 594: "A2", 841: "A1", 1189: "A0"}.get(
        int(PAGE_W), f"{PAGE_W:.0f}mm"
    )
    _log.info(
        "Scale %s:1  page %s  FV(%.0f,%.0f) PV(%.0f,%.0f) SV(%.0f,%.0f) ISO(%.0f,%.0f)",
        SCALE,
        page_label,
        FV_X,
        FV_Y,
        PV_X,
        PV_Y,
        SV_X,
        SV_Y,
        ISO_X,
        ISO_Y,
    )

    return Analysis(
        part=part,
        bb=bb,
        x_size=x_size,
        y_size=y_size,
        z_size=z_size,
        cx=cx,
        cy=cy,
        cz=cz,
        bbox_max=bbox_max,
        holes=holes,
        patterns=patterns,
        slots=slots,
        z_diams=z_diams,
        cross_diams=cross_diams,
        cyls=(z_cyls, cross_cyls),
        od_diam=od_diam,
        is_rotational=is_rotational,
        step_zs=step_zs,
        sv_right=sv_right,
        iso_right_limit=iso_right_limit,
        SCALE=SCALE,
        PAGE_W=PAGE_W,
        PAGE_H=PAGE_H,
        TB_W=TB_W,
        DIM_PAD=DIM_PAD,
        margin=margin,
        x_offset=x_offset,
        FV_X=FV_X,
        FV_Y=FV_Y,
        PV_X=PV_X,
        PV_Y=PV_Y,
        SV_X=SV_X,
        SV_Y=SV_Y,
        proj=_Projector(
            fv_x=FV_X,
            fv_y=FV_Y,
            sv_x=SV_X,
            sv_y=SV_Y,
            pv_x=PV_X,
            pv_y=PV_Y,
            cx=cx,
            cy=cy,
            cz=cz,
            scale=SCALE,
        ),
        ISO_X=ISO_X,
        ISO_Y=ISO_Y,
        iso_left_limit=iso_left_limit,
        iso_bottom_limit=iso_bottom_limit,
        iso_top_limit=iso_top_limit,
        # View half-extents in page units (convenient for strip arithmetic)
        fv_hw=fv_hw,
        fv_hh=fv_hh,
        pv_hh=pv_hh,
        sv_hw=sv_hw,
        # Strip / zone layout model (Phase 1 — regions defined, not yet used)
        fv_zones=fv_zones,
        pv_zones=pv_zones,
        sv_zones=sv_zones,
        step_file=step_file,
        title=title,
        number=number,
        tolerance=tolerance,
        drawn_by=drawn_by,
        out=out,
        pmi=pmi_records,
        pmi_mode=pmi,
    )


# ---------------------------------------------------------------------------
# Drawing builder (composable; make_drawing == build_drawing + export)
# ---------------------------------------------------------------------------


@dataclass
class FeatureInfo:
    """A detected geometric feature, expressed in page coordinates.

    Returned by :meth:`Drawing.features`.  ``page_pos`` is in the coordinate
    system of the view passed to that call.
    """

    type: str
    page_pos: tuple
    diameter: float
    through: bool
    depth: float | None
    count: int


class Drawing:
    """A composable technical drawing — the editable form of :func:`make_drawing`.

    A ``Drawing`` holds the projected views, the annotation list, and per-view
    coordinate helpers. :func:`build_drawing` returns one pre-populated with the
    standard 4-view layout and automatic dimensions; you then add or remove
    annotations, add section/auxiliary views, and finally :meth:`export`.

    Attributes:
        scale: drawing scale factor (e.g. ``2.0`` for 2:1).
        page_w, page_h: sheet size in mm.
        tb_w: title-block width in mm.
        draft: the shared ``Draft`` preset used by the automatic annotations.
        look_at: scaled centroid ``(x, y, z)`` — the default ``look_at`` and a
            building block for custom view cameras (see :meth:`add_view`).
        dist: orthographic camera distance in scaled space.
        centroid: unscaled centroid ``(x, y, z)``.
        views: ``{name: (visible_compound, hidden_compound_or_None)}``.
        items: ordered list of annotation objects (mutable).
        part: the source solid, when known — enables the feature-coverage lint.
        assembly: feature-coverage severity control — ``None`` auto-detects a
            multi-solid part as an assembly (per-part bores at ``info``),
            ``True``/``False`` forces it (#69).

    The constructor also accepts ``cyls``, a precomputed
    ``analyse_cylinders(part)`` result (cached privately; computed lazily on
    first :meth:`lint` otherwise).
    """

    def __init__(
        self,
        *,
        scale,
        page_w,
        page_h,
        tb_w,
        draft,
        look_at,
        dist,
        centroid,
        out,
        part=None,
        cyls=None,
        assembly=None,
    ):
        self.scale = scale
        self.part = part
        self._cyl_cache = cyls
        # None → the coverage lint auto-detects a multi-solid part as an
        # assembly; True/False forces assembly/strict severity (#69).
        self.assembly = assembly
        self.page_w = page_w
        self.page_h = page_h
        self.tb_w = tb_w
        self.draft = draft
        self.look_at = look_at
        self.dist = dist
        self.centroid = centroid
        self.out = out
        self.views: dict = {}
        self.items: list = []
        self._coords: dict = {}
        # Annotation identity, ownership, pins, and build issues live in the
        # registry (#138 / ADR 0005, Step 2). `_named` / `_anno_view` / `_pinned`
        # / `_build_issues` remain reachable as properties (below) so tests and
        # helpers that read through them keep working during the migration.
        self._registry = AnnotationRegistry()
        # Lint-side coverage signal (pattern callouts, patterned holes, dropped
        # callout diameters) lives in its own owner (#138 / ADR 0005, Step 3).
        # _pattern_callouts / _patterned_holes / _dropped_callout_diams remain
        # reachable as properties below.
        self._coverage = CoverageState()
        # One per-drawing cache for lint_drawing's per-view edge bboxes, keyed on
        # id(view shape). repair() / lint_summary() lint the SAME projected view
        # objects (self.views) repeatedly, so persisting it recomputes each
        # view's edges once instead of every lint (helpers #143/#164).
        self._view_edge_cache: dict = {}
        self.svg_path: str | None = None
        self.dxf_path: str | None = None
        self._analysis: Analysis | None = None

    # -- annotation registry (compat accessors, ADR 0005 §4) ------------------
    # The registry owns these four; they are exposed as their live containers so
    # code that reads or mutates ``dwg._named`` / ``_anno_view`` / ``_pinned`` /
    # ``_build_issues`` keeps working until those call sites are redirected.
    @property
    def _named(self) -> dict:
        return self._registry._named

    @_named.setter
    def _named(self, value) -> None:
        self._registry._named = value

    @property
    def _anno_view(self) -> dict:
        return self._registry._anno_view

    @_anno_view.setter
    def _anno_view(self, value) -> None:
        self._registry._anno_view = value

    @property
    def _pinned(self) -> set:
        return self._registry._pinned

    @_pinned.setter
    def _pinned(self, value) -> None:
        self._registry._pinned = value

    @property
    def _build_issues(self) -> list:
        return self._registry._build_issues

    @_build_issues.setter
    def _build_issues(self, value) -> None:
        self._registry._build_issues = value

    # -- coverage state (compat accessors, ADR 0005 §4) -----------------------
    # The CoverageState owner holds these three; exposed as their live containers
    # so code reading dwg._pattern_callouts / _patterned_holes /
    # _dropped_callout_diams keeps working until those sites are redirected.
    @property
    def _pattern_callouts(self) -> set:
        return self._coverage._pattern_callouts

    @_pattern_callouts.setter
    def _pattern_callouts(self, value) -> None:
        self._coverage._pattern_callouts = value

    @property
    def _patterned_holes(self) -> set:
        return self._coverage._patterned_holes

    @_patterned_holes.setter
    def _patterned_holes(self, value) -> None:
        self._coverage._patterned_holes = value

    @property
    def _dropped_callout_diams(self) -> list:
        return self._coverage._dropped_callout_diams

    @_dropped_callout_diams.setter
    def _dropped_callout_diams(self, value) -> None:
        self._coverage._dropped_callout_diams = value

    # -- coverage state operations (used by the annotation passes) ------------
    def _cover_pattern(self, callout_name, holes):
        """Record that placed *callout_name* documents *holes* (#92)."""
        self._coverage.cover_pattern(callout_name, holes)

    def _is_pattern_callout(self, name) -> bool:
        """Is *name* a placed pattern (grouped ``n× ⌀``) callout?"""
        return self._coverage.is_pattern_callout(name)

    def _is_hole_patterned(self, hole) -> bool:
        """Is *hole* already documented by a placed pattern callout?"""
        return self._coverage.is_hole_patterned(hole)

    def _reset_dropped_callout_diams(self):
        """Clear dropped-diameter tracking (top of :func:`_auto_annotate`)."""
        self._coverage.reset_dropped()

    def _drop_callout_diam(self, diam):
        """Record a diameter dropped by the per-view callout cap."""
        self._coverage.drop_diam(diam)

    # -- views ----------------------------------------------------------------
    def add_view(self, name, shape, camera, up, position, *, look_at=None, scaled=False):
        """Project ``shape`` from ``camera`` and place it at ``position``.

        Args:
            name: view name (key in :attr:`views`); also used for coordinate lookups.
            shape: a build123d ``Shape`` to project. Given in world (unscaled)
                coordinates and scaled internally unless ``scaled=True``.
            camera, up, look_at: viewport parameters in **scaled** space (the same
                convention the standard views use). ``look_at`` defaults to
                :attr:`look_at` (the scaled centroid). Compose custom cameras from
                :attr:`look_at` and :attr:`dist`.
            position: ``(x, y)`` page position for the view centre, in mm.
            scaled: set ``True`` if ``shape`` is already scaled by :attr:`scale`.

        Returns:
            The :class:`ViewCoordinates` for this view (also via :meth:`coords`),
            for mapping world points to page coordinates.
        """
        la = self.look_at if look_at is None else look_at
        shape_s = shape if scaled else shape.scale(self.scale)
        vis, hid = shape_s.project_to_viewport(camera, up, la)
        vl, hl = list(vis), list(hid)
        if not vl and not hl:
            raise ValueError(
                f"project_to_viewport returned empty geometry for view {name!r} "
                f"(camera {camera}) — check the camera position and look_at."
            )
        axes = view_axes(camera, up, la)
        # Recover exact circles for revolution silhouettes that HLR projected as
        # approximating splines (#67) — a no-op when no revolution axis is
        # parallel to the view direction (e.g. iso/section views).
        if vl:
            vd = Vector(la[0] - camera[0], la[1] - camera[1], la[2] - camera[2])
            vd = vd.normalized()
            proj = _raw_view_projector(axes, la)
            vl, n_circ = _exactify_silhouettes(vl, shape_s.faces(), (vd.X, vd.Y, vd.Z), proj)
            if n_circ:
                _log.info("  %s: %d silhouette spline(s) refit to circles", name, n_circ)
        loc = Location((position[0], position[1], 0))
        placed = Compound(children=vl).locate(loc)
        placed_hid = Compound(children=hl).locate(loc) if hl else None
        self.views[name] = (placed, placed_hid)
        cx, cy, cz = la[0] / self.scale, la[1] / self.scale, la[2] / self.scale
        self._coords[name] = ViewCoordinates(
            axes, position[0], position[1], cx, cy, cz, self.scale
        )
        _log.info("  %s: %d visible / %d hidden", name, len(vl), len(hl))
        return self._coords[name]

    def coords(self, view):
        """Return the :class:`ViewCoordinates` for a named view."""
        return self._coords[view]

    def at(self, view, x, y, z):
        """Map a world point to a page point ``(px, py, 0)`` in ``view``."""
        px, py = self._coords[view].pp(x, y, z)
        return (px, py, 0.0)

    def view_bounds(self, view):
        """Return ``(x_min, y_min, x_max, y_max)`` of the projected geometry in
        *view*, or ``None`` if the view is unknown (#28).

        The box is the tight bounding box of the placed silhouette — visible
        plus hidden lines — in page coordinates (mm from the sheet origin), the
        same space :meth:`at` returns. Use it to place free-form notes, leader
        elbows and the like just outside a view without guessing offsets::

            x0, y0, x1, y1 = dwg.view_bounds("front")
            dwg.add(Note("SEE NOTE 1", (x1 + 5, (y0 + y1) / 2), dwg.draft))
        """
        placed = self.views.get(view)
        if placed is None:
            return None
        vis, hid = placed
        bb = vis.bounding_box()
        x0, y0, x1, y1 = bb.min.X, bb.min.Y, bb.max.X, bb.max.Y
        if hid:
            hb = hid.bounding_box()
            x0, y0 = min(x0, hb.min.X), min(y0, hb.min.Y)
            x1, y1 = max(x1, hb.max.X), max(y1, hb.max.Y)
        return (x0, y0, x1, y1)

    def features(self, view="front"):
        """Return detected geometric features in page coordinates for *view*.

        Holes are grouped by machining spec (diameter + depth + cbore) and
        returned as :class:`FeatureInfo` objects with ``count`` set to the
        number of identical holes at that spec.  Each group's ``page_pos``
        is the page position of the first hole in the group.

        The view determines which holes appear as circles (and are therefore
        annotatable from that view):

        - ``"plan"``  → Z-axis holes
        - ``"front"`` → Y-axis holes
        - ``"side"``  → X-axis holes

        Returns an empty list when no analysis is available or the view name
        is unrecognised.
        """
        a = self._analysis
        if a is None:
            return []

        _axis_for_view = {"plan": "z", "front": "y", "side": "x"}
        target_axis = _axis_for_view.get(view)
        if target_axis is None:
            return []

        if view not in self._coords:
            return []

        def _to_page(h):
            return self._coords[view].pp(*h.location)

        groups: dict = {}
        for h in a.holes:
            if _axis_letter(h) != target_axis:
                continue
            groups.setdefault(HoleSpec.from_hole(h), []).append(h)

        result = []
        for group in groups.values():
            rep = group[0]
            result.append(
                FeatureInfo(
                    type="hole",
                    page_pos=_to_page(rep),
                    diameter=rep.diameter,
                    through=rep.bottom == "through",
                    depth=None if rep.bottom == "through" else rep.depth,
                    count=len(group),
                )
            )
        return result

    def place_dim(self, p1, p2, side, view, draft, *, name=None, slot=8.0, **kwargs):
        """Add a :class:`~build123d_drafting.helpers.Dimension` that stacks cleanly
        with the auto-generated dimensions by delegating to the same strip-allocation
        system (:class:`Strip`) that :func:`build_drawing` uses internally.

        Args:
            p1, p2: page-coordinate tuples ``(px, py, 0)`` — use :meth:`at` to
                convert world coordinates.
            side: ``"above"``, ``"below"``, ``"left"``, or ``"right"``.
            view: ``"front"``, ``"plan"``, or ``"side"``.
            draft: the drawing's :attr:`draft` preset.
            name: optional annotation name for later :meth:`remove` / replace.
            slot: strip slot depth (mm); the perpendicular space reserved per dim.
            **kwargs: forwarded to ``Dimension`` (e.g. ``label=``, ``tolerance=``).

        Falls back to a fixed ``slot`` offset when the strip is full or when no
        layout analysis is available (e.g. when ``auto_dims=False`` was not used
        with :func:`build_drawing`).
        """
        a = self._analysis
        _view_zones = {"front": "fv_zones", "plan": "pv_zones", "side": "sv_zones"}
        strip = None
        if a is not None:
            zones = getattr(a, _view_zones.get(view, ""), None)
            if zones is not None:
                strip = getattr(zones, side, None)
        dist = slot
        if strip is not None:
            coord = strip.allocate(slot)
            if coord is not None:
                ax = 0 if side in ("left", "right") else 1
                if side in ("right", "above"):
                    dist = coord - max(p[ax] for p in (p1, p2))
                else:
                    dist = min(p[ax] for p in (p1, p2)) - coord
        # p1/p2 are page coordinates; Dimension labels the raw page distance when
        # no label is given, which is scale-too-big at non-1:1 scales. Supply the
        # real-world length (page distance ÷ drawing scale) unless the caller set
        # an explicit label.
        if "label" not in kwargs:
            page_len = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
            kwargs["label"] = _fmt(page_len / self.scale)
        return self.add(_dim(p1, p2, side, max(dist, 4.0), draft, **kwargs), name)

    # -- annotations ----------------------------------------------------------
    def add(self, obj, name=None, view=None):
        """Register an annotation so lint and export include it; returns ``obj``.

        Re-using an existing ``name`` replaces the previously added object (it is
        dropped from :attr:`items`), so a name always maps to one object.

        ``view`` records which orthographic view ("front"/"plan"/"side") owns
        this annotation, so the layout can compose each view with its own
        annotations as a single footprint block (#121).  Pass ``None`` for
        drawing-level marks (title block, iso/section notes) that belong to no
        single view.
        """
        displaced = self._registry.named(name) if name is not None else None
        if displaced is not None:
            self.items.remove(displaced)
        annotate(obj, name)
        self.items.append(obj)
        # The registry records name -> obj and the owning view, and drops any pin
        # the replaced name carried — a replacement is a fresh object (#89) — and
        # clears a stale ownership tag when re-added view-less (#121).
        self._registry.add(obj, name, view)
        return obj

    def remove(self, name):
        """Remove a previously named annotation. Raises ``KeyError`` if absent."""
        obj = self._registry.remove(name)  # forgets object, view, and pin (#89)
        if obj is None:
            raise KeyError(f"no annotation named {name!r}")
        self.items.remove(obj)
        return obj

    def annotations(self) -> dict:
        """Return ``{name: type_name}`` for every *named* annotation (#27).

        Lets a script introspect what is already on the drawing before adding
        more — e.g. ``if "dim_width" not in dwg.annotations()`` — so it can do
        incremental edits without risking a silent name-collision replace.
        Unnamed annotations are omitted; iterate :attr:`items` for those.
        """
        return self._registry.annotations()

    def get_annotation(self, name):
        """Return the named annotation object, or ``None`` if no such name (#27)."""
        return self._registry.named(name)

    def add_table(self, rows, *, prefer="tr", name="table", block_cols=None):
        """Add a generic data table, placed in a free corner (#93).

        *rows* is a list of equal-length string tuples (``rows[0]`` is the
        header). The table is positioned by :func:`fit_box` clear of the views,
        title block, and existing annotations; *prefer* is the page corner to sit
        nearest. Returns the table annotation, or ``None`` if it has no rows or
        will not fit (recorded as ``table_dropped`` lint). Gear-data, BOM, and
        revision tables all go through here; :meth:`add_hole_table` is the
        hole-specific convenience built on it.
        """
        if not rows:
            return None
        table = _build_table(rows, self.draft, block_cols=block_cols)
        w, h = table.table_size
        a = self._analysis
        margin = a.margin if a is not None else 10.0
        pw = a.PAGE_W if a is not None else self.page_w
        ph = a.PAGE_H if a is not None else self.page_h
        region = (margin, margin, pw - margin, ph - margin)
        obstacles = [b for v in self.views if (b := self.view_bounds(v)) is not None]
        for o in self.items:
            try:
                bb = o.bounding_box()
            except Exception:  # noqa: BLE001 — not every annotation bbox-es cleanly
                continue
            obstacles.append((bb.min.X, bb.min.Y, bb.max.X, bb.max.Y))
        pos = fit_box((w, h), region, obstacles, prefer)
        if pos is None:
            self._record_build_issue(
                "warning", "table_dropped", f"table {name!r} did not fit the sheet"
            )
            return None
        return self.add(table.locate(Location((pos[0], pos[1], 0))), name)

    def _hole_spec_groups(self, view):
        """Ordered ``(tag, [holes])`` spec-groups of *view*'s holes (tags A, B,
        …). The shared basis for the hole table's rows and its balloons, so the
        TAG column and the balloon glyphs line up."""
        a = self._analysis
        target = {"plan": "z", "front": "y", "side": "x"}.get(view)
        if a is None or target is None or view not in self._coords:
            return []

        groups: dict = {}
        for h in a.holes:
            if _axis_letter(h) == target:
                groups.setdefault(HoleSpec.from_hole(h), []).append(h)
        glist = list(groups.values())
        return list(zip(_tag_sequence(len(glist)), glist, strict=True))

    def _add_balloons(self, view, specs):
        """Place a leadered balloon for each ``(tag, j, hole)`` in *specs*,
        fitted into the halo the layout reserved around the view (#111).

        Each hole is assigned to the nearest reserved band — left, right, or top
        of the plan view (never the bottom: the front view abuts it there) — and
        the balloons in each band are spread along it with the 1D strip solver so
        none overlap, each pulled toward its hole's coordinate.  A :class:`Leader`
        then runs from the hole rim to the glyph.  Because the layout reserved
        this band before placing the views (:func:`_est_plan_halo` /
        :func:`_will_balloon`), the balloons sit in clear space off the part and
        no leader crosses a neighbouring view.
        """
        a = self._analysis
        if view not in self._coords or a is None:
            return
        pp = self._coords[view].pp
        fs = self.draft.font_size
        r = fs * 1.5  # circle comfortably larger than the glyph
        standoff = _STRIP_GAP
        gap = 2 * r + 2 * _STRIP_SPACING  # min centre-to-centre: balloon + padding both sides

        # Plan-view page edges; the reserved bands sit just outside them.
        pl, pr = a.PV_X - a.fv_hw, a.PV_X + a.fv_hw
        pt, pb = a.PV_Y + a.pv_hh, a.PV_Y - a.pv_hh
        sv_left = a.SV_X - a.sv_hw
        margin, ph = a.margin, a.PAGE_H

        # Stack the balloon ring *beyond* the annotations already placed around
        # the plan view, not on top of them (#121).
        za = a.pv_zones
        bot_dim = za.below.depth_used if za and za.below else 0.0
        left_dim = za.left.depth_used if za and za.left else 0.0
        right_dim = za.right.depth_used if za and za.right else 0.0
        # The TOP band is the one that goes stale: the hole-table escalation
        # deletes the X-location dims but never rewinds the above-strip cursor, so
        # za.above.depth_used keeps their high-water mark (~240 mm of phantom
        # corridor on CTC-02) and the top ring floats ~150 mm over empty space
        # (#125). Top balloons vary in X at a fixed Y, so they must clear the
        # DIMENSIONS spanning the plan's width above it — measure the real depth
        # of those (pitch dims dim_* AND PMI bore dims pmi_*). Construction
        # centrelines (bc_*) are crossable, not obstructions, so they are
        # excluded — their bolt-circle bbox would otherwise re-inflate the band.
        # Left/right/bottom are NOT stale: the deleted X-location dims only ever
        # allocated into the above strip (dim_locy tiers above the *side* view,
        # the width dim below is never removed), so those keep their correct
        # shallow strip depth.
        top_dim = 0.0
        for nm, obj in self._named.items():
            if not nm.startswith(("dim_", "pmi_")) or self._anno_view.get(nm) != view:
                continue
            try:
                ob = obj.bounding_box()
            except Exception:  # noqa: BLE001 — a mark with no bbox can't obstruct
                continue
            if ob.max.X > pl and ob.min.X < pr and ob.max.Y > pt:
                top_dim = max(top_dim, ob.max.Y - pt)

        # A bottom band (below PV, beyond the overall-width dim) is usable only
        # when the FV↔PV gap has room for the width dim *and* a balloon row;
        # otherwise bottom-edge holes fall back to the nearest side/top band.
        bottom_line = pb - bot_dim - standoff - r
        has_bottom = pb - (a.FV_Y + a.fv_hh) > bot_dim + standoff + 2 * r

        # Assign each hole to the nearest reserved band.
        bands: dict = {"left": [], "right": [], "top": [], "bottom": []}
        for tag, j, hole in specs:
            cx, cy = pp(*hole.location)
            choices = {"left": cx - pl, "right": pr - cx, "top": pt - cy}
            if has_bottom:
                choices["bottom"] = cy - pb
            bands[min(choices, key=lambda s: choices[s])].append((tag, j, hole, cx, cy))

        # left/right balloons vary in Y at a fixed X just outside the part; top
        # and bottom balloons vary in X at a fixed Y just beyond it. Each line is
        # offset by its side's dim depth so the ring sits clear of the dims.
        self._place_band(
            view,
            bands["left"],
            "y",
            pl - left_dim - standoff - r,
            margin + r,
            ph - margin - r,
            gap,
            fs,
            r,
        )
        self._place_band(
            view,
            bands["right"],
            "y",
            pr + right_dim + standoff + r,
            margin + r,
            ph - margin - r,
            gap,
            fs,
            r,
        )
        self._place_band(
            view,
            bands["top"],
            "x",
            pt + top_dim + standoff + r,
            pl - standoff,
            sv_left - r,
            gap,
            fs,
            r,
        )
        self._place_band(
            view,
            bands["bottom"],
            "x",
            bottom_line,
            pl - standoff,
            sv_left - r,
            gap,
            fs,
            r,
        )

    def _place_band(self, view, members, axis, line, lo, hi, gap, fs, r):
        """Spread *members* (``(tag, j, hole, cx, cy)``) along one reserved band
        with the strip solver, then render a leadered balloon for each (#111).

        *axis* is the band's free axis (``"y"`` for the left/right bands, ``"x"``
        for the top); *line* is the fixed coordinate of the other axis.  Overflow
        beyond ``[lo, hi]`` drops the tail rather than running balloons off-page.
        """
        if not members:
            return
        k = 4 if axis == "y" else 3  # index of cy / cx in the member tuple
        members.sort(key=lambda m: m[k])
        naturals = [m[k] for m in members]
        coords = (
            _solve_strip_1d(naturals, gap, lo, hi)
            or _greedy_strip_1d(naturals, gap, lo, hi)
            or _greedy_strip_1d(naturals, gap, lo, hi, prefix=True)
        )
        for (tag, j, hole, cx, cy), c in zip(members, coords):
            bx, by = (line, c) if axis == "y" else (c, line)
            self._render_balloon(view, tag, j, hole, cx, cy, bx, by, fs, r)

    def _render_balloon(self, view, tag, j, hole, cx, cy, bx, by, fs, r):
        """Build and add one balloon glyph + leader at solved centre ``(bx, by)``
        for hole ``(cx, cy)`` (#111)."""
        loc = Location((bx, by, 0))
        # The annotation layer fills closed paths, so a circle edge renders as a
        # disc. A thin annular FACE fills as a ring — i.e. a circle outline.
        ring_faces = [f.moved(loc) for f in (Circle(r) - Circle(r - 0.35)).faces()]
        text = Text(
            txt=tag,
            font_size=fs,
            font_path=PLEX_MONO,
            align=(Align.CENTER, Align.CENTER),
            mode=Mode.PRIVATE,
        ).locate(loc)
        parts = [*ring_faces, *text.faces()]
        # Leader from the hole rim to the balloon's near edge — the glyph is the
        # label, so label="".  Skipped when the balloon could not clear the hole
        # (degenerate fallback), where a leader would be a stub through the ring.
        dx, dy = bx - cx, by - cy
        dist = math.hypot(dx, dy)
        hole_r = hole.diameter * self.scale / 2
        if dist > hole_r + r:
            ux, uy = dx / dist, dy / dist
            tip = (cx + ux * hole_r, cy + uy * hole_r, 0)
            elbow = (bx - ux * r, by - uy * r, 0)
            parts.append(Leader(tip, elbow, "", self.draft))
        balloon = Compound(children=parts)
        # Furniture that legitimately sits on the view geometry — exempt from the
        # annotation-overlap / centreline lint, as the section arrows do.
        balloon.is_centerline = True
        self.add(balloon, f"balloon_{view}_{tag}_{j}", view=view)

    def _add_balloon(self, view, tag, j, hole):
        """Single-balloon convenience over :meth:`_add_balloons` (#111)."""
        self._add_balloons(view, [(tag, j, hole)])

    def add_hole_table(self, view="plan", *, prefer="tr", name=None, balloons=True):
        """Add a hole table for *view*'s holes, placed in a free corner (#93).

        One row per hole spec-group — ``TAG | ⌀ | DEPTH | QTY`` with tags
        ``A, B, …`` — placed via :meth:`add_table`. With *balloons* (the
        default) a circled tag is added at each hole keyed to its row. The table
        carries ``covers_diameters`` so the coverage lint counts the tabulated
        holes as dimensioned. Returns the table, or ``None`` when *view* has no
        holes or it will not fit.
        """
        groups = self._hole_spec_groups(view)
        if not groups:
            return None
        rows = [("TAG", "⌀", "DEPTH", "QTY")]
        diams = []
        for tag, holes in groups:
            h = holes[0]
            depth = "THRU" if h.bottom == "through" else (_fmt(h.depth) if h.depth else "")
            rows.append((tag, f"ø{_fmt(h.diameter)}", depth, str(len(holes))))
            diams.append(h.diameter)
        table = self.add_table(rows, prefer=prefer, name=name or f"hole_table_{view}")
        if table is None:
            return None
        # The table documents these diameters — let lint see that (#93).
        table.covers_diameters = tuple(diams)
        if balloons:
            self._add_balloons(
                view,
                [(tag, j, h) for tag, holes in groups for j, h in enumerate(holes)],
            )
        return table

    def pin(self, name):
        """Pin a named annotation so the engine never moves it (#89).

        A deliberate placement — by you or an AI — must win over automatic
        layout. :meth:`repair` will not re-place a pinned annotation, and the
        constraint solver (ADR 0003) treats it as fixed. Pinning fixes the
        *position*, not existence: :meth:`remove` and :meth:`clear_annotations`
        still apply. Raises ``KeyError`` if *name* is not a known annotation.
        Returns ``self`` for chaining.
        """
        if name not in self._registry:
            raise KeyError(f"no annotation named {name!r}")
        self._registry.pin(name)
        return self

    def unpin(self, name):
        """Release a pin so the engine may move *name* again (#89). Returns
        ``self``; a no-op if *name* was not pinned."""
        self._registry.unpin(name)
        return self

    def clear_annotations(self, keep=("title_block",)):
        """Remove all annotations except those named in *keep* (#74).

        Wholesale removal that does not depend on the automatic naming
        scheme — ``dwg.clear_annotations()`` strips every automatic dimension,
        leader, and centreline but keeps the title block.

        Returns:
            The list of removed annotation objects.
        """
        kept_named = self._registry.clear(keep)  # prunes names, views, and pins
        kept_ids = {id(o) for o in kept_named.values()}
        removed = [o for o in self.items if id(o) not in kept_ids]
        self.items = [o for o in self.items if id(o) in kept_ids]
        return removed

    def _record_build_issue(self, severity, code, message):
        """Record a lint issue discovered during construction (e.g. an
        annotation the layout had to drop). Surfaced by :meth:`lint` so a
        dropped feature is never silent."""
        self._registry.record_issue(LintIssue(severity=severity, code=code, message=message))

    def _reset_build_issues(self):
        """Clear build-time issues — called at the top of :func:`_auto_annotate`
        so re-annotation does not accumulate them."""
        self._registry.reset_issues()

    def _drop_build_issues(self, *codes):
        """Drop recorded build issues whose code is in *codes* (a fallback that
        restored tentatively-dropped annotations un-records their drop)."""
        self._registry.drop_issues(codes)

    # -- repair ---------------------------------------------------------------
    def _find_dim(self, label):
        """Return the re-placeable dimension whose label is *label*, or None.

        Only dimensions built by :func:`_dim` (carrying ``_dw_spec``) qualify;
        leaders, callouts and hand-built annotations are left untouched. A
        pinned dimension (#89) is also skipped — a deliberate placement must
        win over automatic repair.
        """
        # Identity-based, matching clear_annotations: "this specific object",
        # not build123d's geometric Shape equality.
        pinned_ids = self._registry.pinned_object_ids()
        for o in self.items:
            if id(o) in pinned_ids:
                continue
            if getattr(o, "_dw_spec", None) is not None and getattr(o, "label", None) == label:
                return o
        return None

    def _replace_dim(self, old, new):
        """Swap *old* for *new* in :attr:`items`, preserving its name and
        any per-view scale tag (so a re-placed detail-view dim stays at scale)."""
        if getattr(old, "_dw_scale", None) is not None:
            new._dw_scale = old._dw_scale
        self.items[self.items.index(old)] = new
        for n, o in self._named.items():
            if o is old:
                self._named[n] = new

    def _repair_dim_inside_part(self, issue) -> bool:
        """Flip a dimension that sits inside the view onto the opposite side."""
        labels = _QUOTED_RE.findall(issue.message)
        dim = self._find_dim(labels[0]) if labels else None
        if dim is None:
            return False
        s = dim._dw_spec
        new_side = _OPPOSITE_SIDE.get(s.side)
        if new_side is None:
            return False
        self._replace_dim(dim, _dim(s.p1, s.p2, new_side, s.distance, s.draft, **s.kwargs))
        return True

    def _repair_overlap(self, issue) -> bool:
        """Push the first re-placeable label in an overlap one strip-row further
        out so the two labels separate. Monotonic, so repeated passes converge."""
        step = _STRIP_SPACING + _SLOT_DIM_HEIGHT
        for label in _QUOTED_RE.findall(issue.message):
            dim = self._find_dim(label)
            if dim is None:
                continue
            s = dim._dw_spec
            self._replace_dim(
                dim, _dim(s.p1, s.p2, s.side, s.distance + step, s.draft, **s.kwargs)
            )
            return True
        return False

    def repair(self, max_iter: int = 3):
        """Close the lint→repair loop: act on violations, don't only report them.

        After the greedy initial placement, re-place the dimensions behind the
        mechanically-clear violations and re-lint, bounded to *max_iter* passes:

        - ``dim_inside_part`` — the offset is on the wrong side; flip it once.
        - ``annotation_overlap`` — two labels collide; push one further out.

        Only engine-built dimensions (carrying ``_dw_spec``) are re-placeable;
        leaders, callouts and standards-judgement issues (e.g.
        ``missing_principal_dimension``) are left for the caller. Each side
        flip is attempted at most once and overlap pushes only move outward, so
        the loop terminates and a clean drawing is returned unchanged.

        A pass that would *net-increase* the issue count (e.g. an overlap push
        that shoves a label out of frame on a tight sheet) is rolled back and
        the loop stops, so :meth:`repair` never makes a drawing worse.

        Returns ``self`` for chaining.
        """
        flipped: set = set()
        for _ in range(max_iter):
            before = self.lint()
            if not before:
                break
            snap_annotations = list(self.items)
            snap_named = dict(self._named)
            changed = False
            for issue in before:
                if issue.code not in _REPAIRABLE_CODES:
                    continue
                if issue.code == "dim_inside_part":
                    labels = _QUOTED_RE.findall(issue.message)
                    key = labels[0] if labels else None
                    if key in flipped:
                        continue
                    if self._repair_dim_inside_part(issue):
                        flipped.add(key)
                        changed = True
                elif issue.code == "annotation_overlap":
                    changed |= self._repair_overlap(issue)
            if not changed:
                break
            if len(self.lint()) > len(before):
                # The repairs net-worsened the sheet — undo this pass and stop.
                self.items[:] = snap_annotations
                self._named.clear()
                self._named.update(snap_named)
                break
        return self

    # -- output ---------------------------------------------------------------
    def lint(self):
        """Lint all annotations against all views; returns the list of issues.

        When :attr:`part` is set, also runs :func:`lint_feature_coverage`.
        Build-time drops recorded via :meth:`_record_build_issue` are included.
        """
        set_page(self.page_w, self.page_h, margin=10)
        view_shapes = [vis for vis, _ in self.views.values()]
        # Most annotations are at sheet scale, but a non-sheet-scale view (the
        # enlarged detail view, #42) tags its dims with `_dw_scale`. Lint each
        # scale group with its own drawing_scale so label-vs-measured is correct
        # per view. The common single-scale case is byte-identical to before.
        by_scale: dict = {}
        for ann in self.items:
            by_scale.setdefault(getattr(ann, "_dw_scale", self.scale), []).append(ann)
        if len(by_scale) <= 1:
            issues = lint_drawing(
                self.items,
                drawing_scale=self.scale,
                view_shapes=view_shapes,
                view_edge_cache=self._view_edge_cache,
            )
        else:
            issues = []
            for _scale, _anns in by_scale.items():
                issues += lint_drawing(
                    _anns,
                    drawing_scale=_scale,
                    view_shapes=view_shapes,
                    view_edge_cache=self._view_edge_cache,
                )
        if self.part is not None:
            if self._cyl_cache is None:
                self._cyl_cache = analyse_cylinders(self.part)
            issues += lint_feature_coverage(
                self.part,
                self.items,
                cyls=self._cyl_cache,
                exclude=self._coverage.dropped_diams,
                assembly=self.assembly,
            )
        issues += list(self._build_issues)
        # Attach a ready-to-paste fix snippet where one is computable (#29).
        # str | None — None when no concrete repair can be inferred.
        for i in issues:
            i.suggestion = _suggest_fix(i, self)
        return issues

    def lint_summary(self) -> dict:
        """Aggregate :meth:`lint` into a JSON-friendly quality summary.

        Gives a non-interactive caller (a script, or an LLM via the API) a
        single signal to gate and optimise on without rendering the SVG:

        - ``passed`` — no error-severity issues;
        - ``score`` — coarse 0–1 quality heuristic (see ``_SCORE_*``);
        - ``errors`` / ``warnings`` / ``infos`` — counts by severity;
        - ``by_code`` — per-check counts;
        - ``geometry_issues`` — count of standards/geometry-correctness issues
          as opposed to pure layout (see ``_GEOMETRY_AWARE_CODES``);
        - ``issues`` — the full list, each as a plain dict.
        """
        issues = self.lint()
        errors = sum(1 for i in issues if i.severity == "error")
        warnings = sum(1 for i in issues if i.severity == "warning")
        infos = sum(1 for i in issues if i.severity == "info")
        by_code: dict[str, int] = {}
        for i in issues:
            by_code[i.code] = by_code.get(i.code, 0) + 1
        score = max(
            0.0,
            1.0 - errors * _SCORE_ERROR_PENALTY - warnings * _SCORE_WARNING_PENALTY,
        )
        return {
            "passed": errors == 0,
            "score": score,
            "errors": errors,
            "warnings": warnings,
            "infos": infos,
            "by_code": by_code,
            "geometry_issues": sum(1 for i in issues if i.code in _GEOMETRY_AWARE_CODES),
            "issues": [
                {
                    "severity": i.severity,
                    "code": i.code,
                    "message": i.message,
                    "location": i.location,
                    # Omit suggestion when None to keep the JSON non-breaking (#29).
                    **(
                        {"suggestion": s}
                        if (s := getattr(i, "suggestion", None)) is not None
                        else {}
                    ),
                }
                for i in issues
            ],
        }

    def export(self, out=None):
        """Lint, then write SVG and DXF. Returns ``(svg_path, dxf_path)``."""
        out = out if out is not None else self.out
        for _ext in (".svg", ".dxf"):
            if out.endswith(_ext):
                out = out[: -len(_ext)]
                break

        issues = self.lint()
        if issues:
            _log.warning("Lint issues:")
            for iss in issues:
                _log.warning("  [%s] %s: %s", iss.severity, iss.code, iss.message)
        else:
            _log.info("Lint: OK")

        blk = Color(0, 0, 0)
        grey = Color(0.5, 0.5, 0.5)
        blue = Color(0, 0.2, 0.7)

        svg = ExportSVG(margin=10)
        svg.add_layer("part", line_color=blk, line_weight=0.5)
        svg.add_layer("hidden", line_color=grey, line_weight=0.25, line_type=LineType.HIDDEN)
        svg.add_layer("dims", line_color=blue, fill_color=blue, line_weight=0.05)
        self._add_shapes(svg)
        svg_path = out + ".svg"
        svg.write(svg_path)
        fix_svg_page_size(svg_path, self.page_w, self.page_h)
        n_arcs = sanitize_svg_arcs(svg_path)
        if n_arcs:
            _log.info("Rewrote %d degenerate (near-zero-radius) arc(s) as line segments", n_arcs)
        link_rect = getattr(self, "_draftwright_link_rect", None)
        if link_rect is not None:
            add_svg_hyperlink(svg_path, link_rect)
        add_svg_metadata(svg_path)
        _log.info("SVG → %s", svg_path)

        dxf = ExportDXF()
        dxf.add_layer("part", line_weight=0.5)
        dxf.add_layer("hidden", line_weight=0.25)
        dxf.add_layer("dims", line_weight=0.05)
        self._add_shapes(dxf)
        set_dxf_metadata(dxf)
        dxf_path = out + ".dxf"
        dxf.write(dxf_path)
        _log.info("DXF → %s", dxf_path)

        self.svg_path = svg_path
        self.dxf_path = dxf_path
        return svg_path, dxf_path

    def export_pdf(self, out=None) -> str:
        """Write a PDF rendered from the SVG.  Requires ``cairosvg`` (install with
        ``pip install draftwright[pdf]``).  Calls :meth:`export` first if the SVG
        hasn't been written yet.  Returns the PDF path.

        The PDF carries a 'generated by draftwright' Creator metadata field and,
        over the title-block URL row, a clickable hyperlink to the project (a
        real PDF link annotation — the SVG ``<a>`` element is not understood by
        the PDF renderer, so it is added here via cairo)."""
        try:
            import cairosvg  # noqa: F401  (import-guard; the real work is in _render_pdf)
        except ImportError as exc:
            raise ImportError(
                "PDF export requires cairosvg. Install it with:  pip install draftwright[pdf]"
            ) from exc

        svg_path: str
        if not hasattr(self, "svg_path") or self.svg_path is None:
            svg_path, _ = self.export(out=out)
        else:
            svg_path = self.svg_path
        pdf_path = svg_path[:-4] + ".pdf" if svg_path.endswith(".svg") else svg_path + ".pdf"
        _render_pdf(svg_path, pdf_path, self.page_h, getattr(self, "_draftwright_link_rect", None))
        _log.info("PDF → %s", pdf_path)
        return pdf_path

    def _add_shapes(self, exporter):
        """Add every view layer and annotation to *exporter* with error context."""
        for name, (vis, hid) in self.views.items():
            _export_shape(exporter, vis, "part", f"view {name!r}")
            if hid:
                _export_shape(exporter, hid, "hidden", f"view {name!r}")
        for ann in self.items:
            label = getattr(ann, "label", "") or type(ann).__name__
            _export_shape(exporter, ann, "dims", f"annotation {label!r}")


# 1D strip placement now lives in draftwright.layout (ADR 0003 phase 1, #79).
# These aliases keep the existing axis-specific callers and their tests working
# while the primitive is axis-neutral; later phases route through LayoutSolver.


def _bbox_within(bb, region, tol: float = 0.5) -> bool:
    """True if (min_x, min_y, max_x, max_y) *bb* fits inside *region* within *tol*."""
    return (
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
    camera = (la[0] + off, la[1] + off, la[2] + off)
    dwg.add_view(
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
    # different scale than the sheet.
    dwg._coords["iso"] = ViewCoordinates.from_viewport(
        camera, (0, 0, 1), la, a.ISO_X, a.ISO_Y, a.cx, a.cy, a.cz, scale
    )


def _fit_iso_view(dwg, a: Analysis, annotate: bool = True):
    """Scale the iso view to fill its page zone, captioning it NTS when the
    scale differs from sheet scale.  Pass ``annotate=False`` to suppress the
    NTS note (used when ``auto_dims=False``).

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
        dwg.add(
            Note(
                "ISO VIEW (NTS)",
                (a.ISO_X, max(bb[1] - 2 * font, a.margin + font)),
                dwg.draft,
            ),
            "note_iso_nts",
        )
    _log.info("Iso view scaled to %g× sheet scale%s", factor, " (NTS)" if annotate else "")


# A view centre must move by more than this (mm) for the measure-and-repack
# pass to re-assemble.  Below it, the estimate already matched the measured
# footprint and pass 1 stands (the common, non-ballooned case).
_REPACK_TOL = 0.75


def _assemble(a, out, assembly, detail_view, auto_dims):
    """Project the 4 views for analysis *a*, run the automatic annotation
    passes, and fit the iso.  This is pass 1 of :func:`build_drawing`; with a
    repacked analysis it is also pass 2 of the measure-and-repack loop (#121)."""
    cxs, cys, czs = a.cx * a.SCALE, a.cy * a.SCALE, a.cz * a.SCALE
    dist = a.bbox_max * a.SCALE + 100

    dwg = Drawing(
        scale=a.SCALE,
        page_w=a.PAGE_W,
        page_h=a.PAGE_H,
        tb_w=a.TB_W,
        draft=draft_preset(font_size=_FONT_SIZE, decimal_precision=1, font_path=PLEX_MONO),
        look_at=(cxs, cys, czs),
        dist=dist,
        centroid=(a.cx, a.cy, a.cz),
        out=out,
        part=a.part,
        cyls=a.cyls,
        assembly=assembly,
    )
    dwg._analysis = a  # expose analysis namespace for testing and future strip access

    part_s = a.part.scale(a.SCALE)
    dwg.add_view("front", part_s, (cxs, cys - dist, czs), (0, 0, 1), (a.FV_X, a.FV_Y), scaled=True)
    dwg.add_view("plan", part_s, (cxs, cys, czs + dist), (0, 1, 0), (a.PV_X, a.PV_Y), scaled=True)
    dwg.add_view("side", part_s, (cxs + dist, cys, czs), (0, 0, 1), (a.SV_X, a.SV_Y), scaled=True)
    _project_iso(dwg, a, a.SCALE, shape_s=part_s)

    if auto_dims:
        # Snapshot outer_limits before _auto_annotate tightens them against the
        # initial (possibly overflowing) iso.  After _fit_iso_view rescales the
        # iso we restore all three right strips to min(original, final_iso_x_limit)
        # so each strip reflects actual final geometry, not the transient state.
        _fv_ol = a.fv_zones.right.outer_limit
        _pv_ol = a.pv_zones.right.outer_limit
        _sv_ol = a.sv_zones.right.outer_limit
        _auto_annotate(dwg, a, detail_view=detail_view)
        _fit_iso_view(dwg, a)
        _ix0, _iy0, _, _iy1 = _iso_bbox(dwg)
        _final_iso_x_lim = _ix0 - 4
        a.fv_zones.right.outer_limit = min(_fv_ol, _final_iso_x_lim)
        a.pv_zones.right.outer_limit = min(_pv_ol, _final_iso_x_lim)
        # Only re-cap the SV right strip when the iso shares its y-range (see the
        # matching guard in _auto_annotate); otherwise restore its full width.
        if (a.SV_Y - a.fv_hh) < _iy1 and _iy0 < (a.SV_Y + a.fv_hh):
            a.sv_zones.right.outer_limit = min(_sv_ol, _final_iso_x_lim)
        else:
            a.sv_zones.right.outer_limit = _sv_ol
    else:
        _fit_iso_view(dwg, a, annotate=False)
        _add_title_block(dwg, a)
    return dwg


def _repack_candidates(a, scale, page):
    """The (scale, page_w, page_h, tb_w) candidates the repack may choose from,
    mirroring :func:`choose_scale`: a user-fixed scale and/or page is honoured;
    otherwise the auto ladder (smallest legible sheet first) is searched."""
    if scale is not None and page is not None:
        pw, ph, tb = _parse_page(page)
        return [(float(scale), pw, ph, tb)]
    if page is not None:
        pw, ph, tb = _parse_page(page)
        return [(s, pw, ph, tb) for s in _SCALES]
    if scale is not None:
        return [(float(scale), pw, ph, _tb_width(pw)) for pw, ph in _PAGE_SIZES.values()]
    # Auto ladder, but floored at pass 1's chosen sheet: the measured blocks are
    # never smaller than the estimate that pass 1 already rejected the earlier
    # rungs against, and the repack's .fits is more permissive than choose_scale's
    # row model — so without this floor the repack could pick a *smaller* sheet
    # than pass 1 and make things worse (#121). Start the search at pass 1's rung.
    start = next(
        (
            i
            for i, (s, pw, ph, _tb) in enumerate(_LADDER)
            if s == a.SCALE and pw == a.PAGE_W and ph == a.PAGE_H
        ),
        0,
    )
    return list(_LADDER[start:])


def _repack(a, dwg, out, assembly, detail_view, scale=None, page=None):
    """Measure the laid-out drawing's *real* per-view annotation footprints and,
    when a view collides across views, pack the blocks disjoint — escalating the
    sheet/scale until the packed layout fits — then re-assemble (#121, ADR 0004 —
    "lay out, don't predict"; the (scale, page) choice is the outer search whose
    fitness is *do the packed disjoint blocks fit*).

    Returns ``(a2, dwg2)`` for the repacked drawing, or ``None`` when pass 1 has
    no cross-view overlap AND nothing overflows the drawable (the common case — a
    clean sheet is left exactly as placed, so well-estimated parts stay
    byte-identical) or when the repack would change nothing (same sheet/scale and
    no view actually moves).
    """
    if _cross_view_overlaps(dwg, a) == 0 and not _annotations_out_of_bounds(dwg, a):
        return None
    blocks = _measure_blocks(dwg, a)

    def _geom(cand):
        s, pw, ph, tb = cand
        return _layout_geometry(
            a.x_size, a.y_size, a.z_size, s, pw, ph, tb, None, 0, blocks=blocks
        )

    candidates = _repack_candidates(a, scale, page)
    fit = next(((c, gg) for c in candidates if (gg := _geom(c)).fits), None)
    if fit is None:
        # Nothing fits — keep the largest candidate and let lint report the
        # overflow (mirrors choose_scale's fallback rather than crashing).
        chosen = candidates[-1]
        g = _geom(chosen)
        _log.warning(
            "measure-repack: no standard sheet fits the measured layout; using %s", chosen
        )
    else:
        chosen, g = fit
    s, pw, ph, tb = chosen
    moved = max(
        abs(g.FV_X - a.FV_X),
        abs(g.FV_Y - a.FV_Y),
        abs(g.PV_X - a.PV_X),
        abs(g.PV_Y - a.PV_Y),
        abs(g.SV_X - a.SV_X),
        abs(g.SV_Y - a.SV_Y),
    )
    if s == a.SCALE and pw == a.PAGE_W and ph == a.PAGE_H and moved < _REPACK_TOL:
        return None
    fv_zones, pv_zones, sv_zones = _build_zones(g, a.margin, ph)
    a2 = replace(
        a,
        SCALE=s,
        PAGE_W=pw,
        PAGE_H=ph,
        TB_W=tb,
        x_offset=g.x_offset,
        FV_X=g.FV_X,
        FV_Y=g.FV_Y,
        PV_X=g.PV_X,
        PV_Y=g.PV_Y,
        SV_X=g.SV_X,
        SV_Y=g.SV_Y,
        fv_hw=g.fv_hw,
        fv_hh=g.fv_hh,
        pv_hh=g.pv_hh,
        sv_hw=g.sv_hw,
        sv_right=g.sv_right,
        iso_right_limit=g.iso_right,
        ISO_X=g.ISO_X,
        ISO_Y=g.ISO_Y,
        iso_left_limit=g.iso_left,
        iso_bottom_limit=g.iso_bottom,
        iso_top_limit=g.iso_top,
        proj=_Projector(
            fv_x=g.FV_X,
            fv_y=g.FV_Y,
            sv_x=g.SV_X,
            sv_y=g.SV_Y,
            pv_x=g.PV_X,
            pv_y=g.PV_Y,
            cx=a.cx,
            cy=a.cy,
            cz=a.cz,
            scale=s,
        ),
        fv_zones=fv_zones,
        pv_zones=pv_zones,
        sv_zones=sv_zones,
    )
    dwg2 = _assemble(a2, out, assembly, detail_view, auto_dims=True)
    return a2, dwg2


def build_drawing(
    step_file: str | Path | Shape,
    out: str | None = None,
    title: str | None = None,
    number: str = "DWG-001",
    tolerance: str = "ISO 2768-m",
    drawn_by: str = "",
    scale: float | None = None,
    page: str | tuple | None = None,
    auto_dims: bool = True,
    detail_view: bool = False,
    pmi: Literal["off", "report", "annotate"] = "off",
    repair: bool = True,
    assembly: bool | None = None,
) -> Drawing:
    """Build a customisable 4-view :class:`Drawing` without exporting it.

    Same arguments as :func:`make_drawing`, but returns the live :class:`Drawing`
    so you can add or remove annotations and add section/auxiliary views before
    calling :meth:`Drawing.export`. ``make_drawing(...)`` is exactly
    ``build_drawing(...).export()``.

    Args:
        auto_dims: pass ``False`` to skip the automatic dimensions,
            centrelines, and leaders (#74) — the automatic set assumes a
            turned part and is wrong for prismatic geometry. Views, scale,
            page, and title block are still produced; add your own
            annotations before export. (Annotations added by the default can
            also be removed wholesale with :meth:`Drawing.clear_annotations`.)
        repair: run the bounded lint→repair loop (:meth:`Drawing.repair`) after
            placement to fix mechanically-clear violations (a dim on the wrong
            side, two overlapping labels). Default ``True``; a no-op on a clean
            sheet. Pass ``False`` to inspect the raw greedy placement (#30).
        assembly: severity of the feature-coverage lint for a general-arrangement
            drawing. ``None`` (default) auto-detects — a multi-solid part is an
            assembly, whose per-part bores are reported at ``info`` rather than
            ``warning`` (a GA omits them by design). Force with ``True``/``False``
            (#69).

    Returns:
        A :class:`Drawing` with the standard front/plan/side/iso views projected
        and the automatic dimensions + title block already added.
    """
    stem = "drawing" if isinstance(step_file, Shape) else Path(step_file).stem
    out = out or stem
    for _ext in (".svg", ".dxf"):
        if out.endswith(_ext):
            out = out[: -len(_ext)]
            break
    title = title or stem.replace("_", " ").upper()

    a = _analyse(
        step_file, title, number, tolerance, drawn_by, out, scale=scale, page=page, pmi=pmi
    )

    # Pass 1: place + annotate from the estimated layout, then measure the real
    # per-view footprints and re-pack the blocks disjoint if a view actually
    # moves (#121, ADR 0004 — "lay out, don't predict").  Non-ballooned parts
    # measure ≈ estimate, so they skip pass 2 and stand byte-identical.
    dwg = _assemble(a, out, assembly, detail_view, auto_dims)
    if auto_dims:
        repacked = _repack(a, dwg, out, assembly, detail_view, scale=scale, page=page)
        if repacked is not None:
            a, dwg = repacked
    if repair:
        # Close the loop on the greedy placement: re-place dims behind any
        # mechanically-clear violations (overlap, wrong-side) and re-lint (#30).
        # A no-op on a clean sheet, so default-on costs nothing when there is
        # nothing to fix.
        dwg.repair()
    return dwg


# ---------------------------------------------------------------------------
# Direct export (SVG + DXF)
# ---------------------------------------------------------------------------


def make_drawing(
    step_file: str | Path | Shape,
    out: str | None = None,
    title: str | None = None,
    number: str = "DWG-001",
    tolerance: str = "ISO 2768-m",
    drawn_by: str = "",
    scale: float | None = None,
    page: str | tuple | None = None,
    auto_dims: bool = True,
    detail_view: bool = False,
    pmi: Literal["off", "report", "annotate"] = "off",
    assembly: bool | None = None,
) -> tuple[str, str]:
    """Generate a 4-view technical drawing from a STEP file or build123d object.

    Args:
        step_file: Path to a STEP/STP file, or a build123d ``Shape`` (e.g. a
            ``Part``, ``Solid``, or ``Compound``) to draw directly.
        out: Output path stem (default: input filename stem, or ``"drawing"``
            when a build123d object is passed).
        title: Part title for the title block (default: stem uppercased).
        number: Drawing number (e.g. ``"DWG-042"``).
        tolerance: General tolerance string (e.g. ``"ISO 2768-m"``).
        drawn_by: Designer name for the title block.
        scale: Drawing-scale override (e.g. ``5`` for 5:1, ``0.5`` for 1:2).
            Default: chosen automatically by :func:`choose_scale`.
        page: Page-size override — an ISO name (``"A3"``), ``"WIDTHxHEIGHT"``
            in mm, or a ``(width, height)`` tuple. Default: chosen
            automatically by :func:`choose_scale`.
        auto_dims: pass ``False`` to skip the automatic dimensions,
            centrelines, and leaders (#74) — views, scale, page, and title
            block only.

    Returns:
        Tuple of ``(svg_path, dxf_path)`` for the generated files.

    This is a thin wrapper: ``make_drawing(...)`` is ``build_drawing(...).export()``.
    To add or remove annotations or add section/auxiliary views before export,
    call :func:`build_drawing` and use the returned :class:`Drawing`.
    """
    return build_drawing(
        step_file,
        out=out,
        title=title,
        number=number,
        tolerance=tolerance,
        drawn_by=drawn_by,
        scale=scale,
        page=page,
        auto_dims=auto_dims,
        detail_view=detail_view,
        pmi=pmi,
        assembly=assembly,
    ).export()


# ---------------------------------------------------------------------------
# Script generation (Cog-enabled .py output)
# ---------------------------------------------------------------------------


def _write_script(a: Analysis) -> str:
    """Write an editable script at ``a.out + '.py'`` that calls make_drawing()."""
    py_path = a.out + ".py"
    py_name = Path(py_path).name

    cog_output = "\n".join(
        [
            f"STEP_FILE = {a.step_file!r}",
            f"TITLE = {a.title!r}",
            f"NUMBER = {a.number!r}",
            f"TOLERANCE = {a.tolerance!r}",
            f"DRAWN_BY = {a.drawn_by!r}",
        ]
    )

    cog_block = (
        "# [[[cog\n"
        "# ── Config: edit these, then run `cog -r <script>.py` to update ────────────\n"
        f"_STEP_FILE = {a.step_file!r}\n"
        f"_TITLE     = {a.title!r}\n"
        f"_NUMBER    = {a.number!r}\n"
        f"_TOLERANCE = {a.tolerance!r}\n"
        f"_DRAWN_BY  = {a.drawn_by!r}\n"
        "try:\n"
        "    cog  # NameError → not under cog\n"
        "    for _k, _v in [\n"
        "        ('STEP_FILE', repr(_STEP_FILE)), ('TITLE', repr(_TITLE)),\n"
        "        ('NUMBER', repr(_NUMBER)), ('TOLERANCE', repr(_TOLERANCE)),\n"
        "        ('DRAWN_BY', repr(_DRAWN_BY)),\n"
        "    ]:\n"
        "        cog.outl(f'{_k} = {_v}')\n"
        "except NameError:\n"
        "    pass\n"
        "# ]]]\n"
        f"{cog_output}\n"
        "# [[[end]]]"
    )

    _tq = '"""'
    _safe_doc_title = a.title.replace(_tq, "'''")
    _safe_doc_number = a.number.replace(_tq, "'''")
    header = (
        f"#!/usr/bin/env python3\n"
        f'"""\n'
        f"{_safe_doc_title} — Technical drawing ({_safe_doc_number}).\n"
        f"\n"
        f"Auto-generated by make-drawing. Edit freely.\n"
        f"To update metadata: edit _STEP_FILE / _TITLE / etc. in the cog block, then run:\n"
        f"  cog -r {py_name}   (pip install cogapp)\n"
        f"\n"
        f"Run:  uv run python {py_name}\n"
        f'"""\n'
        f"import os as _os\n"
        f"from draftwright import build_drawing\n"
        f"\n"
        f"# ── Config (auto-updated by cog) ──────────────────────────────────────────────\n"
    )

    run_section = (
        "\n"
        "# ── Build drawing (standard 4-view layout + automatic dimensions) ─────────────\n"
        "_stem = _os.path.splitext(__file__)[0]\n"
        "dwg = build_drawing(\n"
        "    STEP_FILE,\n"
        "    out=_stem,\n"
        "    title=TITLE,\n"
        "    number=NUMBER,\n"
        "    tolerance=TOLERANCE,\n"
        "    drawn_by=DRAWN_BY,\n"
        ")\n"
        "\n"
        "# ── Customise here — runs BEFORE export, so edits land in the output ───────────\n"
        "# Prefer domain edits (place_dim / features) over page mechanics (at / Leader);\n"
        "# the engine places annotations automatically — say WHAT, not WHERE.\n"
        "# dwg.features(view)       → detected features → [FeatureInfo(.diameter .count .page_pos)]\n"
        "# dwg.place_dim(p1, p2, side, view, dwg.draft, name=…)  → add a dimension, auto-placed\n"
        "# dwg.annotations()        → {name: type} of every named annotation\n"
        "# dwg.get_annotation(name) → the named annotation object, or None\n"
        "# dwg.remove(name) / dwg.add(obj, name)\n"
        "# dwg.pin(name) / dwg.unpin(name)  → fix a placement so repair never moves it\n"
        "# dwg.lint_summary()       → {passed, score, by_code, issues:[…suggestion]}\n"
        "# dwg.repair()             → auto-fix mechanically-fixable lint (never worsens)\n"
        "# dwg.add_view(name, shape, camera, up, position)  → section / auxiliary view\n"
        "# dwg.items / dwg.views / dwg.at(view,x,y,z) / dwg.view_bounds(view)  → low-level escape\n"
        "# Example — add a linear dim (place_dim auto-stacks; endpoints via dwg.at):\n"
        "#   p1, p2 = dwg.at('front', 0, 0, 0), dwg.at('front', 40, 0, 0)\n"
        "#   dwg.place_dim(p1, p2, 'above', 'front', dwg.draft, name='dim_len')\n"
        "\n"
        "# ── Export ────────────────────────────────────────────────────────────────────\n"
        "svg_path, dxf_path = dwg.export(_stem)\n"
        'print(f"SVG \\u2192 {svg_path}")\n'
        'print(f"DXF \\u2192 {dxf_path}")\n'
    )

    content = header + cog_block + run_section
    Path(py_path).write_text(content, encoding="utf-8")
    _log.info("Script → %s", py_path)
    return py_path


def generate_script(
    step_file: str,
    out: str | None = None,
    title: str | None = None,
    number: str = "DWG-001",
    tolerance: str = "ISO 2768-m",
    drawn_by: str = "",
    pmi: Literal["off", "report", "annotate"] = "off",
) -> str:
    """Generate an editable Cog-enabled drawing script from a STEP file.

    Returns:
        Path to the generated ``.py`` file.
    """
    if isinstance(step_file, Shape):
        raise TypeError(
            "generate_script() requires a STEP file path — the generated script "
            "reloads geometry from disk and cannot embed a live build123d object. "
            "Use make_drawing() directly to draw an in-memory object."
        )
    stem = Path(step_file).stem
    out = out or stem
    for _ext in (".py", ".svg", ".dxf"):
        if out.endswith(_ext):
            out = out[: -len(_ext)]
            break
    title = title or stem.replace("_", " ").upper()
    a = _analyse(step_file, title, number, tolerance, drawn_by, out, pmi=pmi)
    return _write_script(a)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli():
    ap = argparse.ArgumentParser(
        description="Zero-AI STEP → technical drawing (SVG + DXF, or editable .py script)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("step_file", help="Input STEP file (.step / .stp)")
    ap.add_argument("--out", default=None, help="Output prefix (default: input stem)")
    ap.add_argument("--title", default=None, help="Part title for title block")
    ap.add_argument("--number", default="DWG-001", help="Drawing number")
    ap.add_argument("--tolerance", default="ISO 2768-m", help="General tolerance")
    ap.add_argument("--drawn-by", default="", help="Designer name")
    ap.add_argument(
        "--scale",
        type=float,
        default=None,
        help="Drawing-scale override, e.g. 5 for 5:1 or 0.5 for 1:2 (default: auto)",
    )
    ap.add_argument(
        "--page",
        default=None,
        help="Page-size override: A4..A0 or WIDTHxHEIGHT in mm, e.g. 420x297 (default: auto)",
    )
    ap.add_argument(
        "--script",
        action="store_true",
        help="Write an editable .py drawing script instead of SVG+DXF",
    )
    ap.add_argument(
        "--pmi",
        default="off",
        choices=["off", "report", "annotate"],
        help=(
            "AP242 PMI handling: 'off' (default) — ignore; "
            "'report' — log extracted PMI without annotating; "
            "'annotate' — add PMI-derived dimensions to the drawing"
        ),
    )
    ap.add_argument(
        "--pdf",
        action="store_true",
        help="Also write a PDF (requires draftwright[pdf] / cairosvg)",
    )
    ap.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show detailed progress (default: warnings and errors only)",
    )
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(message)s",
    )

    if args.script and (args.scale is not None or args.page is not None):
        ap.error("--scale/--page only apply to direct output; edit the generated script instead")

    if args.script:
        py_path = generate_script(
            step_file=args.step_file,
            out=args.out,
            title=args.title,
            number=args.number,
            tolerance=args.tolerance,
            drawn_by=args.drawn_by,
            pmi=args.pmi,
        )
        print(py_path)
    else:
        dwg = build_drawing(
            step_file=args.step_file,
            out=args.out,
            title=args.title,
            number=args.number,
            tolerance=args.tolerance,
            drawn_by=args.drawn_by,
            scale=args.scale,
            page=args.page,
            pmi=args.pmi,
        )
        svg_path, dxf_path = dwg.export()
        print(svg_path)
        print(dxf_path)
        if args.pdf:
            print(dwg.export_pdf())


if __name__ == "__main__":
    _cli()
