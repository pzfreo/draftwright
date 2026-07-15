# Consolidation roadmap — pay down leaf-level debt before the next feature wave

Execution roadmap for the **consolidation epic #635**. Grew out of the 2026-07-15
project review: the architecture is sound (one engine under every surface, a
machine-enforced IR waist, the ADR discipline), but the render layer is accreting
special cases faster than it consolidates them. The dominant failure mode is
**regression-driven patching** — fixes land as new branches/copies citing the
issue they fixed, without the global model being re-derived. Each phase below is
one tracking issue; each work item within a phase is one PR (split if it grows).

## The evidence (what "accreting faster than consolidating" looks like)

- The identical label-clear guard is copy-pasted **five times** across
  `render_chamfers`/`render_fillets`/`render_flats`/`render_pockets`/
  `render_grooves` (`from_model.py:912/978/1042/1146/1204`) — each 0.3.0 feature
  kind landed as a fresh copy of its predecessor.
- Two 0.3.0 features (**plates #559, step-positions #555**) landed on the legacy
  `carve_free_position` path rather than the ADR 0009 corridor solve — the
  half-migrated state doesn't just persist, it attracts new work.
- Magic constants are justified by bug numbers, not invariants: the `0.62`
  text-width fudge appears three times while a real `_text_width()` metric
  exists; two dedup bases (0.5 mm radius vs 0.1 rounding) that a comment
  admits can disagree and resurrect #345.
- Comments cross-reference line numbers that have already drifted
  (`holes.py` "line 638" → now ~1279) — changes are made locally without the
  surrounding model being re-read.
- 72 distinct `dwg._*` reach-ins from `annotations/` keep `Drawing` as the
  implicit state bus ADR 0005 §2 set out to retire.

None of this is runaway. All of it steepens the cost curve of the next feature.

## The rule this epic installs

A change is not done if it (a) places geometry the corridor solve cannot see, or
(b) adds a copy of an existing pass instead of extending a shared one. Same
spirit as the #574 recognise+emit+declare round-trip rule; enforced by the guard
tests Phases 1 and 5 add and by review (the CLAUDE.md architectural-fit
checklist now names it).

## Phases

| Phase | Issue | What | Blocks / blocked by |
|---|---|---|---|
| 1 | **#636** | Finish ADR 0009: migrate remaining `carve_free_position` passes (plates, height ladder, step positions, PMI drop fallback, hole helpers) into the corridor solve; fail-closed guard test | coordinate with #426 (manual verbs) and #602 (footprints); not blocked |
| 2 | **#637** | One `_leader_callout_pass` for the five machined-feature renderers; name the magic constants; unify the dedup policy | independent |
| 3 | **#638** | Split `_annotate_holes` (~643 ln), data-drive `render_pmi` (~470 ln axis matrix), untangle `finalize`/`_classify_intents` | don't overlap #636 in the same files simultaneously |
| 4 | **#639** | ADR 0005 §2: explicit `PlacementContext`; retire `Drawing` as the build-state bus (72 `dwg._*` reach-ins → declared interface) | before Phase 6's re-targeting |
| 5 | **#640** | Enforce the DAG everywhere: extend `test_import_boundaries.py` beyond `model/`, cycle detection, resolve #523; keep CLAUDE.md/ADRs true | any time |
| 6 | **#641** | Tests specify intended behaviour: hypothesis over the layout invariants, re-target white-box `from_model` tests onto the #639 seam, decide the 0.11-kernel snapshot gap, assert `_suggest_fix` content | after Phase 4 (partially) |

**Ordering:** Phases 1–2 first — they stop the accretion (no new feature kinds
until both land). 3 is independent. 4 before 6. 5 any time.

## Relationship to existing threads

- **#426** (record/finalize manual intent verbs through the corridor solve) is
  the *authored-intent* half of the same convergence Phase 1 finishes for the
  *auto-pass*; they share the "one solve sees everything" end state.
- **#602** (measure/plan/render footprint split) makes Phase 1 cheap: corridor
  candidates measured analytically instead of via full OCCT builds. Its
  occupancy-index proposal naturally lives on the Phase 4 `PlacementContext`.
- **#523** (builder↔cli↔sheet_emit import cycle) is folded into Phase 5.
- **ADR 0005 §2** and the **ADR 0009 Current-decision header** now carry status
  notes pointing back at this epic (updated 2026-07-15).

## Done means

- `carve_free_position` has no production callers in `annotations/` outside a
  test-asserted exemption allowlist (recorded in ADR 0009).
- A sixth machined-feature kind is a table row in one shared pass, not a sixth
  function.
- No function in the render layer over ~150 lines; closure nesting ≤ 2.
- `annotations/` neither writes `dwg._*` attributes nor probes
  `getattr(dwg, "_analysis", …)`.
- A PR adding an undeclared cross-layer import fails CI.
- The layout invariants run under hypothesis; the white-box `from_model` test
  imports shrink to a counted allowlist.
