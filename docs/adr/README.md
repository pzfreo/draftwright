# Architecture decision records

Five live records, one per core aspect of Draftwright. Read them before changing anything they
govern. Each states a decision, then a numbered list of invariants an agent must not violate,
each naming the test that fails if it is — that list is the reason the document exists.

| ADR | Owns | Guard tests (representative) |
| --- | --- | --- |
| [1 — The compiler pipeline](0001-compiler-pipeline.md) | One engine, the `PartModel` IR waist, the module DAG, single-owner build state, the compiled plan fed to placement | `test_import_boundaries.py`, `test_drawing_encapsulation.py`, `test_detect_once.py`, `test_compiled_plan_boundary.py` |
| [2 — Sheet layout and view planning](0002-sheet-layout-and-view-planning.md) | Requirement-driven view planning, compose-then-pack, collect-then-solve placement, Policy B, scale-before-sheet | `test_carve_free_position_callers.py`, `test_layout_cleanliness.py`, `test_issue_1146_scale_completeness.py`, `test_issue_1260_view_constraints.py` |
| [3 — The recognition boundary](0003-recognition-boundary.md) | External geometry-only recognition, one run per build, the fail-closed provider join, occurrence ownership, the framed boundary | `test_recogniser_capabilities.py`, `test_declared_recognition_gate.py`, `test_detect_registry.py`, `test_issue_1357_framed_boundary.py` |
| [4 — Declared intent](0004-declared-intent.md) | The IR as public input, authored dimension sets, suppression by omission, the compiled-plan boundary, one declarative script | `test_declare.py`, `test_label_provenance.py`, `test_parameter_id.py`, `test_sheet_identity_invariant.py` |
| [5 — Trust and honest failure](0005-trust-and-honest-failure.md) | Determinism, lint as an independent judge, provenance, reports that refuse rather than shrink, absences reported | `test_export_reproducible.py`, `test_render_seam.py`, `test_issue_1215_no_approved_tolerance_is_dropped.py`, `test_issue_1438_report_projection.py` |

`archive/` holds the twenty records these replaced, frozen, with their filenames and numbers
unchanged. Each is stamped with the live record that now owns its decision. They are the
why-trail; nothing in them is a work instruction.

## Rules of engagement

These exist because the previous corpus reached 21 files, 8,787 lines and one record with 28
amendments, and its own "at roughly four amendments, write a successor" rule was a convention
that nothing enforced. Each rule below is enforced by `tests/test_adr_corpus.py` where a test can
enforce it.

1. **A record changes only when a boundary or invariant changes.** Adopting a provider version,
   adding a feature family, recording ownership for one more record type, adding a report field:
   that is a PR body, not an amendment. If the change does not alter the Invariants section, it
   does not touch the record.
2. **No record text is written without the maintainer's sign-off.** An agent or contributor who
   believes a record needs to change says so in the PR in two sentences and waits. A reviewer
   recommending an ADR is a recommendation, not an authorisation.
3. **Each live record is at most 200 lines; the five together at most 1,000.** Enforced.
4. **No live document cites an archived record as its authority.** Code, tests and docs cite
   ADR 1–5. A pointer into the archive is written `ADR n (was 00NN …)` so the history stays
   findable while the live number is the one a reader acts on. A bare `ADR 00NN` outside
   `archive/` fails the guard.
5. **Every invariant names its guard.** One without a test is listed under *Unguarded* with the
   reason, never mixed into the numbered list. The guard checks that every named test module and
   test function exists. That list is expected to shrink.
6. **Cite symbols and test names, never source line numbers.**

## Reading order

Pipeline first (1), then whichever aspect the change touches. Layout (2) and declared intent (4)
each assume the pipeline; recognition (3) and trust (5) each assume the other four's boundaries.

The consolidation itself is recorded in `docs/plans/adr-consolidation-plan.md`.
