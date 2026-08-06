"""Aggregate recognition result (ADR 0017).

This is the orchestration boundary above the ADR 0013 recognisers. It owns every public
recognition family and the shared evidence consumers reuse. ADR 0017 Amendment 1 deliberately
does not make reconciliation, measurables, requirement identity, or diagnostics automatic
next additions: those extensions are evidence-gated by end-to-end completeness slices.
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
from draftwright.recognition.chamfers import recognise_chamfers
from draftwright.recognition.countersinks import recognise_countersinks
from draftwright.recognition.fillets import recognise_fillets
from draftwright.recognition.flats import recognise_flats
from draftwright.recognition.grooves import recognise_grooves
from draftwright.recognition.levels import FaceLevel, recognise_risers, step_level_records
from draftwright.recognition.pads import recognise_rectangular_pads
from draftwright.recognition.plates import recognise_plates
from draftwright.recognition.slots import (
    recognise_channels,
    recognise_pocket_patterns,
    recognise_pockets,
    recognise_slot_patterns,
    recognise_slots,
)
from draftwright.recognition.turned import TurnedProfile, recognise_turned_steps

#: The families this aggregate runs, exactly once, per orchestration.
MIGRATED: frozenset[str] = frozenset(
    {
        "recognise_bosses",
        "recognise_chamfers",
        "recognise_channels",
        "recognise_countersinks",
        "recognise_fillets",
        "recognise_flats",
        "recognise_grooves",
        # Reached through `step_level_records`, the area-filtered gate over it. The aggregate
        # retains those records now that #915 needs their support spans; `step_ladder()` gives
        # the float projection sizing and critique consume. It was
        # deferred NO_INDEPENDENT_CONSUMER until #1022 gave it one: critique on the declared
        # path needs the geometry ladder, and rescanning it per lint is what the deferral's
        # reason had stopped covering.
        "recognise_face_levels",
        "recognise_hole_patterns",
        "recognise_holes",
        "recognise_pocket_patterns",
        "recognise_plates",
        "recognise_pockets",
        "recognise_rectangular_pads",
        "recognise_risers",
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
    aggregate to hold.  Its last holder left in #1025, which showed the reason is usually a
    *shape* problem rather than a fact about the feature: the scan did not depend on the
    caller's input, only the filter did, so separating the two gave the aggregate something
    single-valued to own.  Prefer that split before reaching for this member again.

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
#: ``CALLER_SPECIFIC_INPUT`` is gone too (#1025): ``recognise_step_shoulders`` split into a
#: level-free scan the aggregate owns (``recognise_risers``) and a pure
#: ``project_step_shoulders`` each consumer applies with its own level set.
#:
#: ``CLASSIFICATION_GATED`` is gone last (#1028). Its three families are gated INSIDE the
#: orchestration now — one place decides, once, from the classification the result carries —
#: rather than each call site deciding for itself. Migration and applicability turned out to
#: be different questions: the aggregate can own a family it does not always run.
#:
#: The map is EMPTY, which is the state ADR 0017 phase 1 was aiming at: every public
#: ``recognise_*`` family is owned by the one orchestration. The mechanism stays — a new
#: family still has to be classified — and every enum member survives for a future one.
DEFERRED: dict[str, Deferred] = {}


@dataclass(frozen=True)
class RecognitionResult:
    """The immutable feature inventory produced by one recognition orchestration run.

    Every public ``recognise_*`` family is owned here, although classification gates mean an
    inapplicable family need not run. This is a recognition inventory, not drafting state and
    not a promise that the evidence-gated correspondence extensions have landed.
    """

    cylinders: tuple[tuple, tuple]
    countersinks: tuple
    holes: tuple
    hole_patterns: tuple
    bosses: tuple
    channels: tuple
    slots: tuple
    slot_patterns: tuple
    grooves: tuple
    flats: tuple
    pockets: tuple
    pocket_patterns: tuple
    pads: tuple
    turned_steps: tuple
    #: Area-filtered interior prismatic levels. The support spans remain on each record so IR
    #: assembly can preserve level-to-face correspondence; sizing and critique project the Z
    #: values through :meth:`step_ladder` (#915).
    step_levels: tuple[FaceLevel, ...]
    #: Whether the part classified as ROTATIONAL, carried so consumers can tell a gated-away
    #: inventory from an empty one (#1028). ``chamfers``/``fillets``/``plates`` are ``()`` on a
    #: rotational part because they were not run, not because the part has none — the same
    #: empty-vs-absent distinction #1022 drew for the declared path.
    rotational: bool
    #: Candidate step risers, scanned once and projected per consumer (#1025). NOT shoulders:
    #: which risers count depends on the level set the asker holds, and that is the whole
    #: reason this family could not be hoisted until the scan and the filter were separated.
    risers: tuple
    #: Classification-gated inventories (#1028). Recognised only for the class that consumes
    #: them: chamfers and fillets on a non-rotational part (a turned part's chamfers are
    #: conical, so the recogniser finds none anyway), plates additionally only when there is no
    #: turned profile. The gate lives HERE, in the one orchestration, rather than at each call
    #: site — which is the distinction that let these migrate at all: owning a family and
    #: always running it are different things.
    chamfers: tuple
    fillets: tuple
    plates: tuple

    def step_ladder(self, bb) -> list[float]:
        """The effective step ladder for *bb*: turned shoulders for a Z-turned part, else the
        prismatic face levels (#1025).

        ONE rule, two callers. ``_analyse`` computes the ladder page/scale selection converges
        on, and critique needs the same set to decide which risers count — but they derived it
        separately, so lint could silently project over a different ladder than the model was
        built from (Codex #1031 r3). Both now call this.

        Geometry-only, so it is a legitimate source for critique under ADR 0015: it reads the
        aggregate's own recognition, never the model.
        """
        prof = TurnedProfile.from_steps(list(self.turned_steps))
        if prof is not None and prof.axis == "z":
            return [z for z in prof.shoulders if bb.min.Z + 0.6 < z < bb.max.Z - 0.6]
        return [level.z for level in self.step_levels]


def build_recognition_result(
    part, *, cylinders=None, rotational: bool = False
) -> RecognitionResult:
    """Run the shared recognition inventory exactly once for *part*.

    Dependencies are computed by this orchestration layer and injected downstream: holes
    reuse both the cylinder substrate and countersinks, while patterns reuse their accepted
    member records.  No recogniser rediscovers one of those dependencies internally.

    *rotational* is the caller's geometric classification (``analysis._classify_geometry``).
    It gates the three families that only one part class consumes, so migrating them did not
    mean scanning every turned build for a discarded result (#1028).

    It is an argument for a SCOPING reason, not an architectural one, and #1037 removes it.
    An earlier version of this docstring claimed the rotational test was "sizing's question,
    not recognition's", and that deriving it here would move drafting classification into
    ``recognition/``.  That is wrong: ``_is_rotational`` reads bbox proportions, the largest
    external cylinder diameter and its concentricity — all geometric, all facts recovered
    from the solid, and ``recognition/`` already uses bounding boxes and the cylinder
    substrate freely.  Drafting *consumes* the classification for view selection exactly as
    it consumes hole diameters; that does not make it drafting policy.

    The real reason is that ``_classify_geometry`` lives in the analysis stage and moving it
    was larger than #1028 warranted.  Until it moves, the gate is a property of this
    orchestration only because a caller supplies the classification.

    The default is ``False`` — prismatic, so nothing is gated away.  A caller who has no
    classification (the lazy critique aggregate on a declared build) gets the COMPLETE
    inventory, which is the right default for a completeness check: over-recognising costs
    time, under-recognising reports a real feature as absent.
    """

    z_cyls, cross_cyls = cylinders if cylinders is not None else analyse_cylinders(part)
    cyls = (z_cyls, cross_cyls)
    countersinks = recognise_countersinks(part)
    holes = recognise_holes(part, cyls=cyls, csinks=countersinks)
    channels = recognise_channels(part)
    pockets = recognise_pockets(part)
    slots = recognise_slots(part)
    turned_steps = recognise_turned_steps(part, cyls=cyls)
    # ONE place decides, from the classification the result then carries. Per-family
    # conditionals at each call site are what ADR 0017 exists to remove; the difference is
    # that this decides once for every consumer rather than each consumer deciding again.
    prismatic = not rotational
    prof = TurnedProfile.from_steps(list(turned_steps))
    return RecognitionResult(
        cylinders=(tuple(z_cyls), tuple(cross_cyls)),
        countersinks=tuple(countersinks),
        holes=tuple(holes),
        hole_patterns=tuple(recognise_hole_patterns(holes)),
        bosses=tuple(recognise_bosses(part, cyls=cyls)),
        channels=tuple(channels),
        slots=tuple(slots),
        # Derived from the accepted members, like the other two pattern families — the
        # recogniser must not rediscover the slots it groups.
        slot_patterns=tuple(recognise_slot_patterns(slots)),
        grooves=tuple(recognise_grooves(part, cyls=cyls)),
        flats=tuple(recognise_flats(part, cyls=cyls)),
        pockets=tuple(pockets),
        pocket_patterns=tuple(recognise_pocket_patterns(pockets)),
        pads=tuple(recognise_rectangular_pads(part)),
        turned_steps=tuple(turned_steps),
        rotational=rotational,
        step_levels=tuple(step_level_records(part)),
        risers=tuple(recognise_risers(part)),
        chamfers=tuple(recognise_chamfers(part)) if prismatic else (),
        fillets=tuple(recognise_fillets(part)) if prismatic else (),
        plates=tuple(recognise_plates(part)) if prismatic and prof is None else (),
    )
