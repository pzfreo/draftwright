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


@pytest.mark.parametrize("convention", ["first", "third"])
@pytest.mark.parametrize("explicit_dimensions", [False, True])
def test_rear_envelope_width_and_height_keep_spans_and_identity(convention, explicit_dimensions):
    from draftwright import Sheet

    part = Pos(13, 7, -8) * Box(80, 40, 50)
    sheet = Sheet(
        part, page="A3", scale=1, scale_policy="strict", projection=convention
    ).authored_dimensions()
    envelope = sheet.envelope()
    placement = {"view": "rear"} if explicit_dimensions else {}
    sheet.dimension(envelope, "width.length", **placement)
    sheet.dimension(envelope, "height.length", side="left", **placement)
    sheet.view("rear")
    if explicit_dimensions:
        sheet.view("front")
    drawing = sheet.build()
    expected = {"m_env_width": ("80", 80), "dim_height": ("50", 50)}
    for name, (label, length) in expected.items():
        annotation = drawing.get_annotation(name)
        assert drawing.view_of(name) == "rear"
        assert annotation.label == label
        assert annotation.measured_length == pytest.approx(length)
        assert drawing.registry.measurement_of(name)
    assert not drawing.lint()

    from draftwright.view_plan import ViewPlanIncomplete

    sheet.dimension(envelope, "depth.length")
    with pytest.raises(ViewPlanIncomplete, match="envelope.depth.length.*side"):
        sheet.build()


@pytest.mark.parametrize("convention", ["first", "third"])
def test_translated_rear_origin_pin_moves_the_complete_view(convention):
    from draftwright import Sheet

    part = Pos(13, 7, -8) * Box(80, 40, 50)

    def declared(pin=None):
        sheet = Sheet(
            part, page="A3", scale=1, scale_policy="strict", projection=convention
        ).authored_dimensions()
        envelope = sheet.envelope()
        sheet.dimension(envelope, "width.length")
        sheet.dimension(envelope, "height.length")
        view = sheet.view("rear")
        if pin is not None:
            view.pin(pin)
        return sheet

    baseline = declared().build()
    origin = baseline.at("rear", 0, 0, 0)
    target = (origin[0] + 10, origin[1] + 10)
    moved = declared(target).build()
    assert moved.at("rear", 0, 0, 0)[:2] == pytest.approx(target)
    for name in ("m_env_width", "dim_height"):
        before = baseline.get_annotation(name).bounding_box().center()
        after = moved.get_annotation(name).bounding_box().center()
        assert tuple(after - before) == pytest.approx((10, 10, 0))
    assert not moved.lint()
    with pytest.raises(ValueError, match="pin.*infeasible.*not relaxed"):
        declared((-1000, target[1])).build()


@pytest.mark.parametrize("convention", ["first", "third"])
def test_rear_only_visibility_pressure_never_adds_an_automatic_rear(rear_enclosure, convention):
    from draftwright import Sheet

    # Native HLR proves the two bore rims are absent from visible front geometry.
    visible, hidden = rear_enclosure.project_to_viewport((0, -1000, 0), look_at=(0, 0, 0))
    assert not any(edge.geom_type == GeomType.CIRCLE for edge in visible)
    assert any(edge.geom_type == GeomType.CIRCLE for edge in hidden)
    sheet = Sheet(
        rear_enclosure, projection=convention, page="A3", scale=1, scale_policy="permissive"
    )
    for diameter, x, z in ((4, -21, 11), (7, 14, -9)):
        hole = sheet.hole(diameter=diameter, at=(x, 20, z), axis="y", through=False, depth=1.5)
        sheet.dimension(hole, "bore.diameter")
        sheet.dimension(hole, "bore.depth")
        sheet.dimension(hole, "location")
    drawing = sheet.auto_views().build()
    assert "rear" not in drawing.views
    assert "rear" not in drawing.view_plan.principal_names
    # Requesting the same measurements in rear must fail when rear was not requested.
    sheet = Sheet(rear_enclosure, projection=convention)
    hole = sheet.hole(diameter=4, at=(-21, 20, 11), axis="y", through=False, depth=1.5)
    sheet.dimension(hole, "bore.diameter", view="rear")
    from draftwright.view_plan import ViewPlanIncomplete

    with pytest.raises(ViewPlanIncomplete, match="rear"):
        sheet.build()


