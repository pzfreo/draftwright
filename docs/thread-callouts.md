# Thread and knurl callouts

For physical recognition-v4 threads, see [modelled screw threads](modelled-threads.md)
and their per-occurrence depiction/designation controls.

Thread and knurl leaders read horizontally. The complete requirement remains associated
with its PMI source, even when a manufacturing note carries its secondary terms.
The existing trimmed-arc radius leaders and perpendicular witnesses are unchanged.

## Format and controls

Set `sheet/callouts/style` to `auto` (default), `compact`, `full`, or `note`.
`features/<id>/callouts/style` overrides the sheet for that feature, for both threads
and knurls. JSON example:

```json
{
  "sheet": {"callouts": {"style": "auto"}},
  "features": {
    "f:turned_step:f0": {"callouts": {"style": "full"}}
  }
}
```

`auto` starts with compact wording. A compact requirement needing more than two
48 mm lines, including its manufacturing reference, uses note form. During sheet
recovery, individual automatic callouts are also tried in note form at the chosen
scale and paper before accepting larger paper. A candidate must fit cleanly and
retain all carried requirements. The plan basis records these decisions.
`compact` keeps compact wording even over two lines; `full` retains the long
Python-style wording; `note` always puts the complete requirement into the existing
numbered manufacturing schedule and leaves its identity plus `SEE MFG n` on the leader.
A dimension that also owns a PMI size keeps its own tolerance and MAX/MIN/AVG
qualifier on the leader before the manufacturing wording or note reference. For
example, GRM-03 reads `ø10 MAX SEE MFG 2`: only the knurl wording moves to MFG 2.
Secondary process and conventional drill-point terms use this same schedule with
compact identity/depth wording retained on the leader. No term is discarded.

Dimension `text/prefix`, `text/suffix`, and `text/template` still wrap the generated
terms. Wrapping occurs before placement and again after authored wording. Words
stay intact; lines are at most 48 mm except for a single longer word. A semicolon
ends a line after at least half that width is used. Width uses the bundled font's
actual glyph and symbol metrics. Forced tap/drill breaks are retained. The same
multiline ink bounds drive lanes, corridors, collisions and sheet fit; SVG/PDF/PNG
use the existing font's ascender/descender/gap line spacing and searchable runs.
The full multiline block is centred on its horizontal leader shoulder, with a
small glyph-clearance adjustment available during final route polish.

## Examples

| Source/old leader | Compact leader (line breaks shown with `<br>`) |
| --- | --- |
| `M3 x 0.5-6g RH, FULL AVAILABLE LENGTH` | `M3-6g`, when thread extent is depicted |
| `ø5 M5x0.8-6g` entered through Spec | `M5-6g` |
| `M8 x 1-6g LH` | `M8 x 1-6g LH` |
| `⌀1.6 ↧ 4 M2 x 0.4-6H RH; 2.5 MIN FULL THREAD` | `M2-6H ↧ 2.5`<br>`⌀1.6 ↧ 4` |
| conventional through M4 tap | `M4-6H THRU` |
| knurl with a maximum diameter and process permission | `ø6 MAX AFTER KNURL SEE MFG 1`<br>`DIAMOND KNURL P1`; complete process wording in MFG 1 |
| explicitly selected thread note | `M2-6H SEE MFG 1`; designation, depth and drill in MFG 1 |

The knurl diameter is on its own leader line: this keeps maximum-after-knurl
wording next to its diameter, while the second line identifies pattern and pitch.
Fine pitches and LH are always retained; the tolerance class is retained exactly.
The shared coarse-pitch table covers the ISO 261 coarse entries from M1 through M68;
unknown diameters never acquire an inferred pitch.

`FULL AVAILABLE LENGTH` is removed only with enabled thread depiction in a profile
view. A cylinder length dimension alone is insufficient. Disabling depiction
retains the words (or the complete note). Likewise, knurl full-width wording needs
a depicted band; chamfer limits are retained unless corresponding chamfer dimensions
remain in the drawing's dimension set. Process permissions belong to the note.
A standard through tapping drill is implicit only when the source drill equals
nominal minus pitch, including the conventional tool-chart exceptions for coarse
M8 (6.8), M9 (7.8), and M12 (10.2); a stated different drill stays explicit.
An authored drill fit, tolerance, or precision also keeps the drill line explicit.

