"""An explicit rear view preserves physical handedness without becoming automatic."""

import pytest
from build123d import Box, Cylinder, GeomType, Pos, Rot, Vector

from draftwright.view_plan import (
    principal_specs,
    third_angle_principals,
    third_angle_view_names,
)


@pytest.fixture(scope="module")
def rear_enclosure():
    stock = Box(80, 40, 50)
    part = stock - Pos(0, -2, 0) * Box(74, 40, 44)
    for radius, x, z in ((2, -21, 11), (3.5, 14, -9)):
        part -= Pos(x, 19.5, z) * Rot(90, 0, 0) * Cylinder(radius, 2)
        # The 1.5 mm blind bore leaves 0.5 mm of the back wall intact.
        assert part.is_inside(Vector(x, 18.25, z))
        assert not part.is_inside(Vector(x, 19.5, z))
    assert part.is_valid and len(part.solids()) == 1
    assert 0 < part.volume < stock.volume
    return part


def test_fixture_rear_targets_are_visible_only_from_rear(rear_enclosure):
    visible_front, _ = rear_enclosure.project_to_viewport((0, -1000, 0), look_at=(0, 0, 0))
    visible_rear, _ = rear_enclosure.project_to_viewport((0, 1000, 0), look_at=(0, 0, 0))
    assert not [edge for edge in visible_front if edge.geom_type == GeomType.CIRCLE]
    circles = sorted(
        (round(edge.radius, 5), tuple(round(v, 5) for v in edge.arc_center))
        for edge in visible_rear
        if edge.geom_type == GeomType.CIRCLE
    )
    assert circles == [(2.0, (21.0, 11.0, 0.0)), (3.5, (-14.0, -9.0, 0.0))]


def test_supported_rear_does_not_expand_automatic_candidate_roster():
    # Extending the supported vocabulary must not let a coverage retry invent rear.
    (rear,) = principal_specs(("rear",))
    assert rear.kind == "principal" and rear.page_axes == ("x", "z")
    assert third_angle_view_names() == ("front", "plan", "side")
    assert tuple(spec.name for spec in third_angle_principals()) == ("front", "plan", "side")


@pytest.mark.parametrize("convention", ["first", "third"])
@pytest.mark.parametrize("view_names", [("rear",), ("front", "side", "rear")])
def test_explicit_rear_geometry_has_physical_handedness(rear_enclosure, convention, view_names):
    from draftwright import Sheet

    sheet = Sheet(rear_enclosure, projection=convention, page="A3", scale=1).authored_dimensions()
    for name in view_names:
        sheet.view(name)
    drawing = sheet.build()
    assert set(drawing.view_plan.principal_names) == set(view_names)
    assert drawing.scale == 1
    origin = drawing.at("rear", 0, 0, 0)
    left_hole = drawing.at("rear", -21, 20, 11)
    right_hole = drawing.at("rear", 14, 20, -9)
    assert left_hole[0] - origin[0] == pytest.approx(21)
    assert right_hole[0] - origin[0] == pytest.approx(-14)
    assert left_hole[1] - origin[1] == pytest.approx(11)
    assert right_hole[1] - origin[1] == pytest.approx(-9)
    assert drawing.views["rear"][0].edges()
    x0, y0, x1, y1 = drawing.view_bounds("rear")
    assert 0 < x0 < x1 < drawing.page_w
    assert 0 < y0 < y1 < drawing.page_h
    if "side" in view_names:
        side = drawing.view_bounds("side")
        assert x1 < side[0] if convention == "first" else x0 > side[2]


def _rear_hole_sheet(part, convention):
    from draftwright import Sheet

    sheet = Sheet(part, projection=convention, page="A3", scale=1, scale_policy="strict")
    for diameter, x, z in ((4, -21, 11), (7, 14, -9)):
        hole = sheet.hole(diameter=diameter, at=(x, 20, z), axis="y", through=False, depth=1.5)
        sheet.dimension(hole, "bore.diameter")
        sheet.dimension(hole, "bore.depth")
        sheet.dimension(hole, "location")
    sheet.view("rear")
    return sheet


@pytest.mark.parametrize("convention", ["first", "third"])
def test_rear_carries_bore_sizes_depths_and_both_location_axes(rear_enclosure, convention):
    from collections import Counter

    drawing = _rear_hole_sheet(rear_enclosure, convention).build()
    labels = Counter(
        getattr(annotation, "label", "")
        for name, annotation in drawing.iter_annotations()
        if drawing.view_of(name) == "rear" and getattr(annotation, "label", "")
    )
    assert labels == Counter(["⌀4 ↧ 1.5", "⌀7 ↧ 1.5", "19", "54", "16", "36"])
    assert len(drawing.model().features) == 2
    for feature in drawing.model().features:
        claims = drawing.annotations_of(feature)
        assert claims
        assert all(drawing.view_of(name) == "rear" for name in claims)
    # The shell's undeclared plates/cavity remain honestly outside this bounded
    # hole fixture; no hole, physical-target or placement warning is accepted.
    background = {
        "unrecognised_defining_geometry",
        "plate_requirement_unverifiable",
        "pocket_requirement_unverifiable",
    }
    assert not [issue for issue in drawing.lint() if issue.code not in background]


