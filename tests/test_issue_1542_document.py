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
from draftwright.model.ir import Note


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    path = tmp_path_factory.mktemp("document") / "hole.step"
    export_step(Box(30, 20, 5) - Cylinder(2, 10), path)
    return path


def authored_member(document, name):
    sheet = document.sheet(name, detail_view=False).authored_dimensions().authored_views()
    for view in ("front", "plan", "side"):
        sheet.view(view)
    return sheet


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
