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
top: leaf modules (`layout.py`, `registry.py`, `linting.py`, `fonts.py`, the
`recognition/` subpackage) → `_core.py` → stage modules (`export.py`, `repair.py`,
`projection.py`, `sheet.py`, `analysis.py`, `drawing.py`, the `annotations/`
subpackage) → `builder.py` → the `make_drawing.py` / `annotate.py` compat facades.
No lower module imports an upper one.

- **`make_drawing.py`** — thin compat facade (~17 lines) re-exporting the public
  surface (`Drawing`, `build_drawing`, `make_drawing`, `generate_script`, `_cli`,
  `FeatureInfo`, `fix_svg_page_size`, `lint_feature_coverage`) so existing imports
  and the `draftwright` CLI entry point keep working. The engine lives in:
  - **`builder.py`** — build orchestration: `build_drawing` (analyse → assemble →
    measure-and-repack → `Drawing`), `make_drawing` (+ export), the editable-script
    generator (`generate_script`), and the CLI (`_cli`). Imports `drawing`/`analysis`/
    the annotation orchestrator/the stage modules — never `make_drawing` (a DAG).
  - **`drawing.py`** — the `Drawing` result object (`.lint()`/`.add()`/`.place_dim()`/
    `.repair()`/`.export*()`; delegates identity to `registry`, coverage to `lint`)
    plus `_build_table` and `FeatureInfo`. Sits below `builder` (which constructs it).
    *(The build context `_analysis`/`_view_edge_cache` still lives on `Drawing`;
    threading it through `builder`→`projection`, ADR 0005 §2, is a deferred follow-up.)*
