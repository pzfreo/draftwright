CTC-03's reviewed invalid H6 plan is an input to the recovery regression, not an
expected-output oracle. It was captured by the earlier release executable from
nist/nist_ctc_03_asme1_rc.stp (native recognition), at A3 1:5. The 108.0 slot
length line intersects the 2x R9.5 callout. Its bound source hash is checked
against a fresh corpus Analysis. gzip has mtime=0 for reproducibility.

The regression requires a complete valid drawing and no exhausted fallback.
The selection-unit test separately proves that an invalid seed's lower material
crossing count cannot reject a clean candidate. No part-name branch exists in
the planner.

`1004-view-gate-{first,unsafe}.plan.json.gz` record the original native
1004 stand-in at A4 2:1 and 1:1 from `385300fb`, using the local quiddity override
`2709a331`. The stand-in moves hole 1 to y=-60, z=120 and retains every recognised
recess. The otherwise valid, complete 1:1 proposal crosses two other views; the
2:1 seed crosses none. The model unit test replays that proposal through the
candidate memo for every arrangement and checks the actual recovery veto. This
keeps the original non-vacuous gate check when improved placement repairs the
live stand-in. Both fixtures bind the real source SHA and retain all carried
claims. gzip uses mtime=0.

`1004-view-gate.analysis.json.gz` is the complete unedited recognition/2 Analysis
from baseline `699acae7`. The integration stand-in uses that supported historical
analysis, checks its source SHA against a fresh native reading, and makes its
original two coordinate edits. Every original gate assertion is retained. The
separate recognition/3 replay above retains all newly recognised recesses and
verifies the same veto against an otherwise valid, complete unsafe candidate.

The CTC-03 AP242, STC-07 AP242 and shf08 STEP sources are copied from the local
`dw-examples/steps/{nist,freecad}` collection for the alpha.3 sheet-size
regressions. They supplement the sampled quiddity corpus, which does not include
these three sources. These are geometry inputs; the tests check valid sheet
selection and physical leader clearance. gzip uses mtime=0.
