"""SVG / DXF / PDF export and post-processing (#138 / ADR 0005, Step "export").

The free functions the public `Drawing.export()` / `Drawing.export_pdf()` wrappers
drive: SVG page-size fixing, the attribution hyperlink and metadata, DXF metadata,
near-degenerate arc sanitisation, the element-wise shape-export degradation, and
the svglib + reportlab PDF render. Moved out of `make_drawing.py` so the orchestration there
stays thin; this module mostly *consumes* drawing contents (paths, exporters,
shapes), so it sits below `make_drawing` in the import DAG and depends only on
`_core` (the shared `_DRAFTWRIGHT_URL`) and build123d.
"""

from __future__ import annotations

import contextlib
import logging
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from io import BytesIO
from itertools import groupby
from pathlib import Path

import ezdxf
from build123d import ExportDXF, ExportSVG
from ezdxf.document import (
    CONST_GUID,
    CONST_MARKER_STRING,
    CREATED_BY_EZDXF,
    WRITTEN_BY_EZDXF,
    juliandate,
    tocodepage,
)
from OCP.BRepTools import BRepTools
from OCP.GeomConvert import GeomConvert
from OCP.TopTools import TopTools_FormatVersion

from draftwright._core import _DRAFTWRIGHT_URL
from draftwright.fonts import PLEX_MONO

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _PDFTextRun:
    """One searchable, invisible text run in drawing page coordinates (mm)."""

    text: str
    x: float
    y: float
    font_size: float
    rotation: float = 0.0
    font_path: str | None = PLEX_MONO
    font_name: str = "Arial"
    font_style: str = "REGULAR"
    h_align: str = "left"
    v_align: str = "baseline"


@lru_cache(maxsize=32)
def _resolved_semantic_font_path(
    font_path: str | None, font_name: str, font_style: str = "REGULAR"
) -> str:
    """Resolve the same regular face used by build123d's text renderer.

    A ``None`` path is a deliberate opt-out from Draftwright's pinned font: build123d
    resolves ``draft.font`` through OCCT.  Ask that same manager for the concrete file so
    ReportLab embeds the matching face instead of silently substituting Plex Mono.
    """
    if font_path is not None:
        return font_path

    from build123d import FontStyle

    style = FontStyle[font_style]
    try:
        from build123d.text import FONT_ASPECT, FontManager
    except ModuleNotFoundError as exc:
        if exc.name != "build123d.text":  # pragma: no cover - broken installation
            raise

        # build123d 0.10 resolves fonts inline in Compound.make_text(); the public
        # FontManager wrapper arrived in 0.11.  Mirror the 0.10 renderer so the
        # invisible PDF face remains the one that produced the visible outlines.
        import os
        import sys

        import OCP.Font as ocp_font
        from OCP.TCollection import TCollection_AsciiString

        if sys.platform.startswith("linux"):
            os.environ["FONTCONFIG_FILE"] = "/etc/fonts/fonts.conf"
            os.environ["FONTCONFIG_PATH"] = "/etc/fonts/"
        font_aspects = {
            FontStyle.REGULAR: ocp_font.Font_FA_Regular,
            FontStyle.BOLD: ocp_font.Font_FA_Bold,
            FontStyle.ITALIC: ocp_font.Font_FA_Italic,
        }
        if hasattr(FontStyle, "BOLDITALIC"):
            font_aspects[FontStyle.BOLDITALIC] = ocp_font.Font_FA_BoldItalic
        font_aspect = font_aspects[style]
        face = ocp_font.Font_FontMgr.GetInstance_s().FindFont(
            TCollection_AsciiString(font_name), font_aspect
        )
        return str(face.FontPath(font_aspect).ToCString())

    face = FontManager().find_font(font_name, style)
    return str(face.FontPath(FONT_ASPECT[style]).ToCString())


