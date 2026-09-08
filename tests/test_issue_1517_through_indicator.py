"""An explicit through indicator is content, independent of engineering facts."""

import json
from collections import Counter
from dataclasses import asdict

import pytest
from build123d import Box

from draftwright import Sheet
from draftwright.model import Frame, HoleFeature


@pytest.mark.parametrize("indicator", [None, "THRU", "", "THROUGH ALL"])
def test_public_through_declaration_preserves_serialized_override(indicator):
    sheet = Sheet(Box(40, 30, 12))
    handle = sheet.hole(diameter=6, at=(5, 3, 0), axis="z", through=False, depth=4)
    handle.through(indicator).tolerance(0.01).fit("H7")
    (feature,) = sheet.features
    assert feature.through and feature.depth is None
    assert feature.through_indicator == indicator
    assert feature.diameter == 6 and feature.count == 1
    payload = json.loads(json.dumps(asdict(feature)))
    restored = HoleFeature(**{**payload, "frame": Frame(**payload["frame"])})
    assert restored.through and restored.depth is None
    assert restored.through_indicator == indicator
    assert restored.diameter == 6
    # Unrelated edits keep the authored content.
    handle.note("FINISH BORE").tolerance(0.02)
    assert sheet.features[0].through_indicator == indicator


def test_blind_edit_clears_indicator_and_next_through_uses_default():
    sheet = Sheet(Box(40, 30, 12))
    handle = sheet.hole(diameter=6, at=(5, 3, 0), axis="z").through("THROUGH ALL")
    handle.depth(4)
    feature = sheet.features[0]
    assert not feature.through and feature.depth == 4 and feature.through_indicator is None
    handle.through()
    feature = sheet.features[0]
    assert feature.through and feature.depth is None and feature.through_indicator is None


@pytest.mark.parametrize("indicator", [False, 3, " ", "A\nB", "A\u2028B", "\x00"])
def test_invalid_indicator_refuses_at_the_ir_boundary(indicator):
    with pytest.raises(ValueError, match="through_indicator"):
        HoleFeature(Frame((0, 0, 0), "z"), 6, None, True, through_indicator=indicator)


def test_direct_blind_ir_cannot_retain_obsolete_indicator():
    with pytest.raises(ValueError, match="blind hole"):
        HoleFeature(Frame((0, 0, 0), "z"), 6, 4, False, through_indicator="THRU")


