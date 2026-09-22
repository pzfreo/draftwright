"""Shared source membership and per-sheet builds retain one conversion authority."""

from dataclasses import replace

import pytest
from build123d import Box, Cylinder, export_step

from draftwright import (
    BuildCancelled,
    Document,
    DocumentBuildError,
    Sheet,
    observe_build,
)
from draftwright import analysis as analysis_module
from draftwright import recognition_cache as cache_module
from draftwright.model.ir import AuthoredDimension, Frame, Note, PmiFeature


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    path = tmp_path_factory.mktemp("document") / "hole.step"
    export_step(Box(30, 20, 5) - Cylinder(2, 10), path)
    return path


@pytest.fixture(scope="module")
def pmi_source():
    path = "tests/fixtures/grm03_thumbwheel_drive_screw_ap242_pmi.step"
    return Document.from_part(path, pmi="annotate")._source


def authored_member(document, name):
    sheet = document.sheet(name, detail_view=False).authored_dimensions().authored_views()
    for view in ("front", "plan", "side"):
        sheet.view(view)
    return sheet


def rendered_text(drawing):
    """Visible semantic strings through the public annotation read surface."""
    values = []
    for _name, annotation in drawing.iter_annotations():
        for attr in ("label", "text", "pdf_text"):
            value = getattr(annotation, attr, None)
            if value:
                values.append(str(value))
        for row in getattr(annotation, "table_rows", ()):
            values.extend(str(value) for value in row)
        for spec in (
            *getattr(annotation, "pdf_text_specs", ()),
            *getattr(annotation, "pdf_text_relative_specs", ()),
        ):
            value = spec[0] if isinstance(spec, tuple) else getattr(spec, "text", None)
            if value:
                values.append(str(value))
    return "\n".join(values)


def test_members_and_live_reports_reuse_one_exact_recognition(source, monkeypatch):
    calls = []
    original = analysis_module.build_recognition_evidence

    def counted(*args, **kwargs):
        evidence = original(*args, **kwargs)
        calls.append(evidence)
        return evidence

    monkeypatch.setattr(analysis_module, "build_recognition_evidence", counted)
    monkeypatch.setattr(cache_module, "build_recognition_evidence", counted)
    document = Document.from_part(source)
    hole = next(feature for feature in document.features if feature.kind == "hole")
    first = authored_member(document, "diameter")
    first.dimension(hole, "bore.diameter")
    second = authored_member(document, "locations")
    second.dimension(hole, "location")
    result = document.build()
    drawings = result.sheets
    members = result._project_members()
    assert len(members) == 2
    assert all(len(rows) == len(result._catalog.requirements) for _, _, rows in members)
    assert {
        row.outcome.state
        for _, _, rows in members
        for row in rows
        if row.parameter_id == "bore.diameter"
    } == {"placed", "suppressed"}
    assert len(calls) == 1
    for drawing in drawings.values():
        assert drawing.recognition_evidence() is calls[0]
        assert all(
            any(member is owner for member in drawing.model().features)
            for owner in document.features
        )
        assert drawing.recognition_ownership() is drawings["diameter"].recognition_ownership()
        drawing.report()
    drawing = drawings["diameter"]
    before = drawing.report()["recognition"]["requirements"]
    row = next(item for item in before if item["parameter_id"] == "bore.diameter")
    assert row["state"] == "placed" and row["annotations"]
    drawing.remove(row["annotations"][0])
    assert all(
        row.outcome.state != "placed"
        for _, _, rows in result._project_members()
        for row in rows
        if row.parameter_id == "bore.diameter"
    )
    after = drawing.report()["recognition"]["requirements"]
    assert len(after) == len(before)
    assert (
        next(item for item in after if item["parameter_id"] == "bore.diameter")["state"]
        != "placed"
    )
    assert len(calls) == 1


def test_sealed_members_reject_physical_mutation_before_changing_anything(source):
    document = Document.from_part(source)
    sheet = authored_member(document, "features")
    hole = next(feature for feature in document.features if feature.kind == "hole")
    handle = sheet.of(hole)
    clone = replace(hole)
    assert clone == hole and clone is not hole
    note = Note(frame=hole.frame, text="REVIEW", view="plan", side="right", origin=hole)
    initial = tuple(sheet.features)
    for mutation in (
        lambda: sheet.features.append(clone),
        lambda: sheet.features.extend((note, clone)),
        lambda: sheet.features.__setitem__(slice(None), [clone]),
        lambda: sheet.features.__delitem__(0),
        lambda: handle.depth(6),
        lambda: sheet.add(clone),
        lambda: sheet.control(clone).position(0.1, to="A"),
    ):
        with pytest.raises(ValueError, match="document|sealed"):
            mutation()
        assert len(sheet.features) == len(initial)
        assert all(a is b for a, b in zip(initial, sheet.features))
    sheet.dimension(handle, "bore.diameter")
    handle.tolerance(0.02).note("REVIEW")
    with_note = tuple(sheet.features)
    assert len(with_note) > len(initial)
    with pytest.raises(ValueError, match="sealed"):
        sheet.features.clear()
    assert len(sheet.features) == len(with_note)
    assert all(a is b for a, b in zip(with_note, sheet.features))
    built = sheet.build()
    assert built.recognition_ownership() is not None
    assert hole in built.model().features


