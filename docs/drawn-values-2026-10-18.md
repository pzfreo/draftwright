# Faithful nominal values and interrupted single flats

The starting revision was `d96f067b`. The report's bugs 3 and 4 reproduce using
`/Users/paul/steps/GRM-02_shaft.step` (SHA-256
`e5babf6b7de9137adc35f90a0087530ee07143ec3a56c68982125068f672a757`).
The unchanged STEP is retained as `tests/fixtures/parts/grm02.step` for regression tests.

Default nominal formatting intentionally departs from Python's `_fmt`: remove noise by
rounding to 1e-6, then choose the shortest decimal within 1e-6, with at most three places.
`4.75`, `7.4`, `12`, and `247.65000001 -> 247.65` are examples. If no faithful decimal fits, all three places are printed (for example, `4.0001 -> 4.000`).
A live numeric carrier
that needs more than three places produces an info `nominal_rounded` problem naming
its dimension/measurement, modelled value, and printed value. Spec precision still wins;
tolerance magnitudes keep their independent formatting. Callouts, dimensions, ordinate
and hole-table cells, chamfers and generated nominal notes use the same policy. The lint
coverage/parameter and PMI formatters independently implement that policy; profiled-bore
lint uses the lint parameter formatter. Flat auto-decimal padding is replaced by the common
nominal policy so it cannot silently round a third decimal.

Quiddity's pinned `features/flats.rs::discover` emits an occurrence for every candidate
face (`defining: vec![cand.face]`); it calculates `across` as opposed-plane distances when
an opposite face exists, otherwise plane-to-axis plus stock radius. GRM-02 therefore has
two provider records, faces `[6]` and `[2]`, both the z=-3.4 plane. Draftwright previously
admitted both records and unconditionally suffixed every flat callout with A/F.

Draftwright's bridge now combines coplanar faces on the same stock, before exposing
stable flat IDs, and retains both face claims and a rebuilt fingerprint. Stock direction
and span are canonicalized, and different stock regions remain separate. Both the native
and Python bridge paths perform the repair. No quiddity source or dependency pin changed.
The bridge records whether parallel flats actually oppose each other across that stock's
axis. A/F requires this proof. Single flats keep the existing Python `FlatFeature` D-height
convention (flat-to-opposite-OD, 7.4 here), without A/F. Python's `model/ir.py` documents that
convention; its `annotations/from_model.py::_flat_label` incorrectly applies A/F even to it.

Live linear dimensions with an endpoint on the single flat's actual plane can carry its
height or axis-offset measurement. GRM-02's 7.4 envelope height and 3.4 hole-axis location
therefore state the one flat when its callout is omitted. The claim reaches both completeness
and lint. Regression tests remove the height too to verify axis-location coverage alone,
and then remove every carrier to ensure a real missing flat is still reported.

The archived Python captures remain unchanged. Exact label assertions and the dimension/PMI
parity ledgers now record the faithful nominal text; the ledgers retain exact measurement,
view, count, coverage and tolerance checks. Hole tables additionally compare every cell
against explicit new nominal snapshots before replaying the historical-format comparison.
Old `nominal_rounded` findings that merely described one-decimal rounding are removed;
three-place-limit information remains required where appropriate.

Longer labels exercise the existing layout rules. Linear text can use outside arrows without
moving measured tips, and a short label can overhang a tip slightly while respecting peer
witness clearances. Dimension-line run-ons stop just past their arrow tails. Final label promotions preserve semantic lane order. An inward overall extent may spend
the same two-lane movement bound beyond the old footprint to restore its outermost position;
page fit, contact and clearance gates remain mandatory. Final callout repair checks the complete live
inventory, including near-parallel and arrow-shaft contacts, without adding material,
furniture, text or extension contacts. Interior attachments and visible-profile proofs remain
protected. Retained session caches separate prepared reference data from policies activated
by the current planning invocation; an earlier automatic solve cannot change a later explicit-scale edit. No new accepted leader or ink defects are added.

