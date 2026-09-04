# Read-only STEP inspection

`draftwright.inspect_step(path)` answers one question — *what does Draftwright actually see in
this STEP file?* — and returns a versioned, strict JSON-compatible evidence document. It builds
no drawing: there is no view projection, annotation placement, render, export, or physical lint
score anywhere on the path.

```python
import json

from draftwright import inspect_step

document = inspect_step("part.step")
print(json.dumps(document, indent=2, ensure_ascii=False))
```

The closed schema is published as
[`draftwright-step-inspection-v1.schema.json`](draftwright-step-inspection-v1.schema.json).
`schema` is always `"draftwright-step-inspection"`; consumers must check `schema_version` before
interpreting the document. Every object is closed except the producer-owned payload containers
`record` (the public recogniser record) and each entry of `pmi.records`. Adding a field to a
closed object, changing a meaning, or removing a field requires a new schema version.

This is an ordinary Python contract. A later MCP tool is a thin adapter over it and re-derives
none of the evidence below.

## What each section is evidence *of*

The document deliberately keeps four different kinds of fact apart, so an automated caller can
never read one as another. Each section states its own `provenance` and `coverage`.

| Section | Provenance | What it is |
| --- | --- | --- |
| `geometry` | `step-source` | Measured STEP geometry: bbox, volume, topology census |
| `recognition` | `recogniser-inference` | Accepted occurrences and Draftwright's consumer disposition for each |
| `recognition.association` | `recogniser-evidence` | The provider's own face/area association accounting |
| `pmi` | `step-ap242-source` | Semantic PMI authored in the source document |

`geometry` covers the solid body — the same geometry the engine would draw, with AP242
presentation wires and construction curves excluded. All values are in caller coordinates, in the
units named by `units` (`mm`, `mm²`, `mm³`, degrees).

## Source identity

The path is resolved once and read once. `source.sha256` and `source.artifact_id` are taken from
that exact immutable byte snapshot, and geometry, recognition, and PMI extraction all consume a
private copy of those same bytes. Replacing a mutable source — or retargeting a symlink — while
an inspection is in flight therefore cannot split the three sections across two different files.

`source.name` is the basename only. An absolute path is caller-machine detail, not evidence, and
never reaches the document.

## Where the document comes from

There are two front doors, and they produce the same document for the same bytes.

`inspect_step(path)` does its own byte snapshot and its own single detect run, always with PMI
lowering off.

