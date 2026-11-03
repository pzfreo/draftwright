# Native section recess merge validation

Merged draftwright main `54704307` (PRs #77–#81 and their prerequisites) into
`cx/native-section-recesses`, retaining both sets of behavior. No push was performed.

## Dependency resolution

`git ls-remote https://github.com/pzfreo/quiddity refs/heads/main` returned
`0fdf5dcbdf4cf675e1b7f690b349069704e77a82`, the recognition/3 merge from PR #14.
Both root dependencies now use that exact `rev`; no other workspace manifest
pins quiddity-rust. The untracked `.cargo/config.toml` override was removed.
Compared with merged main, Cargo.lock changes exactly two source lines, for
`haecceity` and `quiddity`; versions remain `0.1.0-alpha.4`. No registry package changes.

`python3 tools/check_quiddity_pin.py` exited 0 and printed:

```text
draftwright-rust: haecceity: rev 0fdf5dcbdf4cf675e1b7f690b349069704e77a82; quiddity: rev 0fdf5dcbdf4cf675e1b7f690b349069704e77a82
pzfreo/specify-core-rust latest release v0.1.0-alpha.4: haecceity: rev f5ddb726783f2e9384a55164eb7e36a1ec9a6f7c; quiddity: rev f5ddb726783f2e9384a55164eb7e36a1ec9a6f7c
::warning::Kernel note: draftwright-rust uses haecceity: rev 0fdf5dcbdf4cf675e1b7f690b349069704e77a82; quiddity: rev 0fdf5dcbdf4cf675e1b7f690b349069704e77a82; pzfreo/specify-core-rust v0.1.0-alpha.4 uses haecceity: rev f5ddb726783f2e9384a55164eb7e36a1ec9a6f7c; quiddity: rev f5ddb726783f2e9384a55164eb7e36a1ec9a6f7c. Different quiddity-rust pins may change STEP face numbering.
```

## Merge choices

- Keep native recess features, patterns and refusals, plus main’s repeating-radial-profile geometry evidence.
- Combine precision-aware and authored lane demands with recess callout/witness reservation. Keep complete-view base Specs, measured linear corridor composition, recess annotation bounds, established-route refinement and title/table reservation.
- Keep main’s authored linear/aligned/angular placement, central-bore preference, label wording/style protection and explicit leader-side constraints. Preserve native recess radial alternatives and short profile routes.
- Keep all catalogue ports from both branches. Recounted totals: 32 pending, 286 ported, 126 translated, 71 retired.
- Regenerate MCP schemas with `tools/generate_mcp_schemas.py`: unchanged from main, 42,465 compact bytes against the unchanged 42,500 budget.
- Select the lower defect count in each inherited baseline cell, taking metadata from main. The resulting baseline matches main exactly. No observed result was recorded into a baseline.
- Prefer complete measured drawings with readable existing scales and clear new recess corridors. Retain all measurement claims and the original leader-route acceptance rules.
- Keep archived Python 0.3.9 pockets individually annotated; native and Python 0.4 retain unified patterns. The existing tuner pin guards all five occurrence locations and exported callouts.
- Recheck final callout overlaps after unchanged peers are restored following authored-label edits. Bounded slides preserve the exact shaft attachment and physical route gates. Expanded native inventories also repair displaced established witnesses through foreign features.
- Restore the catalogue source/class README table from main; retain the independently recounted merged status table. No test, baseline or threshold was relaxed.

## Planning cost

The merge initially exceeded the unchanged 65-part instruction budget. The repair
keeps the complete placement search and its original rank/tie order. Model and
witness tests use conservative spatial bounds followed by their original exact
predicates. Projected ink, owned feature intersections, standard-font bounds and
identical settled solves are reused with geometry/ownership-aware keys. Refinement
retains each original edge’s identity when comparing a proposed route with the
established route. Ordinary refinement checks candidates lazily in the same stable
score order. Unrepairable off-paper head positions do not generate skew variants.
The final label-overlap repair searches edited labels only. Additional location-witness
repair uses the established drawing’s clear-witness inventory, with that inventory
in the exact solve key, to focus on crossings introduced by recess expansion.

Focused tests compare indexed intersections, parallel runs, label contacts and
edge-preserving route checks with full scans, including thin/boundary leaves,
repacked/scaled views and ownership/endpoint changes. Profiling instrumentation
was temporary and is absent from the final source. The final native and WASM
release profiles retain main’s settings. An initial
ThinLTO full-suite build was interrupted before tests ran because it made test
compilation impractically slow. Additional exact witness preparation removed
that requirement; all final checks below use the original native profile.

## Verification

All Cargo checks use `CARGO_BUILD_JOBS=4 RUST_TEST_THREADS=4`. Corpus checks use
`DRAFTWRIGHT_CORPUS=/Users/paul/repos/quiddity/tests/corpus DRAFTWRIGHT_CORPUS_REQUIRED=1`.
Required checks run sequentially; latency gates retain their normal assertions.

The release build and workspace/all-targets release compilation pass.

Quality: all **300 cells** on 30 parts are no worse than main; **seven cells improve**.
Totals are ink **17 → 9**, line-through-feature **73 → 65**, repeated labels 7,
and zero off-sheet items, repeated measures/spans, view overlaps, title-block overlaps,
hidden-on-visible lines or hidden section lines. The inherited baseline is unchanged.
Evidence: `target/recess-merge-quality-final/baseline.json`.

| Part | Metric | Main | Merged |
| --- | --- | ---: | ---: |
| `10000` | `ink` | 2 | 0 |
| `10020` | `ink` | 1 | 0 |
| `10060` | `ink` | 6 | 3 |
| `nist_ctc_02_asme1_rc` | `line_through_feature` | 6 | 5 |
| `nist_ftc_06_asme1_rd` | `line_through_feature` | 3 | 1 |
| `nist_ftc_08_asme1_rc` | `line_through_feature` | 11 | 6 |
| `nist_ftc_09_asme1_rd` | `ink` | 2 | 0 |

Leader routes: **PASS**, 144 callouts on 30 parts; ten unchanged pre-existing runs.
No new length, hidden/section/cut crossing, parallel-run or extension-contact failure.
Evidence: `target/recess-merge-quality-final/leader-routes.json`.

Plan cost: **PASS**, 3,132,566,556,944 retired instructions on all 65 parts,
below the unchanged 3,165,755,533,285 limit (1.138 of the recorded budget).
Informational wall time: 296.4 seconds. The native release build uses main’s original profile. Evidence: `target/recess-merge-plan-time-verified.json`.

The first complete run exposed four integration failures: archived tuner grouping,
the README class table, a late authored-label overlap and a displaced FTC06 witness.
A later attempt exposed compound-label paper clearance during refinement. All five
regression targets now pass (39 focused tests), and these cases also pass in the
final full suite. The fixes preserve measurement owners, exact attachments, physical
route guards and padded stroke clearance. No test, threshold or baseline was weakened.

### Final check results

| Check | Result |
| --- | --- |
| Kernel pin checker | PASS, exit 0; expected cross-package pin warning, full output above |
| Native release build | PASS, original release profile |
| Formatting | PASS: cargo fmt --all --check |
| Release Clippy | PASS: workspace, all targets, -D warnings |
| Full required-corpus release workspace suite | 1,226 passed; 3 failed; 0 ignored; exit 101 |
| Separate rules catalogue | PASS, 6 tests |
| Focused regression targets | PASS, 39 tests |
| WASM safeguard tests | PASS, 16 tests |
| CI-profile optimized WASM build | PASS, same profile and optimizer settings |
| Reviewed WASM API | PASS, snapshot matches; no binding API change |
| Native WASM reference build | PASS |
| Optimized native/WASM parity | PASS, 4 tests, including authored geometry |
| WASM size | PASS, 14,575,733 raw bytes below 15,700,000 |
| MCP schema validation | PASS, 13 tools / 26 served and published schemas |
| Schema-generator equivalence tests | PASS, 4 tests |
| Stateless HTTP target rerun | PASS, 13 tests, unchanged |

The full-suite failures were:

- `session::turned_benchmark_deltas`: GRM-03 native p90 258.017 ms against 150 ms.
- `session::nist_benchmark_deltas`: CTC01 native p90 308.431 ms against 150 ms.
- `stateless::http_slow_head_has_absolute_deadline_and_serves_queued_ping`:
  one connection reset while reading the response. The entire unchanged stateless
  target passed on rerun, including both slow-header and slow-body deadline tests.

The latency gates remain enabled and unchanged, as requested. They were not
modified or bypassed. The full suite used four test threads; the HTTP rerun used
the same job/thread and required-corpus settings. Detailed logs and command results
are under `target/recess-verification/`, including `results.json` for the initial
sequence and `final-results.json` for corrected-environment retries.

The first WASM build completed Rust compilation and binding generation, then
could not write wasm-pack's tool-cache lock outside the writable workspace.
The build passed with `WASM_PACK_CACHE=/private/tmp/recess-wasm-pack-cache` and
`BINARYEN_CORES=1`, retaining optimization and the original repository settings.
The unoptimized intermediate exceeded the size ceiling; it was not accepted.
API, parity and size checks were repeated on the optimized artifact.
The schema checks initially lacked Python's jsonschema module; both passed using
a temporary environment with the CI-pinned `jsonschema==4.25.1`.

Final WASM transfer sizes: raw 14,575,733; gzip 4,856,281; Brotli 3,240,173 bytes.
Raw headroom is 1,124,267 bytes (7.2%). The native recess Unreleased changelog entry
is present. The binding API matches main, so no additional WASM API note is needed.
MCP schemas remain 42,465 compact bytes against the unchanged 42,500 limit.

All work is committed locally in coherent steps with the requested Codex
co-author trailer. Nothing was pushed. The original untracked
`.recess-rules.json` remains untouched.
