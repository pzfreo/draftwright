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
For a shape, `source="original.step"` supplies the original document's PMI while
keeping the shape's geometry. As in 0.4, the caller asserts that relationship;
the Rust PMI reader reports face correlations it cannot establish. For a STEP
path, that path already supplies the source document.

## Supported arguments

The old positional argument order is preserved. Supported keywords author the
same [Spec](model.md) that `draftwright render` uses. Rust's automatic drawing
policies apply; accepting an old default does not select the Python 0.4 solver.

| Keyword | Mapping / accepted values |
| --- | --- |
| `out` | CLI output stem; str or PathLike |
| `title`, `number`, `material`, `drawn_by`, `date`, `revision`, `company`, `tolerance`, `approved_by`, `document_type`, `sheet` | `sheet.title.<keyword>`; `None` leaves the Rust default |
| `scale` | CLI numeric scale conversion (`0.5` → `1:2`); `None` is automatic |
| `page` | `sheet.size`: `A4`, `A3`, `A2`, `A1`, `A0`, or automatic (`None`) |
| `projection` | `sheet.projection`: `first`, `third`, or default (`None`) |
| `zones` | `sheet.zones` |
| `auto_dims` | `False` selects empty authored dimensions and disables centre lines/marks; `True` uses Rust automatic dimensions |
| `pmi` | `annotate` → `first`; `off`/`report` → `ignore` (inventory without PMI annotations); `None` uses the Rust default |
| `frame` | Both values accepted; Rust always draws the ISO frame |
| `source` | Original STEP document's PMI for an in-memory shape |

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

Custom `page` dimensions also raise `TypeError`. Invalid supported values, such
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

This is a small API over the Rust engine, not the archived Python implementation.
`Drawing`, `Sheet`, `build_drawing` and the rest of the old public surface are not
included. Accessing an old name or importing a documented old submodule raises
`AttributeError` with a migration explanation and a link to the Spec docs.
Unknown names retain Python's normal errors. Pin `draftwright<0.6` to continue
using the old API. `draftwright.__version__` is the Cargo package version.

The wheel contains an abi3 extension (`draftwright._native`) and Python wrappers.
The published Rust crate has no PyO3 dependency. `cargo install draftwright`
continues to install the two native commands.
