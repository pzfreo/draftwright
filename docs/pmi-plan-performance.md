# PMI plan performance

Measured on macOS arm64, Rust 1.99, from baseline `45a65043` and the
`cx/pmi-plan-perf` branch. Both binaries use the same release options, with
`CARGO_PROFILE_RELEASE_DEBUG=true` set only in the environment. No profile
settings, plan budget, thresholds, candidate order, scoring rules or search
limits were changed. No CI acceptance jobs or budget recording were run.

## Profile

The requested native sampling was attempted on a running release planner:
`sample <pid> 30 -file target/pmi-performance/sample-ftc10-before.txt`.
The sandbox denied process inspection ("sample cannot examine process").
`samply 0.13.1` installed successfully, but recording failed with error 1100.
`xctrace` refused because only Command Line Tools, rather than full Xcode, are
installed. There is no native sampling profile to claim from this environment.

Temporary nested RAII timing spans were therefore used in the CLI, analysis,
recognition, sheet recovery, dimensions, GD&T and layout. A separate executable
linked to the unchanged pinned Quiddity library separated discovery from
fingerprinting; an instrumented temporary source copy separated discovery
families. All instrumentation was removed before committing planner changes.
The retained text logs are in this directory. Inclusive function totals can
include nested invocations and must not be added together.

FTC-10's 114.046 s instrumented CLI run spent 110.388 s in native recognition,
0.700 s in initial STEP analysis and 2.957 s in recovered planning. A separate
run spent 107.656 s discovering features and 0.165 s fingerprinting them.
The family-level probe measured 96.459 s in `slots::discover` out of 96.756 s
in feature discovery. The dominant path is:

```
api::native::plan_part
  model::recognise_native
    quiddity::correspondence::recognise
      features::recognise / inventory_in
        slots::discover / recess_core::slot_proposals
          Scan::candidate / has_void_evidence
            volume_probe::prism_is_empty
              haecceity::volume::common_volume
```

The recognizer asked only whether an inset candidate prism was empty, but
computed its complete common volume, including adaptive integration of mixed
material/air slices. A rejected candidate still paid for a quantitative result
that the caller discarded. The AP242 command's long delay was principally
before sheet layout, rather than in GD&T placement.

CTC-02 has a different profile. Cumulative inclusive times from the broad
instrumented run were:

| Path | Calls | Inclusive seconds |
| --- | ---: | ---: |
| dimension inventory solve | 79 | 28.571 |
| linear clearance, including rotated retries | 236 | 14.712 |
| initial leader placement | 254 | 6.902 |
| leader candidate inventories | 1,803 | 5.534 |
| leader refinement | 169 | 5.365 |
| collision reporting | 426 | 1.657 |
| GD&T search | 218 | 0.790 |

A deeper pass counted 8,830,326 generated witness-stroke trials and 1,358,415
label trials. Repeated recovery and greedy-order trials spend most of their
work evaluating dimensions; the GD&T search is a small part of this case.

## Changes

The recognition fix is in quiddity-rust (pzfreo/quiddity#16, `src/features/volume_probe.rs`,
with its equivalence test `tests/volume_probe.rs`); draftwright pins quiddity and haecceity to that
commit until the next quiddity-rust release. (The first version of this branch vendored quiddity's
sources to patch them; that copy was dropped in favour of the upstream change.)

`prism_is_empty` uses the existing kernel `fills_at_most(..., 0.0)` predicate.
The inset-box arithmetic and minimum probe extent are unchanged. A positive
running material volume can reject a candidate immediately. Successful empty
proofs still require the kernel's complete resolution checks; an unresolved
query still proves nothing. Fraction-valued callers retain the original full
integration. The regression test compares bounded answers with complete
volume calculations on planar and curved fixtures, invalid and thin probes,
empty/occupied regions, and probes crossing material boundaries. Large
translations can round a positive requested span to a zero-volume constructed
box; non-normal or degenerate volumes therefore retain the exact original
fraction calculation. Regression probes also cover collapsed bounds and
volume overflow.