@lru_cache(maxsize=16)
def _semantic_font_name(font_path: str) -> str:
    """Return a stable reportlab registration name for one bundled font file."""
    from hashlib import sha256

    path = Path(font_path)
    digest = sha256(path.read_bytes()).hexdigest()[:12]
    stem = re.sub(r"[^A-Za-z0-9]+", "_", path.stem)
    return f"Draftwright_{stem}_{digest}"


_SVG_NS = "{http://www.w3.org/2000/svg}"


def canonicalize_svg(svg_path: str) -> None:
    """Emit the elements of each layer in an order that does not depend on the run.

    ExportSVG writes one element per face, in the order build123d hands them
    over - and that order is not stable between runs. Two faces of one glyph
    (the stem and the dot over an 'i') change places, so two exports of one
    drawing differ in bytes while being the same picture. That is enough to make
    a drawing useless as something to check in and diff, which is how a caller
    notices that its output has changed.

    It is not simply PYTHONHASHSEED: holding the seed fixed does not hold the
    order, so a caller cannot pin this from the environment and the file has to
    be settled here.

    Sorting each layer's children by what they *are* settles it. The layers keep
    their order, so what paints over what is unchanged; within a layer these are
    disjoint outlines with one style, where order is not something the drawing
    means. Only groups whose children are all leaves are touched, so a nested
    group is never flattened or reordered.
    """
    tree = ET.parse(svg_path)
    for group in tree.getroot().iter(_SVG_NS + "g"):
        children = list(group)
        if len(children) < 2 or any(len(child) for child in children):
            continue
        # `tail` is the whitespace ExportSVG writes *after* an element, so it
        # belongs to the slot rather than to the element: the last child of a
        # group carries a shorter indent than its siblings. Leave the tails
        # where they are and move only the elements through them, or the last
        # slot's indent rides along with whichever element happened to land
        # there and two runs that agree on the order still differ in bytes.
        tails = [child.tail for child in children]
        children.sort(key=_leaf_key)
        for child, tail in zip(children, tails):
            child.tail = tail
        group[:] = children
    # Strip the SVG namespace from in-memory tag spellings, then declare it as
    # the document default. This preserves ordinary ``<svg>``/``<g>`` output
    # without ``register_namespace``, whose process-global registry would change
    # how unrelated callers serialize their own XML after a Draftwright export.
    root = tree.getroot()
    for element in root.iter():
        if isinstance(element.tag, str) and element.tag.startswith(_SVG_NS):
            element.tag = element.tag[len(_SVG_NS) :]
    root.set("xmlns", _SVG_NS.strip("{}"))
    tree.write(svg_path, encoding="utf-8", xml_declaration=True)


def _leaf_key(element):
    """A run-independent total order over leaf elements: what the element is.

    ``tail`` is deliberately absent - the caller keeps tails with slots rather
    than with elements, which is what actually settles the bytes - so this is
    the element's identity alone, and cheaper than serializing each one to
    compare it. Elements that tie here are byte-identical, so their relative
    order cannot reach the file either way.
    """
    return (element.tag, tuple(sorted(element.attrib.items())), element.text or "")


def fix_svg_page_size(svg_path: str, page_w: float, page_h: float) -> None:
    """Rewrite the SVG width/height/viewBox to match the full ISO page size.

    ExportSVG crops to content bounding box; this expands it to the declared
    page so the rendering fills the correct A-series sheet.
    """
    data = Path(svg_path).read_text(encoding="utf-8")
    data = re.sub(r'width="[^"]*"', f'width="{page_w:.3f}mm"', data, count=1)
    data = re.sub(r'height="[^"]*"', f'height="{page_h:.3f}mm"', data, count=1)
    data = re.sub(
        r'viewBox="[^"]*"',
        f'viewBox="0 -{page_h:.3f} {page_w:.3f} {page_h:.3f}"',
        data,
        count=1,
    )
    Path(svg_path).write_text(data, encoding="utf-8")


