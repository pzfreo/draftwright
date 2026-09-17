"""Structured Part21 facts used to complete XCAF geometric tolerances."""

from dataclasses import replace
from pathlib import Path

import pytest

from draftwright._pmi_part21 import (
    CommonLabelFact,
    DatumDefinitionFact,
    DatumOccurrenceFact,
    GeometricToleranceFact,
    ManufacturingRequirementFact,
    SurfaceLabelFact,
    match_datum_occurrence,
    match_dimension_display,
    match_geometric_tolerance,
    read_common_labels,
    read_datum_definitions,
    read_datum_occurrences,
    read_dimension_display_facts,
    read_dimension_length_factor,
    read_geometric_tolerances,
    read_manufacturing_requirements,
    read_surface_labels,
)

CTC01 = Path(__file__).parent / "fixtures" / "nist_ctc_01_asme1_ap242.stp"
CTC03 = Path(__file__).parent / "fixtures" / "nist_ctc_03_asme1_ap242.stp"
CTC04 = Path(__file__).parent / "fixtures" / "nist_ctc_04_asme1_ap242.stp"
CTC05 = Path(__file__).parent / "fixtures" / "nist_ctc_05_asme1_ap242.stp"


def _step(*instances: str) -> str:
    return "\n".join(
        (
            "ISO-10303-21;",
            "HEADER;",
            "FILE_DESCRIPTION(('test'),'2;1');",
            "FILE_NAME('test.step','',(''),(''),'','','');",
            "FILE_SCHEMA(('AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF'));",
            "ENDSEC;",
            "DATA;",
            *instances,
            "ENDSEC;",
            "END-ISO-10303-21;",
        )
    )


def _read(tmp_path, name: str, *instances: str):
    step = tmp_path / f"{name}.step"
    step.write_text(_step(*instances), encoding="utf-8")
    return read_geometric_tolerances(step)


def _read_datums(tmp_path, name: str, *instances: str):
    step = tmp_path / f"{name}.step"
    step.write_text(_step(*instances), encoding="utf-8")
    return read_datum_occurrences(step)


def _read_datum_definitions(tmp_path, name: str, *instances: str):
    step = tmp_path / f"{name}.step"
    step.write_text(_step(*instances), encoding="utf-8")
    return read_datum_definitions(step)


def _read_dimension_display_facts(tmp_path, name: str, *instances: str):
    step = tmp_path / f"{name}.step"
    step.write_text(_step(*instances), encoding="utf-8")
    return read_dimension_display_facts(step)


def _read_requirements(tmp_path, name: str, *instances: str):
    step = tmp_path / f"{name}.step"
    step.write_text(_step(*instances), encoding="utf-8")
    return read_manufacturing_requirements(step)


def _read_surface_labels(tmp_path, name: str, *instances: str):
    step = tmp_path / f"{name}.step"
    step.write_text(_step(*instances), encoding="utf-8")
    return read_surface_labels(step)


def _read_common_labels(tmp_path, name: str, *instances: str):
    step = tmp_path / f"{name}.step"
    step.write_text(_step(*instances), encoding="utf-8")
    return read_common_labels(step)


def test_part21_read_session_reuses_one_parse_and_does_not_leak(tmp_path, monkeypatch):
    import draftwright._pmi_part21 as part21

    step = tmp_path / "empty.step"
    step.write_text(_step(), encoding="utf-8")
    original = part21.p21.readfile
    calls = []

    def counted(path):
        calls.append(path)
        return original(path)

    monkeypatch.setattr(part21.p21, "readfile", counted)
    with part21.part21_read_session():
        assert read_geometric_tolerances(step) == ()
        assert read_datum_definitions(step) == ()
        assert read_surface_labels(step) == ()
    assert read_geometric_tolerances(step) == ()

    assert calls == [step, step]


def test_ctc01_geometric_tolerance_facts_are_exact():
    facts = read_geometric_tolerances(CTC01)

    assert [
        (fact.entity_id, fact.semantic_name, fact.kind, fact.value_mm, fact.reason)
        for fact in facts
    ] == [
        ("#21", "Position.1", "position", 0.75, ""),
        ("#22", "Position.2", "position", 0.75, ""),
        ("#26", "Position surfacic profile.3", "profile_surface", 1.25, ""),
        ("#27", "Position surfacic profile.2", "profile_surface", 0.5, ""),
        ("#56", "Perpendicularity.1", "perpendicularity", 1.5, ""),
        ("#57", "Flatness.1", "flatness", 0.2, ""),
    ]


def test_ctc01_datum_occurrences_preserve_context_and_feature_identity():
    facts = read_datum_occurrences(CTC01)

    assert len(facts) == 11
    assert [
        (
            fact.tolerance_id,
            fact.tolerance_name,
            fact.tolerance_kind,
            fact.datum_feature_id,
            fact.datum_id,
            fact.letter,
            fact.reference_item_ids,
            fact.reason,
        )
        for fact in facts
    ] == [
        ("#21", "Position.1", "position", "#34", "#37", "A", ("#861",), ""),
        ("#21", "Position.1", "position", "#35", "#38", "B", ("#854", "#853"), ""),
        ("#21", "Position.1", "position", "#36", "#39", "C", ("#839", "#840"), ""),
        ("#22", "Position.2", "position", "#34", "#37", "A", ("#861",), ""),
        ("#22", "Position.2", "position", "#35", "#38", "B", ("#854", "#853"), ""),
        ("#22", "Position.2", "position", "#36", "#39", "C", ("#839", "#840"), ""),
        (
            "#26",
            "Position surfacic profile.3",
            "profile_surface",
            "#34",
            "#37",
            "A",
            ("#861",),
            "",
        ),
        (
            "#26",
            "Position surfacic profile.3",
            "profile_surface",
            "#35",
            "#38",
            "B",
            ("#854", "#853"),
            "",
        ),
        (
            "#26",
            "Position surfacic profile.3",
            "profile_surface",
            "#36",
            "#39",
            "C",
            ("#839", "#840"),
            "",
        ),
        (
            "#27",
            "Position surfacic profile.2",
            "profile_surface",
            "#34",
            "#37",
            "A",
            ("#861",),
            "",
        ),
        (
            "#56",
            "Perpendicularity.1",
            "perpendicularity",
            "#34",
            "#37",
            "A",
            ("#861",),
            "",
        ),
    ]
    assert {fact.datum_feature_id for fact in facts} == {"#34", "#35", "#36"}


