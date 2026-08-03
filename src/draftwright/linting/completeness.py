"""Manufacturing completeness — is every recognised feature actually defined? (#996 WP1)

`lint_feature_coverage` asks one narrow question: does every part **diameter** have a
callout. Four of the five real-part case studies behind #996 (#909/#914/#916/#917/#918)
produce a drawing you could not manufacture from *and lint clean*, because nobody asks "does
this pocket have a size" or "does anything say where it is".

This module asks that of **every recognised feature**, and returns a per-feature verdict
rather than a count.

## The two sides come from opposite places, on purpose

**Requirements** come from **recognition** — the geometry. Never from the dimensioning IR.
That is the ADR 0015 lint/coverage carve-out (pinned by `tests/test_import_boundaries.py`),
and its rationale is this epic in one line:

    sourcing coverage from the plan would be circular — a feature the planner omitted would
    never be flagged

#916, #917 and #918 are all *recognised, never planned*. Ask the plan what ought to be
dimensioned and those three become invisible by construction, which is exactly why they lint
clean today.

**Coverage** comes from the finished drawing, via the measurement identity each annotation
now carries (#1002): `Drawing.measurement_keys()` says which `(feature, parameter)` a placed
annotation actually draws. That is an exact answer, and it is the reason this check is
possible at all — the first attempt matched dimension *witness geometry* instead and
reported a fully-dimensioned pocket as undefined twice over, because its size is a leader
callout with no witnesses and its location dims did not land where the geometry predicted.

Reading identity off the drawing is not the circularity the carve-out forbids: the drawing
is the artefact under test, and `measurement_keys` returns plain dicts, not IR objects.

## Three verdicts, not two

``covered`` / ``uncovered`` / ``unchecked``. The third carries the weight.

This epic has now made the same mistake three times (#1001 r1 and r2, #1002 r3): narrowing
what counts as a measurement to reduce noise, and thereby turning noise into silence. A kind
with no rule in :data:`_REQUIRED`, or one whose renderer records no identity yet
(#926 hole callouts, #754, #1004, #1005), must answer **"I cannot tell"** — never "fine".
So the unchecked count is visible pressure to close those, rather than a gap that reads as a
pass. :func:`completeness_summary` reports it as its own number for the same reason.
"""

from __future__ import annotations

from dataclasses import dataclass

COVERED = "covered"
UNCOVERED = "uncovered"
UNCHECKED = "unchecked"

#: kind -> (size parameter ids, location parameter ids). A recognised kind absent from here
#: answers ``unchecked``, by design — see the module docstring.
#:
#: The ids are the compiler's own `parameter_id`s, read off real builds rather than guessed,
#: because a typo here would silently mean "never covered" and read as a real finding.
_REQUIRED: dict[str, tuple[frozenset, frozenset]] = {
    "pocket": (
        frozenset({"pocket_width.length", "pocket_length.length", "pocket_depth.length"}),
        frozenset({"location_pocket.location"}),
    ),
    "slot": (
        frozenset({"slot_width.length", "slot_length.length"}),
        frozenset({"location_slot.location"}),
    ),
    "chamfer": (frozenset({"chamfer.length"}), frozenset()),
    "fillet": (frozenset({"fillet.radius"}), frozenset()),
}


@dataclass(frozen=True)
class FeatureVerdict:
    """One recognised feature's completeness, as plain data.

    ``key`` is geometry-derived (``kind@(x,y,z)``) so it survives a rebuild — the property
    `Drawing.suppressions()` needs for the same reason: a bare kind name makes two pockets
    indistinguishable, and *which one* is undefined is the whole question.
    """

    kind: str
    key: str
    size: str
    location: str
    drawn: tuple = ()

    @property
    def complete(self) -> bool:
        """Both defined. ``unchecked`` is NOT complete — an unanswered question is not a
        pass, and collapsing the two is the failure this module exists to avoid."""
        return self.size == COVERED and self.location == COVERED

    @property
    def undefined(self) -> bool:
        """Something is positively missing, as opposed to unknown."""
        return UNCOVERED in (self.size, self.location)


def _origin(feature_key: str) -> tuple | None:
    """The ``(x, y, z)`` out of a ``kind@(x,y,z)/axis[...]`` ledger key."""
    if "@(" not in feature_key:
        return None
    inner = feature_key.split("@(", 1)[1].split(")", 1)[0]
    try:
        return tuple(float(v) for v in inner.split(","))
    except ValueError:
        return None


def _drawn_by_feature(dwg) -> list[tuple[str, tuple, str]]:
    """``(kind, origin, parameter_id)`` for every measurement the drawing actually draws."""
    out = []
    for name, _ann in dwg.iter_annotations():
        for key in dwg.measurement_keys(name):
            feature = key["feature"]
            origin = _origin(feature)
            if origin is not None:
                out.append((feature.split("@", 1)[0], origin, key["parameter_id"]))
    return out


