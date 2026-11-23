# Rust test-suite scheduling and reuse

Measured on 2026-10-17 on the same macOS aarch64 machine (Rust available parallelism: 18), from `origin/main` (`0e393afcda459277deecf78dff5fce1d3758e599`) and this branch. Timed runs were sequential. Release executables were built before the complete before/after suite timings; build time is excluded from nextest’s run duration. The external command wall times below include Cargo/nextest startup.

## Full workspace suite

| Metric | Before | After |
| --- | ---: | ---: |
| Test execution (nextest Summary) | 553.559 s (9m13.6s) | 511.870 s (8m31.9s) |
| Total command wall time | 553.984 s (9m14.0s) | 512.295 s (8m32.3s) |
| Tests run | 1296 | 1483 |
| Result | 1293 passed (21 slow), 3 failed, 0 skipped | 1483 passed (19 slow), 0 skipped |

Observed test execution fell 7.5%; command wall time fell 7.5%.

The first unmodified invocation used the exact requested command and failed fast on the existing `multi_sheet_latency::r5_multi_sheet_latency` timing gate (MCP p90 691.739 ms against 150 ms): 877/1,296 tests ran in 352.659 s. Its cold release build took 4m29s, and the incomplete command took 663.322 s. That incomplete run is excluded from the comparison. Complete workspace runs add `--no-fail-fast` so every test is attempted; no latency gate is disabled.

## Ten slowest tests

### Before

| Test | Seconds |
| --- | ---: |
| `draftwright::items::every_plan_item_has_a_state_and_every_problem_subject_is_a_live_item` | 452.626 |
| `draftwright::pmi_coverage::pmi_coverage_matches_draftwright_or_is_known` | 425.067 |
| `draftwright::items::on_the_quality_parts_too` | 386.961 |
| `draftwright::problem_wording::only_sheet_problems_have_the_sheet_as_subject` | 333.965 |
| `draftwright::problem_wording::no_problem_message_carries_an_internal_id` | 333.955 |
| `draftwright::pmi_dimensions::every_dimension_is_drawn_or_reported_never_both` | 324.333 |
| `draftwright::label_text::pmi_callout_wording_is_reserved_drawn_searchable_and_structured_symbols_refuse_it` | 319.693 |
| `draftwright::pinned_offset::pinning_every_callout_at_its_reported_offset_moves_nothing` | 314.874 |
| `draftwright::pmi_drawings::pmi_on_the_drawing_matches_draftwright_or_is_known` | 302.091 |
| `draftwright::pmi_dimensions::the_same_part_gives_the_same_plan_and_bytes` | 294.776 |

### After

| Test | Seconds |
| --- | ---: |
| `draftwright::problem_wording::only_sheet_problems_have_the_sheet_as_subject` | 315.452 |
| `draftwright::problem_wording::no_problem_message_carries_an_internal_id` | 315.413 |
| `draftwright::pmi_drawings::pmi_on_the_drawing_matches_draftwright_or_is_known::nist_ftc_10_asme1_ap242_e2` | 262.329 |
| `draftwright::items::every_plan_item_has_a_state_and_every_problem_subject_is_a_live_item` | 257.260 |
| `draftwright::label_text::pmi_callout_wording_is_reserved_drawn_searchable_and_structured_symbols_refuse_it` | 241.213 |
| `draftwright::pmi_dimensions::the_same_part_gives_the_same_plan_and_bytes::nist_ftc_10_asme1_ap242_e2` | 241.053 |
| `draftwright::pmi_coverage::pmi_coverage_matches_draftwright_or_is_known::nist_ftc_10_asme1_ap242_e2` | 230.288 |
| `draftwright::pmi_dimensions::the_open_questions_stay_refused::nist_ftc_10_asme1_ap242_e2` | 221.904 |
| `draftwright::pmi_dimensions::pmi_dimensions_match_draftwright_or_are_known::nist_ftc_10_asme1_ap242_e2` | 219.480 |
| `draftwright::pmi_dimensions::every_dimension_is_drawn_or_reported_never_both::nist_ftc_10_asme1_ap242_e2` | 218.092 |

