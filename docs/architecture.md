# Architecture — the detailed module map

The module-by-module map of the draftwright engine, moved out of `CLAUDE.md`
(which keeps the compact map and the working rules). The machine-enforced
authority for the layering is `tests/test_import_boundaries.py` (`_LAYERS`);
keep that table, this document, and `CLAUDE.md`'s compact map in step. The
*why* behind every shape here lives in `docs/adr/`.

## The module map

The dependency graph is a DAG (the #138 / ADR 0005 split is complete). Bottom to
top: leaf modules (`layout.py`, `registry.py`, `fonts.py`, `_geometry.py`,
`fits.py`, `intents.py`, `recognition_cache.py`, and the `linting/` subpackage) →
`_core.py` → stage modules (`export.py`,
`repair.py`, `projection.py`, `compose.py`, `analysis.py`, `drawing.py`, the
`model/` IR subpackage, the `annotations/` subpackage) → `builder.py` → the
user-facing surfaces: the `make_drawing.py` / `annotate.py` compat facades, the
fluent `Sheet` facade (`sheet.py`), the Sheet-script emitter
(`sheet_emit.py`), the recognition-evaluation package (`evaluation/`), and the
`cli.py` entry point. Developer-only `_build_profile.py` sits at the same top layer: it
patches the public builder and Sheet bindings lazily for pytest measurement, and no engine
module depends on it. No lower module imports an
upper one. (All surfaces are front doors onto the one engine,
`build_drawing` → `_auto_annotate` — there is no second engine.)

This DAG is **machine-enforced** by `tests/test_import_boundaries.py` (#640): the
`_LAYERS` table there is the precise, ranked form of this section — a module-level
import that points up a layer fails CI, as does an import cycle. The precise
placement refines the coarse grouping above (e.g. `linting`/`pmi`/`export`/`repair`/
`projection`/`compose` sit *above* `_core` since they depend on it; `model/` is the
IR-waist leaf it is guarded as). The `_LAZY_UPWARD_EXEMPT` sanctioned-cycle-breaker
mechanism is now empty (#523 removed its last occupant, the `builder→cli` edge — see
below); a new upward lazy import must earn an entry with a rationale. The remaining
lazy in-function imports (`cli`→`builder`/`sheet_emit`, for the #313 build123d
lazy-load) are *downward*, not cycle-breakers. The one type-only upward reference
(`_core`→`compose.StripDepths`, under `TYPE_CHECKING`) is an explicit allowlist
entry. Keep `_LAYERS` and this section in step.

`evaluation/` owns the versioned, independently-authored STEP-analysis benchmark and its
scoring model. It is a top-layer consumer of the recognition contract; production recognition,
IR, generation, and drawing code must not depend on benchmark expectations or scores.

- **`make_drawing.py`** — thin compat facade (~20 lines) re-exporting the public
  surface (`Drawing`, `build_drawing`, `make_drawing`, `_cli`,
  `FeatureInfo`, `fix_svg_page_size`, `lint_feature_coverage`) so existing imports
  and the `draftwright` CLI entry point keep working. The engine lives in:
  - **`builder.py`** — build orchestration: `build_drawing` (analyse → assemble →
    measure-and-repack → `Drawing`) and `make_drawing` (+ export). Imports
    `drawing`/`analysis`/the annotation orchestrator/the stage modules — never
    `make_drawing` (a DAG). *(`generate_script`, the imperative editable-script
    generator, was retired by #940 — ADR 0016 phase 6 / ADR 0001 Amdt 2 — and
    **deleted** at its 0.4.0 date (#720), along with the raising stub that stood
    in for it and the bespoke `--style imperative` message. `--style` itself
    survives with the single value `sheet` so existing invocations keep
    working.)*
    *(The CLI moved out to `cli.py`; the `_cli` compat shim lives there too (#523),
    so `builder` no longer imports `cli`.)*
  - **`cli.py`** — the Typer command-line interface (#289): argument parsing,
    `--version`, shell completion, `--format`, rich help. The engine (build123d)
    is imported **lazily inside the command body** so completion/`--help`/
    `--version` stay sub-second (#313). Entry point: `draftwright.cli:app`.
  - **`drawing.py`** — the `Drawing` result object (`.lint()`/`.add()`/`.place_dim()`/
    `.repair()`/`.export*()`; delegates identity to `registry`, coverage to `lint`)
    plus `FeatureInfo` (`_build_table` moved beside `_table_metrics` in `_core`, #699).
    Sits below `builder` (which constructs it).
    *(The build context lives in ONE typed `BuildState` on `Drawing` (`_build`:
    analysis, part model, lint's geometry caches) — filled at a single site in
    `builder._assemble`, read through compat properties, single-writer-guarded
    by `test_drawing_encapsulation`. ADR 0005 §2 / #639 closed: `annotations/`
    has zero private `Drawing` reads (empty allowlist ratchet) — and since #699
    slice d the state-bus guard covers the WHOLE engine: no module but
    `drawing.py` touches `dwg._*` (rationale-carrying allowlist, builder's
    fill site only).)*
- **`annotate.py`** — thin compat facade re-exporting `_auto_annotate` (the
  orchestrator) from `annotations/`. The annotation passes were split into the
  **`annotations/`** subpackage (#164 / ADR 0005, P5):
  - **`annotations/orchestrator.py`** — `_auto_annotate`, the single entry point
    (called by `build_drawing`); classifies the part and drives the render passes
    + title block. Owns **`_PASS_SEQUENCE`** — the ONE canonical stage order
    (#699 slice b): `_auto_annotate` and `Drawing._drain_intents` (the finalize
    drain) both hand name→thunk dicts to the shared `run_stages`, so the two
    build paths cannot diverge in sequencing (the drain step itself is the
    shared `drain_and_reconcile`). The current ADR 0015 shape is
    `build model → plan/model-routed intents → render`; some inline engine code remains — chiefly
    `_maybe_tabulate_holes` (the hole-table/balloon escalation resolver) and the
    iso right-strip outer-limit tightening — pending the last convergence steps.
  - **`annotations/from_model.py`** — the **IR render layer** (largest annotations
    module): turns the planner's `DimensionGroup`/render-intents into placed
    dimensions/callouts/centre marks/section triggers. This is where the turned,
    PMI/GD&T, envelope/OD, centre-mark and step-length passes converged (ADR 0015,
    #200/#208/#237) — the old per-feature `annotations/{turned,pmi}.py` modules
    were deleted as each migrated here.
  - **`annotations/holes.py`** — hole/pattern callouts, balloons, location dims
    (incl. side-drilled #133), pitch/grid dims, slots (the largest *pass*).
  - **`annotations/leaders.py`** — the one bounded late inventory for compatible
  automatic/deferred same-view feature leaders (#1166): sparse ordinary
  side/plan hole jobs and the five post-drain machined-feature families lower
  exact committed component ink conflicts (including component-local curved
  centre furniture and rendered shifted-dimension arrows), retaining any
  rendered residual triangle not covered by one same known component and any
  curved residual area between tessellation stations, including filled
  datum/GD&T faces without segment metadata; candidate construction fails closed
  per alternative, while a selected-survivor construction exception or mismatch
  replays the canonical lazy producer tail rather than silently reducing
  cardinality, with validation-stage diagnostics and without swallowing compiler
  invariant errors; fixed components are lowered once under the same resource
  discipline and reused; the complete title
  band stays hard after rendering; only explicitly
  marked global turning axes exempt only the arrow-local tip attachment (not
  near-collinear shaft travel),
  priorities, Policy-B penalties, and numeric costs into `layout.py`; optional
  sections enter only a separately bounded no-worse refinement, then validate
  their final ink against landed leaders, repair one end-symbol extent, and yield on conflict,
  then validate the selected OCC ink and emit it with its original provenance;
  any retained fixed-ink Policy-B crossing persists as
  `feature_leader_crossing`, independently of opt-in tracing, while a producer
  replay beyond the exact fixed-probe budget persists as
  `feature_leader_fixed_ink_unverified` without exceeding that bound and cannot
  report a perfect legibility quality score. ADR 0014 Amendment 3 (#798) adds
  material re-entry to that Policy-B penalty at a stated exchange rate (one unit
  per visible stroke width of buried shaft) — a cost, never an eligibility gate
  and never an acceptance test, so no callout is dropped because its route cuts.
  The resource-cap floor, which is what actually runs on dense parts, gained a
  bounded lookahead past a first acceptable-but-cutting route; a job whose first
  acceptable route already clears the body selects it exactly where it always did.
  - **`annotations/sections.py`** — section A–A + detail views (ISO 128-44 arrows,
    ISO 128-50 hatching).
  - **`annotations/balloons.py`** — the leadered hole-balloon pass (#111/#516;
    moved down from `Drawing`, #699). `Drawing.add_balloons` is the public verb
    threading build state in; the band-assignment flow solver lives in `layout.py`.
  - **`annotations/gears.py`** — standards-backed data-table presentation for the
    declaration-only metric external spur gear; the table flows through the generic
    solver-owned late-furniture path (#1086).
  - **`annotations/_common.py`** — the ADR 0014 corridor-solve engine
    (`CorridorCandidate`, `solve_corridor`, `register_corridor`/`drain_corridors`,
    `place_strip_candidates`, `PlacementContext`) plus `_box_hits`, at the
    bottom of the annotations DAG. (The bbox/segment primitives it delegates to
    live in `_core`/`_geometry` since #700.) It also owns the **post-fit
    late-furniture** seam (#1197): `late_furniture_obstacles` is the ONE occupancy
    a placer facing the finished sheet uses — views, decomposed annotation ink,
    minus the page-spanning riders, plus the title block as one hull — shared by
    `Drawing.add_table` and by `place_iso_nts_note`, the iso's NTS caption, which
    lives here rather than in `projection` because rank-2 cannot reach that
    occupancy and a hand-rolled substitute was wrong twice.
  Submodules import only down or sideways — `_core`/`layout`/`analysis`/
  `projection`/the `model/` IR/`linting.structural`/third-party, never
  `annotate`/`make_drawing`/`drawing` (the drawing is duck-typed as `dwg`) — so
  the orchestrator calls down with no cycle.
- **`_core.py`** — shared primitives below both `make_drawing.py` and `annotate.py`:
  the `Analysis` namespace and its field types (`_Projector`, `Strip`, `ViewZones`),
  the dimension/format helpers (`_dim`, `_fmt`, `_add_title_block`, …), and the
  page/slot/margin layout constants.
- **`layout.py`** — the deterministic placement primitives used by ADRs 0004/0014:
  the deterministic
  1D PAVA strip solve (`_solve_strip_1d_pava`, plus `plan_strip`/`StripCandidate`,
  the ADR 0014 collect-then-solve entry point), the 2D free-rectangle placer
  (`fit_box`), and the balloon band-assignment min-cost max-flow solve
  (`_assign_balloon_bands`, #516; here since #699 — solvers live in the solver
  layer). Sits *below* the domain API.
- **`_geometry.py`** — model-neutral geometry primitives (`_xyz`, `HoleRef`,
  `_axis_letter`, `_END_ON`) plus the #700 shared page-plane maths (`_fmt`,
  `_boxes_overlap`, the two segment/box tests) and `plane_axes` — the one
  in-plane `(u, v)` basis per axis, shared by `recognition._features._plane_uv`
  and `model.declare._plane_axes` so a pattern's `angle` means the same thing
  detected as declared (#969); the DAG's bottom leaf (guarded
  by `test_geometry_is_a_leaf`) so the IR waist uses them without importing
  `_core`. Also the ADR 0014 Amendment 3 **filled material field** (#798):
  `MaterialField`/`material_field` (page-plane triangles + a uniform grid index),
  `material_span`, `material_intervals`, and `material_reentry_span` — exact
  half-plane clipping with a fixed-point interval union and an incremental cell
  walk, no sampling. Re-entry, not traversal, is the routing defect: a leader's
  first passage out of the body is the legitimate exit every callout makes.
- **`_pmi_part21.py`** — the rank-0 structured ISO 10303-21 adapter for AP242
  geometric-tolerance facts that OCCT's XCAF transfer drops. It resolves explicit
  Part21 references and SI length units, and requires unique semantic-name + kind
  correspondence; it knows nothing about XCAF, drafting IR, or placement.
- **`_warnings.py`** — the public warning categories (`SoftDeprecationWarning`), a
  dependency-free leaf. Separate from `_core` on purpose: `_core` imports build123d, so a
  category defined there costs the CAD kernel (~6 s) to reach, and the pytest
  `filterwarnings` entry naming it pays that on every invocation (#1043).
- **`fits.py`** — the ISO 286 fit tables (`fit_deviation`, `FitClass`; ADR 0011
  P2a.2): a rank-0 leaf consumed by `_core`, `model/ir` and `sheet`.
- **`intents.py`** — the deferred-placement "low IR" behind `Drawing.finalize()`
  (#426): a dependency-free leaf recording edit-verb intents for the recompose
  (deliberately stringly-typed in its Phase-1 form).
- **`registry.py`** — `AnnotationRegistry`: the single owner of annotation
  identity/ownership/pins/build-issues (#138 / ADR 0005, Step 2). `Drawing`
  delegates here and keeps the render list. The `_named`/`_anno_view`/`_pinned`/
  `_build_issues` aliases on `Drawing` (and coverage's three) were **deleted** at
  their §4 date (#720): reach the state through `dwg.registry` (`in reg`,
  `names()`, `issues`, `restore_issues()`) and `dwg.coverage`. Their absence is
  asserted by `test_the_expired_compat_aliases_stay_deleted`.
- **`linting/`** — the lint subpackage (#138 / ADR 0005; ADR 0007: draftwright
  owns linting): `coverage.py` (`lint_feature_coverage` + `CoverageState`),
  `structural.py` (geometry/standards checks), `issues.py` (the `LintIssue` type),
  `gear_coverage.py` (declared gear table/profile reconciliation), and `suggest.py`
  (`_suggest_fix`, #29 snippets). Depends only on `_core`,
  `b123d_recognisers` (typed hole records in `coverage.py`) + build123d_drafting.
  `_QUOTED_RE` (a lint-message label regex shared with the
  repair loop) lives in `_core`.
- **`recognition_cache.py`** — Draftwright's ADR 0017 one-result lifecycle owner. It calls
  external `build_recognition_result(part)` at most once for a build/lazy-critique run; the
  package owns recognition, while Draftwright owns when the result is computed and reused.
- **`recogniser_contract.py`** — the fail-closed cross-repository capability join. It consumes
  only the installed `b123d-recognisers` public manifest, then validates Draftwright-owned IR,
  `Sheet`, generated-code, drawing, completeness, and documentation declarations. It is rank 7
  because validation dynamically resolves implementation references across every lower layer;
  package geometry policy never imports or owns this consumer overlay.
- **`model/`** — the ADR 0015 IR waist: `ir.py` (the `Feature`/`DimParameter`/
  `Datum`/`PartModel` types — the one inventory), `detect.py` (detectors →
  `Feature` objects, adapting `b123d_recognisers` records), `planner.py`
  (`plan_dimensions` —
  one rule set → a `DimensionGroup` per feature, + `plan_sections`; and, since #1154,
  the one cross-feature reconciliation: two features measuring between the same two
  support planes state one fact, so the overall extent keeps it and the feature-local
  one records where it went), and
  `declare.py` (ADR 0011 object→feature constructors: `hole`/`boss`/`step`/… read
  a feature's size off the build123d object — a second, *declared* front-end into
  the same IR the detectors fill). The narrow middle of the compiler hourglass;
  consumed by `annotations/from_model.py`.
- **`compose.py`** — the ADR 0004 **outer** compose-then-pack layout engine
  (`choose_scale`, `ViewBlock`, zone/strip depths). Née `sheet.py`; renamed
  (#640) so the layout engine stops shadowing the user-facing `Sheet` facade
  (which now owns the `sheet.py` name).
- **`analysis.py`** — the `_analyse` stage: solid classification, the one-shot
  feature-inventory detection (ADR 0015), view sizing, and the strip/zone
  model (`fv_zones`/`pv_zones`/`sv_zones`) that ADR 0014 placement reads.
- **`projection.py`** — HLR projection and view-coordinate transforms
  (`_assemble`'s geometry half; #161). Also the #798 **material lowering**:
  `part_material_mesh` tessellates the part once per build and
  `view_material_field` projects that one mesh per view, so every view measures
  the same material. The mesh is taken under explicit control (copy →
  `BRepTools.Clean_s` → `BRepMesh_IncrementalMesh`) because OCC caches a
  triangulation on the shape and returns it for any later request — even a finer
  one — which would make the field a function of build *history* (the ADR 0006
  hazard). `Drawing.material_fields()` holds the result on `BuildState`.
  `_fit_iso_view` **returns** the iso bbox when the fitted iso is off sheet scale and
  therefore needs an NTS caption, else `None`; it does not place that caption. `builder`
  does, at the common post-fit point *before* `render_gear_tables` — the caption is tied
  to the block it labels while a table may sit anywhere, so the constrained furniture
  claims space first (#1197).
- **`sheet.py`** — the fluent declarative **`Sheet`** facade (ADR 0011):
  feature verbs (`hole`/`boss`/`slot`/…), aspect verbs (`.tolerance`/`.fit`/
  `.finish`), GD&T (`datum`/`control`). Facade tier: builds a `PartModel` via
  `model/declare.py` and calls `build_drawing(model=…)`. Née `sheet_dsl.py`
  (renamed #640 — it's a fluent facade, not a DSL, per ADR 0001; the `sheet_dsl`
  alias shim was deleted at 0.4.0, #720).
- **`sheet_emit.py`** — **the** script emitter, behind `--script` (#940 retired the
  imperative alternative): generates an editable `Sheet` script from a detected
  model — one named binding per feature, an explicit dimension source. Facade tier;
  imports `builder` downward at module level. The old builder→cli→sheet_emit
  lazy cycle is **gone** (#523): the `_cli` compat shim moved from `builder` to
  `cli.py` (beside the Typer `app`), so `builder` no longer imports `cli` and
  `_LAZY_UPWARD_EXEMPT` is now empty. The graph is a plain DAG —
  `cli → {builder, sheet_emit}`, `sheet_emit → builder`, `builder → ∅`.
- **`score.py` / `recognition/`** — temporary public compatibility re-exports of
  `b123d_recognisers`, identity-preserving and scheduled for removal in 0.6.0. There are no
  embedded recogniser modules. Engine code imports the external package directly; private
  historical `draftwright.recognition.*` paths are intentionally unsupported.
- **`b123d_recognisers` (external)** — the ADR 0013 geometry-only bottom layer: uniform
  deterministic `recognise_*` functions, frozen serialisable records, shared substrates,
  `RecognitionResult` orchestration/manifest, repeating-profile correspondence, and
  `feature_census`. It imports build123d/OCP and never imports Draftwright.
- **`fonts.py`** — vendored, path-pinned IBM Plex fonts for deterministic
  cross-platform layout (ADR 0006).
- **`export.py`** — SVG/DXF/PDF/PNG export + post-processing (page-size fix,
  attribution hyperlink/metadata, DXF metadata, arc sanitisation, element-wise
  shape-export degradation). The render chain is **SVG → PDF → PNG**: PDF via
  svglib + reportlab (`_render_pdf`, #288), PNG via **pypdfium2 + Pillow**
  (`_render_png`) — both pure-wheel, **no native cairo** and permissively
  licensed (BSD/Apache/HPND, dual-license-clean). The unified
  `Drawing.export(out, *, formats=("pdf",)) → {format: path}` is the front door
  (the legacy `svg=`/`dxf=` tuple form + `export_pdf` are back-compat/deprecated
  wrappers). Sits below `make_drawing.py`, above `_core.py`.

  **`reproducible=` — byte-identical exports, opt-in.** On, two exports of one
  drawing are identical, so a checked-in drawing diffs cleanly and a caller can
  see when its output really changed. Three things are settled to get there: the
  clock and GUIDs an exporter stamps (a per-document fixed metadata updater,
  avoiding ezdxf's concurrency-unsafe process-global testing option, plus
  reportlab's `invariant`), ezdxf's CLASSES section (built by
  iterating a `set[str]`, so it is pre-seeded sorted), and the order the kernel
  hands parts over, which is not stable between runs and does **not** reduce to
  `PYTHONHASHSEED`.

  The two formats reach that by different routes, and the asymmetry is deliberate.
  A DXF must be ordered *going in* — `ExportDXF` writes an entity per element as it
  converts and the handles follow — so `_elements(ordered=True)` sorts the
  emitted boundary entities by geometry, keyed on where an edge sits **and which way it runs**
  (hidden-line removal emits reversed duplicate pairs that agree on everything
  else); faces are flattened before sorting so their cyclic wire start cannot
  leak into entity order, and cheap-key ties use exact B-rep bytes. An SVG is
  settled *coming out*, by `canonicalize_svg` sorting each
  all-leaf layer group in the written file, so it is handed its shapes unordered
  even when the flag is on.

  **Off by default, because ordering is not free**: about a third of DXF export
  time again (interleaved, 9 runs, 358-part sheet: 0.45 s → 0.60 s), one
  `bounding_box()` and one `edges()` per part. Off costs what it did before the
  option existed (0.45 s vs 0.49 s on `main`); the metadata pinning is the cheap
  half at ~1 ms. Both hang off the one flag, since a caller wanting a stable file
  wants both and should not have to know which one costs. Reachable as
  `build_drawing(..., reproducible=True)` (the default a returned `Drawing` then
  carries) and per call as `Drawing.export(..., reproducible=True)`. Weigh any
  change here against #602, which removed a `zoom.extents` walk from the same path.
- **`repair.py`** — the deterministic lint→repair loop (#30 / ADR 0002): the
  re-place helpers (`_find_dim`/`_replace_dim`/`_repair_*`/`repair_drawing`) take
  the drawing duck-typed as `dwg`; `Drawing.repair()` stays a thin wrapper.
  Depends only on `_core`.
- **`pmi.py`** — PMI (product manufacturing information) extraction from STEP AP242.
  Owns the XCAF source census and overlays `_pmi_part21` facts only after exact
  correspondence. It inventories the complete OCCT geometric-tolerance modifier vocabulary,
  preserves unsupported facts in the raw IR fallback, and exposes explicit blockers to typed
  lowering; XCAF and Part21 source identities survive both paths.

## Current ADRs — status detail

The working rules (read ADRs first, assess architectural fit, the amendment
policy) live in `CLAUDE.md`. This is the per-ADR status trail; each ADR's
**Current decision** header remains the authoritative amended state.
- **0001** — deterministic generation over an editable DSL.
- **0002** — iterate via lint-critique and domain-repair (repair is a *safety
  net*, not the primary placement mechanism).
- **0003** — **Retired**: historical universal-solver exploration. Its live
  responsibilities are split between 0004 (outer layout) and 0014 (inner placement).
- **0004** — **compose-then-pack** (Accepted; the **outer** layout): each view is
  a *block* = `view_rect(scale) + its annotation boxes`; choose `(scale, page)`
  by a monotone search whose fitness function is composing + packing the blocks
  **disjoint**; build OCC geometry once at the end. Footprints are page-mm
  **box layouts**, never bbox-measured geometry (perf). Byte-identity is **not**
  required — output may change; acceptance = plan-view labels never overlap
  front-view dimensions (CTC-02) + lint clean. Execution (**#121**) **landed** —
  all nine implementation steps done (see the ADR's 2026-07-09 status amendment).
- **0005** — **Accepted (split complete)** (#138): compiler-pipeline module
  boundaries + single-owner build state. `Drawing` stops being the implicit state
  bus; annotation identity/pins/build-issues moved to `registry.py`, coverage
  state to `linting/`, build context (`Analysis`, edge cache) into the pipeline.
  Stages split into `builder`/`analysis`/`compose` (née `sheet`, #640)/`projection`/`linting/`/`repair`/
  `export`/`annotations/` (all #160–#166 landed; `make_drawing.py` 3,907 → ~20
  facade). `layout.py` unchanged. **Roadmap:** `docs/plans/138-module-split-roadmap.md`.
  Both deferred follow-ups are resolved: the §2 build-context threading closed
  via **#639** (epic #635 — one typed `BuildState`, empty-allowlist ratchet), and
  `annotations/envelope.py` was overtaken by the compiler convergence now
  recorded in ADR 0015 (the envelope pass
  converged into `annotations/from_model.py` instead). §4's compat-alias exit is
  tracked by **#720** for 0.4.0.
- **0006** — **Accepted** (#149): deterministic cross-platform layout via bundled,
  path-pinned fonts. Layout depends on measured text width; resolving a font *name*
  (`"Arial"`) substitutes a different font on Linux, drifting the whole sheet ~1 mm.
  draftwright vendors IBM Plex (OFL) and pins it by `font_path` (Plex Mono for
  dimensions, Plex Sans Condensed for title blocks); the helper renders via
  `font_path` (needs `>=0.13.0`). Output changed once for every drawing.
- **0007** — **Accepted, amended by the deployed extraction**: Draftwright owns linting,
  recognition lifecycle/cache, IR conversion, and drafting policy; `b123d-recognisers`
  owns geometry recognition; `build123d-drafting-helpers` is the rendering library.
  (The 0005 golden harness, `tests/test_golden.py`,
  was **retired** here — byte-exact digests are friction during deliberate output
  evolution; regression coverage rests on the geometry-level + `test_e2e_standards`
  suites. See ADR 0005 §3's retirement note.)
- **0008** — **Superseded by 0015** (#697): the compiler-convergence why-trail,
  frozen. Read 0015 for current state.
- **0009** — **Superseded by 0014** (#697): the collect-then-solve why-trail
  (9 amendments), frozen. Read 0014 for current state.
- **0010** — **Accepted; landed**: **annotation provenance seam**.
  The editable-surface epic needs "which annotations did this feature/intent
  produce?" (for `drop`/`dimension`/`finalize`/the #400 emitter). Rather than
  tagging each render pass (the link is lost at the corridor placer, the
  diameter-spec flattening, and the recognition→IR boundary), record
  `intent → [names]` **once** at the intent→render seam, with an `origin` back-link
  on every IR feature was rejected; aspect features retain targeting handles.
  The render seam is the automatic populator and the contract is audit-tested.
- **0011** — **Accepted** (core landed; #62/#462/#495 remain):
  **the IR as a public input** — declare features, don't only detect them.
  `build_drawing(part, model=…)` accepts a caller-supplied `PartModel`/`Sequence[Feature]`
  and **skips detection**; object→feature constructors
  (`model.hole`/`boss`/`step`/`slot`/`pattern`/`envelope`) read a feature's size off the
  build123d object you built (⌀ from the cylindrical face; axis/location from the bbox),
  with an explicit-value flavour. The fluent `Sheet` façade (`draftwright.Sheet`) is the
  "beautiful-Python" surface over the existing renderers. **Aspects geometry can't carry
  are now built:** tolerance/fit ride `DimParameter` (P2a/P2a.2); **GD&T + surface finish**
  are standalone IR features (`ControlFrame`/`DatumRef`/`Finish`, `model/ir.py`) placed as
  first-class ADR 0014 corridor candidates by `render_gdt` (P2b #478), authored via
  `sheet.datum`/`sheet.control(…).position(…)`/`.finish` whose target view+strip derive
  from the referenced feature/face (`declare.gdt_target`, P2c #480/#482). Complete AP242
  geometric tolerances with no modifier, or the export-safe all-around modifier, lower through
  that same `ControlFrame` path (#1095); unsupported modifiers remain provenance-rich raw
  fallbacks, and all-over export is tracked by #1097. Datum lowering remains #62; number-free
  aspects remain #462 and raw-cutter slot reading remains #495. Sidesteps #298 misdetection;
  complements #400 (read + edit → now also input). Roadmap:
  `docs/plans/0011-phase2-aspects-roadmap.md`; #446/#445.
- **0012** — **Accepted; partially landed** (2026-07-08; corrected 2026-07-19):
  user annotation edits are pinned, priority-ranked corridor candidates. A
  `dimension(..., pin=, priority=)` edit records a
  scale-independent *dimension intent* on the model — **pin** = the solver's `anchored`/
  `_ANCHOR_WEIGHT` (stays put while the rest flow around it), **priority** =
  `CorridorCandidate.priority` (#357). `Drawing.finalize()` drains only recorded
  deferred intents through `_PASS_SEQUENCE`; it does not reconstruct auto candidates or
  perform a global auto-plus-user recompose. `place_dim()` remains the deprecated raw-
  coordinate escape hatch. Full recomposition/parity remains #426/#661/#707.
- **0013** — **Accepted** (#568; **Phases 1–2 deployed**): the **uniform recogniser
  contract** — `recognise_<feature>(part, *, <injected deps>) -> list[<frozen
  record>]` (plus the part-less *derived* shape, e.g.
  `recognise_hole_patterns(holes)`), mechanically enforced by
  `tests/test_recogniser_contract.py`; and the typed record→`Feature` converter
  registry in `model/detect.py` (roadmap 1c / #752), whose completeness+uniqueness
  is fail-closed by `tests/test_detect_registry.py`. The shared `b123d-recognisers`
  `v0.1.0a1` package is now the implementation; Draftwright's duplicate modules are deleted.
  Roadmap: `docs/plans/0013-shared-recognisers-roadmap.md`.
- **0014** — **Accepted** (supersedes 0009, #697): **collect-then-solve
  annotation placement as built** — collect every strip occupant as a
  candidate; one solve per strip (select → order(=feature order ⇒
  crossing-free) → space, the PAVA L1 solve); post-#636 the guarantee holds for
  every auto-pass occupant, with the `carve_free_position` exemptions pinned
  fail-closed. Includes the strip/zone/corridor glossary and the
  StripCandidate↔CorridorCandidate layering. Amendment 1 (#740) introduced
  bounded within-pass machined-leader assignment; Amendment 2 (#1166) collects
  compatible sparse ordinary side/plan hole and post-drain machined leaders into
  one canonical late stage (maximum placed, priority, clear-route penalty, then
  leader length), retaining the old lazy greedy result as the resource-cap floor
  without relaxing page/view/title hard constraints and preserving the abandoned
  admitted inventory in state-cap traces. Amendment 3 (#798) prices a shaft
  cutting back through the part into that same Policy-B penalty, measured on one
  filled projected-material lowering **shared with the
  `leader_crosses_silhouette` critique**, so router and lint cannot disagree; it
  is a cost, never an acceptance test, and the resource-cap floor — which is what
  actually runs on dense parts — gained a bounded clear-route lookahead. Amendment 4
  (2026-08-16) records that a work budget must bound **measured** work rather than
  predict it: three guards were found silently disabling the feature they protect on
  ordinary input (the joint assignment never running on any dense part; two balloon
  bounds over by 1.5x and **497x**, the latter refusing a hole table at 0.4% of its
  real cost). Prefer a live counter; an unavoidable pre-check must be exact rather
  than conservative; and a budget that fires is a capability loss that should say so.
- **0015** — **Accepted** (supersedes 0008, #697): **the part-drawing compiler
  as built** — detectors + declared features → the one PartModel waist (two
  tiers, ADR 0013) → planner → render-intents → shared infra; with the
  planner-coverage split (the #698 migrations are complete; correlated
  furniture/aspects remain model-routed by design, while rotational OD/bore
  groups are residual debt tracked by #754) and the lint/coverage carve-out
  stated properly. New kinds must add every applicable IR, planning, rendering,
  coverage, and test surface while keeping orientation data-driven.
- **0016** — **Accepted; epic #867 complete** (PR0–PR8, #868–#876): **declared
  dimensioning intent**. `sheet.dimension(feature, role)` is *referential* — it names
  a measurement and carries no number; the engine still derives the value from the
  geometry and owns placement. Three parts landed:
  - **A build must say where its dimensions come from** (#874, breaking): either
    `auto_dimensions()` (the planner's set, optionally augmented by `add_dimension`)
    or an authored set of `dimension(...)` declarations. Mutually exclusive, because
    "everything the planner chooses, plus these" and "only these" cannot both hold.
  - **Omission from an authored set means suppression** (#876) — on *every* generated
    dimensional path, positions included. A dimension the author cannot address is a
    dimension the author cannot omit, so `location` became addressable per feature
    (`planner.location_datum` is the single eligibility answer; per-*member* identity
    remains #883).
  - **Amendment 1 — the compiled-plan boundary**: renderers may emit dimensional
    content only from `model/compiled.py`'s `RenderableDimensionPlan`. *Suppression is
    not a flag renderers check, it is content they never receive* — `ApprovedDimension`
    has no `suppressed` field. Guarded on both the symptom
    (`tests/test_compiled_plan_boundary.py`: an empty plan draws nothing) and the cause
    (`tests/test_label_provenance.py`: a renderer that formats a number got it as a
    number rather than as the compiler's `value_text`). Hole callouts (`hc_`) are the
    one renderer still on the legacy surface (#926); the label budget drawdown is #927.
  - **Amendment 6 — the converse**: a renderer must emit everything the plan approves,
    and where it cannot place it, must say so. Two failure modes, one rule. (a) An
    authored tolerance reaches the sheet composed into the **label** — helpers resolve
    `label if label is not None else _number_with_units(measured, tolerance)`, and every
    dimension here passes a label, so a forwarded `tolerance=` renders nothing while
    type-checking and reading back correctly from `Dimension.label`. (b) An approved
    dimension that no annotation claims must be reported: a starved overall extent and a
    rung below the legibility floor both used to vanish with the lint clean. The report
    carries the measurement, must not gate the build (reporting through
    `placement_unsatisfiable` made `build_drawing(scale=…)` raise on parts that had always
    built), and is retracted if a later pass draws the measurement after all. Whether
    either should instead be *placed* is an open ADR 0014 question (#1236); reporting is
    not contingent on answering it. The converse is asserted over `plan.groups` and
    `plan.ladders` only — a contingency is deliberately undrawn (Amdt 5) and a location
    carries no tolerance. Guarded by
    `tests/test_issue_1215_no_approved_tolerance_is_dropped.py`, which sweeps every
    parameter of every feature through both `decorations=` key shapes.
  **Not** shipped: the emitter dimension-mirror (phase 4). `emit_sheet_script` refuses a
  model with an authored set rather than silently writing `auto_dimensions()`, because
  naming a feature in a generated script would have to address it by position — #922.
- **0017** — **Accepted with narrowed scope; ownership phase landed, correspondence work is
  evidence-gated by epic #1018**: **the recognition inventory as a first-class result**.
  Recognition stops being a
  scatter of ad-hoc calls: one orchestration per build produces a frozen
  external `RecognitionResult`, held by Draftwright's `RecognitionCache` in `BuildState` and reused by
  model construction *and* by critique. Automatic-path lint reads its inventories off
  `Analysis`; declared-path critique obtains the same aggregate lazily through `BuildState`.
  ADR 0017 §5 explicitly permits both (independence from the *plan* is not independence from
  the *recognition*).
  Phase 1 (#1019) landed the **fail-closed manifest** — `MIGRATED` / `DEFERRED` in
  `b123d_recognisers.result` classify every public `recognise_*` family, and
  `tests/test_recognition_manifest.py` fails when a new one appears without that decision,
  so a recogniser cannot be added and then quietly re-scanned from three call sites. A
  deferral is a `Deferral` reason **code** plus the issue that removes it, not a paragraph:
  prose in a constant CI reads goes stale silently, and the first cut's did. The why-trail
  lives in the blocking issue.
  **#1022** landed the **ADR 0011 declared-path gate**: a declared build now recognises
  **nothing**. It was not one `if` — sizing sources the turned profile and step ladder from
  the declaration (`_declared_turned_profile` / `_declared_step_zs` in `analysis.py`), and
  the lint→repair loop stopped asking for the feature-coverage half it never used
  (`Drawing.lint(physical=False)`; repair acts on `dim_inside_part` alone, ADR 0002).
  Critique on that path still needs an inventory, so `BuildState.ensure_recognition()` builds
  one lazily, once — in the typed build state, not a lint- or `Drawing`-side memo, which
  would make critique a second recognition owner. Exporting a declared drawing pays for that
  one aggregate by design: `export` logs the coverage critique, and suppressing it to reach
  "zero" would trade a user-facing diagnostic for a benchmark number. Two consequences worth
  knowing: lint takes `step_zs`/`pads`/`pockets` from the **aggregate**, never from
  `Analysis` on the declared path (ADR 0015 — critique must not inventory from the model);
  and `recognise_face_levels` migrated into the aggregate as `step_levels`, its
  `NO_INDEPENDENT_CONSUMER` deferral having stopped being true the moment declared-path
  critique needed the geometry ladder. The gate also exposed a latent strip-sizing bug on
  *both* paths: `_est_right_strip_depth` counted ladder steps only, while boss heights share
  that strip — detected builds hid it in the ladder's slack (`_n_right_strip_boss_heights`,
  `compose.py`).
  **Phase 1 is complete.** #1025 split `recognise_step_shoulders` into level-free riser
  evidence the aggregate owns (`recognise_risers`) plus a pure `project_step_shoulders` each
  consumer applies with its own level set — which took *lint's* per-pass rescan to zero, epic
  #1018's third phase-0 guard, and closed a false-negative door on the way (coverage's
  `step_zs=` argument fully determined the shoulder answer, so `step_zs=[]` silenced
  `unrecognised_defining_geometry`). #1026 then migrated the three `BUILD_MODEL_ONLY`
  families once #1022 had removed their cost, and #1028 migrated the last three by moving
  their classification gate *into* the orchestration — the distinction that made it possible
  being that **owning a family and always running it are different things**. In
  b123d-recognisers 0.2.9 chamfers and fillets became unconditional because the package now
  recognises their conical/toroidal turned forms; plates and angled prismatic steps remain
  classification-gated (#1254/#1281).
  **`DEFERRED` is now empty** — every public `recognise_*` family is owned by the one
  orchestration. The mechanism stays fail-closed (a new family must still be classified, and
  every `Deferral` member survives for a future one); what went was each deferral, as its
  stated constraint stopped being true. Measured per family after 0.2.9: a prismatic build
  runs 25 once each, a turned build 23 (the remaining prismatic-only families excluded), a
  declared build/render **zero**. Physical critique or export may then obtain one cached
  aggregate.
  The accepted contract stops there. `BuildState` proves result-to-build provenance; it does
  **not** yet provide recognition-record→IR-feature→requirement correspondence. The original
  four-type identity taxonomy, shared requirements module, general outcome ledger,
  reconciliation stage, and diagnostics model are candidate extensions rather than an
  approved phase sequence. #1018 now requires two end-to-end slices before any of them is
  generalised: flats first (using #1011's fixtures without label/tip/page inference), then
  off-centre slots plus N:1 slot patterns. Each new semantic guard needs the mutation that
  breaks its claimed contract; a green suite alone is not evidence that the guard is
  load-bearing.

- **0019** — **Proposed**: **display-complete labels and a dimension-outcome ledger** —
  finishing the 0016 Amdt 1 boundary after epic #1215/#1216's ten review rounds showed its
  half-built state: `ApprovedDimension` gains `display_text` (tolerance suffix, fit class and
  collapse wording included) so renderers render and never compose; `_tol_suffix` moves below
  the model rank and drawn precision becomes a compile input; dimension outcomes reconcile at
  one end-of-build seam on both routes, replacing the withholding-code/retraction machinery;
  ladder rungs get per-mark identity (amending 0016 Amdt 3). Success criterion is a
  net-negative diff.
- **0018** — **Accepted** (2026-08-16; #1130): **requirement-driven view planning
  and editable sheet layout**. One view-planning model between drawing requirements and
  projection: authored `ViewConstraints` and the automatic planner share one semantic
  `ViewSpec`/layout vocabulary and produce one immutable `ResolvedViewPlan`. Page,
  preferred scale, view set and arrangement are chosen **jointly** against complete view
  blocks measured with fixed paper-space typography; a candidate is feasible only when
  the real shared annotation solve preserves every supported requirement and all blocks
  stay in bounds. Supersedes ADR 0004's **fixed four-view topology** (0004's
  compose-then-pack of each selected block still stands). Users edit whole view blocks,
  never feature-annotation coordinates (ADR 0012/0014 keep those). Infeasibility is a
  first-class `plan_infeasible` result, never a silent relaxation of an authored
  constraint.
  **Nothing is implemented yet** — there is no `ViewSpec`/`ResolvedViewPlan` in the code
  and the engine still builds fixed front/plan/side/iso. The ADR's "Required evidence
  before acceptance" list is the per-slice delivery gate, not waived by acceptance.
  Accepted on converging evidence from #1187 (leaders that cut the part have no clear
  route because the SHEET is full — every remedy is compositional) and #1190 (the
  section is placed into leftover space, so its presence tracks room rather than need).
