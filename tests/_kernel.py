"""Shared test helper: is the installed build123d ≥ 0.11?

build123d 0.11 (the cadquery-ocp-novtk kernel, required for Python 3.13+ wheels) shifts the
HLR projection very slightly for some geometries.

The byte-exact placement gate that most SKIP_011 markers guarded (``test_layout_snapshot``) is
**retired** (#319/#641 gap 3): cross-kernel placement coverage now rests on the RELATIONAL
invariants (``test_layout_cleanliness`` archetypes, ``test_layout_property`` seeded fuzz,
``test_layout_hypothesis`` adversarial fuzz), which run on every kernel with no skip.

The few remaining SKIP_011 markers guard **behavioural threshold** tests (a specific scale
value, or the iso fitting at sheet scale vs. NTS) where 0.11's projection tips the assertion —
and at the extreme (a very oversized part) the 0.11 layout genuinely differs (the iso overflows
at minimum scale). That is a real kernel difference, not a characterization artifact — tracked
by #665, not something to paper over by relaxing the assertion.
"""

import build123d


def _minor(v: str) -> tuple[int, int]:
    parts = [int(p) for p in v.split(".")[:2] if p.isdigit()]
    return (parts + [0, 0])[0], (parts + [0, 0])[1]


B123D_GE_011 = _minor(build123d.__version__) >= (0, 11)
SKIP_011 = "build123d 0.11 tips a scale/layout threshold; genuine kernel difference (#665)"