def test_ctc01_has_no_custom_manufacturing_requirement_properties():
    assert read_manufacturing_requirements(CTC01) == ()


def test_ctc01_surface_labels_preserve_text_and_exact_geometry_chain():
    assert read_surface_labels(CTC01) == (
        SurfaceLabelFact(
            entity_id="#4340",
            text="B",
            shape_aspect_id="#316",
            representation_id="#4325",
            descriptive_item_id="#4349",
            callout_ids=("#621",),
            reference_item_ids=("#1850",),
        ),
        SurfaceLabelFact(
            entity_id="#4341",
            text="A",
            shape_aspect_id="#317",
            representation_id="#4326",
            descriptive_item_id="#4350",
            callout_ids=("#622",),
            reference_item_ids=("#1844",),
        ),
    )


def test_ctc04_common_labels_preserve_each_occurrence_and_exact_geometry_chain():
    facts = read_common_labels(CTC04)

    assert [
        (
            fact.entity_id,
            fact.presentation_name,
            fact.text,
            fact.shape_aspect_id,
            fact.reference_item_ids,
            fact.reason,
        )
        for fact in facts
    ] == [
        ("#20358", "Text.12", "A", "#18302", ("#12933",), ""),
        (
            "#20386",
            "Text.13",
            "⌴",
            "#19419",
            ("#6118", "#6100", "#6150", "#6168", "#5372", "#5390", "#5422", "#5440"),
            "",
        ),
        ("#20414", "Text.14", "C", "#18382", ("#832", "#856"), ""),
        ("#20442", "Text.15", "B", "#18341", ("#8212", "#8194"), ""),
    ]


def test_common_labels_do_not_collapse_equal_text_or_composite_members(tmp_path):
    facts = _read_common_labels(
        tmp_path,
        "common-label-multiplicity",
        "#1=SHAPE_ASPECT('group','feature group',#99,.T.);",
        "#2=SHAPE_ASPECT('member 1','',#99,.T.);",
        "#3=SHAPE_ASPECT('member 2','',#99,.T.);",
        "#4=SHAPE_ASPECT_RELATIONSHIP('','',#1,#2);",
        "#5=SHAPE_ASPECT_RELATIONSHIP('','',#1,#3);",
        "#6=GEOMETRIC_ITEM_SPECIFIC_USAGE('','',#2,#98,(#90));",
        "#7=GEOMETRIC_ITEM_SPECIFIC_USAGE('','',#3,#98,(#91));",
        "#10=PROPERTY_DEFINITION('semantic text','',#1);",
        "#11=DESCRIPTIVE_REPRESENTATION_ITEM('Text.1','4X');",
        "#12=REPRESENTATION('',(#11),#97);",
        "#13=PROPERTY_DEFINITION_REPRESENTATION(#10,#12);",
        "#14=DRAUGHTING_CALLOUT('Text.1',());",
        "#15=DRAUGHTING_MODEL_ITEM_ASSOCIATION('','',#10,#96,#14);",
        "#16=DRAUGHTING_MODEL_ITEM_ASSOCIATION('','',#1,#96,#14);",
        "#20=PROPERTY_DEFINITION('semantic text','',#2);",
        "#21=DESCRIPTIVE_REPRESENTATION_ITEM('Text.2','4X');",
        "#22=REPRESENTATION('',(#21),#97);",
        "#23=PROPERTY_DEFINITION_REPRESENTATION(#20,#22);",
        "#24=DRAUGHTING_CALLOUT('Text.2',());",
        "#25=DRAUGHTING_MODEL_ITEM_ASSOCIATION('','',#20,#96,#24);",
        "#26=DRAUGHTING_MODEL_ITEM_ASSOCIATION('','',#2,#96,#24);",
    )

    assert [(fact.entity_id, fact.text) for fact in facts] == [("#10", "4X"), ("#20", "4X")]
    assert facts[0].reference_item_ids == ("#90", "#91")
    assert facts[1].reference_item_ids == ("#90",)
    assert all(not fact.reason for fact in facts)


def test_incomplete_common_label_keeps_one_explicit_reason_set(tmp_path):
    (fact,) = _read_common_labels(
        tmp_path,
        "incomplete-common-label",
        "#1=SHAPE_ASPECT('feature','',#99,.T.);",
        "#2=PROPERTY_DEFINITION('semantic text','',#1);",
        "#3=REPRESENTATION('',(),#98);",
        "#4=PROPERTY_DEFINITION_REPRESENTATION(#2,#3);",
    )

    assert fact == CommonLabelFact(
        entity_id="#2",
        presentation_name="",
        text="",
        shape_aspect_id="#1",
        representation_id="#3",
        reason=(
            "linked representation has 0 descriptive items; "
            "common label has no shared semantic/presentation callout; "
            "common-label shape aspect has no representation items"
        ),
    )


def test_semantic_text_for_a_non_shape_definition_is_not_a_common_label(tmp_path):
    assert (
        _read_common_labels(
            tmp_path,
            "non-shape-semantic-text",
            "#1=PRODUCT_DEFINITION('part','',#98,#97);",
            "#2=PROPERTY_DEFINITION('semantic text','',#1);",
            "#3=DESCRIPTIVE_REPRESENTATION_ITEM('Text.1','label');",
            "#4=REPRESENTATION('',(#3),#96);",
            "#5=PROPERTY_DEFINITION_REPRESENTATION(#2,#4);",
        )
        == ()
    )


