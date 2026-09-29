"""Cost-aware tier manifests remain complete and conservative."""

import os
import runpy
from pathlib import Path
from types import SimpleNamespace

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised by the Python 3.10 CI legs
    import tomli as tomllib

from _tier_manifest import (
    BROAD_SOURCE_PATTERNS,
    CONTRACT_GROUPS,
    CRITICAL_CONTRACT_MODULES,
    FULL_EXPRESSION,
    PR_CORE_MODULES,
    PR_POLICY_MODULES,
    pr_modules,
    selected_groups,
)
from _unit_manifest import UNIT_MODULES

_TESTS = Path(__file__).resolve().parent


def test_each_named_contract_group_resolves_to_existing_modules():
    all_modules = {path.name for path in _TESTS.glob("test_*.py")}
    probes = {
        "scale_policy": "src/draftwright/build_policy.py",
        "pmi_support": "src/draftwright/_pmi_support_blockers.py",
        "double_d_evidence": "src/draftwright/evaluation/_double_d_evidence.py",
        "turned_step_evidence": "src/draftwright/evaluation/_turned_step_evidence.py",
        "pocket_evidence": "src/draftwright/evaluation/_pocket_evidence.py",
        "prismatic_evidence": "src/draftwright/evaluation/_prismatic_evidence.py",
        "groove_evidence": "src/draftwright/evaluation/_groove_evidence.py",
        "edge_profile_evidence": "src/draftwright/evaluation/_edge_profile_evidence.py",
        "step_observers": "src/draftwright/evaluation/step_analysis.py",
        "through_step_placement": "src/draftwright/annotations/from_model.py",
        "solve_trace": "src/draftwright/annotations/solve_trace.py",
        "dimension_ink": "src/draftwright/annotations/_dimension_ink.py",
        "dimension_ink_repair": "src/draftwright/annotations/_dimension_ink_repair.py",
        "placement_geometry": "src/draftwright/annotations/_placement_geometry.py",
        "hole_locations": "src/draftwright/annotations/hole_locations.py",
        "oriented_slot_geometry": "src/draftwright/model/oriented_slot_geometry.py",
        "ir_foundation": "src/draftwright/model/ir_foundation.py",
        "slot_rendering": "src/draftwright/annotations/_slots.py",
        "drawing_edits": "src/draftwright/drawing_edits.py",
        "drawing_diagnostics": "src/draftwright/drawing_diagnostics.py",
        "sheet_layout_controls": "src/draftwright/sheet_layout_controls.py",
        "recognition": "src/draftwright/recognition_frame.py",
        "compilation": "src/draftwright/intents.py",
        "placement": "src/draftwright/layout.py",
        "intent_drain": "src/draftwright/intent_drain.py",
        "reporting": "src/draftwright/reporting.py",
        "drawing_tables": "src/draftwright/drawing_tables.py",
        "export": "src/draftwright/export.py",
    }
    assert set(probes) == set(CONTRACT_GROUPS)
    for name, path in probes.items():
        assert name in selected_groups([path])
        selected = set(pr_modules(_TESTS, [path]))
        assert selected <= all_modules
        assert selected - PR_CORE_MODULES - PR_POLICY_MODULES - UNIT_MODULES, name


def test_double_d_evidence_change_runs_its_physical_correspondence_contract():
    selected = pr_modules(_TESTS, ["src/draftwright/evaluation/_double_d_evidence.py"])
    assert "test_issue_1370_double_d_completeness_evidence.py" in selected


def test_edge_profile_evidence_change_runs_both_physical_correspondence_contracts():
    selected = set(pr_modules(_TESTS, ["src/draftwright/evaluation/_edge_profile_evidence.py"]))
    assert {
        "test_issue_1374_chamfer_completeness_evidence.py",
        "test_issue_1374_fillet_completeness_evidence.py",
    } <= selected


