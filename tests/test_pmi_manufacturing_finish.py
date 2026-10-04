"""Source-scoped AP242 edge and surface-finish requirements on a compact GRM-03 STEP."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from build123d import Align, Box, Cylinder, GeomType, Rot, Vertex
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.TopoDS import TopoDS
from quiddity import FrameGauge, PartFrame

import draftwright.drawing_tables as drawing_tables
import draftwright.pmi as pmi
from draftwright import Sheet, build_drawing
from draftwright._geometry import _cylindrical_finish_site
from draftwright.analysis import _import_step
from draftwright.linting.pmi_coverage import (
    _printed_document_notes,
    lint_pmi_lowering,
    lint_pmi_source_unknown,
    lint_pmi_unreconciled,
)
from draftwright.model.ir import (
    CylindricalReference,
    DefaultSurfaceFinish,
    DocumentNote,
    Finish,
    Frame,
    PmiFeature,
)
from draftwright.model.pmi_lowering import lower_ap242_document_requirements
from draftwright.pmi import _face_finish_site_blocker, extract_pmi_report
from draftwright.sheet_emit import emit_sheet_script

_STEP = Path(__file__).parent / "fixtures/grm03_edge_face_finish_ap242.step"


@pytest.fixture(scope="module")
def _case():
    report = extract_pmi_report(_STEP)
    source = {
        record.kind: record
        for record in report.records
        if record.source_category == "manufacturing_requirement"
    }
    assert source["surface_texture"].label == "Ra 3.2 µm unless otherwise specified"
    assert source["edge_condition"].label == "Break sharp edges 0.2 max"
    assert source["edge_condition"].reference_item_ids == ()
    assert source["surface_finish"].label == "Ra 0.8 µm"
    assert source["surface_finish"].reference_item_ids == ("#138",)
    assert source["surface_finish"].lowering_blockers == ()
    return source, build_drawing(_STEP, pmi="annotate", out=None)


def test_document_edge_condition_is_printed_verbatim_and_covered(_case):
    source, drawing = _case
    (default_finish,) = [
        feature
        for feature in drawing.model().features
        if isinstance(feature, DefaultSurfaceFinish)
        and feature.source_id == source["surface_texture"].source_id
    ]
    assert default_finish.ra == "3.2"
    symbol = drawing.get_annotation("default_surface_finish")
    assert symbol.pdf_text_relative_specs[0][0] == "Ra 3.2"
    assert drawing.registry.feature_of("default_surface_finish") == default_finish
    assert not [
        issue
        for issue in drawing.lint()
        if source["surface_texture"].source_id in issue.source_ids
        and issue.code in {"pmi_not_lowered", "pmi_not_rendered", "pmi_dropped"}
    ]
    notes = [
        feature
        for feature in drawing.model().features
        if isinstance(feature, DocumentNote)
        and feature.source_id == source["edge_condition"].source_id
    ]
    assert len(notes) == 1
    assert notes[0].text == source["edge_condition"].label
    assert notes[0].note_kind == "edge_condition"
    table = drawing.get_annotation("general_notes")
    assert "Break sharp edges 0.2 max" in " ".join(
        cell for row in table.table_rows for cell in row
    )
    assert "general_notes" in drawing.registry.names_for_feature(notes[0])
    assert not [
        issue
        for issue in drawing.lint()
        if source["edge_condition"].source_id in issue.source_ids
        and issue.code in {"pmi_not_lowered", "pmi_not_rendered"}
    ]


def test_face_finish_uses_its_referenced_cylinder_and_survives_on_sheet(_case):
    source, drawing = _case
    finishes = [
        feature
        for feature in drawing.model().features
        if isinstance(feature, Finish) and feature.source_id == source["surface_finish"].source_id
    ]
    assert len(finishes) == 1
    finish = finishes[0]
    assert finish.ra == "0.8"
    assert finish.origin.reference_item_ids == ("#138",)
    assert len(finish.origin.cylindrical_refs) == 1
    cylinder = finish.origin.cylindrical_refs[0]
    assert cylinder.diameter == pytest.approx(5.0)
    assert finish.view == "front"
    assert finish.frame.origin[2] == pytest.approx(cylinder.axis_origin[2] + cylinder.radius)
    names = drawing.registry.names_for_feature(finish.origin)
    assert len(names) == 1
    annotation = drawing.get_annotation(names[0])
    assert annotation.tip[:2] == pytest.approx(drawing.at("front", *finish.frame.origin)[:2])
    assert annotation.pdf_text_relative_specs[0][0] == "0.8"
    assert annotation.gdt_visual_finish == "0.8"
    assert not [
        issue
        for issue in drawing.lint()
        if source["surface_finish"].source_id in issue.source_ids
        and issue.code
        in {
            "pmi_not_lowered",
            "pmi_not_rendered",
            "pmi_dropped",
            "pmi_source_text_mismatch",
            "pmi_source_site_mismatch",
        }
    ]


def test_sourced_edge_and_face_finish_replay_as_declarations(_case):
    source, drawing = _case
    script = emit_sheet_script(
        drawing.model(),
        "part",
        "grm03-edge-finish",
        title="GRM-03",
        number="GRM-03",
        pmi="annotate",
        pmi_source=str(_STEP.resolve()),
    )
    assert "sheet.add(Finish(" in script
    assert "sheet.document_note('Break sharp edges 0.2 max', kind='edge_condition'" in script
    namespace = {"part": _import_step(str(_STEP))}
    build_end = script.index("drawing = sheet.build()") + len("drawing = sheet.build()")
    exec(  # noqa: S102 — run the generated declarations and their public build
        compile(script[:build_end], "<edge-finish-emit>", "exec"),
        namespace,
    )
    replayed = namespace["sheet"].model()
    (finish,) = [feature for feature in replayed.features if isinstance(feature, Finish)]
    assert finish.source_id == source["surface_finish"].source_id
    assert finish.origin.reference_item_ids == ("#138",)
    assert finish.origin.cylindrical_refs[0].diameter == pytest.approx(5.0)
    (edge,) = [
        feature
        for feature in replayed.features
        if isinstance(feature, DocumentNote) and feature.note_kind == "edge_condition"
    ]
    assert edge.source_id == source["edge_condition"].source_id
    replayed_drawing = namespace["drawing"]
    assert "Break sharp edges 0.2 max" in str(
        replayed_drawing.get_annotation("general_notes").table_rows
    )
    names = replayed_drawing.registry.names_for_feature(finish.origin)
    assert len(names) == 1
    assert replayed_drawing.get_annotation(names[0]).pdf_text_relative_specs[0][0] == "0.8"
    assert not [
        issue
        for issue in replayed_drawing.lint()
        if source["edge_condition"].source_id in issue.source_ids
        or source["surface_finish"].source_id in issue.source_ids
    ]


def test_declared_finish_replay_keeps_its_public_feature_origin():
    part = Cylinder(5, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
    sheet = Sheet(part, title="SHAFT", number="F1")
    step = sheet.step(diameter=10, length=10, at=(0, 0, 0), axis="z")
    sheet.add(
        Finish(
            frame=Frame((5, 0, 5), "z"),
            ra="1.6",
            view="front",
            side="right",
            origin=step,
        )
    )
    step.thread("M10")  # A later public replacement must rebind the finish origin.
    sheet.authored_dimensions()
    model = sheet.model()
    original_step = next(feature for feature in model.features if feature.kind == "step")
    original_finish = next(feature for feature in model.features if isinstance(feature, Finish))
    assert original_finish.origin is original_step
    assert original_step.thread == "M10"

    script = emit_sheet_script(model, "part", "shaft-finish", title="SHAFT", number="F1")
    finish_line = next(line for line in script.splitlines() if "sheet.add(Finish(" in line)
    assert "origin=" in finish_line
    namespace = {"part": part}
    build_end = script.index("drawing = sheet.build()") + len("drawing = sheet.build()")
    exec(compile(script[:build_end], "<declared-finish-emit>", "exec"), namespace)  # noqa: S102
    replayed = namespace["sheet"].model()
    replayed_step = next(feature for feature in replayed.features if feature.kind == "step")
    replayed_finish = next(feature for feature in replayed.features if isinstance(feature, Finish))
    assert replayed_finish.origin is replayed_step
    drawing = namespace["drawing"]
    placed = drawing.annotations_of(replayed_finish)
    assert len(placed) == 1
    assert placed.keys() <= drawing.annotations_of(replayed_step).keys()
    assert drawing.drop(replayed_finish) == list(placed)
    assert drawing.annotations_of(replayed_finish) == {}
    assert not [issue for issue in drawing.lint() if issue.code == "pmi_unreconciled"]


def test_replayed_source_finish_refuses_a_different_face_site(_case):
    source, drawing = _case
    script = emit_sheet_script(
        drawing.model(),
        "part",
        "grm03-edge-finish",
        title="GRM-03",
        number="GRM-03",
        pmi="annotate",
        pmi_source=str(_STEP.resolve()),
    )
    old = "Finish(frame=Frame((4, 0, 2.5), 'x')"
    new = "Finish(frame=Frame((4, 0, 5), 'x')"
    assert script.count(old) == 1  # The public declaration targets source face #138.
    namespace = {"part": _import_step(str(_STEP))}
    altered = script.replace(old, new)
    build_end = altered.index("drawing = sheet.build()") + len("drawing = sheet.build()")
    exec(compile(altered[:build_end], "<moved-source-finish>", "exec"), namespace)  # noqa: S102
    moved = namespace["drawing"]
    finish = next(
        feature
        for feature in moved.model().features
        if isinstance(feature, Finish) and feature.source_id == source["surface_finish"].source_id
    )
    assert finish.frame.origin == (4, 0, 5)
    assert finish.origin.reference_item_ids == ("#138",)
    assert moved.annotations_of(finish)  # The false claim visibly reached the sheet.
    assert any(
        issue.code == "pmi_source_site_mismatch"
        and source["surface_finish"].source_id in issue.source_ids
        for issue in moved.lint()
    )


@pytest.mark.parametrize(
    ("new", "claimed_source", "claimed_part21", "expected_code"),
    (
        (
            "source_id='manufacturing_requirement:#808', part21_id='#854', origin=",
            "manufacturing_requirement:#808",
            "#854",
            "pmi_source_site_mismatch",
        ),
        (
            "source_id='manufacturing_requirement:#854', part21_id='#999999', origin=",
            "manufacturing_requirement:#854",
            "#999999",
            "pmi_source_site_mismatch",
        ),
        (
            "source_id='manufacturing_requirement:#780', part21_id='#780', origin=",
            "manufacturing_requirement:#780",
            "#780",
            "pmi_source_site_mismatch",
        ),
        (
            "source_id='manufacturing_requirement:#999998', part21_id='#999998', origin=",
            "manufacturing_requirement:#999998",
            "#999998",
            "pmi_source_unknown",
        ),
    ),
)
def test_replayed_finish_refuses_false_source_identity(
    _case, new, claimed_source, claimed_part21, expected_code
):
    _source, drawing = _case
    if claimed_source == "manufacturing_requirement:#780":
        report = extract_pmi_report(_STEP)
        assert claimed_source in {entity.source_id for entity in report.sources}
        assert claimed_source not in {record.source_id for record in report.records}
    if expected_code == "pmi_source_unknown":
        report = extract_pmi_report(_STEP)
        assert claimed_source not in {entity.source_id for entity in report.sources}
        assert claimed_source not in {record.source_id for record in report.records}
    script = emit_sheet_script(
        drawing.model(),
        "part",
        "grm03-edge-finish",
        title="GRM-03",
        number="GRM-03",
        pmi="annotate",
        pmi_source=str(_STEP.resolve()),
    )
    old = "source_id='manufacturing_requirement:#854', part21_id='#854', origin="
    assert script.count(old) == 1
    altered = script.replace(old, new)
    namespace = {"part": _import_step(str(_STEP))}
    build_end = altered.index("drawing = sheet.build()") + len("drawing = sheet.build()")
    exec(compile(altered[:build_end], "<wrong-source-finish>", "exec"), namespace)  # noqa: S102
    replayed = namespace["drawing"]
    finish = next(feature for feature in replayed.model().features if isinstance(feature, Finish))
    assert finish.source_id == claimed_source
    assert finish.part21_id == claimed_part21
    assert finish.origin.reference_item_ids == ("#138",)
    assert replayed.annotations_of(finish)
    issues = replayed.lint()
    if claimed_source == "manufacturing_requirement:#780":
        assert not [issue for issue in issues if issue.code == "pmi_source_unknown"]
    assert any(
        issue.code == expected_code and finish.source_id in issue.source_ids for issue in issues
    )


def test_source_claimed_finish_without_a_step_census_is_an_error():
    part = Cylinder(5, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
    sheet = Sheet(part, title="SHAFT", number="F2", pmi="annotate")
    sheet.add(
        Finish(
            frame=Frame((5, 0, 5), "z"),
            ra="1.6",
            view="front",
            side="right",
            source_id="manufacturing_requirement:#missing",
            part21_id="#missing",
        )
    )
    sheet.authored_dimensions()
    drawing = sheet.build()
    finish = next(feature for feature in drawing.model().features if isinstance(feature, Finish))
    assert drawing.annotations_of(finish)
    assert any(
        issue.code == "pmi_unreconciled"
        and issue.severity == "error"
        and finish.source_id in issue.source_ids
        for issue in drawing.lint()
    )


def test_independent_lint_rejects_changed_finished_manufacturing_text(_case):
    source, drawing = _case
    table = drawing.get_annotation("general_notes")
    original_rows = table.table_rows
    assert ("1  Break sharp edges 0.2 max",) in original_rows
    try:
        table.table_rows = tuple(
            ("1  Deburr all edges",) if row == ("1  Break sharp edges 0.2 max",) else row
            for row in original_rows
        )
        assert any(
            issue.code == "pmi_source_text_mismatch"
            and source["edge_condition"].source_id in issue.source_ids
            for issue in drawing.lint()
        )
        assert (
            "pmi_source_text_mismatch" in drawing.lint_summary()["quality"]["fidelity"]["by_code"]
        )
    finally:
        table.table_rows = original_rows

    finish = next(
        feature
        for feature in drawing.model().features
        if isinstance(feature, Finish) and feature.source_id == source["surface_finish"].source_id
    )
    (name,) = drawing.registry.names_for_feature(finish.origin)
    annotation = drawing.get_annotation(name)
    original_specs = annotation.pdf_text_relative_specs
    assert original_specs[0][0] == "0.8"
    try:
        annotation.pdf_text_relative_specs = (("9.9", *original_specs[0][1:]),)
        assert any(
            issue.code == "pmi_source_text_mismatch"
            and source["surface_finish"].source_id in issue.source_ids
            for issue in drawing.lint()
        )
    finally:
        annotation.pdf_text_relative_specs = original_specs


@pytest.mark.parametrize(
    "false_claim", ["wrong_part21", "wrong_kind", "wrong_note_kind", "unknown_source"]
)
def test_replayed_edge_note_rejects_false_source_identity(_case, false_claim):
    source, drawing = _case
    report = extract_pmi_report(_STEP)
    edge = source["edge_condition"]
    knurl = source["knurl"]
    assert knurl.source_id != edge.source_id
    assert knurl.kind == "knurl"
    if false_claim == "wrong_part21":
        source_id, part21_id, expected_code = edge.source_id, "#wrong", "pmi_source_site_mismatch"
    elif false_claim == "wrong_kind":
        source_id, part21_id, expected_code = (
            knurl.source_id,
            knurl.part21_id,
            "pmi_source_site_mismatch",
        )
    elif false_claim == "wrong_note_kind":
        source_id, part21_id, expected_code = (
            edge.source_id,
            edge.part21_id,
            "pmi_source_site_mismatch",
        )
    else:
        source_id, part21_id, expected_code = (
            "manufacturing_requirement:#9999",
            "#9999",
            "pmi_source_unknown",
        )
        assert source_id not in {record.source_id for record in report.records}

    script = emit_sheet_script(
        drawing.model(),
        "part",
        "grm03-edge-finish",
        title="GRM-03",
        number="GRM-03",
        pmi="annotate",
        pmi_source=str(_STEP.resolve()),
    )
    marker = "drawing = sheet.build()"
    assert script.count(marker) == 1
    note_kind = "datum_scheme" if false_claim == "wrong_note_kind" else "edge_condition"
    added = (
        f"sheet.document_note('Break sharp edges 0.2 max', kind={note_kind!r}, "
        f"source_id={source_id!r}, part21_id={part21_id!r})"
    )
    altered = script.replace(marker, f"{added}\n{marker}")
    namespace = {"part": _import_step(str(_STEP))}
    build_end = altered.index(marker) + len(marker)
    exec(  # noqa: S102 — exercise a generated public Sheet script with one false claim
        compile(altered[:build_end], "<false-edge-source>", "exec"), namespace
    )
    replayed = namespace["drawing"]
    rows = replayed.get_annotation("general_notes").table_rows
    assert sum("Break sharp edges 0.2 max" in row[0] for row in rows) == 2
    assert any(
        issue.code == expected_code and source_id in issue.source_ids for issue in replayed.lint()
    )


def test_valid_source_datum_note_retains_its_provenance_without_edge_diagnostic():
    step = Path(__file__).parent / "fixtures/grm03_thumbwheel_drive_screw_ap242_pmi.step"
    report = extract_pmi_report(step)
    (datum_scheme,) = [record for record in report.records if record.kind == "datum_scheme"]
    sheet = Sheet(Box(12, 12, 12), source=str(step), pmi="annotate")
    sheet.authored_dimensions()
    sheet.document_note(
        datum_scheme.label,
        kind="datum_scheme",
        source_id=datum_scheme.source_id,
        part21_id=datum_scheme.part21_id,
    )
    drawing = sheet.build()
    assert (
        _printed_document_notes(drawing.get_annotation("general_notes"))[0][0]
        == datum_scheme.label
    )
    assert not [
        issue
        for issue in drawing.lint()
        if datum_scheme.source_id in issue.source_ids
        and issue.code in {"pmi_source_unknown", "pmi_source_site_mismatch"}
    ]


def test_edge_note_without_a_source_is_authored_but_a_source_claim_needs_a_census(_case):
    source, drawing = _case
    edge = next(
        feature
        for feature in drawing.model().features
        if isinstance(feature, DocumentNote)
        and feature.source_id == source["edge_condition"].source_id
    )
    authored = replace(edge, source_id="", part21_id="")
    assert lint_pmi_unreconciled(None, (authored,)) == []
    assert lint_pmi_source_unknown(extract_pmi_report(_STEP), (authored,)) == []
    assert {
        issue.source_ids
        for issue in lint_pmi_unreconciled(None, (edge,))
        if issue.code == "pmi_unreconciled" and issue.severity == "error"
    } == {(edge.source_id,)}


@pytest.mark.parametrize("kind", ("finish", "document_note"))
@pytest.mark.parametrize("with_census", (False, True))
def test_printed_part21_claim_requires_a_source_id_issue_2176(_case, kind, with_census):
    if with_census:
        source, control = _case
        script = emit_sheet_script(
            control.model(),
            "part",
            "grm03-edge-finish",
            title="GRM-03",
            number="GRM-03",
            pmi="annotate",
            pmi_source=str(_STEP.resolve()),
        )
        record = source["surface_finish" if kind == "finish" else "edge_condition"]
        tail = ", origin=" if kind == "finish" else ")"
        old = f"source_id={record.source_id!r}, part21_id={record.part21_id!r}{tail}"
        assert script.count(old) == 1
        altered = script.replace(old, f"source_id='', part21_id={record.part21_id!r}{tail}")
        namespace = {"part": _import_step(str(_STEP))}
        build_end = altered.index("drawing = sheet.build()") + len("drawing = sheet.build()")
        exec(compile(altered[:build_end], "<missing-source-id>", "exec"), namespace)  # noqa: S102
        drawing = namespace["drawing"]
    else:
        record = None
        part = Cylinder(5, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
        sheet = Sheet(part, title="SHAFT", number="P21", pmi="annotate")
        if kind == "finish":
            sheet.add(
                Finish(
                    frame=Frame((5, 0, 5), "z"),
                    ra="1.6",
                    view="front",
                    side="right",
                    part21_id="#854",
                )
            )
        else:
            sheet.document_note(
                "Break sharp edges 0.2 max", kind="edge_condition", part21_id="#854"
            )
            sheet.document_note("Authored note", kind="edge_condition")
        sheet.authored_dimensions()
        drawing = sheet.build()
    part21_id = record.part21_id if record is not None else "#854"
    claim = next(
        feature
        for feature in drawing.model().features
        if feature.kind == kind and feature.part21_id == part21_id
    )
    assert claim.source_id == ""
    if kind == "finish":
        assert drawing.annotations_of(claim)
    else:
        rows = drawing.get_annotation("general_notes").table_rows
        assert any("Break sharp edges 0.2 max" in row[0] for row in rows)
        if not with_census:
            assert any("Authored note" in row[0] for row in rows)
    assert any(
        issue.code == "pmi_source_site_mismatch"
        and part21_id in issue.message
        and "source_id" in issue.message
        for issue in drawing.lint()
    )


def test_independent_lint_rejects_wrong_visible_edge_note_ink(_case, monkeypatch):
    source, control = _case
    original = drawing_tables._build_table
    changed = []

    def wrong_note_ink(rows, draft, **kwargs):
        if rows[0] != ("GENERAL NOTES",):
            return original(rows, draft, **kwargs)
        assert ("1  Break sharp edges 0.2 max",) in rows
        changed.append(True)
        visible_rows = tuple(
            ("1  Break sharp edges 9.9 max",) if row == ("1  Break sharp edges 0.2 max",) else row
            for row in rows
        )
        return original(visible_rows, draft, **kwargs)

    monkeypatch.setattr(drawing_tables, "_build_table", wrong_note_ink)
    drawing = build_drawing(_STEP, pmi="annotate", out=None)
    assert changed
    table = drawing.get_annotation("general_notes")
    assert ("1  Break sharp edges 0.2 max",) in table.table_rows
    assert table.table_size == control.get_annotation("general_notes").table_size
    assert any(
        issue.code == "pmi_source_text_mismatch"
        and source["edge_condition"].source_id in issue.source_ids
        for issue in drawing.lint()
    )


def test_source_finish_lint_rejects_wrong_same_width_visible_digits(_case, monkeypatch):
    import draftwright.annotations._gdt as gdt

    source, _ = _case
    original = gdt._gdt_glyph
    changed = []

    def wrong_ink(item, draft):
        if item.kind != "finish" or item.source_id != source["surface_finish"].source_id:
            return original(item, draft)
        glyph = original(replace(item, ra="9.9"), draft)
        assert glyph.label == "9.9"
        glyph.label = item.ra  # The helper claims 0.8 while its vector faces spell 9.9.
        changed.append(glyph)
        return glyph

    monkeypatch.setattr(gdt, "_gdt_glyph", wrong_ink)
    drawing = build_drawing(_STEP, pmi="annotate", out=None)
    assert changed  # Confirm the altered producer was used by the public build.
    finish = next(
        feature
        for feature in drawing.model().features
        if isinstance(feature, Finish) and feature.source_id == source["surface_finish"].source_id
    )
    (name,) = drawing.registry.names_for_feature(finish)
    annotation = drawing.get_annotation(name)
    assert annotation.pdf_text_relative_specs[0][0] == "0.8"
    assert annotation.gdt_visual_finish == ""
    assert any(
        issue.code == "pmi_source_text_mismatch"
        and source["surface_finish"].source_id in issue.source_ids
        for issue in drawing.lint()
    )


def test_source_finish_lint_rejects_a_displaced_visible_leader_tip(_case, monkeypatch):
    import draftwright.annotations._gdt as gdt

    source, _ = _case
    original_builders = gdt._gdt_candidate_builders
    shifted = []

    def displaced_builders(item, draft, leader_ctor, *args):
        if item.kind != "finish" or item.source_id != source["surface_finish"].source_id:
            return original_builders(item, draft, leader_ctor, *args)

        def displaced_leader(*leader_args, **kwargs):
            tip = kwargs["tip"]
            kwargs["tip"] = (tip[0] - 0.5, tip[1] + 0.5)
            shifted.append(kwargs["tip"])
            return leader_ctor(*leader_args, **kwargs)

        return original_builders(item, draft, displaced_leader, *args)

    monkeypatch.setattr(gdt, "_gdt_candidate_builders", displaced_builders)
    drawing = build_drawing(_STEP, pmi="annotate", out=None)
    assert shifted  # The public build used the deliberately displaced leader producer.
    finish = next(
        feature
        for feature in drawing.model().features
        if isinstance(feature, Finish) and feature.source_id == source["surface_finish"].source_id
    )
    (name,) = drawing.registry.names_for_feature(finish)
    annotation = drawing.get_annotation(name)
    source_site = _cylindrical_finish_site(source["surface_finish"].cylindrical_refs[0])
    assert source_site is not None
    expected = drawing.at(source_site[1], *source_site[0])
    assert annotation.tip[:2] == pytest.approx((expected[0] - 0.5, expected[1] + 0.5))
    assert any(
        issue.code == "pmi_source_site_mismatch" and finish.source_id in issue.source_ids
        for issue in drawing.lint()
    )


def test_face_finish_can_share_an_exact_face_with_a_distinct_knurl_requirement(tmp_path):
    source = _STEP.read_text()
    original = "#857=GEOMETRIC_ITEM_SPECIFIC_USAGE('surface finish','',#856,#10,#138);"
    shared = "#857=GEOMETRIC_ITEM_SPECIFIC_USAGE('surface finish','',#856,#10,#193);"
    assert source.count(original) == 1
    assert "#790=GEOMETRIC_ITEM_SPECIFIC_USAGE('knurl','',#789,#10,#193);" in source
    path = tmp_path / "shared-face.step"
    path.write_text(source.replace(original, shared))

    report = extract_pmi_report(path)
    finish = next(record for record in report.records if record.kind == "surface_finish")
    assert finish.reference_item_ids == ("#193",)
    assert finish.lowering_blockers == ()
    assert len(finish.cylindrical_refs) == 1
    assert finish.cylindrical_refs[0].diameter == pytest.approx(10.0)


def test_face_finish_site_check_uses_source_coordinates_under_a_part_frame():
    frame = PartFrame(
        origin=(11.0, -7.0, 3.0),
        x=(0.0, 1.0, 0.0),
        y=(0.0, 0.0, 1.0),
        z=(1.0, 0.0, 0.0),
        gauge=FrameGauge.FULL,
    )
    report = extract_pmi_report(_STEP, frame=frame)
    finish = next(record for record in report.records if record.kind == "surface_finish")
    assert finish.cylindrical_refs[0].principal_axis == "Z"
    assert finish.lowering_blockers == ()


@pytest.mark.parametrize("trim", ("half_cylinder", "cross_hole"))
def test_face_finish_refuses_a_tip_outside_the_exact_trimmed_face(trim, monkeypatch):
    part = (
        Rot(0, 0, 90) * Cylinder(5, 10, arc_size=180)
        if trim == "half_cylinder"
        else Cylinder(5, 10) - Rot(0, 90, 0) * Cylinder(1, 20)
    )
    (face,) = [
        face
        for face in part.faces()
        if face.geom_type == GeomType.CYLINDER
        and BRepAdaptor_Surface(TopoDS.Face_s(face.wrapped)).Cylinder().Radius()
        == pytest.approx(5)
    ]
    surface = BRepAdaptor_Surface(TopoDS.Face_s(face.wrapped))
    axis = surface.Cylinder().Axis()
    point, direction = axis.Location(), axis.Direction()
    reference = CylindricalReference.canonical(
        axis_point=(point.X(), point.Y(), point.Z()),
        axis_direction=(direction.X(), direction.Y(), direction.Z()),
        radius=surface.Cylinder().Radius(),
        local_interval=(surface.FirstVParameter(), surface.LastVParameter()),
        sense="external",
    )
    site = _cylindrical_finish_site(reference)
    assert site is not None
    assert Vertex(*site[0]).distance_to(face) > 0.1  # defect exists before the refusal
    assert _face_finish_site_blocker(face.wrapped, reference, None) == (
        "face-specific finish leader site is not proved on referenced trimmed face"
    )
    monkeypatch.setattr(
        pmi,
        "_DatumTopologyResolver",
        lambda *_args: SimpleNamespace(resolve=lambda *_args, **_kwargs: ((face.wrapped,), ())),
    )
    source = pmi.PmiRecord(
        kind="surface_finish",
        type_code=None,
        value=0.0,
        label="Ra 0.8 µm",
        source_id="manufacturing_requirement:partial",
        part21_id="#finish",
        source_category="manufacturing_requirement",
        reference_item_ids=("#face",),
    )
    (projected,) = pmi._manufacturing_requirement_topology(
        (source,), SimpleNamespace(OneShape=lambda: part.wrapped)
    )
    assert projected.cylindrical_refs == (reference,)
    assert projected.lowering_blockers == (
        "face-specific finish leader site is not proved on referenced trimmed face",
    )


@pytest.mark.parametrize("kind", ["edge_condition", "surface_finish"])
def test_unlowered_required_manufacturing_meaning_is_an_error(kind):
    report = extract_pmi_report(_STEP)
    source = next(record for record in report.records if record.kind == kind)
    raw = PmiFeature(
        frame=Frame((0.0, 0.0, 0.0), "z"),
        pmi_kind=kind,
        value=0.0,
        label=source.label,
        dominant_axis="?",
        source_id=source.source_id,
        source_category="manufacturing_requirement",
    )
    issues = [
        issue
        for issue in lint_pmi_lowering(report, [raw], "annotate")
        if source.source_id in issue.source_ids
    ]
    assert [(issue.code, issue.severity) for issue in issues] == [("pmi_not_lowered", "error")]


def test_edge_condition_with_a_face_link_is_not_recast_as_a_document_note(_case):
    source, drawing = _case
    edge = next(
        feature
        for feature in drawing.model().features
        if isinstance(feature, DocumentNote)
        and feature.source_id == source["edge_condition"].source_id
    )
    raw = PmiFeature(
        frame=edge.frame,
        pmi_kind="edge_condition",
        value=0.0,
        label=edge.text,
        dominant_axis="?",
        source_id=edge.source_id,
        source_category="manufacturing_requirement",
        reference_item_ids=("#138",),
    )
    model = replace(drawing.model(), features=[raw])
    lowered = lower_ap242_document_requirements(model)
    assert len(lowered.features) == 1
    assert isinstance(lowered.features[0], PmiFeature)
    assert lowered.features[0].lowering_blockers == (
        "face-specific edge condition cannot be a document-wide note",
    )
