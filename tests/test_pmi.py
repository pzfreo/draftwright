"""Tests for AP242 PMI extraction, lowering, rendering, and mode reconciliation."""

from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest
from build123d import Box, export_step

from draftwright import build_drawing, extract_pmi, extract_pmi_report
from draftwright._pmi_part21 import GeometricToleranceFact
from draftwright.pmi import _PMI_AVAILABLE, PmiExtractionReport, PmiRecord

FIXTURES = Path(__file__).parent / "fixtures"
CTC01 = FIXTURES / "nist_ctc_01_asme1_ap242.stp"
CTC01_AP203 = FIXTURES / "nist_ctc_01_asme1_ap203.stp"

pytestmark = pytest.mark.skipif(not _PMI_AVAILABLE, reason="OCP GDT support not available")


# ---------------------------------------------------------------------------
# extract_pmi unit tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ctc01_extraction_report():
    return extract_pmi_report(CTC01)


class TestExtractPmi:
    def test_nist_ctc01_returns_records(self, ctc01_extraction_report):
        recs = ctc01_extraction_report.records
        assert len(recs) > 0

    def test_nist_ctc01_dim_count(self, ctc01_extraction_report):
        recs = ctc01_extraction_report.records
        dims = [r for r in recs if r.kind not in ("gtol", "datum")]
        assert len(dims) >= 8, f"expected ≥8 dim records, got {len(dims)}"

    def test_nist_ctc01_gtol_count(self, ctc01_extraction_report):
        recs = ctc01_extraction_report.records
        gtols = [
            r
            for r in recs
            if r.kind
            in (
                "straightness",
                "flatness",
                "circularity",
                "cylindricity",
                "profile_line",
                "profile_surface",
                "perpendicularity",
                "angularity",
                "parallelism",
                "position",
                "concentricity",
                "symmetry",
                "circular_runout",
                "total_runout",
            )
        ]
        assert len(gtols) >= 4

    def test_occt_geometric_tolerance_enum_is_mapped_by_its_actual_values(self):
        """Pin the installed OCCT enum, not a remembered ordering from another binding."""
        from OCP.XCAFDimTolObjects import XCAFDimTolObjects_GeomToleranceType as GtolType

        import draftwright.pmi as pmi_module

        expected = {
            "Angularity": "angularity",
            "CircularRunout": "circular_runout",
            "CircularityOrRoundness": "circularity",
            "Coaxiality": "coaxiality",
            "Concentricity": "concentricity",
            "Cylindricity": "cylindricity",
            "Flatness": "flatness",
            "Parallelism": "parallelism",
            "Perpendicularity": "perpendicularity",
            "Position": "position",
            "ProfileOfLine": "profile_line",
            "ProfileOfSurface": "profile_surface",
            "Straightness": "straightness",
            "Symmetry": "symmetry",
            "TotalRunout": "total_runout",
        }
        actual = {
            int(getattr(GtolType, f"XCAFDimTolObjects_GeomToleranceType_{enum_name}")): kind
            for enum_name, kind in expected.items()
        }

        assert pmi_module._GTOL_TYPE == actual

    def test_nist_ctc01_gtols_keep_characteristic_geometry_and_datums(
        self, ctc01_extraction_report
    ):
        """The NIST source identities are the oracle for all XCAF-owned GD&T fields."""
        expected = {
            "geometric_tolerance:0:1:4:1": (
                "position",
                2,
                ("A", "B", "C"),
                "#21",
                0.75,
            ),
            "geometric_tolerance:0:1:4:5": (
                "position",
                2,
                ("A", "B", "C"),
                "#22",
                0.75,
            ),
            "geometric_tolerance:0:1:4:11": (
                "profile_surface",
                2,
                ("A", "B", "C"),
                "#26",
                1.25,
            ),
            "geometric_tolerance:0:1:4:15": (
                "profile_surface",
                6,
                ("A",),
                "#27",
                0.5,
            ),
            "geometric_tolerance:0:1:4:18": (
                "perpendicularity",
                1,
                ("A",),
                "#56",
                1.5,
            ),
            "geometric_tolerance:0:1:4:20": ("flatness", 1, (), "#57", 0.2),
        }
        records = {
            record.source_id: record
            for record in ctc01_extraction_report.records
            if record.source_id.startswith("geometric_tolerance:")
        }

        assert set(records) == set(expected)
        for source_id, (kind, reference_count, datum_refs, part21_id, value) in expected.items():
            record = records[source_id]
            assert record.kind == kind
            assert record.value == value
            assert record.part21_id == part21_id
            assert record.source_category == "geometric_tolerance"
            assert len(record.ref_pts) == reference_count
            assert record.ref_bbox is not None
            assert record.dominant_axis in {"X", "Y", "Z"}
            assert record.datum_refs == datum_refs
            assert record.lowering_blockers == ()

        assert records["geometric_tolerance:0:1:4:15"].gtol_modifiers == ("all_around",)
        assert all(
            record.gtol_modifiers == ()
            for source_id, record in records.items()
            if source_id != "geometric_tolerance:0:1:4:15"
        )

        outcomes = {
            source.source_id: source
            for source in ctc01_extraction_report.sources
            if source.category == "geometric_tolerance"
        }
        assert all(
            source.outcome == "extracted" and source.reason == "" for source in outcomes.values()
        )

    def test_usable_dims_have_positive_value(self, ctc01_extraction_report):
        recs = ctc01_extraction_report.records
        usable = [r for r in recs if r.value > 0 and len(r.ref_pts) >= 2]
        assert len(usable) >= 4, f"expected ≥4 usable dims, got {len(usable)}"

    def test_diameter_labels_prefixed(self, ctc01_extraction_report):
        recs = ctc01_extraction_report.records
        diameters = [r for r in recs if r.kind == "diameter" and r.value > 0]
        assert len(diameters) >= 1
        for d in diameters:
            assert d.label.startswith("ø"), f"diameter label missing ø: {d.label!r}"

    def test_nist_range_diameters_keep_both_limits(self, ctc01_extraction_report):
        """The two range-encoded CTC-01 requirements are distinct source dimensions.

        Their XCAF objects carry a nominal value of 35 as well as explicit 34.8/35.2
        bounds. Reading only ``GetValue()`` produces a plausible but wrong bare ``ø35``.
        Pin the semantic fields and source identities from the NIST oracle so deleting the
        range branch cannot leave a green extraction test (#675).
        """
        source_ids = {"dimension:0:1:4:25", "dimension:0:1:4:26"}
        records = {
            record.source_id: record
            for record in ctc01_extraction_report.records
            if record.source_id in source_ids
        }

        assert set(records) == source_ids
        for record in records.values():
            assert record.value == 35.0
            assert (record.lower_bound, record.upper_bound) == (34.8, 35.2)
            assert record.label == "ø34.8 - ø35.2"
            assert record.upper_tol is None and record.lower_tol is None

    def test_ref_pts_are_3d_tuples(self, ctc01_extraction_report):
        recs = ctc01_extraction_report.records
        for r in recs:
            for pt in r.ref_pts:
                assert len(pt) == 3, f"ref_pt should be 3-tuple, got {pt!r}"

    def test_dominant_axis_set(self, ctc01_extraction_report):
        recs = ctc01_extraction_report.records
        usable = [r for r in recs if r.value > 0 and len(r.ref_pts) >= 2]
        axes = {r.dominant_axis for r in usable}
        assert axes - {"X", "Y", "Z", "?"} == set()
        assert axes & {"X", "Y", "Z"}, "at least one axis should be determined"

    def test_non_ap242_file_returns_empty(self, tmp_path):
        """AP203 geometry-only STEP file has no semantic PMI → empty list."""
        step = tmp_path / "plain.step"
        export_step(Box(40, 30, 20), str(step))
        recs = extract_pmi(step)
        assert recs == []

    def test_records_are_pmi_record_instances(self, ctc01_extraction_report):
        recs = ctc01_extraction_report.records
        for r in recs:
            assert isinstance(r, PmiRecord)

    def test_records_only_api_remains_a_list_projection(self, ctc01_extraction_report):
        assert extract_pmi(CTC01) == list(ctc01_extraction_report.records)

    def test_report_preserves_the_complete_ctc01_source_denominator(self, ctc01_extraction_report):
        report = ctc01_extraction_report
        assert isinstance(report, PmiExtractionReport)
        assert len(report.sources) == 38
        assert len({source.source_id for source in report.sources}) == 38
        assert Counter((source.category, source.outcome) for source in report.sources) == Counter(
            {
                ("dimension", "extracted"): 12,
                ("dimension", "presentation_only"): 9,
                ("geometric_tolerance", "extracted"): 6,
                ("datum", "extracted"): 11,
            }
        )
        assert {
            source_id
            for record in report.records
            for source_id in (record.source_ids or (record.source_id,))
        } == {
            source.source_id
            for source in report.sources
            if source.outcome in ("extracted", "partially_extracted")
        }

    def test_ctc01_datum_occurrences_project_to_three_feature_definitions(
        self, ctc01_extraction_report
    ):
        datums = [
            record
            for record in ctc01_extraction_report.records
            if record.source_category == "datum"
        ]

        assert [
            (
                record.label,
                record.part21_id,
                record.source_ids,
                record.datum_contexts,
                record.reference_item_ids,
                record.reference_axis,
            )
            for record in datums
        ] == [
            (
                "A",
                "#34",
                (
                    "datum:0:1:4:2",
                    "datum:0:1:4:6",
                    "datum:0:1:4:12",
                    "datum:0:1:4:16",
                    "datum:0:1:4:19",
                ),
                (
                    "Position.1",
                    "Position.2",
                    "Position surfacic profile.3",
                    "Position surfacic profile.2",
                    "Perpendicularity.1",
                ),
                ("#861",),
                "Z",
            ),
            (
                "B",
                "#35",
                ("datum:0:1:4:3", "datum:0:1:4:7", "datum:0:1:4:13"),
                ("Position.1", "Position.2", "Position surfacic profile.3"),
                ("#854", "#853"),
                "Z",
            ),
            (
                "C",
                "#36",
                ("datum:0:1:4:4", "datum:0:1:4:8", "datum:0:1:4:14"),
                ("Position.1", "Position.2", "Position surfacic profile.3"),
                ("#839", "#840"),
                "Z",
            ),
        ]
        assert all(record.lowering_blockers == () for record in datums)
        assert all(record.ref_bbox is not None for record in datums)

    def test_non_record_dimension_types_fail_closed(self):
        import draftwright.pmi as pmi_module

        presentation = pmi_module._dimension_without_record("dimension:p", 31)
        common_label = pmi_module._dimension_without_record("dimension:c", 30)

        assert presentation is not None and presentation.outcome == "presentation_only"
        assert common_label is not None and common_label.outcome == "not_extracted"
        assert "not implemented" in common_label.reason
        assert pmi_module._dimension_without_record("dimension:d", 15) is None

    def test_dimension_field_failures_are_explicit_partial_outcomes(self, monkeypatch):
        import draftwright.pmi as pmi_module

        class FakeSequence:
            def __init__(self):
                self.items = []

            def Length(self):
                return len(self.items)

            def Value(self, index):
                return self.items[index - 1]

        class FakeArray:
            def Lower(self):
                return 1

            def Value(self, _index):
                return 12.0

        class FakeObject:
            def __init__(self, values, *, is_range=False):
                self.values = values
                self.is_range = is_range

            def GetValue(self):
                raise RuntimeError("scalar unavailable")

            def GetValues(self):
                if isinstance(self.values, Exception):
                    raise self.values
                return self.values

            def GetUpperTolValue(self):
                raise RuntimeError("no upper tolerance")

            def GetLowerTolValue(self):
                raise RuntimeError("no lower tolerance")

            def IsDimWithRange(self):
                return self.is_range

            def GetLowerBound(self):
                raise RuntimeError("lower bound unavailable")

            def GetUpperBound(self):
                raise RuntimeError("upper bound unavailable")

        refs = []

        def get_refs(_label, first, second):
            first.items.extend(refs)
            second.items.extend([])

        monkeypatch.setattr(pmi_module, "TDF_LabelSequence", FakeSequence)
        monkeypatch.setattr(
            pmi_module,
            "XCAFDoc_DimTolTool",
            SimpleNamespace(GetRefShapeLabel_s=get_refs),
        )
        shape_tool = SimpleNamespace(GetShape_s=lambda ref: ref)

        record, reasons = pmi_module._dimension_record(
            object(), FakeObject(FakeArray()), 15, shape_tool, "dimension:a"
        )
        assert record.value == 12.0
        assert reasons == ("referenced geometry is unavailable",)

        refs[:] = [None]
        _record, reasons = pmi_module._dimension_record(
            object(), FakeObject(None), 15, shape_tool, "dimension:b"
        )
        assert reasons == (
            "nominal value is unavailable",
            "one referenced shape is unavailable",
        )

        refs[:] = [SimpleNamespace(IsNull=lambda: False)]
        monkeypatch.setattr(
            pmi_module,
            "_shape_bbox",
            lambda _shape: (_ for _ in ()).throw(RuntimeError("bbox failed")),
        )
        _record, reasons = pmi_module._dimension_record(
            object(),
            FakeObject(RuntimeError("array unavailable")),
            15,
            shape_tool,
            "dimension:c",
        )
        assert "nominal value is unavailable (RuntimeError: array unavailable)" in reasons
        assert "one referenced shape could not be measured (RuntimeError: bbox failed)" in reasons

        refs.clear()
        _record, reasons = pmi_module._dimension_record(
            object(), FakeObject(FakeArray(), is_range=True), 15, shape_tool, "dimension:d"
        )
        assert (
            "lower range bound is unavailable (RuntimeError: lower bound unavailable)" in reasons
        )
        assert (
            "upper range bound is unavailable (RuntimeError: upper bound unavailable)" in reasons
        )

    def test_gtol_field_failures_are_explicit_partial_outcomes(self, monkeypatch):
        import draftwright.pmi as pmi_module

        class FakeSequence:
            def __init__(self):
                self.items = []

            def Length(self):
                return len(self.items)

            def Value(self, index):
                return self.items[index - 1]

        monkeypatch.setattr(pmi_module, "TDF_LabelSequence", FakeSequence)
        broken_tool = SimpleNamespace(
            GetDatumOfTolerLabels_s=lambda _label, _refs: (_ for _ in ()).throw(
                RuntimeError("relationship failed")
            )
        )
        assert pmi_module._datum_references(object(), broken_tool) == (
            (),
            ("datum references are unavailable (RuntimeError: relationship failed)",),
        )

        unreadable = object()
        blank = object()

        def get_datums(_label, refs):
            refs.items.extend((unreadable, blank))

        def set_datum(label):
            if label is unreadable:
                return SimpleNamespace(
                    GetObject=lambda: (_ for _ in ()).throw(RuntimeError("datum failed"))
                )
            return SimpleNamespace(GetObject=lambda: SimpleNamespace(GetName=lambda: None))

        monkeypatch.setattr(pmi_module, "XCAFDoc_Datum", SimpleNamespace(Set_s=set_datum))
        assert pmi_module._datum_references(
            object(), SimpleNamespace(GetDatumOfTolerLabels_s=get_datums)
        ) == (
            (),
            (
                "one datum reference is unavailable (RuntimeError: datum failed)",
                "one datum reference has no letter",
            ),
        )

        monkeypatch.setattr(
            pmi_module,
            "_reference_geometry",
            lambda _label, _shape_tool: ((), None, "?", ()),
        )
        monkeypatch.setattr(
            pmi_module,
            "_datum_references",
            lambda _label, _dim_tol_tool: ((), ()),
        )
        record, reasons = pmi_module._geometric_tolerance_record(
            object(),
            SimpleNamespace(
                GetValue=lambda: 0.1,
                GetTypeOfValue=lambda: 0,
                GetMaterialRequirementModifier=lambda: 0,
                GetZoneModifier=lambda: 0,
                GetValueOfZoneModifier=lambda: 0.0,
                GetMaxValueModifier=lambda: 0.0,
                GetModifiers=lambda: (),
            ),
            99,
            object(),
            object(),
            "geometric_tolerance:unknown",
        )
        assert record.kind == "gtol99"
        assert reasons == ("geometric-tolerance type 99 is unsupported",)

    def test_unpreserved_geometric_tolerance_fields_fail_closed(self):
        import draftwright.pmi as pmi_module

        nondefault = SimpleNamespace(
            GetTypeOfValue=lambda: 1,
            GetMaterialRequirementModifier=lambda: 2,
            GetZoneModifier=lambda: 3,
            GetValueOfZoneModifier=lambda: 0.4,
            GetMaxValueModifier=lambda: 0.5,
            GetModifiers=lambda: (15, 16),
        )
        assert pmi_module._unpreserved_geometric_tolerance_fields(nondefault) == (
            "geometric-tolerance type-of-value 1 is not preserved",
            "geometric-tolerance material-requirement modifier 2 is not preserved",
            "geometric-tolerance zone modifier 3 is not preserved",
            "geometric-tolerance zone-modifier value 0.4 is not preserved",
            "geometric-tolerance maximum-value modifier 0.5 is not preserved",
        )

        def unreadable():
            raise RuntimeError("unreadable")

        broken = SimpleNamespace(
            GetTypeOfValue=unreadable,
            GetMaterialRequirementModifier=unreadable,
            GetZoneModifier=unreadable,
            GetValueOfZoneModifier=unreadable,
            GetMaxValueModifier=unreadable,
            GetModifiers=unreadable,
        )
        reasons = pmi_module._unpreserved_geometric_tolerance_fields(broken)
        assert len(reasons) == 5
        assert all("RuntimeError: unreadable" in reason for reason in reasons)

    def test_xcaf_semantic_name_failures_are_explicit(self):
        import draftwright.pmi as pmi_module

        assert pmi_module._semantic_name(SimpleNamespace(GetSemanticName=lambda: None)) == (
            "",
            "XCAF geometric tolerance has no semantic name",
        )

        def unreadable():
            raise RuntimeError("unreadable")

        assert pmi_module._semantic_name(SimpleNamespace(GetSemanticName=unreadable)) == (
            "",
            "XCAF semantic name is unavailable (RuntimeError: unreadable)",
        )

    @pytest.mark.parametrize(
        ("semantic_name", "facts", "expected_part21_id", "expected_reason"),
        (
            (
                None,
                (),
                "",
                "XCAF geometric tolerance has no semantic name",
            ),
            (
                "Wanted",
                (),
                "",
                "Part21 has no flatness geometric tolerance named 'Wanted'",
            ),
            (
                "Wanted",
                (
                    GeometricToleranceFact(
                        "#9", "Wanted", "flatness", None, "unsupported length unit"
                    ),
                ),
                "#9",
                "unsupported length unit",
            ),
        ),
    )
    def test_gtol_magnitude_overlay_failures_remain_explicit(
        self,
        monkeypatch,
        semantic_name,
        facts,
        expected_part21_id,
        expected_reason,
    ):
        import draftwright.pmi as pmi_module

        monkeypatch.setattr(
            pmi_module,
            "_reference_geometry",
            lambda _label, _shape_tool: ((), None, "?", ()),
        )
        monkeypatch.setattr(
            pmi_module,
            "_datum_references",
            lambda _label, _dim_tol_tool: ((), ()),
        )
        semantic = (
            None if semantic_name is None else SimpleNamespace(ToCString=lambda: semantic_name)
        )
        obj = SimpleNamespace(
            GetValue=lambda: 0.0,
            GetSemanticName=lambda: semantic,
            GetTypeOfValue=lambda: 0,
            GetMaterialRequirementModifier=lambda: 0,
            GetZoneModifier=lambda: 0,
            GetValueOfZoneModifier=lambda: 0.0,
            GetMaxValueModifier=lambda: 0.0,
            GetModifiers=lambda: (),
        )

        record, reasons = pmi_module._geometric_tolerance_record(
            object(), obj, 7, object(), object(), "geometric_tolerance:test", facts
        )

        assert record.value == 0.0
        assert record.part21_id == expected_part21_id
        assert reasons == (f"tolerance magnitude is unavailable ({expected_reason})",)

    def test_report_returns_structured_reader_failures(self, monkeypatch):
        import draftwright.pmi as pmi_module

        monkeypatch.setattr(pmi_module, "_PMI_AVAILABLE", False)
        assert "SetGDTMode" in pmi_module.extract_pmi_report("missing.step").error

        class FakeReader:
            def __init__(self, *, status=1, transfer=True):
                self.status = status
                self.transfer = transfer

            def SetGDTMode(self, _enabled):
                pass

            def SetNameMode(self, _enabled):
                pass

            def ReadFile(self, _path):
                if isinstance(self.status, Exception):
                    raise self.status
                return self.status

            def Transfer(self, _doc):
                if isinstance(self.transfer, Exception):
                    raise self.transfer
                return self.transfer

        monkeypatch.setattr(pmi_module, "_PMI_AVAILABLE", True)
        monkeypatch.setattr(pmi_module, "IFSelect_RetDone", 1)
        monkeypatch.setattr(pmi_module, "TCollection_ExtendedString", lambda value: value)
        monkeypatch.setattr(pmi_module, "TDocStd_Document", lambda _name: object())

        for reader, expected in (
            (FakeReader(status=RuntimeError("read exploded")), "read exploded"),
            (FakeReader(status=0), "ReadFile failed"),
            (FakeReader(transfer=RuntimeError("transfer exploded")), "transfer exploded"),
            (FakeReader(transfer=False), "Transfer failed"),
        ):
            monkeypatch.setattr(pmi_module, "STEPCAFControl_Reader", lambda: reader)
            assert expected in pmi_module.extract_pmi_report("broken.step").error

    def test_part21_failure_keeps_each_xcaf_tolerance_explicitly_partial(self, monkeypatch):
        import draftwright.pmi as pmi_module

        def fail(_step_file):
            raise RuntimeError("mutation: Part21 parser failed")

        monkeypatch.setattr(pmi_module, "read_geometric_tolerances", fail)
        report = pmi_module.extract_pmi_report(CTC01)
        sources = [source for source in report.sources if source.category == "geometric_tolerance"]
        records = [
            record
            for record in report.records
            if record.source_id.startswith("geometric_tolerance:")
        ]

        assert len(sources) == len(records) == 6
        assert all(source.outcome == "partially_extracted" for source in sources)
        assert all(
            "tolerance magnitude is unavailable "
            "(Part21 read failed: RuntimeError: mutation: Part21 parser failed)" in source.reason
            for source in sources
        )
        assert all(record.value == 0.0 and record.part21_id == "" for record in records)

    def test_part21_failure_keeps_each_xcaf_datum_occurrence_explicitly_partial(self, monkeypatch):
        import draftwright.pmi as pmi_module

        def fail(_step_file):
            raise RuntimeError("mutation: Part21 datum parser failed")

        monkeypatch.setattr(pmi_module, "read_datum_occurrences", fail)
        report = pmi_module.extract_pmi_report(CTC01)
        sources = [source for source in report.sources if source.category == "datum"]
        records = [record for record in report.records if record.source_category == "datum"]

        assert len(sources) == len(records) == 11
        assert all(source.outcome == "partially_extracted" for source in sources)
        assert all(
            "Part21 datum read failed: RuntimeError: mutation: Part21 datum parser failed"
            in source.reason
            for source in sources
        )
        assert all(record.part21_id == "" and len(record.source_ids) == 1 for record in records)

    def test_a_failed_exact_topology_guard_keeps_all_datum_definitions_raw(self, monkeypatch):
        import draftwright.pmi as pmi_module
        from draftwright.model import DatumRef, build_pmi_features

        def reject(_self, _definition_id, _item_ids):
            return (), ("mutation: exact imported-topology identity is unavailable",)

        monkeypatch.setattr(pmi_module._DatumTopologyResolver, "resolve", reject)
        report = pmi_module.extract_pmi_report(CTC01)
        sources = [source for source in report.sources if source.category == "datum"]
        records = [record for record in report.records if record.source_category == "datum"]

        assert len(sources) == 11
        assert all(source.outcome == "partially_extracted" for source in sources)
        assert len(records) == 3
        assert all(
            any(
                "exact imported-topology identity is unavailable" in reason
                for reason in record.lowering_blockers
            )
            for record in records
        )
        features = build_pmi_features(records, Box(20, 20, 20).bounding_box())
        assert not any(isinstance(feature, DatumRef) for feature in features)

    def test_a_failed_topology_map_setup_is_a_per_datum_blocker(self, monkeypatch):
        import draftwright.pmi as pmi_module

        def fail():
            raise RuntimeError("mutation: topology map setup failed")

        monkeypatch.setattr(pmi_module, "TopTools_IndexedMapOfShape", fail)
        report = pmi_module.extract_pmi_report(CTC01)
        sources = [source for source in report.sources if source.category == "datum"]

        assert len(sources) == 11
        assert all(source.outcome == "partially_extracted" for source in sources)
        assert all("topology map setup failed" in source.reason for source in sources)

    def test_a_failed_datum_conversion_keeps_every_source_identity(self, monkeypatch):
        import draftwright.pmi as pmi_module

        def fail(_label):
            raise RuntimeError("mutation: datum conversion failed")

        monkeypatch.setattr(pmi_module, "_datum_letter", fail)
        report = pmi_module.extract_pmi_report(CTC01)
        sources = [source for source in report.sources if source.category == "datum"]
        records = [record for record in report.records if record.source_category == "datum"]

        assert len(sources) == 11
        assert records == []
        assert all(source.outcome == "not_extracted" for source in sources)
        assert all(
            source.reason == "RuntimeError: mutation: datum conversion failed"
            for source in sources
        )

    def test_a_failed_conversion_does_not_disappear_from_the_source_denominator(
        self, monkeypatch, ctc01_extraction_report
    ):
        import draftwright.pmi as pmi_module

        target = next(
            source.source_id
            for source in ctc01_extraction_report.sources
            if source.category == "dimension" and source.outcome == "extracted"
        )
        original = pmi_module._dimension_record

        def fail_one(label, obj, type_code, shape_tool, source_id):
            if source_id == target:
                raise RuntimeError("mutation: conversion failed")
            return original(label, obj, type_code, shape_tool, source_id)

        monkeypatch.setattr(pmi_module, "_dimension_record", fail_one)
        mutated = extract_pmi_report(CTC01)
        failed = next(source for source in mutated.sources if source.source_id == target)

        assert {source.source_id for source in mutated.sources} == {
            source.source_id for source in ctc01_extraction_report.sources
        }
        assert len(mutated.records) == len(ctc01_extraction_report.records) - 1
        assert failed.outcome == "not_extracted"
        assert failed.reason == "RuntimeError: mutation: conversion failed"

    def test_a_partial_conversion_keeps_both_its_source_and_record(
        self, monkeypatch, ctc01_extraction_report
    ):
        import draftwright.pmi as pmi_module

        target = next(
            source.source_id
            for source in ctc01_extraction_report.sources
            if source.category == "dimension" and source.outcome == "extracted"
        )
        original = pmi_module._dimension_record

        def partial_one(label, obj, type_code, shape_tool, source_id):
            record, reasons = original(label, obj, type_code, shape_tool, source_id)
            if source_id == target:
                reasons = (*reasons, "mutation: one field was lost")
            return record, reasons

        monkeypatch.setattr(pmi_module, "_dimension_record", partial_one)
        mutated = extract_pmi_report(CTC01)
        partial = next(source for source in mutated.sources if source.source_id == target)

        assert {source.source_id for source in mutated.sources} == {
            source.source_id for source in ctc01_extraction_report.sources
        }
        assert {record.source_id for record in mutated.records} == {
            record.source_id for record in ctc01_extraction_report.records
        }
        assert partial.outcome == "partially_extracted"
        assert partial.reason == "mutation: one field was lost"

    def test_a_failed_gtol_conversion_keeps_its_source_identity(
        self, monkeypatch, ctc01_extraction_report
    ):
        import draftwright.pmi as pmi_module

        target = next(
            source.source_id
            for source in ctc01_extraction_report.sources
            if source.category == "geometric_tolerance"
        )
        original = pmi_module._geometric_tolerance_record

        def fail_one(
            label,
            obj,
            type_code,
            shape_tool,
            dim_tol_tool,
            source_id,
            part21_facts,
            part21_error,
        ):
            if source_id == target:
                raise RuntimeError("mutation: gtol conversion failed")
            return original(
                label,
                obj,
                type_code,
                shape_tool,
                dim_tol_tool,
                source_id,
                part21_facts,
                part21_error,
            )

        monkeypatch.setattr(pmi_module, "_geometric_tolerance_record", fail_one)
        mutated = extract_pmi_report(CTC01)
        failed = next(source for source in mutated.sources if source.source_id == target)

        assert len(mutated.sources) == len(ctc01_extraction_report.sources)
        assert len(mutated.records) == len(ctc01_extraction_report.records) - 1
        assert failed.outcome == "not_extracted"
        assert failed.reason == "RuntimeError: mutation: gtol conversion failed"


