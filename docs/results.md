# Public example results

Measured on 2026-11-13 with **draftwright 0.6.0-alpha.2**, on quiddity **0.5.1**.
The engine source is commit
[`979a7aa1f21f13a41adbadd889de9d4d20c75ee8`](https://github.com/pzfreo/draftwright/commit/979a7aa1f21f13a41adbadd889de9d4d20c75ee8);
only documentation and the Cargo package description changed during this measurement.
Inputs and the build script are from dw-examples commit
[`4b5194798ec911d49ccc5eb4bc9d2bd49895b24e`](https://github.com/pzfreo/dw-examples/tree/4b5194798ec911d49ccc5eb4bc9d2bd49895b24e),
with its `drawings.toml` draftwright tag changed from `v0.2.14` to `v0.6.0-alpha.2`.

**These are unreviewed output.** A successful build or a drawing with zero reported errors
is not approval to manufacture from it. Draftwright reports what it could not state;
the findings below are what its own checks detected, rather than an independent assessment.
Layout regressions are tracked in [#452](https://github.com/pzfreo/draftwright/issues/452);
see also the [known limitations](engine.md#known-limitations).

## Reproduce

From the draftwright clone:

```sh
cargo build --release --locked --bin draftwright
```

From the sibling dw-examples clone, after changing the pinned tag in `drawings.toml`:

```sh
python3 build.py --cli ../draftwright/target/release/draftwright
```

This uses the stock `render` and `review` commands for drawings, and `pack` for assemblies.
The manifest requests PDF drawings and PDF/CSV/JSON packs. Of the 54 drawing entries, 44 set
only the title block; ten reuse the existing AI-authored Specs (nine text-to-cad parts and
NIST CTC-01). Packs set only the general assembly title block. No output was corrected by hand.
Generated files stay in the separate dw-examples checkout; they are not committed here.

## Sources

The counts below are manifest entries, including the ten alternate Specs, rather than unique
models. Source files, authors and licences are recorded beside the inputs:

| Source | Drawings | Packs | Provenance |
| --- | ---: | ---: | --- |
| NIST MBE PMI test parts | 16 | 0 | [NIST inputs](https://github.com/pzfreo/dw-examples/tree/4b5194798ec911d49ccc5eb4bc9d2bd49895b24e/steps/nist) |
| FreeCAD parts library | 18 | 0 | [FreeCAD inputs](https://github.com/pzfreo/dw-examples/tree/4b5194798ec911d49ccc5eb4bc9d2bd49895b24e/steps/freecad) |
| build123d | 1 | 0 | [build123d input](https://github.com/pzfreo/dw-examples/tree/4b5194798ec911d49ccc5eb4bc9d2bd49895b24e/steps/build123d) |
| Open CASCADE Technology | 1 | 0 | [OCCT input](https://github.com/pzfreo/dw-examples/tree/4b5194798ec911d49ccc5eb4bc9d2bd49895b24e/steps/occt) |
| text-to-cad | 18 | 2 | [Exported text-to-cad inputs](https://github.com/pzfreo/dw-examples/tree/4b5194798ec911d49ccc5eb4bc9d2bd49895b24e/steps/text-to-cad) |
| Gramil marking gauge | 0 | 1 | [Gramil input](https://github.com/pzfreo/dw-examples/tree/4b5194798ec911d49ccc5eb4bc9d2bd49895b24e/steps/gramil) |

## Build and finding totals

All **54 drawings and 3 packs** completed the build commands.
**40 of 54 drawings have zero reported errors**; they can still have warnings,
missing requirements or mistakes the checks do not detect.

| Scope | Errors | Warnings | Info |
| --- | ---: | ---: | ---: |
| Drawing reviews | 1094 | 1830 | 2382 |
| Pack reports | 0 | 62 | 41 |
| **Total** | **1094** | **1892** | **2423** |

Counts come from the freshly rebuilt `drawings/index.json`, checked against each drawing's
`<name>.review.json` and each pack's `<name>.report.json`. Drawing counts include every finding
in `problems`, `lint` and `structural`, once per array entry, without merging repeated codes
or groups. Pack counts follow `build.py`: pack `findings`, general assembly `problems`, each
row's `problems`, and one error for each failed part row. Pack reports do not run the same
full drawing-review lint, so their counts have different coverage.

## Most frequent finding codes

These are the ten most frequent codes across drawing reviews and pack reports, using the
same arrays as above. Counts are occurrences, including repeated findings on different items.
A failed part row contributes an error to the totals but has no finding code of its own.

| Code | Drawings | Packs | Total |
| --- | ---: | ---: | ---: |
| `pmi_not_extracted` | 2084 | 0 | 2084 |
| `pmi_not_lowered` | 695 | 0 | 695 |
| `annotation_leader_crossing` | 531 | 0 | 531 |
| `nominal_rounded` | 404 | 1 | 405 |
| `extension_through_feature` | 372 | 6 | 378 |
| `leader_through_feature` | 206 | 0 | 206 |
| `feature_not_located` | 205 | 0 | 205 |
| `general_tolerance_missing` | 44 | 45 | 89 |
| `label_crowded` | 70 | 6 | 76 |
| `datum_origin_unusable` | 70 | 0 | 70 |

## Per drawing

`-ai` names use the existing authored Specs. Sheet sizes are read from the generated PDFs
with `pdfinfo -f 1 -l 99`; `× N` means N pages of the same size. Errors and warnings cover
the whole drawing, including any additional sheets.

| Name | Sheet | Errors | Warnings |
| --- | --- | ---: | ---: |
| bearing-608zz | A4 | 0 | 1 |
| bearing-lm10uu | A4 | 0 | 1 |
| corner-bracket-2020 | A4 | 0 | 7 |
| gantry-plate | A3 | 0 | 13 |
| gt2-pulley | A3 | 0 | 4 |
| hex-standoff-m3 | A4 | 0 | 2 |
| hirth-joint | A4 | 0 | 2 |
| l-plate-5-holes | A4 | 0 | 1 |
| linkrods | A4 | 0 | 3 |
| lm8uu | A4 | 0 | 2 |
| nema17-bracket | A3 | 0 | 4 |
| nema17-mount-bracket | A4 | 0 | 9 |
| nist-ctc-01 | A1 | 0 | 13 |
| nist-ctc-01-ai | A3 | 0 | 18 |
| nist-ctc-02 | A2 | 106 | 178 |
| nist-ctc-03 | A2 | 66 | 37 |
| nist-ctc-04 | A1 | 36 | 280 |
| nist-ctc-05 | A1 | 37 | 30 |
| nist-ftc-06 | A2 | 162 | 47 |
| nist-ftc-07 | A1 | 78 | 86 |
| nist-ftc-08 | A2 | 85 | 124 |
| nist-ftc-10 | A1 | 87 | 173 |
| nist-ftc-11 | A4 | 15 | 21 |
| nist-stc-06 | A0 | 56 | 54 |
| nist-stc-07 | A1 | 144 | 50 |
| nist-stc-08 | A1 | 100 | 39 |
| nist-stc-09 | A1 | 40 | 386 |
| nist-stc-10 | A1 | 82 | 154 |
| retaining-ring-din471-m10 | A4 | 0 | 4 |
| shf08 | A3 | 0 | 6 |
| sk08 | A4 | 0 | 4 |
| sprocket-iso606-z08 | A4 | 0 | 2 |
| t2c-angle-bracket | A4 | 0 | 4 |
| t2c-angle-bracket-ai | A4 | 0 | 4 |
| t2c-circular-flange | A4 | 0 | 2 |
| t2c-circular-flange-ai | A4 | 0 | 0 |
| t2c-electronics-enclosure-base | A4 | 0 | 2 |
| t2c-electronics-enclosure-base-ai | A3 | 0 | 2 |
| t2c-guide-bar | A4 | 0 | 2 |
| t2c-guide-bar-ai | A3 | 0 | 0 |
| t2c-l-bracket | A4 | 0 | 10 |
| t2c-l-bracket-ai | A4 | 0 | 7 |
| t2c-mounting-plate | A4 | 0 | 3 |
| t2c-mounting-plate-ai | A4 | 0 | 3 |
| t2c-perforated-plate | A4 | 0 | 3 |
| t2c-perforated-plate-ai | A3 | 0 | 1 |
| t2c-pivot-mount-plate | A4 | 0 | 3 |
| t2c-pivot-mount-plate-ai | A4 | 0 | 1 |
| t2c-stepped-shaft-keyway | A4 | 0 | 1 |
| t2c-stepped-shaft-keyway-ai | A4 | 0 | 0 |
| t8-flange-nut | A4 | 0 | 3 |
| t8-housing-bracket | A4 | 0 | 10 |
| vslot-2020x50 | A3 | 0 | 12 |
| vslot-20x20-100 | A4 | 0 | 2 |

## Assembly packs

Groups are distinct parts, rather than quantities. A successful pack command can retain
failed part rows or a failed general assembly (GA); those are shown explicitly below.

| Pack | Groups | Made / bought | Sheets | Failed parts | GA | Errors | Warnings | Info |
| --- | ---: | --- | ---: | ---: | --- | ---: | ---: | ---: |
| gramil-pack | 12 | 8 / 4 | 9 | 0 | ok | 0 | 30 | 23 |
| t2c-motorcycle-shock-absorber-pack | 10 | 10 / 0 | 11 | 0 | ok | 0 | 23 | 13 |
| t2c-pivot-mount-plate-pack | 3 | 3 / 0 | 4 | 0 | ok | 0 | 9 | 5 |
