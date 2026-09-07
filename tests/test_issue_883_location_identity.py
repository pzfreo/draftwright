"""Select the member and measured axis of a location without changing its meaning."""

from dataclasses import replace

import pytest
from build123d import Box, Cylinder, Pos, Rot

from draftwright import Sheet, SoftDeprecationWarning, build_drawing
from draftwright.audit import diff_builds
from draftwright.linting.issues import LintIssue
from draftwright.model import hole as declared_hole
from draftwright.model.compiled import compile_dimensions, resolve_feature
from draftwright.model.ir import (
    Datum,
    Frame,
    PartModel,
    PatternFeature,
    PocketFeature,
    RequestedDimension,
)
from draftwright.model.planner import plan_dimensions
from draftwright.sheet_emit import emit_sheet_script
from draftwright.view_plan import ViewPlanIncomplete


@pytest.fixture(scope="module")
def grouped_holes():
    members = ((-15, -10, 0), (15, 10, 0))
    part = Box(60, 50, 10)
    for point in members:
        part -= Pos(*point) * Cylinder(2, 10)
    return part, members


def _sheet(grouped_holes, *, count=2):
    part, members = grouped_holes
    sheet = Sheet(part, scale=2).authored_dimensions()
    holes = sheet.hole(diameter=4, depth=10, axis="z", at=members[0], members=members, count=count)
    sheet.dimension(holes, "bore.diameter")
    return sheet, holes


def _locations(drawing):
    return {
        name: (drawing.get_annotation(name).label, drawing.measurement_keys(name))
        for name in drawing.annotations()
        if any("location" in key["parameter_id"] for key in drawing.measurement_keys(name))
    }


@pytest.fixture(scope="module")
def coarse_drawing(grouped_holes):
    sheet, holes = _sheet(grouped_holes)
    sheet.dimension(holes, "location")
    drawing = sheet.build()
    assert sorted(label for label, _ in _locations(drawing).values()) == ["15", "15", "35", "45"]
    return drawing


def test_each_member_and_axis_has_a_distinct_measurement_identity(coarse_drawing):
    rows = _locations(coarse_drawing)
    assert len(rows) == 4 and all(len(keys) == 1 for _, keys in rows.values())
    identities = {(keys[0]["feature"], keys[0]["parameter_id"]) for _, keys in rows.values()}
    assert len(identities) == 4, "Neither a member swap nor an axis swap may reuse an identity"


@pytest.mark.parametrize(
    "member,axis,label,name",
    [
        (0, "x", "15", "m_locx0"),
        (0, "y", "15", "m_locy0"),
        (1, "x", "45", "m_locx1"),
        (1, "y", "35", "m_locy1"),
    ],
)
def test_fine_location_selects_exactly_one_member_axis(
    grouped_holes, coarse_drawing, member, axis, label, name
):
    sheet, holes = _sheet(grouped_holes)
    sheet.dimension(holes, "location", member=member, axis=axis)
    selected = sheet.build()
    ((_, keys),) = _locations(selected).values()
    assert list(_locations(selected).values()) == [(label, keys)]
    assert keys == _locations(coarse_drawing)[name][1]
    assert {name for name in selected.annotations() if name.startswith("m_loc")} == set(
        _locations(selected)
    ), "An unapproved sibling axis must not leak a dimension without provenance"


@pytest.mark.parametrize("count", [1, 2])
def test_fine_location_round_trips_through_a_generated_declaration(grouped_holes, tmp_path, count):
    sheet, holes = _sheet(grouped_holes, count=count)
    sheet.dimension(holes, "location", member=1, axis="y")
    before = sheet.build()
    source = emit_sheet_script(
        sheet.model(),
        "part",
        str(tmp_path / "drawing"),
        title="Member location",
        number="883",
        scale=2,
        formats=(),
    )
    namespace = {"part": grouped_holes[0]}
    exec(compile(source, "<member-location>", "exec"), namespace)
    after = namespace["drawing"]
    assert _locations(after) == _locations(before)
    assert [label for label, _ in _locations(after).values()] == ["35"]


