# ADR 4 — Declared intent

- **Status:** Accepted (2026-09-04). Consolidates archived 0001, 0011, 0012 and 0016; 0019 is
  carried as an open item.
- **Deciders:** Paul Fremantle (pzfreo)

## Decision

Draftwright's public input is its own intermediate representation, spoken in the vocabulary of
the drawing domain — holes, bores, steps, sections, "dimension this bore's depth" — and never in
the vocabulary of the layout engine. There is no editable layout DSL, no imperative script, and
no page coordinate on any authoring surface. A caller who knows the part better than detection
does declares features; a caller who knows which measurements matter declares dimensioning
intent; the engine derives every value from the referenced geometry and places every mark.

Detection and declaration are two producers of the same frozen `PartModel`. Everything downstream
is one path. A declared model is built without recognition; a hybrid — detect, then override — is
first-class.

Dimensioning intent has exactly two sources and a build must name one: the planner's automatic
set, or an authored set that is **complete** — so that omission means suppression, with no hidden
mode. Suppression is not a flag renderers check; it is content they never receive. The compiler
produces approved entries and diagnostics; renderers decide where and how, never what. The one
generated output is a declarative `Sheet` script that mirrors the planner's intent line for line,
never the placed annotations, so solver pressure can never rewrite version-controlled source.

A user's edit to a placed dimension is a scale-independent intent — pin, priority — drained
through the same corridor solve as the automatic set. The raw single-slot placement verb survives
only as a documented, deprecated escape hatch.

## Invariants

1. **No authoring surface carries page geometry.** A `dimension(...)` line names a feature and a
   measurement and carries no number; the emitter reads the addressable plan, not the drawing.
   `test_compiled_plan_boundary.py` (`test_the_emitter_reads_only_the_addressable_result`),
   `test_sheet_emit.py`.
2. **Detection and declaration emit the same IR into the same model.** Declared features render
   with the furniture detection would produce; no renderer branches on producer.
   `test_declare.py`, `test_part_model.py`, `test_issue_1350_detected_takeover.py`.
3. **A declared build performs no recognition** (ADR 3 invariant 2).
   `test_declared_recognition_gate.py`.
4. **Renderers emit dimensional content only from the compiled plan.** `ApprovedDimension` has no
   `suppressed` field; the feature crosses as an opaque ref; no renderer formats a number the
   compiler did not; the migrated-renderer roster is pinned by contract and the `groups` tier is
   empty. `test_compiled_plan_boundary.py`, `test_label_provenance.py`
   (`test_no_renderer_formats_a_number_the_compiler_did_not`, `test_the_ratchet_actually_bites`).
5. **Two explicit dimension sources; mixing raises; requesting neither raises;
   `add_dimension` requires `auto_dimensions()`.** `test_add_dimension.py`,
   `test_sheet_identity_invariant.py` (`test_dimension_intent_verb_roster_is_closed`).
6. **Dimension identity is the parameter id, derived, never positional.** `role.kind[.discriminator]`
   uniformly; a bare role naming more than one measurement is refused; an ambiguous selector raises
   rather than guessing. `test_parameter_id.py`, `test_dimension_role_vocabulary.py`,
   `test_add_dimension.py` (`test_an_ambiguous_role_raises_rather_than_guessing`).
7. **Suppression marks; it never infers.** No renderer derives an engineering fact from a parameter's
   absence (a blind hole never reads `THRU` because its depth was omitted); a compound callout's head
   cannot be suppressed while its dependents remain. `test_suppression_marks.py`.
8. **Authored omission and planner suppression are distinct ledger rows.**
   `test_suppression_ledger.py` (`test_an_authored_omission_is_distinguished_from_a_rule_suppression`).
9. **Nothing the plan approves may vanish silently** (the converse of 4). A tolerance reaches the ink
   through the label; an undrawable approved mark is reported against its measurement.
   `test_issue_1215_no_approved_tolerance_is_dropped.py`.
