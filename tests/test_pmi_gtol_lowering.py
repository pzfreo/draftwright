"""Geometric-tolerance modifier preservation and concept lowering (#1095)."""

from types import SimpleNamespace

import pytest
from build123d import Box

import draftwright.pmi as pmi_module
from draftwright.model import build_pmi_features
from draftwright.model.ir import ControlFrame, Frame, PmiFeature
from draftwright.pmi import PmiRecord
from draftwright.sheet_emit import _feature_block, _feature_line


def test_occt_geometric_tolerance_modifier_enum_is_fully_inventoried():
    from OCP.XCAFDimTolObjects import XCAFDimTolObjects_GeomToleranceModif as Modifier

    expected = {
        "Any_Cross_Section": "any_cross_section",
        "Common_Zone": "common_zone",
        "Each_Radial_Element": "each_radial_element",
        "Free_State": "free_state",
        "Least_Material_Requirement": "least_material_requirement",
        "Line_Element": "line_element",
        "Major_Diameter": "major_diameter",
        "Maximum_Material_Requirement": "maximum_material_requirement",
        "Minor_Diameter": "minor_diameter",
        "Not_Convex": "not_convex",
        "Pitch_Diameter": "pitch_diameter",
        "Reciprocity_Requirement": "reciprocity_requirement",
        "Separate_Requirement": "separate_requirement",
        "Statistical_Tolerance": "statistical_tolerance",
        "Tangent_Plane": "tangent_plane",
        "All_Around": "all_around",
        "All_Over": "all_over",
    }
    actual = {
        int(getattr(Modifier, f"XCAFDimTolObjects_GeomToleranceModif_{enum_name}")): name
        for enum_name, name in expected.items()
    }

    assert pmi_module._GTOL_MODIFIER == actual


def test_supported_scope_modifiers_are_complete():
    def modifiers(*codes):
        return SimpleNamespace(GetModifiers=lambda: codes)

    assert pmi_module._geometric_tolerance_modifiers(modifiers(15)) == (
        ("all_around",),
        (),
    )
    assert pmi_module._geometric_tolerance_modifiers(modifiers(16)) == (
        ("all_over",),
        (),
    )
    assert pmi_module._geometric_tolerance_modifiers(modifiers(1)) == (
        ("common_zone",),
        ("geometric-tolerance modifier 'common_zone' is not supported",),
    )
    assert pmi_module._geometric_tolerance_modifiers(modifiers(99)) == (
        ("unknown(99)",),
        ("geometric-tolerance modifier 99 is unknown",),
    )
    assert pmi_module._geometric_tolerance_modifiers(modifiers(15, 16)) == (
        ("all_around", "all_over"),
        ("geometric-tolerance modifier combination ('all_around', 'all_over') is not supported",),
    )

    def unreadable():
        raise RuntimeError("unreadable")

    assert pmi_module._geometric_tolerance_modifiers(SimpleNamespace(GetModifiers=unreadable)) == (
        (),
        ("geometric-tolerance modifiers are unavailable (RuntimeError: unreadable)",),
    )


def _record(*, value=0.5, modifiers=(), blockers=()) -> PmiRecord:
    return PmiRecord(
        kind="profile_surface",
        type_code=12,
        value=value,
        ref_pts=((0.0, 0.0, 0.0),),
        ref_bbox=(0.0, 0.0, 0.0, 0.0, 10.0, 10.0),
        dominant_axis="X",
        label="profile_surface 0.5",
        source_id="geometric_tolerance:test",
        datum_refs=("A",),
        part21_id="#27",
        source_category="geometric_tolerance",
        gtol_modifiers=modifiers,
        lowering_blockers=blockers,
    )


def test_complete_geometric_tolerance_lowers_to_control_frame():
    (feature,) = build_pmi_features(
        (_record(modifiers=("all_around",)),), Box(20, 20, 20).bounding_box()
    )

    assert isinstance(feature, ControlFrame)
    assert feature.characteristic == "profile_surface"
    assert feature.tolerance == "0.5"
    assert (feature.view, feature.side) == ("side", "below")
    assert feature.datums == ("A",)
    assert feature.all_around is True
    assert feature.source_id == "geometric_tolerance:test"
    assert feature.part21_id == "#27"
    assert isinstance(feature.origin, PmiFeature)
    assert feature.origin.gtol_modifiers == ("all_around",)


