# ADR 0014 — Collect-then-solve annotation placement (as built)

- **Status:** Accepted (2026-07-18). **Supersedes
  [ADR 0009](0009-boundary-labeling-strip-placement.md)** (#697): after 9
  amendments + 2 dated notes, 0009 no longer functioned as a decision record —
  its base Decision text described a solver that was later retired and a phase
  that shipped differently. This ADR states the collect-then-solve placement
  model **as it exists in the code today**, in one pass. 0009 is frozen as the
  historical why-trail; read it for *why* each piece evolved, never for current
  state. Changes go here.
- **Deciders:** Paul Fremantle (pzfreo)

## Context (short — the full story is 0009's)

A recurring defect class (#133/#225/#305: the "invisible occupant" — one strip,
several placers, no shared occupancy model) motivated a control-flow inversion:
render passes stop committing geometry as they run, and instead **collect**
every strip occupant as a candidate so **one solve per strip** places the whole
set. This is boundary labeling (Bekos et al. 2007); the research backing is
[`research/annotation-placement-boundary-labeling.md`](../research/annotation-placement-boundary-labeling.md).
The migration is complete (#636, epic #635): the guarantee below now holds for
every automatic-pass strip occupant **by construction**.

## Decision — the model as built

Per view, per strip: **collect → solve → emit.**

- **Collect.** Each render pass measures its render-intents (ADR 0008 / ADR
  0015) into candidates and *registers* them — `register_corridor` queues a
  `CorridorCandidate` into the run's `PlacementContext.corridor_batch`
  (`annotations/_common.py`), keyed by `(view, side)` — instead of calling
  `dwg.add(...)` mid-flight. Failures are first-class `Escalation` objects on
  `ctx.escalations` (kind/view/feature/reason/remedies), resolved by one
  resolver pass (`_maybe_tabulate_holes`, `annotations/orchestrator.py`) — not
  stringly-typed lint greps.
- **Solve.** `drain_corridors` runs once, after every corridor-feeding pass has
  registered, and executes one `solve_corridor` per strip:
  - **Select** — dedup coincident spans by `(dedup, precedence)` (the
    higher-precedence measurement survives; a dropped winner promotes its top
    loser); over capacity, `plan_strip` (`layout.py`) drops the lowest
    `(priority, key)` — a ranked selection, not an arrival-order drop.
  - **Assign** — the original multi-side assign step was evaluated (P2/#322)
    and **not needed**: each pass picks its candidate's strip before solving,
    with alternate-side fallthroughs in `on_drop` callbacks. What survives as
    assignment is (a) segment assignment within a carved strip
    (`carve_free_segments` + innermost-first fill in
    `place_strip_candidates`), and (b) the balloon pass's genuinely global
    band assignment — a deterministic min-cost max-flow solve
    (`_assign_balloon_bands`, `layout.py`, #516).
  - **Order** — label order along the strip = site/feature order (candidates
    sort by anchor coordinate), so leaders between **distinct** strip-axis
    coordinates are **crossing-free by construction**; coincident sites
    tie-break by key for determinism, which is not crossing-optimal
    (`plan_strip`'s own docstring carries the same qualifier). Corridor
    candidates additionally carry an `order` key segregating size dims from
    the monotonic datum-location ladder (#346).
  - **Space** — the deterministic minimum-total-leader-length **L1 solve**:
    `_solve_strip_1d_pava` (`layout.py`), weighted-median PAVA with per-pair
    gaps, a global box clamp, and a fixed *lower-median* tie convention —
    deterministic by construction (ADR 0001), pure standard library.
    **Anchoring** (`StripCandidate.anchored` → `_ANCHOR_WEIGHT`) pins a
    candidate at its natural position while the rest flow around it; it is a
    spacing hint, not a drop immunity.
- **Emit.** `place_strip_candidates` carves the strip around the *complete*
  occupancy (`strip_obstacles` — full rendered footprints, decomposed per
  stroke since #685, not label boxes), evaluates candidates on analytical or
  probe footprints (`dim_footprint`, #602 — no OCC build per probe), then
  builds each survivor **once** and re-validates its real box (corridor
  blockers, the `forbid` title-block box, out-of-band obstacles) — a
  prediction miss degrades to a later-segment retry, never a collision.
  Feature provenance is recorded at this drain seam
  (`CorridorCandidate.feature` → `dwg.add(..., feature=)`, ADR 0010).

**One stage order, two entry paths.** `_PASS_SEQUENCE`
(`annotations/orchestrator.py`, #699 slice b) is the single canonical stage
tuple; both `_auto_annotate` and the finalize drain (`Drawing._drain_intents`)
hand their stage dicts to the shared `run_stages` executor, so neither path can
run a stage the sequence does not name, nor in a private order. The `"drain"`
stage is `drain_and_reconcile` — `drain_corridors` followed by
`reconcile_witness_labels` (#690, label shifts for witness crossings) — shared
verbatim by both paths. `drain_corridors` also coordinates view corners:
before each strip solves, the innermost-tier footprints of not-yet-drained
same-view siblings' **force** candidates join its obstacles, and `on_drop`
fallthroughs are deferred via `ctx.post_drain` until every strip has drained.

**Best-effort leader decoration places after the drain** (#733, generalising
the grooves precedent): the machined-feature leader-callout passes
(chamfers/fillets/flats/pockets/grooves) sit *after* the `"drain"` stage in
`_PASS_SEQUENCE`, so a principal dim that registers early but places only at
the drain can never have its strip stolen by an immediate callout — the
callouts' clear-room check sees the full drained occupancy and yields (a
warning-level drop) where a principal dim now sits. Pre-#636 the ladder's
early *placement* enforced this implicitly; once it became register-then-drain,
a pocket callout could fill the front-right strip and hard-drop the forced
overall-height dim (CTC-04). Ordering, not reservation: a predicted-footprint
reserve was tried and rejected — phantom reservations displaced callouts into
exactly the space other principals needed.

**Policy B** (two-precedent pattern, ratified 2026-07-02 — 0009 Amendment 2):
when avoiding an occupant would cost more than a bounded relocation, keep the
annotation at its natural position and accept a visible, logged crossing —
never an unbounded search, never a silent drop of a real annotation for a
placement reason alone. Realised as `force=True` candidates (a second
`place_strip_candidates` pass that skips the corridor check) and the permanent
`BENIGN`/`SPACE-CONSTRAINED` entries in `tests/test_layout_cleanliness.py`'s
`_KNOWN_OVERLAPS` (only `PENDING` entries are debt).

## The by-construction guarantee and its exemptions

Every **non-exempt** automatic-pass strip occupant is a candidate in the shared
solve; the exempt placements below run only *after* the drain (or off the strip
axes entirely), so they see the completed occupancy rather than racing it. That
is what removes the invisible-occupant collision class — *provided* no pass
regresses onto the solver-invisible single-position carve
(`carve_free_position`, `annotations/_common.py`). A **fail-closed guard**
(`tests/test_carve_free_position_callers.py`, `_ALLOWED_CALLERS`) pins the
only permitted callers; any new caller anywhere under `annotations/` trips it.
The current allowlist, each an explicit exemption:

- **`_place_pitch_dim`** (`holes.py`) — *permanent*: the pitch fallback
  searches an arbitrary diagonal outward vector, so its dim cannot occupy a
  1-D axis-aligned strip tier and cannot be a solve candidate at all.
- **`render_gdt`** and **`render_plates`** (`from_model.py`) — primary
  placement *is* a corridor candidate; the carve runs only in a
  `ctx.post_drain`-deferred drop fallthrough, after every corridor has drained
  (so it cannot preempt a sibling's reserved corner).
- **`add_feature_callout`** / **`add_feature_location`** (`holes.py`) — manual
  post-build verbs: a single user-driven annotation onto a *finished* sheet,
  where every occupant is already placed and there is no shared drain to join
  (the #426 manual-edit half; cousins of the ADR 0012-exempt `place_dim`).

A genuine new exemption must be recorded **in this list first**, then added to
the allowlist (the guard's failure message cites the ADR 0009 note this
section replaces).

## Glossary — one concept, three names

- **Strip** — the geometric record: `_core.Strip`, a 1-D annotation band
  adjacent to a view (`anchor`/`outer_limit`/`direction`/`gap`/`spacing`).
  The mutable `allocate` cursor is retired (#150); placers read its bounds via
  `strip_free_span` and carve.
- **Zone** — the per-view grouping of strips: `_core.ViewZones`
  (`right`/`above`/`below`/`left`), instantiated by `analysis.py` as
  `fv_zones`/`pv_zones`/`sv_zones`. "Zone" names the collection, "strip" the
  individual band — same objects, two vantage points.
- **Corridor** — a strip viewed as a *shared solve domain across passes*: the
  `(view, side)`-keyed batch in `PlacementContext.corridor_batch`. The name
  entered with the first cross-pass unification (#345/#346, 0009 Amendment 6)
  and stuck for the register/drain machinery. Caution: `corridor_blockers`
  uses the same word for a different thing — the 2-D *witness corridor* a
  right/below dim occupies between the view edge and its dim line, which the
  1-D strip carve cannot represent and which is checked separately.

Two candidate types, layered — the outer wraps the inner:

- **`StripCandidate`** (`layout.py`) — the geometry-only solver input: `key`,
  `anchor`, `size`, `priority`, `anchored`. It *is* a measured render-intent:
  the collect step (in `annotations/`, which may depend on the IR) projects
  intent → page geometry and hands the solver only that, so `layout.py` stays
  a leaf with no IR dependency. A few passes build these directly (the
  concentric-bore leader stack in `from_model.py`).
- **`CorridorCandidate`** (`annotations/_common.py`) — the rich render-intent
  carrier for the cross-pass corridors: a `build(pos) → Dimension` closure,
  the ladder `order` key, `dedup`/`precedence`, `priority`,
  `anchored`/`natural` (how ADR 0012 pinned edits join), `force` (policy B),
  `feature` provenance, real `size` footprint (wide occupants — a ~24×6 mm
  GD&T frame — reserve their true extent, #61), the `forbid` box, the
  analytical `footprint`, and `on_place`/`on_drop` callbacks carrying each
  pass's own bookkeeping.

The boundary: `solve_corridor` → `place_strip_candidates` constructs
per-segment `StripCandidate`s → `plan_strip` → `_solve_strip_1d_pava`. Above
that line the code knows the drawing, closures, and lint; from `plan_strip`
down it knows only numbers.

## Dropped from 0009, deliberately

- **The solver-library evaluation** (0009 Amendment 3's 10-row table): the
  history in one line — the original Cassowary/`kiwisolver`
  constraint-satisfaction solve and its `scipy.optimize.linprog` replacement
  were *both* retired (non-unique L1 optima broke cross-platform determinism
  on day one) for the dependency-free weighted-median PAVA that shipped
  (`_solve_strip_1d_pava`). Neither `kiwisolver` nor `scipy` is a dependency.
  0009 Amendments 3–4 hold the full evaluation and post-mortem.
- **The banded-PAVA DP** (0009 Amendment 5): retired by Amendment 9 (#381).
  `plan_strip` has no keep-out-band parameter; a reserved row (centre-line,
  location-dim extension) is an ordinary interval in the caller's
  `carve_free_segments` carve, like any other obstacle.
- **Rotted line references.** 0009 cited `holes.py` line numbers that no
  longer resolve. This ADR cites files and symbol/test names only.
- **The amendment trail.** The nine amendments and the migration phase issues
  (#317–#323, #351, #318, #345/#346, #61, #524, #381, #636) stay in 0009 as
  the why-trail; this ADR does not restate them.

## Relationships

- **ADR 0001 / 0002** — determinism is why the solve is an exact, explainable
  algorithm ("label *i* sits here because order + min-gap + shortest leader"),
  never a metaheuristic; repair stays a peephole safety net (it no longer
  touches `annotation_overlap`).
- **ADR 0003** (still Accepted) — the constraint-based inner-layout frame this
  ADR realises for the strips. The carrier is `StripCandidate`/`plan_strip`,
  not 0003's original `Placeable`/`LayoutSolver` (deleted unused, #547 — see
  0003's 2026-07-10 correction); the deferred global 2-D solve (#94) remains
  deferred.
- **ADR 0004** — the **outer** layer: compose-then-pack keeps view blocks
  disjoint; this ADR is the **inner** per-view layer whose deterministic
  annotation boxes are exactly the block footprints 0004 packs.
- **ADR 0008 / [ADR 0015](0015-part-drawing-compiler-as-built.md)** — the
  planner's render-intents are what the collect phase measures into
  candidates; 0015 restates that compiler shape (superseding 0008 in the same
  #697 sweep).
- **ADR 0010** — provenance is recorded once at the drain seam
  (`CorridorCandidate.feature`), not tagged through every pass.
- **ADR 0012** — the user-edit layer **on this same solve**: `dimension(...,
  pin=, priority=)` intents become corridor candidates (`anchored=pin`), re-run
  by `Drawing.finalize()` through the same `_PASS_SEQUENCE`/drain. Not
  restated here — 0012 is current.
- Roadmap: [`plans/strip-layout-boundary-labeling-roadmap.md`](../plans/strip-layout-boundary-labeling-roadmap.md);
  research note as above. Guard tests:
  `tests/test_carve_free_position_callers.py`,
  `tests/test_layout_cleanliness.py`.

## Consequences (standing, not aspirational)

- The invisible-occupant collision class is removed by construction; the
  fail-closed guard keeps it removed.
- Deterministic, explainable, dependency-free placement; over-capacity is a
  priority-ranked selection with first-class escalation, not an arrival-order
  drop.
- The explanation is **recordable** (#736, from the #733 post-mortem): the
  opt-in solve trace — `build_drawing(trace=…)` or `DRAFTWRIGHT_TRACE=<path>` —
  dumps one JSON file per build with every strip solve's candidates, carving
  obstacles (named), free segments, and per-candidate outcomes
  (`SolveTrace`, threaded as `PlacementContext.trace`; default off, nil cost),
  and a `placement_unsatisfiable` strip-full drop names the occupants that
  filled the strip. Diagnosing a drop is a `jq` query, not a custom-script
  rebuild.
- Honest edges that remain: placement is per-view (cross-view contention rests
  on ADR 0004); AABB occupancy is deliberately conservative for diagonal
  leaders (the precise `_geometry._segment_crosses_box` test covers leader
  shafts); and a perpendicular-axis conflict — a witness line crossing a
  fixed-height label — is outside the 1-D tier solve's reach, handled by the
  post-drain `reconcile_witness_labels` shift (#690).
