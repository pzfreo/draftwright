"""Executable manifests for pull-request, full, and scheduled test tiers."""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

from _unit_manifest import UNIT_MODULES


@dataclass(frozen=True)
class ContractGroup:
    """Production paths and the complete fast contract modules that protect them."""

    source_patterns: tuple[str, ...]
    test_patterns: tuple[str, ...]


CONTRACT_GROUPS = {
    "recognition": ContractGroup(
        (
            "src/draftwright/recogn*",
            "src/draftwright/model/detect.py",
            "src/draftwright/*_contract.py",
        ),
        (
            "test_*recogn*.py",
            "test_*ownership.py",
            "test_*classification.py",
            "test_*completeness_evidence.py",
        ),
    ),
    "compilation": ContractGroup(
        (
            "src/draftwright/model/*",
            "src/draftwright/intents.py",
            "src/draftwright/measurement_support.py",
            "src/draftwright/sheet.py",
            "src/draftwright/sheet_emit.py",
        ),
        (
            "test_*dimension*.py",
            "test_*callout*.py",
            "test_*lowering*.py",
            "test_compiled_plan_boundary.py",
            "test_declare.py",
            "test_sheet*.py",
        ),
    ),
    "placement": ContractGroup(
        (
            "src/draftwright/annotations/*",
            "src/draftwright/annotate.py",
            "src/draftwright/compose.py",
            "src/draftwright/layout.py",
            "src/draftwright/projection.py",
            "src/draftwright/view_plan.py",
        ),
        (
            "test_*corridor*.py",
            "test_*layout*.py",
            "test_*placement*.py",
            "test_*strip*.py",
            "test_*view*.py",
            "test_deferred_edits.py",
        ),
    ),
    "reporting": ContractGroup(
        (
            "src/draftwright/audit.py",
            "src/draftwright/document*.py",
            "src/draftwright/inspection*.py",
            "src/draftwright/linting/*",
            "src/draftwright/reporting.py",
        ),
        (
            "test_*audit*.py",
            "test_*document*.py",
            "test_*inspection*.py",
            "test_*lint*.py",
            "test_*report*.py",
        ),
    ),
    "export": ContractGroup(
        (
            "src/draftwright/cli.py",
            "src/draftwright/drawing.py",
            "src/draftwright/export.py",
            "src/draftwright/make_drawing.py",
        ),
        (
            "test_*cli*.py",
            "test_*export*.py",
            "test_*searchable_pdf*.py",
            "test_build_drawing_entrypoints.py",
            "test_make_drawing_entrypoints.py",
        ),
    ),
}

# These coordinators cross the area boundaries above. A change to one must run every group.
BROAD_SOURCE_PATTERNS = (
    "src/draftwright/_core.py",
    "src/draftwright/analysis.py",
    "src/draftwright/builder.py",
    "src/draftwright/drawing.py",
    "src/draftwright/sheet.py",
)

# These run on every code-bearing pull request before any change-area expansion.
PR_CORE_MODULES = frozenset(
    {
        "test_build_drawing_entrypoints.py",
        "test_compiled_plan_boundary.py",
        "test_declared_recognition_gate.py",
        "test_e2e_slice.py",
        "test_export_formats.py",
    }
)

# Repository-policy guards scan source, tests, history, or CI configuration rather than one
# production unit. They remain mandatory on every PR without lengthening the inner unit loop.
PR_POLICY_MODULES = frozenset(
    {
        "test_api_docs.py",
        "test_architecture_docs.py",
        "test_carve_free_position_callers.py",
        "test_clone_budget.py",
        "test_deprecation_dates.py",
        "test_import_boundaries.py",
        "test_private_test_attr_reads.py",
        "test_private_test_imports.py",
        "test_suite_shape.py",
        "test_tier_manifest.py",
        "test_version_bump_ci.py",
        "test_workflows.py",
    }
)

# A critical public contract may run in PR/full or in all tiers, but never scheduled only.
CRITICAL_CONTRACT_MODULES = frozenset(
    {
        "test_declared_recognition_gate.py",
        "test_export_formats.py",
        "test_recogniser_contract.py",
        "test_report_writer.py",
        "test_scale_selection.py",
    }
)

FULL_EXPRESSION = "not slow and not scheduled"
SCHEDULED_EXPRESSION = "slow or scheduled"


def _matches(path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch(path, pattern) for pattern in patterns)


def selected_groups(changed_paths: list[str]) -> frozenset[str]:
    """Return every contract group selected by changed production paths.

    Unknown production modules select all groups. That conservative fallback makes adding a new
    production area safe before its narrower ownership is added here.
    """
    selected: set[str] = set()
    for path in changed_paths:
        if not path.startswith("src/draftwright/") or not path.endswith(".py"):
            continue
        if _matches(path, BROAD_SOURCE_PATTERNS):
            selected.update(CONTRACT_GROUPS)
            continue
        matched = {
            name
            for name, group in CONTRACT_GROUPS.items()
            if _matches(path, group.source_patterns)
        }
        selected.update(matched or CONTRACT_GROUPS)
    return frozenset(selected)


def pr_modules(tests_dir: Path, changed_paths: list[str]) -> list[str]:
    """Return stable module names for the PR core, changed tests, and selected groups."""
    available = {path.name for path in tests_dir.glob("test_*.py")}
    modules = set(UNIT_MODULES) | set(PR_CORE_MODULES) | set(PR_POLICY_MODULES)
    modules.update(
        Path(path).name
        for path in changed_paths
        if path.startswith("tests/test_") and path.endswith(".py")
    )
    for name in selected_groups(changed_paths):
        patterns = CONTRACT_GROUPS[name].test_patterns
        modules.update(module for module in available if _matches(module, patterns))
    missing = modules - available
    if missing:
        raise ValueError(f"tier manifest names missing test modules: {sorted(missing)}")
    return sorted(modules)
