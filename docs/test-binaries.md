# Integration test binaries

Integration tests use eight explicit Cargo targets instead of one executable per
`tests/*.rs` file. Four target roots include the original files with `#[path]`:
file history, relative helper paths and fixture paths are retained. Function names
are unchanged; grouped test names gain their original file stem as a module prefix.
The two corpus-wide harness targets and the two latency-sensitive targets remain separate.

## Grouping

| Target | Tests | Original source modules |
| --- | ---: | --- |
| `core` | 279 | `api_analysis`, `assembly_parts`, `bridge`, `cli_native`, `editor_session`, `engine_version`, `facts`, `gears`, `gzip`, `identity`, `mcp`, `mcp_efficiency`, `mcp_spec_schema`, `model`, `native`, `native_recesses`, `pmi`, `pmi_lowering`, `pmi_thread_knurl`, `recognition_corrections`, `revision`, `saved_spec`, `serve`, `sheets_mcp`, `stateless`, `test_support` |
| `dimensions` | 336 | `angled_steps`, `authored_dimensions`, `coverage_holes`, `datum_placement`, `dimension_rules`, `dimension_schemes`, `dimensions`, `dims_blends`, `dims_edges`, `dims_grooves_flats`, `dims_plates`, `dims_recesses`, `dims_slots`, `dims_spotface`, `dims_steps`, `dims_stock`, `dims_turned`, `fit_label_layout`, `hole_tables`, `planned_hole_tables`, `plate_coverage`, `pmi_angular`, `pmi_dimensions`, `precision`, `repeated_dimensions`, `tolerances` |
| `rendering` | 475 | `bands`, `centrelines`, `detail_views`, `dxf_native_gdt`, `fix_relayout`, `gdt_band`, `h5_chains`, `interior_placement`, `iso_nts`, `label_lanes`, `label_rows`, `label_text`, `layout`, `leader_rules`, `leaders`, `line_through_feature`, `linework`, `linework_spec`, `notes`, `oriented_chamfers`, `output`, `page_escalation`, `pinned_callouts`, `pinned_offset`, `placement`, `placement_intent`, `projection_alignment`, `pmi_drawings`, `pmi_labels`, `png`, `render_caches`, `render_panics`, `repeated_labels`, `sections`, `sheet_fill`, `sheet_projection`, `sheets`, `sheets_lint`, `sheets_render`, `sheets_xref`, `spec_gdt`, `spec_notes`, `surface_texture`, `table_symbols`, `tables`, `text`, `timezone`, `title_block`, `title_block_material`, `views`, `views_layout_rules` |
| `audits` | 158 | `completeness`, `completeness_rules`, `issue_regressions`, `issue_tiers`, `items`, `lint`, `lint_coverage`, `lint_family_rest`, `lint_fidelity`, `lint_structural`, `ownership_rules`, `pmi_coverage`, `problem_wording`, `review_leftovers`, `rule_pins`, `rules_catalogue`, `rules_text`, `trust_rules` |
| `issue_drafting` | 37 | `issue_drafting` (separate process scope) |
| `issue_trust` | 13 | `issue_trust` (separate process scope) |
| `session` | 8 | `session` (separate process scope) |
| `multi_sheet_latency` | 1 | `multi_sheet_latency` (separate process scope) |

The current source inventory contains 1,305 integration tests. The table-symbol
regressions add four tests covering inline ink, declared tables and numbered notes,
GRM-03 manufacturing requirements, and the 30 quality parts.
The final-inventory label-association regression adds one rendering test,
including the recorded compact-label exception and unchanged model values.
Historical before/after measurements below retain their original counts.

## Running tests

```sh
export CARGO_BUILD_JOBS=12 RUST_TEST_THREADS=12
export DRAFTWRIGHT_CORPUS=/Users/paul/repos/quiddity/tests/corpus
export DRAFTWRIGHT_CORPUS_REQUIRED=1
cargo nextest run --release --test rendering -E 'test(/^oriented_chamfers::/)'
cargo test --release --test rendering oriented_chamfers::
cargo test --release --test rendering oriented_chamfers::datum_scheme_takes_the_notch_station_once -- --exact
cargo test --release --test issue_drafting --test issue_trust
```

For grouped sources, use the table above to replace `--test old_file` with `--test group old_file::`
for Cargo's harness, or `--test group -E 'test(/^old_file::/)'` for nextest. An
`--exact` selector must include the entire module path. Historical validation
reports retain their recorded counts; these commands describe the current layout.

## Inventory guard

