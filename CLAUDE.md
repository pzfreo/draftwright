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
top: leaf modules (`layout.py`, `registry.py`, `fonts.py`, the `linting/` and
`recognition/` subpackages) → `_core.py` → stage modules (`export.py`,
`repair.py`, `projection.py`, `compose.py`, `analysis.py`, `drawing.py`, the
`model/` IR subpackage, the `annotations/` subpackage) → `builder.py` → the
user-facing surfaces: the `make_drawing.py` / `annotate.py` compat facades, the
fluent `Sheet` facade (`sheet.py`), the Sheet-script emitter
(`sheet_emit.py`), and the `cli.py` entry point. No lower module imports an
upper one. (All surfaces are front doors onto the one engine,
`build_drawing` → `_auto_annotate` — there is no second engine.)

- **`make_drawing.py`** — thin compat facade (~17 lines) re-exporting the public
  surface (`Drawing`, `build_drawing`, `make_drawing`, `generate_script`, `_cli`,
  `FeatureInfo`, `fix_svg_page_size`, `lint_feature_coverage`) so existing imports
  and the `draftwright` CLI entry point keep working. The engine lives in:
  - **`builder.py`** — build orchestration: `build_drawing` (analyse → assemble →
    measure-and-repack → `Drawing`), `make_drawing` (+ export), and the
    editable-script generator (`generate_script`). Imports `drawing`/`analysis`/
    the annotation orchestrator/the stage modules — never `make_drawing` (a DAG).
    *(The CLI moved out to `cli.py`; `_cli` is now a thin shim.)*
  - **`cli.py`** — the Typer command-line interface (#289): argument parsing,
    `--version`, shell completion, `--format`, rich help. The engine (build123d)
    is imported **lazily inside the command body** so completion/`--help`/
    `--version` stay sub-second (#313). Entry point: `draftwright.cli:app`.
  - **`drawing.py`** — the `Drawing` result object (`.lint()`/`.add()`/`.place_dim()`/
    `.repair()`/`.export*()`; delegates identity to `registry`, coverage to `lint`)
    plus `_build_table` and `FeatureInfo`. Sits below `builder` (which constructs it).
    *(The build context `_analysis`/`_view_edge_cache` still lives on `Drawing`;
    threading it through an explicit placement context, ADR 0005 §2, is tracked
    by #639 in the #635 consolidation epic.)*
- **`annotate.py`** — thin compat facade re-exporting `_auto_annotate` (the
  orchestrator) from `annotations/`. The annotation passes were split into the
  **`annotations/`** subpackage (#164 / ADR 0005, P5):
  - **`annotations/orchestrator.py`** — `_auto_annotate`, the single entry point
    (called by `build_drawing`); classifies the part and drives the render passes
    + title block. End state (ADR 0008) is `build model → plan → render`; a little
    inline engine code (some envelope/step-ladder placement) remains here pending
    the last convergence steps.
  - **`annotations/from_model.py`** — the **IR render layer** (largest annotations
    module): turns the planner's `DimensionGroup`/render-intents into placed
    dimensions/callouts/centre marks/section triggers. This is where the turned,
    PMI/GD&T, envelope/OD, centre-mark and step-length passes converged (ADR 0008,
    #200/#208/#237) — the old per-feature `annotations/{turned,pmi}.py` modules
    were deleted as each migrated here.
  - **`annotations/holes.py`** — hole/pattern callouts, balloons, location dims
    (incl. side-drilled #133), pitch/grid dims, slots (the largest *pass*).
  - **`annotations/sections.py`** — section A–A + detail views (ISO 128-44 arrows,
    ISO 128-50 hatching).
  - **`annotations/_common.py`** — shared placement helpers (`_anno_box`,
    `_occupied_boxes`, `_box_hits`) at the bottom of the annotations DAG.
  Submodules import only down or sideways — `_core`/`layout`/`analysis`/
  `projection`/the `model/` IR/`linting.structural`/third-party, never
  `annotate`/`make_drawing`/`drawing` (the drawing is duck-typed as `dwg`) — so
  the orchestrator calls down with no cycle.
- **`_core.py`** — shared primitives below both `make_drawing.py` and `annotate.py`:
  the `Analysis` namespace and its field types (`_Projector`, `Strip`, `ViewZones`),
  the dimension/format helpers (`_dim`, `_fmt`, `_add_title_block`, …), and the
  page/slot/margin layout constants.
- **`layout.py`** — the constraint-based layout engine (ADR 0003): the deterministic
  1D PAVA strip solve (`_solve_strip_1d_pava`, plus `plan_strip`/`StripCandidate`,
  the ADR 0009 collect-then-solve entry point) and the 2D free-rectangle placer
  (`place_box`/`fit_box`). Sits *below* the domain API.
- **`registry.py`** — `AnnotationRegistry`: the single owner of annotation
  identity/ownership/pins/build-issues (#138 / ADR 0005, Step 2). `Drawing`
  delegates here and keeps the render list; `_named`/`_anno_view`/`_pinned`/
  `_build_issues` remain `Drawing` properties during the migration.
- **`linting/`** — the lint subpackage (#138 / ADR 0005; ADR 0007: draftwright
  owns linting): `coverage.py` (`lint_feature_coverage` + `CoverageState`),
  `structural.py` (geometry/standards checks), `issues.py` (the `LintIssue` type),
  `suggest.py` (`_suggest_fix`, #29 snippets). Depends only on `_core` +
  build123d_drafting. `_QUOTED_RE` (a lint-message label regex shared with the
  repair loop) lives in `_core`.
- **`model/`** — the ADR 0008 IR waist: `ir.py` (the `Feature`/`DimParameter`/
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
- **`sheet.py`** — the fluent declarative **`Sheet`** facade (ADR 0011):
  feature verbs (`hole`/`boss`/`slot`/…), aspect verbs (`.tolerance`/`.fit`/
  `.finish`), GD&T (`datum`/`control`). Facade tier: builds a `PartModel` via
  `model/declare.py` and calls `build_drawing(model=…)`. Née `sheet_dsl.py`
  (renamed #640 — it's a fluent facade, not a DSL, per ADR 0001; a deprecated
  `sheet_dsl` alias shim remains for one release).
- **`sheet_emit.py`** — the Sheet-script emitter behind `--script --style sheet`:
  generates an editable `Sheet` script from a detected model. Facade tier;
  #523 tracks breaking its lazy-import cycle with `builder`/`cli`.
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
  `slots.py` (the milled-slot recogniser, #135), `turned.py` (`recognise_turned_steps`
  — turned-shaft shoulders, OD-silhouette filtered), and `levels.py`
  (`recognise_face_levels` prismatic horizontal face levels + `recognise_step_shoulders`
  → `StepShoulder`, #191/#555). Bottom of the DAG: depends only on build123d/OCP. Import
  via the package surface.
- **`fonts.py`** — vendored, path-pinned IBM Plex fonts for deterministic
  cross-platform layout (ADR 0006).
- **`export.py`** — SVG/DXF/PDF export + post-processing (page-size fix,
  attribution hyperlink/metadata, DXF metadata, arc sanitisation, element-wise
  shape-export degradation, pure-Python PDF render via svglib + reportlab — no
  native cairo, #288). The first **module-split** step of
  #138 (ADR 0005): `Drawing.export()` / `export_pdf()` stay as thin wrappers.
  Sits below `make_drawing.py`, above `_core.py`.
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
(ADR 0008)? Does it conform to the recogniser contract (ADR 0013)? Does it sit
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
- **0003** — constraint-based **inner** layout (the deterministic PAVA strip
  solve in `layout.py`): placing one view's annotations within its own zones.
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
  `export`/`annotations/` (all #160–#166 landed; `make_drawing.py` 3,907 → ~17
  facade). `layout.py` unchanged. **Roadmap:** `docs/plans/138-module-split-roadmap.md`.
  One deferred follow-up remains: full build-context threading off `Drawing`
  (§2), tracked by **#639** (consolidation epic #635). (The other —
  `annotations/envelope.py` — was overtaken by ADR 0008: the envelope pass
  converged into `annotations/from_model.py` instead.)
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
- **0008** — **Accepted, migration complete** (one path, 2026-06-30): the
  part-drawing **compiler** — detectors → a Feature/DimParameter **IR/PartModel**
  → a dimensioning **planner** → render-intents → the shared layout/projection/
  export infra. One feature inventory, detected once; orientation/feature-kind are
  *data in the IR*, not code branches. Roadmaps: `docs/plans/0008-*-roadmap.md`.
- **0009** — **Accepted, largely implemented** (Amendments 1–9; supersedes/
  subsumes #150): **collect-then-solve** per-strip annotation placement
  (boundary labeling). Strip passes stop placing-as-they-go;
  every strip occupant is collected as a candidate and one solve per strip does
  select → assign → order(=feature order ⇒ crossing-free) → space. Removes the
  invisible-occupant collision class (#133/#225/#305) by construction; the inner
  per-view layer to 0004's outer block packing; consumes 0008's render-intents.
  **Remaining migration:** a handful of render passes (plates, height ladder,
  step positions, some hole helpers) still place via the solver-invisible
  `carve_free_position` — tracked by **#636** (consolidation epic #635); the
  by-construction guarantee holds only once those join the solve.
  Research: `docs/research/annotation-placement-boundary-labeling.md`. Roadmap:
  `docs/plans/strip-layout-boundary-labeling-roadmap.md`.
- **0010** — **Accepted** (decision; work pending): **annotation provenance seam**.
  The editable-surface epic needs "which annotations did this feature/intent
  produce?" (for `drop`/`dimension`/`finalize`/the #400 emitter). Rather than
  tagging each render pass (the link is lost at the corridor placer, the
  diameter-spec flattening, and the recognition→IR boundary), record
  `intent → [names]` **once** at the intent→render seam, with an `origin` back-link
  on IR features. The registry's `_anno_feature` (#398b) is the sink; the seam is
  the automatic populator. Re-plans #398c–e, enables #400.
- **0011** — **Accepted** (Phase 0+1 + Phase 2 aspects landed; P2d PMI-source pending):
  **the IR as a public input** — declare features, don't only detect them.
  `build_drawing(part, model=…)` accepts a caller-supplied `PartModel`/`Sequence[Feature]`
  and **skips detection**; object→feature constructors
  (`model.hole`/`boss`/`step`/`slot`/`pattern`/`envelope`) read a feature's size off the
  build123d object you built (⌀ from the cylindrical face; axis/location from the bbox),
  with an explicit-value flavour. The fluent `Sheet` façade (`draftwright.Sheet`) is the
  "beautiful-Python" surface over the existing renderers. **Aspects geometry can't carry
  are now built:** tolerance/fit ride `DimParameter` (P2a/P2a.2); **GD&T + surface finish**
  are standalone IR features (`ControlFrame`/`DatumRef`/`Finish`, `model/ir.py`) placed as
  first-class ADR 0009 corridor candidates by `render_gdt` (P2b #478), authored via
  `sheet.datum`/`sheet.control(…).position(…)`/`.finish` whose target view+strip derive
  from the referenced feature/face (`declare.gdt_target`, P2c #480/#482). PMI-sourced
  auto-GD&T is the last item (#62). Sidesteps #298 misdetection; complements #400 (read +
  edit → now also input). Roadmap: `docs/plans/0011-phase2-aspects-roadmap.md`; #446/#445.
- **0012** — **Accepted; landed** (2026-07-08, umbrella #511 closed — supersedes #396,
  extends #388/#426): **user annotation edits are pinned, priority-ranked candidates in
  the one global solve.** A `dimension(..., pin=, priority=)` edit records a
  scale-independent *dimension intent* on the model — **pin** = the solver's `anchored`/
  `_ANCHOR_WEIGHT` (stays put while the rest flow around it), **priority** =
  `CorridorCandidate.priority` (#357) — placed by the **same** `solve_corridor` as the
  auto dims, re-run by the recompose (`Drawing.finalize()`, #426). Edit freely, recompose
  once; pin is the escape valve so the user never fights the solver. `place_dim()` is now
  the deprecated raw-coordinate escape hatch. The #477 below/right fold-in landed as a
  dependency.

## Dependencies

- `build123d-drafting-helpers>=0.13.0` (Apache 2.0)
- `build123d>=0.9.0` (Apache 2.0)

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
