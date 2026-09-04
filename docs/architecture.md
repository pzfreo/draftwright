# Architecture — the detailed module map

The module-by-module map of the draftwright engine, moved out of `CLAUDE.md`
(which keeps the compact map and the working rules). The machine-enforced
authority for the layering is `tests/test_import_boundaries.py` (`_LAYERS`);
keep that table, this document, and `CLAUDE.md`'s compact map in step. The
*why* behind every shape here lives in `docs/adr/`.

## The module map

The dependency graph is a DAG (the #138 / ADR 1 (was 0005) split is complete). Bottom to
top: leaf modules (`layout.py`, `registry.py`, `fonts.py`, `_geometry.py`,
`fits.py`, `intents.py`, `recognition_cache.py`, `recognition_ownership.py`,
`plate_correspondence.py`, `recogniser_policy.py`, `recogniser_schema.py`,
`recognition_frame.py`, and the strict `blend_contract.py` provider-record boundary) →
`_core.py` → stage modules (`export.py`,
`repair.py`, `projection.py`, `compose.py`, `analysis.py`, `drawing.py`, `reporting.py`,
the `linting/` subpackage, the `model/` IR subpackage, the `annotations/` subpackage) →
`builder.py` → the
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
    generator, was retired by #940 — ADR 4 (was 0016) phase 6 / ADR 4 (was 0001 Amdt 2) — and
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
    by `test_drawing_encapsulation`. ADR 1 (was 0005 §2) / #639 closed: `annotations/`
    has zero private `Drawing` reads (empty allowlist ratchet) — and since #699
    slice d the state-bus guard covers the WHOLE engine: no module but
    `drawing.py` touches `dwg._*` (rationale-carrying allowlist, builder's
    fill site only).)*
