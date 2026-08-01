# Multi-Feature Object-Reference Workflow (Worked Example)

The README's ["From a part to an object-referenced script"](../README.md#from-a-part-to-an-object-referenced-script)
section shows the pattern in miniature: expose a `features` object from your build123d
source, then reference its members (`features.journal`, `features.m3_bore`) instead of
restating detected numbers. This doc is a complete, runnable version of that pattern
applied to a real multi-feature turned part, plus two things the miniature example
doesn't surface: external threads have no `.thread()` equivalent, and object-sourcing a
script re-runs your *full* build — pick the cheap geometry variant if your source has one.

## Starting point: a builder that returns one fused `Part`

Most build123d part functions look like this — several primitives combined with boolean
ops, one `Part` returned. A boss, a knurled disc, a bearing journal, and an M2 tap bored
down the journal — nothing here is gramel-specific, this is just shape:

```python
from build123d import Cylinder, Part, Pos, Rot

def build_thumbwheel() -> Part:
    boss = Pos(0.25, 0, 0) * Rot(0, 90, 0) * Cylinder(radius=3, height=0.5)
    disc = Pos(1.5, 0, 0) * Rot(0, 90, 0) * Cylinder(radius=5, height=2)
    journal = Pos(-1.6, 0, 0) * Rot(0, 90, 0) * Cylinder(radius=2, height=3.2)
    tap = Pos(-3.2, 0, 0) * Rot(0, 90, 0) * Cylinder(radius=0.8, height=8)
    thread = Pos(1.5, 0, 0) * Rot(0, 90, 0) * Cylinder(radius=1.5, height=20)

    body = boss + disc + journal + thread
    body = body - tap
    return body
```

Once `boss` / `disc` / `journal` / `tap` / `thread` are unioned into `body`, their identity
is gone — a `Sheet` built from `body` alone has nothing to reference and falls back to
silhouette detection off the finished solid, same as reading a STEP file. Magic numbers,
not because detection is bad, but because the objects it could reference were thrown away.

## The fix: return the pre-union objects too

Split the builder into a features function that keeps every named object, and make the
original single-`Part` function a thin wrapper over it. Every existing caller of
`build_thumbwheel` keeps working unchanged — this is purely additive:

```python
from dataclasses import dataclass

from build123d import Cylinder, Part, Pos, Rot


@dataclass
class ThumbwheelFeatures:
    boss: Part
    disc: Part
    journal: Part
    tap: Part
    thread: Part
    body: Part


def build_thumbwheel_features() -> ThumbwheelFeatures:
    boss = Pos(0.25, 0, 0) * Rot(0, 90, 0) * Cylinder(radius=3, height=0.5)
    disc = Pos(1.5, 0, 0) * Rot(0, 90, 0) * Cylinder(radius=5, height=2)
    journal = Pos(-1.6, 0, 0) * Rot(0, 90, 0) * Cylinder(radius=2, height=3.2)
    tap = Pos(-3.2, 0, 0) * Rot(0, 90, 0) * Cylinder(radius=0.8, height=8)
    thread = Pos(1.5, 0, 0) * Rot(0, 90, 0) * Cylinder(radius=1.5, height=20)

    body = boss + disc + journal + thread
    body = body - tap
    return ThumbwheelFeatures(boss=boss, disc=disc, journal=journal, tap=tap, thread=thread, body=body)


def build_thumbwheel() -> Part:
    return build_thumbwheel_features().body
```

`--out ... --script` against a `module:attr` / `file.py:attr` spec needs a **zero-arg**
callable returning a build123d `Shape` (or an already-built one) — so point it at
`build_thumbwheel`, not `build_thumbwheel_features`: the latter returns a
`ThumbwheelFeatures` dataclass and the resolver rejects it (`resolved to
ThumbwheelFeatures, not a build123d Shape`);
if your builder takes a `params` argument, point the spec at a small zero-arg factory
instead of the parametrised function itself.

## Generate the script first, then edit it

You do not write the `Sheet` script below from scratch — you generate it and edit it. Point
`--script` at the **Shape-returning** wrapper:

```bash
draftwright yourmodule:build_thumbwheel --script --out thumbwheel
```

That writes `thumbwheel.py`. `--script` emits the declarative `Sheet` flavour by default (the
only one since 0.3 — `--style sheet` is the sole accepted value).

