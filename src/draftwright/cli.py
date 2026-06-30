"""cli — the draftwright command-line interface (Typer, #289).

A thin Typer front-end over the build engine (`builder.build_drawing` /
`generate_script`): it owns argument parsing, ``--version``, shell completion,
and rich-formatted help, then calls down into the engine. The engine stays
headless — no presentation concern leaks below this module, so this is also the
home the event-stream / TUI work (#276) wraps its sink + renderer around.
"""

from __future__ import annotations

import logging
from enum import Enum
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path

import typer

from draftwright.builder import build_drawing, generate_script

app = typer.Typer(
    add_completion=True,
    help="Zero-AI STEP → technical drawing (SVG + DXF, or an editable .py script).",
)


class PmiMode(str, Enum):
    """AP242 PMI handling mode (mirrors the engine's ``pmi`` argument)."""

    off = "off"
    report = "report"
    annotate = "annotate"


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
    step_file: Path = typer.Argument(..., help="Input STEP file (.step / .stp)"),
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
    pdf: bool = typer.Option(
        False, "--pdf", help="Also write a PDF (requires draftwright[pdf] / cairosvg)"
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

    step = str(step_file)
    if script:
        py_path = generate_script(
            step_file=step,
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
        step_file=step,
        out=out,
        title=title,
        number=number,
        tolerance=tolerance,
        drawn_by=drawn_by,
        scale=scale,
        page=page,
        pmi=pmi.value,
    )
    svg_path, dxf_path = dwg.export()
    print(svg_path)
    print(dxf_path)
    if pdf:
        print(dwg.export_pdf())
