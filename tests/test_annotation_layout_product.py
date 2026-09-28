"""Public, build-scoped selection of the verified annotation layout."""

from inspect import signature
from pathlib import Path
from runpy import run_path
from types import SimpleNamespace

import pytest
from build123d import Box, export_step
from typer.testing import CliRunner

from draftwright import ScaleCompletenessWarning, Sheet, build_drawing, make_drawing
from draftwright.annotation_layout_profile import (
    annotation_layout_policy,
    candidate_profile,
    current_layout_profile,
)
from draftwright.cli import app
from draftwright.layout_selection import (
    pre_render_view_page_overflow,
    select_best_annotation_layout,
)
from draftwright.linting import LintIssue
from draftwright.sheet_emit import generate_sheet_script


def test_candidate_profile_rejects_unknown_names():
    with pytest.raises(ValueError, match="unknown annotation layout profile"):
        candidate_profile("unknown", 1.0)


@pytest.mark.parametrize(
    ("name", "canonical"),
    [
        ("estimated-strips", "estimated-strips"),
        ("baseline", "estimated-strips"),
        ("demand-guided", "demand-guided"),
        ("candidate-preview", "demand-guided"),
        ("compare", "compare"),
        ("best", "compare"),
    ],
)
def test_layout_names_and_compatibility_aliases(name, canonical):
    assert annotation_layout_policy(name) == canonical


@pytest.mark.parametrize("entry", [build_drawing, Sheet, generate_sheet_script])
def test_public_front_doors_reject_unknown_layout_policy(entry, tmp_path):
    with pytest.raises(ValueError, match="annotation_layout must be"):
        entry(tmp_path / "missing.step", annotation_layout="unverified")


def test_best_layout_selects_verified_larger_iso_on_same_sheet_and_scale():
    part = Box(20, 10, 5)
    baseline = build_drawing(part, page="A4", scale=2, annotation_layout="baseline")
    selected = build_drawing(part, page="A4", scale=2, annotation_layout="best")

    decision = selected.annotation_scheme_decision
    assert decision["policy"] == "compare"
    assert decision["pre_render_choice"]["version"] == 5
    assert decision["pre_render_choice"]["page"] == [selected.page_w, selected.page_h]
    assert decision["pre_render_choice"]["scale"] == selected.scale
    assert decision["safety_evidence"]["version"] == 12
    assert decision["safety_evidence"]["admission_ready"] is False
    assert "recognized_occurrences" not in decision["safety_evidence"]["failed_checks"]
    assert decision["selected_trial"] == "iso-growth"
    assert (selected.page_w, selected.page_h, selected.scale) == (
        baseline.page_w,
        baseline.page_h,
        baseline.scale,
    )
    assert selected.scale_decision == baseline.scale_decision
    assert selected.view_bounds("front") == baseline.view_bounds("front")
    assert selected.view_bounds("plan") == baseline.view_bounds("plan")
    assert selected.view_bounds("side") == baseline.view_bounds("side")
    assert decision["trials"][0]["semantic_parity"] is True
    assert decision["trials"][0]["missing_annotations"] == 0
    assert decision["trials"][0]["iso_area_ratio"] >= 1.10


def test_default_selects_candidate_before_render_without_baseline_build(monkeypatch):
    import draftwright.builder as builder

    assembled = []
    original = builder._assemble

    def observe(*args, **kwargs):
        assembled.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(builder, "_assemble", observe)
    drawing = build_drawing(Box(20, 10, 5), page="A4", scale=2)

    decision = drawing.annotation_scheme_decision
    assert len(assembled) == 1
    assert decision["policy"] == "demand-guided"
    assert decision["status"] == "demand_guided"
    assert decision["admission_ready"] is False
    assert decision["safety_evidence"]["version"] == 12
    assert decision["safety_evidence"]["admission_ready"] is False
    assert "recognized_occurrences" not in decision["safety_evidence"]["failed_checks"]
    assert decision["fallback_decision"] == "not_evaluated"
    assert decision["pre_render_choice"]["profile"] == "iso-growth"
    assert decision["carrier_evidence"]["version"] == 3
    assert decision["carrier_evidence"]["coverage_authority"] is False
    assert decision["carrier_evidence"]["demands"]
    assert all(entry["measurements"] for entry in decision["carrier_evidence"]["demands"])
    assert any(
        entry["status"] == "represented" for entry in decision["carrier_evidence"]["demands"]
    )
    assert (drawing.page_w, drawing.page_h, drawing.scale) == (297.0, 210.0, 2.0)


