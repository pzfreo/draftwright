# Multi-Feature Object-Reference Workflow (Worked Example)

The README's ["From a part to an object-referenced script"](../README.md#from-a-part-to-an-object-referenced-script)
section shows the pattern in miniature: expose a `features` object from your build123d
source, then reference its members (`features.journal`, `features.m3_bore`) instead of
restating detected numbers. This doc is a complete, runnable version of that pattern
applied to a real multi-feature turned part, plus three things the miniature example
doesn't surface: a spec must be a **zero-arg** callable, external threads have no
`.thread()` equivalent, and object-sourcing a script re-runs your *full* build — pick the
cheap geometry variant if your source has one.

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

`--out ... --script` against a `module:attr` / `file.py:attr` spec accepts either a zero-arg
callable returning a build123d `Shape`, or the features-dataclass form above: a zero-arg callable
returning a dataclass with a Shape-valued `body` and named Shape fields. Point it at
`build_thumbwheel_features` to let the emitter preserve independently established references.
If your builder takes a `params` argument, point the spec at a small zero-arg factory instead of
the parametrised function itself.

## Generate the script first, then edit it

You do not write the `Sheet` script below from scratch — you generate it and edit it. Point
`--script` at the **features-returning** factory:

```bash
draftwright yourmodule:build_thumbwheel_features --script --out thumbwheel
```

That writes `thumbwheel.py`. `--script` emits the declarative `Sheet` flavour by default (the
only one since 0.3 — `--style sheet` is the sole accepted value).

What comes out — **verbatim, with feature and dimension lines elided where marked**:

```python
from yourmodule import build_thumbwheel_features as _obj
features = _obj()
part = features.body

sheet = Sheet(part, title='DRAWING', number='DWG-001', scale=5.0, page=(297.0, 210.0))
hole1 = sheet.hole(diameter=1.6, at=(0.8, 0, 0), axis="x").depth(8)   # ⌀1.6 blind 8
step1 = sheet.step(diameter=3, length=5.3, at=(-5.85, 0, 0), axis="x", profile_group='detected-profile-1')   # ⌀3 × 5.3 step
step2 = sheet.step(diameter=4, length=3.2, at=(-1.6, 0, 0), axis="x", profile_group='detected-profile-1')   # ⌀4 × 3.2 step
step3 = sheet.step(diameter=6, length=0.5, at=(0.25, 0, 0), axis="x", profile_group='detected-profile-1')   # ⌀6 × 0.5 step
# ... step4, step5, boss1 ...
envelope1 = sheet.envelope()   # envelope 20 × 10 × 10

sheet.authored_dimensions()
sheet.dimension(hole1, "bore.diameter")
sheet.dimension(hole1, "bore.depth")
# ... twelve more dimension lines ...

drawing = sheet.build()
drawing.export('thumbwheel', formats=('pdf',))
```

`features` and `part` are rebound to your **live source**, not a frozen STEP. References are
substituted only where polarity, defining geometry, and mutual one-to-one correspondence all
agree. This particular fused body no longer has a one-to-one mapping: it detects more axial
segments than the source names, the tap's construction span is offset from the finished bore,
and the fused external objects partially overlap one another. Every line therefore stays a
complete numeric declaration. That is intentional — a numeric fallback is safer than a
plausible but wrong source name. A plate with an isolated named cutter whose centre agrees with
the finished bore instead emits `sheet.hole(features.bore, depth=...)` automatically.
`authored_dimensions()` declares that this is the complete set, so commenting a
`dimension(...)` line out drops exactly that dimension. `sheet.envelope()` reads the overall
size off the part rather than restating it. The title defaults to `DRAWING` — pass `--title` to
set it.

Honest, and a working starting point. Note that the fused body detects as four `step`s: the
silhouette is all detection can see once the objects are unioned. Source references cure
restated numbers where identity survives; they do not recreate manufacturing identity that the
finished solid no longer contains. The rest of this document shows how to declare that intent
explicitly when the generated fail-closed mix cannot.

## Declaring the drawing by reference

```python
from draftwright import Sheet

features = build_thumbwheel_features()
sheet = Sheet(features.body, title="THUMBWHEEL", number="DWG-001")

journal = sheet.step(features.journal)
boss = sheet.step(features.boss)
disc = sheet.step(features.disc)
tap = sheet.hole(features.tap)                # the cutter, not a bore — see below
tap.thread("M2×0.4")                          # internal (tapped) thread
ext = sheet.step(features.thread)
ext.note("M3×0.5")                            # external thread — see below

# Say where the dimensions come from. `authored_dimensions()` declares that the lines
# below are the COMPLETE set, so anything not listed is omitted on purpose (ADR 4).
# A `dimension(...)` line selects the authored source on its own, so the verb is not
# what makes this build; it is how a complete-but-EMPTY set says so, and it states the
# intent for a reader. Building with NEITHER the verb nor a `dimension(...)` line — as
# this example did before #1469 — raises `ValueError`.
sheet.authored_dimensions()
sheet.dimension(tap, "bore.diameter")
sheet.dimension(tap, "location")
for handle in (journal, boss, disc, ext):
    sheet.dimension(handle, "step.diameter")
sheet.dimension(journal, "step.length")

sheet.export("thumbwheel")                    # writes thumbwheel.pdf
```

Run the two snippets above as one file and it produces a real drawing end to end — no
gramel, no STEP file, nothing else required beyond `draftwright` and `build123d`.

