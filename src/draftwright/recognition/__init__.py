"""Feature recognition for build123d solids (ADR 0007).

draftwright owns feature recognition; ``build123d-drafting-helpers`` is the
rendering library. This package is the single home for it:

- :mod:`._features` — vendored hole/boss/cylinder/pattern recognisers (was
  ``build123d_drafting.features``; upstream copy frozen and deprecated).
- :mod:`.slots` — the draftwright-local milled-slot recogniser (#135).

Import the public surface from here, not the submodules.

Recogniser contract (ADR 0013; #568)
------------------------------------
A *feature* recogniser takes one of two shapes:

- **Part-based** — ``recognise_<feature>(part, *, <tuning / injected deps>) -> list[record]``
  (``recognise_holes(part, *, cyls=None)``, ``recognise_chamfers(part, *, tol=...)``,
  ``recognise_step_shoulders(part, *, levels)``). Everything after ``part`` is
  **keyword-only** — both tuning and any injected inventory. A recogniser **never
  re-recognises a dependency internally**; the caller (``detect.py`` / ``analysis.py``)
  owns the single inventory and threads it (one inventory, ADR 0008 Am5).
- **Derived** — ``recognise_<feature>(inventory) -> list[record]`` (``recognise_hole_patterns(holes)``):
  operates purely on another recogniser's records, no ``part`` and no tuning, so the
  single inventory arg is unambiguous and stays positional.

Common to both: a **British** ``recognise_`` verb (not ``find_``/``analyse_``); a
**deterministic ``list`` of frozen-dataclass records**; **geometry-only records** (no
build123d types leak out — they are the future ``b123d-recognisers`` surface, ADR 0013
Phase 2, and ``detect.py`` adapts a record into the dimensioning IR with no recognition
object crossing that boundary, ADR 0008 Am6).

**State of this contract (this is the naming + signature step, #568 step 0).** Naming and
the keyword-only signatures hold for *every* recogniser now. What does **not** yet hold,
tracked by #568:

- **Return shape** — ``recognise_turned_steps`` returns ``TurnedProfile | None`` (genuinely
  a single 0-or-1 profile — whether it is even a ``list[record]`` recogniser is an open
  design call) and ``recognise_face_levels`` returns ``list[float]`` (not yet a record).
- **Record idiom** — the vendored ``_features.py`` records (``HoleFeature``/``BossFeature``/…)
  predate the ``…Record`` naming and clash by name with the IR ``Feature`` types.

``analyse_cylinders`` / ``full_cylinders`` / ``feature_diameters`` are **not** recognisers
under this contract — they are cylinder-analysis *substrate* (a tuple of dicts / a diameter
query), and deliberately keep their names.
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
