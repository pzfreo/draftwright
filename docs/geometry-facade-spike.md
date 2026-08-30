# Geometry facade spike: Draftwright finding

Draftwright's selected F7 workflow was the existing `fillet(face)` declaration. The completed
spike replaced Draftwright's direct `BRepAdaptor_Surface` read and recogniser-specific
`fillet_anchor` call with graph-independent face inspection. That experiment graduated into
`b123d_recognisers.inspection` in recognisers 0.4.4; Draftwright adopts its completed format-1
contract from the immutable 0.4.6 wheel (#1362).

The result is semantically successful: the same principal axis, radius and on-round anchor are
produced; planar faces refuse with the existing user-facing error; existing declared/detected
fillet and drawing tests pass; and Draftwright imports no experimental, family-specific, or
underscore-private recogniser module for declared geometry reads.

The architectural result is a deliberate **no-go on publishing the graph yet**, and a **go on API
review for face inspection**. The published inspection surface now also owns the four other
declared-feature reads (Double-D bore, countersink, chamfer, and groove). No graph, run-local
reference, adjacency, smooth region or blend-selection type crosses the package boundary. The
fillet result contains the closed surface fact and an optional on-face anchor.

`GeometryGraph` should be published only after another concrete out-of-tree workflow independently
requires adjacency or selected blend provenance. Correspondence, snapshots, body matching,
registries and recognition ownership remain out of scope.

Executable evidence lives in `tests/test_geometry_graph_spike.py` and
`tests/test_inspection_contract.py`; the package-side design,
benchmarks and complete recommendation live in `docs/f7-geometry-facade-spike.md` in the paired
`b123d-recognisers` spike checkout.
