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
| `shaft(...).fit("h6")` / `cylinder(boss).fit("g6")` | External diameter `Dimension` with ISO 286 `DimTolerance::Fit` |
| `hole(...).thread("M3x0.5-6H")` / `shaft(...).thread("M10x1.5-6g")` | Internal/external `Thread` plus its `thread manufacturing` pitch attribute |
| `cylinder(...).diameter(plus_minus=0.02)` / `.diameter(limits=(9.98, 10.04))` | Diameter `Dimension` with `DimTolerance::Deviations` / `Limits`; also on `hole`, `shaft` and cylindrical `face` refs |
| `distance(face1, face2).tolerance(plus_minus=0.03)` / `.tolerance(limits=(29.98, 30.04))` | `DimensionKind::Location` (`LinearDistance`) with deviations / limits |
| `holes(...).position(0.1, A, B, C)` | `GeometricTolerance::Position`, cylindrical zone and ordered `DatumSystem` |
| `face(...).flatness(0.05)` | `GeometricTolerance::Flatness` |
| `face(...).perpendicularity(0.05, A)` / `.parallelism(0.05, A)` | `GeometricTolerance::Perpendicularity` / `Parallelism` |
| `shaft(...).cylindricity(0.02)` | `GeometricTolerance::Cylindricity`, without datums |
| `shaft(...).circular_runout(0.03, A)` / `.total_runout(0.04, A)` | `GeometricTolerance::CircularRunout` / `TotalRunout` |
| `face(...).profile_surface(0.05, A)` | `GeometricTolerance::SurfaceProfile`; datums optional |
| `shaft(...).concentricity(0.02, A)` | `GeometricTolerance::Concentricity`, cylindrical zone |
| `shaft(...).knurl(0.8, "straight", teeth=39)` | Native `Knurl` with pitch, maximum diameter and tooth count; also `"diamond"` with `helix_angle=` |
| `face(...).finish(1.6)` / `hole(...).finish(1.6)` | `SurfaceTexture` with Ra in micrometres |
| `material(text)` | `Material` |
| `general_tolerance(text)` | `GeneralTolerance::Class` with the stated text |
| `process(text)` | Part-owned `AttributeSet` named `manufacturing process`; shown in notes and used for process policies |

`process()` accepts `machined`, `3D printed: FDM`, `3D printed: resin`,
`3D printed: powder`, `3D printed: metal`, `sheet metal`, `moulded`, and `cast`.
The aliases `cnc-milled` and `cnc-turned` map to `machined` in both PMI and policy.
Other values raise `ValueError` listing the accepted values.
Repeated size, fit, thread, knurl, finish or geometric control calls on the same
faces replace that declaration; different control kinds remain independent.
`fit()` and `diameter()` describe the same size: on the same cylinder each replaces
the other's tolerance, including when called through a new fluent handle.
`general_tolerance(text)` deliberately accepts **free text**, including `"±0.05"`
and `"ISO 2768-mK"`. It prints the stated text in the title block; it does not
validate ISO 2768 classes or apply that text as individual dimension tolerances.
This in-memory API is independent of Specify's answer-file class restrictions.

Metric thread notation uses the engine's existing ISO 261 coarse pitch when none
is stated and class `6H` internally or `6g` externally when omitted. The stated
size, pitch, fit class and hand are kept.
Thread classes accept one or two grade/position pairs: grades 4–8 with `G`/`H`
internally, grades 3–9 with `e`/`f`/`g`/`h` externally (e.g. `6H`, `6g`, `5g6g`).
Unknown classes and classes of the wrong case raise at the call. The measured
external diameter must match the thread major diameter within 0.01 mm. Internal
threads require a measured drill diameter between major − 1.5 × pitch and major,
with 0.01 mm slack at each end, matching the engine's thread checks. A mismatch
on any selected cylinder raises immediately.
For internal threads the renderer matches the stated faces to canonical holes
and measures their drill geometry; it does not add a full-thread-depth requirement.
External threads cover the full length of the selected continuous cylinder,
recorded in its `thread manufacturing` attribute and drawn with ISO 6410 lines.
The kernel requires a thread count; this API uses its existing single-thread
convention (`number_of_threads = 1`), keeping pitch separately in the manufacturing
attribute rather than deriving it from that ambiguous field. LH/RH is preserved.
The engine's compact callout convention omits ISO 261 coarse pitch and right hand
(e.g. `M10-6g`); fine pitch and left hand remain explicit. For the full designation,
use the layout setting `drawing.update({"sheet": {"callouts": {"style": "full"}}})`.
The native PMI retains the pitch and hand with either presentation.

Objects expand to faces. A face, face list or cutter shape matches the part's own
faces. A counterbore's fit and finish apply to its smallest bore. `hole()` refuses
several holes; `holes()` accepts a group. `shaft(obj)` selects an external cylinder
(including a boss); `cylinder(obj)`
accepts either side. These require one coaxial cylindrical size; a stepped shaft
must be selected one cylindrical face at a time. ISO 286 classes use the native
28 deviation letters, lower-case for shafts and upper-case for holes, and grades
01, 0–18. An unknown class, wrong target type, a fit/diameter tolerance and thread
on one cylinder, an undeclared datum, or a duplicate face name raises at the call.
A thread and knurl require separate cylinders. Native PMI validates references
before rendering.

