"""Relative dimension lanes are exact, replayable layout-only intent (#1757)."""

import copy
import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest
from build123d import Box, Cylinder, Pos, Rot
from build123d_drafting.helpers import Draft
from jsonschema.validators import validator_for

from draftwright import Sheet
from draftwright.annotations._axial_render import _place_or_queue_rotational_od
from draftwright.annotations._common import PlacementContext
from draftwright.annotations._diameters import _render_diameter_controls
from draftwright.annotations.from_model import _record_slot_drop
from draftwright.model.ir import LayoutOverride, RequestedDimension
from draftwright.registry import AnnotationRegistry
from draftwright.sheet_emit import emit_sheet_script


def _slot_sheet() -> Sheet:
    sheet = Sheet(Box(100, 80, 10), title="lane").authored_dimensions()
    slot = sheet.slot(
        width=20,
        length=40,
        long_axis="x",
        width_axis="y",
        lo=-20,
        hi=20,
        w_center=0,
        at=(0, 0, 5),
    ).identify("declaration:slot", provenance="detected-geometry")
    sheet.dimension(slot, "slot_width.length")
    sheet.dimension(slot, "slot_length.length")
    return sheet


def test_lane_capability_targets_one_declared_parameter() -> None:
    sheet = _slot_sheet()

    assert sheet.layout_options("declaration:slot", parameter="slot_width.length") == {
        "schema": "draftwright.layout-options",
        "schema_version": 1,
        "scope": "single-dimension-layout-controls",
        "requires_build_validation": True,
        "declaration_id": "declaration:slot",
        "feature_kind": "slot",
        "parameter_id": "slot_width.length",
        "controls": {
            "lane": {
                "current": None,
                "minimum": 1,
                "maximum": 8,
                "meaning": "one-based drafting-spaced lane from the feature witness",
            }
        },
    }
    assert (
        sheet.validate_layout_override("declaration:slot", parameter="slot_width.length", lane=4)[
            "supported"
        ]
        is True
    )
    unsupported_side = sheet.validate_layout_override(
        "declaration:slot", parameter="slot_width.length", side="left"
    )
    assert unsupported_side["issues"][0]["code"] == "unsupported_control"

    missing_parameter = sheet.validate_layout_override("declaration:slot", lane=4)
    assert missing_parameter["issues"][0]["code"] == "invalid_control_combination"
    mixed_controls = sheet.validate_layout_override(
        "declaration:slot", side="above", parameter="not-a-parameter"
    )
    assert mixed_controls["issues"][0]["code"] == "unsupported_declaration"
    invalid_lane = sheet.validate_layout_override(
        "declaration:slot", parameter="slot_width.length", lane=0
    )
    assert invalid_lane["issues"][0]["code"] == "unsupported_value"
    unsupported = sheet.validate_layout_override(
        "declaration:slot", parameter="slot_end_radius.radius", lane=2
    )
    assert unsupported["issues"][0]["code"] == "unsupported_declaration"


@pytest.mark.parametrize(
    ("axis", "rotation"),
    [("z", (0, 0, 0)), ("x", (0, 90, 0)), ("y", (90, 0, 0))],
)
def test_rotational_od_lane_moves_the_existing_dimension(axis, rotation) -> None:
    part = Rot(*rotation) * Cylinder(15, 40)
    positions = []
    for lane in (1, 2):
        sheet = Sheet(part, page="A3", scale=2).authored_dimensions()
        od = sheet.rotational(od=30, at=(0, 0, 0), axis=axis).identify("declaration:od")
        sheet.dimension(od, "od.diameter")
        assert (
            "lane" in sheet.layout_options("declaration:od", parameter="od.diameter")["controls"]
        )
        sheet.layout_override("declaration:od", parameter="od.diameter", lane=lane)
        drawing = sheet.build()
        mark = drawing.get_annotation("dim_od")
        assert mark is not None
        assert not any(issue.code == "placement_unsatisfiable" for issue in drawing.lint())
        positions.append(mark.placement_spec.distance)
        if axis == "x" and lane == 2:
            assert mark.placement_spec.side == "right"
        assert drawing.report()["layout"]["overrides"][0]["resolved_value"] == lane
    assert positions[1] > positions[0]