def _anchor(feat) -> tuple | None:
    """A geometry-derived point identifying *feat*, matching the IR's frame origin.

    ``None`` when no field yields one — and that is load-bearing. The first cut fell back to
    ``(0, 0, 0)``, so all four chamfers on a box collapsed onto one key, matched none of the
    four drawn identities, and were reported **undefined**. Measured on a part whose chamfers
    are all correctly dimensioned: four fabricated alarms, from the checker rather than the
    drawing. An unmatchable feature is *unknown*, never *undefined* — the same polarity rule
    the rest of this module is built on, applied to the join itself.
    """
    for field in ("location", "at"):
        point = getattr(feat, field, None)
        if point is not None:
            return tuple(point)[:3]
    frame = getattr(feat, "frame", None)
    if frame is not None:
        return tuple(frame.origin)[:3]
    wa, la = getattr(feat, "width_axis", None), getattr(feat, "long_axis", None)
    if wa is not None and la is not None:
        da = next(a for a in "xyz" if a not in (wa, la))
        coords = {wa: feat.w_center, la: (feat.lo + feat.hi) / 2, da: getattr(feat, "d_hi", 0.0)}
        return (coords["x"], coords["y"], coords["z"])
    return None


def _key(kind: str, point) -> str:
    x, y, z = point
    return f"{kind}@({x:.3f},{y:.3f},{z:.3f})"


def feature_completeness(part, dwg, *, analysis=None, tol: float = 0.6) -> list[FeatureVerdict]:
    """A verdict per recognised feature: is a defining size drawn, and a location?

    Reads the recognised inventory off *analysis* when the caller has one (the build already
    paid for it — ADR 0015's one-inventory rule) and recognises directly otherwise, so the
    check judges any producer rather than only this engine's own builds.
    """
    drawn = _drawn_by_feature(dwg)
    out: list[FeatureVerdict] = []
    for kind, feats in _inventory(part, analysis):
        want = _REQUIRED.get(kind)
        for feat in feats:
            anchor = _anchor(feat)
            if anchor is None:
                # No anchor means the join cannot run, so nothing can be concluded either
                # way. Reported as its own key so the feature still APPEARS — a feature
                # missing from the report reads as "nothing to say about it".
                out.append(FeatureVerdict(kind, f"{kind}@?", UNCHECKED, UNCHECKED))
                continue
            mine = tuple(
                sorted(
                    parameter
                    for d_kind, origin, parameter in drawn
                    if d_kind == kind and _near(origin, anchor, tol)
                )
            )
            if want is None:
                size = location = UNCHECKED
            else:
                size_ids, location_ids = want
                size = COVERED if size_ids <= set(mine) else UNCOVERED
                location = COVERED if location_ids <= set(mine) else UNCOVERED
            out.append(FeatureVerdict(kind, _key(kind, anchor), size, location, mine))
    return out


def _near(a, b, tol: float) -> bool:
    return len(a) == len(b) and all(abs(p - q) <= tol for p, q in zip(a, b, strict=True))


def _inventory(part, analysis):
    """``(kind, records)`` for every recognised feature kind.

    Every kind the recognisers produce is listed, including those with no rule yet, so the
    unchecked set is REAL. Enumerating only the kinds this module can judge would make the
    report agree with itself by construction — the self-confirming shape the ADR 0015
    carve-out prevents, one level up.
    """
    from draftwright.recognition import (
        recognise_bosses,
        recognise_chamfers,
        recognise_fillets,
        recognise_flats,
        recognise_grooves,
        recognise_holes,
        recognise_pockets,
        recognise_rectangular_pads,
        recognise_slots,
    )

    def _from(name, fn):
        got = getattr(analysis, name, None) if analysis is not None else None
        return got if got is not None else fn()

    return [
        ("hole", _from("holes", lambda: recognise_holes(part))),
        ("slot", _from("slots", lambda: recognise_slots(part))),
        ("pocket", _from("pockets", lambda: recognise_pockets(part))),
        ("pad", _from("pads", lambda: recognise_rectangular_pads(part))),
        ("boss", _from("bosses", lambda: recognise_bosses(part))),
        ("chamfer", recognise_chamfers(part)),
        ("fillet", recognise_fillets(part)),
        ("flat", recognise_flats(part)),
        ("groove", recognise_grooves(part)),
    ]


def completeness_summary(verdicts) -> dict:
    """Counts by verdict — the blast-radius measurement #996 WP1 asks for first.

    ``unchecked`` is its own number and is folded into neither side. Reporting "83% complete"
    while a third of the features were never examined is the kind of confidence this epic
    exists to remove.
    """
    verdicts = list(verdicts)
    judged = [v for v in verdicts if not (v.size == UNCHECKED and v.location == UNCHECKED)]
    return {
        "features": len(verdicts),
        "judged": len(judged),
        "complete": sum(1 for v in judged if v.complete),
        "undefined": sum(1 for v in judged if v.undefined),
        "unchecked": len(verdicts) - len(judged),
        "by_kind": _by_kind(verdicts),
    }


def _by_kind(verdicts) -> dict:
    out: dict = {}
    for v in verdicts:
        row = out.setdefault(v.kind, {COVERED: 0, UNCOVERED: 0, UNCHECKED: 0})
        for verdict in (v.size, v.location):
            row[verdict] += 1
    return out
