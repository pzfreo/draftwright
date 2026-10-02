"""The standard title block states the drawing units and effective sheet format."""

from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from _parts import dense_plate
from build123d import Box, Location

from draftwright import Sheet, build_drawing
from draftwright._pmi_part21 import MaterialFact
from draftwright.builder import _resolve_title_document_defaults
from draftwright.linting.pmi_coverage import lint_step_title_defaults
from draftwright.pmi import PmiExtractionReport, PmiRecord


def _fields(drawing) -> dict[str, str]:
    block = drawing.get_annotation("title_block")
    return {field: value for field, value, _size, _font in block.title_field_specs}


@dataclass(frozen=True)
class _TitleInputs:
    material: str | None
    tolerance: str | None
    pmi_report: PmiExtractionReport


def _source_defaults(material="CW614N", reason=""):
    fact = MaterialFact("#4", material, "Leaded brass", "#5", "#2", "#1", reason)
    tolerance = SimpleNamespace(kind="general_tolerance", designation="ISO 2768-m")
    return fact, tolerance


def test_step_title_defaults_respect_omitted_blank_explicit_and_ambiguity():
    material, tolerance = _source_defaults()
    inputs = _TitleInputs(None, None, PmiExtractionReport(material_facts=(material,)))
    resolved, tolerance_source, material_source = _resolve_title_document_defaults(
        inputs, (tolerance,), set()
    )
    assert (resolved.material, resolved.tolerance) == ("CW614N", "ISO 2768-m")
    assert (material_source, tolerance_source) == (material, tolerance)

    blank, tolerance_source, material_source = _resolve_title_document_defaults(
        replace(inputs, material="", tolerance=""), (tolerance,), set()
    )
    assert (blank.material, blank.tolerance) == ("", "")
    assert (material_source, tolerance_source) == (None, None)

    authored, tolerance_source, material_source = _resolve_title_document_defaults(
        replace(inputs, material="OTHER", tolerance="ISO 2768-f"), (tolerance,), set()
    )
    assert (authored.material, authored.tolerance) == ("OTHER", "ISO 2768-f")
    assert (material_source, tolerance_source) == (None, None)

    other = replace(material, entity_id="#6", designation="STEEL")
    ambiguous, _tol_source, material_source = _resolve_title_document_defaults(
        replace(inputs, pmi_report=PmiExtractionReport(material_facts=(material, other))),
        (tolerance,),
        set(),
    )
    assert ambiguous.material == "" and material_source is None
    malformed, _tol_source, material_source = _resolve_title_document_defaults(
        replace(
            inputs,
            pmi_report=PmiExtractionReport(
                material_facts=(replace(material, reason="foreign product"),)
            ),
        ),
        (tolerance,),
        set(),
    )
    assert malformed.material == "" and material_source is None


def test_step_title_lint_reports_disagreement_without_replacing_authored_values():
    material, _tolerance = _source_defaults()
    report = PmiExtractionReport(
        material_facts=(material,),
        records=(
            PmiRecord(
                kind="general_tolerances",
                type_code=None,
                value=0,
                label="ISO 2768-m; per ISO GPS",
                source_id="manufacturing_requirement:#9",
                source_category="manufacturing_requirement",
            ),
        ),
    )
    title = SimpleNamespace(
        title_field_specs=(
            ("material", "OTHER", 1, "font"),
            ("general_tolerance", "ISO 2768-f", 1, "font"),
        )
    )
    registry = SimpleNamespace(named=lambda name: title if name == "title_block" else None)
    issues = lint_step_title_defaults(
        report,
        registry,
        material_authored="OTHER",
        tolerance_authored="ISO 2768-f",
        pmi_mode="annotate",
    )
    assert [(issue.code, issue.source_ids) for issue in issues] == [
        ("step_material_disagreement", ("material:#4",)),
        ("step_general_tolerance_disagreement", ("manufacturing_requirement:#9",)),
    ]
    paired_record = replace(
        report.records[0],
        source_ids=("manufacturing_requirement:#9", "manufacturing_requirement:#10"),
    )
    paired_issues = lint_step_title_defaults(
        replace(report, records=(paired_record,)),
        registry,
        material_authored="OTHER",
        tolerance_authored="ISO 2768-f",
        pmi_mode="annotate",
    )
    assert (
        next(
            issue.source_ids
            for issue in paired_issues
            if issue.code == "step_general_tolerance_disagreement"
        )
        == paired_record.source_ids
    )
    # A source-selected default must still agree with the settled title field.
    selected_issues = lint_step_title_defaults(
        replace(report, records=(paired_record,)),
        registry,
        material_authored="",
        tolerance_authored=None,
        tolerance_source_selected=True,
        pmi_mode="annotate",
    )
    assert title.title_field_specs[1][1] == "ISO 2768-f"
    assert paired_record.label.startswith("ISO 2768-m")
    assert [(issue.severity, issue.code, issue.source_ids) for issue in selected_issues] == [
        ("error", "step_general_tolerance_mismatch", paired_record.source_ids)
    ]
    assert (
        lint_step_title_defaults(
            report, registry, material_authored="", tolerance_authored="", pmi_mode="annotate"
        )
        == []
    )
    assert [
        issue.code
        for issue in lint_step_title_defaults(
            report, registry, material_authored=None, tolerance_authored=None, pmi_mode="annotate"
        )
    ] == ["step_material_mismatch"]
    ambiguous = replace(
        report,
        material_facts=(material, replace(material, entity_id="#7", designation="STEEL")),
    )
    assert [
        issue.code
        for issue in lint_step_title_defaults(
            ambiguous,
            registry,
            material_authored=None,
            tolerance_authored=None,
            pmi_mode="annotate",
        )
    ] == ["step_material_ambiguous"]
    malformed = replace(report, material_facts=(replace(material, reason="foreign product"),))
    assert [
        issue.code
        for issue in lint_step_title_defaults(
            malformed,
            registry,
            material_authored=None,
            tolerance_authored=None,
            pmi_mode="annotate",
        )
    ] == ["step_material_unavailable"]


