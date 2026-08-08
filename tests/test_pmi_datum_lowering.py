"""AP242 datum occurrence correspondence and concept lowering (#1099)."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from build123d import Box

import draftwright.pmi as pmi_module
from draftwright.linting import lint_pmi_lowering
from draftwright.model import DatumRef, PmiFeature, build_pmi_features
from draftwright.model.ir import Frame
from draftwright.pmi import PmiExtractionReport, PmiRecord, PmiSourceEntity
from draftwright.sheet_emit import _feature_block, _feature_line

SOURCE_IDS = ("datum:one", "datum:two")


def _record(*, blockers=(), axis="Z") -> PmiRecord:
    return PmiRecord(
        kind="datum",
        type_code=None,
        value=0.0,
        ref_pts=((0.0, 0.0, 0.0),),
        ref_bbox=(-10.0, -10.0, 0.0, 10.0, 10.0, 0.0),
        dominant_axis=axis or "?",
        label="A",
        source_id=SOURCE_IDS[0],
        part21_id="#34",
        source_category="datum",
        lowering_blockers=blockers,
        source_ids=SOURCE_IDS,
        datum_contexts=("Position.1", "Perpendicularity.1"),
        reference_item_ids=("#861",),
        reference_axis=axis,
    )


def test_complete_datum_definition_lowers_once_for_all_source_occurrences():
    (feature,) = build_pmi_features((_record(),), Box(20, 20, 20).bounding_box())

    assert isinstance(feature, DatumRef)
    assert feature.letter == "A"
    assert feature.frame == Frame((0.0, 0.0, 0.0), "z")
    assert (feature.view, feature.side) == ("front", "above")
    assert feature.source_id == SOURCE_IDS[0]
    assert feature.source_ids == SOURCE_IDS
    assert feature.part21_id == "#34"
    assert isinstance(feature.origin, PmiFeature)
    assert feature.origin.datum_contexts == ("Position.1", "Perpendicularity.1")
    assert feature.origin.reference_item_ids == ("#861",)


def test_unresolved_datum_geometry_remains_one_provenance_rich_raw_definition():
    blocker = "referenced geometry is unavailable"
    (feature,) = build_pmi_features(
        (_record(blockers=(blocker,), axis=""),), Box(20, 20, 20).bounding_box()
    )

    assert isinstance(feature, PmiFeature)
    assert feature.source_ids == SOURCE_IDS
    assert feature.part21_id == "#34"
    assert feature.lowering_blockers == (blocker,)
    assert feature.reference_axis == ""


def test_definition_projection_rejects_a_wrong_letter_or_reference_face():
    first = replace(
        _record(),
        source_id=SOURCE_IDS[0],
        source_ids=(SOURCE_IDS[0],),
        datum_contexts=("Position.1",),
    )
    second = replace(
        _record(),
        source_id=SOURCE_IDS[1],
        source_ids=(SOURCE_IDS[1],),
        datum_contexts=(),
        label="B",
        ref_bbox=(-9.0, -10.0, 0.0, 10.0, 10.0, 0.0),
    )

    (projected,) = pmi_module._coalesce_datum_records([first, second])

    assert (
        "datum feature occurrences disagree about the datum letter" in projected.lowering_blockers
    )
    assert (
        "datum feature occurrences disagree about referenced geometry"
        in projected.lowering_blockers
    )
    assert projected.datum_contexts == ("Position.1",)


def test_lowering_reconciliation_counts_every_occurrence_represented_by_one_feature():
    record = _record()
    report = PmiExtractionReport(
        sources=tuple(
            PmiSourceEntity(source_id, "datum", None, "extracted") for source_id in SOURCE_IDS
        ),
        records=(record,),
    )
    (feature,) = build_pmi_features((record,), Box(20, 20, 20).bounding_box())

    assert lint_pmi_lowering(report, (feature,), "annotate") == []
    assert {issue.source_ids for issue in lint_pmi_lowering(report, (), "annotate")} == {
        (SOURCE_IDS[0],),
        (SOURCE_IDS[1],),
    }


def test_generated_sheet_line_round_trips_imported_datum_and_nested_provenance():
    (feature,) = build_pmi_features((_record(),), Box(20, 20, 20).bounding_box())
    captured = []
    sheet = SimpleNamespace(add=captured.append)

    exec(
        _feature_line(feature),
        {
            "sheet": sheet,
            "DatumRef": DatumRef,
            "Frame": Frame,
            "PmiFeature": PmiFeature,
        },
    )

    (restored,) = captured
    assert restored == feature
    assert restored.origin.source_ids == SOURCE_IDS
    assert restored.origin.reference_item_ids == ("#861",)


def test_generated_sheet_refuses_a_datum_with_an_unbound_feature_origin():
    feature = DatumRef(
        frame=Frame((1.0, 2.0, 3.0), "z"),
        letter="A",
        view="front",
        side="below",
        origin=SimpleNamespace(kind="hole"),
    )

    with pytest.raises(ValueError, match="datum_ref.*origin has no emitted binding"):
        _feature_block([feature])
