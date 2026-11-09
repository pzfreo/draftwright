# Browser WASM package

`crates/draftwright-wasm` is a `cdylib`/`rlib` workspace member named `draftwright-wasm`.
The root `draftwright` package, CLI and MCP binaries stay in place. The workspace's default
member is the root package, preserving ordinary native build/test commands. Use `--workspace`
when checking both crates. The wrapper's Rust `Session` contains the editor compatibility
helpers; the `wasm` module only converts arguments/results and exposes `Engine`.

## Build and check locally

Use the pinned Rust toolchain, Node 22 or later, and wasm-pack 0.15.0:

```sh
rustup target add wasm32-unknown-unknown
cargo install wasm-pack --version 0.15.0 --locked
wasm-pack build crates/draftwright-wasm --target web --profile wasm-release --locked
python3 tools/check_wasm_api.py
cargo build --locked --release --example wasm_reference
node --test tools/wasm-parity.test.mjs
python3 tools/check_wasm_label_text.py
python3 tools/wasm_size.py crates/draftwright-wasm/pkg/draftwright_wasm_bg.wasm
python3 tools/check_quiddity_pin.py
```

The generated, ignored directory is `crates/draftwright-wasm/pkg/`: JS glue,
`draftwright_wasm_bg.wasm`, TypeScript `.d.ts` files and `package.json` (plus README and
wasm-pack metadata). No generated binaries are checked in. The dedicated `wasm-release`
profile inherits `release` and sets `lto = true`, `codegen-units = 1`, `opt-level = 3`;
native `release` settings are unchanged. wasm-pack also runs its default wasm-opt pass.

The build enables `uuid/js` and `getrandom` 0.3's `wasm_js` only on WASM. The browser opens
bytes through `model::analyse_native_bytes(name, bytes)`, which never treats the name as a
filesystem path. Gzip is recognised by magic bytes and decompressed in memory. Filesystem
adapters, the JSON-lines service, stateless disk cache, MCP and font-file loading are gated
with `cfg(not(target_arch = "wasm32"))`; native path handling stays intact. There are no
clock, process or thread calls in the browser adapter. The pinned geometry kernel uses its
serial fallback on `wasm32-unknown-unknown` (no worker/thread setup is needed).

## JavaScript API

```js
import init, { Engine } from './wasm/draftwright_wasm.js';
await init(); // Fetches draftwright_wasm_bg.wasm beside the JS glue.
const engine = new Engine(file.name, new Uint8Array(await file.arrayBuffer()));
try {
  const initial = JSON.parse(engine.snapshot());
  const delta = JSON.parse(engine.apply(JSON.stringify([
    { op: 'set', path: 'sheet/title/material', value: 'Steel' },
  ]), true));
  const svg = new TextDecoder().decode(engine.renderSvg());
  const pdf = engine.renderPdf(); // Uint8Array, suitable for a Blob.
} catch (thrown) {
  const { code, message, choices = [] } = JSON.parse(thrown);
  console.error(code, message, choices);
} finally {
  engine.free(); // Release the Rust session when the editor closes the drawing.
}
```

All JSON arguments/results are **strings**, requiring `JSON.stringify`/`JSON.parse`.
The other boundary values are names/source hashes (strings), bytes (`Uint8Array`), booleans
and counts (numbers). Fallible methods throw a **string** containing serialized
`draftwright::api::Error`: `{ "code": "invalid" | "failed", "message": "...", "choices"?: [...], "path"?: "..." }`.
`path` is the escaped Spec entry (`dimensions/d:f:hole:f6~1bore.diameter`) when the refusal is that
the part has no such feature, face, dimension, annotation, view or measurement.
Input checks return refusals before planning or rendering can panic. Edit paths are limited
to 64 components and view gutters to 0–1189 paper mm (the largest supported sheet width).
Rust panics still invoke `console_error_panic_hook` and trap; `catch_unwind` cannot recover
on this target. The browser parity job exercises hostile requests followed by valid calls.

