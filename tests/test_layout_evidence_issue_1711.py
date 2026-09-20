"""Declared report v8 joins layout findings and solve outcomes to editable intent."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from build123d import Box, Cylinder

from draftwright import Sheet, build_drawing
from draftwright.annotations._common import CorridorCandidate, SolveTrace
from draftwright.linting.structural import lint_drawing
from draftwright.model import DeclarationIdentity


def _identified_hole_drawing():
    part = Box(60, 40, 5) - Cylinder(3, 10)
    sheet = Sheet(part)
    hole = sheet.hole(diameter=6, at=(0, 0, 2.5), axis="z", depth=5).identify("declaration:hole")
    sheet.authored_dimensions()
    sheet.dimension(hole, "bore.diameter")
    return sheet.build()


def test_pairwise_label_finding_retains_both_registry_names() -> None:
    first = SimpleNamespace(label="A", label_bbox=(0.0, 0.0, 4.0, 2.0), segments=())
    second = SimpleNamespace(label="B", label_bbox=(1.0, 0.0, 5.0, 2.0), segments=())

    (issue,) = lint_drawing(
        [first, second],
        annotation_names={id(first): "first", id(second): "second"},
    )

    assert issue.code == "annotation_overlap"
    assert issue.annotation_name == "first"
    assert issue.related_annotation_names == ("second",)


def test_layout_projects_page_views_exact_ink_and_trace_absence(tmp_path) -> None:
    drawing = _identified_hole_drawing()
    report = drawing.report()
    layout = report["layout"]

    assert report["schema_version"] == 8
    assert layout["availability"] == "available"
    assert layout["coordinate_space"] == "page-mm-from-sheet-origin"
    assert layout["edit_surface"] == "semantic-dsl-only"
    assert set(layout["remedy_vocabulary"]) == {
        "page",
        "scale",
        "view",
        "section",
        "schedule",
        "side",
        "priority",
        "pin",
    }
    assert layout["page"]["scale"] == drawing.scale
    assert {row["name"] for row in layout["views"]} == set(drawing.views)

    bore_ink = next(
        row
        for row in layout["annotations"]
        if row["semantic"]["declaration_ids"] == ["declaration:hole"]
        and "bore.diameter" in row["semantic"]["parameters"]
    )
    assert bore_ink["ink_bounds"]["availability"] == "available"
    assert bore_ink["view"] in drawing.views
    assert layout["placement"] == {
        "availability": "unavailable",
        "reason": "solve tracing was not enabled for this build",
    }

    traced = build_drawing(
        drawing.working_part,
        model=drawing.model(),
        trace=tmp_path / "actual.trace.json",
    )
    traced_report = traced.report()
    assert traced_report == traced.report(), "projection of one finished drawing must be stable"
    assert traced_report["layout"]["placement"]["availability"] == "available"
    assert traced_report["layout"]["annotations"] == layout["annotations"]


def test_dropped_corridor_outcome_retains_requirement_and_declaration(tmp_path) -> None:
    untraced = _identified_hole_drawing()
    drawing = build_drawing(
        untraced.working_part,
        model=untraced.model(),
        trace=tmp_path / "solve.json",
    )
    feature = drawing.model().features[0]
    trace = drawing.solve_trace
    assert isinstance(trace, SolveTrace)
    trace.begin_phase("test")
    trace.begin_solve(
        ("plan", "above"),
        "plan",
        "x",
        4.0,
        None,
        [
            CorridorCandidate(
                name="attempted_bore",
                build=lambda _position: None,
                order=(0,),
                on_place=lambda _name: None,
                on_drop=lambda _name: None,
                feature=feature,
                measurement=type(
                    "Measurement",
                    (),
                    {"feature": feature, "parameter": "bore.diameter"},
                )(),
            )
        ],
    )
    trace.record_outcome("attempted_bore", "dropped", reason="strip_full")
    trace.end_solve()
    before = drawing.registry.snapshot(), tuple(drawing.items)
    outcome = next(
        row
        for solve in drawing.report()["layout"]["placement"]["solves"]
        for row in solve["outcomes"]
        if row["name"] == "attempted_bore"
    )

    assert outcome["reason"] == "strip_full"
    assert outcome["semantic"] == {
        "availability": "available",
        "owner_ids": ["hole:1"],
        "declaration_ids": ["declaration:hole"],
        "parameters": ["bore.diameter"],
    }
    assert outcome["remedies"] == drawing.report()["layout"]["remedy_vocabulary"]
    assert drawing.registry.snapshot() == before[0]
    assert all(current is prior for current, prior in zip(drawing.items, before[1], strict=True))


def test_ctc01_crossing_joins_both_declarations_and_tracks_semantic_removal() -> None:
    source = Path(__file__).parent / "fixtures" / "nist_ctc_01_asme1_ap203.stp"
    detected = build_drawing(source, repair=False)
    model = detected.model()
    model = replace(
        model,
        declaration_identities=tuple(
            DeclarationIdentity(f"declaration:{index}")
            for index, _feature in enumerate(model.features, start=1)
        ),
    )
    drawing = build_drawing(detected.working_part, model=model, repair=False)
    findings = [
        row
        for row in drawing.report()["layout"]["findings"]
        if row["code"] == "annotation_ink_overlap"
    ]

    assert findings
    assert all(len(row["annotation_names"]) == 2 for row in findings)
    assert all(row["declaration_ids"] for row in findings)
    raw = drawing.report()["lint"]["issues"]
    assert all(raw[row["lint_issue_index"]]["code"] == row["code"] for row in findings)

    # A feature verb is a sanctioned semantic edit. This intentionally proves the report's
    # before/after join, not that dropping required slot documentation is the right production
    # remedy; completeness remains independently visible in the same report.
    drawing.drop(next(feature for feature in drawing.model().features if feature.kind == "slot"))

    assert not any(
        row["code"] == "annotation_ink_overlap" for row in drawing.report()["layout"]["findings"]
    )
