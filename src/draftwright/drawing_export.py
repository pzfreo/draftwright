"""Drawing export orchestration over explicit Drawing operations and public result state."""

from __future__ import annotations

import contextlib
import os
import tempfile
import warnings

from build123d import Color, ExportSVG, LineType

from draftwright._core import _log
from draftwright.export import (
    _DraftwrightDXF,
    _export_shape,
    _render_pdf,
    _render_png,
    add_svg_hyperlink,
    add_svg_metadata,
    canonicalize_svg,
    fix_svg_page_size,
    highlight_svg_annotation,
    sanitize_svg_arcs,
    set_dxf_metadata,
)
from draftwright.export import (
    write_dxf as write_dxf_file,
)


def write_svg(
    out: str, *, page_w, page_h, add_shapes, title_block, reproducible: bool = True
) -> str:
    """Write the SVG (part/hidden/dims layers, page-size fix, arc sanitise, hyperlink +
    metadata) and return its path. The PDF and PNG renders both read this SVG.

    *reproducible* settles the element order so two runs write the same bytes;
    see :func:`export.canonicalize_svg`. Off, this is what it always was."""
    blk = Color(0, 0, 0)
    grey = Color(0.5, 0.5, 0.5)
    blue = Color(0, 0.2, 0.7)
    svg_exp = ExportSVG(margin=10)
    svg_exp.add_layer("part", line_color=blk, line_weight=0.5)
    svg_exp.add_layer("hidden", line_color=grey, line_weight=0.25, line_type=LineType.HIDDEN)
    svg_exp.add_layer("dims", line_color=blue, fill_color=blue, line_weight=0.05)
    add_shapes(svg_exp)
    svg_path = out + ".svg"
    svg_exp.write(svg_path)
    if reproducible:
        # Before the passes below read it back: they rewrite what is there,
        # this settles what order it is in. See canonicalize_svg().
        canonicalize_svg(svg_path)
    fix_svg_page_size(svg_path, page_w, page_h)
    n_arcs = sanitize_svg_arcs(svg_path)
    if n_arcs:
        _log.info("Rewrote %d degenerate (near-zero-radius) arc(s) as line segments", n_arcs)
    link_rect = getattr(title_block(), "draftwright_link_rect", None)
    if link_rect is not None:
        add_svg_hyperlink(svg_path, link_rect)
    add_svg_metadata(svg_path)
    _log.info("SVG → %s", svg_path)
    return svg_path


def write_dxf(out: str, *, page_w, page_h, add_shapes, reproducible: bool = True) -> str:
    """Write the DXF (part/hidden/dims layers + metadata) and return its path.

    *reproducible* orders the entities and pins the metadata ezdxf stamps from
    the clock, so two runs write the same bytes. It costs about a third of the
    export time again — see :func:`export._elements`."""
    dxf_exp = _DraftwrightDXF()
    dxf_exp.add_layer("part", line_weight=0.5)
    dxf_exp.add_layer("hidden", line_weight=0.25)
    dxf_exp.add_layer("dims", line_weight=0.05)
    add_shapes(dxf_exp, ordered=reproducible)
    set_dxf_metadata(dxf_exp)
    dxf_path = out + ".dxf"
    # #602: skip ExportDXF.write's O(entities) zoom.extents pass — the page window is known.
    write_dxf_file(dxf_exp, dxf_path, page_w, page_h, reproducible=reproducible)
    _log.info("DXF → %s", dxf_path)
    return dxf_path


