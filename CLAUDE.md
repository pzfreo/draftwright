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

One module: `make_drawing.py`. It contains:

- **Layout engine** — strip/zone model that places views and reserves space for annotations.
  `_layout_geometry` is the single source of truth for view positions + the iso
  largest-empty-rectangle, shared by `_fits` (scale selection) and `_analyse` (placement).
- **Scale selection** (`choose_scale`) — ISO/ASME standard scales via a **page-major** `_LADDER`
  (smallest sheet first, largest scale on it). A fixed `--page`/`--scale` packs the iso into 2D
  free space (`pack_iso_2d`) so a part fills the requested sheet.
- **Legibility gates** — `_legible_steps` (#41) and `_legible_locations` (#43): only dimension
  features far enough apart on the page to read; the rest surface via lint ("fits" ≠ "legible").
- **Feature orchestration** — calls `find_holes`, `analyse_cylinders` from `build123d_drafting.features`
- **Annotation placement** — calls helpers from `build123d_drafting.helpers`
- **Section view** (`_add_section_view`) — ISO 128-44 arrows, ISO 128-50 hatching
- **Detail view** (`_add_detail_view`, #42) — enlarged view of a crowded step cluster the gate
  dropped; non-sheet scale, so its dims carry `_dw_scale` and `lint()` partitions by scale.
- **`Drawing` class** — composable result with `.lint()`, `.lint_summary()` (machine-readable
  critique), `.add()`, `.at(view,x,y,z)`, `.add_view()`, `.export()`
- **CLI** (`draftwright` command) — STEP → SVG+DXF or editable .py script

## Design direction (read the ADRs)

`docs/adr/` records the architecture decisions. In short: optimise for **deterministic
correctness** ("the best convention is no convention") plus a **domain-semantic edit API**,
*not* bespoke editable-code generation — the drawing domain is familiar to humans/AIs but
draftwright's DSL (strips/zones/`dwg.at`) is in no public corpus. The supported refine loop is
**build → critique (`lint_summary()`) → domain-fix → re-critique**. See
`docs/plans/right-first-time-roadmap.md` for the work and `docs/HANDOVER.md` for current state.

## Dependencies

- `build123d-drafting-helpers>=0.9.1` (Apache 2.0)
- `build123d>=0.9.0` (Apache 2.0)
- `kiwisolver>=1.4,<2` — Cassowary constraint solver for bore-callout Y-placement

## Development

- **Python 3.12** — OCP/vtk do not support 3.13/3.14. If the default interpreter is newer,
  create the venv explicitly: `uv venv --python 3.12` (and run via `uv run --python 3.12`).
- **Tests:** `uv run pytest`. Geometry-level — edge counts, bbox placement, face counts, lint
  clean. Target 100% passing. Heavy NIST CTC end-to-end builds are marked `slow` and deselected
  from the default run; run targeted + the fast tier locally and let CI own the slow tier.
- **Lint/format:** `uv run ruff check src/ tests/` and `uv run ruff format src/ tests/` — keep clean.
- **Visual changes** (views, dims, scale): render to PNG and eyeball — `dwg.export(prefix)` writes
  `prefix.svg`/`.dxf`; `rsvg-convert -b white in.svg -o out.png`. `lint_summary()` is the
  machine check; a render is still the only way to judge layout quality.

## Release

Cutting **vX.Y.Z**: finalise `CHANGELOG.md` (Unreleased → the version) via a PR, then publish a
GitHub **release** with tag `vX.Y.Z`. `.github/workflows/publish.yml` then strips `.dev0`,
publishes to PyPI, and auto-commits the next `…dev0` bump. A push to `main` publishes a dev build
to TestPyPI. Current released line: **v0.1.8** (dev `0.1.9.dev0`).

## License

AGPL-3.0. Anyone running draftwright as a network service must provide their
application's source code. Contact pzfreo@gmail.com for a commercial licence.