Script generation writes it as a sidecar. `generate_sheet_script(...)` — the CLI's `--script` —
already snapshots and hashes its STEP source and already performs one detect run, so it projects
the document from *that* run and writes `<stem>.draftwright-inspection.json` beside `<stem>.py`.
There is no second aggregate. Pass `inspect=False` (the CLI's `--no-report`) to skip it.

The generated `.py` also embeds `DRAFTWRIGHT_RECOGNITION_SNAPSHOT`, which is **not** the same
thing: that block carries only the actionable gaps — `unsupported`, `deferred`, `evidence_only`,
`unexpectedly_missing` — while the sidecar carries the whole document.

Two sources produce a script but no sidecar. A live build123d object has no STEP bytes, so it
cannot have a version-1 *STEP* inspection document at all. A source that cannot state its
evidence truthfully — no solid body, an unclassified ownership ledger — logs a warning at
`draftwright.sheet_emit` and is skipped; a missing sidecar never fails script generation.

`recognition.pmi_mode` records the mode the aggregate ran under. `inspect_step` is always `off`;
script generation passes the caller's `--pmi` through. It matters because with PMI in play the
ownership rewrite can turn a grouped hole member into a singleton owner (`pmi_split_member`), so
a reader must be able to tell whether the ownership below came from geometry alone.

## Recognition evidence

Exactly one aggregate recognition run happens, and its evidence, model, and conversion-time
ownership are reused as-is. No section re-recognises, and nothing reconstructs ownership from
record values, labels, or a second scan.

Occurrence and owner IDs are deterministic **within one document** — allocated from the
provider's accepted-occurrence order and Draftwright's final IR order. They are not persistent
topology IDs and must not be stored as identities across runs; `identity_scope` says so. Two
equal-valued occurrences stay separate rows in provider order. `FeatureRef`, `FaceRef`, topology
indexes, object addresses, and page coordinates are never serialized.

Each occurrence carries its consumer `disposition` — `represented`, `absorbed`, `unsupported`,
`deferred`, `evidence_only`, or `unexpectedly_missing` — with the same meanings as
[the drawing report](reports.md). An unclassified ownership ledger is refused with
`InspectionUnavailableError` rather than quietly shrinking the occurrence denominator.

`faces.defining` and `faces.constituent` describe the exact original faces that establish and
physically belong to each occurrence, as bounded geometry: surface kind, area, centroid, and
bounding box. They are ordered by their own serialized values, because the provider hands them
back unordered.

## Association accounting, and what `unassociated` does not mean

`recognition.association` projects the provider's accounting of accepted constituent evidence
against the original faces. `unassociated` means exactly one thing: **no accepted occurrence
claimed that face**. Stock, background, and deliberately plain faces are in the denominator, so
an unassociated face is not evidence of a missed feature — `unassociated.qualifier` states that
in the document itself. `ratio` is association coverage, never an accuracy, recall, or
correctness score. Family contributions overlap and are not additive.

## PMI

`pmi` is the complete AP242 source census, the records Draftwright could lower from it, and the
extraction error state. `status` is `present`, `absent`, or `extraction_error`. Every source
entity keeps its own `outcome` — `extracted`, `partially_extracted`, `presentation_only`, or
`not_extracted` — so no source entity disappears merely because Draftwright cannot lower it, and
`sources` is routinely longer than `records`.

PMI is read but never lowered into the IR here, so an authored annotation cannot change which
occurrence owns a feature. Recognition stays geometry-only (ADR 0013).

## Status, and what a clear document does not claim

`status` is `needs-attention` when any occurrence is `unsupported`, `deferred`, `evidence_only`,
or `unexpectedly_missing`, or when PMI extraction failed or left a source entity unlowered or
only partly lowered.

Otherwise it is `bounded-recognition-evidence` — and that name is the claim. It is **not**
physical completeness and **not** manufacturing readiness: recognition can miss physical
geometry, and material, process, finish, thread, fit, and tolerance intent all remain separately
authored facts the document never invents. The `qualifiers` array repeats those limits as stable
codes a caller can branch on.

## What it does not avoid

Inspection builds no drawing: no view projection, annotation placement, render, export, or
physical lint score runs, and a guard test pins the exact set of engine modules an inspection
executes so that cannot change unnoticed.

It is not free, though. Draftwright deliberately has **one** detect seam, shared with the drawing
path, and that seam sizes the part while detecting: it picks a page and scale, runs the dimension
planner, builds hole-callout specs, and arranges views. None of that reaches the document, and
all of it is discarded. A leaner inspect-only path would be a second seam whose divergence from
the drawing path could not be checked, so the cost is accepted rather than overlooked — see ADR 0021
in the repository. In practice a plain block inspects in about
1.5 s and a dense AP242 part such as NIST CTC-02 in about 14 s.

## Version 1 boundaries

Version 1 is raw/caller-coordinate only. It refuses a run that recognised in a provider working
frame rather than silently reporting working-frame values as caller coordinates; framed
inspection waits on the one-run refusal contract in b123d-recognisers#493, and will state its
caller/working coordinate provenance explicitly when it lands.

## Failures

`inspect_step` fails before returning a misleading partial document.

- An unreadable path (missing, a directory, permissions) raises the underlying `OSError`.
- `InspectionUnavailableError` covers everything else that would make the document untruthful:
  bytes that are not readable STEP geometry, a source with no solid body, an unclassified
  occurrence ownership ledger, an absent recognition aggregate, or a non-raw recognition frame.
