# Detail and section conventions: implemented subset and limits (#1872)

This is an implementation inventory, **not a standards-conformance claim**. It
separates observable Draftwright behavior from the clauses that still require a
licensed standards review. In particular, a cropped detail is not labelled or
rendered as a section.

## Reference editions and evidence boundary

- [ISO 128-3:2022](https://www.iso.org/standard/83356.html) is the published
  edition for presenting views, sections and cuts. Its public abstract establishes
  that scope, but does not establish that any specific Draftwright glyph, cut,
  caption or crop follows a detailed clause. ISO 128-44:2001 and ISO 128-50:2001,
  previously cited in code comments, are [withdrawn](https://www.iso.org/standard/33943.html)
  [editions](https://www.iso.org/standard/24240.html); their names must not be used
  as evidence of current conformance.
- [ISO 5455:1979](https://www.iso.org/standard/11500.html) is the published scale
  reference. Its public description addresses recommended scales and scale
  designation. Draftwright uses an explicit scale caption, but the detail fitter
  can choose a continuous half-sheet-scale step when a preferred initial
  enlargement will not fit; we do **not** claim every resolved detail scale is
  in the standard's preferred series.
- [ASME Y14.3-2012 (R2024)](https://www.asme.org/codes-standards/find-codes-standards/orthographic-and-pictorial-views)
  is listed by ASME as *Orthographic and Pictorial Views*. The public product
  description states its scope, not the detailed local-detail or section rules.
  The previous *Multiview and Sectional-View Drawings* title belongs to an
  earlier edition and should not be used as the current citation.

## What Draftwright currently does

| Representation | Verified implementation | Limit |
| --- | --- | --- |
| Enlarged detail | A source-direction projection of a Boolean-cropped solid. Primary and optional secondary bounds remain model-space; witness and physical-support checks reject an over-tight crop. The caption carries `DETAIL`, an identifier and the actual scale. It adds `PARTIAL PROFILE` only when the secondary crop truncates the original body envelope. | The marker and caption describe a detail, not a cutting plane. A crop that lacks enough source support falls back to fuller geometry or is refused. There is no general proof of minimum sufficient context. |
| Full section | A Y-normal cut through a planned row, projected as a distinct section. The plan view carries a named cutting-plane line, end arrows and letters; the cut faces receive 45-degree hatch. A skipped section has a named reason and lint finding. | The section retains the full X/Z envelope. It does not plan a minimum local region, an arbitrary cutting-plane orientation or local-section break boundary. |
| Scale and page | Main views use the sheet scale; each placed detail captions its resolved scale. Detail fitting accounts for the cropped silhouette, its annotation pads and caption. A single unambiguous approved Y-step chain now has a pre-sheet minimum-footprint demand, a hard in-pass reservation, and measured post-render containment. | Other detail/section families are not yet pre-sheet planned. A fixed page can still refuse a genuinely unfit required detail. The detail fit may select a non-preferred scale. No standards-conformance claim follows from legibility alone. |

Implementation evidence: `DetailRequest`, `supported_secondary_crop` and
`_detail_caption` in `src/draftwright/_core.py`; `_render_detail`,
`_add_section_view` and `_place_cutting_plane` in
`src/draftwright/annotations/sections.py`; `plan_sections` in
`src/draftwright/model/planner.py`. Tests include
`tests/test_detail_views.py`, `tests/test_issue_1190_section_decision.py` and
`tests/test_issue_1530_section_provenance.py`.

## Remaining acceptance work

1. Plan the minimum sufficient *detail and section* model region from controlled
   features and required witness/attachment geometry, with bounded context;
   keep a fuller view or structured refusal when the local region is ambiguous.
2. Reserve the post-crop view, annotation and caption footprint before selecting
   sheet and scale, then check the finished projection and requirement coverage.
3. Review the licensed current ISO and ASME texts clause by clause with a
   qualified drafting reviewer before claiming a supported standards subset.
   Add convention-level visual/geometry tests for any clause Draftwright claims.

The context margin, crop preference, and decision to say `PARTIAL PROFILE` are
Draftwright policies. They are not attributed to ISO or ASME here.

## Planning contract for the remaining implementation

The pre-sheet result must be a *derived-view demand*, not a second rendered
`Drawing`. It is compiled from the same approved measurements and feature
support that the detail or section renderer will consume. Each demand names its
source view, controlled measurement identities, model-space axial and secondary
extent, minimum legible detail scale, bounded annotation pads, caption footprint,
and whether the view is full, partial/detail, or a true section. A demand whose
required witness/support geometry cannot be established remains explicitly
unplanned; it must not be treated as zero-size optional furniture.

The sequence is:

1. Compile derived-view demands alongside the ordinary dimension/view plan,
   before `choose_scale`. Share the chain-legibility and crop-support policy with
   the renderer; do not duplicate a second set of feature-family thresholds in
   `analysis.py`.
2. For each candidate `(view set, scale, page, arrangement)`, convert each
   demand's **post-crop** model span and paper-space pads/caption into a box.
   Reserve those boxes against principal-view footprints, title block, tables,
   and isometric view in the same compose-then-pack decision. Spend only the
   *remaining* slack on the preferred 12 mm view gutter; the 6 mm safety gutter
   remains a hard floor. Search uses box arithmetic, never an OCC bbox.
3. Carry the chosen reservation and its semantic identity into the one render
   pass. Ordinary annotation placement treats it as occupied; `_render_detail`
   or the section renderer projects once and validates the real cropped geometry,
   witnesses, annotation ink and caption inside the reserved footprint. A
   measured mismatch enters the existing bounded repack/refusal path, not a
   second annotation engine or a silent crop relaxation.
4. Record planned-versus-measured footprint and any refusal on
   `detail_decisions`/section decisions and in exported evidence. An approved
   dimension left without a view remains a named withheld outcome; automatic
   sheet selection must not call that result complete merely because the
   principal blocks fit. Explicit-scale builds retain their existing
   caller-constraint policy: a `step_dim_withheld` finding is reported by lint,
   but does not itself make the requested scale a rejected placement outcome.

The current `test_pre_drain_y_diameter_uses_the_shared_analytical_producer_floor`
is a useful adversarial fixture: at fixed A4/1:1 it needs a 108.16 × 44.19 mm
Y-chain detail, but the settled layout leaves only 102.96 × 45.30 mm. It must
keep the exact 4 and 6 mm step measurements, or report genuine infeasibility
under the caller's fixed constraints. It must not be made green by weakening
the measurement assertion or by stealing the minimum gutter. GRM03 and a
non-turned detail/section remain separate acceptance fixtures; success on this
Y-chain alone is not completion of #1872.

The first pre-sheet probe found a further constraint: a box that fits when
principal views are represented by padded silhouettes can overlap the side
view's dimension and leader bands. The apparent five-millimetre shortage does
**not** prove that preferred gutter space alone solves this fixture. Derived
views must be packed against full planned view blocks and their reserved boxes
must remain hard occupancy for ordinary annotation placement. A prototype
that changes only the scale/page verdict is insufficient. The first Y-chain
producer is wired through hard occupancy and measured validation; other
families remain unplanned. The fixed-A4 adversarial fixture exposes a harder
tradeoff: its exact measured 108.16 × 44.19 mm detail box can be reserved and
recovers the 4 and 6 mm steps. Reserving the front-below Y-hole leader band
also retains the ø4 callout. However, two plan-view pad-length dimensions then
drop with `pad_dim_dropped`/`strip_full`. Although the pads have equal nominal
sizes, no grouped or quantified carrier proves those two feature requirements
are represented by the surviving dimensions. Passing the step and callout
assertions is therefore not a passing semantic result. The conflict needs a
requirement-aware placement/page choice, not unbounded overlap or raw placement.
