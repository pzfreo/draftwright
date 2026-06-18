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

Three modules:

- **`make_drawing.py`** — the bulk of the engine:
  - **Layout orchestration** — strip/zone model that places views and reserves space for annotations
  - **Scale selection** (`choose_scale`) — ISO/ASME standard scales
  - **Feature orchestration** — calls `find_holes`, `analyse_cylinders` from `build123d_drafting.features`
  - **Annotation placement** — calls helpers from `build123d_drafting.helpers`
  - **Section view generation** (`_add_section_view`) — ISO 128-44 arrows, ISO 128-50 hatching
  - **`Drawing` class** — composable result object with `.lint()`, `.add()`, `.export_*`
  - **CLI** (`draftwright` command) — STEP → SVG+DXF or editable .py script
- **`layout.py`** — the constraint-based layout engine (ADR 0003): the `Placeable`
  protocol and `LayoutSolver` (1D Cassowary strip solver `solve_strip`; 2D
  free-rectangle placer `place_box`/`fit_box`). Sits *below* the domain API.
- **`pmi.py`** — PMI (product manufacturing information) extraction from STEP AP242.

## Dependencies

- `build123d-drafting-helpers>=0.9.1` (Apache 2.0)
- `build123d>=0.9.0` (Apache 2.0)
- `kiwisolver>=1.4,<2` — Cassowary constraint solver for bore-callout Y-placement

## Testing

`uv run pytest`. Tests are geometry-level — edge counts, bbox placement, face counts,
lint clean checks. Target is 100% passing.

## License

AGPL-3.0. Anyone running draftwright as a network service must provide their
application's source code. Contact pzfreo@gmail.com for a commercial licence.