M10000 retains A4 at 1:1 and its size/location/overall order. A centred outside-arrow
alternative must fit its own span, and no new slide may cross its own witnesses. Longer labels reserve their actual footprint by reclaiming vacant neighbouring
bands; every neighbouring view, mark, dimension, GD&T glyph and isometric caption remains protected. This
removes the outside-area findings for string_post (its longer `3.75` ordinate and the
previous 1 mm head overrun now fit the measured reservation), and reduces cgb245's
old estimated-band findings from four to one. The strict placement ledger removes those
obsolete findings; no new geometric exception is added.

A private established comparison keeps compact text for existing corridors
and paper-length budgets; it is never rendered. Incoming placements are remeasured using live terms. Inside-arrow
placements that a longer label outgrows stay free for the accurate solve. The
comparison includes furniture settlement, restores its formatter after panics,
and has separate cache keys. Imported recognition and semantic PMI keep their
usual layout.

A late comparison for automatic native dimensions contributes current and established
attachment rays after the complete ink inventory has settled. It keeps paper shoulders
and length budgets independent of projection scale, and respects actual pinned items.
Short shoulders and bounded local moves can fit longer labels between witnesses or into
vacant neighbouring paper. New text contacts are checked by owner, so exchanging one
collision for another cannot pass just because their counts match. Arrowheads, padded
labels, outlines, material, furniture, and protected neighbouring ink are checked on the
live geometry. Already clear routes stay fixed. The rescue hole table has a separate
regression verifying its actual rendered cells and precision-limit information. A revived
coincident dimension inherits its survivor's final lane while retaining its own physical
witness origins.

The axis-overrun regression prescribes its label across the live axis end because automatic
stations can change with longer neighboring labels. Its shelf gap, physical span, opposite
overrun, minimum retained overrun, and axis-column assertions are retained. The MCP fit
scenario identifies the same through bore by its new printed `8.094` rather than `8.1`;
its feature identity, H7 requirement, and preservation checks remain unchanged.

The resumed run merges `origin/main` at `9422945b`, including arc leader,
user-facing text, projection alignment and title-field fixes. The growth-repair
attachment carries the complete arc span, and restored coincident lanes retain
the upstream author-edit and view guards. Verification results and the final
label census are recorded below.


Merge validation exposed a GD&T improvement on the imported STC-09 drawing:
only one control frame remains outside its reserved band, down from two.
`gdt_band` now requires exactly one; its frame-kind, clear-strip explanation,
datum, on-sheet and foreign-view assertions remain intact. The split-grid
fill expectation stays at main's exact 52.2% after integration.

Growth repairs require an outward shoulder and use the initial router's profile
sites, including section/detail transformations. A retained proven attachment
cannot jump to a different occurrence when its elbow moves. A shorter route that
still crosses a peer does not preempt the existing bounded clear-route retry.
Physical-ink, page, length and author-pin checks remain mandatory.

Native perpendicular witnesses use the nearest already-claimed member of a shared
span. A straight row's constant-coordinate location can likewise use any member;
its along-row location still uses its first member. Each new witness is a subset
of the old stroke: the measured stations, value, arrows and text stay fixed.
This removes unnecessarily long witnesses blocking CTC-04's callouts. Authored
placements, oblique witnesses, sections and details retain their geometry.

New-crossing comparisons retain original CAD-edge identity across sampled chords.
Moving across another chord of the same curve is not a new physical edge crossing;
a hard-contact or hidden-crossing repair may exchange edges only within the
established intersection budget and the resolved hidden-ink limit. A regression
requires the identity comparison to distinguish both outcomes while
retaining the independent raw-chord intersection checks. Metadata stores only
curve bounds and chords, without a cache per curve.

Unchanged fillet callouts are compared with complete canonical claims after blend
consolidation, preserving their rays during a tolerance edit. Merely permitting
another sheet (`sheets.max`) keeps the same nominal placement comparison; other
sheet-allocation constraints retain their own solve.

The CTC-04 leader ledger removes exactly two obsolete pattern-9 crossings
(pitch of pattern 2 and length of slot 4). No new crossing is accepted. Exterior
shoulder repairs explicitly reject a new crossing of their own view box, even
when another hard blocker would disappear.

## Census before the final release merge

The baseline plans and PNGs use `d96f067b`; the final plans include merged main
`9422945b` and the fixes above. This is a text census, not the CI repeated-spans job.
Every one of the 65 survey inputs and 30 quality inputs completed successfully.
Common dimension IDs are compared independently of placement:

