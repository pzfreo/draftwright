# Strip-layout boundary-labeling roadmap

Execution roadmap for [ADR 0009](../adr/0009-boundary-labeling-strip-placement.md)
(collect-then-solve per-strip annotation placement). Research backing:
[`research/annotation-placement-boundary-labeling.md`](../research/annotation-placement-boundary-labeling.md).
Tracking issue: **#320**. Each phase below is a GitHub issue; each phase is one PR
(split if it grows).

## Why

A view's annotations are placed into **strips** by several imperative passes that
share a strip but not an occupancy model, so they overprint each other in ways no
single placer can see (#133, #225, #305). ADR 0009 inverts the control flow:
every strip occupant is **collected** as a candidate, **solved** as one
boundary-labeling instance per strip (select → assign → order = feature order ⇒
crossing-free → space), then **emitted**. This removes the invisible-occupant
collision class by construction while keeping determinism (ADR 0001).

## Principles for every phase

- **Behaviour-preserving until P2.** P0/P1 route existing placers through the new
  stage without changing *what* is shown or *where* (beyond fixing the known
  collisions). Output may shift only where a collision is genuinely resolved.
- **Determinism is non-negotiable** — stable candidate ordering; reproducible
  solves; no stochastic placement (that is Approach B, rejected).
- **One contended strip first** (P1) validates the model on the exact recurring
  bug before the broad sweep (P3).
- **Drops become escalation.** "Doesn't fit" is a priority-ranked signal, not an
  arrival-order omission; genuine drops still surface as lint.

## Phases

| Phase | Issue | What | Behaviour change | Depends |
|---|---|---|---|---|
| P0 | #317 | **Collect/solve/emit seam + complete per-strip occupancy.** A `StripLayout` stage that takes a batch of candidate `Placeable`s for a view's strips and a unified occupancy that includes *every* occupant — callout/dim labels **plus** extension lines, leader shafts, and the hatch footprint (closing the `_occupied_boxes` blind spots). Route one placer (bore callouts) through it as proof. | None (scaffolding) | — |
| P1 | #321 | **The contended strip: route both shared-strip placers through `plan_strip`.** Bore callouts and off-axis location dims both become `StripCandidate`s solved by `plan_strip`; delete the post-hoc occupancy-check + tier-retry hack. *Correction (2026-07-01, from implementation):* the two placers stack on **orthogonal axes** (callouts spaced along Y at a fixed X-column; height dims stacked along X-tiers) — so they are two perpendicular 1-D `plan_strip` solves sharing **one occupancy model** (option (c): pre-shrink/split each strip's `[lo,hi]` around `strip_obstacles`), **not** one fused solve. True fusion (collect both placers before either emits, retiring the `_coaxial_lift` forward-reference) is P2/P3. Split into **P1a** (route bore-callout Pass-2 through `plan_strip` — byte-identical, deletes `_solve_strip_via_layout`/greedy prefix-drop) and **P1b** (route `_locate_off_axis_holes`, build the occupancy carve, delete the tier-retry hack **and** `_coaxial_lift`). Validates the model on #133/#225. | Resolves known collisions only | P0 |
| P2 | #322 | **Feature-ordered assignment + priority selection + escalation.** The solve fixes order = feature order (crossing-free) and turns over-capacity into a priority-ranked selection that emits an escalation signal (→ detail view #306/#54, → table), replacing the scattered `*_dropped` arrival-order decisions. | Drop *policy* changes (ranked, not arrival-order) | P1 |
| P3 | #323 | **Migrate the remaining strip placers; retire the `Strip` cursor (#150).** Envelope dims, step-height / turned-diameter ladders, and the section-hatch footprint all become candidates; standalone `Strip.allocate` usages are retired. Fully realises #150. | Dense sheets re-pack; covered by invariants | P2 |
| P4 | #318 | **Optimal leader assignment + angled leaders.** Replace greedy ordering/spacing with the min-cost-matching / DP optimal assignment (minimise total leader length); fold the #305 angled-leader nudge into the model as a first-class leader style. *Scheduling correction (user, 2026-07-01, reconfirmed 2026-07-02):* run **after P5**, not before — optimising leaders before the placement model is fully settled would optimise around a moving target. Split into **P4a** (per-candidate label-size gaps into `plan_strip`), **P4b** (min-total-leader-length isotonic/DP solve, replacing the pull-toward-natural objective), **P4c** (fold the #305 angled-leader nudge in as a first-class constraint). | Leader positions may improve | P3, and in practice P5 |
| P5 | #319 | **Remove dead patches; strengthen the cleanliness invariants.** Delete superseded occupancy patches, tier-retry loops, and ad-hoc drop codes; extend the layout-cleanliness / property tests (#301/#302/#303) to assert the new guarantees: no invisible-occupant overlap, crossing-free leaders, deterministic output. Four strands (see Status): cursor deletion (done), escalation objects (done, #351), `_KNOWN_OVERLAPS` burn-down (not started), property/fuzz tests (not started). | None (cleanup + tests) | P3 |

## Acceptance (overall)

- The #133/#225/#305 fixtures place clean with **no** post-hoc occupancy patches
  in the placement path.
- A full strip has its lowest-priority members **escalated** (detail view / table),
  not silently dropped by code order; remaining drops are lint.
- Leaders within a view are crossing-free and near-minimum length.
- Output is deterministic and reproducible; the property/fuzz layout-cleanliness
  tests (#301) pass and are extended to the new guarantees.
- `Strip.allocate` cursor usages are gone (#150 closed).

## Status

Updated 2026-07-02.

- **P0 (#317)** — done: characterization gate (#326), `strip_obstacles` complete
  per-strip occupancy (#327), `StripCandidate` + `plan_strip` + `StripPlacement`
  collect→solve→emit seam (#328). Cleanliness invariants (determinism + overlap
  ratchet) pulled forward (#333).
- **P1 (#321)** — **done.** #329/#331 landed the occupancy model; **P1a** routed the
  bore-callout Pass-2 through `plan_strip`; **P1b (#335)** routed `_locate_off_axis_holes`
  onto the carve + added **corridor-aware relocate-or-keep** (policy B — never drop a
  real dim; a leader in a right/below dim's witness corridor routes it to the other
  view, else keeps it). Deleted the tier-retry `_box_hits` hack.
- **P2 (#322)** — **done.** Core priority selection landed earlier (#330); **D3 (#336)**
  routed the real per-feature priority (bore diameter — largest wins) into the
  selection. The **escalation ladder was already in place** from prior work (#92/#93/#307):
  a ranked drop records `callout_dropped`/`location_ref_dropped`, `_maybe_tabulate_holes`
  escalates to the hole table + balloons; detail views stay step-legibility-only; holes
  and steps escalate on disjoint feature classes (no double-escalation). *Refinement
  (user, 2026-07-01):* make dropped keys **first-class escalation objects**, not just
  `*_dropped` build-issue codes — a structured signal the table/detail escalators
  consume. Tracked into P5.
- **P3 (#323)** — **DONE.** Every placer migrated to the shared carve, one renderer at a
  time: envelope (#334), P1b locations (#335), the shared placer + tier reservation
  (#337/#340), `render_locations` (#338), `render_slots` + perpendicular-band filter
  (#341), the height/step ladder with its leapfrog preserved via a position-returning
  carve (#342), `render_pmi` (#343), and the public `place_dim` (#344). Shared primitives
  in `annotations/_common.py`: `place_strip_candidates`, `carve_free_position`,
  `strip_free_span`/`carve_free_segments`/`corridor_blockers`/`strip_obstacles`.
- **P5 (#319)** — **in progress.**
  - **Strand 1 — DONE (#349, closes #150):** deleted the `Strip.allocate`/`peek`/`_cursor`
    machinery + the dead orchestrator overflow check; migrated the last state consumer
    (the balloon-ring depth) from `depth_used` to real placed-geometry measurement.
  - **Strand 2 — escalation objects — DONE (2026-07-02, tracker #351, epic closed out
    across 6 PRs):** `*_dropped` string codes → first-class `Escalation` objects + one
    resolver that picks a remedy per group. `#354` scaffolding, `#355` routes the hole
    path (byte-identical), `#356` adds ISO pattern-grouped balloons (fixes #348),
    `#361`/`#363`/`#364` (the PR-4 split) route slot/PMI drops, unify the illegible-step
    → detail-view remedy, and delete the last annotation-name-prefix string-grep. Zone
    grid-ref (#352) stays deferred, a separate later option, not a #351 blocker. Four
    real bugs found and filed as side-discoveries during the work, not fixed in-epic:
    #357 (`StripCandidate.priority` always 0), #358 (two turned-diameter placers still
    on pre-carve occupancy idioms), #360 (PMI bore-diameter witness span 2× too wide —
    `rec.kind`/`rec.pmi_kind` mixup), #362 (Z-turned crowded shoulders silently drop the
    whole step-length chain, no lint at all).
  - **Strand 3 — burn down the `_KNOWN_OVERLAPS` allowlist** (SPACE-CONSTRAINED + PENDING —
    e.g. `side_drilled` `{hc_side0, dim_loc_side_z2300}`, an outer-layout concern) then flip
    the cleanliness ratchet from an allowlist into an **absolute** no-overlap invariant.
    **Not started.** Plan: audit each allowlisted entry against the now-complete P0–P3 +
    P5-strand-2 carve/escalation machinery — some may already be resolved incidentally;
    remove what's fixed, then flip the ratchet. One PR.
  - **Strand 4 — strengthen the property/fuzz cleanliness tests** (#301/#302/#303).
    **Not started.** Plan: generative/property-based coverage (varied hole counts,
    patterns, slot/step mixes) asserting no invisible-occupant overlap, crossing-free
    leaders, and determinism — beyond today's fixed fixture set. One PR, after strand 3
    so the absolute ratchet is what gets property-tested, not the allowlisted one.
- **P4 (#318)** — **not started, and intentionally deferred until P5** (user, 2026-07-01):
  optimising leader assignment before the placement model is fully settled would optimise
  around a moving target. *Sequencing reconfirmed 2026-07-02:* still holds — P5 strands
  3–4 (the allowlist burn-down + property tests) are the "settle the model" work P4 was
  waiting on, so they land **before** P4 starts, not after. Once P5 is fully done, P4
  splits into three gated PRs (mirroring the P1a/b and PR-4a/b/c pattern):
  - **P4a** — wire per-candidate label-size gaps into `plan_strip` (use the already-tested
    `_solve_strip_1d_var`/`_greedy_strip_1d_var` per-pair-gap primitives instead of one
    caller-supplied uniform `min_gap` per strip). Low risk, no new algorithm.
  - **P4b** — replace `plan_strip`'s "pull toward natural position" Cassowary spacing
    objective with a proper min-total-leader-length solve (isotonic regression / DP) —
    order stays fixed by anchor position (crossing-free, already established by P2), this
    only changes *where within that order* each label sits. The algorithmic core.
  - **P4c** — fold the #305 angled-leader nudge (today a bespoke post-hoc special case)
    into the P4b solve as a first-class ≥30°-from-horizontal constraint/penalty; retire
    the special case.

Overlap allowlist classes (`tests/test_layout_cleanliness.py`): **BENIGN** (permanent
datum-chain witness corridors), **SPACE-CONSTRAINED** (real crossing, no roomy
alternate — needs outer-layout rescale), **PENDING** (a placer defect a named phase
removes). P5's burn-down empties SPACE-CONSTRAINED + PENDING.
