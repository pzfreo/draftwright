"""Tests for PMI extraction and annotation (Phase 1–3)."""

from pathlib import Path

import pytest
from build123d import Box, export_step

from draftwright import build_drawing, extract_pmi
from draftwright.pmi import _PMI_AVAILABLE, PmiRecord

FIXTURES = Path(__file__).parent / "fixtures"
CTC01 = FIXTURES / "nist_ctc_01_asme1_ap242.stp"

pytestmark = pytest.mark.skipif(not _PMI_AVAILABLE, reason="OCP GDT support not available")


# ---------------------------------------------------------------------------
# extract_pmi unit tests
# ---------------------------------------------------------------------------


class TestExtractPmi:
    def test_nist_ctc01_returns_records(self):
        recs = extract_pmi(CTC01)
        assert len(recs) > 0

    def test_nist_ctc01_dim_count(self):
        recs = extract_pmi(CTC01)
        dims = [r for r in recs if r.kind not in ("gtol", "datum")]
        assert len(dims) >= 8, f"expected ≥8 dim records, got {len(dims)}"

    def test_nist_ctc01_gtol_count(self):
        recs = extract_pmi(CTC01)
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

    def test_usable_dims_have_positive_value(self):
        recs = extract_pmi(CTC01)
        usable = [r for r in recs if r.value > 0 and len(r.ref_pts) >= 2]
        assert len(usable) >= 4, f"expected ≥4 usable dims, got {len(usable)}"

    def test_diameter_labels_prefixed(self):
        recs = extract_pmi(CTC01)
        diameters = [r for r in recs if r.kind == "diameter" and r.value > 0]
        assert len(diameters) >= 1
        for d in diameters:
            assert d.label.startswith("ø"), f"diameter label missing ø: {d.label!r}"

    def test_ref_pts_are_3d_tuples(self):
        recs = extract_pmi(CTC01)
        for r in recs:
            for pt in r.ref_pts:
                assert len(pt) == 3, f"ref_pt should be 3-tuple, got {pt!r}"

    def test_dominant_axis_set(self):
        recs = extract_pmi(CTC01)
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

    def test_records_are_pmi_record_instances(self):
        recs = extract_pmi(CTC01)
        for r in recs:
            assert isinstance(r, PmiRecord)


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
        pmi_names = [n for n in dwg._named if n.startswith("pmi_")]
        assert pmi_names == [], f"pmi='off' should add no pmi_ annotations, got {pmi_names}"

    def test_pmi_report_extracts_but_does_not_annotate(self, tmp_path):
        """pmi='report' populates a._analysis.pmi but adds no drawing annotations."""
        stem = str(tmp_path / "ctc01_report")
        dwg = build_drawing(str(CTC01), out=stem, title="CTC-01", number="NIST-01", pmi="report")
        a = dwg._analysis
        assert hasattr(a, "pmi"), "_analysis should have .pmi attribute"
        assert len(a.pmi) > 0, "pmi='report' should populate pmi records"
        pmi_names = [n for n in dwg._named if n.startswith("pmi_")]
        assert pmi_names == [], "pmi='report' should not add drawing annotations"

    def test_pmi_annotate_adds_dims(self, ctc01_annotated):
        """pmi='annotate' adds at least one pmi_ dimension to the drawing."""
        pmi_names = [n for n in ctc01_annotated._named if n.startswith("pmi_")]
        assert len(pmi_names) >= 1, f"expected ≥1 pmi_ annotation, got {pmi_names}"

    def test_pmi_annotate_drawing_lint_clean(self, ctc01_annotated):
        """Drawing with PMI annotations passes lint with no errors."""
        issues = ctc01_annotated.lint()
        errors = [i for i in issues if i.severity == "error"]
        assert errors == [], f"lint errors with PMI: {[str(i) for i in errors]}"

    def test_pmi_annotate_exports_svg_dxf(self, ctc01_annotated):
        """build_drawing + export with PMI produces valid SVG and DXF files."""
        svg_path, dxf_path = ctc01_annotated.export()
        assert Path(svg_path).exists() and Path(svg_path).stat().st_size > 0
        assert Path(dxf_path).exists() and Path(dxf_path).stat().st_size > 0

    def test_pmi_annotation_names_unique(self, ctc01_annotated):
        """All pmi_ annotation names in the drawing are unique."""
        pmi_names = [n for n in ctc01_annotated._named if n.startswith("pmi_")]
        assert len(pmi_names) == len(set(pmi_names)), f"duplicate pmi names: {pmi_names}"


class TestDeclaredModelPmi:
    """#472: a DECLARED-model build (build_drawing(path, model=…)) skips detection, so it carried
    no PmiFeatures and dropped PMI even with pmi='annotate'. _assemble now synthesises them from
    the analysis (the same build_pmi_features detection uses), so PMI reproduces on the declared
    path. (The emitted Sheet-script round-trip is a separate gap — import_step strips AP242 PMI.)"""

    def test_declared_model_annotate_matches_auto(self, tmp_path):
        auto = build_drawing(str(CTC01), out=str(tmp_path / "a"), title="P", pmi="annotate")
        declared = build_drawing(
            str(CTC01), out=str(tmp_path / "d"), title="P", model=[], pmi="annotate"
        )
        n_auto = sum(1 for n in auto._named if n.startswith("pmi_"))
        n_decl = sum(1 for n in declared._named if n.startswith("pmi_"))
        assert n_auto >= 1
        assert n_decl == n_auto  # declared path reproduces the auto PMI dims (was 0 before #472)

    def test_declared_model_pmi_off_stays_clean(self, tmp_path):
        # the synthesis is gated on pmi_mode == 'annotate' — a declared build without PMI stays 0
        dwg = build_drawing(str(CTC01), out=str(tmp_path / "off"), title="P", model=[])
        assert [n for n in dwg._named if n.startswith("pmi_")] == []


def test_build_pmi_features_mirrors_detection():
    """build_pmi_features (shared by build_part_model and the declared-model synthesis) builds one
    PmiFeature per record; both callers must construct them identically (#472)."""
    from draftwright.model import build_pmi_features

    recs = extract_pmi(CTC01)
    dims = [r for r in recs if r.kind not in ("gtol", "datum")]
    from build123d import import_step

    bbox = import_step(CTC01).bounding_box()
    feats = build_pmi_features(recs, bbox)
    assert len(feats) == len(recs)
    assert all(f.kind == "pmi" for f in feats)
    # a dim record's value/label ride onto its PmiFeature verbatim
    assert {f.label for f in feats} >= {r.label for r in dims}
    assert build_pmi_features(None, bbox) == []  # None/empty → no features
