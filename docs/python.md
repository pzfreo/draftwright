# Python

Install the platform wheel (Python 3.9 or newer; Rust is not required):

```sh
pip install draftwright
```

```python
from pathlib import Path
from draftwright import make_drawing, DraftwrightError

svg_path, pdf_path = make_drawing(Path("part.step"), out="drawing", title="Bracket")
```

**The return value in 0.6 is `(svg_path, pdf_path)`, both strings. In 0.4 it was
`(svg_path, dxf_path)`. There is no DXF export in 0.6.** Both files (and additional
sheets, if needed) follow the CLI's bare-render naming rules. An output extension
such as `.pdf` is removed from the stem. Without `out`, files go beside the input
STEP; for a shape the default is `drawing` in the working directory. Exceptions
from Rust are `draftwright.DraftwrightError`, carrying the Rust error message.

A build123d `Shape` also works. Install build123d separately if your script needs
it; Draftwright does not depend on or import it for STEP paths. The wrapper uses
build123d's `export_step` in a temporary directory, removed after rendering.
For either a shape or a STEP path, `source="original.step"` supplies the original
STEP document's PMI while keeping the input's geometry. The caller asserts that
relationship; the Rust PMI reader reports face correlations it cannot establish.
An unreadable explicit `source` raises `DraftwrightError`, including for path
inputs. Without `source`, a STEP input supplies its own document.

## Supported arguments

The old positional argument order is preserved. Supported keywords author the
same [Spec](model.md) that `draftwright render` uses. Rust's automatic drawing
policies apply; accepting an old default does not select the Python 0.4 solver.

| Keyword | Mapping / accepted values |
| --- | --- |
| `out` | CLI output stem; str or PathLike |
| `title`, `number`, `material`, `drawn_by`, `date`, `revision`, `company`, `tolerance`, `approved_by`, `document_type`, `sheet` | `sheet.title.<keyword>`; `None` leaves the Rust default |
| `scale` | CLI numeric scale conversion (`0.5` → `1:2`); `None` is automatic |
| `page` | `sheet.size`: `A4`–`A0`, `ANSI A`–`ANSI E`, `ARCH A`–`ARCH E` (case-insensitive, whitespace trimmed), or automatic (`None`) |
| `units` | Printed lengths in `"mm"` (default) or decimal inches (`"in"`); model geometry remains mm. Automatic paper defaults to ISO/ANSI respectively. |
| `projection` | `sheet.projection`: `first`, `third`, or default (`None`) |
| `zones` | `sheet.zones` |
| `auto_dims` | `False` selects empty authored dimensions and disables centre lines/marks; `True` uses Rust automatic dimensions |
| `pmi` | `None`/`off`/`report` → `ignore` (inventory without PMI annotations, preserving 0.4's off default); only `annotate` → `first` opts into PMI annotations |
| `frame` | Both values accepted; Rust always draws the ISO frame |
| `source` | Original STEP document's PMI for a shape or STEP path; unreadable paths are errors |

Unsupported keywords accept **only their old defaults**. A non-default raises
`TypeError` naming the keyword and saying it is not supported in 0.6:

| Keyword | Accepted default |
| --- | --- |
| `detail_view`, `reproducible`, `projection_symbol` | `True` |
| `assembly`, `margin_left`, `margin_right`, `margin_top`, `margin_bottom`, `title_block_width` | `None` |
| `framed_recognition` | `False` |
| `scale_policy` | `"fallback"` |
| `text_position` | `"inline"` |
| `text_orientation` | `"aligned"` |
| `leader_region` | `"auto"` |
| `annotation_layout` | `"demand-guided"` |

Custom `page` dimensions also raise `TypeError` naming the unsupported value. Invalid supported values, such
as a nonpositive scale or an unknown projection, raise `DraftwrightError` from
Rust. Unknown keywords raise Python's normal `TypeError`.

## Commands and interrupts

`pip install` also installs `draftwright` and `draftwright-mcp` on PATH:

```sh
draftwright part.step --out drawing
draftwright render part.step -o drawing --formats svg,pdf
draftwright completions bash
draftwright-mcp --version
```

They pass `sys.argv` to the same Rust functions as the Cargo binaries, release
the GIL, and return the Rust exit code (0 success, 1 failure, 2 invalid CLI/request).
The console adapters install `SIG_DFL` for SIGINT before entering Rust. Ctrl-C
therefore terminates immediately, including during rendering and a blocked MCP
stdio read (POSIX signal termination, conventionally shell status 130; Windows
uses the console's default interrupt handling). A killed render can leave files
already completed; each file uses the engine's normal write behaviour.

Library `make_drawing` also releases the GIL, but leaves the calling application's
signal handlers alone. With Python's default handler, `KeyboardInterrupt` can be
deferred until the native call returns. Applications needing immediate process
cancellation can run their drawing in a subprocess.

## Migrating from Python 0.4


Visible changes from 0.4:

- No DXF export: the second returned string is a PDF path, so unpacking now gives
  `(svg_path, pdf_path)`.
- The sheet frame is always drawn, even with `frame=False`; the projection
  symbol is always drawn as well.
- Unsupported keywords accept only their old defaults. Non-default values raise
  `TypeError` instead of being ignored; the full list is in the table above.
  Custom page dimensions are unsupported, while ISO names retain case-insensitive
  parsing (`page=" a3 "` works).
- The Rust automatic layout, view and scale policies replace the old Python
  solver. Accepting old option defaults does not restore that solver.
- The old classes and drawing-editing API are removed, apart from `make_drawing`.

PMI's default is deliberately preserved: omitted `pmi`, `pmi=None`, `pmi="off"`
and `pmi="report"` all inventory the source without drawing PMI annotations.
Use `pmi="annotate"` to opt in, including when `auto_dims=False`.

This is a small API over the Rust engine, not the archived Python implementation.
`Drawing`, `Sheet`, `build_drawing` and the rest of the old public surface are not
included. Removed attributes raise an `AttributeError`-compatible migration error;
removed imports raise an `ImportError`-compatible error with the same explanation
and a link to the Spec docs. This preserves both `except ImportError` fallbacks
and `hasattr(draftwright, "Sheet") is False`. Every one of the old package's 88
top-level modules/packages is covered, including fonts, recognition and annotate.
Unknown package names retain Python's normal errors.

CPython 3.9 permits an exception inheriting from both `ImportError` and
`AttributeError`; the compatibility layer uses that type there. CPython 3.10+
rejects those two bases with an instance-layout conflict. On those interpreters,
separate attribute/import exception classes preserve the respective protocols
and share a migration-error base and message.

The supported function remains available through the old builder import:

```python
from draftwright.builder import make_drawing
```

Other builder names produce the migration error. Other removed modules point to
`from draftwright import make_drawing` for the supported one-liner.

Pin `draftwright<0.6` to continue
using the old API. `draftwright.__version__` is the Cargo package version.

The wheel contains an abi3 extension (`draftwright._native`) and Python wrappers.
The published Rust crate has no PyO3 dependency. `cargo install draftwright`
continues to install the two native commands.
