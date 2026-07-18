# ADR 0009 — Boundary labeling: collect-then-solve per-strip annotation placement

- **Status:** Accepted (2026-06-30)
- **Deciders:** Paul Fremantle (pzfreo)

## Current decision (as amended, 2026-07-12)

Annotation placement within each view's strips is a **collect-then-solve boundary-labeling**
stage, not place-as-you-go. Per view: **collect** every strip occupant (plus fixed
obstacles) as a geometry-only candidate; **solve** once per strip — *select* (priority;
over-capacity → a first-class escalation), *assign* (side), *order* (= feature order ⇒
leaders **crossing-free by construction**), *space* (a deterministic **PAVA**
minimum-total-leader-length solve — kiwisolver/Cassowary retired, Am 3–4 — with
central-hole **anchoring**); **emit**. Keep-out (centre-lines / bolt-circles) is a keep-out
**band** folded into the **one shared `carve_free_segments` primitive** every pass uses —
the separate banded-PAVA DP was **retired** (Am 9). Corridors are solved **once across
passes** (Am 6); candidates carry real per-candidate footprints and GD&T frames are
first-class candidates (Am 7). **Deterministic and explainable** ("label *i* sits here
because order + min-gap + shortest-leader"), never a global metaheuristic.

*The 9 amendments below are the solver's evolution trail (escalation objects → precise
leader geometry → the L1/PAVA min-leader solve → band-aware then shared-carve keep-out →
one-solve-across-passes → real footprints + GD&T → solver consolidation) — read them for
the* why, *not to reconstruct the current state.*

