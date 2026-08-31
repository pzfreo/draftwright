"""Structured feature notes carry explicit requirement coverage without parsing prose (#1351)."""

from __future__ import annotations

from dataclasses import replace
from typing import cast

import pytest
from b123d_recognisers import recognise_slots
from build123d import Align, Box, Cylinder, Pos

from draftwright import Sheet
from draftwright.linting.hole_coverage import hole_requirement_outcomes
from draftwright.linting.profiled_bore_coverage import lint_profiled_bore_coverage
from draftwright.linting.quality import quality_components
from draftwright.linting.slot_coverage import slot_requirement_outcomes
from draftwright.model import DimensionParameterId, PartModel, pocket
from draftwright.model.compiled import compile_dimensions
from draftwright.model.declare import note as declare_note
from draftwright.sheet_emit import emit_sheet_script

_SATISFIES: tuple[DimensionParameterId, ...] = (
    "counterbore.diameter",
    "counterbore.depth",
)


def _part():
    return Box(60, 40, 20) - Cylinder(4, 40) - Pos(0, 0, 6) * Cylinder(7, 8)


def _sheet(*, note: bool, satisfies: tuple[DimensionParameterId, ...] = ()) -> Sheet:
    sheet = Sheet.from_part(_part(), page="A3").take_over(
        dimensions="authored",
        principal_views="automatic",
        derived_views="authored",
    )
    counterbore = next(
        feature for feature in sheet.features if feature.kind == "hole" and feature.cbore
    )
    handle = sheet.of(counterbore)
    for parameter_id in handle.dimension_ids():
        if parameter_id not in _SATISFIES:
            sheet.dimension(handle, cast(DimensionParameterId, parameter_id))
    for feature in sheet.features:
        if feature.kind == "envelope":
            for parameter in feature.parameters():
                sheet.dimension(feature, parameter.parameter_id)
    if note:
        handle.note(
            "PROFILED BORE: diameter 14 x 8 DEEP",
            satisfies=satisfies,
        )
    return sheet


def _counterbore_states(drawing) -> dict[str, str]:
    drawing.lint()  # declared builds acquire critique recognition lazily
    return {
        outcome.parameter_id: outcome.state
        for outcome in hole_requirement_outcomes(
            drawing.recognition(),
            drawing.model().features,
            drawing.registry,
            compile_dimensions(drawing.model()).diagnostics,
        )
        if outcome.parameter_id in _SATISFIES
    }


def test_structured_note_satisfies_omitted_roles_without_becoming_a_dimension():
    drawing = _sheet(note=True, satisfies=_SATISFIES).build()

    assert _counterbore_states(drawing) == {
        parameter: "satisfied_by_structured_note" for parameter in _SATISFIES
    }
    satisfaction_names = [
        name for name in drawing.registry.names() if drawing.registry.satisfaction_of(name)
    ]
    assert len(satisfaction_names) == 1
    assert drawing.registry.measurement_of(satisfaction_names[0]) == ()
    assert {
        identity.parameter for identity in drawing.registry.satisfaction_of(satisfaction_names[0])
    } == set(_SATISFIES)

    issues = drawing.lint()
    assert not [
        issue
        for issue in issues
        if issue.code == "hole_requirement_suppressed"
        and any(parameter in issue.message for parameter in _SATISFIES)
    ]
    assert not [
        issue
        for issue in issues
        if issue.code == "feature_not_dimensioned" and "ø14" in issue.message
    ]
    completeness = drawing.lint_summary()["quality"]["completeness"]
    assert completeness["satisfied_by_structured_note"] == 2
    assert completeness["audited_score"] == 1.0

    drawing.remove(satisfaction_names[0])
    assert _counterbore_states(drawing) == {parameter: "suppressed" for parameter in _SATISFIES}


@pytest.mark.parametrize("with_note", [False, True])
def test_prose_without_structured_satisfaction_cannot_silence_coverage(with_note):
    drawing = _sheet(note=with_note).build()

    assert _counterbore_states(drawing) == {parameter: "suppressed" for parameter in _SATISFIES}
    issues = drawing.lint()
    suppressed = [
        issue
        for issue in issues
        if issue.code == "hole_requirement_suppressed"
        and any(parameter in issue.message for parameter in _SATISFIES)
    ]
    assert len(suppressed) == 2
    assert any(
        issue.code == "feature_not_dimensioned" and "ø14" in issue.message for issue in issues
    )


