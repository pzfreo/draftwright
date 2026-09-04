"""#1352: PDF export retains searchable text without changing visible drawing ink."""

from __future__ import annotations

import math
import shutil
import subprocess
import warnings
from pathlib import Path
from types import SimpleNamespace

import pypdfium2 as pdfium
import pytest
from build123d import Align, Box, Cylinder, FontStyle, Location, Pos
from build123d_drafting import Dimension
from build123d_drafting.helpers import Draft
from PIL import Image, ImageChops

from draftwright import Sheet, build_drawing
from draftwright._core import _text_line_spacing_em
from draftwright.drawing import Drawing, _exact_vertex_rotation
from draftwright.export import _PDFTextRun, _render_pdf, _resolved_semantic_font_path
from draftwright.fonts import PLEX_MONO, PLEX_SANS_CONDENSED


def _manufacturing_drawing():
    part = Box(90, 60, 12, align=(Align.CENTER, Align.CENTER, Align.MIN))
    part -= Pos(-20, 0, 0) * Cylinder(1.25, 12)
    part -= Pos(20, 0, 0) * Cylinder(4, 12)

    sheet = Sheet(
        part,
        title="SEARCHABLE BRACKET",
        number="DWG-1352",
        material="AL 6061-T6",
        drawn_by="QA",
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        sheet.auto_dimensions()
    sheet.hole(diameter=2.5, at=(-20, 0, 6), axis="z", through=True).thread("M3x0.5")
    sheet.hole(diameter=8, at=(20, 0, 6), axis="z", through=True).fit("H7")
    top_face = max(part.faces(), key=lambda face: face.center().Z)
    sheet.note("DEBURR ALL EDGES", top_face, view="front", side="above")
    drawing = sheet.build()
    drawing.note("INSPECT DATUM A", (160, 185), name="inspection_note")
    return drawing


def _pdf_text(path: str):
    pdf = pdfium.PdfDocument(path)
    try:
        page = pdf[0]
        text_page = page.get_textpage()
        return pdf, text_page, text_page.get_text_range()
    except Exception:
        pdf.close()
        raise


def _assert_first_character_overlaps_annotation(text_page, extracted, text, annotation):
    first_char_box = text_page.get_charbox(extracted.index(text))
    label_box = annotation.label_bbox
    assert label_box is not None
    k = 72.0 / 25.4
    left, bottom, right, top = first_char_box
    x0, y0, x1, y1 = label_box
    assert left < x1 * k and right > x0 * k
    assert bottom < y1 * k and top > y0 * k


def _assert_term_selection_tracks_physical_object(text_page, extracted, text, expected_angle):
    start = extracted.index(text)
    boxes = [text_page.get_charbox(index) for index in range(start, start + len(text))]
    selection_bounds = (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )
    page = text_page.parent
    text_object = next(
        item
        for item in page.get_objects(textpage=text_page)
        if isinstance(item, pdfium.PdfTextObj) and item.extract() == text
    )
    assert selection_bounds == pytest.approx(text_object.get_bounds(), abs=0.1)
    a, b, _c, _d, _e, _f = text_object.get_matrix().get()
    assert math.degrees(math.atan2(b, a)) == pytest.approx(expected_angle, abs=1.0)


def _assert_text_contains_run_anchor(text_page, extracted, text, point):
    start = extracted.index(text)
    boxes = [text_page.get_charbox(index) for index in range(start, start + len(text))]
    left = min(box[0] for box in boxes)
    bottom = min(box[1] for box in boxes)
    right = max(box[2] for box in boxes)
    top = max(box[3] for box in boxes)
    k = 72.0 / 25.4
    assert left <= point[0] * k <= right
    assert bottom <= point[1] * k <= top


def test_pdf_extracts_dimensions_callouts_notes_and_title_block_values(tmp_path):
    drawing = _manufacturing_drawing()
    pdf_path = drawing.export(str(tmp_path / "semantic"), formats=("pdf",))["pdf"]
    pdf, text_page, extracted = _pdf_text(pdf_path)
    try:
        assert "INSPECT DATUM A" in extracted
        assert "DEBURR ALL EDGES" in extracted
        assert "M3x0.5" in extracted
        assert "H7" in extracted
        assert "SEARCHABLE BRACKET" in extracted
        assert "DWG-1352" in extracted
        assert "AL 6061-T6" in extracted
        assert "QA / draftwright" in extracted

        dimension_name = next(
            name
            for name in drawing.annotations()
            if getattr(drawing.get_annotation(name), "label", None) == "65"
        )
        thread_name = next(
            name
            for name in drawing.annotations()
            if "M3x0.5" in (getattr(drawing.get_annotation(name), "label", "") or "")
        )
        _assert_first_character_overlaps_annotation(
            text_page, extracted, "65", drawing.get_annotation(dimension_name)
        )
        _assert_first_character_overlaps_annotation(
            text_page, extracted, "M3x0.5", drawing.get_annotation(thread_name)
        )
    finally:
        text_page.close()
        pdf.close()

    data = Path(pdf_path).read_bytes()
    assert b"/FontFile2" in data and b"/ToUnicode" in data
    assert b"IBMPlexMono-Regular" in data
    assert b"IBMPlexSansCond-Regular" in data


def test_semantic_text_layer_does_not_change_rendered_pixels(tmp_path, monkeypatch):
    drawing = _manufacturing_drawing()
    semantic = drawing.export(str(tmp_path / "semantic"), formats=("png",))["png"]

    monkeypatch.setattr(Drawing, "_pdf_text_runs", lambda _self: ())
    path_only = drawing.export(str(tmp_path / "path_only"), formats=("png",))["png"]

    semantic_image = Image.open(semantic).convert("RGBA")
    path_only_image = Image.open(path_only).convert("RGBA")
    assert ImageChops.difference(semantic_image, path_only_image).getbbox() is None


def _character_centre(text_page, index):
    left, bottom, right, top = text_page.get_charbox(index)
    return ((left + right) / 2.0, (bottom + top) / 2.0)


def _extracted_text_angle(text_page, extracted, value):
    start = extracted.index(value)
    x0, y0 = _character_centre(text_page, start)
    x1, y1 = _character_centre(text_page, start + len(value) - 1)
    return math.degrees(math.atan2(y1 - y0, x1 - x0))


def test_exact_vertex_rotation_rejects_non_similarity():
    def point(x, y):
        return SimpleNamespace(X=x, Y=y)

    assert _exact_vertex_rotation([], []) is None
    assert (
        _exact_vertex_rotation(
            [point(0, 0), point(0, 0)],
            [point(1, 1), point(1, 1)],
        )
        is None
    )
    assert (
        _exact_vertex_rotation(
            [point(0, 0), point(1, 0), point(0, 1)],
            [point(0, 0), point(2, 0), point(0, 1)],
        )
        is None
    )


def test_multiline_notes_and_title_values_retain_visible_line_pitch(tmp_path):
    drawing = build_drawing(
        Box(10, 10, 10),
        auto_dims=False,
        title="TOP\nBOTTOM",
        number="D\n2",
    )
    drawing.note("A\nB\nC\nD\nE\nF\nG", (100, 100), name="multiline")

    pdf_path = drawing.export(str(tmp_path / "multiline"), formats=("pdf",))["pdf"]
    pdf, text_page, extracted = _pdf_text(pdf_path)
    try:
        assert "A\r\nB\r\nC\r\nD\r\nE\r\nF\r\nG" in extracted
        assert "TOP\r\nBOTTOM" in extracted
        expected_pitch_points = (
            _text_line_spacing_em(
                drawing.draft.font_size,
                drawing.draft.font_path,
                drawing.draft.font,
            )
            * drawing.draft.font_size
            * 72.0
            / 25.4
        )
        note_indices = [extracted.index(line) for line in tuple("ABCDEFG")]
        centres = [_character_centre(text_page, index) for index in note_indices]
        assert all(
            upper[1] - lower[1] == pytest.approx(expected_pitch_points, abs=0.15)
            for upper, lower in zip(centres, centres[1:], strict=False)
        )
    finally:
        text_page.close()
        pdf.close()


def test_structured_multiline_note_uses_renderer_line_pitch(tmp_path):
    part = Box(80, 50, 20)
    top = max(part.faces(), key=lambda face: face.center().Z)
    sheet = Sheet(part).auto_dimensions()
    sheet.note("A\nB\nC", top, view="front", side="above")
    drawing = sheet.build()

    pdf_path = drawing.export(str(tmp_path / "structured_note"), formats=("pdf",))["pdf"]
    pdf, text_page, extracted = _pdf_text(pdf_path)
    try:
        assert "A\r\nB\r\nC" in extracted
        expected_pitch_points = (
            _text_line_spacing_em(
                drawing.draft.font_size,
                drawing.draft.font_path,
                drawing.draft.font,
            )
            * drawing.draft.font_size
            * 72.0
            / 25.4
        )
        centres = [_character_centre(text_page, extracted.index(line)) for line in "ABC"]
        assert all(
            math.dist(upper, lower) == pytest.approx(expected_pitch_points, abs=0.15)
            for upper, lower in zip(centres, centres[1:], strict=False)
        )
    finally:
        text_page.close()
        pdf.close()


def test_declared_gdt_values_and_datums_are_searchable(tmp_path):
    part = Box(80, 50, 20) - Pos(0, 0, 0) * Cylinder(6, 20)
    top = max(part.faces(), key=lambda face: face.center().Z)
    sheet = Sheet(part).auto_dimensions()
    hole = sheet.hole(Pos(0, 0, 0) * Cylinder(6, 20))
    sheet.finish("7.77", top, view="front", side="above")
    sheet.datum("Q", top, view="front", side="above")
    sheet.control(hole).position("0.123", to="Q", diameter=True, modifier="M")
    drawing = sheet.build()

    pdf_path = drawing.export(str(tmp_path / "gdt"), formats=("pdf",))["pdf"]
    pdf, text_page, extracted = _pdf_text(pdf_path)
    try:
        assert "7.77" in extracted
        assert "Q" in extracted
        assert "0.123" in extracted
        assert "M" in extracted
        control = next(
            drawing.get_annotation(name)
            for name in drawing.annotations()
            if any(
                spec[0] == "0.123"
                for spec in getattr(drawing.get_annotation(name), "pdf_text_relative_specs", ())
            )
        )
        finish = next(
            drawing.get_annotation(name)
            for name in drawing.annotations()
            if any(
                spec[0] == "7.77"
                for spec in getattr(drawing.get_annotation(name), "pdf_text_relative_specs", ())
            )
        )
        _assert_first_character_overlaps_annotation(text_page, extracted, "0.123", control)
        _assert_first_character_overlaps_annotation(text_page, extracted, "7.77", finish)
        for annotation, value in ((control, "0.123"), (finish, "7.77")):
            spec = next(item for item in annotation.pdf_text_relative_specs if item[0] == value)
            x0, y0, x1, y1 = annotation.label_bbox
            _assert_text_contains_run_anchor(
                text_page,
                extracted,
                value,
                ((x0 + x1) / 2.0 + spec[1], (y0 + y1) / 2.0 + spec[2]),
            )
    finally:
        text_page.close()
        pdf.close()


def test_semantic_order_tiebreak_and_basic_dimension_rotation_are_total(tmp_path):
    drawing = build_drawing(Box(10, 10, 10), auto_dims=False)
    for index, label in enumerate(("AA", "BB")):
        annotation = Dimension(
            (10, 10, 0),
            (50, 50, 0),
            (0, 1, 0),
            10,
            drawing.draft,
            label=label,
            basic=index == 1,
        )
        # Deliberate overlap exercises export robustness. Production feature
        # dimensions still enter through the verbs and placement solve.
        drawing.registry.add(annotation, f"same_box_{index}", view=None)
        drawing.items.append(annotation)

    pdf_path = drawing.export(str(tmp_path / "same_box"), formats=("pdf",))["pdf"]
    pdf, text_page, extracted = _pdf_text(pdf_path)
    try:
        assert "AA" in extracted and "BB" in extracted
        assert _extracted_text_angle(text_page, extracted, "AA") == pytest.approx(45.0, abs=1.0)
        assert _extracted_text_angle(text_page, extracted, "BB") == pytest.approx(45.0, abs=1.0)
    finally:
        text_page.close()
        pdf.close()


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        ((50, 10, 0), (10, 50, 0), -45.0),
        ((20, 50, 0), (20, 10, 0), 90.0),
        ((10, 10, 0), (20, 10, 0), 0.0),
    ],
)
def test_basic_dimension_semantic_text_is_normalised_upright(tmp_path, start, end, expected):
    drawing = build_drawing(Box(10, 10, 10), auto_dims=False)
    annotation = Dimension(start, end, (0, 1, 0), 10, drawing.draft, label="UPRIGHT", basic=True)
    drawing.registry.add(annotation, "upright", view=None)
    drawing.items.append(annotation)

    pdf_path = drawing.export(str(tmp_path / f"upright_{expected}"), formats=("pdf",))["pdf"]
    pdf, text_page, extracted = _pdf_text(pdf_path)
    try:
        assert _extracted_text_angle(text_page, extracted, "UPRIGHT") == pytest.approx(
            expected, abs=1.0
        )
    finally:
        text_page.close()
        pdf.close()


