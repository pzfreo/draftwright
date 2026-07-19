# ADR 0012 — User annotation edits are pinned, priority-ranked corridor candidates

- **Status:** Accepted; partially landed (2026-07-08; accuracy correction
  2026-07-19 — see Amendment 1). Supersedes #396, extends #388/#426.
- **Date:** 2026-07-07
- **Deciders:** Paul Fremantle (pzfreo)

## Context at the time of the decision

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

## Original decision and target

The following records the target accepted on 2026-07-07. Read it with Amendment
1 for the narrower behavior that actually landed.

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

## Intended consequences

- `dimension()` gains `pin=` and `priority=` and records a dimension intent on the model
  (in addition to, or in place of, the raw annotation). `place_dim()` is deprecated for
  normal editable scripts; the raw single-slot carve remains as the *unsolved,
  scale-frozen* escape hatch, honestly documented.
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

## Original phased work

1. **Intent + knobs.** A scale-independent dimension-intent representation on the model;
   `pin=`/`priority=` on `dimension()` recording it. `place_dim()` remains raw-coordinate
   fallback only.
2. **Solve the intents.** The orchestrator collects user dimension intents as
   `CorridorCandidate`s (`anchored=pin`, `priority`) into the shared solve.
3. **Recompose.** `finalize_drawing(dwg)` re-runs the corridor solve over auto + user
   intents (the #388 Phase 2 recompose).
4. **Below/right dependency.** Fold the below/right ladders into the corridor so a user dim
   there co-solves (#477).
5. **Escape hatch.** Keep deprecated raw single-slot `place_dim` for arbitrary-page-coordinate,
   unsolved placement, clearly documented.

## Amendment 1 — recorded-intent corridor solving landed (2026-07-08; corrected 2026-07-19)

**Status:** Accepted; partially landed. Umbrella **#511** closed 2026-07-08,
but the implementation is narrower than the original global-recompose proposal.

The recorded-intent mechanism shipped:

1. **Intent + knobs** — `Drawing.dimension(..., pin=, priority=)` records a
   scale-independent dimension intent (`draftwright/intents.py`) when the
   drawing is in deferred mode (`dwg.deferred()`/`dwg._defer_intents`).
2. **Solve the intents** — `Drawing.finalize()` (not the module-level
   `finalize_drawing(dwg)` named in the original proposal — see below) routes
   recorded dimension intents through the same `CorridorCandidate`/
   `solve_corridor`/`drain_corridors` machinery the auto-pass uses
   (`annotations/_common.py`), `anchored=pin` and `priority=priority` carried
   straight through. Landed via #531 ("Add pinned locate candidates") and #532
   ("Route pinned dimension intents through corridor"), both merged 2026-07-08.
3. **Recorded-intent drain, not full recompose** — `Drawing.finalize()` is
   idempotent and drains the intents recorded while the drawing is deferred.
   It does **not** rerun `_auto_annotate`, reconstruct the automatic candidate
   population, or co-solve a later user edit with annotations already committed
   to the drawing. Existing annotations are obstacles for that drain. The
   `_PASS_SEQUENCE`-keyed drain describes routing of the recorded subsets; it
   is not one sheet-global solve.
4. **Below/right dependency (#477)** — closed 2026-07-08 as part of the
   broader "finish the ADR 0009 unification" umbrella; the below/right ladders
   are corridor registrants, so a user dim there co-solves.
5. **Escape hatch** — `Drawing.place_dim()` is marked deprecated in its own
   docstring, pointing callers at `dimension(..., pin=True)` /
   `locate(..., pin=True)`, and documents itself as the raw, unsolved,
   scale-frozen fallback this phase called for.

One naming correction from the original proposal: the recompose entry point is
the `Drawing.finalize()` method, not a standalone `finalize_drawing(dwg)`
function — the `deferred()` context manager (#426) calls it automatically on
exit, and `export()`/`export_pdf()` call it unconditionally (a no-op once
intents are drained) so a script that never opts into `deferred()` is
unaffected.

Consequently, the original **one global solve over automatic features plus user
edits remains a target, not a landed guarantee**. Generated scripts can approach
that target when they faithfully record the same semantic intents before
finalization, but direct and script output are not guaranteed equal; detail-view
reconstruction is a known example. Track full script/direct equality and
automatic-plus-user recomposition in #426/#661/#707. ADR 0014 is the current
placement contract; ADR 0009 is retained only as its historical predecessor.

The 2026-07-19 correction replaces the earlier "fully landed" wording, which
incorrectly equated draining recorded intents with reconstructing and solving
the complete automatic-plus-user population.

## Current contract summary (2026-07-19)

For current callers, this ADR means only the following:

1. Semantic edit verbs may record scale-independent intents while a drawing is
   deferred.
2. `Drawing.finalize()` drains those recorded intents deterministically through
   corridor-specific routing, carrying pin and priority where supported.
3. Finalization does not reconstruct automatic candidates and is not a global
   automatic-plus-user recompose.
4. Existing annotations therefore participate as obstacles, not as members of
   the newly solved population.
5. `Drawing.place_dim()` remains the raw, scale-frozen, unsolved escape hatch.
6. Script/direct equality and full recomposition remain tracked by
   #426/#661/#707.

The original target and phased plan above are retained to show why the intent
mechanism was introduced; this summary plus ADR 0014 describe the behavior that
exists today.
