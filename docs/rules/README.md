# Rules catalogue

This catalogue lists what the Python draftwright learnt, in drafting terms rather than code, so the
port can track what it owes ([plan](../plan.md); ADR R4 will cite it). It was extracted from
pzfreo/draftwright at `586628b96db6779454d499c7d92d37f1aa8f1df9`.

| File | Source | Entries | Drafting | Trust | Spec | Machinery |
| --- | --- | --- | --- | --- | --- | --- |
| `adr.yaml` | live ADRs 1–5 (invariants, decisions, unguarded rules) and archived ADRs (rules not already live) | 160 | 57 | 61 | 13 | 29 |
| `lint.yaml` | `linting/`: per-feature coverage requirements and every issue code | 166 | 122 | 33 | 0 | 11 |
| `issues.yaml` | the 142 issue-numbered regression test files (`test_issue_*.py`) | 190 | 93 | 85 | 3 | 9 |
| **All** | | **516** | **272** | **179** | **16** | **49** |

Status after the maintainer-approved triage of 2026-10-16, including the earlier Stage G/H
and K1b/K4/K5/K7 integrations, merged with PR #56 (datum placement) and PR #57
(K6, one owner per measurement), PR #61 (datum follow-up), the pins/lint batch
(nine rules ported with tests), and PR #64 (table-rule ports). The table-rule batch adds
verified schedule and manufacturing evidence, transactional hole-table guards and
drawing-consumer checks. The PMI/source and precision batch retains its six verified ports
(exact aligned PMI, source-site fidelity, extent bands and fits, display precision and structured
feature-note authority). The coverage/holes stream retains its nine ported rules (body-local
corridors, prismatic shoulders, countersink seats, compound double-D callouts, pattern
counts and balloon assignment). Automatic profile-angle placement, first withdrawn after
failing the unchanged drawing-quality bar, is now ported (below). The oriented-chamfer consumer adds a bounded
symmetric V-notch grammar, with true included angles, theoretical sharp depths and datum
locations; it reuses the existing C-notation and ownership rules. It ports no additional full
Python catalogue rule, so the counts below remain unchanged. The rules-text merge retains its two completed
ports and pending blockers ([merge report](text-main-merge-2026-10-16.md)).
The views-and-layout batch retains six verified ports: ARCH0004-3, ARCH0018-1,
ISSUE-563-b, ISSUE-1262-a, ISSUE-1530-a and ISSUE-1604-a. ISSUE-563-b includes the
maintainer-approved reservation of every linear corridor, permitting smaller scales.
The detail-view integration also ports ISSUE-443-a, ISSUE-1216-b and
LINT-code-detail_caption_clearance with executable tests.
The radius-leader fix (#145) adds a Rust drafting rule for radial approach from either side; unavailable sections reuse the existing target-unverifiable code as info.
The holes-threads stream (2026-10-23) ports ISSUE-1298-a: the tap drill's refusals are tested and a manufacturing requirements table with no place on the sheet falls back to full callouts, as draftwright does. LINT-hole_coverage-thread_and_profile has its Python half (an authored thread depth) tested; its modelled-thread half was later retired (below).
The leaders-tips stream ports ARCH0017-6, ISSUE-1374-a, ISSUE-1374-d and ISSUE-1276-a: lint
checks flat, recess and slot leader tips against the faces their records publish (never an
open mouth), a turned length's witness stations against its band's shoulders, and a chamfer,
turned diameter or turned length whose leader or witness misses its feature loses its coverage
credit; a knurl note is its band's diameter callout, so its leader lands on the knurled surface.
The Rust [section-depth attachment rule](section-witness-attachment.md) treats detached
witnesses as incorrect measurement application: an error with incomplete impact.
It adds no Python catalogue entry, so the catalogue counts remain unchanged.

Counts are computed from the merged YAML entries:

| File | Pending | Ported | Translated | Retired |
| --- | --- | --- | --- | --- |
| `adr.yaml` | 2 | 71 | 61 | 26 |
| `lint.yaml` | 0 | 132 | 14 | 20 |
| `issues.yaml` | 0 | 114 | 50 | 26 |
| **All** | **2** | **317** | **125** | **72** |

The evaluation of 2026-10-22 re-read the 30 rules then pending against main (alpha.12) and
closed 17. Two were met in full (LINT-code-pmi_source_text_mismatch, ISSUE-1143-e) and 14 are
ported for what Rust implements, each entry's `rust_note` naming the clauses retired and why
(for example the Escalation object, stroke width, per-family term parsers and unreachable
leader-geometry clauses). LINT-code-leader_shaft_unverified is retired, since typed drawings
always carry their leader lines and view transforms. ISSUE-958-a (owned by quiddity) and
ARCH0020-1 (waiting on the quiddity 0.1.0 upgrade) stay pending with updated notes, and the
notes on the two remaining PORT rules were corrected. Every cited test was checked to exist.
ISSUE-958-a was later ported: its one-against-two channel count was a fixture that held a
single solid (quiddity-rust #73, closed), now replaced. What still depends on quiddity:
ARCH0020-1 on #69 to #72,
and ISSUE-1143-e's NIST CTC-04 case (pocket-rim chamfer corners misread as countersink seats)
on #76.

The second one-owner stream (2026-10-23) ports compound counts and lone blind pockets into
the support-plane walk: counted labels and compound callouts state their members' intervals,
and a face-level rung they determine yields. ADR1-8 and ADR4-U1 gain tests of the new
behaviour and ISSUE-1505-b a test pinning existing behaviour; all three stay pending, so the counts are unchanged. ADR1-8 still lacks step legs, the other recess and
hole depths and rebased ladder datums. ADR4-U1's angular sets and ISSUE-1505-b's off-page Z
recovery and cut caps were then proposed for retirement.

The maintainer approved four retirements on 2026-10-23: ADR4-U1's angular sets, ISSUE-1505-b's
off-page Z recovery and solid Boolean cut caps, and the modelled-thread half of
LINT-hole_coverage-thread_and_profile, whose lint (a depth asked of a thread recognised from
modelled geometry) is removed. The three rules are ported, each `rust_note` naming the retired
clauses and why. ADR1-8, ARCH0020-1 and LINT-angular-profile_angle_requirement remained pending.

The profile-angle stream (2026-10-23) ports LINT-angular-profile_angle_requirement on the
design the maintainer agreed (ISO 129-1, ASME Y14.5): a corner is satisfied when its angle is
stated or its geometry is fixed by drawn linear dimensions; a closed polygon's equal corners
take one counted angle (`8× 135°`) and two mirror-equal corners one `2× θ°`; angles are placed
only local to their corner and clear, else dropped and still reported; `faces/<face>/profile_corners`
(including `linear`) and per-corner `dimensions/<id>` overrides steer it. ADR1-8 and ARCH0020-1 remain pending.

The third one-owner stream (2026-10-24) ports through-step legs and blind depths into the
support-plane walk: a leg is walked last and yields to the plate, rung or overall that states
it; circular-step, hex-pocket, rectangular blind-slot and flat-bottomed hole depths are callout
members that carry chains, so a rung or boss height they state or close yields. ADR1-8 stays
pending, so the counts are unchanged: hole point ordinates were tried and backed out (an
ordinate ends on an axis, not a face), and rebased ladder datums are blocked on lint reading a
rung's interval from the box.

The triage covered all 190 rules then pending: 80 PORT, 98 TRANSLATE and 12 RETIRE.
The original PORT triage recorded value, size and stream in `port_note`. Of the TRANSLATE
group, seven rules already met in Rust are marked ported with verified test citations:
ARCH0015-1, LINT-_registry-structured_note_satisfaction, LINT-schedule_evidence-verified_cells,
LINT-turned_step_coverage-length_and_od, ISSUE-917-a, ISSUE-1215-f and ISSUE-1296-a.
At triage, the other 91 were translated into the Rust model; the 12 retirements carry the
approved reason.

PR #57 now supplies the K6 evidence that was unavailable at triage: ARCH0015-2 and
ISSUE-1154-a are ported, and ADR1-8, ADR4-U1 and ISSUE-1505-b gain evidence for their
implemented portions. ISSUE-1505-a kept K6's updated assessment; the 2026-10-22 evaluation
ported it with the decoration clause retired.
The table-rule batch keeps six verified ported entries, promoting four beyond main's triage:
LINT-code-manufacturing_reference_unresolved, ISSUE-1144-a, ISSUE-1202-a and ISSUE-1543-c.
The approved triage is otherwise retained, including ISSUE-883-a's translation. Test
citations name files and tests found in the merged checkout.

The gear batch ports all eight selected gear rules and promotes the earlier
`gear_semantics_missing` translation to its exact Python diagnostic. Evidence is in
`tests/gears.rs`, including the real 13-tooth wheel and a smooth declared blank.

The [pins/lint batch evidence](pins-lint-2026-10-16.md) records its regression pins and
implementation checks. The [main merge report](pins-lint-main-merge-2026-10-16.md) lists
retained batch statuses, main's catalogue decisions and merged validation results.
The [PMI/precision merge report](pmi-precision-main-merge-2026-10-16.md) records this batch’s
six retained ports, the entries inherited from main, final counts and required checks.
The [coverage/holes merge report](coverage-holes-main-merge-2026-10-16.md) records its nine
retained ports, main's remaining decisions, final counts and validation results.

The [fit-label integration report](pmi-precision-fit-main-merge-2026-10-16.md) records the
latest merge, composed precision/fit behavior, catalogue audit and validation.

## An entry

```yaml
- id: ADR5-1                      # ADR<n>-<k>, ADR<n>-D<k> (decision), ADR<n>-U<k> (unguarded), ARCH<nnnn>-<k>,
                                  # LINT-<module>-<slug>, ISSUE-<n>-<letter>
  statement: "Same input, same sheet, every platform."
  detail: "…"                     # optional
  class: trust                    # drafting | trust | spec | machinery
  source: { adr: "…", lint_code: "…", module: "…", issues: [1196], tests: ["…"], corpus_parts: ["…"] }
  status: pending                 # pending | ported | translated | retired
  port_note: "Keep: …"            # the extractor's suggestion
  owner: draftwright              # set when ported, for recognition lessons: quiddity | draftwright
```

- **drafting:** what the sheet must say, show or avoid. These are ported as checks on the plan
  or the rendered sheet, or as scenario tests (a STEP and a spec in, a property of the plan or
  its problems out).
- **trust:** determinism, honest failure, provenance, lint independence and reporting. These are
  kept.
- **spec:** rules the Spec and its edit operations must uphold. Examples: it carries no page
  geometry, edits address items by stable ID, and a view request is distinct from the planned
  view. These are guarded by the Spec's schema, `edit`'s validation and tests.
- **machinery:** rules about the Python compiler's own structure. These are retired, or
  translated into the Analysis/Spec/Plan model.

When a rule is ported, its status becomes `ported` and it gains `rust_test:`, the test that
checks it. A rule ported only in part stays `pending`, with `rust_test:` for the part that is
checked and `rust_note:` saying what is ported and what is not. A rule whose unimplemented
clauses are retired by a dated decision is `ported`, and its `rust_note:` names each retired
clause and why (2026-10-22 evaluation). When it is translated, it gains
`rust_rule:`, the rule it became, and `rust_note:` describing its Rust mechanism and limits,
with `rust_test:` when a test or test suite is named. When it is retired, it gains `why:`;
the 2026-10-16 triage also records the reason and dated maintainer approval in `rust_note:`.
A rule ported in full may also carry `rust_note:`, saying what the port does differently.
Multiple test citations use a semicolon-separated scalar, as in the existing catalogue.

## Review

The extraction was done by agents reading the Python. Every entry cites its source, and the
citations were checked against origin/main: tests exist, and IDs are unique. The
classifications and port notes are judgement calls. Settled with the maintainer (2026-10-07):
- **The `spec` class:** the edit-surface rules were moved out of machinery into it (16 rules:
  ADR2-D7, ADR2-11, ADR3-6, ADR4-D1, ADR4-D3, ADR4-D4, ADR4-1, ADR4-5, ADR4-11, ADR4-14,
  ARCH0011-1, ARCH0018-6, ARCH0018-7, ISSUE-1000-a, ISSUE-1350-a, ISSUE-1432-c).
- **`ADR5-9`**, "serialises no identity meant to survive another run", forbids recognition and
  topology identities. The Spec's draftwright-owned IDs are allowed within one file. Across
  revisions the Spec carries quiddity's fingerprints (ADR R3), never face indices.

- **Recognition lessons:** the split stands.
  - Lessons about not claiming an unproven feature are classed trust.
  - Lessons about how a recognised feature is dimensioned are classed drafting.
  - As each "don't claim it" lesson is ported, it gains `owner:`:
    - `quiddity` when recognition itself should refuse the feature, with its test in
      quiddity-rust;
    - `draftwright` when the drawing must say that recognition refused or was unsure, rather
      than guess.

H7 (2026-10-12) revived ADR2-17 and LINT-code-interior_label_on_narrow_material
with tests for the implemented portion; both were retired only because the port formerly
placed no interior labels. The 2026-10-16 triage translates ADR2-17's placement rule;
the independent narrow-material lint check was retired in the 2026-10-22 evaluation.

The native recess-reader batch ports five pending recess rules and verifies four
previously bridge-only rules on recognition/3. See `tests/native_recesses.rs`.
