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
- **Cross-cutting cleanup** — `render` moved out of `model/`; duplication
  consolidated (`_END_ON`/`_xyz`/leader paths); the `PartModel` is built **once**
  per drawing and threaded (#221 / #229, PR #233).
- **Turned step lengths** — engine X-chain + Z-ladder *deleted*, one IR chain
  (orientation = projected span direction). The template (#223 / #231).
- **Turned diameters** — `_annotate_turned_diameters` *deleted*;
  `annotations/turned.py` gone; ø leaders row-below (X) / column-left (Z) from the
  IR (PR #234).
- **Hole centre marks** — inline `cm_` loop *deleted*; IR `render_centermarks`
  (PR #235).
- **Envelope width/depth** — inline blocks *deleted*; the **first zone-aware** IR
  renderer (`render_envelope`, places through the engine's below-strips so it
  coordinates with un-migrated passes) (PR #236).

The mechanically-tractable migrate-and-delete work is done. **What remains needs
new IR modelling, not a mechanical loop** — each is a design epic (below).

## Remaining — design epics (need IR modelling, not loop iterations)

Ordered by value / readiness. Each needs the modelling in its **Prereq** before the
migrate-and-delete is even possible.

| Engine pass (to delete) | Lives in | Prereq (new IR modelling) | Issue |
|---|---|---|---|
| **prismatic step-height ladder** + **envelope height** + **OD** (`dim_step_*`, `_detect_step_repeat`, `_legible_steps`, `dim_height`, `dim_od`) — all coupled via the shared `fv_zones.right` ladder / `_right_ladder` cursor | orchestrator inline | a **prismatic-step `Feature`** (detection half-exists in `analyse_face_levels` → `step_zs`); rotational-OD modelling. Migrate the whole right-strip group together (zone-aware), folds in #230 (`N× rise`) + #222 (OD on profile) | NEW |
| **holes**: callouts + location dims + `n×` grouping + pitch dims + balloons (`_annotate_holes` 1063 lines, `_add_location_dims`) | `holes.py` | **location datums** + **pattern pitch** + **hole-table escalation** in the IR — the largest single piece. Centre marks already done (#235) | #220 (+ NEW for tables/location) |
| **slots** `_annotate_slots` | `holes.py` | a **slot detector** → `SlotFeature` | #199 → #206 |
| **sections / detail views** `_add_section_view`, `_add_detail_view` | `sections.py` | planner **section-trigger** modelling (which features need a section) | #207 |
| **PMI / GD&T** `_annotate_pmi` | `pmi.py` | a **PMI/thread detector** → GD&T `Feature`s | #200 → #208 |

When these land, the orchestrator's per-feature calls are gone and it reduces to
`build model → plan → render`.

## Cross-cutting (remaining)

- **Retire `render_into`'s test-only parallel** — its hole/envelope/OD capability
  is superseded *in production* as the holes + envelope-height epics land; delete
  the scaffold then so no divergent path lingers. (`render_callouts`/`render_into`
  currently drive only the seam + e2e-slice tests.)

## Definition of done (the whole ADR)

- `annotations/{holes,turned,sections,pmi}` per-feature passes deleted; the inline
  envelope/OD/centre-mark/step-ladder code gone from the orchestrator.
- One path: detectors → IR → planner → render-intents → shared layout/export.
- No `render_into` test-only parallel; no engine/IR duplication.
- Full standards + geometry suites green; X/Z parity tests per feature.
- ADR 0008 status → "migration complete; one path".