# Below this, an elliptical-arc radius (page-mm) is treated as degenerate.
# Real feature arcs are orders of magnitude larger; the bad ones are ~1e-7.
_MIN_ARC_RADIUS = 1e-3

_SVG_NUM = r"(-?\d+\.?\d*(?:[eE][-+]?\d+)?)"
_SVG_ARC_RE = re.compile(
    r"A\s*"
    + _SVG_NUM
    + r"[ ,]+"
    + _SVG_NUM
    + r"[ ,]+"
    + _SVG_NUM
    + r"[ ,]+([01])[ ,]*([01])[ ,]+"
    + _SVG_NUM
    + r"[ ,]+"
    + _SVG_NUM
)


def add_svg_hyperlink(svg_path: str, rect, href: str = _DRAFTWRIGHT_URL) -> None:
    """Overlay a transparent, clickable hyperlink rectangle on the SVG.

    *rect* is ``(x0, y0, x1, y1)`` in drawing page coordinates (Y up). The SVG
    viewBox is ``0 -page_h page_w page_h`` (see :func:`fix_svg_page_size`), so a
    page point ``(X, Y)`` maps to SVG ``(X, -Y)``. The rect is wrapped in an
    ``<a>`` with a fully transparent fill and ``pointer-events="all"`` so the
    whole cell is clickable without altering the rendered drawing. DXF carries
    no equivalent, so this is SVG-only.
    """
    x0, y0, x1, y1 = rect
    sx, sy, sw, sh = x0, -y1, x1 - x0, y1 - y0
    link = (
        f'<a xlink:href="{href}" href="{href}" target="_blank">'
        f'<rect x="{sx:.3f}" y="{sy:.3f}" width="{sw:.3f}" height="{sh:.3f}" '
        f'fill="transparent" pointer-events="all"/></a>'
    )
    data = Path(svg_path).read_text(encoding="utf-8")
    if f'href="{href}"' in data:
        return  # already injected — keep idempotent like add_svg_metadata
    if "xmlns:xlink" not in data:
        data = re.sub(r"(<svg\b)", r'\1 xmlns:xlink="http://www.w3.org/1999/xlink"', data, count=1)
    data = data.replace("</svg>", link + "</svg>", 1)
    Path(svg_path).write_text(data, encoding="utf-8")


_GENERATED_BY = "Generated by draftwright"


def add_svg_metadata(svg_path: str) -> None:
    """Embed a 'generated by draftwright' note in the SVG (comment + Dublin Core
    ``<metadata>``), so the attribution survives in the file itself."""
    meta = (
        f"<!-- {_GENERATED_BY}: {_DRAFTWRIGHT_URL} -->"
        "<metadata>"
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/">'
        "<rdf:Description>"
        "<dc:creator>draftwright</dc:creator>"
        f"<dc:source>{_DRAFTWRIGHT_URL}</dc:source>"
        f"<dc:description>{_GENERATED_BY}</dc:description>"
        "</rdf:Description></rdf:RDF></metadata>"
    )
    data = Path(svg_path).read_text(encoding="utf-8")
    if "<dc:creator>draftwright</dc:creator>" not in data:
        data = re.sub(r"(<svg\b[^>]*>)", r"\1" + meta, data, count=1)
        Path(svg_path).write_text(data, encoding="utf-8")


def set_dxf_metadata(dxf) -> None:
    """Stamp 'generated by draftwright' into the DXF header as custom properties
    (``$CUSTOMPROPERTY``), which CAD applications surface in drawing properties.
    Best-effort: a no-op if the exporter doesn't expose an ezdxf document."""
    doc = getattr(dxf, "_document", None)
    if doc is None:
        return
    try:
        cv = doc.header.custom_vars
        cv.append("GeneratedBy", "draftwright")
        cv.append("draftwrightURL", _DRAFTWRIGHT_URL)
    except Exception:
        pass


