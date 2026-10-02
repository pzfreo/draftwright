"""Hole, countersink, and hole-pattern declaration and drawing evidence.

The evidence oracle remains independent of production rendering. Engine imports stay
inside functions to preserve evaluation's lazy-load seam.
"""

from __future__ import annotations

import re
from collections import Counter
from math import isclose
from typing import Literal, TypeAlias

Scalar: TypeAlias = int | float | str | bool
Value: TypeAlias = Scalar | tuple[float, ...]
Outcome: TypeAlias = Literal["supported", "unknown", "unsupported"]

#: The compiled requirement that carries a hole's SIZE. A table row documents several
#: requirements per hole (location, through-ness, …); only this one is the fact
#: ``drawing_consumer`` asks about. Crediting the others would mean a hole with a located
#: row but no diameter counted as consumed.
_SIZE_REQUIREMENT = "bore.diameter"
_COUNTERSINK_REQUIREMENTS = ("countersink.diameter", "countersink.angle")


def _drawing_consumer_outcomes(holes, drawing) -> list[Outcome]:
    """Per recognised hole: did the DRAWING carry its size? — observed, not declared.

    ``supported`` its size reached the sheet **and the annotation carrying it renders that
    value**; ``unsupported`` the engine accounts for the hole and did not place its size;
    ``unknown`` nothing can be joined to the hole without guessing.

    Be precise about ``unknown``: :func:`evaluate_case` credits a unit only when the state is
    ``supported``, so downstream an ``unknown`` scores as a MISS, distinguishable from a
    dropped callout only in the diagnostic text. It is an honest label, not an exemption.

    **One correspondence implementation** (#1206). This used to carry its own — matching
    recognised holes to IR features by axis, diameter and position, then asking whether any
    annotation named ``hc_*`` — beside `linting.hole_coverage.hole_requirement_outcomes`,
    which answers the same question. Two implementations of one question drift, and the copy
    was the more generous of the two: it credited holes the ledger declines to join.

    That generosity is a measurable score change, not a refactor. Measured base against head
    over all 16 STEP fixtures, 26 holes move: ``nist_ctc_03`` ap203 and ap242 lose five each
    (15/15 -> 10 supported + 5 unknown) and ``nist_ctc_04`` ap203 and ap242 lose eight each
    (54/54 -> 46 + 8). Every one is a hole the ledger reports ``unverifiable`` — it knows they
    exist and cannot tie them to a feature without guessing — where the old matcher scored
    them ``supported``. They now score ``unknown``. ``nist_ctc_02`` does not move at all; an
    earlier version of this paragraph named it and omitted CTC-03, which is the fixture that
    actually diverges. Crediting a hole whose
    evidence cannot be located is exactly the self-validation #1206 was opened to remove, so
    the lower number is the more honest one.

    **The ledger is a pointer, not proof** (@pzfreo's decision on #1206). ``placed`` means the
    engine recorded an annotation as carrying the requirement; this then follows that pointer
    through `linting.evidence` and confirms the annotation actually renders the approved
    value. A claim the drawing does not bear out is ``unsupported``, not ``supported``.

    Name prefixes are gone with the duplicate: provenance does not care what a renderer called
    its annotation.

    That does **not** close the turned-part gap, and an earlier draft of this paragraph said
    it did. Measured on ``Cylinder(20,60) - Cylinder(6,70)``, the outcome is ``unsupported``
    before and after, with a byte-identical annotation set.

    The cause was never the name. When this was written no annotation on that sheet carried a
    measurement claim at all — not ``ldr_z0`` (``ø12``), not ``dim_od`` (``ø40``), not
    ``dim_height`` — while the compiled plan did hold ``hole.bore.diameter`` and
    ``rotational.od.diameter``: the rotational render path threaded no ADR 5 (was 0010) provenance.
    #1225 fixed the threading, so ``ldr_z0`` and ``dim_od`` now claim and both confirm; only
    ``dim_height`` still carries none, and that one is #1230 rather than a tagging gap.

    **The ledger symptom is unchanged by that fix** — measured head against ``main``,
    ``bore.diameter`` is still ``missing`` and this function still returns ``unsupported`` for
    that hole, exactly as the paragraph above says. (A draft of this sentence said "yields no
    outcome", which is false: there is one outcome and it is ``unsupported``. Contradicting a
    correct sentence four lines up, inside the fix for a docstring falsified by its own diff.)
    Tagging the annotation was necessary and is not sufficient, which is worth stating plainly
    here rather than letting "#1227 is fixed" read as "the turned part is covered".

    It is NOT #754, which closed on 2026-07-22 — an earlier draft of this paragraph cited it,
    which is the fourth instance of the stale citation #951 exists to remove, written inside a
    sentence correcting a different false claim.
    """
    from draftwright.linting.evidence import verify_measurement_claims
    from draftwright.linting.hole_coverage import canonical_hole_sites, hole_requirement_outcomes
    from draftwright.model.compiled import compile_dimensions

    model = drawing.model()
    # Recompiled through the public model rather than read off `Drawing._build`: the compiler
    # is deterministic over the model, so this is the same inventory the build used, and the
    # state-bus guard keeps `dwg._*` to `drawing.py` alone.
    plan = compile_dimensions(model)
    ledger = hole_requirement_outcomes(
        drawing.recognition(),
        getattr(model, "features", ()),
        drawing.registry,
        plan.diagnostics,
        ownership=drawing.recognition_ownership(),
    )
    # NOT just `value_absent`. `supported` is meant to mean the annotation carrying the size
    # renders it, so anything short of `confirmed` fails that: an `unreadable` annotation
    # draws no text at all, and an `unresolved` one claims a measurement the compiler never
    # approved (the ADR 4 (was 0016 Amdt 1) violation). Crediting either was the PR body's own
    # sentence — "and the annotation carrying it renders that value" — being false of the
    # code beneath it (#1223).
    unconfirmed = {
        claim.measurement
        for claim in verify_measurement_claims(drawing.registry, plan)
        if claim.state != "confirmed" and claim.measurement is not None
    }

    def _borne_out(entry) -> bool:
        """Whether the placement the ledger reports is confirmed by what is drawn."""
        return not any(
            getattr(claim, "feature", None) in entry.features
            and str(getattr(claim, "parameter", "")) == _SIZE_REQUIREMENT
            for claim in unconfirmed
        )

    by_position: dict[tuple, Outcome] = {}
    for entry in ledger:
        if entry.parameter_id == _SIZE_REQUIREMENT:
            state: Outcome = "supported" if entry.state == "placed" else "unsupported"
            if state == "supported" and not _borne_out(entry):
                state = "unsupported"
        else:
            # Non-size parameters contribute no site. An `unverifiable` entry needs no arm
            # of its own: its holes reach `unknown` through the lookup default below, and a
            # branch that cannot change an outcome is one a reader trusts wrongly. The
            # property it looked like it was enforcing — that every `unknown` joins an
            # `unverifiable` entry — is asserted in the tests, where it can fail.
            continue
        for member in entry.members:
            by_position.setdefault(member, state)

    # `canonical_hole_sites`, not `hole.location`: the ledger keys a THROUGH hole with its
    # axis coordinate zeroed, so the same bore keys identically whichever face it was measured
    # from. Keying on the raw location matched nothing for every through hole and reported
    # four otherwise-perfect corpus units as `unknown`.
    outcomes: list[Outcome] = []
    for hole in holes:
        sites = canonical_hole_sites(hole)
        outcomes.append(next((by_position[s] for s in sites if s in by_position), "unknown"))
    return outcomes


