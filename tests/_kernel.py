"""Shared test helper: is the installed build123d ≥ 0.11?

build123d 0.11 (the cadquery-ocp-novtk kernel, required for Python 3.13+ wheels) shifts the
HLR projection very slightly for some geometries. draftwright's *exact-position* snapshot
guards are pinned to 0.10's output; a handful are skipped on 0.11 so the byte-exact regression
gate keeps running on the 3.10–3.12 (build123d 0.10) matrix while 3.13/3.14 run everything
else. Every functional / feature-count / lint check passes on both kernels.
"""

import build123d


def _minor(v: str) -> tuple[int, int]:
    parts = [int(p) for p in v.split(".")[:2] if p.isdigit()]
    return (parts + [0, 0])[0], (parts + [0, 0])[1]


B123D_GE_011 = _minor(build123d.__version__) >= (0, 11)
SKIP_011 = "0.10-pinned exact-position snapshot; build123d 0.11 (Python 3.13+) shifts projection"