def write_dxf(dxf, path: str, page_w: float, page_h: float, reproducible: bool = False) -> None:
    """Write *dxf* with the viewport zoomed to the known page window.

    ``ExportDXF.write`` resets the viewport via ``ezdxf.zoom.extents`` — a
    pure-Python walk of every modelspace entity that re-flattens each spline,
    costing seconds on a dimension-dense sheet (#602; build123d#382 tracks
    exposing viewport control). The drawing already lives in page-mm
    coordinates ``(0, 0)–(page_w, page_h)``, so set that single-window
    viewport directly and save. Falls back to ``dxf.write`` if the optimized
    viewport seam is unavailable while retaining reproducible metadata whenever
    the ezdxf document is accessible. A requested reproducible export fails
    clearly if the document itself is unavailable rather than returning live data.
    """
    doc = getattr(dxf, "_document", None)
    if doc is None:
        if reproducible:
            raise RuntimeError("reproducible DXF export requires access to the ezdxf document")
        dxf.write(path)
        return

    def save(save_fn) -> None:
        if reproducible:
            with _reproducible_dxf(doc):
                save_fn()
        else:
            save_fn()

    msp = getattr(dxf, "_modelspace", None)
    if msp is None:
        save(lambda: dxf.write(path))
        return
    try:
        from ezdxf import zoom

        zoom.window(msp, (0.0, 0.0), (page_w, page_h))
    except Exception:
        save(lambda: dxf.write(path))
        return
    save(lambda: doc.saveas(path, fmt="asc"))


@contextlib.contextmanager
def _reproducible_dxf(doc):
    """Take the clock and the hash seed out of what ezdxf is about to write.

    Every save stamps the time it happened ($TDCREATE/$TDUPDATE and their UTC
    twins), a freshly generated $VERSIONGUID/$FINGERPRINTGUID pair, and marker
    strings carrying timestamps. ezdxf exposes a testing switch for fixed values,
    but it is process-global and therefore races concurrent exports. Install the
    equivalent updater on this one fresh document for the duration of its save;
    unrelated ezdxf users and concurrent Draftwright exports remain untouched.

    The CLASSES section is the last part. ezdxf registers a class entry for
    every DXF type the document uses and finds them by iterating a set of type
    names, whose order over strings follows the interpreter's hash seed. A
    caller cannot pin that seed from the environment in every host (a sandbox
    running under '-I' makes Python ignore PYTHONHASHSEED), so the names are
    registered here first, sorted: 'add_class()' keeps the first entry for a key,
    and ezdxf's own pass during the save then adds only what is left.
    """
    original_update_metadata = doc._update_metadata

    def update_metadata_reproducibly() -> None:
        metadata = doc.ezdxf_metadata()
        metadata[CREATED_BY_EZDXF] = CONST_MARKER_STRING
        metadata[WRITTEN_BY_EZDXF] = CONST_MARKER_STRING
        fixed_date = juliandate(datetime(2000, 1, 1, 0, 0))
        for name in ("$TDCREATE", "$TDUCREATE", "$TDUPDATE", "$TDUUPDATE"):
            doc.header[name] = fixed_date
        doc.header["$VERSIONGUID"] = CONST_GUID
        doc.header["$FINGERPRINTGUID"] = CONST_GUID
        doc.header["$HANDSEED"] = str(doc.entitydb.handles)
        doc.header["$DWGCODEPAGE"] = tocodepage(doc.encoding)

    doc._update_metadata = update_metadata_reproducibly
    try:
        for dxftype in sorted(doc.entitydb.dxf_types_in_use()):
            doc.classes.add_class(dxftype)
        yield
    finally:
        doc._update_metadata = original_update_metadata


