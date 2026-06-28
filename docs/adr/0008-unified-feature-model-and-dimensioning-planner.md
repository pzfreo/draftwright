# ADR 0008 — Unified feature model and a dimensioning planner

- **Status:** Proposed
- **Date:** 2026-06-28
- **Deciders:** Paul Fremantle (pzfreo)

## Context

draftwright recognises a part's features and then dimensions them. Both halves
grew by accretion — one issue at a time — and the shape now shows it.

**Recognition is fragmented across overlapping scanners.** Each rescans
`part.faces()` (or the cylinder set) with its *own* tolerances and filters:

- `analyse_cylinders` — cylinder bands (radius, axial span, `external`).
- `find_bosses` — external cylinders → bosses (diameter, *chamfer-shortened*
  height).
- `find_holes` — bores / counterbores / spotfaces.
- `analyse_face_levels` — transverse Z-face levels (area-filtered) → `step_zs`.
- `find_turned_steps` — axis-general step segments (cylinder bands + transverse
  faces, OD-silhouette-filtered).
- `find_slots` — milled slots.

**Dimensioning is fragmented across orientation-gated passes.** "Dimension a
turned part's steps" is implemented *three times*, each for a different
orientation and convention, gated by `is_rotational` / `axis == "x"` /
`axis == "z"`:

- step **diameters**: X gets a row below the front view (#77), Z a column to the
  left (#131);
- step **heights**: a Z-only ordinate ladder (`dim_step_*`);
- step **lengths**: an X-only chain (`dim_len_*`, ADR 0007 PR-C).

These are not independent designs; they are strata. The recurring problems are
all symptoms of one missing abstraction:

- **Duplicate recognition** (the `analyse_face_levels` vs `find_turned_steps`
  overlap, issue #191).
- **Inconsistent filtering → silent bugs.** A blind bore's flat floor is excluded
  by `find_turned_steps`' OD-silhouette filter but admitted as a *phantom OD
  shoulder* by `analyse_face_levels`' area filter (verified: a bored Z shaft gets
  a spurious step-height dim at the bore floor; lint is silent).
- **Orientation as branches, not data.** Each new orientation (the "what about
  Y/Z?" question) means another gated pass, not a parameter — so coverage and
  quality differ by axis.

Asked "would you design it this way from scratch?", the answer is no. This ADR
records the design we *would* choose and how to migrate to it without a rewrite.

## Decision

Split recognition and dimensioning into two layers, each with one job, and make
**orientation a property of the model, not a branch in the code**.

### 1. One feature-recognition pass → a single part model

A single pass classifies the part's orientation **once** and produces a
structured, view-independent model built on **one** low-level scan with **one**
consistent set of tolerances and filters:

- Turned part: a `Profile` — an ordered list of `Segment(length, diameter,
  external)` along the turning axis — plus concentric bores and off-axis features
  (holes, slots, patterns).
- Prismatic part: envelope + step levels + faces + holes.

The OD-silhouette / internal-vs-external test is applied **once, here**, so an
internal feature face (a bore floor) is never a shoulder *anywhere* downstream,
by construction. No later stage rescans the solid; they read the model.

### 2. One dimensioning planner → a plan

A planner consumes the model + the chosen views and applies ISO rules to decide
*what* to dimension, *which convention* (chain / ordinate / leader), and *where*.
The orientation-specific behaviour that is three passes today becomes **one rule
parameterised by the model**: "a turned part: dimension its segment lengths and
diameters on whichever view shows it lengthwise; use ordinate when the chain is
crowded." X / Y / Z stop being special cases — Y falls out naturally (no view
shows the length → nothing to plan).

### 3. Migrate by strangler, not rewrite

A clean target is **not** a licence to rewrite working code (the same trap as
preferring the design one would write over the code that works). Instead:

1. **Introduce the model** as a new single recogniser (likely generalising
   `find_turned_steps` into the `Profile`, and `analyse_face_levels` into the
   prismatic branch — keeping the *more general* primitive where it is more
   general, per the #191 review).
2. **Migrate consumers one at a time** onto the model: the step ladder, the step
   chain, the diameter row/column, then page sizing — each its own releasable PR,
   verified against the existing tests.
3. **Retire the redundant recognisers** (`find_bosses` for steps,
   `analyse_face_levels` for steps, `find_turned_steps` as a standalone) as their
   last consumer moves.
4. The dimensioning planner emerges **last**, once the passes already read a
   common model — it is then a refactor of placement, not a rewrite of
   recognition.

### 4. #191 is the first step of this, not a standalone dedup

Doing #191 in isolation (route the ladder through `find_turned_steps`) just adds a
*fourth* orientation special-case — more of the same disease. Re-scope it as
"introduce the `Profile` model and move the step ladder + step chain onto it",
the first migration step above. The phantom-bore bug is fixed for free once
filtering is centralised in the model.

## Consequences

- **One source of truth** for features, computed once, consistently filtered —
  the duplicate-recogniser and inconsistent-filter bug classes disappear.
- **Orientation coverage becomes uniform.** Adding an orientation or a feature is
  data in the model + a planner rule, not a new gated pass.
- **The phantom-bore shoulder is fixed structurally**, not patched per-pass.
- **Incremental, low-risk path.** Each migration PR is small and test-gated; the
  engine keeps working throughout. No big-bang.
- **Cost is real.** This is weeks of incremental work, not hours. Each step is
  gated by the geometry-level + `test_e2e_standards` suites and targeted
  behavioural tests. If a particular step is risky (the sizing-path migration is
  the obvious one), stand up a **scoped, disposable** golden gate for that step
  alone and delete it afterwards — draftwright keeps no standing general golden
  gate by design (ADR 0005 §3), since a permanent one freezes improvement. Worth
  it only because turned/stepped dimensioning is an active growth area; if it were
  a one-off, the accreted code would be left alone.

## Risks

- **Sizing path is load-bearing.** `analyse_face_levels` → `step_zs` drives page
  and scale selection. Migrating it risks layout shifts; do it late, with spot
  checks, after the placement consumers are already on the model.
- **Scope creep into a rewrite.** Mitigation: each step must be a small,
  independently-revertable PR with a clear before/after; if a step balloons,
  stop and re-plan rather than pressing on.
- **Model over-design.** The `Profile` should capture only what current passes
  need (segments, diameters, bores, off-axis features); resist speculative
  generality (ADR-less features). Grow it as consumers demand.

## Impact on other ADRs

- **0003** (constraint layout) — unchanged; the planner still emits boxes the
  layout solver places.
- **0004** (compose-then-pack) — unchanged; the planner feeds the same blocks.
- **0005** (pipeline modules) — extended: this carries the compiler-pipeline
  separation into the recognition/dimensioning stages 0005 left as a single
  `analysis`/`annotations` lump. Import direction (DAG) is preserved.
- **0007** (draftwright owns recognition) — built on: the model lives in the
  now-owned `recognition/`, the planner alongside `annotations/`.

## Related

- Issue #191 (the recogniser-duplication trigger; re-scoped as step 1 here).
- The drive-screw thread (ADR 0007 PR-C): the X step-length chain that, added as
  yet another orientation-gated pass, made the accretion obvious.
- ADR 0005 (compiler-pipeline module boundaries and single-owner state).