def test_surface_label_missing_associations_remain_explicit(tmp_path):
    (fact,) = _read_surface_labels(
        tmp_path,
        "incomplete-surface-label",
        "#1=SHAPE_ASPECT('','NOTE',#99,.F.);",
        "#2=PROPERTY_DEFINITION('',$,#1);",
        "#3=REPRESENTATION('',(),#98);",
        "#4=PROPERTY_DEFINITION_REPRESENTATION(#2,#3);",
    )

    assert fact == SurfaceLabelFact(
        entity_id="#2",
        text="",
        shape_aspect_id="#1",
        representation_id="#3",
        reason=(
            "linked representation has 0 descriptive items; "
            "surface label has no shared semantic/presentation callout; "
            "surface label shape aspect has no representation items"
        ),
    )


def test_manufacturing_requirement_preserves_authoritative_text_and_geometry_chain(tmp_path):
    facts = _read_requirements(
        tmp_path,
        "associated-requirement",
        "#1=DESCRIPTIVE_REPRESENTATION_ITEM('knurl','Straight knurl, 1.0 mm pitch');",
        "#2=REPRESENTATION('knurl requirement',(#1),#99);",
        "#3=PROPERTY_DEFINITION('manufacturing requirement','knurl',#98);",
        "#4=PROPERTY_DEFINITION_REPRESENTATION(#3,#2);",
        "#5=SHAPE_ASPECT('KNURL, 1.0 PITCH','',#98,.T.);",
        "#6=GEOMETRIC_ITEM_SPECIFIC_USAGE('KNURL, 1.0 PITCH','',#5,#97,#96);",
        "#7=DRAUGHTING_MODEL_ITEM_ASSOCIATION('semantic link','',#5,#95,#8);",
        "#8=DRAUGHTING_CALLOUT('Straight knurl requirement',(#94));",
    )

    assert facts == (
        ManufacturingRequirementFact(
            entity_id="#3",
            semantic_name="knurl",
            text="Straight knurl, 1.0 mm pitch",
            representation_id="#2",
            descriptive_item_id="#1",
            callout_ids=("#8",),
            shape_aspect_ids=("#5",),
            reference_item_ids=("#96",),
        ),
    )


def test_escaped_part21_requirement_category_cannot_bypass_the_inventory_guard(tmp_path):
    encoded_category = (
        r"\X2\006D0061006E00750066006100630074007500720069006E006700200072006500710075006900720065006D0065006E0074\X0"
        + "\\"
    )
    facts = _read_requirements(
        tmp_path,
        "encoded-requirement-category",
        "#1=DESCRIPTIVE_REPRESENTATION_ITEM('thread','M3 x 0.5-6g RH');",
        "#2=REPRESENTATION('external thread requirement',(#1),#99);",
        f"#3=PROPERTY_DEFINITION('{encoded_category}','external thread',#98);",
        "#4=PROPERTY_DEFINITION_REPRESENTATION(#3,#2);",
    )

    assert [(fact.entity_id, fact.semantic_name, fact.text) for fact in facts] == [
        ("#3", "external thread", "M3 x 0.5-6g RH")
    ]


def test_comment_between_entity_name_and_parameters_cannot_bypass_inventory_guard(tmp_path):
    facts = _read_requirements(
        tmp_path,
        "commented-property-definition",
        "#1=DESCRIPTIVE_REPRESENTATION_ITEM('thread','M3 x 0.5-6g RH');",
        "#2=REPRESENTATION('external thread requirement',(#1),#99);",
        "#3=PROPERTY_DEFINITION/* valid Part 21 comment */('manufacturing requirement','external thread',#98);",
        "#4=PROPERTY_DEFINITION_REPRESENTATION(#3,#2);",
    )

    assert [(fact.entity_id, fact.semantic_name, fact.text) for fact in facts] == [
        ("#3", "external thread", "M3 x 0.5-6g RH")
    ]


def test_unassociated_general_requirement_remains_authoritative_source_intent(tmp_path):
    facts = _read_requirements(
        tmp_path,
        "general-requirement",
        "#1=DESCRIPTIVE_REPRESENTATION_ITEM('general tolerances','ISO 2768-m');",
        "#2=REPRESENTATION('general tolerances requirement',(#1),#99);",
        "#3=PROPERTY_DEFINITION('manufacturing requirement','general tolerances',#98);",
        "#4=PROPERTY_DEFINITION_REPRESENTATION(#3,#2);",
    )

    assert facts == (
        ManufacturingRequirementFact(
            entity_id="#3",
            semantic_name="general tolerances",
            text="ISO 2768-m",
            representation_id="#2",
            descriptive_item_id="#1",
        ),
    )


def test_each_matched_callout_and_shape_aspect_must_have_a_complete_association(tmp_path):
    (fact,) = _read_requirements(
        tmp_path,
        "partially-associated-requirement",
        "#1=DESCRIPTIVE_REPRESENTATION_ITEM('knurl','Straight knurl');",
        "#2=REPRESENTATION('knurl requirement',(#1),#99);",
        "#3=PROPERTY_DEFINITION('manufacturing requirement','knurl',#98);",
        "#4=PROPERTY_DEFINITION_REPRESENTATION(#3,#2);",
        "#5=SHAPE_ASPECT('first','',#98,.T.);",
        "#6=GEOMETRIC_ITEM_SPECIFIC_USAGE('first','',#5,#97,#96);",
        "#7=DRAUGHTING_MODEL_ITEM_ASSOCIATION('first','',#5,#95,#8);",
        "#8=DRAUGHTING_CALLOUT('Head knurl requirement',(#94));",
        "#9=DRAUGHTING_CALLOUT('Shaft knurl requirement',(#93));",
        "#10=DRAUGHTING_MODEL_ITEM_ASSOCIATION('third','',#12,#95,#11);",
        "#11=DRAUGHTING_CALLOUT('Backup knurl requirement',(#92));",
        "#12=SHAPE_ASPECT('third','',#98,.T.);",
    )

    assert fact.callout_ids == ("#8", "#9", "#11")
    assert fact.shape_aspect_ids == ("#5", "#12")
    assert fact.reference_item_ids == ("#96",)
    assert fact.reason == (
        "matched semantic callout(s) have no shape-aspect association: #9; "
        "associated shape aspect(s) have no representation items: #12"
    )


