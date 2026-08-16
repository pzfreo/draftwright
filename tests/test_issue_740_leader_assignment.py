"""#740 — bounded collect-then-assign for machined-feature leader callouts."""

import ast
import json
import random
from itertools import combinations, product
from math import hypot
from pathlib import Path
from types import SimpleNamespace

import pytest
from build123d import Align, Axis, Box, Cylinder, Pos
from build123d_drafting.helpers import Draft, Leader

from draftwright import ScaleCompletenessWarning, Sheet, build_drawing
from draftwright._geometry import _segments_cross_or_overlap
from draftwright.annotations._common import _box_hits, annotation_obstacle_boxes
from draftwright.layout import _assign_leader_candidates
from draftwright.model import FilletFeature, Frame, GrooveFeature, PartModel, pocket


def test_joint_assignment_is_scoped_to_the_five_post_drain_adapters():
    root = Path(__file__).parents[1] / "src" / "draftwright" / "annotations"

    def call_sites(filename):
        tree = ast.parse((root / filename).read_text(encoding="utf-8"), filename=filename)
        sites = set()
        for function in (node for node in tree.body if isinstance(node, ast.FunctionDef)):
            for call in (node for node in ast.walk(function) if isinstance(node, ast.Call)):
                if not isinstance(call.func, ast.Name) or call.func.id != "_leader_callout_pass":
                    continue
                keyword = next((kw.value for kw in call.keywords if kw.arg == "joint"), None)
                sites.add(
                    (
                        filename,
                        function.name,
                        isinstance(keyword, ast.Constant) and keyword.value is True,
                    )
                )
        return sites

    assert call_sites("from_model.py") | call_sites("holes.py") == {
        ("from_model.py", "render_diameters", False),
        ("from_model.py", "render_chamfers", True),
        ("from_model.py", "render_fillets", True),
        ("from_model.py", "render_flats", True),
        ("from_model.py", "render_pockets", True),
        ("from_model.py", "render_grooves", True),
        ("from_model.py", "render_boss_diameters", False),
        ("from_model.py", "_render_polygonal_prisms", False),
        ("holes.py", "render_pocket_patterns", False),
        ("holes.py", "render_slot_patterns", False),
    }


def test_pre_drain_pattern_keeps_legacy_greedy_semantics(monkeypatch):
    def forbidden_joint_solve(*_args, **_kwargs):
        raise AssertionError("a pre-drain pattern must not enter #740's joint solver")

    monkeypatch.setattr(
        "draftwright.annotations.from_model._assign_leader_candidates",
        forbidden_joint_solve,
    )
    member = pocket(
        width=8.0,
        length=12.0,
        depth=4.0,
        long_axis="x",
        width_axis="y",
        depth_axis="z",
        lo=-6.0,
        hi=6.0,
        w_center=0.0,
        at=(0.0, 0.0, 4.0),
    )
    sheet = Sheet(Box(80, 60, 12)).auto_dimensions()
    sheet.pocket_pattern(member, kind="linear", count=3, pitch=20.0, direction=(1, 0, 0))

    drawing = sheet.build()

    assert [name for name in drawing.annotations() if name.startswith("m_pocketpat")]
    assert [name for name in drawing.annotations() if name.startswith("dim_pocketpat_pitch")]