def test_pre_render_view_overflow_measures_only_planned_principal_geometry():
    analysis = SimpleNamespace(
        FV_X=50.0,
        FV_Y=50.0,
        PV_X=50.0,
        PV_Y=108.0,
        SV_X=98.0,
        SV_Y=50.0,
        fv_hw=20.0,
        fv_hh=30.0,
        pv_hh=5.0,
        sv_hw=5.0,
        PAGE_W=100.0,
        PAGE_H=110.0,
        planned_views=("front", "plan"),
    )

    assert pre_render_view_page_overflow(analysis) == {"plan": 3.0}
    analysis.planned_views = ("front", "plan", "side")
    assert pre_render_view_page_overflow(analysis) == {"plan": 3.0, "side": 3.0}


def test_candidate_preview_chooses_one_build_without_gutters_when_views_are_off_page(
    monkeypatch,
):
    import draftwright.builder as builder

    original_choice = builder.choose_pre_render_profile
    original_assemble = builder._assemble
    assembly_profiles = []

    def dense_choice(*args, **kwargs):
        return {**original_choice(*args, **kwargs), "profile": "columns"}

    def observe(*args, **kwargs):
        assembly_profiles.append(current_layout_profile())
        return original_assemble(*args, **kwargs)

    monkeypatch.setattr(builder, "choose_pre_render_profile", dense_choice)
    monkeypatch.setattr(builder, "_assemble", observe)
    drawing = build_drawing(
        Box(190, 5, 279),
        page="A3",
        scale=1,
        scale_policy="permissive",
        annotation_layout="candidate-preview",
    )

    choice = drawing.annotation_scheme_decision["pre_render_choice"]
    # Measured repacking may reassemble this same drawing, but every pass must
    # use the profile selected before rendering; no baseline profile is built.
    assert assembly_profiles
    assert all(profile == candidate_profile("iso-growth", 1) for profile in assembly_profiles)
    assert choice["proposed_profile"] == "columns"
    assert choice["profile"] == "iso-growth"
    assert choice["reason"] == "protected_gutters_worsen_off_page_views"
    assert (
        choice["selected_view_overflow_mm"]["plan"] < choice["proposed_view_overflow_mm"]["plan"]
    )
    assert (
        "view_page_containment"
        in drawing.annotation_scheme_decision["safety_evidence"]["failed_checks"]
    )


def test_conservative_profile_does_not_inherit_rejected_arrangement(monkeypatch):
    import draftwright.builder as builder

    original_analyse = builder._analyse
    original_choice = builder.choose_pre_render_profile
    initial_arrangements = []
    proposed_arrangements = []

    def observe_analyse(*args, **kwargs):
        analysis = original_analyse(*args, **kwargs)
        if kwargs.get("_reuse") is None and not initial_arrangements:
            initial_arrangements.append(analysis.arrangement)
        elif kwargs.get("_arrangements") == ("staggered-side",):
            proposed_arrangements.append(analysis.arrangement)
        return analysis

    def propose_planned(*args, **kwargs):
        return {**original_choice(*args, **kwargs), "profile": "planned"}

    monkeypatch.setattr(builder, "_analyse", observe_analyse)
    monkeypatch.setattr(builder, "choose_pre_render_profile", propose_planned)
    drawing = build_drawing(
        Box(190, 5, 279),
        page="A3",
        scale=1,
        scale_policy="permissive",
        annotation_layout="demand-guided",
    )

    choice = drawing.annotation_scheme_decision["pre_render_choice"]
    assert choice["proposed_profile"] == "planned"
    assert choice["profile"] == "iso-growth"
    assert proposed_arrangements == ["staggered-side"]
    assert drawing.arrangement_decision["chosen"] == initial_arrangements[0]


