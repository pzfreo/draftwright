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

app = typer.Typer(
    add_completion=True,
    # Plain tracebacks on an engine error, matching the old argparse CLI — a rich
    # source-panel traceback buries clean domain errors (e.g. "could not read STEP
    # file") in developer noise.
    pretty_exceptions_enable=False,
    help="Zero-AI STEP -> technical drawing (PDF by default; SVG/DXF/PNG via --format, "
    "or an editable .py script).",
)

# Output formats the --format selector understands ('all' expands to all four).
_FORMATS = ("pdf", "svg", "dxf", "png")


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
    """Write the requested formats and return their paths in *formats* order. ``export`` handles
    the SVG→PDF→PNG intermediate chain and removes any it wasn't asked to keep."""
    paths = dwg.export(formats=formats)
    return [paths[f] for f in formats]


def _looks_like_object_spec(s: str) -> bool:
    """True when *s* is a ``module:attr`` / ``file.py:attr`` object reference rather than a STEP
    path (#469). An existing file is never a spec; a plain path — a STEP file, or a Windows
    ``C:\\…\\part.step`` drive path — has no trailing ``:identifier``, so the pattern rejects it
    (a Windows drive colon is followed by ``\\``, not an identifier; filenames can't contain ':')."""
    import re

    if os.path.exists(s):
        return False
    return re.match(r"^.+:[A-Za-z_][A-Za-z0-9_]*$", s) is not None


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
    material: str = typer.Option("", help="Material for the title block"),
    date: str = typer.Option("", help="Date for the title block"),
    revision: str = typer.Option("A", help="Revision for the title block"),
    company: str = typer.Option("", help="Company / legal owner for the title block"),
    frame: bool = typer.Option(
        False, "--frame", help="Draw a sheet border; content reserves clearance inside it"
    ),
    projection: str = typer.Option(
        "", "--projection", help="Projection-method symbol: 'third' or 'first' (default: none)"
    ),
    zones: bool = typer.Option(
        False, "--zones", help="Draw the ISO 5457 zone-grid border ruler (implies --frame)"
    ),
    scale: float | None = typer.Option(
        None, help="Drawing-scale override, e.g. 5 for 5:1 or 0.5 for 1:2 (default: auto)"
    ),
    page: str | None = typer.Option(
        None, help="Page-size override: A4..A0 or WIDTHxHEIGHT in mm, e.g. 420x297 (default: auto)"
    ),
    script: bool = typer.Option(
        False,
        "--script",
        help="Write an editable .py drawing script instead of the rendered drawing",
    ),
    style: str = typer.Option(
        "sheet",
        "--style",
        help="--script flavour: 'sheet' (declarative Sheet script - one line per feature, "
        "default) or 'imperative' (edit-verb reconstruction)",
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
        help="Comma-list of output formats: pdf, svg, dxf, png (or 'all'). E.g. --format pdf,png",
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

    formats = _parse_formats(output_format)
    if script and style not in ("imperative", "sheet"):
        # validate before the ~5 s engine import so a typo fails fast
        raise typer.BadParameter("--style must be 'imperative' or 'sheet'", param_hint="--style")

    # Import the engine lazily, only on the build path: it pulls in build123d/OCP
    # (~5 s of CAD-kernel import). Keeping it out of module scope means shell
    # completion, --help and --version (which import this module but never call
    # the command) stay sub-second instead of paying for the kernel every time.
    from draftwright.builder import build_drawing, generate_script

    if script:
        if style == "sheet":
            from draftwright.sheet_emit import generate_sheet_script, resolve_object_spec

            # The Sheet script now carries the title-block / layout aspects (#474), so forward all
            # four flags — the generated script reproduces them on re-run (no more inert warning).
            if _looks_like_object_spec(step_file):
                # STEP_FILE is a `module:attr` / `file.py:attr` spec → reference a live object
                obj, seam = resolve_object_spec(step_file)
                py_path = generate_sheet_script(
                    obj,
                    out=out,
                    title=title,
                    number=number,
                    tolerance=tolerance,
                    drawn_by=drawn_by,
                    scale=scale,
                    page=page,
                    material=material,
                    date=date,
                    revision=revision,
                    company=company,
                    frame=frame,
                    zones=zones,
                    projection=projection or None,
                    part_expr=seam,
                    formats=tuple(formats),
                )
            else:
                py_path = generate_sheet_script(
                    step_file,
                    out=out,
                    title=title,
                    number=number,
                    tolerance=tolerance,
                    drawn_by=drawn_by,
                    scale=scale,
                    page=page,
                    material=material,
                    date=date,
                    revision=revision,
                    company=company,
                    frame=frame,
                    zones=zones,
                    projection=projection or None,
                    pmi=pmi.value,
                    formats=tuple(formats),
                )
        else:
            if _looks_like_object_spec(step_file):
                raise typer.BadParameter(
                    "the imperative reconstruction reads a STEP file; use --style sheet "
                    "(the default) to reference a 'module:attr' object",
                    param_hint="--style",
                )
            py_path = generate_script(
                step_file=step_file,
                out=out,
                title=title,
                number=number,
                tolerance=tolerance,
                drawn_by=drawn_by,
                pmi=pmi.value,
                scale=scale,
                page=page,
                formats=tuple(formats),
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
        material=material,
        date=date,
        revision=revision,
        company=company,
        frame=frame,
        projection=projection or None,
        zones=zones,
    )
    for path in _emit(dwg, formats):
        print(path)