def test_dangling_representation_keeps_its_identity_without_hiding_valid_siblings(tmp_path):
    facts = _read_requirements(
        tmp_path,
        "dangling-representation",
        "#1=DESCRIPTIVE_REPRESENTATION_ITEM('thread','M3 x 0.5-6g RH');",
        "#2=REPRESENTATION('external thread requirement',(#1),#99);",
        "#3=PROPERTY_DEFINITION('manufacturing requirement','external thread',#98);",
        "#4=PROPERTY_DEFINITION_REPRESENTATION(#3,#2);",
        "#5=PROPERTY_DEFINITION('manufacturing requirement','knurl',#98);",
        "#6=PROPERTY_DEFINITION_REPRESENTATION(#5,#404);",
    )

    assert [(fact.entity_id, fact.text) for fact in facts] == [
        ("#3", "M3 x 0.5-6g RH"),
        ("#5", ""),
    ]
    assert facts[1].reason == (
        "linked representation #404 is unavailable or malformed; "
        "linked representation has 0 descriptive items"
    )


def test_malformed_manufacturing_requirement_is_retained_with_explicit_reason(tmp_path):
    (fact,) = _read_requirements(
        tmp_path,
        "ambiguous-requirement",
        "#1=REPRESENTATION('first',(),#99);",
        "#2=REPRESENTATION('second',(),#99);",
        "#3=PROPERTY_DEFINITION('manufacturing requirement','thread',#98);",
        "#4=PROPERTY_DEFINITION_REPRESENTATION(#3,#1);",
        "#5=PROPERTY_DEFINITION_REPRESENTATION(#3,#2);",
    )

    assert fact.entity_id == "#3"
    assert fact.semantic_name == "thread"
    assert fact.text == ""
    assert fact.reason == "manufacturing requirement has 2 linked representations"


def test_datum_correspondence_requires_exact_context_and_letter():
    facts = (
        DatumOccurrenceFact("#1", "Position.1", "position", "#10", "#20", "A"),
        DatumOccurrenceFact("#2", "Position.2", "position", "#10", "#20", "A"),
    )

    assert match_datum_occurrence(facts, "Position.2", "A") == (facts[1], "")
    assert match_datum_occurrence(facts, "Position.1", "B") == (
        None,
        "Part21 has no datum 'B' in tolerance 'Position.1'",
    )
    assert match_datum_occurrence(facts, "", "A") == (
        None,
        "XCAF datum occurrence has no tolerance context",
    )
    assert match_datum_occurrence(facts, "Position.1", "") == (
        None,
        "XCAF datum occurrence has no letter",
    )


def test_datum_correspondence_fails_closed_when_context_and_letter_are_ambiguous():
    facts = (
        DatumOccurrenceFact("#1", "Position.1", "position", "#10", "#20", "A"),
        DatumOccurrenceFact("#1", "Position.1", "position", "#11", "#21", "A"),
    )

    fact, reason = match_datum_occurrence(facts, "Position.1", "A")

    assert fact is None
    assert "ambiguous" in reason
    assert "#1/#10, #1/#11" in reason


def test_part21_datum_with_two_feature_relationships_stays_ambiguous(tmp_path):
    facts = _read_datums(
        tmp_path,
        "ambiguous-datum-feature",
        "#1=(GEOMETRIC_TOLERANCE('Probe','',#90,#91) "
        "GEOMETRIC_TOLERANCE_WITH_DATUM_REFERENCE((#2)) POSITION_TOLERANCE());",
        "#2=DATUM_SYSTEM('',$,#99,.F.,(#3));",
        "#3=DATUM_REFERENCE_COMPARTMENT('',$,#99,.F.,#4,$);",
        "#4=DATUM('',$,#99,.F.,'A');",
        "#5=DATUM_FEATURE('first',$,#99,.T.);",
        "#6=DATUM_FEATURE('second',$,#99,.T.);",
        "#7=SHAPE_ASPECT_RELATIONSHIP('',$,#5,#4);",
        "#8=SHAPE_ASPECT_RELATIONSHIP('',$,#6,#4);",
    )

    assert facts == (
        DatumOccurrenceFact(
            "#1",
            "Probe",
            "position",
            "",
            "#4",
            "A",
            reason="datum #4 has 2 related DATUM_FEATUREs",
        ),
    )
    assert match_datum_occurrence(facts, "Probe", "A") == (
        facts[0],
        "datum #4 has 2 related DATUM_FEATUREs",
    )


def test_reversed_datum_relationship_is_exact_but_missing_items_remain_explicit(tmp_path):
    facts = _read_datums(
        tmp_path,
        "reversed-datum-feature",
        "#1=(GEOMETRIC_TOLERANCE('Probe','',#90,#91) "
        "GEOMETRIC_TOLERANCE_WITH_DATUM_REFERENCE((#2)) POSITION_TOLERANCE());",
        "#2=DATUM_SYSTEM('',$,#99,.F.,(#3));",
        "#3=DATUM_REFERENCE_COMPARTMENT('',$,#99,.F.,#4,$);",
        "#4=DATUM('',$,#99,.F.,'A');",
        "#5=DATUM_FEATURE('first',$,#99,.T.);",
        "#6=SHAPE_ASPECT_RELATIONSHIP('',$,#4,#5);",
    )

    assert facts == (
        DatumOccurrenceFact(
            "#1",
            "Probe",
            "position",
            "#5",
            "#4",
            "A",
            reason="datum feature #5 has no representation items",
        ),
    )