def test_frame_conservative_profile_preserves_required_location_and_iso():
    fixture = Path(__file__).parent / "fixtures" / "issue_1595_whistle_key_frame.step"
    drawing = build_drawing(
        fixture,
        title="frame-a3-2to1",
        number="frame-a3-2to1",
        pmi="annotate",
        page="A3",
        scale=2,
        scale_policy="permissive",
        _views=("front", "plan", "side"),
        annotation_layout="demand-guided",
    )

    assert drawing.annotation_scheme_decision["pre_render_choice"]["profile"] == "iso-growth"
    assert drawing.arrangement_decision["chosen"] == "columns"
    assert drawing.get_annotation("m_locx1").label == "27.8"
    assert drawing.view_bounds("plan")[3] <= drawing.page_h
    iso_x0, _, iso_x1, _ = drawing.view_bounds("iso")
    assert iso_x1 - iso_x0 > 150  # The regressed staggered-side result was only 114 mm wide.


def test_candidate_preview_without_automatic_annotations_does_not_apply_profile():
    drawing = build_drawing(
        Box(20, 10, 5),
        page="A4",
        scale=2,
        auto_dims=False,
        annotation_layout="candidate-preview",
    )

    decision = drawing.annotation_scheme_decision
    assert decision["status"] == "no_demand_profile"
    assert decision["influenced_layout"] is False
    assert decision["pre_render_choice"]["profile"] is None


def test_candidate_preview_keeps_auto_resolved_constraints_through_repack(monkeypatch):
    import draftwright.builder as builder

    original = builder._repack_to_fixed_point
    original_assemble = builder._assemble
    repack_constraints = []
    assembled = []

    def observe(analysis, *args, **kwargs):
        repack_constraints.append((kwargs["scale"], kwargs["page"]))
        return original(analysis, *args, **kwargs)

    def observe_assemble(*args, **kwargs):
        assembled.append(args[0])
        return original_assemble(*args, **kwargs)

    monkeypatch.setattr(builder, "_repack_to_fixed_point", observe)
    monkeypatch.setattr(builder, "_assemble", observe_assemble)
    drawing = build_drawing(Box(20, 10, 5), annotation_layout="candidate-preview")

    settled = (drawing.scale, (drawing.page_w, drawing.page_h))
    assert len(assembled) == 1
    assert repack_constraints == [settled]
    choice = drawing.annotation_scheme_decision["pre_render_choice"]
    assert (choice["scale"], tuple(choice["page"])) == settled


def test_candidate_preview_records_a_settled_safety_failure():
    def add_conflict(drawing):
        drawing.registry.record_issue(
            LintIssue(severity="warning", code="annotation_overlap", message="test conflict")
        )
        return drawing

    drawing = build_drawing(
        Box(20, 10, 5),
        page="A4",
        scale=2,
        annotation_layout="candidate-preview",
        _post_build=add_conflict,
    )

    evidence = drawing.annotation_scheme_decision["safety_evidence"]
    assert evidence["checks_passed"] is False
    assert "lint_blockers" in evidence["failed_checks"]
    assert drawing.annotation_scheme_decision["fallback_decision"] == "not_evaluated"


def test_candidate_preview_evaluates_safety_on_the_explicit_scale_fallback(monkeypatch):
    import draftwright.builder as builder

    blocker = {
        "severity": "error",
        "code": "forced_required_drop",
        "message": "exercise the scale fallback return",
        "measurements": (),
        "hole_requirements": (),
        "source_ids": (),
    }
    monkeypatch.setattr(
        builder,
        "_scale_blockers",
        lambda drawing, *, physical=True: (blocker,) if drawing.scale == 2 else (),
    )
    with pytest.warns(ScaleCompletenessWarning, match="complete fallback scale"):
        drawing = build_drawing(
            Box(20, 10, 5), page="A4", scale=2, annotation_layout="candidate-preview"
        )

    assert drawing.scale_decision["status"] == "fallback"
    assert drawing.scale < 2
    assert drawing.annotation_scheme_decision["safety_evidence"]["scale"] == drawing.scale


