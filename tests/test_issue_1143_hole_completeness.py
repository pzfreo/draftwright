"""Regression coverage for hole-family semantic outcomes (#1143)."""

import itertools
import time
from collections import Counter
from dataclasses import replace

import pytest
from build123d import Align, Box, Cone, Cylinder, Pos, Rot

from draftwright import Sheet, build_drawing
from draftwright.linting.hole_coverage import hole_requirement_outcomes
from draftwright.linting.issues import LintIssue
from draftwright.model.compiled import compile_dimensions
from draftwright.model.declare import hole as declare_hole
from draftwright.model.declare import pattern as declare_pattern
from draftwright.model.ir import Frame, StepFeature
from draftwright.recognition import HoleRecord

_XYZ_MIN = (Align.CENTER, Align.CENTER, Align.MIN)


def _pattern_and_central_bore():
    part = Box(80, 80, 10, align=_XYZ_MIN)
    for x, y, diameter in (
        (25, 0, 6),
        (0, 25, 6),
        (-25, 0, 6),
        (0, -25, 6),
        (0, 0, 20),
    ):
        part -= Pos(x, y, 0) * Cylinder(diameter / 2, 10, align=_XYZ_MIN)
    return part


def _single_hole():
    return Box(80, 50, 10, align=_XYZ_MIN) - Pos(12, 7, 0) * Cylinder(4, 10, align=_XYZ_MIN)


def _two_scattered_same_spec_holes():
    part = Box(80, 60, 10, align=_XYZ_MIN)
    part -= Pos(-20, -10, 0) * Cylinder(4, 10, align=_XYZ_MIN)
    part -= Pos(15, 12, 0) * Cylinder(4, 10, align=_XYZ_MIN)
    return part


def _blind_hole():
    return Box(80, 50, 20, align=_XYZ_MIN) - Pos(12, 7, 12) * Cylinder(4, 8, align=_XYZ_MIN)


def _opposed_blind_holes():
    xyz_min = (Align.MIN, Align.MIN, Align.MIN)
    part = Box(40, 40, 20, align=xyz_min)
    part -= Pos(20, 20, 0) * Cylinder(3, 5, align=xyz_min)
    part -= Pos(20, 20, 15) * Cylinder(3, 5, align=xyz_min)
    return part


def _opposed_blind_patterns():
    xyz_min = (Align.CENTER, Align.CENTER, Align.MIN)
    plate = Box(100, 60, 20, align=xyz_min)
    part = plate
    for z in (0, 15):
        for x in (-25, 0, 25):
            part -= Pos(x, 10, z) * Cylinder(3, 5, align=xyz_min)
    return part


def _partial_lower_group_with_opposed_blind_hole():
    xyz_min = (Align.MIN, Align.MIN, Align.MIN)
    part = Box(40, 40, 20, align=xyz_min)
    cutter_align = (Align.CENTER, Align.CENTER, Align.MIN)
    for x, z in ((10, 0), (30, 0), (10, 15)):
        part -= Pos(x, 20, z) * Cylinder(3, 5, align=cutter_align)
    return part


def _linear_pattern():
    part = Box(100, 60, 10, align=_XYZ_MIN)
    for x in (-25, 0, 25):
        part -= Pos(x, 10, 0) * Cylinder(3, 10, align=_XYZ_MIN)
    return part


def _declared_model(part, feature):
    detected = build_drawing(part, auto_dims=False).model()
    retained = [
        candidate
        for candidate in detected.features
        if getattr(candidate, "kind", None) not in {"hole", "pattern"}
    ]
    return replace(detected, features=[feature, *retained])


def _grid_pattern():
    part = Box(120, 80, 10, align=_XYZ_MIN)
    for x in (-25, 0, 25):
        for y in (-15, 15):
            part -= Pos(x, y, 0) * Cylinder(3, 10, align=_XYZ_MIN)
    return part


def _off_axis_linear_pattern():
    part = Box(20, 80, 40)
    for y in (-25, 0, 25):
        part -= Pos(0, y, 6) * Rot(0, 90, 0) * Cylinder(3, 20)
    return part


def _two_scattered_off_axis_holes():
    part = Box(20, 80, 40)
    for y, z in ((-20, -8), (15, 12)):
        part -= Pos(0, y, z) * Rot(0, 90, 0) * Cylinder(3, 20)
    return part


def _countersunk_pattern():
    part = Box(90, 60, 12)
    for x, y in ((-30, -15), (5, 12), (30, -8)):
        part -= Pos(x, y, 0) * Cylinder(3, 12)
        part -= Pos(x, y, 4) * Cone(3, 7, 4)
    return part


def _dense_scattered_plate():
    part = Box(90, 60, 12)
    columns = [-40 + i * 20 for i in range(5)]
    for i, (column, y) in enumerate(itertools.product(range(5), (-18, -6, 6, 18))):
        part -= Pos(columns[column], y, 0) * Cylinder(1.0 + i * 0.2, 20)
    return part


def _dense_scattered_blind_plate():
    part = Box(90, 60, 12, align=_XYZ_MIN)
    columns = [-40 + i * 20 for i in range(5)]
    for i, (column, y) in enumerate(itertools.product(range(5), (-18, -6, 6, 18))):
        part -= Pos(columns[column], y, 6) * Cylinder(1.0 + i * 0.2, 6, align=_XYZ_MIN)
    return part


def _two_face_countersunk_hole():
    return (
        Box(50, 50, 12)
        - Cylinder(3, 12)
        - Pos(0, 0, 4) * Cone(3, 7, 4)
        - Pos(0, 0, -4) * Cone(7, 3, 4)
    )


def _external_stepped_shaft_with_conical_transition():
    return (
        Cylinder(3, 5, align=_XYZ_MIN)
        + Pos(0, 0, 5) * Cone(3, 7, 4, align=_XYZ_MIN)
        + Pos(0, 0, 9) * Cylinder(7, 5, align=_XYZ_MIN)
    )


def _stepped_x_shaft_with_coaxial_and_offset_bores():
    left = Pos(-42.5, 0, 0) * Rot(0, 90, 0) * Cylinder(9, 25)
    middle = Pos(-10, 0, 0) * Rot(0, 90, 0) * Cylinder(15, 40)
    right = Pos(25, 0, 0) * Rot(0, 90, 0) * Cylinder(11, 30)
    central = Pos(-10, 0, 0) * Rot(0, 90, 0) * Cylinder(2, 100)
    offset = Pos(-10, 5, 0) * Rot(0, 90, 0) * Cylinder(2, 100)
    return (left + middle + right) - central - offset


def _outcomes(drawing):
    drawing.lint()
    return hole_requirement_outcomes(
        drawing.recognition(),
        drawing.model().features,
        drawing.registry,
        compile_dimensions(drawing.model()).diagnostics,
    )


def _completeness(drawing):
    return drawing.lint_summary()["quality"]["completeness"]