def test_datum_level_geometry_is_used_when_the_feature_has_no_items(tmp_path):
    facts = _read_datums(
        tmp_path,
        "datum-level-geometry",
        "#1=(GEOMETRIC_TOLERANCE('Probe','',#90,#91) "
        "GEOMETRIC_TOLERANCE_WITH_DATUM_REFERENCE((#2)) POSITION_TOLERANCE());",
        "#2=DATUM_SYSTEM('',$,#99,.F.,(#3));",
        "#3=DATUM_REFERENCE_COMPARTMENT('',$,#99,.F.,#4,$);",
        "#4=DATUM('',$,#99,.F.,'A');",
        "#5=DATUM_FEATURE('first',$,#99,.T.);",
        "#6=SHAPE_ASPECT_RELATIONSHIP('',$,#5,#4);",
        "#7=GEOMETRIC_ITEM_SPECIFIC_USAGE('','DATUM',#4,#99,#10);",
    )

    assert facts == (DatumOccurrenceFact("#1", "Probe", "position", "#5", "#4", "A", ("#10",)),)


def test_ctc03_datum_level_geometry_recovers_d_and_e():
    facts = read_datum_occurrences(CTC03)
    by_letter = {
        fact.letter: fact.reference_item_ids for fact in facts if fact.letter in {"D", "E"}
    }

    assert by_letter == {"D": ("#1399",), "E": ("#1441",)}
    assert all(not fact.reason for fact in facts if fact.letter in {"D", "E"})


def test_ctc03_datum_definitions_include_standalone_f():
    facts = read_datum_definitions(CTC03)

    assert [(fact.letter, fact.datum_feature_id) for fact in facts] == [
        ("B", "#91"),
        ("A", "#94"),
        ("C", "#92"),
        ("D", "#95"),
        ("E", "#93"),
        ("F", "#96"),
    ]
    assert facts[-1] == DatumDefinitionFact("#96", "#90", "F", ("#1379", "#1378"))


def test_datum_definition_without_one_physical_feature_fails_closed(tmp_path):
    facts = _read_datum_definitions(
        tmp_path,
        "unrelated-datum-definition",
        "#4=DATUM('',$,#99,.F.,'A');",
        "#5=SHAPE_ASPECT_RELATIONSHIP('',$,$,$);",
    )

    assert facts == (
        DatumDefinitionFact(
            "",
            "#4",
            "A",
            reason="datum #4 has 0 related DATUM_FEATUREs",
        ),
    )


def test_malformed_part21_datum_graphs_fail_closed(tmp_path):
    no_base_parameters = _read_datums(tmp_path, "empty-tolerance", "#1=POSITION_TOLERANCE();")
    assert no_base_parameters == ()

    no_datum = _read_datums(
        tmp_path,
        "empty-compartment",
        "#1=(GEOMETRIC_TOLERANCE('Probe','',#90,#91) "
        "GEOMETRIC_TOLERANCE_WITH_DATUM_REFERENCE((#2)) POSITION_TOLERANCE());",
        "#2=DATUM_SYSTEM('',$,#99,.F.,(#3));",
        "#3=DATUM_REFERENCE_COMPARTMENT('',$,#99,.F.,$,$);",
    )
    assert no_datum == (
        DatumOccurrenceFact(
            "#1",
            "Probe",
            "position",
            "",
            "",
            "",
            reason="datum compartment #3 has 0 datum references",
        ),
    )

    no_feature = _read_datums(
        tmp_path,
        "unrelated-datum",
        "#1=(GEOMETRIC_TOLERANCE('Probe','',#90,#91) "
        "GEOMETRIC_TOLERANCE_WITH_DATUM_REFERENCE((#2)) POSITION_TOLERANCE());",
        "#2=DATUM_SYSTEM('',$,#99,.F.,(#3));",
        "#3=DATUM_REFERENCE_COMPARTMENT('',$,#99,.F.,#4,$);",
        "#4=DATUM('',$,#99,.F.,'A');",
        "#5=SHAPE_ASPECT_RELATIONSHIP('',$,$,$);",
    )
    assert no_feature == (
        DatumOccurrenceFact(
            "#1",
            "Probe",
            "position",
            "",
            "#4",
            "A",
            reason="datum #4 has 0 related DATUM_FEATUREs",
        ),
    )


def test_si_length_unit_is_resolved_to_millimetres(tmp_path):
    step = tmp_path / "centimetres.step"
    step.write_text(
        _step(
            "#1=(GEOMETRIC_TOLERANCE('Probe','',#2,#4) POSITION_TOLERANCE());",
            "#2=(LENGTH_MEASURE_WITH_UNIT() MEASURE_REPRESENTATION_ITEM() "
            "MEASURE_WITH_UNIT(LENGTH_MEASURE(1.25),#3) REPRESENTATION_ITEM(''));",
            "#3=(LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.CENTI.,.METRE.));",
        ),
        encoding="utf-8",
    )

    assert read_geometric_tolerances(step) == (
        GeometricToleranceFact("#1", "Probe", "position", 12.5),
    )


def test_conversion_based_length_unit_is_resolved_to_millimetres(tmp_path):
    step = tmp_path / "inch.step"
    step.write_text(
        _step(
            "#1=(GEOMETRIC_TOLERANCE('Probe','',#2,#4) POSITION_TOLERANCE());",
            "#2=(LENGTH_MEASURE_WITH_UNIT() MEASURE_REPRESENTATION_ITEM() "
            "MEASURE_WITH_UNIT(LENGTH_MEASURE(1.25),#3) REPRESENTATION_ITEM(''));",
            "#3=(CONVERSION_BASED_UNIT('INCH',#5) LENGTH_UNIT() NAMED_UNIT(#8));",
            "#5=LENGTH_MEASURE_WITH_UNIT(LENGTH_MEASURE(25.4),#6);",
            "#6=(LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.));",
            "#8=DIMENSIONAL_EXPONENTS(1.,0.,0.,0.,0.,0.,0.);",
        ),
        encoding="utf-8",
    )

    assert read_geometric_tolerances(step) == (
        GeometricToleranceFact("#1", "Probe", "position", 31.75),
    )