def test_ctc01_candidate_grows_iso_into_clear_space_on_fixed_sheet():
    from draftwright.annotations._common import annotation_ink_obstacles

    source = Path(__file__).parent / "fixtures" / "nist_ctc_01_asme1_ap242.stp"
    drawing = build_drawing(
        source,
        title="CTC-01",
        number="NIST-CTC-01",
        pmi="annotate",
        page="A3",
        scale=0.2,
        scale_policy="permissive",
        _views=("front", "plan", "side"),
        _include_iso=True,
        annotation_layout="best",
    )

    assert drawing.annotation_scheme_decision["selected_trial"] == "planned"
    left, _bottom, right, _top = drawing.view_bounds("iso")
    assert right - left > 120.0  # the fixed 65% preview was only about 108 mm wide
    iso = drawing.view_bounds("iso")
    adjacent_frames = [
        box
        for name, box in annotation_ink_obstacles(drawing, named=True)
        if name.startswith("m_gdt") and iso[0] < box[2] and box[0] < iso[2]
    ]
    assert adjacent_frames
    assert all(iso[1] - box[3] >= 4.5 for box in adjacent_frames)
    y55 = sum(drawing.get_annotation("m_slot0_pos").label_bbox[i] for i in (1, 3)) / 2
    y75 = sum(drawing.get_annotation("m_locx0").label_bbox[i] for i in (1, 3)) / 2
    assert 7.5 <= y75 - y55 <= 8.5  # no unused tier between overlapping left dimensions
    hole = drawing.get_annotation("hc_plan4")
    hole_centre = drawing.at("plan", *hole.source_features[0].frame.origin)
    hole_radius = (hole.tip[0] - hole_centre[0], hole.tip[1] - hole_centre[1])
    hole_shaft = (hole.elbow[0] - hole.tip[0], hole.elbow[1] - hole.tip[1])
    assert abs(hole_radius[0] * hole_shaft[1] - hole_radius[1] * hole_shaft[0]) < 0.1
    chamfer = drawing.get_annotation("m_chamfer_y0")
    chamfer_shaft = (
        chamfer.elbow[0] - chamfer.tip[0],
        chamfer.elbow[1] - chamfer.tip[1],
    )
    assert abs(chamfer_shaft[0] + chamfer_shaft[1]) < 0.1
    fillet = drawing.get_annotation("m_fillet_z0")
    fillet_arc = min(
        (
            edge
            for edge in drawing.views["plan"][0].edges()
            if edge.geom_type.name == "CIRCLE" and abs(edge.radius - 10.0) < 0.1
        ),
        key=lambda edge: abs(
            ((fillet.tip[0] - edge.arc_center.X) ** 2 + (fillet.tip[1] - edge.arc_center.Y) ** 2)
            ** 0.5
            - edge.radius
        ),
    )
    fillet_radius = (
        fillet.tip[0] - fillet_arc.arc_center.X,
        fillet.tip[1] - fillet_arc.arc_center.Y,
    )
    fillet_shaft = (fillet.elbow[0] - fillet.tip[0], fillet.elbow[1] - fillet.tip[1])
    assert abs(fillet_radius[0] * fillet_shaft[1] - fillet_radius[1] * fillet_shaft[0]) < 0.1
    assert not any(issue.code == "leader_crosses_silhouette" for issue in drawing.lint())
    assert not any(issue.code == "view_annotation_overlap" for issue in drawing.lint())


def test_candidate_iso_growth_preserves_issue915_detail_view_gain():
    source = Path(__file__).parent / "fixtures" / "issue_915_case_study_2.step"
    drawing = build_drawing(
        source,
        title="issue915-a2-1to2",
        number="issue915-a2-1to2",
        pmi="annotate",
        page="A2",
        scale=0.5,
        scale_policy="permissive",
        _views=("front", "plan", "side"),
        _include_iso=True,
        annotation_layout="best",
    )

    decision = drawing.annotation_scheme_decision
    assert decision["selected_trial"] == "legacy-depth"
    assert decision["selected_quality_key"][:5] == (0, 0, 0, 0, 0)
    assert "detail_a" in drawing.views
    assert "detail_marker_A" in drawing.annotations()
    assert "detail_caption_A" in drawing.annotations()
    assert len([n for n in drawing.annotations() if n.startswith("dim_detail_a_step")]) == 5
    # The temporary staggered-side seed is 65% of the sheet scale. DETAIL A
    # may cap growth at sheet scale, but must not freeze that undersized seed.
    assert drawing.coords("iso")._scale == pytest.approx(drawing.scale)