@pytest.mark.parametrize(
    ("satisfies", "message"),
    [
        (("counterbore.diameter", "counterbore.diameter"), "duplicate parameter ids"),
        (("bore.depth",), "invalid parameter id"),
        (("counterbore",), "invalid parameter id"),
    ],
)
def test_invalid_or_ambiguous_satisfaction_claims_fail_before_mutating_the_sheet(
    satisfies, message
):
    sheet = _sheet(note=False)
    handle = sheet.of(next(feature for feature in sheet.features if feature.kind == "hole"))
    before = tuple(sheet.features)

    with pytest.raises(ValueError, match=message):
        handle.note("PROFILED BORE", satisfies=satisfies)

    assert tuple(sheet.features) == before


def test_synthesised_location_claim_uses_planner_owned_eligibility():
    sheet = _sheet(note=False)
    envelope = sheet.envelope()
    before = tuple(sheet.features)

    with pytest.raises(ValueError, match="no planned location measurement"):
        envelope.note("LOCATED", satisfies=("location",))

    assert tuple(sheet.features) == before


def test_structured_note_round_trips_through_generated_sheet_source():
    sheet = _sheet(note=True, satisfies=_SATISFIES)
    source = emit_sheet_script(
        sheet.model(),
        "part",
        "structured-note",
        title="T",
        number="N",
        page="A3",
    )
    assert ".note(" in source
    assert f"satisfies={_SATISFIES!r}" in source

    namespace = {"part": _part()}
    body = source.replace("\npart\n", "\n", 1)
    exec(  # noqa: S102 - exercise the generated public Sheet source
        compile(body[: body.index("drawing = sheet.build()")], "<structured-note>", "exec"),
        namespace,
    )
    regenerated = namespace["sheet"]
    note = next(feature for feature in regenerated.features if feature.kind == "note")
    assert note.satisfies == _SATISFIES
    assert note.origin in regenerated.features
    assert _counterbore_states(regenerated.build()) == {
        parameter: "satisfied_by_structured_note" for parameter in _SATISFIES
    }


def test_structured_note_round_trip_survives_identity_preserving_feature_reorder():
    sheet = _sheet(note=True, satisfies=_SATISFIES)
    sheet.features.reverse()
    drawing = sheet.build()
    source = emit_sheet_script(
        drawing.model(), "part", "structured-note-reordered", title="T", number="N", page="A3"
    )

    namespace = {"part": _part()}
    body = source.replace("\npart\n", "\n", 1)
    exec(  # noqa: S102 - exercise the generated public Sheet source
        compile(body[: body.index("drawing = sheet.build()")], "<structured-note>", "exec"),
        namespace,
    )
    regenerated = namespace["sheet"]
    note = next(feature for feature in regenerated.features if feature.kind == "note")
    assert note.satisfies == _SATISFIES
    assert note.origin in regenerated.features
    assert not any(name.startswith("note") for name in namespace)


def test_structured_authority_requires_a_feature_owned_by_the_sheet():
    sheet = _sheet(note=False)
    owned = next(feature for feature in sheet.features if feature.kind == "hole")
    external_equal_clone = replace(owned)
    before = tuple(sheet.features)

    with pytest.raises(ValueError, match="managed by this Sheet"):
        sheet.note("EXTERNAL", external_equal_clone, satisfies=("bore.diameter",))
    assert tuple(sheet.features) == before

    structured = declare_note("EXTERNAL", external_equal_clone, satisfies=("bore.diameter",))
    with pytest.raises(ValueError, match="managed by this Sheet"):
        sheet.add(structured)
    assert tuple(sheet.features) == before

    with pytest.raises(ValueError, match="identical feature in PartModel.features"):
        PartModel(
            bbox=_part().bounding_box(),
            orientation=None,
            features=[structured],
            authored_dimensions=(),
        )

    managed = declare_note("MANAGED", owned, satisfies=("bore.diameter",))
    mutable_model = PartModel(
        bbox=_part().bounding_box(),
        orientation=None,
        features=[owned, managed],
        authored_dimensions=(),
    )
    mutable_model.features.remove(owned)
    with pytest.raises(ValueError, match="identical feature in PartModel.features"):
        compile_dimensions(mutable_model)


