# draftwright

Automated technical-drawing generation for [build123d](https://github.com/gumyr/build123d).
Licensed under **AGPL-3.0**. Depends on `build123d-drafting-helpers` for annotation primitives.

## What this is

`draftwright` is the application-level drawing engine. It takes a build123d solid and
produces a fully-annotated multi-view technical drawing (orthographic views, dimensions,
section A–A, ISO hatching, title block) ready for DXF/SVG export.

It sits on top of two Apache 2.0 libraries:
- `build123d-drafting-helpers` — annotation primitives (`Dimension`, `Leader`, `HoleCallout`, …).
  The *rendering* library; draftwright owns feature recognition and linting (ADR 0007).
- `build123d` — the underlying CAD kernel

## Architecture

The dependency graph is a DAG (the #138 / ADR 0005 split is complete). Bottom to
top: leaf modules (`layout.py`, `registry.py`, `fonts.py`, `_geometry.py`,
`fits.py`, `intents.py`, the `linting/` and `recognition/` subpackages) →
`_core.py` → stage modules (`export.py`,
`repair.py`, `projection.py`, `compose.py`, `analysis.py`, `drawing.py`, the
`model/` IR subpackage, the `annotations/` subpackage) → `builder.py` → the
user-facing surfaces: the `make_drawing.py` / `annotate.py` compat facades, the
fluent `Sheet` facade (`sheet.py`), the Sheet-script emitter
(`sheet_emit.py`), and the `cli.py` entry point. No lower module imports an
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

- **`make_drawing.py`** — thin compat facade (~20 lines) re-exporting the public
  surface (`Drawing`, `build_drawing`, `make_drawing`, `generate_script`, `_cli`,
  `FeatureInfo`, `fix_svg_page_size`, `lint_feature_coverage`) so existing imports
  and the `draftwright` CLI entry point keep working. The engine lives in:
  - **`builder.py`** — build orchestration: `build_drawing` (analyse → assemble →
    measure-and-repack → `Drawing`), `make_drawing` (+ export), and the
    editable-script generator (`generate_script`). Imports `drawing`/`analysis`/
    the annotation orchestrator/the stage modules — never `make_drawing` (a DAG).
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
  - **`annotations/sections.py`** — section A–A + detail views (ISO 128-44 arrows,
    ISO 128-50 hatching).
  - **`annotations/balloons.py`** — the leadered hole-balloon pass (#111/#516;
    moved down from `Drawing`, #699). `Drawing.add_balloons` is the public verb
    threading build state in; the band-assignment flow solver lives in `layout.py`.
  - **`annotations/_common.py`** — the ADR 0014 corridor-solve engine
    (`CorridorCandidate`, `solve_corridor`, `register_corridor`/`drain_corridors`,
    `place_strip_candidates`, `PlacementContext`) plus `_box_hits`, at the
    bottom of the annotations DAG. (The bbox/segment primitives it delegates to
    live in `_core`/`_geometry` since #700.)
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
  `_boxes_overlap`, the two segment/box tests); the DAG's bottom leaf (guarded
  by `test_geometry_is_a_leaf`) so the IR waist uses them without importing
  `_core`.
- **`fits.py`** — the ISO 286 fit tables (`fit_deviation`, `FitClass`; ADR 0011
  P2a.2): a rank-0 leaf consumed by `_core`, `model/ir` and `sheet`.
- **`intents.py`** — the deferred-placement "low IR" behind `Drawing.finalize()`
  (#426): a dependency-free leaf recording edit-verb intents for the recompose
  (deliberately stringly-typed in its Phase-1 form).
- **`registry.py`** — `AnnotationRegistry`: the single owner of annotation
  identity/ownership/pins/build-issues (#138 / ADR 0005, Step 2). `Drawing`
  delegates here and keeps the render list; `_named`/`_anno_view`/`_pinned`/
  `_build_issues` remain `Drawing` properties during the migration.
- **`linting/`** — the lint subpackage (#138 / ADR 0005; ADR 0007: draftwright
  owns linting): `coverage.py` (`lint_feature_coverage` + `CoverageState`),
  `structural.py` (geometry/standards checks), `issues.py` (the `LintIssue` type),
  `suggest.py` (`_suggest_fix`, #29 snippets). Depends only on `_core`,
  `recognition/` (typed hole records in `coverage.py`) + build123d_drafting. `_QUOTED_RE` (a lint-message label regex shared with the
  repair loop) lives in `_core`.
- **`model/`** — the ADR 0015 IR waist: `ir.py` (the `Feature`/`DimParameter`/
  `Datum`/`PartModel` types — the one inventory), `detect.py` (detectors →
  `Feature` objects, adapting `recognition/`), `planner.py` (`plan_dimensions` —
  one rule set → a `DimensionGroup` per feature, + `plan_sections`), and
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
  (`_assemble`'s geometry half; #161).
- **`sheet.py`** — the fluent declarative **`Sheet`** facade (ADR 0011):
  feature verbs (`hole`/`boss`/`slot`/…), aspect verbs (`.tolerance`/`.fit`/
  `.finish`), GD&T (`datum`/`control`). Facade tier: builds a `PartModel` via
  `model/declare.py` and calls `build_drawing(model=…)`. Née `sheet_dsl.py`
  (renamed #640 — it's a fluent facade, not a DSL, per ADR 0001; a deprecated
  `sheet_dsl` alias shim remains until 0.4.0).
- **`sheet_emit.py`** — the Sheet-script emitter behind `--script --style sheet`:
  generates an editable `Sheet` script from a detected model. Facade tier;
  imports `builder` downward at module level. The old builder→cli→sheet_emit
  lazy cycle is **gone** (#523): the `_cli` compat shim moved from `builder` to
  `cli.py` (beside the Typer `app`), so `builder` no longer imports `cli` and
  `_LAZY_UPWARD_EXEMPT` is now empty. The graph is a plain DAG —
  `cli → {builder, sheet_emit}`, `sheet_emit → builder`, `builder → ∅`.
- **`score.py`** — `feature_census` (#148f/#608): a standalone
  recognition-completeness measurement tool; depends only on `recognition/` +
  build123d, and nothing in the engine imports it. Ranked 0 in `_LAYERS` (#704),
  so that leaf status is machine-enforced.
- **`recognition/`** — feature recognition (ADR 0007: draftwright owns it, not
  helpers). Every feature recogniser follows the **uniform contract** (ADR 0013 / #568,
  spelled out in `recognition/__init__.py`): `recognise_<feature>(part, *, <injected
  inventory>) -> list[<frozen-dataclass record>]` — British `recognise_` verb,
  keyword-only args (deps injected by the caller, never re-recognised), a deterministic
  list of records. Where a record first looks too thin (a face level, a turned step) the
  fix is the record — `recognise_face_levels -> list[FaceLevel]`, `recognise_turned_steps
  -> list[TurnedStep]` (each step carries its `axis`; `TurnedProfile` survives only as a
  pipeline aggregate). `_features.py` (vendored from `build123d_drafting.features`; the
  hole/boss/pattern recognisers — `recognise_holes`/`recognise_bosses`/
  `recognise_hole_patterns` + the feature/pattern types; plus the cylinder-analysis
  *substrate* `analyse_cylinders`/`feature_diameters`/`full_cylinders`, which keep their
  names — a tuple-of-dicts / diameter query, not `list[record]` recognisers),
  `slots.py` (the milled-slot + pocket recognisers, #135/#148), `turned.py`
  (`recognise_turned_steps` — turned-shaft shoulders, OD-silhouette filtered),
  `levels.py` (`recognise_face_levels` prismatic horizontal face levels +
  `recognise_step_shoulders` → `StepShoulder`, #191/#555), the #148-epic
  recognisers `chamfers.py`/`fillets.py`/`flats.py`/`grooves.py`/`plates.py`/
  `countersinks.py`, and `_record.py` (the shared frozen-`Record` mixin,
  `.to_dict()`). Bottom of the DAG: depends only on build123d/OCP. Import
  via the package surface.
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
- **`repair.py`** — the deterministic lint→repair loop (#30 / ADR 0002): the
  re-place helpers (`_find_dim`/`_replace_dim`/`_repair_*`/`repair_drawing`) take
  the drawing duck-typed as `dwg`; `Drawing.repair()` stays a thin wrapper.
  Depends only on `_core`.
- **`pmi.py`** — PMI (product manufacturing information) extraction from STEP AP242.

## Architecture decisions — READ `docs/adr/` FIRST

**Before any change to layout, scaling, page selection, annotation placement, or
generation strategy, read `docs/adr/` and follow the accepted ADRs.** They are
the source of truth for *why* the engine is shaped the way it is; do not
re-derive or contradict them. If a change conflicts with an ADR, amend the ADR
in the same PR (status, reasoning, date) rather than silently diverging — and if
a decision turns out wrong, record that too.

**Assess architectural fit — always.** An issue, a PR, and a review are each
incomplete until they weigh the change against the ADRs, not just its local
correctness. Ask: does this feature round-trip **all** the surfaces its kind
requires — recognise **+** emit **+** declare (ADR 0011 / epic #574; no
recognition-only debt)? Does it fit the compiler pipeline and one-inventory waist
(ADR 0015)? Does it conform to the recogniser contract (ADR 0013)? Does it sit
where it belongs in the DAG, or re-introduce a coupling an ADR removed? Does it
place geometry through the corridor solve rather than around it, and extend a
shared pass rather than adding another copy (consolidation epic #635)? A change
that is locally correct but architecturally off-pattern *is* tech debt — call it
out in the issue/PR/review, not after merge.

When an ADR has grown unwieldy with amendments, read its **Current decision**
header first (the amended state in one place); the amendments below are the *why*
trail. Once an ADR passes ~3–4 amendments, prefer a new **superseding** ADR over
amendment N+1.

Current ADRs:
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
- **0007** — **Accepted** (deprecate-and-vendor): draftwright owns feature
  recognition (`recognition/`) and linting (`linting/`); `build123d-drafting-helpers`
  becomes the rendering library. (The 0005 golden harness, `tests/test_golden.py`,
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
  from the referenced feature/face (`declare.gdt_target`, P2c #480/#482). PMI-sourced
  auto-GD&T remains #62; number-free aspects remain #462 and raw-cutter slot reading
  remains #495. Sidesteps #298 misdetection; complements #400 (read + edit → now also
  input). Roadmap: `docs/plans/0011-phase2-aspects-roadmap.md`; #446/#445.
- **0012** — **Accepted; partially landed** (2026-07-08; corrected 2026-07-19):
  user annotation edits are pinned, priority-ranked corridor candidates. A
  `dimension(..., pin=, priority=)` edit records a
  scale-independent *dimension intent* on the model — **pin** = the solver's `anchored`/
  `_ANCHOR_WEIGHT` (stays put while the rest flow around it), **priority** =
  `CorridorCandidate.priority` (#357). `Drawing.finalize()` drains only recorded
  deferred intents through `_PASS_SEQUENCE`; it does not reconstruct auto candidates or
  perform a global auto-plus-user recompose. `place_dim()` remains the deprecated raw-
  coordinate escape hatch. Full recomposition/parity remains #426/#661/#707.
- **0013** — **Accepted** (#568; **Phase 1 complete**): the **uniform recogniser
  contract** — `recognise_<feature>(part, *, <injected deps>) -> list[<frozen
  record>]` (plus the part-less *derived* shape, e.g.
  `recognise_hole_patterns(holes)`), mechanically enforced by
  `tests/test_recogniser_contract.py`; and the typed record→`Feature` converter
  registry in `model/detect.py` (roadmap 1c / #752), whose completeness+uniqueness
  is fail-closed by `tests/test_detect_registry.py`. The shared `b123d-recognisers`
  package is the deferred Phase-2 deployment (gated on a second committed consumer).
  Roadmap: `docs/plans/0013-shared-recognisers-roadmap.md`.
- **0014** — **Accepted** (supersedes 0009, #697): **collect-then-solve
  annotation placement as built** — collect every strip occupant as a
  candidate; one solve per strip (select → order(=feature order ⇒
  crossing-free) → space, the PAVA L1 solve); post-#636 the guarantee holds for
  every auto-pass occupant, with the `carve_free_position` exemptions pinned
  fail-closed. Includes the strip/zone/corridor glossary and the
  StripCandidate↔CorridorCandidate layering.
- **0015** — **Accepted** (supersedes 0008, #697): **the part-drawing compiler
  as built** — detectors + declared features → the one PartModel waist (two
  tiers, ADR 0013) → planner → render-intents → shared infra; with the
  planner-coverage split (the #698 migrations are complete; correlated
  furniture/aspects remain model-routed by design, while rotational OD/bore
  groups are residual debt tracked by #754) and the lint/coverage carve-out
  stated properly. New kinds must add every applicable IR, planning, rendering,
  coverage, and test surface while keeping orientation data-driven.

## Dependencies

- `build123d-drafting-helpers>=0.13.0` (Apache 2.0)
- `build123d>=0.9.0` (Apache 2.0)
- Export render chain: `reportlab` (BSD) + `svglib` (LGPL) for PDF; `pypdfium2`
  (Apache-2.0, wrapping Google PDFium BSD-3) + `pillow` (HPND) for PNG. All
  pure-wheel (no native cairo) and — except svglib's weak-copyleft LGPL — permissive,
  so the render path stays dual-license-friendly.

The 1D strip solve (`layout.py`) is the dependency-free minimum-total-leader-length
**PAVA** algorithm (`_solve_strip_1d_pava`, ADR 0009 Amdt 4); the earlier Cassowary
(`kiwisolver`) constraint-satisfaction solve was retired once PAVA gave the exact
L1 placement, so `kiwisolver` is no longer a direct dependency.

## Testing

Tests are geometry-level — edge counts, bbox placement, face counts, lint clean
checks. Target is 100% passing. Tiers (#153):

- **`uv run pytest -m smoke`** (~30 s) — curated build-light subset for a quick
  local "did I break something obvious" check.
- **`uv run pytest`** — full fast tier (`-m 'not slow'`; nearly every test does a
  real OCC build). Prefer **targeted** selections (`-k`, node ids) locally; for a
  full local run add **`-n auto --dist loadscope`** (pytest-xdist) to spread it
  across cores (~471 s → ~200 s on 8 cores, #153).
- **`-m slow`** (CTC fixture builds) — CI-only.

Coverage is kept out of the default addopts (it adds ~13% locally); the CI
workflow passes the `--cov` flags. CI runs the full fast tier (3×3 OS/Python
matrix, parallelised with `-n auto`) on every PR; the **slow tier runs post-merge
on `main`**, not as a PR gate (#153) — a regression there is caught right after
merge rather than blocking every PR for ~19 min.

## License

AGPL-3.0. Anyone running draftwright as a network service must provide their
application's source code. Contact pzfreo@gmail.com for a commercial licence.
