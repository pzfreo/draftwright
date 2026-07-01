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
| P4 | #318 | **Optimal leader assignment + angled leaders.** Replace greedy ordering/spacing with the min-cost-matching / DP optimal assignment (minimise total leader length); fold the #305 angled-leader nudge into the model as a first-class leader style. | Leader positions may improve | P3 |
| P5 | #319 | **Remove dead patches; strengthen the cleanliness invariants.** Delete superseded occupancy patches, tier-retry loops, and ad-hoc drop codes; extend the layout-cleanliness / property tests (#301/#302/#303) to assert the new guarantees: no invisible-occupant overlap, crossing-free leaders, deterministic output. | None (cleanup + tests) | P4 |

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

Updated 2026-07-01.

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
- **P3 (#323)** — **in progress.** Envelope dims decoupled from the cursor (#334). The
  shared collect-then-solve placer extracted to `_common.place_strip_candidates` (#337),
  which now reserves the outermost label's `tier` at the strip boundary (#338 review
  fix). `render_locations` (plan-view X/Y locations) migrated (#338). **Remaining
  `Strip.allocate` production paths, one renderer at a time:** the front-view
  height/step ladder (`render_height_ladder` — note its leapfrog witness cursor), the
  turned step-length chain, PMI/GD&T (`render_pmi`), milled slots (`render_slots`), and
  `drawing.py`. When the last is migrated, delete `Strip.allocate` and close #150.
- **P4 (#318)** — **not started, and intentionally deferred until P3 completes** (user,
  2026-07-01): optimising leader assignment while some placers still use the cursor
  would optimise around mixed placement semantics.
- **P5 (#319)** — **not started.** Delete dead patches + the cursor; promote the
  escalation signal to first-class objects (see P2 refinement); **burn down the
  `_KNOWN_OVERLAPS` allowlist** (relocate/rescale the remaining real crossings — e.g.
  `side_drilled` `{hc_side0, dim_loc_side_z2300}`, an outer-layout concern) then convert
  the cleanliness ratchet from an allowlist into an **absolute** no-overlap invariant.

Overlap allowlist classes (`tests/test_layout_cleanliness.py`): **BENIGN** (permanent
datum-chain witness corridors), **SPACE-CONSTRAINED** (real crossing, no roomy
alternate — needs outer-layout rescale), **PENDING** (a placer defect a named phase
removes). P5's burn-down empties SPACE-CONSTRAINED + PENDING.
