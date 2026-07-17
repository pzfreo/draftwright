"""#602: recognise_fillets finds neighbour faces via an edge→faces map, not a pairwise sweep.

The old neighbour search tested every candidate face against every other face with an
``any(a.IsSame(b) …)`` over both faces' edge lists — O(faces² × edges²). On NIST CTC-02
that was 3.7 million ``TopoDS.IsSame`` calls, ~6 s of the detector's 10.8 s. The
adjacency map is keyed by build123d ``Edge``, whose hash/equality are exactly ``IsSame``
semantics (same TShape + Location, orientation-insensitive).

Guard: count ``Shape.__eq__`` — a *Python-level* method the adjacency dict exercises on
every shared-edge insertion and lookup, and which the raw wrapped-``IsSame`` sweep never
called at all. (A first cut counted pybind ``IsSame`` via cProfile; profiler visibility
of OCP builtins turned out to vary by Python version/wheel — 0 on the 3.10/3.12/3.13 CI
lanes — so the metric moved to a boundary no platform can hide.) On this 13-face part:
map form = 46 calls, pairwise sweep = 0, a from-scratch quadratic ``==`` sweep = many
thousands — the two-sided bound catches both regressions.
"""

from __future__ import annotations

import build123d.topology.shape_core as shape_core
from build123d import Axis, Box, Cylinder, Pos, fillet

from draftwright.recognition import recognise_fillets


def _filleted_plate():
    plate = Box(90, 60, 20)
    plate = fillet(plate.edges().filter_by(Axis.Z), 6)
    for i in range(3):
        plate -= Pos(-25 + i * 25, 0, 0) * Cylinder(4, 20)
    return plate


def test_fillet_neighbour_search_uses_the_adjacency_map(monkeypatch):
    part = _filleted_plate()

    calls = 0
    real_eq = shape_core.Shape.__eq__

    def counting_eq(self, other):
        nonlocal calls
        calls += 1
        return real_eq(self, other)

    monkeypatch.setattr(shape_core.Shape, "__eq__", counting_eq)
    found = recognise_fillets(part)

    assert len(found) == 4  # the four rounded corners — the guard has a real subject
    assert 0 < calls <= 500, (
        f"{calls} Shape.__eq__ calls for a 13-face part (adjacency map: ~46; the old "
        f"wrapped-IsSame pairwise sweep: 0; a quadratic == sweep: thousands) — the "
        f"fillet neighbour search stopped using the hashed edge→faces map"
    )