Complete baseline suite failures: `draftwright::pmi_dimensions::the_open_questions_stay_refused`, `draftwright::pmi_dimensions::pmi_dimensions_match_draftwright_or_are_known`, `draftwright::session::nist_benchmark_deltas`. Complete changed suite failures: none.

The unmodified full run can encounter cross-process PMI scratch-file removal races; its result is reported as observed, and failed loop tests can stop early. This makes its wall-time comparison conservative rather than a clean passing-baseline comparison. The original PMI comparisons also ran successfully in the four-thread CI simulations; process-specific scratch paths address this race in the changed tests.

## CI simulation

Each hash partition ran separately with the unchanged `ci` default filter and `--test-threads 4`. The pre-existing `issue_drafting`/`issue_trust` harness-job exclusions remain exactly as before.

| Shard | Before command wall | After command wall | Change | Tests before → after |
| --- | ---: | ---: | ---: | ---: |
| 1/5 | 273.371 s (4m33.4s) | 358.277 s (5m58.3s) | +31.1% | 260 → 292 |
| 2/5 | 281.337 s (4m41.3s) | 272.580 s (4m32.6s) | -3.1% | 231 → 269 |
| 3/5 | 238.784 s (3m58.8s) | 397.041 s (6m37.0s) | +66.3% | 244 → 289 |
| 4/5 | 194.151 s (3m14.2s) | 226.595 s (3m46.6s) | +16.7% | 258 → 294 |
| 5/5 | 260.950 s (4m20.9s) | 175.776 s (2m55.8s) | -32.6% | 253 → 289 |

Shard outcomes before: 1/5: 260 passed (5 slow), 986 skipped; 2/5: 231 passed (2 slow), 1015 skipped; 3/5: 243 passed (1 slow), 1 failed, 1002 skipped; 4/5: 258 passed (1 slow), 988 skipped; 5/5: 253 passed (4 slow), 993 skipped.
Shard outcomes after: 1/5: 292 passed (5 slow), 1141 skipped; 2/5: 269 passed (3 slow), 1164 skipped; 3/5: 288 passed (2 slow), 1 failed, 1144 skipped; 4/5: 294 passed (2 slow), 1139 skipped; 5/5: 289 passed (2 slow), 1144 skipped.

The slowest observed simulated shard was 281.337 s (4m41.3s) before and 397.041 s (6m37.0s) after (+41.1% change). Hashing case names changes shard membership, so compare the maximum as well as each shard.

## Timing variability and paired check

The primary CI sequence did **not** demonstrate a lower worst-shard wall time. Host load varied substantially: uptime reported a one-minute load above 28 with 18 available CPUs; subsequent recorded samples ranged from 6.4 to 26.6. The same unchanged baseline shard took 273.371 s initially and 328.956 s later. These results are observations on a busy shared host, not controlled estimates of production CI speed.

A supplementary shard-1 pair ran the original code and changed code consecutively, using the same command and environment as the table above. It does not replace the primary measurements.

| Shard-1 paired check | Command wall | Result | One-minute load, start → end |
| --- | ---: | --- | ---: |
| Original | 328.956 s (5m29.0s) | 260 passed (5 slow), 986 skipped | 10.8 → 12.9 |
| Changed | 292.491 s (4m52.5s) | 292 passed (5 slow), 1141 skipped | 12.9 → 8.8 |

The changed run was 11.1% shorter in this pair. Load still changed during the runs, so this is a useful cross-check rather than proof of a general CI improvement.

The NIST native latency gate (`session::nist_benchmark_deltas`, 150 ms) failed in shard 3 in both primary sequences. The changed full workspace run passed it. Its assertions were kept enabled in every run.