def test_pre_drain_y_diameter_keeps_the_policy_b_fallback_tail(monkeypatch):
    """A clear ray blocked by fixed ink must not erase later obstructed fallbacks.

    #890 ranks projected-hole-clear rays first, but its pre-drain greedy pass keeps the
    obstructed Policy-B tail. Filtering that tail for #740's joint tier made this real
    diameter disappear before it could populate structured/downstream coverage.
    """
    locations = ((18, 0), (-18, 0), (0, 18), (0, -18))
    part = Cylinder(21, 4)
    for x, y in locations:
        part += Pos(x, y, 2) * Box(
            10,
            10,
            4,
            align=(Align.CENTER, Align.CENTER, Align.CENTER),
        )
    part += Pos(0, 0, 2) * Cylinder(15.5, 12)
    part += Pos(0, 0, 10) * Cylinder(12.5, 12)
    part -= Cylinder(8, 30)
    for x, y in locations:
        part -= Pos(x, y, 0) * Cylinder(2, 10)
    part = part.rotate(Axis.X, 90)

    def is_diagonal(tip, elbow):
        return (
            abs(float(elbow[0]) - float(tip[0])) > 1e-6
            and abs(float(elbow[1]) - float(tip[1])) > 1e-6
        )

    def controlled_clearance(candidate, _circles):
        return 1.0 if is_diagonal(candidate[0], candidate[1]) else -1.0

    def clear_ray_is_occupied(leader, obstacles, silhouette, page, *, geom_clear=False):
        if str(leader.label) == "ø25":
            # Model fixed inventory that blocks every preferred clear ray while one
            # hole-obstructed Policy-B ray remains usable.
            return not is_diagonal(leader.tip, leader.elbow)
        return True

    monkeypatch.setattr(
        "draftwright.annotations.from_model._leader_hole_clearance",
        controlled_clearance,
    )
    monkeypatch.setattr(
        "draftwright.annotations.from_model._label_lands_clear",
        clear_ray_is_occupied,
    )

    drawing = build_drawing(part, page="A4", scale=1.0, scale_policy="permissive")

    diameter = drawing.get_annotation("m_dia_y0")
    assert diameter.label == "ø25"
    assert not is_diagonal(diameter.tip, diameter.elbow)
    assert diameter.covers_diameters == (25.0,)
    assert drawing.measurement_keys("m_dia_y0")
    assert not [
        issue
        for issue in drawing.lint()
        if issue.code == "diameter_dropped" and "ø25" in issue.message
    ]


def test_pure_assignment_maximises_cardinality_before_leader_length():
    result = _assign_leader_candidates(
        ((20.0, 21.0), (20.0,)),
        ((0, 0, 1, 0),),
    )

    assert result.choices == (1, 0)
    assert result.optimal


def test_pure_assignment_minimises_length_at_equal_cardinality():
    result = _assign_leader_candidates(
        ((9.0, 1.0), (8.0, 2.0)),
        (),
    )

    assert result.choices == (1, 1)
    assert result.optimal


def test_sub_micron_cost_noise_reaches_stable_candidate_order_tie_break():
    result = _assign_leader_candidates(((10.0 + 1e-12, 10.0),), ())

    assert result.choices == (0,)


@pytest.mark.parametrize(
    ("costs", "conflicts", "max_states", "message"),
    (
        (((1.0,),), (), 0, "max_states"),
        (((-1.0,),), (), 1, "finite and non-negative"),
        (((float("nan"),),), (), 1, "finite and non-negative"),
        (((1.0,), (1.0,)), ((0, 0, 1),), 1, "four indices"),
        (((1.0,), (1.0,)), ((0, 0, 2, 0),), 1, "job index"),
        (((1.0,), (1.0,)), ((0, 1, 1, 0),), 1, "candidate index"),
    ),
)
def test_pure_assignment_rejects_malformed_numeric_inputs(costs, conflicts, max_states, message):
    with pytest.raises(ValueError, match=message):
        _assign_leader_candidates(costs, conflicts, max_states=max_states)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    (
        ({"priorities": (1.0,)}, "priorities must match the number of jobs"),
        ({"priorities": (1.0, float("nan"))}, "priorities must be finite"),
        ({"penalties_by_job": ((0,),)}, "penalties must match the candidate-cost shape"),
        ({"penalties_by_job": ((0,), (-1,))}, "penalties must be non-negative"),
    ),
)
def test_pure_assignment_rejects_malformed_objective_inputs(kwargs, message):
    with pytest.raises(ValueError, match=message):
        _assign_leader_candidates(((1.0,), (2.0,)), (), **kwargs)


def test_priority_bound_prunes_an_equal_cardinality_lower_priority_tail():
    result = _assign_leader_candidates(
        ((4.0,), (3.0, 3.0, 2.0)),
        ((0, 0, 1, 0), (0, 0, 1, 1), (0, 0, 1, 2)),
        priorities=(10.0, 1.0),
    )

    assert result.choices == (0, None)
    assert result.optimal
    assert result.states == 4


def test_same_job_conflict_is_redundant_with_one_candidate_per_job():
    result = _assign_leader_candidates(((1.0, 2.0),), ((0, 0, 0, 1),))

    assert result.choices == (0,)
    assert result.optimal
    assert result.states == 1