def test_rear_target_validation_rejects_another_holes_visible_rim(rear_enclosure, monkeypatch):
    drawing = _rear_hole_sheet(rear_enclosure, "first").build()
    (hole,) = [feature for feature in drawing.model().features if feature.diameter == 4]
    (name,) = [
        name
        for name in drawing.annotations_of(hole)
        if hasattr(drawing.get_annotation(name), "tip")
    ]
    leader = drawing.get_annotation(name)
    target = drawing.at("rear", 17.5, 20, -9)  # Physical rim of the other, 7 mm hole.
    monkeypatch.setattr(
        leader,
        "location",
        Pos(target[0] - leader.tip[0], target[1] - leader.tip[1], 0) * leader.location,
    )
    errors = [issue for issue in drawing.lint() if issue.code == "diameter_leader_target_mismatch"]
    assert len(errors) == 1 and name in errors[0].message
    assert errors[0].measurement_ids == drawing.registry.measurement_of(name)


@pytest.mark.parametrize("convention", ["first", "third"])
def test_added_rear_retains_measurements_through_script_and_export(
    rear_enclosure, convention, tmp_path
):
    from draftwright import Sheet
    from draftwright.audit import compare_measurements
    from draftwright.sheet_emit import emit_sheet_script

    sheet = Sheet(rear_enclosure, projection=convention, page="A2", scale=1, scale_policy="strict")
    for diameter, x, z in ((4, -21, 11), (7, 14, -9)):
        hole = sheet.hole(diameter=diameter, at=(x, 20, z), axis="y", through=False, depth=1.5)
        sheet.dimension(hole, "bore.diameter", view="rear")
        sheet.dimension(hole, "bore.depth", view="rear")
        sheet.dimension(hole, "location")
    sheet.auto_views().add_view("rear")
    original = sheet.build()
    assert set(original.view_plan.principal_names) == {"front", "plan", "side", "rear"}
    source = emit_sheet_script(
        sheet.model(),
        "part = supplied_part",
        str(tmp_path / "rear"),
        title="REAR",
        number="REAR",
        page="A2",
        scale=1,
        scale_policy="strict",
        projection=convention,
        formats=("svg", "pdf", "dxf"),
        view_constraints=sheet.view_constraints,
    )
    assert 'sheet.add_view("rear")' in source
    namespace = {"supplied_part": rear_enclosure}
    exec(source, namespace)
    replay = namespace["drawing"]
    assert replay.view_plan.principal_names == original.view_plan.principal_names
    assert replay.view_plan.convention == convention
    pairs = tuple(zip(sheet.model().features, namespace["sheet"].model().features, strict=True))
    assert compare_measurements(original, replay, feature_pairs=pairs)["status"] == "preserved"
    for feature in namespace["sheet"].model().features:
        assert all(replay.view_of(name) == "rear" for name in replay.annotations_of(feature))
    assert all(
        (tmp_path / f"rear.{extension}").stat().st_size > 100
        for extension in ("svg", "pdf", "dxf")
    )


def test_rear_ink_overflow_triggers_shared_repack():
    from types import SimpleNamespace

    import draftwright.builder as builder

    ink = SimpleNamespace(
        bounding_box=lambda: SimpleNamespace(min=Vector(9, 20, 0), max=Vector(30, 40, 0))
    )
    drawing = SimpleNamespace(
        iter_annotations=lambda: [("rear_ink", ink)], view_of=lambda name: "rear"
    )
    analysis = SimpleNamespace(margin=10, PAGE_W=100, PAGE_H=100)
    assert builder._annotations_out_of_bounds(drawing, analysis)


def test_external_location_judge_does_not_invent_a_rear_view():
    from draftwright import build_drawing
    from draftwright.linting import lint_location_coverage

    part = Box(100, 60, 20) - Pos(30, 0, 0) * Rot(90, 0, 0) * Cylinder(4, 80)
    drawing = build_drawing(part, auto_dims=False)

    class ExternalDrawing:
        at = drawing.at
        iter_annotations = drawing.iter_annotations
        view_of = drawing.view_of

    expected = [issue.code for issue in lint_location_coverage(part, drawing)]
    assert expected == ["feature_no_centermark", "feature_not_located"]
    assert [issue.code for issue in lint_location_coverage(part, ExternalDrawing())] == expected


@pytest.mark.parametrize("with_bore", [False, True])
@pytest.mark.parametrize("convention", ["first", "third"])
def test_rear_pattern_pitch_never_draws_in_an_absent_front_view(with_bore, convention):
    from draftwright import Sheet
    from draftwright.model import hole

    part = Box(80, 40, 50)
    for x in (-20, 0, 20):
        part -= Pos(x, 19.5, 5) * Rot(90, 0, 0) * Cylinder(2, 2)
    sheet = Sheet(part, page="A3", scale=1, scale_policy="strict", projection=convention)
    pattern = sheet.pattern(
        hole(diameter=4, at=(-20, 20, 5), axis="y", through=False, depth=1.5),
        kind="linear",
        count=3,
        at=(0, 20, 5),
        members=((-20, 20, 5), (0, 20, 5), (20, 20, 5)),
        pitch=20,
        direction=(1, 0, 0),
    )
    sheet.dimension(pattern, "pitch.length")
    if with_bore:
        sheet.dimension(pattern, "bore.diameter")
        sheet.dimension(pattern, "bore.depth")
    sheet.view("rear")
    drawing = sheet.build()
    assert drawing.view_plan.principal_names == ("rear",)
    assert all(drawing.view_of(name) in (None, "rear") for name in drawing.annotations())
    pitches = [
        annotation
        for _, annotation in drawing.iter_annotations()
        if getattr(annotation, "label", "") == "2× 20"
    ]
    assert len(pitches) == 1
    assert pitches[0].measured_length == pytest.approx(40)
