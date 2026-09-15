# Test burden baseline

Measured before cost-reduction work on 15 September 2026, after #1637. Timing is from the
canonical Python 3.13 environment in this workspace unless stated otherwise.

| Measure | Baseline |
| --- | ---: |
| Collected items | 8,809 |
| Unit-tier items | 412 |
| `pytest -m unit` wall time | 80.75 s |
| Direct-path `scripts/unit-tests`, before scan reuse | 39.11 s (416 passed) |
| Direct-path `scripts/unit-tests`, with scan reuse | 27.58 s (416 passed) |
| Issue-named modules | 151 |
| Cross-module clone budget and measured count | 0 |
| Former #1372 focused/capability run | 309 passed in 1,603.59 s |

Stage 2 baseline (one complete four-worker fast-tier run at `a0a53df`): 8,751 selected
tests, 4,977 drawing builds, 5,475 recognition-evidence acquisitions, 19,257 compilation
calls, 233 format exports, and 36,650,352 build123d shape constructions. The summed pytest
phase duration was 24,055.62 seconds; wall time was 6,075.50 seconds. Operation counts are
the decision metric for cache slices, so they do not depend on runner timing variance.

Operation counts and cache-hit rates require the new `--burden-report` instrumentation.
Their complete-run baseline above is an exact event census. Performance comparisons still
require the plan's median of three runs; the single wall time is context rather than an
approval metric. Store the volatile JSON reports as CI artifacts rather than committing them.

The first instrumentation probe, retained only as review evidence rather than a committed
timing result, built the shared `box_60x40x20` recipe once and recorded 2,945 `Shape`
constructions, one drawing build, one analysis run, one compilation run, and two lint calls.
The count is intentionally low-level: it measures every wrapper construction performed by
build123d, not just user-authored primitives.

The direct unit-module manifest initially reduced the inner loop by 51.6% while selecting
exactly the same 416 node IDs as `pytest -m unit`. Its remaining time was concentrated in
repeated repository-wide policy scans. Reusing each
immutable scan within its test module reduced the loop again to 27.58 seconds, meeting the
stage target without changing the selected tests or assertions.

The first stage 2 slice memoizes the immutable mirror-corpus kind census. Across its three
consumers, recognition and analysis acquisitions fell from 96 to 64 (33.3%); the third guard
now reads the exact census already proved by its sibling instead of rebuilding all 32 parts.

The fresh-drawing cache-boundary probe constructs two independent drawings and records two
builder/analysis executions but only one recognition acquisition. Mutating the first drawing's
sheet membership and `PartModel` containers leaves the second drawing complete, proving the
boundary reuses geometry evidence without sharing those mutable results.

Migrating `test_drawing_state.py` to that boundary preserved its 14 fresh drawing builds and
all 17 behavior tests while recognition acquisitions fell from 14 to 2 (85.7%) and shape
constructions from 37,597 to 25,197 (33.0%). The remaining two acquisitions belong to the
independent read-only drawing cache and the fresh-drawing seed.

Migrating `test_repair.py` preserved 13 builds and all seven repair behaviors while
recognition acquisitions fell from 13 to 7 (46.2%) and shape constructions from 55,300 to
49,277 (10.9%). The seven acquisitions are three option-keyed box seeds and four builds of
the two distinct complex substrates.

Migrating `test_place_dimension.py` preserved its six builds and all seven behaviors while
recognition acquisitions fell from six to five (16.7%). Only the two tests with identical
geometry and `scale=2.0` share an analysis seed; every other option/geometry remains distinct.

Migrating the read-only repetitions in `test_sheet_furniture.py` to `shared_drawing` reduced
its PR-distributed drawing and recognition executions from 13 to 9 (30.8%) and shape
constructions from 36,426 to 25,725 (29.4%). The four avoided builds are same-class cache
hits under xdist `loadscope`; unique scale/page combinations remain independent.

Migrating the four mutation-heavy `box_60x40x30` cases in `test_lint_summary.py` to
`fresh_drawing` preserved all ten drawing builds and 13 behaviors while recognition
acquisitions fell from ten to seven (30.0%) in the PR-distributed run. Each test still owns
its mutable drawing and registry; only the immutable analysis seed is reused within the
loadscope worker.

Sharing the read-only `box_40x30x8` substrate behind the ISO NTS stand-in harness preserved
all 20 caption-placement behaviors while the PR-distributed cohort fell from 29 to 10 drawing
builds (65.5%), 28 to 9 recognition acquisitions (67.9%), and 75,947 to 27,633 shape
constructions (63.6%). Four loadscope workers account for four cache misses; the other 19
harness requests are measured cache hits. The stand-ins own their registries and item lists,
and borrow the real drawing only for immutable draft, page, and furniture evidence.

Within the #1166 cross-pass leader regression family, moving five mutation-heavy fixed-ink
cases to `fresh_drawing` preserved five independent drawings while recognition acquisitions
fell from five to one (80.0%) and shape constructions from 28,642 to 24,454 (14.6%). The
complete family remains at 139 passes and six expected strict xfails, so the retained
historical and bounded-failure signals are unchanged.

The four parametrized #1166 final-preflight failure cases now reuse one immutable analysis
seed while retaining four private drawings. Recognition acquisitions fell from four to one
(75.0%) and shape constructions from 11,777 to 8,636 (26.7%). Their metadata and rendered-face
faults are still installed after construction, so all four fail-closed behaviors remain direct.

The 64-case #1166 candidate-measurement failure matrix now prepares each private drawing from
one immutable analysis seed. All 64 boundary combinations remain collected and passing;
recognition acquisitions fell from 64 to one (98.4%) and shape constructions from 259,321 to
193,360 (25.4%), while drawing, compilation, and lint counts stayed identical. The optional
fixed-work budget is installed after drawing construction, matching its downstream subject and
keeping monkeypatch state outside the cached boundary.

The ten #1166 rendered-survivor validation cases now reuse immutable analysis across their
independent drawings and traces. Recognition acquisitions fell from ten to one (90.0%) and
shape constructions from 59,514 to 50,091 (15.8%), with all analytical-ink, producer-tail,
and mesh-failure outcomes retained.

Five #1166 hard-boundary cases now reuse one immutable analysis seed while keeping private
placement contexts and drawings. Recognition acquisitions fell from five to one (80.0%) and
shape constructions from 18,215 to 14,027 (23.0%). The resource cap is installed after
construction, and page, silhouette, future-section, and rendered-title behavior remains
independently asserted.
