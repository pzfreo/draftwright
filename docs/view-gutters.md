# Default view gutters

Automatic final drawings share spare paper between adjacent annotation bands up
to a preferred maximum of 25 mm, at the selected sheet and scales. A gap of
roughly eight annotation text heights separates sparse views without scattering
the projected set.
Gutters shrink continuously to zero when necessary. Whole rows and columns,
including aligned sections and auxiliary projections, translate together;
explicit `views/<name>/place/gap` stays an authored distance, including zero.
`sheet/view_gutter` overrides the preference (finite, non-negative paper mm);
zero reproduces the drawing without the final spacing pass.

The common counted sheet allocator applies spacing after recovery, measured
annotation packing, detail selection, relative/obstacle placement and multi-sheet
allocation. This covers sessions as well as the library, stateless API and
exports. Enabling the historical `layout::Request::view_gutters` flag would spend
its hard 6 mm floor during sheet selection, so the Python replay profiles retain
that flag and their recorded arithmetic. The new final pass cannot select a
sheet, change a scale, reroute a dimension or drop a view.

The solver translates complete view blocks and their settled annotation ink,
GD&T, line work, balloons and scale captions. It preserves projection alignment,
explicit relative placement groups, pinned views, their anchor chains and pinned
annotation stations. Annotation text and lanes settle in the recovered frame
before this optional final pass. It shares constrained slack before
improving individual pairs, and tries lifting the row above the title block
before sacrificing a gutter. Frames and fixed sheet furniture remain obstacles.
An automatic isometric or section uses its actual geometry, ink and caption
instead of its empty fitting region or generic section padding. Dimension/GD&T
labels and section/detail captions reserve two mm at the frame, title and fixed
furniture; the isometric caption keeps one mm at the frame. Authored placement
footprints retain their literal relations, including zero gaps.

## Initial 12 mm comparison (historical)

Baseline: `9422945b691a798ef97b569a783ab8d51188c3b3` (`origin/main` at start).
The baseline binary was built before editing and retained under
`artifacts/view-gutters/draftwright-main`. Its ten projection-alignment hashes
match the original fixture exactly. The branch binary is retained alongside it, from implementation commit
`500ad83`. Binary SHA-256 values and the environment are recorded in
`artifacts/view-gutters/manifest.json`.

The table reports the minimum signed horizontal/vertical separating strip
between two bands on the same sheet, in paper mm. Both runs normalize the iso to
its actual outline plus caption; other views retain their annotation bands.
Negative means overlapping bands (the pre-existing caption overlap on 10038).
Every sheet/scale, individual view scale and view inventory is checked unchanged
by `tools/check_view_gutters.py`.

