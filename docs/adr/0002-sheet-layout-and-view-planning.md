# ADR 2 — Sheet layout and view planning

- **Status:** Accepted (2026-09-04). Consolidates archived 0018 (the spine), 0004 and 0014, and
  the placement half of 0012. 0018's unbuilt parts are carried under **Open**, not dropped.
- **Deciders:** Paul Fremantle (pzfreo)

## Decision

Which views exist is a decision the engine owns, and it is driven by requirements: a view earns
its place by carrying measurements no other selected view can. Page, preferred standard scale,
view set and arrangement are one constrained choice, evaluated against complete view blocks
measured with fixed paper-space typography. A candidate is feasible only if the real placement
solve preserves every supported requirement with every block in bounds; paper economy never
outranks requirement survival, legibility or projection convention. Infeasibility is a
first-class result, never a silent relaxation of an authored constraint.

Each selected view is composed with its annotation footprint before the page is packed
(compose-then-pack). Footprints are page-mm boxes, so the scale/page search is rectangle
arithmetic; OCC geometry is built once, at the resolved position, and a bounded measured repack
corrects the seed estimate.

Within a view, annotations are never placed as they are rendered. Every pass collects its
candidates; one deterministic solve per strip selects, orders and spaces them; then they are
emitted (collect-then-solve). Over capacity is a ranked selection, not an arrival-order drop.
When avoiding an occupant would cost more than a bounded relocation, the annotation stays and the
crossing is logged (Policy B) — never an unbounded search, never a real annotation dropped for
tidiness. Feature leaders join one late joint assignment; a shaft through the part body is a
priced cost measured on the same lowering the critique uses.

Users edit whole view blocks — presence, relations, a pin on a projection origin — never
feature-annotation coordinates. Authored views with automatic dimensions is refused, because the
dependency runs one way: requirements determine views.

## Invariants

1. **Footprints are boxes; the search is box math; the repack is bounded.** No OCC bbox is
   measured inside the `(scale, page)` search; `_REPACK_MAX_ITER` bounds the corrective loop.
   `test_layout.py`, `test_repack_geometry_seam.py`, `test_issue_1216_callout_reservation_measures_ink.py`.
2. **An explicit scale may cramp geometry, never silently lose a requirement.** Default policy
   retries smaller preferred scales; strict refuses; permissive warns; the decision is recorded
   with stable blocker identities. `test_issue_1146_scale_completeness.py`.
3. **Scale before sheet.** A bounded larger-scale trial on the selected page runs before the
   optional isometric is removed and before a larger sheet is tried; every attempt is recorded.
   `test_issue_1338_scale_before_page_escalation.py`, `test_issue_1299_page_escalation.py`,
   `test_issue_1155_grm04_sheet_use.py`.
4. **Every non-exempt strip occupant is a solve candidate.** `carve_free_position` callers are a
   fail-closed allowlist; a new exemption is recorded here first.
   `test_carve_free_position_callers.py`.
5. **The strip solve is exact and dependency-free.** Weighted-median PAVA with a fixed tie
   convention; crossing-free by construction for distinct anchors; ranked drop over capacity.
   `test_strip_layout.py`, `test_layout_property.py`, `test_layout_hypothesis.py`.
6. **One stage order, two entry paths.** `_PASS_SEQUENCE` is the only stage tuple; `_auto_annotate`
   and `finalize()` both run it through `run_stages`. `test_issue_563_placement_intent.py`.
7. **Feature leaders assign jointly, late, and observably.** Lexicographic objective (placed jobs,
   priority, fixed-obstacle penalty, length, order); budgets count measured work and a fired budget
   is a named capability loss; the greedy result is the floor under every cap.
   `test_issue_1166_cross_pass_feature_leaders.py`, `test_issue_740_leader_assignment.py`,
   `test_issue_1188_per_view_assignment.py`, `test_issue_1308_machined_leader_analytics.py`.
8. **Material re-entry is one predicate for router and critique**, a cost never a gate; the
   material field is built once per build under explicit tessellation control.
   `test_issue_798_material_field.py`, `test_issue_798_silhouette_lint.py`,
   `test_issue_798_floor_cardinality.py`, `test_issue_1187_unroutable_leaders.py`.
9. **Policy B is inventoried.** Known overlaps are `BENIGN`/`SPACE-CONSTRAINED`/`PENDING`; only
   `PENDING` is debt; a retained crossing persists a finding with provenance.
   `test_layout_cleanliness.py`.
