# Thread and knurl lowering fixtures

The three `spindle_*.step.gz` files are the existing editor PMI fixtures described
in `tests/pmi_thread_knurl.rs`.

The four `*_shaft_*.step` files are synthetic regression geometry authored here
with build123d 0.13.0 and exported by OCCT, then given AP242 PMI by haecceity 0.5.1's
PMI writer (`Mode::Replace`, presentation policy `Refuse`). Dimensions are mm:

- `plain_shaft_thread`: diameter 10, length 30; external M10 × 1.5, 6g, right hand,
  thread length 30, on the cylindrical face.
- `plain_shaft_knurl`: the same cylinder; straight knurl, maximum/nominal diameter
  10, linear pitch 0.8, 39 teeth, tooth depth 0.3 and root fillet 0.05.
- `stepped_shaft_thread`: diameter-10 cylinder from z=0 to 30, united with a
  diameter-16 cylinder from z=0 to 6; the diameter-10 band carries M10 × 1.5-6g,
  thread length 24. The step owns the thread, never the turned body.
- `tapered_shaft_thread`: diameter-10 cylinder from z=0 to 20, united with a cone
  from radius 5 at z=20 to radius 3 at z=30; M10 × 1.5-6g on the cylindrical
  portion, thread length 20. Its body is not a plain-cylinder owner, even though
  its largest diameter is 10 and recognition produces no turned steps.

Thread partial-area and applied-shape references identify the same selected
cylinder; nominal size is 10. These additional schema fields make the persisted
PMI valid; no thread pitch is inferred from the kernel's thread-count field.

The Rust regression exercises both AP242 extraction and `pmi::from_model` on the
plain fixtures, checks exactly one claim on `d:envelope/od.diameter`, and verifies
the SVG's searchable labels. The negative fixtures check that neither stepped
nor tapered geometry acquires a turned-body manufacturing owner.