What comes out — **verbatim, with feature and dimension lines elided where marked**:

```python
from yourmodule import build_thumbwheel as _obj
part = _obj()

sheet = Sheet(part, title='DRAWING', number='DWG-001')
hole1 = sheet.hole(diameter=1.6, at=(0.8, 0, 0), axis="x").depth(8)   # ⌀1.6 blind 8
step1 = sheet.step(diameter=3, length=5.3, at=(-5.85, 0, 0), axis="x")   # ⌀3 × 5.3 step
step2 = sheet.step(diameter=4, length=3.7, at=(-1.35, 0, 0), axis="x")   # ⌀4 × 3.7 step
# ... step3, step4, boss1 ...
envelope1 = sheet.envelope()   # envelope 20 × 10 × 10

sheet.authored_dimensions()
sheet.dimension(hole1, "bore.diameter")
sheet.dimension(hole1, "bore.depth")
# ... twelve more dimension lines ...

drawing = sheet.build()
drawing.export('thumbwheel', formats=('pdf',))
```

`part` is rebound to your **live source**, not a frozen STEP. `authored_dimensions()` declares
that this is the complete set, so commenting a `dimension(...)` line out drops exactly that
dimension. `sheet.envelope()` reads the overall size off the part rather than restating it. The
title defaults to `DRAWING` — pass `--title` to set it.

Honest, and a working starting point. But `diameter=4, length=3.7` are numbers *restated* from
geometry you already have — change the journal in your source and the drawing quietly disagrees.
Note too that the fused body detects as four `step`s: the silhouette is all detection can see
once the objects are unioned. The rest of this doc is the edit that fixes both — swap each
numbered line for the object it was measured from.

## Declaring the drawing by reference

```python
from draftwright import Sheet

features = build_thumbwheel_features()
sheet = Sheet(features.body, title="THUMBWHEEL", number="DWG-001")

sheet.step(features.journal)
sheet.step(features.boss)
sheet.step(features.disc)
sheet.hole(features.tap).thread("M2x0.4")     # internal (tapped) thread
sheet.step(features.thread).note("M3×0.5")    # external thread — see below

sheet.export("thumbwheel")                    # writes thumbwheel.pdf
```

Run the two snippets above as one file and it produces a real drawing end to end — no
gramel, no STEP file, nothing else required beyond `draftwright` and `build123d`.

## Gotcha: `.thread()` only exists on holes

`_Hole.thread(spec)` folds a tap/thread callout onto a **bore** — there is no equivalent on
`_Dim` (the handle returned by `sheet.step(...)` / `sheet.diameter(...)`). The tapped
`features.tap` hole above takes `.thread("M2x0.4")` directly. `features.thread` is an
**external** thread — a turned major-diameter cylinder, not a bore — so it's declared as a
step, and the designation goes on a `.note(...)` instead:

```python
sheet.step(features.thread).note("M3×0.5")   # external thread: no .thread() for a step
sheet.hole(features.tap).thread("M2x0.4")    # internal (tapped) thread: use .thread()
```

Both render as a leader callout next to the feature; only the tapped-hole one is the
structured `.thread()` aspect that also folds onto `.finish(...)` (Ra-on-thread, #764).

## Gotcha: object-sourcing rebuilds your *full* source, every run

An object spec (`module:attr`) doesn't read a cached shape — it imports your module and
calls the factory, which re-runs your **entire** build123d construction. If your source has
more than one fidelity level (e.g. a `prototype` flag that switches between real helical
thread geometry and a plain reference cylinder like `features.thread` above), the
object-spec path pays for whichever one your factory selects — every time the script runs.

On one real part, building with the "real thread" path (a loft-based helical sweep) took
**over 10 minutes**; the same part with a reference-cylinder flag set took **14 seconds**.
For a CNC drawing you want the cheap, ISO-6410-style reference geometry anyway — real
thread geometry belongs in the print/manufacturing export, not the drawing. Force the fast
variant explicitly inside your zero-arg factory rather than relying on whatever your
builder's default happens to be:

```python
def make_part():
    params = default_params()
    params = params.model_copy(update={"process": params.process.model_copy(update={"prototype": False})})
    return build_thumbwheel_features(params).body
```

If a `draftwright ... --script` invocation against an object spec is taking minutes rather
than seconds, this is the first thing to check.