def test_best_layout_skips_speculation_without_automatic_annotations():
    selected = build_drawing(Box(20, 10, 5), auto_dims=False, annotation_layout="best")

    assert selected.annotation_scheme_decision["status"] == "retained_baseline"
    assert selected.annotation_scheme_decision["reason"] == "automatic_annotations_disabled"


def test_selector_keeps_baseline_when_a_tidy_candidate_loses_a_dimension(monkeypatch):
    import draftwright.layout_selection as selection

    def record(annotations, quality):
        return {
            "page": [297.0, 210.0],
            "scale": 1.0,
            "views": ["front"],
            "manifest": {
                "annotations": annotations,
                "interior_dimensions": [],
                "coverage": {
                    "requirements": 1,
                    "placed": len(annotations),
                    "missing": 1 - len(annotations),
                    "unsupported": 0,
                    "unverifiable": 0,
                },
                "drops": {},
                "arrangement_quality": {"selection_key": quality},
                "blocker_identities": [],
            },
        }

    required = {"width": {"type": "LinearDimension", "label": "20"}}
    baseline = SimpleNamespace(
        scale=1.0,
        annotation_scheme_decision={},
        record=record(required, (1, 0, 1, 0, 0, -1.0, 62370.0)),
    )
    candidate = SimpleNamespace(
        scale=1.0,
        annotation_scheme_decision={},
        record=record({}, (0, 0, 0, 0, 0, -1.0, 62370.0)),
    )
    monkeypatch.setattr(selection, "drawing_record", lambda drawing: drawing.record)

    selected = select_best_annotation_layout(baseline, lambda _profile: candidate)

    assert selected is baseline
    assert selected.annotation_scheme_decision["status"] == "retained_baseline"
    assert [trial["verdict"] for trial in selected.annotation_scheme_decision["trials"]] == [
        "ineligible",
        "ineligible",
    ]


def test_speculative_build_failure_keeps_the_finished_baseline(monkeypatch):
    import draftwright.layout_selection as selection

    baseline = SimpleNamespace(
        scale=1.0,
        annotation_scheme_decision={},
        record={
            "manifest": {
                "arrangement_quality": {"selection_key": (0, 0, 0, 0, 0, -1.0, 62370.0)},
                "interior_dimensions": [],
            }
        },
    )
    monkeypatch.setattr(selection, "drawing_record", lambda drawing: drawing.record)

    def fail(_profile):
        raise ValueError("candidate cannot fit")

    selected = select_best_annotation_layout(baseline, fail)

    assert selected is baseline
    assert selected.annotation_scheme_decision["status"] == "retained_baseline"
    assert selected.annotation_scheme_decision["trials"] == [
        {"name": "iso-growth", "verdict": "build_failed", "error": "candidate cannot fit"}
    ]


@pytest.mark.parametrize(
    ("hard_defects", "expected_trial", "expected_arrangement"),
    [(1, "columns", "columns"), (0, "planned", "staggered-side")],
)
def test_trial_order_prioritizes_hard_defects_or_required_content(
    monkeypatch, hard_defects, expected_trial, expected_arrangement
):
    import draftwright.layout_selection as selection

    annotation = {"width": {"type": "LinearDimension", "label": "20"}}

    def record(quality):
        return {
            "page": [297.0, 210.0],
            "scale": 1.0,
            "views": ["front"],
            "manifest": {
                "annotations": annotation,
                "interior_dimensions": ["width"],
                "coverage": {
                    "requirements": 1,
                    "placed": 1,
                    "missing": 0,
                    "unsupported": 0,
                    "unverifiable": 0,
                },
                "drops": {},
                "arrangement_quality": {"selection_key": quality},
                "blocker_identities": [],
            },
        }

    baseline = SimpleNamespace(
        scale=1.0,
        annotation_scheme_decision={},
        record=record((hard_defects, 1, hard_defects, 1, 0, -1.0, 62370.0)),
    )
    candidate = SimpleNamespace(
        scale=1.0,
        annotation_scheme_decision={},
        record=record((0, 0, 0, 1, 0, -1.0, 62370.0)),
    )
    monkeypatch.setattr(selection, "drawing_record", lambda drawing: drawing.record)
    profiles = []

    def build(profile):
        profiles.append(profile)
        return candidate

    selected = select_best_annotation_layout(baseline, build)

    assert selected is candidate
    assert selected.annotation_scheme_decision["selected_trial"] == expected_trial
    assert [profile.arrangement for profile in profiles] == [expected_arrangement]


