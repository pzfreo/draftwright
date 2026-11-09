# Dimensioning schemes: validation and drawing review

Issue #76 item 4. Reproduce the six PNG/SVG examples with:

```sh
source "$HOME/.cargo/env"
export CARGO_BUILD_JOBS=4 RUST_TEST_THREADS=4
cargo run --release --example dimension_schemes -- target/dimension-schemes
```

The public Spec vocabulary and edit paths are documented in [model.md](model.md#dimensioning-schemes-76-item-4).

## Decisions

- Fitting chain links share one dimension line, end to end. The common geometric lane
  allocator handles authored and automatic chains, including body-local turned chains.
  Overlapping baselines remain nested; links too short for their printed text retain a
  separate lane. The rule uses the actual scale and measured glyph width.

- `from` defaults to `min`, along the named part-frame axis. The empty map preserves the
  automatic decision, including the inspection-end baseline on turned parts.
- Ordinate means a signed running coordinate, with an origin ring and one endpoint arrow.
  The ring is recognised from its actual closed ink by geometric fidelity lint; the
  independent scheme check also verifies the signed nominal against the named references.
- Named networks are checked together. They may meet at a datum, but cannot close a chain.
  Automatic controls survive only while adding an independent interval.
- A pattern expands to every physical member, including every interval of counted pitches.
  Grouped turned lengths likewise expand every shoulder. Partial counted-group replacements
  are refused with a remedy instead of losing the unselected intervals.
  Coincident stations share one witness, chosen nearest the ordinary exterior strip.
- Existing dimension placement/tolerance/precision edits apply to generated owner IDs.
  Conflicting intent on a retired ID is refused. #78 dimensions and semantic PMI cannot
  close the same network. Authored-only mode still requires listing generated owners.
- Explicit schemes take precedence over coordinate schedules in their carrying view;
  full-part table rescue cannot reintroduce duplicate coordinate owners. Other views
  retain normal per-view scheduling.
- Ambiguous multi-solid networks are refused: select one part before authoring its scheme.
  Circular patterns retain PCD; individual member targets can request Cartesian locations.
  Feature IDs and edits on generated feature-based owners from a different bound revision
  are refused until replaced with fingerprint
  references or explicitly rebound. Fingerprint-only entries resolve after reload.
- Endpoint attachment provenance uses `scheme_witness.<name>.<hash>` roles to retain the
  existing feature witness routing. Those roles state no manufacturing size or location;
  coverage comes only from independently measured printed links and their connectivity.

## Focused tests

The original 12 tests in `tests/dimension_schemes.rs` cover all three schemes; high-end and fingerprint datums;
unchanged defaults after removal; complete hole/slot pattern expansion; turned shoulders;
composition at shared datums, partial counted-pitch refusal, repeated turned shoulders;
unknown references, incompatible axes/views, overlapping schemes, duplicate targets across
views, stale generated edits and conflicting edits;
saved Spec/Analysis and embedded SVG round trips; precision, tolerance and placement;
authored-only selection; session preview/refusal/undo/redo; and wrong, missing or duplicated
printed evidence. The WASM replay fixture exercises chain, high-end baseline preview,
negative ordinates, refreshed fragment placement options, undo/redo and removal against
the same native replay. The unchanged lint independence guard also checks the new rule module.

## Previous MCP budget (before #80/#81 merge)

The 26 generated schemas total **42,340 bytes** in compact served JSON, compared with
42,124 on the starting main: **216 additional bytes**, **160 bytes remaining** under the
unchanged 42,500-byte ceiling. The new map shares the existing sparse per-ID object shape
and the generator's equivalent `patternProperties` grouping. Complete entries are strictly
validated by Rust, as other sparse Spec maps are. No budget or test threshold was raised. The existing 4,096-byte MCP response tests
also pass: global scheme discovery is offered once on sheet options, matching authored
dimensions, rather than repeated on every dimension answer. All 13 tools and 26 published/served
schemas pass the independent draft-2020-12 validator, including scheme round-trip and refusal cases.

## Previous render review

All paths below are relative to the repository root. Each PNG has matching `.svg`,
`.plan.json`, `.spec.json` and `.lint.json` evidence beside it.

| Example | Before PNG | After PNG |
| --- | --- | --- |
| Mounting plate chain | `target/dimension-schemes/holes-chain-before.png` | `target/dimension-schemes/holes-chain-after.png` |
| Turned high-end baseline | `target/dimension-schemes/turned-baseline-before.png` | `target/dimension-schemes/turned-baseline-after.png` |
| Pattern ordinate | `target/dimension-schemes/pattern-ordinate-before.png` | `target/dimension-schemes/pattern-ordinate-after.png` |

The turned example replaces the controlling chain/reference member with the 30 and 70
baseline from the high end. It retains A3 at 2:1. The ordinate example shows the three
member coordinates in place of the counted pitch, with the unselected pattern retaining its
automatic presentation, on A4 at 1:1. The chain connects adjacent stations, rather than
repeating distances from the end: 20, 30, 30 and 15, including both intervals of the counted
grid. Its extra strips cause the ordinary fill/layout policy to select A3 at 2:1 instead of
A4 at 1:1; no large paper or second sheet is needed. The mounting plate has the same one
`leader_centerline_overlap` warning on `d:f:hole:f9/bore.diameter` before and after; it is
still reported. The turned and ordinate pairs have zero findings before and after.
All six PNGs were opened and visually reviewed after the final production build. Labels,
arrow directions, origin rings, physical member witnesses and surviving automatic owners
are legible and consistent with the intended measurements.

## Previous required checks

Raw local logs are `/tmp/scheme-*.log`; generated check evidence is under
`target/dimension-scheme-*`. Implementation commit: `e8fec5d`.

- `cargo fmt --all --check`: pass.
- `cargo clippy --workspace --all-targets --release -- -D warnings`: pass.
- Corpus-required `cargo test --release --no-fail-fast`, with four build jobs and four test
  threads: **1,164 passed, 1 failed, 0 ignored**, across 116 targets. The sole final failure is
  `r5_multi_sheet_latency`: MCP p90 **269.196 ms** versus **150 ms**. Its native p90 is
  61.978 ms and both instruction gates pass (native median 1,148,733,714; MCP median
  1,804,789,093). This is a wall-clock failure; the gate is unchanged. In the previous
  complete run, NIST session edit p90 was 397.386 ms versus 150 ms; that target passes in
  the final run. No test, budget or known-defect entry was weakened.
- `tools/quality.py` on all 30 manifest parts: pass. The `ci_acceptance.py --check-results
  quality` comparison against `tools/ci-quality-baseline.json` passes with **all 300 cells
  identical**, including zero off-sheet items, repeated measurements/spans and view overlaps.
  Existing ink contacts (17), repeated labels (7) and line-through-feature contacts (73) are
  unchanged. Evidence: `target/dimension-scheme-quality/baseline.json`.
- `python3 -I tools/check_leader_routes.py target/dimension-scheme-quality`: pass, 119
  callouts on 30 parts; no new crossing or excessive run; 14 pre-existing long runs unchanged.
- `DRAFTWRIGHT_CORPUS=… python3 -I tools/check_plan_time.py --out
  target/dimension-scheme-plan-time.json`: **pass**, all 65 parts counted;
  **2,620,808,288,959 instructions**, below the unchanged **3,165,755,533,285** limit
  (0.952 of the recorded baseline, wall 191.9 seconds). No budget increase.
- `wasm-pack build crates/draftwright-wasm --target web --profile wasm-release --locked`:
  pass. The initial helper-cache installation hit the filesystem sandbox; retry used an
  unchanged copy of the existing helpers under `WASM_PACK_CACHE=/tmp/dimension-scheme-wasm-pack-cache`.
  No manifest, compiler profile or optimisation setting changed.
- `python3 tools/check_wasm_api.py`: pass; reviewed binding signatures are unchanged.
  The additive JSON vocabulary and fragment-option refresh have an Unreleased WASM API entry.
- `cargo build --locked --release --example wasm_reference` and
  `node --test tools/wasm-parity.test.mjs`: **5/5 pass**. Scheme replay has 7 states,
  19 byte comparisons and 2 preview document comparisons, plus explicit two-station,
  provenance, negative-ordinate/arrow and restored-automatic assertions. All existing parity
  assertions are retained.
- The new replay uses two analytic feature stations on the existing prismatic parity part.
  The initial scattered-hole replay exposed platform-dependent last-bit differences in
  pre-existing sampled principal-profile curves: only `analysis_sha256` differed in the
  default Plan, before any scheme edit. No geometry, plan-hash or default algorithm was
  changed to mask that difference, and no comparison was relaxed. Native scheme tests retain
  scattered holes, whole view axes, hole/slot patterns and turned profiles.
- `python3 tools/wasm_size.py crates/draftwright-wasm/pkg/draftwright_wasm_bg.wasm`:
  pass, raw **14,028,083 bytes** under the unchanged 15,700,000 ceiling (1,671,917 bytes
  headroom); gzip 4,644,803, Brotli 3,103,287.



## Chain correction and merge of #80 / #81 (2026-10-17)

Merged `origin/main` at `54704307` after reading its recognition-correction, label-text
and schema-compaction changes. The merge keeps correction binding before planning,
geometry-authored dimensions, scheme ownership, label wording/styles, annotation wording,
fragment placement-option refresh and all independent live-value checks. Revision-sensitive
scheme references are checked before recognition binding can refresh the saved binding;
fingerprint-only generated owner edits remain eligible for anchored correction rebinding.
Text/style edits on a retiring automatic owner are refused with the same remedy as its
other explicit dimension intent.

The chain fix groups connected, nonoverlapping links in the ordinary lane allocator and
body-local turned placement, using actual scale and printed glyph widths. Overlapping
baseline spans and links that cannot fit their labels between arrows keep separate lanes.
The authored regression proves the 20 / 30 / 30 / 15 chain is collinear at 1:1 and 2:1.
The automatic regression covers horizontal/vertical chains, reversed ends, a bridging link,
overlapping baselines and a genuinely short link. Two merge regressions cover recognition
splitting with generated owner text/basic style, saved replay and damaged printed evidence,
and refusal of stale feature-ranked schemes beside anchored corrections.

### Schema bytes and validation

All JSON was regenerated with `tools/generate_mcp_schemas.py`; no JSON conflict was
hand-merged. Main's iterative compaction loop, root-local definitions, exact name/newline
matching, declaration constraints and typed label/authored fields remain intact. Adding a
separate sparse scheme property under that compactor costs 648 bytes (43,113 total).
Sharing the existing object-map constraint saves 432 bytes, producing **42,681 bytes**.
The only differences from main's 42,465 bytes are **18 bytes in each of 12 schemas** for
the literal `dimension_schemes` alternative: **216 additional bytes**. With 35 bytes of
old headroom, the ceiling increases by **181 bytes**, to exactly **42,681**, inclusive.
No validation constraint is weakened. Independent checks also reject malformed scheme
maps/entries and newline-suffixed field names. All 26 files reproduce byte for byte when
the generator runs in a temporary output directory. Per-schema attribution is saved in
`target/dimension-scheme-merge-validation/schema-sizes.json`.

### Regenerated drawing review

All six PNGs below were opened after the merged production build; their matching SVG,
Plan, Spec and independent lint reports were regenerated beside them.

| Example | Before PNG | After PNG | Result |
| --- | --- | --- | --- |
| Mounting plate chain | `target/dimension-schemes/holes-chain-before.png` | `target/dimension-schemes/holes-chain-after.png` | The 20 / 30 / 30 / 15 links share one line and extension stations; all eight arrow tips have page Y = 281.87 mm. Automatic paper selection retains A3 2:1 after, A4 1:1 before. |
| Turned high-end baseline | `target/dimension-schemes/turned-baseline-before.png` | `target/dimension-schemes/turned-baseline-after.png` | Clear 30 and 70 high-end baselines, A3 2:1 in both. |
| Pattern ordinate | `target/dimension-schemes/pattern-ordinate-before.png` | `target/dimension-schemes/pattern-ordinate-after.png` | Clear origin rings and running 20 / 30 / 40 coordinates; unselected pattern retained, A4 1:1 in both. |

The mounting plate retains the one pre-existing `leader_centerline_overlap` warning
before and after. The turned and ordinate pairs have no independent lint findings.
The chosen drawing uses one common chain line while retaining ordinary sheet/scale
selection; no example-specific placement or paper override was added.

### Merge verification

Checks ran one at a time with `CARGO_BUILD_JOBS=4 RUST_TEST_THREADS=4`. Logs and the
command/exit/time ledger are in `target/dimension-scheme-merge-validation/`.

| Check | Result |
| --- | --- |
| `cargo fmt --all --check` | Passed. |
| `cargo clippy --workspace --all-targets --release -- -D warnings` | Passed. |
| Locked release build and `uv run --with jsonschema python tools/check_mcp_schemas.py` | Passed: all 13 tools' inputs, outputs and refusals; 26 published/served schemas agree. |
| Locked WASM web build with `--profile wasm-release` | Passed with the existing helper cache under `/tmp`. |
| Locked native reference build and Node parity | All five tests passed. Prismatic: 18 states / 52 byte comparisons; polygonal: nine / 25; schemes: ten / 28; plus authored geometry and invalid STEP checks. Preview documents retain structured checks; every committed Spec/Plan/SVG byte check is retained. |
| `python3 tools/check_wasm_api.py --base-ref origin/main` | Matches the reviewed snapshot. |
| Scheme and automatic-chain regressions | All 15 integration tests and the automatic horizontal/vertical unit test passed. |
| Every test target found by `ls tests \| grep -i 'label\|recogn\|authored'` | All 72 tests passed across eight targets: authored dimensions, fit-label layout, label lanes, label rows, label text, PMI labels, recognition corrections and repeated labels. |
| Thirty-part quality comparison | Passed: all 300 cells exactly match `tools/ci-quality-baseline.json`. No baseline or known-defect entry changed. |
| Leader routes | Passed: 119 callouts on 30 parts, no new crossings or excessive runs; 14 pre-existing long runs unchanged. |
| Sixty-five-part planning instruction budget | Passed: **2,627,135,421,883 instructions**, below the unchanged **3,165,755,533,285** limit (0.954 of recorded baseline); wall 182.8 s. |

Main appends recognition edits/history to every WASM replay. The initial parity run exposed
an assertion that assumed the scheme fixture still ended the combined replay. The correction
keeps every original scheme assertion scoped to its fixture, checks that restored automatic
ownership persists through every appended correction state, and retains every byte comparison.
The initial failure remains in `wasm-parity-before-replay-fix.log`; the final run is
`wasm-parity.log`.

The full workspace suite completed with the required corpus:

```sh
export CARGO_BUILD_JOBS=4 RUST_TEST_THREADS=4
DRAFTWRIGHT_CORPUS=/Users/paul/repos/quiddity/tests/corpus \
DRAFTWRIGHT_CORPUS_REQUIRED=1 \
cargo test --workspace --release --no-fail-fast
```

Result: **1,208 passed, one failed, zero ignored** across **120 test/doc-test harnesses**,
including all **114 integration targets**. Both doc-test harnesses completed. Wall time
including compilation was 2,050.636 seconds. The sole failure was
`session::nist_benchmark_deltas`: native p90 **229.095 ms**, above the unchanged **150 ms**
limit. The nine sorted samples were 20.992, 23.014, 25.568, 26.743, 27.258, 27.487,
44.015, 51.111 and 229.095 ms. The slow sample changed the envelope width's carrying view
to `plan`. No latency gate, timing environment override or instruction budget was changed;
the latency test was not rewritten or rerun to replace this result. Plate, turned and
multi-sheet latency tests passed, as did all stateless HTTP tests. The complete log is
`full-suite.log`, and counts are saved in `full-suite-summary.json` beside it.

The four Python schema-compactor tests also pass. No schema constraint, quality baseline,
known-defect list or non-schema byte/time budget was weakened. Nothing was pushed.

The final WASM size gate passes: **14,295,787 raw bytes**, below the unchanged
**15,700,000-byte** ceiling (1,404,213 bytes headroom); gzip 4,741,024 and Brotli
3,164,380 bytes. Evidence is in `wasm-size.log`.