def test_bounded_solver_matches_exhaustive_lexicographic_oracle():
    rng = random.Random(740)
    for _case in range(250):
        costs = tuple(
            tuple(rng.randrange(0, 20) / 10 for _ in range(rng.randrange(4)))
            for _ in range(rng.randrange(1, 6))
        )
        conflicts = tuple(
            (left_job, left_candidate, right_job, right_candidate)
            for left_job in range(len(costs))
            for right_job in range(left_job + 1, len(costs))
            for left_candidate in range(len(costs[left_job]))
            for right_candidate in range(len(costs[right_job]))
            if rng.random() < 0.2
        )
        blocked = {
            ((left_job, left_candidate), (right_job, right_candidate))
            for left_job, left_candidate, right_job, right_candidate in conflicts
        }

        def score(choices):
            count = sum(choice is not None for choice in choices)
            cost = sum(
                int(round(costs[job][choice] * 1000))
                for job, choice in enumerate(choices)
                if choice is not None
            )
            tie = tuple(
                len(costs[job]) if choice is None else choice for job, choice in enumerate(choices)
            )
            return (-count, cost, tie)

        feasible = []
        domains = [(*range(len(job)), None) for job in costs]
        for choices in product(*domains):
            selected = [(job, choice) for job, choice in enumerate(choices) if choice is not None]
            if any(
                (left, right) in blocked
                for index, left in enumerate(selected)
                for right in selected[index + 1 :]
            ):
                continue
            feasible.append(choices)
        expected = min(feasible, key=score)

        result = _assign_leader_candidates(costs, conflicts)

        assert result.optimal
        assert result.choices == expected


def test_state_budget_retains_the_legacy_greedy_incumbent():
    result = _assign_leader_candidates(
        ((20.0, 21.0), (20.0,)),
        ((0, 0, 1, 0),),
        max_states=1,
    )

    assert result.choices == (0, None)
    assert not result.optimal
    assert result.states == 1


def test_production_state_budget_is_a_load_bearing_work_bound():
    rng = random.Random(1060)
    conflicts = [
        (left, 0, right, 0)
        for left in range(32)
        for right in range(left + 1, 32)
        if rng.random() < 0.15
    ]

    result = _assign_leader_candidates([(1.0,)] * 32, conflicts)

    assert not result.optimal
    assert result.states == 100_000
    assert sum(choice is not None for choice in result.choices) >= 1


def test_dense_conflicted_inventory_has_a_nonrecursive_greedy_floor():
    conflicts = [(index, 0, index + 1, 0) for index in range(256)]
    result = _assign_leader_candidates([(1.0,)] * 257, conflicts)

    assert sum(choice is not None for choice in result.choices) == 129
    assert not result.optimal
    assert result.states == 0


def test_real_leader_geometry_makes_the_counterexample_load_bearing():
    draft = Draft()
    first = Leader(tip=(10.0, 10.0, 0.0), elbow=(30.0, 10.0, 0.0), label="A", draft=draft)
    alternate = Leader(tip=(10.0, 30.0, 0.0), elbow=(31.0, 30.0, 0.0), label="A", draft=draft)
    constrained = Leader(tip=(50.0, 10.0, 0.0), elbow=(30.0, 10.0, 0.0), label="B", draft=draft)
    drawing = SimpleNamespace(draft=draft)

    assert _box_hits(constrained.label_bbox, annotation_obstacle_boxes(drawing, first))
    assert not _box_hits(
        constrained.label_bbox,
        annotation_obstacle_boxes(drawing, alternate),
    )
    lengths = tuple(
        sum(hypot(end[0] - start[0], end[1] - start[1]) for start, end in leader.segments)
        for leader in (first, alternate, constrained)
    )
    result = _assign_leader_candidates(
        ((lengths[0], lengths[1]), (lengths[2],)),
        ((0, 0, 1, 0),),
    )

    assert result.choices == (1, 0)


def _crowded_declared_grooves():
    shaft = Cylinder(10, 60)
    grooves = [
        GrooveFeature(Frame((0.0, 0.0, z), "z"), "z", 2.0, 14.0 + index)
        for index, z in enumerate((-2.0, 0.0, 2.0))
    ]
    return shaft, PartModel(shaft.bounding_box(), "z", grooves)


def _groove_leader_evidence(drawing):
    names = sorted(name for name in drawing.annotations() if name.startswith("m_groove"))
    lengths = {
        name: sum(
            hypot(end[0] - start[0], end[1] - start[1])
            for start, end in drawing.get_annotation(name).segments
        )
        for name in names
    }
    measurements = {name: drawing.measurement_keys(name) for name in names}
    return names, lengths, measurements


