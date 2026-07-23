# Using draftwright (guide for AI agents)

draftwright turns a build123d solid into a fully-annotated technical drawing (orthographic
views, dimensions, callouts, GD&T, section, title block → SVG/DXF/PDF/PNG). This is the
one-page "drive it correctly" guide. The **why** lives in `docs/adr/`; this is the **how**.

The single most important rule:

> **Never hand-place a feature callout, dimension, or GD&T frame at raw coordinates.**
> Use the declared/verb surfaces below — they route every annotation through the placement
> solve (ADR 0014), which spaces them crossing-free and packs the sheet. Hand-placed
> annotations are invisible to the solve and produce overlapping, badly-laid-out drawings.
> To force a position, **pin** a candidate — do not give it coordinates.

## Front doors (pick one, in order of preference)

```python
from draftwright import Sheet, build_drawing

# 1. Fully automatic — detect features and annotate everything.
dwg = build_drawing("part.step")            # or build_drawing(a_build123d_solid)
dwg.export("out", formats=("pdf", "png"))

# 2. Declared — the Sheet façade (ADR 0011). Statement-style: declare features + aspects on `s`.
s = Sheet(solid)                            # declare against the build123d part
s.hole(hole_solid).fit("H7")               # a hole + ISO fit  (aspects: .fit/.tolerance/.note/.thread/.finish/…)
s.slot(slot_solid)
s.datum("A", top_face)                      # a datum on a face
s.control(0).position(0.1, to="A")          # GD&T position on declared feature #0, wrt datum A
dwg = s.build()                             # -> a Drawing

# 3. Declared IR — pass a PartModel/features to build_drawing and skip detection.
dwg = build_drawing(solid, model=my_part_model)
```

All three converge on the one engine; there is no second engine.

## Adding / editing annotations the right way

On a built `Drawing`, use the **verbs** — they place through the solve:

```python
dwg.callout(feature)                 # ø / n× / feature callout (leader), solve-placed
dwg.dimension(feature, "length", role="...", pin=True, priority=2)  # pin = force position
dwg.locate(feature)                  # position dims from the datum
dwg.furniture(feature)               # centre marks / pattern furniture
dwg.section()                        # section A–A
dwg.drop("dim_width"); dwg.pin(name); dwg.unpin(name)
```

Batch edits go through record-then-finalize:

```python
with dwg.deferred():                 # verbs RECORD intents; the block exit finalize()s
    dwg.callout(f); dwg.dimension(g, "length", role="height", pin=True)
# on normal exit, one solve places everything at auto-pass quality
```

## GD&T — always declared, never raw

GD&T frames / datums / surface finishes are first-class corridor candidates placed by the
solve. Use the declared surface — there is deliberately **no** public "add a raw frame" verb:

```python
s = Sheet(solid)
s.datum("A", top_face)
s.control(0).position(0.1, to="A").perpendicularity(0.05, to="A")   # chain characteristics
dwg = s.build()
```

Target features/faces; let the solve place the frame. A frame added via `dwg.add(...)` at
computed coordinates bypasses the solve and lays out badly — this is the most common
GD&T-layout mistake.

## Diagnostics — always check lint

```python
for issue in dwg.lint():
    print(issue.severity, issue.code, issue.message)
```

- `gdt_dropped`, `*_dropped`, `annotation_overlap` → something did not place (and why).
- **A clean lint on a sparse drawing usually means features were not recognised**, not that
  the drawing is complete — check `dwg.model().features` / `dwg.features(view)`. If a feature
  you expect is missing, that is a recognition gap, not a "done" drawing.

## Export

```python
paths = dwg.export("out", formats=("svg", "dxf", "pdf", "png"))   # -> {format: path}
```

## What NOT to do

- ❌ Raw page coordinates for feature annotations; `Drawing.place_dim(...)` (deprecated raw
  hatch). To control placement, **pin** — don't hand coordinates.
- ❌ `dwg.add(Dimension/Leader/FeatureControlFrame(...))` to place a *feature* callout — `add`
  is the engine's low-level primitive (fine for free `Note`s / tables that have no strip, not
  for solve-able annotations).
- ❌ Bypassing recognition / assuming byte-identical output (it is not a goal — ADR 0004/0012).

## Source of truth

`docs/adr/` — especially 0004 (compose-then-pack layout), 0014 (collect-then-solve placement),
0011 (declare features), 0012 (edits as pinned candidates), 0015 (the compiler pipeline).
`CLAUDE.md` has the module map.