def _hole_model_outcomes(holes, recognition, features, *, ownership=None) -> list[Outcome]:
    """Per recognised hole: does *features* contain one exact IR owner?

    This deliberately reuses :func:`hole_requirement_outcomes` rather than growing a second
    geometry matcher in the evaluation module.  Only its correspondence evidence is read:
    annotation state from the empty registry is necessarily ``missing`` and contributes no
    credit.  The recognised ``holes`` supplied by the build remain the observed numerator;
    the corpus remains the independent denominator.
    """
    from draftwright.linting.hole_coverage import canonical_hole_sites, hole_requirement_outcomes
    from draftwright.registry import AnnotationRegistry

    ledger = hole_requirement_outcomes(
        recognition, features, AnnotationRegistry(), ownership=ownership
    )
    by_position: dict[tuple, Outcome] = {}
    for entry in ledger:
        if entry.parameter_id != _SIZE_REQUIREMENT:
            continue
        state: Outcome = "supported" if entry.features else "unknown"
        for member in entry.members:
            by_position.setdefault(member, state)
    return [
        next(
            (by_position[site] for site in canonical_hole_sites(hole) if site in by_position),
            "unknown",
        )
        for hole in holes
    ]


def _declared_hole_model(part, holes):
    """Declare observed holes through the public ``Sheet.hole`` seam and return its IR."""
    from quiddity import HoleSpec

    from draftwright.sheet import Sheet

    sheet = Sheet(part)
    # Feature correspondence is independent of dimension selection.  An explicit empty set
    # keeps this a valid public Sheet without asking the planner to add evidence of its own.
    sheet.authored_dimensions()
    for observed in holes:
        spec = HoleSpec.from_hole(observed)
        axis = max(zip("xyz", spec.axis, strict=True), key=lambda item: abs(item[1]))[0]

        def recess(value):
            return None if value is None else (value.diameter, value.depth)

        sheet.hole(
            diameter=observed.diameter,
            at=observed.location,
            axis=axis,
            through=spec.bottom == "through",
            depth=observed.depth,
            cbore=recess(spec.cbore),
            spotface=recess(spec.spotface),
            csink=spec.csink,
        )
    return sheet.model()