10. **Same-batch dimension ink is visible before commit**, resolved by a bounded strictly-improving
    local solve inside Emit — not a second placement engine. `test_issue_1334_prevent_ink.py`.
11. **One view vocabulary; request and result are distinct states.** `ViewSpec` values;
    `ViewConstraints` is the authored request; `ResolvedViewPlan` is immutable, attached once to
    `BuildState`, and read-only on `Drawing`. `test_view_plan.py`, `test_issue_1260_view_constraints.py`.
12. **Authored constraints are never relaxed, and an unshowable dimension fails before projection.**
    Authored views with `auto_dimensions()` raises at the verb; an approved dimension no authored view
    can carry raises `ViewPlanIncomplete` naming the view to add; `view_decision` is always present.
    `test_issue_1260_view_constraints.py`, `test_adr0018_view_routing.py`.
13. **View removal is requirement-aware.** The optional isometric yields only to close missing axial
    coverage, on the settled page and arrangement, with every gate recorded; no principal view is
    removed automatically. `test_adr0018_view_selection.py`, `test_issue_1262_automatic_turned_views.py`,
    `test_issue_443_grm03_axial_coverage.py`, `test_issue_1190_section_decision.py`.
14. **Exact detail/iso factors; unequal principal scales refused.** `test_issue_1204_multiscale_view_issues.py`.
15. **The solve is recordable.** `build_drawing(trace=…)` dumps every corridor solve and pass event;
    a strip-full drop names its occupants. `test_solve_trace.py`.

**Unguarded.** That the optimiser never shrinks text to fit is a stated rule of 0018 with no
dedicated mutation guard. Automatic principal-view selection has no guard because it is not built.

## Boundaries

- **ADR 1 (pipeline).** Owns the planner that produces render intents and the module homes of
  `compose.py`, `layout.py`, `annotations/`. This ADR owns what those modules may decide.
- **ADR 3 (recognition).** Frame choice happens above every layout stage; layout consumes one
  working solid and never a second coordinate system.
- **ADR 4 (declared intent).** Owns the verbs. Intent enters the solve as candidates and never
  chooses a coordinate; the authored-views ban is stated there as intent and here as planning.
- **ADR 5 (trust).** Owns that a drop is reported against its measurement and that lint is
  independent. This ADR owns why the drop happened and records it.

## Superseded

- 0004 — compose-then-pack; box footprints over measured geometry; monotone `(scale, page)` search;
  byte-identity dropped as an acceptance test; explicit scale cannot lose requirements (#1146);
  bounded semantic correction after a recovery detail (#1155); scale before sheet (#1338). Its
  fixed four-view topology is superseded by 0018.
- 0014 — collect-then-solve as built; Cassowary and `scipy.optimize.linprog` both retired for
  non-unique optima; banded DP retired; Amendments 1–6 (joint post-drain assignment, one late
  inventory, material re-entry, measured budgets, analytical tier, same-batch ink).
- 0012 (placement half) — pin and priority realised on the existing `StripCandidate`; below/right
  ladders folded into the corridor.
- 0018 Amendments 1–3 — the verb structure mirrors dimensions; the ban; typed `ViewConstraints`;
  the isometric may yield to axial coverage.

## Open — 0018's delivery gate, retained whole

Automatic semantic view selection lands only once each of these holds; accepting the direction
did not waive them.

- Automatic selection among principal views (the case study reaches A2 and costs six annotations,
  so a gate weighing it refuses); `_views` remains an engine seam, not a public option.
- Resolved-plan script emission with its two distinct promises (automatic vs editable resolved),
  failing with a named capability where a section target cannot be named.
- Removing a visually similar but semantically necessary view is rejected by an asymmetric
  counterexample; a truly redundant view's removal retains every requirement.
- First/third-angle counterexamples preserve principal relationships; a contradicting relational
  constraint is infeasible, not repacked.
- Whole-block pins anchor the projection origin and survive label changes.
- Deterministic largest-appropriate-scale → smallest-feasible-sheet ordering on a fixture where
  each alternative is feasible; the synthetic thin-plate case selects a materially better plan.
- Multi-sheet output is out of scope until a document model exists.
- Bounding what one dropped annotation may buy in scale and sheet (#1336); whether an unrouted
  leader or a too-short rung should escalate to a detail (#1236); candidate richness versus solve
  reach (#1188).
