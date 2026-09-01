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

import dataclasses
import inspect
import typing

import b123d_recognisers as recognition
import pytest
from _recogniser_public_contract import (
    public_record_return_types,
    public_record_universe,
)
from b123d_recognisers import HoleRecord
from build123d import Box, Cylinder, Pos

from draftwright.model.detect import (
    _CONVERTERS,
    _DERIVED_CONVERTERS,
    _ORCHESTRATED_RECORDS,
    _UNCONSUMED_RECORDS,
    ConvContext,
    build_part_model,
    convert,
)
from draftwright.model.ir import Feature, Frame


def _recogniser_record_universe() -> set[type]:
    """The mechanically derived public record universe (kept as the historical test seam)."""

    return public_record_universe()


def test_record_return_grammar_rejects_private_mixed_or_non_list_shapes(monkeypatch):
    """Finding one public record cannot bless unrelated members or an arbitrary container."""

    @dataclasses.dataclass(frozen=True)
    class _UnpublishedRecord:
        value: int

        def to_dict(self):
            return {"value": self.value}

    with pytest.raises(AssertionError, match="non-public-record list member"):
        public_record_return_types(
            list[_UnpublishedRecord], source="recognition.recognise_private"
        )
    with pytest.raises(AssertionError, match=r"expected list\[PublicRecord\]"):
        public_record_return_types(tuple[HoleRecord, int], source="recognition.recognise_bad")
    with pytest.raises(AssertionError, match="non-public-record list member"):
        public_record_return_types(list[HoleRecord | int], source="recognition.recognise_bad")
    with pytest.raises(AssertionError, match="non-public-record list member"):
        public_record_return_types(list[list[HoleRecord]], source="recognition.recognise_bad")

    def recognise_private():
        return []

    recognise_private.__annotations__ = {"return": list[_UnpublishedRecord]}
    with monkeypatch.context() as patch:
        patch.setattr(recognition, "recognise_private", recognise_private, raising=False)
        patch.setattr(recognition, "__all__", [*recognition.__all__, "recognise_private"])
        with pytest.raises(AssertionError, match="non-public-record list member"):
            public_record_universe()

    def recognise_bad():
        return []

    recognise_bad.__annotations__ = {"return": list[int]}
    with monkeypatch.context() as patch:
        patch.setattr(recognition, "recognise_bad", recognise_bad, raising=False)
        patch.setattr(recognition, "__all__", [*recognition.__all__, "recognise_bad"])
        with pytest.raises(AssertionError, match="has no public-record return annotation"):
            public_record_universe()


def test_registry_tiers_partition_every_record_type():
    """Every recognition record type has exactly one home (completeness + uniqueness)."""
    expected = _recogniser_record_universe()
    assert expected, "mechanical record-type derivation found nothing — check recognition surface"

    tiers = [
        set(_CONVERTERS),
        set(_DERIVED_CONVERTERS),
        set(_ORCHESTRATED_RECORDS),
        set(_UNCONSUMED_RECORDS),
    ]
    homed = set().union(*tiers)

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


def test_unconsumed_records_name_the_issue_recording_their_disposition():
    """Tier 4 is an explicit consumer boundary, not a bin: every entry cites its decision.

    Tier 3 entries are non-requirement substrate. Tier 4 entries belong to unsupported families:
    an open issue may still decide their meaning, or a closed issue may record a reviewed
    unsupported outcome. That distinction has to survive in the register or both tiers collapse
    into an unexplained "we do not convert this" (#1244).
    """
    import re

    from draftwright.recogniser_contract import _UNSUPPORTED

    tracked = {
        reference
        for _records, tracking, _rationale in _UNSUPPORTED.values()
        for reference in [tracking.rsplit("/", 1)[-1]]
    }
    for rec_type, reason in _UNCONSUMED_RECORDS.items():
        assert isinstance(reason, str) and reason.strip(), f"{rec_type.__name__} needs a reason"
        cited = set(re.findall(r"#(\d+)", reason))
        assert cited, f"{rec_type.__name__} names no deciding issue"
        assert cited <= tracked, (
            f"{rec_type.__name__} cites {sorted(cited)}, which is not among the issues the "
            f"capability declaration tracks ({sorted(tracked)}) — the two registers disagree "
            "about why this record is unconsumed"
        )


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


