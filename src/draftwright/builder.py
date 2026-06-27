"""Build orchestration (#138 / ADR 0005, P6).

The pipeline driver: `build_drawing` runs analysis -> assemble (project +
annotate + fit) -> measure-and-repack -> returns the `Drawing`; `make_drawing`
wraps it with export; plus the editable-script generator and the CLI. Imports
`drawing` (the result object), `analysis`, the annotation orchestrator, and the
stage modules -- never make_drawing -- so the graph stays a DAG.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import replace
from pathlib import Path
from typing import Literal

from build123d import (
    Shape,
)
from build123d_drafting.helpers import (
    draft_preset,
)

from draftwright._core import (
    _FONT_SIZE,
    _LADDER,
    _PAGE_SIZES,
    _SCALES,
    Analysis,
    _add_title_block,
    _iso_bbox,
    _log,
    _parse_page,
    _Projector,
    _tb_width,
)
from draftwright.analysis import _analyse
from draftwright.annotate import _auto_annotate
from draftwright.drawing import Drawing
from draftwright.fonts import PLEX_MONO
from draftwright.projection import (
    _fit_iso_view,
    _project_iso,
)
from draftwright.sheet import (
    ViewBlock,
    _attribute_annotations,
    _build_zones,
    _layout_geometry,
    _view_geom,
)

_TB_W = 150.0
# Minimum acceptable projected view dimension (page-mm).  Below this, annotation
# geometry (leader wires, centre marks, bore callout elbows) can degenerate and
# cause OCCT Standard_DomainError / SIGABRT (#129).


# ---------------------------------------------------------------------------
# SVG post-processing
# ---------------------------------------------------------------------------


# Equidistance tolerance (page-mm) for accepting a sampled silhouette spline as
# a circle about a known projected axis.  Loose enough to swallow HLR's spline
# approximation error, tight enough not to round a genuinely off-axis curve.

_REPACK_TOL = 0.75


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


# ---------------------------------------------------------------------------
# Drawing builder (composable; make_drawing == build_drawing + export)
# ---------------------------------------------------------------------------


def _assemble(a, out, assembly, detail_view, auto_dims) -> Drawing:
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
