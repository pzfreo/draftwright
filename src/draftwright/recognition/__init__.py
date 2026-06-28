"""Feature recognition for build123d solids (ADR 0007).

draftwright owns feature recognition; ``build123d-drafting-helpers`` is the
rendering library. This package is the single home for it:

- :mod:`._features` — vendored hole/boss/cylinder/pattern recognisers (was
  ``build123d_drafting.features``; upstream copy frozen and deprecated).
- :mod:`.slots` — the draftwright-local milled-slot recogniser (#135).

Import the public surface from here, not the submodules.
"""

from __future__ import annotations

from draftwright.recognition._features import (
    BoltCircle,
    BossFeature,
    CounterBore,
    HoleFeature,
    HoleSpec,
    LinearArray,
    RectGrid,
    analyse_cylinders,
    feature_diameters,
    find_bosses,
    find_hole_patterns,
    find_holes,
    full_cylinders,
)
from draftwright.recognition.slots import Slot, find_slots

__all__ = [
    "BoltCircle",
    "BossFeature",
    "CounterBore",
    "HoleFeature",
    "HoleSpec",
    "LinearArray",
    "RectGrid",
    "Slot",
    "analyse_cylinders",
    "feature_diameters",
    "find_bosses",
    "find_hole_patterns",
    "find_holes",
    "find_slots",
    "full_cylinders",
]