def test_unplaced_structured_note_grants_no_coverage_authority():
    sheet = _sheet(note=False)
    owned = next(feature for feature in sheet.features if feature.kind == "hole" and feature.cbore)
    invalid_target = replace(
        declare_note("PROFILED BORE", owned, satisfies=_SATISFIES),
        view="not-a-view",
    )
    sheet.add(invalid_target)
    drawing = sheet.build()

    assert any(issue.code == "gdt_dropped" for issue in drawing.lint())
    assert not any(drawing.registry.satisfaction_of(name) for name in drawing.registry.names())
    assert _counterbore_states(drawing) == {parameter: "suppressed" for parameter in _SATISFIES}


def test_diameter_claim_does_not_inherit_synthetic_through_or_group_count_authority():
    part = Box(60, 40, 20) - Cylinder(3, 40)
    sheet = Sheet.from_part(part).take_over(
        dimensions="authored", principal_views="automatic", derived_views="authored"
    )
    hole = next(feature for feature in sheet.features if feature.kind == "hole")
    sheet.of(hole).note("DIAMETER 6 ONLY", satisfies=("bore.diameter",))
    drawing = sheet.build()
    drawing.lint()
    states = {
        outcome.parameter_id: outcome.state
        for outcome in hole_requirement_outcomes(
            drawing.recognition(),
            drawing.model().features,
            drawing.registry,
            compile_dimensions(drawing.model()).diagnostics,
        )
    }
    assert states["bore.diameter"] == "satisfied_by_structured_note"
    assert states["bore.through"] == "suppressed"

    grouped_part = (
        Box(80, 50, 20) - Pos(-15, 0, 0) * Cylinder(3, 40) - Pos(15, 0, 0) * Cylinder(3, 40)
    )
    grouped = Sheet.from_part(grouped_part).take_over(
        dimensions="authored", principal_views="automatic", derived_views="authored"
    )
    grouped_hole = next(feature for feature in grouped.features if feature.kind == "hole")
    grouped.of(grouped_hole).note("DIAMETER 6 ONLY", satisfies=("bore.diameter",))
    grouped_drawing = grouped.build()
    grouped_drawing.lint()
    grouped_states = {
        outcome.parameter_id: outcome.state
        for outcome in hole_requirement_outcomes(
            grouped_drawing.recognition(),
            grouped_drawing.model().features,
            grouped_drawing.registry,
            compile_dimensions(grouped_drawing.model()).diagnostics,
        )
    }
    assert grouped_states["grouping.count"] == "suppressed"


def test_duplicate_notes_do_not_multiply_one_requirement_identity_into_physical_count():
    part = Box(80, 50, 20) - Pos(-15, 0, 0) * Cylinder(3, 40) - Pos(15, 0, 10) * Cylinder(3, 8)
    sheet = Sheet.from_part(part).take_over(
        dimensions="authored", principal_views="automatic", derived_views="authored"
    )
    through = next(
        feature for feature in sheet.features if feature.kind == "hole" and feature.through
    )
    sheet.note("DIAMETER 6", through, satisfies=("bore.diameter",))
    sheet.note("INSPECT DIAMETER 6", through, satisfies=("bore.diameter",))
    drawing = sheet.build()

    mismatch = [issue for issue in drawing.lint() if issue.code == "feature_count_mismatch"]
    assert len(mismatch) == 1
    assert "account for 1" in mismatch[0].message


def test_location_is_a_valid_synthesised_role_and_satisfies_both_required_axes():
    part = Box(60, 40, 20) - Pos(12, 7, 0) * Cylinder(3, 40)
    sheet = Sheet.from_part(part).take_over(
        dimensions="authored", principal_views="automatic", derived_views="authored"
    )
    hole = next(feature for feature in sheet.features if feature.kind == "hole")
    handle = sheet.of(hole)
    assert "location" in handle.dimension_ids()
    handle.note("HOLE CENTRE X12 Y7", satisfies=("location",))
    drawing = sheet.build()
    issues = drawing.lint()

    outcomes = hole_requirement_outcomes(
        drawing.recognition(),
        drawing.model().features,
        drawing.registry,
        compile_dimensions(drawing.model()).diagnostics,
    )
    location_states = {
        outcome.state for outcome in outcomes if outcome.parameter_id.startswith("location.")
    }
    assert location_states == {"satisfied_by_structured_note"}
    assert not any(issue.code == "feature_not_located" for issue in issues)


