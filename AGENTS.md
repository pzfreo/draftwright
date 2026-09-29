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
  policy (ADR 3 (was 0007)).
- `quiddity` — deterministic geometry-only feature recognition (ADR 3 (was 0013)).
- `build123d` — the underlying CAD kernel.

[`docs/using-draftwright.md`](docs/using-draftwright.md) is the "drive it correctly" usage
guide for agents *using* draftwright; this file is the working map for
changing the engine.

## Architecture

**One engine**: every user-facing surface is a front door onto
`build_drawing` → `_auto_annotate`; there is no second engine.

The module graph is a strict layered DAG, **machine-enforced** by
`tests/test_import_boundaries.py`: the `_LAYERS` table there is the authoritative
ranked map (an upward module-level import or a cycle fails CI), and
[`docs/architecture.md`](docs/architecture.md) is the module-by-module map. Keep the two
in step; do not restate the module list here.

In outline, by `_LAYERS` rank: **0** leaves and transitional shared helpers — placement solvers,
geometry maths, registry, measurement support with pocket/pad location predicates,
recognition-boundary contracts and ownership, and the `model/` IR waist with its
foundational record owner, dimension-intent and oriented-slot geometry validation
(ADR 1) → **1**
`_core` / `document_input` / the stable `location_contract` import path → **2**
`projection`, `compose`, `export`, `pdf_text`, `repair`, `_pmi_schema`,
`_pmi_linear_geometry`, `_pmi_support_blockers`, `pmi`,
`linting/`, `reporting`, `drawing_evidence` and peers → **3** `analysis` → **4**
`annotations/` (the render passes, including feature-family `_slots` and
`_step_lengths` owners, plus the optional `solve_trace` recorder;
`orchestrator._PASS_SEQUENCE` is the one stage order)
→ **5** `drawing`, `drawing_edits`, `drawing_diagnostics`, and
`intent_drain` (the deferred stages, with transaction state owned by `Drawing`) → **6**
`builder`, one-attempt compilation, explicit-scale resolution, finished-drawing build policy and guarded layout selection → **7** facades
(`sheet` and its private identity/layout-control/view owners, `document`, `sheet_emit`,
`sheet_object_source`, `inspection`, `evaluation/`, `cli`, the recogniser/inspection
contract joins).
`make_drawing` / `annotate`
are thin compat facades; `score` / `recognition/` re-export `quiddity` until 0.6.0.
`annotation_layout_profile` owns feature-leader region policy at rank 1;
`leader_policy` remains its stable import path at that rank.
Shared three-decimal correspondence rounding, exact built-in finite-real validation, and
common profile/frame vector arithmetic live in
the rank-0 `contract_values` leaf;
shared registry evidence, outcome checks, and blind-slot value validation live inside
rank-2 `linting/_coverage_common`.
Within rank-7 `evaluation/`, `_turned_step_evidence.py` owns turned-step IR and drawing
evidence, and `_pocket_evidence.py` owns lone-pocket and pocket-pattern evidence;
`step_analysis.py` keeps the observer registry and its stable helper imports.

Key invariants — each is machine-enforced, and the guard test is the authority:

- No module but `drawing.py` touches `dwg._*`; build state is filled at one site in
  `builder._assemble` (`test_drawing_encapsulation`, ADR 1 (was 0005)).
- `annotations/` submodules import only down or sideways; the drawing is duck-typed
  as `dwg` (`test_import_boundaries`).
- Recognition runs at most once per build, owned by `BuildState`; a declared build
  recognises nothing until physical critique or export asks (ADR 3 (was 0017),
  `test_detect_once` / `test_declared_recognition_gate` fail-closed).
- Renderers emit dimensional content only from `model/compiled.py`'s plan — suppression
  is content they never receive (ADR 4 (was 0016 Amdt 1), `test_compiled_plan_boundary`,
  `test_label_provenance`) — and the converse: nothing the plan approves may reach the
  sheet stating less, or vanish unreported (ADR 4 (was 0016 Amdt 6),
  `test_issue_1215_no_approved_tolerance_is_dropped`).

