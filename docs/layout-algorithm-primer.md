# Layout Algorithm Primer

Draftwright turns a 3D part into a 2D technical drawing. The hard part is not drawing one
dimension; it is deciding where every view, dimension, label, leader, table, balloon, and
section should go without collisions.

Think of the layout engine as a small compiler:

1. **Analyze the part.** Measure the 3D bounding box, detect features such as holes, slots,
   steps, and envelopes, and decide which drawing views are needed.
2. **Choose the page and scale.** Try candidate page/scale combinations and score them by
   whether the views and annotation blocks fit with usable whitespace.
3. **Build view blocks.** Treat each orthographic view, section, detail, and isometric view as
   a rectangle with attached annotation space.
4. **Collect annotation candidates.** A dimension or frame is not placed immediately when a
   pass discovers it. It becomes a candidate with a target strip, size, priority, and builder.
5. **Solve strips.** For a strip above, below, left, or right of a view, the solver spaces
   candidates in one dimension. It keeps higher-priority candidates when the strip is too full.
6. **Reserve real footprints.** The solver uses actual rendered footprints where possible, not
   only label text boxes, so invisible parts such as arrows and leader shafts still take space.
7. **Escalate when needed.** If dense annotations do not fit, the engine can use a table,
   balloon ring, detail view, or controlled drop rather than silently overlapping items.
8. **Export.** Once the drawing is placed and linted, the same geometry exports to SVG, DXF,
   and PDF.

The important idea is **one authority for layout**. If one pass uses the solver but another
pass later adds a fixed-offset label, the solver's result can be invalidated. The destination is
that every automatic placement decision is visible to the same model before anything is
committed.

## Current Status

Recent work has moved most automatic layout surfaces toward that destination:

- Page/scale choice and repack now share one fitness model.
- Section A-A participates in layout selection instead of being a fixed-offset afterthought.
- Furniture uses fuller rendered footprints.
- Tables and balloon rings negotiate with layout instead of using simple first-fit placement.
- Below/right location and envelope dimensions now join the shared corridor solve.
- User-authored `locate(..., pin=True)` and generic `dimension(..., pin=True, priority=...)`
  edits become solver candidates instead of late fixed-position moves. A pin acts like an
  anchor and a high-priority keep signal inside the same corridor model.
- Generated scripts, README examples, and lint suggestions now steer normal edits toward
  feature-backed `dimension(...)` / `locate(...)` calls instead of raw page-coordinate
  `place_dim(...)`.
- Generated Sheet scripts now have a value-aware round-trip parity guard for normal geometry:
  prismatic parts, slots, patterns, counterbore/section cases, and turned/rotational parts
  are checked against direct builds by annotation name, type, dimension geometry, labels,
  callout coverage, and furniture boxes.

Remaining gaps are narrower and more explicit:

- Some placements are intentionally still outside the shared candidate model because they are
  not independent candidates yet. The main example is the front-right prismatic height ladder,
  where each dimension's witness base depends on the previous placed tier.
- Raw page-coordinate `place_dim(...)` is now deprecated for normal editable scripts and
  remains only as an escape hatch. It can place a one-off annotation, but feature-referenced
  `dimension(...)` is the route that participates in re-solving.
- Emitted Sheet-script AP242 PMI round-trip is still separate work: `import_step()` strips the
  semantic PMI before the generated script can re-read it, so PMI must be baked as declared
  features instead of re-extracted (#503).

The next destination is **complete editable-script trust**: ordinary geometry already has a
strong parity guard, and the remaining high-value gap is preserving PMI through generated Sheet
scripts without pretending it can be recovered from a stripped imported shape.