@pytest.mark.slow
@pytest.mark.parametrize(
    ("material", "tolerance", "expected_codes"),
    [
        ("", "", set()),
        (
            "OTHER",
            "ISO 2768-f",
            {"step_material_disagreement", "step_general_tolerance_disagreement"},
        ),
    ],
)
def test_step_title_explicit_values_win_and_remain_source_auditable(
    material, tolerance, expected_codes
):
    source = Path(__file__).parent / "fixtures" / "grm03_thumbwheel_drive_screw_ap242_pmi.step"
    drawing = build_drawing(
        source,
        pmi="annotate",
        material=material,
        tolerance=tolerance,
        auto_dims=False,
        scale=2,
        page="A3",
    )
    fields = _fields(drawing)
    assert fields.get("material", "") == material
    assert fields.get("general_tolerance", "") == tolerance
    assert drawing.material_source is None
    assert drawing.general_tolerance_source is None
    observed = {issue.code for issue in drawing.lint() if issue.code.startswith("step_")}
    assert observed == expected_codes
    assert not [
        issue
        for issue in drawing.lint()
        if issue.code == "pmi_not_rendered"
        and "manufacturing_requirement:#2016" in issue.source_ids
    ]


@pytest.mark.parametrize("page", ("A4", "A2", "A0"))
def test_named_iso_page_states_units_and_format(page):
    sheet = Sheet(Box(30, 20, 10), page=page, scale=1, detail_view=False)
    sheet.authored_dimensions()
    drawing = sheet.build()

    fields = _fields(drawing)
    assert fields["units"] == "mm"
    assert fields["format"] == page
    assert not [issue for issue in drawing.lint() if issue.code == "title_field_overflow"]


def test_dimension_specified_iso_page_uses_its_standard_format_name():
    drawing = build_drawing(Box(30, 20, 10), page=(420, 594), scale=1, auto_dims=False)

    assert _fields(drawing)["format"] == "A2"


def test_nonstandard_page_states_its_actual_dimensions():
    drawing = build_drawing(Box(30, 20, 10), page=(500, 300), scale=1, auto_dims=False)

    assert _fields(drawing)["format"] == "500x300"


def test_new_fields_have_real_cells_and_reach_the_pdf_text_layer():
    drawing = build_drawing(Box(30, 20, 10), page="A2", scale=1, auto_dims=False)
    block = drawing.get_annotation("title_block")

    assert block.cell_bbox("units")["width"] > 0
    assert block.cell_bbox("format")["width"] > 0
    values = {value for value, *_rest in block.pdf_text_specs}
    assert {"mm", "A2"} <= values
    assert not [issue for issue in drawing.lint() if issue.code == "title_field_overflow"]


def test_related_vertical_dividers_share_one_grid():
    drawing = build_drawing(Box(30, 20, 10), page="A4", scale=1, auto_dims=False)
    block = drawing.get_annotation("title_block")

    principal = {
        block.cell_bbox(field)["min_x"]
        for field in ("document_type", "drawing_number", "units", "date")
    }
    secondary = {block.cell_bbox(field)["min_x"] for field in ("general_tolerance", "approved_by")}
    tertiary = {block.cell_bbox(field)["min_x"] for field in ("format", "revision")}

    assert len(principal) == len(secondary) == len(tertiary) == 1


