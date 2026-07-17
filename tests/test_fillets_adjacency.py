"""#602: recognise_fillets finds neighbour faces via an edge→faces map, not a pairwise sweep.

The old neighbour search tested every candidate face against every other face with an
``any(a.IsSame(b) …)`` over both faces' edge lists — O(faces² × edges²). On NIST CTC-02
that was 3.7 million ``TopoDS.IsSame`` calls, ~6 s of the detector's 10.8 s. The
adjacency map (keyed by build123d ``Edge``, whose hash/equality are exactly ``IsSame``
semantics) leaves only the per-neighbour self-skip and hash-bucket comparisons.

Guard: run the detector under cProfile (which counts pybind builtins) and bound the
IsSame call count. On this 13-face part the pairwise sweep makes 752 calls; the map
form makes 78 — the bound sits between with margin on both sides.
"""

from __future__ import annotations

import cProfile
import pstats

from build123d import Axis, Box, Cylinder, Pos, fillet

from draftwright.recognition import recognise_fillets


def _filleted_plate():
    plate = Box(90, 60, 20)
    plate = fillet(plate.edges().filter_by(Axis.Z), 6)
    for i in range(3):
        plate -= Pos(-25 + i * 25, 0, 0) * Cylinder(4, 20)
    return plate


def test_fillet_neighbour_search_is_not_quadratic():
    part = _filleted_plate()
    prof = cProfile.Profile()
    prof.enable()
    found = recognise_fillets(part)
    prof.disable()

    assert len(found) == 4  # the four rounded corners — the guard has a real subject

    # pstats keys builtins as ('~', 0, '<built-in method OCP.OCP.TopoDS.IsSame>');
    # values are (cc, nc, tt, ct, callers) tuples.
    is_same_calls = sum(
        stat[0]
        for func, stat in pstats.Stats(prof).stats.items()  # type: ignore[attr-defined]
        if "IsSame" in func[2]
    )
    assert 0 < is_same_calls <= 300, (
        f"{is_same_calls} TopoDS.IsSame calls for a 13-face part (map form: ~78, "
        f"pairwise sweep: ~752) — the fillet neighbour search regressed to the "
        f"O(faces² × edges²) sweep (or IsSame stopped being counted: 0)"
    )
