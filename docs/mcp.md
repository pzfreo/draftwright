# Stateless MCP

`draftwright mcp-http [127.0.0.1:8765]` serves local Streamable HTTP at `/mcp`.
`draftwright mcp-stdio` serves the same JSON-RPC tools over newline-delimited stdio.
Set `DRAFTWRIGHT_CACHE` to a persistent directory (default `.cache/draftwright`).
These are development/evaluation adapters, bound to loopback without authentication.
The earlier `draftwright-mcp` executable retains its document-passing compatibility API;
new integrations use the subcommands. No production service was changed.

The HTTP implementation uses the JSON-response mode of [MCP Streamable HTTP
2025-06-18](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports).
POST accepts `application/json` and requires `Accept: application/json, text/event-stream`.
Accepted notifications/responses return 202 without a body. GET returns 405: there is no
unsolicited SSE stream. Initialization negotiates 2025-11-25, 2025-06-18 or 2025-03-26;
subsequent requests may send `MCP-Protocol-Version`, with unknown versions refused.
No `Mcp-Session-Id` or live drawing session is retained. Origins must match the local endpoint.
The local adapter handles requests sequentially, caps bodies at 8 MiB and the complete
request head at 16 KiB (request line, headers, line endings and final blank line, counted
as wire bytes), and refuses chunked bodies. Head reads copy only the remaining budget
into a fixed-capacity buffer; exhausting it before the final blank line returns 431,
including input without a newline. A complete head of exactly 16 KiB is accepted.
One absolute 10-second deadline covers reading the head and body after the connection
is accepted for handling; incremental delivery does not reset it. Expiry returns 408
and releases the adapter for the next connection. Refusals half-close the write side
and drain unread input for at most 100 ms before closing, to preserve the status
on Linux without an unbounded wait. Bare-LF blank lines terminate reading promptly
and are refused by header parsing with 400. This read deadline excludes tool
computation. Cold computation runs synchronously here; production jobs are below.

## Drawing packs in the compatibility adapter

The `draftwright-mcp` executable exposes `pack` with `step` (a path or registered
part ref) or `part_ref`, and optional PackSpec `spec`. Its default result contains
one compact JSON **Report** text block and a short row/page/failure summary.
It exports no files by default and does not repeat the report in structured content.
This MCP delivery policy leaves the native `serve` pack envelope and defaults unchanged.

Request files explicitly with `formats`, for example `["pdf","csv"]`. Empty or
omitted formats means report only; duplicates are refused (`uniqueItems: true`).
Files are returned as `resource_link` content with `uri`, `name`, `mimeType` and
`size`, never base64 inside tool text. Read a returned URI with MCP
`resources/read {"uri":"draftwright:<artifact_ref>"}` using the same persistent
`DRAFTWRIGHT_CACHE`. PDF and CSV resources use `{uri,mimeType,blob}` with base64
bytes; JSON uses `{uri,mimeType,text}`. When `json` is requested, its resource
replaces the inline report, so the report is delivered once. Export bytes match
the CLI. This tool belongs to the document-passing executable, not the stateless
subcommands' tool list below.

## Tools and documents

Drawing queries carry `part_ref` and either `spec` or `spec_version`. An absent Spec means
the deterministic default Spec, rather than an implicit remembered edit. `get_spec` without
either loads the explicitly named drawing's head; `drawing_ref` defaults to `default`.
Use different drawing references for independent branches/tasks.