def test_pattern_and_central_bore_have_a_complete_recognition_owned_ledger():
    drawing = build_drawing(_pattern_and_central_bore())
    outcomes = _outcomes(drawing)
    completeness = _completeness(drawing)

    assert {(item.source_kind, item.member_count) for item in outcomes} == {
        ("hole", 1),
        ("hole_pattern", 4),
    }
    assert {item.parameter_id for item in outcomes if item.source_kind == "hole_pattern"} == {
        "bore.diameter",
        "bore.through",
        "bolt_circle.diameter",
        "grouping.count",
        "location_pattern.location.x",
        "location_pattern.location.y",
    }
    assert all(item.state == "placed" for item in outcomes)
    assert completeness["audited_score"] == 1.0
    assert completeness["requirements"] == completeness["placed"] == 10
    assert completeness["by_family"] == {
        "channels": 0,
        "flats": 0,
        "holes": 4,
        "hole_patterns": 6,
        "polygonal_stock": 0,
        "slots": 0,
        "slot_patterns": 0,
    }
    assert "holes" not in completeness["unscored_recognized_families"]
    assert "hole_patterns" not in completeness["unscored_recognized_families"]
    assert not [issue for issue in drawing.lint() if issue.code.startswith("hole_requirement_")]
    pattern_callout = next(
        name
        for name in drawing.annotations()
        if name.startswith("hc_") and drawing.get_annotation(name).label.startswith("4×")
    )
    assert {key["parameter_id"] for key in drawing.measurement_keys(pattern_callout)} == {
        "bolt_circle.diameter",
        "bore.diameter",
    }


def test_shared_location_marks_retain_the_pattern_and_central_bore_axis_identities():
    drawing = build_drawing(_pattern_and_central_bore())

    x_keys = drawing.measurement_keys("m_locx0")
    y_keys = drawing.measurement_keys("m_locy0")
    assert {key["parameter_id"] for key in x_keys} == {
        "location.location.x",
        "location_pattern.location.x",
    }
    assert {key["parameter_id"] for key in y_keys} == {
        "location.location.y",
        "location_pattern.location.y",
    }
    assert len({key["feature"] for key in x_keys}) == 2
    assert len({key["feature"] for key in y_keys}) == 2


def test_blind_depth_and_linear_pitch_have_exact_placed_outcomes():
    blind = build_drawing(_blind_hole(), page="A3")
    assert {item.parameter_id for item in _outcomes(blind)} == {
        "bore.diameter",
        "bore.depth",
        "location.location.x",
        "location.location.y",
    }
    assert all(item.state == "placed" for item in _outcomes(blind))
    callout = next(name for name in blind.annotations() if name.startswith("hc_"))
    assert {key["parameter_id"] for key in blind.measurement_keys(callout)} == {
        "bore.diameter",
        "bore.depth",
    }

    pattern = build_drawing(_linear_pattern(), page="A3")
    outcomes = _outcomes(pattern)
    assert {item.parameter_id for item in outcomes} == {
        "bore.diameter",
        "bore.through",
        "grouping.count",
        "pitch.length",
        "location_pattern.location.x",
        "location_pattern.location.y",
    }
    assert all(item.state == "placed" for item in outcomes)
    pitch = next(name for name in pattern.annotations() if name.startswith("dim_pitch_"))
    assert [key["parameter_id"] for key in pattern.measurement_keys(pitch)] == ["pitch.length"]


def test_opposite_face_blind_holes_remain_two_physical_requirement_groups():
    drawing = build_drawing(_opposed_blind_holes(), page="A3")
    outcomes = _outcomes(drawing)

    assert len([item for item in outcomes if item.parameter_id == "bore.diameter"]) == 2
    assert len([item for item in outcomes if item.parameter_id == "bore.depth"]) == 2
    assert not [item for item in outcomes if item.parameter_id == "grouping.count"]
    assert len(outcomes) == 8  # diameter + depth + X/Y location for each physical bore
    assert all(item.state == "placed" for item in outcomes)
    callouts = [name for name in drawing.annotations() if name.startswith("hc_")]
    assert len(callouts) == 2
    assert all(not drawing.get_annotation(name).label.startswith("2×") for name in callouts)
    assert _completeness(drawing)["requirements"] == 8


def test_one_declared_blind_hole_cannot_certify_two_opposed_physical_bores():
    part = _opposed_blind_holes()
    detected = build_drawing(part, page="A3").model()
    holes = [feature for feature in detected.features if feature.kind == "hole"]
    assert len(holes) == 2
    retained = [
        feature for feature in detected.features if feature.kind not in {"hole", "pattern"}
    ]
    declared = replace(detected, features=[holes[0], *retained])

    drawing = build_drawing(part, model=declared, page="A3")
    outcomes = _outcomes(drawing)
    assert len([item for item in outcomes if item.state == "placed"]) == 4
    unverifiable = [item for item in outcomes if item.state == "unverifiable"]
    assert len(unverifiable) == 1
    assert unverifiable[0].requirement_count == 4
    completeness = _completeness(drawing)
    assert completeness["requirements"] == 8
    assert completeness["placed"] == 4
    assert completeness["unverifiable"] == 4


def test_exact_blind_hole_match_disambiguates_residual_tool_centre_match():
    part = _opposed_blind_holes()
    detected = build_drawing(part, page="A3").model()
    holes = [feature for feature in detected.features if feature.kind == "hole"]
    retained = [
        feature for feature in detected.features if feature.kind not in {"hole", "pattern"}
    ]
    exact, tool_centred = holes
    x, y, z = tool_centred.frame.origin
    centred_at = (x, y, z - tool_centred.depth / 2)
    tool_centred = replace(
        tool_centred,
        frame=replace(tool_centred.frame, origin=centred_at),
        members=(centred_at,),
    )
    declared = replace(detected, features=[exact, tool_centred, *retained])

    drawing = build_drawing(part, model=declared, page="A3")
    outcomes = _outcomes(drawing)
    assert len(outcomes) == 8
    assert all(item.state == "placed" for item in outcomes)
    assert _completeness(drawing)["audited_score"] == 1.0


def test_same_face_tool_centre_cannot_certify_omitted_opposed_blind_bore():
    part = _opposed_blind_holes()
    detected = build_drawing(part, page="A3").model()
    holes = sorted(
        (feature for feature in detected.features if feature.kind == "hole"),
        key=lambda feature: feature.frame.origin[2],
    )
    lower = holes[0]
    x, y, z = lower.frame.origin
    cutter_centre = (x, y, z + lower.depth / 2)
    duplicate_lower = replace(
        lower,
        frame=replace(lower.frame, origin=cutter_centre),
        members=(cutter_centre,),
    )
    retained = [
        feature for feature in detected.features if feature.kind not in {"hole", "pattern"}
    ]
    declared = replace(detected, features=[lower, duplicate_lower, *retained])

    drawing = build_drawing(part, model=declared, page="A3")
    outcomes = _outcomes(drawing)
    assert len([item for item in outcomes if item.state == "placed"]) == 4
    unverifiable = [item for item in outcomes if item.state == "unverifiable"]
    assert len(unverifiable) == 1
    assert unverifiable[0].requirement_count == 4
    assert _completeness(drawing)["audited_score"] == 0.5


def test_extra_exact_owner_cannot_be_reused_for_omitted_opposed_blind_bore():
    part = _partial_lower_group_with_opposed_blind_hole()
    detected = build_drawing(part, page="A3").model()
    holes = [feature for feature in detected.features if feature.kind == "hole"]
    lower = next(feature for feature in holes if feature.count == 2)
    retained = [
        feature for feature in detected.features if feature.kind not in {"hole", "pattern"}
    ]
    duplicate_member = replace(
        lower,
        frame=replace(lower.frame, origin=lower.members[0]),
        count=1,
        members=(lower.members[0],),
    )
    declared = replace(detected, features=[lower, duplicate_member, *retained])

    drawing = build_drawing(part, model=declared, page="A3")
    completeness = _completeness(drawing)
    assert completeness["requirements"] == 9
    assert completeness["placed"] == 0
    assert completeness["unverifiable"] == 9
    assert completeness["audited_score"] == 0.0


