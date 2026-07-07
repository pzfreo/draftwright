# ADR 0012 — User annotation edits are pinned, priority-ranked candidates in the one global solve

- **Status:** Accepted (decision; work pending — supersedes #396, extends #388/#426)
- **Date:** 2026-07-07
- **Deciders:** Paul Fremantle (pzfreo)

## Context

Two placement worlds exist today and do not meet:

1. **The automatic pass** (`build_drawing` → `_auto_annotate`) is *batch-solved*: every
   automatic dimension is collected as a `CorridorCandidate` and placed by one
   `register_corridor` → `solve_corridor` per shared strip (ADR 0009). A single solve
   sees the whole candidate set, so it orders the ladder crossing-free, dedups coincident
   spans, and — since #357 — drops the lowest **priority** over capacity.

2. **A user/AI edit** adds a dimension through `Drawing.place_dim()` (and its feature-
   referenced wrapper `Drawing.dimension()`), which runs *after* that batch is committed.
   It uses the **single-position carve** (`carve_free_position`): it clears already-placed
   annotations but is not a candidate in any solve. So an edited dimension gets no
   priority selection, no crossing-free re-ordering, and no dedup against a coincident
   auto dim.

`place_dim` is invoked **only** on the edit path — `dimension()` delegates to it,
`linting/suggest.py` emits `dwg.place_dim(...)` as a fix snippet, and the imperative
`--script` emits it as an editable example. The auto pass never calls it. So the two
worlds are cleanly separated — but the separation is the problem: the lint actively
tells a user "add this dimension" → they paste a `place_dim`/`dimension` call → repeat
for several fixes. Each lands incrementally and none co-solve, so after a few edits the
sheet **can fit sub-optimally** (dims that the batch would have ordered, dropped by
priority, or deduped). That is the intended edit workflow, so the failure is real, not
hypothetical.

## Decision

**A user annotation edit is a *dimension intent* carrying `(pin, priority)`, placed by the
same global corridor solve as the automatic dimensions — not a raw single-slot poke.**

- **Pin** — the user fixes a dimension where they put it. Realised by the solver's
  existing `StripCandidate.anchored` / `_ANCHOR_WEIGHT` (a weight that dominates the
  weighted-median so the label wins its pool and stays put while the rest flow around it).
- **Priority** — the user sets its rank in the hierarchy. Realised by the existing
  `StripCandidate.priority` (survives over-capacity, orders the ladder) plumbed through the
  corridor in #357.
- **One global solve** — auto candidates and user-edit intents form a single population
  fed to `solve_corridor`; a *recompose* (`finalize_drawing`) re-runs it after edits.

**The layout algorithm already supports this** — `anchored` and `priority` both exist on
`StripCandidate` today. The missing part is *representation and wiring*: an edit must
become a **model intent**, not a raw annotation, so the batch can (re-)consume it.

Why pin is load-bearing, not a nicety: the standard objection to "re-solve on every edit"
is that it *moves the user's other dimensions*. Pin is the escape valve — a user who
wants a dimension to stay pins it, and the solve honours it while re-flowing the rest. So
the user never fights the solver: they express intent (*this one stays*, *this one
outranks that*) and the global solve respects it. This is strictly better than either
frozen imperative placement (never re-optimised) or an unconstrained re-solve (jarring).

### Constraints this forces (the honest edges)

- **Intents must be scale-independent.** A re-solve may run at a different scale/page, so
  an intent is anchored in **world / feature terms**, not baked page coordinates. A
  fully-free arbitrary-page-coordinate `place_dim` cannot be a re-solvable intent — it
  stays a raw annotation (the documented escape hatch), and only feature/world-anchored
  edits join the solve.
- **The target strip must be a corridor registrant.** A user dim co-solves only on a strip
  that goes through `register_corridor`/`solve_corridor`. Today that is the above-view
  corridors (locations/slots/GD&T); the below/right ladders are not yet registrants (the
  #477 "below/right decision"). So a user dim below/right co-solves only once those ladders
  join the corridor — this decision therefore **pulls the below/right fold-in in as a
  dependency**, rather than leaving it a non-goal.
- **Conflict policy.** Competing pins for one slot are broken by `priority`; equal priority
  is deterministic by key (ADR 0001). A pinned intent that is genuinely infeasible (off
  page) drops with lint like any candidate — a pin is a strong weight, not an override of
  the page bounds.
- **Performance.** A full re-solve per edit is costly, so edits accumulate on the model and
  a *recompose runs the solve once* (`finalize_drawing`), rather than per `place_dim` call.

## Consequences

- `place_dim()` / `dimension()` gain `pin=` and `priority=` and record a dimension intent
  on the model (in addition to, or in place of, the raw annotation). The raw single-slot
  carve remains as the *unsolved, scale-frozen* escape hatch, honestly documented.
- `finalize_drawing(dwg)` (the #388 Phase 2 recompose) becomes the batch-solve-everything
  step for the edit path: re-run `_auto_annotate`'s corridor collection over the current
  model — auto features **plus** user dimension intents — and re-solve. Pinned intents
  anchor; priority ranks.
- Supersedes **#396** (which asked to route `place_dim` through collect-then-solve): the
  answer is not to make each call re-solve, but to make edits *intents* the recompose
  solves globally. Extends the **#388/#426** editable-surface epic (record-then-finalize)
  and consumes **ADR 0009** (the solve) + **ADR 0010/0011** (intents in the model).
- The **#477 below/right fold-in** is promoted from an open non-goal to a dependency (a
  user dim on a below/right strip needs those ladders in the corridor).

## Phased work (tracking issue to follow)

1. **Intent + knobs.** A scale-independent dimension-intent representation on the model;
   `pin=`/`priority=` on `dimension()`/`place_dim()` recording it.
2. **Solve the intents.** The orchestrator collects user dimension intents as
   `CorridorCandidate`s (`anchored=pin`, `priority`) into the shared solve.
3. **Recompose.** `finalize_drawing(dwg)` re-runs the corridor solve over auto + user
   intents (the #388 Phase 2 recompose).
4. **Below/right dependency.** Fold the below/right ladders into the corridor so a user dim
   there co-solves (#477).
5. **Escape hatch.** Keep raw single-slot `place_dim` for arbitrary-page-coordinate,
   unsolved placement, clearly documented.
