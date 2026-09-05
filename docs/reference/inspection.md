# Recognition evidence

`draftwright.inspect_step(path)` returns what Draftwright saw in a STEP file, without building a
drawing. Script generation writes the same document to disk beside the script it produces.

It exists so a person or an agent can find and correct two different kinds of failing:

- **recognition failings** — the recogniser found the wrong thing, or missed something; and
- **conversion failings** — recognition found it, and the drawing does nothing with it.

```python
from draftwright import inspect_step

document = inspect_step("part.step")

for entry in document["found"]:
    if not entry["draftwright"]["acted_on"]:
        print(entry["family"], entry["draftwright"]["reason"])
```

The closed schema is published as
[`draftwright-step-inspection-v1.schema.json`](draftwright-step-inspection-v1.schema.json).
`schema` is always `"draftwright-step-inspection"`; check `schema_version` before interpreting
the document. Every object is closed except `found[].feature`, which is the recogniser's own
record.

## found — what recognition produced, and what became of it

One entry per accepted feature, in the recogniser's order. Each carries two halves that must not
be confused:

- `feature` is the recogniser's record, forwarded exactly as it stated it, with `feature_type`
  and `feature_schema_version` naming its format. Draftwright never edits it. If it is wrong,
  the finding belongs upstream.
- `draftwright` is what this engine did with it. `acted_on` is the plain answer — `true` when
  the finding became an IR feature of its own or was absorbed into one. `disposition` is the
  precise one (`represented`, `absorbed`, `unsupported`, `deferred`, `evidence_only`,
  `unexpectedly_missing`), with a stable `reason` code and the `owners` it maps to.

`acted_on: false` is the conversion failing this document exists to surface: recognition found
something real and the drawing does not use it, and `reason` says why.

IDs are deterministic **within one document**, allocated from the recogniser's order and
Draftwright's IR order. They are not persistent identities and must not be stored across runs.
Provider references, topology indexes, object addresses and absolute paths are never serialized.

## missed — what nothing claimed

`unclaimed_faces` are faces no accepted feature claimed, described by surface kind, area, a
representative `position` on the face and a bounding box, with `face_count` giving the totals. This is the recogniser's own
accounting, and it is a place to start looking — **not** a defect list. Stock, background and
deliberately plain faces are unclaimed too, and they are in the denominator.

`rejected_candidates` is the other half of the story and is currently `available: false`. The
recogniser can explain what it proposed and then rejected, and which families it did not
evaluate — but only from a second recognition run, which would break the one-run rule of
ADR 3 (was 0017). b123d-recognisers#494 asks for an API that explains an already-completed result. The
field states its own absence rather than letting it read as "nothing was rejected".

## source, producer and run

`source` names the file by basename and its SHA-256. The path is resolved once and read once,
and recognition consumes a private copy of those exact bytes, so replacing a mutable or
symlinked source mid-run cannot make the document describe two different files. An absolute path
is caller-machine detail and never appears.

`producer` gives the Draftwright and recogniser versions, so a finding can be reproduced or
filed upstream against the right release.

`run` records the options that determined the content. Today that is `pmi_mode`. It matters
because PMI lowering can rewrite a grouped hole member into a singleton owner, so two runs over
identical bytes can disagree about what Draftwright did with a finding — `source.sha256` alone
would imply a reproducibility the document does not have. `inspect_step` always records `off`;
a sidecar records the mode its script was generated with. Two documents agreeing on `source`,
`producer` and `run` agree entirely.

## Beside a generated script

`generate_sheet_script(...)` — the CLI's `--script` — writes
`<stem>.draftwright-inspection.json` next to `<stem>.py`. It already reads and hashes its source
and already runs recognition once, so the document is projected from that run; there is no second
aggregate. The script itself contains none of this: it is a drawing declaration a person edits,
and evidence about the run belongs in a file that can be diffed and re-read without parsing
Python.

Pass `inspect=False` (the CLI's `--no-report`) to skip it. Skipping also **removes** any
document already at that path, because an earlier run's evidence beside a new script describes a
different part and nothing in it would say so.

Two sources produce a script but no document: a live build123d object has no STEP bytes and so
no source identity, and a source that cannot state its evidence truthfully logs a warning at
`draftwright.sheet_emit` and is skipped. A missing document never fails script generation.

## What it does not avoid

Inspection builds no drawing: no view projection, annotation placement, render, export or lint
score runs, and a guard test pins the exact set of engine modules it executes.

It is not free. Draftwright has one detect seam, shared with the drawing path, and that seam
sizes the part while detecting — it picks a page and scale, runs the dimension planner, builds
hole-callout specs and arranges views, all discarded. A leaner inspect-only path would be a
second seam whose divergence from the drawing path could not be checked; issue #1462 tracks
whether that trade should be revisited. Order of magnitude on a developer machine: a plain block
inspects in a couple of seconds, a dense AP242 part such as NIST CTC-02 in tens of seconds.

## What it never claims

Nothing here is completeness or manufacturing readiness. An empty `found` list means recognition
accepted nothing, not that the part has no features. An empty unused list means nothing found
was ignored, not that nothing was missed — `rejected_candidates` is exactly the evidence that
would speak to that, and it is not available yet. Material, process, finish, thread, fit and
tolerance intent are separately authored and never inferred here.

## Failures

- An unreadable path (missing, a directory, permissions) raises the underlying `OSError`.
- `InspectionUnavailableError` covers anything that would make the document untruthful: bytes
  that are not readable STEP geometry, a source with no solid body, an unclassified ownership
  ledger, a face that cannot be described, a non-raw recognition frame, or a value JSON cannot
  state.

Version 1 reports raw caller coordinates only. A run that recognised in a provider working frame
is refused rather than reported as caller coordinates.
