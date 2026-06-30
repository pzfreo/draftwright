# Annotation Placement in draftwright: Diagnosis and Two Forward Paths

*A research note on the "strip allocation" layout engine — June 2026*

> **Status.** This note is the research backing for
> [ADR 0009](../adr/0009-boundary-labeling-strip-placement.md), which decides in
> favour of **Approach A** (boundary labeling — unify the strips). The migration
> plan lives in
> [`plans/strip-layout-boundary-labeling-roadmap.md`](../plans/strip-layout-boundary-labeling-roadmap.md).

## Abstract

The recurring layout defects in draftwright (#133, #225, #293, #305, and the
`*_dropped` lint family) are not independent bugs. They are symptoms of a single
structural choice: annotation placement is **imperative, per-pass, and split
across mechanisms that do not share an occupancy model**. This note maps the
current engine against the established academic literature — which turns out to
describe draftwright's problem almost exactly — and lays out the two most
credible ways forward, with honest trade-offs. The headline finding: draftwright
is solving a textbook **boundary-labeling** problem with ad-hoc heuristics, and
the literature offers both a *provably-optimal, deterministic* upgrade (keep the
strips, solve them properly) and a *higher-ceiling, higher-risk* global one
(dissolve the strips into one optimisation).

---

## 1. The current engine, briefly

draftwright places a view's annotations into **strips** (`ViewZones`: left /
right / above / below bands around each orthographic view; `_core.py:166–251`).
Two different allocation authorities run inside those strips:

- A **cursor** (`Strip.allocate`) that hands out space outward from the view
  edge, used by envelope dims, step-height ladders, and off-axis location dims.
- A **1D Cassowary solve** (`_solve_strip_1d`, kiwisolver; `layout.py:56–91`),
  wrapped by `LayoutSolver.solve_strip` and the `Placeable` protocol
  (ADR 0003), used by bore-callout and turned-diameter leaders. It pulls each
  label toward its natural position subject to bounds + min-gap, with a greedy
  fallback.

2-D placement of rigid blocks (tables, GD&T frames) is a separate exact
free-rectangle enumeration (`fit_box`, O(n³); `layout.py:275–317`). Cross-view
layout is the **compose-then-pack** outer loop (ADR 0004).

**The root defect.** The cursor and the solver are *different allocation
models that do not observe each other*, and several placers write into the
**same** strip. The code says so directly (`annotations/holes.py`):