@pytest.mark.parametrize(
    ("start", "end", "label", "label_offset_x", "rotation", "live_rotation", "expected"),
    [
        ((50, 50, 0), (90, 50, 0), "X", -20, 0, 0, 0.0),
        ((50, 50, 0), (50, 90, 0), "W" * 40, 0, 0, 0, 90.0),
        ((50, 50, 0), (90, 90, 0), "W" * 40, 0, 0, 0, 45.0),
        ((50, 90, 0), (90, 50, 0), "W" * 40, 0, 0, 0, -45.0),
        (
            (50, 50, 0),
            (56.945927, 89.39231, 0),
            "UPRIGHT",
            0,
            30,
            0,
            110.0,
        ),
        (
            (50, 50, 0),
            (56.945927, 89.39231, 0),
            "UPRIGHT",
            0,
            30,
            20,
            130.0,
        ),
        ((50, 50, 0), (90, 50, 0), "UPRIGHT", 0, 220, 0, -140.0),
        ((50, 50, 0), (90, 50, 0), "UPRIGHT", 0, 400, 0, 40.0),
    ],
)
def test_raw_basic_dimension_rotation_survives_missing_or_mixed_spans(
    tmp_path, start, end, label, label_offset_x, rotation, live_rotation, expected
):
    drawing = build_drawing(Box(10, 10, 10), auto_dims=False)
    annotation = Dimension(
        start,
        end,
        "above",
        4,
        drawing.draft,
        label=label,
        basic=True,
        label_offset_x=label_offset_x,
        rotation=rotation,
    )
    annotation.location = Location((0, 0, 0), (0, 0, live_rotation))
    drawing.registry.add(annotation, "raw_basic", view=None)
    drawing.items.append(annotation)

    pdf_path = drawing.export(str(tmp_path / f"raw_basic_{expected}"), formats=("pdf",))["pdf"]
    pdf, text_page, extracted = _pdf_text(pdf_path)
    try:
        assert label in extracted
        if math.cos(math.radians(expected)) < -1e-9:
            _assert_first_character_overlaps_annotation(text_page, extracted, label, annotation)
        else:
            assert _extracted_text_angle(text_page, extracted, label) == pytest.approx(
                expected, abs=1.0
            )
    finally:
        text_page.close()
        pdf.close()