def test_foreign_member_handles_remain_foreign_and_plain_sheets_keep_editing(source):
    document = Document.from_part(source)
    first, second = authored_member(document, "a"), authored_member(document, "b")
    hole = next(feature for feature in document.features if feature.kind == "hole")
    with pytest.raises(ValueError, match="different Sheet"):
        second.dimension(first.of(hole), "bore.diameter")
    second.dimension(hole, "bore.diameter")
    plain = Sheet(Box(30, 20, 5)).authored_dimensions()
    declared = plain.hole(diameter=4, depth=5, at=(0, 0, 0), axis="z")
    declared.depth(6)
    assert plain.features[0].depth == 6
    plain.features.clear()
    assert not plain.features


def test_member_pmi_policy_projects_one_common_acquisition(pmi_source):
    document = Document(pmi_source)
    annotated = document.sheet("gdt")
    reported = document.sheet("report", pmi="report")
    suppressed = document.sheet("dimensions", pmi="off")

    annotation_kinds = {
        "authored_dimension",
        "datum_ref",
        "default_surface_finish",
        "document_note",
        "general_tolerance",
    }
    assert annotation_kinds <= {feature.kind for feature in annotated.features}
    assert annotation_kinds.isdisjoint(feature.kind for feature in suppressed.features)
    assert tuple(document.features) == tuple(suppressed.features)
    assert annotated._opts["pmi"] == "annotate"
    assert suppressed._opts["pmi"] == "off"
    snapshot = reported._snapshot_for_document()
    source_annotations = pmi_source.source_annotations()
    assert all(
        any(feature is source_feature for feature in snapshot.features)
        for source_feature in source_annotations
    )

    analysis = analysis_module._analyse(
        suppressed._part,
        None,
        "DWG-001",
        None,
        "",
        None,
        pmi="off",
        model=suppressed._build_model_input(),
        _document_input=document._source,
    )
    assert analysis.pmi_mode == "off"
    assert analysis.pmi_working_records is None
    assert analysis.recognition_evidence is pmi_source.analysis.recognition_evidence


@pytest.mark.slow
def test_member_pmi_policy_controls_all_rendered_source_requirements(pmi_source):
    document = Document(pmi_source)
    options = {
        "detail_view": False,
        "page": "A3",
        "scale": 0.5,
        "scale_policy": "permissive",
    }
    document.sheet("gdt", **options).auto_dimensions().auto_views()
    document.sheet("report", pmi="report", **options).auto_dimensions().auto_views()
    document.sheet("dimensions", pmi="off", **options).auto_dimensions().auto_views()

    result = document.build()
    drawings = result.sheets
    assert all(
        drawing.recognition_evidence() is pmi_source.analysis.recognition_evidence
        for drawing in drawings.values()
    )

    annotated_names = drawings["gdt"].annotations()
    annotated_text = rendered_text(drawings["gdt"])
    assert {"default_surface_finish", "general_notes"} <= annotated_names.keys()
    assert any(name.startswith("m_gdt") for name in annotated_names)
    assert any(name.startswith("pmi_") for name in annotated_names)
    assert any(name.startswith("m_chamfer") for name in annotated_names)
    for expected in ("ISO 2768-m", "KNURL", "M3 x 0.5", "GENERAL NOTES", "Ra 3.2"):
        assert expected in annotated_text

    for name in ("report", "dimensions"):
        drawing = drawings[name]
        names = drawing.annotations()
        rendered = rendered_text(drawing)
        assert "default_surface_finish" not in names
        assert "general_notes" not in names
        assert not any(item.startswith(("m_gdt", "pmi_", "m_chamfer")) for item in names)
        for excluded in ("ISO 2768-m", "KNURL", "M3 x 0.5", "GENERAL NOTES", "Ra 3.2"):
            assert excluded not in rendered

    # The policy survives the initial build: neither an immediate edit nor the deferred
    # shared solve may restore source PMI from the sealed physical owners.
    drawing = drawings["dimensions"]
    hole = next(
        feature for feature in drawing.model().features if getattr(feature, "thread", None)
    )
    threaded_step = next(
        feature
        for feature in drawing.model().features
        if feature.kind == "step" and getattr(feature, "thread", None)
    )
    sourced_chamfer = next(
        feature
        for feature in drawing.model().features
        if feature.kind == "chamfer" and feature.source_ids
    )
    drawing.drop(hole)
    drawing.callout(hole)
    drawing.drop(threaded_step)
    with drawing.deferred():
        drawing.callout(threaded_step)
    drawing.drop(sourced_chamfer)
    assert drawing.callout(sourced_chamfer) == ""
    edited_text = rendered_text(drawing)
    assert "M2 x 0.4" not in edited_text
    assert "M3 x 0.5" not in edited_text
    assert not any(name.startswith("m_chamfer") for name in drawing.annotations())


