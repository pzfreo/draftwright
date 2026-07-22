# ADR 0015 — The part-drawing compiler, as built

- **Status:** Accepted. **Supersedes [ADR 0008](0008-unified-feature-model-and-dimensioning-planner.md).**
- **Amendment 1** (2026-07-19): the migrations tracked by planner-convergence
  epic #698 are complete. The remaining model-routed passes are classified
  honestly below, including rotational dimension debt now tracked by #754.
  The open/closed consequence is narrowed accordingly.
- **Amendment 2** (2026-07-22): #754 closed — `render_rotational`'s OD and bore
  dimension *labels* are now planner-fed (they read the folded value/tolerance off
  the feature's `DimensionGroup`), moving out of the model-routed list. Only its
  axis centrelines and bore-stack layout remain model-routed furniture.
- **Date:** 2026-07-18
- **Deciders:** Paul Fremantle (pzfreo)

## Why a superseding ADR

ADR 0008 accumulated eight amendments; the last two exist only as status-header
bullets, and the header's "Current decision" summary has had to be re-synced
against the trail more than once (#696/#697). Per the project rule (past ~3–4
amendments, supersede), this ADR restates the compiler architecture **as it is
actually built today** — including where the code has *not* yet converged on
0008's stated end state — in one clean pass. 0008 is marked Superseded; its
amendment trail remains the historical record of *how* the shape was reached
(contract refinement → out-grow strategy → one-path convergence → IR/infra
boundary → one inventory → IR-typed interface → two-tier waist → the lint
carve-out). Nothing in that trail is re-litigated here.

## The compiler shape

The part-drawing engine is a **compiler**:

```
  recognisers (recognition/)          declared features (model/declare.py)
        │  geometry-only records            │  ADR 0011: the caller supplies
        │  (ADR 0013 contract)              │  the features it already knows
        ▼                                   ▼
  model/detect.py ──────────────► PART MODEL — the IR waist ◄──────────────
        adapters: record → Feature   (model/ir.py: Feature / DimParameter /
                                      Datum / PartModel; frozen dataclasses)
                                            │
  dimensioning planner (model/planner.py)   │  plan_dimensions → DimensionGroup
  plan_locations / plan_sections            │  per feature; convention + view +
                                            ▼  model-level suppression + datum
  render-intents → the IR render layer (annotations/from_model.py, holes.py)
                                            │
  shared layout / projection / export  (ADR 0014 placement, ADR 0004 pack,
                                        projection.py, export.py — fed, never absorbed)
```

The load-bearing properties, all live in the code today:

1. **One feature inventory, detected once — regardless of `auto_dims`.**
   `analysis._analyse` runs the recognisers once and builds the `PartModel` up
   front, so **page/scale sizing reads the same feature model the renderers do**
   (`sizing_model` → `plan_dimensions` feeds the `compose.py` estimators;
   detected and declared parts share one sizing path). `builder._assemble`
   attaches that model to the `Drawing` *before* the `auto_dims` gate, so
   `dwg.model()` and the feature-edit verbs work even in manual mode; the
   orchestrator (`annotations/orchestrator.py`) reads the attached model rather
   than rebuilding, and `builder.detect_part_model` exposes the same
   detect-only path as a cheap seed (`Sheet.from_part`).
2. **Orientation and feature kind are data in the IR**, never code branches in
   the back-end: `Feature.frame` carries the axis; the planner derives a
   group's view from it by one rule — `_END_ON` for a diameter callout,
   `_PROFILE` (the in-plane containment rule: front is the x–z plane, so X and
   Z both derive to front) for a turned step's length/OD — so X and Z are
   symmetric. (The former residue — a hardcoded `kind == "step"` → front —
   was replaced by that derivation in #731.)
3. **The waist is two tiers.** The lower tier is the geometry-only recognition
   record produced under the uniform `recognise_<feature>` contract (ADR 0013);
   the upper tier is the dimensioning IR `Feature`. They are joined by the
   `model/detect.py` adapters. The typed per-record **adapter registry** there
   is decided but pending — that is **ADR 0013 Phase 1, roadmap item 1c**, not
   this ADR; `detect.py` today remains bespoke per-feature translators. No
   recognition object crosses the boundary in either state.
4. **Two front doors, one waist.** Detection (`model/detect.py`, from
   `recognition/`) and declaration (`model/declare.py`, ADR 0011:
   `hole`/`boss`/`step`/… constructors that read a feature's size off the
   build123d object, or take explicit values) both emit the **same** IR
   `Feature` types into the same `PartModel`, so no renderer branches on
   feature *types* by producer. Downstream is not fully producer-**blind**,
   though: one declared-provenance flag survives — `builder.build_drawing`
   synthesises rotational/PMI features for a caller-declared model (a declared
   turned shaft must render with the same furniture detection produces, #472)
   and records `model_declared`, which the orchestrator reads to widen the
   hole-callout membership set to declared positions detection missed
   (ADR 0011 #448). The flag gates *parity behaviours*, not divergent paths.
5. **The IR→infrastructure boundary is IR-typed** (0008 Am 6, kept): shared
   services take model-space locations, `DimParameter`s, feature kinds, and
   frozen value keys (`HoleRef`), enforced by their signatures. The shared
   layout/table/section/projection/export machinery is *fed* by render-intents,
   never reabsorbed (0008 Am 4, kept).

## The planner, honestly: what flows through it and what bypasses it

0008 §3 claimed "one rule set over DimParameters … uniformly". The 2026-07-18
audit found that many dimension-bearing feature passes still bypassed it and
opened **#698**. The migrations owned by that epic are now complete:
`orchestrator._auto_annotate` calls `plan_dimensions` exactly once and threads
its `DimensionGroup`s to the migrated renderers. The audit of this amendment
found one residual dimension-bearing bypass, rotational OD/bores — since closed
by #754 (Amendment 2): those labels are now planner-fed.

**Planner-fed today** (the renderer consumes `DimensionGroup`s from
`plan_dimensions`, or another planner entry point):

| Feature kind(s) | Renderer (annotations/) | Planner entry |
| --- | --- | --- |
| holes / patterns (bore, counterbore, spotface, thread, BCD, pitch) | `holes._annotate_holes` (+ centre marks via `from_model.render_centermarks`) | `plan_dimensions` |
| hole / pattern locations | `from_model.render_locations` | `plan_locations` (refs + datum) |
| turned diameters (ø leaders, row/column) | `from_model.render_diameters` | `plan_dimensions` |
| boss diameters | `from_model.render_boss_diameters` | `plan_dimensions` |
| rotational OD + concentric bore diameters (labels; #754) | `from_model.render_rotational` | `plan_dimensions` |
| envelope (overall W/D/L, with model-level suppression) | `from_model.render_envelope` | `plan_dimensions` |
| turned step lengths (the chain) | `from_model.render_step_lengths` | `plan_dimensions` |
| chamfers (C{leg} / {leg}×{angle}° leader, #724) | `from_model.render_chamfers` | `plan_dimensions` |
| fillets (R{radius} / n× R leader, #725) | `from_model.render_fillets` | `plan_dimensions` |
| flats ({across} A/F leader, #726) | `from_model.render_flats` | `plan_dimensions` |
| grooves ({width} WIDE × ø{diameter} leader, #727) | `from_model.render_grooves` | `plan_dimensions` |
| pockets (W × L × D DEEP leader, #728) | `from_model.render_pockets` | `plan_dimensions` |
| plates (thickness linear dim, #729) | `from_model.render_plates` | `plan_dimensions` |
| slots (width/length linear dims, #730; the datum position dim stays model-derived — it is drawing state, not a feature parameter) | `from_model.render_slots` | `plan_dimensions` |
| section trigger + cut plane | `sections._add_section_view` etc. | `plan_sections` → `SectionPlan` |

**Model-routed today** (where a feature exposes parameters, `plan_dimensions`
still computes a group that these passes do not consume):

- `render_rotational`'s axis centrelines and the concentric-bore leader-stack
  layout/drop bookkeeping are furniture and remain model-routed. Its OD and bore
  dimension **labels** are now planner-fed (#754): they read the value and any
  authored tolerance/fit off the feature's `DimensionGroup`, not the raw
  `RotationalFeature` fields. (A single `(feature, "diameter")` decoration still
  folds onto OD *and* every bore alike — per-role targeting is #746, not #754.)
- `render_height_ladder` and `render_step_positions` are also model-routed,
  **by design**: `StepLevelFeature` carries correlated sets that must never be
  flattened into independent dims, so group-per-feature is the wrong shape for
  them. Their computed groups are discarded; the whole-set renderers are the
  sanctioned owner of those correlated dimensions.
- `render_pmi` is model-routed by design: authored PMI features expose no
  parameters for `plan_dimensions`, so no group is computed or discarded.
- `render_gdt` is model-routed and **out of the planner's scope by design**:
  `ControlFrame`/`DatumRef`/`Finish` (ADR 0011 P2b) are placement intents, not
  `DimParameter`-bearing features — there is nothing for `plan_dimensions` to
  plan.

**Why the split matters:** the planner is where authored decorations fold onto
dimension parameters and where dimension-level convention and suppression
belong. #698 migrated chamfer, fillet, flat, groove, pocket, plate, and slot
dimensions (#724–#730), closing the latent authored-tolerance failure class for
those features. Model-routing is legitimate where there is no independent
`DimParameter` to plan or where flattening a correlated set would destroy its
semantics; otherwise it is explicit debt to be closed (as #754 since was).

## The lint/coverage carve-out

0008's Amendment 8 established this but never gave it a body; stated properly:

**One path deliberately keeps reading recognised geometry instead of the IR,
and that is correct — not a boundary violation.** `linting/coverage.py`
(`lint_feature_coverage`) answers "is every feature that physically *exists*
dimensioned?". It runs the recognisers itself (`recognise_holes`,
`recognise_turned_steps`, `analyse_cylinders`, …) for the ground truth, and
reads the **placed drawing** (dimension witness endpoints, callout labels —
`_dim_vertices`) for what was actually drawn — never a build-time side
channel, and never the plan. Sourcing coverage from the dimensioning plan
would be circular: a feature the planner (or a bypassing renderer) omitted
would never be flagged. Coverage reading recognition is the check *working*.
Structurally, `linting/` has **no `draftwright.model` import** — machine-checked
by the dedicated `test_linting_does_not_import_model` guard in
`tests/test_import_boundaries.py` (the general layer rule alone would permit
linting→model, so the carve-out gets its own fail-closed assertion) — so it
cannot silently widen into IR coupling. The only other place recognition
records cross is the sanctioned `build_part_model` boundary itself.

## What this ADR does not restate

- **ADR 0011** — the IR as a *public input* (declare features, `model=`, the
  `Sheet` façade, tolerance/fit/GD&T aspects). 0015 only records that
  declaration is the second front door into the same waist.
- **ADR 0013** — the uniform recogniser contract and the pending typed
  adapter registry in `detect.py` (Phase 1 item 1c), plus the deferred shared
  `b123d-recognisers` package. The intake tier's rules live there.
- **ADR 0014** — placement (superseding ADR 0009's collect-then-solve strip
  record). The planner emits *intents*; how they are placed is entirely 0014's
  concern (as 0004 owns the outer pack).

## Consequences

- New shapes require the applicable detector and/or declaration constructor,
  IR adapter/declaration, planner convention for dimension parameters,
  renderer/stage support, coverage, and tests. Orientation and view selection
  must remain data-driven rather than growing producer- or axis-specific paths.
- The duplicate-recogniser and orientation-gate bug classes stay designed out
  (one inventory, axis-as-data).
- The ADR now matches the code: readers get the real planner coverage and the
  real state of the adapter protocol, instead of 0008's aspirational
  "migration complete — one rule set".

## Supersession

ADR 0008 is **Superseded by this ADR**. Its status header, decision text, and
Amendments 1–8 are frozen as the historical record of the convergence; consult
them for the *why* trail (strategy pivots, the retired equivalence gates, the
boundary decisions), not for current state. Step 1 of the original 0008
(unified Z step recognition, #191/#193) stands.

## Related

- #697 — the audit item mandating this supersession (and ADR 0014's).
- #698 — completed planner-bypass migration epic tracked by this ADR's coverage
  table and Amendment 1.
- #754 — residual rotational OD/bore planner bypass found during Amendment 1's
  accuracy review; closed by Amendment 2 (labels now planner-fed).
- #699 — the one canonical `_PASS_SEQUENCE` shared by the auto-pass and
  `finalize()` (orchestrator `run_stages`), which orders the passes named
  above.
- `docs/plans/0008-convergence-roadmap.md` — the historical migration plan of
  record under 0008.
