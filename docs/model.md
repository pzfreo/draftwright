# The draftwright model: three documents and five operations

Spec validation refuses an instruction only when it would state something false,
names a quantity that cannot be measured unambiguously, or cannot be drawn
truthfully. Otherwise generation produces the drawing and explains the shortcoming
in a specific warning. Failure to find layout space is not proof of impossibility.
Strict automated acceptance remains separate from generation.

The refusal-family audit (#308) keeps measured-label placeholders, unique and
revision-safe geometry references, incompatible requirements (including semantic
PMI), readable document/symbol/precision syntax, finite geometry and positive
scales, valid sheet/view relationships, recognition proofs and gear geometry
checks. A missing source view or degenerate crop cannot support a truthful detail;
a failed layout search can, however, leave the parent drawing and warn.
Truthful same-feature repeats and repeated scheme intervals warn as
`duplicate_measurement`. Stale or covered label edits and unavailable placements
retain their Spec intent and warn; authored omissions are allowed. Fits print the
valid ISO 286 class independently of the numerical table: only requested deviations
that cannot be calculated warn, and none are invented. Unique transverse axis
origins and the checker's Cartesian location-role vocabulary are accepted.
`axis:f:slot:<id>` names the unique centre of a recognised slot in the part frame.
Sheet-layout retention is an explicit transaction contract; its strict edit check
is separate from whether a drawing can be generated. Unknown document versions,
malformed notes/tables and unsupported representation syntax remain unreadable
instructions, rather than presentation shortcomings.

This note describes how a drawing is described, edited and produced in draftwright-rust. It
replaces the Python implementation's authoring DSL (the fluent `Sheet` facade, the script
emitter and the deferred-intent compiler) with a small, stateless API. A command line, a
JSON-lines service and an MCP server can all sit thinly on top of it. The decision and the
reasons for it are recorded in [ADR R1](adr/R1-document-model.md); this note is the reference.

## Why

- **For an LLM, an API works as well as a program, at far less cost.** In the Python version,
  changing one callout means editing a generated script, running it and resubmitting it whole.
  pzfreo/specify-core shows the alternative: small stateless calls over plain JSON, with
  explicit IDs, defaults that explain themselves, and errors that say how to fix them.
- **The cost of the old approach was real.** In the Python version:
  - about 12.5k lines are authoring surface and about 35k compile and orchestrate the result;
  - a script emitter has to round-trip the whole DSL;
  - finalising snapshots and restores about ten collections in a hand-rolled transaction;
  - an auto path and a replay path are kept in step by a shared sequence of thirty passes.