| Part | Sheet | Scale | Gap before → after (mm) |
| --- | --- | --- | ---: |
| cadgenbench_inputs/cgb203.step | A4 | 1:5 | 8.50 → 12.00 |
| cadgenbench_inputs/cgb207.step | A3 | 1:2 | 0.00 → 12.00 |
| cadgenbench_inputs/cgb241.step | A3 | 1:5 | 0.00 → 2.13 |
| cadgenbench_inputs/cgb245.step | A2 | 1:2 | 0.00 → 12.00 |
| gramel/GRM-03_thumbwheel_drive_screw.step | A3 | 10:1 | 1.21 → 10.40 |
| gramel/GRM-05_depth_lock_bolt.step | A3 | 5:1 | 0.00 → 5.30 |
| gramel/string_post.step | A4 | 5:1 | 0.00 → 13.30 |
| nist/nist_ctc_01_asme1_rd.stp | A3 | 1:5 | 0.00 → 7.50 |
| nist/nist_ctc_02_asme1_rc.stp | A0 | 1:5 | 0.00 → 12.00 |
| nist/nist_ctc_03_asme1_rc.stp | A3 | 1:5 | 0.00 → 12.00 |
| nist/nist_ctc_04_asme1_rd.stp | A1 | 1:5 | 0.00 → 12.00 |
| nist/nist_ctc_05_asme1_rd.stp | A2 | 1:5 | 0.00 → 12.00 |
| nist/nist_ftc_06_asme1_rd.stp | A1 | 1:2 | 0.00 → 12.00 |
| nist/nist_ftc_07_asme1_rd.stp | A2 | 1:2 | 0.00 → 12.00 |
| nist/nist_ftc_08_asme1_rc.stp | A1 | 1:2 | 0.00 → 12.00 |
| nist/nist_ftc_09_asme1_rd.stp | A1 | 1:1 | 0.00 → 12.00 |
| nist/nist_ftc_10_asme1_rb.stp | A1 | 1:1 | 0.00 → 12.00 |
| ttt_inputs/sm-hanger.step | A3 | 1:2 | 0.00 → 12.00 |
| mfcadpp/1000.step | A4 | 2:1 | 0.00 → 6.26 |
| mfcadpp/10000.step | A4 | 1:1 | 0.00 → 11.42 |
| mfcadpp/10007.step | A3 | 2:1 | 0.00 → 12.00 |
| mfcadpp/10020.step | A3 | 2:1 | 0.00 → 12.00 |
| mfcadpp/10033.step | A3 | 2:1 | 18.00 → 18.00 |
| mfcadpp/10038.step | A4 | 2:1 | -1.22 → 12.00 |
| mfcadpp/1004.step | A4 | 1:1 | 0.00 → 12.00 |
| mfcadpp/10046.step | A3 | 2:1 | 0.00 → 12.00 |
| mfcadpp/10047.step | A3 | 1:1 | 0.00 → 12.00 |
| mfcadpp/10049.step | A4 | 1:1 | 0.00 → 12.00 |
| mfcadpp/10060.step | A4 | 1:1 | 0.00 → 12.00 |
| mfcadpp/10063.step | A4 | 1:1 | 0.00 → 9.96 |

All 30 sheets/scales and all view scales/inventories are unchanged. Twenty-three
parts meet or exceed 12 mm; seven use smaller positive gutters.

## Constrained gutters

| Part | Gap (mm) | Limiting room at the retained sheet and scales |
| --- | ---: | --- |
| cgb241 | 2.13 | The aligned column, side and section annotation blocks share the horizontal row with the iso/note-block obstacles. |
| GRM-03 | 10.40 | The aligned end view reaches the upper frame; the iso and its caption need the lower frame clearance. |
| GRM-05 | 5.30 | Front and plan bands occupy 271.7 mm of the A3 sheet's 277 mm usable height. |
| NIST CTC-01 | 7.50 | The tall plan band reaches the upper frame while the aligned side view must clear the title block below. |
| 1000 | 6.26 | Iso, front and plan share A4's height; the two vertical gutters share the remaining room after caption clearance. |
| 10000 | 11.42 | The front/plan stack reaches the upper frame while the aligned side band clears the title block. |
| 10063 | 9.96 | Column, side and iso fill A4's width, including the iso's frame clearance; two horizontal gutters share the slack. |

The second title-block arrangement restores a full 12 mm on 10046: the row can
lift above the title instead of squeezing the side view against its left edge.

## Render review

Before/after PNGs were rendered and inspected for all eight parts below. The new
spacing separates the view bands without changing geometry scale, leaving the
frame, title block and fixed furniture clear. The high-enlargement GRM-03 and A4
1000 are tight: their caption-to-frame clearance takes precedence over 12 mm.

All paths are under `artifacts/view-gutters/`; each stem has both
`main/<stem>.png` and `branch/<stem>.png`:

- `nist_ctc_01_asme1_rd`: plate with dense dimension bands and radius table.
- `nist_ctc_04_asme1_rd`: large plate, section and long hole schedule.
- `nist_ftc_06_asme1_rd`: bracket with section and isometric.
- `GRM-03_thumbwheel_drive_screw`: turned part at 10:1.
- `string_post`: Gramel turned part with end view and section.
- `sm-hanger`: bent sheet-metal part.
- `1000`: small prismatic part on A4 at 2:1.
- `cgb245`: broad plate with hole schedule.

## Checks and reproduction

All Rust checks use:

```sh
export CARGO_BUILD_JOBS=8 RUST_TEST_THREADS=8
export DRAFTWRIGHT_CORPUS=/Users/paul/repos/quiddity-python/tests/corpus
export DRAFTWRIGHT_CORPUS_REQUIRED=1
cargo fmt --all --check
cargo clippy --workspace --all-targets --release -- -D warnings
cargo test --release --test rendering --test core
python3 tools/check_test_binaries.py
python3 tools/test_mcp_spec_outline.py
```

The initial pass ran only core and rendering; the review follow-up below also
covers dimensions and audits. Five new rendering regressions cover roomy first
and third angle layouts, unchanged sheets and scales, shrinking, explicit gaps,
override validation/round-trip and rigid annotation transport. Existing no-op pin,
editor fragment, detail, relative iso, section and multi-sheet assertions remain
active. Expected-position groups carry a one-line reason: final gutters follow
raw recovery, existing view pins now repeat the reported default gap, and the ten
alignment hashes include final spacing while retaining authored alignments.

```sh
python3 -I tools/quality.py artifacts/view-gutters/draftwright-main artifacts/view-gutters/main $(sed "s|^|$DRAFTWRIGHT_CORPUS/|" tools/quality_parts.txt)
python3 -I tools/quality.py artifacts/view-gutters/draftwright-branch artifacts/view-gutters/branch $(sed "s|^|$DRAFTWRIGHT_CORPUS/|" tools/quality_parts.txt)
python3 tools/check_view_gutters.py artifacts/view-gutters/main artifacts/view-gutters/branch artifacts/view-gutters/comparison.json
python3 tools/check_leader_routes.py artifacts/view-gutters/branch --report artifacts/view-gutters/branch-leaders.json
python3 tools/check_quality_table.py docs/status-2026-10-10.md artifacts/view-gutters/branch/baseline.json
```

| Check | Result |
| --- | --- |
| `cargo fmt --all --check` | PASS |
| `cargo clippy --workspace --all-targets --release -- -D warnings` | PASS |
| `cargo test --release --test core --test rendering` | PASS: core 270/270 (52.79 s), rendering 465/465 (121.55 s) |
| `python3 tools/check_test_binaries.py` | PASS: 8 binaries, 140 reachable Rust files, 1,273 integration tests in source; docs counts updated |
| `python3 tools/test_mcp_spec_outline.py` | PASS: 3 tests; advertised schemas unchanged |
| `tools/quality.py`, main and branch | PASS: all 30 parts; zero off-sheet annotations, view overlaps or views on title block |
| `tools/check_view_gutters.py` | PASS: all sheet/view scales and inventories unchanged |
| `tools/check_leader_routes.py` | PASS: 144 callouts; no longer/new bad routes; 8 unchanged pre-existing runs |
| `tools/check_quality_table.py` | PASS: 450 cells, no part worse than the maintained status table |
| Direct main/branch quality comparison | PASS: all 450 tracked cells equal main |

The final quality totals remain: 7 repeated labels, 6 ink findings, 63 feature
crossings, 1 off-span text, and 18 leader/extension contacts. These are unchanged
from main; gutters do not reroute or remove annotations to hide existing findings.

Raw plan/analysis JSON, SVGs, the 16 PNGs, comparison JSON, binary copies and
check logs remain in the ignored `artifacts/view-gutters/` directory.

## Review follow-up (2026-10-18)

The earlier uncommitted fixes were retained and completed:

- Text-edit references disable optional gutters. Previously, a reference with
  final translations changed authored diagonal lanes before unchanged width ink
  was restored, and exhausted an enlarged corner label's border clearance.
  Edited ink now settles once before gutters. Fixed A4 at 2:1 is also exercised
  with 0, 25 and 1,000 mm requested gutters.
- Two mm around dimension/GD&T labels, section/detail captions and reference text
  counts as occupied paper.
  This protects the fit label's border/title/furniture clearance before spacing.
  Section/detail captions are measured in their rendered annotation font, rather
  than relying on the shared furniture helper's condensed title font.
- View pins are fixed, and item pins resolve the actual coverage owner/carrier.
  Explicit view relations remain rigid, so fixing a view fixes its anchor chain.
  Authored relation anchors keep their literal footprint too; cross-sheet views
  keep reference-label reservations and both ends of their zone addresses fixed.
  Already measured areas survive no-op passes exactly: recalculating an iso box
  from its translated origin had changed one coordinate by an ulp on a bare pin.
- Equal projection origins constrain translations for every projected row/column,
  including sections and auxiliary views. Sections already raised to clear the
  title retain their existing row offset too (string_post). The section no longer
  stays behind when GRM-01's front and side move. Automatic sections relinquish empty generic
  allocation padding after their geometry, ink and captions are measured.
- The default maximum is 25 mm; the existing override remains the preferred
  maximum. The solver shares tight slack before improving individual gaps.