def test_drawing_table_and_export_owners_select_their_behavior_contracts():
    tables = set(pr_modules(_TESTS, ["src/draftwright/drawing_tables.py"]))
    assert {
        "test_hole_table.py",
        "test_sheet_tables.py",
        "test_issue_1144_transactional_hole_table.py",
    } <= tables
    export = set(pr_modules(_TESTS, ["src/draftwright/drawing_export.py"]))
    assert {"test_export_reproducible.py", "test_issue_1533_review_diagnostics.py"} <= export


def test_scale_policy_change_runs_scale_and_arrangement_contracts():
    selected = set(pr_modules(_TESTS, ["src/draftwright/build_policy.py"]))
    assert {
        "test_issue_1146_scale_completeness.py",
        "test_issue_1299_page_escalation.py",
        "test_arrangement_gate.py",
    } <= selected


def test_turned_step_evidence_change_runs_its_physical_correspondence_contract():
    selected = pr_modules(_TESTS, ["src/draftwright/evaluation/_turned_step_evidence.py"])
    assert "test_issue_1374_turned_step_completeness_evidence.py" in selected


def test_pmi_support_change_runs_source_and_rendering_contracts():
    source = "src/draftwright/_pmi_support_blockers.py"
    assert selected_groups([source]) == {"pmi_support"}
    assert selected_groups(["src/draftwright/pmi.py"]) == {"pmi_support"}
    selected = set(pr_modules(_TESTS, [source]))
    assert {
        "test_issue_1209_linear_pmi_witnesses.py",
        "test_pmi.py",
        "test_pmi_gtol_lowering.py",
        "test_pmi_records.py",
    } <= selected


def test_pocket_evidence_change_runs_both_occurrence_contracts():
    selected = set(pr_modules(_TESTS, ["src/draftwright/evaluation/_pocket_evidence.py"]))
    assert {
        "test_pocket_completeness_evidence.py",
        "test_pocket_pattern_completeness_evidence.py",
    } <= selected


def test_prismatic_evidence_change_runs_both_occurrence_contracts():
    selected = set(pr_modules(_TESTS, ["src/draftwright/evaluation/_prismatic_evidence.py"]))
    assert {
        "test_pad_completeness_evidence.py",
        "test_issue_1373_plate_completeness_evidence.py",
    } <= selected


def test_step_observer_change_runs_every_family_contract():
    selected = set(pr_modules(_TESTS, ["src/draftwright/evaluation/step_analysis.py"]))
    assert set(CONTRACT_GROUPS["step_observers"].test_patterns) <= selected


def test_from_model_change_runs_through_step_placement_contract():
    selected = pr_modules(_TESTS, ["src/draftwright/annotations/from_model.py"])
    assert "test_through_step_semantics.py" in selected


def test_drawing_edit_owner_runs_live_and_deferred_contracts():
    source = "src/draftwright/drawing_edits.py"
    assert selected_groups([source]) == {"drawing_edits"}
    selected = set(pr_modules(_TESTS, [source]))
    assert {
        "test_add_dimension.py",
        "test_canonical_angles.py",
        "test_deferred_edits.py",
        "test_feature_edit_replay.py",
        "test_issue_1613_circular_channel.py",
        "test_refactor_golden.py",
    } <= selected


def test_solve_trace_owner_runs_recorder_and_boundary_contracts():
    selected = set(pr_modules(_TESTS, ["src/draftwright/annotations/solve_trace.py"]))
    assert {"test_solve_trace.py", "test_compiled_plan_boundary.py"} <= selected


def test_step_length_owner_selects_its_placement_contract():
    source = "src/draftwright/annotations/_step_lengths.py"
    assert selected_groups([source]) == frozenset({"through_step_placement", "placement"})
    selected = set(pr_modules(_TESTS, [source]))
    assert {
        "test_through_step_semantics.py",
        "test_turned_lengths.py",
        "test_issue_1505_short_axial_chains.py",
        "test_issue_1357_plural_turned_profiles.py",
    } <= selected


