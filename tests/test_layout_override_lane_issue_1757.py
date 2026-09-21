"""Relative dimension lanes are exact, replayable layout-only intent (#1757)."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from build123d import Box
from jsonschema.validators import validator_for

from draftwright import Sheet
from draftwright.annotations._common import PlacementContext
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

    missing_parameter = sheet.validate_layout_override("declaration:slot", lane=4)
    assert missing_parameter["issues"][0]["code"] == "invalid_control_combination"
    mixed_controls = sheet.validate_layout_override(
        "declaration:slot", side="above", parameter="not-a-parameter"
    )
    assert mixed_controls["issues"][0]["code"] == "invalid_control_combination"
    invalid_lane = sheet.validate_layout_override(
        "declaration:slot", parameter="slot_width.length", lane=0
    )
    assert invalid_lane["issues"][0]["code"] == "unsupported_value"
    unsupported = sheet.validate_layout_override(
        "declaration:slot", parameter="slot_end_radius.radius", lane=2
    )
    assert unsupported["issues"][0]["code"] == "unsupported_declaration"


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
        ({"side": "above", "parameter_id": "slot_width.length"}, "not a parameter"),
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
    with pytest.raises(ValueError, match="does not expose a lane control"):
        missing.layout_options("declaration:hole", parameter="bore.diameter")

    duplicate = _slot_sheet()
    duplicate.dimension(duplicate.model().features[0], "slot_width.length")
    with pytest.raises(ValueError, match="declared more than once"):
        duplicate.layout_options("declaration:slot", parameter="slot_width.length")


def test_impossible_lane_drop_retains_bounded_blocker_evidence() -> None:
    context = PlacementContext(registry=AnnotationRegistry())

    _record_slot_drop(
        context,
        None,
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
