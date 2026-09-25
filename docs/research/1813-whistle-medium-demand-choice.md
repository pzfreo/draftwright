# #1813 whistle: preserve the quality gain without a thumbnail iso

The [v4 fixed-sheet trial](1813-bounded-chooser-trial-v4.json) chose
`legacy-depth` for whistle A3 at 1:1. Its quality key improved from
`[1,0,1,0,2,-1,124740]` to `[0,0,0,0,1,-1,124740]`, but the iso area fell
to **0.4647×** baseline. At 72 dpi the orientation view became a thumbnail.

| Saved v4 baseline | Saved v4 legacy-depth preview | This PR's planned preview |
| --- | --- | --- |
| [PDF-rendered PNG](../images/1813-v4-whistle-baseline.png) | [PDF-rendered PNG](../images/1813-v4-whistle-legacy-preview.png) | [PDF-rendered PNG](../images/1813-whistle-planned-preview.png) |

The pre-render report has 38 typed demands, 7 unplanned requests, and 5
under-reserved corridors. The old absolute cap of five unplanned requests sent
this **medium-demand** case to `legacy-depth`. The revised version-4 chooser
allows a one-fifth unplanned tail below the existing 40-demand dense-case
boundary. Larger cases keep the absolute cap. Among the saved fixed 15, only
whistle changes profile: frame (38/5) was already `planned`; CTC01 (62/11) and
CTC03 (59/12) remain `legacy-depth`. A separate changed-source `planned` probe
for CTC01 retained parity and improved its metric key but still shrank iso area
to 0.555× baseline, so extending the ratio rule to large cases is not justified.

One actual changed-source `candidate-preview` build chose `planned` **before
rendering** on the same caller page/scale. Its SVG was byte-identical to the
direct planned-profile probe. It retained the winning key
`[0,0,0,0,1,-1,124740]`, achieved iso area **1.120×** the saved baseline,
passed saved-baseline semantic parity, and introduced no required blocker
identities. No baseline or full corpus was rebuilt for this slice.

Reviewed SVG SHA-256: v4 baseline
`2e2af5f54943800ac117ad16b9028e32f92766787016f980a57d929fd94b3ede`,
v4 legacy preview
`df9724830d97c379afdb09491e0ebf9d6eae8f525ceb77e8f749aea7bab8cfe5`,
this PR's candidate-first planned preview
`063697493937b2d5ab590fdcf6868115544e9d481f07cf0f66343c9d476d002f`.

This is a relative visual improvement, **not** production safety admission.
The central dimension ladders remain dense, and required-outcome/lint failures
still need resolution. The fixed15 visual-win, broader real-part, platform-cost,
fallback, and default-switch gates remain open.
