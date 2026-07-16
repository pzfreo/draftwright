"""Shared test helper: is the installed build123d ≥ 0.11?

build123d 0.11 (the cadquery-ocp-novtk kernel, required for Python 3.13+ wheels) shifts the
HLR projection very slightly for some geometries.

``test_layout_snapshot`` — a TEMPORARY, byte-exact ADR 0009 characterization gate marked
"delete at P5 (#319)" — is **retired** (#641 gap 3). It skipped exactly one of its ten cases on
0.11 (``box``); the other nine ran. Retiring it deliberately drops its *absolute*-position
coverage (expected label/geometry positions, view bboxes, item count) — that was always
temporary characterization for the strip-layout refactor, not a permanent guard. What remains,
on every kernel with no skip, is the RELATIONAL coverage (``test_layout_cleanliness`` archetypes,
``test_layout_property`` seeded fuzz, ``test_layout_hypothesis`` adversarial fuzz): collision-free,
in-bounds, deterministic — NOT absolute positions. Permanent cross-kernel absolute-placement
coverage, if wanted, is a separate, kernel-safe gate — not this one.

The few remaining SKIP_011 markers guard **behavioural** tests where 0.11's projection genuinely
differs — an extreme oversized part whose iso overflows at minimum scale, a CTC-01 iso that no
longer fits at sheet scale, and the CTC-01 iso world→page mapping (its centroid lands on the iso
bbox edge under 0.11's foreshortening). Real kernel differences, tracked by #665 — not papered
over by relaxing the assertions.
"""

import build123d


def _minor(v: str) -> tuple[int, int]:
    parts = [int(p) for p in v.split(".")[:2] if p.isdigit()]
    return (parts + [0, 0])[0], (parts + [0, 0])[1]


B123D_GE_011 = _minor(build123d.__version__) >= (0, 11)
SKIP_011 = "build123d 0.11 tips a scale/layout threshold; genuine kernel difference (#665)"