Linear candidate trials now keep their fixed three strokes and two arrows in
inline arrays and reuse one `Placed` scratch buffer for physical predicates.
Only surviving candidates allocate the public vectors needed for label
alternatives or the selected result. Every coordinate operation and candidate
visit remains in its original order. The existing exhaustive peer-contact test
now also verifies the reused buffer's serialized bytes against each original
candidate before comparing collision results.

Witness feature-count queries bind ownership once per measurement set. Their
memo uses exact endpoint bits and a frame-local ownership ID; numeric feature
IDs preserve the original deduplication of repeated footprints. The count pool
is limited to 8,192 entries per projected frame. Changed ownership gets a
separate preparation, and changed frames retain the existing projection-cache
invalidation. A regression compares both fresh and cached counts with complete
uncached scans over circles, boxes, repeated feature IDs, pattern ownership and
value-dependent step-level ownership. Developmental CTC-02 runs measured about
621 billion instructions with inline trials alone and 603 billion with the
ownership-count memo, so this smaller saving is retained.

Experiments caching complete physical witness grades did not improve the
measured cost reliably and were removed. The final witness-cache experiment reused only
536,556 of 8,830,326 trials (6.1%). Moving the existing floor test before the
slot-volume query also failed to improve FTC-10 and was removed. The source
changes retain the demonstrated volume-query, allocation and ownership-count
improvements.

## Reproduction

```
export CARGO_BUILD_JOBS=12 RUST_TEST_THREADS=12
export DRAFTWRIGHT_CORPUS=/Users/paul/repos/quiddity/tests/corpus
export DRAFTWRIGHT_CORPUS_REQUIRED=1 CARGO_PROFILE_RELEASE_DEBUG=true
cargo build --release --locked

python3 -I tools/check_plan_time.py --binary /absolute/path/to/draftwright \
  --corpus "$DRAFTWRIGHT_CORPUS" --out target/plan-time.json

# Capture from the preserved baseline binary, then compare the new binary:
python3 -I tools/check_plan_identity.py --binary /absolute/path/to/base-draftwright \
  --corpus "$DRAFTWRIGHT_CORPUS" --out target/before-identity --record
python3 -I tools/check_plan_identity.py --binary /absolute/path/to/draftwright \
  --corpus "$DRAFTWRIGHT_CORPUS" --out target/after-identity \
  --reference target/before-identity
```

The identity script's `--record` records output bytes, not a performance budget.
Neither instruction survey used `--record`. AP242 inputs were decompressed to
temporary `.stp`/`.step` files by the survey's existing `inputs()` function.

## Measured results

The final after survey ran after the test suite and render capture finished.
The before/after reports contain all 65 per-part retired-instruction counts
and wall times. Instructions fell from **3,183,554,981,045 to 2,250,225,983,533**
(**29.32% fewer**); survey wall time was
243.707 s before and 236.823 s after.
Wall time varied considerably with load during development; instruction counts
are the stable comparison. The performance budget was left unchanged.

- `nist_ftc_10_asme1_ap242-e2`: 115.287 → **4.776 s**, 958,867,375,631 → 82,738,061,141 instructions (91.37% fewer). The 20 s target was **met**.
- `nist_ctc_02_asme1_ap242-e2`: 38.177 → **98.020 s**, 629,884,451,669 → 604,086,550,395 instructions (4.10% fewer). The 8 s target was **not met**.

The ten most expensive inputs before the changes (G = 10⁹ retired instructions):

| Part | Before G | After G | Before s | After s | After rank |
| --- | ---: | ---: | ---: | ---: | ---: |
| `nist_ftc_10_asme1_ap242-e2` | 958.867 | 82.738 | 115.287 | 4.776 | 7 |
| `nist_ctc_02_asme1_ap242-e2` | 629.884 | 604.087 | 38.177 | 98.020 | 1 |
| `nist_ctc_02_asme1_rc` | 237.897 | 232.874 | 10.600 | 23.849 | 2 |
| `nist_stc_09_asme1_ap242-e4` | 193.898 | 189.651 | 11.638 | 14.696 | 3 |
| `cgb203` | 181.993 | 182.008 | 9.726 | 23.014 | 4 |
| `nist_ftc_08_asme1_rc` | 97.076 | 93.200 | 4.799 | 4.655 | 5 |
| `nist_stc_10_asme1_ap242-e2` | 88.185 | 82.414 | 5.406 | 10.063 | 8 |
| `bracket_positions` | 85.773 | 85.830 | 4.475 | 10.463 | 6 |
| `nist_stc_06_asme1_ap242-e3` | 72.987 | 70.699 | 4.822 | 4.836 | 9 |
| `cgb207` | 64.106 | 64.081 | 7.581 | 7.996 | 10 |

