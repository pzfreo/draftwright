"""Append-only side overrides stay semantic, discoverable, and replayable (#1757)."""

import inspect
import json
from dataclasses import replace
from pathlib import Path

import pytest
from build123d import Box
from jsonschema.validators import validator_for

from draftwright import Sheet
from draftwright.model import ControlFrame, DatumRef, Frame, LayoutOverride
from draftwright.model.ir import PLACEMENT_SIDES as IR_PLACEMENT_SIDES
from draftwright.sheet import PLACEMENT_SIDES as SHEET_PLACEMENT_SIDES
from draftwright.sheet_emit import emit_sheet_script, mirror_model


def test_sheet_placement_sides_import_path_stays_available() -> None:
    assert SHEET_PLACEMENT_SIDES is IR_PLACEMENT_SIDES


def _sheet() -> Sheet:
    sheet = Sheet(Box(80, 60, 10), title="override").authored_dimensions()
    sheet.add(
        ControlFrame(
            Frame((0, 0, 5), "z"),
            "position",
            "0.1",
            "plan",
            "below",
            datums=("A",),
            diameter=True,
        )
    ).identify("declaration:57", provenance="pmi")
    sheet.add(DatumRef(Frame((20, 0, 5), "z"), "A", "plan", "above")).identify(
        "declaration:63", provenance="pmi"
    )
    return sheet


def test_options_and_validation_are_available_before_build() -> None:
    sheet = _sheet()

    assert sheet.layout_options("declaration:57") == {
        "schema": "draftwright.layout-options",
        "schema_version": 1,
        "scope": "single-declaration-layout-controls",
        "requires_build_validation": True,
        "declaration_id": "declaration:57",
        "feature_kind": "control_frame",
        "controls": {
            "side": {
                "current": "below",
                "supported_values": ["above", "below", "left", "right"],
            }
        },
    }
    assert sheet.validate_layout_override("declaration:57", side="above")["supported"] is True

    missing = sheet.validate_layout_override("declaration:404", side="above")
    assert missing["supported"] is False
    assert missing["issues"][0]["code"] == "invalid_declaration"
    invalid = sheet.validate_layout_override("declaration:57", side="diagonal")
    assert invalid["supported"] is False
    assert invalid["issues"][0]["code"] == "unsupported_value"
    sheet.hole(diameter=6, at=(0, 0, 0), axis="z").identify("declaration:hole")
    unsupported = sheet.validate_layout_override("declaration:hole", side="above")
    assert unsupported["supported"] is False
    assert unsupported["issues"][0]["code"] == "unsupported_declaration"
    future = sheet.validate_layout_override("declaration:57", lane="outer")
    assert future["supported"] is False
    assert future["issues"][0] == {
        "code": "invalid_control_combination",
        "message": "lane requires an exact parameter selector",
    }


def test_unset_authored_dimension_side_is_editable_and_replayable() -> None:
    part = Box(40, 20, 10)
    sheet = Sheet(part, page="A4", scale=2).authored_dimensions()
    sheet.measured_dimension(
        kind="linear",
        value=40,
        label="40",
        dominant_axis="X",
        ref_pts=((-20, 0, 0), (20, 0, 0)),
    ).identify("declaration:source-width", provenance="pmi")

    options = sheet.layout_options("declaration:source-width")
    assert options["controls"]["side"] == {
        "current": None,
        "supported_values": ["above", "below"],
    }
    assert (
        sheet.validate_layout_override("declaration:source-width", side="right")["issues"][0][
            "code"
        ]
        == "unsupported_value"
    )
    assert sheet.validate_layout_override("declaration:source-width", side="below")["supported"]
    sheet.layout_override("declaration:source-width", side="below")

    model = sheet.model()
    assert model.features[0].side == "below"
    assert model.layout_overrides == (LayoutOverride("declaration:source-width", "below"),)
    source = emit_sheet_script(
        model, "part = source_part", "authored-side", title="authored-side", number="2211"
    )
    assert 'sheet.layout_override("declaration:source-width", side="below")' in source
    namespace = {"source_part": part}
    exec(  # noqa: S102 - generated Sheet script is the subject of this replay test
        compile(source[: source.index("drawing = sheet.build()")], "<authored-side>", "exec"),
        namespace,
    )
    assert namespace["sheet"].model().layout_overrides == model.layout_overrides

    drawing = sheet.build()
    ink = drawing.annotations_of(model.features[0])
    assert len(ink) == 1
    _, front_bottom, _, _ = drawing.view_bounds("front")
    assert next(iter(ink.values())).label_bbox[3] <= front_bottom
    assert drawing.report()["layout"]["overrides"] == [
        {
            "declaration_id": "declaration:source-width",
            "control": "side",
            "authored_value": "below",
            "resolved_value": "below",
            "intent_class": "layout-only",
            "status": "applied",
        }
    ]


