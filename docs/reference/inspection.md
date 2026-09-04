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
