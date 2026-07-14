"""score — feature-completeness metric (#148f / #608).

A **measurement tool**, not a recogniser and not part of the drawing engine: it quantifies how
completely the recognition suite (:mod:`draftwright.recognition`) captures a part's machined
features, so the coverage broadening across the #148 epic (148a–e) is measurable and guarded
against regression. All-three-surfaces (ADR 0011) is N/A — this produces no drawn feature.

Two outputs, matching how much *substrate* each feature family has:

* **census** — the number of features each recogniser finds, per kind. This is the primary
  coverage-progress signal: it is the *only* honest measure for the planar families (slots /
  pockets / flats), which have no geometric substrate *below* the recogniser — the recogniser
  is the only detector, so there is nothing independent to diff against. Progress across the
  epic shows up as a census that gains kinds and counts as each child lands (a slotted bar
  reports ``slot: 1`` only once #135/#148 recognises it; a grooved shaft gains ``groove: 1``
  only with #606).

* **completeness** — a rigorous *inventory-and-diff* ratio on the **cylinder** family, reusing
  the :func:`~draftwright.linting.coverage.lint_feature_coverage` idea (#80). The cheap
  substrate :func:`~draftwright.recognition.feature_diameters` lists every distinct feature
  diameter the geometry carries (bores, ODs, turned-step and groove floors); the ratio is the
  fraction of those a recognised cylindrical feature (hole / boss / step / groove) accounts for.
  It is a **regression guard**, not a per-kind progress bar: a diameter is usually reachable by
  more than one recogniser (a groove floor is also a turned step, so ``recognise_grooves`` adds
  the groove *semantics* in the census without moving this ratio). The ratio drops below 1.0
  only when *no* cylindrical recogniser accounts for a diameter — i.e. a genuine recognition
  hole or a regression in the base hole/boss/step recognisers. Purely prismatic parts have no
  feature diameter and score 1.0 vacuously; their coverage lives entirely in the census.

Bottom of the DAG beside the recognisers: depends only on :mod:`draftwright.recognition` +
build123d, and nothing in the engine imports it.
"""

from __future__ import annotations

from dataclasses import dataclass

from draftwright.recognition import (
    feature_diameters,
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

# Two diameters are the "same" feature within this (mm) — matches lint_feature_coverage's
# _RECON_DIA_TOL so the completeness diff agrees with the coverage lint.
_DIA_TOL = 0.2

# The cylinder-family kinds whose records carry a ``diameter`` that a feature_diameters signal
# can be matched against (the numerator of the completeness ratio).
_DIAMETER_KINDS = ("hole", "boss", "step", "groove")


@dataclass(frozen=True)
class FeatureScore:
    """The feature-completeness measurement for one part.

    census:       recognised feature count per kind (every feature recogniser).
    total:        sum of ``census`` — total features recognised.
    diameters:    the part's distinct feature diameters (the cylinder substrate; the ratio's
                  denominator), sorted.
    covered:      the subset of ``diameters`` a recognised cylindrical feature accounts for.
    completeness: ``len(covered) / len(diameters)`` in [0, 1]; 1.0 when the part has no feature
                  diameter (a purely prismatic part — nothing on the cylinder side to miss).
    """

    census: dict[str, int]
    total: int
    diameters: tuple[float, ...]
    covered: tuple[float, ...]
    completeness: float


def _recognise_all(part) -> dict[str, list]:
    """Run every feature recogniser once (injecting patterns from the recognised holes — the
    one ADR 0013 dep) and return the records per kind. Detection is not shared with the model
    build; this is a standalone tool. The prismatic *substrate* recognisers (face levels, step
    shoulders — #555) are excluded: they feed other recognisers rather than being distinct
    machined features, and their level-derivation belongs to the model layer, not a metric."""
    holes = recognise_holes(part)
    return {
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


def feature_census(part) -> dict[str, int]:
    """The count of recognised features per kind for *part* (every feature recogniser)."""
    return {kind: len(records) for kind, records in _recognise_all(part).items()}


def feature_completeness(part) -> FeatureScore:
    """Score how completely the recognition suite captures *part*'s features (see module docs).

    Returns a :class:`FeatureScore`: the per-kind census plus a cylinder-family completeness
    ratio (the fraction of the part's feature diameters a recognised feature accounts for)."""
    records = _recognise_all(part)
    census = {kind: len(recs) for kind, recs in records.items()}
    diameters = tuple(sorted(feature_diameters(part)))
    recognised = [
        f.diameter for kind in _DIAMETER_KINDS for f in records[kind] if hasattr(f, "diameter")
    ]
    covered = tuple(d for d in diameters if any(abs(d - r) <= _DIA_TOL for r in recognised))
    completeness = len(covered) / len(diameters) if diameters else 1.0
    return FeatureScore(
        census=census,
        total=sum(census.values()),
        diameters=diameters,
        covered=covered,
        completeness=completeness,
    )
