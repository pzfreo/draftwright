# #1831 GRM03 generated-Sheet holdout: one semantic tie, not admission

This is one **candidate-only front-door check plus one missing offline baseline
script worker**, not a complete #1831 holdout or a current-head rollout gate.
The source is the vendored plain STEP
[`grm03_thumbwheel_drive_screw.step`](../../tests/fixtures/grm03_thumbwheel_drive_screw.step)
at code commit `0f178488bb21666317432f2cf58e5aaac92f7d32`. Both scripts used
caller-fixed A3 at 5:1, `title='GRM03 CANDIDATE HOLDOUT'`, and
`number='GRM03-CANDIDATE-HOLDOUT'`. Generation used `generate_sheet_script`
with `formats=()` and `inspect=False`; the emitted Python was executed once
per layout policy. The candidate script was part of a direct-automatic versus
generated-Sheet check; only the baseline script was subsequently run to fill
the missing offline comparator. No fixed-15, baseline-direct, or already
completed candidate worker was restarted. The STEP SHA-256 is
`b4d455810969ee6644e73f3ee0576b43dc0937eeb2589a4383c931d126b6d285`;
the workers ran on Linux x86_64 / Python 3.12.14. Their single process times,
including script generation, were 17.20 s candidate and 15.11 s baseline;
these are not representative latency statistics.

| Observation | Direct automatic candidate | Generated Sheet candidate | Generated Sheet baseline |
| --- | --- | --- | --- |
| Layout policy | `candidate-preview` | `candidate-preview` | `baseline` |
| Selected candidate profile | `legacy-depth` | `legacy-depth` | not applicable |
| Page / scale | A3 / 5:1 | same | A3 / 5:1 |
| Annotation inventory | 18 names | same 18 names | same 18 names |
| Lint codes | none | none | none |
| Audited physical requirements | 15 placed / 15 | 7 placed, 8 unverifiable / 15 | 7 placed, 8 unverifiable / 15 |

All 18 **scripted** baseline and candidate annotations agree on type, label,
view, exact measurement claims, and owners; neither has missing or added
semantic carriers. The baseline script's quality key is
`[0, 0, 0, 0, 0, -5, 124740]` with no drops or blocker identities. The
candidate-script versus direct-automatic comparison also agrees on page,
scale, view set, drop and blocker identities, and lint. It differs in the
ownership representation of `dim_height`: the direct compiler gives a
bbox-derived height a synthetic measurement identity without adding a
recognized `EnvelopeFeature`, while the emitter intentionally declares an
envelope so the same authored height is addressable. The glyph still says
`10`. Do not add that synthetic envelope to the detected feature inventory
merely to make these manifests identical.

The 15/15 versus 7/15 figures are **different evidence authorities**, not
proof of eight candidate-lost annotations. Direct automatic rendering has
raw-recognition ownership (report schema 3). The generated Sheet is declared
intent (schema 8); its public `requirement_snapshot()` refuses with
`ReportUnavailableError` because exact raw occurrence ownership is unavailable.
Its eight `unverifiable` outcomes also occur under the scripted baseline.
Neither clean lint nor equal script inventory makes that drawing complete.

There is one separately actionable authored-claim failure common to both
scripts: the emitter requests hole member 0's Y and Z locations, and the
compiler approves exact IDs
`location_off_axis.location.member.0.y` and
`location_off_axis.location.member.0.z`, but no live annotation carries either.
PR #1883 changes only the independent safety check so it names those missing
representations instead of incorrectly saying a location has no static feature
parameter. It neither draws the locations nor turns the safety verdict green.

This case supports a narrow **relative semantic tie** for one plain STEP
generated-Sheet path. It does not establish a visual win, independent safety,
AP203 behavior, novel large/user CAD, supported-platform cost, or generated
script parity for other feature families. A stronger authored holdout should
compare declared model geometry, compiled intent, carriers and lint under the
existing [generated-script fidelity contract](https://github.com/pzfreo/draftwright/issues/964),
while marking raw occurrence coverage unavailable rather than copying it from
the direct drawing. The wider #1831 and #1813 gates remain open.