def test_complete_all_over_tolerance_lowers_to_control_frame():
    (feature,) = build_pmi_features(
        (_record(modifiers=("all_over",)),), Box(20, 20, 20).bounding_box()
    )

    assert isinstance(feature, ControlFrame)
    assert feature.all_around is False
    assert feature.all_over is True
    assert isinstance(feature.origin, PmiFeature)
    assert feature.origin.gtol_modifiers == ("all_over",)


def test_lowering_does_not_round_the_source_tolerance_magnitude():
    (feature,) = build_pmi_features((_record(value=0.12345),), Box(20, 20, 20).bounding_box())

    assert isinstance(feature, ControlFrame)
    assert feature.tolerance == "0.12345"


@pytest.mark.parametrize("modifier", ["common_zone"])
def test_incomplete_geometric_tolerance_remains_a_provenance_rich_raw_fallback(modifier):
    blocker = f"geometric-tolerance modifier {modifier!r} is not supported"
    (feature,) = build_pmi_features(
        (_record(modifiers=(modifier,), blockers=(blocker,)),),
        Box(20, 20, 20).bounding_box(),
    )

    assert isinstance(feature, PmiFeature)
    assert feature.gtol_modifiers == (modifier,)
    assert feature.lowering_blockers == (blocker,)
    assert feature.source_id == "geometric_tolerance:test"
    assert feature.part21_id == "#27"


def _execute_feature_line(feature):
    captured = []
    sheet = SimpleNamespace(add=captured.append)
    exec(
        _feature_line(feature),
        {"sheet": sheet, "ControlFrame": ControlFrame, "Frame": Frame, "PmiFeature": PmiFeature},
    )
    (restored,) = captured
    return restored


def test_generated_sheet_line_round_trips_imported_control_frame():
    (feature,) = build_pmi_features(
        (_record(modifiers=("all_around",)),), Box(20, 20, 20).bounding_box()
    )

    restored = _execute_feature_line(feature)

    assert isinstance(restored, ControlFrame)
    assert restored.all_around is True
    assert restored.source_id == "geometric_tolerance:test"
    assert restored.part21_id == "#27"
    assert isinstance(restored.origin, PmiFeature)
    assert restored.origin.source_category == "geometric_tolerance"
    assert restored.origin.gtol_modifiers == ("all_around",)
    assert restored.origin.lowering_blockers == ()
    assert restored.origin.source_id == restored.source_id
    assert restored.origin.part21_id == restored.part21_id


def test_generated_sheet_line_round_trips_imported_all_over_control_frame():
    (feature,) = build_pmi_features(
        (_record(modifiers=("all_over",)),), Box(20, 20, 20).bounding_box()
    )

    restored = _execute_feature_line(feature)

    assert isinstance(restored, ControlFrame)
    assert restored.all_around is False
    assert restored.all_over is True
    assert isinstance(restored.origin, PmiFeature)
    assert restored.origin.gtol_modifiers == ("all_over",)


def test_generated_sheet_line_round_trips_control_frame_zone_fields():
    feature = ControlFrame(
        frame=Frame((1.0, 2.0, 3.0), "z"),
        characteristic="position",
        tolerance="0.0125",
        view="plan",
        side="above",
        diameter=True,
        modifier="M",
    )

    restored = _execute_feature_line(feature)

    assert restored.diameter is True
    assert restored.modifier == "M"
    assert restored.datums == ()
    assert restored.all_around is False
    assert restored.all_over is False
    assert restored.source_id == ""
    assert restored.part21_id == ""
    assert restored.origin is None


def test_generated_sheet_refuses_a_control_frame_with_an_unbound_feature_origin():
    feature = ControlFrame(
        frame=Frame((1.0, 2.0, 3.0), "z"),
        characteristic="position",
        tolerance="0.1",
        view="plan",
        side="above",
        origin=SimpleNamespace(kind="hole"),
    )

    with pytest.raises(ValueError, match="origin has no emitted binding"):
        _feature_block([feature])


@pytest.mark.parametrize("modifier", ["common_zone"])
def test_generated_sheet_line_round_trips_raw_modifier_fallback(modifier):
    blocker = f"geometric-tolerance modifier {modifier!r} is not supported"
    (feature,) = build_pmi_features(
        (_record(modifiers=(modifier,), blockers=(blocker,)),),
        Box(20, 20, 20).bounding_box(),
    )

    restored = _execute_feature_line(feature)

    assert isinstance(restored, PmiFeature)
    assert restored.source_category == "geometric_tolerance"
    assert restored.gtol_modifiers == (modifier,)
    assert restored.lowering_blockers == (blocker,)
    assert restored.source_id == "geometric_tolerance:test"
    assert restored.part21_id == "#27"
