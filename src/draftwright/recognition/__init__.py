"""Feature recognition for build123d solids (ADR 0007).

draftwright owns feature recognition; ``build123d-drafting-helpers`` is the
rendering library. This package is the single home for it:

- :mod:`._features` — vendored hole/boss/cylinder/pattern recognisers (was
  ``build123d_drafting.features``; upstream copy frozen and deprecated).
- :mod:`.slots` — the draftwright-local milled-slot recogniser (#135).

Import the public surface from here, not the submodules.

Recogniser contract (ADR 0013; #568)
------------------------------------
Every *feature* recogniser conforms to one shape::

    recognise_<feature>(part, *, <injected inventory>) -> list[<frozen-dataclass record>]

- **British verb** ``recognise_`` (not ``find_``/``analyse_``); codespell pins spelling.
- **Keyword-only** args after ``part`` — tuning *and* injected shared inventory
  (``recognise_hole_patterns(part, *, holes)``, ``recognise_step_shoulders(part, *,
  levels)``). A recogniser **never re-recognises a dependency internally**; the caller
  (``detect.py`` / ``analysis.py``) owns the single inventory and threads it (one
  inventory, ADR 0008 Am5).
- Returns a **deterministic ``list`` of frozen-dataclass records** (never
  ``Optional``-singular, never a bare untyped ``list``); empty when the feature is absent.
- **Geometry-only records** — no build123d types leak out; the records are the future
  ``b123d-recognisers`` surface (ADR 0013, Phase 2). ``detect.py`` adapts a record into
  the dimensioning IR (no recognition object crosses that boundary — ADR 0008 Am6).

``analyse_cylinders`` / ``full_cylinders`` / ``feature_diameters`` are cylinder-analysis
*substrate* (a tuple of dicts / a diameter query), not ``list[record]`` feature
recognisers, and deliberately keep their names. Two recognisers still return a
non-conforming shape pending the #568 retypes: ``recognise_turned_steps``
(``TurnedProfile | None`` — a single profile) and ``recognise_face_levels``
(``list[float]``).
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
    full_cylinders,
    recognise_bosses,
    recognise_hole_patterns,
    recognise_holes,
)
from draftwright.recognition.chamfers import Chamfer, recognise_chamfers
from draftwright.recognition.levels import (
    StepShoulder,
    recognise_face_levels,
    recognise_step_shoulders,
)
from draftwright.recognition.plates import Plate, recognise_plates
from draftwright.recognition.slots import Slot, recognise_slots
from draftwright.recognition.turned import TurnedProfile, TurnedStep, recognise_turned_steps

__all__ = [
    "BoltCircle",
    "Chamfer",
    "BossFeature",
    "CounterBore",
    "HoleFeature",
    "HoleSpec",
    "LinearArray",
    "Plate",
    "RectGrid",
    "Slot",
    "StepShoulder",
    "TurnedProfile",
    "TurnedStep",
    "analyse_cylinders",
    "recognise_face_levels",
    "recognise_step_shoulders",
    "feature_diameters",
    "recognise_bosses",
    "recognise_chamfers",
    "recognise_hole_patterns",
    "recognise_holes",
    "recognise_plates",
    "recognise_slots",
    "recognise_turned_steps",
    "full_cylinders",
]