@pytest.mark.parametrize("indicator", [None, "THRU", "", "THROUGH ALL"])
@pytest.mark.parametrize("decoration", ["tolerance", "fit"])
def test_wording_changes_preserve_measurements_and_printed_credit(indicator, decoration):
    from build123d import Cylinder, Pos

    from draftwright.audit import compare_measurements
    from draftwright.model.compiled import compile_dimensions

    tool = Pos(5, 3, 0) * Cylinder(3, 20)
    part = Box(40, 30, 12) - tool
    sheet = Sheet(part, page="A3", scale=1)
    handle = sheet.hole(tool)
    if decoration == "fit":
        handle.fit("H7")
    else:
        handle.tolerance(0.02)
    sheet.dimension(handle, "bore.diameter")
    sheet.dimension(handle, "location")
    before_model = sheet.model()
    before = sheet.build()
    handle.through(indicator)
    after_model = sheet.model()
    after = sheet.build()
    assert len(before_model.features) == len(after_model.features) == 1
    pairs = ((before_model.features[0], after_model.features[0]),)
    assert pairs[0][0].diameter == pairs[0][1].diameter == 6
    assert pairs[0][0].frame == pairs[0][1].frame
    comparison = compare_measurements(before, after, feature_pairs=pairs)
    assert not comparison["lost"] and not comparison["gained"] and not comparison["unknown"]
    assert Counter(
        (claim.parameter, claim.meaning, claim.witnesses)
        for claim in before.measurement_snapshot().claims
    ) == Counter(
        (claim.parameter, claim.meaning, claim.witnesses)
        for claim in after.measurement_snapshot().claims
    )
    expected_changes = [] if indicator in (None, "THRU") else ["bore.diameter"]
    assert [item["parameter_id"] for item in comparison["changed"]] == expected_changes
    callouts = [
        a for _, a in after.iter_annotations() if getattr(a, "covers_diameters", ()) == (6,)
    ]
    assert len(callouts) == 1
    (callout,) = callouts
    label = callout.label
    assert ("H7" if decoration == "fit" else "0.02") in label
    resolved = "THRU" if indicator is None else indicator
    expected_label = "⌀6 " + ("H7" if decoration == "fit" else "±0.02")
    assert label == expected_label + (f" {resolved}" if resolved else "")
    if resolved:
        assert resolved in label
        assert "bore.through" in callout.covers_hole_requirements
    else:
        assert "THRU" not in label and "bore.through" not in callout.covers_hole_requirements
    omissions = [
        item
        for item in compile_dimensions(after_model).diagnostics
        if item.parameter_id == "bore.through"
    ]
    assert len(omissions) == int(indicator == "")
    if omissions:
        assert omissions[0].authored and omissions[0].feature is after_model.features[0]
        assert "indicator explicitly omitted" in omissions[0].reason
    from draftwright.linting.hole_coverage import hole_requirement_outcomes

    outcomes = hole_requirement_outcomes(
        after.recognition(),
        after_model.features,
        after.registry,
        compile_dimensions(after_model).diagnostics,
    )
    assert [
        (outcome.parameter_id, outcome.state) for outcome in outcomes if outcome.state != "placed"
    ] == ([("bore.through", "suppressed")] if indicator == "" else [])
    unexpected = [
        issue
        for issue in after.lint()
        if issue.severity in {"warning", "error"} and issue.code != "hole_requirement_suppressed"
    ]
    assert not unexpected


@pytest.mark.parametrize(
    "second_indicator,expected_count", [("THRU", 1), ("", 2), ("THROUGH ALL", 2)]
)
def test_matching_wording_groups_without_collapsing_owners(second_indicator, expected_count):
    from build123d import Cylinder, Pos

    from draftwright.linting.hole_coverage import _index_hole_evidence
    from draftwright.model.callout import hole_callout_batches
    from draftwright.model.planner import plan_dimensions

    points = ((-10, -6, 0), (12, 7, 0))
    tools = [Pos(*point) * Cylinder(3, 20) for point in points]
    part = Box(50, 35, 12)
    for tool in tools:
        part -= tool
    sheet = Sheet(part, page="A3", scale=1)
    for tool, indicator in zip(tools, (None, second_indicator), strict=True):
        handle = sheet.hole(tool).through(indicator)
        sheet.dimension(handle, "bore.diameter")
        sheet.dimension(handle, "location")
    model = sheet.model()
    assert len(model.features) == 2
    assert [feature.through_indicator for feature in model.features] == [None, second_indicator]
    batches = hole_callout_batches(plan_dimensions(model))
    assert len(batches) == expected_count
    assert {id(group.feature) for batch in batches for group in batch.groups} == {
        id(feature) for feature in model.features
    }
    drawing = sheet.build()
    callouts = [
        (name, a)
        for name, a in drawing.iter_annotations()
        if getattr(a, "covers_diameters", ()) == (6,)
    ]
    assert len(callouts) == expected_count
    assert sum(item.covers_count for _, item in callouts) == 2
    for feature in model.features:
        owned = drawing.annotations_of(feature)
        assert any(name in owned for name, _ in callouts)
        assert any(
            identity.feature is feature and identity.parameter == "bore.diameter"
            for name, _ in callouts
            for identity in drawing.registry.measurement_of(name)
        )
    evidence = _index_hole_evidence(drawing.registry)
    for feature in model.features:
        assert evidence.requirement_counts[(feature, "grouping.count")] == {1}
        printed = evidence.requirement_counts.get((feature, "bore.through"), set())
        assert printed == (set() if feature.through_indicator == "" else {1})
    from draftwright.model.compiled import compile_dimensions

    omissions = [
        item
        for item in compile_dimensions(model).diagnostics
        if item.parameter_id == "bore.through"
    ]
    assert [(item.feature, item.authored) for item in omissions] == (
        [(model.features[1], True)] if second_indicator == "" else []
    )
    if expected_count == 1:
        name, callout = callouts[0]
        assert "2×" in callout.label
        drawing.pin(name)
        with drawing.deferred():
            # An unrelated pending edit must wait until the outer batch ends.
            drawing.locate(model.features[0], pin=True)
            removed = drawing.drop(model.features[1])
            assert name in removed
            assert not drawing.annotations_of(model.features[1])
            assert not [
                a
                for _, a in drawing.iter_annotations()
                if getattr(a, "covers_diameters", ()) == (6,)
            ]
        assert drawing.registry.pinned_names()
        assert not drawing.registry.is_pinned(name)
        survivor = [
            a for _, a in drawing.iter_annotations() if getattr(a, "covers_diameters", ()) == (6,)
        ]
        assert len(survivor) == 1 and survivor[0].covers_count == 1
        assert drawing.annotations_of(model.features[0])