def test_profiled_group_preserves_member_selection_in_generated_declaration(
    grouped_holes, tmp_path
):
    _part, members = grouped_holes
    part = Box(60, 50, 10)
    for point in members:
        part -= Pos(*point) * (Cylinder(2, 10) & Box(2.8, 8, 10))
    sheet, handle = _sheet((part, members))
    sheet.dimension(handle, "location", member=1, axis="y")
    model = sheet.model()
    old = model.features[0]
    feature = replace(old, profile="double_d", across_flats=2.8, profile_direction=(1, 0, 0))
    model = replace(
        model,
        features=[feature, *model.features[1:]],
        authored_dimensions=tuple(
            replace(request, feature=feature) if request.feature is old else request
            for request in model.authored_dimensions
        ),
    )
    before = build_drawing(part, model=model, scale=2)
    assert [label for label, _ in _locations(before).values()] == ["35"]
    source = emit_sheet_script(
        model,
        "part",
        str(tmp_path / "drawing"),
        title="Profiled members",
        number="883",
        scale=2,
        formats=(),
    )
    namespace = {"part": part}
    exec(compile(source, "<profiled-member-location>", "exec"), namespace)
    after = namespace["drawing"]
    assert _locations(after) == _locations(before)
    rebuilt = next(item for item in after.model().features if item.kind == "hole")
    assert rebuilt.members == members
    assert rebuilt.count == 2 and rebuilt.profile == "double_d"


def test_discovery_names_the_components_that_dimension_accepts(grouped_holes):
    sheet, holes = _sheet(grouped_holes)
    before = sheet.model().authored_dimensions
    options = sheet.dimension_options(holes, "location")
    components = options["location_components"]
    assert {(item["member"], item["axis"]) for item in components} == {
        (0, "x"),
        (0, "y"),
        (1, "x"),
        (1, "y"),
    }
    assert len({item["parameter_id"] for item in components}) == 4
    for component in components:
        selected = {key: component[key] for key in ("member", "axis")}
        validation = sheet.validate_dimension(holes, "location", **selected)
        assert validation["supported"], validation
        assert validation["options"]["parameter_id"] == component["parameter_id"]
    assert sheet.model().authored_dimensions == before


@pytest.mark.parametrize(
    "selector",
    [
        {"member": True, "axis": "x"},
        {"member": -1, "axis": "x"},
        {"member": 2, "axis": "x"},
        {"member": "0", "axis": "x"},
        {"member": 0, "axis": "z"},
        {"member": 0},
        {"axis": "x"},
        {"member": "centre", "axis": "x"},
    ],
)
def test_invalid_location_selector_is_refused_before_intent_is_recorded(grouped_holes, selector):
    sheet, holes = _sheet(grouped_holes)
    before = sheet.model().authored_dimensions
    validation = sheet.validate_dimension(holes, "location", **selector)
    assert not validation["supported"]
    assert validation["issues"][0]["code"] == "invalid_measurement"
    with pytest.raises(ValueError):
        sheet.dimension(holes, "location", **selector)
    assert sheet.model().authored_dimensions == before


def test_member_cannot_select_a_size_or_an_unaddressable_location_family(grouped_holes):
    sheet, holes = _sheet(grouped_holes)
    with pytest.raises(ValueError, match="only a location"):
        sheet.dimension(holes, "bore.diameter", member=0)
    feature = next(feature for feature in sheet.model().features if feature.kind == "hole")
    with pytest.raises(ValueError, match="only a location"):
        RequestedDimension(feature, "bore.diameter", member=0)
    pocket = PocketFeature(Frame((10, 10, 0), "z"), "y", "x", 8, 20, 4, 10, 4, 24)
    sheet.features.append(pocket)
    options = sheet.dimension_options(pocket, "location")
    assert options["location_components"] == []
    with pytest.raises(ValueError, match="hole or hole pattern"):
        sheet.dimension(pocket, "location", member=0, axis="x")


def test_missing_datum_reports_the_requested_location_component(grouped_holes):
    sheet, holes = _sheet(grouped_holes)
    sheet.dimension(holes, "location", member=1, axis="y")
    model = replace(sheet.model(), datums=[])
    diagnostics = [
        item
        for item in compile_dimensions(model).diagnostics
        if item.parameter_id.startswith("location")
    ]
    assert {item.parameter_id for item in diagnostics} == {"location.location.member.1.y"}
    assert all("no datum_xy" in item.reason for item in diagnostics)


def test_omitting_the_whole_location_set_keeps_the_coarse_suppression(grouped_holes):
    sheet, _holes = _sheet(grouped_holes)
    compiled = compile_dimensions(sheet.model())
    assert compiled.locations == ()
    omissions = [item for item in compiled.diagnostics if item.parameter_id.startswith("location")]
    assert {item.parameter_id for item in omissions} == {"location.location"}
    assert all("authored" in item.reason for item in omissions)


