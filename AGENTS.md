# Using draftwright (guide for AI agents)

draftwright turns a build123d solid into a fully-annotated technical drawing (orthographic
views, dimensions, callouts, GD&T, section, title block → SVG/DXF/PDF/PNG). This is the
one-page "drive it correctly" guide. The **why** lives in `docs/adr/`; this is the **how**.

The single most important rule:

> **Never hand-place a feature callout, dimension, or GD&T frame at raw coordinates.**
> You declare *what* to annotate; the engine's placement solve decides *where* (crossing-free,
> packed — ADR 0004/0014). There is deliberately **no** arbitrary-coordinate placement in the
> sanctioned surface — you cannot choose positions, and you should not want to. Hand-placed
> annotations bypass the solve and lay out badly (overlaps, off in a corner). To make one
> annotation win a contested spot, raise its `priority=` or `pin=` it — that **ranks/anchors**
> it at its natural position, it does not set coordinates (and an infeasible pin can still drop).

## Front doors (pick one, in order of preference)

```python
from draftwright import Sheet, build_drawing

# 1. Fully automatic — detect features and annotate everything.
dwg = build_drawing("part.step")            # or build_drawing(a_build123d_solid)
dwg.export("out", formats=("pdf", "png"))

# 2. Declared — the Sheet façade (ADR 0011). Statement-style: declare features + aspects on `s`.
s = Sheet(solid)                            # declare against the build123d part
h = s.hole(hole_solid)                      # returns a handle you can keep
h.fit("H7")                                 # aspects: .fit/.tolerance/.note/.thread/.finish/…
s.slot(slot_solid)
s.datum("A", top_face)                      # a datum on a face
s.control(h).position(0.1, to="A")          # GD&T on the hole handle, wrt datum A
dwg = s.build()                             # -> a Drawing

# 3. Declared IR — pass a PartModel/features to build_drawing and skip detection.
dwg = build_drawing(solid, model=my_part_model)
```

All three converge on the one engine; there is no second engine.

## Adding / editing annotations

On a built `Drawing`, use the **verbs** (never raw coordinates). Batch edits through
`deferred()` reach auto-pass placement quality; a single live verb places one annotation
reasonably (not the full solve), so prefer `deferred()` when adding more than one:

```python
with dwg.deferred():                 # verbs RECORD intents; block exit finalize()s them
    dwg.callout(feature)             # ø / n× / feature callout (leader)
    dwg.dimension(feature, "length", role="height", pin=True, priority=2)  # anchored, ranked
    dwg.locate(feature)              # position dims from the datum
    dwg.furniture(feature)           # centre marks / pattern furniture
    dwg.section()                    # section A–A
# finalize() drains the recorded intents through the auto-pass's shared placement stages.

dwg.drop(feature)                    # remove every annotation for an IR feature (from model())
dwg.remove("dim_width")              # remove one annotation BY NAME (KeyError if absent)
dwg.pin("dim_width"); dwg.unpin("dim_width")   # freeze / unfreeze an already-placed annotation
```

`drop()` takes a **feature** (`dwg.model().features[i]`), not a name; `remove()` takes a name.

## GD&T — always declared, never raw

GD&T frames / datums / surface finishes are placed as first-class candidates by the solve.
Use the declared surface — there is deliberately **no** public "add a raw frame" verb. Declare
the controlled **feature first**, then target it (a handle, `Feature`, index, or a face):

```python
s = Sheet(solid)
hole = s.hole(hole_solid)                   # the feature the tolerance controls
s.datum("A", top_face)
s.control(hole).position(0.1, to="A").perpendicularity(0.05, to="A")  # chain characteristics
dwg = s.build()
```

A frame added via `dwg.add(...)` at computed coordinates bypasses the solve and lays out
badly — this is the most common GD&T-layout mistake.

## Diagnostics — always check lint

```python
for issue in dwg.lint():
    print(issue.severity, issue.code, issue.message)
```

- `gdt_dropped`, `*_dropped` → an annotation could not be placed (and why). `annotation_overlap`
  → annotations placed but conflict. Both mean the layout needs attention.
- **A clean lint on a sparse drawing usually means features were not recognised**, not that
  the drawing is complete — check `dwg.model().features` / `dwg.features(view)`. If a feature
  you expect is missing, that is a recognition gap, not a "done" drawing.

## Export

```python
paths = dwg.export("out", formats=("svg", "dxf", "pdf", "png"))   # -> {format: path}
```

## What NOT to do

- ❌ Raw page coordinates for feature annotations; `Drawing.place_dim(...)` (deprecated raw
  hatch). To influence placement use `pin=`/`priority=`, not coordinates.
- ❌ `dwg.add(Dimension/Leader/FeatureControlFrame(...))` to place a *feature* callout — `add`
  is the engine's low-level primitive (fine for free `Note`s / tables that have no strip, not
  for solve-able annotations).
- ❌ Bypassing recognition / assuming byte-identical output (it is not a goal — ADR 0004/0012).

## Source of truth

`docs/adr/` — especially 0004 (compose-then-pack layout), 0014 (collect-then-solve placement),
0011 (declare features), 0012 (edits as pinned candidates), 0015 (the compiler pipeline).
`CLAUDE.md` has the module map.