@pytest.mark.parametrize(
    ("kind", "part", "missing_code"),
    [
        ("pocket", Box(60, 50, 40) - Pos(20, 8, 5) * Box(20, 16, 18), "pocket_not_located"),
        (
            "pad",
            Box(60, 50, 10) + Pos(15, 8, 10) * Box(20, 16, 10),
            "pad_footprint_not_defined",
        ),
    ],
)
def test_prismatic_location_authority_joins_the_exact_physical_feature(kind, part, missing_code):
    sheet = Sheet.from_part(part).take_over(
        dimensions="authored", principal_views="automatic", derived_views="authored"
    )
    feature = next(item for item in sheet.features if item.kind == kind)
    for parameter in feature.parameters():
        sheet.dimension(feature, parameter.parameter_id)
    sheet.note("LOCATED BY AUTHORED COORDINATES", feature, satisfies=("location",))
    drawing = sheet.build()

    assert not any(issue.code == missing_code for issue in drawing.lint())
    note_name = next(
        name for name in drawing.registry.names() if drawing.registry.satisfaction_of(name)
    )
    drawing.remove(note_name)
    assert any(issue.code == missing_code for issue in drawing.lint())


def test_pocket_pattern_location_authority_stays_on_the_pattern_owner():
    part = Box(100, 60, 30) - Pos(-20, 5, 10) * Box(15, 10, 15) - Pos(20, 5, 10) * Box(15, 10, 15)
    sheet = Sheet(part)
    member = pocket(
        width=10,
        length=15,
        depth=12.5,
        long_axis="x",
        width_axis="y",
        depth_axis="z",
        lo=-7.5,
        hi=7.5,
        w_center=5,
        at=(0, 5, 8.75),
    )
    pattern = sheet.pocket_pattern(member, kind="linear", count=2, pitch=40, direction=(1, 0, 0))
    for parameter_id in pattern.dimension_ids():
        if parameter_id != "location":
            sheet.dimension(pattern, cast(DimensionParameterId, parameter_id))
    pattern.note("POCKET ARRAY LOCATED X/Y", satisfies=("location",))
    drawing = sheet.build()

    assert not any(issue.code == "pocket_not_located" for issue in drawing.lint())
    note_name = next(
        name for name in drawing.registry.names() if drawing.registry.satisfaction_of(name)
    )
    drawing.remove(note_name)
    assert any(issue.code == "pocket_not_located" for issue in drawing.lint())


def test_boss_height_coverage_accepts_exact_structured_authority():
    boss_solid = Pos(0, 0, 24) * Cylinder(7, 10)
    part = Box(90, 64, 38) + boss_solid
    sheet = Sheet(part)
    boss = sheet.boss(boss_solid)
    sheet.dimension(sheet.envelope(), "width.length")
    boss.note("BOSS PROJECTS 10", satisfies=("boss_height.length",))
    drawing = sheet.build()

    assert not any(issue.code == "boss_height_missing" for issue in drawing.lint())


def test_axial_step_note_joins_the_exact_recognised_span():
    part = Cylinder(15, 30) + Pos(0, 0, 30) * Cylinder(8, 30)
    sheet = Sheet.from_part(part).take_over(
        dimensions="authored", principal_views="automatic", derived_views="authored"
    )
    for step in [feature for feature in sheet.features if feature.kind == "step"]:
        sheet.note("AXIAL SEGMENT", step, satisfies=("step.length",))
    drawing = sheet.build()

    assert not any(issue.code == "axial_length_missing" for issue in drawing.lint())
    note_name = next(
        name for name in drawing.registry.names() if drawing.registry.satisfaction_of(name)
    )
    drawing.remove(note_name)
    assert any(issue.code == "axial_length_missing" for issue in drawing.lint())


