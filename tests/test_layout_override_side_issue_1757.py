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
from draftwright.sheet_emit import emit_sheet_script, mirror_model


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