@pytest.mark.parametrize("kind", ["curve_length", "angular"])
def test_unsupported_authored_dimension_kind_does_not_advertise_side(kind) -> None:
    sheet = Sheet(Box(40, 20, 10)).authored_dimensions()
    sheet.measured_dimension(
        kind=kind,
        value=40,
        label="40 ARC",
        dominant_axis="X",
        ref_pts=((-20, 0, 0), (20, 0, 0)),
    ).identify("declaration:arc")
    assert (
        sheet.validate_layout_override("declaration:arc", side="below")["issues"][0]["code"]
        == "unsupported_declaration"
    )


def test_duplicate_live_identity_is_refused_but_withdrawn_identity_can_be_reused() -> None:
    sheet = _sheet()
    replacement = sheet.add(DatumRef(Frame((-20, 0, 5), "z"), "B", "plan", "above"))

    with pytest.raises(ValueError, match="duplicate declaration_id 'declaration:57'"):
        replacement.identify("declaration:57")

    sheet.features.pop(0)
    with pytest.raises(ValueError, match="requires one live declaration; found 0"):
        sheet.by_declaration("declaration:57")
    replacement.identify("declaration:57")
    sheet.layout_override("declaration:57", side="below")
    assert sheet.model().features[-1].side == "below"


def test_validation_rejects_unknown_and_ambiguous_controls_without_changing_intent() -> None:
    sheet = _sheet()
    before = sheet.model().features

    unknown = sheet.validate_layout_override("declaration:57", side="above", x=10, y=20)
    assert unknown["supported"] is False
    assert unknown["issues"] == [
        {
            "code": "unsupported_control",
            "controls": ["x", "y"],
            "message": "layout_override accepts only side, or parameter with side or lane",
        }
    ]
    for controls in ({}, {"side": "above", "lane": 2}):
        invalid = sheet.validate_layout_override("declaration:57", **controls)
        assert invalid["issues"] == [
            {
                "code": "invalid_control_combination",
                "message": "specify exactly one of side or lane",
            }
        ]
    assert sheet.model().features == before
    assert sheet.model().layout_overrides == ()


def test_two_overrides_change_only_side_and_are_retained_as_layout_intent() -> None:
    sheet = _sheet()
    sheet.layout_override("declaration:57", side="above")
    sheet.layout_override("declaration:63", side="below")

    model = sheet.model()
    by_id = {
        identity.declaration_id: feature
        for feature, identity in zip(model.features, model.declaration_identities, strict=True)
        if identity is not None
    }
    assert by_id["declaration:57"].side == "above"
    assert by_id["declaration:63"].side == "below"
    assert [(row.declaration_id, row.side) for row in model.layout_overrides] == [
        ("declaration:57", "above"),
        ("declaration:63", "below"),
    ]
    assert by_id["declaration:57"].characteristic == "position"
    assert by_id["declaration:57"].tolerance == "0.1"
    assert by_id["declaration:57"].datums == ("A",)
    assert by_id["declaration:63"].letter == "A"

    sheet.features.reverse()
    reordered = sheet.model()
    reordered_by_id = {
        identity.declaration_id: feature
        for feature, identity in zip(
            reordered.features, reordered.declaration_identities, strict=True
        )
        if identity is not None
    }
    assert reordered_by_id["declaration:57"].side == "above"
    assert reordered_by_id["declaration:63"].side == "below"