## Source depths and missing thread

The string-post fixtures both state **2.5 mm full thread**, with **⌀1.6 × 4 mm**
tapping drill. `hw_string_post_tapped_intent` used the field `full thread depth`;
the old lowering rejected the entire thread as an unsupported structured field,
leaving only its bore callout. This field now aliases `minimum full thread` and
`full thread length`. Explicit aliases must agree; structured/prose disagreement
still refuses lowering. No depth is calculated from drill depth and pitch.

The three spindle fixtures state **6.5 mm full thread** and **⌀4.2 × 9.2 mm** drill.
The thumbwheel taps state **2.5 mm full thread** and **⌀1.6 × 3.8 mm** drill.
No fixture in this review states a 6 mm full-thread depth. Source-value checks read
rendered leader and source-bound note runs, and reject damaged designation, pitch,
class, depth, drill diameter or drill depth.

## Standards checked

The reviewer requested compact English knurl wording; it is a presentation policy,
not a claim that `DIAMOND KNURL P…` is the ISO 13444 designation syntax.
ISO 13444 specifies knurl diameter, width and pitch, and references ISO 129-1 for
dimensioning. ISO 6410-1 ties thread size to its designation and allows RH only when
needed. ISO 261's coarse column, rather than a diameter/pitch formula, supplies the
omission table.

