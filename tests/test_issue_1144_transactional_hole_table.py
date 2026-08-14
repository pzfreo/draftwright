"""Regression coverage for transactional automatic hole-table escalation (#1144)."""

from __future__ import annotations

import inspect
import itertools
from dataclasses import replace
from types import SimpleNamespace

import pytest
from build123d import Align, Box, Cylinder, Pos

from draftwright import build_drawing
from draftwright.fits import fit_class
from draftwright.linting.hole_coverage import hole_requirement_outcomes
from draftwright.model.compiled import compile_dimensions


def _multi_hole_plate():
    """Two semantic groups: one Ø16 bore and a two-member Ø10 group."""
    return (
        Box(120, 80, 20)
        - Pos(40, 25, 0) * Cylinder(5, 30)
        - Pos(-40, 25, 0) * Cylinder(5, 30)
        - Pos(0, -25, 0) * Cylinder(8, 30)
    )


def _bolt_circle_plate():
    xyz_min = (Align.CENTER, Align.CENTER, Align.MIN)
    part = Box(80, 80, 10, align=xyz_min)
    for x, y in (
        (25.0, 0.0),
        (12.5, 21.650635),
        (-12.5, 21.650635),
        (-25.0, 0.0),
        (-12.5, -21.650635),
        (12.5, -21.650635),
    ):
        part -= Pos(x, y, 0) * Cylinder(3, 10, align=xyz_min)
    return part


def _dense_scattered_plate():
    """Twenty distinct-spec bores, forcing automatic table escalation."""
    part = Box(90, 60, 12)
    columns = [-40 + i * 20 for i in range(5)]
    for i, (column, y) in enumerate(itertools.product(range(5), (-18, -6, 6, 18))):
        part -= Pos(columns[column], y, 0) * Cylinder(1.0 + i * 0.2, 20)
    return part


def _dense_plate_with_close_x_ordinates():
    """Dense inventory with two separately placed X ordinates only 0.4 mm apart."""
    positions = (
        [(x, -30.0) for x in (-45.0, 0.4, 15.0, 45.0)]
        + [(x, 30.0) for x in (-45.0, 0.0, 15.0, 45.0)]
        + [(-54.0, y) for y in (-18.0, -6.0, 6.0, 18.0)]
        + [(54.0, y) for y in (-18.0, -6.0, 6.0, 18.0)]
    )
    part = Box(120, 80, 12)
    for index, (x, y) in enumerate(positions):
        part -= Pos(x, y, 0) * Cylinder(1.0 + index * 0.15, 20)
    return part


def _dense_perimeter_plate():
    """Sixteen scattered bores whose short outward balloons are collision-free."""
    positions = (
        [(x, -30.0) for x in (-45.0, -15.0, 15.0, 45.0)]
        + [(x, 30.0) for x in (-45.0, -15.0, 15.0, 45.0)]
        + [(-54.0, y) for y in (-18.0, -6.0, 6.0, 18.0)]
        + [(54.0, y) for y in (-18.0, -6.0, 6.0, 18.0)]
    )
    part = Box(120, 80, 12)
    for index, (x, y) in enumerate(positions):
        part -= Pos(x, y, 0) * Cylinder(1.0 + index * 0.15, 20)
    return part


def _dense_plate_with_counterbore():
    """Reviewer fixture whose diagonal balloon AABB used to reject a clear leader."""
    positions = (
        [(x, -30.0) for x in (-45.0, -15.0, 15.0, 45.0)]
        + [(x, 30.0) for x in (-45.0, -15.0, 15.0, 45.0)]
        + [(-54.0, y) for y in (-18.0, -6.0, 6.0, 18.0)]
        + [(54.0, y) for y in (-18.0, -6.0, 6.0, 18.0)]
    )
    part = Box(120, 80, 12)
    for i, (x, y) in enumerate(positions):
        diameter = 2.0 + i * 0.4
        part -= Pos(x, y, 0) * Cylinder(diameter / 2, 20)
        if i == len(positions) - 1:
            part -= Pos(x, y, 4.5) * Cylinder((diameter + 3.0) / 2, 3)
    return part


def _dense_plate_with_double_d():
    part = Box(90, 60, 12)
    columns = [-40 + i * 20 for i in range(5)]
    centered = (Align.CENTER, Align.CENTER, Align.CENTER)
    for i, (column, y) in enumerate(itertools.product(range(5), (-18, -6, 6, 18))):
        if i == 0:
            cutter = Cylinder(2.5, 20, align=centered) & Box(3.6, 20, 30, align=centered)
        else:
            cutter = Cylinder(1.0 + i * 0.2, 20)
        part -= Pos(columns[column], y, 0) * cutter
    return part


def _dense_plate_with_one_group():
    """Twenty bores, including one physical two-member machining-spec group."""
    positions = [
        (-39, -19),
        (-27, -14),
        (-14, -21),
        (1, -16),
        (17, -22),
        (35, -17),
        (-36, -4),
        (-21, 2),
        (-7, -6),
        (9, 1),
        (24, -5),
        (39, 4),
        (-34, 14),
        (-18, 21),
        (-2, 12),
        (13, 20),
        (29, 13),
        (38, 23),
        (-28, 26),
        (4, 27),
    ]
    part = Box(100, 70, 12)
    for index, (x, y) in enumerate(positions):
        radius = 2.0 if index < 2 else 1.0 + index * 0.17
        part -= Pos(x, y, 0) * Cylinder(radius, 20)
    return part


def _outcomes(drawing):
    drawing.lint()
    return hole_requirement_outcomes(
        drawing.recognition(),
        drawing.model().features,
        drawing.registry,
        compile_dimensions(drawing.model()).diagnostics,
    )


def _transaction_state(drawing):
    registry = drawing.registry.snapshot()
    return {
        "items": tuple(id(item) for item in drawing.items),
        "named": {name: id(item) for name, item in registry["named"].items()},
        "views": registry["anno_view"],
        "features": registry["anno_feature"],
        "measurements": registry["anno_measurement"],
        "pins": registry["pinned"],
        "issues": drawing.registry.issues,
        "coverage": drawing.coverage.snapshot(),
        "markers": {
            name: (
                getattr(drawing.get_annotation(name), "hole_representation", None),
                getattr(drawing.get_annotation(name), "hole_representation_reason", None),
            )
            for name in drawing.annotations()
        },
    }


def _plan_bore_callouts(drawing):
    callouts = {}
    for name in drawing.annotations():
        if drawing.registry.view_of(name) != "plan":
            continue
        diameter_ids = [
            item
            for item in drawing.registry.measurement_of(name)
            if item.parameter == "bore.diameter"
        ]
        if len(diameter_ids) == 1:
            callouts[diameter_ids[0].feature] = (name, drawing.get_annotation(name))
    return callouts