- **`annotate.py`** — thin compat facade re-exporting `_auto_annotate` (the
  orchestrator) from `annotations/`. The annotation passes were split into the
  **`annotations/`** subpackage (#164 / ADR 0005, P5):
  - **`annotations/orchestrator.py`** — `_auto_annotate`, the single entry point
    (called by `build_drawing`); classifies the part, places envelope/OD dims
    inline, drives the capability passes + title block. (Envelope dims remain
    inline here; pulling them into `annotations/envelope.py` is a deferred
    follow-up.)
  - **`annotations/holes.py`** — hole/pattern callouts, balloons, location dims
    (incl. side-drilled #133), pitch/grid dims, slots (the largest pass).
  - **`annotations/sections.py`** — section A–A + detail views (ISO 128-44 arrows,
    ISO 128-50 hatching).
  - **`annotations/turned.py`** — turned-part step-diameter callouts and the
    axial step-length chain (X-axis turned parts; `find_turned_steps` +
    `lint_axial_coverage` close the drive-screw gap — diameters dimensioned but
    shoulders unlocatable).
  - **`annotations/pmi.py`** — the PMI/GD&T annotation pass (distinct from the
    STEP-side extraction in `pmi.py`).
  - **`annotations/_common.py`** — shared placement helpers (`_anno_box`,
    `_occupied_boxes`, `_box_hits`) at the bottom of the annotations DAG.
  Each submodule imports only `_core`/`layout`/`projection`/third-party — never
  `annotate`/`make_drawing` — so the orchestrator calls down with no cycle.
- **`_core.py`** — shared primitives below both `make_drawing.py` and `annotate.py`:
  the `Analysis` namespace and its field types (`_Projector`, `Strip`, `ViewZones`),
  the dimension/format helpers (`_dim`, `_fmt`, `_add_title_block`, …), and the
  page/slot/margin layout constants.
- **`layout.py`** — the constraint-based layout engine (ADR 0003): the `Placeable`
  protocol and `LayoutSolver` (1D Cassowary strip solver `solve_strip`; 2D
  free-rectangle placer `place_box`/`fit_box`). Sits *below* the domain API.
- **`registry.py`** — `AnnotationRegistry`: the single owner of annotation
  identity/ownership/pins/build-issues (#138 / ADR 0005, Step 2). `Drawing`
  delegates here and keeps the render list; `_named`/`_anno_view`/`_pinned`/
  `_build_issues` remain `Drawing` properties during the migration.
- **`linting.py`** — the lint module (#138 / ADR 0005): `lint_feature_coverage`
  (feature-coverage completeness check), `_suggest_fix` (#29 fix snippets), and
  `CoverageState` (the coverage signal — pattern callouts, patterned holes,
  dropped diameters). Depends only on `_core` + build123d_drafting. `_QUOTED_RE`
  (a lint-message label regex shared with the repair loop) lives in `_core`.
- **`recognition/`** — feature recognition (ADR 0007: draftwright owns it, not
  helpers). `_features.py` (vendored from `build123d_drafting.features`; the
  hole/boss/cylinder/pattern recognisers — `find_holes`/`find_bosses`/
  `analyse_cylinders`/`feature_diameters`/`find_hole_patterns`/`full_cylinders`
  + the feature/pattern types), `slots.py` (the milled-slot recogniser, #135),
  `turned.py` (`find_turned_steps` — turned-shaft shoulders, OD-silhouette filtered),
  and `levels.py` (`analyse_face_levels` — prismatic horizontal face levels; the
  complement of `turned.py`, dispatched by part class, #191). Bottom of the DAG:
  depends only on build123d/OCP. Import via the package surface.
- **`fonts.py`** — vendored, path-pinned IBM Plex fonts for deterministic
  cross-platform layout (ADR 0006).
- **`export.py`** — SVG/DXF/PDF export + post-processing (page-size fix,
  attribution hyperlink/metadata, DXF metadata, arc sanitisation, element-wise
  shape-export degradation, cairo PDF render). The first **module-split** step of
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

Current ADRs:
- **0001** — deterministic generation over an editable DSL.
- **0002** — iterate via lint-critique and domain-repair (repair is a *safety
  net*, not the primary placement mechanism).
- **0003** — constraint-based **inner** layout (`Placeable`/Cassowary in
  `layout.py`): placing one view's annotations within its own zones.
- **0004** — **compose-then-pack** (Accepted; the **outer** layout): each view is
  a *block* = `view_rect(scale) + its annotation boxes`; choose `(scale, page)`
  by a monotone search whose fitness function is composing + packing the blocks
  **disjoint**; build OCC geometry once at the end. Footprints are page-mm
  **box layouts**, never bbox-measured geometry (perf). Byte-identity is **not**
  required — output may change; acceptance = plan-view labels never overlap
  front-view dimensions (CTC-02) + lint clean. Execution tracked as **#121**
  (the current order — annotations placed *after* views, into shared corridors —
  is the root cause of cross-view overlap).
- **0005** — **Accepted, in progress** (#138): compiler-pipeline module boundaries
  + single-owner build state. `Drawing` stops being the implicit state bus;
  annotation identity/pins/build-issues move to a `registry.py`, coverage state to
  lint, build context (`Analysis`, edge cache) to the pipeline. Stages split into
  `builder`/`analysis`/`sheet`/`projection`/`linting`/`repair`/`export`/`annotations/`;
  `layout.py` unchanged. **Roadmap + per-phase issues:**
  `docs/plans/138-module-split-roadmap.md`. **Landed** (`make_drawing.py`
  3,907 → 3,476): golden gate (Step 0), public helper APIs (#139), `registry.py`
  (Step 2), `linting.py` (`CoverageState` + lint functions, Step 3), `repair.py`,
  `export.py`. **Still ahead:** P1 `_text_width`→`_core` (#160), P2 `projection.py`
  (#161), P3 `sheet.py` (#162), P4 `analysis.py` (#163), P5 `annotations/` (#164),
  P6 `builder.py` + build-context threading (#165), P7 mypy (#166). The module list
  above is the *current* tree; the remaining stage modules do not exist yet.
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

## Dependencies

- `build123d-drafting-helpers>=0.13.0` (Apache 2.0)
- `build123d>=0.9.0` (Apache 2.0)
- `kiwisolver>=1.4,<2` — Cassowary constraint solver for bore-callout Y-placement

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