@pytest.mark.parametrize("rotation", [30, 120])
def test_engine_dimension_keeps_its_construction_draft_and_rotation(tmp_path, rotation):
    drawing = build_drawing(Box(10, 10, 10), auto_dims=False)
    custom = Draft(font_size=5, font="Arial", font_style=FontStyle.BOLD)
    custom.font_path = None
    with pytest.warns(DeprecationWarning):
        drawing.place_dim(
            (20, 20, 0),
            (60, 20, 0),
            "above",
            "front",
            custom,
            name="tagged",
            basic=True,
            label="TAGGED",
            rotation=rotation,
        )

    pdf_path = drawing.export(str(tmp_path / f"tagged_{rotation}"), formats=("pdf",))["pdf"]
    pdf, text_page, extracted = _pdf_text(pdf_path)
    try:
        if math.cos(math.radians(rotation)) < -1e-9:
            annotation = drawing.get_annotation("tagged")
            _assert_first_character_overlaps_annotation(text_page, extracted, "TAGGED", annotation)
        else:
            assert _extracted_text_angle(text_page, extracted, "TAGGED") == pytest.approx(
                rotation, abs=1.0
            )
    finally:
        text_page.close()
        pdf.close()
    from reportlab.pdfbase.ttfonts import TTFont

    resolved = _resolved_semantic_font_path(None, "Arial", "BOLD")
    expected_postscript_name = TTFont("ResolvedArialBold", resolved).face.name
    assert expected_postscript_name in Path(pdf_path).read_bytes()