def _countersink_sites(countersinks, recognition) -> list[tuple[tuple[float, float, float], ...]]:
    """Return each seat's validated provider-owned parent-hole sites.

    Object identity says which owner the aggregate chose; the provider's public semantic predicate
    independently proves that choice.  Each seat must be attached exactly once.  A mismatched,
    duplicate or absent attachment therefore fails closed instead of replaying a corrupt aggregate
    through every consumer boundary.  A second seat on the opposite face of one bore deliberately
    remains absent: the singular ``HoleRecord`` waist cannot represent it, and the production
    ledger reports its two requirements as unverifiable.
    """
    from quiddity import countersink_matches_hole

    from draftwright.linting.hole_coverage import canonical_hole_sites

    inventory_ids = {id(countersink) for countersink in countersinks}
    if len(inventory_ids) != len(countersinks):
        return [()] * len(countersinks)

    owners: dict[int, list[object | None]] = {identity: [] for identity in inventory_ids}
    invalid_inventory = False
    for hole in recognition.holes:
        countersink = getattr(hole, "csink", None)
        if countersink is None:
            continue
        identity = id(countersink)
        if identity not in owners:
            invalid_inventory = True
            continue
        if not countersink_matches_hole(countersink, hole):
            owners[identity].append(None)
            continue
        owners[identity].append(hole)

    if invalid_inventory:
        return [()] * len(countersinks)
    sites: list[tuple[tuple[float, float, float], ...]] = []
    for countersink in countersinks:
        matches = owners[id(countersink)]
        sites.append(
            canonical_hole_sites(matches[0])
            if len(matches) == 1 and matches[0] is not None
            else ()
        )

    # A through-hole's canonical site intentionally drops its axial coordinate.  Two
    # disconnected coaxial bodies can therefore collide even though their seats are distinct.
    # The hole ledger cannot attribute one surviving feature/annotation between those physical
    # occurrences, so no colliding seat may borrow the same evidence.
    collisions = Counter(site for site in sites if site)
    return [site if not site or collisions[site] == 1 else () for site in sites]


