# Candidate-first demand-aware chooser trial (#1813)

This **local, unaccepted** chooser trial improved the first candidate-first fixed-sheet
run from 6 to 8 strict offline wins and from 9 to 11 semantic-parity cases, but it
still fails the 12/15 win, zero-loss, and independent-safety gates. Only one of 15
candidate drawings passed all provisional safety checks; no candidate was
production-selected. Its compact case-level record is
[`1813-candidate-first-evidence-v2.json`](1813-candidate-first-evidence-v2.json).

The source was `117458b35a8678df2bb3d862fb2237b5cbe3a589` on the
`issue-1813-profile-demand-chooser` evidence branch, with the v2 fixed-sheet
manifest on a Linux x86_64 host (4 cores, 7,941 MiB RAM), two corpus workers. Each
candidate was built by the actual `candidate-preview` path; a separate baseline
worker supplied **offline** semantic and quality comparison. The existing paired
selector was not rerun. The source trial itself is not in the PR stack and must not
be construed as a proposed default.

Columns selected from dense, partly unplanned pre-render demand restored parity
and produced strict gains on CTC02, CTC04, and CTC05. The planned rule regressed
CTC03 (seven missing annotations, including feature leaders) and issue915 (five,
including detail and hole furniture); tuner and grm03-PMI each still lose one.
Rendered SVG comparisons for CTC02/04/05 showed CTC02 remains visibly crowded
around GD&T and callouts, despite the relative gain. These observations are not
a substitute for the required broader PDF and real-part review.

On this one Linux host, candidate process median/p95 was 42.5/300.5 seconds,
against baseline 35.8/285.1 seconds; candidate peak RSS median/p95 was
537.5/884.5 MiB. These are samples, not a cross-platform budget; the preview
performed no automatic fallback. The next chooser revision must preserve the
three dense-case gains without introducing CTC03/issue915 losses and must close
the remaining parity/safety gaps before any default or fallback decision.
