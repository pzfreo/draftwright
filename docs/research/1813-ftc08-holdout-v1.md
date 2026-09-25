# #1813 independent public STEP holdout: NIST FTC-08 AP242

This is one **public real-part** comparison outside the fixed15 corpus. NIST's
[download page](https://www.nist.gov/ctl/smart-connected-systems-division/smart-connected-manufacturing-systems-group/mbe-pmi-0)
permits unrestricted use of these test files and requests acknowledgement;
credit goes to the NIST MBE PMI Validation and Conformance Testing Project. It
does not substitute for a user-supplied large CAD model. No NIST logo is used.

The source was the [NIST STEP archive](https://www.nist.gov/document/nist-pmi-step-files),
member `NIST-PMI-STEP-Files/nist_ftc_08_asme1_ap242-e2.stp` (4,722,264 bytes).
Archive/member SHA-256 values, exact caller constraints, source commit, and the
full report hash are in the [compact case record](1813-ftc08-holdout-v1.json).
The manifest hash refers to the local one-case manifest with an absolute source
path; the compact record uses the stable archive member instead of that
machine-specific path.
Import-only preflight gave a roughly 311.607 × 222.707 × 48.260-mm compound;
the STEP text has one `MANIFOLD_SOLID_BREP` record. The one-case manifest fixed
A2 at 1:1, staggered side, exterior dimensions, and SVG export. The actual
candidate-first comparison used one worker at a time and a 600-second bound
per isolated baseline/candidate worker. Both workers completed once. An
earlier invocation failed before CAD import because the worktree selected a
system Python without `build123d`; the corrected invocation used the installed
venv. No successful CAD worker was rerun.

## Result and visual review

The candidate chose `columns` before rendering. Offline baseline/candidate
comparison exited zero and found a **tie**, not a win: identical quality keys,
the same A2/1:1 page and view bounds, semantic parity, no missing annotations,
no introduced blocker identities, and no new interior dimensions. The two SVG
files are byte-identical (SHA-256 in the case record). The baseline page was
rendered and inspected; because the candidate SVG is identical, it has the same
visible sheet. The upper plan and iso are readable as views, but a lower-right
orthographic view and some annotation ink run off the physical page. The side
view's x-maximum is **659.65 mm** on a **594-mm**-wide sheet, about **65.65 mm**
past its edge. No larger page or relaxed scale was used to hide this defect.

Independent candidate safety failed `required_outcomes`,
`recognized_occurrences`, `lint_blockers`, `audited_coverage`,
`view_page_containment`, and `annotation_page_containment`. The reported 131
requirements include 100 placed, 24 dropped, 2 unsupported, and 5
unverifiable. NIST's source drawing depicts GD&T and the STEP file contains
datum/tolerance entities, but the delivered sheet does not visibly establish
that source-PMI coverage. This is an unresolved recognition/placement question,
not a claim that every source entity was dropped or that clean lint proves
completeness. Neither mode is an absolutely safe sheet at these caller
constraints; candidate parity does not cure the shared failure.

On the four-core Linux host, baseline/candidate process times were
240.41/211.89 s and peak RSS was 657.47/655.16 MiB. These are **single
samples** from one platform, not meaningful median/p95 or fallback-frequency
evidence. `candidate-preview` performed no automatic fallback and
`admission_ready=false`; production selected no candidate. This case adds a
distinct public real-part tie and an explicit page-fit/PMI limitation. It does
not close the broader #1831 holdout, cost, visual-win, or rollout gates.