def _drop_one_diameter_balloon(monkeypatch, diameter):
    import draftwright.annotations.balloons as balloons_module

    original = balloons_module._assign_balloon_bands

    def partial_assignment(*args, **kwargs):
        bands, dropped = original(*args, **kwargs)
        for members in bands.values():
            for index, member in enumerate(members):
                if member[2].diameter == pytest.approx(diameter):
                    members.pop(index)
                    return bands, dropped + 1
        raise AssertionError(f"no assigned Ø{diameter:g} balloon to remove")

    monkeypatch.setattr(balloons_module, "_assign_balloon_bands", partial_assignment)


def _features_from_issue(issue):
    return {
        feature
        for feature in (
            *(getattr(measurement, "feature", None) for measurement in issue.measurement_ids),
            *(requirement[0] for requirement in issue.hole_requirement_ids),
        )
        if feature is not None
    }


def test_annotation_hole_features_unions_every_semantic_owner_channel():
    from draftwright.annotations._common import _annotation_hole_features
    from draftwright.registry import AnnotationRegistry

    def feature(kind="hole"):
        return type("Feature", (), {"kind": kind})()

    owner = feature()
    measurement_owner = feature()
    direct_location_owner = feature()
    identified_location_owner = feature()
    requirement_owner = feature()
    representation_owner = feature()
    scoped_representation_owner = feature()
    center_owner = feature()
    ignored_owner = feature("boss")
    annotation = SimpleNamespace(
        covers_hole_locations=(
            (direct_location_owner, (1.0, 2.0, 3.0), "plan"),
            (SimpleNamespace(feature=identified_location_owner), (1.0, 2.0, 3.0)),
            (ignored_owner, (1.0, 2.0, 3.0), "plan"),
        ),
        covers_hole_requirements_by_feature=(
            (requirement_owner, "bore.through", 1),
            (ignored_owner, "bore.through", 1),
        ),
        covers_hole_representations_by_feature=(
            (representation_owner, "hole_table", "placed"),
            (ignored_owner, "hole_table", "placed"),
        ),
        covers_hole_representations_by_requirement=(
            (
                scoped_representation_owner,
                "location.location.x",
                "hole_table",
                "required_balloons_placed",
            ),
            (ignored_owner, "location.location.x", "hole_table", "placed"),
        ),
        covers_hole_centers=(
            (center_owner, (0.0, 0.0, 0.0), "plan"),
            (ignored_owner, (0.0, 0.0, 0.0), "plan"),
        ),
    )
    registry = AnnotationRegistry()
    registry.add(
        annotation,
        "semantic_union",
        "plan",
        feature=owner,
        measurement=SimpleNamespace(feature=measurement_owner),
    )

    assert _annotation_hole_features(registry, "semantic_union", annotation) == frozenset(
        {
            owner,
            measurement_owner,
            direct_location_owner,
            identified_location_owner,
            requirement_owner,
            representation_owner,
            scoped_representation_owner,
            center_owner,
        }
    )


def test_hole_table_eligibility_fails_closed_without_a_plain_hole_owner():
    from draftwright.annotations._common import (
        _hole_table_replaceable_annotation,
        _hole_table_replaceable_feature,
        _hole_table_replaceable_location_annotation,
    )
    from draftwright.registry import AnnotationRegistry

    registry = AnnotationRegistry()
    annotation = SimpleNamespace()
    pattern = type("Feature", (), {"kind": "pattern"})()

    assert not _hole_table_replaceable_annotation(registry, "missing", annotation)
    assert not _hole_table_replaceable_location_annotation(registry, "missing", annotation)
    assert not _hole_table_replaceable_feature(pattern)

    registry.add(annotation, "pattern_annotation", "plan", feature=pattern)
    assert not _hole_table_replaceable_annotation(registry, "pattern_annotation", annotation)
    assert not _hole_table_replaceable_location_annotation(
        registry, "pattern_annotation", annotation
    )


def test_table_commit_requires_exact_balloon_cardinality_and_owner():
    from draftwright.annotations._common import _fully_ballooned_features
    from draftwright.registry import AnnotationRegistry

    feature = type("Feature", (), {"kind": "hole"})()
    wrong_owner = type("Feature", (), {"kind": "hole"})()
    tagged = [
        ("A", 0, object(), feature),
        ("A", 1, object(), feature),
    ]
    registry = AnnotationRegistry()
    first, second = object(), object()
    registry.add(first, "balloon_plan_A_0", "plan", feature=feature)

    # A declared QTY=2 row is not committed from one landed member.
    assert not _fully_ballooned_features(
        "plan",
        tagged,
        {"balloon_plan_A_0"},
        registry,
        {feature: 2},
    )

    # The right attempt-local name is still not evidence when another feature
    # owns that physical balloon position.
    registry.add(second, "balloon_plan_A_1", "plan", feature=wrong_owner)
    assert not _fully_ballooned_features(
        "plan",
        tagged,
        {"balloon_plan_A_0", "balloon_plan_A_1"},
        registry,
        {feature: 2},
    )

    registry.add(second, "balloon_plan_A_1", "plan", feature=feature)
    assert _fully_ballooned_features(
        "plan",
        tagged,
        {"balloon_plan_A_0", "balloon_plan_A_1"},
        registry,
        {feature: 2},
    ) == {feature}


def test_guarded_balloon_obstacles_use_compact_labels_and_note_boxes():
    from draftwright.annotations._common import balloon_annotation_label_boxes

    class BoxedNote:
        def bounding_box(self):
            return SimpleNamespace(
                min=SimpleNamespace(X=3.0, Y=4.0),
                max=SimpleNamespace(X=8.0, Y=9.0),
            )

    class UnreadableLabel(BoxedNote):
        @property
        def label_bbox(self):
            raise RuntimeError("external label metadata unavailable")

    class Unmeasurable:
        label_bbox = None

        def bounding_box(self):
            raise RuntimeError("external annotation does not measure")

    label = SimpleNamespace(label_bbox=(1.0, 1.0, 2.0, 2.0))
    note = BoxedNote()
    unreadable = UnreadableLabel()
    unmeasurable = Unmeasurable()
    centerline = SimpleNamespace(
        is_centerline=True,
        label_bbox=(30.0, 30.0, 31.0, 31.0),
    )
    other_view = SimpleNamespace(label_bbox=(20.0, 20.0, 21.0, 21.0))
    drawing = SimpleNamespace(
        box_cache={},
        iter_annotations=lambda: iter(
            (
                ("label", label),
                ("note", note),
                ("unreadable", unreadable),
                ("unmeasurable", unmeasurable),
                ("centerline", centerline),
                ("other", other_view),
            )
        ),
        view_of=lambda name: "side" if name == "other" else "plan",
    )

    assert balloon_annotation_label_boxes(drawing, "plan") == (
        (1.0, 1.0, 2.0, 2.0),
        (3.0, 4.0, 8.0, 9.0),
        (3.0, 4.0, 8.0, 9.0),
    )


