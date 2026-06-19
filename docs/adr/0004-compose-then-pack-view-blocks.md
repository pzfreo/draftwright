# ADR 0004 — Compose-then-pack: views as blocks carrying their annotation footprint

- **Status:** Proposed
- **Date:** 2026-06-19
- **Deciders:** Paul Fremantle (pzfreo)

## Context

draftwright lays out a sheet in two stages today: `_analyse` places the
orthographic views using *heuristic* space reservations (strip depths, gap
maxes, the balloon halo), then `_auto_annotate` adds the dimensions, callouts,
balloons, **and the hole table** into whatever space is left.

This ordering — **views first, annotations second** — is the root cause of a
recurring class of failures (surfaced while making dense hole patterns legible,
#111/#112):

- The hole table is dropped into a free corner *after* layout and never enters
  scale/page selection, so reserving any balloon space starves it
  (`table_dropped`).
- Balloons can only be reserved on three sides of the plan view; the fourth (the
  bottom) abuts the front view, and pushing the front view down to make room
  cascades into the table and the scale choice.
- `_will_balloon` must *predict* whether a view escalates *before* the views are
  placed — but reserving the predicted space changes the layout enough that the
  part may no longer escalate. The prediction is self-defeating on borderline
  parts.
- `lint()` measures real geometry bounding boxes at O(n²); a balloon-heavy
  drawing (NIST CTC-02, ~60 balloons) sits at the CI timeout.

Each symptom is a patch on the same flaw: annotations are second-class citizens
placed post-hoc, competing for scraps. ADR 0003 settles how *annotations within
a view* are placed (the `Placeable`/Cassowary inner layout). This ADR settles
the layer around it: **how a view and its full annotation set are composed into
one placeable block, and how those blocks are packed onto the page and scaled.**

## Decision

Adopt **compose-then-pack**. Each view becomes a *block* whose footprint
includes its geometry **and all of its annotations** (dimensions, callouts,
balloons) **and its furniture** (the hole table, the iso). The sheet is laid out
by composing the blocks first and packing the complete blocks second.

Two rules make it work and keep it maintainable:

1. **Lay out, don't predict.** A block's footprint is the *actual* laid-out
   extent of its annotations, not an a-priori guess. Escalation ("N callouts or
   one table") is decided by whether the per-instance layout fits, not by
   `_will_balloon`. The prediction problem disappears.
2. **Layout reasons about boxes; geometry is built last.** Annotations are laid
   out as lightweight page-mm **rectangles** in view-local coordinates; the
   packer measures the footprint from those boxes; the real OCC geometry is
   built only once, at the block's resolved page position.

### The footprint is a box layout, not measured geometry

Three footprint representations were considered:

- **Scalar per-side bands** (today's `StripDepths` / `pv_halo`) — fast but lossy;
  it cannot say "balloons here, table there," which is exactly what forced the
  prediction and the cramming.
- **Measured OCC geometry** — a single source of truth, but *slow* (the O(n²)
  `lint()` perf wall we already hit in CI) and it forces annotations to be built
  before placement, then translated.
- **A box layout (chosen)** — annotations laid out as plain page-mm rectangles.
  The block's footprint *is* that box layout; the same layout drives both the
  reservation and the final render positions (single source of truth, no drift);
  it is rectangle math (fast) and decoupled from OCC (testable in isolation).

The deciding factor is evidence, not theory: we have already hit the
measured-geometry performance wall in production. The box layout's one real risk
— box-vs-geometry drift — is controlled by making the box the **contract** the
renderer must fill at the resolved position.

### Scaling and the scale/page search

A drawing mixes two coordinate regimes that behave oppositely under scale:

- **View geometry scales** with the scale factor (a 70 mm part at 2:1 is a
  140 mm box on the page).
- **Annotations are page-mm — scale-independent** (a 3 mm dimension label, a
  9 mm balloon, the hole-table text are constant at any scale).

So a block's footprint is `(scaled geometry box) + (fixed-size annotation
boxes)`. Most of it is scale-invariant. The one scale-coupled input is *which*
annotations exist — legibility/escalation, `world_separation × scale` vs a fixed
page-mm minimum. So composition is a function of scale: `compose(view, scale)`.

This turns the current tangled fixed-point iteration (measure strips → choose
scale → re-measure, ×3) into a clean **monotone search over `(scale, page)`
candidates**:

```text
for (scale, page) in ladder:                 # largest scale first
    blocks = [compose(view, scale) for v]    # decide annotations + lay out boxes
    packed = pack(blocks, page)              # fixed projection topology + free-rect placement
    if packed fits: return (scale, page), packed
```

At a *given* scale the footprint is deterministic, so there is no circular
scale↔footprint loop to iterate — `compose(scale)` fixes the legibility and
escalation choices for that scale. The scattered estimators (`_est_*_depth`,
table size, halo) collapse into one composer per view.

### Relationship to ADR 0003 and the deferred 2D solve

- ADR 0003 governs the **inner** layout (placing a view's own annotations in its
  zones via the `Placeable`/Cassowary system). This ADR governs the **outer**
  layout (composing view+annotation blocks and packing them on the page). A
  block's footprint is produced by running the ADR-0003 inner layout in
  view-local space.
- The **page-level packing stays fixed-topology** (plan above front sharing X;
  side beside front sharing Y; iso and table in free rectangles via
  `place_box` / `fit_box`). It is **not** the full global 2D solve deferred in
  #94, which we keep deferred — most "2D freedom" is forbidden by projection
  alignment, and a global non-overlap solve is disjunctive/NP-hard and
  non-deterministic. Fixed-topology + composed footprints + local free-rect
  placement is the right amount of structure.

### Roadmap (incremental; each step mergeable, byte-identical for unaffected drawings)

1. **Done** — `ViewBlock` data model + block-driven view placement
   (byte-identical foundation).
2. **Hole table as a composed block** — footprint computed up front, packed
   alongside the iso, participating in scale/page selection.
3. **Balloon ring as part of the plan block's footprint** — all four sides; the
   packer makes room (lift the plan view, grow the page, or reduce scale — its
   choice, not hand-coded).
4. **Generalise** — each view's dims/callouts become its block footprint; retire
   the scalar strip-depth heuristics.
5. **Retire `_will_balloon`** — escalation decided from the composed fit.

The standing acceptance test at every step is the **dense-ballooning hard case**
(NIST CTC-02); the golden/geometry suite is the contract that unaffected
drawings do not move.

## Consequences

**Positive**
- The recurring failures dissolve by construction: no post-hoc table cramming,
  no self-defeating prediction, no balloon-vs-table competition. The bottom band
  is just "the plan block's bottom footprint," and the packer places it.
- The page-level "front view down or plan view up?" decision moves *into the
  packer* — we stop hand-writing `PV_Y = FV_Y + …`.
- Performance recovers: layout is rectangle math; OCC geometry is built once at
  the end, not measured during the search.
- The scattered estimators and the 3× measure/choose iteration collapse into one
  `compose(view, scale)` + one scale/page search — fewer mechanisms, more
  testable.

**Negative / costs**
- A box model must be maintained for every annotation type (its page-mm size),
  and the renderer must honour the reserved box. Drift is the failure mode to
  guard against in tests.
- Multi-PR migration with a real risk of a half-composed engine running two
  models at once; mitigated by the byte-identical-per-step discipline and the
  golden suite.
- `compose(view, scale)` must run the *real* legibility/escalation logic (not
  estimate) to be correct — this is where the substance moves, and it must stay
  fast (box math) to preserve the performance win.