**Remaining migration (tracked by #636 / consolidation epic #635).**
Amendment 8 consolidated PMI, front-view callouts, and the pitch fallback. #636
then migrated the two 0.3.0 features that had regressed onto the solver-invisible
single-position carve (`carve_free_position`) — **`render_plates` (#559)** and
**`render_step_positions` (#555)** now register `CorridorCandidate`s that co-solve
with the locations sharing their strips — and added a fail-closed guard test
(`tests/test_carve_free_position_callers.py`) so the legacy path can no longer
silently attract new callers. Until the remaining sites join the solve, the
"invisible-occupant collision class removed **by construction**" claim above holds
only for the migrated passes.

The carve callers still allowed in `annotations/` (the guard-test allowlist):

- **Permanent exemption** — `_place_pitch_dim`'s diagonal fallback (`holes.py`):
  searches an arbitrary outward vector, so its dim cannot occupy a 1-D axis-aligned
  strip tier and cannot be a solve candidate at all (Amendment 8's decision).
- **Pending migration (#636)**, each with a genuine design fork deferred to its own
  PR: `render_height_ladder` (the leapfrog witness cursor — a collect-then-solve
  pass must rebuild the chain from solved positions), `render_gdt`'s PMI
  alternate-strip fallback (runs inside a candidate's `on_drop`, i.e. after its
  strip has already drained — needs a two-side candidate, not a post-hoc carve),
  and the detect-only verbs `add_feature_callout` / `add_feature_location` (a
  detect-only build has no shared corridor batch to register into — cousins of the
  ADR 0012-exempt `place_dim`).

A new carve caller in `annotations/` fails the guard test; a genuine new exemption
must be recorded in this note first.

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
  The underlying 1-D solve (`layout.py`) is reused unchanged in spirit, though
  the concrete carrier is `StripCandidate`/`plan_strip`, not 0003's original
  `Placeable`/`LayoutSolver` — see ADR 0003's 2026-07-10 correction (#547).
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

**Status:** **Superseded by Amendment 4** (2026-07-02). The *algorithm* it
settled — L1 isotonic regression with gap/box constraints, minimise total
leader length — stands and is what shipped. The *implementation choice*
(`scipy.optimize.linprog`, HiGHS) did not survive first contact: the L1
optimum is non-unique far more often than the "one bounded degenerate case"
this amendment assumed, and HiGHS resolves those ties differently across the
two scipy builds in the CI matrix — an ADR 0001 determinism violation that
broke every CI job. Amendment 4 keeps the algorithm and swaps the solver for
a deterministic weighted-median PAVA (the fallback this amendment had already
verified). Retained below as the decision record and the reason the switch
was needed.

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
a squared one) — is mathematically **L1 isotonic regression with a minimum-gap
and box constraint**, and is small enough (well under 20 candidates per strip)
to solve directly as a **linear program** via `scipy.optimize.linprog`
(HiGHS backend): the standard L1-as-LP trick (auxiliary variables
`t_i ≥ p_i−x_i`, `t_i ≥ x_i−p_i`, minimise `Σt_i`) plus the monotone/gap/box
constraints encoded directly as linear inequalities (`p_i + gap_i ≤
p_{i+1}`, `lo ≤ p_i ≤ hi`) — no reduction trick needed, unlike the
gap-shift/PAVA route below. Every library alternative was evaluated below
with evidence rather than recollection.

   | Library | Verdict | Evidence |
   |---|---|---|
   | `scipy.optimize.isotonic_regression` | not suitable | L2-only, no box-constraint parameter. |
   | `sklearn.isotonic.IsotonicRegression` | not suitable | Also L2-only; native L1 support was proposed and closed unlanded upstream (scikit-learn#14569). |
   | `scipy.optimize.linprog` (HiGHS) | **selected** | Already a transitive dependency (build123d → scipy); solves the *direct* problem formulation, no reduction needed. Verified correct on hand-built cases; deterministic across 30 repeated calls and both HiGHS sub-methods (`highs-ds` simplex, `highs-ipm` interior-point) — including on a deliberately degenerate case. See caveat below. |
   | `cvxpy` | not suitable | The formulation is trivial (~8 lines), but `pip install --dry-run cvxpy` pulls **14 packages, 85MB+** (numpy, scipy, scs, highspy, clarabel, osqp, qdldl, …) — and, decisively, its **default solver changed between versions** (ECOS → Clarabel, ECOS dropped as a bundled dep in 1.6); different bundled solvers are documented to return numerically different optima for the same problem (iterative ADMM/interior-point tolerances, not an exact combinatorial algorithm). |
   | Google OR-Tools | not suitable | Could model the problem (LP/CP-SAT), but the wheel alone is ~30MB plus 8 more deps (pandas, protobuf, absl-py, …) — enterprise-scale tooling for a sub-millisecond, <20-item problem. Wrong scale for what we need. |
   | NetworkX | not suitable, for a principled reason | No LP/PAVA implementation, and L1 isotonic regression doesn't reduce to a graph shortest-path/matching problem the way L∞ isotonic regression does (L∞ is a bottleneck/max-type objective with a known graph reduction, arXiv:1507.02226; L1 is a separable *sum* of convex terms — natively an LP, not a graph problem). |
   | `pyStoNED` (PyPI) | not suitable | Confirmed dependency tree: pyomo, mosek, pandas, matplotlib — a full econometrics/LP stack, not a fit for a single positioning primitive. |
   | `stucchio/isotonic` (GitHub) | not suitable | Does support Lp losses including L1, but has no gap/box-constraint support, isn't published to PyPI, and is unmaintained (12 commits, no releases). |
   | Other PyPI hits (`cir-model`, `MOBPY`, `calibre`, `netcal`, `torchsort`, `regressio`, `constrained-linear-regression`) | not suitable | Probability-calibration/smoothing tools or constrained *linear regression* (bounding coefficients, not per-point isotonic values) — none address gap/box-constrained L1 isotonic regression. |
   | Hand-rolled weighted-median PAVA | not selected | Textbook algorithm (Robertson/Wright/Dykstra 1988; Chakravarti 1989), verified sound via the gap-shift + boundary-pinning reduction (300+ randomised trials vs. a real QP/L1 solve, worst-case gap ~2.6e-7). Fully eliminates the tie-break risk below by construction — but superseded by `linprog` per the user's explicit call (2026-07-02): scipy is already a dependency, and the smaller/simpler implementation outweighs a narrower, bounded version of the same risk class. Kept here as the fallback if the caveat below ever proves troublesome in practice. |

**Accepted risk: solver-internal tie-breaking on degenerate optima.** On a
constructed case with a non-unique L1-optimum (`x = [4,3,2,1]`, no gap
requirement — any constant in `[2,3]` is equally optimal), HiGHS
consistently picked `3.0` (the upper end), stable across repeated calls and
both sub-methods *within scipy 1.17.1*. This is the same *category* of risk
that disqualified CVXPY (a solver's internal vertex-selection on a degenerate
LP is not a documented contract — a future HiGHS/scipy version could pick a
different point in the tie without notice), just narrower in practice: one
fixed solver, already pinned via the project's lockfile, not a
configurable-default meta-package. **Ratified by the user (2026-07-02):**
accept this bounded risk in exchange for the dependency-free, simpler
implementation. **Mitigation for the implementer:** add a regression test
that pins the exact degenerate case above and asserts today's known result —
so a future scipy/HiGHS upgrade that silently shifts tie-break behaviour is
caught by CI, not silently absorbed into a changed drawing output.

**Open, not yet decided:**
1. Whether the general-correctness verification (the gap-shift/PAVA route's
   300+-trial QP comparison, done during design to confirm the problem
   shape) is worth porting into the permanent test suite as an *independent*
   correctness check on `linprog`'s output — this would add `cvxpy` as a
   **test-only** dependency. Given `linprog` solves the direct formulation
   (no reduction to trust), this is now a lower-priority nice-to-have rather
   than load-bearing; the degenerate-case regression test above is the one
   that actually matters and needs no extra dependency.
2. Expected behaviour change: some strips will re-pack to a tighter, truly
   optimal arrangement where the current Cassowary solve settled for a
   merely-constraint-satisfying one — same "dense sheets re-pack; covered by
   invariants" class of change P3 already established, not a regression by
   itself, but worth calling out explicitly since it touches real output.

## Amendment 4 — Deterministic weighted-median PAVA + central-hole anchoring (P4b, #318)

**Status:** Accepted (2026-07-02) — implemented and verified. Supersedes
Amendment 3's *implementation* (not its algorithm).

**Problem (why Amendment 3's `linprog` route failed).** Amendment 3 shipped as
`scipy.optimize.linprog` and CI failed on **every** OS/Python job. Two symptoms,
one root cause:

1. **Determinism (ADR 0001).** The L1 objective (minimise `Σ|p_i − x_i|`) is a
   median-type objective and is **non-unique whenever a crowded pair is pushed
   apart by the gap constraint** — an entire interval of positions is equally
   optimal, and HiGHS returns one arbitrary vertex. The vertex differs between
   the two scipy builds the lockfile pins across the matrix (1.15.3 on py3.10,
   1.17.1 on py3.11+): e.g. `naturals=[1.99,2.76,5.06,5.25], gaps=[3,0,5]` →
   `[0,3,3,8]` on one, `[0,3,5,10]` on the other, both L1-cost 7.04, ~2 mm apart
   (far above the 0.1 mm snapshot grid). A 4000-case fuzz found this on
   realistic (strictly-positive-gap) strips. So `build_drawing` was no longer a
   pure function across the matrix. Amendment 3's mitigation — pinning the one
   degenerate case `[4,3,2,1]` — gave false confidence: it addressed a single
   corner, not the structural non-uniqueness. *(Found by the PR's adversarial
   review and confirmed against both pinned scipy versions.)*
2. **Domain-wrong tie-break.** The same non-uniqueness let the solver place a
   **central** hole's callout off the view-centre row: for two callouts whose
   naturals are close, `[keep #0, move #1]` and `[move #0, keep #1]` have equal
   total length, and HiGHS chose the one that shifts the central label
   (`test_prismatic_central_hole_callout_not_lifted`: 5 mm off, > the 3 mm
   font-height rule).

**Decision.** Keep Amendment 3's algorithm; make two changes.

1. **Solve with weighted-median PAVA, not an LP.** `_solve_strip_1d_pava`
   (`layout.py`) computes the exact same L1 optimum via the Pool Adjacent
   Violators Algorithm: shift `s_i = x_i − Σ_{j<i} gap_j` (min-gap chain → plain
   monotone), weighted-median PAVA for the L1 isotonic fit, then clamp to the
   **global** box `[lo, hi − Σgap]` the shift reduces the per-point bounds to
   (an exact clamp, since after the shift the box is global — verified, not the
   post-hoc-clip the research note warned is inexact) and unshift. It is
   **deterministic by construction** — the median's tie is resolved by a fixed
   *lower-median* convention, no solver vertex choice, so no cross-version drift.
   Verified: total-leader-length matches `linprog` on all 4000 fuzz cases;
   feasibility (gaps/box) and the `None`-on-infeasible contract hold. This is the
   hand-rolled route Amendment 3 had *already* verified and kept "as the
   fallback if the caveat ever proves troublesome" — it did, on day one.
   **`scipy` is dropped** as a dependency (it was only added for `linprog`).
2. **Anchor central features.** A new `StripCandidate.anchored` flag maps to a
   dominating weight in the weighted median, pinning that candidate at its
   natural position while the rest flow around it. `annotations/holes.py` marks
   the coaxial/central hole's callout anchored (a view-centre test; Amendment 5
   later gated it to prismatic parts), so "central hole stays on the centre row"
   is now a **domain decision
   the solve honours by construction**, not an accident of which tied vertex a
   solver happened to pick. It remains a spacing hint, not a hard pin — an
   anchored candidate can still be *dropped* when the strip is over capacity.

**Consequences.** `build_drawing` is again pure across the matrix. Corpus
output is unchanged — all snapshots stay byte-identical to `main` (the PAVA +
anchoring solve reproduces the old Cassowary placement exactly on every corpus
strip, including the crowded/anchored bracket strips). Anchoring and the
deterministic optimum are a determinism-and-correctness guard, not an
output-improvement change here. The `test_deterministic_by_construction` unit test pins the
lower-median convention so any future change to it is a conscious, reviewed one
(replacing Amendment 3's brittle solver-vertex pin). No new runtime dependency;
the solve is O(n) per strip for the <20-candidate strips in practice.

## Amendment 5 — Band-aware PAVA: fold the coaxial-lift into the solve (P4c, #318)

**Status:** Accepted (2026-07-02). Completes P4 (P4a gaps, P4b optimum, P4c
this). Retires the last pre-solve placement heuristic in the callout pass.

**Problem.** A callout's "⌀… ↓…" text must not sit *on* certain horizontal lines
— a turned view's centre-line, or a location dimension's extension line
(#305/#321). Until now `annotations/holes.py::_coaxial_lift` handled this as a
**pre-solve nudge**: it lifted a callout's natural row one clearance off any such
line *before* the strip solve. Two defects made it a "special case" the roadmap
scheduled for removal: (1) it was pre-solve, so the P4b spacing solve could
legally **re-crowd** a lifted callout back toward the line while spacing its
siblings — the constraint was not one the solve honoured; and (2) a fixed lift
toward the "roomier half" is not the min-leader-length choice.

**Decision.** Make avoidance a property of the solve. A "keep-out **band**"
`(centre, half_width)` is a row the labels must avoid; `plan_strip` takes a
`forbidden=` list and routes to `_solve_strip_1d_pava_banded`. A band is a
**non-convex** keep-out (above *or* below the row) the plain PAVA box can't
express — but the bands split `[lo, hi]` into disjoint feasible **segments**, and
*within one segment* the box is convex again, so the exact PAVA atom still
applies. The fixed label order (crossing-free, from P2) means labels map to
segments as contiguous runs at non-decreasing indices; a small O(n²·segments) DP
searches for a low-cost partition, solving each run with `_solve_strip_1d_pava`
inside its segment. **No bands → byte-identical to the plain solve.** Deterministic
(each run solve is; equal-cost states break on the lexicographically smaller
position vector).

The DP is **not globally optimal across ≥2 segments** — it keeps one
representative per labels-placed count, but a later segment's feasible room
depends on the last placed position, so a band alongside an `anchored` candidate
can drag the anchor off centre. Unreachable on the corpus (anchor and band never
co-occur in a placed strip: the centre-line band is gated to rotational parts,
which don't anchor), so output is byte-identical; the exact fix — a Pareto
frontier over `(cost, last_pos)` per count — is a tracked follow-up.

**Correction (2026-07-10, #381):** "unreachable" above covers only the
centre-line band. `holes.py`'s *other* band source — `reserved_rows`, the
off-axis holes' location-dim extension lines — is **not** gated by part class;
it applies to prismatic parts too, independently of `_is_central`. A prismatic
part with a genuinely central hole (anchored) and another off-axis hole on the
same strip hit this DP gap in the un-carved baseline solve (`holes.py`'s
pre-#381 `base_res`, since renamed `base_y` by Amendment 9 below) — not a
hypothetical combination. See Amendment 9, which retires this DP rather than
making it exact.

Two supporting changes in `holes.py`:
- **Bands built from the same causes** `_coaxial_lift` used — the off-axis
  location-dim rows, plus the centre-line row on a turned/rotational view — and
  handed to both the baseline and the carve-aware per-segment `plan_strip` calls.
  `_coaxial_lift` is **deleted**.
- **Anchoring gated to prismatic parts.** The centre-line band pushes a coaxial
  bore *off* centre; the Amendment 4 anchor pins a central bore *on* centre — the
  opposite. They are mutually exclusive by part class, so `_is_central`
  (anchoring) now returns false exactly when the centre-line band is on
  (`is_rotational or prof`). A prismatic central bore still anchors on centre; a
  turned coaxial bore is pushed off it by the band.

**Graceful degradation.** Avoidance is a strong preference, not a hard drop. A
band can be *wider than the whole strip* (a shallow view — the `dshape` side
strip is 16 mm, its band 18 mm), leaving no clear segment. Rather than drop a
real callout to honour the band — against policy B (never drop a real annotation
just to avoid a crossing) — the DP-can't-place case falls back to a plain solve
toward band-edge-snapped naturals: the callout sits at the strip edge farthest
from the row (minimal residual), exactly what the old lift did. A genuinely
over-capacity strip still returns `None` for the caller's drop-and-retry.

**Consequences.** Most of the corpus is unchanged; `dshape` (shallow-strip
fallback) stays byte-identical. Two turned parts re-blessed where the solve now
seats the coaxial callout at the min-leader segment edge instead of the old fixed
lift: `flange` (callout moves to the roomier segment) and `drive_screw_x` (same,
which also lets its Z location dim route to the front view). Both are lint-clean
and pass the layout-cleanliness ratchet (no new overlaps). The tie-break between
the two equal-cost segments is the lexicographic (lower-position) rule, not the
old "roomier half" — a reviewed, deterministic choice, not byte-identity chasing
(ADR 0004: output may change). `_solve_strip_1d_pava` is untouched; the band
layer is a wrapper.

## Amendment 6 — Unify the shared above corridors: one solve across passes (#345/#346)

**Status:** Accepted (2026-07-03). Delivers the ADR's "**consume the full per-strip
intent set before committing**" (Decision, phase 1) for the two contended above
corridors — the first place two *different render passes* feed one strip.

**Problem.** The collect-then-solve inversion had been applied *within* each pass but
never *across* passes. `render_locations` (hole X/Y-location ladder) and `render_slots`
(slot size + position) both write the SAME `pv_zones.above` / `sv_zones.above` strip, at
different points in the orchestrator, each committing its own `place_strip_candidates`
solve. So the later pass only carved around the earlier one's placed dims. Two CTC-01
defects followed directly: **#345** a hole location and a slot position that measure the
same datum span each drew their own dim (the duplicate "75"); **#346** the two passes'
dims interleaved into a non-monotonic ladder (75, 80, 725, 560, 240, 75) because neither
solve saw the other's candidates.

**Decision.** A per-strip **`CorridorCandidate` batch** on the drawing that both passes
*register* into instead of committing, drained once (`solve_corridor`) after both have run
— one `place_strip_candidates` solve over the whole set, so the full intent set is seen
before anything is placed. Two properties fall out by construction:

- **Dedup (#345).** Candidates carry a coincidence key on the measured span; the
  higher-precedence one survives (a hole *location*, which feeds coverage + the table
  escalation, outranks a coincident slot *position* line). The displaced duplicate is
  dropped **silently** — it was never starved, so firing `slot_dim_dropped` would be a
  false report. The key is built from the **raw (pre-snap) endpoints** on both sides —
  the location key uses the raw ref, so the slot key must too. Keying the slot side on
  its *snapped* endpoint (`datum + round(span, 1)`) let a ~0.05 mm snap gap cross a
  0.1 mm page-rounding bin at fractional datum distances, so a genuinely coincident span
  escaped dedup and the duplicate survived (adversarial-review finding, fixed in the same
  PR; regression-tested at a fractional 20.15 distance).
- **Ordering (#346).** Candidates carry an `order` key with two segregated runs: feature
  **size** dims nearest the view, datum **location** dims nesting outward in ascending
  datum-distance order — so a slot length never lands mid-ladder and the location chain is
  one monotonic run.

Each candidate keeps its pass's own bookkeeping as `on_place`/`on_drop` callbacks, so
every existing drop code, `Escalation`, the hole-table trigger, the force-keep policy-B
(a plan-X/side-Y location has no alternate view), and the slot's below-side fallthrough
(now inside `on_drop`) are preserved. The drain runs before detail views and PMI so they
still see the placed ladder as an obstacle.

**Scope + follow-up.** Deliberately the two contended above corridors, where both bugs
live. At acceptance time, **PMI's above-corridor writes** (`render_pmi`,
`carve_free_position`) still carved outside the batch, so a PMI-annotated part could
interleave a PMI dim in that corridor. Amendment 8 (#524) later folded PMI into the same
batch. The below / right ladders (envelope, height leapfrog, step lengths) are untouched
here — they carry no datum-location ladder and share no candidates, so unifying them would
be scope without a bug to fix.

**Consequences.** Only `slotted` re-blessed (its position dim re-tiered ~2 mm by the
shared solve); the pure-location and hole-free fixtures stay byte-identical. A `holed_slot`
corpus fixture (a hole X coincident with a slot edge) plus explicit dedup + monotonic-order
assertions lock both bugs; 85 layout/e2e tests unchanged green.

## Amendment 7 — Real per-candidate footprint; GD&T frames as first-class candidates (#61)

**Status:** Accepted (2026-07-06). The first candidate whose footprint is *not* one
label-height, and the first non-dimension occupant of a shared corridor.

**Problem.** `place_strip_candidates` built every `StripCandidate` with
`size=(tier, tier)` — the label-height square that is right for a dimension but wrong for
a wide/tall occupant. ADR 0011's GD&T aspect layer (#61) needs to place a **feature
control frame** (~24×6 mm) through the same collect-then-solve strip stage, and two
stacked frames spaced by one label-height would overlap.

**Decision.**
- `CorridorCandidate.size` (optional) carries a candidate's real page-mm footprint;
  `solve_corridor` forwards a `{name: (w, h)}` map into `place_strip_candidates`, which
  feeds it to the `StripCandidate` instead of the `(tier, tier)` hardcode. Absent → the
  `(tier, tier)` default, so **every existing dimension is byte-identical** — this is a
  pure extension, not a re-tiering. `plan_strip` already spaces by `size[idx]` (P4a), so
  no solver change is needed; the real gap simply flows through.
- The **strip-edge reservation** (`place_strip_candidates` pulls the outer boundary in so
  the outermost label doesn't overshoot `outer_limit`) also uses the real outward extent,
  not a fixed `tier` — else a glyph wider than `tier` renders off the sheet
  (`annotation_out_of_bounds`) instead of dropping when the strip is too narrow. It is
  orientation-aware: the Leader **centres** the glyph on the elbow for an above/below
  strip (outward extent = `height/2`) but places it **one-sided** for left/right (extent =
  full width). Dims carry no size → the reservation stays `tier`, byte-identical.
  *(Both this and the leader-box footnote above were adversarial-review findings on the
  P2b commit — a wide multi-datum frame rendered 17 mm off-sheet before the fix.)*
- The footprint is the **glyph's own box**, NOT the leader+glyph box. The leader shaft
  runs back to the feature, so measuring the composite would inflate the stacking-axis
  extent — exactly the reason dims reserve one label-height and not their witness span.
- **GD&T is placed at build time, as a first-class corridor candidate** (`render_gdt`,
  registered before the drain), not post-hoc. A frame placed after `build_drawing`
  returns is blind to the shared cross-view corridor and never triggers a repack — it
  overlapped a plan-view dim in a first cut. In the corridor it is ordered and spaced
  crossing-free *with* the dims, and `view=`-tagged so ADR 0004's `_measure_blocks` +
  repack separate cross-view. This is the disciplined answer to "where does a new
  annotation family go" — the collect-then-solve corridor, not `Strip.allocate`.

**Scope.** The `sizes` map is threaded but only GD&T sets it today; every dimension pass
still passes the default. This is the down-payment on the broader real-footprint
migration (diameters/envelope will want it next). Roadmap: `0011-phase2-aspects-roadmap.md`
(P2b). Tests: `tests/test_gdt_placement.py` (stacked frames reserve real footprint; a full
strip drops with a `gdt_dropped` warning; placement is lint-clean).

## Amendment 8 — Solver-path consolidation and remaining Bucket-C cleanup (#524)

**Status:** Accepted (2026-07-08). Closes the stale gap between the ADR 0009 direction
and several still-parallel placement paths found in the July layout audit.

**Problem.** After Amendments 6 and 7, the automatic path still had several important
ways to place sheet furniture without joining the shared solve:

- STEP/AP242 **PMI** dimensions (`render_pmi`) carved after the corridor drain, so a
  PMI datum dimension could still interleave with, or duplicate, an automatic corridor
  candidate that would otherwise be ordered/deduped with it.
- **Front-view hole callouts** used fixed row stepping plus a local retry list, while
  plan/side callouts already used the strip solver.
- **Pitch dimensions** had an obstacle-aware strip path for axis-aligned arrays, but the
  diagonal/rotated/full-strip fallback still used count-based outward stacking.
- **Repair** still treated `annotation_overlap` as a placement problem it could nudge
  with a fixed step, hiding defects the solver path should own.
- The scale/page pre-pass had a fixed three-iteration step-count sizing loop and accepted
  the last value even if the legibility count had not converged.

**Decision.**

- `render_pmi` queues authored PMI as `CorridorCandidate`s before `drain_corridors`, with
  authored-intent priority and fallback sides handled from the candidate's drop callback.
  PMI now co-solves with locations, slots, and GD&T in the same shared corridors.
- Front-view hole callouts are measured as strip candidates below the front view. The
  solve preserves crossing-free order and uses bore diameter as the over-capacity
  priority, matching the policy used by the plan/side callout queues.
- Pitch-dimension fallback now performs a bounded search along the chosen outward vector,
  probes the full generated dimension footprint, and rejects off-page or obstacle-hitting
  positions before trying the opposite side. This removes the residual count-stack shape
  without pretending a diagonal dimension is an axis-aligned strip occupant.
- `repair()` is narrowed back to a peephole safety net: it may flip a clear
  `dim_inside_part` wrong-side dimension, but it no longer repairs `annotation_overlap`.
- Step-corridor sizing iterates to a fixed point; if it cycles, it reserves the largest
  attempted step count and logs the non-convergence instead of silently accepting an
  arbitrary iteration result.

**Consequences.** The corridor path is now the owner for the remaining placement classes
that shared its strips most directly. The remaining non-corridor work is a narrower design
question: below/right ladders and user edit intents still need the ADR 0012/#477 fold-in,
but the fixed-step/count-stack residuals from the audit are gone. The review follow-up on
#524 also fixed `place_strip_candidates` segment selection so a high-priority candidate
that is rejected late by `forbid`/corridor blockers does not waste a usable slot; the
segment refills from the remaining candidates before committing placements.

## Amendment 9 — Retire the banded-PAVA DP; fold keep-out bands into the shared carve (#381)

**Status:** Accepted and implemented (2026-07-10, #381).

**Problem.** Amendment 5 gave keep-out bands their own segmentation-and-solve
mechanism (`_feasible_segments` + the cross-segment DP in
`_solve_strip_1d_pava_banded`), separate from the general obstacle-avoidance
path every other pass uses (`carve_free_segments` + nearest-segment greedy
assignment + independent per-segment `_solve_strip_1d_pava`, in
`place_strip_candidates`/`solve_corridor`). `holes.py` runs *both, nested*: it
carves for real 2-D obstacles, then re-invokes the banded DP inside each carved
segment for row-bands. No other pass gets row-avoidance at all — a GD&T frame
or PMI note has no way to avoid a centre-line, not because it shouldn't, but
because that avoidance only exists inside `holes.py`'s two direct call sites.

This is exactly the failure mode this ADR's own Context section names as the
reason it exists: a mechanism that doesn't share an occupancy model with the
rest of the corridor system, landing as a point-fix for one pass's symptom
(the old `_coaxial_lift` re-crowding bug) instead of a generalisation of the
shared solve. `carve_free_segments(lo, hi, intervals, pad)` and
`_feasible_segments(lo, hi, bands)` are structurally the same operation — both
reduce `[lo, hi]` minus a set of intervals to a free-segment list — differing
only in whether the clearance arrives as a separate `pad` or is pre-baked into
each band's half-width. Two APIs for one operation.

The cost of the duplication is now concrete, not just aesthetic: the DP has a
real correctness gap (Amendment 5's follow-up, #381) that a corrected
reachability read shows is live on ordinary prismatic parts today, not gated
by part class as originally believed (see the Correction note on Amendment 5).

**Decision.** Retire `_feasible_segments` and `_solve_strip_1d_pava_banded`.
Express a keep-out band as an ordinary carve interval
(`(centre - half_width, centre + half_width)`, `pad=0` since the clearance is
already in the half-width) fed into the existing `carve_free_segments` — the
same carve primitive `place_strip_candidates` already uses for every other
pass, though its own segment-fill strategy (sort segments once by distance
from the strip's inner edge, fill near-to-far via priority-capacity slicing)
is not reused here; `holes.py`'s two `plan_strip(forbidden=...)` call sites
instead route through a new per-candidate segment-assignment step (below)
built specifically for this carve, then solve each assigned segment with
per-segment `_solve_strip_1d_pava`, instead of calling `plan_strip`'s
`forbidden=` parameter directly. `plan_strip`'s `forbidden=` parameter and the
banded solve path are removed once nothing calls them.

This resolves #381 by construction, not by making the DP exact: an anchored
candidate assigned to its own obstacle-free segment never sees the band that
would have needed the DP's cross-segment reasoning to route around. It unifies
on the *one* carve primitive (`carve_free_segments`) every corridor pass
already uses — but band **declaration** (`band_intervals`, built from
`reserved_rows`/the centreline case) still lives entirely inside
`holes.py`'s own `_place_queue`, not threaded through the shared
`CorridorCandidate`/`solve_corridor` seam. A GD&T frame or PMI note still has
no way to declare a keep-out band; only `holes.py`'s two callers carve them.
Making that available to every pass is a further, separate change, not part
of #381.

A first implementation used plain nearest-segment greedy assignment — not
actually `place_strip_candidates`'s own strategy (see the Decision above), but
an independent review found it capacity-oblivious regardless: a candidate assigned to its nearest segment could
be dropped there even when a farther segment had ample free room — a real
regression against Policy B (never drop a real annotation to avoid a
crossing/band) that the retired DP, for all its own bugs, did not have. The
shipped fix instead does **global, priority-ordered greedy assignment with
live feasibility re-checks**: candidates are processed highest-priority-first;
each tries its nearest segment, then progressively farther ones, accepting a
segment only when re-running the real per-segment solve on the trial
membership drops nobody. Because segments only ever gain higher-or-equal-
priority members before a lower-priority one is tried, a segment's own
drop-on-overflow can (outside a rare priority tie) only ever evict the
candidate currently being tried, never a prior commitment — one pass, no
cascading, and every feasibility check is the real solver rather than an
approximate count, so it cannot reintroduce the DP's original bug.

**Trade-off (the honest edge).** This gives up the DP's aim of an exact,
provably cost-optimal cross-segment placement, in favour of a greedy
(highest-priority-first, nearest-segment-first) assignment that is optimal
per-candidate-in-order but not globally optimal across all orderings.
Byte-identity is not preserved for the (narrow) cases where the DP's
cross-segment optimum would have differed from greedy-assign's answer —
acceptable under ADR 0004's standing byte-identity waiver (output may change;
the invariant is lint-clean + no new overlaps).

**Investigated, not fixed (2026-07-10, #381 follow-up).** Solving each carved
segment independently raises an apparent second gap the trade-off above
didn't name: `min_gap` itself is not enforced *across* a band, so two
candidates assigned to the segments either side of a thin band could each sit
at their own natural position even when those positions are less than
`min_gap` apart — the old DP's single cross-segment solve used to catch this;
independent per-segment solves can't see across the carve. A review raised
this using the retired DP test's synthetic numbers (a 3 mm band, 10 mm
`min_gap`); those numbers don't occur in `holes.py`. Its band half-width is
`clr = font_size + 3·pad_around_text` and `min_gap = font_size +
2·pad_around_text` (`holes.py` line ~1316/1078), so `clr - min_gap =
pad_around_text`, always positive — a band's width (`2·clr`) always exceeds
`min_gap`. `pad_around_text` is not a public parameter: `builder.py`'s
`_assemble` always constructs `draft_preset(...)` without overriding it, so
it is fixed at `2.0` on every real build. The same argument covers
`obstacle_intervals` (pre-inflated by `min_gap` on each side already, so its
occupied width is always >= `2·min_gap`) and the combined band+obstacle carve
(`holes.py` lines ~1617-1622): `carve_free_segments` only merges intervals that
touch or overlap, so the worst-case cross-segment gap always equals the width
of the one occupied block separating two free segments, and every
contributor to that block (band alone, obstacle alone, or both) already
exceeds `min_gap` on its own. This failure mode is therefore unreachable
through `holes.py`'s actual formulas, not merely untested; no code change was
made. Documented here so a future change to the `clr`/`min_gap` formulas or
to `pad_around_text`'s fixed value re-opens this as a live gap, not a
resolved one.

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
  2's filed residual gaps), #524 (Amendment 8 solver-path consolidation), #381
  (banded-DP anchor-defeat gap; Amendment 9 retires the DP instead of fixing it
  in place).

**2026-07-17 (helpers 0.14):** `render_plates` joins the on_drop carve exemptions: primary placement stays a corridor candidate; the fallthrough carve is DEFERRED via `ctx.post_drain` until every corridor has drained (so it cannot preempt a sibling's reserved corner), then retries the opposite/side-view strip. Witness-hull corner overlaps are an accepted one-box-occupancy artifact pending L-shaped occupancy (#602 follow-up).