def test_segmented_centerline_diagnostic_ignores_only_the_empty_aabb_triangle():
    from draftwright.linting.structural import _label_centerline_overlap

    label = SimpleNamespace(label="CALL", label_bbox=(0.0, 0.0, 2.0, 2.0))
    clear_elbow = SimpleNamespace(
        segments=(((-1.0, -1.0), (3.0, -1.0)), ((3.0, -1.0), (3.0, 3.0)))
    )
    crossing = SimpleNamespace(segments=(((-1.0, 1.0), (3.0, 1.0)),))

    assert _label_centerline_overlap(label, clear_elbow) is None
    assert _label_centerline_overlap(label, crossing) is not None

    # A balloon's ring/text glyph is a real occupied component even when its
    # leader shaft stays clear.  The precise component box keeps that collision
    # visible without restoring the compound's empty diagonal AABB triangle.
    balloon_glyph = SimpleNamespace(
        centerline_segments=(((-1.0, -1.0), (3.0, -1.0)),),
        centerline_boxes=((0.5, 0.5, 1.5, 1.5),),
    )
    assert _label_centerline_overlap(label, balloon_glyph) is not None
    assert (
        _label_centerline_overlap(
            SimpleNamespace(label="", label_bbox=None),
            balloon_glyph,
        )
        is None
    )


def test_centerline_warning_tolerance_uses_the_actual_colliding_component():
    from draftwright.linting.structural import _label_centerline_overlap

    balloon = SimpleNamespace(
        centerline_segments=(((-10.0, 0.5), (0.0, 0.5)),),
        centerline_boxes=((0.0, 0.0, 1.0, 1.0),),
    )
    edge_touch = SimpleNamespace(label="EDGE", label_bbox=(0.9, 0.0, 2.0, 1.0))
    significant = SimpleNamespace(label="HIT", label_bbox=(0.4, 0.0, 2.0, 1.0))

    assert _label_centerline_overlap(edge_touch, balloon) is None
    assert _label_centerline_overlap(significant, balloon) is not None


def test_near_axis_segment_keeps_deep_label_penetration_visible():
    from draftwright.linting.structural import _label_centerline_overlap

    label = SimpleNamespace(label="CALL", label_bbox=(0.0, 0.0, 10.0, 10.0))
    vertical = SimpleNamespace(centerline_segments=(((5.0, -1.0), (5.0, 11.0)),))
    near_vertical = SimpleNamespace(centerline_segments=(((4.9, -1.0), (5.1, 11.0)),))

    assert _label_centerline_overlap(label, vertical) is not None
    assert _label_centerline_overlap(label, near_vertical) is not None


def test_long_public_balloon_text_remains_visible_to_structural_lint():
    from draftwright.linting.structural import _label_centerline_overlap

    drawing = build_drawing(_multi_hole_plate(), page="A4", auto_dims=False)
    hole = drawing.recognition().holes[0]
    drawing.add_balloons("plan", [("LONG_BALLOON_TAG", 0, hole)])
    balloon = drawing.get_annotation("balloon_plan_LONG_BALLOON_TAG_0")
    ring_box, text_box = balloon.centerline_boxes

    assert text_box[0] < ring_box[0]
    overlap_box = (
        text_box[0] + 0.1,
        (text_box[1] + text_box[3]) / 2 - 0.6,
        ring_box[0] - 0.1,
        (text_box[1] + text_box[3]) / 2 + 0.6,
    )
    label = SimpleNamespace(label="OTHER", label_bbox=overlap_box)
    assert _label_centerline_overlap(label, balloon) is not None


def test_empty_public_balloon_tag_does_not_create_a_page_origin_lint_box():
    from draftwright.linting.structural import _label_centerline_overlap

    drawing = build_drawing(_multi_hole_plate(), page="A4", auto_dims=False)
    hole = drawing.recognition().holes[0]
    drawing.add_balloons("plan", [("", 0, hole)])
    balloon = drawing.get_annotation("balloon_plan__0")

    assert len(balloon.centerline_boxes) == 1
    origin_label = SimpleNamespace(label="ORIGIN", label_bbox=(-1.0, -1.0, 1.0, 1.0))
    assert _label_centerline_overlap(origin_label, balloon) is None


def test_guarded_assignment_preserves_the_canonical_flow_cost_objective():
    from draftwright.annotations.balloons import (
        _guarded_assignment,
        _GuardedSegments,
    )

    members = [
        ("A", 0, object(), 0.0, 0.0),
        ("B", 0, object(), 0.0, 0.0),
        ("C", 0, object(), 0.0, 0.0),
    ]
    choices = [
        {"left": 2.0, "right": 13.0},
        {"left": 16.0, "right": 1.0},
        {"left": 8.0},
    ]
    bands = {
        "left": ("y", 0.0, 0.0, 10.0),
        "right": ("y", 1.0, 0.0, 10.0),
    }
    guarded = _GuardedSegments(
        {
            (0, "left"): ((2.0, 2.0),),
            (0, "right"): ((2.0, 2.0),),
            (1, "left"): ((5.0, 5.0),),
            (1, "right"): ((2.0, 2.0),),
            (2, "left"): ((0.0, 0.0),),
        },
        1.0,
    )

    assignments, solutions, dropped = _guarded_assignment(
        members,
        choices,
        bands,
        {"left": 1, "right": 1},
        guarded,
    )

    assert dropped == 1
    assert assignments == {"left": [0], "right": [1]}
    assert solutions == {"left": {0: 2.0}, "right": {1: 2.0}}


def test_guarded_assignment_is_bounded_and_fails_an_infeasible_flow_closed(monkeypatch):
    import draftwright.annotations.balloons as balloons_module

    band_names = ("left", "right", "top", "bottom")
    member_count = 28
    members = [
        (str(index), 0, object(), float(index), float(index)) for index in range(member_count)
    ]
    bands = {
        band: ("y", float(index), 0.0, float(member_count))
        for index, band in enumerate(band_names)
    }
    choices = [{band: 0.0 for band in band_names} for _member in members]
    guarded = balloons_module._GuardedSegments(
        {
            (index, band): ((float(member_count - index),) * 2,)
            for index in range(member_count)
            for band in band_names
        },
        1.0,
    )
    calls = 0
    original = balloons_module._solve_guarded_band

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(balloons_module, "_solve_guarded_band", counted)

    assignments, solutions, dropped = balloons_module._guarded_assignment(
        members,
        choices,
        bands,
        {band: member_count for band in band_names},
        guarded,
    )

    assert calls == len(band_names)
    assert assignments == {band: [] for band in band_names}
    assert solutions == {band: {} for band in band_names}
    assert dropped == member_count


