"""Tests for PMI extraction and annotation (Phase 1–3)."""

from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest
from build123d import Box, export_step

from draftwright import build_drawing, extract_pmi, extract_pmi_report
from draftwright.pmi import _PMI_AVAILABLE, PmiExtractionReport, PmiRecord

FIXTURES = Path(__file__).parent / "fixtures"
CTC01 = FIXTURES / "nist_ctc_01_asme1_ap242.stp"

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
                ("geometric_tolerance", "partially_extracted"): 6,
                ("datum", "not_extracted"): 11,
            }
        )
        assert {record.source_id for record in report.records} == {
            source.source_id
            for source in report.sources
            if source.outcome in ("extracted", "partially_extracted")
        }

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
            def __init__(self, values):
                self.values = values

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

        def fail_one(obj, type_code, source_id):
            if source_id == target:
                raise RuntimeError("mutation: gtol conversion failed")
            return original(obj, type_code, source_id)

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


class TestBuildDrawingPmi:
    def test_pmi_off_leaves_drawing_unchanged(self, tmp_path):
        """pmi='off' produces an identical drawing to not passing pmi at all."""
        stem = str(tmp_path / "ctc01_no_pmi")
        dwg = build_drawing(str(CTC01), out=stem, title="CTC-01", number="NIST-01", pmi="off")
        pmi_names = [n for n in dwg.annotations() if n.startswith("pmi_")]
        assert pmi_names == [], f"pmi='off' should add no pmi_ annotations, got {pmi_names}"
        assert not [issue for issue in dwg.lint() if issue.code == "pmi_not_extracted"]

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
        assert len(issues) == 17
        assert {issue.severity for issue in issues} == {"info"}
        assert len({issue.source_ids for issue in issues}) == 17

    def test_pmi_annotate_adds_dims(self, ctc01_annotated):
        """pmi='annotate' adds at least one pmi_ dimension to the drawing."""
        pmi_names = [n for n in ctc01_annotated.annotations() if n.startswith("pmi_")]
        assert len(pmi_names) >= 1, f"expected ≥1 pmi_ annotation, got {pmi_names}"

    def test_pmi_annotate_reports_each_incomplete_source_record(self, ctc01_annotated):
        issues = [issue for issue in ctc01_annotated.lint() if issue.code == "pmi_not_extracted"]
        assert len(issues) == 17
        assert {issue.severity for issue in issues} == {"error"}
        assert len({issue.source_ids for issue in issues}) == 17
        assert all(issue.source_ids[0] in issue.message for issue in issues)
        assert (
            sum("datum extraction is not implemented" in issue.message for issue in issues) == 11
        )
        assert (
            sum("only the characteristic type is preserved" in issue.message for issue in issues)
            == 6
        )
        summary = ctc01_annotated.lint_summary()
        assert summary["passed"] is False
        assert summary["by_code"]["pmi_not_extracted"] == 17
        assert all(
            "source_ids" in issue
            for issue in summary["issues"]
            if issue["code"] == "pmi_not_extracted"
        )

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
        auto_pmi = {n for n in auto.annotations() if n.startswith("pmi_")}
        decl_pmi = {n for n in declared.annotations() if n.startswith("pmi_")}
        assert auto_pmi
        # The declared path has fewer auto-generated dimensions competing for strip capacity,
        # so it may place extra authored PMI. The #472 invariant is no loss: every PMI dim the
        # detected path placed must also be reproduced by the declared path.
        assert auto_pmi <= decl_pmi

    def test_declared_model_pmi_off_stays_clean(self, tmp_path):
        # the synthesis is gated on pmi_mode == 'annotate' — a declared build without PMI stays 0
        dwg = build_drawing(str(CTC01), out=str(tmp_path / "off"), title="P", model=[])
        assert [n for n in dwg.annotations() if n.startswith("pmi_")] == []


def test_build_pmi_features_mirrors_detection(ctc01_extraction_report):
    """build_pmi_features (shared by build_part_model and the declared-model synthesis) builds one
    imported drafting annotation per record; both callers construct them identically (#472)."""
    from draftwright.model import AuthoredDimension, PmiFeature, build_pmi_features

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
    assert authored
    assert raw  # GD&T/datum AP242 records are explicit raw fallbacks until concept lowering.
    assert all(f.kind == "authored_dimension" for f in authored)
    assert all(f.kind == "pmi" for f in raw)
    # a dimensional PMI record's value/label ride onto its authored dimension verbatim
    assert {f.label for f in authored} >= {r.label for r in dims}
    for r in dims:
        assert any(
            f.label == r.label and f.upper_tol == r.upper_tol and f.lower_tol == r.lower_tol
            for f in authored
        )
    assert build_pmi_features(None, bbox) == []  # None/empty → no features


def test_render_pmi_drops_unrecognized_bore_axis_without_crashing():
    # #638 review: the bore ø/R placement became a `_bore[axis]` table lookup. A diameter
    # record whose dominant_axis doesn't resolve to X/Y/Z must still drop gracefully — as the
    # old Z/X/Y if-chain did by falling through — not KeyError-crash the whole build.
    from types import SimpleNamespace

    from build123d import Box

    from draftwright import build_drawing
    from draftwright.annotations._common import PlacementContext
    from draftwright.annotations.from_model import render_pmi
    from draftwright.model import AuthoredDimension
    from draftwright.model.ir import Frame

    dwg = build_drawing(Box(40, 30, 20), number="X")
    bogus = AuthoredDimension(
        frame=Frame((0.0, 0.0, 0.0), "z"),
        dimension_kind="diameter",
        value=5.0,
        label="ø5",
        dominant_axis="Q",  # not X/Y/Z → matches no bore config
        ref_bbox=(0.0, 0.0, 0.0, 5.0, 1.0, 1.0),
        ref_pts=((0.0, 0.0, 0.0), (5.0, 0.0, 0.0)),
    )
    model = SimpleNamespace(features=[bogus])
    # The pmi_dropped lint now routes through the ctx's registry/coverage (#639); wire them to
    # the drawing's so the drop lands on dwg's build issues.
    ctx = PlacementContext(registry=dwg.registry, coverage=dwg.coverage)
    n = render_pmi(dwg, model, dwg._analysis, ctx=ctx)  # must not raise
    assert n == 0
    assert any(i.code == "pmi_dropped" for i in dwg.registry.issues)  # graceful drop recorded
