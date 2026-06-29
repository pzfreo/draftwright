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
- **Slots** — `SlotFeature` + `render_slots`; engine `_annotate_slots` *deleted*;
  a genuinely new feature type end-to-end (PR #242).
- **Hole location dims** — `_add_location_dims` *deleted*; the default min-corner
  `Datum` + `plan_locations` (intent) + `render_locations` (tier/legibility/zone
  layout). First consumer of the #250 datum slot (#238 part 1, PR #256).
- **IR hole grouping** — `HoleFeature.members` + `count`; `build_part_model` groups
  un-patterned holes by `HoleSpec` (#238, PR #257).
- **Hole callouts** — the engine's spec/grouping logic *deleted*; the callout is
  built from the IR's `hole_callout_spec` (B1, PR #259) and the callout loop is
  driven by the IR groups (B2, PR #260). `_annotate_holes` is now placement-only,
  fed by the IR.
- **IR-typed interface** (ADR Amendment 6, #263) — cover/table (B3.1 #266),
  furniture + projector (B3.2 #267), placement geometry (B3.3 #268), and the last
  `a.holes` read (B3.4 #269) all go IR-typed: `_annotate_holes` has **zero**
  recogniser `Hole`/`Pattern` references; the only recognition-derived input is a
  `HoleRef` position-key set.
- **Section A–A trigger** — `plan_sections(model, feature_keys)` decides the trigger
  + cut-plane row; `_add_section_view` is the shared rendering it feeds (#207, PR #271).
- **Bosses detected once** — threaded through the one inventory; `find_bosses` runs
  once per build, closing the #244 residual (#264, PR #272).

**The ADR-0008 convergence is nearly complete.** The holes pass, sections, turned
parts, slots, centre marks, envelope, the prismatic step-ladder + height + rotational
OD/bore furniture (#237), and the foundation track are all on the IR. **The only
remaining feature epic is PMI/GD&T (#208)**, which needs a new PMI/thread detector.

## Foundation hardening — ✅ complete (ADR 0008 Amendment 5, umbrella #241)

A mid-migration review (#241) found the foundation must catch up before the last
epics, because the IR is now load-bearing for the production passes. Each item is a
discrete sub-issue under #241; **all are done** (umbrella #241 closed):

1. ✅ **Unify the feature inventory** — *keystone* (#244: PR #246 build-time, #247
   lint; bosses threaded in #264). `_analyse` detects holes/patterns/bosses/turned-
   steps once; `build_part_model` + `Drawing.lint()` consume its products.
2. ✅ **Docs/comment sweep** (#248, PR #253).
3. ✅ **Annotation-ownership accessor** (#249, PR #254) — registry-backed
   `iter_annotations`/`view_of`/`annotations_in_view` + `replace_object`/`snapshot`/
   `restore`; no production code reads `dwg._named`/`_anno_view` directly.
4. ✅ **Planner render-intent increment** (#250, PR #255) — `PlannedDimension` carries
   `suppressed`/`reason` (model-level suppression moved into the planner) + a `datum`
   slot (consumed by #238 location dims).
5. ✅ **Delete `render_into`** (#251, PR #270) — the test-only parallel
   (`render_into`/`render_callouts` + their leader helpers) is removed; the seam +
   e2e-slice tests are repointed at the production renderers.
6. ✅ **IR-typed interface** (#263, PR #266–#269) — ADR Amendment 6: the data
   crossing IR→shared-infra is a `HoleRef` key, not recogniser objects.

## Remaining — feature epics (need IR modelling; AFTER the foundation track)

Ordered by value / readiness. Each needs the **Prereq** modelling before the
migrate-and-delete is possible. (Slots, turned diameters/lengths, centre marks, and
envelope width/depth are already done — see [Done](#done).)

| Issue | Engine pass (to delete) | Prereq (new IR modelling) | Priority |
|---|---|---|---|
| ~~**#238**~~ ✅ | **holes** — callouts + location dims + grouping + pitch/furniture + cover/table | **done** — fully on the IR (location dims #256, grouping #257, callout spec #259, callout loop #260, IR-typed interface #263). `_annotate_holes` is placement-only, fed by the IR | done |
| ~~**#207**~~ ✅ | **section view** trigger | **done** — `plan_sections(model, feature_keys)` decides the A–A trigger + cut-plane row from the IR; `_add_section_view` is the shared rendering it feeds. Detail view stays user/lint-triggered | done |
| **#200 → #208** | **PMI / GD&T** (`_annotate_pmi`) | a **PMI/thread detector** → GD&T `Feature`s | medium |
| ~~**#237**~~ ✅ | **prismatic step-ladder + height + OD/centreline/bore** | **done** (PR #280) — `StepLevelFeature` + `RotationalFeature` + `render_height_ladder`/`render_rotational`; the inline `_right_ladder` block is deleted. #230 (turned `N×`-rise) + #222 (OD on profile) remain as deferred *enhancements*; #279 (phantom ø0) filed |

When these land, the orchestrator's per-feature calls are gone and it reduces to
`build model → plan → render`.

## #238 holes — done (how it landed)

The largest, most-coupled pass, migrated in sequenced behaviour-equivalent PRs (each
with a dense-part visual gate). The placement / balloon / hole-table machinery stayed
**shared infrastructure** (Amendment 4); the recognition→intent logic moved to the IR
and the interface across the boundary was made IR-typed (Amendment 6):

- **B1** (PR #259) — callout built from the IR's `hole_callout_spec` via a shared
  `callout_from_spec`; `_build_callout` deleted. (Found+fixed #261: `HoleCallout`
  renders a float wider than the `_fmt` string — the IR stays float-clean, the
  renderer formats.)
- **B2** (PR #260) — the IR is the single grouping authority; `HoleSpec` grouping +
  `_subspecs` deleted; `_annotate_holes` iterates the IR groups.
- **B3 — IR-typed interface** (#263, Amendment 6): cover-by-location (B3.1 #266),
  furniture + projector from `PatternFeature` (B3.2 #267), placement on IR geometry
  (B3.3 #268), and the last `a.holes` read removed (B3.4 #269). `_annotate_holes`
  now has **zero** recogniser `Hole`/`Pattern` references — fed by the IR + a
  `HoleRef` position-key set.

## Cross-cutting

- ✅ **Retire `render_into`'s test-only parallel** (#251, PR #270) — removed; the
  seam + e2e-slice tests are repointed at the production renderers. No divergent path.

## The IR / infrastructure boundary (ADR 0008 Amendment 4)

Migrate the **feature→dimension-intent** logic only. The **shared infrastructure**
stays — the IR *feeds* it, never reabsorbs it:

- **IR path (migrate + delete):** detectors, `Feature`s/`DimParameter`s, planner
  convention rules, render intent (which callout/dim, which view, which datum).
- **Shared infra (keep; the renderer calls it):** zone-strip allocators, the
  hole-table/balloon escalation (`add_table`/`_maybe_tabulate_holes`), section/detail
  *rendering*, projection (`Drawing.at`), export.

So a section is *triggered* by the planner but *drawn* by the existing section
machinery; a dense hole field is *modelled* as a hole set but *tabulated* by the
existing escalation. This is why #238/#207 are "model the intent + feed the infra,"
not "rebuild the infra."

## Definition of done (the whole ADR)

- The per-feature **recognition + dimension-intent** logic moved to the IR/planner,
  and the engine recognition/grouping/spec code deleted — so each pass is fed by the
  IR. Per Amendment 4 the shared **rendering** machinery (layout/strips, table/balloon
  escalation, section drawing, projection, export) **stays** and is fed by the IR.
  - ✅ done: holes, sections (trigger), turned, slots, centre marks, envelope,
    diameters, location dims, the prismatic step-ladder + height + OD/bore (#237);
    foundation track; IR-typed interface. The orchestrator's inline OD/step-ladder is gone.
  - 🔲 remaining: PMI/GD&T (#208).
- **Shared infrastructure intact** (fed by the IR, per Amendment 4) — not rewritten.
- ✅ No `render_into` test-only parallel; no engine/IR duplication in the migrated passes.
- Full standards + geometry suites green; X/Z parity tests per feature.
- ADR 0008 status → "migration complete; one path" (after #208).
