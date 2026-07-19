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

- **Inner** (collect-then-solve, ADR 0014): a dependency-free weighted-median
  PAVA solver places labels in deterministic 1-D corridors; `fit_box` handles
  bounded free-rectangle furniture placement. ADR 0003 is retired history.
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
  layout, tables, sections, projection, export stay shared — **through an IR-typed
  interface: no recognition objects cross the boundary** (Amendment 6). The IR is
  the single representation downstream of detection.
- **One feature inventory per build** (Amendment 5) — detect once.
- **Deterministic generation — no model in the pipeline** (ADR 0001).
- **Lint is the single correctness judge** (no standing golden gate).

## Current gaps

Where the codebase is **today** vs the target above (as of 2026-06-29). The
architecture and pipeline exist and are load-bearing in production; convergence is
partial. Tracked under epic [#195](https://github.com/pzfreo/draftwright/issues/195).

**On the IR path in production (migrated + engine recognition/placement deleted):**
turned step lengths, turned diameters, hole centre marks, envelope width/depth,
slots, hole **location dims** (`_add_location_dims` deleted, #256), and the full
hole **callout** pass — grouping by machining spec (#257), the callout spec
(#259), the callout loop (#260), and the **IR-typed interface** (#263, ADR
Amendment 6: cover/table, furniture, placement, and the section trigger all consume
IR data — no recogniser `Hole`/`Pattern` object crosses into the renderers). The
**section A–A trigger** is planner-decided too (`plan_sections`, #207); the cut /
hatch / arrow rendering stays shared infrastructure it feeds. `annotations/turned.py`
and the `render_into` test-only parallel are gone. The prismatic step-height ladder,
overall height, and the rotational OD/centreline/bore furniture are on the IR too
(`StepLevelFeature`/`RotationalFeature` + `render_height_ladder`/`render_rotational`,
#237) — the orchestrator's inline `_right_ladder` block is deleted. Finally, **PMI/GD&T**
is on the IR (`PmiFeature` + `render_pmi`, #208): `extract_pmi`'s AP242 records are
re-homed as features and rendered directly (PMI is pre-authored, so it bypasses the
planner — empty `parameters()`); `_annotate_pmi`/`annotations/pmi.py` are deleted.

**Migration complete (2026-06-30).** Every feature pass is on the IR; no engine
feature pass remains. The orchestrator is `build model → plan → render` + the shared
section/table/PMI rendering it feeds. Remaining open items are *enhancements*, not
migrations: #230 (turned `N×`-rise), #222 (OD on the profile view), #279 (phantom ø0).

**Foundation track — ✅ complete** (umbrella
[#241](https://github.com/pzfreo/draftwright/issues/241); ADR Amendment 5):

- ✅ **One feature inventory** (keystone,
  [#244](https://github.com/pzfreo/draftwright/issues/244), done via #246/#247;
  bosses threaded too in #264). `_analyse` detects holes/patterns/bosses/turned-steps
  **once**; `build_part_model` and `Drawing.lint()` consume its results — each
  detector runs once per build, zero extra in lint.
- ✅ **Docs/comment sweep** ([#248](https://github.com/pzfreo/draftwright/issues/248),
  PR #253).
- ✅ **Annotation-ownership accessor**
  ([#249](https://github.com/pzfreo/draftwright/issues/249), PR #254) — production no
  longer reads `dwg._named`/`_anno_view` directly; registry-backed accessors +
  mutation API.
- ✅ **Planner render-intents**
  ([#250](https://github.com/pzfreo/draftwright/issues/250), PR #255) — model-level
  suppression moved into the planner; `datum` slot added (consumed by #238).
- ✅ **Delete `render_into`**
  ([#251](https://github.com/pzfreo/draftwright/issues/251)) — the test-only parallel
  (`render_into`/`render_callouts` + their leader helpers) is removed; the seam/e2e
  tests are repointed at the production renderers.
- ✅ **IR-typed interface** ([#263](https://github.com/pzfreo/draftwright/issues/263),
  ADR Amendment 6) — the data crossing IR→shared-infra is IR-typed (a `HoleRef`
  position key), not recogniser objects; enforced by typed signatures.

**Inherent (not a migration gap):** recognition is heuristic — a chamfered bore in
a tapered section will not recognise itself cleanly. The architecture *contains*
that mess inside detectors behind the IR; it does not eliminate it.