def test_sheet_and_generated_script_forward_layout_policy(tmp_path):
    sheet = Sheet(Box(20, 10, 5), annotation_layout="compare")
    assert sheet._opts["annotation_layout"] == "compare"

    script = Path(
        generate_sheet_script(
            Box(20, 10, 5),
            out=str(tmp_path / "box"),
            page="A4",
            scale=2,
            annotation_layout="compare",
            formats=(),
            inspect=False,
        )
    ).read_text(encoding="utf-8")
    assert "annotation_layout='compare'" in script


def test_default_front_doors_and_generated_script_choose_demand_guided(tmp_path):
    assert signature(make_drawing).parameters["annotation_layout"].default == "demand-guided"
    assert Sheet(Box(20, 10, 5))._opts["annotation_layout"] == "demand-guided"

    source = tmp_path / "default.step"
    export_step(Box(20, 10, 5), source)
    script_path = generate_sheet_script(
        str(source),
        out=str(tmp_path / "default"),
        formats=(),
        inspect=False,
    )
    script = Path(script_path).read_text(encoding="utf-8")
    assert "annotation_layout='demand-guided'" in script
    drawing = run_path(script_path)["drawing"]
    assert drawing.annotation_scheme_decision["policy"] == "demand-guided"


@pytest.mark.parametrize(
    ("name", "canonical"),
    [
        ("estimated-strips", "estimated-strips"),
        ("baseline", "estimated-strips"),
        ("demand-guided", "demand-guided"),
        ("candidate-preview", "demand-guided"),
        ("compare", "compare"),
        ("best", "compare"),
    ],
)
def test_sheet_normalizes_layout_names(name, canonical):
    sheet = Sheet(Box(20, 10, 5), annotation_layout=name)
    assert sheet._opts["annotation_layout"] == canonical


def test_generated_step_script_replays_the_layout_selection(tmp_path):
    source = tmp_path / "box.step"
    export_step(Box(20, 10, 5), source)
    direct = build_drawing(str(source), annotation_layout="compare")
    script = generate_sheet_script(
        str(source),
        out=str(tmp_path / "box"),
        annotation_layout="compare",
        formats=(),
        inspect=False,
    )

    drawing = run_path(script)["drawing"]
    assert drawing.annotation_scheme_decision["policy"] == "compare"
    assert drawing.annotation_scheme_decision["status"] == "candidate"
    assert (drawing.page_w, drawing.page_h, drawing.scale) == (
        direct.page_w,
        direct.page_h,
        direct.scale,
    )


@pytest.mark.parametrize(
    "name",
    ["estimated-strips", "demand-guided", "compare", "baseline", "candidate-preview", "best"],
)
def test_cli_forwards_layout_policy_to_a_rendered_build(monkeypatch, name):
    import draftwright.builder as builder

    forwarded = []

    class Drawing:
        out = "out"
        annotation_scheme_decision = {"selected_trial": "columns"}

        def export(self, *, formats):
            return {name: f"out.{name}" for name in formats}

    def capture(*_args, **kwargs):
        forwarded.append(kwargs)
        return Drawing()

    monkeypatch.setattr(builder, "build_drawing", capture)
    result = CliRunner().invoke(
        app,
        ["part.step", "--annotation-layout", name, "--format", "svg", "--no-report"],
    )

    assert result.exit_code == 0, result.output
    assert forwarded[0]["annotation_layout"] == name
    if name in {"compare", "best"}:
        assert "Selected annotation layout: columns" in result.output


def test_cli_defaults_to_demand_guided_planning(monkeypatch):
    import draftwright.builder as builder

    forwarded = []

    class Drawing:
        out = "out"
        annotation_scheme_decision = {}

        def export(self, *, formats):
            return {name: f"out.{name}" for name in formats}

    def capture(*_args, **kwargs):
        forwarded.append(kwargs)
        return Drawing()

    monkeypatch.setattr(builder, "build_drawing", capture)
    result = CliRunner().invoke(app, ["part.step", "--format", "svg", "--no-report"])

    assert result.exit_code == 0, result.output
    assert forwarded[0]["annotation_layout"] == "demand-guided"