def test_hole_location_source_selects_its_behavior_and_evidence_contracts():
    source = "src/draftwright/annotations/hole_locations.py"
    assert selected_groups([source]) == frozenset({"hole_locations", "placement"})
    selected = set(pr_modules(_TESTS, [source]))
    assert {
        "test_location_dimensions.py",
        "test_location_vocabulary.py",
        "test_lint_summary.py",
    } <= selected


def test_oriented_slot_geometry_change_runs_its_semantics_contract():
    selected = pr_modules(_TESTS, ["src/draftwright/model/oriented_slot_geometry.py"])
    assert "test_issue_1432_oriented_slot_semantics.py" in selected


def test_ir_foundation_change_runs_manufacturing_requirement_contract():
    selected = pr_modules(_TESTS, ["src/draftwright/model/ir_foundation.py"])
    assert "test_issue_1298_manufacturing_requirements.py" in selected


def test_slot_renderer_change_runs_its_slot_and_pocket_behavior_contracts():
    source = "src/draftwright/annotations/_slots.py"
    assert selected_groups([source]) == {"slot_rendering", "placement"}
    selected = set(pr_modules(_TESTS, [source]))
    assert {
        "test_feature_edit_corridors.py",
        "test_issue_885_prismatic_coverage.py",
        "test_issue_1599_duplicate_slot_widths.py",
        "test_layout_override_lane_issue_1757.py",
        "test_slot_completeness.py",
        "test_slot_pattern.py",
        "test_slot_recognition.py",
        "test_tolerances.py",
    } <= selected
    assert "test_pad_rendering.py" in selected


def test_unknown_production_module_selects_every_contract_group():
    assert selected_groups(["src/draftwright/a_new_area.py"]) == frozenset(CONTRACT_GROUPS)
    for pattern in BROAD_SOURCE_PATTERNS:
        assert selected_groups([pattern]) == frozenset(CONTRACT_GROUPS)


def test_sheet_feature_view_changes_run_the_sheet_identity_contract():
    changed = ["src/draftwright/sheet_features.py"]
    assert selected_groups(changed) == {"compilation"}
    assert "test_sheet_identity_invariant.py" in pr_modules(_TESTS, changed)


def test_sheet_layout_control_changes_run_the_declaration_contract():
    changed = ["src/draftwright/sheet_layout_controls.py"]
    assert selected_groups(changed) == {"sheet_layout_controls"}
    modules = pr_modules(_TESTS, changed)
    assert "test_layout_override_lane_issue_1757.py" in modules
    assert "test_layout_override_side_issue_1757.py" in modules
    assert "test_sheet_identity_invariant.py" in modules
    assert "test_dimension_lane_canary_issue_1757.py" not in modules


def test_pmi_changes_run_structured_reader_failure_contract():
    selected = pr_modules(_TESTS, ["src/draftwright/pmi.py"])
    assert "test_pmi_records.py" in selected


def test_analytical_dimension_ink_runs_its_geometry_contract():
    selected = pr_modules(_TESTS, ["src/draftwright/annotations/_dimension_ink.py"])
    assert "test_pitch_dim_footprint.py" in selected
    assert "test_issue_1334_prevent_ink.py" in selected


def test_placement_geometry_runs_footprint_and_occupancy_contracts():
    selected = set(pr_modules(_TESTS, ["src/draftwright/annotations/_placement_geometry.py"]))
    assert {
        "test_leader_footprint.py",
        "test_occupancy_boxes.py",
        "test_strip_layout.py",
    } <= selected


def test_nonproduction_changes_do_not_expand_the_core():
    assert set(pr_modules(_TESTS, ["docs/guide.md"])) == (
        set(PR_CORE_MODULES) | set(PR_POLICY_MODULES) | set(UNIT_MODULES)
    )


