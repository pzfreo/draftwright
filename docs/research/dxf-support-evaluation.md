# What proper DXF support would take

**Evaluation only — no implementation is proposed for adoption here.** Measured against
`main` at `a7497f5`, draftwright 0.4.28.dev0, ezdxf as pinned.

## 1. What draftwright writes today

`Drawing._write_dxf` builds a `build123d.ExportDXF` from the *rendered shapes* on three layers
and writes it. Everything on the sheet is already geometry by that point, so the exporter has
only geometry to give.

Probe: a 90 × 64 × 38 block with one ⌀16 through hole, one authored dimension set, A3.

```
entities by type   LINE 1020 · SPLINE 635 · CIRCLE 2 · ELLIPSE 6 · ARC 8
by layer           dims    LINE 978 · SPLINE 625 · ARC 8
                   hidden  LINE  21 · SPLINE  10 · CIRCLE 1 · ELLIPSE 3
                   part    LINE  21 · CIRCLE  1 · ELLIPSE 3
layers             0, Defpoints, dims, hidden, part
dimstyles          Standard (unused)     textstyles  Standard (unused)
blocks             none                  paperspace  Layout1, empty
$INSUNITS 4 (mm)   $MEASUREMENT 1 (metric)   DXF 2013 (AC1027)
file size          361 KB
```

Three facts follow, and they are the whole problem:

- **There is no text.** Not one `TEXT` or `MTEXT`. The 625 splines on `dims` are glyph
  outlines — every digit of every dimension is a set of Bézier curves. That is also where the
  361 KB goes; the part itself is 25 entities.
- **There are no dimensions.** Not one `DIMENSION`. A dimension is a line, two arrowheads and
  a cloud of glyph splines that happen to sit near it.
- **The geometry is in page coordinates in model space.** Extent `x 30..409, y 10.9..237.5` on a
  420 × 297 sheet. Measuring anything in the file returns *page* millimetres, so on a 1:2
  drawing every measured length is half the part. Paper space is empty.

So the current export is a **faithful vector picture of the sheet**, not a drawing. It is
correct as a picture — units are right, layers are separated, and it is reproducible on demand.
It carries no meaning a recipient can use.

## 2. What "proper" means — and for whom

"Proper DXF" is not one thing, because two consumers want incompatible files.

| | **Drafting exchange** | **Fabrication / CAM** |
|---|---|---|
| Who | a draughtsman opening it in AutoCAD, QCAD, LibreCAD, Fusion | a laser/waterjet/router shop, a nesting tool |
| Wants | text, dimensions, layers, title block, paper space | closed profiles at 1:1 model scale, one layer, **no annotation at all** |
| Hates | exploded glyphs it cannot restyle or translate | dimension clutter it must delete before nesting |
| Scale | drawing scale, in a viewport | always 1:1, model units |

Today's file serves neither well: too much annotation for CAM, too little meaning for drafting.
**Deciding which of these draftwright is for is the first question, and it is a product
question, not a technical one.** The aspects below are ordered for drafting exchange, which is
the harder of the two and the one the current file is closest to.

## 3. The aspects, and how each could be implemented

### 3.1 Text as text — `TEXT` / `MTEXT` (the highest-value single change)

**What it means.** Every label, note, title-block field and table cell is a `TEXT` or `MTEXT`
entity with a height, rotation, alignment and a named `STYLE`, not an outline.

**Why it matters.** A recipient can select it, edit a value, restyle the sheet, translate it,
search it, and run text-aware tooling. It also removes ~97% of the entities: the probe would
fall from 1671 entities to roughly 60 and from 361 KB to a few tens of KB.

**How.** The data already exists and is already proven, by the PDF path. `Drawing._pdf_text_runs()`
returns `_PDFTextRun(text, x, y, font_size, rotation, font_path, font_name, font_style,
h_align, v_align)` — precisely a `TEXT` entity's parameter list. The work is:

1. emit one `TEXT`/`MTEXT` per run into a `text` (or per-purpose) layer via ezdxf;
2. suppress the corresponding glyph shapes from the shape export, so text is not written twice;
3. register a `STYLE` pointing at a font name, accepting that a recipient without IBM Plex sees
   a substituted face — which is what every CAD file does.

Step 2 is the only fiddly part: the exporter must know which shapes are glyphs. The annotation
registry knows which annotation each shape belongs to, and `_pdf_text_runs` already solves the
same "which of these is text" problem for PDF, including its partial cases (unsupported feature
symbols stay path-only while adjacent text becomes selectable). That precedent is the design.

**Risk.** Low, and reversible behind a flag. The visual result must be verified — a substituted
font changes metrics, so text can overflow a title-block cell that fitted as outlines. The
existing `title_field_overflow` lint measures exactly that and would need to run against the
DXF's own assumptions, or the export must accept that it cannot guarantee the recipient's
rendering.

