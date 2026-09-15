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

The 24 repeated through-step report cases now use fresh drawings backed by one analysis seed per
build-option key. All 63 report-projection outcomes remain collected and passing; drawing builds
(57), analysis runs (74), compilation calls (194), format exports (two), lint calls (212), and raw
recognition (one) are exact. Recognition acquisitions fell from 55 to 31 (43.6%) and shape
constructions from 285,863 to 251,615 (12.0%). The recognition-count probe and unavailable-model
boundary cases retain direct builds so their construction boundaries remain observable.

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

The pocket-pattern mutation campaign now uses the exact three-case signature cover: one linear
pattern, one grid, and one confusable negative. A clean module-scoped evaluation prevents vacuous
kills before five damaged evaluations reuse that cover. The five probes fall from 35 to 18 drawing
and recognition runs (48.6%), 35 to 18 STEP imports, and 482,511 to 329,401 shape constructions
(31.7%). Across the complete unchanged 36-item module, drawing and recognition runs fall from 74 to
57 (23.0%), STEP imports from 44 to 27 (38.6%), and shapes from 1,088,678 to 935,551 (14.1%). All
36 outcomes pass, including the full seven-case topology corpus and both lattice mutation branches.

The Double-D provider-deletion probe now evaluates the shared two-case reduced corpus after proving
that subset clean. Its mutation work falls from 11 to four drawing and recognition runs (63.6%) and
from 43,484 to 19,692 shape constructions (54.7%). Across the unchanged 66-item module, drawing
builds fall from 52 to 45 (13.5%), recognition acquisitions from 55 to 48 (12.7%), STEP imports from
27 to 20 (25.9%), and shapes from 250,926 to 226,392 (9.8%). All malformed-schema, topology,
correspondence, declaration, drawing, and cached-solid integrity outcomes remain passing.

The plate provider-deletion and malformed-record probes now use the same clean reduced corpus as
the family's interval and identity mutations. Their combined drawing, recognition, and STEP-import
work falls from 22 runs to six (72.7%), and shapes from 86,629 to 21,603 (75.1%). Across all 53
unchanged plate outcomes, drawing builds fall from 92 to 74 (19.6%), recognition acquisitions from
91 to 73 (19.8%), STEP imports from 49 to 31 (36.7%), and shapes from 775,016 to 701,905 (9.4%).
The full eleven-case topology corpus and every selected provider mutation remain passing.

The polygonal-stock provider-deletion probe now uses its existing clean reduced corpus while the
varied-positive mutation deliberately retains all thirteen fixtures. The deletion slice falls from
13 to four drawing, recognition, and STEP-import runs (69.2%), and from 41,351 to 30,474 shapes
(26.3%). Under the four-worker pull-request distribution, all 66 module outcomes pass; aggregate
drawing builds fall from 91 to 82, recognition acquisitions from 104 to 95, and STEP imports from
53 to 44. The complete varied-value and topology-order oracles remain unreduced.

The polygonal-boss provider-deletion probe now uses the family's clean reduced corpus. Its fault
slice falls from eleven to four drawing, recognition, and STEP-import runs (63.6%), and from 83,603
to 26,484 shapes (68.3%). Across all 41 unchanged outcomes under pull-request distribution, drawing
builds fall from 70 to 63 (10.0%), recognition acquisitions from 69 to 62 (10.1%), STEP imports
from 38 to 31 (18.4%), and shapes from 426,993 to 370,554 (13.2%). Full topology, malformed-provider,
correspondence, generated-code, and drawing mutations remain passing.

The pocket provider-deletion probe now evaluates the existing clean two-case cover instead of all
ten corpus entries. Its clean-plus-damaged work falls from ten to four drawing, recognition, and
STEP-import runs (60.0%), and from 50,849 to 23,170 shape constructions (54.4%). Across all 24
unchanged outcomes under pull-request distribution, drawing builds fall from 50 to 42 (16.0%),
recognition acquisitions from 51 to 43 (15.7%), STEP imports from 30 to 22 (26.7%), and shapes from
473,013 to 433,377 (8.4%). The full corpus topology checks and every retained provider, generated-
code, declaration, drawing, and lint mutation remain passing.