class _DraftwrightDXF(ExportDXF):
    """build123d's DXF exporter with bounded OCCT curve-array extraction.

    ``Geom_BSplineCurve.Poles()`` and ``KnotSequence()`` expose OCCT arrays through
    unusually expensive Python sequence wrappers.  A glyph-dense CTC-02 hole table has
    4,504 Bezier edges; iterating just its 13,512 poles took 8.6 seconds while the
    equivalent indexed ``Pole(i)`` calls took 0.014 seconds (#1070).  Keep build123d's
    conversion and ezdxf construction unchanged, but read the same values through OCCT's
    indexed API so output geometry is identical and cost is linear in the actual array size.
    """

    def _convert_bspline(self, edge, attribs):
        if edge.is_null:
            raise ValueError(f"Edge is empty {edge}.")
        edge = edge.to_splines()
        location = edge.location
        if location is None:
            raise ValueError(f"Edge has no location {edge}.")
        adaptor = edge.geom_adaptor()
        curve = adaptor.Curve().Curve()
        spline = GeomConvert.SplitBSplineCurve_s(
            curve,
            adaptor.FirstParameter(),
            adaptor.LastParameter(),
            self.PARAMETRIC_TOLERANCE,
        )

        spline.Transform(location.wrapped.Transformation())

        order = spline.Degree() + 1
        knots = [
            spline.Knot(index)
            for index in range(1, spline.NbKnots() + 1)
            for _ in range(spline.Multiplicity(index))
        ]
        poles = [
            self._convert_point(spline.Pole(index)) for index in range(1, spline.NbPoles() + 1)
        ]
        weights = (
            [spline.Weight(index) for index in range(1, spline.NbPoles() + 1)]
            if spline.IsRational()
            else None
        )

        # SplitBSplineCurve currently deperiodicizes every bounded edge.  Retain
        # build123d's padding rule if a future OCCT version preserves periodicity.
        if spline.IsPeriodic():  # pragma: no cover - defensive upstream parity
            pad = spline.NbKnots() - spline.LastUKnotIndex()
            poles += poles[:pad]

        dxf_spline = ezdxf.math.BSpline(poles, order, knots, weights)
        self._modelspace.add_spline(dxfattribs=attribs).apply_construction_tool(dxf_spline)


def sanitize_svg_arcs(svg_path: str) -> int:
    """Rewrite near-degenerate elliptical arcs as straight line segments.

    build123d's ``ExportSVG`` projects a circular edge seen edge-on (a hole or
    fillet rim whose plane is parallel to the view direction) as an elliptical
    arc with a vanishing minor radius (``ry`` ≈ 1e-7).  The SVG spec says a
    zero-radius arc is a straight line, but because the radius is not *exactly*
    zero, many SVG renderers treat it as a hugely eccentric ellipse
    and draw a spurious full-page line.  Each such arc (``A rx ry rot lf sf x
    y``) with ``rx`` or ``ry`` below :data:`_MIN_ARC_RADIUS` is replaced by
    ``L x y`` — its true geometry.  Returns the number of arcs rewritten.
    """
    data = Path(svg_path).read_text(encoding="utf-8")
    n = 0

    def _repl(m):
        nonlocal n
        if abs(float(m.group(1))) < _MIN_ARC_RADIUS or abs(float(m.group(2))) < _MIN_ARC_RADIUS:
            n += 1
            return f"L {m.group(6)} {m.group(7)}"
        return m.group(0)

    fixed = _SVG_ARC_RE.sub(_repl, data)
    if n:
        Path(svg_path).write_text(fixed, encoding="utf-8")
    return n


