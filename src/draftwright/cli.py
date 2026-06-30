"""cli — the draftwright command-line interface (Typer, #289).

A thin Typer front-end over the build engine (`builder.build_drawing` /
`generate_script`): it owns argument parsing, ``--version``, shell completion,
and rich-formatted help, then calls down into the engine. The engine stays
headless — no presentation concern leaks below this module, so this is also the
home the event-stream / TUI work (#276) wraps its sink + renderer around.
"""

from __future__ import annotations

import logging
import os
from enum import Enum
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

import typer

from draftwright.builder import build_drawing, generate_script

app = typer.Typer(
    add_completion=True,
    # Plain tracebacks on an engine error, matching the old argparse CLI — a rich
    # source-panel traceback buries clean domain errors (e.g. "could not read STEP
    # file") in developer noise.
    pretty_exceptions_enable=False,
    help="Zero-AI STEP -> technical drawing (PDF by default; SVG/DXF via --format, "
    "or an editable .py script).",
)

# Output formats the --format selector understands ('all' expands to all three).
_FORMATS = ("pdf", "svg", "dxf")


class PmiMode(str, Enum):
    """AP242 PMI handling mode (mirrors the engine's ``pmi`` argument)."""

    off = "off"
    report = "report"
    annotate = "annotate"


def _parse_formats(value: str) -> list[str]:
    """Parse a ``--format`` value (comma-list, with an ``all`` alias) into an
    ordered, de-duplicated list of formats. Raises on an unknown token."""
    out: list[str] = []
    for raw in value.split(","):
        tok = raw.strip().lower()
        if not tok:
            continue
        names = _FORMATS if tok == "all" else (tok,)
        for name in names:
            if name not in _FORMATS:
                raise typer.BadParameter(
                    f"unknown format {tok!r}; choose from {', '.join(_FORMATS)} (or 'all')"
                )
            if name not in out:
                out.append(name)
    if not out:
        raise typer.BadParameter("no output format given")
    return out


def _emit(dwg, formats: list[str]) -> list[str]:
    """Write the requested formats and return their paths in *formats* order.
    PDF renders from an on-disk SVG, so the SVG is written to drive the PDF even
    when it wasn't itself requested — and removed again afterwards."""
    want = {f: f in formats for f in _FORMATS}
    svg_path, dxf_path = dwg.export(svg=want["svg"] or want["pdf"], dxf=want["dxf"])
    pdf_path = dwg.export_pdf() if want["pdf"] else None
    if want["pdf"] and not want["svg"] and svg_path is not None:
        os.remove(svg_path)  # temp SVG, written only to render the PDF
    paths = {"pdf": pdf_path, "svg": svg_path, "dxf": dxf_path}
    return [paths[f] for f in formats]


def _installed_version() -> str:
    """The installed distribution version (the PyPI version once pip-installed);
    ``"unknown"`` when running from a source tree with no installed metadata."""
    try:
        return _pkg_version("draftwright")
    except PackageNotFoundError:
        return "unknown"


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"draftwright {_installed_version()}")
        raise typer.Exit()


@app.command()
def main(
    step_file: str = typer.Argument(..., help="Input STEP file (.step / .stp)"),
    out: str | None = typer.Option(None, help="Output prefix (default: input stem)"),
    title: str | None = typer.Option(None, help="Part title for title block"),
    number: str = typer.Option("DWG-001", help="Drawing number"),
    tolerance: str = typer.Option("ISO 2768-m", help="General tolerance"),
    drawn_by: str = typer.Option("", "--drawn-by", help="Designer name"),
    scale: float | None = typer.Option(
        None, help="Drawing-scale override, e.g. 5 for 5:1 or 0.5 for 1:2 (default: auto)"
    ),
    page: str | None = typer.Option(
        None, help="Page-size override: A4..A0 or WIDTHxHEIGHT in mm, e.g. 420x297 (default: auto)"
    ),
    script: bool = typer.Option(
        False, "--script", help="Write an editable .py drawing script instead of SVG+DXF"
    ),
    pmi: PmiMode = typer.Option(
        PmiMode.off,
        help=(
            "AP242 PMI handling: 'off' ignore; 'report' log extracted PMI without "
            "annotating; 'annotate' add PMI-derived dimensions to the drawing"
        ),
    ),
    output_format: str = typer.Option(
        "pdf",
        "--format",
        "-f",
        help="Comma-list of output formats: pdf, svg, dxf (or 'all'). E.g. --format pdf,dxf",
    ),
    verbose: bool = typer.Option(
        False,
        "-v",
        "--verbose",
        help="Show detailed progress (default: warnings and errors only)",
    ),
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the installed draftwright version and exit.",
    ),
) -> None:
    """Generate a fully-annotated technical drawing from a STEP file."""
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING, format="%(message)s")

    if script and (scale is not None or page is not None):
        raise typer.BadParameter(
            "--scale/--page only apply to direct output; edit the generated script instead"
        )

    formats = _parse_formats(output_format)

    if script:
        py_path = generate_script(
            step_file=step_file,
            out=out,
            title=title,
            number=number,
            tolerance=tolerance,
            drawn_by=drawn_by,
            pmi=pmi.value,
        )
        print(py_path)
        return

    dwg = build_drawing(
        step_file=step_file,
        out=out,
        title=title,
        number=number,
        tolerance=tolerance,
        drawn_by=drawn_by,
        scale=scale,
        page=page,
        pmi=pmi.value,
    )
    for path in _emit(dwg, formats):
        print(path)
