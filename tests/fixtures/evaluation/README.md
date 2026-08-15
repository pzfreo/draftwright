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

The `FILE_NAME` timestamp is normalized. `corpus-v1.json` pins every fixture SHA-256 and records
case-level provenance. A changed fixture therefore requires an explicit corpus-version decision;
regenerating recognition output can never rewrite the expected facts silently.
