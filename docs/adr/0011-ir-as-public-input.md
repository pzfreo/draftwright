# ADR 0011 — The IR as a public input: declare features, don't only detect them

- **Status:** Accepted — Phase 0 (the `model=` seam + object→feature constructors) and
  Phase 1 (the `Sheet` façade) landed; Phase 2 (aspect renderers) underway: **P2a
  toleranced dimensions** (±/limit on a diameter/step/hole, via a `decorations=` side-layer
  → `DimParameter.tolerance`) and **P2a.2 fit-class** (`.fit("H7")` → ISO 286 deviation,
  `draftwright/fits.py`, riding the same `tolerance` field as a `FitClass` marker) landed;
  P2b GD&T+finish / P2c aspect verbs / P2d PMI still pending per the #446 roadmap. Phase 2
  execution plan:
  [`docs/plans/0011-phase2-aspects-roadmap.md`](../plans/0011-phase2-aspects-roadmap.md).
  **Amendment 1** (2026-07-05): the three authoring modes + the **mode-3 generation surface**
  (a declarative `Sheet`-DSL emitter, #461/#462/#463) — sequenced *before* P2b; carries one
  **open decision** (for detected input: reconstruct the part vs. leave a seam).
- **Date:** 2026-07-05
- **Deciders:** Paul Fremantle (pzfreo)

## Context

draftwright compiles a solid into a drawing through one waist: the **IR
`PartModel`** — a list of frozen `Feature` dataclasses (ADR 0008). Everything
downstream of it — the planner, render-intents, the ADR-0009 layout, lint,
export — reads *that*, never the raw solid. Today there is exactly **one producer**
of the IR: **feature detection** (`recognition/` → `build_part_model`), which
recovers features from the finished solid's silhouettes.

Detection is the right default — the common case is "hand me a STEP, draw it." But
it has two structural limits:

1. **It can be wrong.** Silhouette recognition misclassifies. #298 is the canonical
   case: a ⌀6 band nested under a ⌀10 OD collapsed to ⌀10 in the max-silhouette read
   and went silently undimensioned; detection's own `feature_diameters` also *missed
   a blind hole entirely* in the #445 prototype. The engine cannot always out-detect
   the person who built the part.
2. **It re-derives what the caller already knows.** When the part is built
   parametrically (e.g. the `pzfreo/gramel` `thumbwheel_drive_screw`, which holds
   `boss_diameter = 6`, `thread = "M3"` as literal parameters), re-recovering ⌀6 from
   the silhouette is redundant at best and, per (1), unreliable at worst.

ADR 0001 Amendment 1 established that *both inputs converge at the detected IR* and
that the edit surface is the model; #400 made the model a **read** surface
(`dwg.model()`) plus imperative edit verbs (`callout`/`locate`/`dimension`/`section`).
The missing half is symmetric: let the caller **supply the IR as an input**, so
detection becomes *a* producer of the model, not the *only* one.

The `proto/declare-features` spike proved the seam is small: the orchestrator already
honours a pre-set `dwg._part_model` (`orchestrator.py:170`) — `build_drawing` merely
always overwrote it with detection.

## Decision

Make the IR `PartModel` a **first-class public input**, on equal footing with
detection. Concretely:

1. **A `model=` input to the build entry point.** `build_drawing(part, model=…)`
   accepts a caller-supplied `PartModel` (or a `Sequence[Feature]`, wrapped with the
   part's bbox + a default corner datum). When supplied, **detection is skipped** and
   the auto-pass renders the declared features. `model=None` is the unchanged default —
   detect. Detection and declaration are two producers of the *same* IR; **everything
   downstream is untouched.**

2. **Object → feature constructors.** `hole` / `boss` / `step` / `slot` / `pattern` /
   `envelope` read a feature's geometry off a known build123d object (a cylindrical
   face → radius / axis / location) — *geometry supplies the value* — with an
   explicit-value flavour (`hole(diameter=6, at=…, axis="z")`) for parametric code
   that never built a discrete tool.

3. **Declaration is not all-or-nothing — the hybrid is first-class.** The caller may
   start from `dwg.model()` (detected) and **override** specific features. Declaration
   is for where you know better than detection, not everywhere.

4. **Aspects geometry cannot carry attach as decorations, not on the frozen IR.** A
   *tolerance* is a property of a **dimension** (`DimParameter`); *GD&T* and *surface
   finish* are annotation kinds **keyed to a feature/face** in a decoration side-layer
   consumed by their renderers. The frozen `Feature` schema stays clean, so every
   existing consumer keeps ignoring what it does not understand. (The renderers
   themselves are deferred engine work — #61 / #62.)

The declarative `model=` path is the **from-scratch authoring** mode ("I know all the
features"); the #400 imperative verbs remain the **incremental-edit** mode ("this
detected drawing needs one more thing"). Two complementary modes, one IR.

## Consequences

- **Misdetection becomes recoverable by construction** — you declare ⌀6 and it is
  drawn as ⌀6, full stop. The #298 class of bug cannot silence a feature you named.
- **Parametric/generated code reads as the drawing** — you reference the feature you
  built and decorate it with intent; no restating numbers the model owns. This is the
  foundation of the #445 "beautiful-Python drawing DSL".
- **The IR schema becomes a public contract.** Its field shapes are now an API surface,
  so changes to `Feature`/`DimParameter` carry versioning discipline they did not before.
- **The detector is no longer privileged.** It is one front-end; a future front-end
  (PMI, a CAD feature tree, an LLM) can produce the same IR.
- **Known caveat — analysis and the coverage lint still detect.** `_analyse` recovers
  `a.holes` etc. to size the sheet and estimate strips, and `lint_feature_coverage`
  re-detects the part's holes/diameters to check coverage — both independently of
  `model=`. So a supplied model overrides *dimensioning*, but sheet estimation still
  detects (the auto-scale heuristic may differ slightly), and a **partial** declaration
  is correctly flagged by the coverage lint for the geometry it left undimensioned (the
  drawing is right — those features genuinely have no callout). A full declared-model
  bypass of estimation + coverage is a follow-up, not a blocker.
- **Hole/pattern render membership is model-driven (#448, resolved).** Originally the
  *hole and pattern* renderers gated on `feature_keys` — the set of *detected* hole
  positions (`feature_holes_of(a)`) — so a declared hole/pattern only rendered where its
  members coincided (to 3 dp) with a detected hole, and a detection-*missed* hole rendered
  nothing (surfacing only as a coverage warning). When `model=` is supplied, the callout
  membership set is now sourced from the declared IR groups too (`_declared_feature_keys`),
  mirroring the exact member source and rotational concentric-bore exclusion of the callout
  filter — so a declared hole/pattern renders at its **declared** position regardless of
  detection. Gated on the declared flag, so the detection-only path is byte-identical. The
  Amendment-6 invariant still holds (no recogniser `Hole` crosses into the renderers — the
  keys are IR-derived). One detection-dependent bit remains: off-axis side-drilled hole
  *location* dims (`_locate_off_axis_holes`) still need recogniser-`Hole` geometry a declared
  feature doesn't carry, so a detection-missed side hole's location dim is a further follow-up.

## Alternatives considered

- **Improve detection only.** Necessary and ongoing, but it cannot close the gap: #298
  shows the engine cannot always out-detect the author, and parametric callers already
  hold the exact values. Detection stays the default; this ADR adds a second producer,
  it does not replace the first.
- **Imperative edit verbs only (the #400 surface).** Complementary, kept — but
  "I know every feature" is inherently *declarative*, and driving a from-scratch drawing
  through per-annotation imperative calls is more ceremony than declaring the model and
  letting the auto-pass place it coherently.
- **Tag build123d objects with drawing metadata as you model.** Couples the modelling
  code to draftwright's vocabulary and spreads drawing intent through the part build;
  the `model=` input keeps the two concerns separate (build the part, then describe its
  drawing).
- **Extend the frozen IR with tolerance/GD&T fields.** Rejected for the schema churn and
  because tolerance is per-dimension, not per-feature; the decoration side-layer keeps
  the IR minimal and the existing consumers untouched.

## Amendment 1 — the three authoring modes and the mode-3 *generation* surface (2026-07-05)

`model=` made the IR an **input**; #400 made it a **read+edit** surface. Stepping back, that
gives three ways to author a drawing, in increasing control — and naming them clarifies what
is done and what is still missing:

1. **Just do it.** `make_drawing(part_or_step)` → SVG/DXF. *(done)*
2. **Auto, then tweak.** `build_drawing()` returns a live `Drawing`; edit it (`remove`,
   `place_dim`, `del views[…]`, `pin`, `repair`) then `.export()`. *(done)*
3. **Generate an editable beautiful-Python DSL script** that captures *every* view, feature,
   and dimension as commentable lines the user edits / drops / extends. *(partial — this
   amendment)*

Mode 3 exists today only as the **imperative** `--script` emitter (the #400/#426 verb
reconstruction, STEP-only). The target is a **declarative `Sheet`-DSL emitter** (#461). The
value of mode 3 is a *single source of truth*: reference a feature and read its size off the
geometry, so the drawing tracks the part parametrically (change `Cylinder(9, 40)`, the callout
follows ⌀18 → ⌀20) — no restated numbers to drift.

### The 3a / 3b split (accepted)

Mode 3 has two fundamentally different ceilings, set by whether source objects exist:

- **3a — detected input** (STEP, or a finished solid with no handles). Features were recovered
  from silhouettes; there are **no source objects**, so a *from-scratch* generation cannot
  reference them. This is a **generation** problem.
- **3b — build123d objects with handles**. The drawing *references* the objects and reads every
  size off geometry → a **number-free drawing layer**, numbers staying in the part build. This
  is fundamentally **authored, not generated**: a finished solid cannot recover *variable names*
  or *semantic intent* (bore vs boss, needs-a-fit). A tool can at most *scaffold* reference lines
  from a caller-supplied `{name: object}` map. `sheet.hole(obj)` / `diameter(obj)` / `step(obj)`
  / `envelope()` read size off objects **today** (proven lint-clean); the remaining gap is that
  size-carrying *aspects* still take numbers (a counterbore's `cbore=(30, 14)`) — closed by the
  object-reading aspect verbs (#462).

### Open decision — for 3a, reconstruct the part or leave a seam?

The one fork that shapes the whole emitter. From a detected solid we can either **reconstruct**
a build123d part (so the drawing layer can be reference-based / number-free too), or leave a
**seam** for the caller's own part and write the detected numbers into the drawing.

**Option A — reconstruct the part (number-free drawing layer, self-contained).**

```python
from build123d import Box, Cylinder, Pos
from draftwright import Sheet

# Part — RECONSTRUCTED from detected features. Numbers live HERE, in the geometry.
plate = Box(100, 70, 24)
bore  = Pos(0, 0, 0) * Cylinder(9, 40)
cbore = Pos(0, 0, 8) * Cylinder(15, 20)
h0 = Pos(-38, -24, 0) * Cylinder(4, 40)
h1 = Pos(38, -24, 0)  * Cylinder(4, 40)
h2 = Pos(-38, 24, 0)  * Cylinder(4, 40)
h3 = Pos(38, 24, 0)   * Cylinder(4, 40)
part = plate - bore - cbore - h0 - h1 - h2 - h3

# Drawing — references the objects; sizes READ off geometry (number-free, bar the
# counterbore, which is exactly what #462 removes with .cbore(cbore)).
sheet = Sheet(part, title="Mounting Plate", number="DWG-001")
sheet.envelope()
sheet.hole(bore, cbore=(30, 14))
for h in (h0, h1, h2, h3):
    sheet.hole(h)
sheet.export("mounting_plate")
```

*Pros:* the drawing layer is references, not numbers — the mode-3 ideal; self-contained (no
STEP file / no `part` to supply); the whole script is runnable as-is. *Cons:* the reconstruction
is a **flat CSG approximation**, not the caller's parametric intent, and reconstructing arbitrary
geometry from detected features is itself lossy/hard (counterbores need two cuts; fillets, drafts,
turned profiles, and slots may not round-trip). Numbers still exist — they have moved into a
*synthetic* part build the user didn't write.

**Option B — part-seam (detected numbers in the drawing, honest, always works).**

```python
from draftwright import Sheet
from draftwright.model import hole

part = ...   # ← YOUR build123d object, or import_step("plate.step")

sheet = Sheet(part, title="Mounting Plate", number="DWG-001")
sheet.hole(diameter=8, at=(-38, -24, 12), axis="z", count=4)
sheet.hole(diameter=18, at=(0, 0, 12), axis="z", cbore=(30, 14))
sheet.envelope()   # 100 × 24 × 70
# step_level @ (0, 0, -12) — auto-dimensioned (no declarative verb yet)
sheet.export("mounting_plate")
```

*Pros:* honest (it *is* detected data); works for **any** geometry, however complex; the caller
plugs in their real (parametric) part at the seam. *Cons:* the drawing layer carries magic
numbers — the very thing mode 3 wants to avoid — and there is no object to re-read, so it does
not track a parametric part.

**Recommendation (pending sign-off):** ship **B first** (it is the honest, universal baseline and
unblocks the emitter), and offer **A as an opt-in** (`reconstruct=True`) for the parts whose
features reconstruct cleanly (prismatic + holes + simple counterbores), falling back to B when
reconstruction is lossy. The two are not exclusive; the fork is really *which is the default*.

### Fidelity contract (proposed)

The generated script guarantees **a lint-clean drawing of the same features**, not a byte-identical
copy of the auto-pass. (The imperative `--script` chases auto-pass quality via `finalize()`; the
declarative emitter re-runs the auto-pass over the declared model, so placement is auto-pass by
construction.)

### Coverage honesty (accepted)

Kinds with no declarative verb yet (`step_level`, `rotational`, `pmi`) are **flagged inline** as
auto-dimensioned, never silently dropped — matching the fail-loud discipline of the constructors
(#452). Growing verbs for them is follow-up, not a blocker.

Tracked by **#461** (the emitter), **#462** (object-reading aspects → number-free 3b), **#463**
(`sheet.of(feature)` → decorate a generated feature). Sequenced **before** the P2b GD&T work: the
generation surface is the differentiator; GD&T is additive rendering behind the same façade.

## Relationship to other ADRs

- **Extends ADR 0001 Amendment 1** (both inputs converge at the detected IR) — the
  caller is now a first-class *producer* of that IR, not only an editor of it.
- **Builds on ADR 0008** (the unified feature model / dimensioning planner) — the
  PartModel waist is exactly the seam that makes a single `model=` input possible with
  zero downstream change.
- **Complements #400** (the editable surface): read (`dwg.model()`) + edit (verbs) +
  now **input** (`model=`).