| Set | Parts with changed text | Changed nominal labels | A/F-only changes | Changed labels drawn in both versions |
| --- | ---: | ---: | ---: | ---: |
| 65-part survey | 52 | 798 | 1 | 745 |
| 30 quality parts | 28 | 420 | 1 | 377 |

Thus total changed common-ID labels are 799 and 421 respectively. There are six
new dimension IDs in the survey (two in the quality subset), with none removed;
these additions are not counted as changed labels. Undrawn candidate labels are
included in the common-ID count but excluded from the drawn count. The sole
qualifier-only change is CTC-05's `2× 403.86 A/F -> 2× 403.86`.

The live auxiliary-carrier comparison additionally finds 723 changed hole-table
cells, 24 table cells and five notes in the survey; the quality subset has 545
changed hole-table cells, four table cells and three notes. Carrier additions and
removals caused by layout/table choices are recorded separately in the JSON.
No new number with more than three fractional digits appears on live ink. The
only longer tokens are pre-existing explicit tolerances such as `+0.3048` and
`-0.0508`. There are 741 informational three-place-limit observations across the
survey; these state both the modelled and printed value. Noise regression
`247.65000001 -> 247.65` passes.

Examples: GRM-02 `4.8 -> 4.75`; GRM-05 `ø6.2 -> ø6.25`; cgb203
`134.0 -> 134.009` and `44.0 -> 44.009`; M1000 `R7.2 -> R7.179`
and `12.6 -> 12.593`; CTC-02 `23.5 -> 23.49` and `8.4 -> 8.38`;
FTC-06 `247.7 -> 247.65`; string_post `3.8 -> 3.75`.

All evidence remains under `target/values/` in this checkout (not committed):

| Part | Before PNG | Final PNG |
| --- | --- | --- |
| GRM-02 | `target/values/before/GRM-02.png` | `target/values/after/GRM-02.png` |
| GRM-05 | — | `target/values/after/GRM-05_depth_lock_bolt.png` |
| cgb203 | — | `target/values/after/cgb203.png` |
| M1000 | — | `target/values/after/1000.png` |
| CTC-02 | — | `target/values/after/nist_ctc_02_asme1_rc.png` |

`after/GRM-02-omit-flat.png` verifies the omitted-callout case. Its plan has no
flat `feature_not_dimensioned` finding. The default PNG prints 4.75 and one 7.4
flat callout without A/F. Fresh PNG inspection covers all five final parts.
The census evidence is `label-changes.json`, `carrier-changes.json`,
`label-changes.txt` and `label-audit.txt`; the plans themselves are in `before/`
and `after/`.

## Final route validation

Native Analysis now optionally carries the renderer's three resolved principal
projections, including their visible/hidden classification. These are immutable
curves, never a retained Part or kernel object. The planner maps them to paper,
applies the renderer's visible-over-hidden precedence, and samples to 0.002 mm
for route queries. Export inspection also uses SVG's six-place endpoints,
polynomial rational pieces, canonical circle origin and endpoint-arc reconstruction.
Its crossing predicate handles the strict crossing introduced when a rounded endpoint
was an analytic touch (FTC-10); the analytic router keeps its existing tolerance. Older analyses and imported recognition retain the conservative
edge-evidence fallback. An independent unit regression serializes/deserializes a
native Analysis and compares visible/hidden intersection counts with dense
sampling of the actual renderer's curves.

The final physical pass runs after blend consolidation, optional-detail decisions
and temporary retention pins. It uses printed dimensions and preserves latent
user omissions, while conveyed raw aliases cannot block it. A candidate must pass
the complete geometry recheck without increasing hard findings, ink findings or
feature crossings. Hidden intersections stay within the established route’s budget (or the incoming
route when no established attachment is available);
moved candidates cannot run beside drawn ink or extensions for over 2 mm. Padded
labels also clear the page border. Comparisons match the live placement region;
a compact interior route cannot impose its paper reach on an exterior label.
The repair's two-mm length allowance is also capped against the shorter incoming
route, preventing a later pass from lengthening a corridor already repaired.
The half-mm retry covers two 7 mm lanes plus padding,
and at most four improving final passes allow one repaired route to free another.

