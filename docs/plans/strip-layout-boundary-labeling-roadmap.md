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
| P1 | #321 | **The contended strip: bore callouts + off-axis location dims as one solve.** Both placers that share the side/below strip contribute candidates to a single solve; delete the post-hoc occupancy-check + tier-retry hack. Validates the model on #133/#225. | Resolves known collisions only | P0 |
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

Not started — ADR 0009 accepted 2026-06-30; phases filed (#317, #321, #322, #323,
#318, #319 under tracking #320).