def test_part_model_refuses_unresolved_or_mismatched_override_evidence() -> None:
    model = _sheet().model()

    with pytest.raises(ValueError, match="found none for 'declaration:404'"):
        replace(
            model,
            layout_overrides=(LayoutOverride("declaration:404", "above"),),
        )
    with pytest.raises(ValueError, match="resolved feature side is 'below'"):
        replace(
            model,
            layout_overrides=(LayoutOverride("declaration:57", "above"),),
        )


def test_emitter_synthesised_envelope_keeps_override_identity_alignment() -> None:
    sheet = _sheet()
    sheet.layout_override("declaration:57", side="above")
    automatic = replace(sheet.model(), authored_dimensions=None)

    mirrored, synthesised = mirror_model(automatic)

    assert synthesised is not None
    assert len(mirrored.declaration_identities) == len(mirrored.features)
    assert mirrored.declaration_identities[-1] is None
    assert mirrored.layout_overrides == automatic.layout_overrides


def test_invalid_or_duplicate_override_fails_before_build_and_accepts_no_coordinates() -> None:
    sheet = _sheet()
    with pytest.raises(ValueError, match="declaration:404"):
        sheet.layout_override("declaration:404", side="above")
    with pytest.raises(ValueError, match="supported side"):
        sheet.layout_override("declaration:57", side="diagonal")

    sheet.layout_override("declaration:57", side="above")
    with pytest.raises(ValueError, match="already has a layout override"):
        sheet.layout_override("declaration:57", side="left")

    assert tuple(inspect.signature(Sheet.layout_override).parameters) == (
        "self",
        "declaration_id",
        "side",
        "parameter",
        "lane",
        "axis",
        "member",
    )
    with pytest.raises(TypeError):
        sheet.layout_override("declaration:63", side="below", x=10)  # type: ignore[call-arg]


def test_emitted_script_replays_override_and_declared_report_records_resolution() -> None:
    sheet = _sheet()
    sheet.layout_override("declaration:57", side="above")
    sheet.layout_override("declaration:63", side="below")
    model = sheet.model()
    source_part = Box(80, 60, 10)
    source = emit_sheet_script(
        model,
        "part = source_part",
        "override",
        title="override",
        number="N",
    )

    assert 'sheet.layout_override("declaration:57", side="above")' in source
    assert 'sheet.layout_override("declaration:63", side="below")' in source
    namespace = {"source_part": source_part}
    exec(  # noqa: S102 - execute the generated replay surface this test owns
        compile(source[: source.index("drawing = sheet.build()")], "<override-replay>", "exec"),
        namespace,
    )
    replayed = namespace["sheet"].model()
    assert replayed.layout_overrides == model.layout_overrides

    drawing = sheet.build()
    report = drawing.report()
    assert report["layout"]["overrides"] == [
        {
            "declaration_id": "declaration:57",
            "control": "side",
            "authored_value": "above",
            "resolved_value": "above",
            "intent_class": "layout-only",
            "status": "applied",
        },
        {
            "declaration_id": "declaration:63",
            "control": "side",
            "authored_value": "below",
            "resolved_value": "below",
            "intent_class": "layout-only",
            "status": "applied",
        },
    ]
    schema = json.loads(
        (Path(__file__).parents[1] / "docs/reference/draftwright-report-v8.schema.json").read_text(
            encoding="utf-8"
        )
    )
    validator_for(schema)(schema).validate(report)


def test_absent_override_preserves_existing_model_and_script_behavior() -> None:
    sheet = _sheet()
    model = sheet.model()

    assert model.layout_overrides == ()
    source = emit_sheet_script(
        model,
        "part = source_part",
        "default",
        title="default",
        number="N",
    )
    assert "sheet.layout_override(" not in source
    assert sheet.build().report()["layout"]["overrides"] == []
