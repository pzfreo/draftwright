# ADR 0003 — Constraint-based layout: one solver for every placeable

- **Status:** Accepted (core implemented; the unifying *global 2-D* solve stays
  deferred — #94). The `Placeable`/`LayoutSolver` model, the 1-D strip solve,
  `place_box`/`fit_box`, and pins (#89) have shipped. The **assignment layer**
  and **escalation ladder** for per-view strip placement are made concrete by
  [ADR 0009](0009-boundary-labeling-strip-placement.md) (collect-then-solve
  boundary labeling).
- **Date:** 2026-06-18
- **Deciders:** Paul Fremantle (pzfreo)

## Context

draftwright places many kinds of annotation on a sheet — linear/diameter
dimensions, leaders, hole callouts, centrelines, GD&T frames (#61/#62), and
soon tables (hole tables, BOM, revision blocks). Placement decides *where* each
label, witness line, and leader shaft goes so the result is legible: nothing
overlaps, leaders stay short and uncrossed, dimensions sit at standard offsets,
and a too-busy sheet degrades gracefully rather than producing a tangle.

Today that work is spread across **four mechanisms that do not compose**:

1. **Strip/zone allocation** — `Strip.allocate(slot)` over `fv_zones` etc. The
   principled space-budget; used by step/height dim ladders.
2. **Manual pitch stacking** — bore leaders (and, initially, the #77 turned-
   diameter leaders) compute positions by hand and bypass the allocator.
3. **Cassowary (kiwisolver)** — `_solve_strip_ys` solves bore-callout placement
   along one strip as a 1D constraint problem. The right tool, used in one place.
4. **Post-hoc repair** (#30, ADR 0002) — `Drawing.repair()` nudges *dimensions*
   to clear overlaps after the fact, but **cannot move leaders** and has no
   model of leader length or crossing.

The costs are concrete: leaders are second-class (nothing deconflicts them); a
new annotation type means inventing placement from scratch; stacked-dimension
"nesting" is hand-rolled where a constraint would say it directly; and "it fit"
is decided greedily, so a dense sheet silently produces long or colliding
leaders instead of escalating.

We already pay for a Cassowary solver and have proven it works (mechanism 3).
The question this ADR settles: **what is the single layout model that every
placeable uses, so dimensions, leaders, callouts, tables, and GD&T all place
well together?**

## Decision

Adopt a **two-layer, constraint-based layout architecture**. Every placeable
implements one protocol; placement is one assignment pass feeding one global
Cassowary solve.

The load-bearing insight: **Cassowary is linear, and non-overlap is not**
("A clears B" is a disjunction — left *or* right *or* above *or* below). So a
single solve cannot do layout alone. Layout is two layers:

1. **Assignment (combinatorial).** The discrete choices: which zone/side a label
   takes, which view annotates a feature, the order within a stack, and whether
   N features become N callouts or one *table*. Seeded heuristically from anchor
   position (today's strip model, generalised). Fixing these choices turns each
   non-overlap into a *linear* separation constraint.

2. **Placement (continuous, Cassowary).** Given the assignment, solve **all**
   continuous positions as one global linear system with priorities:
   - *required*: boxes within page; no overlap with views / title block / one
     another (separation direction fixed by layer 1); minimum standoff.
   - *strong/medium/weak*: pull each box to its natural offset; **align** a group
     by a shared offset variable (stacked-dim nesting becomes one equality);
     minimise leader length as `|Δx| + |Δy|` (linear).

### The unifying abstraction: `Placeable`

Every dimension, leader, callout, table, and GD&T frame exposes:

- **anchors** — fixed geometric points it must connect to (dimension: 2 witness
  points; leader/callout: 1 tip; table: 0; note: 0–1).
- **box** — its label/footprint bbox; size from text metrics, position from
  solver variables.
- **dof** — what the solver may move: a dimension slides along its offset axis
  (plus text along the dim line); a leader's elbow+label move with the tip
  pinned; a table is 2-DOF or corner-pinned.
- **connectors** — witness/leader lines linking anchor→box that should not cross
  other boxes.
- **preferences** — natural offset, pull-toward-anchor, alignment-group id.

Once everything is a `Placeable`, **leaders cease to be second-class** — they are
solved by the same system as dimensions, which is the specific gap today.

### The solve pipeline (this subsumes the repair loop)

1. **Assign** each placeable to a zone/side (heuristic seed from anchors).
2. **Build** one Cassowary system from the constraints above.
3. **Solve** once; priorities resolve contention.
4. **Validate** → on residual overlap/crossing, add a separating constraint and
   re-solve (lazy / branch-and-bound). This is the principled replacement for
   #30's nudge-and-hope — *and it works for leaders*.
5. **Escalate** when unsatisfiable (below).

### The escalation ladder (systematises today's drop-lint)

When a fidelity level is infeasible, follow a defined ladder rather than ad-hoc
drops: zone A → zone B → **tabulate** (callouts → hole table) → **detail view**
→ **reduce scale**. Today's `callout_dropped` / `step_dim_dropped` /
`location_ref_dropped` are the scattered manual version; "unsatisfiable" becomes
a real solver outcome that drives the ladder, and a genuine drop is still
surfaced as lint. *[ADR 0009](0009-boundary-labeling-strip-placement.md) makes
this concrete for the strips: the per-strip solve's **selection** step is the
ladder's first rung — a priority-ranked keep/escalate decision over the full
candidate set, replacing the arrival-order drops.]*

## Consequences

**Positive**
- One placement model for every annotation type; a new type (GD&T, tables) drops
  in as a `Placeable` with **no new layout code** — the main payoff.
- Leaders become first-class: deconflicted, length-minimised, escalated.
- Stacked-dimension alignment and nesting become constraints, deleting bespoke
  "outermost" ordering logic.
- Repair (ADR 0002) generalises from "nudge dims" to "add a constraint and
  re-solve," covering leaders and tables for free.
- Tests improve: "assert constraint satisfied" beats brittle bbox assertions.

**Negative / costs**
- The combinatorial assignment layer is irreducible — this is **not** "one magic
  solve." Over-selling Cassowary would mislead; the win is *structure*.
- Global solves can move distant elements surprisingly; "pin near anchor" plus
  priorities mitigate but need tuning.
- **Determinism is mandatory** — stable variable ordering, or drawings and tests
  stop being reproducible. Cassowary is deterministic given stable input order.
- Multi-PR effort; risk of a half-migrated engine running two models at once.

**Editability — the constraint this engine must not break (ADR 0001/0002)**

draftwright's whole point is that humans and AI tweak in *domain vocabulary*
and never touch placement mechanics; the solver lives strictly *below* that
line and must keep it. Two specific risks a global solve introduces, and the
required mitigations:

- **Edit locality.** Adding or nudging one annotation must not silently shift
  unrelated ones. A from-scratch global re-solve violates the human expectation
  of a local change. Mitigation: incremental / warm-started re-solve that
  perturbs minimally, plus the "pin near anchor" priority. An edit's blast
  radius is part of the editability contract, not just an aesthetic.
- **Manual override must win.** When a human or AI places something explicitly
  ("put *this* label *here*"), the solver must treat it as a hard **pin** that
  survives every later re-solve and stays local — never re-derive over a
  deliberate placement. _Partly landed (#89):_ `dwg.pin(name)` / `dwg.unpin(name)`
  are the domain verbs, `Placeable.locked` is the solver-side flag, and
  `repair()` already refuses to move a pinned annotation. _(The pin **state** now
  lives in `registry.py` as its single owner per
  [ADR 0005](0005-pipeline-architecture-and-state-ownership.md) (split complete);
  the override contract here is unchanged, only its home.)_ **Still owed
  by #82:**
  the global 2D solve must honour `locked` (keep it at `natural`, solve the rest
  around it). This remains a hard prerequisite for that solve, not a later nicety.

Keep `Placeable`/`LayoutSolver` an implementation detail: callers edit through
the domain API (`place_dim`, `features`, `annotations`, lint→repair), never by
constructing placeables. As long as that holds, the engine *improves*
editability for AI (state intent, get a correct deconflicted placement) rather
than eroding it.

**Neutral / follow-ups**
- Performance: hundreds of variables is comfortable for Cassowary; watch the
  largest sheets.
- Euclidean leader length is non-linear; Manhattan is the supported approximation.

## Migration — incremental, subsumes existing behaviour

1. Define `Placeable` + a `LayoutSolver` wrapping Cassowary; generalise
   `_solve_strip_ys` into the axis-neutral 1D primitive it already is. *(#79)*
2. **Prove on one mechanism** — the #77 turned-diameter leaders place via the
   shared 1D solver, not manual pitch. *(#77)*
3. Port hole callouts onto the solver *(#80)*; add per-pair gaps *(#81a)*; add
   pin/`locked` so a deliberate placement wins *(#89)*.
4. Add the 2D capabilities the next features need — `place_box` (free-rectangle
   placement for tables/frames) via the hole table *(#93)* — and grow the engine
   through real consumers, not a speculative big-bang solve.
5. GD&T arrives reusing `place_box` / the leader machinery.

## Correction (2026-06-18): the "global 2D solve" is deferred, not central

This ADR originally framed phase 4 as a single **global 2D Cassowary solve**.
On contact with the code that proved **over-scoped**:

- **Cross-pass overlap is rare.** Only one zone (`front.below`) has two passes
  competing, and the fix there is a deterministic shared cursor, not a solve.
- **2D box placement is the real need, and it is not Cassowary.** Non-overlap is
  a disjunction; the practical answer is an exact **free-rectangle finder**
  (`fit_box`/`place_box`, #93) — which the codebase already had 80 % of in
  `_largest_empty_rect`. Tables, GD&T frames, and BOM/revision blocks all reuse
  it; *that* is the genuine, reusable 2D capability.
- **Non-crossing leader routing** (the genuinely hard, combinatorial part) is
  built **only when a real fixture needs it** — adjacent (leaderless) balloons
  cover typical parts first.

So the engine grows **per real consumer**, and a monolithic global 2D Cassowary
solve is **deferred until a part actually forces it (tracked in #94), and may
never be needed.** This is a deliberate scope correction, not an omission.

## Current state vs target

- **Exists:** the strip/zone allocator; `LayoutSolver` with 1D `solve_strip`
  (+ per-pair gaps) and 2D `place_box`; `Placeable`/`locked`; pin/override
  (#89); hole callouts + turned diameters on the solver; `repair()`.
- **Target:** the escalation ladder + tables/balloons (#93) and GD&T (#61/#62)
  built on the above. The full global 2D solve (#94) remains deferred unless a
  real part forces it.

## Related

- [ADR 0001](0001-deterministic-generation-over-editable-dsl.md) — deterministic
  generation; layout quality is a pillar of that determinism.
- [ADR 0002](0002-iterate-via-lint-critique-and-domain-repair.md) — the repair
  loop this ADR generalises from "nudge dims" to "constrain and re-solve."
- [ADR 0005](0005-pipeline-architecture-and-state-ownership.md) — module
  boundaries and single-owner build state; `layout.py` is unchanged, but pin
  state moves to `registry.py`.
- [ADR 0009](0009-boundary-labeling-strip-placement.md) — makes this ADR's
  assignment layer and escalation ladder concrete for per-view strip placement
  (collect-then-solve boundary labeling); the per-view inner layer to ADR 0004's
  outer block packing.
- Issue #77 (external turned diameters — the first `Placeable`); the phased
  layout issues (protocol/solver → port callouts → port dims → tables/GD&T);
  #61/#62 (GD&T — beneficiaries of the unified placement).
