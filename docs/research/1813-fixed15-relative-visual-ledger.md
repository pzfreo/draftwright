# #1813 fixed-sheet relative visual ledger

This is a page-by-page review of the **12 candidate quality-key verdicts** in the
[saved v4 fixed-15 report](1813-bounded-chooser-trial-v4.json), not a new CAD run.
The saved pages use the caller's fixed sheet and scale. Three later *single-case*
repairs (#1845 issue915/lever and #1846 whistle) replace the visibly shrunken
v4 candidate page in those rows. The mixed source heads therefore **do not
constitute a current-head 15-case corpus result**.

## Fair review rule

A relative visual win requires a discernible improvement in the relevant view,
dimension, or leader at the **same page and scale**, with no comparable new
legibility regression. Semantic ownership parity and no introduced required
blocker identities are prerequisites. A pre-existing blocker does not cancel a
relative gain; neither does a relative gain make the candidate independently
safe. Isometric area is a prompt to inspect the printed-size view, **not** an
automatic cutoff: a smaller but readable orientation view can be acceptable,
while a thumbnail is not. Where the pages do not support a clear judgment, the
case stays *unverified* rather than being called a loss or a win.

The saved v4 report gives 12 metric wins, 3 ties, 15/15 semantic parity, and
zero introduced required-blocker identities. Under the rule above, **nine
cases have case-level relative visual-win evidence**; three CTC cases remain
unverified. This is a review ledger, **not** a passed 12/15 rollout gate: it
combines historical and targeted source heads, and the three unresolved cases
still need layout/visual resolution. The independent candidate safety, wider
real/user corpus, supported-platform cost, fallback, and exact-head full/slow
gates also remain open.

| Fixed-sheet case | Reviewed baseline / candidate page | Relative visual finding |
| --- | --- | --- |
| CTC01 A3 1:5 | [baseline](../images/ctc01-a3-1to5-baseline.png) / [v4 candidate](../images/ctc01-a3-1to5-candidate-preview.png) | **Win, with tradeoff.** The crowded plan/front annotation routes separate: required drops 3→2, interior dimensions 2→0, crossings 2→0. The iso is 0.564× baseline area but remains identifiable at page scale. Not independently safe. |
| CTC02 A2 1:5 | [baseline](../images/1813-v4-ctc02-baseline.png) / [v4 candidate](../images/1813-v4-ctc02-candidate-preview.png) | **Unverified.** Crossings 4→0 and the iso remains substantial despite 0.808× area, but the long upper-plan leader and crowded GD&T remain; 43 required blocker identities persist. This is a real relative metric gain, not yet an unqualified page-level visual gain. |
| CTC03 A2 1:5 | [baseline](../images/ctc03-a2-1to5-baseline.png) / [v4 candidate](../images/ctc03-a2-1to5-candidate-preview.png) | **Relative win, with residual crowding.** At the same page and scale, the lower GD&T/dimension ladder is visibly spread across the front and side margins rather than concentrated under the front view; crossings fall 5→0 and overlaps 31→29. The long imported tolerance strings remain hard to read in both pages, so this does not pass independent safety. Iso area and requirements are unchanged. A separate columns trial is weaker and not substituted here (#1847). |
| CTC04 A2 1:5 | [baseline](../images/1813-v4-ctc04-baseline.png) / [v4 candidate](../images/1813-v4-ctc04-candidate-preview.png) | **Unverified.** One hard violation and one crossing disappear (12→11, 1→0), but at full-page size the pages are nearly indistinguishable and the dense top ladder remains. The gain is not denied; its visual significance is not yet proven. |
| CTC05 A2 1:5 | [baseline](../images/1813-v4-ctc05-baseline.png) / [v4 candidate](../images/1813-v4-ctc05-candidate-preview.png) | **Unverified.** Crossings 5→1, but a callout moves above the crowded side view with a long leader through it. It is not clear that this route is more readable overall; existing required blockers remain. |
| GRM04 A3 5:1 | [baseline](../images/grm04-a3-5to1-baseline.png) / [v4 candidate](../images/grm04-a3-5to1-candidate-preview.png) | **Win.** Same clear dimensions and ownership, with a visibly larger and still clear iso (1.331× area). |
| Tuner A3 1:1 | [baseline](../images/tuner-a3-1to1-baseline.png) / [v4 candidate](../images/tuner-a3-1to1-candidate-preview.png) | **Win.** The iso grows 1.331× without crowding the orthographic dimensions or losing the pocket-pattern location. Both sheets retain an independently reported `step_dim_withheld`; that does not erase the relative gain or pass safety. |
| Whistle A3 1:1 | [baseline](../images/1813-v4-whistle-baseline.png) / [#1846 planned candidate](../images/1813-whistle-planned-preview.png) | **Win after targeted fix.** The v4 legacy-depth iso was a thumbnail (0.465×); the changed-source pre-render `planned` choice restores it to 1.120× baseline while retaining the hard-violation/crossing gain and semantic parity. The central ladders remain dense. [Evidence](1813-whistle-medium-demand-choice.md). |
| Issue915 A2 1:2 | [baseline](../images/1813-v4-issue915-baseline.png) / [#1845 candidate](../images/1813-issue915-detail-safe-iso-growth.png) | **Win after targeted fix.** The v4 0.4225× iso was too small. The changed-source candidate returns it to baseline area, removes the scored crossing, and keeps DETAIL A, its five step dimensions, marker and caption. Existing section/requirement failures remain. [Evidence](1813-issue915-detail-safe-iso-growth.md). |
| Lever A3 5:1 | [baseline](../images/lever-a3-5to1-baseline.png) / [#1845 candidate](../images/1813-lever-detail-safe-iso-growth.png) | **Win after targeted fix.** The iso returns from 0.4225× to baseline area and the crossing gain remains with DETAIL A retained. A pre-existing ink overlap at the detail caption remains an absolute-safety failure. |
| Frame A3 2:1 | [baseline](../images/frame-a3-2to1-baseline.png) / [v4 candidate](../images/frame-a3-2to1-candidate-preview.png) | **Win, with tradeoff.** Orthographic annotations are better separated and a section is visible; required drops 3→1, hard violations/overlaps 1→0, crossings 2→1. The iso is smaller (0.690×) but its features remain distinguishable. |
| Sloped A3 2:1 | [baseline](../images/sloped-a3-2to1-baseline.png) / [v4 candidate](../images/sloped-a3-2to1-candidate-preview.png) | **Win, with tradeoff.** The side-view dimension moves clear of the interior (1→0) while the angular/length labels remain readable. The iso shrinks to 0.679× area but still shows the stepped slope distinctly. A dropped step-position obligation remains an independent failure. |

The other three fixed-sheet cases—GRM03 PMI, wheel, and angled—are metric
ties in v4. They are **not counted** as visual wins here; this review makes no
claim that a tie is an improvement or a regression.

## Provenance and remaining test

The v4 page pairs are the saved renderings from source
`4ab713e52834b5852416bbbdad428c62a9b28605`; the compact report records
their exact fixed page, scale, quality keys and parity. #1845's changed-source
candidate was `9eb9cbb5f10650c6e5c43198ac901309d8ae29dc`; the lever SVG
reviewed here has SHA-256
`7f1676f4a5c9bf796854c430ddbbbe3ed9c364a80c07de3a45eb5df359072b59`.
#1846's whistle candidate provenance is in its linked evidence note. These
case-level repairs were checked against the saved baseline, **not** by rerunning
the baseline or all 15 cases. Before #1813's 12/15 visual gate can be claimed,
the three unverified CTC pages need a clear visual decision or layout repair and
the actual candidate-first path must be checked together at one final source
head, with the independent admission and rollout gates evaluated separately.
