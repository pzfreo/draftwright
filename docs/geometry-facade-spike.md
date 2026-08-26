# Geometry facade spike: Draftwright finding

Draftwright's selected F7 workflow was the existing `fillet(face)` declaration. The spike replaces
Draftwright's direct `BRepAdaptor_Surface` read and recogniser-specific `fillet_anchor` call with
`b123d_recognisers.experimental_geometry`.

The result is semantically successful: the same principal axis, radius and on-round anchor are
produced; planar faces refuse with the existing user-facing error; existing declared/detected
fillet and drawing tests pass; and Draftwright imports no underscore-private recogniser module.

The architectural result is a deliberate **no-go on publishing the graph yet**. This workflow
needs a closed analytic fact for one face and an anchor on that face. It does not need adjacency,
smooth regions, blend selection or graph-owned traversal. A one-face `GeometryGraph` costs only
about 0.2 ms, but its conceptual surface is still too large.

The next Draftwright integration should therefore consume a smaller graph-independent
`inspect_face(face)` projection. `GeometryGraph` should be published only after another concrete
Draftwright workflow independently requires adjacency or selected blend provenance. Correspondence,
snapshots, body matching, registries and recognition ownership remain out of scope.

Executable evidence lives in `tests/test_geometry_graph_spike.py`; the package-side design,
benchmarks and complete recommendation live in `docs/f7-geometry-facade-spike.md` in the paired
`b123d-recognisers` spike checkout.
