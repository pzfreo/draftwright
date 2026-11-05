# File-stated BOM data

Drawing packs read native product ids, names, descriptions, formation revisions
and make/buy source, product categories, classifications, material designations
and CAx-IF `material property` / `material name` properties. UDA 1.8 properties on
the row's product, formation, definition, definition shape and immediate leaf
occurrences are read through pinned haecceity P21 access. No Specify dependency,
private saved-answer payload, or document-wide BOM attestation is used.

`part_number` is populated only by that row's own explicit `specify bom`
`part_number` item (or a pack Spec override). Raw PRODUCT.id is never a part number:
all native ids remain in `product_ids`, with file provenance, including ids of
merged products and ids whose numbers are overridden. Stored `default.part_number`
proposals are retained separately as `part_number_suggestion`, with suggested
provenance. Neither raw ids nor suggestions change DWG NO, REFERENCE or identity
grouping. The GA header likewise needs its root's own explicit part-number UDA;
otherwise its existing generic-name/file-stem selection is preserved.

Explicit Specify answers override native values. Stored items flagged by boolean
`default.<field>=T` fill gaps but keep source `suggested`; native facts win over
these suggestions. Keys are case-insensitive with spaces/hyphens normalised to
underscores. Known values are normalised; invalid and blank values are ignored.
Pack Spec overrides win over both. JSON `sources` and CSV `_source` fields use
`spec`, `file`, `suggested` and `guess`; absent values have no source. Suggested
supply produces the existing make/buy guess finding. Other active stored
suggestions produce `pack_bom_suggestion`, rather than being asserted as facts.

Native MADE/BOUGHT are supply statements. Native NOT_KNOWN is retained in
`file_make_buy` with source `file`, but does not select supply: the ordinary guess
and its finding apply. Stock and standard-part classification do not imply supply.
Stock overrides accept the same case/underscore/hyphen aliases as file values and
are stored as `from stock` or `to be made`.

Find numbers come from the row's `item_number` UDA or an unambiguous positive
numeric NAUO.name, as Specify documents. Occurrence ids and reference designators
remain in `occurrence_references`; numeric reference designators are not find
numbers. Duplicate effective file claims produce `pack_duplicate_item`, naming
all affected rows. Their original `item_number` values and provenance are retained;
automatic find allocation follows the existing made/bought row order. A Spec
number wins over conflicting file claims; conflicting Spec numbers still refuse
invalid intent.

Only optional rows receive a `quantity_range`, e.g. `0–1` or `0–2` after a Spec
quantity override. Placed quantities are unchanged. MATERIAL and qualified DWG NO
fill the existing title cells after layout; explicit sheet title overrides win.
The GA's existing MATERIAL column uses each row's material. Descriptions,
revisions, optional ranges, stock and classifications remain data-only; no new
sheet elements are added. Real title/table overflow remains reported.