def test_partial_member_omissions_do_not_suppress_an_axis_that_is_still_requested(grouped_holes):
    sheet, holes = _sheet(grouped_holes)
    sheet.dimension(holes, "location", member=0, axis="x")
    sheet.dimension(holes, "location", member=1, axis="y")
    plan = compile_dimensions(sheet.model())
    assert {item.id.parameter for item in plan.locations} == {
        "location.location.member.0.x",
        "location.location.member.1.y",
    }
    assert {
        item.parameter_id for item in plan.diagnostics if item.parameter_id.startswith("location")
    } == {"location.location.member.0.y", "location.location.member.1.x"}


@pytest.mark.parametrize("axis", ["x", "y", "z"])
def test_whole_axis_suppression_is_recorded_once_without_losing_member_facts(grouped_holes, axis):
    sheet, holes = _sheet(grouped_holes)
    model = sheet.model()
    feature = replace(model.features[0], frame=Frame((-15, -10, 0), axis))
    selected, omitted = [component for component in "xyz" if component != axis]
    model = replace(
        model,
        features=[feature],
        authored_dimensions=(
            RequestedDimension(feature, "location", discriminator=selected, member=0),
        ),
    )
    plan = compile_dimensions(model)
    assert len(plan.locations) == 1
    stem = "location" if axis == "z" else "location_off_axis"
    physical = f"{stem}.location.{omitted}" if axis == "z" else f"{stem}.{omitted}"
    omissions = [
        item.parameter_id for item in plan.diagnostics if item.parameter_id.startswith(stem)
    ]
    assert omissions.count(physical) == 1
    assert set(omissions) == {
        physical,
        f"{stem}.location.member.0.{omitted}",
        f"{stem}.location.member.1.{selected}",
        f"{stem}.location.member.1.{omitted}",
    }


@pytest.mark.parametrize("axis", ["x", "y", "z"])
def test_pattern_without_member_positions_reports_omission_in_every_orientation(
    grouped_holes, axis
):
    part, _members = grouped_holes
    feature = PatternFeature(
        Frame((0, 0, 0), axis),
        "linear",
        2,
        declared_hole(diameter=4, axis=axis, at=(0, 0, 0)),
        pitch=20,
    )
    model = PartModel(
        bbox=part.bounding_box(),
        orientation="prismatic",
        features=[feature],
        datums=[Datum(id="datum_xy", kind="point", at=(-30, -25, -5))],
        authored_dimensions=(RequestedDimension(feature, "location"),),
    )
    assert feature.members == ()
    plan = compile_dimensions(model)
    assert plan.locations == ()
    omissions = [item for item in plan.diagnostics if item.parameter_id.startswith("location")]
    assert len(omissions) == 1
    assert omissions[0].parameter_id == "location_pattern.location"
    assert "no members" in omissions[0].reason


def test_missing_view_names_only_the_selected_location_component(grouped_holes):
    sheet, holes = _sheet(grouped_holes)
    sheet.dimension(holes, "location", member=1, axis="y")
    with pytest.raises(ViewPlanIncomplete) as caught:
        plan_dimensions(sheet.model(), planned_views=("front", "side"))
    locations = [
        item for item in caught.value.uncovered if item.identity.parameter.startswith("location")
    ]
    assert {item.identity.parameter for item in locations} == {"location.location.member.1.y"}
    assert all(item.preferred_view == "plan" for item in locations)


def test_malformed_member_addresses_do_not_certify_a_physical_drop():
    part = Box(12, 40, 30) - Pos(0, 8, 6) * Rot(0, 90, 0) * Cylinder(3, 12)
    drawing = build_drawing(part)
    name = next(
        name
        for name in drawing.annotations()
        if any(
            key["parameter_id"] == "location_off_axis.location.member.0.z"
            for key in drawing.measurement_keys(name)
        )
    )
    (measurement,) = drawing.registry.measurement_of(name)
    drawing.remove(name)
    original_issues = drawing.registry.issues
    assert drawing.lint_summary()["by_code"]["hole_requirement_missing"] == 1
    for parameter in (
        "location_off_axis.location.member.01.z",
        "location_off_axis.location.member.-1.z",
        "location_off_axis.location.member.١.z",
        "location_off_axis.length.member.0.z",
        "location_off_axis.location.member.0.q",
        "unknown.location.member.0.z",
        measurement.parameter,
    ):
        drawing.registry.restore_issues(original_issues)
        drawing.registry.record_issue(
            LintIssue(
                severity="info",
                code="off_axis_location_dropped",
                message="recorded test failure",
                measurement_ids=(replace(measurement, parameter=parameter),),
            )
        )
        missing = drawing.lint_summary()["by_code"].get("hole_requirement_missing", 0)
        assert missing == (0 if parameter == measurement.parameter else 1), parameter