def test_blind_hole_with_unknown_declared_depth_fails_closed_without_crashing():
    part = _blind_hole()
    detected = build_drawing(part, auto_dims=False).model()
    hole = next(feature for feature in detected.features if feature.kind == "hole")
    declared = _declared_model(part, replace(hole, depth=None))

    drawing = build_drawing(part, model=declared, auto_dims=False)
    completeness = drawing.lint_summary()["quality"]["completeness"]
    unverifiable = [item for item in _outcomes(drawing) if item.state == "unverifiable"]
    assert len(unverifiable) == 1
    assert unverifiable[0].requirement_count == 4
    assert completeness["requirements"] == completeness["unverifiable"] == 4


def test_unique_declared_blind_tool_center_corresponds_to_its_recognised_opening():
    xyz_min = (Align.CENTER, Align.CENTER, Align.MIN)
    plate = Box(80, 50, 20, align=xyz_min)
    tool = Pos(12, 7, 12) * Cylinder(4, 8, align=xyz_min)
    sheet = Sheet(plate - tool).auto_dimensions()
    sheet.hole(tool).depth(8)
    sheet.envelope()

    drawing = sheet.build()
    outcomes = _outcomes(drawing)
    assert {item.parameter_id for item in outcomes} == {
        "bore.diameter",
        "bore.depth",
        "location.location.x",
        "location.location.y",
    }
    assert all(item.state == "placed" for item in outcomes)


def test_separate_declared_blind_tools_cover_one_physical_loose_group():
    plate = Box(80, 50, 20, align=_XYZ_MIN)
    tools = [Pos(x, 7, 12) * Cylinder(4, 8, align=_XYZ_MIN) for x in (-15, 15)]
    part = plate
    for tool in tools:
        part -= tool
    sheet = Sheet(part).auto_dimensions()
    for tool in tools:
        sheet.hole(tool).depth(8)
    sheet.envelope()

    drawing = sheet.build()
    outcomes = _outcomes(drawing)
    assert {item.parameter_id for item in outcomes} == {
        "bore.depth",
        "bore.diameter",
        "grouping.count",
        "location.location.x",
        "location.location.y",
    }
    assert all(item.state == "placed" for item in outcomes)
    assert _completeness(drawing)["audited_score"] == 1.0


def test_duplicate_exact_grouped_hole_owners_fail_closed():
    part = _two_scattered_same_spec_holes()
    detected = build_drawing(part, page="A3").model()
    grouped = next(feature for feature in detected.features if feature.kind == "hole")
    retained = [feature for feature in detected.features if feature is not grouped]
    declared = replace(detected, features=[grouped, replace(grouped), *retained])

    drawing = build_drawing(part, model=declared, page="A3")
    outcomes = _outcomes(drawing)
    assert len(outcomes) == 1
    assert outcomes[0].state == "unverifiable"
    assert outcomes[0].requirement_count == 5
    assert _completeness(drawing)["audited_score"] == 0.0


def test_grouped_and_per_member_exact_owners_are_an_ambiguous_cover():
    part = _two_scattered_same_spec_holes()
    detected = build_drawing(part, page="A3").model()
    grouped = next(feature for feature in detected.features if feature.kind == "hole")
    separate = [
        declare_hole(diameter=grouped.diameter, at=member, axis="z", through=True)
        for member in grouped.members
    ]
    retained = [feature for feature in detected.features if feature is not grouped]
    declared = replace(detected, features=[grouped, *separate, *retained])

    drawing = build_drawing(part, model=declared, page="A3")
    outcomes = _outcomes(drawing)
    assert len(outcomes) == 1
    assert outcomes[0].state == "unverifiable"
    assert outcomes[0].requirement_count == 5
    assert _completeness(drawing)["audited_score"] == 0.0


def test_dense_separate_blind_tool_correspondence_scales_linearly():
    baseline = build_drawing(_blind_hole(), auto_dims=False)

    def elapsed_for(count):
        physical = tuple(
            HoleRecord(
                axis=(0.0, 0.0, 1.0),
                location=(float(index), 0.0, 0.0),
                diameter=8.0,
                depth=8.0,
                bottom="flat",
            )
            for index in range(count)
        )
        recognition = replace(
            baseline.recognition(), holes=physical, hole_patterns=(), countersinks=()
        )
        singletons = tuple(
            declare_hole(
                diameter=8,
                at=(float(index), 0.0, 4.0),
                axis="z",
                through=False,
                depth=8,
            )
            for index in range(2, count)
        )
        grouped = replace(
            declare_hole(
                diameter=8,
                at=(0.0, 0.0, 4.0),
                axis="z",
                through=False,
                depth=8,
            ),
            count=2,
            members=((0.0, 0.0, 4.0), (1.0, 0.0, 4.0)),
        )
        started = time.perf_counter()
        outcomes = hole_requirement_outcomes(
            recognition, (grouped, *singletons), baseline.registry
        )
        return time.perf_counter() - started, outcomes

    small_elapsed, small = elapsed_for(1_000)
    large_elapsed, large = elapsed_for(2_000)

    assert len(small) == len(large) == 5
    assert not [item for item in (*small, *large) if item.state == "unverifiable"]
    assert large_elapsed < 2.0
    assert large_elapsed < small_elapsed * 3.0


def test_overlapping_owner_chain_fails_closed_without_quadratic_rescans(monkeypatch):
    import draftwright.linting.hole_coverage as hole_coverage_module

    baseline = build_drawing(_blind_hole(), auto_dims=False)
    item_visits = 0

    class CountingCounter(Counter):
        def items(self):
            nonlocal item_visits
            for item in super().items():
                item_visits += 1
                yield item

    monkeypatch.setattr(hole_coverage_module, "Counter", CountingCounter)

    def elapsed_for(count):
        physical = tuple(
            HoleRecord(
                axis=(0.0, 0.0, 1.0),
                location=(float(index), 0.0, 0.0),
                diameter=8.0,
                depth=8.0,
                bottom="flat",
            )
            for index in range(count)
        )
        recognition = replace(
            baseline.recognition(), holes=physical, hole_patterns=(), countersinks=()
        )
        template = declare_hole(diameter=8, at=(0, 0, 0), axis="z", through=False, depth=8)
        owners = tuple(
            replace(
                template,
                frame=replace(template.frame, origin=(float(index), 0.0, 0.0)),
                count=2,
                members=(
                    (float(index), 0.0, 0.0),
                    (float(index + 1), 0.0, 0.0),
                ),
            )
            for index in range(count - 1)
        ) + (replace(template, frame=replace(template.frame, origin=(0.0, 0.0, 0.0))),)
        started = time.perf_counter()
        outcomes = hole_requirement_outcomes(recognition, owners, baseline.registry)
        return time.perf_counter() - started, outcomes

    _small_elapsed, small = elapsed_for(1_000)
    item_visits = 0
    large_elapsed, large = elapsed_for(2_000)

    assert small[0].state == large[0].state == "unverifiable"
    assert large_elapsed < 2.0
    assert item_visits < 20_000


