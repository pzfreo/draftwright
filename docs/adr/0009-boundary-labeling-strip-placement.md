# ADR 0009 — Boundary labeling: collect-then-solve per-strip annotation placement

- **Status:** Accepted (2026-06-30)
- **Deciders:** Paul Fremantle (pzfreo)

## Context

A run of layout fixes — #133 (locate side-drilled holes), #225 (locate *every*
side-drilled hole), #293 (dense step-length chains), #305 (coaxial bore callout
crossed by its location-dim line), and the `callout_dropped` /
`location_ref_dropped` / `off_axis_location_dropped` family — keep landing in the
same place and keep being patches, not cures. They share one root cause.

draftwright places a view's annotations into **strips** (`ViewZones`:
left/right/above/below bands around each orthographic view; `_core.py`). Inside
those strips, placement is **imperative, single-pass, and split across mechanisms
that do not share an occupancy model**:

- a **cursor** (`Strip.allocate`) used by envelope dims, step/height ladders, and
  off-axis location dims; and
- a **1-D Cassowary solve** (`LayoutSolver.solve_strip`, kiwisolver; ADR 0003)
  used by bore-callout and turned-diameter leaders.

Several passes write into the **same** strip, each blind to the others. The code
admits it (`annotations/holes.py`): *"the right/below strips are SHARED with hole
callouts and the section hatch, which use other placers and are invisible to the
cursor (#133). So a clean allocation is necessary but not sufficient…"* The
workarounds are post-hoc occupancy checks and tier-retry loops; and when a strip
is full, **which annotation gets dropped is decided by arrival order in the code,
not by priority.**

This is, precisely, the academic problem of **boundary labeling** (Bekos,
Kaufmann, Symvonis & Wolff, 2007): point features inside a view rectangle, labels
on the surrounding strips, joined by leaders; minimise short, **crossing-free**
leaders. The research backing — current-engine critique, the literature, and the
full pros/cons of the two candidate approaches — is in
[`research/annotation-placement-boundary-labeling.md`](../research/annotation-placement-boundary-labeling.md).

ADR 0003 already framed the right *shape* (assignment → placement, an escalation
ladder) but left the assignment layer and escalation under-specified and deferred
the global solve. The strip passes were never actually unified; they still
place-as-they-go. This ADR settles **which concrete model finishes ADR 0003 for
the strips, and which to reject.**

## Decision

Adopt **boundary labeling with a collect-then-solve per-strip stage** (the
research note's *Approach A*). Reject the global metaheuristic/MIP optimiser
(*Approach B*) as the primary direction — see *Alternatives*.

The load-bearing change is a **control-flow inversion**. Today each pass commits
real geometry (`dwg.add(...)`) as it runs. Instead, for each view, run three
phases:

1. **Collect.** The stage consumes the planner's **render-intents** for this view
   (ADR 0008) and *measures* each into a placement **candidate** — a geometry-only
   `StripCandidate` carrying the site anchor, label-box size, and priority (and an
   eligible-sides field once the multi-side *assign* step lands, P2). The candidate
   **is a measured render-intent**: the collect
   step (in `annotations/`, which may depend on the IR) projects intent → page
   geometry, and hands the solver only that geometry, so the solver stays a leaf
   with no dependency on the IR. Fixed obstacles that are *not* placed — the
   section-hatch footprint, the title block (`strip_obstacles`, P0b) — enter the
   solve as things to avoid, not as candidates. **Nothing is placed yet**; this
   consumes the **full** per-strip set at once.
2. **Solve.** One optimisation over that set:
   - **Select** — if the strip cannot hold everything, keep the highest-priority
     set that fits; the rest produce a first-class **escalation** signal.
   - **Assign** — side/zone (the discrete, disjunctive choice).
   - **Order** — label order along the strip = **feature order**. This is the key
     move: with order fixed, leaders are **crossing-free by construction** and
     non-overlap collapses to a chain of *linear* inequalities — exactly what
     kiwisolver can solve, which is why the disjunction problem disappears.
   - **Space** — final positions via the existing 1-D Cassowary strip solve.
3. **Emit.** Materialise the chosen geometry from the solve's decisions.

Objective: **minimise total leader length (+ kept priority) subject to
non-overlap and bounds.** Order-fixing kills crossings for free; selection
handles over-capacity; the optimum is a sweep / min-cost matching / dynamic
program (O(n log n)–O(n³)) finished by the 1-D solver already in `layout.py`.

**Why this and not a global solve.** It removes the actual defect class — the
invisible-occupant collision — *by construction*, while preserving the two
properties draftwright cannot give up: **determinism** (ADR 0001) and
**explainability** ("label *i* sits here because order + min-gap +
shortest-leader," not "the annealer landed there"). It is consolidation of parts
that already exist (`Placeable`, `LayoutSolver`, the strips, the drop plumbing),
not a rewrite, and it is the disciplined version of the already-funded #150.

### Relationship to the existing layout ADRs

- **ADR 0003 (constraint-based layout).** This ADR *finishes* 0003 for the
  strips: it makes the **assignment layer** concrete (collect-then-solve, with
  feature-ordered side/zone assignment) and turns the **escalation ladder** from
  prose into the selection step's output. 0003's "global 2-D Cassowary solve"
  stays deferred (#94); this is the per-view inner layer 0003 always implied.
  `Placeable` / `LayoutSolver` are reused unchanged in spirit.
- **ADR 0004 (compose-then-pack).** Orthogonal and complementary. 0004 is the
  **outer** layer (each view is a block; pack blocks disjoint so cross-view
  overlap cannot occur). 0009 is the **inner** layer (place one view's
  annotations into its own strips, optimally). The block footprint 0004 needs is
  exactly the bounding box this stage now produces deterministically.
- **ADR 0008 (the dimensioning planner).** 0008 already separates *what to
  dimension* (planner → render-intent) from *render*. 0009 slots onto that seam:
  the layout stage consumes the **full per-strip intent set** before committing,
  rather than each render pass committing on its own. The planner's intents are
  the natural source of the collect-phase candidates.
- **ADR 0001 / 0002.** Determinism is the reason A was chosen over B. Repair
  (0002) stays a safety net; principled selection/escalation replaces the bulk of
  the ad-hoc `*_dropped` decisions, so repair has less to clean up.

## Consequences

**Positive**
- The invisible-occupant collision class (#133/#225/#305 and kin) is removed by
  construction: one occupancy model per strip, every occupant in it.
- Crossing-free, minimum-leader-length placement within a view; provably optimal
  and fast (O(n log n)–O(n³)).
- Deterministic and explainable; no stochastic placement, no golden-test drift
  risk.
- "Doesn't fit" becomes a **priority-ranked escalation** (→ detail view #306/#54,
  → table) instead of an arrival-order drop. Drops that remain are still lint.
- A new strip annotation type joins the boundary model instead of inventing
  placement from scratch.

**Negative / costs**
- **Control-flow inversion** is real work: passes must return candidates to a
  single layout stage rather than calling `dwg.add(...)` mid-flight. Risk of a
  half-migrated engine running both models at once during the migration —
  mitigated by phasing one contended strip first and keeping behaviour-preserving
  steps.
- **Per-view, not global:** cross-view and inner-vs-outer-zone contention still
  rest on ADR 0004 + the assignment heuristic, not a single global optimum.
- **Modelling friction:** dim *chains*, ladders, and hatching must be coerced
  into the label/leader/obstacle abstraction.
- **Leader style:** optimality results are richest for rectilinear leaders;
  angled leaders (the #305 case) are first-class but weaken the guarantees where
  mixed.

## Alternatives considered

- **Approach B — one global optimisation (MIP or simulated annealing).** Highest
  quality ceiling and eliminates the root cause at the largest scope (across
  placers *and* views), but it conflicts with **determinism** (SA is stochastic;
  MIP solver-sensitive — and golden tests were retired in ADR 0007, so drift is
  less guarded), is far heavier (NP-hard) for sheets this sparse, and is
  hard to debug/lint/repair. **Held in reserve:** revisit only if, after A,
  genuine cross-view / inner-vs-outer-zone conflicts survive compose-then-pack —
  and then prefer the seeded-annealing variant with a re-introduced
  output-stability test. (Research note §4–5.)
- **Status quo + more patches.** Each new fixture finds a new invisible-occupant
  collision; the cost compounds and the drop policy stays arrival-order. Rejected.

## Migration

Phased, behaviour-preserving where possible, contended-strip-first so the model
is validated on the exact recurring bug before the broad sweep. Tracking issue
**#320**; the execution plan and per-phase issues (P0–P5: #317, #321, #322, #323,
#318, #319) live in
[`plans/strip-layout-boundary-labeling-roadmap.md`](../plans/strip-layout-boundary-labeling-roadmap.md).

## Amendment 1 — Escalation objects (P5-strand-2, 2026-07-01)

**Status:** Accepted; **fully landed (2026-07-02)**. Tracker **#351**, closed —
6 PRs (#354, #355, #356, #361, #363, #364), full history and side-discoveries
in [`plans/strip-layout-boundary-labeling-roadmap.md`](../plans/strip-layout-boundary-labeling-roadmap.md#status).

**Problem.** Today "this couldn't be placed" is a stringly-typed side effect: seven
scattered codes (`callout_dropped`, `location_ref_dropped`, `off_axis_location_dropped`,
`slot_dim_dropped`, `step_dim_dropped`, `placement_unsatisfiable`, `table_dropped`) are
emitted via `_record_build_issue(...)`, and the escalators *pattern-match the strings* to
act — `_maybe_tabulate_holes` greps for two of them and always applies **one** remedy
(hole table + **one balloon per hole**), while step crowding routes through a separate
channel (`_detail_requests`/`_resolve_details`). So the escalation *policy* is entangled
with lint text, and the remedy is one-size — the root cause of the CTC-02 balloon ring
that cannot fit (#348).

**Decision.** Make "couldn't place" a **first-class object** emitted at the failure point,
and separate *collecting* failures from *deciding* remedies.

```
Escalation(kind, view, feature, reason, remedies)
  kind:     "callout" | "location" | "slot" | "step" | "pmi"
  feature:  IR feature/key ref — carries PATTERN membership
  reason:   "strip_full" | "illegible" | "corridor_blocked" | "no_room"
  remedies: ranked candidates the resolver may pick, e.g. ("group_balloon","table","detail","drop")
```

- **Placers collect, don't decide:** append an `Escalation` to `dwg._escalations` instead
  of recording a `*_dropped` string. The strip carve already knows *why* it failed, so
  `reason` is free.
- **One resolver pass** (subsuming `_maybe_tabulate_holes` + detail resolution): group by
  `(view, feature-or-pattern)`; pick a remedy per group by a fixed policy ladder (the D4
  order); execute it; and emit the **same `*_dropped` codes only for what stays
  unresolved** — so coverage lint + the cleanliness ratchet keep working unchanged (the
  strings remain the lint *surface*; the *decision* is now object-driven).

**Ratified sub-decisions (user, 2026-07-01):**
1. **Pattern grouping = ISO** — a recognised bolt-circle / linear / grid escalates to
   **one balloon tagging the pattern once** (with an `n×` count) near the feature, not one
   balloon per member. CTC-02 is largely patterned, so its ring collapses from dozens to a
   handful — this is the #348 fix.
2. **Scattered-hole fallback = table + balloons** (today's behaviour) for now; a **zone
   grid reference** (A1/B2) is a separate, later option — filed as its own issue (**#352**).
3. **PR-1 = scaffolding only** (the `Escalation` type + `dwg._escalations` collector, no
   routing) so the first diff is byte-identical; routing + the ISO grouping follow.

**Migration (each its own gated PR).** (1) scaffolding — type + collector, no behaviour
change; (2) route the hole path (callouts + locations) through it, resolver *reproduces*
`_maybe_tabulate_holes` → byte-identical; (3) add ISO pattern-grouping (`group_balloon`) →
first intended drift, fixes #348, re-bless CTC-02; (4) fold in slots/step/pmi, delete the
string-grep. Keeps the byte-identity tripwire through steps 1–2. **Landed as PR-1 through
PR-4c**, (4) split further into 4a/4b/4c once its real scope proved bigger than one PR —
see the roadmap doc.

## Amendment 2 — Precise leader geometry + Policy B (P5-strand-3, 2026-07-02)

**Status:** Accepted; landed (#368). Two findings from finishing the strip-3
allowlist burn-down (`tests/test_layout_cleanliness.py`'s `_KNOWN_OVERLAPS`)
turned out to be load-bearing for the rest of ADR 0009, not local fixes — recorded
here so P4 and any future placer migration don't rediscover them the hard way.

**Finding 1 — AABB occupancy is provably too coarse for a diagonal leader, and
this is exactly what P4 exists to fix.** `strip_obstacles`'s occupancy model
(P0, `_common.py`) represents every occupant as an axis-aligned bounding box —
correct and cheap for rectilinear geometry (dimension lines, tables, hatching),
but a **diagonal leader shaft**'s AABB over-claims the empty triangle on either
side of the true line (this was already flagged, prophetically, in
`_geom_box`'s own docstring and in this ADR's Consequences: *"angled leaders are
first-class but weaken the guarantees where mixed"*). Migrating
`_annotate_holes`'s hole-callout placer onto `strip_obstacles` (P5 strand 3)
made this concrete and costly: a plain AABB collision check caused **5 real
regressions** on ordinary fixtures with no actual crossing, because a callout's
diagonal shaft merely *near* an obstacle registered as blocked.

The fix, `_segment_hits_box` (`annotations/_common.py`) — a precise
line-segment-vs-AABB intersection test, used for the diagonal shaft portion of
a leader only (the elbow→label shelf+text stays an ordinary AABB check, since
it genuinely is axis-aligned) — is a **direct, reusable building block for P4**
("fold the #305 angled-leader nudge into the model as a first-class leader
style," `docs/plans/strip-layout-boundary-labeling-roadmap.md`). P4b/P4c should
start from this primitive rather than re-deriving it, and should audit whether
`strip_obstacles`'s AABB representation needs a general precise-geometry escape
hatch for *any* future non-rectilinear occupant, not just hole-callout leaders.
Two narrower residual gaps in the current implementation are filed, not fixed:
**#366** (the section-row reservation's un-widened extent can miss later
bolt-circle-driven widening) and **#367** (the precise shaft check still treats
the leader as a zero-width line, ignoring the rendered arrowhead's flare/line
width) — both real, both currently unexercised by any corpus fixture.

**Finding 2 — "Policy B": prefer a bounded, visible crossing over an unbounded
relocation or a silent drop.** This pattern first appeared informally for
`side_drilled`'s `{hc_side0, dim_loc_side_z2300}` SPACE-CONSTRAINED entry (P1b):
when relocating a dimension to avoid a corridor conflict isn't cleanly possible,
keep it on its natural view and accept the same-feature crossing rather than
drop a real dimension. P5 strand 3 needed the *same* decision again, independently,
for hole callouts avoiding the section cutting-plane arrow — and this time made
it an explicit, reusable comparison: **a candidate is only relocated when doing
so costs no more than one `min_gap` of extra displacement from its natural
position; otherwise it stays put and the crossing is accepted (logged, never
silently dropped)** — never an unbounded search for *some* clear spot regardless
of cost. Ratified by the user (2026-07-02): *"I would rather have a crossing
leader line than drop the callout... especially if I have hand-tweaked the
design."*

This is now a **named, two-precedent pattern**, not a one-off special case —
treat it as the default answer whenever a future placer migration (P4, or any
new strip occupant) faces "avoid vs. relocate vs. drop": bound the relocation
cost, accept a cheap crossing beyond that bound, never drop a real annotation
for a placement reason alone. The `_KNOWN_OVERLAPS` allowlist's steady state is
therefore **not** an empty PENDING set converging to zero — it is a permanent
allowlist of `BENIGN` (structural, ISO-legitimate) and `SPACE-CONSTRAINED`
(policy-B-accepted) entries, with `PENDING` (a genuine unaddressed placer
defect) the only category a phase is obligated to empty.

**Process note.** Both findings surfaced only because the actual migration was
attempted, not from analysis — three rounds of independent adversarial review
were needed to converge (round 1 found the AABB-precision gap after the naive
reservation-only fix proved insufficient; round 2 and 3 found smaller residual
issues). The roadmap's original strand-3 estimate ("audit the allowlist, one
PR") undersold this badly; treat future "should be a small placer migration"
estimates with the same caution, especially where diagonal/angled geometry is
involved.

## Amendment 3 — Min-total-leader-length via L1 isotonic regression (P4b, #318)

**Status:** Proposed — design only, not yet implemented. Recommendation below
reached in a design discussion with the user (2026-07-02) and backed by an
empirical verification pass (real QP-solver comparison, not just literature
citation); flagged decisions below still need explicit sign-off before coding
starts.

**Problem.** `plan_strip`'s current position solve (`_solve_strip_1d_var`, via
kiwisolver/Cassowary) is a constraint-*satisfaction* solve with a strength
hierarchy — "pull toward natural position" is a `strong` constraint, not a
minimised objective. It can settle on a placement that satisfies every
constraint (order, per-pair gap from P4a, `[lo,hi]` bounds) without finding
the placement that minimises total leader length, especially once a strip is
crowded and several candidates get pulled off their natural position at once.
P4b replaces *only this inner positioning step* with an exact solve — the
outer selection/drop loop (priority-ranked, over-capacity candidates dropped
until the rest fit, P2/#322) is untouched, and this must keep the same
"return `None` when the fixed candidate set is provably infeasible" contract
`_solve_strip_1d_var` has today, since the drop-and-retry loop depends on it.

**Decision.** The exact problem — fixed order (already established
crossing-free by P2), a required minimum gap between each adjacent pair
(`gap_i`, from P4a), a closed bound `[lo, hi]`, minimise total leader length
(`Σ|p_i − x_i|`, i.e. **L1**, not L2 — leader length is a real distance, not
a squared one) — reduces exactly to **L1 isotonic regression**, via three
composed steps:

1. **Gap-shift.** `q_i = p_i − Σ_{j<i} gap_j` turns "monotone with per-pair
   minimum gaps" into plain "monotone, no gap" on the shifted naturals
   `y_i = x_i − Σ_{j<i} gap_j`. (This is the reduction already sketched in
   conversation before this amendment was written; the research pass below
   confirmed it's sound and standard, not just plausible.)
2. **Boundary-pinning, not clipping, for the `[lo,hi]` bound.** Add two
   fictitious endpoints pinned at `lo` and `hi − Σgap` (effectively infinite
   weight) rather than clamping the unconstrained result afterward.
   Boundary-pinning is exact by construction for both L1 and L2; post-hoc
   clipping happens to also be exact **for L2 only** (verified empirically,
   see below) — not a coincidence worth relying on for L1, so the
   implementation should pin, not clip, to stay correct under either loss if
   the choice is ever revisited.
3. **Weighted-median PAVA**, not weighted-mean PAVA, to get true L1 —
   textbook (Robertson/Wright/Dykstra, *Order Restricted Statistical
   Inference*, 1988; Chakravarti, "Isotonic Median Regression: A Linear
   Programming Approach," *Math. of OR* 14(2), 1989), evaluated below against
   every library alternative a reviewer of this amendment asked about, with
   evidence rather than recollection.

   | Library | Verdict | Evidence |
   |---|---|---|
   | `scipy.optimize.isotonic_regression` | not suitable | L2-only, no box-constraint parameter (scipy is already a transitive dependency via build123d, so this isn't a cost objection — a capability one). |
   | `sklearn.isotonic.IsotonicRegression` | not suitable | Also L2-only; native L1 support was proposed and closed unlanded upstream (scikit-learn#14569). |
   | `cvxpy` | not suitable | The formulation is trivial (~8 lines), but `pip install --dry-run cvxpy` pulls **14 packages, 85MB+** (numpy, scipy, scs, highspy, clarabel, osqp, qdldl, …) — and, decisively, its **default solver changed between versions** (ECOS → Clarabel, ECOS dropped as a bundled dep in 1.6); different bundled solvers are documented to return numerically different optima for the same problem (iterative ADMM/interior-point tolerances, not an exact combinatorial algorithm). That's disqualifying against ADR 0001's determinism requirement specifically, not merely "heavy." |
   | Google OR-Tools | not suitable | Could model the problem (LP/CP-SAT), but the wheel alone is ~30MB plus 8 more deps (pandas, protobuf, absl-py, …) — enterprise-scale tooling for a sub-millisecond, <20-item problem. Wrong scale for what we need. |
   | NetworkX | not suitable, for a principled reason | No LP/PAVA implementation, and L1 isotonic regression doesn't reduce to a graph shortest-path/matching problem the way L∞ isotonic regression does (L∞ is a bottleneck/max-type objective with a known graph reduction, arXiv:1507.02226; L1 is a separable *sum* of convex terms — natively an LP or a PAVA fixed-point, not a graph problem). |
   | `pyStoNED` (PyPI) | not suitable | Confirmed dependency tree: pyomo, mosek, pandas, matplotlib — a full econometrics/LP stack, not a fit for a single positioning primitive. |
   | `stucchio/isotonic` (GitHub) | not suitable | Does support Lp losses including L1, but has no gap/box-constraint support, isn't published to PyPI, and is unmaintained (12 commits, no releases). |
   | Other PyPI hits (`cir-model`, `MOBPY`, `calibre`, `netcal`, `torchsort`, `regressio`, `constrained-linear-regression`) | not suitable | Probability-calibration/smoothing tools or constrained *linear regression* (bounding coefficients, not per-point isotonic values) — none address gap/box-constrained L1 isotonic regression. |

   Every alternative either lacks L1 + gap + box support outright, or brings a
   large, version-drifting solver stack whose determinism guarantees are
   weaker than an exact combinatorial algorithm — the CVXPY/OR-Tools
   rejection rests on ADR 0001's determinism requirement specifically, not on
   dependency size alone (size is corroborating, not the deciding factor).

   **Decision: implement the positioning solve using the standard
   weighted-median PAVA algorithm** (pure Python, ~30-40 lines; this is a
   well-known algorithm, not new research). A third-party implementation may
   replace the hand-rolled one if a suitable lightweight, maintained,
   deterministic L1/gap/box-constrained implementation becomes available —
   this amendment fixes the *algorithm*, not the *implementation vendor*.

**Verification, not just citation.** Before recommending this, the reduction
was checked against a real solver rather than trusted on the literature
alone: 300+ randomised trials comparing (gap-shift + boundary-pin +
weighted-median-PAVA) against a genuine convex QP/L1 solve of the original
(un-reduced) problem — worst-case gap ~2.6e-7 (solver tolerance), i.e.
effectively exact. Two findings worth keeping on record for whoever writes
the real test:
- A first attempt at "ground truth" via `scipy.optimize.minimize(SLSQP)`
  silently produced a non-monotonic "optimum" — SLSQP violated its own
  constraints without raising. **Use a real QP/LP solver as ground truth for
  this class of problem, not a general nonlinear optimiser.**
- The L2-clip-is-exact result (sklearn's `y_min`/`y_max` is literally
  `np.clip()` after unconstrained PAVA) also checked out empirically (400
  trials, worst gap ~1.8e-6) — included here because it's surprising enough
  to have been worth doubting, and because it's *why* clipping is tempting
  but should not be assumed to generalise to L1 without its own check.

**Open, not yet decided:**
1. Whether to port the QP-comparison harness into the permanent test suite
   as a property test (in the spirit of `test_layout_property.py`) — this
   would add `cvxpy` (a real QP/LP solver) as a **test-only** dependency,
   which is heavier than anything currently in the dev/test dependency set.
   Alternative: keep the QP comparison as a one-time development-time check
   (not committed) and rely on smaller hand-verified example cases plus the
   existing layout-cleanliness/determinism property tests for ongoing
   regression coverage. Needs a decision before implementation starts.
2. Expected behaviour change: some strips will re-pack to a tighter, truly
   optimal arrangement where the current Cassowary solve settled for a
   merely-constraint-satisfying one — same "dense sheets re-pack; covered by
   invariants" class of change P3 already established, not a regression by
   itself, but worth calling out explicitly since it touches real output.

## Related

- [ADR 0001](0001-deterministic-generation-over-editable-dsl.md) — determinism;
  the reason A is chosen over B.
- [ADR 0002](0002-iterate-via-lint-critique-and-domain-repair.md) — repair stays
  a safety net; principled escalation replaces most ad-hoc drops.
- [ADR 0003](0003-constraint-based-layout.md) — the two-layer model this ADR
  makes concrete for the strips; the global 2-D solve stays deferred (#94).
- [ADR 0004](0004-compose-then-pack-view-blocks.md) — the outer block-packing
  layer; 0009 is the inner per-view layer.
- [ADR 0008](0008-unified-feature-model-and-dimensioning-planner.md) — the
  planner whose intents feed the collect phase.
- Research note: [`research/annotation-placement-boundary-labeling.md`](../research/annotation-placement-boundary-labeling.md).
- Issues: #150 (consolidate 1-D placement — subsumed here), #301/#302/#303
  (layout-cleanliness/convergence — closed out by the new invariants),
  #306/#54 (detail-view escalation — the "doesn't fit" target), #305 (the
  original angled-leader-vs-centreline case Amendment 2's Finding 1 traces back
  to), #318 (P4 — the direct consumer of Amendment 2's `_segment_hits_box` and
  "policy B" findings, and the subject of Amendment 3), #366/#367 (Amendment
  2's filed residual gaps).