The chamfer provider-deletion probe now uses the family's clean two-case cover. Its mutation work
falls from ten to four drawing, recognition, and STEP-import runs (60.0%), and from 29,710 to
12,114 shape constructions (59.2%). Across all 30 unchanged outcomes under pull-request
distribution, drawing builds and recognition acquisitions fall from 51 to 45 (11.8%), STEP imports
from 32 to 26 (18.8%), and shapes from 169,243 to 152,381 (10.0%). Full topology coverage and the
retained provider, parameter, declaration, drawing, and quality mutations remain passing.

The fillet provider-deletion probe now uses the family's clean two-case cover. Its mutation work
falls from ten to four drawing, recognition, and STEP-import runs (60.0%), and from 29,338 to
13,546 shape constructions (53.8%). Across all 28 unchanged outcomes under pull-request
distribution, drawing builds and recognition acquisitions fall from 47 to 39 (17.0%), STEP imports
from 27 to 19 (29.6%), and shapes from 151,713 to 129,554 (14.6%). Full topology coverage and the
retained provider, radius, declaration, drawing, and quality mutations remain passing.

The groove provider-deletion probe now uses the family's clean two-case cover. Its mutation work
falls from eight to four drawing, recognition, and STEP-import runs (50.0%), and from 44,552 to
15,494 shape constructions (65.2%). Across all 45 unchanged outcomes under pull-request
distribution, drawing builds and recognition acquisitions fall from 65 to 61 (6.2%), STEP imports
from 29 to 25 (13.8%), and shapes from 461,277 to 432,920 (6.1%). Full topology coverage and the
retained provider, parameter, identity, declaration, drawing, and quality mutations remain passing.

The countersink provider-deletion probe now uses the family's clean two-case cover. Its mutation
work falls from seven to four drawing, recognition, and STEP-import runs (42.9%), and from 27,180
to 19,349 shape constructions (28.8%). Across all 27 unchanged outcomes under pull-request
distribution, drawing builds and recognition acquisitions fall from 43 to 40 (7.0%), STEP imports
from 45 to 42 (6.7%), and shapes from 189,205 to 182,297 (3.7%). Full topology coverage and the
retained malformed-provider, declaration, drawing, and negative-control mutations remain passing.

The turned-step provider-deletion probe now uses the family's clean two-case cover. Its
clean-plus-damaged work falls from 22 to eight drawing and recognition runs (63.6%), from eleven to
four STEP imports (63.6%), and from 72,310 to 26,521 shape constructions (63.3%). Across all 110
unchanged outcomes under pull-request distribution, drawing builds fall from 210 to 196 (6.7%),
recognition acquisitions from 206 to 192 (6.8%), and shapes from 773,652 to 729,534 (5.7%). Full
topology coverage, cached-solid integrity, and all retained correspondence, provider, parameter,
declaration, generated-code, and drawing mutations remain passing.

The pad provider-deletion probe now uses the family's clean two-case cover. Its
clean-plus-damaged work falls from twelve to four drawing, recognition, and STEP-import runs
(66.7%), and from 59,376 to 37,872 shape constructions (36.2%). All 54 module outcomes pass under
pull-request distribution, including the full topology corpus and every retained correspondence,
provider, parameter, identity, declaration, generated-code, drawing, and quality mutation.

The flat provider-deletion probe now uses the family's clean two-case cover. Its mutation work
falls from seven to four drawing, recognition, and STEP-import runs (42.9%), and from 18,665 to
12,080 shape constructions (35.3%). Across all 15 unchanged outcomes under pull-request
distribution, drawing builds and recognition acquisitions fall from 36 to 33 (8.3%), STEP imports
from 21 to 18 (14.3%), and shapes from 105,903 to 100,048 (5.5%). Full topology coverage and all
retained provider, parameter, declaration, generated-code, and drawing mutations remain passing.

The candidate-construction failure matrix now uses sixteen explicit pairwise cases instead of the
64-case Cartesian product. Every one of the eight failure modes is paired with both values of
valid-tail, drop-callback, and fixed-budget behavior; a pure guard also proves every pair of values
across all four dimensions remains represented. The selected family falls from 64 integration
items to sixteen plus the pure guard (73.4%), drawing and analysis runs from 64 to sixteen (75.0%),
compilations from 128 to 32, lint calls from 192 to 48, and shapes from 196,660 to 52,465 (73.3%).
Summed phase time falls from 193.86 to 40.58 seconds (79.1%). The complete feature-leader module
falls from 145 to 98 items and retains 92 passes and six expected xfails under pull-request
distribution.