def test_od_lane_without_batch_solve_reports_refusal() -> None:
    context = PlacementContext(registry=AnnotationRegistry())
    od = SimpleNamespace(lane=2, measurement_ids=())
    count = _place_or_queue_rotational_od(
        None, context, od, (0, 0, 0), (1, 0, 0), "above", "front", "ø1"
    )

    (issue,) = context.registry.issues
    assert count == 0
    assert issue.code == "placement_unsatisfiable"
    assert issue.evidence_reason == "requested_lane_unavailable:2:measured_lane_solve_unavailable"


@pytest.mark.parametrize(
    ("axis", "rotation"),
    [("x", (0, 90, 0)), ("y", (90, 0, 0)), ("z", (0, 0, 0))],
)
def test_step_diameter_lane_moves_one_bounded_leader(axis, rotation) -> None:
    elbows = []
    for lane in (1, 2):
        sheet = Sheet(Rot(*rotation) * Cylinder(15, 40), page="A3", scale=2).authored_dimensions()
        step = sheet.step(diameter=30, length=40, at=(0, 0, 0), axis=axis)
        step.identify("declaration:step")
        sheet.dimension(step, "step.diameter")
        assert (
            "lane"
            in sheet.layout_options("declaration:step", parameter="step.diameter")["controls"]
        )
        sheet.layout_override("declaration:step", parameter="step.diameter", lane=lane)
        drawing = sheet.build()
        mark = drawing.get_annotation(f"m_dia_{axis}0")
        assert mark is not None
        if axis == "y":
            assert mark.covers_diameters == (30,)
        assert not any(issue.code == "diameter_dropped" for issue in drawing.lint())
        assert drawing.report()["layout"]["overrides"][0]["resolved_value"] == lane
        elbows.append(mark.elbow)
    spacing = drawing.draft.font_size + 2 * drawing.draft.pad_around_text
    assert math.dist(elbows[0][:2], elbows[1][:2]) == pytest.approx(spacing)


def test_step_lane_does_not_rename_or_duplicate_untouched_diameter() -> None:
    part = Pos(-10, 0, 0) * Rot(0, 90, 0) * Cylinder(15, 20)
    part += Pos(10, 0, 0) * Rot(0, 90, 0) * Cylinder(10, 20)
    sheet = Sheet(part, page="A3", scale=2).authored_dimensions()
    first = sheet.step(diameter=30, length=20, at=(-10, 0, 0), axis="x").identify("step:a")
    second = sheet.step(diameter=20, length=20, at=(10, 0, 0), axis="x").identify("step:b")
    sheet.dimension(first, "step.diameter")
    sheet.dimension(second, "step.diameter")
    sheet.layout_override("step:b", parameter="step.diameter", lane=2)

    drawing = sheet.build()
    assert drawing.get_annotation("m_dia_x0").label == "ø30"
    assert drawing.get_annotation("m_dia_x1").label == "ø20"
    assert not any(issue.code == "diameter_dropped" for issue in drawing.lint())


@pytest.mark.parametrize(
    ("pmi_mode", "expected"),
    [("annotate", ("manufacturing_requirement:#1",)), ("omit", ())],
)
def test_step_lane_preserves_visible_source_thread_identity(pmi_mode, expected) -> None:
    aspect = SimpleNamespace(source_ids=("manufacturing_requirement:#1",))
    facts = SimpleNamespace(
        frame=SimpleNamespace(axis="y"), get=lambda key: aspect if key == "thread" else None
    )
    group = SimpleNamespace(
        facts=facts,
        dims=(SimpleNamespace(kind="diameter", lane=2, id="dimension:step"),),
    )
    entry = ((0, 0, 0), 30, "30", {"step"}, None, None, [group])
    drawing = SimpleNamespace(draft=Draft(), view_bounds=lambda _view: (0, 0, 100, 100))
    analysis = SimpleNamespace(SCALE=2, pmi_mode=pmi_mode)
    captured = {}

    def place_jobs(*_args, **kwargs):
        captured.update(kwargs)
        return 0

    _render_diameter_controls(
        drawing,
        analysis,
        [(0, entry)],
        axis="y",
        prefix="m_dia_y",
        start=0,
        ctx=SimpleNamespace(document_member=True),
        radial_candidates=lambda *_args, **_kwargs: [((0, 0), (1, 1, 0), None)],
        place_jobs=place_jobs,
        leader_reach=lambda _draft: 8,
    )

    assert captured["source_ids_by_name"] == {"m_dia_y0": expected}
    assert captured["requested_lanes"] == {"m_dia_y0": 2}