def test_public_render_is_crossing_free_and_pair_budget_is_legacy_floor(monkeypatch, tmp_path):
    part, model = _crowded_declared_grooves()
    joint = build_drawing(part, model=model, page="A4")
    joint_names, _joint_lengths, joint_measurements = _groove_leader_evidence(joint)
    joint_boxes = {name: joint.get_annotation(name).label_bbox for name in joint_names}

    # These 2-mm stations make the legacy first rays cross. The cross-pass lane
    # alternatives let the exact-ink inventory retain all three facts without a
    # rendered shaft/arrow conflict.
    assert all(
        not _box_hits(joint_boxes[left], [joint_boxes[right]])
        for left, right in combinations(joint_names, 2)
    )

    # Force the real public renderer through its deterministic pre-quadratic
    # fallback. This is the pre-#740 first-clear result, not a parallel test
    # implementation of it.
    monkeypatch.setattr(
        "draftwright.annotations.leaders._FEATURE_LEADER_MAX_PAIR_PROBES",
        0,
    )
    trace_path = tmp_path / "legacy.json"
    legacy = build_drawing(part, model=model, page="A4", trace=trace_path)
    legacy_names, _legacy_lengths, legacy_measurements = _groove_leader_evidence(legacy)
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    event = next(item for item in trace["pass_events"] if item["label"] == "groove_callouts")

    assert joint_names == ["m_groove_z0", "m_groove_z1", "m_groove_z2"]
    assert legacy_names == ["m_groove_z0", "m_groove_z1", "m_groove_z2"]
    assert joint_measurements == {name: legacy_measurements[name] for name in joint_names}
    assert all(joint.registry.feature_of(name) is not None for name in joint_names)
    assert not any(
        _segments_cross_or_overlap(a0, a1, b0, b1)
        for left, right in combinations(joint_names, 2)
        for a0, a1 in joint.get_annotation(left).segments
        for b0, b1 in joint.get_annotation(right).segments
    )
    # The bounded floor deliberately preserves the pre-#740 placement even
    # though #1166's stricter real-shaft inventory can now see its crossing.
    assert any(
        _segments_cross_or_overlap(a0, a1, b0, b1)
        for left, right in combinations(legacy_names, 2)
        for a0, a1 in legacy.get_annotation(left).segments
        for b0, b1 in legacy.get_annotation(right).segments
    )
    assert event["assignment"] == "greedy_pair_budget"
    assert event["optimal"] is False
    assert not [issue for issue in joint.lint() if issue.code == "groove_dropped"]
    assert not [issue for issue in legacy.lint() if issue.code == "groove_dropped"]

    # The pass-size bound fires before collect-all OCC construction. Lower the
    # production constant to make that branch load-bearing on the same public
    # fixture without manufacturing hundreds of CAD annotations in CI.
    monkeypatch.setattr("draftwright.annotations.leaders._LEADER_ASSIGN_MAX_JOBS", 2)
    job_trace_path = tmp_path / "legacy-job-budget.json"
    job_floor = build_drawing(part, model=model, page="A4", trace=job_trace_path)
    job_names, _job_lengths, job_measurements = _groove_leader_evidence(job_floor)
    job_trace = json.loads(job_trace_path.read_text(encoding="utf-8"))
    job_event = next(
        item for item in job_trace["pass_events"] if item["label"] == "groove_callouts"
    )

    assert job_names == legacy_names
    assert job_measurements == legacy_measurements
    assert job_event["assignment"] == "greedy_job_budget"
    assert job_event["optimal"] is False


def test_grouped_job_candidate_budget_precedes_occ_construction(monkeypatch, tmp_path):
    shaft = Cylinder(10, 20)
    fillets = [FilletFeature(Frame((10.0, 0.0, 10.0), "z"), "z", 1.0) for _index in range(257)]
    created = 0

    def counted_leader(*args, **kwargs):
        nonlocal created
        created += 1
        return Leader(*args, **kwargs)

    monkeypatch.setattr("draftwright.annotations.from_model.Leader", counted_leader)
    trace_path = tmp_path / "candidate-budget.json"
    drawing = build_drawing(
        shaft,
        model=PartModel(shaft.bounding_box(), "z", fillets),
        page="A4",
        trace=trace_path,
    )

    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    event = next(item for item in trace["pass_events"] if item["label"] == "fillet_callouts")
    annotation = drawing.get_annotation("m_fillet_z0")

    assert created == 1  # the legacy first-clear floor, not all 257 OCC alternatives
    assert annotation.label == "257× R1"
    assert len(drawing.measurement_keys("m_fillet_z0")) == 257
    assert event["assignment"] == "greedy_candidate_budget"
    assert event["optimal"] is False


