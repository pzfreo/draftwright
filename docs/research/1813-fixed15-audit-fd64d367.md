# #1813 fixed-15 candidate-first audit at `fd64d367`

This is a **failed rollout gate**, not a candidate adoption decision. The
merged `main` source `fd64d3677bad6553d5c80d71cc1828bbbb3c07fc` ran the
versioned fixed-sheet manifest
`tests/fixtures/annotation-layout-corpus-v2.json` (SHA-256
`e38d88bfc400638e692ea029e750a6684e48a6c05f6bf5955c477bb5848ae050`)
once on Linux x86_64, four logical CPUs, 7,941 MiB RAM. The command used
`scripts/annotation-scheme-corpus --candidate-first --jobs 3 --require-wins 10`,
with `PYTHONPATH=src` and the project's installed Python environment. Baseline
was rendered only by the offline comparator, not by the normal candidate-first
build. The full local JSON report has SHA-256
`9fd8cd41c3d26113766ee08eb4e7ef76817b02bd502d9f419bde23581f4bd28c`.
All 15 pairs completed; the gate command exited 1.

The report gives **7 metric candidate wins, 3 ties, 1 metric baseline win, and
4 ineligible cases**. Semantic parity passed in only **11/15**; a metric win
is not automatically a visual win. The four failures are:

| Case | Candidate-only loss or blocker | Other observation |
| --- | --- | --- |
| CTC02 | Two imported geometric-tolerance leaders dropped (`...:89`, `...:98`). | The candidate improves the quality key, but cannot count while required PMI is lost. Absolute crowding remains shared. |
| CTC03 | Three imported PMI leaders dropped (one datum and two geometric tolerances). | Crossings fall 5→0, but required drops rise 3→6. The candidate is visually cleaner, not semantically equivalent. |
| CTC05 | Hole Y-location `279.4` and overall width `527.1` disappear; a PMI leader is added and a location blocker is introduced. | Crossings fall 2→0, but the exchange of requirements is not parity. |
| Frame | Hole X-location `27.8` disappears and its location blocker is introduced. | The pre-render planner selects `iso-growth` after a proposed `planned` view exceeds the page by 0.69 mm; iso area falls to 0.386× baseline and hard violations rise 1→4. |

Whistle retains semantic parity but is a **material visual regression**:
three new dimension-line-over-label conflicts between each pad's length and
long-axis position dimension. Its isometric grows, but that does not cancel
hard ink violations. The other seven candidate verdicts and three ties remain
case-level evidence only; the 10/15 *visually verified*, no-material-regression
criterion is not met. CTC02/CTC04 need only relative non-regression, never an
invented absolute one-page safety pass, but CTC02's candidate-only PMI loss
must be fixed or explicitly adjudicated before adoption.

The same source's [manual full CI run](https://github.com/pzfreo/draftwright/actions/runs/36355037640)
was canceled after this independent fixed-15 gate failed. Its still-running
slow/platform jobs were not restarted; a repaired final code head requires one
new exact-head full/slow run. The report and rendered pages in the local run
directory are diagnostic artifacts, not a claim that a clean lint result makes
any crowded sheet complete.