def test_ctc03_inch_geometric_tolerances_are_resolved_to_millimetres():
    facts = read_geometric_tolerances(CTC03)

    assert len(facts) == 13
    assert all(fact.reason == "" for fact in facts)
    assert [fact.value_mm for fact in facts] == pytest.approx(
        [0.254, 1.016, 0.127, 1.524, 0.762, 1.27, 1.27, 0.508, 1.524, 2.032, 0.762, 0.254, 0.254]
    )


def test_ctc03_dimension_length_factor_is_resolved_from_authored_representations():
    factor, reason = read_dimension_length_factor(CTC03)

    assert factor == pytest.approx(25.4)
    assert reason == ""


def test_ctc03_dimension_display_facts_preserve_inch_precision():
    facts = read_dimension_display_facts(CTC03)
    by_id = {fact.entity_id: fact for fact in facts}

    assert len(facts) == 9
    assert by_id["#97"].authored_value == pytest.approx(0.75)
    assert (by_id["#97"].kind, by_id["#97"].value_decimals) == ("linear", 3)
    assert by_id["#267"].authored_value == pytest.approx(2.0)
    assert (by_id["#267"].value_decimals, by_id["#267"].tolerance_decimals) == (2, 2)
    assert by_id["#270"].authored_value == pytest.approx(0.82)
    assert (by_id["#270"].value_decimals, by_id["#270"].tolerance_decimals) == (2, 2)
    assert all(fact.unit_name == "inch" for fact in facts)


def test_dimension_display_match_rejects_conflicting_source_policies():
    fact = read_dimension_display_facts(CTC03)[0]

    assert (
        match_dimension_display(
            (fact, replace(fact, unit_name="conflicting-unit")),
            fact.semantic_name,
            fact.kind,
            fact.authored_value,
        )
        is None
    )


def test_dimension_display_rejects_unbounded_source_precision(tmp_path):
    facts = _read_dimension_display_facts(
        tmp_path,
        "unbounded-precision",
        "#1=DIMENSIONAL_SIZE(#99,'diameter');",
        "#2=SHAPE_DIMENSION_REPRESENTATION('',(#3),#99);",
        "#3=(LENGTH_MEASURE_WITH_UNIT() MEASURE_REPRESENTATION_ITEM() "
        "MEASURE_WITH_UNIT(LENGTH_MEASURE(1.),#5) "
        "QUALIFIED_REPRESENTATION_ITEM((#6)) REPRESENTATION_ITEM('nominal value'));",
        "#4=DIMENSIONAL_CHARACTERISTIC_REPRESENTATION(#1,#2);",
        "#5=(LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.));",
        "#6=VALUE_FORMAT_TYPE_QUALIFIER('NR2 0.1000000000');",
    )

    assert len(facts) == 1
    assert facts[0].value_decimals is None


def test_ctc01_dimension_length_factor_is_already_millimetres():
    factor, reason = read_dimension_length_factor(CTC01)

    assert factor == pytest.approx(1.0)
    assert reason == ""


def test_ctc05_positive_length_measures_and_presentation_items_are_supported():
    factor, reason = read_dimension_length_factor(CTC05)

    assert factor == pytest.approx(25.4)
    assert reason == ""


def test_mixed_dimension_length_units_fail_closed(tmp_path):
    step = tmp_path / "mixed-dimension-units.step"
    step.write_text(
        _step(
            "#1=SHAPE_DIMENSION_REPRESENTATION('',(#2,#3),#9);",
            "#2=(LENGTH_MEASURE_WITH_UNIT() MEASURE_REPRESENTATION_ITEM() "
            "MEASURE_WITH_UNIT(LENGTH_MEASURE(1.0),#4) REPRESENTATION_ITEM(''));",
            "#3=(LENGTH_MEASURE_WITH_UNIT() MEASURE_REPRESENTATION_ITEM() "
            "MEASURE_WITH_UNIT(LENGTH_MEASURE(1.0),#5) REPRESENTATION_ITEM(''));",
            "#4=(LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.));",
            "#5=(LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.CENTI.,.METRE.));",
        ),
        encoding="utf-8",
    )

    factor, reason = read_dimension_length_factor(step)

    assert factor is None
    assert reason == "length dimensions use multiple unit scales: (1.0, 10.0)"


def test_valid_factor_plus_malformed_dimension_item_fails_closed(tmp_path):
    step = tmp_path / "malformed-dimension-unit.step"
    step.write_text(
        _step(
            "#1=SHAPE_DIMENSION_REPRESENTATION('',(#2),#9);",
            "#2=(LENGTH_MEASURE_WITH_UNIT() MEASURE_REPRESENTATION_ITEM() "
            "MEASURE_WITH_UNIT(LENGTH_MEASURE(1.0),#4) REPRESENTATION_ITEM(''));",
            "#3=SHAPE_DIMENSION_REPRESENTATION('',(#6),#9);",
            "#4=(LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.));",
            "#6=REPRESENTATION_ITEM('missing measure');",
        ),
        encoding="utf-8",
    )

    factor, reason = read_dimension_length_factor(step)

    assert factor is None
    assert reason == "shape-dimension item #6 has no measure with unit"


@pytest.mark.parametrize(
    ("item", "expected_reason"),
    [
        (
            "#2=MEASURE_WITH_UNIT(1.0,#4);",
            "shape-dimension item #2 has no typed measure",
        ),
        (
            "#2=MEASURE_WITH_UNIT(COUNT_MEASURE(1.0),#4);",
            "shape-dimension item #2 uses unsupported measure type COUNT_MEASURE",
        ),
        (
            "#2=MEASURE_WITH_UNIT(LENGTH_MEASURE(1.0),$);",
            "shape-dimension item #2 has no unit reference",
        ),
    ],
)
def test_malformed_dimension_measures_fail_closed(tmp_path, item, expected_reason):
    step = tmp_path / "malformed-dimension-measure.step"
    step.write_text(
        _step(
            "#1=SHAPE_DIMENSION_REPRESENTATION('',(#2),#9);",
            item,
            "#4=(LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.));",
        ),
        encoding="utf-8",
    )

    factor, reason = read_dimension_length_factor(step)

    assert factor is None
    assert reason == expected_reason