The cgb245 schedule reproducer combines bare dimension pins with an explicitly
changed **zero-gap** plan/front relation. The default now includes an automatic
gutter. These are different anchor intents: resolving zero deliberately moves
plan and its ink relative to front. This follows the documented S3 rule in
[model.md](model.md#placement-place-s3): editing a pin's anchor intent re-resolves
it. Silently substituting the default gap would discard the authored distance.
The original regression assertion is retained; the reported-gap replay and the
literal-zero replay are recorded separately below.

### A4 at 1:1 drawing review

Each render was made with `--spec` using
`{"sheet":{"size":"A4","scale":"1:1"}}`. The comparison also includes
`view_gutter:0`. The before binary was built from the exact starting HEAD,
`0bf7ff40`, in a temporary source export of this repository; the after binary
includes this follow-up. No external repository was copied or vendored. Files are
under the ignored
`artifacts/view-gutters/followup/` directory, with a three-column
`comparisons.html` (disabled / previous 12 mm / final 25 mm). Both SVG and PNG
are retained for every drawing in `before/` and `after/`.

| Part | Front/side gap: previous → final (mm) | Plan/front gap: previous → final (mm) |
| --- | ---: | ---: |
| GRM-01_shank | 6.74 → 24.59 | 12.00 → 25.00 |
| mfcadpp/1004 | 12.00 → 25.00 | 12.34 → 25.34 |
| mfcadpp/10060 | 12.00 → 27.00 | 12.00 → 26.00 |

These numbers use reported view areas; the solver also includes ink extending
past those areas, so an area gap can exceed its 25 mm occupied-band preference.
GRM-01's front/side/section origins were 78/78/90 mm with the previous gutters;
all three are now **71.00001 mm**. The shapes' tops and bottoms are level.
The side/section band gap also grows, from 4.01 to 16.59 mm; the caption's two mm
clearance from the title block limits further horizontal expansion.
All three A4 drawings retain 1:1 and have no `page_fit_uncertain`. The automatic
GRM-01 drawing also retains A3 at 2:1 with clear gutters and an aligned section.
Before/after PNGs for all three parts and the automatic GRM-01 PNG were inspected.

For cgb245, replaying the reported 25 mm gap preserves the schedule and **all six
pinned dimensions byte for byte**. Requesting zero instead preserves the schedule
and front pins, but moves the plan's slot-location arrow from y = 282.3955465 to
257.3955465 mm, exactly 25 mm. The earlier 12 mm build's equivalent movement was
282.3955465 → 270.3955465 mm. Both cases and all six dimension comparisons are in
`cgb245-{reported-gap,zero-gap}.{spec,plan,check}.json`. The unchanged regression
compares these distinct anchor intents as if they requested the same position;
its failure is reported explicitly rather than changing the test or overriding
zero.

### Sheet/scale and section checks

All **30 sheet/scale choices and all view scales/inventories are identical to the
exact starting HEAD, 0bf7ff40**. The complete before/after gap table is retained in
`followup/comparison-merged-head.md` and `.json`. Eighteen parts meet/exceed the
25 mm preference; twelve shrink it to keep real clearances. A few tight gaps are
up to one mm smaller than the old 12 mm build because measured label clearance
now takes priority.

The historical table above predates the reviewer merge. Its 10047 entry says A3
at 1:1, but an independent build of **0bf7ff40 already chooses A4 at 1:1**. This
round retains that choice; it does not introduce that historical discrepancy.

All seven quality parts with sections were also compared with gutters disabled:
**13 row/column coordinates or deliberate raised-section offsets are preserved**,
including string_post's title-clearing offset. The report is
`followup/section-alignment-verified.json`. GRM-01 was checked separately at both
its automatic A3 2:1 and requested A4 1:1. The unit regression also exercises an
auxiliary projection in a shared column and both level and raised sections.

### Final checks

Rust verification used the corpus-required environment shown above, with eight
build jobs and eight Rust test threads. The final source was held unchanged during
verification. `followup/manifest.json` records the exact baseline/implementation
commits, binary SHA-256 values and environment. Reproduction:

```sh
cargo fmt --all --check
cargo clippy --workspace --all-targets --release -- -D warnings
cargo test --release --test core --test rendering --test dimensions --test audits --no-fail-fast
python3 tools/check_test_binaries.py
python3 tools/test_mcp_spec_outline.py
python3 -I tools/quality.py artifacts/view-gutters/followup/draftwright-after artifacts/view-gutters/followup/quality-final-state $(sed "s|^|$DRAFTWRIGHT_CORPUS/|" tools/quality_parts.txt)
python3 tools/check_view_gutters.py artifacts/view-gutters/followup/head-plans artifacts/view-gutters/followup/quality-final-state artifacts/view-gutters/followup/comparison-merged-head.json
python3 -I tools/check_quality_table.py docs/status-2026-10-10.md artifacts/view-gutters/followup/quality-final-state/baseline.json
python3 -I tools/check_leader_routes.py artifacts/view-gutters/followup/quality-final-state --report artifacts/view-gutters/followup/leaders-final-state.json
```

| Check | Final result |
| --- | --- |
| `cargo fmt --all --check` | PASS |
| Strict workspace/all-target release Clippy | PASS (37.10 s) |
| `core` | PASS: 277/277 (60.54 s), including engine-version and fresh-process/session equality |
| `audits` | PASS: 158/158 (109.42 s) |
| `rendering` | PASS: 466/466 (108.09 s), including no-op pins, authored relations, narrow cross-sheet captions and reciprocal zone addresses |
| `dimensions` | 321/322 (64.67 s); only `planned_hole_tables::the_cgb245_view_pin_reproducer_keeps_its_schedule` fails, for the explicit-zero anchor change explained above |
| Three reported angle/diagonal/fit-label regressions | PASS unchanged |
| Fixed A4 2:1 authored corner, gutter 0 / 25 / 1,000 | PASS |
| Source inventory | PASS: 8 binaries, 140 reachable Rust files, 1,282 integration tests; docs counts match |
| MCP Spec outline/schema guards | PASS: 3 tests |
| `tools/quality.py` and maintained table checker | PASS: all 30 parts, all 450 cells no worse |
| Leader checker | PASS: 144 callouts; no new/longer bad routes; 8 unchanged pre-existing runs |
| Exact starting-HEAD sheet/scale comparison | PASS: all 30 choices and every view scale/inventory unchanged |
| Section row/column check | PASS: all 7 corpus section parts, 13 coordinates/offsets; GRM-01 also checked at A3 2:1 and A4 1:1 |

The final tracked quality totals are unchanged: zero off-sheet annotations, zero
view overlaps, zero views on the title block, 7 repeated labels, 6 ink findings,
63 feature crossings, 1 off-span text and 18 leader/extension contacts. The final
four-binary log is `followup/final-state.log`; quality/checker logs use the
`final-state` suffix. No original test was removed, ignored or weakened. The
legacy alignment hashes were re-recorded for the requested 25 mm default and
clearance changes; authored alignment/gap assertions remain unchanged.

All work was committed locally in coherent steps. Nothing was pushed, no foreign
repository was copied or vendored, and `CHANGELOG.md` was not edited.

## Quality-pins replay repair (2026-10-18)

The original `1-1` acceptance cell reproduced FTC-09's **0 → 12 ink findings**.
The final gutter solver kept views clear of a table's literal cells but consumed
the **2 mm clearance** required by `tables::place`. FTC-09's isometric ended
0.00001 mm from its hole schedule. Pin replay retained those canonical view
frames, could no longer install the schedule, and restored crowded individual
hole dimensions.

The gutter solver now reserves `tables::PAD` around hole schedule blocks,
authored tables and the radius table. FTC-09 retains 2.00001 mm of clearance.
The regression pins the failing cell's step-height dimension and checks that
the schedule retains its rows and coverage, every dimension keeps its exact
placed geometry, and no annotation ink overlap appears. The acceptance runner,
its pin selection and thresholds are unchanged.

The first eight-cell run then exposed 10060 in cell `3-8`: mixing bare item
pins with view pins restacked its unchanged width dimension above the front
view. Its anchor area grew 8.55 mm, so replaying the reported plan/front gap
pushed plan off-sheet. Bare pins now retain settled peers when view relations
are present too; existing anchor-delta transport carries those peers with
their views. A second regression checks this mixed pin replay with gutters
disabled and at the 25 mm default.

Validation uses eight build/test threads and the required corpus environment
above. `cargo fmt --all --check`, strict workspace/all-target release Clippy,
and the source inventory guard pass. The four requested release binaries pass:
**core 279, rendering 468, dimensions 332, audits 158** (1,237 tests, no failures
or skips). The complete integration source inventory is **1,296** tests across
eight binaries; `docs/test-binaries.md` reflects the current counts.

Each cell ran independently with `python3 -I tools/ci_acceptance.py --job
quality-pins --pin-cell CELL --out target/acceptance-final/CELL`. All eight
reports use the same final release binary and identical fixture hashes.

| Cell | Complete job | Total ink, unpinned → pinned | Off-sheet, unpinned → pinned |
| --- | --- | ---: | ---: |
| `1-1` | PASS | 6 → 6 | 0 → 0 |
| `1-3` | PASS | 6 → 6 | 0 → 0 |
| `2-1` | PASS | 6 → 6 | 0 → 0 |
| `2-3` | PASS | 6 → 6 | 0 → 0 |
| `2-8` | PASS | 6 → 6 | 0 → 0 |
| `3-1` | PASS | 6 → 6 | 0 → 0 |
| `3-3` | PASS | 6 → 6 | 0 → 0 |
| `3-8` | PASS | 6 → 6 | 0 → 0 |

FTC-09 is **0 → 0 ink in every cell**. All 30 parts in every cell pass, as do
the fresh baseline, leader-route, label-lane-locality and deliberate-rejection
checks. Raw JSON, plans, SVGs and logs remain under `target/acceptance-final/`.
The final Rust logs are `target/final-{required-binaries,strict-clippy,fmt-check}.log`.
No original test or acceptance threshold was weakened. Nothing was pushed, and
`CHANGELOG.md` was not edited.