def test_guarded_solver_budget_rolls_the_automatic_table_transaction_back(monkeypatch):
    import draftwright.layout as layout_module

    # Make the production resource guard load-bearing without constructing a
    # pathological state matrix in the test process.  Exceeding the budget is
    # an ordinary infeasible guarded inventory: the shared table and all of its
    # balloons must lose to the complete feature-backed fallback inventory.
    monkeypatch.setattr(layout_module, "_GUARDED_STRIP_MAX_STATES", 1)

    drawing = build_drawing(_dense_scattered_plate(), page="A3")

    assert "hole_table_plan" not in drawing.annotations()
    assert not [name for name in drawing.annotations() if name.startswith("balloon_plan_")]
    assert len([name for name in drawing.annotations() if name.startswith("hc_plan")]) == 17
    codes = [issue.code for issue in drawing.registry.issues]
    assert "table_dropped" in codes
    assert "balloon_dropped" in codes
    assert not [issue for issue in drawing.lint() if issue.code == "hole_requirement_missing"]


def test_real_shaft_crossing_carves_the_guarded_band_without_aabb_sampling():
    from draftwright.annotations.balloons import (
        _balloon_shaft_segments,
        _guarded_free_segments,
    )

    assert _balloon_shaft_segments(0.0, 0.0, 1.0, 0.0, 1.0, 1.0) == ()

    member = ("A", 0, SimpleNamespace(diameter=2.0), 0.0, 0.0)
    free = _guarded_free_segments(
        member,
        "y",
        10.0,
        ((0.0, 10.0),),
        1.0,
        1.0,
        ((4.0, 4.0, 6.0, 6.0),),
    )

    assert any(lo <= 0.0 <= hi for lo, hi in free)
    assert not any(lo <= 10.0 <= hi for lo, hi in free)


def test_guarded_band_rejects_a_retained_label_in_long_balloon_text():
    from draftwright.annotations.balloons import (
        _balloon_text_box,
        _guarded_free_segments,
    )

    tag = "LONG_BALLOON_TAG"
    font_size = 3.0
    text_box = _balloon_text_box(tag, font_size)
    assert text_box is not None
    radius = 4.5
    line = 20.0
    label_box = (
        line + radius + 0.2,
        text_box[1] + 0.2,
        line + text_box[2] - 0.2,
        text_box[3] - 0.2,
    )
    member = (tag, 0, SimpleNamespace(diameter=2.0), 0.0, 0.0)

    assert (
        _guarded_free_segments(
            member,
            "y",
            line,
            ((0.0, 0.0),),
            radius,
            1.0,
            (label_box,),
            text_box,
        )
        == ()
    )


def test_guarded_private_band_places_joint_solution_and_fails_closed(monkeypatch):
    import draftwright.annotations.balloons as balloons_module

    members = [
        ("A", 0, SimpleNamespace(diameter=2.0), 0.0, 0.0),
        ("B", 0, SimpleNamespace(diameter=2.0), 0.0, 5.0),
    ]
    rendered = []
    monkeypatch.setattr(
        balloons_module,
        "balloon_annotation_label_boxes",
        lambda *_args: (),
    )
    monkeypatch.setattr(
        balloons_module,
        "_render_balloon",
        lambda *args: rendered.append(args),
    )

    dropped = balloons_module._place_band(
        SimpleNamespace(scale=1.0),
        "plan",
        members,
        "y",
        20.0,
        0.0,
        10.0,
        5.0,
        3.0,
        1.0,
        object(),
        avoid_annotation_labels=True,
    )

    assert dropped == 0
    assert len(rendered) == 2

    monkeypatch.setattr(
        balloons_module,
        "_guarded_free_segments",
        lambda *_args: (),
    )
    assert (
        balloons_module._place_band(
            SimpleNamespace(scale=1.0),
            "plan",
            members,
            "y",
            20.0,
            0.0,
            10.0,
            5.0,
            3.0,
            1.0,
            object(),
            avoid_annotation_labels=True,
        )
        == 2
    )


def test_guarded_private_band_separates_sibling_long_tag_text(monkeypatch):
    import draftwright.annotations.balloons as balloons_module
    from draftwright._geometry import _boxes_overlap

    tags = ("LONG_BALLOON_TAG_0", "LONG_BALLOON_TAG_1")
    members = [
        (tag, 0, SimpleNamespace(diameter=2.0), natural, 0.0)
        for tag, natural in zip(tags, (0.0, 13.0), strict=True)
    ]
    placed = []
    monkeypatch.setattr(balloons_module, "balloon_annotation_label_boxes", lambda *_args: ())
    monkeypatch.setattr(
        balloons_module,
        "_render_balloon",
        lambda _dwg, _view, tag, _j, _hole, _cx, _cy, bx, by, *_rest: placed.append((tag, bx, by)),
    )

    dropped = balloons_module._place_band(
        SimpleNamespace(scale=1.0),
        "plan",
        members,
        "x",
        20.0,
        -50.0,
        50.0,
        13.0,
        3.0,
        4.5,
        object(),
        avoid_annotation_labels=True,
    )

    assert dropped == 0
    assert len(placed) == 2
    boxes = []
    for tag, bx, by in placed:
        text_box = balloons_module._balloon_text_box(tag, 3.0)
        assert text_box is not None
        boxes.append(tuple(value + offset for value, offset in zip(text_box, (bx, by, bx, by))))
    assert not _boxes_overlap(*boxes)