def test_many_distinct_hole_specs_do_not_cross_scan_the_ir_inventory(monkeypatch):
    import draftwright.linting.hole_coverage as hole_coverage_module

    baseline = build_drawing(_blind_hole(), auto_dims=False)
    feature_spec_calls = 0
    original_feature_spec = hole_coverage_module._feature_spec

    def counted_feature_spec(feature):
        nonlocal feature_spec_calls
        feature_spec_calls += 1
        return original_feature_spec(feature)

    monkeypatch.setattr(hole_coverage_module, "_feature_spec", counted_feature_spec)

    def elapsed_for(count):
        physical = tuple(
            HoleRecord(
                axis=(0.0, 0.0, 1.0),
                location=(float(index), 0.0, 0.0),
                diameter=float(index + 1),
                depth=8.0,
                bottom="flat",
            )
            for index in range(count)
        )
        recognition = replace(
            baseline.recognition(), holes=physical, hole_patterns=(), countersinks=()
        )
        owners = tuple(
            declare_hole(
                diameter=float(index + 1),
                at=(float(index), 0.0, 0.0),
                axis="z",
                through=False,
                depth=8,
            )
            for index in range(count)
        )
        started = time.perf_counter()
        outcomes = hole_requirement_outcomes(recognition, owners, baseline.registry)
        return time.perf_counter() - started, outcomes

    _small_elapsed, small = elapsed_for(1_000)
    feature_spec_calls = 0
    large_elapsed, large = elapsed_for(2_000)

    assert len(small) == 4_000
    assert len(large) == 8_000
    assert large_elapsed < 2.0
    assert feature_spec_calls < 20_000


def test_overlapping_declared_covers_fail_closed_in_bounded_time():
    baseline = build_drawing(_blind_hole(), auto_dims=False)
    count = 15
    physical = tuple(
        HoleRecord(
            axis=(0.0, 0.0, 1.0),
            location=(float(index), 0.0, 0.0),
            diameter=8.0,
            depth=8.0,
            bottom="flat",
        )
        for index in range(count)
    )
    recognition = replace(
        baseline.recognition(), holes=physical, hole_patterns=(), countersinks=()
    )
    template = declare_hole(
        diameter=8,
        at=(0.0, 0.0, 4.0),
        axis="z",
        through=False,
        depth=8,
    )
    features = tuple(
        replace(
            template,
            frame=replace(template.frame, origin=(float(left), 0.0, 4.0)),
            count=2,
            members=((float(left), 0.0, 4.0), (float(right), 0.0, 4.0)),
        )
        for left in range(count)
        for right in range(left + 1, count)
    )

    started = time.perf_counter()
    outcomes = hole_requirement_outcomes(recognition, features, baseline.registry)
    elapsed = time.perf_counter() - started

    assert len(outcomes) == 1
    assert outcomes[0].state == "unverifiable"
    assert outcomes[0].requirement_count == 5
    assert elapsed < 2.0


def test_grid_pattern_accounts_for_both_independent_pitch_measurements():
    drawing = build_drawing(_grid_pattern(), page="A3")
    outcomes = _outcomes(drawing)

    assert {item.parameter_id for item in outcomes} == {
        "bore.diameter",
        "bore.through",
        "grid_pitch.length.col",
        "grid_pitch.length.row",
        "grouping.count",
        "location_pattern.location.x",
        "location_pattern.location.y",
    }
    assert all(item.state == "placed" for item in outcomes)
    pitch_ids = {
        key["parameter_id"]
        for name in drawing.annotations()
        if name.startswith("dim_pitch_")
        for key in drawing.measurement_keys(name)
    }
    assert pitch_ids == {"grid_pitch.length.col", "grid_pitch.length.row"}


def test_transposed_grid_declaration_corresponds_to_the_same_physical_lattice():
    part = _grid_pattern()
    detected = build_drawing(part, page="A3").model()
    features = []
    for feature in detected.features:
        if getattr(feature, "pattern", None) == "grid":
            assert (feature.rows, feature.cols, feature.grid, feature.angle) == (
                2,
                3,
                (30.0, 25.0),
                0.0,
            )
            feature = replace(feature, rows=3, cols=2, grid=(25.0, 30.0), angle=90.0)
        features.append(feature)

    drawing = build_drawing(part, model=replace(detected, features=features), page="A3")
    outcomes = _outcomes(drawing)
    assert len(outcomes) == 7
    assert all(item.state == "placed" for item in outcomes)
    assert _completeness(drawing)["audited_score"] == 1.0


def test_inconsistent_grid_definition_cannot_be_certified_from_member_points_alone():
    part = _grid_pattern()
    detected = build_drawing(part, page="A3").model()
    features = [
        replace(feature, grid=(30.0, 26.0))
        if getattr(feature, "pattern", None) == "grid"
        else feature
        for feature in detected.features
    ]

    drawing = build_drawing(part, model=replace(detected, features=features), page="A3")
    unverifiable = [item for item in _outcomes(drawing) if item.state == "unverifiable"]
    assert len(unverifiable) == 1
    assert unverifiable[0].requirement_count == 7


def test_linear_pattern_correspondence_treats_opposite_directions_as_the_same_axis():
    part = _linear_pattern()
    detected = build_drawing(part, page="A3").model()
    features = []
    for feature in detected.features:
        if getattr(feature, "pattern", None) == "linear":
            feature = replace(feature, direction=tuple(-value for value in feature.direction))
        features.append(feature)
    declared = replace(detected, features=features)

    drawing = build_drawing(part, model=declared, page="A3")
    assert all(item.state == "placed" for item in _outcomes(drawing))


@pytest.mark.parametrize(
    ("part", "feature"),
    [
        (
            _linear_pattern(),
            declare_pattern(
                declare_hole(diameter=6, at=(0, 10, 0), axis="z"),
                kind="linear",
                count=3,
                at=(0, 10, 0),
                pitch=25,
            ),
        ),
        (
            _grid_pattern(),
            declare_pattern(
                declare_hole(diameter=6, at=(0, 0, 0), axis="z"),
                kind="grid",
                count=6,
                at=(0, 0, 0),
                grid=(30, 25),
                rows=2,
                cols=3,
            ),
        ),
    ],
    ids=("linear-default-direction", "grid-default-angle"),
)
def test_declared_pattern_defaults_correspond_to_recognition(part, feature):
    drawing = build_drawing(part, model=_declared_model(part, feature), page="A3")

    outcomes = _outcomes(drawing)
    assert outcomes
    assert all(item.state == "placed" for item in outcomes)
    assert _completeness(drawing)["audited_score"] == 1.0


def test_declared_blind_pattern_tool_centres_correspond_to_recognised_openings():
    plate = Box(100, 60, 20, align=_XYZ_MIN)
    tools = [Pos(x, 10, 12) * Cylinder(3, 8, align=_XYZ_MIN) for x in (-25, 0, 25)]
    part = plate
    for tool in tools:
        part -= tool
    member = declare_hole(tools[1], through=False, depth=8)
    feature = declare_pattern(
        member,
        kind="linear",
        count=3,
        at=member.frame.origin,
        pitch=25,
        direction=(1, 0, 0),
    )

    drawing = build_drawing(part, model=_declared_model(part, feature), page="A3")
    outcomes = _outcomes(drawing)
    assert {item.parameter_id for item in outcomes} == {
        "bore.depth",
        "bore.diameter",
        "grouping.count",
        "location_pattern.location.x",
        "location_pattern.location.y",
        "pitch.length",
    }
    assert all(item.state == "placed" for item in outcomes)


