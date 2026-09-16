"""Coverage selection keeps fast PRs cheap without weakening broad changes."""

from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / "scripts/coverage-mode"
LOADER = SourceFileLoader("coverage_mode", str(SCRIPT))
SPEC = spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
coverage_mode = module_from_spec(SPEC)
LOADER.exec_module(coverage_mode)
Change = coverage_mode.Change


@pytest.mark.parametrize(
    "changes",
    [
        [Change("A", ("tests/test_new_contract.py",))],
        [Change("A", ("tests/fixtures/new.step",))],
        [Change("M", ("tests/test_export.py",))],
        [Change("D", ("tests/fixtures/source.step",))],
        [Change("M", (".github/workflows/ci.yml",))],
        [Change("M", ("docs/guide.md",))],
    ],
)
def test_additive_source_neutral_changes_skip_coverage(changes):
    assert coverage_mode.classify(changes) == "skip"


@pytest.mark.parametrize(
    "changes",
    [
        [Change("M", ("src/draftwright/export.py",))],
        [Change("M", ("src/draftwright/annotations/from_model.py",))],
        [Change("M", ("src/draftwright/reporting.py",))],
        [Change("D", ("src/draftwright/export.py",))],
        [Change("M", ("src/draftwright/builder.py",))],
        [Change("A", ("src/draftwright/new_area.py",))],
        [Change("R", ("src/draftwright/export.py", "src/draftwright/exporter.py"))],
    ],
)
def test_every_production_change_uses_changed_area_coverage(changes):
    assert coverage_mode.classify(changes) == "changed"


def test_full_coverage_label_overrides_a_source_neutral_diff():
    changes = [Change("A", ("tests/fixtures/new.step",))]

    assert coverage_mode.classify(changes, force_full=True) == "full"