- **The part worth keeping is the narrow waist.** Features become a plan of dimensions and
  annotations, and renderers draw only from that plan. That boundary fixed a real class of bugs
  (#921), and an API needs it just as much: the plan is what a caller inspects and edits by ID.

## The three documents

All three are JSON, versioned by a `schema` field, and deterministic. The same inputs give the
same bytes.

### Analysis: what the part is

`analyse(step)` produces it. It can always be regenerated from the STEP file, and it is never
edited.

```json
{
  "schema": "draftwright.analysis/7",
  "binding": { "source_sha256": "…", "part": "10127", "face_count": 27, "loader": "haecceity 0.1" },
  "frame": { "origin": [0, 0, 0], "axes": [[1, 0, 0], [0, 1, 0], [0, 0, 1]] },
  "envelope": { "min": [0, 0, 0], "max": [28.8, 32.3, 44.7] },
  "features": [
    { "id": "f:hole:3", "kind": "hole", "faces": [12, 13],
      "parameters": { "diameter": 8.1, "depth": "through" },
      "fingerprint": { "…": "quiddity's revision-stable signature, see Rebinding" } }
  ],
  "levels": [28.288, 32.368]
}
```

- **Levels** (`draftwright.analysis/3`) are the heights of the part's horizontal face levels,
  read from the geometry as quiddity's `step_level_records` reads them but in the part's own
  frame: what a prismatic part's height ladder dimensions. Older analyses read with none.
- **Level supports and risers** (`draftwright.analysis/4`): each level's support (its height and
  the x and y extents of its faces, per solid) and the step risers between the levels (quiddity's
  `FaceLevel` and `RiserEvidence`, read with Rust quiddity in the part's own frame, as
  draftwright's own recognition run reads them). The height ladder's witnesses stand on a level's
  support, and its shoulders' positions come from the risers (`src/dims/levels.rs`). quiddity's
  recognition document cannot give them: it reads them in its working frame, whose z need not be
  the part's. Older analyses read with none; a version 3 analysis with levels gets an info
  problem saying the step positions could not be found.
- **Unrecognised families** (`draftwright.analysis/5`, `unrecognised`): the quiddity families
  the recogniser did not give, each with how many records of it its document has on the part
  (absent when the document does not carry the family at all). Empty, and left out, for Python
  quiddity's JSON, which the bridge reads in full; for Rust quiddity's recognition
  (`model::analyse_native`, the command line's default) the families its document has that
  `bridge::native` does not read. Unified recesses and
  their patterns are represented by recognition/3. `plan` reports unread families as one
  `features_not_recognised` warning naming the affected geometry and suggesting
  `feature_declarations` for supported missed features. Unknown occurrence counts are stated
  as unknown. Tool/version provenance and original reader diagnostics stay in problem `refs`
  (and lint `evidence`), outside drawing-user messages. `plan` reads older analyses with none;
  `render` and `review` draw only from this version.
- **Principal profiles** (`draftwright.analysis/6`, `principal_profiles`): for each solid of a
  part of several (else the whole part), its box and each principal boundary face (a plane flat
  across one axis on that box), with its inner loops' edges and its outer loop's arcs: the B-rep
  scan draftwright's lint makes for undimensionable openings and toothed outlines, made here when
  the part is analysed because lint never sees the part (`src/lint/profiles.rs`). `plan` reads
  older analyses with none; `review` judges only this version.
  Principal faces now also retain optional `outer_lines` (straight outer boundary edges).
  Plate planning can choose a boundary profile whose two thickness faces have overlapping
  solid edges, and anchor its witnesses there. This is model-space Analysis evidence;
  the Plan does not copy the projected line-work inventory. Each profile source also retains optional
  `boundary_segments`: model-space B-rep chords used as tag-clearance obstacles.
  They include internal face edges and analytic cylinder/cone generators tangent
  to principal datum directions. Optional `curved_faces` retain the remaining
  curved surfaces' bounds and revolution axes for conservative clearance. Older analyses default to no straight-edge
  evidence and keep the axis-map fallback. Profile visibility is proved only for one solid;
  body bounds are transformed from the recognition frame before matching.

- **Edge evidence** (optional `edge_ink`, `draftwright.analysis/7`): every edge as a
  polyline in the part's frame (within 0.05 mm, coordinates to 0.001 mm), planar faces by
  normal and loop edges, and curved faces by box (cylinders also by axis and radius). The
  planner projects it into a view to prove a region blank before placing a label inside
  (`src/model/projected_ink.rs`, `src/dims/interior.rs`); the projection is a superset of the
  drawn ink. Rendering does not read it. Older documents default to none and keep every label
  outside its view. The Plan records only the resolved annotation's `region: interior` and
  basis. See [R1's decision note](adr/R1-document-model.md).

- **PMI** (`draftwright.analysis/7`, `pmi`): the part's AP242 semantic PMI as `src/pmi.rs`
  reads it (`read`, the report: records with their faces, and every source not read with its
  reason), each geometric tolerance and datum with where it attaches (`geometry`: a datum's site
  on each face, its plane normal or cylinder axis and its outward normal; a tolerance's common
  cylinder axis), derived from the faces as draftwright's `_datum_geometry_from_shapes` derives
  them; or `refused` with the reason when a file naming semantic PMI cannot be read. Absent when
  the file has none (presentation alone is counted). `plan` lowers it (below); older analyses
  read with none. Manufacturing requirements (threads and knurls with their structured fields
  and faces, general tolerances, surface texture and part notes (semantic text on the part:
  `general note`, `surface treatment`, `heat treatment`, `edge condition`); AP242 surface textures, a
  part's as a default `surface_texture`, a face's as a `surface_finish` with its material removal
  condition and parameters in `fields`), surface labels and common
  labels are records too; a surface or common label carries its proven sites (`geometry`, below)
  and is drawn as a label; the drawing-wide requirements are the sheet's (below); a thread or
  knurl carries its cylinders (`geometry.cylinders`) and is printed on its owner's callout
  (below).
- **A part slightly off the axes** (#216; no schema bump, `squared` is optional and absent for
  a part on the file's axes). When the part's own frame (quiddity's `infer_part_frame`) lies
  more than 0.01° and at most 2° off the file's axes, as a library or exported model often
  does, the part is turned about its centre of mass onto the file axes it is nearest before
  anything is read from it, so its envelope, features and views are its own, not a tilted
  box's. `squared` records the angle and the placement applied, every later read of the file
  for that Analysis applies it again, and `plan` reports `part_squared_to_axes` (info). A file
  that carries PMI is never squared: its frames, datums and sites are placed and measured in
  the file's axes. A part further off its axes is drawn in the file's axes as before. The
  frame is worked out only when some plane normal or cylinder axis is off the file's axes. No
  Spec field turns squaring off yet: the Analysis is made before any Spec is read.
- **Assemblies: one chosen part** (draftwright-rust #8, slice 3; no schema bump, every field is
  optional and absent for a file of one part, whose Analysis is byte-identical). A STEP file's
  distinct parts are listed by `draftwright parts part.step` (serve/MCP `parts`):
  `{"parts": [{"part": 0, "name": "plate", "product_id": "plate", "faces": 13, "instances": 1},
  …]}`, in haecceity's order (`read_part_definitions`). A part is chosen by its **index**
  (`--part K`; `"part": K` in a request), with the name and product id reported beside it (the
  maintainer's D1). Part K is read alone, in its own frame (`haecceity::StepFile::part`: its
  first placement's shape with no placement applied), so a part placed twice is drawn once.
  Its `binding.part` is the part's name and `binding.part_index` is K; `face_count` and
  `envelope` are the part's own. Its PMI is the part's only: other parts' records, findings and
  material are dropped, not refused (`pmi.drawn` = K), and a material it states fills the title
  block. With no part chosen an assembly is read whole (D4), except as below: the Analysis lists the
  file's parts in `parts` (only when there are two or more) and `plan` warns
  `assembly_drawn_whole`, naming the parts and how to choose one. Bodies that lie on top of an
  earlier one (#212: their faces pair off with the same surface type and box, to a micron, as
  placed) are listed in the Analysis's `coincident`. When every body of an assembly coincides
  with the first, that part is read alone as `--part K` reads it (`binding.part_index` is K), so
  no feature is counted once per copy, and `plan` reports `coincident_bodies` (info); otherwise
  the file is still drawn whole and `coincident_bodies` warns that the copies' features are
  counted again. When the bodies are distinct and every other body lies within the box of the
  largest (by box diagonal, as placed; #219), that part is read alone in the same way, with
  `principal` set in the Analysis, so a body it holds (a pulley's set screws, a bearing's inner
  sleeve) is not dimensioned as its features; `plan` warns `assembly_principal_drawn`, naming
  the parts not drawn and how to choose one. Parts side by side, or a pin standing out of a
  plate, are still drawn whole. An Analysis made from one
  part is re-read from that part when passed back (`--analysis`), and a `part` beside it must be
  the same. An index the file does not have is refused (`invalid`), naming the parts. Nested
  sub-assemblies are flattened to their leaf parts; a sub-assembly is not selectable (D2).

- **Feature IDs** are stable within one file. They are anchored on face indices and derived
  from the feature's kind and faces, never from the order it was found in.
- **Features** come from Rust quiddity. Until a family is ported, Python quiddity's JSON fills in
  through a bridge (`src/bridge.rs`): `tools/recognise.py part.step -o r.json` writes it, and
  `analyse`, `plan`, `render` and `review` take it as `--features r.json` (an optional
  `features` path in the service and MCP requests). The bridge refuses JSON from
  another file or whose faces (count, surface type, area) are not the part's. A feature's `n` is
  its rank among its kind ordered by faces; `recognition` records the recogniser and the frame
  quiddity's parameters are in. Every quiddity record type has one home: a feature, evidence
  (`recognition.evidence`, e.g. face levels and risers) or a refusal (`recognition.refusals`,
  with quiddity's reason); a record type the bridge does not know is refused. `recognition` and
  `features` were added to `draftwright.analysis/1` as optional fields, so analyses written
  before them still read. Rust quiddity's CLI (quiddity-rust at c9ce1cd) emits, beside its
  records, fingerprints that name each feature's defining faces, but its records themselves do
  not carry their defining faces, so the bridge does not read it yet.
- **Slot patterns** are features: quiddity's derived `slot_patterns` (`SlotArray`, `SlotGrid`)
  become `slot_pattern` features, `f:slot_pattern:<n>` ranked by faces, on their member slots'
  faces, with quiddity's record (its pitch and direction, or its rows, columns, pitches, angle and
  centre) whose `slots` names the members by feature ID in quiddity's order. A member must be one
  slot record, and the members' own pattern links must agree, or the read is refused; members
  and pattern carry the pattern's count and pitch in their fingerprints. No new field: analyses
  written before read as they did (with no slot patterns).
- **What the bridge knows** (ADR3-4): it reads quiddity's document format `quiddity-recognition`
  version 3, written by a quiddity release it is checked against (`bridge::QUIDDITY_VERSIONS`:
  0.3.9 and 0.4.0.dev0, and the one the wrapper names), in local coordinates, with only the
  families, family/record-type pairs (`bridge::RECORDS`) and derived collections it knows.
  Anything else is refused with the mismatch named, never read past.

### Enlarged details and turned inspection dimensions

Automatic details recover a crowded X head, a Y axial chain, or approved prismatic height
rungs that fail the parent-view spacing gate. A thin terminal flange on a Z turned part also
gets a head detail. Repeated pitches stay counted on the parent. Details reserve rectangles
in the sheet search before the isometric is fitted. An automatic detail that cannot fit on
the parent sheet is reported as `detail_unplaceable`; it cannot alone force a larger sheet.
A complete turned-head drawing with a defining enlargement keeps its smallest sheet. Prismatic
layouts may still use the normal fill search, provided it preserves clear leaders.

Details are ordinary planned views with stable IDs `v:detail_a` through `v:detail_h`. Their
optional `detail` record carries the source view, letter, kind, model-space crop and marker
bounds, scale and recovered dimension IDs. The renderer clips exact projected curves to the
crop, then uses the normal dimension renderer at the recorded view scale. It draws a circular
parent marker, letter and `DETAIL B (10:1)` caption. The kernel currently has no solid Boolean
crop: a crop does not introduce new cut-face edges. A Y chain or X head may show a supported
strip of the profile. View-scale fidelity and pairwise lint still compare all annotations in
one pass; detail captions also have their own text-clearance check.

An authored detail is a view entry, for example:

```json
{"views":{"detail_c":{"detail":{"around":"f:turned_step:3","scale_factor":5}}}}
```

`scale_factor` is an exact multiple of sheet scale (default 2, greater than 1). Without
`from`, a single feature keeps its existing source camera, crop and context margins.
`around` also accepts a nonempty list of feature IDs and/or face IDs, using the same face
names as `faces/<face>` (`face:<n>`, `top`, `bottom`, `left`, `right`, `front`, `back`).
Its crop is the union of the members' model-space crops, retaining each feature's existing
context margins. Face crops use the faces' attachment bounds. The Spec contains intent
only: no crop coordinates are authored.

```json
{"views":{"detail_c":{"detail":{"from":"side","around":["f:groove:1","f:turned_step:2"],"scale_factor":3}}}}
```

Optional `from` selects an existing principal or section view ID without the `v:` prefix,
for example `front`, `plan`, `side` or `section_aa`. The detail uses that parent's camera,
projected crop and section plane, and the parent carries its marker. Every named member
must exist. Each member's crop must project to a non-degenerate region in an explicit
`from` view; an edge-on planar face is refused, even if other members would give the union
area. Visibility here means non-degenerate crop projection, rather than an occlusion test.
An omitted, automatically dropped or absent source view is refused; explicitly retain a
principal view with e.g. `"side":{}` if needed.

When a list's natural source views differ and `from` is absent, choose the available view
where the most members' crops have non-degenerate projections. Ties prefer front, plan,
side, then section. The detail's basis records the chosen view and visible member count.
A shared natural source retains existing behavior. Single-string and list forms survive
saving and reopening unchanged.

Authored crops survive with no annotations. Omitting the view removes the detail. Missing
members, source views, or space for the exact scale are errors. An automatic iso that fits
stays at sheet scale when a defining detail claims space; an explicitly authored iso scale
remains exact. The typed MCP detail schemas total 47,732 compact UTF-8 bytes per session
(previously 42,356); the measured budget is 48,000 bytes. See the
[authored-detail verification](authored-detail-verification.md) for regressions and inspected PNGs.

A whole connected multi-step turned profile uses one inspection end by default. PMI datums
and PMI baseline lengths retain precedence. Otherwise the larger flat end supplies the
origin (end diameter is the available proxy for face size), with the low station breaking a
tie. Shoulder positions, the overall length and
cross-hole axial positions use that origin, shortest nearest the part. The old `step.length`
IDs remain valid: J4's coverage records connect conveyed band lengths to their baseline
positions. Explicitly toleranced band lengths remain independent controlling requirements;
their tolerances are never silently transferred to a cumulative position.

The default drawing title prefers a meaningful STEP product name for a single part or
selected assembly member. Empty names and generic CAD names such as `Part1`, `Body`
and `Solid` fall back to the file stem. Underscores and hyphens become spaces and
the title is printed in upper case. An explicit `sheet/title/title` always wins.
`product_name` retains the STEP name; a selected assembly member also retains `source_stem`
so generic member names still fall back to the source file. Older analyses without those
fields use their binding's display name.

### Spec: what the author decided

The spec is the source of truth. It is small, edited, and stored beside the STEP file as
`part.drawing.json`, and inside every output so the work can be resumed. Anything it doesn't
mention takes its default.

```json
{
  "schema": "draftwright.spec/2",
  "binding": { "source_sha256": "…" },
  "sheet": { "size": "A3", "scale": "2:1",
             "title": { "title": "BRACKET", "number": "DWG-001", "material": "6082-T6" } },
  "views": {
    "plan": { "place": { "relative_to": "front", "side": "above",
                         "gap": 10, "align": "middle", "pinned": true } },
    "side": { "omit": true }
  },
  "features": { "f:hole:3": { "fit": "H7" } },
  "dimensions": {
    "d:f:hole:3/bore.diameter": { "tolerance": "±0.05" },
    "d:envelope/width.length": {
      "place": { "view": "front", "side": "above", "lane": 1,
                 "along": "middle", "pinned": true }
    }
  },
  "annotations": {
    "pmi:datum.A:f12:t=A": { "place": { "view": "front", "side": "left",
                                      "lane": 0, "pinned": true } }
  },
  "notes": [ { "id": "n:1", "text": "DO NOT SCALE" } ]
}
```

`views/sections` requests a longitudinal section through named geometry, for example
`"sections": [{"id": "A", "through": "f:section_recess:1"}]`. IDs are distinct
letters A–H. When drawable, the first request replaces the automatic section, using a
front-facing plane through a z-axis hole or a z-depth pocket running along x. The plan records
the cut and the applied Spec path. A pocket section carries its measured width and
length as a callout (width lies perpendicular to this plane), and a floor-attached depth
from floor to mouth. On a cylindrical mouth this is the maximum depth from its
tangent at the centre cut. Missing feature references are refused. Geometry for
which this implementation cannot name a plane, additional sections beyond the one
available layout slot, or an omitted carrier view retain the drawing and warn
specifically. An unavailable named request keeps the automatic section and remains
in the Spec. Explicit sections reserve a separate labelled frame before paper and
scale selection, including when dimensions or views are pinned. If adding the cut
makes retained pinned ink unfit, automatic paper/scale trials include that ink.
Feature `anchor` is read for recognition corrections, below;
a feature's `note` is read since K1b (below).

`views/section_aa: {}` also requests a section through plain z-axis through holes.
The densest row determines the plane, with ties going to the row nearest the
part centre and then the lower station. This extends explicit requests only;
automatic sections keep the hidden-profile rule. If no supported plane can be
named, `section_not_shown` explains the missing geometry and the drawing is kept.

- **Fields read:** `sheet` (`size`, `scale`, `preferred_max_size`, `preferred_max_sheets`, `dimension_spacing`, `frame_gutter`, `table_columns`, `title`, `references`, `fit_deviations`, `fit_limits`, `projection`, `default_finish`, `hidden`, `centre_marks`, `centre_lines`, `thread_depiction`, `knurl_depiction`), `sheets` (below), `views/<name>` (`omit`, `place`, `sheet`, `hidden`, `centre_marks`, `centre_lines`, `thread_depiction`, `knurl_depiction`, `half_section`, `break`),
  `iso_scale_factor`, `dimension_source`, `dimension_schemes/<name>` (`scheme`, `axis`, `targets`, `view`, `from`), `hole_table` (`auto`, `always`, `never`), `dimensions/<id>` (`omit`, `tolerance`, `fit`, `text`, `style`, `place`, `kind`, `between`, `along`, and the earlier `view`
  spelling), `annotations/<id>` (`omit`, `place`, `text` for prose callouts and automatic notes), `features/<id>` (`fit`, `datum`, `controls`, `finish`, `thread`, `knurl`, `note`, `anchor`, `reclassify`, `suppress`, `split`, `pattern`, `gusset_view`), `feature_declarations`,
  `faces/<face>` (`datum`, `controls`, `finish`, `note`: "Datums and controls authored in the Spec" and "Finish, threads,
  knurls and notes authored in the Spec", below; `profile_corners`: "Outer-profile corners", below), `notes` and `tables`.
  Unknown fields, including page coordinates, are refused. Absent fields keep their defaults.
- **The spec stores intent, never geometry.** A fit, lane, side or fractional label position is
  resolved against the part and its anchors on every plan. Measured values and resolved paper
  coordinates occur only in the Analysis and Plan.

`iso_scale_factor` is an optional finite positive multiplier of the sheet scale, kept exactly
through automatic fitting and recovery. An infeasible authored iso scale is refused rather
than reduced. Without an override the orientation view fills its available space, retaining
the sheet scale within Python's strict 5% no-op band. A fitting sheet-scale iso stays at that
scale when a detail claims space (see enlarged details above).

When the drawn iso differs from its sheet scale, `Plan.captions` carries `note_iso_nts`,
text `ISO VIEW (NTS)` at 3 mm, with its view, sheet, resolved position and ink bounds. It is
an automatic read-only `caption` item, reserved before late notes and tables. It tries
Python's positions below, further below, left, right and above the iso, keeping 2 mm clear
of settled view and annotation ink and the title block. If every position is blocked, it
retries without the extra text clearance, then slides the candidates vertically to clear
obstacle edges. Only an entirely blocked search retains Python's natural-position fallback;
structural lint reports even shallow caption contacts with labels, lines and view edges.
A sheet-scale or omitted iso carries no NTS caption. This furniture does not change the sheet-choice ladder or fill.

#### Recognition corrections (#76 item 2)

The automatic reading remains the default. Corrections sit beside a feature's other authored
requirements; missed features have a separate declaration list. They carry intent and stable
face fingerprints, never measured sizes or page coordinates:

```json
{
  "features": {
    "f:boss:1": { "reclassify": "turned_step" },
    "f:hole:9": { "suppress": true },
    "f:hole_pattern:1": { "split": true },
    "f:hole_pattern:mounting": {
      "pattern": { "arrangement": "grid", "members": ["f:hole:1", "f:hole:2", "f:hole:3", "f:hole:5", "f:hole:6", "f:hole:8"] }
    }
  },
  "feature_declarations": [
    { "id": "f:declared:pilot", "kind": "hole", "faces": ["face:sha256:<64 hex digits>"] }
  ]
}
```

The example's actions are independent; a grouped member must remain an active hole. `pattern`
accepts `linear`, `grid` or `bolt_circle`; all count, pitch, centre and diameter data is derived
from its physical members. Authored pattern IDs require a nonnumeric name, keeping them
separate from automatic pattern ranks across re-analysis. A line must be evenly spaced along a principal axis; a grid must
fill an evenly spaced rectangular lattice; a bolt circle needs at least three noncollinear,
equally spaced holes on one circle. Members have identical machining sizes, depths and drilling
direction and open on one plane. The ordinary planner's corroboration still applies to bolt
circles with fewer than five holes: a bore or boss must prove the centre. A new group replaces
intersecting automatic groups; holes may belong to only one authored group. `split` dissolves
a recognised hole or slot pattern and keeps its members. Normal sharing of identical hole
sizes continues; their locations are individual and the pattern's pitch statements disappear.
On a lone slot, `split` keeps it out of its congruent obround slots' `N× SLOT W × L` callout.
A full-round obround (its end radius half its width) split this way is dimensioned by its two
end centres, as two arcs located: its width, its low end's centre located from the part's box
(`location_slot.length`), and the centres' distance apart (`slot_centres.length`, the length less
the width), with no length or end radius, which the width and the located centres fix. A
centre mark is drawn at each end centre for those dimensions to end on. Review asks the same of
it. Any other split slot (a keyhole, a straight slot, an obround whose ends are
not full-round) is dimensioned singly (width, length, end radius, low-end location). Splitting
one of two congruent slots leaves the other alone in its group, so it too is dimensioned singly;
split both to dimension both by their centres. Without `split` nothing changes.

`reclassify` accepts `hole`, `boss`, `turned_step`, `slot` or `pocket`. Supported changes are
boss ↔ turned step and slot ↔ pocket, when defining faces prove the target grammar. Cylindrical
interpretations need complete, coaxial, equal-radius principal cylinders with contiguous axial
spans; opening evidence distinguishes a bore from an external band. Rectangular interpretations
need four inward-facing planar walls with matching rectangular spans. A pocket also needs
its capped planar floor and a proved open planar mouth; a through slot needs two proved
open ends. Exact principal profiles or the existing planar edge graph on a single solid
prove the opening, including local stock planes below a higher boss. An omitted cap cannot
turn a closed void or a blind pocket into an open slot. Curved/unproved mouths are refused. Rust's existing
`section_recess` feature is the pocket representation. Unsupported, partial, oblique or
ambiguous geometry is refused with its reason; the correction never invents dimensions.
Derived fingerprints retain their principal axes, so existing view-demand rules apply. A named
boss end selects its outward direction; without one unique end, a boss interpretation uses
the positive principal direction. Name the free end to select a negative-facing boss. Hole
and boss vectors can still be read when a bridge's working frame is oblique.
A suppressed feature cannot also carry fit, thread, datum, control, finish, knurl or note
requirements. Choose only one of `reclassify`, `suppress`, `split` and `pattern` on a key.

Declarations use the same five kinds and geometry checks. `id` is a unique authored
`f:declared:<name>`; the list carries no numerical parameters. For a bore, name the cylindrical
wall and its planar blind floor when present; without a floor the cylinder must reach both
stock planes to prove throughness. Equivalent automatic recognition yields to the declared ID, including when a later
recogniser learns the previously missed feature. Automatic hole/slot pattern links follow
that identity, so a physical occurrence is never counted twice. Extra planar end caps may
belong to the same proven scope. A different/incomplete scope, another authored declaration,
or authored requirements/references still naming the retired automatic owner is refused;
move those references to the declared ID, or suppress/reclassify the conflicting reading
explicitly. The declaration then
accepts ordinary `features/<declared-id>` requirements and `dimensions/d:<declared-id>/…` edits.

`recognition_corrections::face_fingerprint(analysis, face_index)` supplies each face name;
`item_options` with `id: "face:<index>"` reads one in
`settable.feature_declarations.fields`; sheet options explain this lookup. Indices are
Analysis discovery addresses only: the Spec stores the resulting fingerprint. Each response
stays bounded rather than returning every face on a large part.
Signatures hash the existing attachment's surface, principal axis, support, bounds and outward
planar normal, rounded to 1e-6 mm; they exclude face indices and sample order. They are
model-space identity fingerprints, never authored geometry. An Analysis lacking face evidence
must be regenerated. A fingerprint must resolve to exactly one face: missing or ambiguous
faces are refused, naming the saved path and fingerprint and advising reattachment to current
fingerprints or removal. Identical signatures are deliberately ambiguous.

When saving a correction, the bound Plan Spec fills `features/<id>/anchor` with
`{"faces":["face:sha256:…", …]}` for the target and every grouped member. Explicit anchors are
never silently replaced. This lets a saved correction resolve by its defining faces on
re-analysis rather than trusting a changed rank. Exact matches refresh feature keys,
pattern-member references, and the associated dimension and precision keys when recognition
ranks change. Many-to-one collisions are refused for the author to combine explicitly. Authored declaration and group IDs are the
chosen names, independent of recognition order. Re-analysis does not renumber them. A
geometric change outside the exact signature's tolerance needs an explicit reattachment;
this stream does not guess a nearest face or implement general revision correspondence. When a
source hash changes but all correction anchors still resolve exactly, a correction-only Spec
can bind to the new source. A Spec also containing unrelated, unanchored revision-specific
edits is refused until those edits are reattached; they cannot inherit the correction's proof.

Corrections resolve before sheet demand, dimensioning, callouts, schedules and completeness.
The same effective Analysis is passed to independent lint by `spec_gdt::effective` (the existing
merge entry point used by API review and sessions). Lint continues to read only Analysis and
rendered evidence. Suppression removes a spurious feature from that inventory; it does not
acknowledge unrelated findings, remove PMI or silence the independent profile checks.
Declarations and reclassifications acquire the normal measurement requirements. Omitting or
misprinting their output still raises lint findings. Library callers who invoke lint directly
must pass this effective Analysis, as for authored GD&T.

`Plan.recognition_corrections` records every applied decision as `{id, from_spec, basis}`, even
suppression, which has no resulting dimension. Affected dimensions also carry the correction
path in `from_spec`, set `default: false`, and explain the change in their basis. With no
corrections the resolver borrows the existing Analysis, emits no new Plan fields and performs
no face lookup, hashing, geometry scan or recognition run. Sheet selection, detail policy,
datum position and fits defaults continue through the existing planner.

This is authored intent within R4's **one analysed part** decision (ADR4-3), consistent with
ADR1-4's single feature waist. `analyse` continues to report recognition unchanged; it never
automatically generates Spec declarations. The author names a missed feature and its faces;
resolution reads their already analysed geometry. As in Python's `model/declare.py`, these
features feed the same IR and planning as recognition. Rust deliberately offers no second
recognition-free build mode and no explicit numerical geometry constructors.

`item_options` with a recognised feature or pattern ID (`f:…`) returns its correction
controls. Keeping these on feature queries preserves the dimension options' wire budget.

Session `apply`/preview, undo/redo, stateless operations and WASM's existing `apply`/`setSpec`
all use the common Spec editor and validation. Use `set`/`remove` at `features/<id>/<field>`;
use `add` at `feature_declarations`, or `set` the whole declaration list to replace/re-anchor it.
List-index editing remains reserved for the existing table sheet operation. Invalid edits
leave the committed session unchanged. MCP declaration shapes are published by
`tools/generate_mcp_schemas.py`; supported kinds, face resolution and geometric semantics are
validated by the common Rust model, as with other feature requirements.

Reproduce three before/after PNG, SVG, Plan, Spec and lint pairs with
`cargo run --release --example recognition_corrections` in
`target/recognition-corrections/renders/`. These use unchanged STEP geometry: split a native
grid, declare a real native pocket omission, and reinterpret a boss as a turned band.
See [the validation and drafting review](recognition-corrections-validation.md) for
results, retained warnings and the measured performance gates.

#### Projection method and a pinned sheet's fit

`sheet/projection` is ISO 5456-2's method, `first` or `third` (angle); absent means third, and the
plan is then byte for byte what it was before the field existed (`third` set explicitly differs only
in the spec it echoes). First angle puts the plan below the front and the right side view left of
it, the same blocks and bands mirrored within the packed group, and draws the first-angle symbol
(cone left, circles right, the cone's large end beside them) where the third-angle symbol
(circles left, cone right, its small end beside them) goes, both ISO 5456-2's figures;
`sheet.projection_symbol.method` says `first-angle` or `third-angle`, and the views' bases say
which side each sits. The session's `item_options` for `sheet` lists the choices. Other sheets of a
multi-sheet drawing carry no projected views, so the setting only arranges sheet 1.

A spec that pins both `sheet/size` and `sheet/scale` is judged as the automatic search judges its
candidates: when the packed estimate doubts the fit, the finished drawing (after the same measured
correction the search applies) is checked, and if every drawn item and view lies on the sheet and
off the title block, `page_fit_uncertain` is not raised (the basis says "the finished drawing
fits"). So pinning the pair the engine would choose itself raises nothing. When the views really
do not fit, the problem's `fix` sets the nearest pair that does, found by the engine's own search:
the pinned sheet at the scale the recovery picks for it, else the pinned scale on the smallest sheet
that holds it, else the automatic pair; each is offered only when, pinned, its drawing fits. The
message names it (`…; A4 at 1:1 fits.`).

#### Broken views

`views/<id>/break` requests one ISO 128 zig-zag interruption on a principal view.
`{}` chooses the longest proven uniform span along the longest in-plane world
axis. The default removal retains one cross-section width (at least 5 model mm)
inside each end of that span and leaves a 6 paper mm gap; `remove` and `gap`
change those defaults. For example, `{"axis":"x", "remove":[100,500], "gap":6, "aligned":true}`
removes the open interval from x=100 to x=500 model mm, leaving a 6 paper mm gap.
`axis`, `remove`, `gap` and `aligned` override each automatic choice. Coordinates
refer to model geometry, never the page; `gap` alone is a paper-mm size, constant at every scale. `aligned` defaults to true: the same
interval is carried into principal peers seeing that axis; false limits it to
the named view. Explicit peer requests take precedence. Omit `break` to draw
full length. Multiple intervals and breaks on cuts, details or isometrics are
not supported; unavailable or conflicting requests warn `break_not_applied` and
keep the view unbroken. The extrusion proof excludes all transverse edges and
non-extruded curved surfaces; older analyses without this evidence also warn.

Incompatible aligned details and half sections warn about displaced alignment; suppressed automatic half sections are reported. A leader, datum or symbol anchored inside the removed span warns `break_anchor_removed`; change `remove` or omit the break to show that attachment.

The removed region contributes no visible or hidden ink. The two retained ends
move equally towards one another, with thin zig-zag break lines across their
profiles, repeated at a 4 paper mm pitch and overshooting the outline by 2 paper mm. Dimensions retain true measured values and their continuous dimension
lines; witnesses, hole marks and lint's inverse transform use the shortened map.
The reserved view extents use the shortened length before paper/scale selection;
the unbroken isometric is independently fitted. Automatic selection never worsens the unbroken paper or scale; if no broken arrangement preserves both, it keeps the unbroken drawing with a specific warning. Explicit `sheet/size` and `sheet/scale` override that fallback. Applied fields and rejected or
displaced intent are recorded by the central override reporter. Explicit sheet
size/scale remain authoritative; automatic paper uses the existing smallest-first
policy and its clean, legible scale trials.

#### Half sections

A part turned whole about one axis whose inside a section shows more cleanly than hidden lines
has its profile view drawn as an ISO 128-50 half section when the plan has no full section. That
is when a coaxial hole has a counterbore, spotface or countersink, the coaxial bore changes
diameter along the axis, a groove is turned inside the material, or a through bore runs under an
outer groove (a bush or linear bearing, whose thin wall at the groove only a section shows). A
plain bore under a plain or merely stepped outline stays a plain view, its hidden lines already
clean; a part recognition does not give as turned whole gets no automatic half section. The view
is drawn half as seen, half cut on the turning axis and hatched, with no hidden
edges in either half and no caption or cutting-plane line. The cut half is the side the view's
lengths are dimensioned on. Witnesses on internal edges of the half drawn as seen move to their
mirror in the cut half, and an internal diameter becomes a half dimension: one arrow on the
cut edge, its line running past the axis. `views/<name>/half_section` steers it: `true` forces it
on a profile view of a turned part (a `section_not_shown` warning says when the view shows none),
`false` forbids it.

#### Line work per view (#76 item 6)

Each view accepts optional boolean `hidden`, `centre_marks`, `centre_lines`,
`thread_depiction` and `knurl_depiction`. The same fields under `sheet` set defaults
for **every view on every sheet**, including auxiliary views allocated to later sheets:

```json
{
  "sheet": {"hidden": false, "centre_marks": true, "thread_depiction": false},
  "views": {
    "side": {"hidden": true, "centre_marks": false},
    "front": {"centre_lines": false, "knurl_depiction": false}
  }
}
```

Precedence is **view, then sheet, then the view's automatic convention**. There are
currently no `sheets/<n>` settings: `sheets` controls allocation, not numbered sheet
styles. Removing a setting restores inheritance; absent fields preserve the original
output, including the isometric's own defaults. An explicit centre switch can request
eligible centre furniture in the isometric; it does not fabricate an axis or feature.
`centre_marks` controls end-on crosses, while `centre_lines` controls profile axes and
pitch circles. They are independent. Thread and knurl switches affect conventional
ink only: manufacturing requirements, dimensions, callouts and coverage remain.
The isometric omits hidden edges by default, as pictorial views conventionally do;
`views/iso/hidden: true` (or `sheet/hidden: true`) draws them, with visible precedence.
Cosmetic depiction still requires a proven profile feature. Sections always omit hidden
edges and cosmetic threads/knurls, even with `true`; centre switches do not remove the
cutting-plane chain line, section arrows or hatching.

Settings name stable existing view keys (`front`, `plan`, `side`, `iso`, `section_aa`),
never page coordinates or projected edges. Unknown view references are refused with
available names and a replacement/removal instruction; misspelled fields and non-boolean
values are refused. As with other optional Spec fields, JSON `null` means absent.
A setting for an omitted view is saved for when that view is shown again. No new
feature-level switch or fingerprint address is introduced: furniture can be merged across
features, so per-feature suppression needs separate ownership work.

Planned views record effective authored switches in `line_style`, with the winning
Spec paths in `from_spec`, `default: false`, and an explanation in `basis`. The embedded
Spec retains all inherited and overridden entries. `set`/`remove` edits use the usual
paths, for example `views/side/hidden` or `sheet/centre_marks`; session options expose
all five booleans for views and sheets. Preview, undo/redo, geometry caching and delta
replay follow the ordinary Spec edit path.

These are drawing choices, not lint exemptions. Hidden curves switched off are absent
from rendered geometry, so `hidden_on_visible` and `section_hidden` measure only drawn
curves. Hidden-geometry completeness is not inferred from undrawn curves. Independent
lint still reports missing centre marks when the author disables them, as well as wrong
dimensions, missing manufacturing requirements and other drawing defects. Automatic
layout and its conservative projected-ink clearance remain in force; hiding edges does
not relax safety margins or change sheet-size, scale, detail, fit or datum policy.

#### Sheet allocation (R5, Stage I)

`sheet/preferred_max_size` (`"A4"` through `"A0"`, default `"A3"`) and
`sheet/preferred_max_sheets` (integer at least 1, default 2) are soft preferences.
The existing `sheets.max` is a hard limit (up to 9); preferences never grant
permission beyond it. Explicit paper, scale and allocation intent take precedence.
`sheet/keep_layout` retains the author's explicit paper, scale and count, including
when either preference changes.

Search one sheet at A4, then each size through the preferred maximum. Next try
2 through the preferred count at that size. If none is usable, increase paper one
size at a time, trying one through the preferred count at each size. Finally try
more A0 sheets, up to `sheets.max`. The first usable result wins. Principal and
auxiliary views can move as complete annotated blocks; dimension, PMI and balloon
ownership follows the view. Authored view relations and keep-together groups remain
constraints. Footprint bounds skip impossible candidates. At each request the
finished baseline's whole annotated views are repacked first (no new plan), then a
forced trial at the largest scale the footprint bound allows; one smaller readable
scale follows only after a lack of room (clipping, a hard layout finding or a space
drop), never after a routing refusal. When a smaller readable scale can fit
individual views, their complete annotated blocks are planned on roomy paper before
transport; a small-sheet plan that already dropped items is not used to manufacture
a fit. The search stops at the first usable candidate.

Dimension lines sit 10 mm from the view outline and 7 mm apart (3 mm text). On a
one-sheet request, when a forced trial at a scale runs out of room for its lanes, the
same paper and scale are tried with compact spacing before the smaller scale or the next
request. Running out of room for lanes means a space drop, a page-fit verdict, dimension ink off the sheet
or over the title block, or clipping that compact lanes could cover: no view outline larger
than the frame, and the annotated views' extent beyond the frame's span no more than
compact lanes save across every view (3 mm per side, plus 1.5 mm per further stacked
line). Overlaps between items and unrelated furniture do not earn a retry. Compact lanes
are the first line 7 mm from the
outline, then 5.5 mm between lines, which leaves 1.5 mm between a label's top and the
next line and clears the 2 mm extension overshoot. Compact spacing therefore ranks
before the next paper size and the preferred-count escalation. The footprint bound is
unchanged: a paper it rules out for normal lanes is not tried with compact ones. A split onto more sheets keeps normal lanes, since pinned
replay across whole-view sheets is not yet reliable. The usable test below gates compact
candidates like any other. The selected drawing records
`sheet.dimension_spacing: "compact"` (absent means normal), and the sheet choice says
"with compact dimension spacing". `sheet/dimension_spacing` sets the intent: `normal`
forbids the fallback, `compact` uses compact lanes for every candidate, including
explicit paper and scale. A local fix offered on a drawing that fell back to compact
spacing retains it with the paper, scale and count.

A candidate is usable relative to the automatic baseline (the planner's own
unrestricted drawing). It is never clipped: every view outline keeps its whole stroke
inside its own sheet's frame, and thinner ink (witness, centre and crop lines) may end
on the frame line but not beyond it. It is never below the readable floor (the existing
0.8 mm minimum feature opening, 20 mm overall span, printed-step limits, the 1:10
absolute floor and any authored `sheets.smallest_scale`). Otherwise it is disqualified
only by unreadable, error or loss findings that the baseline does not have (compared by
code and subjects):

- an error-severity layout finding;
- a warning for overlapping text, leader-through-text, ink off the sheet, outside its
  view's area or over the title block, views that do not fit, or unplaced notes and
  tables;
- a measured leader running on an extension line, or a view frame on the title block
  or on another view;
- a required item the baseline carries that it does not, a new drop or undrawn GD&T
  item, an unreported undrawable item, or printed steps illegible beyond the baseline's.

A sheet that would carry only pictorial views (the isometric) is not a usable split,
unless the author assigned it there. A split also costs a page turn, so a multi-sheet
candidate is refused by new leader runs beside model ink or new hidden-edge crossings.
Other warnings (extension and feature crossings, route lengths, crowded but legible
location ordinates, and on one sheet leader runs and hidden-edge crossings) are
reported but never choose larger paper, so a crowded but readable small sheet does not lose to an
enlargement with fewer warnings.
Unsupported requirements and any undrawn items retain their existing findings.
Clipping is a hard rejection, including for fallback ranking. If no usable result
exists through A0, retain the least-bad unclipped candidate using the existing
hard-findings, required-drops, scale and sheet-count ranking, with an explanation.

One `sheet_preference_exceeded` info/advisory notice identifies the size, sheet
count or both exceeded, the actual paper/scale/count, and why the preferred layout
was refused. `Plan.sheet_candidates` and `sheet.basis` record the preferences and
individual decisions. A request whose reserved footprints (each view's outline at the
smallest readable scale plus its baseline annotation bands, the aligned principal views,
and the tables) cannot fit its paper is not planned: its candidate carries
`kind: "ruled_out"`, no `scales`, and an outcome starting `ruled out: ` that states the
first failed condition in paper mm against the paper's drawable area, for example
`ruled out: view v:front with its annotation bands needs at least 305 × 217 mm at 1:2;
A4 drawable area is 277 × 190 mm; 1:2 is the smallest readable scale`. Planned
candidates carry no `kind`. Explicit `sheet/size` or `sheet/scale` bypasses this search.
Automatic feasibility is checked before pin replay. A subsequent explicit drag
keeps its ink and reports any off-sheet placement, as on a single sheet. A bare pin
holds the finished drawing, after its route repair. Pins in a multi-sheet choice
replay into its view frames, and a callout offset is measured from that drawing's
own automatic leader tip. A fresh automatic choice carries no `layout_replanned`
notice; the planner's recovery from its first estimate is stated in `sheet.basis`.

`preferred_printer` is refused with guidance to `sheet/preferred_max_size`.
The unreleased `max_size` spelling is not accepted.


`sheets.max` allows 1–9 sheets. `sheets.smallest_scale` is an ISO 5455 floor,
`keep_together` groups view names (also `hole_table`), `hole_table` assigns a rescue
table if one is made, and `gdt_on_sheet_1` records that requirement.
`views/<name>/sheet` assigns auxiliaries; principals accept only 1.
`tables/<index>/sheet` assigns a corner table. All sheets share paper, title and number.

An assignment must be within `1..sheets.max`, must agree with its keep-together
group, and must put a view with `place.relative_to` on its anchor's sheet. Those
relations also form implicit keep-together groups. Placement pins are kept;
an explicit sheet assignment remains possible for an auxiliary group. A stated
`sheet.scale` below `sheets.smallest_scale` is refused with choices to remove the
scale or lower the floor. List-index edits are supported only for
`tables/<index>/sheet`, with `set` and `remove`.

With `sheets.max > 1`, each allocation prefix uses the existing finished-sheet
recovery, including table recovery. Larger paper is tried before moving auxiliary content.
The cumulative overflow order is isometric, section A–A, then a rescue hole table
withdrawn for lack of table room. Assigned content moves first; a group containing a
principal or a placement pin does not move automatically. Sections that fit stay
on sheet 1. The first clean prefix wins; otherwise the fewest hard findings and
required drops, largest first-sheet scale, then fewest sheets and moves choose the result.
The sheet basis records every attempted or blocked prefix.

Assigned corner tables are placed first on later sheets, then complete view blocks
in wrapping rows, clear of the title block and each other. A view tries a fresh sheet
at its parent's scale before taking a smaller standard scale. A long rescue table
gets its own sheet; an explicit keep-together group shares that sheet and keeps clear
of the table. Every balloon must still land on the plan view on sheet 1. A table
that cannot fit even on its own sheet is withdrawn with its original dimension
states and geometry restored. Nothing is silently removed. Empty assigned sheets
are closed by renumbering, with `sheet_renumbered` and an edit to the actual sheet.

`plan.sheet` remains sheet 1 (`sheet`). Optional `plan.sheets` contains sheets 2..m
(`sheet:2` etc.); each carries `number` and `zones`.
Views, corner tables and hole tables carry `sheet`, omitted when 1. A moved view's
`scale_note` records a scale different from its sheet's. A section's `xref` and its
cut's `xref` refer both ways, with sheet, grid zone and resolved text position.
Zones use 6×4 fields on A4, 8×6 on A3, 12×8 on A2, 16×12 on A1 and 24×16 on A0;
numbers run left to right and letters top to bottom, skipping I and O.
Every sheet, single-sheet drawings included, draws the zones in its margin with ISO 5457
centring marks: 0.7 mm lines at each side's midpoint from the trimmed edge to 5 mm inside
the frame, stopping at the frame where they would meet the title block, a table or other
drawn ink. `sheet/zones: false` omits both (`plan.sheet.zones` absent); zones cited by a
section's `xref` stay and are reported once as `sheet_zones_kept` info, with a fix that
removes the setting. The furniture never changes the layout.

Item IDs survive moves. `items::Item.sheet` follows the carrying view or table;
notes and drawing-wide requirements stay on sheet 1. Every Stage I dimension,
frame and datum carrier is asserted to be on sheet 1. The generic datum check
requires each referenced symbol on sheet 1 or the frame's sheet. Collision audits
compare items only within their own sheet; later-sheet block audits check the
border, title block and other blocks. A missing section cross-reference is an error.
`sheet_below_smallest_scale` reports a floor that could not be reached, offering
more sheets only when a blocked allocation could help, otherwise larger fixed
paper or a lower floor. An unused rescue-table assignment reports
`sheet_assignment_unused` with a remove edit.

Stage I1 produces and checks these later-sheet plans. I2a adds
`model::render_sheets`, one Drawing per sheet, while `model::render` continues to
draw sheet 1, including the parent's cutting-plane furniture for a remote section.
Every sheet repeats the unchanged title-block grid with a planned `n/m` SHEET
value; an authored sheet label is replaced with `sheet_label_replaced` info.
The projection symbol and general notes stay on sheet 1. SVG and PNG keep the
sheet-1 filename and add `<stem>.sheet<n>.svg` / `.png` for later sheets; one PDF
contains every sheet in order (`Drawing::pdf_pages`, single-sheet bytes unchanged).
Every output embeds the whole Spec. Review keeps `svg` for sheet 1 and adds
`sheets: [svg…]` for sheets 2..m, omitted for one sheet. Coverage reads the marks of
every sheet once; structural checks run per sheet and
later-sheet issues carry their sheet number. Border zones and reciprocal section
addresses are drawn as searchable text, and structural lint checks that each
address reaches its rendered target in the printed grid. MCP sheet
operations are I3's scope. The plan and spec schema versions stay the same;
old documents read with single-sheet defaults.

#### Dimensioning schemes (#76 item 4)

`dimension_schemes` is a map keyed by a stable author name (letters, digits, `_`, `-`).
Each entry owns a station network on a **part-frame** axis. For example:

```json
{
  "dimension_schemes": {
    "inspection": {"scheme": "baseline", "axis": "z", "view": "front", "from": "max"},
    "mounts": {"scheme": "chain", "axis": "x", "targets": ["f:hole_pattern:1"], "view": "plan"},
    "drilling": {"scheme": "ordinate", "axis": "y", "targets": ["f:hole:1", "f:hole:3"],
                 "view": "plan", "from": "face:v1:<fingerprint>"}
  }
}
```

These are independent examples. Entries can meet at a shared datum; their combined network
must remain acyclic, and a target cannot have two owners.
The same named target cannot be dimensioned twice across different views either.
`scheme` and `axis` are required. `targets` names a feature pattern, feature IDs or exact
fingerprint references from `Analysis.edge_ink.references`; omitted/empty targets applies to
all automatic locations and turned-profile shoulders on the named `view` axis. A view is
required for that whole-axis form. Otherwise the first compatible automatic location's view
is preferred, falling back to front (plan for Y). `view` is a bare principal view name and
must show the measuring axis across the page. It constrains the generated owners' carrier.
An incompatible carrier for an already located target is refused with its compatible views.

A hole or slot pattern expands to its physical members. A turned step expands to both its
shoulders. Other feature IDs select their position; a feature proving one transverse planar
face can also name that datum plane. `start:f:turned_step:1` and `end:f:turned_step:1`
select a band's low and high shoulder. `from` accepts the same station references, or
`min`/`max` for the two ends of the part along the chosen axis; absent means `min`.
A face must be planar and perpendicular to the measuring axis; a cylinder axis or straight
edge must have one constant coordinate on that axis. A vertex is a station too.
No face/edge traversal indices, page coordinates or nominal distances are accepted.
A datum authored through `features/<id>/datum` or `faces` remains the existing GD&T intent;
select its feature ID or face fingerprint here instead of inventing another datum symbol.

Chain orders distinct stations along the axis and dimensions adjacent pairs. Baseline
measures every station from the chosen datum. Ordinate uses signed coordinates from that
datum, with an origin circle and one endpoint arrow per running coordinate. Coincident
stations share one owner; a controlling overall that would close the combined network is
covered. Remaining automatic controls are retained only when they add an independent interval.
A counted pitch or grouped length is atomic: a partial replacement that would drop other
intervals is refused; select the whole pattern, every member station, or the whole view axis.
Circular patterns retain their PCD convention: to request Cartesian dimensions explicitly,
name their individual members. Multi-solid analyses are refused for schemes; select a single
part first, so one station cannot accidentally borrow another body's ownership.

New owners have deterministic `d:scheme:<name>/<reference-hash>` IDs. Retired automatic IDs
stay in `omitted` as `covered`, naming the network owner. Generated items record
`dimension_schemes/<name>` in `from_spec`, `default: false`, measured values and endpoint
references. They use the ordinary dimension placement, tolerancing, precision and layout
pipeline. Inspect the generated IDs, then set `dimensions/<id>/{tolerance,place,...}` as
usual. A conflicting explicit omission/tolerance/placement on a retired automatic ID, or
an overlapping #78 authored dimension or semantic PMI owner, is refused with its ID and a
remedy. In `dimension_source: authored`, generated IDs must also be listed to draw them;
the scheme does not bypass the authored set. Missing or omitted links lose lint coverage.

Set/remove uses ordinary `items::Op` and session transactions, for example
`set dimension_schemes/inspection` with a complete entry, or
`set dimension_schemes/inspection/from` to change its datum. Remove the entry to restore
automatic planning. Preview, undo/redo and saved Specs retain the same references. Browser fragment edits
refresh placement options when a scheme changes its dimension inventory. A Spec
bound to another revision cannot reuse feature IDs here, including edits on generated
feature-based whole-view owners: replace them with matching current fingerprints, remove
the stale generated edits, or explicitly rebind. Unknown, ambiguous or incompatible references and
misspelled fields are refused with an explanation and correction/removal instruction.

Without entries the existing automatic choice, including the turned inspection-end baseline,
is unchanged. Scheme resolution returns immediately for an empty map. Lint independently
re-measures the printed links from Analysis references and checks their connectivity before
crediting inferred locations or band lengths. Wrong text, removed links, closed chains,
placement collisions and manufacturing omissions remain findings. Sheet size, scale,
second-sheet recovery, details, datum placement and class-only fit defaults remain the
ordinary policies. An automatic hole schedule cannot replace a view carrying an explicit scheme;
the full-part coordinate-table rescue is disabled while a scheme is authored. MCP retains its sparse per-ID object map; Rust validates each complete
scheme entry and session options describe its forms.

#### Placement (`place`, S3)

Dimensions (linear dimensions and callouts, including holes, fillets and chamfers) and PMI
frames, datum symbols and labels share `{view?, side?, lane?, along?, pinned?}`; callouts and linear dimensions
also take `offset` (below):

| Field | Meaning |
| --- | --- |
| `view` | `front`, `plan` or `side`; a dimension must be visible across that view. `place.view` takes precedence over the earlier `dimensions/<id>/view`. |
| `side` | `above`, `below`, `left` or `right` of the carrying view's projected envelope. A linear dimension uses the two sides parallel to its measured span; incompatible sides are refused with the choices. |
| `lane` | Zero-based order out from the view, 0–1000. Linear lanes use the existing paper spacing; callout columns on the left/right use the widest canonical label on that view plus shoulder and clearance, so labels at the same along position have distinct columns; PMI tiers use their glyph's size and clearance. Without strip/label fields a pin keeps the automatic ink. For an explicit strip placement, an omitted lane uses the automatic linear lane or the nearest PMI tier. |
| `along` | A fraction 0–1, or `start`, `middle`, `end`, `outside` (1.25). A linear label is centred at that fraction of its witness span; at `outside` it sits just past the line's far end, the dimension line run on to it, as a span too short for its label has it (#32: 1.25 of a long span stranded the text). Leadered labels use that fraction of the view's extent along the strip, keeping their original feature attachment. |
| `offset` | `[dx, dy]`, each within ±1000 paper mm (y up): a callout bend from its automatic leader tip, or a linear label from its canonical automatic label position. Requires `pinned: true` and excludes `side`, `lane` and `along`. |
| `pinned` | Defaults to false. True fixes the resolved geometry before automatic layout; without side, lane, along or offset it keeps the canonical automatic ink (its lines and arrows; a label whose own text changes, such as a new tolerance, is re-set from its current text, centred where the old one was, or for a callout from the end of its shoulder); a free item can be adjusted around obstacles. |

An unpinned callout with only `side` (and optionally `view`) keeps participating in the
shared leader solve. Its candidates stay on that side, its printed label reserves paper space
before view packing, and the solver chooses a station clear of other ink. Supplying `lane`,
`along` or a pin continues to prescribe a station.

A pinned callout above or below a view puts its text on the side its leader runs to:
an elbow left of the tip carries text to the left, and a vertical leader defaults to the
right. Left/right columns keep their stated side. A pinned callout's bend and label are
never slid to avoid a feature crossing. The existing `leader_through_feature` warning
names the callout and crossed feature in `subjects`, also names the callout in `refs`,
and explains that the pinned label's leader crosses part features and keeps its position.

A pinned `offset` (#12) can put a callout's label anywhere, including the open space inside its
view, where the automatic solver often puts it and side/lane/along cannot. The bend goes at the
automatic leader tip plus the offset. That tip is the canonical plan's (the part's default
plan, as a bare pin keeps), moved with its view when packing moves the view, so unrelated edits
cannot move the pin. The leader re-attaches radially to the site the automatic tip lies on, so
a bend dragged round its hole keeps a radial shaft, and the text reads on the side the leader
runs to (a vertical leader keeps the automatic side). An offset that puts the bend where the
canonical plan has it is planned as a bare pin, so it is the automatic ink exactly whatever that
leader's direction: pinning a callout where it is moves nothing. A callout drawn without a
leader (no automatic placement, e.g. a pattern given by a hole table) has no tip to pin from:
`callout_offset` refuses it. With `place.view` naming a view other than the canonical one, the
offset is taken from the callout's first site centre in that view and the leader goes to the
nearest site; `callout_offset` reports only the canonical view and refuses the callout there. The bend and label are never slid; a crossing is the
`leader_through_feature` warning above, and a label over the view is `annotation_over_view`.

For a linear dimension, `offset` is the displacement of its label origin from the canonical
plan's automatic label origin, in paper mm (y up). Both positions travel with the carrying
view's origin. `[0, 0]` pins the automatic ink as a bare pin. The perpendicular component moves
the dimension line; each witness extends or shortens towards it from its own feature, so the
line can lie inside the view, between feature edges. The parallel component moves the text
along the line. Text outside the witnesses gets outside arrows and a dimension-line run-on,
using the existing text settlement clearances. Measured stations and values never change.
This also applies to automatic ladder rungs and authored baseline, chain and ordinate members,
and to aligned linear spans. The canonical placement must be drawn in the requested view;
an offset into another carrier view is refused. Pins are obstacles for the free solvers and
are never slid to avoid collisions; existing problems such as `annotation_over_view` report
conflicts. `offset` cannot be combined with `side`, `lane` or `along`, and requires `pinned: true`.

Existing angular PMI keeps its measured sector: `lane` increases the radius in paper steps and
`along` selects a fraction of its arc. A requested `side` is reported as
`placement_side_not_applicable`, with a fix removing that field; it cannot change the sector.

Views use `{relative_to, side, gap?, align?, pinned?}`. `relative_to` names another view
(`front`, `plan`, `side`, `iso`, `section_aa`, `detail_a`–`detail_h`); the page is never an anchor, so a view's place
is always relative to another view (ADR R1 invariant 1), typically its projection partner.
`gap` is the clear paper distance between the views’ annotation bands, not their part
outlines (default 10 mm, 0–1000). The reserved view area includes annotation bands,
so the visible part outlines will usually be farther apart than `gap`;
`align` is `start`, `middle` (default), `end` or `outside`
(`outside` also aligns ends). These existing choices retain their saved-Spec behavior.
Use `align: "projection"` to change the gap while keeping the projected geometry's origins
aligned across `side`: x for above/below, y for left/right, regardless of lopsided bands.
It applies only to `plan` against `front` or `side` against `front`, in either direction,
in first- and third-angle projection. Keep the conventional side for the selected projection.
For example, `views/plan/place = {"relative_to":"front","side":"above","gap":15,
"align":"projection","pinned":true}` keeps the plan on the front's x in third angle.
Other pairs refuse `projection` and name `start`, `middle`, `end`, `outside` as valid choices.
Projection relations preserve the principal group during packing so an automatic partner
cannot slide off its projection line. Dependencies resolve before packing; cycles and self-anchors
are refused. Missing anchors are reported, with the entry retained. Unpinned views fit around fixed footprints; an automatic
isometric may be reduced to fit the space left. A pinned isometric first resolves the requested
relative region, clips it to the drawing area and fits its ink in the largest free rectangle
inside that region. Its resulting position and scale are then fixed. Principal view pins keep
the literal footprint relation and report any overrun.

A pin also fixes its carrying view and the view-anchor dependency chain for automatic packing.
Those frames and the automatic sheet/scale are resolved from the part's default plan and the
explicit sheet/scale choices, independent of unrelated text, omission and placement edits.
Recovery cannot change them to repair another item's collision. Editing the pin's own anchor
intent, sheet or scale deliberately re-resolves it. All original `place` entries remain in the
embedded Spec, including entries that are covered, omitted, or cannot fit.

Automatic linear labels settle against the completed dimension and GD&T ink. Text stays
between its own witness stations when it fits, otherwise just beside an arrow. If no such
label position clears ink, the whole dimension tries the next outward paper lane within the
existing view footprint. It may skip one occupied dimension rung, so the absolute movement
limit is two lanes (14 paper mm); a fixed-ink obstruction alone never permits that skip.
Settlement traverses the inventory once and tests span endpoints, centres and obstacle
boundaries rather than sampling the whole span. Cleared text/line contacts leave at least
1 paper mm, and a moved label (or its moved lines) may not come within 1 paper mm of another
item's stroke or label that was clear by 1 mm before the move. Inside the span comes first;
with outside arrows, text just past an arrow tail (ARROW + 1 mm) is the fallback. Its witness origins and measured value stay fixed. Candidates cannot add
label/line contact, share an overlapping measured span on another dimension line,
cross a settled GD&T leader, or introduce a band overrun. Pins stay fixed;
an infeasible label keeps its reported collision. `tools/quality.py` counts detached text
independently as `text_off_span`, with item IDs; this metric adds no MCP problem rows.

Pinned annotations are obstacles to the free linear, leader and PMI solvers. Explicit pin ink
reserves additional carrier bands before free views are packed; the carrier origin and scale
stay fixed. Free linear labels start from their canonical settled geometry, then try slides,
other lanes and the opposite side when a pin blocks them. Pinned callout shafts attach radially
to the nearest proven feature site, as automatic leaders do. A collision or
sheet overrun remains a problem against the pinned ID; its geometry is kept, including pinned
hole items that ordinary drop-and-escalate would withdraw. A covered measurement stays covered:
`omit: false` and a pin do not bypass consolidation or repeat information. A pin that cannot be
drawn has `pinned_item_not_drawn`, alongside the reason in its typed state. Pinned view collisions
and overruns have `pinned_view_overlap` and `pinned_view_off_sheet`. Any explicit view
placement outside the drawing area reports `pinned_view_off_sheet` as a plan error, including
relations without the optional pin flag. Its fix removes the view placement; adjusting the
relation is also possible. The view keeps its requested position.

An explicit (unpinned) side or lane for a linear dimension, such as a proposed fix sets, re-runs
that side's stack spacing (#32, `dims::placement::respace`): when its line would share a
dimension line with another linear dimension over an overlapping span (an automatic item at
its lane, or another explicit one), it moves out lane by lane to the nearest lane of its own
that fits the page (else the nearest free lane, its overrun reported), shorter spans inside.
An item that collides with nothing keeps its requested lane. Two lines sharing one offset over
overlapping spans are `dimension_line_shared` (error, both IDs), and a linear label more than a
text height (3 mm) from its own dimension line is `dimension_text_stray` (error); structural
lint reports the same two codes from the drawn ink. A fix that leaves either is therefore a new
problem in the edit's delta, not a resolved one.

Structural lint (`review`'s `structural`) also checks that each label says what its ink draws
(stream K7, `lint::fidelity`): a linear dimension's value against the span between its extension
lines divided by its view's scale (`label_vs_measured`: warning past 0.5%, error from 5%, a label
correctly rounded at its printed places excepted; an `INCH` label in millimetres; `N× v` per
member, as the port draws counted dimensions), with the correctly rounded ones noted once per
sheet (`nominal_rounded`, info); an angle's degrees against its drawn rays, its arc and extension
lines, and its rays along visible edges of the view (`angular_label_vs_geometry`,
`angular_geometry_mismatch`, `angular_support_mismatch`, errors); and a PMI datum symbol more
than max(24 mm, 2.5 × its frame) from its surface (`datum_leader_remote`, warning). These are
findings, not plan problems: no wire format changes.

Lint (`review`'s `lint`) also checks each callout's leader against the part (stream K4,
`lint::leaders`), from its drawn leader, its view's recorded transform and the Analysis: a hole
callout's tip must lie within 0.002 mm of a boundary circle of a hole it names (a double-D bore's
trimmed profile, flats included) as projected in its view (`diameter_leader_target_mismatch`,
warning; `diameter_leader_target_unverifiable` when no named hole can be read), and a
radius callout's tip on its trimmed circular profile arc seen end-on
(`radius_leader_target_mismatch`, warning). The drawn shaft must meet its middle 25–75%,
within 15° of radial, from either side (`radius_leader_not_radial`, warning),
including fillet callouts. Clear space, crossings, length and contacts choose the side,
independently of convexity, concavity or radius size. Interior callouts may stand over blank
face with a leader reaching the arc from the material side. Unreadable circular sections use the existing
`radius_leader_target_unverifiable` as info, not a wrong-shaft warning.
A turned step's diameter callout must tip on the band's own drawn cylinder, its outline trimmed
by any flats that cut it, or its arcs between the flats seen along its axis
(`step_diameter_leader_target_mismatch`, warning); a chamfer callout on its bevel: a turned
bevel's meridian on the outline about its coaxial band, a prismatic bevel's section seen along
its edge, an oriented bevel's face (`chamfer_leader_target_mismatch`, warning). Each has an
`…_unverifiable` info when the view cannot show the target or its record cannot be read.
Exact `Analysis.radius_arcs` records the cylinder, trim, proven middle-face points and
material side; older straight blends use their rolling axis and proven samples. Toroidal
minor-radius sections are checked only at proven quarter/half/three-quarter major-path
meridians seen end-on; oblique and unsupported meridians remain explicitly unverifiable.
Routing retains the best existing callout when no compliant candidate preserves the
existing contact/length budget. Drawn lint reports any remaining violation once per callout;
there is no separate fallback problem, and no callout's shaft may cut
back into the part's material after leaving it at its tip (`leader_crosses_silhouette`, info,
with the depth cut). This test deliberately exempts the initial traversal: it cannot detect
a boss crossing within a single solid box, as on CTC-01 (#145). The radius direction check
covers the local material-side approach independently. The material is each solid's projected box (`material::solids`) and the
measure `material::reentry`, the same predicate the leader router prices: a candidate that
re-enters is a hard blocker, its depth added to its length, and a chosen one is reported as
`annotation_line_over_view` with the depth in its message; a pinned callout's leader, which the
router does not route, is reported the same way ("The pinned leader of … runs back across its own
view, …, cutting … mm of it. Its bend and label are kept at the prescribed position.", with the
unpinning fix). A callout's `annotation_ink_overlap`
reads its leader's rendered ink, its arrowhead's flare included; a contact only the flare makes
is worded "The arrowhead of … reaches into the label of …".
These add findings and problem wording only: no wire format changes.

Placement problems provide `edit` operations addressing `place`. Resetting an annotation's
`place` to `{}` releases it to automatic placement; removing a view's `place` restores automatic
packing. Suggestions for free annotations choose another strip or label position and must be
reviewed by re-planning. Exports embed these entries, so extracting the Spec and re-rendering
reproduces the same geometry (`tests/placement_intent.rs`, SVG, DXF, PDF and PNG).

### Plan: what the sheet will say

`analysis_sha256` identifies the complete effective Analysis used for planning, including its
recognition records and recogniser, beside the STEP `binding` and embedded `spec`. It is a
SHA-256 digest of the deterministic JSON serialization, not merely the source-file hash;
changing `--features` therefore changes the run's provenance even for the same STEP and Spec.
Older saved plans read with no digest.

`feature_dispositions` reports each recognised occurrence without a dimension owner. Its
`subject` is a feature ID, or `recognition:evidence:<index>` / `recognition:refusal:<index>` in
the Analysis's same-run record arrays. Each informational entry has a closed `disposition`
(`unsupported`, `deferred`, `evidence_only`) and `reason_code`. The dimension planners and their
undrafted capability table supply the policy: a countersink is hole-callout evidence, while
unreviewed freeform, sheet-metal and similar grammars are deferred. Evidence-only step levels,
risers and repeating profiles keep their own reasons. These records do not remove completeness
or unsupported-feature warnings, and an authored omission is not an ownerless disposition.


`plan(analysis, spec)` produces it. The plan is the preview, and renderers draw only from it.

```json
{
  "schema": "draftwright.plan/1",
  "sheet": { "size": "A3", "scale": "2:1", "page": [420, 297] },
  "views": [ { "id": "v:front", "direction": [0, -1, 0], "at": [120, 180], "default": true,
               "basis": "front shows the most features" } ],
  "dimensions": [
    { "id": "d:f:hole:3/diameter", "view": "v:plan", "value": 8.1, "label": "ø8.1 H7",
      "from_spec": ["fit"], "default": false, "basis": "a hole's size is called out where it is seen end-on",
      "state": { "kind": "shown" }, "rebind": "carried" }
  ],
  "omitted": [
    { "id": "d:f:pocket:1/depth", "label": "↧ 5", "basis": "authored: the spec omits it",
      "state": { "kind": "omitted_by_user" } }
  ],
  "problems": [
    { "id": "p:tolerance_withheld:d:envelope/width.length", "code": "tolerance_withheld",
      "severity": "warning", "message": "±0 is not printed on d:envelope/width.length: …",
      "subjects": ["d:envelope/width.length"],
      "fix": [ { "op": "remove", "path": "dimensions/d:envelope~1width.length/tolerance" } ] }
  ]
}
```

- **Every item has a stable ID**, its resolved value, whether it is a default, and a `basis`
  sentence saying why it is there.
- **Every item has a typed `state`** (`src/items.rs`; the editor thin slice's S2), set where the
  plan decides it, so a caller never reads it from the `basis`: `{"kind": "shown"}`;
  `omitted_by_user` (the spec's `omit`) or `not_authored` (not listed in an authored set), in
  `omitted`; `covered` with `by`, the item that states it instead (a coincident location, a
  shared size, a boss height the overall extent states: `omitted`; a hole callout or location
  the hole table states: `dimensions`, unplaced); or `not_drawable` with `reason_code`, the code
  of the problem on its ID that says why it is kept undrawn (`view_plan_incomplete`,
  `callout_dropped`, `location_ref_dropped`, `off_axis_location_dropped`, `pmi_not_drawn`,
  `notes_not_placed`, `table_not_placed`). The sheet (its ID is `sheet`), the views, the hole
  table and the balloons are in the plan only when drawn, so always `shown`; a drawing-wide
  requirement carried by a general note is drawn as that note is. `tests/items.rs` checks every
  item's state against what the plan did with it, on the slice parts, every AP242 fixture and
  the quality parts.
- **One label formatter**: `items::items(plan)` lists every item once, with its kind, view, state
  and the text draftwright draws for it (`items::label`): a dimension's label, an omitted one's
  as it would print (`omitted[].label`), a frame's compartments joined by ` | ` (`gdt[].label`),
  a datum's letter, a requirement's text, a note's rows, a balloon's tag.
- **Every problem names its subjects** and, where one exists, an edit that would fix it.
  `subjects` are live item IDs only: the plan's items (the sheet as `sheet`), the Analysis's
  features and, for geometry no feature claims (`unclaimed_geometry`), the part's faces as
  `face:<index>`. `sheet` is the subject only of a problem about the paper, the scale, what fits
  on the sheet or the title block drawn on it (#25). What else a problem names is in `refs`: a PMI record's source (its Part 21
  provenance), a source not read, a recogniser family, a view not drawn (`v:section_aa`), the
  height ladder (`step_level`), the whole part (`part`, for PMI not read), a title-block cell (`title_block/<field>`, the sheet its
  subject), a spec entry not applied, a thread a schedule states. `fix` is a list of `edit`
  operations that resolve the problem, given only where one does: a band of nothing removed
  (`tolerance_withheld`); an omitted dimension brought back, else its tolerance or fit removed
  (`tolerance_not_shown`); an omitted view restored (`dimension_view_not_shown`,
  `view_plan_incomplete`, `pmi_not_drawn`, `section_not_shown`) or the omitted dimension
  stating a PMI dimension brought back (`pmi_not_drawn`); a section with no plane un-asked
  (`section_not_shown`); an empty isometric omitted (`iso_projection_empty`); another revision's entries removed (`spec_entries_inactive`). Placement
  problems use `place` (above). Non-placement fixes are applied and re-planned in
  `tests/items.rs` (the isometric's in `tests/views.rs`), and the problem is gone. Where the
  turned-part rule, not the spec, dropped a view, the message says so and gives no fix (restoring
  an omission the spec never made would fail).
- **Fix scope and alternatives.** Every executable remedy is in `fixes`, an ordered list of
  `{scope, description, ops}`. Apply one alternative's `ops` together; `fix` retains the
  preferred operation bundle for older clients. Alternatives are ordered `local`, `view`,
  then `sheet`. Scope comes only from the Spec paths in the operations, using the table in
  `src/fixes.rs`; a bundle takes its broadest scope, edits to multiple item/view owners are `sheet`, and
  unknown paths default to `sheet`. Object-valued edits include their nested Spec paths;
  replacing a whole collection retains its conservative sheet scope.
  `local` edits one item's placement, side, lane, along, offset, text, style, omission or
  tolerance. `view` edits a view's placement, scale or omission, or adds/removes a detail or
  section. `sheet` edits paper, sheet scale/count/allocation, hole-table selection,
  dimension source/schemes, PMI mode, or other drawing-wide intent. Sheet descriptions
  state the changes once in drawing language, then say “The whole sheet is laid out again, so placements you have moved by hand may change.” Pins retain relative intent, but the canonical frame is rebuilt for the new paper and scale. Scope describes authored intent;
  it is not a guarantee that the solver keeps every other item stationary.
  New crossing/label remedies use existing compatible placements, checked against the
  settled ink with the existing collision predicates; applying them may re-space peers. Generating them performs no full layout and
  changes no ink. Eligible hole schedules can be offered at the next legible ISO sheet scale
  as an explicit sheet alternative after local remedies. Lint's prose `fix` advice has no Spec operations, so it carries conservative `scope: sheet`
  and an explicit `fix_description` containing the advice without claiming that a sheet re-layout is applied; the original `fix` text remains available for older clients. The Plan, Session, MCP and WASM JSON expose the same
  scoped alternatives. Session item/options `operation_scopes` maps operation names to scope,
  and every `settable` descriptor has `scope` (the broadest scope of its `paths`, if grouped).
- **A problem's message is in drawing language** (#24; `src/wording.rs`): it names a dimension
  by the text it prints (`2× 30`), a feature by what a draftsman sees of it (`the ⌀8 hole`), a
  view by its name (`the plan view`), a note by its text; never by an internal ID (`d:`, `f:`,
  `v:`, `pmi:`, `n:`, `t:`, `face:`), which stay in `subjects` and `refs`. A PMI record not
  drawn is still named by its source (`dimensional_size:#123`), as it is no item. Messages are
  for people; nothing should parse them. `tests/problem_wording.rs` scans every message of the
  test parts and AP242 fixtures.
- **Nothing the plan approves vanishes silently.** An item that can't be drawn is reported
  against its ID, never dropped. This is carried over from the Python version's ADR 4
  invariant 9.
- **The baseline and explicit-paper scale are chosen on finished drawings** (maintainer, 2026-10-12,
  H6; `model::plan_recovered` / `model::recover`, policy in `sheet_fit::choose`).
  The smallest-first policy above wraps this baseline search. Single sheets from A4
  through `sheet/preferred_max_size`, then the preferred sheet count, are judged
  before larger paper. Explicit size or scale bypasses the preference search. A
  candidate is valid and complete when it has no hard layout finding, its drawn items and
  views fit inside the border and off the title block, it drops or loses no measurement
  the first plan carried, every GD&T item is drawn, and it lays no more annotations over
  another view than the first plan. The smallest fit is the smallest sheet with a valid
  complete candidate, at its largest such scale. Only that pair and the next larger sheet
  at a larger scale compete. Against a valid first plan, an allowed pair that adds material
  crossings (a leader or witness through a feature) is set aside; this never changes which
  pair is the smallest fit, and if every allowed pair adds them the valid first plan is
  kept. Among the rest, in
  `sheet_fit::FILL_BAND` (initially 60–85%) the larger scale wins, then the smaller sheet;
  if none reaches the band, the fullest wins. A Spec-named sheet keeps its sheet and
  chooses its scale by the same ranking; a named scale or pinned placement is not
  re-planned. On the larger neighbour the orientation view keeps the smallest fit's
  paper scale, so the extra paper goes to the dimensioned views.
  `sheet.fill` is the union of each drawn item's bounding footprint (view envelopes,
  dimensions with witnesses, leaders and arrows, GD&T, notes, tables, balloons and the
  scale/projection/section furniture), with 1 mm clearance, inside the border and
  excluding the drawn title grid. Reserved bands never count. `sheet.basis` lists every
  measured candidate's fill and findings. The legibility floor
  (`sheet_fit::min_legible_scale`) keeps the smallest hole or slot opening at least
  0.8 mm and the longest extent at least 20 mm on paper, and never goes below 1:10. If
  no candidate is valid and complete, the first drawing and its findings are kept on
  its own sheet, with `layout_recovery_exhausted`: a warning when that drawing is below
  the legibility floor, otherwise info. Only a first drawing below 1:10 is redrawn at
  1:10 on its sheet. A GD&T layout may also be composed from settled view blocks of a
  same-scale A0 template, moving witnesses, leaders and frames with their view; one
  measured correction may lift a free projection row and refit its orientation view.
  Every title-block collision lifts the row, even when the resulting overflow must be
  reported. Nothing is clipped.
- **A hole item with no place on the sheet is dropped and escalated** (the maintainer's decision
  of 2026-10-09, draftwright's `_prepare_hole_table_run`; `src/hole_table.rs`, applied to every
  plan the recovery judges by `model::plan_escalated`). A hole callout or hole location that
  placement put off the sheet keeps its ID in the plan, unplaced, with a problem
  (`callout_dropped`, `location_ref_dropped`, or `off_axis_location_dropped` at info for a hole
  the plan view does not see end-on); every such drop is a required loss the recovery tries to
  repair. A dropped callout or plan-view location escalates as draftwright's does: when the plan
  view has at least 16 holes outside a pattern, a hole table (`hole_table`: one row per hole,
  `TAG | ⌀ | DEPTH | X | Y`, in up to four side-by-side blocks towards the top right) replaces
  their plain callouts and locations, each row with a balloon (`balloons`: a tagged ring leadered
  from the hole); a dropped pattern callout in the plan view gets one `n×TAG` balloon that states
  nothing, so its drop stays. The table is kept only whole, every row's balloon placed; else it
  is withdrawn and the drops stay reported (`table_dropped`, `balloon_dropped`). The drawing
  records each row as an annotation claiming what its cells state, so lint credits the rows as
  it credits callouts, never reading the plan.

The title block also carries the resolved third-angle projection symbol in
`sheet.projection_symbol` (method and paper curves). Native view planning uses third
angle; first-angle planning remains unsupported. Rendering draws these planned curves
in the reserved band beside the scale note. Older plans may omit this optional field.
A blank GEN. TOL. cell is `general_tolerance_missing` (warning, subject `sheet`), with
a set fix at `sheet/title/tolerance`. The offered `UNSPECIFIED` explicitly acknowledges
the absence without asserting manufacturing limits; supply the intended requirement
to state limits. A nonblank Spec tolerance or the unique usable PMI tolerance fills
the cell. A Spec material different from the unique usable STEP material is
`step_material_disagreement` (warning, both values and source/path refs); the Spec
value still wins, including an explicit blank.

### Centre furniture

Each shown principal or section view carries automatic `line_work` in paper
coordinates: provenance ID, kind (`cross`, `axis`, `pitch_circle`, `cutting_plane`)
and exact curves, with a centre for end-on crosses and a `global_axis` flag only
for the whole turning axis. It contains no values and is not Spec-addressable.
Hole, boss, turned-band and circular-seat geometry supplies it independently of
dimensional omissions. Bolt-circle crosses run radially and tangentially.

The centre layer uses thin black long-dash-dot ink; a long dash meets at the
cross centre. Crosses extend approximately 3 mm beyond the edge, proportionately
less for very small holes and more for very large ones. Local axes extend beyond
their ends; the whole turning axis extends 5 mm. Visible and hidden ink is painted above
centre ink; this is paint order, so chain ink can remain in hidden-dash gaps. Sections omit axes wholly on the removed side and retain axes of
cut holes and cylinders.

This furniture, including the finite cutting-plane line, its arrowhead/wing
outlines and both letters measured in the rendering font, enters the existing
fixed-ink placement hook before dimensions and PMI. Labels and travel along a
line are hard conflicts; transverse crossings are routing costs, with only a
local transverse tip exemption on an explicitly marked global turning axis.
Furniture provenance appears in problem `refs`; its carrier view is a subject.
Drawing lint checks the actual marked centres against independent projections
of the Analysis's holes, including pattern members and double-D bores. Structural
review separately counts collinear leader travel along fixed chain strokes and
actual labels touching section letters or arrows; quality includes those in ink.
A callout label that must stay across the end of a hole axis (its leader search
found no place as good under the ordinary-ink and feature-crossing floors) is not
moved off its shelf: the axis's overrun past the feature is shortened instead
(ISO 128-24), stopping 0.5 mm short of the label and keeping at least 1 mm past
the feature (`centrelines::trim`, after dimension placement). A label across the
feature's own span is never trimmed around; lint still reports it.
A leader shaft running within 1 mm of a centre stroke at under ten degrees, for
more than 0.5 mm, reads as part of the centre line (`centrelines::runs_beside`).
Exact collinear travel is a hard blocker; near-parallel shaft travel is weighted
just below one crossing, so it is avoided whenever that costs no crossing. A
radial leader leaving its own hole along that hole's centre-mark arm is exempt.

### Dimensions (first cut)

Default nominal text uses the shortest decimal within 1e-6 of the value after 1e-6 noise
removal, up to three decimal places (`4.75`, `7.4`, `12`, `247.65000001 -> 247.65`).
If three places are insufficient, all three are printed and an info `nominal_rounded`
problem identifies the carrier and its modelled and printed values. Per-measurement Spec
precision overrides this default; tolerances keep their own formatting. This intentionally
departs from Python's one-decimal `_fmt`, including flat auto-decimal padding. See
[the drawn-value verification report](drawn-values-2026-10-18.md).

Interrupted coplanar faces on the same stock form one flat feature. A/F is used only for
proven opposing parallel flats. A single flat retains the Python flat-to-opposite-OD
measurement without A/F. A drawn height or axis-offset span whose endpoint is on that
flat's plane also carries its coverage claim, even if its separate callout is omitted.

`src/dims/` plans the dimensions draftwright's defaults would state for the families ported
so far, each a Plan item `{id, kind, view, value, label, terms, measurements, default, from_spec,
basis, placed, reference?, determined_by?}`:

- **Overall extents:** `d:envelope/width.length` (in the front view), `depth.length` (side),
  `height.length` (front) for a prismatic part (each falling back to the plan or side view when
  its view is omitted). A part turned whole states `od.diameter` (printed `ø…`) and its length along
  the axis instead, in either profile view; the diameter is drawn across whichever radial axis
  that view shows. A view that looks along a dimension never carries it.
- **Turned step lengths** on a whole connected multi-step profile use a baseline from one
  inspection end. A PMI planar datum or PMI shoulder lengths retain precedence (#33);
  otherwise the larger end diameter selects the end, with the low station breaking a tie.
  Independent profiles retain a chain of `d:f:turned_step:<n>/step.length`.
  In a baseline the band at the datum keeps its `step.length`, each further shoulder is
  `d:f:turned_step:<n>/step_position.length` (the distance from the datum to the shoulder that
  ends band `n`), and the overall length is the last rung (it also claims the last band's
  `step_position.length`), so no rung is reference. The other bands' `step.length` items stay
  in the inventory (a spec may name them) and are `covered` by the rung locating their band,
  unless the spec tolerances them or omits that rung. A PMI length across the same span takes its
  rung's place. Lint counts a band's length as placed when a baseline locates both its
  shoulders.
  On a part of several turned bodies along one axis (stream K5, ARCH0020-2), each body's
  chain goes in the profile view that separates its axis line from the others' and its witnesses
  start at its own silhouette, on the side of the view it is nearer; a groove whose record names
  none of the part's turned profiles is refused (`feature_not_dimensioned`, ADR3-12). Lint
  credits a grouped length (`3× 10`) only when it prints exactly the count of the adjoining bands
  it claims.

- **Gusset ribs** (`src/dims/gussets.rs`, #256) use lint’s checked groups. Each group states thickness and both legs
  once, with `n×` for its ribs; arrays state centre pitch, mirrored pairs centre spacing,
  and every group states the box-minimum distance along its thickness axis to the first
  centre (mirror plane for a pair). Legs lie in the view square to the triangle; the
  other spans default to the view looking along the first support axis in part-axis
  order. `features/<group-id>/gusset_view` selects the view along either leg (`front`,
  `side`, `plan`); an unreadable group or a view that hides thickness is rejected.
  Each size statement claims the group and its members, so both gusset lint and
  general feature completeness follow the same drawn statement.
  The group ID is the pattern ID, or a lone rib ID. Existing
  `dimensions/d:<group-id>/gusset_thickness.length`, `gusset_leg.length.<axis>`,
  `gusset_pitch.length`, `gusset_spacing.length`, `gusset_location.length` overrides
  control each statement's view, placement, omission, precision and tolerance. They
  change drafting intent, never recognised geometry or the group's membership/count.
  An equal size at a different span still needs its own leg dimension. An existing
  dimension of the exact projected leg span carries the group claim; an explicit
  dimension override retains a separate statement. PMI precedence
  uses the existing strict/first/ignore policy and exact attached spans; first fills gaps.
  Unreadable groups keep their port-gap and unverifiable lint findings.
  Location witnesses use the box’s outward silhouette; `place/side` overrides the
  automatic side through the existing per-dimension placement intent.

- **Raised pads** (`src/dims/pads.rs`, stream K5): `d:f:pad:<n>/pad_width.length` and
  `pad_length.length` between the pad's own edges, `pad_height.length` as a callout `5 HIGH`, and
  `location_pad.location.x|y` (a pad along z) or `location_pad.<axis>` from the part's corner to
  its centre, all in the view that looks down on it; a direction on the datum is an info
  `location_on_datum`.
- **Free-direction slots** (`src/dims/oriented_slots.rs`, stream K5): one callout
  `d:f:oriented_slot:<n>/oriented_slot_width.length`, `ORIENTED SLOT 2 WIDE × 4.5 LONG`, claiming
  width and length, in the view along the slot's dominant run axis, its leader to the slot's rim;
  equal slots in one view share a counted callout.
- **Hole callouts** in draftwright's grammar (`4× ⌀10 THRU ⌴ ⌀16 ↧ 5 (2×2)`), in the view that
  looks along the hole. Identical holes drilled the same way are grouped and counted; quiddity's
  hole patterns (kept by the bridge in `recognition.patterns` as `f:hole_pattern:<n>`) are called
  out as grids `(r×c)` or bolt circles `EQ SP ON øD BC` (five or more members), or as linear
  patterns. THRU is the feature's fact (`bottom: through`). A spotface is stated with ASME
  Y14.5-2018's spotface symbol, the counterbore symbol with SF inside (`⌴SF` in the text, drawn as
  one symbol with the font's `SF` in the box), where draftwright prints the counterbore's `⌴`. A
  hole with both a counterbore and a spotface states both (`⌴ ⌀11 ↧ 6.4 ⌴SF ⌀20 ↧ 1`, counterbore
  first), where draftwright prints only the counterbore and loses the spotface its own lint
  requires.
- **Locations** (`location.length.<axis>`) from the part's box, or the same inspection end
  for an axial location on a whole turned profile, in the hole's view, one
  dimension per distance; patterns by their centre (or a linear pattern by its first member), with
  their pitches (`grid_pitch.length.row|col`, `pitch.length`). A hole or bolt circle on a turned
  part's axis is not located across it.

- **Slot patterns** (`src/dims/slots.rs`), as draftwright's `render_slot_patterns`: one
  `N× SLOT W × L` callout, an obround's `2× R`, the pitch (`2× 25`) or the grid's column and row
  pitches (quiddity's), and the centre located on both axes (`location_slot_pattern.length.<axis>`),
  in the view that looks through the slots; the members are not dimensioned again. A pattern whose
  slots do not stand as quiddity describes them is reported and its slots dimensioned singly.

- **Fillets and chamfers** (`src/dims/edges.rs`): `R3` and `C1` or `4.5 × 42.1°` callouts, equal
  ones counted (`4× R3`), where the edge is seen end-on, or in the profile when quiddity marks
  them turned. A record whose sizes or axis cannot be read is reported (`feature_not_dimensioned`).
  Python defers oriented chamfers (`consumer_semantics_deferred`); on CTC-01 its front
  view states only `2× C5`. These four records describe the hex boss's 5 × 5 mm top
  bevels (corner z=50, run length 51.96 mm), independently of the plate's V-notches.
  Oriented chamfers are read with their defining faces, true support-plane legs, run,
  virtual corner and support spans. Their leader states `C5` for equal legs at 45°, or
  `10 × 26.6°`; it never measures an angle from a foreshortened projection. Equal bevels
  share a counted callout with principal-edge chamfers, retaining the existing principal
  owner and ID (CTC-01: `2× C5` becomes `6× C5`, while `C50` remains independent).
  Bevels are edge treatments located by their drawn supports, as principal chamfers are.
  The provider virtual corner is a run midpoint, not a design datum: no automatic station
  is invented there. Explicit dimension schemes may still target that corner.
- **Rectangular through-notches**: quiddity's published `RectangularThroughNotch`
  records supply their thickness axis, opening direction, bottom-wall centre,
  width, depth and optional corner radius. Draftwright reads that geometry;
  it does not recognise or reconstruct the family. Ordinary linear dimensions
  state its width, depth and mouth-corner locations in the view through the plate.
  A common opening plane shares one ordinate with all physical feature claims.
  Existing radius callouts retain the corner radii. A measured PMI width keeps
  a linear carrier between exactly its authored wall planes. Bounded interior
  lanes may use proven openings, with the same attachment collars and complete
  ink checks as other dimensions; no coordinate notes or feature tags substitute
  for the dimensions. Final local clearance checks include filled arrowheads;
  a leader may shorten its shoulder or refine its bend by at most 4 mm,
  reproving its attachment and whole route without increasing reported contacts.
- **V-notches** (`src/lint/notch_evidence.rs`, `src/dims/notches.rs`): ordered outer-profile supports
  establish symmetric straight-sided V recesses with a principal mouth and depth direction.
  A sharp or tangent circular root is supported. The true included angle is drawn in the
  normal principal view, plus depth to the theoretical sharp apex and mouth-centre locations
  from minimum envelope datums. Equal depth spans share a counted dimension; coincident
  locations retain all notch claims through ordinary span ownership. This is a bounded
  drafting projection of provider evidence, not another recognition family. Back profiles,
  asymmetric sections, inner wires and arbitrary sloping edge pairs grant no notch authority.
  The sheet reserves the minimum exterior angular footprint before packing, so an acute
  notch does not have to extend beyond a band estimated only from linear lanes. Added
  vertical notch statements reserve their own datum lanes; a silhouette-mouth station
  leaves its witness on that free side. Mouth stations distribute their ink between
  true aligned projections: left-half X
  stations prefer the normal view, right-half X stations prefer front, and Y stations
  prefer side. This keeps the plan's notch arcs and bore-leader bands clear without
  stacking every X witness in front. When front carries GD&T, both X stations stay
  there so its existing shared dimension/frame band is retained; removing one tier
  must not push a control frame into a datum's compact route. A notch angle may use
  blank exterior space within its view's
  envelope only after projected-face containment and edge/arrow clearance prove it empty.
  Every planar projection counts; curved faces use their full covers. Radius leaders displaced
  by these new bands consider model strokes before their joint solve. This preserves short
  existing leaders without enabling general interior angular placement.
  Within the chosen sheet and scale, the layout tries reserving an existing automatic
  concave radius-centre schedule when at least four exact, end-on centres share a view.
  It may use another existing arrangement but cannot enlarge the chosen page or change
  its scale for this optional table. Principal fillet owners and authored radius
  presentations are excluded; the late table still requires genuine leader obstruction
  and states every occurrence's own centre. This keeps the added notch
  information from displacing CTC-01's four-row R25 schedule.
  Python's general profile-angle rule chooses only the foremost camera-facing planar
  face per body/view: on CTC-01 that is the hex top, whose 120° corners the HEX callout
  defines. It never reaches the plate notches. This bounded notch grammar deliberately
  adds that information; it does not enable general profile-angle planning.
  Profile dimensions use ordinary envelope IDs, for example
  `d:envelope/notch.angle.f3_e88_e90`, with support stamps distinguishing physical
  corners. Their measurement claims retain the source notch identity. Overall-extent
  policies match the four extent IDs exactly, so a profile dimension never inherits
  an overall tolerance or fit. The IDs accept omit, tolerance, placement and label-text intent.
  Authored angles compare the same projected supports even when their normal-section stations
  differ; authored mode transfers the automatic role, and auto mode names the existing owner.
- **Outer-profile corners** (`src/lint/profile_angles.rs`, `src/dims/profile_angles.rs`;
  LINT-angular-profile_angle_requirement). Each non-right convex corner of the foremost
  camera-facing outer profile (a plate's face counts as foremost unless a nearer face of the same
  plate hides it, so a bent bracket's base plate is required behind the upright's end face; a
  notch's flank corners are left to the notch) needs its geometry *defined*, not necessarily its
  angle printed
  (ISO 129-1, ASME Y14.5: dimension only what is needed; no angle is implied except 0°, 90° and
  180°). A corner is defined when its two edges are located by linear dimensions chained to the
  part's box or datum planes: the offset of a principal edge, both coordinates of both ends of an
  oblique edge (a rounded end uses its theoretical sharp vertex). By default (`auto`):
  - a corner already defined gets no angle (never a toleranced angle on a linearly fixed
    corner);
  - a cut across a corner of principal edges (one edge of the corner principal, and the corner
    at the far end of its oblique edge also meeting a principal edge: l-plate-5-holes' 45° cut,
    a bracket's corner chamfers, a chamfered rectangle) is located by its legs (#217): each
    missing station from the nearer side of the part's box, drawn on the side of the view its
    corner lies on so its witness leaves the part at once, reserving its lane there when that is
    not the view's default strip (a `dimensions/<id>` side overrides),
    `d:envelope/profile_location_<axis>.length.f<face>_<n>`. Its two angles would fix the cut's
    direction, not where it lies. By default two equal stations from opposite sides of the box
    (a symmetric octagon's, a bracket's two chamfers) are stated once, `2× v`, as
    `…f<face>_<n>_<m>`, and lint reads a counted station as locating its mirror; the `linear`
    choice, or a Spec naming either station, states each on its own;
  - a 45° chamfer across a right corner (such a cut whose two corners are both 135°, so its legs
    are equal) is called out by its size instead, `C<leg>` as an edge chamfer is, with a leader
    to the cut, and equal ones of a face share one counted callout, `4× C21.075` (maintainer
    2026-10-28), `d:envelope/profile_chamfer.length.f<face>_e<i>`. Only its principal edges'
    missing offsets get stations; its legs need none. Lint credits the callout with both
    corners' 135° when its size is the cut's leg, and locates the cut from its principal edges'
    offsets. A Spec naming either corner's angle (`{}` or `omit`) keeps that cut as before;
  - any other corner gets its angle in the principal view that shows it true,
    `d:envelope/profile_angle.angle.f<face>_e<i>_e<j>`. Completing it with stations is the
    explicit `linear` choice below: stations are ordinary dimensions, and completing every
    corner automatically moved mfcadpp/1013, 10020 and CTC-04 onto other paper;
  - a regular polygon (a closed all-line profile whose corners are all issued and equal and whose
    sides are equal) gets one counted angle, `6× 120°` as
    `d:envelope/profile_angle.angle.f<face>`, never one per corner. Lint proves the count from
    the closed loop of visible edges through the drawn corner. An octagon of equal corners but
    unequal sides (FTC-08's and STC-08's chamfered rectangle) takes its chamfers' `4× C21.075`
    instead, or its counted `8× 135°` under the `angle` choice.

  Lint reports a corner whose angle is drawn but whose edges no drawn linear dimension places
  (`profile_corner_unlocated`): a principal edge needs its offset, an oblique edge both
  coordinates of at least one end.

  An angle stays local to its corner, so a reader sees which corner it states: its arc's radius
  and its whole label lie within twelve label heights of the corner (about 26 mm), its label
  sits on its arc (on or beside the bisector), and all its ink lies within the view's reserved
  area. A short edge does not limit it: the witness lines extend the corner's edges outward past
  the part, as a short chamfer's angle is drawn. It stands inside the corner (label on proven
  blank face) or in the equal sector between the extended edges (label on proven exterior
  paper). A place whose lines cross nothing is preferred; otherwise its arc and witness lines may
  cross the part's edges, an edge blend's silhouette included (as dimension and extension lines
  may where unavoidable), but never a hole or boss seen end-on, never another item's dimension
  line, leader, arrow, frame or label; only other
  dimensions' extension lines, centre lines and cutting-plane chain lines may be crossed
  transversely. No callout's leader or padded label may meet its witness lines (the 2026-10-12
  extension contact rule), and leader refinement keeps clear of them as of linear extension
  lines. Two mirror-equal corners that keep their angles in one view (CTC-04's waist) take one
  counted angle, `2× θ°`, drawn at whichever corner has a clear place; lint proves the count
  from the mirrored corner's visible edges. The
  angles are placed last of all, around the finished ink. Only when that leaves corners
  unstated is placing them before the callouts route tried, and it is kept only when it states
  more corners and makes no drawing finding (including extension contacts) worse; an early angle
  a later callout meets is withdrawn and tried again last. An angle with no clear place is not drawn
  (`angular_dimension_dropped`, info) and lint still reports its corner
  `angular_requirement_missing`. Placement never moves another item, takes a fallback place, or
  changes the sheet: profile angles are not required measurements when sheets and scales are
  compared. Lint reads linear definition from the drawn ink (each linear dimension re-measured
  from its witnesses through its view's transform), so a corner fixed by drawn dimensions is
  not reported.

  Overrides (Spec intent): `dimensions/<id>` with `{}` shows a corner's angle even where the
  defaults would not draw it (as a reference, in parentheses, when linear dimensions already
  fix the corner), and `omit` hides it. `faces/face:<n>/profile_corners` chooses, for the outer
  profile of face *n*: `auto` (the default above), `angle` (every corner's angle; a reference
  where the corner is already linearly fixed; no stations) or `linear` (stations for every
  corner, a regular polygon's too; these are ordinary dimensions and may need more paper). A
  box side (`top`, `front`, …) names its faces as for datums.
  An existing source PMI angle on the exact same flank faces also owns the bounded
  angle across normal-section heights: AP242 CTC-01 keeps its `60 ±0.5` and omits the
  duplicate automatic `60°`. Its PMI ID and tolerance remain, with both claims.
  Independent lint remeasures the source supports before accepting PMI's implicit
  degree notation; removing the typed source claim or changing the nominal loses credit.
  A bevel's already printed first leg can also own depth when the exact flank face,
  mouth-end virtual corner, principal run, both legs and half-angle agree within the
  provider's published precision. On 10020, `10.6 × 10.6°` retains that size instead
  of adding a duplicate depth line; CTC-01's unrelated C50 stays independent.
  Dimension schemes may own mouth stations. Lint re-reads the provider supports and rejects
  wrong nominal angle, depth and location claims, without taking tolerance digits as proof.

- **Tolerances and fits** (`src/dims/tolerance.rs`; ARCH0011-1: a tolerance or fit belongs to a
  dimension) come only from the spec, never from the part (ADR5-D2), and reach the label
  verbatim, as draftwright prints them:
  - `dimensions/<id>/tolerance` is `"±0.05"` or `"+0.1 -0.02"` (an upper and a lower deviation,
    both magnitudes, as draftwright's `.tolerance(t)` and `.tolerance(lo, hi)`). It is printed
    after the term that states the value (`304.8 ±0.02`, `4× ⌀7.1 ±0.03 THRU …`, `HEX 100 ±0.2
    A/F`), with its authored digits and at least the drawn precision of one decimal (`±1` prints
    `±1.0`): an authored magnitude is never rounded into another requirement. Any other form is
    refused naming the dimension (`"+-0.05"` reads as `"±0.05"`).
  - `features/<id>/fit` is an ISO 286 class from draftwright's table (`fits.py`): on a hole an
    upper-case hole class (H, G, F), on a cylindrical boss a lower-case shaft class (h, g, f, js,
    k, n, p), grades 5 to 11, for a nominal size up to 250 mm. It is printed after the diameter
    of the callout that states the feature's size. The default is the class alone
    (`2× ⌀6.3 H7 ↧ 12.7`, `ø44.5 h6`), including classes imported from AP242 PMI.
    `sheet/fit_deviations: true` adds ISO 286 deviations stacked upper above lower after
    the class, at 70% text height (ISO 129-1). Signs and upper/lower order are retained;
    zero prints `0`. Plan labels and searchable text encode that stack with a newline,
    for example `ø10 g6 -0.005\n-0.014`. This applies to turned diameters, hole fits,
    spec fits, editor previews and MCP output. Explicit ± and band tolerances are unchanged.
    The legacy `sheet/fit_limits` spelling remains accepted; `fit_deviations` takes precedence
    when both are set. A fit on any other feature, on a hole pattern (set it on its holes), on a feature the
    part does not have, of the wrong case (`"h7" reads as "H7"`) or outside the table is
    refused; so is a size given both a tolerance and a fit.
  - The item lists what the spec gave it in `from_spec` (`tolerance`, `fit`) and is not a
    default. A band is withheld, with a `tolerance_withheld` warning on the item's ID, when it
    is a band of nothing (`±0`: ARCH0019-2; draftwright prints `±0.0`) or when an `n×` callout's
    members do not all have the same fit (ARCH0019-3; draftwright splits such a group of bosses
    instead). A tolerance on a dimension the plan leaves out, or a fit on a feature no planned
    callout states, is a `tolerance_not_shown` warning on its ID. Across a revision, feature
    entries are kept inactive like the other feature-keyed entries.

Plate and wall thicknesses (`src/dims/plates.rs`, ISSUE-1373-a) are ordinary
`d:f:plate:<n>/thickness.length` items: z slabs on the front-left strip, y walls
above the side view and x walls below front. Equal thicknesses on one recognised
body share a counted statement with every slab's claim. Proven envelope, step,
turned-band and channel statements may cover an item; supporting polygonal slabs
and slot-pattern webs require physical ownership evidence. Single-axis native
slabs are retained when the ladder does not already state their interval. Plate
coverage (`src/lint/plates.rs`) checks claimed, printed nominal values independently;
zero-ink `suppressed:<id>` consumer annotations record authored omissions without
stating a thickness or letting lint read the Spec. Outcomes use
`plate_requirement_missing`, `_suppressed` and `_unverifiable`.

Each family is a planner module in `src/dims/` (`envelope`, `turned`, `holes`, `slots`, `bosses`,
`edges`, `pads`, `oriented_slots`, …), listed in `dims::PLANNERS` with the feature kinds it dimensions. Every other recognised
feature gets one warning problem, `dimensions_not_ported`, on its
ID: draftwright dimensions those kinds and the port does not yet, so this is a gap in the port,
not in the part. Record types draftwright itself leaves undimensioned (freeform surfaces,
thin-wall and sheet-metal bodies, …; `detect.py`'s unconsumed and orchestrated records) are not
reported, except an angled step: draftwright cannot dimension one, so it gets a warning
`angled_step_requirement_unsupported` on its ID (ISSUE-1247-a; lint's code and wording). That
Problem is `p:angled_step_requirement_unsupported:<feature id>` (`src/dims/mod.rs`,
`unsupported`), by the maintainer's decision of 2026-10-09 (stream d-angled-step-unsupported),
until an angled step is dimensioned or the port has a completeness report.

One measurement has one owner (`src/dims/redundancy.rs`). Matching physical
support-plane intervals share a surviving statement and a typed `covered` omission;
a functional outside diameter or A/F can own an equal overall interval. Equal values
alone prove nothing. A stated overall length makes one otherwise redundant chain
member a typed `covered` omission by default, with all determining dimension IDs in its
basis; a member the spec tolerances, or one that states the part's PMI, is never the one (#33).
The same omission policy applies to slot-wall shoulders, floor intervals and boss heights
closed by a stated ladder rung. `sheet/references` is a boolean (default `false`);
`true` restores these auxiliary dimensions as parenthesized references. A pinned reference
stays drawn, and explicit per-dimension `style: "reference"` remains supported. Independent drawing lint reports
`duplicate_measurement` and `closed_dimension_chain` from printed claims and Analysis
stations, excluding parenthesized references. Point ordinates, height-ladder datums
and counted/compound statements retain their existing ownership contracts. Reference
choice does not change when another member gains a tolerance. Omission can make an
unpinned reference controlling, reflowing its text at the same centre and witnesses.
A pinned reference keeps its ink and raises `pinned_reference_chain_changed` if its
controlling chain is edited; its offered fix releases the pin. With references opted in,
pinned, or explicitly styled, a dimension drawn in brackets carries `reference: true` and, in `determined_by`, the IDs of the dimensions that determine it,
in the order its basis names them: a chain's stated total and other members, the stated overall
and slot length for a floor interval, a slot's width and location for a shoulder on its wall
(#26; `tests/problem_wording.rs`). Both fields are optional on the wire and absent on a
controlling dimension; a pinned reference kept after its chain was edited has `reference: true`
and no `determined_by`. A flush boss height is credited from
the same stated envelope interval, without reading the omission.
An authored set decides what is drawn, not where the geometry states a fact (stream K6,
ISSUE-1154-a): an item the set leaves out that the automatic plan would cover by an owner the
set keeps is `covered` by that owner, with the automatic basis, rather than `not_authored`, and
the owner carries the same claims; an owner the set leaves out takes nothing. A boss height
closed by a stated ladder rung and the overall height (a boss standing on a face level) is the
chain's covered member by default (a reference when opted in), determined by the rung
and the overall height. Lint reports a
bracketed label that prints a tolerance or a fit (`toleranced_reference`: a reference dimension
is only for a redundant, untoleranced one) and a linear or grid pattern's pitch printed beside
every member's own location along that axis (`over_dimensioned_pattern`, ADR4-U1).

The Plan also carries `status` (`clear` or `needs_attention`) and `completeness`:
`claimed_faces`, `faces` and `unplanned_features`. Face claims are the distinct valid
face indices on Analysis features, measured against `binding.face_count`; they describe
this Analysis's recognition inventory, not recognition recall. When the recognition carries
quiddity's `face_accounting` (Analysis `face_accounting`, by file face index; #207), a face it
accounts to any record, evidence records such as `step_levels` included, is claimed, and on a
single solid its `body` faces receive credit when printed measurements prove all overall
extents; `refused` and `unexplained` faces stay unclaimed. Without that accounting, optional
`stock_faces` records principal boundary plane face IDs, credited on the same condition;
unrecognised interior/curved faces can still be unclaimed. Older analyses default
that evidence to empty; a six-face rectangular box can be proved from its profiles. An informational, advisory `unclaimed_geometry` finding names both counts and asks for review,
without inventing missed features. A recognised feature with no drawn measurement is
reported at warning; absorbed groove floors and bands, pattern members, dimensions
carried by a hole table, and the existing deliberately undrafted record types retain
their established dispositions. `unplanned_features` counts whole features, not each
missing parameter; independent lint covers missing extents, band sizes and pattern
locations/pitches.

New analyses include optional `solid_count`. Several solids make these completeness
findings informational, following Python's assembly coverage policy. Older analyses
without the count are conservatively assessed at warning. With several solids, optional
`body_faces` lists the faces of the one solid that fills the part's box, when exactly one does:
support-plane chains (closed chains, references, determined lengths) are walked on its features
and the box alone, never from one solid to another (#220). `clear` means no assessed
Plan problem needs attention (warning or error); it does not mean manufacturing readiness.
The inspect summary additionally includes render problems in that status. Independent
coverage lint still lives in `review.lint`, rather than being inferred from the Plan.

`lint::check` now runs the envelope and turned-step ledgers as findings:
`missing_principal_dimension`, `axial_length_missing`,
`turned_step_requirement_missing` and `turned_step_requirement_unverifiable`.
Pattern owners have `hole_pattern_requirement_missing` or
`hole_pattern_requirement_unverifiable`, with each location axis or pitch named in
`requirements`. These checks read Analysis geometry and printed drawing marks only.
The ledger-to-finding extension is a deliberate Python difference, authorised for G1;
its verdicts are in `tests/fixtures/known_lint.json` and
`tests/fixtures/known_completeness.json`. No dimension planner changes for these checks.

`placed` is the item's ink on the page (lines, arrowheads, the label's baseline origin, rotation
and box); renderers draw it and the terms, and nothing else. Placement is simple: lanes outward
from each view (draftwright's strips: below, and above the plan; right, and left of the plan),
sizes nearest, locations by distance, overall extents, then callouts with 45° leaders. The side
view's bands are reserved from the lanes its dimensions take (`dims::lanes`, `Demand::lanes`,
`layout::Strips::reserve`); the front's and the plan's are still draftwright's estimates. Every
item the part would have is placed before the spec chooses what is drawn, so omitting or adding
one moves no other (ARCH0003-2). A strip that cannot hold its linear dimensions on the page
passes the lanes it has no room for to the strip across the view (`dims::placement::fall_through`,
ISSUE-1215-e), an overall extent claiming the free lanes first; one that fits on neither side keeps
its lane. Then labels settle off other items' ink
(`dims::placement::settle`, a first part of ADR2-10's bounded local solve): a linear label
slides along its line, or moves to the other end of a span too short for it; a callout's leader
may turn to the left, its tip moving to the rim's left 45° point so the leader stays radial; a
callout whose lane does not fit the page (off the sheet, or its leader across another view) goes
out beside its view, as draftwright's plan callouts do. A moved label stays on the sheet and off
the views and the title block. Whatever then collides is
a problem on the item's ID (`annotation_overlap`, `annotation_ink_overlap`,
`annotation_over_view`, `annotation_line_over_view` for a line across a view not its own,
`annotation_over_title_block`, `annotation_off_sheet`, and the info
`annotation_outside_area` for ink past its view's area and bands); an item whose view is omitted
stays unplaced and is reported (`view_plan_incomplete`, naming every view that could carry it and
why none does). Placement problems on the corpus are a ratchet (`tests/placement.rs`,
`tests/fixtures/known_placement.json`).

What the collision checks cover, and what they do not: label boxes against label boxes, a
dimension's lines against other labels' boxes, labels against view outlines and the title block,
and all ink against the drawing area. **Not checked yet:** extension, dimension and leader lines
crossing another view's outline or other dimensions' lines (on dense sheets such as ftc06 the
front view's right-hand lanes run into the side view and callout leaders cross dimension lines,
unreported). Labels are measured and drawn with one bundled font (`dims::FONT`), so the boxes
are the ink. Each label's text run is the plan's string, symbols included, laid invisibly over
the ink in the PDF and the SVG so it can be searched and copied, and read by lint.

### General notes

`src/notes.rs` combines the part's PMI notes with the Spec's `notes`, a list of `{id, text}`,
and draws them as draftwright draws its document notes (`render_document_notes`). Notes about
making the part (deburr, etch, coating, heat treatment) belong in the model's PMI. Drawing-only
notes ("DO NOT SCALE", "DIMENSIONS IN MM") belong in the Spec. Existing Spec notes stay there;
there is no automatic migration.

- **PMI notes first.** Part-level `semantic text` attribute sets (PMI practice §7.4) carry one
  note per set: `general note`, `surface treatment` (also `coating`), `heat treatment` or
  `edge condition` (also `edges`, the NIST/haecceity spelling). Aliases produce the same
  canonical records and item IDs as the standard names.
  `src/pmi.rs` exposes each as a manufacturing-requirement record without requiring graphic
  presentation or producer metadata. `pmi_lowering::sheet_requirements` lowers all these kinds
  in one function. Specify's one-based `INTEGER_REPRESENTATION_ITEM('note index', n)`
  orders the notes; legacy non-negative `order` or `index` items (integer or decimal text)
  are also accepted. Indexed notes come first in index order, then unindexed notes in model
  instance order; equal indices keep model instance order. Spec notes follow in their list order.
  The default surface finish retains its symbol beside the title block.
- **No requirement and wording.** Trimmed, case-insensitive `None`, `N/A`, `NA`, `-`, empty
  or whitespace-only part-note values declare no requirement. The raw record stays read;
  its source reason and inventory basis say `info`, and its planned requirement has kind
  `no_requirement`, carrier `none`, state `not_authored`, and no place. It creates no Problem
  and no note row. Other general/edge notes retain their text. A coating or heat-treatment
  value gets `Coating: ` or `Heat treatment: ` unless it names a process. This is a small
  lexical rule: alphabetic word tokens, ignoring case, recognize `plate`/`plated`/`plating`
  and stems `anodis`, `anodiz`, `paint`, `coat`, `harden`, `temper`, `nitrid`, `treat`,
  `anneal`, `carbur`, `quench`, `passivat`, `oxid`, `phosphat`, `galvanis`, `galvaniz`.
  Thus `Anodise black` and `Harden to HRC 40` stay as authored; `Black` becomes
  `Coating: Black`, and `HRC 40` becomes `Heat treatment: HRC 40`. It does not interpret
  material specifications or invent process parameters.
- **Omit a PMI note.** Use `annotations/<PMI item ID>/omit: true`, the same stable identity and
  path convention as other PMI annotations, for example
  `annotations/pmi:manufacturing_requirement.general_note/omit`. The note remains in the model
  and in the plan/items inventory as `omitted_by_user`, with no place and number zero; it
  reserves no block space. This does not remove a matching existing Spec note.
- **Duplicates.** Text matching ignores case and normalizes whitespace runs, and accepts
  either the raw model value or its kind-prefixed drawing text. A matching Spec
  note is drawn once through the PMI item; the Spec item remains `covered` by it, with number
  zero. Info Problem `note_duplicated_in_spec` names both items and the PMI source and Spec
  path. Its offered fix sets `notes` to the list with that Spec copy removed. Planning never
  edits the Spec itself.
- **Refused** (by `plan` and `edit`, with the next free ID as the choice): an ID not of the form
  `n:<k>` (`k` a whole number from 1, no leading zeros), two notes with one ID, a note whose text
  is empty or only whitespace. An absent or empty list is not serialised, so a spec without
  notes reads and writes as before.
- **Plan items** (`notes`): `{id, text, number, rows, default: false, basis, placed}`. `number`
  is the note's place in the combined visible list (the IDs stay while the
  numbers follow the order); `default` is true for model notes; `rows` are
  `document_note_rows`' (`1  BREAK ALL SHARP EDGES`, wrapped
  at 48 characters as Python's `textwrap` wraps them, never splitting a word, continuation rows
  indented three spaces); `placed` is each row's ink centre and the note's cells. The block
  (`notes_block`: the `GENERAL NOTES` heading, its ink centre and the table's box) is drawn
  ruled, one row of 3 mm text and 2 mm padding above and below for the heading and for each
  note row, in IBM Plex Mono.
- **Placement.** The block's measured size is reserved when the sheet is chosen, top right
  (draftwright's `layout_required_tables`), so a long list moves the sheet or scale on; then it
  is placed after the dimensions by `fit_box` towards the top right, with 6 mm of breathing room
  and then at the 2 mm text padding, clear of the views' areas (their bands, where the
  dimension lanes run), the isometric's drawn box, every dimension's ink and the title block. A
  block no layout at a standard scale can hold is not reserved, so it never shrinks the views;
  a block that then has no place is a `notes_not_placed` error naming every note, which stays
  in the plan undrawn.
- **Render.** Each row is drawn as glyphs and is one invisible text run of the row as the plan
  states it (continuation rows without their indent), so the notes can be searched and copied
  from the PDF and the SVG; `⌀` is drawn as `ø`, as draftwright's `_font_safe_text` does.
- **Edits.** `{"op": "add", "path": "notes", "value": {"text": "DO NOT SCALE"}}` appends a note; one
  given no `id` takes the next free `n:<k>`. A note is changed or removed by setting `notes`.
- **Differences from draftwright**, with verdicts (`tests/notes.rs` compares the block with
  draftwright's on m10127 and ctc01, `tests/fixtures/notes.json` from `tools/capture_notes.py`):
  the rows, their numbering and wrapping, the block's height and its top-right corner are
  draftwright's; a continuation row keeps its indent, where draftwright's left alignment on each
  row's ink drops it, so the block is wider by the indent (rust-correct: draftwright writes the
  indent into the row and loses it in the drawing); the block keeps clear of the views' bands as
  well as the drawn ink (equivalent on the parts compared: both land in the corner the sheet
  reserved); a block no sheet can hold at a standard scale is not reserved (draftwright would let
  it drive the sheet to a computed scale; undetermined, a question in `docs/next-session.md`);
  the text layer keeps `⌀` where draftwright's has `ø` (rust-correct, as for callouts). The
  per-feature `note` is drawn as a leadered label since K1b ("Finish, threads, knurls and
  notes authored in the Spec", below); hole tables are not ported here.

### External spur gears

`gears` is an ordered list of authored manufacturing requirements (ISO 21771-1:2024,
ISO 21771-2:2025, ISO 53:1998, ISO 54:1996 and ISO 1328-1:2013):

```json
{"gears": [{
  "on": "f:boss:1",
  "frame": {"origin": [0, 0, 0], "axis": "z"},
  "tooth_count": 13, "module": 1.25, "pressure_angle": 20,
  "profile_shift": 0, "face_width": 10,
  "tooth_thickness": 1.9634954084936207,
  "tooth_thickness_tolerance": [-0.03, 0.01],
  "flank_tolerance_class": 7
}]}
```

Every field is required. `on` anchors ownership to a cylindrical `boss`,
`cylindrical_boss` or `turned_step` feature, a `face:<n>`, or a named stock face
(`top`, `bottom`, `left`, `right`, `front`, `back`). Use a face anchor for a toothed
wheel. `frame` explicitly authors the gear centre and principal axis in the part's
coordinates, in mm; axis letters accept either case. The axial span is the centre
coordinate on that axis ± half the face width. Anchors on another revision are
refused until reattached.

Validation follows Python's `ExternalSpurGearFeature`: finite origin; integer tooth
count 5–1000; positive finite module, face width and tooth thickness; module
0.5–70 mm; width 4–1200 mm; reference diameter `module × tooth_count` 5–15000 mm;
finite pressure angle strictly between 0 and 90 degrees; finite profile shift;
two finite tooth-thickness deviations, lower ≤ upper, retaining a strictly positive
lower thickness limit; integer flank tolerance class 1–11. Booleans are refused
as numeric values. Deviations are lower then upper, in mm.

A declaration creates `gear_data_1`, `gear_data_2`, … table items. Each prints the
header and Python's nine fixed ISO rows with exact authored float formatting,
units, and signed deviations. These tables reserve space through the sheet rules
and are placed towards the top right as late furniture, clear of dimensions,
notes, captions and earlier tables. They carry rendered provenance to `gear:1`,
`gear:2`, …; they are never recognised from geometry. Edit the whole `gears` list
with `set`, or append a declaration with `add` on `gears`; the MCP Spec schemas
expose the same list, like `notes` and `tables`.

Lint reads declarations lowered into the Analysis's independent requirement
inventory and the actual rendered table text, without reading the embedded Spec.
It reports missing, ambiguous or stale tables. Full-wire repeating-profile evidence
from native or Python recognition is matched by centre and axial span (1e-6 mm),
then audited for axis and repeat count. Missing, displaced or ambiguous evidence
is `gear_correspondence_unverifiable`; an undeclared repeating profile is
`gear_semantics_missing`. Arc counts alone never prove correspondence. A smooth
blank can carry a declaration and table but remains unverifiable, as in Python.
Python imposes no derived tip-diameter or face-width agreement with a blank;
the port adds no such refusal. `tests/gears.rs` covers the blank, real wheel,
validation, export and all reconciliation outcomes.

### Tables

`src/tables.rs` reads the spec's `tables`, a list of `{id, rows, prefer?}` placed in order, and
draws each as draftwright's `Sheet.table` draws a declared corner-block table (revision blocks,
BOMs, schedules) through `Drawing.add_table` and `_build_table`. The pieces the notes block
shares with a table (text height, padding, rows, font-safe ink, what a block keeps clear of,
placement and rules) live there too; the notes block is draftwright's single-column table.

- **Refused** (by `plan` and `edit`): an ID not of the form `t:<k>` or two tables with one ID
  (with the next free ID as the choice); and, as `Sheet.table` refuses them, a table with no
  rows, a row that is a string (pointing to `notes`), rows not all of one non-zero length; and,
  beyond draftwright, a row that is not a list, a cell that is neither text nor a number (`null`,
  `true`, a list), and a `prefer` other than `tr`, `tl`, `br`, `bl` (with those as the choices).
  A number cell is printed as JSON writes it (`4`, `0.5`). An absent or empty list is not
  serialised.
- **Plan items** (`tables`): `{id, rows, default: false, basis, placed}`, `rows` the cells as
  text, row 0 the header; `placed` is each cell's ink centre (none for an empty cell, which
  draws nothing), each column's left edge and the right edge, and the table's box. Each column
  is as wide as its widest cell's ink plus 2 mm each side, at least 7.5 mm (`_table_metrics`);
  each row 7 mm (3 mm IBM Plex Mono and 2 mm padding above and below); each cell's ink centred
  in its cell; ruled at every column edge and at the top and bottom of every row.
- **Placement.** Each table's measured size is reserved towards its corner when the sheet is
  chosen, ahead of the notes block, as draftwright's `layout_required_tables` orders them (a
  reservation that only a fallback would make room for is dropped, with the notes', so it never
  shrinks the views). Each is then placed after the dimensions and the notes block, in the
  spec's order, by `fit_box` towards its `prefer` corner (default `tr`; the corner ranks the
  places, it does not exclude the others), with 6 mm of breathing room and then at the 2 mm
  padding, clear of the views' areas, the isometric's drawn box, every dimension's ink, the
  title block, the notes block and the tables before it. A table with no place is a
  `table_not_placed` error naming it (draftwright's `table_dropped`), and stays in the plan
  undrawn.
- **Render.** Each non-empty cell is drawn as glyphs and is one invisible text run of the cell
  (trimmed) in the PDF and the SVG text layer; `⌀` is drawn as `ø`.
- **Edits.** `{"op": "add", "path": "tables", "value": {"rows": [["REV", "DESCRIPTION"], ["A",
  "INITIAL RELEASE"]]}}` appends a table; one given no `id` takes the next free `t:<k>`.
- **Differences from draftwright**, with verdicts (`tests/tables.rs` compares a four-column
  revision block towards `tr`, with an empty cell, and a four-column BOM towards `bl` on m10127
  and ctc01, `tests/fixtures/tables.json` from `tools/capture_tables.py`, which declares them as
  `Sheet.build` does): rows, height, every column's width and every cell's ink centre agree to
  1e-4 mm on both parts, and an empty cell draws nothing in both; on ctc01, where the port
  chooses draftwright's sheet with and without the tables, both tables sit flush in the same
  corners; on m10127 the port's sheet already differs without them (A4 1:1 against A3 2:1,
  `tests/fixtures/known_views.json`), and the BOM's bottom-left corner is taken by the front
  view's area, so it goes to the next free place (not comparable). An unknown `prefer` is
  refused, where `fit_box` reads anything but `br`, `tl`, `tr` as `bl` (rust-correct: a typo
  would silently move the table); a non-list row and a cell that is not text or a number are
  refused, where Python's `str()` would print `None`, `True` or a list's repr (rust-correct);
  `table_not_placed` is an error, where `table_dropped` is a warning (rust-correct:
  draftwright's own `build_policy._is_required_scale_drop` treats a sourced `table_dropped`,
  which every declared table is, as a lost requirement); each cell's text run is centred on its
  ink, where draftwright's PDF overlay starts every cell's run at the column's left padding
  although the ink is centred (rust-correct: the run lies over the glyphs it stands for); the
  views' bands are kept clear as well as their ink (as for notes; equivalent where compared).
  Not ported: `block_cols` (side-by-side blocks, which only the hole table uses through
  `_wrap_rows`), `left_align_cols`, a table's `name`, and `Sheet.schedule`'s measured tables.

### Feature control frames and datum feature symbols (PMI)

`src/pmi_lowering.rs` lowers the Analysis's PMI as draftwright's `build_pmi_features` does, and
`src/dims/gdt.rs` places and draws it. Plan items (`gdt`): `{id: "pmi:<identity>", kind:
"control_frame" | "datum_feature" | "label" | "surface_finish", view, side, source, faces, cells,
modifiers?, default, basis, placed}` (`surface_finish` added by F2, draftwright-rust #18).

- **A feature control frame** for each geometric tolerance read without blockers: its
  characteristic, magnitude (`_fmt_pmi_magnitude`), the zone's `⌀` or `S⌀`, `Ⓜ` or `Ⓛ`, the
  datum letters (repeats removed) and the all-around or all-over circle at its leader's bend. It is
  drawn in the view that sees its axis end-on (its faces' common cylinder axis, else their box's
  longest extent, else z: x → side, y → front, z → plan), below the front and the side, above
  the plan, its leader from the centre of its faces' box. Only the modifiers draftwright draws
  are drawn (all-around, all-over, the zone, the material condition); any other is a
  `pmi_not_lowered` error.
- **A datum feature symbol** for every declared datum in the STEP or Spec that a control frame
  cites or that dimensions measure from (the planar datum selected as each axis's origin, for
  example Specify ± mode measurement origins). Any other declared datum is not drawn by default:
  it stays in `plan().gdt` with `state: {"kind": "not_drawable", "reason_code":
  "datum_not_drawn"}` and is reported as `datum_not_drawn` (info; maintainer 2026-10-25,
  revising #141). Set `sheet/uncited_datums: true` to draw every declared datum, or `false` to
  hide every datum no frame cites, measurement origins included, as the user's omission.
  Set `annotations/<id>/omit: true` to hide any individual datum, using its stable ID from
  `plan().gdt`. Hidden symbols remain there with `state: {"kind": "omitted_by_user"}`, source, faces,
  and an explanatory basis. `pmi().unresolved` explains the omission or attachment/placement
  failure until the symbol is drawn; it never returns an empty reason. Rendered drawings
  retain explicit omission provenance, and coverage reports `pmi_datum_suppressed` (info)
  without counting the hidden symbol as drawn.
  Each datum whose faces are all planes, or all cylinders, on one
  principal plane or axis: in the view that sees it edge-on (x and z: front; y: side; a y-normal
  plane goes to the plan when the side view is not drawn), on the side its outward normal points
  to, from a proven site on its face. A cylinder may use either silhouette proved against
  the trimmed face; a plane may use a drawn extension of its edge-on face beyond either end
  of the view outline, with the triangle standing on that extension. Drawn as ISO 5459 draws it:
  the filled triangle on the surface, a line along the normal, the letter in a square frame.
- **A surface texture symbol** (`surface_finish`) for each face's AP242 surface texture (ISO
  10303-1110, which draftwright does not read): attached as a datum's symbol is (its faces all
  planes or all cylinders on one principal plane or axis, from a proven site on the face's edge
  line in the view that sees it edge-on, on its outward side), drawn as ISO 1302's symbol
  standing on its leader's shelf, the leader's arrowhead on the edge line, the material removal
  condition's bar or circle, and its parameters under the bar, one per cell and line (`Ra 0.8`,
  µm). A face no principal view shows edge-on (a cone, a tilted plane) is `pmi_not_lowered`; one
  whose view is not drawn is `pmi_not_drawn`. A plane keeps its outward strip. A cylinder
  starts in the front view's strip that carries no dimension lanes (left of a z axis, above an x
  axis) when a site proves it, tries compact tiers between its face and the first lane, and
  takes the other silhouette only when its own strip has no place clear of every label and
  line. A cylinder with no proven silhouette (one trimmed by flats) is reached head-on: proven
  points inside its outline on the half facing that view, with nothing in front, midway between
  the axis and either side, the leader ending in a dot there (ISO 128-22), sites clear of the
  view's edges and silhouettes first. The symbol's box starts 0.9 mm past the leader's bend.
  A leader or shelf run alongside a dimension or extension line closer than one text height (3 mm)
  grades as a line through a label, so the next tier or site wins; in a strip, the finish with
  fewest proven sites is placed first.
  `annotations/<id>/place/side` may name either strip a cylinder proves a site in.
- **Placement**: automatic datums first try inside tiers between their face and the first
  dimension lane, with normal triangle, shaft, frame and lane clearances. All inside sites
  are searched before the existing tiers beyond the dimensions. Cylinder alternatives use
  either proven silhouette; planar alternatives draw an extension outside either end of the
  view, with a 1 mm outline gap and overrun past the triangle. Sites nearest the original win
  equal grades. Frames and labels also retry the other strips. Every glyph's obstacle grade
  includes the count of crossings with dimension and extension lines, arrow shafts and other
  leaders, using the collision reporter's predicate (including collinear travel). Grade zero
  requires zero crossings; equal clearance classes prefer fewer crossings. The original
  complete arrangement remains a candidate. A replacement cannot introduce an annotation
  contact with a new unordered pair of subjects (reporting direction does not affect the
  comparison), so a greedy move cannot crowd later frames into overlaps. If the full
  rearrangement fails this guard, datum changes are tried alone. Unavoidable
  collisions stay visible and are reported. Notes and tables keep clear of the frames.
  Newly drawn uncited datums with remaining leader crossings also try the same placer with
  exact clearance grading in narrow shoulder gaps that the conservative first-stroke band
  rejects. This reuses the existing attachment sites, tiers and ordering.
  Override the automatic attachment/view/tier with `annotations/<id>/place`; explicit
  coordinates retain their strip/tier behavior and pins keep their resolved geometry.
  An empty `place: {}` reset restores automatic placement, including inside datum tiers.
- **Render**: strokes for the frame, the symbols and rings, the font's glyphs for letters and
  numbers (`⌀` drawn as a ring and a stroke, `S⌀` as `Sø`), and one invisible text run per
  compartment (`⌖`, `⌀0.05Ⓜ`, `A`), so the PDF and SVG text layers can be searched; lint reads
  them as annotations that claim no measurement.
- **Reported, not drawn**: a record whose reading left blockers or whose faces prove no
  attachment, or whose faces lie in several placements of its part (`pmi_not_lowered`, error); a
  source not read (`pmi_not_extracted`: error for a requirement, info for a shape aspect or
  property); a file whose PMI cannot be read (`pmi_not_read`); presentation
  (`pmi_presentation_not_read`, info); an item whose view is not drawn (`pmi_not_drawn`). PMI
  dimensions are the next section's. draftwright draws PMI only
  with `pmi="annotate"`; the port draws it whenever the part has some.

Compared item by item with draftwright on 14 AP242 files (`tests/pmi_lowering.rs`,
`tests/fixtures/known_pmi_lowering.json`). Datum placement's
[base/head survey and render evidence](datum-placement.md) records the corpus/fixture
crossing and overlap counts and the spindle's inside-lane and face-extension placements.

### Datums and controls authored in the Spec

A part whose STEP carries no AP242 PMI can still carry datums and feature control frames
(`src/spec_gdt.rs`, draftwright-rust #43 item 1; Python draftwright's `Sheet.datum` and
`Sheet.control` are the reference):

```json
"faces":    { "bottom": { "datum": "A" } },
"features": { "f:hole_pattern:1": { "controls": [
  { "characteristic": "position", "tolerance": 0.1, "datums": ["A"] } ] } }
```

- **Targets.** `features/<id>` names a recognised feature or hole pattern by its ID, as
  `features/<id>/fit` does; a datum or control on a feature with cylinders applies to its
  cylinders (its axis, as draftwright's `gdt_target` takes a feature's), else to all its faces.
  `faces/<face>` names a face by the side of the part's box it lies on, facing out (`bottom`,
  `top`, `left`, `right`, `front`, `back`: -z, +z, -x, +x, -y, +y; every planar face lying
  there), or by its index, `face:<n>`, as the plan's problems name faces.
- **`datum`**: a datum letter, one to three capitals (ISO 5459).
- **`controls`**: a list of `{characteristic, tolerance, datums?, diameter?, modifier?}`: one of
  draftwright's 14 characteristics (`straightness`, `flatness`, `circularity`, `cylindricity`,
  `profile_line`, `profile_surface`, `angularity`, `perpendicularity`, `parallelism`,
  `position`, `concentricity`, `symmetry`, `circular_runout`, `total_runout`), a positive
  magnitude in mm, the datum letters in precedence order (none on a form tolerance), a `⌀` zone
  (the default for position and concentricity; accepted on straightness and the orientation
  characteristics too, refused on the others) and `M` or `L`. `add` appends a control.
- **Lowering.** Each is lowered into the same record (`pmi::Record`) an AP242 datum or geometric
  tolerance on those faces is read into, its attachment measured as `src/pmi.rs` measures it,
  from the Analysis's `face_attachments` (below), and merged into the Analysis's PMI reading
  (`spec_gdt::effective`, once per plan, and again by `api::review` for lint, which reads no spec).
  Lowering, placement, band reservation, drawing and lint then treat it as the file's own: its
  item is `pmi:<identity>`, `annotations/<id>` places or omits it, its `source` is
  `spec:<path>` (`spec:faces/bottom/datum`), its basis says `spec-authored` and it is not a
  default.
- **The Spec wins.** Where the file's PMI states the same (a datum of the same letter; a
  tolerance of the same characteristic on the same faces), the file's record is not drawn and a
  `spec_overrides_pmi` warning names both (`refs`: the AP242 source, then the spec's). The same
  requirement on the same faces keeps its item ID, so its placement carries over.
- **Reported:** a control referring to a letter neither the spec nor the file declares is drawn
  with a `datum_undeclared` warning (draftwright's `_validate_datums`); an attachment the faces
  do not prove is `pmi_not_lowered`, as for the file's records. **Refused** (`invalid`): an
  unknown feature (with the nearest IDs) or face, a characteristic, letter or modifier not listed
  (`P`, a projected zone, is not drawn for AP242 PMI either), a tolerance not positive, one letter
  on two targets, two controls of one characteristic on the same faces, and an Analysis without
  face attachments (analyse again). Across a revision, the entries are inactive like the other
  feature-keyed entries.

The Analysis carries `face_attachments` (added to version 7 as an optional field): per face,
its box, its surface (`plane` or `cylinder`), principal axis and support, its proven sites
(first, then up to eight spread over it), a plane's outward normal and a cylinder's exact
principal axis: what `pmi::datum_geometry` and `tolerance_geometry` read of the part, which they
now read through the same record for the file's own PMI. `tests/spec_gdt.rs`.

### Finish, threads, knurls and notes authored in the Spec

The rest of what a part without PMI may need stated (`src/spec_notes.rs`, draftwright-rust #43
item 1, stream K1b; Python draftwright's `Sheet.finish`, `thread`, `knurl`, `note` and
`default_surface_finish` are the reference) is authored the way datums and controls are (above):

```json
"sheet":    { "default_finish": { "ra": 3.2 }, "title": { "tolerance": "ISO 2768-m" } },
"faces":    { "top": { "finish": { "ra": 0.8, "removal": "required" } } },
"features": { "f:hole_pattern:1": { "thread": { "designation": "M6" } },
              "f:turned_step:1":  { "thread": { "designation": "M10", "length": 15 } },
              "f:turned_step:3":  { "knurl": { "pattern": "diamond", "pitch": 0.8 } },
              "f:hole:1":         { "note": "DRILL AND TAP AFTER HARDENING" } },
"notes":    [ { "id": "n:1", "text": "BREAK ALL SHARP EDGES" } ]
```

- **`finish`** (`features/<id>`, `faces/<face>`): `{ra, removal?}`, Ra in µm, `removal` one of
  `required` (ISO 1302's bar), `prohibited` (its circle) or `any` (the default). Lowered into an
  AP242 face surface texture's record (`surface_finish`, on the feature's cylinders, else its
  faces) and drawn as F2 draws one: the symbol on a leader from the face's edge in the view that
  sees it edge-on, or, for a cylinder with no proven silhouette, a dot-terminated leader from
  inside its outline (placed as the file's own, above). Faces no principal view shows edge-on,
  or several cylinders on different axes (a hole pattern's), are `pmi_not_lowered`, as for the
  file's own.
- **`sheet/default_finish`**: the same, the part's default finish: the record of an AP242
  surface texture on the part (`Ra <v> µm unless otherwise specified`), drawn beside the title
  block (`requirements`, `carrier: sheet`).
- **General tolerance**: `sheet/title/tolerance`, as before (it fills the title block's cell and
  wins over the file's, `step_general_tolerance_disagreement`). No new path.
- **`thread`** (`features/<id>`): `{designation, length?}`, an ISO metric designation
  (`M<size>[x<pitch>][-<class>][ LH|RH]`, printed as written; with no pitch, ISO 261's coarse
  pitch is used for the checks) and a minimum full-thread length in mm. On a hole the callouts
  state on its own, a hole pattern, or a turned step (or boss), it is lowered into an AP242
  thread's record (`internal_thread` on a hole or pattern, `external_thread` on a turned
  diameter) and printed on the owner's callout by the thread path: after a hole's terms
  (`⌀4.2 ↧ 9.2 M5x0.8-6H; 8 MIN FULL THREAD`), before a pattern's suffix (`4× ⌀6 THRU M6 (2×2)`,
  as draftwright folds a pattern member's thread), after a turned diameter
  (`ø10 M10; 15 MIN FULL THREAD`); the callout claims it. Reported (`pmi_not_lowered`, error):
  a member of a pattern the callouts state as one (set it on the pattern), a feature no callout
  states on its own, a hole larger than the nominal size or smaller than nominal less 1.5
  pitches, a turned diameter that is not the nominal size, a length past a blind hole's depth or
  a step's length (ISSUE-1360-a), a boss (as for AP242 threads).
- **`knurl`** (`features/<id>`): `{pattern, pitch}` (`straight` or `diamond`, mm), on a turned
  step, lowered into an AP242 knurl's record and printed after its diameter
  (`ø24 DIAMOND KNURL P0.8`; on the overall diameter when the step's is shared with it).
- **`note`** (`features/<id>`, `faces/<face>`): one line of text on a leader, lowered into a
  surface label's record (kind `note`, its leader from each face's first proven site) and drawn
  as an AP242 label is (`gdt` kind `label`). It claims no measurement, so it never silences
  coverage (ISSUE-1351-a). A note on the sheet is a general note (`notes`, above).
- **Lowered and merged** with the datums and controls (`spec_gdt::effective`): item IDs are the
  records' identities (`pmi:manufacturing_requirement.surface_finish:f2`,
  `pmi:surface_label.note:f3:t=…`), sources `spec:<path>`, bases `spec-authored`, never defaults;
  lint judges the drawing against the merged reading.
- **The Spec wins** with `spec_overrides_pmi` (warning) over the file's finish on any of the same
  faces, its default finish, and its thread (of either kind) or knurl on any of the same faces.
  Messages name Specify when that requirement carries its exact per-item authorship marker;
  unmarked requirements are attributed only to the STEP file, without claiming who wrote them.
  A compatible Spec designation refines a Specify-written thread (`spec_refines_pmi`, info,
  advisory), retaining its full-thread length unless an explicit Spec length replaces it.
  Messages contain neither Spec paths nor entity IDs; provenance stays in `refs`.
- Finishes and other manufacturing annotations do not activate dimensional station coverage.
  Dimensional PMI, datums, geometric controls and explicit origins retain that audit.
- **Refused** (`invalid`): an unknown feature or face, Ra not positive, an unknown removal or
  knurl pattern, a designation that is not ISO metric or states no pitch where ISO 261 has
  none, a length or pitch not positive, an empty or multi-line note, a thread on a feature of
  no threadable kind and a knurl on one that is not turned. `tests/spec_notes.rs`.

### Toleranced and basic dimensions (PMI)

`src/dims/pmi_dimensions.rs` lowers each AP242 dimension record as draftwright's
`lower_ap242_*_tolerances`/`_nominal_*` and `render_pmi` do, from where its faces put it (the
Analysis's `geometry` on a dimension: a linear or thickness dimension's two proven `stations` and
their axis, its one witness `view`; a diameter's or radius's exact `cylinders`, a radius's site on
its arc; `src/pmi_lowering.rs`'s `dimension_geometry`, draftwright's `_proved_planar_linear` and
`_cylindrical_references`). **The rule** (draftwright's): a requirement on a quantity a recognised
feature's dimension already states annotates that dimension, which states it once: a bore whose
cylinders are a hole callout's holes (axis lines, axis, diameter) gives the callout its band
(`6× ⌀10 +0.09 0 THRU`, a zero deviation unsigned; symmetric limits as `±`, asymmetric ones as limits, a band wholly above
or below the nominal with its signs), and a callout whose holes carry different requirements is
split by requirement (a bolt circle is not: reported); an external cylinder gives its band to the
turned step, boss or turned part whose diameter it is; a plain diameter or step length is stated
by its owner. Otherwise it **stands beside** them, an ordinary planned dimension `d:pmi:<identity>/
<kind>[.basic]` (a length along x, y or z in draftwright's view; a diameter or radius by a
leader where it is seen end-on), its label the record's, a basic one boxed (its label box widened
to take the frame; a label authored in inches is printed in mm, its exact conversion at its authored precision, #214). Every item that states a PMI dimension claims it (a measurement of feature
`pmi:<identity>`, its record's item ID); a spec's own tolerance or fit on that size (a hole's or a boss's) wins
(`pmi_not_lowered`; when the spec's is withheld, the label states neither and the report says
so), and an omitted item's PMI is `pmi_not_drawn`. Not drawn, with the reason: reading blockers, faces in
several placements, faces that prove no witness (`pmi_not_lowered`); angles, oblique lengths,
non-principal cylinders (`pmi_dimensions_not_ported`). An ISO 286 class stated without limits is
printed with its limits from draftwright's table on the item that owns its size: a hole class
on proven internal cylinders on the one bore callout they match, a shaft class (`g6`) on proven
external cylinders on the one turned diameter they match (`⌀15 g6 (-0.006/-0.017)`, #30); with
no unique owner, or a class of the other kind for the cylinder, it stays refused. The open
question of supplemental geometry stays refused. Compared record by record on the 14 AP242 files
(`tests/pmi_dimensions.rs`, `tests/fixtures/known_pmi_dimensions.json`).

**Missing PMI nominals:** a dimension without a `dimensional_characteristic_representation`
may use its measured nominal when its kind and native targets prove one value. The existing
parallel-plane, finite-cylinder and angular attachment proofs apply; a box diagonal or differing
cylinder sizes cannot supply a value. A stated nominal still has to agree with the geometry.
The record carries `nominal source: geometry (no stated value)` and retains its PMI identity,
including its originally empty label discriminator where another requirement shares its faces.
The drawing uses the ordinary formatter and only explicitly stated tolerances. One
`pmi_read_fact` information finding per recovered record names the absent representation and
measured value (severity info, advisory impact, grouped by source kind). Reader facts, including
projected zones whose end has no geometry, are also information findings and never block lowering. Matching native
sizes and already toleranced PMI callouts retain a single carrier with all source claims.
Other reader or geometry blockers remain refusals.

**PMI coverage lint** (`src/lint/pmi_coverage.rs`, draftwright's `linting/pmi_coverage.py`): lint
checks the finished drawing against the Analysis's PMI, never the Plan. A record is drawn when an
annotation is named `pmi:<identity>` (a frame, symbol, label or sheet requirement) or claims a
measurement of feature `pmi:<identity>`, each read back to its record's source through the
Analysis's identities (a dimension, hole callout or turned size stating a PMI
dimension); otherwise it is `pmi_not_lowered` or `pmi_not_rendered`. A record lowers when
`pmi_lowering::lower` (a function of the Analysis alone) lowers it, a frame, symbol or label, when
`pmi_lowering::sheet_requirements` lowers it to the sheet (a general tolerance, default finish or
document note), or when it is a dimension of a kind stage 2b draws (a length along x, y or z, a
diameter or radius seen end-on) whose reading and faces prove it, or when it is a thread or knurl
`pmi_lowering::manufacturing` gives to its owner (whose callout then claims it); a face finish or
angle does not yet. Not lowered, it is `pmi_not_lowered` with the reason; lowered and not drawn,
`pmi_not_rendered` (reported even when the plan already says why, as lint cannot read it: an open
question). A source not read, or read in part, is `pmi_not_extracted` (error; info for a source of
no draftwright category); a refused reading is `pmi_not_extracted` on the part. A drawn frame
whose text runs do not state its source's magnitude, zone and datum letters is
`pmi_value_mismatch`; an annotation claiming a source the reading does not hold is
`pmi_source_unknown`, and one claiming a source on a part with no reading `pmi_unreconciled`
(warning). The parts' materials (`pmi::Report::materials`) give `step_material_ambiguous`
(several) and `step_material_unavailable` (one part's of several). Compared with draftwright's
lint on the 14 AP242 files, each drawn as the command line draws it (Rust quiddity's features, the
default spec) (`tests/pmi_coverage.rs`, `tests/fixtures/known_pmi_coverage.json`).

### PMI labels and drawing-wide requirements

A **surface or common label** (text on edges or faces: `A`, `F1.1`, `2X R`) is a `gdt` item of
kind `label`, its one cell the text, hung from a leader like a frame (draftwright's `Note`): from
its first proven site (`geometry.sites`: a surface label's edge at its parameter midpoint, a
common label's face at the vertex nearest the face's box centre, `pmi_lowering::label_geometry`)
in the front view, or the side when its references' box is longest along y, above or below by
the site against the part's centre; with no site it is `pmi_not_lowered`. The **drawing-wide
requirements** (`pmi_lowering::sheet_requirements`, draftwright's
`lower_ap242_document_requirements`) are the plan's `requirements`: `{id: "pmi:<identity>", kind,
source, text, carrier, basis, placed?}`. The part's one general tolerance fills the title
block's tolerance cell (`carrier: title_block`) unless the spec states one, which wins (a
different PMI one is `step_general_tolerance_disagreement`, a warning); its one `Ra <v> µm unless
otherwise specified` is ISO 1302's symbol at the left end of the band above the title block
(`sheet`, its collisions reported as a dimension's), as is an AP242 surface texture on the part
itself (F2: read as that default, its material removal condition drawn as ISO 1302's bar or
circle, `placed.arcs` holding the circle; the field is absent otherwise). Part-level edge
conditions, general notes, surface treatments and heat treatments become general notes
`pmi:<identity>` before the Spec notes (`notes`), in model order, with omission and duplicate
reconciliation as described under General notes. Two of one default are ambiguous and
neither is drawn (`pmi_not_lowered`). Each is an annotation of the drawing by its ID, over the
runs that state it. A turned part keeps a view a PMI item is drawn in (draftwright's
`annotation_view_required`). Compared with draftwright's drawings by `tests/pmi_drawings.rs`.

### Threads and knurls (PMI)

An AP242 thread or knurl is no item of its own (`pmi_lowering::manufacturing`, draftwright's
`lower_ap242_manufacturing_requirements` and `manufacturing_schedule`): read from its words and its
structured statement, reconciled, its one cylinder the size its text states, it is given to the one
hole (blind to the drill depth, or through) or turned step that cylinder proves, and that
feature's callout prints its suffix after the size (`⌀1.6 ↧ 4 M2 x 0.4-6H RH; 2.5 MIN FULL
THREAD`; a through tap's `THRU` stands for the bore's) and claims it (feature `pmi:<identity>`,
parameter `thread` or `knurl`). With two or more owned and one suffix over 52 characters, each
callout prints `SEE MFG n` instead and the plan's table `manufacturing_requirements` (`default:
true`, `REF | MANUFACTURING REQUIREMENT`, the text wrapped at 44 characters, the second column
left-aligned) states them in their source IDs' order, reserved when the sheet is chosen and placed
before the notes; with no place on the finished sheet the table is not used, as draftwright's
fallback: the drawing is planned again with the owners in full wording, no `SEE MFG n`, and an
info `manufacturing_table_fallback` naming them (`model::manufacturing_fallback`). A threaded
owner keeps a callout of its own. No owner, several owners,
or text that disagrees with the cylinder is `pmi_not_lowered` (error) with draftwright's reason; a
requirement a boss owns is refused too, as the boss callout prints none yet
(`tests/pmi_thread_knurl.rs`).

A semantic AP242 `thread` (haecceity's, with its 'thread manufacturing' attribute set: pitch,
and a thread length for an external one) is read as draftwright's structured `internal_thread` or
`external_thread`; a `turned_knurl` as a structured `knurl`. Beyond draftwright (#23), an external
thread whose callout prints it (not `SEE MFG n`) prints its designation in place of the plain
diameter (`M10 x 1.5-6g RH, FULL AVAILABLE LENGTH`, not `ø10 M10 x …`), and a turned step's
external thread or knurl is drawn in its profile views (not section views) on the SVG/PDF/DXF
layer `thin` (thin continuous, 0.25 mm; only a drawing that has such lines has the layer): ISO
6410's thin line each side of the axis at the minor diameter d3 = d − 1.22687·P over the thread
length, and a knurl's band over the middle half of its length (lines along the axis for a
straight knurl, crossed at 30° for a diamond one, the pitch apart on paper but at least 3 mm).
The thread's limit is the step's own end (ownership requires the stated length to be the step's
cylinder or span), so the shoulder edge is its limit line and the step length dimensions it. The
plan is unchanged: these lines are render furniture, not plan items. In the session's SVG
fragments they travel in their view's fragment (layer `thin`), not in `furniture`, so they move
with the view. A turned step's diameter callout is led to the silhouette at 15° or more to it: a
leader along the silhouette would lie on the part's edge (ISO 128-22), so the placer counts one as
a hard blocker.

**IDs and views.** A dimension's ID names what it measures, never the view that carries it
(`draftwright.spec/2`; version 1 added `@<view>`). The view is the item's `view`. Omitting that
view moves the dimension to its next view under the same ID (the basis says so), and so does a
change of sheet or scale. The spec may choose the view, `dimensions/<id>/place/view` (the earlier
`dimensions/<id>/view` is still read) (`front`,
`plan` or `side`): it must be one that can carry the dimension (one of its views that does not
look along it), else the plan is refused with those that can; one the spec omits leaves the
dimension in its default view, with a warning (`dimension_view_not_shown`). The item then lists
`view` in `from_spec`. A version 2 spec keyed by a version 1 ID is refused, reading it as the ID
it now has (first among the choices). Every planner states each quantity once under one ID
(asserted in every build); a planner that drew one quantity in two views would give the second a
discriminator from what it states, as the height ladder's rungs are told apart by their heights
(`step_height.length.h12_001`), never from where it is drawn.

The spec chooses the source (ADR4-5): `"dimension_source": "auto"` (the default: the defaults
less `dimensions/<id>/omit`) or `"authored"` (exactly the IDs listed in `dimensions`, a complete
set). Omitted items are listed in the Plan's `omitted` with their basis. A correlated set, the
height ladder's rungs or the ladder's shoulders along one axis, is approved or omitted whole
(stream K6, ARCH0015-2): each member keeps its own ID, and the spec's decision on any member is
the set's. With `auto`, omitting one member omits the others too (`omitted_by_user`, the basis
naming the set); with `authored`, listing one member draws the others. Either way an `info`
problem `correlated_set_whole` names the members the decision spread to (with `auto`, its fix
removes the omission). An omission never overrides a member the spec decides itself (any entry
on it: a tolerance, a view, a place, `omit: false`): that set is drawn as the spec states it,
half omitted, with a `correlated_set_conflict` warning naming the omitted and the decided members,
its fix removing the omission. A turned profile's chain is not such a set. In an edit path, the
`/` inside a dimension's ID is written `~1`, as in a JSON pointer:
`{"op": "set", "path": "dimensions/d:envelope~1width.length/omit", "value": true}`.

### Dimensions authored from geometry (#76 item 1)

`dimensions/d:authored:<name>` adds a quantity independently of recognition. The name is
chosen by the author (letters, digits, `_` and `-`), remains its ID after moving it to another
view, and must not reuse an automatic dimension ID. For example (replace the abbreviated
fingerprints with complete keys from the Analysis):

```json
{
  "dimensions": {
    "d:authored:axis_offset": {
      "between": ["face:v1:<fingerprint>", "axis:face:v1:<fingerprint>"],
      "along": "x", "view": "front", "tolerance": "±0.1",
      "place": {"side": "below", "lane": 1, "along": "middle"}
    },
    "d:authored:diagonal": {
      "kind": "linear", "between": ["vertex:v1:<fingerprint>", "vertex:v1:<fingerprint>"],
      "along": "true", "view": "front"
    },
    "d:authored:seat_radius": {
      "kind": "radius", "between": ["face:v1:<fingerprint>"], "tolerance": "±0.05"
    },
    "d:authored:corner": {
      "kind": "angle", "between": ["edge:v1:<fingerprint>", "edge:v1:<fingerprint>"]
    }
  }
}
```

- `kind` is `linear` (default), `diameter`, `radius` or `angle`. `between` contains two
  references for linear/angle, one cylindrical or spherical face for diameter/radius.
  `along` is `x`, `y`, `z` or `true` (default), for linear dimensions only. It is distinct
  from `place.along`, which positions the label. No measured value or model/paper point is
  accepted in the Spec. Existing `tolerance`, `view`, `place`, omission and `precision`
  policies apply. Linear sizes and diameters also accept `fit` (ISO 286 class, without an
  explicit tolerance), class-only by default; radius and angle dimensions take explicit bands.
- **Discover references in `Analysis.edge_ink.references`.** This optional Analysis/7 field
  maps full `face:v1:<sha256>`, `edge:v1:<sha256>` and `vertex:v1:<sha256>` keys to lists of
  exact model-space records. A usable key has exactly one record. Plane, cylinder and sphere
  faces, straight B-rep edges and topological vertices are available. `axis:<key from Analysis.edge_ink.references>` (prefix the complete cylindrical face key
  with `axis:`, giving `axis:face:v1:<sha256>`, never `axis:face:face:v1:…`)
  selects a cylinder's axis; `axis:f:hole:3` selects the common cylinder axis of a recognized
  feature, refusing a feature with several axes. Prefer the face spelling when saving across
  revisions. Index-based `face:12`, `edge:12`, and page-coordinate references are not accepted.
  Older Analyses lacking evidence still plan their defaults; authoring requires analysing again.
  Each record adds `features` (owning Analysis feature IDs), `stock_faces` (incident indices
  in `Analysis.stock_faces`), `stock_sides` (their `bottom`/`top`/`left`/`right`/`front`/`back`
  names, using the same resolver as Spec face names), and `faces` (all incident B-rep face indices). Empty ownership
  lists mean that recognition assigned no such role; no role is guessed from coordinates.
  A face also lists `edges` and `vertices`. Each boundary edge gives its `index` in
  `Analysis.edge_ink.edges`, endpoint `vertices` as complete reference keys, and, for a
  straight edge, its copyable `reference` key. Curved boundary edges are included but have
  no straight-edge dimension reference. Face `vertices` is a sorted unique list of their
  endpoint keys. Closed edges include their seam vertex. Coincident records retain their
  separate memberships. These indices are discovery addresses for this Analysis only;
  authored dimensions always store full fingerprint keys. Metadata refreshes after native,
  bridge or authored recognition changes and is excluded from the fingerprint.
- **Fingerprint contract.** The `v1` spelling and its content are fixed: SHA-256 of canonical
  sorted-key JSON containing the exact analytic record and sorted face-boundary endpoint
  signatures, with numbers rounded to 1e-6 mm. Plane normals and cylinder axes have their
  first nonzero component positive; their origins are the nearest point to the model origin
  on the plane/axis. Edge ends and boundary surface sites are sorted; a boundaryless sphere
  uses its six cardinal sites in x−, x+, y−, y+, z−, z+ order. Face traversal indices are absent.
  Fingerprints therefore survive face/edge/vertex enumeration and edge reversal. They identify
  unchanged geometry, not a guessed successor after a resize or rigid move. Coincident
  topological entities remain a multi-record ambiguity and are refused. A future fingerprint
  algorithm must retain `v1` resolution. A missing key names the reference and tells the author
  to copy a current Analysis key; it never silently binds to another entity.
- **Measurement.** Linear references are supporting planes, straight supporting lines (including
  cylinder axes), or vertices. A principal direction requires a unique station: a plane normal
  to that direction, an edge/axis perpendicular to it, or a vertex. `true` measures the shortest
  separation of the supports. Intersecting supports have zero distance and are refused.
  Angular references are two nonparallel planar faces or two coplanar, nonparallel straight
  edges; as for PMI, the actual face stations or edge ends select the half-rays and their
  minor included sector (0–180 degrees). Obtuse included angles remain obtuse.
  True lengths and angular sectors need a principal view that shows them true; a requested
  incompatible view is refused with the compatible choices. Angular placement uses view,
  radial lane and along; a side does not choose another measured sector. A size leader can attach to any
  cylindrical/spherical face, including an oblique cylinder, using exact surface sites.
- **One quantity, one owner (K6).** A new dimension duplicating a drawn automatic owner is
  refused, naming that owner ID in the message and choices. Edit its tolerance/view/place
  instead. Coincident principal spans, identical true spans/angular sectors, and the radius
  or diameter of a face already sized by its feature owner are duplicates; equal numeric
  sizes alone are not. A radius and diameter on the same face are one size quantity.
  In `dimension_source: authored`, only listed automatic IDs are owners; unlisted automatic
  candidates remain withheld, so a drawing can use entirely geometry-authored dimensions.
  Omitted dimensions do not own quantities. A replacement retains the matched numeric
  roles for independent completeness lint. A wholly restated automatic candidate is `covered`
  by the authored ID, with the basis naming that owner; a compound candidate's THRU, count,
  depth and other unstated roles remain required and are never inferred from its diameter.
- **Same planning and lint.** Authored quantities enter the ordinary dimension inventory before
  view routing, tolerance formatting and band reservation. They use its lanes, arrows, angular
  arcs, leaders, collision findings and sheet-selection rules. `default` is false, the basis
  says `spec-authored`, and `from_spec` includes `between`, plus explicit `kind`, `along`,
  tolerance and placement fields. A drawing claim carries only reference identities and the
  requested quantity (`geometry:<reference>|<reference>`, `linear.x`/`linear.true`, `diameter`,
  `radius`, `angle`), not a trusted nominal. Lint remeasures these against the Analysis and
  checks the leading printed value and size symbol; bad ink or unresolved references report
  `authored_dimension_invalid` errors. Existing structural and feature-completeness lint still
  applies; authored dimensions never silence missing manufacturing requirements.
- **Editing.** Existing `items::Op::set` / `remove` and session preview, apply, undo and redo
  operate on the entry and its fields, with atomic refusal. Set the complete entry to create
  it, for example `{"op":"set","path":"dimensions/d:authored:corner","value":{...}}`;
  remove the entry to delete it. Session/MCP item answers expose its reference anchors,
  placement options and editable fields. Sheet options describe the creation forms. MCP
  schemas carry a compact top-level Spec outline. Call MCP `spec_schema {}` for the full
  draft-2020-12 Spec schema and its named `spec_read` shape; `item_options` supplies valid
  paths and choices. The 28 advertised schemas total 34,477 compact UTF-8 bytes; nested
  Spec fields do not grow this budget. Server preflight and model/geometry validation
  are unchanged. Browser bindings accept the same Spec and operations; their method
  signatures are unchanged.

## The five operations

These are one Rust core: pure functions over the documents, deterministic, with no state between
calls.

| Operation | In | Out |
| --- | --- | --- |
| `analyse` | STEP file | Analysis |
| `plan` | Analysis, Spec | Plan (with problems); rebinds first if the spec's binding differs |
| `edit` | Spec, ops | Spec, or a validation error |
| `render` | Plan, formats, dpi | SVG, DXF and PDF by default, and PNG at `dpi` (150) when asked, each with the spec embedded |
| `review` | Plan | a rendered image, the problems and lint's findings on the drawing, for a check |

**`edit` operations** are small and addressed by path:

```json
[ { "op": "set", "path": "features/f:hole:3/fit", "value": "H7" },
  { "op": "remove", "path": "dimensions/d:f:pocket:1~1depth/omit" },
  { "op": "add", "path": "notes", "value": { "text": "DEBURR" } } ]
```

`add` appends its value to the list at its path (only `notes` is a list).

Each operation is validated against the analysis:

- **Wrong value:** the error says what would be accepted, e.g. "Choose one of: H6, H7, H8 …".
- **Close but wrong:** "'h7' reads as 'H7'".
- **Unknown ID:** the error lists the nearest valid IDs.

A caller may equally replace the whole spec.

**Adapters.** All three call one module, `src/api.rs`: the operations typed, and `api::call`,
the same operations by name with JSON arguments. Batch requests carry their documents and name
files by path, so they need no session state; the Session additions are described below. Errors are `{"code": "invalid" | "failed",
"message", "choices"?}`.

`plan`, `render` and `review` take the part either as `step` (the STEP file's path) with
`features`? (Python quiddity's recognition JSON), which they analyse, or as `analysis`: the
Analysis document that `analyse` returned, carried inline as the spec is, so the caller need not
analyse again (typed: `api::plan_analysed`, `api::render_analysed`, `api::review_analysed`). The
output is byte-identical to analysing the same STEP with the same features.

- `plan` with `analysis` reads no file: `step` may be omitted, and plans any Analysis version
  `plan` reads (`draftwright.analysis/2` or `/3`).
- `render` and `review` still need the part's geometry, so they take `step` beside `analysis`
  and read it without recognition. They take only the version this build writes
  (`draftwright.analysis/3`), and refuse, as `invalid`, an Analysis whose binding's
  `source_sha256` or `face_count` is not the STEP file's, naming both digests: one part is never
  drawn with another's analysis. `plan` given both checks them the same way.
- `analysis` beside `features` is refused (the analysis already carries the features it was made
  with), and so is an Analysis that does not parse (unknown or missing fields, not an object).

- **CLI.** `draftwright analyse part.step`, `draftwright plan part.step --spec part.drawing.json`,
  `draftwright review …` and `draftwright render … -o out` all print JSON. `--analysis a.json`
  names an Analysis file in place of `--features` (`draftwright plan --analysis a.json` needs no
  STEP file). Exit code 2 means
  invalid input; exit code 1 means a write failed without that invocation replacing its destination.
  Earlier formats in the same export may already have been published.
- **JSON-lines service.** `draftwright serve` takes one request and gives one reply per line, in
  the shape of specify-core's `serve`: `{"id": 1, "op": "plan", "step": "part.step", "spec": {…}}`
  gives `{"id": 1, "ok": true, "result": …}` or `{"id": 1, "ok": false, "error": {…}}`, and
  `{"op": "hello"}` gives the protocol version and the operations. A malformed line is answered,
  and the service carries on.
- **MCP server.** `draftwright-mcp` speaks MCP over stdio (JSON-RPC 2.0, one message per line).
  Its tools map one-to-one onto the operations, each with an input schema and a description
  written for an LLM; `plan`, `render` and `review` take `analysis` as an object, and `plan`
  requires no `step`. Results are the documents as JSON text; `review` adds the drawing as SVG
  text. A refused request is a tool error (`isError`) carrying the message and the choices.

**File exports.** `api::write` creates unique sibling staging files exclusively and publishes
with atomic replacement per destination. Concurrent callers use last-writer-wins per file
(the last successful rename), and cleanup removes only their own staged copy. A multi-format
export is not transactional: concurrent callers can publish different formats, and a later
failure does not roll back formats already published. Single-sheet destination names are unchanged.

**The spec in each output** (invariant 8): in the SVG's metadata, in the PDF's document
information, and in the DXF as comments (group code 999) ahead of the first section. AutoCAD,
ezdxf and the `dxf` crate read past such comments; a re-save drops them. `draftwright spec`
reads it back from any of the three.

## IDs

| Item | Form | Stable across |
| --- | --- | --- |
| Feature | `f:<kind>:<n>`, derived from its faces | the same file |
| Dimension | `d:<feature>/<parameter>`; never the view, which is the item's `view` | the same file, whichever view carries it and whatever the sheet or scale; carried to revisions through its feature |
| Sheet | `sheet` | always |
| View | `v:<name>` | always |
| Detail view | `v:detail_a`–`v:detail_h` | its allocated identifier, including authored omissions; automatic presence depends on scale and readable source requirements |
| Note, table, section | `n:<k>`, `t:<k>`, `s:<letter>` | always (set in the spec) |
| PMI frame, symbol, label, drawing-wide requirement, thread or knurl (and the feature of a PMI dimension's claim) | `pmi:<identity>`: the record's semantic identity, below; never its Part 21 instance (that is the item's `source`, e.g. `geometric_tolerance:#123`) | the same requirements on the same faces: the intent written again (new Part 21 numbers) keeps it |
| Problem | `p:<code>:<subject>` | the same plan |

Parameter names follow the Python version's role vocabulary (`role.kind[.discriminator]`). A
name that matches more than one measurement is refused rather than guessed (Python ADR 4
invariant 6).

Unavailable dimension edits remain in the Spec with a `dimension_edit_unapplied`
warning whose `subjects` name the escaped edit path, even for an unknown ID; they
create no measurement. Stale annotation edits with `pmi:<identity>` or `n:<identity>`
likewise remain with `annotation_edit_unapplied`; an untyped or empty annotation
identity is refused as an unsafe reference. `setSpec` and `openSpec` apply the same
validation. `openSpec` additionally migrates legacy `sheet/preferred_printer` to
`sheet/preferred_max_size` and sets aside entries that validation still refuses.

**A PMI record's identity** (`pmi::identity`, `pmi::Report::identities`; the editor's
`gdt::editor_key`, upstreamed) is its category and kind (`.basic` for a basic dimension), then,
each tagged and left out when empty: its part in a file of several (`p<n>`), the drawing faces
(`f<i>,<j>`) and edges (`e<i>,<j>`) it applies to, its datum references (`ref=A,B,C`) and its
modifiers (`mod=…`), and a datum's letter or a label's text (`t=…`), every character but
letters, digits, `_`, `.` and `-` written `%XX`:
`pmi:geometric_tolerance.position:f12,13:ref=A,B:mod=diameter_zone`. No value enters it, so a
changed magnitude is the same item with another label. Records that share an identity (a
composite frame's two segments, NIST FTC-07's and FTC-10's) are each told apart by their label,
`:t=position%200.8`, a discriminator from what they state, never from their order; records whose
labels are the same too (NIST STC-09's two identical `0.03 ±0.01 INCH` records, FTC-10's three
`ø3` on no face) get no ID and are not drawn: `pmi_not_lowered`, naming the records they cannot
be told from (ADR R1 invariant 5). Problems about PMI records still name the record's source
(`p:pmi_not_lowered:<source>`), as the record is not an item.

**Spec versions.** `draftwright.spec/2` is the version this build writes. A
`draftwright.spec/1` spec is read through `model::migrate` (in `plan`, so every caller of the
library gets it; the plan's embedded spec is the migrated one): `d:<feature>/<parameter>@<view>`
becomes `d:<feature>/<parameter>` (the view was where the plan routed it, not a decision, so the
entry does not keep it as its `view`), and a PMI dimension's `pmi:<category>:#<n>` becomes its
record's identity, when the spec is bound to the analysis's file (a Part 21 instance names a
record only in its own file). Two keys that come to one are refused, with both as the
candidates; a key whose record has no identity, or is of another file, is kept for `plan` to
refuse with the nearest IDs. `edit` has no analysis and leaves a spec's version and keys as they
are. A version 1 spec plans as it did (`tests/identity.rs`, on fixtures the 0.1.0-alpha.1 build
planned).

## Rebinding: when the STEP changes

Feature IDs are stable only within one file. When the spec's `source_sha256` differs from the
analysis, `plan` first rebinds the spec using quiddity's `correspond(old, new)` (see
quiddity-rust's `docs/correspondence.md`). Every spec entry keyed to a feature carries that
feature's fingerprint, so the old STEP file is not needed. Each entry then becomes one of these:

- **carried:** it applies unchanged;
- **adapted:** it applies, and the plan says what changed in the feature (e.g. "ø6.6 → ø8.4,
  fit H7 kept"). A choice that no longer fits, such as a fit for the old size, becomes a problem
  with a suggested replacement;
- **ambiguous:** it is kept, and a question lists the candidates;
- **orphaned:** its feature is gone. It is kept, marked inactive and reported, so a later
  revision or a manual re-anchor can revive it;
- **new:** the feature has no entries; it takes its defaults and is listed for review.

Every plan item records which of these it is, so nothing changes without a trace.

## How the port is checked

- **The [rules catalogue](rules/README.md)** lists every rule the Python version learnt, from its
  ADRs, its lint rules and its issue regressions. Each rule is classified and tracked as pending,
  ported, translated or retired.
- **A differential comparison** checks Rust's plan against Python's sheets, fact by fact
  (measurement identities, values, labels, views, findings), with a verdict for each
  difference. Layout is judged by rules, not by matching Python's positions.
- **The ported lint rules** (`src/lint/`) run on the rendered drawing, independently of the plan:
  they read only the Analysis and what the drawing prints and claims (its annotations), so they
  judge Python's sheets and Rust's alike. A guard test fails if lint names the Plan or the Spec.
  `review` replies with lint's findings (`LintIssue`: code, severity, message, subjects, the
  requirements, evidence and a fix). `tests/lint.rs` runs them on Python's captured sheets and
  lists every difference from Python's own lint, with a verdict, in
  `tests/fixtures/known_lint.json`.

## Rendering caches (thin slice S4 part A)

`model::render` remains a fresh deterministic render. A caller that keeps
`model::Projections` and uses `model::render_with` shares each hidden-line projection (direction,
up and cut), and its latest placed curves and visible-ink bounds (scale, projected centre and page
origin). Changing the part's source binding clears these caches. `Projections::stats` counts the
actual projection and view-bounds passes, so an annotation edit can demonstrate that neither ran.

The same path is available in two steps: `model::render_geometry` produces the views' visible and
hidden edges and bounds; `model::render_annotations` assembles that geometry with annotations and
sheet furniture. Pass the geometry of the same planned views, in the same order and at the same
placement and scale. For a slice item's own drawing, `model::render_item` takes
`AnnotationItem::Dimension` (including callouts) or `AnnotationItem::Gdt` (frames, datums and PMI
labels). Its `RenderedItem` holds only that item's ink, searchable text and lint provenance, with
local run indices. `RenderedItem::append` rebases those indices when combining fragments. The
whole-sheet renderer uses these same item functions; planning and placement are unchanged. Sheet
furniture (border, title, notes, tables and section furniture) is assembled in the whole-sheet path.

`pmi::Reader::default()` keeps the latest reading for one immutable part. Assign
`analysis.pmi = reader.attach(pmi_bytes, &part)` when PMI changes, and plan from that Analysis:
material, threads/knurls, dimensions, frames and datums consume the one report. Identical bytes
(including an empty or refused reading) do not read again; changed bytes replace the report.
`Reader::reads` counts reading attempts. Replace the reader when replacing the geometry. The
ordinary analysis API still attaches PMI once and carries the report in the Analysis; there are
no separate material, thread or frame reads in planning or rendering. Haecceity's geometry/part
inventory reader remains separate from its semantic PMI parser.

## Sessions (thin slice S4 part B)

`session::Session` (also exported as `draftwright::Session`) owns one immutable part and its
Analysis (including the already-read PMI report), current Spec/Plan, projections, annotation
ink and Spec history. `Session::new(part, analysis, spec)` takes trusted in-process geometry
and analysis; `Session::open(step, api::Features, spec)` loads them once. The batch operations
above retain their existing behaviour.

`apply(&[items::Op], commit)` uses exactly `edit`'s operation vocabulary and validates the
result through the recovered planner. `commit: false` returns the same delta as committing
from that state, while changing neither state, history nor cache counters. Refused edits also
leave the last good state intact. A batch is one undo step; `undo()` and `redo()` return deltas.
A successful new commit discards the redo branch; a preview or refusal preserves it. History
retains Specs, rather than whole drawings or projections.

Each edit makes one complete `plan_recovered` invocation. The existing planner is global, so
this is the conservative full-plan fallback: its internal layout candidate/recovery attempts
and canonical pin plan remain part of that invocation. `Session::stats().plans` counts those
invocations at the planning entry point, including undo/redo, and excludes discarded previews;
it does not claim to count individual internal layout candidates. `Delta.plans` reports the
measured invocations for a transaction, including a discarded preview. Queries never invoke it.
Projection and bounds counters count actual expensive passes as in S4 part A; the annotation
counter counts actual independent item renders. Unchanged label/geometry/provenance keeps its
ink, even when the item's basis or default changes. Sheet furniture is assembled afresh.

`Delta` reports `changed`, `added` and `removed` item IDs/typed answers, and complete problems
added/resolved (a changed problem is resolved and replaced). It also carries a lossless
`PlanDelta`: changed top-level fields and changed collection records by stable ID, with order
only when it changes. The other payloads are changed view geometry and independently rendered
annotation ink, using S4 part A's `RenderedItem`. Geometry and ink are typed, serialisable curve
records; no full SVG is returned on each edit. Omitting an item can keep its ID/state in the
Plan while removing its ink. `Delta::apply_to(&mut session::State)` replays without planning or
formatting annotation labels. `State::render` assembles those cached pieces and the plan's
furniture through the same renderer as a fresh sheet, including searchable text, lint provenance
and embedded Spec. `Session::drawing()` exposes the current assembled drawing for export.

The queries are `item(id)`, `items(&ItemFilter)`, `problems(&ProblemFilter)` and `options(id)`.
An item answer carries typed state, resolved value, the engine's label, placed geometry,
anchors, features, basis and allowed edit fields. Item filters combine view (with or without
`v:`), kind (including linear/callout/angular), exact typed state, feature, region (page millimetres) and proximity to another
item's ink (`near`, optional `radius`, default 10 mm). PMI feature associations follow overlap with the feature's drawing faces; their anchors also
name those faces. Problem filters combine code, severity
and subject ID or feature ID (all associated item subjects, plus the feature itself).
Unknown item IDs give the five nearest IDs. Options conservatively offer compatible view/side
placements, view anchors and the ISO scale series for the sheet. `settable` maps escaped Spec
paths to choices, ranges or forms, including tolerance strings, bare view keys, title fields,
and fit classes valid for each associated hole/boss's nominal size. A descriptor's `paths`
shares its choices across all listed member paths. Collisions are reported by preview/apply;
queries do not run trial plans.

`Session::label_rows(callout_address)` returns a serialisable map keyed by compatible
shown principal view IDs (`v:plan`, etc.) for an editor's exterior snap guide. Each
`dims::LabelRows` has `outline: [left, bottom, right, top]`, `first_row` and
`row_spacing`; the latter two are maps keyed by `above`, `below`, `left`, `right`.
All coordinates and spacings are paper millimetres, with y increasing upwards.
These are **elbow coordinates**, not text baselines. Lane `n` is
`first_row[side] + sign * n * row_spacing[side]`, with sign +1 above/right and
−1 below/left. The first row is 10 mm outside the projected envelope. Above/below
spacing is 7 mm; beside a view it is the greater of that view's widest canonical
callout and this callout's current ink width, plus its 3 mm shoulder and 7 mm
clearance. The resolver and query share the spacing calculation. Canonical widths
are retained as planning metadata, so another item's text edit cannot move
a pin's columns; the Plan's serialised shape is unchanged.

This query performs no planning or projection, accepts the usual bare/qualified
item addresses, and refuses non-callouts. Re-read it after applying an edit to a
view, sheet or this callout's text. It describes explicit exterior `side/lane/along`
placement. Automatic labels can lie inside the view or use the automatic solver's
other exterior candidates; read their actual geometry through `item`/`plan`.
A bare pin retains that automatic geometry. To pin a label dragged anywhere, including into
the view's open space, use `place.offset`: a placed callout dimension record exposes `callout_offset: [dx,dy]`
(in paper mm, y up; omitted without a canonical placement). It equals bend minus the automatic
tip, so it can be copied into `place.offset`. `Session::callout_offset(address)` returns
`CalloutOffset { view, tip, bend, offset, members }` (paper mm), where `tip` is the automatic leader tip
the offset is measured from, `bend` the bend as drawn now and `offset = bend - tip`. Pinning a
drop at `b` is `{"offset": b - tip, "pinned": true}`; pinning the returned `offset` keeps the
callout exactly where it is. `tip` is kept relative to its view's origin, so it is in the same
frame as the drawn bend after packing moves views. `members` lists the projected attachment
sites as `{centre: [x,y], radius, span?: {start, sweep}}`, in paper mm (y up),
with counterclockwise arc spans in radians. A dragged group callout attaches to the
member nearest its elbow; ties retain the automatic member. The tip is placed on
that member with the same rim/arc rule as automatic leaders. Switching members
keeps the elbow, shoulder and label position; `tip` remains the automatic offset origin.
The query does not solve layout or project model edges and refuses
non-callouts, a callout drawn without a leader, and one pinned into a non-canonical view. Editors with their own Plan read the same tip with
`plan.automatic_callout_tip(id) -> Option<(view, [x, y])>` (`None` on a deserialised Plan).
For linear dimensions, `Session::label_offset(address)` returns
`LabelOffset { view, anchor, label, offset }`, all coordinates in paper mm (y up). `anchor` is
the canonical automatic label origin transported with the current view; `label` is the label
origin as drawn now, and `offset = label - anchor`. Pin a drop at `p` with
`{"offset": p - anchor, "pinned": true}`. The returned offset reproduces the current position;
for an automatic dimension it is zero. The query performs no planning or projection and
refuses non-linear items, missing automatic placements and items not drawn in the canonical
view. `plan.automatic_dimension_label(id)` reads the retained anchor (`None` after Plan
deserialisation).

The WASM Engine exposes these live queries as `calloutOffset(address)` and `labelOffset(address)`;
use them when the editor holds a deserialised Plan. Both return JSON strings and preserve the
session's API refusals. They add no MCP tool or JSON-lines operation.

Editors that keep their own session can call public
`dims::label_rows(&analysis, view, width)` directly. Pass the greater of the
edited callout's ink width and `plan.canonical_callout_width(&view.id)` (paper mm).
The latter returns `Option<f64>`: `None` means no canonical callout for that view,
or a deserialised Plan whose non-wire metadata is absent. Keep the freshly planned
Plan to use this API; do not infer canonical widths from edited placements.
This uses the same calculation as `Session::label_rows` without requiring a
`draftwright::Session`.

`draftwright serve` keeps one Session per connection, behind `api::Service`. The additional
operations advertised by `hello` are:

```json
{"id":1,"op":"open","step":"part.step","spec":{}}
{"id":2,"op":"apply","commit":false,"ops":[{"op":"set","path":"dimensions/d:envelope~1width.length/tolerance","value":"±0.1"}]}
{"id":3,"op":"apply","ops":[{"op":"set","path":"views/iso/omit","value":true}]}
{"id":4,"op":"undo"}
{"id":5,"op":"redo"}
{"id":6,"op":"item","item":"d:envelope/width.length"}
{"id":7,"op":"items","filter":{"kind":"dimension","view":"front"}}
{"id":8,"op":"problems","filter":{"severity":"error"}}
{"id":9,"op":"options","item":"sheet"}
```

`open` returns the initial replayable State, defaults to native recognition, accepts `features`
(`native`, `none` or Python recognition JSON path), or a bound `analysis` in its place. A new
successful `open` replaces the connection's part/history; a failed one preserves them.
`apply` defaults to committing. Item IDs use `item` so the request's `id` remains the reply
correlation ID. The original six operations and protocol/reply shapes are unchanged, and may
be interleaved with session requests. `api::call` remains stateless; `api::Service::call` owns
the connection state. Editor integration and MCP adapters belong to S5 and S7.


### Plan-time hole schedules (H1)

`hole_table` defaults to `auto`; `always` requests schedules and `never` disables
both plan-time schedules and the sixteen-hole rescue. The same `set`/`remove` Spec
operations and MCP `item_options` expose this field. `model::plan` remains the
raw layout diagnostic; `plan_escalated` and every recovered API plan honor it.

Each principal end-on view chooses its own presentation. Six or more holes use
a schedule when `layout::crowded_locations` finds illegible ordinates, there are
more than eight hole location statements, or a hole leader crosses a feature or
another annotation. Equal spacing on an established bolt circle keeps the
existing counted PCD/BC note in auto mode. A schedule uses the theoretical
intersection of the carrying solid's minimum datum planes, including tangents
at rounded corners. The solid's measured profile bounds must prove this origin.

`Plan.hole_tables` carries each view's schedule, resolved model-space origin,
rows and placed blocks. Columns are `TAG | ø / DEPTH / THREAD | X | Y | Z OUT / DIR`.
All three coordinates are in the carrying view's frame: X right, Y up, Z out of
the page toward the reader (the header says `Z OUT`). The entry is the
opening's distance from the part's rearmost face along view Z; the direction is
`−Z` (drilled into the page) or `+Z` (from the far side), for example `0 / +Z`.
The entry claim carries the view's out-of-page direction (`entry.view.x,y,z`),
which lint uses to check the cell. Each physical hole has a row;
coaxial openings share one tag such as `B1/B11`, with separate entry cells.
A tag sits beside its hole only where it is plainly nearer that hole's rim and
centre than any other scheduled hole's; otherwise it takes a short leader from
the hole's rim (shortest, fewest crossings, fanning outward from neighbours,
never through another scheduled rim, label or tag leader).
Tags are grouped by approved size wording; X/Y are the carrying view's two in-plane coordinates. The origin
and positive coordinate directions are marked in that view. Size and location
cells retain the planner's formatting and tolerances; an authored omission
is not resurrected. Rows retain typed measurement claims, and the rendered
coordinate cells certify only their own axis. Lint checks their printed values
against Analysis geometry; a wrong or missing entry cell reports
`hole_table_entry_invalid`. Five or more distinct equal-radius sites about a
recognized bore axis can use a `pcd` statement, a located circle centre and an
explicit `ANGLE` column (counterclockwise from local +X). Missing positions do
not imply equal spacing; the member X/Y remain supplementary locating evidence.
Tags and origin labels avoid boundaries, bore silhouettes, other annotation ink
and section furniture; their resolved `label_box` is also judged by structural
lint and the SVG quality instrument. Pins keep their callout or location ink. A row replaces a
semantic PMI size callout too: it prints the callout's text (band, fit or thread) verbatim, and
the callout's PMI claim moves to the first row of its holes, so every PMI size is stated once.
A frame or datum symbol that stood on a replaced callout attaches to the hole itself instead (a
frame by a leader to the hole, with the count of several holes over it; a datum symbol on its
tag or on such a frame). Where replacing those callouts would add an error the drawing without
schedules lacks, or would leave such a glyph with no attachment at all, the schedules are
installed again with them kept beside their rows. Auto retains authored size
fits/tolerances. Coordinate tolerances keep their original span.

Table-owned callouts remain as stable covered dimensions; retired linear spans
are covered entries in `omitted`, as other superseded spans are. Their measurement
provenance is retained in the schedule's `covered` list for the completeness ledger.
They reserve no lanes; aliases previously consolidated into them remain consolidated. The ordinary recovered principal
and isometric geometry scale and sheet size are retained for the schedule
layout. If preserving the scale at a new automatic isometric centre would clip
the drawing, its proven full frame is retained; bare pins retain their canonical
frame and explicit isometric placements keep their fitted frame. Parenthesized fit limits survive; only the pattern lattice suffix is
removed when member coordinates replace its pitches. Table placement uses the existing clear corner-block inventory; a
failed complete-table/tag transaction restores the original drawing. An
unavailable explicitly requested table is a warning, with original dimensions
kept. The old sixteen-hole rescue remains a separate backstop. The deliberate
Python differences are recorded in `tests/fixtures/known_hole_tables.json`.

**Long tables split side by side.** Any table (declared, radius, manufacturing, gear or
hole schedule) taller than the sheet's drawable height uses side-by-side blocks, up to four.
Every block repeats the header rows (including a schedule's title), and rows read down then
across. The last block is padded to the common height. A table that fits whole stays unchanged;
shorter forms are tried only after whole-table placement fails.

Sheet choice considers these forms before ruling paper out for a table's width or height.
When a tall schedule needs it, the settled drawing is repacked on one sheet around the tables:
all dimensioned views keep their scales and projection alignment, every row, tag, datum symbol,
measurement and note is retained, and the undimensioned isometric fits with its NTS caption.
Counts are tried in order of total blocks; a companion schedule may wrap too. Existing source
completeness findings stay with this same-sheet transport. It does not add sheet-assignment
errors for datum symbols already unavailable in the source. This is no permission to split the
principal views across more sheets. An unavailable schedule keeps the original dimensions.

For shared tables, `placed.blocks` holds each ruled block; `rows` and `placed.cells_at` retain
source order and claims. Hole-table blocks are `<id>/1`, `<id>/2`, … in
`Plan.hole_tables[].blocks`; each row still certifies its hole from its own cells. The older
sixteen-hole rescue (`hole_table_plan`) keeps its existing fewest-blocks wrapping.

`sheet/table_columns` overrides the count by table ID: a declared `t:<k>`,
`t:radius-centres`, `manufacturing_requirements`, `gear_data_<owner>`, `hole_schedule_front`,
`hole_schedule_plan`, `hole_schedule_side` or `hole_table_plan`, each 1 to 4. `1` never splits
the table; `2`–`4` always draw that many blocks (the sheet choice reserves that form). Any other
ID or count is refused when the Spec is read, for example
`{ "op": "set", "path": "sheet/table_columns", "value": { "hole_schedule_plan": 2 } }`.


### Interior placement (Stage H7)

Automatic callouts contribute Python's bounded eight rays and eight text-lane
stations, sharing at most 64 alternatives across a grouped feature's anchors.
Bore targets stay radial; other features retain their physical attachment while
the ray rotates. Every tip must touch a projected edge. A complete interior label must fit over proven
projected material, with half a paper millimetre of clearance from every visible
and hidden edge and all settled annotation text/ink. The candidate's entire
leader/dimension geometry stays off other views and page furniture. Pins and
explicit placement retain precedence. Linear dimensions retry up to eight inward
lanes after their settled exterior placement fails; the whole dimension moves,
including witnesses, arrows and label. Every line and arrow independently clears
visible and hidden projected ink; only the first 0.6 mm of a callout at its
attachment may touch the target. Witnesses leave their own opening at its near
rim with the normal gap. Labels inside their own view are accepted
by placement checks only after re-proving blank face; rendered structural lint
still checks projected ink and suppresses the blank-region notice for a
deliberately interior annotation, including Python-read drawings without a
second annotation ownership record.

Interior placement currently requires a planar face square to the view. A curved face's
silhouette is bounded by its generators (a cylinder seen square) or its whole box (anything
else), so labels keep off it. Section views keep exterior labels; a cutting-plane line, its
arrows and letters are obstacles in the view that marks them (`model::cutting_line`, shared
with the renderer). These limits and the difference from Python's projected material field
are recorded in `tests/fixtures/known_interior.json` and `docs/next-session.md`.

### Blend radius notes (G3)

The Analysis optionally carries `blend_sites`, exact trimmed-cylinder/torus points in the part
frame, indexed by blend feature ID. Recognition derives them from the defining faces and checks
the rolling radius; the rolling path itself is not a leader attachment. Older version-7 analyses
read with no sites and cannot supply a new blend leader without that evidence.

Blends with equal displayed radii share exact occurrence credit. Fewer than five radius-bearing
blends outside grouped relief use compact `R` / `n× R` leaders. On a denser part, its most
frequent repeated radius is
one `ALL UNSPECIFIED RADII R…` note, including fillets with the same displayed value.
Other radii retain explicit exception leaders, combining equal blend and fillet groups.
Obstructed straight exception groups use an optional `radius_table` (`t:radius-centres`):
every occurrence has its own named view, radius and centre X/Y in model millimetres from
the left/bottom of that view's envelope. Centre locators print up to three decimals; radius
sizes retain the normal displayed precision. The blend's own trimmed-surface samples must
prove that centre. Dimensions represented there keep their IDs and become `covered` table rows.
Independent lint validates the printed view, X, Y and radius against each exact occurrence.
A wrong, missing or duplicated locator cannot borrow credit or unlock the default. If a
complete table cannot fit, the required leader remains with its routing findings (Policy B).
An undrawn or omitted exception withdraws the blanket default. Scoped counts and representative XYZ points cannot locate a group
and grant no coverage. Explicit dimension placements and tolerances remain author choices
and retain their geometry findings.

Automatic notes have stable IDs `n:f:blend:<id>/blend.radius` and accept
`annotations/<escaped-id>/{omit,place}`. Session/MCP item answers expose the claimed features
and the edit paths. Automatic radius-only blocks use the existing 3 mm font on compact 5 mm
rows, clear of actual view outlines and placed annotation ink; authored/PMI notes keep the
existing ruled table, with a separate optional `radius_notes_block` for automatic radii when
both are present. An omitted or unplaced note grants no radius credit. The rendered
annotation's `satisfies` list identifies the exact feature/parameter requirements; the printed default must agree with those claims and other radii need explicit callouts.
Text alone never infers authority. General authored notes still carry no satisfaction claims.

Analysis also optionally carries `radius_reliefs`: geometry-owned evidence of grouped tiny,
complex shallow profiles on a common broad carrier (`src/blends.rs`). Recognition features
remain intact. Every defining face of an excluded radius must belong to one proved region;
lone/simple/deep pockets and large faces are retained. Old Analyses have no exclusions.
The companion automatic note `n:f:blend:relief/profiles` says
`SHALLOW RELIEF PROFILES PER MODEL`, carries no fabricated radius claims and accepts the
same omit/place edits. A placed note reports the exact governed features through the
informational `relief_profiles_per_model` disposition. With relief present, a blanket radius
default requires that note to
be drawn; hiding/removing its annotation withdraws default authority in planning and lint.
The geometric thresholds and the single-solid restriction are overturnable drafting policy,
recorded with positive and negative evidence in `docs/next-session.md`.

Same-face boss/blend precedence uses one shared predicate for planning and lint. A uniquely
proven blend radius replaces that face's duplicate boss diameter; the independent boss height
remains required. Missing or ambiguous face evidence never deduplicates by scalar equality.
The differences and verdicts are recorded in `tests/fixtures/known_blends.json`.

Precision is a display policy in `precision`, a map from measurement IDs such as
`d:envelope/width.length` or `d:f:hole:1/counterbore.depth` to decimal places (0–15).
It retains trailing zeroes in the label and its measured reservation, without changing the
nominal or tolerance. Conflicting explicit policies for a shared printed term are refused;
they require separate dimension owners. Without a policy, formatting is unchanged.

`envelope_tolerance` supplies a band to all three overall extents. A dimension's own
`tolerance` or `fit` overrides that fallback. An extent fit uses the existing ISO 286 table
and prints the class alone by default. `sheet/fit_deviations: true` adds stacked upper/lower
deviations; the legacy `sheet/fit_limits` alias is supported, with the new setting taking
precedence. Authored precision controls the extent nominal in both modes and leaves the
fit deviations and explicit tolerance digits unchanged.

A feature note can explicitly satisfy its own measurement roles with
`features/<id>/note_satisfies`, for example `["counterbore.diameter", "counterbore.depth"]`.
Only its placed leadered note grants authority. This is separate from dimension claims:
prose grants no coverage, a diameter role implies neither THRU nor count, and sibling
features need their own explicit claims.


### Label wording and explicit dimension styles (#76, item 5)

Wording is authored under the existing stable dimension or annotation ID. No page
coordinates or measured values are stored in the Spec. These additive fields work in
`draftwright.spec/2`; saved Specs without them retain their automatic labels and layout.

```json
{
  "dimensions": {
    "d:envelope/width.length": {
      "text": {"prefix": "2×", "suffix": "TYP"},
      "style": "reference"
    },
    "d:f:hole:3/bore.diameter": {
      "text": {"template": "{value} AFTER PLATING"},
      "style": "basic"
    }
  },
  "annotations": {
    "pmi:common_label.example": {"text": {"template": "{value} REF"}}
  }
}
```

Use IDs returned by the current Plan or item queries; the example annotation ID is
illustrative. A dimension callout belongs in `dimensions`; unframed PMI prose labels
and the automatic radius/relief notes belong in `annotations`. Datum letters, geometric
tolerance frames and surface texture symbols retain their structured feature edits:
free wording on those symbols is refused.

`text` takes either `prefix`/`suffix`, or `template` with exactly one `{value}`.
The placeholder expands to **all generated terms**, including compound diameters,
depths, angles, counts, thread designations, precision, tolerances and fit classes.
It is deliberately more than a bare nominal: changing wording cannot remove another
measured requirement from a callout. Prefix and suffix are separated from the generated
terms by one space; surrounding literal whitespace is trimmed. For example `REF`,
`AFTER PLATING` and `(NOM)` are ordinary suffixes. A literal full replacement, even one
containing today's measured number, is refused: use `{value}` so the saved Spec remains
truthful when geometry or display precision changes.

Literal wording is one line, at most 240 UTF-8 bytes per field, without control characters,
extra numeric values, tolerance/measurement symbols or unknown placeholders. The exception
is one positive leading quantity `N×`; it cannot be added to an already counted label,
and independent quantity lint still judges it against the part. These rules refuse a
second nominal or a conflicting value instead of letting prose impersonate a measurement.
Unknown IDs are refused with current IDs or an explicit removal instruction. An override
on a merged dimension is refused with its owning label ID; apply wording to that owner.

`style: "reference"` encloses the entire label in parentheses; `style: "basic"` draws a
box around its full ink. The enum makes these choices mutually exclusive. Remove `style`
to restore the automatic choice. An imported basic PMI dimension cannot be made reference
without correcting its source PMI. A newly authored reference has no fabricated
`determined_by` chain. Basic/reference styling changes no nominal or measurement claim,
and lint continues checking the resulting output, including redundant controlling sizes.
Reference dimensions remain information only. Parenthesised hole ordinates supply no
location credit, matching the controlling dimension-chain check. Reference-specific
diagnostics, such as a tolerance attached to a reference, still apply.

Wording is applied after automatic ownership/consolidation and precision/tolerance
formatting, before placement and reservation. Full terms drive ink measurement, leader
shelves, label clearance and searchable SVG/PDF text. Automatic references keep their
parentheses around all authored wording. Plans report `text`, `style.reference` or
`style.basic` in `from_spec`, with the choice explained in `basis`; annotation and automatic
note wording likewise report `from_spec: ["text"]`. Unmentioned choices keep their defaults,
including paper/scale policy, automatic views, datum placement and class-only fits.

The existing `items::Op` `set`/`remove` operations, session previews, history and replay
handle these fields. Escape the slash inside an ID, for example:
`dimensions/d:envelope~1width.length/text`. Item options expose `text`'s fields/template
form and the two `style` choices. `get_spec`'s output schema describes the saved text shape;
other MCP tools retain the existing generic per-item Spec-map input convention. The schema
generator uses equivalent minimum-property counts for closed objects whose every field is
required, preserving the existing session byte cap.

Lint reads the live value-bearing text inside the compiled wording wrapper and the
original measured claims. It does not infer a new value from authored prose or trust an
approved nominal instead of the drawn text. Damaged wrappers or missing values lose claim
proof; linear fidelity compares the live numeric text against the drawn witnesses. Wording
never acknowledges or suppresses a lint finding.

Large MCP `item_options` replies keep the complete descriptor map in `structuredContent`.
When repeating it as text would exceed the existing 4,096-byte reply cap, the text channel
contains an `id`/`detail_ref` stub. Text-only clients read the identical complete JSON with
`resources/read {"uri":"draftwright:<detail_ref>"}`. Native/session options keep every field.

Automatic drawings use available slack to open up to 25 mm of blank paper between
neighbouring views' annotation bands. This spacing is applied after sheet, scale,
detail and sheet-allocation decisions, using settled ink. It translates complete
rows and columns, including aligned sections and auxiliary projections, without
resizing anything. The gutter shrinks continuously to zero when the selected
paper has no room, with the frame, title block, tables, notes, projection alignment
and pinned annotation stations held clear. Automatic isometrics and sections
reserve their drawn geometry, annotation ink and captions. Relative view
placements retain their authored `views/<name>/place/gap`, including zero.
`sheet/view_gutter` sets the preferred maximum paper-mm spacing (0–1189,
the largest supported sheet width); `0` disables this final spacing pass.

### PMI and location origins

`pmi` is `"first"` by default. Semantic PMI takes precedence over generated dimensions:
a feature already located by a chain of PMI dimensions to an origin receives no second
engine location on that axis. PMI sizes similarly own their feature's size. Pattern
pitches yield to individual basic locations, and PMI pitches plus a member location
convey the other member locations. Covered engine IDs remain in the omitted ledger
with the IDs of their PMI owners.

Specify's general-tolerance dimensions (specify-core 0.1.4: a Specify-written dimension with
its nominal alone, which Specify itself reads back as settling no question) are the exception
until one dimension planner serves both (#264): they are drawn as the generated drawing
dimensions that geometry, never as PMI dimensions on top of it (#265). The generated item of
the same span, or the one dimensioning the feature their faces belong to, names them in its
basis. One no generated item states is planned as draftwright's own dimension
(`d:specify_general:<identity>/general_size.length`): it takes the ordinary candidate, lane,
redundancy and drop path, with no PMI precedence, and its basis names its source (carrying
its characteristic number). The PMI coverage check does not report them as undrawn.
`pmi: "strict"` still draws them as the file's dimensions.

`pmi: "strict"` draws the part's semantic dimensions, callouts, GD&T and cited datum
symbols, without generated dimensions or callouts. With `dimension_source: "authored"`,
listed engine IDs can be added. Missing locations and sizes are reported as Problems.
`pmi: "ignore"` draws the ordinary generated drawing and reports the number of PMI
items withheld; analysis still reads and reports the PMI. With no PMI, all three modes
produce the ordinary drawing.

`origin` overrides location origins by part axis, for example
`"origin": {"x": "face:5", "y": "min", "z": "max"}`. Values are `"min"`, `"max"`,
or a face/feature reference resolving to a plane normal to that axis. Otherwise the
first usable planar datum in letter order (A, B, C) supplies the origin; absent a usable
datum, the box minimum remains the origin. Unusable datums produce an informational
Problem. Dimension basis text names the selected origin. Datum symbols are drawn only
when a geometric tolerance cites them; using a datum as a dimension origin alone does
not draw its symbol.

The located-once check uses the dimensions actually drawn. `feature_not_located`
means no dimension chain reaches an origin; `feature_located_twice` names independent
chains locating the same feature axis. Identical PMI spans with identical text share
one drawn dimension, which claims every source record. Distinct authored constraints
are retained even when redundant. The check runs for parts carrying PMI or a Spec origin;
plain parts keep their existing location checks. A cylindrical datum establishes coaxial
features, while principal planar datums supply ± dimension origins.

A bolt circle on the axis of the part's whole round outline needs no tangent
ordinates when a true-shape view shows that outline, the pattern's pitch circle,
and their common centre mark. Model concentricity within the 0.001 mm coordinate
resolution confirms this drawn relation. Credit applies only to the view's two
in-plane axes; coincidence in the model alone grants no location credit.

Hole schedules state every size and coordinate, including coordinates already supplied
by retained PMI. Those cells restate the same origin frame and do not add an independent
locating constraint. Whether an automatic schedule is drawn on a PMI part is still decided by
the native callouts it frees: one whose rows would replace only PMI callouts stays inline;
`hole_table: "always"` can request that presentation explicitly. Selected table
origins are named in the basis and are labelled as origins rather than box minima.
Each coordinate cell locates only the feature identified by its row. A coincident
number for another hole cannot locate a slot; shared location credit needs a drawn
dimension witness conveying that slot's position.
The transverse backstop checks standalone obround slots with proven full-round
ends, including when other features are scheduled in a hole table. Compound and
unproven-end profiles retain their existing ledgers pending constraint analysis.
An authored ordinate to a slot's explicitly named end-cylinder axis locates its
transverse centre from either physical edge. That end cylinder supplies no claim
to the slot's longitudinal centre or low end.

### Specify PMI authorship

Specify's [PMI provenance contract](https://github.com/pzfreo/specify-core-rust/blob/main/docs/pmi-provenance.md)
uses the exact token `specify-core:written:v1`: `THREAD` description (slot 1),
`TURNED_KNURL` name (slot 0), or an inverse `DESCRIPTION_ATTRIBUTE` on a requirement's
own `PROPERTY_DEFINITION_REPRESENTATION` (a dimension's own `SHAPE_DIMENSION_REPRESENTATION`). Draftwright reads these from the raw STEP
Document and preserves the token in the existing `pmi.sources[].reason` diagnostic as
`Specify-written PMI (specify-core:written:v1).`, followed by any blockers. This survives
Analysis serialization without adding schema fields. Authorship is never inherited from a
shared face, feature, datum, file name or stored interview answers. Lint-only
`pmi_not_lowered` messages describe the annotation and retain a refusal reason when it
contains no internal IDs or authored paths. Full refusal diagnostics remain in `evidence`,
and the entity identity remains in structured sources.


### Inspection characteristic balloons (#254)

`sheet/balloons: "all"` draws inspection rings for numbered AP242 dimensions, geometric
tolerance frames, datum features, surface textures and thread/knurl callouts; `"off"` and
an absent setting keep them off. Numbers come from the requirement's own `ID_ATTRIBUTE`
([storage convention](pmi-bookkeeping.md#characteristic-numbers-producer-convention-254)),
with the interim `characteristic number` UDA as fallback. Conflicting numeric values are
reported and the id wins; duplicate ids are reported on the item and supply no number.
Non-integer ids are quietly ignored for numbering. Draftwright does not allocate or change
a number. Unnumbered derived items and general CSV rows get no ring. Semantic notes stay
unnumbered unless their own requirement carries a numeric id. A part-default texture's
ring attaches to its default texture symbol beside the title block; a numbered note's ring
attaches to its own note line. General-tolerance dimensions credited by #265 carry their
numbers onto the credited planned item.

`Plan.characteristics` lists the file's numbers, original `item`, final `drawn_item` and
`sheet`, including requirements that cannot be drawn. `items::items` exposes
`characteristic_numbers` on the original item and its carrier (an array because one item
can state several requirements). The library offers `characteristics::list(plan)` and
`Session::numbered_characteristics`; WASM offers `numberedCharacteristics()` as JSON.

A grouped `n×` characteristic gets one number-only circular ring on the callout. Hole-table
characteristics get their rings beside their rows, repeating a shared size or thread
number at every applicable row (including when a retained size callout also states it);
the existing TAG rings retain their letters. Number rings enclose the measured glyph bounds with 0.35 mm inner clearance
and have a minimum 3 mm outer radius. A covered dimension's ring attaches to the dimension, table row or general-note
line that states it. Rings use settled label/leader clearance predicates and belong to the
sheet carrying that item. Like other annotation leaders, ring leaders may cross dimension
extension strokes transversely; they cannot follow them or cross text, dimension lines or
other leaders. Several numbers conveyed by one compound callout may form a connected
group of rings on that same carrier, sharing its single exit into free space. A failed placement is reported, never drawn over information, and does
not itself request larger paper.

`balloons/occurrences: true` additionally requests the same number at each proven repeated
cylindrical occurrence in the carrier's end-on view. Unlocatable occurrences are not guessed.
`balloons/items/<source>/suppress: true` suppresses a ring; `position: [dx, dy]` pins its
centre relative to the carrier's ink centre, in paper mm (x right, y up). Source keys are
PMI source IDs such as `dimension:#640`; original plan-item IDs are also accepted. A source
key wins when both exist. Positions are checked for clearance; unsafe pins remain undrawn
and reported. With inspection enabled, `characteristic_not_drawn` and
`characteristic_balloon_missing` have `incomplete` impact, including explicit suppression.


## Drawing packs (#41)

`draftwright pack assembly.step -o directory [--spec pack.json]
[--formats pdf,csv,json]` builds one drawing set per distinct made part, one
multi-page `pack.pdf`, RFC 4180 `pack.csv`, and `pack.json`. The library entry
points are `pack::build` (report and drawings in memory) and `pack::render`
(report and encoded files). The default formats are PDF, CSV and JSON. Failures
of individual parts retain their rows and error details, with no sheet reference;
other made parts still draw. Without a GA, zero made sheets produces a zero-page PDF. The CLI
returns success for a completed report even when it contains failed parts; check
`rows[].error` and `rows[].problems` before sending a pack to manufacture.


A **general assembly (GA)** is the front page when the pack has more than one
**group** (including bought groups). It counts in global SHEET numbering; part
page references shift accordingly. A single-group pack keeps its existing sheets.
`ga.include: false` omits the GA; `true` forces it for a single group. An all-buy
assembly still gets its GA and parts list. GA failure is a `pack_ga_failed`
finding; the remaining part sheets retain correct numbering.

The GA draws the complete placed assembly, through the existing view selection,
HLR and A4-first paper/scale policy. It uses principal views plus an isometric,
with only the envelope reference sizes (`d:envelope/width.length`,
`d:envelope/depth.length`, `d:envelope/height.length`). Component PMI and feature
manufacturing dimensions do not appear. Sizes are untoleranced auxiliary dimensions in parentheses, rounded to 0.1 mm with integer values kept as integers. Equal rounded envelope sizes are stated once; explicit dimension intent can retain both axes. Existing dimension `style` and `precision` intent overrides these defaults. Hidden lines default **off**. There is
**no exploded view**: this slice has no source of explode directions. The
assembly's top-level PRODUCT name and ID supply TITLE and DWG NO.; QTY is blank.
Generic root names/IDs (ASSEMBLY, PART, PRODUCT, UNTITLED, COMPONENT and known numbered CAD defaults) fall back to the STEP file stem. Authored TITLE/DWG NO. always win.

The ISO 7573 parts list sits directly above the title-block furniture. Its
columns are ITEM, QTY, NAME / DESCRIPTION, REFERENCE, SUPPLY and MATERIAL. The
rows match the pack rows/CSV, including bought and failed parts. REFERENCE uses
the part's authored DWG NO. (`parts/<key>/drawing/sheet/title/number`), otherwise
its product key; a reference equal to the description is left blank in both list and CSV. A suffix used only to disambiguate repeated product names is not treated as a drawing number. MATERIAL stays blank until #267 provides material evidence.
The CSV also carries `reference` and blank `material` columns. Rows read **from
the bottom upwards**, with the lowest item number at the bottom (item 1 normally).
The full list is reserved during planning; the final position must clear views
and their dimensions. A rigid lift preserves the principal views' alignment;
if it cannot fit cleanly, the ordinary paper and ISO scale trials are retried.

ISO 6433 **item references** mark one visible instance per group, including
bought groups, using the pack's stable item number. Their default **6 mm radius**
circle (expanded to fit unusually long item numbers) is larger than both the small number-only inspection characteristic
rings (`sheet/balloons`, `characteristics.rs`) and the 4.5 mm hole-table TAG rings.
They have separate `ga_item_<item>` identities and never claim an inspection
characteristic or a hole-table measurement. The shared balloon band/lane solver
aligns outside circles and rejects crossings with earlier leaders, dimensions,
the parts list and other views. A depth/ownership map per camera measures unobstructed visible area per physical instance; the clearest carrier wins. Exact edge probes recover exposed thin rims between pixels. Interior face sites use dots and outline sites use arrows, and sites for different items are separated before the references on a view are assigned jointly to nearby lanes. Alternative proven sites/views are retried when a band is full. Every attachment retains the owning source instance; unresolved rays are rejected. Native product HLR and cut contours are reused across proven congruent groups/placements; depth samples are shared by repeated cameras and the assembled kernel resolves inter-product occlusion. Resolved part curves retain their exact line, conic and rational geometry. When every GA view hides hidden lines, invisible curves are discarded before rendering; a hidden-line override retains them. Detail crops reject distant positive-weight splines by their conservative control hull before the ordinary clipping/render pass, retaining uncertain or degenerate input for normal findings. Recognised physical threads reuse the ordinary ISO 6410 carriers and conventional ink, with the grouping proof’s face correspondence mapped through STEP face entities to each instance. This also reuses recognition already performed for bought groups. The existing `ga.drawing.sheet.thread_depiction` and per-view `thread_depiction` overrides select physical geometry instead; unrecognised threads retain their native geometry. The JSON records face, instance,
GA-frame point, page tip and ring centre for review. `general_assembly.frame` maps STEP assembly coordinates to that common drawing frame. For cut attachments, `section: true` identifies the generated cut face/outline; `face` is the original ownership witness, and its source instance is independently classified at the target. A group with no proven visible
face is listed and reported as `pack_ga_not_visible`; one with no clear route is
`pack_ga_balloon_unplaced`. Neither gets a fabricated leader.

The assembly is oriented with its longest source axis horizontal while preserving the source depth/up convention where possible (`ga.horizontal_axis` selects x/y/z instead). All principal views share this frame and the ordinary first/third-angle projection. Native cylindrical faces supply centre lines, respecting `ga.drawing` sheet/view `centre_lines` intent.

An item without a distinguishable external site triggers full SECTION A–A through its centre, marked on the plan view. Cut material is classified per item and uses distinct hatch angle/spacing combinations; longitudinal screws, pins, shafts, rods and washers are left unhatched. A section wall below 0.7 paper mm triggers DETAIL B at a larger standard scale. These use existing section, clipping, hatch and free-paper placement machinery, retrying A4 before larger paper. Derived views reserve paper before the subordinate isometric, using the existing free-rectangle and isometric fitting policy. `ga.sections: false` and `ga.details: false` disable those recoveries. `ga.section_item` chooses the item whose centre selects the section plane; `ga.detail_scale` selects its enlarged region’s standard scale (larger than the section). `ga.hatching/<item>/{omit,angle,spacing}` overrides hatch intent (degrees 0–180, paper spacing 0.5–10 mm); omitted/null `omit` uses the automatic longitudinal-standard-part exception, `false` forces hatch and `true` omits it. Nested references prefer the recovered section/detail; `ga.balloons/<item>/view` also accepts `section_aa` and `detail_b`. Unresolved items retain findings and list rows.

GA intent lives in the strict `ga` pack Spec object:

```json
{
  "ga": {
    "include": true,
    "drawing": {
      "sheet": { "hidden": false, "size": "A4", "scale": "1:2",
        "title": { "title": "ASSEMBLY", "number": "GRM-00" } },
      "views": { "plan": { "omit": true } },
      "dimensions": { "d:envelope/width.length": { "view": "front" } }
    },
    "balloon_radius": 6,
    "balloons": {
      "1": { "view": "front", "position": [-35, 20] },
      "9": { "suppress": true }
    },
    "parts_list": true
  }
}
```

All GA fields are optional. `ga.drawing` uses the existing drawing Spec for
view selection/omission/relative placement, hidden lines (sheet or view),
paper/scale, title, and envelope dimension wording/placement/omission. Its
complete dimension set starts with the three envelope sizes; `omit: true`
removes a size. `sheet.keep_layout: true` prevents the automatic GA lift.
`ga.balloon_radius` overrides circle size (5–15 mm); `ga.parts_list: false`
omits the list. Table `t:1` is reserved for the list when enabled.
`ga.balloons` keys are **final item numbers**, so item-number overrides apply
first. `view` chooses a carrying view; `position: [dx,dy]` pins the circle centre
relative to that view's origin, in paper mm, y up; `suppress: true` records an
intentional omission. Unknown items and invalid views/radii are refused. Hidden
items remain reported even with a position override. `general_assembly` in the
pack report gives sheet, paper, scale, envelope, list, drawn/suppressed/unballooned
items and problems; `ga_seconds` is outside the deterministic report.

The independent, strict pack Spec (`schemas/pack-spec.json`) contains intent:

```json
{
  "schema": "draftwright.pack-spec/1",
  "parts": {
    "GRM-01 shank": {
      "supply": "make",
      "item": 12,
      "quantity": 4,
      "separate": false,
      "drawing": {
        "schema": "draftwright.spec/2",
        "sheet": { "size": "A3" }
      }
    }
  }
}
```

All entries are optional; absent drawing intent uses the ordinary drawing Spec.
Unknown document fields and unknown part keys are refused. Item and quantity
must be positive. Quantity replaces the total for the group (for a shop order,
for example), rather than multiplying each instance. `supply` overrides the guess.
`separate: true` keeps a product out of automatic geometry merges. A nested
`drawing` is the existing Spec, including its binding and normal validation;
a bad drawing Spec is reported on that part and does not stop other drawings.
An override on any member applies to the merged group; conflicting overrides
are refused with instructions to separate the products.

Product definitions already aggregate their placements in haecceity. Leaf keys,
also shown as `key` by `draftwright parts`, never contain STEP entity numbers:

1. Use the trimmed product **name** when nonempty and unique in the file.
2. Otherwise use a unique, meaningful product ID (not empty or a generic
   `SOLID`, `COMPOUND`, `ASSEMBLY`, `PART`, `PRODUCT` or `UNNAMED`, ignoring case).
3. Otherwise use the name plus its one-based occurrence in assembly order,
   such as `ASSEMBLY#2`. Blank names use `unnamed#1`, `unnamed#2`, etc.

Literal unique names reserve their keys before generated occurrence keys; append
`#` to a generated key until it is unused if a literal name already occupies it.
A name/ID collision keeps the unique name and gives the ID-addressed product an
occurrence key. These keys
survive entity renumbering on re-export. Renaming products, inserting/reordering
ambiguous names, or changing grouping can change keys; a Spec key matching no
leaf or group is refused with the complete valid-key list, never silently ignored.
The library's `pack::part_keys` provides the same leaf keys without drawing.

When **all** geometry-merged names differ only by a trailing numeric index after
`_`, `-` or a space, use the common stem for the group's key **and display name**:
`wheel_1` … `wheel_5` become `wheel`. Otherwise keep the first member's key/name.
A stem colliding with an unrelated leaf key falls back to the first member's key.
`products` lists all leaf keys; `part_indices` preserves the original indices.
A Spec may address the group stem or any member; their overrides must agree.
`separate` addresses individual leaf keys, not a merged-only stem (the error
names this requirement). No geometry hash is a user-facing identity.

Geometry equality first compares the exact resolved B-rep definition in the
products’ own frames: surfaces (including NURBS poles, weights and knots),
edge curves and endpoints, face orientation, loops, pcurves and solid topology.
This path needs no sampled inversion, fingerprint or face-area integration.
Fingerprints are computed lazily only for compatible nonidentical B-reps. Otherwise it
tries the products’ own frames with fingerprints, then quiddity’s
fingerprint alignment using **proper rigid motions only**. It requires equal
face, edge and solid counts; face surface kinds, intrinsic parameters, areas,
adjacency, centroids and outward normals; and bidirectional sampled points
against exact trimmed face surfaces. Intrinsic scalars allow 1e-7 relative
error with a 1e-7 absolute floor; positions and surface distances allow 1e-5 mm,
and normal vectors 1e-6. Haecceity's face clouds use 0.1 mm sampling deflection;
this is sampling density, **not** the equality tolerance. Outside the exact B-rep path, missing face areas or
an unproven alignment conservatively keep separate groups. Reflection is never
an alignment candidate: handed parts remain separate. These are conservative
engineering identity checks, not a proof of equality of arbitrary freeform B-reps.
Each geometry merge is an info finding naming every merged product.

Automatic buy candidates use whole name tokens (screw, washer, nut, bearing,
dowel, bolt and their plurals, plus setscrew). A leading custom drawing identifier
(letters-number, excluding ISO/DIN/BS/ANSI) keeps authored screw/bolt descriptions
made. Otherwise quiddity's recognised features support these bounded shape rules:

- **Plain thin-annulus washer:** one complete turned body, exactly two planar
  shoulders and one internal/external cylinder, one full-length bore, OD 0.5–100 mm,
  thickness 0.05 mm to OD/4, bore 0.2–0.9 OD. No additional profile features.
- **Plain dowel:** one complete solid turned profile, OD 0.5–50 mm, length 1–10 OD,
  one constant-diameter barrel, two solid end planes, and a small conical chamfer
  or toroidal round at **both** ends (each axial/radial extent at most 0.2 OD).
  Zero-sweep torus seam segments are ignored; steps, bores and untreated ends fail.
- **Headed externally threaded fastener with hex drive:** one recognised external,
  single-start modelled thread (major diameter 1–30 mm, length 0.5–12 diameters,
  pitch 0.05–0.4 diameters), a coaxial recognised turned-step head 1.3–2.5 diameters
  wide and 0.2–1.5 diameters high near a thread end, and a recognised six-sided
  prismatic socket in that head. This bounded rule accepts axis-aligned evidence;
  unsupported/oblique evidence conservatively stays make.

Each guess is an info finding naming its name/shape rule, even after an override.
CSV/JSON buy reasons contain each distinct text once. GRM-03 has no recognised
external thread and has a blind bore and several shaft diameters; GRM-05 also has
no recognised external thread and has a stepped shaft. Both stay made even when
renamed. A plain cylinder or long push rod without two treated ends stays made.
Single-product files default to make. Nothing is silently excluded. A product
containing multiple solids is reported; this slice does not split unlabelled
compounds (an upstream reader change).

Unnumbered made groups precede bought groups; each category retains the first
occurrence order from haecceity's leaf traversal (`parts`, depth first from roots).
Explicit item numbers reserve their positive numbers first and must be unique;
remaining rows take the smallest unused numbers in category order. Row order
stays in category/assembly order even with item overrides. Item identity is
independent of inspection characteristic balloons and hole-table tags.

Each distinct group is recognised at most once; unknown-shape supply detection
reuses that recognition for any drawing. Each made group’s ordinary analysis,
recovered planning and rendering pipeline runs once. The assembly geometry and definitions are parsed once through
`haecceity::step::StepFile`; PMI attachment still reads each drawn part's PMI
from the shared bytes. A single-product single-placement file follows the
existing single-part read path exactly. No alternate feature recognition or
fallback drawing is used after a failure.

Pack furniture uses the existing ISO 7200 title-block reservation: the owner
cell shares its top-row space with ITEM and QTY, retaining document type and
all other fields. Only pack drawings receive these two cells. The default
assembly pack title is the product name; single-product files keep the ordinary
render title (including its file-stem fallback). An explicit nested drawing title wins. Page
size, scale, views, annotations and sheet count remain those of the nested
ordinary drawing Spec. CSV and JSON sheet references are one-based pages in
the combined PDF. The title block’s SHEET field is the global pack page/total
(e.g. `3/8`), excluding failed parts. Local view and annotation cross-references
retain the ordinary drawing numbering. Ordinary `render` is unchanged. A rare
rendering failure renumbers surviving sheets using their existing projection
caches, without reanalysing or reprojecting geometry.
The CSV has item, name, product keys, quantity, supply, buy reasons and sheets;
the JSON adds per-part problems, errors and the automatic-decision findings.

`pack::build` also returns `timings`; `pack::render_profiled` returns the report,
files and timings. CLI stdout includes these timings separately from `pack.json`
so the manufacturing report remains deterministic. Seconds cover shared file
read/parse, grouping (own-frame part reads and geometry comparison), and each
group’s analysis/recognition and drawing (planning plus rendering). Encoding and
file writes are outside those stages. Known bought names skip analysis unless
overridden to make.


Authored text accepts `⌀` (U+2300). The bundled IBM Plex fonts lack that code point;
the text renderer explicitly uses their supported `ø` outline and advance, matching the
slashed-o used by generated diameter symbols. Authored `⌀80` and `ø80` therefore have
identical ink and spacing in both bundled fonts. Searchable text keeps the authored Unicode.
Generated callout symbols retain their established one-em symbol cells and token spacing;
prose keeps the spaces the author typed.


### Pack bytes and JSON-lines operation

`pack::build_bytes(name, bytes, &PackSpec)` and `pack::render_bytes(name, bytes,
&PackSpec, formats)` accept STEP or gzip bytes without filesystem access. `name`
supplies the same filename stem as the CLI path. Path-based build/render use
these same implementations. Row errors/problems retain `key` and `products`;
per-part report findings carry `products`.

`draftwright serve` accepts:

```json
{"id":1,"op":"pack","step":"assembly.step.gz","spec":{},"formats":["pdf","csv","json"]}
```

`spec` defaults to PackSpec's defaults and `formats` defaults to all three formats.
The reply is `{"id":1,"ok":true,"result":{"report":<pack Report>,"files":[
{"name":"pack.pdf","mimeType":"application/pdf","blob":"<base64>"}, ...]}}`.
All files use base64 `blob` and `mimeType`, as the binary resource envelope does.
The report is the same shape as WASM `Pack.reportJson()` and CLI `pack.json`.
No files are written to disk by this operation. Errors use the ordinary
`{"id":1,"ok":false,"error":{"code":"invalid","message":"..."}}` envelope.

`step` also accepts a registered part ref from `mcp_inspect_part`; alternatively
pass `part_ref` in place of `step`. A selected-part registration uses its retained
whole source assembly for the pack. The registered filename and source hash are
preserved and validated. `parts` already accepts `{"op":"parts","step":"..."}`
and returns `{parts:[...]}`.

Page references are 1-based in exact PDF order: GA first when present, then
made-part sheets. Failed and bought rows have no sheets. The WASM Pack API is
documented in [wasm.md](wasm.md#assembly-drawing-packs).


### Part selection in the Spec

`part` selects one part before recognition and drawing, like `--part K`. Use the
persistent `key` printed by `draftwright parts`, or its numeric `part` index.
A command/request selector must agree with the Spec; conflicts are refused.
An Analysis supplied without its STEP must already describe the selected part:
it cannot supply another part's geometry. Explicit selection of the already
analysed principal part removes `assembly_principal_drawn`.


### Cylindrical-mouth pocket depth

A measured pocket depth says `DEEP`; a cylinder tangent is not a maximum allowed
limit. On the pocket's width callout, `dimensions/<id>/presentation` selects
`tangent_depth` (distance from the near cylinder tangent) or `far_extreme`
(floor distance from the opposite tangent). Both distances are derived from the
cylinder axis, radius and floor, including offset pockets.
For a 30 mm cylinder and 3 mm depth these state 3 and 27 respectively. The choices claim `pocket_depth_from_tangent.length` and
`pocket_floor_from_far_extreme.length`, never a fictitious 27 mm depth.
PMI requirements and lint remain independent: the alternate term does not claim
that an unstated source depth or toleranced requirement has been printed.
Unsupported or unplaced presentation requests retain the drawing and warn.


### Automatic-decision overrides

Every automatic drawing choice is a default: its plan `basis` names the Spec
controls, or explains the current limitation. The single inventory in
`src/decision_overrides.rs` covers the approved nine families: selection and
orientation; views; paper and arrangement; dimensions and origins; recognition,
grouping and attachment; wording and precision; tables and furniture; linework,
symbols and rings; requirements and checker conclusions. The inventory names
only accepted fields and explicitly identifies pending #309 capabilities.
References identify model geometry or drawing items, never absolute page points.
`from_spec` records influence; item state and problems describe unmet intent.
Part and depth presentation explicitly report application; an unsupported or
undrawn depth presentation warns. Requirements belong to PMI, and checker
conclusions have no presentation override. Basis reporting runs after the outer
planning operation, so it cannot influence the layout solve.

Basis guidance uses item-kind-specific paths: dimension callouts name `dimensions`,
views name `views`, and only PMI prose labels name `annotations`. Each dimension
lists its local controls once. Global recognition and callout defaults are reported
on the sheet, keeping item replies compact.

Lint verifies tangent-depth terms against the recognised cylinder and floor. A
far-tangent floor distance defines depth only alongside a stated diameter of the
carrying cylinder. Neither presentation invents a claim of the original depth.
Single-part Analyses omit `part_keys` on serialization, retaining their existing
schema shape; multi-part files keep the persistent selection map.
### Manufacturing process

Per-part `process` accepts `machined`, `3D printed: FDM`, `3D printed: resin`,
`3D printed: powder`, `3D printed: metal`, `sheet metal`, `moulded`, or `cast`.
Precedence is Spec > PRODUCT_DEFINITION's `specify process` UDA item
`manufacturing_process` > material-class guess. `default.manufacturing_process`
marks a suggestion, below a file answer. Plan `process` records the value, source
(spec/file/suggested/guess), and basis; pack rows carry process and its source.
Only the four `3D printing: filament/resin/powder/metal` material classes imply
printing; absent evidence means machined. Guesses need checking.

Printed general tolerances default to ±0.3 mm/0.3 % (FDM and powder),
±0.15 mm/0.15 % (resin), or ±0.1 mm/0.2 % (metal), whichever is greater.
The cell states “whichever is greater” on a second line and marks the value `CHECK`;
the requirement basis spells out the full default. Moulded/cast use ISO 20457 /
ISO 8062-3 without inventing a grade; a suggested or guessed process marks that
ungraded standard `CHECK`. `sheet/title/tolerance` overrides these, including an
explicit blank. Authored PMI finishes remain. Where no finish is authored,
printed parts omit the default finish; moulded/cast use a no-removal symbol
with “as moulded” / “as cast”. `sheet/default_finish` overrides the finish.
`process: machined` restores machining policy. Printed gear semantics are capped
at Warning; multi-solid Info findings stay Info. Other dimensioning lint is unchanged.
GA lists append Process beside Material only when a non-bought row has a Spec,
file or suggested value to print. Bought and guessed cells stay blank, including
guesses of printing. CSV `process` and `process_source` mirror JSON `process` and
`sources.process`; absent values stay blank. Made-part guesses remain labelled
in JSON/CSV, but bought parts get no invented process. In a pack, override a part at
`parts/<part-key>/drawing/process`, using its existing per-part drawing Spec.
Insert and printed-thread
hole callouts await Specify's hole functions and are outside this change.
