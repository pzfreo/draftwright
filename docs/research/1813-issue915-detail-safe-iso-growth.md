# #1813 issue915: grow the temporary iso seed without losing DETAIL A

The [v4 fixed-sheet trial](1813-bounded-chooser-trial-v4.json) chose the
`legacy-depth` candidate for issue915 on A2 at 1:2. It removed one crossing,
but left the isometric view at the staggered-side layout's temporary 65% seed:
only **0.4225×** the baseline iso area. The rendered page was not a visually
verified win. The `detail_a` guard in `_settle_iso_view` prevented the normal
obstacle-aware growth pass, even though the pass already checks other views and
annotation ink.

| Existing v4 baseline | Existing v4 candidate | This PR's candidate |
| --- | --- | --- |
| [PDF-rendered PNG](../images/1813-v4-issue915-baseline.png) | [PDF-rendered PNG](../images/1813-v4-issue915-candidate-preview.png) | [PDF-rendered PNG](../images/1813-issue915-detail-safe-iso-growth.png) |

This PR caps growth at sheet scale when a detail exists, and includes that
detail and its annotations in the clearance search. One changed-source,
candidate-first build of `tests/fixtures/issue_915_case_study_2.step` on the
same A2/1:2 sheet returned iso scale 0.5 and area **1.0×** the saved baseline.
The candidate kept its zero-crossing quality key, DETAIL A, all five step
dimensions, its marker and caption; the saved-baseline comparator found semantic
parity and no introduced blocker identities. The three images were rendered
from their SVGs via the PDF exporter at 72 dpi; no baseline or corpus was rebuilt.

SVG provenance (SHA-256): v4 baseline
`86fdf969ab6224c03a07ab19fbd0ec206b0d53e3d76bd0c43696bb1e06bc3bcb`,
v4 candidate
`6202d5a31a495a7c1fa433f154f8f8edf9e96a5ebcd578caf50575dfb0574615`,
this PR's candidate
`d5436aa531191cbc233a1de7da7b62f6b06c94935e4fa2c6586474aad3a72a46`.

This fixes one visual regression, **not** the #1813 rollout gate. Issue915 still
has a dropped section and unsupported required outcomes; the other six shrinking
cases and the broader safety/cost/holdout gates remain open.
