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
the four bore callouts, the location table and the preliminary-status notes. The feature table
uses X/Y/Z distances from the model's minimum datum planes; the axis column identifies each
operation's drilling axis. Ordinary annotation placement and table fitting remain the engine's
responsibility. A table that does not fit must be reported, not compressed or overlapped.

Read the document assessment alongside the drawings. Per-sheet missing-measurement warnings
are expected where the other sheet supplies the measurement. Package coverage still includes
all **58 known obligations**, including six unsupported pockets. The initial recipe credits
15 of those obligations; the remainder must not disappear merely because a sparse sheet has
few overlaps. Coverage, layout, fidelity and manufacturing readiness are separate judgments.
Unmeasured section furniture and notes also remain explicit in the fidelity unknowns.

## Fixed operation facts and deliberate limits

The pinned part has these recognized operations:

| Operation | Observed identity | Authored representation |
| --- | --- | --- |
| Through bolts | Six Z-axis Ø2.4 bores, depth 1.6 mm, represented by existing owners of four and two members | Separate quantity/diameter callouts and six pairs of member coordinates |
| Blind sockets | Three X-axis Ø2.4 bores, depth 1.5 mm | One three-member callout with depth and three pairs of member coordinates |
| Hinge bore-path | One Y-axis Ø1.1 through path, depth 53.2 mm | One diameter callout and an uncredited six-bearing-segment note |

These are plain circular bore operations: counterbores, spotfaces, countersinks, threads and
profile modifiers are absent. The recipe refuses changed operation semantics instead of
selecting by diameter alone. The canary independently checks member centres and complete
attributes, not merely the number of features recognition returned.

The hinge traverses six interrupted bearing segments. Quiddity currently supplies one path
owner, not six segment owners. The note `SIX BEARING SEGMENTS - FIT / PIN RETENTION TBD` has no
`satisfies=` declaration and earns no additional coverage. This recipe intentionally leaves the
path's transverse location unresolved. Even supplying that location would not locate or
tolerance each of the six axial intervals; those need provider-issued segment identity.

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
