# ADR 0007 — draftwright owns feature recognition and linting; helpers becomes the rendering library

- **Status:** Proposed
- **Date:** 2026-06-28
- **Deciders:** Paul Fremantle (pzfreo)

## Context

draftwright is a deterministic drawing compiler (ADR 0001, 0005): part →
**recognise** features → **decide** what/where to annotate → **render**
primitives → **critique** (lint) → **repair** → export. Today the *recognise*
and *critique* stages do not live in draftwright — they live in the upstream
Apache-2.0 dependency `build123d-drafting-helpers`:

- **Recognition** — `build123d_drafting.features` (1,029 LOC): `find_holes`,
  `find_bosses`, `find_hole_patterns`, `analyse_cylinders`, `feature_diameters`,
  `full_cylinders`, and the feature/pattern types `HoleSpec`, `BoltCircle`,
  `LinearArray`, `RectGrid`.
- **Linting** — inside `build123d_drafting.helpers`: `lint_drawing` and its
  `_lint_*` structural checks, `find_overlaps`, `find_interferences`, and the
  `LintIssue` type.

This placement has become an active drag, and the trigger was concrete. While
adding axial step-length dimensioning for turned parts (the drive-screw gap:
every diameter dimensioned, no shoulder locatable), two facts surfaced:

1. **Recognition semantics are fixed by the wrong owner.** `find_bosses`
   reports a step's *cylindrical-face* length (`.height`), shortened by chamfers.
   That is correct for the diameter pass but wrong for axial lengths — the spans
   do not tile the axis or sum to the overall length. The "right" behaviour
   depends on the *consumer's* intent, yet the recogniser lives a repo away from
   the consumer. Recognition drifts from intent when it lives apart from the code
   that knows the intent.
2. **Iterating recognition or lint requires a cross-repo release dance.** Any
   tweak means: change helpers → release helpers → bump `>=0.x` in draftwright.
   For the lint rules — the feedback source of the ADR-0002 lint→repair loop,
   draftwright's most-iterated subsystem — this release wall is paid constantly.

The conceptual boundary is **render vs. reason**. helpers should *render* an
annotation (the objects, their styling, their coordinate frames). draftwright
should *reason*: recognise geometry, decide placement, and judge correctness.
Recognition and linting are reasoning; they belong with the reasoner.

This ADR does not re-open 0001–0006. It moves two stages to their correct home
and sets the long-term shape of the dependency.

## Decision

### 1. draftwright owns feature recognition and all linting

Both the *recognise* and *critique* stages move into draftwright. The dividing
principle for the helpers boundary is sharpened from "drawing objects only" to:

> **helpers renders; draftwright reasons. helpers contains no feature
> recognition, no linting, and no layout *decisions*.**

- **Recognition** → a new `recognition/` subpackage in draftwright (subpackage,
  not a single module: it is ~1,000 LOC and growing, e.g. the new turned-step
  recogniser). Indicative split: `holes.py`, `bosses.py`, `cylinders.py`,
  `patterns.py`, plus the feature/pattern dataclasses. It depends only on
  build123d — the bottom of the import DAG, below `_core`.
- **Linting** → folded into a `linting/` package alongside the existing
  `linting.py`. Indicative split: `structural.py` (`lint_drawing` + `_lint_*`),
  `coverage.py` (the current `lint_feature_coverage` + `CoverageState`),
  `issues.py` (`LintIssue`), `suggest.py` (`_suggest_fix`). Structural lint
  duck-types the drawing objects, so it imports `Dimension`/`Leader` **from
  helpers** — a thin, one-directional dependency draftwright already has.

### 2. helpers keeps the rendering substrate, not just bare objects

helpers retains the annotation objects (`Dimension`, `Leader`, `HoleCallout`,
`Centerline`, `TitleBlock`, the GD&T family, …) **and the infrastructure they
need to render**: `Draft`/`draft_preset`, `set_page`, and the view-coordinate
frames (`ViewCoordinates`/`view_axes`). These are rendering *substrate*, not
content decisions; stripping them would gut the objects. The standalone
placement conveniences (`place_dims`/`place_labels`) may stay in helpers — they
are not content recognition and draftwright does not import them (it has its own
`layout.py`, ADR 0003).

### 3. Migrate by deprecate-and-vendor, not by removal