def _countersink_model_outcomes(countersinks, recognition, features) -> list[Outcome]:
    """Per physical seat: does *features* retain both exact countersink requirements?"""
    from draftwright.linting.hole_coverage import hole_requirement_outcomes
    from draftwright.registry import AnnotationRegistry

    ledger = hole_requirement_outcomes(recognition, features, AnnotationRegistry())
    sites = _countersink_sites(countersinks, recognition)
    results: list[Outcome] = []
    for owned_sites in sites:
        if not owned_sites:
            results.append("unknown")
            continue
        supported = all(
            any(
                entry.parameter_id == parameter
                and entry.features
                and any(site in entry.members for site in owned_sites)
                for entry in ledger
            )
            for parameter in _COUNTERSINK_REQUIREMENTS
        )
        results.append("supported" if supported else "unknown")
    return results


_COUNTERSINK_TERM_RE = re.compile(
    r"⌵\s*[ø⌀Ø]\s*(?P<diameter>\d+(?:\.\d+)?)\b[^×]*"
    r"×\s*(?P<angle>\d+(?:\.\d+)?)\b\s*°"
)


def _countersink_claim_has_role_specific_ink(claim, annotation) -> bool:
    """Verify the nominal against its countersink role, not any number in the label."""
    label = getattr(annotation, "label", None)
    match = _COUNTERSINK_TERM_RE.search(str(label)) if label else None
    parameter = str(getattr(claim.measurement, "parameter", ""))
    role = {
        "countersink.diameter": "diameter",
        "countersink.angle": "angle",
    }.get(parameter)
    if match is None or role is None:
        return False
    try:
        rendered = float(match.group(role))
        expected = tuple(float(value) for value in claim.expected)
    except (TypeError, ValueError):
        return False
    return any(isclose(rendered, value, rel_tol=0.0, abs_tol=1e-6) for value in expected)


def _countersink_drawing_outcomes(countersinks, recognition, drawing) -> list[Outcome]:
    """Per physical seat: did confirmed placed ink carry both seat measurements?"""
    from draftwright.linting.evidence import verify_measurement_claims
    from draftwright.linting.hole_coverage import hole_requirement_outcomes
    from draftwright.model.compiled import compile_dimensions

    model = drawing.model()
    plan = compile_dimensions(model)
    ledger = hole_requirement_outcomes(
        recognition,
        getattr(model, "features", ()),
        drawing.registry,
        plan.diagnostics,
        ownership=drawing.recognition_ownership(),
    )
    confirmed = set()
    for claim in verify_measurement_claims(drawing.registry, plan):
        feature = getattr(claim.measurement, "feature", None)
        parameter = str(getattr(claim.measurement, "parameter", ""))
        if (
            claim.state == "confirmed"
            and feature is not None
            and parameter in _COUNTERSINK_REQUIREMENTS
            and _countersink_claim_has_role_specific_ink(
                claim, drawing.registry.named(claim.annotation)
            )
        ):
            confirmed.add((id(feature), parameter))
    results: list[Outcome] = []
    for owned_sites in _countersink_sites(countersinks, recognition):
        if not owned_sites:
            results.append("unknown")
            continue
        supported = all(
            any(
                entry.parameter_id == parameter
                and entry.state == "placed"
                and any(site in entry.members for site in owned_sites)
                and any((id(feature), parameter) in confirmed for feature in entry.features)
                for entry in ledger
            )
            for parameter in _COUNTERSINK_REQUIREMENTS
        )
        results.append("supported" if supported else "unsupported")
    return results


def _pattern_kind(pattern) -> str:
    if _is_rectangular_hole_set(pattern):
        return "grid"
    if hasattr(pattern, "diameter") and hasattr(pattern, "center"):
        return "bolt_circle"
    if hasattr(pattern, "row_pitch"):
        return "grid"
    return "linear"