def test_final_guarded_inventory_validation_covers_cross_band_and_page_geometry():
    from draftwright.annotations.balloons import _guarded_inventory_geometry_is_clear

    page = (0.0, 0.0, 100.0, 100.0)
    first = ((((10.0, 10.0, 20.0, 20.0),), ()),)
    clear_second = (((30.0, 30.0, 40.0, 40.0),), ())
    overlapping_second = (((19.0, 15.0, 29.0, 25.0),), ())
    shaft_crossing_second = (((24.0, 12.0, 28.0, 18.0),), ())
    shaft_crossing_first = ((((10.0, 10.0, 20.0, 20.0),), (((0.0, 15.0), (30.0, 15.0)),)),)
    self_text_crossing = (
        (
            (10.0, 10.0, 20.0, 20.0),
            (20.5, 13.0, 30.0, 17.0),
        ),
        (((15.0, 15.0), (25.0, 15.0)),),
    )
    off_page = (((95.0, 95.0, 105.0, 105.0),), ())

    assert _guarded_inventory_geometry_is_clear((*first, clear_second), page)
    assert not _guarded_inventory_geometry_is_clear((*first, overlapping_second), page)
    assert not _guarded_inventory_geometry_is_clear(
        (*shaft_crossing_first, shaft_crossing_second), page
    )
    assert not _guarded_inventory_geometry_is_clear((self_text_crossing,), page)
    assert not _guarded_inventory_geometry_is_clear((*first, off_page), page)
    assert not _guarded_inventory_geometry_is_clear(
        first,
        page,
        retained_label_boxes=((15.0, 15.0, 25.0, 25.0),),
    )
    assert not _guarded_inventory_geometry_is_clear(
        first,
        page,
        retained_leader_segments=(((0.0, 15.0), (30.0, 15.0)),),
    )
    shaft_only = ((((40.0, 40.0, 50.0, 50.0),), (((0.0, 15.0), (30.0, 15.0)),)),)
    assert not _guarded_inventory_geometry_is_clear(
        shaft_only,
        page,
        retained_leader_segments=(((15.0, 0.0), (15.0, 30.0)),),
    )


def test_retained_leader_segment_intersection_ignores_only_a_shared_endpoint():
    from draftwright._geometry import _segments_cross_or_overlap

    assert _segments_cross_or_overlap((0.0, 0.0), (2.0, 2.0), (0.0, 2.0), (2.0, 0.0))
    assert _segments_cross_or_overlap((0.0, 0.0), (3.0, 0.0), (1.0, 0.0), (4.0, 0.0))
    assert _segments_cross_or_overlap((0.0, 0.0), (3.0, 0.0), (1.0, -1.0), (1.0, 0.0))
    assert not _segments_cross_or_overlap((0.0, 0.0), (1.0, 0.0), (1.0, 0.0), (2.0, 1.0))
    assert not _segments_cross_or_overlap((0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0))


def test_automatic_transaction_fails_closed_when_final_inventory_gate_rejects(monkeypatch):
    import draftwright.annotations.balloons as balloons_module

    monkeypatch.setattr(
        balloons_module,
        "_guarded_inventory_geometry_is_clear",
        lambda *_args, **_kwargs: False,
    )

    drawing = build_drawing(_dense_scattered_plate())

    assert "hole_table_plan" not in drawing.annotations()
    assert not [name for name in drawing.annotations() if name.startswith("balloon_plan_")]
    assert len(_plan_bore_callouts(drawing)) == 17
    assert {"balloon_dropped", "table_dropped"} <= {
        issue.code for issue in drawing.registry.issues
    }
    assert ("feature_annotation", "required_balloon_not_placed") in {
        (outcome.representation, outcome.representation_reason)
        for outcome in _outcomes(drawing)
        if outcome.state == "placed"
    }
    assert "view_annotation_overlap" not in {issue.code for issue in drawing.lint()}


def test_automatic_actual_label_guard_restores_features_with_no_safe_balloon(monkeypatch):
    import draftwright.annotations.balloons as balloons_module

    # A real retained-label box occupying every reserved band makes every
    # attempted ring/shaft infeasible. The automatic transaction must retain
    # the feature-backed leaders instead of silently accepting table coverage.
    monkeypatch.setattr(
        balloons_module,
        "balloon_annotation_label_boxes",
        lambda *_args: ((-1000.0, -1000.0, 1000.0, 1000.0),),
    )

    drawing = build_drawing(_dense_scattered_plate())

    assert not [name for name in drawing.annotations() if name.startswith("balloon_plan_")]
    assert len(_plan_bore_callouts(drawing)) == 17
    assert "balloon_dropped" in {issue.code for issue in drawing.registry.issues}
    assert ("feature_annotation", "required_balloon_not_placed") in {
        (outcome.representation, outcome.representation_reason)
        for outcome in _outcomes(drawing)
        if outcome.state == "placed"
    }
    assert "hole_requirement_missing" not in {issue.code for issue in drawing.lint()}


def test_public_hole_table_remains_additive_and_keeps_every_balloon():
    drawing = build_drawing(_multi_hole_plate(), page="A4")
    original = _plan_bore_callouts(drawing)

    table = drawing.add_hole_table("plan")

    assert table is not None
    assert all(drawing.get_annotation(name) is item for name, item in original.values())
    assert len([name for name in drawing.annotations() if name.startswith("balloon_plan_")]) == 3
    assert not [issue for issue in drawing.registry.issues if issue.code == "balloon_dropped"]
    assert {
        (outcome.representation, outcome.representation_reason)
        for outcome in _outcomes(drawing)
        if outcome.parameter_id == "bore.diameter"
    } == {(None, None)}


def test_public_add_balloons_retains_its_existing_cardinality_contract():
    drawing = build_drawing(_multi_hole_plate(), page="A4", auto_dims=False)
    holes = [
        SimpleNamespace(location=point, diameter=feature.diameter)
        for feature in drawing.model().features
        if feature.kind == "hole"
        for point in (feature.members or (feature.frame.origin,))
    ]

    drawing.add_balloons("plan", [(f"T{index}", 0, hole) for index, hole in enumerate(holes)])

    assert {name for name in drawing.annotations() if name.startswith("balloon_plan_T")} == {
        f"balloon_plan_T{index}_0" for index in range(3)
    }
    assert not [issue for issue in drawing.registry.issues if issue.code == "balloon_dropped"]


def test_public_add_balloons_separates_long_sibling_text_components():
    from draftwright._geometry import _boxes_overlap

    drawing = build_drawing(_multi_hole_plate(), page="A4", auto_dims=False)
    holes = drawing.recognition().holes
    drawing.add_balloons(
        "plan",
        [(f"LONG_BALLOON_TAG_{index}", 0, holes[index % len(holes)]) for index in range(4)],
    )
    balloons = [
        drawing.get_annotation(name)
        for name in drawing.annotations()
        if name.startswith("balloon_plan_LONG_BALLOON_TAG_")
    ]

    assert len(balloons) == 4
    assert not any(
        _boxes_overlap(first_box, second_box)
        for index, first in enumerate(balloons)
        for second in balloons[index + 1 :]
        for first_box in first.centerline_boxes
        for second_box in second.centerline_boxes
    )


def test_public_hole_table_does_not_expose_an_uncomposable_replacement_option():
    from draftwright.drawing import Drawing

    assert "replace_callouts" not in inspect.signature(Drawing.add_hole_table).parameters


