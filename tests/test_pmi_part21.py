"""Structured Part21 facts used to complete XCAF geometric tolerances."""

from pathlib import Path

import pytest

from draftwright._pmi_part21 import (
    DatumOccurrenceFact,
    GeometricToleranceFact,
    match_datum_occurrence,
    match_geometric_tolerance,
    read_datum_occurrences,
    read_geometric_tolerances,
)

CTC01 = Path(__file__).parent / "fixtures" / "nist_ctc_01_asme1_ap242.stp"


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


def test_unsupported_length_unit_stays_explicitly_unresolved(tmp_path):
    step = tmp_path / "unsupported-unit.step"
    step.write_text(
        _step(
            "#1=(GEOMETRIC_TOLERANCE('Probe','',#2,#4) POSITION_TOLERANCE());",
            "#2=(LENGTH_MEASURE_WITH_UNIT() MEASURE_REPRESENTATION_ITEM() "
            "MEASURE_WITH_UNIT(LENGTH_MEASURE(1.25),#3) REPRESENTATION_ITEM(''));",
            "#3=CONVERSION_BASED_UNIT('INCH',#5);",
        ),
        encoding="utf-8",
    )

    (fact,) = read_geometric_tolerances(step)
    assert fact.entity_id == "#1"
    assert fact.value_mm is None
    assert fact.reason == "length unit #3 is not a supported SI metre unit"


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