### 3.2 Dimensions as `DIMENSION` entities

**What it means.** A linear dimension is one `DIMENSION` entity with definition points, a
`DIMSTYLE`, and either its measured value or an override.

**Why it matters.** The recipient can restyle arrows and precision globally, the value is
machine-readable, and — with associative dimensions — it updates if geometry moves. It is the
difference between "a drawing" and "a picture of a drawing" for most CAD users.

**How.** `_core._dim()` already tags every engine-built dimension with
`_dw_spec = SimpleNamespace(p1, p2, side, distance, draft, kwargs)` so the repair loop can
re-place it. Those are the definition points and the dimension-line offset — the same inputs an
ezdxf `add_linear_dim(base=…, p1=…, p2=…)` takes. Add the label (already on the annotation) and
the measurement claim (`registry.measurement_of`) and the entity is fully determined.

Scope honestly: **linear dimensions first.** Radial, diameter, angular and ordinate each have
their own DXF entity and their own definition-point conventions, and draftwright's leaders and
hole callouts are not DXF dimensions at all — they are `LEADER`/`MLEADER`, a separate job.

**Risk.** Medium. Two specific traps:
- **Rendering divergence.** Once it is a `DIMENSION`, the *recipient's* CAD draws the arrows and
  text, not draftwright. The sheet the customer sees is no longer pixel-identical to the PDF,
  and every placement decision draftwright made — the corridor solve, the strip packing, the
  collision repair — is advisory. A `DIMSTYLE` can pin most of it; it cannot pin all of it.
- **Associativity.** True associative dimensions need the geometry to be in the same file and
  referenced. Non-associative (`DIMENSION` with explicit definition points) is far simpler and
  is what most exchange files contain. Start there.

### 3.3 Layers, colours and linetypes

**What it means.** A conventional layer scheme — visible outline, hidden, centre, dimensions,
text, hatch, border/title — each with a colour and, critically, a **linetype**: hidden lines
`DASHED`, centrelines `CENTER`.

**Why it matters.** Today `hidden` is a layer name only: the entities are continuous lines that
merely happen to be drawn dashed in SVG. In the DXF they are solid. A recipient turning on the
`hidden` layer sees solid lines.

**How.** `doc.linetypes.add()` for the ISO patterns ezdxf ships, then set `linetype` on the
layer. The shape export already segregates part/hidden/dims, so the mapping exists; centrelines
and the border currently have no layer of their own and would need one. Small work.