| Tool | Input beyond the part/Spec | Output |
| --- | --- | --- |
| `inspect_part` | `step` and optional `features` to register, or `part_ref`; `detail`, `problem_filter` | Compact sheet summary, bounded key item IDs/labels, counts, default/current input Spec hash; optional `problem_filter` adds a paged corrective answer; detail adds a lossless Plan artifact |
| `list_items` | `filter`, `cursor`, `limit`, `detail` | Paged IDs, labels, kind, view, typed state; detail adds Session item answers |
| `get_item` | `id`, `detail` | Label, state, value, basis and allowed operations; detail adds resolved placement/anchors/features |
| `list_problems` | `filter`, `cursor`, `limit`, `detail` | Paged ID, code, severity and subjects; default includes corrective ops; detail adds message |
| `item_options` | `id` | Operations, relative placements, compatible view anchors and scales, plus every settable escaped Spec path and its choices/forms/ranges |
| `preview_ops` | `ops`, `cursor`, `limit`, `detail` | Paged delta events, planner count and lossless delta artifact; no Spec version/head is kept |
| `apply_ops` | `parent`, `ops`, `drawing_ref`, `limit`, `detail` | Immutable new Spec hash and paged delta; stale parent refuses with current head in message and choices |
| `view_sheet` | `sheet`, `region`, `overlay_ids`, `dpi` | PNG artifact metadata plus MCP image content for vision clients |
| `render_pdf` | `sheet` | PDF artifact metadata |
| `get_spec` | Optional version/Spec, or drawing head | Spec and its immutable version hash |
| `spec_schema` | None (`{}`), no part needed | Full draft-2020-12 Spec schema and fragment naming the `get_spec` read shape |
| `analyse` | `step`, optional `features` | One-shot full Analysis document |
| `render` | `step`/`part_ref`, optional `features`, `format` (`png`/`pdf`), `sheet`, PNG region/overlay/dpi | One-shot render artifact metadata |
| `parts` | `step` | The file's distinct parts: `{parts: [{part, name, product_id, faces, instances}]}` |

**Assemblies.** `inspect_part`, `analyse` and `render` take an optional `part` with `step`: the
index of one part of an assembly, as `parts` lists it, drawn alone in its own frame with its PMI
only. A part registered alone has its own key, `<STEP SHA-256>_p<part>-<engine SHA-256>`; a
`part_ref` already names its part, so `part` beside one is refused. Without `part` an assembly
is drawn whole and its plan warns `assembly_drawn_whole`, unless every body coincides with
one part's (`coincident_bodies`) or lies within the largest body's box
(`assembly_principal_drawn`), when that part is drawn alone. `serve` offers the tool as
`mcp_parts`, and its batch operations (`analyse`, `plan`, `render`, `review`, `open`) take the
same `part`, with a `parts` operation; the compatibility `draftwright-mcp` server has a `parts`
tool and a `part` argument too (docs/model.md, "Assemblies: one chosen part").

Tool input and output schemas are standalone draft-2020-12 files under `schemas/mcp/`.
Both adapters serve exactly those documents in `tools/list`, with `outputSchema` and
`structuredContent`. Regenerate with `python3 tools/generate_mcp_schemas.py`.
Spec values in `tools/list` are compact typed outlines: explicit top-level keys,
basic types and short enums, with `additionalProperties: false` as before. Nested
fields are described once on demand: call `spec_schema {}` and use the returned
`spec` schema document (`schemas/spec.json`). Its `spec_read` result is the fragment
`#/$defs/spec_read` within that same document, describing `get_spec`'s Spec value.
Fetch it once before authoring unfamiliar fields; `item_options` supplies valid edit
paths and geometry-specific choices. The full schema describes structure; reference,
geometry and engineering checks still run in the model. Private generated preflight
schemas (`schemas/mcp-validation.json`) preserve main's validation and refusal messages.
Neither private nor full schemas are loaded into `tools/list`.
The shared operation shape is each apply/preview schema's `op` (local `#/op` reference): `set`, `remove`, `add`,
using the existing RFC 6901 escaped Spec paths (a slash within an ID is `~1`). S5 can consume
that definition for its typed operation wrapper. The model performs semantic validation.
Every tool's output also describes structured refusals (`error.code`, `message`, `choices`).
Linear placement options offer only the two sides compatible with their measured span.
Unknown IDs offer nearest valid IDs; invalid choices/paths come from the same validation as
Session. A malformed input names the accepted schema shape or missing field.

Default lists and delta envelopes are below **4096 UTF-8 JSON bytes**, including the MCP
JSON-RPC response with both its serialized text and structured-content copy. Paging reserves
room for that transport overhead. Paging stops at the
byte limit or the requested record limit (default 20, maximum 100). `next_cursor: null` ends
the collection; cursors are offsets against the same immutable input Spec. Geometry is absent
by default. Oversized detail records become `{id, detail_ref}`; the full record stays available.
A committed delta's subsequent pages are read using
`resources/read {uri: "draftwright:<delta_ref>", events: true, cursor: <next_cursor>}`;
this does not commit the ops again. Preview pages can also be recomputed with the same input.
Lossless JSON artifacts use `resources/read {uri: "draftwright:<artifact_ref>"}` (text);
PNG/PDF artifacts return base64 blobs. These explicit detail reads can exceed the default budget.
The PNG image content is also intentionally outside the text budget. `get_spec` returns the
complete author document, which grows with authored intent rather than projected geometry.
`spec_schema` is also an explicit, unpaged full-document read outside the default list budget.

