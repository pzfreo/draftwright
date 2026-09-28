"""Compatibility and behavior at the extracted IR record boundary."""

import copy
import pickle
from dataclasses import FrozenInstanceError, replace

import pytest

from draftwright._geometry import _fmt
from draftwright.feature_identity import is_exact_envelope_feature
from draftwright.model import ir, ir_foundation

_RECORDS = (
    "TurnedProfileIdentity",
    "Feature",
    "AngularReference",
    "CylindricalReference",
    "CircularReference",
    "Frame",
    "Datum",
    "DimParameter",
    "ToleranceDecoration",
    "NominalRequirement",
    "ThreadRequirement",
    "ThreadOperation",
    "KnurlRequirement",
    "HoleFeature",
    "StepFeature",
    "PatternFeature",
    "EnvelopeFeature",
    "SlotFeature",
    "PocketFeature",
)


@pytest.mark.parametrize("name", _RECORDS)
def test_extracted_records_keep_the_public_identity_and_pickle_path(name):
    record = getattr(ir_foundation, name)
    assert getattr(ir, name) is record
    assert record.__module__ == "draftwright.model.ir"
    assert pickle.loads(pickle.dumps(record)) is record


def test_ir_keeps_the_original_formatting_helper_binding():
    assert ir._fmt is _fmt


def test_extracted_features_keep_positional_construction_and_value_semantics():
    frame = ir.Frame((0.0, 0.0, 0.0), "z")
    hole = ir.HoleFeature(frame, 8.0, 5.0, False, cbore=(12.0, 2.0))
    pattern = ir.PatternFeature(frame, "linear", 2, hole, pitch=15.0)
    pocket = ir.PocketFeature(frame, "x", "y", 10.0, 20.0, 3.0, 0.0, -10.0, 10.0)
    envelope = ir.EnvelopeFeature(frame, 30.0, 20.0, 10.0, (0, 0, 0), (30, 10, 20))
    step = ir.StepFeature(frame, 5.0, 10.0, ((0, 0, 0), (0, 0, 5)))
    slot = ir.SlotFeature(frame, "x", "y", 5.0, 12.0, 0.0, -6.0, 6.0)

    assert [p.parameter_id for p in hole.parameters()] == [
        "bore.diameter",
        "bore.depth",
        "counterbore.diameter",
        "counterbore.depth",
    ]
    assert [p.parameter_id for p in pattern.parameters()][-1] == "pitch.length"
    assert [p.parameter_id for p in pocket.parameters()] == [
        "pocket_width.length",
        "pocket_length.length",
        "pocket_depth.length",
    ]
    assert is_exact_envelope_feature(envelope)
    for value in (frame, hole, pattern, pocket, envelope, step, slot):
        assert copy.copy(value) == value
        assert copy.deepcopy(value) == value
        assert pickle.loads(pickle.dumps(value)) == value
    assert replace(hole, diameter=10.0).diameter == 10.0
    with pytest.raises(FrozenInstanceError):
        hole.diameter = 10.0


def test_reference_canonicalization_preserves_support_geometry():
    cylinder = ir.CylindricalReference.canonical(
        axis_point=(2, 0, 3),
        axis_direction=(0, 0, -2),
        radius=4,
        local_interval=(1, 5),
        sense="internal",
    )
    circle = ir.CircularReference.canonical(
        center=(2, 0, 3),
        normal=(0, 0, -2),
        radius=4,
    )
    assert cylinder.axis_origin == (2, 0, 0)
    assert cylinder.axis_direction == (0, 0, 1)
    assert cylinder.axial_interval == (-2, 2)
    assert cylinder.midpoint == (2, 0, 0)
    assert cylinder.principal_axis == circle.principal_axis == "Z"
    assert cylinder.diameter == circle.diameter == 8
    assert circle.normal == (0, 0, 1)
    angular = ir.AngularReference((0, 0, 0), (1, 0, 0), (0, 1, 0))
    for value in (cylinder, circle, angular):
        assert copy.copy(value) == pickle.loads(pickle.dumps(value)) == value
    assert (
        ir.CircularReference.canonical(
            center=(0, 0, 0),
            normal=(1, 1, 0),
            radius=1,
        ).principal_axis
        == "?"
    )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"axis_direction": (0, 0, 2)}, "unit length"),
        ({"axis_direction": (0, 0, -1)}, "positive dominant"),
        ({"axis_origin": (0, 0, 1)}, "perpendicular"),
        ({"axial_interval": (1, 1)}, "finite and increasing"),
        ({"radius": True}, "finite and positive"),
        ({"sense": "unknown"}, "sense must be"),
    ],
)
def test_cylindrical_reference_rejects_ambiguous_geometry(change, message):
    reference = ir.CylindricalReference((0, 0, 0), (0, 0, 1), 2, (0, 5), "internal")
    assert reference.principal_axis == "Z"
    with pytest.raises(ValueError, match=message):
        replace(reference, **change)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"normal": (0, 0, 2)}, "unit length"),
        ({"normal": (0, 0, -1)}, "positive dominant"),
        ({"radius": 0}, "finite and positive"),
    ],
)
def test_circular_reference_rejects_ambiguous_geometry(change, message):
    reference = ir.CircularReference((0, 0, 0), (0, 0, 1), 2)
    assert reference.principal_axis == "Z"
    with pytest.raises(ValueError, match=message):
        replace(reference, **change)


def test_manufacturing_requirements_require_finite_evidence_and_source_identity():
    cylinder = ir.CylindricalReference((0, 0, 0), (1, 0, 0), 1.5, (0, 10), "external")
    thread = ir.ThreadRequirement(
        "external",
        "M3 x 0.5-6g RH",
        3,
        0.5,
        "6g",
        "RH",
        "M3 x 0.5-6g RH",
        ("source:1",),
        "#1",
        ("aspect:1",),
        ("face:1",),
        (cylinder,),
    )
    knurl = ir.KnurlRequirement(
        "straight",
        1,
        True,
        "Straight knurl",
        ("source:2",),
        "#2",
        ("aspect:2",),
        ("face:2",),
        (cylinder,),
        processes=("cut",),
    )
    assert thread.callout_suffix == "M3 x 0.5-6g RH"
    assert "CUT PERMITTED" in knurl.callout_suffix
    for value in (thread, knurl):
        assert copy.deepcopy(value) == pickle.loads(pickle.dumps(value)) == value
    for value, change in (
        (thread, {"source_ids": ()}),
        (thread, {"cylindrical_refs": ()}),
        (thread, {"nominal_diameter": 0}),
        (thread, {"part21_id": ""}),
        (knurl, {"source_ids": ()}),
        (knurl, {"cylindrical_refs": ()}),
        (knurl, {"processes": ("cast",)}),
        (knurl, {"pitch": 0}),
    ):
        with pytest.raises(ValueError):
            replace(value, **change)


def test_thread_operation_normalizes_values_and_rejects_invalid_depth():
    operation = ir.ThreadOperation(" M3 x 0.5 ", 5)
    assert operation.designation == operation.callout_suffix == "M3 x 0.5"
    assert operation.depth == 5.0
    with pytest.raises(ValueError, match="depth must be finite and positive"):
        replace(operation, depth=0)