> "The strip cursor only tracks dims it allocated; the right/below strips are
> SHARED with hole callouts (`hc_*`) and the section hatch, which use other
> placers and are invisible to the cursor (#133). So a clean allocation is
> necessary but not sufficient…"

And ADR 0003 admits the same at the architecture level:

> "Today that work is spread across four mechanisms that do not compose…
> leaders are second-class (nothing deconflicts them)… 'it fit' is decided
> greedily, so a dense sheet silently produces long or colliding leaders
> instead of escalating."

So every recent fix has been a **post-hoc patch** to an invisible-occupant
collision: #225 added "retry the next tier on collision"; #305 nudged a coaxial
callout off an extension line the placer couldn't see; #43/#41 drop features
that won't fit rather than re-plan. The placement is **single-pass with no
convergence and no shared occupancy** — exactly the conditions under which
local first-fit produces overlaps. Crucially, "which annotation gets dropped"
when a strip is full is decided by **arrival order in the code**, not priority.

**What is genuinely good** and worth preserving: determinism (stable variable
ordering → reproducible solves, per ADR 0001); the crisp 1D solver interface;
the compose-then-pack outer layer that sidesteps a global 2-D solve; explicit
drops surfaced as lint rather than silent omission; and pins/overrides (#89).

---

## 2. What the literature says (best practices)

draftwright's "strip allocation" is, precisely, **boundary labeling** (Bekos,
Kaufmann, Symvonis & Wolff, 2007): point features sit inside a rectangle, labels
are placed on 1/2/4 sides of its boundary, and each label connects to its
feature by a **leader**; the objective is short, **crossing-free** leaders. The
field's relevant results:

1. **Fix the ordering and non-overlap becomes free.** Non-overlap is
   *disjunctive* ("A left-of B **or** above B…"); a linear solver (Cassowary)
   provably **cannot** express it, which is why a global 2-D solve is hard
   (and why draftwright deferred it). Boundary labeling's escape hatch:
   make the **label order along the boundary match the feature order** — then
   leaders are automatically crossing-free and spacing is a chain of *linear*
   inequalities. *draftwright already relies on this trick in its 1-D strip
   solve; it just hasn't applied it to all strip occupants at once.*

2. **The optimal sub-problems are cheap.** For uniform/po leaders the optimal
   (min total leader length, crossing-free) assignment is a sweep in
   O(n log n); for two-sided or non-uniform labels it is a **min-cost bipartite
   matching** / **dynamic program**, O(n²)–O(n³). These are well within
   draftwright's budget (dozens of annotations).

3. **Leaders to circular features should be angled, not axial.** General
   drafting practice (and the reason #305 exists): a leader to a feature should
   be inclined > 30° to the horizontal, clear of centre-lines and other text.
   Engineering-CAD work formalises this as **dimension-set selection first,
   spatial layout second** (Dori), often with a **territory / zone** model
   (inner vs outer dimension bands) — which is what `ViewZones` already is.

4. **For the *global* problem, metaheuristics win on quality.** The canonical
   empirical study (Christensen, Marks & Shieber, 1995) shows the basic
   placement problem is **NP-hard**, and that **simulated annealing
   substantially outperforms** greedy / gradient methods on conflict-free
   placement, at the cost of run-time and a stochastic (non-deterministic)
   result. Exact non-overlap at global scale is **integer programming**
   (GRIDS; floor-layout MIP) — optimal but expensive and harder to keep
   deterministic.

The two forward paths below correspond to the two halves of finding (1): **keep
the order fixed and solve each boundary optimally**, or **relax the order and
optimise globally**.

---

## 3. Approach A — Boundary labeling done properly (keep the strips, unify the solve)

**Idea.** Stop having N placers fight over a strip. For each view, build **one**
boundary-labeling instance whose "labels" are *every* annotation that wants that
strip — bore callouts, location dims, step/turned-diameter dims, even the
section-hatch footprint as a fixed obstacle — and solve it as three phases:
**collect** (every contributor emits a *candidate*, nothing is placed) →
**solve** (one optimisation: select what fits by priority, assign side/zone,
order = feature order ⇒ crossing-free, space via the existing 1-D solver) →
**emit** (materialise the chosen geometry). Occupancy stops being "post-hoc
check + retry" because **there is one model and everything is in it**.

This is the disciplined version of the work already on the roadmap as **#150**
("consolidate 1-D placement around `LayoutSolver`"), generalised from
bore-callouts to *all* strip occupants, and it composes cleanly under the
existing **compose-then-pack** (ADR 0004) outer layer for cross-view conflicts.
It also slots onto ADR 0008's planner→intent seam: the layout stage consumes the
**full** per-strip intent set instead of each render pass committing on its own.

**Pros**
- **Attacks the root cause directly:** one occupancy model per strip → the
  invisible-occupant collision class (the source of #133/#225/#305) disappears
  by construction, not by patch.
- **Deterministic and explainable** — preserves ADR 0001; "label i sits here
  because order + min-gap + shortest-leader," not "the annealer landed there."
- **Provably optimal & fast** within a view (crossing-free, min leader length;
  O(n log n)–O(n³)).
- **Incremental & low-risk:** the strips, `Placeable`, the 1-D solver and the
  drop/escalate plumbing already exist; this is consolidation, not a rewrite.
  Matches the stated architecture (ADR 0003 §"assignment then placement").
- **Principled escalation:** "doesn't fit" becomes a first-class, priority-ranked
  signal feeding the detail-view ladder (#306/#54), instead of an arrival-order
  drop.

**Cons**
- **Per-view, not global:** optimal *within* a strip/view; cross-view and
  inner-vs-outer-zone conflicts still rely on compose-then-pack + the
  assignment layer. (Arguably correct separation of concerns, but it means A is
  not a single global optimum.)
- **Modelling friction:** heterogeneous annotations (a dim *chain*, hatching, a
  multi-segment ladder) must be coerced into the label/leader/port abstraction;
  some don't fit the "one label, one leader" mould cleanly.
- **Leader style:** the textbook results are richest for rectilinear (po/opo)
  leaders; draftwright sometimes wants **angled** leaders (the #305 fix). The
  straight-line ("s") variant exists but mixing styles weakens the optimality
  guarantees.
- **Control-flow inversion:** passes must stop calling `dwg.add(...)` mid-flight
  and instead return candidates to a single layout stage — a real refactor (the
  intent/render seam), not a patch.

---

## 4. Approach B — One global placement optimisation (dissolve the strips)

**Idea.** Realise ADR 0003's original ambition in full: every annotation on the
sheet is a `Placeable` with a small set of **candidate positions** (or
continuous DOF), and a single optimiser minimises a global objective —
overlaps, total leader length, and **soft drafting penalties** (leader < 30°,
text on a centre-line, dim inside the part, broken alignment) — across *all*
annotations and *all* views at once. Two realisations:

- **B1 — Mixed-integer programming.** Encode pairwise non-overlap with the
  classic big-M disjunction + binary "which side" variables (floor-layout /
  GRIDS formulations). Exact and globally optimal.
- **B2 — Simulated annealing / metaheuristic.** Discrete candidate positions
  per annotation; anneal over a conflict + aesthetics cost (the Christensen–
  Marks–Shieber recipe). Best empirical quality, scales to large sheets.

**Pros**
- **Structurally eliminates the root cause at the largest scope:** there is one
  model for the whole sheet, so *no* occupant is ever invisible to another —
  across placers **and** across views.
- **Highest quality ceiling:** SA empirically resolves "all or nearly all"
  conflicts; a global optimum can trade a slightly longer leader here to avoid
  a collision there — something local strip solves cannot do.
- **Extensible objective:** new ISO conventions and new annotation types become
  *penalty terms*, not new bespoke placers.
- Subsumes the inner/outer-zone and cross-view problems that Approach A leaves
  to a separate layer.

**Cons**
- **Tension with determinism (ADR 0001).** SA is stochastic; reproducibility
  needs a fixed seed and even then is brittle to input perturbation. MIP is
  deterministic but solver-version-sensitive. Golden tests were retired
  (ADR 0007), so silent output drift is *less* guarded now — a real risk.
- **Cost & complexity.** MIP non-overlap is exponential in the worst case
  (NP-hard); SA needs careful cooling/penalty tuning. Both are far heavier than
  an O(n log n) sweep for what is usually a sparse strip.
- **Explainability / debuggability.** "Why did it place the callout there?"
  has no local answer — bad for a tool whose value is trustworthy, inspectable
  drawings, and harder to lint/repair deterministically.
- **Big-bang risk.** Replaces several working, well-tested placers at once; a
  regression touches every drawing rather than one feature class.

---

## 5. Comparison and recommendation

| | **A — Boundary labeling (unify the strips)** | **B — Global optimisation (dissolve them)** |
|---|---|---|
| Fixes invisible-occupant collisions | Per view, by construction | Whole sheet, by construction |
| Determinism (ADR 0001) | ✅ preserved | ⚠️ SA stochastic / MIP solver-sensitive |
| Optimality | Provable, *within* a view | Global (B1 exact, B2 near-optimal) |
| Speed | O(n log n)–O(n³), trivial | NP-hard (B1) / tunable seconds (B2) |
| Cross-view & zone choice | Needs assignment + ADR 0004 | Built-in |
| Implementation risk | Low — consolidation of existing parts (#150) | High — replaces working placers |
| Explainability / lint / repair | High | Low |
| New annotation type | Add to the boundary model | Add a penalty term |

**Recommendation.** Pursue **Approach A first**. It is the lowest-risk move that
*actually removes the defect class* rather than patching it: one occupancy model
per strip, optimal crossing-free leaders, full determinism, and it is already
the funded direction (#150) sitting on top of the existing `Placeable` /
`LayoutSolver` / compose-then-pack scaffolding. Treat the "doesn't fit" outcome
as a first-class escalation signal into the detail-view ladder rather than a
silent drop.

Hold **Approach B in reserve.** Only reach for a global optimiser if, after A,
genuine *cross-view* or *inner-vs-outer-zone* conflicts remain that compose-
then-pack cannot resolve — and if so, prefer the **B2 (annealing)** variant with
a fixed seed and a re-introduced output-stability test, since exact MIP buys
little for sheets this sparse while costing determinism and speed. In short:
**make the strips honest before deciding whether to abolish them.**

This recommendation is adopted as [ADR 0009](../adr/0009-boundary-labeling-strip-placement.md).

---

## References

1. Bekos, Kaufmann, Symvonis, Wolff. *Boundary labeling: Models and efficient
   algorithms for rectangular maps.* Computational Geometry, 2007.
   <https://www1.pub.informatik.uni-wuerzburg.de/pub/wolff/pub/bksw-blmea-06.pdf>
2. Christensen, Marks, Shieber. *An empirical study of algorithms for
   point-feature label placement.* ACM TOG, 1995.
   <https://www.eecs.harvard.edu/shieber/Biblio/Papers/tog-final.pdf>
3. Badros, Borning, Stuckey. *The Cassowary linear arithmetic constraint solving
   algorithm.* ACM TOCHI, 2001. <http://badros.com/greg/papers/cassowary-tochi.pdf>
4. Bekos et al. *Boundary labeling with octilinear leaders.* Algorithmica.
   <https://link.springer.com/chapter/10.1007/978-3-540-69903-3_22>
5. *GRIDS: Interactive Layout Design with Integer Programming.* arXiv:2001.02921.
   <https://arxiv.org/pdf/2001.02921>
6. *Strong mixed-integer formulations for the floor layout problem.*
   arXiv:1602.07760. <https://arxiv.org/pdf/1602.07760>
7. *Intelligent Dimension Annotation in Engineering Drawings (CBR + MKD-ICP).*
   Applied Sciences, 2025. <https://www.mdpi.com/2076-3417/15/11/5992>
8. draftwright `docs/adr/0003-constraint-based-layout.md`,
   `0004-compose-then-pack-view-blocks.md`; `src/draftwright/layout.py`,
   `_core.py` (`Strip`/`ViewZones`), `annotations/holes.py`, `_common.py`.
