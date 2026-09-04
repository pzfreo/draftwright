# ADR 5 — Trust and honest failure

- **Status:** Accepted (2026-09-04). Consolidates archived 0002, 0006, 0010 and the reporting
  half of 0017 (Amendments 21–25).
- **Deciders:** Paul Fremantle (pzfreo)

## Decision

The engine's first obligation is to tell the truth about what it drew, what it did not, and
what it does not know. Three things make that possible and are held as rules rather than
aspirations.

**Determinism is a precondition, not a feature.** The same input produces the same sheet on every
platform. Text is measured and rendered from bundled, path-pinned fonts; the placement solver is
an exact, dependency-free algorithm with a fixed tie convention; nothing samples, rasterises or
searches stochastically. A layout that varies with the host is a layout whose correctness cannot
be judged.

**Lint is an independent judge.** It reads the placed drawing and the recognised geometry, never
the compiled plan as its inventory: a feature the planner omitted must still be flagged. Quality
is reported as separate components — completeness, restraint, legibility, fidelity — never a
composite, and the completeness figure is `audited_score`, named for its bound in the field name
itself because the qualifier in a sibling key is lost the moment the number is quoted. Repair is
an allowlisted safety net for one mechanically clear case; the structural cure for a collision
class is a placement rule, not a peephole.

**Every statement carries its provenance, and every absence is reported.** An annotation knows
which intent produced it, recorded once at the render seam and never parsed back out of a name.
What the sheet claims is checked against what the compiler approved. An approved measurement that
cannot be placed is reported against that measurement, without gating the build. A machine-readable
document refuses rather than shrinks its denominator, serializes no identity meant to survive
another run, and names its own limit — `bounded-clear` is not readiness, and no document infers
material, process, finish, thread, fit or tolerance intent.

A guard is load-bearing only when breaking the rule on purpose fails a named test. A green suite
proves nothing about a guard that was never mutated.

## Invariants

1. **Same input, same sheet, every platform.** Fonts are bundled and referenced by path; the strip
   solver is `_solve_strip_1d_pava` with a lower-median tie rule; exports are reproducible.
   `test_export_reproducible.py`, `test_issue_1196_deterministic_view_names.py`.
2. **Lint derives no completeness denominator from the plan or the IR.** `linting/` imports no
   `draftwright.model`; a completeness check starts from recognition and the placed drawing.
   `test_import_boundaries.py` (`test_linting_does_not_import_model`). Lint *may* compare a claim
   the drawing makes against the plan — that contributes no denominator.
   `test_issue_1217_the_facility_is_shared.py`.
3. **Repair is allowlisted and idempotent.** `repair.py` handles `dim_inside_part` only, never
   executes an arbitrary suggestion, attempts once, and never increases issue counts.
   `test_make_drawing.py` (`test_repair_dim_inside_part_flips_side`,
   `test_repair_idempotent_on_clean_drawing`, `test_repair_does_not_increase_issue_counts`).
4. **Provenance is recorded once, at the seam, for every feature kind.** `annotations_of(feature)`
   equals exactly what `drop(feature)` removes; no pass resolves a feature from an annotation name.
   `test_render_seam.py`, `test_make_drawing.py` (`test_drop_is_complete_for_a_multi_feature_prismatic_part`,
   `test_drop_is_complete_for_a_turned_part`, `test_drop_is_complete_for_side_drilled_holes`).
5. **Quality is components; completeness names its bound.** No composite score; `audited_score`
   with `excludes` and an `unrecognised_geometry_reports` floor; fidelity asks whether what the
   sheet says is true. `test_quality_components.py`
   (`test_the_audited_score_carries_its_qualifier_in_its_own_name`),
   `test_issue_1176_quality_fidelity.py`.
6. **A claim on the sheet is checked against the compiler, not against a pattern.** A measurement
   the plan never approved is reported (`claimed_measurement_not_compiled`); an approved tolerance
   reaches the ink verbatim through the label. `test_issue_1217_claimed_representations.py`,
   `test_issue_1215_no_approved_tolerance_is_dropped.py`.
