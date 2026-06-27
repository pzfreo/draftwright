# #138 — Module-split roadmap

Execution roadmap for [ADR 0005](../adr/0005-pipeline-architecture-and-state-ownership.md)
(compiler-pipeline module boundaries + single-owner build state). Tracking issue:
**#138**. Each phase below is a GitHub issue; each chunk is one PR.

## Why
`make_drawing.py` (3,907 lines at the start) and `annotate.py` (2,587) held almost
everything, and `Drawing` was the implicit state bus between subsystems. ADR 0005
reshapes the package along the compiler-pipeline stages and gives each build-time
state concern a single owner. The golden-output gate (`tests/test_golden.py`,
made cross-platform-deterministic by the #149 font pinning) proves every move is
behaviour-preserving.

## Status

**Landed** (`make_drawing.py` 3,907 → 3,476):

| Step | What | PR |
|---|---|---|
| 0 | Golden-output gate (Step 0) | #147 |
| — | Deterministic layout (font pinning — makes the gate pin real parts) | #149 |
| 1 | Public helper APIs (#139) | #152 |
| 2 | `registry.py` — annotation identity/ownership/pins/build-issues | #152 |
| 3 | `linting.py` — `CoverageState` + `lint_feature_coverage` + `_suggest_fix` | #155, #158, #159 |
| — | `repair.py` — deterministic lint→repair loop | #159 |
| — | `export.py` — SVG/DXF/PDF export + post-processing | #156 |
| — | Infra: smoke tier, coverage→CI, xdist, slow-tier-post-merge | #154, #157 |

**Remaining** — the deeply-coupled stage modules, sequenced prerequisite-first so
no PR introduces an import cycle, riskiest (annotations) last:

| Phase | Issue | Chunk(s) | Size | Depends |
|---|---|---|---|---|
| ~~P1~~ | #160 | `_text_width` → `_core` ✅ | tiny | — |
| ~~P2~~ | #161 | `projection.py` (silhouettes, iso) ✅ | med | — |
| ~~P3~~ | #162 | `sheet.py` — compose-then-pack (repack deferred to P6) ✅ | large (2 PRs) | P1 |
| ~~P4~~ | #163 | `analysis.py` (`_analyse`) ✅ | med-lg | — |
| ~~P5~~ | #164 | `annotations/` — sections, turned, pmi, holes, orchestrator ✅ (envelope.py deferred) | biggest (5 PRs) | P1–P4 |
| **P6** | #165 | `builder.py` + thread the build context | med | P2, P4 |
| **P7** | #166 | tighten mypy on settled contracts | cleanup | all |

## Target module shape (ADR 0005 §1)
```text
make_drawing.py   # transitional compat facade / public re-exports
builder.py        # build_drawing/make_drawing orchestration (P6)
analysis.py       # _analyse, Analysis construction (DONE)
sheet.py          # choose_scale, compose-then-pack (DONE; repack→P6)
projection.py     # view projection, silhouettes, iso fit (DONE)
registry.py       # annotation identity (DONE)
linting.py        # lint_feature_coverage, _suggest_fix, CoverageState (DONE)
repair.py         # deterministic repair loop (DONE)
export.py         # SVG/DXF/PDF export (DONE)
fonts.py          # vendored path-pinned fonts (DONE)
annotations/      # the split annotate.py passes (P5)
  orchestrator.py envelope.py holes.py turned.py sections.py pmi.py
layout.py         # solver/placement — UNCHANGED (ADR 0003)
_core.py          # shared primitives below everything
```

## Per-PR playbook (proven across the six splits already landed)
1. Branch off `main`; move **one** cluster.
2. `make_drawing.py`/`annotate.py` re-import the moved symbols (compat facade) so
   `from draftwright.make_drawing import …` and public re-exports keep working;
   redirect test imports to the new home only where deliberate.
3. **Golden gate must pass against the committed snapshots with no regeneration** —
   the proof the move changed nothing.
4. `ruff check` **and** `ruff format --check` **and** `mypy` (all three — `ruff
   check` alone misses format/type errors), plus the moved area's targeted tests.
5. Open PR; merge on green. CI: lint + parallel matrix on the PR; slow tier
   post-merge on `main`.

## Risk notes
- **Cycles** — if a moved cluster needs a `make_drawing`-local helper, relocate
  that helper to `_core` first as a mini-prereq (P1 does this for `_text_width`).
- **Hairiest** — P5 holes (most shared helpers) and the orchestrator (the envelope
  OD/width/depth/height/step dims are *inline* in `_auto_annotate`, not separate
  functions, so they must be extracted into `annotations/envelope.py` during the
  orchestrator split). Give these the most review.
- **Build context** (`_analysis`, `_view_edge_cache`) is **not** made a standalone
  owner — that would contradict ADR 0005 §2 ("threaded through `builder`/
  `projection`, not parked on `Drawing`"). It is resolved in P6.

## Success criteria (every phase)
- Named symbols live in the new module; compat facade keeps imports working.
- No import cycle; new module imports only `_core`/below + build123d.
- Golden gate unchanged (no snapshot regeneration).
- `ruff check` + `ruff format --check` + `mypy` clean; targeted tests + CI green.
- `make_drawing.py` / `annotate.py` line count drops; CLAUDE.md + ADR 0005 updated.
