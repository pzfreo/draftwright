"""Aggregate recognition result (ADR 0017).

This is the orchestration boundary above the ADR 0013 recognisers.  It starts with the
inventories already shared by analysis and model detection; later ADR 0017 slices add the
remaining families, reconciliation, measurables and diagnostics without changing the
individual recogniser contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from draftwright.recognition._features import (
    analyse_cylinders,
    recognise_bosses,
    recognise_hole_patterns,
    recognise_holes,
)
from draftwright.recognition.countersinks import recognise_countersinks
from draftwright.recognition.flats import recognise_flats
from draftwright.recognition.grooves import recognise_grooves
from draftwright.recognition.levels import step_level_zs
from draftwright.recognition.pads import recognise_rectangular_pads
from draftwright.recognition.slots import (
    recognise_pocket_patterns,
    recognise_pockets,
    recognise_slot_patterns,
    recognise_slots,
)
from draftwright.recognition.turned import recognise_turned_steps

#: The families this aggregate runs, exactly once, per orchestration.
MIGRATED: frozenset[str] = frozenset(
    {
        "recognise_bosses",
        "recognise_countersinks",
        "recognise_flats",
        "recognise_grooves",
        # Reached through `step_level_zs`, the area-filtered gate over it — which is the form
        # both consumers want, so the aggregate stores that rather than the raw levels. It was
        # deferred NO_INDEPENDENT_CONSUMER until #1022 gave it one: critique on the declared
        # path needs the geometry ladder, and rescanning it per lint is what the deferral's
        # reason had stopped covering.
        "recognise_face_levels",
        "recognise_hole_patterns",
        "recognise_holes",
        "recognise_pocket_patterns",
        "recognise_pockets",
        "recognise_rectangular_pads",
        "recognise_slot_patterns",
        "recognise_slots",
        "recognise_turned_steps",
    }
)


class Deferral(Enum):
    """Why a family is not in :data:`MIGRATED` — a code, not a paragraph.

    The reasoning belongs in the issue that removes the constraint.  A constant CI reads
    goes stale silently, and this one already had: it carried a "governing principle" the
    list below did not satisfy, and cold timings for a migration that review reverted.

    ``CLASSIFICATION_GATED`` — ``build_part_model`` runs it only for one part class, so
    hoisting it unconditionally scans the other class for a result that is discarded.  Note
    this constraint is on the AUTOMATIC path: #1022 removes recognition from the declared
    path and leaves it entirely untouched.  It ends when the orchestration itself carries
    the classification (#1028).

    ``BUILD_MODEL_ONLY`` — its sole engine consumer is ``build_part_model``, which the ADR
    0011 declared path skips.  The aggregate runs unconditionally, so migrating it removes
    no scan from the detected path and adds one to the declared path.  (``score.py`` calls
    some of these too, but it is a standalone measurement tool off both build paths, and it
    calls the recogniser directly whatever the manifest says.)

    ``CALLER_SPECIFIC_INPUT`` — an input other than the part decides the answer and the
    callers pass different ones, so there is no single per-build value for a frozen
    aggregate to hold.

    ``NO_INDEPENDENT_CONSUMER`` — reached only through one shared helper, so there is
    nothing to cache for.  Unlike the others, not scheduled to change.
    """

    CLASSIFICATION_GATED = "classification-gated"
    BUILD_MODEL_ONLY = "build-part-model-only"
    CALLER_SPECIFIC_INPUT = "caller-specific-input"
    NO_INDEPENDENT_CONSUMER = "no-independent-consumer"


@dataclass(frozen=True)
class Deferred:
    """A family the aggregate does not own, and the constraint that stops it.

    ``blocker`` is the issue that removes the constraint, or ``None`` when the deferral is
    not scheduled to end.  A deferral without either is "not got to it yet", which is not a
    reason.
    """

    reason: Deferral
    blocker: int | None = None


#: The families the aggregate does NOT own, each with its constraint.
#:
#: ``BUILD_MODEL_ONLY`` is gone as a live reason (#1026): its three families cost the
#: declared path nothing now that #1022 stops that path recognising at all, so ADR 0017
#: completeness was reason enough on its own.  The enum member survives because a future
#: family can be deferred for that reason again; what does not survive is a *deferral*
#: justified by a cost that no longer exists.
#:
#: What is left binds on the AUTOMATIC path, which #1022 did not touch: the classification
#: gate (#1028) and the one recogniser whose answer depends on which caller is asking
#: (#1025).
DEFERRED: dict[str, Deferred] = {
    "recognise_chamfers": Deferred(Deferral.CLASSIFICATION_GATED, blocker=1028),
    "recognise_fillets": Deferred(Deferral.CLASSIFICATION_GATED, blocker=1028),
    "recognise_plates": Deferred(Deferral.CLASSIFICATION_GATED, blocker=1028),
    "recognise_step_shoulders": Deferred(Deferral.CALLER_SPECIFIC_INPUT, blocker=1025),
}


@dataclass(frozen=True)
class RecognitionResult:
    """The immutable feature inventory produced by one recognition orchestration run.

    The aggregate is intentionally incomplete in its first migration slice: these are the
    inventories that ``analysis`` already detected and injected into ``build_part_model``.
    Keeping that boundary explicit lets subsequent families move here monotonically instead
    of introducing a second, big-bang detector.
    """

    cylinders: tuple[tuple, tuple]
    countersinks: tuple
    holes: tuple
    hole_patterns: tuple
    bosses: tuple
    slots: tuple
    slot_patterns: tuple
    grooves: tuple
    flats: tuple
    pockets: tuple
    pocket_patterns: tuple
    pads: tuple
    turned_steps: tuple
    #: The interior prismatic step Z-levels (:func:`step_level_zs` — ``recognise_face_levels``
    #: behind its area filter). A float tuple rather than records because both consumers want
    #: the gated levels, not the faces: sizing converges page/scale on them, and critique feeds
    #: them to ``recognise_step_shoulders`` as the geometry's own ladder (#1022).
    step_levels: tuple[float, ...]


def build_recognition_result(part, *, cylinders=None) -> RecognitionResult:
    """Run the initial shared recognition inventory exactly once for *part*.

    Dependencies are computed by this orchestration layer and injected downstream: holes
    reuse both the cylinder substrate and countersinks, while patterns reuse their accepted
    member records.  No recogniser rediscovers one of those dependencies internally.
    """

    z_cyls, cross_cyls = cylinders if cylinders is not None else analyse_cylinders(part)
    cyls = (z_cyls, cross_cyls)
    countersinks = recognise_countersinks(part)
    holes = recognise_holes(part, cyls=cyls, csinks=countersinks)
    pockets = recognise_pockets(part)
    slots = recognise_slots(part)
    return RecognitionResult(
        cylinders=(tuple(z_cyls), tuple(cross_cyls)),
        countersinks=tuple(countersinks),
        holes=tuple(holes),
        hole_patterns=tuple(recognise_hole_patterns(holes)),
        bosses=tuple(recognise_bosses(part, cyls=cyls)),
        slots=tuple(slots),
        # Derived from the accepted members, like the other two pattern families — the
        # recogniser must not rediscover the slots it groups.
        slot_patterns=tuple(recognise_slot_patterns(slots)),
        grooves=tuple(recognise_grooves(part, cyls=cyls)),
        flats=tuple(recognise_flats(part, cyls=cyls)),
        pockets=tuple(pockets),
        pocket_patterns=tuple(recognise_pocket_patterns(pockets)),
        pads=tuple(recognise_rectangular_pads(part)),
        turned_steps=tuple(recognise_turned_steps(part, cyls=cyls)),
        step_levels=tuple(step_level_zs(part)),
    )