def export_drawing(
    drawing,
    out=None,
    *,
    formats=None,
    svg=None,
    dxf=None,
    dpi: int = 150,
    reproducible: bool | None = None,
    supported_formats,
    lint_and_log,
    write_svg,
    write_dxf,
    pdf_text_runs,
) -> dict[str, str] | tuple[str | None, str | None]:
    """Lint, then write the requested output *formats*; return ``{format: path}``.

    *formats* is a format name or an iterable from ``("svg", "dxf", "pdf", "png")``. PDF
    renders from the SVG and PNG from the PDF, so the SVG/PDF are written as intermediates
    and removed when not themselves requested. *dpi* sets the PNG raster resolution.

    Omitting *formats* — or passing ``None``, which is indistinguishable from omitting it
    — does **not** default to ``("pdf",)``; that is :meth:`Sheet.export`'s default. Here it
    selects the deprecated legacy path below, which writes SVG + DXF and returns a tuple.

    Legacy (deprecated in 0.3.1, **removed in 0.5.0**): the boolean ``svg=``/``dxf=``
    keywords — and calling ``export()`` with no ``formats`` — select those two vector
    formats and return the old ``(svg_path, dxf_path)`` tuple. Prefer ``formats=[...]``
    (the dict API); ``export_pdf`` is likewise superseded by ``export(formats=("pdf",))``.

    Both legacy shapes now **warn** (#987). They were listed under "Deprecated" in the
    v0.3.1 changelog and then said nothing at runtime for four minor releases, which made
    the planned 0.5.0 removal a silent break — and invisible to
    ``tests/test_deprecation_dates.py``, which can only scan things that warn. A
    deprecation nobody is warned about is documentation, not a deprecation.

    *reproducible* makes two exports of one drawing byte-identical — the element
    order is settled and the metadata the exporters take from the clock is pinned,
    so a written drawing can be diffed or checksummed to see whether its content
    actually changed. ``None`` (the default) uses :attr:`reproducible`, which
    :func:`~draftwright.build_drawing` sets and which is ``True`` unless the
    caller opts out: a file that changes between runs cannot be diffed,
    checksummed or cached, and that is worth more than the ordering costs on a
    part (+2.0% of a whole CTC-01 job). The cost grows with part count, so a
    part-heavy sheet may want ``False`` — see :func:`draftwright.export._elements`.
    Passing the keyword here overrides the drawing's default for this call only.
    """
    drawing.finalize()  # #426: drain any recorded intents before export (no-op if none)
    # An explicit keyword wins; otherwise the drawing's own default (build_drawing's).
    reproducible = drawing.reproducible if reproducible is None else reproducible
    out = out if out is not None else drawing.out
    for _ext in supported_formats:
        if out.endswith("." + _ext):
            out = out[: -(len(_ext) + 1)]
            break
    # Normalise ONCE, here, before anything reads it. `formats` may be a one-shot iterable,
    # and the mixed-API warning below used to build its message with `tuple(formats)` —
    # which consumed a generator, leaving the export loop nothing to iterate: it warned
    # that it would write SVG+DXF and then wrote nothing. Normalising early
    # also makes the message say `('svg',)` rather than `('s', 'v', 'g')` for `formats="svg"`.
    want: list[str] | None = None
    if formats is not None:
        want = [formats.lower()] if isinstance(formats, str) else [f.lower() for f in formats]
        # Validate BEFORE the deprecation warning below. A caller who mistyped a format has
        # a broken call, not a deprecated one — and under `-W error` a warning raised first
        # would surface the deprecation instead of the typo that actually stopped the
        # export. Report the fault that matters.
        unknown = [f for f in want if f not in supported_formats]
        if unknown:
            raise ValueError(
                f"unknown export format(s) {unknown}; choose from {supported_formats}"
            )
        # Beside the format check, not down at the PNG render: EVERY reason this call
        # cannot succeed belongs before the work, or the "validate first" rule holds for
        # whichever argument was validated first.
        if "png" in want and dpi <= 0:
            raise ValueError(f"png export needs dpi > 0, got {dpi}")

    # AFTER validation, for the same reason validation precedes the deprecation warning
    # above: a call that is about to raise should not first do the work. On a declared
    # drawing this critique builds the recognition aggregate (#1022), so a mistyped format
    # used to scan the whole solid and only then report the typo.
    lint_and_log()

    # `formats=` wins over the legacy booleans, which means `export(out, formats=("svg",),
    # svg=False)` writes the SVG the caller just switched off — silently, since the legacy
    # branch below never runs. A caller passing both has a stale mental model, and a
    # deprecated argument that is ignored WITHOUT a word is the exact failure this change
    # exists to fix (#987). Say so; `formats` still wins.
    if want is not None and (svg is not None or dxf is not None):
        _ignored = [f"{k}=" for k, v in (("svg", svg), ("dxf", dxf)) if v is not None]
        warnings.warn(
            f"Drawing.export(): {', '.join(_ignored)} is deprecated and is IGNORED when "
            f"formats= is given — this call writes formats={tuple(want)!r}. Drop it, or "
            "put the format in formats=. Removed in 0.5.0.",
            DeprecationWarning,
            stacklevel=4,  # Include Drawing.export and its observer wrapper.
        )

    # --- legacy path: svg=/dxf= keywords → the old (svg, dxf) tuple (back-compat) ---
    if formats is None:
        # Two distinct legacy shapes, warned separately because the fix differs (#987):
        # the booleans SELECT formats, while bare export() is the old DEFAULT whose return
        # type is a tuple. Callers of the first want `formats=[...]`; callers of the second
        # additionally have to stop unpacking two values.
        if svg is not None or dxf is not None:
            # Name the formats THIS call selected, not a fixed ('svg', 'dxf') pair: the
            # booleans can deselect, so `export(out, svg=False, dxf=True)` writes DXF only
            # and a canned suggestion would tell the caller to start writing an SVG they
            # had switched off — advice that changes behaviour.
            _wanted = tuple(
                f for f, on in (("svg", svg is None or svg), ("dxf", dxf is None or dxf)) if on
            )
            warnings.warn(
                f"Drawing.export(svg=…, dxf=…) is deprecated; pass formats={_wanted!r} and "
                "read the {format: path} dict. Removed in 0.5.0.",
                DeprecationWarning,
                stacklevel=4,  # Include Drawing.export and its observer wrapper.
            )
        else:
            warnings.warn(
                "Drawing.export() with formats= omitted or None returns the legacy "
                "(svg, dxf) tuple and is deprecated; pass formats=(...) and read the "
                "{format: path} dict — e.g. export(out, formats=('svg', 'dxf')). "
                "Removed in 0.5.0.",
                DeprecationWarning,
                stacklevel=4,  # Include Drawing.export and its observer wrapper.
            )
        svg_path = write_svg(out, reproducible=reproducible) if (svg is None or svg) else None
        dxf_path = write_dxf(out, reproducible=reproducible) if (dxf is None or dxf) else None
        drawing.svg_path, drawing.dxf_path = svg_path, dxf_path
        return svg_path, dxf_path

    # --- formats=... → {format: path} (requested order); normalised + validated above ---
    assert want is not None  # formats is not None on this branch
    want_set = set(want)
    paths: dict[str, str] = {}
    # Intermediates — the SVG behind a PDF/PNG, the PDF behind a PNG — go to a temp dir when
    # not themselves requested, NEVER the user's <out>.svg/.pdf. Otherwise a later
    # `export(out, formats="png")` would overwrite then delete an <out>.svg/.pdf an earlier
    # export wrote. The temp dir + its contents are removed when the stack closes.
    with contextlib.ExitStack() as stack:
        tmpdir: str | None = None

        def _intermediate_stem() -> str:
            nonlocal tmpdir
            if tmpdir is None:
                tmpdir = stack.enter_context(
                    tempfile.TemporaryDirectory(prefix="draftwright-export-")
                )
            return os.path.join(tmpdir, "intermediate")

        svg_path = None
        if want_set & {"svg", "pdf", "png"}:
            svg_path = write_svg(
                out if "svg" in want_set else _intermediate_stem(),
                reproducible=reproducible,
            )
        drawing.svg_path = svg_path if "svg" in want_set else None
        if "svg" in want_set:
            paths["svg"] = svg_path  # type: ignore[assignment]

        drawing.dxf_path = None
        if "dxf" in want_set:
            drawing.dxf_path = paths["dxf"] = write_dxf(out, reproducible=reproducible)

        pdf_path = None
        if want_set & {"pdf", "png"}:
            assert svg_path is not None
            pdf_path = (out if "pdf" in want_set else _intermediate_stem()) + ".pdf"
            _render_pdf(
                svg_path,
                pdf_path,
                getattr(drawing.get_annotation("title_block"), "draftwright_link_rect", None),
                pdf_text_runs(),
                reproducible=reproducible,
            )
            _log.info("PDF → %s", pdf_path)
            if "pdf" in want_set:
                paths["pdf"] = pdf_path

        if "png" in want_set:
            assert pdf_path is not None
            paths["png"] = out + ".png"
            _render_png(pdf_path, paths["png"], dpi=dpi)
            _log.info("PNG → %s", paths["png"])
    return {f: paths[f] for f in want}