Sizes are measured from the selected geometry in mm; `plus_minus` is a positive
symmetric deviation, and `limits=(lower, upper)` gives absolute sizes containing
the measured nominal. Supply exactly one. `distance` takes two distinct parallel
planar faces normal to X, Y or Z, including `face(...)`/datum handles. It states
the perpendicular separation of their support planes. Oblique and angular
lengths are outside this API. Diameters require cylindrical faces. Perpendicularity
and parallelism accept planes or cylinders; cylindricity and concentricity require
cylinders; runout accepts planes or cylinders. These controls are also available
on hole/cylinder handles. Perpendicularity, parallelism, runout and concentricity
require at least one declared datum; surface profile takes optional datums;
cylindricity takes none. Datum order is preserved. Any datum sharing any face with
the controlled feature is refused at the call; use a separate datum
surface or cylinder. Perpendicularity and parallelism on a cylinder control its
axis in a cylindrical `⌀` zone; on a plane they use a two-planes zone.

Straight and diamond knurls draw the engine's manufacturing suffix and depiction.
Pitch is linear mm, and the measured diameter is the maximum diameter after
knurling. `teeth=` must agree with π × diameter / pitch within one tooth; no tooth
count is invented. Diamond requires `helix_angle=` in degrees (0 < angle < 90),
which native PMI retains; the current drawing prints the pattern and pitch, not
that angle. A straight knurl refuses `helix_angle`. Diagonal knurls are refused
because the engine does not lower them.
Standalone chamfer requirements are omitted: the engine has no chamfer PMI form
that it draws (geometry recognition can still dimension modelled chamfers).
AP242/Specify mapping and Pack remain deferred.

The following complete example renders each new requirement on a real turned
cylinder. Each sheet has its own Part so the fit, thread, knurl and numeric size
requirements use separate cylinders. It runs with build123d 0.13.0:

```python
from pathlib import Path
from build123d import Cylinder, GeomType, Axis, Align, Pos
from draftwright import Part

out = Path("requirements")
out.mkdir(exist_ok=True)
for kind in ("shaft_fit", "thread", "diameter_pm", "diameter_limits",
             "length_pm", "length_limits", "perpendicularity", "parallelism",
             "cylindricity", "circular_runout", "total_runout", "profile_surface",
             "concentricity", "straight_knurl", "diamond_knurl"):
    shape = Cylinder(5, 30)  # diameter 10, length 30 mm
    controls = ("perpendicularity", "parallelism", "circular_runout", "total_runout", "concentricity")
    if kind in controls:
        align = (Align.CENTER, Align.CENTER, Align.MIN)
        shape = Cylinder(5, 30, align=align) + Pos(0, 0, -6) * Cylinder(7, 6, align=align)
    p = Part(shape, kind).material("BRASS H62").general_tolerance("±0.05")
    cylinders = shape.faces().filter_by(GeomType.CYLINDER)
    wall = [f for f in cylinders if abs(f.radius - 5) < 1e-6][0]
    ends = shape.faces().filter_by(GeomType.PLANE).sort_by(Axis.Z)
    # The larger, separate cylinder establishes datum A's axis for these controls.
    datum_face = ([f for f in cylinders if abs(f.radius - 7) < 1e-6][0]
                  if kind in ("perpendicularity", "circular_runout", "total_runout", "concentricity")
                  else ends[0])
    A = p.datum("A", datum_face)
    shaft = p.shaft(wall)
    if kind == "shaft_fit":
        shaft.fit("h6")  # p.cylinder(wall).fit("h6") also works on a boss
    elif kind == "thread":
        shaft.thread("M10x1.5-6g")  # "M10" defaults to the same pitch and class
    elif kind.startswith("diameter"):
        shaft.diameter(**({"plus_minus": 0.02} if kind.endswith("pm")
                          else {"limits": (9.98, 10.04)}))
    elif kind.startswith("length"):
        p.distance(ends[0], ends[-1]).tolerance(
            **({"plus_minus": 0.03} if kind.endswith("pm")
               else {"limits": (29.98, 30.04)}))
    elif kind.endswith("knurl"):
        pattern = kind.split("_")[0]
        shaft.knurl(0.8, pattern, teeth=39,
                    **({"helix_angle": 30} if pattern == "diamond" else {}))
    elif kind == "cylindricity":
        shaft.cylindricity(0.02)
    elif kind in ("perpendicularity", "parallelism", "profile_surface"):
        getattr(p.face(ends[-1]), kind)(0.05, A)
    else:
        getattr(shaft, kind)(0.03, A)
    p.drawing(sheet="A3").render(out / kind, formats=("svg",))
```

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
what was found. These lookups do not name raw objects, change face names, or
invalidate cached geometry.

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
`drawing.problems` contains the most recent successful render's findings. Every dictionary
includes the engine-owned `tier`: `fix_before_printing`, `tidy`, or `information`.
See the [code-to-tier table](finding-impact.md), including its explicit severity-dependent rules.

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