def test_repository_policy_guards_are_pr_only():
    assert PR_POLICY_MODULES.isdisjoint(UNIT_MODULES)


def test_changed_test_module_is_selected_directly():
    assert "test_tier_manifest.py" in pr_modules(_TESTS, ["tests/test_tier_manifest.py"])


def test_deleted_test_module_is_not_reintroduced_as_a_missing_path():
    selected = pr_modules(_TESTS, ["tests/test_deleted_contract.py"])

    assert "test_deleted_contract.py" not in selected


def test_tier_runner_includes_deletions_in_each_git_diff():
    runner = _TESTS.parent / "scripts" / "test-tier"
    namespace = runpy.run_path(str(runner))
    calls = []

    def record_git_paths(*arguments):
        calls.append(arguments)
        return set()

    namespace["changed_paths"].__globals__["_git_paths"] = record_git_paths
    namespace["changed_paths"]("base")

    diff_calls = [arguments for arguments in calls if arguments[0] == "diff"]
    assert len(diff_calls) == 3
    assert all("--diff-filter=ACMRD" in arguments for arguments in diff_calls)


def test_critical_contracts_are_fast_and_exist():
    available = {path.name for path in _TESTS.glob("test_*.py")}
    assert CRITICAL_CONTRACT_MODULES <= available
    for module in CRITICAL_CONTRACT_MODULES:
        source = (_TESTS / module).read_text()
        assert "pytest.mark.slow" not in source
        assert "pytest.mark.scheduled" not in source


def test_tier_runner_is_executable():
    runner = _TESTS.parent / "scripts" / "test-tier"
    assert runner.is_file()
    assert os.access(runner, os.X_OK)


def test_scheduled_selection_is_intersected_with_the_tier(monkeypatch):
    runner = _TESTS.parent / "scripts" / "test-tier"
    namespace = runpy.run_path(str(runner))
    captured = []

    monkeypatch.setattr(namespace["pytest"], "main", lambda args: captured.extend(args) or 0)

    assert namespace["main"](["scheduled", "--selection", "ctc04", "--workers", "1"]) == 0
    marker = captured[captured.index("-m") + 1]
    assert marker == "(slow or scheduled) and (ctc04)"


def test_isolated_scheduled_runner_restarts_pytest_per_test(monkeypatch):
    runner = _TESTS.parent / "scripts" / "test-tier"
    namespace = runpy.run_path(str(runner))
    calls = []

    def completed(command, **kwargs):
        calls.append(command)
        if "--collect-only" in command:
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "tests/test_one.py::test_a\n"
                    "tests/test_one.py::test_b\n"
                    "tests/test_two.py::test_c\n"
                ),
            )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(namespace["subprocess"], "run", completed)

    assert namespace["main"](["scheduled", "--workers", "0", "--isolate-tests"]) == 0
    assert "--collect-only" in calls[0]
    assert calls[1][3:4] == ["tests/test_one.py::test_a"]
    assert calls[2][3:4] == ["tests/test_one.py::test_b"]
    assert calls[3][3:4] == ["tests/test_two.py::test_c"]
    assert (
        calls[1][-5:]
        == calls[2][-5:]
        == calls[3][-5:]
        == [
            "-m",
            "slow or scheduled",
            "-n",
            "0",
            "--timeout=600",
        ]
    )

    calls.clear()
    assert (
        namespace["main"](
            ["scheduled", "--workers", "0", "--isolate-tests", "--scheduled-timeout", "900"]
        )
        == 0
    )
    assert all(command[-1] == "--timeout=900" for command in calls[1:])


def test_default_pytest_selection_matches_the_full_tier():
    config = tomllib.loads((_TESTS.parent / "pyproject.toml").read_text(encoding="utf-8"))

    assert f"-m '{FULL_EXPRESSION}'" in config["tool"]["pytest"]["ini_options"]["addopts"]