The complete plate text-style matrix now uses eight strength-two cases instead of the sixteen-case
Cartesian product. A pure guard proves every value and every pair across text position, text
orientation, projection convention, and scale remains represented. The selected family falls from
sixteen integration items to eight plus the pure guard (43.8%), drawing builds from 32 to sixteen,
compilations from 104 to 52, lint calls from 80 to 40, and shapes from 130,596 to 68,218 (47.8%).
Summed phase time falls from 136.98 to 51.08 seconds (62.7%). The complete dimension-text module
falls from 102 to 95 items and all 95 pass under pull-request distribution.

The challenging single-glyph rotation contract now places all seven independent glyph probes on
one drawing per axis and checks every resulting PDF text object, instead of exporting seven
one-glyph drawings per axis. Across the unchanged two test outcomes, PDF exports and drawing builds
fall from fourteen to two (85.7%), shapes from 93,549 to 17,505 (81.3%), and summed phase time from
43.77 to 8.24 seconds (81.2%). Across all 49 unchanged searchable-PDF outcomes, format exports fall
from 56 to 44 (21.4%), drawing builds from 55 to 43, and shapes from 396,487 to 322,235 (18.7%);
all 48 runnable contracts pass and the existing optional-tool contract remains skipped.

The six raw-helper semantic fallback cases now share one PDF while retaining a separately named
dimension for every constructor form. The assertion compares exact PDF text-object multiplicities,
so the two pairs of equal visible labels cannot pass by collapsing to one object. The selected
family falls from six exports, builds, and integration items to one (83.3%), shapes from 41,287 to
9,602 (76.7%), and summed phase time from 17.62 to 4.70 seconds (73.3%). Across the searchable-PDF
module after the preceding glyph consolidation, items fall from 49 to 44, format exports from 44 to
39, drawing builds from 43 to 38, and shapes from 322,235 to 289,571 (10.1%). All 43 runnable
contracts pass and the existing optional-tool contract remains skipped.

The two custom-font construction-draft rotations now share one PDF, with independently named
annotations retaining both the extracted-angle and first-character placement checks. The selected
family falls from two exports, builds, and integration items to one (50.0%), shapes from 13,431 to
7,328 (45.4%), and summed phase time from 7.11 to 3.46 seconds (51.3%). Across the searchable-PDF
module after the preceding consolidations, items fall from 42 to 41, format exports from 37 to 36,
drawing builds from 36 to 35, and shapes from 277,428 to 271,219 (2.2%). All 40 runnable contracts
pass and the existing optional-tool contract remains skipped.

The plain and basic custom-draft free-form dimensions now share one PDF with distinct labels, while
the contract still rejects a synthesized measurement label for either form. The selected family
falls from two exports, builds, and integration items to one (50.0%), shapes from 13,296 to 7,257
(45.4%), and summed phase time from 5.87 to 3.10 seconds (47.1%). Across the searchable-PDF module
after the preceding consolidations, items fall from 41 to 40, format exports from 36 to 35, drawing
builds from 35 to 34, and shapes from 271,219 to 265,840 (2.0%). All 39 runnable contracts pass and
the existing optional-tool contract remains skipped.

The three basic-dimension upright-orientation cases now share one PDF, using unique equal-length
labels so each extracted angle remains independently attributable without changing the text-shape
boundary under test. The selected family falls from three exports, builds, and integration items
to one (66.7%), shapes from 20,229 to 7,768 (61.6%), and summed phase time from 8.64 to 4.08 seconds
(52.8%). Across the searchable-PDF module after the preceding consolidations, items fall from 44 to
42, format exports from 39 to 37, drawing builds from 38 to 36, and shapes from 289,571 to 277,428
(4.2%). All 41 runnable contracts pass and the existing optional-tool contract remains skipped.