## Architecture decisions — READ the five ADRs FIRST

**Before any change to layout, scaling, page selection, annotation placement, recognition,
declared intent, reporting or generation strategy, read the live records in `docs/adr/` and
follow them.** There are five, one per core aspect, each capped at 200 lines and each listing
the invariants you must not violate with the test that fails if you do:

- **ADR 1** the compiler pipeline — one engine, the IR waist, the module DAG, single-owner state
- **ADR 2** sheet layout and view planning — requirement-driven views, compose-then-pack,
  collect-then-solve placement, Policy B
- **ADR 3** the recognition boundary — external geometry-only recognition, one run per build,
  the fail-closed provider join, occurrence ownership, the framed boundary
- **ADR 4** declared intent — the IR as public input, authored sets, suppression by omission,
  the compiled-plan boundary, one declarative script
- **ADR 5** trust and honest failure — determinism, lint as an independent judge, provenance,
  documents that refuse rather than shrink

`docs/adr/archive/` holds the twenty records these replaced. They are history; nothing in them
is a work instruction, and no live code or doc may cite one as its authority (write
`ADR n (was 00NN …)` for a pointer — `tests/test_adr_corpus.py` enforces it).

**A record changes only when an invariant or boundary changes, and only with the maintainer's
sign-off before any text is written.** Adopting a provider version, adding a family, recording
ownership for one more record type, adding a report field: PR body, not ADR. If you think a
record needs to change, say so in two sentences in the PR and wait. A reviewer's recommendation
is not authorisation.

**Assess architectural fit — always.** An issue, a PR, and a review are incomplete until they
weigh the change against the five records, not just its local correctness: does a feature
round-trip recognise **+** emit **+** declare (ADR 4)? Does it fit the compiler pipeline and the
one-inventory waist (ADR 1) and the recogniser contract (ADR 3)? Does it sit at its DAG rank,
place geometry through the corridor solve (ADR 2), and extend a shared pass rather than adding a
copy? A change that is locally correct but architecturally off-pattern *is* tech debt — call it
out in the issue/PR/review, not after merge.

## Dependencies

- `build123d-drafting-helpers>=0.13.0` (Apache 2.0), `build123d>=0.9.0` (Apache 2.0)
- Export render chain: `reportlab` + `svglib` (PDF), `pypdfium2` + `pillow` (PNG) —
  all pure-wheel, no native cairo; svglib is the one weak-copyleft (LGPL) member.
- The 1D strip solve is dependency-free PAVA (`_solve_strip_1d_pava`); `kiwisolver`
  was retired.

## Testing

