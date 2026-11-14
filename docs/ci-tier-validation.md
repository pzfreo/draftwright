# CI tier validation — 2026-11-14

The tier change preserves the root-package scope of the existing nextest archive. Every
main push runs all ten duration-balanced shards and the separate whole-binary harness;
the previous release-metadata shortcut has been removed. No engine or Rust test code changed.

## Inventory and runner budget

Built with `CARGO_BUILD_JOBS=2 cargo nextest list --workspace --release --lib --bins --tests
--profile ci --message-format json`. No workspace nextest run was performed. Running
`tools/test_shards.py --max-seconds 10 --check-tiers` against that JSON gives:

| Inventory | PR shard tests | Full shard tests | Unrecorded, always included | PR recorded minutes | Full recorded minutes |
| --- | ---: | ---: | ---: | ---: | ---: |
| Workspace | 2,084 | 2,355 | 8 | 34.11 | 341.17 |
| Existing CI root package | 2,076 | 2,347 | 0 | 34.11 | 341.17 |

The root row selects the `draftwright` package's suites from the workspace listing. The
other eight tests belong to the Python-extension and WASM crates, outside the existing CI
archive's scope. Retaining this scope keeps main's suite unchanged.

Both tiers additionally run **117 whole-harness tests**: `issue_drafting` 40,
`issue_trust` 13 and `steerability` 64 (inventoried with each binary's `--list`). Thus PRs
cover 2,193 root tests and main covers 2,464, excluding doc-tests and acceptance checks.
Harness durations are not in the duration record. There are **271 currently listed slow
tests**, contributing 307.06 recorded minutes. The original duration record covered only 1,763 tests. Refreshed it with the existing
`tools/test_shards.py --record` using all ten JUnit artifacts of successful main run
[43603748868](https://github.com/pzfreo/draftwright/actions/runs/43603748868), whose head
`48587162e22291814f14c42167c39d57af2c8e19` exactly matches this branch's `origin/main` base.
Verified 2,347 distinct report cases equal the entire local root listing before recording.
Every future unrecorded test still runs on its PR; eight workspace-only crate tests remain
unrecorded. Local benchmark timings were not used to update CI durations.
The CLI simulation of every PR/full shard verified exactly-once coverage and
`PR ∪ slow = main`, with no overlap between PR and slow.

Two PR shards carry about **17.06 recorded test-minutes each**; ten main shards carry about
**34.12 each**. Unrecorded tests use median weights for balancing. The estimates are balancing
weights, not wall times. Two PR shards leave headroom for process startup and new tests;
one would carry 34.1 minutes before startup overhead. Unrecorded tests may be arbitrarily slow.

`ci.yml` expands to **10 PR jobs versus 22 previously**: setup, lint, archive build, acceptance
binary, two fast shards, harness, two quality-pins shards and quality-gate. The PR acceptance
shards measure all **68 unpinned quality/AP242/survey parts**. The gate retains drawing metric
ratchets, crossing and route checks, label-lane checks, the deliberate-regression rejection,
and the complete zero-repeated-span survey. The full tier retains **338 measurements**,
including all **270 random-pin drawings** in nine cells, across its original four shards.
Missing/wrong-tier shard evidence fails the gate; PR evidence cannot satisfy the full gate.
The full tier also retains budgets (plan-time, render-tail, MCP) and browser WASM parity.
Main remains 22 jobs. Unrelated label events use an isolated concurrency group and skip
the setup job, so they neither consume runners nor cancel an active PR run. Other path-filtered packaging workflows retain their PR checks.

Read-only inspection of the latest successful main run, **43603748868**, found a 24.5-minute
run: archive build 8.83 minutes, lint 9.12, harness job 3.77, the longest quality-pins shard
11.73 after a 3.83-minute binary build, then quality-gate 1.02. Therefore retaining the entire
acceptance grid on PRs would itself take about 17 minutes. This evidence drove the PR-only
unpinned acceptance tier rather than retaining the whole pin sweep. Existing harnesses
still cover drawing and pin behavior on PRs. The approximately 10–12-minute PR target is
unverified: the historical build/lint plus harness path alone was about 13 minutes, and
unrecorded tests/runner queues add uncertainty. No workflow was pushed or dispatched.
Read-only branch-rules lookups found no required status checks needing a matrix-name migration;
the classic protection endpoint reported that main is not protected.

## Release enforcement

All four publishing workflows (CLI assets, WASM assets, crates.io, PyPI) now check the exact
tag commit using the read-only Actions API before any public write. The check requires a
successful completed push-to-main run and all 22 full-tier jobs successful in one attempt.
It rejects historical lint-only success, skipped jobs, PRs, dispatches and other commits.
It reads the guard from the workflow revision so backfilled tags cannot omit it. Package
builds, versions, publication actions and trusted-publishing permissions are otherwise retained.

## Checks

- `cargo fmt --all --check`: passed.
- `CARGO_BUILD_JOBS=2 cargo clippy --workspace --all-targets --release -- -D warnings`: passed.
- Shard/inventory/tier/release Python tests: 19 passed. Acceptance runner tests: 30 completed
  with one optional skip. Wheel version/provenance guard tests: 21 passed.
- `yaml.safe_load` parsed all six workflow files. Extracted shell steps passed `bash -n`
  after substituting GitHub expressions. `actionlint` was not installed.
- Matrix expansion and the latest successful main run verified all 22 job names equal
  the release guard's required set.
- The existing latency-gate environment, duration balancing, longest-first priorities and
  plan-cache warm-up remain enabled in CI. No full-run lock was added.

## Local cache measurements

Measured release builds on Darwin arm64, with `DRAFTWRIGHT_CORPUS_REQUIRED=1`, the local
pinned corpus, and two test threads. These are cache-heavy workloads from two slow binaries,
not an extrapolation of full-workspace speed: **23 `dimensions` tests matching `dims_blends::`**
and **30 `audits` tests matching `items::on_the_quality_part::`**. A broader preliminary run
was stopped to avoid unrelated engine work on the loaded machine; only the scoped,
verified readings below are used.

For each binary, cold means a fresh empty directory beneath `target/ci-cache-bench/`;
warm repeats the same workload using that directory in a new process; no cache unsets
`DRAFTWRIGHT_TEST_PLAN_CACHE`. The two binaries use separate directories, so these results
do not include the additional savings from sharing entries between different binaries.

`/usr/bin/time -l /usr/bin/true` failed with exit 1 and
`sysctl kern.clockrate: Operation not permitted` in the execution sandbox. Used the
repository's **existing `tools/check_plan_time.py::counted`** instead: it reads the kernel's
`proc_pid_rusage(RUSAGE_INFO_V4).ri_instructions` before reaping the child and correctly
waits through load-governor pauses. The figures are retired instructions, not wall time.
They count the test process and its threads; tiny fixture-writer/helper subprocesses are
outside that counter. No engine, cache or latency assertions were changed.

| Workload | Cold cache (trillion instructions) | Warm cache | No cache | Warm reduction vs no cache |
| --- | ---: | ---: | ---: | ---: |
| `dimensions` (23 cases) | 3.283438 | 2.555227 | 3.266296 | 21.8% |
| `audits` (30 cases) | 5.530732 | 3.275905 | 5.503673 | 40.5% |

All **159 case executions passed** across the six measured runs, with no ignored cases or
refused cache entries. Cold/warm directories held the same 21 entries for dimensions and
60 for audits; warm runs added none. Filling an isolated cache cost about 0.5% more
instructions than no cache in these samples; warm reuse saved 21.8% and 40.5%, respectively.
Use the cached full-suite command in [CONTRIBUTING.md](../CONTRIBUTING.md). Cache keys include
the engine source fingerprint (`src/plan_cache.rs::key`, populated by `build.rs` and
`build_support.rs`), so rebuilt engine changes cannot read old entries. The existing per-entry
cache coordination remains; there is no lock serializing full local test runs.
