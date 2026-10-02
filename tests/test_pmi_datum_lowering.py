"""AP242 datum occurrence correspondence and concept lowering (#1099)."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from build123d import Box

import draftwright.pmi as pmi_module
from draftwright import _pmi_topology as topology_module
from draftwright._pmi_part21 import read_datum_definitions, read_datum_occurrences
from draftwright.linting import lint_pmi_lowering
from draftwright.model import DatumRef, PmiFeature, build_pmi_features
from draftwright.model.ir import Frame
from draftwright.pmi import PmiExtractionReport, PmiRecord, PmiSourceEntity
from draftwright.sheet_emit import _feature_block, _feature_line

SOURCE_IDS = ("datum:one", "datum:two")


class _Sequence:
    def __init__(self):
        self.items = []

    def Append(self, item):
        self.items.append(item)

    def Length(self):
        return len(self.items)

    def Value(self, index):
        return self.items[index - 1]


class _Face:
    def __init__(self, identity):
        self.identity = identity

    def IsNull(self):
        return False

    def ShapeType(self):
        return "face"

    def IsSame(self, other):
        return self.identity == other.identity


class _ImportedFaces:
    def __init__(self, *faces):
        self.faces = faces

    def FindIndex(self, shape):
        return next((index for index, face in enumerate(self.faces, 1) if face.IsSame(shape)), 0)


class _StepModel:
    def __init__(self, ranks, entity_type="StepShape_AdvancedFace"):
        self.ranks = ranks
        self.entity_type = entity_type

    def NextNumberForLabel(self, label, lastnum=0, exact=True):
        assert lastnum == 0
        assert exact is True
        return self.ranks.get(label, 0)

    def Value(self, rank):
        return SimpleNamespace(DynamicType=lambda: SimpleNamespace(Name=lambda: self.entity_type))


class _StepReader:
    def __init__(self, ranks, results, *, entity_type="StepShape_AdvancedFace"):
        self.model = _StepModel(ranks, entity_type)
        self.results = results
        self.shapes = []

    def StepModel(self):
        return self.model

    def TransferOne(self, rank):
        shape = self.results.get(rank)
        if shape is None:
            return False
        self.shapes.append(shape)
        return True

    def NbShapes(self):
        return len(self.shapes)

    def Shape(self, index):
        return self.shapes[index - 1]


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


def test_datum_topology_resolution_uses_part21_labels_and_exact_imported_identity(monkeypatch):
    imported = (_Face("left"), _Face("right"))
    reader = _StepReader({"#839": 7, "#840": 11}, {7: imported[0], 11: imported[1]})
    monkeypatch.setattr(topology_module, "TopAbs_FACE", "face")
    resolver = pmi_module._DatumTopologyResolver(reader, _ImportedFaces(*imported))

    shapes, reasons = resolver.resolve("#36", ("#839", "#840"))

    assert reasons == ()
    assert shapes == imported


@pytest.mark.parametrize(
    ("ranks", "results", "imported", "expected"),
    (
        ({"#839": 7}, {}, (_Face("left"),), "could not be transferred"),
        (
            {"#839": 7},
            {7: _Face("other")},
            (_Face("left"),),
            "is not a face in the imported topology",
        ),
        (
            {"#839": 7, "#840": 11},
            {7: _Face("left"), 11: _Face("left")},
            (_Face("left"),),
            "resolve to the same imported face",
        ),
    ),
)
def test_datum_topology_resolution_fails_closed_for_missing_unimported_or_collapsed_faces(
    monkeypatch, ranks, results, imported, expected
):
    monkeypatch.setattr(topology_module, "TopAbs_FACE", "face")
    resolver = pmi_module._DatumTopologyResolver(
        _StepReader(ranks, results), _ImportedFaces(*imported)
    )

    shapes, reasons = resolver.resolve("#36", tuple(ranks))

    assert shapes == ()
    assert any(expected in reason for reason in reasons)


def test_datum_topology_resolution_rejects_empty_nonface_and_transfer_exception(monkeypatch):
    monkeypatch.setattr(topology_module, "TopAbs_FACE", "face")

    resolver = pmi_module._DatumTopologyResolver(_StepReader({}, {}), _ImportedFaces())
    assert resolver.resolve("#36", ())[1] == ("datum feature has no Part21 representation items",)
    assert "is unavailable" in resolver.resolve("#36", ("#missing",))[1][0]

    reader = _StepReader({"#839": 7}, {}, entity_type="StepRepr_RepresentationItem")
    resolver = pmi_module._DatumTopologyResolver(reader, _ImportedFaces())
    assert "is not an advanced face" in resolver.resolve("#36", ("#839",))[1][0]

    reader = _StepReader({"#839": 7}, {})
    reader.TransferOne = lambda _rank: (_ for _ in ()).throw(RuntimeError("binder failed"))
    resolver = pmi_module._DatumTopologyResolver(reader, _ImportedFaces())
    assert "RuntimeError: binder failed" in resolver.resolve("#36", ("#839",))[1][0]

    bad_shape = _Face("bad")
    bad_shape.ShapeType = lambda: "edge"
    reader = _StepReader({"#839": 7}, {7: bad_shape})
    resolver = pmi_module._DatumTopologyResolver(reader, _ImportedFaces(bad_shape))
    assert "did not transfer to one face" in resolver.resolve("#36", ("#839",))[1][0]


def test_datum_topology_resolution_rejects_two_definitions_claiming_one_face(monkeypatch):
    imported = (_Face("shared"), _Face("other"))
    reader = _StepReader(
        {"#839": 7, "#840": 11, "#853": 13},
        {7: imported[0], 11: imported[1], 13: imported[0]},
    )
    monkeypatch.setattr(topology_module, "TopAbs_FACE", "face")
    resolver = pmi_module._DatumTopologyResolver(reader, _ImportedFaces(*imported))

    assert resolver.resolve("#36", ("#839", "#840"))[1] == ()
    shapes, reasons = resolver.resolve("#35", ("#853",))

    assert shapes == ()
    assert any("already claimed by datum feature #36" in reason for reason in reasons)

    # The same source item reused by another definition takes the cache path and is rejected
    # by the same imported-topology ownership guard.
    shapes, reasons = resolver.resolve("#34", ("#839",))
    assert shapes == ()
    assert any("already claimed by datum feature #36" in reason for reason in reasons)


def test_surface_labels_may_share_one_exact_imported_edge(monkeypatch):
    imported = _Face("shared-edge")
    reader = _StepReader(
        {"#1850": 7},
        {7: imported},
        entity_type="StepShape_EdgeCurve",
    )
    monkeypatch.setattr(topology_module, "TopAbs_EDGE", "face")
    resolver = pmi_module._SurfaceLabelTopologyResolver(reader, _ImportedFaces(imported))

    first, first_reasons = resolver.resolve("#316", ("#1850",), noun="surface label")
    second, second_reasons = resolver.resolve("#317", ("#1850",), noun="surface label")

    assert first_reasons == second_reasons == ()
    assert first == second == (imported,)


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
        reference_item_ids=("#other",),
    )

    (projected,) = pmi_module._coalesce_datum_records([first, second])

    assert (
        "datum feature occurrences disagree about the datum letter" in projected.lowering_blockers
    )
    assert (
        "datum feature occurrences disagree about referenced geometry"
        in projected.lowering_blockers
    )
    assert (
        "datum feature occurrences disagree about referenced Part21 items"
        in projected.lowering_blockers
    )
    assert projected.datum_contexts == ("Position.1",)


def test_datum_geometry_rejects_unusable_reference_shapes(monkeypatch):
    class Shape:
        def __init__(self, *, null=False, surface=None, broken=False):
            self.null = null
            self.surface = surface
            self.broken = broken

        def IsNull(self):
            return self.null

    class Surface:
        def __init__(self, kind, direction=(0.0, 0.0, 1.0), location=(0.0, 0.0, 0.0)):
            self.kind = kind
            self.direction = direction
            self.location = location

        def GetType(self):
            return self.kind

        def Plane(self):
            dx, dy, dz = self.direction
            x, y, z = self.location
            direction = SimpleNamespace(X=lambda: dx, Y=lambda: dy, Z=lambda: dz)
            location = SimpleNamespace(X=lambda: x, Y=lambda: y, Z=lambda: z)
            axis = SimpleNamespace(Direction=lambda: direction, Location=lambda: location)
            return SimpleNamespace(Axis=lambda: axis)

        Cylinder = Plane

    def get_refs(label, first, second):
        for item in label[0]:
            first.Append(item)
        for item in label[1]:
            second.Append(item)

    def as_face(shape):
        if shape.broken:
            raise TypeError("not a face")
        return shape

    monkeypatch.setattr(pmi_module, "TDF_LabelSequence", _Sequence)
    monkeypatch.setattr(
        pmi_module, "XCAFDoc_DimTolTool", SimpleNamespace(GetRefShapeLabel_s=get_refs)
    )
    monkeypatch.setattr(pmi_module, "_shape_bbox", lambda _shape: (0.0, 1.0, 2.0, 2.0, 3.0, 4.0))
    monkeypatch.setattr(pmi_module, "TopoDS", SimpleNamespace(Face_s=as_face))
    monkeypatch.setattr(pmi_module, "BRepAdaptor_Surface", lambda shape: shape.surface)
    monkeypatch.setattr(pmi_module, "GeomAbs_Plane", "plane")
    monkeypatch.setattr(pmi_module, "GeomAbs_Cylinder", "cylinder")
    shape_tool = SimpleNamespace(GetShape_s=lambda ref: ref)

    cases = (
        ((Shape(null=True),), "datum reference surface is unavailable"),
        (
            (Shape(surface=Surface("unsupported")),),
            "one datum reference is neither planar nor cylindrical",
        ),
        (
            (Shape(surface=Surface("plane", direction=(1.0, 0.2, 0.0))),),
            "one datum reference surface is not axis-aligned",
        ),
        (
            (Shape(broken=True),),
            "one datum reference surface is unavailable (TypeError: not a face)",
        ),
        (
            (
                Shape(surface=Surface("plane", location=(0.0, 0.0, 0.0))),
                Shape(surface=Surface("plane", location=(0.0, 0.0, 1.0))),
            ),
            "datum reference faces are not coplanar",
        ),
        (
            (
                Shape(surface=Surface("plane")),
                Shape(surface=Surface("cylinder")),
            ),
            "datum reference faces mix planar and cylindrical surfaces",
        ),
    )
    for shapes, expected_reason in cases:
        points, bbox, axis, reasons = pmi_module._datum_reference_geometry(
            (shapes, ()), shape_tool
        )
        usable_shape_count = sum(not shape.null for shape in shapes)
        assert points == ((1.0, 2.0, 3.0),) * usable_shape_count
        assert bbox == ((0.0, 1.0, 2.0, 2.0, 3.0, 4.0) if usable_shape_count else None)
        assert axis == "", expected_reason
        assert expected_reason in reasons


def test_datum_metadata_failures_are_explicit(monkeypatch):
    def explode(*_args):
        raise RuntimeError("metadata unavailable")

    monkeypatch.setattr(pmi_module, "TDF_LabelSequence", _Sequence)
    monkeypatch.setattr(pmi_module, "XCAFDoc_Datum", SimpleNamespace(Set_s=explode))
    assert pmi_module._datum_letter(object()) == (
        "",
        "datum letter is unavailable (RuntimeError: metadata unavailable)",
    )
    monkeypatch.setattr(
        pmi_module,
        "XCAFDoc_Datum",
        SimpleNamespace(Set_s=lambda _label: SimpleNamespace(GetIdentification=lambda: None)),
    )
    assert pmi_module._datum_letter(object()) == ("", "datum occurrence has no letter")

    assert pmi_module._datum_context(object(), SimpleNamespace(GetTolerOfDatumLabels=explode)) == (
        "",
        "datum tolerance context is unavailable (RuntimeError: metadata unavailable)",
    )
    assert pmi_module._datum_context(
        object(), SimpleNamespace(GetTolerOfDatumLabels=lambda _label, _seq: None)
    ) == ("", "datum occurrence has 0 tolerance contexts")

    def one_context(_label, sequence):
        sequence.Append(object())

    monkeypatch.setattr(pmi_module, "XCAFDoc_GeomTolerance", SimpleNamespace(Set_s=explode))
    assert pmi_module._datum_context(
        object(), SimpleNamespace(GetTolerOfDatumLabels=one_context)
    ) == ("", "datum tolerance context is unavailable (RuntimeError: metadata unavailable)")
    monkeypatch.setattr(
        pmi_module,
        "XCAFDoc_GeomTolerance",
        SimpleNamespace(
            Set_s=lambda _label: SimpleNamespace(
                GetObject=lambda: SimpleNamespace(GetSemanticName=lambda: None)
            )
        ),
    )
    assert pmi_module._datum_context(
        object(), SimpleNamespace(GetTolerOfDatumLabels=one_context)
    ) == ("", "datum occurrence has no tolerance context")


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


def test_datum_definitions_do_not_require_one_named_tolerance_context_issue_2128(monkeypatch):
    step = Path(__file__).parent / "fixtures/nist_ctc_01_asme1_ap242.stp"
    definitions = read_datum_definitions(step)
    occurrences = read_datum_occurrences(step)
    # The fixture contains several authored tolerance uses of datum A and one physical A.
    (a_definition,) = [definition for definition in definitions if definition.letter == "A"]
    assert (
        len(
            {
                occurrence.tolerance_id
                for occurrence in occurrences
                if occurrence.datum_feature_id == a_definition.datum_feature_id
            }
        )
        > 1
    )

    # Simulate the XCAF writer presenting zero/multiple unnamed contexts on each label.
    # The physical datum's own Part21 definition still names its letter and support.
    monkeypatch.setattr(
        pmi_module,
        "_datum_context",
        lambda *_args: ("", "datum occurrence has no unique named tolerance context"),
    )
    report = pmi_module.extract_pmi_report(step)
    datums = [record for record in report.records if record.source_category == "datum"]
    assert {record.label for record in datums} == {"A", "B", "C"}
    assert len(datums) == 3
    assert sum(len(record.source_ids) for record in datums) == 11
    assert all(not record.lowering_blockers for record in datums)
    assert not [
        source
        for source in report.sources
        if source.category == "datum" and source.outcome != "extracted"
    ]


def test_complete_datum_definition_survives_occurrence_reader_failure_issue_2128(monkeypatch):
    step = Path(__file__).parent / "fixtures/nist_ctc_01_asme1_ap242.stp"
    definitions = read_datum_definitions(step)
    assert {definition.letter for definition in definitions} == {"A", "B", "C"}

    def failed_occurrence_reader(_step):
        raise ValueError("occurrence reader unavailable")

    monkeypatch.setattr(pmi_module, "read_datum_occurrences", failed_occurrence_reader)
    report = pmi_module.extract_pmi_report(step)
    datums = [record for record in report.records if record.source_category == "datum"]
    assert {record.label for record in datums} == {"A", "B", "C"}
    assert len(datums) == 3
    assert sum(len(record.source_ids) for record in datums) == 11
    assert not [
        source
        for source in report.sources
        if source.category == "datum" and source.outcome != "extracted"
    ]


def test_datum_definition_refuses_a_different_physical_support_issue_2128(monkeypatch):
    step = Path(__file__).parent / "fixtures/nist_ctc_01_asme1_ap242.stp"
    definitions = read_datum_definitions(step)
    (a_definition,) = [definition for definition in definitions if definition.letter == "A"]
    (b_definition,) = [definition for definition in definitions if definition.letter == "B"]
    baseline = pmi_module.extract_pmi_report(step)
    a = next(
        record
        for record in baseline.records
        if record.source_category == "datum" and record.label == "A"
    )
    b = next(
        record
        for record in baseline.records
        if record.source_category == "datum" and record.label == "B"
    )
    assert a.ref_bbox is not None and not a.lowering_blockers
    assert b.ref_bbox is not None and b.ref_bbox != a.ref_bbox

    original = pmi_module._DatumTopologyResolver.resolve

    def wrong_support(self, feature_id, item_ids):
        if feature_id == a_definition.datum_feature_id:
            return original(self, b_definition.datum_feature_id, b_definition.reference_item_ids)
        return original(self, feature_id, item_ids)

    monkeypatch.setattr(pmi_module._DatumTopologyResolver, "resolve", wrong_support)
    report = pmi_module.extract_pmi_report(step)
    a_sources = [
        source
        for source in report.sources
        if source.category == "datum" and source.source_id in a.source_ids
    ]
    assert len(a_sources) == len(a.source_ids)
    assert any(source.outcome == "partially_extracted" for source in a_sources)
    assert any(
        "datum definition support disagrees with XCAF" in source.reason for source in a_sources
    )
    definition_source_id = f"datum_definition:{a_definition.datum_id}"
    (definition_source,) = [
        source for source in report.sources if source.source_id == definition_source_id
    ]
    assert definition_source.outcome == "partially_extracted"
    assert "datum definition support disagrees with XCAF" in definition_source.reason
    definition_records = [
        record for record in report.records if definition_source_id in record.source_ids
    ]
    assert definition_records
    assert all(record.lowering_blockers for record in definition_records)
    assert not any(
        isinstance(feature, DatumRef) and definition_source_id in feature.source_ids
        for feature in build_pmi_features(report.records, Box(20, 20, 20).bounding_box())
    )


@pytest.mark.parametrize("geometry_reasons", ((), ("probe XCAF datum geometry failure",)))
def test_unmeasurable_xcaf_datum_occurrence_keeps_its_source_partial_issue_2128(
    monkeypatch, geometry_reasons
):
    step = Path(__file__).parent / "fixtures/nist_ctc_01_asme1_ap242.stp"
    baseline = pmi_module.extract_pmi_report(step)
    datum_a = next(
        record
        for record in baseline.records
        if record.source_category == "datum" and record.label == "A"
    )
    assert len(datum_a.source_ids) > 1 and datum_a.ref_bbox is not None
    source_id = datum_a.source_ids[0]
    original = pmi_module._datum_reference_geometry

    def unreadable_support(label, *args):
        geometry = original(label, *args)
        if pmi_module._source_id("datum", label) == source_id:
            assert geometry[1] is not None
            return geometry[0], None, "", geometry_reasons
        return geometry

    monkeypatch.setattr(pmi_module, "_datum_reference_geometry", unreadable_support)
    report = pmi_module.extract_pmi_report(step)
    (source,) = [item for item in report.sources if item.source_id == source_id]
    assert source.outcome == "partially_extracted"
    assert "support cannot be matched" in source.reason
    assert not any(
        source_id in record.source_ids and not record.lowering_blockers
        for record in report.records
    )
    assert any(record.label == "A" and not record.lowering_blockers for record in report.records)


@pytest.mark.parametrize(
    ("xcaf_shapes", "expected_outcome"),
    (((), "extracted"), ((None,), "partially_extracted")),
)
def test_only_absent_xcaf_datum_references_can_use_part21_support_issue_2128(
    monkeypatch, xcaf_shapes, expected_outcome
):
    step = Path(__file__).parent / "fixtures/nist_ctc_01_asme1_ap242.stp"
    baseline = pmi_module.extract_pmi_report(step)
    datum_a = next(
        record
        for record in baseline.records
        if record.source_category == "datum" and record.label == "A"
    )
    assert len(datum_a.source_ids) > 1 and datum_a.ref_bbox is not None
    source_id = datum_a.source_ids[0]
    direct_geometry = pmi_module._datum_geometry_from_shapes(xcaf_shapes)
    assert direct_geometry[:3] == ((), None, "")
    assert ("one referenced shape is unavailable" in direct_geometry[3]) == bool(xcaf_shapes)
    original = pmi_module._datum_reference_geometry

    def direct_support(label, *args):
        if pmi_module._source_id("datum", label) == source_id:
            return direct_geometry
        return original(label, *args)

    monkeypatch.setattr(pmi_module, "_datum_reference_geometry", direct_support)
    report = pmi_module.extract_pmi_report(step)
    (source,) = [item for item in report.sources if item.source_id == source_id]
    assert source.outcome == expected_outcome
    if xcaf_shapes:
        assert "support cannot be matched" in source.reason
        assert not any(
            source_id in record.source_ids and not record.lowering_blockers
            for record in report.records
        )
    else:
        assert not source.reason


def test_coalesced_datum_keeps_a_partial_sibling_occurrence_issue_2128(monkeypatch):
    step = Path(__file__).parent / "fixtures/nist_ctc_01_asme1_ap242.stp"
    baseline = pmi_module.extract_pmi_report(step)
    datum_a = next(
        record
        for record in baseline.records
        if record.source_category == "datum" and record.label == "A"
    )
    assert len(datum_a.source_ids) > 1 and datum_a.ref_bbox is not None
    assert datum_a.lowering_blockers == ()
    source_id = datum_a.source_ids[-1]
    assert (
        next(source for source in baseline.sources if source.source_id == source_id).outcome
        == "extracted"
    )
    original = pmi_module._datum_reference_geometry

    def partial_support(label, *args):
        geometry = original(label, *args)
        if pmi_module._source_id("datum", label) == source_id:
            assert geometry[0] and geometry[1] is not None and geometry[2]
            return (*geometry[:3], (*geometry[3], "probe XCAF partial support"))
        return geometry

    monkeypatch.setattr(pmi_module, "_datum_reference_geometry", partial_support)
    report = pmi_module.extract_pmi_report(step)
    (source,) = [item for item in report.sources if item.source_id == source_id]
    assert source.outcome == "partially_extracted"
    assert "probe XCAF partial support" in source.reason
    (coalesced,) = [
        record
        for record in report.records
        if record.source_category == "datum" and source_id in record.source_ids
    ]
    assert set(coalesced.source_ids) == set(datum_a.source_ids)
    assert "probe XCAF partial support" in coalesced.lowering_blockers
    assert not any(
        isinstance(feature, DatumRef) and source_id in feature.source_ids
        for feature in build_pmi_features(report.records, Box(20, 20, 20).bounding_box())
    )


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


def test_generated_sheet_line_omits_absent_datum_provenance():
    feature = DatumRef(
        frame=Frame((1.0, 2.0, 3.0), "z"),
        letter="A",
        view="front",
        side="below",
    )

    line = _feature_line(feature)

    assert "source_id=" not in line
    assert "source_ids=" not in line
    assert "part21_id=" not in line
    assert "origin=" not in line
