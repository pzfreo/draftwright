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

import pytest
from build123d import Box, Cylinder, Pos

# The authoritative universe of recogniser record types is maintained by the
# recogniser-contract suite; reusing it ties the two guards together — a new
# recogniser must be added there (which forces it to be exercised) *and* placed in
# exactly one registry tier here, or one of the two suites fails.
from test_recogniser_contract import _EXPECTED_RECORD_TYPES

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


def test_registry_tiers_partition_every_record_type():
    """Every recognition record type has exactly one home (completeness + uniqueness)."""
    tiers = [set(_CONVERTERS), set(_DERIVED_CONVERTERS), set(_ORCHESTRATED_RECORDS)]
    homed = tiers[0] | tiers[1] | tiers[2]

    missing = _EXPECTED_RECORD_TYPES - homed
    assert not missing, (
        f"record types with no converter/home: {sorted(t.__name__ for t in missing)}"
    )

    extra = homed - _EXPECTED_RECORD_TYPES
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
