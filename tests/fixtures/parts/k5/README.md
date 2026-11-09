# Stream K5 fixtures

Copies, unedited, of Python draftwright's evaluation and case-study STEP files
(pzfreo/draftwright-python at `f1a5db9b4490e9b233ae5b7cac1138633929c693`), used by
`tests/completeness_rules.rs` with Rust quiddity's recognition:

| File | draftwright source | What it is (draftwright's fixture README) |
| --- | --- | --- |
| `turned-step-compound.step` | `tests/fixtures/evaluation/` | two disjoint parallel shafts, four bands |
| `turned-step-repeated-lengths.step` | `tests/fixtures/evaluation/` | three adjoining bands 10 long |
| `groove-compound.step` | `tests/fixtures/evaluation/` | two parallel shafts, a groove each |
| `pad-z-positive.step`, `pad-x-positive.step` | `tests/fixtures/evaluation/` | one raised pad along +z, +x |
| `pad-compound-equal.step` | `tests/fixtures/evaluation/` | two bodies, equal pads at distinct sites |
| `issue_909_basic_part_design_017_body.step` | `tests/fixtures/` | the #909 sloped-profile case study with a raised pad |

They live in this subdirectory so the single-sheet guard (which reads `tests/fixtures/parts/*.step`)
does not take them as new cases.