def add_shapes(exporter, *, views, items, iter_annotations, ordered: bool = False):
    """Add every view layer and annotation to *exporter* with error context.

    *ordered* hands each shape's parts over in a geometric order rather than
    the kernel's, which is what makes a DXF's entities (and their handles)
    come out the same on the next run. It is the costly half of
    ``reproducible=``; see :func:`export._elements`."""
    for name, (vis, hid) in views.items():
        _export_shape(exporter, vis, "part", f"view {name!r}", ordered=ordered)
        if hid:
            _export_shape(exporter, hid, "hidden", f"view {name!r}", ordered=ordered)
    names = {id(annotation): name for name, annotation in iter_annotations()}
    for ann in items:
        identity = names.get(id(ann)) or getattr(ann, "label", "") or type(ann).__name__
        _export_shape(exporter, ann, "dims", f"annotation {identity!r}", ordered=ordered)


def preview_annotation(
    name: str,
    path: str | os.PathLike[str],
    *,
    deferred_pending,
    get_annotation,
    view_of,
    view_bounds,
    views,
    write_svg,
) -> str:
    """Write a diagnostic SVG highlighting a placed annotation and its drawn tip.

    Includes the owning view when known. This is a snapshot of current ink, not a
    physical-target certificate. It neither finalizes edits nor alters the drawing or
    its export paths. Finish a deferred edit before requesting a preview. Unknown names
    raise ``KeyError``; missing ink bounds or a non-SVG path raise ``ValueError``.
    """
    if deferred_pending:
        raise ValueError("finish deferred edits before previewing an annotation")
    annotation = get_annotation(name)
    if annotation is None:
        raise KeyError(name)
    destination = os.fspath(path)
    if os.path.splitext(destination)[1].lower() != ".svg":
        raise ValueError("annotation previews require an .svg destination")
    if not hasattr(annotation, "bounding_box"):
        raise ValueError(f"{name}: annotation ink bounds unavailable")
    box = annotation.bounding_box()
    bounds = (box.min.X, box.min.Y, box.max.X, box.max.Y)
    view = view_of(name)
    context = view_bounds(view) if view is not None and view in views else bounds
    tip = getattr(annotation, "tip", None)
    with tempfile.TemporaryDirectory(dir=os.path.dirname(destination) or ".") as temporary:
        svg_path = write_svg(os.path.join(temporary, "preview"))
        highlight_svg_annotation(
            svg_path, name=name, view=view, bounds=bounds, context=context, tip=tip
        )
        os.replace(svg_path, destination)
    return destination
