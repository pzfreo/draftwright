"""Output contract for analytical machined-feature leaders (#1308)."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from build123d import Box, Cylinder, Pos
from build123d_drafting.helpers import Draft

from draftwright import build_drawing
from draftwright.annotations import from_model, leaders
from draftwright.annotations._common import analytical_leader_lands_clear

FIXTURES = Path(__file__).parent / "fixtures"

# Expected annotation inventory and lint inventory for the three measured STEP parts.
# Page-space boxes are deliberately omitted: view planning may move otherwise
# identical feature ink. The test below validates the actual analytical geometry
# against the rendered OCC leader for every placed analytical survivor.
EXPECTED = {
    "grm03_thumbwheel_drive_screw.step": (
        {
            "centerline_front": "Centerline",
            "centerline_plan": "Centerline",
            "m_cm0": "CenterMark",
            "m_dia_x1": "Leader",
            "m_dia_x2": "Leader",
            "m_dia_x3": "Leader",
            "m_dia_x0": "Leader",
            "m_steplen0": "Dimension",
            "m_steplen1": "Dimension",
            "m_steplen2": "Dimension",
            "dim_height": "Dimension",
            "hc_side0": "Leader",
            "m_chamfer_x0": "Leader",
            "m_chamfer_x1": "Leader",
            "title_block": "TitleBlock",
            "scale_note": "Note",
        },
        {},
    ),
    "issue_1058_wheel_rh.step": (
        {
            "m_cm0": "CenterMark",
            "hc_plan0": "Leader",
            "m_locx0": "Dimension",
            "m_locy0": "Dimension",
            "dim_height": "Dimension",
            "m_env_width": "Dimension",
            "m_env_depth": "Dimension",
            "title_block": "TitleBlock",
            "scale_note": "Note",
            "note_iso_nts": "Note",
        },
        {"gear_semantics_missing": 1, "nominal_rounded": 1, "unrecognised_defining_geometry": 1},
    ),
    "nist_ctc_01_asme1_ap242.stp": (
        {
            "m_cm0": "CenterMark",
            "m_cm1": "CenterMark",
            "m_cm2": "CenterMark",
            "m_cm3": "CenterMark",
            "m_cm4": "CenterMark",
            "m_cm5": "CenterMark",
            "m_cm6": "CenterMark",
            "m_cm7": "CenterMark",
            "m_cm8": "CenterMark",
            "m_cm9": "CenterMark",
            "hc_front0": "Leader",
            "m_polygonal_boss_z0": "Leader",
            "m_slot0_width": "Dimension",
            "m_slot1_width": "Dimension",
            "m_slot0_length": "Dimension",
            "m_slot1_length": "Dimension",
            "m_slot0_pos": "Dimension",
            "m_locx0": "Dimension",
            "dim_pitch_plan0_0": "Dimension",
            "dim_pitch_plan0_1": "Dimension",
            "m_slot1_pos": "Dimension",
            "m_locy0": "Dimension",
            "dim_pitch_plan1_0": "Dimension",
            "dim_pitch_plan1_1": "Dimension",
            "dim_step_0": "Dimension",
            "m_bossheight_z0": "Dimension",
            "dim_loc_front_z7500": "Dimension",
            "dim_height": "Dimension",
            "m_env_depth": "Dimension",
            "dim_loc_front_x37000": "Dimension",
            "dim_loc_front_x43000": "Dimension",
            "m_env_width": "Dimension",
            "hc_plan0": "Leader",
            "hc_plan1": "Leader",
            "m_slot0_radius": "Leader",
            "m_chamfer_y0": "Leader",
            "m_chamfer_z1": "Leader",
            "m_fillet_z0": "Leader",
            "m_blend_x0": "Leader",
            "m_blend_z1": "Leader",
            "m_blend_z2": "Leader",
            "title_block": "TitleBlock",
            "scale_note": "Note",
            "note_iso_nts": "Note",
        },
        {
            "plate_requirement_unverifiable": 1,
            "pmi_present_but_ignored": 1,
            "step_dim_withheld": 1,
        },
    ),
}

# GRM03 can use its original ISO plan or a detail view for the final step lengths.
# Both choices retain the common machined feature inventory above.
GRM03_VIEW_ALTERNATIVES = (
    {"m_steplen3": "Dimension", "note_iso_nts": "Note"},
    {
        "detail_caption_A": "Note",
        "detail_marker_A": "Compound",
        "detail_marker_label_A": "Note",
        "dim_detail_a_steplen0": "Dimension",
        "dim_detail_a_steplen1": "Dimension",
    },
)


def test_analytical_producer_floor_matches_label_and_full_geometry_clearance():
    candidate = SimpleNamespace(
        label_box=(10.0, 10.0, 12.0, 12.0),
        ink_polygons=(((5.0, 5.0), (7.0, 5.0), (7.0, 7.0), (5.0, 7.0)),),
    )
    page = (0.0, 0.0, 100.0, 100.0)
    silhouette = (20.0, 20.0, 30.0, 30.0)
    shaft_obstacle = ((5.5, 5.5, 6.5, 6.5),)

    assert analytical_leader_lands_clear(candidate, shaft_obstacle, silhouette, page, label="R1")
    assert not analytical_leader_lands_clear(
        candidate,
        shaft_obstacle,
        silhouette,
        page,
        label="R1",
        geom_clear=True,
    )
    assert not analytical_leader_lands_clear(candidate, (), silhouette, page, label="")
    assert not analytical_leader_lands_clear(
        SimpleNamespace(label_box=None, ink_polygons=()),
        (),
        silhouette,
        page,
        label="R1",
    )


def test_shared_machined_adapter_fails_closed_with_provenance(monkeypatch):
    issues = []
    ctx = SimpleNamespace(
        feature_leaders=[],
        record_issue=lambda *args, **kwargs: issues.append((args, kwargs)),
    )
    drawing = SimpleNamespace(draft=Draft())
    feature = object()
    monkeypatch.setattr(
        "draftwright.annotations.from_model._text_size",
        lambda *_args, **_kwargs: (0.0, 0.0),
    )

    placed = from_model.place_machined_leader_jobs(
        drawing,
        None,
        [("probe", "plan", (0.0, 0.0, 1.0, 1.0), "R1", [((2, 2), (2, 2), feature)], ())],
        noun="probe",
        drop_code="probe_dropped",
        ctx=ctx,
        joint=True,
        source_ids_by_name={"probe": ("feature:1",)},
    )

    assert placed == 0
    (job,) = ctx.feature_leaders
    assert list(job.candidates) == [((2, 2), (2, 2), feature)]
    assert job.analytical_geometry((2, 2), (2, 2), feature) is None
    assert job.on_drop is not None
    job.on_drop("geometry_validation")
    job.on_drop("no_clear_room")
    assert [args[:3] for args, _kwargs in issues] == [
        (
            "warning",
            "probe_dropped",
            "probe callout R1 not placed (rendered geometry validation failed)",
        ),
        ("warning", "probe_dropped", "probe callout R1 not placed (no clear room)"),
    ]
    assert [kwargs["source"] for _args, kwargs in issues] == [
        ("feature:1",),
        ("feature:1",),
    ]


def test_source_aware_drop_severity_preserves_unsourced_warning(monkeypatch):
    issues = []
    ctx = SimpleNamespace(
        feature_leaders=[],
        record_issue=lambda *args, **kwargs: issues.append((args, kwargs)),
    )
    monkeypatch.setattr(from_model, "_text_size", lambda *_args, **_kwargs: (0.0, 0.0))
    jobs = [
        ("plain", "plan", (0.0, 0.0, 1.0, 1.0), "C1", (), ()),
        ("imported", "plan", (0.0, 0.0, 1.0, 1.0), "C2", (), ()),
    ]

    from_model.place_machined_leader_jobs(
        SimpleNamespace(draft=Draft()),
        None,
        jobs,
        noun="chamfer",
        drop_code="chamfer_dropped",
        ctx=ctx,
        joint=True,
        source_ids_by_name={"imported": ("manufacturing_requirement:#1",)},
        source_drop_severity="source",
    )
    for job in ctx.feature_leaders:
        assert job.on_drop is not None
        job.on_drop("no_clear_room")

    assert [args[0] for args, _kwargs in issues] == ["warning", "error"]
    assert [kwargs["source"] for _args, kwargs in issues] == [
        (),
        ("manufacturing_requirement:#1",),
    ]


def test_requested_leader_lane_refusal_names_exact_rank(monkeypatch):
    issues = []
    ctx = SimpleNamespace(
        feature_leaders=[],
        record_issue=lambda *args, **kwargs: issues.append((args, kwargs)),
    )
    monkeypatch.setattr(from_model, "_text_size", lambda *_args, **_kwargs: (0.0, 0.0))
    from_model.place_machined_leader_jobs(
        SimpleNamespace(draft=Draft()),
        None,
        [("step", "front", (0.0, 0.0, 1.0, 1.0), "ø10", (), ())],
        noun="diameter",
        drop_code="diameter_dropped",
        ctx=ctx,
        joint=True,
        requested_lanes={"step": 4},
    )

    (job,) = ctx.feature_leaders
    assert job.on_drop is not None
    job.on_drop("no_clear_room")
    (args, kwargs), = issues
    assert args[0:2] == ("error", "placement_unsatisfiable")
    assert "requested lane 4 unavailable" in args[2]
    assert kwargs["evidence_reason"] == "requested_lane_unavailable:4:no_clear_room"


@pytest.mark.parametrize("fixture", tuple(EXPECTED))
def test_analytical_machined_leaders_preserve_the_occ_measured_drawing(fixture, monkeypatch):
    from draftwright import builder as builder_module

    # Inter-view clearance has dedicated compose/repack coverage; this test isolates
    # whether each selected analytical leader matches its actual OCC ink.
    monkeypatch.setattr(builder_module, "_annotation_clearance", lambda _drawing: 0.0)
    captured_jobs = []
    constructed_polygonal = []
    analytical_matches = {}
    rendered_ink_matches = {}
    materialized_jobs = {}
    place_feature_leader_jobs = from_model.place_feature_leader_jobs
    collect_feature_leader = from_model.collect_feature_leader
    real_leader = from_model.Leader
    geometry_matches = leaders._geometry_matches
    ink_matches = leaders._rendered_ink_matches
    materialize = leaders._materialize

    def capture_geometry_match(candidate, annotation, **kwargs):
        matches = geometry_matches(candidate, annotation, **kwargs)
        analytical_matches[id(annotation)] = matches
        return matches

    monkeypatch.setattr(leaders, "_geometry_matches", capture_geometry_match)

    def capture_rendered_ink_match(candidate, annotation, **kwargs):
        matches = ink_matches(candidate, annotation, **kwargs)
        rendered_ink_matches[id(annotation)] = matches
        return matches

    monkeypatch.setattr(leaders, "_rendered_ink_matches", capture_rendered_ink_match)

    def capture_materialized_job(dwg, job, candidate):
        annotation = materialize(dwg, job, candidate)
        if annotation is not None:
            materialized_jobs[id(annotation)] = (job.name, job.analytical_geometry is not None)
        return annotation

    monkeypatch.setattr(leaders, "_materialize", capture_materialized_job)

    def capture_immediate(drawing, analysis, ctx, jobs, *, producer_floor=False):
        jobs = list(jobs)
        captured_jobs.extend(jobs)
        return place_feature_leader_jobs(
            drawing,
            analysis,
            ctx,
            jobs,
            producer_floor=producer_floor,
        )

    monkeypatch.setattr(from_model, "place_feature_leader_jobs", capture_immediate)

    def capture_late(context, job):
        captured_jobs.append(job)
        return collect_feature_leader(context, job)

    monkeypatch.setattr(from_model, "collect_feature_leader", capture_late)

    def counted_leader(*args, **kwargs):
        leader = real_leader(*args, **kwargs)
        if str(leader.label).startswith("HEX "):
            constructed_polygonal.append(leader)
        return leader

    monkeypatch.setattr(from_model, "Leader", counted_leader)
    # Keep the established principal topology and furniture policy explicit.
    drawing = build_drawing(
        FIXTURES / fixture,
        page="A2" if fixture == "nist_ctc_01_asme1_ap242.stp" else None,
        _views=("front", "plan", "side"),
        projection_symbol=False,
    )
    actual = {name: type(annotation).__name__ for name, annotation in drawing.iter_annotations()}

    expected_annotations, expected_lint = EXPECTED[fixture]
    if fixture == "grm03_thumbwheel_drive_screw.step":
        assert actual in [expected_annotations | extra for extra in GRM03_VIEW_ALTERNATIVES]
    else:
        assert actual == expected_annotations
    assert drawing.lint_summary()["by_code"] == expected_lint
    analytical_names = {
        name
        for name, annotation in drawing.iter_annotations()
        if materialized_jobs.get(id(annotation)) == (name, True)
    }
    required_analytical = {
        "grm03_thumbwheel_drive_screw.step": {
            "m_chamfer_x0",
            "m_chamfer_x1",
        },
        "issue_1058_wheel_rh.step": set(),
        "nist_ctc_01_asme1_ap242.stp": {
            "m_polygonal_boss_z0",
            "m_chamfer_y0",
            "m_chamfer_z1",
            "m_fillet_z0",
            "m_blend_x0",
            "m_blend_z1",
            "m_blend_z2",
        },
    }[fixture]
    assert required_analytical <= analytical_names
    placed_analytical = analytical_names & actual.keys()
    for name in placed_analytical:
        annotation = drawing.get_annotation(name)
        assert analytical_matches.get(id(annotation)) is True, name
        assert rendered_ink_matches.get(id(annotation)) is True, name
    if fixture == "nist_ctc_01_asme1_ap242.stp":
        from build123d import Edge, GeomType

        # Both symmetric grids share one physical X and Y centre mark; each mark
        # retains the two approved pattern-location identities.
        for axis in "xy":
            keys = drawing.measurement_keys(f"m_loc{axis}0")
            assert len(keys) == 2
            assert {key["parameter_id"] for key in keys} == {
                f"location_pattern.location.centre.{axis}"
            }

        # #1479 moved all three Blend tips from analytic axes to their own
        # physical boundaries. Check the named occurrence independently of
        # the renderer's candidate selection and page layout.
        evidence = drawing.recognition_evidence()
        ownership = drawing.recognition_ownership()
        bounds = drawing.working_part.bounding_box()
        for name, view, axis, radius in (
            ("m_blend_x0", "side", "x", 5),
            ("m_blend_z1", "plan", "z", 10),
            ("m_blend_z2", "plan", "z", 25),
        ):
            feature = drawing.registry.feature_of(name)
            assert feature.radius == radius
            assert drawing.view_of(name) == view
            (binding,) = [
                item
                for item in ownership.bindings
                if evidence.family(item.occurrence) == "blends" and item.feature is feature
            ]
            curves = [
                edge
                for ref in evidence.defining_faces(binding.occurrence)
                for edge in evidence.face(ref).edges()
                if edge.geom_type != GeomType.LINE
            ]
            assert curves
            tip = drawing.get_annotation(name).tip
            origin = drawing.at(view, 0, 0, 0)
            if axis == "x":
                assert all(edge.geom_type == GeomType.BSPLINE for edge in curves)
                y = (tip[0] - origin[0]) / (drawing.at(view, 0, 1, 0)[0] - origin[0])
                z = (tip[1] - origin[1]) / (drawing.at(view, 0, 0, 1)[1] - origin[1])
                ray = Edge.make_line((bounds.min.X - 1, y, z), (bounds.max.X + 1, y, z))
            else:
                x = (tip[0] - origin[0]) / (drawing.at(view, 1, 0, 0)[0] - origin[0])
                y = (tip[1] - origin[1]) / (drawing.at(view, 0, 1, 0)[1] - origin[1])
                ray = Edge.make_line((x, y, bounds.min.Z - 1), (x, y, bounds.max.Z + 1))
            assert min(edge.distance_to(ray) for edge in curves) < 1e-6, name
        polygonal_jobs = [job for job in captured_jobs if job.name == "m_polygonal_boss_z0"]
        assert polygonal_jobs
        assert all(job.analytical_geometry is not None for job in polygonal_jobs)
        assert len(constructed_polygonal) == len(polygonal_jobs), (
            "each page-layout assembly builds only its selected polygonal survivor"
        )
        assert constructed_polygonal[-1] is drawing.get_annotation("m_polygonal_boss_z0")


def test_pre_drain_boss_diameter_builds_only_its_analytical_survivor(monkeypatch, tmp_path):
    part = Box(90, 64, 38) + Pos(0, 0, 24) * Cylinder(14, 10)
    real_leader = from_model.Leader
    constructed = []

    def counted_leader(*args, **kwargs):
        leader = real_leader(*args, **kwargs)
        if str(leader.label) == "ø28":
            constructed.append(leader)
        return leader

    monkeypatch.setattr(from_model, "Leader", counted_leader)
    trace_path = tmp_path / "boss.json"
    drawing = build_drawing(part, trace=trace_path)

    boss = drawing.get_annotation("m_bossdia_z0")
    assert constructed == [boss], "only the selected analytical survivor builds OCC"
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    event = next(item for item in trace["pass_events"] if item["label"] == "boss_callouts")
    assert event["assignment"] == "greedy_stage_boundary"
    assert not [
        item
        for item in trace["pass_events"]
        if item["label"] == "feature_leader_inventory"
        and item["assignment"] == "greedy_stage_boundary"
    ], "an immediate producer batch must not impersonate the authoritative cross-pass drain"
