# Derived PMI geometry, 2026-10-18

Draftwright side of [#99](https://github.com/pzfreo/draftwright/issues/99).
Both kernel dependencies consume merge commit
`6ab5f29df418a06ee26e86ce1f7b010b653e1ade` through Cargo. No kernel checkout was
edited or vendored. Return the pins to a tag at the next quiddity release.
The unchanged release-pin comparison reports the difference from specify-core-rust's
published pin as a warning; `artifacts/derived-dw/release-pin.log` retains it.

The consumer follows the kernel's `Feature::Derived.geometry` links into
`PartPmi.derived_geometry` (PMI document format 2, reader `haecceity-pmi-read/7`).
It keeps the axis/plane, optional cylinder radius, and deriving-face support in the
Analysis, transforming local coordinates with the part occurrence. A selected part
keeps its own coordinates. Supplemental points never select drawing witnesses.
Missing geometry and missing size radii remain refused with their reasons retained.

Plane pairs use normal separation; axis–plane pairs use perpendicular separation;
axis pairs use their closest points. Infinite-target witness stations are chosen near
the deriving faces. The existing projection and nominal-agreement checks still apply.
Tolerance frames use their resolved target's direction and a site on that target.
A complete chain of derived plane-to-plane profile lengths takes precedence over automatic
cumulative shoulder locations. A partial source chain stands beside the automatic baseline
rather than claiming a hidden local length. This keeps source dimensions on the sheet.
Authored nominal values, tolerance bands and MAX/MIN/AVG qualifiers survive lowering
and ownership by recognised feature dimensions. Lint independently compares drawn
requirements with the retained target geometry and checks the qualifier.

For the unattended drawing choice, resolved semantic geometry takes precedence over
supplemental presentation points, while unsupported face-only bore witnesses keep their
existing refusal. No baseline or acceptance threshold is relaxed.

The unchanged GRM-03 fixture contains ten semantic nominal dimensions and Maximum on
#914; it contains no per-dimension semantic deviations or limit pairs. Separate planted
band regressions verify tolerance preservation on both absorbed and standalone dimensions.
The original fixture's decompressed SHA-256 is
`4b6462b9cc9f0d419250933bd77fb305f9cfebb7ec2b3f377008732876010a21`.
The new fixture has independent kernel-contract regressions rather than a Python
comparison capture; every existing Python comparison fixture remains required. The
full repeated-span and planning-cost survey inventories expand from 43 + 22 to
43 + 23 fixtures. The planning instruction budget and its margin are unchanged.

## Drawing and plan evidence

Before PNGs and complete plans for all 23 PMI fixtures are retained in
`artifacts/derived-dw/`. The baseline executable was built from the initial source and
alpha.6 tag dependency before implementation. The two before sheets are GRM-03 A4,
5:1, and bracket_positions A2, 1:1.

GRM-03 changes from **0/10 semantic dimensions drawn (10 refused) to 10/10 drawn
(0 refused or dropped)**: diameters 4, 6, 10 MAX, 5 and 1.6 mm, and lengths
3.2, 0.5, 2, 3 and 20 mm. Recognised diameters retain their callouts and source claims;
the complete PMI length chain replaces the automatic cumulative shoulder locations.
The drawing has 14 dimensions instead of 13, with no duplicate geometric annotation for
any of the ten semantic sizes. Both sheets are A4 at 5:1. Inspection confirms readable
labels, short-length outside arrows and witnesses on the intended shoulders/cylinders.
The initial after raster exposed a datum-placement regression (3 before, 7 after datum-leader
crossing warnings). The review correction below replaces that result; the original evidence
remains available and no warning or baseline was hidden.

Bracket_positions is unchanged: the five location records #31343, #31359, #31375,
#31401 and #31417 still have no face-supported plane-to-bore axis witness. These are
direct face targets, not resolved derived aspects, so the kernel adds no axis/plane target
for them. Their bounding-box centre differences include unrelated transverse and axial
offsets; those centres cannot truthfully replace the missing witnesses. They remain
refused, with the existing reason. The complete plan, apart from its Analysis hash,
is identical; the before/after PNG raster is identical, on A2 at 1:1.

All **38** committed STEP fixtures carrying semantic dimensions/tolerances were compared:
the 23 PMI fixtures and 15 other committed fixtures (`extra-fixtures.json`). Only
`analysis_sha256` is excluded from the comparison. **31 plans are identical.** Every
changed fixture is listed here:

| Fixture | Reason |
| --- | --- |
| `GRM-03_thumbwheel_drive_screw_AP242_PMI` | Ten resolved PMI dimensions and source ownership; authored profile chain replaces cumulative lengths; MAX retained; annotation bands, table and datums reflow on the same sheet. |
| `hw_assembly_plate_pin_intent` | Remove the obsolete maintainer-question reference from one refusal; drawing unchanged. |
| `hw_every_kind` | Same reference cleanup on one refusal; drawing unchanged. |
| `nist_ftc_10_asme1_ap242-e2` | Same reference cleanup on six refusals; drawing unchanged. |
| `nist_stc_10_asme1_ap242-e2` | Same reference cleanup on nine refusals; drawing unchanged. |
| `assembly/assembly_pin_pmi.step.gz` | Same reference cleanup on one refusal; drawing unchanged. |
| `rule_pins/nist_ctc_05_asme1_ap242.stp.gz` | Same reference cleanup on four refusals; drawing unchanged. |

`plan-comparison.json` records every changed JSON path. There are no unexplained plan
changes, and no existing comparison capture or accepted difference was changed.

The four inspected PNGs are:

- `artifacts/derived-dw/GRM-03_thumbwheel_drive_screw_AP242_PMI-before.png`
- `artifacts/derived-dw/GRM-03_thumbwheel_drive_screw_AP242_PMI-after.png`
- `artifacts/derived-dw/bracket_positions-before.png`
- `artifacts/derived-dw/bracket_positions-after.png`

## Verification

All Rust and acceptance checks use `CARGO_BUILD_JOBS=8`, `RUST_TEST_THREADS=8`,
`DRAFTWRIGHT_CORPUS=/Users/paul/repos/quiddity/tests/corpus`, and
`DRAFTWRIGHT_CORPUS_REQUIRED=1`. Check logs and PNGs are under
`artifacts/derived-dw/` (generated evidence, ignored by Git).

| Check | Result |
| --- | --- |
| `cargo fmt --all --check` | PASS |
| `cargo clippy --workspace --all-targets --release -- -D warnings` | PASS |
| Focused derived geometry and repaired guards | PASS: 7/7 |
| `tools/check_test_binaries.py`, source and compiled inventory | PASS: 8 integration binaries, 138 reachable Rust files, 1,260 tests with exact matching names |
| `cargo nextest run --release --workspace --ignore-default-filter --no-fail-fast --test-threads 8` | PASS: 1,514/1,514, 0 skipped; 595.322 s; includes all 1,201 cases in the four affected binaries and both native latency gates |
| `cargo test --release --workspace --doc` | PASS: 0 doc tests in both workspace crates |
| `tools/ci_acceptance.py --job mcp` | PASS: Python quality tests, acceptance tests, Node client tests and MCP scenarios; 11.353 s |
| `tools/quality.py` on `tools/quality_parts.txt` | PASS: 30 parts; no worse than the unchanged CI baseline |
| `tools/check_leader_routes.py` | PASS: 144 callouts on 30 parts; eight unchanged pre-existing runs remain |
| `tools/check_quality_table.py` | PASS: 30 parts, 300 CI metric cells, no part worse |
| Direct `ci_acceptance.py --check-results quality` comparison | PASS against `tools/ci-quality-baseline.json` |
| Release-pin comparison | WARNING: different published specify-core-rust pin; checker unchanged |

The exploratory affected-binary run executed all 1,201 cases: 1,198 passed and three
failed. Two test setup corrections registered the independent fixture and checked complete
rendered marks against the three exact pre-existing manufacturing refusals. The fingerprint
guard also correctly rejected an executable after source edits during that run. All three
checks passed after a frozen-source rebuild; the final workspace run rechecked every affected
binary as well as all remaining unit, corpus and latency targets. No existing comparison
fixture, assertion threshold, quality baseline, instruction budget or test exclusion changed.

The quality table's reference is an exact transcription of the ten metric columns in
`tools/ci-quality-baseline.json`, with its source SHA-256 retained in
`artifacts/derived-dw/ci-quality-reference.md`. Totals are off-sheet 0, repeated labels 7,
repeated measurements 0, view overlaps 0, title-block overlaps 0, ink findings 6,
repeated spans 0, lines through features 63, hidden-on-visible 0 and section-hidden 0.
The JSON baseline, leader-route baseline, assertion thresholds and budget files are unchanged.
Final PNGs were regenerated from the tested release binary; bracket_positions still has
an identical raster. Both code commits and this evidence remain local; nothing was pushed.

## Datum placement review correction

The source chain changed the dimension ink used to rank B's attachment sites. The old
search put the letter box beyond the entire view's right edge even when the shoulder
had clear paper beside it. Grading considered the letter boxes and leaders, but did not
reserve filled datum triangles: B's lower alternative could overlap A's triangle without
a penalty. The new dimensions therefore exposed two general placement defects.

Compact datum tiers now include an attachment-based grid with the existing triangle,
shaft and frame clearances, while retaining the original view-boundary grid. Replacing the
old grid would change its phase and lose existing clear lanes on unrelated drawings.
A box inside the view's overall rectangle is usable only when the existing projected-edge
and full curved-face-cover proof certifies exterior paper;
its entire leader must also clear the model ink. That proof previously required a
planar face square to the viewer, which excludes an entirely turned side view.
Datum exterior proof now works without that face. Ordinary dimension routing retains its
original eligibility, and interior labels still require their containing planar face.
Missing surface evidence remains refused. The same proof is used by placement and collision
reporting; all sheet checks remain enabled.

The compact search places a planar datum before cylinders, whose other proven silhouettes
provide more freedom. This prevents a cylinder from consuming the plane's only clear
shoulder band. Equally clear automatic datum candidates are ranked by total leader length
before their original site order. Only the normal minimum-length clear shaft ends the
search early. A shorter candidate receives an exact crossing count: it cannot inherit a
pruned tie with an earlier, longer route.

The original, unmodified placement remains the contact reference. An already separated
seed retains the complete no-new-contact guard; triangle reservations are applied in the
corrective searches instead of silently changing that reference. A seed with touching
triangles is illegal: symbol-separation repair takes precedence and may exchange soft
contacts for fewer hard symbol overlaps, with every resulting finding still reported.
The previous complete compact arrangement also remains a candidate, subject to the new
triangle lint. A bounded joint assignment is used only if ordinary placement still leaves
triangle contacts, reserving disjoint proven attachment sites before placing the boxes.
Three cylinders can share a projection and need an earlier symbol to leave its middle
station. The search never invents a point on a face. Filled triangles have priority over
soft label/leader congestion so coincident projections try other proven sites even when
every letter-box tier is busy.

Filled triangles reserve their bounds with 0.5 mm clearance alongside the letter boxes,
including editor-pinned placements. Independent final-plan lint reports
`datum_symbol_overlap` as an error when two actual filled triangles overlap, contain one
another or touch. It uses separating axes rather than bounding boxes, so empty triangular
corners do not produce a false overlap. The arrangement guard includes this finding.

The drawing choice is to keep B near its proven upper shoulder attachment, away from A,
with a short leader, retaining all ten source dimensions on A4 at 5:1. New regressions
require more than one symbol height between A and B, no B leader crossings, a leader
shorter than 20 mm, no touching triangles, and no more than main's three datum-leader
crossings. Planted overlapping ink is an error even with separate letter boxes. Separate
unit checks cover overlapping/touching triangles, containment, disjoint triangles whose
bounds overlap, and exterior proof on an entirely curved part without permitting ink
inside its body or through its silhouette. Further native regressions require the original
zero-crossing datum layout on `hw_every_kind` and distinct triangles for all 5 / 8 / 5 / 8 drawn
datums on NIST FTC-07, FTC-10, STC-09 and STC-10, without dropping any of those datums.

GRM-03's datum-leader crossings are **2**, down from the reviewed head's **7** and below
main's **3**. **B has zero crossings**, with a **2.4 mm** leader at the upper proven shoulder
site. Its attachment is **34.3 mm** from A (the symbol frame is 6 mm tall). A remains below
the shoulder. All ten PMI dimensions, the 14 total dimensions, A4 and 5:1 scale are retained.

### Review-fix drawing evidence

The comparison against reviewed head `3c43abc0` covers the same 38 fixtures. All dimension
requirements, labels, qualifiers, measurement claims and states are identical; GD&T source
identities, faces, cells, modifiers and states are also identical. No requirement was dropped
to obtain separation. `fix/requirements-comparison.json` records these checks and
`fix/plan-diffs.json` lists every changed JSON path. The original derived-geometry comparison
above remains historical evidence for the lowering itself.

The original filled-triangle contacts were GRM-03 B/A, FTC-07 B/C, FTC-10 C/B + E/G + F/D,
STC-09 A/G, and STC-10 B/C + D/E + G/F + G/E + F/E. The final 38-plan audit has **zero**
filled-triangle contacts. Crowded NIST leaders still have warnings; keeping distinct,
face-supported datum symbols takes precedence over retaining an invalid coincident symbol.
These warnings are retained without changing the CI baseline:

| Fixture | Triangle contacts before → after | Datum-leader crossings before → after |
| --- | ---: | ---: |
| GRM-03 AP242 PMI | 1 → 0 | 7 → 2 (main: 3) |
| NIST FTC-07 | 1 → 0 | 5 → 6 |
| NIST FTC-10 | 3 → 0 | 13 → 20 |
| NIST STC-09 | 1 → 0 | 11 → 15 |
| NIST STC-10 | 5 → 0 | 12 → 20 |

The other 33 fixtures have unchanged datum-leader crossing counts. FTC-07 changes from
A2 at 1:5 to A1 at 1:1, making its features more readable while separating the symbols.
The other three NIST sheets retain their previous size and scale. There is no increase in
hard annotation errors, and every previous PMI refusal remains reported.

PNGs regenerated from the saved before binary and final tested binary are under
`artifacts/derived-dw/fix/`. All seven were visually inspected:

- `GRM-03_thumbwheel_drive_screw_AP242_PMI-before.png` and `-after.png`: B moves from the
  lower overlapping pair to the upper shoulder, with a nearby box and a short clear leader.
- `bracket_positions-before.png` and `-after.png`: pixel-identical, on A2 at 1:1; its plan is
  also unchanged, including the five face-to-bore refusals.
- `nist_ftc_07_asme1_ap242-e2-before.png` and `-after.png`: the enlarged 1:1 view and separated
  B/C attachments are legible; the source frames and hole schedule remain present.
- `nist_stc_10_asme1_ap242-e2-after.png`: the previously coincident datum markers are distinct;
  the congested leader warnings remain visible.

### Review-fix verification

The source is frozen at `c46affc`. Runs use the same required-corpus environment and eight
build/test workers specified above. No assertion, threshold, comparison fixture, baseline,
instruction budget, test exclusion or kernel pin was weakened or re-recorded. No kernel
repository was edited, and no repository was vendored or copied. The initial affected run
was interrupted to correct wider survey findings; the completed frozen-source results below
are the verification record. The full suite ran once after those corrections.

| Check | Result |
| --- | --- |
| `cargo fmt --all --check` | PASS |
| `cargo clippy --workspace --all-targets --release -- -D warnings` | PASS |
| Source and compiled test inventory | PASS: 8 integration binaries, 138 reachable files, 1,267 exact test names |
| Focused datum and interior-proof checks | PASS: 26/26 |
| 66-part `tools/check_plan_time.py` | PASS: 2,266,945,131,444 instructions; unchanged limit 2,705,481,516,652 |
| Four affected integration binaries | PASS: 1,208/1,208, 0 skipped; 361.846 s |
| Full workspace nextest run | 1,524/1,525 passed, 0 skipped; sole failure: native NIST session latency (663.839 s) |
| Final isolated native NIST session gate | PASS with unchanged 150 ms assertion; 15.534 s (`fix/latency-retry-quiet.log`) |
| Workspace documentation tests | PASS (both crates; 0 doctests) |
| MCP acceptance | PASS: Python quality tests, acceptance tests, Node client tests, 7/7 scripted tasks |
| Quality and table checker | PASS: 30 parts / 300 baseline cells; no part worse |
| Rendered leader-route checker | PASS: 144 callouts on 30 parts; 8 unchanged pre-existing runs |
| CI quality baseline comparison | PASS against unchanged `tools/ci-quality-baseline.json` |

The full run's NIST session latency sample maximum was 329.561 ms; its other eight
samples were 37–104 ms. An unchanged isolated retry also failed: maximum 168.291 ms,
other samples 10–91 ms. The 150 ms native gate remained enabled throughout. Both
failures are retained in `fix/full-suite.log` and `fix/latency-retry.log`. They are timing
failures rather than delta/plan/render equivalence failures; those checks pass in the same
test before its final latency assertion. The other native session latency benchmarks
and the multi-sheet latency gate passed in the full run. The latency fixture contains no
GD&T and does not enter the new datum candidate search. A final isolated retry after all
quality work completed **passed**, including the unchanged 150 ms assertion; no source,
threshold or gate setting changed between these runs. The full suite was not repeated.

Quality totals remain off-sheet 0, repeated labels 7, repeated measurements 0, view overlaps 0,
title-block overlaps 0, ink findings 6, repeated spans 0, lines through features 63,
hidden-on-visible 0 and section-hidden 0. `fix/ci-quality-reference.md` is generated verbatim
from the unchanged CI reference JSON and records its SHA-256. `fix/validation.json` and
`fix/verification-continuation.json` retain the run outcomes, including the full-suite failure.

The final release SHA-256 is `3be615a3db43e0a8f606bd95a585ef510bf05cf1788f87bec13d1154772df827`,
matching the saved after binary, the acceptance summary and the drawing captures. The final
GRM-03 before/after PNGs were viewed again after all checks: the upper B attachment and
nearby frame are clear of dimension ink, with A below. Commits remain local; nothing was pushed.

## Integration with manufacturing note callouts

After merging #112, a knurl's note form must retain the owned PMI size's own
qualifier and tolerance before the manufacturing reference. GRM-03 therefore
prints `ø10 MAX SEE MFG 2`; MFG 2 carries the complete knurl wording. The planted
tolerance regression exercises `auto`, `note`, `compact` and `full` presentation,
both with the original manufacturing set and with a lone knurl. A manufacturing
`MAX AFTER KNURL` term cannot mask the independent size qualifier before note
conversion. The manufacturing identity also retains its exact source precision;
only explicit dimension limits replace that nominal identity.

Callout reflow also exposed an arrangement-level tie: the boundary-only datum
layout could retain B's 113.22 mm leader when the local layout had the same
contact score. The final arrangement comparison now prefers shorter total datum
leaders after symbol overlaps, datum crossings and annotation contacts. The
boundary-only pass must compete with the local search before ending the search;
a drawing without datum leaders retains its early exit. The original contact
guard and proven-site requirements still apply, with no fixture-specific rule.

The drawing choice retains A4 at 5:1, all ten source sizes and 14 total dimensions.
B's leader is **2.4 mm**, with **zero crossings**; the drawing has **two total
datum-leader crossings**, both on A. The PNG in `artifacts/derived-dw/merge/` was
visually inspected: B is clear beside the upper shoulder, and MAX remains on
the diameter leader. SVG, PDF, plan, render report and metrics are alongside it.

The final source is `bf98176b`. Checks use the required corpus and eight build/test
workers as above, with logs under `artifacts/derived-dw/merge/`.

| Merge check | Result |
| --- | --- |
| `cargo fmt --all --check` | PASS |
| `cargo clippy --workspace --all-targets --release -- -D warnings` | PASS |
| Focused GRM-03 and source-precision regressions | PASS: 9/9 |
| Requested nextest binaries | PASS: core 279, dimensions 331, rendering 461, audits 158; **1,229/1,229**, zero skipped, 434.126 s |
| `python3 tools/check_test_binaries.py` | PASS: 8 binaries, 139 reachable files, 1,288 integration tests; documented counts corrected |
| Final GRM-03 render | SVG/PDF/PNG regenerated; PNG byte-identical to the inspected image; metrics in `GRM-03-metrics.json` |

The first selected-binary run passed 1,227/1,229: two existing bolt/knurl checks
caught the same accidental rounding of the exact source diameter 6.25 to 6.2.
The code now retains the manufacturing identity's precision while adding the
size's own band and qualifier. Both unchanged precision expectations pass in the
focused check and the final complete selected-binary run. No baseline, tolerance,
placement threshold or test exclusion changed. The full suite was not run;
`CHANGELOG.md` is untouched. Commits remain local; nothing was pushed.