We do **not** delete anything from helpers in this work.

- **Vendor**: copy the recognition and lint source into draftwright; switch every
  draftwright import to the local copy; vendor the corresponding tests so the
  code is iterable here in isolation. Add a one-line provenance note on each
  vendored module (`originally build123d_drafting.<x>, vendored #NNN`).
- **Deprecate**: in a *separate* helpers release, mark the vendored symbols with
  a `DeprecationWarning` + docstring note pointing at draftwright. External
  consumers get notice; nothing breaks.
- **Freeze**: the helpers copies stop changing. All future iteration happens in
  draftwright. helpers may delete them in a later major version once nothing
  depends on them.

This is the strangler-fig pattern: no flag day, no cross-repo release in the
critical path of draftwright work, full backward compatibility.

### 4. Licensing

Vendoring is sound: the same author (pzfreo) owns both repos, and Apache-2.0 is
compatible with AGPL-3.0 — the vendored copies become AGPL within draftwright.
The provenance note (§3) is for honesty, not legal necessity.

### 5. Migration order (each step its own releasable PR)

- **PR-A** — vendor recognition → `recognition/`; switch draftwright imports;
  vendor recognition tests. helpers untouched.
- **PR-B** — vendor lint → `linting/`; switch imports; vendor lint tests. Give
  draftwright its own `LintIssue` (helpers keeps its own for its standalone
  validators — structurally identical dataclasses; duplication over coupling).
- **PR-C** — the original goal, now built on owned code: add `turned_steps`
  recognition (with axial-length-correct shoulder extraction, fixing the #1
  semantics problem) + the step-length annotation pass + the scoring fix.
- **PR-D** (separate repo, off the critical path) — deprecation warnings in
  helpers.

## Consequences

- **Recognition and lint become iterable in one repo.** The lint→repair loop
  (0002) is fully owned; recognition semantics can be fixed for the consumer that
  needs them (the drive-screw step lengths) without a helpers release.
- **A clean render-vs-reason boundary.** helpers is a dumb rendering library;
  draftwright is the intelligence. New recognition/lint has an obvious home.
- **Temporary duplication.** Two copies of recognition/lint exist until helpers
  deletes its versions. This is *not* DRY in the interim — the helpers copies are
  frozen-and-wrong once draftwright's diverge (e.g. `find_bosses` length
  semantics). Accepted cost; the alternative (cross-repo coupling) is worse.
- **CLAUDE.md dependency story changes.** "Sits on top of two Apache 2.0
  libraries… `build123d-drafting-helpers` — annotation primitives" stays true,
  but the framing shifts: helpers is the *rendering* library; draftwright owns
  recognition + lint. Update CLAUDE.md when PR-A lands.
- **~1,700–2,000 LOC vendored** before the drive-screw dimensions land. Mostly
  mechanical, but real review surface — hence the staged PRs.

## Risks

- **Divergence confusion.** A future contributor patches the helpers copy by
  habit. Mitigation: the deprecation warning (§3, PR-D) and the provenance note
  point to draftwright as the source of truth.
- **Vendored lint's dependency on helpers objects.** `lint_drawing` duck-types
  `Dimension`/`Leader`; if helpers later changes those objects' geometry API,
  draftwright's lint must track it. This dependency already exists today — the
  move does not add it — but it is now explicit and one-directional.
- **Test drift.** Vendored tests must actually run here, not silently reference
  helpers internals. Each PR vendors its tests and must be green before merge.

## Impact on other ADRs

- **0002** (lint→repair) — strengthened: the lint half of the loop is now owned,
  so the loop is iterable without an external release. No decision changes.
- **0005** (pipeline modules / DAG) — extended: `recognition/` is a new bottom
  layer (depends only on build123d, below `_core`); `linting/` generalises the
  current `linting.py`. Import direction is unchanged (lower never imports
  upper). The `Drawing.lint()` wiring is unaffected.
- **0001, 0003, 0004, 0006** — unaffected.

## Related

- ADR 0001 (deterministic compiler), 0002 (lint→repair), 0005 (pipeline modules).
- Trigger: turned-part axial step-length dimensioning (the drive-screw gap —
  every diameter dimensioned, no shoulder locatable; `find_bosses` length
  semantics wrong for the consumer).
