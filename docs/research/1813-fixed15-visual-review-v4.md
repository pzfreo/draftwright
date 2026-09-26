# #1813 fixed-sheet visual review: bounded chooser v4

For the case-by-case relative-visual rubric across all 12 metric wins, including
the later issue915/lever/whistle repairs, see the
[fixed-sheet visual ledger](1813-fixed15-relative-visual-ledger.md). It does not
claim that the composite evidence is a current-head 15-case gate pass.

This reviews **existing** candidate-first outputs from source
`4ab713e52834b5852416bbbdad428c62a9b28605` and the versioned
[v4 metric summary](1813-bounded-chooser-trial-v4.json). No drawing was rebuilt.
The six A2, 1:5 CTC02/04/05 SVGs from that run were converted to PDFs through
draftwright's PDF exporter, then rasterized at 72 dpi from those PDFs for visual
inspection. The PNGs below preserve the reviewed pages; they are not new CAD
results. The raw local SVGs are not a durable artifact, so their SHA-256 values
are recorded below for provenance.

| Case | Baseline page | Candidate-preview page | Visual and gate finding |
| --- | --- | --- | --- |
| CTC02 | [PNG](../images/1813-v4-ctc02-baseline.png) | [PNG](../images/1813-v4-ctc02-candidate-preview.png) | Relative gain: hard violations 24→23 and crossings 4→0. Tradeoff: the iso area shrinks to **0.808×** baseline; crowded GD&T below the plan and a long upper-plan leader persist. The candidate's 43 required blockers fail independent safety. The route gain is real, but an overall visual win is not yet established. |
| CTC04 | [PNG](../images/1813-v4-ctc04-baseline.png) | [PNG](../images/1813-v4-ctc04-candidate-preview.png) | Relative gain: hard violations 12→11 and crossings 1→0, with unchanged iso area. Dense dimension ladders above the plan and crowded lower-view callouts persist; the candidate has 38 blockers, 11 overlaps, and 2 interior dimensions. This does not pass independent safety, even though it improves on the baseline. |
| CTC05 | [PNG](../images/1813-v4-ctc05-baseline.png) | [PNG](../images/1813-v4-ctc05-candidate-preview.png) | Relative gain: crossings 5→1 with unchanged iso area and retained required content. The lower-right orthographic view still has crowded interior dimensions/leaders; 2 blockers, 5 overlaps, and 5 interior dimensions remain. This is an improvement, not proof of absolute legibility. |

All three are strict **relative metric** wins on the same caller page/scale with
semantic parity and no newly introduced blocker identities. Their independent
candidate safety checks fail. These are different questions: a hard CTC sheet
can improve over its equally constrained baseline without either sheet being
safe to manufacture from. Pre-existing blockers do not erase a relative gain;
they also do not become acceptable just because a few crossings disappeared.
The images support a narrower conclusion than the quality key alone: the
candidate sheets are still dense and incomplete. CTC02's smaller iso and long
leader specifically prevent an unqualified overall visual-win call from this
review, while CTC04/05 have visible local gains but unresolved legibility.

## Isometric shrinkage across the fixed 15

The v4 report's `verified_wins: 12` counts 12 quality-key candidate verdicts. The
comparison accepts an improved defect key even if the isometric view shrinks;
it considers iso *growth* only as a tie-break gain. Seven of those 12 metric wins
have a smaller iso area on the same page and scale:

| Case | Candidate/baseline iso area | Pre-render profile |
| --- | ---: | --- |
| issue915-a2-1to2 | 0.4225 | legacy-depth |
| lever-a3-5to1 | 0.4225 | legacy-depth |
| whistle-a3-1to1 | 0.4647 | legacy-depth |
| ctc01-a3-1to5 | 0.5645 | legacy-depth |
| sloped-a3-2to1 | 0.6786 | legacy-depth |
| frame-a3-2to1 | 0.6904 | planned |
| ctc02-a2-1to5 | 0.8081 | columns |

These ratios are geometric evidence of view shrinkage, **not** by themselves a
calibrated legibility verdict for every part. In particular, the full 15-case
visual review and a defensible view-size criterion are still missing. Therefore
12 metric wins must **not** be presented as 12 visually verified wins or as a
passed #1813 fixed-sheet rollout gate. No win count is recomputed here by
arbitrarily disqualifying a ratio; the gate remains open pending review and a
layout/chooser change or case-specific justified acceptance. The separately
established 15/15 semantic parity and zero introduced blocker identities are
not invalidated by this visual finding.

## Reviewed source SVG SHA-256

| Case | Baseline | Candidate-preview |
| --- | --- | --- |
| CTC02 | `30cdd4ec9bd9d8705e28d25a9c62757b0641723e8db8f803518fb8947d79431b` | `ddaa8e44eaa897dc4109ee934ae5ab9c070c8ef89d40f8feb19e7a373af22610` |
| CTC04 | `dafcb5d2e8c774f8ff843b2fdd9dd6b3fda998dc3d77bfb4586e3701fadf6ead` | `bd711aa6a36da3cd28bafee6a59ffd2faad57086a1d79a442ad1e4629d39ad6f` |
| CTC05 | `b5350c7076a50fb12b173d533760b697c70f0ad6c5a12012bacbb5ba722fd0a9` | `f513cd5e674f9def8d14224f73c1631a394d6b95f2067502794c04663c7086e2` |