def test_dimension_representation_without_semantic_length_has_explicit_reason(tmp_path):
    step = tmp_path / "angular-dimension.step"
    step.write_text(
        _step(
            "#1=SHAPE_DIMENSION_REPRESENTATION('',(#2),#9);",
            "#2=MEASURE_WITH_UNIT(PLANE_ANGLE_MEASURE(45.0),#4);",
            "#4=(NAMED_UNIT(*) PLANE_ANGLE_UNIT() SI_UNIT($,.RADIAN.));",
        ),
        encoding="utf-8",
    )

    factor, reason = read_dimension_length_factor(step)

    assert factor is None
    assert reason == "no authored length-dimension unit is available"


@pytest.mark.parametrize(
    ("measure", "unit", "expected_reason"),
    [
        (
            "#2=MEASURE_WITH_UNIT(LENGTH_MEASURE(1.0),#3);",
            (
                "#3=(CONVERSION_BASED_UNIT('HUGE',#5) LENGTH_UNIT() NAMED_UNIT(#8));",
                "#5=LENGTH_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E308),#6);",
                "#6=(LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.EXA.,.METRE.));",
                "#8=DIMENSIONAL_EXPONENTS(1.,0.,0.,0.,0.,0.,0.);",
            ),
            "length unit #3 conversion factor must be finite and positive in millimetres",
        ),
        (
            "#2=MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E308),#3);",
            ("#3=(LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT($,.METRE.));",),
            "tolerance magnitude #2 must be finite and positive in millimetres",
        ),
        (
            "#2=MEASURE_WITH_UNIT(LENGTH_MEASURE(1.0),#3);",
            (
                "#3=(CONVERSION_BASED_UNIT('TINY',#5) LENGTH_UNIT() NAMED_UNIT(#8));",
                "#5=LENGTH_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-310),#6);",
                "#6=(LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.ATTO.,.METRE.));",
                "#8=DIMENSIONAL_EXPONENTS(1.,0.,0.,0.,0.,0.,0.);",
            ),
            "length unit #3 conversion factor must be finite and positive in millimetres",
        ),
        (
            "#2=MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-310),#3);",
            ("#3=(LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.ATTO.,.METRE.));",),
            "tolerance magnitude #2 must be finite and positive in millimetres",
        ),
    ],
)
def test_length_conversion_overflow_fails_closed(tmp_path, measure, unit, expected_reason):
    (fact,) = _read(
        tmp_path,
        "overflow",
        "#1=(GEOMETRIC_TOLERANCE('Probe','',#2,#4) POSITION_TOLERANCE());",
        measure,
        *unit,
    )

    assert fact.value_mm is None
    assert fact.reason == expected_reason


@pytest.mark.parametrize(
    ("unit_entities", "expected_reason"),
    [
        (
            (
                "#3=(CONVERSION_BASED_UNIT('CYCLE',#5) LENGTH_UNIT() NAMED_UNIT(#8));",
                "#5=LENGTH_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.0),#3);",
            ),
            "length unit #3 has a cyclic conversion",
        ),
        (
            ("#3=(LENGTH_UNIT() NAMED_UNIT(#8));",),
            "length unit #3 is not a supported length unit",
        ),
        (
            ("#3=(CONVERSION_BASED_UNIT('BAD',1.0) LENGTH_UNIT() NAMED_UNIT(#8));",),
            "length unit #3 has no referenced conversion factor",
        ),
        (
            (
                "#3=(CONVERSION_BASED_UNIT('BAD',#5) LENGTH_UNIT() NAMED_UNIT(#8));",
                "#5=REPRESENTATION_ITEM('not a measure');",
            ),
            "length unit #3 has no usable conversion factor",
        ),
        (
            (
                "#3=(CONVERSION_BASED_UNIT('BAD',#5) LENGTH_UNIT() NAMED_UNIT(#8));",
                "#5=MEASURE_WITH_UNIT(PLANE_ANGLE_MEASURE(1.0),#6);",
            ),
            "length unit #3 conversion factor is not a length measure",
        ),
        (
            (
                "#3=(CONVERSION_BASED_UNIT('BAD',#5) LENGTH_UNIT() NAMED_UNIT(#8));",
                "#5=MEASURE_WITH_UNIT(LENGTH_MEASURE(1.0),1.0);",
            ),
            "length unit #3 conversion factor has no referenced length unit",
        ),
        (
            (
                "#3=(CONVERSION_BASED_UNIT('BAD',#5) LENGTH_UNIT() NAMED_UNIT(#8));",
                "#5=MEASURE_WITH_UNIT(LENGTH_MEASURE('bad'),#6);",
            ),
            "length unit #3 conversion factor is not numeric",
        ),
        (
            (
                "#3=(CONVERSION_BASED_UNIT('BAD',#5) LENGTH_UNIT() NAMED_UNIT(#8));",
                "#5=MEASURE_WITH_UNIT(LENGTH_MEASURE(0.0),#6);",
            ),
            "length unit #3 conversion factor must be finite and positive",
        ),
    ],
)
def test_malformed_conversion_based_units_fail_closed(tmp_path, unit_entities, expected_reason):
    (fact,) = _read(
        tmp_path,
        "malformed-conversion",
        "#1=(GEOMETRIC_TOLERANCE('Probe','',#2,#4) POSITION_TOLERANCE());",
        "#2=MEASURE_WITH_UNIT(LENGTH_MEASURE(1.0),#3);",
        *unit_entities,
        "#8=DIMENSIONAL_EXPONENTS(1.,0.,0.,0.,0.,0.,0.);",
    )

    assert fact.value_mm is None
    assert fact.reason == expected_reason