def test_one_declared_blind_pattern_cannot_certify_two_opposed_physical_patterns():
    part = _opposed_blind_patterns()
    detected = build_drawing(part, page="A3").model()
    patterns = [feature for feature in detected.features if feature.kind == "pattern"]
    assert len(patterns) == 2
    retained = [
        feature for feature in detected.features if feature.kind not in {"hole", "pattern"}
    ]
    declared = replace(detected, features=[patterns[0], *retained])

    drawing = build_drawing(part, model=declared, page="A3")
    outcomes = _outcomes(drawing)
    assert len([item for item in outcomes if item.state == "placed"]) == 6
    unverifiable = [item for item in outcomes if item.state == "unverifiable"]
    assert len(unverifiable) == 1
    assert unverifiable[0].requirement_count == 6
    completeness = _completeness(drawing)
    assert completeness["requirements"] == 12
    assert completeness["placed"] == 6
    assert completeness["unverifiable"] == 6


def test_exact_blind_pattern_match_disambiguates_residual_tool_centres():
    part = _opposed_blind_patterns()
    detected = build_drawing(part, page="A3").model()
    patterns = [feature for feature in detected.features if feature.kind == "pattern"]
    retained = [
        feature for feature in detected.features if feature.kind not in {"hole", "pattern"}
    ]
    exact, tool_centred = patterns
    shift = -tool_centred.member.depth / 2

    def shifted(point):
        return (point[0], point[1], point[2] + shift)

    tool_centred = replace(
        tool_centred,
        frame=replace(tool_centred.frame, origin=shifted(tool_centred.frame.origin)),
        member=replace(
            tool_centred.member,
            frame=replace(
                tool_centred.member.frame,
                origin=shifted(tool_centred.member.frame.origin),
            ),
        ),
        members=tuple(shifted(point) for point in tool_centred.members),
    )
    declared = replace(detected, features=[exact, tool_centred, *retained])

    drawing = build_drawing(part, model=declared, page="A3")
    outcomes = _outcomes(drawing)
    assert len(outcomes) == 12
    assert all(item.state == "placed" for item in outcomes)
    assert _completeness(drawing)["audited_score"] == 1.0


def test_same_face_pattern_tool_centres_cannot_certify_omitted_opposed_pattern():
    part = _opposed_blind_patterns()
    detected = build_drawing(part, page="A3").model()
    patterns = sorted(
        (feature for feature in detected.features if feature.kind == "pattern"),
        key=lambda feature: feature.frame.origin[2],
    )
    lower = patterns[0]
    shift = lower.member.depth / 2

    def shifted(point):
        return (point[0], point[1], point[2] + shift)

    duplicate_lower = replace(
        lower,
        frame=replace(lower.frame, origin=shifted(lower.frame.origin)),
        member=replace(
            lower.member,
            frame=replace(lower.member.frame, origin=shifted(lower.member.frame.origin)),
        ),
        members=tuple(shifted(point) for point in lower.members),
    )
    retained = [
        feature for feature in detected.features if feature.kind not in {"hole", "pattern"}
    ]
    declared = replace(detected, features=[lower, duplicate_lower, *retained])

    drawing = build_drawing(part, model=declared, page="A3")
    outcomes = _outcomes(drawing)
    assert len([item for item in outcomes if item.state == "placed"]) == 6
    unverifiable = [item for item in outcomes if item.state == "unverifiable"]
    assert len(unverifiable) == 1
    assert unverifiable[0].requirement_count == 6
    assert _completeness(drawing)["audited_score"] == 0.5


def test_off_axis_pattern_keeps_absolute_location_requirements_fail_closed():
    drawing = build_drawing(_off_axis_linear_pattern(), page="A3")
    outcomes = {item.parameter_id: item.state for item in _outcomes(drawing)}

    assert outcomes == {
        "bore.diameter": "placed",
        "bore.through": "placed",
        "grouping.count": "placed",
        "pitch.length": "placed",
        "location_pattern.location.y": "missing",
        "location_pattern.location.z": "missing",
    }
    completeness = _completeness(drawing)
    assert completeness["requirements"] == 6
    assert completeness["audited_score"] == pytest.approx(4 / 6)


def test_compound_countersink_callout_accounts_for_every_printed_measurement():
    drawing = build_drawing(_countersunk_pattern(), page="A3")
    outcomes = _outcomes(drawing)

    assert {item.parameter_id for item in outcomes} == {
        "bore.diameter",
        "bore.through",
        "countersink.angle",
        "countersink.diameter",
        "grouping.count",
        "location.location.x",
        "location.location.y",
    }
    assert all(item.state == "placed" for item in outcomes)
    callout = next(name for name in drawing.annotations() if name.startswith("hc_"))
    assert {key["parameter_id"] for key in drawing.measurement_keys(callout)} == {
        "bore.diameter",
        "countersink.angle",
        "countersink.diameter",
    }


def test_cross_hole_locations_retain_both_off_axis_measurement_identities():
    part = Box(12, 40, 30) - Pos(0, 8, 6) * Rot(0, 90, 0) * Cylinder(3, 12)
    drawing = build_drawing(part)

    outcomes = _outcomes(drawing)
    assert {item.parameter_id for item in outcomes} == {
        "bore.diameter",
        "bore.through",
        "location_off_axis.y",
        "location_off_axis.z",
    }
    assert all(item.state == "placed" for item in outcomes)
    placed_location_ids = {
        key["parameter_id"]
        for name in drawing.annotations()
        for key in drawing.measurement_keys(name)
        if key["parameter_id"].startswith("location_off_axis.")
    }
    assert placed_location_ids == {"location_off_axis.y", "location_off_axis.z"}


def test_grouped_off_axis_locations_require_every_physical_member_mark():
    drawing = build_drawing(_two_scattered_off_axis_holes(), page="A3")
    feature = next(feature for feature in drawing.model().features if feature.kind == "hole")
    assert feature.count == 2

    z_marks = [
        name
        for name in drawing.annotations_of(feature)
        if any(
            key["parameter_id"] == "location_off_axis.z" for key in drawing.measurement_keys(name)
        )
    ]
    assert len(z_marks) == 2
    assert all(
        len(getattr(drawing.get_annotation(name), "covers_hole_locations", ())) == 1
        for name in z_marks
    )
    assert all(item.state == "placed" for item in _outcomes(drawing))

    drawing.remove(z_marks[1])
    outcomes = {item.parameter_id: item.state for item in _outcomes(drawing)}
    assert outcomes["location_off_axis.y"] == "placed"
    assert outcomes["location_off_axis.z"] == "missing"
    assert _completeness(drawing)["audited_score"] == pytest.approx(4 / 5)


def test_mixed_coaxial_group_requires_location_evidence_for_the_offset_member():
    drawing = build_drawing(_stepped_x_shaft_with_coaxial_and_offset_bores(), page="A3")
    feature = next(
        feature
        for feature in drawing.model().features
        if feature.kind == "hole" and feature.count == 2
    )
    assert sorted(round(member[1], 3) for member in feature.members) == [0.0, 5.0]
    assert all(item.state == "placed" for item in _outcomes(drawing))

    offset_mark = next(
        name
        for name in drawing.annotations_of(feature)
        if name.startswith("m_cm")
        and any(
            owner is feature and round(member[1], 3) == 5.0
            for owner, member, _view in drawing.get_annotation(name).covers_hole_centers
        )
    )
    removed = [
        name
        for name in tuple(drawing.annotations())
        if name == offset_mark or name.startswith("dim_loc")
    ]
    assert offset_mark in removed
    assert any(name.startswith("dim_loc") for name in removed)
    for name in removed:
        drawing.remove(name)

    locations = {
        item.parameter_id: item.state
        for item in _outcomes(drawing)
        if "location" in item.parameter_id
    }
    assert locations == {
        "location_off_axis.y": "missing",
        "location_off_axis.z": "missing",
    }
    assert _completeness(drawing)["audited_score"] == pytest.approx(3 / 5)