### What you should see

Measured from the snippet above on 2026-09-05 (draftwright 0.4.x, `Sheet.build()`):

- **A4 landscape at 2:1**, chosen automatically — the part is 20 mm long.
- **Two orthographic views plus an isometric**: `front` (x–z) and `side` (y–z), then `iso`.
  Two, not three: the part is rotational about x, so a top view (x–y) would show the same
  profile as the front (x–z). The side view is the circular end view, and carries the
  diameters.
- **Eleven annotations.** `Drawing.annotations()` names them, which is the quickest way to
  confirm the run did what you asked. In full: `m_dia_x0`, `m_dia_x1`, `m_dia_x2`,
  `m_dia_x3` (the four turned diameters), `m_steplen0` (the journal's length), `hc_side0`
  (the `M2×0.4` tap callout, on the side view), `m_gdt0` (the `M3×0.5` note),
  `centerline_front`, `m_cm0`, `title_block` and `note_iso_nts`.
- **Two `warning`-level lint notes, and no errors.** Both are correct reports about this
  deliberately-short example, not failures of the workflow:

  ```
  axial_length_missing        turned part has 4 axial steps but only 1 step length(s)
                              dimensioned — shoulders cannot be located
  hole_requirement_unverifiable
                              hole at (0.8, 0.0, 0.0) all 4 physical requirements, which
                              no IR feature claimed, cannot be joined to measurement
                              provenance without guessing
  ```

  The first is the authored-set contract working: only one `step.length` was declared, so
  the other three shoulders are unlocated and Draftwright says so rather than inventing
  them. Declaring the other three does **not** clear it — measured, the four shoulders are
  then too close together to dimension at 2:1 on A4, the chain is dropped whole, and you get
  `axial_length_missing` (now 0 of 4 dimensioned) plus `warning step_dim_dropped` and
  `error plan_incomplete`. Widening the page or dropping the scale is the real fix; this is
  a four-shoulder part 20 mm long. The second warning is explained under
  [Why `sheet.hole(features.tap)` works](#why-sheethofeatures-tap-works) below.

  Neither of the two is an `error`. `plan_incomplete` is what an error looks like.

## Why `sheet.hole(features.tap)` works

`features.tap` is the **cutter** — the cylinder that was subtracted (`body = body - tap`).
It is not a bore in the finished solid. `sheet.hole(...)` reads ⌀, axis and location off
that tool object, which is the whole trick: the subtraction tool is the only thing that
still knows the *intent* ("an M2 tap here, on this axis"), and the fused solid does not. So
you reference the tool, not the hole it left. The same applies to
`sheet.step(features.thread)`: an external thread is declared from the reference cylinder
that made it.

That is also why `hole_requirement_unverifiable` shows up above — and it is worth seeing
exactly what disagrees, because "reference the tool" is not free. Measured on this part:

| | anchor | depth | end condition |
| --- | --- | --- | --- |
| declared, from `features.tap` | `(-3.2, 0, 0)` | `None` | `through=True` |
| recognised, from the solid | `(0.8, 0, 0)` | `8.0` | flat-bottomed (blind) |

Both describe the same 8 mm of cylinder, and they are still not the same feature. A cutter
is positioned and sized for cutting, so it is anchored where the tool starts rather than at
the bore's mouth, and it has no end condition of its own — `through` is a property of the
tool's sweep, not of the part. Draftwright will not assert that these two are one feature
without a join it can prove, so it reports the requirement as unattributable instead. That
refusal is the point: the alternative is a confident wrong provenance.

## Gotcha: `.thread()` only exists on holes

`_Hole.thread(spec)` folds a tap/thread callout onto a **bore** — there is no equivalent on
`_Dim` (the handle returned by `sheet.step(...)` / `sheet.diameter(...)`). The tapped
`features.tap` hole above takes `.thread("M2×0.4")` directly. `features.thread` is an
**external** thread — a turned major-diameter cylinder, not a bore — so it's declared as a
step, and the designation goes on a `.note(...)` instead:

```python
sheet.step(features.thread).note("M3×0.5")   # external thread: no .thread() for a step
sheet.hole(features.tap).thread("M2×0.4")    # internal (tapped) thread: use .thread()
```

Both render as a leader callout next to the feature; only the tapped-hole one is the
structured `.thread()` aspect that also folds onto `.finish(...)` (Ra-on-thread).

`sheet.diameter(...)` is the other verb that returns a `_Dim`. Use `step(...)` for a turned
segment that has a **length** as well as a diameter — a shoulder the drawing must locate —
and `diameter(...)` when only the diameter is a requirement. `step` is what you want for
every cylinder in this example.

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
# Illustrative, not runnable: `build_my_part_features` is YOUR parametrised builder, and
# `default_params()` / `.model_copy(...)` assume a Pydantic-v2 params model. The point is
# the shape — the zero-arg factory pins the cheap variant explicitly — not this API.
# `build_thumbwheel_features` above is deliberately a DIFFERENT, zero-arg function; a spec
# must be zero-arg, which is why the parametrised one needs this wrapper at all.
def make_part():
    params = default_params()                       # your own params object
    params = params.with_prototype(False)           # however yours spells "cheap variant"
    return build_my_part_features(params).body      # your parametrised builder
```

If a `draftwright ... --script` invocation against an object spec is taking minutes rather
than seconds, this is the first thing to check.
