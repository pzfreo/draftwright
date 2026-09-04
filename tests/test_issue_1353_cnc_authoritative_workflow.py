"""#1353 integration canaries: detected baseline to audited manufacturing package."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import cast

import pytest
from build123d import Align, Box, Cylinder, Pos, Rot
from PIL import Image, ImageChops

from draftwright import Sheet
from draftwright.model import DimensionParameterId, ThreadOperation
from draftwright.sheet_emit import emit_sheet_script


def _prismatic_part():
    blank = Box(66, 13.55, 60, align=(Align.CENTER, Align.CENTER, Align.MIN))
    # The smooth section runs 48.5 below the collar floor: z=54.5 down to z=6.
    smooth_tool = Pos(0, 0, 6) * Cylinder(2.5, 54, align=(Align.CENTER, Align.CENTER, Align.MIN))
    collar_tool = Pos(0, 0, 54.5) * Cylinder(
        3.175, 5.5, align=(Align.CENTER, Align.CENTER, Align.MIN)
    )
    return blank - smooth_tool - collar_tool, collar_tool


def _prismatic_sheet() -> Sheet:
    part, collar_tool = _prismatic_part()
    sheet = Sheet.from_part(
        part,
        page="A2",
        scale=1,
        title="GRM-01 AUTHORITY CANARY",
        number="GRM-01-CANARY",
        material="CZ121 BRASS",
    ).take_over(
        dimensions="authored",
        principal_views="automatic",
        derived_views="authored",
    )
    detected = next(feature for feature in sheet.features if feature.kind == "hole")
    stack = sheet.of(detected).cbore(collar_tool).thread("M6x1", depth=12).fit("H8")
    for parameter_id in stack.dimension_ids():
        intent = sheet.dimension(stack, cast(DimensionParameterId, parameter_id))
        if parameter_id == "counterbore.diameter":
            intent.format(decimals=2)

    envelope = next(feature for feature in sheet.features if feature.kind == "envelope")
    for parameter in envelope.parameters():
        intent = sheet.dimension(
            envelope,
            cast(DimensionParameterId, parameter.parameter_id),
        )
        if parameter.parameter_id == "depth.length":
            intent.format(decimals=2)

    # Replaces the inferred derived-view set: one explicit section, not an augmentation.
    sheet.section_view("A", through=stack)
    return sheet


def _turned_part():
    body = Rot(0, 90, 0) * Cylinder(5, 20)
    collar = Pos(10.9375, 0, 0) * Rot(0, 90, 0) * Cylinder(3.125, 1.875)
    tip = Pos(12.3, 0, 0) * Rot(0, 90, 0) * Cylinder(2.375, 0.85)
    return body + collar + tip


def _turned_sheet() -> Sheet:
    sheet = Sheet.from_part(
        _turned_part(),
        page="A3",
        scale=4,
        title="GRM-03 TURNED CANARY",
        number="GRM-03-CANARY",
    ).take_over(
        dimensions="authored",
        principal_views="automatic",
        derived_views="automatic",
    )
    for feature in sheet.features:
        if feature.kind != "step":
            continue
        handle = sheet.of(feature)
        for parameter_id in handle.dimension_ids():
            intent = sheet.dimension(handle, cast(DimensionParameterId, parameter_id))
            if feature.diameter == 6.25:
                intent.format(decimals=3 if parameter_id == "step.length" else 2)
            elif feature.diameter == 4.75:
                intent.format(decimals=2)
    return sheet


def _regenerate(
    sheet: Sheet,
    part,
    stem: str,
    *,
    title: str,
    number: str,
    material: str = "",
    page: str,
    scale: float,
) -> tuple[str, Sheet]:
    source = emit_sheet_script(
        sheet.model(),
        "part",
        stem,
        title=title,
        number=number,
        material=material,
        page=page,
        scale=scale,
        view_constraints=sheet.view_constraints,
    )
    namespace = {"part": part}
    body = source.replace("\npart\n", "\n", 1)
    exec(  # noqa: S102 - generated public Sheet source is the round-trip under test
        compile(body[: body.index("drawing = sheet.build()")], "<issue-1353>", "exec"),
        namespace,
    )
    return source, namespace["sheet"]


def _lint_signature(drawing):
    return tuple((issue.severity, issue.code) for issue in drawing.lint())


def _dimension_signature(sheet: Sheet):
    return tuple(
        (
            request.feature.kind,
            getattr(request.feature, "diameter", None),
            request.role,
            request.display_decimals,
            request.view,
            request.side,
        )
        for request in sheet.model().authored_dimensions
    )


def _feature_signature(drawing):
    """Semantic IR facts, normalising emitter-only singleton/rounding representation."""

    return tuple(
        (
            feature.kind,
            tuple(round(float(value), 3) for value in feature.frame.origin),
            feature.frame.axis,
            tuple(
                (parameter.parameter_id, round(float(parameter.value), 3))
                for parameter in feature.parameters()
            ),
            getattr(feature, "through", None),
            getattr(feature, "thread", None),
        )
        for feature in drawing.model().features
    )


def test_prismatic_takeover_retains_baseline_and_compiles_the_complete_staged_stack():
    sheet = _prismatic_sheet()
    drawing = sheet.build()

    assert sheet.view_constraints.principal_source == "automatic"
    assert sheet.view_constraints.derived_source == "authored"
    assert set(drawing.views) == {"front", "plan", "side", "iso", "section_aa"}
    assert [spec.name for spec in drawing.view_plan.of_kind("section")] == ["section_aa"]
    assert len([name for name in drawing.annotations() if name == "section_caption"]) == 1
    assert not [issue for issue in drawing.lint() if issue.severity != "info"]

    callout_name, callout = next(
        (name, annotation)
        for name, annotation in drawing.iter_annotations()
        if name.startswith("hc_")
    )
    assert callout.label == "⌀5 H8 ↧ 48.5 ⌴ ⌀6.35 ↧ 5.5 M6x1 x 12 DEEP"
    assert {identity.parameter for identity in drawing.registry.measurement_of(callout_name)} >= {
        "bore.diameter",
        "bore.depth",
        "counterbore.diameter",
        "counterbore.depth",
        "thread.depth",
    }
    hole = next(feature for feature in drawing.model().features if feature.kind == "hole")
    assert hole.thread == ThreadOperation("M6x1", 12)


def test_prismatic_generated_sheet_round_trip_preserves_semantics_views_and_lint():
    direct_sheet = _prismatic_sheet()
    part, _collar = _prismatic_part()
    source, regenerated = _regenerate(
        direct_sheet,
        part,
        "grm01",
        title="GRM-01 AUTHORITY CANARY",
        number="GRM-01-CANARY",
        material="CZ121 BRASS",
        page="A2",
        scale=1,
    )

    assert 'sheet.dimension(hole1, "counterbore.diameter").format(decimals=2)' in source
    assert 'sheet.dimension(hole1, "thread.depth")' in source
    assert ".fit('H8')" in source
    assert "ThreadOperation(designation='M6x1', depth=12.0)" in source
    assert _dimension_signature(regenerated) == _dimension_signature(direct_sheet)
    assert regenerated.view_constraints.principal_source == "automatic"
    assert regenerated.view_constraints.derived_source == "authored"

    direct, rebuilt = direct_sheet.build(), regenerated.build()
    assert _feature_signature(direct) == _feature_signature(rebuilt)
    assert set(direct.views) == set(rebuilt.views)
    assert _lint_signature(direct) == _lint_signature(rebuilt)


def test_prismatic_exports_are_semantic_and_visually_stable_after_round_trip(tmp_path):
    executable = shutil.which("pdftotext")
    if executable is None:
        pytest.skip("Poppler pdftotext is not installed")

    direct_sheet = _prismatic_sheet()
    part, _collar = _prismatic_part()
    _source, regenerated = _regenerate(
        direct_sheet,
        part,
        "grm01",
        title="GRM-01 AUTHORITY CANARY",
        number="GRM-01-CANARY",
        material="CZ121 BRASS",
        page="A2",
        scale=1,
    )
    direct, rebuilt = direct_sheet.build(), regenerated.build()
    direct_paths = direct.export(str(tmp_path / "direct"), formats=("pdf", "svg", "dxf", "png"))
    rebuilt_paths = rebuilt.export(str(tmp_path / "rebuilt"), formats=("png",))

    extracted = subprocess.run(
        [executable, direct_paths["pdf"], "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "M6x1 x 12 DEEP" in extracted
    for term in ("6.35", "48.5", "M6x1", "H8", "CZ121 BRASS", "GRM-01-CANARY"):
        assert term in extracted
    for format_name in ("pdf", "svg", "dxf"):
        assert Path(direct_paths[format_name]).stat().st_size > 100

    direct_image = Image.open(direct_paths["png"]).convert("RGBA")
    rebuilt_image = Image.open(rebuilt_paths["png"]).convert("RGBA")
    assert ImageChops.difference(direct_image, rebuilt_image).getbbox() is None


def test_turned_canary_prints_precision_nominals_and_round_trips_cleanly():
    sheet = _turned_sheet()
    drawing = sheet.build()
    labels = {getattr(annotation, "label", None) for _, annotation in drawing.iter_annotations()}
    assert {"ø6.25", "ø4.75", "1.875", "0.85"} <= labels
    assert not [issue for issue in drawing.lint() if issue.severity != "info"]
    assert set(drawing.views) == {"front", "side", "iso"}

    source, regenerated = _regenerate(
        sheet,
        _turned_part(),
        "grm03",
        title="GRM-03 TURNED CANARY",
        number="GRM-03-CANARY",
        page="A3",
        scale=4,
    )
    for text in (
        'sheet.dimension(step2, "step.diameter").format(decimals=2)',
        'sheet.dimension(step2, "step.length").format(decimals=3)',
        'sheet.dimension(step3, "step.diameter").format(decimals=2)',
        'sheet.dimension(step3, "step.length").format(decimals=2)',
    ):
        assert text in source
    rebuilt = regenerated.build()
    assert _feature_signature(drawing) == _feature_signature(rebuilt)
    assert _dimension_signature(regenerated) == _dimension_signature(sheet)
    assert _lint_signature(rebuilt) == _lint_signature(drawing)


def test_opposed_coaxial_taps_keep_solver_participation_on_authored_opposite_sides():
    part = (
        Box(40, 40, 20, align=(Align.CENTER, Align.CENTER, Align.CENTER))
        - Pos(0, 0, 4) * Cylinder(1, 6)
        - Pos(0, 0, -10) * Cylinder(2, 6)
    )
    sheet = Sheet.from_part(part, page="A3", scale=2).take_over(
        dimensions="authored",
        principal_views="automatic",
        derived_views="automatic",
    )
    holes = sorted(
        (feature for feature in sheet.features if feature.kind == "hole"),
        key=lambda feature: feature.diameter,
    )
    for feature, thread, side in zip(
        holes,
        ("M2x0.4", "M4x0.7"),
        ("left", "right"),
        strict=True,
    ):
        handle = sheet.of(feature).thread(thread)
        for parameter_id in handle.dimension_ids():
            if parameter_id == "location":
                sheet.dimension(handle, cast(DimensionParameterId, parameter_id))
            else:
                sheet.dimension(
                    handle,
                    cast(DimensionParameterId, parameter_id),
                    view="plan",
                    side=side,
                )
    envelope = next(feature for feature in sheet.features if feature.kind == "envelope")
    for parameter in envelope.parameters():
        sheet.dimension(envelope, cast(DimensionParameterId, parameter.parameter_id))

    drawing = sheet.build()
    plan_left, _bottom, plan_right, _top = drawing.view_bounds("plan")
    callouts = {
        annotation.label: annotation
        for name, annotation in drawing.iter_annotations()
        if name.startswith("hc_plan")
    }
    assert callouts["⌀2 ↧ 6 M2x0.4"].elbow[0] < plan_left
    assert callouts["⌀4 ↧ 3 M4x0.7"].elbow[0] > plan_right
    assert not [issue for issue in drawing.lint() if issue.severity != "info"]