def test_automatic_initial_table_failure_restores_every_fallback(monkeypatch):
    import draftwright.annotations.orchestrator as orchestrator
    import draftwright.drawing as drawing_module

    part = _dense_scattered_plate()
    with monkeypatch.context() as disabled:
        disabled.setattr(orchestrator, "_maybe_tabulate_holes", lambda *_args, **_kwargs: None)
        baseline = build_drawing(part)
    baseline_callouts = {
        name: (item, baseline.registry.identity_of(name))
        for name, item in baseline.iter_annotations()
        if name.startswith("hc_plan")
    }
    monkeypatch.setattr(drawing_module, "fit_box", lambda *_args, **_kwargs: None)

    drawing = build_drawing(part)

    assert "hole_table_plan" not in drawing.annotations()
    assert tuple(drawing.annotations()) == tuple(baseline.annotations())
    assert drawing.coverage.snapshot() == baseline.coverage.snapshot()
    assert {
        name: drawing.registry.identity_of(name)
        for name in drawing.annotations()
        if name.startswith("hc_plan")
    } == {name: identity for name, (_item, identity) in baseline_callouts.items()}
    codes = [issue.code for issue in drawing.lint()]
    assert codes.count("table_dropped") == 1
    assert "hole_requirement_missing" not in codes
    assert ("feature_annotation", "table_not_placed") in {
        (outcome.representation, outcome.representation_reason)
        for outcome in _outcomes(drawing)
        if outcome.state == "placed"
    }


def test_automatic_partial_balloon_result_rolls_back_the_shared_table(monkeypatch):
    _drop_one_diameter_balloon(monkeypatch, 2.0)

    drawing = build_drawing(_dense_scattered_plate())

    assert "hole_table_plan" not in drawing.annotations()
    assert not [name for name in drawing.annotations() if name.startswith("balloon_plan_")]
    assert len(_plan_bore_callouts(drawing)) == 17
    codes = {issue.code for issue in drawing.lint()}
    assert {"balloon_dropped", "table_dropped"} <= codes
    assert "view_annotation_overlap" not in codes
    assert "hole_requirement_missing" not in codes
    assert {outcome.state for outcome in _outcomes(drawing)} <= {"placed", "dropped"}
    assert ("feature_annotation", "required_balloon_not_placed") in {
        (outcome.representation, outcome.representation_reason)
        for outcome in _outcomes(drawing)
        if outcome.state == "placed"
    }


def test_scattered_table_reports_a_balloon_landed_on_the_wrong_semantic_owner():
    part = _dense_perimeter_plate()
    detected = build_drawing(part, auto_dims=False).model()
    victim = max(
        (feature for feature in detected.features if feature.kind == "hole"),
        key=lambda feature: feature.diameter,
    )
    # The sanctioned position bridge resolves the last feature at a coincident
    # centre.  A distinct appended declaration therefore owns both rendered
    # balloons even though the original feature still has its own visible row.
    wrong_owner = replace(victim, diameter=victim.diameter + 0.123)
    declared = replace(detected, features=[*detected.features, wrong_owner])

    drawing = build_drawing(part, model=declared, page="A3")

    assert "hole_table_plan" not in drawing.annotations()
    assert victim in _plan_bore_callouts(drawing)
    failures = [issue for issue in drawing.registry.issues if issue.code == "balloon_dropped"]
    assert len(failures) == 1
    assert any(
        issue.code == "table_dropped" and "feature-owned balloon" in issue.message
        for issue in drawing.registry.issues
    )
    assert {
        outcome.representation
        for outcome in _outcomes(drawing)
        if outcome.source_at[:2] == tuple(victim.frame.origin[:2])
        and outcome.parameter_id == "bore.diameter"
    } != {"hole_table"}


def test_successful_auto_table_preserves_an_unrelated_public_table_drop():
    drawing = build_drawing(_dense_perimeter_plate(), auto_dims=False, page="A3")
    assert (
        drawing.add_table(
            [("X" * 50,) for _ in range(100)],
            name="user_oversize_table",
        )
        is None
    )
    user_issue = next(
        issue
        for issue in drawing.registry.issues
        if issue.code == "table_dropped" and "user_oversize_table" in issue.message
    )

    with drawing.deferred():
        for feature in drawing.model().features:
            if feature.kind == "hole":
                drawing.callout(feature)
                drawing.locate(feature)

    assert "hole_table_plan" in drawing.annotations()
    assert len([name for name in drawing.annotations() if name.startswith("balloon_plan_")]) == 16
    assert user_issue in drawing.registry.issues


def test_grouped_pattern_marker_cannot_certify_an_undefined_hole_tag():
    from draftwright.analysis import _analyse
    from draftwright.annotations._common import Escalation, PlacementContext
    from draftwright.annotations.orchestrator import _maybe_tabulate_holes
    from draftwright.linting.issues import LintIssue

    part = _bolt_circle_plate()
    drawing = build_drawing(part, page="A4")
    analysis = _analyse(
        part,
        title="",
        number="",
        tolerance="ISO 2768-m",
        drawn_by="",
        out="",
        page="A4",
        model=drawing.model(),
    )
    pattern = next(feature for feature in drawing.model().features if feature.kind == "pattern")
    callout_name = next(name for name in drawing.annotations() if name.startswith("hc_plan"))
    measurements = drawing.registry.measurement_of(callout_name)
    drawing.remove(callout_name)
    issue = LintIssue(
        severity="warning",
        code="callout_dropped",
        message="synthetic grouped pattern callout drop",
        measurement_ids=measurements,
        hole_requirement_ids=(
            (pattern, "bore.through"),
            (pattern, "grouping.count"),
        ),
    )
    drawing.registry.record_issue(issue)
    ctx = PlacementContext(
        registry=drawing.registry,
        coverage=drawing.coverage,
        items=drawing.items,
        part_model=drawing.model(),
        escalations=(
            Escalation(
                kind="callout",
                view="plan",
                feature=pattern,
                reason="strip_full",
                targets=measurements,
            ),
        ),
    )

    _maybe_tabulate_holes(drawing, analysis, ctx=ctx)

    balloon_name = "balloon_plan_6×A_0"
    assert balloon_name in drawing.annotations()
    assert drawing.measurement_keys(balloon_name) == []
    assert issue in drawing.registry.issues
    dropped = {
        outcome.parameter_id
        for outcome in _outcomes(drawing)
        if outcome.source_kind == "hole_pattern" and outcome.state == "dropped"
    }
    assert dropped == {
        "bore.diameter",
        "bore.through",
        "bolt_circle.diameter",
        "grouping.count",
    }