## Library, cache and handle model

`stateless::Cache` exposes `apply(part_ref, spec, ops) -> Applied {spec, delta}`, `preview`,
`items`, `item`, `problems`, `options`, `summary`, and `render(region, format, overlay, dpi)`.
The cache object contains only a filesystem root. Each call reconstructs the same `Session`
from immutable documents, then discards it. No undo stack, live part or planner survives a call.
The cache affects cost, never answers. `serve` additionally exposes `stateless_apply` and
`stateless_preview` for lossless subprocess access, and all tools as `mcp_<tool_name>`.
Tool subprocess requests may put fields inside `arguments` (e.g.
`{"op":"mcp_get_item","arguments":{"part_ref":"...","id":"d:..."}}`) so the transport's
request ID does not conflict with the item's ID. Existing live Session operations are preserved.

`part_ref` is `<STEP SHA-256>-<engine SHA-256>`. `build.rs` derives `ENGINE_VERSION` from
sorted, length-delimited relative paths and contents of `src/**/*.rs`, Cargo.lock, Cargo.toml,
the build scripts, all `schemas/**/*.json` documents and bundled `*.ttf`/`*.otf` fonts. Stray files
(such as `.DS_Store`, swap files and backups) are excluded. Cargo watches files and
directories, so edits, additions and removals of relevant inputs invalidate the key
automatically; no manual suffix bump is needed.
Absolute paths and timestamps are excluded, so identical checkouts share keys. Unknown keys, including keys
from another engine, explain registration with `inspect_part {step: ...}`. Registration copies
STEP bytes, their original basename, and optional recognition JSON. Cold keys compute Analysis,
HLR and visible bounds on demand. Warm calls read Analysis and projections/bounds from disk;
STEP B-rep still parses on reconstruction, but recognition, semantic PMI parsing, HLR and bounds
need not rerun. Source SHA/schema checks refuse damaged bindings.

The local layout is:

- `parts/<part_ref>/`: immutable source and registration, Analysis, HLR/section/placed-geometry
  cache (visible/hidden curves and visible bounds), drawing heads and version-edge references.
- `specs/<hash>.json`: immutable, canonical serialized bound Specs; an applied version hash includes parent and ops as well as Spec content.
- `artifacts/<hash>.<json|png|pdf>`: immutable plans, deltas, history edges and renders.

Sorted numeric cache keys and round-trip float parsing preserve exact projection bytes.
Per-part OS file locks protect cold computation, projection replacement and head comparison
across processes. Writes stage adjacent files and rename; a refused edit never advances its head.
`apply_version` records parent, ops, new Spec and lossless delta before updating the head.
Initial/standalone Spec hashes identify content. Applied version hashes include parent and ops, so a no-op also creates a distinct immutable version and history edge.
Registration retains the first basename for a digest. Different recognition for the same key is
refused: evaluation bridges use a separate cache root from native recognition. There is no
cache eviction in this slice. Operators may remove regenerable artifacts; persisted versions
and history are author state and should be retained.

PNG uses the existing deterministic renderer, with a direct paper-region viewport (millimetres,
y up: `[left,bottom,right,top]`). ID overlays use the bundled font over resolved item bounds;
PDF uses the existing delivery renderer. All exports retain the bound Spec, including crops.
File exports through `api::write` (CLI, JSON-lines `render` and the compatibility MCP server)
use unique sibling staging files created exclusively, then atomic replacement per destination.
Concurrent exports to one prefix use last-writer-wins per file (the last successful rename).
Cleanup removes only the invocation's staged copy. A multi-format export is not transactional:
formats may come from different callers, and a later failure leaves earlier published formats.

No new Rust crate was added: std supplies sockets, OS locks and atomic files; existing serde,
serde_json and sha2 supply serialization and hashes. `serde_json/float_roundtrip` is enabled
because persisted doubles must read back bit-identically. The independent schema check uses
Python `jsonschema==4.25.1` as a development dependency, not an engine dependency.

