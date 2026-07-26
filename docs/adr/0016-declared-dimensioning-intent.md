# ADR 0016 — Declared dimensioning intent: capture what to measure, let the engine place it

- **Status:** Proposed
- **Date:** 2026-07-26
- **Deciders:** Paul Fremantle (pzfreo)

## Context

A user reading a generated `Sheet` script today sees the part's **features** — one
declarative line per hole / slot / pattern / chamfer / step — and, since the
self-describing-emitter change, a comment mirroring each. What they do **not** see is
the drawing's *dimensioning*: the ⌀ callouts, the location ladders, the overall
envelope spans, the pitch dims. Those are derived by the planner (`plan_dimensions` →
one `DimensionGroup` per feature, ADR 0015) and placed by the collect-then-solve
corridor solver (ADR 0014). They are real, dominant marks on the sheet, yet they are
invisible in the code and cannot be adjusted from it.

Three ways to express drawing intent already exist, and they bracket the gap:

1. **Feature declarations** (`sheet.hole(...)`, ADR 0011): declare geometry; the engine
   decides which dimensions it earns. You cannot say *which* of a feature's possible
   measurements matter — the planner's rule set decides.
2. **Aspect declarations that ride a feature** (`.thread("M3x0.5")`, `.finish("1.6")`,
   `.note(...)`, `.tolerance(...)`): declare an *enrichment* semantically; it folds onto
   the feature's callout and **does not touch the layout engine**. This is the archetype
   the user pointed at — adding a thread detail enriches the drawing without denying
   auto-placement.
3. **Edit emphasis** (`dimension(..., pin=, priority=)`, ADR 0012): rank or anchor a
   dimension in the global solve without hardcoding its position (`intents.py`, drained
   by `Drawing.finalize()`).

The missing axis sits between (1) and (3): a way to declare **which measurements the
drawing should carry, and why** — a *dimensioning-selection intent* — expressed in
feature/world terms, never in page coordinates or strip assignments, routed through the
same planner and corridor solve as the automatic dimensions. Concretely, today you
cannot declare "dimension the pitch between these two bosses", "give this wall an
overall-thickness dimension", or "do **not** auto-dimension this hole's location" as
*intent* and let the engine place the result.

The near-miss primitive is `sheet.dimension(...)` (`model.declare.authored_dimension`),
used for imported AP242 PMI. But it is a *materialized* dimension — it carries
`ref_pts` / `ref_bbox` / `at` — so it leans toward the hardcoded-geometry form ADR 0001
deliberately rejected, not toward scale-independent intent. It is the escape hatch for
"a source already measured this", not the intent layer for "this measurement matters".

**This is not in tension with ADR 0001 — it is its unfinished half.** ADR 0001 §2 says
editing should be exposed as "the domain vocabulary a model already knows (features
such as holes/bores/sections; **intent such as 'dimension this bore's depth'**), not the
internal strip/zone machinery." Feature and aspect intent landed; *dimensioning-selection*
intent did not. ADR 0001 rejected only the fully-expanded, place-everything editable DSL.

## Decision

**Add a declared dimensioning-intent layer: semantic statements of _what to measure_
(and what to suppress), resolved by the planner and ADR 0014 solver into placed
dimensions. Intent never carries page geometry, never assigns a strip, and never
overrides the layout engine — it enters the same population the automatic dimensions
do.** The generated script becomes a mirror of the drawing's *intent*, not its geometry:
every line is a droppable / editable declaration; the engine still owns derivation and
placement; re-running re-solves.

Three kinds of dimensioning intent, all scale- and placement-independent (anchored in
feature / world terms, per the ADR 0012 constraint):

- **Additive** — declare a measurement the planner would not auto-produce, referencing
  features or faces, not coordinates: a span between two features, a pattern pitch, a
  wall thickness, an angle, an overall extent. It becomes a `CorridorCandidate` in the
  shared solve like any planned dimension.
- **Suppressive** — declare that an auto dimension should *not* appear, by its **semantic
  identity** (feature + `DimParameter` role), not by name-string or position. "Drop this
  hole's location dim" is intent; the solver simply never receives that candidate.
- **Emphasis** — rank / anchor, already ADR 0012 (`pin`, `priority`). Folded in here as
  the third face of the same layer so the three read as one vocabulary.

The `.thread` / `.finish` pattern is the template and the invariant: a dimensioning
intent enriches or edits the dimension *set* the drawing carries, and the corridor solve
still decides ordering, dedup, priority-drop, and position. **Intent selects; the engine
places.**

### Constraints this forces (the honest edges)