def test_long_grouped_pattern_marker_fails_closed_when_its_shaft_crosses_its_text():
    from draftwright.analysis import _analyse
    from draftwright.annotations._common import Escalation, PlacementContext
    from draftwright.annotations.orchestrator import _maybe_tabulate_holes
    from draftwright.linting.issues import LintIssue

    part = _bolt_circle_plate()
    detected = build_drawing(part, page="A4")
    pattern = next(feature for feature in detected.model().features if feature.kind == "pattern")
    long_pattern = replace(pattern, count=1000)
    model = replace(
        detected.model(),
        features=[
            long_pattern if feature is pattern else feature
            for feature in detected.model().features
        ],
    )
    drawing = build_drawing(part, model=model, page="A4")
    analysis = _analyse(
        part,
        title="",
        number="",
        tolerance="ISO 2768-m",
        drawn_by="",
        out="",
        page="A4",
        model=model,
    )
    callout_name = next(name for name in drawing.annotations() if name.startswith("hc_plan"))
    measurements = drawing.registry.measurement_of(callout_name)
    drawing.remove(callout_name)
    issue = LintIssue(
        severity="warning",
        code="callout_dropped",
        message="synthetic long grouped pattern callout drop",
        measurement_ids=measurements,
        hole_requirement_ids=((long_pattern, "grouping.count"),),
    )
    drawing.registry.record_issue(issue)
    ctx = PlacementContext(
        registry=drawing.registry,
        coverage=drawing.coverage,
        items=drawing.items,
        part_model=model,
        escalations=(
            Escalation(
                kind="callout",
                view="plan",
                feature=long_pattern,
                reason="strip_full",
                targets=measurements,
            ),
        ),
    )

    _maybe_tabulate_holes(drawing, analysis, ctx=ctx)

    assert "balloon_plan_1000×A_0" not in drawing.annotations()
    assert issue in drawing.registry.issues
    assert "balloon_dropped" in {record.code for record in drawing.registry.issues}


def test_fitted_callout_can_remain_while_table_resolves_its_location_drop(monkeypatch):
    import draftwright.annotations.orchestrator as orchestrator

    part = _dense_plate_with_close_x_ordinates()
    with monkeypatch.context() as disabled:
        disabled.setattr(orchestrator, "_maybe_tabulate_holes", lambda *_args, **_kwargs: None)
        baseline = build_drawing(part, page="A2")
    placed_callout_features = set(_plan_bore_callouts(baseline))
    location_issue = next(
        issue
        for issue in baseline.registry.issues
        if issue.code == "location_ref_dropped"
        and _features_from_issue(issue) & placed_callout_features
    )
    feature = next(iter(_features_from_issue(location_issue) & placed_callout_features))
    dropped_parameters = {
        parameter for owner, parameter in location_issue.hole_requirement_ids if owner is feature
    }
    assert dropped_parameters
    declared = replace(
        baseline.model(),
        decorations={(feature, "diameter"): fit_class("H7", feature.diameter)},
    )

    drawing = build_drawing(part, page="A2", model=declared)

    table = drawing.get_annotation("hole_table_plan")
    assert table is not None
    callout_name, callout = _plan_bore_callouts(drawing)[feature]
    assert "H7" in callout.label
    assert drawing.get_annotation(callout_name) is callout
    assert any(fact[0] == feature for fact in getattr(table, "covers_hole_locations", ()))
    assert not any(
        issue.code == "location_ref_dropped" and feature in _features_from_issue(issue)
        for issue in drawing.registry.issues
    )
    outcomes = [
        outcome
        for outcome in _outcomes(drawing)
        if outcome.source_at[:2] == tuple(feature.frame.origin[:2])
    ]
    assert {
        outcome.representation
        for outcome in outcomes
        if outcome.parameter_id in dropped_parameters
    } == {"hole_table"}
    assert {
        outcome.representation for outcome in outcomes if outcome.parameter_id == "bore.diameter"
    } == {None}


@pytest.mark.parametrize(
    "decoration",
    [0.1, fit_class("H7", 5.2)],
    ids=["tolerance", "fit"],
)
def test_automatic_table_cannot_resolve_a_dropped_decorated_callout(monkeypatch, decoration):
    import draftwright.annotations.orchestrator as orchestrator

    part = _dense_scattered_plate()
    with monkeypatch.context() as disabled:
        disabled.setattr(orchestrator, "_maybe_tabulate_holes", lambda *_args, **_kwargs: None)
        baseline = build_drawing(part)
    dropped_issue = next(
        issue
        for issue in baseline.registry.issues
        if issue.code == "callout_dropped" and issue.measurement_ids
    )
    feature = dropped_issue.measurement_ids[0].feature
    declared = replace(
        baseline.model(),
        decorations={(feature, "diameter"): decoration},
    )

    drawing = build_drawing(part, model=declared)

    assert "hole_table_plan" not in drawing.annotations()
    assert any(issue.code == "table_dropped" for issue in drawing.registry.issues)
    assert any(
        issue.code == "callout_dropped" and feature in _features_from_issue(issue)
        for issue in drawing.registry.issues
    )
    assert {
        outcome.representation
        for outcome in _outcomes(drawing)
        if outcome.source_at[:2] == tuple(feature.frame.origin[:2])
        and outcome.parameter_id == "bore.diameter"
    } == {None}


def test_automatic_table_cannot_resolve_a_dropped_thread_callout(monkeypatch):
    import draftwright.annotations.orchestrator as orchestrator

    part = _dense_scattered_plate()
    with monkeypatch.context() as disabled:
        disabled.setattr(orchestrator, "_maybe_tabulate_holes", lambda *_args, **_kwargs: None)
        baseline = build_drawing(part)
    dropped_issue = next(
        issue
        for issue in baseline.registry.issues
        if issue.code == "callout_dropped" and issue.measurement_ids
    )
    original = dropped_issue.measurement_ids[0].feature
    threaded = replace(original, thread="M5x0.8")
    declared = replace(
        baseline.model(),
        features=[
            threaded if feature is original else feature for feature in baseline.model().features
        ],
    )

    drawing = build_drawing(part, model=declared)

    assert "hole_table_plan" not in drawing.annotations()
    assert any(issue.code == "table_dropped" for issue in drawing.registry.issues)
    assert any(
        issue.code == "callout_dropped" and threaded in _features_from_issue(issue)
        for issue in drawing.registry.issues
    )
    assert {
        outcome.representation
        for outcome in _outcomes(drawing)
        if outcome.source_at[:2] == tuple(threaded.frame.origin[:2])
        and outcome.parameter_id == "bore.diameter"
    } == {None}