def _is_rectangular_hole_set(pattern) -> bool:
    from quiddity import RectangularHoleSet

    return isinstance(pattern, RectangularHoleSet)


def _pattern_members(pattern) -> tuple[tuple[float, float, float], ...]:
    """Recognition-owned member sites in the production ledger's canonical space."""
    from draftwright.linting.hole_coverage import canonical_hole_sites

    return canonical_hole_sites(pattern)


def _pattern_model_outcomes(patterns, recognition, features) -> list[Outcome]:
    """Per recognised pattern: does *features* contain one exact IR owner?"""
    from draftwright.linting.hole_coverage import hole_requirement_outcomes
    from draftwright.registry import AnnotationRegistry

    outcomes = hole_requirement_outcomes(recognition, features, AnnotationRegistry())
    by_members: dict[tuple, list] = {}
    for outcome in outcomes:
        if outcome.source_kind == "hole_pattern":
            by_members.setdefault(outcome.members, []).append(outcome)
    result: list[Outcome] = []
    for pattern in patterns:
        matched = by_members.get(_pattern_members(pattern), ())
        result.append(
            "supported"
            if matched and all(len(outcome.features) == 1 for outcome in matched)
            else "unknown"
        )
    return result


def _pattern_drawing_outcomes(patterns, drawing) -> list[Outcome]:
    """Per recognised pattern: did its grouping grammar reach the placed drawing?"""
    from draftwright.linting.evidence import verify_measurement_claims
    from draftwright.linting.hole_coverage import hole_requirement_outcomes
    from draftwright.model.compiled import compile_dimensions

    recognition = drawing.recognition()
    model = drawing.model()
    plan = compile_dimensions(model)
    outcomes = hole_requirement_outcomes(
        recognition,
        model.features,
        drawing.registry,
        plan.diagnostics,
        ownership=drawing.recognition_ownership(),
    )
    unconfirmed = {
        claim.measurement
        for claim in verify_measurement_claims(drawing.registry, plan)
        if claim.state != "confirmed" and claim.measurement is not None
    }

    def rendered_group_count(outcome) -> bool:
        """Whether the exact owner's count-bearing callout actually renders ``N×``."""
        for name, annotation in drawing.registry.iter_named():
            measurements = drawing.registry.measurement_of(name)
            owns_pattern_diameter = any(
                getattr(measurement, "feature", None) in outcome.features
                and str(getattr(measurement, "parameter", "")) == "bore.diameter"
                for measurement in measurements
            )
            if not owns_pattern_diameter:
                continue
            if int(getattr(annotation, "covers_count", 1) or 1) != outcome.member_count:
                continue
            label = getattr(annotation, "label", None) or getattr(
                annotation, "_annotate_label", None
            )
            match = re.match(r"^\s*(\d+)\s*[×x]\s", str(label or ""))
            if match is not None and int(match.group(1)) == outcome.member_count:
                return True
        return False

    def rendered_interval_count(outcome, expected: int) -> bool:
        """Whether the exact pitch dimension renders its required interval multiplier."""
        for name, annotation in drawing.registry.iter_named():
            owns_pitch = any(
                getattr(measurement, "feature", None) in outcome.features
                and str(getattr(measurement, "parameter", "")) == outcome.parameter_id
                for measurement in drawing.registry.measurement_of(name)
            )
            if not owns_pitch:
                continue
            label = getattr(annotation, "label", None) or getattr(
                annotation, "_annotate_label", None
            )
            match = re.match(r"^\s*(\d+)\s*[×x]\s", str(label or ""))
            if match is not None and int(match.group(1)) == expected:
                return True
        return False

    def borne_out(outcome, pattern) -> bool:
        """Whether placed dimensional evidence renders its compiler-approved value."""
        if outcome.parameter_id == "grouping.count":
            return rendered_group_count(outcome)
        value_confirmed = not any(
            getattr(claim, "feature", None) in outcome.features
            and str(getattr(claim, "parameter", "")) == outcome.parameter_id
            for claim in unconfirmed
        )
        if not value_confirmed:
            return False
        if outcome.parameter_id == "pitch.length":
            interval_count = len(pattern.holes) - 1
        elif outcome.parameter_id == "grid_pitch.length.row":
            interval_count = (2 if _is_rectangular_hole_set(pattern) else pattern.rows) - 1
        elif outcome.parameter_id == "grid_pitch.length.col":
            interval_count = (2 if _is_rectangular_hole_set(pattern) else pattern.cols) - 1
        else:
            interval_count = None
        return interval_count is None or rendered_interval_count(outcome, interval_count)

    by_members: dict[tuple, list] = {}
    for outcome in outcomes:
        if outcome.source_kind == "hole_pattern":
            by_members.setdefault(outcome.members, []).append(outcome)
    expected_parameters = {
        "bolt_circle": {"grouping.count", "bolt_circle.diameter"},
        "linear": {"grouping.count", "pitch.length"},
        "grid": {
            "grouping.count",
            "grid_pitch.length.row",
            "grid_pitch.length.col",
        },
    }
    placed = {"placed", "satisfied_by_structured_note"}
    result: list[Outcome] = []
    for pattern in patterns:
        candidates = by_members.get(_pattern_members(pattern), ())
        relevant = {
            outcome.parameter_id: outcome
            for outcome in candidates
            if outcome.parameter_id in expected_parameters[_pattern_kind(pattern)]
        }
        expected = expected_parameters[_pattern_kind(pattern)]
        if not candidates or any(len(outcome.features) != 1 for outcome in candidates):
            result.append("unknown")
        elif set(relevant) != expected or any(
            outcome.state not in placed or not borne_out(outcome, pattern)
            for outcome in relevant.values()
        ):
            result.append("unsupported")
        else:
            result.append("supported")
    return result


