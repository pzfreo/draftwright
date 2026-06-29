# ADR 0008 convergence roadmap — one path, strangler migration

The plan of record for ADR 0008 (see Amendment 3). Supersedes the
reproduce-and-swap roadmap (`0008-compiler-migration-roadmap.md`, retired).

**Goal:** one feature→dimensioning path —
`detectors → IR → planner → render-intents → [shared layout/projection/export]` —
with the per-feature engine passes **deleted**. The orchestrator ends as
`build model → plan → render`.

## The discipline (non-negotiable)

Every migration PR must:

1. **Delete the engine pass it replaces** (or the migration is not done — adding a
   parallel IR renderer is the divergence smell). Convergence is measured by engine
   code *removed*, not IR code added.
2. **Be judged by correctness, not equivalence** — lint-clean (drawing-derived,
   #218/#219) + ISO/ASME + coverage-complete + visual sanity. Output may differ
   from / improve on the engine. No standing equivalence gate.
3. **Hold X/Z (and view) parity by construction** — orientation is data
   (`Feature.frame` / projected span direction), never an axis branch in the
   back-end. Asserted per migrated feature.
4. Keep `ruff`/`format`/`mypy` and the full fast suite green.

## Done

- IR + planner + detectors + renderer seam + lint-as-correctness-judge
  (#194, #211, #212, #213, #218, #219).
- **Turned step lengths** — engine X-chain + Z-ladder *deleted*, replaced by one
  IR chain (orientation = projected span direction). The template (#223 / #231).

## The convergence backlog (engine passes still to migrate + delete)

Ordered worst-handled-first (most value / de-risk first). Each row is one (or a
few) migrate-and-delete PRs.

| # | Engine pass (to delete) | Lives in | Migrates to | Notes |
|---|---|---|---|---|
| 1 | turned **diameters** `_annotate_turned_diameters` | `turned.py` | `StepFeature`/`BossFeature` diameter render | pairs naturally with the step-length chain already migrated |
| 2 | **holes**: callouts + centre marks + location dims + `n×` grouping (`_annotate_holes`, `_add_location_dims`, inline `cm_`) | `holes.py`, orchestrator | `HoleFeature`/`PatternFeature` + planner location rule + render (folds in #220) | the largest pass; needs datum/location modelling in the IR. **Migrate-and-delete, not the `render_into` parallel.** |
| 3 | **envelope** dims (`dim_width`/`dim_height`/`dim_depth`) + **OD** (`dim_od`) | orchestrator inline | `EnvelopeFeature` (exists) + planner/render | partly prototyped in `render_into`; wire to production and delete the inline code. Fixes #222 (rotational OD on profile). |
| 4 | **prismatic step-height ladder** (`dim_step_*`, `_detect_step_repeat`, `_legible_steps`) | orchestrator inline | a prismatic step `Feature` + planner rule | the last turned/stepped remnant; reunifies with the step chain. Folds in #230 (`N× rise`). |
| 5 | **slots** `_annotate_slots` | `holes.py` | `SlotFeature` + planner/render | needs the slot detector (#199) → render (#206). |
| 6 | **sections / detail views** `_add_section_view`, `_add_detail_view` | `sections.py` | planner-triggered from features needing them | #207; the planner decides when a feature needs a section. |
| 7 | **PMI / GD&T** `_annotate_pmi` | `pmi.py` | thread/GD&T `Feature`s + planner/render | needs the PMI detector (#200) → placement (#208). |

When rows 1–7 land, the orchestrator's per-feature calls are gone and it reduces to
`build model → plan → render`.

## Cross-cutting (do alongside, not after)

- **#221** — move `render` out of `model/` + consolidate the duplication that
  accreted (the two leader paths, `_END_ON`, `_pt`/`_loc_xyz`, drawing-introspection
  helpers). Do early — before more renderers pile on.
- **#229** — build the `PartModel` once per drawing and thread it (the orchestrator
  currently rebuilds it); kills per-pass recompute as more passes consume the IR.
- **Retire `render_into`'s test-only parallel** — its hole/envelope/OD capability is
  superseded by rows 2–3 *in production*; once those land, delete the scaffold so no
  divergent path lingers.

## Definition of done (the whole ADR)

- `annotations/{holes,turned,sections,pmi}` per-feature passes deleted; the inline
  envelope/OD/centre-mark/step-ladder code gone from the orchestrator.
- One path: detectors → IR → planner → render-intents → shared layout/export.
- No `render_into` test-only parallel; no engine/IR duplication.
- Full standards + geometry suites green; X/Z parity tests per feature.
- ADR 0008 status → "migration complete; one path".