@pytest.mark.parametrize("indicator", [None, "THRU", "", "THROUGH ALL"])
def test_script_replay_tables_and_measured_content_agree(indicator, tmp_path):
    from build123d import Cylinder, Pos

    from draftwright.annotations.from_model import callout_from_spec
    from draftwright.audit import compare_measurements
    from draftwright.compose import _est_planned_bore_callout_width
    from draftwright.model.callout import hole_callout_spec
    from draftwright.model.planner import plan_dimensions
    from draftwright.sheet_emit import emit_sheet_script

    tool = Pos(5, 3, 0) * Cylinder(3, 20)
    part = Box(40, 30, 12) - tool
    sheet = Sheet(part, page="A3", scale=1)
    # Explicit constructor form preserves measured through-depth during replay.
    handle = sheet.hole(tool, depth=12, through_indicator=indicator).tolerance(0.02)
    sheet.dimension(handle, "bore.diameter")
    sheet.dimension(handle, "location")
    original = sheet.build()
    feature = sheet.model().features[0]
    assert feature.through and feature.depth is not None
    script = emit_sheet_script(
        sheet.model(),
        "part = supplied_part",
        str(tmp_path / "replay"),
        title="WORDING",
        number="WORDING",
        page="A3",
        scale=1,
        formats=("svg", "pdf", "dxf"),
    )
    namespace = {"supplied_part": part}
    exec(script, namespace)
    replay = namespace["drawing"]
    recreated = namespace["sheet"].model().features[0]
    assert recreated.through_indicator == indicator
    assert recreated.through and recreated.depth == feature.depth
    assert (
        compare_measurements(original, replay, feature_pairs=((feature, recreated),))["status"]
        == "preserved"
    )
    (group,) = plan_dimensions(namespace["sheet"].model())
    primitive = callout_from_spec(hole_callout_spec(group), replay.draft, None)
    resolved = "THRU" if indicator is None else indicator
    expected = "⌀6 ±0.02" + (f" {resolved}" if resolved else "")
    assert primitive.label == expected
    width = _est_planned_bore_callout_width([group], replay.draft)
    assert primitive.bounding_box().size.X - 0.1 <= width < primitive.bounding_box().size.X + 5
    table = replay.add_hole_table(balloons=False)
    assert table is not None
    assert any("±0.02" in cell for row in table.table_rows for cell in row)
    depth_column = table.table_rows[0].index("DEPTH")
    assert [row[depth_column] for row in table.table_rows[1:]] == [resolved]
    assert all(
        (tmp_path / f"replay.{extension}").stat().st_size > 100
        for extension in ("svg", "pdf", "dxf")
    )