@pytest.mark.parametrize("deferred", [False, True])
def test_rear_callout_and_furniture_edits_preserve_the_physical_view(rear_enclosure, deferred):
    from contextlib import nullcontext

    drawing = _rear_hole_sheet(rear_enclosure, "third").build()
    hole = drawing.model().features[0]
    drawing.drop(hole)
    with drawing.deferred() if deferred else nullcontext():
        name = drawing.callout(hole)
        marks = drawing.furniture(hole)
    claims = drawing.annotations_of(hole)
    assert claims
    assert all(drawing.view_of(name) == "rear" for name in claims)
    if not deferred:
        assert name in claims and set(marks) <= set(claims)
    assert any(getattr(drawing.get_annotation(name), "label", "") == "⌀4 ↧ 1.5" for name in claims)
    assert not [
        issue for issue in drawing.lint() if issue.code == "diameter_leader_target_mismatch"
    ]


@pytest.mark.parametrize("convention", ["first", "third"])
def test_epic_1508_combined_style_rear_and_wording_canary(rear_enclosure, convention, tmp_path):
    from collections import Counter

    from draftwright import Sheet, build_drawing
    from draftwright.audit import compare_measurements
    from draftwright.sheet_emit import emit_sheet_script

    # The third hole passes through the 2 mm back wall; the other two remain blind.
    part = rear_enclosure - Pos(0, 19, -16) * Rot(90, 0, 0) * Cylinder(2.5, 4)
    assert not part.is_inside(Vector(0, 18.25, -16))
    assert part.is_inside(Vector(-21, 18.25, 11))
    options = dict(
        page="A3",
        scale=1,
        scale_policy="strict",
        projection=convention,
        text_position="above",
        text_orientation="horizontal",
    )
    sheet = Sheet(part, **options).authored_dimensions()
    for diameter, x, z in ((4, -21, 11), (7, 14, -9)):
        hole = sheet.hole(diameter=diameter, at=(x, 20, z), axis="y", through=False, depth=1.5)
        sheet.dimension(hole, "bore.diameter")
        sheet.dimension(hole, "bore.depth")
        sheet.dimension(hole, "location")
    through = sheet.hole(diameter=5, at=(0, 20, -16), axis="y").through("DURCH")
    sheet.dimension(through, "bore.diameter")
    sheet.dimension(through, "location")
    envelope = sheet.envelope()
    sheet.dimension(envelope, "width.length")
    sheet.dimension(envelope, "height.length")
    sheet.view("rear")
    original = sheet.build()
    direct = build_drawing(
        part,
        model=sheet.model(),
        _views=("rear",),
        _include_iso=False,
        _view_constraints=sheet.view_constraints,
        **options,
    )
    script = emit_sheet_script(
        sheet.model(),
        "part = supplied_part",
        str(tmp_path / "epic"),
        title="EPIC",
        number="1508",
        formats=("svg", "pdf", "dxf"),
        view_constraints=sheet.view_constraints,
        **options,
    )
    namespace = {"supplied_part": part}
    exec(script, namespace)
    replay = namespace["drawing"]
    original_features = sheet.model().features
    for drawing in (original, direct, replay):
        labels = Counter(
            annotation.label
            for name, annotation in drawing.iter_annotations()
            if drawing.view_of(name) == "rear" and getattr(annotation, "label", "")
        )
        assert labels == Counter(
            ["⌀4 ↧ 1.5", "⌀7 ↧ 1.5", "⌀5 DURCH", "80", "50", "19", "54", "40", "16", "36", "9"]
        )
        assert drawing.view_plan.principal_names == ("rear",)
        assert drawing.view_plan.convention == convention
        assert drawing.draft.text_position == "above"
        assert drawing.draft.text_orientation == "horizontal"
        pairs = tuple(zip(original_features, drawing.model().features, strict=True))
        assert (
            compare_measurements(original, drawing, feature_pairs=pairs)["status"] == "preserved"
        )
        assert not [
            issue
            for issue in drawing.lint()
            if issue.code
            in {
                "diameter_leader_target_mismatch",
                "annotation_overlap",
                "annotation_ink_overlap",
                "callout_dropped",
                "overall_dim_withheld",
                "placement_unsatisfiable",
                "bore_through_not_placed",
            }
        ]
    assert all((tmp_path / f"epic.{ext}").stat().st_size > 100 for ext in ("svg", "pdf", "dxf"))