@pytest.mark.parametrize(
    ("dimension_kwargs", "expected"),
    [
        ({}, "10.0mm"),
        ({"basic": True}, "10.0mm"),
        ({"tolerance": 0.1}, "10.0 ±0.1mm"),
        ({"basic": True, "tolerance": 0.1}, "10.0 ±0.1mm"),
        ({"tolerance": (0.1, 0.2)}, "10.0 +0.1 -0.2mm"),
        ({"label": "CUSTOM"}, "CUSTOM"),
    ],
)
def test_raw_helper_dimension_semantic_fallback_keeps_visible_label(
    tmp_path, dimension_kwargs, expected
):
    drawing = build_drawing(Box(10, 10, 10), auto_dims=False)
    annotation = Dimension(
        (10, 10, 0),
        (20, 10, 0),
        "above",
        10,
        drawing.draft,
        **dimension_kwargs,
    )
    drawing.registry.add(annotation, "raw_units", view=None)
    drawing.items.append(annotation)

    pdf_path = drawing.export(str(tmp_path / "raw_units"), formats=("pdf",))["pdf"]
    pdf, text_page, extracted = _pdf_text(pdf_path)
    try:
        assert expected in extracted
    finally:
        text_page.close()
        pdf.close()


def test_raw_asymmetric_limits_keep_visible_order_without_units(tmp_path):
    drawing = build_drawing(Box(10, 10, 10), auto_dims=False)
    drawing.draft.display_units = False
    annotation = Dimension(
        (10, 10, 0),
        (20, 10, 0),
        "above",
        10,
        drawing.draft,
        tolerance=(0.1, 0.2),
    )
    drawing.registry.add(annotation, "raw_limits", view=None)
    drawing.items.append(annotation)

    pdf_path = drawing.export(str(tmp_path / "raw_limits"), formats=("pdf",))["pdf"]
    pdf, text_page, extracted = _pdf_text(pdf_path)
    try:
        assert "10.0 +0.1 -0.2" in extracted
        assert "10.0 +0.2 -0.1" not in extracted
        assert "10.0 +0.1 -0.2mm" not in extracted
    finally:
        text_page.close()
        pdf.close()