`autotests = false` makes new root files opt-in. Add a module to the appropriate
target root when adding a test source. Lint runs
`python3 tools/check_test_binaries.py`, which walks every explicit target's module
graph and rejects any unreachable `.rs` file anywhere under `tests/`, including
helper files. It handles ordinary modules, path modules, inline modules and the
existing `part_cases!` / `pmi_cases!` generators. A new generator must extend the
inventory reader and its regression checks.

CI's harness job also lists the uploaded archive and passes the complete inventory
to this check. The equivalent local commands are:

```sh
cargo nextest list --release --ignore-default-filter --message-format json > nextest-list.json
python3 tools/check_test_binaries.py --nextest-list nextest-list.json
```

The second check compares exact per-target test-name sets inferred from source
with the compiled inventory, including the two harness targets. A count drop,
missing target, omitted macro case or replacement with an unrelated test fails.
There is no manually maintained count to forget to update.

## Process-local state

`session` and `multi_sheet_latency` keep their original process scopes. An initial
six-group trial put both into an interface binary: its shared-process libtest run
failed the unchanged R5 MCP 150 ms gate at p90 521.676 ms, although both nextest
profiles passed. Keeping both benchmark targets separate avoids adding competing
modules to their native measurements; no gate, threshold or assertion was changed.

Each original module still declares its own helper modules. The caches and counters
in `common/cache.rs` are therefore still separate instances per original module,
with the original filename/content/recognition-mode and whole-Analysis cache keys.
`OnceLock` fixture/plan caches remain private to their original modules. Scratch
paths use their original area namespaces, plus process IDs and counters where
parallel calls need distinct files. Tests pass environment settings to child
commands rather than mutating the parent environment. Production memo keys include
the full inputs and are synchronized or thread-local. Shared-process libtest runs
exercise the grouped modules as well as nextest's per-test processes.

Group roots allow Clippy's `duplicate_mod` lint because loading helpers separately
is intentional. Only the `facts` wrapper allows `module_inception`, retaining its
original private `facts` helper and test prefix. Other Clippy warnings remain errors.

The `ci` profile still excludes only `issue_drafting` and `issue_trust`; the workflow
continues deriving those names from its default filter and running their archived
executables whole. Grouped priorities and reservations use qualified module names;
former binary-wide rules select the corresponding module within its group. Native
benchmark filters keep their original target and test names.

## Shared analyses and plans (CI)

nextest runs every test in its own process, so the `OnceLock` caches above do not
outlive one test. In CI, `DRAFTWRIGHT_TEST_PLAN_CACHE` names a directory through
which `common/cache.rs` shares `native` analyses and `default_plan` plans between
a job's test processes (`src/plan_cache.rs`). An entry is keyed by the engine's
source fingerprint (`DRAFTWRIGHT_ENGINE_VERSION`) and every input (the file's name
and bytes; the whole Analysis and Spec), is written only when it reads back
identical (`Debug`) to the value made, and a miss computes as before. The lint
job fills it for the 66 survey parts with the command line
(`tools/warm_plan_cache.py`, `DRAFTWRIGHT_PLAN_CACHE`), and each quality-pins shard
shares its own analyses and plans the same way. Unset locally, nothing changes; set
it to any empty directory to share work across a local nextest run.
`corpus::analyse` is shared too; `cache::analysed` and every direct `model::plan`
call still compute in each process.

## Before/after measurements

Measured sequentially on 2026-10-18, macOS aarch64, 18 logical CPUs, Rust 1.99.0,
nextest 0.9.148, with `CARGO_BUILD_JOBS=12`. Baseline: `bd170311` (`origin/main`
at the start of this work). CI pins nextest 0.9.146; the inventory reader uses its
stable JSON suite fields. No release optimization settings changed.

The checkout started without `target/`. Warm dependencies and targets first,
then measure a library-touch rebuild. Warm the final grouped targets before
repeating the same measurement. The archive comparison also touches the library,
so both archive times include rebuilding the crate and every test target:

```sh
export CARGO_BUILD_JOBS=12
cargo build --release --tests                       # unmeasured warm-up
# Measure:
touch src/lib.rs
/usr/bin/time -p cargo build --release --tests --timings
cargo nextest list --release --ignore-default-filter --message-format json > list.json
# Separately measure a fresh library-touch CI archive:
touch src/lib.rs
/usr/bin/time -p cargo nextest archive --release --archive-file nextest-archive.tar.zst
```