@pytest.mark.parametrize("intent", ["authored", "requested"])
def test_unrequested_rear_dimension_refuses_before_projection(rear_enclosure, intent, monkeypatch):
    import draftwright.builder as builder
    from draftwright import build_drawing
    from draftwright.model import hole
    from draftwright.model.ir import RequestedDimension
    from draftwright.view_plan import ViewPlanIncomplete

    feature = hole(diameter=4, at=(-21, 20, 11), axis="y", through=False, depth=1.5)
    request = RequestedDimension(feature, "bore.diameter", view="rear")

    def forbidden_projection(*args, **kwargs):
        raise AssertionError("unshowable rear requirement reached projection")

    monkeypatch.setattr(builder, "_assemble", forbidden_projection)
    with pytest.raises(ViewPlanIncomplete, match="rear"):
        build_drawing(rear_enclosure, model=[feature], **{intent: (request,)})


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("deferred", [False, True])
def test_rear_linear_edit_uses_selected_view_and_shared_strip(explicit, deferred):
    from contextlib import nullcontext

    from draftwright import Sheet

    sheet = Sheet(Box(80, 40, 50), page="A3", scale=1).authored_dimensions()
    envelope = sheet.envelope()
    sheet.dimension(envelope, "width.length")
    sheet.dimension(envelope, "height.length")
    sheet.view("rear")
    drawing = sheet.build()
    feature = drawing.model().features[0]
    assert set(drawing.drop(feature)) == {"m_env_width", "dim_height"}
    with drawing.deferred() if deferred else nullcontext():
        drawing.dimension(
            feature,
            "width.length",
            view="rear" if explicit else None,
            name="edited_width",
            priority=1,
        )
        drawing.dimension(
            feature,
            "height.length",
            view="rear" if explicit else None,
            name="edited_height",
            side="right",
            priority=1,
        )
    for name, value in (("edited_width", 80), ("edited_height", 50)):
        annotation = drawing.get_annotation(name)
        assert drawing.view_of(name) == "rear"
        assert annotation.measured_length == pytest.approx(value)
        assert drawing.registry.measurement_of(name)
    assert not drawing.lint()


def test_rear_discovery_exposes_rendered_holes_at_their_physical_positions(rear_enclosure):
    drawing = _rear_hole_sheet(rear_enclosure, "first").build()
    features = drawing.features("rear")
    assert sorted(feature.diameter for feature in features) == [4, 7]
    for feature in features:
        x, z = (-21, 11) if feature.diameter == 4 else (14, -9)
        assert feature.page_pos == pytest.approx(drawing.at("rear", x, 20, z)[:2])
        assert not feature.through and feature.depth == 1.5
    assert drawing.features("front") == []


@pytest.mark.parametrize("axis,point", [("x", (-39.5, 20, 5)), ("z", (-21, 20, -24.5))])
def test_short_rear_location_is_a_reported_loss_and_strict_refusal(axis, point):
    from draftwright import Sheet
    from draftwright.builder import ScaleIncompatibilityError

    part = Box(80, 40, 50) - Pos(point[0], 19.5, point[2]) * Rot(90, 0, 0) * Cylinder(0.1, 2)
    assert part.is_valid and not part.is_inside(Vector(point[0], 19.5, point[2]))
    sheet = Sheet(part, page="A3", scale=1, scale_policy="strict")
    hole = sheet.hole(diameter=0.2, at=point, axis="y", through=False, depth=1.5)
    sheet.dimension(hole, "location")
    sheet.view("rear")
    with pytest.raises(ScaleIncompatibilityError, match="off_axis_location_dropped"):
        sheet.build()
    from draftwright import build_drawing
    from draftwright._warnings import ScaleCompletenessWarning

    with pytest.warns(ScaleCompletenessWarning):
        drawing = build_drawing(
            part,
            model=sheet.model(),
            _views=("rear",),
            _include_iso=False,
            page="A3",
            scale=1,
            scale_policy="permissive",
        )
    drops = [issue for issue in drawing.lint() if issue.code == "off_axis_location_dropped"]
    assert len(drops) == 1 and "shorter than 1 mm" in drops[0].message
    assert any(identity.parameter.endswith(f".{axis}") for identity in drops[0].measurement_ids)
    assert not [issue for issue in drawing.lint() if issue.code == "hole_requirement_missing"]


