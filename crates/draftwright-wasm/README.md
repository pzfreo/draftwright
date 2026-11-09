# draftwright-wasm

Browser bindings for draftwright's live drawing session. JSON documents cross the JS boundary
as strings; SVG and PDF exports are `Uint8Array`. Uses the bundled drawing fonts.

See [the API and consumption guide](https://github.com/pzfreo/draftwright/blob/main/docs/wasm.md).
Release assets contain this package; there is no npm publication.

Use `placementOptions()` to discover free placement: each compatible item's `offset` is
`"callout"` or `"linear"`. `calloutOffset(address)` returns JSON
`{view, tip:[x,y], bend:[x,y], offset:[dx,dy], members:[{centre:[x,y], radius, span?:{start,sweep}}]}`; `labelOffset(address)` returns
`{view, anchor:[x,y], label:[x,y], offset:[dx,dy]}` for linear dimensions. Coordinates are
paper mm, y up. Pin a dropped bend with `{offset: bend - tip, pinned: true}`, or a linear
label origin with `{offset: label - anchor, pinned: true}`. Returned offsets pin the current
position without movement. These methods throw the same API errors as the live Rust session.

Callout members are the projected attachment sites in paper mm (y up). An arc
includes `span` with counterclockwise `start` and `sweep` in radians. A dragged
callout attaches radially to the member nearest its elbow, retaining the automatic
member on ties. The automatic `tip` remains the offset origin; changing members
keeps the elbow, shoulder and label position. Reapplying the automatic offset
retains the exact automatic ink.

`new Pack(name, bytes, specJson?)` builds an assembly drawing pack once. Use
`reportJson()`, `specJson()`, `pageCount()`, `pageSvg(page)` (a string), `pdf()`
(`Uint8Array`) and `csv()`. Pages are **1-based**, in PDF order: GA first when
present, then made-part sheets; `rows[].sheets` uses the same numbering.
`listParts(name, bytes)` returns the same JSON as `draftwright parts`.

Build a pack in a **Worker**: construction and `setSpec(specJson)` are synchronous,
with no progress callback in v1. `setSpec` rebuilds using the retained STEP bytes
and keeps the instance; a refusal leaves its previous pack intact. Call `free()`
when finished to release its retained source and drawings. Errors are thrown as
JSON strings through the same `error_json` function as Engine.

`pageSvg(page)` uses wasm-bindgen's unsigned 32-bit number coercion: fractions
truncate toward zero and negatives wrap modulo 2³² before the 1-based range
check. Thus `1.9` selects page 1 and `-1` is out of range; callers should pass
positive integer page numbers. No JavaScript-side integer validation is added.

During `setSpec`, the previous pack stays alive until the replacement succeeds;
peak pack memory can briefly be approximately **2×** during a rebuild (plus the
retained source and temporary analysis allocations).
