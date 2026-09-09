"""Feature schedules select exact compiler measurements, never a table of invented values."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from build123d import Box, Cylinder, Pos

from draftwright import Sheet, build_drawing
from draftwright.linting.evidence import compiled_values, verify_measurement_claims
from draftwright.model.compiled import compile_dimensions, resolve_feature
from draftwright.model.declare import envelope, hole
from draftwright.model.ir import (
    ChamferFeature,
    Datum,
    FeatureSchedule,
    Frame,
    PartModel,
    PatternFeature,
    RequestedDimension,
    ScheduleRow,
)
from draftwright.model.planner import (
    authored_dimension_requests,
    location_components,
    plan_dimensions,
    plan_locations,
    schedule_row_dimensions,
)
from draftwright.registry import AnnotationRegistry, MeasurementCell
from draftwright.view_plan import ViewPlanIncomplete


@pytest.fixture(scope="module")
def bolt_group():
    return hole(
        diameter=2.4,
        depth=10,
        through=True,
        axis="z",
        at=(2, 3, 0),
        members=((2, 3, 0), (8, 9, 0)),
        count=2,
    )


def test_schedule_keeps_identical_operations_as_distinct_targets(bolt_group):
    other = replace(bolt_group)
    rows = tuple(ScheduleRow(owner, ("bore.diameter",)) for owner in (bolt_group, other))
    model = PartModel(
        None,
        None,
        features=[bolt_group, other],
        authored_dimensions=(),
        schedules=(FeatureSchedule("bores", rows),),
    )
    selected = [schedule_row_dimensions(row)[0] for row in model.schedules[0].rows]
    assert selected[0].feature is bolt_group
    assert selected[1].feature is other
    assert selected[0].feature is not selected[1].feature


def test_schedule_refuses_equal_foreign_owner_and_inventory_replacement(bolt_group):
    schedule = FeatureSchedule("bores", (ScheduleRow(bolt_group, ("bore.diameter",)),))
    with pytest.raises(ValueError, match="identical feature"):
        PartModel(None, None, features=[replace(bolt_group)], schedules=(schedule,))
    model = PartModel(None, None, features=[bolt_group], schedules=(schedule,))
    model.features[:] = [replace(bolt_group)]
    with pytest.raises(ValueError, match="identical feature"):
        plan_dimensions(model)


@pytest.mark.parametrize("axis", ["x", "y", "z"])
def test_location_alias_selects_every_physical_member_and_transverse_axis(bolt_group, axis):
    feature = replace(bolt_group, frame=Frame((2, 3, 0), axis))
    requests = schedule_row_dimensions(ScheduleRow(feature, ("location",)))
    assert {(request.member, request.discriminator) for request in requests} == {
        (member, component) for member in range(2) for component in "xyz" if component != axis
    }
    assert all(request.feature is feature and request.role == "location" for request in requests)


def test_pattern_alias_does_not_substitute_anchor_for_member_locations(bolt_group):
    pattern = PatternFeature(
        frame=Frame((5, 6, 0), "z"),
        pattern="bolt_circle",
        member=bolt_group,
        members=bolt_group.members,
        count=2,
        bcd=10,
    )
    requests = schedule_row_dimensions(ScheduleRow(pattern, ("location",)))
    assert {(request.member, request.discriminator) for request in requests} == {
        (0, "x"),
        (0, "y"),
        (1, "x"),
        (1, "y"),
    }
    centre = next(item for item in location_components(pattern) if item["member"] == "centre")
    explicit = schedule_row_dimensions(ScheduleRow(pattern, (centre["parameter_id"],)))
    assert len(explicit) == 1 and explicit[0].member == "centre"


def test_exact_location_parameter_preserves_one_member_axis(bolt_group):
    component = location_components(bolt_group)[-1]
    (request,) = schedule_row_dimensions(ScheduleRow(bolt_group, (component["parameter_id"],)))
    assert (request.member, request.discriminator) == (1, "y")


@pytest.mark.parametrize("selectors", [("bore",), ("made.up",), ("bore.depth",)])
def test_invalid_or_unavailable_measurement_is_not_an_empty_credited_cell(bolt_group, selectors):
    with pytest.raises(ValueError, match="canonical measurement"):
        schedule_row_dimensions(ScheduleRow(bolt_group, selectors))


def test_overlapping_alias_and_exact_component_is_refused(bolt_group):
    component = location_components(bolt_group)[0]["parameter_id"]
    with pytest.raises(ValueError, match="repeats measurement"):
        schedule_row_dimensions(ScheduleRow(bolt_group, ("location", component)))


def test_ir_freezes_input_containers_without_copying_feature_identity(bolt_group):
    parameters = ["bore.diameter"]
    row = ScheduleRow(bolt_group, parameters)
    rows = [row]
    schedule = FeatureSchedule("bores", rows)
    parameters.clear()
    rows.clear()
    assert schedule.rows == (row,) and row.parameters == ("bore.diameter",)
    assert row.feature is bolt_group


@pytest.mark.parametrize(
    "parameters", [(), "bore.diameter", ("",), (2,), ("location", "location")]
)
def test_malformed_row_refuses_before_planning(bolt_group, parameters):
    with pytest.raises(ValueError, match="schedule"):
        ScheduleRow(bolt_group, parameters)


@pytest.mark.parametrize("name,prefer", [("", "tr"), ("bores", "middle")])
def test_schedule_needs_named_existing_table_region(bolt_group, name, prefer):
    with pytest.raises(ValueError, match="schedule"):
        FeatureSchedule(name, (ScheduleRow(bolt_group, ("bore.diameter",)),), prefer=prefer)


def test_empty_or_duplicate_named_schedules_are_refused(bolt_group):
    with pytest.raises(ValueError, match="ScheduleRow"):
        FeatureSchedule("empty", ())
    schedule = FeatureSchedule("bores", (ScheduleRow(bolt_group, ("bore.diameter",)),))
    with pytest.raises(ValueError, match="duplicate schedule"):
        PartModel(None, None, features=[bolt_group], schedules=(schedule, schedule))


@pytest.fixture(scope="module")
def schedule_part():
    return Box(20, 20, 10)


@pytest.fixture(scope="module")
def schedule_bbox(schedule_part):
    return schedule_part.bounding_box()


def _model(bolt_group, schedule_bbox, *, authored=(), parameters=("bore.diameter", "location")):
    return PartModel(
        schedule_bbox,
        None,
        features=[bolt_group],
        datums=[Datum("datum_xy", "point", at=(-10, -10, -5))],
        authored_dimensions=authored,
        schedules=(FeatureSchedule("bolts", (ScheduleRow(bolt_group, parameters),)),),
    )


def test_table_only_measurements_do_not_demand_a_leader_view(bolt_group, schedule_bbox):
    model = _model(bolt_group, schedule_bbox)
    (group,) = plan_dimensions(model, planned_views=("front",))
    assert [(pd.param.parameter_id, pd.suppressed) for pd in group.dims] == [
        ("bore.diameter", False),
    ]
    locations = plan_locations(model)
    assert len(locations) == 2 and all(not pd.suppressed for pd in locations)
    assert {pd.location_member for pd in locations} == {0, 1}
    assert model.authored_dimensions == (), "Resolving schedules must not rewrite source intent"


def test_same_measurement_requested_as_annotation_still_requires_its_view(
    bolt_group, schedule_bbox
):
    model = _model(
        bolt_group, schedule_bbox, authored=(RequestedDimension(bolt_group, "bore.diameter"),)
    )
    with pytest.raises(ViewPlanIncomplete):
        plan_dimensions(model, planned_views=("front",))
    assert plan_dimensions(model, planned_views=("plan",))


def test_schedule_reuses_annotation_display_policy_and_feature_tolerance(
    bolt_group, schedule_bbox
):
    request = RequestedDimension(bolt_group, "bore.diameter", display_decimals=3, view="plan")
    model = _model(bolt_group, schedule_bbox, authored=(request,))
    model.decorations[(bolt_group, "diameter")] = 0.02
    (group,) = plan_dimensions(model, planned_views=("plan",))
    (diameter,) = group.dims
    assert diameter.display_decimals == 3 and diameter.view == "plan"
    assert diameter.param.tolerance == 0.02
    assert authored_dimension_requests(model)[0] is request


def test_planning_refuses_schedule_on_automatic_source(bolt_group, schedule_bbox):
    model = _model(bolt_group, schedule_bbox, authored=None)
    with pytest.raises(ValueError, match="authored dimension set"):
        plan_dimensions(model)


def test_planning_revalidates_mutated_schedule_owners(bolt_group, schedule_bbox):
    model = _model(bolt_group, schedule_bbox)
    model.features[:] = [replace(bolt_group)]
    with pytest.raises(ValueError, match="identical feature"):
        plan_dimensions(model)


def _measured_cells(plan):
    return [
        cell
        for schedule in plan.schedules
        for row in schedule.rows
        for cell in row
        if cell.measurement is not None
    ]


def test_compiler_routes_schedule_measurements_without_ordinary_ink(bolt_group, schedule_bbox):
    model = _model(bolt_group, schedule_bbox)
    plan = compile_dimensions(model, include_overall=False, planned_views=("front",))
    cells = _measured_cells(plan)
    assert len(cells) == 5
    assert [cell.text for cell in cells] == ["Ø2.4 THRU", "12", "13", "18", "19"]
    assert all(resolve_feature(cell.measurement.ref) is bolt_group for cell in cells)
    assert {cell.measurement.id.parameter for cell in cells} == {
        "bore.diameter",
        *(component["parameter_id"] for component in location_components(bolt_group)),
    }
    assert not plan.locations and not any(group.dims for group in plan.groups)
    assert len(plan.addressable()) == 5
    values = compiled_values(plan)
    assert all(cell.measurement in values[cell.measurement.id] for cell in cells)
    assert len({len(row) for row in plan.schedules[0].rows}) == 1


def test_schedule_and_annotation_share_approved_precision_tolerance_and_span(
    bolt_group, schedule_bbox
):
    request = RequestedDimension(bolt_group, "bore.diameter", display_decimals=3)
    model = _model(bolt_group, schedule_bbox, authored=(request,), parameters=("bore.diameter",))
    model.decorations[(bolt_group, "diameter")] = (0.002, 0.015)
    plan = compile_dimensions(model, include_overall=False)
    (cell,) = _measured_cells(plan)
    (diameter,) = next(group.dims for group in plan.groups if group.dims)
    assert cell.measurement is diameter
    assert cell.text == "Ø2.400 +0.015 -0.002 THRU"
    assert diameter.value == 2.4 and diameter.tolerance == (0.002, 0.015)


def test_mixed_ordinary_and_scheduled_location_keeps_only_selected_annotation(
    bolt_group, schedule_bbox
):
    model = _model(
        bolt_group,
        schedule_bbox,
        authored=(RequestedDimension(bolt_group, "location", member=1, discriminator="y"),),
        parameters=("location",),
    )
    plan = compile_dimensions(model, include_overall=False, planned_views=("plan",))
    assert len(_measured_cells(plan)) == 4
    (location,) = plan.locations
    assert (location.location_member, location.discriminator, location.value) == (1, "y", 19)


def test_equal_nominal_through_and_blind_operations_keep_separate_cell_meanings(
    bolt_group, schedule_bbox
):
    blind = replace(bolt_group, through=False, depth=1.5, frame=Frame((2, 3, 0), "x"))
    model = _model(bolt_group, schedule_bbox, parameters=("bore.diameter",))
    model.features.append(blind)
    model.schedules = (
        FeatureSchedule(
            "operations",
            (
                ScheduleRow(bolt_group, ("bore.diameter",)),
                ScheduleRow(blind, ("bore.diameter", "bore.depth")),
            ),
        ),
    )
    cells = _measured_cells(
        compile_dimensions(model, include_overall=False, planned_views=("front",))
    )
    assert [cell.text for cell in cells] == ["Ø2.4 THRU", "Ø2.4", "↓1.5"]
    assert cells[0].measurement.id.feature is bolt_group
    assert cells[1].measurement.id.feature is cells[2].measurement.id.feature is blind


def test_missing_datum_refuses_schedule_instead_of_claiming_empty_location_cells(
    bolt_group, schedule_bbox
):
    model = _model(bolt_group, schedule_bbox, parameters=("location",))
    model.datums.clear()
    with pytest.raises(ValueError, match="exactly one approved measurement; found 0"):
        compile_dimensions(model, include_overall=False)


def test_sheet_schedule_uses_final_feature_after_handle_edit_and_reorder(
    bolt_group, schedule_part
):
    sheet = Sheet(schedule_part)
    bolt = sheet.hole(diameter=2.4, depth=10, axis="z", at=(2, 3, 0), through=True)
    other = sheet.add(replace(bolt_group, diameter=4))
    sheet.schedule([(bolt, ("bore.diameter",))], name="bolts")
    bolt.through("THROUGH")
    bolt.tolerance(0.02)
    sheet.features.reverse()
    model = sheet.model()
    (row,) = model.schedules[0].rows
    assert row.feature is model.features[1]
    assert row.feature is not model.features[0]
    assert other.dimension_ids() == bolt.dimension_ids()
    (cell,) = _measured_cells(compile_dimensions(model, include_overall=False))
    assert cell.text == "Ø2.4 ±0.02 THROUGH"


def test_invalid_sheet_schedule_is_transactional(bolt_group, schedule_part):
    sheet = Sheet(schedule_part).authored_dimensions()
    bolt = sheet.add(bolt_group)
    with pytest.raises(ValueError, match="canonical measurement"):
        sheet.schedule([(bolt, ("bore.diameter",)), (bolt, ("nonsense",))], name="bad")
    assert sheet.model().schedules == ()
    sheet.schedule([(bolt, ("bore.diameter",))], name="good")
    with pytest.raises(ValueError, match="duplicate schedule"):
        sheet.schedule([(bolt, ("location",))], name="good")
    assert len(sheet.model().schedules) == 1


@pytest.mark.parametrize("ordinary", [False, True])
def test_scheduled_overall_height_keeps_canonical_identity_and_no_empty_ladder(
    schedule_part, ordinary
):
    feature = envelope(schedule_part)
    model = _model(
        feature,
        schedule_part.bounding_box(),
        parameters=("height.length",),
        authored=(RequestedDimension(feature, "height.length"),) if ordinary else (),
    )
    plan = compile_dimensions(model)
    (cell,) = _measured_cells(plan)
    assert cell.measurement.id.parameter == "height.length"
    assert [(entry.role, entry.member) for entry in plan.addressable()] == [
        ("height.length", None)
    ]
    assert all(ladder.rungs for ladder in plan.ladders)
    assert bool(plan.ladder("overall_height")) is ordinary


@pytest.mark.parametrize("other_leg,angle,expected", [(2, 45, "C2"), (1, 26.6, "2 × 26.6°")])
def test_schedule_preserves_chamfer_form(schedule_bbox, other_leg, angle, expected):
    feature = ChamferFeature(Frame((0, 0, 0), "z"), "z", 2, other_leg, angle)
    model = _model(feature, schedule_bbox, parameters=("chamfer.length",))
    (cell,) = _measured_cells(compile_dimensions(model, include_overall=False))
    assert cell.text == expected


def test_schedule_preserves_countersink_angle_units(bolt_group, schedule_bbox):
    feature = replace(bolt_group, csink=(4.8, 90))
    model = _model(feature, schedule_bbox, parameters=("countersink.angle",))
    (cell,) = _measured_cells(compile_dimensions(model, include_overall=False))
    assert cell.text == "90°"


def _registered_table(plan):
    (schedule,) = plan.schedules
    table = SimpleNamespace(
        table_rows=tuple(tuple(cell.text for cell in row) for row in schedule.rows)
    )
    table.measurement_schedule = schedule.name
    cells = tuple(
        MeasurementCell(schedule.name, ri, ci, cell.measurement.id)
        for ri, row in enumerate(schedule.rows)
        for ci, cell in enumerate(row)
        if cell.measurement is not None
    )
    registry = AnnotationRegistry()
    registry.add(table, schedule.name, None, cells=cells)
    return registry, table, cells


def test_cell_verifier_cannot_borrow_a_matching_number_from_another_row(bolt_group, schedule_bbox):
    model = _model(bolt_group, schedule_bbox, parameters=("location",))
    plan = compile_dimensions(model, include_overall=False)
    registry, table, cells = _registered_table(plan)
    assert len(verify_measurement_claims(registry, plan)) == 4
    assert all(
        outcome.state == "confirmed" for outcome in verify_measurement_claims(registry, plan)
    )
    target, neighbour = cells[0], cells[-1]
    rows = [list(row) for row in table.table_rows]
    original = rows[target.row][target.column]
    rows[target.row][target.column] = rows[neighbour.row][neighbour.column]
    rows[neighbour.row][neighbour.column] = original
    table.table_rows = tuple(tuple(row) for row in rows)
    outcomes = verify_measurement_claims(registry, plan)
    assert {outcome.cell for outcome in outcomes if outcome.state == "value_absent"} == {
        (target.row, target.column),
        (neighbour.row, neighbour.column),
    }
    assert sum(outcome.state == "confirmed" for outcome in outcomes) == 2


@pytest.mark.parametrize("change", ["tolerance", "through", "quantity", "axis", "row_removed"])
def test_cell_verifier_checks_engineering_content_and_actual_row_presence(
    bolt_group, schedule_bbox, change
):
    model = _model(bolt_group, schedule_bbox, parameters=("bore.diameter",))
    model.decorations[(bolt_group, "diameter")] = 0.02
    plan = compile_dimensions(model, include_overall=False)
    registry, table, (cell,) = _registered_table(plan)
    rows = [list(row) for row in table.table_rows]
    if change == "row_removed":
        rows.pop(cell.row)
    elif change == "quantity":
        rows[cell.row][rows[0].index("Qty")] = "9"
    elif change == "axis":
        rows[cell.row][rows[0].index("Axis")] = "X"
    else:
        rows[cell.row][cell.column] = rows[cell.row][cell.column].replace(
            " ±0.02" if change == "tolerance" else " THRU", ""
        )
    table.table_rows = tuple(tuple(row) for row in rows)
    (outcome,) = verify_measurement_claims(registry, plan)
    assert outcome.state == ("unreadable" if change == "row_removed" else "value_absent")
    assert outcome.cell == (cell.row, cell.column)


def test_cell_provenance_refuses_equal_foreign_owner_and_unsliced_extra_claim(
    bolt_group, schedule_bbox
):
    plan = compile_dimensions(_model(bolt_group, schedule_bbox), include_overall=False)
    registry, table, cells = _registered_table(plan)
    first = cells[0]
    foreign = replace(first.measurement, feature=replace(bolt_group))
    registry.add(
        table,
        "bolts",
        None,
        cells=(replace(first, measurement=foreign), *cells[1:]),
        measurement=replace(first.measurement, parameter="invented.measurement"),
    )
    outcomes = verify_measurement_claims(registry, plan)
    assert outcomes[0].state == "unresolved" and outcomes[-1].state == "unresolved"
    assert len(outcomes) == len(cells) + 1


def test_cell_identity_survives_rollback_but_not_removal_or_fresh_replacement(
    bolt_group, schedule_bbox
):
    plan = compile_dimensions(_model(bolt_group, schedule_bbox), include_overall=False)
    registry, table, cells = _registered_table(plan)
    snapshot = registry.snapshot()
    identity = registry.identity_of("bolts")
    registry.pin("bolts")
    registry.remove("bolts")
    assert registry.cells_of("bolts") == () and registry.measurement_of("bolts") == ()
    registry.add(table, "bolts", None)
    assert registry.cells_of("bolts") == ()
    registry.reapply("bolts", identity)
    assert registry.cells_of("bolts") == cells
    registry.clear(())
    assert registry.cells_of("bolts") == ()
    registry.restore(snapshot)
    assert registry.cells_of("bolts") == cells
    assert all(
        outcome.state == "confirmed" for outcome in verify_measurement_claims(registry, plan)
    )


@pytest.mark.parametrize("rename", [False, True])
def test_losing_all_cell_addresses_cannot_downgrade_to_numeric_bag(
    bolt_group, schedule_bbox, rename
):
    model = _model(bolt_group, schedule_bbox, parameters=("bore.diameter",))
    model.decorations[(bolt_group, "diameter")] = 0.02
    plan = compile_dimensions(model, include_overall=False)
    registry, table, (cell,) = _registered_table(plan)
    rows = [list(row) for row in table.table_rows]
    rows[cell.row][cell.column] = "Ø2.4"
    table.table_rows = tuple(tuple(row) for row in rows)
    identities = registry.measurement_of("bolts")
    registry.remove("bolts")
    registry.add(table, "renamed" if rename else "bolts", None, measurement=identities)
    (outcome,) = verify_measurement_claims(registry, plan)
    assert outcome.state == "unresolved"


def test_raising_cell_content_is_unreadable_evidence(bolt_group, schedule_bbox):
    plan = compile_dimensions(_model(bolt_group, schedule_bbox), include_overall=False)
    registry, _table, cells = _registered_table(plan)

    class BrokenTable:
        @property
        def table_rows(self):
            raise RuntimeError("unavailable table content")

    registry.add(BrokenTable(), "bolts", None, cells=cells)
    outcomes = verify_measurement_claims(registry, plan)
    assert len(outcomes) == 5 and all(outcome.state == "unreadable" for outcome in outcomes)


@pytest.fixture(scope="module")
def rendered_schedule(bolt_group):
    part = Box(40, 40, 10)
    for point in bolt_group.members:
        part -= Pos(*point) * Cylinder(1.2, 10)
    sheet = Sheet(part, page="A3", detail_view=False).authored_views()
    feature = sheet.add(bolt_group)
    sheet.schedule([(feature, ("bore.diameter", "location"))], name="bolts")
    sheet.view("front")
    return sheet, sheet.build()


def test_public_schedule_build_places_actual_cells_through_the_table_path(rendered_schedule):
    _sheet, drawing = rendered_schedule
    table = drawing.get_annotation("bolts")
    assert table is not None
    assert set(drawing.views) == {"front"}
    assert len(drawing.registry.cells_of("bolts")) == 5
    outcomes = verify_measurement_claims(drawing.registry, compile_dimensions(drawing.model()))
    assert len(outcomes) == 5 and all(outcome.state == "confirmed" for outcome in outcomes)
    assert [
        table.table_rows[cell.row][cell.column] for cell in drawing.registry.cells_of("bolts")
    ] == [
        "Ø2.4 THRU",
        "22",
        "23",
        "28",
        "29",
    ]
    assert not any(name.startswith(("hc_", "m_loc")) for name in drawing.annotations())


def test_bare_ir_build_places_same_schedule_as_sheet_front_door(rendered_schedule):
    sheet, drawing = rendered_schedule
    replay = build_drawing(drawing.working_part, model=sheet.model(), page="A3", detail_view=False)
    assert replay.get_annotation("bolts").table_rows == drawing.get_annotation("bolts").table_rows
    assert len(replay.registry.cells_of("bolts")) == 5


def test_measurement_snapshot_binds_each_actual_cell_separately(rendered_schedule):
    from draftwright.document_evidence import bind_document_claims

    _sheet, drawing = rendered_schedule
    snapshot = drawing.measurement_snapshot()
    claims = [claim for claim in snapshot.claims if claim.annotation == "bolts"]
    assert len(claims) == 5
    assert {claim.cell for claim in claims} == {
        (cell.row, cell.column) for cell in drawing.registry.cells_of("bolts")
    }
    assert all(len(claim.meaning) == len(claim.approved) == 1 for claim in claims)
    bound = bind_document_claims("features", snapshot, drawing.registry)
    assert not bound.unknown
    assert len(bound.claims) == 5
    locations = [claim for claim in bound.claims if claim.address[:1] == ("location",)]
    assert len(locations) == 4 and len({claim.address for claim in locations}) == 4
    assert {claim.cell for claim in bound.claims} == {claim.cell for claim in claims}


def test_wrong_schedule_cell_does_not_taint_other_cell_snapshots(rendered_schedule):
    from draftwright.audit import compare_measurements

    _sheet, drawing = rendered_schedule
    table = drawing.get_annotation("bolts")
    original = table.table_rows
    before = drawing.measurement_snapshot()
    target = drawing.registry.cells_of("bolts")[1]
    rows = [list(row) for row in original]
    rows[target.row][target.column] = "999"
    try:
        table.table_rows = tuple(tuple(row) for row in rows)
        after = drawing.measurement_snapshot()
        assert len(after.claims) == 4
        assert all(claim.cell != (target.row, target.column) for claim in after.claims)
        assert after.unknown == (("bolts", "compiled_claim_unconfirmed"),)
        (uncertain,) = after.cell_unknown
        assert uncertain.annotation == "bolts" and uncertain.cell == (target.row, target.column)
        for claim in after.claims:
            prior = next(item for item in before.claims if item.cell == claim.cell)
            assert claim == prior
        assert compare_measurements(before, after)["status"] == "unknown"
    finally:
        table.table_rows = original


def test_document_unknown_keeps_each_bad_cell_address(rendered_schedule):
    from draftwright.document_evidence import bind_document_claims

    _sheet, drawing = rendered_schedule
    table = drawing.get_annotation("bolts")
    original = table.table_rows
    cells = drawing.registry.cells_of("bolts")[1:3]
    rows = [list(row) for row in original]
    for cell in cells:
        rows[cell.row][cell.column] = "999"
    try:
        table.table_rows = tuple(tuple(row) for row in rows)
        snapshot = drawing.measurement_snapshot()
        assert len(snapshot.claims) == 3
        bound = bind_document_claims("features", snapshot, drawing.registry)
        assert {item.cell for _, item in bound.cell_unknown} == {
            (cell.row, cell.column) for cell in cells
        }
        assert all(sheet == "features" for sheet, _ in bound.cell_unknown)
    finally:
        table.table_rows = original


def test_document_conflict_retains_both_schedule_cell_addresses(rendered_schedule):
    from draftwright.document_evidence import bind_document_claims, document_conflicts

    sheet, drawing = rendered_schedule
    model = sheet.model()
    feature = model.features[0]
    model.decorations[(feature, "diameter")] = 0.02
    toleranced = build_drawing(drawing.working_part, model=model, page="A3", detail_view=False)
    assert "±0.02" in toleranced.get_annotation("bolts").table_rows[1][4]
    snapshots = (
        bind_document_claims("nominal", drawing.measurement_snapshot(), drawing.registry),
        bind_document_claims("toleranced", toleranced.measurement_snapshot(), toleranced.registry),
    )
    assert all(not snapshot.unknown for snapshot in snapshots)
    (conflict,) = document_conflicts(snapshots)
    assert {claim.sheet for claim in conflict.claims} == {"nominal", "toleranced"}
    assert all(
        claim.parameter == "bore.diameter" and claim.cell == (1, 4) for claim in conflict.claims
    )


def test_unfit_schedule_preserves_ink_and_records_its_measurements(rendered_schedule):
    _sheet, drawing = rendered_schedule
    registry_before = drawing.registry.snapshot()
    issues_before = list(drawing.registry.issues)
    items_before = tuple(drawing.items)
    font_size = drawing.draft.font_size
    cells = drawing.registry.cells_of("bolts")
    table = drawing.get_annotation("bolts")
    rows = (table.table_rows[0],) + table.table_rows[1:] * 50
    try:
        assert (
            drawing.add_table(
                rows, name="unfit_schedule", _source_id="schedule:bolts", _cells=cells
            )
            is None
        )
        assert drawing.registry.cells_of("unfit_schedule") == ()
        assert drawing.registry.measurement_of("unfit_schedule") == ()
        assert tuple(drawing.items) == items_before
        assert drawing.registry.snapshot() == registry_before
        assert drawing.draft.font_size == font_size
        issue = drawing.registry.issues[-1]
        assert issue.code == "table_dropped"
        assert issue.source_ids == ("schedule:bolts",)
        assert issue.measurement_ids == tuple(cell.measurement for cell in cells)
    finally:
        drawing.registry.restore_issues(issues_before)


def test_schedule_name_collision_preserves_registered_cells(rendered_schedule):
    _sheet, drawing = rendered_schedule
    before = drawing.registry.snapshot()
    items = tuple(drawing.items)
    with pytest.raises(ValueError, match="already belongs"):
        drawing.add_table(
            drawing.get_annotation("bolts").table_rows,
            name="bolts",
            _cells=drawing.registry.cells_of("bolts"),
        )
    assert drawing.registry.snapshot() == before
    assert tuple(drawing.items) == items


def test_interrupted_cell_commit_restores_registry_ink_and_pins(rendered_schedule, monkeypatch):
    _sheet, drawing = rendered_schedule
    original_registry = drawing.registry.snapshot()
    drawing.pin("bolts")
    before = drawing.registry.snapshot()
    items = tuple(drawing.items)
    original_add = drawing.registry.add
    issues = drawing.registry.issues

    def interrupted_add(annotation, name, view, *args, **kwargs):
        original_add(annotation, name, view, *args, **kwargs)
        drawing.remove("bolts")
        drawing.registry.record_issue(SimpleNamespace(code="interrupted_side_effect"))
        raise KeyboardInterrupt("cancel after registration")

    monkeypatch.setattr(drawing.registry, "add", interrupted_add)
    try:
        with pytest.raises(KeyboardInterrupt, match="cancel after registration"):
            drawing.add_table(
                drawing.get_annotation("bolts").table_rows,
                name="interrupted_schedule",
                _cells=drawing.registry.cells_of("bolts"),
            )
        assert drawing.registry.snapshot() == before
        assert tuple(drawing.items) == items
        assert drawing.registry.issues == issues
        assert drawing.registry.cells_of("interrupted_schedule") == ()
    finally:
        drawing.registry.restore(original_registry)
        drawing.registry.restore_issues(issues)
        drawing.items[:] = items


def test_emitted_recipe_keeps_schedule_intent_and_owned_row_bindings(rendered_schedule):
    from draftwright.sheet_emit import emit_sheet_script

    sheet, drawing = rendered_schedule
    source = emit_sheet_script(
        sheet.model(),
        "part = PART",
        "unused",
        title="Schedule",
        number="TEST",
        page="A3",
        view_constraints=sheet.view_constraints,
    )
    assert "sheet.schedule([" in source
    assert "sheet.dimension(" not in "\n".join(
        line for line in source.splitlines() if not line.startswith("#")
    )
    namespace = {"PART": drawing.working_part}
    exec(
        compile(source[: source.index("drawing = sheet.build()")], "<schedule-recipe>", "exec"),
        namespace,
    )
    replay_sheet = namespace["sheet"]
    model = replay_sheet.model()
    (schedule,) = model.schedules
    assert schedule.name == "bolts" and schedule.prefer == "tr"
    assert schedule.rows[0].feature is model.features[0]
    assert schedule.rows[0].feature is not sheet.model().features[0]
    assert schedule.rows[0].parameters == ("bore.diameter", "location")
    replay = replay_sheet.build()
    assert replay.get_annotation("bolts").table_rows == drawing.get_annotation("bolts").table_rows
    assert len(replay.registry.cells_of("bolts")) == 5


def test_combined_document_verifies_same_named_schedules_against_each_member_plan(
    monkeypatch, tmp_path
):
    from build123d import export_step

    from draftwright import Document
    from draftwright.linting import requirements

    source = tmp_path / "part.step"
    export_step(Box(30, 20, 5) - Cylinder(2, 10), source)
    document = Document.from_part(source)
    hole = next(feature for feature in document.features if feature.kind == "hole")
    for name, tolerance in (("first", 0.02), ("second", 0.05)):
        sheet = document.sheet(name, detail_view=False, page="A3").authored_views()
        sheet.view("front")
        sheet.of(hole).tolerance(tolerance)
        sheet.schedule([(hole, ("bore.diameter",))], name="holes")
    result = document.build()
    original = requirements.recognized_requirement_outcomes
    captured = []

    def observe(recognition, features, registry, omissions, **kwargs):
        if "0:holes" in registry.names():
            captured.extend(verify_measurement_claims(registry, kwargs["dimension_plan"]))
        return original(recognition, features, registry, omissions, **kwargs)

    monkeypatch.setattr(requirements, "recognized_requirement_outcomes", observe)
    evaluation = result._evaluate()
    assert len(captured) == 2
    assert all(outcome.state == "confirmed" and outcome.cell == (1, 4) for outcome in captured)
    assert {outcome.annotation for outcome in captured} == {"0:holes", "1:holes"}
    assert len(evaluation.conflicts) == 1


def test_partial_drop_of_shared_schedule_refuses_before_removing_any_ink():
    part = Box(40, 40, 10) - Pos(-8, 0, 0) * Cylinder(1.2, 10) - Pos(8, 0, 0) * Cylinder(2, 10)
    sheet = Sheet(part, page="A3", detail_view=False).authored_views()
    first = sheet.hole(diameter=2.4, depth=10, through=True, at=(-8, 0, 0), axis="z")
    second = sheet.hole(diameter=4, depth=10, through=True, at=(8, 0, 0), axis="z")
    sheet.view("front")
    sheet.schedule([(first, ("bore.diameter",)), (second, ("bore.diameter",))], name="operations")
    drawing = sheet.build()
    owner = drawing.registry.cells_of("operations")[0].measurement.feature
    assert len(drawing.measurement_snapshot().claims) == 2
    drawing.note("FIRST FEATURE NOTE", (20, 20), name="first_note")
    identity = drawing.registry.identity_of("first_note")
    identity["feature"] = owner
    drawing.registry.reapply("first_note", identity)
    drawing.pin("operations")
    registry = drawing.registry.snapshot()
    items = tuple(drawing.items)
    before = drawing.measurement_snapshot()
    with pytest.raises(ValueError, match="also measures other features"):
        drawing.drop(owner)
    assert drawing.registry.snapshot() == registry and tuple(drawing.items) == items
    assert drawing.measurement_snapshot() == before
    drawing.remove("operations")
    assert drawing.registry.cells_of("operations") == ()


def test_schedule_only_locations_do_not_reserve_an_ordinary_rear_corridor(schedule_bbox):
    from draftwright.compose import _compose_anno_boxes

    feature = hole(diameter=2.4, depth=20, through=True, axis="y", at=(2, 0, 3))
    scheduled = _model(feature, schedule_bbox, parameters=("location",))
    omitted = replace(scheduled, schedules=())
    ordinary = replace(scheduled, authored_dimensions=(RequestedDimension(feature, "location"),))
    assert compile_dimensions(scheduled).locations == ()
    assert compile_dimensions(ordinary).locations
    for model in (omitted, scheduled):
        assert not any(box.side == "rear_right" for box in _compose_anno_boxes(model, 0))
    assert any(box.side == "rear_right" for box in _compose_anno_boxes(ordinary, 0))


@pytest.mark.parametrize("planner_name", ("plan_dimensions", "plan_locations"))
def test_schedule_selectors_expand_once_per_row_in_each_plan(monkeypatch, planner_name):
    from draftwright.model import planner

    features = [
        hole(diameter=2.4, depth=10, through=True, at=(index * 4, 0, 0), axis="z")
        for index in range(80)
    ]
    schedule = FeatureSchedule(
        "holes", tuple(ScheduleRow(feature, ("bore.diameter", "location")) for feature in features)
    )
    model = PartModel(
        None,
        None,
        features=features,
        datums=[Datum("datum_xy", "point", (-10, -10, 0))],
        authored_dimensions=(),
        schedules=(schedule,),
    )
    original = planner.schedule_row_dimensions
    expanded = []

    def observe(row):
        expanded.append(row)
        return original(row)

    monkeypatch.setattr(planner, "schedule_row_dimensions", observe)
    plan = getattr(planner, planner_name)(model)
    assert plan and len(expanded) == len(features)
    assert model.schedules == (schedule,) and model.authored_dimensions == ()
    # A new pass must see changed public IR; a cached selection would retain the old rows.
    model.schedules = (FeatureSchedule("first", (schedule.rows[0],)),)
    expanded.clear()
    updated = getattr(planner, planner_name)(model)
    assert expanded == [schedule.rows[0]]
    if planner_name == "plan_dimensions":
        selected = [
            group.feature
            for group in updated
            for dimension in group.dims
            if not dimension.suppressed
        ]
    else:
        selected = [dimension.feature for dimension in updated if not dimension.suppressed]
    assert len(selected) == 1 and selected[0] is features[0]


@pytest.mark.parametrize(
    "fields",
    (
        {"schedule": ""},
        {"schedule": None},
        {"row": 0},
        {"row": True},
        {"row": 1.5},
        {"column": -1},
        {"column": False},
        {"column": "1"},
        {"measurement": None},
    ),
)
def test_invalid_cell_addresses_cannot_enter_registry_provenance(
    bolt_group, schedule_bbox, fields
):
    plan = compile_dimensions(_model(bolt_group, schedule_bbox, parameters=("bore.diameter",)))
    registry, _table, (cell,) = _registered_table(plan)
    before = registry.snapshot()
    with pytest.raises(ValueError, match="measurement cell requires"):
        replace(cell, **fields)
    assert registry.snapshot() == before


@pytest.mark.parametrize(
    "damage", ("unknown-schedule", "row-outside", "column-outside", "no-content")
)
def test_unresolvable_cell_address_never_reuses_table_numbers(bolt_group, schedule_bbox, damage):
    from draftwright.linting.schedule_evidence import verified_schedule_registry

    plan = compile_dimensions(_model(bolt_group, schedule_bbox, parameters=("bore.diameter",)))
    registry, table, (cell,) = _registered_table(plan)
    if damage == "no-content":
        table.table_rows = None
    else:
        fields = {
            "unknown-schedule": {"schedule": "absent"},
            "row-outside": {"row": len(table.table_rows)},
            "column-outside": {"column": len(table.table_rows[cell.row])},
        }[damage]
        cell = replace(cell, **fields)
        registry.add(table, "bolts", None, cells=(cell,))
    outcomes = verify_measurement_claims(registry, plan)
    assert outcomes and all(row.state != "confirmed" for row in outcomes)
    assert any(row.cell == (cell.row, cell.column) for row in outcomes)
    verified = verified_schedule_registry(registry, plan)
    assert not verified.cells_of("bolts")
    assert not verified.measurement_of("bolts")