| Method | Result and behaviour |
| --- | --- |
| `new Engine(name, bytes)` | Analyse STEP with native Rust recognition and open a default `session::Session`. Name supplies the title and diagnostics; no file is read. |
| `apply(opsJson, commit)` | Delta JSON. `commit=false` computes a delta without changing the session. |
| `applyFragments(opsJson)` | Commits; JSON `{delta, fragments, pmi, placementOptions?}` with compact changed-item markers and collection `present` flags. View edits include new placement options. |
| `fragments()` | Complete `svg_fragments(None)` JSON: sheet 1. |
| `sheetFragments(sheet)` | The same for one sheet (1 and the numbers in the plan's `sheets`); invalid error with the sheet numbers as choices otherwise. |
| `sheetSvg(sheet)` | `Uint8Array`: one sheet's UTF-8 SVG; same sheet check. |
| `preview(opsJson)` | Snapshot JSON with the hypothetical plan/spec/SVG/problems; session stays unchanged. Other snapshot metadata comes from the current session. |
| `snapshot()` | JSON `{problems, plan, spec, svg, placementOptions, provenance, analysis, pmi}`. `svg` here is text. |
| `undo()`, `redo()` | Delta JSON; invalid error if the corresponding history is empty. |
| `setPmi(source, bytes)` | Attach Specify's STEP PMI. `source` must match the original source SHA-256 in snapshot provenance. Returns void. |
| `setPmiOps(source, bytes, opsJson)` | Attach PMI and commit Spec operations together; same source check. Returns void. |
| `setSpec(json)` | Migrate the incoming Spec against the Analysis, diff its top-level fields and commit changed operations. Returns void. |
| `openSpec(json)` | Open a saved Spec: read older forms as current (`sheet/preferred_printer` as `preferred_max_size`), set aside each entry the part no longer has, and commit the rest as `setSpec` does. JSON `{spec, set_aside: [{path, entry, reason_code}]}`; `spec` is the Spec applied, `reason_code` is `no_feature`, `no_face`, `no_dimension`, `no_measurement`, `no_annotation` or `no_view`, decided by what the part has. A gone member of an authored group is set aside alone as `features/<group>/pattern/members/<i>` (the member's ID as `entry`) while the rest is still a group. Other refusals throw, and the session is unchanged. |
| `specOps(before, after)` | Module function: JSON `[{op: "set" \| "remove", path, value?}]` turning Spec `before` into `after` (`api::spec_ops`): sorted keys, root `schema` skipped, escaped paths, arrays and scalars replaced whole, a new empty object set, missing or null keys removed (the editor's `specOperations` sets an explicit null instead). |
| `spec()`, `pmi()`, `plan()` | Current Spec, PMI report or Plan JSON. |
| `planCount()` | Cumulative planner count (useful for checking one replan per edit). |
| `faceCount()`, `featureCount()` | Loaded face count and recognised feature count. |
| `placementOptions()` | JSON map from editable item IDs to labels, per-view sides and allowed operations. `offset: "callout"` or `offset: "linear"` advertises free placement; absent for other forms. |
| `calloutOffset(address)` | JSON `{view, tip:[x,y], bend:[x,y], offset:[dx,dy], members:[{centre:[x,y], radius, span?:{start,sweep}}]}` from `Session::callout_offset`: paper mm, y up. Pin a dragged bend `b` with `{offset: b - tip, pinned: true}`. Refuses non-callouts, missing automatic leaders and non-canonical carriers. |
| `labelOffset(address)` | JSON `{view, anchor:[x,y], label:[x,y], offset:[dx,dy]}` from `Session::label_offset`: paper mm, y up. Pin a dragged linear label origin `p` with `{offset: p - anchor, pinned: true}`. Refuses non-linear items, missing automatic placements and non-canonical carriers. |
| `renderSvg()`, `renderPdf()` | `Uint8Array`: sheet 1's UTF-8 SVG, or the PDF with a page per sheet and bundled fonts. |
| `start()` | Installs the panic hook once; wasm-bindgen invokes it automatically at initialization. |

The helpers preserve the editor wrapper's packet shape, source validation, Spec migration,
PDF font lookup, preview and snapshot behaviour. **One migration change:** the old editor
wrapper returned a string from `renderSvg()`. This package returns bytes as requested by
issue #66; decode with `new TextDecoder().decode(engine.renderSvg())` where text is needed.
`snapshot().svg` and SVG fragment strings retain their existing formats.

The MCP `tools/list` Spec shape is a shallow discovery outline. Editors needing the
complete document schema can use the generated draft-2020-12 `schemas/spec.json`
(or MCP `spec_schema {}` when connected); `#/$defs/spec_read` names the Spec value
returned by `get_spec`. Live browser sessions continue to use their existing Spec,
operation and placement-option methods and the same model validation. This schema
advertisement change adds no WASM method or TypeScript signature.

Rendered SVG, PDF and PNG carry ISO 5457 grid zones and centring marks on every sheet, in a
`centring_marks` layer with the zone labels as margin text. `setSpec` or `apply` with `sheet/zones: false`
omits them unless section cross-references cite zones; see `schemas/spec.json`.

## Consume a pinned release in the editor

Each published GitHub release whose tag starts with `v` builds from that tag. Keep both
Cargo package versions in sync with the release tag (without `v`); the workflow checks this.
The job runs the parity test before uploading with `gh release upload` and `GITHUB_TOKEN`.
Only that release job has `contents: write`; CI has read permission. Nothing is published
to npm. Existing native release procedures can stay separate.

For example, after a release carrying these assets exists:

```sh
version=0.1.0-alpha.4 # Replace with the chosen published version.
asset="draftwright-wasm-${version}.tgz"
gh release download "v${version}" --repo pzfreo/draftwright \
  --pattern "$asset" --pattern "$asset.sha256"
sha256sum -c "$asset.sha256" # macOS: shasum -a 256 -c "$asset.sha256"
mkdir -p public/wasm/drawing
tar -xzf "$asset" --strip-components=1 -C public/wasm/drawing
```

The archive has a top-level `pkg/`, including the engine license and bundled-font notices. Pin the tag/version and retain the checksum in the
editor's dependency/update process. Copy the complete package together so JS glue and WASM
stay matched; serve `.wasm` as `application/wasm`. Import `draftwright_wasm.js` in place of
`dw_engine.js` (the WASM basename changes too). Await initialization, update the SVG byte
call sites, then delete the editor's engine crate and Rust/wasm-pack CI build once validated.
Keep its existing build fallback until the first package release is available. No editor
repository changes are made by this work.

## Parity and size

`tools/wasm-parity.test.mjs` replays prismatic and polygonal-stock sessions under Node using the **web**
package. Native `examples/wasm_reference.rs` independently loads the same STEP through
`model::analyse_native_step` and uses the core Session. It compares Spec JSON, Plan JSON and
SVG byte for byte after open, edits, fragment edits, Spec replacement and history. Preview
SVG is also compared byte for byte; its nested JSON documents are compared structurally
because JS reserialization changes object order and number spelling.
It also replays compact plan deltas, checks changed SVG fragments against full output, and
exercises gzip input, counts, snapshots, placement options, JSON error strings, PMI
source checks and attachment calls, and the PDF signature. Fixtures in `tests/fixtures/wasm/`
refer to small STEP files already in the repository, so no external corpus is needed.
These planar fixtures enforce full byte parity, including the Analysis hash. Curved models
can have target-dependent last-bit differences in profile sampling and HLR segment boundaries
(the local plate Analysis hash and turned SVG demonstrated this); the parity gate does not
claim byte identity for every curved model. No comparison tolerance or hash exclusion is used
for committed states. Native output is unchanged.
The CI WASM job runs for pull requests and main pushes; the release job repeats parity.

Measured locally on 2026-10-16 with Rust 1.99 and wasm-pack 0.15.0, web target, `wasm-release` and
wasm-pack's default wasm-opt pass:

| `.wasm` | Bytes | Decimal MB |
| --- | ---: | ---: |
| Raw | 13,602,172 | 13.602 |
| gzip level 9 | 4,486,165 | 4.486 |
| Brotli quality 11, lgwin 22 | 2,992,302 | 2.992 |

The size script prints raw and gzip level 9 sizes, plus Brotli quality 11 when the Python
module or `brotli` CLI is installed. These are sizes of the `.wasm` alone; the release tarball also contains glue and
declarations. Compression is a transfer/deployment choice and does not alter the WASM file.
The editor's previous build was approximately 15 MB raw / 4 MB Brotli (decimal MB).

## Reviewed API snapshot

`crates/draftwright-wasm/api.d.ts` is the reviewed copy of wasm-pack's generated
`pkg/draftwright_wasm.d.ts`. The normalizer removes comments (including generator/version
noise), sorts top-level declarations and class/interface members, and retains signatures,
parameter names, literal types and nested types. Generated JS and binaries remain ignored.
After building, run:

```sh
python3 tools/check_wasm_api.py
# When intentionally changing the API:
python3 tools/check_wasm_api.py --update
```

Review and commit the snapshot diff, and add a new note line (normally a bullet) under
`### WASM API` in `CHANGELOG.md`'s `## Unreleased` section in the same change. CI compares against the PR base
commit or the commit before a main push (`--base-ref`); local checks default to `HEAD`.
API drift fails unless that comparison also adds a WASM API note line. Updating the snapshot
alone cannot bypass the changelog requirement, and an existing note cannot approve a later
change. A documented mismatch is allowed as approved by the maintainer, but the check still
prints the diff and the snapshot update command. Normally commit the refreshed snapshot.
Run the safeguard tests with `python3 -m unittest discover -s tools -p 'test_wasm_safeguards.py'`.

## Size budget

`tools/wasm-size-budget.json` records the baseline raw, gzip level 9 and Brotli quality 11
(lgwin 22 for both the Python module and CLI)
sizes measured on the web target with the `wasm-release` profile and wasm-pack's default
wasm-opt pass. Its `ceiling_bytes` is 10% above the raw baseline, rounded up to the
next 100,000 bytes. `tools/wasm_size.py` enforces that raw-byte ceiling in CI and release
builds; equality passes. It prints actual transfer sizes and remaining headroom on every
run. The initial ceiling (2026-10-16) was **15,700,000 bytes**, 15% above a 13,602,172-byte
baseline. On 2026-10-19 the maintainer re-baselined on main's 15,649,337-byte build with a
**17,300,000-byte** ceiling (10.5% headroom). Brotli reporting requires the Python `brotli` module
or the `brotli` CLI; enforcement
has no optional dependencies. Gzip/Brotli sizes are recorded for deployment planning and
are not separate gates. A budget increase requires a deliberate reviewed JSON edit; the
check never raises its own ceiling.

## Matching geometry kernels

Draftwright and Specify embed independent copies of quiddity-rust. STEP files pass between
them in the editor, so different kernel commits may change face numbering. Run:

```sh
python3 tools/check_quiddity_pin.py
```

The script reads both `haecceity` and `quiddity` tag/rev pins from this `Cargo.toml` and
fetches `pzfreo/specify-core-rust`'s manifest at its most recently published GitHub release,
including prereleases. It compares resolved commits so a tag and its full commit rev match,
prints the original pins, and checks that both dependencies agree within each package.
It uses `gh api` when installed (including its existing login), otherwise the GitHub REST
API without authentication. `GITHUB_TOKEN` is used when set (`GH_TOKEN` also works).

CI runs the comparison on main pushes. The release job runs it before asset upload with
`--release "$RELEASE_TAG" --repo "$GITHUB_REPOSITORY"`. A mismatch emits a GitHub Actions
`::warning::` and appends a `Kernel note:` line describing both packages' pins to the release
body via `gh release edit`, preserving other notes and replacing its own line on retries.
It never fails the release: one package necessarily releases first after a kernel bump.
Fetch, parsing and release-note-edit errors are warnings as well. Running locally without
`--release` only reads GitHub; it does not edit any release.

Free placements can lie inside a view. The query anchors travel with the view origin; re-read
them after edits to views or sheets. Pinning the returned offset preserves the current
position, including automatic callouts already drawn inside the view. Linear offsets move the
dimension line perpendicular to its span and the text along it, retaining the measured value.
Offsets require `pinned: true`, exclude `side`, `lane` and `along`, and accept each component
within ±1000 mm. Collisions remain pinned and are reported through the existing problems.

Callout members are the projected attachment sites in paper mm (y up). An arc
includes `span` with counterclockwise `start` and `sweep` in radians. A dragged
callout attaches radially to the member nearest its elbow, retaining the automatic
member on ties. The automatic `tip` remains the offset origin; changing members
keeps the elbow, shoulder and label position. Reapplying the automatic offset
retains the exact automatic ink.


`Session.numberedCharacteristics()` returns JSON containing each extracted inspection
number, its PMI source, original plan item, drawn carrier, sheet and expected ring IDs.
The values are file-owned; undrawn requirements remain listed. Dimensions, tolerance
frames and datum features support numbers. Surface textures and semantic notes have no
number owner in haecceity 0.2.5 and retain their existing drawing behavior.

## Assembly drawing packs

Import `Pack` and `listParts` beside `Engine`:

```ts
const parts = JSON.parse(listParts(name, bytes)); // same {parts} as CLI parts
const pack = new Pack(name, bytes);              // optional third arg: PackSpec JSON
try {
  const report = JSON.parse(pack.reportJson()); // same report as pack --formats json
  const effectiveSpec = JSON.parse(pack.specJson());
  for (let page = 1; page <= pack.pageCount(); page++) {
    const svg = pack.pageSvg(page);              // SVG string, same drawing form as Engine
  }
  const pdf = pack.pdf();                        // Uint8Array, same writer as CLI
  const csv = pack.csv();
  pack.setSpec(JSON.stringify({ga: {include: false}}));
} finally {
  pack.free();
}
```

Construct and rebuild packs in a **Worker**. Both calls are synchronous; v1 has
no progress callback. The instance owns its STEP bytes and rendered drawings;
`setSpec` keeps that instance and replaces its effective PackSpec and pack only
on success. A failed rebuild preserves the last successful pack. Release the
instance with `free()` when finished, and do not use it afterwards.

Pages are **1-based**, exactly in PDF order: the GA first when present, followed
by each made part's sheets. `rows[].sheets` contains those same page numbers;
bought or failed parts have no sheets. An empty pack has zero pages. Page zero
and pages above `pageCount()` throw an `invalid` API error. Errors use Engine's
`error_json` JSON string encoding. Per-part failures live on rows with `key` and
`products`; per-part findings carry `products`.

`pageSvg(page)` uses wasm-bindgen's unsigned 32-bit number coercion: fractions
truncate toward zero and negatives wrap modulo 2³² before the 1-based range
check. Thus `1.9` selects page 1 and `-1` is out of range; callers should pass
positive integer page numbers. No JavaScript-side integer validation is added.

During `setSpec`, the previous pack stays alive until the replacement succeeds;
peak pack memory can briefly be approximately **2×** during a rebuild (plus the
retained source and temporary analysis allocations).
