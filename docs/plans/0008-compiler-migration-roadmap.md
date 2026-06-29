# ADR 0008 migration roadmap — rewrite detectors + drawing components onto the IR

> **SUPERSEDED (2026-06-28) by ADR 0008 Amendment 2.** This roadmap described a
> *reproduce-each-engine-pass-and-swap-under-an-equivalence-gate* migration. That
> strategy was abandoned: the byte-equivalence gate forced the clean framework to
> clone the old engine's quirks (parity-first, value-last), which is the opposite
> of a robust framework. The new strategy is **out-grow, not replace** — the IR is
> the path for new/poorly-handled shapes, judged by *correctness* not equivalence;
> the engine is migrated only opportunistically. The scoped golden gate (Phase 0)
> is retired. Kept for history; do not execute the phases below as written.
> **Live plan: epic #195** (out-grow Done / Now / Next / Backlog).

Execution plan for ADR 0008 (the part-drawing compiler). Moves **every** detector
and **every** drawing component onto the `Feature`/`DimParameter` IR + planner,
behind a **scoped, disposable golden gate**, with **X/Z parity** as a standing
acceptance criterion at every step.

Strangler discipline: the engine keeps working throughout; each phase is one (or a
few) releasable PRs; old code is retired only as its replacement lands.

## Standing acceptance criteria (apply to every phase)

1. **Golden gate green** — the migration gate (Phase 0) shows no *unreviewed*
   change to the semantic dimension set for the whole corpus. Intentional changes
   regenerate the snapshot with a rationale in the PR (the ADR-0004 discipline).
2. **X/Z parity** — every feature, detector, and planner rule is exercised on both
   an X-oriented and a Z-oriented instance, and they produce the *same* dimension
   set (same values + conventions, orientation-appropriate placement). No code path
   may be gated to one axis. Parity is asserted by a dedicated test per migrated
   component, not left implicit.
3. **Orientation is data** — no new `is_rotational` / `axis == "x"` / `axis == "z"`
   branch enters the back-end. Orientation lives in `Feature.frame`; the planner
   chooses conventions by geometric rule.
4. Existing suites (geometry-level + `test_e2e_standards`) stay green; `ruff` /
   `format` / `mypy` clean.

## Phase 0 — the scoped migration golden gate (do first)

A purpose-built characterisation gate, **deleted in Phase 6**.

- **Corpus** (`tests/_migration_gate/corpus.py`): a representative part per feature
  scenario, **each present as both an X-turned and a Z-turned variant** where
  orientation applies — plain stepped shaft, bored stepped shaft, chamfered shaft,
  turned-and-drilled flange, prismatic plate with holes, bolt-circle pattern,
  slotted part, counterbored/spotfaced part. The X+Z pairing is what makes the gate
  *enforce* parity, not just hope for it.
- **Digest**: the **semantic dimension set** of the produced drawing, not bytes —
  for each part, the sorted set of `(feature_kind, param_kind, value, convention,
  target_view)` plus placed-annotation labels. Robust to layout jitter, sensitive
  to real coverage/convention changes. (No font-metric or absolute-position data —
  the ADR-0005 §3 portability lesson.)
- **Diff tool**: `UPDATE_MIGRATION_GATE=1` regenerates; otherwise a mismatch fails
  with a readable per-part diff. The snapshot is taken **now**, off the current
  engine, as the equivalence baseline.
- **Lifecycle**: lives only for the migration. Phase 6 deletes
  `tests/_migration_gate/` entirely — no standing general gate remains (ADR 0005
  §3 / the golden-gate principle).

## Inventory to migrate

**Detectors → `Feature` emitters** (front-ends; heuristics unchanged, output
normalised into the IR):

| Detector | Emits | X/Z note |
|---|---|---|
| `find_holes` (+ cbore/spotface) | `HoleFeature` | axis from `frame`; already orientation-agnostic |
| `find_turned_steps` | `StepFeature` | returns the axis; X+Z already symmetric in the recogniser |
| `find_bosses` | `BossFeature` | external ODs on non-turned parts (both axes) |
| `find_hole_patterns` | `PatternFeature` (bolt-circle / linear / grid) | — |
| `find_slots` | `SlotFeature` | — |
| `pmi` (STEP AP242) | `ThreadFeature` / GD&T features | — |

**Drawing components → planner rules** (back-end; one rule set over
`DimParameter`s):

| Component (today) | Becomes |
|---|---|
| envelope dims (width/height/depth/OD), inline in orchestrator | planner rule over the part's bounding `DimParameter`s |
| turned **diameters** (X row-below / Z column-left) | one planner rule, view+side chosen by `frame` |
| turned **step lengths** (X chain / Z ordinate ladder) | one planner rule: chain or ordinate by *crowding*, **for both axes** |
| hole callouts + location dims | planner rules over `HoleFeature` params + datums |
| section / detail views | planner-triggered from features needing them |
| PMI / GD&T pass | planner rules over PMI features |
| title block / centrelines / balloons / hole tables | planner-emitted furniture |

## Phases

- **Phase 1 — IR foundation.** *(Landed, prototype #194.)* Lock `DimParameter` /
  `Feature` / `PartModel` / `plan_dimensions`.
- **Phase 2 — detectors emit Features.** Migrate each detector into
  `build_part_model`, building the full model in parallel with the live engine.
  Gate: the model captures everything the current recognisers find, X and Z. No
  engine behaviour change yet.
- **Phase 3 — planner in production: holes first.** Route hole callouts + location
  dims through `plan_dimensions` → existing layout. Holes are the most mature and
  naturally axis-agnostic, so this proves the planner→layout seam under the gate
  with parity for free. Retire the hole placement in `annotations/holes.py`.
- **Phase 4 — turned components (the X/Z crux).** Move **diameters** and **step
  lengths** onto the planner, handling X and Z **symmetrically**: the planner picks
  row/column and chain/ordinate by `frame` + crowding rules, not by separate
  passes. This is where today's asymmetry is fixed — a Z shaft can get a length
  chain and an X shaft an ordinate ladder when the rule says so. Retire
  `_annotate_turned_diameters`, `_annotate_turned_lengths`, and the `dim_step_`
  ladder; `step_zs`/`analyse_face_levels` for steps goes too. Parity test: the
  X-variant and Z-variant of every turned corpus part produce equal dimension sets.
- **Phase 5 — remaining components.** Envelope dims, slots, patterns, section/detail
  triggers, PMI, furniture onto the planner. Retire each inline pass as it lands.
- **Phase 6 — retire scaffolding.** Delete the orchestrator's inline passes, the
  now-unused recogniser entry points superseded by detectors, and **the migration
  golden gate**. Update ADR 0008 status to "migration complete"; the engine is now
  detectors → IR → planner → layout, with no standing gate.

## Risks & mitigations

- **The sizing path** (`step_zs` → scale/page) is load-bearing; migrate it within
  Phase 4 behind the gate, last among the turned components.
- **Intentional output changes** (fixing the X/Z asymmetry will *add* dims the old
  engine lacked, e.g. a Z length chain) are expected — the gate is a *diff-and-
  review* tool, not a freeze. Each such change is reviewed and the snapshot
  regenerated with rationale.
- **Recognition stays heuristic** — contained in detectors behind the IR, not
  cleaned. Detector PRs in Phase 2 may surface latent recogniser bugs (as Phase 1
  did with the phantom bore floor); fix them at the detector, once.
- **Scope creep into a rewrite** — each phase is independently revertable; if a
  phase balloons, stop and re-plan rather than press on.