def test_live_furniture_retains_physical_member_center_provenance():
    drawing = build_drawing(_single_hole(), auto_dims=False)
    feature = next(feature for feature in drawing.model().features if feature.kind == "hole")
    for name in tuple(drawing.annotations_of(feature)):
        drawing.remove(name)

    names = drawing.furniture(feature)
    mark = drawing.get_annotation(names[0])
    assert mark.covers_hole_centers == ((feature, feature.members[0], "plan"),)


def test_declared_step_cannot_make_plate_center_mark_replace_location_dimensions():
    part = _single_hole()
    detected = build_drawing(part).model()
    hole = next(feature for feature in detected.features if feature.kind == "hole")
    synthetic_step = StepFeature(
        frame=Frame(origin=hole.frame.origin, axis="z"),
        length=10,
        diameter=20,
        span=((12, 7, 0), (12, 7, 10)),
    )
    drawing = build_drawing(
        part, model=replace(detected, features=[*detected.features, synthetic_step])
    )
    drawing.lint()
    assert not drawing.recognition().turned_steps
    for name in tuple(drawing.annotations_of(hole)):
        if name.startswith("m_loc"):
            drawing.remove(name)

    locations = {
        item.parameter_id: item.state
        for item in _outcomes(drawing)
        if "location" in item.parameter_id
    }
    assert locations == {
        "location.location.x": "missing",
        "location.location.y": "missing",
    }


def test_coaxial_bore_centerline_accounts_for_two_physical_location_axes():
    left = Pos(-42.5, 0, 0) * Rot(0, 90, 0) * Cylinder(9, 25)
    middle = Pos(-10, 0, 0) * Rot(0, 90, 0) * Cylinder(15, 40)
    right = Pos(25, 0, 0) * Rot(0, 90, 0) * Cylinder(11, 30)
    bore = Pos(-10, 0, 0) * Rot(0, 90, 0) * Cylinder(4, 100)
    drawing = build_drawing((left + middle + right) - bore, page="A3")

    outcomes = _outcomes(drawing)
    assert {item.parameter_id for item in outcomes} == {
        "bore.diameter",
        "bore.through",
        "location_off_axis.centerline.y",
        "location_off_axis.centerline.z",
    }
    assert all(item.state == "placed" for item in outcomes)
    assert _completeness(drawing)["requirements"] == 4


def test_authored_coaxial_location_omissions_remain_suppressed_without_center_furniture():
    left = Pos(-42.5, 0, 0) * Rot(0, 90, 0) * Cylinder(9, 25)
    middle = Pos(-10, 0, 0) * Rot(0, 90, 0) * Cylinder(15, 40)
    right = Pos(25, 0, 0) * Rot(0, 90, 0) * Cylinder(11, 30)
    bore = Pos(-10, 0, 0) * Rot(0, 90, 0) * Cylinder(4, 100)
    sheet = Sheet((left + middle + right) - bore, page="A3")
    declared = sheet.hole(diameter=8, at=(40, 0, 0), axis="x").through()
    sheet.dimension(declared, "bore.diameter")
    drawing = sheet.build()
    feature = next(feature for feature in drawing.model().features if feature.kind == "hole")
    assert {
        item.parameter_id: item.state
        for item in _outcomes(drawing)
        if ".centerline." in item.parameter_id
    } == {
        "location_off_axis.centerline.y": "placed",
        "location_off_axis.centerline.z": "placed",
    }

    for name in tuple(drawing.annotations_of(feature)):
        if name.startswith("m_cm"):
            drawing.remove(name)

    assert {
        item.parameter_id: item.state
        for item in _outcomes(drawing)
        if ".centerline." in item.parameter_id
    } == {
        "location_off_axis.centerline.y": "suppressed",
        "location_off_axis.centerline.z": "suppressed",
    }


def test_wrong_view_live_center_furniture_cannot_certify_coaxial_location():
    left = Pos(-42.5, 0, 0) * Rot(0, 90, 0) * Cylinder(9, 25)
    middle = Pos(-10, 0, 0) * Rot(0, 90, 0) * Cylinder(15, 40)
    right = Pos(25, 0, 0) * Rot(0, 90, 0) * Cylinder(11, 30)
    bore = Pos(-10, 0, 0) * Rot(0, 90, 0) * Cylinder(4, 100)
    drawing = build_drawing((left + middle + right) - bore, page="A3")
    coaxial = next(feature for feature in drawing.model().features if feature.kind == "hole")
    for name in tuple(drawing.annotations_of(coaxial)):
        if name.startswith("m_cm"):
            drawing.remove(name)
    assert {item.state for item in _outcomes(drawing) if ".centerline." in item.parameter_id} == {
        "missing"
    }

    wrong_view_marks = drawing.furniture(coaxial, view="front")
    assert wrong_view_marks
    assert {item.state for item in _outcomes(drawing) if ".centerline." in item.parameter_id} == {
        "missing"
    }


def test_eccentric_longitudinal_boss_is_not_the_turned_profile_axis():
    left = Pos(-42.5, 0, 0) * Rot(0, 90, 0) * Cylinder(9, 25)
    middle = Pos(-10, 0, 0) * Rot(0, 90, 0) * Cylinder(15, 40)
    right = Pos(25, 0, 0) * Rot(0, 90, 0) * Cylinder(11, 30)
    boss = Pos(-10, 8, 0) * Rot(0, 90, 0) * Cylinder(3, 100)
    bore = Pos(-10, 8, 0) * Rot(0, 90, 0) * Cylinder(1.5, 100)
    drawing = build_drawing((left + middle + right + boss) - bore, page="A3")
    feature = next(feature for feature in drawing.model().features if feature.kind == "hole")

    assert {
        key["parameter_id"]
        for name in drawing.annotations_of(feature)
        for key in drawing.measurement_keys(name)
        if key["parameter_id"].startswith("location_off_axis.")
    } == {"location_off_axis.y", "location_off_axis.z"}
    assert {
        item.parameter_id: item.state
        for item in _outcomes(drawing)
        if "location" in item.parameter_id
    } == {"location_off_axis.y": "placed", "location_off_axis.z": "placed"}

    for name in tuple(drawing.annotations_of(feature)):
        if name.startswith("m_cm"):
            drawing.remove(name)
    assert {
        item.parameter_id: item.state
        for item in _outcomes(drawing)
        if "location" in item.parameter_id
    } == {"location_off_axis.y": "placed", "location_off_axis.z": "placed"}


def test_nearby_distinct_member_locations_are_dropped_not_semantically_merged():
    part = Box(40, 40, 5, align=_XYZ_MIN)
    for x in (5.0, 5.4):
        part -= Pos(x, 5, 0) * Cylinder(0.05, 5, align=_XYZ_MIN)
    drawing = build_drawing(part, page="A3")

    x_marks = [name for name in drawing.annotations() if name.startswith("m_locx")]
    assert x_marks == ["m_locx0"]
    mark = drawing.get_annotation(x_marks[0])
    assert mark.label == "25"
    assert {round(point[0], 1) for _measurement, point in mark.covers_hole_locations} == {5.0}
    outcomes = {item.parameter_id: item.state for item in _outcomes(drawing)}
    assert outcomes["location.location.x"] == "dropped"
    assert outcomes["location.location.y"] == "placed"