@pytest.mark.parametrize(
    ("axis", "rotation", "sides", "coordinate"),
    [
        ("x", (0, 90, 0), ("above", "below"), 1),
        ("y", (90, 0, 0), ("left", "right"), 0),
        ("z", (0, 0, 0), ("left", "right"), 0),
    ],
)
def test_step_diameter_side_override_keeps_leader_on_requested_side(
    axis, rotation, sides, coordinate
) -> None:
    observed = []
    for side in sides:
        sheet = Sheet(Rot(*rotation) * Cylinder(15, 40), page="A3", scale=2).authored_dimensions()
        step = sheet.step(diameter=30, length=40, at=(0, 0, 0), axis=axis)
        step.identify("declaration:step")
        sheet.dimension(step, "step.diameter")
        side_options = sheet.layout_options("declaration:step", parameter="step.diameter")[
            "controls"
        ]["side"]
        assert side in side_options["supported_values"]
        sheet.layout_override("declaration:step", parameter="step.diameter", side=side)
        drawing = sheet.build()
        mark = drawing.get_annotation(f"m_dia_{axis}0")
        assert mark is not None
        assert not any(issue.code == "placement_unsatisfiable" for issue in drawing.lint())
        observed.append(mark.elbow[coordinate] - mark.tip[coordinate])
    assert observed[0] * observed[1] < 0


def test_step_length_does_not_advertise_diameter_callout_side() -> None:
    sheet = Sheet(Rot(0, 90, 0) * Cylinder(15, 40)).authored_dimensions()
    step = sheet.step(diameter=30, length=40, at=(0, 0, 0), axis="x")
    step.identify("declaration:step")
    sheet.dimension(step, "step.length")
    result = sheet.validate_layout_override(
        "declaration:step", parameter="step.length", side="above"
    )
    assert result["supported"] is False


def test_lane_override_changes_only_the_exact_dimension_policy() -> None:
    sheet = _slot_sheet()
    sheet.layout_override("declaration:slot", parameter="slot_width.length", lane=4)

    model = sheet.model()
    by_role = {request.role: request for request in model.authored_dimensions or ()}
    assert by_role["slot_width.length"].lane == 4
    assert by_role["slot_length.length"].lane is None
    assert model.layout_overrides[0].parameter_id == "slot_width.length"
    assert model.layout_overrides[0].lane == 4
    assert model.layout_overrides[0].side is None


def test_direct_dimension_lane_refuses_an_unsupported_family() -> None:
    sheet = Sheet(Box(100, 80, 10), title="lane").authored_dimensions()
    hole = sheet.hole(diameter=10, at=(0, 0, 5), axis="z")

    with pytest.raises(ValueError, match="does not expose a lane control"):
        sheet.dimension(hole, "bore.diameter").place(lane=2)


def test_direct_dimension_lane_rejects_non_semantic_values() -> None:
    sheet = Sheet(Box(100, 80, 10), title="lane").authored_dimensions()
    slot = sheet.slot(
        width=20,
        length=40,
        long_axis="x",
        width_axis="y",
        lo=-20,
        hi=20,
        w_center=0,
        at=(0, 0, 5),
    )

    with pytest.raises(ValueError, match="integer from 1 to 8"):
        sheet.dimension(slot, "slot_width.length").place(lane=0)


def test_lane_field_preserves_requested_dimension_positional_member_abi() -> None:
    sheet = Sheet(Box(100, 80, 10), title="lane").auto_dimensions()
    sheet.hole(diameter=10, at=(0, 0, 5), axis="z")
    feature = sheet.model().features[-1]

    request = RequestedDimension(feature, "location", "x", None, None, None, 0)

    assert request.member == 0
    assert request.lane is None


def test_requested_dimension_rejects_invalid_and_location_lanes() -> None:
    sheet = Sheet(Box(100, 80, 10), title="lane").auto_dimensions()
    sheet.slot(
        width=20,
        length=40,
        long_axis="x",
        width_axis="y",
        lo=-20,
        hi=20,
        w_center=0,
        at=(0, 0, 5),
    )
    sheet.hole(diameter=10, at=(0, 0, 5), axis="z")
    slot_feature, hole_feature = sheet.model().features

    with pytest.raises(ValueError, match="integer from 1 to 8"):
        RequestedDimension(slot_feature, "slot_width.length", lane=0)
    with pytest.raises(ValueError, match="unavailable for location dimensions"):
        RequestedDimension(hole_feature, "location", "x", member=0, lane=2)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({}, "exactly one of side or lane"),
        ({"side": "above", "parameter_id": " slot_width.length "}, "non-empty parameter_id"),
        ({"lane": 2}, "non-empty parameter_id"),
        ({"parameter_id": " slot_width.length ", "lane": 2}, "surrounding whitespace"),
        ({"parameter_id": "slot_width.length", "lane": 9}, "integer from 1 to 8"),
    ],
)
def test_layout_override_record_rejects_ambiguous_or_unbounded_state(kwargs, message) -> None:
    with pytest.raises(ValueError, match=message):
        LayoutOverride("declaration:slot", **kwargs)


