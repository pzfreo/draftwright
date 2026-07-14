"""score — feature-completeness metric (#148f / #608).

A **measurement tool**, not a recogniser and not part of the drawing engine: it quantifies how
completely the recognition suite (:mod:`draftwright.recognition`) captures a part's machined
features, so the coverage broadening across the #148 epic (148a–e) is measurable. All-three-
surfaces (ADR 0011) is N/A — this produces no drawn feature.

The metric is a **census**: the number of features each recogniser finds, per kind. Progress
across the epic shows up directly — the census gains kinds and counts as each child lands (a
slotted bar reports ``slot: 1`` only once #135/#148 recognises it; a grooved shaft gains
``groove: 1`` only with #606). Sum the census over a corpus of representative parts to track
the epic; a single part's census reads off exactly what was recognised.

*Why census-only, no completeness ratio.* A ratio needs an independent denominator, and there
isn't a good one. The obvious substrate — :func:`~draftwright.recognition.feature_diameters` —
is itself built from ``recognise_holes`` / ``recognise_bosses``, so diffing recognised diameters
against it is **tautological**: both sides move together, the ratio is 1.0 for every real part,
and a genuine recogniser regression drops the denominator too (so it never signals). The only
independent substrate, the raw ``analyse_cylinders`` patch list, is **noisy** — radiused slot
ends and other non-feature partial cylinders are never features, so legitimate parts would score
below 1.0 permanently (exactly the #158 reason ``feature_diameters`` avoids that substrate). A
census is the one honest signal, so that is all this reports.

Bottom of the DAG beside the recognisers: depends only on :mod:`draftwright.recognition` +
build123d, and nothing in the engine imports it.
"""

from __future__ import annotations

from draftwright.recognition import (
    recognise_bosses,
    recognise_chamfers,
    recognise_countersinks,
    recognise_fillets,
    recognise_flats,
    recognise_grooves,
    recognise_hole_patterns,
    recognise_holes,
    recognise_plates,
    recognise_pockets,
    recognise_slots,
    recognise_turned_steps,
)


def feature_census(part) -> dict[str, int]:
    """The count of recognised features per kind for *part* (see module docs).

    Runs every feature recogniser once — injecting hole patterns from the recognised holes (the
    one ADR 0013 dependency) — and returns ``{kind: count}`` with a stable, complete set of
    keys (a kind absent from the part reports ``0``, not a missing key). The prismatic
    *substrate* recognisers (face levels, step shoulders — #555) are excluded: they feed other
    recognisers rather than being distinct machined features, and their level-derivation belongs
    to the model layer, not a metric."""
    holes = recognise_holes(part)
    records = {
        "hole": holes,
        "hole_pattern": recognise_hole_patterns(holes),
        "boss": recognise_bosses(part),
        "step": recognise_turned_steps(part),
        "groove": recognise_grooves(part),
        "flat": recognise_flats(part),
        "slot": recognise_slots(part),
        "pocket": recognise_pockets(part),
        "chamfer": recognise_chamfers(part),
        "fillet": recognise_fillets(part),
        "countersink": recognise_countersinks(part),
        "plate": recognise_plates(part),
    }
    return {kind: len(recs) for kind, recs in records.items()}
