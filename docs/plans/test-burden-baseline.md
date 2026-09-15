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

The paired #1166 provisional and committed-section cases retain two private drawings and both
classification outcomes while sharing one immutable analysis seed. Recognition acquisitions
fell from two to one (50.0%); shape constructions fell from 12,088 to 11,041 (8.7%), so this
slice qualifies by removing a measured duplicate rather than by the 10% cost threshold.

The 26 direct positive-pad builds in the #1372 completeness-evidence module now obtain fresh
drawings from three principal-axis analysis seeds. All 54 outcomes remain collected and passing;
drawing builds (83), analysis runs (120), compilation calls (457), lint calls (280), raw
recognition (one), and STEP imports (37) are exact. Recognition acquisitions fell from 83 to 60
(27.7%) and shape constructions from 571,761 to 527,923 (7.7%). Observer and injected-failure
paths still build directly, and negative-axis substrates remain separate.

The 33 repeated single-slot cases in the #1432 oriented-slot semantics module now build private
drawings from one immutable analysis seed. All 100 outcomes remain collected and passing;
drawing builds (70), analysis runs (70), compilation calls (87), format exports (one), lint calls
(149), and raw recognition (one) are exact. Recognition acquisitions fell from 114 to 81 (28.9%)
and shape constructions from 561,020 to 495,086 (11.8%). Rigid-motion, pattern, authored-model,
and provider fault paths continue to construct their distinct substrates directly.

A complete post-migration run of the #1166 family confirms the slices compose under one
session cache: all 145 outcomes remain 139 passes and six expected strict xfails. Drawing
builds (104), analysis runs (113), compilation calls (194), and lint calls (294) are unchanged;
recognition acquisitions fell from 107 to 18 (83.2%) and shape constructions from 499,281 to
406,098 (18.7%). This whole-family census supersedes summing the isolated slice counts.

Reusing parsed syntax trees across the import-policy views and traversing each consumer tree
once reduced the policy module's three-run post-change median to 19.15 seconds (18.66, 19.15,
20.18). The comparable pre-change run was 22.90 seconds; the structural work reduction is
exact: a consumer source tree is walked once rather than four times. All 19 policy and
self-probe tests retain their original assertions.

Collapsing the provider-contract guard from four AST passes to two and rejecting source files
without a literal provider reference before parsing reduced the import-policy module's
three-run median from 19.15 to 15.55 seconds (14.47, 15.55, 15.61), an 18.8% improvement.
All 19 boundary guards and their synthetic violation probes remain unchanged and passing.
The complete unit tier is still above target at 36.23-38.49 seconds with three loadscope workers,
so this slice does not close the unit-loop gate by itself.

Filtering the private-annotation import ratchet before parsing and sharing one AST walk across
its direct and aliased forms reduced its three-run median from 11.33 to 6.32 seconds (6.10,
6.32, 6.41), a 44.2% improvement. A new synthetic probe proves both detectable forms survive
the filter. The full unit tier remains above target at 37.05 seconds with three loadscope workers.

Streaming each clone candidate directly into its normalized structural fingerprint replaces the
old copied-AST transform, second node walk, and full `ast.dump`. The standalone guard's three-run
median fell from 15.22 seconds (15.81, 15.22, 14.70) to 10.94 seconds (10.75, 11.67, 10.94), a
28.1% improvement. An exhaustive review comparison over all 4,544 candidates found identical
normalized node counts and the same 4,417 equivalence classes. The complete unit tier improved to
32.92 seconds with three loadscope workers; it remains above the 30-second stage target.

Stopping clone discovery at each collected test definition avoids traversing every test body once
before fingerprinting it. The guard's three-run median fell again from 10.94 seconds to 9.54 seconds
(8.82, 9.54, 10.43), a 12.8% improvement. Comparing the old full-tree discovery with the pruned
walk found the same 4,542 threshold-qualified candidates and identical equivalence classes. The
complete unit tier measured 31.81 seconds with three loadscope workers, so further work remains.

Memoizing the immutable deprecation census removes the second repository-wide parse performed by
its sibling guard. The module's three-run median fell from 10.77 seconds (10.77, 10.52, 10.91) to
8.25 seconds (8.25, 8.38, 7.36), a 23.4% improvement, with all three assertions unchanged. The
complete unit tier reached 30.31 seconds with three loadscope workers, just above the stage target.

Rejecting source files without the literal module name before the reporting-contract AST walk
reduced its scan set from 110 files to 20. The import-boundary module's three-run median fell from
15.55 seconds to 13.92 seconds (14.94, 13.92, 13.91), a 10.5% improvement. All 19 guards and the
five synthetic reporting-import spellings remain passing. The complete unit tier then completed in
29.30 seconds with three loadscope workers, crossing the stage wall-time target inside pytest.

The canonical unit runner now keeps each module in one worker so module-scoped syntax and policy
caches remain effective, and uses four workers after the serial scan reductions changed the optimal
balance. Against `auto/worksteal`, its three-run median fell from 38.63 seconds inside pytest
(29.32, 38.63, 40.20; 39.43 seconds process wall time) to 27.72 seconds (27.72, 26.36, 33.57;
28.38 seconds process wall time), a 28.3% pytest-time reduction. All three runs selected and passed
the same 424 unit items without constructing CAD shapes. This closes the stage 1 exit gate.

Caching the 32 immutable mirror-corpus solids and their detected models within each worker removed
the second recognition pass paid by `TestTheDimensionMirror`. Across its unchanged 75 items,
recognition acquisitions fell from 73 to 39 (46.6%), analysis runs from 163 to 129 (20.9%), and
shape constructions from 453,169 to 365,571 (19.3%); compilation (262), drawing builds (66), and
lint calls (177) are unchanged. Summed phase duration fell from 278.35 to 212.30 seconds (23.7%).
A full 502-item emitter-module run retained 501 passes and one expected xfail, while module- and
class-scoped teardown fingerprints proved no consumer mutated cached solids or recognition models.

The adjacent 32-part declared-fidelity corpus now likewise constructs each solid and detection
model once per loadscope worker. Across the same 143 outcomes (142 passes and one expected xfail),
recognition acquisitions fell from 231 to 102 (55.8%), analysis runs from 252 to 123 (51.2%), and
shape constructions from 891,411 to 500,233 (43.9%). Compilation (582), drawing builds (64), and
lint calls (239) remain exact. Summed phase duration fell from 393.29 to 198.89 seconds (49.4%),
and the class-scoped solid/model fingerprints remained unchanged after every consumer.

Caching the seven immutable STEP solids used directly by the turned-step completeness module
reduced STEP imports from 136 to 36 (73.5%) while retaining all 110 outcomes, including its
fault-injection and downstream-observer mutations. Recognition (206), analysis (404), compilation
(931), drawing builds (210), and lint calls (711) remain exact. A module-scoped teardown comparison
of volume, bounds, solids, faces, and edges proved no consumer mutated a cached imported shape.

Canonicalizing and caching the five STEP solids directly reused by the Double-D completeness module
reduced STEP imports from 66 to 27 (59.1%). All 66 outcomes remain passing, including malformed
provider records, correspondence mutations, negative controls, and the real corpus evaluator;
recognition (55), analysis (52), compilation (266), drawing builds (52), lint (106), and raw
recognition (20) counts are exact. Its teardown both fingerprints every cached topology and rejects
an unlisted cache key; that check caught and prompted removal of an omitted-default duplicate during
review.
