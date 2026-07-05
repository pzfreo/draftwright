# ADR 0011 — The IR as a public input: declare features, don't only detect them

- **Status:** Accepted — Phase 0 (the `model=` seam + object→feature constructors) and
  Phase 1 (the `Sheet` façade) landed; Phase 2 (aspect renderers — tolerance/GD&T/finish)
  pending per the #446 roadmap. Phase 2 execution plan:
  [`docs/plans/0011-phase2-aspects-roadmap.md`](../plans/0011-phase2-aspects-roadmap.md).
- **Date:** 2026-07-05
- **Deciders:** Paul Fremantle (pzfreo)

## Context

draftwright compiles a solid into a drawing through one waist: the **IR
`PartModel`** — a list of frozen `Feature` dataclasses (ADR 0008). Everything
downstream of it — the planner, render-intents, the ADR-0009 layout, lint,
export — reads *that*, never the raw solid. Today there is exactly **one producer**
of the IR: **feature detection** (`recognition/` → `build_part_model`), which
recovers features from the finished solid's silhouettes.

Detection is the right default — the common case is "hand me a STEP, draw it." But
it has two structural limits:

1. **It can be wrong.** Silhouette recognition misclassifies. #298 is the canonical
   case: a ⌀6 band nested under a ⌀10 OD collapsed to ⌀10 in the max-silhouette read
   and went silently undimensioned; detection's own `feature_diameters` also *missed
   a blind hole entirely* in the #445 prototype. The engine cannot always out-detect
   the person who built the part.
2. **It re-derives what the caller already knows.** When the part is built
   parametrically (e.g. the `pzfreo/gramel` `thumbwheel_drive_screw`, which holds
   `boss_diameter = 6`, `thread = "M3"` as literal parameters), re-recovering ⌀6 from
   the silhouette is redundant at best and, per (1), unreliable at worst.

ADR 0001 Amendment 1 established that *both inputs converge at the detected IR* and
that the edit surface is the model; #400 made the model a **read** surface
(`dwg.model()`) plus imperative edit verbs (`callout`/`locate`/`dimension`/`section`).
The missing half is symmetric: let the caller **supply the IR as an input**, so
detection becomes *a* producer of the model, not the *only* one.

The `proto/declare-features` spike proved the seam is small: the orchestrator already
honours a pre-set `dwg._part_model` (`orchestrator.py:170`) — `build_drawing` merely
always overwrote it with detection.

## Decision

Make the IR `PartModel` a **first-class public input**, on equal footing with
detection. Concretely:

1. **A `model=` input to the build entry point.** `build_drawing(part, model=…)`
   accepts a caller-supplied `PartModel` (or a `Sequence[Feature]`, wrapped with the
   part's bbox + a default corner datum). When supplied, **detection is skipped** and
   the auto-pass renders the declared features. `model=None` is the unchanged default —
   detect. Detection and declaration are two producers of the *same* IR; **everything
   downstream is untouched.**

2. **Object → feature constructors.** `hole` / `boss` / `step` / `slot` / `pattern` /
   `envelope` read a feature's geometry off a known build123d object (a cylindrical
   face → radius / axis / location) — *geometry supplies the value* — with an
   explicit-value flavour (`hole(diameter=6, at=…, axis="z")`) for parametric code
   that never built a discrete tool.

3. **Declaration is not all-or-nothing — the hybrid is first-class.** The caller may
   start from `dwg.model()` (detected) and **override** specific features. Declaration
   is for where you know better than detection, not everywhere.

4. **Aspects geometry cannot carry attach as decorations, not on the frozen IR.** A
   *tolerance* is a property of a **dimension** (`DimParameter`); *GD&T* and *surface
   finish* are annotation kinds **keyed to a feature/face** in a decoration side-layer
   consumed by their renderers. The frozen `Feature` schema stays clean, so every
   existing consumer keeps ignoring what it does not understand. (The renderers
   themselves are deferred engine work — #61 / #62.)

The declarative `model=` path is the **from-scratch authoring** mode ("I know all the
features"); the #400 imperative verbs remain the **incremental-edit** mode ("this
detected drawing needs one more thing"). Two complementary modes, one IR.

## Consequences

- **Misdetection becomes recoverable by construction** — you declare ⌀6 and it is
  drawn as ⌀6, full stop. The #298 class of bug cannot silence a feature you named.
- **Parametric/generated code reads as the drawing** — you reference the feature you
  built and decorate it with intent; no restating numbers the model owns. This is the
  foundation of the #445 "beautiful-Python drawing DSL".
- **The IR schema becomes a public contract.** Its field shapes are now an API surface,
  so changes to `Feature`/`DimParameter` carry versioning discipline they did not before.
- **The detector is no longer privileged.** It is one front-end; a future front-end
  (PMI, a CAD feature tree, an LLM) can produce the same IR.
- **Known caveat — analysis and the coverage lint still detect.** `_analyse` recovers
  `a.holes` etc. to size the sheet and estimate strips, and `lint_feature_coverage`
  re-detects the part's holes/diameters to check coverage — both independently of
  `model=`. So a supplied model overrides *dimensioning*, but sheet estimation still
  detects (the auto-scale heuristic may differ slightly), and a **partial** declaration
  is correctly flagged by the coverage lint for the geometry it left undimensioned (the
  drawing is right — those features genuinely have no callout). A full declared-model
  bypass of estimation + coverage is a follow-up, not a blocker.

## Alternatives considered

- **Improve detection only.** Necessary and ongoing, but it cannot close the gap: #298
  shows the engine cannot always out-detect the author, and parametric callers already
  hold the exact values. Detection stays the default; this ADR adds a second producer,
  it does not replace the first.
- **Imperative edit verbs only (the #400 surface).** Complementary, kept — but
  "I know every feature" is inherently *declarative*, and driving a from-scratch drawing
  through per-annotation imperative calls is more ceremony than declaring the model and
  letting the auto-pass place it coherently.
- **Tag build123d objects with drawing metadata as you model.** Couples the modelling
  code to draftwright's vocabulary and spreads drawing intent through the part build;
  the `model=` input keeps the two concerns separate (build the part, then describe its
  drawing).
- **Extend the frozen IR with tolerance/GD&T fields.** Rejected for the schema churn and
  because tolerance is per-dimension, not per-feature; the decoration side-layer keeps
  the IR minimal and the existing consumers untouched.

## Relationship to other ADRs

- **Extends ADR 0001 Amendment 1** (both inputs converge at the detected IR) — the
  caller is now a first-class *producer* of that IR, not only an editor of it.
- **Builds on ADR 0008** (the unified feature model / dimensioning planner) — the
  PartModel waist is exactly the seam that makes a single `model=` input possible with
  zero downstream change.
- **Complements #400** (the editable surface): read (`dwg.model()`) + edit (verbs) +
  now **input** (`model=`).