def test_comparison_detects_a_member_swap_with_the_same_name_and_value():
    members = ((-15, -10, 0), (15, -10, 0))
    part = Box(60, 50, 10)
    for point in members:
        part -= Pos(*point) * Cylinder(2, 10)
    drawings = []
    for member in (0, 1):
        sheet = Sheet(part, scale=2).authored_dimensions()
        holes = sheet.hole(diameter=4, depth=10, axis="z", at=members[0], members=members, count=2)
        sheet.dimension(holes, "bore.diameter")
        sheet.dimension(holes, "location", member=member, axis="y")
        drawings.append(sheet.build())
    for drawing in drawings:
        assert {name: label for name, (label, _) in _locations(drawing).items()} == {
            "m_locy0": "15"
        }
    difference = diff_builds(*drawings)
    assert difference["measurements_substituted"] == {
        "m_locy0": (
            [("hole", "location.location.member.0.y")],
            [("hole", "location.location.member.1.y")],
        )
    }


@pytest.mark.parametrize("reverse", [False, True])
def test_automatic_pattern_augmentation_survives_generated_declaration(
    grouped_holes, tmp_path, reverse
):
    part, members = grouped_holes
    if reverse:
        members = tuple(reversed(members))
    far_member = 0 if reverse else 1
    sheet = Sheet(part, scale=2)
    hole = declared_hole(diameter=4, depth=10, axis="z", at=members[0])
    pattern = sheet.pattern(hole, kind="other", count=2, members=members)
    with pytest.warns(SoftDeprecationWarning):
        sheet.auto_dimensions()
    with pytest.warns(SoftDeprecationWarning):
        sheet.add_dimension(pattern, "location", member=far_member, axis="x")
    before = sheet.build()
    assert sorted(label for label, _ in _locations(before).values()) == ["15", "15", "45"]
    source = emit_sheet_script(
        sheet.model(),
        "part",
        str(tmp_path / "drawing"),
        title="Augmented pattern",
        number="883",
        scale=2,
        formats=(),
    )
    namespace = {"part": part}
    exec(compile(source, "<augmented-pattern>", "exec"), namespace)
    assert _locations(namespace["drawing"]) == _locations(before)
    line = next(
        line
        for line in source.splitlines()
        if '"location"' in line and 'axis="x"' in line and f"member={far_member}" in line
    )
    edited = source.replace(line, "# " + line)
    namespace = {"part": part}
    exec(compile(edited, "<omitted-pattern-component>", "exec"), namespace)
    remaining = _locations(namespace["drawing"])
    assert sorted(label for label, _ in remaining.values()) == ["15", "15"]
    assert {keys[0]["parameter_id"]: label for label, keys in remaining.values()} == {
        keys[0]["parameter_id"]: label
        for label, keys in _locations(before).values()
        if label != "45"
    }


def test_addressable_compiler_result_retains_every_location_selector(grouped_holes):
    sheet, holes = _sheet(grouped_holes)
    sheet.dimension(holes, "location")
    model = sheet.model()
    feature = next(feature for feature in model.features if feature.kind == "hole")
    intents = [
        intent
        for intent in compile_dimensions(model).addressable()
        if resolve_feature(intent.ref) is feature and intent.role == "location"
    ]
    assert len(intents) == 4
    assert {(intent.member, intent.discriminator) for intent in intents} == {
        (0, "x"),
        (0, "y"),
        (1, "x"),
        (1, "y"),
    }


@pytest.fixture(scope="module", params=["x", "y"])
def side_drilled_group(request):
    axis = request.param
    members = ((0, -10, -5), (0, 10, 5)) if axis == "x" else ((-15, 0, -5), (15, 0, 5))
    part = Box(60, 50, 40)
    rotation = Rot(0, 90, 0) if axis == "x" else Rot(90, 0, 0)
    depth = 60 if axis == "x" else 50
    for point in members:
        part -= Pos(*point) * rotation * Cylinder(2, depth)
    return part, members, axis, depth


@pytest.mark.parametrize("member,component", [(0, 0), (0, 1), (1, 0), (1, 1)])
def test_side_drilled_group_selects_only_the_named_member_component(
    side_drilled_group, member, component
):
    part, members, normal, depth = side_drilled_group
    axis = [axis for axis in "xyz" if axis != normal][component]
    sheet = Sheet(part, scale=2).authored_dimensions()
    holes = sheet.hole(
        diameter=4, depth=depth, axis=normal, at=members[0], members=members, count=2
    )
    sheet.dimension(holes, "bore.diameter")
    sheet.dimension(holes, "location", member=member, axis=axis)
    drawing = sheet.build()
    ((label, keys),) = _locations(drawing).values()
    index = "xyz".index(axis)
    expected = members[member][index] - (-30, -25, -20)[index]
    assert label == str(expected)
    assert len(keys) == 1
    assert keys[0]["parameter_id"] == f"location_off_axis.location.member.{member}.{axis}"


