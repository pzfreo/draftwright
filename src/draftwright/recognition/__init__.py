"""Feature recognition for build123d solids (ADR 0007).

draftwright owns feature recognition; ``build123d-drafting-helpers`` is the
rendering library. This package is the single home for it:

- :mod:`._features` — vendored hole/boss/cylinder/pattern recognisers (was
  ``build123d_drafting.features``; upstream copy frozen and deprecated).
- :mod:`.slots` — draftwright-local milled slot, pocket, and channel recognition.

Import the public surface from here, not the submodules.

Recogniser contract (ADR 0013; #568)
------------------------------------
A *feature* recogniser takes one of two shapes:

- **Part-based** — ``recognise_<feature>(part, *, <tuning / injected deps>) -> list[record]``
  (``recognise_holes(part, *, cyls=None)``, ``recognise_chamfers(part, *, tol=...)``,
  ``recognise_risers(part, *, tol=...)``). Everything after ``part`` is
  **keyword-only** — both tuning and any injected inventory. A recogniser **never
  re-recognises a dependency internally**; the caller (``detect.py`` / ``analysis.py``)
  owns the single inventory and threads it (one inventory, ADR 0008 Am5).
- **Derived** — ``recognise_<feature>(inventory) -> list[record]`` (``recognise_hole_patterns(holes)``):
  operates purely on another recogniser's records, no ``part`` and no tuning, so the
  single inventory arg is unambiguous and stays positional.

Common to both: a **British** ``recognise_`` verb (not ``find_``/``analyse_``); a
**deterministic ``list`` of frozen-dataclass records** (empty when absent — never
``Optional``-singular, never a bare ``list`` of primitives); **geometry-only records** (no
build123d types leak out — they are the future ``b123d-recognisers`` surface, ADR 0013
Phase 2, and ``detect.py`` adapts a record into the dimensioning IR with no recognition
object crossing that boundary, ADR 0008 Am6).

The contract holds for **every** recogniser, including the two that once strained it —
their records were simply the wrong shape (#568):

- ``recognise_face_levels -> list[FaceLevel]`` (was ``list[float]``) — a level is now a
  ``FaceLevel(z)`` record.
- ``recognise_turned_steps -> list[TurnedStep]`` (was ``TurnedProfile | None``) — each
  ``TurnedStep`` now carries its ``axis``, so it is a self-contained record and the old
  ``TurnedProfile`` wrapper is no longer the return. ``TurnedProfile`` survives only as a
  **pipeline aggregate** (``TurnedProfile.from_steps``) for consumers that want axis +
  shoulders as a unit — it is not a recogniser return.

Record class names avoid the IR ``Feature`` types: the vendored records are ``HoleRecord``
/ ``BossRecord`` (not ``HoleFeature`` / ``BossFeature`` — those are the IR types), keeping
``from draftwright.recognition import HoleRecord`` unambiguous against ``from
draftwright.model.ir import HoleFeature``.

``analyse_cylinders`` / ``full_cylinders`` / ``feature_diameters`` are **not** recognisers
under this contract — they are cylinder-analysis *substrate* (a tuple of dicts / a diameter
query), and deliberately keep their names. Likewise the **shared single-face reads**
(``classify_bevel``/``BevelReject``, ``fillet_anchor``, ``cone_rims``,
``floor_face_anchor``, ``step_level_zs``, #704): helpers shared with the declared
front-end, not recognisers — they traffic in build123d/OCP objects,
so a future ADR 0013 Phase-2 package extraction would keep them internal, not surface.

``project_step_shoulders`` is likewise not a recogniser but its mirror image: a **pure
projection** over :func:`recognise_risers`' records, with no ``part`` and no geometry access
at all (#1025). It carries the ``project_`` verb rather than ``recognise_`` precisely so the
manifest does not have to decide whether the aggregate owns it — it owns the evidence, and
each consumer projects. A function that cannot look at a solid cannot become a second
recognition site.
"""

from __future__ import annotations

from draftwright.recognition._features import (
    BoltCircle,
    BossRecord,
    CounterBore,
    HoleRecord,
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
from draftwright.recognition.chamfers import (
    BevelReject,
    Chamfer,
    classify_bevel,
    recognise_chamfers,
)
from draftwright.recognition.countersinks import CounterSink, cone_rims, recognise_countersinks
from draftwright.recognition.fillets import Fillet, fillet_anchor, recognise_fillets
from draftwright.recognition.flats import Flat, recognise_flats
from draftwright.recognition.grooves import Groove, floor_face_anchor, recognise_grooves
from draftwright.recognition.levels import (
    FaceLevel,
    RiserEvidence,
    StepShoulder,
    project_step_shoulders,
    recognise_face_levels,
    recognise_risers,
    step_level_records,
    step_level_zs,
)
from draftwright.recognition.pads import RaisedPad, recognise_rectangular_pads
from draftwright.recognition.plates import Plate, has_multi_axis_plates, recognise_plates
from draftwright.recognition.polygonal_bosses import (
    PolygonalBoss,
    PolygonalStock,
    recognise_polygonal_bosses,
    recognise_polygonal_stock,
)
from draftwright.recognition.profiled_bores import DoubleDBore, recognise_double_d_bores
from draftwright.recognition.repeating_profiles import (
    RepeatingRadialProfile,
    recognise_repeating_radial_profiles,
)
from draftwright.recognition.result import RecognitionResult, build_recognition_result
from draftwright.recognition.slots import (
    Channel,
    Pocket,
    PocketArray,
    PocketGrid,
    Slot,
    SlotArray,
    SlotGrid,
    recognise_channels,
    recognise_pocket_patterns,
    recognise_pockets,
    recognise_slot_patterns,
    recognise_slots,
)
from draftwright.recognition.turned import TurnedProfile, TurnedStep, recognise_turned_steps

__all__ = [
    "BoltCircle",
    "Chamfer",
    "Channel",
    "Fillet",
    "Flat",
    "Groove",
    "BossRecord",
    "CounterBore",
    "CounterSink",
    "DoubleDBore",
    "FaceLevel",
    "HoleRecord",
    "HoleSpec",
    "LinearArray",
    "Plate",
    "Pocket",
    "PocketArray",
    "PocketGrid",
    "PolygonalBoss",
    "PolygonalStock",
    "RaisedPad",
    "RepeatingRadialProfile",
    "RectGrid",
    "Slot",
    "SlotArray",
    "SlotGrid",
    "RiserEvidence",
    "StepShoulder",
    "TurnedProfile",
    "TurnedStep",
    "RecognitionResult",
    "BevelReject",
    "analyse_cylinders",
    "classify_bevel",
    "cone_rims",
    "fillet_anchor",
    "floor_face_anchor",
    "has_multi_axis_plates",
    "project_step_shoulders",
    "recognise_face_levels",
    "recognise_risers",
    "step_level_records",
    "step_level_zs",
    "feature_diameters",
    "recognise_bosses",
    "recognise_chamfers",
    "recognise_channels",
    "recognise_fillets",
    "recognise_flats",
    "recognise_grooves",
    "recognise_countersinks",
    "recognise_double_d_bores",
    "recognise_hole_patterns",
    "recognise_holes",
    "recognise_plates",
    "recognise_pocket_patterns",
    "recognise_pockets",
    "recognise_polygonal_bosses",
    "recognise_polygonal_stock",
    "recognise_rectangular_pads",
    "recognise_repeating_radial_profiles",
    "recognise_slot_patterns",
    "recognise_slots",
    "recognise_turned_steps",
    "build_recognition_result",
    "full_cylinders",
]