M10060's repeated-label count falls from one to zero: two formerly identical
`19.3` labels become `19.310` and `19.256`. The first is explicitly reported at
the three-place limit. No frozen quality or route baseline is changed.

## Verification before the final release merge

Build, test and acceptance commands use `CARGO_BUILD_JOBS=8`, `RUST_TEST_THREADS=8`,
`DRAFTWRIGHT_CORPUS=/Users/paul/repos/quiddity/tests/corpus` and
`DRAFTWRIGHT_CORPUS_REQUIRED=1`, and run sequentially.

- `cargo fmt --all --check`: pass.
- `cargo clippy --workspace --all-targets --release -- -D warnings`: pass;
  `target/values/resume-final-clippy.log`.
- Source inventory guard: pass, eight binaries / 139 reachable files / 1,272 tests.
- All eight grouped integration binaries were exercised after merging main.
  The final focused run passes all 61 selected cases, including the initially
  failing merge regressions, flat/precision/repeated-dimension cases, projection
  alignment, trimmed-arc leaders and session benchmarks. The visibility fixture
  was changed from a plain turned part with no hidden ink to GRM-02's concealed
  bores; the hidden-coverage and exact crossing assertions are retained.
  Evidence: `resume-final-focused2.log` (60 passing cases) and
  `resume-visibility-test.log` (the corrected fixture and source fingerprint pass).
- `tools/ci_acceptance.py --job mcp`: pass on the final binary, all seven tasks;
  `resume-final-mcp.log` and `resume-final-acceptance/mcp/`. An earlier run's
  paused-child instruction-counter self-test missed its 90% lower bound; its
  isolated retry and the final complete job pass without changing the assertion.
- Census and five fresh PNGs: pass; `resume-artifacts.log` and the evidence above.

The final release checks below supersede the earlier focused checks. The full
suite and the CI-only quality-pins, repeated-spans, render-tail and plan-time
jobs were not run in this final round.


## Final release merge and route checks