# ---------------------------------------------------------------------------
# build_drawing + PMI integration tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ctc01_annotated(tmp_path_factory):
    """One ``pmi='annotate'`` build of CTC-01, shared **read-only** across the
    annotate assertions below — each used to rebuild the ~18 s CTC AP242 import +
    annotate just to check a different read-only property (#153). Any test that
    MUTATES the drawing (add/remove/pin/repair/export-to-a-new-path) must build its
    own, not use this fixture."""
    stem = str(tmp_path_factory.mktemp("ctc01_pmi") / "ctc01")
    return build_drawing(str(CTC01), out=stem, title="CTC-01", number="NIST-01", pmi="annotate")


def _single_source_dimension_drawing(**opts):
    from draftwright import Sheet

    sheet = Sheet(Box(40, 30, 20), title="PMI render mutation", **opts).authored_dimensions()
    sheet.measured_dimension(
        kind="linear",
        value=20,
        label="20",
        dominant_axis="X",
        ref_pts=[(-10, -15, 0), (10, -15, 0)],
        source_id="dimension:mutation",
    )
    return sheet.build()


class TestBuildDrawingPmi:
    def test_explicit_pmi_off_reports_one_ignored_inventory_without_render_failures(
        self, tmp_path
    ):
        stem = str(tmp_path / "ctc01_no_pmi")
        dwg = build_drawing(str(CTC01), out=stem, title="CTC-01", number="NIST-01", pmi="off")
        pmi_names = [n for n in dwg.annotations() if n.startswith("pmi_")]
        assert pmi_names == [], f"pmi='off' should add no pmi_ annotations, got {pmi_names}"
        issues = dwg.lint()
        ignored = [issue for issue in issues if issue.code == "pmi_present_but_ignored"]
        assert len(ignored) == 1
        assert ignored[0].severity == "info"
        assert "pmi='off' was selected" in ignored[0].message
        assert "21 dimensions" in ignored[0].message
        assert "6 geometric tolerances" in ignored[0].message
        assert "11 datum references" in ignored[0].message
        assert "--pmi report" in ignored[0].message
        assert "--pmi annotate" in ignored[0].message
        assert not [issue for issue in issues if issue.code.startswith("pmi_not_")]

        assert dwg.lint_summary()["pmi"] == {
            "mode": "off",
            "sources": 38,
            "by_category": {"datum": 11, "dimension": 21, "geometric_tolerance": 6},
            "extracted": 29,
            "lowered": 0,
            "rendered": 0,
            "dropped": 0,
        }

    def test_default_pmi_off_says_annotation_is_disabled_by_default(self, tmp_path, caplog):
        drawing = build_drawing(CTC01, out=str(tmp_path / "ctc01_default"))
        ignored = [issue for issue in drawing.lint() if issue.code == "pmi_present_but_ignored"]

        assert len(ignored) == 1
        assert "PMI annotation is disabled by default" in ignored[0].message
        assert "pmi='off' was selected" not in ignored[0].message
        assert not [name for name in drawing.annotations() if name.startswith("pmi_")]

        caplog.clear()
        drawing.export(formats=("svg",))
        assert sum("pmi_present_but_ignored" in record.message for record in caplog.records) == 1

    def test_default_pmi_probe_keeps_ap203_quiet(self, tmp_path):
        drawing = build_drawing(CTC01_AP203, out=str(tmp_path / "ctc01_ap203"))

        assert not [issue for issue in drawing.lint() if issue.code.startswith("pmi_")]
        assert "pmi" not in drawing.lint_summary()

    def test_a_top_level_extraction_failure_cannot_become_no_pmi(self, tmp_path, monkeypatch):
        import draftwright.pmi as pmi_module

        step = tmp_path / "plain.step"
        export_step(Box(40, 30, 20), str(step))

        def fail(_step_file):
            raise RuntimeError("mutation: XCAF pass failed")

        monkeypatch.setattr(pmi_module, "extract_pmi_report", fail)
        drawing = build_drawing(step, pmi="annotate")
        issues = [issue for issue in drawing.lint() if issue.code == "pmi_not_extracted"]

        assert len(issues) == 1
        assert issues[0].severity == "error"
        assert "RuntimeError: mutation: XCAF pass failed" in issues[0].message

    def test_pmi_report_extracts_but_does_not_annotate(self, tmp_path):
        """pmi='report' populates a._analysis.pmi but adds no drawing annotations."""
        stem = str(tmp_path / "ctc01_report")
        dwg = build_drawing(str(CTC01), out=stem, title="CTC-01", number="NIST-01", pmi="report")
        a = dwg._analysis
        assert hasattr(a, "pmi"), "_analysis should have .pmi attribute"
        assert len(a.pmi) > 0, "pmi='report' should populate pmi records"
        pmi_names = [n for n in dwg.annotations() if n.startswith("pmi_")]
        assert pmi_names == [], "pmi='report' should not add drawing annotations"
        issues = [issue for issue in dwg.lint() if issue.code == "pmi_not_extracted"]
        assert issues == []
        lowering = [issue for issue in dwg.lint() if issue.code == "pmi_not_lowered"]
        assert len(lowering) == 4
        assert {issue.severity for issue in lowering} == {"info"}
        assert len({issue.source_ids for issue in lowering}) == 4
        assert not [issue for issue in dwg.lint() if issue.code == "pmi_not_rendered"]
        assert dwg.lint_summary()["pmi"] == {
            "mode": "report",
            "sources": 38,
            "by_category": {"datum": 11, "dimension": 21, "geometric_tolerance": 6},
            "extracted": 29,
            "lowered": 25,
            "rendered": 0,
            "dropped": 0,
        }

    def test_pmi_annotate_adds_dims(self, ctc01_annotated):
        """Supported hole PMI renders through canonical bore callouts, not duplicate pmi_ dims."""
        from draftwright.model.ir import ToleranceDecoration

        requirements = [
            (key[0], value)
            for key, value in ctc01_annotated.model().decorations.items()
            if isinstance(value, ToleranceDecoration)
        ]
        assert {source_id for _owner, value in requirements for source_id in value.source_ids} == {
            "dimension:0:1:4:21",
            "dimension:0:1:4:22",
            "dimension:0:1:4:23",
            "dimension:0:1:4:24",
            "dimension:0:1:4:25",
            "dimension:0:1:4:26",
            "dimension:0:1:4:29",
        }
        assert all(ctc01_annotated.registry.names_for_feature(owner) for owner, _ in requirements)
        assert not [name for name in ctc01_annotated.annotations() if name.startswith("pmi_")]

    def test_pmi_annotate_renders_each_nist_range_requirement_with_both_limits(
        self, ctc01_annotated
    ):
        from draftwright.model.ir import ToleranceDecoration

        source_ids = {"dimension:0:1:4:25", "dimension:0:1:4:26"}
        requirements = [
            (key[0], value)
            for key, value in ctc01_annotated.model().decorations.items()
            if isinstance(value, ToleranceDecoration) and set(value.source_ids) == source_ids
        ]
        annotations = dict(ctc01_annotated.iter_annotations())

        assert len(requirements) == 1
        owner, requirement = requirements[0]
        assert owner.diameter == 35.0 and owner.count == 2
        assert requirement.value == 0.2  # 34.8 / 35.2 retained as -0.2 / +0.2
        assert any(
            annotations[name].label == "2× ⌀35 ±0.2 THRU"
            for name in ctc01_annotated.registry.names_for_feature(owner)
        )

    def test_pmi_annotate_reports_each_incomplete_source_record(self, ctc01_annotated):
        issues = [issue for issue in ctc01_annotated.lint() if issue.code == "pmi_not_extracted"]
        assert issues == []
        summary = ctc01_annotated.lint_summary()
        assert summary["passed"] is False
        assert "pmi_not_extracted" not in summary["by_code"]

    def test_pmi_annotate_reports_each_raw_ir_fallback(self, ctc01_annotated):
        from draftwright.model import ControlFrame, PmiFeature

        raw_features = {
            feature.source_id: feature
            for feature in ctc01_annotated.model().features
            if isinstance(feature, PmiFeature)
        }
        issues = [issue for issue in ctc01_annotated.lint() if issue.code == "pmi_not_lowered"]

        frames = {
            feature.source_id: feature
            for feature in ctc01_annotated.model().features
            if isinstance(feature, ControlFrame) and feature.source_id
        }

        assert len(raw_features) == 4
        assert {issue.severity for issue in issues} == {"error"}
        assert {issue.source_ids[0] for issue in issues} == {
            source_id
            for feature in raw_features.values()
            for source_id in (feature.source_ids or (feature.source_id,))
        }
        expected_gtol_datums = {
            "geometric_tolerance:0:1:4:1": ("A", "B", "C"),
            "geometric_tolerance:0:1:4:5": ("A", "B", "C"),
            "geometric_tolerance:0:1:4:11": ("A", "B", "C"),
            "geometric_tolerance:0:1:4:15": ("A",),
            "geometric_tolerance:0:1:4:18": ("A",),
            "geometric_tolerance:0:1:4:20": (),
        }
        assert {source_id: frames[source_id].datums for source_id in expected_gtol_datums} == (
            expected_gtol_datums
        )
        assert all(frames[source_id].origin.ref_pts for source_id in expected_gtol_datums)
        assert {source_id: frames[source_id].part21_id for source_id in expected_gtol_datums} == {
            "geometric_tolerance:0:1:4:1": "#21",
            "geometric_tolerance:0:1:4:5": "#22",
            "geometric_tolerance:0:1:4:11": "#26",
            "geometric_tolerance:0:1:4:15": "#27",
            "geometric_tolerance:0:1:4:18": "#56",
            "geometric_tolerance:0:1:4:20": "#57",
        }
        assert frames["geometric_tolerance:0:1:4:15"].all_around is True

    def test_pmi_annotate_renders_each_proven_datum_feature_once(self, ctc01_annotated):
        from draftwright.model import DatumRef

        datums = [
            feature
            for feature in ctc01_annotated.model().features
            if isinstance(feature, DatumRef)
        ]

        assert {(datum.letter, datum.part21_id) for datum in datums} == {
            ("A", "#34"),
            ("B", "#35"),
            ("C", "#36"),
        }
        assert sum(len(datum.source_ids) for datum in datums) == 11
        assert all(
            len(ctc01_annotated.registry.names_for_feature(datum.origin)) == 1 for datum in datums
        )

    def test_pmi_annotate_accounts_for_each_typed_dimension_at_the_render_seam(
        self, ctc01_annotated
    ):
        from draftwright.model import AuthoredDimension

        authored = [
            feature
            for feature in ctc01_annotated.model().features
            if isinstance(feature, AuthoredDimension)
        ]
        rendered = {
            feature.source_id
            for feature in authored
            if ctc01_annotated.registry.names_for_feature(feature)
        }
        dropped = {
            source_id
            for issue in ctc01_annotated.registry.issues
            if issue.code == "pmi_dropped"
            for source_id in issue.source_ids
        }

        # `dimension:0:1:4:17` is an ANGULAR dimension (label '60 ±0.5'). It used to render
        # through the linear path, producing an annotation whose label states an angle and
        # whose geometry states a length — the #1177 defect, present in a real NIST AP242
        # fixture. It remains materialised and is refused by category. The seven diameter
        # records are consumed once as canonical hole decorations (#1116), so they no longer
        # appear in this authored-feature inventory or compete in the PMI placement pass.
        refused = {
            source_id
            for issue in ctc01_annotated.registry.issues
            if issue.code == "dimension_kind_unsupported"
            for source_id in issue.source_ids
        }
        angular = {
            feature.source_id for feature in authored if feature.dimension_kind == "angular"
        }
        assert len(authored) == 1
        assert len(rendered) == 0
        assert len(dropped) == 0
        assert refused == angular, (
            f"category refusals {refused} do not match the angular records {angular}"
        )
        assert rendered.isdisjoint(dropped) and rendered.isdisjoint(refused)
        assert rendered | dropped | refused == {feature.source_id for feature in authored}
        assert not [issue for issue in ctc01_annotated.lint() if issue.code == "pmi_not_rendered"]
        assert ctc01_annotated.lint_summary()["pmi"] == {
            "mode": "annotate",
            "sources": 38,
            "by_category": {"datum": 11, "dimension": 21, "geometric_tolerance": 6},
            "extracted": 29,
            "lowered": 25,
            # Seven diameter sources now ride canonical bore owners. The angular record is
            # still explicitly refused and the four raw location records remain unlowered.
            "rendered": 24,
            "dropped": 0,
        }

    def test_a_deleted_render_dispatch_is_reported_by_source_identity(self, monkeypatch):
        import draftwright.annotations.from_model as from_model
        from draftwright.linting import lint_pmi_rendering

        original = from_model._renderable_pmi_records
        monkeypatch.setattr(
            from_model,
            "_renderable_pmi_records",
            lambda records: [
                record for record in original(records) if record.source_id != "dimension:mutation"
            ],
        )
        drawing = _single_source_dimension_drawing()
        feature = next(
            feature
            for feature in drawing.model().features
            if feature.source_id == "dimension:mutation"
        )
        issues = lint_pmi_rendering(drawing.model().features, drawing.registry, "annotate")

        assert feature in original([feature])
        assert drawing.registry.names_for_feature(feature) == []
        assert not [issue for issue in drawing.registry.issues if issue.code == "pmi_dropped"]
        assert [(issue.code, issue.severity, issue.source_ids) for issue in issues] == [
            ("pmi_not_rendered", "error", ("dimension:mutation",))
        ]

    def test_a_forced_placement_drop_is_not_reported_as_missing_render_dispatch(self, monkeypatch):
        import draftwright.annotations._common as common
        import draftwright.annotations.from_model as from_model
        from draftwright.linting import lint_pmi_rendering

        original = common.place_strip_candidates

        def reject_source_candidate(*args, **kwargs):
            features = kwargs.get("features", {})
            if any(
                getattr(feature, "source_id", "") == "dimension:mutation"
                for feature in features.values()
            ):
                return list(args[4])
            return original(*args, **kwargs)

        monkeypatch.setattr(common, "place_strip_candidates", reject_source_candidate)
        monkeypatch.setattr(from_model, "place_strip_candidates", reject_source_candidate)
        # Pinned scale + `permissive`: this test FORCES a placement drop, and since #1250 the
        # automatic path answers a drop by looking for a smaller complete scale. Asking for
        # the incomplete drawing explicitly is how a caller says "yes, I want this one" — and
        # it keeps the fixture's subject, which is how the drop is REPORTED, not whether the
        # engine can dodge it.
        drawing = _single_source_dimension_drawing(scale=1.0, scale_policy="permissive")
        feature = next(
            feature
            for feature in drawing.model().features
            if feature.source_id == "dimension:mutation"
        )
        drops = [issue for issue in drawing.registry.issues if issue.code == "pmi_dropped"]

        assert drawing.registry.names_for_feature(feature) == []
        assert [(issue.severity, issue.source_ids) for issue in drops] == [
            ("warning", ("dimension:mutation",))
        ]
        assert [issue.outcome_stage for issue in drops] == ["placement"]
        assert lint_pmi_rendering(drawing.model().features, drawing.registry, "annotate") == []

    def test_pmi_annotate_exports_svg_dxf(self, ctc01_annotated):
        """build_drawing + export with PMI produces valid SVG and DXF files."""
        _p = ctc01_annotated.export(formats=("svg", "dxf"))
        svg_path = _p["svg"]
        dxf_path = _p["dxf"]
        assert Path(svg_path).exists() and Path(svg_path).stat().st_size > 0
        assert Path(dxf_path).exists() and Path(dxf_path).stat().st_size > 0

    def test_pmi_annotation_names_unique(self, ctc01_annotated):
        """All pmi_ annotation names in the drawing are unique."""
        pmi_names = [n for n in ctc01_annotated.annotations() if n.startswith("pmi_")]
        assert len(pmi_names) == len(set(pmi_names)), f"duplicate pmi names: {pmi_names}"


