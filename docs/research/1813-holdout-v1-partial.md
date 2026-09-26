# #1813 candidate-first holdout v1: interrupted run and visual review

This is **partial, failed-to-complete evidence**, not a candidate-first holdout pass. The
six-case manifest is [`annotation-layout-holdout-v1.json`](../../tests/fixtures/annotation-layout-holdout-v1.json).
It adds AP203 variants of CTC01/02/04, non-PMI GRM03, a single-cylinder STEP, and a
flat-across-cylinder STEP. The AP203 CTC variants share geometry with fixed-corpus AP242
cases; they are format holdouts, **not independent geometric wins**. This set also does not
yet contain a novel large user STEP outside the fixed 15.

## Run provenance and outcome

- Run source commit: `74fb65ec0417bd67c15453977f6e14d2f2b61d35`; bounded chooser
  implementation: `e0a828d0669a5529f12de7da4c45f5901dd2fba8`.
- Manifest SHA-256: `40ec88cc4fb285eddae941afa9cc366951a38a1d14457bc13f5966d2698f0a0d`.
- Linux x86_64, Python 3.13.5, four available logical CPUs, 7,941 MiB RAM, no swap.
  The command used `--candidate-first --jobs 2` with the caller-fixed pages and scales
  in the manifest. The baseline and preview workers are separate offline evidence;
  production candidate-preview does not build the baseline.
- Five cases produced both SVGs: CTC01/02/04 AP203, GRM03 without PMI, and the single
  cylinder. The runner version in this run buffered its JSON until **all** cases finished,
  so it wrote **no per-case semantic comparison** and the aggregate report is empty.
- The final `if_step_flat_across_cylinder.step` baseline worker produced no SVG. At
  termination it had run about 51 minutes wall / 34 minutes CPU and used roughly 700 MiB
  RSS. The parent command ended with exit code 143 (SIGTERM); the source of that signal
  was not established. The candidate worker for this case was never reached. This is a
  baseline/runner cost outlier, **not** evidence of a candidate regression.
- The run was not restarted. PR #1836 now writes completed case JSON atomically for
  future runs and records workflow provenance before CAD work, but cannot reconstruct
  this pre-change run's missing JSON.

## Visual review of completed AP203 pairs

The following 100 dpi page images were rasterized from the already-finished SVGs via
draftwright's SVG→PDF→PDFium path. They are review aids, not substituted metrics or
semantic manifests. Open each image at full resolution to inspect the blue annotations.

| Case | Baseline | Candidate preview | Observation |
| --- | --- | --- | --- |
| CTC02 AP203 | [page](../images/1813-holdout-v1-ctc02-ap203-baseline.png) | [page](../images/1813-holdout-v1-ctc02-ap203-preview.png) | The preview still crowds dimension/callout text around the orthographic views, and its isometric is visibly smaller. No visual safety pass. |
| CTC04 AP203 | [page](../images/1813-holdout-v1-ctc04-ap203-baseline.png) | [page](../images/1813-holdout-v1-ctc04-ap203-preview.png) | The preview shows a section A–A view, but dense stacked dimensions remain; long blue annotation strokes span the lower-right blank corridor. No visual safety pass. |

## Gates still open

No semantic ownership/requirement parity, blocker delta, verified win count, or
candidate safety verdict can be inferred from five SVG pairs. The pathological final
case needs diagnosis under a bounded run; the larger real/user-part sample, remaining
PDF reviews, supported-platform cost budgets, and independent safety admission remain
open. Do not change defaults or claim fallback removal from this report.