def test_simple_length_measure_with_unit_is_supported(tmp_path):
    facts = _read(
        tmp_path,
        "simple-measure",
        "#1=(GEOMETRIC_TOLERANCE('Probe','',#2,#4) POSITION_TOLERANCE());",
        "#2=LENGTH_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.5),#3);",
        "#3=(LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT($,.METRE.));",
    )

    assert facts == (GeometricToleranceFact("#1", "Probe", "position", 1500.0),)


@pytest.mark.parametrize(
    ("name", "instances", "expected_reason"),
    [
        (
            "missing-measure",
            ("#1=(GEOMETRIC_TOLERANCE('Probe','',#2,#4) POSITION_TOLERANCE());",),
            "tolerance magnitude #2 is missing",
        ),
        (
            "missing-unit",
            (
                "#1=(GEOMETRIC_TOLERANCE('Probe','',#2,#4) POSITION_TOLERANCE());",
                "#2=MEASURE_WITH_UNIT(LENGTH_MEASURE(1.0),#3);",
            ),
            "length unit #3 is missing",
        ),
        (
            "not-measure-with-unit",
            (
                "#1=(GEOMETRIC_TOLERANCE('Probe','',#2,#4) POSITION_TOLERANCE());",
                "#2=REPRESENTATION_ITEM('not a measure');",
            ),
            "tolerance magnitude #2 is not a measure with unit",
        ),
        (
            "not-length",
            (
                "#1=(GEOMETRIC_TOLERANCE('Probe','',#2,#4) POSITION_TOLERANCE());",
                "#2=MEASURE_WITH_UNIT(PLANE_ANGLE_MEASURE(1.0),#3);",
            ),
            "tolerance magnitude #2 is not a length measure",
        ),
        (
            "unit-not-reference",
            (
                "#1=(GEOMETRIC_TOLERANCE('Probe','',#2,#4) POSITION_TOLERANCE());",
                "#2=MEASURE_WITH_UNIT(LENGTH_MEASURE(1.0),1.0);",
            ),
            "tolerance magnitude #2 has no referenced length unit",
        ),
        (
            "not-numeric",
            (
                "#1=(GEOMETRIC_TOLERANCE('Probe','',#2,#4) POSITION_TOLERANCE());",
                "#2=MEASURE_WITH_UNIT(LENGTH_MEASURE('bad'),#3);",
            ),
            "tolerance magnitude #2 is not numeric",
        ),
        (
            "not-positive",
            (
                "#1=(GEOMETRIC_TOLERANCE('Probe','',#2,#4) POSITION_TOLERANCE());",
                "#2=MEASURE_WITH_UNIT(LENGTH_MEASURE(0.0),#3);",
            ),
            "tolerance magnitude #2 must be finite and positive",
        ),
        (
            "unsupported-prefix",
            (
                "#1=(GEOMETRIC_TOLERANCE('Probe','',#2,#4) POSITION_TOLERANCE());",
                "#2=MEASURE_WITH_UNIT(LENGTH_MEASURE(1.0),#3);",
                "#3=(LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.RADIAN.));",
            ),
            "length unit #3 is not a supported SI metre unit",
        ),
    ],
)
def test_malformed_or_unsupported_magnitudes_fail_closed(
    tmp_path, name, instances, expected_reason
):
    (fact,) = _read(tmp_path, name, *instances)

    assert fact.entity_id == "#1"
    assert fact.value_mm is None
    assert fact.reason == expected_reason


@pytest.mark.parametrize(
    ("name", "entity", "expected_semantic_name", "expected_kind", "expected_reason"),
    [
        (
            "multiple-characteristics",
            "#1=(FLATNESS_TOLERANCE() GEOMETRIC_TOLERANCE('Probe','',#2,#4) "
            "POSITION_TOLERANCE());",
            "",
            "",
            "geometric tolerance has multiple supported characteristics",
        ),
        (
            "missing-tuple",
            "#1=POSITION_TOLERANCE();",
            "",
            "position",
            "geometric tolerance has no name/magnitude tuple",
        ),
        (
            "magnitude-not-reference",
            "#1=POSITION_TOLERANCE('Probe','',1.0,#4);",
            "Probe",
            "position",
            "geometric tolerance has no referenced magnitude",
        ),
    ],
)
def test_malformed_characteristics_remain_inventory_facts(
    tmp_path, name, entity, expected_semantic_name, expected_kind, expected_reason
):
    (fact,) = _read(tmp_path, name, entity)

    assert fact == GeometricToleranceFact(
        "#1", expected_semantic_name, expected_kind, None, expected_reason
    )


def test_correspondence_requires_one_nonblank_name_and_kind_pair():
    facts = (
        GeometricToleranceFact("#1", "Same", "position", 0.1),
        GeometricToleranceFact("#2", "Same", "flatness", 0.2),
    )

    fact, reason = match_geometric_tolerance(facts, "Same", "flatness")
    assert fact == facts[1]
    assert reason == ""

    fact, reason = match_geometric_tolerance(facts, "", "position")
    assert fact is None
    assert reason == "XCAF geometric tolerance has no semantic name"


def test_correspondence_rejects_duplicates_instead_of_using_source_order():
    facts = (
        GeometricToleranceFact("#20", "Duplicate", "position", 0.2),
        GeometricToleranceFact("#10", "Duplicate", "position", 0.1),
    )

    fact, reason = match_geometric_tolerance(facts, "Duplicate", "position")

    assert fact is None
    assert reason == ("Part21 correspondence is ambiguous for position 'Duplicate' (#20, #10)")


@pytest.mark.parametrize("facts", [(), (GeometricToleranceFact("#1", "Other", "position", 0.1),)])
def test_correspondence_reports_a_missing_pair(facts):
    fact, reason = match_geometric_tolerance(facts, "Wanted", "position")

    assert fact is None
    assert reason == "Part21 has no position geometric tolerance named 'Wanted'"