## Production integration design: draftwright-io

This section is a design only. The existing authenticated `/mcp-stateless/` endpoint keeps
Supabase bearer-token validation, `X-Maquetto-Project` scope, upload/artifact/job tools, and
leased jobs that any Fly machine can execute. It adds the ten drawing tools above and the
`analyse`/`render` conveniences, publishing the same schemas. It replaces immutable script
versions with immutable Spec versions; no sticky Session is introduced.

1. Upload gives a project-scoped STEP artifact. The server computes its SHA and combines it
   with the deployed engine revision to obtain `part_ref`. Project access is checked before
   resolving that reference or accepting any client-supplied Spec/artifact handle.
2. Registration/analysis jobs stage STEP and recognition inputs in a worker's persistent
   cache. The authoritative Analysis, HLR and bounds artifacts live in shared object storage
   keyed by content and engine version. A worker hydrates its local cache; a cold miss creates
   a leased job handle, while a warm query answers synchronously. Local locks coordinate one
   host only; storage/database leases coordinate Fly machines. A future Python binding may
   replace the subprocess without changing the functions or schemas.
3. The server calls `draftwright serve` with `mcp_inspect_part`, `mcp_list_items`, etc., or the
   lossless `stateless_apply`/`stateless_preview` interface. Requests carry the staged `part_ref`
   and complete Spec or a resolved version. Large artifacts go to object storage; local paths
   are never advertised as public capabilities. The server maps raw content refs to existing
   authenticated artifact handles/URLs and image content.
4. `apply_ops` names a project drawing and immutable parent version. The server resolves its
   Spec, runs the pure apply operation, then transactionally compare-and-swaps the database
   head and persists the new Spec, parent, ops and delta. A race refuses with the current
   project version. The local `apply_version` lock/head implementation is for development;
   it is not a distributed lock. History/undo uses the version chain, not a worker's memory.
5. Rendering is a leased job, resulting in project-scoped PNG/PDF artifact handles. Crops and
   ID overlays are render parameters. Job status/artifact tools remain the existing ones.
   Error choices map engine IDs/versions to project handles; no foreign-project paths leak.

Wiring, rollout flags, auth changes, cloud persistence and a real-model evaluation are deferred.
The editor's WASM Session joins the parity oracle in S5; the native Session/serve/HTTP oracle
here covers the complete shared S4B S0 op sequence on all four slice parts.

## Reproducible acceptance

- `cargo test --release --test core stateless::http_` runs the HTTP byte-limit, absolute
  head/body deadline, bare-LF refusal and queued-ping regressions. These tests also
  run in the existing Linux CI nextest shards; the Python probe below is optional.
- `python3 tools/check_http_limits.py target/release/draftwright --report target/http-limits.json`
  checks oversized request lines/headers, aggregate overflow, newline-free input, head/body
  trickles and a queued valid ping after each refusal. It also checks the exact head-budget
  boundary with multibyte UTF-8, body cap, Origin rules, loopback binding and valid JSON-RPC.
- `DRAFTWRIGHT_CORPUS=... DRAFTWRIGHT_CORPUS_REQUIRED=1 cargo test --release --test core stateless:: -- --nocapture`
  checks schema contracts, 4 KB budgets, fresh-process warm-cache equality, Session/serve/HTTP
  op-sequence parity, refusal/concurrency behaviour, transports and render embedding.
- Install `jsonschema==4.25.1` into a Python environment, then run
  `python tools/check_mcp_schemas.py target/release/draftwright` for independent schema validation.
- `node tools/mcp-eval/run-scripted.mjs --out target/s7-mcp-eval.json` starts a local HTTP adapter,
  runs all seven unchanged S0 tasks, and fails if any task fails. `--url` connects to an existing
  adapter; `--binary` selects a binary. The task set/features and exact S0 baseline are copied
  from draftwright-editor `origin/thin-slice`. Parts resolve from the bundled mounting plate
  and `DRAFTWRIGHT_CORPUS`. The judge's success-condition logic is unchanged; only its import
  and task-file location were adapted. Per-task bytes/tokens exclude judge calls as S0 did;
  tokens are `ceil(bytes/4)`, not actual model tokenizer counts. Fixed context is reported
  separately. Task IDs/quantities are known scripted inputs, not inferred model outputs.

