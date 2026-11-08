# Projection alignment (#109)

`views/<name>/place/align: "projection"` aligns geometry origins across `side`
(x for above/below, y for left/right) and sets the clear gap between the two
areas, including dimension bands. The accepted pairs are front/plan and
front/side, in either direction and either projection angle. Conventional sides
still apply. `item_options` lists it first for a related anchor and excludes it
for other pairs; refusal messages and choices name start/middle/end/outside.
Annotation `along` slots do not accept projection. Explicit projection relations preserve
the principal group during packing, including an automatic partner of a moved front.

Existing alignment branches, defaults and Specs without placement keep their
behavior. Ten whole-Plan SHA-256 baselines were captured before implementation
on `9e4b13e8` (origin/main), for no placement and all four legacy alignments,
with and without sheet controls. The regression compares serialized bytes.

## Reproduction

The fixture is `tests/fixtures/samples/editor-plate.step`, byte-identical to
`tests/fixtures/parts/mounting_plate.step` (SHA-256
`0336b397faae8d94757b1fb0c4a1eca152428559298c6da66c9134851667781e`).
No editor checkout is needed. The placement tests now also use local fixtures
instead of their former editor-checkout fallback.

At A3, 1:1, pin `d:f:hole_pattern:f6/bore.diameter` with
`{"view":"plan","side":"right","lane":4,"along":"middle","pinned":true}`.
This extends only the plan's right band. Pin the plan above front at gap 15:

| Alignment | Front x | Plan x | Plan area x bounds | Clear gap |
| --- | ---: | ---: | --- | ---: |
| `end`, before implementation | 80 | -116.965 | [-186.965, 150] | 15 |
| `projection`, after implementation | 80 | 80 | [10, 346.965] | 15 |

The after plan has `at=[80,181.2]`, area `[10,133.2,346.965,238.33]`;
front has `at=[80,108.5]`, area `[10,83.5,150,118.2]`. The measured floating-point
gap is 14.999999999999986 mm and the x difference is exactly zero.

A separate fixture preserves the issue's reported coordinates. In the sheet
area resolver, start/middle/end/outside retain lateral shifts of
+0.95/-40.9005/-82.751/-82.751 mm; projection gives zero. The main placement
pass's historical middle special case is also retained.

Regressions cover gaps 0/5/15/30 mm, both angles, both anchor directions,
and sheet controls on/off. A side depth dimension pinned above in lane 4 creates
the corresponding asymmetric side band. The exact-coordinate placement test
also covers gap 1000 and an unpinned projection relation obstructed by ink.

## Schema measurements

| Compact JSON | Before | After | Change |
| --- | ---: | ---: | ---: |
| Advertised input/output schemas (28 documents, 14 tools) | 34,477 | 34,477 | 0 |
| Complete tools/list tools array | 42,526 | 42,714 | +188 |
| On-demand Spec schema | 11,999 | 12,362 | +363 |
| Private validation schemas | 26,503 | 26,503 | 0 |

Only `item_options.description` changes in the advertised tools array.
The new enum and field descriptions are in the on-demand Spec; SCHEMA_BUDGET
and its exact-byte test remain at 34,477. The formatted Spec file grows from
28,605 to 29,005 bytes. Regenerated advertised and private schemas are identical.

## Verification

Checks use `CARGO_BUILD_JOBS=8`, `RUST_TEST_THREADS=8`,
`DRAFTWRIGHT_CORPUS=/Users/paul/repos/quiddity/tests/corpus`, and
`DRAFTWRIGHT_CORPUS_REQUIRED=1`, and run sequentially.

The first affected-binary run passed 255 library and 270 core tests; rendering
passed 450 tests and exposed one new side regression. Its gap and side-origin
assertions passed, but packing moved the automatic plan partner off front's
projection line after front moved relative to side. Preserving the principal
group for explicit projection relations fixed that failure. The assertion was
retained, and the subsequent projection selection passed all 26 tests across
the same three binaries, including the ten legacy Plan hashes.

The source and compiled-inventory guards agree on eight integration binaries,
139 reachable Rust files, and 1,256 integration tests. The current per-target
counts are recorded in [test-binaries.md](test-binaries.md). Formatting and
workspace/all-target release Clippy with warnings denied both pass after the fix.

| Check | Result |
| --- | --- |
| `cargo fmt --all --check` | PASS |
| `cargo clippy --workspace --all-targets --release -- -D warnings` | PASS |
| Affected unit/core/rendering tests in final full run | PASS: 255 + 270 + 451 |
| Projection-focused post-fix run | PASS: 26 |
| Source and compiled test inventory | PASS: 1,256 integration tests |
| `cargo nextest run --release --workspace --no-fail-fast --test-threads 8` (once) | 1,510 passed, 1 failed, 0 skipped; 1,127.272 s |
| `cargo test --release --workspace --doc` | PASS: 0 doc-tests in both crates |
| `python3 tools/ci_acceptance.py --job mcp --out target/projection-align-acceptance` | PASS: 7/7 tasks; all prerequisites; 35.444 s |

The full run's sole failure was the existing `session::nist_benchmark_deltas`
150 ms gate: p90 **191.409 ms**. An unchanged isolated rerun failed at
**624.492 ms**. Both runs kept the native timing assertion enabled. The full
suite was not rerun, and these failures are not counted as passes.

An unchanged-main control in a temporary detached worktree of this same
repository (`9e4b13e8`) also failed the identical gate at p90 **837.610 ms**, with
the same environment and assertion. Thus the latency-gate failure is also
present before this feature. No threshold, assertion, baseline or environment
waiver was changed. One attempted paired run reused the baseline executable
through the shared Cargo target cache (p90 775.916 ms); it is excluded as a
feature measurement. The cache mismatch was detected by binary hash and the
absence of the new MCP guidance, then a feature-source rebuild was forced.

After forcing that rebuild from the feature checkout, the identical standalone
NIST benchmark **passed at p90 70.369 ms**, with the 150 ms assertion still
enabled and all nine edits/replay checks retained. The rebuilt release binary
was verified against the MCP-accepted feature build, and its tools/list matches
the recorded feature response exactly. The temporary baseline worktree was
removed. The passing isolated result does not replace the failed full-run result.