Two original PMI tests were rerun serially after the timing sequence: `pmi_dimensions_match_draftwright_or_are_known` passed in 150.904 s and `the_open_questions_stay_refused` passed in 174.738 s (325.643 s test execution, 326.043 s command wall). This confirms their original comparisons pass when their shared scratch paths cannot collide.

An initial changed-tree trial used four slots per worker audit and took 574.563 s, with a GD&T scratch rename collision. That trial was discarded. The final code isolates that path by process, selects fixtures before decompression, reserves two slots for bounded workers, and starts wording audits before individual NIST cases. Invalid-priority startup attempts ran no tests and are excluded. The complete final profile was validated by release test listing before the final full suite and five partitions.

## Final validation

- Full changed workspace: 1,483 passed; zero skipped.
- `cargo fmt --all --check`: passed.
- `cargo clippy --workspace --all-targets --release -- -D warnings`: passed.
- Nextest inventory audit: exactly 23 intended loop names replaced by 210 new case/check names; all other original tests retained; no ignored tests.
- All functional corpus and draftwright comparisons passed in the changed suite and shard runs; the only primary shard failure was the unchanged NIST latency gate.
- No acceptance jobs, library source changes, or pushes.

## Coverage and case mapping

All 30 quality parts, all 22 AP242 fixtures, every existing Spec variant, all draftwright comparisons, and all original assertion expressions remain. No test was newly ignored or excluded; the existing CI harness exclusions are unchanged. No `src/` file changed. Four new helper checks cover the fixture inventory, cache edit isolation/full-analysis keys, concurrent initialisation, and worker order/panic propagation.

| Original loop test | Before → after |
| --- | ---: |
| `pmi_coverage::pmi_coverage_matches_draftwright_or_is_known` | 1 → 22 |
| `centrelines::callouts_do_not_borrow_hole_centre_strokes` | 1 → 6 |
| `centrelines::leaders_do_not_run_beside_centre_lines` | 1 → 3 |
| `pmi_lowering::the_same_part_gives_the_same_plan_and_bytes` | 1 → 2 |
| `pmi_lowering::pmi_lowering_matches_draftwright_or_is_known` | 1 → 22 |
| `pmi_lowering::every_record_and_source_is_drawn_or_reported` | 1 → 22 |
| `pinned_offset::pinning_every_callout_at_its_reported_offset_moves_nothing` | 1 → 4 |
| `pmi_dimensions::the_open_questions_stay_refused` | 1 → 2 |
| `pmi_dimensions::the_same_part_gives_the_same_plan_and_bytes` | 1 → 2 |
| `pmi_dimensions::pmi_dimensions_match_draftwright_or_are_known` | 1 → 22 |
| `pmi_dimensions::every_dimension_is_drawn_or_reported_never_both` | 1 → 22 |
| `pmi_drawings::pmi_on_the_drawing_matches_draftwright_or_is_known` | 1 → 22 |
| `planned_hole_tables::the_dense_acceptance_parts_have_no_hole_location_tower` | 1 → 3 |
| `planned_hole_tables::rounded_datums_and_stacked_openings_are_unambiguous` | 1 → 3 |
| `planned_hole_tables::schedule_labels_clear_the_rendered_part_and_other_annotations` | 1 → 3 |
| `planned_hole_tables::each_tag_is_beside_or_leadered_to_its_own_hole` | 1 → 4 |
| `planned_hole_tables::entry_cells_use_the_view_frame_and_lint_checks_it` | 1 → 2 |
| `title_block_material::the_title_block_states_the_files_material_as_draftwright_does` | 1 → 22 |
| `completeness::nist_member_ordinates_leave_no_false_pattern_findings` | 1 → 2 |
| `dims_blends::many_blends_are_stated_without_counted_radius_clutter` | 1 → 3 |
| `dims_blends::grouped_relief_radii_are_excluded_on_several_parts_with_explicit_evidence` | 1 → 6 |
| `dims_blends::radius_centre_table_credits_every_occurrence_and_rejects_wrong_locators` | 1 → 3 |
| `gdt_band::frames_datum_symbols_and_labels_are_on_the_sheet_and_off_the_other_views` | 1 → 4 |

