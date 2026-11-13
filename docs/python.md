# Python

Install the platform wheel (Python 3.9 or newer; Rust is not required):

```sh
pip install --pre draftwright
```

```python
from pathlib import Path
from draftwright import make_drawing, DraftwrightError

svg_path, dxf_path = make_drawing(Path("part.step"), out="drawing", title="Bracket")
```

**The return value is `(svg_path, dxf_path)`, both strings, as in 0.4.** The DXF is
editable in CAD: native text, live dimensions, blocks and title-block attributes. For PDF
or PNG use `draftwright render --formats`. Both files (and additional
sheets, if needed) follow the CLI's bare-render naming rules. An output extension
such as `.dxf` is removed from the stem. Without `out`, files go beside the input
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

## Requirements and drawings from build123d (`Part`, 0.6.0-alpha.2)

State design intent where you build the faces. `Part` records each call as a
[Specify](https://github.com/pzfreo/specify-core) answer (`datum.*`, `part.*`, `extra.*`),
names the faces you pointed at in the exported STEP and writes the answers beside it.
`import draftwright` still never imports build123d; only `Part` needs it.

```python
from build123d import *
from draftwright import Part

with BuildPart() as plate:
    Box(120, 80, 20)
    bore = CounterBoreHole(radius=6, counter_bore_radius=10, counter_bore_depth=5)
    with GridLocations(90, 50, 2, 2):
        mount = Hole(radius=3.3)

p = Part(plate.part, "plate", number="DWG-002", title="Mounting plate")
p.process("machined").material("6082-T6").general_tolerance("ISO 2768-mK")

A = p.datum("A", plate.faces().sort_by(Axis.Z)[0])
B = p.datum("B", plate.faces().sort_by(Axis.X)[0])
C = p.datum("C", plate.faces().sort_by(Axis.Y)[0])

p.hole(bore).fit("H7").finish(1.6)           # on the bore, not the counterbore
p.holes(mount).position(0.1, A, B, C)
p.face(plate.faces().sort_by(Axis.Z)[-1]).flatness(0.05)

p.export_step("plate.step")   # plate.step, plate.answers.json, plate.pending.json
dwg = p.drawing(sheet="A3").views(shaded=True)
svg, dxf = dwg.render("plate")   # layout only; same (svg, dxf) as make_drawing
```

- **Objects expand to faces.** A face, a list of faces, or any shape (a hole's cutter, as
  above) is matched to the part's own faces: faces of the part are taken as given; a
  cutter or solid matches the part faces it coincides with (same surface, inside its
  bounding box). Nothing matching raises `ValueError`, as does `hole()` on several holes
  (use `holes()`), a fit on a shape with no hole (a boss or shaft), an unknown or shaft fit class,
  a list holding faces that are not the part's, a fit and a
  thread on one hole, a repeated datum, and a face name that would land on two faces.
- **Face names** are `f:<part>.<label>.<n>`: `label` is `label=` if given, else
  `datum_A`, `hole1`, `face1`... by a counter, and `n` follows the part's face order, so
  they repeat across runs. build123d's `export_step`
  names assemblies and nodes but not sub-shapes (a test pins this), so `Part` writes the
  STEP with OCCT's writer and sets the name of each referenced face's `ADVANCED_FACE`.
  (OCCT's XCAF sub-shape route, `write.stepcaf.subshapes.name`, loses the names of faces
  that carry a location, such as a filleted box's planes.)
- **`<stem>.answers.json`** is a plain Specify answer map; today's `specify-core
  questions/write` and the editor read it. Face numbers are the faces' order in the
  written STEP, so it belongs to that file. It carries no `_specify` evidence (that needs
  Specify's analysis). Hole finish, and flatness and finish on a face, are written as
  `extra.*` requirements (on a hole, its bore cylinder, not the counterbore).
- **`<stem>.pending.json`** (`"schema": "draftwright.pending/1"`) holds what Specify keys to
  *recognised features*: a fit (`hole.function: fit:H7`), a thread (`hole.function:
  tapped:M6`) and `hole.position`/`hole.frame`. A fit is not written as an `extra.*`,
  because Specify asks `hole.function` itself and the two would contradict. They are
  listed by face name and applied by the Rust half. Nothing is dropped silently.
- **`drawing()`** edits layout only: `sheet=`, `scale=`, `views(shaded=, omit=)`,
  `section(id, through=<feature id string>)`. `spec()` returns the Spec. Title and number
  come from `Part`; requirements are never written to the Spec.
- **`check()` and `answer()`** raise `NotImplementedError`: they need Specify's question
  queue and feature recognition, which remain work for the Rust half of the API.
  The same half will accept `section(through=<object>)` and turn `hole`/`holes` into
  `hole.*` answers keyed to the recognised feature, with the "one recognised feature"
  check.

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

- The return value is still `(svg_path, dxf_path)`, both strings, and the DXF is now
  editable in CAD. 0.6.0-alpha.1 briefly returned `(svg_path, pdf_path)`.
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

This is a small API over the Rust engine. The earlier Python implementation is being retired.
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
The published Rust crate has no PyO3 dependency. `cargo install draftwright --version 0.6.0-alpha.2`
continues to install the two native commands.