10. **A contingency is approved content, not a renderer's inference.** A fallback ladder is compiled,
    carried inactive, and released only when the primary places nothing.
    `test_issue_955_height_contingency.py`.
11. **Edits are intents in the shared solve.** `dimension(..., pin=, priority=)` records a
    scale-independent intent drained by `finalize()` through `_PASS_SEQUENCE`; `place_dim` is the
    deprecated raw fallback. `test_issue_563_placement_intent.py`.
12. **Sheet identity is invariant between declaration and build.** A mutation after declaration is
    invisible; materialisation is idempotent; exactly one resolver bears identity; the verb and handle
    rosters are closed. `test_sheet_identity_invariant.py`.
13. **The generated script executes what it emits, for every kind, and fabricates no geometry.** A
    detected part is emitted as numbers at a part-seam, never as reconstructed build123d objects; a
    live-source reference is emitted only on a polarity-checked one-to-one match.
    `test_sheet_emit.py`, `test_script_detail_parity.py`, `test_e2e_standards.py`.
14. **Authored views with automatic dimensions is refused** — requirements determine views, not the
    reverse (ADR 2 owns the planner; this ADR owns the ban as intent).
    `test_issue_1260_view_constraints.py`.
15. **Automatic dimensioning is the detected front door and carries no warning**; on `Sheet`,
    authored is preferred and `auto_dimensions()` is soft-deprecated with no removal date.
    `test_soft_deprecation.py`.

**Unguarded.** Lint's redundancy check (a pattern's per-hole locations *and* its pitch) does not
exist. Hole callouts (`hc_`) honour suppression per term but remain on the legacy surface.

## Boundaries

- **ADR 1 (pipeline).** Owns the compiler shape and where `compile_dimensions` runs. This ADR owns
  the rule that its output is the only dimensional content renderers see.
- **ADR 2 (layout).** Owns placement entirely. Intent never chooses a view, strip or coordinate;
  `view=`/`side=` on a dimension line is a derived-with-override routing hint, not a placement.
- **ADR 3 (recognition).** Recognition breadth equals editable-vocabulary breadth: the edit surface
  can only name what the recognisers detect. A declaration is recognition-free at build time.
- **ADR 5 (trust).** Owns how omissions, drops and withholdings are reported and kept distinct.

## Superseded

- 0001 — deterministic correctness over a bespoke editable DSL; Amendment 1 made the detected IR the
  edit surface; Amendment 2 retired `generate_script` and `--style imperative` (deleted at 0.4.0).
- 0011 — the IR as public input: `model=`, object-reading constructors, hybrid override, aspects as
  decorations; the mode-3 emitter chose a part-seam over reconstruction on principle; role-keyed
  decorations; fail-closed named-source emission.
- 0012 — edits as pinned, ranked corridor candidates. The one global automatic-plus-user recompose
  was a target, not a delivery: `finalize()` drains recorded intents against committed annotations
  as obstacles.
- 0016 — declared dimensioning intent; Amendment 1 the compiled-plan boundary (eight review rounds
  found eight renderers ignoring an advisory flag, so the flag was deleted); Amendment 3 the parameter
  id as canonical spelling; Amendment 4 authored preferred on `Sheet`; Amendment 5 contingencies;
  Amendment 6 the converse rule.

## Open

- **0019 (proposed): display-complete labels and a dimension-outcome ledger.** `ApprovedDimension`
  gains `display_text` so renderers never compose a tolerance; outcomes reconcile at one seam on both
  routes; per-mark identity as a separate type; one build-owned compile under one formatting policy.
- **Post-build global recompose** — suppressing an *automatic* dimension from `finalize()` needs the
  automatic population reconstructed (#867).
- **Inter-feature spans** need `RelationDimensionId`'s selector spelling; until then they emit as a
  comment, and authored views inherit that gap.
- **Durable feature identity** for an intent that must survive re-detection; per-member location
  addressing (#883); the remaining authoring-completeness work (#62, #462, #495).