def test_pmi_summary_is_derived_from_registry_outcomes():
    from draftwright.linting import LintIssue, pmi_stage_summary
    from draftwright.pmi import PmiSourceEntity

    drawing = _single_source_dimension_drawing()
    feature = next(
        feature
        for feature in drawing.model().features
        if feature.source_id == "dimension:mutation"
    )
    report = PmiExtractionReport(
        sources=(PmiSourceEntity("dimension:mutation", "dimension", 1, "extracted"),),
        records=(PmiRecord("linear", 1, 20, source_id="dimension:mutation"),),
    )

    placed = pmi_stage_summary(report, drawing.model().features, drawing.registry, "annotate")
    (annotation_name,) = drawing.registry.names_for_feature(feature)
    drawing.remove(annotation_name)
    missing = pmi_stage_summary(report, drawing.model().features, drawing.registry, "annotate")
    drawing.registry.record_issue(
        LintIssue(
            severity="warning",
            code="pmi_dropped",
            message="mutation: solver rejected the candidate",
            source_ids=("dimension:mutation",),
        )
    )
    dropped = pmi_stage_summary(report, drawing.model().features, drawing.registry, "annotate")

    assert placed == {
        "mode": "annotate",
        "sources": 1,
        "by_category": {"dimension": 1},
        "extracted": 1,
        "lowered": 1,
        "rendered": 1,
        "dropped": 0,
    }
    assert missing["rendered"] == 0 and missing["dropped"] == 0
    assert dropped["rendered"] == 0 and dropped["dropped"] == 1


