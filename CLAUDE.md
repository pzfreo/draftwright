# draftwright

Automated technical-drawing generation for [build123d](https://github.com/gumyr/build123d).
Licensed under **AGPL-3.0**. Depends on `build123d-drafting-helpers` for annotation primitives.

## What this is

`draftwright` is the application-level drawing engine: it takes a build123d solid (or a
declared feature model) and produces a fully-annotated multi-view technical drawing
(orthographic views, dimensions, section A–A, ISO hatching, title block) exported to
SVG/DXF/PDF/PNG.

It sits on top of three Apache 2.0 libraries:
- `build123d-drafting-helpers` — annotation primitives (`Dimension`, `Leader`,
  `HoleCallout`, …); the rendering library. Draftwright owns linting and drafting
  policy (ADR 0007).
- `b123d-recognisers` — deterministic geometry-only feature recognition (ADR 0013).
- `build123d` — the underlying CAD kernel.

`AGENTS.md` is the "drive it correctly" usage guide; this file is the working map for
changing the engine.

## Architecture

**One engine**: every user-facing surface is a front door onto
`build_drawing` → `_auto_annotate`; there is no second engine.

The module graph is a strict layered DAG, **machine-enforced** by
`tests/test_import_boundaries.py` — the `_LAYERS` table there is the authoritative
ranked map (an upward module-level import or a cycle fails CI). The detailed
module-by-module prose map lives in [`docs/architecture.md`](docs/architecture.md);
keep `_LAYERS`, that document, and this compact map in step.

Compact map, bottom to top:

- **Leaf modules** — `layout.py` (the deterministic placement solvers: 1D PAVA strip
  solve, `fit_box`, balloon band flow), `registry.py` (annotation identity/pins/issues),
  `_geometry.py` (page-plane maths + the ADR 0014 Amdt 3 material field; the DAG's
  bottom leaf), `fonts.py` (pinned IBM Plex, ADR 0006), `fits.py` (ISO 286),
  `intents.py` (deferred-edit low IR), `recognition_cache.py` (ADR 0017 one-result
  lifecycle), `_warnings.py`, `_pmi_part21.py`, and the `linting/` subpackage
  (draftwright owns lint, ADR 0007).
- **`_core.py`** — shared primitives: the `Analysis` namespace, dim/format helpers,
  page/slot/margin constants.
- **Stage modules** — `analysis.py` (classification + one-shot feature inventory),
  `projection.py` (HLR + material lowering), `compose.py` (the ADR 0004 outer
  compose-then-pack layout), `export.py` (SVG→PDF→PNG chain, DXF), `repair.py`
  (the ADR 0002 lint→repair safety net), `pmi.py` (STEP AP242 PMI),
  `model/` (the ADR 0015 IR waist: `ir`/`detect`/`planner`/`declare`/`compiled`),
  `drawing.py` (the `Drawing` result object; single-owner `BuildState`), and
  `annotations/` (the render passes; `orchestrator.py` owns `_PASS_SEQUENCE`, the one
  canonical stage order; `_common.py` owns the corridor solve + late-furniture seam).
- **`builder.py`** — build orchestration: `build_drawing`, `make_drawing`.
- **Facades / top layer** — `make_drawing.py` + `annotate.py` (thin compat),
  `sheet.py` (the fluent `Sheet` facade, ADR 0011), `sheet_emit.py` (the `--script`
  emitter), `cli.py` (Typer; engine imported lazily inside command bodies, #313),
  `evaluation/` (the versioned STEP-analysis benchmark — production code must never
  depend on benchmark expectations or scores), `recogniser_contract.py` (the
  fail-closed cross-repository capability join).
- `score.py` / `recognition/` — temporary identity-preserving re-exports of
  `b123d_recognisers`; removal scheduled for 0.6.0.

Key invariants — each is machine-enforced, and the guard test is the authority:

- No module but `drawing.py` touches `dwg._*`; build state is filled at one site in
  `builder._assemble` (`test_drawing_encapsulation`, ADR 0005).
- `annotations/` submodules import only down or sideways; the drawing is duck-typed
  as `dwg` (`test_import_boundaries`).
- Recognition runs at most once per build, owned by `BuildState`; a declared build
  recognises nothing until physical critique or export asks (ADR 0017,
  `test_recognition_manifest` fail-closed).
- Renderers emit dimensional content only from `model/compiled.py`'s plan — suppression
  is content they never receive (ADR 0016 Amdt 1, `test_compiled_plan_boundary`,
  `test_label_provenance`).

## Architecture decisions — READ `docs/adr/` FIRST

**Before any change to layout, scaling, page selection, annotation placement, or
generation strategy, read `docs/adr/` and follow the accepted ADRs.** They are the
source of truth for *why* the engine is shaped the way it is; do not re-derive or
contradict them. If a change conflicts with an ADR, amend the ADR in the same PR
(status, reasoning, date) rather than silently diverging — and if a decision turns
out wrong, record that too.

**Assess architectural fit — always.** An issue, a PR, and a review are incomplete
until they weigh the change against the ADRs, not just its local correctness: does a
feature round-trip recognise **+** emit **+** declare (ADR 0011)? Does it fit the
compiler pipeline and one-inventory waist (ADR 0015) and the recogniser contract
(ADR 0013)? Does it sit at its DAG rank, place geometry through the corridor solve,
and extend a shared pass rather than adding a copy? A change that is locally correct
but architecturally off-pattern *is* tech debt — call it out in the issue/PR/review,
not after merge.

Read an amended ADR's **Current decision** header first; the amendments are the why
trail. Past ~3–4 amendments, prefer a new superseding ADR. Per-ADR status detail:
[`docs/architecture.md`](docs/architecture.md).

Index: **0001** deterministic generation (no imperative editable script — retired
#940) · **0002** lint-critique + repair as safety net · **0003** retired ·
**0004** compose-then-pack outer layout · **0005** pipeline module boundaries +
single-owner build state (complete) · **0006** pinned vendored fonts for
deterministic layout · **0007** draftwright owns lint/recognition-lifecycle/policy ·
**0008, 0009** superseded (read 0015, 0014) · **0010** annotation-provenance seam ·
**0011** the IR as public input: declare features (`Sheet`, `model/declare`) ·
**0012** user edits as pinned, ranked corridor candidates · **0013** uniform
recogniser contract (external `b123d-recognisers`) · **0014** collect-then-solve
placement, 4 amendments (late leader stage; material-re-entry penalty; budgets must
measure, not predict) · **0015** the part-drawing compiler as built ·
**0016** declared dimensioning intent (authored sets suppress by omission; compiled-
plan boundary) · **0017** recognition inventory as first-class result (ownership
landed; correspondence work evidence-gated by #1018) · **0018** requirement-driven
view planning (**accepted, nothing implemented yet** — the evidence list is the
per-slice gate).

## Dependencies

- `build123d-drafting-helpers>=0.13.0` (Apache 2.0), `build123d>=0.9.0` (Apache 2.0)
- Export render chain: `reportlab` + `svglib` (PDF), `pypdfium2` + `pillow` (PNG) —
  all pure-wheel, no native cairo; svglib is the one weak-copyleft (LGPL) member.
- The 1D strip solve is dependency-free PAVA (`_solve_strip_1d_pava`); `kiwisolver`
  was retired.

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
