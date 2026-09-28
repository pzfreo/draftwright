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
| Scale and page | Main views use the sheet scale; each placed detail captions its resolved scale. Detail fitting accounts for the cropped silhouette, its annotation pads and caption when searching free space on the **already selected** sheet. | The detail footprint is not yet reserved before sheet/scale selection. The detail fit may select a non-preferred scale. No standards-conformance claim follows from legibility alone. |

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