class TestDeclaredModelPmi:
    """#472: a DECLARED-model build (build_drawing(path, model=…)) skips detection, so it carried
    no imported authored annotations and dropped PMI even with pmi='annotate'. _assemble now
    synthesises them from the analysis (the same build_pmi_features detection uses), so PMI
    reproduces on the declared path. (The emitted Sheet-script round-trip is a separate gap —
    import_step strips AP242 PMI.)"""

    def test_declared_model_annotate_matches_auto(self, tmp_path):
        auto = build_drawing(str(CTC01), out=str(tmp_path / "a"), title="P", pmi="annotate")
        declared = build_drawing(
            str(CTC01), out=str(tmp_path / "d"), title="P", model=[], pmi="annotate"
        )

        def source_ids(drawing):
            ids = {
                source_id
                for feature in drawing.model().features
                for source_id in (
                    tuple(getattr(feature, "source_ids", ()))
                    or (
                        (getattr(feature, "source_id", ""),)
                        if getattr(feature, "source_id", "")
                        else ()
                    )
                )
            }
            ids.update(
                source_id
                for value in drawing.model().decorations.values()
                for source_id in getattr(value, "source_ids", ())
            )
            return ids

        # With geometry features available the automatic path correlates hole requirements;
        # an empty declared model cannot, so it keeps them materialised. Both still account for
        # every extracted source identity — #472's no-loss invariant.
        assert source_ids(auto) == source_ids(declared)

    def test_declared_model_pmi_off_stays_clean(self, tmp_path):
        # the synthesis is gated on pmi_mode == 'annotate' — a declared build without PMI stays 0
        dwg = build_drawing(str(CTC01), out=str(tmp_path / "off"), title="P", model=[])
        assert [n for n in dwg.annotations() if n.startswith("pmi_")] == []