def test_layout_options_refuses_missing_duplicate_and_unsupported_dimensions() -> None:
    missing = Sheet(Box(100, 80, 10), title="lane").authored_dimensions()
    missing.slot(
        width=20,
        length=40,
        long_axis="x",
        width_axis="y",
        lo=-20,
        hi=20,
        w_center=0,
        at=(0, 0, 5),
    ).identify("declaration:missing", provenance="detected-geometry")
    with pytest.raises(ValueError, match="has no declared dimension"):
        missing.layout_options("declaration:missing", parameter="slot_width.length")

    hole = missing.hole(diameter=10, at=(0, 0, 5), axis="z").identify(
        "declaration:hole", provenance="detected-geometry"
    )
    missing.dimension(hole, "bore.diameter")
    assert (
        "side" in missing.layout_options("declaration:hole", parameter="bore.diameter")["controls"]
    )
    assert (
        missing.validate_layout_override("declaration:hole", parameter="bore.diameter", lane=2)[
            "issues"
        ][0]["code"]
        == "unsupported_control"
    )

    duplicate = _slot_sheet()
    duplicate.dimension(duplicate.model().features[0], "slot_width.length")
    with pytest.raises(ValueError, match="declared more than once"):
        duplicate.layout_options("declaration:slot", parameter="slot_width.length")


def test_impossible_lane_drop_retains_bounded_blocker_evidence() -> None:
    context = PlacementContext(registry=AnnotationRegistry())

    _record_slot_drop(
        context,
        "width",
        0,
        "plan",
        SimpleNamespace(kind="slot"),
        lane=8,
        blockers=("view_boundary_straddle", "page_bounds"),
    )

    (issue,) = context.registry.issues
    assert "requested lane 8 unavailable" in issue.message
    assert "blockers: view_boundary_straddle, page_bounds" in issue.message
    assert (
        issue.evidence_reason == "requested_lane_unavailable:8:view_boundary_straddle,page_bounds"
    )


def test_lane_override_round_trips_and_reports_layout_only_evidence() -> None:
    sheet = _slot_sheet()
    sheet.layout_override("declaration:slot", parameter="slot_width.length", lane=4)
    model = sheet.model()
    source_part = Box(100, 80, 10)
    source = emit_sheet_script(model, "part = source_part", "lane", title="lane", number="N")

    assert (
        'sheet.layout_override("declaration:slot", '
        'parameter="slot_width.length", lane=4)' in source
    )
    namespace = {"source_part": source_part}
    exec(  # noqa: S102 - execute the generated replay surface this test owns
        compile(source[: source.index("drawing = sheet.build()")], "<lane-replay>", "exec"),
        namespace,
    )
    assert namespace["sheet"].model().layout_overrides == model.layout_overrides

    report = sheet.build().report()
    assert report["layout"]["overrides"] == [
        {
            "declaration_id": "declaration:slot",
            "parameter_id": "slot_width.length",
            "control": "lane",
            "authored_value": 4,
            "resolved_value": 4,
            "intent_class": "layout-only",
            "status": "applied",
        }
    ]
    schema = json.loads(
        (
            Path(__file__).parents[1] / "docs/reference/draftwright-report-v8.schema.json"
        ).read_text()
    )
    validator_for(schema)(schema).validate(report)
    missing_parameter = copy.deepcopy(report)
    del missing_parameter["layout"]["overrides"][0]["parameter_id"]
    assert list(validator_for(schema)(schema).iter_errors(missing_parameter))


@pytest.mark.parametrize("lane", [True, 0, 9, 2.5, "outer"])
def test_lane_vocabulary_is_bounded_and_never_a_coordinate(lane) -> None:
    sheet = _slot_sheet()
    with pytest.raises(ValueError, match="integer from 1 to 8"):
        sheet.layout_override("declaration:slot", parameter="slot_width.length", lane=lane)
    with pytest.raises(TypeError):
        sheet.layout_override(  # type: ignore[call-arg]
            "declaration:slot", parameter="slot_width.length", lane=2, x=10
        )
