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

State requirements where you build the faces. `Part` owns an in-memory
`haecceity::pmi::PartPmi`, the same semantic model the engine reads from AP242.
Drawing lowers that model directly; requirements never go into the drawing Spec.
No questionnaire, answer files or separate package is needed.

build123d is optional. Install it alongside Draftwright, for example
`pip install --pre draftwright build123d`, or use
`uv run --with draftwright --with build123d script.py`. A pipx/uv-tool environment
needs build123d in the same environment to use `Part`. Importing Draftwright does
not import build123d or OCP.

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
bearing = p.hole(bore).fit("H7").finish(1.6)
p.holes(mount).position(0.1, A, B, C)
p.face(plate.faces().sort_by(Axis.Z)[-1]).flatness(0.05)

dwg = p.drawing(sheet="A3").views(shaded=True)
dwg.title(drawn_by="PA", date="2026-11-14", revision="B")
svg, pdf = dwg.render("plate", formats=("svg", "pdf"))
print(dwg.problems)  # the engine's findings as dictionaries
```

The mapping uses haecceity's native types:

| Part call | In-memory semantic PMI |
| --- | --- |
| `datum(letter, face)` | `Datum` on a face-anchored `Feature` |
| `hole(...).fit("H7")` | Diameter `Dimension` with ISO 286 `DimTolerance::Fit` |
| `hole(...).thread("M3x0.5-6H")` | Internal `Thread` plus its `thread manufacturing` pitch attribute |
| `holes(...).position(0.1, A, B, C)` | `GeometricTolerance::Position`, cylindrical zone and ordered `DatumSystem` |
| `face(...).flatness(0.05)` | `GeometricTolerance::Flatness` |
| `face(...).finish(1.6)` / `hole(...).finish(1.6)` | `SurfaceTexture` with Ra in micrometres |
| `material(text)` | `Material` |
| `general_tolerance(text)` | `GeneralTolerance::Class` with the stated text |
| `process(text)` | Part-owned `AttributeSet` named `manufacturing process`; shown in notes and used for recognised process policies |

Metric thread notation uses the engine's existing ISO 261 coarse pitch when none
is stated and internal class `6H` when omitted. The stated size, pitch, fit class and hand are kept.
The renderer matches the stated faces to canonical holes and measures their drill
geometry; it does not add a full-thread-depth requirement. The kernel requires a thread count; this API uses its existing single-thread
convention (`number_of_threads = 1`), keeping pitch separately in the manufacturing
attribute rather than deriving it from that ambiguous field. LH/RH is preserved.

Objects expand to faces. A face, face list or cutter shape matches the part's own
faces. A counterbore's fit and finish apply to its smallest bore. `hole()` refuses
several holes; `holes()` accepts a group. A fit on a boss/shaft, an unknown fit class,
a fit and thread on one hole, an undeclared datum, or a duplicate face name raises
an error. Native PMI validates references before rendering. Shaft fits, external
threads, new requirement kinds and Pack remain a later stream.

`export_step(path)` writes **plain geometry**, product metadata and referenced face
names. It does not write PMI or sidecars. Its return value is `{"step": Path,
"faces": {name: zero_based_face_number}}`. Face names are `f:<part>.<label>.<n>`;
`label=` supplies the label, otherwise one is generated deterministically. The
STEP product ID/name use the Part's number/title when supplied, otherwise its name.
AP242 PMI export is deferred.

Rendering does not call `export_step()` or create a STEP file. OCCT geometry is
transferred to the Rust kernel once as in-memory STEP geometry bytes, with an
exact face-name/index map. The native shape is cached, and requirement edits
change its separate native PMI model without re-exporting geometry. No PMI is
serialized to STEP or read back to draw it. A direct OCCT-handle import is not
available in the pure Rust kernel.

`drawing()` edits layout: `sheet=`, `scale=`, `units=`, `views(shaded=, omit=)` and
`section(id, through=...)`. Sections accept a fluent reference, build123d object,
or drawing feature ID. The bridge to stable drawing keys is:

```python
feature = p.feature_id(bearing)  # f:hole:... from the drawing's Analysis
bottom_key = p.reference_key(plate.faces().sort_by(Axis.Z)[0])  # face:v1:...
dwg.section("A-A", through=bearing)
```

`feature_id()` requires one unambiguous recognised feature; `reference_key()`
requires one geometry face. Unresolved/ambiguous references raise an error with
what was found.

`Drawing.update(spec_dict)` deep-merges dictionaries; scalars and lists replace whole.
It validates the merged result against the published [Spec schema](../schemas/spec.json)
and typed Rust Spec before changing the builder. `spec()` returns an independent copy.
`render(spec=...)` applies the same merge for that render only:

```python
dwg.update({"sheet": {"zones": False}, "views": {"iso": {"sheet": 2}},
            "sheets": {"max": 2}})
dwg.render("review", formats=("pdf",),
           spec={"sheet": {"title": {"revision": "C"}}})
```

Use `title(**fields)` or `update({"sheet": {"title": {...}}})` for `title`, `number`,
`drawn_by`, `date`, `revision`, `company`, `approved_by`, `document_type` and other
schema title fields. Requirement keys such as material, tolerance, process,
fit/thread/finish, GD&T and requirement notes are refused with guidance to the Part
calls. Placement, omission, dimension presentation and recognition corrections are
layout edits.

`render()` defaults to `(svg_path, dxf_path)`. `formats=("svg", "pdf", "png", "dxf")`
selects any distinct subset and returns a tuple of **all** written paths, in format
order. SVG, PNG and DXF have one file per sheet (`name.sheet2.svg`, etc.); PDF is
one document containing every sheet. PNG uses the engine's default resolution.
`drawing.problems` contains the most recent successful render's findings.

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

<a id="migrating-from-python-0-4"></a>

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