- **`annotate.py`** — thin compat facade re-exporting `_auto_annotate` (the
  orchestrator) from `annotations/`. The annotation passes were split into the
  **`annotations/`** subpackage (#164 / ADR 1 (was 0005), P5):
  - **`annotations/orchestrator.py`** — `_auto_annotate`, the single entry point
    (called by `build_drawing`); classifies the part and drives the render passes
    + title block. Owns **`_PASS_SEQUENCE`** — the ONE canonical stage order
    (#699 slice b): `_auto_annotate` and `Drawing._drain_intents` (the finalize
    drain) both hand name→thunk dicts to the shared `run_stages`, so the two
    build paths cannot diverge in sequencing (the drain step itself is the
    shared `drain_and_reconcile`). The current ADR 1 (was 0015) shape is
    `build model → plan/model-routed intents → render`; some inline engine code remains — chiefly
    `_maybe_tabulate_holes` (the hole-table/balloon escalation resolver) and the
    iso right-strip outer-limit tightening — pending the last convergence steps.
  - **`annotations/from_model.py`** — the **IR render layer** (largest annotations
    module): turns the planner's `DimensionGroup`/render-intents into placed
    dimensions/callouts/centre marks/section triggers. This is where the turned,
    PMI/GD&T, envelope/OD, centre-mark and step-length passes converged (ADR 1 (was 0015),
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
  report a perfect legibility quality score. ADR 2 (was 0014 Amendment 3) (#798) adds
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
  - **`annotations/_common.py`** — the ADR 2 (was 0014) corridor-solve engine
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
  the ADR 2 (was 0014) collect-then-solve entry point), the 2D free-rectangle placer
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
  `_core`. Also the ADR 2 (was 0014 Amendment 3) **filled material field** (#798):
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
- **`fits.py`** — the ISO 286 fit tables (`fit_deviation`, `FitClass`; ADR 4 (was 0011)
  P2a.2): a rank-0 leaf consumed by `_core`, `model/ir` and `sheet`.
- **`intents.py`** — the deferred-placement "low IR" behind `Drawing.finalize()`
  (#426): a dependency-free leaf recording edit-verb intents for the recompose
  (deliberately stringly-typed in its Phase-1 form).
- **`registry.py`** — `AnnotationRegistry`: the single owner of annotation
  identity/ownership/pins/build-issues (#138 / ADR 1 (was 0005), Step 2). `Drawing`
  delegates here and keeps the render list. The `_named`/`_anno_view`/`_pinned`/
  `_build_issues` aliases on `Drawing` (and coverage's three) were **deleted** at
  their §4 date (#720): reach the state through `dwg.registry` (`in reg`,
  `names()`, `issues`, `restore_issues()`) and `dwg.coverage`. Their absence is
  asserted by `test_the_expired_compat_aliases_stay_deleted`.
- **`linting/`** — the lint subpackage (#138 / ADR 1 (was 0005); ADR 3 (was 0007): draftwright
  owns linting): `coverage.py` (`lint_feature_coverage` + `CoverageState`),
  `structural.py` (geometry/standards checks), `issues.py` (the `LintIssue` type),
  `gear_coverage.py` (declared gear table/profile reconciliation), and `suggest.py`
  (`_suggest_fix`, #29 snippets). Depends only on `_core`, the pure
  `plate_correspondence` leaf,
  `b123d_recognisers` (typed hole records in `coverage.py`) + build123d_drafting.
  `_QUOTED_RE` (a lint-message label regex shared with the
  repair loop) lives in `_core`.
- **`recognition_cache.py`** — Draftwright's ADR 3 (was 0017) one-result lifecycle owner. Raw automatic
  analysis seeds it with one external `build_recognition_evidence(part)` acquisition; on a lazy
  declared critique the empty cache makes that call itself. It retains the exact
  `RecognitionResult` projection and evidence authority together. The package owns recognition,
  while Draftwright owns when the pair is computed and reused. A bare or explicit framed result
  remains valid without evidence; the cache never rescans to backfill it.
- **`recognition_ownership.py`** — the run-local consumer ledger below/beside the ADR 1 (was 0015) IR
  waist. During record→IR conversion it binds opaque provider occurrences to the exact IR
  feature objects that represent or absorb them, by same-run record identity and explicit
  aggregate membership. Ownerless outcomes are projected from the existing consumer capability
  declaration, not a second policy table. It exposes no persistent topology/order/address ID.
  Unconditional 1:1 adapters, singleton/grouped/pattern hole/slot/pocket members, nested
  countersinks, conditional channel/turned-step owners, direct or multi-feature through-step
  owners, and settled
  unsupported/deferred/evidence-only occurrences are classified; remaining conditional
  cross-family records stay unclassified.
- **`plate_correspondence.py`** — pure shared Plate-record/final-IR correspondence predicates.
  Model assembly uses their feature dependency sets only while recording exact same-run
  occurrence ownership; completeness lint uses the same predicates for requirement ownership.
  The leaf imports neither consumer, recogniser implementation, nor drawing state.
- **`recogniser_policy.py`** — the rank-0 Draftwright-owned source for reviewed unsupported,
  deferred, and geometry-only family policy. Both the cross-repository capability declaration and
  the run-local occurrence ledger project this same immutable data; recognition remains
  geometry-only and no engine leaf imports the rank-7 contract validator.
- **`recogniser_schema.py`** — the rank-0 Draftwright-owned table of public provider record
  schema versions consumed by adapters. The report projector and rank-7 cross-repository
  validator share this leaf, so the engine never imports the validator to learn schema metadata.
- **`reporting.py`** — the rank-0 pure schema-v1 report and generation-snapshot projector.
  `Drawing` owns all report reads from its `BuildState`, while generated Python passes the exact
  retained evidence, ownership, detected model, and source digest explicitly. Reporting never
  reaches through `Drawing` private state or triggers recognition, placement, or export.
- **`recognition_frame.py`** — the ADR 3 (was 0020) prepared local-frame boundary. It calls the public
  provider preparation seam, classifies the exact normalized solid from its already-scanned
  cylinders, runs one paired aggregate, propagates typed refusal without fallback, and exposes
  conservative FULL/ORTHOGONAL/AXIAL semantic policy. Analysis calls it only for the explicit
  `framed_recognition=True` rollout path and owns any visible raw fallback above this leaf.
- **`blend_contract.py`** — the strict leaf boundary for released schema-v3 straight/circular
  `Blend` path records.
  It rejects widened, mutable, non-finite, non-canonical, and unreleased values and owns the
  exact occurrence key shared by conversion and completeness lint.
- **`recogniser_contract.py`** — the fail-closed cross-repository capability join. It consumes
  only the installed `b123d-recognisers` public manifest and rank-0 consumer policy, then validates
  Draftwright-owned IR,
  `Sheet`, generated-code, drawing, completeness, and documentation declarations. It is rank 7
  because validation dynamically resolves implementation references across every lower layer;
  package geometry policy never imports or owns this consumer overlay.
- **`inspection_contract.py`** — the separate fail-closed join for declared-feature geometry
  reads. It validates inspection manifest format 1/API major 1, the exact installed recogniser
  release, and only the stable `b123d_recognisers.inspection` symbols and value schemas consumed
  by `model/declare.py`. It deliberately does not declare recognition-family semantics.
- **`model/`** — the ADR 1 (was 0015) IR waist: `ir.py` (the `Feature`/`DimParameter`/
  `Datum`/`PartModel` types — the one inventory), `detect.py` (detectors →
  `Feature` objects, adapting `b123d_recognisers` records), `planner.py`
  (`plan_dimensions` —
  one rule set → a `DimensionGroup` per feature, + `plan_sections`; and, since #1154,
  the one cross-feature reconciliation: two features measuring between the same two
  support planes state one fact, so the overall extent keeps it and the feature-local
  one records where it went), and
  `declare.py` (ADR 4 (was 0011) object→feature constructors: `hole`/`boss`/`step`/… read
  a feature's size off the build123d object — a second, *declared* front-end into
  the same IR the detectors fill). The narrow middle of the compiler hourglass;
  consumed by `annotations/from_model.py`.
- **`compose.py`** — the ADR 2 (was 0004) **outer** compose-then-pack layout engine
  (`choose_scale`, `ViewBlock`, zone/strip depths). Née `sheet.py`; renamed
  (#640) so the layout engine stops shadowing the user-facing `Sheet` facade
  (which now owns the `sheet.py` name).
- **`analysis.py`** — the `_analyse` stage: solid classification, the one-shot
  feature-inventory detection (ADR 1 (was 0015)), view sizing, and the strip/zone
  model (`fv_zones`/`pv_zones`/`sv_zones`) that ADR 2 (was 0014) placement reads.
- **`projection.py`** — HLR projection and view-coordinate transforms
  (`_assemble`'s geometry half; #161). Also the #798 **material lowering**:
  `part_material_mesh` tessellates the part once per build and
  `view_material_field` projects that one mesh per view, so every view measures
  the same material. The mesh is taken under explicit control (copy →
  `BRepTools.Clean_s` → `BRepMesh_IncrementalMesh`) because OCC caches a
  triangulation on the shape and returns it for any later request — even a finer
  one — which would make the field a function of build *history* (the ADR 5 (was 0006)
  hazard). `Drawing.material_fields()` holds the result on `BuildState`.
  `_fit_iso_view` **returns** the iso bbox when the fitted iso is off sheet scale and
  therefore needs an NTS caption, else `None`; it does not place that caption. `builder`
  does, at the common post-fit point *before* `render_gear_tables` — the caption is tied
  to the block it labels while a table may sit anywhere, so the constrained furniture
  claims space first (#1197).
- **`sheet.py`** — the fluent declarative **`Sheet`** facade (ADR 4 (was 0011)):
  feature verbs (`hole`/`boss`/`slot`/…), aspect verbs (`.tolerance`/`.fit`/
  `.finish`), GD&T (`datum`/`control`). Facade tier: builds a `PartModel` via
  `model/declare.py` and calls `build_drawing(model=…)`. Née `sheet_dsl.py`
  (renamed #640 — it's a fluent facade, not a DSL, per ADR 4 (was 0001); the `sheet_dsl`
  alias shim was deleted at 0.4.0, #720).
- **`sheet_emit.py`** — **the** script emitter, behind `--script` (#940 retired the
  imperative alternative): generates an editable `Sheet` script from a detected
  model — one named binding per feature, an explicit dimension source, and a bounded
  generation-time recognition-gap snapshot. Facade tier; imports `builder` and the pure
  `reporting` projector downward at module level. The old builder→cli→sheet_emit
  lazy cycle is **gone** (#523): the `_cli` compat shim moved from `builder` to
  `cli.py` (beside the Typer `app`), so `builder` no longer imports `cli` and
  `_LAZY_UPWARD_EXEMPT` is now empty. The graph is a plain DAG —
  `cli → {builder, sheet_emit}`, `sheet_emit → {builder, reporting}`, `builder → ∅`.
- **`score.py` / `recognition/`** — temporary public compatibility re-exports of
  `b123d_recognisers`, identity-preserving and scheduled for removal in 0.6.0. There are no
  embedded recogniser modules. Engine code imports the external package directly; private
  historical `draftwright.recognition.*` paths are intentionally unsupported.
- **`b123d_recognisers` (external)** — the ADR 3 (was 0013) geometry-only bottom layer: uniform
  deterministic `recognise_*` functions, frozen serialisable records, shared substrates,
  `RecognitionResult` orchestration/manifest, repeating-profile correspondence, and
  `feature_census`. It imports build123d/OCP and never imports Draftwright.
- **`fonts.py`** — vendored, path-pinned IBM Plex fonts for deterministic
  cross-platform layout (ADR 5 (was 0006)).
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
- **`repair.py`** — the deterministic lint→repair loop (#30 / ADR 5 (was 0002)): the
  re-place helpers (`_find_dim`/`_replace_dim`/`_repair_*`/`repair_drawing`) take
  the drawing duck-typed as `dwg`; `Drawing.repair()` stays a thin wrapper.
  Depends only on `_core`.
- **`pmi.py`** — PMI (product manufacturing information) extraction from STEP AP242.
  Owns the XCAF source census and overlays `_pmi_part21` facts only after exact
  correspondence. It inventories the complete OCCT geometric-tolerance modifier vocabulary,
  preserves unsupported facts in the raw IR fallback, and exposes explicit blockers to typed
  lowering; XCAF and Part21 source identities survive both paths.

## Architecture decisions

The five live records in `docs/adr/` own every decision this map implements; each names the
tests that guard it. This document is the module map only — where things live and what imports
what, kept in step with `tests/test_import_boundaries.py`. It does not restate the decisions.