def test_raw_custom_limit_lookalike_is_not_rewritten(tmp_path):
    drawing = build_drawing(Box(10, 10, 10), auto_dims=False)
    annotation = Dimension(
        (10, 10, 0),
        (20, 10, 0),
        "above",
        10,
        drawing.draft,
        label="10.0 +0.2 -0.1",
    )
    drawing.registry.add(annotation, "custom_limits", view=None)
    drawing.items.append(annotation)

    pdf_path = drawing.export(str(tmp_path / "custom_limits"), formats=("pdf",))["pdf"]
    pdf, text_page, extracted = _pdf_text(pdf_path)
    try:
        assert "10.0 +0.2 -0.1" in extracted
        assert "10.0 +0.1 -0.2" not in extracted
        assert "10.0 +0.2 -0.1mm" not in extracted
    finally:
        text_page.close()
        pdf.close()


def test_raw_numeric_freeform_label_keeps_authored_spelling(tmp_path):
    drawing = build_drawing(Box(10, 10, 10), auto_dims=False)
    annotation = Dimension(
        (10, 10, 0),
        (11, 10, 0),
        "above",
        10,
        drawing.draft,
        label="1",
    )
    drawing.registry.add(annotation, "raw_numeric_label", view=None)
    drawing.items.append(annotation)

    pdf_path = drawing.export(str(tmp_path / "raw_numeric_label"), formats=("pdf",))["pdf"]
    pdf, text_page, extracted = _pdf_text(pdf_path)
    page = text_page.parent
    try:
        assert any(
            isinstance(item, pdfium.PdfTextObj) and item.extract() == "1"
            for item in page.get_objects(textpage=text_page)
        )
        assert "1.0mm" not in extracted
    finally:
        text_page.close()
        pdf.close()


@pytest.mark.parametrize("basic", [False, True])
def test_raw_freeform_dimension_from_a_different_draft_keeps_its_text(tmp_path, basic):
    drawing = build_drawing(Box(10, 10, 10), auto_dims=False)
    custom = Draft(
        font_size=5,
        font="Arial",
        font_style=FontStyle.BOLD,
        display_units=False,
    )
    custom.font_path = None
    annotation = Dimension(
        (20, 20, 0),
        (60, 20, 0),
        "above",
        10,
        custom,
        label="BOLD",
        basic=basic,
    )
    drawing.registry.add(annotation, "raw_custom_draft", view=None)
    drawing.items.append(annotation)

    pdf_path = drawing.export(str(tmp_path / f"raw_custom_{basic}"), formats=("pdf",))["pdf"]
    pdf, text_page, extracted = _pdf_text(pdf_path)
    try:
        assert "BOLD" in extracted
        assert "40.0mm" not in extracted
    finally:
        text_page.close()
        pdf.close()


@pytest.mark.parametrize(("axis", "expected"), [(-80, -30.0), (45, 95.0)])
def test_raw_single_glyph_basic_dimension_recovers_rotation(tmp_path, axis, expected):
    drawing = build_drawing(Box(10, 10, 10), auto_dims=False)
    custom = Draft(font_size=20)
    angle = math.radians(axis)
    annotation = Dimension(
        (50, 50, 0),
        (50 + math.cos(angle), 50 + math.sin(angle), 0),
        "above",
        4,
        custom,
        label="R",
        basic=True,
        rotation=30,
    )
    annotation.location = Location((0, 0, 0), (0, 0, 20))
    drawing.registry.add(annotation, "raw_single_glyph", view=None)
    drawing.items.append(annotation)

    pdf_path = drawing.export(str(tmp_path / f"single_{axis}"), formats=("pdf",))["pdf"]
    pdf, text_page, extracted = _pdf_text(pdf_path)
    page = text_page.parent
    try:
        assert "R" in extracted
        text_object = next(
            item
            for item in page.get_objects(textpage=text_page)
            if isinstance(item, pdfium.PdfTextObj) and item.extract() == "R"
        )
        a, b, _c, _d, _e, _f = text_object.get_matrix().get()
        actual = math.degrees(math.atan2(b, a))
        assert (actual - expected + 90.0) % 180.0 - 90.0 == pytest.approx(0.0, abs=1.0)
    finally:
        text_page.close()
        pdf.close()