- [ISO 261:1998, Table 2](https://cdn.standards.iteh.ai/samples/4165/ec50482690954e82931c52fdce3bc8bb/ISO-261-1998.pdf)
- [ISO 6410-1:1993, designation and dimensioning](https://cdn.standards.iteh.ai/samples/12750/07f99becf07a454ab9fece84a0dce861/ISO-6410-1-1993.pdf)
- [ISO 13444:2012, knurl presentation and dimensions](https://cdn.standards.iteh.ai/samples/53788/610defe3dff544b1aab0cbea2b755c33/ISO-13444-2012.pdf)
- [Walter threading compendium, metric tapping drills](https://cdn.walter-tools.com/files/sitecollectiondocuments/downloads/global/manuals/en-gb/technical-compendium-threading-2024-en.pdf)

## Schema budget

Fields were added in `tools/mcp_spec.py` and regenerated with
`python3 tools/generate_mcp_schemas.py`. The on-demand Spec schema grows;
`tools/list` stays **34,477 compact input/output-schema bytes**, a **0-byte** increase.
The generator's `SCHEMA_BUDGET` and `tests/engine_version.rs` explicitly record this.
The on-demand Spec schema grows **11,999 → 12,256 compact bytes (+257)**,
or **28,605 → 29,250 bytes (+645)** in the checked-in indented JSON.

## Verification evidence

Before/after plans, analyses, SVG/PDF/PNG renders are under
`target/thread-callouts/{before,after}/<fixture>.*`. The baseline binary was built
from `db2d922e` in an isolated worktree of this repository. No other repository
was copied or vendored. All 11 before and after drawings were inspected, including
enlarged callout crops and complete sheets. Leaders read horizontally, forced
tap/drill lines are separate and clear, and process notes remain readable.

| Part | Before | After |
| --- | --- | --- |
| `GRM-03_thumbwheel_drive_screw` | A3 (10:1) | A3 (10:1) |
| `bolt_thread_knurl` | A4 (2:1) | A4 (2:1) |
| `hw_string_post_tapped_intent` | A3 (5:1) | A3 (5:1) |
| `hw_thumbwheel_notes_add` | A3 (10:1) | A4 (5:1) |
| `hw_thumbwheel_thread_knurl_intent` | A3 (10:1) | A4 (5:1) |
| `spindle_diamond_knurl` | A3 (2:1) | A4 (1:1) |
| `spindle_external_thread` | A3 (2:1) | A4 (1:1) |
| `spindle_turned_knurl` | A3 (2:1) | A4 (1:1) |
| `string_post` | A4 (5:1) | A4 (5:1) |
| `string_post_tapped` | A3 (5:1) | A3 (5:1) |
| `thumbwheel_thread_knurl` | A3 (10:1) | A4 (5:1) |

A4 count rises **2 → 8 of 11**. The automatic clean, legible A4 candidate prevents
unnecessary paper enlargement for the thumbwheels and spindles. The two tapped
string-post sheets remain A3: their remaining placement constraints are not cured
by moving the thread into a note. GRM-03 remains A3 with its existing large-scale
presentation; the unthreaded corpus string post remains A4.

Automatic notes in this review:

- `bolt_thread_knurl`: the knurl identity plus its required reference would need
  more than two compact lines, so the leader is `ø6.25 SEE MFG 1` and MFG 1 retains
  maximum diameter, straight knurl pitch, and `CUT/FORMED PERMITTED`.
- `thumbwheel_thread_knurl` and `hw_thumbwheel_notes_add`: the leader retains
  `ø6 MAX AFTER KNURL SEE MFG 1` / `DIAMOND KNURL P1`; MFG 1 retains the complete
  wording including `CUT/FORMED PERMITTED`. This is the secondary-term rule,
  rather than the greater-than-two-line fallback.
- No reviewed fixture needs the additional per-callout note-for-paper-fit fallback.
  Each selected presentation and reason is recorded in its dimension basis.

`hw_thumbwheel_thread_knurl_intent` contains two source threads and no source
knurl requirement despite its filename. Its drawing therefore has no invented
knurl callout. No reviewed drawing prints a full-thread depth different from its
stated source value. The existing unrelated 1.2 mm attachment refusal in the
tapped string-posts remains; the manufacturing thread refusal is fixed.

PNG evidence: `target/thread-callouts/{before,after}/<part>.png` using the exact
part names in the table. Inspection sheets are in `target/thread-callouts/qa/`:
`before-{1,2,3}.png`, `after-{1,2,3}.png`, and `callout-crops-{1,2,3}.png`.
Plans and PDF/SVGs are alongside the PNGs; `target/thread-callouts/review.json`
records each source field beside the selected labels, reasons and note rows. These generated artifacts are ignored
by Git; source, regressions, schema, and this report are committed.

Expected-label regressions now compare complete joined multiline runs, preserving
exact values and bounds. Exact catalogue records were refreshed for the new
wording and wrapping, and the two obsolete `full thread depth` refusal exceptions
were removed. Wrapping clears FTC-07's old pitch clash at A1 1:1, but its enlarged leaders
exceed the unchanged two-millimetre route budget. The final sheet stays A2 1:2;
the regression retains the independent one-paper-step oracle, complete
measurements and clear ink, and now requires the recorded leader-quality veto.
FTC-06's two wrapped counterbores are checked against the same unchanged route
baseline after schedules and retained geometry settle. The final route polish
freezes other annotations, uses exact physical and glyph-clearance gates, and
accepts only shorter leaders without additional overlaps or feature crossings.
Explicit dimension, view or scale edits retain their planned geometry rather than
restarting this optional polish; explicit sheet choices override the enlargement
veto. The existing placement/text controls therefore retain edit locality. The stacked-fit spindle is now A4 1:1; its clearance
assertions remain unchanged. No quality, route, tolerance or latency threshold was
relaxed, and no CI baseline was updated. The shared-process drafting audit reads
all line runs in order, with exact claims and tolerance strings. The side-only
corridor regression now requires the exact widest-line ink plus unchanged lane
and shoulder padding; it also retains the check that authored precision adds
more than 20 mm of unwrapped text. The lint independence guard is unchanged:
source designation comparison is exposed by the source-thread API and reads no
Plan or Spec.

The comparison does not imply that these source-rich fixtures are warning-free.
The final plans retain their other extraction/attachment findings. In particular,
`thumbwheel_thread_knurl` and `hw_thumbwheel_notes_add` have nine datum/FCF leader
crossing warnings on A4 (previously one and four respectively on A3). The two
string-post tap plans have one FCF/dimension crossing and three flatness-leader
feature-crossing warnings. Their manufacturing text blocks are clear. These
findings are recorded in the plans; they are not suppressed or reclassified, and
the unchanged 30-part CI quality and route gates are checked separately below.
A diagnostic explicit `compact` run produces the same A4 routes, so these are not
introduced by the new automatic small-paper preference. Forcing the thumbwheel
to A3 at 10:1 instead puts its side view off sheet and a control frame outside its
reserved area. The automatic A4 at 5:1 is retained as the complete readable fit;
the remaining datum/FCF routing issue is reported rather than hidden.

All checks use `CARGO_BUILD_JOBS=8`, `RUST_TEST_THREADS=8`,
`DRAFTWRIGHT_CORPUS=/Users/paul/repos/quiddity/tests/corpus`, and
`DRAFTWRIGHT_CORPUS_REQUIRED=1`, one verification job at a time.

| Check | Result |
| --- | --- |
| `cargo fmt --all --check` | PASS |
| `cargo clippy --workspace --all-targets --release -- -D warnings` | PASS |
| Eight grouped integration binaries | 1,255/1,258 initially passed; three one-line audit assumptions were corrected, then all 37 shared-process drafting tests passed. The final workspace run below covers all eight again. |
| Library tests before final route work | PASS: 254 tests; independence guard unchanged |
| Thread, paper, route and session focus | 50/51 passed; NIST latency outliers investigated separately below |
| Isolated NIST benchmark, unchanged 150 ms gate | PASS: final p90/max 47.382 ms (earlier quiet run 82.431 ms) |
| `tools/ci_acceptance.py --job mcp` | PASS: all four steps; 7/7 scripted tasks |
| `tools/quality.py` | PASS: all 30 parts |
| `tools/check_leader_routes.py` | PASS: 144 callouts on 30 parts; unchanged length/crossing/clearance limits, 8 unchanged pre-existing runs |
| `tools/check_quality_table.py` | PASS: 450 cells, no part worse |
| CI quality baseline comparison | PASS: 300 cells, no part worse; ink 17 → 6, lines through features 73 → 63 |
| Full workspace suite (one complete run) | 1,510/1,513 passed; one fixture staging race and two wall-latency failures, detailed below |
| Spec fixture process-isolation follow-up | PASS: all 9 Spec-note tests in separate processes |
| Doc tests | PASS: both crates, zero examples |

Earlier latency runs recorded wall-time outliers while an isolated diagnostic
measured one 625 ms edit at only 38 ms of process CPU. A quiet run of the original
benchmark passes at 47.382 ms in the final retry; its 150 ms bound remains enabled. These results are
reported separately rather than changing an assertion or treating CPU time as
the gate. The complete workspace run recorded a 404.471 ms NIST sheet-scale edit,
and a 776.804 ms multi-sheet MCP p90; both fail the unchanged 150 ms assertions.
The native multi-sheet edit p90 was 35.715 ms. An isolated multi-sheet retry also
failed its MCP gate (1,047.769 ms p90), while native edits passed at 34.116 ms.
These are recorded failures, not a claim that the full suite passed. The MCP
path includes durable filesystem writes; no persistence or timing assertion was
changed to obtain a passing result.

The remaining full-run failure was an existing test-fixture race: separate
nextest processes both used `ThreadId(1)` in an atomic staging filename. One
process renamed away the other process's temporary file. The sample helper now
uses a process-specific directory as well as its thread-specific staging file;
all original drawing assertions remain intact.

Final MCP evidence is `target/thread-callouts/acceptance-mcp/mcp/job.json`.
Quality evidence is `target/thread-callouts/quality/baseline.json` and
`target/thread-callouts/leader-routes.json`; individual check logs are in
`target/thread-callouts/checks/`. Initial quality evidence is preserved separately.
The forbidden `quality-pins`, `repeated-spans`, `render-tail`, and `plan-time`
acceptance jobs were not run. No baseline was changed and nothing was pushed.