| Metric | Before | After | Reduction |
| --- | ---: | ---: | ---: |
| Library-touch release test rebuild | 198.25 s | 136.18 s | 31.3% |
| Library-touch nextest archive, including build | 217.86 s | 136.94 s | 37.1% |
| Integration binaries | 119 | 8 | 111 fewer |
| Default-member test binaries, including unit harnesses | 122 | 11 | 111 fewer |
| Integration executable bytes | 2,864,520,160 (2.67 GiB) | 272,136,224 (259.5 MiB) | 90.5% |
| Default-member test executable bytes | 2,898,662,224 | 306,278,288 | 89.4% |
| Archive bytes | 891,178,779 (849.9 MiB) | 109,517,842 (104.4 MiB) | 87.7% |
| Unfiltered nextest tests | 1,497 | 1,497 | unchanged |
| Integration tests | 1,246 | 1,246 | unchanged |
| CI harness exclusions | 50 (37 drafting + 13 trust) | 50 (same tests) | unchanged |

The external rebuild timer was inconsistent: it printed `real 2.97` before and
`real 77.16` after, while Cargo's timing reports span 198.10 and 136.14 seconds.
The table uses independent UTC elapsed measurements: the baseline build log's
creation-to-final-write timestamps (198.250 s), and timestamps around
the final rebuild subprocess (136.183 s). Archive UTC durations
(217.859 / 136.937 s) agree with their external timers
(217.85 / 136.93 s). The inconsistent rebuild timers are not used.

Cargo timing reports attribute 1,613.50 → 282.31 summed unit-seconds to
integration compilation/linking, or 93.2% → 64.9% of all
compiler unit time. Total summed compiler unit time is 1,730.59 → 435.00
seconds. These overlapping elapsed unit durations are neither wall time nor pure
CPU time. Stable Cargo did not provide separate codegen/link sections for
executable test targets, so a **pure linker share is unavailable**.

Sizes sum executable paths in nextest's unfiltered inventory under
`target/release/deps`, excluding libraries, metadata and application executables.
Superseded test executables were removed by their recorded paths after archiving
the baseline; dependency artifacts stayed warm. The name comparison restores each
grouped module's original binary identity and checks all names and ignored flags:
all 1,497 tests match.

Production sources, common helpers and every fixture are unchanged. Among the
original 119 integration sources, only the catalogue checker changed: qualified
citations now resolve through their target/module instead of accepting only their
last function-name segment. YAML changes are confined to `rust_test` fields.
The survey's recursive source scan and source-file links retain their original
paths; no original test source was moved or copied.

These are local build/archive measurements, not GitHub-hosted workflow wall times.
Measurements ran without concurrent verification commands. Host load can vary,
and new binary/test paths also change hash-shard membership.

## Verification

All runs used `CARGO_BUILD_JOBS=12`, `RUST_TEST_THREADS=12`,
`DRAFTWRIGHT_CORPUS=/Users/paul/repos/quiddity/tests/corpus` and
`DRAFTWRIGHT_CORPUS_REQUIRED=1`. Native latency assertions stayed enabled.

| Check | Final result |
| --- | --- |
| `cargo fmt --all --check` | PASS |
| `cargo clippy --workspace --all-targets --release -- -D warnings` | PASS |
| `cargo nextest run --release --workspace --no-fail-fast --test-threads 12` | PASS: 1,497 tests, 0 skipped; 426.838 s test execution |
| Same nextest command with `--profile ci` | PASS: 1,447 tests, 0 skipped; 374.556 s test execution |
| `cargo test --release --test issue_drafting --test issue_trust` | PASS: 37 + 13 tests |
| `python3 -m unittest discover -s tools -p 'test_*.py'` | PASS: 101 tests, no skips |
| Source reachability and actual uploaded-archive inventory | PASS: 8 targets, 138 reachable Rust files, 1,246 integration tests |
| Before/after normalized nextest inventory | PASS: all 1,497 names and ignored flags identical |
| Nextest exact-name override audit | PASS: all 18 distinct exact selectors resolve uniquely |
| `cargo test --release --doc` | PASS: 0 doc-tests |

The Python suite ran in a temporary virtual environment with the documented
`jsonschema==4.25.1` dependency; the CI inventory guard and its four regression
checks use only the standard library.

The additional shared-process check, `cargo test --release --tests --no-fail-fast`,
ran all 1,497 tests: **1,496 passed**, and the unchanged standalone session target's
NIST latency gate failed at p90 **282.973 ms** against 150 ms. All four grouped
binaries passed (155 audit, 269 core, 321 dimension and 442 rendering tests), as
did the separate R5 latency target and both corpus harnesses. An unchanged
`cargo test --release --test session -- --nocapture` rerun, still with 12 threads,
passed **all eight tests**, with NIST p90 **99.601 ms**. Both complete nextest
profiles also passed the gate. This successful rerun is reported separately from
the full-Cargo timing failure; no assertion, gate, threshold or environment
waiver was introduced.

No CI acceptance jobs were run. No original test was removed, renamed beyond its
module prefix, ignored or weakened. Nothing was pushed.