@pytest.mark.parametrize(
    "machining",
    [
        {"cbore": (12, 3)},
        {"spotface": (12, 1)},
        {"csink": (12, 90)},
    ],
)
def test_omitted_machining_terms_still_prevent_grouping(machining):
    from draftwright.model.callout import hole_callout_batches, hole_callout_spec
    from draftwright.model.planner import plan_dimensions

    sheet = Sheet(Box(50, 35, 12)).authored_dimensions()
    first = sheet.hole(diameter=6, at=(-10, -6, 0), axis="z", **machining)
    second = sheet.hole(diameter=6, at=(12, 7, 0), axis="z")
    for handle in (first, second):
        sheet.dimension(handle, "bore.diameter")
    groups = plan_dimensions(sheet.model())
    assert len(groups) == 2
    specs = [hole_callout_spec(group) for group in groups]
    # The author selected identical printed content, but physical machining differs.
    assert {key: value for key, value in specs[0].items() if key != "measurements"} == {
        key: value for key, value in specs[1].items() if key != "measurements"
    }
    assert len(hole_callout_batches(groups)) == 2


@pytest.mark.parametrize("indicator", [None, "THRU", "", "THROUGH ALL"])
def test_pattern_member_script_preserves_wording_counts_and_depth(indicator, tmp_path):
    from build123d import Cylinder, Pos

    from draftwright.audit import compare_measurements
    from draftwright.model import hole
    from draftwright.sheet_emit import emit_sheet_script

    points = ((-10, 0, 0), (10, 0, 0))
    part = Box(50, 35, 12)
    for point in points:
        part -= Pos(*point) * Cylinder(3, 20)
    sheet = Sheet(part, page="A3", scale=1)
    member = hole(diameter=6, at=(0, 0, 0), axis="z", depth=12, through_indicator=indicator)
    pattern = sheet.pattern(member, kind="linear", count=2, pitch=20, members=points)
    sheet.dimension(pattern, "bore.diameter")
    sheet.dimension(pattern, "pitch.length")
    sheet.dimension(pattern, "location")
    direct = sheet.build()
    script = emit_sheet_script(
        sheet.model(),
        "part = supplied_part",
        str(tmp_path / "pattern"),
        title="PATTERN",
        number="PATTERN",
        page="A3",
        scale=1,
        formats=("svg",),
    )
    namespace = {"supplied_part": part}
    exec(script, namespace)
    replay = namespace["drawing"]
    original = sheet.model().features[0]
    recreated = namespace["sheet"].model().features[0]
    assert recreated.member.through_indicator == indicator
    assert recreated.member.through and recreated.member.depth == 12
    assert recreated.count == 2 and recreated.members == points
    assert (
        compare_measurements(direct, replay, feature_pairs=((original, recreated),))["status"]
        == "preserved"
    )
    callouts = [
        a for _, a in replay.iter_annotations() if getattr(a, "covers_diameters", ()) == (6,)
    ]
    assert len(callouts) == 1 and callouts[0].covers_count == 2
    resolved = "THRU" if indicator is None else indicator
    assert callouts[0].label == "2× ⌀6" + (f" {resolved}" if resolved else "")


@pytest.mark.parametrize("difference", ["side", "tolerance", "axis_plane", "overlap", "count"])
def test_grouping_respects_content_support_and_placement_constraints(difference):
    from draftwright.model.callout import hole_callout_batches
    from draftwright.model.planner import plan_dimensions

    sheet = Sheet(Box(50, 35, 12)).authored_dimensions()
    first = sheet.hole(diameter=6, at=(-10, -6, 0), axis="z")
    second = sheet.hole(
        diameter=6,
        at=(-10, -6, 0)
        if difference == "overlap"
        else (12, 7, 2 if difference == "axis_plane" else 0),
        axis="z",
        count=2 if difference == "count" else 1,
    )
    if difference == "tolerance":
        first.tolerance(0.01)
        second.tolerance(0.02)
    sheet.dimension(first, "bore.diameter", side="left" if difference == "side" else None)
    sheet.dimension(second, "bore.diameter", side="right" if difference == "side" else None)
    batches = hole_callout_batches(plan_dimensions(sheet.model()))
    assert len(batches) == 2
    assert sum(batch.spec["count"] or 1 for batch in batches) == (
        3 if difference == "count" else 2
    )