@pytest.mark.parametrize(("axis", "expected"), [(-80, -30.0), (45, 95.0)])
def test_raw_helper_default_font_recovers_challenging_single_glyph_rotations(
    tmp_path, axis, expected
):
    for label in "BGCO069":
        drawing = build_drawing(Box(10, 10, 10), auto_dims=False)
        custom = Draft(font_size=20)
        angle = math.radians(axis)
        annotation = Dimension(
            (50, 50, 0),
            (50 + math.cos(angle), 50 + math.sin(angle), 0),
            "above",
            10,
            custom,
            label=label,
            basic=True,
            rotation=30,
        )
        annotation.location = Location((0, 0, 0), (0, 0, 20))
        drawing.registry.add(annotation, "raw_single_glyph", view=None)
        drawing.items.append(annotation)

        pdf_path = drawing.export(
            str(tmp_path / f"raw_challenging_{axis}_{label}"), formats=("pdf",)
        )["pdf"]
        pdf, text_page, _extracted = _pdf_text(pdf_path)
        page = text_page.parent
        try:
            text_object = next(
                item
                for item in page.get_objects(textpage=text_page)
                if isinstance(item, pdfium.PdfTextObj) and item.extract().strip() == label
            )
            a, b, _c, _d, _e, _f = text_object.get_matrix().get()
            assert math.degrees(math.atan2(b, a)) == pytest.approx(expected, abs=1.0)
        finally:
            text_page.close()
            pdf.close()


@pytest.mark.parametrize(
    ("base_angle", "constructor_rotation", "live_rotation", "expected"),
    [(-170, 0, 0, 10.0), (-45, 30, 20, 5.0)],
)
def test_raw_drawing_font_single_curved_glyph_keeps_exact_rotation(
    tmp_path, base_angle, constructor_rotation, live_rotation, expected
):
    drawing = build_drawing(Box(10, 10, 10), auto_dims=False)
    drawing.draft.font_size = 20
    angle = math.radians(base_angle)
    annotation = Dimension(
        (50, 50, 0),
        (50 + math.cos(angle), 50 + math.sin(angle), 0),
        "above",
        10,
        drawing.draft,
        label="C",
        basic=True,
        rotation=constructor_rotation,
    )
    annotation.location = Location((0, 0, 0), (0, 0, live_rotation))
    box = annotation.bounding_box()
    annotation.location = (
        Location((100 - (box.min.X + box.max.X) / 2, 100 - (box.min.Y + box.max.Y) / 2, 0))
        * annotation.location
    )
    drawing.registry.add(annotation, "raw_curved_glyph", view=None)
    drawing.items.append(annotation)

    pdf_path = drawing.export(
        str(tmp_path / f"raw_curved_{base_angle}_{constructor_rotation}_{live_rotation}"),
        formats=("pdf",),
    )["pdf"]
    pdf, text_page, _extracted = _pdf_text(pdf_path)
    page = text_page.parent
    try:
        text_object = next(
            item
            for item in page.get_objects(textpage=text_page)
            if isinstance(item, pdfium.PdfTextObj) and item.extract().strip() == "C"
        )
        a, b, _c, _d, _e, _f = text_object.get_matrix().get()
        assert math.degrees(math.atan2(b, a)) == pytest.approx(expected, abs=1.0)
    finally:
        text_page.close()
        pdf.close()


@pytest.mark.parametrize(
    ("label", "axis", "expected"),
    [("1", 45, 45.0), ("1", -45, -45.0), ("%", 45, 45.0)],
)
def test_raw_drawing_font_single_glyph_prefers_exact_outline(tmp_path, label, axis, expected):
    drawing = build_drawing(Box(10, 10, 10), auto_dims=False)
    angle = math.radians(axis)
    annotation = Dimension(
        (50, 50, 0),
        (50 + math.cos(angle), 50 + math.sin(angle), 0),
        "above",
        1,
        drawing.draft,
        label=label,
        basic=True,
    )
    drawing.registry.add(annotation, "raw_single_glyph", view=None)
    drawing.items.append(annotation)

    pdf_path = drawing.export(
        str(tmp_path / f"raw_single_glyph_{label}_{axis}"), formats=("pdf",)
    )["pdf"]
    pdf, text_page, _extracted = _pdf_text(pdf_path)
    try:
        text_object = next(
            item
            for item in text_page.parent.get_objects(textpage=text_page)
            if isinstance(item, pdfium.PdfTextObj) and item.extract().strip() == label
        )
        a, b, _c, _d, _e, _f = text_object.get_matrix().get()
        assert math.degrees(math.atan2(b, a)) == pytest.approx(expected, abs=1.0)
    finally:
        text_page.close()
        pdf.close()