@pytest.mark.parametrize("axis,views", [("x", ("side",)), ("z", ("plan",))])
def test_bbox_height_requires_a_selected_elevation_before_projection(axis, views, monkeypatch):
    import draftwright.builder as builder
    from draftwright import build_drawing
    from draftwright.model import hole
    from draftwright.view_plan import ViewPlanIncomplete

    cutter = Cylinder(2, 100) if axis == "z" else Rot(0, 90, 0) * Cylinder(2, 100)
    part = Box(80, 40, 50) - cutter
    feature = hole(diameter=4, at=(0, 0, 0), axis=axis)

    def forbidden_projection(*args, **kwargs):
        raise AssertionError("unshowable height reached projection")

    with monkeypatch.context() as guard:
        guard.setattr(builder, "_assemble", forbidden_projection)
        with pytest.raises(ViewPlanIncomplete, match="overall_height.length"):
            build_drawing(part, model=[feature], _views=views, _include_iso=False)
    drawing = build_drawing(
        part, model=[feature], _views=views, _include_iso=False, auto_dims=False
    )
    drawing.overall_height()
    assert "rear" not in drawing.views and "dim_height" not in drawing.annotations()
    issues = [issue for issue in drawing.lint() if issue.code == "placement_unsatisfiable"]
    assert len(issues) == 1 and "no planned front or rear" in issues[0].message
    assert issues[0].measurement_ids


@pytest.mark.parametrize("kind", ["grid", "bolt_circle"])
@pytest.mark.parametrize("convention", ["first", "third"])
def test_rear_grid_and_bolt_circle_keep_pattern_measurements(kind, convention):
    from draftwright import Sheet
    from draftwright.model import hole, pattern

    grammar = (
        dict(count=4, grid=(16, 24), rows=2, cols=2) if kind == "grid" else dict(count=3, bcd=24)
    )
    member = hole(diameter=4, at=(0, 20, 0), axis="y", through=False, depth=1.5)
    layout = pattern(member, kind=kind, **grammar)
    part = Box(80, 40, 50)
    for x, _, z in layout.members:
        part -= Pos(x, 19.5, z) * Rot(90, 0, 0) * Cylinder(2, 2)
    visible, _ = part.project_to_viewport((0, 1000, 0), look_at=(0, 0, 0))
    assert len([edge for edge in visible if edge.geom_type == GeomType.CIRCLE]) == layout.count
    sheet = Sheet(part, page="A3", scale=1, scale_policy="strict", projection=convention)
    handle = sheet.pattern(member, kind=kind, **grammar)
    for parameter in layout.parameters():
        sheet.dimension(handle, parameter.parameter_id, axis=parameter.discriminator)
    sheet.view("rear")
    drawing = sheet.build()
    assert drawing.view_plan.principal_names == ("rear",)
    marks = [
        (name, annotation)
        for name, annotation in drawing.iter_annotations()
        if getattr(annotation, "label", "") and drawing.view_of(name) == "rear"
    ]
    callouts = [
        annotation
        for _, annotation in marks
        if getattr(annotation, "covers_count", 0) == layout.count
    ]
    assert len(callouts) == 1 and "1.5" in callouts[0].label
    if kind == "grid":
        dimensions = [
            annotation.measured_length
            for _, annotation in marks
            if hasattr(annotation, "measured_length")
        ]
        assert sorted(dimensions) == pytest.approx([16, 24])
    else:
        assert "24" in callouts[0].label
        assert any(name.startswith("bc_rear") for name in drawing.annotations())
    assert not [
        issue
        for issue in drawing.lint()
        if issue.code.endswith("_dropped") or issue.code == "diameter_leader_target_mismatch"
    ]