7. **An approved dimension that is not drawn is reported against its measurement, and does not
   gate the build.** `overall_dim_withheld` / `step_dim_withheld` carry the measurement identity
   and are retracted if a later pass draws it. `test_issue_1215_envelope_tolerance.py`,
   `test_issue_1215_no_approved_tolerance_is_dropped.py`.
8. **An authored omission is distinguishable from a planner omission.** The suppression ledger
   names the rule or the author, one row per fact. `test_suppression_ledger.py`
   (`test_an_authored_omission_is_distinguished_from_a_rule_suppression`).
9. **A report refuses rather than shrinks.** An unclassified occurrence, foreign result, absent
   model or missing ownership raises `ReportUnavailableError`; IDs are document-local; no
   `FeatureRef`, `FaceRef`, topology index or object address is serialized; NaN/Infinity are
   rejected. `test_issue_1438_report_projection.py`.
10. **Report persistence is explicit and atomic.** `write_report` flushes a sibling temporary and
    replaces; a failure leaves the destination untouched; export does not write reports as a side
    effect. `test_issue_1438_report_writer.py`.
11. **Direct CLI rendering writes the report beside its output by default**; `--no-report` opts
    out. `test_issue_1438_cli_report_sidecar.py`.
12. **Generation-time evidence comes from one hashed byte snapshot.** The source is resolved once
    and read once; recognition, PMI and any correction build consume a private copy; A→B→A
    replacement or symlink retargeting cannot split provenance from the generated model.
    `test_issue_1438_generation_snapshot.py`.
13. **A clear result names its bound.** `bounded-clear` and `no_unrepresented_accepted_occurrences`
    are the literals; nothing is called complete or ready. `test_issue_1438_report_projection.py`,
    `test_issue_1438_generation_snapshot.py`.

**Unguarded.** Lint's coverage of "everything a competent reviewer would flag" is a goal, not a
property; a blind spot is a silent pass. Visual-channel defects (which row a section should cut)
are outside lint by definition and rest on a human or a vision reader.

## Boundaries

- **ADR 1 (pipeline).** Owns where the plan is compiled and that lint has no `model` import. This
  ADR owns what lint may and may not conclude from the plan.
- **ADR 2 (layout).** Owns why a placement drops and records it (`placement_unsatisfiable`, the
  solve trace). This ADR owns that the drop is reported against its measurement.
- **ADR 3 (recognition).** Supplies the evidence: one run, exact ownership, provider-verbatim
  records. This ADR owns how that evidence is written down and what it may claim.
- **ADR 4 (declared intent).** Owns suppression-by-omission and the compiled-plan boundary. This
  ADR owns that authored and planner omissions stay distinguishable in every ledger.

## Superseded

- 0002 — the build → critique → domain-fix loop; suggestions advisory; repair allowlisted. Built.
- 0006 — bundled IBM Plex by path; osifont rejected on licence; the byte-exact golden harness it
  enabled was later retired in favour of geometry-level and standards suites.
- 0010 — one provenance seam at intent → render. Its universal `origin` back-link was never built:
  the seam links annotation → feature directly and recognition → IR resolves by position.
- 0017 Amendment 21 — report schema v1 over the raw occurrence ledger; 22 — atomic persistence;
  23 — Plate ownership; 24 — a gap snapshot embedded in generated Python; 25 — the CLI sidecar
  default; 26 — reports project the recognition-owned requirement ledger, never a denominator
  rebuilt from IR.

## Open

- **A dimension-outcome ledger** (proposed 0019): dimensions do not yet participate in the
  `placed / dropped / withheld` outcome pattern hole, slot, channel and flat requirements use;
  absence is reported per site rather than reconciled at one seam.
- **What the recogniser rejected** is not obtainable without a second run (b123d-recognisers#494);
  every document that would carry it must say it is absent (ADR 3).
- **Redundancy lint** — over-dimensioning that is neither identical nor coincident is not reported.
