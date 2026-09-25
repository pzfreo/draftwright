# Bounded pre-render chooser trial (#1813)

The actual `candidate-preview` path reached the fixed-sheet corpus threshold: **12/15 strict
offline wins, 3 ties, and 15/15 semantic parity**. All candidates used the caller's fixed
page and scale, and the offline comparison found no missing annotations, introduced
required blockers, or new interior dimensions. The compact per-case record is
[`1813-bounded-chooser-trial-v4.json`](1813-bounded-chooser-trial-v4.json).

This is **not** a default-switch or safety-admission result. Only grm04 passed the
independent provisional checks; all 15 have `admission_ready=false`, and production
selected none. CTC02 and CTC04 still have visibly crowded GD&T/leader regions (tracked
in #1830). The isometric area is also smaller than baseline in several strict-win cases:
CTC01 0.56×, CTC02 0.81×, whistle 0.46×, issue915 0.42×, lever 0.42×, frame
0.69×, and sloped 0.68×. The quality key's strict win does not waive view-legibility
review. A broader AP203/user-part/Sheet holdout is #1831; platform cost budgets are #1832.

## Provenance

- Trial source: `4ab713e52834b5852416bbbdad428c62a9b28605`. The stacked chooser
  commit `e0a828d0669a5529f12de7da4c45f5901dd2fba8` has the same net code/test
  patch (stable patch-id `321a4b5dac93bee9b7e8ff0920a0b077ba9f5567`) on top of
  the later evidence and safety slices.
- Manifest: `tests/fixtures/annotation-layout-corpus-v2.json`, version 2.0.0;
  metric version 1. Every case used its declared fixed sheet and scale.
- Command: `scripts/annotation-scheme-corpus --manifest tests/fixtures/annotation-layout-corpus-v2.json --output <run-dir>/cases --candidate-first --jobs 2`.
- Host: Linux x86_64, 4 logical/physical cores, about 7,941 MiB RAM. The candidate
  worker rendered one pre-chosen profile; a separate baseline worker supplied only
  offline comparison. This trial did not rerun the previously passed paired corpora.
- Candidate process median/p95: 46.21/278.21 s; baseline process median/p95:
  39.94/274.54 s. Candidate peak RSS median/p95: 537.27/877.68 MiB. These are
  unbudgeted, same-host corpus observations, not a supported-platform production cost
  result. Preview fallback frequency is unknown, not zero.

The chooser is deliberately experimental and based only on pre-render demand counts,
unplanned demand, and reserved-corridor pressure. These thresholds were developed against
the fixed 15-part set, so the holdout in #1831 is required before treating the rule as
general. The default remains `baseline`; the preview remains opt-in and does not use a
finished baseline drawing to choose its profile.
