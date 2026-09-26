# Candidate-first corpus, first offline run (#1813)

The first run of the actual `annotation_layout="candidate-preview"` path does **not** meet the
default-switch gate. On the fixed-sheet 15-part corpus it produced 6 strict offline layout
wins, 2 ties, 1 quality regression, and 6 semantically ineligible results. Semantic parity
held in only 9 of 15 cases; the independent provisional safety checks passed in only 1.
No result was safety-admitted or production-selected. The compact, per-case machine-readable
record is [`1813-candidate-first-evidence-v1.json`](1813-candidate-first-evidence-v1.json).

## Provenance and method

- Source commit: `c37ed57a9d691033b0732cc115ecac44de57957f` (before the later
  CLI type/coverage/sparse-inventory review fixes, which did not alter profile selection).
- Manifest: `tests/fixtures/annotation-layout-corpus-v2.json`, version 2.0.0, with its
  fixed page and scale for each case.
- Host: Linux 6.8.0-139-generic x86_64, 4 logical CPUs, 7,941 MiB physical RAM.
- Command: `scripts/annotation-scheme-corpus --manifest tests/fixtures/annotation-layout-corpus-v2.json --output <run-dir>/cases --candidate-first --jobs 2`.
- Each candidate request built `candidate-preview`; a separate baseline worker supplied
  **offline** semantic and quality comparison. There was no runtime baseline comparison
  or automatic fallback in the preview request. The existing paired-selector corpora were
  not rerun.

All 15 pre-render decisions chose `legacy-depth`: every case had either unplanned typed
requests or under-reserved corridors. That conservative rule erased the profile diversity
that made the earlier opt-in `best` result reach 13 wins and 2 ties. It is the first
selection rule to revisit, using only pre-render obligations and sheet facts—not a finished
baseline quality key. CTC02, CTC04, and CTC05 were among the semantically ineligible
cases, with respectively 6, 5, and 2 missing annotations in the offline comparison.
CTC01 improved its scored layout but still failed the absolute provisional safety checks;
that distinction matters when the baseline itself has unresolved obligations.

The corpus summary's `selected` field describes the *offline quality-comparison verdict*,
not production selection. `rendered_candidate=15` and `production_selected=0` are the
relevant preview facts. This run did not collect the newer process-time/peak-memory fields,
did not review borderline PDFs visually, and cannot establish a production budget or
default-switch readiness. The next evidence run must use a changed chooser; repeating
this exact one would add no information.
