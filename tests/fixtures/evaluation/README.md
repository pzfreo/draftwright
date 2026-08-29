# STEP analysis corpus v1 fixtures

These fixtures and their expected facts were authored by Draftwright project contributors for the
independent STEP-analysis evaluation. They are released under CC0-1.0. The oracle comes from these
construction recipes and nominal dimensions, not from recogniser output:

- All cases begin with a 60 × 40 × 12 mm block centred in X/Y with its base at Z = 0.
- `plain-block.step` has no subtraction.
- `blind-hole.step` subtracts a Ø10 cylinder at (10, 5) from Z = 6 through the top face.
- `ambiguous-open-semicircle.step` subtracts a Ø10 through cylinder centred on the X = 30 stock
  boundary. It is an open semicircular channel and deliberately not a hole.
- `topology-a.step` subtracts through cylinders Ø6 at (-15, 0) and Ø10 at (15, 0).
- `topology-b.step` is geometrically identical to `topology-a.step`; every Part 21 entity identifier
  was bijectively renumbered and the entity records serialized in reverse order. References were
  rewritten with the same bijection. This changes file/topology traversal order without changing
  geometry or the oracle.

`corpus-hole-patterns-v1.json` is a separate arrangement corpus. It deliberately scores one
grouping fact per pattern and does not repeat the member holes' diameters, depths, bottoms or
individual location requirements, which remain owned by `corpus-v1.json`:

- `pattern-grid.step` starts with a 120 × 100 × 12 mm centred block and subtracts six Ø6 through
  cylinders at X = (-15, 0, 15) and Y = (-10, 10). The authored arrangement is a 2 × 3 grid with
  20 mm row pitch and 15 mm column pitch.
- `pattern-ambiguous.step` starts with an 80 × 50 × 12 mm centred block and subtracts three Ø6
  through cylinders at X = (-20, 0, 21), Y = 0. Its adjacent gaps are 20 and 21 mm, so it is not a
  constant-pitch linear array; collinearity also prevents treating the three points as a bolt
  circle.
- `pattern-topology-a.step` starts with a 140 × 90 × 12 mm centred block. Four Ø6 through holes
  form a 32 mm bolt circle centred at (-35, 0), and three Ø4 through holes form an independent
  vertical linear array at X = 35 with 18 mm pitch.
- `pattern-topology-b.step` is geometrically identical to `pattern-topology-a.step`; every Part 21
  entity identifier was bijectively renumbered and the entity records serialized in reverse order,
  with references rewritten by the same bijection.

`corpus-flats-v1.json` is a separate physical across-flats corpus. Opposed faces on one stock line
are one requirement, while separate lines or disjoint axial spans remain independent:

- `flat-nonround.step` is a 40 × 30 × 20 mm rectangular block and deliberately has no machined
  flat requirement.
- `flat-lone-d.step` clips Ø30 × 40 mm stock at X = 7.5 mm to leave one X-normal D-flat.
- `flat-double-d.step` clips Ø30 × 40 mm stock at X = ±7.5 mm, producing one
  15 mm A/F requirement from two opposed faces.
- `flat-slanted-double-d.step` rotates equivalent Double-D stock 30° about Y, preserving its one
  physical requirement without relying on a principal-axis orientation.
- `flat-coaxial.step` fuses two disjoint axial lone-D spans on the same axis; each span owns its
  own A/F requirement.
- `flat-topology-a.step` fuses two parallel lone-D lobes, so equal nominal sizes on separate axis
  lines remain independent requirements.
- `flat-topology-b.step` is geometrically identical to `flat-topology-a.step`; every Part 21 entity
  identifier was bijectively renumbered and the entity records serialized in reverse order, with
  references rewritten by the same bijection.

The `FILE_NAME` timestamp is normalized. Each corpus manifest pins every fixture SHA-256 and records
case-level provenance. A changed fixture therefore requires an explicit corpus-version decision;
regenerating recognition output can never rewrite the expected facts silently.