def _render_pdf(
    svg_path: str,
    pdf_path: str,
    link_rect=None,
    text_runs=(),
    reproducible: bool = False,
) -> None:
    """Render *svg_path* to *pdf_path* via svglib + reportlab, adding draftwright
    metadata and — when *link_rect* (drawing page coords, Y up) is given — a
    clickable PDF link annotation over that rectangle. *text_runs* overlays
    invisible Unicode text on the path-rendered glyphs, preserving the visual
    output while making notes and table cells searchable and selectable.

    svglib does not translate the SVG ``<a>`` element into a PDF link, so the
    link is added with reportlab's ``Canvas.linkURL``. The drawing page maps to
    PDF points at 72/25.4 pt·mm⁻¹; both the page and PDF user space are Y up with
    a bottom-left origin, so the page rect ``(x0, y0, x1, y1)`` scales straight
    through with no flip. Pure Python — no native cairo — so PDF works on every
    platform. Falls back to a plain render if the richer path fails for any
    reason."""
    from reportlab.pdfgen.canvas import Canvas
    from svglib.svglib import svg2rlg

    drawing = svg2rlg(svg_path)
    if drawing is None:
        raise ValueError(f"could not parse SVG for PDF render: {svg_path}")

    try:
        from reportlab.graphics import renderPDF

        # 'invariant' is reportlab's own switch for a file that does not
        # carry the moment it was produced: a fixed /CreationDate and a derived
        # /ID rather than the clock's. Without it two renders of one drawing
        # differ in bytes.
        canvas = Canvas(pdf_path, pagesize=(drawing.width, drawing.height), invariant=reproducible)
        canvas.setCreator(_GENERATED_BY)
        canvas.setTitle(_GENERATED_BY)
        renderPDF.draw(drawing, canvas, 0, 0)
        if text_runs:
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.ttfonts import TTFont

            k = 72.0 / 25.4
            font_choices: dict[tuple[str | None, str, str], tuple[str, str]] = {}
            for run in text_runs:
                font_key = (run.font_path, run.font_name, run.font_style)
                choice = font_choices.get(font_key)
                if choice is None:
                    font_path = _resolved_semantic_font_path(*font_key)
                    font_name = _semantic_font_name(font_path)
                    if font_name not in pdfmetrics.getRegisteredFontNames():
                        try:
                            pdfmetrics.registerFont(TTFont(font_name, font_path))
                        except Exception as exc:  # noqa: BLE001 - CFF/single-stroke faces
                            # Geometry supports more font formats than ReportLab's TTFont
                            # subsetter. Keep export/search total with the bundled Unicode face.
                            # Cache the choice per export so a dense drawing warns/probes once.
                            _log.warning(
                                "PDF semantic font %s cannot be embedded (%s); "
                                "using bundled Plex Mono",
                                font_path,
                                exc,
                            )
                            font_path = PLEX_MONO
                            font_name = _semantic_font_name(font_path)
                            if font_name not in pdfmetrics.getRegisteredFontNames():
                                pdfmetrics.registerFont(TTFont(font_name, font_path))
                    choice = (font_path, font_name)
                    font_choices[font_key] = choice
                font_path, font_name = choice
                font_size = run.font_size * k
                dx = 0.0
                if run.h_align == "center":
                    dx = -pdfmetrics.stringWidth(run.text, font_name, font_size) / 2.0
                elif run.h_align == "right":
                    dx = -pdfmetrics.stringWidth(run.text, font_name, font_size)
                elif run.h_align != "left":
                    raise ValueError(f"unknown PDF text horizontal alignment {run.h_align!r}")
                dy = 0.0
                if run.v_align == "middle":
                    ascent, descent = pdfmetrics.getAscentDescent(font_name, font_size)
                    dy = -(ascent + descent) / 2.0
                elif run.v_align != "baseline":
                    raise ValueError(f"unknown PDF text vertical alignment {run.v_align!r}")

                angle = math.radians(run.rotation)
                cos_angle, sin_angle = math.cos(angle), math.sin(angle)
                origin_x = run.x * k + cos_angle * dx - sin_angle * dy
                origin_y = run.y * k + sin_angle * dx + cos_angle * dy
                text = canvas.beginText()
                text.setTextRenderMode(3)  # invisible, but retained for search/copy
                text.setFont(font_name, font_size)
                if run.rotation:
                    text.setTextTransform(
                        cos_angle,
                        sin_angle,
                        -sin_angle,
                        cos_angle,
                        origin_x,
                        origin_y,
                    )
                else:
                    text.setTextOrigin(origin_x, origin_y)
                text.textOut(run.text)
                if cos_angle < -1e-9:
                    # Poppler can reorder a leftward run even though its geometry is correct.
                    # ActualText is the PDF-standard logical representation for extraction;
                    # its marked content retains the run's matrix and overall positioned ink
                    # bounds. Replacement text is span-atomic: PDFium partitions that overall
                    # AABB for partial-character selection, but whole-term selection stays on
                    # the physical word. Keeping an unwrapped physical copy would instead
                    # corrupt search/copy in every Poppler extraction mode.
                    actual_text = "FEFF" + run.text.encode("utf-16-be").hex().upper()
                    canvas.addLiteral(f"/Span << /ActualText <{actual_text}> >> BDC")
                    canvas.drawText(text)
                    canvas.addLiteral("EMC")
                else:
                    canvas.drawText(text)
        if link_rect is not None:
            k = 72.0 / 25.4
            x0, y0, x1, y1 = link_rect
            canvas.linkURL(
                _DRAFTWRIGHT_URL, (x0 * k, y0 * k, x1 * k, y1 * k), relative=0, thickness=0
            )
        canvas.showPage()
        canvas.save()
    except Exception:
        if text_runs or reproducible:
            # Searchable text is a requested output property, not an optional
            # embellishment, and reproducibility is likewise a requested contract.
            # Never report success after silently discarding either property.
            _log.error("PDF render failed with required output properties", exc_info=True)
            raise
        # Never fail the export over the link/metadata extras; degrade to a
        # plain render. Logged at debug so a regression in the link annotation
        # is diagnosable (in normal use only the dedicated test would catch it).
        _log.debug("PDF link/metadata extras failed; rendered a plain PDF", exc_info=True)
        from reportlab.graphics import renderPDF

        renderPDF.drawToFile(drawing, pdf_path)