def test_every_uniform_converter_lowers_a_representative_public_record_to_ir():
    """The no-leak guard executes every registered 1:1 converter, not a lucky subset."""

    from test_recogniser_contract import _records_from_recognisers

    records = {type(record): record for _, record in _records_from_recognisers()}
    missing = set(_CONVERTERS) - records.keys()
    assert not missing, (
        f"no representative record for converters: {sorted(t.__name__ for t in missing)}"
    )

    ctx = ConvContext(bbox=Box(200, 200, 200).bounding_box(), orientation="z")
    provider_types = _recogniser_record_universe()
    for record_type in _CONVERTERS:
        converted = convert(records[record_type], ctx)
        assert type(converted) not in provider_types, (
            f"{record_type.__name__} converter leaked {type(converted).__name__}"
        )
        assert isinstance(converted, Feature), (
            f"{record_type.__name__} converter returned {type(converted).__name__}, not IR"
        )


def test_every_derived_converter_lowers_representative_public_records_to_ir():
    """Every grouped converter is called with real provider records and member evidence."""

    from b123d_recognisers import (
        HoleRecord,
        recognise_hole_patterns,
        recognise_holes,
        recognise_pocket_patterns,
        recognise_pockets,
        recognise_slot_patterns,
        recognise_slots,
    )
    from test_recogniser_contract import (
        _bolt_circle_plate,
        _csk_plate,
        _grid_plate,
        _linear_array_plate,
        _pocket_array_plate,
        _pocket_grid_plate,
        _slot_array_plate,
        _slot_grid_plate,
    )

    hole = recognise_holes(_csk_plate())[0]
    converted: list[Feature] = [
        _DERIVED_CONVERTERS[HoleRecord](hole, Frame(tuple(hole.location), "z"))
    ]
    cases = (
        (recognise_holes, recognise_hole_patterns, _bolt_circle_plate),
        (recognise_holes, recognise_hole_patterns, _linear_array_plate),
        (recognise_holes, recognise_hole_patterns, _grid_plate),
        (recognise_pockets, recognise_pocket_patterns, _pocket_array_plate),
        (recognise_pockets, recognise_pocket_patterns, _pocket_grid_plate),
        (recognise_slots, recognise_slot_patterns, _slot_array_plate),
        (recognise_slots, recognise_slot_patterns, _slot_grid_plate),
    )
    exercised = {HoleRecord}
    for recognise_members, recognise_patterns, build_part in cases:
        members = recognise_members(build_part())
        patterns = recognise_patterns(members)
        assert len(patterns) == 1, build_part.__name__
        pattern = patterns[0]
        exercised.add(type(pattern))
        converted.append(_DERIVED_CONVERTERS[type(pattern)](pattern, members))

    assert exercised == set(_DERIVED_CONVERTERS), (
        "derived converter corpus mismatch: "
        f"missing={sorted(t.__name__ for t in set(_DERIVED_CONVERTERS) - exercised)}"
    )
    provider_types = _recogniser_record_universe()
    assert all(type(feature) not in provider_types for feature in converted)
    assert all(isinstance(feature, Feature) for feature in converted)


def _rich_parts():
    """Parts spanning the feature families detect.py emits — holes, prismatic
    envelope, a turned profile (steps/boss), and a chamfer."""
    plate = Box(60, 40, 20) - Pos(0, 0, 10) * Cylinder(4, 20)
    shaft = Cylinder(8, 40) + Pos(0, 0, 25) * Cylinder(12, 10)
    return [plate, shaft]


def test_build_part_model_never_leaks_a_recognition_record():
    """The seam always lowers to the IR: no `build_part_model` output is a
    provider object; every feature satisfies the IR `Feature` shape."""
    provider_types = _recogniser_record_universe()
    for part in _rich_parts():
        model = build_part_model(part)
        assert model.features, "expected the drive part to yield features"
        for f in model.features:
            assert type(f) not in provider_types, (
                f"recognition-layer object leaked into IR: {type(f).__name__}"
            )
            # IR Feature shape (kind + frame + parameters), incl. the PMI/authored features.
            assert hasattr(f, "kind") and hasattr(f, "frame") and hasattr(f, "parameters")
            assert isinstance(f, Feature)