The ten pocket-lowering topology cases now replay their generated Sheet scripts while intercepting
the final export call and returning the constructed drawing directly. The same ten topology
outcomes remain independently checked, while format exports fall from ten to zero. Compilations
fall from 141 to 131 and lint calls from 133 to 123 because an empty-format export no longer
finalizes the drawing a second time. Collection remains at 64 items for the complete section-pocket
module, and all 64 pass under pull-request distribution. Both existing consumers of the shared
script-replay helper also pass unchanged.

The location-identity generated-script contracts now supply their in-memory part namespace to the
shared replay helper and inspect the constructed drawing without serializing it. Across all 49
unchanged module outcomes, format exports fall from eleven to zero, compilations from 133 to 122,
and lint calls from 95 to 84. This retains both replays of each deliberately edited pattern script,
as well as every member, axis, profile, order, and bolt-circle identity assertion.

The semantic-side generated scripts now use the same in-memory replay boundary while retaining
their execution namespaces for assertions about the rebuilt sheet and part. Across all fifteen
unchanged module outcomes under pull-request distribution, format exports fall from five to zero,
and compilations and lint calls each fall by five. The original, side-edited, and emitted-roundtrip
drawings retain their layout, authored-side, and measurement-equivalence checks.

The supported-placement roundtrip now inspects its replayed drawing and authored Sheet directly,
without running the empty-format exporter. Across all 29 unchanged dimension-option outcomes,
format exports fall from one to zero, and compilation and lint calls each fall by one. The real
build still proves that the advertised plan-side bore dimension reaches a rendered annotation.

The curved-pocket generated-script contracts now replay in memory before checking their declared
features and exact geometric constraints. Across all 52 unchanged module outcomes, format exports
fall from eleven to four; the four retained calls are the per-topology SVG contracts that inspect a
nonempty file. Requirement-outcome checks still run after the operation that populates their
registries: the retained SVG export for single pockets and an explicit lint pass for the pattern.

The nominal-mismatch generated-script contract now inspects its reconstructed authored dimension
without an empty-format export. Across all nine unchanged module outcomes, format exports fall from
one to zero, and compilation and lint calls each fall by one. The exact nominal value, source
identity, and lowering-blocker assertions remain unchanged.

The rounded-pocket generated-script contracts now replay in memory while retaining the inspected
SVG export for the ordinary roundtrip. Across all twenty unchanged module outcomes, format exports
fall from three to one. The SVG export populates the placed-outcome registry for its case; an
explicit critique does the same for the authored-radius case, preserving its unverifiable outcome
without serializing a file.

The through-indicator table-and-measurement matrix now runs its complete SVG/PDF/DXF contract for
the default wording and replays the three authored wording variants in memory. All four semantic
outcomes remain independent, while their format exports fall from four to one, compilations from 36
to 33, lint calls from twenty to seventeen, and shapes from 107,209 to 49,882 (53.5%). The complete
52-outcome module passes under pull-request distribution with nine exporter calls, down from twelve.

The four through-pattern wording cases now replay their generated declarations in memory because
they assert only reconstructed pattern semantics and measurement equivalence; the sibling matrix
owns this module's explicit format evidence. Their format exports fall from four to zero,
compilations from 32 to 28, lint calls from twenty to sixteen, and shapes from 46,291 to 28,572
(38.3%). All 52 module outcomes pass with five exporter calls, down from nine after the prior slice.

The four profiled-bore wording cases likewise replay in memory: their contract is reconstructed
profile, pattern, callout, and measurement meaning, while the sibling wording matrix owns file
formats. Their format exports fall from four to zero, compilations from 32 to 28, lint calls from
twenty to sixteen, and shapes from 54,698 to 35,674 (34.8%). The complete 52-outcome module now
passes with one exporter call, down from the original twelve, while retaining every wording value.

The eight raw basic-dimension span and rotation probes now occupy separate regions of one drawing
and use unique labels, so every extracted text object remains independently attributable. The
selected family falls from eight integration items, drawing builds, and PDF exports to one (87.5%),
and shapes from 57,337 to 12,905 (77.5%). The complete searchable-PDF module falls from forty to 33
items and from 35 to 28 exports; all 32 runnable outcomes pass and its optional-tool case is skipped.

