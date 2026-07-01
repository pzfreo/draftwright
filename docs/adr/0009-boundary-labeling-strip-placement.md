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

1. **Collect.** The stage consumes the planner's **render-intents** for this view
   (ADR 0008) and *measures* each into a placement **candidate** — a geometry-only
   `StripCandidate` carrying the site anchor, label-box size, and priority (and an
   eligible-sides field once the multi-side *assign* step lands, P2). The candidate
   **is a measured render-intent**: the collect
   step (in `annotations/`, which may depend on the IR) projects intent → page
   geometry, and hands the solver only that geometry, so the solver stays a leaf
   with no dependency on the IR. Fixed obstacles that are *not* placed — the
   section-hatch footprint, the title block (`strip_obstacles`, P0b) — enter the
   solve as things to avoid, not as candidates. **Nothing is placed yet**; this
   consumes the **full** per-strip set at once.
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

## Amendment 1 — Escalation objects (P5-strand-2, 2026-07-01)

**Status:** Accepted (design); scaffolding first. Tracker **#351**.

**Problem.** Today "this couldn't be placed" is a stringly-typed side effect: seven
scattered codes (`callout_dropped`, `location_ref_dropped`, `off_axis_location_dropped`,
`slot_dim_dropped`, `step_dim_dropped`, `placement_unsatisfiable`, `table_dropped`) are
emitted via `_record_build_issue(...)`, and the escalators *pattern-match the strings* to
act — `_maybe_tabulate_holes` greps for two of them and always applies **one** remedy
(hole table + **one balloon per hole**), while step crowding routes through a separate
channel (`_detail_requests`/`_resolve_details`). So the escalation *policy* is entangled
with lint text, and the remedy is one-size — the root cause of the CTC-02 balloon ring
that cannot fit (#348).

**Decision.** Make "couldn't place" a **first-class object** emitted at the failure point,
and separate *collecting* failures from *deciding* remedies.

```
Escalation(kind, view, feature, reason, remedies)
  kind:     "callout" | "location" | "slot" | "step" | "pmi"
  feature:  IR feature/key ref — carries PATTERN membership
  reason:   "strip_full" | "illegible" | "corridor_blocked" | "no_room"
  remedies: ranked candidates the resolver may pick, e.g. ("group_balloon","table","detail","drop")
```

- **Placers collect, don't decide:** append an `Escalation` to `dwg._escalations` instead
  of recording a `*_dropped` string. The strip carve already knows *why* it failed, so
  `reason` is free.
- **One resolver pass** (subsuming `_maybe_tabulate_holes` + detail resolution): group by
  `(view, feature-or-pattern)`; pick a remedy per group by a fixed policy ladder (the D4
  order); execute it; and emit the **same `*_dropped` codes only for what stays
  unresolved** — so coverage lint + the cleanliness ratchet keep working unchanged (the
  strings remain the lint *surface*; the *decision* is now object-driven).

**Ratified sub-decisions (user, 2026-07-01):**
1. **Pattern grouping = ISO** — a recognised bolt-circle / linear / grid escalates to
   **one balloon tagging the pattern once** (with an `n×` count) near the feature, not one
   balloon per member. CTC-02 is largely patterned, so its ring collapses from dozens to a
   handful — this is the #348 fix.
2. **Scattered-hole fallback = table + balloons** (today's behaviour) for now; a **zone
   grid reference** (A1/B2) is a separate, later option — filed as its own issue (**#352**).
3. **PR-1 = scaffolding only** (the `Escalation` type + `dwg._escalations` collector, no
   routing) so the first diff is byte-identical; routing + the ISO grouping follow.

**Migration (each its own gated PR).** (1) scaffolding — type + collector, no behaviour
change; (2) route the hole path (callouts + locations) through it, resolver *reproduces*
`_maybe_tabulate_holes` → byte-identical; (3) add ISO pattern-grouping (`group_balloon`) →
first intended drift, fixes #348, re-bless CTC-02; (4) fold in slots/step/pmi, delete the
string-grep. Keeps the byte-identity tripwire through steps 1–2.

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
