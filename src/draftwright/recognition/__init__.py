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
from draftwright.recognition.levels import analyse_face_levels, find_step_shoulders
from draftwright.recognition.plates import Plate, find_plates
from draftwright.recognition.slots import Slot, find_slots
from draftwright.recognition.turned import TurnedProfile, TurnedStep, find_turned_steps

__all__ = [
    "BoltCircle",
    "BossFeature",
    "CounterBore",
    "HoleFeature",
    "HoleSpec",
    "LinearArray",
    "Plate",
    "RectGrid",
    "Slot",
    "TurnedProfile",
    "TurnedStep",
    "analyse_cylinders",
    "analyse_face_levels",
    "find_step_shoulders",
    "feature_diameters",
    "find_bosses",
    "find_hole_patterns",
    "find_holes",
    "find_plates",
    "find_slots",
    "find_turned_steps",
    "full_cylinders",
]
