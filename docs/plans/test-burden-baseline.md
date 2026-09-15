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
