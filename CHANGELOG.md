# Changelog

All notable changes to draftwright-rust are recorded here, in the style of
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions follow
[Semantic Versioning](https://semver.org/).

## Unreleased

## 0.2.9 (2026-11-02)

Still on quiddity-rust **v0.4.3**. Fixes a 0.2.8 regression (#287): draftwright again draws the
basic datum-to-bore locations Specify writes when a bore and a hole pattern share a centre,
such as the 50 and 35 that locate the bore on draftwright-editor's mounting plate once Specify
has written its PMI. 0.2.8 refused them
with `pmi_not_lowered` ("plane-to-bore location has no proven face-supported axis witness").
Other drawings are unchanged. Instructions retired on draftwright-editor's four samples are
within 0.1% of 0.2.8, with the same problem counts.

### Fixed

- Recover basic datum-to-bore locations on a compound hole group when its
  symmetric cylindrical supports prove an actual central bore axis. Keep the
  authored datum and boxed value on the matching engine centre location instead
  of adding a duplicate;
  asymmetric groups, empty pattern centres and inconsistent nominals stay refused.

## 0.2.8 (2026-11-01)

Now on quiddity-rust **v0.4.3** (from v0.2.6). Draftwright draws the PMI that Specify writes as
authored (#281). Dimensions try other sides and references before crossing another feature, which clears the mounting plate's slot (#285). Graphical PMI is
reported only when no drawn semantic requirement covers it, replacing the blanket
presentation notice (#283). Feature control frames and
labels use haecceity's shared notation (#274).

Instructions retired to render draftwright-editor's samples, 0.2.7 → 0.2.8 (median of three,
interleaved): demo bracket 3.60 → 3.85 G (+7.1%), mounting plate 10.97 → 10.77 G (−1.9%),
NIST CTC-01 e1 42.71 → 39.95 G (−6.5%), spindle 4.43 → 4.42 G (−0.1%). Problems: demo bracket
3 → 2, mounting plate 6 → 2 (no extension lines through features), CTC-01 e1 32 → 31, spindle
3 → 3. All four keep their sheet and scale. Upgrading: plan and PMI JSON labels now write `⌀`
and `S⌀`, and PMI deviation pairs print as `+u/l`. PMI readings gain `callouts` and
`reader_problems` fields. New problem codes: `pmi_graphical_not_drawn` (warning, impact
incomplete) and `pmi_read_nonconformance` (warning). `pmi_presentation_not_read` is now emitted
only for saved readings without a callout inventory.

### Changed

- Retry crossed linear extensions on alternate sides and physical references (#285).
  Slot groups reserve clear local lanes, keep the length and end location
  together, and try opposite straight-wall width references. Full-round ends
  are witnessed at their actual extrema on the slot centreline. Grid pitches
  can use either claimed row/column, with their exact measured stations intact.
  Slot-width witnesses leave their own rim even on a box boundary and cannot
  skip a foreign opening. Compact local candidates retain full geometry checks;
  automatic slot-layout callouts get at most eight shoulder-only text alternatives
  without changing the joint-search budget. Failed ordinary crossing retries keep
  their existing lane unless they remove a foreign-feature contact. Measurements
  and pins stay intact. The FreeCAD gantry plate and the build123d NEMA-17 bracket
  move from A4 1:1 to A3 2:1; the gantry plate keeps 10 reported crossings.

- Pin haecceity and quiddity v0.4.3 (#284; recognition /6, fingerprints /3, PMI reader /14).
  A constituent face aspect aliases its datum feature only when their complete face sets
  match; multi-face datum constituents retain their own feature and dimension references.
  The shared writer now bounds generated STEP strings and reports retained long strings,
  and generated presentation keeps sizes and frames together with reserved label ink.
  Draftwright continues to lay out drawings from semantic requirements.

- Triage graphical PMI callouts by their part-qualified semantic links (#283). Drawn semantic
  requirements need no presentation notice; refused requirements retain the callout ID and
  decoded name on their existing findings. An annotation with no resolved requirement gets
  one incomplete warning, including when its links identify only supporting geometry.
  Exact reader-refused definitions reuse their existing source findings. Older saved readings
  without a callout inventory retain an explicit presentation-count fallback. Triage runs
  after layout selection and does not change the drawing or its paper/scale policy.
  Existing legacy manufacturing associations and #265 engine carriers retain semantic
  coverage; triage does not introduce new name or face matching.
  Drawing-lint callout references remain evidence of the original semantic finding,
  rather than being counted as extra semantic sources.
  Native thread, knurl and surface-texture links use the definition roots their lowerers
  already use, including when extraction retained a refused source instead of a record.

- Pin haecceity and quiddity v0.4.2 (recognition /6, fingerprints /3, PMI reader /13).
  Preserve non-fatal reader findings for repaired reversed limits/deviations, source
  identifiers and orientation-independent zones without refusing their valid semantic
  requirements. An incomplete recognition document is explicitly refused, rather than
  silently treating a failed family as absent. Datum-feature child aspects use their
  owning datum feature as supplied by the reader.
  The recognition audit adds eight reviewed `corner: null` differences for CTC-01,
  FTC-06 and m11512: v0.4.2 refuses virtual corners whose published wedge is unproved;
  their defining faces and measured legs/angles remain intact.
  Face-finish adapters now supply explicit micrometres to the shared ISO 1302 formatter,
  preserving `Ra 0.8` when the renderer retains input units. A withheld virtual corner
  no longer covers an independently proved notch depth; that depth remains explicit.
  Interrupted torus groups change radius counts and run-local IDs; route audits retain
  their physical defining faces instead of pinning the provider's occurrence numbers.

- For #258 (#274), pin quiddity-rust and haecceity v0.2.7. Imported PMI deviation pairs now print on
  one line as `+u/l` (ISO 129-1:2004 §6.2), haecceity's shared notation, instead of `+u -l`
  (`⌀62 +0.03/0`); authored Spec tolerances keep their `+U -L` form. The quality parts'
  drawings are byte-identical. On PMI parts the slash, whose ink stands taller than the digits,
  moves some callouts: nist_ftc_07 wraps a callout after its size, nist_stc_09's two-hole
  position frame stands over its holes' `2×` callout, string_post_tapped's `⌀1.7` callout stays
  with its frame (see #282), and nist_ctc_01's `⌀35` callouts go to the
  right of their leaders.

- For #258, feature control frames, labels, surface texture symbols and material removal marks
  come from haecceity's shared notation, measured with the sheet's IBM Plex widths, and the fit
  table's classes, grades and largest size are haecceity's. Draftwright's copies are deleted
  (the frame symbol table, characteristic strokes and frame layout, the surface texture layout,
  `title_block::removal_marks` and the fit class lists). Placement, label lanes, leaders and
  drawn-precision rounding stay Draftwright's. Drawings are byte-identical.

- Draw the PMI Specify writes as authored (#281). Coplanar multi-face aspects are measured as
  plane-normal distances, with witness lines on real trimmed faces, instead of between bounding-box
  midpoints: spindle 19's 4.9 is no longer measured as 18.27. A nominal-only dimension measured
  from a datum is drawn as authored, not restated as an engine chain, because restating it
  would loosen the tolerance through stack-up; dimensions without a datum keep the #265
  replanning. Specify's `specify pmi origin` record is read as bookkeeping. Characteristic numbers
  attach to the dimension that draws them, and inclined-bore fits apply when face and axis
  ownership is proven. On 21 parts written by specify-core, `pmi_not_lowered` falls from 22 to 0
  and problems from 515 to 233. Legacy Specify 0.1.4 files follow the same datum rule, so the
  legacy plate grows from A3 to A2; re-saving with current Specify avoids that.

- For #258, an imported dimension's label states a diameter as `⌀` and a spherical one as `S⌀`
  (ISO 129-1:2004 §7.2), as haecceity does, where it wrote `ø` for both. Drawings are
  byte-identical (the sheet already drew `⌀`); the PMI and plan JSON labels and the problem
  messages quoting them change.

### Fixed

- A hole's size label stays stacked with its position frame (#282). The size label grew taller
  with the slash deviations, and the late label solve then pulled it away from its frame onto
  a long leader. It now searches up to 512 radial positions for the whole frame-and-label stack.
  string_post_tapped's leader goes from 58.9 mm back to 24.5 mm; all 17 attached pairs stay
  attached.

## 0.2.7 (2026-10-31)

Still on quiddity-rust **v0.2.6**. Planning a drawing with PMI takes about a third fewer
instructions (#276), and drawings no longer leave empty rungs in dimension ladders, state an
assembly's shared general note twice, or lose inspection balloons in large views (#277).

### Changed

- Recovery tries the note form once per drawn callout, not once per owner: a thread several
  bores share is stated by one counted callout, and a callout a hole table states cannot fit
  better in another form. On draftwright-editor's Plate sample (two M6 bores) planning with PMI
  took 44.1 G instructions retired in 0.2.6 and now takes 31.8 G (0.2.4: 23.5 G), with the
  same plan.

### Fixed

- A dimension the part's PMI conveys (a basic location the PMI states twice over one span, or a
  recognised hole's location its rooted location network states) no longer takes a lane of its
  own. It shares its survivor's lane, as other merged dimensions do, both when the dimensions
  are placed and when the views' bands are reserved. Before, each covered item kept an empty
  rung: on the `assembly_plate_pin` PMI fixture the ladder above the plan view stood 25 mm from
  the view with rungs 28, 14 and 14 mm apart (and 30 and 45 beside it were 14 mm apart). Every
  rung is now one lane pitch from the last, and with the ladders no longer inflated the fixture
  fits A4 at 1:2 (drawn-item fill 36.5%) where every candidate used to be refused and it fell
  back to A3 at 1:2 (fill 22.7%).
- An assembly drawn whole states a general note once when its parts state it alike: the copy
  is kept in the plan as covered by the drawn note, and omitting the drawn one brings it back.
  A note only some of the parts state names them (`(PART PLATE)`). `assembly_plate_pin` listed
  "Break sharp edges 0.2 max" twice, once for each part. The PMI coverage check reads the
  copy as stated by the drawn note, and a note's part names as its own.
- An inspection balloon whose carrier stands deep inside a large view can take a ring just
  outside that view, square to the carrier, its leader crossing the view's interior. Before, rings
  were tried only within 52 mm of the carrier, so on `editor_pmi` drawn at 2:1 two numbers had no
  place.
- Inspection balloons are placed once the paper policy has chosen the scale. When some numbered
  characteristic then has no clear place for its balloon, the next smaller standard scale on the
  same sheet is tried, and kept only when it places more balloons with no more hard layout
  findings and no more leader crossings. An explicit sheet/scale is kept as asked.
- A balloon grown from another ring of the same compound callout keeps the usual clearance from
  that ring after its leader's first bend: on the Specify 0.1.4 plate drawn at 1:1, one such
  leader ran along the bottom of the ring it started from.

## 0.2.6 (2026-10-31)

Still on quiddity-rust **v0.2.6**. The PMI report now lowers each record to the item that draws
it, so consumers no longer see drawn PMI as unresolved (#265).

### Fixed

- For #265, the PMI report (`Engine::pmi`, the session's `pmi_report`) lowers each PMI record to
  the item that draws it. A Specify general-tolerance dimension is lowered to the planners' item
  that conveys it, or to the planned dimension made for one no item conveys. And any record whose
  item is not drawn because another states what it would (`covered`: a hole callout the hole
  table replaces, a level other dimensions determine) is lowered to that item, with kind
  `covered`. Such records were listed as `unresolved` ("has no supported drawing
  representation") although drawn, which the editor shows as PMI missing from the sheet: its
  Plate sample with Specify 0.1.4 went from 35 issues to 8.

## 0.2.5 (2026-10-31)

Now on quiddity-rust **v0.2.6**. Adds assembly drawing packs with a general assembly front sheet
(#41), and Specify's own characteristic numbers on inspection balloons (#254).

### Added

- For #41: a general assembly front sheet for drawing packs, with overall sizes,
  visible source-owned item balloons, a bottom-up parts list above the title block,
  global sheet numbering and pack Spec overrides for GA presentation and references;
  reference envelope sizes, consistent projection, visibility-ranked item dots, nested-item
  sections/details, per-item hatching, cylindrical centre lines, generic-root title fallback,
  and reused product projections, depth samples, cut contours and recognised thread carriers.

- For #41: assembly drawing packs through the library and `draftwright pack`, with conservative geometry grouping, re-export-stable name keys, quantities, name/feature-based make/buy guesses and strict intent overrides, item/QTY and global SHEET title cells, a multi-page PDF, CSV parts list and per-part JSON findings and failures.

### Changed

- For #254 and quiddity-rust#143, pin quiddity-rust and haecceity v0.2.6. Recognition schema
  `/6` and fingerprints `/3` are unchanged; the dependency graph has one Draftwright copy.
- For #254, read Specify's characteristic numbers from each requirement's own `ID_ATTRIBUTE`,
  retaining the interim UDA fallback. Report conflicting numbers (id wins) and duplicate ids
  (no number). Extend rings to surface textures, including the default symbol, thread/knurl
  callouts and notes explicitly numbered on their own requirement. General-tolerance dimensions
  keep their numbers on the items credited by #265, including covered intervals. A shared
  internal-thread requirement reaches every individually proven hole, with a counted
  callout or numbered schedule rows. Keep established native hole schedules when recognizing
  shared threads, retain their native drilling sizes beside the thread statements in the rows,
  and retain size callouts when replacing them would add frame-leader crossings. PMI JSON fields retain id metadata;
  WASM signatures and MCP tools/list shapes are unchanged.

- For #258, fit and band notation (the ISO 286 class table and its limit deviations, stacked
  deviations, `±T` and `+U -L` bands), the datum feature symbol's frame and part-note wording
  come from haecceity's shared `presentation` module, and Draftwright's copies are deleted.
  Drawings are byte-identical. Feature control frames, labels and surface texture symbols keep
  Draftwright's layout: haecceity measures their cells with its Hershey STEP lettering, not the
  sheet's IBM Plex, and does not expose the frame layout. Sheet text rendering (`src/text.rs`)
  stays Draftwright's.

- For #265, Specify's general-tolerance dimensions (nominal-only PMI marked
  `specify-core:written:v1`, from specify-core-rust 0.1.4) no longer flood the drawing. One that
  matches a planned dimension is credited to it. One that matches nothing is drawn as an ordinary
  planned dimension, without PMI precedence. Strict mode still draws them all as PMI. Specify's
  `specify characteristic numbers` history set is skipped as bookkeeping.
- For #256: dimension gusset groups with counted thickness and legs, pitch or spacing, and box location in true views; allow a per-group transverse view override. MCP Spec discovery includes `features/<id>/gusset_view`; tools/list and WASM signatures are unchanged.

## 0.2.4 (2026-10-30)

Now on quiddity-rust **v0.2.5**. Adds optional inspection balloons for numbered AP242 characteristics (#254).

### Changed

- Pin quiddity-rust v0.2.5. For quiddity-rust#134, boss heights reach the free end through convex chamfers and rounds, edge-treated plates retain their thickness dimensions, and concave turned fillets remain in counted radius callouts. The recognition schema (`/6`) and fingerprint format (`/3`) are unchanged, but fingerprint values of bosses with end treatments and of edge-treated plates change.
- Optional inspection balloons read file-owned characteristic numbers from AP242 UDAs on
  dimensions, geometric tolerances and datum features. They are off unless
  `sheet/balloons: all` is set. `balloons/occurrences: true` repeats a number at each proven
  occurrence; `balloons/items/<source>/suppress: true` removes a ring and
  `balloons/items/<source>/position: [dx, dy]` pins it (paper mm, relative to its carrier),
  where `<source>` is a PMI ID such as `dimension:#640` or a plan-item ID. Items (including
  MCP items) carry `characteristic_numbers`, and the plan lists each number's sheet and drawn
  carrier (`characteristics::list`, `Session::numbered_characteristics`). Missing carriers
  and rings are reported with incomplete impact. Known limit: surface textures and semantic
  notes have no number owner in the AP242 reader (haecceity 0.2.5) and draw unchanged.

### WASM API

- Add `Session.numberedCharacteristics()` returning file-owned characteristic numbers and
  their original item, drawn item, sheet and expected inspection rings as JSON.

## 0.2.3 (2026-10-30)

Now on quiddity-rust **v0.2.3**.

### Changed

- Pin quiddity-rust v0.2.3. For quiddity-rust#123, use complete turned bodies for overall diameters and half-section eligibility, and call out annular face recesses with rim diameters, depth and sloping wall angles. For quiddity-rust#127, draw cylindrical profiles as grouped radius callouts and include concave fillets with equal edge rounds.

- Feature control frames on shafts, bosses and other cylinders use the hole-frame attachment
  order: their own size callout or diameter dimension where there is room, otherwise a radial
  leader to their own end-on rim, visible before hidden. Leaders avoid centre-line travel and
  off-axis cylinders; different cylinders cannot share an axis-point leader. CTC-05's circularity
  and concentricity frames now identify their own surfaces. Explicit annotation placements
  override this choice; hidden-rim fallbacks retain `leader_to_hidden_edge` warnings. An explicit
  zero view gutter keeps the selected reservations for new rim repairs. Existing hole-rim
  attachments may retain only their own short centre-mark overrun as a last resort.
- Tables taller than the drawable sheet height split into side-by-side blocks with repeated
  headers, retaining row order and information. Sheet selection considers those forms;
  `sheet/table_columns` can fix a table's column count or forbid splitting. Tables that fit
  keep their existing layout. Tall-table repacks retain the same sheet and source datum
  findings. NIST CTC-02 can use one A2 sheet at 1:5, retaining every row, note and datum symbol.
- A hole callout with more than one counterbore or spotface step reads one step per line: the bore
  on the first line, then each `⌴` or `⌴SF` step on its own, innermost first (`⌀6 THRU` /
  `⌴ ⌀12 ↧ 3` / `⌴ ⌀20 ↧ 2` / `⌴SF ⌀30 ↧ 0.5`). A hole with one recess keeps its callout, and
  hole-table rows still read on one line. No dw-examples or quality-set drawing has such a hole.
- A frame or datum symbol no longer keeps a PMI hole callout beside its hole table row
  (maintainer decision). Where a frame on holes has no clear place on their size callout or
  tag, it takes a leader to one of the holes in the view that sees them end-on: the arrowhead on
  the bore's circle, the leader radial to it and clear of every other hole. A frame on several
  holes leads to the hole their callout points at and carries their count over it (`4×`, as
  ISO 1101 states it). A datum symbol with no clear place on its mark stands on such a frame of
  its hole. The rows then replace the PMI callouts as they do elsewhere. The callouts stay only
  where a frame or symbol on a replaced callout can attach in no other way. A leader to a hole
  may pass through material no more often than the leader it replaces. On dw-examples, FTC-10
  loses `3× ⌀3.5 ±0.2 THRU`, `4× ⌀6 ±0.15 THRU ⌴ ⌀10 ↧ 2` and `8× ⌀2.5 ±0.15 THRU`. Its
  cylindricity and `⌀0.0Ⓜ` position frames lead to the ⌀7 holes, and datum C stands on its
  hole's position frame. CTC-05 loses `⌀254 ±0.0254 ↧ 92.08` beside row B1, and its total runout
  frame leads to the bore with datum B on it. STC-10's cylindricity and the position frames on
  its ⌀7 holes, ⌀2.5 holes (`8×`) and datum C's hole lead to their holes. So do FTC-07's `2×`
  ⌀1.143 position frame, which was led to a different hole, and CTC-04's ⌀0.35 frame on B5.
  Leader crossings: FTC-10 111 to 118 (its freed layout), STC-10 97 to 92, FTC-07 31 to 26.
  Leaders through features: STC-10 53 to 38, FTC-10 34 to 33, CTC-04 24 to 22. The FTC-10
  AP242 fixture's leader-crossing record is re-recorded (leader to leader 42 to 46, to
  extension line 46 to 50, to arrowhead 26 to 29). Lint reads a frame's count past, as it does
  its symbol, when checking the frame states its source.
- A frame on holes attaches in this order (maintainer decision, 2026-10-29). First, as before,
  its holes' size callout or tag. Next, a leader to one of its holes that the end-on view draws
  visible: ASME Y14.2 and ISO 128-22 end leaders on visible outlines. A hole counts as visible
  when no planar face of the part lies nearer the viewer over its opening, or over its exit for a
  through hole. Then the callout kept beside its hole table row. Last, a leader to one of its own
  holes drawn hidden, with a `leader_to_hidden_edge` warning. A frame never keeps a leader to its
  faces that ends on another hole's centre: such a frame re-aims its own leader at the rim of one
  of its holes, or takes a new one, and either may cross other leaders as often as the old one
  did. A second frame on the same holes may stack under the first on its leader. Leader bends now
  reach up to 85 mm past the rim (was 48 mm), and a frame's leader no longer ends where another
  leader ends.
  - CTC-05: `⌀254 ±0.0254 ↧ 92.08` is back beside row B1, with the total runout frame and
    datum B on it. That callout's `leader_to_hidden_edge` warning returns, as in 0.2.2.
  - FTC-07: the four position frames on the ⌀15.082 holes all lead to their own holes, each with
    its `2×` count. Before, three of them led to the middle of their two holes' faces, which is the
    centre of a hole of the other pair, and the fourth led to a dashed circle with no warning. The
    far-wall pair's stack (`⌀1.143`, with `⌀1.016Ⓜ` under it) leads to a dashed hole and warns.
    Leader crossings go from 26 to 17.
  - STC-09: the `⌀0.508 A D B` position frame on two holes leads to one of them, where before its
    leader to its faces ran about 130 mm to another hole's centre. Leader crossings go from 302 to
    277, ink overlaps from 34 to 28, and leaders along a centre line from 4 to 3.
  - FTC-10, STC-10 and CTC-04 are unchanged; their frames already lead to visible holes.
- When moving the isometric to a second sheet clears sheet 1, the isometric now comes back to
  sheet 1 if the finished sheet has room for it. It is centred in the largest clear area and may
  shrink to 0.6× (drawn NTS), as on a single sheet. A sheet with nothing but the isometric is now
  a last resort. FTC-08 (rc) moves from A1 to A2 at 1:2 and its `detail_unplaceable` warning
  goes. The dw-examples CTC-04 moves from A0 to A1 at 1:5. Both are still one sheet. A sheet holding
  the isometric and a table is no longer refused as pictorial-only.
- Each refused paper request in the sheet-choice basis now names the check that refused it,
  such as `a sheet carries only the pictorial view`, `required items not carried`, or the new
  unreadable finding codes. Before, these refusals all gave the same generic text.
- The final gutter pass now keeps the isometric 6 mm from a table, not 2 mm. Its bounds are drawn
  ink rather than a padded band, so 2 mm left it almost touching the table (FTC-08). Seven drawings
  move their isometric 3 to 4 mm; nothing else changes.
- The automatic half section covers more turned parts. Besides a coaxial counterbore, spotface or
  countersink, it now fires when the bore changes diameter along the axis, when a groove is turned
  inside the material, or when a through bore runs under an outer groove, as on a bush or linear
  bearing. The rule is whether the section shows something hidden lines cannot show cleanly: a
  plain bore under a plain or merely stepped outline stays a plain view (gt2-pulley's ⌀5 bore,
  t8-flange-nut). dw-examples' lm8uu now draws its side view half cut, the thin wall under each
  ⌀13.4 groove a section edge. No other dw-examples drawing or quality part changes;
  in the wider corpus, cadgenbench/threaded_connector_109 now draws its front view half cut too.
  `views/<name>/half_section` still forces (`true`) or forbids (`false`) it.

## 0.2.2 (2026-10-29)

Now on quiddity-rust **v0.2.2**.

### Changed

- A near-side hole step deeper than 1 mm is now a counterbore, not a spotface. For
  quiddity-rust#118. lm10uu reads `⌀10 THRU ⌴ ⌀16.4 ↧ 1.4 BOTH SIDES`, and the CTC-02 bores read
  `⌀40 THRU ⌴ ⌀120 ↧ 8`. A hole's further counterbore steps (`outer_steps`) are no longer lost.
  Each is called out once, as another `⌴ ⌀d ↧ depth` after the counterbore, reading from the
  bore outward: `⌀6 THRU ⌴ ⌀12 ↧ 3 ⌴ ⌀20 ↧ 2 ⌴SF ⌀30 ↧ 0.5`. Each depth is that step's own,
  as quiddity measures it. Lint requires and checks each step's diameter and depth.
- Straight convex rounds between tangent walls are fillets at any wall angle (quiddity-rust#120).
  Many former blends are now called out as `n× R` fillets, and some `ALL UNSPECIFIED RADII`
  notes change radius or give way to callouts. No radius goes unstated. An oblique fillet is
  counted on the callout of equal principal fillets, or called out where its edge is seen most
  nearly end-on, so it no longer withdraws the radius note. CTC-05's four R203.2 faces are no
  longer fillets in quiddity, so their callout goes.
- A capped drawing that has left its established paper now takes an uncapped drawing at a larger
  scale on paper no larger, unless that drawing has more hard findings, drops more or fits worse.
  STC-08 stays on A1 1:1.

### WASM API

- `Engine.openPart(name, bytes, part)` opens part *part* of an assembly (an index as `parts`
  lists them) alone, in its own frame and with its PMI only, as the command line's `--part`
  does. `new Engine(name, bytes)` is unchanged.

## 0.2.1 (2026-10-28)

Still on quiddity-rust **v0.2.1**. This release carries three rounds of fixes from a sheet-by-sheet
review of the 35 dw-examples drawings. Turned parts whose bore steps inside (a counterbore, spotface
or countersink on the axis) are drawn as half sections; `views/<name>/half_section` forces one on or
off. Cut profile corners are located by their legs, and a 45° corner chamfer is called out by its
size (`4× C21.075`). Congruent obround slots are dimensioned as slots (`N× SLOT W × L`), and
`features/<id>/split` dimensions a full-round slot by its two end centres instead. Where a part has
a hole table, a table row replaces a PMI size callout and carries its tolerance and fit; a wrapped
callout keeps `↧ 10` together. Cited datums on a width or read through a nonconformance are drawn, a
rectangle of holes is no longer called out as a bolt circle, a location stated twice by model noise
is drawn once, and a boss on a hole's location line counts as located. Turned parts are not located
from a silhouette, and a recess at both ends says BOTH SIDES. Sheets are judged by their drawn
items, not an empty band past the border (#163). A modelled thread quiddity refuses only because a
chamfer splits its flanks (the T8 flange nut's Tr8 bore) is measured and drawn, an assembly's
drawing notes the parts it leaves out, PMI angles print `°`, and a PMI label that is a lone symbol
is refused instead of drawn as an empty box.

Over the 94 corpus parts and the 23 PMI fixtures, against 0.2.0: errors fall from 226 to 218,
unlocated features from 209 to 203, lines through features from 392 to 379, leader crossings from
691 to 669, and warnings other than `shape_not_recognised` from 2198 to 2115; claimed faces (6873)
and `shape_not_recognised` (156) are unchanged. On the dw-examples drawings: NIST FTC-08 gets its
hole table and loses its overlapping labels and shared dimension line (158 warnings to 105, leader
crossings 81 to 52), STC-06 draws its datum B, so 12 more of its frames have every datum drawn,
STC-08 keeps A1 1:1 and its hole table and draws its datums A and D, STC-10's hole sizes gain their
tolerances and fit (`⌀3.5 G6`), and CTC-01 draws `225` once.

**Speed.** Instructions retired for `draftwright plan`, this release against 0.2.0 (median of
three interleaved runs each): 0.90× on FTC-08, 1.01× on CTC-01, 1.00× on string_post and 1.45×
on cgb245 (see below).

### Known regressions

Measured on this release against 0.2.0, on the same parts.

- **cgb245 plans 1.45× slower.** The cost arrived with #241's congruent slots dimensioned as
  slots; its drawing improves (lines through features 3 to 1).
- **Leader crossings on one part:** NIST FTC-09 (0 to 1: a slot callout's leader crosses one
  slot location's extension line). STC-10 AP242 has one more leader through a feature (lines
  through features 66 to 67). On dw-examples, FTC-06 (3 to 14) and STC-07 (4 to 12) gain
  crossings around their newly drawn datum symbols.
- **Sheet changes:** 10046 A4 2:1 to A4 1:1 (with one `detail_unplaceable`), 1017 A4 2:1 to A4 1:1,
  408 A4 1:2 to A4 1:1, 10155 A3 2:1 to A4 1:1, 274 A3 2:1 to A4 2:1, sm-hanger A3 1:2 to A4 1:2,
  388 A3 2:1 to A2 5:1; on dw-examples, vslot-2020x50 A4 1:1 to A3 2:1.
- **nema17-mount-bracket** goes from A3 1:1 to A4 1:2, its `40` crossing two ⌀4 holes.
- **Not yet fixed:** LM10UU's end recess still prints `SF` (quiddity-rust#118), and 608ZZ is not
  recognised as turned, so it gets no half section (quiddity-rust#123). The quality-corpus copy
  of NIST FTC-08 (rc) stays on A1 1:2 with `detail_unplaceable`, as on 0.2.0, where A2 1:2
  should fit.

### Added

- **Half sections for turned parts with internal steps (ISO 128-50).** A part turned whole about
  one axis whose bore steps inside (a coaxial hole with a counterbore, spotface or countersink)
  draws its profile view half as seen and half cut on the axis, hatched, with no hidden edges and
  no caption or cutting-plane line, unless the plan already has a full section. The cut half is
  the side the view's lengths are dimensioned on; witnesses on internal edges move to their
  mirror in the cut half, and an internal diameter becomes a half dimension (one arrow on the cut
  edge, its line past the axis, its witness from the repeated edge nearest the dimension line),
  which lint's label-against-measurement check reads from the axis. Centre lines of holes the
  half section cuts away go with them. dw-examples' bearing-lm10uu shows its ⌀16.4 × 1.4 recesses, ⌀11.2 × 0.7 rings and ⌀10 bore
  as section edges instead of overlapping hidden lines, its `2× ⌀11.2` and `0.7`s on them;
  cadgenbench's flanged spool 132 shows its ⌀62 bore and ⌀70 ↧ 10 spotface cut in the right half
  of its front view. No other dw-examples drawing or quality part qualifies (lm8uu's and 608zz's
  bores are plain). The
  Spec's `views/<name>/half_section` forces (`true`) or forbids (`false`) it; forcing it on a view
  with no turned profile seen side on leaves the view as it was with a `section_not_shown`
  warning.

### Changed

- **A 45° corner chamfer is called out by its size (#217).** A cut across a right corner of
  principal edges whose two corners are both 135° (equal legs) now gets `C<leg>`, the edge
  chamfers' house form, on a leader to the cut, and equal ones on a face share one counted
  callout; only the principal edges' missing offsets get stations, never the legs. Lint credits
  the callout with both corners' 135° when its size is the cut's leg and locates the cut from
  those offsets. dw-examples' FTC-08 octagon gets `4× C21.075` with `2× 12.827` and `2× 11.938`
  in place of four counted stations (`2× 33.902` and `2× 33.013` go): its plan view's left and
  lower lanes empty, its hole table now fits (main: `hole_table_unavailable`), and
  `extension_through_feature` goes 32 to 13, `annotation_ink_overlap` 24 to 3,
  `leader_through_feature` 35 to 19 and `label_crowded` 15 to 3 at the same A2 1:5; the table
  brings the ten `hole_pattern_requirement_missing` and one `feature_not_located` warnings that
  STC-08's table drawing already has. l-plate-5-holes gets `C40` for its two `20`s; the NEMA 17
  bracket's chamfers `2× C4` twice for `4`, `2× 4`, `2× 4` and `4`, and it moves from A3 1:1 to
  A4 1:2. STC-08 gets the same callout and stations and stays on A1 at 1:1: a recess drawing
  whose search under the established drawing's scale cap had to leave that drawing's paper for
  larger now also tries the search without the cap, as an exhausted one already did, and takes it
  where it reads better (the capped search had settled on A1 1:2 at 19% fill, beside the
  established A2 1:2, with `extension_through_feature` 9 to 30). A profile corner station that a
  re-seated recess location lands on moves out lane by lane to a line of its own (FTC-08's
  `2× 11.938` had shared the line of the 64.999 seat location, a `dimension_line_shared` error).
  The FTC-08 quality part goes from A2 1:2 back to A1 1:2 with its `detail_unplaceable`, as
  before the stations reserved their lanes. Naming either
  corner's angle in `dimensions` keeps that cut located by its legs, and
  `faces/<face>/profile_corners` (`linear`, `angle`) still chooses for the whole profile.
  Station IDs are numbered among the remaining stations (FTC-08's `…f1_0_3` is now `…f1_0_1`).

- **A hole table row replaces a PMI size callout, and a wrapped callout keeps `↧ 10` together
  (maintainer decisions).** Where a schedule exists, its row now replaces the callout of a hole
  whose size comes from PMI, as it already replaced a native one: the row prints the callout's
  text verbatim (band, fit class or thread) and carries its PMI claim once, on the first row of
  its holes, so every PMI size is still stated once and no longer twice. dw-examples' STC-10 loses
  its `4× ⌀6 ±0.15 THRU ⌴SF ⌀10 ↧ 1.8`, `3× ⌀3.5 ±0.2 THRU`, `3× ⌀3.5 G6 THRU` and
  `8× ⌀2.5 ±0.15 THRU` beside rows E, D, C and A, and STC-07 three. Whether an automatic schedule
  is drawn on a PMI part is still decided by the native callouts it frees, and the schedules are
  installed again with the PMI callouts kept where replacing them would add an error the drawing
  without schedules lacks (FTC-08's schedule would fit but put `64.999` and `2× 11.938` on one
  dimension line), or would detach a frame or datum symbol that attaches to a hole's mark with
  them kept (a row has no place for a frame on several holes, and a tag may have no room). So
  FTC-10 keeps its cylindricity frame beneath the `4× ⌀7` callout and CTC-05 its datum B and
  runout frame beneath `⌀254 ±0.0254 ↧ 92.08`, both with their PMI callouts as before. When a callout wraps, a depth,
  counterbore or countersink symbol now moves to the next line with its value instead of ending a
  line, a departure from draftwright's 48 mm wrap: STC-06's three counterbore callouts break
  before `⌴`, and FTC-10's and STC-10's `⌴ ⌀10 ↧` / `10 BOTH SIDES` read `⌴ ⌀10` / `↧ 10 BOTH
  SIDES`.

- **`features/<id>/split` on a full-round slot dimensions it by its two end centres.** #241 made
  congruent obround slots one `N× SLOT W × L` callout; a split slot used to fall back to its
  width, length, `2× R` and low-end location. It now states its width, its low end's centre
  located from the part's box, and the centres' distance apart (`slot_centres.length`), as two
  arcs located, with no length or end radius (the width and the located centres fix the
  full-round ends), and a centre mark at each end centre for those dimensions to end on;
  review asks the same of it. On dw-examples' gantry-plate, splitting both long
  slots gives `2× 20` between centres that stand on the hole columns (the low centre is located by
  the `22.75` hole location) in place of `2× 25`, `20.25` and two `2× R2.5` leaders; on
  nema17-mount-bracket, `10` and `2× 30` in place of `8`, `2× 34` and two `2× R2` (and the
  sheet goes from A3 1:1 to A4 1:2). A keyhole, straight or not-full-round slot split this way
  is still dimensioned singly. The editor now offers `split` on a lone slot. No plan of the 65
  quality and dw-examples parts changes.
- **A cut corner is located by its legs, and an angle with no position is reported (#217).** Two
  angles fix an edge's direction, not where it lies: dw-examples' l-plate-5-holes printed `135°` at
  both ends of its 45° cut and neither leg. A cut across a corner of principal edges now gets its
  missing stations, each from the nearer side of the box and drawn on the side its corner lies on,
  so the witness leaves the part at once, instead of angles (the l-plate gets `20` above and `20`
  on the left, not through the plate; a `dimensions/<id>` side places one elsewhere; a station
  on such an open side reserves its lane when the sheet is laid out, which takes mfcadpp/10046
  from A4 2:1 to A4 1:1 for its `18.759` on the side view's left, with two more of its corner
  angles dropped and one `detail_unplaceable`; the NEMA 17
  bracket's motor-plate chamfers `2× 4` and `4`, where two of their angles had been dropped;
  10049's cut is located). A plate's face is now hidden only by a nearer face of
  the same plate, so the bracket's base-plate chamfers, behind the motor plate's end face and never
  reported, get `2× 4` and `4` too; a notch's flank corners are left to the notch (CTC-01's plate
  adds nothing; 1013's `2× 113.974°` and 10020's `100.614°` beside their notches go). The counted
  angle needs a regular polygon: FTC-08's and STC-08's chamfered-rectangle octagon loses `8× 135°`
  for its stations (`profile_corners: angle` keeps the counted angle). Two equal stations from
  opposite sides of the box are stated once, `2× v`, and lint reads a counted station as locating
  its mirror: the symmetric octagon gets four (`2× 12.827`, `2× 33.902`, `2× 11.938`, `2× 33.013`),
  not eight in repeated pairs; FTC-08 stays at 1:2 (dw-examples' copy on A2 1:5) and STC-08
  on A1 1:1 with its hole table: where a first estimate places no table and its established scale
  cap leaves nothing valid, the search runs once more without the cap, as it already did for a
  table (without it, STC-08 with the cited datums drawn went to A2 1:5 with no table and 51
  crossings). STC-08 costs two more `extension_through_feature` (7 to 9) and one
  `extension_off_centreline`. Where a linear solve leaves two dimensions on one dimension line, it
  is solved once more with the new recess witnesses kept off the lines of the settled locations
  that yielded to them (a yielded location comes back where it was and may find no other clear
  lane), and taken where fewer lines are shared. On a part with section recesses every station
  reserves its lane when the sheet is laid out, not only one on an open side: the recess locations
  yield to the callouts and come back around them, so a strip left a station short overflowed
  across the view into the callouts' place (dw-examples' FTC-08 piled `2× ø10.16 × 3.429 LONG`
  and `4× ⌀5.410 THRU` on `157.709`, `49.124` and `136.754` right of its plan view). The plan
  view now stands clear of its left-hand locations, which all stay on that side, and FTC-08 has
  no overlapping labels or shared lines (main: two overlapping pairs, `225.654` on `215.494`'s
  line and `2× ⌀6.147` on `2× ⌀6.35`), with `annotation_leader_crossing` 81 to 67 and
  `leader_through_feature` 38 to 35, but the left baseline's long witnesses cost
  `extension_through_feature` 25 to 32, `annotation_ink_overlap` 4 to 12 (seven of them witnesses
  running through the `192.23` and `158.75` slot labels), `extension_off_centreline` 1 to 3 and
  two `leader_across_view`. The FTC-08 quality part goes from A1 1:2 to A2 1:2 and loses its
  `detail_unplaceable`, with one more `label_crowded`; no other quality part or dw-example
  changes with this reservation.
  A settled location whose witness would cross a feature now starts it beyond the near face, as a
  corridor repair would: FTC-08's `141.827` slot location stays clear of the 12.7 slot, and CTC-02
  rc (3) and mfcadpp/1004 (1) lose their `extension_through_feature` findings. vslot-2020x50's slot
  openings get `2× 1.8`, `2× 5.15` and `2× 6.95` for their sixteen angles and move from A4 1:1
  (five leader crossings) to A3 2:1. FTC-08's fillet callout reads `8× R1.524`, not `8× R1.5` (the
  new route loses the established one-decimal text; the retention check in `tests/label_lanes.rs`
  now uses mfcadpp/1004); FTC-08 gains one `label_crowded`.
  `profile_corners: linear`, or naming one station in the Spec, states each station on its own. A
  45° corner chamfer gets its leg stations, not a chamfer callout (the issue's alternative, left
  for later). Other corners are unchanged and stay unlocated (shf08's slanted top shows neither
  angle nor station, its planned angles being dropped, as on 0.2.0; corner-bracket-2020 too);
  lint now reports `profile_corner_unlocated` where no linear dimension places a drawn angle's
  edge (CTC-03 2, cgb207 1, 10046 1, 10060 2), and 10007 and 10060 gain plate-face corners whose
  angles find no clear place (`angular_requirement_missing`).

- **Congruent slots are dimensioned as slots.** dw-examples' nema17-bracket pieced each of its
  six 4.5 × 7.5 slots together from a counted width, three counted lengths, six `2× R2.25`
  leaders and locations mixing slot ends and centres. Lone obround slots that are congruent (the
  same width, length and full-round ends, seen through the same axis, whichever way each runs)
  now get one `6× SLOT 4.5 × 7.5` callout, its leader to a slot end, and each is located by its
  centre along its length as well as across it, so the group reads centre to centre. A full-round
  end radius (half the width, to quiddity's 0.01 mm rounding) is no longer called out, for slot
  patterns too (gantry-plate's `6× SLOT 3 × 9.5` loses its `2× R1.5`): the slot callout implies it,
  and review accepts it only from a `SLOT` callout claiming the width, and judges a slot's
  location at its centre only when such a callout states it. gantry-plate's two long slots get
  `2× SLOT 5 × 25` in place of `2× 5`, `2× 25` and two `2× R2.5`, and nema17-mount-bracket's get
  `2× SLOT 4 × 34`. Straight slots (windows with blended corners, CTC-04's) and single slots are
  dimensioned as before, and so are keyhole slots (a circular seat opening the slot at its
  middle, FTC-08's and STC-08's), whose seat callout and axis carry the compound;
  `features/<id>/split` on a slot keeps it out of the callout. A grouped slot whose centre stands
  on a hole's, pattern's or seat's location line is located by that line (ARCH0009-6), and review
  and the location audit credit it.
- **A slot callout keeps off the hole table's origin and off its neighbours.** On NIST CTC-03 the
  `2× SLOT` label, laid out with the plan view's hole table, took the datum corner the table's
  X/Y origin mark needs, so the table failed at A3 1:5. When a slot callout takes that place, the
  table presentation is planned once more with the mark's place reserved before the dimensions
  (`hole_table::origin_reservations`); other table failures keep the original dimensions as
  before. A slot callout's leader counts the features it crosses even on a part with semantic
  PMI, whose other leaders keep their established routing, so FTC-09's no longer runs through
  the wide slot beside its two slots, and it does not run along a location's extension line. A location that also conveys coincident slots' locations
  starts its extension line at whichever of their centres is nearest its dimension line, so the
  witness of a row of slots does not cross the near ones (nema17-bracket, gantry-plate, CTC-03).
- **A baseline's lone inner link follows the rest across the view.** When a strip cannot hold a
  baseline (one face, one axis), its outer links fall through to the strip across the view; the
  one inner link left behind now follows them, innermost, where the whole baseline fits there and
  its extension lines cross fewer features there: nema17-bracket's `5` joins `6.5`, `34.5` and
  `36` on the right instead of running left across a slot and the flange's three holes
  (`dims::placement::fall_through`; `dimensions/<id>/place/side` still chooses). No other part of
  the 65 changes.
- **A radius leader is not left off radial for another callout's sake.** The final leader pass
  takes its repairs together, so when nema17-bracket's hole callout was refined out of its area
  the trial was refused and its `R3` kept a route 97° off radial; the radius repair is now tried
  alone when that happens.
- **A slot leader on a full-round end is judged against the round end.** quiddity rounds the end
  radius to 0.01 mm (R3.18 on FTC-09's 6.35 wide slots, R9.52 on CTC-03's 19.05), and the slot
  tip check then read the ends as square, so a slot callout's tip on the end arc was reported as
  `slot_leader_target_mismatch` (0.147 and 0.002 mm off).
- Plans change on 8 of the 65 quality and dw-examples parts, all with congruent obround slots:
  nema17-bracket now fits A4 1:1 (was A3 2:1); gantry-plate, nema17-mount-bracket, NIST STC-09,
  cgb245 and the corpus CTC-03 and FTC-09 keep their sheets; dw-examples' CTC-03 keeps A3 1:5
  and regains its hole table (dimensioned singly since the cited-datum change above).
  `extension_through_feature`: nema17-bracket 10 to 8, gantry-plate 28 to 30, CTC-03 1 and 4
  to 0, cgb245 5 to 1. FTC-09 trades its slot leader through a slot for one
  crossing of slot 3's `38.1` extension line (`annotation_leader_crossing` 0 to 1): that
  dimension's extension lines and dimension line box its two slots in below slot 3, so every
  route from them crosses something.

### Fixed

- **A location stated twice by model noise is drawn once.** CTC-01's plan dimensioned the same
  span (the bottom edge to the hex boss's and holes' centre line) twice, `225` and `225.000` side
  by side: the boss's centre, averaged from its corners, lies 2.7e-5 mm short of the holes', so
  the one-span-one-dimension merge (ARCH0009-6), which compared ends to 1e-6 mm and labels
  exactly, kept both, the boss's printing three places. Ends now agree to 1e-4 mm (still far
  below the file's 0.01 mm coincidence tolerance) and two defaults' nominals that differ only in
  printed places agree; a precision the Spec states still keeps its own dimension. The hole
  location survives and conveys the boss's.

- **A boss on a hole's location line is located by it.** The location audit kept each feature
  axis its own node, so a boss whose location the planner omitted as conveyed by a hole's or
  pattern's (ARCH0009-6) was reported `feature_not_located`: CTC-01's hex boss on x (the holes'
  `400`), and with the merge above on y too. A mark claiming a location whose feature stands
  within 1e-4 mm of a boss's centre on that axis now claims the boss's too, as a grouped slot's
  centre already was; the witness must still land on the boss's centre. CTC-01 loses both boss
  findings and STC-06 the four on its ⌀31.75 bosses round holes 1 and 2, located by those holes'
  `158.75`, `76.2` and `228.6`. A hole table row conveys nothing: it has no witness line, and
  with the rows STC-07's bosses, each on two rows' x and y, read as located twice. No drawing on the 30 quality parts or the 35 dw-examples
  drawings changes but CTC-01's plan.

- **A rectangle of holes is no longer called out as a bolt circle (#223).** quiddity accepts a
  bolt circle whose holes are up to 2% off one radius and 4% off even spacing, so dw-examples'
  gantry plate called four ⌀5.1 holes on a 19.85 × 20 rectangle "4× ⌀5.1 THRU EQ SP ON ø28.18 BC",
  located by a centre that is no feature (22.825, 0.075 from a hole at 22.75) and marked with
  radial crosses. A bolt circle now needs its holes on one radius within 0.01 mm and evenly spaced
  within 0.05°; otherwise its holes are loose, each located by its own coordinates. The gantry
  plate now calls "7× ⌀5.1 THRU" and goes from A3 2:1 to A4 1:1. CTC-02's four three-hole
  "circles" (already dimensioned singly) change only their stated basis; the `boss_circle` test
  part's 24.3 × 24.38 rectangle (0.19° off even) now prints "6× ⌀2.4 THRU" located singly, where
  it printed "4× ⌀2.4 THRU EQ SP ON ø34.42 BC" as Python draftwright does, and its tests check
  the boss rule on the four squared. t8-flange-nut and t8-housing-bracket keep their bolt
  circles. The Spec's `pattern` can still declare one.

- **A hole size that also names the hole's rim edge is drawn.** NIST STC-10 references each
  hole of a pattern by its cylindrical face and a rim edge of that face, and the edge alone
  made the size undrawn ("one diameter reference is not a face"). An edge every point of which
  lies on one of the referenced cylinders, within its extent, now names that hole again. STC-10
  gains A1-A8 `⌀2.5 ±0.15`, the `⌀3.5 G6` locating holes (printed by class), `⌀3.5 ±0.2` and
  `4× ⌀7 ±0.25`, on their callouts and repeated in the hole table rows; `pmi_not_lowered` falls
  from 35 to 29. Any other edge still refuses. The G6 callout, now carrying PMI, stays beside
  the schedule in the plan view and moves the frames above it: two leaders of the C-hole frames
  now cross the labels of `⌖ ⌀1Ⓜ A B C` and `⌖ ⌀0.65 A B C` (`annotation_ink_overlap` 1 to 5).
  No other drawing of the 30 quality parts or the 35 dw-examples changes.

- **Cited datums on a width or read through a nonconformance are drawn.** A datum whose faces are a
  width's two opposite planes is the width's median plane, so it was refused ("not coplanar") and
  every frame citing it said `frame_datum_not_drawn`. Once the drawing is finished, such a datum a
  shown frame cites now stands on an extension line of the width's own size dimension, in line with
  its dimension line (ISO 5459), where it has a clear place (`src/dims/size_datums.rs`);
  `annotations/<id>/omit` leaves it out. A datum feature naming no face, which the STEP reader reads
  as its datum's usage's faces (a nonconformance it reads through, said in the basis), is no longer
  refused either. dw-examples: STC-06's datum B (12 frames read in full), FTC-06's A–E (every
  frame), STC-08's A and D (7 of 9 frames), CTC-02's D; STC-07 and CTC-03 draw their origin datums
  A, D, E and D. `frame_datum_not_drawn` over the 35 dw-examples and 30 quality parts falls from 37
  to 13 (leader crossings 771 to 786: FTC-06 and STC-07 gain 11 and 8 around the new symbols, CTC-02
  loses 7), and CTC-03's plan view states its holes by dimensions rather than a hole table
  (`hole_table: always` restores it). Still undrawn: widths whose size dimension has no clear place
  (FTC-10 J, STC-09 H, STC-10 J), datums on hole patterns (STC-06 F, STC-08 E, H, J) and datums set
  by datum targets (CTC-02 A–C, CTC-05 C, D, FTC-10 K, L).

- **A modelled thread quiddity refuses only because a chamfer splits its flanks is measured and
  drawn conventionally.** dw-examples' T8 flange nut has a modelled 4-start, 8 mm lead Tr8 bore;
  its top chamfer cuts each flank into two faces, and quiddity-rust 0.2.1 refuses the thread
  (`inconsistent_diameters`) because the short pieces do not span the thread depth. The bore had
  no size or designation (`modelled_thread_refused`) and its helix was drawn as a grey tangle.
  draftwright now measures a refused thread from its faces with quiddity's own invariants (one
  lead on every helical edge, faces of constant radius only at the minor and major diameters,
  crest and root edges repeating every lead / starts, quiddity's depth-to-pitch bounds) and
  reads it as a modelled thread, with the refusal's reason kept as `refused`; anything else stays
  refused. The nut's bore is now called out "⌀8.3 × P2 4 STARTS" (no ISO 261 match, so measured,
  info `thread_designation_measured`) and drawn with ISO 6410 minor and major lines.
  `features/<id>/modelled_thread` overrides the designation or turns the depiction off. Where a
  chamfer or end face meets any modelled thread's flanks along a jagged curve, that boundary is
  now drawn at the crest, like a circular one, instead of as the raw intersection.

- **A drawing of an assembly's principal part says the other parts are not shown.** dw-examples'
  LM8UU is a sleeve and an insert; only the sleeve is drawn (#219), so under the title "LM8UU
  linear bearing" the sheet showed a ⌀12.4 bore and no ⌀8, with the warning only in the review.
  The sheet now carries an automatic general note, `ASSEMBLY OF 2 PARTS: ONLY PART METAL SHOWN;
  PART CENTER NOT SHOWN` (the parts by name, or the others by count beyond three), which
  `annotations/n:assembly/omit` leaves out and `annotations/n:assembly/text` words. A part chosen
  with `--part` gets none. Only LM8UU and the GT2 pulley change on the 30 quality parts and the
  35 dw-examples drawings, on the same sheets. LM8UU's notes block takes the top left, so its
  views move down and its isometric (not to scale) is drawn much smaller in the lower-left corner;
  the GT2 pulley's notes block takes the top right, so its isometric moves up beside it and is
  drawn at the sheet's scale (5:1, was 6.35:1).

- **A PMI label that is a lone symbol is no longer drawn as an empty box.** CTC-04's side view
  and FTC-07's plan carried leadered labels of a lone `⌴`, and FTC-07 a private-use glyph
  (U+F055) of its authoring system's font: AP242 common labels that are fragments of callouts
  whose sizes the file states elsewhere, printed as empty boxes with no size or depth. A label
  whose text has no letter or digit is now refused with why (`pmi_not_lowered`, a warning: the
  lone symbol states nothing its features' own dimensions do not). On the 30 quality parts and
  the 35 dw-examples drawings, only CTC-04 (one label) and FTC-07 (two) change; FTC-07 has four
  fewer leader crossings.

- **A PMI angle prints its unit.** An AP242 angular dimension printed its record's label, whose
  unit is only its kind, so CTC-01's right V-notch read `60 ±0.5` (as if millimetres) beside the
  left notch's `60°`. Every value of a drawn PMI angle, its deviations and limits included, now
  carries `°` (`60° ±0.5°`, ISO 129-1). Only CTC-01's angle label changes on the 30 quality parts
  and the 35 dw-examples drawings.

- **A sheet is judged by its drawn items, not by an empty band past its border (#163).** The
  automatic search trimmed a reservation that ran past the border into empty paper only for
  section views, so a principal view's unused band refused an enlargement whose ink fits. The
  21 mm 2020 corner bracket stayed at 1:1 on a near-empty A4 although 2:1 fits it cleanly, and
  the sheet-metal hanger went to A3 at 1:2 although A4 holds the same drawing at 1:2. The trim
  now applies to every view, still only when every drawn footprint is proven inside the frame:
  the bracket is drawn at 2:1 on A4 and the hanger at 1:2 on A4 (and the gear-blank test part
  at 5:1 on A4 rather than A3). An enlargement that adds a crossing of two dimension lines is
  now held to the same ratchet as one that adds a line through material, so the authored
  vertex-to-vertex diagonal on the `plate.step` test part keeps 1:1 rather than crossing its
  60 mm overall width at 2:1 (`authored_aligned_span_clears_dimension_lines_extensions_and_arrowheads`).
  The other 29 quality parts and 34 dw-examples drawings plan identically. Not changed by this
  fix, because their scale has other causes: NIST STC-09 (its front view's stacked baseline lanes
  rule out A4–A2 at any scale, and a GD&T corridor pass takes A1 from 1:2 to 1:5), NIST FTC-08
  and STC-06 (no candidate on any paper is valid; layout density), and the 2020×50 V-slot (A4 2:1
  is set aside for one added leader through material; left for a decision).

- **Turned and round parts: coaxial bores are not located from a silhouette, a recess at both
  mouths says so, and leaders leave a shared centre line (#104).** A hole on the axis of a round
  face that is the part's whole outline in its end view is no longer located from that face's
  tangents: dw-examples' 608ZZ bearing loses its two `11` dimensions (half its ø22), the Hirth
  joint its two `10`s and cgb203 its two `180`s; the centre lines locate the bore, and
  `origin/<axis>: min` keeps the box sides. A counterbore or spotface quiddity finds alike at
  both mouths of a through hole, which its record states once, is called out `BOTH SIDES`
  (LM10UU's `⌀10 THRU ⌴SF ⌀16.4 ↧ 1.4`, FTC-10's and STC-10's `4× ⌀7 THRU ⌴ ⌀10 ↧ 10`); a like
  recess of another hole farther along the same line is not counted. A leader to a bore whose
  centre mark runs on to a larger coaxial circle than the callout's own outermost one (its
  counterbore or spotface, else its bore) is no longer free to run down or beside it: the router
  weighs a leader that review's `leader_along_centreline` reads as that line (within 6° and 2 mm
  of it over more than 5 mm) as beside it. 608ZZ, LM10UU, LM8UU, the GT2 pulley, the sprocket,
  the Hirth joint, FTC-11, cgb203, GRM-03 and FTC-06 lose that finding, and STC-06 one of its
  three (leader crossings 23 to 21). A mark sized to the callout's own recess (CTC-02's
  `⌀40 THRU ⌴SF ⌀120 ↧ 8`) and another hole's mark on the same line keep their old weighing, so
  CTC-02 plans as before. `BOTH SIDES` rides on the requirement's last line: the 48 mm wrap
  applies to the requirement, so LM10UU's callout and the corpus FTC-10's stay one line (FTC-10
  keeps A2 1:1 on two sheets; a two-line callout took it to one sheet at 1:2) and STC-10's
  wrapped one ends `10 BOTH SIDES`. LM10UU's longer label runs past its view's area over empty
  paper (`annotation_outside_area`, info). A section's address beside other ink on its line now
  keeps 3 mm from it rather than 1 mm, where the corpus FTC-10's `SECTION A–A, SHEET 2, ZONE D9`
  read as one line with the longer callout; it and the corpus CTC-04's address move 2 mm.

## 0.2.0 (2026-10-27)

Now on quiddity-rust **v0.2.1** (previously `0.2.0-alpha.2`), which changes no library code from
v0.2.1-alpha.1. Since 0.1.0: `draftwright diff` carries a drawing to a new revision of its part and
reports what changed, and the command line is attached to each release for Linux, macOS and Windows.
quiddity's face accounting and its annular grooves, V channels and semicircular blind slots are read
and drawn; a recognisable shape that quiddity leaves unexplained is a warning
(`shape_not_recognised`); a plan or side view no dimension needs is left out. Inch-authored PMI
prints as millimetres, a thread quiddity refuses is no longer drawn as a counterbore, coincident
copies of a body and assemblies with no part chosen draw one part, surface models no longer inflate
the envelope, and a part slightly off the file's axes is squared to them. Hole datums and frames
attach to their holes' callouts, bolt circles are called out as one, angular dimensions have
unbroken arcs near their corners, round parts state each size once, and review now reports crowded
labels and leaders to hidden edges, along centre lines or across their view. Over the 94 corpus
parts and the 23 PMI fixtures, against 0.1.0: leader crossings fall from 840 to 691, leaders and
extension lines through features from 434 to 392, warnings of the kinds 0.1.0 reported on them from
2138 to 1954, and claimed faces rise from 6210 to 6873; errors (226) and unlocated features (209)
are unchanged. STC-09 AP242 has 115 fewer leader crossings, FTC-10 AP242 20 fewer lines through
features, CTC-02 AP242 13 fewer and 10 fewer crossings. Ten MFCAD parts besides those listed below
get a larger scale on the same paper, and GRM-03 goes from A3 10:1 to A4 5:1. Drawings still report
what they do not state; dense parts are not yet as clean as Python draftwright's (see **Known
regressions** and the README's "Known limitations").

**Speed.** Instructions retired for `draftwright plan`, this release against 0.1.0 (median of
three interleaved runs each): 0.73× on FTC-08, 0.97× on CTC-01, 1.008× on string_post and 1.058×
on cgb245.

### Known regressions

Measured on this release against 0.1.0, over the 94 corpus parts (with cgb202, cgb217, cgb242
and cgb243) and the 23 PMI fixtures. New kinds of finding are not counted as regressions:
#213's `shape_not_recognised` (156 warnings on the corpus parts and fixtures, by design) and the
review findings added by #224 and #226 (`leader_to_hidden_edge` 107, `leader_along_centreline`
40, `extension_off_centreline` 32, `frame_datum_not_drawn` 26, `leader_across_view` 23,
`label_section_furniture_overlap` 15, and 141 `label_crowded` notices) report what 0.1.0 already
drew. Also by design: the three CTC-01 drawings report their rectangular through notches as one
`features_not_recognised` (0.1.0: `unclaimed_geometry`), and STC-09's `2X R` and `4X R` labels
are now `pmi_not_lowered` (#214).

- 10033: A3 2:1 (0.1.0: A4 1:1), with one leader crossing (0.1.0: none).
- Holdout 274: A3 2:1 (0.1.0: A4 1:1); its warnings are unchanged.
- Holdout 196: A3 1:1 (0.1.0: A3 2:1).
- Holdout 483: A4 1:1 (0.1.0: A4 2:1), with one leader crossing and one `detail_unplaceable`
  (0.1.0: none).
- 10092: A4 2:1 (0.1.0: A4 1:1), but with one leader crossing and one `annotation_over_view`
  (0.1.0: none).
- 10060: A4 2:1 (0.1.0: A4 1:1), its leader crossing traded for a `detail_unplaceable`.
- 10127: A4 1:1 (0.1.0: 1:2), but 3 leaders through features (0.1.0: none; its unplaceable
  detail is gone). Holdout 317: A4 1:1 (0.1.0: 1:2), but 3 leaders through features (0.1.0: 1)
  and one hole location too close to read (`layout_recovery_exhausted`).
- 10101 and 1013: one extension line through a feature each (0.1.0: none); 10138: 2 leaders
  through features (0.1.0: 1) and its 98.995° corner no longer dimensioned
  (`angular_dimension_dropped`); cgb243: 5 leaders through features (0.1.0: 4).
- FTC-10 rb: 46 extension lines through features (0.1.0: 43).
- FTC-10 AP242: 62 extension lines through features (0.1.0: 59) and one more leader through
  text, though its leaders through features fall from 55 to 34.
- 28 of the 94 corpus parts still claim 1–3 fewer faces than alpha.14 (0.1.0: 58 parts, 1–6
  fewer), string_post (14, alpha.14: 16) and GRM-03 among them; CTC-01 and FTC-06 are recovered.

Still open from 0.1.0:

- STC-06 AP242: A2 1:5 (alpha.12: A0 1:1).
- FTC-06: A2 1:5 (alpha.12: A1 1:2).
- FTC-10 rb: two A2 sheets (alpha.12: one A1).
- 10119: A4 1:1 (alpha.12: 2:1).
- FTC-07 AP242: 12 unlocated features, its 4 polygonal bosses among them; 28 extension lines through
  features (0.1.0: 26, alpha.12: 14) and 6 leaders through features (0.1.0: 4); now on A1 1:1
  (0.1.0: A2 1:2, alpha.14: A1 1:1) with 37 leader crossings (0.1.0: 40, alpha.14: 33).
- string_post: the random-pin cell (seed 1, 8 pins) that puts the hole centreline on the title
  block's scale note (#206), still listed in `tools/known-pin-failures.json`.

Fixed since 0.1.0: the CTC-01 merge fixture is back to 21 warnings (alpha.12: 21); cgb241's `2× R23`
is led from the blend again, 37 mm as in alpha.12 (0.1.0: 71 mm); cgb217 has 7 undimensioned
features (0.1.0: 15, alpha.13: 7).

### Added

- **The `draftwright` command line for Linux (x86_64), macOS (Apple Silicon) and Windows
  (x86_64), attached to each release** (`.github/workflows/cli.yml`), each built, tried on a
  revision and checksummed.
- **`draftwright diff old.step new.step --spec s.json -o dir`: what changed between two revisions of
  a part, and its drawing carried to the new one (ADR R3).** quiddity's correspondence matches the
  two parts, and every `f:<kind>:<n>` and `face:<n>` in the Spec is rebound to what it named: an
  entry is carried, renamed (removing a hole renumbers the holes after it), or set aside with its
  original kept, as *orphaned* (its feature is gone) or *ambiguous* (symmetric repeats; the
  candidates are listed, none is guessed). The new revision is drawn with the carried Spec and
  compared with the old drawing: changed features, dimensions and notes, new problems, and each
  view's visible edges in the part's own coordinates. It writes a report (JSON, and Markdown for a
  pull request comment), the carried Spec, and the new drawing marked up in colour
  (`src/revision.rs`).

### Changed

- **A recognisable shape among the faces quiddity leaves unexplained is a warning (#213),
  `shape_not_recognised`, impact `incomplete`, naming its faces, kind and place:** a protrusion
  (nema17-mount-bracket's gussets, corner-bracket-2020's tongues, sm-hanger's tabs), a cut, a
  chamfer on a cylinder's edge with its size (shf08, sk08 and t8-flange-nut's hole and
  thread-entry chamfers), a turned step or land with its shoulder ø (FTC-11's ø57 step) or a
  periodic pattern of teeth or notches with its count (sprocket-iso606-z08's 8, gt2-pulley's
  20). Congruent shapes share one warning with their count (CTC-04's thirty 3 × 45° hole
  chamfers). Shapes are read from the part's topology when it is analysed
  (`unexplained_shapes`). The Spec withdraws a warning by declaring the feature
  (`feature_declarations`), by a leader note or feature control frame on one of its faces
  (`faces`), or, for teeth, by an authored gear on their axis (`gears`). `unclaimed_geometry`
  stays the info-level count. On the 30 quality parts only findings change. Not covered: faces
  that recognition claims but no dimension states, such as shf08 and sk08's clamp slits and
  shf08's stem and shoulder, which stay silent.
- **quiddity's annular grooves, V channels and semicircular blind slots are read and drawn (#233),**
  no longer reported as `features_not_recognised`. A semicircular blind slot is called out as a
  round-bottom open slot (`ROUND-BOTTOM OPEN SLOT W × L × D DEEP`) and located across its width by
  its centre line. A V channel states `A° V × D DEEP` and its trough's location, and an annular
  groove `⌀OD × ⌀ID ↧ D` and its centre's, unless the profile V-notch on its flanks, or the hole and
  boss on its walls, already state it, as on CTC-01, holdout 408 and 419 and the corpus grooves
  checked (1008, 10077, 10098). A Spec that omits one of those stating dimensions (the hole's or
  boss's diameter, the notch's angle or depth) brings back the groove's or channel's own callout, so
  its sizes are never lost. Lint checks each family's sizes (`annular_groove_requirement_*`,
  `v_channel_requirement_*`, `semicircular_blind_slot_requirement_*`), reading a wall hole's and
  boss's printed diameters and depth as the groove's; a notch-stated channel is checked by the
  notch's own lint. 10092 moves from A4 1:1 to A4 2:1 in this release (one new
  `annotation_leader_crossing`, one `annotation_over_view` and two `label_crowded`, its
  `detail_unplaceable` gone), holdout 196 from A3 2:1 to A3 1:1 and 483 from A4 2:1 to 1:1 (new
  `detail_unplaceable` and `annotation_leader_crossing`); 1013's slot location gains
  `extension_through_feature` and `label_section_furniture_overlap`; 10098 loses its 78.046° profile
  angle, the supplement of the 101.954° on the same edge, to its V channel's locations. Rectangular
  through notches (CTC-01's two) stay unrecognised: drawn, they cost CTC-01 its 1:5 scale or add
  leader crossings. The quality parts are otherwise unchanged.
- **A plan or side view that no dimension needs is left out (#222).** On a part that is not turned,
  the plan or side view on which the drawing with every view shown places no dimension, so that all
  it would add is its outline (often a strip of hidden lines), is dropped, unless it is a prism's
  profile or looks along a cylinder. The decision is read from that drawing itself, after every
  routing preference and added statement (a profile angle, a thickness moved for readable
  witnesses). At most one view goes, the plan first, never the front, and never one carrying PMI,
  section A–A's cutting plane or anything the spec refers to it; the kept views' basis says why.
  Leaving it out never takes larger paper or more sheets than the drawing with it shown: then the
  view is kept, and the sheet basis says so (MFCAD 10092 kept its plan view rather than go to A3; it
  is on A4 2:1 in this release). Naming the view (`views/plan: {}`) keeps it, in a pinned drawing's
  unpinned frame too; naming any principal view, or authored dimensions or a dimension scheme, turns
  the rule off. vslot-2020x50 and MFCAD 10020, 10046, 10060, 10156, 10167, 1017 and 10245 go from
  1:1 to 2:1 in the room freed (10156 and 10167 lose warnings; 10060 trades a leader crossing for an
  unplaceable prismatic-steps detail, and 10101 gains an extension through a feature at its
  unchanged 2:1); l-plate-5-holes, retaining-ring, shf08, MFCAD 10038 and FTC-09 lose their empty
  plan. The rest of the 30 quality parts are unchanged. The title-block fixture tb_stepped now fits
  A4, as draftwright's sheet, instead of A3.
- On quiddity-rust **v0.2.1** (first taken as v0.2.1-alpha.1; the release changes no library
  code from it). The reader accepts its new `rectangular_through_notches`
  family (not drawn yet), so CTC-01's two V-slot notches and their rounded
  channels are reported as unrecognised features instead of 3 unclaimed faces. Golden's
  30-degree slots are recognised in the plate's file axes and claim all four walls, as Python
  quiddity does. The 30-part quality table and the random-pin cells are unchanged.
- **A Spec tolerance writes a zero deviation as `0`, unsigned** (ISO 129-1), as imported and
  generated bands already do: `+0 -0.1` prints `0 -0.1`, not `+0.0 -0.1`.
- On quiddity-rust **v0.2.0**. Its face accounting (blended box corners, plate edges, stock
  walls, drill-point tips, slot end walls) raises claimed faces on 65 corpus parts: CTC-01 AP242
  goes from 27 unclaimed faces to 3, and most of the claim losses listed for 0.1.0 are
  recovered (the rest are under 0.2.0's **Known regressions**). Newly
  recognised steps change the layout of 10033 (now A3 2:1, with one leader crossing a dimension
  line), 10127 and holdout 119. FTC-10 rb gains 3 and FTC-07 AP242 2 extension lines through
  features, and the FTC-10 AP242 drawing one leader through text. The three new families
  (annular grooves, V channels, semicircular blind slots) were read but not yet drawn,
  reported as `features_not_recognised`; #233 above draws them.

### Fixed

- **A part a fraction of a degree off the file's axes is drawn square, in its own frame
  (#216).** dw-examples' DIN 471 M10 retaining ring lies about 1.09° off them, so it was drawn
  as a tilted box: 1.147 thick instead of 1, 12.309 × 14.550 instead of 12.298 × 14.537, a
  slanted plan rectangle, and its holes and boss unlocated as inclined to every axis. When
  quiddity's part frame is within 2° of the file's axes, the part is now turned onto them
  about its centre of mass before it is read (`squared` in the Analysis), and the plan says so
  (info `part_squared_to_axes`). The ring now gets its true sizes, its three holes and boss
  located, and its flats and gap recognised. A part on the file's axes, or further off them, or
  whose file carries PMI (placed in the file's axes), is unchanged: the 30 quality parts' plans
  are identical.
- **Hole datums and frames attach where their place runs into vacant paper (#224).** A view's
  reserved area, tightened to the settled ink, refused a place just past it even with empty
  paper beyond, so on NIST CTC-01 datum B stayed edge-on in the front view (its ⌀35 callout's
  other side is blocked by the 4× R25 leader), and on CTC-02 datum F stayed there with a leader
  crossing seven dimensions and two holes. That area now grows to hold an attached datum or
  frame where it stays inside the margins, off the title block and clear of every other view's
  area. CTC-01's B and C now both stand beneath their callouts in the plan view; CTC-02's F
  stands beneath its B2 tag and J beside D3, mirroring H (9 fewer findings); CTC-04's datum B
  leaves its leader across seven dimensions; STC-09's ⟂ and two position frames stack over their
  holes' `3× ⌀7.137` callout (net 103 fewer findings; two touching frames in the stack now
  `label_crowded`). A pinned drawing replays the frames as they were before attaching, so a bare
  pin still holds the attached ink. The area grows only into paper no view, table, notes block
  or caption holds, so a relayout that leaves no such paper (applying fixes in the editor can)
  returns a frame to its leader: the spindle's f7 position frame then shows main's
  `leader_through_feature` again (`src/dims/hole_marks.rs`).
- **A leader exactly at the 75 mm limit is now legal on every platform (#236).** The route
  sweep's longest reach is the limit, and its `cos`, `sin` and `hypot` round differently in the
  last bit on x86-64 Linux and Apple Silicon, so FTC-07 AP242's all-over profile-surface frame
  took a 75 mm route on macOS that was "too long" on CI (19 leader-to-dimension-line crossings
  there, 15 here). A leader now meets the limit within 1e-6 mm. The 34 quality and AP242 plans
  are unchanged on macOS.
- **A thread recognition refused is no longer drawn as a counterbore (#218).** quiddity refuses
  dw-examples' T8 flange nut thread (`inconsistent_diameters`) yet also reported a hole on its
  flanks, printed `⌀6.5 THRU ⌴ ⌀8.3 ↧ 14.8` in a 15 mm part. No hole, nor its pattern, is now
  built from faces a `ModelledThreadRefusal` names; the refusal is reported against them as
  warning `modelled_thread_refused` until the feature is declared on them in the Spec. A smooth
  hole or boss at exactly the major diameter of an ISO metric size the title or product names
  (`M3 x 12 hex standoff`) keeps its measured callout and gains info `possible_thread`, which
  `features/<id>/thread` settles.
- **Small parts no longer go to A3 over a leader the finished drawing would not have (#221).**
  A paper trial skipped the finishing route pass whenever its reported findings were clear, but
  the paper policy also measures leaders on extension lines from the ink, so a contact that pass
  clears refused A4. The pass now runs while such a contact remains. bearing-608zz and
  hex-standoff-m3 (dw-examples) are drawn on A4 at 2:1 instead of A3 at 5:1, and GRM-03 on A4 at
  5:1 instead of A3 at 10:1, each with no contact. A title-block value too long for its cell at
  3 mm is drawn smaller, down to 1.8 mm (ISO 3098's smallest height), so a long drawing number
  fits A4's cell instead of overflowing it and asking for A3; only a value that does not fit even
  then is reported as `title_field_overflow`. Still on A3: bearing-lm10uu, whose A4 trial keeps
  a groove callout ending where the 19.4 step's extension line leaves the outline even after
  the finishing pass (the leader placer finds no other route), and gt2-pulley, whose extra side
  view, kept only for a radial hole, needs 303 mm across.
- **A profile corner's angle is drawn near its corner (#227).** Its arc still started at the
  radius that left the label's width between the arrowheads, from when the label broke the arc,
  so an acute corner's arc began far out: vslot-2020x50's "45°" at 18 mm from its vertex, below
  and left of the profile. With the label outside the arc, a profile corner's arc now starts at
  the least radius that carries both arrowheads and keeps the label inside the sector, clear of
  both extension lines, and it prefers a place whose label is 0.5 mm clear of other ink
  (`label_crowded`), taking a crowded one only when no other is clear (`src/dims/angular.rs`).
  Its arrowheads never lie across another item's line, and its ink keeps out of the span that
  an off-span linear label settles back into. The triangle fixture's "45°" arc goes from 18 mm
  to 8 mm, and it and the trapezoid fixture now state their 63.435° corners; hex-standoff-m3's
  "6× 120°" moves in from 14 mm to 12 mm. vslot-2020x50's "45°" keeps its corner and moves in
  from 18 mm to 10 mm, and a "135°" (6 mm) is added; its arc and witness line cross the lower
  lip, as the "45°"'s witness line already did. The "135°" takes the left-hand place "8× C2.661"
  had, so that callout moves up beside "⌀4.2 THRU" and the two are crowded (two notices).
  10046 states one more corner and moves its other two nearer their corners. 10049 states its
  109.428° corner (r 15 mm) instead of its 160.572° (r 13 mm). CTC-03, FTC-08 and cgb207's
  angles move by under 1 mm. PMI and notch angles keep the wider radius: the recess callouts do
  not yet route around a notch arc brought in, and 10020's 21° notch then went from A4 1:1 to
  A3 2:1. Placing a profile angle now passes over items whose ink is far from a candidate, so
  planning costs about what it did or less: against main, 0.42× the instructions on
  vslot-2020x50, 0.91× on 10046, 0.83× on FTC-08, and 1.09× on 10049.
- **Angular dimensions are drawn with an unbroken dimension arc (#227).** The arc was broken
  around an inline label, and at the smallest radius that left room for the arrowheads all that
  showed of it was the two arrowheads (dw-examples' hex-standoff-m3 "6× 120°" and
  vslot-2020x50 "45°"). Per ISO 129-1 the arc now runs unbroken about the vertex from one
  extension line to the other, an arrowhead at each end, with the label just outside it
  (`src/dims/angular.rs`). Where the first radius is clear the label keeps the inline label's
  place and the arc moves inside it, so the ink reaches no further. Where the unbroken arc
  meets something the break used to skip, the angle takes a later place: CTC-01's "60°" and
  CTC-03's "2× 135°" step out 1–2 mm, and FTC-08 rc's "8× 135°" goes to another of its
  corners, its arc across a slot outline. The vslot "45°", which still sat away from its
  corner, is brought to it by the entry above.
- **An assembly with no part chosen no longer dimensions its other bodies as one part's
  features (#219).** The whole file was drawn as one part: dw-examples' GT2 pulley stated its two
  set screws as "ø3" bosses, "HEX 2.000 A/F × 2.5 DEEP" sockets, "4× C0.25" and "6× 120°", and
  the LM8UU bearing printed "⌀8 THRU" for its inner sleeve's 22.4 mm bore. With no part chosen,
  when every other body lies within the box of the largest (by box diagonal, as placed), that
  part is drawn alone, as `--part K` draws it: the pulley's drawing is `--part 2`'s and the
  bearing's is its sleeve with "⌀12.4 THRU". The plan warns `assembly_principal_drawn`, naming
  the parts not drawn and how to choose another. Parts side by side, or a pin standing out of a
  plate, are drawn whole as before.
- **Four holes on a bolt circle are called out as one (#223).** quiddity reports four holes at
  90° on a pitch circle as a rectangle turned 45°, which no view can space as a lattice, so each
  hole was located singly from the part's faces or a round flange's tangent edges
  (dw-examples' t8-flange-nut: "3" and "19" from the ø22 flange, with
  `hole_pattern_requirement_missing`). A square with its holes on the axes through its centre is
  now read as a bolt circle: "4× ⌀3.4 THRU EQ SP ON ø16 BC", located by its centre, with its
  pitch circle drawn (t8-flange-nut, t8-housing-bracket). The Spec's `split` still dissolves it.
  A grid located by its centre now draws a centre mark there when no hole or boss stands on it
  (t8-housing-bracket's 2×2, NIST CTC-01); none is drawn where a hole table locates the holes.
- **A hole beside a round end is located from that end's axis, not its tangent (#223).** Where
  the part's box side is only the tangent of a round face (no plane lies on it) and a hole or
  boss stands on that face's axis, the other holes and hole patterns are located from the axis,
  and the feature on it is located by those dimensions' witnesses on its centre. dw-examples'
  linkrods: the ⌀0.440 hole is 3.875 and 0.125 from the ⌀1.034 hole in the ø1.5 boss, not 4.625
  and 0.625 from the boss's tangents, and two extension lines through features go; shf08: its
  ⌀8 and far ⌀5.5 holes are 16.25 and 32.5 from the near ⌀5.5 hole in the R5.25 end. A hole
  table still states every hole from its corner. The Spec's `origin/<axis>` (`min`) keeps the box
  side; parts that carry PMI keep their datums' origins.
- **Frames and datum symbols on holes attach to the holes' size callout or table tag (#224).** A
  datum on a hole was drawn edge-on, on the hole's hidden silhouette, and a frame's leader ended
  at the centre of its faces, often on blank material. Once the drawing is finished, a frame whose
  faces are all bores of the holes one shown size callout (or, for one hole, table tag) names now
  stands beneath it (or over it), without a leader, in the view that sees the holes end-on; a
  datum on one hole's bore stands on that hole's own size callout, else its table tag. A bore
  split in halves is recognised by its radius, not its box. Each moves only to a clear place with
  no finding of its own, so dense drawings keep most of theirs (NIST STC-07, STC-08 and STC-10
  keep every placement: their tags sit over material or their callouts are hemmed in); FTC-10
  AP242 moves its cylindricity and 8-hole position frames and datum F (net 11 fewer findings),
  STC-09 its 4-hole ⌀6.35 position frame (net 13 fewer), FTC-11 datum B, STC-06 a position frame,
  CTC-01 datum B, CTC-02 datums F and H, CTC-05 datum B and a runout frame, and the spool
  fixtures' datum B and their ⟂ frame onto the ⌀62 bore's callout. A view or side in
  `annotations/<id>/place` keeps an item where lowering put it; a bare pin, or a pin in the view
  it attaches in, holds the attached ink (`src/dims/hole_marks.rs`).
- **A drawn frame citing a datum the drawing does not show is said (#224):** a
  `frame_datum_not_drawn` warning names the frame and the missing datums (STC-08: nine frames
  citing A, D, E, H or J).
- **Hole and slot callouts in drafting wording, counted as made (#225).** A through hole split
  by a slit into coaxial segments is one hole: shf08's and sk08's clamp holes read "⌀4 THRU" and
  "⌀7 THRU", not "2×" (segments farther apart than the bore is wide, such as sm-hanger's two
  flanges, still count twice), and lint counts them the same way. A grid of holes no longer
  prints its "(2×2)" arrangement, which ISO 129 has no notation for (its pitches show it). A
  free-direction slot reads "SLOT 2 WIDE × 4.5 LONG", without the internal "ORIENTED", and a gap
  open at both ends (the retaining ring's) reads "GAP 1.64 WIDE", its length being the wall it
  cuts, which lint no longer asks for.
- **Review reports annotation contacts it used to pass as clear (#226).** Placement keeps labels and
  lines only just clear of each other, box to box, so text 0.03–0.4 mm from a line or a section
  letter was never reported. The finished drawing is now checked once more, after its final
  leader-route repair (`src/dims/contacts.rs`): `label_crowded` (info) for text within 0.5 mm of
  another label, of other line-work or of a section's arrowhead or letter (t8-housing-bracket's 15
  against letter A, nema-17-bracket's 31 against the 20.5 and 30 extension lines,
  nema17-mount-bracket's R0.5 under the 50 line, lm8uu's groove shoulder against 2× C0.25);
  `label_section_furniture_overlap` for a line through a section mark (t8's 15 through the A
  arrowhead; a label over a mark stays lint's); `extension_off_centreline` (warning) for an
  extension line 0.01–0.5 mm beside a centre line, which reads as locating that centre
  (nema17-mount-bracket's 28.85, STC-07's 12.7, 210.006 and 2× 9.297); and `leader_along_centreline`
  (warning) for a leader within 6° and 2 mm of a centre line over more than 5 mm (608zz, lm8uu,
  lm10uu, hirth-joint, sprocket, gt2-pulley and STC-07 bores). None moves an annotation or chooses
  paper: layouts are unchanged. When a later repair changes the selected candidate's
  annotation-conflict count, the sheet basis says what the finished drawing has (nema-17-bracket: 1
  counted, 0 left), so the basis agrees with the plan's problems. Leaders to hidden edges and across
  their view are reported by the next entry.
- **Review reports leaders that end on hidden edges or run across their own view (#226).** The
  final contact check also reads each callout leader against its view's physical edges
  (`src/dims/contacts.rs`, with the interior router's edge proof): `leader_to_hidden_edge`
  (warning) when the tip lies on an edge only where a nearer planar face covers it, so it points
  at dashed line-work (nema17-mount-bracket's R0.5, GRM-03's ⌀1.6 blind hole, string post's
  2.4 A/F, CTC-02's 6× R18, CTC-04's counterbores and R13, FTC-06's bosses
  and 4× ⌀7.137 counterbore pattern, and pockets, holes and ramps seen
  only hidden on 8 of the first 12 MFCAD parts in `tools/quality_parts.txt`); and
  `leader_across_view` (warning) when a leader at least 15 mm long crosses four or more edges of
  its view (visible or hidden, coincident ones once; a cylinder's side-on silhouette is not
  counted) beyond the fewest a straight way out of the
  view from its tip would cross (cgb245's two 2× R3 slot leaders, CTC-02's ⌀40 counterbores
  and 6× R18, CTC-03's 2× R9.52, CTC-05's ø304.8). Length alone does not single these out: a
  bore leader whose every way out crosses the same rings (gt2-pulley's ⌀5 THRU) is not
  reported. gt2-pulley's 43.8 mm HEX 2.000 A/F leader no longer appears, so it was not
  judged. Layouts are unchanged: neither moves a leader, and a feature seen only hidden keeps
  its callout where it is. Still open on #226: linkrods' 0.625 and 0.75 standing beside the
  boss they do not locate.
- **Coincident copies of a body no longer multiply every count (#212).** A STEP holding the same
  solid several times on top of itself (a FreeCAD export of a body and its `Pad`) was drawn
  whole, each feature once per copy: dw-examples' gantry plate printed "24× ⌀5.1 THRU" for 12
  holes and went to A1 5:1. When every body of an assembly coincides with the first (the same
  faces, each of the same surface type and box, as placed), that part is drawn alone, as
  `--part K` draws it: the gantry plate is A3 2:1 with its 12 holes, and the V-slot profile's
  one hole is no longer "3× ⌀4.2 THRU". The copies are reported as `coincident_bodies` (info);
  where only some bodies coincide the file is drawn whole as before, and `coincident_bodies`
  warns that their features are counted again.
- **PMI from inch-authored files prints as millimetres an mm sheet can state (#214).** A length is
  converted from its stated digits less the writer's float noise (at most six decimals in its own
  unit, more for a larger unit such as the metre, the product to three more), so NIST STC-08's
  frames print `1.524` and `0.381`, not `1.524000000006`; a PMI dimension authored in inches
  prints its exact conversion (`6.35 ±0.254`, not `0.25 ±0.01 INCH`, STC-09); a zero deviation is
  `0`, unsigned (`⌀6.35 +0.0762 0`, ISO 129-1, STC-07 and STC-09); and a common label that states
  a radius without its value (`2X R`, `4X R`: ASME's full radius) is not drawn but reported as
  `pmi_not_lowered` (a warning).
- **Surface models beside the solid no longer inflate the envelope (#215).** A STEP's
  `SHELL_BASED_SURFACE_MODEL` is read with the part (as OpenCascade reads it), but only the
  solids' faces are drawn; the envelope, the overall dimensions, the stock faces, the principal
  profiles and the edge ink now come from those faces too. NIST STC-07's two CATIA fills (open
  triangles bounded by `Sketch.2`) put its overall height at 257.688 with the top extension line
  in empty space; it is now the solid's 222.706, and the front view is centred on the solid. A
  part with no solid is still drawn from all its faces. NIST FTC-07 (AP242, the same geometry and
  fills) now lands on A1 1:1 instead of A2 1:2: with the honest, 17.5 mm shorter front-view box its
  A2 1:2 layout gains leader crossings (the `F1.2` label's leader now crosses the front view) and
  is refused; `sheet/size` A2 with `sheet/scale` 1:2 still draws it there.
- **A slot position line states every slot whose end it reaches (#228).** The planner draws one
  location for lone slots whose low ends lie on the same span (one span, one dimension:
  ARCH0009-6), but lint read it as stating only its own slot, so `slot_requirement_missing`
  warned about the others: nema17-bracket's slots 3, 5 and 6, CTC-03's slot 2, CTC-04's slots 5
  and 6 and FTC-09's slot 2. Lint now credits the line to every lone slot whose low end lies the
  same distance from the part's box on the same axis, seen through the same axis, and still
  checks its text against each slot's value.
- **Round parts no longer state one size several times (#220).** Three or more separate lands
  of a turned part's largest diameter are stated by its overall diameter, which claims each:
  lm8uu prints ø15 once, not four times. Equal grooves on one profile are one counted callout
  (`2× 1.2 WIDE × ø13.4`), and equal bosses that are bands of the profile one diameter
  (`2× ø11.2`). A groove's width and a turned `C` chamfer's leg are support-plane intervals, so a
  chain of lands and grooves, or a cylinder between two end chamfers, closes with the overall
  length and its longest member is reference: omitted by default, in parentheses with
  `sheet/references` (lm10uu's 19.4, 608zz's 6.5). A boss filling the part's box across its axis
  states the overall width and depth (608zz, hirth-joint, FTC-11's `ø63 ±0.5 AVG`, no `63` beside
  it; cgb203's ø360), unless a flat face lies on that side of the box, and a short disc on z
  drops the side view that repeated its front, as a turned part does (`views/side` keeps it).
  A file of several solids drawn as one, where one solid fills the box, now has its chains walked
  on that solid (the analysis records its faces as `body_faces`): lm8uu's 15.2 is reference, and
  gt2-pulley's band lengths are conveyed by its baseline as on a single solid. Chains are still
  never walked from one solid to another.

## 0.1.0 (2026-10-25)

The first release without an alpha tag. It is still on quiddity-rust **v0.2.0-alpha.2**, as in
alpha.14. Since alpha.14: dimension lanes are closed up, views keep a 3 mm gutter inside the
frame, more feature callouts sit inside the views, radius leaders prefer the middle of their arc
and a radial approach, only datums that something uses are drawn, and face claims follow quiddity's face
accounting. Drawings still report what they do not state; dense parts are not yet as clean as
Python draftwright's (see **Known regressions** and the README's "Known limitations").

**Speed.** In instructions retired, as measured in the pull requests and not re-measured for this
release: the interior-callout pass (#208) makes each edit cost about 10% more; the frame gutter
(#205), against the build before it, is 0.89× on CTC-01, 1.002× on string_post, 1.012× on
FTC-08 and 1.048× on cgb245.

### Known regressions

Measured on this release; the alpha.12 figures are the reference from before the move to
quiddity-rust's framed records.

- STC-06 AP242: A2 1:5 (alpha.12: A0 1:1).
- FTC-06: A2 1:5 (alpha.12: A1 1:2).
- FTC-10 rb: two A2 sheets (alpha.12: one A1).
- 10119: A4 1:1 (alpha.12: 2:1). Closing up the lanes restored 2:1; the frame gutter took it back.
- CTC-01 merge: 23 warnings (alpha.12: 21; alpha.14: 30).
- FTC-07 AP242: its 4 polygonal bosses are not located (12 unlocated features), extension lines
  through features are 26 (alpha.12: 14), and it is now on A2 1:2 (alpha.14: A1 1:1) with
  40 leader crossings (alpha.14: 33).
- cgb241: `2× R23` is still led from the fillet: 71 mm with three hidden-line crossings, where
  alpha.12's leader from the blend was 37 mm with none.
- cgb217: quiddity's frame vote still puts it at 45° (#81), leaving 15 undimensioned features
  (alpha.13: 7).
- string_post: one random-pin cell (seed 1, 8 pins) puts the hole centreline on the title
  block's scale note (#206), listed in `tools/known-pin-failures.json`.
- 58 of the 94 corpus parts claim 1–6 fewer faces (CTC-01, FTC-06 and string_post among them):
  the envelope planes quiddity marks `unexplained` are no longer claimed by draftwright's own
  stock rule, until quiddity-rust#100 adds body evidence.

### Changed

- **Dimension lanes are closed up after layout.** Dimensions that do not overlap now share a lane
  whatever their kind, instead of each taking its own. After layout each dimension line, inner
  first, moves into the innermost lane where nothing already there comes within a clearance of
  it and none of its own findings grows; a line stays outside any dimension it overlaps that
  lies inside it, so sizes stay nearest and a baseline keeps its order. Authored, pinned and
  interior dimensions stay put, omitting one dimension moves no other, and an item promoted
  into an omitted item's lane follows it. NIST CTC-01's plan band goes from 8 lanes to 5. On the
  100-part corpus no finding changes.
- **A 3 mm gutter inside the frame.** Views and their annotation keep at least 3 mm clear of the
  frame (maintainer, 2026-10-25); `sheet/frame_gutter` sets another width, 0–20 mm. Tables,
  notes, the title block and other sheet furniture still stand against the frame. When there is
  spare paper, each sheet's composition moves away from the frame as one block, up to 10 mm
  more, after the gaps between views have had their share. A strip of dimensions that would
  overflow is first packed, nesting lines that overlap, which keeps NIST CTC-01's baseline whole
  on A3 1:5. On the 100-part corpus, 91 parts are
  unchanged; seven synthetic mfcadpp parts change paper or scale (four one step smaller on A4,
  three from A4 1:1 to A3 2:1), and NIST CTC-04 loses its two leader crossings and five
  extension lines through features. Off the corpus, FTC-07 AP242 moves from A1 1:1 to A2 1:2
  (its leaders through features fall from 10 to 4, but extension lines through features rise
  from 23 to 26 and leader crossings from 33 to 40), and the CTC-01 merge fixture falls from 30
  warnings to 23.
- **Only used datums are drawn by default (revising #141).** A declared datum's symbol is drawn
  when a control frame cites it or dimensions measure from it (the planar datum selected as an
  axis's origin, as for Specify ± mode origins). Any other declared datum is not drawn; it stays
  in the plan as `not_drawable` and is reported as `datum_not_drawn` (info) (maintainer,
  2026-10-25). `sheet/uncited_datums: true` draws every declared datum; `false` hides every
  datum no frame cites, origins included. The layout's room for datum symbols now follows the
  Spec's datum visibility, not only edited wording. Among the PMI fixtures only GRM-03 changes:
  its cylindrical datum A, which nothing cites, is reported instead of drawn.
- **More feature callouts inside the views, and radius leaders that point at the arc's middle.**
  An automatic feature callout drawn outside its view moves inside when its whole label sits on
  blank face or in an opening the part surrounds (a slot, the gap between lugs), its leader is
  no longer and none of its findings grows (maintainer, 2026-10-25). A leader leaving its tip may
  cross outlines parallel to the tip's own edge, as around a chamfered hex's flats, which any
  route must cross; NIST CTC-01's HEX 100 A/F now sits on the plate beside the hex. Radius
  leaders, slot ends included, prefer the middle quarter of the arc and within 7.5° of radial,
  taking a longer clear route to get there: CTC-01's 4× R25 now meets its arc radially (its
  radius_leader_not_radial finding goes) and 7× R50 points at the middle of a lug corner. The
  pass runs on the selected drawing only; when the Spec omits items, the moves come from the same
  request without them, so an omission moves nothing else. A radius route may not run into more
  extension lines than the one drawn.

### Fixed

- **Unclaimed faces follow quiddity's face accounting (#207).** A face quiddity accounts to any
  record, evidence records such as `step_levels` included, is claimed, and its `body` faces are
  credited once the overall sizes are stated; the envelope-plane stock rule now applies only to
  analyses without that accounting. On NIST CTC-01 (AP242) the end blocks' inner walls, the notch
  side walls and slot 1's end walls are no longer reported, while the envelope planes quiddity
  leaves unexplained, the hex boss's top among them, now are (26 unclaimed faces become 27)
  until quiddity-rust#100 supplies body evidence. Across the 94-part corpus, 27 parts claim more
  faces (cgb245 +274, CTC-02 rc +51, sm-hanger +44), 58 claim 1–6 fewer and 9 are unchanged.
- **Sheet choice.** A part whose views fit A3 at 1:1 is no longer offered A2 or larger by the
  paper search either, as the automatic planner already ruled (maintainer, 2026-10-15). A refused
  automatic drawing gets one smaller readable scale on its own paper before larger paper, and a
  trial that dropped items for want of room counts as cramped, so it too tries the smaller scale
  (cadgenbench cgb245 stays on A3 1:5).

## 0.1.0-alpha.14 (2026-10-25)

Now on quiddity-rust **v0.2.0-alpha.2** (previously `0.1.1`), which publishes recognition schema 6.
It picks up quiddity-rust's fixes for #81, #83, #84, #85 and #89 and its decision on #82, and
recovers most of the regressions accepted in alpha.13. Those still open are listed under **Known regressions** below.

### quiddity-rust v0.2.0-alpha.2

- **Recognition schema 6** (#92). Records quiddity measures in a part's own frame now carry an
  `origin` and measuring axes (a file-axis letter or a unit vector in file coordinates), with
  distances measured along those axes. draftwright reads them back into file coordinates.
  Oriented slots, chamfers and slot patterns now arrive in `slots`, `chamfers` and
  `slot_patterns`; draftwright still checks and draws them as oriented features, and their
  draftwright IDs (`f:oriented_slot:1`, …) are unchanged. A record still in the part's frame is left out with a reason, never
  drawn in the wrong place. Schema 5 and earlier are refused.
- **Recovered from alpha.13's known regressions.**
  - CTC-02 AP242: the axis-aligned frame is back (#81). It has no undimensioned features
    (previously 15), and `10× R25` is drawn again.
  - FTC-07 AP242: A1 1:1 again (previously A2 1:2), with 4 undimensioned features
    (previously 25). quiddity now also finds its 4 polygonal bosses, which are not yet located
    (see Known regressions).
  - The countersink-two-face fixture's hole links to its seat again (#83).
  - 1000.step's plate top is on the grid (#84), so its 4.219 has no rounding advisory.
  - whistle_frame_reference's plate is 7.0 thick again (#85), and it is back on alpha.12's
    single A2 2:1 sheet.
  - cgb202: the false fillets are gone (#89). 91 warnings and 1 error fall to 50 warnings and no
    errors, with no undimensioned features (previously 42).
  - cgb207: `2× R5` and "ALL UNSPECIFIED RADII" are drawn again, and R5 is no longer
    undimensioned.
- **Settled as correct.** CTC-02 rc's 49.5 mm array stays unpublished: quiddity found two
  mirror-image rows with equal claim and publishes neither (#82). Every hole is still drawn and
  located.
- **Other drawing changes** (#89): a few mfcadpp parts lose one radius callout, as quiddity no
  longer reads a concave round as a fillet. mfcadpp 10033 moves from A3 2:1 to A4 1:1, and 10163
  from A4 1:2 to A4 1:1.
- **Speed.** Unchanged: planning CTC-02 rc costs 1.0003× main's instructions, FTC-06 0.993×.

### Known regressions

- STC-06 AP242: A2 1:5 (alpha.12: A0 1:1).
- FTC-06: A2 1:5 (alpha.12: A1 1:2).
- FTC-10 rb: two A2 sheets (alpha.12: one A1).
- 10119: A4 1:1 (alpha.12: 2:1).
- CTC-01 merge: 30 warnings (alpha.12: 21).
- FTC-07 AP242: its 4 newly recognised polygonal bosses are not located (12 unlocated features,
  previously 4), and extension lines through features rise from 14 to 23.
- cgb241: quiddity now calls one of its two mirrored R23 rounds a fillet. The sheet stays A3 1:5,
  but `2× R23` is led from the fillet: 71 mm with three hidden-line crossings, where main's
  leader from the blend was 37 mm with none.
- cgb217: quiddity's frame vote moves it from 13.1° to 45° (#81), leaving 15 undimensioned
  features (alpha.13: 7). Its ø60 band is still read as a boss, not a turned step.

### Added

- **Outer-profile corners are defined, angled only where needed** (LINT-angular-profile_angle_requirement,
  now ported). A non-right convex outer-profile corner is satisfied by its angle or by linear
  dimensions that fix both its edges (ISO 129-1, ASME Y14.5); lint reads those dimensions from the
  drawn ink, so a corner fixed linearly is no longer reported. The planner adds no angle on a
  linearly fixed corner, gives any other corner its angle in the view that shows it true
  (`d:envelope/profile_angle.angle.f<face>_e<i>_e<j>`), gives a closed polygon of equal corners
  one counted angle (`8× 135°`) and two mirror-equal corners one `2× θ°`; lint proves both counts
  from the drawn edges. An angle is placed only local to its corner (arc and label within twelve
  label heights of it, label on its arc) and clear of holes and every other item; otherwise it
  is dropped (`angular_dimension_dropped`, info) and its corner stays reported. Placement never
  moves another item or changes the sheet, and the 30-part quality metrics are unchanged. NIST
  FTC-08 (`8× 135°`), CTC-03 (`2× 135°`) and cadgenbench cgb207 now have no
  `angular_requirement_*` finding; CTC-04 and the mfcadpp parts keep theirs.
- `faces/<face>/profile_corners` (`auto`, `angle`, `linear`) chooses how a face's outer-profile
  corners are defined (`linear` locates the missing stations,
  `d:envelope/profile_location_<axis>.length.f<face>_<n>`); `dimensions/<id>` `{}` shows and `omit` hides one corner's angle.
- **Opening a saved Spec across kernel upgrades** (#191). `Session::open_spec(json)` applies what
  the part still has and sets aside each entry naming a feature, face, dimension, annotation, view
  or measurement it no longer has, with the entry and a reason code (`no_feature`, `no_face`,
  `no_dimension`, `no_measurement`, `no_annotation`, `no_view`). A saved face anchor the part no
  longer has is set aside too. A gone member of an authored group is taken out of the group alone
  (`features/<group>/pattern/members/<i>`) while the rest is still a group, else the whole group
  is set aside. Reason codes depend on what the part has, not on the order of refusals. It reads
  `sheet/preferred_printer` as `preferred_max_size`. Any other refusal still refuses the whole
  Spec. All the stale entries one check finds are set aside together, so many cost about one
  plan.
- `api::Error` and `model::Invalid` gain `path`: the escaped Spec entry
  (`dimensions/d:f:hole:1~1bore.diameter`) of every "there is no …" refusal. Messages and choices
  are unchanged.
- `api::spec_ops(before, after)`: the `set`/`remove` ops that turn one Spec into another, as the
  MCP's `spec_diff` makes them (a new empty object is set). An explicit `null` in *after* removes
  the key, where the editor's `specOperations` sets `null`. `Session::set_spec` replaces the Spec
  as the WASM `setSpec` did.

### Changed

- **One owner for through-step legs and blind depths** (ADR1-8, still pending). A through step's
  leg now has support planes and yields to the plate thickness, face-level rung or overall extent
  that states it (lint already accepts those as the leg's statement); the plate, rung and overall
  stay controlling. A circular blind step's, hexagonal pocket's, rectangular blind slot's and
  plain flat-bottomed blind hole's depth now counts as the interval its callout states: the
  callout never yields, so a rung it states or closes is conveyed by it or shown as reference,
  and a boss height that a hole's depth states (a boss on the hole's floor) is conveyed by the
  hole callout. A tolerance keeps any of these controlling; `sheet/references` shows a yielded one
  bracketed. On the corpus, 4 legs (3 parts), 10 rungs (8 parts) and 32 boss heights (23 parts)
  leave mfcadpp drawings; NIST, gramel and cadgenbench drawings are unchanged and there are no
  new lint findings. Lint no longer reports two holes' callouts or schedule rows of equal depth
  as one measurement stated twice, and a pinned item that its canonical plan conveys by another
  is pinned in its own view (it was given its owner's, which could refuse the spec).
  Hole point ordinates and rebased ladder datums remain.
- **Leader and witness targets.** Lint checks flat, recess and slot callout tips against the
  faces their records publish, never a pocket's open mouth or a seat's chord
  (`flat_`, `recess_` and `slot_leader_target_*`), and a turned length's extension lines against
  its band's shoulders (`step_length_witness_target_*`). A chamfer, turned diameter or turned
  length whose leader or witness misses its feature no longer earns coverage credit.
- **Obround pockets.** Their leaders no longer aim at the box corners beyond the end arcs; 20
  MFCAD pocket callouts move onto the straight walls.
- **Thread depth lint.** A thread recognised from modelled geometry no longer has to state a
  depth: its trimmed span includes partial end turns and runouts, so it does not prove one, and
  its extent is shown by the ISO 6410 depiction. A depth from a Spec thread length or an AP242
  minimum full thread is still required. cgb243's three `thread.depth` warnings go away.
- **Rules catalogue.** Four Python clauses are retired rather than ported, with the maintainer's
  approval: ADR4-U1's angular sets (Rust draws no angular pattern locations, so no two automatic
  angles share a vertex), ISSUE-1505-b's off-page Z shoulder recovery (Rust places those
  shoulders on the main view) and solid Boolean cut caps (the detail clips exact projected
  intervals), and the modelled-thread half of LINT-hole_coverage-thread_and_profile. The three
  rules are marked ported, which leaves three pending.

### Fixed

- **Pin replay on multi-sheet plans** (#170). Pins replay in the unpinned drawing's frames, and
  later sheets keep its gutters and incumbent callout routes. FTC-10's five and CTC-03's one
  failing random-pin cells, accepted in alpha.13, now pass; `tools/known-pin-failures.json` is
  empty.

- **A fillet callout joined by equal blends is placed at its final size.** When quiddity calls
  one of two equal rounds a fillet and the other a blend, the blend is still joined to the
  fillet's callout after layout (`2× R23`), but the fillet callout now carries that count from the
  start, so its leader is placed with the label it ends with. A label that grew after placement
  could run past its view's area and push the sheet up: cgb241 went to A0 1:2 during the move to
  quiddity-rust 0.2.0-alpha.2 and is A3 1:5 again. Overridden callouts are joined as before.

- **Slot location lint agrees with the drawn end.** Lint judged a slot's `location_slot.length` at
  quiddity's recorded `lo`, while the planner draws the low end at the exact centre of the slot's
  faces less half its length. Each is rounded to 0.01 mm separately, so on quiddity-rust 0.1.1 they
  fall either side of a 0.005 mm tie: cgb245 drew 285.965 and 79.075 against a recorded 285.96
  and 79.07, and both slots' locations were reported unverifiable. Lint now reads the end the
  planner draws. cgb245 has no slot findings again, and CTC-03's first slot location (112.547) is
  no longer reported unverifiable.
- **Identical slots share one width** (CTC-03's repeated label, accepted in alpha.13). Lone slots
  in one view with the same width, length and end radius, run the same way, share one counted
  width wherever they lie (`2× 19.05`); widths spanning one stretch still share as before.
  CTC-03's two slots, which alpha.12 read as 19.04/19.05 wide, now read the same 19.05 and were
  stated twice: repeated_label 1 → 0. Their length was already shared across its one span
  (`2× 107.95`), and each slot keeps its own `2× R9.52`. CTC-04 now reads `2× 75` and
  `2× 140` (repeated_label 2 → 0, line_through_feature 22 → 19; the `2× 75` label sits beside
  its 15 mm span, text_off_span 0 → 1, accepted) and FTC-09 `2× 6.35` (repeated_label 1 → 0,
  line_through_feature 2 → 0). Sharing lengths and end radii of
  identical slots on different spans too was tried and left out: it moved CTC-03's radius
  leader onto an extension line and put FTC-08 on A1 at 1:1 with three extensions through a
  slot.
- **CTC-01's radius-centre table: correct as it is** (listed as a known regression in alpha.13).
  alpha.12 tabulated the 4× R25 corners only because the 350 row pitch was witnessed from the
  grid's left-hand column and its extension lines crossed the 4× R25 leader (two
  `annotation_leader_crossing` findings). The pitch is witnessed from the grid's first member,
  which quiddity-rust lists in the right-hand column, so its extension lines are short and
  cross nothing, and the leader is not obstructed. The 4× R25 callout stays; it meets its arc off
  radial, as the counted leaders on CTC-02 and FTC-09 do, which are kept by design. The pitch's
  station still follows quiddity's member order.
- **Manufacturing table with no room.** When the thread and knurl requirements table has no place
  on the sheet (only reachable with an explicit `sheet/size` and `sheet/scale`), the callouts now
  state each requirement in full instead of pointing with `SEE MFG n` to a table that is not drawn,
  as draftwright does. The `table_not_placed` error goes away with the table, and the plan keeps
  the requested spec; an info finding `manufacturing_table_fallback` names the requirements printed
  in full. Drawings with room for the table are unchanged (ISSUE-1298-a).

### WASM API

- `Engine.openSpec(json)` returns `{spec, set_aside: [{path, entry, reason_code}]}` (#191).
- `specOps(before, after)`, a module function, returns the ops JSON (#191).
- Refusals carry an additive `path` for an entry the part does not have.

## 0.1.0-alpha.13 (2026-10-23)

Now on quiddity-rust **v0.1.1** (previously `0.1.0-alpha.8`). This release picks up quiddity-rust's fixes
for #69, #70, #71, #75 and #76, and its improvement to #72. Some drawings get worse, and those regressions are
accepted for this alpha (listed under **Known regressions** below). Fixes are planned for alpha.14,
on quiddity 0.1.2 and in draftwright.

### quiddity-rust v0.1.1

- **Grid and frames.** Framed positions are on the 0.001 mm grid (#69) and near-axis frames are
  treated as axis-aligned (#70), so draftwright's origin-relative re-rounding and near-axis remap
  are removed. A sub-floor snap stays for values quiddity still publishes with frame round-off.
- **Plates, CTC-04 and CTC-05.**
  - GRM-03's plate is 2.5 thick again, not 2.25 (#71).
  - CTC-04's 12 false countersinks, which were pocket-rim chamfer corners, are gone (#76).
  - CTC-05's shaft outline is drawn visible (#75).
- **Framed records restored to file axes.** Records on frames that are only partly aligned have
  their turned-step ends, turned-profile axes and flat axis lines mapped back to file axes.
  threaded_connector_109 keeps its 7 turned steps, 3 grooves and flat, and stays A3 1:5.
- **Speed.** Planning CTC-02 rc costs 1.13× main's instructions; FTC-06 is unchanged.
- **WASM size.** The raw WASM grows by 622 KB, to 17.34 MB. The size budget is re-baselined on
  that build, with a ceiling of 19.1 MB.

### Known regressions (fix in alpha.14)

- **Frame inference rotates axis-aligned parts** (quiddity-rust #81).
  - CTC-02 AP242: +15 undimensioned features, and "10× R25" is lost.
  - FTC-07 AP242: A1 1:1 → A2 1:2, +21 undimensioned features.
- **Layouts that change with the move to framed records.**
  - FTC-06: A1 1:2 → A2 1:5.
  - FTC-10: 1×A1 → 2×A2.
  - STC-06 AP242: A0 1:1 → A2 1:5.
  - 10119: A4 2:1 → 1:1.
  - CTC-01 rd: the radius-centre table is no longer planned, and 4× R25 is a placed callout.
  - CTC-01 merge: 21 → 30 warnings.
- **Other changes in quiddity's output.**
  - CTC-02 rc: the 49.5 hole array is not found (#82).
  - A countersunk hole loses its seat on the countersink-two-face fixture (#83).
  - 1000.step: the plate's top is off the grid (#84).
  - whistle_frame_reference: the plate is 7.3 thick, not 7.0 (#85).
  - cgb217: its ø60 band is read as a boss, not a turned step.
- **Draftwright's handling of framed records.**
  - cgb202: 16 fillets are read as blends (51 → 86 warnings, 1 error).
  - cgb207: "ALL UNSPECIFIED RADII" is not planned, and R5 is undimensioned.
  - cgb245: slot ends round the other way on a 0.005 tie.
- **Random-pin acceptance.**
  - FTC-10 fails five pin cells, because pin replay on multi-sheet plans re-packs the views
    (draftwright #170).
  - CTC-03 fails one pin cell.
  - Both are listed in `tools/known-pin-failures.json`; every other part and cell must pass.

### Added

- **ISO 5457 sheet furniture on every sheet.** Grid zones (numbers along the top and bottom,
  letters down the sides, skipping I and O) are now drawn on single-sheet drawings too, not
  only on multi-sheet ones. Each sheet also has centring marks: 0.7 mm lines at the midpoint of
  each side, from the trimmed edge to 5 mm inside the frame. A mark stops at the frame where
  it would meet the title block, a table or other drawn ink.
  - Everything is in the margin outside the frame (apart from the short mark ends), so sheet
    size, scale, view placement and annotations do not change.
  - `sheet/zones: false` omits the zones and centring marks. Zones that section
    cross-references cite (`SHEET n, ZONE …`) stay, with one `sheet_zones_kept` info finding.
- **Thread depth lint.** A modelled internal thread shorter than the hole it is cut in must
  state its depth: lint reports `hole_requirement_missing` for `thread.depth` when the callout
  claiming the thread gives no `↧` depth. cgb243's M14×1.5 and two M62×1.5 threads are reported.
  The callout does not print the depth yet: on cgb243 that makes the leader search draw two
  bolt-circle labels on top of each other.

### Changed

- **The title block sits flush with the frame.** Following ISO 7200, its right and bottom edges
  are now the frame line on every sheet, instead of 1 mm inside it, which drew a doubled line
  with a thin gap. Those two sides are drawn once, as the frame's line. Its size and contents
  are unchanged; the views reserve exactly its area, and the scale note, projection symbol and
  default Ra symbol move with it.
- The Python-rules catalogue (`docs/rules`) applies the 2026-10-22 evaluation of the 30 pending
  rules: 2 met in full and 14 ported for what Rust implements, each naming the clauses retired
  and why; `leader_shaft_unverified` retired; ARCH0020-1 left pending on
  quiddity (quiddity-rust #69–#72); stale notes corrected. 11 rules remain pending (ISSUE-1298-b was since ported with document PMI on the part's solid, and ISSUE-958-a with the two-channels fixture). Two new
  tests check that a fillet radius leader moved off its arc is reported, and that omitting a
  threaded hole's callout reports its thread rather than losing it.
- **Profile-angle lint credits determined corners.** Two kinds of outer-profile corner no
  longer report `angular_requirement_missing`, because the drawing already fixes them:
  - the chamfered cap of a regular polygonal boss, whose sides stay parallel to the boss's
    flats, so the `HEX … A/F` callout states its angles (NIST CTC-01's two 120° corners);
  - a corner between a principal line and the common tangent of two arcs centred on located
    hole or boss axes (GRM-04's two 96° taper corners between R3 and R4).
  Real gaps still report: the corners on cgb202, cgb207, CTC-03, CTC-04 and FTC-08 have no
  stated size or angle on the drawing. Drawings are unchanged.
- **ISSUE-958-a ported.** The forward-order two-channels fixture held only one occurrence of
  the channelled block, so it reported one channel (reported upstream as quiddity-rust #73, which
  was the fixture, not recognition). It is replaced with quiddity-rust's
  `two_channels_ordered.step`, and both orders now report two channels.
- **Ruled-out sheet requests say why.** A paper and sheet-count request that cannot fit at any
  readable scale was already skipped without planning. Its candidate now carries
  `kind: "ruled_out"` and an outcome giving the first condition that fails, in paper mm against
  the drawable area (for example FTC-09: `A4 up to 1: ruled out: view v:front with its annotation
  bands needs at least 305 × 217 mm at 1:2; A4 drawable area is 277 × 190 mm; …`). The same
  requests are skipped as before.

### Fixed

- **Holes and bosses on inclined axes get a callout** instead of none (defect E2). The callout
  goes in the view that sees the feature most nearly end-on, where it shows as an ellipse. The
  leader lands on an end of the ellipse's major axis, on the rim nearer the viewer.
  - Every view that sees it as an ellipse can carry it, so `dimensions/<id>/view` can move it,
    and a part whose best view is not drawn uses the next one. On an exact 45° tie, x is taken
    before y before z.
  - Inclined holes and bosses are still not located, and a boss's height is still not stated:
    those need a view square to the axis. Each reports this as `feature_not_dimensioned`.
    Inclined bosses of one diameter are counted once only along one line.
  - A plan-view hole table leaves inclined holes as callouts. Its rows give a direction along
    z and a plan location, and an inclined hole has neither.
  - nist_ctc_03, nist_ctc_04, nist_ftc_07 and cgb217: no hole_requirement_missing or diameter
    feature_not_dimensioned findings remain (22 → 15, 43 → 34, 16 → 12 and 19 → 9 lint
    findings), on the same paper as before.
- **One owner per measurement** in four more cases (ADR1-8, ADR4-U1, ISSUE-1505-b).
  - A recess location on a stated face level is stated once, by the level's rung, even when the
    two print their value to different precisions (ftc09: `128.100` and `128.1` → `128.100`).
  - Equal sizes over one span are counted together when recognition reads their ends a few
    micrometres apart (cgb202: `255` and `3× 255` → `4× 255`).
  - A polygonal boss's height on a stated level is conveyed by that level and the overall, as a
    round boss's is (ctc01: `50` → covered by the `100` rung and `150` overall).
  - A closed chain leaves its longest member to the total, never a short shoulder, and lint
    accepts a length the stated chain determines (cgb217: the 1 mm shoulder is drawn and the
    `45` is covered; `axial_length_missing` 1 → 0).
- **Document PMI on the part's own solid.** A general tolerance or default finish presented on
  the part's solid, not on a face, is a requirement of the whole part. GRM-03's ISO 2768-m now
  fills the GEN. TOL. cell and its Ra 3.2 is drawn beside the title block. Before, both were
  `pmi_not_extracted` errors and the sheet reported `general_tolerance_missing`.
- **Threads and knurls on bosses.** A Spec or AP242 thread or knurl on a boss prints on the
  boss's diameter callout, as on a turned step: an external thread's designation replaces the
  diameter (`M31.75 x 1.5`), and the boss keeps a callout of its own. Before, it was a
  `pmi_not_lowered` error worded with internal names.
- **Leader tips on flat-cut bands, chamfers and fillets** land on the feature's own drawn
  face.
  - A turned diameter on a band that flats cut is read first in the profile view where the
    band's outline is its cylinder. Elsewhere it leads to the edge of the trimmed cylinder on the
    flat, not to the full circle in air (string_post's ø3.4, ctc05's ø558.8).
  - A turned chamfer or fillet turns about the coaxial band that holds it, not only about the
    axis of a part turned whole. ctc05's 4× C6.35 no longer tips at the shaft's axis.
  - A fillet callout whose tip would miss every arc its view sees end-on now leads to one of
    its members' arcs, never a straight edge (ftc10's 41× R1). Its shaft may still approach
    more than 15° off radial, so `radius_leader_not_radial` remains on ftc10 (E4 stays open).
  - Lint checks turned-diameter and chamfer leader tips against those faces
    (`step_diameter_leader_target_mismatch`, `chamfer_leader_target_mismatch`). Each has an
    `…_unverifiable` info when the view cannot show the target, or when the tip misses the
    members it can measure while others in the callout cannot be measured.
- **Oblique radius exceptions no longer withdraw the general radius note.** A blend on an
  oblique axis has no arc in any principal view, so it gets no leader of its own. When a fillet
  leader already states the same radius, its count now includes the blend, so the radius is
  stated and `ALL UNSPECIFIED RADII` is placed instead of a `notes_not_placed` error (NIST
  CTC-03, FTC-07; cgb207 loses this on quiddity v0.1.1, see Known regressions). With no equal leader the note is still withdrawn, as an error.
- **Hexagonal stock states its overall extents.** The envelope lint now credits a stock's
  length with the extent along the bar, and its `HEX … A/F` callout with the extents across
  it that the regular section reaches, whatever its roll. The three false
  `missing_principal_dimension` warnings on the stock bars (roll17 among them) are gone.

### Performance

- **Late callout repair.** Routes the repair must refuse on geometry or on their first
  measurement are skipped before they are measured, and repeated measurements are computed
  once. Planning the quality set costs about 25% fewer instructions, and every plan and sheet
  SVG is byte-identical.

### Development

- CI: quality-pins measures each part once per cell and gates in one job; the acceptance jobs
  start from a separate binary build; test shards are balanced by recorded duration. A main run
  takes about 22 minutes, down from 32. A push to main that changes only release metadata runs
  lint alone when CI passed on its parent.

## 0.1.0-alpha.12 (2026-10-22)

Still on quiddity-rust `0.1.0-alpha.8`. The move to quiddity-rust 0.1.0 is waiting on
quiddity-rust #69–#72.

### Added

- **Compact dimension spacing.** By default, the first dimension line stays 10 mm from the
  outline and later lines stay 7 mm apart. When a one-sheet layout runs out of room, the same
  paper and scale are tried with compact spacing before a smaller scale, larger paper or more
  sheets.
  - Compact spacing puts the first line 7 mm from the outline, then 5.5 mm between lines. That
    leaves 1.5 mm clear above each label and clears the 2 mm extension overshoot.
  - The retry runs only when compact lanes could make the room: a dimension dropped for space,
    a page-fit verdict, dimension ink off the sheet or over the title block, or clipping that
    compact lanes could cover (no view outline larger than the frame). It does not run for
    overlaps or unrelated furniture.
  - The usable test (overlaps, leaders, legibility, lost information) still decides whether a
    compact layout is accepted. Splits onto more sheets keep normal spacing.
  - `sheet/dimension_spacing: normal | compact` states the intent: `normal` forbids the
    fallback, and `compact` uses compact spacing for every candidate, including an explicit
    paper and scale.
  - The plan records `sheet.dimension_spacing: "compact"` when compact spacing is used (absent
    means normal), and the sheet choice text adds "with compact dimension spacing". The Spec
    is not changed.
  - A local fix offered on a compact drawing keeps `sheet/dimension_spacing: compact` along
    with its paper, scale and count. An explicit `sheet/dimension_spacing` edit releases an
    inherited `sheet/keep_layout` hold, as paper, scale and sheet-count edits do.
  - Drawings that change: mfcadpp 1013 goes from A3 2:1 to A4 1:1, with the same problem
    counts. The prismatic test fixture without its isometric stays at A4 2:1 instead of
    dropping to 1:1. No other part in the 63-part comparison (quality set, NIST CTC/FTC, NIST
    STC 06–10, 40 mfcadpp parts) changes (#163).

### WASM API

- **Spec:** `sheet/dimension_spacing` (`"normal"` or `"compact"`) is added.
- **Plan:** `sheet.dimension_spacing` is added; it appears only as `"compact"`.
- **MCP:** `sheet.dimension_spacing` (`normal | compact`) is added to the MCP Spec schemas.
- **Options:** the sheet item's settable fields list `sheet/dimension_spacing` with choices
  `normal` and `compact`.
- Method signatures are unchanged.

### Performance

Ratio of retired CPU instructions for `draftwright plan`, alpha.12 ÷ alpha.11, from
interleaved runs on PR #167 (the only change since alpha.11):

| Part | Ratio |
|---|---|
| NIST CTC-01 | 1.00 |
| NIST CTC-02 | 1.00 |
| NIST CTC-04 | 1.00 |
| NIST FTC-06 | 1.00 |
| NIST FTC-09 | 1.01 |
| cgb243 | 1.00 |
| cgb245 | 1.06 |
| string_post | 1.00 |
| MFCAD++ part 1000 | 1.00 |
| MFCAD++ part 10163 | 1.00 |
| MFCAD++ part 1013 (changed drawing) | 1.35 |

- cgb245 makes one compact retry on A4, which fails.
- 1013 pays for the trials that end on A4 compact.

## 0.1.0-alpha.11 (2026-10-21)

### Added

- **Preferred sheet size and count.** `sheet/preferred_max_size` (A4–A0, default A3) and
  `sheet/preferred_max_sheets` (integer ≥ 1, default 2) steer automatic paper choice:
  - The search tries one sheet from A4 up to the preferred size, then more sheets at that
    size, then one paper size up at a time.
  - A candidate is never chosen if it clips a view or prints below a readable scale.
  - Otherwise it is refused only for unreadable ink, errors or lost information that the
    automatic first plan does not already have. So a crowded but readable small sheet beats
    a blow-up.
  - No sheet carries only the isometric unless the author put it there. A split onto more
    sheets is also refused if it adds leader runs or hidden-edge crossings.
  - Going past a preference reports one `sheet_preference_exceeded` info notice, and a fresh
    automatic plan no longer carries a `layout_replanned` notice.
  - An explicit size, scale or count, `sheet/keep_layout` and `sheets/max` still win.
  - Drawings that change: CTC-02 goes from A0 to A1, 1013 and string_post_tapped from two A4
    sheets to one A3, cgb243 from one A1 to two A2 sheets, and bracket_positions from A1 2:1
    to A2 1:1.
- **Finding impact and groups.** Every plan problem, lint finding and structural finding
  carries an additive `impact` (`incomplete`, `decision`, `readability` or `advisory`) and a
  stable repeat-cause `group`. These appear in serve, WASM and compact MCP results.
  - Review adds per-impact and per-group counts, also returned by the MCP review tool.
  - Severity, finding order and drawings are unchanged, and gaps are never promoted to errors.
  - On NIST CTC-01 (`nist_ctc_01_asme1_rd`), review shows 8 incomplete findings in 3 groups,
    1 decision, 1 readability, and 13 advisory findings in 4 groups.
  - See `docs/finding-impact.md` (#152).
- **Interior callouts.** Feature callouts, leader-attached GD&T frames and AP242 surface
  labels that cross other ink are retried over blank face inside their own view, where the
  geometry confirms there is room. This includes inward diameter and thread leaders over hole
  floors.
  - Text height stays 3 mm, and equal costs favour exterior routes.
  - GD&T frames now route around neighbouring views.
  - On NIST CTC-01 AP242, leader-crossing warnings fall from 25 to 10 (53 → 38 problems in
    total), with no other problem code changing.
  - Across 68 parts there were no regressions in other problem codes, sheet size, scale or
    retained information (#147).
- **Frameless datums are drawn.** Datums declared in STEP AP242 or the Spec are drawn even
  when no control frame cites them.
  - `sheet/uncited_datums: false` hides uncited symbols, and `annotations/<id>/omit` hides one.
    Omitted symbols keep their provenance in the plan.
  - Datum leaders that end on the frame border are no longer reported as
    `leader_line_through_text` errors; a shaft through the datum letter still is. This removes
    95 false errors on cited datums across 31 test parts (#141).
- **Leader crossing kinds.** Retained crossings are classified, in additive `crossing_kinds`
  metadata, by what they cross: a leader, dimension line, extension line or arrow.
  - Leader-through-text is marked `ink_is_leader`.
  - Crossed linear dimension lines are reported as `dimension_line_crossing` warnings.
  - Drawings are unchanged (#144).
- **Local fixes keep the layout.** A fix with local scope keeps its sheet size, scale and
  count, and `sheet/keep_layout: true` states the same intent for any edit. No page
  coordinates or item lists enter the Spec.
  - An offer discloses any new findings that applying it would introduce.
  - Conflicts are refused, and moved views are named.
  - An edit that does change paper, scale or count reports `layout_replanned`.
  - A later explicit paper, scale or sheet-count edit lifts the hold.
  - See `docs/local-fixes.md` (#126).

### Changed

- `sheet/preferred_printer` is removed. A Spec that still carries it is refused with guidance:
  an A4 printer becomes `sheet/preferred_max_size: "A4"`, an A3 printer `"A3"`.
- **Radius leaders are radial.** The arrow meets the middle half of the arc within 15° of
  radial, from whichever side gives the clearer route (ISO 129), so a small fillet on a cutout
  can be called out from the surrounding face.
  - A blocked case keeps its callout and reports one `radius_leader_not_radial` warning (new).
  - Sections the engine cannot verify stay informational (`radius_leader_target_unverifiable`)
    (#145, #146).
- **Datum leaders leave square to the face.** Datum frames are placed on the face normal
  through the triangle. Failing that, they take one bend after a square stub, and an oblique
  leader is only a reported last resort.
  - A drawn datum leader, automatic or pinned, that does not leave square is reported as the
    lint warning `datum_leader_not_normal` (new).
  - Datum survey (33 parts): crossings 1,063 → 1,040, overlaps 27 → 23 (#146).
- **Fix descriptions in drawing language.** Each fix says in one sentence what applying it
  changes, for example "Move the M14×1.5 callout above the front view, nearest the view."
  Sheet-wide fixes add what happens to placements already moved by hand. Fix operations are
  unchanged (#138).
- **Pinned group callouts** point at the member nearest their elbow, keeping the automatic
  member on ties. Existing pins keep their label positions; only the member the arrow lands on
  can change (#149).
- **Messages.**
  - PMI that Specify wrote is credited to Specify rather than the STEP file, and a compatible
    Spec thread designation refines it.
  - Override and lint messages no longer show internal entity IDs or Spec paths.
  - `unclaimed_geometry` is informational (#102, #25).
- **Verbose MCP problems** keep their code and subjects inline, plus the preferred fix, its
  scope and the full trade description when they fit. `detail_ref` keeps every full
  alternative, and no offer hides a trade (#151).
- The advertised MCP schema ceiling is 40,000 compact bytes (was 38,000), to make room for the
  sheet-size preference keys.

### Fixed

- **Per-face surface finishes** place beside their face, clear of the dimensions, and no
  longer force a larger sheet.
  - A finish leader stays a text height away from lines it runs alongside.
  - A cylinder trimmed by flats gets a dot-ended leader on its face (ISO 128-22).
  - `annotations/<id>/place/side` picks either side.
  - GD&T placed only left of the front/plan column no longer pins the section row.
  - string_post with two Spec finishes: A3 → A4, crossings 3 → 0, no undrawn finishes (#98).
- An explicit view placement outside the drawing area is a plan error, with a fix that removes
  the placement (#95).
- Spec surface finishes no longer trigger the dimensional location audit and invent
  missing-location warnings (string_post with Spec finishes: 18 → 15 problems).
- Malformed browser requests no longer abort the WASM module, which keeps answering
  afterwards (#134).
  - Edit paths over 64 components and view gutters over 1189 mm are refused before work
    starts.
  - Conflicting dimension ownership returns an error.
  - A malformed projected spline returns an `edges_not_drawn` problem.

### WASM API

- `calloutOffset` and `Session::callout_offset` return `members: [{centre: [x,y], radius,
  span?: {start, sweep}}]` (#149). Sites are paper mm, y up; arc spans are counterclockwise
  radians. Existing fields are unchanged.
- **Spec:** `sheet/preferred_printer` is removed (refused with guidance), and
  `sheet/preferred_max_size`, `sheet/preferred_max_sheets`, `sheet/keep_layout` and
  `sheet/uncited_datums` are added.
- **Findings:**
  - Every finding gains additive `impact` and `group`.
  - Leader crossings gain `crossing_kinds`, and `annotation_ink_overlap` gains `ink_is_leader`.
  - Review gains per-impact and per-group counts.
  - New codes: `dimension_line_crossing`, `radius_leader_not_radial`,
    `datum_leader_not_normal`, `sheet_preference_exceeded`.
- Method signatures are unchanged.

### Performance

Ratio of retired CPU instructions for `draftwright plan`, alpha.11 ÷ alpha.10, from
interleaved runs; below 1 is faster:

| Part | Ratio |
|---|---|
| NIST CTC-01 AP242 | 0.89 |
| string_post | 1.00 |
| MFCAD++ part 1000 | 1.01 |
| cgb243 | 1.03 |
| NIST CTC-01 (native STEP) | 1.08 |
| NIST CTC-02 | 1.11 |
| NIST FTC-09 | 1.89 |

- FTC-09's cost is the preferred-size search: its A2 candidates fit by footprint, so they are
  planned and judged before A1 is kept.
- On native CTC-01, #146's leader search measured +9% on its own PR.

## 0.1.0-alpha.10 (2026-10-20)

### Added

- Modelled screw threads are drawn by ISO 6410 convention (#137). The helix faces of a
  recognised thread are replaced, for drawing only, by a smooth surface at the crest diameter:
  thick crests, thin roots, termination lines, a thin three-quarter root arc in end views and
  section hatching to the crest. The part, face IDs and recognition records are unchanged. The
  info problem `modelled_thread_not_depicted` is now reported only when depiction is turned off
  for the thread or the sheet, or is unavailable.
- Thread callouts state the designation only, inferred from a unique ISO 261 diameter/pitch
  match (e.g. `M75×1.5`; pitch is always stated, coarse or fine; multiple-start and left-hand
  threads in ISO 965 form, e.g. `M20×Ph2P1-LH`). With no unique match the measured values are
  stated, e.g. `⌀110 × P6.35`, with info `thread_designation_measured`. No length is stated: the
  depiction shows the thread extent, and the measured span stays in the plan as evidence. A
  semantic PMI thread on the same feature owns the callout in `pmi: "first"` and `"strict"`,
  ahead of any Spec `designation`; `"ignore"` uses recognition alone.
- Per-thread Spec override `features/<id>/modelled_thread`: `depiction: false` draws the
  modelled edges again, and `designation` replaces the callout text verbatim. Schemas and MCP
  item options include both. A `modelled_thread` override on any other feature is refused
  ("features/<id>/modelled_thread requires a recognised modelled thread."), and designation text
  is validated like a note. See `docs/modelled-threads.md`.
- On cgb243 the callouts are `M14×1.5`, `M62×1.5` (twice) and `M75×1.5`; on
  threaded_connector_109, `⌀110 × P6.35`. The other 98 corpus plans and SVGs are byte-identical.
  With depiction on, the helix edges are no longer projected. Retired instructions: cgb243 plan
  2,779 → 578 billion and render 3,010 → 696 billion; threaded_connector_109 render 4,127 → 299
  billion (plan unchanged, 147 → 146 billion). cgb243 has 21 leader crossings against 17 before;
  no labels overlap.

### Changed

- The two tests that pinned SHA-256 hashes of output (plans, SVGs and PDFs for notes identity;
  plans for legacy view alignment) are replaced by assertions of what they protected: a part
  without part PMI draws only its Spec notes, and explicit view alignments keep their edge and
  gap (#137).

## 0.1.0-alpha.9 (2026-10-20)

### Changed

- Pinned to quiddity-rust `v0.1.0-alpha.8` (#135). Recognition documents v3 and v4 are both
  read (v3 only without thread records). Modelled threads appear as `modelled_thread` features
  with their measurements, claimed faces and refusals; each is reported as info
  `modelled_thread_not_depicted`, in place of `feature_not_dimensioned`, until conventional
  depiction lands, and the modelled edges are drawn as before. On cgb243 (4 threads) and threaded_connector_109 (1), every dimension, the
  sheet and the SVG bytes are unchanged. Drawing fields are unchanged across all 100 corpus
  plans. Planning costs 1.7% more retired instructions over the corpus (mfcadpp/10038, the
  worst, +36%, 1.03 → 1.41 billion).
- Graphic PMI accounting from haecceity: geometry-only STEP files no longer report PMI
  presentation and no longer report `pmi_presentation_not_read` (the ten NIST geometry-only files
  1,595 → 0 in total; nist_ctc_01 AP242 keeps 71, nist_ftc_10 AP242 279). The plan, SVG and PDF carry the
  PMI loader binding `haecceity-pmi-read/8` (#135, for #125).

## 0.1.0-alpha.8 (2026-10-20)

### Added

- PMI modes: `pmi: "strict" | "first" | "ignore"`, `"first"` by default (#122).
  - `first`: semantic PMI takes precedence. A feature that PMI already sizes, or locates through
    a chain to an origin, gets no second engine dimension on that axis. Covered engine IDs stay
    in the omitted ledger with the IDs of their PMI owners.
  - `strict`: draws the part's semantic dimensions, callouts, GD&T and cited datum symbols only.
    Missing sizes and locations are reported as `feature_not_sized` and `feature_not_located`;
    no hole schedule is drawn. `dimension_source: "authored"` can
    add listed engine IDs.
  - `ignore`: draws the ordinary generated drawing and reports how many PMI items it withheld
    (`pmi_ignored`).
  - With no PMI, all three modes give the ordinary drawing; the 213 plain corpus drawings are
    byte-identical to alpha.7.
- Location origins (#122). Each axis's origin is the first datum, in letter order, that is a plane
  normal to it, or else the box minimum. `origin` overrides it per part axis with `"min"`, `"max"`
  or a face/feature reference (`{"x": "face:5", "y": "min", "z": "max"}`). An unusable datum gives
  info `datum_origin_unusable`,
  and dimension basis text names the selected origin.
- Located-once checks on parts drawn with PMI (`first` or `strict`) or with a Spec `origin` (#122). `feature_not_located`
  means no drawn dimension chain reaches an origin; `feature_located_twice` names independent
  chains locating the same feature axis. Plain parts keep their existing location checks.

### Changed

- Datum symbols are drawn only when a geometric tolerance cites them. Using a datum only as a
  dimension origin no longer draws its symbol (#122).
- Identical PMI spans with identical text share one drawn dimension, which claims every source
  record. Distinct authored constraints are kept even when redundant (#122).
- Hole schedules state every size and coordinate, grouped by size, including coordinates already
  given by retained PMI; a schedule measured from a selected origin labels it ORIGIN. Automatic
  schedules that would retain every source callout stay inline; `hole_table: "always"` still
  requests one, except in `strict` mode (#122).
- `schemas/spec.json` and the MCP tool input schemas gain `pmi` and `origin`; `pmi` is left out of
  saved Specs when it is `"first"`. The WASM API is unchanged (#122).
- PMI planning costs at most 1.12× alpha.7 in retired instructions; plain parts are unchanged
  within 1% (#122).

## 0.1.0-alpha.7 (2026-10-20)

### WASM API

- `calloutOffset(address)` and `labelOffset(address)` return a callout's or linear dimension's
  automatic anchor, current label position and offset (paper mm), so an editor can pin a label
  dropped anywhere, inside a view included, with `place.offset`. `placementOptions` marks items
  that accept an offset (`"offset": "callout" | "linear"`) (#121).
- Problems in snapshots and deltas carry ordered fix alternatives (`fixes`), each with a
  `scope` (below); `placementOptions` gives each item's `operation_scopes` (#128).

### Added

- Prefer A4 for the automatic sheet. The automatic plan is the baseline; when it is not
  already one A4 sheet, forced candidates are tried and the first with no layout or
  readability problem the baseline lacks (including new leader-on-extension contacts) is
  taken, else the baseline stays. `sheet/preferred_printer` (`"A4"` default, or `"A3"`) sets
  the order: A4 printer 1×A4 → up to 2×A4 → 1×A3; A3 printer 1×A4 → 1×A3 → A3 overflow. The
  sheet basis records the choice. 16 of the 100 corpus parts move to smaller paper and none to
  larger (e.g. mfcadpp/10190 A1 10:1 → A4 1:1, NIST FTC-07 A2 1:2 → A3 1:5). The search adds
  about 5% planning cost in retired instructions; mfcadpp/10278, which tries every candidate
  before falling back, costs 1.82× (#130).
- Draw the part's semantic-text notes from PMI in the general notes block, before the Spec's
  notes, ordered by Specify's `note index` and then file order: Specify's free-text `general
  note`s, coating (`surface treatment` or `coating`), heat treatment and edge conditions. Values that state no
  requirement (`None`, `N/A`, `-`, empty) are info only; a value that does not name its process
  is prefixed (`Coating: Black`). `annotations/<PMI id>/omit` leaves one off the drawing. A Spec
  note repeating a PMI note is drawn once, with info `note_duplicated_in_spec` and a fix
  removing the Spec copy (#127).
- Every offered fix states its `scope`, `local`, `view` or `sheet`, from the Spec paths it
  writes (unknown paths count as `sheet`), and alternatives are listed local first; a sheet-wide
  fix says it re-plans the sheet. MCP (`list_problems`, `get_item`, `item_options`, preview and
  apply rows), WASM and lint output carry it. NIST CTC-01's leader and extension crossings at A3
  1:5 now offer local placement edits before the hole-table remedy (#128).
- `place.offset` pins a linear dimension's label as well as a callout's (#121).

### Changed

- Omit redundant reference dimensions by default instead of drawing them in brackets; each is
  a `covered` omission naming the dimensions that determine it. `sheet/references: true`
  restores them; pinned references and per-dimension `style: "reference"` still draw. The demo
  bracket's ladder becomes 8, 12, 20, 32, 48; 21 references go across 8 corpus parts (#120).

### Fixed

- Draftwright's own records in a STEP file (Specify's stored answers, Python Specify's earlier
  `schema 1` property, the editor's project export) are no longer reported as unread PMI;
  foreign attribute sets still are (#129).
- Isometric captions stay clear of reserved projection bands (#120).

### Internal

- The WASM size budget is re-baselined on this build's main at +10% (ceiling 17,300,000 raw
  bytes), after feature growth since alpha.6 (#128).

## 0.1.0-alpha.6 (2026-10-19)

### WASM API

- `sheetFragments(sheet)` and `sheetSvg(sheet)` give any sheet's fragments and SVG, as
  `fragments()` and `renderSvg()` give sheet 1's; `renderPdf()` has a page per sheet and
  throws an API error instead of returning bytes unconditionally.

### Added

- Pin a projected view's gap with `views/<name>/place/align: "projection"` while keeping
  geometry on its projection line despite asymmetric dimension bands. Valid front/plan
  and front/side pairs offer it first in `item_options`; other pairs refuse it with choices.
- Leave blank gutters between views by default: spare paper is shared between the gaps, up
  to 25 mm, and shrinks toward 0 only when needed to fit. Gutters never change the sheet size
  or scale; rows and columns move together, and pinned views keep their positions. Override
  with `sheet/view_gutter` (`0` disables).
- Choose thread and knurl callout form with `sheet/callouts/style` (`auto`, `compact`, `full`
  or `note`), overridden per feature by `features/<id>/callouts/style`. `auto` moves a
  callout that would still need more than two lines to `<size> SEE MFG n`.

### Changed

- Omit hidden edges from the isometric by default, as pictorial views conventionally do;
  `views/iso/hidden: true` or `sheet/hidden: true` still draws them (#117).
- Shorten thread and knurl callouts to the ISO 965 / ISO 6410 short forms: `M5-6g` without
  the coarse pitch or RH, the designation alone on a threaded diameter, two-line blind tapped
  holes (`M2-6H ↧ 2.5` over `⌀1.6 ↧ 4`), and horizontal knurl callouts with process wording in
  the numbered manufacturing note. Callouts wrap at 48 mm with real font metrics. 8 of the 11
  thread and knurl parts now fit on A4, up from 2 (#94).

### Fixed

- Print the modelled value: labels use the shortest form, up to three decimals, that equals
  it (`4.75`, not `4.8`); per-measurement Spec precision still wins (#92). Two audited
  exceptions keep a compact label where the longer one cannot be routed: NIST FTC-08's
  `8× R1.5` (modelled 1.524) and mfcadpp/1004's `⌀7.4 ↧ 5.9` (modelled 7.447, 5.86).
- Dimension a single flat by its distance (`7.4`) without `A/F`; `A/F` needs proven opposed
  flats (#93).
- Read the structured `full thread depth` property, so a tapped hole whose depth is given only
  by that property keeps its thread callout.
- Draw `⌀ ↧ ⌴ ⌵` in tables and numbered notes as in callout labels (SVG, PNG and PDF), keeping
  the text searchable. GRM-03's table printed `⌀1.6 □ 8`.
- Draw AP242 dimensions and tolerances on resolved derived axes and planes, preserving
  authored values, tolerances and MAX/MIN/AVG qualifiers. GRM-03 now carries its ten
  semantic sizes and lengths instead of geometric fallback dimensions.
- Keep datum symbols apart during placement so no two overlap or touch. NIST FTC-07 moves
  from A2 at 1:5 to A1 at 1:1; FTC-10, STC-09 and STC-10 also change.
- Default drawing numbers to a leading part-number token in the STEP product name, then
  the file stem (including numeric names such as `10000`), before `DWG-001`. Explicit Spec
  numbers, including blanks, win; readable titles retain the part number (#103).
- Print GRM-03's CAx-IF material (`CZ121 / CW614N / CuZn39Pb3`) in the title block without a
  false undrawn warning (#101).
- Keep radius leaders on the drawn part of trimmed arcs (quarter-cylinder edges, obround
  and slot-pattern ends) instead of the full construction circle.
- Prefer perpendicular location witnesses on either side and finite-gap witnesses before
  any oblique fallback; a fallback uses parallel 30° witnesses clear of material and other
  dimensions. On the 30 quality parts oblique witnesses fall from 16 dimensions to none.
- Lint radius-leader targets on circular steps and slot ends, and oblique witness ink.
- Stop reporting a STEP material as undrawn when the title block states it; retain the
  warning when a blank or conflicting Spec material replaces it.
- Describe missed feature recognition and unsupported STEP annotations in drawing language,
  with Spec remedies; retain tool/version and reader diagnostics in structured references.
- Prefer meaningful STEP product names for default titles, otherwise print readable file
  stems with spaces. Explicit Spec titles, including blanks, keep precedence.
- Explain that hole tables require X/Y locations from a datum corner and recommend the
  retained individual dimensions when those locations are absent.
- Clarify that view placement gaps measure between annotation bands, not part outlines,
  in the Spec schema, MCP item options and documentation. Tools/list stays at 34,477
  compact schema bytes.

## 0.1.0-alpha.5 (2026-10-18)

### Added

- Consume oriented chamfers from native recognition, claim their faces, and include equal
  oblique and principal bevels in one counted true-section C/size-and-angle callout.
- Dimension symmetric V-notches from ordered profile evidence with true included angles,
  theoretical sharp depths and datum locations. Their ordinary IDs support dimension edits,
  authored ownership and location schemes; lint verifies live values against source geometry
  and printed scheme links. Reserve acute-notch arcs before layout; bevel run midpoints do
  not introduce artificial automatic datum stations. Existing PMI angles on the same
  physical flank faces own the notch angle across normal-section heights, retaining their
  tolerance and implicit degree notation without a duplicate automatic arc. An existing
  bevel first leg owns a matching physical notch depth, keeping its callout without an
  extra depth line.
- Read native recognition/3 unified section recesses, source refusals, face levels
  and risers. The default path now uses the existing pocket/channel/blind-slot/
  passage drafting grammars and no longer warns that section recesses are absent
  from the document. Native and Python 0.4 readers retain unified pocket patterns: grouped count
  and size/depth callouts, pitch and absolute locations, independent coverage
  outcomes, and four retained radius requirements per rounded pocket. Five pending
  recess-reader rules and four formerly bridge-only rules have native regressions.
  The kernel is pinned to quiddity-rust release `v0.1.0-alpha.6`.
  See docs/native-recesses.md for reader and drafting details.

- Authored detail `from` selects an existing principal or section parent; `around` accepts
  a feature/face ID or a nonempty list whose crops are united. Missing members and
  degenerate projections in an explicit parent are refused. Mixed natural sources choose
  the view with most visible members and explain the choice in the basis. Saved strings
  remain compatible.

- Enlarged orthographic detail views for crowded turned chains and heads, thin terminal
  flanges, prismatic height ladders and authored feature crops. Details participate in sheet
  planning, carry stable view IDs and their own scale, and draw parent circles, letters,
  captions and relocated dimensions. A fitting iso keeps sheet scale beside a detail.
  Verification and Python differences: [detail views](docs/detail-verification.md).
- MCP `spec_schema {}` returns the full draft-2020-12 author Spec schema and its
  named `get_spec` read shape on demand. Tool schemas retain explicit typed top-level
  outlines. Against alpha.4 the per-session schema context falls from 42,464 to 34,477 bytes
  (−18.8%), including this release's new Spec fields.
  Nested Spec additions no longer grow tools/list; server validation and corrective
  guidance are unchanged.

- Spec dimensioning schemes by feature pattern, station set or principal view axis: chain,
  chosen-datum baseline and signed running ordinate, with stable references, session edits,
  one-owner networks and independent printed-link lint (#76 item 4).

- Spec label wording under `dimensions/<id>/text` and prose `annotations/<id>/text`:
  prefix/suffix or one `{value}` template retain every generated measured term. Explicit
  dimension `style: reference | basic` draws parentheses or a box. Full ink drives placement;
  plans expose the overrides and lint still reads live measured text (#76, item 5).
- Session/editor text and style options use existing Spec operations, preview and history;
  saved Specs round-trip and stale/merged label IDs are refused with a correction.
  Large MCP option replies retain full structured data and a lossless text-channel resource.
- Recognition corrections in the Spec (#76 item 2): reclassify supported cylindrical and
  rectangular features, group holes as a linear/grid/bolt-circle pattern, split recognised
  patterns, suppress spurious recognitions, and declare missed features from face fingerprints.
  Geometry supplies every size; saved face anchors and authored IDs survive re-analysis.
  Corrections share ordinary dimensions, schedules and independent lint, with `from_spec`
  provenance, transactional editor operations and MCP schemas inside the existing byte budget.
- Geometry-authored dimensions (`dimensions/d:authored:<name>`): principal or true distances,
  cylindrical/spherical diameters and radii, and planar-face/straight-edge angles, with stable
  fingerprint references, ordinary tolerance/placement, editor/session operations and independent
  geometry lint (#76 item 1). Duplicate drawn quantities are refused with their existing owner.
- Spec line-work switches per view and drawing-wide sheet defaults: hidden lines,
  centre marks, centre lines, thread depiction and knurl depiction (#76 item 6).
  Session options/edits support them; effective paths are reported in view `from_spec`.

### Changed

- Planning is faster on PMI-heavy AP242 files (#90): linear-dimension trials reuse their
  stroke geometry and footprint witness counts are memoised; with quiddity-rust alpha.6's
  bounded emptiness probe, NIST FTC-10's AP242 plan falls from 203 s to about 5 s. Every
  plan JSON and SVG on the 65-part survey is byte-identical to the output before this change.
- The plan-time budget is re-recorded at 2,352,592,623,176 instructions on the 65-part
  survey (from 3,166,420,524,176 after native recesses; margin still 15%).
- Development: integration tests are grouped into eight binaries, halving CI's build step,
  with a guard against unreachable test files (#91); long test loops are split into
  individual cases (#89); release-only PRs skip the build in CI (#86).

### Fixed

- Preserve archived Python 0.3.9 pocket callouts and individual datum locations.
- Repair displaced location witnesses through foreign features and overlapping
  callout text with bounded route moves that retain the existing physical guards.

- Avoid repeated projection and collision work during complete sheet searches with native
  recesses, retaining the original native and WASM release settings.


- Automatic sheet candidates require dimensioned turned-step intervals to span at least
  the printed annotation capital height in their owning view, including baseline replacements.
  Explicit scales retain their geometry and warn below this floor. A complete, legible
  A3 drawing keeps practical paper instead of growing to A2 solely for fill.
- Keep geometry-authored angle arcs local to their support intersection and place aligned
  dimensions at a clear parallel offset, moving unpinned linear dimensions out of their way
  before escalating the sheet.

### WASM API

- Additive Spec `dimension_schemes` map and scheme measurement claims; native and browser
  sessions share the same JSON vocabulary. Scheme fragment edits refresh placement options.
  Engine method signatures are unchanged.
- Additive recognition-correction Spec fields and Plan provenance use the shared native
  session model; binding methods and TypeScript signatures are unchanged.

- Establish the reviewed TypeScript API snapshot and changelog gate, a measured WASM size
  ceiling, and warning-only cross-package quiddity pin checks in CI and release notes.
- Additive Spec line-work switches and optional Plan view `line_style`/`from_spec`
  fields, shared with native sessions; no binding method changes.
- Spec JSON accepts authored feature crops (`detail`, `around`, `from`, `scale_factor`)
  and optional Plan view `detail` records; engine method signatures are unchanged.
- Additive Spec text/style fields and annotation/note `from_spec` JSON provenance
  through the existing session methods; no new JavaScript methods or signatures.
- Spec JSON accepts authored dimension fields; Analysis/7 exposes optional exact
  dimension-reference evidence. Engine method signatures are unchanged.

## 0.1.0-alpha.4 (2026-10-16)

### Added

- Browser `draftwright-wasm` workspace crate, moving the editor's live-session wrapper here
  (#69, #66). JSON-string operations and errors, SVG/PDF bytes, bundled fonts and panic hook;
  byte-based analysis and native-only adapter gates keep the browser independent of files.
- Dedicated `wasm-release` profile, Node/native session parity CI, and a published-release
  workflow attaching the web package and SHA-256 checksum. See `docs/wasm.md` for builds,
  package sizes and switching the editor to a pinned release asset.
- Spec-authored external spur gears, with an ISO 1328 gear data table, validated anchors
  and gear coverage and reconciliation lint; gears are declared, not recognised (#67).

### Changed

- Small parts try a measured single A4/A3 sheet, then a second A4/A3 sheet, before larger
  paper; second-sheet views are centred and section references name the right zones (#63).
- ISO 286 fit callouts show the class alone by default; `sheet/fit_deviations: true` draws
  stacked upper and lower deviations after the class (#72).

### Fixed

- Datum placement skips searches that cannot improve the result, restoring planning cost;
  planar datum extensions require the face to end within 1 mm of the view outline (#61).
- Pinned labels obey the shared-line guard, omitted authored-set callouts report a restoring
  fix, dimension edits cannot add extension crossings through the part, and thread inference
  requires a positive designation (#62).
- Schedule cells verify tolerance, precision and THRU evidence; manufacturing references
  require complete source-matched ink, and a retained leader crossing rolls back the whole
  table. Damaged cells lose evidence and descriptive tables cannot replace measurements (#64).
- Opposed coaxial taps retain their authored callout sides and GRM-04 retains its small-hole
  callout; plan provenance binds the full recognition input, ownerless occurrences state
  closed policy reasons, and assembly severity reaches every feature coverage family (#65).
- Prismatic bore labels keep their view row and turned bore labels clear the axis; lint
  checks overlaps and manufacturing source text, and finish values respect declared precision
  and multi-word parameters (#68).
- Middle turned bodies use their own corridors; prismatic shoulders need truthful evidence,
  omitted separating views report a restoring fix, double-D callouts preserve their roles,
  countersink seats stay independent, and hole-pattern counts and balloon allocation are
  checked (#70).
- Finish leaders stay on their source faces, oblique PMI uses its exact projected span,
  envelope bands and extent fits reach the ink, and requested precision reaches dimension
  chains and locations. Invalid or conflicting precision is refused; structured notes cover
  only their placed owner's explicit roles (#71).
- Full fit-label ink drives reservation and placement; dimension lines and arrowheads are
  obstacles, final settlement includes callouts, and lint counts linear arrow flares (#72).

### Fixed

- Optional detail requests no longer veto valid ordinary drawings or cap principal scale.
  Cropped edges meet thin zigzag break lines; captions follow real projected ink. Prismatic
  details keep the feature silhouette and use preferred enlargement scales. Extent queries
  map only the envelope, once per projected view, and large-paper omissions precede planning.
- Multi-step turned profiles use one inspection end by default, preserving PMI datum
  precedence and saved chain IDs. Cross-hole axial locations use the same end; baseline
  lanes run shortest nearest the part. Valid complete larger principal views take precedence
  over optional details under the practical-paper sheet rules.
- Visible edges take precedence over hidden edges in the isometric as well as principal
  views, including when a detail changes the isometric scale.

## 0.1.0-alpha.3 (2026-10-16)

### Added

- Gzipped STEP input (maintainer decision 2026-10-15): every command (`analyse`, `plan`,
  `render`, `review`, `parts`), serve and MCP request, cached registration and library entry
  point that reads a STEP file or its bytes (`api`, `model::analyse_step`, `model::parts`,
  `pmi::read`, `pmi::Reader::attach`, `Session::open`, `stateless::Cache::register`) accepts
  gzip, told by its magic bytes (`1f 8b`), not the file name, and decompressed in memory
  (`model::step_bytes`, `model::read_step_file`). The binding's `source_sha256` and the cache
  key are of the decompressed bytes, so a `.step` and its `.step.gz` bind, cache and draw
  identically (a `.step.gz` is titled by its stem without `.gz`). A gzip that does not
  decompress is refused as invalid input, saying so.

- Draw one chosen assembly part through the CLI (`parts`, `--part`), API, sessions and MCP,
  with its own geometry, PMI and material; drawing an assembly whole reports it (#28).
- AP242 external threads replace turned-diameter callouts with their designations; profile
  views draw thread minor-diameter lines and knurl bands (#35).
- AP242 surface textures draw as ISO 1302 face symbols or a part-wide default finish (#38).
- Pinned callouts accept a paper-space `place.offset`, including positions inside their view;
  sessions report the current offset and pinning it leaves the callout in place (#39).
- First- and third-angle projection via `sheet/projection` (#37); off-scale isometric views
  carry `ISO VIEW (NTS)` (#49).
- ISO shaft fits on turned diameters print their ISO 286 limits; `sheet/fit_limits: false`
  keeps the class alone (#42).
- Spec-authored feature and face datums and GD&T controls, sharing the AP242 drawing path;
  spec entries override STEP PMI and report undeclared datum letters (#47).
- Spec-authored surface finish, threads, knurls and leadered notes on features and faces,
  plus `sheet/default_finish`, through the same paths as AP242 PMI (#52).
- Structural lint checks labels against measured ink, angles against their rays and visible
  edges, datum-symbol distance, and nominal rounding (#50).
- Per-body length chains for parallel turned bodies, raised-pad sizes, heights and locations,
  and counted free-direction slot callouts; lint credits full extents and exact adjoining
  groups, and ambiguous groove ownership is refused (#55).

### Changed

- The Python-rules catalogue (`docs/rules`) applies the maintainer-approved triage of every
  pending rule: 12 retired, 91 translated with the Rust mechanism that meets each, 7 already
  met marked ported, and the 80 left to port tagged with value, size and stream; stale notes
  describe current Rust. `tests/rules_catalogue.rs` checks that every rule says what backs its
  status, every cited test exists and the README tables match the files.

- Pinned haecceity and quiddity to quiddity-rust `v0.1.0-alpha.4` (commit `f5ddb726`), from rev `7cb26d85`.
- Plan problems use drawing language and identify the affected faces and callouts;
  reference dimensions carry `reference` and `determined_by` (#29).
- Turned profiles measured from a datum end use baseline shoulder positions; existing band
  dimension IDs remain covered by those positions (#48).
- Faster hidden-line rendering through quiddity-rust, with render-tail acceptance budgets
  for cgb243 and threaded_connector_109 (#51).
- The single-sheet byte guard is opt-in (`DRAFTWRIGHT_SINGLE_SHEET=1`); its fixtures are kept
  without maintenance and CI no longer records them. Quality, leader-route, lint and test
  gates remain (#53).
- One owner per measurement: authored sets preserve covered items, ladder and shoulder sets
  are approved or omitted whole unless an explicit member entry conflicts, and closed boss
  heights become references. Lint reports redundant pattern locations and toleranced
  references (#57).

### Fixed

- Pinned sheet size and scale are judged on the finished drawing, with a verified fix for
  real misfits (#37).
- The third-angle projection symbol follows ISO 5456-2; basic cross-hole locations work
  across principal bore axes and replace the plain default with a boxed dimension (#40).
- Applied leader fixes re-space overlapping dimension lanes and keep outside text beside
  its line; plan problems and lint report shared lines and stray text (#46).
- Leader routing and lint share the material re-entry check; pinned callouts report re-entry
  with an unpin fix, diameter and radius leaders are checked against their targets, and
  callout arrowhead flares count as collision ink (#54).

- Datum feature symbols try the space inside dimension lanes before outer tiers, can use
  either proven cylinder silhouette, and can stand on drawn planar face extensions. Shared
  GD&T placement counts leader crossings with dimension and extension lines for frames,
  labels and surface finishes too. Complete arrangements cannot introduce a new annotation
  contact to clear an earlier leader; placement intents and pins retain priority.
  Empty placement resets restore automatic tiers. The CNC-review spindle's A and B clear
  the dimension stack.

- NTS captions retry with reduced clearance and nearby vertical positions before retaining
  a clash; structural lint reports shallow caption overlaps with text and line ink. Pinned
  placement refits use the same strict 5% sheet-scale snap as measured refits. An authored
  iso scale only skips infeasible-iso candidates during recovery; other errors propagate.

- `--part 0` (`"part": 0`) of a file of one part is the whole file: drawn byte for byte as without
  a part chosen, titled by the file's stem rather than the STEP product's name (`plate.step`
  gave `COMPOUND`). A part of a real assembly is still titled by its product's name.

## 0.1.0-alpha.2 (2026-10-14)

### Changed

- Pinned to quiddity-rust `v0.1.0-alpha.3` (from `v0.1.0-alpha.1`). Its recognition document's
  new `section_recesses` family (`SectionRecess`, `SectionRecessRefusal`) is admitted as known
  and not yet read, as the other recess families are; without that every part with a section
  recess was refused. No drawing changes: across the single-sheet guard's 1876 cases the new pin
  leaves SVG, PDF and PNG bytes unchanged and each plan differs only in its
  `features_not_recognised` warning (the recogniser's version, and section recesses counted from
  the document); the version bump then changes only the loader version in each plan's binding
  and each file's embedded Spec. The guard is re-baselined for this (`quiddity-alpha3`).
- Multiple sheets: a plan allocates views and tables across sheets (SHEET n/m), every sheet is
  rendered, sessions and MCP work on multi-sheet plans, and sheets carry ISO 5457 zones and
  section cross-references (`lint_sheets`).
- Leader routes keep callout text, arrowheads and elbows off extension lines and the outline;
  pinned callout labels keep their place and side.
- Fits print their ISO 286 limits (`sheet/fit_limits: false` prints the class alone); repeated
  spans are drawn once; the title block credits draftwright in DRAWN BY.
- DXF export is disabled until it is fixed.
- Stable identity (`draftwright.spec/2`): a dimension's ID is `d:<feature>/<parameter>`, never
  its view, so moving it to another view, omitting a view or changing the sheet or scale keeps
  it; the view is the item's `view` and may be chosen in the spec (`dimensions/<id>/view`). A
  PMI item's ID is its record's semantic identity (`pmi:<category>.<kind>:f…:ref=…`), never its
  Part 21 instance, so the same intent written again keeps it; records that cannot be told apart
  are refused. A `draftwright.spec/1` spec is migrated when planned; ambiguous keys are refused
  with their candidates (`docs/model.md`, "IDs").

### Added

- Typed state and problems: every plan item carries a `state` (`shown`, `omitted_by_user`,
  `not_authored`, `covered` by another item, or `not_drawable` with the code of the problem that
  says why), beside its basis sentence; every problem's `subjects` are live item IDs, with what
  else it names in `refs`, and a machine-readable `fix` (edit operations) where one resolves it;
  `items::items` and `items::label` list every item with the text it draws, an omitted
  dimension's and a GD&T item's label included (`docs/model.md`, "Plan").
- Placement as intent, a session core (apply, preview, undo and redo, queries) and a stateless
  MCP core with its cache and schemas.
- AP242 semantic `turned_knurl` PMI is drawn.

## 0.1.0-alpha.1 (2026-10-10)

The first alpha. Its gates and their state are in `docs/alpha1.md`.

### Added

- The document model (ADR R1): Analysis, Spec and Plan, with `analyse`, `plan`, `edit`,
  `render`, `review` and `spec` on the command line, as a JSON-lines service (`draftwright
  serve`) and as an MCP server (`draftwright-mcp`).
- Native feature recognition through quiddity-rust (`quiddity-rust/recognition/2`), read
  fail-closed; Python quiddity's JSON through `--features`.
- Layout with draftwright's sheet, scale and view arithmetic, reserved annotation bands, and
  page escalation and recovery; section A–A without a boolean.
- Dimensions per family (envelope, turned steps, holes and patterns, slots, bosses, fillets and
  chamfers, grooves, flats, steps and the height ladder, section recesses, hexagonal stock),
  with spec tolerances and ISO 286 fits; hole tables by drop-and-escalate.
- Lint of the drawing against the recognised features, independent of the Plan.
- AP242 semantic PMI: read through haecceity; feature control frames, datum feature symbols,
  and toleranced and basic dimensions drawn; surface and common labels drawn with leaders, and
  the general tolerance, default surface finish and edge-condition notes on the sheet; thread
  and knurl requirements on their owning hole's or turned step's callout, or `SEE MFG n` with a
  manufacturing requirements table.
- The title block's material from the STEP file's one usable material property when the spec
  states none, as draftwright does.
- Angular PMI dimensions between two planar faces, drawn about their vertex in the view that
  shows the angle true (nist_ctc_01's `60 ±0.5` in the plan); one no view shows true is refused.
- The AP242 files haecceity's PMI writer writes (8, from quiddity-rust's written fixtures) in the
  PMI reading, drawing and coverage comparisons against draftwright, every difference verdicted.
- Band space for GD&T: the layout reserves room for feature control frames, datum feature
  symbols and PMI labels, and places them inside their views' bands (draftwright reserves none).
- PMI on hole occurrences (AP242 edition 4 `basic_round_hole`), now that haecceity reads them:
  nist_stc_09's hole callouts and their position frames are drawn, counted over the pattern.
- For callers with no file system, such as a browser build: `model::analyse_step` and
  `analyse_native_step` analyse a STEP file from its bytes, and the bundled font is public
  (`api::MONO`); haecceity runs hidden lines on one thread where none can be spawned.
- SVG, DXF, PDF (subset fonts, searchable text) and PNG output, each carrying its Spec;
  deterministic to the byte in every time zone.
- The PMI coverage lint: every PMI item is on the drawing or reported.
- `plan` from a saved Analysis is fast again (the maintainer's render cache and straight-piece
  boxes, PR #1): about 16 ms on NIST FTC 06, from about 490 ms; drawings unchanged.
- Drawing quality on the 30 measured parts (`tools/quality_parts.txt`), against the 86ffcbc
  baseline: annotations off the sheet 34 to 0, overlapping views 34 to 0, views on the title
  block 1 to 0, repeated labels 50 to 18 (each remaining one verdicted), ink problems 506 to 282,
  with none over a view.

### Known limitations

- Oblique PMI lengths, a cone's angle, a thread or knurl on a boss and face-finish
  requirements are read and reported, not drawn.
- Section recesses and some other families are not read from quiddity-rust's document
  (`features_not_recognised`); an angled step is reported unsupported.
- Revision rebinding (ADR R3) is not implemented.
- 282 ink problems remain on the 30 measured parts (109 leader crossings, 155 cases of ink
  past a view's area, 18 lines through labels), four parts have accepted cells worse than the
  baseline, dense AP242 parts carry much more ink, and hole tables are drawn only by
  drop-and-escalate; the README's "Known limitations" lists each with its counts and verdict.