def test_title_block_is_constructed_once_per_build_issue_1942(monkeypatch):
    from draftwright.annotations import _sheet_furniture

    original = _sheet_furniture._make_title_block
    calls = []

    def counted(drawing, analysis):
        calls.append((analysis.PAGE_W, analysis.PAGE_H))
        return original(drawing, analysis)

    monkeypatch.setattr(_sheet_furniture, "_make_title_block", counted)
    drawing = build_drawing(Box(30, 20, 10), page="A4", scale=1)
    block = drawing.get_annotation("title_block")
    bounds = block.bounding_box()

    # The measured reservation and the placed block are both exercised by this build.
    assert drawing.pending_title_block_box() == pytest.approx(
        (bounds.min.X, bounds.min.Y, bounds.max.X, bounds.max.Y)
    )
    assert block.pdf_text_specs
    assert calls == [(297.0, 210.0)]


def test_title_block_is_shared_across_page_retries_issue_1942(monkeypatch):
    from draftwright.annotations import _sheet_furniture
    from draftwright.drawing import Drawing

    # This part still exercises multiple attempts on the same page with the
    # current recogniser, so the cache-sharing assertion keeps its precondition.
    part = dense_plate()

    original_for = Drawing.title_block_for
    original_make = _sheet_furniture._make_title_block
    request_drawings = {}
    built = []
    calls = []

    def cached(drawing, key, factory):
        # Retain the instances: an id-only record could be recycled after a
        # discarded retry, making one Drawing look like two (or vice versa).
        request_drawings.setdefault(key, []).append(drawing)

        def counted_factory():
            built.append(key)
            return factory()

        return original_for(drawing, key, counted_factory)

    def counted_make(drawing, analysis):
        calls.append((analysis.PAGE_W, analysis.PAGE_H))
        return original_make(drawing, analysis)

    monkeypatch.setattr(Drawing, "title_block_for", cached)
    monkeypatch.setattr(_sheet_furniture, "_make_title_block", counted_make)
    drawing = build_drawing(part)

    # Repeated calls on one Drawing are insufficient: this must cross a build retry.
    assert any(
        len({id(drawing) for drawing in drawings}) > 1 for drawings in request_drawings.values()
    )
    assert Counter(built) == Counter(request_drawings.keys())
    assert len(calls) == len(built)
    assert drawing.get_annotation("title_block") is not None


def test_cached_title_block_annotations_have_independent_ownership_issue_1942(monkeypatch):
    from draftwright._core import SheetMargins
    from draftwright.analysis import _analyse
    from draftwright.annotations import _sheet_furniture
    from draftwright.builder import _assemble

    part = Box(30, 20, 10)
    analysis = _analyse(
        part, title="OWNED", number="DWG-1", tolerance=None, drawn_by="A", out="owned", pmi="off"
    )
    moved_analysis = replace(analysis, title_block_margins=SheetMargins(right=20, bottom=20))
    original_make = _sheet_furniture._make_title_block
    calls = []

    def counted(drawing, candidate):
        calls.append((candidate.PAGE_W, candidate.PAGE_H))
        return original_make(drawing, candidate)

    monkeypatch.setattr(_sheet_furniture, "_make_title_block", counted)
    cache = {}
    first = _assemble(analysis, "owned", None, False, auto_dims=False, title_block_cache=cache)
    second = _assemble(
        moved_analysis, "owned", None, False, auto_dims=False, title_block_cache=cache
    )
    first_block = first.get_annotation("title_block")
    second_block = second.get_annotation("title_block")
    first_bbox = first_block.bounding_box()
    first_bounds = (first_bbox.min.X, first_bbox.min.Y, first_bbox.max.X, first_bbox.max.Y)
    first_rect = first_block.draftwright_link_rect
    first_specs = first_block.pdf_text_specs

    assert len(calls) == 1
    assert first_block is not second_block
    offset_x = second_block.draftwright_link_rect[0] - first_rect[0]
    offset_y = second_block.draftwright_link_rect[1] - first_rect[1]
    assert (offset_x, offset_y) == pytest.approx((-9, 9))
    assert [
        (text, x - offset_x, y - offset_y, size, font)
        for text, x, y, size, font in second_block.pdf_text_specs
    ] == list(first_specs)
    second_block.draftwright_link_rect = (0, 0, 1, 1)
    second_block.locate(Location((0, 0, 0)))
    assert second_block.bounding_box().min.X != pytest.approx(first_bounds[0])
    second.remove("title_block")

    assert first.get_annotation("title_block") is first_block
    first_bbox_after = first_block.bounding_box()
    assert (
        first_bbox_after.min.X,
        first_bbox_after.min.Y,
        first_bbox_after.max.X,
        first_bbox_after.max.Y,
    ) == pytest.approx(first_bounds)
    assert first_block.draftwright_link_rect == first_rect
    assert first_block.pdf_text_specs == first_specs