def test_raw_same_face_multi_outline_glyph_ignores_face_order(tmp_path):
    drawing = build_drawing(Box(10, 10, 10), auto_dims=False)
    custom = Draft(font_size=20)
    custom.font_path = drawing.draft.font_path
    angle = math.radians(-170)
    annotation = Dimension(
        (50, 50, 0),
        (50 + math.cos(angle), 50 + math.sin(angle), 0),
        "above",
        1,
        custom,
        label="%",
        basic=True,
        rotation=30,
    )
    annotation.location = Location((0, 0, 0), (0, 0, 20))
    box = annotation.bounding_box()
    annotation.location = (
        Location((100 - (box.min.X + box.max.X) / 2, 100 - (box.min.Y + box.max.Y) / 2, 0))
        * annotation.location
    )
    drawing.registry.add(annotation, "raw_multi_outline", view=None)
    drawing.items.append(annotation)

    pdf_path = drawing.export(str(tmp_path / "raw_multi_outline"), formats=("pdf",))["pdf"]
    pdf, text_page, _extracted = _pdf_text(pdf_path)
    try:
        text_object = next(
            item
            for item in text_page.parent.get_objects(textpage=text_page)
            if isinstance(item, pdfium.PdfTextObj) and item.extract().strip() == "%"
        )
        a, b, _c, _d, _e, _f = text_object.get_matrix().get()
        assert math.degrees(math.atan2(b, a)) == pytest.approx(60.0, abs=1.0)
    finally:
        text_page.close()
        pdf.close()


@pytest.mark.parametrize("label", ["C", "%"])
def test_raw_unknown_single_glyph_font_keeps_vector_fallback(tmp_path, label):
    drawing = build_drawing(Box(10, 10, 10), auto_dims=False)
    drawing.draft.font_size = 20
    custom = Draft(font_size=20)
    custom.font_path = PLEX_SANS_CONDENSED
    angle = math.radians(-170)
    annotation = Dimension(
        (50, 50, 0),
        (50 + math.cos(angle), 50 + math.sin(angle), 0),
        "above",
        10,
        custom,
        label=label,
        basic=True,
    )
    box = annotation.bounding_box()
    annotation.location = Location(
        (100 - (box.min.X + box.max.X) / 2, 100 - (box.min.Y + box.max.Y) / 2, 0)
    )
    drawing.registry.add(annotation, "raw_custom_glyph", view=None)
    drawing.items.append(annotation)

    pdf_path = drawing.export(str(tmp_path / f"raw_custom_glyph_{label}"), formats=("pdf",))["pdf"]
    pdf, text_page, _extracted = _pdf_text(pdf_path)
    try:
        assert not any(
            isinstance(item, pdfium.PdfTextObj) and item.extract().strip() == label
            for item in text_page.parent.get_objects(textpage=text_page)
        )
    finally:
        text_page.close()
        pdf.close()


