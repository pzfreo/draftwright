# Handover — current state of work

_Snapshot for a Claude/dev picking this up on another machine. Last updated 2026-06-16._

## Orientation (read these first)

1. `CLAUDE.md` — what the project is, architecture, dev/release workflow.
2. `docs/adr/0001-*.md` and `docs/adr/0002-*.md` — the **design direction** (deterministic
   correctness + a domain-semantic edit API; the build→critique→domain-fix loop).
3. `docs/plans/right-first-time-roadmap.md` — the work plan and sequencing.
4. The whole engine is one module: `src/draftwright/make_drawing.py`; tests in
   `tests/test_make_drawing.py`; NIST CTC fixtures in `tests/fixtures/`.

## Where things stand

- **Released:** **v0.1.8** on PyPI. `pyproject.toml` is at `0.1.9.dev0` (next dev line).
- **Merged in the recent arc:** #41 (step legibility gate + tier pitch), #46 (staircase fixes),
  #47 (page-aware scale selection: page-major ladder + 2D iso packing on fixed page/scale + iso
  growth cap), #43/#49 (hole-location legibility gate), #42/#52 (enlarged detail view MVP),
  plus #48/#50 (roadmap + changelog).
- **Open PR:** **#51** — ADRs 0001 & 0002 (docs only, awaiting merge).
- **Open issues (the roadmap's remaining work):**
  - Cluster B (domain-semantic API): #26 `features()`, #25 `place_dim()`, #27 `annotations()`,
    #28 `view_bounds()`.
  - Self-correction: #29 lint `suggestion` snippets, #30 lint→repair loop.
  - Drawing quality: #45 representative / "TYP" dimensioning for repeated features.

## Recommended next steps

Per ADR 0002 and the roadmap, the highest-leverage work is the **domain-semantic layer** that
makes the build→critique→domain-fix loop real:

1. **#26 `features()` + #25 `place_dim()`** — the domain vocabulary + a strip-aware dimension
   primitive. Standalone API wins and prerequisites for the repair loop. Frame these in **domain
   terms** (holes/bores/sections/dimensions), never strip/zone internals (ADR 0001).
2. **#29 suggestions** — each lint issue carries a ready domain-API call.
3. **#30 lint→repair loop** — auto-apply computable suggestions; surface the rest.
4. **#45 TYP dimensioning** — opportunistic; reduces clutter for repeated features.

## Known follow-ups / tech debt (not yet all filed)

- **Detail-view legibility on reduction-scale parts** (#42 MVP): on a part already reduced
  (e.g. CTC-02 at 0.2 → detail at 0.4) the detail is small/dense. Consider a minimum legible
  detail size, or giving the detail its own sheet. **Not yet filed — worth an issue.**
- **`step_dim_dropped` is redundant when a detail view now resolves it** — the warning is still
  truthful (the main view did drop them) but the resolution exists; consider noting "shown in
  detail A" or downgrading to info. Affects `lint_summary()` (the critique contract).
- **Detail marker reuses `is_centerline=True`** to dodge the overlap lint; a dedicated furniture
  flag would be cleaner (needs a `build123d-drafting-helpers` change).
- **lint is only per-view-scale aware via the `_dw_scale` tag** added for the detail view;
  the helper `_lint_dim` still assumes one `drawing_scale`. Fine today (detail is the only
  non-sheet-scale view with dims) but revisit if more such views appear.

## Conventions & gotchas

- **Python 3.12 only** — OCP/vtk break on 3.13/3.14. `uv venv --python 3.12` if needed.
- **Import shadowing:** `draftwright/__init__` exposes the `make_drawing` *function*, which
  shadows the submodule — `import draftwright.make_drawing as m; m.SomeName` fails. Import names
  directly (`from draftwright.make_drawing import _fits`) or use
  `sys.modules['draftwright.make_drawing']`.
- **Branch/PR discipline (from the user's global rules):** never commit to `main`; one feature
  branch + PR per change; **stop after opening a PR** and wait for explicit "merge"; never
  auto-merge. Commits end with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`; PR
  bodies end with the Claude Code generated-with line.
- **Testing tiers:** run targeted + the fast suite locally; CI owns the slow NIST CTC tier
  (3 OS × 3 Python + a slow job). Full fast suite ≈ 5 min, ~239 passing.
- **Worktree agents** that need to run tests must create a 3.12 venv in the worktree.
- **Verifying a change quickly:**
  ```python
  from draftwright import build_drawing
  from draftwright.make_drawing import _import_step
  d = build_drawing(_import_step("tests/fixtures/nist_ctc_02_asme1_ap203.stp"))
  print(d.lint_summary()["by_code"])      # machine critique
  d.export("/tmp/check")                    # then rsvg-convert to PNG and look
  ```

## The core loop the project is building toward (ADR 0002)

`build_drawing()` → render + `lint_summary()` → (apply **domain** fixes ⇄ re-lint) → done.
The deterministic engine gives the first lap; lint is the machine critic; the domain API +
repair loop (#25–#30) give an AI the same iterate-and-fix laps a human gets interactively —
without anyone needing fluency in draftwright's internal DSL.
