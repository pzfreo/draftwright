# Recognition corrections: implementation and review

Issue #76 item 2, branch `cx/steer-recognition-corrections`. The Spec vocabulary and
geometry/identity rules are in [model.md](model.md#recognition-corrections-76-item-2).
The Python declaration implementation was read as a reference; it was not changed.

## Authored intent

The feature entry accepts one of `reclassify`, `suppress`, `split`, or `pattern`.
For example, `features/f:boss:f25/reclassify = "turned_step"`,
`features/f:hole:9/suppress = true`, and `features/f:hole_pattern:f6/split = true`.
An authored group is:

```json
{"features":{"f:hole_pattern:pair":{"pattern":{"arrangement":"linear","members":["f:hole:1","f:hole:2"]}}}}
```

`arrangement` also accepts `grid` and `bolt_circle`. A missed feature is:

```json
{"feature_declarations":[{"id":"f:declared:pilot","kind":"hole","faces":["face:sha256:<64 hex digits>"]}]}
```

Replace the placeholder with an actual face fingerprint, available through
`recognition_corrections::face_fingerprint` or the bounded `item_options` query
`face:<index>`. The index is a discovery address; only the fingerprint is saved.
The generated `renders/*-after.spec.json` files below are complete, bound examples.
Session preview/apply/history and stateless/MCP operations share the common editor;
declarations support `add` and whole-list `set`, and feature fields support `set`/`remove`.

Chosen boundaries and validation:

- Supported kinds are `hole`, `boss`, `turned_step`, `slot`, and `pocket` (the existing
  `section_recess` IR). Complete principal cylindrical and rectangular geometry must
  prove the interpretation. No authored size or page-coordinate constructor is added.
  Closed voids, omitted caps, partial/ambiguous geometry and unproved mouths are refused.
- Linear/grid spacing and complete lattice membership must be proved. Bolt circles
  require equally spaced coplanar holes and retain the ordinary small-circle
  corroboration rule. Members must have identical machining requirements and may not
  overlap another authored group. Authored group names are nonnumeric to avoid
  collisions with future automatic ranks.
- Declarations have unique `f:declared:<name>` IDs. Equivalent automatic ownership
  yields to that ID, even after a recognizer learns the feature; references and
  conflicting authored ownership must be resolved explicitly. No occurrence is counted twice.
- A unique named boss end selects its outward direction. Without one unique end,
  the positive principal direction is used; name the free end for a negative-facing boss.
- Saved feature anchors rebind feature keys, group members, fits, dimension edits and
  precision keys across rank changes. Missing/ambiguous faces and many-to-one collisions
  fail with the saved path and instructions to reattach or remove the correction.
  Geometric changes outside the exact fingerprint tolerance require reattachment.
- Suppression cannot carry conflicting manufacturing requirements. It removes the
  spurious inventory entry, while unrelated PMI and independent profile checks remain.

Corrections resolve before ordinary planning, including view demand, dimensions,
callouts, schedules and completeness. `Plan.recognition_corrections` and affected
dimensions record `from_spec`; lint judges the effective Analysis and actual rendered
evidence. Direct lint callers use `spec_gdt::effective`, as for authored GD&T.
Without corrections the resolver borrows the original Analysis without face hashing,
face lookup or geometry scans. R4 remains intact: analysis never manufactures Spec
declarations; these declarations are authored intent over the one analysed part.

## Checks before merging #77

All checks used the final source, jobs/threads set to four, and the required corpus.
Logs and reports are under `target/recognition-corrections/` (ignored build artifacts).

| Check | Result |
| --- | --- |
| `cargo fmt --all --check` | Passed. |
| `cargo clippy --workspace --all-targets --release -- -D warnings` | Passed. |
| Corpus-required release tests | 1,131 passed, two wall-clock failures, zero ignored. All 109 integration targets ran. The original full command stopped at the first latency failure; all remaining targets ran with `--no-fail-fast`. Doctests completed (zero tests). |
| New recognition-correction regressions | All 19 passed: defaults, each action, geometry/refusal rules, saved round-trip/re-analysis, recognizer improvement, transactional editing and independent lint after damaged/missing output. |
| 30-part quality and CI acceptance | Passed: all 300 cells exactly match `tools/ci-quality-baseline.json`. No baseline was changed. |
| Leader routes | Passed: 119 callouts on 30 parts; 14 unchanged pre-existing runs remain. |
| 65-part planning instruction budget | Passed: 2,766,512,996,858 instructions; 1.005 times the recorded budget, below the unchanged 1.15 limit. Wall time 278.9 s is informational. |
| MCP schemas | Independent draft-2020-12 validation passed for all 13 tools / 26 published and served schemas, including new feature/face queries. Compact schemas total **42,283 bytes**, below the unchanged 42,500-byte budget. |
| WASM build and native parity | Passed: locked `wasm-release` web package build, locked native reference build and all three Node tests. The two session replays cover 19 states, 53 byte comparisons and four structured preview comparisons, including correction edits and undo/redo. |

WASM size is 13,797,666 bytes raw, 4,556,177 bytes gzip and 3,016,029 bytes Brotli.
At that pre-merge checkpoint, `Engine` exports and signatures were unchanged and
`tools/check_wasm_api.py` was not present. The #77 merge adds that gate; its result is
recorded below.
The package build reported the existing wrapper-directory LICENSE-file notice; it
completed successfully. Nothing was published.

Wall-clock failures were retained and rechecked without changing or disabling either gate:

| Gate (150 ms limit) | In suite | Isolated recheck |
| --- | ---: | ---: |
| Multi-sheet MCP p90 | 767.579 ms | 324.825 ms |
| NIST session p90 | 511.574 ms | 375.075 ms |

The multi-sheet native p90 was 124.521 ms in suite and 64.956 ms in isolation.
The instruction gate and all functional assertions passed. The timing failures are
reported as measured; no claim is made that the wall-clock gates passed.

## Drafting review

Run `cargo run --release --example recognition_corrections`. All six PNGs were opened
and inspected. Their STEP geometry is unchanged; the pocket is a real native omission.
SVG, bound Spec, Plan and independent lint JSON accompany each PNG.

| Example | Before PNG | After PNG | Observed change |
| --- | --- | --- | --- |
| Split grid | `target/recognition-corrections/renders/split-grid-before.png` | `target/recognition-corrections/renders/split-grid-after.png` | Grid pitch notation becomes individual column/row locations. Shared hole size remains counted. A4 1:1 / 11 dimensions becomes A3 2:1 / 13 dimensions through normal sheet selection. |
| Missed pocket | `target/recognition-corrections/renders/missed-pocket-before.png` | `target/recognition-corrections/renders/missed-pocket-after.png` | Adds the measured `14 × 22 × 10 DEEP` callout and two 35 mm centre locations. Both use A3 2:1; four dimensions become seven. The pre-existing 20 mm floor station remains. |
| Boss to band | `target/recognition-corrections/renders/boss-band-before.png` | `target/recognition-corrections/renders/boss-band-after.png` | Uses the turned-band diameter/length grammar in the side view; the ordinary hole planner re-evaluates pattern corroboration. A4 1:2 / 13 dimensions becomes A4 1:1 / 11 dimensions. |

The changed statements are readable, with clear leaders and unchanged geometry. All
six independent lint reports contain zero findings. Existing planner notifications
remain visible: missing general tolerance and recognition status, plus three
`annotation_outside_area` warnings on the grid sheets and the corrected boss sheet
(six on its default sheet). Unclaimed geometry remains on the boss sheets and on the
uncorrected pocket. The corrected pocket removes its unclaimed-geometry warning.
These examples use only A4/A3; no A2+, forced detail, sheet policy, datum rule or fit
formatting change was introduced.

## Merge of #77 and split-grid investigation (2026-10-17)

Merged `origin/main` without rebasing. Both Unreleased feature entries and the WASM
API notes are retained. MCP schemas were regenerated, preserving the typed line-work
switches and recognition declarations. Main's anchored-name compactor is the only
property-grouping implementation; exact end-of-name checks remain necessary because
JSON Schema's `$` can match before a final newline. Common map/list constraints apply
alongside named view/declaration constraints. Closed-object cardinalities and short
local JSON Pointer targets keep the 26 standalone schemas at **42,413 bytes**, below
main's unchanged **42,500-byte** limit. Native validation covers the emitted object
cardinalities and property-name whitelists. Independent validation checks simultaneous
named/pattern constraints, missing/replaced declaration keys and malformed types.
An additional 13,863 original/compact schema comparisons agreed.

The premise that A4 1:1 was rejected is false. With all **13 dimensions**, the automatic
candidate report says:

```text
A4 1:1 fill 52.2% (valid and complete, 0 additional view conflicts,
0 additional material crossings, 4 annotation conflicts, stacked-iso)
A3 2:1 fill 55.1% (valid and complete, 0 additional view conflicts,
0 additional material crossings, 3 annotation conflicts, columns)
```

No A4 fit check, hard finding, missing ordinate or dropped item failed. The four
annotation entries are informational `annotation_outside_area` notices, not ink
collisions: one thickness dimension extends 2.7 mm past its reserved band, and three
X locations extend 0.1, 7.1 and 14.1 mm past theirs. These do not leave the usable
sheet. The pinned A4 1:1 plan places every dimension and passes independent drawing
lint. Its two warning-level notifications concern absent general tolerance and the
native recognizer's unsupported recess family, neither caused by splitting.

A3 wins the existing fill comparison after A4 is accepted. The unsplit example keeps
A4 despite A3's 54.4% fill because its A3 candidate adds one material crossing; the
split A3 candidate adds none. No split-location placement fault was found. The new
`split_grid_a4_is_a_complete_fit_and_a3_is_selected_only_by_fill` regression proves
both the accepted automatic A4 candidate and the fully placed, independently linted
pinned A4 drawing. Sheet selection, thresholds and placement code are unchanged.

Merge validation uses logs in `target/recognition-corrections/merge-validation/`:

| Check | Result |
| --- | --- |
| `cargo fmt --all --check` | Passed. |
| `cargo clippy --workspace --all-targets --release -- -D warnings` | Passed on the final test source. |
| MCP schemas | Passed: all 13 tools / 26 published and served schemas agree, including refusal outputs and strict line-work/declaration shapes. Clean regeneration reproduces every file. |
| Recognition regressions | All 20 passed: the original 19 plus the A4 acceptance/fill regression. |
| `tests/linework_spec.rs` | All six passed. |
| Schema budget / engine version | All three tests passed; 42,413 compact schema bytes. |
| WASM | The exact CI `wasm-pack build crates/draftwright-wasm --target web --profile wasm-release --locked` build passed, then `python3 tools/check_wasm_api.py --base-ref origin/main` matched the reviewed snapshot. |

The WASM tool cache and uv cache were placed under `/private/tmp` because the default
user caches are outside this run's writable roots. WASM retained the existing wrapper
LICENSE-file notice. No build setting, gate or threshold was disabled or changed.

The final full run used:

```sh
DRAFTWRIGHT_CORPUS=/Users/paul/repos/quiddity/tests/corpus \
DRAFTWRIGHT_CORPUS_REQUIRED=1 CARGO_BUILD_JOBS=4 RUST_TEST_THREADS=4 \
cargo test --workspace --release --no-fail-fast
```

It completed **1,140 passed, two failed, zero ignored** across 116 test/doc-test
harnesses, including all **110 integration targets**. Both documentation-test
harnesses completed. The two failures were retained and reported:

| Full-run failure | Evidence / recheck |
| --- | --- |
| `session::turned_benchmark_deltas` | GRM-03 native p90 **540.969 ms**, above the unchanged **150 ms** gate. The eight samples were 7.342, 7.383, 7.592, 8.120, 10.480, 12.666, 13.654 and 540.969 ms. No gate, threshold or latency environment override was changed. |
| `stateless::http_slow_body_shares_head_deadline_and_serves_queued_ping` | `read_to_end` returned macOS `ConnectionReset` (OS error 54) before the test reached its timing assertion. The exact test passed unchanged in isolation in **10.64 s**, including its 408 response, 9–12 second deadline assertion and queued ping. This is reported separately from the latency gate; HTTP implementation and test timing remain unchanged. |

The isolated recheck used the same corpus-required workspace release command with
`--test core stateless::http_slow_body_shares_head_deadline_and_serves_queued_ping -- --exact --nocapture`
and one test thread. Its log is `http-slow-body-recheck.log`. The original full-run
failure remains in `full-suite.log`; the successful recheck does not replace it.
No other full-suite target failed. Nothing was pushed.

## Merge of geometry-authored dimensions (#78, 2026-10-17)

Merged `origin/main` at `a22bcab2` without rebasing. Recognition correction binding
runs after migration and before the authored-dimension check, so validation sees
remapped dimension entries. Both feature descriptions and WASM API notes remain in
`model.md` and `CHANGELOG.md`. A combined regression plans a suppressed recognition
and a geometry-authored width, round-trips the bound Spec, and refuses malformed
reference counts.

The MCP generator retains `dimension`, `spec_read`, recognition declarations and
shared map/list `patternProperties`. Its iterative compaction includes main's
`hoist_objects`, preserves root-local definitions, and measures compact UTF-8 bytes.
Closed enum objects can use their inherited object type; finite property-name
choices share underscore prefixes such as `centre_(marks|lines)`. The native matcher
accepts exactly those literal choices. The newline guard now rejects a final newline
with the same meaning in independent JSON Schema validation and native validation.
The shared map constraint supplies object types for the typed `spec_read` dimensions.
No schema JSON was hand-merged.

The 26 schemas total **42,405 compact bytes**, below the unchanged **42,500-byte**
budget. All **36,548** original/compact schema comparisons agreed. The independent
server check validates all 13 tools' inputs, outputs and refusals and confirms the
26 published and served schemas agree. Native and independent regressions cover
prefix choices, final-newline keys, authored dimension forms and declarations together.

Logs for this merge are in
`target/recognition-corrections/authored-merge-validation/`.

| Merge check | Result |
| --- | --- |
| Release build | Passed. |
| `cargo fmt --all --check` | Passed. |
| `cargo clippy --workspace --all-targets --release -- -D warnings` | Passed. |
| Independent MCP validation | Passed: 13 tools / 26 schemas; clean regeneration is reproducible. |
| Python schema compactor / WASM safeguard tests | All three / all 16 passed. |
| Focused release regressions, required corpus | All 54 passed: 21 recognition, 14 authored dimensions, six linework, ten MCP efficiency and three engine-version/schema-budget tests. |
| WASM build / API | Exact locked CI `wasm-release` web build passed; `check_wasm_api.py --base-ref origin/main` matches the reviewed snapshot. |
| WASM size | Passed: 13,951,742 bytes raw, 4,614,466 gzip, 3,080,837 Brotli; raw ceiling remains 15,700,000 bytes. |
| Native/WASM parity | Locked native reference build and all four Node tests passed, including correction sessions and geometry-authored dimension replay. |
| Full corpus-required workspace release suite | 1,160 passed, one failed, zero ignored across 117 test/doc-test harnesses, including all 111 integration targets. Both doc-test harnesses completed. All session latency gates passed unchanged. |

The full suite ran to completion with:

```sh
DRAFTWRIGHT_CORPUS=/Users/paul/repos/quiddity/tests/corpus \
DRAFTWRIGHT_CORPUS_REQUIRED=1 CARGO_BUILD_JOBS=4 RUST_TEST_THREADS=4 \
cargo test --workspace --release --no-fail-fast
```

The one failure was
`stateless::http_slow_head_has_absolute_deadline_and_serves_queued_ping`:
`http_response`'s `read_to_end` returned macOS `ConnectionReset` (OS error 54) at
`tests/stateless.rs:181`, before the timing assertion. The slow-body counterpart
passed. The unchanged slow-head test passed in isolation in **10.65 s**, with the
same required corpus and one test thread:

```sh
DRAFTWRIGHT_CORPUS=/Users/paul/repos/quiddity/tests/corpus \
DRAFTWRIGHT_CORPUS_REQUIRED=1 CARGO_BUILD_JOBS=4 RUST_TEST_THREADS=1 \
cargo test --workspace --release --test core \
  stateless::http_slow_head_has_absolute_deadline_and_serves_queued_ping -- --exact --nocapture
```

The original failure remains in `full-suite.log`; its successful isolated recheck
is in `http-slow-head-recheck.log`. Neither HTTP implementation nor its tests were
changed. No latency gate, threshold or latency environment override was changed.
The release and WASM builds passed; WASM emitted the existing wrapper LICENSE-file
notice. Nothing was pushed.
