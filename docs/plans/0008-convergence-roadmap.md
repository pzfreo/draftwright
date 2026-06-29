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
  un-patterned holes by `HoleSpec` (the engine's grouped-callout key). The
  prerequisite for migrating the callouts (#238, PR #257).

The mechanically-tractable migrate-and-delete work is done. **What remains needs
new IR modelling, not a mechanical loop** — and the hardest remainder is the hole
**callout placement** migration (see [#238 remaining](#238-remaining--hole-callout-placement)).

## Foundation hardening — do FIRST (ADR 0008 Amendment 5, umbrella #241)

A mid-migration review (#241) found the foundation must catch up before the last
epics, because the IR is now load-bearing for 6 production passes. Each item is a
discrete sub-issue under #241. **The foundation track is complete** (except #251,
which waits on the holes epic):

1. ✅ **Unify the feature inventory** — *keystone* (#244: PR #246 build-time, #247
   lint). `_analyse` detects once; `build_part_model` + `Drawing.lint()` consume its
   products. Residual: bosses still detected independently.
2. ✅ **Docs/comment sweep** (#248, PR #253).
3. ✅ **Annotation-ownership accessor** (#249, PR #254) — registry-backed
   `iter_annotations`/`view_of`/`annotations_in_view` + `replace_object`/`snapshot`/
   `restore`; no production code reads `dwg._named`/`_anno_view` directly.
4. ✅ **Planner render-intent increment** (#250, PR #255) — `PlannedDimension` carries
   `suppressed`/`reason` (model-level suppression moved into the planner) + a `datum`
   slot (consumed by #238 location dims).
5. ✅ **Delete `render_into`** (#251, PR pending) — the test-only parallel
   (`render_into`/`render_callouts` + their leader helpers) is removed; the seam +
   e2e-slice tests are repointed at the production renderers. **Foundation track
   complete.**

## Remaining — feature epics (need IR modelling; AFTER the foundation track)

Ordered by value / readiness. Each needs the **Prereq** modelling before the
migrate-and-delete is possible. (Slots, turned diameters/lengths, centre marks, and
envelope width/depth are already done — see [Done](#done).)

| Issue | Engine pass (to delete) | Prereq (new IR modelling) | Priority |
|---|---|---|---|
| **#238** | **holes**: callout *placement* + pitch/balloons (`_annotate_holes`, `_build_callout`/`_subspecs`). *Done:* location dims (`_add_location_dims` deleted, #256); centre marks (#235); IR hole grouping (#257) | callout placement fed from the grouped IR; **feed** the existing strip/balloon/**table** escalation (don't rebuild it, Amend. 4). See [#238 remaining](#238-remaining--hole-callout-placement) | **highest** — partially landed; placement is the hard remainder |
| **#207** | **sections / detail views** (`_add_section_view`, `_add_detail_view`) | planner **section-trigger** (which features need a section); rendering stays shared infra | medium |
| **#200 → #208** | **PMI / GD&T** (`_annotate_pmi`) | a **PMI/thread detector** → GD&T `Feature`s | medium |
| **#237** | **prismatic step-height ladder + envelope height + OD** (`dim_step_*`, `_detect_step_repeat`, `_legible_steps`, `dim_height`, `dim_od`) — coupled via the `fv_zones.right` / `_right_ladder` cursor | a **prismatic-step `Feature`** (`analyse_face_levels` → `step_zs`) + rotational classification/OD. Folds in #230, #222 | **deferred** — lowest frequency, highest complexity, worst ROI |

When these land, the orchestrator's per-feature calls are gone and it reduces to
`build model → plan → render`.

## #238 remaining — hole-callout placement

Location dims, centre marks, and IR hole grouping have landed (above). The remaining
piece is the hole **callout placement** itself — the largest, most-coupled migration
in the convergence. Reading `_annotate_holes` surfaced *why* it is not a swap:

- **The placement is shared infra, not feature logic.** `_solve_strip_via_layout`
  (Cassowary strip + per-view deconfliction) and the front-view shaft-row packing are
  mature layout machinery. Per **Amendment 4** they **stay** — the IR *feeds* them.
  (The test-only `render_into` elbow-ring search is materially weaker — it already
  needed an on-page guard, #257 — so it is **not** the production placement.)
- **Balloons + the table escalation are coupled to recognition objects.**
  `_add_furniture` (bolt-circle centre-lines, linear/grid pitch dims) and
  `_cover_pattern` (which feeds `_maybe_tabulate_holes`) consume the recognition
  `Pattern` and `Hole` objects — `_cover_pattern` matches covered holes against
  `a.holes` for the table. `PatternFeature` already carries `members`/`bcd`/`pitch`/
  `rows`/`cols`; the open question is hole **identity** for the table match.

### Approach (decomposed, each its own PR)

- ✅ **B1 — callout spec from the IR** (PR #259). The `HoleCallout` is built from the
  IR's `hole_callout_spec` via a shared `callout_from_spec`; the duplicated engine-side
  extraction (`_build_callout`) is gone. (Found+fixed: `HoleCallout` renders a float
  wider than the `_fmt` string — #261; the IR stays float-clean, the renderer formats.)
- ✅ **B2 — drive the callout loop from the IR** (PR #260). The IR is the single
  grouping authority; the engine's `HoleSpec` grouping + `_subspecs` are deleted.
  `_annotate_holes` iterates the IR groups, mapping back to recognition `Hole`/`Pattern`
  objects to feed the (still shared) placement + furniture + table machinery.
- **B3 — IR-typed interface (Amendment 6, #263): retire the recognition-object
  mapping.** B2's `loc_to_hole` / `pat_by_key` mapping is a **scaffold, not the steady
  state** — recognition objects must not stay load-bearing downstream of the IR.
  Sequence (each a PR, behaviour-equivalent, dense-part visual gate):
  1. **Cover-by-location** — `CoverageState.cover_pattern`/`is_pattern_covered` match by
     location key, not `Hole` identity, so `_maybe_tabulate_holes` no longer needs
     `Hole` objects. *(Riskiest — the table-escalation matching.)*
  2. **Furniture from `PatternFeature`** — `_add_furniture` reads the IR feature
     (members/bcd/pitch/rows/cols); the *which-pitch-dim* intent moves to the planner,
     `_place_pitch_dim`/`_add_grid_pitch_dims` stay as the shared placement.
  3. **Placement on IR geometry** — `to_page` over member locations, `_rim_tip` from the
     feature diameter; drop `loc_to_hole`.
  4. **Remove `a.holes`/`found_patterns`** from `_annotate_holes` — only the IR flows in.

After B3, `_annotate_holes` is fed **purely by the IR** (Amendment 6 satisfied);
recognition objects stop at detection, and **#251** (delete `render_into`) unblocks.
Per the project stance: *do it right or not at all* — B3 is on the path, not optional.

### Verification bar (this migration specifically)

- **Dense-part visual regression** — multi-hole plate, bolt circle, rect grid,
  counterbore, mixed spec groups, and a part heavy enough to trigger the **hole
  table** — placement quality + no overlap/OOB, by eye.
- The table-escalation tests (`_maybe_tabulate_holes` / `cover_pattern`) stay green —
  the coupling above is the main regression risk.
- Lint-clean + X/Z parity; full fast suite; CI green.

## Cross-cutting (remaining)

- **Retire `render_into`'s test-only parallel** — its hole/envelope/OD capability
  is superseded *in production* as the holes + envelope-height epics land; delete
  the scaffold then so no divergent path lingers. (`render_callouts`/`render_into`
  currently drive only the seam + e2e-slice tests.)

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

- The per-feature **recognition + placement** passes deleted —
  `annotations/{holes,sections,pmi}` and the inline envelope/OD/step-ladder code in
  the orchestrator; orchestrator reduced to `build model → plan → render`.
- **Shared layout/table/section/projection/export infrastructure intact** (fed by
  the IR, per Amendment 4) — not rewritten.
- No `render_into` test-only parallel; no engine/IR duplication.
- Full standards + geometry suites green; X/Z parity tests per feature.
- ADR 0008 status → "migration complete; one path".
