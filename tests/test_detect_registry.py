"""ADR 0013 Phase 1c — the typed record→Feature converter registry (#752).

`model/detect.py` translates recognition records into IR `Feature`s through one
typed registry seam. These tests are the fail-closed guard on that seam:

- **completeness + uniqueness** — every recognition record type has *exactly one*
  home across the three tiers (uniform converter / derived converter / documented
  orchestrated), so a new recogniser cannot silently produce a record no converter
  handles, nor be double-registered.
- **fail-closed dispatch** — `convert()` raises on an unregistered record type.
- **no leak** — nothing produced by `build_part_model` is a recognition-layer
  record; the seam always lowers to the IR.
"""

from __future__ import annotations

import inspect
import typing

import pytest
from build123d import Box, Cylinder, Pos

import draftwright.recognition as recognition
from draftwright.model.detect import (
    _CONVERTERS,
    _DERIVED_CONVERTERS,
    _ORCHESTRATED_RECORDS,
    ConvContext,
    build_part_model,
    convert,
)
from draftwright.model.ir import Feature
from draftwright.recognition._record import Record


def _record_types_in(annot) -> set[type]:
    """Every `Record` subclass reachable in a return annotation — unwrapping
    ``list[...]``, unions/``| None`` and nesting.

    Decompose parameterised generics (``get_args``) BEFORE the bare-class check: on
    Python 3.10 ``isinstance(list[X], type)`` is ``True`` (a GenericAlias quirk fixed in
    3.11), so an ``isinstance``-first order would treat ``list[BossRecord]`` as a plain
    class and never reach its args — silently dropping the record type on 3.10."""
    args = typing.get_args(annot)
    if args:
        out: set[type] = set()
        for arg in args:
            out |= _record_types_in(arg)
        return out
    if isinstance(annot, type):
        return {annot} if issubclass(annot, Record) else set()
    return set()


def _recogniser_record_universe() -> set[type]:
    """The authoritative set of record types, derived MECHANICALLY from the public
    ``recognise_*`` return annotations (not a hand-maintained list). This is what makes
    the completeness guard genuinely fail-closed: add ``recognise_threads() ->
    list[ThreadRecord]`` and ``ThreadRecord`` enters this universe automatically, so the
    partition test below fails until it is given a home — matching the ADR 0013 claim
    that a new recogniser cannot silently emit features with no converter."""
    universe: set[type] = set()
    for name in dir(recognition):
        if not name.startswith("recognise_"):
            continue
        fn = getattr(recognition, name)
        # Fail loud, not open: an unresolvable return annotation must surface as a test
        # failure naming the recogniser, never be silently skipped (which would let a new
        # record type escape the universe and defeat the completeness guard below).
        try:
            hints = typing.get_type_hints(fn)
        except Exception as exc:
            raise AssertionError(
                f"could not resolve return hints for recognition.{name}: {exc!r}"
            ) from exc
        # Every recogniser must *declare* a Record-typed return (all 14 today do:
        # `list[<Record...>]`). A missing annotation (`get_type_hints` → no "return")
        # or a non-Record return contributes nothing to the universe — which would let a
        # new recogniser's record type escape the completeness guard. Reject it here so
        # the fail-closed property survives a recogniser added with a bad/absent annotation.
        found = _record_types_in(hints.get("return"))
        assert found, (
            f"recognition.{name} has no Record-typed return annotation "
            f"(got {hints.get('return')!r}) — the record→Feature completeness guard needs one"
        )
        universe |= found
    return universe


def test_registry_tiers_partition_every_record_type():
    """Every recognition record type has exactly one home (completeness + uniqueness)."""
    expected = _recogniser_record_universe()
    assert expected, "mechanical record-type derivation found nothing — check recognition surface"

    tiers = [set(_CONVERTERS), set(_DERIVED_CONVERTERS), set(_ORCHESTRATED_RECORDS)]
    homed = tiers[0] | tiers[1] | tiers[2]

    missing = expected - homed
    assert not missing, (
        f"record types with no converter/home: {sorted(t.__name__ for t in missing)}"
    )

    extra = homed - expected
    assert not extra, f"registry names non-recogniser types: {sorted(t.__name__ for t in extra)}"

    # Pairwise disjoint — no record type lives in two tiers.
    for i, a in enumerate(tiers):
        for b in tiers[i + 1 :]:
            dup = a & b
            assert not dup, f"record type in two tiers: {sorted(t.__name__ for t in dup)}"


def test_orchestrated_records_document_their_residual_reason():
    """Tier 3 is the ADR 0013 Phase 1 accepted residual — each entry states why."""
    for rec_type, reason in _ORCHESTRATED_RECORDS.items():
        assert isinstance(reason, str) and reason.strip(), f"{rec_type.__name__} needs a reason"


def test_uniform_converters_are_callable():
    assert all(callable(c) for c in _CONVERTERS.values())
    assert all(callable(c) for c in _DERIVED_CONVERTERS.values())


def test_uniform_converter_is_registered_under_the_type_it_consumes():
    """Each uniform converter is keyed under the record type its first parameter is
    annotated for — a mechanical guard against a mis-registration (e.g. ``Slot ->
    _convert_pocket``) that the ``Any``-typed registry value cannot catch statically."""
    for key, conv in _CONVERTERS.items():
        first = next(iter(inspect.signature(conv).parameters))
        consumed = typing.get_type_hints(conv).get(first)
        assert consumed is key, (
            f"{conv.__name__} is registered under {key.__name__} but consumes "
            f"{getattr(consumed, '__name__', consumed)}"
        )


def test_convert_fails_closed_on_unregistered_record():
    class _NotARecord:
        pass

    ctx = ConvContext(bbox=None, orientation=None)
    with pytest.raises(TypeError, match="no IR converter registered"):
        convert(_NotARecord(), ctx)


def _rich_parts():
    """Parts spanning the feature families detect.py emits — holes, prismatic
    envelope, a turned profile (steps/boss), and a chamfer."""
    plate = Box(60, 40, 20) - Pos(0, 0, 10) * Cylinder(4, 20)
    shaft = Cylinder(8, 40) + Pos(0, 0, 25) * Cylinder(12, 10)
    return [plate, shaft]


def test_build_part_model_never_leaks_a_recognition_record():
    """The seam always lowers to the IR: no `build_part_model` output is a
    recognition-layer `Record`; every feature satisfies the IR `Feature` shape."""
    for part in _rich_parts():
        model = build_part_model(part)
        assert model.features, "expected the drive part to yield features"
        for f in model.features:
            assert not isinstance(f, Record), (
                f"recognition record leaked into IR: {type(f).__name__}"
            )
            # IR Feature shape (kind + frame + parameters), incl. the PMI/authored features.
            assert hasattr(f, "kind") and hasattr(f, "frame") and hasattr(f, "parameters")
            assert isinstance(f, Feature)
