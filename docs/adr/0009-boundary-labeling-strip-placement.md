# ADR 0009 — Boundary labeling: collect-then-solve per-strip annotation placement

- **Status:** Accepted (2026-06-30)
- **Deciders:** Paul Fremantle (pzfreo)

## Context

A run of layout fixes — #133 (locate side-drilled holes), #225 (locate *every*
side-drilled hole), #293 (dense step-length chains), #305 (coaxial bore callout
crossed by its location-dim line), and the `callout_dropped` /
`location_ref_dropped` / `off_axis_location_dropped` family — keep landing in the
same place and keep being patches, not cures. They share one root cause.

draftwright places a view's annotations into **strips** (`ViewZones`:
left/right/above/below bands around each orthographic view; `_core.py`). Inside
those strips, placement is **imperative, single-pass, and split across mechanisms
that do not share an occupancy model**:

- a **cursor** (`Strip.allocate`) used by envelope dims, step/height ladders, and
  off-axis location dims; and
- a **1-D Cassowary solve** (`LayoutSolver.solve_strip`, kiwisolver; ADR 0003)
  used by bore-callout and turned-diameter leaders.

Several passes write into the **same** strip, each blind to the others. The code
admits it (`annotations/holes.py`): *"the right/below strips are SHARED with hole
callouts and the section hatch, which use other placers and are invisible to the
cursor (#133). So a clean allocation is necessary but not sufficient…"* The
workarounds are post-hoc occupancy checks and tier-retry loops; and when a strip
is full, **which annotation gets dropped is decided by arrival order in the code,
not by priority.**

This is, precisely, the academic problem of **boundary labeling** (Bekos,
Kaufmann, Symvonis & Wolff, 2007): point features inside a view rectangle, labels
on the surrounding strips, joined by leaders; minimise short, **crossing-free**
leaders. The research backing — current-engine critique, the literature, and the
full pros/cons of the two candidate approaches — is in
[`research/annotation-placement-boundary-labeling.md`](../research/annotation-placement-boundary-labeling.md).

ADR 0003 already framed the right *shape* (assignment → placement, an escalation
ladder) but left the assignment layer and escalation under-specified and deferred
the global solve. The strip passes were never actually unified; they still
place-as-they-go. This ADR settles **which concrete model finishes ADR 0003 for
the strips, and which to reject.**

## Decision

Adopt **boundary labeling with a collect-then-solve per-strip stage** (the
research note's *Approach A*). Reject the global metaheuristic/MIP optimiser
(*Approach B*) as the primary direction — see *Alternatives*.

The load-bearing change is a **control-flow inversion**. Today each pass commits
real geometry (`dwg.add(...)`) as it runs. Instead, for each view, run three
phases:

1. **Collect.** Every contributor — bore callouts, location dims, step/
   turned-diameter dims, the section-hatch footprint (as a fixed obstacle) —
   emits a *candidate* (`Placeable` + priority + eligible side(s)). **Nothing is
   placed.** This consumes the **full** per-strip set at once.
2. **Solve.** One optimisation over that set:
   - **Select** — if the strip cannot hold everything, keep the highest-priority
     set that fits; the rest produce a first-class **escalation** signal.
   - **Assign** — side/zone (the discrete, disjunctive choice).
   - **Order** — label order along the strip = **feature order**. This is the key
     move: with order fixed, leaders are **crossing-free by construction** and
     non-overlap collapses to a chain of *linear* inequalities — exactly what
     kiwisolver can solve, which is why the disjunction problem disappears.
   - **Space** — final positions via the existing 1-D Cassowary strip solve.
3. **Emit.** Materialise the chosen geometry from the solve's decisions.

Objective: **minimise total leader length (+ kept priority) subject to
non-overlap and bounds.** Order-fixing kills crossings for free; selection
handles over-capacity; the optimum is a sweep / min-cost matching / dynamic
program (O(n log n)–O(n³)) finished by the 1-D solver already in `layout.py`.

**Why this and not a global solve.** It removes the actual defect class — the
invisible-occupant collision — *by construction*, while preserving the two
properties draftwright cannot give up: **determinism** (ADR 0001) and
**explainability** ("label *i* sits here because order + min-gap +
shortest-leader," not "the annealer landed there"). It is consolidation of parts
that already exist (`Placeable`, `LayoutSolver`, the strips, the drop plumbing),
not a rewrite, and it is the disciplined version of the already-funded #150.

### Relationship to the existing layout ADRs

- **ADR 0003 (constraint-based layout).** This ADR *finishes* 0003 for the
  strips: it makes the **assignment layer** concrete (collect-then-solve, with
  feature-ordered side/zone assignment) and turns the **escalation ladder** from
  prose into the selection step's output. 0003's "global 2-D Cassowary solve"
  stays deferred (#94); this is the per-view inner layer 0003 always implied.
  `Placeable` / `LayoutSolver` are reused unchanged in spirit.
- **ADR 0004 (compose-then-pack).** Orthogonal and complementary. 0004 is the
  **outer** layer (each view is a block; pack blocks disjoint so cross-view
  overlap cannot occur). 0009 is the **inner** layer (place one view's
  annotations into its own strips, optimally). The block footprint 0004 needs is
  exactly the bounding box this stage now produces deterministically.
- **ADR 0008 (the dimensioning planner).** 0008 already separates *what to
  dimension* (planner → render-intent) from *render*. 0009 slots onto that seam:
  the layout stage consumes the **full per-strip intent set** before committing,
  rather than each render pass committing on its own. The planner's intents are
  the natural source of the collect-phase candidates.
- **ADR 0001 / 0002.** Determinism is the reason A was chosen over B. Repair
  (0002) stays a safety net; principled selection/escalation replaces the bulk of
  the ad-hoc `*_dropped` decisions, so repair has less to clean up.

## Consequences

**Positive**
- The invisible-occupant collision class (#133/#225/#305 and kin) is removed by
  construction: one occupancy model per strip, every occupant in it.
- Crossing-free, minimum-leader-length placement within a view; provably optimal
  and fast (O(n log n)–O(n³)).
- Deterministic and explainable; no stochastic placement, no golden-test drift
  risk.
- "Doesn't fit" becomes a **priority-ranked escalation** (→ detail view #306/#54,
  → table) instead of an arrival-order drop. Drops that remain are still lint.
- A new strip annotation type joins the boundary model instead of inventing
  placement from scratch.

**Negative / costs**
- **Control-flow inversion** is real work: passes must return candidates to a
  single layout stage rather than calling `dwg.add(...)` mid-flight. Risk of a
  half-migrated engine running both models at once during the migration —
  mitigated by phasing one contended strip first and keeping behaviour-preserving
  steps.
- **Per-view, not global:** cross-view and inner-vs-outer-zone contention still
  rest on ADR 0004 + the assignment heuristic, not a single global optimum.
- **Modelling friction:** dim *chains*, ladders, and hatching must be coerced
  into the label/leader/obstacle abstraction.
- **Leader style:** optimality results are richest for rectilinear leaders;
  angled leaders (the #305 case) are first-class but weaken the guarantees where
  mixed.

## Alternatives considered

- **Approach B — one global optimisation (MIP or simulated annealing).** Highest
  quality ceiling and eliminates the root cause at the largest scope (across
  placers *and* views), but it conflicts with **determinism** (SA is stochastic;
  MIP solver-sensitive — and golden tests were retired in ADR 0007, so drift is
  less guarded), is far heavier (NP-hard) for sheets this sparse, and is
  hard to debug/lint/repair. **Held in reserve:** revisit only if, after A,
  genuine cross-view / inner-vs-outer-zone conflicts survive compose-then-pack —
  and then prefer the seeded-annealing variant with a re-introduced
  output-stability test. (Research note §4–5.)
- **Status quo + more patches.** Each new fixture finds a new invisible-occupant
  collision; the cost compounds and the drop policy stays arrival-order. Rejected.

## Migration

Phased, behaviour-preserving where possible, contended-strip-first so the model
is validated on the exact recurring bug before the broad sweep. Tracking issue
**#320**; the execution plan and per-phase issues (P0–P5: #317, #321, #322, #323,
#318, #319) live in
[`plans/strip-layout-boundary-labeling-roadmap.md`](../plans/strip-layout-boundary-labeling-roadmap.md).

## Related

- [ADR 0001](0001-deterministic-generation-over-editable-dsl.md) — determinism;
  the reason A is chosen over B.
- [ADR 0002](0002-iterate-via-lint-critique-and-domain-repair.md) — repair stays
  a safety net; principled escalation replaces most ad-hoc drops.
- [ADR 0003](0003-constraint-based-layout.md) — the two-layer model this ADR
  makes concrete for the strips; the global 2-D solve stays deferred (#94).
- [ADR 0004](0004-compose-then-pack-view-blocks.md) — the outer block-packing
  layer; 0009 is the inner per-view layer.
- [ADR 0008](0008-unified-feature-model-and-dimensioning-planner.md) — the
  planner whose intents feed the collect phase.
- Research note: [`research/annotation-placement-boundary-labeling.md`](../research/annotation-placement-boundary-labeling.md).
- Issues: #150 (consolidate 1-D placement — subsumed here), #301/#302/#303
  (layout-cleanliness/convergence — closed out by the new invariants),
  #306/#54 (detail-view escalation — the "doesn't fit" target).
