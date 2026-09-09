"""Nonempty source catalogs for every current family survive member intent changes."""

from dataclasses import replace
from importlib import import_module
from pathlib import Path

import pytest
from build123d import RegularPolygon, extrude

from draftwright.builder import _detect_part_model_analysis
from draftwright.linting.requirements import recognized_requirement_outcomes
from draftwright.model.compiled import compile_dimensions
from draftwright.registry import AnnotationRegistry
from draftwright.reporting import (
    ReportUnavailableError,
    build_requirement_catalog,
    match_requirement_catalog,
)

# Reuse each family's existing real geometric reproducer. The roster assertion below
# requires an explicit nonempty example when the shared collector gains a family.
_CASES = (
    ("chamfers", "chamfer-planar-z.step", None),
    ("fillets", "fillet-planar-z.step", None),
    ("flats", "flat-lone-d.step", None),
    ("grooves", "groove-lone-z.step", None),
    ("hole_patterns", "pattern-grid.step", None),
    ("holes", "blind-hole.step", None),
    ("pads", "pad-x-positive.step", None),
    ("plates", "plate-t-yz.step", None),
    ("pockets", "pocket-lone.step", None),
    ("pocket_patterns", "pocket-pattern-linear.step", None),
    ("polygonal_bosses", "polygonal-boss-x.step", None),
    ("polygonal_stock", "polygonal-stock-x.step", None),
    ("through_steps", "plate-t-yz.step", None),
    ("turned_steps", "turned-step-axis-x.step", None),
    ("channels", "plate-u-additive.step", None),
    ("oriented_slots", "test_issue_1432_oriented_slot_semantics", "_part"),
    ("blends", "test_issue_1433_blend_semantics", "_single_blend"),
    ("paired_ramp_steps", "test_issue_1382_paired_ramp_semantics", "_paired_ramp_part"),
    ("circular_blind_steps", "test_issue_1382_circular_blind_step_semantics", "_part"),
    ("round_bottom_blind_slots", "test_issue_1421_round_bottom_blind_slot_completeness", "_part"),
    ("rectangular_blind_slots", "test_issue_1421_rectangular_blind_slot_completeness", "_part"),
    ("slots", "test_slot_completeness", "_off_centre_slot"),
    ("slot_patterns", "test_slot_completeness", "_slot_row"),
    ("section_recesses", "test_issue_1438_report_projection", "_passage_part"),
    ("outer_profile_angles", None, None),
)


@pytest.fixture(scope="module", params=_CASES, ids=[case[0] for case in _CASES])
def family_intake(request):
    family, source, factory = request.param
    if factory is not None:
        part = getattr(import_module(source), factory)()
    elif source is not None:
        part = Path(__file__).parent / "fixtures/evaluation" / source
    else:
        part = extrude(RegularPolygon(30, 3), amount=4)
    model, analysis = _detect_part_model_analysis(part)
    return family, model, analysis


def _catalog(model, analysis, outcomes=None):
    return build_requirement_catalog(
        evidence=analysis.recognition_evidence,
        ownership=analysis.recognition_ownership,
        model=model,
        part=analysis.part,
        requirement_outcomes=outcomes,
    )


def _outcomes(model, analysis):
    return recognized_requirement_outcomes(
        analysis.recognition,
        model.features,
        AnnotationRegistry(),
        compile_dimensions(model).diagnostics,
        part=analysis.part,
        evidence=analysis.recognition_evidence,
        ownership=analysis.recognition_ownership,
        datum=next((datum for datum in model.datums if datum.id == "datum_xy"), None),
    )


def test_all_current_families_have_a_nonempty_stable_document_catalog(family_intake):
    family, model, analysis = family_intake
    baseline = _catalog(model, analysis)
    assert set(baseline.families) == {case[0] for case in _CASES}
    required = [row for row in baseline.requirements if row.family == family]
    assert required, f"{family} reproducer no longer exercises its requirement ledger"
    empty = replace(model, authored_dimensions=())
    current = _catalog(empty, analysis, _outcomes(empty, analysis))
    assert len(match_requirement_catalog(baseline, current)) == len(baseline.requirements)
    if family == "slot_patterns":
        records = tuple(
            analysis.recognition_evidence.record(ref)
            for ref in analysis.recognition_evidence.features
        )
        exclusions = [
            row.intrinsic_exclusion for row in baseline.requirements if row.family == "plates"
        ]
        assert exclusions and all(exclusion is not None for exclusion in exclusions)
        for exclusion in exclusions:
            assert exclusion.reason_code == "slot_pattern_owns_material_web"
            assert len(exclusion.source_records) == 4
            assert all(
                any(source is record for record in records) for source in exclusion.source_records
            )


def test_member_cannot_erase_supported_family_obligations(family_intake):
    family, model, analysis = family_intake
    baseline = _catalog(model, analysis)
    outcomes = dict(_outcomes(model, analysis))
    outcomes[family] = ()
    if family == "section_recesses":
        # The accepted unsupported occurrence independently synthesizes its opaque
        # obligation even when this producer emits no row. It must not disappear.
        current = _catalog(model, analysis, outcomes)
        aligned = match_requirement_catalog(baseline, current)
        assert any(row.family == family and row.unsupported for row in aligned)
    else:
        with pytest.raises(ReportUnavailableError):
            match_requirement_catalog(baseline, _catalog(model, analysis, outcomes))