def _render_png(pdf_path: str, png_path: str, *, dpi: int = 150) -> None:
    """Rasterise *pdf_path* (page 1) to *png_path* at *dpi*, via pypdfium2 (Google PDFium,
    BSD-3-Clause) + Pillow (HPND) — both permissively licensed, pre-built wheels with NO native
    system deps, so PNG works cross-platform without cairo (ADR 0006). The PNG rides on the PDF
    render, so the raster matches the vector output exactly."""
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:  # pragma: no cover — packaging guard
        raise ImportError(
            "PNG export requires pypdfium2 + Pillow (core dependencies); reinstall draftwright"
        ) from exc

    pdf = pdfium.PdfDocument(pdf_path)
    try:
        page = pdf[0]
        bitmap = page.render(scale=dpi / 72.0)  # PDF user space is 72 dpi
        bitmap.to_pil().save(png_path)  # PIL encodes the PNG
    finally:
        pdf.close()


def _geometry_key(part):
    """Cheap geometric prefix for grouping export parts before exact tie-breaking.

    The box alone does not separate parts: hidden-line removal emits the same
    segment twice where two silhouettes project onto each other, once each way
    round, and the pair shares a box, a length and a vertex set. Only the
    *directed* ends tell them apart - and the direction is itself what the
    exporter writes out, so leaving them tied would let two runs swap a pair and
    change the file. Distinct faces can still share every fact in this prefix;
    :func:`_ordered_parts` resolves those groups from exact B-rep bytes.
    """
    box = part.bounding_box()
    key = (
        round(box.min.X, 9),
        round(box.min.Y, 9),
        round(box.min.Z, 9),
        round(box.max.X, 9),
        round(box.max.Y, 9),
        round(box.max.Z, 9),
        str(part.geom_type),
        len(part.edges()),
    )
    try:
        start, end = part.start_point(), part.end_point()
    except Exception:
        return key
    return key + tuple(round(v, 9) for v in (start.X, start.Y, start.Z, end.X, end.Y, end.Z))