The ten most expensive inputs after the changes:

| Part | Before G | After G | Before s | After s |
| --- | ---: | ---: | ---: | ---: |
| `nist_ctc_02_asme1_ap242-e2` | 629.884 | 604.087 | 38.177 | 98.020 |
| `nist_ctc_02_asme1_rc` | 237.897 | 232.874 | 10.600 | 23.849 |
| `nist_stc_09_asme1_ap242-e4` | 193.898 | 189.651 | 11.638 | 14.696 |
| `cgb203` | 181.993 | 182.008 | 9.726 | 23.014 |
| `nist_ftc_08_asme1_rc` | 97.076 | 93.200 | 4.799 | 4.655 |
| `bracket_positions` | 85.773 | 85.830 | 4.475 | 10.463 |
| `nist_ftc_10_asme1_ap242-e2` | 958.867 | 82.738 | 115.287 | 4.776 |
| `nist_stc_10_asme1_ap242-e2` | 88.185 | 82.414 | 5.406 | 10.063 |
| `nist_stc_06_asme1_ap242-e3` | 72.987 | 70.699 | 4.822 | 4.836 |
| `cgb207` | 64.106 | 64.081 | 7.581 | 7.996 |

A sequential paired rerun of CTC-02 measured the original and final binaries
with process CPU accounting, using the same input and instruction-counting
wrapper. Both outputs again matched the original JSON bytes:

| Binary | Instructions G | Wall s | User CPU s | System CPU s |
| --- | ---: | ---: | ---: | ---: |
| Before | 630.100 | 56.305 | 36.800 | 0.147 |
| After | 603.577 | 41.465 | 33.069 | 0.139 |

This supplementary run supports a modest compute improvement, while showing
substantial wall-time variation. It does not replace the official survey above
or meet the CTC-02 target. The [paired measurements](pmi-plan-performance/paired-cpu.json)
retain the unrounded values.

CTC-02 remains above its 8-second target. Its remaining cost is dominated by
complete linear/leader placement and refinement over many recovered sheet
candidates. The small ownership and allocation improvements do not remove
that search. No scoring, search or output tradeoff was taken to meet a time
limit.

## Exact-output evidence

**65 plan JSON files and 65 SVG files are byte-identical**, including
all 22 AP242 plans and SVGs. The script compares file counts, SHA-256 manifests
and raw bytes. Nothing is normalized, filtered or excluded. Input SHA-256
manifests also match, so both captures used identical input bytes.

The original outputs and both binaries remain under `target/pmi-performance/`.
The committed evidence files are:

- [Before instructions/time](pmi-plan-performance/before-time.json) and [after instructions/time](pmi-plan-performance/after-time.json), with all 65 rows.
- [Before identity manifest](pmi-plan-performance/before-identity.json) and [after identity manifest](pmi-plan-performance/after-identity.json).
- [Before provenance](pmi-plan-performance/before-provenance.json) and [after provenance](pmi-plan-performance/after-provenance.json), recording input and binary hashes.
- [Run provenance](pmi-plan-performance/run.json), [check summary](pmi-plan-performance/checks.json), and the retained timing-span logs.

## Checks

With `CARGO_BUILD_JOBS=12`, `RUST_TEST_THREADS=12`, the required corpus path
and `DRAFTWRIGHT_CORPUS_REQUIRED=1`:

- `cargo fmt --all --check`: PASS.
- `cargo clippy --workspace --all-targets --release --locked -- -D warnings`: PASS.
- `cargo test --workspace --release --locked --no-fail-fast`: PASS (1,298 passed, 0 ignored, across 124 test groups, including doc tests).
- Bounded-volume and cached-count equality regressions: PASS in the full suite.
- Full-survey exact JSON/SVG comparison: PASS.
- Instruction survey against the unchanged budget: PASS.
- CI acceptance jobs: not run.

All work was committed locally; nothing was pushed.
