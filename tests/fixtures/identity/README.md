# Identity fixtures

What `tests/identity.rs` checks stable identity against (the editor thin slice's stream S1).

- `../parts/mounting_plate.step`: the editor's mounting plate, byte for byte
  (draftwright-editor `public/samples/plate.step` at e15f1a2, branch thin-slice; sha256
  `0336b397…781e`), a slice part (the thin-slice plan, section 3.1).
- `plate_intent.step.gz`: specify-core-rust v0.1.0-alpha.1 (4a361b1) writing its defaults onto
  the mounting plate, the material answered (`plate_intent.answers.json`); 21 PMI records
  (datums, basic locations, a fit, two positions, a thread, the sheet's defaults). sha256 of the
  STEP text `76a2bdfe…4559`.
- `plate_intent_rewritten.step.gz`: the same intent written again by the same build onto
  `plate_intent.step` (specify-core's Replace mode, as it wrote that file): the written intent is
  the same (its `--intent` JSON differs only in the binding's sha256), every record has a new
  Part 21 instance (e.g. datum A `#1023` → `#1252`). sha256 `d2fae995…1d41`.

  ```text
  specify-core analyse -o a.json plate.step
  specify-core write -o plate_intent.step --accept-defaults plate.step a.json plate_intent.answers.json
  specify-core analyse -o a2.json plate_intent.step
  specify-core write -o plate_intent_rewritten.step --accept-defaults plate_intent.step a2.json plate_intent.answers.json
  ```
- `<part>.spec1.json`: a `draftwright.spec/1` spec, keyed as that version keys dimensions
  (`@<view>`, a PMI dimension by its record's Part 21 instance). The plate uses
  `tests/fixtures/parts/plate.step` with `--features tests/fixtures/recognition/plate.json`;
  `bracket_positions` uses `../pmi/step/bracket_positions.step.gz` with Rust quiddity's
  features. Each omits a view, so a version 1 key names the view its dimension moved to.
  Both parts have changed geometry since 0.1.0-alpha.1. The migration test compares their
  complete current plans with independently renamed current-Spec inputs; unused alpha.1
  geometry, reporting, title-furniture and interior-placement captures have been removed.