Tests are geometry-level — edge counts, bbox placement, face counts, lint clean
checks. Target is 100% passing. Tiers (#153):

- **`uv run pytest -m unit`** (~30 s, most of it interpreter/OCC import) — the pure-logic
  inner loop: zero OCC geometry, enforced by a conftest hook (#656). Membership is the
  `UNIT_MODULES` list in `tests/_unit_manifest.py`; grow it there.
- **`uv run pytest -m smoke`** (~30 s) — curated build-light subset for a quick
  local "did I break something obvious" check.
- **`uv run pytest`** — full fast tier (`-m 'not slow'`; nearly every test does a
  real OCC build). Prefer **targeted** selections (`-k`, node ids) locally;
  `scripts/pr-check --full` uses `-n auto --dist worksteal`, CI keeps `loadscope`. A
  critique-style test should share a module-scoped built drawing, not mint a new dense
  fixture.
- **`-m slow`** (integration builds, including CTC fixtures) — full tier in post-merge CI.
  The bounded `-m real_part_canary` tuner STEP test also runs once before merge (#827).

The suite may not grow by CLONING. `tests/test_clone_budget.py` compares test bodies by
*shape* (identifiers, attributes and literals erased) across modules, because renamed,
symbol-substituted copies evade both name and AST comparison. When it fails, parametrize
over the symbol that varies (`tests/_evidence_contract.py` is the worked example) and
ratchet `CLONE_BUDGET` down; raising it needs a reason in the PR body, like `fail_under`.

The suite may not grow by ACCRETING issue-named files. `tests/test_suite_shape.py`
pins the number of `tests/test_issue_*` modules and lets it only shrink. A regression
test goes in the module named after the behaviour it defends, as
`test_<behaviour>_issue_NNNN` (maintainer decision, 2026-09-13); the existing
issue-named modules fold into behaviour modules over time (#1637).

For reproducible build-cost profiling, run `scripts/profile-builds` with a fresh output
directory and `--expect-collected N`, where N is today's `uv run pytest --collect-only -q`
count (never a number copied from a doc); see the script's `--help`.

Coverage is kept out of the default addopts; CI passes `--cov` in two shards combined for
the Codecov upload and the `fail_under` gate. PR CI runs the fast tier across supported
Python versions plus macOS/Windows canaries and the real-part canary; the **full slow tier
runs post-merge on `main`** (#153, #827). The `full-matrix` PR label runs the wider matrix.

A PR that only bumps the next-patch development version takes a short metadata-only CI
path, proved byte-exact by `scripts/check-version-bump`; anything mixed runs normal CI.

## Working practices — evidence, not confidence

These are not style preferences. Each exists because its absence shipped a defect or a
believed-and-false claim; epic #1202 alone produced about twenty-five confidently written
false statements in commits, comments, docstrings and PR bodies, several inside the fix for
the previous one.

### Comment convention

Comments explain why the current code is as it is. Cite at most one relevant issue
when it identifies a current invariant; omit review-round citations and move history.

### Reproduce every prose claim by execution before committing it

If a commit message, comment, docstring or PR body asserts a fact about this codebase — a
count, a behaviour, "no caller does X", "this is the only Y" — run the thing that proves it.
Real claims that passed review-by-reading and failed on execution: *"`label_vs_measured` is
the only such code"* (there were five); *"any permutation fails"* (one passed all 4,092
tests); *"156 claims confirmed across every STEP fixture"* (the glob missed every `*.stp`,
and the corrected figure mixed in local files and used `build123d.import_step`).

**When measuring a corpus, say which files and through which entry point.**
`build_drawing(path)` uses `STEPControl_Reader` to avoid an XCAF segfault that
`build123d.import_step` hits on CTC-02 AP242; they are not the same code path.

### A green suite is not evidence that a guard is load-bearing

Break the rule on purpose and confirm a named test fails, and assert the substitution
applied — a run that collects nothing, or a `sed` that matched nothing, is a broken harness
reporting success. Guards that survived the entire suite until mutated: three of five
`_FIDELITY_CODES` deleted; `_owner_drawn` replaced with `return True`; `_PLANE_TOL` widened
from `1e-6` to 2.0. **Mutation results expire when the code changes.** Beware tests that pass
for the wrong reason: a determinism test comparing runs *within one process* passes on
unsorted code, because string hashing is stable for a given `PYTHONHASHSEED`.

### Every fixture asserts its own precondition

Assert the defect is present before asserting it is handled — four #1202 tests passed on
unfixed code because their fixtures never contained the defect. A precondition is necessary,
not always sufficient: where a *different* mechanism could refuse the candidate, also
assert that relaxing the named mechanism changes the outcome.

### Fix it, or state a reason you could not have manufactured

When work turns up a defect, the default is to fix it. Filing needs a reason that does not
reduce to a choice you just made: a **decision that is the maintainer's**, or you
**attempted** it and found it larger than it looked. "It is in a different file" or "a
different subsystem" is not a reason — you chose the boundary. Look first; decide after.

### Read a gate's exit code, never its output

`scripts/pr-check --static` exits non-zero on failure. Grepping its text for `error` once
hid ruff-format's "Would reformat" for several commits, so a real failure read as a pass.

## License

AGPL-3.0. Anyone running draftwright as a network service must provide their
application's source code. Contact paul@fremantle.org for a commercial licence.