def test_live_locate_restores_member_level_location_provenance():
    drawing = build_drawing(_single_hole())
    feature = next(feature for feature in drawing.model().features if feature.kind == "hole")
    for name in tuple(drawing.annotations_of(feature)):
        if any(
            key["parameter_id"].startswith("location.location.")
            for key in drawing.measurement_keys(name)
        ):
            drawing.remove(name)
    assert {item.parameter_id for item in _outcomes(drawing) if item.state == "missing"} == {
        "location.location.x",
        "location.location.y",
    }

    names = drawing.locate(feature)
    assert len(names) == 2
    assert all(
        len(getattr(drawing.get_annotation(name), "covers_hole_locations", ())) == 1
        for name in names
    )
    assert all(item.state == "placed" for item in _outcomes(drawing))


def test_removing_a_required_callout_or_location_reduces_completeness():
    callout_drawing = build_drawing(_pattern_and_central_bore())
    central = next(
        name
        for name in callout_drawing.annotations()
        if name.startswith("hc_") and callout_drawing.get_annotation(name).label == "⌀20 THRU"
    )
    callout_drawing.remove(central)
    central_outcomes = [item for item in _outcomes(callout_drawing) if item.source_kind == "hole"]
    assert {item.parameter_id for item in central_outcomes if item.state == "missing"} == {
        "bore.diameter",
        "bore.through",
    }
    assert _completeness(callout_drawing)["audited_score"] == 0.8

    location_drawing = build_drawing(_pattern_and_central_bore())
    location_drawing.remove("m_locx0")
    missing_x = {
        (item.source_kind, item.parameter_id)
        for item in _outcomes(location_drawing)
        if item.state == "missing"
    }
    assert missing_x == {
        ("hole", "location.location.x"),
        ("hole_pattern", "location_pattern.location.x"),
    }
    assert _completeness(location_drawing)["audited_score"] == 0.8


def test_grouped_loose_holes_require_every_member_location_mark():
    drawing = build_drawing(_two_scattered_same_spec_holes(), page="A3")
    assert all(item.state == "placed" for item in _outcomes(drawing))

    x_marks = sorted(name for name in drawing.annotations() if name.startswith("m_locx"))
    y_marks = sorted(name for name in drawing.annotations() if name.startswith("m_locy"))
    assert len(x_marks) == len(y_marks) == 2
    drawing.remove(x_marks[1])
    drawing.remove(y_marks[1])

    outcomes = {item.parameter_id: item.state for item in _outcomes(drawing)}
    assert outcomes["location.location.x"] == "missing"
    assert outcomes["location.location.y"] == "missing"
    assert _completeness(drawing)["audited_score"] == pytest.approx(3 / 5)


def test_successful_hole_table_escalation_carries_every_replaced_requirement():
    drawing = build_drawing(_dense_scattered_plate())

    assert "hole_table_plan" in drawing.annotations()
    assert len(drawing.measurement_keys("hole_table_plan")) == 60
    outcomes = _outcomes(drawing)
    assert len(outcomes) == 80
    assert all(item.state == "placed" for item in outcomes)
    assert not [issue for issue in drawing.lint() if issue.code.startswith("hole_requirement_")]
    assert _completeness(drawing)["audited_score"] == 1.0


def test_public_hole_table_replaces_callout_with_semantic_bore_coverage():
    drawing = build_drawing(_single_hole(), page="A3")
    feature = next(feature for feature in drawing.model().features if feature.kind == "hole")
    for name in tuple(drawing.annotations_of(feature)):
        if name.startswith("hc_"):
            drawing.remove(name)
    assert {item.parameter_id for item in _outcomes(drawing) if item.state == "missing"} == {
        "bore.diameter",
        "bore.through",
    }

    table = drawing.add_hole_table("plan", balloons=False)

    assert table is not None
    assert {key["parameter_id"] for key in drawing.measurement_keys("hole_table_plan")} == {
        "bore.diameter"
    }
    assert table.covers_hole_requirements_by_feature == ((feature, "bore.through", 1),)
    assert all(item.state == "placed" for item in _outcomes(drawing))


def test_public_pattern_hole_table_covers_only_the_facts_it_prints():
    drawing = build_drawing(_linear_pattern(), page="A3")
    pattern = next(feature for feature in drawing.model().features if feature.kind == "pattern")
    for name in tuple(drawing.annotations_of(pattern)):
        if name.startswith(("hc_", "dim_pitch_")):
            drawing.remove(name)
    assert {item.parameter_id for item in _outcomes(drawing) if item.state == "missing"} == {
        "bore.diameter",
        "bore.through",
        "grouping.count",
        "pitch.length",
    }

    table = drawing.add_hole_table("plan", balloons=False)

    assert table is not None
    assert [key["parameter_id"] for key in drawing.measurement_keys("hole_table_plan")] == [
        "bore.diameter"
    ]
    assert table.covers_hole_requirements_by_feature == (
        (pattern, "bore.through", 1),
        (pattern, "grouping.count", 3),
    )
    assert table.covers_diameters == (6.0, 6.0, 6.0)
    outcomes = {item.parameter_id: item.state for item in _outcomes(drawing)}
    assert outcomes["bore.diameter"] == "placed"
    assert outcomes["bore.through"] == "placed"
    assert outcomes["grouping.count"] == "placed"
    assert outcomes["pitch.length"] == "missing"
    assert "feature_count_mismatch" not in {issue.code for issue in drawing.lint()}


def test_public_hole_table_explicitly_places_printed_authored_omissions():
    part = _blind_hole()
    sheet = Sheet(part)
    sheet.hole(diameter=8, at=(12, 7, 20), axis="z").depth(8)
    envelope = sheet.envelope()
    sheet.dimension(envelope, "width.length")
    drawing = sheet.build()
    before = {item.parameter_id: item.state for item in _outcomes(drawing)}
    assert before["bore.diameter"] == before["bore.depth"] == "suppressed"

    table = drawing.add_hole_table("plan", balloons=False)

    assert table is not None
    assert {key["parameter_id"] for key in drawing.measurement_keys("hole_table_plan")} == {
        "bore.diameter",
        "bore.depth",
    }
    after = {item.parameter_id: item.state for item in _outcomes(drawing)}
    assert after["bore.diameter"] == after["bore.depth"] == "placed"
    assert after["location.location.x"] == after["location.location.y"] == "suppressed"


def test_blind_hole_table_prints_every_depth_it_claims(monkeypatch):
    from draftwright.drawing import Drawing

    captured_rows = []
    original = Drawing.add_table

    def capture_rows(self, rows, **kwargs):
        captured_rows.append(tuple(tuple(cell for cell in row) for row in rows))
        return original(self, rows, **kwargs)

    monkeypatch.setattr(Drawing, "add_table", capture_rows)
    drawing = build_drawing(_dense_scattered_blind_plate(), page="A3")

    assert "hole_table_plan" in drawing.annotations()
    rows = captured_rows[-1]
    block_cols = 5
    assert all(rows[0][offset + 2] == "DEPTH" for offset in range(0, len(rows[0]), block_cols))
    assert {
        row[offset + 2]
        for row in rows[1:]
        for offset in range(0, len(row), block_cols)
        if row[offset]
    } == {"6"}
    outcomes = _outcomes(drawing)
    assert len(outcomes) == 80
    assert all(item.state == "placed" for item in outcomes)