The blend mappings include the extracted `ctc02_radius_exceptions_retain_credit` and `real_corners_have_no_relief_exclusions` control tests, which each still run once. The 23 loop replacements add 183 cases including those controls; four new helper tests give **1,296 + 183 + 4 = 1,483** tests.

These existing aggregate tests retain their test names and global assertions, mapping independent parts on at most `min(4, available_parallelism())` scoped workers, then reducing results in input order:

- `items::every_plan_item_has_a_state_and_every_problem_subject_is_a_live_item`: all slice specs and AP242 fixtures, including the state-set and >500-items checks.
- `items::on_the_quality_parts_too`: default, never-table, forced 10:1 and explicit FTC08 starvation checks; both original dropped-item totals remain.
- `items::the_drawing_review_reads_typed_states_and_classifies_as_from_prose` and `items::every_item_is_labelled_as_it_is_drawn`: all slice specs and omission checks.
- `interior_placement::all_quality_parts_interior_lines_arrows_and_tips_clear_rendered_ink`: all parts and the positive aggregate count.
- `problem_wording`’s two corpus audits use the same bounded worker helper, retaining every spec and the >200-problems check.

`pinned_offset` additionally distributes callouts over bounded workers within each part case. Each callout still opens separate edited and bare-pin Sessions, and the first callout of each part is still dragged.

## Cache and scheduling details

`tests/common/cache.rs` uses a process-wide `OnceLock<Mutex<HashMap<…>>>` of per-input `OnceLock`s: expensive work runs outside the map lock, and concurrent misses for one input compute once. Native import keys include filename/path and content; scratch import keys also distinguish native recognition from no recognition. Geometry is read-only, while each caller receives its own Analysis and Plan clones. Per-part fixture helpers select filenames before gzip decoding, so a case no longer inflates every unrelated fixture. Default-plan keys hash the entire Analysis and distinguish direct from recovered planning, including edits to PMI, recognition or projection evidence. Non-default specs keep their original planner calls.

Cache helpers are used by items, wording, PMI dimensions/lowering/drawings/coverage/labels, centreline, blend, GD&T, hole-table, interior-placement and label-text audits. `nextest` launches a separate process per case, so caches improve repeated work within a case and reuse under the standard binary harness; they do not share across nextest cases. Independent determinism plans/renders and JSON round-trip replans bypass the cache, including PMI labels, both PMI plan/bytes suites, PMI coverage, interior placement and both blend determinism checks.

`.config/nextest.toml` gives the two remaining whole-corpus wording audits priority 100, then starts other expensive aggregates, FTC10/STC09/CTC02 AP242 cases and pinning cases at priority 80 in both the default and CI profiles. Four-worker tests reserve two scheduler slots, inherited by `ci`, so other cases can use CPU during uneven worker tails while concurrent worker audits remain bounded. The five existing latency benchmarks reserve all slots to measure the unchanged 150 ms gates without other tests competing for CPU; no gate environment variable is disabled. The CI default filter is unchanged.

## Reproduce and validation

```sh
export CARGO_BUILD_JOBS=12
export DRAFTWRIGHT_CORPUS=/Users/paul/repos/quiddity-python/tests/corpus
export DRAFTWRIGHT_CORPUS_REQUIRED=1
cargo nextest run --release --workspace --no-fail-fast
for k in 1 2 3 4 5; do
  cargo nextest run --release --profile ci --partition hash:$k/5 --test-threads 4
done
cargo fmt --all --check
cargo clippy --workspace --all-targets --release -- -D warnings
```

Command wall time uses Python `time.monotonic()` around each subprocess; individual durations and test execution totals are taken from nextest output. No acceptance jobs were run.