def test_groove_length_authority_requires_exact_physical_band_correspondence():
    part = Cylinder(10, 40) - (Cylinder(10, 4) - Cylinder(8, 4))

    def drawing_with_groove(*, wrong_axis: bool):
        sheet = Sheet.from_part(part).take_over(
            dimensions="authored", principal_views="automatic", derived_views="authored"
        )
        # Both surrounding bands are independent requirements. The groove width supplies only
        # the narrow middle band; one step plus one groove must not falsely certify all three.
        for step in (feature for feature in sheet.features if feature.kind == "step"):
            sheet.note("AXIAL SEGMENT", step, satisfies=("step.length",))
        if wrong_axis:
            groove = sheet.groove(axis="x", width=4, diameter=16, at=(0, 0, 0))
            groove.note("4 WIDE GROOVE", satisfies=("groove.length",))
        else:
            physical = next(feature for feature in sheet.features if feature.kind == "groove")
            sheet.note("4 WIDE GROOVE", physical, satisfies=("groove.length",))
        return sheet.build()

    exact = drawing_with_groove(wrong_axis=False)
    assert not any(issue.code == "axial_length_missing" for issue in exact.lint())

    unrelated = drawing_with_groove(wrong_axis=True)
    assert any(issue.code == "axial_length_missing" for issue in unrelated.lint())


def test_compound_profiled_bore_note_covers_both_exact_profile_requirements():
    center = (Align.CENTER, Align.CENTER, Align.CENTER)
    cutter = Cylinder(5, 20, align=center) & Box(7.2, 20, 30, align=center)
    part = Box(30, 30, 10, align=center) - cutter
    sheet = Sheet.from_part(part).take_over(
        dimensions="authored", principal_views="automatic", derived_views="authored"
    )
    bore = next(feature for feature in sheet.features if feature.kind == "hole")
    sheet.of(bore).note(
        "DOUBLE-D 10 MAJOR × 7.2 A/F THRU",
        satisfies=("bore.diameter", "profile_across_flats.length"),
    )
    drawing = sheet.build()

    assert not any(issue.code == "profiled_bore_not_dimensioned" for issue in drawing.lint())
    note_name = next(
        name for name in drawing.registry.names() if drawing.registry.satisfaction_of(name)
    )
    drawing.remove(note_name)
    assert any(issue.code == "profiled_bore_not_dimensioned" for issue in drawing.lint())


def test_callout_and_note_on_one_profile_do_not_cover_an_identical_sibling():
    center = (Align.CENTER, Align.CENTER, Align.CENTER)
    cutter = Cylinder(5, 20, align=center) & Box(7.2, 20, 30, align=center)
    part = Box(60, 30, 10, align=center) - Pos(-15, 0, 0) * cutter - Pos(15, 0, 0) * cutter
    sheet = Sheet.from_part(part).take_over(
        dimensions="authored", principal_views="automatic", derived_views="authored"
    )
    bores = [feature for feature in sheet.features if feature.kind == "hole"]
    first = min(bores, key=lambda feature: feature.frame.origin[0])
    sheet.dimension(first, "bore.diameter")
    sheet.dimension(first, "profile_across_flats.length")
    sheet.of(first).note(
        "DOUBLE-D PROFILE",
        satisfies=("bore.diameter", "profile_across_flats.length"),
    )

    drawing = sheet.build()
    issues = drawing.lint()
    warning = next(issue for issue in issues if issue.code == "profiled_bore_not_dimensioned")
    assert "document 1" in warning.message

    bore = drawing.recognition().double_d_bores[0]
    dropped = (
        "double_d",
        bore.axis,
        bore.through,
        bore.major_diameter,
        bore.across_flats,
        bore.flat_direction,
    )
    drop_reconciled = lint_profiled_bore_coverage(
        part,
        drawing.items,
        recognition=drawing.recognition(),
        features=drawing.model().features,
        registry=drawing.registry,
        dropped_profiles=(dropped,),
    )
    assert any(issue.code == "profiled_bore_not_dimensioned" for issue in drop_reconciled)

    same_owner_drop = lint_profiled_bore_coverage(
        part,
        drawing.items,
        recognition=drawing.recognition(),
        features=drawing.model().features,
        registry=drawing.registry,
        dropped_profile_evidence=((dropped, first),),
    )
    assert any(issue.code == "profiled_bore_not_dimensioned" for issue in same_owner_drop)

    second = max(bores, key=lambda feature: feature.frame.origin[0])
    sibling_drop = lint_profiled_bore_coverage(
        part,
        drawing.items,
        recognition=drawing.recognition(),
        features=drawing.model().features,
        registry=drawing.registry,
        dropped_profile_evidence=((dropped, second),),
    )
    assert not sibling_drop