- **Auto dimensions must be semantically nameable.** Suppression and override require a
  stable identity for "the location dimension of hole H" that survives a re-solve at a
  different scale. That identity is the planner's `(feature, DimParameter role)`, not a
  page-keyed annotation name — this leans on ADR 0010 provenance and the ADR 0015
  planner keeping parameter roles stable. Additive intent needs no such key; **suppressive
  intent is gated on it.**
- **Honest reconciliation needs the full recompose (#426/#707).** Suppressing or
  re-emphasizing an *automatic* dimension means the finalize path must reconstruct the
  automatic candidate population and co-solve it with the declared intents — the global
  recompose ADR 0012 Amendment 1 records as still open. Until it lands, `Drawing.finalize()`
  drains *recorded* intents against already-committed annotations as obstacles; it does not
  reconcile against the auto-plan. So **additive intent is reachable on today's machinery
  (it is a new candidate); suppressive / full-mirror intent depends on #426/#707.** This
  ADR therefore *motivates* completing that recompose rather than routing around it.
- **Intent stays declarative and order-independent.** Two intents competing for one span
  dedup like coincident auto candidates; ties break by deterministic key (ADR 0001). An
  infeasible intent (off page) drops with lint like any candidate — declaring a dimension
  is a strong request, not a bounds override.
- **The emitter only mirrors intent it can round-trip.** The self-describing comment stays
  the honest floor: the generated script gains dimensioning-intent lines only for the
  intents the engine can faithfully re-solve, never decorative lines that re-run cannot
  reproduce (the same fidelity contract `emit_sheet_script` holds for features).

## Consequences

- A new intent vocabulary on `Sheet` / `Drawing` (verb names deferred to the roadmap):
  additive measurement declarations (feature/face-referenced), and a suppression verb /
  per-feature `.no_location()`-style aspect. `pin` / `priority` are re-documented as the
  emphasis face of the same layer.
- `plan_dimensions` (ADR 0015) grows an intent input: declared additive measurements join
  the planned `DimensionGroup`s; declared suppressions filter them. The corridor solve
  (ADR 0014) is unchanged — it still receives one candidate population per strip.
- `intents.py` (ADR 0012) is the recording home; `Drawing.finalize()` /
  `_PASS_SEQUENCE` the drain. Additive intent lands there first; suppression follows the
  #426/#707 recompose.
- `sheet_emit` gains an intent-mirroring pass: after the feature basis, emit the
  dimensioning intents (and views / sections, ADR-0016-adjacent config work) the drawing
  carries — each a commentable, editable *intent* line, with the auto-derived remainder
  still produced by the engine on re-run.
- Extends ADR 0011 (declare features) to declare *dimensioning intent*; extends ADR 0012
  (edit one dimension) to declaring the dimension *set*; consumes ADR 0015 (planner) and
  ADR 0014 (solve); fulfils ADR 0001 §2. Does **not** reintroduce the ADR 0001 hardcoded
  DSL — intent carries no geometry.

## Proposed phased work

1. **Additive intent, feature-referenced.** A scale-independent additive-measurement
   intent (span-between-features, pitch, thickness, overall) recorded on the model and
   entered as a `CorridorCandidate`; reachable on today's solve. Re-uses / narrows
   `authored_dimension` so intent and materialized-PMI stay distinct.
2. **Semantic identity for auto dimensions.** Expose a stable `(feature, role)` handle for
   planned dimensions (ADR 0010 provenance + ADR 0015 roles) so intent can *reference* an
   auto dimension.
3. **Suppressive intent.** `undim` / `.no_location()` filtering `plan_dimensions` output
   by that handle.
4. **Full recompose (#426/#707).** Reconstruct the automatic population at finalize and
   co-solve with declared intents, making suppression / emphasis honest and script/direct
   output convergent.
5. **Emitter intent-mirror.** Emit the round-trippable dimensioning intents (with views /
   sections) as editable lines; keep the self-describing comment as the floor for the rest.

## Open questions

- The additive-intent verb surface: which semantic measurements to support first (span,
  pitch, thickness, angle, radius, overall), and how a two-feature reference reads
  fluently (`sheet.measure(a, b)` vs a feature-handle method).
- Whether suppression is a standalone verb (`sheet.undim(hole)`) or a per-feature aspect
  (`sheet.hole(...).no_location()`), and how it composes with `of(...)` on detected models.
- Guards to add when this lands: a fidelity test that an emitted intent re-solves to the
  same dimension (mirroring `test_sheet_emit` parity), and an audit that intent carries no
  page geometry (the scale-independence invariant).