def _declared_pattern_model(part, patterns):
    """Declare observed arrangements through public ``Sheet.pattern`` and return its IR."""
    from quiddity import HoleSpec

    from draftwright.model import hole
    from draftwright.sheet import Sheet

    sheet = Sheet(part)
    sheet.authored_dimensions()

    def recess(value):
        return None if value is None else (value.diameter, value.depth)

    for observed in patterns:
        member = observed.holes[0]
        spec = HoleSpec.from_hole(member)
        axis = max(zip("xyz", spec.axis, strict=True), key=lambda item: abs(item[1]))[0]
        declared_member = hole(
            diameter=member.diameter,
            at=member.location,
            axis=axis,
            through=spec.bottom == "through",
            depth=member.depth,
            cbore=recess(spec.cbore),
            spotface=recess(spec.spotface),
            csink=spec.csink,
        )
        members = tuple(item.location for item in observed.holes)
        center = getattr(observed, "center", None)
        if center is None:
            center = tuple(
                sum(point[index] for point in members) / len(members) for index in range(3)
            )
        kind = _pattern_kind(observed)
        kwargs: dict[str, object] = {
            "kind": kind,
            "count": len(members),
            "at": center,
            "axis": axis,
            "members": members,
        }
        if kind == "bolt_circle":
            kwargs["bcd"] = observed.diameter
        elif kind == "linear":
            kwargs.update(pitch=observed.pitch, direction=observed.direction)
        else:
            grid = (
                (observed.height, observed.width)
                if _is_rectangular_hole_set(observed)
                else (observed.row_pitch, observed.col_pitch)
            )
            kwargs.update(
                grid=grid,
                rows=2 if _is_rectangular_hole_set(observed) else observed.rows,
                cols=2 if _is_rectangular_hole_set(observed) else observed.cols,
                angle=observed.angle,
            )
        sheet.pattern(declared_member, **kwargs)
    return sheet.model()
