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
- `b123d-recognisers` — deterministic geometry-only feature recognition (ADR 3 (was 0013)).
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
  `_geometry.py` (page-plane maths + the ADR 2 (was 0014 Amdt 3) material field; the DAG's
  bottom leaf), `fonts.py` (pinned IBM Plex, ADR 5 (was 0006)), `fits.py` (ISO 286),
  `intents.py` (deferred-edit low IR), `recognition_cache.py` (ADR 3 (was 0017) one-result
  lifecycle), `recognition_ownership.py` (run-local accepted-occurrence outcomes, including
  final-IR binding, group/pattern absorption, and settled ownerless consumer policy),
  `plate_correspondence.py` (pure shared Plate-record/final-IR correspondence predicates),
  `recogniser_policy.py` (single Draftwright-owned unsupported/deferred/evidence-only policy),
  `recogniser_schema.py` (consumer-owned public record schema versions shared by validation and
  reporting),
  `recognition_frame.py` (ADR 3 (was 0020) prepared local-frame boundary and fail-closed
  body-local occurrence joins), `blend_contract.py` (strict released-Blend schema and
  occurrence identity),
  `_warnings.py`, and `_pmi_part21.py`.
- **`_core.py`** — shared primitives: the `Analysis` namespace, dim/format helpers,
  page/slot/margin constants.
- **Stage modules** — `analysis.py` (classification + one-shot feature inventory),
  `projection.py` (HLR + material lowering), `compose.py` (the ADR 2 (was 0004) outer
  compose-then-pack layout), `export.py` (SVG→PDF→PNG chain, DXF), `repair.py`
  (the ADR 5 (was 0002) lint→repair safety net), `pmi.py` (STEP AP242 PMI), `linting/`
  (draftwright-owned lint and recognition requirement ledgers, ADR 3 (was 0007)), `reporting.py`
  (versioned projection over explicitly supplied finished-build state and those ledgers),
  `model/` (the ADR 1 (was 0015) IR waist: `ir`/`detect`/`planner`/`declare`/`compiled`),
  `drawing.py` (the `Drawing` result object; single-owner `BuildState`), and
  `annotations/` (the render passes; `orchestrator.py` owns `_PASS_SEQUENCE`, the one
  canonical stage order; `_common.py` owns the corridor solve + late-furniture seam).
