# Geometry facade spike: Draftwright finding

Draftwright's selected F7 workflow was the existing `fillet(face)` declaration. The completed
spike replaces Draftwright's direct `BRepAdaptor_Surface` read and recogniser-specific
`fillet_anchor` call with the graph-independent
`b123d_recognisers.experimental_geometry.inspect_face`.

The result is semantically successful: the same principal axis, radius and on-round anchor are
produced; planar faces refuse with the existing user-facing error; existing declared/detected
fillet and drawing tests pass; and Draftwright imports no underscore-private recogniser module.

The architectural result is a deliberate **no-go on publishing the graph yet**, and a **go on API
review for face inspection**. Draftwright imports exactly `AnalyticSurface` and `inspect_face`; no
graph, run-local reference, adjacency, smooth region or blend-selection type crosses the package
boundary. The result contains the closed surface fact and an optional on-face anchor.

`GeometryGraph` should be published only after another concrete out-of-tree workflow independently
requires adjacency or selected blend provenance. Correspondence, snapshots, body matching,
registries and recognition ownership remain out of scope.

Executable evidence lives in `tests/test_geometry_graph_spike.py`; the package-side design,
benchmarks and complete recommendation live in `docs/f7-geometry-facade-spike.md` in the paired
`b123d-recognisers` spike checkout.
