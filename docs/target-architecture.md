# Target Architecture

draftwright is a **part-drawing compiler**: it turns a build123d B-rep solid into a
fully-annotated, standards-compliant multi-view technical drawing —
**deterministically** (no model in the loop) and **verifiably**. The shape is a
compiler hourglass: many feature front-ends → one narrow intermediate
representation → many dimensioning back-ends, all judged by one correctness check.

This is the *target* state defined by [ADR 0008](adr/0008-unified-feature-model-and-dimensioning-planner.md)
(+ Amendments 1–5). For where the codebase is **today** vs this target, see
[Current gaps](#current-gaps); for the migration plan, see
[`plans/0008-convergence-roadmap.md`](plans/0008-convergence-roadmap.md).

## The pipeline

```
 Solid
   │  geometry: faces · edges · cylinders · silhouettes (scanned once)
   ▼
 Detectors ── recognise holes / steps / bosses / patterns / slots / envelope
   │          → typed Feature objects
   ▼
┌──────────────────────────────────────────────┐
│  IR · PartModel   ── THE ONE INVENTORY        │   ← detect once, consume thrice
│  Features + DimParameters + datums + frame    │
└──────────────────────────────────────────────┘
   │            ╲ (same inventory)
   ▼             ╲
 Planner          ╲──────────────► Verification (linting + scoring) ⟲ repair
   │  one rule set → a DimensionGroup (view · datum · convention) per feature
   ▼
 Renderers ── groups → placed callouts / dims / leaders / centre marks
   │
   ▼
 Shared infrastructure ── layout · projection · tables · sections · export
   │
   ▼
 Drawing  (SVG / DXF / PDF)
```

The **same `PartModel` inventory** feeds the planner, the renderers, *and*
verification — detection happens once, three consumers read it.

## Label, annotation & dimension creation

- **Detect → IR.** Each `Feature` exposes `DimParameter`s: a *kind*
  (diameter / length / depth …) + a semantic *role* (bore, counterbore, step, od,
  slot-width …), a model-space span, and datums. **No baked label** — GD&T symbols
  (⌴ ⌵ ↧) are drawn as *geometry*, so the IR carries meaning, not glyphs.
- **Plan.** One rule set maps `(role, kind)` → a convention (chain · ordinate ·
  leader · pitch), with **view & datum chosen geometrically** from the feature
  frame — so X- and Z-oriented parts flow through the *same* path. Output: one
  `DimensionGroup` per feature (a compound callout stays together).
- **Render.** Groups become placed `HoleCallout` / `Dimension` / `Leader` /
  `CenterMark` via the helper primitives, allocating from the shared layout.
- **Open/Closed.** A new shape = a new `Feature` type + a detector exposing
  *existing* `DimParameter` kinds → **zero planner/layout change**.

## Layout

- **Inner** (constraint-based, ADR 0003): a 1-D Cassowary strip solver spreads a
  view's labels; a 2-D free-rectangle placer fits boxes in the view's zones.
- **Outer** (compose-then-pack, ADR 0004): each view is a *block* = projected
  geometry + its annotation boxes; `(scale, page)` is chosen by a monotone search
  that packs the blocks **disjoint** — so cross-view overlap cannot occur.
- **Zone strips** (above/below/left/right of each view) coordinate placement;
  renderers allocate from them, so a migrated renderer shares the strip and never
  collides with an un-migrated one.
- **Deterministic.** Layout depends on measured text width, so fonts are
  **bundled & path-pinned** (IBM Plex, ADR 0006) for identical output on every
  platform.

## Scoring

- One call (`lint_summary`) → `score ∈ [0, 1]` (clean = 1.0; −0.2 per error,
  −0.05 per warning), `passed` (no errors), `by_code` counts, and a
  `geometry_issues` tally (standards vs pure layout).
- A **single non-interactive signal**: a script — or an LLM driving the API — can
  gate and optimise *without* rendering the SVG.
- The bar is **correctness, not byte-equivalence** to any prior output — the
  drawing may legitimately improve.

## Verification — linting is the single judge

(ADR 0002 / 0007.)

- **Structural:** annotation overlap, out-of-bounds, label-vs-measured mismatch.
- **Coverage** (read from the *drawing* against the one inventory): every detected
  feature **dimensioned**, **located**, **centre-marked**; turned steps' lengths
  present. Lint checks the pipeline's output against the pipeline's own input.
- **Standards:** ISO/ASME conventions & legibility gates.
- **lint → repair** loop: deterministic re-placement is a *safety net*, not the
  primary placement mechanism.

## Why this approach — vs the alternatives

| Approach | Pros | Cons |
|---|---|---|
| **Compiler IR + planner + lint** *(this)* | Open/Closed — Nth shape costs the same as the 3rd; orientation is data, not branches; one inventory feeds dims *and* verification; fully deterministic & machine-verifiable; output free to improve. | Recognition stays heuristic (B-rep); convention rules are hand-written; one-time migration cost off the accreted engine. |
| **Accreted per-feature passes** *(the prior engine)* | Fast to add the first few features; nothing to design up front. | N×M coupling of recognisers × passes; orientation `if`-branches multiply; duplicate recognisers diverge → "ball of mud". |
| **Reproduce-and-swap under a byte / golden gate** | Guarantees the new path matches the old exactly. | Freezes the old engine's quirks bug-for-bug; parity-first / value-last; *forbids* the improvement that motivates the rewrite. |
| **ML / LLM end-to-end dimensioning** | Tolerates messy or ambiguous intent; little explicit rule-writing. | Non-deterministic; hard to guarantee standards; no audit trail; can't be unit-tested or repaired predictably. |
| **Hand-authored template / DSL per part** | Total control over each drawing. | Manual per part — not automated generation; doesn't scale to arbitrary solids. |

## Load-bearing principles

- **Orientation is data, not branches** — X/Y/Z/turned/prismatic are inputs
  (`Feature.frame`), never code paths.
- **Correctness, not equivalence** — judged by lint/standards, not byte-identity.
- **The IR feeds shared infrastructure; it doesn't reabsorb it** (Amendment 4) —
  layout, tables, sections, projection, export stay shared.
- **One feature inventory per build** (Amendment 5) — detect once.
- **Deterministic generation — no model in the pipeline** (ADR 0001).
- **Lint is the single correctness judge** (no standing golden gate).

## Current gaps

Where the codebase is **today** vs the target above (as of 2026-06-29). The
architecture and pipeline exist and are load-bearing in production; convergence is
partial. Tracked under epic [#195](https://github.com/pzfreo/draftwright/issues/195).

**On the IR path in production (migrated + engine code deleted):**
turned step lengths, turned diameters, hole centre marks, envelope width/depth,
slots. Each was migrate-and-delete; `annotations/turned.py` is gone.

**Still produced by the legacy engine passes (not yet migrated):**

- **Hole callouts + location dims + `n×` grouping + pitch + balloons + table
  escalation** (`annotations/holes.py`) — the largest pass; needs location-datum
  modelling in the IR. ([#238](https://github.com/pzfreo/draftwright/issues/238))
- **Prismatic step-height ladder + envelope height + OD** — coupled via the shared
  right-strip cursor; needs a prismatic-step `Feature`.
  ([#237](https://github.com/pzfreo/draftwright/issues/237))
- **Section / detail views** — the *trigger* needs to move into the planner; the
  rendering machinery stays shared. ([#207](https://github.com/pzfreo/draftwright/issues/207))
- **PMI / GD&T placement** — needs a PMI/thread detector emitting GD&T `Feature`s.
  ([#208](https://github.com/pzfreo/draftwright/issues/208))

**Foundation gaps to close before the remaining epics** (review
[#241](https://github.com/pzfreo/draftwright/issues/241)):

- **One inventory not yet realised** *(keystone,
  [#244](https://github.com/pzfreo/draftwright/issues/244)).* Feature detection
  currently runs in **three** places — `_analyse()`, `build_part_model()`, and
  linting — so some features are detected 3–4× per build, and `a.slots` is now
  computed with no reader. The `PartModel` must become the single inventory all
  three consume. This is the prerequisite for #237/#238.
- **Private `Drawing` state still read across production** (`_named`,
  `_anno_view`, …) — needs a registry-backed accessor so the state-bus surface
  stops widening.
- **The planner is still thin** — suppression / view / datum / grouping decisions
  largely live in the renderers; they should become explicit planner render-intents
  (without absorbing layout).
- **`render_into` is a test-only parallel path** — superseded in production by the
  per-feature renderers; delete once the holes epic lands.

**Inherent (not a migration gap):** recognition is heuristic — a chamfered bore in
a tapered section will not recognise itself cleanly. The architecture *contains*
that mess inside detectors behind the IR; it does not eliminate it.
