"""Geometric-tolerance modifier preservation and concept lowering (#1095)."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from build123d import Box, Draft
from build123d_drafting import FeatureControlFrame

import draftwright.pmi as pmi_module
from draftwright.annotations._gdt import _gdt_glyph, _gdt_pdf_text_specs
from draftwright.builder import build_drawing, detect_part_model
from draftwright.linting.pmi_coverage import lint_pmi_rendering
from draftwright.model import build_pmi_features
from draftwright.model.ir import ControlFrame, Frame, PmiFeature
from draftwright.pmi import PmiExtractionReport, PmiRecord
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


def test_repeated_datum_targets_lower_to_one_ordered_compartment():
    record = replace(_record(), datum_refs=("A", "A", "A", "B", "B", "C"))

    (feature,) = build_pmi_features((record,), Box(20, 20, 20).bounding_box())

    assert isinstance(feature, ControlFrame)
    assert feature.datums == ("A", "B", "C")


def test_imported_control_frame_keeps_source_magnitude_precision():
    record = replace(_record(), value=0.254000000000003)

    (feature,) = build_pmi_features((record,), Box(20, 20, 20).bounding_box())

    assert isinstance(feature, ControlFrame)
    assert feature.tolerance == "0.254000000000003"
    assert feature.display_tolerance == "0.254"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.254000000000003, "0.254"),
        (0.050000000000003, "0.05"),
        (0.05, "0.05"),
        (0.00000000000001, "0.00000000000001"),
    ],
)
def test_xcaf_display_fallback_has_bounded_significant_digits(value, expected):
    from decimal import Decimal

    from draftwright._geometry import _fmt_pmi_magnitude

    text = _fmt_pmi_magnitude(value)
    assert text == expected
    source = Decimal(str(value))
    assert abs(Decimal(text) - source) <= Decimal("0.5").scaleb(source.adjusted() - 12)
    assert _fmt_pmi_magnitude(value, 3) == f"{source:.3f}"


def test_flatness_source_magnitude_reaches_ink_and_lint_detects_a_wrong_value():
    part = Box(20, 20, 20)
    record = replace(
        _record(value=0.05),
        kind="flatness",
        type_code=7,
        label="flatness 0.05",
        ref_pts=((0.0, 0.0, 10.0),),
        ref_bbox=(-10.0, -10.0, 10.0, 10.0, 10.0, 10.0),
        dominant_axis="Z",
    )
    assert record.value == 0.05  # the source contains the value before lowering
    (feature,) = build_pmi_features((record,), part.bounding_box())
    assert isinstance(feature, ControlFrame)
    assert feature.display_tolerance == "0.05"

    model = detect_part_model(part)
    model.features.append(feature)
    drawing = build_drawing(part, model=model)
    (name,) = (
        name
        for name in drawing.registry.names()
        if drawing.registry.declaration_of(name) is feature
    )
    annotation = drawing.registry.named(name)
    assert annotation.pdf_text_relative_specs[0][0] == "0.05"
    report = PmiExtractionReport(records=(record,))
    assert lint_pmi_rendering(model.features, drawing.registry, "annotate", report=report) == []

    # The independent source may carry binary transfer noise; the printed 0.05
    # remains within the documented 13-digit interval, but a value just beyond it
    # must be reported even though both would round to 0.05 at two decimals.
    noisy = replace(record, value=0.050000000000003)
    assert (
        lint_pmi_rendering(
            model.features,
            drawing.registry,
            "annotate",
            report=PmiExtractionReport(records=(noisy,)),
        )
        == []
    )
    outside = replace(record, value=0.05000000000001)
    assert [
        issue.code
        for issue in lint_pmi_rendering(
            model.features,
            drawing.registry,
            "annotate",
            report=PmiExtractionReport(records=(outside,)),
        )
    ] == ["pmi_value_mismatch"]

    annotation.pdf_text_relative_specs = (
        ("0.1", *annotation.pdf_text_relative_specs[0][1:]),
        *annotation.pdf_text_relative_specs[1:],
    )
    assert annotation.pdf_text_relative_specs[0][0] == "0.1"
    issues = lint_pmi_rendering(model.features, drawing.registry, "annotate", report=report)
    assert [(issue.code, issue.source_ids, issue.annotation_name) for issue in issues] == [
        ("pmi_value_mismatch", (record.source_id,), name)
    ]


def test_complete_all_over_tolerance_lowers_to_control_frame():
    (feature,) = build_pmi_features(
        (_record(modifiers=("all_over",)),), Box(20, 20, 20).bounding_box()
    )

    assert isinstance(feature, ControlFrame)
    assert feature.all_around is False
    assert feature.all_over is True
    assert isinstance(feature.origin, PmiFeature)
    assert feature.origin.gtol_modifiers == ("all_over",)


@pytest.mark.parametrize(
    ("modifiers", "diameter", "material"),
    [
        (("diameter_zone",), True, None),
        (("diameter_zone", "maximum_material_requirement"), True, "M"),
        (("least_material_requirement",), False, "L"),
    ],
)
def test_zone_and_material_qualifiers_lower_to_control_frame(modifiers, diameter, material):
    (feature,) = build_pmi_features(
        (_record(modifiers=modifiers),), Box(20, 20, 20).bounding_box()
    )

    assert isinstance(feature, ControlFrame)
    assert feature.diameter is diameter
    assert feature.modifier == material
    assert isinstance(feature.origin, PmiFeature)
    assert feature.origin.gtol_modifiers == modifiers


def test_generated_sheet_round_trips_imported_zone_and_material_qualifiers():
    modifiers = ("diameter_zone", "maximum_material_requirement")
    (feature,) = build_pmi_features(
        (_record(modifiers=modifiers),), Box(20, 20, 20).bounding_box()
    )

    restored = _execute_feature_line(feature)

    assert isinstance(restored, ControlFrame)
    assert restored.diameter is True
    assert restored.modifier == "M"
    assert restored.display_tolerance == feature.display_tolerance == "0.5"
    assert isinstance(restored.origin, PmiFeature)
    assert restored.origin.gtol_modifiers == modifiers


def test_spherical_diameter_zone_is_drawn_with_numeric_source_check_issue_2156(tmp_path):
    import pypdfium2 as pdfium

    part = Box(20, 20, 20)
    record = replace(
        _record(modifiers=("spherical_diameter_zone",)),
        datum_refs=(),
        ref_pts=((0.0, 0.0, 10.0),),
        ref_bbox=(-10.0, -10.0, 10.0, 10.0, 10.0, 10.0),
        dominant_axis="Z",
    )
    (frame,) = build_pmi_features((record,), part.bounding_box())
    assert isinstance(frame, ControlFrame)
    assert frame.spherical_diameter and not frame.diameter
    assert frame.display_tolerance == "0.5"
    with pytest.raises(ValueError, match="both diametral and spherical"):
        replace(frame, diameter=True)

    draft = Draft(font_size=3.0)
    glyph = _gdt_glyph(frame, draft)
    assert isinstance(glyph, FeatureControlFrame)
    assert glyph.tolerance_str == "Sø0.5"
    assert tuple(spec[0] for spec in _gdt_pdf_text_specs(glyph, frame, draft))[:2] == (
        "Sø",
        "0.5",
    )

    restored = _execute_feature_line(frame)
    assert isinstance(restored, ControlFrame)
    assert restored.spherical_diameter and restored.origin.gtol_modifiers == record.gtol_modifiers
    replay_glyph = _gdt_glyph(restored, draft)
    assert replay_glyph.tolerance_str == glyph.tolerance_str
    assert tuple(spec[0] for spec in _gdt_pdf_text_specs(replay_glyph, restored, draft))[:2] == (
        "Sø",
        "0.5",
    )

    model = detect_part_model(part)
    model.features.append(frame)
    drawing = build_drawing(part, model=model)
    (name,) = (
        name for name in drawing.registry.names() if drawing.registry.declaration_of(name) is frame
    )
    annotation = drawing.registry.named(name)
    assert tuple(spec[0] for spec in annotation.pdf_text_relative_specs)[:2] == ("Sø", "0.5")
    pdf_path = drawing.export(str(tmp_path / "spherical-zone"), formats=("pdf",))["pdf"]
    pdf = pdfium.PdfDocument(pdf_path)
    try:
        page = pdf[0]
        text_page = page.get_textpage()
        try:
            text = text_page.get_text_range()
            assert "Sø" in text and "0.5" in text
        finally:
            text_page.close()
    finally:
        pdf.close()
    report = PmiExtractionReport(records=(record,))
    assert lint_pmi_rendering(model.features, drawing.registry, "annotate", report=report) == []
    annotation.pdf_text_relative_specs = (
        *annotation.pdf_text_relative_specs[:1],
        ("0.6", *annotation.pdf_text_relative_specs[1][1:]),
        *annotation.pdf_text_relative_specs[2:],
    )
    assert [
        issue.code
        for issue in lint_pmi_rendering(
            model.features, drawing.registry, "annotate", report=report
        )
    ] == ["pmi_value_mismatch"]


def test_xcaf_diametral_position_survives_glyph_pdf_and_sheet_issue_2156():
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPCAFControl import STEPCAFControl_Reader
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDF import TDF_LabelSequence
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_GeomTolerance

    step = Path(__file__).parent / "fixtures/nist_ctc_03_asme1_ap242.stp"
    reader = STEPCAFControl_Reader()
    reader.SetGDTMode(True)
    reader.SetNameMode(True)
    assert reader.ReadFile(str(step)) == IFSelect_RetDone
    document = TDocStd_Document(TCollection_ExtendedString("XCAF"))
    assert reader.Transfer(document)
    labels = TDF_LabelSequence()
    XCAFDoc_DocumentTool.DimTolTool_s(document.Main()).GetGeomToleranceLabels(labels)
    raw_positions = []
    for index in range(1, labels.Length() + 1):
        label = labels.Value(index)
        tolerance = XCAFDoc_GeomTolerance.Set_s(label).GetObject()
        if int(tolerance.GetTypeOfValue()) == 1 and int(tolerance.GetType()) == 10:
            raw_positions.append(label)
    assert raw_positions, "the fixture must contain an XCAF diameter-zone position"
    source_id = pmi_module._source_id("geometric_tolerance", raw_positions[0])

    report = pmi_module.extract_pmi_report(step)
    (source,) = [source for source in report.sources if source.source_id == source_id]
    (record,) = [record for record in report.records if record.source_id == source_id]
    assert source.outcome == "extracted" and record.lowering_blockers == ()
    assert record.kind == "position" and "diameter_zone" in record.gtol_modifiers

    (frame,) = build_pmi_features((record,), Box(100, 100, 100).bounding_box())
    assert isinstance(frame, ControlFrame)
    assert frame.diameter is True and frame.source_id == source_id
    draft = Draft(font_size=3.0)
    glyph = _gdt_glyph(frame, draft)
    assert isinstance(glyph, FeatureControlFrame)
    assert glyph.tolerance_str == frame.display_tolerance
    plain_glyph = FeatureControlFrame(
        frame.characteristic,
        frame.display_tolerance,
        datums=frame.datums,
        draft=draft,
        modifier=frame.modifier,
    )
    assert glyph.bounding_box().size.X > plain_glyph.bounding_box().size.X + 1.0
    assert tuple(spec[0] for spec in _gdt_pdf_text_specs(glyph, frame, draft))[:2] == (
        "ø",
        frame.display_tolerance,
    )

    restored = _execute_feature_line(frame)
    assert isinstance(restored, ControlFrame)
    assert restored.diameter is True and restored.source_id == source_id
    assert restored.origin.gtol_modifiers == record.gtol_modifiers
    replay_glyph = _gdt_glyph(restored, draft)
    assert replay_glyph.bounding_box().size.X == pytest.approx(glyph.bounding_box().size.X)
    assert tuple(spec[0] for spec in _gdt_pdf_text_specs(replay_glyph, restored, draft))[:2] == (
        "ø",
        frame.display_tolerance,
    )


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
