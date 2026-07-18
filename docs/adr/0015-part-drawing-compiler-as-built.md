# ADR 0015 — The part-drawing compiler, as built

- **Status:** Accepted. **Supersedes [ADR 0008](0008-unified-feature-model-and-dimensioning-planner.md).**
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
   group's view from it by one rule (`_END_ON`), so X and Z are symmetric.
   (One residue: `planner._group_view` special-cases `kind == "step"` → front —
   flagged in #698.)
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
   `Feature` types into the same `PartModel`. Everything downstream is
   producer-blind.
5. **The IR→infrastructure boundary is IR-typed** (0008 Am 6, kept): shared
   services take model-space locations, `DimParameter`s, feature kinds, and
   frozen value keys (`HoleRef`), enforced by their signatures. The shared
   layout/table/section/projection/export machinery is *fed* by render-intents,
   never reabsorbed (0008 Am 4, kept).

## The planner, honestly: what flows through it and what bypasses it

0008 §3 claimed "one rule set over DimParameters … uniformly". As built, that
centralisation covers a **core, not the whole surface** — the 2026-07-18 audit
finding, tracked as **#698**. `orchestrator._auto_annotate` calls
`plan_dimensions` exactly once and threads the `DimensionGroup`s to every
renderer that reads them; but a majority of feature kinds are rendered by
passes that take the `model` and format raw feature fields directly, ignoring
the groups the orchestrator computed for them.

**Planner-fed today** (the renderer consumes `DimensionGroup`s from
`plan_dimensions`, or another planner entry point):

| Feature kind(s) | Renderer (annotations/) | Planner entry |
| --- | --- | --- |
| holes / patterns (bore, counterbore, spotface, thread, BCD, pitch) | `holes._annotate_holes` (+ centre marks via `from_model.render_centermarks`) | `plan_dimensions` |
| hole / pattern locations | `from_model.render_locations` | `plan_locations` (refs + datum) |
| turned diameters (ø leaders, row/column) | `from_model.render_diameters` | `plan_dimensions` |
| boss diameters | `from_model.render_boss_diameters` | `plan_dimensions` |
| envelope (overall W/D/L, with model-level suppression) | `from_model.render_envelope` | `plan_dimensions` |
| turned step lengths (the chain) | `from_model.render_step_lengths` | `plan_dimensions` |
| section trigger + cut plane | `sections._add_section_view` etc. | `plan_sections` → `SectionPlan` |

**Planner-bypassing today** (the renderer takes `model`, re-filters
`model.features` by kind, and formats raw fields — its computed
`DimensionGroup`s are discarded):

- `render_slots`, `render_chamfers`, `render_fillets`, `render_flats`,
  `render_pockets`, `render_grooves`, `render_plates`, `render_rotational`
  (OD/centreline/bore furniture), `render_pmi` — all in
  `annotations/from_model.py`.
- `render_height_ladder` and `render_step_positions` are also model-routed,
  **by design**: `StepLevelFeature` carries correlated sets that must never be
  flattened into independent dims, so group-per-feature is the wrong shape for
  them. Their model-routing is sanctioned, not debt.
- `render_gdt` is model-routed and **out of the planner's scope by design**:
  `ControlFrame`/`DatumRef`/`Finish` (ADR 0011 P2b) are placement intents, not
  `DimParameter`-bearing features — there is nothing for `plan_dimensions` to
  plan.

**Why the bypass list matters** (from #698, adversarially verified): the
planner is where authored decorations fold onto the parameter
(`plan_dimensions` merges `model.decorations` into `DimParameter.tolerance`),
so a bypassed kind silently drops an authored tolerance — the exact #629 bug
class already fixed for bosses, latent in the bypassed passes. And ISO 129
no-double-dimensioning/suppression rules currently have no single home
(`detect.py` exclusions, `planner._suppression`, per-renderer collapses).
`_CONVENTION` in `planner.py` has entries only for the planner-fed roles.

**The convergence tracker is #698** — extend `_CONVENTION` +
`plan_dimensions` kind-by-kind (chamfer, fillet, flat, groove, pocket, plate,
slots first) and convert each renderer to consume its group, pulling the
scattered suppression rules into the planner as each kind migrates. This ADR
deliberately does **not** claim that work done; it records the split so the
ADR stays true while #698 proceeds. New feature kinds must take the planner
path, not add to the bypass list.

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
Structurally, `linting/` stays a leaf with **no `draftwright.model` import**
(machine-checked by `tests/test_import_boundaries.py`), so the carve-out
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

- New shapes remain **new `Feature` types + a detector and/or declare
  constructor**, never new back-end branches — with the added, explicit rule
  that their rendering goes through `plan_dimensions` (#698), so the planner's
  coverage grows instead of shrinking.
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
- #698 — the planner-bypass audit finding; the convergence epic this ADR's
  coverage table tracks.
- #699 — the one canonical `_PASS_SEQUENCE` shared by the auto-pass and
  `finalize()` (orchestrator `run_stages`), which orders the passes named
  above.
- `docs/plans/0008-convergence-roadmap.md` — the historical migration plan of
  record under 0008.
