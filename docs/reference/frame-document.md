# Two-sheet frame example

The [executable recipe](../examples/frame_document.py) builds a general sheet and a feature
sheet from one shared recognition inventory. It demonstrates the supported workflow behind
the titanium-frame field report, using `Document`, authored `Sheet` views/dimensions, a
section, and compiler-owned location cells. It does not patch a renderer or position feature
annotations with page coordinates.

This is **QUOTATION / DFM REVIEW — NOT RELEASED**. It demonstrates selected bore operations,
overall dimensions and a section; it is not a complete manufacturing drawing. Missing pad,
flat and blend measurements, hinge location, unsupported pockets and unresolved engineering
decisions remain visible in the report.

## Run and inspect

From an isolated checkout/environment with the repository's locked dependencies:

```sh
uv sync --group dev --group docs
uv run python docs/examples/frame_document.py tests/fixtures/whistle_frame_reference.step build/frame-review
uv run pytest tests/test_issue_1544_frame_document_canary.py -m real_part_canary -v
```

The output directory must be new. It receives `general` and `features` PDF/SVG/PNG files,
`document.json` and `manifest.json`. The manifest records the source and recipe hashes,
package/Python versions, platform, sheet options and export options. `reproducible=True` is
an export setting, not a promise that a future version will draw the same picture. The canary
checks physical identities and measurement meanings rather than a cross-version image digest.

Open both exported sheets. Check the cut material and upward cutting arrows on section A–A,
the counted seat callout and axis locations on the general sheet, and the three grouped
bore callouts, location table and preliminary-status notes on the feature sheet. The feature table
uses X/Y/Z distances from the model's minimum datum planes; the axis column identifies each
operation's drilling axis. Ordinary annotation placement and table fitting remain the engine's
responsibility. A table that does not fit must be reported, not compressed or overlapped.

Read the document assessment alongside the drawings. Per-sheet missing-measurement warnings
are expected where the other sheet supplies the measurement. Package coverage still includes
all **71 known obligations**, including six unsupported pockets and eighteen circular-seat
requirements. With Quiddity 0.2.9 and the circular-seat grammar, the recipe credits 31 of those obligations; the remainder must not disappear merely because a sparse sheet has
few overlaps. Coverage, layout, fidelity and manufacturing readiness are separate judgments.
Unmeasured section furniture and notes also remain explicit in the fidelity unknowns.

## Original printed-frame regression input

`tests/fixtures/issue_1595_whistle_key_frame.step` preserves the exact fixture supplied on
[the #1595 fixture branch](https://github.com/pzfreo/draftwright/commit/4aaa610ca62d6851b6e9c990a4b16aae0f0c8f97).
It was generated from `pzfreo/whistle-key` at `7fb4d14`, from
`exports/three-key/print-oriented/frame_print.step`. Its SHA-256 is
`8f060dfd4eeb4ff9589243f85eea8ca027784ac51ae98dc2d29a2371a2d115d0`.

Keep its acceptance separate from the titanium recipe's reference:

| Input | Envelope (mm) | Seat run intervals along Y (mm) |
|---|---|---|
| Original printed frame | 31.3 × 71.595 × 15.8 | −35.297…−29.297, −10.923…−4.923, 29.297…35.297 |
| Titanium reference | 31.3 × 71.595 × 15.3 | −3…3, 21.375…27.375, 61.595…67.595 |

Both published Quiddity 0.2.9 inventories contain three circular seats and six hexagonal
blind pockets. The printed variant also contains three additional rectangular channel
records. Coordinates and dimensions from one variant must not silently become the other's
expected facts. The recipe above remains specific to the titanium input.

## Fixed operation facts and deliberate limits

The pinned part has these recognized operations:

| Operation | Observed identity | Authored representation |
| --- | --- | --- |
| Through bolts | Six Z-axis Ø2.4 bores, depth 1.6 mm, with one grouped owner | One six-member quantity/diameter callout and six pairs of member coordinates |
| Blind sockets | Three X-axis Ø2.4 bores, depth 1.5 mm | One three-member callout with depth and three pairs of member coordinates |
| Hinge bores | Six Y-axis Ø1.1 through bores, each 3.0 mm deep, with one grouped owner | One six-member diameter callout and an uncredited engineering-decision note |

These are plain circular bore operations: counterbores, spotfaces, countersinks, threads and
profile modifiers are absent. The recipe refuses changed operation semantics instead of
selecting by diameter alone. The canary independently checks member centres and complete
attributes, not merely the number of features recognition returned.

The hinge traverses six interrupted bearing segments. Quiddity 0.2.9 supplies six exact
physical bore occurrences; Draftwright groups their equal machining specifications without
joining the air gaps. The pinned datum-aligned fixture has 3.0 mm lands, unlike the 2.7 mm
lands described for the original printed-part report. The note
`SIX BEARING SEGMENTS - FIT / PIN RETENTION TBD` has no `satisfies=` declaration and earns
no additional coverage. This recipe still leaves the hinge's transverse location and each
bearing's axial placement unresolved; correct occurrence identity alone does not state them.
The general sheet states the three cylindrical seats with one counted diameter/run/arc
callout and datum-to-axis coordinates. Centre marks identify the virtual cylinder axis;
the leader targets the physical curved wall. Each seat retains its own six requirements
and source occurrence even where the coaxial seats share dimensional ink (#1613).

All six underside hex pockets stay in the denominator with unsupported dispositions. Their
ordinary table contains author-supplied descriptive text and has no measured-cell authority.
Adding nominal AF/depth numbers to that text would not prove them. Titanium grade, pocket
manufacturing detail, hinge fit and pin retention remain engineering decisions.

## Replay and measurement transfer

Keep the STEP, this Python recipe and the manifest. Replay by executing the recipe and
resolving the current exact feature owners with its operation assertions. Report owner,
occurrence, requirement and sheet IDs are local to that document run; do not deserialize them
as durable feature identities or union independently built Drawing/PDF reports.

Edit source declarations to choose which sheet carries a measurement. For live inspection,
the canary declares the hinge diameter on both sheets, removes the general-sheet instance to
establish a feature-only baseline, restores it through the public `callout()` verb and removes
the feature-sheet instance. It compares exact requirement membership throughout. Removing the
last instance must lose coverage while retaining the same physical obligation. Repeating a
measurement across sheets earns one credit, not two; conflicting meanings retain both claims.

The recipe and the canary are complementary: the recipe is the editable source; the canary
checks its physical facts, actual placed claims, section hatch/arrow geometry, coverage loss
and production exports. Neither certifies manufacturing readiness or unrecognized geometry.

## Fixture provenance

The fixture is the datum-aligned `drawings/titanium/frame-reference.step` from
[`pzfreo/whistle-key@76ccdc2`](https://github.com/pzfreo/whistle-key/blob/76ccdc2/drawings/titanium/frame-reference.step),
retained locally as `tests/fixtures/whistle_frame_reference.step` so CI needs no download.
Its SHA-256 is
`078d0abd52b618a1a9180bb27a162a8fd1b8ae1a74a52571adfdbf3914f23d71`.
The hash pins the input, not the generated drawing. A different part requires a deliberate
recipe and expectation review rather than changing the hash until the test passes.
