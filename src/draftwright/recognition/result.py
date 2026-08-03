"""Aggregate recognition result (ADR 0017).

This is the orchestration boundary above the ADR 0013 recognisers.  It starts with the
inventories already shared by analysis and model detection; later ADR 0017 slices add the
remaining families, reconciliation, measurables and diagnostics without changing the
individual recogniser contract.
"""

from __future__ import annotations

from dataclasses import dataclass

from draftwright.recognition._features import (
    analyse_cylinders,
    recognise_bosses,
    recognise_hole_patterns,
    recognise_holes,
)
from draftwright.recognition.countersinks import recognise_countersinks
from draftwright.recognition.pads import recognise_rectangular_pads
from draftwright.recognition.slots import (
    recognise_pocket_patterns,
    recognise_pockets,
    recognise_slots,
)
from draftwright.recognition.turned import recognise_turned_steps


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
    pockets: tuple
    pocket_patterns: tuple
    pads: tuple
    turned_steps: tuple


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
    return RecognitionResult(
        cylinders=(tuple(z_cyls), tuple(cross_cyls)),
        countersinks=tuple(countersinks),
        holes=tuple(holes),
        hole_patterns=tuple(recognise_hole_patterns(holes)),
        bosses=tuple(recognise_bosses(part, cyls=cyls)),
        slots=tuple(recognise_slots(part)),
        pockets=tuple(pockets),
        pocket_patterns=tuple(recognise_pocket_patterns(pockets)),
        pads=tuple(recognise_rectangular_pads(part)),
        turned_steps=tuple(recognise_turned_steps(part, cyls=cyls)),
    )