def test_build_pmi_features_mirrors_detection(ctc01_extraction_report):
    """build_pmi_features (shared by build_part_model and the declared-model synthesis) builds one
    imported drafting annotation per record; both callers construct them identically (#472)."""
    from draftwright.model import (
        AuthoredDimension,
        ControlFrame,
        DatumRef,
        PmiFeature,
        build_pmi_features,
    )

    recs = ctc01_extraction_report.records
    dim_kinds = {
        "linear",
        "diameter",
        "radius",
        "angular",
        "curved_dist",
        "oriented",
        "curve_length",
        "thickness",
    }
    dims = [r for r in recs if r.kind in dim_kinds]
    from build123d import import_step

    bbox = import_step(CTC01).bounding_box()
    feats = build_pmi_features(recs, bbox)
    assert len(feats) == len(recs)
    authored = [f for f in feats if isinstance(f, AuthoredDimension)]
    raw = [f for f in feats if isinstance(f, PmiFeature)]
    frames = [f for f in feats if isinstance(f, ControlFrame)]
    datum_refs = [f for f in feats if isinstance(f, DatumRef)]
    assert len(authored) == 8
    # Four location dimensions remain explicit raw fallbacks; all six supported geometric
    # tolerances lower to the existing control-frame drafting concept.
    assert len(raw) == 4
    assert len(frames) == 6
    assert len(datum_refs) == 3
    assert all(f.kind == "authored_dimension" for f in authored)
    assert all(f.kind == "pmi" for f in raw)
    assert {
        source_id
        for feature in feats
        for source_id in (getattr(feature, "source_ids", ()) or (feature.source_id,))
    } == {source_id for record in recs for source_id in (record.source_ids or (record.source_id,))}
    assert {(datum.letter, datum.part21_id) for datum in datum_refs} == {
        ("A", "#34"),
        ("B", "#35"),
        ("C", "#36"),
    }
    assert {datum.part21_id: datum.source_ids for datum in datum_refs} == {
        record.part21_id: record.source_ids for record in recs if record.source_category == "datum"
    }
    assert {feature.source_id: feature.part21_id for feature in frames if feature.part21_id} == {
        record.source_id: record.part21_id
        for record in recs
        if record.source_category == "geometric_tolerance" and record.part21_id
    }
    assert next(frame for frame in frames if frame.part21_id == "#27").all_around is True
    # A dimensional PMI record's measured semantics ride onto its authored dimension verbatim.
    assert {f.label for f in authored} >= {r.label for r in dims}
    for r in dims:
        assert any(
            f.source_id == r.source_id
            and f.label == r.label
            and f.upper_tol == r.upper_tol
            and f.lower_tol == r.lower_tol
            and f.lower_bound == r.lower_bound
            and f.upper_bound == r.upper_bound
            for f in authored
        )
    assert build_pmi_features(None, bbox) == []  # None/empty → no features


