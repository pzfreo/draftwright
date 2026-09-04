# ADR 1 — The compiler pipeline

- **Status:** Accepted (2026-09-04). Consolidates archived 0005 and 0015 and the retired
  `docs/target-architecture.md`.
- **Deciders:** Paul Fremantle (pzfreo)

## Decision

Draftwright is a compiler. It turns a build123d solid or a caller-declared feature model into a
deterministic technical drawing through one path:

```
recognisers (b123d_recognisers)          declared features (model/declare.py)
   geometry-only records                    the caller supplies what it knows
          │                                          │
          ▼                                          ▼
   model/detect.py  ────────────►  PART MODEL — the IR waist  ◄────────────
   typed adapter registry          model/ir.py: Feature / DimParameter / Datum /
                                   PartModel — frozen dataclasses, one inventory
                                                     │
   model/planner.py: plan_dimensions / plan_locations / plan_sections
                                                     │
   model/compiled.py: compile_dimensions → RenderableDimensionPlan + diagnostics
                                                     │
   render intents → annotations/ (from_model.py, holes.py, sections.py, …)
                                                     │
   shared placement (ADR 2) · projection.py · export.py — fed, never absorbed
                                                     │
                                                  Drawing
```

There is one engine. Every user-facing surface — `build_drawing`, `make_drawing`, `Sheet`, the
CLI, script generation — is a front door onto `build_drawing → _auto_annotate`. There is one
feature inventory per build, detected once, and page/scale sizing reads the same model the
renderers do. Orientation and feature kind are data in the IR, never code branches in the
back end: the planner derives a group's view from `Feature.frame` by one rule, so X and Z are
symmetric.

Each compiler stage has one module home, and the module graph is a strict layered DAG that no
runtime import may climb. Build-time state has one typed owner, `BuildState`, filled at one site
in `builder._assemble`; `Drawing` is the public editable object and not a communication bus.
Annotation identity belongs to the registry; coverage state belongs to lint.

Lint is deliberately outside the waist. `linting/` may not import `draftwright.model`: a
completeness check that read the plan or the IR could not flag what the planner omitted.

## Invariants

1. **The import graph is a ranked DAG.** Every module is in `_LAYERS`; no runtime import points
   up; the `annotations/` subpackage imports only down or sideways; `TYPE_CHECKING`-only upward
   references are enumerated. `test_import_boundaries.py`.
2. **`Drawing` is not a state bus.** Render passes take `PlacementContext` and make zero private
   `Drawing` reads (the allowlist is empty and fail-closed); no engine module touches `dwg._*`;
   `BuildState` has one construction and one fill site. `test_drawing_encapsulation.py`
   (`test_no_engine_module_touches_drawing_privates`,
   `test_build_state_has_a_single_construction_and_fill_site`, `test_no_build_context_probing`).
3. **One inventory per build, detected once.** `_analyse` builds the `PartModel` up front;
   `_assemble` attaches it before the `auto_dims` gate; the orchestrator reads it rather than
   rebuilding. `test_detect_once.py`, `test_part_model.py`.
4. **Two front doors, one waist.** Detection and declaration emit the same `Feature` types into the
   same `PartModel`; downstream branches on no producer flag except the one that gates parity
   behaviours (`model_declared`). `test_part_model.py`, `test_declare.py`.
5. **Records cross only at `model/detect.py`.** The adapter registry is fail-closed over the
   provider's public record universe, derived mechanically from return annotations; each record
   type has exactly one home. `test_detect_registry.py`, `test_external_recognition_boundary.py`.
6. **Orientation is data.** `Feature.frame` carries the axis; view derivation is one rule
   (`_END_ON` / `_PROFILE`); no `kind == "step"` branch chooses a view. `test_turned_steps.py`,
   `test_principal_profile_classifier.py`.
7. **Dimension-bearing passes consume planner output.** `plan_dimensions` runs once per pass
   sequence; every `render_*` of generated dimensions is on the `plan` contract; the exceptions
   (correlated ladders, PMI, GD&T, the declared gear table) are enumerated by contract, and the
   `groups` tier is empty. `test_compiled_plan_boundary.py`.
8. **One owner per physical measurement.** Two features that measure between the same two faces
   reconcile by support-plane coincidence; the overall extent takes the fact; nothing is deduplicated
   by value; a toleranced measurement is never handed over.
   `test_issue_1154_one_owner_per_measurement.py`, `test_issue_1153_contradictory_dimensions.py`.
9. **`linting/` imports no `draftwright.model`.** `test_import_boundaries.py`
   (`test_linting_does_not_import_model`).
10. **Expired compatibility surfaces stay deleted.** The seven `Drawing` aliases, `generate_script`,
    `--style imperative` and `sheet_dsl` are asserted absent, not merely undocumented; every
    remaining compat surface carries a tracking issue and a removal date.
    `test_drawing_encapsulation.py` (`test_the_expired_compat_aliases_stay_deleted`,
    `test_the_deleted_modules_and_stubs_stay_deleted`), `test_deprecation_dates.py`.
11. **Reporting sits beside lint at rank 2** and consumes the shared requirement ledger explicitly;
    it never reaches through `Drawing`. `test_import_boundaries.py`.
12. **Private test reads are a shrinking allowlist.** `test_private_test_attr_reads.py`,
    `test_private_test_imports.py`.

**Unguarded.** That `_PASS_SEQUENCE` is the *only* stage tuple is enforced by structure (`run_stages`
takes it) rather than by a mutation test; ADR 2 invariant 6 cites the nearest behavioural guard.

## Boundaries

- **ADR 2 (layout).** Receives render intents and owns everything after: composition, packing,
  the strip solve, view planning. This ADR owns that the plan is compiled in one place and fed,
  never reabsorbed.
- **ADR 3 (recognition).** Owns the provider join and the one-run lifecycle. This ADR owns the
  adapter registry's shape and the rank of every module that consumes recognition.
- **ADR 4 (declared intent).** Owns what an authored set means. This ADR owns that declaration is
  the second front door and reaches the same waist with zero downstream change.
- **ADR 5 (trust).** Owns what lint may conclude. This ADR owns the structural carve-out that
  keeps lint independent.

## Superseded

- 0005 — the module split (`make_drawing.py` 3,907 lines → a 17-line facade); three state
  owners, not one god-object; the golden harness that gated the split, retired once it was done;
  aliases deleted at 0.4.0 with their absence asserted; reporting re-ranked beside lint (#1438).
- 0015 — the compiler as built, superseding 0008's aspirational "one rule set": planner coverage
  stated honestly, the lint carve-out given a body, lint permitted to compare a claim against the
  plan (#1217), cross-feature reconciliation by support plane (#1154). 0008's step-1 unified Z
  step recognition stands.
- `docs/target-architecture.md` — its pipeline diagram and load-bearing rules are the Decision
  above.

## Open

- **One build-owned compile under one formatting policy** (proposed 0019 §4): `compile_dimensions`
  has ~32 call sites and is recomputed during rendering and verification; the plan should be
  compiled at most once per build and self-describe its policy.
- **Hole callouts (`hc_`)** remain on the legacy surface, honouring suppression per term.
- **`lint_prismatic_coverage(recognition=...)`** accepts a foreign aggregate (#1032; ADR 3).
- **`_classify_geometry`** lives in analysis and feeds recognition; moving it is tidying (#1037).
