"""The supported part of the archived make_drawing interface."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from . import _native


def _unsupported(name):
    raise TypeError(f"make_drawing keyword {name!r} is not supported in 0.6")


def make_drawing(
    step_file, out=None, title=None, number="DWG-001", tolerance=None,
    drawn_by="", scale=None, page=None, auto_dims=True, detail_view=True,
    pmi=None, assembly=None, material=None, date="", revision="A", company="",
    frame=False, projection=None, zones=False, scale_policy="fallback",
    reproducible=True, framed_recognition=False, text_position="inline",
    text_orientation="aligned", *, projection_symbol=True, source=None,
    approved_by="", document_type="", sheet="", margin_left=None,
    margin_right=None, margin_top=None, margin_bottom=None, title_block_width=None,
    units="mm", leader_region="auto", annotation_layout="demand-guided",
):
    """Draw a STEP path (str/PathLike) or build123d Shape using the Rust CLI engine.

    Returns **(svg_path, pdf_path)** as strings. In 0.4 the second path was DXF;
    **This wrapper still returns PDF**; editable DXF is available through the CLI
    with ``render --formats dxf``. Additional sheets use the
    CLI's usual suffixes. ``out`` is a stem, defaulting to the STEP path without
    its extension, or ``drawing`` in the working directory for a shape.

    Title fields, numeric scale, ISO/ANSI/ARCH page names, mm/inch units, first/third projection,
    zones, auto_dims and pmi map to the Rust Spec. The renderer always draws a
    frame and projection symbol. PMI defaults to off: None, "off" and "report"
    select inventory without annotations; "annotate" opts in. Paper names
    accept surrounding whitespace and either case. Other old options accept
    their defaults only; a non-default value raises TypeError naming the
    unsupported keyword. Custom page dimensions
    are likewise unsupported. Rust failures raise DraftwrightError.

    build123d is imported only for a Shape, exported via its own STEP exporter
    into a temporary directory. It is not a wheel dependency. For a shape,
    ``source`` names the original STEP document for PMI; geometry still comes
    from step_file. It also supplies PMI when step_file is a STEP path. An
    unreadable source raises DraftwrightError.

    The Rust defaults for automatic dimensions, views, scale and layout apply;
    accepting an old option's default does not select the old Python solver.
    Spec documentation: https://github.com/pzfreo/draftwright/blob/main/docs/model.md
    """
    values = locals()
    for name, default in {
        "detail_view": True, "assembly": None,
        "scale_policy": "fallback", "reproducible": True, "framed_recognition": False,
        "text_position": "inline", "text_orientation": "aligned",
        "projection_symbol": True, "margin_left": None, "margin_right": None,
        "margin_top": None, "margin_bottom": None, "title_block_width": None,
        "leader_region": "auto", "annotation_layout": "demand-guided",
    }.items():
        if values[name] != default:
            _unsupported(name)
    if pmi not in (None, "off", "report", "annotate"):
        _unsupported("pmi")
    if page is not None:
        original_page = page
        page = page.strip().upper() if isinstance(page, str) else page
        if page not in ("A4", "A3", "A2", "A1", "A0", *(f"{family} {size}" for family in ("ANSI", "ARCH") for size in "ABCDE")):
            raise TypeError(
                f"make_drawing page value {original_page!r} is not supported in 0.6; "
                "expected A4..A0, ANSI A..E, ARCH A..E (case-insensitive) or None"
            )
    if units not in ("mm", "in"):
        raise TypeError("make_drawing units must be mm or in")
    fields = {
        name: values[name] for name in (
            "title", "number", "tolerance", "drawn_by", "material", "date", "revision",
            "company", "approved_by", "document_type", "sheet",
        ) if values[name] is not None
    }
    spec = {"units": units, "sheet": {"title": fields, "zones": zones}}
    if not auto_dims:
        spec["dimension_source"] = "authored"
        spec["sheet"].update(centre_lines=False, centre_marks=False)
    # The archived Python API defaults to off, unlike the native CLI's First.
    spec["pmi"] = "first" if pmi == "annotate" else "ignore"
    if page is not None:
        spec["sheet"]["size"] = page
    if projection is not None:
        spec["sheet"]["projection"] = projection
    spec_json = json.dumps(spec)
    output = os.fsdecode(os.fspath(out)) if out is not None else None
    source_path = os.fsdecode(os.fspath(source)) if source is not None else None
    if isinstance(step_file, (str, bytes, os.PathLike)):
        return _native.render(os.fsdecode(os.fspath(step_file)), output, spec_json, scale, source_path)

    # No CAD-kernel import is paid by path users, importers or console commands.
    from build123d import Shape, export_step
    if not isinstance(step_file, Shape):
        raise TypeError("step_file must be a STEP path or a build123d Shape")
    with TemporaryDirectory(prefix="draftwright-") as temporary:
        step = Path(temporary) / "drawing.step"
        if not export_step(step_file, step):
            raise _native.DraftwrightError("build123d could not export the shape to STEP")
        return _native.render(
            str(step), output if output is not None else "drawing", spec_json, scale,
            source_path,
        )