def test_a_supported_dimension_routed_to_raw_ir_is_reported_by_source_identity(
    ctc01_extraction_report, monkeypatch
):
    from dataclasses import replace

    from build123d import import_step

    import draftwright.model.detect as detect_module
    from draftwright.annotations.from_model import _renderable_pmi_records
    from draftwright.linting import lint_pmi_lowering
    from draftwright.model import PmiFeature

    target = next(record for record in ctc01_extraction_report.records if record.kind == "angular")
    bbox = import_step(CTC01).bounding_box()
    baseline = detect_module.build_pmi_features(ctc01_extraction_report.records, bbox)
    compatibility_record = replace(target, source_id="")
    compatibility_report = replace(ctc01_extraction_report, records=(compatibility_record,))
    assert lint_pmi_lowering(compatibility_report, (), "annotate") == []
    assert target.source_id not in {
        issue.source_ids[0]
        for issue in lint_pmi_lowering(ctc01_extraction_report, baseline, "annotate")
    }
    missing = [feature for feature in baseline if feature.source_id != target.source_id]
    missing_issue = next(
        issue
        for issue in lint_pmi_lowering(ctc01_extraction_report, missing, "annotate")
        if issue.source_ids == (target.source_id,)
    )
    assert "did not produce a typed IR feature" in missing_issue.message

    monkeypatch.setattr(
        detect_module,
        "AUTHORED_DIMENSION_KINDS",
        detect_module.AUTHORED_DIMENSION_KINDS - {"angular"},
    )
    mutated = detect_module.build_pmi_features(ctc01_extraction_report.records, bbox)
    raw = next(feature for feature in mutated if feature.source_id == target.source_id)
    issues = lint_pmi_lowering(ctc01_extraction_report, mutated, "annotate")
    issue = next(issue for issue in issues if issue.source_ids == (target.source_id,))

    assert isinstance(raw, PmiFeature)
    assert raw not in _renderable_pmi_records(mutated)
    assert issue.code == "pmi_not_lowered"
    assert issue.severity == "error"
    assert target.source_id in issue.message


