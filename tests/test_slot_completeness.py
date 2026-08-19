"""Slot completeness follows semantic provenance, never presentation (#1018 Gate 2)."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from b123d_recognisers import recognise_slots
from build123d import Box, Cylinder, Pos

from draftwright import Drawing, Sheet, build_drawing
from draftwright.linting.slot_coverage import slot_requirement_outcomes
from draftwright.model import slot
from draftwright.model.compiled import compile_dimensions


def _off_centre_slot():
    return Box(100, 70, 10) - Pos(22, -11, 0) * Box(30, 8, 20)


def _two_distinct_slots():
    return Box(140, 90, 10) - Pos(-35, -15, 0) * Box(30, 8, 20) - Pos(30, 18, 0) * Box(24, 12, 20)


def _slot_grid():
    part = Box(176, 136, 20)
    for x in (-22, 22):
        for y in (-34, 0, 34):
            part -= Pos(x, y, 0) * Box(24, 8, 20)
    return part


def _equal_pitch_slot_grid():
    part = Box(176, 136, 20)
    for x in (-17, 17):
        for y in (-34, 0, 34):
            part -= Pos(x, y, 0) * Box(24, 8, 20)
    return part


def _default_angle_slot_grid():
    part = Box(176, 136, 20)
    for x in (-34, 0, 34):
        for y in (-22, 22):
            part -= Pos(x, y, 0) * Box(24, 8, 20)
    return part


def _slot_row():
    part = Box(60, 180, 20)
    for y in (-45, -15, 15, 45):
        part -= Pos(0, y, 0) * Box(30, 8, 20)
    return part


def _slot_column():
    part = Box(180, 60, 20)
    for x in (-45, -15, 15, 45):
        part -= Pos(x, 0, 0) * Box(8, 30, 20)
    return part


def _off_axis_slot_grid():
    part = Box(20, 176, 136)
    for y in (-22, 22):
        for z in (-34, 0, 34):
            part -= Pos(0, y, z) * Box(20, 24, 8)
    return part


def _slot_requirement_codes(dwg):
    return [issue.code for issue in dwg.lint() if issue.code.startswith("slot_requirement_")]


def _outcomes(dwg):
    dwg.lint()  # declared critique obtains and caches its recognition aggregate here
    return slot_requirement_outcomes(
        dwg.recognition(),
        dwg.model().features,
        dwg.registry,
        compile_dimensions(dwg.model()).diagnostics,
    )


def _declared_slot_drawing(*, suppress=False, w_shift=0.0):
    part = _off_centre_slot()
    (source,) = recognise_slots(part)
    sheet = Sheet(part)
    handle = sheet.slot(
        width=source.width,
        length=source.length,
        long_axis=source.long_axis,
        width_axis=source.width_axis,
        depth_axis=source.depth_axis,
        w_center=source.w_center + w_shift,
        lo=source.lo,
        hi=source.hi,
        at=source.location,
    )
    if suppress:
        envelope = sheet.envelope()
        sheet.dimension(envelope, "width.length")
    else:
        sheet.auto_dimensions()
    return sheet.build(), handle


def _pattern_proxy(pattern, *, parameters, location_stem="location_slot_pattern"):
    values = {
        name: getattr(pattern, name)
        for name in (
            "frame",
            "pattern",
            "count",
            "member",
            "members",
            "pitch",
            "direction",
            "grid",
            "rows",
            "cols",
            "angle",
        )
    }
    values.update(kind="slot_pattern", parameters=parameters)
    if location_stem is not None:
        values["LOCATION_STEM"] = location_stem
    return SimpleNamespace(**values)


@pytest.fixture(scope="module")
def slot_grid_dwg():
    """One slot-grid build shared by read-only critiques (#656). Mutating tests build their own."""
    return build_drawing(_slot_grid())


def test_removing_an_off_centre_slots_location_is_detected():
    """The recogniser centroid and IR frame differ on the transverse axis by design.

    Removing only the compiler-identified location dimension must therefore be found through
    semantic slot correspondence, not a fuzzy kind-plus-frame-origin join.
    """
    dwg = build_drawing(_off_centre_slot())
    (slot,) = [feature for feature in dwg.model().features if feature.kind == "slot"]
    assert slot.frame.origin[1] == 0.0
    assert dwg.recognition().slots[0].location[1] == -11.0

    (location_name,) = [
        name
        for name in dwg.registry.names_for_feature(slot)
        if any(key["parameter_id"] == "location_slot.length" for key in dwg.measurement_keys(name))
    ]
    dwg.remove(location_name)

    assert _slot_requirement_codes(dwg) == ["slot_requirement_missing"]
    states = {outcome.parameter_id: outcome.state for outcome in _outcomes(dwg)}
    assert states == {
        "slot_width.length": "placed",
        "slot_length.length": "placed",
        "location_slot.length": "missing",
    }


def test_grid_pitch_annotations_retain_distinct_directional_provenance():
    """One placed pitch direction must not satisfy the other by set membership."""
    dwg = build_drawing(_slot_grid())
    pitch_names = sorted(name for name in dwg.annotations() if "slotpat_pitch" in name)
    assert len(pitch_names) == 2

    pitch_by_id = {dwg.measurement_keys(name)[0]["parameter_id"]: name for name in pitch_names}
    assert set(pitch_by_id) == {"grid_pitch.length.row", "grid_pitch.length.col"}

    dwg.remove(pitch_by_id["grid_pitch.length.row"])
    assert _slot_requirement_codes(dwg) == ["slot_requirement_missing"]
    outcomes = {outcome.parameter_id: outcome.state for outcome in _outcomes(dwg)}
    assert outcomes["grid_pitch.length.row"] == "missing"
    assert outcomes["grid_pitch.length.col"] == "placed"


def test_pattern_location_keeps_x_and_y_measurement_identity_distinct():
    dwg = build_drawing(_slot_grid())
    locations = {
        key["parameter_id"]: name
        for name in dwg.annotations()
        for key in dwg.measurement_keys(name)
        if key["parameter_id"].startswith("location_slot_pattern.")
    }
    assert set(locations) == {
        "location_slot_pattern.location.x",
        "location_slot_pattern.location.y",
    }

    dwg.remove(locations["location_slot_pattern.location.x"])
    states = {outcome.parameter_id: outcome.state for outcome in _outcomes(dwg)}
    assert states["location_slot_pattern.location.x"] == "missing"
    assert states["location_slot_pattern.location.y"] == "placed"


def test_equal_grid_pitches_do_not_collapse_directional_identity():
    """Equal numeric values must still retain row/column identity."""
    dwg = build_drawing(_equal_pitch_slot_grid())
    pitch_names = [name for name in dwg.annotations() if "slotpat_pitch" in name]
    assert len(pitch_names) == 2
    assert {
        dwg.measurement_keys(name)[0]["parameter_id"]
        for name in pitch_names
        if dwg.measurement_keys(name)
    } == {"grid_pitch.length.row", "grid_pitch.length.col"}


def test_one_recognised_grid_is_one_requirement_with_compound_provenance():
    dwg = build_drawing(_slot_grid())
    outcomes = _outcomes(dwg)
    assert [(outcome.source_kind, outcome.member_count) for outcome in outcomes] == [
        ("slot_pattern", 6),
    ] * 6
    assert {outcome.parameter_id for outcome in outcomes} == {
        "slot_width.length",
        "slot_length.length",
        "grid_pitch.length.row",
        "grid_pitch.length.col",
        "location_slot_pattern.location.x",
        "location_slot_pattern.location.y",
    }
    assert {outcome.state for outcome in outcomes} == {"placed"}

    (callout,) = [name for name in dwg.annotations() if name.startswith("m_slotpat")]
    assert {key["parameter_id"] for key in dwg.measurement_keys(callout)} == {
        "slot_width.length",
        "slot_length.length",
    }
    dwg.remove(callout)
    missing = [outcome for outcome in _outcomes(dwg) if outcome.state == "missing"]
    assert {outcome.parameter_id for outcome in missing} == {
        "slot_width.length",
        "slot_length.length",
    }
    assert all(outcome.member_count == 6 for outcome in missing)


def test_linear_pattern_pitch_retains_its_compiler_identity():
    dwg = build_drawing(_slot_row())
    (pitch,) = [name for name in dwg.annotations() if "slotpat_pitch" in name]
    assert [key["parameter_id"] for key in dwg.measurement_keys(pitch)] == ["pitch.length"]
    assert {outcome.state for outcome in _outcomes(dwg)} == {"placed"}


def test_severing_pitch_provenance_is_unverifiable_not_placed():
    dwg = build_drawing(_slot_grid())
    pitch = next(name for name in dwg.annotations() if "slotpat_pitch" in name)
    parameter = dwg.measurement_keys(pitch)[0]["parameter_id"]
    identity = dwg.registry.identity_of(pitch)
    identity["measurement"] = ()
    dwg.registry.reapply(pitch, identity)

    outcome = next(item for item in _outcomes(dwg) if item.parameter_id == parameter)
    assert outcome.state == "unverifiable"
    assert _slot_requirement_codes(dwg) == ["slot_requirement_unverifiable"]


def test_matching_free_text_does_not_certify_a_slot_location():
    dwg = build_drawing(_off_centre_slot())
    slot = next(feature for feature in dwg.model().features if feature.kind == "slot")
    location = next(
        name
        for name in dwg.registry.names_for_feature(slot)
        if any(key["parameter_id"] == "location_slot.length" for key in dwg.measurement_keys(name))
    )
    label = dwg.get_annotation(location).label
    dwg.remove(location)
    dwg.note(label, (20, 20), view="plan")

    assert _slot_requirement_codes(dwg) == ["slot_requirement_missing"]


def test_authored_omission_is_suppressed_not_missing():
    dwg, _handle = _declared_slot_drawing(suppress=True)
    outcomes = _outcomes(dwg)
    assert [outcome.state for outcome in outcomes] == ["suppressed"] * 3
    assert _slot_requirement_codes(dwg) == ["slot_requirement_suppressed"] * 3


def test_removing_a_slot_declaration_cannot_shrink_the_quality_denominator():
    complete, _handle = _declared_slot_drawing()
    sparse = Sheet(_off_centre_slot())
    envelope = sparse.envelope()
    sparse.dimension(envelope, "width.length")

    complete_quality = complete.lint_summary()["quality"]["completeness"]
    sparse_quality = sparse.build().lint_summary()["quality"]["completeness"]
    assert complete_quality["available"] is sparse_quality["available"] is True
    assert complete_quality["requirements"] == sparse_quality["requirements"] == 3
    assert complete_quality["placed"] == 3
    assert sparse_quality["unverifiable"] == 3
    assert complete_quality["audited_score"] == 1.0
    assert sparse_quality["audited_score"] == 0.0


def test_independent_audited_families_contribute_to_one_recognition_owned_denominator():
    part = _off_centre_slot() - Pos(-25, 15, -5) * Cylinder(4, 20)
    completeness = build_drawing(part).lint_summary()["quality"]["completeness"]

    assert completeness["available"] is True
    assert completeness["audited_score"] == 1.0
    assert completeness["scope"] == "audited_recognized_requirements"
    assert completeness["requirements"] == completeness["placed"] == 7
    assert completeness["by_family"]["slots"] == 3
    assert completeness["by_family"]["holes"] == 4
    assert completeness["unscored_recognized_families"] == []


def test_deleting_declared_features_cannot_improve_a_damaged_quality_score():
    part = _two_distinct_slots()
    full = build_drawing(part)
    declared_slots = [feature for feature in full.model().features if feature.kind == "slot"]
    for name in list(full.registry.names_for_feature(declared_slots[0])):
        full.remove(name)

    source = recognise_slots(part)[0]
    sparse_sheet = Sheet(part)
    sparse_sheet.slot(
        width=source.width,
        length=source.length,
        long_axis=source.long_axis,
        width_axis=source.width_axis,
        depth_axis=source.depth_axis,
        w_center=source.w_center,
        lo=source.lo,
        hi=source.hi,
        at=source.location,
    )
    sparse_sheet.auto_dimensions()
    sparse = sparse_sheet.build()

    empty_sheet = Sheet(part)
    envelope = empty_sheet.envelope()
    empty_sheet.dimension(envelope, "width.length")
    empty = empty_sheet.build()

    damaged_quality = full.lint_summary()["quality"]["completeness"]
    sparse_quality = sparse.lint_summary()["quality"]["completeness"]
    empty_quality = empty.lint_summary()["quality"]["completeness"]
    assert damaged_quality["requirements"] == sparse_quality["requirements"] == 6
    assert damaged_quality["placed"] == sparse_quality["placed"] == 3
    assert damaged_quality["audited_score"] == sparse_quality["audited_score"] == 0.5
    assert empty_quality["requirements"] == empty_quality["unverifiable"] == 6
    assert empty_quality["audited_score"] == 0.0


@pytest.mark.parametrize(("part_factory", "requirement_count"), [(_slot_row, 5), (_slot_grid, 6)])
def test_removing_a_pattern_declaration_preserves_recognised_cardinality(
    part_factory, requirement_count
):
    part = part_factory()
    complete = build_drawing(part).lint_summary()["quality"]["completeness"]
    sparse = Sheet(part)
    envelope = sparse.envelope()
    sparse.dimension(envelope, "width.length")
    omitted = sparse.build().lint_summary()["quality"]["completeness"]

    assert complete["requirements"] == omitted["requirements"] == requirement_count
    assert omitted["unverifiable"] == requirement_count


def test_authored_pattern_omission_suppresses_both_location_directions():
    part = _slot_grid()
    detected = build_drawing(part)
    (source,) = detected.recognition().slot_patterns
    source_member = source.slots[0]
    member = slot(
        width=source_member.width,
        length=source_member.length,
        long_axis=source_member.long_axis,
        width_axis=source_member.width_axis,
        depth_axis=source_member.depth_axis,
        w_center=source_member.w_center,
        lo=source_member.lo,
        hi=source_member.hi,
        at=source_member.location,
    )
    sheet = Sheet(part)
    sheet.slot_pattern(
        member,
        kind="grid",
        count=len(source.slots),
        grid=(source.row_pitch, source.col_pitch),
        rows=source.rows,
        cols=source.cols,
        angle=source.angle,
        at=source.center,
    )
    envelope = sheet.envelope()
    sheet.dimension(envelope, "width.length")
    outcomes = _outcomes(sheet.build())

    assert {outcome.parameter_id for outcome in outcomes if outcome.state == "suppressed"} == {
        "slot_width.length",
        "slot_length.length",
        "grid_pitch.length.row",
        "grid_pitch.length.col",
        "location_slot_pattern.location.x",
        "location_slot_pattern.location.y",
    }


def test_a_real_placement_failure_retains_the_dropped_measurement():
    dwg = build_drawing(_off_centre_slot(), page="A4", scale=1.5, scale_policy="permissive")
    (drop,) = [issue for issue in dwg.lint() if issue.code == "slot_dim_dropped"]
    legibility = dwg.lint_summary()["quality"]["legibility"]
    assert legibility["by_code"]["slot_dim_dropped"] == 1
    assert legibility["placement_drops"] >= 1
    assert legibility["score"] < 1.0
    assert len(drop.measurement_ids) == 1
    dropped_parameter = drop.measurement_ids[0].parameter
    outcome = next(item for item in _outcomes(dwg) if item.parameter_id == dropped_parameter)
    assert outcome.state == "dropped"
    assert "slot_requirement_dropped" not in _slot_requirement_codes(dwg)

    dwg.registry.reset_issues()
    dwg.registry.record_issue(replace(drop, measurement_ids=()))
    outcome = next(item for item in _outcomes(dwg) if item.parameter_id == dropped_parameter)
    assert outcome.state == "missing"


def test_pattern_placement_failures_retain_each_failed_measurement():
    dwg = build_drawing(_slot_grid(), page="A4", scale=1.0, scale_policy="permissive")
    outcomes = _outcomes(dwg)
    dropped = {outcome.parameter_id for outcome in outcomes if outcome.state == "dropped"}
    assert dropped == {
        "grid_pitch.length.row",
        "location_slot_pattern.location.x",
        "location_slot_pattern.location.y",
    }
    assert not [outcome for outcome in outcomes if outcome.state == "missing"]


def test_grouped_callout_failure_marks_its_orphaned_pattern_pitches(monkeypatch):
    annotations = Drawing.annotations

    def without_grouped_slot_callout(drawing):
        return [name for name in annotations(drawing) if not name.startswith("m_slotpat")]

    monkeypatch.setattr(Drawing, "annotations", without_grouped_slot_callout)
    dwg = build_drawing(_slot_grid())

    dropped = {
        measurement.parameter
        for issue in dwg.registry.issues
        if issue.code == "slot_dim_dropped"
        for measurement in issue.measurement_ids
    }
    assert dropped == {
        "grid_pitch.length.row",
        "grid_pitch.length.col",
    }


def test_stale_declared_geometry_is_unverifiable_without_a_nearest_match():
    dwg, _handle = _declared_slot_drawing(w_shift=1.0)
    assert [name for name in dwg.annotations() if name.startswith("m_slot")]
    outcomes = _outcomes(dwg)
    assert len(outcomes) == 1
    assert outcomes[0].state == "unverifiable"


def test_duplicate_ir_candidates_are_unverifiable_not_merged(slot_grid_dwg):
    dwg = slot_grid_dwg
    pattern = next(feature for feature in dwg.model().features if feature.kind == "slot_pattern")
    outcomes = slot_requirement_outcomes(
        dwg.recognition(),
        (pattern, pattern),
        dwg.registry,
        compile_dimensions(dwg.model()).diagnostics,
    )
    assert len(outcomes) == 1
    assert outcomes[0].state == "unverifiable"


def test_zero_declared_linear_direction_is_unverifiable():
    dwg = build_drawing(_slot_row())
    pattern = next(feature for feature in dwg.model().features if feature.kind == "slot_pattern")

    outcomes = slot_requirement_outcomes(
        dwg.recognition(),
        (replace(pattern, direction=(0.0, 0.0, 0.0)),),
        dwg.registry,
    )
    assert [(outcome.parameter_id, outcome.state) for outcome in outcomes] == [
        ("?", "unverifiable")
    ]


def test_missing_pattern_location_vocabulary_is_unverifiable(slot_grid_dwg):
    dwg = slot_grid_dwg
    pattern = next(feature for feature in dwg.model().features if feature.kind == "slot_pattern")
    proxy = _pattern_proxy(pattern, parameters=pattern.parameters, location_stem=None)

    outcomes = slot_requirement_outcomes(dwg.recognition(), (proxy,), dwg.registry)
    assert [(outcome.parameter_id, outcome.state) for outcome in outcomes] == [
        ("?", "unverifiable")
    ]


def test_incomplete_pattern_measurement_vocabulary_is_unverifiable(slot_grid_dwg):
    dwg = slot_grid_dwg
    pattern = next(feature for feature in dwg.model().features if feature.kind == "slot_pattern")
    proxy = _pattern_proxy(pattern, parameters=lambda: pattern.parameters()[:-1])

    outcomes = slot_requirement_outcomes(dwg.recognition(), (proxy,), dwg.registry)
    assert [(outcome.parameter_id, outcome.state) for outcome in outcomes] == [
        ("?", "unverifiable")
    ]


def test_off_axis_pattern_location_is_unverifiable_not_assumed_complete():
    dwg = build_drawing(_off_axis_slot_grid())
    outcomes = _outcomes(dwg)
    assert len(outcomes) == 1
    assert outcomes[0].source_kind == "slot_pattern"
    assert outcomes[0].member_count == 6
    assert outcomes[0].state == "unverifiable"


def test_automatic_and_declared_paths_agree_despite_their_frame_conventions():
    automatic = build_drawing(_off_centre_slot())
    declared, _handle = _declared_slot_drawing()
    auto_slot = next(feature for feature in automatic.model().features if feature.kind == "slot")
    declared_slot = next(
        feature for feature in declared.model().features if feature.kind == "slot"
    )
    assert auto_slot.frame.origin != declared_slot.frame.origin

    auto_states = [(outcome.parameter_id, outcome.state) for outcome in _outcomes(automatic)]
    declared_states = [(outcome.parameter_id, outcome.state) for outcome in _outcomes(declared)]
    assert auto_states == declared_states
    assert declared.recognition() is not None


def test_detected_and_equivalently_rotated_declared_grids_have_the_same_outcomes():
    part = _slot_grid()
    automatic = build_drawing(part)
    (source,) = automatic.recognition().slot_patterns
    source_member = source.slots[0]
    member = slot(
        width=source_member.width,
        length=source_member.length,
        long_axis=source_member.long_axis,
        width_axis=source_member.width_axis,
        depth_axis=source_member.depth_axis,
        w_center=source_member.w_center,
        lo=source_member.lo,
        hi=source_member.hi,
        at=source_member.location,
    )
    sheet = Sheet(part).auto_dimensions()
    sheet.slot_pattern(
        member,
        kind="grid",
        count=len(source.slots),
        grid=(source.row_pitch, source.col_pitch),
        rows=source.rows,
        cols=source.cols,
        angle=source.angle + 180.0,
        at=source.center,
    )
    declared = sheet.build()

    expected = [(outcome.parameter_id, outcome.state) for outcome in _outcomes(automatic)]
    actual = [(outcome.parameter_id, outcome.state) for outcome in _outcomes(declared)]
    assert actual == expected


def test_declared_grid_accepts_the_implicit_zero_degree_angle():
    part = _default_angle_slot_grid()
    automatic = build_drawing(part)
    (source,) = automatic.recognition().slot_patterns
    assert source.angle == 0.0
    source_member = source.slots[0]
    member = slot(
        width=source_member.width,
        length=source_member.length,
        long_axis=source_member.long_axis,
        width_axis=source_member.width_axis,
        depth_axis=source_member.depth_axis,
        w_center=source_member.w_center,
        lo=source_member.lo,
        hi=source_member.hi,
        at=source_member.location,
    )
    sheet = Sheet(part).auto_dimensions()
    sheet.slot_pattern(
        member,
        kind="grid",
        count=len(source.slots),
        grid=(source.row_pitch, source.col_pitch),
        rows=source.rows,
        cols=source.cols,
        at=source.center,
    )
    declared = sheet.build()

    expected = [(outcome.parameter_id, outcome.state) for outcome in _outcomes(automatic)]
    actual = [(outcome.parameter_id, outcome.state) for outcome in _outcomes(declared)]
    assert actual == expected


def test_declared_linear_pattern_accepts_another_member_and_reversed_axis():
    """Representative position and direction sign do not change the physical pattern."""
    part = _slot_row()
    automatic = build_drawing(part)
    (source,) = automatic.recognition().slot_patterns
    source_member = source.slots[-1]
    member = slot(
        width=source_member.width,
        length=source_member.length,
        long_axis=source_member.long_axis,
        width_axis=source_member.width_axis,
        depth_axis=source_member.depth_axis,
        w_center=source_member.w_center,
        lo=source_member.lo,
        hi=source_member.hi,
        at=source_member.location,
    )
    center = tuple(
        sum(slot.location[i] for slot in source.slots) / len(source.slots) for i in range(3)
    )
    sheet = Sheet(part).auto_dimensions()
    sheet.slot_pattern(
        member,
        kind="linear",
        count=len(source.slots),
        pitch=source.pitch,
        direction=tuple(-value for value in source.direction),
        at=center,
    )
    declared = sheet.build()

    expected = [(outcome.parameter_id, outcome.state) for outcome in _outcomes(automatic)]
    actual = [(outcome.parameter_id, outcome.state) for outcome in _outcomes(declared)]
    assert actual == expected


def test_declared_linear_pattern_accepts_its_implicit_default_axis():
    """Omitting the default +X axis is representation, not different geometry."""
    part = _slot_column()
    automatic = build_drawing(part)
    (source,) = automatic.recognition().slot_patterns
    source_member = source.slots[0]
    member = slot(
        width=source_member.width,
        length=source_member.length,
        long_axis=source_member.long_axis,
        width_axis=source_member.width_axis,
        depth_axis=source_member.depth_axis,
        w_center=source_member.w_center,
        lo=source_member.lo,
        hi=source_member.hi,
        at=source_member.location,
    )
    center = tuple(
        sum(item.location[i] for item in source.slots) / len(source.slots) for i in range(3)
    )
    sheet = Sheet(part).auto_dimensions()
    sheet.slot_pattern(
        member,
        kind="linear",
        count=len(source.slots),
        pitch=source.pitch,
        at=center,
    )
    declared = sheet.build()

    expected = [(outcome.parameter_id, outcome.state) for outcome in _outcomes(automatic)]
    actual = [(outcome.parameter_id, outcome.state) for outcome in _outcomes(declared)]
    assert actual == expected


def test_a_caller_assembled_empty_inventory_cannot_silence_slot_coverage():
    from draftwright.registry import AnnotationRegistry

    with pytest.raises(TypeError, match="RecognitionResult"):
        slot_requirement_outcomes(
            SimpleNamespace(slots=(), slot_patterns=()), (), AnnotationRegistry()
        )


def test_absent_recognition_and_a_valid_empty_inventory_are_both_quiet():
    from draftwright.registry import AnnotationRegistry

    dwg = build_drawing(Box(60, 40, 20))
    assert slot_requirement_outcomes(None, (), AnnotationRegistry()) == []
    assert slot_requirement_outcomes(dwg.recognition(), (), AnnotationRegistry()) == []