**Risk.** Low. The main question is whether to follow a published scheme (ISO 13567, or a
customer's CAD standard) or draftwright's own names — a decision worth taking once rather than
per-customer.

### 3.4 Blocks for repeated symbols

**What it means.** Surface-finish symbols, GD&T frames, centre marks, datum triangles and the
title block become `BLOCK` definitions inserted with `INSERT`.

**Why it matters.** Editable and restylable as units, far smaller, and conventional. A drawing
with 12 identical hole callouts should not contain 12 copies of the geometry.

**How.** Draftwright's annotation registry already names each annotation and its kind, so the
grouping is known. The work is defining a block per symbol type and emitting `INSERT` with a
transform. It composes with 3.1 — a block containing an attribute (`ATTDEF`/`ATTRIB`) is how
title blocks are conventionally done, and would make the title block's fields individually
editable.

**Risk.** Low-medium. Mostly volume of work, one symbol family at a time.

### 3.5 Section hatching as `HATCH`

**What it means.** Section A–A's ISO hatching is a `HATCH` entity with a pattern name, angle and
scale, over a boundary path — not a bundle of parallel lines.

**Why it matters.** Editable, and enormously smaller. It is also the difference between a
section a recipient can re-hatch and one they must erase.

**How.** ezdxf's `add_hatch` with `set_pattern_fill("ANSI31", …)` and the boundary from the
section face's outer wire, which draftwright already computes to produce the hatch lines.

**Risk.** Low, but boundary construction for multi-region sections with islands needs care —
`HATCH` islands have their own rules.

### 3.6 Model space, paper space, and the scale problem

**What it means.** The part geometry lives in **model space at true model scale** (a 90 mm block
measures 90 mm). The sheet — border, title block, annotations — lives in a **paper-space layout**
sized to the sheet, with one or more `VIEWPORT` entities showing model space at the drawing
scale.

**Why it matters.** This is the one aspect that is arguably a *correctness* problem rather than
a fidelity one. Today, measuring the DXF returns page millimetres. On a 1:2 drawing every
measurement is half the part; on a 5:1 detail, five times. Anyone who opens the file to check a
dimension gets a wrong number unless they know the scale and divide. For a CAM consumer the file
is unusable without rescaling.

**How.** This is the largest and most invasive change, because draftwright's whole layout
pipeline works in page coordinates by design — ADR 2's compose-then-pack, the corridor solve and
the strip packing all reason in page millimetres. Options, cheapest first:

1. **Scale-correct model space only** — write the part geometry (and nothing else) into model
   space divided by the drawing scale, keep annotations in page coordinates. Trivial to
   implement, fixes measurement for the part, but mixes two coordinate systems in one space and
   is arguably worse than being consistently wrong.
2. **A second, CAM-oriented export** — `format="dxf-profile"`: model-scale closed profiles of the
   part outline only, no annotation, one layer. Small, self-contained, serves the fabrication
   consumer properly, and sidesteps the layout question entirely. **This is the best
   value-per-unit-work in the whole evaluation.**
3. **Full paper-space layout** — model geometry at 1:1, a `VIEWPORT` per view at the drawing
   scale, annotations in paper space. This is what "proper" means to a CAD user. It requires the
   projection stage to retain the model-space geometry alongside its page projection, and the
   annotation stage to know which space each annotation belongs to. It is a genuine
   architectural change, not an export change.

**Risk.** Option 3 is high and touches ADR 2's territory. It should not be attempted as an
export-layer patch.

### 3.7 Conformance and round-trip verification

**What it means.** The file opens without warnings in the tools customers actually use, and
survives a round trip.

**Why it matters.** DXF is a format with wide, uneven support. "ezdxf wrote it" does not mean
AutoCAD, Fusion, QCAD, LibreCAD and Shapr3D all read it identically — particularly for
`DIMENSION`, `MTEXT` formatting codes and `HATCH` islands, which are exactly what 3.1–3.5 add.

**How.**
- `ezdxf.audit` on every exported file in CI: cheap, catches structural errors immediately.
- A round-trip test: write, read back with ezdxf, assert the entity census and that every
  expected `TEXT` string is present. This is the DXF analogue of the existing claim ratchet and
  would have caught "there is no text in the file" on day one.
- Manual verification in at least two independent readers per feature added. No amount of
  ezdxf-side testing substitutes; this is the honest cost of the format.

**Risk.** This is where an unbudgeted tail lives. Each new entity type carries its own
compatibility matrix.

### 3.8 Provenance

**What it means.** Each annotation entity carries draftwright's identity for it — the
measurement claim, the source feature — as `XDATA` under a registered `APPID`.

**Why it matters.** It makes the DXF a participant in the evidence chain rather than a dead end,
and would let a consumer (including the hosted AI product) map an entity back to the feature it
measures. Nothing else in the format offers that.

**How.** `doc.appids.add("DRAFTWRIGHT")` then `entity.set_xdata(...)` with the
`registry.measurement_of(name)` identity. Small, and only worth doing once 3.1/3.2 exist —
there is little point tagging a spline that is one thirtieth of a digit.

**Risk.** Low. XDATA is widely tolerated and ignored by readers that do not want it.

## 4. What I would do, in order

1. **`ezdxf.audit` in CI plus a round-trip entity-census test.** Hours, not days. It costs
   nothing and it is the guard whose absence let a text-free DXF ship unnoticed.
2. **Decide the consumer.** Drafting exchange, fabrication, or explicitly both. Everything below
   depends on it and it is not a technical decision.
3. **Text as `TEXT`** (3.1). Largest benefit per unit of work, data already exists and is proven
   by the PDF path, low risk, visually verifiable.
4. **Layers and linetypes** (3.3). Small, and fixes a real defect — hidden lines are currently
   not dashed in the DXF at all.
5. **The CAM profile export** (3.6 option 2), *if* fabrication is a target consumer. Small,
   self-contained, and the only thing that makes the file usable for cutting.
6. **Linear `DIMENSION` entities** (3.2), behind a flag, with the rendering-divergence trade-off
   made explicitly. This is the point where draftwright stops controlling what the customer
   sees.
7. Blocks (3.4), hatch (3.5), XDATA (3.8) as increments.
8. **Paper space (3.6 option 3) only as a deliberate architectural project**, if at all.

## 5. What this evaluation does not cover

- **Effort estimates in time.** I have sized each aspect relatively and named where the data
  already exists; I have not estimated hours, and the conformance tail in 3.7 is genuinely
  unpredictable.
- **Whether customers want this.** I have no evidence about which consumer matters. Step 2 above
  should be answered from customer contact, not from this document.
- **DWG.** Out of scope; it is a different format with licensing implications.
- **Any measurement of how other tools render the current file.** I inspected the DXF's contents
  with ezdxf; I did not open it in AutoCAD, Fusion or QCAD. Claims about what a recipient *sees*
  are inferences from the entity census, not observations.
