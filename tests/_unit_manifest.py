"""Single source of truth for the pure-logic unit tier."""

from pathlib import Path

UNIT_MODULES = frozenset(
    {
        "test_burden_report.py",
        "test_corpus_cover.py",
        "test_counting_calls.py",
        "test_fit_calculations.py",
        "test_inspection_contract.py",
        "test_label_provenance.py",
        "test_layout.py",
        "test_issue_1312_engine_costs.py",
        "test_issue_1332_overlap_remedy.py",
        "test_recognition_evidence_schema.py",
        "test_lint_ink_overlap.py",
        "test_linting.py",
        "test_pmi_part21.py",
        "test_principal_profile_classifier.py",
        "test_quality_components.py",
        "test_recogniser_adoption.py",
        "test_recogniser_policy.py",
        "test_registry.py",
    }
)


def unit_paths(tests_dir: Path) -> list[str]:
    """Return stable pytest paths for every unit module."""
    return [str(tests_dir / name) for name in sorted(UNIT_MODULES)]