D1 adds the real-model call client and finished-task checker; see
[`tools/mcp-eval/README.md`](../tools/mcp-eval/README.md) for starting HTTP, listing tools,
calling, logging bytes/tokens and scoring. Served descriptions explain each tool without
requiring source access. Schemas factor repeated shapes through local JSON Pointer targets while
remaining standalone. `cargo test --release --test core engine_version::` checks the source hash,
cache namespace, tool descriptions and the exact 36,393-byte per-session schema total (28 documents, 14 tools).
See [the measurement](mcp-schema-budget.md) for baseline attribution and per-tool sizes.

`inspect_part.key_items` is a deterministic, 700-byte index: envelope dimensions and
representative feature sizes/dimensions. Use `list_items` for the complete list.
`inspect_part.problem_filter` batches opening and a problem query; its `problem_page`
has the same paging contract as `list_problems`. A problem filter's `feature` matches
all associated item subjects and the feature itself; `subject` remains exact and all
filter fields combine. Default and detailed problem rows carry ordered `fixes` alternatives, each with `scope`
(`local`, `view`, `sheet`), `description` and one atomic `ops` bundle; `fix` retains the
preferred bundle for older clients. Local alternatives precede view and sheet remedies.
Descriptions state the actual changes in drawing language. Sheet descriptions add “The whole sheet is laid out again, so placements you have moved by hand may change.” Applying a fix applies its operations. Item answers and options add
`operation_scopes`, and each `settable` descriptor includes path-derived `scope`. A `page_fit_uncertain` row's fix
sets the nearest sheet size and/or scale that fit (`docs/model.md`); `sheet/references` (boolean, default false) opts into automatic parenthesized reference
dimensions; pins and per-dimension reference style keep explicit intent. `sheet/projection` (`first`
or `third`) is a spec path like `sheet/size` and `sheet/scale`, and the regenerated schemas
(the `spec_schema` response / `schemas/spec.json`) list it. `sheet/zones` (boolean, default
true) draws ISO 5457 grid zones and centring marks on every sheet; `false` omits them unless
section cross-references cite zones (`sheet_zones_kept` info).