def test_poppler_keeps_a_term_whole_when_its_baseline_points_left(tmp_path):
    executable = shutil.which("pdftotext")
    if executable is None:
        pytest.skip("Poppler pdftotext is not installed")
    drawing = build_drawing(Box(10, 10, 10), auto_dims=False)
    annotation = Dimension(
        (50, 50, 0),
        (90, 50, 0),
        "above",
        4,
        drawing.draft,
        label="ANGLE",
        basic=True,
        rotation=110,
    )
    box = annotation.bounding_box()
    annotation.location = Location(
        (100 - (box.min.X + box.max.X) / 2, 100 - (box.min.Y + box.max.Y) / 2, 0)
    )
    drawing.registry.add(annotation, "leftward_text", view=None)
    drawing.items.append(annotation)
    drawing.note("LEFTWARD", (150, 150), rotation=110, name="leftward_note")
    note = drawing.get_annotation("leftward_note")
    box = note.bounding_box()
    note.location = Location(
        (150 - (box.min.X + box.max.X) / 2, 150 - (box.min.Y + box.max.Y) / 2, 0)
    )

    pdf_path = drawing.export(str(tmp_path / "poppler_rotation"), formats=("pdf",))["pdf"]
    for options in ((), ("-raw",), ("-layout",)):
        extracted = subprocess.run(
            [executable, *options, pdf_path, "-"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        lines = [line.strip() for line in extracted.splitlines() if line.strip()]
        assert lines.count("ANGLE") == 1
        assert lines.count("LEFTWARD") == 1
        assert all("ANGLE" not in line or line == "ANGLE" for line in lines)
        assert all("LEFTWARD" not in line or line == "LEFTWARD" for line in lines)

    pdf, text_page, extracted = _pdf_text(pdf_path)
    try:
        _assert_term_selection_tracks_physical_object(text_page, extracted, "ANGLE", 110)
        _assert_term_selection_tracks_physical_object(text_page, extracted, "LEFTWARD", 110)
    finally:
        text_page.close()
        pdf.close()


def test_note_semantic_rotation_tracks_later_annotation_transform(tmp_path):
    drawing = build_drawing(Box(10, 10, 10), auto_dims=False)
    drawing.note("ROTATED", (100, 100), rotation=10, name="rotated")
    drawing.get_annotation("rotated").location = Location((0, 0, 0), (0, 0, 20))

    pdf_path = drawing.export(str(tmp_path / "rotated"), formats=("pdf",))["pdf"]
    pdf, text_page, extracted = _pdf_text(pdf_path)
    try:
        assert _extracted_text_angle(text_page, extracted, "ROTATED") == pytest.approx(
            30.0, abs=1.0
        )
    finally:
        text_page.close()
        pdf.close()


def test_named_font_opt_out_resolves_the_renderer_face_and_style():
    regular = Path(_resolved_semantic_font_path(None, "Arial", "REGULAR"))
    bold = Path(_resolved_semantic_font_path(None, "Arial", "BOLD"))
    assert regular.is_file() and bold.is_file()
    assert regular != bold


def test_named_font_opt_out_supports_build123d_010(monkeypatch):
    import sys

    _resolved_semantic_font_path.cache_clear()
    monkeypatch.setitem(sys.modules, "build123d.text", None)
    try:
        regular = Path(_resolved_semantic_font_path(None, "Arial", "REGULAR"))
        bold = Path(_resolved_semantic_font_path(None, "Arial", "BOLD"))
    finally:
        _resolved_semantic_font_path.cache_clear()

    assert regular.is_file() and bold.is_file()
    assert regular != bold


def test_named_font_opt_out_supports_build123d_009(monkeypatch):
    import sys
    from enum import Enum
    from types import ModuleType

    class LegacyFontStyle(Enum):
        REGULAR = 1
        BOLD = 2
        ITALIC = 3

    legacy_build123d = ModuleType("build123d")
    legacy_build123d.FontStyle = LegacyFontStyle
    _resolved_semantic_font_path.cache_clear()
    monkeypatch.setitem(sys.modules, "build123d", legacy_build123d)
    monkeypatch.setitem(sys.modules, "build123d.text", None)
    try:
        regular = Path(_resolved_semantic_font_path(None, "Arial", "REGULAR"))
        bold = Path(_resolved_semantic_font_path(None, "Arial", "BOLD"))
    finally:
        _resolved_semantic_font_path.cache_clear()

    assert regular.is_file() and bold.is_file()
    assert regular != bold


def test_non_ttfont_semantic_face_falls_back_once_without_losing_text(
    tmp_path, monkeypatch, caplog
):
    from reportlab.pdfbase import ttfonts

    svg_path = tmp_path / "blank.svg"
    svg_path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" '
        'viewBox="0 0 100 100"><path d="M0 0 L1 1"/></svg>',
        encoding="utf-8",
    )
    unsupported = tmp_path / "unsupported.otf"
    shutil.copyfile(PLEX_SANS_CONDENSED, unsupported)
    real_ttfont = ttfonts.TTFont

    class RejectNonTTFont(real_ttfont):
        def __init__(self, name, path, *args, **kwargs):
            if Path(path) == unsupported:
                raise ValueError("CFF-style font is not supported by ReportLab TTFont")
            super().__init__(name, path, *args, **kwargs)

    monkeypatch.setattr(ttfonts, "TTFont", RejectNonTTFont)
    pdf_path = tmp_path / "fallback.pdf"
    _render_pdf(
        str(svg_path),
        str(pdf_path),
        text_runs=tuple(
            _PDFTextRun(f"FALLBACK{index}", 10, 10 + index * 5, 3, font_path=str(unsupported))
            for index in range(3)
        ),
    )

    pdf, text_page, extracted = _pdf_text(str(pdf_path))
    try:
        assert all(f"FALLBACK{index}" in extracted for index in range(3))
        assert Path(PLEX_MONO).is_file()
        assert sum("cannot be embedded" in record.message for record in caplog.records) == 1
    finally:
        text_page.close()
        pdf.close()