def test_render_pmi_reports_unrecognized_bore_axis_as_not_rendered_without_crashing():
    # #638 review: the bore ø/R placement became a `_bore[axis]` table lookup. A diameter
    # record whose dominant_axis doesn't resolve to X/Y/Z must report the missing renderer,
    # not claim placement capacity rejected it or KeyError-crash the whole build.
    from types import SimpleNamespace

    from build123d import Box

    from draftwright import build_drawing
    from draftwright.annotations._common import PlacementContext
    from draftwright.annotations.from_model import render_pmi
    from draftwright.linting import lint_pmi_rendering
    from draftwright.model import AuthoredDimension
    from draftwright.model.ir import Frame
    from draftwright.registry import AnnotationRegistry

    dwg = build_drawing(Box(40, 30, 20), number="X")
    bogus = AuthoredDimension(
        frame=Frame((0.0, 0.0, 0.0), "z"),
        dimension_kind="diameter",
        value=5.0,
        label="ø5",
        dominant_axis="Q",  # not X/Y/Z → matches no bore config
        ref_bbox=(0.0, 0.0, 0.0, 5.0, 1.0, 1.0),
        ref_pts=((0.0, 0.0, 0.0), (5.0, 0.0, 0.0)),
        source_id="dimension:bogus-axis",
    )
    model = SimpleNamespace(features=[bogus])
    # The pmi_dropped lint now routes through the ctx's registry/coverage (#639); wire them to
    # the drawing's so the drop lands on dwg's build issues.
    ctx = PlacementContext(registry=dwg.registry, coverage=dwg.coverage)
    analysis = dwg._analysis
    n = render_pmi(dwg, model, analysis, ctx=ctx)  # must not raise
    assert n == 0
    assert not any(i.code == "pmi_dropped" for i in dwg.registry.issues)
    recorded = [issue for issue in dwg.registry.issues if issue.code == "pmi_not_rendered"]
    assert [(issue.severity, issue.source_ids) for issue in recorded] == [
        ("error", ("dimension:bogus-axis",))
    ]
    issues = lint_pmi_rendering(model.features, dwg.registry, "annotate")
    assert issues == []

    # Removing the false capacity label must not make a source-less malformed declaration
    # disappear either. Reuse the same analysis so this test does not widen private test reads.
    record = AuthoredDimension(
        frame=Frame((0.0, 0.0, 0.0), "z"),
        dimension_kind="diameter",
        value=5.0,
        label="ø5",
        dominant_axis="Z",
        ref_pts=((0.0, 0.0, 0.0), (5.0, 0.0, 0.0)),
    )
    registry = AnnotationRegistry()
    source_less_ctx = PlacementContext(registry=registry, coverage=dwg.coverage)

    assert render_pmi(dwg, SimpleNamespace(features=[record]), analysis, ctx=source_less_ctx) == 0
    assert [(issue.severity, issue.code, issue.source_ids) for issue in registry.issues] == [
        ("warning", "pmi_not_rendered", ()),
    ]
