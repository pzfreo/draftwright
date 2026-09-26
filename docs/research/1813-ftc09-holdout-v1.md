# #1813 independent public STEP holdout: NIST FTC-09 AP242

This is a **public real-part** holdout outside the fixed 15-part CTC/other corpus. It
does not substitute for a user-supplied large CAD model. NIST identifies FTC-09 as one
of its fully-toleranced test cases and [permits unrestricted use of the STEP files](https://www.nist.gov/ctl/smart-connected-systems-division/smart-connected-manufacturing-systems-group/mbe-pmi-0),
while requesting acknowledgement; the model is credited here to NIST's MBE PMI
Validation and Conformance Testing Project. No NIST logo is used.

- Source archive: `NIST-PMI-STEP-Files.zip`, [NIST download](https://www.nist.gov/document/nist-pmi-step-files),
  SHA-256 `8fa78429e6d8d9b0d7681d223b6aa9ec98c3772185c55b1a0e3679b21c181911`.
- Archive member: `NIST-PMI-STEP-Files/nist_ftc_09_asme1_ap242-e1.stp` (AP242 with PMI),
  retained byte-for-byte as [`nist_ftc_09_asme1_ap242-e1.stp`](../../tests/fixtures/nist_ftc_09_asme1_ap242-e1.stp).
  Size 6,109,836 bytes; SHA-256
  `f1215fe15a78085a9fa78dd81714caf774b59071be92580b04ebfdc19f52a1bd`.
- Import-only preflight: one solid, bounding size approximately 227.028 × 5.252 ×
  279.4 mm. This is a large *file/PMI* case, not a physically enormous part.
- Versioned caller constraints: [`annotation-layout-holdout-ftc09-v1.json`](../../tests/fixtures/annotation-layout-holdout-ftc09-v1.json),
  A2 at 1:1, SHA-256 `9012b99e4b6719f0357d5eeee61c1c8c17f33502aa10ca86bfec86f9615c4efa`.
  The first bounded run uses code commit `b160f2e6d789ac137e4c2537cfa9ce6b70e0a017`,
  candidate-first with one concurrent case and a 600-second bound **per isolated
  worker**. Baseline is built only in the offline comparison runner, not in the
  production candidate-preview path.

## First bounded comparison (one run, no retry)

The baseline and candidate-preview workers both completed within their individual
600-second bounds. The comparison exited zero, but this is **not a candidate win**:
the quality keys were identical, `[21, 63, 11, 0, 9, -1.0, 249480.0]`, and the
verdict was `tie`. The same A2 page, 1:1 scale, front/plan/side/iso views,
annotation manifest, and coverage survived. Semantic parity passed with no missing
or newly added annotations, no new interior dimensions, no new blockers, and no
changed drop identity. The rendered PDFs and 72-dpi PNGs were byte-identical
between baseline and candidate; the PNG SHA-256 is
`55203ec1dbc676db51173c68b6999c7c571459ba9d6bd9dbb362c52256ff2830`.
The [shared baseline/preview page](../images/1813-ftc09-ap242-a2-1to1-baseline-and-preview.png)
is retained for visual review.

The independent admission gate **failed** (`required_outcomes`,
`recognized_ownership`, `lint_blockers`, `audited_coverage`). Both layouts placed
53 of 109 requirements, with 54 dropped and 2 unsupported; both reported 90 lint
errors and 36 warnings. The visible page remains crowded, with callouts/PMI at or
beyond the upper edge and a very narrow side view; matching the baseline is not
proof of legibility. This case adds an independent real-part *non-win* and exposes
existing completeness/layout problems. It does not pass the broader real/user STEP
or visual-review gate, and no production candidate was selected or fallback used.

On the 4-core, 7,941-MiB Linux x86_64 runner, baseline/candidate process times
were 203.70/210.33 seconds, build times 176.44/180.17 seconds, and peak worker RSS
661.29/593.88 MiB. These are **single samples**; any nearest-rank p95 printed by
the runner for this one-case corpus is the same sample, not a meaningful latency
budget. Supported-platform cost budgets remain open.