def test_automatic_compound_inventory_fails_closed_on_cross_balloon_geometry():
    drawing = build_drawing(_dense_plate_with_counterbore(), page="A3")
    feature = next(
        feature
        for feature in drawing.model().features
        if feature.kind == "hole" and feature.cbore is not None
    )

    assert "hole_table_plan" not in drawing.annotations()
    assert feature in _plan_bore_callouts(drawing)
    assert not [name for name in drawing.annotations() if name.startswith("balloon_plan_")]
    assert [issue for issue in drawing.registry.issues if issue.code == "balloon_dropped"]
    states = {outcome.parameter_id: outcome.state for outcome in _outcomes(drawing)}
    assert states["counterbore.diameter"] == "placed"
    assert states["counterbore.depth"] == "placed"
    compound_outcomes = [
        outcome
        for outcome in _outcomes(drawing)
        if outcome.source_at[:2] == tuple(feature.frame.origin[:2])
    ]
    assert {
        (outcome.representation, outcome.representation_reason) for outcome in compound_outcomes
    } == {(None, None)}
    assert ("feature_annotation", "required_balloon_not_placed") in {
        (outcome.representation, outcome.representation_reason)
        for outcome in _outcomes(drawing)
        if outcome.state == "placed"
    }
    visible_labels = {
        annotation.label
        for _owner, (_name, annotation) in _plan_bore_callouts(drawing).items()
        if annotation.label
    }
    assert not [
        issue
        for issue in drawing.lint()
        if issue.code in {"label_centerline_overlap", "hole_requirement_missing"}
        and (
            issue.code == "hole_requirement_missing"
            or any(f"label '{label}'" in issue.message for label in visible_labels)
        )
    ]


def test_automatic_table_keeps_profiled_callout():
    drawing = build_drawing(_dense_plate_with_double_d())
    feature = next(
        feature for feature in drawing.model().features if feature.profile == "double_d"
    )
    callout_name, callout = _plan_bore_callouts(drawing)[feature]

    assert "hole_table_plan" in drawing.annotations()
    assert drawing.get_annotation(callout_name) is callout
    assert "DOUBLE-D 3.6 A/F" in callout.label
    table = drawing.get_annotation("hole_table_plan")
    assert feature not in {
        owner
        for owner, _parameter, _representation, _reason in getattr(
            table, "covers_hole_representations_by_requirement", ()
        )
    }


@pytest.mark.parametrize("reserved_name", ["hole_table_plan", "balloon_plan_A_0"])
def test_automatic_escalation_preserves_preexisting_reserved_names(reserved_name):
    drawing = build_drawing(_dense_scattered_plate(), auto_dims=False)
    drawing.note("USER KEEP", (15, 15), view="plan", name=reserved_name)
    user_annotation = drawing.get_annotation(reserved_name)

    with drawing.deferred():
        for feature in drawing.model().features:
            if feature.kind == "hole":
                drawing.callout(feature)
                drawing.locate(feature)

    assert drawing.get_annotation(reserved_name) is user_annotation
    if reserved_name != "hole_table_plan":
        assert "hole_table_plan" not in drawing.annotations()
    assert any(issue.code == "table_dropped" for issue in drawing.registry.issues)


def test_finalize_exception_restores_automatic_transaction_markers(monkeypatch):
    import draftwright.annotations.orchestrator as orchestrator

    part = _dense_perimeter_plate()
    with monkeypatch.context() as disabled:
        disabled.setattr(orchestrator, "_maybe_tabulate_holes", lambda *_args, **_kwargs: None)
        drawing = build_drawing(part)
    original_callouts = _plan_bore_callouts(drawing)
    marked_name, marked_annotation = next(iter(original_callouts.values()))
    marked_annotation.hole_representation = "preexisting_representation"
    marked_annotation.hole_representation_reason = "preexisting_reason"
    before = _transaction_state(drawing)
    original_reapply = drawing.registry.reapply

    def fail_after_coverage(name, identity):
        original_reapply(name, identity)
        if name == "hole_table_plan":
            raise RuntimeError("forced automatic coverage failure")

    monkeypatch.setattr(drawing.registry, "reapply", fail_after_coverage)

    with pytest.raises(RuntimeError, match="forced automatic coverage failure"):
        with drawing.deferred():
            for feature in drawing.model().features:
                if feature.kind == "hole":
                    drawing.callout(feature)
                    drawing.locate(feature)

    assert _transaction_state(drawing) == before
    assert all(
        drawing.get_annotation(name) is annotation
        for name, annotation in original_callouts.values()
    )
    assert drawing.get_annotation(marked_name) is marked_annotation
    assert (
        marked_annotation.hole_representation,
        marked_annotation.hole_representation_reason,
    ) == ("preexisting_representation", "preexisting_reason")


def test_one_physical_group_with_mixed_table_and_fit_evidence_has_no_scalar_winner():
    part = _dense_plate_with_one_group()
    detected = build_drawing(part, auto_dims=False).model()
    grouped = next(
        feature for feature in detected.features if feature.kind == "hole" and feature.count == 2
    )
    split = tuple(
        replace(grouped, frame=replace(grouped.frame, origin=point), count=1, members=(point,))
        for point in grouped.members
    )
    fitted, plain = split
    declared = replace(
        detected,
        features=[
            *split,
            *(feature for feature in detected.features if feature is not grouped),
        ],
        decorations={(fitted, "diameter"): fit_class("H7", fitted.diameter)},
    )

    drawing = build_drawing(part, model=declared)

    assert "hole_table_plan" in drawing.annotations()
    assert fitted in _plan_bore_callouts(drawing)
    assert plain not in _plan_bore_callouts(drawing)
    grouped_outcomes = [
        outcome
        for outcome in _outcomes(drawing)
        if outcome.member_count == 2
        and outcome.parameter_id in {"bore.diameter", "bore.through", "grouping.count"}
    ]
    assert grouped_outcomes
    assert {outcome.state for outcome in grouped_outcomes} == {"placed"}
    assert {
        (outcome.representation, outcome.representation_reason) for outcome in grouped_outcomes
    } == {(None, None)}


def test_retained_fit_leader_crossing_rolls_the_automatic_table_back():
    part = _dense_plate_with_close_x_ordinates()
    detected = build_drawing(part, page="A3", auto_dims=False).model()
    fitted = next(
        feature
        for feature in detected.features
        if feature.kind == "hole"
        and feature.frame.origin == (-54.0, -18.0, 6.0)
        and feature.diameter == pytest.approx(4.4)
    )
    declared = replace(
        detected,
        decorations={(fitted, "diameter"): fit_class("H7", fitted.diameter)},
    )

    drawing = build_drawing(part, model=declared, page="A3")

    assert "hole_table_plan" not in drawing.annotations()
    assert not [name for name in drawing.annotations() if name.startswith("balloon_plan_")]
    assert fitted in _plan_bore_callouts(drawing)
    assert {"table_dropped", "balloon_dropped"} <= {
        issue.code for issue in drawing.registry.issues
    }
    assert "hole_requirement_missing" not in {issue.code for issue in drawing.lint()}