- **`builder.py`** — build orchestration: `build_drawing`, `make_drawing`.
- **Facades / top layer** — `make_drawing.py` + `annotate.py` (thin compat),
  `sheet.py` (the fluent `Sheet` facade, ADR 4 (was 0011)), `sheet_emit.py` (the `--script`
  emitter), `cli.py` (Typer; engine imported lazily inside command bodies, #313),
  `_build_profile.py` (developer-only pytest/runner profiling support),
  `evaluation/` (the versioned STEP-analysis benchmark — production code must never
  depend on benchmark expectations or scores), `recogniser_contract.py` (the
  fail-closed cross-repository capability join), and `inspection_contract.py` (the
  separate fail-closed declared-geometry inspection join).
- `score.py` / `recognition/` — temporary identity-preserving re-exports of
  `b123d_recognisers`; removal scheduled for 0.6.0.

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
  `_UNIT_MODULES` list in `tests/conftest.py`; grow it there.
- **`uv run pytest -m smoke`** (~30 s) — curated build-light subset for a quick
  local "did I break something obvious" check.
- **`uv run pytest`** — full fast tier (`-m 'not slow'`; nearly every test does a
  real OCC build). Prefer **targeted** selections (`-k`, node ids) locally;
  `scripts/pr-check --full` uses **`-n auto --dist worksteal`** to balance the long
  tail on many-core developer machines. At 4734 collected tests this measured
  159–172 s across three green 18-core runs, versus 266 s with `loadscope`
  (2026-08, #1311). On the same 18-core host, limiting pytest to four workers
  measured 358 s with `worksteal` versus 348 s with `loadscope`; that does not
  model CPU affinity or a hosted runner. CI deliberately retains its established
  class/module scope grouping with `loadscope`. The tier grows with every
  trust fix; a critique-style test should share a module-scoped built drawing,
  not mint a new dense fixture.
- **`-m slow`** (CTC fixture builds) — CI-only.

For reproducible build-cost profiling, use a fresh output directory and state the expected
collection census explicitly:

```bash
scripts/profile-builds --output /tmp/draftwright-profile \
  --expect-collected 4740 -- tests/ -n auto --dist loadscope
```

The runner passes every module/option as a literal argv entry, writes one JSON file per xdist
worker, and refuses to report success when any worker's collected count differs. It times the
public builder binding, `Sheet`'s import-time builder binding, and `_build_drawing_once`, and
records pytest phases of at least 5 ms for attribution. Do not reuse an output directory that
already contains worker profiles.

Coverage is kept out of the default addopts (it adds ~13% locally); the CI
workflow passes the `--cov` flags. CI runs the full fast tier (3×3 OS/Python
matrix, parallelised with `-n auto`) on every PR; the **slow tier runs post-merge
on `main`**, not as a PR gate (#153) — a regression there is caught right after
merge rather than blocking every PR for ~19 min.

## Working practices — evidence, not confidence

These are not style preferences. Each one is here because its absence produced a
defect that shipped, or a claim that was believed and false. Epic #1202 alone
produced roughly twenty-five confidently-written false statements in commit
messages, comments, docstrings and PR bodies — several written *inside the fix
for the previous one*, twice as a PR's own headline.

### Reproduce every prose claim by execution before committing it

If a sentence in a commit message, comment, docstring or PR body asserts a fact
about this codebase — a count, a behaviour, "no caller does X", "this is the only
Y" — run the thing that proves it. Not "I read the code and it looks true".

Real examples, all of which passed review-by-reading and failed on execution:

- *"`label_vs_measured` is currently the only such code"* — there were five.
- *"any permutation fails"* — one passed all 4,092 tests.
- *"the union of these registers is exactly the set of codes the engine emits"* —
  41 against 55.
- *"`representation_features` is populated by nothing"* — true of the field, but
  the reason given was wrong, and the neighbouring live parameter was nearly
  deleted with it.
- *"156 claims, 156 confirmed across every STEP fixture"* — the script globbed
  `*.step` and half the fixtures are `*.stp`. The corrected figure was **also**
  wrong: it mixed repo fixtures with files from a local directory and used
  `build123d.import_step` instead of the engine's `analysis._import_step`.

**When measuring a corpus, say which files and through which entry point.**
`build_drawing(path)` and `build123d.import_step(path)` are not the same code
path — the first uses `STEPControl_Reader` specifically to avoid an XCAF segfault
the second hits on CTC-02 AP242.

### A green suite is not evidence that a guard is load-bearing

Break the rule on purpose and confirm a named test fails. Assert the substitution
applied — a run that collects no tests, or a `sed` that matched nothing, is a
broken harness reporting success.

Guards that survived the **entire** suite until mutated, each with a test sitting
next to it: three of five `_FIDELITY_CODES` deleted outright; `_owner_drawn`
replaced with `return True`; `_PLANE_TOL` widened from `1e-6` to **2.0**; both
halves of an availability predicate *and* its fail-safe override.

**Mutation results expire when the code changes.** Re-run them for anything a
later commit touches. And beware tests that pass for the wrong reason: a
determinism test comparing runs *within one process* passes on unsorted code,
because string hashing is stable for a given `PYTHONHASHSEED`.

### Every fixture asserts its own precondition

A test that the defect is present, before asserting it is handled. Four tests in
#1202 passed against completely unfixed code because their fixtures never
contained the defect — a "this thickness is now printed once" test whose geometry
produced no duplicate in the first place; an "everything else is unaffected"
generator that was empty.

A precondition is necessary and often not sufficient: a candidate can exist and be
refused by a *different* mechanism than the one under test. Where that is possible,
also assert that relaxing the named mechanism changes the outcome.

### Fix it, or state a reason you could not have manufactured

When work turns up a defect, the default is to fix it. Filing needs a reason that
does not reduce to a choice you just made:

- it needs a **decision that is the maintainer's**; or
- you **attempted** it and found it larger than it looked.

**"It is in a different file" and "it is a different subsystem" are not reasons.**
You choose which files a change touches, so citing that boundary is circular — it
lets any defect be deferred by declining to open the file. Look first; decide
after. A reason produced before looking is a justification for what you already
did.

### Read a gate's exit code, never its output

`scripts/pr-check --static` exits non-zero on failure. Grepping its text for
`error` once hid ruff-format's "Would reformat" for several commits, so a real
failure read as a pass.

## License

AGPL-3.0. Anyone running draftwright as a network service must provide their
application's source code. Contact paul@fremantle.org for a commercial licence.