Merged `origin/main` at `51d8f9f5` (including #113–#115) in `43f81793`.
`CHANGELOG.md` is exactly upstream's version; the branch's earlier entries were
removed. Conflicting expectations combine faithful nominal numbers with the
upstream 48 mm callout wrapping and manufacturing-note ownership. The source
inventory guard and unfiltered workspace nextest listing agree on 1,304 integration
tests in eight binaries (core 279, dimensions 336, rendering 472, audits 158).
A new final-inventory label-association regression raises the final inventory
to 1,305 tests, with 473 in rendering.
The four additional separate binaries were listed but are outside this round's
requested test run.

The uncommitted rank-tracing block from the interrupted run was removed before
validation. Exported hidden-ink crossings use the SVG inspection geometry and
strict endpoint predicate. Clear short corridors outrank extra hidden-edge
reductions once the crossing budgets have been met. The late comparison retains
both full-inventory and pre-recess routes, and uses the shorter matching corridor
for its length budget. These are unrendered comparisons; final labels and their
placement checks use the live terms.

The unrendered comparisons retain their compact single-line labels; all live
labels use current measured wrapping. The full and pre-recess comparisons share
the existing comparison scope and preparation. New recesses absent from that established inventory compare
model reach at the live view scale; existing callouts retain their paper budgets.

Final inventory repair now considers the witnesses separating another size’s text
from its arrows, as well as a detached label itself. Native geometry can slide text
on its existing dimension line without inheriting an unrelated lane-order defect.
These changes retain 10047’s 4.53, 2.526 and 3.526, cgb207’s 12.001 and FTC-09’s
precise locations. Authored PMI retains its established placement policy.

FTC-08’s complete 1:1 drawing leaves a new seat leader through hole 19. One bounded
retry on the same A1 paper at 1:2 clears all feature crossings while retaining the
precise 192.21 slot width, 31.75 slot lengths and ø10.16 × 3.429 seat statement.
The retry preserves every carried measurement, the geometry’s minimum opening,
step readability, text association, and the existing layout/ink/leader budgets.
Finished outward-arrow placement supplies the proof when the estimate for text
inside a slot chain is too conservative. No numerical acceptance limit changes.
At the initial release validation, FTC-08's
`d:f:fillet:7/fillet.radius` printed `8× R1.5` for the eight claimed fillets
`f:fillet:5` through `f:fillet:12`, whose modelled radii remain 1.524 mm.
The precise `8× R1.524` label cannot find a route within the established
hidden-edge, length and clearance budgets; retaining its original shaft and
compact text preserves the baseline's existing 3.275 mm ink run. Its label,
terms, placement box and inspection basis all carry that same decision.
The CI repair keeps one further compact exception: mfcadpp/1004 hole 1 prints
`⌀7.4 ↧ 5.9` (modelled 7.44654 and 5.86) to preserve its existing ink run.

The NTS caption is reconciled after the final gutter translations, against the
finished views and fixed schedules. Four exact upstream spool132 wrapping
expectations lost in the merge resolution were restored from origin/main; no
new discrepancy allowance was added. Ordinary counted-seat test strings combine
the new numbers with upstream line breaks.

Consolidated native callouts also retain the proven attachments of every claimed
raw occurrence in the same view. A longer fillet label must be able to point at
another equivalent occurrence when its old leader ran beside drawn ink; conveyed
aliases cannot remove those genuine attachment choices.

The final repair uses the upstream furniture policy for authored PMI, including
its original profile evidence. The manufacturing plate again chooses A3 at 1:1.
Gutter translations are rounded at their existing 1e-8 mm solver resolution so
an annotation-only edit cannot dirty view geometry through nanometre noise.
The upstream two-frame STC-09 expectation is retained. Two assertions about an
old polishing-phase marker and the first candidate's wording were removed;
final sheet/scale, frame clearance, the independent sheet-selection oracle,
wrapped labels and unchanged leader-budget assertions remain intact.

The compact fallback runs only on the finished inventory, after consolidation
and table ownership. Bare pins receive the same final text decision, and the
fallback preserves their shaft and arrow coordinates byte for byte. Covered
hole callouts do not trigger an exception; their tables retain 6.647 and 2.459.

Final settling respects the effective no-op-offset normalization used by the
main solve. Bare pins preserve all already settled peers while still receiving
the final text decision. This keeps FTC-06 callout-offset replay and FTC-09's
schedule replay identical to their automatic drawings. Automatic paper/scale
retries also retain the bound caller intent instead of restoring an unbound
input Spec; semantic PMI remains source carrying beside a requested schedule.

Fresh release evidence on 2026-10-19:

- `cargo fmt --all --check` and strict workspace/all-target release clippy pass.
- `cargo nextest list --release --ignore-default-filter --message-format json`
  and `tools/check_test_binaries.py --nextest-list …` agree on eight binaries,
  141 reachable Rust files and 1,305 integration tests. Listing does not run them.
- Fresh `tools/quality.py` output for all 30 parts passes. The unchanged
  `tools/check_leader_routes.py` baseline passes all 145 callouts, including
  CTC-03 and FTC-10 with precise labels. There are no added hidden/section/cut
  crossings, no length increases beyond the existing 2 mm allowance, and no new
  runs over 2 mm. The two unchanged pre-existing runs are cgb241's
  `d:f:blend:f45/blend.radius` (2.881 mm) and FTC-08's fillet callout (3.275 mm).
- `tools/check_quality_table.py docs/status-2026-10-10.md …/baseline.json`
  passes all 450 cells: no part is worse. Off-sheet items, view overlaps,
  title-block overlaps, repeated measurements and repeated spans are all zero.

The fresh drawing evidence is `target/values/certified-quality/`, with check
summaries in `certified-quality-summary.log` and `certified-checks-summary.log`.
The route fixture, frozen quality references and numerical thresholds are unchanged.

All 30 fresh SVGs are byte identical to the earlier successful route/quality
audit before the final binding and pin-replay fixes. Those fixes preserve the
automatic drawings.

The requested integration binaries pass: core 279, dimensions 336, rendering 473
and audits 158 (1,246 tests total). Core and audits passed in the four-binary run;
after fixing its binding/pin failures, dimensions and rendering were rerun in full
and passed. The four focused `dims::leaders::arc_tip_tests` units also pass.
The full eight-binary suite was not run in this round.
