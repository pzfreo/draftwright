# ADR 0003 — Constraint-based layout: one solver for every placeable

- **Status:** Proposed
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
surfaced as lint.

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

**Neutral / follow-ups**
- Performance: hundreds of variables is comfortable for Cassowary; watch the
  largest sheets.
- Euclidean leader length is non-linear; Manhattan is the supported approximation.

## Migration — incremental, subsumes existing behaviour

1. Define `Placeable` + a `LayoutSolver` wrapping Cassowary; generalise
   `_solve_strip_ys` into the axis-neutral 1D primitive it already is.
2. **Prove on one mechanism** — the #77 turned-diameter leaders place via the
   shared 1D solver, not manual pitch (this PR; the bridge to the protocol).
3. Port hole callouts (already Cassowary) → bore leaders → dimension ladders.
4. Retire manual pitch-stacking and fold the post-hoc repair loop into
   validate-and-resolve as passes move in.
5. Tables and GD&T arrive as new `Placeable`s.

## Current state vs target

- **Exists:** the strip/zone allocator, `_solve_strip_ys` (a working 1D
  Cassowary placement), and `Drawing.repair()` (dim-only post-hoc fix).
- **Target:** the `Placeable` protocol, a global `LayoutSolver`, the escalation
  ladder, and all passes migrated onto them. Until then the engine runs the four
  mechanisms side by side; each migration step removes one.

## Related

- [ADR 0001](0001-deterministic-generation-over-editable-dsl.md) — deterministic
  generation; layout quality is a pillar of that determinism.
- [ADR 0002](0002-iterate-via-lint-critique-and-domain-repair.md) — the repair
  loop this ADR generalises from "nudge dims" to "constrain and re-solve."
- Issue #77 (external turned diameters — the first `Placeable`); the phased
  layout issues (protocol/solver → port callouts → port dims → tables/GD&T);
  #61/#62 (GD&T — beneficiaries of the unified placement).