def test_unmatched_second_face_countersink_fails_closed_in_hole_ledger():
    drawing = build_drawing(_two_face_countersunk_hole(), page="A3")
    assert len(drawing.recognition().countersinks) == 2
    assert sum(hole.csink is not None for hole in drawing.recognition().holes) == 1

    unverifiable = [item for item in _outcomes(drawing) if item.state == "unverifiable"]
    assert {item.parameter_id for item in unverifiable} == {
        "countersink.angle",
        "countersink.diameter",
    }
    completeness = _completeness(drawing)
    assert completeness["requirements"] == 8
    assert completeness["placed"] == 6
    assert completeness["unverifiable"] == 2
    assert completeness["audited_score"] == pytest.approx(0.75)
    assert [
        issue.code for issue in drawing.lint() if issue.code == "hole_requirement_unverifiable"
    ] == ["hole_requirement_unverifiable"] * 2


def test_unattached_external_countersink_false_positive_is_not_a_hole_requirement():
    drawing = build_drawing(_external_stepped_shaft_with_conical_transition(), auto_dims=False)
    assert not drawing.recognition().holes
    assert len(drawing.recognition().countersinks) == 1

    assert _outcomes(drawing) == []
    assert not [issue for issue in drawing.lint() if issue.code.startswith("hole_requirement_")]
    completeness = _completeness(drawing)
    assert completeness["by_family"]["holes"] == 0


def test_failed_hole_table_escalation_restores_semantic_fallback_evidence(monkeypatch):
    import draftwright.drawing as drawing_module

    monkeypatch.setattr(drawing_module, "fit_box", lambda *_args, **_kwargs: None)
    drawing = build_drawing(_dense_scattered_plate())

    assert "hole_table_plan" not in drawing.annotations()
    assert "table_dropped" in {issue.code for issue in drawing.lint()}
    outcomes = _outcomes(drawing)
    assert not [item for item in outcomes if item.state == "missing"]
    assert {item.state for item in outcomes} <= {"placed", "dropped"}


def test_callout_drop_retains_semantic_outcomes_without_duplicate_hole_lint():
    drawing = build_drawing(_single_hole())
    callout = next(name for name in drawing.annotations() if name.startswith("hc_"))
    measurements = tuple(
        drawing.registry.measurement_of(callout)
    )  # capture before remove clears provenance
    drawing.remove(callout)
    drawing.registry.record_issue(
        LintIssue(
            severity="warning",
            code="callout_dropped",
            message="synthetic placement failure",
            measurement_ids=measurements,
        )
    )

    outcomes = _outcomes(drawing)
    assert {item.parameter_id for item in outcomes if item.state == "dropped"} == {
        "bore.diameter",
        "bore.through",
    }
    assert not [issue for issue in drawing.lint() if issue.code == "hole_requirement_dropped"]
    completeness = _completeness(drawing)
    assert completeness["dropped"] == 2
    assert completeness["missing"] == 0


def test_authored_omissions_are_suppressed_on_the_declared_path():
    part = _single_hole()
    sheet = Sheet(part)
    sheet.hole(diameter=8, at=(12, 7, 10), axis="z").through()
    envelope = sheet.envelope()
    sheet.dimension(envelope, "width.length")
    drawing = sheet.build()

    outcomes = _outcomes(drawing)
    assert {item.parameter_id for item in outcomes if item.state == "suppressed"} == {
        "bore.diameter",
        "bore.through",
        "location.location.x",
        "location.location.y",
    }
    assert _completeness(drawing)["suppressed"] == 4


def test_planner_omission_without_a_datum_is_missing_not_authored_suppression():
    part = _single_hole()
    detected = build_drawing(part).model()
    drawing = build_drawing(part, model=replace(detected, datums=[]))

    outcomes = {item.parameter_id: item.state for item in _outcomes(drawing)}
    assert outcomes == {
        "bore.diameter": "placed",
        "bore.through": "placed",
        "location.location.x": "missing",
        "location.location.y": "missing",
    }
    issues = [issue for issue in drawing.lint() if issue.code.startswith("hole_requirement_")]
    assert {issue.code for issue in issues} == {"hole_requirement_missing"}
    assert all("deliberately omitted" not in issue.message for issue in issues)
    summary = drawing.lint_summary()
    assert summary["by_code"] == {
        "feature_not_located": 1,
        "hole_requirement_missing": 2,
    }
    assert summary["geometry_issues"] == 3


def test_deleting_a_declaration_cannot_shrink_the_recognition_denominator():
    part = _pattern_and_central_bore()
    complete = build_drawing(part)
    baseline = _completeness(complete)
    model = complete.model()
    reduced = replace(
        model,
        features=[
            feature for feature in model.features if getattr(feature, "kind", None) != "hole"
        ],
    )
    drawing = build_drawing(part, model=reduced)
    completeness = _completeness(drawing)

    assert completeness["requirements"] == baseline["requirements"] == 10
    assert completeness["unverifiable"] == 4
    assert completeness["audited_score"] == 0.6
    assert {
        item.requirement_count for item in _outcomes(drawing) if item.state == "unverifiable"
    } == {4}


def test_hole_outcomes_ignore_rendered_names_and_labels_when_semantic_ids_survive():
    drawing = build_drawing(_single_hole())
    name = next(name for name in drawing.annotations() if name.startswith("hc_"))
    drawing.get_annotation(name).label = "NOT A DIAMETER"

    assert all(item.state == "placed" for item in _outcomes(drawing))


def test_through_and_grouping_outcomes_require_structured_callout_facts():
    drawing = build_drawing(_linear_pattern(), page="A3")
    name = next(name for name in drawing.annotations() if name.startswith("hc_"))
    leader = drawing.get_annotation(name)
    leader.covers_count = 2

    outcomes = {item.parameter_id: item.state for item in _outcomes(drawing)}
    assert outcomes["bore.diameter"] == "placed"
    assert outcomes["bore.through"] == "placed"
    assert outcomes["grouping.count"] == "missing"

    leader.covers_count = 4
    outcomes = {item.parameter_id: item.state for item in _outcomes(drawing)}
    assert outcomes["grouping.count"] == "missing", "an over-count is not physical coverage"


def test_duplicate_truthful_count_facts_do_not_erase_coverage():
    drawing = build_drawing(_linear_pattern(), page="A3")
    feature = next(feature for feature in drawing.model().features if feature.kind == "pattern")
    duplicate = drawing.callout(feature)
    assert duplicate is not None

    outcomes = {item.parameter_id: item.state for item in _outcomes(drawing)}
    assert outcomes["grouping.count"] == "placed"


def test_through_fact_is_independent_of_diameter_provenance():
    drawing = build_drawing(_linear_pattern(), page="A3")
    name = next(name for name in drawing.annotations() if name.startswith("hc_"))
    leader = drawing.get_annotation(name)

    leader.covers_hole_requirements = ()
    outcomes = {item.parameter_id: item.state for item in _outcomes(drawing)}
    assert outcomes["bore.diameter"] == "placed"
    assert outcomes["bore.through"] == "missing"
    assert outcomes["grouping.count"] == "placed"


def test_hole_outcome_boundary_rejects_an_unowned_inventory_type():
    drawing = build_drawing(_single_hole())
    with pytest.raises(TypeError, match="RecognitionResult"):
        hole_requirement_outcomes(object(), drawing.model().features, drawing.registry)