def _brep_key(part) -> bytes:
    """Exact, run-independent tie-breaker containing topology and curve geometry."""
    stream = BytesIO()
    BRepTools.Write_s(
        part.wrapped,
        stream,
        False,
        False,
        TopTools_FormatVersion.TopTools_FormatVersion_VERSION_3,
    )
    return stream.getvalue()


def _ordered_parts(parts):
    """Order by the cheap key, serialising exact B-rep only inside tied groups."""
    keyed = sorted(((_geometry_key(part), part) for part in parts), key=lambda item: item[0])
    ordered = []
    for _key, entries in groupby(keyed, key=lambda item: item[0]):
        group = [part for _prefix, part in entries]
        if len(group) > 1:
            group.sort(key=_brep_key)
        ordered.extend(group)
    return ordered


def _elements(shape, *, ordered: bool = False):
    """Decompose *shape* for export retry: faces plus any loose edges.

    With *ordered*, faces are flattened to the boundary edges DXF actually emits
    and the complete entity stream is sorted by exact geometry. Sorting whole
    faces is insufficient: one face can retain a run-dependent cyclic starting
    edge inside its B-rep traversal. Entity-level ordering fixes both the order of
    faces and the order within each face. (The SVG settles the same question after
    the fact in :func:`canonicalize_svg`, where the elements are already in the
    file and cost nothing to reorder.)

    **Ordering is the expensive half of a reproducible export** - one
    ``bounding_box()`` and one ``edges()`` per part, measured at about a third
    of DXF export time again on a 358-part sheet (0.40 s -> 0.54 s, interleaved
    over 9 runs) - so it is opt-in, and off costs exactly what it did before the
    option existed. The metadata pinning in :func:`write_dxf` is the cheap half
    (~1 ms); both hang off the one ``reproducible`` flag a caller sets, because
    a caller asking for a file that does not change between runs wants both and
    should not have to know which one costs. This sits on the hot path #602
    cleared: measure, do not predict.
    """
    faces = list(shape.faces())
    if not faces:
        edges = list(shape.edges())
        return _ordered_parts(edges) if ordered else edges
    owned = {e for f in faces for e in f.edges()}
    loose = [e for e in shape.edges() if e not in owned]
    if not ordered:
        return faces + loose
    face_edges = [edge for face in faces for edge in face.edges()]
    return _ordered_parts(face_edges + loose)


def _export_shape(exporter, shape, layer, ctx, *, ordered: bool = False):
    """Add *shape* to *exporter*, degrading element-by-element on failure.

    build123d's exporters abort the whole export on the first edge whose
    curve cannot be approximated (a bare ``AssertionError`` from OCCT, #83).
    Instead, drop only the offending elements with a warning naming the
    view/layer, and raise (with that context) only if nothing exported.

    ``ExportSVG.add_shape`` is atomic — it appends converted elements only
    after the whole shape succeeds — so the shape is tried in one call first.
    ``ExportDXF`` writes edge-by-edge as it converts, so a mid-shape failure
    would leave partial output that a blind retry duplicates; for it (and any
    unknown exporter) every element is added individually from the start.
    """
    first_err = None
    if isinstance(exporter, ExportSVG):
        try:
            exporter.add_shape(shape, layer=layer)
            return
        except Exception as exc:
            first_err = exc
            _log.warning(
                "%s (layer %r) failed to export as one shape: %s — retrying element-wise",
                ctx,
                layer,
                exc,
            )
    elements = _elements(shape, ordered=ordered)
    skipped = 0
    for element in elements:
        try:
            exporter.add_shape(element, layer=layer)
        except Exception as exc:
            first_err = first_err or exc
            skipped += 1
            _log.debug("%s (layer %r): element failed to convert: %s", ctx, layer, exc)
    if skipped == len(elements) and first_err is not None:
        raise RuntimeError(f"{ctx} (layer {layer!r}): nothing could be exported") from first_err
    if skipped:
        _log.warning(
            "%s (layer %r): skipped %d of %d elements that failed to convert",
            ctx,
            layer,
            skipped,
            len(elements),
        )
