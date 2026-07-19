# ADR 0005 — Compiler-pipeline module boundaries and single-owner build state

- **Status:** Accepted; implemented, with compatibility-alias deletion tracked by #720 for 0.4.0.
- **Date:** 2026-06-27
- **Deciders:** Paul Fremantle (pzfreo)
- **Progress:** Execution roadmap with per-phase tracking issues:
  [`docs/plans/138-module-split-roadmap.md`](../plans/138-module-split-roadmap.md).
  **Landed** (`make_drawing.py` 3,907 → 3,476): the golden gate (Step 0, made
  cross-platform-deterministic by the #149 font pinning), public helper APIs
  (#139), `registry.py` (annotation identity, Step 2), `linting/`
  (`CoverageState` + `lint_feature_coverage` + `_suggest_fix`, Step 3), `repair.py`
  (the lint→repair loop), and `export.py`. **Then** (the deeply-coupled stage
  splits, sequenced prerequisite-first — **all since landed**): P1 `_text_width`→`_core`
  (#160), P2 `projection.py` (#161), P3 `sheet.py` (#162; repack deferred to P6),
  P4 `analysis.py` (#163), P5 `annotations/` (#164), P6 `builder.py`+`drawing.py`
  (#165; context-threading deferred), P7 mypy (#166). All phases landed:
  make_drawing.py 3,907 → ~17 (facade).
  **Follow-up status (2026-07-15):** the `annotations/envelope.py` extraction was
  **overtaken by ADR 0008** — the envelope pass converged into
  `annotations/from_model.py` with the other IR render passes, so no standalone
  module is planned. The §2 build-context threading is **closed**
  (2026-07-18, #639 — see the dated amendment at the end of this file): the
  passes take an explicit `PlacementContext` and make zero private `Drawing`
  reads (empty fail-closed ratchet); the build context lives in one typed
  `BuildState` filled at a single `builder._assemble` site.

## Context

draftwright is, in fact, a **deterministic drawing compiler** (ADR 0001): part →
geometry/feature analysis → sheet planning → projection → annotation → critique
→ repair → export. The earlier ADRs settled the *strategy* of each stage —
deterministic generation (0001), the lint→repair loop (0002), the constraint
solver (0003), compose-then-pack (0004). None of them settled *where the code for
those stages lives* or *who owns the state that flows between them*. That gap has
now become the dominant maintainability cost.

Two concrete symptoms (measured on the current tree):

- **Two files hold almost everything.** `make_drawing.py` is **3,907 lines** and
  `annotate.py` is **2,430** — together ~85 % of the package. Analysis, sheet
  planning, projection, lint, repair, export, and the public facade all coexist
  in `make_drawing.py`; every annotation pass lives in `annotate.py`.
- **`Drawing` is the implicit state bus, not just the public object.** Stages
  communicate by mutating `Drawing`'s private dictionaries — `_named`,
  `_anno_view`, `_pinned`, `_pattern_callouts`, `_patterned_holes`,
  `_build_issues`, `_dropped_callout_diams`, `_view_edge_cache`, `_analysis`.
  These nine names are referenced **277 times** across `src/` and `tests/`, and
  tests reach *through* them directly (`test_make_drawing.py`, `test_pmi.py`,
  `test_e2e_standards.py`, `test_layout.py`).

The line count is a *symptom*; the disease is that `Drawing` is simultaneously the
public editable object **and** the internal channel for projection, annotation
ownership, lint, repair, repack measurement, tables, and balloons. Unrelated
changes cannot be isolated, and new features depend on incidental private state.

This ADR settles two questions the others left open:

1. **What are the module boundaries** of the compiler pipeline?
2. **Who owns the build-time state** that flows between stages, so it stops living
   on `Drawing` as a grab-bag of private dicts?

It deliberately does **not** re-open any decision in 0001–0004; it gives them a
home and a clean import direction.

## Decision

### 1. Module shape follows the pipeline stages

Reshape the package so each compiler stage is its own module, with a strict
top-down import direction (no module imports an upper one):

```text
draftwright/
  __init__.py
  make_drawing.py        # compatibility facade / public re-exports (transitional)
  builder.py             # build_drawing / make_drawing orchestration: analyse → assemble → repack → repair
  analysis.py            # STEP import, feature analysis, Analysis construction
  compose.py             # née sheet.py (#640): page sizes, choose_scale, ViewBlock, StripDepths, compose-then-pack/repack (ADR 0004)
  projection.py          # view projection, exact silhouettes, iso fitting (_assemble, _project_iso, _fit_iso_view)
  drawing.py             # Drawing public facade
  registry.py            # annotation identity/ownership/pins/build-issues (see §2)
  linting/               # coverage, issues, structural lint, suggestions (ADR 0002)
  repair.py              # deterministic repair loop (ADR 0002)
  export.py              # SVG/DXF/PDF export + post-processing
  tables.py              # generic table construction/placement helpers
                         #   (never created — table GEOMETRY converged into _core._table_metrics/_build_table,
                         #    the one sizing model (#700/#699); PLACEMENT stayed a Drawing verb, add_table)
  annotations/
    orchestrator.py      # auto_annotate sequencing (_auto_annotate)
    _common.py           # shared annotation placement/rendering helpers
    from_model.py        # IR planner/render passes, including envelope features
    holes.py             # hole callouts, location dims, hole-table escalation
    sections.py          # section/detail views
    balloons.py          # balloon rendering and placement
  layout.py              # placement primitives consumed by ADRs 0004/0014
  _core.py               # shared primitives below builder/annotations (Analysis types, _dim/_fmt, layout constants)
```

This is the landed shape of the modules this ADR proposed. The earlier proposal named standalone
`envelope.py`, `turned.py`, and `pmi.py` modules; those responsibilities
converged into `from_model.py` and the shared annotation modules instead.
`layout.py` keeps its placement-primitives responsibility near the bottom of the
DAG with `_core.py`.

### 2. Build-time state gets explicit owners — three, not one god-object

The nine private fields are **not one concern**, and folding them into a single
`DrawingState` would only rename the bus. Split by responsibility:

- **`registry.py` — annotation identity & ownership.** Owns `_named`,
  `_anno_view`, `_pinned`, `_build_issues`. This is the registry the migration
  centres on:

  ```text
  registry.add(obj, name, view) / remove(name) / named(name) / annotations()
  registry.owner(name) / pin(name) / unpin(name)
  registry.record_issue(severity, code, message)
  registry.pattern_covers(callout_name, holes)
  ```

  `Drawing.add/remove/annotations/get_annotation/pin/unpin/clear_annotations`
  remain the public API and **delegate** here. The pin/`locked` override contract
  from ADR 0003 ("manual override must win, survives every re-solve") is now
  *owned* by the registry — the contract is unchanged, only its home moves.

- **Coverage state lives with linting.** `_pattern_callouts`, `_patterned_holes`,
  and `_dropped_callout_diams` are lint/repair *signal* (what was covered, what
  was dropped), not annotation identity. They belong with `linting.py`/`repair.py`
  and feed `lint_feature_coverage` and the repair loop (ADR 0002).

- **Build context lives with the pipeline.** `_analysis` (the `Analysis`
  namespace) and `_view_edge_cache` (a projection cache) are pipeline artefacts,
  not registry entries. They are threaded as an explicit build context through
  `builder.py` → `projection.py`, not parked on the public `Drawing`.

**The acceptance test for the disease being cured:** annotation passes no longer
mutate raw `dwg._named` / `dwg._anno_view` / `dwg._build_issues` /
`_patterned_holes`; they go through the registry/linting APIs. `Drawing` remains
the public editable object but is no longer the implicit state bus.

### 3. Behaviour-equivalence is gated, not assumed

A pure-refactor of ~6,300 lines cannot be proven safe by the current
geometry-level tests alone (they assert edge counts and bboxes; a 0.3 mm shift or
a reordered pass can pass them all). Therefore, as **Step 0, before any module
moves**: stand up a golden/characterisation harness (`tests/test_golden.py`) that
builds a fixed corpus — three build123d primitives (turned / prismatic / stepped)
— and snapshots a canonical digest of each: the Drawing's per-view geometry +
annotations + lint summary, the SVG structure, and the DXF geometry-entity counts.
Every refactor PR must leave those snapshots identical unless it **explicitly**
states it is correcting behaviour and regenerates them (`UPDATE_GOLDEN=1`) with
rationale — the discipline ADR 0004's amendment applied to *intended* changes, here
used to prove *no* change.

Two portability constraints shape what the gate can pin, both learned on the CI
matrix (Linux/macOS/Windows):

- **No text.** Dimension glyph boxes and their tessellated DXF entities are
  font-metric-dependent and differ across OS, so the digest pins *counts and
  geometry, never text extents* (it records dimension values, not label boxes).
- **No real-part absolute layout.** A real part's page centering and iso fit
  depend on summed text-metric measurements, so its whole sheet drifts ~1 mm
  across OS (NIST CTC-01 shifted +1.028 mm in X on Linux vs macOS). A byte-exact
  geometry gate cannot pin that portably, so the corpus is the primitives, which
  *are* cross-platform stable. Real-part coverage stays with `test_e2e_standards`
  (property-based); golden coverage of a real part would require pinning the gate
  to a single OS — a separate decision, deferred. (The dense-ballooning CTC-02
  case is likewise out: too heavy for a routine gate, and its overlap acceptance
  is already pinned by `test_e2e_standards`.)

> **Amended 2026-06-28 — golden harness retired.** `tests/test_golden.py` and
> its `tests/golden/*.json` snapshots have been removed. The harness did its job:
> it was Step 0 *characterisation* scaffolding to prove the 0005 module split (now
> complete) changed no behaviour. With the migration done, the engine is in a
> phase of *deliberate* output evolution (ADR 0007 ownership move + new features
> such as turned-part axial dims), where a byte-exact digest is high-maintenance
> regeneration friction with low signal — and, being pinned to three primitives +
> one prismatic part, it covered none of the turned-part work it nominally
> "passed". Regression coverage now rests where it belongs: the geometry-level
> tests (edge/face counts, bbox placement, lint-clean) and the property-based
> `test_e2e_standards` real-part suite. The "leave the snapshots identical" rule
> above no longer applies.

### 4. Compatibility facade is transitional, with a deletion deadline

`make_drawing.py` stays as a re-export facade, and `_named`/`_anno_view`/… remain
as transitional aliases, so the 277 reach-throughs (especially in tests) don't
break in one churn PR. **But the aliases are the disease**: "two ways to reach the
same state" is exactly what we are removing. Each alias carries a tracking issue
and a removal target; the migration is not "done" until tests are redirected to
the public/registry API and the aliases are deleted. A facade with no exit date is
a failure mode, not a success.

> **Status (2026-07-18, #699 slice c):** the tracking and redirect
> *prerequisites* of this criterion are now met — the deletion itself remains
> open until 0.4.0. The seven live aliases (`_named`/`_anno_view`/`_pinned`/
> `_build_issues` on the registry side; `_pattern_callouts`/`_patterned_holes`/
> `_dropped_callout_diams` on the coverage side) are tracked by **#720** with
> removal target **0.4.0**. The one production reach-through (`sheet.py`'s
> `set(dwg._named)`) and the test-file reach-throughs were redirected to the
> public reads (`annotations()` / `iter_annotations()` / `get_annotation()` /
> `view_of()` / `registry.pinned_names()` / `registry.issues`); only
> `drawing.py` internals ride the aliases until #720 deletes them.

### 5. Migration order (each step its own releasable PR)

Follows the issue (#138) sequence, with §3 added as Step 0:

0. Golden/characterisation gate (above).
1. **Adopt public helper APIs first (#139)** — bump
   `build123d-drafting-helpers`, replace private upstream imports
   (`_full_cyls`→`full_cylinders`, `_spec_key`→`HoleSpec.from_hole`, title-block
   geometry→public `TitleBlock` cell accessors). *Prerequisite, currently
   unsatisfied — see Risks.*
2. Add `registry.py`; make `Drawing` delegate; keep aliases. No behaviour move.
3. Extract `export.py` (low semantic risk — mostly consumes drawing contents).
4. Extract `linting.py` + `repair.py`; make them consume the registry API.
5. Extract `sheet.py` (ADR 0004 compose-then-pack stays coherent and testable).
6. Extract `projection.py`/`builder.py`; `builder.py` becomes the orchestrator.
7. Only then split `annotations/` by drafting capability — a *move*, not a
   rewrite, onto the now-stable registry/Drawing API.
8. Tighten mypy once seams are stable (not before — avoid churning types against
   moving targets).

`main` stays releasable after each step; no two steps in flight at once.

## Consequences

**Positive**
- Each pipeline stage is independently readable, testable, and changeable; a
  feature touches one stage, not a 3,900-line file.
- Annotation/lint/repair state has *one owner each*; new annotation types depend
  on a stable API, not incidental private dicts.
- The import direction is an explicit DAG with no cycles, enforceable by a simple
  import-lint and (later) mypy.
- ADRs 0002/0003/0004 get a clear home: lint/repair → `linting.py`/`repair.py`,
  the solver → unchanged `layout.py`, compose-then-pack → `sheet.py` (since renamed `compose.py`, #640).

**Negative / costs**
- A long multi-PR migration with a real risk of a half-moved engine; mitigated by
  the golden gate (§3) and one-step-at-a-time discipline.
- The transitional facade/aliases temporarily *increase* surface area (two ways to
  reach state) until they are deleted (§4) — the explicit deletion deadline is the
  control.
- Splitting the state bus into three owners is more design than one `DrawingState`
  blob, but a single blob would not actually cure the disease.

**Neutral / follow-ups**
- `CLAUDE.md`'s "Five modules … DAG" architecture section becomes stale as modules
  move; update it in lockstep with the migration (it should describe the new
  shape, not the old five files).
- mypy hardening (Step 8) is deferred by design.

## Risks

- **Step 1 is a hard prerequisite that the dev environment does not currently
  satisfy.** `pyproject.toml` pins `build123d-drafting-helpers>=0.12.0`, but the
  installed helper is **0.10.1**, in which `full_cylinders` and `HoleSpec` do not
  exist. Resolve the env/version mismatch and **verify 0.12.1 actually ships**
  `full_cylinders`, `HoleSpec.from_hole`, and the public `TitleBlock` cell
  accessors before starting — the migration's first step depends on APIs that are
  absent from what is installed today.

## Impact on other ADRs

- **ADR 0001 / 0002 — no decision change.** 0002's lint→repair *contract*
  (`lint_summary()` codes, `Drawing.lint()/repair()` as public wrappers) is
  unchanged; only the implementation moves to `linting.py`/`repair.py`, and it now
  reads coverage/issue state through the registry API instead of private dicts.
- **ADR 0003 — no decision change; state-ownership note.** `layout.py` stays
  exactly as-is. The pin/`unpin`/`locked` *state* (the "manual override must win"
  editability contract) moves from `Drawing`'s `_pinned` dict into `registry.py`;
  the contract and the solver are untouched. Worth a one-line cross-reference in
  0003 pointing at the registry as the owner.
- **ADR 0004 — no decision change; anchor refresh needed.** Compose-then-pack and
  the `(scale, page)` search are unchanged, but 0004 names anchors by their
  *current* location (`_analyse`, `StripDepths`, `ViewBlock`, `_auto_annotate` in
  `make_drawing.py`/`annotate.py`). After Steps 5–7 those live in `sheet.py` (now `compose.py`),
  `projection.py`, and `annotations/orchestrator.py`. 0004 should get a short
  amendment noting the new module homes so its anchors don't go stale — a
  pointer, not a reversal.

## Related

- Issue **#138** (this refactor) and **#139** (adopt public helper APIs).
- [ADR 0001](0001-deterministic-generation-over-editable-dsl.md),
  [ADR 0002](0002-iterate-via-lint-critique-and-domain-repair.md),
  [ADR 0003](0003-constraint-based-layout.md),
  [ADR 0004](0004-compose-then-pack-view-blocks.md).

**Amendment (2026-07-18) — §2 closed (#639):** `Drawing` is no longer the implicit state bus. The render passes take an explicit `PlacementContext` and make **zero** private `Drawing` reads (the `_DWG_PRIVATE_READ_ALLOW` ratchet is empty and fail-closed). The build context (`Analysis`, part model, lint geometry caches) lives in one typed `BuildState` on the drawing, filled at a single `builder._assemble` site and single-writer-guarded; it serves the Drawing's own methods (lint/finalize/repair/edit verbs) and the test surface — hosting its own state was never the §2 sin, passes reaching in was.

## Amendment — current architecture contract (2026-07-19)

The original migration plan, measurements, golden-harness discussion, module
proposals, and prerequisite warnings above are retained as implementation
history. They are not current work instructions. The lasting decision is:

1. Compiler stages have explicit module homes: orchestration in `builder.py`,
   analysis in `analysis.py`, outer layout in `compose.py`, projection in
   `projection.py`, annotation passes in `annotations/`, lint in `linting/`,
   repair in `repair.py`, and export in `export.py`.
2. `Drawing` is the public editable object, not an implicit communication bus.
   Render passes receive an explicit `PlacementContext`; their fail-closed
   private-read allowlist is empty.
3. Build artefacts are held in one typed `BuildState`, populated at the single
   `builder._assemble` boundary. Annotation identity belongs to
   `AnnotationRegistry`, and
   lint coverage belongs to `CoverageState`.
4. Import direction and state encapsulation are executable contracts enforced
   by `test_import_boundaries.py` and `test_drawing_encapsulation.py`.
5. Seven private compatibility aliases remain only for the 0.4.0 transition;
   #720 owns their deletion. They are not sanctioned extension points.

This amendment is the reader's current entry point. The dated sections above
explain how the package reached it.
