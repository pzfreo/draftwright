# draftwright

Automated technical-drawing generation for [build123d](https://github.com/gumyr/build123d).
Licensed under **AGPL-3.0**. Depends on `build123d-drafting-helpers` for annotation primitives.

## What this is

`draftwright` is the application-level drawing engine. It takes a build123d solid and
produces a fully-annotated multi-view technical drawing (orthographic views, dimensions,
section A–A, ISO hatching, title block) ready for DXF/SVG export.

It sits on top of two Apache 2.0 libraries:
- `build123d-drafting-helpers` — annotation primitives (`Dimension`, `Leader`, `HoleCallout`, …)
- `build123d` — the underlying CAD kernel

## Architecture

The dependency graph is a DAG: the leaf modules `layout.py`, `registry.py`, and
`fonts.py` sit below `_core.py` → (`make_drawing.py`, `annotate.py`), and
`make_drawing.py` → `annotate.py`. No lower module imports an upper one.

- **`make_drawing.py`** — orchestration and the public surface:
  - **STEP/Shape import + geometry analysis** (`_analyse`) — builds the `Analysis` namespace
  - **Layout orchestration** — strip/zone model that places views and reserves space for annotations
  - **Scale selection** (`choose_scale`) — ISO/ASME standard scales
  - **Feature orchestration** — calls `find_holes`, `analyse_cylinders` from `build123d_drafting.features`
  - **`Drawing` class** — composable result object with `.lint()`, `.add()`, `.export_*`
  - **CLI** (`draftwright` command) — STEP → SVG+DXF or editable .py script
- **`annotate.py`** — the automatic annotation passes. `_auto_annotate` is the single
  entry point (called by `build_drawing`); it drives `_annotate_holes`, `_annotate_pmi`,
  `_add_location_dims`, `_add_section_view` (ISO 128-44 arrows, ISO 128-50 hatching),
  and `_add_detail_view`. Imports only from `_core.py`/`layout.py` and third-party libs.
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
- **`fonts.py`** — vendored, path-pinned IBM Plex fonts for deterministic
  cross-platform layout (ADR 0006).
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
  `layout.py` unchanged. **Landed so far:** Step 0 (golden-output harness, gates
  behaviour-equivalence), Step 1 (#139, public helper APIs), Step 2 (`registry.py`
  owns annotation identity; `Drawing` delegates, with `_named`/`_anno_view`/
  `_pinned`/`_build_issues` kept as compat properties). **Still ahead:** coverage
  state → lint, build context → pipeline, the sheet/projection/export/annotations
  splits. The module list above is the *current* tree; the remaining stage modules
  do not exist yet.
- **0006** — **Accepted** (#149): deterministic cross-platform layout via bundled,
  path-pinned fonts. Layout depends on measured text width; resolving a font *name*
  (`"Arial"`) substitutes a different font on Linux, drifting the whole sheet ~1 mm.
  draftwright vendors IBM Plex (OFL) and pins it by `font_path` (Plex Mono for
  dimensions, Plex Sans Condensed for title blocks); the helper renders via
  `font_path` (needs `>=0.13.0`). Output changed once for every drawing (goldens
  regenerated); the 0005 golden gate now pins a real part (CTC-01).

## Dependencies

- `build123d-drafting-helpers>=0.13.0` (Apache 2.0)
- `build123d>=0.9.0` (Apache 2.0)
- `kiwisolver>=1.4,<2` — Cassowary constraint solver for bore-callout Y-placement

## Testing

Tests are geometry-level — edge counts, bbox placement, face counts, lint clean
checks. Target is 100% passing. Tiers (#153):

- **`uv run pytest -m smoke`** (~30 s) — curated build-light subset for a quick
  local "did I break something obvious" check.
- **`uv run pytest`** — full fast tier (`-m 'not slow'`, ~8 min; nearly every test
  does a real OCC build). Prefer **targeted** selections (`-k`, node ids) locally.
- **`-m slow`** (~19 min, CTC fixture builds) — CI-only.

Coverage is kept out of the default addopts (it adds ~13% locally); the CI
workflow passes the `--cov` flags. CI runs the full fast tier (3×3 OS/Python
matrix) plus the slow tier on every PR.

## License

AGPL-3.0. Anyone running draftwright as a network service must provide their
application's source code. Contact pzfreo@gmail.com for a commercial licence.