def test_profile_note_must_match_the_recognised_physical_site():
    center = (Align.CENTER, Align.CENTER, Align.CENTER)
    cutter = Cylinder(5, 20, align=center) & Box(7.2, 20, 30, align=center)
    part = Box(30, 30, 10, align=center) - cutter
    sheet = Sheet(part)
    phantom = sheet.double_d_bore(
        major_diameter=10,
        across_flats=7.2,
        at=(8, 0, 5),
        axis="z",
        depth=10,
        profile_direction=(1, 0, 0),
    )
    sheet.authored_dimensions()
    phantom.note(
        "DOUBLE-D PROFILE",
        satisfies=("bore.diameter", "profile_across_flats.length"),
    )

    codes = {issue.code for issue in sheet.build().lint()}
    assert "profiled_bore_not_dimensioned" in codes
    assert "declared_feature_absent" in codes


def test_callout_and_note_on_one_diameter_do_not_cover_an_identical_sibling():
    center = (Align.CENTER, Align.CENTER, Align.CENTER)
    part = (
        Box(60, 30, 10, align=center)
        - Pos(-15, 0, 0) * Cylinder(3, 20, align=center)
        - Pos(15, 0, 2.5) * Cylinder(3, 5, align=center)
    )
    sheet = Sheet.from_part(part).take_over(
        dimensions="authored", principal_views="automatic", derived_views="authored"
    )
    holes = [feature for feature in sheet.features if feature.kind == "hole"]
    first = min(holes, key=lambda feature: feature.frame.origin[0])
    sheet.dimension(first, "bore.diameter")
    sheet.of(first).note("DIAMETER 6", satisfies=("bore.diameter",))

    warning = next(
        issue for issue in sheet.build().lint() if issue.code == "feature_count_mismatch"
    )
    assert "account for 1" in warning.message


def test_pre_satisfaction_registry_shape_remains_a_valid_consumer_boundary():
    drawing = _sheet(note=True, satisfies=_SATISFIES).build()
    drawing.lint()  # declared builds acquire critique recognition lazily

    class LegacyRegistry:
        """The public reads available before structured satisfaction provenance."""

        def __init__(self, inner):
            self._inner = inner
            self.issues = inner.issues

        def names(self):
            return self._inner.names()

        def named(self, name):
            return self._inner.named(name)

        def measurement_of(self, name):
            return self._inner.measurement_of(name)

    legacy = LegacyRegistry(drawing.registry)
    recognition = drawing.recognition()
    features = drawing.model().features
    omissions = compile_dimensions(drawing.model()).diagnostics

    outcomes = hole_requirement_outcomes(recognition, features, legacy, omissions)
    assert outcomes
    quality = quality_components(
        recognition=recognition,
        features=features,
        registry=legacy,
        omissions=omissions,
        issues=[],
        error_penalty=0.2,
        warning_penalty=0.1,
        has_asserted_content=True,
    )
    assert quality["completeness"]["available"] is True


def test_structured_note_authority_is_shared_by_non_hole_requirement_ledgers():
    part = Box(100, 70, 10) - Pos(22, -11, 0) * Box(30, 8, 20)
    (source,) = recognise_slots(part)
    sheet = Sheet(part)
    handle = sheet.slot(
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
    sheet.dimension(sheet.envelope(), "width.length")  # authored: slot roles are omitted
    handle.note("MILL 8 WIDE SLOT", satisfies=("slot_width.length",))
    drawing = sheet.build()
    drawing.lint()

    states = {
        outcome.parameter_id: outcome.state
        for outcome in slot_requirement_outcomes(
            drawing.recognition(),
            drawing.model().features,
            drawing.registry,
            compile_dimensions(drawing.model()).diagnostics,
        )
    }
    assert states["slot_width.length"] == "satisfied_by_structured_note"
    assert states["slot_length.length"] == "suppressed"