def test_grouped_job_at_candidate_budget_still_uses_joint_assignment(monkeypatch, tmp_path):
    shaft = Cylinder(10, 20)
    fillets = [FilletFeature(Frame((10.0, 0.0, 10.0), "z"), "z", 1.0) for _index in range(2)]
    created = 0

    def counted_leader(*args, **kwargs):
        nonlocal created
        created += 1
        return Leader(*args, **kwargs)

    monkeypatch.setattr("draftwright.annotations.leaders._FEATURE_LEADER_MAX_CANDIDATES", 54)
    monkeypatch.setattr("draftwright.annotations.from_model.Leader", counted_leader)
    trace_path = tmp_path / "candidate-budget-boundary.json"
    drawing = build_drawing(
        shaft,
        model=PartModel(shaft.bounding_box(), "z", fillets),
        page="A4",
        trace=trace_path,
    )

    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    event = next(item for item in trace["pass_events"] if item["label"] == "fillet_callouts")

    assert created == 54
    assert drawing.get_annotation("m_fillet_z0").label == "2× R1"
    assert event["assignment"] == "joint"
    assert event["optimal"] is True


def test_candidate_budget_is_global_across_jobs(monkeypatch, tmp_path):
    part, model = _crowded_declared_grooves()
    monkeypatch.setattr("draftwright.annotations.leaders._FEATURE_LEADER_MAX_CANDIDATES", 16)
    trace_path = tmp_path / "global-candidate-budget.json"

    drawing = build_drawing(
        part,
        model=model,
        page="A4",
        scale=1,
        scale_policy="permissive",
        trace=trace_path,
    )

    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    event = next(item for item in trace["pass_events"] if item["label"] == "groove_callouts")

    assert event["assignment"] == "greedy_candidate_budget"
    assert event["optimal"] is False
    assert [item["candidates_tried"] for item in event["items"]] == [1, 4, 1]
    assert len([name for name in drawing.annotations() if name.startswith("m_groove")]) == 3


def test_candidate_budget_fallback_restores_consumed_prefix_and_lazy_tail(monkeypatch, tmp_path):
    shaft = Cylinder(10, 20)
    fillets = [
        FilletFeature(Frame(origin, "z"), "z", 1.0)
        for origin in ((-1000.0, 0.0, 10.0), (-900.0, 0.0, 10.0), (10.0, 0.0, 10.0))
    ]
    monkeypatch.setattr("draftwright.annotations.leaders._FEATURE_LEADER_MAX_CANDIDATES", 2)
    trace_path = tmp_path / "candidate-budget-tail.json"

    drawing = build_drawing(
        shaft,
        model=PartModel(shaft.bounding_box(), "z", fillets),
        page="A4",
        scale=1,
        scale_policy="permissive",
        trace=trace_path,
    )

    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    event = next(item for item in trace["pass_events"] if item["label"] == "fillet_callouts")
    (item,) = event["items"]

    assert event["assignment"] == "greedy_candidate_budget"
    assert item["candidates_tried"] == 3
    assert item["candidate"] == 2  # the untouched generator tail, after two invalid members
    assert drawing.get_annotation("m_fillet_z0").label == "3× R1"
    assert len(drawing.measurement_keys("m_fillet_z0")) == 3


def test_trace_identifies_a_joint_conflict_between_fixed_clear_candidates(tmp_path):
    shaft = Cylinder(10, 60)
    grooves = [
        GrooveFeature(Frame((0.0, 0.0, 0.0), "z"), "z", 2.0, 14.0 + index * 0.1)
        for index in range(6)
    ]
    trace_path = tmp_path / "joint-conflict.json"

    with pytest.warns(ScaleCompletenessWarning, match="groove_dropped"):
        build_drawing(
            shaft,
            model=PartModel(shaft.bounding_box(), "z", grooves),
            page="A4",
            scale=1,
            scale_policy="permissive",
            trace=trace_path,
        )

    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    event = next(item for item in trace["pass_events"] if item["label"] == "groove_callouts")
    dropped = [item for item in event["items"] if item["outcome"] == "dropped"]
    # All six declarations share one physical tip. The bounded lane inventory
    # finds two non-overlapping ray directions; the remaining four conflict with
    # one of those exact rendered arrow/shaft footprints.
    assert len(dropped) == 4
    # Thirty-three candidates clear the fixed inventory; six additional
    # fixed-ink crossings remain explicit Policy-B alternatives so semantic
    # cardinality is still primary and any retained crossing is diagnosed.
    assert all(item["viable_candidates"] == 39 for item in dropped)
    assert all(item["reason"] == "assignment_conflict" for item in dropped)
    assert all(
        any(
            blocker.startswith("m_groove")
            for rejected in item["rejected"]
            for blocker in rejected["blockers"]
        )
        for item in dropped
    )


