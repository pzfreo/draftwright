# Test burden baseline

Measured before cost-reduction work on 15 September 2026, after #1637. Timing is from the
canonical Python 3.13 environment in this workspace unless stated otherwise.

| Measure | Baseline |
| --- | ---: |
| Collected items | 8,809 |
| Unit-tier items | 412 |
| `pytest -m unit` wall time | 80.75 s |
| Direct-path `scripts/unit-tests` wall time | 39.11 s (416 passed) |
| Issue-named modules | 151 |
| Cross-module clone budget and measured count | 0 |
| Former #1372 focused/capability run | 309 passed in 1,603.59 s |

Operation counts and cache-hit rates require the new `--burden-report` instrumentation.
Record their canonical baseline as the median of three complete pull-request-tier runs before
the first caching or corpus reduction. Store the volatile JSON reports as CI artifacts; add
the resulting aggregate figures to this table.

The first instrumentation probe, retained only as review evidence rather than a committed
timing result, built the shared `box_60x40x20` recipe once and recorded 2,945 `Shape`
constructions, one drawing build, one analysis run, one compilation run, and two lint calls.
The count is intentionally low-level: it measures every wrapper construction performed by
build123d, not just user-authored primitives.

The direct unit-module manifest reduced the inner loop by 51.6% while selecting exactly the
same 416 node IDs as `pytest -m unit`. It does not yet meet the 30-second stage target; the
remaining time is concentrated in repeated repository-wide policy scans.
