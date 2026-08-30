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

`corpus-pockets-v1.json` is a separate physical lone-pocket corpus. Pocket-pattern members are
excluded because their grouped arrangement and callout belong to the pocket-pattern family:

- `pocket-through-negative.step` is a rectangular removal through the complete stock and is owned
  by Slot, not Pocket.
- `pocket-lone.step` has one off-centre 30 × 12 × 6 mm +Z-opening blind recess.
- `pocket-edge-anchored.step` crosses the +X/+Y stock corner, leaving one 15 × 15 × 6 mm recess
  whose position is implicit in the two envelope edges.
- `pocket-two-equal.step` has two equal 26 × 10 × 6 mm recesses at non-pattern positions.
- `pocket-opposed.step` has equal recesses on opposite Z faces, retaining distinct `open_sign`
  identities.
- `pocket-side.step` rotates the lone construction 90° about Y to exercise a principal X opening.
- `pocket-prismatic-negative.step` has a regular-hexagonal blind recess owned by PrismaticPocket,
  not the rectangular Pocket family.
- `pocket-compound.step` has one equal recess on each of two disjoint solids.
- `pocket-topology-a.step` has two distinct blind recesses in canonical Part 21 entity order.
- `pocket-topology-b.step` is geometrically identical to `pocket-topology-a.step`; every Part 21
  entity identifier was bijectively renumbered and the entity records serialized in reverse order,
  with references rewritten by the same bijection.

`corpus-pocket-patterns-v1.json` owns grouped rectangular-pocket arrangements without recounting
their member pockets. Its construction facts are independent of recognition output:

- `pocket-pattern-linear.step` starts with a 180 × 140 × 20 mm centred block and subtracts four
  12 × 8 × 6 mm top-opening pockets on a 30° line through the origin at signed distances
  (-45, -15, 15, 45) mm. The authored arrangement has 30 mm pitch and direction
  `(cos 30°, sin 30°, 0)`; it is not derived from the recogniser's reported direction.
- `pocket-pattern-grid.step` starts with a 200 × 170 × 20 mm centred block and subtracts six
  12 × 8 × 6 mm top-opening pockets on a 30°-rotated 2 × 3 lattice centred at the origin. The
  authored row offsets are ±18 mm and column offsets are (-28, 0, 28) mm, giving 36 mm row
  pitch, 28 mm column pitch and a 30° arrangement angle.
- `pocket-pattern-pair.step` contains two equal pockets. Two occurrences remain independent and
  deliberately do not meet the provider's pattern threshold.
- `pocket-pattern-ambiguous.step` contains three equal collinear pockets with adjacent 21 mm and
  20 mm gaps. The unequal gaps deliberately do not define a constant-pitch array.
- `pocket-pattern-topology-a.step` starts with a 180 × 130 × 24 mm centred block, subtracts an
  underside-opening 2 × 3 grid of 8 × 12 × 4 mm pockets at X = (18, 40, 62), Y = (-17, 17),
  and adds one differently-sized lone top pocket. The grid has 34 mm row pitch and 22 mm column
  pitch; the lone pocket proves that its seven physical pockets retain disjoint ownership.
- `pocket-pattern-topology-b.step` is geometrically identical to its topology-a counterpart;
  every Part 21 entity identifier was bijectively renumbered and the entity records serialized in
  reverse order, with references rewritten by the same bijection.

`corpus-grooves-v1.json` is a separate physical turned-groove corpus. Each annular recess owns
one axial-width requirement and one floor-diameter requirement:

- `groove-monotonic-negative.step` joins diameter 20 and diameter 16 shaft segments with one
  monotonic shoulder, so TurnedStep—not Groove—owns the geometry.
- `groove-lone-z.step` cuts one 4 mm wide band to a diameter 16 floor in a diameter 20 shaft.
- `groove-lone-x.step` and `groove-lone-y.step` rigidly rotate that construction onto the other
  two principal axes without changing its physical measurements.
- `groove-narrow.step` cuts a 1 mm wide circlip band to diameter 18. The reduced band is present
  in raw boss and turned-step inventories, but the aggregate Draftwright model gives its width
  and floor diameter to the Groove callout once.
- `groove-compound.step` contains two disjoint parallel shafts with equal grooves at distinct
  axis lines and stations, so equal sizes cannot collapse into one occurrence.
- `groove-topology-a.step` cuts distinct grooves centred at Z=-15 and Z=16 in that order;
  `groove-topology-b.step` applies the same two Boolean cuts in reverse order. Their STEP hashes
  differ while their geometry and independently authored groove facts are identical.

`corpus-chamfers-v1.json` is a separate physical bevel corpus. One planar or conical treatment is
one callout requirement even when equal specifications share ink:

- `chamfer-plain.step` is an unmodified 60 × 40 × 30 mm block and has no bevel requirement.
- `chamfer-planar-z.step` applies a 6 mm equal-leg bevel to its +X/+Y edge;
  `chamfer-planar-x.step` and `chamfer-planar-y.step` rigidly rotate that construction onto the
  other principal edge axes without changing its physical size.
- `chamfer-asymmetric.step` applies a 4 × 8 mm bevel whose construction angle is atan(4/8).
- `chamfer-turned.step` applies a 3 mm conical treatment to the +Z end of diameter 30 × 60 mm
  shaft stock.
- `chamfer-overlap.step` contains a partial-width ramp with triangular blind ends and a separate
  3 mm full-length bevel. The ramp slant is AngledStep-owned; only the independent X edge enters
  this corpus's chamfer denominator.
- `chamfer-compound.step` contains two translated copies of the equal planar bevel, proving that
  grouped `2× C6` ink retains two physical occurrences.
- `chamfer-topology-a.step` applies 4 mm and 7 mm bevels to opposite edges in that order;
  `chamfer-topology-b.step` applies the same operations in reverse order. Their geometry and
  authored requirements are identical while their STEP hashes differ.

`corpus-fillets-v1.json` is a separate physical rounded-edge corpus. One cylindrical or toroidal
round is one radius-callout requirement even when equal radii share ink:

- `fillet-plain.step` is an unmodified 60 × 40 × 30 mm block and has no round requirement.
- `fillet-planar-z.step` applies a 4 mm round to its +X/+Y edge; `fillet-planar-x.step` and
  `fillet-planar-y.step` rigidly rotate that construction onto the other principal edge axes.
- `fillet-repeated.step` rounds all four Z-running edges to 5 mm, proving grouped `4× R5` ink
  retains four physical occurrences on one body.
- `fillet-turned.step` applies a 3 mm toroidal round to the +Z end of diameter 30 × 60 mm shaft
  stock.
- `fillet-overlap.step` is a quarter-cylindrical blind corner cut. Direct fillet discovery sees
  its curved wall, while the aggregate assigns that physical surface only to CircularBlindStep.
- `fillet-compound.step` contains two translated copies of the equal planar round, proving that
  grouped `2× R4` ink retains two physical occurrences on separate bodies.
- `fillet-topology-a.step` applies 4 mm and 7 mm rounds to opposite edges in that order;
  `fillet-topology-b.step` applies the same operations in reverse order. Their geometry and
  authored requirements are identical while their STEP hashes differ.

The `FILE_NAME` timestamp is normalized. Each corpus manifest pins every fixture SHA-256 and records
case-level provenance. A changed fixture therefore requires an explicit corpus-version decision;
regenerating recognition output can never rewrite the expected facts silently.