def test_bounded_legacy_fallback_preserves_drop_diagnostic(monkeypatch, tmp_path):
    shaft = Cylinder(10, 60)
    grooves = [
        GrooveFeature(Frame((0.0, 0.0, 0.0), "z"), "z", 2.0, 14.0 + index * 0.1)
        for index in range(6)
    ]
    monkeypatch.setattr(
        "draftwright.annotations.leaders._FEATURE_LEADER_MAX_PAIR_PROBES",
        0,
    )
    trace_path = tmp_path / "greedy-drop.json"

    with pytest.warns(ScaleCompletenessWarning, match="groove_dropped"):
        drawing = build_drawing(
            shaft,
            model=PartModel(shaft.bounding_box(), "z", grooves),
            page="A4",
            scale=1,
            scale_policy="permissive",
            trace=trace_path,
        )

    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    event = next(item for item in trace["pass_events"] if item["label"] == "groove_callouts")
    dropped = [item for item in event["items"] if item["outcome"] == "dropped"]
    issues = [issue for issue in drawing.registry.issues if issue.code == "groove_dropped"]

    assert len([name for name in drawing.annotations() if name.startswith("m_groove")]) == 5
    assert event["assignment"] == "greedy_pair_budget"
    assert event["optimal"] is False
    assert len(dropped) == 1
    assert dropped[0]["name"] == "m_groove_z5"
    assert dropped[0]["label"] == "2 WIDE × ø14.5"
    assert dropped[0]["candidates_tried"] == 8
    assert dropped[0]["reason"] == "no_clear_room"
    assert len(issues) == 1
    assert len(issues[0].measurement_ids) == 2


def test_state_budget_replays_the_producer_legacy_floor(monkeypatch, tmp_path):
    shaft = Cylinder(10, 60)
    grooves = [
        GrooveFeature(Frame((0.0, 0.0, 0.0), "z"), "z", 2.0, 14.0 + index * 0.1)
        for index in range(6)
    ]
    import draftwright.annotations.leaders as leader_placement

    original = leader_placement._assign_leader_candidates

    def one_state(*args, **kwargs):
        kwargs["max_states"] = 1
        return original(*args, **kwargs)

    monkeypatch.setattr(leader_placement, "_assign_leader_candidates", one_state)
    trace_path = tmp_path / "state-budget-floor.json"
    with pytest.warns(ScaleCompletenessWarning, match="groove_dropped"):
        drawing = build_drawing(
            shaft,
            model=PartModel(shaft.bounding_box(), "z", grooves),
            page="A4",
            scale=1,
            scale_policy="permissive",
            trace=trace_path,
        )

    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    event = next(item for item in trace["pass_events"] if item["label"] == "groove_callouts")
    names = [name for name in drawing.annotations() if name.startswith("m_groove")]
    issues = [issue for issue in drawing.registry.issues if issue.code == "groove_dropped"]

    assert event["assignment"] == "greedy_state_budget"
    assert event["optimal"] is False
    assert event["states"] > 0
    assert len(names) == 5
    assert len(issues) == 1
    assert all("producer_fallback" in item for item in event["items"])
    assert all(
        sorted(candidate["candidate"] for candidate in item["candidate_inventory"])
        == list(range(item["candidates_tried"]))
        for item in event["items"]
    )
    assert all(
        candidate["outcome"] in {"fixed_rejected", "joint_abandoned"}
        for item in event["items"]
        for candidate in item["candidate_inventory"]
    )
    assert any(
        item["candidates_tried"] > item["producer_fallback"]["candidates_tried"]
        for item in event["items"]
    )
    assert any(
        candidate.get("assignment_blockers")
        for item in event["items"]
        for candidate in item["candidate_inventory"]
        if candidate["outcome"] == "joint_abandoned"
    )
