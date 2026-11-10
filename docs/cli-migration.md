# Moving from Python draftwright

The Rust wheel is CLI-only and replaces Python `draftwright` (0.4.x and 0.5.0rc).
PyPI publishing begins with 0.6.0. Users of the Python API should pin `draftwright<0.6` for now.

`draftwright part.step [options]` renders through the same native engine as
`draftwright render part.step -o output`. Both forms accept the following common options;
explicit options override the corresponding fields of `render --spec spec.json`.

| Options | Rust destination |
| --- | --- |
| `--title`, `--number`, `--material`, `--drawn-by`, `--revision`, `--date`, `--company`, `--approved-by`, `--tolerance` | `sheet.title` fields (`drawn_by`, `approved_by` use underscores) |
| `--out` | Output prefix; relative paths use the working directory |
| `--out-dir` | Output directory, created if needed, with the input filename stem |
| `--scale` | Numeric factor converted to `sheet.scale`, e.g. `0.5` becomes `1:2` |
| `--frame` | Rust's existing ISO sheet frame and clearance (always drawn) |

The bare form defaults to an output prefix beside the input, like Python. `--out` and
`--out-dir` are mutually exclusive; `.pdf`, `.svg`, `.png` or `.dxf` suffixes on a bare
`--out` are stripped. The explicit `render` form still requires `-o`/`--out` or `--out-dir`.

Subcommand names always win, even if a file of that name exists in the working directory.
Use `draftwright ./render` for a file called `render`; `draftwright render` is the subcommand.
Options can precede the bare filename. Top-level `--help` describes the subcommands;
`draftwright part.step --help` describes the common subset.

Differences from Python's help/defaults (checked against the old CLI on 2026-11-09):

- Rust writes SVG **and** PDF and prints a JSON result to stdout, with no automatic JSON
  sidecar. Python defaults to PDF plus its JSON sidecar. For specific formats use
  `draftwright render part.step -o output --formats pdf`.
- Unspecified title fields follow the Rust Spec and STEP identity rules. Rust does not insert
  Python's `DWG-001` drawing number or revision `A`. Omitted material and general tolerance
  follow Rust's STEP-source rules. `--tolerance` sets title-block text, not dimension tolerances.
- Rust always frames the sheet and defaults to ISO zones. Python defaults to no frame or zones.
  `--frame` requests that existing Rust behaviour; it adds no separate Spec field.
- A positive numeric scale is honoured under Rust's Spec scale and layout rules. It does not
  invoke Python's `--scale-policy fallback`. Ratios have at most six decimal places, with
  trailing zeros removed; factors needing longer ratios (such as `0.3`) are refused with a
  clean-ratio message. Invalid, nonfinite, nonpositive and overflowing factors are refused.
- Native recognition, PMI and automatic paper/layout follow Rust defaults, including its
  supported paper sizes. They do not reproduce Python's off-by-default PMI or layout algorithms.

Every other Python option is refused. Where a Rust control exists the error names it:
`--document-type` and `--sheet` use `render --spec` title fields; `--projection`, `--zones`,
`--page` and `--pmi` use Spec fields; `--format`/`-f` uses `render --formats` (including editable DXF);
completion installation/display uses `draftwright completions <shell>`.
There is no equivalent CLI option for `--margin-left`, `--margin-right`, `--margin-top`,
`--margin-bottom`, `--title-block-width`, `--projection-symbol`, `--no-projection-symbol`,
`--text-position`, `--text-orientation`, `--leader-region`, `--annotation-layout`,
`--scale-policy`, `--script`, `--style`, `--no-report`, `--no-progress` or `--verbose`/`-v`.
These receive an explicit “no Rust equivalent” refusal; none is silently ignored.