def test_member_cannot_request_pmi_that_the_document_did_not_acquire(source):
    document = Document.from_part(source, pmi="off")
    with pytest.raises(ValueError, match="pmi must be"):
        document.sheet("invalid", pmi="invented")
    with pytest.raises(ValueError, match="cannot raise.*annotate"):
        document.sheet("gdt", pmi="annotate")


def test_member_authored_requirements_survive_lower_pmi_policy(source):
    document = Document.from_part(source, pmi="annotate")
    for mode in ("report", "off"):
        sheet = document.sheet(mode, pmi=mode, detail_view=False)
        sheet.authored_dimensions().authored_views()
        for view in ("front", "plan", "side"):
            sheet.view(view)
        sheet.general_tolerance(f"MEMBER-{mode.upper()}")
        sheet.default_surface_finish("1.6")
        sheet.document_note(f"MEMBER {mode.upper()} NOTE", kind="model_representation")
        sheet.add(
            AuthoredDimension(
                frame=Frame((0.0, 0.0, 0.0), "z"),
                dimension_kind="linear",
                value=1.0,
                label=f"MEMBER {mode.upper()} PMI",
                dominant_axis="X",
                ref_pts=((0.0, 0.0, 0.0), (1.0, 0.0, 0.0)),
                source_id=f"member:{mode}",
            )
        )
        sheet.add(
            PmiFeature(
                frame=Frame((0.0, 0.0, 0.0), "z"),
                pmi_kind="surface_texture",
                value=0.0,
                label=f"MEMBER {mode.upper()} RAW PMI",
                dominant_axis="?",
                source_id=f"member:{mode}:raw",
            )
        )

    drawings = document.build().sheets
    for mode, drawing in drawings.items():
        names = drawing.annotations()
        text = rendered_text(drawing)
        assert {"default_surface_finish", "general_notes"} <= names.keys()
        assert any(name.startswith("pmi_") for name in names)
        assert f"MEMBER {mode.upper()} NOTE" in text
        assert f"MEMBER {mode.upper()} PMI" in text
        assert "Ra 1.6" in text
        assert any(
            feature.kind == "pmi" and feature.source_id == f"member:{mode}:raw"
            for feature in drawing.model().features
        )


def test_document_names_failure_and_returns_no_partial_result(source):
    document = Document.from_part(source)
    document.sheet("unconfigured")
    with pytest.raises(DocumentBuildError, match="unconfigured") as failure:
        document.build()
    assert failure.value.sheet_name == "unconfigured"
    assert isinstance(failure.value.__cause__, ValueError)
    with pytest.raises(ValueError, match="duplicate"):
        document.sheet("unconfigured")


def test_cancellation_names_member_without_publishing_completed_child(source):
    document = Document.from_part(source)
    authored_member(document, "cancelled")
    with observe_build(lambda _event: None) as control:
        control.cancel()
        with pytest.raises(BuildCancelled) as failure:
            document.build()
    assert failure.value.diagnostic["sheet"] == "cancelled"
    assert failure.value.completed_result is None


def test_member_intents_are_snapshotted_before_any_member_build(source):
    document = Document.from_part(source)
    first = authored_member(document, "first")
    second = authored_member(document, "second")
    hole = next(feature for feature in document.features if feature.kind == "hole")
    first.dimension(hole, "bore.diameter")
    changed = []

    def observe(event):
        if event.phase == "started" and event.stage == ("document sheet first",):
            second.dimension(hole, "bore.diameter")
            changed.append(True)

    with observe_build(observe):
        result = document.build()
    assert changed == [True]
    rows = result.sheets["second"].report()["recognition"]["requirements"]
    assert (
        next(row for row in rows if row["parameter_id"] == "bore.diameter")["state"]
        == "suppressed"
    )
    assert second.model().authored_dimensions


@pytest.mark.parametrize("clone", (False, True))
def test_live_document_refuses_lost_or_replaced_physical_members(source, clone):
    from draftwright import ReportUnavailableError

    document = Document.from_part(source)
    authored_member(document, "damaged")
    result = document.build()
    drawing = result.sheets["damaged"]
    features = drawing.model().features
    hole = next(feature for feature in features if feature.kind == "hole")
    index = next(index for index, feature in enumerate(features) if feature is hole)
    if clone:
        features[index] = replace(hole)
    else:
        del features[index]
    with pytest.raises(ReportUnavailableError, match="damaged.*sealed"):
        result._project_members()
