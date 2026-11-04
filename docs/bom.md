# File-stated BOM data

Drawing packs read native product number, name, description, formation revision
and make/buy source, product categories, applied classifications, material
designations and CAx-IF `material property` / `material name` properties. They
also read UDA 1.8 properties on the product, definition, definition shape and
immediate leaf occurrences. No Specify library or saved interview payload is
needed. `bom::read` accepts the pinned haecceity P21 document and leaf definitions;
single-part analysis reuses the PMI document parse.

The public Specify v0.1.8 occurrence set is `specify bom`. Its items are
`part_number`, `name`, `description`, `revision`, `make_buy`, `item_number`,
`optional` and `stock`. Explicit answers in this set override native fields. Accepted suggestions marked
by boolean `default.<field>` items, and other exporter attributes, fill missing
fields. Keys are case-insensitive, spaces/hyphens are
normalised to underscores, recognised values are normalised, and invalid or
empty values are ignored. Appended sets are visited in entity order. Written
suggestions are file statements, regardless of their authoring provenance.

Pack Spec fields override file values; missing supply and item numbers retain
pack's guesses/allocation and their existing findings. Every populated field has
`spec`, `file` or `guess` provenance in the JSON row's `sources` map and the CSV's
corresponding `_source` column. Missing optional fields are absent from JSON and
empty in CSV, with no source. `item` is the effective find number; `make_buy`
retains `made`, `bought` or `not known`. Explicitly unknown supply is `unknown`;
it receives a drawing without a make/buy judgment. Stock and standard-part
classification do not imply supply.

A positive numeric occurrence reference designator or unambiguous numeric NAUO
name can supply a find number. All occurrence IDs and populated reference
designators are retained as `occurrence_references`. An exporter's sequential
placement IDs are not repurposed as find numbers. Repeated occurrences must
agree on a native find number; explicit Specify item UDAs take precedence.
Duplicate effective item numbers refuse the pack instead of renumbering file
facts. Different stated BOM facts prevent automatic geometry merging. Legacy
product IDs that duplicate display labels retain the existing geometry grouping.

`optional` provides a `quantity_range`, e.g. `0–1` (or `0–2` after a Spec quantity
override). False yields the fixed range. It never changes placed geometry.
MATERIAL and DWG NO use the effective material and complete part number in the
existing title block. Explicit Spec revision overrides use the existing REV field. Sheet title
intent remains in `drawing.sheet.title`; its material/number/revision overrides
also appear in the row's values and provenance. Descriptions, optional ranges,
stock and classifications are exported as data; no new visible sheet fields or
layout are added. File defaults fill the title block after paper and view selection;
long file values retain their overflow findings without changing the drawing layout.