`item_options.settable` maps escaped Spec paths to accepted `choices`, numeric `range`
(inclusive, with `integer` where needed), tolerance `forms`, or title `fields`. A
fit descriptor with `paths` applies to all listed feature paths. Fits list only classes
valid for the feature kind and recognised size in the engine's ISO 286 table. Tolerances
are strings (`±T` or `+U -L`); fits use `features/<id>/fit`. A feature's item also lists
`features/<id>/datum` (`forms`: a letter) and `features/<id>/controls` (`fields`; the 14
characteristics are in `docs/model.md`) for the feature its ID names; a face takes the
same at `faces/<bottom|top|left|right|front|back|face:n>/…` (`docs/model.md`, "Datums and
controls authored in the Spec"; the full Spec schema's `faces` in `schemas/spec.json`). It also lists
`features/<id>/finish` (`fields` `ra`, `removal`), `/note` (`forms`: text), `/thread` (`fields`
`designation`, `length`) on a hole, hole pattern or turned diameter and `/knurl` (`fields`
`pattern`, `pitch`) on a turned diameter; the sheet item lists `sheet/default_finish`
(`docs/model.md`, "Finish, threads, knurls and notes authored in the Spec"). View keys are bare (`iso`)
and the drawing number is `sheet/title/number`. Compatible view/side pairs remain in
`placements`; candidates require preview/apply, and a view relation needs both
`relative_to` and `side`. Set several subfields by setting their complete `place` object.

For `plan`/`front` and `side`/`front` pairs (either direction), `place/align` choices list
`projection` first. It keeps geometry origins aligned across `side` and changes only the
clear gap between areas, including dimension bands; use it when nudging a projected view.
Choices reflect the current `relative_to`, or available projection partners before a relation
is authored. Other pairs, including `plan`/`side`, isometrics and details, offer only
`start`, `middle`, `end`, `outside`; requesting `projection` is refused with those choices.
First and third angle use the same alignment and their respective conventional sides.
Omitting `place` and existing alignment values keep their previous planning behavior.
See the [#109 reproduction and verification](projection-alignment.md) for measured examples.

A callout also lists `place/offset` (`range` per component of the pair): its bend's `[dx, dy]`
from its automatic leader tip in paper mm, which can put the label inside the view. It is a
pinned form of its own: set `{"offset":[dx,dy],"pinned":true}` with no side, lane or along
(`docs/model.md`, Placement). The full Spec schema's `$defs/place` in `schemas/spec.json` lists it.

Common unambiguous vocabulary errors include valid choices and an explicit
`Corrected op: {JSON}` in the refusal message. They keep the invalid edit unapplied;
The message identifies the replaced zero-based `ops[index]`; the correction is
previewed through Session and corresponds to the reported refusal. A batch with multiple
invalid operations can still require another retry. Review and resubmit the correction. Ambiguous feature ownership and missing engineering
magnitudes are not guessed. Batch all edits for one request in `apply_ops.ops`.


`inspect_part` exposes the Plan's bounded completeness counts (`claimed_faces`, `faces`,
`unplanned_features`) and a `status` of `clear` or `needs_attention`. Warning/error Plan
or render problems require attention; informational findings do not. Coverage gaps on
single solids warn, while several solids keep Python's informational severity. Face
coverage names the Analysis inventory's bound: proven stock faces are credited when all overall sizes are stated, and a
fully claimed inventory does not establish manufacturing readiness. The independent
finished-drawing lint is available from `review`; inspect does not silently mix that
separate assessment into its Plan problem collection.

Title-block requirement warnings are returned by the same problem queries:
`general_tolerance_missing` names `sheet` and offers a `set` operation at
`sheet/title/tolerance` with value `UNSPECIFIED`. This is an explicit acknowledgement
of a blank requirement, not manufacturing limits; clients can replace the offered
value with the author's intended general tolerance. `step_material_disagreement`
names `sheet`, includes both the authored and STEP material in its detail message,
and retains the Spec value. Both warnings use source/Spec paths in `refs`; neither
is attached to an unrelated feature.

## Operation and correction bounds

`preview_ops` and `apply_ops` accept at most **32 submitted operations**, including
no-ops and duplicates. HTTP, stdio, `serve`'s `mcp_*` tools, the lossless
`stateless_apply`/`stateless_preview` adapters, and Cache apply/preview/version calls
share this limit. Refusal happens before part reconstruction or head lookup. The
published input schemas use the same `maxItems`; regenerate them after changing
`stateless::MAX_OPS`. Lists' separate 100-record page limit is unchanged.

Corrective discovery has a separate **eight-operation validation budget** and
**one noncommitting Session preview**. It uses the original refused Session, with
no second reconstruction and no per-op planner replay. For an initialized drawing
head, these edit-validation refusals use at most two complete recovered-planner
invocations: the original Session reconstruction and,
when offered, the correction preview. A refusal during the original apply planner
adds that attempted invocation (at most three with a warm head). Initializing a new
drawing head may add its existing one reconstruction. Corrective analysis itself
still adds at most one plan. These counts include previews, unlike committed Session statistics, and exclude recovery's
internal trials. Cold analysis/projection work and individual planner cost are not
wall-clock bounded by this policy.

The whole batch is still validated transactionally. Its temporary invalid values
can be overwritten before final validation. The current Session/model refusal has
no operation index, so discovery uses bounded pure edit validation and reports the
matching zero-based index. An unknown `v:` view key is identified from the original
planner refusal's exact message and valid choices, without a planner replay.
Discovery keeps valid preceding edits for the candidate preview,
without installing them. Unrelated invalid operations can still require another
retry. If discovery runs out of operations, the candidate preview refuses, or options
cannot establish a safe correction, the original structured code/message/choices
are returned unchanged. Stale-parent refusals never enter corrective analysis.
Longer batches may receive useful refusal choices without an explicit corrected op;
use item_options and retry. Corrections remain suggestions requiring resubmission.

The Stage F schemas/runtime already enforced 100 operations, satisfying the transport
and schema portion of the first acceptance item. The reported corrective replay
still existed without an independent work budget, up to that transport cap. Thirty-two
reduces the input validation bound while retaining headroom for ordinary edits; eight discovery steps
and one preview trade corrections late in a batch for deterministic planner work.
These unattended choices can be changed following real request measurements.

Reproduce the regression/latency instrument with required corpus and
`cargo test --release --lib mcp::batch_tests -- --nocapture`, then check the actual
transports with `python3 tools/check_mcp_batches.py target/release/draftwright`.
The dense instrument uses native NIST CTC-04 (518 faces, 137 recognized features),
a warmed disk cache, and one release sample per tool and refusal position. It times
the complete tool call including test-only planner counting. The measurements below
are observed maxima across these cases, not a percentile or a hard deadline.

| Tool | Refusal index / submitted ops | Plans before | After | Latency before (ms) | After (ms) |
| --- | --- | ---: | ---: | ---: | ---: |
| preview_ops | 0 / 32 | 3 | 2 | 10645.568 | 8127.861 |
| apply_ops | 0 / 32 | 3 | 2 | 18240.143 | 6631.284 |
| preview_ops | 7 / 32 | 10 | 2 | 55861.236 | 6627.409 |
| apply_ops | 7 / 32 | 10 | 2 | 36974.945 | 6043.203 |
| preview_ops | 31 / 32 | 34 | 1 | 144450.113 | 2938.937 |
| apply_ops | 31 / 32 | 34 | 1 | 170866.449 | 3059.773 |
| preview_ops | 32 / 33 | 35 | 0 | 187626.482 | 0.030 |
| apply_ops | 32 / 33 | 35 | 0 | 255325.582 | 0.023 |

The largest observed latency across these probes is **255,325.582 → 8,127.861 ms**.
For batches within the new limit (32), it is **170,866.449 → 8,127.861 ms**.
Single samples are sensitive to scheduling and do not establish a per-case timing
guarantee. The 32/33-op probes were legal under the old 100-op limit; this does not
claim to have measured the old maximum-size 100-op batch. The 33-op after probes
stop before any Session reconstruction. Original/after logs and machine-readable
measurements are in `target/h5/`; the tests assert planner bounds and refusal
behavior, not timings.

The unchanged scripted MCP evaluation succeeds **7/7** before and after, with
**16 calls / 5,685 estimated tool tokens** in both runs on this checkout.
The complete required-corpus suite passes **671 tests / 71 groups**, zero
failures/ignored; fmt, strict all-target release Clippy, the six actual-transport
checks and all 24 independently validated schemas pass.


H1 adds `hole_table: "auto" | "always" | "never"` to the Spec (default auto).
Use `{ "op": "set", "path": "hole_table", "value": "always" }` in
`preview_ops`/`apply_ops`, or remove the field to restore auto. `item_options`
includes these choices. Generated standalone schemas accept the same field.
Scheduled dimensions remain inspectable as covered items; schedules and tags
are ordinary item records and participate in Session deltas.

H1 fix round: `hole_table_entry_invalid` is a warning when a rendered schedule's
entry coordinate/direction does not identify the physical opening, even if X/Y
are correct. A selected automatic table that cannot place all its ink falls back
to the original dimensions and reports `hole_table_unavailable` at info; an
unmet explicit `hole_table: always` request retains warning severity. The fallback
itself loses no requirement. Coaxial rows use a shared shown tag; per-row entry
cells distinguish them. `pcd` in the Plan names the circle's proven centre,
diameter and member IDs; its actual angular positions print in the schedule.

## Multiple sheets (R5, I3)

Sheet numbers run from 1 to 9. Item IDs remain unique and survive moves. Wherever
an item address is accepted (`get_item`, `item_options`, `list_items.filter.near`
and an overlay ID list), `sheet:<n>/<id>` checks the item's sheet. A wrong qualifier
is refused with the actual sheet and the correct address in `error.choices`.
Bare IDs keep working. `list_items.filter.sheet` selects one sheet; a `near` search
is local to its item's sheet, including when its ID is bare. A region without a
sheet filter applies the same paper rectangle to every sheet.

List rows, item answers and delta items carry `sheet` in a multi-sheet drawing,
including `sheet: 1`. An absent field means sheet 1; single-sheet result shapes
and the 4096-byte paging budget are unchanged. `inspect_part` adds `sheets` (the
count) and `sheet_details` (number, size, scale and page for every sheet) only when
there is more than one sheet. Analysis remains independent of sheets.

After enabling `Spec.sheets` (for example `set sheets/max 2`), a view's
`item_options` includes `move_to_sheet`. Its settable
`views/<name>/sheet` descriptor lists sheet choices and `consequences` explaining
keep-together groups and placement relations. Principal views offer only sheet 1.
Apply the usual Spec edits, for example:

```json
[
  {"op":"set","path":"sheets/max","value":2},
  {"op":"set","path":"views/iso/sheet","value":2}
]
```

The whole batch is validated together. A conflicting keep-together assignment or
`place.relative_to` relation is refused by the same model validation as the CLI.
There is no new `move` op kind. Moving a view reports it and its carried items as
`changed`; their IDs survive. `PlanDelta.collections.sheets` patches sheets 2..m,
keyed by derived `sheet:n` IDs. Geometry is still keyed by view ID. Rendered session
annotations gain optional `provenance` containing their owning items on multi-sheet
documents; it travels in `State.annotations` and `Delta.annotations`.

`view_sheet`, `render` and `render_pdf` accept `sheet` (default 1). Each returns
one selected page, retaining the complete embedded Spec. ID overlays are local:
`overlay_ids: true` labels every bounded item on that sheet; an array labels only
the supplied bare or qualified addresses and refuses items on another sheet.
The existing boolean form remains supported.

The Rust methods `State::render` and `Session::svg_fragments` retain their sheet-1
signatures. Select another sheet with `State::render_sheet`,
`Session::drawing_on_sheet`, `Session::svg_fragments_on_sheet`, or
`Cache::render_sheet`. Invalid page numbers return valid sheet choices. The session
keeps geometry for every sheet so deltas can replay without projecting again.
`Stats.sheet_steps` counts actual recovered sheet-1 runs, summed across committed
invocations; blocked preflight prefixes do not count. Previews leave Stats untouched.
`plans`, the 32-op limit and correction budgets keep their existing meanings.

Schemas inline one-use definitions and use equivalent nullable types and enums.
They omit redundant canonical `$id` URLs and the dialect annotation, using MCP's
[default JSON Schema 2020-12 dialect](https://modelcontextprotocol.io/specification/2025-11-25/basic#schema-dialect).
`op` remains published by apply/preview input schemas with local reference `#/op`.
The context budget is 34,477 bytes after replacing repeated nested Spec definitions
with the typed outline. Nested Spec fields no longer increase that total; generated
private schemas retain the existing server validation constraints.

`cargo test --release --test core sheets_mcp::` covers addressing, filtering, move/replay,
render selection and schema controls. `cargo test --release --test multi_sheet_latency
-- --nocapture` measures the fixed-A4 string_post 5:1 overflow case, including JSON
delta encoding, and reports actual sheet steps. It retains the 150 ms bound unless
`DRAFTWRIGHT_LATENCY_GATE=0`; local loaded-machine timings do not establish speed.
I2 owns later-sheet title numbering, zones and cross-reference furniture; the I3
session assembly selects that renderer's page and ink without allocating anew.


## CLI release discovery

The CLI release archive includes `draftwright`, `draftwright-mcp`, `schemas/`,
`docs/model.md`, `docs/mcp.md` and the linked `docs/rules/` catalogue. Internal working notes
are excluded.
`draftwright --help` lists commands and discovery paths; `draftwright --version` prints the
release version. `draftwright spec_schema` returns the full Spec schema. To discover an item's
choices, obtain a `part_ref` from `inspect_part` through `draftwright mcp-stdio` (or the
`mcp_inspect_part` serve operation), then run
`draftwright item_options <part_ref> <id> [--spec spec.json]`.

`serve` also accepts `spec_schema` and `item_options`, with the same arguments and results as
MCP. Use `{"id":1,"op":"spec_schema","arguments":{}}` or
`{"id":2,"op":"item_options","arguments":{"part_ref":"…","id":"sheet"}}`.
`serve` requires a nested `arguments` object for `item_options` (including `mcp_item_options`):
it keeps the item's ID separate from the request correlation ID. Missing, null or non-object
`arguments` are refused. CLI and direct API calls still take flat tool arguments.
The standalone `draftwright-mcp` also advertises both discovery tools.

A placed callout's Plan dimension record includes `callout_offset: [dx,dy]`, in paper mm,
y up. It is its current bend minus the canonical automatic tip, exactly the `offset` from
`Session::callout_offset`. Use it as `place.offset` with `pinned: true` to retain the bend.
The field is absent for other dimension kinds, undrawn callouts and non-canonical carrier
views. Older saved plans can omit it.