The two opposite rotations of the digit `1` and the percent-glyph rotation now share one PDF. The
assertion groups exact extracted text objects by glyph and compares the complete angle multiset, so
neither repeated `1` can satisfy the other one's direction. The selected family falls from three
integration items, drawing builds, and exports to one (66.7%). The complete searchable-PDF module
falls from 33 to 31 items and from 28 to 26 exports; thirty outcomes pass and one remains skipped.

The `C` and percent-glyph unknown-font fallbacks now share one PDF and assert that neither glyph was
converted into a semantic text object. The selected family falls from two integration items,
drawing builds, and exports to one (50.0%). The complete searchable-PDF module falls from 31 to
thirty items and from 26 to 25 exports; 29 outcomes pass and one remains skipped.

The two raw single-`R` rotations now share one PDF. Their duplicate extracted objects are compared
as a complete orientation multiset modulo 180 degrees, preserving the original direction-neutral
text contract without relying on object order. The selected family falls from two integration
items, drawing builds, and exports to one (50.0%). The complete searchable-PDF module falls from
thirty to 29 items and from 25 to 24 exports; 28 outcomes pass and one remains skipped.

The two challenging default-font axes now share one PDF containing both rotations of every glyph.
Each glyph's two extracted objects must have the complete `[-30, 95]` angle multiset, so neither
axis nor any of the seven outline classes can disappear. The selected family falls from two
integration items, drawing builds, and exports to one (50.0%). The complete searchable-PDF module
falls from 29 to 28 items and from 24 to 23 exports; 27 outcomes pass and one remains skipped.

The two curved-`C` rotation paths now share one PDF and compare both exact extracted angles as a
multiset. This retains the base-angle-only case and the composed constructor-plus-live rotation
case without depending on PDF object order. The selected family falls from two integration items,
drawing builds, and exports to one (50.0%). The complete searchable-PDF module falls from 28 to 27
items and from 23 to 22 exports; 26 outcomes pass and one remains skipped.

The scale-policy warning and selection contracts now stop at `build_drawing`, the operation whose
result and warnings they inspect, instead of invoking `make_drawing` and ignoring its files. The
Sheet façade case still checks that its default PDF exists. Across all seven unchanged outcomes,
format exports fall from six to one (83.3%); the complete module passes under pull-request
distribution.

The turned-boss generated-script contracts now replay in memory because they inspect only rebuilt
annotations, diagnostics, views, labels, and lint. Across all 27 unchanged module outcomes, format
exports fall from five to zero. All three principal-axis cap-height cases, the synthetic overall
owner, and the partial X-chain layout comparison remain direct generated-script executions.

The shoulder-detail replay retains its SVG existence contract on the Z-axis representative and
runs the X- and Y-axis semantic replays in memory. All three axes still compare the editable detail
view, tolerance label, complete measurement-claim multiset, and lint result; the non-exporting cases
also prove no file appeared. Across all fifteen unchanged module outcomes, format exports fall from
three to one, and the complete module passes under pull-request distribution.

The two canonical-angle sectors retain exact-reference, small-tolerance, emitted-model, and rendered
angle checks. The minor-sector representative keeps the SVG existence contract; the opposite sector
replays in memory and proves it produced no file. Across all 58 unchanged module outcomes, format
exports fall from two to one, and the complete module passes under pull-request distribution.

The angle-pattern generated-script contract now replays in memory because it compares the complete
compiled member-reference and final-label sequence and never inspects a file. Across all seventeen
unchanged module outcomes, format exports fall from one to zero, and the complete module passes
under pull-request distribution.

The partial-chain overall-height generated script now replays in memory because its contract is the
rebuilt `62` label, measurement identity, and exact lint outcome rather than file serialization.
Across all nineteen unchanged module outcomes, format exports fall from one to zero, and the
complete module passes under pull-request distribution.

The high-precision oriented-slot generated script now replays in memory because its contract is the
exact reconstructed feature, including authored dimensions and directions. Across all one hundred
unchanged module outcomes, format exports fall from one to zero, and the complete module passes
under pull-request distribution.

The GRM04 boss/blend generated script now replays in memory because its contract is emitted R4
ownership, absence of a duplicate diameter, and complete feature coverage. Across all eight
unchanged module outcomes, format exports fall from one to zero, and the complete module passes
under pull-request distribution.