@pytest.mark.parametrize("reverse", [False, True])
def test_emission_preserves_the_declared_member_order(grouped_holes, tmp_path, reverse):
    part, members = grouped_holes
    if reverse:
        members = tuple(reversed(members))
    sheet = Sheet(part, scale=2).authored_dimensions()
    holes = sheet.hole(diameter=4, depth=10, axis="z", at=members[0], members=members, count=2)
    sheet.dimension(holes, "bore.diameter")
    sheet.dimension(holes, "location", member=0, axis="x")
    before = sheet.build()
    source = emit_sheet_script(
        sheet.model(),
        "part",
        str(tmp_path / "drawing"),
        title="Member order",
        number="883",
        scale=2,
        formats=(),
    )
    namespace = {"part": part}
    exec(compile(source, "<member-order>", "exec"), namespace)
    assert _locations(namespace["drawing"]) == _locations(before)
    assert [label for label, _ in _locations(before).values()] == ["45" if reverse else "15"]


@pytest.mark.parametrize("fine_first", [False, True])
def test_coarse_and_fine_location_requests_form_an_idempotent_union(grouped_holes, fine_first):
    sheet, holes = _sheet(grouped_holes)
    requests = [{}, {"member": 1, "axis": "y"}]
    if fine_first:
        requests.reverse()
    for request in requests * 2:
        sheet.dimension(holes, "location", **request)
    locations = compile_dimensions(sheet.model()).locations
    assert len(locations) == len({location.id for location in locations}) == 4
    assert sorted(location.value for location in locations) == [15, 15, 35, 45]


@pytest.mark.parametrize("axis,expected", [("x", "30"), ("y", "25")])
def test_bolt_circle_centre_is_distinct_from_a_member(grouped_holes, tmp_path, axis, expected):
    part, members = grouped_holes
    sheet = Sheet(part, scale=2).authored_dimensions()
    hole = declared_hole(diameter=4, depth=10, axis="z", at=(0, 0, 0))
    pattern = sheet.pattern(
        hole,
        kind="bolt_circle",
        count=2,
        bcd=2 * (15**2 + 10**2) ** 0.5,
        members=members,
        at=(0, 0, 0),
    )
    sheet.dimension(pattern, "bore.diameter")
    sheet.dimension(pattern, "location", member="centre", axis=axis)
    before = sheet.build()
    ((label, keys),) = _locations(before).values()
    assert label == expected
    assert keys[0]["parameter_id"] == f"location_pattern.location.centre.{axis}"
    source = emit_sheet_script(
        sheet.model(),
        "part",
        str(tmp_path / "drawing"),
        title="Pattern centre",
        number="883",
        scale=2,
        formats=(),
    )
    namespace = {"part": part}
    exec(compile(source, "<pattern-centre>", "exec"), namespace)
    assert _locations(namespace["drawing"]) == _locations(before)


@pytest.mark.parametrize("second_axis", ["x", "y"])
def test_coincident_ordinate_records_only_approved_owners(second_axis):
    points = ((-15, -10, 0), (15, -10, 0))
    part = Box(60, 50, 10)
    for point, diameter in zip(points, (4, 6), strict=True):
        part -= Pos(*point) * Cylinder(diameter / 2, 10)
    sheet = Sheet(part, scale=2).authored_dimensions()
    handles = []
    for point, diameter in zip(points, (4, 6), strict=True):
        handle = sheet.hole(diameter=diameter, depth=10, axis="z", at=point)
        handles.append(handle)
        sheet.dimension(handle, "bore.diameter")
    sheet.dimension(handles[0], "location", member=0, axis="y")
    sheet.dimension(handles[1], "location", member=0, axis=second_axis)
    drawing = sheet.build()
    locations = _locations(drawing)
    y_name = next(name for name in locations if name.startswith("m_locy"))
    label, keys = locations[y_name]
    assert label == "15"
    assert {key["parameter_id"] for key in keys} == {"location.location.member.0.y"}
    assert len(keys) == (2 if second_axis == "y" else 1)
    features = [feature for feature in drawing.model().features if feature.kind == "hole"]
    assert len(features) == 2
    if second_axis == "x":
        assert len(locations) == 2
        assert y_name in drawing.annotations_of(features[0])
    else:
        assert len(locations) == 1
        drawing.drop(features[0])
        assert _locations(drawing)[y_name] == (label, keys)
