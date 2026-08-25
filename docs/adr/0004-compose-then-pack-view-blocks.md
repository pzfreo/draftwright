# ADR 0004 — Compose-then-pack: views as blocks carrying their annotation footprint

- **Status:** Accepted (2026-06-19; amended 2026-06-20, 2026-07-09,
  2026-07-18, 2026-08-14, 2026-08-23 and 2026-08-25 — see Amendments). **Its fixed four-view topology
  assumption is superseded by [ADR 0018](0018-requirement-driven-view-planning-and-editable-sheet-layout.md)
  (accepted 2026-08-16)**: which view blocks exist is now 0018's decision. Everything
  else here stands and is what 0018 builds on — each selected view is still composed
  with its annotation footprint before outer packing, and footprints are still page-mm
  box layouts rather than bbox-measured geometry.
- **Date:** 2026-06-19
- **Deciders:** Paul Fremantle (pzfreo)

## Context

draftwright lays out a sheet in two stages today: `_analyse` places the
orthographic views using *heuristic* space reservations (strip depths, gap
maxes, the balloon halo), then `_auto_annotate` adds the dimensions, callouts,
balloons, **and the hole table** into whatever space is left.

This ordering — **views first, annotations second** — is the root cause of a
recurring class of failures (surfaced while making dense hole patterns legible,
#111/#112):

- The hole table is dropped into a free corner *after* layout and never enters
  scale/page selection, so reserving any balloon space starves it
  (`table_dropped`).
- Balloons can only be reserved on three sides of the plan view; the fourth (the
  bottom) abuts the front view, and pushing the front view down to make room
  cascades into the table and the scale choice.
- `_will_balloon` must *predict* whether a view escalates *before* the views are
  placed — but reserving the predicted space changes the layout enough that the
  part may no longer escalate. The prediction is self-defeating on borderline
  parts.
- `lint()` measures real geometry bounding boxes at O(n²); a balloon-heavy
  drawing (NIST CTC-02, ~60 balloons) sits at the CI timeout.

Each symptom is a patch on the same flaw: annotations are second-class citizens
placed post-hoc, competing for scraps. ADR 0014 now settles how *annotations
within a view* are placed (collect-then-solve corridors; retired ADR 0003 holds
the earlier `Placeable`/Cassowary exploration). This ADR settles
the layer around it: **how a view and its full annotation set are composed into
one placeable block, and how those blocks are packed onto the page and scaled.**

## Decision

Adopt **compose-then-pack**. Each view becomes a *block* whose footprint
includes its geometry **and all of its annotations** (dimensions, callouts,
balloons) **and its furniture** (the hole table, the iso). The sheet is laid out
by composing the blocks first and packing the complete blocks second.

Two rules make it work and keep it maintainable:

1. **Lay out, don't predict.** A block's footprint is the *actual* laid-out
   extent of its annotations, not an a-priori guess. Escalation ("N callouts or
   one table") is decided by whether the per-instance layout fits, not by
   `_will_balloon`. The prediction problem disappears.
2. **Layout reasons about boxes; geometry is built last.** Annotations are laid
   out as lightweight page-mm **rectangles** in view-local coordinates; the
   packer measures the footprint from those boxes; the real OCC geometry is
   built only once, at the block's resolved page position.

### The footprint is a box layout, not measured geometry

Three footprint representations were considered:

- **Scalar per-side bands** (today's `StripDepths` / `pv_halo`) — fast but lossy;
  it cannot say "balloons here, table there," which is exactly what forced the
  prediction and the cramming.
- **Measured OCC geometry** — a single source of truth, but *slow* (the O(n²)
  `lint()` perf wall we already hit in CI) and it forces annotations to be built
  before placement, then translated.
- **A box layout (chosen)** — annotations laid out as plain page-mm rectangles.
  The block's footprint *is* that box layout; the same layout drives both the
  reservation and the final render positions (single source of truth, no drift);
  it is rectangle math (fast) and decoupled from OCC (testable in isolation).

The deciding factor is evidence, not theory: we have already hit the
measured-geometry performance wall in production. The box layout's one real risk
— box-vs-geometry drift — is controlled by making the box the **contract** the
renderer must fill at the resolved position.

### Scaling and the scale/page search

A drawing mixes two coordinate regimes that behave oppositely under scale:

- **View geometry scales** with the scale factor (a 70 mm part at 2:1 is a
  140 mm box on the page).
- **Annotations are page-mm — scale-independent** (a 3 mm dimension label, a
  9 mm balloon, the hole-table text are constant at any scale).

So a block's footprint is `(scaled geometry box) + (fixed-size annotation
boxes)`. Most of it is scale-invariant. The one scale-coupled input is *which*
annotations exist — legibility/escalation, `world_separation × scale` vs a fixed
page-mm minimum. So composition is a function of scale: `compose(view, scale)`.

This turns the current tangled fixed-point iteration (measure strips → choose
scale → re-measure, ×3) into a clean **monotone search over `(scale, page)`
candidates**:

```text
for (scale, page) in ladder:                 # largest scale first
    blocks = [compose(view, scale) for v]    # decide annotations + lay out boxes
    packed = pack(blocks, page)              # fixed projection topology + free-rect placement
    if packed fits: return (scale, page), packed
```

At a *given* scale the footprint is deterministic, so there is no circular
scale↔footprint loop to iterate — `compose(scale)` fixes the legibility and
escalation choices for that scale. The scattered estimators (`_est_*_depth`,
table size, halo) collapse into one composer per view.

**The geometry legibility floor remains advisory for an *explicit* scale (#489), but
required annotation outcomes are not (#1146).** `_MIN_VIEW_MM` (10 mm) is purely the
threshold below which an explicit scale earns a legibility *warning*. It does **not** bound
the auto scale (`choose_scale` picks by a pure geometric page fit) and does **not** gate which
annotations exist (step/location legibility use `_MIN_STEP_*`/`_MIN_LOC_SEP_MM`). Since #1146,
the completed drawing is also checked for required placement drops: the default policy retries
smaller preferred ISO 5455 scales, strict policy rejects the request, and only an explicit
permissive policy returns the incomplete scale with a warning. Thus a caller may accept small
geometry, but not silently lose manufacturing requirements. The one unconditional hard
rejection is `_MIN_RENDER_MM` (0.1 mm) — a conservative geometry floor well above where OCCT's
annotation arcs actually degenerate (`Geom_TrimmedCurve U1==U2`, ~1e-4 mm); there we raise a
clean message rather than crash.

### Relationship to retired ADR 0003 and the rejected 2D solve

- [ADR 0014](0014-collect-then-solve-annotation-placement.md) governs the
  **inner** layout: collect-then-solve annotation placement per corridor. This
  ADR governs the **outer** layout: composing view+annotation blocks and
  packing them on the page. ADR 0003 is retained only as the retired historical
  proposal that preceded these two concrete contracts.
- The **page-level packing stays fixed-topology** (plan above front sharing X;
  side beside front sharing Y; iso and table in free rectangles via
  `fit_box`). The full global 2D solve in #94 was closed as superseded and
  unnecessary: most "2D freedom" is forbidden by projection alignment, while
  a global non-overlap solve is disjunctive/NP-hard and risks nondeterminism.
  Fixed-topology + composed footprints + local free-rect placement is the
  accepted amount of structure.

### Implementation state (updated 2026-07-09)

> **Amended 2026-06-20:** the original "byte-identical for unaffected drawings"
> discipline is **dropped** — see the Amendment section below.

The core "automatic layout has one authority" tranche is now implemented:

1. **Done** — `ViewBlock` data model + block-driven view placement.
2. **Done** — page/scale choice and repack share one composed-footprint fitness
   model (#519).
3. **Done** — section A-A participates in scale/layout selection instead of
   being a fixed-offset afterthought (#515).
4. **Done** — furniture reserves/checks full rendered footprints rather than
   label boxes only (#518, #540).
5. **Done** — hole/data tables and balloon rings are layout-aware escalation
   outputs instead of first-fit drops (#516, #517).
6. **Done** — below/right corridor ladders that can be independent candidates
   join the shared corridor solve; correlated ladder renderers remain specialized
   where each rung depends on the previous placed tier (#477).
7. **Done** — user edits can enter the same solve as pinned, priority-ranked
   candidates instead of late fixed-position moves (#511).
8. **Done** — measure/repack iterates to a fixed point rather than relying on a
   single measured pass (#302).
9. **Done** — estimated and measured FV/PV block placement use the same
   `ViewBlock` gap semantics; the old estimator-only plan-view halo lift is gone
   (#112).

The standing acceptance test is still the **dense-ballooning hard case** (NIST
CTC-02). **Specifically: the plan view's labels (balloons) must not overlap
front-view dimensions** — the inter-view annotation overlap that motivates this
ADR. Output *is allowed to change* (see Amendment); correctness is judged by
overlaps-gone + lint-clean, not by golden/geometry stability.

The remaining layout work is no longer the main compose-then-pack migration. It
is hardening and second-order product work:

- place pitch-dim labels clear of vertical centerlines at creation time (#129);
- add stronger property/fuzz coverage for layout-cleanliness invariants (#301);
- advance detail-view page/scale participation (#306/#54/#444);
- carry remaining manual intent verbs through the corridor solve (#426);
- revisit known heuristic limits in the strip DP and narrow residual collision
  edge cases (#381, #303, #366, #367, #443).

## Consequences

**Positive**
- The recurring failures dissolve by construction: no post-hoc table cramming,
  no self-defeating prediction, no balloon-vs-table competition. The bottom band
  is just "the plan block's bottom footprint," and the packer places it.
- The page-level "front view down or plan view up?" decision moves *into the
  packer* — we stop hand-writing `PV_Y = FV_Y + …`.
- Performance recovers: layout is rectangle math; OCC geometry is built once at
  the end, not measured during the search.
- The scattered estimators and the 3× measure/choose iteration collapse into one
  `compose(view, scale)` + one scale/page search — fewer mechanisms, more
  testable.

**Negative / costs**
- A box model must be maintained for every annotation type (its page-mm size),
  and the renderer must honour the reserved box. Drift is the failure mode to
  guard against in tests.
- Multi-PR migration with a real risk of a half-composed engine running two
  models at once; mitigated by the byte-identical-per-step discipline and the
  golden suite.
- `compose(view, scale)` must run the *real* legibility/escalation logic (not
  estimate) to be correct — this is where the substance moves, and it must stay
  fast (box math) to preserve the performance win.

## Amendment (2026-06-20) — accepted; byte-identity dropped

Acting on this ADR exposed that holding step 1 (`ViewBlock` foundation, 4a/4b)
**byte-identical** is exactly why nothing improved: a byte-identical composer just
re-expresses the old scalar reservation, so the front-dim/plan-label overlap
persists by construction. Chasing the overlap with post-hoc balloon-placement
tweaks (push the ring out, stack beyond dims) only relocates the collision —
confirming the ADR's thesis that the fix is the *reorder*, not placement.

Decisions:

1. **Status → Accepted.** Build steps 2–5 of the Roadmap for real.
2. **Drop the "byte-identical per step" rule.** Output *will* change on many
   drawings, by design. The golden/geometry suite is no longer the contract;
   it is updated to the new, correct output. *(Further amended 2026-06-28: the
   golden harness `tests/test_golden.py` has since been removed entirely — see
   ADR 0005 §3's retirement note. With output evolving by design, a byte-exact
   digest was friction without signal; the geometry-level and `test_e2e_standards`
   suites carry regression coverage.)*
3. **Acceptance test = the inter-view overlap.** A step is correct when
   **plan-view labels do not overlap front-view dimensions** (and lint is clean)
   on the hard case (NIST CTC-02), *not* when output is unchanged.
4. **Footprint stays a box layout** (page-mm rectangles via the ADR-0014 inner
   layout in view-local space) — never bbox-measured OCC geometry (the O(n²)
   `lint()` wall, ~110 s on CTC-02, is the standing evidence).
5. **Tracked as #121.** The root cause is "annotations placed *after* views are
   positioned, into shared corridors." The fix order is: **compose** each view
   as `view_rect(scale) + its annotation boxes` → **size** via the monotone
   `(scale, page)` search → **pack** blocks **disjoint** (one block's annotations
   cannot enter another block's rectangle) → build geometry once → repair only as
   a safety net. Scale/page is the outer search; compose-then-pack is its
   fitness function. If even A0 at the smallest standard scale will not hold the
   packed blocks, **suggest a larger page / smaller scale** rather than cram.

## Module homes (forward note — ADR 0005, in progress)

This ADR names its anchors by their *current* location: `_analyse`,
`StripDepths`, `ViewBlock`, `_repack`, `choose_scale` in `make_drawing.py`, and
`_auto_annotate` in `annotate.py`. [ADR 0005](0005-pipeline-architecture-and-state-ownership.md)
(Accepted; the split is complete) relocated these **without changing this decision** —
sheet planning/compose-then-pack to `sheet.py` (#162; since renamed `compose.py`, #640), projection/`_assemble` to
`projection.py` (#161) / `builder.py` (#165), `_analyse` to `analysis.py` (#163),
annotation sequencing to `annotations/orchestrator.py` (#164). Refresh the anchor
names above as each phase lands (roadmap: `docs/plans/138-module-split-roadmap.md`).
The compose-then-pack model, the box-layout footprint, and the monotone
`(scale, page)` search are unaffected.

## Amendment (2026-07-09) — implementation status refreshed

PRs through #544 complete the original layout-authority migration. The live
engine no longer treats plan-view balloon headroom as an estimator-only exception:
both the estimated path and the measured/repack path center and pack FV/PV from
the same composed `ViewBlock` bands. That closes the architecture gap tracked in
#112.

This does **not** mean layout is globally solved in two dimensions. The outer
layout remains deliberately fixed-topology: projection-aligned orthographic
blocks, free-rectangle placement for furniture, and a fixed-point measure/repack
loop. That is the intended architecture from this ADR, not a temporary halfway
state. Remaining open issues are targeted hardening, coverage, detail-view
capability, and manual-intent integration work.

## Amendment (2026-07-18) — reconciling the absolute wording with the as-built loop

Two of this ADR's absolutes read stricter than the accepted implementation; the
code is right, and the text is hereby reconciled rather than left to mislead:

1. **"The prediction problem disappears" (Decision rule 1) — prediction survives
   as the pass-1 seed.** The composer's scale/page fitness still runs a-priori
   estimators (`_will_balloon`, the `_est_*_depth` family in `compose.py`) to
   seed the first compose; the **measure-and-repack fixed-point loop**
   (`builder._repack_to_fixed_point`) is the corrective that makes the final
   footprint the *actual* laid-out extent. Escalation authority did move to the
   per-instance layout (the 2026-07-09 amendment); the estimators remain as
   seeds, backstopped — not decision-makers. Retiring them entirely is possible
   future convergence, not the current state.
2. **"Geometry is built only once" / "never bbox-measured OCC geometry" — the
   search is box-math; the measured pass reads built annotations.** The
   `(scale, page)` *search* reasons purely over page-mm boxes (the O(n²)
   perf motive this rule encodes). After a full build, the bounded repack loop
   (≤ `_REPACK_MAX_ITER` extra `_assemble` runs) and the out-of-bounds check
   (`_annotations_out_of_bounds`) deliberately measure the *built* annotations'
   real bounding boxes — O(n) per bounded iteration, matching what lint tests.
   The rule constrains the fitness search, not the post-build verification.

## Amendment (2026-08-14) — explicit scale cannot silently lose requirements (#1146)

ADR 0004 originally said an explicit scale below the advisory geometry-legibility floor is
honoured because the caller has accepted a cramped drawing. That remains true for geometry:
an explicit scale may make a view small and earn a warning without being vetoed. It is not,
however, authority to discard manufacturing requirements. A drawing that silently loses a
required dimension, callout, GD&T frame, or authored table is incomplete rather than merely
cramped.

The explicit-scale contract is therefore amended as follows:

1. A completed drawing is checked for required placement outcomes, including the measured
   footprint of authored sheet tables. Pinning, priority, or informational diagnostic severity
   cannot hide a displaced requirement.
2. The default policy retries smaller preferred ISO 5455 scales and selects the largest complete
   candidate at or below the requested scale. Strict policy rejects an incomplete requested
   scale; permissive policy returns it only with an explicit warning.
3. The decision remains inspectable through the drawing's structured scale-decision record,
   including requested/effective scale, attempted candidates, and stable blocker identities.
4. Trial builds reuse imported, classified, recognised, and critique inventories. This keeps the
   policy search bounded and preserves the ADR's box-math performance rationale; discarded
   trials are not independent full recognition pipelines.
5. Automatic page selection is deliberately more conservative than an explicitly forced page.
   Required authored-table footprints are reserved before the flexible, orientation-only iso and
   the candidate must retain the iso fitness budget. This may select a larger standard page even
   when a caller-forced smaller page can place every requirement by fitting furniture around the
   rendered iso. The forced-page build is complete and strict policy therefore honours it; the
   automatic search is not a “smallest page on which lint happens to be clean” optimiser.

The unconditional `_MIN_RENDER_MM` safety floor and the advisory `_MIN_VIEW_MM` warning retain
their prior meanings. This amendment narrows only the old “honour explicit scale” statement:
caller-accepted geometric cramping is permitted, silent semantic incompleteness is not.

## Amendment (2026-08-23) — bounded semantic correction after a recovery detail (#1155)

The compose-time box estimate can conservatively reserve an automatic detail for a crowded
dimension run even though a larger preferred scale would make that run legible inline. A
finished automatic drawing that actually contains such a recovery detail may therefore trigger
a bounded trial of larger preferred scales.

This is post-build verification, not a second occupancy-based fitness function:

1. the trigger is the semantic recovery-detail artifact itself; union area, OCC bounding-box
   occupancy, page-size literals, and density thresholds are not scale-selection inputs;
2. every candidate is confined to the settled page and arrangement and is compiled through the
   ordinary compose/placement pipeline;
3. a candidate wins only if the recovery detail disappears and a single lint pass proves no
   structural error or required placement loss;
4. the ladder has a hard two-candidate work budget. Only the known geometry-degeneration floor
   and OCC failures reject a speculative candidate; unrelated `ValueError`s remain visible; and
5. every attempted scale, page, view set, reason, rejection, and winner is recorded in the
   structured scale decision. If no candidate passes, the original drawing remains the result.

The box-math search and bounded measured-repack loop remain the layout authorities. This
amendment permits a bounded semantic corrective trial around a known conservative recovery artifact;
it does not license arbitrary post-build bbox optimisation.

## Amendment (2026-08-25) — the sheet is not the first lever (#1338)

When an automatic plan is incomplete — an axial-coverage gap, or a required annotation
outcome dropped — the bounded larger-scale trial on the **already-selected page** now runs
**before** the optional ISO is removed and before any larger sheet is tried. It is the same
bounded trial the 2026-08-23 amendment introduced for a recovery detail, and it is now
literally the same helper; only the trigger and the gate set differ.

The order matters because the previous recovery sequence — drop the optional ISO, then
escalate the sheet — could return a candidate strictly worse than one it never reached.
GRM-03 (28.7 × 10 × 10 mm) settled on 5:1/A3 *without* its ISO while 5:1/**A4** is clean
*with* it; the synthetic five-step profile behind #1299 did the same.

The constraints of the 2026-08-23 amendment carry over unchanged: the candidate is confined
to the settled page and arrangement, is compiled through the ordinary pipeline, shares the
hard two-candidate work budget, wins only by passing the same structural, required-outcome
and (when the failure was axial) axial-coverage gates the larger sheet would have had to
pass, and every attempt, rejection and winner is recorded in the structured scale decision.
An explicitly requested page pins the **sheet**, not the scale, so the trial still applies
there — it fills the sheet the caller chose instead of returning an incomplete layout on it.

What this amendment does **not** settle: raising the drawing scale is still the only lever
the ladder has for a placement shortage, and scale is a statement about the part, not a
layout knob. Text height is fixed in page mm, so every step up this ladder buys annotation
room by shrinking the text relative to the geometry. Bounding what a single dropped
annotation may buy in scale and sheet size is open in #1336.
